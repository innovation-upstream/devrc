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
  * an address whose every hextet is DECIMAL, written inside a Python subscript —
    see the slice carve-out below. 🔴 STATE ITS REACH HONESTLY: this is NOT a
    family of implausible values. `26xx::`, `28xx::` and `30xx::` prefixes are
    allocated ARIN/APNIC/RIPE global-unicast space, and a short host address in one
    of them has all-decimal hextets and a slice's arity, so it is inside this gap
    (MEASURED: five such addresses are `is_reportable()` yet unreported inside a
    subscript). What bounds the cost is the POSITION: the gap is only the
    bare-unquoted-inside-a-subscript one. The same values are still reported bare,
    quoted, in a list display, in a URL host, in the bracketed-host spelling
    `ssh [<addr>]` — the reason condition (3) skips no blank — and in the QUOTED
    endpoint spelling `"[<addr>]:port"`, the reason it checks quote parity. That is
    not how an address is written in any file this gate scans.

    🔴 THE POSITION LIST IS THE CLAIM, SO IT IS ENUMERATED AND ASSERTED, NOT ARGUED:
    every position named above is driven by
    `test_no_public_ips.py::test_the_slice_carveout_is_blind_to_a_decimal_only_
    subscript`, which fails if any of them stops reporting. That test is the only
    thing standing between this paragraph and a false claim — an audit found the
    quoted-endpoint position exempt while this prose already said "quoted", because
    the position was described here and not asserted there. Add a position to the
    prose and add it to that test in the same commit, or do neither.

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
requires all three of: Python slice ARITY AND DIGITS (at most
`_MAX_SLICE_COMPONENTS` colon-separated parts, each empty or DECIMAL), being a
WHOLE comma-separated group of a bracketed list (blanks allowed either side, so
`grid[1::2, ::3]`, `arr[mask, 1::2]` and `x[ 1::2 ]` are covered), and SUBSCRIPT
position (the group's `[` is ADJACENT to an identifier / `)` / `]` / a closing
quote, so a list display, a bracketed URL host and `ssh [<addr>]` are untouched).
A real address keeps being reported on any one of those failing, which is what the
negative controls in `test_no_public_ips.py` pin at every one of those shapes.

⚠ Two shapes it deliberately does NOT cover, both still REPORTED (false positive,
never a hole): a blank BETWEEN the name and the `[` — PEP8-illegal (E211), and
exempting it would exempt `ssh [<addr>]` — and a subscript split across lines,
which is outside a line-at-a-time scan by construction. (That first shape is one
this file cannot write out: outside a subscript the token is reportable, so
spelling it would plant a finding here. It is assembled by
`test_no_public_ips.py::uncovered_slice_lines`, which pins it.)

🔴 The first wording of this carve-out covered only the ADJACENT SINGLE-SLICE
subset while claiming the whole class — a guard whose DESCRIPTION was wider than
its implementation, in a change whose entire point was a coverage claim. An audit
caught it, and then caught this paragraph making the same mistake about the FIX:

**What is pinned, exactly** — two axes, two mechanisms, and a gap between them:
  * SLICE SPELLING. `COVERED_SLICE_LINES` must all be silent
    (`test_python_extended_slices_are_not_addresses`) and everything
    `uncovered_slice_lines()` returns must still be reported
    (`test_the_covered_slice_shapes_are_enumerated`). Two directions, two tests.
  * BRACKET POSITION. Enumerated only in the blind-spot bound assertion in
    `test_the_slice_carveout_is_blind_to_a_decimal_only_subscript`.

⚠ Neither list carries a position axis, so **a widening that swallows a shape
appearing in NEITHER list and in NO enumerated position fails nothing.** That is not
hypothetical — it is exactly how the quoted-endpoint false negative shipped. An
earlier wording of this paragraph claimed the two lists pinned the rule "in both
directions … so widening the rule without moving a shape between them fails the
suite", which a reader would trust and stop looking at. When you widen this rule, ask
which POSITION you have newly exempted and add it to the bound assertion; the lists
will not tell you.

🔴 QUOTED ENDPOINT — the FALSE NEGATIVE this carve-out shipped, and the reason
`quote_is_closing()` exists. `SUBSCRIPTABLE_CHARS` holds both quote characters so
that `"abc"[1::2]` is a subscript; a line-at-a-time scan cannot tell an opening
quote from a closing one. So the canonical IPv6 endpoint spelling — `bind:
"[<addr>]:53"`, `addr = "[<addr>]:443"`, `REDIS_URL="[<addr>]:6379"`,
`net.Dial("tcp", "[<addr>]:80")`, `{"upstream": "[<addr>]:8080"}` — had a quote
before the `[` and was EXEMPT, in YAML, JSON, `.env`, shell, Go and Python alike.
The base branch reported every one of those lines. Bounded but real: only the
all-decimal-hextet family could take that path (a hex hextet is still rejected by
slice shape, measured), so it widened the accepted family's POSITIONS rather than
the family — and it collapsed the very bound that makes that family acceptable,
which is why it was a blocker and not part of the accepted gap. The fix is parity,
not deleting the quotes from the set: deleting them reddens the `"abc"[1::2]`
control, measured.

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

#: The two quote characters. A quote before a `[` is AMBIGUOUS in a line-at-a-time
#: scan — it closes a string in `"abc"[1::2]` and OPENS one in `bind: "[<addr>]:53"`
#: — so membership in `SUBSCRIPTABLE_CHARS` is not enough on its own;
#: `quote_is_closing()` decides which, and `is_subscript_slice` calls it. See the
#: QUOTED ENDPOINT note in the module docstring: taking these on trust exempted the
#: canonical IPv6 endpoint spelling in YAML/JSON/.env/shell/Go/Python.
QUOTE_CHARS = frozenset("'\"")

#: Characters that may be ADJACENTLY followed by a `[` which OPENS A SUBSCRIPT: an
#: identifier, a closing paren/bracket, or a string literal's CLOSING quote
#: (`parts[…]`, `f()[…]`, `x[0][…]`, `"abc"[…]`). A `[` preceded by ANYTHING else —
#: a blank, `=`, `/`, `:`, or start-of-line — opens a list/array display or
#: brackets a host, and that is exactly where a real address is legitimately
#: written (`lighthouse: [2001:db8::1]`, `https://[2001:db8::1]:443/`,
#: `ssh [<addr>]`). Keeping those two cases apart is what stops the carve-out
#: becoming a place to hide an address, and it is why no blank is skipped here.
#:
#: 🔴 The `QUOTE_CHARS` members are NECESSARY BUT NOT SUFFICIENT — a quote here only
#: qualifies when `quote_is_closing()` agrees. Removing them from this set instead is
#: the wrong fix and was measured to be: it reddens the `"abc"[1::2]` positive
#: control (this module's scan reads its own test's source).
SUBSCRIPTABLE_CHARS = frozenset("0123456789"
                                "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
                                "abcdefghijklmnopqrstuvwxyz"
                                "_)]") | QUOTE_CHARS

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


def _skip_ws_left(line: str, pos: int) -> int:
    """Index of the first non-blank at or left of `pos - 1`; -1 if there is none."""
    i = pos - 1
    while i >= 0 and line[i] in " \t":
        i -= 1
    return i


def _skip_ws_right(line: str, pos: int) -> int:
    """Index of the first non-blank at or right of `pos`; `len(line)` if none."""
    i = pos
    while i < len(line) and line[i] in " \t":
        i += 1
    return i


def enclosing_subscript_open(line: str, pos: int) -> int:
    """Index of the `[` opening the bracket group containing `pos`, else -1.

    A backward bracket match: the first opener not closed again before `pos`. When
    that opener is `(` or `{` the token sits in a CALL or a dict display, not a
    subscript, and -1 says so — which is what keeps `f(x, <addr>)` reportable.

    Needed because a tuple subscript puts the token in a LATER group:
    `arr[mask, 1::2]` reaches its `[` only by matching backwards past a comma and
    whatever precedes it. Depth counting is what stops an inner CLOSED bracket
    (`arr[f( [0] ), 1::2]`) being mistaken for the enclosing one.

    ⚠ Only reached when BOTH of the token's neighbours are separators, so the
    `(`-opener case that matters is a MIDDLE argument (`f(a, <addr>, b)`) — a
    trailing one is already rejected by the `)` beside it. Measured: a mutant
    accepting a `(` opener survived a green suite until a middle-argument fixture
    existed. The `{` arm shares this path; no fixture reaches it separately, because
    a `{` adjacent to a subscriptable character is not valid Python.
    """
    depth = 0
    for i in range(pos - 1, -1, -1):
        ch = line[i]
        if ch in ")]}":
            depth += 1
        elif ch in "([{":
            if depth == 0:
                return i if ch == "[" else -1
            depth -= 1
    return -1


def quote_is_closing(line: str, pos: int) -> bool:
    """True when the quote at `line[pos]` CLOSES a string literal, not opens one.

    🔴 THE FALSE-NEGATIVE THIS EXISTS FOR. `SUBSCRIPTABLE_CHARS` holds both quotes so
    that `"abc"[1::2]` is recognised as a subscript, and a line-at-a-time scan cannot
    tell an opening quote from a closing one by looking at the character alone. Taking
    it on trust exempted the canonical IPv6 endpoint spelling — `bind: "[<addr>]:53"`,
    `REDIS_URL="[<addr>]:6379"`, `net.Dial("tcp", "[<addr>]:80")` — in which the char
    before the `[` is a quote that OPENS the string the address sits inside. An audit
    found it; the base branch reported every one of those lines.

    PARITY is the discriminator: count unescaped occurrences of THAT SAME quote
    character up to and including `pos`. Even means it is the closer of a pair that
    began earlier (`"abc"[`); odd means it opened a literal and the `[` is inside it
    (`bind: "[`). Only that quote kind is counted, so the other kind nesting around it
    is irrelevant — which is what lets a Python source line spelling
    `'stride = "abcdef"[1::2]'` stay recognised while `"[<addr>]"` does not.

    ⚠ Scope, stated honestly: this is parity on ONE line, not a tokenizer. A string
    literal opened on a previous line, or a triple-quoted block, is outside what a
    line-at-a-time scan can see (module docstring, "split across lines"). Both
    unhandled cases resolve toward REPORTING, never toward exempting.
    """
    quote = line[pos]
    seen = 0
    i = 0
    while i <= pos:
        if line[i] == "\\":
            i += 2  # an escaped character, whatever it is, is not a delimiter
            continue
        if line[i] == quote:
            seen += 1
        i += 1
    return seen % 2 == 0


def is_subscript_slice(line: str, start: int, end: int) -> bool:
    """True when `line[start:end]` is a Python subscript slice, not an address.

    MEASURED false positive, and the reason this exists: `zip(parts[1::2],
    parts[2::2])` reported two routable public addresses. See the module
    docstring.

    🔴 THREE conditions, ALL required, because any one of them alone is a hole:

      1. **slice shape** — at most `_MAX_SLICE_COMPONENTS` colon-separated
         components, each empty or DECIMAL. A hextet containing any of `a`-`f`
         (`x[2001:db8::1]`) is an address in a subscript and stays reportable, and
         so is an all-decimal run too long to be a slice. Tested FIRST because it
         is what rejects every IPv4 match — a dotted quad is one non-decimal
         component — so the scan's IPv4 pass never reaches the bracket walk.
      2. **whole group** — the token is one complete comma-separated group of a
         bracketed list: `[` or `,` to its left and `]` or `,` to its right,
         BLANKS ALLOWED on both sides. That is what covers the tuple/numpy
         subscripts `grid[1::2, ::3]`, `arr[mask, 1::2]` and `grid[1::2,3]`, and
         `x[ 1::2 ]`. Anything else between — `x[<addr> 3]`, `d['<addr>']`, an
         unclosed `x[<addr>` — means the token is not a slice group.
      3. **subscript position** — the group's OWN opening `[` is immediately
         preceded by something SUBSCRIPTABLE (`SUBSCRIPTABLE_CHARS`), with NO
         whitespace skipped, AND — when that character is a quote — by a quote that
         `quote_is_closing()` says CLOSES a literal rather than opening one. That
         adjacency is load-bearing, not fastidiousness: MEASURED, `ssh [<addr>]` and
         `curl [<addr>]` are reported today, and skipping a blank there would exempt
         them — the bracketed-host spelling in prose or a shell line is one of the
         likeliest ways a real address gets written down. The quote-parity half is
         the same argument for the QUOTED spelling `bind: "[<addr>]:53"`, which was
         exempt until an audit found it. `lighthouse: [2001:db8::1]` and
         `https://[2001:db8::1]:443/` are this condition doing the same work.

    ⚠ KNOWN, DELIBERATE GAP: a blank between the name and the subscript is still
    reported. It is PEP8-illegal (E211) and rare in real code, and exempting it
    means exempting the `ssh [<addr>]` class above, which is worth more. That trade
    is the operator's to revisit; it is not an oversight. (Not spelled here on
    purpose — the token is reportable in that position, so writing the example out
    would make this module match its own scan. `uncovered_slice_lines()` in
    `test_no_public_ips.py` assembles it and pins that it stays reported.)

    ⚠ A subscript SPLIT ACROSS LINES is also still reported: this scan is
    line-at-a-time by construction, so the group's `[` is not on the line at all.
    Condition (2)'s right-hand half is what keeps that failing in the SAFE
    direction — see the D4 note in `test_no_public_ips.py`.
    """
    token = line[start:end]
    components = token.split(":")
    if len(components) > _MAX_SLICE_COMPONENTS:
        return False
    if not all(_DECIMAL_RE.fullmatch(c) for c in components if c):
        return False

    left = _skip_ws_left(line, start)
    if left < 0 or line[left] not in "[,":
        return False
    right = _skip_ws_right(line, end)
    if right >= len(line) or line[right] not in "],":
        return False

    opener = left if line[left] == "[" else enclosing_subscript_open(line, left)
    if opener <= 0 or line[opener - 1] not in SUBSCRIPTABLE_CHARS:
        return False
    if line[opener - 1] in QUOTE_CHARS:
        return quote_is_closing(line, opener - 1)
    return True


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
