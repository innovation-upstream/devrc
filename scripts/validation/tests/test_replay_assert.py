"""Tests for replay ground-truth math and assert_queries comparison logic.

Neither hits a real ClickHouse: replay's ground truth is pure math, and
assert_queries is driven by a mocked client that echoes the expected values.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "collector" / "keylog"))
import replay as RP  # noqa: E402
import assert_queries as A  # noqa: E402


def test_ground_truth_matches_plan():
    plan = RP.ReplayPlan(n_commands=5, m_navs=4, nav_scroll_pct=42,
                         k_switches=3, deep_work_block_ms=4000)
    gt, spec = RP.build_ground_truth(plan, "vrun-test", "workbench",
                                     keystrokes=False, notes=[])
    # counts
    assert gt.expected_command_count == 5
    assert gt.expected_nav_count == 4
    # active_ms is RETIRED — no expected_active_ms on the ground truth anymore.
    assert not hasattr(gt, "expected_active_ms")
    # switches: the deep-work island is one app (A,A) then K distinct apps.
    # sequence apps = [A, A, S0, S1, S2] -> switches A->S0, S0->S1, S1->S2 = 3 = K
    assert gt.expected_switches == 3
    # deep-work block == the scripted gap
    assert gt.expected_deep_work_ms == 4000
    # spec has the right number of events
    assert len(spec["commands"]) == 5
    assert len(spec["navs"]) == 4
    assert len(spec["focus"]) == 2 + 3  # island(2) + K single focuses


def test_ground_truth_hour_bucket_is_local():
    plan = RP.ReplayPlan()
    gt, _ = RP.build_ground_truth(plan, "vrun-tz", "workbench",
                                  keystrokes=False, notes=[])
    # the hour bucket is derived from the local wall-clock started_local string
    assert gt.expected_hour_bucket == int(gt.started_local[11:13])


def test_keystrokes_skipped_when_headless(monkeypatch):
    monkeypatch.delenv("DISPLAY", raising=False)
    done, note = RP.replay_keystrokes("vrun-x")
    assert done is False
    assert "headless" in note.lower() and "laptop" in note.lower()


# --------------------------------------------------------------------------- #
# assert_queries against a mocked client that returns the expected values
# --------------------------------------------------------------------------- #
class EchoClient:
    """Returns each ground-truth value for the matching query, simulating a
    correct ClickHouse (assertions should all PASS)."""

    def __init__(self, gt):
        self.gt = gt

        class _Conn:
            fq_table = "activity.events"
            url = "http://fake"
        self.conn = _Conn()

    def scalar(self, sql):
        if "source = 'zsh'" in sql:
            return self.gt["expected_command_count"]
        if "source = 'browser' AND text != ''" in sql:
            return self.gt["expected_nav_count"]
        if "lagInFrame" in sql and "sum(is_switch) AS value" in sql:
            return self.gt["expected_switches"]
        if "max(run_ms)" in sql:
            return self.gt["expected_deep_work_ms"]
        return 0

    def rows(self, sql):
        if "toHour(ts)" in sql:
            return [{"hour": self.gt["expected_hour_bucket"], "events": 11}]
        return []


def _gt():
    plan = RP.ReplayPlan()
    gt, _ = RP.build_ground_truth(plan, "vrun-assert", "workbench",
                                  keystrokes=False, notes=[])
    from dataclasses import asdict
    return asdict(gt)


def test_assert_all_passes_on_correct_ch():
    gt = _gt()
    results = A.assert_all(EchoClient(gt), gt)
    names = {r.name for r in results}
    assert {"command_count", "nav_count", "app_switches",
            "deep_work_block_ms", "timezone_hour_bucket"} == names
    # active_ms_sum is RETIRED — must not be asserted anymore.
    assert "active_ms_sum" not in names
    assert all(r.passed for r in results), [(r.name, r.detail) for r in results if not r.passed]


def test_assert_catches_wrong_value():
    gt = _gt()

    class WrongClient(EchoClient):
        def scalar(self, sql):
            if "lagInFrame" in sql and "sum(is_switch) AS value" in sql:
                return 999999  # wrong switch count
            return super().scalar(sql)

    results = A.assert_all(WrongClient(gt), gt)
    sw = [r for r in results if r.name == "app_switches"][0]
    assert sw.passed is False


def test_assert_catches_timezone_bug():
    gt = _gt()

    class TZBugClient(EchoClient):
        def rows(self, sql):
            if "toHour(ts)" in sql:
                # simulate a UTC-shift bug: events land 5 hours off
                return [{"hour": (gt["expected_hour_bucket"] + 5) % 24, "events": 11}]
            return []

    results = A.assert_all(TZBugClient(gt), gt)
    tz = [r for r in results if r.name == "timezone_hour_bucket"][0]
    assert tz.passed is False
