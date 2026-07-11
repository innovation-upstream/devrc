"""Unit tests for insights.py — the telemetry-native report.

No test hits a real ClickHouse: the script is SQL-builders + pure aggregation +
formatting. We test:
  - SQL builders (windowed, host-filterable, argMax read-contract, well-formed),
  - aggregate() over fixture session-summary + message rows (per-host breakdown,
    tool/lang/commit totals, top commands/themes, activity-by-day, unreadable),
  - the Layer-B "qualitative pending" placeholder vs. present outcomes,
  - gather() over a fake client + graceful degrade when the client raises,
  - render()/render_json/render_html on the aggregate.
"""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

SA_DIR = Path(__file__).resolve().parent.parent           # scripts/session-analysis
sys.path.insert(0, str(SA_DIR.parent / "validation"))     # chquery
_spec = importlib.util.spec_from_file_location("insights", SA_DIR / "insights.py")
I = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(I)


# --------------------------------------------------------------------------- #
# Pure helpers + SQL builders
# --------------------------------------------------------------------------- #
def test_window_seconds():
    assert I.window_seconds(14) == 14 * 86400
    with pytest.raises(ValueError):
        I.window_seconds(0)


def _balanced(sql):
    depth = 0
    for c in sql:
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth < 0:
                return False
    return depth == 0


def test_summaries_query_is_argmax_windowed_and_scoped():
    sql = I.q_summaries(14 * 86400)
    assert _balanced(sql)
    assert "kind='session-summary'" in sql
    assert "argMax(toString(payload), ingested_at)" in sql  # the read contract
    assert "GROUP BY session" in sql
    assert "now()-1209600" in sql
    assert "host=" not in sql  # no host filter when host is None


def test_host_filter_is_quoted():
    sql = I.q_summaries(86400, "o'brien")
    assert "host='o\\'brien'" in sql
    assert "host='laptop'" in I.q_messages(86400, "laptop")


def test_messages_and_insights_queries():
    assert "kind IN ('prompt','command')" in I.q_messages(86400)
    assert "kind='session-insight'" in I.q_insights(86400)


# --------------------------------------------------------------------------- #
# aggregate()
# --------------------------------------------------------------------------- #
def _summary(session, host, project, payload, ts="2026-07-10 10:00:00.000"):
    return {"session": session, "host": host, "project": project,
            "ts": ts, "payload": json.dumps(payload)}


def _sample_summaries():
    return [
        _summary("s1", "workbench", "devrc", {
            "tool_counts": {"Bash": 10, "Edit": 5}, "languages": {"Python": 3, "Nix": 2},
            "input_tokens": 1000, "output_tokens": 20000,
            "user_message_count": 4, "assistant_message_count": 40,
            "git_commits": 2, "git_pushes": 1, "lines_added": 50, "lines_removed": 10,
            "files_modified": 6, "user_interruptions": 1, "tool_errors": 2,
            "tool_error_categories": {"Command Failed": 2},
            "duration_minutes": 30, "models": ["claude-opus-4-8"], "unreadable": False}),
        _summary("s2", "laptop", "homelab-talos", {
            "tool_counts": {"Bash": 4, "Read": 8}, "languages": {"YAML": 5},
            "input_tokens": 500, "output_tokens": 8000,
            "user_message_count": 2, "assistant_message_count": 20,
            "git_commits": 1, "git_pushes": 0, "lines_added": 12, "lines_removed": 3,
            "files_modified": 2, "user_interruptions": 0, "tool_errors": 0,
            "duration_minutes": 15, "models": ["claude-opus-4-8"], "unreadable": False}),
        _summary("s3", "workbench", "devrc", {"unreadable": True}),
    ]


def _sample_messages():
    return [
        {"kind": "command", "host": "workbench", "text": "handoff now", "ts": "2026-07-10 10:00:00.000"},
        {"kind": "command", "host": "laptop", "text": "resume", "ts": "2026-07-10 11:00:00.000"},
        {"kind": "prompt", "host": "workbench", "text": "deploy the pod to the cluster", "ts": "2026-07-10 10:30:00.000"},
        {"kind": "prompt", "host": "workbench", "text": "fix the failing test", "ts": "2026-07-11 09:00:00.000"},
    ]


def test_aggregate_totals_and_hosts():
    d = I.aggregate(_sample_summaries(), _sample_messages(), [], 14, None)
    assert d["sessions"] == 3
    assert d["unreadable_sessions"] == 1
    t = d["totals"]
    assert t["commits"] == 3 and t["pushes"] == 1
    assert t["lines_added"] == 62 and t["lines_removed"] == 13
    assert t["output_tokens"] == 28000
    assert d["tool_counts"]["Bash"] == 14
    assert d["languages"]["Python"] == 3 and d["languages"]["YAML"] == 5
    assert d["projects"]["devrc"] == 2
    # message stream
    assert d["messages"] == 4 and d["prompts"] == 2 and d["commands"] == 2
    assert dict(d["top_commands"]).get("handoff") == 1
    assert dict(d["top_themes"]).get("deploy/infra") == 1
    assert dict(d["top_themes"]).get("debug/errors") == 1
    # per-host breakdown
    assert d["hosts"]["workbench"]["sessions"] == 2
    assert d["hosts"]["laptop"]["sessions"] == 1
    assert d["hosts"]["workbench"]["commits"] == 2
    # activity by day sorted
    assert d["activity_by_day"] == {"2026-07-10": 3, "2026-07-11": 1}


def test_aggregate_qualitative_pending_by_default():
    d = I.aggregate(_sample_summaries(), [], [], 14, None)
    assert d["qualitative_pending"] is True
    assert d["outcomes"] is None


def test_aggregate_renders_outcomes_when_layer_b_present():
    insights = [
        {"session": "s1", "payload": json.dumps({"outcome": "fully_achieved"})},
        {"session": "s2", "payload": json.dumps({"outcome": "partially_achieved"})},
    ]
    d = I.aggregate(_sample_summaries(), [], insights, 14, None)
    assert d["qualitative_pending"] is False
    assert d["outcomes"]["fully_achieved"] == 1


def test_aggregate_host_scope_recorded():
    d = I.aggregate([], [], [], 14, "laptop")
    assert d["host"] == "laptop"
    assert d["sessions"] == 0


# --------------------------------------------------------------------------- #
# gather() + graceful degrade
# --------------------------------------------------------------------------- #
class FakeClient:
    def __init__(self, summaries, messages, insights):
        self._s, self._m, self._i = summaries, messages, insights

    def rows(self, sql):
        if "session-summary" in sql:
            return self._s
        if "session-insight" in sql:
            return self._i
        return self._m


def test_gather_assembles_from_client():
    client = FakeClient(_sample_summaries(), _sample_messages(), [])
    d = I.gather(client, days=14, host=None)
    assert d["sessions"] == 3 and d["commands"] == 2


class BoomClient:
    def rows(self, sql):
        raise RuntimeError("connection refused")


def test_gather_degrades_to_telemetry_unavailable():
    with pytest.raises(I.TelemetryUnavailable):
        I.gather(BoomClient(), days=14, host=None)


# --------------------------------------------------------------------------- #
# render / json / html
# --------------------------------------------------------------------------- #
def test_render_text_has_sections_and_numbers():
    d = I.aggregate(_sample_summaries(), _sample_messages(), [], 14, None)
    text = I.render(d)
    assert "## ACTIVITY" in text
    assert "## TOOLS" in text
    assert "## TOP SLASH-COMMANDS" in text
    assert "## OUTCOMES" in text
    assert "qualitative layer pending (PR-2)" in text
    assert "1 unreadable" in text
    assert "3 commits" in text


def test_render_json_is_valid():
    d = I.aggregate(_sample_summaries(), [], [], 14, None)
    parsed = json.loads(json.dumps(d, default=str))
    assert parsed["sessions"] == 3


def test_render_html_is_self_contained_and_honest():
    d = I.aggregate(_sample_summaries(), _sample_messages(), [], 14, None)
    h = I.render_html(d)
    assert h.startswith("<!doctype html>")
    assert "prefers-color-scheme:dark" in h and "data-theme=dark" in h
    assert "no fabricated outcomes" in h.lower()
    assert "unreadable" in h.lower()
    # no external resources (CSP-safe / self-contained)
    assert "http://" not in h and "https://" not in h
    assert "<script" not in h.lower()
