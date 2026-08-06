"""THE structural scan for routable public IP literals in tracked files.

WHY
---
This repo is PUBLIC (`gh repo view --json visibility`), and `CLAUDE.md` says so:
"Never commit a real media-library path, directory name, filename, route log, or
a real third-party hostname used as an example." A production cluster's public
IP is the same class of disclosure, and one sat in
`scripts/opencode/agent/k8s.md` from #276 until it was found by hand.

Hand-finding it is the problem. This module makes it deterministic.

ONE RULE, ONE PLACE
-------------------
The IPv4 predicate is NOT reimplemented here. `scripts/claude-hooks/guard_core.py`
already owns it — `_public_ips()`, the thing that denies a `git commit` carrying a
public IP — and a second copy would be wrong in one of the two places (RULES.md →
"One rule, one place"). This module DELEGATES IPv4 to it and adds only what a
file scan needs on top: IPv6, file walking, and a doc-range carve-out that the
command guard has no reason to carry.

`scripts/tests/test_no_public_ips.py` pins that delegation with a seam test, so
the two cannot drift apart silently.

WHAT COUNTS AS REPORTABLE
-------------------------
A literal is reportable when `ipaddress` says it is **globally routable** and it
is not one of the ranges that exist precisely so they can be written down:

  * RFC1918 private (10/8, 172.16/12, 192.168/16) — the operator's own LAN, and
    the nebula 10.42/16 overlay, which is inside 10/8 already
  * loopback, link-local, multicast, unspecified, reserved
  * CGNAT 100.64/10
  * documentation ranges: TEST-NET-1/2/3 (192.0.2/24, 198.51.100/24,
    203.0.113/24) and IPv6 2001:db8::/32
  * IPv6 ULA fc00::/7 (covered by `is_private`)

Everything else is reported with `file:line`. The CALLER owns the allowlist —
this module has no policy, deliberately, so the reasons live next to the
assertions that grant them.

🔴 IPv6 FALSE POSITIVE, MEASURED. A naive IPv6 regex matches `DB::` inside
`Code: 209. DB::Exception: …` — and `ipaddress.ip_address("DB::")` parses fine
and reports `is_global`. Four such lines exist in this repo (ClickHouse error
strings). A reportable IPv6 literal must therefore carry at least
`MIN_IPV6_HEXTETS` non-empty hextets; `DB::` has one.
"""
from __future__ import annotations

import ipaddress
import re
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
# guard_core is a hook module, not a package; it is import-safe (its only
# top-level work is building regexes — the CLI is behind `if __name__`).
sys.path.insert(0, str(_REPO / "scripts" / "claude-hooks"))
import guard_core as _gc  # noqa: E402

# IPv4 candidates come from guard_core's regex, so the two scanners cannot
# disagree about what even LOOKS like an address.
IPV4_RE = _gc.IPV4_RE

# Colon-separated hextet runs, INCLUDING `::`-compressed forms and the
# IPv4-in-IPv6 tail (`::ffff:10.244.0.123`). Three things here are MEASURED
# against this repo, not guessed:
#
#   1. `(hextet? :){2,7} tail` — NOT the usual `(hextet :){1,7}(: | hextet)`,
#      which stops at the `::` in `2a01:…:b3f2::1` and reports a TRUNCATED
#      literal. Caught by the planted-IPv6 positive control.
#   2. The boundary excludes ALPHANUMERICS, not just hex characters. `::` is a
#      scope operator in half the world's languages, and a hex-only boundary
#      chopped a fake address out of the hex-ish letters either side of it in
#      ClickHouse `Exception` strings, i3 `workspace` event names and a repo
#      path joined with a double colon — ELEVEN false positives across this
#      repo, every one of which `ipaddress` then calls globally routable.
#      (Deliberately described, not quoted: writing the truncated tokens here
#      would make THIS file match its own scan, which is how the first draft
#      failed.)
#   3. The IPv4 tail alternative is tried FIRST so an IPv4-mapped address is
#      consumed whole and classified as the private address it is, instead of
#      being truncated before the dotted quad and reported.
IPV6_RE = re.compile(
    r"(?<![0-9A-Za-z:._-])"
    r"(?:[0-9A-Fa-f]{0,4}:){2,7}"
    r"(?:(?:\d{1,3}\.){3}\d{1,3}|[0-9A-Fa-f]{0,4})"
    r"(?![0-9A-Za-z:_-])")

#: A `::`-compressed literal needs this many non-empty hextets to be treated as
#: an address at all. See the DB:: note in the module docstring.
MIN_IPV6_HEXTETS = 2

#: Ranges reserved for documentation. `ipaddress` reports TEST-NET-2/3 as
#: non-global already, but TEST-NET-1 (192.0.2.0/24) and 2001:db8::/32 vary by
#: Python version, so they are named explicitly rather than assumed.
DOC_NETWORKS = (
    ipaddress.ip_network("192.0.2.0/24"),      # TEST-NET-1
    ipaddress.ip_network("198.51.100.0/24"),   # TEST-NET-2
    ipaddress.ip_network("203.0.113.0/24"),    # TEST-NET-3
    ipaddress.ip_network("2001:db8::/32"),     # IPv6 documentation
)

#: Directories never scanned. `.claude/worktrees` matters most: on a dev host it
#: holds FULL copies of this repo, so without it the scan re-reports every other
#: agent's tree as if it were this one.
SKIP_DIRS = frozenset({
    ".git", ".direnv", "result", "node_modules", "__pycache__",
    ".pytest_cache", ".mypy_cache", ".ruff_cache", "worktrees",
})

SKIP_SUFFIXES = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".webp", ".pdf", ".zip", ".gz",
    ".xz", ".zst", ".woff", ".woff2", ".ttf", ".otf", ".so", ".bin", ".wasm",
})


def is_reportable(token: str) -> bool:
    """True when `token` is a globally-routable address literal worth flagging."""
    try:
        ip = ipaddress.ip_address(token)
    except ValueError:
        return False  # octet > 255, a version string, a truncated hextet run
    if ip.version == 6 and _hextets(token) < MIN_IPV6_HEXTETS:
        return False
    if not ip.is_global or ip.is_multicast:
        return False
    return not any(ip in net for net in DOC_NETWORKS if net.version == ip.version)


def _hextets(token: str) -> int:
    return len([h for h in token.split(":") if h])


def find_in_line(line: str) -> list[str]:
    """Every reportable literal in `line`, left to right, deduped in order."""
    out: list[str] = []
    for rx in (IPV4_RE, IPV6_RE):
        for m in rx.finditer(line):
            tok = m.group(0)
            if is_reportable(tok) and tok not in out:
                out.append(tok)
    return out


def scan_text(text: str) -> list[tuple[int, str, str]]:
    """`(lineno, literal, stripped_line)` for every reportable hit in `text`."""
    hits: list[tuple[int, str, str]] = []
    for i, line in enumerate(text.splitlines(), 1):
        for tok in find_in_line(line):
            hits.append((i, tok, line.strip()))
    return hits


def scan_file(path: Path) -> list[tuple[int, str, str]]:
    if path.suffix.lower() in SKIP_SUFFIXES:
        return []
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return []  # binary or unreadable — nothing a reviewer would read either
    return scan_text(text)


def repo_files(root: Path) -> list[Path]:
    """Every candidate file under `root`, sorted.

    Prefers `git ls-files` so the scan covers exactly what is COMMITTED. The nix
    build sandbox gets the flake source without a `.git` dir, so it falls back to
    a filesystem walk — which over that tree is the same set. Keeping both tiers
    on the tracked set is the point: a suite whose file list differs per tier is
    structurally blind in one of them (RULES.md → "two tiers").
    """
    import subprocess

    if (root / ".git").exists():
        try:
            out = subprocess.run(
                ["git", "-C", str(root), "ls-files", "-z"],
                capture_output=True, text=True, check=True, timeout=60).stdout
            return sorted(root / p for p in out.split("\0") if p)
        except (OSError, subprocess.SubprocessError):
            pass  # fall through to the walk
    return sorted(
        p for p in root.rglob("*")
        if p.is_file() and not _is_skipped(p, root)
    )


def _is_skipped(path: Path, root: Path) -> bool:
    """Skip-dir test, evaluated RELATIVE to `root`.

    🔴 Against absolute parts this silently skips the ENTIRE repo when the
    checkout itself sits under a skipped name — every agent worktree lives in
    `…/.claude/worktrees/<id>/`, so `"worktrees" in path.parts` was true for
    every file and the scan reported a clean zero over nothing. The positive
    control in test_no_public_ips.py is what caught it.
    """
    return any(part in SKIP_DIRS for part in path.relative_to(root).parts)


def scan_repo(root: Path) -> list[tuple[str, int, str, str]]:
    """`(relpath, lineno, literal, stripped_line)` for the whole repo, sorted."""
    hits: list[tuple[str, int, str, str]] = []
    for path in repo_files(root):
        if _is_skipped(path, root):
            continue
        for lineno, tok, line in scan_file(path):
            hits.append((str(path.relative_to(root)), lineno, tok, line))
    return sorted(hits)
