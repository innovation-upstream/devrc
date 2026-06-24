"""winctx — active-window context (app / title / workspace) via Xlib.

Reads the EWMH `_NET_ACTIVE_WINDOW` from the root, then that window's WM_CLASS
(app) and `_NET_WM_NAME` (UTF-8 title), falling back to WM_NAME. i3 workspace is
read best-effort from `_NET_DESKTOP_NAMES` + `_NET_CURRENT_DESKTOP` (cheap; no i3
IPC dependency). All lookups are wrapped — a transient window race must never
crash the keylogger; we just return empty context.

Kept thin and dependency-light so the keylogger can import it; it needs a live
Xlib display so it is exercised in the on-host synthetic verification, not the
pure unit tests.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class WinCtx:
    app: str = ""        # WM_CLASS instance/class
    title: str = ""      # _NET_WM_NAME / WM_NAME
    window_id: str = ""   # active window id as a stable session key
    workspace: str = ""  # i3/EWMH desktop name


class WindowContext:
    def __init__(self, display):
        from Xlib import X  # noqa: F401  (import guarded behind a live display)

        self._d = display
        self._root = display.screen().root
        self._atom = display.intern_atom
        self._NET_ACTIVE_WINDOW = self._atom("_NET_ACTIVE_WINDOW")
        self._NET_WM_NAME = self._atom("_NET_WM_NAME")
        self._UTF8_STRING = self._atom("UTF8_STRING")
        self._NET_CURRENT_DESKTOP = self._atom("_NET_CURRENT_DESKTOP")
        self._NET_DESKTOP_NAMES = self._atom("_NET_DESKTOP_NAMES")

    def _prop(self, win, atom, prop_type):
        try:
            r = win.get_full_property(atom, prop_type)
            return r.value if r else None
        except Exception:
            return None

    def _active_window(self):
        from Xlib import X
        val = self._prop(self._root, self._NET_ACTIVE_WINDOW, X.AnyPropertyType)
        if val:
            try:
                wid = int(val[0])
                if wid:
                    return self._d.create_resource_object("window", wid), wid
            except Exception:
                pass
        # Fallback: input focus.
        try:
            f = self._d.get_input_focus().focus
            return f, getattr(f, "id", 0)
        except Exception:
            return None, 0

    def _title(self, win) -> str:
        v = self._prop(win, self._NET_WM_NAME, self._UTF8_STRING)
        if v:
            return _to_text(v)
        from Xlib import X
        v = self._prop(win, self._atom("WM_NAME"), X.AnyPropertyType)
        return _to_text(v) if v else ""

    def _wm_class(self, win) -> str:
        try:
            cls = win.get_wm_class()
            if cls:
                # (instance, class) — prefer the class name.
                return cls[1] or cls[0] or ""
        except Exception:
            pass
        return ""

    def _workspace(self) -> str:
        from Xlib import X
        cur = self._prop(self._root, self._NET_CURRENT_DESKTOP, X.AnyPropertyType)
        names = self._prop(self._root, self._NET_DESKTOP_NAMES, self._UTF8_STRING)
        if cur is None:
            return ""
        idx = int(cur[0])
        if names:
            parts = _to_text(names).split("\x00")
            parts = [p for p in parts if p != ""]
            if 0 <= idx < len(parts):
                return parts[idx]
        return str(idx)

    def current(self) -> WinCtx:
        win, wid = self._active_window()
        if win is None:
            return WinCtx()
        return WinCtx(
            app=self._wm_class(win),
            title=self._title(win),
            window_id=str(wid),
            workspace=self._workspace(),
        )


def _to_text(v) -> str:
    if isinstance(v, bytes):
        return v.decode("utf-8", "replace").rstrip("\x00")
    if isinstance(v, str):
        return v.rstrip("\x00")
    try:
        return bytes(v).decode("utf-8", "replace").rstrip("\x00")
    except Exception:
        return str(v)
