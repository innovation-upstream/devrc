#!/usr/bin/env python3
"""Stage 1 — deterministic noise DROP (the high-precision tier).

PURE functions, NO LLM, NO DB. Given a mail row's metadata (from_addr, subject,
category, headers dict), decide whether it is obvious bulk/notification noise that
can be dropped WITHOUT spending an LLM call.

Design contract: this tier must NEVER drop a genuine action item. When in doubt it
KEEPS (lets the LLM judge). It only drops on unambiguous, header-driven signals:
mailing-list / bulk headers, the `alert` category, or a SHORT denylist of senders
that are unambiguously automated notification machinery.

It deliberately does NOT blanket-drop `no-reply@` / `noreply@` — AWS / Google /
verification / password-expiry mail can be action-required, so those survive to the
LLM. It does NOT try to enumerate newsletters; the header signals + LLM cover those.
"""
from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass

# Senders that are unambiguously automated notification machinery. KEEP THIS SHORT —
# header signals do the heavy lifting; the LLM handles ambiguous survivors. Patterns
# are matched case-insensitively with fnmatch (so `*@npmjs.com` covers subdomains).
SENDER_DENYLIST = (
    "notifications@github.com",
    "*@npmjs.com",
    "no-reply@pagerduty.com",
    "notifications@bugsnag.com",
    "notifications@tasks.clickup.com",
)

# Headers whose mere PRESENCE marks the mail as a mailing-list / bulk / auto blast.
_BULK_PRESENCE_HEADERS = ("List-Unsubscribe", "List-Id", "Auto-Submitted")
# Precedence values that mark bulk mail.
_BULK_PRECEDENCE = frozenset({"bulk", "list", "junk"})

# Billing / invoice EXEMPTION. Transactional money mail often ALSO carries bulk
# headers (vendors blast invoices through ESPs — e.g. Cloudflare's invoice arrives
# via sparkpost with List-Id + List-Unsubscribe), so the bulk-header drop above would
# lose genuine action-required bills. This exemption is additive-KEEP only: it rescues
# such mail from the drop so the LLM can judge it — it never drops anything itself, so
# it cannot violate the never-drop-an-action-item contract.
#   • sender allowlist: billing-style local-parts (high precision)
#   • subject regex: HEURISTIC (per RULES — flagged) but tight; covers vendors that
#     send invoices from a generic/noreply address (Cloudflare's noreply@notify.…).
BILLING_SENDER_ALLOWLIST = (
    "billing@*",
    "billing-*@*",
    "invoice@*",
    "invoices@*",
    "invoicing@*",
    "accounts@stripe.com",
    "support@datapacket.com",
)
_BILLING_SUBJECT_RE = re.compile(
    r"\b(invoice|past[\s-]?due|overdue|payment\s+(failed|declined|due)|"
    r"your\s+bill|statement\s+is\s+ready)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class FilterResult:
    """Outcome of the Stage-1 decision for one mail row."""

    drop: bool          # True → label 'bulk', mark processed, NO LLM
    reason: str         # short machine-readable reason (e.g. "header:List-Id")


def _header_get(headers: dict | None, key: str) -> str | None:
    """Case-insensitive header lookup; returns the value or None."""
    if not headers:
        return None
    if key in headers:
        return headers[key]
    lk = key.lower()
    for k, v in headers.items():
        if k.lower() == lk:
            return v
    return None


def _header_present(headers: dict | None, key: str) -> bool:
    """Case-insensitive header presence (a key present with value None still counts)."""
    if not headers:
        return False
    if key in headers:
        return True
    lk = key.lower()
    return any(k.lower() == lk for k in headers)


def _sender_denylisted(from_addr: str | None) -> bool:
    if not from_addr:
        return False
    addr = from_addr.strip().lower()
    return any(fnmatch.fnmatch(addr, pat) for pat in SENDER_DENYLIST)


def _billing_exempt(from_addr: str | None, subject: str | None) -> bool:
    """True if the mail looks like transactional billing/invoice → rescue from bulk drop."""
    addr = (from_addr or "").strip().lower()
    if addr and any(fnmatch.fnmatch(addr, pat) for pat in BILLING_SENDER_ALLOWLIST):
        return True
    return bool(_BILLING_SUBJECT_RE.search(subject or ""))


def classify(
    *,
    from_addr: str | None,
    subject: str | None,
    category: str | None,
    headers: dict | None,
) -> FilterResult:
    """Pure Stage-1 decision for a single mail row.

    Returns FilterResult(drop=True, reason=...) to DROP as bulk noise, or
    FilterResult(drop=False, reason="survivor") to pass on to the LLM tier.
    """
    # 1. category alert — civitai/GPU, handled by ADS elsewhere.
    if (category or "").lower() == "alert":
        return FilterResult(True, "category:alert")

    # 1b. billing/invoice exemption — rescue transactional money mail from the bulk
    # drop below (vendors blast invoices via ESPs, so they carry List-* headers). KEEP
    # → let the LLM judge whether a bill actually needs action.
    if _billing_exempt(from_addr, subject):
        return FilterResult(False, "exempt:billing")

    # 2. mailing-list / bulk / auto headers by presence.
    for h in _BULK_PRESENCE_HEADERS:
        if _header_present(headers, h):
            return FilterResult(True, f"header:{h}")

    # 3. Precedence: bulk/list/junk.
    prec = (_header_get(headers, "Precedence") or "").strip().lower()
    if prec in _BULK_PRECEDENCE:
        return FilterResult(True, f"precedence:{prec}")

    # 4. short automated-notification sender denylist.
    if _sender_denylisted(from_addr):
        return FilterResult(True, "sender:denylist")

    return FilterResult(False, "survivor")
