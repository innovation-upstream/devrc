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

EMIT-ON-SETTLE (idempotent + mutable-session aware). A session grows until it
ends, so its summary changes over time. A state file (default
~/.local/state/activity/session-summary-state.json, env-overridable via
CLAUDE_SUMMARY_STATE) records, per transcript path, a cheap signature
(mtime-ns + byte size) plus WHEN we last emitted for it. On every tick each
transcript takes exactly one of these branches:

  unchanged   signature == the one we last emitted for  -> skip (nothing new)
  settled     changed AND idle >= CLAUDE_SUMMARY_SETTLE_MINUTES (default 20)
              AND (never emitted, or last emit older than
              CLAUDE_SUMMARY_INTERIM_HOURS) -> EMIT. This is the authoritative
              final rollup for the session. A session that is later RESUMED
              (`claude --resume` is real here) changes signature again, settles
              again, and re-emits — so the latest row is always the complete
              rollup.
  settled-recently
              settled, but emitted within CLAUDE_SUMMARY_INTERIM_HOURS -> skip.
              The new signature is deliberately NOT recorded, so the session
              still owes a rollup and emits on the first tick past the window.
  first-seen  changed, still active, never emitted before -> EMIT once, so an
              in-flight session (or one interrupted by a reboot) is present in
              the report instead of missing entirely.
  interim     changed, still active, last emit older than
              CLAUDE_SUMMARY_INTERIM_HOURS (default 4) -> EMIT a bounded
              interim rollup (backstop for very long sessions).
  active      changed, still active, emitted recently -> skip.

🔴 `settled-recently` was added 2026-08-02 and it closes a REAL, MEASURED
amplification. The settled branch used to emit unconditionally, without
consulting `emitted_at` — so an ACTIVE session was bounded to one emit per
interim window while a session idling BETWEEN BURSTS was not bounded at all: one
full rollup per burst, forever. That is the ordinary `claude --resume` shape, so
the bound was missing from exactly the common case. Measured over the 30 days to
2026-08-02: 31,815 rows across 482 sessions — median 2/session, but p90 209, max
572, mean 66 — against validation/invariants.py's `session_summary_rows_bounded`,
which flags >24 rows/session/24h. The fix is to make the settled branch consult
the same `emitted_at` with the same interval, so there is ONE knob rather than
two that can disagree; worst case becomes 1 + 24h/interim ≈ 7 rows/session/24h.
No backfill: reads dedupe with argMax(…, ingested_at) and the existing rows age
out of the invariant's trailing window.

WHY: the previous rule was "re-emit whenever the signature changed", i.e. once
per 5-min tick for the whole life of a live session. Measured on 2026-07-28 that
produced 27,061 session-summary rows over 702 sessions (avg 38.5/session, worst
486, ~1,800 new rows/day) of which 26,359 (97.4%) were immediately superseded.
Nothing consumed them — the read contract already takes only the newest.

READ CONTRACT UNCHANGED: activity.events is append-only, a session may still
accumulate a handful of summary rows (first-seen / interim / final), and a
consumer takes the LATEST per session with `argMax(<field>, ingested_at)`
grouped by `session` — the newest emitted rollup is still the most complete one,
because every emit re-reads the WHOLE transcript. See
scripts/session-analysis/insights.py.

The settle window MUST stay well under validation/invariants.py's
SUMMARY_ORPHAN_GRACE_HOURS (2h), which flags a settled prompt-session that has
no summary; 20 min + a 5-min tick leaves a wide margin.

The host is stamped by the collector daemon (ACTIVITY_HOST), so this runs
unchanged on both workbench + laptop. Skips the synthetic `subagents/` and `wf_*`
transcript dirs exactly like tailer.py (shared iter_transcripts).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

# The collector ROOT (this file's grandparent) holds `changed_paths.py`, the one
# definition of the `changed_paths*` payload block, shared with the opencode
# summariser. It resolves in both layouts: in the repo it is
# scripts/collector/, and when deployed it is ~/.config/activity-collector/
# (home-manager lands `claude/` as a real dir of per-file symlinks beside it).
# 🔴 Do NOT `.resolve()`. Resolving walks INTO /nix/store and anchors the import
# to the store path rather than the deployed tree — the trap that broke the
# browser receiver's spool_emit import and is documented at
# opencode/_shared.py's spool bridge. Stated honestly: for THIS import the two
# happen to coincide today, because the store dir mirrors the deployed one; the
# ban is upheld because the coincidence is not a property anyone maintains.
# `__file__` is absolute in Python 3, so plain parent traversal is enough.
# APPENDED, not inserted at 0: the collector root also holds `collector.py`,
# `deadman.py`, `invocation.py` and `ch_regrowth.py`, and putting it ahead of
# this file's own directory would let any of those shadow a sibling module.
_COLLECTOR_ROOT = str(Path(__file__).parent.parent)
if _COLLECTOR_ROOT not in sys.path:
    sys.path.append(_COLLECTOR_ROOT)

import changed_paths as CP  # noqa: E402

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


# --------------------------------------------------------------------------- #
# Rollup
# --------------------------------------------------------------------------- #
def _empty_rollup() -> dict:
    """The zero rollup.

    🔴 Its `changed_paths*` block is the UNOBSERVABLE one (all None), not an
    empty list. This function is returned verbatim for a transcript that could
    not be opened or held no parseable JSON, and for that session "no paths" is
    a statement about our reading, not about the session. `files_modified` etc.
    stay ints at 0 — see `stats_unavailable` below for why the legacy counters
    keep their type.
    """
    r = {
        "tool_counts": {},
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_creation_tokens": 0,
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
        "start_ts": None,
        "end_ts": None,
        "cwd": "",
        "unreadable": False,
        # Which of the derived statistic GROUPS this rollup could not observe.
        # Sentinels: "files" (files_modified / lines_added / lines_removed /
        # changed_paths) and "git" (git_commits / git_pushes). Empty list = every
        # group was observed, so its zeros are real zeros.
        #
        # 🔴 The legacy integer counters deliberately KEEP their int type here
        # even when a group is unavailable, because they are read live by
        # scripts/session-analysis/insights.py and pinned by
        # scripts/validation/invariants.py, and the brief for this change is
        # additive-only: adding a field is safe, changing a field's TYPE is not.
        # So on the claude side `stats_unavailable` is the authority on whether a
        # 0 means anything, and `changed_paths` (a NEW field, with no existing
        # consumer to break) carries the distinction structurally as None.
        # The opencode summariser, whose counters have no consumer at all and
        # were a constant 0 lie, nulls them outright — see its build_rollup.
        "stats_unavailable": [],
    }
    # The zero rollup is returned VERBATIM for a transcript that could not be
    # opened or parsed, so it must start out unobservable rather than
    # observably-empty. build_rollup() overwrites both halves once it knows.
    return _mark_unobservable(r)


def _mark_unobservable(r: dict) -> dict:
    """Set BOTH halves of the unobservable verdict, together.

    🔴 One rule, one place — and here the two halves are a matched pair that a
    reader will treat as contradictory if they ever disagree: a `changed_paths`
    of None beside `stats_unavailable == []` says "unknown" and "everything was
    observed" in the same payload. That exact disagreement was shipped and
    caught by `test_an_unopenable_file_reports_ABSENT`, because
    `summarize_transcript` returns `_empty_rollup()` directly on OSError and so
    never reached build_rollup's branch.
    """
    r.update(CP.unobservable())
    r["stats_unavailable"] = ["files", "git"]
    return r


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
    start_iso = end_iso = None
    cwd = ""

    for obj in objects:
        if not isinstance(obj, dict):
            continue
        # Skip subagent/sidechain turns FIRST — before the timestamp min/max below —
        # so their timestamps don't inflate the session's duration_minutes.
        if obj.get("isSidechain"):
            continue  # subagent's own turns — not this session's work
        iso = obj.get("timestamp")
        if iso:
            if start_iso is None or iso < start_iso:
                start_iso = iso
            if end_iso is None or iso > end_iso:
                end_iso = iso
        if not cwd and obj.get("cwd"):
            cwd = obj.get("cwd") or ""

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

        elif typ == "assistant":
            msg = obj.get("message") or {}
            r["assistant_message_count"] += 1
            model = msg.get("model")
            if model:
                models.add(model)
            usage = msg.get("usage") or {}
            r["input_tokens"] += _int(usage.get("input_tokens"))
            r["output_tokens"] += _int(usage.get("output_tokens"))
            # input_tokens is only the NON-cached input; the bulk of real input
            # volume is cached — sum both so the report can show honest input.
            r["cache_read_tokens"] += _int(usage.get("cache_read_input_tokens"))
            r["cache_creation_tokens"] += _int(usage.get("cache_creation_input_tokens"))
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
    r["cwd"] = cwd
    r["start_ts"] = to_ch_ts(start_iso)
    r["end_ts"] = to_ch_ts(end_iso)
    s, e = ch_ts_to_epoch(r["start_ts"]), ch_ts_to_epoch(r["end_ts"])
    if s is not None and e is not None and e >= s:
        r["duration_minutes"] = round((e - s) / 60)
    r["unreadable"] = (r["user_message_count"] == 0
                       and r["assistant_message_count"] == 0)
    # 🔴 THE OBSERVABILITY BRANCH. `unreadable` already means "this transcript
    # yielded no messages at all", i.e. we never got far enough to see a single
    # tool_use block — so the file set is UNKNOWN, not empty, and the paths must
    # come back None rather than []. Emitting [] here would recreate the exact
    # defect this PR fixes one source over: a manufactured empty list that a
    # downstream associator reads as "this session touched nothing".
    if r["unreadable"]:
        _mark_unobservable(r)
    else:
        r.update(CP.summarize(files, cwd))
        r["stats_unavailable"] = []
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
# Settle policy (env-tunable — mirrors Layer B's INSIGHT_SETTLE_HOURS style)
# --------------------------------------------------------------------------- #
# Idle time after which a transcript counts as SETTLED and gets its (final)
# rollup. 20 min = 4 timer ticks: long enough that a normal think/read pause
# mid-session doesn't count as settled, short enough that a finished session is
# summarised promptly and stays far inside the 2h orphan-invariant grace.
DEFAULT_SETTLE_MINUTES = 20.0
# Backstop for a session that never settles (an all-day agent run, or a host that
# reboots mid-session): at most ONE interim rollup per this many hours. 4h bounds
# a 12h session to ~5 rows instead of ~144, while keeping the report from being
# more than 4h stale on a session that is still going.
DEFAULT_INTERIM_HOURS = 4.0

STATE_VERSION = 2


def _env_number(name: str, default: float) -> float:
    """Non-negative float from the environment, falling back to `default` on
    anything unparseable (a typo in the unit file must not kill the timer)."""
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        val = float(raw)
    except ValueError:
        return default
    return val if val >= 0 else default


def settle_seconds() -> float:
    """Idle seconds after which a transcript is SETTLED. 0 disables the gate
    (every changed transcript emits — the pre-emit-on-settle behaviour)."""
    return _env_number("CLAUDE_SUMMARY_SETTLE_MINUTES", DEFAULT_SETTLE_MINUTES) * 60.0


def interim_seconds() -> float:
    """Minimum seconds between interim emits for a still-active session.
    0 disables interim emits entirely (only first-seen + settled emit)."""
    return _env_number("CLAUDE_SUMMARY_INTERIM_HOURS", DEFAULT_INTERIM_HOURS) * 3600.0


# --------------------------------------------------------------------------- #
# State (idempotency + settle bookkeeping)
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
    st = _stat(path)
    return None if st is None else f"{st.st_mtime_ns}:{st.st_size}"


def _stat(path: str):
    try:
        return os.stat(path)
    except OSError:
        return None


def load_state(path: Path) -> dict:
    """path -> {"sig": str, "emitted_at": float|None} for every transcript we
    have emitted for.

    Tolerates EVERYTHING: a missing file, truncated/garbage JSON, the v1 schema
    ({"sigs": {path: sig}}), or individual junk entries. Anything it cannot make
    sense of degrades to "we have never emitted for this" — which re-emits once
    and re-converges, rather than crashing the timer.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    # v1: {"version":1,"sigs":{path: "mtime:size"}} — migrate in place. An entry
    # with no known emit time is treated as "emitted long ago", so a changed
    # transcript emits once on the first post-upgrade tick and then settles in.
    raw = data.get("sessions")
    if not isinstance(raw, dict):
        legacy = data.get("sigs")
        if not isinstance(legacy, dict):
            return {}
        return {str(p): {"sig": str(s), "emitted_at": None}
                for p, s in legacy.items() if isinstance(s, str)}
    out: dict = {}
    for p, ent in raw.items():
        if isinstance(ent, dict) and isinstance(ent.get("sig"), str):
            at = ent.get("emitted_at")
            out[str(p)] = {"sig": ent["sig"],
                           "emitted_at": at if isinstance(at, (int, float)) else None}
        elif isinstance(ent, str):  # tolerate a flattened entry
            out[str(p)] = {"sig": ent, "emitted_at": None}
    return out


def save_state(path: Path, sessions: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps({"version": STATE_VERSION, "sessions": sessions},
                   separators=(",", ":")),
        encoding="utf-8",
    )
    os.replace(tmp, path)  # atomic


# --------------------------------------------------------------------------- #
# The emit decision (pure — unit-tested with an injected clock)
# --------------------------------------------------------------------------- #
def emit_decision(prev: dict | None, sig: str, mtime: float, now: float,
                  settle_s: float, interim_s: float) -> tuple[bool, str]:
    """(should_emit, reason) for one transcript. See the module docstring.

    Reasons: unchanged | settled | settled-recently | first-seen | interim |
    active.
    """
    if prev and prev.get("sig") == sig:
        return (False, "unchanged")
    idle = now - mtime
    last = prev.get("emitted_at") if prev else None
    known_last = isinstance(last, (int, float))
    if settle_s <= 0:
        # Documented escape hatch, unchanged: 0 disables the settle gate
        # ENTIRELY and restores the pre-emit-on-settle "every change emits"
        # behaviour. Rate-limiting it would make the knob a no-op, so the
        # bounding below deliberately does not apply here.
        return (True, "settled")
    if idle >= settle_s:
        # 🔴 The settled branch is rate-limited on `emitted_at`, exactly as the
        # interim branch below is. It was NOT, until 2026-08-02, and that
        # asymmetry is the whole bug:
        #
        #   an ACTIVE session was bounded to one emit per `interim_s`, while a
        #   session that went idle BETWEEN BURSTS was unbounded — every burst
        #   followed by >= settle_s of quiet re-entered this branch and shipped
        #   a fresh full rollup. That is the ordinary `claude --resume` shape,
        #   so the bound was missing from precisely the common case. Measured
        #   over 30 days: 31,815 rows across 482 sessions, median 2/session but
        #   p90 209, max 572, mean 66 — against a documented invariant
        #   (`session_summary_rows_bounded`, validation/invariants.py) that
        #   flags >24 rows/session/24h.
        #
        # The fix is the missing consult, not a clamp on a counter: the same
        # `emitted_at` the interim branch already reads, with the same interval,
        # so there is ONE knob (CLAUDE_SUMMARY_INTERIM_HOURS) rather than two
        # that can disagree. Worst case per session per 24h becomes
        # 1 first-seen + 24h/interim_s ≈ 7 at the 4h default, well under the
        # invariant's cap of 24.
        #
        # 🔴 The legitimate case is deliberately PRESERVED, two ways:
        #   * a session with no known last-emit (never emitted, or a v1/corrupt
        #     state entry) ALWAYS emits — so a session's first rollup is never
        #     deferred, and `session_summary_no_orphans` cannot start failing;
        #   * a session that settles, is resumed DAYS later and settles again
        #     has an `emitted_at` far older than the interval, so it emits
        #     again. That is the case the rate limit must not swallow, and it is
        #     pinned by test_decision_settled_reemits_after_a_long_gap and
        #     test_resumed_after_settle_reemits_the_final_rollup.
        #
        # Cost, stated honestly: a deferred settle means the newest row can lag
        # the transcript's final content by up to `interim_s`. Nothing is lost —
        # the deferral does NOT record the new signature (see run()), so the
        # session still owes a rollup and emits on the first tick past the
        # window, and reads take argMax(…, ingested_at) anyway.
        if not known_last:
            return (True, "settled")
        if interim_s <= 0 or (now - last) >= interim_s:
            return (True, "settled")
        return (False, "settled-recently")
    if not prev:
        return (True, "first-seen")
    if not known_last:
        # Unknown last-emit (v1 state / corrupt entry): emit once to establish it.
        return (True, "interim")
    if interim_s > 0 and (now - last) >= interim_s:
        return (True, "interim")
    return (False, "active")


# Flush the seen-set to disk every this-many emits so an interrupted run
# (e.g. a first-run full-corpus backfill SIGTERMed on the systemd start timeout)
# resumes from where it left off instead of re-emitting everything.
CHECKPOINT_EVERY = 25


# --------------------------------------------------------------------------- #
# Run
# --------------------------------------------------------------------------- #
def run(now: float | None = None) -> int:
    """One pass over every transcript. `now` is injectable so the settle policy
    is testable with a fake clock (no sleeping)."""
    now = time.time() if now is None else now
    settle_s = settle_seconds()
    interim_s = interim_seconds()
    roots = projects_roots()
    sp = state_path()
    prev = load_state(sp)
    emit = emit_path()

    # Start from the prior state and update a session's entry ONLY after it has
    # been (re-)emitted (or confirmed unchanged/deferred), checkpointing
    # periodically. This makes a long backfill RESUMABLE: whatever was flushed is
    # skipped next run rather than re-emitted, so an interrupted scan converges
    # instead of storming duplicates. A session reached-but-not-yet-emitted at
    # interrupt is simply re-emitted next run (append-only read contract dedupes).
    new_state: dict = dict(prev)
    seen: set = set()
    reasons: dict = {}
    emitted = 0
    scanned = 0
    since_checkpoint = 0
    for path, session in iter_transcripts(roots):
        st = _stat(path)
        if st is None:
            continue
        sig = f"{st.st_mtime_ns}:{st.st_size}"
        seen.add(path)
        scanned += 1
        should, reason = emit_decision(prev.get(path), sig, st.st_mtime, now,
                                       settle_s, interim_s)
        reasons[reason] = reasons.get(reason, 0) + 1
        if not should:
            # Carry the PRIOR entry forward untouched. Deliberately do NOT record
            # the new signature: the session still owes us a rollup, and leaving
            # the old signature in place is what makes the next tick re-evaluate
            # it (and eventually emit once it settles).
            if path in prev:
                new_state[path] = prev[path]
            continue
        rollup = summarize_transcript(path)
        emit_event(emit, build_event(session, rollup))
        new_state[path] = {"sig": sig, "emitted_at": now}  # ONLY after a successful emit
        emitted += 1
        since_checkpoint += 1
        if since_checkpoint >= CHECKPOINT_EVERY:
            save_state(sp, new_state)  # atomic tmp+rename checkpoint
            since_checkpoint = 0

    # Final save: prune entries for transcripts that no longer exist (we saw
    # every current path this pass), keeping the state file from growing forever.
    new_state = {p: s for p, s in new_state.items() if p in seen}
    save_state(sp, new_state)
    breakdown = " ".join(f"{k}={v}" for k, v in sorted(reasons.items()))
    print(f"session-tailer: scanned={scanned} emitted={emitted} "
          f"[{breakdown}] settle={settle_s / 60:g}m interim={interim_s / 3600:g}h "
          f"state={sp}")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
