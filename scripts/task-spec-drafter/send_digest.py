#!/usr/bin/env python3
"""Send the task-spec-drafter daily triage digest by email.

The digest is the SHADOW soak's review surface: each scheduled run emails Zach
the day's triage (would-dispatch TASKs + the NEEDS-DECISION / STALE / ALREADY-DONE
classifications) so he can adjudicate from his inbox without the tool touching
anything.

REUSES repo-cos's `email_send.py` (the DKIM-signed postfix-relay send path) — it
does NOT build a new mailer — but sends under the drafter's OWN identity
(`From: task-drafter@mail.zacx.dev`, `Reply-To: zachlowden1@gmail.com`) so a reply
lands in Zach's inbox, NOT repo-cos's Postgres feedback parser (which acts on
approve/pause keywords). `email_send.py` is loaded by EXPLICIT importlib path, NOT
by putting `scripts/repo-cos/` on sys.path: repo-cos ships an `llm.py` (and
mail-actions a shadowing `_db.py`) that we must never pull in — email_send.py is
standalone stdlib, so an isolated module load is both sufficient and safe (mirrors
feedback.py's importlib gotcha). The identity is set via email_send's own
REPO_COS_FROM / REPO_COS_REPLY_TO env hooks (process-local — repo-cos unaffected).

Usage:
  send_digest.py --subject SUBJ --body-file FILE [--to ADDR] [--dry-run] [--out FILE]

DRY-RUN (--dry-run or DRAFTER_EMAIL_DRYRUN=1): render `To/Subject/body` to --out
(or stdout) and send NOTHING — used by the shadow proof run so a dry-run costs
no external side effect. The first REAL scheduled run does the actual send.
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import sys
from pathlib import Path

DEFAULT_TO = "zachlowden1@gmail.com"

# DISTINCT mail identity — the drafter digest must NOT reuse repo-cos's From/
# Reply-To. repo-cos sends `From: repo-cos@mail.zacx.dev` / `Reply-To:
# repo-cos@inbox.zacx.dev`, and a reply to THAT address routes into repo-cos's
# Postgres feedback inbox where feedback.py/exclusions.py act on approve/pause
# keywords (and can POST a clawgate Task). Zach WILL reply to these soak digests,
# so the drafter uses its own sender and points replies back at his OWN inbox.
# email_send.py reads these from REPO_COS_FROM / REPO_COS_REPLY_TO (its
# `_from_addr()` / `_reply_to()` env hooks) — so we set them in THIS process's
# env before calling send_digest, WITHOUT modifying email_send.py (read-only
# reuse; repo-cos's own runs, a different process, keep their defaults).
DRAFTER_FROM = "task-drafter <task-drafter@mail.zacx.dev>"
DRAFTER_REPLY_TO = "zachlowden1@gmail.com"


def _from_addr() -> str:
    return os.environ.get("DRAFTER_EMAIL_FROM", DRAFTER_FROM)


def _reply_to() -> str:
    return os.environ.get("DRAFTER_EMAIL_REPLY_TO", DRAFTER_REPLY_TO)


def _load_email_send():
    """Load repo-cos/email_send.py in isolation (explicit path, no sys.path edits)."""
    here = Path(__file__).resolve().parent
    mod_path = here.parent / "repo-cos" / "email_send.py"
    if not mod_path.exists():
        raise FileNotFoundError(f"repo-cos email_send.py not found at {mod_path}")
    spec = importlib.util.spec_from_file_location("drafter_email_send", mod_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot build import spec for {mod_path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--subject", required=True)
    ap.add_argument("--body-file", required=True)
    ap.add_argument("--to", default=os.environ.get("DRAFTER_EMAIL_TO", DEFAULT_TO))
    ap.add_argument(
        "--dry-run",
        action="store_true",
        default=os.environ.get("DRAFTER_EMAIL_DRYRUN", "0") == "1",
        help="render the email, send nothing",
    )
    ap.add_argument("--out", default="", help="dry-run: write rendered email here (else stdout)")
    args = ap.parse_args(argv)

    body = Path(args.body_file).read_text(encoding="utf-8", errors="replace")
    from_addr = _from_addr()
    reply_to = _reply_to()

    if args.dry_run:
        rendered = (
            f"From: {from_addr}\nReply-To: {reply_to}\n"
            f"To: {args.to}\nSubject: {args.subject}\n\n{body}"
        )
        if args.out:
            Path(args.out).write_text(rendered, encoding="utf-8")
            print(f"[dry-run] digest email rendered to {args.out} (nothing sent)")
        else:
            sys.stdout.write(rendered)
        return 0

    # Real send — best-effort: never raise into the caller (the drafter treats a
    # send failure as a logged, non-fatal degrade; the queue is still written).
    # Point email_send at the DRAFTER's identity (not repo-cos's) via its env
    # hooks — process-local, so repo-cos's own sends are unaffected.
    os.environ["REPO_COS_FROM"] = from_addr
    os.environ["REPO_COS_REPLY_TO"] = reply_to
    try:
        email_send = _load_email_send()
        to = email_send.send_digest(subject=args.subject, body=body, to_addr=args.to)
        print(f"digest sent to {to} (From: {from_addr}, Reply-To: {reply_to})")
        return 0
    except Exception as exc:  # noqa: BLE001 — deliberate best-effort boundary
        print(f"digest send FAILED: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
