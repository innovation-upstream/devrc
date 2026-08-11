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
    """5 fields: the current query. The 5th is the HUMAN-PRESENCE count.

    🔴 Every number here is DISTINCT on purpose. An earlier version used
    n == n_presence (3, 3), which made "carry the presence column" and "ignore it
    and reuse n" byte-identical — a mutant that dropped the 5th field survived the
    whole suite. A fixture of equal values cannot tell two implementations apart.

    The 5th field is AUTHORITATIVE when present: `i3` is a presence source and it
    still parses to 0 here, because the server computed that.
    """
    rows = D.parse_buckets("h1\tkeys\t100\t7\t5\nh1\ti3\t200\t3\t0\n")
    assert rows == [("h1", "keys", 100, 7, 5), ("h1", "i3", 200, 3, 0)]
    assert rows[1][4] == 0 and rows[1][3] == 3, \
        "the presence count must survive the parse independently of the total"


def test_parse_buckets_reconstructs_presence_for_a_legacy_four_field_capture():
    """A legacy capture has no presence column, so the parser rebuilds it from the
    SOURCE NAME — which is exact, because presence is a property of the source
    alone and the source column is right there.

    🔴 Both directions are pinned with LITERAL sources and LITERAL counts. Reusing
    `n` for every row (the old behaviour) reinstates "an agent row means the human
    is here" on every replayed capture; hard-zeroing makes a replayed capture
    unable to judge anything and report a reassuring 0.
    """
    rows = D.parse_buckets("h1\tkeys\t100\t3\nh1\tclaude\t100\t9\n"
                           "h1\ttool\t200\t4\nh1\ttmux\t200\t2\n")
    assert rows == [("h1", "keys", 100, 3, 3),
                    ("h1", "claude", 100, 9, 0),
                    ("h1", "tool", 200, 4, 0),
                    ("h1", "tmux", 200, 2, 2)]


def test_presence_sources_are_the_five_human_driven_ones():
    """🔴 The allowlist itself, pinned as a LITERAL — not derived from the module.

    This is the one assertion that fails when a source is added to or removed from
    the set. `claude`/`tool`/`opencode`/`browser-bridge` are agent-driven and are
    named here as explicitly EXCLUDED: they add active buckets only when the
    operator is away, which is exactly when adding them manufactures a false
    alarm. `browser` (the operator's own browsing) is NOT `browser-bridge` (the
    agent's driver) — that pair of names is one typo apart.
    """
    assert set(D.PRESENCE_SOURCES) == {"keys", "i3", "tmux", "zsh", "browser"}
    for agent in ("claude", "tool", "opencode", "browser-bridge"):
        assert agent not in D.PRESENCE_SOURCES, agent


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


def workday_source(host, source, now_bucket, days=6, awake_h=10, skip_last=0):
    """One source emitting `awake_h` hours a day for `days` days, then nothing.

    `skip_last` drops that many trailing days — how a source is made to STOP
    while the rest of the host carries on.
    """
    rows = []
    per_day = 24 * 3600 // B
    awake = awake_h * 3600 // B
    day0 = now_bucket - (days - 1) * per_day * B
    for d in range(days - skip_last):
        rows += contiguous(host, source, day0 + d * per_day * B, awake)
    return rows


def workday_table(now_bucket, days=6, awake_h=10):
    """A REALISTIC host: `awake_h` hours of operator activity per day, then idle.

    🔴 This fixture exists because `healthy_table` has NO idle time, and a
    fixture with no idle time is structurally blind to every bug about what
    counts as active time — which is precisely the bug that shipped in the first
    version of the heartbeat, and again in the denylist active-time rule (see
    PRESENCE_SOURCES in deadman.py). Any test about what marks a bucket ACTIVE
    must use THIS one.

    Both sources here (`keys`, `i3`) are HUMAN-PRESENCE sources, so this table
    defines the operator's timeline all by itself.
    """
    return (workday_source("h1", "keys", now_bucket, days, awake_h)
            + workday_source("h1", "i3", now_bucket, days, awake_h))


def cadence_rows(host, source, start, end, step=HEARTBEAT_STEP):
    """A machine-cadence emitter ticking from `start` to `end` regardless of
    whether anyone is at the desk. n=1, n_presence=0 — the shape the real query
    emits for any source that is not in PRESENCE_SOURCES."""
    return [(host, source, b, 1, 0) for b in range(start, end + 1, step * B)]


def all_night_rows(host, source, start, end):
    """An AGENT source emitting in EVERY bucket from `start` to `end`, straight
    through the night. 4-tuples on purpose: the presence column is reconstructed
    from the source name, exactly as `parse_buckets` does for a legacy capture."""
    return [(host, source, b, 1) for b in range(start, end + 1, B)]


def _dead_pairs(verdict):
    return sorted("%s/%s" % (s["host"], s["source"]) for s in verdict["dead"])


def _as_any_source_active(rows):
    """The OLD active-time rule, re-expressed as data: every row's own count is
    its presence count, i.e. ANY source emitting marks the bucket ACTIVE."""
    return [(r[0], r[1], r[2], r[3], r[3]) for r in rows]


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
    # presence (n_presence=1) is how the bug manifests. If this stops failing,
    # the assertion above has stopped meaning anything.
    as_present = [r for r in rows if r[1] != "browser-bridge"]
    as_present += [(r[0], r[1], r[2], r[3], 1) for r in rows
                   if r[1] == "browser-bridge"]
    v_bad = D.evaluate(as_present, now=now + 60)
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


def test_the_presence_column_survives_the_WHOLE_path_tsv_to_verdict():
    """🔴 END-TO-END across the parse boundary, because every other test in this
    section hands `evaluate` ready-made tuples and therefore cannot see the parser
    throwing the presence count away.

    That gap was real: a mutant making parse_buckets reuse `n` for the presence
    column — which reinstates the original bug on live data, since the check only
    ever reads the table through this function — survived the entire 360-test
    suite.
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
        "the parser dropped the presence column"

    v = D.evaluate(parsed, now=now + 60)
    dead = sorted("%s/%s" % (s["host"], s["source"]) for s in v["dead"])
    assert dead == [], "idle sources read DEAD after a round-trip through the parser: %s" % dead
    assert _pair(v, "browser-bridge")["evaluated"] is True


def test_the_query_check_ACTUALLY_SENDS_carries_the_presence_column():
    """🔴 The production path. Every other `check()` test injects a fake fetch
    that IGNORES its query argument, so nothing asserts what SQL the live check
    sends — and `bucket_query(..., presence=<anything else>)` at the call site
    would produce a 5-wide TSV whose n_presence means something other than
    "a human was here", on the only path bar-status-poll ever uses, with the
    suite green.
    """
    seen = {}

    def fake_fetch(url, user, password, query, timeout):
        seen["query"] = query
        return "h1\tkeys\t100\t3\t3\n"

    D.check(env={"CLICKHOUSE_URL": "http://x", "CLICKHOUSE_USER": "u",
                 "CLICKHOUSE_PASSWORD": "p"},
            env_file="/nonexistent", fetch=fake_fetch)
    q = seen.get("query", "")
    # 🔴 The countIf must be the expression aliased AS n_presence, not merely
    # PRESENT somewhere in the query. A substring check for "AS n_presence" is
    # satisfied by "AS n_presence_unused" — a mutant that aliased the filtered
    # count aside and put a raw count() in the n_presence position passes exactly
    # that check. The literal IN-list is spelled out here, so a query built from
    # ANY other set (the cadence pairs, an emptied allowlist, an allowlist with
    # `claude` added) fails on THIS assertion rather than somewhere downstream.
    assert re.search(
        r"countIf\(source IN \('browser', 'i3', 'keys', 'tmux', 'zsh'\)\) "
        r"AS n_presence(?!\w)", q), \
        ("check() did not send the human-presence filter as the n_presence "
         "column — non-presence rows would count as the operator being at the "
         "machine: %r" % q)
    # ...and the agent sources must NOT be anywhere in the presence predicate.
    for agent in ("claude", "tool", "opencode", "browser-bridge"):
        assert "'%s'" % agent not in q, (agent, q)


def test_presence_count_expression_matches_the_presence_set():
    """The SQL and the Python must not drift: the query's countIf is BUILT from
    PRESENCE_SOURCES, so a source added to the set appears in the SQL."""
    q = D.bucket_query(presence=frozenset({"keys", "tmux"}))
    assert "countIf(source IN ('keys', 'tmux')) AS n_presence" in q, q

    # Rendering is SORTED, so the SQL is stable across runs even though
    # PRESENCE_SOURCES is a set (a set literal's iteration order is not a
    # contract, and an unstable query string defeats every substring assertion
    # here as well as any query-log diffing).
    assert D.presence_predicate_sql(frozenset({"zsh", "browser", "i3"})) == \
        "source IN ('browser', 'i3', 'zsh')"

    # 🔴 An EMPTY allowlist must NOT degrade to counting everything. `count()`
    # there is the denylist behaviour this change removed, and it is the shape a
    # careless "empty means no filter" refactor produces. It renders the constant
    # 0 instead: nothing is presence, so nothing is active, so nothing is judged
    # — quiet, never falsely reassuring about a live rule.
    empty = D.bucket_query(presence=frozenset())
    assert "0 AS n_presence" in empty, empty
    assert "count() AS n_presence" not in empty, empty
    assert D.presence_predicate_sql(frozenset()) == ""

    # The DEFAULT is what the production call site uses.
    assert D.presence_count_expr() == \
        "countIf(source IN ('browser', 'i3', 'keys', 'tmux', 'zsh'))"


def test_cadence_predicate_is_exported_for_the_other_consumer():
    """`cadence_predicate_sql` + `MACHINE_CADENCE` are PUBLIC on purpose, and are
    NO LONGER USED BY THIS MODULE'S OWN QUERY — active time is defined by
    PRESENCE_SOURCES, which subsumes them (`browser-bridge` is not a presence
    source). They survive because scripts/agent-ops imports both for its
    telemetry-freshness panel, where the question is "is this row machine
    generated?" rather than "does this row prove a human is at the desk?" — that
    panel still counts `claude`/`tool` as real usage.

    A local copy of the rendering there was immediately wrong in the same way
    (AND-joined), so this pins the contract that consumer depends on. Its seam
    tests live in scripts/tests/test_agent_ops.py.
    """
    assert D.cadence_predicate_sql(()) == ""
    one = D.cadence_predicate_sql((("x", "beat"),))
    assert one == "(source = 'x' AND kind = 'beat')", one
    # 🔴 TWO entries: the terms must be OR-joined. With AND, the consumer's
    # `AND NOT (A AND B)` excludes NOTHING (measured against the live table:
    # 206760 of 206760 rows counted), so a second cadence emitter would silently
    # reinstate that panel's bug.
    two = D.cadence_predicate_sql((("x", "beat"), ("y", "tick")))
    assert two == ("(source = 'x' AND kind = 'beat') OR "
                   "(source = 'y' AND kind = 'tick')"), two
    assert " AND (source = 'y'" not in two, "cadence terms joined with AND: %s" % two
    # The DEFAULT argument is what the consumer reaches for (a mutant defaulting
    # it to () survived until this was asserted), and the tuple must be non-empty
    # — an emptied MACHINE_CADENCE makes agent-ops' filter vanish silently.
    assert D.cadence_predicate_sql() == D.cadence_predicate_sql(D.MACHINE_CADENCE)
    assert D.cadence_predicate_sql() != ""
    assert ("browser-bridge", "heartbeat") in D.MACHINE_CADENCE, \
        "the browser-bridge heartbeat is no longer declared machine-cadence"

    # 🔴 And the cadence predicate must NOT have leaked back into this module's
    # own query: two rules for one active-time definition is the shape that
    # regenerates the bug at whichever site gets edited second.
    assert "kind = 'heartbeat'" not in D.bucket_query(), \
        "the bucket query is filtering on machine cadence again"


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


# --------------------------------------------------------------------------- #
# THE PRESENCE ALLOWLIST — active time is HUMAN time, not machine time
#
# The rule active time used to run on was a DENYLIST ("anything that is not a
# timer-driven emitter counts as the operator being present"), which defaulted
# every agent-driven source to "the human is here". On 2026-08-11 an unattended
# overnight agent session emitted `claude`/`tool` rows for hours while the human
# slept; that marked the night ACTIVE, and workbench/keys and workbench/tmux blew
# their 2-ACTIVE-HOUR floor and read DEAD by morning.
#
# 🔴 EVERY test below uses a day/night fixture. `healthy_table` fills every
# bucket, so it has NO idle time and is structurally blind to this entire class
# of bug — a suite built on it stays green through both the denylist rule and the
# allowlist rule and can therefore certify neither.
# --------------------------------------------------------------------------- #
def test_an_unattended_agent_at_night_does_not_make_human_sources_look_dead():
    """🔴 THE REGRESSION TEST. A workday host where an agent source keeps emitting
    all night must not convert the operator's night into ACTIVE time.

    Evaluated 8 hours into the idle stretch — the moment the 45-second bar poller
    would ask, and the moment the denylist rule cried wolf.
    """
    rows = workday_table(NOW_BUCKET, days=6, awake_h=10)      # keys + i3
    lo = min(b for (_h, _s, b, *_r) in rows)
    last_awake = max(b for (_h, s, b, *_r) in rows if s == "keys")
    now = last_awake + 8 * 3600
    agent = all_night_rows("h1", "claude", lo, now)

    v = D.evaluate(rows + agent, now=now + 60)
    assert _dead_pairs(v) == [], (
        "an unattended agent made the operator's own sources read DEAD: %s"
        % _dead_pairs(v))
    # The human sources must have been JUDGED, not skipped — a verdict of "not
    # dead" is worthless if the pair fell out of the evaluated set.
    for src in ("keys", "i3"):
        rec = _pair(v, src)
        assert rec["evaluated"] is True, rec
        # Nothing the operator drove has happened since they stopped, so the
        # silence in ACTIVE time is exactly zero.
        assert rec["silent_active_buckets"] == 0, rec
    # The agent source itself is still measured on its own liveness.
    assert _pair(v, "claude")["evaluated"] is True

    # 🔴 NEGATIVE CONTROL, same fixture, same code path: under the OLD rule (any
    # source's rows mark the bucket active) the night IS active, so the human
    # sources blow the floor. If this stops failing, the fixture no longer
    # reproduces the bug and the green above has stopped meaning anything.
    v_bad = D.evaluate(_as_any_source_active(rows + agent), now=now + 60)
    bad = _dead_pairs(v_bad)
    assert "h1/keys" in bad and "h1/i3" in bad, (
        "the control did not reproduce the bug — this fixture can no longer "
        "distinguish the allowlist from its absence: %s" % bad)


def test_a_human_source_that_genuinely_stops_is_still_dead():
    """🔴 POSITIVE CONTROL for the allowlist: narrowing what counts as active must
    not blind the check to a real death.

    `tmux` stops a full day before the others; `keys`/`i3` keep the operator's
    timeline advancing, so its silence is measured in real active buckets and it
    must alarm. Without this, the test above is indistinguishable from a check
    that can only ever say "ok".
    """
    rows = workday_table(NOW_BUCKET, days=6, awake_h=10)
    rows += workday_source("h1", "tmux", NOW_BUCKET, days=6, awake_h=10,
                           skip_last=1)
    last_awake = max(b for (_h, s, b, *_r) in rows if s == "keys")
    v = D.evaluate(rows, now=last_awake + 60)
    assert _dead_pairs(v) == ["h1/tmux"], _dead_pairs(v)
    rec = _pair(v, "tmux")
    # One whole 10-hour workday of the OTHER sources' activity has passed:
    # 10h / 5min = 120 active buckets, against the 24-bucket floor budget.
    assert rec["silent_active_buckets"] == 120, rec
    assert rec["budget_buckets"] == D.FLOOR_BUCKETS, rec


def test_an_agent_source_that_genuinely_stops_is_still_dead():
    """The allowlist changes what marks a bucket ACTIVE — it must not change
    whether a NON-presence source is judged at all. `claude` runs every night for
    five days and then stops; the operator's next workday supplies the active
    buckets that convict it."""
    rows = workday_table(NOW_BUCKET, days=6, awake_h=10)
    lo = min(b for (_h, _s, b, *_r) in rows)
    last_awake = max(b for (_h, s, b, *_r) in rows if s == "keys")
    stopped = last_awake - 24 * 3600          # a full day earlier
    rows += all_night_rows("h1", "claude", lo, stopped)
    v = D.evaluate(rows, now=last_awake + 60)
    rec = _pair(v, "claude")
    assert rec["evaluated"] is True, rec
    assert rec["dead"] is True, rec


def test_losing_every_presence_source_at_once_goes_QUIET_not_loud():
    """🔴 THE DOCUMENTED COST, asserted so it stays documented (module docstring,
    'THE COST OF THE PRESENCE ALLOWLIST').

    An X-session crash takes `keys` and `i3` together. After it, no presence
    source emits, so active buckets stop accruing and every silence measures 0 —
    the check reports 0 dead even though the agent is still emitting. That is a
    WIDENING of the pre-existing blackout blind spot, and it is deliberate: the
    alternative is the overnight false alarm the test above pins.

    `newest_event_age_minutes` is what a human reads instead, so it is asserted
    to be present and non-trivial rather than merely non-None.
    """
    rows = workday_table(NOW_BUCKET, days=6, awake_h=10)
    lo = min(b for (_h, _s, b, *_r) in rows)
    crash = max(b for (_h, s, b, *_r) in rows if s == "keys")
    now = crash + 3 * 86400                   # three days of agent-only rows
    rows += all_night_rows("h1", "claude", lo, now)

    v = D.evaluate(rows, now=now + 60)
    assert v["state"] == D.STATE_OK
    assert _dead_pairs(v) == [], _dead_pairs(v)
    assert v["newest_event_age_minutes"] is not None
    assert v["newest_event_age_minutes"] < 5, v["newest_event_age_minutes"]

    # ...and the CONTRAST that makes this a cost and not a bug: lose only ONE
    # presence source and the other still carries the timeline, so it IS caught.
    partial = workday_source("h1", "keys", NOW_BUCKET, days=6, awake_h=10,
                             skip_last=1)
    partial += workday_source("h1", "i3", NOW_BUCKET, days=6, awake_h=10)
    v2 = D.evaluate(partial, now=max(b for (_h, _s, b, *_r) in partial) + 60)
    assert _dead_pairs(v2) == ["h1/keys"], _dead_pairs(v2)


def test_the_presence_allowlist_survives_the_WHOLE_path_tsv_to_verdict():
    """🔴 END-TO-END through `parse_buckets`, the only door the live check uses.

    The 4-field legacy width is the interesting one: the presence column is
    RECONSTRUCTED from the source name there, so a mutant that reuses `n` (the
    old meaning) reinstates the overnight false alarm on every replayed capture
    while every `evaluate`-level test above stays green.
    """
    rows = workday_table(NOW_BUCKET, days=6, awake_h=10)
    lo = min(b for (_h, _s, b, *_r) in rows)
    last_awake = max(b for (_h, s, b, *_r) in rows if s == "keys")
    now = last_awake + 8 * 3600
    rows += all_night_rows("h1", "claude", lo, now)

    # 4-field: exactly what a pre-change capture or `--tsv` replay looks like.
    tsv = "".join("%s\t%s\t%d\t%d\n" % (h, s, b, n) for (h, s, b, n) in rows)
    parsed = D.parse_buckets(tsv)
    assert any(r[1] == "claude" and r[4] == 0 for r in parsed), \
        "the parser did not reconstruct the presence column from the source name"
    assert any(r[1] == "keys" and r[4] > 0 for r in parsed), \
        "the parser zeroed a HUMAN source's presence count"
    assert _dead_pairs(D.evaluate(parsed, now=now + 60)) == []

    # 5-field: the live query's width, presence computed server-side.
    tsv5 = "".join("%s\t%s\t%d\t%d\t%d\n"
                   % (h, s, b, n, n if s in ("keys", "i3") else 0)
                   for (h, s, b, n) in rows)
    assert _dead_pairs(D.evaluate(D.parse_buckets(tsv5), now=now + 60)) == []
