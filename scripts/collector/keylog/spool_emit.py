"""spool_emit — build + append a v1 spool line, byte-compatible with `emit`.

Both GUI collectors (keylogger, browser receiver) write directly to the spool
in Python rather than shelling out to the `emit` bash helper per event (these
are not the latency-critical shell hot path, and a per-event fork is wasteful).
This module reproduces the EXACT v1 line contract so the existing collector
daemon (`collector.parse_line`) ships the records unchanged:

    v1<TAB>ts=<...><TAB>source=<s><TAB>kind=<k><TAB>b64:text=<b64>...

  * Free-text fields go through `b64:<key>=` base64 (no-wrap) so arbitrary bytes
    (quotes, newlines, tabs, unicode, "passwords") survive intact.
  * Plain scalar keys (ts, source, kind, host, integers) are written verbatim.
  * `ts` (ClickHouse DateTime64(3) local format) and `host` are auto-filled if
    the caller omits them — mirroring the bash `emit`.
  * Append is a single newline-terminated `write`, opened O_APPEND, so
    concurrent writers do not interleave for sub-PIPE_BUF lines.
"""
from __future__ import annotations

import base64
import datetime
import os
import socket
from pathlib import Path

CURRENT_NAME = "current.log"
# Keys written as plain scalars; everything else free-text is base64-encoded.
_PLAIN_KEYS = {"ts", "host", "source", "kind", "duration_ms", "exit_code"}


def default_spool_dir() -> Path:
    """Resolve the spool dir the same way emit/collector do."""
    env = os.environ.get("ACTIVITY_SPOOL_DIR")
    if env:
        return Path(env)
    state = os.environ.get("XDG_STATE_HOME") or (Path.home() / ".local/state")
    return Path(state) / "activity" / "spool"


def _ts_now() -> str:
    # %3N has no portable strftime equivalent; build milliseconds explicitly.
    now = datetime.datetime.now()
    return now.strftime("%Y-%m-%d %H:%M:%S.") + f"{now.microsecond // 1000:03d}"


def build_line(fields: dict[str, object]) -> str:
    """Build a v1 spool line from a field dict.

    Plain-scalar keys (_PLAIN_KEYS) are emitted verbatim; every other key is
    treated as free text and base64-encoded under a `b64:` prefix. `ts`/`host`
    are auto-filled when absent.
    """
    parts = ["v1"]
    have_ts = "ts" in fields
    have_host = "host" in fields
    for key, val in fields.items():
        if key in _PLAIN_KEYS:
            parts.append(f"{key}={val}")
        else:
            enc = base64.b64encode(str(val).encode("utf-8")).decode("ascii")
            parts.append(f"b64:{key}={enc}")
    if not have_ts:
        parts.append(f"ts={_ts_now()}")
    if not have_host:
        parts.append(f"host={os.environ.get('HOST') or socket.gethostname()}")
    return "\t".join(parts)


def emit(fields: dict[str, object], spool_dir: Path | None = None) -> str:
    """Append one event to <spool_dir>/current.log. Returns the written line.

    Best-effort + bounded: telemetry must never crash its caller, so spool I/O
    errors are swallowed (the line is still returned for tests).
    """
    line = build_line(fields)
    d = spool_dir or default_spool_dir()
    try:
        d.mkdir(parents=True, exist_ok=True)
        # O_APPEND single write == atomic for sub-PIPE_BUF lines.
        with open(d / CURRENT_NAME, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass
    return line
