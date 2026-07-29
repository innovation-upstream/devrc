#!/usr/bin/env python3
"""session_tailer — emit a per-SESSION rollup for the OpenCode activity source.

Reads OpenCode's SQLite database, walks sessions/messages/parts, and emits a
deterministic `kind=session-summary` event per session via the shared spool.
Exactly ONE event per session; re-emitted only when the session's signature
(cheap checksum of time_updated + cost + tokens_input) changes.

Event shape (via _shared.spool_emit → spool → ClickHouse activity.events):
    source  = opencode
    kind    = session-summary
    session = session UUID
    project = repo basename from session directory
    cwd     = session directory
    ts      = session start instant (ClickHouse DateTime64(3))
    payload = rollup JSON

State file tracks per-session signatures to skip unchanged sessions.
CHECKPOINT_EVERY = 25 for resumable backfills.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

import _shared as S  # noqa: E402


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
EXT_LANG = {
    ".py": "Python", ".pyi": "Python",
    ".js": "JavaScript", ".jsx": "JavaScript", ".mjs": "JavaScript", ".cjs": "JavaScript",
    ".ts": "TypeScript", ".tsx": "TypeScript",
    ".go": "Go", ".rs": "Rust", ".rb": "Ruby", ".java": "Java", ".kt": "Kotlin",
    ".c": "C", ".h": "C", ".cpp": "C++", ".cc": "C++", ".hpp": "C++",
    ".cs": "C#", ".php": "PHP", ".swift": "Swift", ".scala": "Scala",
    ".sh": "Shell", ".bash": "Shell", ".zsh": "Shell",
    ".nix": "Nix", ".lua": "Lua",
    ".md": "Markdown", ".markdown": "Markdown",
    ".yaml": "YAML", ".yml": "YAML",
    ".json": "JSON", ".toml": "TOML", ".ini": "INI",
    ".html": "HTML", ".htm": "HTML", ".css": "CSS", ".scss": "SCSS",
    ".sql": "SQL", ".tf": "Terraform", ".hcl": "HCL",
    ".vim": "VimScript", ".xml": "XML", ".txt": "Text", ".csv": "CSV",
}
_SPECIAL_NAMES = {"dockerfile": "Dockerfile", "makefile": "Makefile"}

# Tools that create/modify a file → contribute to languages / files / churn.
_FILE_TOOLS = {"edit", "write", "write_file", "create_file"}

# git commit / push inside a bash command.
_GIT_COMMIT = re.compile(r"\bgit\s+(?:-C\s+\S+\s+|-\S+\s+)*commit\b")
_GIT_PUSH = re.compile(r"\bgit\s+(?:-C\s+\S+\s+|-\S+\s+)*push\b")


def lang_for_path(path: str) -> str | None:
    """Map an edited/written file path to a language name, or None."""
    if not path:
        return None
    base = os.path.basename(path)
    special = _SPECIAL_NAMES.get(base.lower())
    if special:
        return special
    return EXT_LANG.get(os.path.splitext(base)[1].lower())


def is_git_commit(cmd: str) -> bool:
    return bool(cmd) and _GIT_COMMIT.search(cmd) is not None


def is_git_push(cmd: str) -> bool:
    return bool(cmd) and _GIT_PUSH.search(cmd) is not None


def categorize_tool_error(text: str) -> str:
    """Bucket a failed tool invocation into a coarse deterministic category."""
    t = (text or "").lower()
    if "timed out" in t or "timeout" in t:
        return "Timeout"
    if ("no such file" in t or "not found" in t or "does not exist" in t
            or "no matches found" in t):
        return "File Not Found"
    if ("permission denied" in t or "not permitted" in t
            or "operation not permitted" in t):
        return "Permission Denied"
    if ("error" in t or "failed" in t or "exit code" in t or "non-zero" in t
            or "traceback" in t):
        return "Command Failed"
    return "Other"


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


# --------------------------------------------------------------------------- #
# Rollup
# --------------------------------------------------------------------------- #
def build_rollup(session_data: dict, messages: list[dict], parts: list[dict]) -> dict:
    """Deterministic per-session rollup from session dict, messages, and parts.

    `session_data` is the dict from iter_sessions().
    `messages` is a list of dicts from iter_messages().
    `parts` is a flat list of ALL part dicts from iter_parts() for ALL messages.

    Returns a rollup dict with tool counts, tokens, languages, git activity, etc.
    """
    r: dict = {
        "tool_counts": {},
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        "reasoning_tokens": 0,
        "user_message_count": 0,
        "assistant_message_count": 0,
        "duration_minutes": 0.0,
        "languages": {},
        "git_commits": 0,
        "git_pushes": 0,
        "files_modified": 0,
        "lines_added": 0,
        "lines_removed": 0,
        "tool_errors": 0,
        "tool_error_categories": {},
        "uses_task_agent": False,
        "uses_mcp": False,
        "uses_web_search": False,
        "uses_web_fetch": False,
        "models": [],
        "start_ts": None,
        "end_ts": None,
        "unreadable": False,
        "cost": 0.0,
    }

    tool_counts: dict = r["tool_counts"]
    languages: dict = r["languages"]
    err_cats: dict = r["tool_error_categories"]
    files: set[str] = set()
    models: set[str] = set()

    # --- Session-level aggregates ---
    r["cost"] = _float(session_data.get("cost"))
    tokens = session_data.get("tokens") or {}
    r["input_tokens"] = _int(tokens.get("input"))
    r["output_tokens"] = _int(tokens.get("output"))
    r["reasoning_tokens"] = _int(tokens.get("reasoning"))
    cache = tokens.get("cache") or {}
    r["cache_read_tokens"] = _int(cache.get("read"))
    r["cache_write_tokens"] = _int(cache.get("write"))

    # --- Session timestamps ---
    time_created = session_data.get("time_created")
    time_updated = session_data.get("time_updated")
    if time_created is not None:
        r["start_ts"] = S.to_ch_ts(int(time_created))
    if time_updated is not None:
        r["end_ts"] = S.to_ch_ts(int(time_updated))
    if (time_created is not None and time_updated is not None
            and time_updated >= time_created):
        r["duration_minutes"] = round((time_updated - time_created) / 60000, 2)

    # --- Walk messages ---
    for msg in messages:
        role = msg.get("role")
        if role == "user":
            r["user_message_count"] += 1
        elif role == "assistant":
            r["assistant_message_count"] += 1
        # Collect models from message-level model field
        model = msg.get("model")
        if isinstance(model, dict):
            mid = model.get("modelID") or model.get("id")
            if mid:
                models.add(mid)
        elif isinstance(model, str) and model:
            models.add(model)

    # --- Walk parts ---
    for part in parts:
        part_type = part.get("type")

        # Tool invocations
        if part_type == "tool":
            tool_name = part.get("tool") or ""
            if tool_name:
                tool_counts[tool_name] = tool_counts.get(tool_name, 0) + 1

            # Extract tool arguments from the parsed data blob.
            # OpenCode stores tool-specific fields (command, file_path, etc.)
            # in the part's JSON data column, not as top-level part fields.
            # _shared.iter_parts includes the raw parsed dict as "_data".
            _data = part.get("_data") or {}
            args_str = ""
            fp = ""
            if isinstance(_data, dict):
                args_str = str(_data.get("command") or _data.get("input") or "")
                fp = str(_data.get("file_path") or _data.get("path") or "")

            if tool_name == "bash":
                if is_git_commit(args_str):
                    r["git_commits"] += 1
                if is_git_push(args_str):
                    r["git_pushes"] += 1
            elif tool_name in ("task",):
                r["uses_task_agent"] = True
            elif tool_name.startswith("mcp__"):
                r["uses_mcp"] = True
            elif tool_name == "websearch":
                r["uses_web_search"] = True
            elif tool_name == "webfetch":
                r["uses_web_fetch"] = True

            # File tools → language detection + files modified
            if tool_name in _FILE_TOOLS and fp:
                files.add(fp)
                lang = lang_for_path(fp)
                if lang:
                    languages[lang] = languages.get(lang, 0) + 1

            # Tool errors
            state = part.get("state") or {}
            if isinstance(state, dict) and state.get("status") == "error":
                r["tool_errors"] += 1
                err_text = str(state.get("error") or state.get("message") or "")
                cat = categorize_tool_error(err_text)
                err_cats[cat] = err_cats.get(cat, 0) + 1

    r["files_modified"] = len(files)
    r["models"] = sorted(models)
    r["unreadable"] = (r["user_message_count"] == 0
                       and r["assistant_message_count"] == 0)
    return r


# --------------------------------------------------------------------------- #
# Event / emit
# --------------------------------------------------------------------------- #
def build_event(session_id: str, directory: str, rollup: dict) -> dict:
    """Build the event dict for spool_emit."""
    return {
        "source": "opencode",
        "kind": "session-summary",
        "session": session_id,
        "project": S.project_basename(directory),
        "cwd": directory,
        "ts": rollup.get("start_ts"),
        "app": "opencode",
        "payload": json.dumps(rollup, ensure_ascii=False, separators=(",", ":")),
    }


# --------------------------------------------------------------------------- #
# State (idempotency + mutable-session awareness)
# --------------------------------------------------------------------------- #
def state_path() -> Path:
    explicit = os.environ.get("OPENCODE_SESSION_STATE")
    if explicit:
        return Path(explicit)
    base = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state"))
    return base / "activity" / "opencode-session-summary-state.json"


def signature(session: dict) -> str:
    """Cheap change signature — computed WITHOUT reading messages/parts."""
    return f"{session.get('time_updated', 0)}:{session.get('cost', 0)}:{(session.get('tokens') or {}).get('input', 0)}"


def load_state(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    sigs = data.get("sigs") if isinstance(data, dict) else None
    return dict(sigs) if isinstance(sigs, dict) else {}


def save_state(path: Path, sigs: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps({"version": 1, "sigs": sigs}, separators=(",", ":")),
        encoding="utf-8",
    )
    os.replace(tmp, path)  # atomic


CHECKPOINT_EVERY = 25


# --------------------------------------------------------------------------- #
# Run
# --------------------------------------------------------------------------- #
def run(db_path: Path | None = None) -> int:
    sp = state_path()
    prev = load_state(sp)

    db = S.get_db(db_path)
    if db is None:
        print("session_tailer: no OpenCode DB found, exiting")
        return 0

    new_sigs: dict = dict(prev)
    seen: set = set()
    emitted = 0
    scanned = 0
    since_checkpoint = 0

    try:
        for session in S.iter_sessions(db):
            sid = session["id"]
            sig = signature(session)
            seen.add(sid)
            scanned += 1
            if prev.get(sid) == sig:
                new_sigs[sid] = sig
                continue

            directory = session.get("directory") or ""
            messages = list(S.iter_messages(db, sid))
            all_parts: list[dict] = []
            for msg in messages:
                all_parts.extend(S.iter_parts(db, msg["id"]))

            rollup = build_rollup(session, messages, all_parts)
            ev = build_event(sid, directory, rollup)
            S.spool_emit(ev)
            new_sigs[sid] = sig
            emitted += 1
            since_checkpoint += 1
            if since_checkpoint >= CHECKPOINT_EVERY:
                save_state(sp, new_sigs)
                since_checkpoint = 0
    finally:
        db.close()

    # Prune deleted sessions from state
    new_sigs = {sid: s for sid, s in new_sigs.items() if sid in seen}
    save_state(sp, new_sigs)
    print(f"session_tailer: scanned={scanned} emitted={emitted} state={sp}")
    return 0


if __name__ == "__main__":
    # When run as a standalone script, ensure opencode dir is on sys.path
    _dir = str(Path(__file__).parent)
    if _dir not in sys.path:
        sys.path.insert(0, _dir)
    raise SystemExit(run())
