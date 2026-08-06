#!/usr/bin/env python3
"""Regrowth check for the homelab ClickHouse behind `activity.events`.

WHY THIS EXISTS
---------------
2026-08-03: the store was cut from **112.4 GB -> 82.1 MB**. The cause was
`system.trace_log` — 3.77 BILLION rows accumulated in ~5 weeks because the
server logger was at `trace` and the query-profiler log tables had no TTL.

Two fixes landed:
  A1  TRUNCATE (one-time, done).
  A2  a Flux change to the ClickHouse config: logger trace -> warning;
      `text_log` / `trace_log` / `latency_log` / `processors_profile_log`
      REMOVED; 7d TTLs on `metric_log` / `asynchronous_metric_log`, 14d on
      `query_log` / `part_log`; background merge pool 32 -> 8 slots.

🔴 NOBODY WILL NOTICE IF A2 SILENTLY STOPS WORKING. That is the entire reason
this file exists. A config revert, a chart bump that restores the upstream
defaults, or a TTL that quietly stops binding all reproduce the same 112 GB
outcome, and the only symptom is a disk filling up months later. So this runs on
a timer and is LOUD on failure.

MEASURED BASELINE (read-only, 2026-08-06, ~3 days post-fix)
-----------------------------------------------------------
  du -sk /var/lib/clickhouse/store    541,224 KiB  (528.5 MiB)
  sum of system.tables total_bytes    ~207 MiB
  tables in system.tables (non-schema) 120
  non-empty tables                     7 -- metric_log 86.7 MB / 306k rows,
                                       asynchronous_metric_log 75.6 MB / 70.5M,
                                       activity.events 34.3 MB / 527k,
                                       query_log 6.8 MB, part_log 4.1 MB,
                                       error_log 1.8 MB, query_metric_log 600 KB
  tables named `%_0`                   0
  trace_log/text_log/latency_log/processors_profile_log   ABSENT
  oldest row (metric_log/part_log/asynchronous_metric_log) ~3.55 days

🔴 THE `du` GAP IS NOT A LEAK. `du` sits persistently ~40% above the live
`total_bytes` sum because the merge pool was cut 32 -> 8, so inactive parts are
cleaned more slowly. Do NOT re-derive an alarm from that gap; the thresholds
below are on `du` itself, sized against the projected steady state of
~400 MiB live / ~550-700 MiB of `du`.

🔴 NO TTL HAD FIRED EVEN ONCE when this check was written — every system-log row
was younger than its own 7-day TTL. The mechanism that is supposed to bound
growth had NEVER been observed working. First real evidence: 2026-08-10 (7d) and
2026-08-17 (14d). Check 5 below is the only one that tests the MECHANISM rather
than today's outcome, and it is the one that matters on those dates.

WHAT IS ASSERTED
----------------
  1. du(/var/lib/clickhouse/store) > 2 GiB -> ALARM; > 1 GiB -> WARN.
  2. any table named `%_0` -> ALARM. Applying a `<ttl>` to a `*_log` makes
     ClickHouse RENAME the old table to `<name>_0` and start a fresh one. The
     renamed table KEEPS ITS ROWS WITH NO TTL, FOREVER — a silent regrowth
     vector that looks like nothing at all. Baseline is 0.
  3. `trace_log` / `text_log` / `latency_log` / `processors_profile_log` exists
     -> ALARM. A config revert is the likeliest cause and IS the 112 GB
     mechanism.
  4. any single table > 250 MiB -> ALARM.
  5. TTL EFFECTIVENESS: min(event_time) younger than TTL + 1 day, per table
     (metric_log / asynchronous_metric_log: 8d; query_log / part_log: 15d).
     Rows older than the TTL surviving means the TTL is not binding.

🔴 A ZERO FROM A BROKEN CLIENT MUST NEVER READ AS A CLEAN STORE
---------------------------------------------------------------
Every reassuring answer this check can give is a ZERO — zero orphans, zero
forbidden tables, zero oversized tables. A zero is exactly what a mistyped table
name, a failed auth, a missing pod or a harness wired to nothing also produces.
So, following `deadman.py`:

  * Every non-evaluable condition gets its OWN state and none of them is `ok`:
    `not-configured`, `unreachable`, `query-failed`, `exec-failed`, `no-data`.
    There is no code path on which an error produces a healthy verdict.
  * `ok` carries POSITIVE CONTROLS. The verdict refuses to be `ok` unless the
    read demonstrably CAN observe the things it is counting:
      - the listing came back non-empty, AND
      - the SENTINEL table `activity.events` is in it (proves we read the right
        server, not an empty/other one), AND
      - suffix matching found >0 tables ending `_log` (proves the exact
        code path used for the `_0` orphan test can match at all), AND
      - the size comparison found >0 tables over PROBE_BYTES (1 MiB) (proves
        the byte comparison used for the 250 MiB test can fire at all), AND
      - at least one TTL target table was actually measured.
    Each control's OBSERVED NUMBER is reported next to the zero it validates,
    so the output reads "9 tables over 1 MiB / 0 over 250 MiB" rather than a
    bare "0". A zero alone is never presented as evidence.

READ-ONLY. This module issues SELECTs and one `du`. No TTL change, no TRUNCATE,
no DROP, no INSERT — there is no write path in this file.

CLI
---
    python3 ch_regrowth.py                  # live check, human table
    python3 ch_regrowth.py --json
    python3 ch_regrowth.py --reading FILE   # evaluate a captured/seeded reading
                                            #   (the control harness: seed a
                                            #   bad store and watch it go RED)
    python3 ch_regrowth.py --capture FILE   # live read -> save the raw reading

Exit codes: 0 = ok · 1 = ALARM · 2 = CANNOT TELL · 3 = WARN.
Every non-zero code is a systemd unit failure, which the existing
`OnFailure=notify-failure@%n.service` turns into a sticky critical toast. That
includes 2: "cannot tell" is louder than silence, on purpose.
Never raises.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

# --------------------------------------------------------------------------- #
# Thresholds. Each is sized against the measured baseline in the docstring.
# --------------------------------------------------------------------------- #
MIB = 1024 ** 2
GIB = 1024 ** 3

# du(store). Measured 528.5 MiB at +3d; projected steady state 550-700 MiB.
# 1 GiB is ~1.5x the top of that projection (leading indicator, not yet a
# problem); 2 GiB is ~3x it and cannot be reached by normal operation.
DU_WARN_BYTES = 1 * GIB
DU_ALARM_BYTES = 2 * GIB

# Largest live table measured 86.7 MiB (metric_log). 250 MiB is ~3x that and
# below anything that could plausibly be a healthy system log under the 7d TTL.
TABLE_ALARM_BYTES = 250 * MIB

# POSITIVE CONTROL for the size comparison: a threshold that MUST match. 7
# tables were over 1 MiB at baseline. If nothing clears this, the byte
# comparison is not observing anything and the 250 MiB zero means nothing.
TABLE_PROBE_BYTES = 1 * MIB

# The four tables A2 removed. Any of them existing again = config revert.
FORBIDDEN_TABLES = ("trace_log", "text_log", "latency_log",
                    "processors_profile_log")
FORBIDDEN_DATABASE = "system"

# Orphan suffix left behind when a TTL is applied to an existing *_log table.
ORPHAN_SUFFIX = "_0"
# POSITIVE CONTROL for suffix matching: 14 tables ended in `_log` at baseline.
PROBE_SUFFIX = "_log"

# POSITIVE CONTROL for "we read the right server". This table is the entire
# point of the store; if it is missing, the read is not trustworthy.
SENTINEL_TABLE = ("activity", "events")

# TTL + 1 day of slack (ClickHouse evaluates TTL during merges, not instantly).
TTL_MAX_AGE_DAYS = {
    "metric_log": 8,                  # 7d TTL
    "asynchronous_metric_log": 8,     # 7d TTL
    "query_log": 15,                  # 14d TTL
    "part_log": 15,                   # 14d TTL
}
TTL_DATABASE = "system"

STORE_PATH = "/var/lib/clickhouse/store"
DEFAULT_NAMESPACE = "activity"
DEFAULT_WORKLOAD = "deploy/clickhouse"
DEFAULT_CONTAINER = "clickhouse"
DEFAULT_TIMEOUT = 30.0
DEFAULT_STATUS_PATH = "~/.cache/ch-regrowth/status.json"

STATE_OK = "ok"
STATE_WARN = "warn"
STATE_ALARM = "alarm"
STATE_NO_DATA = "no-data"
STATE_UNREACHABLE = "unreachable"
STATE_QUERY_FAILED = "query-failed"
STATE_EXEC_FAILED = "exec-failed"
STATE_NOT_CONFIGURED = "not-configured"

# 🔴 Every state that is NOT a measurement of the store. None of these is `ok`
# and none of them exits 0.
UNKNOWN_STATES = frozenset((STATE_NO_DATA, STATE_UNREACHABLE,
                            STATE_QUERY_FAILED, STATE_EXEC_FAILED,
                            STATE_NOT_CONFIGURED))

EXIT_OK = 0
EXIT_ALARM = 1
EXIT_UNKNOWN = 2
EXIT_WARN = 3


# --------------------------------------------------------------------------- #
# Queries
# --------------------------------------------------------------------------- #
def listing_query() -> str:
    """Every table on the server, with rows + on-disk bytes.

    ONE query drives checks 2, 3 and 4 plus all three listing positive
    controls — deliberately, so a single successful read either validates all
    of them or fails all of them together. Separate per-check queries would let
    one silently return nothing while the others looked fine.

    `ifNull(...)` because views/system engines report NULL bytes; they are kept
    in the listing anyway so an orphan of ANY engine is still caught.
    """
    return (
        "SELECT database, name, engine, ifNull(total_rows, 0), "
        "ifNull(total_bytes, 0) "
        "FROM system.tables "
        "WHERE database NOT IN ('INFORMATION_SCHEMA', 'information_schema') "
        "ORDER BY ifNull(total_bytes, 0) DESC FORMAT TSV"
    )


def now_query() -> str:
    """Server clock. Ages are measured against the SERVER, never the host, so a
    skewed workbench clock cannot manufacture or hide a stale-TTL alarm."""
    return "SELECT toUnixTimestamp(now()) FORMAT TSV"


def ttl_query(present_tables) -> str:
    """min(event_time) per TTL target, restricted to targets actually present.

    Built from the listing rather than hardcoded: querying a table that does not
    exist errors the WHOLE union, which would turn a legitimately-absent table
    into `query-failed` for every other one.
    """
    parts = []
    for name in sorted(present_tables):
        parts.append(
            "SELECT '%s' AS t, count() AS c, "
            "toUnixTimestamp(min(event_time)) AS m FROM %s.%s"
            % (name, TTL_DATABASE, name)
        )
    return " UNION ALL ".join(parts) + " FORMAT TSV"


def du_argv(namespace: str = DEFAULT_NAMESPACE,
            workload: str = DEFAULT_WORKLOAD,
            container: str = DEFAULT_CONTAINER,
            path: str = STORE_PATH):
    """`du -sk`, NOT `du -sb`: the server image is alpine/busybox and busybox du
    has no `-b`. `-k` (KiB) is portable across busybox and coreutils. Verified
    against the live pod 2026-08-06."""
    return ["kubectl", "-n", namespace, "exec", workload, "-c", container,
            "--", "du", "-sk", path]


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #
def parse_listing(text: str):
    rows = []
    for line in text.splitlines():
        if not line.strip():
            continue
        f = line.split("\t")
        if len(f) < 5:
            raise ValueError("listing row has %d fields, want 5: %r"
                             % (len(f), line[:120]))
        rows.append({"database": f[0], "name": f[1], "engine": f[2],
                     "rows": int(f[3]), "bytes": int(f[4])})
    return rows


def parse_ttl(text: str):
    out = []
    for line in text.splitlines():
        if not line.strip():
            continue
        f = line.split("\t")
        if len(f) < 3:
            raise ValueError("ttl row has %d fields, want 3: %r"
                             % (len(f), line[:120]))
        out.append({"table": f[0], "rows": int(f[1]),
                    "min_event_ts": int(f[2])})
    return out


def parse_du_kib(text: str) -> int:
    """`du -sk` prints `<kib>\\t<path>`. Returns BYTES."""
    first = text.strip().split("\n")[0]
    field = first.split()[0]
    return int(field) * 1024


def human_bytes(n) -> str:
    if n is None:
        return "-"
    n = float(n)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if abs(n) < 1024.0 or unit == "TiB":
            return "%.1f %s" % (n, unit) if unit != "B" else "%d B" % n
        n /= 1024.0
    return "%.1f TiB" % n


# --------------------------------------------------------------------------- #
# Evaluation — PURE. Takes a reading, returns a verdict. No I/O, never raises.
# --------------------------------------------------------------------------- #
def evaluate(reading, now=None) -> dict:
    """Evaluate a reading dict:

        {"du_bytes": int, "tables": [...], "ttl": [...], "server_now": int}

    Returns a verdict dict. `now` overrides the clock for tests.
    """
    findings = []
    tables = list(reading.get("tables") or [])
    ttl_rows = list(reading.get("ttl") or [])
    du_bytes = reading.get("du_bytes")
    now = (now if now is not None
           else reading.get("server_now") or int(time.time()))

    # ---- POSITIVE CONTROLS -------------------------------------------------
    # Computed FIRST and reported alongside every zero they validate.
    tables_seen = len(tables)
    sentinel_present = any(t["database"] == SENTINEL_TABLE[0]
                           and t["name"] == SENTINEL_TABLE[1] for t in tables)
    probe_suffix = [qualify(t) for t in tables
                    if t["name"].endswith(PROBE_SUFFIX)]
    probe_size = [qualify(t) for t in tables
                  if t["bytes"] > TABLE_PROBE_BYTES]

    controls = {
        "tables_seen": tables_seen,
        "sentinel": "%s.%s" % SENTINEL_TABLE,
        "sentinel_present": sentinel_present,
        "probe_suffix_pattern": "*%s" % PROBE_SUFFIX,
        "probe_suffix_matches": len(probe_suffix),
        "probe_size_threshold": TABLE_PROBE_BYTES,
        "probe_size_matches": len(probe_size),
        "ttl_tables_measured": 0,   # filled in below
    }

    # ---- 1. store size -----------------------------------------------------
    if du_bytes is None:
        return _unknown(STATE_EXEC_FAILED,
                        "no du reading — store size was never measured",
                        controls=controls)
    if du_bytes >= DU_ALARM_BYTES:
        findings.append({
            "check": "store-size", "level": "alarm",
            "detail": "du(%s) = %s >= alarm threshold %s"
                      % (STORE_PATH, human_bytes(du_bytes),
                         human_bytes(DU_ALARM_BYTES)),
            "observed": du_bytes, "threshold": DU_ALARM_BYTES})
    elif du_bytes >= DU_WARN_BYTES:
        findings.append({
            "check": "store-size", "level": "warn",
            "detail": "du(%s) = %s >= warn threshold %s"
                      % (STORE_PATH, human_bytes(du_bytes),
                         human_bytes(DU_WARN_BYTES)),
            "observed": du_bytes, "threshold": DU_WARN_BYTES})

    # ---- listing-derived checks need a listing we can trust ----------------
    if tables_seen == 0:
        return _unknown(STATE_NO_DATA,
                        "table listing came back EMPTY — cannot distinguish a "
                        "clean store from a failed read",
                        controls=controls, du_bytes=du_bytes)
    if not sentinel_present:
        return _unknown(STATE_NO_DATA,
                        "sentinel table %s.%s absent from a %d-row listing — "
                        "this is not the activity store, or the read is wrong"
                        % (SENTINEL_TABLE[0], SENTINEL_TABLE[1], tables_seen),
                        controls=controls, du_bytes=du_bytes)
    if not probe_suffix:
        return _unknown(STATE_NO_DATA,
                        "positive control FAILED: 0 tables end in '%s' in a "
                        "%d-row listing, so the suffix match used for the '%s' "
                        "orphan test is not observing anything — its zero is "
                        "meaningless"
                        % (PROBE_SUFFIX, tables_seen, ORPHAN_SUFFIX),
                        controls=controls, du_bytes=du_bytes)
    if not probe_size:
        return _unknown(STATE_NO_DATA,
                        "positive control FAILED: 0 tables exceed %s in a "
                        "%d-row listing, so the byte comparison used for the "
                        "%s test is not observing anything — its zero is "
                        "meaningless"
                        % (human_bytes(TABLE_PROBE_BYTES), tables_seen,
                           human_bytes(TABLE_ALARM_BYTES)),
                        controls=controls, du_bytes=du_bytes)

    # ---- 2. `%_0` orphan tables -------------------------------------------
    orphans = [qualify(t) for t in tables
               if t["name"].endswith(ORPHAN_SUFFIX)]
    if orphans:
        findings.append({
            "check": "orphan-tables", "level": "alarm",
            "detail": "%d table(s) named '*%s' exist: %s — applying a TTL "
                      "RENAMES the old table aside and it then keeps its rows "
                      "with NO TTL forever"
                      % (len(orphans), ORPHAN_SUFFIX, ", ".join(orphans)),
            "observed": orphans})

    # ---- 3. forbidden log tables resurrected -------------------------------
    forbidden = [qualify(t) for t in tables
                 if t["database"] == FORBIDDEN_DATABASE
                 and t["name"] in FORBIDDEN_TABLES]
    if forbidden:
        findings.append({
            "check": "forbidden-tables", "level": "alarm",
            "detail": "%d removed log table(s) exist again: %s — likeliest "
                      "cause is a ClickHouse config revert, which IS the "
                      "112 GB mechanism"
                      % (len(forbidden), ", ".join(forbidden)),
            "observed": forbidden})

    # ---- 4. oversized single table -----------------------------------------
    oversized = [{"table": qualify(t), "bytes": t["bytes"], "rows": t["rows"]}
                 for t in tables if t["bytes"] > TABLE_ALARM_BYTES]
    if oversized:
        findings.append({
            "check": "table-size", "level": "alarm",
            "detail": "%d table(s) exceed %s: %s"
                      % (len(oversized), human_bytes(TABLE_ALARM_BYTES),
                         ", ".join("%s (%s)" % (o["table"],
                                                human_bytes(o["bytes"]))
                                   for o in oversized)),
            "observed": oversized})

    # ---- 5. TTL effectiveness ----------------------------------------------
    # The ONLY check that tests the MECHANISM. If a row older than the TTL is
    # still there, the TTL is not binding and the store WILL regrow.
    present_targets = sorted(
        t["name"] for t in tables
        if t["database"] == TTL_DATABASE and t["name"] in TTL_MAX_AGE_DAYS)
    ttl_report = []
    measured = 0
    by_name = {r["table"]: r for r in ttl_rows}
    for name in present_targets:
        row = by_name.get(name)
        if row is None:
            ttl_report.append({"table": name, "rows": None,
                               "age_days": None,
                               "max_age_days": TTL_MAX_AGE_DAYS[name],
                               "stale": False, "reason": "not-queried"})
            continue
        if row["rows"] == 0:
            # An empty table has no oldest row; min() would report the epoch.
            # Not evidence either way — and NOT counted as measured.
            ttl_report.append({"table": name, "rows": 0, "age_days": None,
                               "max_age_days": TTL_MAX_AGE_DAYS[name],
                               "stale": False, "reason": "empty"})
            continue
        age_days = (now - row["min_event_ts"]) / 86400.0
        stale = age_days > TTL_MAX_AGE_DAYS[name]
        measured += 1
        ttl_report.append({"table": name, "rows": row["rows"],
                           "age_days": round(age_days, 2),
                           "max_age_days": TTL_MAX_AGE_DAYS[name],
                           "stale": stale, "reason": "measured"})
        if stale:
            findings.append({
                "check": "ttl-effectiveness", "level": "alarm",
                "detail": "%s.%s oldest row is %.2f days old, past its %d-day "
                          "bound — the TTL is NOT binding"
                          % (TTL_DATABASE, name, age_days,
                             TTL_MAX_AGE_DAYS[name]),
                "observed": {"table": name, "age_days": round(age_days, 2),
                             "max_age_days": TTL_MAX_AGE_DAYS[name],
                             "rows": row["rows"]}})

    controls["ttl_tables_measured"] = measured
    if measured == 0:
        return _unknown(STATE_NO_DATA,
                        "positive control FAILED: 0 of %d TTL target table(s) "
                        "yielded a measurable oldest row, so the TTL check "
                        "proved nothing — a clean verdict here would be a "
                        "harness reporting on itself"
                        % len(present_targets),
                        controls=controls, du_bytes=du_bytes,
                        tables_total_bytes=sum(t["bytes"] for t in tables),
                        ttl=ttl_report)

    alarms = sum(1 for f in findings if f["level"] == "alarm")
    warns = sum(1 for f in findings if f["level"] == "warn")
    state = STATE_ALARM if alarms else (STATE_WARN if warns else STATE_OK)

    largest = max(tables, key=lambda t: t["bytes"])
    return {
        "state": state,
        "alarms": alarms,
        "warns": warns,
        "findings": findings,
        "controls": controls,
        "measurements": {
            "du_bytes": du_bytes,
            "du_human": human_bytes(du_bytes),
            "tables_total_bytes": sum(t["bytes"] for t in tables),
            "tables_seen": tables_seen,
            "non_empty_tables": sum(1 for t in tables if t["bytes"] > 0),
            "largest_table": qualify(largest),
            "largest_table_bytes": largest["bytes"],
            "orphan_count": len(orphans),
            "forbidden_count": len(forbidden),
            "oversized_count": len(oversized),
            "ttl": ttl_report,
        },
        "detail": _summarize(state, findings, du_bytes, controls),
    }


def qualify(t) -> str:
    return "%s.%s" % (t["database"], t["name"])


def _summarize(state, findings, du_bytes, controls) -> str:
    if state == STATE_OK:
        # 🔴 Report the CONTROL NUMBER next to every zero it validates. A bare
        # "0 orphans" is indistinguishable from a check wired to nothing.
        return ("store %s; 0 orphan '*%s' tables (control: %d tables matched "
                "'*%s'); 0 forbidden log tables; 0 tables over %s (control: %d "
                "tables over %s); %d TTL target(s) measured, all within bound"
                % (human_bytes(du_bytes), ORPHAN_SUFFIX,
                   controls["probe_suffix_matches"], PROBE_SUFFIX,
                   human_bytes(TABLE_ALARM_BYTES),
                   controls["probe_size_matches"],
                   human_bytes(TABLE_PROBE_BYTES),
                   controls["ttl_tables_measured"]))
    return "; ".join(f["detail"] for f in findings)


def _unknown(state, detail, *, controls=None, du_bytes=None, **extra) -> dict:
    v = {
        "state": state,
        "alarms": 0,
        "warns": 0,
        "findings": [],
        "controls": controls or {},
        "measurements": {"du_bytes": du_bytes,
                         "du_human": human_bytes(du_bytes)},
        "detail": detail,
    }
    v["measurements"].update(extra)
    return v


# --------------------------------------------------------------------------- #
# Collection
# --------------------------------------------------------------------------- #
def http_query(url: str, user: str, password: str, query: str,
               timeout: float = DEFAULT_TIMEOUT) -> str:
    """POST a SELECT to ClickHouse over HTTP.

    Credentials go in the Authorization header, NEVER in argv — a password on a
    command line lands in the process list and shell history.
    """
    req = urllib.request.Request(url.rstrip("/") + "/",
                                 data=query.encode("utf-8"), method="POST")
    import base64
    token = base64.b64encode(("%s:%s" % (user, password)).encode()).decode()
    req.add_header("Authorization", "Basic " + token)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def run_du(argv, timeout: float = DEFAULT_TIMEOUT) -> str:
    p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    if p.returncode != 0:
        raise RuntimeError("exit %d: %s"
                           % (p.returncode,
                              (p.stderr or p.stdout).strip()[:200]))
    return p.stdout


def resolve_config(env=None) -> dict:
    env = os.environ if env is None else env
    return {
        "url": env.get("CH_REGROWTH_URL") or env.get("CLICKHOUSE_URL") or "",
        "user": env.get("CH_REGROWTH_USER")
        or env.get("CLICKHOUSE_USER") or "default",
        "password": env.get("CH_REGROWTH_PASSWORD")
        or env.get("CLICKHOUSE_PASSWORD") or "",
        "namespace": env.get("CH_REGROWTH_NAMESPACE", DEFAULT_NAMESPACE),
        "workload": env.get("CH_REGROWTH_WORKLOAD", DEFAULT_WORKLOAD),
        "container": env.get("CH_REGROWTH_CONTAINER", DEFAULT_CONTAINER),
    }


def collect(env=None, timeout: float = DEFAULT_TIMEOUT,
            query=None, du=None) -> dict:
    """Take a live reading. Returns {"reading": {...}} or {"error": (state, detail)}.

    `query(url, user, password, sql, timeout) -> str` and `du(argv, timeout)
    -> str` are injectable so EVERY failure branch is testable offline. Never
    raises.
    """
    cfg = resolve_config(env)
    query = query or http_query
    du = du or run_du

    if not cfg["url"] or not cfg["password"]:
        missing = []
        if not cfg["url"]:
            missing.append("CH_REGROWTH_URL")
        if not cfg["password"]:
            missing.append("CH_REGROWTH_PASSWORD")
        return {"error": (STATE_NOT_CONFIGURED,
                          "missing %s — the check cannot run, and MUST NOT "
                          "report a clean store" % "/".join(missing))}

    def _q(sql):
        return query(cfg["url"], cfg["user"], cfg["password"], sql, timeout)

    # --- server clock (also the liveness probe) ---
    try:
        now_text = _q(now_query())
    except Exception as e:  # noqa: BLE001 — an unreachable server is NOT clean
        return {"error": (STATE_UNREACHABLE,
                          "ClickHouse unreachable: %s" % str(e)[:200])}
    try:
        server_now = int(now_text.strip().split("\n")[0])
    except Exception as e:  # noqa: BLE001
        return {"error": (STATE_QUERY_FAILED,
                          "unparseable now() response: %s" % str(e)[:200])}

    # --- table listing ---
    try:
        listing_text = _q(listing_query())
    except Exception as e:  # noqa: BLE001
        return {"error": (STATE_UNREACHABLE,
                          "listing query failed: %s" % str(e)[:200])}
    try:
        tables = parse_listing(listing_text)
    except Exception as e:  # noqa: BLE001
        return {"error": (STATE_QUERY_FAILED,
                          "unparseable listing: %s" % str(e)[:200])}

    # --- TTL ages, for the targets that actually exist ---
    present = sorted(t["name"] for t in tables
                     if t["database"] == TTL_DATABASE
                     and t["name"] in TTL_MAX_AGE_DAYS)
    ttl_rows = []
    if present:
        try:
            ttl_text = _q(ttl_query(present))
        except Exception as e:  # noqa: BLE001
            return {"error": (STATE_UNREACHABLE,
                              "ttl query failed: %s" % str(e)[:200])}
        try:
            ttl_rows = parse_ttl(ttl_text)
        except Exception as e:  # noqa: BLE001
            return {"error": (STATE_QUERY_FAILED,
                              "unparseable ttl response: %s" % str(e)[:200])}

    # --- du(store) via kubectl exec ---
    argv = du_argv(cfg["namespace"], cfg["workload"], cfg["container"])
    try:
        du_text = du(argv, timeout)
    except Exception as e:  # noqa: BLE001 — a failed exec is NOT a small store
        return {"error": (STATE_EXEC_FAILED,
                          "kubectl exec du failed: %s" % str(e)[:200])}
    try:
        du_bytes = parse_du_kib(du_text)
    except Exception as e:  # noqa: BLE001
        return {"error": (STATE_EXEC_FAILED,
                          "unparseable du output: %s" % str(e)[:200])}

    return {"reading": {"du_bytes": du_bytes, "tables": tables,
                        "ttl": ttl_rows, "server_now": server_now,
                        "captured_at": int(time.time())}}


def check(env=None, now=None, timeout: float = DEFAULT_TIMEOUT,
          query=None, du=None) -> dict:
    """Full live check. Returns a verdict; NEVER raises and NEVER returns `ok`
    on any error path."""
    got = collect(env=env, timeout=timeout, query=query, du=du)
    if "error" in got:
        state, detail = got["error"]
        return _unknown(state, detail)
    return evaluate(got["reading"], now=now)


# --------------------------------------------------------------------------- #
# Output
# --------------------------------------------------------------------------- #
def exit_code(verdict) -> int:
    state = verdict["state"]
    if state in UNKNOWN_STATES:
        return EXIT_UNKNOWN
    if state == STATE_ALARM:
        return EXIT_ALARM
    if state == STATE_WARN:
        return EXIT_WARN
    return EXIT_OK


def render_text(v: dict) -> str:
    m = v.get("measurements", {})
    c = v.get("controls", {})
    lines = [
        "ch-regrowth: %s   alarms=%d warns=%d"
        % (v["state"].upper(), v.get("alarms", 0), v.get("warns", 0)),
        "detail: %s" % v.get("detail", ""),
        "",
        "store du:        %s (warn %s / alarm %s)"
        % (m.get("du_human", "-"), human_bytes(DU_WARN_BYTES),
           human_bytes(DU_ALARM_BYTES)),
    ]
    if m.get("tables_total_bytes") is not None:
        lines.append("live table bytes: %s  (du sits ~40%% higher by design — "
                     "8-slot merge pool leaves inactive parts around)"
                     % human_bytes(m["tables_total_bytes"]))
    if m.get("tables_seen") is not None:
        lines.append("tables:          %s seen, %s non-empty, largest %s (%s)"
                     % (m.get("tables_seen"), m.get("non_empty_tables"),
                        m.get("largest_table"),
                        human_bytes(m.get("largest_table_bytes"))))
        lines.append("counts:          orphan '*%s'=%s  forbidden=%s  over %s=%s"
                     % (ORPHAN_SUFFIX, m.get("orphan_count"),
                        m.get("forbidden_count"),
                        human_bytes(TABLE_ALARM_BYTES),
                        m.get("oversized_count")))
    if c:
        lines.append("")
        lines.append("positive controls (a zero above is only meaningful if "
                     "these are non-zero):")
        lines.append("  listing rows            = %s" % c.get("tables_seen"))
        lines.append("  sentinel %-14s= %s"
                     % (c.get("sentinel", "?"), c.get("sentinel_present")))
        lines.append("  tables matching '%s'  = %s   (vs orphan '*%s' = %s)"
                     % (c.get("probe_suffix_pattern", "?"),
                        c.get("probe_suffix_matches"), ORPHAN_SUFFIX,
                        m.get("orphan_count")))
        lines.append("  tables over %-11s = %s   (vs over %s = %s)"
                     % (human_bytes(c.get("probe_size_threshold")),
                        c.get("probe_size_matches"),
                        human_bytes(TABLE_ALARM_BYTES),
                        m.get("oversized_count")))
        lines.append("  TTL tables measured     = %s"
                     % c.get("ttl_tables_measured"))
    if m.get("ttl"):
        lines.append("")
        lines.append("%-26s %12s %10s %10s  %s"
                     % ("ttl target", "rows", "oldest_d", "bound_d", "verdict"))
        for r in m["ttl"]:
            lines.append("%-26s %12s %10s %10s  %s"
                         % (r["table"],
                            "-" if r["rows"] is None else r["rows"],
                            "-" if r["age_days"] is None
                            else "%.2f" % r["age_days"],
                            r["max_age_days"],
                            "STALE" if r["stale"] else r.get("reason", "ok")))
    if v.get("findings"):
        lines.append("")
        for f in v["findings"]:
            lines.append("[%s] %s: %s"
                         % (f["level"].upper(), f["check"], f["detail"]))
    return "\n".join(lines)


def write_status(verdict, path: str = DEFAULT_STATUS_PATH) -> str:
    """Atomically write the verdict so it is discoverable after the fact."""
    p = os.path.expanduser(path)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    payload = dict(verdict)
    payload["checked_at"] = int(time.time())
    tmp = p + ".tmp"
    with open(tmp, "w") as f:
        json.dump(payload, f, indent=2, sort_keys=True)
    os.replace(tmp, p)
    return p


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="ClickHouse regrowth check for the activity store")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--reading", metavar="FILE",
                    help="evaluate this captured/seeded reading JSON instead "
                         "of querying the cluster (the control harness)")
    ap.add_argument("--capture", metavar="FILE",
                    help="write the raw live reading here (for seeding tests)")
    ap.add_argument("--now", type=int, help="override 'now' (unix seconds)")
    ap.add_argument("--status-file", default=DEFAULT_STATUS_PATH)
    ap.add_argument("--no-status-file", action="store_true")
    ap.add_argument("--print-queries", action="store_true")
    args = ap.parse_args(argv)

    if args.print_queries:
        sys.stdout.write(now_query() + "\n" + listing_query() + "\n"
                         + ttl_query(sorted(TTL_MAX_AGE_DAYS)) + "\n")
        return EXIT_OK

    if args.reading:
        try:
            with open(args.reading) as f:
                reading = json.load(f)
            verdict = evaluate(reading, now=args.now)
        except Exception as e:  # noqa: BLE001
            verdict = _unknown(STATE_QUERY_FAILED,
                               "unreadable reading file: %s" % str(e)[:200])
    else:
        got = collect()
        if "error" in got:
            state, detail = got["error"]
            verdict = _unknown(state, detail)
        else:
            if args.capture:
                with open(args.capture, "w") as f:
                    json.dump(got["reading"], f, indent=2)
            verdict = evaluate(got["reading"], now=args.now)

    if not args.no_status_file:
        try:
            write_status(verdict, args.status_file)
        except Exception as e:  # noqa: BLE001 — a status-file failure must not
            # mask the verdict, but must be visible.
            sys.stderr.write("ch-regrowth: could not write status file: %s\n"
                             % str(e)[:200])

    sys.stdout.write((json.dumps(verdict, indent=2, sort_keys=True)
                      if args.json else render_text(verdict)) + "\n")
    return exit_code(verdict)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:  # noqa: BLE001 — an unexpected crash is CANNOT TELL
        sys.stderr.write("ch-regrowth: %s\n" % e)
        sys.exit(EXIT_UNKNOWN)
