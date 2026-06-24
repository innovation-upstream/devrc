#!/usr/bin/env python3
"""refsources — independent reference readers for cross-source reconciliation.

Each function reads an EXISTING, independent record of activity that the
collector pipeline does NOT produce, so reconcile.py can diff the collected
`activity.events` against ground truth the collector never touched:

  * zsh history   (~/.zsh_history)           ↔ source=zsh
  * Chrome/Brave  (…/Default/History sqlite) ↔ source=browser
  * tmux          (~/.tmux/tasks/*.json,
                   ~/.tmux/activity/*)        ↔ source=tmux
  * Claude        (~/.claude/projects/**/*.jsonl) ↔ source=claude

Design rules:
  * Pure parsing functions take an explicit path/handle so they are unit-tested
    against fixtures with NO real home dir.
  * Every reader is robust to a missing/empty source: it returns an empty list,
    and reconcile.py reports "no data, skipped" rather than failing.
  * The Chrome History DB is usually LOCKED by the running browser — callers
    copy it to a temp file first (copy_locked_sqlite) and read the copy.
"""
from __future__ import annotations

import json
import re
import shutil
import sqlite3
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path


# --------------------------------------------------------------------------- #
# zsh history
# --------------------------------------------------------------------------- #
# Extended-history line:  ": <epoch>:<elapsed>;<command>"
# Plain line:             "<command>"
_EXT_RE = re.compile(r"^: (\d+):(\d+);(.*)$", re.DOTALL)


def parse_zsh_history(text: str) -> list[dict]:
    """Parse zsh history text → [{ts: float|None, command: str}, ...].

    Handles BOTH the extended format (`: <epoch>:<elapsed>;cmd`) and the plain
    format (bare command per line). Multi-line commands in extended history are
    continued with a trailing backslash; we join those. Plain format has no
    timestamps, so ts is None (reconcile then matches on command text only).
    """
    out: list[dict] = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        m = _EXT_RE.match(line)
        if m:
            ts = float(m.group(1))
            cmd = m.group(3)
            # Continuation: extended-history commands ending in backslash wrap.
            while cmd.endswith("\\") and i + 1 < len(lines):
                i += 1
                cmd = cmd[:-1] + "\n" + lines[i]
            out.append({"ts": ts, "command": cmd})
        else:
            if line.strip() != "":
                out.append({"ts": None, "command": line})
        i += 1
    return out


def read_zsh_history(path: Path) -> list[dict]:
    """Read + parse the zsh histfile; [] if absent."""
    p = Path(path)
    if not p.exists():
        return []
    return parse_zsh_history(p.read_text(encoding="utf-8", errors="replace"))


# --------------------------------------------------------------------------- #
# Chrome / Chromium / Brave history (sqlite)
# --------------------------------------------------------------------------- #
# Chrome stores visit times as microseconds since 1601-01-01 (Windows epoch).
_CHROME_EPOCH = datetime(1601, 1, 1, tzinfo=timezone.utc)


def chrome_time_to_dt(chrome_micros: int) -> datetime:
    """Convert a Chrome WebKit timestamp (µs since 1601) → aware UTC datetime."""
    return _CHROME_EPOCH + timedelta(microseconds=int(chrome_micros))


def copy_locked_sqlite(src: Path) -> Path:
    """Copy a (possibly locked) sqlite DB to a temp file; return the copy path."""
    src = Path(src)
    fd = tempfile.NamedTemporaryFile(prefix="refhist-", suffix=".sqlite", delete=False)
    fd.close()
    shutil.copy2(src, fd.name)
    return Path(fd.name)


def read_chrome_history(db_path: Path, since_epoch: float | None = None,
                        copy_first: bool = True) -> list[dict]:
    """Read browser visits → [{ts: float(epoch_s), url: str, title: str}, ...].

    Reads a COPY by default (the live DB is usually locked). `since_epoch`
    filters to visits at/after that unix time. [] if the DB is absent.
    """
    db = Path(db_path)
    if not db.exists():
        return []
    read_path = copy_locked_sqlite(db) if copy_first else db
    try:
        return _query_chrome(read_path, since_epoch)
    finally:
        if copy_first:
            try:
                Path(read_path).unlink()
            except OSError:
                pass


def _query_chrome(read_path: Path, since_epoch: float | None) -> list[dict]:
    con = sqlite3.connect(f"file:{read_path}?mode=ro", uri=True)
    try:
        cur = con.cursor()
        cur.execute(
            "SELECT v.visit_time, u.url, u.title "
            "FROM visits v JOIN urls u ON u.id = v.url "
            "ORDER BY v.visit_time"
        )
        out = []
        for visit_time, url, title in cur.fetchall():
            dt = chrome_time_to_dt(visit_time)
            ep = dt.timestamp()
            if since_epoch is not None and ep < since_epoch:
                continue
            out.append({"ts": ep, "url": url or "", "title": title or ""})
        return out
    finally:
        con.close()


# --------------------------------------------------------------------------- #
# tmux task / activity files
# --------------------------------------------------------------------------- #
def read_tmux_tasks(tasks_dir: Path) -> list[dict]:
    """Read ~/.tmux/tasks/*.json → list of task dicts (robust to bad json)."""
    d = Path(tasks_dir)
    if not d.exists():
        return []
    out = []
    for p in sorted(d.glob("*.json")):
        try:
            obj = json.loads(p.read_text(encoding="utf-8", errors="replace"))
        except (ValueError, OSError):
            continue
        if isinstance(obj, dict):
            out.append(obj)
    return out


def read_tmux_activity(activity_dir: Path) -> list[dict]:
    """Read ~/.tmux/activity/* → [{window: str, last_activity: float}, ...].

    Each file is named by window id and contains a unix timestamp (the window's
    last-activity time). Robust to non-numeric/empty files.
    """
    d = Path(activity_dir)
    if not d.exists():
        return []
    out = []
    for p in sorted(d.iterdir()):
        if not p.is_file():
            continue
        raw = p.read_text(encoding="utf-8", errors="replace").strip().split()
        if not raw:
            continue
        try:
            ts = float(raw[0])
        except ValueError:
            continue
        out.append({"window": p.name, "last_activity": ts})
    return out


# --------------------------------------------------------------------------- #
# Claude session transcripts
# --------------------------------------------------------------------------- #
def _parse_iso_z(s: str) -> float | None:
    """Parse a Claude ISO-8601 'Z' timestamp → unix epoch seconds (UTC)."""
    if not s:
        return None
    try:
        s2 = s.replace("Z", "+00:00")
        return datetime.fromisoformat(s2).timestamp()
    except ValueError:
        return None


def parse_claude_jsonl(text: str) -> list[dict]:
    """Parse one Claude transcript's text → list of USER message records.

    Returns [{ts: float|None, session: str, cwd: str}, ...] for every line whose
    type == 'user' and whose message is an actual user turn (not a tool result).
    Tool-result echoes also arrive as type=user with a content list containing
    tool_result blocks — those are excluded so the count matches real prompts.
    """
    out: list[dict] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if obj.get("type") != "user":
            continue
        if _is_tool_result(obj):
            continue
        out.append({
            "ts": _parse_iso_z(obj.get("timestamp", "")),
            "session": obj.get("sessionId", ""),
            "cwd": obj.get("cwd", ""),
        })
    return out


def _is_tool_result(obj: dict) -> bool:
    """True if this type=user line is a tool-result echo, not a human prompt."""
    msg = obj.get("message")
    if not isinstance(msg, dict):
        return False
    content = msg.get("content")
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_result":
                return True
    return False


def count_claude_user_msgs(jsonl_paths: list[Path],
                           since_epoch: float | None = None) -> int:
    """Count human user messages across transcripts, optionally since a time."""
    total = 0
    for p in jsonl_paths:
        try:
            text = Path(p).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for rec in parse_claude_jsonl(text):
            if since_epoch is not None and (rec["ts"] is None or rec["ts"] < since_epoch):
                continue
            total += 1
    return total
