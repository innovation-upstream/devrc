#!/usr/bin/env python3
"""activity-scan — weekly "where is my workflow time going + what to automate" report.

The zsh/i3/browser analogue of toil-scan.py (which scans Claude transcripts).
This scans the personal activity telemetry in ClickHouse (`activity.events`) over a
trailing window and surfaces three things, deterministically, read-only, no LLM:

  1. AUTOMATION CANDIDATES  — exact repeated shell commands + the binaries you run most.
  2. BOTTLENECKS            — where wall-clock WAIT time goes (slow commands).
  3. SIGNAL vs NOISE        — attention split + context-switch rate + deep-work blocks
                              (i3 = laptop-only).

Output is a ranked, skimmable report. Each section leads with a one-line "what to look
for" header, then the ranked rows.

HONESTY NOTE: "signal vs noise" here is switch-rate / attention-SPLIT only — it measures
HOW fragmented your attention is and WHERE it goes, not whether any given app/domain is
"good" or "bad". The value judgement (is github.com signal or noise right now?) needs a
human or an LLM layer on top. Treat this as a descriptive instrument: it earns its keep
only if reading it actually changes what you automate or how you protect focus.

Credentials (read-only reader, from env — NEVER hardcoded):
  export CLICKHOUSE_URL=http://192.168.50.94:30123    # workbench LAN endpoint
  export CLICKHOUSE_USER=activity_reader
  export CLICKHOUSE_PASSWORD=<reader-password>
Populate the password from SOPS (homelab-talos trunk):
  git -C ~/workspace/homelab-talos fetch origin trunk -q
  git -C ~/workspace/homelab-talos show origin/trunk:clusters/homelab/apps/activity/secrets.enc.yaml > /tmp/s.yaml
  export CLICKHOUSE_PASSWORD=$(SOPS_AGE_KEY_FILE=~/workspace/homelab-talos/.secrets/age.key \
      sops -d --extract '["stringData"]["reader-password"]' /tmp/s.yaml); rm -f /tmp/s.yaml

Usage:
  activity-scan.py [--days N] [--json] [--laptop-host NAME]
  --days         trailing window in days (default 7)
  --json         machine-readable output (the raw section data)
  --laptop-host  host value for the GUI (i3) sections (default 'laptop')

PARTIAL REPORTS (exit 3). The report is seven independent queries and any one of them
can be stopped by the SERVER rather than by anything wrong with the query: activity's
ClickHouse runs in a 3 GiB pod with max_server_memory_usage 2.5 GiB and CH 25.x corrects
that server-wide tracker against container RSS, so under load the OvercommitTracker stops
whichever query is allocating. Measured 2026-09-12 over 25 whole-report rounds,
`q_top_binaries` and `q_context_switches` were EACH killed independently. So a
CHQueryError in one section no longer destroys the whole report: it is RECORDED, the
other six still print, and the failed one prints an explicit marker.

  🔴 UNAVAILABLE and EMPTY ARE DIFFERENT FACTS. A section that returned zero rows prints
  its own "(none…)" line; a section whose query FAILED prints `!! SECTION UNAVAILABLE`
  and names the reason. Neither is ever silently omitted — a truncated report that reads
  as a complete one is the worst of the three outcomes.

Exit status:
  0  full report — every section computed
  2  usage error (bad --days)
  3  PARTIAL — the report printed but at least one section is unavailable. Deliberately
     not 0: a caller redirecting stdout must be able to tell a whole report from a holed
     one without parsing it (same reasoning as scripts/gate.sh's 90/91 — "it ran, but you
     cannot read it as a full verdict"). The failure detail is on stderr too, and in the
     `failures` object under --json.
  CHUnreachable (server down / DNS / timeout) is NOT degraded — nothing can be said about
  any section, so it aborts, exactly as chquery's taxonomy prescribes.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Reuse the shared ClickHouse client + creds-from-env handling (no new deps).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "validation"))
import chquery as Q  # noqa: E402


# --------------------------------------------------------------------------- #
# Pure logic (unit-tested without a live ClickHouse)
# --------------------------------------------------------------------------- #
def window_seconds(days: int) -> int:
    """Trailing window length in seconds for the `ts > now() - N` filters."""
    if days <= 0:
        raise ValueError("--days must be positive")
    return days * 86400


# Shell wrappers / clusters worth calling out as obvious automation targets when
# they show up among the top repeated commands. Deterministic substring match only
# — a hint, not a verdict.
SEQUENCE_HINTS = (
    ("civitai app create", "civitai dogfood app lifecycle (create → upgrade → rm) — scriptable as one command"),
    ("dogfood-manual", "civitai dogfood app lifecycle (create → upgrade → rm) — scriptable as one command"),
)


def sequence_hint(commands: list[str]) -> str | None:
    """Return a single deterministic hint if the top commands match a known cluster."""
    blob = "\n".join(commands).lower()
    for needle, msg in SEQUENCE_HINTS:
        if needle in blob:
            return msg
    return None


def fmt_min(v) -> str:
    """Format a minutes value compactly (e.g. 429.1 -> '429.1m', 5.0 -> '5.0m')."""
    if v is None:
        return "-"
    return f"{float(v):.1f}m"


def fmt_s(v) -> str:
    if v is None:
        return "-"
    return f"{float(v):.1f}s"


def num(v, default=0):
    """Coerce a ClickHouse JSON field to a number. UInt64/Int64 come back as quoted
    strings in JSONEachRow, floats as numbers, NULL as None — normalize all to a number."""
    if v is None:
        return default
    if isinstance(v, (int, float)):
        return v
    try:
        s = str(v).strip()
        return int(s) if s.lstrip("-").isdigit() else float(s)
    except (ValueError, TypeError):
        return default


def bar(n, peak, width: int = 20) -> str:
    """A tiny ASCII bar for ranked rows (deterministic, no color)."""
    n, peak = num(n), num(peak)
    if peak <= 0:
        return ""
    filled = max(1, round(n / peak * width)) if n > 0 else 0
    return "█" * filled


# --------------------------------------------------------------------------- #
# Queries (validated live against activity.events — see header)
# --------------------------------------------------------------------------- #
def q_repeated_commands(win: int) -> str:
    return (
        "SELECT count() n, any(host) host, substring(text,1,60) cmd "
        "FROM activity.events "
        f"WHERE source='zsh' AND kind='command' AND ts>now()-{win} AND text!='' "
        "GROUP BY text HAVING n>=4 ORDER BY n DESC LIMIT 20"
    )


def q_top_binaries(win: int) -> str:
    return (
        "SELECT count() n, splitByChar(' ', trim(BOTH ' ' FROM text))[1] bin "
        "FROM activity.events "
        f"WHERE source='zsh' AND kind='command' AND ts>now()-{win} AND text!='' "
        "GROUP BY bin ORDER BY n DESC LIMIT 20"
    )


def q_binaries_by_wait(win: int) -> str:
    return (
        "SELECT splitByChar(' ', trim(BOTH ' ' FROM text))[1] bin, count() n, "
        "round(sum(duration_ms)/60000,1) tot_min, round(median(duration_ms)/1000,1) med_s, "
        "round(max(duration_ms)/1000,1) max_s "
        "FROM activity.events "
        f"WHERE source='zsh' AND kind='command' AND ts>now()-{win} AND duration_ms<7200000 "
        "GROUP BY bin HAVING n>=3 ORDER BY tot_min DESC LIMIT 15"
    )


def q_context_switches(win: int, host: str) -> str:
    h = Q.sql_quote(host)
    return (
        "SELECT round(avg(sw),1) avg_per_hr, max(sw) peak FROM ("
        "SELECT toStartOfHour(ts) h, count() sw FROM activity.events "
        f"WHERE source='i3' AND kind='window-focus' AND host={h} AND ts>now()-{win} "
        "GROUP BY h HAVING sw>5)"
    )


def q_attention_by_app(win: int, host: str) -> str:
    h = Q.sql_quote(host)
    return (
        "SELECT app, round(sum(dwell_ms)/60000,1) dwell_min FROM ("
        "SELECT app, kind, least("
        "leadInFrame(toUnixTimestamp64Milli(ts),1,toUnixTimestamp64Milli(ts)) "
        "OVER (PARTITION BY host ORDER BY ts ASC ROWS BETWEEN CURRENT ROW AND UNBOUNDED FOLLOWING) "
        "- toUnixTimestamp64Milli(ts), 1800000) AS dwell_ms "
        f"FROM activity.events WHERE source='i3' AND host={h} AND ts>now()-{win}) "
        "WHERE kind='window-focus' AND app!='' GROUP BY app ORDER BY dwell_min DESC LIMIT 15"
    )


def q_browser_by_domain(win: int, host: str) -> str:
    """i3 Brave-focused intervals ∩ nav-domain timeline (the project's i3-derived
    browser-attention metric). URL is in `text`; domain via domain()/netloc() fallback.
    Same measure as dashboard panel "Browser attention by domain (i3-derived, s)":
    per domain, the wall-clock time during which Brave held i3 focus AND that domain
    was the most recent navigation.

    🔴 COMPUTED AS A SINGLE-PASS SWEEP OVER INTERVAL BOUNDARIES — NOT AS A JOIN. The
    obvious spelling, and the one this used to ship, is `brave CROSS JOIN dom` with an
    overlap predicate. That materialises |brave| x |dom| candidate pairs, which is
    QUADRATIC in the window: measured 2026-09-12 against the live server, 727 x 831 =
    604k pairs at --days 7 against 2,799 x 3,180 = 8.9M at --days 30 — 14.7x the work
    for a 4.3x wider window. The pairs are streamed, not held, so the query's own
    tracked peak stayed modest (16 MiB at 30d, still the largest of the report's seven
    queries; the next is 10 MiB). What broke was the SERVER, not the query's own
    budget: activity's ClickHouse runs in a 3 GiB pod with max_server_memory_usage
    2.5 GiB, and CH 25.x corrects that server-wide tracker against container RSS. A
    burst of 40 runs of the join raised the container's memory.current by ~110-155 MiB
    of allocator churn; the same burst of a plain count() moved it -18 MiB and of this
    sweep +14 MiB. Once the server-wide tracker sits at the ceiling the OvercommitTracker
    stops whichever query is allocating, and that was this one — hence the user-visible
    `Code: 241 … (total) memory limit exceeded … While executing JoiningTransform`.
    Interleaved failure rates at --days 30, every arm in the same loop so all three saw
    the same server conditions: join 9/225, this sweep 0/225, plain count() control
    0/145. The gap widens with the window — at --days 90 the join failed 11 of 12 runs
    and at --days 120 all 12, the sweep none of either.

    ⚠ TWO HONEST CAVEATS. (1) The trigger is load, so the join did not fail every time
    and this sweep is NOT immune by construction — it was stopped once in a further 25
    whole-report rounds. What the rewrite removes is this report's own dominant
    contribution to the pressure, not the ceiling. (2) The report has no per-section
    degradation: in those same 25 rounds `q_top_binaries` and `q_context_switches` were
    each killed too, and any one CHQueryError still aborts the whole gather().

    The rewrite is exact, not an approximation. Each interval family is internally
    non-overlapping — every interval runs from one `ts` to the next `ts` in the SAME
    stream, capped at 30 min — so the join's pairwise-overlap sum is precisely the
    measure of the set intersection, and a sweep computes the same measure: emit two
    boundary rows per interval, carry Brave-focus depth and the active domain forward
    across the merged stream, and charge each gap between consecutive boundaries to the
    domain in force. 2 * (|brave| + |dom|) rows — 12k at --days 30, LINEAR in the window.

    Measured equality against the join it replaces: byte-identical output at --days
    7/14/21/30 (12 paired live runs each). At --days 90 fourteen of fifteen rows still
    match exactly and one differs by 0.2 of 156 minutes — traced to three nav events
    sharing one identical MILLISECOND, where `ORDER BY ts` has no tie-break, so which of
    the tied rows gets the non-zero interval is undefined. That ambiguity is pre-existing
    and belongs to the data, not to the rewrite; the sweep at least resolves it the same
    way every run (25/25 identical), which the join does not promise.
    """
    h = Q.sql_quote(host)
    return (
        # --- the two interval families (unchanged from the join version) ---
        "WITH brave AS ("
        "SELECT host, bs, be FROM ("
        "SELECT host, toUnixTimestamp64Milli(ts) AS bs, app, least("
        "(leadInFrame(toUnixTimestamp64Milli(ts),1,toUnixTimestamp64Milli(ts)) "
        "OVER (PARTITION BY host ORDER BY ts ASC ROWS BETWEEN CURRENT ROW AND UNBOUNDED FOLLOWING)), "
        "toUnixTimestamp64Milli(ts)+1800000) AS be "
        f"FROM activity.events WHERE source='i3' AND kind='window-focus' AND host IN ({h}) AND ts>now()-{win}"
        ") WHERE app='Brave-browser'"
        "), dom AS ("
        "SELECT host, d, ds, least(de, ds+1800000) AS de FROM ("
        "SELECT host, if(domain(text)!='',domain(text),netloc(text)) AS d, toUnixTimestamp64Milli(ts) AS ds, "
        "(leadInFrame(toUnixTimestamp64Milli(ts),1,toUnixTimestamp64Milli(ts)+1800000) "
        "OVER (PARTITION BY host ORDER BY ts ASC ROWS BETWEEN CURRENT ROW AND UNBOUNDED FOLLOWING)) AS de "
        f"FROM activity.events WHERE source='browser' AND kind='nav' AND text!='' AND host IN ({h}) AND ts>now()-{win}"
        ")), "
        # --- one boundary row per interval endpoint: 2*(|brave|+|dom|) rows total ---
        # bd    : +1/-1 delta on the Brave-focus depth.
        # dm    : the domain this boundary puts in force ('' = none).
        # ord   : tie-break WITHIN one instant, so a domain that starts exactly when the
        #         previous one ends wins over the end. Folded into dseq below.
        # dseq  : t*4+ord for domain boundaries, 0 for Brave ones, so a running max()
        #         carries the LATEST domain boundary forward without a join or a lookup.
        "ev AS ("
        "SELECT host, bs AS t, 0 AS ord, 1 AS bd, '' AS dm, 0 AS dseq FROM brave "
        "UNION ALL SELECT host, be, 0, -1, '', 0 FROM brave "
        "UNION ALL SELECT host, de, 1, 0, '', de*4+1 FROM dom "
        "UNION ALL SELECT host, ds, 2, 0, d, ds*4+2 FROM dom"
        "), "
        # --- the sweep: gap to the next boundary + the state in force across it ---
        "seg AS ("
        "SELECT (leadInFrame(t,1,t) OVER (PARTITION BY host ORDER BY t ASC, ord ASC "
        "ROWS BETWEEN CURRENT ROW AND UNBOUNDED FOLLOWING))-t AS ms, "
        "(sum(bd) OVER (PARTITION BY host ORDER BY t ASC, ord ASC "
        "ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)) AS brave_on, "
        "tupleElement((max((dseq, dm)) OVER (PARTITION BY host ORDER BY t ASC, ord ASC "
        "ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)),2) AS d "
        "FROM ev) "
        "SELECT d AS domain, round(sum(ms)/60000,1) AS attention_min "
        "FROM seg WHERE brave_on>0 AND ms>0 AND d!='' "
        "GROUP BY d HAVING attention_min>0 ORDER BY attention_min DESC LIMIT 15"
    )


def q_deep_work(win: int, host: str) -> str:
    h = Q.sql_quote(host)
    return (
        "SELECT countIf(run_s>=600) b10, countIf(run_s>=1500) b25, round(max(run_s)/60,1) longest_min "
        "FROM ("
        "SELECT (leadInFrame(toUnixTimestamp64Milli(ts),1,toUnixTimestamp64Milli(ts)) "
        "OVER (PARTITION BY host ORDER BY ts ASC ROWS BETWEEN CURRENT ROW AND UNBOUNDED FOLLOWING) "
        "- toUnixTimestamp64Milli(ts))/1000 run_s, app "
        f"FROM activity.events WHERE source='i3' AND kind='window-focus' AND host={h} AND ts>now()-{win}) "
        "WHERE app='Alacritty' AND run_s<3600"
    )


# --------------------------------------------------------------------------- #
# Gather
# --------------------------------------------------------------------------- #
# The report's sections, in render order. This ledger is the SET of things that can
# independently fail, so it is what `failures` is keyed by and what the "N of M" banner
# counts against. Pinned two-way by the test suite: a query gather() runs without a
# ledger entry, or an entry no query produces, is a test failure — that is what stops an
# eighth section from being added un-degraded.
SECTION_LABELS: dict[str, str] = {
    "repeated_commands": "Top repeated exact commands",
    "top_binaries": "Top binaries (first token)",
    "binaries_by_wait": "Bottlenecks by wait time",
    "context_switches": "Context switches/active hour",
    "attention_by_app": "Attention by app",
    "browser_by_domain": "Browser attention by domain",
    "deep_work": "Deep-work (Alacritty) blocks",
}

# Printed for a section whose query FAILED. Deliberately distinct from every "(none)"
# line, and deliberately NOT a substring of the summary banner, so a test can count
# occurrences and mean it.
SECTION_UNAVAILABLE_MARK = "!! SECTION UNAVAILABLE"

EXIT_PARTIAL = 3


def _failure_reason(exc: "Q.CHQueryError") -> str:
    """One-line, bounded rendering of a ClickHouse query failure.

    ClickHouse error bodies are multi-line and can be long; a report line must stay a
    line. The numeric code is the load-bearing part (241 = memory limit) so it is
    surfaced separately by the caller and kept here too.
    """
    text = " ".join(str(exc).split())
    return text[:300] if text else f"{type(exc).__name__} (no detail)"


def gather(client: "Q.CHClient", days: int, host: str) -> dict:
    """Run every section, recording per-section failures instead of propagating them.

    🔴 Only CHQueryError is degraded. It means "the server answered and this specific
    query failed" — the pipeline is up and the other six sections are still worth having.
    CHUnreachable means nothing can be said about ANY query, so it propagates and the run
    aborts; catching it here would print six empty sections over a dead server and call
    that a report.
    """
    win = window_seconds(days)
    failures: dict[str, dict] = {}

    def section(key: str, sql: str, *, scalar: bool = False):
        try:
            rows = client.rows(sql)
        except Q.CHQueryError as e:
            failures[key] = {
                "section": SECTION_LABELS[key],
                "error": _failure_reason(e),
                "code": getattr(e, "code", None),
                "http_status": getattr(e, "http_status", None),
            }
            return {} if scalar else []
        return (rows or [{}])[0] if scalar else rows

    repeated = section("repeated_commands", q_repeated_commands(win))
    return {
        "days": days,
        "host": host,
        "automation": {
            "repeated_commands": repeated,
            "top_binaries": section("top_binaries", q_top_binaries(win)),
            "sequence_hint": sequence_hint([r.get("cmd", "") for r in repeated]),
        },
        "bottlenecks": {
            "binaries_by_wait": section("binaries_by_wait", q_binaries_by_wait(win)),
        },
        "signal_noise": {
            "context_switches": section(
                "context_switches", q_context_switches(win, host), scalar=True),
            "attention_by_app": section("attention_by_app", q_attention_by_app(win, host)),
            "browser_by_domain": section("browser_by_domain", q_browser_by_domain(win, host)),
            "deep_work": section("deep_work", q_deep_work(win, host), scalar=True),
        },
        # Always present, so a machine consumer can branch on them without a probe.
        "failures": failures,
        "partial": bool(failures),
    }


# --------------------------------------------------------------------------- #
# Render
# --------------------------------------------------------------------------- #
def unavailable_line(data: dict, key: str, indent: str = "    ") -> str | None:
    """The explicit marker for a section whose query failed, or None if it did not.

    🔴 A caller must print this INSTEAD of the section's empty-marker and never instead
    of nothing: "the query died" and "there was no data" are different facts, and a
    silently omitted section makes a holed report look complete.
    """
    f = (data.get("failures") or {}).get(key)
    if not f:
        return None
    return (f"{indent}{SECTION_UNAVAILABLE_MARK}: {f['section']} — the query FAILED, so "
            f"this section is MISSING, not empty. reason: {f['error']}")


def render(data: dict) -> str:
    days = data["days"]
    out: list[str] = []
    out.append(f"=== activity-scan: trailing {days}d  (i3 sections: host={data['host']}) ===")

    fails = data.get("failures") or {}
    if fails:
        out.append(
            f"!! PARTIAL REPORT — {len(fails)} of {len(SECTION_LABELS)} sections could not "
            f"be computed (the query FAILED; that is NOT the same as 'no data'): "
            + ", ".join(sorted(fails))
        )

    # ---- AUTOMATION CANDIDATES ----
    aut = data["automation"]
    out.append("\n## AUTOMATION CANDIDATES")
    out.append("   Look for: exact commands you retype + the binaries you lean on — wrap/alias the top ones.")
    rc = aut["repeated_commands"]
    out.append("  Top repeated exact commands (n = times run):")
    if (u := unavailable_line(data, "repeated_commands")):
        out.append(u)
    elif not rc:
        out.append("    (none crossed n>=4)")
    else:
        peak = max((num(r.get("n")) for r in rc), default=0)
        for r in rc:
            out.append(f"    {num(r.get('n')):>4}  {bar(r.get('n'), peak):<20}  "
                       f"[{r.get('host','?')}] {r.get('cmd','')}")
    if aut.get("sequence_hint"):
        out.append(f"  ↳ hint: {aut['sequence_hint']}")
    tb = aut["top_binaries"]
    out.append("  Top binaries (first token):")
    if (u := unavailable_line(data, "top_binaries")):
        out.append(u)
    elif not tb:
        out.append("    (none)")
    else:
        peak = max((num(r.get("n")) for r in tb), default=0)
        for r in tb:
            out.append(f"    {num(r.get('n')):>4}  {bar(r.get('n'), peak):<20}  {r.get('bin','')}")

    # ---- BOTTLENECKS ----
    out.append("\n## BOTTLENECKS (wait time)")
    out.append("   Look for: high tot = total wall-clock you spend WAITING on it; high max = worst stalls.")
    bw = data["bottlenecks"]["binaries_by_wait"]
    out.append(f"  {'bin':<16} {'n':>4} {'tot':>9} {'med':>8} {'max':>9}")
    if (u := unavailable_line(data, "binaries_by_wait")):
        out.append(u)
    elif not bw:
        out.append("    (none)")
    else:
        for r in bw:
            out.append(f"  {str(r.get('bin','')):<16} {num(r.get('n')):>4} "
                       f"{fmt_min(r.get('tot_min')):>9} {fmt_s(r.get('med_s')):>8} {fmt_s(r.get('max_s')):>9}")

    # ---- SIGNAL vs NOISE ----
    sn = data["signal_noise"]
    out.append("\n## SIGNAL vs NOISE (attention; i3 = laptop-only)")
    out.append("   Look for: switch rate = fragmentation; attention split = where focus goes; deep-work = protected blocks.")
    out.append("   NOTE: this is switch-rate / attention-SPLIT only — 'good vs bad' is a human/LLM call, not measured here.")
    cs = sn["context_switches"]
    if (u := unavailable_line(data, "context_switches", indent="  ")):
        out.append(u)
    else:
        out.append(f"  Context switches/active hour:  avg {cs.get('avg_per_hr','-')}   peak {cs.get('peak','-')}")
    dw = sn["deep_work"]
    if (u := unavailable_line(data, "deep_work", indent="  ")):
        out.append(u)
    else:
        out.append(f"  Deep-work (Alacritty) blocks:  >=10min: {dw.get('b10','-')}   "
                   f">=25min: {dw.get('b25','-')}   longest: {fmt_min(dw.get('longest_min'))}")
    aba = sn["attention_by_app"]
    out.append("  Attention by app (i3 dwell, 30min cap/gap):")
    if (u := unavailable_line(data, "attention_by_app")):
        out.append(u)
    elif not aba:
        out.append("    (no i3 data — laptop GUI host?)")
    else:
        peak = max((num(r.get("dwell_min")) for r in aba), default=0)
        for r in aba:
            out.append(f"    {fmt_min(r.get('dwell_min')):>9}  {bar(r.get('dwell_min'), peak):<20}  {r.get('app','')}")
    bd = sn["browser_by_domain"]
    out.append("  Browser attention by domain (i3-derived ∩ nav):")
    if (u := unavailable_line(data, "browser_by_domain")):
        out.append(u)
    elif not bd:
        out.append("    (no browser/i3 overlap)")
    else:
        peak = max((num(r.get("attention_min")) for r in bd), default=0)
        for r in bd:
            out.append(f"    {fmt_min(r.get('attention_min')):>9}  {bar(r.get('attention_min'), peak):<20}  {r.get('domain','')}")

    return "\n".join(out)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Weekly activity-telemetry report: automation candidates, bottlenecks, attention.",
        epilog=("exit status: 0 = full report; 2 = usage error; "
                f"{EXIT_PARTIAL} = PARTIAL (printed, but at least one section's query "
                "failed and is marked UNAVAILABLE, not empty)."),
    )
    p.add_argument("--days", type=int, default=7, help="trailing window in days (default 7)")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.add_argument("--laptop-host", default="laptop", help="host value for the i3 (GUI) sections")
    return p.parse_args(argv)


def main(argv=None) -> int:
    a = parse_args(argv)
    if a.days <= 0:
        print("error: --days must be positive", file=sys.stderr)
        return 2
    conn = Q.CHConn.from_env()
    client = Q.CHClient(conn)
    data = gather(client, a.days, a.laptop_host)
    if a.json:
        print(json.dumps(data, indent=2, default=str))
    else:
        print(render(data))
    failures = data.get("failures") or {}
    if failures:
        # stderr as well as the report body: a caller redirecting stdout still sees it,
        # and the exit code says the same thing without needing the text parsed.
        for key, f in sorted(failures.items()):
            print(f"activity-scan: section {key} UNAVAILABLE (query failed): {f['error']}",
                  file=sys.stderr)
        print(f"activity-scan: PARTIAL report — {len(failures)} of {len(SECTION_LABELS)} "
              f"sections unavailable (exit {EXIT_PARTIAL})", file=sys.stderr)
        return EXIT_PARTIAL
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
