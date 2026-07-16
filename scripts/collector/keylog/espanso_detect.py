"""espanso_detect — detect true espanso usage from live keystrokes.

Because espanso erases both the trigger and the expansion (backspace + clipboard
paste) the ONLY place espanso usage is observable is the raw keystroke stream,
BEFORE espanso reacts. `EspansoDetector` watches that stream (fed per-key by the
keylogger, in parallel with the chunker) and emits an `EspansoEvent` the moment
a trigger completes — the deterministic, forward-only usage signal.

Two detection paths:

  1. DIRECT triggers (deterministic). A bounded recent-char ring mirrors what
     espanso itself sees. When the ring ENDS WITH a known trigger we emit and
     CLEAR the ring — exactly as espanso consumes the trigger on firing. Because
     we check after every char and clear on match, the trigger that completes
     FIRST wins: typing ":datetime" emits ":date" (mirrors espanso's prefix
     behaviour), and ":datetime" never forms. Clearing also means espanso's
     trailing backspaces are no-ops (no double-emit), while a genuinely retyped
     trigger fills the ring again and fires again.

  2. Ctrl+Space SEARCH UI (best-effort attribution). On the search shortcut the
     keylogger calls `feed_search_open`; subsequent chars accumulate as the
     search TERM (not the direct ring) until a close boundary (Enter, Escape,
     focus change, or idle). We fuzzy-attribute the term to a snippet; a unique
     match is attributed, zero/multiple → trigger=None, but the search-open +
     term are recorded regardless. Search events are always `inferred=True`.

Honest limitation: "ring ends with a trigger" ≈ "espanso fired", EXCEPT in
per-app espanso-disabled contexts (still far better than phrase-counting). The
detector NEVER raises out of `feed_*`; the crash-guard also lives at the call
site in keylog.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

_WORD_RE = re.compile(r"[a-z0-9]+")
# Chars that terminate the search term when in search mode.
_ENTER = "\n"
_ESCAPE = "\x1b"
_BACKSPACE = "\b"


@dataclass
class EspansoEvent:
    trigger: str | None      # the fired trigger, or None (search w/o unique match)
    method: str              # "direct" | "search"
    inferred: bool           # True for all search attributions
    search_term: str | None  # the typed search query (search method only)
    app: str
    session: str
    workspace: str = ""
    label: str = ""


class EspansoDetector:
    def __init__(self, trigger_set):
        self.ts = trigger_set
        # Ring long enough to hold the longest trigger; >=1 so it is never empty.
        self._maxlen = max(getattr(trigger_set, "max_len", 0), 1)
        self._ring: list[str] = []
        self._app = None
        self._session = None
        self._workspace = ""
        # search-mode state
        self._search = False
        self._search_term: list[str] = []
        self._last_ts = 0.0

    # -- direct-trigger + search feed ------------------------------------- #
    def feed_char(self, char, *, app, session, now, workspace="") -> list:
        """Feed one resolved character. Returns a list of EspansoEvents (0 or 1)."""
        out: list = []
        try:
            # Focus change: typing moved to another window. Flush an open search
            # under the OLD context, then reset the direct ring (no cross-window
            # false match).
            if self._app is not None and (app != self._app or session != self._session):
                if self._search:
                    ev = self._close_search("focus")
                    if ev is not None:
                        out.append(ev)
                self._ring.clear()
            self._app, self._session, self._workspace = app, session, workspace
            self._last_ts = now

            if self._search:
                self._feed_search_char(char, out)
                return out

            # -- direct ring --
            if char == _BACKSPACE:
                if self._ring:
                    self._ring.pop()
                return out
            self._ring.append(char)
            if len(self._ring) > self._maxlen:
                del self._ring[0:len(self._ring) - self._maxlen]
            ev = self._match_direct(app, session, now, workspace)
            if ev is not None:
                out.append(ev)
            return out
        except Exception:
            return out

    def feed_search_open(self, *, app, session, now, workspace="") -> None:
        """Enter search-mode (called when keylog sees the Ctrl+Space shortcut)."""
        try:
            self._app, self._session, self._workspace = app, session, workspace
            self._last_ts = now
            self._ring.clear()
            self._search = True
            self._search_term = []
        except Exception:
            pass

    def flush_idle(self, now, idle_seconds) -> list:
        """Close an idle, unterminated search (called by keylog's idle loop)."""
        out: list = []
        try:
            if self._search and (now - self._last_ts) >= idle_seconds:
                ev = self._close_search("idle")
                if ev is not None:
                    out.append(ev)
        except Exception:
            pass
        return out

    def flush_now(self) -> list:
        """Force-close an open search (e.g. on shutdown)."""
        out: list = []
        try:
            if self._search:
                ev = self._close_search("close")
                if ev is not None:
                    out.append(ev)
        except Exception:
            pass
        return out

    # -- internals -------------------------------------------------------- #
    def _feed_search_char(self, char, out: list) -> None:
        if char == _ENTER:
            ev = self._close_search("enter")
            if ev is not None:
                out.append(ev)
        elif char == _ESCAPE:
            ev = self._close_search("escape")
            if ev is not None:
                out.append(ev)
        elif char == _BACKSPACE:
            if self._search_term:
                self._search_term.pop()
        else:
            self._search_term.append(char)

    def _match_direct(self, app, session, now, workspace):
        if not self.ts.triggers:
            return None
        s = "".join(self._ring)
        # Emit the SHORTEST trigger the ring ends with (the one that completed
        # first / at the current char), mirroring espanso's prefix behaviour.
        best = None
        for trig in self.ts.triggers:
            if trig and s.endswith(trig) and (best is None or len(trig) < len(best)):
                best = trig
        if best is None:
            return None
        # espanso consumes the trigger on firing → clear so trailing backspaces
        # are no-ops and a longer overlapping trigger cannot also match.
        self._ring.clear()
        label = (self.ts.meta.get(best) or {}).get("label", "") or ""
        return EspansoEvent(
            trigger=best, method="direct", inferred=False, search_term=None,
            app=app, session=session, workspace=workspace, label=label,
        )

    def _close_search(self, reason):
        term = "".join(self._search_term)
        self._search = False
        self._search_term = []
        trigger = self._attribute(term)
        label = (self.ts.meta.get(trigger) or {}).get("label", "") if trigger else ""
        return EspansoEvent(
            trigger=trigger, method="search", inferred=True, search_term=term,
            app=self._app or "", session=self._session or "",
            workspace=self._workspace, label=label or "",
        )

    def _attribute(self, term):
        """Fuzzy-attribute a search term to exactly one snippet, else None."""
        t = (term or "").strip().lower()
        if not t:
            return None
        matched = [trig for trig in self.ts.triggers if self._term_matches(t, trig)]
        return matched[0] if len(matched) == 1 else None

    def _term_matches(self, term, trig):
        meta = self.ts.meta.get(trig) or {}
        if term in trig.lower():
            return True
        label = (meta.get("label") or "").lower()
        for w in _WORD_RE.findall(label):
            if term in w:
                return True
        for st in meta.get("search_terms") or []:
            if isinstance(st, str) and term in st.lower():
                return True
        return False
