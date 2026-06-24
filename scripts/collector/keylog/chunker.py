"""chunker — buffer keystrokes into sensible typing units before emitting.

Why chunk: emitting one spool event per keystroke is wasteful (the daemon would
ship thousands of one-char rows) and useless for analysis. Instead we accumulate
characters into a buffer and flush a single "typing" event when a natural
boundary is reached:

  * focus change   — typing moved to a different window/app (caller drives this)
  * Enter (\\n)     — a line / message / command was submitted
  * idle timeout   — N seconds without a keystroke (a pause = a unit)
  * max length     — guard so a single never-paused stream still bounds memory

BackSpace ("\\b") edits the buffer in place (pops the last char) so the captured
text reflects what was actually left standing, not raw keystrokes. This is the
deliberate chunking policy; full content, no redaction.

The chunker is pure + clock-injectable so it unit-tests without real time.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Chunk:
    text: str
    app: str
    title: str
    session: str
    workspace: str
    reason: str  # why it flushed: enter|idle|focus|maxlen|close


@dataclass
class Chunker:
    idle_seconds: float = 2.0
    max_chars: int = 4096
    _buf: list[str] = field(default_factory=list)
    _app: str = ""
    _title: str = ""
    _session: str = ""
    _workspace: str = ""
    _last_ts: float = 0.0

    def _ctx(self, reason: str) -> Chunk:
        return Chunk(
            text="".join(self._buf),
            app=self._app, title=self._title,
            session=self._session, workspace=self._workspace,
            reason=reason,
        )

    def _reset(self) -> None:
        self._buf = []

    def feed(self, char: str, *, app: str, title: str, session: str,
             workspace: str, now: float) -> list[Chunk]:
        """Feed one resolved character with its window context + timestamp.

        Returns a list of Chunks to emit (0, 1, or 2 — e.g. a focus change that
        flushes the prior buffer AND an immediate Enter would flush twice).
        """
        out: list[Chunk] = []

        # Idle flush: a gap since the last keystroke closes the current unit
        # BEFORE recording the new context, so the pause boundary is honored.
        if self._buf and (now - self._last_ts) >= self.idle_seconds:
            out.append(self._ctx("idle"))
            self._reset()

        # Focus change: typing moved to a different window. Flush what we had
        # under the OLD context, then adopt the new one.
        focus_changed = (
            self._buf
            and (app != self._app or session != self._session)
        )
        if focus_changed:
            out.append(self._ctx("focus"))
            self._reset()

        # Adopt the (possibly new) context for the buffer we are about to grow.
        self._app, self._title = app, title
        self._session, self._workspace = session, workspace
        self._last_ts = now

        # BackSpace edits the standing buffer.
        if char == "\b":
            if self._buf:
                self._buf.pop()
            return out

        self._buf.append(char)

        # Enter submits a unit (the newline is kept in the text).
        if char == "\n":
            out.append(self._ctx("enter"))
            self._reset()
            return out

        # Length guard.
        if len(self._buf) >= self.max_chars:
            out.append(self._ctx("maxlen"))
            self._reset()

        return out

    def flush_idle(self, now: float) -> list[Chunk]:
        """Called by the daemon's idle timer: flush if the buffer has aged out."""
        if self._buf and (now - self._last_ts) >= self.idle_seconds:
            c = self._ctx("idle")
            self._reset()
            return [c]
        return []

    def flush_now(self, reason: str = "close") -> list[Chunk]:
        """Force-flush whatever is buffered (e.g. on shutdown)."""
        if self._buf:
            c = self._ctx(reason)
            self._reset()
            return [c]
        return []
