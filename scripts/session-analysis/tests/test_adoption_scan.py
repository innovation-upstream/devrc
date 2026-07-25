"""Unit tests for adoption-scan.py — PURE aggregation + graceful degrade.

No live ClickHouse: fake `activity.events` rows are injected through a FakeClient
(the same style as test_insights.py) and the pure aggregators are exercised
directly. Covers per-item invocation counts, outcome breakdown, week-over-week
trend, the DEAD flag (fires at 0 uses, and in the sub-window), the friction
trend, --json shape, and telemetry-off degradation.
"""
import datetime as _dt
import importlib.util
import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
SCRIPT = HERE.parent / "adoption-scan.py"
sys.path.insert(0, str(HERE.parent.parent / "validation"))
_spec = importlib.util.spec_from_file_location("adoption_scan", SCRIPT)
A = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(A)

DAY = 86400
NOW = 1_700_000_000.0


def _ts(age_days):
    """A ClickHouse/emit UTC ts string `age_days` before NOW."""
    dt = _dt.datetime.fromtimestamp(NOW - age_days * DAY, _dt.timezone.utc)
    return dt.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def _tool_row(tool, outcome, age_days):
    return {"tool": tool, "text": tool, "exit_code": 0, "duration_ms": 5,
            "ts": _ts(age_days),
            "payload": json.dumps({"tool": tool, "outcome": outcome})}


# --------------------------------------------------------------------------- #
# pure helpers
# --------------------------------------------------------------------------- #
def test_ch_ts_to_epoch_roundtrip():
    e = A.ch_ts_to_epoch(_ts(0))
    assert abs(e - NOW) < 1.0
    assert A.ch_ts_to_epoch("garbage") is None


def test_half_bucketing():
    half = 7 * DAY
    assert A._half(NOW - 1 * DAY, NOW, half) == "recent"
    assert A._half(NOW - 10 * DAY, NOW, half) == "prior"
    assert A._half(NOW - 30 * DAY, NOW, half) is None


# --------------------------------------------------------------------------- #
# aggregate_tool
# --------------------------------------------------------------------------- #
def test_aggregate_tool_counts_outcomes_and_trend():
    rows = [
        _tool_row("verify-agent-work", "pass", 1),
        _tool_row("verify-agent-work", "fail", 1),
        _tool_row("verify-agent-work", "pass", 10),
        _tool_row("obs-read", "ok", 1),           # different tool -> ignored
    ]
    agg = A.aggregate_tool(rows, "verify-agent-work", NOW, 14 * DAY)
    assert agg["total"] == 3
    assert agg["outcomes"] == {"pass": 2, "fail": 1}
    assert agg["recent"] == 2 and agg["prior"] == 1
    assert agg["trend"] == "up"


def test_aggregate_tool_excludes_rows_outside_window():
    # A wider dead-window fetch can return older rows; counts stay scoped to win.
    rows = [_tool_row("obs-read", "ok", 3), _tool_row("obs-read", "ok", 20)]
    agg = A.aggregate_tool(rows, "obs-read", NOW, 14 * DAY)
    assert agg["total"] == 1  # the 20-day-old row is excluded from counts


def test_aggregate_command_prefix_match():
    rows = [
        {"text": "/verify-agent .", "ts": _ts(1)},
        {"text": "/verify-agent", "ts": _ts(10)},
        {"text": "/obs-read foo", "ts": _ts(1)},   # different prefix -> ignored
    ]
    agg = A.aggregate_command(rows, ["/verify-agent"], NOW, 14 * DAY)
    assert agg["total"] == 2 and agg["recent"] == 1 and agg["prior"] == 1
    assert agg["trend"] == "flat"


def test_command_matches_is_tight():
    # A near-miss command must NOT be counted as the tracked one.
    assert A.command_matches("/obs-read", ["/obs-read"]) is True
    assert A.command_matches("/obs-read foo", ["/obs-read"]) is True
    assert A.command_matches("/obs-read-foo", ["/obs-read"]) is False
    assert A.command_matches("/obs-readnow", ["/obs-read"]) is False


def test_aggregate_command_does_not_subsume_suffix():
    rows = [{"text": "/obs-read-foo bar", "ts": _ts(1)},
            {"text": "/obs-read real", "ts": _ts(1)}]
    agg = A.aggregate_command(rows, ["/obs-read"], NOW, 14 * DAY)
    assert agg["total"] == 1  # only the genuine /obs-read counts


def test_aggregate_cwd_counts_sessions():
    rows = [{"session": "s1", "ts": _ts(1)}, {"session": "s2", "ts": _ts(10)}]
    agg = A.aggregate_cwd(rows, NOW, 14 * DAY)
    assert agg["total"] == 2 and agg["recent"] == 1 and agg["prior"] == 1


def test_aggregate_friction_two_windows():
    rows = [
        {"session": "a", "ts": _ts(1),
         "payload": json.dumps({"friction_counts": {"permission_block": 2, "wrong_approach": 1}})},
        {"session": "b", "ts": _ts(10),
         "payload": json.dumps({"friction_counts": {"permission_block": 5}})},
        {"session": "c", "ts": _ts(2), "payload": json.dumps({"unreadable": True})},
    ]
    fr = A.aggregate_friction(rows, NOW, 14 * DAY)
    assert fr["recent"] == {"permission_block": 2, "wrong_approach": 1}
    assert fr["prior"] == {"permission_block": 5, "wrong_approach": 0}
    assert fr["deltas"]["permission_block"] == -3
    assert fr["deltas"]["wrong_approach"] == 1
    assert fr["unreadable"] == 1
    assert fr["sessions_recent"] == 1 and fr["sessions_prior"] == 1


# --------------------------------------------------------------------------- #
# gather() end-to-end via a fake client
# --------------------------------------------------------------------------- #
class FakeClient:
    def __init__(self, tool_rows, cmd_rows, insight_rows, cwd_rows):
        self._tool, self._cmd, self._ins, self._cwd = (
            tool_rows, cmd_rows, insight_rows, cwd_rows)

    def rows(self, sql):
        if "kind='invocation'" in sql:
            return self._tool
        if "session-insight" in sql:
            return self._ins
        if "position(lower(cwd)" in sql:
            return self._cwd
        return self._cmd


def _sample_client():
    tool_rows = [
        _tool_row("verify-agent-work", "pass", 1),
        _tool_row("verify-agent-work", "fail", 1),
        _tool_row("verify-agent-work", "pass", 10),
        _tool_row("obs-read", "matched-nothing", 10),  # PRIOR-only (sub-window DEAD)
        # ticket-status + playwright-nixos absent -> DEAD
    ]
    cmd_rows = [{"text": "/verify-agent go", "ts": _ts(1)}]  # /obs-read absent -> DEAD
    insight_rows = [
        {"session": "a", "ts": _ts(1),
         "payload": json.dumps({"friction_counts": {"permission_block": 2, "wrong_approach": 1}})},
        {"session": "b", "ts": _ts(10),
         "payload": json.dumps({"friction_counts": {"permission_block": 5}})},
    ]
    cwd_rows = [{"session": "d1", "ts": _ts(1)}]
    return FakeClient(tool_rows, cmd_rows, insight_rows, cwd_rows)


def _by_id(data):
    return {it["id"]: it for it in data["items"]}


def test_gather_per_item_counts_and_outcomes():
    data = A.gather(_sample_client(), days=14, insight_days=30, dead_days=14, now=NOW)
    items = _by_id(data)
    vaw = items["verify-agent-work"]
    assert vaw["total"] == 3
    assert vaw["outcomes"] == {"pass": 2, "fail": 1}
    assert vaw["trend"] == "up"
    # impact = false-greens caught (fail+incomplete)
    assert vaw["impact"] == {"fail": 1, "incomplete": 0}
    assert items["cmd:verify-agent"]["total"] == 1
    assert items["task-spec-drafter"]["total"] == 1


def test_gather_dead_flag_fires_at_zero_and_not_when_used():
    data = A.gather(_sample_client(), days=14, insight_days=30, dead_days=14, now=NOW)
    items = _by_id(data)
    assert items["ticket-status"]["dead"] is True       # 0 uses
    assert items["playwright-nixos"]["dead"] is True     # 0 uses
    assert items["cmd:obs-read"]["dead"] is True          # opt-in, 0 uses
    assert items["verify-agent-work"]["dead"] is False   # used
    # obs-read has a PRIOR-only use -> alive under the full window...
    assert items["obs-read"]["dead"] is False
    assert items["obs-read"]["total"] == 1


def test_gather_dead_subwindow():
    # ...but DEAD under a 3-day dead window (its only use was 10 days ago).
    data = A.gather(_sample_client(), days=14, insight_days=30, dead_days=3, now=NOW)
    items = _by_id(data)
    assert items["obs-read"]["dead"] is True
    assert items["verify-agent-work"]["dead"] is False  # recent use


def test_gather_dead_days_larger_than_days_is_honest():
    """A tool used 20d ago: DEAD over 14 days, but NOT dead when --dead-days=25
    (the fetch must span the wider dead window, not silently clamp to --days)."""
    tool_rows = [_tool_row("obs-read", "ok", 20)]  # only use is 20 days ago
    client = FakeClient(tool_rows, [], [], [])
    # default dead window == days: 0 uses in last 14d -> DEAD, and total(win)==0.
    d14 = _by_id(A.gather(client, days=14, insight_days=30, dead_days=14, now=NOW))
    assert d14["obs-read"]["total"] == 0
    assert d14["obs-read"]["dead"] is True
    # wider dead window sees the 20-day-old use -> NOT dead (count still 0 in win).
    d25 = _by_id(A.gather(client, days=14, insight_days=30, dead_days=25, now=NOW))
    assert d25["obs-read"]["total"] == 0
    assert d25["obs-read"]["dead"] is False


def test_gather_friction_section():
    # insight_days=14 -> 7d halves: the 1d row is recent, the 10d row is prior.
    data = A.gather(_sample_client(), days=14, insight_days=14, dead_days=14, now=NOW)
    fr = data["friction"]
    assert fr["recent"]["permission_block"] == 2
    assert fr["prior"]["permission_block"] == 5
    assert fr["deltas"]["permission_block"] == -3


def test_gather_json_shape():
    data = A.gather(_sample_client(), days=14, insight_days=30, dead_days=14, now=NOW)
    parsed = json.loads(json.dumps(data, default=str))
    assert parsed["days"] == 14 and parsed["insight_days"] == 30
    assert isinstance(parsed["items"], list) and parsed["items"]
    for it in parsed["items"]:
        for k in ("id", "label", "via", "opt_in", "total", "outcomes",
                  "recent", "prior", "trend", "dead", "impact"):
            assert k in it


def test_render_has_sections():
    data = A.gather(_sample_client(), days=14, insight_days=30, dead_days=14, now=NOW)
    text = A.render(data)
    assert "## ADOPTION" in text
    assert "## IMPACT" in text
    assert "## DRAFTER FRICTION TREND" in text
    assert "DEAD" in text
    assert "CAVEATS" in text


# --------------------------------------------------------------------------- #
# graceful degradation
# --------------------------------------------------------------------------- #
class BoomClient:
    def rows(self, sql):
        raise RuntimeError("connection refused")


def test_gather_degrades_to_telemetry_unavailable():
    with pytest.raises(A.TelemetryUnavailable):
        A.gather(BoomClient(), days=14, insight_days=30, dead_days=14, now=NOW)


def test_main_no_creds_exits_zero(monkeypatch, capsys):
    monkeypatch.delenv("CLICKHOUSE_URL", raising=False)
    rc = A.main([])
    assert rc == 0
    assert "telemetry not configured" in capsys.readouterr().err


def test_main_unavailable_exits_zero(monkeypatch, capsys):
    monkeypatch.setenv("CLICKHOUSE_URL", "http://127.0.0.1:1")
    monkeypatch.setattr(A.Q, "CHClient", lambda conn: BoomClient())
    rc = A.main([])
    assert rc == 0
    assert "telemetry unavailable" in capsys.readouterr().err


def test_main_bad_days_exits_two(capsys):
    assert A.main(["--days", "0"]) == 2


def test_aggregating_queries_do_not_shadow_ts_column():
    """Regression: aliasing an aggregate as `max(ts) AS ts` shadows the raw `ts`
    column, so the WHERE `ts>now()` binds to the aggregate → ClickHouse
    ILLEGAL_AGGREGATION (a live 500 hermetic mocks can't catch). The two GROUP BY
    queries must alias to `last_ts`, and only those two carry that alias."""
    for sql in (A.q_insights(3600), A.q_cwd_sessions(3600, "task-spec-drafter")):
        assert "max(ts) AS ts " not in sql and "max(ts) AS ts\n" not in sql
        assert "max(ts) AS last_ts" in sql
        # the WHERE still filters on the raw column, unshadowed
        assert "ts>now()-" in sql
    # a normalised row exposes `ts` for the shared aggregators
    assert A._norm_last_ts([{"session": "s", "last_ts": "2026-01-01 00:00:00"}])[0]["ts"] \
        == "2026-01-01 00:00:00"
