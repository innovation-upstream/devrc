#!/usr/bin/env python3
"""insights — telemetry-native Claude Code insights report.

The successor to the built-in `/insights` harness command. Where the built-in
wrote an EPHEMERAL, per-host, non-versioned cache under ~/.claude/usage-data/ and
rendered it with an LLM layer that CONFABULATED friction, this reads the durable
`activity.events` ClickHouse table and reports deterministically — NO LLM.

Three data layers in activity.events (source='claude'):
  * MESSAGE STREAM   kind=prompt|command   — the user's typed turns / slash-cmds
                     (emitted by scripts/collector/claude/tailer.py).
  * LAYER A          kind=session-summary   — per-session deterministic rollups
                     (emitted by scripts/collector/claude/session-tailer.py); the
                     drop-in replacement for the built-in session-meta cache.
  * LAYER B          kind=session-insight    — qualitative facets (goal/outcome/
                     friction + automation opportunities). NOT emitted yet — a
                     later PR-2 (owned LLM extractor). This report renders it IF
                     PRESENT and otherwise shows "qualitative layer pending (PR-2)"
                     — it NEVER fabricates outcomes.

READ CONTRACT: activity.events is append-only and a mutating session emits several
session-summary rows over its life, so we take the LATEST per session with
`argMax(<field>, ingested_at)` grouped by `session`.

Credentials (read-only reader, from env — NEVER hardcoded — same block as
activity-scan.py / initiative-scan.py):
  export CLICKHOUSE_URL=http://192.168.50.94:30123    # workbench LAN endpoint
  export CLICKHOUSE_USER=activity_reader
  export CLICKHOUSE_PASSWORD=<reader-password>        # from SOPS (see the activity skill)

Usage:
  insights.py [--days N] [--json] [--host H] [--html PATH]
  --days   trailing window in days (default 14 — Zach's "last 2 weeks")
  --json   machine-readable output (the aggregated report data)
  --host   restrict to one host label (default: all hosts, with a per-host breakdown)
  --html   ALSO write a styled, self-contained HTML report to PATH
           (default when the flag is given bare: ~/.claude/usage-data/insights-<today>.html)

FAILURE REPORTING (🔴 this is load-bearing — see the exit codes below):
"telemetry is switched off", "the server is unreachable" and "the server
rejected my query" are THREE DIFFERENT FACTS. Until 2026-08-02 this tool
collapsed all three into one `TelemetryUnavailable` and exited 0, so a healthy
pipeline whose message-stream query was being OvercommitTracker-killed by
ClickHouse reported "telemetry unavailable; nothing to report" and no caller
could tell. (That query is also gone now — see the block above `q_summaries`.)

    exit 0  ok                — the full report
    exit 0  not-configured    — no CLICKHOUSE_URL; the documented optional path
    exit 3  unreachable       — could not reach the server at all
    exit 4  query-failed      — server reachable, ALL queries rejected
    exit 5  degraded          — server reachable, SOME queries failed; the
                                report renders with a loud banner naming which
                                query died and which sections are missing

`--json` always carries `status`, and `error`/`errors`/`failed_queries`.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import html
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path

# Reuse the shared ClickHouse client + creds-from-env handling (no new deps).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "validation"))
import chquery as Q  # noqa: E402
# Layer B schema is the single source of truth for vocab / enum ordering — the
# report imports it so it stays a drop-in over the session-insight payload.
sys.path.insert(0, str(Path(__file__).resolve().parent / "session_insight"))
import schema as SCH  # noqa: E402

DAY = 86400
DEFAULT_DAYS = 14
# Qualitative facets (Layer B) accrue slower than raw activity, so the insight
# sections use a wider default window than the Layer A sections (spec §11).
DEFAULT_INSIGHT_DAYS = 30
_LEVERAGE_ORDER = {lv: i for i, lv in enumerate(SCH.LEVERAGES)}


class TelemetryUnavailable(Exception):
    """Base class. Kept so older `except TelemetryUnavailable` callers still work.

    🔴 Do NOT catch this to mean "telemetry is off". The three cases below are
    different facts and must be reported (and exited) differently — conflating
    them is the bug this taxonomy exists to kill.
    """


class TelemetryNotConfigured(TelemetryUnavailable):
    """No CLICKHOUSE_URL in the environment. Not a failure: the tool is
    optional and this is the documented graceful path (exit 0)."""


class TelemetryUnreachable(TelemetryUnavailable):
    """The ClickHouse server could not be reached (connect/DNS/timeout).
    A genuine failure — exit non-zero."""


class TelemetryQueryFailed(TelemetryUnavailable):
    """The server answered and REJECTED our queries. The pipeline is up and the
    report is wrong; the loudest possible failure. Exit non-zero."""

    def __init__(self, message: str, errors: dict[str, str] | None = None):
        super().__init__(message)
        self.errors = errors or {}


# Exit codes — distinct so a caller/cron can branch without parsing text.
EXIT_OK = 0
EXIT_NOT_CONFIGURED = 0     # documented graceful degradation, not a failure
EXIT_UNREACHABLE = 3
EXIT_QUERY_FAILED = 4
EXIT_DEGRADED = 5           # some queries succeeded, some failed

# Which report sections each query feeds, so a partial failure can say exactly
# what is missing instead of silently rendering zeros.
QUERY_SECTIONS = {
    "summaries": "ACTIVITY totals, TOOLS, LANGUAGES, PROJECTS, PER-HOST session counts",
    "messages": "message counts, PER-HOST messages, ACTIVITY OVER TIME",
    "commands": "TOP SLASH-COMMANDS",
    "first_words": "TOP PROMPT FIRST-WORDS",
    "themes": "TOP PROMPT THEMES",
    "insights": "OUTCOMES, AUTOMATION CANDIDATES, RECURRING TOIL, WORKFLOW GAPS (Layer B)",
}


# --------------------------------------------------------------------------- #
# 🔴 WHY THERE IS NO "HOURS WORKED" FIGURE IN THIS REPORT
# --------------------------------------------------------------------------- #
# Layer A's `duration_minutes` is `round((end_ts - start_ts) / 60)` — the span
# from a session's FIRST message to its LAST (session-tailer.py:372). It is
# idle-inclusive, and a resumed session's span reaches back arbitrarily far, so
# Σ duration_minutes is NOT bounded by the report's window and is NOT time spent.
#
# MEASURED 2026-08-05 over 2,393 real transcripts touched in a trailing 14d
# window: Σ duration_minutes = 720,904 min = 12,015.1 h, against the 336 h of
# wall clock that EXIST in 14 days — a 35.8× overstatement. Ten of those sessions
# had a span longer than the whole window on their own; the worst was 818.5 h
# (2026-06-23 → 2026-07-27). The built-in `/insights` reported this same quantity
# as elapsed time and produced "12224h over a 44-day window" (1,056 h exist).
#
# Two mechanisms drive it and NEITHER is recoverable from Layer A:
#   1. IDLE — a session open across a lunch, a night or a fortnight accrues span
#      it did not work; there is no keystroke/turn-gap signal in the rollup.
#   2. OVERLAP — Zach runs many concurrent agent sessions, so spans double-count
#      the same wall-clock minute across sessions.
# Clipping spans to the window would fix (1) partially and (2) not at all, and an
# "active time" estimate would be a number nobody can validate. So the figure is
# kept, RENAMED off "wall-clock", and printed next to the window's real bound so
# a reader cannot mistake it for hours worked. `render_html` prints NO time
# figure at all — see `test_hours_figure_ledger`.
SESSION_SPAN_CAVEAT = (
    "spans are IDLE-INCLUSIVE and overlap across concurrent sessions, and a "
    "resumed session's span reaches back before the window — so this sum is NOT "
    "bounded by the window and is NOT time worked. Layer A carries no "
    "active-time signal; this report has no hours-worked figure."
)


# --------------------------------------------------------------------------- #
# Pure helpers
# --------------------------------------------------------------------------- #
def window_seconds(days: int) -> int:
    if days <= 0:
        raise ValueError("--days must be positive")
    return days * DAY


def num(v, default=0):
    """Coerce a ClickHouse JSON field (UInt64 → quoted string) to a number."""
    if v is None:
        return default
    if isinstance(v, (int, float)):
        return v
    try:
        s = str(v).strip()
        return int(s) if s.lstrip("-").isdigit() else float(s)
    except (ValueError, TypeError):
        return default


def _parse_payload(p) -> dict:
    if isinstance(p, dict):
        return p
    if isinstance(p, str) and p:
        try:
            d = json.loads(p)
            return d if isinstance(d, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


# Non-exclusive theme buckets for typed prompts (compact subset of analyze.py's).
THEMES = {
    "deploy/infra": r"\b(deploy|kubernetes|k8s|kubectl|helm|flux|talos|cluster|nixos|home-manager|nix|nodeport|ingress|namespace|pod|rollout)\b",
    "git/PR": r"\b(commit|push|merge|rebase|pull request|\bpr\b|branch|\bgit\b|\bgh\b)\b",
    "debug/errors": r"\b(error|fail|failing|broken|not working|bug|crash|fix|debug|why is|investigate)\b",
    "verify/test": r"\b(test|verify|reproduce|confirm|check that|make sure|validate)\b",
    "build/run": r"\b(build|run|start|launch|screenshot|server|port forward|localhost)\b",
    "UI/frontend": r"\b(ui|button|css|style|component|modal|page|frontend|layout|icon|color|dark mode|responsive)\b",
    "AI/agents": r"\b(model|prompt|agent|claude|llm|inference|sglang|gpu|token|context|harness|mcp|skill|hook)\b",
    "config/dx": r"\b(config|dotfile|alias|keybind|tmux|i3|neovim|shell|zsh|espanso)\b",
    "data/db": r"\b(database|sql|postgres|clickhouse|query|table|migration|schema)\b",
    "automation": r"\b(schedule|cron|loop|automat|recurring|telemetry|pipeline)\b",
}
_THEME_RX = {name: re.compile(pat, re.I) for name, pat in THEMES.items()}
_WORD_RX = re.compile(r"[a-zA-Z']+")


def _first_token(text: str) -> str:
    t = (text or "").strip()
    return t.split()[0] if t.split() else t


# --------------------------------------------------------------------------- #
# Queries
# --------------------------------------------------------------------------- #
def _host_filter(host: str | None) -> str:
    return f" AND host={Q.sql_quote(host)}" if host else ""


# --------------------------------------------------------------------------- #
# 🔴 WHY THE MESSAGE STREAM IS AGGREGATED SERVER-SIDE
# --------------------------------------------------------------------------- #
# This tool used to stream the whole `text` column to the client and count in
# Python. `activity.events.text` is wide (mean 2,498 B, max 27,136 B over 14d —
# measured 2026-08-02) and the `activity` ClickHouse runs under a hard 2.5 GiB
# total-memory ceiling, so materializing those strings into an HTTP result set
# is what the server cannot do. That is the whole bug: the report said
# "telemetry unavailable" while the pipeline was healthy.
#
# MEASURED on the laptop → nebula endpoint, 2026-08-02, interleaved with
# `SELECT 1` (0.04s) and `count()` (0.20s) controls so a server outage could not
# be mistaken for a query problem:
#
#   ROW STREAM  `SELECT kind, host, text, ts … ts>now()-N`
#      1d / 2d / 4d / 7d / 14d ....... TIMEOUT, every one, at 60s
#      14d, second attempt ............ HTTP 500 `Code: 241 MEMORY_LIMIT_EXCEEDED
#                                       … (while reading column text)`
#      14d + max_block_size=1024 ...... TIMEOUT
#      14d + max_threads=1 ............ TIMEOUT
#      14d + both ..................... TIMEOUT
#   SERVER-SIDE AGGREGATES (what this file now issues, same 14d window)
#      counts by kind/host/day ........ 0.59s,  56 rows,  3,581 B
#      top slash-commands ............. 0.51s,  30 rows,    986 B
#      prompt first-words ............. 0.46s,  20 rows,    466 B
#      10 × countIf(match(…)) themes .. 0.72s,   1 row,     120 B
#
# So: capping block size / threads does NOT fix it (an earlier single
# observation suggested it did and did not reproduce — one measurement is not a
# general claim), and neither does chunking by day, because even a 1-day slice
# times out. Not sending 15.8 MB of prompt text over the wire does fix it, and
# it drops the payload for these sections from ~15.8 MB to ~5 KB.
#
# SEMANTICS: the SQL below is generated FROM the Python definitions above
# (THEMES / _WORD_RX) so the two cannot drift, and was cross-checked against the
# Python implementation over 200 real rows — themes, first-words and
# slash-command tokens all matched EXACTLY. Caveat on that check: only 4 of the
# 200 sampled rows were slash-commands, so the command-token comparison is thin.
#
# Ordering is the one deliberate difference: ties used to fall out in row order
# (which was never pinned — the old query had no ORDER BY, so the "top 15" was
# not reproducible run to run). Ties now break on the key, ascending.
_WORD_PATTERN = "[a-zA-Z']+"           # must stay in step with _WORD_RX above
_TOP_N_SQL = 200                       # generous head; the report renders 15


def q_summaries(win: int, host: str | None = None) -> str:
    """Latest session-summary per session (argMax on ingested_at — the read contract)."""
    return (
        "SELECT session, "
        # A SELECT alias must NOT reuse the name of any column referenced by the
        # WHERE clause: ClickHouse resolves the WHERE identifier to the aggregate
        # alias and errors with ILLEGAL_AGGREGATION. `host` is filtered below via
        # _host_filter, so the aggregate is aliased `sess_host` (aggregate() reads
        # that key). `ts` is filtered too, so it is NOT selected as an aggregate at
        # all (an `AS ts` alias would collide the same way; the value is unused).
        # `project` has no WHERE filter, so reusing its name is safe.
        "argMax(host, ingested_at) AS sess_host, "
        "argMax(project, ingested_at) AS project, "
        "argMax(toString(payload), ingested_at) AS payload "
        "FROM activity.events "
        f"WHERE source='claude' AND kind='session-summary' AND ts>now()-{win}{_host_filter(host)} "
        "GROUP BY session"
    )


def _msg_where(win: int, host: str | None, kind: str | None = None) -> str:
    k = f" AND kind={Q.sql_quote(kind)}" if kind else " AND kind IN ('prompt','command')"
    return (f"WHERE source='claude'{k} AND ts>now()-{win}{_host_filter(host)}")


def q_message_counts(win: int, host: str | None = None) -> str:
    """Message volume per (kind, host, day) — feeds every message COUNT in the
    report (totals, per-host, activity-by-day). Tens of rows, no `text`."""
    return ("SELECT kind, host, toDate(ts) AS day, count() AS n "
            f"FROM activity.events {_msg_where(win, host)} "
            "GROUP BY kind, host, day ORDER BY day ASC, host ASC, kind ASC")


def q_top_commands(win: int, host: str | None = None, limit: int = _TOP_N_SQL) -> str:
    """Slash-command leaderboard. `splitByWhitespace(trimBoth(text))[1]` is the
    SQL twin of `_first_token` (Python's str.split() also splits on ANY
    whitespace — splitByChar(' ') would NOT be equivalent)."""
    return ("SELECT splitByWhitespace(trimBoth(text))[1] AS cmd, count() AS n "
            f"FROM activity.events {_msg_where(win, host, 'command')} "
            f"GROUP BY cmd ORDER BY n DESC, cmd ASC LIMIT {int(limit)}")


def q_first_words(win: int, host: str | None = None, limit: int = _TOP_N_SQL) -> str:
    """First word of each typed prompt. `extract` returns the FIRST match of the
    pattern, which is what `_WORD_RX.findall(...)[0]` does."""
    return (f"SELECT lower(extract(text, {Q.sql_quote(_WORD_PATTERN)})) AS w, count() AS n "
            f"FROM activity.events {_msg_where(win, host, 'prompt')} "
            f"GROUP BY w ORDER BY n DESC, w ASC LIMIT {int(limit)}")


def q_prompt_themes(win: int, host: str | None = None) -> str:
    """One row of 10 `countIf(match(lower(text), <THEMES pattern>))` columns.

    Generated from THEMES so the SQL and the Python regexes cannot drift. The
    aliases are positional (`t0`…`tN`) because a theme name like `deploy/infra`
    is not a bare SQL identifier; `theme_aliases()` is the decoder.
    """
    cols = ", ".join(f"countIf(match(lower(text), {Q.sql_quote(pat)})) AS t{i}"
                     for i, pat in enumerate(THEMES.values()))
    return (f"SELECT {cols} FROM activity.events "
            f"{_msg_where(win, host, 'prompt')}")


def theme_aliases() -> list[str]:
    """Positional SQL alias → theme name, in THEMES declaration order."""
    return list(THEMES)


def q_insights(win: int, host: str | None = None) -> str:
    """Latest Layer-B session-insight per session (empty until PR-2 ships it)."""
    return (
        "SELECT session, "
        "argMax(toString(payload), ingested_at) AS payload "
        "FROM activity.events "
        f"WHERE source='claude' AND kind='session-insight' AND ts>now()-{win}{_host_filter(host)} "
        "GROUP BY session"
    )


# --------------------------------------------------------------------------- #
# Message-stream aggregation from the SERVER-SIDE rollups — pure
# --------------------------------------------------------------------------- #
def aggregate_message_stats(count_rows: list[dict], command_rows: list[dict],
                            word_rows: list[dict], theme_rows: list[dict]) -> dict:
    """Fold the four server-side rollups into the shape `aggregate()` renders.

    Mirrors, exactly, what the old client-side loop over raw message rows
    produced — same keys, same numbers. Only tie-breaking differs (see the note
    beside the query builders). Zero-count themes are DROPPED, because
    `Counter.most_common()` never emitted a theme that matched nothing.
    """
    prompts = commands = 0
    by_day: Counter = Counter()
    hosts: dict[str, dict] = {}
    for r in count_rows:
        n = int(num(r.get("n")))
        kind = r.get("kind")
        h = r.get("host") or "?"
        day = str(r.get("day") or "")[:10]
        if day:
            by_day[day] += n
        hp = hosts.setdefault(h, {"messages": 0, "prompts": 0, "commands": 0})
        hp["messages"] += n
        if kind == "command":
            commands += n
            hp["commands"] += n
        else:
            prompts += n
            hp["prompts"] += n

    top_commands = [(r.get("cmd") or "", int(num(r.get("n")))) for r in command_rows]
    top_first_words = [(r.get("w") or "", int(num(r.get("n")))) for r in word_rows]

    themes: list[tuple[str, int]] = []
    if theme_rows:
        row = theme_rows[0]
        for i, name in enumerate(theme_aliases()):
            n = int(num(row.get(f"t{i}")))
            if n:
                themes.append((name, n))
        themes.sort(key=lambda kv: -kv[1])   # stable → ties keep THEMES order

    return {
        "prompts": prompts,
        "commands": commands,
        "activity_by_day": dict(sorted(by_day.items())),
        "hosts": hosts,
        "top_commands": top_commands,
        "top_first_words": top_first_words,
        "top_themes": themes,
    }


# --------------------------------------------------------------------------- #
# Layer B (qualitative) aggregation — pure
# --------------------------------------------------------------------------- #
def _norm(s) -> str:
    """Normalize a free-text key for exact-match grouping (lowercase, whitespace-
    collapsed). Exact-match only — no fuzzy clustering (decision O4 / YAGNI)."""
    return " ".join(str(s or "").lower().split())


def aggregate_insights(insight_rows: list[dict]) -> dict:
    """Aggregate the Layer B session-insight payloads (spec §11).

    Unreadable rows are EXCLUDED from every qualitative aggregate but counted so
    the report can footnote them honestly. Automation opportunities are grouped
    by a normalized (trigger|description) key and ranked leverage (high>med>low)
    then session-frequency; toil by category, gaps by kind."""
    outcomes: Counter = Counter()
    session_types: Counter = Counter()
    goal_categories: Counter = Counter()
    friction: Counter = Counter()
    help_vals: list[int] = []
    help_hist: dict[int, int] = {i: 0 for i in range(1, 6)}
    auto_groups: dict = {}
    toil_groups: dict = {}
    gap_groups: dict = {}
    unreadable = 0
    readable = 0

    for row in insight_rows:
        p = _parse_payload(row.get("payload"))
        if p.get("unreadable"):
            unreadable += 1
            continue
        readable += 1
        if p.get("outcome"):
            outcomes[p["outcome"]] += 1
        if p.get("session_type"):
            session_types[p["session_type"]] += 1
        for g in p.get("goal_categories") or []:
            goal_categories[g] += 1
        for k, v in (p.get("friction_counts") or {}).items():
            friction[k] += num(v)
        h = p.get("claude_helpfulness")
        if isinstance(h, int) and not isinstance(h, bool) and 1 <= h <= 5:
            help_vals.append(h)
            help_hist[h] += 1

        ao = p.get("automation_opportunity")
        if isinstance(ao, dict) and (ao.get("description") or ao.get("trigger")):
            key = _norm(ao.get("trigger")) + "|" + _norm(ao.get("description"))
            g = auto_groups.setdefault(key, {
                "description": ao.get("description", ""),
                "trigger": ao.get("trigger", ""),
                "leverage": ao.get("leverage", "low"),
                "evidence": ao.get("evidence", ""),
                "sessions": 0})
            g["sessions"] += 1
            if not g["evidence"] and ao.get("evidence"):
                g["evidence"] = ao["evidence"]
            # keep the highest leverage seen for this opportunity
            if _LEVERAGE_ORDER.get(ao.get("leverage"), 9) < _LEVERAGE_ORDER.get(g["leverage"], 9):
                g["leverage"] = ao.get("leverage")

        rt = p.get("recurring_toil")
        if isinstance(rt, dict) and rt.get("description"):
            key = _norm(rt.get("category")) + "|" + _norm(rt.get("description"))
            g = toil_groups.setdefault(key, {
                "category": rt.get("category", "other"),
                "description": rt.get("description", ""),
                "frequency_hints": [], "sessions": 0})
            g["sessions"] += 1
            fh = rt.get("frequency_hint")
            if fh and fh not in g["frequency_hints"]:
                g["frequency_hints"].append(fh)

        wg = p.get("workflow_gap")
        if isinstance(wg, dict) and wg.get("description"):
            key = _norm(wg.get("kind")) + "|" + _norm(wg.get("description"))
            g = gap_groups.setdefault(key, {
                "kind": wg.get("kind", ""),
                "description": wg.get("description", ""), "sessions": 0})
            g["sessions"] += 1

    automation = sorted(auto_groups.values(),
                        key=lambda a: (_LEVERAGE_ORDER.get(a["leverage"], 9),
                                       -a["sessions"]))
    toil = sorted(toil_groups.values(), key=lambda t: (t["category"], -t["sessions"]))
    gaps = sorted(gap_groups.values(), key=lambda w: (w["kind"], -w["sessions"]))
    mean_help = round(sum(help_vals) / len(help_vals), 2) if help_vals else None

    return {
        "insight_rows": len(insight_rows),
        "insight_sessions": readable,
        "unreadable_insights": unreadable,
        "outcomes": dict(outcomes.most_common()) if readable else None,
        "session_types": dict(session_types.most_common()),
        "goal_categories": dict(goal_categories.most_common()),
        "friction": dict(friction.most_common()),
        "helpfulness": {"mean": mean_help, "n": len(help_vals), "hist": help_hist},
        "automation": automation,
        "toil": toil,
        "gaps": gaps,
    }


# --------------------------------------------------------------------------- #
# Aggregate (pure — the testable core)
# --------------------------------------------------------------------------- #
def aggregate(summary_rows: list[dict], message_rows: list[dict],
              insight_rows: list[dict], days: int, host: str | None,
              insight_days: int | None = None,
              message_stats: dict | None = None) -> dict:
    """`message_stats`, when given, REPLACES the client-side loop over
    `message_rows` — that is the server-side-aggregation path gather() uses.
    The raw-rows path is kept because it is the pure, fixture-driven reference
    the tests exercise (and it is what `message_stats` is validated against)."""
    tool_counts: Counter = Counter()
    languages: Counter = Counter()
    projects: Counter = Counter()
    models: Counter = Counter()
    err_cats: Counter = Counter()
    hosts: dict[str, dict] = {}

    agg = Counter()
    unreadable = 0
    for row in summary_rows:
        p = _parse_payload(row.get("payload"))
        # summary rows carry the host under the `sess_host` alias (see q_summaries);
        # tolerate a bare `host` key too for robustness.
        h = row.get("sess_host") or row.get("host") or "?"
        hp = hosts.setdefault(h, {"sessions": 0, "messages": 0, "prompts": 0,
                                  "commands": 0, "commits": 0,
                                  "output_tokens": 0})
        hp["sessions"] += 1
        proj = row.get("project") or "?"
        projects[proj] += 1
        if p.get("unreadable"):
            unreadable += 1
            continue
        for k, v in (p.get("tool_counts") or {}).items():
            tool_counts[k] += num(v)
        for k, v in (p.get("languages") or {}).items():
            languages[k] += num(v)
        for k, v in (p.get("tool_error_categories") or {}).items():
            err_cats[k] += num(v)
        for m in (p.get("models") or []):
            models[m] += 1
        agg["messages"] += num(p.get("user_message_count")) + num(p.get("assistant_message_count"))
        agg["user_messages"] += num(p.get("user_message_count"))
        agg["assistant_messages"] += num(p.get("assistant_message_count"))
        agg["commits"] += num(p.get("git_commits"))
        agg["pushes"] += num(p.get("git_pushes"))
        agg["lines_added"] += num(p.get("lines_added"))
        agg["lines_removed"] += num(p.get("lines_removed"))
        agg["files_modified"] += num(p.get("files_modified"))
        agg["input_tokens"] += num(p.get("input_tokens"))
        agg["cache_read_tokens"] += num(p.get("cache_read_tokens"))
        agg["cache_creation_tokens"] += num(p.get("cache_creation_tokens"))
        agg["output_tokens"] += num(p.get("output_tokens"))
        agg["interruptions"] += num(p.get("user_interruptions"))
        agg["tool_errors"] += num(p.get("tool_errors"))
        agg["duration_minutes"] += num(p.get("duration_minutes"))
        hp["commits"] += num(p.get("git_commits"))
        hp["output_tokens"] += num(p.get("output_tokens"))

    # message stream
    commands: Counter = Counter()
    themes: Counter = Counter()
    first_words: Counter = Counter()
    by_day: Counter = Counter()
    prompt_n = command_n = 0
    if message_stats is not None:
        # Server-side path: the counts already exist; only merge the per-host
        # message columns into the host map the summary loop just built.
        prompt_n = int(num(message_stats.get("prompts")))
        command_n = int(num(message_stats.get("commands")))
        by_day = Counter(message_stats.get("activity_by_day") or {})
        for h, hp_src in (message_stats.get("hosts") or {}).items():
            hp = hosts.setdefault(h, {"sessions": 0, "messages": 0, "prompts": 0,
                                      "commands": 0, "commits": 0,
                                      "output_tokens": 0})
            for k in ("messages", "prompts", "commands"):
                hp[k] += int(num(hp_src.get(k)))
        commands = None      # rendered straight from message_stats below
        first_words = None
        themes = None
        message_rows = []
    for row in message_rows:
        kind = row.get("kind")
        h = row.get("host") or "?"
        text = row.get("text") or ""
        ts = str(row.get("ts") or "")
        day = ts[:10]
        if day:
            by_day[day] += 1
        hp = hosts.setdefault(h, {"sessions": 0, "messages": 0, "prompts": 0,
                                  "commands": 0, "commits": 0,
                                  "output_tokens": 0})
        hp["messages"] += 1
        if kind == "command":
            command_n += 1
            hp["commands"] += 1
            commands[_first_token(text)] += 1
        else:
            prompt_n += 1
            hp["prompts"] += 1
            low = text.lower()
            for name, rx in _THEME_RX.items():
                if rx.search(low):
                    themes[name] += 1
            w = _WORD_RX.findall(low)
            if w:
                first_words[w[0]] += 1

    # Layer B (qualitative) — present only when PR-2 has emitted it.
    ins = aggregate_insights(insight_rows)
    eff_insight_days = max(insight_days or DEFAULT_INSIGHT_DAYS, days)

    now = _dt.datetime.now(_dt.timezone.utc)
    return {
        "generated_utc": now.strftime("%Y-%m-%d %H:%M:%SZ"),
        "days": days,
        "host": host,
        "window_start_utc": (now - _dt.timedelta(days=days)).strftime("%Y-%m-%d"),
        "sessions": len(summary_rows),
        "unreadable_sessions": unreadable,
        "totals": dict(agg),
        # 🔴 The bound that makes an impossible time total self-evident — see
        # `SESSION_SPAN_CAVEAT`. `session_span_hours` is Σ per-session span; it is
        # NOT clipped to the window and NOT bounded by `window_wall_clock_hours`.
        "session_span_hours": num(agg.get("duration_minutes", 0)) / 60,
        "window_wall_clock_hours": days * 24,
        # 🔴 The span sum's ACTUAL divisor population. The loop above `continue`s
        # on an unreadable row BEFORE reaching `duration_minutes`, so an
        # unreadable session contributes no span and must not be counted as one.
        # `sessions` (= len(summary_rows)) is a DIFFERENT population, and printing
        # it beside the sum reproduced — inside this very report — the
        # undisclosed-denominator defect the rest of this file exists to close.
        "session_span_sessions": len(summary_rows) - unreadable,
        "messages": prompt_n + command_n,
        "prompts": prompt_n,
        "commands": command_n,
        "tool_counts": dict(tool_counts.most_common()),
        "languages": dict(languages.most_common()),
        "projects": dict(projects.most_common()),
        "models": dict(models.most_common()),
        "tool_error_categories": dict(err_cats.most_common()),
        "top_commands": (message_stats["top_commands"][:15] if message_stats is not None
                         else commands.most_common(15)),
        "top_themes": (message_stats["top_themes"] if message_stats is not None
                       else themes.most_common()),
        "top_first_words": (message_stats["top_first_words"][:15] if message_stats is not None
                            else first_words.most_common(15)),
        "activity_by_day": dict(sorted(by_day.items())),
        "hosts": hosts,
        # Layer B qualitative aggregates (spec §11).
        "insight_days": eff_insight_days,
        "insight_rows": ins["insight_rows"],
        "insight_sessions": ins["insight_sessions"],
        "unreadable_insights": ins["unreadable_insights"],
        "outcomes": ins["outcomes"],
        "session_types": ins["session_types"],
        "goal_categories": ins["goal_categories"],
        "friction": ins["friction"],
        "helpfulness": ins["helpfulness"],
        "automation": ins["automation"],
        "toil": ins["toil"],
        "gaps": ins["gaps"],
        "qualitative_pending": ins["insight_rows"] == 0,
    }


# --------------------------------------------------------------------------- #
# Gather
# --------------------------------------------------------------------------- #
def gather(client, days: int, host: str | None = None,
           insight_days: int | None = None) -> dict:
    win = window_seconds(days)
    # Layer B uses a wider window than Layer A (qualitative facets accrue slower).
    iwin = window_seconds(max(insight_days or DEFAULT_INSIGHT_DAYS, days))

    # 🔴 Run the three queries INDEPENDENTLY and account for each one.
    #
    # The old code wrapped all three in one `except Exception` and re-raised a
    # single "telemetry unavailable", so (a) a query rejected by a healthy
    # server was reported as the pipeline being off, and (b) if query 1 blew up,
    # queries 2 and 3 never ran and their sections rendered as zeros with no
    # indication anything was missing. Silently-dropped sections are the failure
    # mode this whole taxonomy exists to prevent.
    #
    # An UNREACHABLE server is different: no query can possibly succeed, so
    # abort immediately rather than paying three timeouts.
    plan = (
        ("summaries", q_summaries(win, host)),
        ("messages", q_message_counts(win, host)),
        ("commands", q_top_commands(win, host)),
        ("first_words", q_first_words(win, host)),
        ("themes", q_prompt_themes(win, host)),
        ("insights", q_insights(iwin, host)),
    )
    results: dict[str, list[dict]] = {}
    errors: dict[str, str] = {}
    for name, sql in plan:
        try:
            results[name] = client.rows(sql)
        except Q.CHUnreachable as e:
            raise TelemetryUnreachable(str(e)) from e
        except Exception as e:  # noqa: BLE001 — record and carry on, degraded
            errors[name] = f"{type(e).__name__}: {e}"
            results[name] = []

    if len(errors) == len(plan):
        raise TelemetryQueryFailed(
            "every query was rejected by the server (it IS reachable — this is "
            "a query failure, not a telemetry outage)", errors)

    stats = aggregate_message_stats(results["messages"], results["commands"],
                                    results["first_words"], results["themes"])
    data = aggregate(results["summaries"], [], results["insights"],
                     days, host, insight_days=insight_days, message_stats=stats)
    data["failed_queries"] = errors
    data["status"] = "degraded" if errors else "ok"
    return data


# --------------------------------------------------------------------------- #
# Render — text
# --------------------------------------------------------------------------- #
def _bar(n, peak, width=18):
    n, peak = num(n), num(peak)
    if peak <= 0:
        return ""
    return "█" * (max(1, round(n / peak * width)) if n > 0 else 0)


def render_failure_banner(failed: dict) -> list[str]:
    """A LOUD, unmissable header naming every query that failed and the report
    sections that are consequently empty. A degraded report that looks like a
    quiet one is worse than no report."""
    if not failed:
        return []
    out = ["", "!" * 78,
           f"!! DEGRADED REPORT — {len(failed)} of {len(QUERY_SECTIONS)} queries FAILED.",
           "!! The server IS reachable; these numbers are INCOMPLETE, not low."]
    for name, err in failed.items():
        out.append(f"!!   {name}: {err}")
        out.append(f"!!     → missing: {QUERY_SECTIONS.get(name, '?')}")
    out.append("!" * 78)
    return out


def render(data: dict) -> str:
    t = data["totals"]
    out = []
    scope = data["host"] or "all hosts"
    failed = data.get("failed_queries") or {}
    out.append(f"=== Claude Code insights — trailing {data['days']}d ({scope}) ===")
    out.append(f"    {data['window_start_utc']} → now (UTC) · generated {data['generated_utc']}")
    out.extend(render_failure_banner(failed))

    out.append("\n## ACTIVITY")
    out.append(f"  sessions:   {data['sessions']}"
               + (f"  ({data['unreadable_sessions']} unreadable)" if data['unreadable_sessions'] else ""))
    out.append(f"  messages:   {data['messages']}  ({data['prompts']} typed prompts, {data['commands']} slash-commands)")
    out.append(f"  turns:      {t.get('user_messages',0)} user / {t.get('assistant_messages',0)} assistant")
    out.append(f"  git:        {t.get('commits',0)} commits · {t.get('pushes',0)} pushes")
    out.append("   NOTE: git counts are approximate — one Bash call chaining several commits under-counts; "
               "`--amend`/failed commits over-count (per-tool-use regex match).")
    out.append(f"  code churn: +{t.get('lines_added',0)} / -{t.get('lines_removed',0)} lines · {t.get('files_modified',0)} files")
    in_fresh = num(t.get('input_tokens', 0))
    cache_r = num(t.get('cache_read_tokens', 0))
    cache_w = num(t.get('cache_creation_tokens', 0))
    total_in = in_fresh + cache_r + cache_w
    out.append(f"  tokens:     {total_in:,} in "
               f"({in_fresh:,} fresh + {cache_r:,} cache-read + {cache_w:,} cache-write) "
               f"/ {num(t.get('output_tokens',0)):,} out")
    # 🔴 NOT "wall-clock" — see SESSION_SPAN_CAVEAT. The window's real bound is
    # printed alongside so an impossible total flags itself.
    span_h = num(data.get("session_span_hours", num(t.get('duration_minutes', 0)) / 60))
    bound_h = num(data.get("window_wall_clock_hours", data["days"] * 24))
    ratio = f" — {span_h / bound_h:,.1f}x MORE THAN EXISTS" if bound_h and span_h > bound_h else ""
    # NOT data['sessions'] — an unreadable session contributes no span. See
    # `session_span_sessions`.
    span_n = data.get("session_span_sessions", data["sessions"] - data["unreadable_sessions"])
    out.append(f"  session span: {span_h:,.1f}h summed over {span_n} session spans "
               f"(the {data['days']}d window holds {bound_h:,.0f}h of wall clock{ratio})")
    out.append(f"   NOTE: {SESSION_SPAN_CAVEAT}")
    out.append(f"  friction:   {t.get('interruptions',0)} interruptions · {t.get('tool_errors',0)} tool errors")

    def _bars(title, items, n=8):
        out.append(f"\n## {title}")
        items = list(items)[:n]
        if not items:
            out.append("  (none)")
            return
        peak = max((num(v) for _, v in items), default=0)
        for k, v in items:
            out.append(f"  {num(v):>6}  {_bar(v, peak):<18}  {k}")

    _bars("TOOLS", data["tool_counts"].items())
    _bars("LANGUAGES", data["languages"].items(), n=10)
    _bars("PROJECTS (sessions)", data["projects"].items(), n=10)
    _bars("TOP SLASH-COMMANDS", data["top_commands"])
    _bars("TOP PROMPT THEMES", data["top_themes"])

    out.append("\n## ACTIVITY OVER TIME (messages/day)")
    abd = data["activity_by_day"]
    if not abd:
        out.append("  (none)")
    else:
        peak = max(abd.values())
        for day, cnt in abd.items():
            out.append(f"  {day}  {_bar(cnt, peak):<18}  {cnt}")

    out.append("\n## PER-HOST")
    for h, hp in sorted(data["hosts"].items()):
        out.append(f"  {h:<10} {hp['sessions']} sessions · {hp['messages']} msgs · "
                   f"{hp['commits']} commits · {hp['output_tokens']:,} out-tokens")

    out.append(f"\n## OUTCOMES (qualitative — Layer B · trailing {data['insight_days']}d)")
    if "insights" in failed:
        # NOT "nothing here yet" — that would read a query failure as an empty
        # dataset, which is exactly the conflation this PR removes.
        out.append(f"  UNAVAILABLE — the session-insight query FAILED: {failed['insights']}")
        return "\n".join(out)
    if data["qualitative_pending"]:
        out.append("  (no qualitative insights yet — run session_insight via the activity skill)")
        out.append("  This report shows ONLY deterministic facts — no fabricated outcomes.")
        return "\n".join(out)

    # 🔴 DENOMINATOR DISCLOSURE. The percentages below are over the Layer-B
    # population, which is a DIFFERENT set (and a WIDER window) than the
    # `sessions:` figure in ACTIVITY. The built-in report quoted four session
    # populations in one document without ever naming which was which.
    out.append(f"  population: {data['insight_sessions']} session(s) with a Layer-B insight "
               f"in the trailing {data['insight_days']}d window — the percentages below "
               f"are over THAT denominator, NOT the {data['sessions']} Layer-A session(s) "
               f"in the {data['days']}d window above.")
    oc = data["outcomes"] or {}
    total_oc = sum(oc.values()) or 1
    peak = max(oc.values(), default=0)
    for name, cnt in oc.items():
        out.append(f"  {cnt:>4} {100 * cnt / total_oc:>4.0f}%  {_bar(cnt, peak):<18}  {name}")

    h = data["helpfulness"]
    if h["n"]:
        out.append(f"  mean Claude-helpfulness: {h['mean']:.2f}/5  (n={h['n']}; "
                   "5=materially drove the win … 1=mostly got in the way)")
        hpeak = max(h["hist"].values(), default=0)
        for score in (5, 4, 3, 2, 1):
            c = num(h["hist"].get(score, h["hist"].get(str(score), 0)))
            out.append(f"    {score}  {_bar(c, hpeak):<18}  {c}")

    def _dist(title, mapping, nmax=10):
        items = list(mapping.items())[:nmax]
        if not items:
            return
        out.append(f"  {title}:")
        pk = max((num(v) for _, v in items), default=0)
        for k, v in items:
            out.append(f"    {num(v):>4}  {_bar(v, pk):<18}  {k}")

    _dist("session types", data["session_types"])
    _dist("goal categories", data["goal_categories"])
    _dist("friction (qualitative interaction tallies)", data["friction"])
    if data["unreadable_insights"]:
        out.append(f"  ({data['unreadable_insights']} session(s) flagged unreadable "
                   "— excluded from the aggregates above)")

    out.append("\n## AUTOMATION CANDIDATES / RECURRING TOIL / WORKFLOW GAPS (leverage-ranked)")
    out.append("   NOTE: model-surfaced qualitative candidates, NOT measured savings — "
               "they earn their keep only if reading them changes what you automate.")
    aut = data["automation"]
    out.append("  Automation candidates (leverage · sessions):")
    if not aut:
        out.append("    (none surfaced)")
    for a in aut[:12]:
        out.append(f"    [{a['leverage']:<6} ×{a['sessions']}]  {a['description']}")
        if a.get("trigger"):
            out.append(f"        trigger:  {a['trigger']}")
        if a.get("evidence"):
            out.append(f"        evidence: {a['evidence']}")

    toil = data["toil"]
    out.append("  Recurring toil (by category):")
    if not toil:
        out.append("    (none surfaced)")
    for t in toil[:12]:
        hints = f"  [{', '.join(t['frequency_hints'])}]" if t["frequency_hints"] else ""
        out.append(f"    {t['category']:<18} ×{t['sessions']}  {t['description']}{hints}")

    gaps = data["gaps"]
    out.append("  Workflow gaps (by kind):")
    if not gaps:
        out.append("    (none surfaced)")
    for w in gaps[:12]:
        out.append(f"    {w['kind']:<18} ×{w['sessions']}  {w['description']}")

    return "\n".join(out)


# --------------------------------------------------------------------------- #
# Render — HTML (self-contained, theme-aware; adapted from the prototype)
# --------------------------------------------------------------------------- #
def _html_bars(items, n=8):
    items = list(items)[:n]
    if not items:
        return '<p class="mut">(none)</p>'
    peak = max((num(v) for _, v in items), default=1) or 1
    rows = []
    for k, v in items:
        pct = 100 * num(v) / peak
        rows.append(f'<div class="bar"><span class="bl">{html.escape(str(k))}</span>'
                    f'<span class="bt"><i style="width:{pct:.0f}%"></i></span>'
                    f'<span class="bv">{num(v):,}</span></div>')
    return "\n".join(rows)


def render_html(data: dict) -> str:
    t = data["totals"]
    scope = data["host"] or "all hosts"
    total_in = (num(t.get('input_tokens', 0)) + num(t.get('cache_read_tokens', 0))
                + num(t.get('cache_creation_tokens', 0)))
    failed = data.get("failed_queries") or {}
    degraded_html = ""
    if failed:
        items = "".join(
            f"<li><b>{html.escape(n)}</b>: {html.escape(e)}<br>"
            f"<span class=mut>missing: {html.escape(QUERY_SECTIONS.get(n, '?'))}</span></li>"
            for n, e in failed.items())
        degraded_html = (
            f'<div class="note" style="border-left-color:#c0392b"><b>DEGRADED — '
            f'{len(failed)} of {len(QUERY_SECTIONS)} queries FAILED.</b> The server is reachable; the '
            f'numbers below are <b>incomplete, not low</b>.<ul>{items}</ul></div>')
    hosts_rows = "".join(
        f"<tr><td>{html.escape(h)}</td><td>{hp['sessions']}</td><td>{hp['messages']}</td>"
        f"<td>{hp['commits']}</td><td>{hp['output_tokens']:,}</td></tr>"
        for h, hp in sorted(data["hosts"].items()))
    if data["qualitative_pending"]:
        outcomes_html = ('<p class="mut">No qualitative insights yet — run '
                         '<code>session_insight</code> via the activity skill. This report '
                         'shows ONLY deterministic facts — <b>no fabricated outcomes</b>.</p>')
    else:
        # 🔴 DENOMINATOR + WINDOW DISCLOSURE — the text renderer has carried this
        # since it was written; the HTML one did not, so a reader saw the page's
        # "trailing {days}d · {sessions} sessions" subtitle sitting directly above
        # percentages drawn from a different population over a wider window.
        parts = [
            f'<p class="mut">Population: <b>{data["insight_sessions"]}</b> session(s) with a '
            f'Layer-B insight in the trailing <b>{data["insight_days"]}d</b> window. The '
            f'percentages below are over THAT denominator — <b>not</b> the '
            f'{data["sessions"]} Layer-A session(s) in the {data["days"]}d window above.</p>',
            _html_bars((data["outcomes"] or {}).items(), n=12),
        ]
        h = data["helpfulness"]
        if h["n"]:
            parts.append(f'<p class="mut">mean Claude-helpfulness '
                         f'<b>{h["mean"]:.2f}/5</b> (n={h["n"]})</p>')
        if data["unreadable_insights"]:
            parts.append(f'<p class="mut">{data["unreadable_insights"]} session(s) flagged '
                         'unreadable — excluded from the aggregates.</p>')
        if data["automation"]:
            rows = "".join(
                f'<div class="bar"><span class="bl">{html.escape(a["leverage"])} '
                f'×{a["sessions"]}</span><span class="bt"><i style="width:100%"></i></span>'
                f'<span class="bv">{html.escape(a["description"][:80])}</span></div>'
                for a in data["automation"][:10])
            # Provenance label — parity with render()'s NOTE. These strings are
            # MODEL-authored free text; without the label the HTML page presented
            # them beside deterministic counts as if equally measured.
            parts.append(f'<p class="mut" style="margin-top:12px"><b>Automation candidates '
                         f'(leverage-ranked)</b> — model-surfaced qualitative candidates, '
                         f'NOT measured savings.</p>{rows}')
        outcomes_html = "\n".join(parts)
    unreadable_note = ""
    if data["unreadable_sessions"]:
        unreadable_note = (f'<div class="note">{data["unreadable_sessions"]} session(s) '
                           'could not be parsed and are shown as <b>unreadable</b> rather than '
                           'guessed at (the built-in report invented a token-limit story here).</div>')
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Claude Code Insights — telemetry-native</title>
<style>
:root{{--bg:#faf9f7;--fg:#1a1a1a;--mut:#6b6b6b;--card:#fff;--line:#e7e4df;--acc:#b8552a}}
@media(prefers-color-scheme:dark){{:root{{--bg:#17150f;--fg:#ece7dd;--mut:#9a938a;--card:#211e17;--line:#332f26;--acc:#e08a4f}}}}
:root[data-theme=dark]{{--bg:#17150f;--fg:#ece7dd;--mut:#9a938a;--card:#211e17;--line:#332f26;--acc:#e08a4f}}
:root[data-theme=light]{{--bg:#faf9f7;--fg:#1a1a1a;--mut:#6b6b6b;--card:#fff;--line:#e7e4df;--acc:#b8552a}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--fg);font:15px/1.55 -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif}}
.wrap{{max-width:900px;margin:0 auto;padding:32px 20px 80px}}
h1{{font-size:25px;margin:0 0 2px}}h2{{font-size:16px;margin:32px 0 12px;letter-spacing:.02em}}
.sub{{color:var(--mut);margin:0 0 22px}}.mut{{color:var(--mut)}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:10px;margin:16px 0}}
.stat{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px}}
.stat b{{display:block;font-size:21px}}.stat span{{color:var(--mut);font-size:12px}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px 18px;margin:12px 0}}
.cols{{display:grid;grid-template-columns:1fr 1fr;gap:16px}}@media(max-width:640px){{.cols{{grid-template-columns:1fr}}}}
.bar{{display:flex;align-items:center;gap:10px;margin:5px 0;font-size:13px}}
.bl{{width:38%;color:var(--mut);text-align:right;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
.bt{{flex:1;background:var(--line);border-radius:4px;height:9px;overflow:hidden}}
.bt i{{display:block;height:100%;background:var(--acc)}}
.bv{{width:60px;text-align:right;font-variant-numeric:tabular-nums}}
table{{width:100%;border-collapse:collapse;font-size:13.5px}}td,th{{text-align:left;padding:6px 8px;border-bottom:1px solid var(--line)}}
.note{{border-left:3px solid var(--acc);padding:6px 0 6px 14px;color:var(--mut);font-size:13.5px;margin:12px 0}}
footer{{color:var(--mut);font-size:12px;margin-top:40px;border-top:1px solid var(--line);padding-top:14px}}
</style></head><body><div class="wrap">
<h1>Claude Code Insights <span style="color:var(--acc)">· telemetry-native</span></h1>
<p class="sub">{data['window_start_utc']} → now · Layer A: trailing {data['days']}d · {html.escape(scope)} ·
{data['sessions']} Layer-A sessions · generated {data['generated_utc']}</p>
<div class="note">Deterministic view built from <code>activity.events</code> (Layer&nbsp;A
session rollups + the prompt/command stream). No LLM, no confabulation. The qualitative
layer (goal/outcome/friction) arrives in PR-2.</div>
{degraded_html}
{unreadable_note}
<h2>ACTIVITY</h2>
<div class="grid">
<div class="stat"><b>{data['sessions']}</b><span>sessions</span></div>
<div class="stat"><b>{data['messages']:,}</b><span>messages</span></div>
<div class="stat"><b>{t.get('commits',0)}</b><span>commits · {t.get('pushes',0)} pushes</span></div>
<div class="stat"><b>+{t.get('lines_added',0):,}</b><span>lines (−{t.get('lines_removed',0):,})</span></div>
<div class="stat"><b>{t.get('files_modified',0):,}</b><span>files touched</span></div>
<div class="stat"><b>{total_in//1_000_000}M</b><span>input tokens (incl. cache)</span></div>
<div class="stat"><b>{num(t.get('output_tokens',0))//1_000_000}M</b><span>output tokens</span></div>
</div>
<div class="note">Input tokens include cache-read + cache-creation, not just fresh input
({num(t.get('input_tokens',0)):,} fresh · {num(t.get('cache_read_tokens',0)):,} cache-read ·
{num(t.get('cache_creation_tokens',0)):,} cache-write). Git commit/push counts are approximate
(per-tool-use regex: chained commits under-count, <code>--amend</code>/failed commits over-count).</div>
<div class="cols">
<div class="card"><b>Tools</b>{_html_bars(data['tool_counts'].items())}</div>
<div class="card"><b>Languages</b>{_html_bars(data['languages'].items(), n=8)}</div>
</div>
<div class="cols">
<div class="card"><b>Projects (sessions)</b>{_html_bars(data['projects'].items(), n=8)}</div>
<div class="card"><b>Top slash-commands</b>{_html_bars(data['top_commands'])}</div>
</div>
<h2>TOP PROMPT THEMES</h2>
<div class="card">{_html_bars(data['top_themes'], n=10)}</div>
<h2>ACTIVITY OVER TIME (messages/day)</h2>
<div class="card">{_html_bars(data['activity_by_day'].items(), n=60)}</div>
<h2>PER-HOST</h2>
<div class="card"><table>
<tr><th>host</th><th>sessions</th><th>messages</th><th>commits</th><th>out-tokens</th></tr>
{hosts_rows}
</table></div>
<h2>OUTCOMES (qualitative — Layer B · trailing {data['insight_days']}d)</h2>
<div class="card">{outcomes_html}</div>
<footer>Telemetry-native successor to the built-in <code>/insights</code>.
Regenerate: <code>insights.py --days {data['days']} --html PATH</code>. Source: activity.events.</footer>
</div></body></html>"""


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Telemetry-native Claude Code insights report.")
    p.add_argument("--days", type=int, default=DEFAULT_DAYS,
                   help=f"trailing window in days for Layer A sections (default {DEFAULT_DAYS})")
    p.add_argument("--insight-days", type=int, default=None,
                   help=f"window for the Layer B qualitative sections "
                        f"(default max({DEFAULT_INSIGHT_DAYS}, --days))")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.add_argument("--host", default=None, help="restrict to one host (default: all)")
    p.add_argument("--html", nargs="?", const="__DEFAULT__", default=None,
                   help="also write a styled HTML report (optionally to PATH)")
    return p.parse_args(argv)


def _default_html_path() -> str:
    today = _dt.datetime.now().strftime("%Y-%m-%d")
    base = Path(os.path.expanduser("~/.claude/usage-data"))
    return str(base / f"insights-{today}.html")


def main(argv=None) -> int:
    a = parse_args(argv)
    if a.days <= 0:
        print("error: --days must be positive", file=sys.stderr)
        return 2
    # 🔴 THREE separable states, three exit codes, an error carried in --json.
    # Previously all of these printed one "telemetry unavailable" line and
    # exited 0, so a broken query looked identical to telemetry being off and
    # nothing downstream could ever notice.
    def _fail(status: str, message: str, rc: int, errors: dict | None = None) -> int:
        if a.json:
            print(json.dumps({"status": status, "error": message,
                              "errors": errors or {}, "days": a.days,
                              "host": a.host}, indent=2))
        print(f"insights: {message}", file=sys.stderr)
        return rc

    try:
        conn = Q.CHConn.from_env()
    except RuntimeError as e:
        # Not a failure: the tool is optional and this is the documented path.
        return _fail("not-configured",
                     f"telemetry NOT CONFIGURED — {e}", EXIT_NOT_CONFIGURED)
    client = Q.CHClient(conn)
    try:
        data = gather(client, a.days, a.host, insight_days=a.insight_days)
    except TelemetryUnreachable as e:
        return _fail("unreachable",
                     f"telemetry UNREACHABLE at {conn.url} — {e}. "
                     "The report was NOT produced.", EXIT_UNREACHABLE)
    except TelemetryQueryFailed as e:
        return _fail("query-failed",
                     f"telemetry QUERY FAILED at {conn.url} — {e}. The server is "
                     "reachable; the queries are broken or the server rejected "
                     "them. The report was NOT produced.",
                     EXIT_QUERY_FAILED, errors=e.errors)

    if a.json:
        print(json.dumps(data, indent=2, default=str))
    else:
        print(render(data))

    if a.html is not None:
        path = _default_html_path() if a.html == "__DEFAULT__" else a.html
        p = Path(os.path.expanduser(path))
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(render_html(data), encoding="utf-8")
        print(f"\nwrote HTML report → {p}", file=sys.stderr)

    failed = data.get("failed_queries") or {}
    if failed:
        print(f"insights: DEGRADED — {len(failed)} of {len(QUERY_SECTIONS)} queries failed "
              f"({', '.join(failed)}); the sections they feed are EMPTY, not zero.",
              file=sys.stderr)
        return EXIT_DEGRADED
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
