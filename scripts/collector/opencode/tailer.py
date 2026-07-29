#!/usr/bin/env python3
"""tailer — emit per-message activity events for the OpenCode source.

Reads OpenCode's SQLite database, walks sessions/messages/parts, and emits
`kind=prompt` (user messages) and `kind=assistant-turn` (assistant responses)
events via the shared spool.

Design:
  * REUSE _shared iterators (iter_sessions / iter_messages / iter_parts) and
    spool_emit for DB access and event emission.
  * IDEMPOTENT: a state file records every already-emitted message ID. Each
    run emits ONLY IDs not in the state set, then persists the union.
  * Per-message event mapping:
        source  = opencode
        kind    = "prompt" for user messages, "assistant-turn" for assistant
        text    = first text part content
        project = repo basename from session directory
        cwd     = session directory
        session = session id
        app     = opencode
        ts      = message creation timestamp
        payload = {role, model, agent, cost, tokens, tool_calls, tool_count}
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# Ensure symlink dir is on sys.path BEFORE importing _shared.
# Python resolves script symlinks for sys.path[0], but Path(__file__).parent
# preserves the symlink path — use it so _shared resolves keylog correctly.
_dir = str(Path(__file__).parent)
if _dir not in sys.path:
    sys.path.insert(0, _dir)

import _shared as S  # noqa: E402


# --------------------------------------------------------------------------- #
# Noise filtering
# --------------------------------------------------------------------------- #
def classify(text: str) -> tuple[str, str] | None:
    """Classify text → (kind, text) or None if noise.

    kind is "command" for slash-commands, "typed" for genuine messages.
    Returns None for empty, very short, or noise text.
    """
    if not text:
        return None
    t = text.strip()
    if len(t) < 2:
        return None
    if t.startswith("/"):
        return ("command", t)
    return ("typed", t)


def clean_text(text: str) -> str:
    """Strip OpenCode-specific noise and whitespace.

    OpenCode doesn't have the same <system-reminder> tags as Claude,
    so this just strips whitespace for now.
    """
    return (text or "").strip()


# --------------------------------------------------------------------------- #
# Identity / extraction
# --------------------------------------------------------------------------- #
def message_id(msg: dict) -> str:
    """Globally unique message ID: session_id:msg_id."""
    return f"{msg['session_id']}:{msg['id']}"


def extract_text(parts: list[dict]) -> str:
    """Extract text content from the first text-type part."""
    for part in parts:
        if part.get("type") == "text":
            return part.get("text") or ""
    return ""


_TOOL_META_KEYS = {"type", "tool", "callID", "state", "reason", "tokens", "cost", "text"}


def _extract_args(data: dict) -> dict:
    """Extract tool-specific arguments from the part data dict."""
    return {k: v for k, v in data.items() if k not in _TOOL_META_KEYS}


def extract_tool_calls(parts: list[dict]) -> list[dict]:
    """Extract tool-invocation parts as [{tool_name, args, state}]."""
    calls = []
    for part in parts:
        if part.get("type") in ("tool", "tool-invocation"):
            calls.append({
                "tool_name": part.get("tool") or "",
                "args": _extract_args(part.get("_data") or {}),
                "state": part.get("state") or {},
            })
    return calls


# --------------------------------------------------------------------------- #
# Event building
# --------------------------------------------------------------------------- #
def _int(v) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def _float(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def build_event(msg: dict, parts: list[dict], session: dict) -> dict | None:
    """Build an activity event dict from a message and its parts.

    Returns None if the message should be filtered out (noise user messages,
    unknown roles).
    """
    role = msg.get("role")
    if role not in ("user", "assistant"):
        return None

    text = extract_text(parts)

    # Filter noise for user messages
    if role == "user":
        classification = classify(text)
        if classification is None:
            return None

    directory = session.get("directory") or ""
    tool_calls = extract_tool_calls(parts)

    # Get token info from message data blob
    tokens = msg.get("tokens") or {}

    kind = "prompt" if role == "user" else "assistant-turn"

    tool_calls_summary = [{"name": tc["tool_name"], "state": tc["state"]} for tc in tool_calls]

    payload = json.dumps({
        "role": role,
        "model": msg.get("model") or session.get("model"),
        "agent": msg.get("agent") or session.get("agent"),
        "cost": _float(msg.get("cost")),
        "tokens_input": _int(tokens.get("input")),
        "tokens_output": _int(tokens.get("output")),
        "tool_calls": tool_calls_summary,
        "tool_count": len(tool_calls),
    }, ensure_ascii=False, separators=(",", ":"))

    return {
        "source": "opencode",
        "kind": kind,
        "text": text,
        "project": S.project_basename(directory),
        "cwd": directory,
        "session": msg["session_id"],
        "app": "opencode",
        "ts": S.to_ch_ts(msg["time_created"]),
        "payload": payload,
    }


# --------------------------------------------------------------------------- #
# State (idempotency)
# --------------------------------------------------------------------------- #
def state_path() -> Path:
    explicit = os.environ.get("OPENCODE_TAILER_STATE")
    if explicit:
        return Path(explicit)
    base = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state"))
    return base / "activity" / "opencode-tailer-state.json"


def load_state(path: Path) -> set[str]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return set()
    seen = data.get("seen") if isinstance(data, dict) else None
    return set(seen) if isinstance(seen, list) else set()


def save_state(path: Path, seen: set[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps({"version": 1, "seen": sorted(seen)}, separators=(",", ":")),
        encoding="utf-8",
    )
    os.replace(tmp, path)  # atomic


# --------------------------------------------------------------------------- #
# Run
# --------------------------------------------------------------------------- #
def run(db_path: Path | None = None) -> int:
    sp = state_path()
    seen = load_state(sp)

    db = S.get_db(db_path)
    if db is None:
        print("tailer: no OpenCode DB found, exiting")
        return 0

    new_seen: set[str] = set(seen)
    emitted = 0
    scanned = 0

    try:
        for session in S.iter_sessions(db):
            sid = session["id"]
            for msg in S.iter_messages(db, sid):
                mid = message_id(msg)
                scanned += 1
                if mid in new_seen:
                    continue
                parts = list(S.iter_parts(db, msg["id"]))
                ev = build_event(msg, parts, session)
                if ev is None:
                    new_seen.add(mid)
                    continue
                S.spool_emit(ev)
                new_seen.add(mid)
                emitted += 1
    finally:
        db.close()

    save_state(sp, new_seen)
    print(f"tailer: scanned={scanned} emitted={emitted} state={sp}")
    return 0


if __name__ == "__main__":
    _dir = str(Path(__file__).parent)
    if _dir not in sys.path:
        sys.path.insert(0, _dir)
    raise SystemExit(run())
