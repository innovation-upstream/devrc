#!/usr/bin/env python3
"""Shared, dependency-free helpers for the two Claude-transcript activity sources.

Two sibling sources tail the SAME `~/.claude/projects/**/*.jsonl` transcripts and
emit into the SAME spool via the SAME `emit` helper, so their parsing/emit/ts
plumbing must be byte-identical:

  * tailer.py         — the MESSAGE STREAM (kind=prompt|command): one event per
                        genuine user-typed message / slash-command.
  * session-tailer.py — LAYER A session rollups (kind=session-summary): one event
                        per session with deterministic tool/token/lang/git counts.

This module holds ONLY the pieces both need identically (ts conversion, project
basename, emit-binary resolution, projects-root discovery, transcript iteration
with the subagent/`wf_` skip). Behaviour is lifted verbatim from tailer.py so the
message stream is unchanged. NO LLM, NO network, stdlib only.
"""
from __future__ import annotations

import datetime as _dt
import glob
import os
from pathlib import Path


# --------------------------------------------------------------------------- #
# Timestamp conversion: ISO8601 (…Z) -> ClickHouse DateTime64(3) UTC string
# --------------------------------------------------------------------------- #
def to_ch_ts(iso: str | None) -> str | None:
    """Convert a transcript ISO timestamp to emit's '%Y-%m-%d %H:%M:%S.%3N' UTC
    format (matches emit's `date -u`). Returns None if it cannot be parsed
    (caller then lets emit auto-fill)."""
    if not iso:
        return None
    s = iso.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = _dt.datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(_dt.timezone.utc)  # normalize to the UTC instant
    return dt.strftime("%Y-%m-%d %H:%M:%S.") + f"{dt.microsecond // 1000:03d}"


def ch_ts_to_epoch(s: str | None) -> float | None:
    """Parse a ClickHouse/emit 'YYYY-MM-DD HH:MM:SS[.fff]' UTC string to epoch.

    The stored `ts` is a tz-less DateTime64 holding the UTC instant, so we parse
    it as UTC (mirrors initiative-scan.ch_ts_to_epoch / the pipeline's ts-is-UTC
    contract)."""
    if s is None:
        return None
    txt = str(s).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
        try:
            return _dt.datetime.strptime(txt, fmt).replace(
                tzinfo=_dt.timezone.utc).timestamp()
        except ValueError:
            continue
    return None


# --------------------------------------------------------------------------- #
# Project / emit / root resolution
# --------------------------------------------------------------------------- #
def project_basename(cwd: str | None) -> str:
    if not cwd:
        return ""
    return os.path.basename(cwd.rstrip("/"))


def emit_path() -> str:
    """Resolve the `emit` helper. `CLAUDE_SOURCE_EMIT` overrides (tests); else the
    symlinked sibling `emit` (two dirs up from this file), else the deployed copy."""
    explicit = os.environ.get("CLAUDE_SOURCE_EMIT")
    if explicit:
        return explicit
    here = Path(__file__).resolve().parent.parent / "emit"
    if here.exists():
        return str(here)
    return str(Path.home() / ".config/activity-collector/emit")


def projects_roots() -> list[str]:
    """Transcript roots. `CLAUDE_PROJECTS_DIR` (os.pathsep-joined) overrides for
    tests; else the real `~/.claude/projects`."""
    roots_env = os.environ.get("CLAUDE_PROJECTS_DIR")
    if roots_env:
        return [p for p in roots_env.split(os.pathsep) if p]
    return [os.path.expanduser("~/.claude/projects")]


def iter_transcripts(roots: list[str]):
    """Yield (path, session_stem) for every real transcript under `roots`.

    Skips the synthetic agent transcript dirs (`subagents/`, `wf_*`) EXACTLY like
    tailer.iter_messages, so both sources cover the identical session set."""
    for root in roots:
        for path in glob.glob(os.path.join(root, "**", "*.jsonl"), recursive=True):
            project_dir = os.path.basename(os.path.dirname(path))
            if project_dir == "subagents" or project_dir.startswith("wf_"):
                continue
            yield path, Path(path).stem
