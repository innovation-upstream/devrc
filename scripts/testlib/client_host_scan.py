"""THE structural scan for CLIENT hostname literals in tracked files.

WHY
---
`CLAUDE.md`: "This repo is PUBLIC. Never commit a real media-library path,
directory name, filename, route log, **or a real third-party hostname used as an
example**." `scripts/testlib/public_ip_scan.py` gates the IP half of that
sentence. This module gates the hostname half.

WHAT IT LOOKS FOR — AND WHY NOT "ALL HOSTNAMES"
----------------------------------------------
🔴 MEASURED, not assumed. A naive "flag every dotted FQDN" scan over this repo
returns **7,470 distinct tokens**, almost all of them code (`assert.equal`,
`json.dumps`, `path.insert`, `SKILL.md`). Tightening to URL position plus a
curated real-TLD list still leaves ~160 registrable domains — `github.com`,
`nixos.org`, `npmjs.com`, `wikipedia.org` … A gate that demands an allowlist
entry for every documentation link anyone ever pastes is a gate that goes
permanently red, and `claude/RULES.md` is explicit that a permanently-red gate is
worse than none: it trains people to click through.

So the scan is narrow ON PURPOSE, and narrow in the dimension that carries the
disclosure:

    a SUBDOMAIN of a CLIENT registrable domain.

The apex is not the finding. `civitai.com` appears ~100 times in this repo as
prose — the client relationship is not the secret, and never was. What leaks is
the *internal service topology*: `grafana-new.<client>`, `auth.<client>`,
`sish.<client>`, `review-<build hash>.<client>`, `<unreleased product>.<client>`.
Those are exactly what CLAUDE.md means by "a real third-party hostname used as an
example", and six of them were sitting in browser-bridge fixtures, a slash
command and two initiative data dumps when this scan was written.

The domain list is DATA (`CLIENT_DOMAINS`); the regex is DERIVED from it. Adding
a client is one line, and cannot be spelled wrong in two places.

WHAT THIS DOES **NOT** CATCH — read before calling it complete
--------------------------------------------------------------
  * a client hostname under a domain not in `CLIENT_DOMAINS`;
  * the apex itself, by design (see above);
  * a host split across lines, encoded, or punycoded;
  * a hostname in a FILENAME rather than file content — like the IP scan, this
    reads content only, never `path.name`;
  * a client's IP address, which is the OTHER gate's job.

It stops the accidental paste. It is not an exfiltration control.
"""
from __future__ import annotations

import re
from pathlib import Path

#: Registrable domains belonging to CLIENTS / third parties whose internal
#: subdomains must not be written down here. Apexes only — a subdomain in this
#: list would defeat the point of the list.
#:
#: `civit.ai` and `civitai.com` are the same client's two public apexes; both
#: appear in this repo as prose already, which is why they are safe to NAME here
#: while their subdomains are not safe to name anywhere.
CLIENT_DOMAINS = frozenset({
    "civit.ai",
    "civitai.com",
    "civitai.red",
})

#: Directories never scanned — same reasoning as public_ip_scan.SKIP_DIRS, and
#: the same 🔴 trap: this is matched against the path RELATIVE to the root,
#: because every agent worktree lives under `…/.claude/worktrees/<id>/` and an
#: absolute-parts test skips the entire repo while reporting a confident zero.
SKIP_DIRS = frozenset({
    ".git", ".direnv", "result", "node_modules", "__pycache__",
    ".pytest_cache", ".mypy_cache", ".ruff_cache", "worktrees",
})

SKIP_SUFFIXES = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".webp", ".pdf", ".zip", ".gz",
    ".xz", ".zst", ".woff", ".woff2", ".ttf", ".otf", ".so", ".bin", ".wasm",
})

_LABEL = r"[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?"


def _build_re(domains) -> re.Pattern:
    """`(label.)+<domain>` — at least one label, so the apex never matches.

    Three boundary decisions, each MEASURED against a control in
    `scripts/tests/test_no_client_hostnames.py` rather than reasoned:

      * `(?<![\\w.-])` on the left — otherwise `mycivitclient.example` reports a
        truncated tail, and a longer host reports as its own suffix.
      * `(?![\\w-])` on the right — otherwise `<domain>munity` is a hit.
      * `(?!\\.[A-Za-z0-9])` on the right as WELL. Without it a longer,
        unrelated host (`x.<domain>.evil.test`) reports as a client subdomain.
        It is deliberately NOT the simpler `(?![\\w.-])`: that also rejects a
        SENTENCE-FINAL host ("… lives at api.<domain>."), which is a real leak
        written in the most ordinary way there is.

    🔴 IGNORECASE. The browser-bridge suite asserts an UPPERCASE host on purpose
    (it pins case-insensitive frame matching), and the first pass of the scrub
    this gate was written for missed exactly that occurrence. `find_in_line`
    lower-cases what it returns, so a hit is reported in one canonical form.
    """
    alt = "|".join(re.escape(d) for d in sorted(domains))
    return re.compile(
        rf"(?<![\w.-])((?:{_LABEL}\.)+(?:{alt}))(?![\w-])(?!\.[A-Za-z0-9])",
        re.IGNORECASE)


HOST_RE = _build_re(CLIENT_DOMAINS)


def find_in_line(line: str, rx: re.Pattern | None = None) -> list[str]:
    """Every client-subdomain literal in `line`, left to right, deduped."""
    out: list[str] = []
    for m in (rx or HOST_RE).finditer(line):
        tok = m.group(1).lower()
        if tok not in out:
            out.append(tok)
    return out


def scan_text(text: str, rx: re.Pattern | None = None):
    hits = []
    for i, line in enumerate(text.splitlines(), 1):
        for tok in find_in_line(line, rx):
            hits.append((i, tok, line.strip()[:200]))
    return hits


def scan_file(path: Path, rx: re.Pattern | None = None):
    if path.suffix.lower() in SKIP_SUFFIXES:
        return []
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return []
    return scan_text(text, rx)


def _is_skipped(path: Path, root: Path) -> bool:
    return any(part in SKIP_DIRS for part in path.relative_to(root).parts)


def repo_files(root: Path) -> list[Path]:
    """Every candidate file under `root`, sorted.

    Prefers `git ls-files` so the scan covers exactly what is COMMITTED; the nix
    build sandbox gets the flake source with no `.git`, so it falls back to a
    filesystem walk over the same set. Both tiers must see the same files or one
    of them is structurally blind (RULES.md → "two tiers").
    """
    import subprocess

    if (root / ".git").exists():
        try:
            out = subprocess.run(
                ["git", "-C", str(root), "ls-files", "-z"],
                capture_output=True, text=True, check=True, timeout=60).stdout
            return sorted(root / p for p in out.split("\0") if p)
        except (OSError, subprocess.SubprocessError):
            pass
    return sorted(p for p in root.rglob("*")
                  if p.is_file() and not _is_skipped(p, root))


def scan_repo(root: Path, rx: re.Pattern | None = None):
    """`(relpath, lineno, host, stripped_line)` for the whole repo, sorted."""
    hits = []
    for path in repo_files(root):
        if _is_skipped(path, root):
            continue
        for lineno, tok, line in scan_file(path, rx):
            hits.append((str(path.relative_to(root)), lineno, tok, line))
    return sorted(hits)
