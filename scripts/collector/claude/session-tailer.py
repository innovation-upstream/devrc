#!/usr/bin/env python3
"""session-tailer — emit a deterministic per-SESSION rollup (Layer A) for the
personal telemetry pipeline.

This is the sibling of tailer.py. Where tailer.py emits the MESSAGE STREAM
(`kind=prompt|command`, one event per user turn), THIS emits LAYER A: exactly ONE
`kind=session-summary` event per Claude Code session, whose `payload` is a
deterministic rollup of the WHOLE transcript — tool counts, tokens, languages,
git commits/pushes, churn, durations, interruptions, tool errors, models, etc.
It is the telemetry-native, durable, versioned replacement for the built-in
`/insights` `session-meta/*.json` cache (per-host, ephemeral, overwritten each
run). NO LLM anywhere — every number is computed from the transcript.

Event shape (via the shared `emit` helper → spool → ClickHouse activity.events):
    source  = claude
    kind    = session-summary
    session = transcript filename stem (a session UUID)
    project = repo basename from the transcript's cwd
    cwd     = the transcript's cwd
    ts      = the session START instant (UTC, same to_ch_ts conversion tailer uses)
    payload = the rollup JSON (see build_rollup)

IDEMPOTENT + MUTABLE-SESSION AWARE. A session grows until it ends, so its summary
changes over time. A state file (default
~/.local/state/activity/session-summary-state.json, env-overridable via
CLAUDE_SUMMARY_STATE) records, per transcript path, a cheap signature
(mtime-ns + byte size). The summary is (re-)emitted ONLY when the signature
changed since the last emit — so the periodic timer never re-ships an unchanged
session, but DOES re-ship one that grew.

READ CONTRACT (how the report/consumer dedupes): activity.events is append-only,
so a mutating session accumulates several session-summary rows over its life. A
consumer takes the LATEST per session with `argMax(<field>, ingested_at)` grouped
by `session` — the newest emitted rollup wins. See scripts/session-analysis/insights.py.

The host is stamped by the collector daemon (ACTIVITY_HOST), so this runs
unchanged on both workbench + laptop. Skips the synthetic `subagents/` and `wf_*`
transcript dirs exactly like tailer.py (shared iter_transcripts).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

from _shared import (
    ch_ts_to_epoch,
    emit_path,
    iter_transcripts,
    project_basename,
    projects_roots,
    to_ch_ts,
)
# Reuse tailer's noise-filtering so "genuine user message" means the SAME thing
# in the rollup as in the message stream (DRY — one definition of a real turn).
from tailer import classify, extract_blocks


# --------------------------------------------------------------------------- #
# Deterministic helpers (pure — unit-tested without any transcript file)
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
_FILE_TOOLS = {"Edit", "Write", "NotebookEdit", "MultiEdit"}

# git commit / push inside a Bash command (allowing `-C path`, `-c k=v`, chains).
_GIT_COMMIT = re.compile(r"\bgit\s+(?:-C\s+\S+\s+|-\S+\s+)*commit\b")
_GIT_PUSH = re.compile(r"\bgit\s+(?:-C\s+\S+\s+|-\S+\s+)*push\b")

FIRST_PROMPT_MAX = 240


def lang_for_path(path: str) -> str | None:
    """Map an edited/written file path to a language name (by extension / special
    filename), or None if unknown."""
    if not path:
        return None
    base = os.path.basename(path)
    special = _SPECIAL_NAMES.get(base.lower())
    if special:
        return special
    return EXT_LANG.get(os.path.splitext(base)[1].lower())


def count_lines(s) -> int:
    """Line count of a text block (0 for empty/None). Used for edit CHURN."""
    if not s or not isinstance(s, str):
        return 0
    return s.count("\n") + 1


def is_git_commit(cmd: str) -> bool:
    return bool(cmd) and _GIT_COMMIT.search(cmd) is not None


def is_git_push(cmd: str) -> bool:
    return bool(cmd) and _GIT_PUSH.search(cmd) is not None


def categorize_tool_error(text: str) -> str:
    """Bucket a failed tool_result into a coarse deterministic category."""
    t = (text or "").lower()
    if "timed out" in t or "timeout" in t:
        return "Timeout"
    if ("no such file" in t or "not found" in t or "does not exist" in t
            or "has not been read" in t or "no matches found" in t):
        return "File Not Found"
    if ("permission denied" in t or "not permitted" in t
            or "operation not permitted" in t):
        return "Permission Denied"
    if ("error" in t or "failed" in t or "exit code" in t or "non-zero" in t
            or "traceback" in t):
        return "Command Failed"
    return "Other"


def churn(tool_name: str, inp: dict) -> tuple[int, int]:
    """(lines_added, lines_removed) approximated from an edit/write tool input.

    This is an EDIT-BLOCK CHURN measure (size of the text blocks the tool
    replaced/wrote), NOT git's line diff — we cannot see the real file. Documented
    as such so the report never over-claims. Write adds its content; Edit removes
    old_string's lines and adds new_string's; MultiEdit sums its edits."""
    if tool_name == "Write":
        return (count_lines(inp.get("content")), 0)
    if tool_name == "NotebookEdit":
        return (count_lines(inp.get("new_source")), 0)
    if tool_name == "Edit":
        return (count_lines(inp.get("new_string")), count_lines(inp.get("old_string")))
    if tool_name == "MultiEdit":
        a = r = 0
        for e in inp.get("edits") or []:
            if isinstance(e, dict):
                a += count_lines(e.get("new_string"))
                r += count_lines(e.get("old_string"))
        return (a, r)
    return (0, 0)


def _result_text(block: dict) -> str:
    """Flatten a tool_result block's content (str or list of text blocks)."""
    c = block.get("content")
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        parts = []
        for b in c:
            if isinstance(b, dict) and b.get("type") == "text":
                parts.append(b.get("text", ""))
            elif isinstance(b, str):
                parts.append(b)
        return "\n".join(parts)
    return ""


def _int(v) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def _ts_hour(ch_ts: str | None) -> int | None:
    """UTC hour-of-day from a 'YYYY-MM-DD HH:MM:SS.fff' string (tz-less/UTC)."""
    if not ch_ts or len(ch_ts) < 13:
        return None
    try:
        return int(ch_ts[11:13])
    except ValueError:
        return None


# --------------------------------------------------------------------------- #
# Rollup
# --------------------------------------------------------------------------- #
def _empty_rollup() -> dict:
    return {
        "tool_counts": {},
        "input_tokens": 0,
        "output_tokens": 0,
        "user_message_count": 0,
        "assistant_message_count": 0,
        "duration_minutes": 0,
        "languages": {},
        "git_commits": 0,
        "git_pushes": 0,
        "files_modified": 0,
        "lines_added": 0,
        "lines_removed": 0,
        "user_interruptions": 0,
        "tool_errors": 0,
        "tool_error_categories": {},
        "uses_task_agent": False,
        "uses_mcp": False,
        "uses_web_search": False,
        "uses_web_fetch": False,
        "models": [],
        "first_prompt": None,
        "start_ts": None,
        "end_ts": None,
        "message_hours": [],
        "cwd": "",
        "unreadable": False,
    }


def build_rollup(objects: list[dict]) -> dict:
    """Deterministic per-session rollup from parsed transcript JSON objects.

    Mirrors the built-in `/insights` session-meta fields so insights.py is a
    drop-in. `unreadable` is True when the transcript yielded no messages at all
    (so the report can show it honestly rather than inventing content)."""
    r = _empty_rollup()
    tool_counts: dict = r["tool_counts"]
    languages: dict = r["languages"]
    err_cats: dict = r["tool_error_categories"]
    files: set[str] = set()
    models: set[str] = set()
    hours: list[int] = []
    start_iso = end_iso = None
    cwd = ""

    for obj in objects:
        if not isinstance(obj, dict):
            continue
        iso = obj.get("timestamp")
        if iso:
            if start_iso is None or iso < start_iso:
                start_iso = iso
            if end_iso is None or iso > end_iso:
                end_iso = iso
        if not cwd and obj.get("cwd"):
            cwd = obj.get("cwd") or ""

        if obj.get("isSidechain"):
            continue  # subagent's own turns — not this session's work
        typ = obj.get("type")

        if typ == "user":
            if obj.get("isMeta"):
                continue
            msg = obj.get("message") or {}
            if msg.get("role") != "user":
                continue
            content = msg.get("content")
            # tool_result blocks (errors) live in the raw content list.
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_result":
                        if block.get("is_error"):
                            r["tool_errors"] += 1
                            cat = categorize_tool_error(_result_text(block))
                            err_cats[cat] = err_cats.get(cat, 0) + 1
            # genuine typed / slash-command turns (reusing tailer's classifier).
            genuine = None
            for raw in extract_blocks(content):
                if raw and raw.lstrip().startswith("[Request interrupted"):
                    r["user_interruptions"] += 1
                res = classify(raw)
                if res is not None and genuine is None:
                    genuine = res  # (kind, text)
            if genuine is not None:
                r["user_message_count"] += 1
                h = _ts_hour(to_ch_ts(iso))
                if h is not None:
                    hours.append(h)
                if r["first_prompt"] is None and genuine[0] == "typed":
                    r["first_prompt"] = genuine[1][:FIRST_PROMPT_MAX]

        elif typ == "assistant":
            msg = obj.get("message") or {}
            r["assistant_message_count"] += 1
            model = msg.get("model")
            if model:
                models.add(model)
            usage = msg.get("usage") or {}
            r["input_tokens"] += _int(usage.get("input_tokens"))
            r["output_tokens"] += _int(usage.get("output_tokens"))
            content = msg.get("content")
            if isinstance(content, list):
                for block in content:
                    if not isinstance(block, dict) or block.get("type") != "tool_use":
                        continue
                    name = block.get("name") or ""
                    tool_counts[name] = tool_counts.get(name, 0) + 1
                    inp = block.get("input") or {}
                    if name in ("Task", "Agent"):
                        r["uses_task_agent"] = True
                    if name.startswith("mcp__"):
                        r["uses_mcp"] = True
                    if name == "WebSearch":
                        r["uses_web_search"] = True
                    if name == "WebFetch":
                        r["uses_web_fetch"] = True
                    if name == "Bash":
                        cmd = str(inp.get("command") or "")
                        if is_git_commit(cmd):
                            r["git_commits"] += 1
                        if is_git_push(cmd):
                            r["git_pushes"] += 1
                    if name in _FILE_TOOLS:
                        fp = inp.get("file_path") or inp.get("notebook_path") or ""
                        if fp:
                            files.add(fp)
                            lang = lang_for_path(fp)
                            if lang:
                                languages[lang] = languages.get(lang, 0) + 1
                        add, rem = churn(name, inp)
                        r["lines_added"] += add
                        r["lines_removed"] += rem

    r["files_modified"] = len(files)
    r["models"] = sorted(models)
    r["message_hours"] = hours
    r["cwd"] = cwd
    r["start_ts"] = to_ch_ts(start_iso)
    r["end_ts"] = to_ch_ts(end_iso)
    s, e = ch_ts_to_epoch(r["start_ts"]), ch_ts_to_epoch(r["end_ts"])
    if s is not None and e is not None and e >= s:
        r["duration_minutes"] = round((e - s) / 60)
    r["unreadable"] = (r["user_message_count"] == 0
                       and r["assistant_message_count"] == 0)
    return r


def summarize_transcript(path: str) -> dict:
    """Read a transcript fully and return its rollup. A file that can't be opened
    or contains no parseable JSON is flagged `unreadable` (never fabricated)."""
    objects: list[dict] = []
    parsed_any = False
    try:
        with open(path, errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                parsed_any = True
                objects.append(obj)
    except OSError:
        r = _empty_rollup()
        r["unreadable"] = True
        return r
    if not parsed_any:
        r = _empty_rollup()
        r["unreadable"] = True
        return r
    return build_rollup(objects)


# --------------------------------------------------------------------------- #
# Event / emit
# --------------------------------------------------------------------------- #
def build_event(session: str, rollup: dict) -> dict:
    cwd = rollup.get("cwd") or ""
    return {
        "session": session,
        "project": project_basename(cwd),
        "cwd": cwd,
        "ts": rollup.get("start_ts"),
        "payload": json.dumps(rollup, ensure_ascii=False, separators=(",", ":")),
    }


def build_emit_args(ev: dict) -> list[str]:
    args = [
        "source=claude",
        "kind=session-summary",
        f"b64:project={ev['project']}",
        f"b64:session={ev['session']}",
        f"b64:cwd={ev['cwd']}",
        "b64:app=claude-code",
        f"b64:payload={ev['payload']}",
    ]
    if ev["ts"]:
        args.append(f"ts={ev['ts']}")
    return args


def emit_event(emit: str, ev: dict) -> None:
    subprocess.run([emit, *build_emit_args(ev)], check=True)


# --------------------------------------------------------------------------- #
# State (idempotency + mutable-session awareness)
# --------------------------------------------------------------------------- #
def state_path() -> Path:
    explicit = os.environ.get("CLAUDE_SUMMARY_STATE")
    if explicit:
        return Path(explicit)
    base = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state"))
    return base / "activity" / "session-summary-state.json"


def signature(path: str) -> str | None:
    """Cheap change signature (mtime-ns + byte size) — computed WITHOUT reading
    the file, so an unchanged session is skipped without parsing it."""
    try:
        st = os.stat(path)
    except OSError:
        return None
    return f"{st.st_mtime_ns}:{st.st_size}"


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


# --------------------------------------------------------------------------- #
# Run
# --------------------------------------------------------------------------- #
def run() -> int:
    roots = projects_roots()
    sp = state_path()
    prev = load_state(sp)
    emit = emit_path()

    emitted = 0
    scanned = 0
    new_sigs: dict = {}
    for path, session in iter_transcripts(roots):
        sig = signature(path)
        if sig is None:
            continue
        scanned += 1
        new_sigs[path] = sig
        if prev.get(path) == sig:
            continue  # unchanged since last emit — skip (idempotent)
        rollup = summarize_transcript(path)
        emit_event(emit, build_event(session, rollup))
        emitted += 1

    save_state(sp, new_sigs)
    print(f"session-tailer: scanned={scanned} emitted={emitted} state={sp}")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
