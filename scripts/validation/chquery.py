#!/usr/bin/env python3
"""chquery — ClickHouse HTTP client + dashboard query builders + pure math.

This is the shared core of the validation harness. It holds three kinds of
thing, all unit-testable WITHOUT a live ClickHouse:

  1. A tiny stdlib HTTP client for the ClickHouse `activity` database, reading
     CLICKHOUSE_URL / CLICKHOUSE_USER / CLICKHOUSE_PASSWORD from the env. The
     password is NEVER hardcoded — it must come from the env (the harness docs
     show pulling the reader password from SOPS).

  2. Builders for the EXACT dashboard queries (lifted from
     clusters/homelab/flux-system/charts/prom-stack/dashboards/activity-productivity.json),
     reduced to a scalar result and re-scoped to a replay window / run-id so the
     controlled replay (replay.py) can assert query == known-expected.

  3. Pure Python re-implementations of the same computations (switch count,
     longest deep-work block via gaps-and-islands, hour-of-day bucketing). The
     replay records its ground truth with THESE so the test
     suite can verify the math independently of ClickHouse, and so the live
     assertions compare CH's answer against an independent computation.

Timezone note (load-bearing): emit/spool_emit stamp `ts` with the host's LOCAL
wall clock (`date +"%Y-%m-%d %H:%M:%S.%3N"`), and the `ts` column is a bare
DateTime64(3) with NO column timezone. ClickHouse `toHour(ts)` therefore returns
the LOCAL hour of the stored wall-clock value, which is what the heatmap wants.
But `now()` / `today()` on the (UTC) server are in UTC, so any `ts <= now()`
style comparison mixes a local wall-clock against a UTC clock. `hour_of_day()`
here mirrors CH's behaviour: it reads the literal hour off the wall-clock string.
"""
from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime


# --------------------------------------------------------------------------- #
# Connection
# --------------------------------------------------------------------------- #
@dataclass
class CHConn:
    url: str
    user: str
    password: str
    database: str = "activity"
    table: str = "events"
    timeout: float = 15.0

    @classmethod
    def from_env(cls, env: dict | None = None) -> "CHConn":
        e = os.environ if env is None else env
        url = e.get("CLICKHOUSE_URL")
        if not url:
            raise RuntimeError(
                "CLICKHOUSE_URL not set. Export CLICKHOUSE_URL / CLICKHOUSE_USER / "
                "CLICKHOUSE_PASSWORD (reader creds via SOPS) before running."
            )
        return cls(
            url=url.rstrip("/"),
            user=e.get("CLICKHOUSE_USER", "activity_reader"),
            password=e.get("CLICKHOUSE_PASSWORD", ""),
            database=e.get("CLICKHOUSE_DATABASE", "activity"),
            table=e.get("CLICKHOUSE_TABLE", "events"),
            timeout=float(e.get("CLICKHOUSE_HTTP_TIMEOUT", "15")),
        )

    @property
    def fq_table(self) -> str:
        return f"{self.database}.{self.table}"


class CHClient:
    """Minimal read client. `scalar`/`rows` send SQL via the HTTP interface."""

    def __init__(self, conn: CHConn, opener=None):
        self.conn = conn
        self._opener = opener or urllib.request.urlopen

    def _request(self, sql: str, fmt: str) -> str:
        q = sql.strip()
        if fmt and " format " not in (" " + q.lower() + " "):
            q = f"{q} FORMAT {fmt}"
        params = urllib.parse.urlencode({"query": q})
        req = urllib.request.Request(f"{self.conn.url}/?{params}", method="GET")
        if self.conn.user:
            req.add_header("X-ClickHouse-User", self.conn.user)
        if self.conn.password:
            req.add_header("X-ClickHouse-Key", self.conn.password)
        resp = self._opener(req, timeout=self.conn.timeout)
        try:
            code = getattr(resp, "status", None) or resp.getcode()
            body = resp.read()
        finally:
            close = getattr(resp, "close", None)
            if close:
                close()
        if isinstance(body, bytes):
            body = body.decode("utf-8", "replace")
        if not (200 <= code < 300):
            raise RuntimeError(f"ClickHouse HTTP {code}: {body[:500]}")
        return body

    def scalar(self, sql: str):
        """Run a query returning a single value; return it parsed (int/float/str)."""
        out = self._request(sql, "TSV").strip()
        if out == "":
            return None
        first = out.splitlines()[0].split("\t")[0]
        return _coerce(first)

    def rows(self, sql: str) -> list[dict]:
        """Run a query and return rows as dicts (JSONEachRow)."""
        out = self._request(sql, "JSONEachRow").strip()
        rows = []
        for line in out.splitlines():
            if line:
                rows.append(json.loads(line))
        return rows


def _coerce(s: str):
    if s == "\\N" or s == "":
        return None
    try:
        return int(s)
    except ValueError:
        pass
    try:
        return float(s)
    except ValueError:
        return s


# --------------------------------------------------------------------------- #
# SQL scope helpers
# --------------------------------------------------------------------------- #
def sql_quote(s: str) -> str:
    """Single-quote-escape a string literal for ClickHouse SQL."""
    return "'" + s.replace("\\", "\\\\").replace("'", "\\'") + "'"


def run_scope(run_id: str) -> str:
    """A WHERE fragment isolating one replay run by its run-id marker.

    The replay tags every event's `session` column with the run-id, so a run is
    fully isolable even though the reader cannot DELETE. This makes assertions
    deterministic regardless of other (historical) data in the table.
    """
    return f"session = {sql_quote(run_id)}"


# --------------------------------------------------------------------------- #
# Dashboard query builders (verbatim logic, scoped + reduced to a scalar)
# --------------------------------------------------------------------------- #
def q_event_count(where: str, table: str = "activity.events") -> str:
    return f"SELECT count() AS value FROM {table} WHERE {where}"


def q_command_count(where: str, table: str = "activity.events") -> str:
    return (
        f"SELECT count() AS value FROM {table} "
        f"WHERE source = 'zsh' AND text != '' AND ({where})"
    )


def q_nav_count(where: str, table: str = "activity.events") -> str:
    return (
        f"SELECT count() AS value FROM {table} "
        f"WHERE source = 'browser' AND text != '' AND ({where})"
    )


def q_app_switches(where: str, table: str = "activity.events") -> str:
    return (
        "SELECT sum(is_switch) AS value FROM ("
        "SELECT app != lagInFrame(app, 1, app) OVER (ORDER BY ts ASC) AS is_switch "
        f"FROM {table} WHERE app != '' AND ({where})"
        ")"
    )


def q_longest_deep_work_ms(where: str, table: str = "activity.events") -> str:
    """Longest uninterrupted same-app run, in MILLISECONDS (gaps-and-islands).

    Logic verbatim from the dashboard: islands formed by a running sum of
    is_switch; each island's span is (max(ts)-min(ts))*1000; take the max.
    """
    return (
        "SELECT max(run_ms) AS value FROM ("
        "SELECT grp, (max(ts) - min(ts)) * 1000 AS run_ms FROM ("
        "SELECT ts, sum(is_switch) OVER (ORDER BY ts ASC ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS grp FROM ("
        "SELECT ts, app != lagInFrame(app, 1, app) OVER (ORDER BY ts ASC) AS is_switch "
        f"FROM {table} WHERE app != '' AND ({where})"
        ")) GROUP BY grp)"
    )


def q_hour_histogram(where: str, table: str = "activity.events") -> str:
    """Heatmap bucketing: count of events per LOCAL hour-of-day."""
    return (
        "SELECT toHour(ts) AS hour, count() AS events "
        f"FROM {table} WHERE {where} GROUP BY hour ORDER BY hour"
    )


# --------------------------------------------------------------------------- #
# Pure-Python re-implementations of the same computations
# --------------------------------------------------------------------------- #
def count_switches(apps: list[str]) -> int:
    """Number of app changes (matches CH lagInFrame(app,1,app), seed=self).

    The first non-empty row compares app against itself (lag default = current),
    so it is never a switch. Every subsequent row is a switch iff app != prev.
    Empty apps are filtered upstream (app != '') — mirror that here.
    """
    seq = [a for a in apps if a != ""]
    if not seq:
        return 0
    switches = 0
    prev = seq[0]
    for a in seq[1:]:
        if a != prev:
            switches += 1
        prev = a
    return switches


def _to_ms(ts) -> int:
    if isinstance(ts, datetime):
        return int(ts.timestamp() * 1000)
    return int(ts)


def longest_deep_work_ms(events: list[tuple]) -> int:
    """Longest uninterrupted same-app island, in ms. `events` = [(ts, app), ...].

    Mirrors the dashboard gaps-and-islands query: walk rows in ts order, start a
    new island on each app change, island length = last_ts - first_ts in the
    island (ms). Returns the max island length. ts may be datetime or epoch-ms.
    """
    rows = [(ts, app) for ts, app in events if app != ""]
    if not rows:
        return 0
    rows.sort(key=lambda r: r[0])

    best = 0
    island_start = rows[0][0]
    last_ts = rows[0][0]
    prev_app = rows[0][1]
    for ts, app in rows[1:]:
        if app != prev_app:
            best = max(best, _to_ms(last_ts) - _to_ms(island_start))
            island_start = ts
        last_ts = ts
        prev_app = app
    best = max(best, _to_ms(last_ts) - _to_ms(island_start))
    return best


def hour_of_day(ts) -> int:
    """Local hour-of-day bucket, matching CH `toHour(ts)` on a wall-clock ts.

    `ts` may be a 'YYYY-MM-DD HH:MM:SS[.fff]' wall-clock string (as emit writes),
    or a datetime. For the string form we read the hour off the literal value
    (no tz conversion) — exactly what CH does to a tz-less DateTime64.
    """
    if isinstance(ts, datetime):
        return ts.hour
    return int(str(ts).strip()[11:13])


def hour_histogram(timestamps: list) -> dict:
    out: dict = {}
    for ts in timestamps:
        h = hour_of_day(ts)
        out[h] = out.get(h, 0) + 1
    return out
