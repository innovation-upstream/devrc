#!/usr/bin/env python3
"""Email sender for the repo-cos digest.

TWO send paths, selected by REPO_COS_SEND (default `relay`):

  * relay  (DEFAULT) — send through Zach's OWN postfix relay so the digest is
    DKIM-signed by `mail.zacx.dev` (SPF/DMARC published → clean Gmail
    deliverability). `From: repo-cos@mail.zacx.dev`, `Reply-To:
    repo-cos@inbox.zacx.dev` (his reply routes Gmail→his MX→mail-receiver→
    Postgres, where `feedback.py` reads it). No SMTP auth: the relay trusts
    MYNETWORKS (127.0.0.0/8), and we reach it over a `kubectl port-forward` to
    `service/postfix-relay` in ns `nebula` of the PRODUCTION cluster.

  * gmail  (FALLBACK) — the original Gmail-SMTP self-to-self path, using the
    SAME Gmail app-password the mailbox sent-poller uses (SOPS
    `mailbox-gmail-imap`, key `IMAP_APP_PASSWORD`). Selectable via
    REPO_COS_SEND=gmail; kept so a relay/cluster hiccup has a working fallback.

Reply-To is set on BOTH paths so a reply always routes to
`repo-cos@inbox.zacx.dev` (→ Postgres), regardless of which sender was used.

The network sends are isolated (`_relay_send` / `_smtp_send`) so `send_digest`
is unit-testable with a mock (no real SMTP, no port-forward, in tests).
"""
from __future__ import annotations

import contextlib
import os
import shutil
import smtplib
import socket
import ssl
import subprocess
import tempfile
import time
from email.message import EmailMessage
from pathlib import Path

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587

# SOPS secret coordinates — verified against homelab-talos trunk (see module docstring).
HOMELAB = Path("~/workspace/homelab-talos").expanduser()
SECRET_FILE = "clusters/homelab/apps/mailbox/secrets-imap.enc.yaml"
SECRET_USER_KEY = "IMAP_USER"           # value: zachlowden1@gmail.com
SECRET_PASSWORD_KEY = "IMAP_APP_PASSWORD"
AGE_KEY_FILE = HOMELAB / ".secrets" / "age.key"

OWNER_EMAIL = "zachlowden1@gmail.com"

# --- relay (his infra) coordinates. All env-overridable with sane defaults. ---
# The digest is sent AS repo-cos@mail.zacx.dev (DKIM-signed by the relay) and asks
# for replies at repo-cos@inbox.zacx.dev (→ his MX → mail-receiver → Postgres).
DEFAULT_FROM = "repo-cos <repo-cos@mail.zacx.dev>"
DEFAULT_REPLY_TO = "repo-cos@inbox.zacx.dev"
# The postfix relay lives in the PRODUCTION cluster (distinct kubeconfig from the
# homelab one feedback.py uses). Only reachable via a port-forward.
PROD_KUBECONFIG = Path(
    os.environ.get("REPO_COS_PROD_KUBECONFIG",
                   "~/workspace/homelab-talos/production-kubeconfig")
).expanduser()
RELAY_NAMESPACE = os.environ.get("REPO_COS_RELAY_NS", "nebula")
RELAY_SERVICE = os.environ.get("REPO_COS_RELAY_SVC", "service/postfix-relay")
RELAY_REMOTE_PORT = 587
RELAY_READY_TIMEOUT = 20.0  # seconds to wait for the port-forward to accept


class EmailError(RuntimeError):
    pass


def _from_addr() -> str:
    return os.environ.get("REPO_COS_FROM", DEFAULT_FROM)


def _reply_to() -> str:
    return os.environ.get("REPO_COS_REPLY_TO", DEFAULT_REPLY_TO)


def _send_mode() -> str:
    return os.environ.get("REPO_COS_SEND", "relay").strip().lower() or "relay"


# ---------------------------------------------------------------------------
# SOPS Gmail-credential resolution (fallback path only)
# ---------------------------------------------------------------------------
def _sops_extract(key: str) -> str:
    """Decrypt one stringData key from the mailbox IMAP secret on homelab trunk.

    Fetches the file content from `origin/trunk` via `git show` (no checkout needed) into
    a temp file, then `sops -d --extract`. `sops` is provided ad-hoc via nix-shell if not
    already on PATH.
    """
    if not AGE_KEY_FILE.exists():
        raise EmailError(f"age key not found at {AGE_KEY_FILE}")
    try:
        blob = subprocess.run(
            ["git", "-C", str(HOMELAB), "show", f"origin/trunk:{SECRET_FILE}"],
            capture_output=True, text=True, check=True, timeout=30,
        ).stdout
    except subprocess.CalledProcessError as exc:
        raise EmailError(f"git show failed for {SECRET_FILE}: {exc.stderr.strip()}") from exc

    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as tf:
        tf.write(blob)
        tmp = tf.name
    try:
        env = dict(os.environ, SOPS_AGE_KEY_FILE=str(AGE_KEY_FILE))
        extract = f'["stringData"]["{key}"]'
        if shutil.which("sops"):
            cmd = ["sops", "-d", "--extract", extract, tmp]
        else:
            # ad-hoc sops via nix-shell (NixOS host, no global install)
            cmd = ["nix-shell", "-p", "sops", "--run",
                   f"sops -d --extract '{extract}' {tmp}"]
        proc = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=120)
        if proc.returncode != 0:
            raise EmailError(f"sops decrypt failed for key {key}: {proc.stderr.strip()}")
        return proc.stdout.strip()
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def load_credentials() -> tuple[str, str]:
    """Return (username, app_password) for the GMAIL fallback path. Env overrides
    (REPO_COS_SMTP_USER / REPO_COS_SMTP_PASSWORD) win so a caller can supply creds
    without SOPS; otherwise decrypt from the mailbox secret."""
    user = os.environ.get("REPO_COS_SMTP_USER")
    pw = os.environ.get("REPO_COS_SMTP_PASSWORD")
    if user and pw:
        return user, pw
    user = user or _sops_extract(SECRET_USER_KEY) or OWNER_EMAIL
    pw = pw or _sops_extract(SECRET_PASSWORD_KEY)
    if not pw:
        raise EmailError("no SMTP app password resolved (env or SOPS)")
    return user, pw


# ---------------------------------------------------------------------------
# Message building (header-injection-safe: EmailMessage rejects newlines in headers)
# ---------------------------------------------------------------------------
def build_message(*, subject: str, body: str, from_addr: str, to_addr: str,
                  reply_to: str | None = None) -> EmailMessage:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = to_addr
    if reply_to:
        msg["Reply-To"] = reply_to
    msg.set_content(body)
    return msg


# ---------------------------------------------------------------------------
# Relay send path (DEFAULT) — his postfix relay over a production port-forward
# ---------------------------------------------------------------------------
def _free_local_port() -> int:
    """Ask the OS for a free TCP port (bind to 0, read it back, release)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _wait_for_port(pf: subprocess.Popen, host: str, port: int,
                   timeout: float = RELAY_READY_TIMEOUT) -> None:
    """Poll until `host:port` accepts a connection, or the port-forward dies / times out."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pf.poll() is not None:
            err = pf.stderr.read().decode() if pf.stderr else ""
            raise EmailError(f"kubectl port-forward exited early:\n{err}")
        with contextlib.suppress(OSError):
            with socket.create_connection((host, port), timeout=1):
                return
        time.sleep(0.25)
    raise EmailError(f"port-forward to {host}:{port} not ready in {timeout}s")


def _relay_send(msg: EmailMessage) -> None:
    """Send `msg` through the postfix relay via a PRODUCTION-cluster port-forward.

    The relay presents a `mail.zacx.dev` cert but we connect over 127.0.0.1, so
    STARTTLS with hostname-verify OFF (justified: a localhost hop to our OWN relay,
    already tunneled through the authenticated k8s API; no external network exposure).
    The port-forward is ALWAYS torn down in the finally.
    """
    if not PROD_KUBECONFIG.exists():
        raise EmailError(f"production kubeconfig not found at {PROD_KUBECONFIG}")
    local_port = _free_local_port()
    env = dict(os.environ, KUBECONFIG=str(PROD_KUBECONFIG))
    pf = subprocess.Popen(
        ["kubectl", "-n", RELAY_NAMESPACE, "port-forward", RELAY_SERVICE,
         f"{local_port}:{RELAY_REMOTE_PORT}"],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, env=env,
    )
    try:
        _wait_for_port(pf, "127.0.0.1", local_port)
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        smtp = smtplib.SMTP("127.0.0.1", local_port, timeout=25)
        try:
            smtp.starttls(context=ctx)
            smtp.send_message(msg)
        finally:
            with contextlib.suppress(Exception):
                smtp.quit()
    finally:
        pf.terminate()
        with contextlib.suppress(Exception):
            pf.wait(timeout=5)


# ---------------------------------------------------------------------------
# Gmail send path (FALLBACK)
# ---------------------------------------------------------------------------
def _smtp_send(msg: EmailMessage, *, user: str, password: str,
               host: str = SMTP_HOST, port: int = SMTP_PORT) -> None:
    """Isolated Gmail network send — mocked in tests."""
    with smtplib.SMTP(host, port, timeout=30) as smtp:
        # Verify the server cert + hostname — a bare starttls() uses an unverified
        # context (CERT_NONE), letting an on-path attacker capture the app-password.
        smtp.starttls(context=ssl.create_default_context())
        smtp.login(user, password)
        smtp.send_message(msg)


# ---------------------------------------------------------------------------
# Public entrypoint
# ---------------------------------------------------------------------------
def send_digest(*, subject: str, body: str, to_addr: str = OWNER_EMAIL,
                mode: str | None = None,
                _relay=_relay_send, _sender=_smtp_send,
                _creds=load_credentials) -> str:
    """Build + send the digest. Returns the recipient on success.

    mode (or REPO_COS_SEND env, default `relay`) selects the send path. `_relay` /
    `_sender` / `_creds` are injectable for tests (no real SMTP / port-forward).
    Reply-To is set on BOTH paths → replies route to repo-cos@inbox.zacx.dev.
    """
    mode = (mode or _send_mode()).strip().lower()
    reply_to = _reply_to()

    if mode == "gmail":
        user, password = _creds()
        from_addr = user or OWNER_EMAIL
        msg = build_message(subject=subject, body=body, from_addr=from_addr,
                            to_addr=to_addr, reply_to=reply_to)
        _sender(msg, user=user, password=password)
        return to_addr

    if mode != "relay":
        raise EmailError(f"unknown REPO_COS_SEND mode {mode!r} (expected relay|gmail)")

    msg = build_message(subject=subject, body=body, from_addr=_from_addr(),
                        to_addr=to_addr, reply_to=reply_to)
    _relay(msg)
    return to_addr
