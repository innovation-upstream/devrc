"""Tests for the activity-telemetry deadman check (scripts/collector/deadman.py).

ALL OFFLINE — every test builds a synthetic bucket table and calls the pure
`evaluate`, or injects a fake `fetch` into `check`. No ClickHouse is contacted.

🔴 THE CONTROL PAIR IS THE POINT OF THIS FILE. The check's reassuring answer is a
ZERO, which is indistinguishable from a check wired to nothing. So the zero is
never asserted alone: `test_control_pair_*` asserts a NON-zero on a deliberately
staled fixture and a zero on the same fixture left alone, through the same code
path. A harness that could only ever produce 0 would fail those tests.

Run: pytest scripts/collector/tests/test_deadman.py
"""
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import deadman as D  # noqa: E402

MODULE = Path(__file__).resolve().parent.parent / "deadman.py"
B = D.BUCKET_SECONDS  # 300


# --------------------------------------------------------------------------- #
# Fixture builders
# --------------------------------------------------------------------------- #
def contiguous(host, source, start, count, step=1):
    """`count` emitting buckets for one pair, every `step` buckets from `start`."""
    return [(host, source, start + i * step * B, 1) for i in range(count)]


def healthy_table(now_bucket, n=400):
    """A busy host: two continuous sources filling every bucket up to now."""
    start = now_bucket - (n - 1) * B
    return (contiguous("h1", "keys", start, n)
            + contiguous("h1", "i3", start, n))


NOW_BUCKET = 1_700_000_000 // B * B


# --------------------------------------------------------------------------- #
# percentile / budget — pure arithmetic
# --------------------------------------------------------------------------- #
def test_percentile_empty_is_zero():
    assert D.percentile([], 0.99) == 0.0


def test_percentile_single_value():
    assert D.percentile([7], 0.99) == 7.0


def test_percentile_interpolates():
    # p50 of [0, 10] is the midpoint, not an endpoint.
    assert D.percentile([0, 10], 0.5) == 5.0


def test_percentile_picks_the_tail():
    """p99 must land in the tail, not near the median — the budget is sized off
    the worst NORMAL lull, so a percentile that ignored the tail would produce a
    budget that cries wolf."""
    vals = [0] * 90 + [1000] * 10
    assert D.percentile(vals, 0.99) == 1000.0
    assert D.percentile(vals, 0.50) == 0.0


def test_budget_floor_applies_to_a_continuous_source():
    # p99 gap of 1 bucket must NOT yield a 2-bucket budget — that would alarm on
    # a coffee break. The floor is what stops the wolf-crying.
    assert D.budget_for(1.0) == D.FLOOR_BUCKETS


def test_budget_scales_with_the_measured_gap():
    assert D.budget_for(100.0, k=2.0) == 200


def test_budget_is_capped():
    assert D.budget_for(100000.0) == D.CAP_BUCKETS


# --------------------------------------------------------------------------- #
# parse_buckets — strict on purpose
# --------------------------------------------------------------------------- #
def test_parse_buckets_happy():
    """5 fields: the current query. The 5th is the OPERATOR-driven count.

    🔴 Every number here is DISTINCT on purpose. An earlier version used n == n_op
    (3, 3), which made "carry the operator column" and "ignore it and reuse n"
    byte-identical — a mutant that dropped the 5th field survived the whole
    suite. A fixture of equal values cannot tell two implementations apart.
    """
    rows = D.parse_buckets("h1\tkeys\t100\t7\t5\nh1\ti3\t200\t3\t0\n")
    assert rows == [("h1", "keys", 100, 7, 5), ("h1", "i3", 200, 3, 0)]
    assert rows[1][4] == 0 and rows[1][3] == 3, \
        "the operator count must survive the parse independently of the total"


def test_parse_buckets_accepts_a_legacy_four_field_capture():
    """A pre-heartbeat capture (or a hand-written fixture) has no operator column.
    Treating it as all-operator is exactly right for a table in which nothing
    emitted on a timer — and it keeps old captures replayable through --tsv."""
    rows = D.parse_buckets("h1\tkeys\t100\t3\n")
    assert rows == [("h1", "keys", 100, 3, 3)]


def test_parse_buckets_rejects_a_width_that_changes_mid_file():
    """Inferring per-line would be the silent-drop failure this parser exists to
    prevent: a truncated or concatenated capture would parse as 'fewer events'
    and decay into 'no staleness'."""
    with pytest.raises(ValueError):
        D.parse_buckets("h1\tkeys\t100\t3\t3\nh1\ti3\t100\t1\n")


def test_parse_buckets_skips_blank_lines():
    assert D.parse_buckets("\n\nh1\tkeys\t100\t3\t3\n\n") == [("h1", "keys", 100, 3, 3)]


def test_parse_buckets_rejects_wrong_field_count():
    # A changed output format must be an ERROR, not "fewer rows" silently
    # decaying into "no staleness".
    with pytest.raises(ValueError):
        D.parse_buckets("h1\tkeys\t100\n")
    with pytest.raises(ValueError):
        D.parse_buckets("h1\tkeys\t100\t3\t3\t9\n")


def test_parse_buckets_rejects_non_integer():
    with pytest.raises(ValueError):
        D.parse_buckets("h1\tkeys\tnotanumber\t3\n")


def test_bucket_query_mentions_table_and_window():
    q = D.bucket_query(days=14, database="activity", table="events")
    assert "activity.events" in q
    assert "INTERVAL 14 DAY" in q
    assert "FORMAT TSV" in q


# --------------------------------------------------------------------------- #
# evaluate — the core verdict
# --------------------------------------------------------------------------- #
def test_healthy_table_reports_ok_and_zero_dead():
    rows = healthy_table(NOW_BUCKET)
    v = D.evaluate(rows, now=NOW_BUCKET + 60)
    assert v["state"] == D.STATE_OK
    assert v["count"] == 0
    assert v["evaluated"] == 2


def test_a_source_that_stopped_is_dead():
    """POSITIVE CONTROL. `keys` stops 200 active buckets before now while `i3`
    keeps emitting; 200 > the 24-bucket floor budget, so it must be DEAD."""
    rows = healthy_table(NOW_BUCKET)
    rows = [r for r in rows if not (r[1] == "keys" and r[2] > NOW_BUCKET - 200 * B)]
    v = D.evaluate(rows, now=NOW_BUCKET + 60)
    assert v["count"] == 1
    assert v["dead"][0]["source"] == "keys"
    assert v["dead"][0]["silent_active_buckets"] == 200


def test_control_pair_same_path_zero_and_nonzero():
    """The pair, reported together — a zero alone proves nothing about a harness."""
    rows = healthy_table(NOW_BUCKET)
    staled = [r for r in rows
              if not (r[1] == "keys" and r[2] > NOW_BUCKET - 200 * B)]
    clean = D.evaluate(rows, now=NOW_BUCKET + 60)
    positive = D.evaluate(staled, now=NOW_BUCKET + 60)
    assert (positive["count"], clean["count"]) == (1, 0)


def test_a_brief_lull_under_the_floor_is_not_dead():
    """ALERT-FATIGUE GUARD: 10 active buckets (50 min) of silence on a
    continuous source must stay quiet — the floor is 24."""
    rows = healthy_table(NOW_BUCKET)
    rows = [r for r in rows if not (r[1] == "keys" and r[2] > NOW_BUCKET - 10 * B)]
    v = D.evaluate(rows, now=NOW_BUCKET + 60)
    assert v["count"] == 0


def test_silence_is_measured_in_ACTIVE_time_not_wall_time():
    """The on-demand answer. Two pairs on one host: `keys` emits in a dense
    recent window, `od` last emitted long before it in WALL time but with only a
    handful of ACTIVE buckets in between — so it is NOT dead, even though wall
    staleness would call it stale by days."""
    # One old island of activity where BOTH sources emit, then a long stretch in
    # which the host is idle (nothing at all), then `keys` alone emits 3 recent
    # buckets. `od` has been silent for ~347 WALL days but only 3 ACTIVE buckets.
    old = NOW_BUCKET - 100000 * B
    rows = contiguous("h1", "keys", old, 30) + contiguous("h1", "od", old, 30)
    rows += contiguous("h1", "keys", NOW_BUCKET - 2 * B, 3)
    v = D.evaluate(rows, now=NOW_BUCKET + 60)
    od = [s for s in v["sources"] if s["source"] == "od"][0]
    assert od["evaluated"] is True
    assert od["silent_active_buckets"] == 3
    # Wall time says months; active time says 15 minutes. The verdict follows
    # active time — this is the whole reason a fixed staleness window was
    # rejected for on-demand sources.
    assert od["wall_silent_minutes"] > 100 * 24 * 60
    assert od["dead"] is False


def test_pair_below_baseline_is_skipped_not_alarmed():
    """A source with too little history has no normal to compare against, and a
    source that legitimately does not exist on a host (measured: workbench has
    zero `browser` rows) is simply not in the table. Neither may alarm."""
    rows = healthy_table(NOW_BUCKET)
    rows += contiguous("h1", "brandnew", NOW_BUCKET - 100000 * B, 3)
    v = D.evaluate(rows, now=NOW_BUCKET + 60)
    rec = [s for s in v["sources"] if s["source"] == "brandnew"][0]
    assert rec["evaluated"] is False
    assert rec["reason"] == "insufficient-baseline"
    assert rec["dead"] is False
    assert v["count"] == 0


def test_absent_source_on_a_host_cannot_alarm():
    """h2 never emits `keys` at all — it must not appear as a dead pair."""
    rows = healthy_table(NOW_BUCKET) + contiguous("h2", "i3", NOW_BUCKET - 399 * B, 400)
    v = D.evaluate(rows, now=NOW_BUCKET + 60)
    assert ("h2", "keys") not in {(s["host"], s["source"]) for s in v["sources"]}
    assert v["count"] == 0


def test_hosts_are_scored_independently():
    """h1's activity must not supply active buckets to an h2 source."""
    rows = healthy_table(NOW_BUCKET)
    rows += contiguous("h2", "zsh", NOW_BUCKET - 399 * B, 400)
    # h2/zsh stops but h2 has no other source, so h2 has NO active buckets after
    # it stopped -> silence measures 0 and it cannot be judged dead. That is the
    # documented relative-check blind spot, asserted so it stays documented.
    rows = [r for r in rows if not (r[0] == "h2" and r[2] > NOW_BUCKET - 200 * B)]
    v = D.evaluate(rows, now=NOW_BUCKET + 60)
    h2 = [s for s in v["sources"] if s["host"] == "h2"][0]
    assert h2["silent_active_buckets"] == 0
    assert h2["dead"] is False


def test_empty_rows_is_no_data_not_ok():
    """NEGATIVE CONTROL: nothing came back -> we cannot prove we read the table."""
    v = D.evaluate([], now=NOW_BUCKET)
    assert v["state"] == D.STATE_NO_DATA
    assert v["state"] in D.UNKNOWN_STATES
    assert v["count"] == 0


def test_all_pairs_below_baseline_is_no_data_not_ok():
    """Rows came back but nothing was measurable — still cannot tell."""
    rows = contiguous("h1", "x", NOW_BUCKET - 5 * B, 3)
    v = D.evaluate(rows, now=NOW_BUCKET)
    assert v["state"] == D.STATE_NO_DATA
    assert v["evaluated"] == 0


def test_ok_state_requires_a_nonzero_evaluated_count():
    """The positive control baked into the verdict itself: `ok` is unreachable
    unless at least one pair was actually measured."""
    v = D.evaluate(healthy_table(NOW_BUCKET), now=NOW_BUCKET)
    assert v["state"] == D.STATE_OK
    assert v["evaluated"] > 0
    assert v["rows"] > 0


def test_newest_event_age_is_reported_but_never_alarms():
    """A total blackout (every source on every host) is out of scope and must
    NOT be faked into an alarm — it is indistinguishable from the operator being
    away. It is reported, not alarmed."""
    rows = healthy_table(NOW_BUCKET)
    v = D.evaluate(rows, now=NOW_BUCKET + 30 * 86400)
    assert v["state"] == D.STATE_OK
    assert v["count"] == 0
    assert v["newest_event_age_minutes"] > 30 * 24 * 60 - 60


def test_describe_lists_dead_pairs():
    rows = healthy_table(NOW_BUCKET)
    rows = [r for r in rows if not (r[1] == "keys" and r[2] > NOW_BUCKET - 200 * B)]
    v = D.evaluate(rows, now=NOW_BUCKET + 60)
    assert "h1/keys" in v["detail"]


def test_describe_clean_names_the_evaluated_count():
    assert D.describe([], 17) == "17 source(s) fresh"


# --------------------------------------------------------------------------- #
# check() — every failure branch is its OWN state, none of them `ok`
# --------------------------------------------------------------------------- #
def test_check_not_configured_when_no_url(tmp_path):
    v = D.check(env={}, env_file=str(tmp_path / "nope"))
    assert v["state"] == D.STATE_NOT_CONFIGURED
    assert v["state"] in D.UNKNOWN_STATES


def test_check_unreachable_when_fetch_raises(tmp_path):
    def boom(*a, **kw):
        raise OSError("timed out")
    v = D.check(env={"CLICKHOUSE_URL": "http://x"}, env_file=str(tmp_path / "nope"),
                fetch=boom)
    assert v["state"] == D.STATE_UNREACHABLE
    assert "timed out" in v["detail"]


def test_check_query_failed_on_unparseable_body(tmp_path):
    v = D.check(env={"CLICKHOUSE_URL": "http://x"}, env_file=str(tmp_path / "nope"),
                fetch=lambda *a, **kw: "Code: 62. DB::Exception: Syntax error")
    assert v["state"] == D.STATE_QUERY_FAILED


def test_check_no_data_on_empty_body(tmp_path):
    v = D.check(env={"CLICKHOUSE_URL": "http://x"}, env_file=str(tmp_path / "nope"),
                fetch=lambda *a, **kw: "")
    assert v["state"] == D.STATE_NO_DATA


def test_check_ok_on_a_healthy_body(tmp_path):
    body = "".join("%s\t%s\t%d\t1\n" % (h, s, b)
                   for (h, s, b, _n) in healthy_table(NOW_BUCKET))
    v = D.check(env={"CLICKHOUSE_URL": "http://x"}, env_file=str(tmp_path / "nope"),
                fetch=lambda *a, **kw: body, now=NOW_BUCKET + 60)
    assert v["state"] == D.STATE_OK
    assert v["count"] == 0
    assert v["evaluated"] == 2


def test_no_unknown_state_is_ok():
    """INVARIANT GUARD (not regression coverage): `ok` is never in the set of
    states that mean 'cannot tell'."""
    assert D.STATE_OK not in D.UNKNOWN_STATES
    assert len(D.UNKNOWN_STATES) == 4


# --------------------------------------------------------------------------- #
# resolve_config / load_env_file
# --------------------------------------------------------------------------- #
def test_env_file_supplies_credentials(tmp_path):
    p = tmp_path / "env"
    p.write_text("# comment\nCLICKHOUSE_URL=http://ch:8123\n"
                 "CLICKHOUSE_USER=reader\nCLICKHOUSE_PASSWORD=pw\njunkline\n")
    cfg = D.resolve_config(env={}, env_file=str(p))
    assert cfg["url"] == "http://ch:8123"
    assert cfg["user"] == "reader"
    assert cfg["password"] == "pw"
    assert cfg["database"] == "activity"


def test_process_env_wins_over_the_file(tmp_path):
    p = tmp_path / "env"
    p.write_text("CLICKHOUSE_URL=http://from-file\n")
    cfg = D.resolve_config(env={"CLICKHOUSE_URL": "http://from-env"}, env_file=str(p))
    assert cfg["url"] == "http://from-env"


def test_missing_env_file_is_not_an_error(tmp_path):
    assert D.load_env_file(str(tmp_path / "absent")) == {}


# --------------------------------------------------------------------------- #
# CLI — exit codes carry the verdict
# --------------------------------------------------------------------------- #
def _run_cli(args, cwd=None):
    return subprocess.run([sys.executable, str(MODULE)] + args,
                          capture_output=True, text=True, timeout=60)


def test_cli_tsv_clean_exits_zero(tmp_path):
    tsv = tmp_path / "clean.tsv"
    tsv.write_text("".join("%s\t%s\t%d\t1\n" % (h, s, b)
                           for (h, s, b, _n) in healthy_table(NOW_BUCKET)))
    p = _run_cli(["--tsv", str(tsv), "--now", str(NOW_BUCKET + 60), "--json"])
    assert p.returncode == 0, p.stderr
    v = json.loads(p.stdout)
    assert (v["state"], v["count"]) == ("ok", 0)


def test_cli_tsv_with_a_dead_source_exits_one(tmp_path):
    """POSITIVE CONTROL through the CLI: the same file minus one source's recent
    buckets must move the count off zero AND change the exit code."""
    rows = [r for r in healthy_table(NOW_BUCKET)
            if not (r[1] == "keys" and r[2] > NOW_BUCKET - 200 * B)]
    tsv = tmp_path / "stale.tsv"
    tsv.write_text("".join("%s\t%s\t%d\t1\n" % (h, s, b) for (h, s, b, _n) in rows))
    p = _run_cli(["--tsv", str(tsv), "--now", str(NOW_BUCKET + 60), "--json"])
    assert p.returncode == 1, p.stderr
    v = json.loads(p.stdout)
    assert v["count"] == 1


def test_cli_unreadable_tsv_exits_two(tmp_path):
    p = _run_cli(["--tsv", str(tmp_path / "absent.tsv"), "--json"])
    assert p.returncode == 2
    assert json.loads(p.stdout)["state"] == "query-failed"


def test_cli_not_configured_exits_two(tmp_path):
    p = _run_cli(["--env-file", str(tmp_path / "absent"), "--json"])
    # No CLICKHOUSE_URL in the sandbox env and no env file -> not-configured.
    if p.returncode == 2:
        assert json.loads(p.stdout)["state"] in D.UNKNOWN_STATES
    else:  # a dev host WITH the collector env file configured reaches ClickHouse
        assert p.returncode in (0, 1)


def test_cli_print_query():
    p = _run_cli(["--print-query"])
    assert p.returncode == 0
    assert "activity.events" in p.stdout


def test_cli_text_render_shows_the_table(tmp_path):
    tsv = tmp_path / "clean.tsv"
    tsv.write_text("".join("%s\t%s\t%d\t1\n" % (h, s, b)
                           for (h, s, b, _n) in healthy_table(NOW_BUCKET)))
    p = _run_cli(["--tsv", str(tsv), "--now", str(NOW_BUCKET + 60)])
    assert "budget_h" in p.stdout
    assert "keys" in p.stdout


# --------------------------------------------------------------------------- #
# The heartbeat contract — why browser-bridge grew a cadence
#
# An on-demand source (browser-bridge, tool) emits only when an agent drives it,
# so its normal silence is UNBOUNDED and no measured budget can separate "unused"
# from "down". Measured 2026-08-11: laptop/browser-bridge was silent 30.9 ACTIVE
# hours -- 2.2x the worst lull in its own 14-day history, and 2x what even a
# K=4 budget would have allowed -- purely because nobody had run a browser task.
# Raising the budget could not fix that; giving the source a CADENCE could.
#
# These two tests pin the before/after as DATA, so the claim "a 15-minute
# heartbeat collapses the budget onto the 2-active-hour floor" is checked rather
# than asserted in a commit message. The heartbeat emitter itself lives in
# scripts/browser-bridge/server.py (run_heartbeat / HEARTBEAT_INTERVAL_S) and is
# tested there; this is the other half of that seam.
# --------------------------------------------------------------------------- #
HEARTBEAT_STEP = 3  # 900s / BUCKET_SECONDS -- one heartbeat every 3 buckets
# The interval<->bucket relationship this number encodes is pinned against the
# emitter's real constant in scripts/browser-bridge/tests/test_server.py
# (test_heartbeat_interval_keeps_the_deadman_budget_on_the_floor), so this stays
# a readable fixture constant instead of a second, silently-drifting definition.


def workday_table(now_bucket, days=6, awake_h=10):
    """A REALISTIC host: `awake_h` hours of operator activity per day, then idle.

    🔴 This fixture exists because `healthy_table` has NO idle time, and a
    fixture with no idle time is structurally blind to every bug about what
    counts as active time — which is precisely the bug that shipped in the first
    version of the heartbeat (see MACHINE_CADENCE in deadman.py). Any test about
    machine-cadence emitters must use THIS one.
    """
    rows = []
    per_day = 24 * 3600 // B
    awake = awake_h * 3600 // B
    day0 = now_bucket - (days - 1) * per_day * B
    for d in range(days):
        start = day0 + d * per_day * B
        rows += contiguous("h1", "keys", start, awake)
        rows += contiguous("h1", "i3", start, awake)
    return rows


def cadence_rows(host, source, start, end, step=HEARTBEAT_STEP):
    """A machine-cadence emitter ticking from `start` to `end` regardless of
    whether anyone is at the desk. n=1, n_op=0 — the shape the real query emits
    for a MACHINE_CADENCE pair."""
    return [(host, source, b, 1, 0) for b in range(start, end + 1, step * B)]


def _pair(verdict, source, host="h1"):
    recs = [s for s in verdict["sources"]
            if s["source"] == source and s["host"] == host]
    assert recs, "pair %s/%s missing from the verdict" % (host, source)
    return recs[0]


def test_command_only_source_earns_an_unbounded_budget():
    """THE BEFORE CASE (the negative control for the fix): a source that emits in
    rare bursts gets a budget so wide it cannot mean anything.

    This is the shape browser-bridge had. The test asserts the PROBLEM exists, so
    that the after-case below is a measured contrast and not a lone green."""
    n = 400
    start = NOW_BUCKET - (n - 1) * B
    rows = healthy_table(NOW_BUCKET, n)
    # Five short bursts separated by long idle stretches. Five (not three) so the
    # pair clears MIN_BASELINE_BUCKETS and is actually JUDGED — at 15 buckets it
    # is skipped as insufficient-baseline and the test proves nothing.
    for offset in (0, 90, 180, 270, 350):
        rows += contiguous("h1", "bursty", start + offset * B, 5)
    assert 5 * 5 >= D.MIN_BASELINE_BUCKETS, "fixture no longer clears the baseline"
    v = D.evaluate(rows, now=NOW_BUCKET + 60)
    rec = _pair(v, "bursty")
    assert rec["evaluated"] is True
    assert rec["budget_active_hours"] > 8.0, \
        ("a burst-only source should earn a budget far above the floor — got "
         "%r; if this ever drops, the after-case below proves nothing"
         % rec["budget_active_hours"])


def test_heartbeat_cadence_collapses_the_budget_to_the_floor():
    """THE AFTER CASE: the same source emitting every 3rd bucket (900s) has a p99
    active-gap of ~3 buckets, so its budget bottoms out on FLOOR_BUCKETS.

    That is the whole point of the heartbeat: at a 2-ACTIVE-HOUR budget, silence
    means the unit is not running, and no amount of not-using-the-browser can
    manufacture it."""
    n = 400
    start = NOW_BUCKET - (n - 1) * B
    rows = healthy_table(NOW_BUCKET, n)
    rows += contiguous("h1", "browser-bridge", start, n // HEARTBEAT_STEP,
                       step=HEARTBEAT_STEP)
    v = D.evaluate(rows, now=NOW_BUCKET + 60)
    rec = _pair(v, "browser-bridge")
    assert rec["evaluated"] is True
    assert rec["budget_buckets"] == D.FLOOR_BUCKETS, rec
    assert rec["budget_active_hours"] == 2.0, rec
    assert rec["dead"] is False, "a beating heartbeat must never read as dead"


def test_a_machine_cadence_source_does_not_make_idle_sources_look_dead():
    """🔴 THE REGRESSION TEST for the flaw the first version of this change had.

    A 24/7 emitter must not convert the operator's night into ACTIVE time. When
    it did, six real pairs flipped to DEAD in a sweep over live data — including
    a 20-hour continuous false DEAD on workbench/keys across a Saturday the
    operator was away.

    Evaluated at 06:00 — eight hours into an idle stretch — which is exactly the
    moment the 45-second bar poller would ask, and exactly the moment the broken
    version cried wolf.
    """
    rows = workday_table(NOW_BUCKET, days=6, awake_h=10)
    last_awake = max(b for (_h, s, b, *_r) in rows if s == "keys")
    now = last_awake + 8 * 3600            # 8 idle hours later
    rows += cadence_rows("h1", "browser-bridge",
                         min(b for (_h, _s, b, *_r) in rows), now)

    v = D.evaluate(rows, now=now + 60)
    dead = sorted("%s/%s" % (s["host"], s["source"]) for s in v["dead"])
    assert dead == [], (
        "a machine-cadence emitter made idle operator sources read DEAD: %s"
        % dead)

    # ... and the negative control: the SAME table with the heartbeat counted as
    # operator activity (n_op=1) is how the bug manifests. If this stops failing,
    # the assertion above has stopped meaning anything.
    as_operator = [r for r in rows if r[1] != "browser-bridge"]
    as_operator += [(r[0], r[1], r[2], r[3], 1) for r in rows
                    if r[1] == "browser-bridge"]
    v_bad = D.evaluate(as_operator, now=now + 60)
    assert v_bad["count"] > 0, \
        ("the control did not reproduce the bug — this fixture can no longer "
         "distinguish the fix from its absence")


def test_a_cadence_source_is_still_judged_on_its_own_liveness():
    """Excluding heartbeats from ACTIVE time must not exclude them from the
    source's OWN liveness — otherwise the fix for finding #1 would silently undo
    the fix this whole change exists for."""
    rows = workday_table(NOW_BUCKET, days=6, awake_h=10)
    lo = min(b for (_h, _s, b, *_r) in rows)
    last_awake = max(b for (_h, s, b, *_r) in rows if s == "keys")

    alive = rows + cadence_rows("h1", "browser-bridge", lo, last_awake)
    rec = _pair(D.evaluate(alive, now=last_awake + 60), "browser-bridge")
    assert rec["evaluated"] is True, rec
    assert rec["budget_buckets"] == D.FLOOR_BUCKETS, rec
    assert rec["dead"] is False, rec

    # Stopped one full workday earlier -> more than FLOOR_BUCKETS of OPERATOR
    # time has passed since, so it must alarm.
    stopped = rows + cadence_rows("h1", "browser-bridge", lo,
                                  last_awake - 6 * 3600)
    rec2 = _pair(D.evaluate(stopped, now=last_awake + 60), "browser-bridge")
    assert rec2["dead"] is True, rec2


def test_the_operator_column_survives_the_WHOLE_path_tsv_to_verdict():
    """🔴 END-TO-END across the parse boundary, because every other test in this
    section hands `evaluate` ready-made tuples and therefore cannot see the parser
    throwing the operator count away.

    That gap was real: a mutant making parse_buckets reuse `n` for `n_op` — which
    reinstates the original bug on live data, since the check only ever reads the
    table through this function — survived the entire 360-test suite.
    """
    rows = workday_table(NOW_BUCKET, days=6, awake_h=10)
    last_awake = max(b for (_h, s, b, *_r) in rows if s == "keys")
    now = last_awake + 8 * 3600
    cadence = cadence_rows("h1", "browser-bridge",
                           min(b for (_h, _s, b, *_r) in rows), now)

    # Serialise to the EXACT 5-column TSV the query emits, then read it back the
    # only way the live check ever does.
    tsv = "".join("h1\t%s\t%d\t%d\t%d\n" % (r[1], r[2], r[3], 1) for r in rows)
    tsv += "".join("h1\t%s\t%d\t%d\t%d\n" % (r[1], r[2], r[3], 0) for r in cadence)

    parsed = D.parse_buckets(tsv)
    assert any(r[1] == "browser-bridge" and r[4] == 0 for r in parsed), \
        "the parser dropped the operator column"

    v = D.evaluate(parsed, now=now + 60)
    dead = sorted("%s/%s" % (s["host"], s["source"]) for s in v["dead"])
    assert dead == [], "idle sources read DEAD after a round-trip through the parser: %s" % dead
    assert _pair(v, "browser-bridge")["evaluated"] is True


def test_the_query_check_ACTUALLY_SENDS_carries_the_operator_column():
    """🔴 The production path. Every other `check()` test injects a fake fetch
    that IGNORES its query argument, so nothing asserted what SQL the live check
    sends — and `bucket_query(..., cadence=())` at the call site would produce a
    5-wide TSV with n_op == n, i.e. the original bug, on the only path
    bar-status-poll ever uses, with the suite green.
    """
    seen = {}

    def fake_fetch(url, user, password, query, timeout):
        seen["query"] = query
        return "h1\tkeys\t100\t3\t3\n"

    D.check(env={"CLICKHOUSE_URL": "http://x", "CLICKHOUSE_USER": "u",
                 "CLICKHOUSE_PASSWORD": "p"},
            env_file="/nonexistent", fetch=fake_fetch)
    q = seen.get("query", "")
    # 🔴 The countIf must be the expression aliased AS n_op, not merely PRESENT
    # somewhere in the query. A substring check for "AS n_op" is satisfied by
    # "AS n_op_unused" — a mutant that aliased the filtered count aside and put a
    # raw count() in the n_op position passed exactly that check.
    assert re.search(r"countIf\(NOT \(.+?\)\) AS n_op(?!\w)", q), \
        ("check() did not send the machine-cadence filter as the n_op column — "
         "cadence rows would count as operator activity: %r" % q)
    for src, kind in D.MACHINE_CADENCE:
        assert "source = '%s' AND kind = '%s'" % (src, kind) in q, q


def test_operator_count_expression_matches_the_cadence_table():
    """The SQL and the Python must not drift: the query's countIf is BUILT from
    MACHINE_CADENCE, so a pair added to the tuple appears in the SQL."""
    q = D.bucket_query(cadence=(("browser-bridge", "heartbeat"),))
    assert "countIf(NOT ((source = 'browser-bridge' AND kind = 'heartbeat')))" in q
    assert "AS n_op" in q

    # 🔴 TWO entries, because the module's own note tells maintainers to add
    # them: the terms must be OR-joined. With AND, `countIf(NOT (A AND B))`
    # excludes NOTHING (measured against the live table: 206760 of 206760 rows
    # counted), so a second cadence emitter would silently reinstate the bug.
    q2 = D.bucket_query(cadence=(("a", "beat"), ("b", "tick")))
    assert "(source = 'a' AND kind = 'beat') OR (source = 'b' AND kind = 'tick')" in q2, q2
    assert " AND (source = 'b'" not in q2, "cadence terms joined with AND: %s" % q2
    # Empty cadence degrades to the pre-heartbeat query, which is the right
    # answer when nothing emits on a timer.
    assert "count() AS n_op" in D.bucket_query(cadence=())

    # 🔴 The live table must NOT be empty, asserted before the loop below — an
    # emptied MACHINE_CADENCE makes that loop iterate zero times and pass, and it
    # is exactly the edit that reinstates the bug (a mutant emptying the tuple
    # survived this file until this assertion existed; it died only in the
    # browser-bridge suite, which is not where a deadman reader would look).
    assert ("browser-bridge", "heartbeat") in D.MACHINE_CADENCE, \
        "the browser-bridge heartbeat is no longer declared machine-cadence"
    for src, kind in D.MACHINE_CADENCE:
        assert src in D.bucket_query() and kind in D.bucket_query()


def test_a_stopped_heartbeat_is_dead_within_the_floor():
    """The positive control for the pair above: the SAME cadenced source, stopped
    just over the floor, must flip to DEAD through the same code path.

    Without this, the green above is indistinguishable from a check that can only
    ever say 'ok'."""
    n = 400
    start = NOW_BUCKET - (n - 1) * B
    stopped_at = n - (D.FLOOR_BUCKETS + 5)   # silent for floor+5 active buckets
    rows = healthy_table(NOW_BUCKET, n)
    rows += contiguous("h1", "browser-bridge", start,
                       stopped_at // HEARTBEAT_STEP, step=HEARTBEAT_STEP)
    v = D.evaluate(rows, now=NOW_BUCKET + 60)
    rec = _pair(v, "browser-bridge")
    assert rec["dead"] is True, rec
    assert v["count"] == 1 and v["state"] == D.STATE_OK, v
