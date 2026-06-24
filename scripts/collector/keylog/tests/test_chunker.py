"""Unit tests for the keystroke chunker: focus / enter / idle / backspace / maxlen."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from chunker import Chunker  # noqa: E402


def feed(ch, c, t, app="xterm", title="T", session="w1", ws="1"):
    return ch.feed(c, app=app, title=title, session=session, workspace=ws, now=t)


def test_enter_flushes_unit_including_newline():
    ch = Chunker(idle_seconds=10)
    assert feed(ch, "h", 1) == []
    assert feed(ch, "i", 1) == []
    out = feed(ch, "\n", 1)
    assert len(out) == 1
    assert out[0].text == "hi\n"
    assert out[0].reason == "enter"


def test_idle_timeout_flushes():
    ch = Chunker(idle_seconds=2)
    feed(ch, "a", 0)
    feed(ch, "b", 1)               # within idle window, no flush
    out = ch.flush_idle(now=5)     # 4s since last key > 2s
    assert len(out) == 1
    assert out[0].text == "ab"
    assert out[0].reason == "idle"
    # Buffer cleared.
    assert ch.flush_idle(now=10) == []


def test_idle_flush_on_next_feed():
    ch = Chunker(idle_seconds=2)
    feed(ch, "a", 0)
    out = feed(ch, "b", 10)        # 10s gap → flushes "a" before recording "b"
    assert [c.text for c in out] == ["a"]
    assert out[0].reason == "idle"
    fin = ch.flush_now()
    assert fin[0].text == "b"


def test_focus_change_flushes_under_old_context():
    ch = Chunker(idle_seconds=100)
    feed(ch, "x", 1, app="xterm", session="w1")
    feed(ch, "y", 1, app="xterm", session="w1")
    out = feed(ch, "z", 1, app="firefox", session="w2")
    assert len(out) == 1
    assert out[0].text == "xy"
    assert out[0].app == "xterm"      # flushed under the OLD context
    assert out[0].reason == "focus"
    # The new char is buffered under the new context.
    fin = ch.flush_now()
    assert fin[0].text == "z"
    assert fin[0].app == "firefox"


def test_backspace_edits_buffer():
    ch = Chunker(idle_seconds=100)
    feed(ch, "h", 1)
    feed(ch, "x", 1)
    feed(ch, "\b", 1)   # delete the "x"
    feed(ch, "i", 1)
    out = ch.flush_now()
    assert out[0].text == "hi"


def test_backspace_on_empty_is_noop():
    ch = Chunker(idle_seconds=100)
    assert feed(ch, "\b", 1) == []
    assert ch.flush_now() == []


def test_maxlen_flushes():
    ch = Chunker(idle_seconds=100, max_chars=3)
    feed(ch, "a", 1)
    feed(ch, "b", 1)
    out = feed(ch, "c", 1)
    assert len(out) == 1
    assert out[0].text == "abc"
    assert out[0].reason == "maxlen"


def test_unicode_and_specials_preserved():
    ch = Chunker(idle_seconds=100)
    for c in "你好!@#":
        feed(ch, c, 1)
    out = ch.flush_now()
    assert out[0].text == "你好!@#"
