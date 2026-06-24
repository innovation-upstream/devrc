#!/usr/bin/env python3
"""keylog — X11 full-content keystroke collector for the activity pipeline.

Captures ALL KeyPress events globally via the X11 RECORD extension (python-xlib),
WITHOUT needing root or the `input` group — it works at the X-protocol level as
the logged-in user. Each key is mapped keycode → keysym → character honoring
modifier state (Shift / CapsLock / AltGr), buffered into typing units by
`chunker`, annotated with active-window context (WM_CLASS / _NET_WM_NAME /
workspace), and emitted into the local spool in the v1 contract so the existing
collector daemon ships it unchanged.

FULL CONTENT, NO REDACTION — this is an explicit self-instrumentation choice by
the machine owner. Records must NOT leave the local spool until an authenticated
ClickHouse is in place.

Architecture
------------
RECORD runs on a SECOND display connection (the "record" connection) which is
blocked inside `record.enable_context`; a separate "local" connection serves
keymap + window-context queries (you cannot issue normal requests on the record
connection while it is recording). A wall-clock idle flush is achieved by
enabling the context in a background thread and polling an idle timer on the
main thread.

Config (env)
------------
  ACTIVITY_SPOOL_DIR      spool dir (default ~/.local/state/activity/spool)
  KEYLOG_IDLE_SECONDS     idle gap that closes a typing unit (default 2.0)
  KEYLOG_MAX_CHARS        hard cap per unit (default 4096)
  KEYLOG_POLL_SECONDS     idle-timer poll interval (default 0.5)
"""
from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path

# Sibling modules (this dir is not a package).
sys.path.insert(0, str(Path(__file__).resolve().parent))
import keymap as KM        # noqa: E402
import spool_emit as SE    # noqa: E402
from chunker import Chunker  # noqa: E402
from winctx import WindowContext, WinCtx  # noqa: E402

SOURCE = "keys"
KIND = "typing"


def _emit_chunk(chunk, spool_dir: Path) -> str:
    """Map a Chunker.Chunk → the v1 emit fields and append to the spool."""
    import json
    payload = json.dumps(
        {"title": chunk.title, "workspace": chunk.workspace, "flush": chunk.reason},
        ensure_ascii=False, separators=(",", ":"),
    )
    fields = {
        "source": SOURCE,
        "kind": KIND,
        "text": chunk.text,
        "app": chunk.app,
        "project": "",          # cwd/project not available from an X key event
        "session": chunk.session,
        "payload": payload,
    }
    return SE.emit(fields, spool_dir=spool_dir)


class KeyLogger:
    def __init__(self, spool_dir: Path, idle_seconds: float, max_chars: int):
        self.spool_dir = spool_dir
        self.chunker = Chunker(idle_seconds=idle_seconds, max_chars=max_chars)
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._ctx = None

        from Xlib import display
        from Xlib.ext import record  # noqa: F401  (presence check)

        # Two connections: one drives RECORD (blocks), one answers queries.
        self.local_dpy = display.Display()
        self.record_dpy = display.Display()
        if not self.record_dpy.has_extension("RECORD"):
            raise RuntimeError("X server lacks the RECORD extension")
        self.winctx = WindowContext(self.local_dpy)

    # -- keysym/char resolution on the LOCAL connection ------------------- #
    def _keysyms(self, keycode: int, group: int):
        """Return (lower, upper) keysyms for a keycode's active group.

        python-xlib caches the keymap at connect time; tools like xdotool remap
        a keycode on the fly (XChangeKeyboardMapping) to inject unicode, so a
        cached lookup returns 0 for the freshly-remapped code. On a 0 result we
        do a fresh, uncached `get_keyboard_mapping` for that single keycode and
        also refresh the local cache so subsequent lookups are fast.
        """
        lower = self.local_dpy.keycode_to_keysym(keycode, group * 2 + 0)
        upper = self.local_dpy.keycode_to_keysym(keycode, group * 2 + 1)
        if lower == 0 and upper == 0:
            try:
                # Uncached server query for just this keycode — catches keycodes
                # remapped after our connection cached the keymap.
                km = self.local_dpy.get_keyboard_mapping(keycode, 1)
                if km and len(km[0]) > group * 2 + 1:
                    row = km[0]
                    lower = row[group * 2 + 0]
                    upper = row[group * 2 + 1]
            except Exception:
                pass
        return lower, upper

    def _char_for(self, keycode: int, state: int) -> str | None:
        group = KM.group_index(state)
        lower, upper = self._keysyms(keycode, group)
        if upper == 0:
            upper = lower
        return KM.resolve_char(lower, upper, state)

    # -- RECORD callback -------------------------------------------------- #
    def _handle(self, reply):
        from Xlib import X
        from Xlib.ext import record
        from Xlib.protocol import rq
        if reply.category != record.FromServer:
            return
        if reply.client_swapped or not reply.data or reply.data[0] < 2:
            return
        data = reply.data
        while len(data):
            event, data = rq.EventField(None).parse_binary_value(
                data, self.record_dpy.display, None, None
            )
            if event.type != X.KeyPress:
                continue
            char = self._char_for(event.detail, event.state)
            if char is None:
                continue
            ctx = self._safe_ctx()
            now = time.time()
            with self._lock:
                chunks = self.chunker.feed(
                    char, app=ctx.app, title=ctx.title,
                    session=ctx.window_id, workspace=ctx.workspace, now=now,
                )
            for ch in chunks:
                _emit_chunk(ch, self.spool_dir)

    def _safe_ctx(self) -> WinCtx:
        try:
            return self.winctx.current()
        except Exception:
            return WinCtx()

    # -- idle flusher (wall clock) --------------------------------------- #
    def _idle_loop(self, poll: float):
        while not self._stop.wait(poll):
            now = time.time()
            with self._lock:
                chunks = self.chunker.flush_idle(now)
            for ch in chunks:
                _emit_chunk(ch, self.spool_dir)

    def run(self, poll_seconds: float = 0.5):
        from Xlib import X
        from Xlib.ext import record

        ctx = self.record_dpy.record_create_context(
            0,
            [record.AllClients],
            [{
                "core_requests": (0, 0),
                "core_replies": (0, 0),
                "ext_requests": (0, 0, 0, 0),
                "ext_replies": (0, 0, 0, 0),
                "delivered_events": (0, 0),
                "device_events": (X.KeyPress, X.KeyRelease),
                "errors": (0, 0),
                "client_started": False,
                "client_died": False,
            }],
        )
        self._ctx = ctx
        idle = threading.Thread(target=self._idle_loop, args=(poll_seconds,), daemon=True)
        idle.start()
        try:
            # Blocks here, dispatching to _handle, until disable_context.
            self.record_dpy.record_enable_context(ctx, self._handle)
        finally:
            self._stop.set()
            try:
                self.record_dpy.record_free_context(ctx)
            except Exception:
                pass
            # Flush whatever was buffered at shutdown.
            with self._lock:
                for ch in self.chunker.flush_now():
                    _emit_chunk(ch, self.spool_dir)

    def stop(self):
        # record_enable_context blocks on the record connection; disabling must
        # be issued from a DIFFERENT connection. The local connection serves.
        self._stop.set()
        try:
            if self._ctx is not None:
                self.local_dpy.record_disable_context(self._ctx)
                self.local_dpy.flush()
        except Exception:
            pass


def main(argv=None) -> int:
    spool_dir = SE.default_spool_dir()
    idle_seconds = float(os.environ.get("KEYLOG_IDLE_SECONDS", "2.0"))
    max_chars = int(os.environ.get("KEYLOG_MAX_CHARS", "4096"))
    poll = float(os.environ.get("KEYLOG_POLL_SECONDS", "0.5"))

    logger = KeyLogger(spool_dir, idle_seconds, max_chars)
    print(
        f"keylog: spool={spool_dir} idle={idle_seconds}s max_chars={max_chars}",
        file=sys.stderr, flush=True,
    )
    try:
        logger.run(poll_seconds=poll)
    except KeyboardInterrupt:
        logger.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
