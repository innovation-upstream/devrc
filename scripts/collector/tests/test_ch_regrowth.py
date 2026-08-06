"""Tests for the ClickHouse regrowth check (scripts/collector/ch_regrowth.py).

ALL OFFLINE — every test builds a synthetic reading and calls the pure
`evaluate`, or injects fake `query`/`du` callables into `check`. No cluster is
contacted and nothing is written.

🔴 WHY THIS FILE IS SHAPED THE WAY IT IS
----------------------------------------
Every reassuring answer this check can give is a ZERO: zero orphan tables, zero
forbidden tables, zero oversized tables, zero stale TTL targets. A zero is
exactly what a harness wired to nothing also produces. So this file NEVER
asserts a zero on its own:

  * NEGATIVE CONTROLS (`test_negative_*`) feed a known-BAD store — oversized,
    a `*_0` orphan present, a resurrected `trace_log`, a stale oldest row — and
    assert the checker goes RED, with THAT check's own name in the finding, and
    with the right exit code. A harness that cannot go red proves nothing.
  * POSITIVE CONTROLS (`test_positive_control_*`) assert the counting code path
    produces a NON-ZERO on a fixture that must move it, using the SAME code
    path that reports the zero. The pair is asserted together in
    `test_control_pair_*` so the zero always arrives with its own evidence.
  * ERROR PATHS (`test_error_*`) assert that auth failure, an unreachable
    server, an unparseable response and a failed `kubectl exec` each produce a
    CANNOT-TELL state and a non-zero exit — never `ok`.

The baseline fixture uses the REAL numbers measured against the live store on
2026-08-06 (see the module docstring), so "clean" means clean against reality
rather than against a hand-picked shape.

Run: pytest scripts/collector/tests/test_ch_regrowth.py
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import ch_regrowth as R  # noqa: E402

MODULE = Path(__file__).resolve().parent.parent / "ch_regrowth.py"

# Measured on the live store 2026-08-06 (server clock 1786034311).
NOW = 1786034311
OLDEST = 1785727742          # ~3.55 days before NOW
DU_BASELINE = 541224 * 1024  # `du -sk` -> 528.5 MiB

MEASURED_NON_EMPTY = [
    ("system", "metric_log", "MergeTree", 306363, 86680883),
    ("system", "asynchronous_metric_log", "MergeTree", 70479746, 75617527),
    ("activity", "events", "MergeTree", 526982, 34284751),
    ("system", "query_log", "MergeTree", 41186, 6789151),
    ("system", "part_log", "MergeTree", 48286, 4094133),
    ("system", "error_log", "MergeTree", 3100503, 1845104),
    ("system", "query_metric_log", "MergeTree", 1389, 600088),
]

# Empty-but-existing `*_log` tables the live server also lists. They matter:
# they are what makes the `*_log` suffix positive control non-trivial.
MEASURED_EMPTY_LOGS = [
    "asynchronous_insert_log", "backup_log", "blob_storage_log", "crash_log",
    "opentelemetry_span_log", "query_thread_log", "query_views_log",
    "s3queue_log",
]


def tbl(database, name, engine="MergeTree", rows=0, size=0):
    return {"database": database, "name": name, "engine": engine,
            "rows": rows, "bytes": size}


def baseline_tables():
    rows = [tbl(d, n, e, r, b) for d, n, e, r, b in MEASURED_NON_EMPTY]
    rows += [tbl("system", n) for n in MEASURED_EMPTY_LOGS]
    # A handful of non-log system views, to make the listing realistic.
    rows += [tbl("system", n, "SystemNumbers") for n in
             ("numbers", "one", "tables", "columns", "parts")]
    return rows


def baseline_ttl(oldest=OLDEST):
    return [
        {"table": "metric_log", "rows": 306363, "min_event_ts": oldest},
        {"table": "asynchronous_metric_log", "rows": 70479746,
         "min_event_ts": oldest + 1},
        {"table": "query_log", "rows": 41186, "min_event_ts": oldest + 5},
        {"table": "part_log", "rows": 48286, "min_event_ts": oldest + 2},
    ]


def baseline_reading(**over):
    r = {"du_bytes": DU_BASELINE, "tables": baseline_tables(),
         "ttl": baseline_ttl(), "server_now": NOW}
    r.update(over)
    return r


def checks_named(verdict, name):
    return [f for f in verdict["findings"] if f["check"] == name]


# --------------------------------------------------------------------------- #
# The baseline is clean — and this is the ONLY test allowed to assert that.
# Everything else exists to prove this green is not vacuous.
# --------------------------------------------------------------------------- #
def test_measured_baseline_is_ok():
    v = R.evaluate(baseline_reading(), now=NOW)
    assert v["state"] == R.STATE_OK, v["detail"]
    assert v["alarms"] == 0
    assert v["warns"] == 0
    assert R.exit_code(v) == R.EXIT_OK


def test_baseline_reports_the_numbers_it_read_not_just_a_verdict():
    v = R.evaluate(baseline_reading(), now=NOW)
    m = v["measurements"]
    assert m["du_bytes"] == DU_BASELINE
    assert m["du_human"] == "528.5 MiB"
    assert m["tables_seen"] == len(baseline_tables())
    assert m["largest_table"] == "system.metric_log"
    assert m["largest_table_bytes"] == 86680883
    assert m["non_empty_tables"] == len(MEASURED_NON_EMPTY)
    # The rendered text must carry the measurements too, not only a word.
    text = R.render_text(v)
    assert "528.5 MiB" in text
    assert "system.metric_log" in text


# --------------------------------------------------------------------------- #
# 1. store size
# --------------------------------------------------------------------------- #
def test_negative_control_store_over_2gib_alarms():
    v = R.evaluate(baseline_reading(du_bytes=3 * R.GIB), now=NOW)
    assert v["state"] == R.STATE_ALARM
    f = checks_named(v, "store-size")
    assert len(f) == 1 and f[0]["level"] == "alarm"
    assert f[0]["observed"] == 3 * R.GIB
    assert R.exit_code(v) == R.EXIT_ALARM


def test_negative_control_store_over_1gib_warns():
    v = R.evaluate(baseline_reading(du_bytes=int(1.5 * R.GIB)), now=NOW)
    assert v["state"] == R.STATE_WARN
    assert checks_named(v, "store-size")[0]["level"] == "warn"
    # A WARN is still a non-zero exit, so the systemd OnFailure toast fires.
    assert R.exit_code(v) == R.EXIT_WARN != 0


@pytest.mark.parametrize("du,expect", [
    (R.DU_WARN_BYTES - 1, R.STATE_OK),
    (R.DU_WARN_BYTES, R.STATE_WARN),
    (R.DU_ALARM_BYTES - 1, R.STATE_WARN),
    (R.DU_ALARM_BYTES, R.STATE_ALARM),
])
def test_store_size_thresholds_at_both_boundaries(du, expect):
    """Measured at each boundary AND one byte below it — a single midpoint
    measurement would not distinguish `>` from `>=`."""
    assert R.evaluate(baseline_reading(du_bytes=du), now=NOW)["state"] == expect


# --------------------------------------------------------------------------- #
# 2. `*_0` orphan tables — the silent regrowth vector
# --------------------------------------------------------------------------- #
def test_negative_control_orphan_table_alarms():
    tables = baseline_tables() + [
        tbl("system", "metric_log_0", rows=999_999, size=40 * R.MIB)]
    v = R.evaluate(baseline_reading(tables=tables), now=NOW)
    assert v["state"] == R.STATE_ALARM
    f = checks_named(v, "orphan-tables")
    assert len(f) == 1
    assert "system.metric_log_0" in f[0]["observed"]
    assert v["measurements"]["orphan_count"] == 1
    assert R.exit_code(v) == R.EXIT_ALARM


def test_orphan_alarms_even_when_empty_and_small():
    """The hazard is the table EXISTING with no TTL, not its current size — it
    grows silently forever after."""
    tables = baseline_tables() + [tbl("system", "trace_log_0")]
    v = R.evaluate(baseline_reading(tables=tables), now=NOW)
    assert v["state"] == R.STATE_ALARM
    assert v["measurements"]["orphan_count"] == 1


def test_control_pair_orphan_count_moves_off_zero():
    """🔴 THE PAIR. Same code path, same fixture: 0 on the clean store, 2 with
    orphans seeded. A zero from a checker that could only ever say zero would
    fail the second half of this."""
    clean = R.evaluate(baseline_reading(), now=NOW)
    seeded = R.evaluate(baseline_reading(
        tables=baseline_tables() + [tbl("system", "metric_log_0"),
                                    tbl("system", "query_log_0")]), now=NOW)
    assert clean["measurements"]["orphan_count"] == 0
    assert seeded["measurements"]["orphan_count"] == 2
    assert clean["state"] == R.STATE_OK
    assert seeded["state"] == R.STATE_ALARM


# --------------------------------------------------------------------------- #
# 3. resurrected forbidden log tables — the actual 112 GB mechanism
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("name", list(R.FORBIDDEN_TABLES))
def test_negative_control_each_forbidden_table_alarms(name):
    tables = baseline_tables() + [tbl("system", name, rows=1)]
    v = R.evaluate(baseline_reading(tables=tables), now=NOW)
    assert v["state"] == R.STATE_ALARM, name
    f = checks_named(v, "forbidden-tables")
    assert len(f) == 1 and "system.%s" % name in f[0]["observed"]
    assert R.exit_code(v) == R.EXIT_ALARM


def test_forbidden_check_is_scoped_to_the_system_database():
    """A user table that happens to be called `text_log` in another database is
    not the config revert this check is looking for."""
    tables = baseline_tables() + [tbl("activity", "text_log", rows=5)]
    v = R.evaluate(baseline_reading(tables=tables), now=NOW)
    assert v["measurements"]["forbidden_count"] == 0
    assert v["state"] == R.STATE_OK


def test_control_pair_forbidden_count_moves_off_zero():
    clean = R.evaluate(baseline_reading(), now=NOW)
    seeded = R.evaluate(baseline_reading(
        tables=baseline_tables() + [tbl("system", "trace_log", rows=3_770_000_000,
                                        size=100 * R.GIB)]), now=NOW)
    assert clean["measurements"]["forbidden_count"] == 0
    assert seeded["measurements"]["forbidden_count"] == 1


# --------------------------------------------------------------------------- #
# 4. oversized single table
# --------------------------------------------------------------------------- #
def test_negative_control_table_over_250mib_alarms():
    tables = baseline_tables() + [
        tbl("system", "metric_log2", rows=1, size=300 * R.MIB)]
    v = R.evaluate(baseline_reading(tables=tables), now=NOW)
    assert v["state"] == R.STATE_ALARM
    f = checks_named(v, "table-size")
    assert f[0]["observed"][0]["table"] == "system.metric_log2"
    assert R.exit_code(v) == R.EXIT_ALARM


@pytest.mark.parametrize("size,expect_count", [
    (R.TABLE_ALARM_BYTES - 1, 0),
    (R.TABLE_ALARM_BYTES, 0),
    (R.TABLE_ALARM_BYTES + 1, 1),
])
def test_table_size_threshold_at_the_boundary(size, expect_count):
    tables = baseline_tables() + [tbl("system", "big_log", rows=1, size=size)]
    v = R.evaluate(baseline_reading(tables=tables), now=NOW)
    assert v["measurements"]["oversized_count"] == expect_count


def test_control_pair_oversize_probe_vs_alarm_count():
    """🔴 THE PAIR the preliminary manual run used: validate the >250 MiB query
    that returns nothing by running the SAME comparison at >1 MiB, which must
    return rows. Report both numbers, never the zero alone."""
    v = R.evaluate(baseline_reading(), now=NOW)
    # 6 of the 7 non-empty tables clear 1 MiB (query_metric_log is 586 KiB).
    expected = sum(1 for _, _, _, _, b in MEASURED_NON_EMPTY
                   if b > R.TABLE_PROBE_BYTES)
    assert expected == 6
    assert v["controls"]["probe_size_matches"] == expected > 0
    assert v["measurements"]["oversized_count"] == 0
    assert "%d" % v["controls"]["probe_size_matches"] in R.render_text(v)


# --------------------------------------------------------------------------- #
# 5. TTL effectiveness — the only check that tests the MECHANISM
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("table,bound", sorted(R.TTL_MAX_AGE_DAYS.items()))
def test_negative_control_stale_oldest_row_alarms(table, bound):
    ttl = baseline_ttl()
    for row in ttl:
        if row["table"] == table:
            row["min_event_ts"] = NOW - int((bound + 2) * 86400)
    v = R.evaluate(baseline_reading(ttl=ttl), now=NOW)
    assert v["state"] == R.STATE_ALARM, (table, v["detail"])
    f = checks_named(v, "ttl-effectiveness")
    assert len(f) == 1
    assert f[0]["observed"]["table"] == table
    assert f[0]["observed"]["age_days"] > bound
    assert R.exit_code(v) == R.EXIT_ALARM


@pytest.mark.parametrize("table,bound", sorted(R.TTL_MAX_AGE_DAYS.items()))
def test_ttl_bound_is_per_table_and_measured_at_both_sides(table, bound):
    """A single midpoint would not catch a table wired to the wrong bound: at
    7.9 days `query_log` (15d) must be fine while `metric_log` (8d) must be
    fine too, but at 8.1 days they must DIVERGE."""
    for age_days, expect_stale in ((bound - 0.1, False), (bound + 0.1, True)):
        ttl = baseline_ttl()
        for row in ttl:
            if row["table"] == table:
                row["min_event_ts"] = NOW - int(age_days * 86400)
        v = R.evaluate(baseline_reading(ttl=ttl), now=NOW)
        stale = [r for r in v["measurements"]["ttl"]
                 if r["table"] == table][0]["stale"]
        assert stale is expect_stale, (table, age_days)


def test_the_8d_and_15d_bounds_actually_differ():
    """Pins that the two TTL tiers are not collapsed to one number: at 10 days
    old, the 7d-TTL tables are STALE and the 14d-TTL tables are not."""
    ten_days = NOW - 10 * 86400
    ttl = [dict(r, min_event_ts=ten_days) for r in baseline_ttl()]
    v = R.evaluate(baseline_reading(ttl=ttl), now=NOW)
    stale = {r["table"] for r in v["measurements"]["ttl"] if r["stale"]}
    assert stale == {"metric_log", "asynchronous_metric_log"}
    assert v["state"] == R.STATE_ALARM
    assert len(checks_named(v, "ttl-effectiveness")) == 2


def test_empty_ttl_target_is_not_evidence_and_is_not_stale():
    """min() over an empty table reports the epoch. That must not read as a
    62-year-old row (a false ALARM) — nor be counted as a measurement."""
    ttl = baseline_ttl()
    ttl[0].update(rows=0, min_event_ts=0)
    v = R.evaluate(baseline_reading(ttl=ttl), now=NOW)
    row = [r for r in v["measurements"]["ttl"] if r["table"] == "metric_log"][0]
    assert row["stale"] is False
    assert row["reason"] == "empty"
    assert v["controls"]["ttl_tables_measured"] == 3
    assert v["state"] == R.STATE_OK


def test_control_pair_ttl_measured_count_moves():
    clean = R.evaluate(baseline_reading(), now=NOW)
    none_measured = R.evaluate(
        baseline_reading(ttl=[dict(r, rows=0) for r in baseline_ttl()]),
        now=NOW)
    assert clean["controls"]["ttl_tables_measured"] == 4
    # 🔴 Zero measurable TTL targets is CANNOT TELL, never a clean store.
    assert none_measured["controls"]["ttl_tables_measured"] == 0
    assert none_measured["state"] == R.STATE_NO_DATA
    assert R.exit_code(none_measured) == R.EXIT_UNKNOWN


# --------------------------------------------------------------------------- #
# POSITIVE CONTROLS — a zero is never trusted without one
# --------------------------------------------------------------------------- #
def test_positive_control_suffix_matching_observes_something():
    v = R.evaluate(baseline_reading(), now=NOW)
    # 14 `*_log` tables on the live server: 6 non-empty + 8 empty.
    expected = len(MEASURED_EMPTY_LOGS) + sum(
        1 for _, n, _, _, _ in MEASURED_NON_EMPTY if n.endswith("_log"))
    assert v["controls"]["probe_suffix_matches"] == expected > 0
    assert v["measurements"]["orphan_count"] == 0


def test_a_listing_that_cannot_match_the_suffix_is_no_data_not_ok():
    """If NOTHING in the listing ends in `_log`, the suffix comparison is not
    observing anything, so `0 orphans` is meaningless. That must be CANNOT
    TELL — this is the 'harness wired to nothing' case."""
    tables = [tbl("activity", "events", rows=1, size=34 * R.MIB),
              tbl("activity", "other", rows=1, size=2 * R.MIB)]
    v = R.evaluate(baseline_reading(tables=tables), now=NOW)
    assert v["state"] == R.STATE_NO_DATA
    # 🔴 Pin THIS guard's own message. Every later gate (size, sentinel, TTL)
    # also returns no-data on this fixture, so asserting the STATE alone leaves
    # the test green with the suffix control deleted — measured, it survived a
    # mutation sweep until this line was added.
    assert "suffix match" in v["detail"], v["detail"]
    assert "positive control FAILED" in v["detail"]
    assert R.exit_code(v) == R.EXIT_UNKNOWN


def test_a_listing_where_nothing_clears_1mib_is_no_data_not_ok():
    tables = [tbl("activity", "events", rows=1, size=1024),
              tbl("system", "metric_log", rows=1, size=512)]
    v = R.evaluate(baseline_reading(tables=tables), now=NOW)
    assert v["state"] == R.STATE_NO_DATA
    assert "positive control FAILED" in v["detail"]


def test_empty_listing_is_no_data_not_a_clean_store():
    v = R.evaluate(baseline_reading(tables=[]), now=NOW)
    assert v["state"] == R.STATE_NO_DATA
    assert v["state"] != R.STATE_OK
    # Same reason as the suffix test above: the sentinel gate would also catch
    # an empty listing, so pin the empty-listing guard's own wording.
    assert "came back EMPTY" in v["detail"], v["detail"]
    assert R.exit_code(v) == R.EXIT_UNKNOWN


def test_missing_sentinel_table_is_no_data():
    """Reading a real-but-WRONG ClickHouse would produce a plausible listing
    with no `activity.events`. That is not a clean activity store."""
    tables = [t for t in baseline_tables()
              if not (t["database"] == "activity" and t["name"] == "events")]
    v = R.evaluate(baseline_reading(tables=tables), now=NOW)
    assert v["state"] == R.STATE_NO_DATA
    assert "sentinel" in v["detail"]


def test_missing_du_reading_is_exec_failed_not_ok():
    v = R.evaluate(baseline_reading(du_bytes=None), now=NOW)
    assert v["state"] == R.STATE_EXEC_FAILED
    assert R.exit_code(v) == R.EXIT_UNKNOWN


# --------------------------------------------------------------------------- #
# ERROR PATHS — none may report a healthy store
# --------------------------------------------------------------------------- #
def fake_query_factory(responses, fail_on=None, exc=None):
    """`responses` maps a substring of the SQL -> reply text."""
    def q(url, user, password, sql, timeout):
        if fail_on and fail_on in sql:
            raise (exc or RuntimeError("boom"))
        for needle, reply in responses.items():
            if needle in sql:
                return reply
        raise AssertionError("unexpected query: %s" % sql[:80])
    return q


def listing_tsv(rows):
    return "\n".join("\t".join(str(x) for x in
                               (r["database"], r["name"], r["engine"],
                                r["rows"], r["bytes"])) for r in rows) + "\n"


def ttl_tsv(rows):
    return "\n".join("\t".join(str(x) for x in
                               (r["table"], r["rows"], r["min_event_ts"]))
                     for r in rows) + "\n"


def good_responses():
    return {"now()": "%d\n" % NOW,
            "system.tables": listing_tsv(baseline_tables()),
            "UNION ALL": ttl_tsv(baseline_ttl())}


GOOD_ENV = {"CH_REGROWTH_URL": "http://ch.invalid:8123",
            "CH_REGROWTH_PASSWORD": "pw"}


def ok_du(argv, timeout):
    return "%d\t/var/lib/clickhouse/store\n" % (DU_BASELINE // 1024)


def test_injected_happy_path_is_ok():
    """The harness's OWN negative control: prove this injection path CAN reach
    `ok`, so the failure tests below are failing for their stated reason and
    not because the fake wiring is broken."""
    v = R.check(env=GOOD_ENV, now=NOW,
                query=fake_query_factory(good_responses()), du=ok_du)
    assert v["state"] == R.STATE_OK, v["detail"]
    assert R.exit_code(v) == R.EXIT_OK


@pytest.mark.parametrize("env,missing", [
    ({}, "CH_REGROWTH_URL"),
    ({"CH_REGROWTH_URL": "http://x"}, "CH_REGROWTH_PASSWORD"),
    ({"CH_REGROWTH_PASSWORD": "pw"}, "CH_REGROWTH_URL"),
])
def test_error_not_configured_is_never_ok(env, missing):
    v = R.check(env=env, now=NOW,
                query=fake_query_factory(good_responses()), du=ok_du)
    assert v["state"] == R.STATE_NOT_CONFIGURED
    assert missing in v["detail"]
    assert R.exit_code(v) == R.EXIT_UNKNOWN


def test_error_auth_failure_is_never_ok():
    """A 401 from a wrong/rotated password must be LOUD, not an empty result
    set that reads as a clean store."""
    import urllib.error
    err = urllib.error.HTTPError("http://ch.invalid:8123/", 401,
                                 "Unauthorized", {}, None)
    v = R.check(env=GOOD_ENV, now=NOW,
                query=fake_query_factory(good_responses(), fail_on="now()",
                                         exc=err),
                du=ok_du)
    assert v["state"] == R.STATE_UNREACHABLE
    assert v["state"] not in (R.STATE_OK, R.STATE_WARN)
    assert "401" in v["detail"] or "Unauthorized" in v["detail"]
    assert R.exit_code(v) == R.EXIT_UNKNOWN


@pytest.mark.parametrize("fail_on", ["now()", "system.tables", "UNION ALL"])
def test_error_any_failing_query_is_unreachable_not_ok(fail_on):
    v = R.check(env=GOOD_ENV, now=NOW,
                query=fake_query_factory(good_responses(), fail_on=fail_on),
                du=ok_du)
    assert v["state"] == R.STATE_UNREACHABLE
    assert R.exit_code(v) == R.EXIT_UNKNOWN


@pytest.mark.parametrize("bad", [
    {"now()": "not-a-number\n"},
    {"system.tables": "only\ttwo\n"},
    {"UNION ALL": "metric_log\tnope\n"},
])
def test_error_unparseable_response_is_query_failed_not_ok(bad):
    responses = good_responses()
    responses.update(bad)
    v = R.check(env=GOOD_ENV, now=NOW,
                query=fake_query_factory(responses), du=ok_du)
    assert v["state"] == R.STATE_QUERY_FAILED
    assert R.exit_code(v) == R.EXIT_UNKNOWN


def test_error_empty_response_body_is_never_ok():
    """🔴 The exact 'zero from a broken client' shape: the server answers, but
    with nothing. An empty listing must not read as a clean store."""
    responses = good_responses()
    responses["system.tables"] = ""
    v = R.check(env=GOOD_ENV, now=NOW,
                query=fake_query_factory(responses), du=ok_du)
    assert v["state"] == R.STATE_NO_DATA
    assert R.exit_code(v) == R.EXIT_UNKNOWN


def test_error_kubectl_exec_failure_is_never_ok():
    """A missing pod / bad kubeconfig makes `du` fail. That must NOT be read as
    a small store."""
    def bad_du(argv, timeout):
        raise RuntimeError("exit 1: Error from server (NotFound): "
                           "deployments.apps \"clickhouse\" not found")
    v = R.check(env=GOOD_ENV, now=NOW,
                query=fake_query_factory(good_responses()), du=bad_du)
    assert v["state"] == R.STATE_EXEC_FAILED
    assert "NotFound" in v["detail"]
    assert R.exit_code(v) == R.EXIT_UNKNOWN


def test_error_unparseable_du_is_exec_failed():
    def weird_du(argv, timeout):
        return "du: /var/lib/clickhouse/store: Permission denied\n"
    v = R.check(env=GOOD_ENV, now=NOW,
                query=fake_query_factory(good_responses()), du=weird_du)
    assert v["state"] == R.STATE_EXEC_FAILED
    assert R.exit_code(v) == R.EXIT_UNKNOWN


def test_no_error_path_can_produce_a_healthy_state():
    """Belt and braces: sweep every unknown state and assert none of them is
    `ok`/`warn` and all of them exit non-zero."""
    for state in R.UNKNOWN_STATES:
        v = R._unknown(state, "synthetic")
        assert v["state"] not in (R.STATE_OK, R.STATE_WARN, R.STATE_ALARM)
        assert R.exit_code(v) == R.EXIT_UNKNOWN != 0
        assert v["alarms"] == 0


# --------------------------------------------------------------------------- #
# Parsing / query construction
# --------------------------------------------------------------------------- #
def test_du_is_parsed_as_kib_because_busybox_has_no_dash_b():
    assert R.parse_du_kib("541224\t/var/lib/clickhouse/store\n") == 541224 * 1024
    assert R.parse_du_kib("12 /x\n") == 12 * 1024
    assert R.du_argv()[-3:] == ["du", "-sk", R.STORE_PATH]


def test_ttl_query_only_names_tables_that_exist():
    """Querying an absent table errors the WHOLE union, which would turn one
    legitimately-absent table into query-failed for all four."""
    sql = R.ttl_query(["metric_log", "part_log"])
    assert "system.metric_log" in sql and "system.part_log" in sql
    assert "query_log" not in sql
    assert sql.count("UNION ALL") == 1


def test_listing_query_keeps_null_byte_tables():
    """Views report NULL bytes; they stay in the listing so an orphan of ANY
    engine is still caught."""
    sql = R.listing_query()
    assert "ifNull(total_bytes, 0)" in sql
    assert "engine" not in sql.split("FROM")[1]  # no engine filter in WHERE


def test_collect_never_puts_the_password_in_argv():
    """The credential goes in an HTTP header and the environment, never a
    command line."""
    seen = []

    def spy_du(argv, timeout):
        seen.append(argv)
        return ok_du(argv, timeout)

    R.check(env={"CH_REGROWTH_URL": "http://ch.invalid",
                 "CH_REGROWTH_PASSWORD": "s3cr3t-do-not-leak"},
            now=NOW, query=fake_query_factory(good_responses()), du=spy_du)
    assert seen, "du was never invoked"
    for argv in seen:
        assert not any("s3cr3t" in a for a in argv)


# --------------------------------------------------------------------------- #
# CLI — exercised as a real subprocess, including the exit code
# --------------------------------------------------------------------------- #
def run_cli(args, reading=None, tmp_path=None):
    argv = [sys.executable, str(MODULE), "--no-status-file"] + args
    if reading is not None:
        p = tmp_path / "reading.json"
        p.write_text(json.dumps(reading))
        argv += ["--reading", str(p)]
    return subprocess.run(argv, capture_output=True, text=True)


def test_cli_clean_reading_exits_zero(tmp_path):
    r = run_cli(["--json", "--now", str(NOW)], baseline_reading(), tmp_path)
    assert r.returncode == R.EXIT_OK, r.stdout + r.stderr
    assert json.loads(r.stdout)["state"] == R.STATE_OK


def test_cli_bad_reading_exits_one(tmp_path):
    """🔴 The negative control at the process boundary — the layer systemd
    actually reads."""
    bad = baseline_reading(du_bytes=5 * R.GIB,
                           tables=baseline_tables()
                           + [tbl("system", "trace_log", rows=3_770_000_000,
                                  size=110 * R.GIB)])
    r = run_cli(["--json", "--now", str(NOW)], bad, tmp_path)
    assert r.returncode == R.EXIT_ALARM
    v = json.loads(r.stdout)
    assert v["state"] == R.STATE_ALARM
    assert {f["check"] for f in v["findings"]} >= {"store-size",
                                                   "forbidden-tables",
                                                   "table-size"}


def test_cli_unreadable_reading_exits_two(tmp_path):
    p = tmp_path / "nope.json"
    r = subprocess.run([sys.executable, str(MODULE), "--no-status-file",
                        "--json", "--reading", str(p)],
                       capture_output=True, text=True)
    assert r.returncode == R.EXIT_UNKNOWN
    assert json.loads(r.stdout)["state"] in R.UNKNOWN_STATES


def test_cli_unconfigured_live_run_exits_two(tmp_path):
    """No CH_REGROWTH_URL in a scrubbed env: the live path must refuse, not
    report a clean store."""
    r = subprocess.run([sys.executable, str(MODULE), "--no-status-file",
                        "--json"],
                       capture_output=True, text=True,
                       env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path)})
    assert r.returncode == R.EXIT_UNKNOWN
    assert json.loads(r.stdout)["state"] == R.STATE_NOT_CONFIGURED


def test_cli_writes_a_discoverable_status_file(tmp_path):
    p = tmp_path / "sub" / "status.json"
    reading = tmp_path / "reading.json"
    reading.write_text(json.dumps(baseline_reading()))
    r = subprocess.run([sys.executable, str(MODULE), "--reading", str(reading),
                        "--now", str(NOW), "--status-file", str(p)],
                       capture_output=True, text=True)
    assert r.returncode == R.EXIT_OK, r.stdout + r.stderr
    v = json.loads(p.read_text())
    assert v["state"] == R.STATE_OK
    assert v["measurements"]["du_bytes"] == DU_BASELINE
    assert v["checked_at"] > 0


# --------------------------------------------------------------------------- #
# Deployment wiring — the check is worthless if it is not actually scheduled
# --------------------------------------------------------------------------- #
HOME_NIX = MODULE.parent.parent.parent / "nix" / "home.nix"


@pytest.fixture(scope="module")
def home_nix():
    assert HOME_NIX.is_file(), HOME_NIX
    return HOME_NIX.read_text()


def test_unit_and_timer_are_workbench_only(home_nix):
    """The laptop's unresolved nebula fault makes these queries stall, and a
    flaky check gets ignored. Both halves must be serverMode-gated."""
    for unit in ("systemd.user.services.ch-regrowth-check",
                 "systemd.user.timers.ch-regrowth-check"):
        assert unit in home_nix, unit
        line = [l for l in home_nix.splitlines() if unit in l][0]
        assert "lib.mkIf serverMode" in line, line


def test_timer_fires_monthly_on_the_11th_and_catches_up(home_nix):
    block = home_nix.split("systemd.user.timers.ch-regrowth-check")[1][:600]
    assert 'OnCalendar = "*-*-11 09:00:00"' in block
    # A missed run with the host off must fire on next boot — the growth this
    # watches for is slow and unattended.
    assert "Persistent = true" in block


def test_failure_is_surfaced_through_the_existing_notify_path(home_nix):
    """No new notification mechanism: the repo's OnFailure -> notify-failure@
    template (scripts/notify-failure.sh) is what makes an ALARM loud."""
    block = home_nix.split("systemd.user.services.ch-regrowth-check")[1][:2000]
    assert 'OnFailure = [ "notify-failure@%n.service" ]' in block
    # 🔴 SuccessExitStatus would swallow exit 2 (CANNOT TELL) and exit 3 (WARN),
    # turning "could not measure" into a silent success. It must not be set.
    assert "SuccessExitStatus" not in block


def test_unit_runs_the_committed_wrapper_with_its_deps_on_path(home_nix):
    block = home_nix.split("systemd.user.services.ch-regrowth-check")[1][:2000]
    assert "scripts/collector/run-regrowth-check.sh" in block
    for dep in ("pkgs.python312", "pkgs.kubectl", "pkgs.sops"):
        assert dep in block, dep
    # X-Restart-Triggers must cover BOTH halves, or a change to one ships
    # without re-running the unit.
    assert "../scripts/collector/ch_regrowth.py" in block
    assert "../scripts/collector/run-regrowth-check.sh" in block


def test_wrapper_fails_closed_when_the_age_key_is_missing(tmp_path):
    """🔴 The wrapper's own negative control. A sops decrypt it cannot do must
    exit 2 (CANNOT TELL), NOT 0 — the opposite of run-sync.sh's best-effort
    block, and the reason that difference is commented in both files."""
    wrapper = MODULE.parent / "run-regrowth-check.sh"
    bash = shutil.which("bash")
    assert bash, "bash must be on PATH — this test drives the REAL wrapper"
    # PATH is inherited on purpose: the age-key guard is the FIRST check in the
    # wrapper, so this exercises it regardless of whether sops/kubectl exist.
    r = subprocess.run([bash, str(wrapper)], capture_output=True, text=True,
                       env={"PATH": os.environ.get("PATH", ""),
                            "HOME": str(tmp_path),
                            "HOMELAB": str(tmp_path / "absent"),
                            "SOPS_AGE_KEY_FILE": str(tmp_path / "no.key")})
    assert r.returncode == R.EXIT_UNKNOWN, r.stdout + r.stderr
    assert "CANNOT TELL" in r.stderr
