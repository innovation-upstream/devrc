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
import urllib.error
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
    """Measured on BOTH sides of each table's own bound, and EXACTLY ON it.

    A single midpoint would not catch a table wired to the wrong bound; and
    without the exact-boundary point, `age_days > bound` and `age_days >= bound`
    are indistinguishable — a surviving mutant until this case was added.
    Exactly-at-bound must NOT be stale: the bound is already TTL + 3 days of
    merge slack, so the first second past it is the first real evidence."""
    for age_days, expect_stale in ((bound - 0.1, False),
                                   (bound, False),
                                   (bound + 0.1, True)):
        ttl = baseline_ttl()
        for row in ttl:
            if row["table"] == table:
                row["min_event_ts"] = NOW - int(age_days * 86400)
        v = R.evaluate(baseline_reading(ttl=ttl), now=NOW)
        stale = [r for r in v["measurements"]["ttl"]
                 if r["table"] == table][0]["stale"]
        assert stale is expect_stale, (table, age_days)


def test_the_two_ttl_tiers_actually_differ():
    """Pins that the two TTL tiers are not collapsed to one number: at 12 days
    old, the 7d-TTL tables (bound 10d) are STALE and the 14d-TTL tables (bound
    17d) are not."""
    assert sorted(set(R.TTL_MAX_AGE_DAYS.values())) == [10, 17]
    twelve_days = NOW - 12 * 86400
    ttl = [dict(r, min_event_ts=twelve_days) for r in baseline_ttl()]
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


def test_the_probe_size_control_needs_a_table_STRICTLY_over_the_threshold():
    """Measured ON the threshold and one byte above it. Without the exact
    boundary point `> TABLE_PROBE_BYTES` and `>=` are indistinguishable, and a
    control that counts a table sitting exactly ON its probe is weaker than it
    claims — this gate is what licenses the 250 MiB zero."""
    on_boundary = [tbl("activity", "events", rows=1, size=R.TABLE_PROBE_BYTES),
                   tbl("system", "metric_log", rows=1,
                       size=R.TABLE_PROBE_BYTES)]
    v = R.evaluate(baseline_reading(tables=on_boundary), now=NOW)
    assert v["controls"]["probe_size_matches"] == 0
    assert v["state"] == R.STATE_NO_DATA
    assert "0 tables exceed" in v["detail"], v["detail"]

    above = [dict(t, bytes=R.TABLE_PROBE_BYTES + 1) for t in on_boundary]
    v2 = R.evaluate(baseline_reading(tables=above), now=NOW)
    assert v2["controls"]["probe_size_matches"] == 2


def test_an_alarm_outranks_a_warn_found_in_the_same_reading():
    """🔴 A 1.5 GiB store is a WARN and a resurrected `trace_log` is an ALARM.
    Reporting the WARN would exit 3 instead of 1 and understate the worst thing
    found — the two levels had never been seeded together."""
    tables = baseline_tables() + [tbl("system", "trace_log", rows=1,
                                      size=5 * R.MIB)]
    v = R.evaluate(baseline_reading(du_bytes=int(1.5 * R.GIB), tables=tables),
                   now=NOW)
    assert v["warns"] == 1, v["findings"]
    assert v["alarms"] == 1, v["findings"]
    assert v["state"] == R.STATE_ALARM
    assert R.exit_code(v) == R.EXIT_ALARM


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
    with no `activity.events`. That is not a clean activity store.

    🔴 THE FIXTURE MUST NOT COLLAPSE. The predicate is `database == 'activity'
    AND name == 'events'`; with no other `activity.*` table and no other table
    called `events` in the listing, `AND` and `OR` compute the SAME answer and
    the fixture cannot tell them apart — an `and`->`or` mutant survived on
    exactly that. So both non-default siblings are seeded here: matching only
    the database, and matching only the name, must each still be a miss.
    """
    tables = [t for t in baseline_tables()
              if not (t["database"] == "activity" and t["name"] == "events")]
    tables += [tbl("activity", "other", rows=7, size=3 * R.MIB),
               tbl("system", "events", rows=9, size=4 * R.MIB)]
    assert any(t["database"] == "activity" for t in tables)
    assert any(t["name"] == "events" for t in tables)
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
# 🔴 `du` PARTIAL WALK — the observed 15% false CANNOT-TELL (audit #1a)
#
# 3 of 20 live runs exited 2 because busybox `du` returned 1 after racing a
# `tmp_merge_*` directory that vanished mid-walk — WHILE PRINTING THE CORRECT
# TOTAL. These are the only tests that drive `run_du` through a REAL subprocess;
# every other error test injects a fake `du`, which is exactly why the
# returncode guard had zero coverage.
# --------------------------------------------------------------------------- #
PARTIAL_DU_ARGV = [
    sys.executable, "-c",
    "import sys\n"
    "print('541224\\t/var/lib/clickhouse/store')\n"
    "sys.stderr.write('du: /var/lib/clickhouse/store/ede/tmp_merge_202608_"
    "17463_29293_234: No such file or directory\\n')\n"
    "sys.exit(1)\n",
]


def test_run_du_partial_walk_is_a_measurement_not_a_failure():
    """🔴 THE REGRESSION. Non-zero exit + a usable total on stdout is a
    MEASUREMENT. Before the fix this raised and the whole month's run became a
    CANNOT-TELL indistinguishable from a real ALARM."""
    out = R.run_du(PARTIAL_DU_ARGV)
    assert R.parse_du_kib(out) == 541224 * 1024


def test_run_du_partial_walk_against_the_real_du_binary(tmp_path):
    """The same case driven through the actual `du` binary rather than a
    simulation — `du -sk <dir> <missing>` is the shape confirmed in-pod."""
    du = shutil.which("du")
    assert du, "coreutils du must be on PATH for this test to mean anything"
    store = tmp_path / "store"
    store.mkdir()
    (store / "part.bin").write_bytes(b"x" * 300_000)
    argv = [du, "-sk", str(store), str(tmp_path / "tmp_merge_gone")]

    # Positive control for the FIXTURE: prove this argv really does exit
    # non-zero and really does print a total, or the test below is vacuous.
    p = subprocess.run(argv, capture_output=True, text=True)
    assert p.returncode != 0, "fixture is vacuous: du exited 0"
    assert p.stdout.strip(), "fixture is vacuous: du printed no total"

    assert R.parse_du_kib(R.run_du(argv)) >= 292 * 1024


def test_run_du_still_raises_when_there_is_no_usable_total():
    """🔴 FAIL-CLOSED HALF. Parsing stdout first must not turn a genuinely
    failed exec into a small store: no total on stdout still raises, which is
    what makes the caller report exec-failed / exit 2."""
    argv = [sys.executable, "-c",
            "import sys; sys.stderr.write('kubectl: pod not found\\n');"
            " sys.exit(1)"]
    with pytest.raises(RuntimeError) as e:
        R.run_du(argv)
    assert "exit 1" in str(e.value)
    assert "pod not found" in str(e.value)


def test_run_du_raises_on_a_zero_exit_that_printed_nothing_usable():
    """The other half of the same guard: a clean exit is not a measurement
    either if nothing parseable came out."""
    argv = [sys.executable, "-c", "print('du: Permission denied')"]
    with pytest.raises(RuntimeError):
        R.run_du(argv)


def test_partial_du_walk_reaches_a_measured_verdict_end_to_end():
    """The finding as the operator experiences it: the whole check, with a `du`
    that exits 1 mid-walk, must produce a real verdict — not exit 2."""
    v = R.check(env=GOOD_ENV, now=NOW,
                query=fake_query_factory(good_responses()),
                du=lambda argv, timeout: R.run_du(PARTIAL_DU_ARGV, timeout))
    assert v["state"] == R.STATE_OK, v["detail"]
    assert v["measurements"]["du_bytes"] == 541224 * 1024
    assert R.exit_code(v) == R.EXIT_OK


# --------------------------------------------------------------------------- #
# 🔴 TRANSIENT QUERY FAILURE — the other half of the 15% (audit #1b)
#
# The TTL union returned `HTTP 500 / Code: 241 MEMORY_LIMIT_EXCEEDED` on 2 of 20
# live runs: the pod's 2.5 GiB ceiling reached by background merges. Retry —
# without ever letting an exhausted retry reach `ok`.
# --------------------------------------------------------------------------- #
def memory_limit_500():
    return urllib.error.HTTPError(
        "http://ch.invalid:8123/", 500, "Internal Server Error", {}, None)


def flaky_query(responses, fail_on, failures, exc_factory=memory_limit_500):
    """Fail the first `failures` calls whose SQL contains `fail_on`, then
    answer normally. Records every call so attempt COUNTS are assertable."""
    calls = []

    def q(url, user, password, sql, timeout):
        calls.append(sql)
        if fail_on in sql:
            if sum(1 for c in calls if fail_on in c) <= failures:
                raise exc_factory()
        for needle, reply in responses.items():
            if needle in sql:
                return reply
        raise AssertionError("unexpected query: %s" % sql[:80])

    q.calls = calls
    return q


def test_transient_500_is_retried_and_the_check_recovers():
    """🔴 THE REGRESSION. Two 500s then success must yield a real verdict, and
    must be shown to have actually retried (2 extra calls, 2 real backoffs) —
    otherwise this passes on a fake that never failed."""
    slept = []
    q = flaky_query(good_responses(), "UNION ALL", 2)
    v = R.check(env=GOOD_ENV, now=NOW, query=q, du=ok_du, sleep=slept.append)
    assert v["state"] == R.STATE_OK, v["detail"]
    assert sum(1 for c in q.calls if "UNION ALL" in c) == 3
    assert slept == list(R.QUERY_BACKOFF_SECONDS)
    assert v["controls"]["ttl_tables_measured"] == 4


@pytest.mark.parametrize("fail_on", ["now()", "system.tables"])
def test_transient_500_is_retried_on_every_query_not_just_the_union(fail_on):
    slept = []
    q = flaky_query(good_responses(), fail_on, 1)
    v = R.check(env=GOOD_ENV, now=NOW, query=q, du=ok_du, sleep=slept.append)
    assert v["state"] == R.STATE_OK, v["detail"]
    assert len(slept) == 1


def test_exhausted_retries_still_fail_closed():
    """🔴 THE FAIL-CLOSED HALF, and the reason a retry is safe here at all. When
    every attempt 500s the verdict must be UNREACHABLE and exit 2 — never `ok`,
    never a fall-through."""
    slept = []
    q = flaky_query(good_responses(), "now()", 99)
    v = R.check(env=GOOD_ENV, now=NOW, query=q, du=ok_du, sleep=slept.append)
    assert v["state"] == R.STATE_UNREACHABLE
    assert v["state"] not in (R.STATE_OK, R.STATE_WARN)
    assert R.exit_code(v) == R.EXIT_UNKNOWN
    assert sum(1 for c in q.calls if "now()" in c) == R.QUERY_ATTEMPTS
    assert len(slept) == R.QUERY_ATTEMPTS - 1


def test_a_4xx_is_not_retried_because_it_is_a_durable_answer():
    """A rotated password does not become right on attempt 3. Retrying a 401
    would only spend the unit's start timeout — and must not mute it either."""
    slept = []
    err = urllib.error.HTTPError("http://ch.invalid:8123/", 401,
                                 "Unauthorized", {}, None)
    q = flaky_query(good_responses(), "now()", 99, exc_factory=lambda: err)
    v = R.check(env=GOOD_ENV, now=NOW, query=q, du=ok_du, sleep=slept.append)
    assert v["state"] == R.STATE_UNREACHABLE
    assert R.exit_code(v) == R.EXIT_UNKNOWN
    assert sum(1 for c in q.calls if "now()" in c) == 1
    assert slept == []


def test_a_timeout_is_not_retried_because_it_already_spent_its_budget():
    slept = []
    q = flaky_query(good_responses(), "now()", 99,
                    exc_factory=lambda: TimeoutError("timed out"))
    v = R.check(env=GOOD_ENV, now=NOW, query=q, du=ok_du, sleep=slept.append)
    assert R.exit_code(v) == R.EXIT_UNKNOWN
    assert sum(1 for c in q.calls if "now()" in c) == 1
    assert slept == []


def per_table_ttl_query(union_exc=memory_limit_500, ok_tables=None):
    """A server on which the TTL UNION always 500s (the memory ceiling) but the
    per-table queries in `ok_tables` succeed."""
    rows = {r["table"]: r for r in baseline_ttl()}
    ok_tables = rows.keys() if ok_tables is None else ok_tables
    calls = []

    def q(url, user, password, sql, timeout):
        calls.append(sql)
        if "now()" in sql:
            return "%d\n" % NOW
        if "FROM system.tables" in sql:
            return listing_tsv(baseline_tables())
        if "UNION ALL" in sql:
            raise union_exc()
        for name in rows:
            if sql.startswith("SELECT '%s'" % name):
                if name not in ok_tables:
                    raise union_exc()
                return ttl_tsv([rows[name]])
        raise AssertionError("unexpected query: %s" % sql[:80])

    q.calls = calls
    return q


def test_ttl_union_falls_back_to_one_query_per_table():
    """The union aggregates all four targets at once and is what hit the 2.5 GiB
    ceiling. Splitting it is the last resort before CANNOT TELL."""
    q = per_table_ttl_query()
    v = R.check(env=GOOD_ENV, now=NOW, query=q, du=ok_du, sleep=lambda s: None)
    assert v["state"] == R.STATE_OK, v["detail"]
    assert v["controls"]["ttl_tables_measured"] == 4
    assert sum(1 for c in q.calls if "UNION ALL" in c) == R.QUERY_ATTEMPTS
    assert sum(1 for c in q.calls
               if "UNION ALL" not in c and " AS t," in c) == 4


def test_a_partial_per_table_fallback_measures_what_it_can():
    """Two targets answer, two do not. The two that answered are real
    measurements; the two that did not are `not-queried`, never assumed fine."""
    q = per_table_ttl_query(ok_tables={"metric_log", "query_log"})
    v = R.check(env=GOOD_ENV, now=NOW, query=q, du=ok_du, sleep=lambda s: None)
    assert v["controls"]["ttl_tables_measured"] == 2
    unmeasured = {r["table"] for r in v["measurements"]["ttl"]
                  if r["reason"] == "not-queried"}
    assert unmeasured == {"asynchronous_metric_log", "part_log"}


def test_a_fallback_that_measures_nothing_is_cannot_tell_not_ok():
    """🔴 The fallback cannot create a clean verdict out of nothing — and must
    say WHICH thing failed, so `unreachable` is pinned rather than merely
    'some non-ok state'."""
    q = per_table_ttl_query(ok_tables=set())
    v = R.check(env=GOOD_ENV, now=NOW, query=q, du=ok_du, sleep=lambda s: None)
    assert v["state"] == R.STATE_UNREACHABLE
    assert v["state"] not in (R.STATE_OK, R.STATE_WARN)
    assert R.exit_code(v) == R.EXIT_UNKNOWN
    assert "per-table fallbacks" in v["detail"], v["detail"]


def test_the_retry_schedule_is_pinned_to_literal_values():
    """🔴 Asserting `slept == list(QUERY_BACKOFF_SECONDS)` elsewhere reads the
    constant back and would hold for a schedule of zeros. Pin the numbers."""
    assert R.QUERY_ATTEMPTS == 3
    assert R.QUERY_BACKOFF_SECONDS == (2.0, 5.0)
    assert len(R.QUERY_BACKOFF_SECONDS) == R.QUERY_ATTEMPTS - 1
    assert all(s > 0 for s in R.QUERY_BACKOFF_SECONDS)
    # The whole retry budget must stay well inside the unit's TimeoutStartSec.
    assert sum(R.QUERY_BACKOFF_SECONDS) * 3 < R.COLLECT_BUDGET_SECONDS


def test_no_retry_starts_once_the_collection_budget_is_spent():
    """The retries are bounded by wall clock, not just by attempt count — a
    unit killed at TimeoutStartSec mid-retry reports nothing at all."""
    slept = []
    # tick 1 sets the deadline; every later read is already past it.
    ticks = iter([0.0] + [R.COLLECT_BUDGET_SECONDS + 1.0] * 40)
    q = flaky_query(good_responses(), "now()", 99)
    v = R.check(env=GOOD_ENV, now=NOW, query=q, du=ok_du, sleep=slept.append,
                clock=lambda: next(ticks))
    assert R.exit_code(v) == R.EXIT_UNKNOWN
    assert v["state"] == R.STATE_UNREACHABLE
    assert slept == [], "a retry started past the budget"
    assert sum(1 for c in q.calls if "now()" in c) == 1


def test_a_non_transient_ttl_failure_does_not_reach_the_fallback():
    """An unparseable/programming error is a durable answer, not a memory
    spike; splitting the query would only repeat it four times."""
    q = flaky_query(good_responses(), "UNION ALL", 99,
                    exc_factory=lambda: RuntimeError("syntax error"))
    v = R.check(env=GOOD_ENV, now=NOW, query=q, du=ok_du, sleep=lambda s: None)
    assert v["state"] == R.STATE_UNREACHABLE
    assert sum(1 for c in q.calls if " AS t," in c and "UNION ALL" not in c) == 0


# --------------------------------------------------------------------------- #
# 🔴 A CONFIRMED ALARM MUST SURVIVE AN UNEVALUABLE READING (audit #2)
#
# The gates decide whether a reading is EVALUABLE. They must not decide what was
# FOUND. Previously every one of them returned a fresh verdict with
# `findings: []`, so a 5 GiB store or a resurrected 100 GiB `trace_log` vanished
# from the report the moment any positive control also tripped.
# --------------------------------------------------------------------------- #
def test_the_orphan_population_no_longer_destroys_its_own_diagnosis():
    """🔴 THE SHARPEST CASE. Applying a TTL to an existing `*_log` renames it to
    `*_log_0`. Do that to ALL of them and NOTHING ends in `_log` any more — so
    the suffix positive control trips, on precisely the regrowth vector the
    orphan check exists to catch. The verdict is still CANNOT TELL (exit 2), but
    the 20 orphans must be named."""
    tables = [tbl(t["database"],
                  t["name"] + "_0" if t["name"].endswith("_log") else t["name"],
                  t["engine"], t["rows"], t["bytes"])
              for t in baseline_tables()]
    assert not any(t["name"].endswith("_log") for t in tables)
    orphans = [t for t in tables if t["name"].endswith("_0")]
    assert len(orphans) == 14, len(orphans)

    v = R.evaluate(baseline_reading(tables=tables), now=NOW)
    assert v["state"] == R.STATE_NO_DATA          # verdict unchanged
    assert R.exit_code(v) == R.EXIT_UNKNOWN       # exit code unchanged
    assert checks_named(v, "orphan-tables"), v    # DIAGNOSIS preserved
    assert v["alarms"] == 1
    assert "metric_log_0" in v["detail"]
    assert "suffix match" in v["detail"]          # and the gate's own reason
    assert "metric_log_0" in R.render_text(v)


def test_a_store_size_alarm_survives_an_empty_listing():
    """`du` says 5 GiB and the listing came back empty. Both facts matter; the
    5 GiB used to disappear."""
    v = R.evaluate(baseline_reading(du_bytes=5 * R.GIB, tables=[]), now=NOW)
    assert v["state"] == R.STATE_NO_DATA
    assert R.exit_code(v) == R.EXIT_UNKNOWN
    assert checks_named(v, "store-size")
    assert v["alarms"] == 1
    assert "5.0 GiB" in v["detail"]
    assert "came back EMPTY" in v["detail"]


def test_a_resurrected_trace_log_survives_zero_measurable_ttl_targets():
    """`trace_log` back at 100 GiB — the literal 112 GB mechanism — while every
    TTL target reads empty. Reporting only `no-data` here was the worst case."""
    tables = baseline_tables() + [tbl("system", "trace_log",
                                      rows=3_770_000_000, size=100 * R.GIB)]
    ttl = [dict(r, rows=0) for r in baseline_ttl()]
    v = R.evaluate(baseline_reading(tables=tables, ttl=ttl), now=NOW)
    assert v["state"] == R.STATE_NO_DATA
    assert R.exit_code(v) == R.EXIT_UNKNOWN
    assert checks_named(v, "forbidden-tables")
    assert "system.trace_log" in v["detail"]
    assert v["alarms"] >= 1


def test_a_missing_du_reading_no_longer_hides_the_listing_findings():
    """The `du` exec failed AND `trace_log` is back. The exec failure is the
    state; the resurrection is still the news."""
    tables = baseline_tables() + [tbl("system", "trace_log", rows=1,
                                      size=90 * R.GIB)]
    v = R.evaluate(baseline_reading(du_bytes=None, tables=tables), now=NOW)
    assert v["state"] == R.STATE_EXEC_FAILED
    assert R.exit_code(v) == R.EXIT_UNKNOWN
    assert checks_named(v, "forbidden-tables")


def test_an_unknown_verdict_with_no_findings_still_reports_zero_alarms():
    """The counterpart control: carrying findings must not invent them."""
    v = R._unknown(R.STATE_NO_DATA, "synthetic")
    assert v["findings"] == [] and v["alarms"] == 0 and v["warns"] == 0
    assert "findings preserved" not in v["detail"]


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
