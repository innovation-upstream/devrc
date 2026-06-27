#!/usr/bin/env python3
"""invariants — SQL sanity battery over ALL of activity.events.

Each invariant is a (name, SQL, evaluator) triple. The SQL returns a small
result (usually one number = count of violating rows); the evaluator turns that
into PASS/FAIL with a human message. Run live with `python3 invariants.py`
(needs CLICKHOUSE_* env). The pure evaluators are unit-tested by feeding
synthetic rows that violate / satisfy each invariant.

Timezone caveat baked into the checks (see chquery.py): `ts` is stored as the
host's LOCAL wall clock while the server clock (now()) is UTC. A naive
`ts > now()` "future" check would NOT catch a clock-skew bug for a host east of
UTC and WOULD false-positive a host west of UTC. We therefore allow a tz slack
window (FUTURE_SLACK_HOURS) on the future check and report it explicitly.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))
from chquery import CHClient, CHConn  # noqa: E402

DURATION_SANITY_CAP = 24 * 60 * 60 * 1000  # >24h in one command = garbage
# Slack for the local-vs-UTC timezone offset on the "no future ts" check.
FUTURE_SLACK_HOURS = 26  # > max |tz offset| (14h) + margin
# Oldest plausible ts: the table TTL is 180d, but data only started 2026; guard
# against an epoch-0 / 1970 clock-skew bug.
OLDEST_PLAUSIBLE = "2025-01-01 00:00:00"

# RETIRED METRIC — browser extension `active_ms` (and its guards) ----------------
# The browser extension's per-page `active_ms` is STRUCTURALLY WRONG on the i3
# host and is NO LONGER a trusted or displayed metric. `chrome.idle` measures
# *system-wide* input (so typing in the terminal kept the browser counted as
# "active") and `chrome.windows.onFocusChanged` blur is unreliable on i3 — so the
# extension counted time-spent-in-OTHER-apps as browser engagement (a 10-min
# stretch focused entirely on Alacritty logged as ~60 min of Brave "active";
# 2026-06-27 11:00-12:00 UTC read ~144 min "active" against only 12.4 min of
# actual i3 Brave focus). The two invariants that guarded `active_ms`
# (`active_ms_capped`, `per_host_hour_active_cap`) and their constants
# (`ACTIVE_MS_CAP`, `ACTIVE_CAP_WINDOW_HOURS`) have therefore been REMOVED — the
# metric is being RETIRED, not silenced: there is nothing left to guard once it's
# no longer trusted/shown. True per-domain browser attention is now DERIVED
# downstream by intersecting i3 Brave-focused intervals with the active-tab
# domain timeline (see `derived_attention_consistent` below + the dashboard panel
# "Browser attention by domain (i3-derived, s)"). The vestigial `active_ms` field
# is still emitted by the extension (stripping it needs an operator Brave reload);
# that is a deferred follow-up and does not affect correctness here.

# Dwell cap applied to a single i3 focus interval, matching the dashboard's
# i3-dwell panels (an idle focus must not inflate attention). 30 minutes in ms.
DWELL_CAP_MS = 30 * 60 * 1000
# Tolerance for the derived-attention <= i3-Brave-dwell bound (boundary straddle /
# rounding). 2% of the dwell.
DERIVED_ATTENTION_TOL = 0.02
# Trailing window for the derived-attention consistency check (current-health).
DERIVED_ATTENTION_WINDOW_HOURS = 48

EXPECTED_HOSTS = {"workbench", "laptop"}
EXPECTED_SOURCES = {"zsh", "tmux", "keys", "browser", "claude", "i3"}


@dataclass
class Invariant:
    name: str
    sql: str
    # evaluator(value) -> (passed: bool, detail: str)
    evaluate: Callable


@dataclass
class Result:
    name: str
    passed: bool
    detail: str


# --------------------------------------------------------------------------- #
# Pure evaluators (unit-tested with synthetic inputs)
# --------------------------------------------------------------------------- #
def eval_zero_violations(label: str):
    """Build an evaluator that PASSES iff the violating-row count is 0."""
    def _ev(value):
        n = int(value or 0)
        return (n == 0, f"{label}: {n} violating row(s)")
    return _ev


def eval_unexpected_set(allowed: set, label: str):
    """Evaluator over a list of {value,count} rows; PASS iff all values allowed."""
    def _ev(rows):
        bad = []
        for r in rows or []:
            v = r.get("value")
            if v not in allowed and v not in (None, ""):
                bad.append(f"{v}({r.get('count')})")
        if bad:
            return (False, f"{label}: unexpected {', '.join(bad)}")
        return (True, f"{label}: only expected values")
    return _ev


def eval_derived_attention(rows):
    """PASS iff the DERIVED per-domain browser attention is internally consistent.

    This is the replacement guard for the retired `active_ms` invariants. It
    checks the new i3-derived attention metric (intersection of i3 Brave-focused
    intervals with the active-tab domain timeline) against two structural bounds
    that the intersection MUST satisfy by construction:

      1. No single domain's attention exceeds wall-clock for the window
         (an interval intersection can never exceed elapsed real time).
      2. The SUM of per-domain Brave attention is <= total i3 Brave dwell for the
         same window (the intersection is a subset of the i3 Brave intervals), up
         to a small tolerance for boundary-straddle/rounding.

    rows: a single-row result
      [{derived_total_ms, brave_dwell_ms, max_domain_ms, wallclock_ms}].
    If there's no browser/i3 data in the window (e.g. headless workbench) the
    metric is vacuously consistent -> PASS.
    """
    if not rows:
        return (True, "derived attention: no rows in window (vacuous PASS)")
    r = rows[0]
    derived_total = int(r.get("derived_total_ms") or 0)
    brave_dwell = int(r.get("brave_dwell_ms") or 0)
    max_domain = int(r.get("max_domain_ms") or 0)
    wallclock = int(r.get("wallclock_ms") or 0)
    problems = []
    if wallclock and max_domain > wallclock:
        problems.append(
            f"max-domain {max_domain}ms exceeds wall-clock {wallclock}ms")
    bound = int(brave_dwell * (1 + DERIVED_ATTENTION_TOL))
    if derived_total > bound:
        problems.append(
            f"sum-per-domain {derived_total}ms exceeds i3 Brave dwell "
            f"{brave_dwell}ms (+{int(DERIVED_ATTENTION_TOL*100)}% tol)")
    if problems:
        return (False, "derived attention inconsistent: " + "; ".join(problems))
    return (True,
            f"derived attention consistent: sum={derived_total}ms <= "
            f"brave_dwell={brave_dwell}ms, max_domain={max_domain}ms <= "
            f"wallclock={wallclock}ms")


# --------------------------------------------------------------------------- #
# Derived-attention SQL (the replacement guard for the retired active_ms checks)
# --------------------------------------------------------------------------- #
def derived_attention_sql(table: str = "activity.events",
                          window_hours: int = DERIVED_ATTENTION_WINDOW_HOURS,
                          cap_ms: int = DWELL_CAP_MS) -> str:
    """SQL computing the i3-derived per-domain browser attention summary.

    Intersects i3 Brave-focused intervals (dwell-capped) with the active-tab
    domain timeline (from browser `nav` events; the URL is in `text`), and
    returns ONE row of structural aggregates the evaluator can bound:

      derived_total_ms  sum of intersection across all domains
      brave_dwell_ms    total i3 Brave dwell over the window (the upper bound)
      max_domain_ms     largest single-domain attention (must be <= wall-clock)
      wallclock_ms      elapsed real time of the window

    The interval END for each i3 focus uses leadInFrame over ALL i3 focus events
    (the next focus of anything), then keeps only Brave rows — so a Brave
    interval ends when focus LEAVES Brave, not at the next Brave focus. Restricted
    to host='laptop' (the only GUI host with i3 + browser); Brave only (the
    extension runs only in Brave, so Chromium focus has no domain data).
    """
    w = f"ts > now() - INTERVAL {window_hours} HOUR"
    return (
        "WITH brave AS ( "
        "SELECT bs, be FROM ( "
        "SELECT toUnixTimestamp64Milli(ts) AS bs, app, "
        "least((leadInFrame(toUnixTimestamp64Milli(ts), 1, toUnixTimestamp64Milli(ts)) "
        "OVER (PARTITION BY host ORDER BY ts ASC ROWS BETWEEN CURRENT ROW AND UNBOUNDED FOLLOWING)), "
        f"toUnixTimestamp64Milli(ts) + {cap_ms}) AS be "
        f"FROM {table} WHERE source = 'i3' AND kind = 'window-focus' "
        f"AND host = 'laptop' AND {w} "
        ") WHERE app = 'Brave-browser' "
        "), dom AS ( "
        f"SELECT d, ds, least(de, ds + {cap_ms}) AS de FROM ( "
        "SELECT if(domain(text) != '', domain(text), netloc(text)) AS d, "
        "toUnixTimestamp64Milli(ts) AS ds, "
        f"(leadInFrame(toUnixTimestamp64Milli(ts), 1, toUnixTimestamp64Milli(ts) + {cap_ms}) "
        "OVER (PARTITION BY host ORDER BY ts ASC ROWS BETWEEN CURRENT ROW AND UNBOUNDED FOLLOWING)) AS de "
        f"FROM {table} WHERE source = 'browser' AND kind = 'nav' AND text != '' "
        f"AND host = 'laptop' AND {w} "
        ") ), "
        "per_domain AS ( "
        "SELECT dom.d AS d, "
        "sum(greatest(0, least(be, de) - greatest(bs, ds))) AS attn_ms "
        "FROM brave CROSS JOIN dom WHERE be > ds AND de > bs "
        "GROUP BY dom.d HAVING attn_ms > 0 "
        ") "
        "SELECT "
        "toInt64(ifNull((SELECT sum(attn_ms) FROM per_domain), 0)) AS derived_total_ms, "
        "toInt64(ifNull((SELECT sum(be - bs) FROM brave), 0)) AS brave_dwell_ms, "
        "toInt64(ifNull((SELECT max(attn_ms) FROM per_domain), 0)) AS max_domain_ms, "
        f"toInt64({window_hours} * 3600 * 1000) AS wallclock_ms"
    )


# --------------------------------------------------------------------------- #
# The battery
# --------------------------------------------------------------------------- #
def build_invariants(table: str = "activity.events") -> list[Invariant]:
    fs = FUTURE_SLACK_HOURS
    return [
        Invariant(
            "no_future_ts",
            f"SELECT count() FROM {table} WHERE ts > now() + INTERVAL {fs} HOUR",
            eval_zero_violations(f"ts beyond now()+{fs}h (tz-slack)"),
        ),
        Invariant(
            "duration_ms_nonneg",
            # duration_ms is UInt32 so it cannot be negative at rest; this guards
            # against a future schema/parse change re-introducing signed values.
            f"SELECT count() FROM {table} WHERE toInt64(duration_ms) < 0",
            eval_zero_violations("duration_ms < 0"),
        ),
        # NOTE: `active_ms_capped` was REMOVED here — the browser extension's
        # `active_ms` is a retired, untrusted metric (see the RETIRED METRIC note
        # at the top of this module). Its replacement is `derived_attention_consistent`.
        Invariant(
            "duration_ms_capped",
            # Raw duration is PRESERVED (a multi-hour interactive `claude` is real);
            # only flag values above the 24h garbage bound.
            f"SELECT count() FROM {table} WHERE duration_ms > {DURATION_SANITY_CAP}",
            eval_zero_violations(f"duration_ms > {DURATION_SANITY_CAP}ms (garbage)"),
        ),
        Invariant(
            "ts_not_ancient",
            f"SELECT count() FROM {table} WHERE ts < '{OLDEST_PLAUSIBLE}'",
            eval_zero_violations(f"ts before {OLDEST_PLAUSIBLE} (clock skew)"),
        ),
        Invariant(
            "expected_hosts",
            f"SELECT host AS value, count() AS count FROM {table} GROUP BY host",
            eval_unexpected_set(EXPECTED_HOSTS, "host"),
        ),
        Invariant(
            "expected_sources",
            f"SELECT source AS value, count() AS count FROM {table} GROUP BY source",
            eval_unexpected_set(EXPECTED_SOURCES, "source"),
        ),
        # NOTE: `per_host_hour_active_cap` was REMOVED here — it guarded the
        # retired `active_ms` metric (see the RETIRED METRIC note at the top).
        # `derived_attention_consistent` below is its structural replacement.
        Invariant(
            "derived_attention_consistent",
            # The replacement guard. Asserts the NEW i3-derived per-domain browser
            # attention is internally consistent: sum-per-domain <= total i3 Brave
            # dwell (the intersection is a subset of the i3 Brave intervals) and no
            # single domain exceeds wall-clock. Trailing-windowed (current-health,
            # like the metric it replaces): the append-only store never rewrites
            # history, but the DERIVED metric is bounded BY CONSTRUCTION so it can
            # never go inconsistent unless the query/data shape regresses — this
            # catches such a regression promptly. NB: `ts` is host-LOCAL and now()
            # is UTC, so the window edge is fuzzy by the tz offset — fine for a
            # recent-health window. Vacuously PASSes when there's no GUI data
            # (e.g. the headless workbench has no i3/browser rows).
            derived_attention_sql(table),
            eval_derived_attention,
        ),
    ]


def run_invariants(client: CHClient) -> list[Result]:
    results: list[Result] = []
    table = client.conn.fq_table
    for inv in build_invariants(table):
        try:
            # Multi-row evaluators expect rows(); scalar evaluators expect a number.
            if inv.name in ("expected_hosts", "expected_sources", "derived_attention_consistent"):
                data = client.rows(inv.sql)
            else:
                data = client.scalar(inv.sql)
            passed, detail = inv.evaluate(data)
            results.append(Result(inv.name, passed, detail))
        except Exception as exc:  # a failing query is itself a FAIL
            results.append(Result(inv.name, False, f"query error: {exc}"))
    return results


def collector_drop_state(client: CHClient) -> str:
    """Best-effort surface of collector drop-log state.

    The collector logs buffer-cap drops to its journal, not to ClickHouse, so it
    is not queryable from the reader. We surface a proxy: ingestion lag (max
    ingested_at - max ts) and total rows, so a stalled/dropping collector shows
    as stale ingestion. Returns a one-line summary; never raises.
    """
    try:
        table = client.conn.fq_table
        row = client.rows(
            f"SELECT count() AS rows, max(ts) AS max_ts, max(ingested_at) AS max_ing, "
            f"dateDiff('second', max(ts), now()) AS lag_s FROM {table}"
        )
        if not row:
            return "no rows"
        r = row[0]
        return (f"rows={r.get('rows')} max_ts={r.get('max_ts')} "
                f"max_ingested={r.get('max_ing')} ts_to_now_lag_s={r.get('lag_s')} "
                "(note: lag includes the local-vs-UTC offset)")
    except Exception as exc:
        return f"unavailable: {exc}"


def format_table(results: list[Result]) -> str:
    width = max((len(r.name) for r in results), default=10)
    lines = ["", f"{'INVARIANT':<{width}}  RESULT  DETAIL", f"{'-'*width}  ------  ------"]
    for r in results:
        tag = "PASS" if r.passed else "FAIL"
        lines.append(f"{r.name:<{width}}  {tag:<6}  {r.detail}")
    return "\n".join(lines)


def main(argv=None) -> int:
    conn = CHConn.from_env()
    client = CHClient(conn)
    results = run_invariants(client)
    print(f"Invariant battery over {conn.fq_table} @ {conn.url}")
    print(format_table(results))
    print()
    print("Collector/ingestion state: " + collector_drop_state(client))
    failed = [r for r in results if not r.passed]
    print(f"\n{len(results) - len(failed)}/{len(results)} invariants PASS"
          + (f"; {len(failed)} FAIL" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
