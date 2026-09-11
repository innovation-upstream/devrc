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
import xml.etree.ElementTree as ET

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS = os.path.join(_HERE, "..")


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
def test_the_REAL_slot_table_parses_to_twenty_slots_with_the_expected_entry():
    """Pinned as LITERALS, never derived from the parser's own output — a
    parser that dropped a field would otherwise agree with itself."""
    slots = blk.load_slots(
        [os.path.join(_SCRIPTS, "tmux-scratch-slots.sh")])
    assert slots is not None
    assert len(slots) == 20, [s[0] for s in slots]
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


def test_fetch_sessions_ignores_malformed_lines():
    got = blk.fetch_sessions(
        run=lambda: (0, "scratchA 7\ngarbage\nscratchB notanumber\n", ""))
    assert got == {"scratchA": 7}


def test_fetch_sessions_no_server_running_is_a_REAL_ZERO():
    """🔴 The one non-zero exit that IS a reading. tmux exits 1 with `no server
    running on /tmp/tmux-1000/default` when nothing is up — that is genuinely
    "zero sessions", and it must render the legend, not `?`."""
    got = blk.fetch_sessions(
        run=lambda: (1, "", "no server running on /tmp/tmux-1000/default\n"))
    assert got == {}
    assert got is not None
    out = blk.render(_SLOTS, got)
    assert out["text"] != blk.UNMEASURED
    assert "?" not in out["text"]


def test_fetch_sessions_no_server_running_match_is_case_insensitive():
    assert blk.fetch_sessions(
        run=lambda: (1, "", "NO SERVER RUNNING on /tmp/x\n")) == {}


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
    IS markup. An unescaped `<` or `&` anywhere in it breaks the whole bar line,
    not just this block. Parse it as XML so a future unescaped character fails
    here rather than on the operator's screen."""
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


def test_the_block_is_text_only_and_carries_no_icon_glyph():
    """Deliberately no nerd-font codepoint: an unverified one renders as tofu.
    Assert on the RENDERED text (structural), not on the source prose."""
    for slots, sessions in ((_SLOTS, _SESSIONS), (_SLOTS, {}), (None, None)):
        text = blk.render(slots, sessions)["text"]
        assert all(ord(ch) < 0x2000 for ch in text), text
