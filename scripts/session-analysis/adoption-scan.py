#!/usr/bin/env python3
"""adoption-scan — which shipped "gametape" tools are actually USED and YIELDING.

We ship productivity tools but some die unused — a measured past failure ("opt-in
commands didn't stick → pivoted to autonomous loops"). This is the deterministic
tracker that tells USE from idle and, where we can, USE from VALUE, straight off
the activity telemetry (`activity.events`):

  * a REGISTRY of tracked improvements, each mapping to how its use is detected.
  * ADOPTION per item: invocations in the window, broken down by outcome, a
    this-half vs prior-half trend, and a loud DEAD flag at 0 uses (the opt-in
    tools are the ones most at risk of silently dying).
  * IMPACT (yielding, not just firing): the outcome MIX that proves the tool
    caught something real — verify-agent `fail|incomplete` (false-greens caught),
    obs-read `matched-nothing` (silent-zeros caught), ticket-status `already-done`
    (non-tasks dissolved) — plus a compact, explicitly-DIRECTIONAL drafter
    friction trend (permission_block + wrong_approach over two windows).

Data sources (all already emitted; nothing new stored except the tool-invocation
events this project added):
  * `source=tool  kind=invocation`  — the emit-instrumented tools (verify-agent-
    work, obs-read, ticket-status, playwright-nixos). tool name in `text`,
    `outcome`/dims in the JSON `payload`.
  * `source=claude kind=command`    — the opt-in slash commands from the message
    stream (`/verify-agent`, `/obs-read`).
  * `source=claude kind=session-insight` — Layer-B qualitative facets, for the
    friction trend (dedup argMax per session, per the read contract).

Credentials (read-only reader, from env — NEVER hardcoded; same block as
activity-scan.py / initiative-scan.py):
  export CLICKHOUSE_URL=http://192.168.50.94:30123
  export CLICKHOUSE_USER=activity_reader
  export CLICKHOUSE_PASSWORD=<reader-password>   # from SOPS
Degrades gracefully: if telemetry is unconfigured/unreachable it prints a clear
message and exits 0 (mirrors insights.py) — never crashes.

HONESTY (baked into the output):
  * an invocation COUNT proves USE, not VALUE. Only the outcome MIX gestures at
    value, and even then imperfectly.
  * the friction trend is DIRECTIONAL — different tickets/models/週 confound it;
    read the direction, not the absolute delta.

Usage:
  adoption-scan.py [--days N] [--insight-days N] [--json] [--host H] [--dead-days N]
  --days         adoption window in days (default 14).
  --insight-days friction-trend window in days (default 30 — facets accrue slower).
  --dead-days    a tool with 0 uses within this many days is flagged DEAD
                 (default = --days).
  --json         machine-readable output.
  --host         restrict to one ACTIVITY_HOST label.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import sys
from collections import Counter
from pathlib import Path

# Reuse the shared ClickHouse client + creds-from-env (no new deps).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "validation"))
import chquery as Q  # noqa: E402

DAY = 86400
DEFAULT_DAYS = 14
# Qualitative facets (Layer B) accrue slower than raw invocations, so the
# friction trend uses a wider default window than the adoption sections.
DEFAULT_INSIGHT_DAYS = 30
DRAFTER_CWD_MATCH = "task-spec-drafter"
# The friction categories the #157/#159 fixes were meant to move (baseline was
# permission_block=57). Kept tiny + explicit so the trend stays legible.
FRICTION_TRACKED = ["permission_block", "wrong_approach"]


class TelemetryUnavailable(Exception):
    """Telemetry is not configured / not reachable — degrade, don't crash."""


# --------------------------------------------------------------------------- #
# Registry of tracked improvements
# --------------------------------------------------------------------------- #
# Each row says HOW to detect an item's use. Add a row when we ship more:
#   via="tool"    -> match `source=tool kind=invocation` rows by tool name.
#   via="command" -> match `source=claude kind=command` message-stream text by
#                    slash-command prefix(es).
#   via="cwd"     -> match `source=claude` sessions whose cwd names the item
#                    (best-effort/heuristic — used for the autonomous drafter).
# `impact_outcomes` are the outcomes that evidence a REAL catch (value signal),
# with `impact_label` explaining what a hit means. opt_in items are the ones
# whose adoption is fragile (a human has to choose to invoke them).
REGISTRY = [
    {
        "id": "verify-agent-work",
        "label": "verify-agent-work — post-agent verification gate",
        "via": "tool", "tool": "verify-agent-work", "opt_in": True,
        "outcomes": ["pass", "fail", "incomplete"],
        "impact_outcomes": ["fail", "incomplete"],
        "impact_label": "false-greens caught (agent 'done' contradicted)",
    },
    {
        "id": "obs-read",
        "label": "obs-read — cluster-aware observability query",
        "via": "tool", "tool": "obs-read", "opt_in": True,
        "outcomes": ["ok", "matched-nothing", "error"],
        "impact_outcomes": ["matched-nothing"],
        "impact_label": "silent-zeros caught (wrong label/service, not a real 0)",
    },
    {
        "id": "ticket-status",
        "label": "ticket-status — drafter prior-art / merge-status probe",
        "via": "tool", "tool": "ticket-status", "opt_in": False,
        "outcomes": ["already-done", "partial", "not-found", "error"],
        "impact_outcomes": ["already-done"],
        "impact_label": "non-tasks dissolved (already merged to trunk)",
    },
    {
        "id": "playwright-nixos",
        "label": "playwright-nixos — NixOS Chromium wrapper for Playwright",
        "via": "tool", "tool": "playwright-nixos", "opt_in": False,
        "outcomes": ["ok", "error"],
        "impact_outcomes": [],
        "impact_label": "",
    },
    {
        "id": "cmd:verify-agent",
        "label": "/verify-agent (slash command typed)",
        "via": "command", "prefixes": ["/verify-agent"], "opt_in": True,
        "outcomes": [], "impact_outcomes": [], "impact_label": "",
    },
    {
        "id": "cmd:obs-read",
        "label": "/obs-read (slash command typed)",
        "via": "command", "prefixes": ["/obs-read"], "opt_in": True,
        "outcomes": [], "impact_outcomes": [], "impact_label": "",
    },
    {
        "id": "task-spec-drafter",
        "label": "task-spec-drafter — autonomous weekly ticket drafter (cwd, heuristic)",
        "via": "cwd", "match": DRAFTER_CWD_MATCH, "opt_in": False,
        "outcomes": [], "impact_outcomes": [], "impact_label": "",
    },
]


# --------------------------------------------------------------------------- #
# Pure helpers
# --------------------------------------------------------------------------- #
def window_seconds(days: int) -> int:
    if days <= 0:
        raise ValueError("days must be positive")
    return days * DAY


def ch_ts_to_epoch(s) -> float | None:
    """Parse a ClickHouse/emit 'YYYY-MM-DD HH:MM:SS[.fff]' UTC string to epoch.
    The stored `ts` is a tz-less DateTime64 holding the UTC instant."""
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


def parse_payload(p) -> dict:
    if isinstance(p, dict):
        return p
    if isinstance(p, str) and p:
        try:
            d = json.loads(p)
            return d if isinstance(d, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _half(ts_epoch, now: float, half_s: float) -> str | None:
    """Bucket a row into 'recent' (age <= half) or 'prior' (half < age <= 2*half).
    Returns None if the ts is unparseable or falls outside the window."""
    if ts_epoch is None:
        return None
    age = now - ts_epoch
    if age < 0:
        return "recent"  # clock skew: a just-emitted row -> treat as recent
    if age <= half_s:
        return "recent"
    if age <= 2 * half_s:
        return "prior"
    return None


def _trend(recent: int, prior: int) -> str:
    if recent > prior:
        return "up"
    if recent < prior:
        return "down"
    return "flat"


# --------------------------------------------------------------------------- #
# Per-item aggregation (pure — the whole decision surface is unit-tested)
# --------------------------------------------------------------------------- #
def command_matches(text: str, prefixes: list[str]) -> bool:
    """True iff a slash-command line IS one of `prefixes` (bare or with args).
    Tight on purpose: `/obs-read-foo` must NOT count as `/obs-read`. Pure."""
    t = (text or "").strip()
    return any(t == pre or t.startswith(pre + " ") for pre in prefixes)


def aggregate_tool(rows: list[dict], tool: str, now: float, win: float) -> dict:
    """Aggregate `source=tool kind=invocation` rows for ONE tool name, counting
    ONLY events within the adoption window `win` (rows from a wider dead-window
    fetch are excluded from counts/trend). Trend halves are win/2."""
    half_s = win / 2.0
    total = 0
    outcomes: Counter = Counter()
    recent = prior = 0
    for r in rows:
        p = parse_payload(r.get("payload"))
        name = r.get("tool") or r.get("text") or p.get("tool")
        if name != tool:
            continue
        e = ch_ts_to_epoch(r.get("ts"))
        if e is None or (now - e) > win:
            continue  # outside the --days window (may be in the wider fetch)
        total += 1
        outcome = p.get("outcome") or "unknown"
        outcomes[outcome] += 1
        b = _half(e, now, half_s)
        if b == "recent":
            recent += 1
        elif b == "prior":
            prior += 1
    return {"total": total, "outcomes": dict(outcomes),
            "recent": recent, "prior": prior, "trend": _trend(recent, prior)}


def aggregate_command(rows: list[dict], prefixes: list[str], now: float,
                      win: float) -> dict:
    """Aggregate message-stream `command` rows whose text IS one of the slash
    prefixes, within the adoption window `win`."""
    half_s = win / 2.0
    total = 0
    recent = prior = 0
    for r in rows:
        if not command_matches(r.get("text"), prefixes):
            continue
        e = ch_ts_to_epoch(r.get("ts"))
        if e is None or (now - e) > win:
            continue
        total += 1
        b = _half(e, now, half_s)
        if b == "recent":
            recent += 1
        elif b == "prior":
            prior += 1
    return {"total": total, "outcomes": {}, "recent": recent, "prior": prior,
            "trend": _trend(recent, prior)}


def aggregate_cwd(rows: list[dict], now: float, win: float) -> dict:
    """Aggregate the (already cwd-filtered) session rows for a cwd-detected item,
    within the adoption window `win`. Each row is one session touching the cwd."""
    half_s = win / 2.0
    total = 0
    recent = prior = 0
    for r in rows:
        e = ch_ts_to_epoch(r.get("ts"))
        if e is None or (now - e) > win:
            continue
        total += 1
        b = _half(e, now, half_s)
        if b == "recent":
            recent += 1
        elif b == "prior":
            prior += 1
    return {"total": total, "outcomes": {}, "recent": recent, "prior": prior,
            "trend": _trend(recent, prior)}


def aggregate_friction(insight_rows: list[dict], now: float, iwin: float) -> dict:
    """Sum the tracked friction categories over two windows (recent vs prior half
    of the insight window `iwin`). DIRECTIONAL only — confounded by different
    tickets/models across the windows."""
    half_s = iwin / 2.0
    recent = {k: 0 for k in FRICTION_TRACKED}
    prior = {k: 0 for k in FRICTION_TRACKED}
    sess_recent = sess_prior = 0
    unreadable = 0
    for r in insight_rows:
        p = parse_payload(r.get("payload"))
        if not p or p.get("unreadable"):
            unreadable += 1
            continue
        b = _half(ch_ts_to_epoch(r.get("ts")), now, half_s)
        if b is None:
            continue
        bucket = recent if b == "recent" else prior
        if b == "recent":
            sess_recent += 1
        else:
            sess_prior += 1
        fc = p.get("friction_counts") or {}
        if isinstance(fc, dict):
            for k in FRICTION_TRACKED:
                v = fc.get(k)
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    bucket[k] += int(v)
    deltas = {k: recent[k] - prior[k] for k in FRICTION_TRACKED}
    return {
        "recent": recent, "prior": prior, "deltas": deltas,
        "sessions_recent": sess_recent, "sessions_prior": sess_prior,
        "unreadable": unreadable,
    }


# --------------------------------------------------------------------------- #
# Queries
# --------------------------------------------------------------------------- #
def _host_filter(host: str | None) -> str:
    return f" AND host={Q.sql_quote(host)}" if host else ""


def q_tool_invocations(win: int, host: str | None = None) -> str:
    return (
        "SELECT text AS tool, toString(payload) AS payload, "
        "exit_code, duration_ms, ts, host "
        "FROM activity.events "
        f"WHERE source='tool' AND kind='invocation' AND ts>now()-{win}"
        f"{_host_filter(host)}"
    )


def q_commands(win: int, host: str | None = None) -> str:
    return (
        "SELECT text AS text, ts, host FROM activity.events "
        f"WHERE source='claude' AND kind='command' AND ts>now()-{win}"
        f"{_host_filter(host)}"
    )


def q_cwd_sessions(win: int, match: str, host: str | None = None) -> str:
    lit = Q.sql_quote(match.lower())
    # Alias the aggregate to `last_ts`, NOT `ts`: aliasing max(ts) AS ts shadows
    # the raw `ts` column, so the WHERE `ts>now()` binds to the aggregate →
    # ClickHouse ILLEGAL_AGGREGATION. gather() normalises `last_ts`→`ts` on read.
    return (
        "SELECT session AS session, max(ts) AS last_ts FROM activity.events "
        f"WHERE source='claude' AND ts>now()-{win} "
        f"AND position(lower(cwd), {lit}) > 0{_host_filter(host)} "
        "GROUP BY session"
    )


def q_insights(win: int, host: str | None = None) -> str:
    # `max(ts) AS last_ts` not `AS ts` — see q_cwd_sessions: an aggregate aliased
    # to the raw column name `ts` shadows it in WHERE → ILLEGAL_AGGREGATION.
    return (
        "SELECT session, argMax(toString(payload), ingested_at) AS payload, "
        "max(ts) AS last_ts FROM activity.events "
        f"WHERE source='claude' AND kind='session-insight' AND ts>now()-{win}"
        f"{_host_filter(host)} GROUP BY session"
    )


def _norm_last_ts(rows: list[dict]) -> list[dict]:
    """Map the `last_ts` aggregate alias back to `ts` so the shared aggregators
    (which read `r.get("ts")`) work unchanged. The alias is `last_ts` in SQL only
    to avoid shadowing the raw `ts` column in the WHERE clause."""
    for r in rows:
        if "last_ts" in r:
            r["ts"] = r["last_ts"]
    return rows


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
def gather(client, days: int, insight_days: int, dead_days: int,
           host: str | None = None, now: float | None = None) -> dict:
    """Fetch + aggregate. Raises TelemetryUnavailable if the store is unreachable."""
    import time
    now = time.time() if now is None else now
    win = window_seconds(days)
    iwin = window_seconds(insight_days)
    dead_s = window_seconds(dead_days)
    # The fetch must span BOTH windows so a dead_days LARGER than days is honest
    # (otherwise a bigger --dead-days is silently clamped to the fetch). Counts
    # stay scoped to `win` inside the aggregators; DEAD uses the full dead window.
    fetch_win = max(win, dead_s)

    try:
        tool_rows = client.rows(q_tool_invocations(fetch_win, host))
        cmd_rows = client.rows(q_commands(fetch_win, host))
        insight_rows = _norm_last_ts(client.rows(q_insights(iwin, host)))
        # cwd-detected items each get their own filtered query.
        cwd_rows: dict = {}
        for item in REGISTRY:
            if item["via"] == "cwd":
                cwd_rows[item["id"]] = _norm_last_ts(client.rows(
                    q_cwd_sessions(fetch_win, item["match"], host)))
    except Exception as e:  # noqa: BLE001 — telemetry optional; degrade cleanly
        raise TelemetryUnavailable(str(e)) from e

    items = []
    for item in REGISTRY:
        via = item["via"]
        if via == "tool":
            agg = aggregate_tool(tool_rows, item["tool"], now, win)
        elif via == "command":
            agg = aggregate_command(cmd_rows, item["prefixes"], now, win)
        elif via == "cwd":
            agg = aggregate_cwd(cwd_rows.get(item["id"], []), now, win)
        else:
            agg = {"total": 0, "outcomes": {}, "recent": 0, "prior": 0,
                   "trend": "flat"}

        # DEAD = zero uses within the dead-days window, computed over the FULL
        # fetched set (which spans dead_s), so it is correct whether dead_days is
        # smaller, equal to, or larger than days.
        dead = _count_within(_item_ts(item, tool_rows, cmd_rows, cwd_rows),
                             now, dead_s) == 0

        impact = {o: agg["outcomes"].get(o, 0) for o in item.get("impact_outcomes", [])}
        items.append({
            "id": item["id"], "label": item["label"], "via": via,
            "opt_in": item["opt_in"], "total": agg["total"],
            "outcomes": agg["outcomes"], "recent": agg["recent"],
            "prior": agg["prior"], "trend": agg["trend"], "dead": dead,
            "impact": impact, "impact_label": item.get("impact_label", ""),
        })

    friction = aggregate_friction(insight_rows, now, iwin)

    return {
        "days": days, "insight_days": insight_days, "dead_days": dead_days,
        "host": host, "items": items, "friction": friction,
        "totals": {
            "tool_events": len(tool_rows), "command_events": len(cmd_rows),
            "insight_sessions": len(insight_rows),
        },
    }


def _item_ts(item, tool_rows, cmd_rows, cwd_rows) -> list:
    """Ts strings attributable to `item` (for the sub-window DEAD re-bucket)."""
    if item["via"] == "tool":
        out = []
        for r in tool_rows:
            p = parse_payload(r.get("payload"))
            name = r.get("tool") or r.get("text") or p.get("tool")
            if name == item["tool"]:
                out.append(r.get("ts"))
        return out
    if item["via"] == "command":
        pres = item["prefixes"]
        return [r.get("ts") for r in cmd_rows
                if command_matches(r.get("text"), pres)]
    if item["via"] == "cwd":
        return [r.get("ts") for r in cwd_rows.get(item["id"], [])]
    return []


def _count_within(ts_list: list, now: float, window_s: float) -> int:
    n = 0
    for ts in ts_list:
        e = ch_ts_to_epoch(ts)
        if e is not None and (now - e) <= window_s:
            n += 1
    return n


# --------------------------------------------------------------------------- #
# Render
# --------------------------------------------------------------------------- #
_ARROW = {"up": "▲", "down": "▼", "flat": "→"}


def render(data: dict) -> str:
    out: list[str] = []
    host = data.get("host") or "all hosts"
    out.append(f"=== adoption-scan: trailing {data['days']}d  (host: {host}) ===")
    t = data["totals"]
    out.append(f"  telemetry: {t['tool_events']} tool-invocation events, "
               f"{t['command_events']} slash-commands, "
               f"{t['insight_sessions']} insight sessions in window")

    # ---- ADOPTION ----
    out.append("\n## ADOPTION (is it USED?)")
    out.append("   count = invocations in window; trend = this-half vs prior-half; "
               "DEAD = 0 uses in the dead window.")
    dead_any = False
    for it in data["items"]:
        flag = "  ⚠ DEAD" if it["dead"] else ""
        if it["dead"]:
            dead_any = True
        opt = "opt-in" if it["opt_in"] else "auto"
        arrow = _ARROW.get(it["trend"], "?")
        out.append(f"  [{opt:6}] {it['total']:>4}  {arrow} "
                   f"({it['recent']} vs {it['prior']})  {it['label']}{flag}")
        if it["outcomes"]:
            mix = "  ".join(f"{k}={v}" for k, v in sorted(it["outcomes"].items()))
            out.append(f"            outcomes: {mix}")
    if dead_any:
        out.append("  ⚠ DEAD items had ZERO uses in the window — an opt-in tool that "
                   "is DEAD is a candidate to retire or auto-trigger.")

    # ---- IMPACT ----
    out.append("\n## IMPACT (is it YIELDING?)")
    out.append("   invocation COUNT proves USE, not VALUE. The outcome MIX below is "
               "the closest value signal — read it as directional.")
    any_impact = False
    for it in data["items"]:
        if not it["impact_label"]:
            continue
        any_impact = True
        hits = sum(it["impact"].values())
        detail = ", ".join(f"{k}={v}" for k, v in it["impact"].items()) or "—"
        out.append(f"  {it['id']:20} {hits:>4}  {it['impact_label']}")
        out.append(f"            ({detail})")
    if not any_impact:
        out.append("  (no impact-bearing outcomes recorded yet)")

    # ---- FRICTION TREND ----
    fr = data["friction"]
    out.append("\n## DRAFTER FRICTION TREND (directional — NOT a controlled metric)")
    out.append(f"   session-insight friction over two {data['insight_days'] // 2}d halves "
               f"({fr['sessions_prior']} prior → {fr['sessions_recent']} recent sessions). "
               "Confounds: different tickets/models per window. Baseline was "
               "permission_block=57.")
    for k in FRICTION_TRACKED:
        rec = fr["recent"][k]
        pri = fr["prior"][k]
        d = fr["deltas"][k]
        sign = "+" if d > 0 else ""
        arrow = _ARROW.get(_trend(rec, pri), "?")
        out.append(f"  {k:20} prior {pri:>4}  →  recent {rec:>4}   "
                   f"({arrow} {sign}{d})")
    if fr["unreadable"]:
        out.append(f"  ({fr['unreadable']} insight session(s) unreadable — excluded)")

    out.append("\nCAVEATS: counts prove USE not VALUE; the friction trend is "
               "directional (window confounds). cwd-detected items (drafter) are "
               "heuristic. Telemetry-off → this report degrades to a clean notice.")
    return "\n".join(out)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Adoption + impact tracking for the shipped gametape tools.")
    p.add_argument("--days", type=int, default=DEFAULT_DAYS,
                   help=f"adoption window in days (default {DEFAULT_DAYS})")
    p.add_argument("--insight-days", type=int, default=DEFAULT_INSIGHT_DAYS,
                   help=f"friction-trend window in days (default {DEFAULT_INSIGHT_DAYS})")
    p.add_argument("--dead-days", type=int, default=None,
                   help="0-uses-within-N-days => DEAD (default = --days)")
    p.add_argument("--host", default=None, help="restrict to one ACTIVITY_HOST label")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    return p.parse_args(argv)


def main(argv=None) -> int:
    a = parse_args(argv)
    if a.days <= 0 or a.insight_days <= 0:
        print("adoption-scan: --days / --insight-days must be positive", file=sys.stderr)
        return 2
    dead_days = a.dead_days if a.dead_days is not None else a.days
    if dead_days <= 0:
        print("adoption-scan: --dead-days must be positive", file=sys.stderr)
        return 2

    try:
        conn = Q.CHConn.from_env()
    except RuntimeError as e:
        print(f"adoption-scan: telemetry not configured — {e}", file=sys.stderr)
        return 0
    client = Q.CHClient(conn)
    try:
        data = gather(client, a.days, a.insight_days, dead_days, host=a.host)
    except TelemetryUnavailable as e:
        print(f"adoption-scan: telemetry unavailable ({e}); nothing to report.",
              file=sys.stderr)
        return 0

    if a.json:
        print(json.dumps(data, indent=2, default=str))
    else:
        print(render(data))
    return 0


if __name__ == "__main__":
    sys.exit(main())
