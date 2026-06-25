"""Unit tests for i3source.build_record — pure mapping of i3ipc-event-like
objects → v1 spool fields. No live i3, no X, no network: synthetic event/con
objects duck-type the i3ipc WindowEvent/WorkspaceEvent + Con shapes.

Also round-trips a built record through the existing collector.parse_line to
prove `source=i3` events ship unchanged through the daemon.
"""
import json
import sys
from pathlib import Path

I3 = Path(__file__).resolve().parent.parent       # scripts/collector/i3
COLLECTOR = I3.parent                              # scripts/collector
KEYLOG = COLLECTOR / "keylog"
sys.path.insert(0, str(I3))
sys.path.insert(0, str(KEYLOG))
sys.path.insert(0, str(COLLECTOR))

import i3source as M  # noqa: E402
import collector as C  # noqa: E402


# --------------------------------------------------------------------------- #
# Synthetic i3ipc-shaped objects (duck-typed; no i3ipc import needed)
# --------------------------------------------------------------------------- #
class FakeWorkspace:
    def __init__(self, name):
        self.name = name


class FakeCon:
    """Duck-types i3ipc.Con: window_class/instance/name + a workspace() method."""
    def __init__(self, window_class=None, window_instance=None, name=None,
                 window_title=None, workspace_name=None):
        self.window_class = window_class
        self.window_instance = window_instance
        self.name = name
        self.window_title = window_title
        self._workspace_name = workspace_name

    def workspace(self):
        if self._workspace_name is None:
            return None
        return FakeWorkspace(self._workspace_name)


class FakeWindowEvent:
    def __init__(self, change, container):
        self.change = change
        self.container = container
        # workspace events carry .current; a window event does not.
        self.current = None


class FakeWorkspaceEvent:
    def __init__(self, change, current):
        self.change = change
        self.current = current
        self.container = None


# --------------------------------------------------------------------------- #
# window-focus
# --------------------------------------------------------------------------- #
def test_window_focus_normal():
    ev = FakeWindowEvent(
        "focus",
        FakeCon(window_class="firefox", window_instance="Navigator",
                name="GitHub - Mozilla Firefox", workspace_name="2:web"),
    )
    f = M.build_record(ev)
    assert f is not None
    assert f["source"] == "i3"
    assert f["kind"] == "window-focus"
    assert f["app"] == "firefox"
    assert f["text"] == "GitHub - Mozilla Firefox"
    pl = json.loads(f["payload"])
    assert pl["title"] == "GitHub - Mozilla Firefox"
    assert pl["workspace"] == "2:web"
    # No dwell / active_ms field is ever emitted.
    assert "active_ms" not in f
    assert "active_ms" not in pl


def test_window_focus_missing_class_falls_back_to_instance():
    ev = FakeWindowEvent(
        "focus",
        FakeCon(window_class=None, window_instance="xterm",
                name="zsh", workspace_name="1"),
    )
    f = M.build_record(ev)
    assert f["app"] == "xterm"
    assert f["text"] == "zsh"


def test_window_focus_missing_class_and_title_graceful_empties():
    ev = FakeWindowEvent(
        "focus",
        FakeCon(window_class=None, window_instance=None,
                name=None, window_title=None, workspace_name=None),
    )
    f = M.build_record(ev)
    assert f["source"] == "i3"
    assert f["kind"] == "window-focus"
    assert f["app"] == ""
    assert f["text"] == ""
    pl = json.loads(f["payload"])
    assert pl["title"] == ""
    assert pl["workspace"] == ""


def test_window_focus_title_falls_back_to_window_title():
    ev = FakeWindowEvent(
        "focus",
        FakeCon(window_class="code", name=None, window_title="main.py — code",
                workspace_name="3"),
    )
    f = M.build_record(ev)
    assert f["text"] == "main.py — code"


# --------------------------------------------------------------------------- #
# workspace-focus
# --------------------------------------------------------------------------- #
def test_workspace_focus():
    ev = FakeWorkspaceEvent("focus", FakeWorkspace("4:chat"))
    f = M.build_record(ev)
    assert f is not None
    assert f["source"] == "i3"
    assert f["kind"] == "workspace-focus"
    assert f["app"] == ""
    assert f["text"] == "4:chat"
    pl = json.loads(f["payload"])
    assert pl["workspace"] == "4:chat"
    assert "title" not in pl


# --------------------------------------------------------------------------- #
# non-focus changes are skipped
# --------------------------------------------------------------------------- #
def test_non_focus_window_change_skipped():
    ev = FakeWindowEvent("title", FakeCon(window_class="firefox", name="x"))
    assert M.build_record(ev) is None


def test_non_focus_workspace_change_skipped():
    ev = FakeWorkspaceEvent("init", FakeWorkspace("9"))
    assert M.build_record(ev) is None


def test_focus_event_with_no_container_or_current_skipped():
    class Bare:
        change = "focus"
        container = None
        current = None
    assert M.build_record(Bare()) is None


# --------------------------------------------------------------------------- #
# round-trip through the existing collector.parse_line
# --------------------------------------------------------------------------- #
# --------------------------------------------------------------------------- #
# current_workspace fallback (Fix 2): window-focus whose container can't resolve
# its own workspace gets stamped with the daemon's tracked workspace.
# --------------------------------------------------------------------------- #
def test_window_focus_uses_tracked_workspace_when_container_unresolved():
    ev = FakeWindowEvent(
        "focus",
        FakeCon(window_class="Alacritty", name="zsh", workspace_name=None),
    )
    f = M.build_record(ev, current_workspace="3:code")
    pl = json.loads(f["payload"])
    assert pl["workspace"] == "3:code"


def test_window_focus_prefers_container_workspace_over_tracked():
    # When the container DOES resolve, that wins over the tracker.
    ev = FakeWindowEvent(
        "focus",
        FakeCon(window_class="firefox", name="x", workspace_name="2:web"),
    )
    f = M.build_record(ev, current_workspace="9:stale")
    assert json.loads(f["payload"])["workspace"] == "2:web"


def test_window_focus_empty_when_neither_container_nor_tracker_resolve():
    ev = FakeWindowEvent(
        "focus",
        FakeCon(window_class="x", name="y", workspace_name=None),
    )
    f = M.build_record(ev)  # current_workspace defaults to ""
    assert json.loads(f["payload"])["workspace"] == ""


def test_workspace_focus_still_sets_from_current():
    # A workspace-focus record reads its workspace from .current, regardless of
    # any tracked value passed in.
    ev = FakeWorkspaceEvent("focus", FakeWorkspace("4:chat"))
    f = M.build_record(ev, current_workspace="ignored")
    assert f["kind"] == "workspace-focus"
    assert json.loads(f["payload"])["workspace"] == "4:chat"


def test_window_focus_roundtrips_through_parse_line():
    import spool_emit as SE
    ev = FakeWindowEvent(
        "focus",
        FakeCon(window_class="Alacritty", name='nvim "weird\ttitle" 你好',
                workspace_name="2"),
    )
    f = M.build_record(ev)
    line = SE.build_line(f)
    parsed = C.parse_line(line)
    assert parsed is not None
    assert parsed["source"] == "i3"
    assert parsed["kind"] == "window-focus"
    assert parsed["app"] == "Alacritty"
    assert parsed["text"] == 'nvim "weird\ttitle" 你好'
    assert json.loads(parsed["payload"])["workspace"] == "2"
