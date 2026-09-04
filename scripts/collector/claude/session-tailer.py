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

SECOND EVENT STREAM: MENTIONS. The same pass also scans each session's ASSISTANT
TEXT for cross-platform references — clawgate task ids, GitHub issues/PRs,
ClickUp task ids — and emits one event per distinct mention:

    source  = mentions
    kind    = mention-detected
    ts      = the assistant turn's own instant (not the session start)
    payload = platform / reference_id / url / context / candidates

It rides here rather than in a PostToolUse hook because this file ALREADY walks
every transcript and ALREADY extracts assistant content blocks — a hook would
add a ~50 ms Python start to every tool call to re-read data this pass has in
hand. The cost of that choice, stated plainly: a mention is telemetry-visible on
the emit-on-settle schedule below (up to ~20 min), not in real time. Nothing
consumes it in real time.

🔴 MENTIONS ARE DEDUPED PER SESSION, IN THE STATE FILE. A session re-emits its
rollup up to ~7 times a day (first-seen / interim / settled), and without a
per-session ledger every mention would be re-emitted on each of those. That is
the exact row-amplification shape documented below for session-summary itself,
one source over, so it is closed the same way: the state entry carries the
mention keys already emitted, and only NEW ones ship. Deliberately capped
(MENTIONS_PER_SESSION_CAP) so a transcript full of references cannot grow the
state file without bound.

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
# The mention scanner lives at the collector ROOT for the same reason
# changed_paths does: it is shared (this tailer emits the telemetry, and
# scripts/mention-open.py resolves a click with the identical rules), and a
# module beside `claude/` needs its OWN `home.file` entry in nix/home.nix —
# `claude/` is a `recursive = true` directory source, a file next to it is not.
# `scripts/tests/test_collector_deploy_declares.py` derives that requirement
# from THIS import line, so the deploy cannot silently omit it.
import mention_scan as MS  # noqa: E402

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
from tailer import COMMAND_NAME, classify, extract_blocks


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
# 🔴 A BOUND ON WHAT MAY BECOME A PAYLOAD KEY. These maps ship to ClickHouse for
# every session on both hosts, and their keys come from transcript text.
# `classify()` returns `(cname + " " + cargs)`, so an EMPTY or whitespace-only
# `<command-name>` makes `ctext` the ARGS — and the first word of operator
# free-text then became a key. Three inputs reached it: an empty name tag, a
# whitespace name tag, and a `<command-name>` holding prose with no args tag. A
# 4,000-char name also went straight through, multiplying the payload.
#
# Measured 2026-08-29: 37 distinct real command names across the corpus,
# every one `/name`, none empty, none containing whitespace, longest 26 chars.
# So this is a LATENT hazard with zero live instances — bounded here rather than
# after it ships something.
#
# 🔴 `:` IS ADMITTED, `/` IS NOT, AND A PATH-DERIVED PREFIX IS DROPPED RATHER
# THAN REJECTED. All three follow from what the corpus actually carries.
#
# A skill identity can be namespace-qualified: `plugin:skill` for a plugin
# skill, `<dir>:<skill>` for a directory-scoped one. A bound drawn around the 37
# bare names that exist today would silently REJECT a plugin skill the first
# time one is enabled (14 are cached on this host) — ClickHouse would report it
# as never used while `find-session --skill` still found the session, which is
# the silent zero this work removes, reintroduced inside the guard protecting it.
#
# 🔴 But the directory form's prefix is a PATH, and a path is per-run-unique.
# Keeping the whole string would make an unbounded-cardinality ClickHouse map
# key out of a filesystem path, for every session on both hosts, and this repo
# is PUBLIC: `home/zach/workspace/clients/<name>/.env` fits the same shape.
#
# ⚠ THIS IS A FORWARD-LOOKING BOUND, NOT ONE BACKED BY LIVE INSTANCES, and an
# earlier revision of this comment claimed otherwise. Measured 2026-08-29 over
# the 837 SESSION transcripts these readers actually walk: **0**
# namespace-qualified values on either route (71 distinct `attributionSkill`,
# 67 distinct `Skill` `input.skill`, none containing `/` or `:`). The
# `.claude/worktrees/agent-<hex>:remix` shape exists in exactly 2 files, and
# BOTH are `subagents/` transcripts — a tier `iter_transcripts` excludes by
# design. The retracted "10 distinct" figure came from a raw grep over the whole
# directory, which is both the wrong count and the wrong corpus. The rule stands
# on the documented identity shape and on cardinality, not on a measurement.
#
# So `canonical_skill_name` drops a path-derived prefix and keeps the SKILL,
# and a value that is a bare path with no skill after it is REJECTED and
# COUNTED. `apps/web:deploy` and `apps/api:deploy` therefore both record
# `deploy`: the identity we measure is the skill, not where it was loaded from.
# That is a deliberate loss — the alternative is one key per worktree.
#
# 🔴 DUPLICATED, DELIBERATELY, in `scripts/lib/transcript_search.py`, and pinned
# byte-identical by `test_the_two_skill_name_bounds_agree`. It cannot be a
# shared import: `nix/home.nix` deploys `scripts/collector/claude` ALONE to
# `~/.config/activity-collector/claude/`, so an import from `scripts/lib` would
# pass every test here and break the running daemon on both hosts.
SKILL_NAME_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$"
SKILL_NAME_RE = re.compile(SKILL_NAME_PATTERN)
# Bounds the WHOLE identity before any path-strip. Generous next to the 64-char
# name bound because a legitimate directory prefix can be long; its job is to
# stop a multi-kilobyte blob being rewritten into a clean key by the strip.
MAX_IDENTITY_CHARS = 256


def canonical_skill_name(raw):
    """The key a skill identity may become, or None if it may not become one.

    🔴 ONE function for all three routes. The previous revision bounded only the
    typed route, so the emitter and the search disagreed about what a name IS —
    `find-session --skill "not a valid name"` matched a session ClickHouse
    reported as having used no such skill. Every route goes through here.
    """
    # A non-string identity is not a name. Without this the two readers
    # disagreed: the tailer's own `isinstance` pre-guard dropped `42` while the
    # search recorded the key `"42"`, so `--skill 42` matched a session
    # ClickHouse reported as having used no such skill.
    if not isinstance(raw, str):
        return None
    s = raw.strip().lstrip("/").strip()
    if not s:
        return None
    # 🔴 CHECKED BEFORE THE PATH-STRIP, and that order is the whole point. The
    # strip examines only the tail, so a value whose HEAD is prose or megabytes
    # of junk used to be rewritten into a clean key: `not a valid
    # name/x:handoff` recorded `handoff`, and `("z"*4000)/a:handoff` did too —
    # both REJECTED AND COUNTED before the strip existed. That turned "malformed,
    # visible in unusable_skill_names" into "attributed to a real skill, audit
    # trail clean", which is worse than what it replaced.
    if len(s) > MAX_IDENTITY_CHARS or any(c.isspace() for c in s):
        return None
    if "/" in s:
        # A path-derived prefix. Keep what follows the FIRST colon after it; a
        # bare path is not a skill identity at all.
        #
        # `split`, not `rsplit`: for a directory-scoped PLUGIN skill
        # `apps/web:cloudflare:wrangler`, the directory is `apps/web` and the
        # identity is `cloudflare:wrangler`. `rsplit` returned `wrangler`,
        # colliding with a bare `wrangler` and contradicting this module's own
        # argument that a plugin namespace is bounded and meaningful, so it is
        # not truncated.
        if ":" not in s:
            return None
        s = s.split(":", 1)[1].strip()
    if not s or not SKILL_NAME_RE.match(s):
        return None
    return s


def _note_name(bag: dict, raw, rollup: dict) -> None:
    """Record `raw` as a key of `bag`, or count it as unusable. Never both."""
    name = canonical_skill_name(raw)
    if name is None:
        if raw is not None and str(raw).strip():
            rollup["unusable_skill_names"] += 1
        return
    bag[name] = bag.get(name, 0) + 1


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
        # WHICH SKILLS THIS SESSION USED — THREE INDEPENDENTLY OBSERVED signals,
        # deliberately not merged into one number.
        #
        # `skills_used`    {skill: assistant-records attributed to it}, read off
        #                  Claude Code's own `attributionSkill` field. The only
        #                  signal that sees a skill which AUTO-FIRED from its
        #                  description rather than being invoked explicitly.
        # `skills_invoked` {skill: Skill tool_use blocks}, from a `Skill`
        #                  tool_use's `input.skill`. The explicit-invocation
        #                  route. 🔴 `input.args` beside it is operator
        #                  free-text — take the NAME only.
        # `commands_typed` {command: times typed}, from `<command-name>`. Named
        #                  for what it actually holds: it includes BUILT-INS
        #                  (`/login`, `/clear`), which are not skills, so a
        #                  reader wanting skills must intersect with the skill
        #                  list.
        #
        # 🔴 NO ONE OF THEM IS A SUPERSET OF THE OTHERS, so a "simplification"
        # down to one field loses real usage. Measured 2026-08-29 over the
        # workbench corpus, counting SESSIONS (subagent transcripts excluded)
        # with THIS module's own counting rule:
        #   browser    50 attributed,   0 typed  ->  50 attributed-only, 0 typed-only
        #   clawgate   72 attributed,  28 typed  ->  44 attributed-only, 0 typed-only
        #   activity    8 attributed,   3 typed  ->   5 attributed-only, 0 typed-only
        #   handoff   383 attributed, 346 typed  ->  40 attributed-only, 3 TYPED-ONLY
        # ⚠ An earlier revision cited `clawgate` 4 typed-only as the evidence for
        # the typed direction. That was WRONG — it came from a raw file grep,
        # which matches `<command-name>` inside quoted TOOL OUTPUT, the exact
        # false positive this module narrows against. clawgate's typed-only is
        # **0**. `handoff` (3) is the real live instance; do not re-derive either
        # number with a grep.
        #
        # Same convention as `tool_counts`, NOT the `changed_paths` one: an
        # unreadable transcript leaves these `{}` and lets `unreadable` /
        # `stats_unavailable` carry the verdict, rather than encoding
        # unobservability a second time in a different shape.
        "skills_used": {},
        "skills_invoked": {},
        "commands_typed": {},
        # Skill/command identities this session offered that could NOT become a
        # payload key: rejected by `canonical_skill_name` (prose, a bare path, a
        # 4,000-char blob), AND the command turn whose only `<command-name>` tag
        # was empty — that one has no value to reject, so it is counted at the
        # call site instead. Counted, never dropped silently: a filter nobody can
        # count is indistinguishable from one wired to nothing.
        "unusable_skill_names": 0,
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
        # Count of tool_use blocks whose file-path argument could not be read as
        # a path at all (a non-string, or whitespace-only). COUNTED, never
        # dropped silently and never coerced into a path — see the guard in
        # build_rollup. It is a diagnostic about the TRANSCRIPT, not a
        # measurement of the session: 0 means "no malformed entry was seen",
        # including on a transcript we could not read at all (where
        # `unreadable` / `stats_unavailable` already carry the verdict).
        "unusable_file_paths": 0,
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


def build_rollup(objects: list[dict], *, absolute_root: str = "") -> dict:
    """Deterministic per-session rollup from parsed transcript JSON objects.

    Mirrors the built-in `/insights` session-meta fields so insights.py is a
    drop-in. `unreadable` is True when the transcript yielded no messages at all
    (so the report can show it honestly rather than inventing content).

    `absolute_root` is OPT-IN and OFF for the emit path. Given an absolute
    directory it adds `changed_paths.ABSOLUTE_KEYS` — the entries this session
    named ABSOLUTELY that resolve under that root, made root-relative (see
    `changed_paths.absolute_under` for why that is the only re-frameable subset).
    🔴 The root goes IN and the answer comes OUT; the raw path set never leaves
    this function. That is deliberate: the raw set is dominated by agent
    scratchpad and temp-worktree paths (measured 86.1% outside cwd — 3,913
    distinct paths over the 636 transcripts this tailer walks, 543 under cwd,
    which reproduces the 14.3% in `changed_paths`'s own header), and handing it
    to a caller is how one of them ends up persisted somewhere.

    🔴 `run()` never passes it, so the emitted payload — which is `json.dumps` of
    this whole dict — is byte-for-byte what it was. Pinned by a test."""
    r = _empty_rollup()
    tool_counts: dict = r["tool_counts"]
    skills_used: dict = r["skills_used"]
    skills_invoked: dict = r["skills_invoked"]
    commands_typed: dict = r["commands_typed"]
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
            cmd_name = None
            cmd_tag_empty = False
            for raw in extract_blocks(content):
                if raw and raw.lstrip().startswith("[Request interrupted"):
                    r["user_interruptions"] += 1
                # 🔴 Read the NAME TAG ITSELF, never `classify`'s text. classify
                # returns `cname + " " + cargs`, so an empty or whitespace-only
                # name tag makes the ARGS the whole string and its first word
                # then looks exactly like a legitimate name — charset-valid,
                # length-valid, and operator free-text. A bounded charset alone
                # does NOT close that: the first fix for this tried it and the
                # regression test still leaked `harbourPermit…`. Parsing the tag
                # is what distinguishes "no name" from "a name".
                #
                # 🔴 Latch the first NON-EMPTY tag, not the first tag. Latching
                # any match let an EMPTY tag in an earlier block suppress a real
                # command in a later one — losing a genuine typed command AND
                # counting nothing, so the loss was invisible. That regression
                # was introduced by the fix above; `cmd_tag_empty` is what makes
                # the discarded case observable instead.
                # `finditer`, not `search`, so a block holding an empty tag
                # FOLLOWED by a real one still sees the real one.
                #
                # 🔴 `finditer` IS LOAD-BEARING, and a previous revision of this
                # comment wrongly called it "defensive only" — which invited a
                # revert that would silently lose a real command. Whether it
                # matters depends on `<command-args>`:
                #
                #   empty tag + real tag, NO args   -> `classify()` runs its own
                #     `search`, hits the empty tag, yields empty text, returns
                #     None. The turn is never a command and this branch does not
                #     run at all — `finditer` changes nothing.
                #   empty tag + real tag, WITH args -> `classify()` builds
                #     `(cname + " " + cargs).strip()`, which is truthy, so it
                #     returns ("command", …) and this branch DOES run. With
                #     `search` the real command is lost; with `finditer` it is
                #     counted.
                #
                # Both shapes have zero live instances (0 of the session
                # transcripts measured 2026-08-29 carry two tags in one turn), and the no-args half cannot be
                # fixed here — its gate is `classify()`, shared with tailer.py's
                # message stream, where a change moves `kind=command` for every
                # row that source has emitted. Both halves are pinned by tests.
                if raw:
                    for m in COMMAND_NAME.finditer(raw):
                        val = m.group(1).strip()
                        if val:
                            if cmd_name is None:
                                cmd_name = val
                        else:
                            cmd_tag_empty = True
                res = classify(raw)
                if res is not None and genuine is None:
                    genuine = res  # (kind, text)
            if genuine is not None:
                r["user_message_count"] += 1
                # `classify` returns ("command", "/name args") for a slash
                # invocation — take the NAME only, so args (which carry operator
                # free-text) never reach the payload.
                if genuine[0] == "command":
                    if cmd_name is not None:
                        _note_name(commands_typed, cmd_name, r)
                    elif cmd_tag_empty:
                        # A command turn whose only name tag was empty. Counted,
                        # not dropped: `_note_name` cannot see this case because
                        # it never receives a value for it.
                        r["unusable_skill_names"] += 1

        elif typ == "assistant":
            msg = obj.get("message") or {}
            r["assistant_message_count"] += 1
            # Claude Code stamps the ACTIVE skill on the assistant record, at the
            # record's top level — a sibling of `message`, not inside it.
            skill = obj.get("attributionSkill")
            if isinstance(skill, str) and skill.strip():
                _note_name(skills_used, skill, r)
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
                    # The EXPLICIT-invocation route. 🔴 `input.skill` ONLY —
                    # `input.args` sitting beside it is operator free-text
                    # ("look up civitai user id …"), and this payload is public.
                    if name == "Skill":
                        sinp = block.get("input")
                        if isinstance(sinp, dict):
                            _note_name(skills_invoked, sinp.get("skill"), r)
                    inp = block.get("input")
                    # 🔴 A tool_use `input` that is not a dict is a MALFORMED
                    # TRANSCRIPT, not a caller bug — `or {}` kept a truthy list
                    # here and the next `.get` raised AttributeError, killing the
                    # whole 5-minute pass for every OTHER session too. Count it
                    # and carry on; `tool_counts` above is still honest.
                    if not isinstance(inp, dict):
                        if inp is not None:
                            r["unusable_file_paths"] += 1
                        inp = {}
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
                        # 🔴 VALIDATE, DO NOT COERCE. `changed_paths.summarize`
                        # RAISES on a non-string or blank entry, and this run()
                        # had no per-session try — so ONE malformed `file_path`
                        # anywhere in ~/.claude/projects aborted the pass and
                        # stopped ALL claude session-summary emission. Measured
                        # reachability is 0 across the 600 transcripts the tailer
                        # walks (2026-08-11), but that is a property of today's
                        # data, not of this code, and it is live on both hosts.
                        #
                        # Rejected: `str(fp)` — the coercion the opencode side
                        # does. It turns `["a.py"]` into the string "['a.py']",
                        # which `to_repo_relative` accepts as a plain relative
                        # path and EMITS: a manufactured path, in the one module
                        # whose whole contract is that nothing is invented. A
                        # value we cannot read is counted (`unusable_file_paths`)
                        # and excluded — never guessed at, never dropped in
                        # silence. Note `files` would also raise TypeError on an
                        # unhashable list/dict before ever reaching summarize().
                        if isinstance(fp, str) and fp.strip():
                            files.add(fp)
                            lang = lang_for_path(fp)
                            if lang:
                                languages[lang] = languages.get(lang, 0) + 1
                        elif fp:
                            r["unusable_file_paths"] += 1
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
        if absolute_root:
            # All-None, not []. An empty list would say "the root was checked and
            # nothing resolved under it" — a measurement — about a session whose
            # file set we never read at all.
            r.update(CP.absolute_unobservable())
    else:
        r.update(CP.summarize(files, cwd))
        if absolute_root:
            r.update(CP.absolute_under(files, absolute_root))
        r["stats_unavailable"] = []
    return r


def load_transcript(path: str) -> list[dict] | None:
    """Every parseable JSON line of a transcript, or None when the file could not
    be opened OR held no parseable JSON at all.

    Split out of `summarize_transcript` so ONE read serves BOTH consumers — the
    rollup and the mention scan. Re-reading a 600-transcript corpus twice per
    tick to answer two questions about the same bytes is exactly the kind of
    waste that gets noticed as a timer overrun months later.

    None is the UNREADABLE verdict and `[]` is unreachable: `parsed_any` is set
    on the same line that appends, so a non-None return always has content. The
    two are still kept distinct at the call site (`is None`) rather than relying
    on truthiness, because "we read nothing" and "there was nothing" are
    different claims everywhere else in this file."""
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
        return None
    return objects if parsed_any else None


def summarize_transcript(path: str, *, absolute_root: str = "") -> dict:
    """Read a transcript fully and return its rollup. A file that can't be opened
    or contains no parseable JSON is flagged `unreadable` (never fabricated).

    `absolute_root` is forwarded to `build_rollup` — and applied to the two
    early returns below as well, so a caller that asked for the block gets it in
    EVERY outcome. An absent key and a None key are different claims, and the
    OSError path is exactly where the difference was shipped once before (see
    `_mark_unobservable`)."""
    objects = load_transcript(path)
    if objects is None:
        return _unreadable_rollup(absolute_root)
    return build_rollup(objects, absolute_root=absolute_root)


def _unreadable_rollup(absolute_root: str) -> dict:
    """The zero rollup, flagged unreadable, carrying the absolute block if asked."""
    r = _empty_rollup()
    r["unreadable"] = True
    if absolute_root:
        r.update(CP.absolute_unobservable())
    return r


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
# Mentions (source=mentions) — see the module docstring
# --------------------------------------------------------------------------- #
# Per-session ceiling on DISTINCT mentions. Bounds both the emitted rows and the
# state file (each key is stored so it is never re-emitted). 200 is far above any
# real session — the point is that a pathological transcript cannot make the
# state file grow without limit, not that 200 is a meaningful threshold.
MENTIONS_PER_SESSION_CAP = 200

# Cheap pre-filter before the regex pass: a text block containing none of these
# literals cannot match any pattern this tailer scans for, and does not need
# scanning. Measured over one 24h window it skipped 81% of assistant text blocks.
#
# 🔴 DERIVED FROM THE SCANNER'S OWN LEDGER, NEVER HAND-MAINTAINED. This used to
# be the literal `("#", "868")`, and that made it the exact place a widening goes
# to die: every telemetry-only shape the scanner learned — `audit-pr 1291`,
# `gh pr view 1291`, `clawgate task 370` — contains neither literal, so the
# regexes would have been reachable in a unit test calling `scan_mentions()`
# directly and DEAD in production, with the whole suite green. Deriving it means
# a pattern added to `PATTERN_LEDGER` widens the filter in the same commit,
# because there is nowhere else to put the fact.
#
# 🔴 AND IT IS THE TELEMETRY PROFILE, not the default. `mention_hints()` with no
# argument returns the TERMINAL profile's two literals — the old value — which
# would look correct, pass a "derived from the ledger" review, and still skip
# every new shape.
_MENTION_HINTS = MS.mention_hints(MS.PROFILE_TELEMETRY)

# Where the operator's generated repo mapping lives — the same per-host file
# `scripts/mention-open.py` reads, and for the same reason: it names PRIVATE
# repositories, so it lives OUTSIDE every checkout and this repo is public.
# Nothing breaks when it is absent; a `#N` simply stays unattributed.
#
# 🔴 THIS EXPRESSION IS THE THIRD COPY OF ONE PATH — the generator writes it, the
# hint handler reads it, and now so does this tailer. They agree only by
# coincidence, so `scripts/tests/test_mention_open.py` imports all three modules
# and pins the three answers equal. Move one and the mapping is generated into
# a file nothing loads, or read from a file nothing writes, with every suite green.
#
# 🔴 A FUNCTION, NOT A MODULE CONSTANT, and that is not a style choice. A
# constant is evaluated at IMPORT, so a test setting `MENTION_OPEN_KNOWN_REPOS`
# afterwards changes nothing and the tailer reads the OPERATOR'S REAL mapping —
# which names private repositories. `scripts/mention-open.py` records the same
# defect twice (`load_known_repos`, `discover_repos`): once measured running 91
# real `git remote` calls from a test that believed it had an empty fixture.
def mention_repos_path() -> Path:
    return Path(
        os.environ.get("MENTION_OPEN_KNOWN_REPOS")
        or Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
        / "mention-open" / "known_repos.json")


def load_mention_repos(path: Path | None = None) -> dict:
    """{repo name: "owner/repo"} from the operator's generated mapping, or {}.

    🔴 EVERY failure is {} — absent, unreadable, malformed, wrong shape. This
    runs inside a timer-driven collector: a mapping it cannot read must cost
    attribution, never the telemetry pass. The value SHAPE rule is
    `mention_scan.clean_repo_map`, shared with the hint handler so the two
    cannot disagree about what a usable entry is.
    """
    p = path or mention_repos_path()
    try:
        return MS.clean_repo_map(json.loads(p.read_text()))
    except (OSError, ValueError):
        return {}


def mention_key(m: dict) -> str:
    """The dedupe identity of a mention: platform + the exact matched text.

    🔴 The RAW text, not the bare id. `devrc#370` and `talos-infra#370` are
    different references that share an id, and keying on the id alone would emit
    only the first of them and silently drop the second for the life of the
    session."""
    return f"{m['platform']}:{m['raw']}"


def collect_mentions(objects: list[dict], *, repos: dict | None = None) -> list[dict]:
    """Distinct mentions in this session's ASSISTANT text, in first-seen order.

    `repos` is the caller-measured {name: "owner/repo"} mapping used to attribute
    a bare `#N` written next to a repo token (`devrc PR #1291`). It is a
    PARAMETER and not a module read for the same reason the rest of this function
    is pure: `main()` loads it once per pass, and every test can supply its own.

    Assistant TEXT blocks only — not tool inputs, not tool results, not user
    messages. A tool result is usually a file the agent read, so scanning it
    would record the repo's own contents as "mentions"; a user message is the
    operator, and this stream is about what the AGENT surfaced.

    Sidechain (subagent) turns are skipped for the same reason `build_rollup`
    skips them: they are not this session's work.

    PURE — no I/O. `ts` is the assistant turn's own instant so a mention lands in
    ClickHouse at the moment it was written, not at the session's start."""
    seen: set[str] = set()
    out: list[dict] = []
    for obj in objects:
        if not isinstance(obj, dict) or obj.get("isSidechain"):
            continue
        if obj.get("type") != "assistant":
            continue
        msg = obj.get("message")
        if not isinstance(msg, dict):
            continue
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        ts = to_ch_ts(obj.get("timestamp")) if isinstance(obj.get("timestamp"), str) else None
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "text":
                continue
            text = block.get("text")
            if not isinstance(text, str):
                continue
            if not any(h in text for h in _MENTION_HINTS):
                continue
            # 🔴 THE WIDER PROFILE. Telemetry is a private row, not an underlined
            # span on the operator's screen, so this side of the split takes the
            # enumerated widening (`gh pr view N`, `clawgate task N`, GitHub URLs)
            # that `scripts/mention-open.py` deliberately does not.
            for span in MS.scan_mention_spans(text, repos=repos,
                                              profile=MS.PROFILE_TELEMETRY):
                m = {
                    "platform": span["platform"],
                    "id": span["id"],
                    "raw": span["raw"],
                    "url": span["url"],
                    "repo": span["repo"],
                    "repo_source": span["repo_source"],
                    "context": span["context"],
                    "candidates": ",".join(c["platform"] for c in span["candidates"]),
                    "ts": ts,
                }
                key = mention_key(m)  # ONE definition of the dedupe identity
                if key in seen:
                    continue
                seen.add(key)
                out.append(m)
                if len(out) >= MENTIONS_PER_SESSION_CAP:
                    return out
    return out


def build_mention_emit_args(ev: dict, m: dict) -> list[str]:
    """The v1 emit line for one mention. Unknown keys (`platform`,
    `reference_id`, `url`, `repo`, `repo_source`, `context`, `candidates`) land
    in the collector's `payload` JSON — no schema change, see collector.parse_line.

    `repo` is the ATTRIBUTION and it is what makes an ambiguous row useful: 92%
    of mentions in a measured 24h window were a bare `#N`, and a row that names
    the repository can be grouped even when the platform is still open.
    `repo_source` rides beside it so a consumer can tell a repo the text stated
    from one resolved through the operator's mapping — an analysis that cannot
    separate those is one measurement pretending to be two.
    """
    args = [
        "source=mentions",
        "kind=mention-detected",
        f"b64:text={m['raw']}",
        # A plain (non-b64) scalar: the value comes from mention_scan's fixed
        # platform vocabulary, so it can never carry a tab or an '='.
        f"platform={m['platform']}",
        f"b64:reference_id={m['id']}",
        f"b64:url={m['url']}",
        f"b64:repo={m.get('repo', '')}",
        # Also a fixed vocabulary (mention_scan's SOURCE_* constants).
        f"repo_source={m.get('repo_source', '')}",
        f"b64:context={m['context']}",
        f"b64:candidates={m['candidates']}",
        f"b64:project={ev['project']}",
        f"b64:session={ev['session']}",
        f"b64:cwd={ev['cwd']}",
        "b64:app=claude-code",
    ]
    if m.get("ts"):
        args.append(f"ts={m['ts']}")
    return args


def emit_mention(emit: str, ev: dict, m: dict) -> None:
    subprocess.run([emit, *build_mention_emit_args(ev, m)], check=True)


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


def _mention_keys(value) -> list[str]:
    """The stored mention ledger for one transcript, sanitised.

    Anything that is not a list of strings degrades to `[]` — which re-emits
    those mentions once and re-converges, exactly like a missing `emitted_at`.
    A corrupt ledger must never be able to make a mention permanently
    unemittable, and must never crash the timer."""
    if not isinstance(value, list):
        return []
    return [v for v in value if isinstance(v, str)][:MENTIONS_PER_SESSION_CAP]


def load_state(path: Path) -> dict:
    """path -> {"sig": str, "emitted_at": float|None, "mentions": [str]} for
    every transcript we have emitted for.

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
        return {str(p): {"sig": str(s), "emitted_at": None, "mentions": []}
                for p, s in legacy.items() if isinstance(s, str)}
    out: dict = {}
    for p, ent in raw.items():
        if isinstance(ent, dict) and isinstance(ent.get("sig"), str):
            at = ent.get("emitted_at")
            out[str(p)] = {"sig": ent["sig"],
                           "emitted_at": at if isinstance(at, (int, float)) else None,
                           "mentions": _mention_keys(ent.get("mentions"))}
        elif isinstance(ent, str):  # tolerate a flattened entry
            out[str(p)] = {"sig": ent, "emitted_at": None, "mentions": []}
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
    # ONCE per pass, not once per transcript: the mapping is a property of the
    # host, and re-reading it per session would be hundreds of identical reads.
    mention_repos = load_mention_repos()

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
    failed = 0
    mentions_emitted = 0
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
        # 🔴 ONE BAD TRANSCRIPT MUST NOT COST EVERY OTHER SESSION'S SUMMARY.
        # Summarising is pure parsing over data we do not control, and every
        # exception it can raise is a statement about THAT file. Before this
        # wrap a single malformed value aborted the pass — measured on
        # 2026-08-11, six distinct shapes did it (a non-string or blank
        # `file_path`; a `tool_use.input` that is not a dict; a non-string
        # `timestamp`, which mixes int and str under `<`), and the loop had no
        # per-session try, so all claude session-summary emission stopped.
        #
        # SKIP AND REPORT, not log-and-continue: the failure is named on stderr
        # with the path and the exception, and counted into the run's own
        # summary line, because a swallowed exception here would recreate the
        # silent-zero class this whole payload exists to remove.
        #
        # THE SPLIT IS PER-SESSION-DATA vs SYSTEMIC, not "the summarise call":
        #   * inside  — everything derived from THIS transcript's bytes.
        #     `build_event` belongs here and was a 7th fatal shape until it moved
        #     (found by review, PRE-EXISTING — it raises identically on the tree
        #     before this whole change): it calls `project_basename(cwd)` ->
        #     `cwd.rstrip("/")`, reachable whenever a transcript has ZERO
        #     messages and a non-str `cwd`. Zero messages routes through
        #     `_mark_unobservable`, which skips `CP.summarize` — the only guard
        #     that would have rejected that cwd — so the rollup comes back clean
        #     and the raise lands one line later, outside the try.
        #   * outside — `emit_event`. A spool/helper failure is broken for EVERY
        #     session, and turning it into a per-session skip would convert a
        #     loud systemic outage into a quiet count.
        # The signature is NOT recorded on failure, so the transcript is
        # re-evaluated next tick rather than marked done.
        try:
            # ONE read, TWO answers (see load_transcript). The mention scan is
            # per-session data derived from the same bytes, so it belongs INSIDE
            # this try for exactly the reason the rollup does: a malformed value
            # is a statement about THIS transcript and must not abort the pass.
            objects = load_transcript(path)
            if objects is None:
                rollup = _unreadable_rollup("")
                mentions = []
            else:
                rollup = build_rollup(objects)
                mentions = collect_mentions(objects, repos=mention_repos)
            ev = build_event(session, rollup)
        except Exception as exc:  # noqa: BLE001 — deliberately broad; see above
            failed += 1
            print(f"session-tailer: ERROR summarising {path}: "
                  f"{type(exc).__name__}: {exc} — session SKIPPED, not emitted",
                  file=sys.stderr)
            continue
        emit_event(emit, ev)
        # 🔴 The session-summary emit comes FIRST and is unchanged. Mentions are
        # additive: if this loop is ever cut short, the rollup — the thing with
        # existing consumers and a documented invariant — has already shipped.
        #
        # Emitted OUTSIDE the per-session try, like emit_event, because a spool
        # failure is systemic for every session and must stay a loud abort
        # rather than becoming a quiet per-session count.
        already = set(prev.get(path, {}).get("mentions") or [])
        fresh = [m for m in mentions if mention_key(m) not in already]
        for m in fresh:
            emit_mention(emit, ev, m)
        mentions_emitted += len(fresh)
        new_state[path] = {  # ONLY after a successful emit
            "sig": sig,
            "emitted_at": now,
            # The union, capped: a mention already shipped must not ship again
            # even after the session is resumed and re-summarised days later.
            "mentions": sorted(already | {mention_key(m) for m in mentions}
                               )[:MENTIONS_PER_SESSION_CAP],
        }
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
    # `failed` is printed UNCONDITIONALLY, including as 0. A counter that only
    # appears when it is non-zero is one nobody can tell apart from a build that
    # never had the counter — the zero is the reading that makes the non-zero
    # legible.
    # `mentions` is printed unconditionally, including as 0, for the same reason
    # `failed` is: a counter that only appears when non-zero is one nobody can
    # tell apart from a build that never had it.
    print(f"session-tailer: scanned={scanned} emitted={emitted} failed={failed} "
          f"mentions={mentions_emitted} "
          f"[{breakdown}] settle={settle_s / 60:g}m interim={interim_s / 3600:g}h "
          f"state={sp}")

    # 🔴 A COUNT NOBODY READS IS THE SAME SILENT ZERO, ONE LEVEL UP.
    # Skipping a bad transcript keeps the pass alive, but if a skip can never
    # fail the UNIT then a SYSTEMIC breakage — `changed_paths` throwing for every
    # session, a bad deploy, an import resolving differently under the timer —
    # shows up as every session failing while the process still exits 0. systemd
    # records success, `OnFailure = notify-failure@%n.service` never fires, and
    # the only trace is a stderr line in journald nobody is tailing. That is
    # precisely the class this payload exists to remove, so the counter has to
    # reach the exit status. (`claude-activity-source` is Type=oneshot and this
    # script is its SECOND ExecStart, so a non-zero return does fail the unit.)
    #
    # ANY failure fails the run. A ratio rule (`failed >= emitted`, "escalate
    # only the systemic shape") was written first and REJECTED — on the merits
    # below, NOT on the workload argument that was tried first and does not hold.
    # MEASURED over 2,781 real ticks from this unit's own journal (60 days):
    # emitted=0 on 55.0%, and the two rules agree on 81.0% of ticks at failed=1.
    # So they ARE distinguishable, on roughly one tick in five — an earlier draft
    # of this comment claimed "the overwhelming majority" and that the two could
    # not be told apart in practice, which the numbers do not support.
    #
    # The rejection stands on this instead. A transcript that
    # cannot be summarised does not degrade a number — that session's summary is
    # MISSING, and stays missing while the condition holds, because the signature
    # is deliberately not recorded (a code fix must be able to heal the corpus
    # without the file changing). Ongoing data loss is what should keep alerting,
    # so "it would toast every 5 minutes until someone fixes it" is the feature.
    # The usual objection — a permanently-red gate trains people to click
    # through — is about a gate standing between someone and a merge, not about
    # a deadman reporting that data is STILL being dropped.
    #
    # ⚠ WHAT `failed` DOES NOT COVER — stated rather than left to be found,
    # because a bare "any failure fails the run" reads as a completeness this
    # code does not have:
    #   * a breakage that makes summaries WRONG rather than raising. Nothing
    #     here inspects a rollup's contents; `failed` counts exceptions only.
    #   * anything raised OUTSIDE the per-session try — `emit_event`,
    #     `iter_transcripts`, `save_state`. Those still abort the pass with a
    #     traceback and are never counted, BY DESIGN (they are systemic, not
    #     per-transcript). The pass exits non-zero either way, so the unit still
    #     fails; what differs is that `failed=N` will not name them.
    #   * a failure in the unit's FIRST ExecStart (`claude/tailer.py`), which
    #     stops this script from running at all. Pre-existing and out of scope
    #     here; a systemd oneshot runs its ExecStart list sequentially and stops
    #     at the first non-zero.
    if failed:
        print(f"session-tailer: FAILING the run — {failed} session(s) could not "
              f"be summarised and their summaries are MISSING, not degraded "
              f"(emitted={emitted}); see the ERROR line(s) above for each path",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
