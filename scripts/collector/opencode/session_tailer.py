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

# Ensure symlink dir is on sys.path BEFORE importing _shared.
_dir = str(Path(__file__).parent)
if _dir not in sys.path:
    sys.path.insert(0, _dir)

# The collector ROOT (this file's grandparent) holds `changed_paths.py`, shared
# with the Claude summariser so the `changed_paths*` block has ONE definition.
# 🔴 Do NOT `.resolve()` — a nix-store symlink resolves INTO the store and loses
# the ~/.config/activity-collector/ prefix (see _shared.spool_emit's note).
# APPENDED, not inserted at 0: the collector root also holds `collector.py`,
# `deadman.py` and friends, and putting it ahead of this file's own directory
# would let one of those shadow a sibling module (`_shared` in particular).
_root = str(Path(__file__).parent.parent)
if _root not in sys.path:
    sys.path.append(_root)

import _shared as S  # noqa: E402
import changed_paths as CP  # noqa: E402


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
# MEASURED 2026-08-11 over the live store (7,982 tool parts): only `edit` (474)
# and `write` (160) occur. `write_file` / `create_file` have never appeared and
# are kept only so a rename of the same concept still counts — they cost
# nothing, and unlike an over-broad INPUT key they cannot manufacture a match.
_FILE_TOOLS = {"edit", "write", "write_file", "create_file"}
# The bash-equivalent tool, whose input carries the shell command.
_COMMAND_TOOL = "bash"

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


def count_lines(s) -> int:
    """Line count of a text block (0 for empty/None). Used for edit CHURN.

    Byte-identical semantics to `claude/session-tailer.py:count_lines`, on
    purpose: the two sources' `lines_added`/`lines_removed` are meant to be
    COMPARABLE, and a churn measure that counted differently per source would be
    a silent apples-to-oranges in every cross-source report.
    """
    if not s or not isinstance(s, str):
        return 0
    return s.count("\n") + 1


def churn(tool_name: str, inp: dict) -> tuple[int, int]:
    """(lines_added, lines_removed) from an opencode edit/write tool INPUT.

    Same EDIT-BLOCK CHURN measure the Claude summariser documents — the size of
    the text blocks the tool wrote/replaced, NOT git's line diff, because we
    cannot see the real file. Only the key names differ: opencode's tool input
    is camelCase (`newString`/`oldString`/`content`) where Claude's transcript
    is snake_case (`new_string`/`old_string`/`content`).

    ⚠ INVESTIGATED AND REJECTED, 2026-08-11: opencode DOES carry a real unified
    diff at `state.metadata.diff` on an `edit` part, and a `patch` part type
    listing `files`. Either would give a truer line count than block churn — and
    that is exactly why neither is used. The number has to mean the same thing
    as the Claude number to be worth emitting beside it; mixing a real diff into
    one source and block churn into the other produces two columns that look
    comparable and are not. Also rejected: the `session.summary_additions` /
    `summary_deletions` / `summary_files` columns — MEASURED 2026-08-11, all
    three are 0 for all 233 sessions in the live store, i.e. opencode does not
    populate them.
    """
    if tool_name in ("write", "write_file", "create_file"):
        return (count_lines(inp.get("content")), 0)
    if tool_name == "edit":
        return (count_lines(inp.get("newString")), count_lines(inp.get("oldString")))
    return (0, 0)


def tool_input(data: dict) -> dict | None:
    """The tool INPUT dict from a raw opencode `part.data` blob, or None.

    🔴 THIS IS THE WHOLE OF GAP B. The previous code read `data["command"]` and
    `data["file_path"]` from the part's TOP LEVEL. opencode has never put them
    there: the real shape, MEASURED 2026-08-11 over 7,982 live tool parts, is

        {"type": "tool", "tool": "edit", "callID": "...",
         "state": {"status": "completed",
                   "input": {"filePath": ..., "newString": ..., "oldString": ...},
                   "output": ..., "metadata": {...}}}

    — nested under `state.input`, and camelCase. Every top-level lookup returned
    None, so `files_modified`, `lines_added`, `lines_removed`, `git_commits` and
    `git_pushes` were structurally pinned at 0 for every opencode session ever
    summarised, while `tool_counts` (which reads the genuinely top-level `tool`
    key) stayed correct — which is why the rollups looked populated.

    The unit tests did not catch it because their fixtures were written in the
    same wrong shape as the extractor, so they asserted the implementation back
    to itself. Those fixtures are now built from this measured shape.

    Returns None — never `{}` — when there is no readable input, so a caller can
    COUNT the misses and decide the statistic is unobservable rather than zero.
    """
    state = data.get("state")
    if not isinstance(state, dict):
        return None
    inp = state.get("input")
    return inp if isinstance(inp, dict) else None


# --------------------------------------------------------------------------- #
# Rollup
# --------------------------------------------------------------------------- #
def build_rollup(session_data: dict, messages: list[dict], parts: list[dict],
                 *, directory: str = "") -> dict:
    """Deterministic per-session rollup from session dict, messages, and parts.

    `session_data` is the dict from iter_sessions().
    `messages` is a list of dicts from iter_messages().
    `parts` is a flat list of ALL part dicts from iter_parts() for ALL messages.
    `directory` is the session cwd — needed to make changed paths repo-relative.

    Returns a rollup dict with tool counts, tokens, languages, git activity, etc.

    🔴 ABSENT IS NOT ZERO. The file group (`files_modified`, `lines_added`,
    `lines_removed`, `changed_paths*`) and the git group (`git_commits`,
    `git_pushes`) are emitted as **None** — not 0 — when this function can show
    it was unable to observe them, and the group name is listed in
    `stats_unavailable`. The three unobservable conditions are each MEASURED,
    not imagined:

      * the store read yielded no parts at all for a session that has assistant
        messages. `_shared.iter_parts` swallows `sqlite3.OperationalError` and
        yields nothing, so a `part`-table schema change is indistinguishable
        from "this session ran no tools" at the call site. MEASURED 2026-08-11:
        233 of 233 live sessions with assistant messages have >= 1 part, so this
        condition fires on nothing today and fires loudly on that drift.
        (Deliberately a SESSION-level test: per-MESSAGE it would be wrong —
        10 of 5,576 assistant messages legitimately have no parts.)
      * file-tool parts were present but not one yielded a `filePath`;
      * `bash` parts were present but not one yielded a `command`.

    The last two are the direct regression guards for gap B: had they existed,
    the shape drift that pinned every one of these fields at 0 would have shown
    up as `stats_unavailable: ["files","git"]` on every session instead of as a
    plausible-looking zero nobody could distinguish from a quiet day.

    Unlike the Claude summariser — whose integer counters keep their type
    because live consumers read them — these are nulled outright. Nothing reads
    opencode `session-summary` payload fields today (`insights.py`,
    `session_insight/selection.py` and `validation/invariants.py` all filter
    `source='claude'`), and the values they would preserve are a constant 0 that
    was never true.
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
        # See the docstring: [] means every group was observed, so its zeros are
        # real; "files" / "git" mean the matching fields are None because we
        # could not read them.
        "stats_unavailable": [],
    }

    tool_counts: dict = r["tool_counts"]
    languages: dict = r["languages"]
    err_cats: dict = r["tool_error_categories"]
    files: set[str] = set()
    models: set[str] = set()

    # --- Observability accounting (the positive control, in the emitter) ------
    # Counting the parts we COULD read beside the parts we SAW is what makes a
    # zero falsifiable. Without it, "0 files modified" is emitted identically by
    # a quiet session and by a broken extractor — which is exactly how gap B
    # survived in production for months.
    file_tool_parts = 0
    file_paths_seen = 0
    command_parts = 0
    commands_seen = 0

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

            # Extract tool arguments from the parsed data blob. OpenCode nests
            # them under `state.input` with camelCase keys — see tool_input()
            # for the measured shape and for what reading them from the top
            # level cost. `_shared.iter_parts` includes the raw dict as "_data".
            _data = part.get("_data")
            inp = tool_input(_data) if isinstance(_data, dict) else None
            args_str = ""
            fp = ""
            if inp is not None:
                args_str = str(inp.get("command") or "")
                # ONLY `filePath`. Widening this to also accept `path` would
                # make the extractor match `grep`/`glob` inputs and would blunt
                # the drift detector below: a rename would keep resolving via
                # the alternate key and go back to being invisible.
                fp = str(inp.get("filePath") or "")

            if tool_name == _COMMAND_TOOL:
                command_parts += 1
                if args_str:
                    commands_seen += 1
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

            # File tools → language detection + files modified + churn
            if tool_name in _FILE_TOOLS:
                file_tool_parts += 1
                if fp:
                    file_paths_seen += 1
                    files.add(fp)
                    lang = lang_for_path(fp)
                    if lang:
                        languages[lang] = languages.get(lang, 0) + 1
                if inp is not None:
                    add, rem = churn(tool_name, inp)
                    r["lines_added"] += add
                    r["lines_removed"] += rem

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

    # --- The observability verdict -------------------------------------------
    # A session with assistant messages ALWAYS produced parts in the live store
    # (233/233, measured), so zero parts here means the part read failed, not
    # that the session was quiet. `iter_parts` returns an empty iterator on
    # sqlite3.OperationalError, so this is the only place that difference is
    # still visible.
    store_unreadable = (r["assistant_message_count"] > 0 and not parts)
    files_unobservable = store_unreadable or (
        file_tool_parts > 0 and file_paths_seen == 0)
    git_unobservable = store_unreadable or (
        command_parts > 0 and commands_seen == 0)

    unavailable: list[str] = []
    if files_unobservable:
        unavailable.append("files")
        r["files_modified"] = None
        r["lines_added"] = None
        r["lines_removed"] = None
        r.update(CP.unobservable())
    else:
        r.update(CP.summarize(files, directory or ""))
    if git_unobservable:
        unavailable.append("git")
        r["git_commits"] = None
        r["git_pushes"] = None
    r["stats_unavailable"] = unavailable
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

            rollup = build_rollup(session, messages, all_parts,
                                  directory=directory)
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
