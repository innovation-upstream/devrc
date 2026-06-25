#!/usr/bin/env python3
"""i3source — i3 window/workspace focus collector for the activity pipeline.

Subscribes to i3's IPC event stream (via the `i3ipc` Python library) and emits
ONE `source=i3` record on EVERY window-focus and workspace-focus change —
independent of typing. The keylogger only annotates focus context WHEN the user
types; time spent READING a window without typing was invisible. This daemon
closes that gap so per-app / per-workspace attention and context-switching are
accurate.

Emitted spool records (v1 contract, shipped unchanged by the collector daemon):

    source=i3  kind=window-focus     text=<title>  app=<WM_CLASS>
        payload={"title":<title>,"workspace":<ws name/num>}
    source=i3  kind=workspace-focus  text=<ws name>
        payload={"workspace":<ws name>}

Field names mirror the keylogger's winctx (`app`=WM_CLASS, payload `title` /
`workspace`) so i3 and keys events union cleanly in queries.

NO DWELL FIELD. We do NOT store an `active_ms`/dwell value: dwell is computed
downstream from the gap between consecutive focus events (the dashboard's
deep-work / context-switch panels already work off timestamps). Storing a raw
dwell would re-introduce the "walked-away-for-hours" inflation that was just
fixed; we emit bare focus-change events with an accurate `ts`.

GUI / laptop-only — needs a live i3 IPC socket (`I3SOCK`). Shares `spool_emit`
with the keylogger (single source of truth for the v1 line format).

Config (env)
------------
  ACTIVITY_SPOOL_DIR   spool dir (default ~/.local/state/activity/spool)
  I3SOCK               i3 IPC socket (auto-discovered by i3ipc if unset)
"""
from __future__ import annotations

import sys
from pathlib import Path

# spool_emit lives in the sibling keylog/ dir (single source of truth for the
# v1 line format). Use the INVOKED path — do NOT .resolve(): home-manager
# symlinks each deployed file to a flat /nix/store object, so resolving discards
# the i3/ ↔ keylog/ sibling layout and breaks this import at runtime.
_HERE = Path(__file__).parent
sys.path.insert(0, str(_HERE.parent / "keylog"))
import spool_emit as SE  # noqa: E402

SOURCE = "i3"


def _text(v) -> str:
    """Coerce an i3ipc string-ish value to a plain str, empty on None."""
    if v is None:
        return ""
    return str(v)


def _con_app(con) -> str:
    """WM_CLASS of a container — prefer class, fall back to instance.

    Mirrors winctx._wm_class (which prefers the class name). i3ipc parses these
    straight from the window_properties, so no Xlib round-trip is needed.
    """
    cls = getattr(con, "window_class", None)
    if cls:
        return _text(cls)
    inst = getattr(con, "window_instance", None)
    return _text(inst)


def _con_title(con) -> str:
    """Window title — i3 mirrors _NET_WM_NAME into `name` (and window_title)."""
    name = getattr(con, "name", None)
    if name:
        return _text(name)
    return _text(getattr(con, "window_title", None))


def _con_workspace_name(con) -> str:
    """Name of the workspace containing this container, best-effort.

    `Con.workspace()` walks up to the enclosing workspace node; on a bare/synthetic
    container (or one above the workspace level) it may be absent or return None,
    in which case we yield an empty string rather than raising.
    """
    ws_fn = getattr(con, "workspace", None)
    if not callable(ws_fn):
        return ""
    try:
        ws = ws_fn()
    except Exception:
        return ""
    if ws is None:
        return ""
    return _text(getattr(ws, "name", None))


def _json(obj) -> str:
    import json
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def build_record(event) -> dict | None:
    """Pure: map an i3ipc-event-like object → v1 spool fields (or None to skip).

    Handles WINDOW events (`change == 'focus'`, has `.container`) and WORKSPACE
    events (`change == 'focus'`, has `.current`). Any other change/shape returns
    None so the caller emits nothing. No i3 / X / network — unit-testable with
    synthetic event objects.
    """
    change = getattr(event, "change", None)
    if change != "focus":
        return None

    # Window-focus: the event carries the focused container.
    con = getattr(event, "container", None)
    if con is not None:
        title = _con_title(con)
        workspace = _con_workspace_name(con)
        return {
            "source": SOURCE,
            "kind": "window-focus",
            "text": title,
            "app": _con_app(con),
            "project": "",
            "session": "",
            "payload": _json({"title": title, "workspace": workspace}),
        }

    # Workspace-focus: the newly-focused workspace is `.current`.
    cur = getattr(event, "current", None)
    if cur is not None:
        ws_name = _con_title(cur)  # workspace name lives in `name`
        return {
            "source": SOURCE,
            "kind": "workspace-focus",
            "text": ws_name,
            "app": "",
            "project": "",
            "session": "",
            "payload": _json({"workspace": ws_name}),
        }

    return None


def _safe_emit(fields: dict, spool_dir: Path) -> None:
    """Emit one record; a spool failure must never kill the daemon."""
    try:
        SE.emit(fields, spool_dir=spool_dir)
    except Exception as exc:  # best-effort telemetry
        print(f"i3source: emit failed: {exc}", file=sys.stderr, flush=True)


def main(argv=None) -> int:
    # Import i3ipc lazily so the pure build_record stays testable without the
    # package installed (mirrors how keylog guards its Xlib import behind use).
    try:
        from i3ipc import Connection, Event
    except Exception as exc:
        print(f"i3source: i3ipc unavailable: {exc}", file=sys.stderr, flush=True)
        return 1

    spool_dir = SE.default_spool_dir()

    try:
        i3 = Connection()
    except Exception as exc:
        # No reachable i3 IPC socket (no I3SOCK / i3 not running). Exit non-zero
        # so systemd (Restart=always, RestartSec) retries without a tight loop.
        print(f"i3source: cannot connect to i3 IPC: {exc}", file=sys.stderr, flush=True)
        return 1

    def _on_event(_conn, event):
        fields = build_record(event)
        if fields is not None:
            _safe_emit(fields, spool_dir)

    i3.on(Event.WINDOW_FOCUS, _on_event)
    i3.on(Event.WORKSPACE_FOCUS, _on_event)

    print(f"i3source: spool={spool_dir} — subscribed to window/workspace focus",
          file=sys.stderr, flush=True)

    try:
        # Blocks dispatching events until i3 exits/restarts (main loop returns or
        # raises). Either way we fall through and exit so systemd restarts us and
        # we re-subscribe against the fresh i3.
        i3.main()
    except KeyboardInterrupt:
        return 0
    except Exception as exc:
        print(f"i3source: event loop ended: {exc}", file=sys.stderr, flush=True)
        return 1
    # Clean return from i3.main() means i3 went away (restart/exit). Non-zero so
    # systemd brings us back to re-subscribe.
    print("i3source: i3 connection closed — exiting for restart", file=sys.stderr, flush=True)
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
