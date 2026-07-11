#!/usr/bin/env python3
"""scrub — redact secrets from a transcript BEFORE anything reads it.

Transcripts routinely contain pasted API keys and private keys. The scrubbed
text is what lands in the staging `input.json` on disk, what the live session /
its subagents read, and what could otherwise be echoed into a `brief_summary`
and shipped to ClickHouse. So we scrub at the source (prepare.py) — the whole
transcript is leaving to the model.

The patterns are VENDORED from `~/.claude/hooks/bash-guard.py` (its
`SECRET_PATTERNS` + private-key block + `ipaddress.is_global` public-IP
detector). That hook is the source of truth but lives OUTSIDE this repo and
cannot be imported reliably, so the regexes are copied here with this citation.
Two deliberate divergences from bash-guard:

  * NO publish-sink gate — bash-guard only redacts at a git/gh publish sink; here
    redaction is UNCONDITIONAL because the entire transcript is handed to a model.
  * Public-IP redaction is present but DEFAULT OFF (`--redact-public-ips` /
    `INSIGHT_REDACT_IPS=1`). Zach's infra work is full of internal RFC1918 /
    nebula / NodePort IPs that are NOT sensitive and whose redaction would
    degrade summary usefulness; the staging file never leaves the authed host.
"""
from __future__ import annotations

import ipaddress
import re

# --------------------------------------------------------------------------- #
# Secret token patterns — (compiled regex, short label slug).
# Vendored from bash-guard.py SECRET_PATTERNS (labels shortened to slugs so the
# redaction token reads `<REDACTED:aws-key>` etc.).
# --------------------------------------------------------------------------- #
SECRET_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "aws-key"),
    (re.compile(r"\bASIA[0-9A-Z]{16}\b"), "aws-temp-key"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b"), "github-token"),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{40,}\b"), "github-pat"),
    (re.compile(r"\bglpat-[A-Za-z0-9_-]{20,}"), "gitlab-token"),
    (re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}"), "anthropic-key"),
    (re.compile(r"\bsk-or-v1-[A-Za-z0-9]{20,}"), "openrouter-key"),
    (re.compile(r"\bsk-proj-[A-Za-z0-9_-]{20,}"), "openai-key"),
    (re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}"), "slack-token"),
    (re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"), "google-key"),
]

# Private-key BLOCK (BEGIN…END, multiline). bash-guard only matches the BEGIN
# line (it denies the command); here we redact the WHOLE block.
PRIVATE_KEY_RE = re.compile(
    r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?-----END [A-Z0-9 ]*PRIVATE KEY-----",
    re.DOTALL,
)

# IPv4 candidate (validated with `ipaddress` before redaction).
IPV4_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")


def _redact_public_ips(text: str, counts: dict) -> str:
    def repl(m: re.Match) -> str:
        try:
            ip = ipaddress.ip_address(m.group(0))
        except ValueError:
            return m.group(0)  # octet > 255 etc. → a version string, not an IP
        if ip.is_global and not ip.is_multicast:
            counts["public-ip"] = counts.get("public-ip", 0) + 1
            return "<REDACTED:public-ip>"
        return m.group(0)  # internal / RFC1918 / nebula / loopback survives
    return IPV4_RE.sub(repl, text)


def scrub(text: str, redact_public_ips: bool = False) -> tuple[str, dict]:
    """Redact secrets from `text`.

    Returns `(scrubbed_text, redaction_counts)` where `redaction_counts` maps a
    label slug → how many matches were redacted (e.g. {"aws-key": 1,
    "private-key": 1}). The counts go into input.json so the model is TOLD "N
    secrets were redacted here" and never mistakes a `<REDACTED:...>` token for
    content.

    Order: private-key blocks first (largest, multiline), then the token
    patterns, then (optionally) public IPs — so a key embedded in a block is not
    double-counted by a token pattern.
    """
    if not text:
        return text or "", {}
    counts: dict = {}

    def _block_repl(_m: re.Match) -> str:
        counts["private-key"] = counts.get("private-key", 0) + 1
        return "<REDACTED:private-key>"

    out = PRIVATE_KEY_RE.sub(_block_repl, text)

    for rx, label in SECRET_PATTERNS:
        def _tok_repl(_m: re.Match, _label=label) -> str:
            counts[_label] = counts.get(_label, 0) + 1
            return f"<REDACTED:{_label}>"
        out = rx.sub(_tok_repl, out)

    if redact_public_ips:
        out = _redact_public_ips(out, counts)

    return out, counts
