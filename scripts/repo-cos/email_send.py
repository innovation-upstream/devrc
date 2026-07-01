#!/usr/bin/env python3
"""Email sender — Gmail SMTP (STARTTLS), gated behind --email (default OFF).

Reuses the SAME Gmail app-password the mailbox sent-poller uses: SOPS secret
`mailbox-gmail-imap` in homelab-talos trunk, key `IMAP_APP_PASSWORD` (user `IMAP_USER`).
The app password authenticates SMTP (send) just as it does IMAP (read) for that account.

Secret resolution (verified against
  homelab-talos:origin/trunk:clusters/homelab/apps/mailbox/secrets-imap.enc.yaml
  and .../sent-poller.yaml which mounts the same secret):

  SOPS_AGE_KEY_FILE=~/workspace/homelab-talos/.secrets/age.key \
    sops -d --extract '["stringData"]["IMAP_APP_PASSWORD"]' /tmp/s.yaml

The network send is isolated in `_smtp_send` so `send_digest` is unit-testable with a
mock (no real SMTP in tests).
"""
from __future__ import annotations

import os
import shutil
import smtplib
import ssl
import subprocess
import tempfile
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


class EmailError(RuntimeError):
    pass


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
    """Return (username, app_password). Env overrides (REPO_COS_SMTP_USER /
    REPO_COS_SMTP_PASSWORD) win so a caller can supply creds without SOPS; otherwise
    decrypt from the mailbox secret."""
    user = os.environ.get("REPO_COS_SMTP_USER")
    pw = os.environ.get("REPO_COS_SMTP_PASSWORD")
    if user and pw:
        return user, pw
    user = user or _sops_extract(SECRET_USER_KEY) or OWNER_EMAIL
    pw = pw or _sops_extract(SECRET_PASSWORD_KEY)
    if not pw:
        raise EmailError("no SMTP app password resolved (env or SOPS)")
    return user, pw


def build_message(*, subject: str, body: str, from_addr: str, to_addr: str) -> EmailMessage:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = to_addr
    msg.set_content(body)
    return msg


def _smtp_send(msg: EmailMessage, *, user: str, password: str,
               host: str = SMTP_HOST, port: int = SMTP_PORT) -> None:
    """Isolated network send — mocked in tests."""
    with smtplib.SMTP(host, port, timeout=30) as smtp:
        # Verify the server cert + hostname — a bare starttls() uses an unverified
        # context (CERT_NONE), letting an on-path attacker capture the app-password.
        smtp.starttls(context=ssl.create_default_context())
        smtp.login(user, password)
        smtp.send_message(msg)


def send_digest(*, subject: str, body: str, to_addr: str = OWNER_EMAIL,
                _sender=_smtp_send, _creds=load_credentials) -> str:
    """Resolve creds, build the message, send it. Returns the recipient on success.
    `_sender`/`_creds` are injectable for tests."""
    user, password = _creds()
    from_addr = user or OWNER_EMAIL
    msg = build_message(subject=subject, body=body, from_addr=from_addr, to_addr=to_addr)
    _sender(msg, user=user, password=password)
    return to_addr
