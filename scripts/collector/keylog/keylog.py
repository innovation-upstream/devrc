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
from espanso_detect import EspansoDetector  # noqa: E402
from espanso_triggers import (  # noqa: E402
    TriggerSet, load_triggers, standard_config_paths,
)

SOURCE = "keys"
KIND = "typing"
KIND_ESPANSO = "espanso"

# X ControlMask (1<<2); espanso's search shortcut is Ctrl+Space on this host.
CONTROL_MASK = 0x04
XK_ESCAPE = 0xFF1B

# Caret-navigation / editing keysyms. These reposition the caret or delete
# forward, breaking contiguously-typed text — espanso resets its buffer on
# them, so the detector's direct ring must reset too (else ":da" → arrow →
# "te" would assemble a ":date" that was never typed contiguously). They carry
# no printable char, so we detect them from the base keysym BEFORE the
# char-is-None drop. NOTE: mouse-click caret repositioning is NOT captured
# (device_events are KeyPress/KeyRelease only) → a documented residual
# false-positive vector.
NAV_KEYSYMS = frozenset({
    0xFF50,                          # Home
    0xFF51, 0xFF52, 0xFF53, 0xFF54,  # Left / Up / Right / Down
    0xFF55,                          # Prior (PageUp)
    0xFF56,                          # Next  (PageDown)
    0xFF57,                          # End
    0xFFFF,                          # Delete
})


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


def _emit_espanso(ev, spool_dir: Path) -> str:
    """Map an EspansoEvent → the v1 emit fields and append to the spool.

    The trigger goes in `text` (base64'd like typing); method / inferred /
    search_term / label / workspace ride in the JSON `payload`. `kind=espanso`
    is a plain scalar so the report can filter on it cheaply.
    """
    import json
    payload = json.dumps(
        {
            "method": ev.method,
            "inferred": ev.inferred,
            "search_term": ev.search_term,
            "label": ev.label,
            "workspace": ev.workspace,
        },
        ensure_ascii=False, separators=(",", ":"),
    )
    fields = {
        "source": SOURCE,
        "kind": KIND_ESPANSO,
        "text": ev.trigger or "",
        "app": ev.app,
        "project": "",
        "session": ev.session,
        "payload": payload,
    }
    return SE.emit(fields, spool_dir=spool_dir)


class KeyLogger:
    def __init__(self, spool_dir: Path, idle_seconds: float, max_chars: int):
        self.spool_dir = spool_dir
        self.chunker = Chunker(idle_seconds=idle_seconds, max_chars=max_chars)
        # Espanso usage detector (forward-only, deterministic). A missing or
        # unparseable espanso config yields an empty trigger set → the detector
        # is inert and typing capture behaves exactly as before.
        try:
            base_p, default_p = standard_config_paths()
            self.detector = EspansoDetector(load_triggers(base_p, default_p))
        except Exception:
            self.detector = EspansoDetector(TriggerSet())
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
            ctx = self._safe_ctx()
            now = time.time()

            # -- espanso: search-shortcut / Escape (keysym-level, fully guarded).
            # A detector bug must NEVER kill keystroke capture, so every detector
            # call here is wrapped in try/except.
            try:
                base_ks = self.local_dpy.keycode_to_keysym(event.detail, 0)
            except Exception:
                base_ks = 0
            try:
                ctrl_req, sc_keysym = self.detector.ts.search_shortcut
                is_shortcut = (
                    base_ks == sc_keysym
                    and (not ctrl_req or bool(event.state & CONTROL_MASK))
                )
            except Exception:
                is_shortcut = False
            if is_shortcut:
                # Ctrl+Space opens the espanso search UI — enter search-mode and
                # do NOT also feed this as a normal char.
                try:
                    with self._lock:
                        self.detector.feed_search_open(
                            app=ctx.app, session=ctx.window_id,
                            now=now, workspace=ctx.workspace,
                        )
                except Exception:
                    pass
                continue
            if base_ks in NAV_KEYSYMS:
                # Caret moved / forward-delete → reset the direct ring so a
                # trigger split by the navigation cannot assemble. Search-mode
                # is left as-is. Guarded like every other detector call; these
                # keys carry no char so they drop below and never reach the
                # chunker (chunker behaviour is byte-for-byte unchanged).
                try:
                    with self._lock:
                        self.detector.notify_navigation()
                except Exception:
                    pass
            if char is None:
                # Escape closes an open espanso search; otherwise it is a
                # non-printing key the chunker already ignores.
                if base_ks == XK_ESCAPE:
                    try:
                        with self._lock:
                            evs = self.detector.feed_char(
                                "\x1b", app=ctx.app, session=ctx.window_id,
                                now=now, workspace=ctx.workspace,
                            )
                    except Exception:
                        evs = []
                    for ev in evs:
                        _emit_espanso(ev, self.spool_dir)
                continue

            with self._lock:
                chunks = self.chunker.feed(
                    char, app=ctx.app, title=ctx.title,
                    session=ctx.window_id, workspace=ctx.workspace, now=now,
                )
            for ch in chunks:
                _emit_chunk(ch, self.spool_dir)

            # Feed the detector in parallel with the chunker (guarded).
            try:
                with self._lock:
                    evs = self.detector.feed_char(
                        char, app=ctx.app, session=ctx.window_id,
                        now=now, workspace=ctx.workspace,
                    )
            except Exception:
                evs = []
            for ev in evs:
                _emit_espanso(ev, self.spool_dir)

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
            # Close an idle, unterminated espanso search (guarded).
            try:
                with self._lock:
                    evs = self.detector.flush_idle(now, self.chunker.idle_seconds)
            except Exception:
                evs = []
            for ev in evs:
                _emit_espanso(ev, self.spool_dir)

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
            try:
                with self._lock:
                    evs = self.detector.flush_now()
            except Exception:
                evs = []
            for ev in evs:
                _emit_espanso(ev, self.spool_dir)

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
