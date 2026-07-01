#!/usr/bin/env python3
"""REPLY-FEEDBACK loop — pull Zach's emailed reply to the LAST digest, so the NEXT run
can feed his steering into synthesis as CONTEXT.

The digest goes out Zach→Zach (zachlowden1@gmail.com), so his reply lands in his OWN
Gmail. We read it back over IMAP with the SAME Gmail app-password repo-cos already uses
for SMTP send (`email_send.load_credentials()` → SOPS `mailbox-gmail-imap`). NO new deps
(imaplib is stdlib), NO homelab-cluster / Postgres / kubectl path.

Design contract:
  * BEST-EFFORT + SAFE. Any failure — no creds, IMAP down, no reply found, parse error —
    is logged to stderr and returns None. This NEVER crashes the weekly run.
  * The previous digest's `subject` + `generated_at` come from ~/.config/repo-cos/latest.json
    (missing → no prior digest → return None).
  * Matching is robust to Gmail quirks: we SEARCH on the ASCII-stable subject core
    (`digest.SUBJECT_CORE`, e.g. "Repo proposals") — the full subject's emoji + em-dash are
    non-ASCII and flaky in IMAP SEARCH — restricted to mail SINCE the digest date, then pick
    the most-recent message that is a genuine REPLY FROM Zach (subject starts "Re:" and/or
    carries In-Reply-To/References). We look in INBOX and, if needed, "[Gmail]/All Mail".
  * The reply body is de-quoted: lines starting ">" are dropped, and everything from an
    "On <date> … wrote:" attribution onward is cut, leaving only Zach's NEW words. Capped.

Returned to synthesis: a `Feedback` with the cleaned reply text + the previous proposals
(title + first evidence ref) so the model can reference exactly what it's steering off.
"""
from __future__ import annotations

import email
import imaplib
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from email.header import decode_header, make_header
from email.message import Message
from pathlib import Path

# Local imports (scan.py already puts this dir on sys.path; guard for standalone use).
try:
    import digest  # noqa: E402
except ImportError:  # pragma: no cover - import guard for direct execution
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import digest  # noqa: E402
import email_send  # noqa: E402

IMAP_HOST = "imap.gmail.com"
IMAP_PORT = 993
IMAP_TIMEOUT = 30  # short — a slow/hung IMAP must not stall the weekly run
ALL_MAIL = "[Gmail]/All Mail"
MAX_REPLY_CHARS = 4000

PERSIST_LATEST = Path("~/.config/repo-cos/latest.json").expanduser()

# "On Mon, 1 Jul 2026 at 08:00, Zach <…> wrote:" — Gmail's quote attribution line. Once we
# hit it, everything after is quoted history, not Zach's new words. Kept permissive (the
# locale/format varies) but anchored on a leading "On " AND the "… wrote:" tail, so a
# legitimate reply line like "the note I wrote:" is NOT mistaken for an attribution.
_ATTRIBUTION_RE = re.compile(r"^\s*On\b.+\bwrote:\s*$", re.IGNORECASE)


@dataclass
class Feedback:
    """What synthesis needs to honor last week's steering."""
    reply_text: str
    prev_proposals: list[dict] = field(default_factory=list)  # {title, evidence}
    replied_at: str = ""  # the digest's generated_at, for the stderr log

    def prev_summary(self) -> list[str]:
        """Compact `title — evidence` lines for the prompt (first ref only)."""
        out = []
        for p in self.prev_proposals:
            title = str(p.get("title") or "").strip()
            if not title:
                continue
            ev = p.get("evidence") or []
            first = str(ev[0]).strip() if ev else ""
            out.append(f"{title} — {first}" if first else title)
        return out


def _log(msg: str) -> None:
    print(f"  feedback: {msg}", file=sys.stderr)


def _load_last_digest() -> dict | None:
    """Read ~/.config/repo-cos/latest.json (the previous run). None if absent/unreadable."""
    try:
        if not PERSIST_LATEST.exists():
            return None
        return json.loads(PERSIST_LATEST.read_text())
    except Exception as exc:  # noqa: BLE001
        _log(f"could not read {PERSIST_LATEST}: {exc}")
        return None


def _decode(value: str | None) -> str:
    """Decode a possibly RFC2047-encoded header to a str; never raises."""
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:  # noqa: BLE001
        return value


def _since_token(generated_at: str) -> str:
    """IMAP SINCE date (DD-Mon-YYYY) one day BEFORE the digest, so timezone skew between
    the ISO `generated_at` and Gmail's server-side date can't hide a same-day reply."""
    try:
        dt = datetime.fromisoformat(generated_at)
    except Exception:  # noqa: BLE001
        dt = datetime.now().astimezone() - timedelta(days=8)
    dt = dt - timedelta(days=1)
    return dt.strftime("%d-%b-%Y")


def _is_reply_from_owner(msg: Message) -> bool:
    """A genuine reply from Zach: FROM his address AND (subject starts Re: OR it threads via
    In-Reply-To/References). The subject-core match already scoped us to the digest thread;
    this rejects the ORIGINAL digest (Zach→Zach, but no Re:/In-Reply-To) so we don't feed
    the model its own prior output as "feedback"."""
    from email.utils import parseaddr
    # EXACT addr-spec match (not substring) — a display-name or lookalike domain
    # containing the address (e.g. `zachlowden1@gmail.com.evil.com`) must NOT pass.
    frm_addr = parseaddr(_decode(msg.get("From")))[1].strip().lower()
    if frm_addr != email_send.OWNER_EMAIL.lower():
        return False
    subj = _decode(msg.get("Subject")).strip().lower()
    is_re = subj.startswith("re:")
    threads = bool(msg.get("In-Reply-To") or msg.get("References"))
    return is_re or threads


def _plain_body(msg: Message) -> str:
    """Extract the text/plain body (first such part for multipart). Falls back to the
    payload decoded as text. Never raises."""
    try:
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_type() == "text/plain" and \
                        "attachment" not in str(part.get("Content-Disposition", "")).lower():
                    payload = part.get_payload(decode=True)
                    if payload is not None:
                        return payload.decode(part.get_content_charset() or "utf-8",
                                              errors="replace")
            return ""
        payload = msg.get_payload(decode=True)
        if payload is None:
            return str(msg.get_payload())
        return payload.decode(msg.get_content_charset() or "utf-8", errors="replace")
    except Exception as exc:  # noqa: BLE001
        _log(f"body extract failed: {exc}")
        return ""


def strip_quoted(body: str) -> str:
    """Return only Zach's NEW words: drop the quoted digest.

    Rules (applied line-by-line, top-down):
      * once an "On … wrote:" attribution line is seen, cut EVERYTHING from there on
        (that's Gmail's reply history);
      * drop any line starting with ">" (quoted text);
      * stop at a "-- " signature separator.
    Collapses surrounding blank lines and caps length.
    """
    kept: list[str] = []
    for raw in (body or "").splitlines():
        line = raw.rstrip()
        if _ATTRIBUTION_RE.match(line):
            # SKIP the attribution line but keep scanning — do NOT cut here, or a
            # bottom-posted reply (quote first, new text below) loses everything.
            # Gmail >-prefixes every quoted line, so the quote itself is dropped below;
            # only the (unprefixed) attribution needs removing.
            continue
        if line.strip() == "--":  # signature delimiter (some clients omit the trailing space)
            break
        if line.lstrip().startswith(">"):
            continue
        kept.append(line)
    text = "\n".join(kept).strip()
    # collapse 3+ blank lines to a single blank
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
    return text[:MAX_REPLY_CHARS].strip()


def _search_uids(imap: imaplib.IMAP4_SSL, core: str, since: str) -> list[bytes]:
    """IMAP SEARCH for the subject-core SINCE the digest date. Returns UID bytes list
    (newest-relevant handled by the caller). Empty on any failure."""
    try:
        typ, data = imap.search(None, "SINCE", since, "SUBJECT", f'"{core}"')
        if typ != "OK" or not data or not data[0]:
            return []
        return data[0].split()
    except Exception as exc:  # noqa: BLE001
        _log(f"search failed: {exc}")
        return []


def _best_reply_in_mailbox(imap: imaplib.IMAP4_SSL, mailbox: str, core: str,
                           since: str) -> str | None:
    """Select `mailbox`, find the most-recent genuine reply-from-owner matching the subject
    core, return its cleaned body (or None)."""
    try:
        typ, _ = imap.select(f'"{mailbox}"', readonly=True)
        if typ != "OK":
            return None
    except Exception as exc:  # noqa: BLE001
        _log(f"select {mailbox} failed: {exc}")
        return None

    uids = _search_uids(imap, core, since)
    if not uids:
        return None
    # newest first — IMAP SEARCH returns ascending sequence numbers
    for num in reversed(uids):
        try:
            typ, msg_data = imap.fetch(num, "(RFC822)")
            if typ != "OK" or not msg_data or not msg_data[0]:
                continue
            raw = msg_data[0][1]
            if not isinstance(raw, (bytes, bytearray)):
                continue
            msg = email.message_from_bytes(raw)
        except Exception as exc:  # noqa: BLE001
            _log(f"fetch/parse failed: {exc}")
            continue
        if not _is_reply_from_owner(msg):
            continue
        cleaned = strip_quoted(_plain_body(msg))
        if cleaned:
            return cleaned
    return None


def fetch_last_feedback(*, _creds=None, _imap_factory=None) -> Feedback | None:
    """Pull Zach's reply to the previous digest as steering CONTEXT for the next synthesis.

    Best-effort: returns None (never raises) if there's no prior digest, no creds, IMAP is
    unreachable, or no reply is found. `_creds` / `_imap_factory` are injectable for tests
    (no real network).
    """
    last = _load_last_digest()
    if not last:
        _log("no previous digest (latest.json missing) — skipping")
        return None
    subject = str(last.get("subject") or "")
    generated_at = str(last.get("generated_at") or "")
    prev_proposals = [
        {"title": p.get("title"), "evidence": p.get("evidence") or []}
        for p in (last.get("proposals") or []) if isinstance(p, dict)
    ]

    core = digest.SUBJECT_CORE
    if core not in subject and subject:
        # Subject drifted from our known format — fall back to the constant anyway (that's
        # what we'd have sent), but note it.
        _log(f"prev subject {subject!r} lacks core {core!r}; matching on core")
    since = _since_token(generated_at)

    creds = _creds or email_send.load_credentials
    try:
        user, password = creds()
    except Exception as exc:  # noqa: BLE001
        _log(f"no credentials ({exc}) — skipping")
        return None

    imap = None
    try:
        if _imap_factory is not None:
            imap = _imap_factory()
        else:
            imap = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT, timeout=IMAP_TIMEOUT)
        imap.login(user, password)
        reply = _best_reply_in_mailbox(imap, "INBOX", core, since)
        if reply is None:
            reply = _best_reply_in_mailbox(imap, ALL_MAIL, core, since)
    except Exception as exc:  # noqa: BLE001
        _log(f"IMAP error ({exc}) — skipping")
        return None
    finally:
        if imap is not None:
            try:
                imap.logout()
            except Exception:  # noqa: BLE001
                pass

    if not reply:
        _log("no reply found for the previous digest")
        return None

    _log(f"applied reply from {generated_at or 'unknown'} ({len(reply)} chars)")
    return Feedback(reply_text=reply, prev_proposals=prev_proposals,
                    replied_at=generated_at)
