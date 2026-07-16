"""Unit tests for EspansoDetector: direct triggers + Ctrl+Space search UI.

The detector reconstructs espanso firings from the raw keystroke stream (the
only place they are observable, since espanso erases both trigger and expansion).
These tests pin the documented semantics: prefix collisions, no double-emit on
espanso's trailing backspaces, focus-reset, and best-effort search attribution.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import espanso_triggers as ET       # noqa: E402
from espanso_detect import EspansoDetector  # noqa: E402

# A representative slice of the live config. :date is a prefix of :datetime
# (prefix-collision case); the workflow triggers carry labels/search_terms used
# by the fuzzy search-attribution tests.
BASE = {"matches": [
    {"label": "Today's date", "replace": "{{d}}",
     "search_terms": ["today", "calendar"], "trigger": ":date"},
    {"label": "Date and time", "replace": "{{dt}}",
     "search_terms": ["timestamp"], "trigger": ":datetime"},
    {"label": "Recommend next steps ranked by leverage", "replace": "...",
     "search_terms": ["ranked", "leverage"], "trigger": ":rnx"},
    {"label": "Dispatch subagent", "replace": "...",
     "search_terms": ["dispatch", "delegate"], "trigger": ":ds"},
]}
DEFAULT = {"search_shortcut": "CTRL+SPACE"}

APP, SESS = "kitty", "win-1"


def _det():
    return EspansoDetector(ET.load_triggers(BASE, DEFAULT))


def _type(det, s, *, app=APP, session=SESS, now=0.0):
    """Feed a string char-by-char, returning ALL emitted events."""
    out = []
    for i, ch in enumerate(s):
        out.extend(det.feed_char(ch, app=app, session=session, now=now + i))
    return out


# -- direct triggers ---------------------------------------------------------
def test_direct_trigger_at_buffer_end():
    d = _det()
    evs = _type(d, ":date")
    assert len(evs) == 1
    assert evs[0].trigger == ":date"
    assert evs[0].method == "direct"
    assert evs[0].inferred is False
    assert evs[0].app == APP and evs[0].session == SESS


def test_trigger_after_other_chars():
    d = _det()
    evs = _type(d, "foo:rnx")
    assert [e.trigger for e in evs] == [":rnx"]


def test_prefix_collision_emits_shorter_first():
    # Typing ":datetime" must emit ":date" (mirrors espanso firing on the prefix);
    # ":datetime" never forms because firing consumes the trigger.
    d = _det()
    evs = _type(d, ":datetime")
    assert [e.trigger for e in evs] == [":date"]


def test_backspaces_after_trigger_do_not_reemit_but_retype_does():
    d = _det()
    evs1 = _type(d, ":date")
    assert [e.trigger for e in evs1] == [":date"]
    # espanso backspaces the trigger away → no-ops on the (cleared) ring.
    evs2 = _type(d, "\b\b\b\b\b", now=10.0)
    assert evs2 == []
    # A genuinely retyped trigger fires again.
    evs3 = _type(d, ":date", now=20.0)
    assert [e.trigger for e in evs3] == [":date"]


def test_plain_typing_emits_nothing():
    d = _det()
    assert _type(d, "hello world, no triggers here") == []


def test_focus_change_resets_ring():
    d = _det()
    # ":da" under window A, then "te" under window B → the ":date" sequence is
    # broken across the focus boundary, so nothing fires.
    out = []
    for i, ch in enumerate(":da"):
        out.extend(d.feed_char(ch, app="A", session="wA", now=i))
    for i, ch in enumerate("te"):
        out.extend(d.feed_char(ch, app="B", session="wB", now=10 + i))
    assert out == []


# -- Ctrl+Space search UI ----------------------------------------------------
def test_search_open_term_then_enter_emits_one_search_event():
    d = _det()
    d.feed_search_open(app=APP, session=SESS, now=0.0)
    assert _type(d, "today", now=1.0) == []          # accumulates, no emit yet
    evs = list(d.feed_char("\n", app=APP, session=SESS, now=6.0))
    assert len(evs) == 1
    ev = evs[0]
    assert ev.method == "search"
    assert ev.inferred is True
    assert ev.search_term == "today"


def test_search_term_does_not_feed_direct_ring():
    # Typing ":date" WHILE in search-mode must NOT fire a direct event.
    d = _det()
    d.feed_search_open(app=APP, session=SESS, now=0.0)
    evs = _type(d, ":date", now=1.0)
    assert evs == []  # captured as a search term, not a direct trigger


def test_search_fuzzy_unique_match_attributes():
    d = _det()
    d.feed_search_open(app=APP, session=SESS, now=0.0)
    _type(d, "leverage", now=1.0)  # matches ONLY :rnx (label + search_terms)
    evs = list(d.feed_char("\n", app=APP, session=SESS, now=9.0))
    assert len(evs) == 1
    assert evs[0].trigger == ":rnx"
    assert evs[0].inferred is True
    assert evs[0].search_term == "leverage"


def test_search_fuzzy_zero_match_still_emits_with_term():
    d = _det()
    d.feed_search_open(app=APP, session=SESS, now=0.0)
    _type(d, "zzzzz", now=1.0)
    evs = list(d.feed_char("\n", app=APP, session=SESS, now=6.0))
    assert len(evs) == 1
    assert evs[0].trigger is None
    assert evs[0].search_term == "zzzzz"


def test_search_fuzzy_multiple_match_is_ambiguous():
    # "date" is a substring of BOTH :date and :datetime → ambiguous → trigger None.
    d = _det()
    d.feed_search_open(app=APP, session=SESS, now=0.0)
    _type(d, "date", now=1.0)
    evs = list(d.feed_char("\n", app=APP, session=SESS, now=5.0))
    assert len(evs) == 1
    assert evs[0].trigger is None
    assert evs[0].search_term == "date"


def test_search_flush_on_idle_without_enter():
    d = _det()
    d.feed_search_open(app=APP, session=SESS, now=0.0)
    _type(d, "today", now=1.0)
    # No Enter; the idle sweep closes it.
    evs = d.flush_idle(now=100.0, idle_seconds=2.0)
    assert len(evs) == 1
    assert evs[0].method == "search"
    assert evs[0].search_term == "today"
    # Not idle yet → nothing.
    assert EspansoDetector(ET.load_triggers(BASE, DEFAULT)).flush_idle(0, 2) == []


def test_search_flush_on_focus_change_without_enter():
    d = _det()
    d.feed_search_open(app="A", session="wA", now=0.0)
    for i, ch in enumerate("today"):
        d.feed_char(ch, app="A", session="wA", now=1 + i)
    # Focus moves to another window before Enter → search closes and emits.
    evs = d.feed_char("x", app="B", session="wB", now=10.0)
    assert len(evs) == 1
    assert evs[0].method == "search"
    assert evs[0].search_term == "today"
    assert evs[0].app == "A"  # attributed to the window where search happened


def test_empty_trigger_set_is_inert():
    d = EspansoDetector(ET.TriggerSet())
    assert _type(d, ":date anything :rnx") == []
    d.feed_search_open(app=APP, session=SESS, now=0.0)
    # An empty search close is a phantom → suppressed (no trigger=None row).
    evs = list(d.feed_char("\n", app=APP, session=SESS, now=1.0))
    assert evs == []


# -- FIX 1: bounded search-mode + phantom-empty suppression ------------------
def test_search_term_over_cap_aborts_without_emitting():
    # A non-espanso Ctrl+Space that keeps typing past the cap is a misfire:
    # search-mode aborts silently (no method=search row for ordinary text).
    from espanso_detect import SEARCH_TERM_MAX
    d = _det()
    d.feed_search_open(app=APP, session=SESS, now=0.0)
    long_term = "x" * (SEARCH_TERM_MAX + 5)
    assert _type(d, long_term, now=1.0) == []
    # search-mode is off again; a subsequent close produces nothing.
    assert list(d.feed_char("\n", app=APP, session=SESS, now=200.0)) == []
    # And the direct ring is live again (typed text is no longer swallowed).
    assert [e.trigger for e in _type(d, ":date", now=300.0)] == [":date"]


def test_ctrl_space_then_empty_close_suppresses_phantom():
    # Accidental Ctrl+Space then Escape with nothing typed → NO phantom row.
    d = _det()
    d.feed_search_open(app=APP, session=SESS, now=0.0)
    evs = list(d.feed_char("\x1b", app=APP, session=SESS, now=1.0))
    assert evs == []


def test_search_whitespace_only_close_suppressed():
    d = _det()
    d.feed_search_open(app=APP, session=SESS, now=0.0)
    _type(d, "   ", now=1.0)
    evs = list(d.feed_char("\n", app=APP, session=SESS, now=5.0))
    assert evs == []


def test_search_short_attributed_term_still_emits():
    # Short term that uniquely attributes → event as before (unchanged).
    d = _det()
    d.feed_search_open(app=APP, session=SESS, now=0.0)
    _type(d, "leverage", now=1.0)
    evs = list(d.feed_char("\n", app=APP, session=SESS, now=9.0))
    assert len(evs) == 1
    assert evs[0].trigger == ":rnx"
    assert evs[0].inferred is True


def test_search_short_unattributed_term_emits_trigger_none():
    # Short term matching nothing → legit "real search we couldn't attribute".
    d = _det()
    d.feed_search_open(app=APP, session=SESS, now=0.0)
    _type(d, "zzzzz", now=1.0)
    evs = list(d.feed_char("\n", app=APP, session=SESS, now=6.0))
    assert len(evs) == 1
    assert evs[0].trigger is None
    assert evs[0].inferred is True
    assert evs[0].search_term == "zzzzz"


# -- FIX 3: espanso search window steals X focus -----------------------------
# espanso's Ctrl+Space search bar opens as its OWN window (WM_CLASS
# .espanso-wrapped) and STEALS X focus. The FIRST query char therefore arrives
# under app=".espanso-wrapped", a focus change from the original window. That
# must NOT close search-mode with an empty term (the live bug: the search path
# produced ZERO events because search died before the query was typed).
ESPANSO_WIN = ".espanso-wrapped"


def test_focus_steal_by_espanso_window_keeps_search_and_attributes():
    # Ctrl+Space recorded under Alacritty; espanso's window then steals focus and
    # the query "leverage" is typed INTO .espanso-wrapped; Enter closes it.
    d = _det()
    d.feed_search_open(app="Alacritty", session="win-term", now=0.0)
    # Query chars arrive under the STOLEN focus (espanso's own window).
    evs = _type(d, "leverage", app=ESPANSO_WIN, session="win-esp", now=1.0)
    assert evs == []  # accumulates, nothing emitted mid-term
    out = list(d.feed_char("\n", app=ESPANSO_WIN, session="win-esp", now=9.0))
    assert len(out) == 1
    ev = out[0]
    assert ev.method == "search"
    assert ev.inferred is True
    assert ev.search_term == "leverage"
    assert ev.trigger == ":rnx"  # "leverage" uniquely maps to :rnx
    # Attributed to the ORIGIN window (where Ctrl+Space was pressed), NOT the
    # ".espanso-wrapped" window that stole focus mid-search.
    assert ev.app == "Alacritty"
    assert ev.session == "win-term"


def test_focus_steal_ambiguous_term_still_emits_trigger_none():
    # Same focus-steal path, but an ambiguous term ("date" ⊂ :date and :datetime)
    # → trigger=None yet the event is still emitted (real search, unattributed).
    d = _det()
    d.feed_search_open(app="Alacritty", session="win-term", now=0.0)
    _type(d, "date", app=ESPANSO_WIN, session="win-esp", now=1.0)
    out = list(d.feed_char("\n", app=ESPANSO_WIN, session="win-esp", now=6.0))
    assert len(out) == 1
    assert out[0].trigger is None
    assert out[0].search_term == "date"
    assert out[0].method == "search"
    assert out[0].app == "Alacritty"  # origin window preserved


def test_focus_steal_multichar_window_hop_accumulates_full_term():
    # Every char of the term lands under the espanso window (no per-char focus
    # thrash re-closing search); the FULL term survives to attribution.
    d = _det()
    d.feed_search_open(app="Alacritty", session="win-term", now=0.0)
    for i, ch in enumerate("clarify"):
        assert d.feed_char(ch, app=ESPANSO_WIN, session="win-esp", now=1 + i) == []
    out = list(d.feed_char("\n", app=ESPANSO_WIN, session="win-esp", now=20.0))
    assert len(out) == 1
    assert out[0].search_term == "clarify"
    assert out[0].app == "Alacritty"  # origin window preserved


def test_focus_change_to_nonespanso_window_still_closes_search():
    # A focus change to a genuinely DIFFERENT non-espanso window (e.g. the
    # browser) is a real abandon: search closes under the OLD context and does
    # NOT keep accumulating into the new window.
    d = _det()
    d.feed_search_open(app="Alacritty", session="win-term", now=0.0)
    _type(d, "today", app="Alacritty", session="win-term", now=1.0)
    # Focus jumps to Brave before Enter → close under the old context.
    out = list(d.feed_char("x", app="Brave-browser", session="win-brave", now=10.0))
    assert len(out) == 1
    assert out[0].method == "search"
    assert out[0].search_term == "today"  # the "x" did NOT get appended
    assert out[0].app == "Alacritty"      # closed under the window search began in
    # Search-mode is off; the stray "x" and a following Enter emit nothing more.
    assert list(d.feed_char("\n", app="Brave-browser", session="win-brave", now=11.0)) == []


def test_espanso_focus_steal_does_not_break_direct_fires():
    # Regression: after a focus-steal search completes, direct triggers under a
    # normal window still fire (search state fully reset).
    d = _det()
    d.feed_search_open(app="Alacritty", session="win-term", now=0.0)
    _type(d, "leverage", app=ESPANSO_WIN, session="win-esp", now=1.0)
    list(d.feed_char("\n", app=ESPANSO_WIN, session="win-esp", now=9.0))
    evs = _type(d, ":date", app="Alacritty", session="win-term", now=20.0)
    assert [e.trigger for e in evs] == [":date"]


def test_empty_close_still_suppressed_under_espanso_focus_steal():
    # Focus steal to the espanso window, then Escape with nothing typed → still a
    # phantom empty close → suppressed (prior fix preserved through this path).
    d = _det()
    d.feed_search_open(app="Alacritty", session="win-term", now=0.0)
    out = list(d.feed_char("\x1b", app=ESPANSO_WIN, session="win-esp", now=1.0))
    assert out == []


# -- FIX 2: caret-navigation resets the direct ring --------------------------
def test_notify_navigation_resets_direct_ring():
    # ":da" then a caret move then "te" → ":date" was NOT typed contiguously,
    # so nothing fires (mirrors espanso resetting its buffer on nav keys).
    d = _det()
    assert _type(d, ":da") == []
    d.notify_navigation()
    evs = _type(d, "te", now=10.0)
    assert evs == []


def test_notify_navigation_leaves_search_mode_intact():
    # A nav key during search must NOT drop the accumulated term.
    d = _det()
    d.feed_search_open(app=APP, session=SESS, now=0.0)
    _type(d, "today", now=1.0)
    d.notify_navigation()
    evs = list(d.feed_char("\n", app=APP, session=SESS, now=8.0))
    assert len(evs) == 1
    assert evs[0].search_term == "today"
