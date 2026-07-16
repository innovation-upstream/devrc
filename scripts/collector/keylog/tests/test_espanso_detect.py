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
    evs = list(d.feed_char("\n", app=APP, session=SESS, now=1.0))
    # Search still records the open even with no snippets to attribute.
    assert len(evs) == 1
    assert evs[0].trigger is None
