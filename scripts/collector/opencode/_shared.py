#!/usr/bin/env python3
"""Shared, dependency-free helpers for the OpenCode activity source.

Reads OpenCode's Drizzle-ORM SQLite database at ~/.local/share/opencode/ and
yields normalized session/message/part dicts. Emits v1 spool lines via
keylog.spool_emit for the collector daemon to ship.

Design goals:
  * Read-only on the OpenCode DB — never write or lock.
  * Tolerate missing DB or schema drift — return empty iterators, not errors.
  * Stdlib only — no third-party imports.
"""
from __future__ import annotations

import datetime as _dt
import glob
import json
import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator


# --------------------------------------------------------------------------- #
# OpenCode SQLite schema constants (Drizzle ORM)
# Column names are snake_case in the actual DB.
# --------------------------------------------------------------------------- #
SESSION_TABLE = "session"
MESSAGE_TABLE = "message"
PART_TABLE = "part"
PROJECT_TABLE = "project"


# --------------------------------------------------------------------------- #
# Path resolution
# --------------------------------------------------------------------------- #
def opencode_data_dir() -> Path:
    """Return the OpenCode data directory (~/.local/share/opencode/)."""
    env = os.environ.get("OPENCODE_DATA_DIR")
    if env:
        return Path(env)
    return Path.home() / ".local" / "share" / "opencode"


def opencode_db_path() -> Path | None:
    """Find the OpenCode SQLite database file.

    Known locations (checked in order):
      1. $OPENCODE_DB_PATH (explicit override)
      2. ~/.local/share/opencode/opencode-stable.db
      3. Any *.db or *.sqlite under ~/.local/share/opencode/
    Returns None if no DB is found.
    """
    explicit = os.environ.get("OPENCODE_DB_PATH")
    if explicit:
        p = Path(explicit)
        return p if p.exists() else None

    data_dir = opencode_data_dir()
    stable = data_dir / "opencode-stable.db"
    if stable.exists():
        return stable

    # Fallback: glob for any db file under the data dir
    for pattern in ("*.db", "*.sqlite", "*.sqlite3"):
        hits = sorted(data_dir.glob(pattern))
        if hits:
            return hits[0]
    return None


# --------------------------------------------------------------------------- #
# SQLite connection
# --------------------------------------------------------------------------- #
def get_db(path: Path | None = None) -> sqlite3.Connection | None:
    """Open the OpenCode SQLite DB as readonly. Returns None if not found."""
    db = path or opencode_db_path()
    if db is None or not db.exists():
        return None
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


# --------------------------------------------------------------------------- #
# Iterators
# --------------------------------------------------------------------------- #
def _parse_json_field(val: str | None) -> dict | None:
    """Safely parse a JSON string, returning None on failure."""
    if not val:
        return None
    try:
        obj = json.loads(val)
        return obj if isinstance(obj, dict) else None
    except (json.JSONDecodeError, TypeError):
        return None


def iter_sessions(
    db: sqlite3.Connection,
    since_ts: float | None = None,
) -> Iterator[dict]:
    """Yield normalized session dicts from the OpenCode DB.

    Args:
        db: Readonly sqlite3 connection with Row factory.
        since_ts: If provided, filter to sessions with time_created > since_ts
                   (epoch milliseconds).
    """
    sql = f"SELECT * FROM {SESSION_TABLE}"
    params: list = []
    if since_ts is not None:
        sql += " WHERE time_created > ?"
        params.append(int(since_ts))
    sql += " ORDER BY time_created"

    try:
        cur = db.execute(sql, params)
    except sqlite3.OperationalError:
        return

    for row in cur:
        model_obj = _parse_json_field(row["model"])
        tokens_obj = {
            "input": row["tokens_input"],
            "output": row["tokens_output"],
            "reasoning": row["tokens_reasoning"],
            "cache": {
                "read": row["tokens_cache_read"],
                "write": row["tokens_cache_write"],
            },
        }
        yield {
            "id": row["id"],
            "project_id": row["project_id"],
            "directory": row["directory"],
            "title": row["title"],
            "agent": row["agent"],
            "model": model_obj,
            "version": row["version"],
            "cost": row["cost"],
            "tokens": tokens_obj,
            "time_created": row["time_created"],
            "time_updated": row["time_updated"],
            "parent_id": row["parent_id"],
        }


def iter_messages(db: sqlite3.Connection, session_id: str) -> Iterator[dict]:
    """Yield normalized message dicts for a session.

    The `data` column is a JSON blob; we extract role, time, agent, model.
    """
    sql = (
        f"SELECT * FROM {MESSAGE_TABLE} WHERE session_id = ? ORDER BY time_created"
    )
    try:
        cur = db.execute(sql, (session_id,))
    except sqlite3.OperationalError:
        return

    for row in cur:
        data = _parse_json_field(row["data"]) or {}
        time_obj = data.get("time") or {}
        yield {
            "id": row["id"],
            "session_id": row["session_id"],
            "role": data.get("role"),
            "time_created": row["time_created"],
            "time_updated": row["time_updated"],
            "time": time_obj,
            "agent": data.get("agent"),
            "model": data.get("model"),
            "cost": data.get("cost"),
            "tokens": data.get("tokens"),
        }


def iter_parts(db: sqlite3.Connection, message_id: str) -> Iterator[dict]:
    """Yield normalized part dicts for a message.

    The `data` column is a JSON blob; we extract type, text, tool, etc.
    """
    sql = f"SELECT * FROM {PART_TABLE} WHERE message_id = ? ORDER BY time_created"
    try:
        cur = db.execute(sql, (message_id,))
    except sqlite3.OperationalError:
        return

    for row in cur:
        data = _parse_json_field(row["data"]) or {}
        yield {
            "id": row["id"],
            "message_id": row["message_id"],
            "session_id": row["session_id"],
            "time_created": row["time_created"],
            "time_updated": row["time_updated"],
            "type": data.get("type"),
            "text": data.get("text"),
            "tool": data.get("tool"),
            "call_id": data.get("callID"),
            "state": data.get("state"),
            "reason": data.get("reason"),
            "tokens": data.get("tokens"),
            "cost": data.get("cost"),
            "_data": data,
        }


# --------------------------------------------------------------------------- #
# Timestamp conversion
# --------------------------------------------------------------------------- #
def to_ch_ts(epoch_ms: int) -> str:
    """Convert Unix epoch milliseconds to ClickHouse DateTime64(3) string."""
    dt = _dt.datetime.fromtimestamp(epoch_ms / 1000, tz=_dt.timezone.utc)
    return dt.strftime("%Y-%m-%d %H:%M:%S.") + f"{dt.microsecond // 1000:03d}"


def to_ch_ts_from_iso(iso_str: str) -> str | None:
    """Parse ISO8601 string → ClickHouse DateTime64(3) timestamp string."""
    if not iso_str:
        return None
    s = iso_str.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = _dt.datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(_dt.timezone.utc)
    return dt.strftime("%Y-%m-%d %H:%M:%S.") + f"{dt.microsecond // 1000:03d}"


# --------------------------------------------------------------------------- #
# Project / helpers
# --------------------------------------------------------------------------- #
def project_basename(directory: str | None) -> str:
    """Extract repo name from a path (reuses claude/_shared pattern)."""
    if not directory:
        return ""
    return os.path.basename(directory.rstrip("/"))


# --------------------------------------------------------------------------- #
# Spool bridge
# --------------------------------------------------------------------------- #
def spool_emit(fields: dict, spool_dir: Path | None = None) -> str:
    """Emit fields into the v1 spool via keylog.spool_emit."""
    import importlib
    try:
        mod = importlib.import_module("keylog.spool_emit")
    except ModuleNotFoundError:
        # Fallback: direct import when keylog is on sys.path as a directory
        import sys
        # NOTE: do NOT .resolve() — nix store symlinks resolve INTO the
        # nix store, losing the ~/.config/activity-collector/ prefix.
        # __file__ is always absolute in Python 3, so parent traversal works.
        keylog_dir = str(Path(__file__).parent.parent / "keylog")
        if keylog_dir not in sys.path:
            sys.path.insert(0, keylog_dir)
        mod = importlib.import_module("spool_emit")
    return mod.emit(fields, spool_dir=spool_dir)
