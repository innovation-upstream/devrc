"""Unit tests for scripts/agent-ops — the tmux agent-ops dashboard.

Exercises the PURE aggregation + render functions against mock inputs (mock
bar-status JSONs, a mock tmux-pane list + process tree, mock initiative-scan
--json). fetch is separated from render, so nothing here touches /proc, tmux,
the network, or the filesystem sources. Also asserts fail-safe: missing /
malformed / empty inputs degrade to a graceful "—"/"n/a" line, never an
exception.
"""
import importlib.util
import os
import re

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPT = os.path.join(_HERE, "..", "agent-ops")

# agent-ops has no .py extension → load it by explicit path.
_spec = importlib.util.spec_from_loader(
    "agent_ops", importlib.machinery.SourceFileLoader("agent_ops", _SCRIPT))
ao = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ao)

_ANSI = re.compile(r"\033\[[0-9;]*m")


def plain(lines):
    """Strip ANSI so assertions read the visible text."""
    if isinstance(lines, str):
        return _ANSI.sub("", lines)
    return [_ANSI.sub("", ln) for ln in lines]


# ---------------------------------------------------------------------------
# clawgate_pending_titles
# ---------------------------------------------------------------------------
def test_clawgate_titles_filters_pending_only():
    tasks = [
        {"id": 1, "status": "open", "title": "ship X"},
        {"id": 2, "status": "in_progress", "title": "running Y"},
        {"id": 3, "status": "ready_for_review", "title": "review Z"},
        {"id": 4, "status": "done", "title": "old"},
    ]
    out = ao.clawgate_pending_titles(tasks)
    assert out == ["#1 ship X", "#3 review Z"]


def test_clawgate_titles_failsafe_on_junk():
    assert ao.clawgate_pending_titles(None) == []
    assert ao.clawgate_pending_titles({"not": "a list"}) == []
    # tolerates junk elements + missing title
    out = ao.clawgate_pending_titles(["x", 3, {"id": 9, "status": "open"}])
    assert out == ["#9 (no title)"]


# ---------------------------------------------------------------------------
# parse_panes
# ---------------------------------------------------------------------------
def test_parse_panes_wellformed_and_junk():
    raw = "\n".join([
        "%0|16060|main|1|devrc ●|/home/zach/workspace/devrc|claude",
        "%1|16095|main|2|dp|/home/zach/workspace/civit/datapacket-talos|zsh",
        "garbage line without pipes",
        "%2|notanint|x|1|w|/p|zsh",       # bad pid → dropped
    ])
    panes = ao.parse_panes(raw)
    assert len(panes) == 2
    assert panes[0]["pane_pid"] == 16060
    assert panes[0]["window_name"] == "devrc"   # trailing ' ●' stripped
    assert panes[1]["command"] == "zsh"


def test_parse_panes_empty():
    assert ao.parse_panes("") == []


# ---------------------------------------------------------------------------
# classify_claude_sessions — the live-Claude detector
# ---------------------------------------------------------------------------
def _proc_index():
    """Mock tree:
      16060 zsh(claude pane) -> 108149 .claude-wrapped -> 200 npm(mcp)  [INCLUDE]
      16095 zsh(plain pane)                                              [EXCLUDE]
      500   zsh(dashboard's own pane) -> 999 (our pid)                   [EXCLUDE]
    """
    return {
        16060: {"comm": "zsh", "ppid": 1, "state": "S", "age_secs": 100,
                "children": [108149]},
        108149: {"comm": ".claude-wrapped", "ppid": 16060, "state": "R",
                 "age_secs": 90, "children": [200]},
        200: {"comm": "npm exec mcp", "ppid": 108149, "state": "S",
              "age_secs": 80, "children": []},
        16095: {"comm": "zsh", "ppid": 1, "state": "S", "age_secs": 100,
                "children": []},
        500: {"comm": "zsh", "ppid": 1, "state": "S", "age_secs": 5,
              "children": [999]},
        999: {"comm": "python3", "ppid": 500, "state": "R", "age_secs": 5,
              "children": []},
    }


def _panes():
    return [
        {"pane_id": "%0", "pane_pid": 16060, "session": "main", "window_index": "1",
         "window_name": "devrc", "path": "/home/zach/workspace/devrc", "command": "claude"},
        {"pane_id": "%1", "pane_pid": 16095, "session": "main", "window_index": "2",
         "window_name": "dp", "path": "/home/zach/ws/dp", "command": "zsh"},
        {"pane_id": "%9", "pane_pid": 500, "session": "main", "window_index": "9",
         "window_name": "self", "path": "/home/zach/workspace/devrc", "command": "python3"},
    ]


def test_classify_includes_claude_excludes_plain_and_own():
    sessions = ao.classify_claude_sessions(
        _panes(), _proc_index(), own_pids={999},
        root_resolver=lambda p: p)  # treat path as its own root
    # the plain zsh pane and the dashboard's own pane are both excluded
    ids = [s["pane_id"] for s in sessions]
    assert ids == ["%0"]
    s = sessions[0]
    assert s["repo"] == "devrc"
    assert s["session"] == "main" and s["window_index"] == "1"
    assert s["busy"] is True          # .claude-wrapped state == R
    assert s["age_secs"] == 90


def test_classify_detects_via_foreground_command_when_tree_missing():
    # No proc_index entry at all → falls back to pane_current_command == 'claude'.
    panes = [{"pane_id": "%0", "pane_pid": 7, "session": "s", "window_index": "1",
              "window_name": "w", "path": "/repo", "command": "claude"}]
    sessions = ao.classify_claude_sessions(panes, {}, root_resolver=lambda p: p)
    assert len(sessions) == 1
    assert sessions[0]["busy"] is None      # no proc info → unknown, not a crash


def test_classify_empty_and_ordering():
    assert ao.classify_claude_sessions([], {}) == []
    # ordering: sort by (repo, session, window_index)
    panes = [
        {"pane_id": "%b", "pane_pid": 2, "session": "z", "window_index": "5",
         "window_name": "", "path": "/b", "command": "claude"},
        {"pane_id": "%a", "pane_pid": 1, "session": "a", "window_index": "1",
         "window_name": "", "path": "/a", "command": "claude"},
    ]
    sessions = ao.classify_claude_sessions(panes, {}, root_resolver=lambda p: p)
    assert [s["repo"] for s in sessions] == ["a", "b"]


# ---------------------------------------------------------------------------
# flatten_initiatives
# ---------------------------------------------------------------------------
def _scan():
    return {"by_repo": {
        "/home/zach/workspace/devrc": [
            {"repo": "/home/zach/workspace/devrc", "slug": "one", "title": "Init One",
             "momentum": "active", "last_touch": 2000, "next_step": "do the thing",
             "open_prs": [{"number": 70, "title": "restore plan override"}],
             "merged_prs": 0, "last_commit": 1900},
            {"repo": "/home/zach/workspace/devrc", "slug": "two", "title": "Init Two",
             "momentum": "stalled", "last_touch": 1000, "next_step": None,
             "open_prs": [], "merged_prs": 2, "last_commit": 1500},
        ],
        "/home/zach/workspace/homelab": [
            {"repo": "/home/zach/workspace/homelab", "slug": "three", "title": "Init Three",
             "momentum": "slowing", "last_touch": 1800, "next_step": "wire it up",
             "open_prs": [], "merged_prs": 0, "last_commit": None},
            "junk-not-a-dict",
        ],
    }}


def test_flatten_initiatives_and_failsafe():
    items = ao.flatten_initiatives(_scan())
    assert len(items) == 3          # junk string dropped
    assert ao.flatten_initiatives(None) == []
    assert ao.flatten_initiatives({}) == []
    assert ao.flatten_initiatives({"by_repo": "nope"}) == []


# ---------------------------------------------------------------------------
# render_blocked
# ---------------------------------------------------------------------------
def test_render_blocked_counts_and_titles():
    cg = {"count": 2, "state": "Warning", "detail": "2 awaiting"}
    mail = {"count": 0, "detail": "inbox clear"}
    out = plain(ao.render_blocked(cg, mail, titles=["#1 ship X", "#3 review Z"]))
    body = "\n".join(out)
    assert "BLOCKED ON ME" in body
    assert "clawgate" in body and "2 awaiting" in body
    assert "ship X" in body and "review Z" in body
    assert "inbox clear" in body


def test_render_blocked_failsafe_missing():
    out = plain(ao.render_blocked(None, None))
    body = "\n".join(out)
    assert "clawgate  — n/a" in body
    assert "mail      — n/a" in body


# ---------------------------------------------------------------------------
# render_active_runs
# ---------------------------------------------------------------------------
def test_render_active_runs_rows_and_glyph():
    sessions = [
        {"pane_id": "%0", "repo": "devrc", "session": "main", "window_index": "1",
         "window_name": "devrc", "busy": True, "age_secs": 3600},
        {"pane_id": "%1", "repo": "homelab", "session": "s", "window_index": "2",
         "window_name": "h", "busy": False, "age_secs": 90},
    ]
    out = plain(ao.render_active_runs(sessions))
    body = "\n".join(out)
    assert "2 live Claude session(s)" in body
    assert "devrc" in body and "main:1" in body and "1h" in body
    assert "homelab" in body and "s:2" in body


def test_render_active_runs_empty():
    out = plain(ao.render_active_runs([]))
    assert any("no live Claude sessions" in ln for ln in out)


def test_render_active_runs_maps_scratch_codenames():
    sessions = [
        {"pane_id": "%0", "repo": "devrc", "session": "scratch10", "window_index": "1",
         "window_name": "w", "busy": True, "age_secs": 60},
        {"pane_id": "%1", "repo": "homelab", "session": "8", "window_index": "2",
         "window_name": "w", "busy": False, "age_secs": 60},
    ]
    codenames = {"scratch10": "Nickel", "scratch2": "Gold"}
    out = plain(ao.render_active_runs(sessions, codenames))
    body = "\n".join(out)
    assert "Nickel:1" in body          # scratch10 → codename
    assert "scratch10" not in body     # raw name gone
    assert "8:2" in body               # numbered session passes through untouched


def test_render_active_runs_empty_codenames_fallback():
    sessions = [{"pane_id": "%0", "repo": "devrc", "session": "scratch10",
                 "window_index": "1", "window_name": "w", "busy": None,
                 "age_secs": 1}]
    # empty map / None both fall back to the raw session name (never crash)
    for cn in ({}, None):
        body = "\n".join(plain(ao.render_active_runs(sessions, cn)))
        assert "scratch10:1" in body


# ---------------------------------------------------------------------------
# codename mapping — _session_label / load_scratch_codenames
# ---------------------------------------------------------------------------
def test_session_label_scratch_and_passthrough():
    cn = {"scratch4": "Vapor", "scratch10": "Nickel"}
    assert ao._session_label("scratch4", cn) == "Vapor"
    assert ao._session_label("scratch10", cn) == "Nickel"
    assert ao._session_label("8", cn) == "8"          # numbered → passthrough
    assert ao._session_label("main", cn) == "main"    # named → passthrough
    assert ao._session_label("scratch4", {}) == "scratch4"   # empty map
    assert ao._session_label("scratch4", None) == "scratch4"  # missing map


def test_load_scratch_codenames_parses_and_prefers_first(tmp_path):
    slots = tmp_path / "scratch-slots.sh"
    slots.write_text(
        'SCRATCH_SLOTS=(\n'
        '    "scratch2:G:#d79921:Gold"\n'
        '    "scratch10:N:#928374:Nickel"\n'
        ')\n')
    mapping = ao.load_scratch_codenames([str(slots)])
    assert mapping == {"scratch2": "Gold", "scratch10": "Nickel"}
    # first non-empty wins: a missing deployed path falls through to the repo copy
    missing = tmp_path / "nope.sh"
    assert ao.load_scratch_codenames([str(missing), str(slots)]) == mapping


def test_load_scratch_codenames_failsafe(tmp_path):
    assert ao.load_scratch_codenames([str(tmp_path / "nope.sh")]) == {}


# ---------------------------------------------------------------------------
# viewport — the PURE scroll-slice
# ---------------------------------------------------------------------------
def _lines(n):
    return ["L%d" % i for i in range(n)]


def test_viewport_short_content_no_scroll():
    body = _lines(5)
    visible, off, ind = ao.viewport(body, avail=20, offset=0)
    assert visible == body            # everything fits
    assert off == 0
    assert ind == ""                  # no indicator when nothing clipped


def test_viewport_top_window_and_indicator():
    body = _lines(58)
    visible, off, ind = ao.viewport(body, avail=20, offset=0)
    assert visible == body[0:20]
    assert off == 0
    assert "1–20/58" in ind
    assert "↓" in ind and "↑" not in ind   # more below, nothing above


def test_viewport_middle_window():
    body = _lines(58)
    visible, off, ind = ao.viewport(body, avail=20, offset=10)
    assert visible == body[10:30]
    assert off == 10
    assert "11–30/58" in ind
    assert "↑" in ind and "↓" in ind        # clipped both ends


def test_viewport_clamps_offset_to_bottom():
    body = _lines(58)
    # a huge offset (e.g. from 'G') clamps to the last full window
    visible, off, ind = ao.viewport(body, avail=20, offset=10 ** 9)
    assert off == 38                        # 58 - 20
    assert visible == body[38:58]
    assert "39–58/58" in ind
    assert "↑" in ind and "↓" not in ind    # at bottom, nothing below


def test_viewport_clamps_negative_offset():
    body = _lines(58)
    visible, off, ind = ao.viewport(body, avail=20, offset=-5)
    assert off == 0 and visible == body[0:20]


def test_viewport_degenerate_avail():
    # avail < 1 is coerced to 1 (never an empty/negative slice)
    visible, off, ind = ao.viewport(_lines(10), avail=0, offset=3)
    assert len(visible) == 1 and off == 3


# ---------------------------------------------------------------------------
# render_prs / render_momentum / render_done
# ---------------------------------------------------------------------------
def test_render_prs_lists_open_prs():
    items = ao.flatten_initiatives(_scan())
    out = plain(ao.render_prs(items, "updated 1m ago"))
    body = "\n".join(out)
    assert "#70" in body and "restore plan override" in body
    assert "updated 1m ago" in body


def test_render_prs_empty():
    out = plain(ao.render_prs([]))
    assert any("no open PRs" in ln for ln in out)


def test_render_momentum_orders_active_first_and_shows_next_step():
    items = ao.flatten_initiatives(_scan())
    out = plain(ao.render_momentum(items))
    body = "\n".join(out)
    # stalled 'two' must NOT appear; active/slowing do
    assert "Init One" in body and "Init Three" in body
    assert "Init Two" not in body
    # active ('Init One') is rendered before slowing ('Init Three')
    assert body.index("Init One") < body.index("Init Three")
    assert "do the thing" in body and "wire it up" in body


def test_render_momentum_empty():
    out = plain(ao.render_momentum([]))
    assert any("nothing active" in ln for ln in out)


def test_render_done_lists_merged():
    items = ao.flatten_initiatives(_scan())
    out = plain(ao.render_done(items))
    body = "\n".join(out)
    assert "Init Two" in body and "✓2" in body


def test_render_done_empty():
    out = plain(ao.render_done([]))
    assert any("no recently merged" in ln for ln in out)


# ---------------------------------------------------------------------------
# render_health
# ---------------------------------------------------------------------------
def test_render_health_counts_and_failsafe():
    alerts = {"count": 22, "state": "Critical", "detail": "22 firing (15 critical)"}
    out = plain(ao.render_health(alerts, None))
    body = "\n".join(out)
    assert "homelab" in body and "22 firing" in body
    assert "civitai" in body and "n/a" in body


# ---------------------------------------------------------------------------
# freshness
# ---------------------------------------------------------------------------
def test_initiatives_freshness_no_cache(tmp_path):
    note = ao.initiatives_freshness(cache=str(tmp_path / "nope.json"),
                                    lock=str(tmp_path / "nolock"))
    assert "no cache yet" in note


def test_initiatives_freshness_refreshing(tmp_path):
    cache = tmp_path / "c.json"
    lock = tmp_path / "l"
    cache.write_text("{}")
    lock.write_text("")
    note = ao.initiatives_freshness(cache=str(cache), lock=str(lock))
    assert "updated" in note and "refreshing" in note


# ---------------------------------------------------------------------------
# build_frame smoke test — must never raise even with everything missing
# ---------------------------------------------------------------------------
def test_build_frame_failsafe(monkeypatch):
    monkeypatch.setattr(ao, "read_json", lambda p: None)
    monkeypatch.setattr(ao, "list_tmux_panes_raw", lambda: "")
    monkeypatch.setattr(ao, "build_proc_index", lambda: {})
    monkeypatch.setattr(ao, "own_pid_chain", lambda: set())
    monkeypatch.setattr(ao, "maybe_refresh_initiatives", lambda *a, **k: None)
    monkeypatch.setattr(ao, "enrich_clawgate_titles", lambda *a, **k: [])
    frame = ao.build_frame(100)
    body = plain(frame)
    for section in ("BLOCKED ON ME", "ACTIVE AGENT RUNS", "IN FLIGHT",
                    "MOMENTUM", "HEALTH", "RECENTLY DONE"):
        assert section in body
