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
def headered(body: str) -> str:
    """Prepend the exact header the live query (FORMAT TSVWithNames) emits."""
    return D.TSV_HEADER + "\n" + body


def test_parse_buckets_happy():
    """Header + 5 fields: the current query. The 5th is the HUMAN-PRESENCE count.

    🔴 Every number here is DISTINCT on purpose. An earlier version used
    n == n_presence (3, 3), which made "carry the presence column" and "ignore it
    and reuse n" byte-identical — a mutant that dropped the 5th field survived the
    whole suite. A fixture of equal values cannot tell two implementations apart.

    The 5th field is AUTHORITATIVE when the header is present: `i3` is a presence
    source and it still parses to 0 here, because the server computed that.
    """
    rows = D.parse_buckets(headered("h1\tkeys\t100\t7\t5\nh1\ti3\t200\t3\t0\n"))
    assert rows == [("h1", "keys", 100, 7, 5), ("h1", "i3", 200, 3, 0)]
    assert rows[1][4] == 0 and rows[1][3] == 3, \
        "the presence count must survive the parse independently of the total"


def test_parse_buckets_refuses_a_headerless_five_column_file():
    """🔴 THE FORMAT-AMBIGUITY GUARD, and the reason the query emits a header.

    The PRE-presence query was ALSO five columns wide; its 5th was `n_op`
    (operator-driven = not machine-cadence), which is ~= `n` for every source.
    Replaying such a capture would reinstate the overnight false-alarm bug with
    no error and no way to notice — the widths match and the values are
    plausible. So a headerless 5-column file must be an ERROR, not a guess.
    """
    with pytest.raises(ValueError) as e:
        D.parse_buckets("h1\tkeys\t100\t3\t3\n")
    assert "ambiguous" in str(e.value)


def test_parse_buckets_names_the_stale_n_op_header():
    """A capture from the one-commit window where the query emitted a header-less
    `n_op`... does not exist (that query had no header at all), but a hand-written
    or future `n_op` header must be refused BY NAME rather than silently accepted
    as "some 5-column thing"."""
    with pytest.raises(ValueError) as e:
        D.parse_buckets("host\tsource\tb\tn\tn_op\nh1\tkeys\t100\t3\t3\n")
    msg = str(e.value)
    assert "n_op" in msg and "PRE-PRESENCE" in msg, msg


def test_parse_buckets_rejects_an_unrecognised_header():
    with pytest.raises(ValueError):
        D.parse_buckets("host\tsource\tbucket\tn\tn_presence\nh1\tk\t1\t1\t1\n")


def test_tsv_header_matches_the_query_column_list():
    """The header the parser demands and the header the query produces are the
    SAME string, single-sourced — a parser that accepts a header the server never
    emits would reject every live response."""
    assert D.TSV_HEADER == "host\tsource\tb\tn\tn_presence"
    q = D.bucket_query()
    assert "FORMAT TSVWithNames" in q, q
    for col in D.TSV_COLUMNS:
        assert " AS %s " % col in q or q.startswith("SELECT host, source"), col


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


def test_presence_sources_are_the_four_human_driven_ones():
    """🔴 The allowlist itself, pinned as a LITERAL — not derived from the module.

    This is the one assertion that fails when a source is added to or removed from
    the set. `claude`/`tool`/`opencode`/`browser-bridge` are agent-driven and are
    named here as explicitly EXCLUDED: they add active buckets only when the
    operator is away, which is exactly when adding them manufactures a false
    alarm.

    🔴 `browser` is excluded too, and it is the subtle one — the operator really
    does drive it, but the browser-bridge AGENT drives the same Brave profile the
    activity extension instruments, so agent navigation emits `browser` rows.
    Measured on the live table: 76 of 777 laptop `browser` buckets co-occur with
    a browser-bridge command bucket, and `browser` was the sole presence source in
    exactly ONE. It bought nothing and carried a contamination path.
    """
    assert set(D.PRESENCE_SOURCES) == {"keys", "i3", "tmux", "zsh"}
    for agent in ("claude", "tool", "opencode", "browser-bridge", "browser"):
        assert agent not in D.PRESENCE_SOURCES, agent


def test_parse_buckets_rejects_a_width_that_changes_mid_file():
    """Inferring per-line would be the silent-drop failure this parser exists to
    prevent: a truncated or concatenated capture would parse as 'fewer events'
    and decay into 'no staleness'. Both directions."""
    with pytest.raises(ValueError):
        D.parse_buckets(headered("h1\tkeys\t100\t3\t3\nh1\ti3\t100\t1\n"))
    with pytest.raises(ValueError):
        D.parse_buckets("h1\tkeys\t100\t3\nh1\ti3\t100\t1\t1\n")


def test_parse_buckets_skips_blank_lines():
    assert D.parse_buckets(headered("\n\nh1\tkeys\t100\t3\t3\n\n")) == \
        [("h1", "keys", 100, 3, 3)]
    assert D.parse_buckets("\n\nh1\tkeys\t100\t3\n\n") == [("h1", "keys", 100, 3, 3)]


def test_parse_buckets_rejects_wrong_field_count():
    # A changed output format must be an ERROR, not "fewer rows" silently
    # decaying into "no staleness".
    with pytest.raises(ValueError):
        D.parse_buckets("h1\tkeys\t100\n")
    with pytest.raises(ValueError):
        D.parse_buckets("h1\tkeys\t100\t3\t3\t9\n")


def test_parse_buckets_rejects_non_integer():
    """🔴 Stays a NON-INTEGER error, not a header error. The header sniff keys on
    the first two column NAMES precisely so a junk bucket value in a data row is
    still diagnosed as junk."""
    with pytest.raises(ValueError) as e:
        D.parse_buckets("h1\tkeys\tnotanumber\t3\n")
    assert "non-integer" in str(e.value), str(e.value)


def test_bucket_query_mentions_table_and_window():
    q = D.bucket_query(days=14, database="activity", table="events")
    assert "activity.events" in q
    assert "INTERVAL 14 DAY" in q
    assert q.endswith("FORMAT TSVWithNames"), q


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


def test_the_DEATH_boundary_is_exclusive_spending_the_budget_is_not_dying():
    """🔴 The module's CORE predicate, `silent > budget`, pinned at its boundary.

    This was unpinned: a `>` -> `>=` mutant survived the whole suite at every
    commit on this branch, and it is not equivalent — it converts "has spent
    exactly its whole measured budget" into a death, which on a floor-pinned
    source is a routine two-hour lull. Flagged as pre-existing during review and
    taken here because it is one assertion and it guards the verdict everything
    else in this file is about.
    """
    # healthy_table fills every bucket, so p99 gap is 0 and the budget is the
    # floor: 24 active buckets. Spend exactly that.
    exact = [r for r in healthy_table(NOW_BUCKET)
             if not (r[1] == "keys" and r[2] > NOW_BUCKET - D.FLOOR_BUCKETS * B)]
    v = D.evaluate(exact, now=NOW_BUCKET + 60)
    rec = _pair(v, "keys")
    assert rec["budget_buckets"] == D.FLOOR_BUCKETS, rec
    assert rec["silent_active_buckets"] == D.FLOOR_BUCKETS, rec
    assert rec["dead"] is False, "spending exactly the budget is not dying"
    assert v["count"] == 0

    # One bucket more IS a death — the pair that proves the boundary is reachable
    # rather than merely asserted.
    over = [r for r in healthy_table(NOW_BUCKET)
            if not (r[1] == "keys"
                    and r[2] > NOW_BUCKET - (D.FLOOR_BUCKETS + 1) * B)]
    v2 = D.evaluate(over, now=NOW_BUCKET + 60)
    assert _pair(v2, "keys")["silent_active_buckets"] == D.FLOOR_BUCKETS + 1
    assert _pair(v2, "keys")["dead"] is True
    assert v2["count"] == 1


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
    """Rows came back but nothing was measurable — still cannot tell.

    The source is a PRESENCE one on purpose: with an agent-only source the host
    would have no human timeline at all and the verdict would be
    `presence-stalled` (also an unknown state, but a different diagnosis), so
    this fixture would stop testing the baseline path.
    """
    rows = contiguous("h1", "keys", NOW_BUCKET - 5 * B, 3)
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
    states that mean 'cannot tell', and the set is pinned as a LITERAL so adding
    a state is a deliberate edit here AND in bar-status-poll's fallback copy."""
    assert D.STATE_OK not in D.UNKNOWN_STATES
    assert set(D.UNKNOWN_STATES) == {
        "no-data", "unreachable", "query-failed", "not-configured",
        "misconfigured", "presence-stalled"}


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
    tsv = headered(
        "".join("h1\t%s\t%d\t%d\t%d\n" % (r[1], r[2], r[3], 1) for r in rows)
        + "".join("h1\t%s\t%d\t%d\t%d\n" % (r[1], r[2], r[3], 0) for r in cadence))

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
        return headered("h1\tkeys\t100\t3\t3\n")

    v = D.check(env={"CLICKHOUSE_URL": "http://x", "CLICKHOUSE_USER": "u",
                     "CLICKHOUSE_PASSWORD": "p"},
                env_file="/nonexistent", fetch=fake_fetch)
    assert v["state"] != D.STATE_QUERY_FAILED, v["detail"]
    q = seen.get("query", "")
    # 🔴 The countIf must be the expression aliased AS n_presence, not merely
    # PRESENT somewhere in the query. A substring check for "AS n_presence" is
    # satisfied by "AS n_presence_unused" — a mutant that aliased the filtered
    # count aside and put a raw count() in the n_presence position passes exactly
    # that check. The literal IN-list is spelled out here, so a query built from
    # ANY other set (the cadence pairs, an emptied allowlist, an allowlist with
    # `claude` added) fails on THIS assertion rather than somewhere downstream.
    assert re.search(
        r"countIf\(source IN \('i3', 'keys', 'tmux', 'zsh'\)\) "
        r"AS n_presence(?!\w)", q), \
        ("check() did not send the human-presence filter as the n_presence "
         "column — non-presence rows would count as the operator being at the "
         "machine: %r" % q)
    # ...and the agent sources must NOT be anywhere in the presence predicate.
    for agent in ("claude", "tool", "opencode", "browser-bridge", "browser"):
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

    # The DEFAULT is what the production call site uses.
    assert D.presence_count_expr() == \
        "countIf(source IN ('i3', 'keys', 'tmux', 'zsh'))"


def test_an_empty_allowlist_is_LOUD_not_a_confident_green():
    """🔴 An empty PRESENCE_SOURCES must never render valid SQL.

    An earlier version degraded to `0 AS n_presence` and called that "quiet, never
    falsely reassuring". Run live it produced `state=ok evaluated=13 count=0` — a
    confident green with nothing judged, and `evaluated` stayed non-zero so
    evaluate's own positive control could not catch it. There is no safe SQL for
    "active time is undefined"; every entry point must refuse.
    """
    with pytest.raises(ValueError):
        D.presence_predicate_sql(frozenset())
    with pytest.raises(ValueError):
        D.presence_count_expr(frozenset())
    with pytest.raises(ValueError):
        D.bucket_query(presence=frozenset())

    # evaluate() and check() convert it into a state that is NOT ok.
    v = D.evaluate(healthy_table(NOW_BUCKET), now=NOW_BUCKET,
                   presence=frozenset())
    assert v["state"] == D.STATE_MISCONFIGURED
    assert v["state"] in D.UNKNOWN_STATES
    assert (v["evaluated"], v["count"]) == (0, 0)

    called = []
    v2 = D.check(env={"CLICKHOUSE_URL": "http://x"}, env_file="/nonexistent",
                 fetch=lambda *a, **kw: called.append(1) or "",
                 presence=frozenset())
    assert v2["state"] == D.STATE_MISCONFIGURED, v2
    assert not called, "check() queried ClickHouse with an undefined active time"


def test_MACHINE_CADENCE_is_a_DECLARATION_and_its_renderer_is_gone():
    """🔴 WHAT THIS GUARD IS NOW, and why it was renamed.

    It used to be `test_cadence_predicate_is_exported_for_the_other_consumer`
    and it pinned `cadence_predicate_sql`'s OR-join. "The other consumer" was
    `scripts/agent-ops`, and that TUI is retired: a tree-wide grep then showed
    the function had ZERO production callers and exactly one caller in total —
    this test. A guard whose only consumer is itself proves the code compiles.
    So the renderer was DELETED (this repo's own "check USED/configured before
    hardening" lesson), and what remains is pinned for what it actually is.

    `MACHINE_CADENCE` is kept, and is NOT dead: it is the single declaration of
    which emitters are timer-driven, i.e. the stated reason the browser-bridge
    heartbeat must stay OUT of PRESENCE_SOURCES — and
    `scripts/browser-bridge/tests/test_server.py` asserts its own heartbeat is
    declared here, which makes it a live cross-module contract rather than an
    ornament.
    """
    assert ("browser-bridge", "heartbeat") in D.MACHINE_CADENCE, \
        "the browser-bridge heartbeat is no longer declared machine-cadence"
    assert D.MACHINE_CADENCE, "an emptied declaration silently declares nothing"

    # 🔴 The declaration's whole point: a machine-cadence pair may never be a
    # presence source. Both halves asserted, because "not in the set" is also
    # what an empty set says.
    for source, _kind in D.MACHINE_CADENCE:
        assert source not in D.PRESENCE_SOURCES, source
    assert D.PRESENCE_SOURCES, "PRESENCE_SOURCES is empty — see above"

    # 🔴 And it must NOT have leaked into this module's own query: two rules for
    # one active-time definition is the shape that regenerates the bug at
    # whichever site gets edited second.
    assert "kind = 'heartbeat'" not in D.bucket_query(), \
        "the bucket query is filtering on machine cadence again"

    # 🔴 REGROWTH PIN, with the reason. `cadence_predicate_sql` renders these
    # pairs into SQL and had no caller; re-exporting it "for later" puts an
    # untested `" OR ".join` back in the tree, which is exactly the copy that
    # was once AND-joined (measured: `AND NOT (A AND B)` excluded 0 of 206760
    # rows). If a second consumer really appears, re-add the function AND its
    # caller AND a seam test in the same change, and delete this assertion.
    assert not hasattr(D, "cadence_predicate_sql"), (
        "cadence_predicate_sql is back — it must arrive with the caller that "
        "needs it, not as a maybe-someday export")


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


def _crashed_desk(days=6, awake_h=10, agent_days=4):
    """An X-session crash: every presence source stops, an agent keeps emitting
    for `agent_days` afterwards. Returns (rows, now)."""
    rows = workday_table(NOW_BUCKET, days=days, awake_h=awake_h)
    lo = min(b for (_h, _s, b, *_r) in rows)
    crash = max(b for (_h, s, b, *_r) in rows if s == "keys")
    now = crash + agent_days * 86400
    return rows + all_night_rows("h1", "claude", lo, now), now


def test_a_presence_blackout_is_CANNOT_TELL_not_a_silent_ok():
    """🔴 THE COST OF THE ALLOWLIST, and the detector that keeps it observable.

    After an X-session crash no presence source emits, so active buckets stop
    accruing and EVERY source on the host measures 0 silence — including a
    genuinely dead one. Reporting `ok, 0 dead` there is the invisible green the
    module's "CANNOT TELL IS NOT HEALTHY" section forbids.

    Measured against the live table with a seeded 96h workbench presence blackout
    plus `workbench/claude` genuinely silent 74h: the pre-change rule reported 5
    dead pairs; the allowlist WITHOUT this detector reported ZERO with state=ok.
    """
    rows, now = _crashed_desk(agent_days=4)          # 96h > PRESENCE_STALL_HOURS
    v = D.evaluate(rows, now=now + 60)

    assert v["state"] == D.STATE_PRESENCE_STALLED, v["state"]
    assert v["state"] in D.UNKNOWN_STATES
    assert [s["host"] for s in v["presence_stalled"]] == ["h1"]
    assert v["presence_stalled"][0]["stall_hours"] == 96.0, v["presence_stalled"]
    # Every pair on the stalled host is marked UN-evaluated with a reason, so the
    # per-pair table cannot print a row of confident `ok`s for a host nobody can
    # judge.
    for rec in v["sources"]:
        assert rec["evaluated"] is False, rec
        assert rec["reason"] == "presence-stalled", rec
    assert v["count"] == 0

    # 🔴 NEGATIVE CONTROL for the detector: the SAME fixture with the threshold
    # lifted out of reach reproduces the blocker exactly — a silent green.
    v_bad = D.evaluate(rows, now=now + 60, stall_hours=10 ** 6)
    assert v_bad["state"] == D.STATE_OK and v_bad["count"] == 0, v_bad["state"]
    assert v_bad["presence_stalled"] == []


def test_newest_event_age_CANNOT_see_a_presence_blackout():
    """🔴 The claim an earlier docstring made, pinned as FALSE.

    `newest_event_age_minutes` is a max over the WHOLE result — every host and
    every source — so surviving agent rows hold it near zero while the human
    timeline is days stale. It is not, and never was, the mitigation.
    """
    rows, now = _crashed_desk(agent_days=4)
    v = D.evaluate(rows, now=now + 60)
    assert v["newest_event_age_minutes"] < 5, v["newest_event_age_minutes"]
    assert v["presence_stalled"][0]["stall_hours"] > 24, \
        "the fixture no longer has a stale human timeline to hide"


def test_a_source_that_stops_DURING_a_stall_is_unjudgeable_not_ok():
    """A source that stopped AFTER the presence timeline froze has silence of 0
    BY CONSTRUCTION — no active buckets have accrued since. It must not read
    `ok`, and it cannot honestly read DEAD either, so it is un-evaluated."""
    rows, now = _crashed_desk(agent_days=5)
    # `claude` stops 24h before now, i.e. 96h AFTER the crash — well inside the
    # stall, so nothing real was ever measured against it.
    rows = [r for r in rows if not (r[1] == "claude" and r[2] > now - 86400)]
    v = D.evaluate(rows, now=now + 60)
    rec = _pair(v, "claude")
    assert rec["evaluated"] is False and rec["reason"] == "presence-stalled", rec
    assert rec["dead"] is False, rec
    assert rec["silent_active_buckets"] == 0, \
        "the fixture no longer exercises the 0-by-construction case"
    assert v["state"] == D.STATE_PRESENCE_STALLED


def test_a_death_that_PREDATES_the_stall_is_still_reported_by_name():
    """🔴 REGRESSION TEST. A per-HOST discriminator discarded this death.

    A pair that stopped BEFORE the presence timeline froze accrued its silence
    against REAL active buckets. That measurement is genuine and already
    complete, so "cannot tell about the host" must not erase "this source is
    dead". Measured on the live table (96h blackout, workbench/claude dead 200h)
    the discarded verdict was 54.58 silent ACTIVE hours against a 2.0h budget —
    27x over — dropped from `dead` entirely, taking `tlm 1` + a dunst toast down
    to `tlm ?` with the source's name nowhere in the payload.
    """
    rows = workday_table(NOW_BUCKET, days=6, awake_h=10)      # keys + i3
    lo = min(b for (_h, _s, b, *_r) in rows)
    crash = max(b for (_h, s, b, *_r) in rows if s == "keys")
    # `claude` emits from the start but STOPS two workdays before the crash, so
    # it is silent for ~20 real active hours by the time the desk dies.
    rows += all_night_rows("h1", "claude", lo, crash - 2 * 86400)
    now = crash + 4 * 86400                                   # 96h stall
    rows += all_night_rows("h1", "tool", lo, now)             # keeps the host up

    v = D.evaluate(rows, now=now + 60)
    assert v["state"] == D.STATE_PRESENCE_STALLED, v["state"]
    assert [s["host"] for s in v["presence_stalled"]] == ["h1"]

    rec = _pair(v, "claude")
    assert rec["evaluated"] is True, rec
    assert rec["dead"] is True, rec
    assert rec["silent_active_buckets"] > 0, \
        "the death must have been measured against REAL active buckets"
    # ...and it survives into the payload the consumers read.
    assert _dead_pairs(v) == ["h1/claude"], _dead_pairs(v)
    assert v["count"] == 1
    assert "h1/claude" in v["detail"], v["detail"]

    # The pairs that are genuinely unjudgeable still are — the fix is per-PAIR,
    # not "stop marking anything".
    assert _pair(v, "tool")["reason"] == "presence-stalled", _pair(v, "tool")
    assert _pair(v, "keys")["reason"] == "presence-stalled", _pair(v, "keys")


def test_the_stall_threshold_boundary_is_EXCLUSIVE():
    """A gap of exactly PRESENCE_STALL_HOURS is not a stall; one bucket more is.

    72h is an exact multiple of the 300s bucket, so `gap == stall_seconds` is a
    REACHABLE state, not a theoretical one — a `>` vs `>=` mutant survived until
    this existed.
    """
    rows = workday_table(NOW_BUCKET, days=6, awake_h=10)
    lo = min(b for (_h, _s, b, *_r) in rows)
    crash = max(b for (_h, s, b, *_r) in rows if s == "keys")

    exact = rows + all_night_rows("h1", "claude", lo,
                                  crash + int(D.PRESENCE_STALL_HOURS * 3600))
    v_exact = D.evaluate(exact, now=crash + 10 ** 6)
    assert v_exact["presence_stalled"] == [], v_exact["presence_stalled"]
    assert v_exact["state"] == D.STATE_OK

    over = rows + all_night_rows("h1", "claude", lo,
                                 crash + int(D.PRESENCE_STALL_HOURS * 3600) + B)
    v_over = D.evaluate(over, now=crash + 10 ** 6)
    assert [s["host"] for s in v_over["presence_stalled"]] == ["h1"]


def test_an_ordinary_away_from_desk_stretch_does_NOT_stall():
    """ALERT-FATIGUE GUARD for the detector. The operator being away for a day
    while agents run is normal — measured on the live 14-day table, the worst
    real presence stall on either host was 39.8 wall hours, with ZERO points over
    48h. A 24h stall must stay `ok`."""
    rows, now = _crashed_desk(agent_days=1)          # 24h < PRESENCE_STALL_HOURS
    v = D.evaluate(rows, now=now + 60)
    assert v["state"] == D.STATE_OK, v["state"]
    assert v["presence_stalled"] == []
    assert _pair(v, "keys")["evaluated"] is True


def test_a_host_that_is_simply_SWITCHED_OFF_does_not_stall():
    """🔴 Why the predicate compares the host's OWN two timestamps and not `now`.

    A laptop that is shut emits nothing at all, so BOTH timestamps stop advancing
    and the gap FREEZES at whatever it was at power-off — it does not keep
    growing. (Not "the two timestamps move together": that phrasing was wrong and
    is retracted; measured on the live table, the workbench froze at a 38.75h gap
    and held that exact value for the 4.6h it stayed down.) Here the gap froze
    small, so the host stays quiet however long it is off, which is the point —
    `now - last_presence` would scream every weekend.
    """
    rows = workday_table(NOW_BUCKET, days=6, awake_h=10)
    last = max(b for (_h, _s, b, *_r) in rows)
    v = D.evaluate(rows, now=last + 30 * 86400)      # a month later, host off
    assert v["presence_stalled"] == []
    assert v["state"] == D.STATE_OK
    assert v["newest_event_age_minutes"] > 29 * 24 * 60


def test_a_host_powered_off_ALREADY_past_the_threshold_stays_stalled():
    """The other side of the freeze, and the honest cost of it: a host that goes
    down while its gap is ALREADY over the threshold keeps that gap forever, so
    it stays `presence-stalled` for the rest of the window rather than ageing
    out. That is not a false positive — it really was unjudgeable when it went
    down, and nothing since has made it judgeable — but it does mean the
    condition can outlive the machine, and the docstring says so.

    🔴 CHARACTERISATION, not regression coverage: this passes at the previous
    commit too. It exists because the consequence was newly *stated* and an
    unasserted claim in a docstring is the thing this repo keeps being bitten by,
    not because the behaviour changed.
    """
    rows, now = _crashed_desk(agent_days=4)          # 96h gap at power-off
    v_at_death = D.evaluate(rows, now=now + 60)
    assert v_at_death["presence_stalled"][0]["stall_hours"] == 96.0

    # 30 days later, not one further row from the host.
    v_later = D.evaluate(rows, now=now + 30 * 86400)
    assert v_later["state"] == D.STATE_PRESENCE_STALLED
    assert v_later["presence_stalled"][0]["stall_hours"] == 96.0, \
        "the frozen gap drifted — the predicate is reading a clock again"


def test_a_host_with_NO_presence_rows_is_skipped_not_alarmed():
    """🔴 REGRESSION TEST for a permanently-red gate.

    A host that has NEVER emitted a human-driven row in the window has no normal
    to compare against — it is the "expected-present is MEASURED, not declared"
    case, not a change. It must be marked un-evaluated (never a silent `ok`) and
    must raise NOTHING at the fleet level.

    It used to be folded into `presence-stalled` with no threshold and no
    minimum-evidence requirement, so ONE row from an agent pod, a container or a
    mis-set ACTIVITY_HOST flipped the whole fleet to cannot-tell and zeroed the
    other hosts' real dead count, for the full 14 days until it rolled out of the
    window. Measured on the live table: a single synthetic ("agentpod","claude")
    row took the fleet from `state=ok count=1` to `state=presence-stalled
    count=1`, and through the bar from `tlm 1` to `tlm ?` with the toast
    suppressed.
    """
    # A healthy host, a real death on it, and an agent-only host alongside.
    rows = workday_source("h1", "keys", NOW_BUCKET, days=6, awake_h=10,
                          skip_last=1)
    rows += workday_source("h1", "i3", NOW_BUCKET, days=6, awake_h=10)
    now = max(b for (_h, _s, b, *_r) in rows) + 60
    rows += [("agentpod", "claude", NOW_BUCKET - 600, 5)]

    v = D.evaluate(rows, now=now)
    assert v["presence_stalled"] == [], v["presence_stalled"]
    assert v["state"] == D.STATE_OK, v["state"]
    # The healthy host's real death survives untouched — this is the masking.
    assert _dead_pairs(v) == ["h1/keys"], _dead_pairs(v)
    assert v["count"] == 1
    # ...and the agent-only host is VISIBLE as unjudged, never a confident ok.
    rec = _pair(v, "claude", host="agentpod")
    assert rec["evaluated"] is False, rec
    assert rec["reason"] == "no-presence-baseline", rec
    assert rec["dead"] is False, rec


def test_a_host_with_NO_presence_rows_ALONE_is_still_cannot_tell():
    """The other half: when the agent-only host is the ONLY one, nothing was
    measurable at all, so the verdict must be an unknown state — `no-data`, the
    module's existing "we cannot prove we measured anything" answer — never
    `ok`."""
    rows = [("h2", "claude", NOW_BUCKET - i * B, 1) for i in range(400)]
    v = D.evaluate(rows, now=NOW_BUCKET + 60)
    assert v["state"] == D.STATE_NO_DATA, v["state"]
    assert v["state"] in D.UNKNOWN_STATES
    assert v["evaluated"] == 0
    assert all(s["reason"] == "no-presence-baseline" for s in v["sources"]), \
        v["sources"]


def test_one_host_stalling_does_not_erase_the_other_hosts_findings():
    """A stalled host makes the STATE cannot-tell, but the dead pairs found on a
    healthy host stay in the payload and in the detail string — the bar zeroes the
    count for any unknown state, so the detail is the only place the operator can
    read both facts."""
    rows, now = _crashed_desk(agent_days=4)
    healthy = workday_source("h2", "keys", NOW_BUCKET, days=6, awake_h=10)
    healthy += workday_source("h2", "i3", NOW_BUCKET, days=6, awake_h=10,
                              skip_last=1)
    v = D.evaluate(rows + healthy, now=now + 60)
    assert v["state"] == D.STATE_PRESENCE_STALLED
    assert [s["host"] for s in v["presence_stalled"]] == ["h1"]
    assert _dead_pairs(v) == ["h2/i3"], _dead_pairs(v)
    assert "h1" in v["detail"] and "h2/i3" in v["detail"], v["detail"]


def test_losing_only_ONE_presence_source_is_still_caught():
    """The contrast that makes the blackout a cost and not a bug: the remaining
    presence sources carry the timeline, so a single one dying still alarms —
    which is the whole motivating case."""
    partial = workday_source("h1", "keys", NOW_BUCKET, days=6, awake_h=10,
                             skip_last=1)
    partial += workday_source("h1", "i3", NOW_BUCKET, days=6, awake_h=10)
    v = D.evaluate(partial, now=max(b for (_h, _s, b, *_r) in partial) + 60)
    assert v["state"] == D.STATE_OK
    assert _dead_pairs(v) == ["h1/keys"], _dead_pairs(v)


def test_the_presence_parameter_is_wired_through_EVERY_stage():
    """🔴 `check(presence=...)` was once a SILENT NO-OP: the query was built from
    the module default (so the server computed the default's presence column) and
    the parser's own copy never fired because live rows are 5-wide. A maintainer
    previewing "what if I add this source" got an unchanged answer, no error.

    So all three consumers are asserted through the ONE public entry point, using
    a set that is NOT the default and that changes the verdict.
    """
    seen = {}
    # `keys` stops FOUR days early. With the default allowlist, `i3` carries the
    # timeline and `keys` is DEAD. With an allowlist of ONLY `keys`, nothing is
    # active after it stops, so the host's human timeline is frozen 96h and it
    # cannot be judged at all.
    rows = workday_source("h1", "keys", NOW_BUCKET, days=6, awake_h=10,
                          skip_last=4)
    rows += workday_source("h1", "i3", NOW_BUCKET, days=6, awake_h=10)
    body = "".join("%s\t%s\t%d\t%d\n" % r for r in rows)      # 4-wide, no header
    now = max(b for (_h, _s, b, *_r) in rows) + 60

    def fake_fetch(url, user, password, query, timeout):
        seen.setdefault("queries", []).append(query)
        return body

    env = {"CLICKHOUSE_URL": "http://x"}
    default = D.check(env=env, env_file="/nonexistent", fetch=fake_fetch, now=now)
    keysonly = D.check(env=env, env_file="/nonexistent", fetch=fake_fetch,
                       now=now, presence=frozenset({"keys"}))

    # 1. the QUERY differs — the SQL is built from the argument, not the default
    assert "countIf(source IN ('i3', 'keys', 'tmux', 'zsh'))" in seen["queries"][0]
    assert "countIf(source IN ('keys'))" in seen["queries"][1], seen["queries"][1]
    # 2. the PARSE differs — the 4-wide reconstruction uses the argument, so `i3`
    #    stops contributing presence and the verdict changes
    assert _dead_pairs(default) == ["h1/keys"], _dead_pairs(default)
    assert _dead_pairs(keysonly) == [], _dead_pairs(keysonly)
    # 3. and EVALUATE saw it too: with only `keys` allowed, the host's human
    #    timeline is frozen from the moment `keys` stopped.
    assert keysonly["state"] == D.STATE_PRESENCE_STALLED, keysonly["state"]


def test_check_forwards_presence_to_evaluate_even_though_it_is_currently_MOOT(
        monkeypatch):
    """🔴 A STRUCTURAL guard, labelled as one, and here is why it has to be.

    A mutant deleting `presence=presence` from check()'s evaluate() call SURVIVED
    the whole suite, and the reason is not a missing test — it is that the mutant
    is behaviourally EQUIVALENT through check() today. By the time evaluate runs,
    `parse_buckets` has already applied the allowlist and every row is 5-wide, so
    evaluate's own copy of the membership test cannot fire; and an empty allowlist
    is refused by bucket_query before evaluate is ever reached.

    The second assertion MEASURES that equivalence rather than asserting it, so
    this test says something true today and starts failing the moment evaluate
    grows a use of `presence` that a parsed row can reach — which is exactly when
    a missing forward would become a real bug.
    """
    rows = workday_table(NOW_BUCKET, days=6, awake_h=10)
    body = "".join("%s\t%s\t%d\t%d\n" % r for r in rows)
    custom = frozenset({"keys", "i3", "tmux"})
    seen = {}
    real_evaluate = D.evaluate

    def spy(rows_, **kw):
        seen.update(kw)
        return real_evaluate(rows_, **kw)

    monkeypatch.setattr(D, "evaluate", spy)
    D.check(env={"CLICKHOUSE_URL": "http://x"}, env_file="/nonexistent",
            fetch=lambda *a, **kw: body, now=NOW_BUCKET + 60, presence=custom)
    assert seen.get("presence") == custom, seen

    # ...and the measured "currently moot" claim: on ALREADY-PARSED rows, the
    # argument changes nothing, which is what makes the guard above structural.
    parsed = D.parse_buckets(body, presence=custom)
    a = real_evaluate(parsed, now=NOW_BUCKET + 60, presence=custom)
    b = real_evaluate(parsed, now=NOW_BUCKET + 60,
                      presence=frozenset({"keys", "i3", "tmux", "zsh"}))
    assert (a["state"], a["count"], _dead_pairs(a)) == \
           (b["state"], b["count"], _dead_pairs(b)), \
        ("evaluate's `presence` now CHANGES a parsed-row verdict — the guard "
         "above is no longer merely structural, and check() dropping the "
         "forward would be a real bug. Replace this with a behavioural test.")


def test_evaluate_and_parse_honour_a_custom_presence_set_directly():
    """The same parameter at the two lower-level entry points, pinned with
    literals — `check` above could pass while both of these ignored it."""
    assert D.parse_buckets("h1\tclaude\t100\t9\n",
                           presence=frozenset({"claude"})) == \
        [("h1", "claude", 100, 9, 9)]
    assert D.parse_buckets("h1\tkeys\t100\t9\n",
                           presence=frozenset({"claude"})) == \
        [("h1", "keys", 100, 9, 0)]

    # evaluate: `claude` alone marks the night active, so the human sources blow
    # the floor — the OLD bug, reproduced purely by widening the argument.
    rows = workday_table(NOW_BUCKET, days=6, awake_h=10)
    lo = min(b for (_h, _s, b, *_r) in rows)
    last_awake = max(b for (_h, s, b, *_r) in rows if s == "keys")
    now = last_awake + 8 * 3600
    rows += all_night_rows("h1", "claude", lo, now)
    wide = D.evaluate(rows, now=now + 60,
                      presence=frozenset({"keys", "i3", "claude"}))
    assert "h1/keys" in _dead_pairs(wide), _dead_pairs(wide)


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
    tsv5 = headered("".join("%s\t%s\t%d\t%d\t%d\n"
                            % (h, s, b, n, n if s in ("keys", "i3") else 0)
                            for (h, s, b, n) in rows))
    assert _dead_pairs(D.evaluate(D.parse_buckets(tsv5), now=now + 60)) == []
