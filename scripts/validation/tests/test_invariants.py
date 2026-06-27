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


def test_per_host_hour_cap_clean():
    rows = [
        {"host": "workbench", "hour": "2026-06-24 07:00:00", "active_ms": 60 * 60 * 1000},
        {"host": "laptop", "hour": "2026-06-24 08:00:00", "active_ms": 1234},
    ]
    assert I.eval_per_host_hour_cap(rows)[0] is True


def test_per_host_hour_cap_catches_overflow():
    rows = [
        {"host": "workbench", "hour": "2026-06-24 07:00:00", "active_ms": 90 * 60 * 1000},
    ]
    passed, detail = I.eval_per_host_hour_cap(rows)
    assert passed is False
    assert "workbench" in detail


def test_per_host_hour_cap_tolerates_small_overshoot():
    # 60min + 2% < 5% tolerance -> still PASS (boundary-straddle case)
    rows = [{"host": "h", "hour": "x", "active_ms": int(60 * 60 * 1000 * 1.02)}]
    assert I.eval_per_host_hour_cap(rows)[0] is True


def test_per_host_hour_cap_query_is_windowed():
    # The active-time HEALTH invariant must scan only a trailing window, not
    # all-time — the append-only store never rewrites bad historical hours, so an
    # all-time scan would stay RED forever after a fixed+deployed bug. Every
    # OTHER invariant must stay all-time (no such WHERE-ts window).
    invs = {inv.name: inv for inv in I.build_invariants("activity.events")}
    cap = invs["per_host_hour_active_cap"]
    assert f"INTERVAL {I.ACTIVE_CAP_WINDOW_HOURS} HOUR" in cap.sql
    assert "WHERE ts > now() -" in cap.sql
    assert I.ACTIVE_CAP_WINDOW_HOURS == 48
    # No other invariant should carry a trailing ts window (they guard immutable
    # correctness, not transient health).
    for name, inv in invs.items():
        if name == "per_host_hour_active_cap":
            continue
        assert "now() - INTERVAL" not in inv.sql, f"{name} unexpectedly windowed"


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
        if "GROUP BY host, hour" in sql:
            return [{"host": "workbench", "hour": "2026-06-24 07:00:00", "active_ms": 1000}]
        if "GROUP BY source" in sql:
            return [{"value": "zsh", "count": 5}]
        if "GROUP BY host" in sql:
            return [{"value": "workbench", "count": 5}]
        return []


def test_run_invariants_all_pass_on_clean_data():
    results = I.run_invariants(FakeClient())
    names = {r.name for r in results}
    # all the documented invariants present
    assert {"no_future_ts", "duration_ms_nonneg", "active_ms_capped",
            "ts_not_ancient", "expected_hosts", "expected_sources",
            "per_host_hour_active_cap"} <= names
    assert all(r.passed for r in results), [r.detail for r in results if not r.passed]


class FailClient(FakeClient):
    def scalar(self, sql):
        return 7  # violations everywhere

    def rows(self, sql):
        if "GROUP BY host, hour" in sql:
            return [{"host": "h", "hour": "x", "active_ms": 99 * 60 * 1000}]
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
    assert "per_host_hour_active_cap" in failed


def test_run_invariants_query_error_is_fail():
    class BoomClient(FakeClient):
        def scalar(self, sql):
            raise RuntimeError("connection refused")

    results = I.run_invariants(BoomClient())
    # the scalar-based invariants should be FAIL with the error surfaced
    boom = [r for r in results if "connection refused" in r.detail]
    assert boom and all(not r.passed for r in boom)
