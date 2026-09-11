"""Unit tests for scripts/i3status-scratchpads — the scratchpad colour legend.

Drives the pure `render` formatter and the two fetchers (`load_slots`,
`fetch_sessions`) against FIXTURE data — no real tmux, no real slot table
except where a test is deliberately pinning the real one.

🔴 THE TESTS THAT MATTER MOST ARE THE DISCRIMINANT ONES. This block has a real
reading that looks like nothing — "no scratchpad sessions running" renders the
full legend with every slot dim — so a reading that COULD NOT BE TAKEN must be
visibly different (`scratch ?`) or the pill lies quietly. Four ways to fail to
measure are pinned below: an unloadable slot table, a missing `tmux`
executable, and a non-zero `tmux` exit that is NOT `no server running`; the
fourth, `no server running` itself, is the one that must NOT render `?`.
"""
import html
import importlib.machinery
import importlib.util
import json
import os
import re
import shutil
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS = os.path.join(_HERE, "..")
_REPO = Path(_HERE).resolve().parents[1]
_TABLE = os.path.join(_SCRIPTS, "tmux-scratch-slots.sh")
_GRAPHICAL = _REPO / "nix" / "graphical.nix"
_TMUX_NIX = _REPO / "nix" / "programs" / "tmux" / "default.nix"
_PICKER = os.path.join(_SCRIPTS, "tmux-scratch-picker.sh")


def _load(relpath, modname):
    loader = importlib.machinery.SourceFileLoader(
        modname, os.path.join(_SCRIPTS, relpath))
    spec = importlib.util.spec_from_loader(modname, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


blk = _load("i3status-scratchpads", "i3status_scratchpads")

# Fixture slots. 🔴 PAIRWISE DISTINCT ON EVERY FIELD, and distinct from every
# constant the assertions name: no two window counts are equal, none of them is
# 0 or 1 (values a mutant could hardcode and still look right), and no fixture
# colour is `blk.DIM`. A fixture that can only ever produce a constant's own
# value cannot see a mutant that hardcodes that constant.
_SLOTS = [
    ("scratchA", "a", "#111111", "Alpha"),
    ("scratchB", "b", "#222222", "Bravo"),
    ("scratchC", "c", "#333333", "Charlie"),
]
_SESSIONS = {"scratchA": 7, "scratchC": 4}      # B absent; 7 != 4, neither is 1


# --------------------------------------------------------------------------- #
# The real slot table
# --------------------------------------------------------------------------- #
def _bash_slot_entries():
    """The real table as BASH sees it — an INDEPENDENT parser.

    🔴 This exists so the expected slot COUNT is not derived from the regex
    under test. `len(slots) == 20` compared the parser's output to a literal
    that was itself read off that parser's output, so a 21st slot the regex
    silently dropped left the assertion green. bash `source` is the third
    consumer of this file and shares no code with the regex, which is exactly
    what makes it usable as the yardstick."""
    out = subprocess.run(
        ["bash", "-c", '. "$1"; printf "%s\\n" "${SCRATCH_SLOTS[@]}"',
         "_", _TABLE],
        capture_output=True, text=True, check=True)
    return [ln for ln in out.stdout.splitlines() if ln.strip()]


def test_the_REAL_slot_table_parses_to_twenty_slots_with_the_expected_entry():
    """Entries pinned as LITERALS; the COUNT derived from bash, not from us.

    A parser that dropped a field would otherwise agree with itself."""
    slots = blk.load_slots([_TABLE])
    assert slots is not None
    expected = len(_bash_slot_entries())
    # positive control: the independent parser actually read something
    assert expected >= 20, expected
    assert len(slots) == expected, (
        "the regex and bash disagree about how many slots this file has: "
        "regex=%d bash=%d — %r" % (len(slots), expected, [s[0] for s in slots]))
    assert ("scratch4", "V", "#83a598", "Vapor") in slots
    assert ("scratch", "g", "#b8bb26", "grove") in slots
    assert ("scratch20", "Y", "#79740e", "Yarrow") in slots
    # CASE IS SIGNIFICANT in this table: M-v and M-V are different sessions.
    assert ("scratch3", "v", "#b16286", "violet") in slots
    # ORDER is the table's order — the legend renders in it.
    assert slots[0][0] == "scratch" and slots[-1][0] == "scratch20"


def test_the_sibling_deployed_leg_is_tried_FIRST():
    """🔴 On a live host this script is a lone nix-store symlink in
    ~/.config/i3status-rust/scripts and the slot table is symlinked BESIDE it as
    `scratch-slots.sh`. That leg must win, or a stale $DEVRC_DIR checkout
    silently overrides the table home-manager just deployed."""
    paths = blk._SLOT_PATHS
    assert len(paths) == 3, paths
    here = os.path.dirname(
        os.path.abspath(os.path.join(_SCRIPTS, "i3status-scratchpads")))
    assert paths[0] == os.path.join(here, "scratch-slots.sh")
    # leg 2 resolves inside ANY checkout without $DEVRC_DIR being right
    assert paths[1] == os.path.join(here, "tmux-scratch-slots.sh")
    assert os.path.exists(paths[1]), paths[1]


def test_load_slots_falls_through_a_missing_path_to_a_real_one(tmp_path):
    missing = str(tmp_path / "nope" / "scratch-slots.sh")
    slots = blk.load_slots(
        [missing, os.path.join(_SCRIPTS, "tmux-scratch-slots.sh")])
    assert slots is not None and len(slots) == 20


def test_load_slots_returns_NONE_not_empty_when_nothing_parses(tmp_path):
    """None, NOT []. [] renders as an empty legend — a reading nobody can take,
    because the table always has slots in it."""
    junk = tmp_path / "scratch-slots.sh"
    junk.write_text("# no slot entries here at all\n", encoding="utf-8")
    assert blk.load_slots([str(junk)]) is None
    assert blk.load_slots([str(tmp_path / "absent.sh")]) is None


# --------------------------------------------------------------------------- #
# ONE GRAMMAR, THREE READERS  (the slot-entry regex)
# --------------------------------------------------------------------------- #
#: Lines that separate the grammars that USED to be in this repo. Each carries
#: the reason it is here; every one is classified by nix, by Python and (where
#: applicable) by bash in the tests below.
_GRAMMAR_CORPUS = [
    # (line, is_a_slot, why)
    ('    "scratch4:V:#83a598:Vapor"', True, "the real shape"),
    ('#    "scratch21:z:#123456:zed"', False,
     "COMMENTED OUT: nix skipped it (whole-line match), the legend's unanchored "
     "regex matched it and advertised a hotkey bound to nothing"),
    ('    "scratch21:z:#123456789:zed"', True,
     "9 hex digits IS valid pango; nix's `+` took it, the legend's {3,8} dropped it"),
    ('    "scratch21:z:#123456789abc:zed"', True, "12 hex digits, valid pango"),
    ('    "scratch21:z:#abc:zed"', True, "3 hex digits, valid pango"),
    ('    "scratch21:z:#12345:zed"', False, "5 hex digits: no pango parser accepts it"),
    ('    "scratch21:z:#1234567:zed"', False, "7 hex digits: same"),
    ('    "scratch21:z:#12345678:zed"', False,
     "8 hex digits: the legend's old {3,8} upper bound, not a pango colour"),
    ('"scratch21:z:#123456:zed"', True, "no indent"),
    ('  "scratch21:z:#123456:zed"  ', True, "trailing spaces"),
    ('    "scratch21:z:#123456:zed" # trailing comment', False,
     "not the whole line — bash would not put it in the array either"),
    ('SCRATCH_SLOTS=(', False, "the array opener"),
]


def _marker_lines():
    text = open(_TABLE, encoding="utf-8").read()
    return re.findall(r"^# SLOT_ENTRY_RE: (\S.*?)[ ]*$", text, re.M)


def test_the_slot_entry_grammar_has_EXACTLY_ONE_copy_and_both_readers_read_it():
    """🔴 The fix for three grammars over one source of truth. The pattern lives
    on the table's own `# SLOT_ENTRY_RE:` line; the bar block and
    nix/programs/tmux/default.nix both READ it rather than spelling their own.

    Two of the three measured disagreements were invisible precisely because
    each side's regex was individually plausible — so this asserts the
    SINGLE-COPY property, not any particular pattern text."""
    assert len(_marker_lines()) == 1, _marker_lines()

    blk_src = open(os.path.join(_SCRIPTS, "i3status-scratchpads"),
                   encoding="utf-8").read()
    nix_src = _TMUX_NIX.read_text()

    # Neither reader may DEFINE an entry grammar any more. Asserted structurally
    # rather than by scanning for `0-9a-fA-F`: both files discuss the old
    # regexes in prose, and a guard that a comment can trip is a guard a comment
    # can also satisfy.
    assert not hasattr(blk, "_SLOT_RE"), (
        "scripts/i3status-scratchpads defines a module-level entry regex again; "
        "the grammar's one home is the `# SLOT_ENTRY_RE:` line in "
        "scripts/tmux-scratch-slots.sh, read by `slot_pattern`")
    for name in ("_MARKER_RE", "_CANDIDATE_RE"):
        pat = getattr(blk, name).pattern
        assert "0-9a-fA-F" not in pat, (
            "%s has grown a colour grammar: %r" % (name, pat))
    assert re.search(r"^\s*slotRe\s*=\s*$", nix_src, re.M) or not re.search(
        r'^\s*slotRe\s*=\s*"', nix_src, re.M), (
        "nix/programs/tmux/default.nix assigns `slotRe` a string LITERAL again — "
        "it must read the marker out of the slot table")
    # …and both must actually consult the marker.
    assert "SLOT_ENTRY_RE" in blk_src, blk_src[:200]
    assert 'builtins.match "# SLOT_ENTRY_RE: (.*)"' in nix_src, (
        "nix/programs/tmux/default.nix no longer reads the marker line")


def test_the_colour_grammar_is_the_ONE_PANGO_ACCEPTS():
    """3 / 6 / 9 / 12 hex digits in, everything else out.

    MEASURED (i3status-rs 0.36.1): the bar passes `foreground="#fffffff"`
    through verbatim and neither validates nor escapes it, so a 5- or 7-digit
    colour reaches pango as-is. This is the always-runnable half of the grammar
    check — it needs no nix."""
    text = open(_TABLE, encoding="utf-8").read()
    rx = blk.slot_pattern(text)
    assert rx is not None
    for line, want, why in _GRAMMAR_CORPUS:
        got = rx.match(line) is not None
        assert got == want, "%r -> %s, expected %s (%s)" % (line, got, want, why)


@pytest.mark.skipif(shutil.which("nix-instantiate") is None,
                    reason="nix-instantiate absent — `pkgs.nix` is in the "
                           "flake's gateTools, so this skipping means the "
                           "environment is not a gate environment")
def test_the_NIX_and_PYTHON_slot_grammars_CLASSIFY_THE_CORPUS_IDENTICALLY():
    """🔴 THE CROSS-ARTIFACT COMPARISON. Both sides now read the same pattern
    string, but they run it through DIFFERENT regex engines — nix's POSIX ERE
    (`builtins.match`, whole-string) and Python's `re` (anchored with `^…$`
    under MULTILINE). "Same bytes" is not "same verdict" until something
    evaluates both, which is what this does.

    Pure `builtins` on the nix side: no `<nixpkgs>`, no channel, no network."""
    marker = _marker_lines()
    assert len(marker) == 1
    pat = marker[0]
    lines = [c[0] for c in _GRAMMAR_CORPUS]
    nixlist = "[ " + " ".join(json.dumps(ln) for ln in lines) + " ]"
    expr = ("let p = %s; c = %s; in map (l: builtins.match p l != null) c"
            % (json.dumps(pat), nixlist))
    proc = subprocess.run(
        ["nix-instantiate", "--eval", "--strict", "--json", "--expr", expr],
        capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr[:800]
    nix_says = json.loads(proc.stdout)
    assert len(nix_says) == len(lines)

    rx = blk.slot_pattern(open(_TABLE, encoding="utf-8").read())
    py_says = [rx.match(ln) is not None for ln in lines]

    # Positive control: the corpus is not all-False (a nix expression wired to
    # nothing would return all False and agree with a Python regex that also
    # matched nothing).
    assert any(nix_says) and any(py_says), (nix_says, py_says)
    assert not all(nix_says), nix_says

    disagreements = [(ln, p, n) for ln, p, n in zip(lines, py_says, nix_says)
                     if p != n]
    assert not disagreements, (
        "the tmux key bindings and the bar legend disagree about what a slot "
        "IS: %r" % disagreements)
    assert py_says == [c[1] for c in _GRAMMAR_CORPUS]


def test_BASH_and_the_regex_agree_on_the_REAL_table():
    """The third reader. bash `source`s this file; the other two read it with
    the marker's regex. A slot bash puts in `SCRATCH_SLOTS` that the regex drops
    is a scratchpad with a HUD entry, no key binding and no legend entry."""
    from_bash = _bash_slot_entries()
    slots = blk.load_slots([_TABLE])
    assert slots is not None
    rebuilt = ["%s:%s:%s:%s" % s for s in slots]
    assert rebuilt == from_bash, (
        "bash and the slot grammar disagree about the real table:\n"
        "  bash-only:  %r\n  regex-only: %r"
        % (sorted(set(from_bash) - set(rebuilt)),
           sorted(set(rebuilt) - set(from_bash))))


def test_load_slots_needs_EXACTLY_ONE_grammar_marker(tmp_path):
    """Zero markers means this is not the canonical table; two means a stray
    copy could silently win. Either way the honest answer is `?`, never a guess
    at the grammar — a guessed grammar is how the two readers drifted apart in
    the first place."""
    real = open(_TABLE, encoding="utf-8").read()
    marker_line = [ln for ln in real.splitlines()
                   if ln.startswith("# SLOT_ENTRY_RE: ")][0]

    none = tmp_path / "none.sh"
    none.write_text(real.replace(marker_line + "\n", ""), encoding="utf-8")
    assert blk.load_slots([str(none)]) is None

    two = tmp_path / "two.sh"
    two.write_text(real.replace(marker_line, marker_line + "\n" + marker_line),
                   encoding="utf-8")
    assert blk.load_slots([str(two)]) is None

    # control: the untouched file still parses, so the two above failed for the
    # marker and not because the fixture was mangled
    good = tmp_path / "good.sh"
    good.write_text(real, encoding="utf-8")
    assert blk.load_slots([str(good)]) is not None


def test_a_PARTIALLY_parseable_table_is_UNMEASURED_not_a_short_legend(tmp_path):
    """🔴 F2. `if slots:` accepted ANY non-empty parse, so a table whose colour
    fields had mostly rotted rendered a short, plausible, wrong legend.

    MEASURED on the shipped code: corrupt 17 of the 20 colour fields and it
    rendered `g3 G v` — three slots, no `?`, indistinguishable from a design
    choice. The floor compares what the grammar ACCEPTED against what the file
    DECLARES (quoted strings alone on a line), so it needs no literal 20."""
    real = open(_TABLE, encoding="utf-8").read()
    lines = real.splitlines(keepends=True)
    corrupted, n = [], 0
    for ln in lines:
        m = re.match(r'^(\s*)"([^":]+):([^":]+):#[0-9a-fA-F]+:([^"]+)"(\s*)$', ln)
        if m and n < 17:
            n += 1
            corrupted.append('%s"%s:%s:#zzz:%s"%s\n'
                             % (m.group(1), m.group(2), m.group(3), m.group(4),
                                m.group(5).rstrip("\n")))
        else:
            corrupted.append(ln)
    assert n == 17, n                      # the fixture really did corrupt 17

    broken = tmp_path / "scratch-slots.sh"
    broken.write_text("".join(corrupted), encoding="utf-8")

    # the grammar still accepts the 3 survivors — that is the whole hazard
    rx = blk.slot_pattern(broken.read_text())
    assert len(rx.findall(broken.read_text())) == 3

    assert blk.load_slots([str(broken)]) is None, (
        "a table that parsed to 3 of 20 entries was accepted as a reading")
    assert blk.render(blk.load_slots([str(broken)]), {})["text"] == blk.UNMEASURED


def test_the_floor_lets_a_LATER_path_rescue_a_corrupt_earlier_one(tmp_path):
    """The floor is per-file: a corrupt deployed copy must fall through to a
    good one, not blank the pill."""
    real = open(_TABLE, encoding="utf-8").read()
    broken = tmp_path / "scratch-slots.sh"
    broken.write_text(real.replace("#b8bb26", "#zz"), encoding="utf-8")
    assert blk.load_slots([str(broken)]) is None          # alone: unmeasured
    slots = blk.load_slots([str(broken), _TABLE])         # with a fallback: fine
    assert slots is not None and len(slots) == len(_bash_slot_entries())


# --------------------------------------------------------------------------- #
# render()
# --------------------------------------------------------------------------- #
def test_render_colours_and_bolds_present_slots_and_dims_absent_ones():
    out = blk.render(_SLOTS, _SESSIONS)
    assert out["state"] == "Idle"
    text = out["text"]
    assert text == (
        '<span foreground="#111111" weight="bold">a7</span>'
        ' <span foreground="#504945">b</span>'   # DIM, NOT the slot's own colour
        ' <span foreground="#333333" weight="bold">c4</span>'
    ), text


def test_render_dims_an_absent_slot_and_gives_it_NO_count():
    out = blk.render(_SLOTS, _SESSIONS)
    tree = ET.fromstring("<r>" + out["text"] + "</r>")
    spans = {s.text[0]: s for s in tree}
    assert spans["b"].get("foreground") == blk.DIM
    assert spans["b"].get("weight") is None
    assert spans["b"].text == "b"          # key alone — no digits
    # …and the present ones DO carry their own colour + count
    assert spans["a"].get("foreground") == "#111111"
    assert spans["a"].get("weight") == "bold"
    assert spans["a"].text == "a7"
    assert spans["c"].text == "c4"


def test_render_keeps_slot_table_ORDER_and_single_space_separation():
    out = blk.render(_SLOTS, _SESSIONS)
    tree = ET.fromstring("<r>" + out["text"] + "</r>")
    assert [s.text for s in tree] == ["a7", "b", "c4"]
    # exactly one space between spans, none leading or trailing
    assert "  " not in out["text"]
    assert out["text"] == out["text"].strip()
    assert out["text"].count("</span> <span") == len(_SLOTS) - 1


def test_render_with_zero_live_sessions_is_a_FULL_DIM_LEGEND_not_a_question_mark():
    """🔴 THE READING THAT LOOKS LIKE NOTHING. An empty session map is a REAL
    measurement — the operator has no scratchpads up — and must render every
    slot, dim. If this ever renders `?` the pill has stopped distinguishing
    "measured zero" from "could not measure" in the other direction."""
    out = blk.render(_SLOTS, {})
    assert out["state"] == "Idle"
    assert out["text"] != blk.UNMEASURED
    assert "?" not in out["text"]
    tree = ET.fromstring("<r>" + out["text"] + "</r>")
    assert [s.text for s in tree] == ["a", "b", "c"]
    assert {s.get("foreground") for s in tree} == {blk.DIM}
    assert all(s.get("weight") is None for s in tree)


def test_render_unmeasured_when_the_slot_table_is_None():
    out = blk.render(None, _SESSIONS)
    assert out["text"] == blk.UNMEASURED
    assert out["text"].endswith("?")
    assert out["state"] == "Idle"
    assert out["text"] != blk.render(_SLOTS, {})["text"]


def test_render_unmeasured_when_the_session_map_is_None():
    out = blk.render(_SLOTS, None)
    assert out["text"] == blk.UNMEASURED
    assert out["text"] != blk.render(_SLOTS, {})["text"], (
        "an unmeasured read rendered as a measured zero")


def test_render_branches_on_IS_NONE_not_on_truthiness():
    """{} and None are both falsy. Branching on truthiness is the single edit
    that collapses "measured zero" into "could not measure"."""
    assert blk.render(_SLOTS, {})["text"] != blk.UNMEASURED
    assert blk.render(_SLOTS, None)["text"] == blk.UNMEASURED
    assert blk.render([], _SESSIONS)["text"] == blk.UNMEASURED


# --------------------------------------------------------------------------- #
# fetch_sessions() — the tool-output parsing seam
# --------------------------------------------------------------------------- #
def test_fetch_sessions_parses_names_and_window_counts():
    got = blk.fetch_sessions(
        run=lambda: (0, "scratchA 7\nscratchC 4\nmain 12\n", ""))
    assert got == {"scratchA": 7, "scratchC": 4, "main": 12}


_NO_SOCKET = lambda: (None, None)                    # noqa: E731
_SOCKET_FOUND = lambda: ("/run/user/1000",           # noqa: E731
                         "/run/user/1000/tmux-1000/default")


def test_fetch_sessions_ignores_malformed_lines():
    """Malformed lines are skipped — but only while SOMETHING parsed. The
    all-garbage case is a different claim and is pinned separately below."""
    got = blk.fetch_sessions(
        run=lambda: (0, "scratchA 7\ngarbage\nscratchB notanumber\n", ""),
        socket_finder=_NO_SOCKET)
    assert got == {"scratchA": 7}


def test_fetch_sessions_rc_ZERO_with_unreadable_output_is_UNMEASURED():
    """🔴 F4: THE FALSE ZERO ON THE SUCCESS PATH. The docstring promised that a
    tmux format change would cost a visible `?`; that was true only of the
    NON-ZERO-exit path. `tmux` exiting 0 with output this parser cannot read a
    single line of used to yield `{}` — the MEASURED-ZERO value — and render the
    full dim legend while every scratchpad was live."""
    for out in ("some new format tmux invented\n",
                "scratchA|7\nscratchB|4\n",
                "   \tnot-a-pair\n",
                "a b c\nd e f\n"):
        got = blk.fetch_sessions(run=lambda o=out: (0, o, ""),
                                 socket_finder=_NO_SOCKET)
        assert got is None, (out, got)
        assert blk.render(_SLOTS, got)["text"] == blk.UNMEASURED


def test_fetch_sessions_rc_ZERO_with_EMPTY_output_is_still_a_REAL_ZERO():
    """The control on the rule above, and the case it must NOT swallow: a
    server holding no sessions prints nothing at all. That is a reading of zero
    and renders the full dim legend."""
    for out in ("", "\n", "   \n\n"):
        got = blk.fetch_sessions(run=lambda o=out: (0, o, ""),
                                 socket_finder=_NO_SOCKET)
        assert got == {}, (out, got)
        assert got is not None
        assert blk.render(_SLOTS, got)["text"] != blk.UNMEASURED


def test_fetch_sessions_no_server_running_is_a_REAL_ZERO():
    """🔴 The one non-zero exit that IS a reading. tmux exits 1 with `no server
    running on /tmp/tmux-1000/default` when nothing is up — that is genuinely
    "zero sessions", and it must render the legend, not `?`.

    Conditioned on there being no socket anywhere: see the next test."""
    got = blk.fetch_sessions(
        run=lambda: (1, "", "no server running on /tmp/tmux-1000/default\n"),
        socket_finder=_NO_SOCKET)
    assert got == {}
    assert got is not None
    out = blk.render(_SLOTS, got)
    assert out["text"] != blk.UNMEASURED
    assert "?" not in out["text"]


def test_no_server_running_is_a_real_zero_ONLY_WHEN_NO_SOCKET_EXISTS():
    """🔴 F5: `no server running on <path>` names the path tmux LOOKED AT, which
    is not necessarily where the socket is.

    MEASURED on the workbench 2026-09-11 with `TMUX_TMPDIR` unset — which is the
    bar's environment, since that variable is exported by the interactive shell
    and appears in no config file on this box — tmux looked at
    `/tmp/tmux-1000/default` while 20 scratchpads were live on
    `/run/user/1000/tmux-1000/default`. A stale socket left in /tmp by any
    process turns that into the `no server running` spelling, i.e. a FALSE
    ZERO: a full dim legend over a full house."""
    err = "no server running on /tmp/tmux-1000/default\n"
    assert blk.fetch_sessions(run=lambda: (1, "", err),
                              socket_finder=_SOCKET_FOUND) is None
    # and the discriminating control: same stderr, no socket anywhere -> zero
    assert blk.fetch_sessions(run=lambda: (1, "", err),
                              socket_finder=_NO_SOCKET) == {}


def test_fetch_sessions_no_server_running_match_is_case_insensitive():
    assert blk.fetch_sessions(
        run=lambda: (1, "", "NO SERVER RUNNING on /tmp/x\n"),
        socket_finder=_NO_SOCKET) == {}


def test_socket_roots_name_run_user_LITERALLY_not_only_via_XDG(monkeypatch):
    """🔴 `$XDG_RUNTIME_DIR` is as absent from a bar block's environment as
    `$TMUX_TMPDIR` is, so `/run/user/<uid>` is listed as a literal. Measured
    with BOTH variables stripped, which is the case that matters."""
    monkeypatch.delenv("TMUX_TMPDIR", raising=False)
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    roots = blk._socket_roots()
    assert "/run/user/%d" % os.getuid() in roots, roots
    assert roots[-1] == "/tmp", roots          # tmux's own fallback, last
    # …and an explicit TMUX_TMPDIR still wins
    monkeypatch.setenv("TMUX_TMPDIR", "/somewhere/else")
    assert blk._socket_roots()[0] == "/somewhere/else"


def test_find_socket_returns_the_FIRST_root_that_has_one():
    seen = []

    def exists(p):
        seen.append(p)
        return p == "/run/user/%d/tmux-%d/default" % (os.getuid(), os.getuid())

    root, sock = blk.find_socket(roots=["/tmp", "/run/user/%d" % os.getuid()],
                                 exists=exists)
    assert root == "/run/user/%d" % os.getuid()
    assert sock == "/run/user/%d/tmux-%d/default" % (os.getuid(), os.getuid())
    # negative control: nothing anywhere -> (None, None), not a crash
    assert blk.find_socket(roots=["/tmp"], exists=lambda p: False) == (None, None)


def test_run_tmux_POINTS_TMUX_AT_THE_SOCKET_IT_FOUND(monkeypatch):
    """🔴 Finding the socket is worth nothing unless the subprocess is told.
    This is the seam between `find_socket` and the tmux invocation — each side
    is individually plausible and neither test above can see it."""
    captured = {}

    class _P:
        returncode, stdout, stderr = 0, "scratch 1\n", ""

    def fake_run(argv, **kw):
        captured["argv"] = argv
        captured["env"] = kw.get("env")
        return _P()

    monkeypatch.setattr(blk.subprocess, "run", fake_run)
    monkeypatch.setattr(blk, "find_socket", lambda: ("/run/user/4242", "/s"))
    rc, out, err = blk._run_tmux()
    assert rc == 0 and out == "scratch 1\n"
    assert captured["argv"][0] == "tmux"
    assert captured["env"]["TMUX_TMPDIR"] == "/run/user/4242", captured["env"]

    # …and when nothing is found the ambient environment is left alone rather
    # than being pointed somewhere arbitrary.
    monkeypatch.setenv("TMUX_TMPDIR", "/ambient")
    monkeypatch.setattr(blk, "find_socket", lambda: (None, None))
    blk._run_tmux()
    assert captured["env"]["TMUX_TMPDIR"] == "/ambient", captured["env"]


def test_fetch_sessions_an_UNEXPECTED_nonzero_exit_is_unmeasured():
    """🔴 The fallback direction is deliberately toward `?`. A tmux that is
    wedged, a broken socket, a permissions error — none of those is a reading of
    zero, and calling them zero is the quiet lie this block exists to avoid."""
    got = blk.fetch_sessions(
        run=lambda: (1, "", "error connecting to /tmp/tmux-1000/default "
                            "(Permission denied)\n"))
    assert got is None
    assert blk.render(_SLOTS, got)["text"] == blk.UNMEASURED


def test_fetch_sessions_missing_tmux_executable_is_unmeasured():
    def boom():
        raise FileNotFoundError(2, "No such file or directory", "tmux")

    got = blk.fetch_sessions(run=boom)
    assert got is None
    assert blk.render(_SLOTS, got)["text"] == blk.UNMEASURED


def test_fetch_sessions_any_other_exception_is_unmeasured():
    def boom():
        raise RuntimeError("timeout expired")

    assert blk.fetch_sessions(run=boom) is None


# --------------------------------------------------------------------------- #
# JSON + pango well-formedness
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("slots,sessions", [
    (_SLOTS, _SESSIONS),
    (_SLOTS, {}),
    (None, _SESSIONS),
    (_SLOTS, None),
    (None, None),
])
def test_every_render_is_valid_json_and_always_Idle(slots, sessions):
    parsed = json.loads(json.dumps(blk.render(slots, sessions)))
    assert parsed["state"] == "Idle"
    assert isinstance(parsed["text"], str) and parsed["text"] != ""


def test_the_emitted_text_is_WELL_FORMED_PANGO_MARKUP():
    """🔴 i3status-rust passes `text` through `$text.pango-str()`, so the string
    IS markup, and it does NOT sanitise it.

    MEASURED 2026-09-11 (i3status-rs 0.36.1, throwaway TOML, `timeout 6`): a
    block emitting `a < b & c` reaches the i3bar line as `full_text":" a < b &
    c "` with `"markup":"pango"` — verbatim, unescaped, unvalidated, and the
    other blocks on the line are still emitted. ⚠ SO THE SCOPE OF THE DAMAGE IS
    NOT MEASURED HERE: what pango and i3bar do with invalid markup, and whether
    it costs one block or the line, was NOT observed — checking it means
    rendering the operator's real bar. An earlier version of this docstring
    asserted "breaks the whole bar line, not just this block"; that was never
    verified and is withdrawn. What IS established is that nothing between this
    script and pango will escape a stray `<` for us, which is reason enough to
    escape it here. Parse the output as XML so a future unescaped character
    fails here rather than on the operator's screen."""
    ET.fromstring("<r>" + blk.render(_SLOTS, _SESSIONS)["text"] + "</r>")
    ET.fromstring("<r>" + blk.render(_SLOTS, {})["text"] + "</r>")
    # …and against the REAL table, so a hostile character landing in the slot
    # file is caught too.
    real = blk.load_slots([os.path.join(_SCRIPTS, "tmux-scratch-slots.sh")])
    ET.fromstring("<r>" + blk.render(real, {"scratch": 3})["text"] + "</r>")


def test_a_hostile_key_is_ESCAPED_not_interpolated_raw():
    """🔴 THE GUARD'S REACHABILITY CASE, not a hypothetical: feed a slot whose
    key really does contain `<` and `&` and watch it come back escaped. Without
    `html.escape` the XML parse below raises and the bar line is corrupt."""
    hostile = [("scratchX", "<&x>", "#444444", "Hos<tile&"),
               ("scratchY", "q", "#555555", "Quiet")]
    out = blk.render(hostile, {"scratchX": 5})
    assert "<span" in out["text"]
    # the raw hostile key must NOT appear; its escaped form must
    assert "<&x>" not in out["text"].replace("<span", "").replace("</span>", "")
    assert html.escape("<&x>") in out["text"]
    tree = ET.fromstring("<r>" + out["text"] + "</r>")          # must not raise
    assert [s.text for s in tree] == ["<&x>5", "q"]             # round-trips


def test_the_last_resort_pill_IS_the_unmeasured_pill():
    """A second hand-spelled literal is how the discriminant drifts back to an
    empty legend while every other path stays correct."""
    assert blk._UNMEASURED_PILL == blk.render(None, None)
    assert blk._UNMEASURED_PILL["text"] == blk.UNMEASURED
    assert blk._UNMEASURED_PILL["text"].endswith("?")
    src = open(os.path.join(_SCRIPTS, "i3status-scratchpads"),
               encoding="utf-8").read()
    tail = src.split('if __name__ == "__main__":')[1]
    assert "_UNMEASURED_PILL" in tail, tail


# --------------------------------------------------------------------------- #
# The nix seam: the block, its click, and its sibling data file
# --------------------------------------------------------------------------- #
def _nix_block_body(name):
    nix = _GRAPHICAL.read_text()
    m = re.search(r"%s = \{(.*?)^  \};" % re.escape(name), nix, re.S | re.M)
    assert m, "%s not found in nix/graphical.nix — repin this test" % name
    return nix, m.group(1)


def test_the_scratchPickerCmd_STRING_runs_the_picker_in_a_TERMINAL():
    """🔴 HALF ONE OF TWO, and it never opens `scratchpadsBlock`.

    i3status-rust runs a click through `sh -c` with NO controlling terminal and
    the picker is an fzf TUI — MEASURED: with stdin on /dev/null it dies
    `inappropriate ioctl for device` and the click is a silent no-op. So the
    click must spawn a terminal, exactly like `syshealthCmd`.

    The sibling `…are_wired_to_scratchPickerCmd` is the other half: the repo's
    own measurement on `runawaysBlock` was that pinning the string ALONE leaves
    "a perfect string nothing calls" surviving a mutation."""
    nix = _GRAPHICAL.read_text()
    m = re.search(r'^\s*scratchPickerCmd\s*=\s*"(?P<cmd>.*)";\s*$', nix, re.M)
    assert m, "scratchPickerCmd is gone or reshaped — repin this test"
    cmd = m.group("cmd")
    assert cmd.lstrip().startswith("alacritty"), (
        "the click must run in a TERMINAL — i3status-rust spawns it with no "
        "controlling tty: %r" % cmd)
    assert "scratch-picker.sh" in cmd, cmd
    # Negative control: the regex CAN read a different binding's value, so a
    # green here is not the parser silently matching nothing.
    other = re.search(r'^\s*syshealthCmd\s*=\s*"(?P<cmd>.*)";\s*$', nix, re.M)
    assert other and "syshealth" in other.group("cmd")


def test_the_scratchpads_pill_CLICK_is_wired_to_scratchPickerCmd():
    """🔴 THE MISSING HALF. MEASURED on `runawaysBlock` in this very file's
    neighbour (`test_bar_status.py`): with only the string guard, re-pointing
    the block's click at a different binding left the suite green — a perfect
    string nothing calls. So assert what the BLOCK commands."""
    nix, body = _nix_block_body("scratchpadsBlock")
    wired = dict(re.findall(r'button = "(\w+)";\s*cmd = ([^;]+);', body))
    assert wired, "scratchpadsBlock declares no `button = …; cmd = …;`: %r" % body
    assert wired.get("left") == "scratchPickerCmd", (
        "the scratchpad legend's left-click does not run `scratchPickerCmd` — "
        "it is wired to %r. wired=%r" % (wired.get("left"), wired))
    # the identifier it names must exist
    assert re.search(r'^\s*scratchPickerCmd\s*=\s*"', nix, re.M), (
        "scratchpadsBlock commands `scratchPickerCmd` but graphical.nix defines "
        "no such binding")
    # Negative control: the same parse on runawaysBlock reads a DIFFERENT value,
    # so this is not a regex that matches whatever it is pointed at.
    _n, runaways = _nix_block_body("runawaysBlock")
    assert dict(re.findall(r'button = "(\w+)";\s*cmd = ([^;]+);',
                           runaways)).get("left") == "syshealthCmd"


def test_the_SLOT_TABLE_is_deployed_BESIDE_the_block_script():
    """🔴 THE DEPLOY NOBODY WOULD MISS. On a live host the block script is a
    lone nix-store symlink in ~/.config/i3status-rust/scripts and the slot table
    is symlinked in beside it as `scratch-slots.sh` — leg 1 of `_SLOT_PATHS`,
    and the only leg that is true there.

    Delete that `home.file` entry and leg 1 fails, leg 2 is absent (there is no
    repo `scripts/` next to a deployed symlink) and LEG 3 SILENTLY SUCCEEDS off
    ~/workspace/devrc — a shared, branch-switching working tree. The pill then
    renders a legend read from whatever branch that checkout happens to be on,
    which is not a failure anyone would notice."""
    nix = _GRAPHICAL.read_text()
    m = re.search(
        r'home\.file\."\.config/i3status-rust/scripts/scratch-slots\.sh"'
        r'\s*=\s*\{(.*?)\};', nix, re.S)
    assert m, (
        "nix/graphical.nix no longer deploys scratch-slots.sh beside the block "
        "script — the legend would fall back to ~/workspace/devrc's working "
        "tree (leg 3), which succeeds silently off whatever branch it is on")
    assert "tmux-scratch-slots.sh" in m.group(1), m.group(1)
    # …and the block script itself is deployed, under the name the block commands
    assert re.search(
        r'home\.file\."\.config/i3status-rust/scripts/i3status-scratchpads"',
        nix), "the block script's own home.file entry is gone"
    _n, body = _nix_block_body("scratchpadsBlock")
    assert "i3status-scratchpads" in body, body


def test_the_deployed_sibling_name_is_the_one_LEG_ONE_looks_for():
    """The two halves of leg 1 are in different files and each is plausible
    alone: the block looks for `scratch-slots.sh`, nix deploys under that name.
    A rename on either side is silent — leg 3 catches it on the dev box."""
    assert os.path.basename(blk._SLOT_PATHS[0]) == "scratch-slots.sh"
    nix = _GRAPHICAL.read_text()
    assert '.config/i3status-rust/scripts/scratch-slots.sh' in nix


def test_the_block_SPAWNS_TMUX_AND_NOTHING_ELSE():
    """🔴 THE PIN BEHIND THIS FILE'S `home-manager` ACKNOWLEDGEMENT in
    scripts/tests/test_no_real_launchers.py.

    That scanner is a TEXT scan, so the one clause of comment prose above
    `_socket_roots` naming home-manager reads as a hit. Acknowledging it is the
    repo's convention — but an acknowledgement with no pin BLINDS the guard it
    is filed under, which was MEASURED once on `tmux-reply-agent`: with the row
    in place, injecting a real `subprocess.run(["systemctl", …])` left that
    suite green.

    So: walk the AST, collect every spawn's argv[0], and pin the set BOTH
    WAYS — a new binary fails, and losing tmux fails too. A spawn built from a
    variable becomes `<computed>` and fails rather than silently leaving the
    set."""
    import ast

    src = open(os.path.join(_SCRIPTS, "i3status-scratchpads"),
               encoding="utf-8").read()
    tree = ast.parse(src)

    SPAWNERS = {"run", "Popen", "call", "check_call", "check_output",
                "getoutput", "getstatusoutput"}
    FORBIDDEN = {"system", "popen", "execv", "execve", "execvp", "execvpe",
                 "execl", "execlp", "execle", "spawnv", "spawnl", "posix_spawn"}
    argv0, forbidden_hits = set(), []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        name = fn.attr if isinstance(fn, ast.Attribute) else (
            fn.id if isinstance(fn, ast.Name) else None)
        if name in FORBIDDEN:
            forbidden_hits.append(name)
        if name not in SPAWNERS or not node.args:
            continue
        first = node.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            argv0.add(first.value)
        elif isinstance(first, (ast.List, ast.Tuple)) and first.elts:
            head = first.elts[0]
            argv0.add(head.value if isinstance(head, ast.Constant)
                      and isinstance(head.value, str) else "<computed>")
        else:
            argv0.add("<computed>")

    assert not forbidden_hits, (
        "scripts/i3status-scratchpads reaches a raw exec/system primitive: %r"
        % forbidden_hits)
    assert argv0 == {"tmux"}, (
        "the block's spawn set is %r, not {'tmux'} — the `home-manager` "
        "acknowledgement in test_no_real_launchers.py rests on this file "
        "having exactly one, read-only, tmux call site" % sorted(argv0))


# --------------------------------------------------------------------------- #
# The picker the click runs
# --------------------------------------------------------------------------- #
def test_the_picker_DETACH_branch_requires_TMUX():
    """🔴 F1. `tmux display-message -p '#{session_name}'` does NOT answer "none"
    outside a client — it answers the server's MOST-RECENTLY-USED session. So
    the `scratch*` test alone is a question about the SERVER, not about us.

    REPRODUCED on a private-socket server 2026-09-11, `$TMUX` unset, one client
    attached to the NON-scratch session `work`, `scratch2` MRU: the picker took
    its detach branch and `tmux detach-client` threw the `work` client off the
    server (client-count 1 -> 0), exiting 0 with no output. From the bar that
    reads as "the click did nothing".

    Asserted as STRUCTURE — the `$TMUX` test must GUARD the detach, not merely
    appear in the file."""
    src = open(_PICKER, encoding="utf-8").read()
    # strip comments: the incident is described at length above the code
    code = "\n".join(ln for ln in src.splitlines()
                     if not ln.lstrip().startswith("#"))
    assert "detach-client" in code, code
    guard = re.search(
        r'if \[ -n "\$\{TMUX:-\}" \]; then(?P<body>.*?)\nfi', code, re.S)
    assert guard, (
        "the detach branch is no longer guarded by `[ -n \"${TMUX:-}\" ]` — "
        "without it the picker detaches a live client of whatever session the "
        "server used last:\n%s" % code)
    assert "detach-client" in guard.group("body"), (
        "`detach-client` escaped the $TMUX guard: %r" % guard.group("body"))
    # …and nothing detaches outside it
    assert code.count("detach-client") == 1, code


def test_the_picker_HOLDS_the_terminal_open_when_it_can_do_NEITHER():
    """Attach failed and create failed: with `exec` there was nothing left to
    hold the float terminal open, so it vanished — the same
    indistinguishable-from-nothing failure `syshealthCmd` carries a hold for."""
    src = open(_PICKER, encoding="utf-8").read()
    code = "\n".join(ln for ln in src.splitlines()
                     if not ln.lstrip().startswith("#"))
    assert "read -n 1" in code, code
    assert "exec tmux" not in code, (
        "`exec` replaces this shell, so the failure hold below it can never "
        "run: %s" % code)
    tail = code.split("new-session -s")[-1]
    assert "read -n 1" in tail, tail


def test_the_block_is_text_only_and_carries_no_icon_glyph():
    """Deliberately no nerd-font codepoint: an unverified one renders as tofu.
    Assert on the RENDERED text (structural), not on the source prose."""
    for slots, sessions in ((_SLOTS, _SESSIONS), (_SLOTS, {}), (None, None)):
        text = blk.render(slots, sessions)["text"]
        assert all(ord(ch) < 0x2000 for ch in text), text
