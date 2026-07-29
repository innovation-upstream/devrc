#!/usr/bin/env python3
"""vetr-mailbox — send 1:1 personalized email AS zach@vetr.com (Google Workspace SMTP).

Reuses the proven /mailbox send pattern (smtp.gmail.com:587, STARTTLS with a
VERIFYING TLS context, app-password login) but points it at the Vetr Workspace
mailbox so recruitment/outreach mail rides Google's sender reputation (inboxes),
NOT Omnisend's cold bulk domain (spam).

SCOPE (enforced by design): 1:1, personalized, rate-limited, dedup'd. This is a
recruitment tool for a BOUNDED, high-value audience (vets). It is deliberately
NOT a bulk marketing blaster — see the guardrails in SKILL.md. Bulk owner mail
belongs on the ESP (Omnisend), not here.

Credentials (0600):  ~/.config/vetr/vetr-mailbox.env
    VETR_SMTP_USER=zach@vetr.com
    VETR_SMTP_APP_PASSWORD=xxxx xxxx xxxx xxxx     # Workspace app-password (2FA on)
    VETR_SMTP_FROM_NAME=Zach Lowden                # optional display name
    VETR_REPLY_TO=zach@vetr.com                    # optional; defaults to the sender

State files:
    ~/.config/vetr/vetr-mailbox-sent.jsonl         # append-only send log (dedup source)
    ~/.config/vetr/vetr-mailbox-suppress.txt       # one email/line: never send (opt-outs)

Recipients: a JSON array of {"email": "...", "name": "First Last"} (name optional).
Template:   a plain-text body file; placeholders {{first_name}} {{last_name}} {{name}}.

Usage:
    # dry run (renders + shows who WOULD get it, sends nothing):
    python send.py --recipients batch.json --subject "..." --body body.txt --dry-run
    # real send, max 15 this run, ~45s (jittered) between sends, tag for the log:
    python send.py --recipients batch.json --subject "..." --body body.txt \
        --limit 15 --sleep 45 --campaign vet_1to1_ca --send
Nothing sends without --send. Already-sent (in the log) and suppressed emails are
skipped automatically.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import smtplib
import ssl
import sys
import time
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import formataddr, parseaddr
from pathlib import Path

CONF_DIR = Path("~/.config/vetr").expanduser()
ENV_FILE = CONF_DIR / "vetr-mailbox.env"
SENT_LOG = CONF_DIR / "vetr-mailbox-sent.jsonl"
SUPPRESS = CONF_DIR / "vetr-mailbox-suppress.txt"

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587


def _load_env() -> dict:
    if not ENV_FILE.exists():
        sys.exit(f"missing {ENV_FILE} (see SKILL.md — needs a Workspace app-password)")
    env = {}
    for line in ENV_FILE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
    if not env.get("VETR_SMTP_USER") or not env.get("VETR_SMTP_APP_PASSWORD"):
        sys.exit("env file must set VETR_SMTP_USER and VETR_SMTP_APP_PASSWORD")
    return env


def _already_sent() -> set[str]:
    seen = set()
    if SENT_LOG.exists():
        for line in SENT_LOG.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                seen.add(json.loads(line)["email"].lower())
            except (json.JSONDecodeError, KeyError):
                pass
    return seen


def _suppressed() -> set[str]:
    if not SUPPRESS.exists():
        return set()
    return {ln.strip().lower() for ln in SUPPRESS.read_text().splitlines()
            if ln.strip() and not ln.startswith("#")}


def _render(template: str, name: str) -> str:
    parts = (name or "").split()
    first = parts[0] if parts else ""
    last = parts[-1] if len(parts) > 1 else ""
    return (template
            .replace("{{first_name}}", first)
            .replace("{{last_name}}", last)
            .replace("{{name}}", name or ""))


def _smtp_send(msg: EmailMessage, *, user: str, password: str) -> None:
    """Verified-TLS Gmail/Workspace send (same pattern as the mailbox skill)."""
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as smtp:
        smtp.starttls(context=ssl.create_default_context())
        smtp.login(user, password)
        smtp.send_message(msg)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--recipients", required=True, help="JSON array of {email,name}")
    ap.add_argument("--subject", required=True)
    ap.add_argument("--body", required=True, help="plain-text body file with {{ }} placeholders (always sent as the text fallback)")
    ap.add_argument("--html", help="optional HTML body file with {{ }} placeholders; sent as multipart/alternative on top of --body")
    ap.add_argument("--campaign", default="vetr_mailbox", help="tag stored in the send log")
    ap.add_argument("--limit", type=int, default=20, help="max sends this run (safety cap)")
    ap.add_argument("--sleep", type=float, default=45.0, help="base seconds between sends (jittered ±30%)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--send", action="store_true", help="actually send (required; else dry-run)")
    ap.add_argument("--force", action="store_true", help="resend even if in the sent log (rare)")
    args = ap.parse_args()

    env = _load_env()
    user = env["VETR_SMTP_USER"]
    pw = env["VETR_SMTP_APP_PASSWORD"]
    from_name = env.get("VETR_SMTP_FROM_NAME", "")
    reply_to = env.get("VETR_REPLY_TO", user)
    from_hdr = formataddr((from_name, user)) if from_name else user

    template = Path(args.body).expanduser().read_text()
    html_template = Path(args.html).expanduser().read_text() if args.html else None
    recips = json.loads(Path(args.recipients).expanduser().read_text())
    seen = set() if args.force else _already_sent()
    suppress = _suppressed()

    queue = []
    queued: set[str] = set()
    skipped = {"sent": 0, "suppressed": 0, "no_email": 0, "dup_in_batch": 0}
    for r in recips:
        email = (r.get("email") or "").strip()
        if not email or "@" not in parseaddr(email)[1]:
            skipped["no_email"] += 1
            continue
        el = email.lower()
        if el in suppress:
            skipped["suppressed"] += 1
            continue
        if el in seen:
            skipped["sent"] += 1
            continue
        if el in queued:  # same address twice in one batch — never double-send
            skipped["dup_in_batch"] += 1
            continue
        queued.add(el)
        queue.append({"email": email, "name": (r.get("name") or "").strip()})

    do_send = args.send and not args.dry_run
    mode = "SEND" if do_send else "DRY-RUN"
    fmt = "html+text" if html_template else "text"
    print(f"[{mode}] from={from_hdr} reply-to={reply_to} format={fmt} subject={args.subject!r}")
    print(f"  eligible={len(queue)}  skipped: already-sent={skipped['sent']} "
          f"suppressed={skipped['suppressed']} bad-email={skipped['no_email']} "
          f"dup-in-batch={skipped['dup_in_batch']}")
    print(f"  cap this run: --limit {args.limit}")

    to_process = queue[: args.limit]
    if not do_send:
        for q in to_process:
            print(f"  would send → {q['name'] or '(no name)'} <{q['email']}>")
        if len(queue) > args.limit:
            print(f"  ...(+{len(queue) - args.limit} more beyond --limit)")
        print("DRY-RUN: nothing sent. Add --send to send for real.")
        return

    SENT_LOG.parent.mkdir(parents=True, exist_ok=True)
    sent = 0
    for i, q in enumerate(to_process):
        body = _render(template, q["name"])
        if "{{" in body:
            sys.exit(f"unrendered placeholder in text body for {q['email']} — aborting")
        html_body = None
        if html_template is not None:
            html_body = _render(html_template, q["name"])
            if "{{" in html_body:
                sys.exit(f"unrendered placeholder in HTML body for {q['email']} — aborting")
        msg = EmailMessage()
        msg["Subject"] = args.subject
        msg["From"] = from_hdr
        msg["To"] = formataddr((q["name"], q["email"])) if q["name"] else q["email"]
        msg["Reply-To"] = reply_to
        msg.set_content(body)                         # text fallback (always present)
        if html_body is not None:
            msg.add_alternative(html_body, subtype="html")
        try:
            _smtp_send(msg, user=user, password=pw)
        except Exception as exc:  # noqa: BLE001 — log + stop; do not silently continue
            sys.exit(f"send FAILED to {q['email']}: {exc!r} (sent {sent} before failure)")
        with SENT_LOG.open("a") as f:
            f.write(json.dumps({
                "email": q["email"], "name": q["name"], "campaign": args.campaign,
                "subject": args.subject, "ts": datetime.now(timezone.utc).isoformat(),
            }) + "\n")
        sent += 1
        print(f"  sent [{sent}/{len(to_process)}] → {q['email']}")
        if i < len(to_process) - 1:
            time.sleep(max(0.0, args.sleep * random.uniform(0.7, 1.3)))
    print(f"done: {sent} sent, logged to {SENT_LOG}")


if __name__ == "__main__":
    main()
