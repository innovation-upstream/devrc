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
    Logic lifted from dashboard panel "Browser attention by domain (i3-derived, s)"."""
    h = Q.sql_quote(host)
    return (
        "WITH brave AS ("
        "SELECT bs, be FROM ("
        "SELECT toUnixTimestamp64Milli(ts) AS bs, app, least("
        "(leadInFrame(toUnixTimestamp64Milli(ts),1,toUnixTimestamp64Milli(ts)) "
        "OVER (PARTITION BY host ORDER BY ts ASC ROWS BETWEEN CURRENT ROW AND UNBOUNDED FOLLOWING)), "
        "toUnixTimestamp64Milli(ts)+1800000) AS be "
        f"FROM activity.events WHERE source='i3' AND kind='window-focus' AND host IN ({h}) AND ts>now()-{win}"
        ") WHERE app='Brave-browser'"
        "), dom AS ("
        "SELECT d, ds, least(de, ds+1800000) AS de FROM ("
        "SELECT if(domain(text)!='',domain(text),netloc(text)) AS d, toUnixTimestamp64Milli(ts) AS ds, "
        "(leadInFrame(toUnixTimestamp64Milli(ts),1,toUnixTimestamp64Milli(ts)+1800000) "
        "OVER (PARTITION BY host ORDER BY ts ASC ROWS BETWEEN CURRENT ROW AND UNBOUNDED FOLLOWING)) AS de "
        f"FROM activity.events WHERE source='browser' AND kind='nav' AND text!='' AND host IN ({h}) AND ts>now()-{win}"
        ")) "
        "SELECT d AS domain, round(sum(greatest(0, least(be,de)-greatest(bs,ds)))/60000,1) AS attention_min "
        "FROM brave CROSS JOIN dom WHERE be>ds AND de>bs "
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
def gather(client: "Q.CHClient", days: int, host: str) -> dict:
    win = window_seconds(days)
    repeated = client.rows(q_repeated_commands(win))
    return {
        "days": days,
        "host": host,
        "automation": {
            "repeated_commands": repeated,
            "top_binaries": client.rows(q_top_binaries(win)),
            "sequence_hint": sequence_hint([r.get("cmd", "") for r in repeated]),
        },
        "bottlenecks": {
            "binaries_by_wait": client.rows(q_binaries_by_wait(win)),
        },
        "signal_noise": {
            "context_switches": (client.rows(q_context_switches(win, host)) or [{}])[0],
            "attention_by_app": client.rows(q_attention_by_app(win, host)),
            "browser_by_domain": client.rows(q_browser_by_domain(win, host)),
            "deep_work": (client.rows(q_deep_work(win, host)) or [{}])[0],
        },
    }


# --------------------------------------------------------------------------- #
# Render
# --------------------------------------------------------------------------- #
def render(data: dict) -> str:
    days = data["days"]
    out: list[str] = []
    out.append(f"=== activity-scan: trailing {days}d  (i3 sections: host={data['host']}) ===")

    # ---- AUTOMATION CANDIDATES ----
    aut = data["automation"]
    out.append("\n## AUTOMATION CANDIDATES")
    out.append("   Look for: exact commands you retype + the binaries you lean on — wrap/alias the top ones.")
    rc = aut["repeated_commands"]
    out.append("  Top repeated exact commands (n = times run):")
    if not rc:
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
    if not tb:
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
    if not bw:
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
    out.append(f"  Context switches/active hour:  avg {cs.get('avg_per_hr','-')}   peak {cs.get('peak','-')}")
    dw = sn["deep_work"]
    out.append(f"  Deep-work (Alacritty) blocks:  >=10min: {dw.get('b10','-')}   "
               f">=25min: {dw.get('b25','-')}   longest: {fmt_min(dw.get('longest_min'))}")
    aba = sn["attention_by_app"]
    out.append("  Attention by app (i3 dwell, 30min cap/gap):")
    if not aba:
        out.append("    (no i3 data — laptop GUI host?)")
    else:
        peak = max((num(r.get("dwell_min")) for r in aba), default=0)
        for r in aba:
            out.append(f"    {fmt_min(r.get('dwell_min')):>9}  {bar(r.get('dwell_min'), peak):<20}  {r.get('app','')}")
    bd = sn["browser_by_domain"]
    out.append("  Browser attention by domain (i3-derived ∩ nav):")
    if not bd:
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
        description="Weekly activity-telemetry report: automation candidates, bottlenecks, attention.")
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
