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

Degrades gracefully: if telemetry is unconfigured/unreachable it prints a clear
message and exits 0 (only a real error — e.g. an unwritable --html path — is
non-zero), exactly like initiative-scan.
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

DAY = 86400
DEFAULT_DAYS = 14


class TelemetryUnavailable(Exception):
    """Telemetry is not configured / not reachable — degrade, don't crash."""


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


def q_messages(win: int, host: str | None = None) -> str:
    """The user's typed prompts + slash-commands over the window."""
    return (
        "SELECT kind, host, text, ts FROM activity.events "
        f"WHERE source='claude' AND kind IN ('prompt','command') AND ts>now()-{win}{_host_filter(host)}"
    )


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
# Aggregate (pure — the testable core)
# --------------------------------------------------------------------------- #
def aggregate(summary_rows: list[dict], message_rows: list[dict],
              insight_rows: list[dict], days: int, host: str | None) -> dict:
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
    outcomes: Counter = Counter()
    for row in insight_rows:
        p = _parse_payload(row.get("payload"))
        oc = p.get("outcome")
        if oc:
            outcomes[oc] += 1

    now = _dt.datetime.now(_dt.timezone.utc)
    return {
        "generated_utc": now.strftime("%Y-%m-%d %H:%M:%SZ"),
        "days": days,
        "host": host,
        "window_start_utc": (now - _dt.timedelta(days=days)).strftime("%Y-%m-%d"),
        "sessions": len(summary_rows),
        "unreadable_sessions": unreadable,
        "totals": dict(agg),
        "messages": prompt_n + command_n,
        "prompts": prompt_n,
        "commands": command_n,
        "tool_counts": dict(tool_counts.most_common()),
        "languages": dict(languages.most_common()),
        "projects": dict(projects.most_common()),
        "models": dict(models.most_common()),
        "tool_error_categories": dict(err_cats.most_common()),
        "top_commands": commands.most_common(15),
        "top_themes": themes.most_common(),
        "top_first_words": first_words.most_common(15),
        "activity_by_day": dict(sorted(by_day.items())),
        "hosts": hosts,
        "outcomes": dict(outcomes.most_common()) if outcomes else None,
        "qualitative_pending": not bool(outcomes),
    }


# --------------------------------------------------------------------------- #
# Gather
# --------------------------------------------------------------------------- #
def gather(client, days: int, host: str | None = None) -> dict:
    win = window_seconds(days)
    try:
        summaries = client.rows(q_summaries(win, host))
        messages = client.rows(q_messages(win, host))
        insights = client.rows(q_insights(win, host))
    except Exception as e:  # noqa: BLE001 — telemetry is optional; degrade cleanly
        raise TelemetryUnavailable(str(e)) from e
    return aggregate(summaries, messages, insights, days, host)


# --------------------------------------------------------------------------- #
# Render — text
# --------------------------------------------------------------------------- #
def _bar(n, peak, width=18):
    n, peak = num(n), num(peak)
    if peak <= 0:
        return ""
    return "█" * (max(1, round(n / peak * width)) if n > 0 else 0)


def render(data: dict) -> str:
    t = data["totals"]
    out = []
    scope = data["host"] or "all hosts"
    out.append(f"=== Claude Code insights — trailing {data['days']}d ({scope}) ===")
    out.append(f"    {data['window_start_utc']} → now (UTC) · generated {data['generated_utc']}")

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
    dur_h = num(t.get('duration_minutes', 0)) / 60
    out.append(f"  wall-clock: {dur_h:,.1f}h across sessions (sum of per-session spans)")
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

    out.append("\n## OUTCOMES (qualitative — Layer B)")
    if data["qualitative_pending"]:
        out.append("  qualitative layer pending (PR-2): the owned LLM extractor "
                   "(goal/outcome/friction) is not emitted yet.")
        out.append("  This report shows ONLY deterministic facts — no fabricated outcomes.")
    else:
        peak = max(data["outcomes"].values())
        for oc, cnt in data["outcomes"].items():
            out.append(f"  {cnt:>4}  {_bar(cnt, peak):<18}  {oc}")

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
    hosts_rows = "".join(
        f"<tr><td>{html.escape(h)}</td><td>{hp['sessions']}</td><td>{hp['messages']}</td>"
        f"<td>{hp['commits']}</td><td>{hp['output_tokens']:,}</td></tr>"
        for h, hp in sorted(data["hosts"].items()))
    if data["qualitative_pending"]:
        outcomes_html = ('<p class="mut">Qualitative layer pending (PR-2). The owned LLM '
                         'extractor (goal / outcome / friction + automation opportunities) is not '
                         'emitted yet. This report shows ONLY deterministic facts — '
                         '<b>no fabricated outcomes</b>.</p>')
    else:
        outcomes_html = _html_bars(data["outcomes"].items(), n=12)
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
<p class="sub">{data['window_start_utc']} → now · trailing {data['days']}d · {html.escape(scope)} ·
{data['sessions']} sessions · generated {data['generated_utc']}</p>
<div class="note">Deterministic view built from <code>activity.events</code> (Layer&nbsp;A
session rollups + the prompt/command stream). No LLM, no confabulation. The qualitative
layer (goal/outcome/friction) arrives in PR-2.</div>
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
<h2>OUTCOMES (qualitative — Layer B)</h2>
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
                   help=f"trailing window in days (default {DEFAULT_DAYS})")
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
    try:
        conn = Q.CHConn.from_env()
    except RuntimeError as e:
        print(f"insights: telemetry not configured — {e}", file=sys.stderr)
        return 0
    client = Q.CHClient(conn)
    try:
        data = gather(client, a.days, a.host)
    except TelemetryUnavailable as e:
        print(f"insights: telemetry unavailable ({e}); nothing to report.", file=sys.stderr)
        return 0

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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
