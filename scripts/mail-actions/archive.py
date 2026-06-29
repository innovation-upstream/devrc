#!/usr/bin/env python3
"""Invoice archiver — pure logic for extracting PDF invoices from raw RFC822 mail
and deriving their MinIO destination + metadata sidecar.

This module is deliberately PURE (no DB, no MinIO, no network): it operates on the
raw bytes of a message and on mail-row metadata, and returns structured results. The
orchestration (fetch from Postgres, upload to MinIO, label state) lives in
`extract.py`'s `archive-invoices` subcommand so the side-effecting code stays thin
and the decision logic stays unit-testable offline.

Scope decision (see README): archive ALL invoices across the full backlog,
independent of the action-required triage pipeline — a paid invoice the LLM marks
'fyi' is still a tax document. Idempotency is the dedicated `invoice-archived`
label, NOT `processed_at` (archival is orthogonal to action-triage).
"""
from __future__ import annotations

import email
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from email.message import Message
from email.utils import parsedate_to_datetime

import filter as f  # noqa: E402  (sibling module, reuse the billing logic — do NOT duplicate)

ARCHIVED_LABEL = "invoice-archived"

# Filename signal: an attachment whose name looks like a financial document. Used as
# an ALTERNATIVE candidate signal to the billing-sender/subject exemption, so an
# invoice from a non-allowlisted sender (named `invoice_123.pdf`) is still captured.
_INVOICE_FILENAME_RE = re.compile(r"invoice|receipt|statement", re.IGNORECASE)


@dataclass(frozen=True)
class PdfAttachment:
    """One PDF attachment extracted from a message."""

    filename: str | None  # as declared in the message (may be None / missing)
    data: bytes


def extract_pdf_attachments(raw: bytes) -> list[PdfAttachment]:
    """Parse raw RFC822 bytes and return every PDF attachment (by content-type OR
    a `.pdf` filename). Inline/body parts and non-PDFs are ignored."""
    msg: Message = email.message_from_bytes(raw)
    out: list[PdfAttachment] = []
    for part in msg.walk():
        if part.is_multipart():
            continue
        ctype = (part.get_content_type() or "").lower()
        fname = part.get_filename()
        is_pdf = ctype == "application/pdf" or (
            bool(fname) and fname.lower().endswith(".pdf")
        )
        if not is_pdf:
            continue
        payload = part.get_payload(decode=True)
        if not isinstance(payload, (bytes, bytearray)):
            continue
        # fname may be None when a PDF is detected by content-type alone; key
        # derivation falls back to message_id for an empty filename.
        out.append(PdfAttachment(filename=fname or "", data=bytes(payload)))
    return out


def is_archive_candidate(
    *, from_addr: str | None, subject: str | None, attachments: list[PdfAttachment]
) -> bool:
    """A mail is an archive candidate iff it has >=1 PDF attachment AND
    (it looks like billing per filter._billing_exempt OR any attachment filename
    matches /invoice|receipt|statement/i)."""
    if not attachments:
        return False
    if f._billing_exempt(from_addr, subject):
        return True
    return any(
        a.filename and _INVOICE_FILENAME_RE.search(a.filename) for a in attachments
    )


def invoice_date(
    date_header: "str | datetime | None", received_at: datetime | None
) -> datetime:
    """Best-effort invoice date: use `date_header` (a datetime, or an RFC2822 string
    we parse) if possible, else fall back to `received_at`, else now(UTC). Returns a
    tz-aware-or-naive datetime usable for `.year` and `strftime`.

    NOTE: the Postgres `date_header` column is typed timestamptz, so psycopg2 hands it
    back as a `datetime` already; we still accept a string for synthetic/test inputs."""
    if isinstance(date_header, datetime):
        return date_header
    if date_header:
        try:
            dt = parsedate_to_datetime(date_header)
            if dt is not None:
                return dt
        except (TypeError, ValueError, IndexError):
            pass
    if received_at is not None:
        return received_at
    return datetime.now(timezone.utc)


def bucket_for(dt: datetime) -> str:
    """`taxes-{year}-invoices` bucket name for a given date."""
    return f"taxes-{dt.year}-invoices"


def vendor_domain(from_addr: str | None) -> str:
    """Registrable domain of the sender, as a coarse vendor key.

    HEURISTIC: takes the last two dotted labels of the address domain
    (`billing@hetzner.com` → `hetzner.com`; `noreply@notify.cloudflare.com` →
    `cloudflare.com`). This is WRONG for multi-label public suffixes such as
    `co.uk` / `com.au` (`x@a.co.uk` → `co.uk` instead of `a.co.uk`) — a full fix
    needs the Public Suffix List, which is out of scope here. Falls back to
    `unknown-vendor` when no domain is present.
    """
    if not from_addr or "@" not in from_addr:
        return "unknown-vendor"
    domain = from_addr.rsplit("@", 1)[1].strip().lower().rstrip(".")
    labels = [p for p in domain.split(".") if p]
    if len(labels) >= 2:
        return ".".join(labels[-2:])
    return domain or "unknown-vendor"


def _sanitize_filename(name: str) -> str:
    """Strip path separators and collapse whitespace into single spaces, trim ends."""
    # Drop any directory components (handles both / and \ path separators).
    base = re.split(r"[\\/]", name)[-1]
    base = re.sub(r"\s+", " ", base).strip()
    return base


def object_key(
    *, vendor: str, dt: datetime, filename: str | None, message_id: str | None
) -> str:
    """`{vendor}/{YYYY-MM-DD}-{sanitized_filename}`.

    If `filename` is missing/blank, derive a stable name from `message_id`
    (angle brackets + unsafe chars stripped) with a `.pdf` suffix."""
    date_str = dt.strftime("%Y-%m-%d")
    if filename and filename.strip():
        safe = _sanitize_filename(filename)
    else:
        mid = (message_id or "").strip().strip("<>")
        mid = re.sub(r"[^A-Za-z0-9._@-]", "_", mid) or "message"
        safe = f"{mid}.pdf"
    return f"{vendor}/{date_str}-{safe}"


def sidecar_metadata(
    *,
    vendor: str,
    from_addr: str | None,
    dt: datetime,
    amount: str | None,
    subject: str | None,
    message_id: str | None,
    mail_id: int,
) -> dict:
    """The JSON metadata sidecar written next to each archived PDF.

    `amount` is best-effort and may be None: the archiver does NOT call the LLM; the
    authoritative amount lives in the PDF / is resolved by the downstream tax agent.
    A pre-existing `mail_actions` row's amount (if any) is passed through here.
    """
    return {
        "vendor": vendor,
        "from_addr": from_addr,
        "date": dt.strftime("%Y-%m-%d"),
        "amount": amount,
        "subject": subject,
        "message_id": message_id,
        "mail_id": mail_id,
    }
