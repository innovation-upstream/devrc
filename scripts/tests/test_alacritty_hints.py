"""🔴 `hints.enabled` IS AN ARRAY, AND DECLARING IT REPLACES ALACRITTY'S DEFAULT.

There is no merge. The moment `nix/programs/alacritty/default.nix` declares
`hints.enabled` at all, alacritty's built-in URL hint — the thing that makes
every link in this terminal clickable — is GONE unless it is re-declared by
hand. Nothing warns you. The config is valid, the switch succeeds, and the only
symptom is that clicking a URL stops doing anything.

That is the failure this file exists to make loud. It pins:

  * that a URL hint is still declared at all,
  * that its regex is still alacritty 0.17.0's own default, byte for byte
    (whole-string, not a keyword search — a guard on WORDS is walkable by
    rewording, so the WHOLE normalised string is pinned),
  * that its command, mouse binding and keyboard binding are unchanged, because
    a regex that matches but a `command` that does not open anything is the same
    outage wearing a different hat,
  * that the URL hint comes FIRST, so a mention-shaped substring inside a URL is
    claimed by the hint that already opens the right page.

PROVENANCE OF THE EXPECTED REGEX. It was extracted from the compiled
`alacritty-0.17.0` binary on this host, not from `man 5 alacritty` — the man
page is roff-escaped and line-wrapped, and copying it out of `man` output yields
a subtly different string. The one deliberate difference from the compiled
default is documented in the nix file and re-stated below.

NO `nix` SUBPROCESS. The authoritative test tier runs inside a nix build
sandbox, where recursive `nix` calls are unavailable — so this file decodes the
Nix string literal itself. The decoder handles exactly the escapes Nix's
double-quoted strings define.
"""
from __future__ import annotations

import contextlib
import importlib.util
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent


@contextlib.contextmanager
def _collector_on_path():
    """`scripts/collector` on `sys.path` for the duration, then OFF again.

    🔴 A TEST BODY MUST NOT LEAVE A GLOBAL BEHIND. This used to be a bare
    `sys.path.insert(0, …)` inside one test, never undone — so every test that
    ran after it in the same process (any file, any order) resolved imports
    against a directory it never asked for, and the leak was invisible until a
    name in `scripts/collector` happened to shadow one somewhere else.
    Whichever test ran first would decide, and `-p no:randomly` is not a fix.
    """
    inserted = str(ROOT / "scripts" / "collector")
    sys.path.insert(0, inserted)
    try:
        yield
    finally:
        with contextlib.suppress(ValueError):
            sys.path.remove(inserted)


def _mention_scan():
    """The scanner module, imported without leaving `sys.path` modified."""
    with _collector_on_path():
        import mention_scan  # noqa: PLC0415 — see `_collector_on_path`
    return mention_scan
ALACRITTY_NIX = ROOT / "nix" / "programs" / "alacritty" / "default.nix"

# alacritty 0.17.0's built-in URL-hint regex, as the regex engine receives it.
#
# ⚠ ONE DELIBERATE SUBSTITUTION, and it is the only difference from the compiled
# default: alacritty's Rust source writes `\u{0000}` etc. and so embeds LITERAL
# C0/C1 control characters in the excluded set. A Nix string cannot hold a NUL,
# so the identical set is spelled with the regex engine's own `\xNN` escapes.
# Verified against the binary: replacing U+0000 / U+001F / U+007F / U+009F in the
# compiled string with `\x00` / `\x1F` / `\x7F` / `\x9F` yields exactly this.
EXPECTED_URL_REGEX = (
    "(ipfs:|ipns:|magnet:|mailto:|gemini://|gopher://|https://|http://|news:|"
    "file:|git://|ssh:|ftp://)"
    '[^\\x00-\\x1F\\x7F-\\x9F<>"\\s{-}\\^⟨⟩`\\\\]+'
)

EXPECTED_URL_COMMAND = "xdg-open"
URL_HINT_BINDING = ("O", "Control|Shift")


# --------------------------------------------------------------------------- #
# A very small Nix reader — enough for one `hints.enabled` array of flat attrsets
# --------------------------------------------------------------------------- #
def _unescape_nix(s: str) -> str:
    """Decode a Nix double-quoted string body.

    Nix defines `\\n`, `\\r`, `\\t`, `\\\\`, `\\"` and `\\${`; any other
    backslash-escape is the character itself. That is the whole grammar, and it
    is why this is 12 lines rather than a dependency."""
    out: list[str] = []
    i = 0
    simple = {"n": "\n", "r": "\r", "t": "\t"}
    while i < len(s):
        c = s[i]
        if c == "\\" and i + 1 < len(s):
            nxt = s[i + 1]
            out.append(simple.get(nxt, nxt))
            i += 2
            continue
        out.append(c)
        i += 1
    return "".join(out)


def _structural(text: str, start: int):
    """Yield (index, char) for every character from `start` that is STRUCTURAL —
    i.e. not inside a `#` comment and not inside a `"…"` string literal.

    🔴 Not optional bookkeeping. A naive brace scan over this file finds `{0000}`
    inside a prose comment, `{-}` and `{1,6}` inside the regexes themselves, and
    reports six "hints" that do not exist — which then makes every downstream
    assertion pass or fail for reasons unrelated to the config. This is the
    difference between parsing the file and grepping it."""
    i = start
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == "#":                       # comment to end of line
            nl = text.find("\n", i)
            i = n if nl < 0 else nl + 1
            continue
        if ch == '"':                       # string literal (backslash escapes)
            i += 1
            while i < n:
                if text[i] == "\\":
                    i += 2
                    continue
                if text[i] == '"':
                    i += 1
                    break
                i += 1
            continue
        yield i, ch
        i += 1


def _hint_blocks(text: str) -> list[str]:
    """The raw `{ … }` source of each entry in `hints.enabled = [ … ];`."""
    start = text.find("hints.enabled")
    if start < 0:
        return []
    blocks: list[str] = []
    depth_sq = 0
    depth_curly = 0
    block_start = -1
    for i, ch in _structural(text, start):
        if ch == "[":
            depth_sq += 1
        elif ch == "]":
            depth_sq -= 1
            if depth_sq == 0:
                break
        elif ch == "{":
            if depth_sq == 1 and depth_curly == 0:
                block_start = i
            depth_curly += 1
        elif ch == "}":
            depth_curly -= 1
            if depth_sq == 1 and depth_curly == 0 and block_start >= 0:
                blocks.append(text[block_start:i + 1])
                block_start = -1
    return blocks


def _code_only(text: str) -> str:
    """`text` with `#` comments removed and string literals kept intact.

    🔴 The attribute readers below MUST run on this, never on the raw block. The
    nix file documents each field in prose right next to it — a comment reading
    "post_processing = false — that pass exists to repair URL over-capture" is
    matched by a naive `post_processing\\s*=\\s*(true|false)` search, so the test
    would be reading the COMMENT and reporting it as the config. That is a guard
    that passes while the setting says the opposite."""
    out: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == "#":
            nl = text.find("\n", i)
            i = n if nl < 0 else nl
            continue
        if ch == '"':
            j = i + 1
            while j < n:
                if text[j] == "\\":
                    j += 2
                    continue
                if text[j] == '"':
                    j += 1
                    break
                j += 1
            out.append(text[i:j])
            i = j
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _string_attr(block: str, key: str) -> str | None:
    m = re.search(rf'\b{key}\s*=\s*("(?:[^"\\]|\\.)*")\s*;', _code_only(block), re.S)
    return _unescape_nix(m.group(1)[1:-1]) if m else None


def _bool_attr(block: str, key: str) -> bool | None:
    m = re.search(rf"\b{key}\s*=\s*(true|false)\s*;", _code_only(block))
    return (m.group(1) == "true") if m else None


def _inline_table(block: str, key: str) -> str | None:
    m = re.search(rf"\b{key}\s*=\s*\{{(.*?)\}}\s*;", _code_only(block), re.S)
    return m.group(1) if m else None


def _binding(block: str) -> tuple[str | None, str | None]:
    inner = _inline_table(block, "binding")
    if inner is None:
        return (None, None)
    return (_string_attr(inner, "key"), _string_attr(inner, "mods"))


def _mouse_enabled(block: str) -> bool | None:
    inner = _inline_table(block, "mouse")
    return _bool_attr(inner, "enabled") if inner is not None else None


@pytest.fixture(scope="module")
def blocks() -> list[str]:
    return _hint_blocks(ALACRITTY_NIX.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def url_hint(blocks) -> str | None:
    for b in blocks:
        if (_string_attr(b, "regex") or "").startswith("(ipfs:"):
            return b
    return None


@pytest.fixture(scope="module")
def mention_hint(blocks) -> str | None:
    for b in blocks:
        if not (_string_attr(b, "regex") or "").startswith("(ipfs:"):
            return b
    return None


# --------------------------------------------------------------------------- #
# Positive control
# --------------------------------------------------------------------------- #
def test_positive_control_the_parser_finds_the_hint_array(blocks):
    """🔴 A reassuring pass from a parser that found NOTHING is indistinguishable
    from a real pass, and would make every assertion below vacuous. This is the
    reading that must be non-zero before any other result here means anything."""
    assert len(blocks) >= 2, (
        f"the hints.enabled reader found {len(blocks)} entries in "
        f"{ALACRITTY_NIX} — it is broken, or the array was restructured. Every "
        "other assertion in this file is vacuous until this passes."
    )


def test_positive_control_the_nix_string_decoder_works():
    assert _unescape_nix(r"a\\b") == "a\\b"
    assert _unescape_nix(r"say \"hi\"") == 'say "hi"'
    assert _unescape_nix(r"\\x00") == "\\x00"


def test_positive_control_the_readers_see_config_and_not_prose():
    """🔴 The nix file documents each hint field in prose RIGHT NEXT TO IT. A
    comment reading "post_processing = false — that pass exists to repair URL
    over-capture" is matched by a naive attribute search, so a reader without the
    comment-stripping step would be reporting the COMMENT as the config — and
    would stay green on the day the setting and its comment disagree, which is
    the whole point of checking.

    The mirror hazard is stripping too much: every regex in that file contains a
    literal `#`, inside a string, which must survive."""
    src = ('  # post_processing = true; in a comment\n'
           '  post_processing = false;\n'
           '  regex = "a # not a comment";\n')
    assert _bool_attr(src, "post_processing") is False
    assert _string_attr(src, "regex") == "a # not a comment"


# --------------------------------------------------------------------------- #
# 🔴 THE GUARD
# --------------------------------------------------------------------------- #
def test_the_builtin_url_hint_is_still_declared(url_hint):
    assert url_hint is not None, (
        "nix/programs/alacritty/default.nix declares `hints.enabled` but NO URL "
        "hint.\n"
        "🔴 Declaring hints.enabled REPLACES alacritty's built-in default "
        "outright — there is no merge — so URL clicking is now DEAD in this "
        "terminal, silently: valid config, successful switch, links that do "
        "nothing when clicked.\n"
        "Fix: re-declare the built-in URL hint verbatim alongside whatever you "
        "were adding. Its expected regex is EXPECTED_URL_REGEX in this file."
    )


def test_the_url_hint_regex_is_alacrittys_own_default(url_hint):
    """Pinned as the WHOLE normalised string, not by keyword. A guard that looks
    for `https` in the regex passes for a regex that matches nothing else, and a
    URL hint that has quietly lost `mailto:` or the excluded-character class is
    still a broken URL hint."""
    assert _string_attr(url_hint, "regex") == EXPECTED_URL_REGEX, (
        "the URL hint's regex is no longer alacritty 0.17.0's built-in default.\n"
        "It was extracted from the compiled binary (NOT from `man 5 alacritty`, "
        "whose rendering is roff-escaped and line-wrapped). The only sanctioned "
        "difference is spelling the literal C0/C1 control characters as \\xNN "
        "escapes, because a Nix string cannot hold a NUL."
    )


def test_the_url_hint_still_opens_things(url_hint):
    """A correct regex with a broken action is the same outage."""
    assert _string_attr(url_hint, "command") == EXPECTED_URL_COMMAND
    assert _bool_attr(url_hint, "hyperlinks") is True
    assert _bool_attr(url_hint, "post_processing") is True
    assert _bool_attr(url_hint, "persist") is False


def test_the_url_hint_is_still_click_to_open(url_hint):
    """`mouse.enabled = true` with no mods is what makes a plain hover underline
    and a plain click open — the behaviour every other hint here is measured
    against."""
    assert _mouse_enabled(url_hint) is True
    assert "mods" not in (_inline_table(url_hint, "mouse") or "mods")


def test_the_url_hints_keyboard_binding_is_unchanged(url_hint):
    assert _binding(url_hint) == URL_HINT_BINDING


def test_the_url_hint_comes_first(blocks, url_hint):
    """`github.com/owner/repo#1` matches BOTH hints, and the URL hint is the one
    that already opens exactly the right page — so it is declared first.

    ⚠ SCOPE, stated rather than implied: this pins the ORDER WE INTEND. That
    alacritty resolves an overlap in declaration order is a reasonable reading
    of its hint lookup but was NOT verified here, and verifying it needs a live
    terminal. The ordering is therefore defensive, not load-bearing: if
    alacritty picked the other hint, `mention-open.py` still resolves
    `owner/repo#N` to the same GitHub issue URL. Do not upgrade this docstring
    to a claim about alacritty without measuring one."""
    assert blocks[0] is url_hint, (
        "the URL hint is no longer the first entry in hints.enabled — the "
        "intended precedence for a mention-shaped substring inside a URL has "
        "been inverted."
    )


# --------------------------------------------------------------------------- #
# The mention hint
# --------------------------------------------------------------------------- #
def test_the_mention_hint_exists_and_points_at_the_handler(mention_hint):
    """The command is a Nix interpolation of a wrapper derivation, so this
    follows the reference one hop: the hint names `mentionOpen`, `mentionOpen`
    execs `scripts/mention-open.py`, and that file is ON DISK.

    The last hop is the point. A hint whose command does not exist fails at
    CLICK time with nothing on screen — the same silent shape as a dropped hint,
    and exactly what a new-but-untracked script would produce after a deploy."""
    assert mention_hint is not None
    command = _string_attr(mention_hint, "command")
    assert command == "${mentionOpen}", (
        f"the mention hint's command is {command!r}; expected the "
        "`mentionOpen` wrapper derivation defined in the same file"
    )
    text = ALACRITTY_NIX.read_text(encoding="utf-8")
    assert re.search(r"\bmentionOpen\s*=\s*pkgs\.writeShellScript\b", text), (
        "the mention hint interpolates `mentionOpen` but the file no longer "
        "defines it"
    )
    m = re.search(r"scripts/(mention-open\.py)", text)
    assert m, "the mentionOpen wrapper no longer names scripts/mention-open.py"
    handler = ROOT / "scripts" / m.group(1)
    assert handler.is_file(), f"{handler} does not exist — the hint is dead"


def test_the_mention_hint_matches_every_supported_shape(mention_hint):
    """Compiled with Python's engine as a syntax-compatible stand-in. It is NOT
    a claim about the Rust engine — it is a claim that the pattern still
    DESCRIBES the four shapes, which is the part an edit breaks."""
    pattern = _string_attr(mention_hint, "regex")
    rx = re.compile(pattern)
    for text in ("#370", "devrc#591", "civitai/talos-infra#1065", "868abc123"):
        assert rx.search(text), f"the mention hint no longer matches {text!r}"


def test_every_TERMINAL_shape_the_scanner_detects_is_also_UNDERLINED(mention_hint):
    """🔴 THE SEAM THAT SHIPPED A DEAD FEATURE, PINNED IN THE DIRECTION THAT
    FAILED. `mention_scan.PATTERN_LEDGER` decides what the handler will RESOLVE;
    this regex decides what the terminal UNDERLINES. Unhighlighted text cannot be
    clicked, so a shape enabled in the ledger's TERMINAL profile and absent from
    this regex is a feature that is 100% dead — and both files stay hermetically
    green, because neither suite reads the other.

    MEASURED: that is exactly what `audit-pr 1291` was. The ledger flip alone
    left the scanner returning a span for text the terminal never underlined.

    Driven off the LEDGER, not a hand-written list, so a pattern moved into the
    terminal profile later is covered without anyone remembering this file.

    ⚠ IT IS A CLAIM ABOUT THE PATTERN, NOT ABOUT THE RUST ENGINE. Python's
    engine is a syntax-compatible stand-in — what an edit breaks is whether the
    pattern still DESCRIBES the shape.
    """
    MS = _mention_scan()

    rx = re.compile(_string_attr(mention_hint, "regex"))
    terminal = {name: pat for name, pat in MS.PATTERN_LEDGER.items()
                if pat.role == "detect" and MS.PROFILE_TERMINAL in pat.profiles}
    # 🔴 THE NAMED CLAIM COMES FIRST, AND THE ORDER IS NOT COSMETIC. A mutation
    # run scored this row KILLED-WRONG-REASON when the count was asserted first:
    # reverting the ledger drops `terminal` to three entries, so `len >= 4` fired
    # with a message that named no hazard, and the assertion that DOES name it
    # never ran. A guard's own error text is the thing being verified.
    assert "AUDIT_PR_RE" in terminal, (
        "AUDIT_PR_RE is no longer clickable — if that reversal was intended, "
        "drop the `audit-pr` alternation from the hint regex in the SAME "
        "change, or the terminal underlines a shape nothing resolves")
    # POSITIVE CONTROL — there ARE terminal-profile detect patterns, so the loop
    # below is not vacuous on a ledger that lost every terminal row.
    assert len(terminal) >= 4, sorted(terminal)

    spec = importlib.util.spec_from_file_location(
        "mention_open_seam", ROOT / "scripts" / "mention-open.py")
    mo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mo)

    for name, pat in sorted(terminal.items()):
        m = rx.search(pat.sample)
        assert m, (
            f"{name}: the scanner detects {pat.sample!r} on the CLICK profile "
            f"but the alacritty hint regex does not underline it — the shape is "
            f"unclickable, and both suites stay green while it is")
        # 🔴 AND THE SPAN THE TERMINAL HANDS OVER MUST BE RESOLVABLE ON ITS OWN.
        # Alacritty passes the MATCHED SUBSTRING verbatim as the last argument,
        # not the line it came from — so a regex that matches a fragment the
        # handler cannot re-scan is underlined and inert.
        span, _ = mo.resolve(m.group(0))
        assert span is not None, (
            f"{name}: the hint underlines {m.group(0)!r} but the handler "
            f"re-scans that span to NOTHING — a click on it does nothing")


def test_the_hint_underlines_BOTH_spellings_of_an_audit_pr_reference(mention_hint):
    """`/audit-pr 1291` is how it appears in prose; `audit-pr 1291` is how it is
    said. Both must underline, and the match must stop at the number rather than
    running on into the next word."""
    rx = re.compile(_string_attr(mention_hint, "regex"))
    assert rx.search("see audit-pr 1291 for the diff").group(0) == "audit-pr 1291"
    assert rx.search("see /audit-pr 1291 for the diff").group(0) == "/audit-pr 1291"
    # NEGATIVE CONTROLS — the alternation must not fire without a number, and
    # must not swallow a neighbouring word.
    assert not rx.search("audit-pr the thing")
    assert rx.search("audit-pr 12 and more").group(0) == "audit-pr 12"


def test_the_hint_swallows_an_OVER_LONG_audit_pr_number_whole(mention_hint):
    """🔴 THE SAME `{1,6}` CONTRACT THE `#` ALTERNATION CARRIES, and the reason
    it is not `{1,5}`. The scanner accepts `\\d{1,5}` guarded by a trailing-digit
    lookahead this engine cannot express. Under `{1,5}` the terminal would
    underline `audit-pr 12345` inside `audit-pr 123456` and the handler would
    open PR 12345 — a confident wrong page. `{1,6}` captures the whole run, and
    the strict scanner then refuses it."""
    import importlib.util

    rx = re.compile(_string_attr(mention_hint, "regex"))
    m = rx.search("audit-pr 123456")
    # 🔴 THIS MESSAGE NAMES THE HAZARD BECAUSE THIS IS THE ASSERTION THAT FIRES.
    # A mutation run scored the `{1,5}` mutant KILLED-WRONG-REASON while this
    # line read `assert ..., m`: the right test failed, on the right subject,
    # with an error message that said only `<re.Match object …>`.
    assert m and m.group(0) == "audit-pr 123456", (
        f"the hint underlines only {(m.group(0) if m else None)!r} of "
        f"'audit-pr 123456' — a TRUNCATED audit-pr number reaches the handler, "
        f"which resolves it and opens a confident wrong page")

    spec = importlib.util.spec_from_file_location(
        "mention_open_overlong", ROOT / "scripts" / "mention-open.py")
    mo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mo)
    span, candidates = mo.resolve(m.group(0))
    assert (span, candidates) == (None, []), (
        f"the handler RESOLVED an over-long audit-pr number: {span}")


def test_the_mention_hint_swallows_a_six_digit_colour_whole(mention_hint):
    """🔴 NOT a false positive — a deliberate one. Rust's regex crate has no
    lookaround, so the scanner's trailing-digit guard cannot be expressed in the
    hint. A `{1,5}` bound would match the first FIVE digits of `#282828` and
    offer to open task 28282. `{1,6}` swallows the whole literal instead, and
    the handler's strict scanner rejects it — nothing opens.

    If this ever matches only part of the literal, the loose-regex/strict-handler
    contract has been broken."""
    rx = re.compile(_string_attr(mention_hint, "regex"))
    m = rx.search('background = "#282828";')
    assert m and m.group(0) == "#282828"


def test_the_handlers_OFFER_bound_matches_the_hints_own(mention_hint):
    """🔴 A SEAM BETWEEN TWO FILES, OWNED BY NEITHER.

    The hint decides what the terminal UNDERLINES; `mention-open.py`'s
    `_OFFER_NUM_RE` decides what a dismissible picker may OFFER for text the
    strict scanner refused. Those are two spellings of one fact — "how many
    digits can arrive from a click" — and they live in different files, in
    different languages, tested by different suites. Each is hermetically green
    while they disagree, and the disagreement is silent in both directions: a
    NARROWER handler bound turns a real click back into the dead-end toast this
    whole path exists to remove, and a WIDER one offers a number no click can
    produce.

    ⚠ THIS IS NOT THE `_NUM = \\d{1,5}` GUARD and must not be confused with it.
    That bound decides what may be OPENED and is deliberately narrower than both
    of these; it is pinned in `test_mention_scan.py` and is not this test's
    subject.
    """
    import importlib.util

    handler = ROOT / "scripts" / "mention-open.py"
    spec = importlib.util.spec_from_file_location("mention_open_bound", handler)
    mo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mo)

    hint_bound = re.search(r"#\[0-9\]\{1,(\d+)\}",
                           _string_attr(mention_hint, "regex"))
    assert hint_bound, "the mention hint no longer spells its digit bound as {1,N}"
    offer_bound = re.search(r"\[0-9\]\{1,(\d+)\}", mo._OFFER_NUM_RE.pattern)
    assert offer_bound, "_OFFER_NUM_RE no longer spells its digit bound as {1,N}"
    assert offer_bound.group(1) == hint_bound.group(1), (
        f"the handler offers up to {offer_bound.group(1)} digits while the "
        f"terminal underlines up to {hint_bound.group(1)}")

    # POSITIVE CONTROL, on the real objects rather than on the two integers: a
    # number at the shared bound must be underlined by the hint AND recovered by
    # the handler. Two constants can agree while both are wired to nothing.
    at_bound = "#" + "8" * int(hint_bound.group(1))
    assert re.compile(_string_attr(mention_hint, "regex")).search(at_bound)
    assert mo.offer_number(at_bound) == "8" * int(hint_bound.group(1))


def test_the_mention_hint_behaves_like_a_link(mention_hint):
    """Zach's requirement, verbatim: "for click i want it to function the same
    way link clicking already does". Same mouse semantics as the URL hint."""
    assert _mouse_enabled(mention_hint) is True


def test_the_mention_hint_does_not_post_process(mention_hint):
    """post_processing exists to repair URL over-capture. The mention pattern
    ends exactly at the reference, so the pass could only remove characters that
    were matched deliberately."""
    assert _bool_attr(mention_hint, "post_processing") is False
    assert _bool_attr(mention_hint, "hyperlinks") is False


def test_no_two_hints_share_a_keyboard_binding(blocks):
    bindings = [_binding(b) for b in blocks]
    assert len(set(bindings)) == len(bindings), (
        f"two hints share a keyboard binding: {bindings}. The second one is "
        "unreachable from the keyboard."
    )


def test_the_mention_binding_does_not_collide_with_the_terminals_own_bindings(mention_hint):
    """`keyboard.bindings` in the same file sends escape sequences to the shell;
    a hint binding that matched one of them would shadow it."""
    key, mods = _binding(mention_hint)
    text = ALACRITTY_NIX.read_text(encoding="utf-8")
    kb = re.search(r"keyboard\.bindings\s*=\s*\[(.*?)\];", text, re.S)
    assert kb, "keyboard.bindings disappeared from the alacritty module"
    for k, m in re.findall(r'key\s*=\s*"([^"]+)";\s*mods\s*=\s*"([^"]+)";', kb.group(1)):
        assert (k, m) != (key, mods), (
            f"the mention hint binding {key}+{mods} collides with a "
            "keyboard.bindings entry in the same file"
        )
