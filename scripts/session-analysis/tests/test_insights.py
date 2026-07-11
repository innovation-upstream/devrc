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


def test_summaries_host_alias_is_not_shadowing():
    # the host aggregate must be aliased sess_host (NOT host) so the WHERE host
    # filter isn't resolved to the aggregate (ILLEGAL_AGGREGATION).
    sql = I.q_summaries(86400, "workbench")
    assert "argMax(host, ingested_at) AS sess_host" in sql
    assert "AS host" not in sql
    assert "host='workbench'" in sql


def test_no_select_alias_shadows_a_where_filtered_column():
    """Guard the whole file: in EVERY query, no `... AS <alias>` may reuse the
    name of a column referenced by a WHERE comparison (the class of bug that hit
    both `ts` and `host`)."""
    import re
    op = re.compile(r"\b([a-zA-Z_]\w*)\s*(?:>=|<=|=|>|<)")
    for sql in (I.q_summaries(86400, "workbench"),
                I.q_insights(86400, "workbench"),
                I.q_messages(86400, "workbench")):
        aliases = set(re.findall(r"\bAS\s+(\w+)", sql))
        where = sql.split("WHERE", 1)[1] if "WHERE" in sql else ""
        where_cols = set(op.findall(where))
        clash = aliases & where_cols
        assert not clash, f"alias(es) {clash} shadow a WHERE column in: {sql}"


# --------------------------------------------------------------------------- #
# aggregate()
# --------------------------------------------------------------------------- #
def _summary(session, host, project, payload, ts="2026-07-10 10:00:00.000"):
    # q_summaries aliases the host aggregate as `sess_host` (a bare `host` alias
    # would shadow the WHERE host filter → ILLEGAL_AGGREGATION), so real rows
    # carry the host under `sess_host`.
    return {"session": session, "sess_host": host, "project": project,
            "ts": ts, "payload": json.dumps(payload)}


def _sample_summaries():
    return [
        _summary("s1", "workbench", "devrc", {
            "tool_counts": {"Bash": 10, "Edit": 5}, "languages": {"Python": 3, "Nix": 2},
            "input_tokens": 1000, "output_tokens": 20000,
            "cache_read_tokens": 500000, "cache_creation_tokens": 40000,
            "user_message_count": 4, "assistant_message_count": 40,
            "git_commits": 2, "git_pushes": 1, "lines_added": 50, "lines_removed": 10,
            "files_modified": 6, "user_interruptions": 1, "tool_errors": 2,
            "tool_error_categories": {"Command Failed": 2},
            "duration_minutes": 30, "models": ["claude-opus-4-8"], "unreadable": False}),
        _summary("s2", "laptop", "homelab-talos", {
            "tool_counts": {"Bash": 4, "Read": 8}, "languages": {"YAML": 5},
            "input_tokens": 500, "output_tokens": 8000,
            "cache_read_tokens": 100000, "cache_creation_tokens": 10000,
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
    assert t["input_tokens"] == 1500
    assert t["cache_read_tokens"] == 600000
    assert t["cache_creation_tokens"] == 50000
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
    assert "no qualitative insights yet" in text
    assert "1 unreadable" in text
    assert "3 commits" in text
    # honest tokens: total input = 1500 fresh + 600000 cache-read + 50000 cache-write
    assert "651,500 in" in text
    assert "cache-read" in text and "cache-write" in text
    # approximate-git caveat present
    assert "git counts are approximate" in text


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
    # honest input tokens (incl. cache) surfaced, not bare fresh input
    assert "input tokens (incl. cache)" in h
    assert "cache-read" in h
    # no external resources (CSP-safe / self-contained)
    assert "http://" not in h and "https://" not in h
    assert "<script" not in h.lower()


# --------------------------------------------------------------------------- #
# Layer B (PR-2) — session-insight aggregation + report sections
# --------------------------------------------------------------------------- #
def _insight(session, payload):
    return {"session": session, "payload": json.dumps(payload)}


def _sample_insights():
    return [
        _insight("s1", {
            "outcome": "fully_achieved", "session_type": "feature_build",
            "goal_categories": ["infra", "feature"], "claude_helpfulness": 5,
            "friction_counts": {"wrong_approach": 2},
            "automation_opportunity": {"present": True, "description": "wrap deploy dance",
                "trigger": "hand-typed nix switch+verify", "leverage": "high",
                "evidence": "did it by hand"},
            "recurring_toil": {"present": True, "description": "manual env export",
                "category": "env-setup", "frequency_hint": "every session"},
            "workflow_gap": {"present": True, "description": "no status skill",
                "kind": "missing_tool"},
            "unreadable": False}),
        _insight("s2", {
            "outcome": "partially_achieved", "session_type": "bugfix",
            "goal_categories": ["bugfix"], "claude_helpfulness": 3,
            "friction_counts": {"tool_error": 1, "wrong_approach": 1},
            # SAME normalized automation opportunity as s1 → grouped, session-count 2
            "automation_opportunity": {"present": True, "description": "Wrap Deploy Dance",
                "trigger": "hand-typed   nix switch+verify", "leverage": "medium",
                "evidence": "again"},
            "recurring_toil": None, "workflow_gap": None, "unreadable": False}),
        _insight("s3", {"unreadable": True, "unreadable_reason": "truncated"}),
    ]


def test_aggregate_insights_distribution_and_grouping():
    d = I.aggregate(_sample_summaries(), [], _sample_insights(), 14, None)
    assert d["qualitative_pending"] is False
    assert d["insight_sessions"] == 2
    assert d["unreadable_insights"] == 1
    # outcome distribution (unreadable excluded)
    assert d["outcomes"]["fully_achieved"] == 1
    assert d["outcomes"]["partially_achieved"] == 1
    # mean helpfulness over the 2 readable rows
    assert d["helpfulness"]["mean"] == 4.0 and d["helpfulness"]["n"] == 2
    assert d["helpfulness"]["hist"][5] == 1 and d["helpfulness"]["hist"][3] == 1
    # session types + goal categories
    assert d["session_types"]["feature_build"] == 1
    assert d["goal_categories"]["infra"] == 1 and d["goal_categories"]["bugfix"] == 1
    # friction summed across sessions
    assert d["friction"]["wrong_approach"] == 3 and d["friction"]["tool_error"] == 1
    # automation grouped by normalized trigger|description → one entry, 2 sessions,
    # highest leverage (high) retained
    assert len(d["automation"]) == 1
    a = d["automation"][0]
    assert a["sessions"] == 2 and a["leverage"] == "high"
    assert d["toil"][0]["category"] == "env-setup"
    assert d["gaps"][0]["kind"] == "missing_tool"


def test_automation_leverage_then_frequency_ranking():
    rows = [
        _insight("a", {"outcome": "unclear", "session_type": "chore",
                       "claude_helpfulness": 3, "friction_counts": {},
                       "automation_opportunity": {"present": True, "description": "low win",
                           "trigger": "t-low", "leverage": "low"}, "unreadable": False}),
        _insight("b", {"outcome": "unclear", "session_type": "chore",
                       "claude_helpfulness": 3, "friction_counts": {},
                       "automation_opportunity": {"present": True, "description": "high win",
                           "trigger": "t-high", "leverage": "high"}, "unreadable": False}),
    ]
    d = I.aggregate([], [], rows, 14, None)
    # high leverage ranks first regardless of insertion order
    assert d["automation"][0]["leverage"] == "high"
    assert d["automation"][1]["leverage"] == "low"


def test_render_lights_up_layer_b_sections():
    d = I.aggregate(_sample_summaries(), [], _sample_insights(), 14, None)
    text = I.render(d)
    assert "## OUTCOMES" in text
    assert "fully_achieved" in text
    assert "mean Claude-helpfulness" in text
    assert "AUTOMATION CANDIDATES / RECURRING TOIL / WORKFLOW GAPS" in text
    assert "wrap deploy dance" in text
    assert "env-setup" in text
    assert "missing_tool" in text
    # unreadable footnote present, excluded from aggregates
    assert "1 session(s) flagged unreadable" in text


def test_insight_window_defaults_to_30():
    d = I.aggregate(_sample_summaries(), [], _sample_insights(), 14, None)
    assert d["insight_days"] == 30
    d2 = I.aggregate(_sample_summaries(), [], _sample_insights(), 45, None)
    assert d2["insight_days"] == 45   # widens with --days
    d3 = I.aggregate(_sample_summaries(), [], _sample_insights(), 14, None, insight_days=60)
    assert d3["insight_days"] == 60


def test_empty_window_degrades_gracefully():
    d = I.aggregate(_sample_summaries(), [], [], 14, None)
    text = I.render(d)
    assert "no qualitative insights yet" in text
    assert "AUTOMATION CANDIDATES" not in text   # section suppressed when empty


def test_gather_uses_wider_insight_window():
    # the insights query must use the WIDER window (spec §11); assert the SQL win.
    captured = {}

    class C:
        def rows(self, sql):
            if "session-summary" in sql:
                captured["summary"] = sql
                return _sample_summaries()
            if "session-insight" in sql:
                captured["insight"] = sql
                return _sample_insights()
            return []
    I.gather(C(), days=14, host=None)
    assert "now()-1209600" in captured["summary"]     # 14d
    assert "now()-2592000" in captured["insight"]      # 30d

