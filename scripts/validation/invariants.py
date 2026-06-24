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

# A single event cannot plausibly represent more than this much active time.
# Browser nav active_ms and zsh duration_ms both live in the same ms space.
ACTIVE_MS_CAP = 6 * 60 * 60 * 1000  # 6h in one event = implausible
# Slack for the local-vs-UTC timezone offset on the "no future ts" check.
FUTURE_SLACK_HOURS = 26  # > max |tz offset| (14h) + margin
# Oldest plausible ts: the table TTL is 180d, but data only started 2026; guard
# against an epoch-0 / 1970 clock-skew bug.
OLDEST_PLAUSIBLE = "2025-01-01 00:00:00"

EXPECTED_HOSTS = {"workbench", "laptop"}
EXPECTED_SOURCES = {"zsh", "tmux", "keys", "browser", "claude"}


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


def eval_per_host_hour_cap(rows, cap_ms: int = 60 * 60 * 1000):
    """PASS iff no (host,hour) bucket sums more active time than the wall clock.

    rows: [{host, hour, active_ms}, ...]. Summed active time within any one
    clock-hour bucket cannot exceed 60 minutes of real time (a sanity bound on
    double-counted / runaway active_ms). A small overshoot tolerance covers the
    bucket-boundary case where a single long dwell straddles the hour edge.
    """
    tol = int(cap_ms * 0.05)
    over = []
    for r in rows or []:
        ams = int(r.get("active_ms") or 0)
        if ams > cap_ms + tol:
            over.append(f"{r.get('host')}@{r.get('hour')}={ams}ms")
    if over:
        return (False, f"per-(host,hour) active time over 60min: {', '.join(over)}")
    return (True, "per-(host,hour) active time within 60min")


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
        Invariant(
            "active_ms_capped",
            "SELECT count() FROM " + table + " WHERE "
            f"toUInt32OrZero(JSONExtractString(payload, 'active_ms')) > {ACTIVE_MS_CAP}",
            eval_zero_violations(f"active_ms > {ACTIVE_MS_CAP}ms cap"),
        ),
        Invariant(
            "duration_ms_capped",
            f"SELECT count() FROM {table} WHERE duration_ms > {ACTIVE_MS_CAP}",
            eval_zero_violations(f"duration_ms > {ACTIVE_MS_CAP}ms cap"),
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
        Invariant(
            "per_host_hour_active_cap",
            # Sum active time per (host, clock-hour) bucket.
            "SELECT host, toStartOfHour(ts) AS hour, "
            "sum(toUInt32OrZero(JSONExtractString(payload, 'active_ms')) + duration_ms) AS active_ms "
            f"FROM {table} GROUP BY host, hour",
            eval_per_host_hour_cap,
        ),
    ]


def run_invariants(client: CHClient) -> list[Result]:
    results: list[Result] = []
    table = client.conn.fq_table
    for inv in build_invariants(table):
        try:
            # Multi-row evaluators expect rows(); scalar evaluators expect a number.
            if inv.name in ("expected_hosts", "expected_sources", "per_host_hour_active_cap"):
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
