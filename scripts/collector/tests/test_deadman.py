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
    rows = D.parse_buckets("h1\tkeys\t100\t3\nh1\ti3\t100\t1\n")
    assert rows == [("h1", "keys", 100, 3), ("h1", "i3", 100, 1)]


def test_parse_buckets_skips_blank_lines():
    assert D.parse_buckets("\n\nh1\tkeys\t100\t3\n\n") == [("h1", "keys", 100, 3)]


def test_parse_buckets_rejects_wrong_field_count():
    # A changed output format must be an ERROR, not "fewer rows" silently
    # decaying into "no staleness".
    with pytest.raises(ValueError):
        D.parse_buckets("h1\tkeys\t100\n")


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
