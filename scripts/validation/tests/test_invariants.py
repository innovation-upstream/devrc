"""Unit tests for the invariant evaluators.

Feed synthetic results that violate / satisfy each invariant and assert the
evaluator catches it. The SQL itself is exercised live by validate.py; here we
test the pure PASS/FAIL logic and that run_invariants wires evaluators to the
right query shape via a mocked client.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import invariants as I  # noqa: E402


def test_zero_violations_evaluator():
    ev = I.eval_zero_violations("future ts")
    assert ev(0)[0] is True
    assert ev(3)[0] is False
    assert "3" in ev(3)[1]
    assert ev(None)[0] is True  # null count treated as 0


def test_unexpected_set_evaluator_clean():
    ev = I.eval_unexpected_set(I.EXPECTED_SOURCES, "source")
    rows = [{"value": "zsh", "count": 10}, {"value": "browser", "count": 5},
            {"value": "claude", "count": 2}, {"value": "i3", "count": 7}]
    passed, detail = ev(rows)
    assert passed is True


def test_i3_is_an_expected_source():
    # The i3 focus collector is a first-class source; once its events land the
    # expected_sources invariant must not flag them.
    assert "i3" in I.EXPECTED_SOURCES
    ev = I.eval_unexpected_set(I.EXPECTED_SOURCES, "source")
    assert ev([{"value": "i3", "count": 3}])[0] is True


def test_unexpected_set_evaluator_catches_bad():
    ev = I.eval_unexpected_set(I.EXPECTED_SOURCES, "source")
    rows = [{"value": "zsh", "count": 10}, {"value": "wibble", "count": 1}]
    passed, detail = ev(rows)
    assert passed is False
    assert "wibble" in detail


def test_unexpected_set_ignores_empty_value():
    ev = I.eval_unexpected_set(I.EXPECTED_HOSTS, "host")
    rows = [{"value": "workbench", "count": 9}, {"value": "", "count": 1},
            {"value": None, "count": 1}]
    assert ev(rows)[0] is True


# --------------------------------------------------------------------------- #
# Derived-attention consistency (the replacement guard for the retired active_ms
# invariants — see the RETIRED METRIC note in invariants.py).
# --------------------------------------------------------------------------- #
def test_derived_attention_clean():
    # sum-per-domain <= brave dwell and max-domain <= wall-clock -> PASS.
    rows = [{"derived_total_ms": 18_000_000, "brave_dwell_ms": 20_000_000,
             "max_domain_ms": 3_000_000, "wallclock_ms": 172_800_000}]
    passed, detail = I.eval_derived_attention(rows)
    assert passed is True, detail


def test_derived_attention_catches_sum_over_dwell():
    # The intersection can NEVER exceed total i3 Brave dwell — this is the core
    # structural property that the broken active_ms metric violated (20x inflated).
    rows = [{"derived_total_ms": 40_000_000, "brave_dwell_ms": 20_000_000,
             "max_domain_ms": 5_000_000, "wallclock_ms": 172_800_000}]
    passed, detail = I.eval_derived_attention(rows)
    assert passed is False
    assert "Brave dwell" in detail


def test_derived_attention_catches_domain_over_wallclock():
    rows = [{"derived_total_ms": 1000, "brave_dwell_ms": 1000,
             "max_domain_ms": 200_000_000, "wallclock_ms": 172_800_000}]
    passed, detail = I.eval_derived_attention(rows)
    assert passed is False
    assert "wall-clock" in detail


def test_derived_attention_tolerates_small_overshoot():
    # sum at +1% (< 2% tolerance) for boundary-straddle/rounding -> PASS.
    dwell = 20_000_000
    rows = [{"derived_total_ms": int(dwell * 1.01), "brave_dwell_ms": dwell,
             "max_domain_ms": 1000, "wallclock_ms": 172_800_000}]
    assert I.eval_derived_attention(rows)[0] is True


def test_derived_attention_vacuous_when_no_data():
    # Headless host / empty window: no rows -> vacuously consistent.
    assert I.eval_derived_attention([])[0] is True
    assert I.eval_derived_attention(
        [{"derived_total_ms": 0, "brave_dwell_ms": 0,
          "max_domain_ms": 0, "wallclock_ms": 172_800_000}])[0] is True


def test_derived_attention_query_is_windowed_and_brave_only():
    # The derived-attention check is trailing-windowed (current-health) and
    # restricted to Brave + the laptop GUI host. Every OTHER invariant stays
    # all-time (they guard immutable correctness, not transient health).
    invs = {inv.name: inv for inv in I.build_invariants("activity.events")}
    da = invs["derived_attention_consistent"]
    assert f"INTERVAL {I.DERIVED_ATTENTION_WINDOW_HOURS} HOUR" in da.sql
    assert "now() - INTERVAL" in da.sql
    assert "Brave-browser" in da.sql
    assert "host = 'laptop'" in da.sql
    assert I.DERIVED_ATTENTION_WINDOW_HOURS == 48
    # derived_attention_consistent and session_summary_no_orphans are the only
    # legitimately-windowed invariants (current-health / settled-grace); every
    # other guards immutable, all-time correctness.
    windowed_ok = {"derived_attention_consistent", "session_summary_no_orphans"}
    for name, inv in invs.items():
        if name in windowed_ok:
            continue
        assert "now() - INTERVAL" not in inv.sql, f"{name} unexpectedly windowed"


def test_retired_active_ms_invariants_are_gone():
    # The broken active_ms metric is RETIRED — its guards must no longer exist.
    names = {inv.name for inv in I.build_invariants("activity.events")}
    assert "active_ms_capped" not in names
    assert "per_host_hour_active_cap" not in names
    assert not hasattr(I, "ACTIVE_MS_CAP")
    assert not hasattr(I, "ACTIVE_CAP_WINDOW_HOURS")
    assert not hasattr(I, "eval_per_host_hour_cap")


# --------------------------------------------------------------------------- #
# Layer-A session-summary invariants (session-tailer.py)
# --------------------------------------------------------------------------- #
def test_session_summary_invariants_registered():
    names = {inv.name for inv in I.build_invariants("activity.events")}
    assert "session_summary_wellformed" in names
    assert "session_summary_no_orphans" in names


def test_session_summary_wellformed_sql_checks_required_keys():
    sql = I.session_summary_wellformed_sql("activity.events")
    assert "kind='session-summary'" in sql
    assert "NOT (" in sql
    for key in I.SUMMARY_REQUIRED_KEYS:
        assert f"JSONHas(toString(payload),'{key}')" in sql
    # all-time (guards immutable correctness) — NOT trailing-windowed
    assert "now() - INTERVAL" not in sql
    assert "unreadable" in I.SUMMARY_REQUIRED_KEYS


def test_session_summary_orphans_sql_is_settled_and_layer_a_scoped():
    sql = I.session_summary_orphans_sql("activity.events")
    # settled-grace window + Layer-A-era gate (vacuous before the first summary)
    assert f"INTERVAL {I.SUMMARY_ORPHAN_GRACE_HOURS} HOUR" in sql
    assert "first_summary" in sql
    assert "last_ts >= first_summary" in sql
    assert "kind IN ('prompt','command')" in sql
    assert "NOT IN (SELECT session" in sql


def test_session_summary_invariants_use_zero_violations_evaluator():
    invs = {inv.name: inv for inv in I.build_invariants("activity.events")}
    # 0 violations -> PASS, N>0 -> FAIL, NULL -> PASS (vacuous)
    for name in ("session_summary_wellformed", "session_summary_no_orphans"):
        ev = invs[name].evaluate
        assert ev(0)[0] is True
        assert ev(4)[0] is False
        assert ev(None)[0] is True


# --------------------------------------------------------------------------- #
# run_invariants wiring (mocked client)
# --------------------------------------------------------------------------- #
class FakeClient:
    """Returns clean results for every invariant query."""
    class _Conn:
        fq_table = "activity.events"
        url = "http://fake"
    conn = _Conn()

    def scalar(self, sql):
        return 0  # no violations

    def rows(self, sql):
        if "per_domain" in sql:  # derived_attention_consistent
            return [{"derived_total_ms": 18_000_000, "brave_dwell_ms": 20_000_000,
                     "max_domain_ms": 3_000_000, "wallclock_ms": 172_800_000}]
        if "GROUP BY source" in sql:
            return [{"value": "zsh", "count": 5}]
        if "GROUP BY host" in sql:
            return [{"value": "workbench", "count": 5}]
        return []


def test_run_invariants_all_pass_on_clean_data():
    results = I.run_invariants(FakeClient())
    names = {r.name for r in results}
    # all the documented invariants present (active_ms guards retired)
    assert {"no_future_ts", "duration_ms_nonneg", "duration_ms_capped",
            "ts_not_ancient", "expected_hosts", "expected_sources",
            "derived_attention_consistent"} <= names
    assert all(r.passed for r in results), [r.detail for r in results if not r.passed]


class FailClient(FakeClient):
    def scalar(self, sql):
        return 7  # violations everywhere

    def rows(self, sql):
        if "per_domain" in sql:  # derived attention inconsistent (sum > dwell)
            return [{"derived_total_ms": 40_000_000, "brave_dwell_ms": 20_000_000,
                     "max_domain_ms": 5_000_000, "wallclock_ms": 172_800_000}]
        if "GROUP BY source" in sql:
            return [{"value": "evil-source", "count": 1}]
        if "GROUP BY host" in sql:
            return [{"value": "evil-host", "count": 1}]
        return []


def test_run_invariants_catches_violations():
    results = I.run_invariants(FailClient())
    failed = {r.name for r in results if not r.passed}
    assert "no_future_ts" in failed
    assert "expected_sources" in failed
    assert "expected_hosts" in failed
    assert "derived_attention_consistent" in failed


def test_run_invariants_query_error_is_fail():
    class BoomClient(FakeClient):
        def scalar(self, sql):
            raise RuntimeError("connection refused")

    results = I.run_invariants(BoomClient())
    # the scalar-based invariants should be FAIL with the error surfaced
    boom = [r for r in results if "connection refused" in r.detail]
    assert boom and all(not r.passed for r in boom)
