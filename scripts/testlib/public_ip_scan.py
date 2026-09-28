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

🔴 WHAT THIS SCAN DOES **NOT** CATCH — read this before calling it complete
--------------------------------------------------------------------------
An adversarial audit enumerated forms that carry a routable address past the
regexes below. NONE of them is present in this repo today (checked), and closing
them is deliberately NOT attempted — every one costs false positives on ordinary
source, and this gate's job is stopping the accidental paste, not a determined
author. Documented so nobody mistakes "green" for "there is no address here":

  * a **leading-zero octet** (`203.0.113.009`) — `ipaddress` rejects it, so it is
    a ValueError, not a hit. Shared with `guard_core._public_ips`, which
    delegates to the same stdlib: the seam stays honest, both sides miss it.
  * **decimal-integer** (the plain 32-bit int) and **hex-dotted** forms —
    resolvers accept them, this regex does not.
  * a quad **glued to alphanumerics** (`host203.0.113.9x`), which the word
    boundary rejects on purpose (it is what keeps version strings out).
  * **split across lines** — the scan is line-at-a-time by construction.
  * **base64 / any encoding** of the literal.
  * a **fullwidth or unicode dot** between octets.
  * IPv6 with a **`%zone` suffix**.
  * the value in a **FILENAME** rather than file CONTENT — `scan_file` never
    inspects `path.name`.
  * an address whose every hextet is DECIMAL, written inside a Python subscript
    (`x[1234::5678]`) — see the slice carve-out below. That shape is the price of
    the carve-out and it is DRIVEN, not merely asserted, by
    `test_no_public_ips.py::test_the_slice_carveout_is_blind_to_a_decimal_only_
    subscript`. Change one and you are told about the other.

🔴 IPv6 FALSE POSITIVE, MEASURED. A naive IPv6 regex matches `DB::` inside
`Code: 209. DB::Exception: …` — and `ipaddress.ip_address("DB::")` parses fine
and reports `is_global`. Four such lines exist in this repo (ClickHouse error
strings). A reportable IPv6 literal must therefore carry at least
`MIN_IPV6_HEXTETS` non-empty hextets; `DB::` has one.

🔴 SECOND IPv6 FALSE POSITIVE, MEASURED — and the hextet floor does NOT catch it.
Ordinary Python extended-slice syntax IS a compressed IPv6 literal: in the `nix
build` sandbox tier a line reading `zip(parts[1::2], parts[2::2])` was reported as
TWO committed public addresses, because each subscript parses as an address, is
`is_global`, and carries two hextets. `MIN_IPV6_HEXTETS` cannot reach them —
raising the floor to three would stop reporting genuine two-hextet addresses
everywhere, which is a hole, not a fix. At the time this was found NO tracked
`.py` file in the repo used `[N::M]` slicing, so the gate had been green by luck;
the next extended slice anyone wrote would have reddened `main`.

`is_subscript_slice()` is the carve-out, and it is deliberately NOT "ignore
anything inside brackets" — that would be a way to commit a real address. It
requires all three of: bracket ENCLOSURE, SUBSCRIPT position (the `[` follows an
identifier / `)` / `]` / a closing quote, so a list display or a bracketed URL
host is untouched), and the token having Python slice ARITY AND DIGITS (at most
`_MAX_SLICE_COMPONENTS` colon-separated parts, each empty or DECIMAL). A real
address in a subscript keeps being reported on any one of those failing, which is
what the negative controls in `test_no_public_ips.py` pin.

⚠ Illustrations here are `2001:db8::` (a DOC_NETWORKS address, so not reportable)
or written INSIDE a subscript — spelling a bare routable literal in this file
would make it match its own scan, the trap noted at IPV6_RE point 2. That also
makes this docstring a reachability signal: the `parts[1::2]` above is only clean
because the carve-out runs, and `test_this_guards_own_sources_are_clean` reads it.
"""
from __future__ import annotations

import ipaddress
import re
import sys
from pathlib import Path

from . import skip_dirs as _skip_dirs

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

#: Characters that may precede a `[` which OPENS A SUBSCRIPT: an identifier, a
#: closing paren/bracket, or a string literal's closing quote (`parts[…]`,
#: `f()[…]`, `x[0][…]`, `"abc"[…]`). A `[` preceded by ANYTHING else — a space,
#: `=`, `/`, `:`, or start-of-line — opens a list/array display or brackets a
#: host, and that is exactly where a real address is legitimately written
#: (`lighthouse: [2001:db8::1]`, `https://[2001:db8::1]:443/`). Keeping those two
#: cases apart is what stops the carve-out becoming a place to hide an address.
SUBSCRIPTABLE_CHARS = frozenset("0123456789"
                                "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
                                "abcdefghijklmnopqrstuvwxyz"
                                "_)]'\"")

#: Python slice arity: `start:stop:step` is THREE colon-separated components, so
#: at most two colons. An 8-hextet address whose every hextet happens to be
#: decimal (no `a`-`f` anywhere) is all-digits like a slice but cannot BE one —
#: this bound is the only thing that still reports it, and a negative control in
#: `test_no_public_ips.py` drives exactly that token. (Described, not quoted: it
#: is routable, so spelling it here would make this file match its own scan.)
_MAX_SLICE_COMPONENTS = 3

_DECIMAL_RE = re.compile(r"[0-9]+")

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
#:
#: 🔴 THE BASE SET AND NOTHING ELSE. This is a SECURITY gate on a PUBLIC repo:
#: every name added here is a directory a real address may be committed into
#: unseen. In particular it must never inherit `.claude`/`claudedocs` from the
#: `scripts/tests/` ledgers that share `skip_dirs.GENERATED` -- `claudedocs/` is
#: committed prose and one of the likeliest places an address gets written down.
#: `test_skip_dirs_ledger.py` pins this two-way. See `testlib/skip_dirs.py`.
SKIP_DIRS = _skip_dirs.GENERATED

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


def is_subscript_slice(line: str, start: int, end: int) -> bool:
    """True when `line[start:end]` is a Python subscript slice, not an address.

    MEASURED false positive, and the reason this exists: `zip(parts[1::2],
    parts[2::2])` reported two routable public addresses. See the module
    docstring.

    🔴 THREE conditions, ALL required, because any one of them alone is a hole:

      1. **enclosure** — the token is exactly what sits between `[` and `]`;
      2. **subscript position** — the `[` follows something SUBSCRIPTABLE
         (`SUBSCRIPTABLE_CHARS`). This is what leaves `lighthouse: [2001:db8::1]`
         and `https://[2001:db8::1]:443/` reportable: those brackets follow a
         space and a `/`, so they are a list display and a URL host, not a
         subscript;
      3. **slice shape** — at most `_MAX_SLICE_COMPONENTS` colon-separated
         components, each empty or DECIMAL. A hextet containing any of `a`-`f`
         (`x[2001:db8::1]`) is an address in a subscript and stays reportable, and
         so is an all-decimal run too long to be a slice.

    Evaluated per-line and per-match, so it costs nothing on the IPv4 pass: a
    dotted quad is one component and never all-decimal, so (3) rejects it.
    """
    token = line[start:end]
    if start == 0 or line[start - 1] != "[":
        return False
    if end >= len(line) or line[end] != "]":
        return False
    if start < 2 or line[start - 2] not in SUBSCRIPTABLE_CHARS:
        return False
    components = token.split(":")
    if len(components) > _MAX_SLICE_COMPONENTS:
        return False
    return all(_DECIMAL_RE.fullmatch(c) for c in components if c)


def find_in_line(line: str) -> list[str]:
    """Every reportable literal in `line`, left to right, deduped in order."""
    out: list[str] = []
    for rx in (IPV4_RE, IPV6_RE):
        for m in rx.finditer(line):
            tok = m.group(0)
            if is_subscript_slice(line, m.start(), m.end()):
                continue
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
            pass  # fall through to the walk  # pragma: no cover - not raised over the tracked corpus
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
            continue  # pragma: no cover - SKIP_DIRS are gitignored, so the git ls-files path never yields one; on the rglob fallback repo_files has already filtered. Load-bearing on the git path
        for lineno, tok, line in scan_file(path):
            hits.append((str(path.relative_to(root)), lineno, tok, line))
    return sorted(hits)
