"""Unit tests for scripts/agent-ops — the tmux agent-ops dashboard.

Exercises the PURE aggregation + render functions against mock inputs (mock
bar-status JSONs, a mock tmux-pane list + process tree, mock initiative-scan
--json). fetch is separated from render, so nothing here touches /proc, tmux,
the network, or the filesystem sources. Also asserts fail-safe: missing /
malformed / empty inputs degrade to a graceful "—"/"n/a" line, never an
exception.
"""
import datetime
import importlib.util
import json
import os
import re
import time

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
        "%0|16060|main|1|@4|devrc ●|/home/zach/workspace/devrc|claude",
        "%1|16095|main|2|@16|dp|/home/zach/workspace/civit/datapacket-talos|zsh",
        "garbage line without pipes",
        "%2|notanint|x|1|@7|w|/p|zsh",    # bad pid → dropped
        "%3|17000|s|1|w|/p|zsh",          # 7 fields (pre-window_id shape) → dropped
    ])
    panes = ao.parse_panes(raw)
    assert len(panes) == 2
    assert panes[0]["pane_pid"] == 16060
    assert panes[0]["window_id"] == "@4"        # the per-window age join's key
    assert panes[0]["window_name"] == "devrc"   # trailing ' ●' stripped
    assert panes[1]["window_id"] == "@16"
    assert panes[1]["command"] == "zsh"
    # title stays LAST and absorbs any trailing pipes it contains
    titled = ao.parse_panes("%9|1|s|3|@9|w|/p|claude|✳ do|the|thing")
    assert titled[0]["title"] == "✳ do|the|thing"


def test_parse_panes_empty():
    assert ao.parse_panes("") == []


def test_color_only_when_stdout_is_a_terminal(monkeypatch):
    """`--once` is piped, and a truecolor escape per token costs its reader a
    lot for nothing. Colour follows isatty, and NO_COLOR always wins."""
    class _Out:
        def __init__(self, tty):
            self._tty = tty

        def isatty(self):
            return self._tty

    for tty, novar, want in ((True, None, True), (False, None, False),
                             (True, "1", False), (False, "1", False)):
        monkeypatch.setattr(ao, "_COLOR", None)
        monkeypatch.setattr(ao.sys, "stdout", _Out(tty))
        if novar is None:
            monkeypatch.delenv("NO_COLOR", raising=False)
        else:
            monkeypatch.setenv("NO_COLOR", novar)
        assert ao.color_enabled() is want, (tty, novar)
        painted = ao.paint("hello", ao.GREEN, bold=True)
        assert ("\033[" in painted) is want
        assert "hello" in painted

    # A stdout with no isatty() at all must not crash the dashboard.
    monkeypatch.setattr(ao, "_COLOR", None)
    monkeypatch.setattr(ao.sys, "stdout", object())
    assert ao.color_enabled() is False


def test_build_frame_emits_no_ansi_when_not_a_terminal(monkeypatch):
    """The whole frame, not just one token — the claim `--once` actually makes."""
    monkeypatch.setattr(ao, "_COLOR", False)
    monkeypatch.setattr(ao, "read_json", lambda p: None)
    monkeypatch.setattr(ao, "list_tmux_panes_raw", _scratch12_raw)
    monkeypatch.setattr(ao, "build_proc_index", lambda: _SCRATCH12_PROC)
    monkeypatch.setattr(ao, "own_pid_chain", lambda: set())
    monkeypatch.setattr(ao, "load_scratch_codenames", lambda *a, **k: {})
    monkeypatch.setattr(ao, "read_fuzzyclaw_task_texts",
                        lambda *a, **k: _scratch12_activity(time.time()))
    monkeypatch.setattr(ao, "maybe_refresh_initiatives", lambda *a, **k: None)
    monkeypatch.setattr(ao, "maybe_refresh_prs", lambda *a, **k: None)
    monkeypatch.setattr(ao, "maybe_refresh_telemetry", lambda *a, **k: None)
    monkeypatch.setattr(ao, "enrich_clawgate_titles", lambda *a, **k: [])
    monkeypatch.setattr(ao, "fetch_failed_user_units", lambda *a, **k: None)
    monkeypatch.setattr(ao, "fetch_unit_show", lambda *a, **k: None)
    monkeypatch.setattr(ao, "read_uptime", lambda *a, **k: None)

    frame = ao.build_frame(100)
    assert "\033" not in frame
    # POSITIVE CONTROL: the same frame with colour ON is full of escapes, so the
    # clean result above is a fact about the switch, not about an empty render.
    assert "ACTIVE AGENT RUNS" in frame and "review-bombing" in frame
    monkeypatch.setattr(ao, "_COLOR", True)
    assert frame.count("\033") == 0 < ao.build_frame(100).count("\033")


def test_pane_format_and_parser_agree_field_for_field():
    """🔴 SEAM. The tmux `-F` format and `parse_panes` are one contract split in
    two, joined only by POSITION, and every way of breaking it is SILENT: drop
    `#{window_id}` from the format and the parser reads window_name into
    `window_id`, the age join misses on every row, and the column degrades to
    "—" with nothing red anywhere. So drive the real format through the real
    parser: substitute a DISTINCT sentinel per tmux variable, then assert each
    parsed key holds the sentinel of the variable it is supposed to carry.
    """
    fields = re.findall(r"#\{([a-z_]+)\}", ao.PANE_FORMAT)
    assert len(fields) == len(ao.PANE_FIELDS)
    assert "window_id" in fields                 # the age join's key

    line = ao.PANE_FORMAT
    for i, var in enumerate(fields):
        line = line.replace("#{%s}" % var, "1234" if var == "pane_pid"
                            else "V%d-%s" % (i, var))
    pane = ao.parse_panes(line)[0]
    for i, (var, key) in enumerate(zip(fields, ao.PANE_FIELDS)):
        want = 1234 if var == "pane_pid" else "V%d-%s" % (i, var)
        assert pane[key] == want, (
            "format field #%d %r landed in %r as %r" % (i, var, key, pane[key]))
    # …and the pairing is the one the age join needs, spelled out.
    assert dict(zip(ao.PANE_FIELDS, fields))["window_id"] == "window_id"
    assert dict(zip(ao.PANE_FIELDS, fields))["session"] == "session_name"


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
    # The /proc number is still carried — under its TRUE name, process uptime.
    assert s["proc_age_secs"] == 90
    # …and it is NOT the age column: with no activity index there is no proven
    # last-activity, so the row has none. (Rendering it as an idle time was the bug.)
    assert s["age_secs"] is None


def test_classify_detects_via_foreground_command_when_tree_missing():
    # No proc_index entry at all → falls back to pane_current_command == 'claude'.
    panes = [{"pane_id": "%0", "pane_pid": 7, "session": "s", "window_index": "1",
              "window_name": "w", "path": "/repo", "command": "claude"}]
    sessions = ao.classify_claude_sessions(panes, {}, root_resolver=lambda p: p)
    assert len(sessions) == 1
    assert sessions[0]["busy"] is None      # no proc info → unknown, not a crash


def test_classify_task_and_busy_from_pane_title():
    # busy is derived from the pane_title's leading glyph, task from the rest.
    panes = [
        {"pane_id": "%i", "pane_pid": 7, "session": "sa", "window_index": "1",
         "window_name": "devrc", "path": "/r1", "command": "claude",
         "title": "✳ Investigate remaining 500 errors"},        # sparkle → idle
        {"pane_id": "%b", "pane_pid": 8, "session": "sb", "window_index": "1",
         "window_name": "dp", "path": "/r2", "command": "claude",
         "title": "⠐ Trace and validate external app listing"},  # braille → busy
        {"pane_id": "%e", "pane_pid": 9, "session": "sc", "window_index": "1",
         "window_name": "dp", "path": "/r3", "command": "claude",
         "title": ""},                                            # empty → fallback
    ]
    sessions = ao.classify_claude_sessions(panes, {}, root_resolver=lambda p: p)
    by_pane = {s["pane_id"]: s for s in sessions}
    assert by_pane["%i"]["task"] == "Investigate remaining 500 errors"
    assert by_pane["%i"]["busy"] is False        # ✳ sparkle = idle/awaiting
    assert by_pane["%b"]["task"] == "Trace and validate external app listing"
    assert by_pane["%b"]["busy"] is True         # braille spinner = running
    assert by_pane["%e"]["task"] == ""           # empty title → caller falls back
    assert by_pane["%e"]["busy"] is None         # no glyph, no proc info → unknown


def test_strip_status_glyph_and_busy_from_title():
    assert ao.strip_status_glyph("✳ Foo bar") == "Foo bar"
    assert ao.strip_status_glyph("⠐ Foo bar") == "Foo bar"
    assert ao.strip_status_glyph("nixos") == "nixos"      # no glyph → unchanged
    assert ao.strip_status_glyph("") == ""
    assert ao.strip_status_glyph(None) == ""
    assert ao.busy_from_title("⠂ working") is True        # braille spinner
    assert ao.busy_from_title("✳ idle") is False          # sparkle
    assert ao.busy_from_title("plain title") is None      # no glyph
    assert ao.busy_from_title("") is None


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
# PER-WINDOW AGE — index_window_activity / window_activity_age / classify
#
# 🔴 REGRESSION. The age column used to render the Claude PROCESS's uptime
# (/proc starttime). Measured on the workbench 2026-08-11, session `scratch12`:
#
#   window  window_id  claude proc uptime   real last_activity   rendered
#   ------  ---------  ------------------   -----------------    --------
#   :1 ▶    @4         4.97d                ~45m ago             4d  ❌
#   :2 ●    @16        4.87d                ~4.79d ago           4d  ✅
#
# Two windows of one session launched in the same burst have near-identical
# process uptimes, so BOTH rows printed one age — and the busy row read as "▶ …
# 4d", simultaneously running and four days abandoned. A dogfooding agent read
# that and told the operator to treat a live window as dead. The fixture below
# reproduces those exact numbers: the two proc uptimes are DISTINCT yet both
# round to "4d", so an implementation that reuses them cannot pass by accident.
# ---------------------------------------------------------------------------
_SCRATCH12_PANES = [
    {"pane_id": "%4", "pane_pid": 4083112, "session": "scratch12",
     "window_index": "1", "window_id": "@4", "window_name": "dp",
     "path": "/home/zach/ws/dp", "command": "zsh",
     "title": "⠐ Continue review-bombing and co-cry work"},      # braille → busy
    {"pane_id": "%16", "pane_pid": 653899, "session": "scratch12",
     "window_index": "2", "window_id": "@16", "window_name": "probe",
     "path": "/home/zach/ws/probe", "command": "zsh",
     "title": "✳ Run bitdex probe on ImageTechnique"},           # sparkle → idle
]

_SCRATCH12_PROC = {
    4083112: {"comm": "zsh", "ppid": 1, "state": "S", "age_secs": 429500,
              "children": [3173016]},
    3173016: {"comm": ".claude-wrapped", "ppid": 4083112, "state": "S",
              "age_secs": 429408, "children": []},               # 4.97d → "4d"
    653899: {"comm": "zsh", "ppid": 1, "state": "S", "age_secs": 420900,
             "children": [2888458]},
    2888458: {"comm": ".claude-wrapped", "ppid": 653899, "state": "S",
              "age_secs": 420768, "children": []},               # 4.87d → "4d"
}

_BUSY_AGE, _IDLE_AGE = 2730.0, 413912.0        # 45m ago vs 4.79d ago


def _scratch12_activity(now):
    """The two fuzzyclaw task files backing scratch12:1 and :2, as raw bodies."""
    def iso(delta):
        return (datetime.datetime.fromtimestamp(now - delta)
                .astimezone().isoformat())
    return [
        json.dumps({"window_id": "@4", "tmux_session": "scratch12",
                    "window_index": 1, "last_activity": iso(_BUSY_AGE),
                    "task": "dp", "status": "paused"}),
        json.dumps({"window_id": "@16", "tmux_session": "scratch12",
                    "window_index": 2, "last_activity": iso(_IDLE_AGE),
                    "task": "probe", "status": "paused"}),
    ]


def test_classify_ages_are_per_window_not_process_uptime():
    now = 1_786_499_288.0
    index = ao.index_window_activity(_scratch12_activity(now))
    rows = ao.classify_claude_sessions(
        _SCRATCH12_PANES, _SCRATCH12_PROC, root_resolver=lambda p: p,
        activity_index=index, now=now)
    by_win = {r["window_id"]: r for r in rows}
    assert set(by_win) == {"@4", "@16"}
    # Each window carries ITS OWN last-activity, to the second.
    assert round(by_win["@4"]["age_secs"]) == 2730
    assert round(by_win["@16"]["age_secs"]) == 413912
    assert by_win["@4"]["age_secs"] != by_win["@16"]["age_secs"]
    # …and they render as visibly different ages — the whole point.
    assert ao.rel_age(by_win["@4"]["age_secs"]) == "45m"
    assert ao.rel_age(by_win["@16"]["age_secs"]) == "4d"
    # The busy row is no longer self-contradictory: running AND minutes fresh.
    assert by_win["@4"]["busy"] is True
    # The process uptimes are still there, under their true name — DISTINCT
    # values that both collapse to "4d", which is what made the bug look
    # per-session rather than per-quantity.
    assert by_win["@4"]["proc_age_secs"] == 429408
    assert by_win["@16"]["proc_age_secs"] == 420768
    assert ao.rel_age(429408) == ao.rel_age(420768) == "4d"


def _scratch12_raw():
    """The same two panes as a raw `tmux list-panes -a` dump."""
    return "\n".join(
        "|".join([p["pane_id"], str(p["pane_pid"]), p["session"],
                  p["window_index"], p["window_id"], p["window_name"],
                  p["path"], p["command"], p["title"]])
        for p in _SCRATCH12_PANES)


def test_build_frame_renders_per_window_ages_end_to_end(monkeypatch):
    """🔴 THE regression test, driven through `build_frame` — the whole render.

    It injects ONLY seams that predate the fix (`list_tmux_panes_raw`,
    `build_proc_index`, `own_pid_chain`), so pre-change code runs end-to-end and
    prints its own answer: the process uptimes, i.e. "4d" on BOTH rows,
    including the busy one. That is the reported defect verbatim, and it is what
    this assertion fails on at the base commit.
    """
    now = time.time()
    monkeypatch.setattr(ao, "read_json", lambda p: None)
    monkeypatch.setattr(ao, "list_tmux_panes_raw", _scratch12_raw)
    monkeypatch.setattr(ao, "build_proc_index", lambda: _SCRATCH12_PROC)
    monkeypatch.setattr(ao, "own_pid_chain", lambda: set())
    monkeypatch.setattr(ao, "load_scratch_codenames", lambda *a, **k: {})
    monkeypatch.setattr(ao, "read_fuzzyclaw_task_texts",
                        lambda *a, **k: _scratch12_activity(now), raising=False)
    monkeypatch.setattr(ao, "maybe_refresh_initiatives", lambda *a, **k: None)
    monkeypatch.setattr(ao, "maybe_refresh_prs", lambda *a, **k: None)
    monkeypatch.setattr(ao, "maybe_refresh_telemetry", lambda *a, **k: None)
    monkeypatch.setattr(ao, "enrich_clawgate_titles", lambda *a, **k: [])
    monkeypatch.setattr(ao, "fetch_failed_user_units", lambda *a, **k: None)
    monkeypatch.setattr(ao, "fetch_unit_show", lambda *a, **k: None)
    monkeypatch.setattr(ao, "read_uptime", lambda *a, **k: None)

    body = plain(ao.build_frame(100)).splitlines()
    busy = next(ln for ln in body if "review-bombing" in ln)
    idle = next(ln for ln in body if "bitdex" in ln)
    assert busy.split()[-1] == "45m", busy   # ← "4d" before the fix
    assert idle.split()[-1] == "4d", idle
    assert busy.split()[-1] != idle.split()[-1]


def test_index_window_activity_keys_by_window_id_and_tolerates_junk():
    idx = ao.index_window_activity([
        json.dumps({"window_id": "@4", "tmux_session": "s", "window_index": 1,
                    "last_activity": "2026-08-11T19:08:55-05:00"}),
        "{not json",                       # unparseable → skipped
        json.dumps([1, 2, 3]),             # not a dict → skipped
        json.dumps({"tmux_session": "s"}),  # no window_id → skipped
        json.dumps({"window_id": "", "tmux_session": "s"}),   # blank → skipped
    ])
    assert set(idx) == {"@4"}
    assert idx["@4"]["session"] == "s" and idx["@4"]["window_index"] == 1
    assert ao.index_window_activity([]) == {}
    assert ao.index_window_activity(None) == {}


def test_index_window_activity_ambiguous_claim_fails_closed():
    same = {"window_id": "@4", "tmux_session": "s", "window_index": 1,
            "last_activity": "2026-08-11T19:08:55-05:00"}
    other = dict(same, last_activity="2026-08-01T00:00:00-05:00")
    # A byte-identical duplicate carries the same answer — not a conflict.
    assert ao.index_window_activity([json.dumps(same),
                                     json.dumps(same)])["@4"] == {
        "session": "s", "window_index": 1,
        "last_activity": "2026-08-11T19:08:55-05:00"}
    # Two files DISAGREEING about one window id → ambiguous → no age at all.
    idx = ao.index_window_activity([json.dumps(same), json.dumps(other)])
    assert idx["@4"] is None
    pane = {"window_id": "@4", "session": "s", "window_index": "1"}
    assert ao.window_activity_age(pane, idx, now=1_786_499_288.0) is None


def test_window_activity_age_requires_session_and_index_to_agree():
    now = 1_786_499_288.0
    ts = datetime.datetime.fromtimestamp(now - 600).astimezone().isoformat()
    idx = ao.index_window_activity([json.dumps(
        {"window_id": "@4", "tmux_session": "scratch12", "window_index": 1,
         "last_activity": ts})])
    ok = {"window_id": "@4", "session": "scratch12", "window_index": "1"}
    assert round(ao.window_activity_age(ok, idx, now=now)) == 600

    # Each guard is reached on its OWN: the session check with a MATCHING index,
    # the index check with a MATCHING session — neither can shadow the other.
    wrong_session = dict(ok, session="scratch9")      # index still 1
    assert ao.window_activity_age(wrong_session, idx, now=now) is None
    wrong_index = dict(ok, window_index="2")          # session still scratch12
    assert ao.window_activity_age(wrong_index, idx, now=now) is None
    # An id nobody claims, and an empty/absent index.
    assert ao.window_activity_age(dict(ok, window_id="@99"), idx, now=now) is None
    assert ao.window_activity_age(ok, {}, now=now) is None
    assert ao.window_activity_age(ok, None, now=now) is None


def test_window_activity_age_unparseable_and_future_timestamps():
    now = 1_786_499_288.0
    pane = {"window_id": "@4", "session": "s", "window_index": "1"}
    for bad in ("", "yesterday", None, 17864, "2026-13-45T99:99:99"):
        idx = ao.index_window_activity([json.dumps(
            {"window_id": "@4", "tmux_session": "s", "window_index": 1,
             "last_activity": bad})])
        assert ao.window_activity_age(pane, idx, now=now) is None
    # A clock-skewed FUTURE timestamp clamps to 0, never a negative age.
    future = datetime.datetime.fromtimestamp(now + 900).astimezone().isoformat()
    idx = ao.index_window_activity([json.dumps(
        {"window_id": "@4", "tmux_session": "s", "window_index": 1,
         "last_activity": future})])
    assert ao.window_activity_age(pane, idx, now=now) == 0.0


def test_render_active_runs_unknown_age_is_a_dash_not_a_number():
    rows = [{"pane_id": "%0", "repo": "devrc", "session": "main",
             "window_index": "1", "window_id": "@1", "window_name": "devrc",
             "task": "Ship it", "busy": True, "age_secs": None,
             "proc_age_secs": 429408}]
    body = "\n".join(plain(ao.render_active_runs(rows)))
    assert "Ship it" in body
    assert "—" in body
    # the process uptime must NOT leak into the column as if it were an idle time
    assert "4d" not in body


def test_read_fuzzyclaw_task_texts_reads_json_only_and_failsafe(tmp_path):
    (tmp_path / "4.json").write_text('{"window_id": "@4"}')
    (tmp_path / "notes.txt").write_text("ignore me")
    got = ao.read_fuzzyclaw_task_texts(str(tmp_path))
    assert got == ['{"window_id": "@4"}']
    assert ao.read_fuzzyclaw_task_texts(str(tmp_path / "nope")) == []


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
def test_render_active_runs_rows_show_task_and_glyph():
    sessions = [
        {"pane_id": "%0", "repo": "devrc", "session": "main", "window_index": "1",
         "window_name": "devrc", "task": "Investigate remaining 500 errors",
         "busy": True, "age_secs": 3600},
        {"pane_id": "%1", "repo": "homelab", "session": "scratch2", "window_index": "2",
         "window_name": "h", "task": "Wire up the exporter", "busy": False,
         "age_secs": 90},
    ]
    out = plain(ao.render_active_runs(sessions, {"scratch2": "Gold"}))
    body = "\n".join(out)
    assert "2 live Claude session(s)" in body
    # the ACTUAL task (from pane_title) is shown, plus the codename, plus age
    assert "Investigate remaining 500 errors" in body and "1h" in body
    assert "Gold" in body and "Wire up the exporter" in body


def test_render_active_runs_empty():
    out = plain(ao.render_active_runs([]))
    assert any("no live Claude sessions" in ln for ln in out)


def test_render_active_runs_maps_scratch_codenames():
    sessions = [
        {"pane_id": "%0", "repo": "devrc", "session": "scratch10", "window_index": "1",
         "window_name": "w", "task": "Ship the drafter", "busy": True, "age_secs": 60},
        {"pane_id": "%1", "repo": "homelab", "session": "8", "window_index": "2",
         "window_name": "w", "task": "Audit the cluster", "busy": False, "age_secs": 60},
    ]
    codenames = {"scratch10": "Nickel", "scratch2": "Gold"}
    out = plain(ao.render_active_runs(sessions, codenames))
    body = "\n".join(out)
    assert "Nickel" in body             # scratch10 → codename label
    assert "scratch10" not in body      # raw name gone
    assert "Ship the drafter" in body   # task text rendered
    assert "Audit the cluster" in body  # numbered session (8) still renders its task


def test_render_active_runs_task_falls_back_to_window_name():
    # a bare-shell / empty-title claude pane → task '' → falls back to window name
    sessions = [{"pane_id": "%0", "repo": "devrc", "session": "scratch10",
                 "window_index": "1", "window_name": "devrc", "task": "",
                 "busy": None, "age_secs": 1}]
    # empty codename map / None both fall back to the raw session label (never crash)
    for cn in ({}, None):
        body = "\n".join(plain(ao.render_active_runs(sessions, cn)))
        assert "scratch10" in body      # raw session label fallback
        assert "devrc" in body          # window-name task fallback


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
def _prs_cache():
    """Mock ~/.cache/agent-ops/open-prs.json — multiple repos, a draft, an empty
    repo (no PRs), all sourced from `gh pr list --json`."""
    return {"generated": 123, "repos": {
        "/home/zach/workspace/devrc": [
            {"number": 72, "title": "add editorconfig", "headRefName": "add-editorconfig",
             "isDraft": False, "reviewDecision": "", "createdAt": "2026-07-05T23:00:00Z"},
            {"number": 71, "title": "shellcheckrc", "headRefName": "feat/shellcheckrc",
             "isDraft": True, "reviewDecision": "", "createdAt": "2026-07-05T22:00:00Z"},
        ],
        "/home/zach/workspace/homelab-talos": [
            {"number": 10, "title": "bump image", "headRefName": "bump",
             "isDraft": False, "reviewDecision": "APPROVED", "createdAt": "2026-07-06T00:00:00Z"},
        ],
        "/home/zach/workspace/empty-repo": [],   # a repo with no open PRs
    }}


def test_flatten_open_prs_rows_sorted_newest_first():
    rows = ao.flatten_open_prs(_prs_cache())
    assert len(rows) == 3                       # empty repo contributes nothing
    # sorted by createdAt desc: homelab #10 (07-06) precedes the devrc PRs (07-05)
    assert rows[0]["repo"] == "homelab-talos" and rows[0]["number"] == 10
    assert rows[1]["number"] == 72 and rows[2]["number"] == 71
    assert rows[2]["draft"] is True and rows[2]["branch"] == "feat/shellcheckrc"
    assert rows[1]["repo"] == "devrc"


def test_flatten_open_prs_failsafe():
    assert ao.flatten_open_prs(None) == []          # no cache file
    assert ao.flatten_open_prs({}) == []
    assert ao.flatten_open_prs({"repos": "nope"}) == []
    # junk PR elements dropped, repo basename derived
    rows = ao.flatten_open_prs({"repos": {"/a/b": ["x", {"number": 1, "title": "ok"}]}})
    assert len(rows) == 1 and rows[0]["repo"] == "b" and rows[0]["title"] == "ok"


def test_pr_repo_dirs_reuses_scan_keys_with_fallback():
    scan = {"by_repo": {"/w/devrc": [], "/w/homelab": []}}
    assert set(ao.pr_repo_dirs(scan)) == {"/w/devrc", "/w/homelab"}
    assert ao.pr_repo_dirs(None, fallback=["/x"]) == ["/x"]
    assert ao.pr_repo_dirs({}) == [ao.DEVRC_DIR]        # no scan cache → devrc only


def test_refresh_prs_cache_skips_gh_error_repo(tmp_path, monkeypatch):
    import json as _json
    monkeypatch.setattr(ao, "CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(ao, "PRS_CACHE", str(tmp_path / "open-prs.json"))
    monkeypatch.setattr(ao, "PRS_LOCK", str(tmp_path / "prs.lock"))
    monkeypatch.setattr(ao, "ISCAN_CACHE", str(tmp_path / "iscan.json"))
    (tmp_path / "iscan.json").write_text(
        _json.dumps({"by_repo": {"/w/devrc": [], "/w/broken": []}}))
    (tmp_path / "prs.lock").write_text("")   # a live refresh lock, cleared on exit

    def fake_fetch(repo, timeout=ao.PRS_GH_TIMEOUT):
        if repo.endswith("broken"):
            return None                      # gh errored on this repo
        return [{"number": 9, "title": "t", "headRefName": "b",
                 "isDraft": False, "createdAt": "z"}]

    monkeypatch.setattr(ao, "fetch_repo_open_prs", fake_fetch)
    ao.refresh_prs_cache(now=1000)
    cache = ao.read_json(str(tmp_path / "open-prs.json"))
    assert set(cache["repos"].keys()) == {"/w/devrc"}   # broken repo skipped
    rows = ao.flatten_open_prs(cache)
    assert len(rows) == 1 and rows[0]["repo"] == "devrc"
    assert not os.path.exists(str(tmp_path / "prs.lock"))  # lock always dropped


def test_render_prs_lists_open_prs():
    rows = ao.flatten_open_prs(_prs_cache())
    out = plain(ao.render_prs(rows, "updated 1m ago"))
    body = "\n".join(out)
    assert "devrc #72" in body and "add editorconfig" in body
    assert "add-editorconfig" in body        # branch shown
    assert "draft" in body                   # #71 flagged as draft
    assert "updated 1m ago" in body


def test_render_prs_empty_and_missing_cache():
    assert any("no open PRs" in ln for ln in plain(ao.render_prs([])))
    # a missing cache file (read_json → None) degrades to the same empty section
    assert any("no open PRs" in ln
               for ln in plain(ao.render_prs(ao.flatten_open_prs(None))))


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


def test_freshness_one_shot_says_the_refresh_will_not_be_shown(tmp_path):
    """🔴 "refreshing…" promises a next frame. `--once` has none: the worker
    finishes AFTER the frame is printed, so the reader holds the old copy while
    a fresh one lands on disk. Measured: `updated 7d ago · refreshing…` printed
    beside cache files 20 seconds old."""
    cache = tmp_path / "c.json"
    lock = tmp_path / "l"
    cache.write_text("{}")
    lock.write_text("")
    note = ao.initiatives_freshness(cache=str(cache), lock=str(lock),
                                    one_shot=True)
    assert "stale; refresh kicked, re-run for current data" in note
    assert "refreshing" not in note          # the promise is gone, not reworded
    assert "updated" in note                 # the age it IS showing still stated
    # An absent cache is still named as such in one-shot mode.
    gone = ao.initiatives_freshness(cache=str(tmp_path / "nope.json"),
                                    lock=str(lock), one_shot=True)
    assert gone.startswith("no cache yet")


def test_freshness_defaults_to_the_module_one_shot_flag(tmp_path, monkeypatch):
    """The kwarg exists for tests; the RENDER path passes nothing, so the
    default has to follow the flag main() sets — otherwise the fix is inert."""
    cache = tmp_path / "c.json"
    lock = tmp_path / "l"
    cache.write_text("{}")
    lock.write_text("")
    monkeypatch.setattr(ao, "ONE_SHOT", True)
    assert "re-run for current data" in ao.initiatives_freshness(
        cache=str(cache), lock=str(lock))
    monkeypatch.setattr(ao, "ONE_SHOT", False)
    assert "refreshing" in ao.initiatives_freshness(
        cache=str(cache), lock=str(lock))


def test_main_once_and_pipe_set_one_shot(monkeypatch):
    """Both single-frame entry points announce themselves — and the interactive
    one must NOT, or the popup starts lying in the other direction."""
    seen = []
    monkeypatch.setattr(ao, "build_frame", lambda w: seen.append(ao.ONE_SHOT) or "")
    monkeypatch.setattr(ao, "_run_interactive", lambda: seen.append(ao.ONE_SHOT))

    class _Out:
        def __init__(self, tty):
            self._tty = tty

        def isatty(self):
            return self._tty

        def write(self, _s):
            return 0

        def flush(self):
            pass

    monkeypatch.setattr(ao, "ONE_SHOT", False)
    monkeypatch.setattr(ao.sys, "stdout", _Out(True))
    assert ao.main(["--once"]) == 0            # explicit flag, on a tty

    monkeypatch.setattr(ao, "ONE_SHOT", False)
    monkeypatch.setattr(ao.sys, "stdout", _Out(False))
    assert ao.main([]) == 0                    # piped stdout, no flag

    monkeypatch.setattr(ao, "ONE_SHOT", False)
    monkeypatch.setattr(ao.sys, "stdout", _Out(True))
    assert ao.main([]) == 0                    # interactive popup
    assert seen == [True, True, False]


# ---------------------------------------------------------------------------
# build_frame smoke test — must never raise even with everything missing
# ---------------------------------------------------------------------------
def test_build_frame_failsafe(monkeypatch):
    monkeypatch.setattr(ao, "read_json", lambda p: None)
    monkeypatch.setattr(ao, "list_tmux_panes_raw", lambda: "")
    monkeypatch.setattr(ao, "build_proc_index", lambda: {})
    monkeypatch.setattr(ao, "own_pid_chain", lambda: set())
    monkeypatch.setattr(ao, "read_fuzzyclaw_task_texts", lambda *a, **k: [])
    monkeypatch.setattr(ao, "maybe_refresh_initiatives", lambda *a, **k: None)
    monkeypatch.setattr(ao, "maybe_refresh_prs", lambda *a, **k: None)
    monkeypatch.setattr(ao, "maybe_refresh_telemetry", lambda *a, **k: None)
    monkeypatch.setattr(ao, "enrich_clawgate_titles", lambda *a, **k: [])
    # Local-health fetches must not touch systemctl / the network in the sandbox.
    monkeypatch.setattr(ao, "fetch_failed_user_units", lambda *a, **k: None)
    monkeypatch.setattr(ao, "fetch_unit_show", lambda *a, **k: None)
    monkeypatch.setattr(ao, "read_uptime", lambda *a, **k: None)
    frame = ao.build_frame(100)
    body = plain(frame)
    for section in ("BLOCKED ON ME", "ACTIVE AGENT RUNS", "IN FLIGHT",
                    "MOMENTUM", "HEALTH", "LOCAL HEALTH", "RECENTLY DONE"):
        assert section in body


# ---------------------------------------------------------------------------
# Local health — parse_failed_units / parse_systemctl_show / unit_health_row
# ---------------------------------------------------------------------------
def test_parse_failed_units_extracts_unit_column():
    raw = (
        "keylog.service       loaded failed failed X11 keystroke collector\n"
        "repo-cos.timer       loaded failed failed Weekly timer\n"
        "\n"
    )
    assert ao.parse_failed_units(raw) == ["keylog.service", "repo-cos.timer"]


def test_parse_failed_units_failsafe():
    assert ao.parse_failed_units(None) == []
    assert ao.parse_failed_units("") == []
    # a stray non-unit line (e.g. a legend) is ignored
    assert ao.parse_failed_units("0 loaded units listed.") == []


def test_parse_systemctl_show_keyvalues():
    raw = "LoadState=loaded\nActiveState=failed\nResult=exit-code\n"
    d = ao.parse_systemctl_show(raw)
    assert d["LoadState"] == "loaded"
    assert d["ActiveState"] == "failed"
    assert d["Result"] == "exit-code"
    assert ao.parse_systemctl_show(None) == {}


def test_unit_health_row_oneshot_success():
    show = {"LoadState": "loaded", "ActiveState": "inactive", "SubState": "dead",
            "Result": "success", "ExecMainExitTimestampMonotonic": "1000000"}
    r = ao.unit_health_row("repo-cos.service", "repo-cos", show, uptime=3601)
    assert r["present"] and r["ok"] is True and r["running"] is False
    # 3601s uptime - 1_000_000us(=1s) exit → ~3600s (1h) ago
    assert 3590 <= r["age_secs"] <= 3601


def test_unit_health_row_running_daemon_uses_start_age():
    show = {"LoadState": "loaded", "ActiveState": "active", "SubState": "running",
            "Result": "success", "ExecMainStartTimestampMonotonic": "1000000",
            "ExecMainExitTimestampMonotonic": "0"}
    r = ao.unit_health_row("activity-collector.service", "collector", show,
                           uptime=7201)
    assert r["running"] is True and r["ok"] is True
    assert 7190 <= r["age_secs"] <= 7201   # up ~2h (start age, not exit)


def test_unit_health_row_failed():
    show = {"LoadState": "loaded", "ActiveState": "failed", "SubState": "failed",
            "Result": "exit-code", "ExecMainExitTimestampMonotonic": "1000000"}
    r = ao.unit_health_row("keylog.service", "keylog", show, uptime=100)
    assert r["ok"] is False and r["result"] == "exit-code"


def test_unit_health_row_absent_when_not_found():
    r = ao.unit_health_row("mail-actions-archive.service", "mail-archive",
                           {"LoadState": "not-found"}, uptime=100)
    assert r["present"] is False and r["ok"] is None and r["age_secs"] is None
    # an empty show (systemctl failed) is also absent
    assert ao.unit_health_row("x.service", "x", {}, uptime=100)["present"] is False


def test_unit_health_row_never_run_unknown():
    # loaded but never started (oneshot before first fire): no exit ts, inactive.
    show = {"LoadState": "loaded", "ActiveState": "inactive", "SubState": "dead",
            "Result": "success", "ExecMainExitTimestampMonotonic": "0"}
    r = ao.unit_health_row("mail-actions-archive.service", "mail-archive", show,
                           uptime=None)
    assert r["present"] is True and r["age_secs"] is None


# ---------------------------------------------------------------------------
# Local health — render_local_health / _render_telemetry_line
# ---------------------------------------------------------------------------
def _row(label, **kw):
    base = {"unit": label + ".service", "label": label, "present": True,
            "ok": True, "running": False, "age_secs": 60, "result": "success",
            "active": "inactive"}
    base.update(kw)
    return base


def test_render_local_health_all_healthy():
    fresh = {"sources": {"zsh": 30, "tmux": 45, "keys": 5, "i3": 12,
                         "browser": 300, "claude": 90}, "generated": 0}
    out = plain(ao.render_local_health([], [_row("repo-cos")], fresh))
    text = "\n".join(out)
    assert "LOCAL HEALTH" in text
    assert "all user units healthy" in text
    assert "repo-cos" in text and "ok" in text
    # every expected source appears in the telemetry line
    for src in ("zsh", "tmux", "keys", "i3", "browser", "claude"):
        assert src in text


def test_render_local_health_failed_and_absent():
    out = plain(ao.render_local_health(
        ["keylog.service"],
        [_row("bad", ok=False, result="exit-code"),
         _row("gone", present=False, ok=None, age_secs=None)],
        None))
    text = "\n".join(out)
    assert "1 failed: keylog.service" in text
    assert "exit-code" in text
    assert "absent" in text
    # None cache → telemetry unreachable
    assert "unreachable" in text


def test_render_local_health_systemctl_na():
    out = plain(ao.render_local_health(None, [], {"error": "x", "generated": 0}))
    text = "\n".join(out)
    assert "systemctl n/a" in text
    assert "unreachable" in text


def test_render_telemetry_line_missing_source_shown_dim():
    # keys/browser absent from the result → shown as "keys —"/"browser —"
    line = plain(ao._render_telemetry_line(
        {"sources": {"zsh": 10, "tmux": 20, "i3": 5, "claude": 8},
         "generated": 0}))
    assert "keys —" in line and "browser —" in line
    assert "zsh" in line and "tmux" in line


def test_telemetry_freshness_excludes_machine_cadence_rows():
    """🔴 SEAM GUARD — this panel's freshness vs deadman's MACHINE_CADENCE.

    browser-bridge emits a heartbeat every 900s whether or not it can execute
    anything, so an unfiltered `max(ts)` reports a bridge with no extension
    attached as "fresh 15 min ago". Deleting the exclusion from the SQL used to
    survive the whole suite — nothing here ever read that query.

    The predicate is IMPORTED from deadman rather than re-spelled, so this
    asserts the relationship against the real tuple: every declared pair must
    appear in the emitted clause, and an empty tuple must emit nothing (there is
    then nothing to exclude).
    """
    # agent-ops resolves its siblings through DEVRC_DIR (HOME-based) — the right
    # thing in production, where the deployed script must read the real checkout,
    # and the same convention CHQUERY_PATH already uses. The nix check sandbox
    # has no $HOME/workspace/devrc, so point it at THIS tree's copy for the test
    # rather than changing how production resolves paths.
    dm_py = os.path.join(_HERE, "..", "collector", "deadman.py")
    spec = importlib.util.spec_from_file_location("_dm_seam", dm_py)
    DM = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(DM)

    orig_path = ao.DEADMAN_PATH
    try:
        ao.DEADMAN_PATH = dm_py
        clause = ao.machine_cadence_sql_filter()
    finally:
        ao.DEADMAN_PATH = orig_path
    assert DM.MACHINE_CADENCE, "deadman declares no machine-cadence pairs"
    assert clause.startswith("AND NOT ("), clause
    for src, kind in DM.MACHINE_CADENCE:
        assert "source = '%s' AND kind = '%s'" % (src, kind) in clause, clause
    # ... and it is genuinely derived, not a constant that happens to match.
    assert "heartbeat" in clause
    # EQUALITY, not containment. The loop above only proves every declared pair
    # APPEARS — a helper that ADDS a pair deadman never declared satisfies it,
    # and since the call-site assertion below binds the SQL to `clause`, both
    # sides would then be wrong in the same way and stay green. Over-excluding a
    # source drops real activity from the panel (it reads stale, not falsely
    # fresh — fail-loud, but still wrong). This still permits the legitimate
    # maintenance path: adding a real pair to MACHINE_CADENCE moves both sides.
    assert clause == "AND NOT (%s) " % DM.cadence_predicate_sql(
        DM.MACHINE_CADENCE), clause

    # 🔴 AND THE CALL SITE USES IT. Asserting the helper alone is not enough:
    # dropping `machine_cadence_sql_filter()` from the SQL survived a suite that
    # had this test in it, because nothing here read the query the function
    # actually builds. Drive the real function with a stubbed chquery and read
    # the SQL it sends.
    sent = {}

    class _FakeConn:
        fq_table = "activity.events"
        timeout = 1.0

        @staticmethod
        def from_env(_env):
            return _FakeConn()

    class _FakeClient:
        def __init__(self, _conn):
            pass

        @staticmethod
        def rows(sql):
            sent["sql"] = sql
            return [{"source": "zsh", "age_secs": 7}]

    class _FakeQ:
        CHConn = _FakeConn
        CHClient = _FakeClient

    orig = ao._load_chquery
    try:
        ao._load_chquery = lambda: _FakeQ
        ao.DEADMAN_PATH = dm_py          # same sandbox reason as above
        out = ao.query_telemetry_freshness()
    finally:
        ao._load_chquery = orig
        ao.DEADMAN_PATH = orig_path

    assert out == {"zsh": 7}, out
    # 🔴 SHAPE **AND** CONTENT, in one assertion. Each alone has a blind spot,
    # and picking one is not a trade — it is a hole:
    #   * presence-only (`clause in sql`) passes for a mangled concatenation
    #     ("INTERVAL 2 DAYAND NOT (...)") or a clause placed after GROUP BY,
    #     both invalid ClickHouse;
    #   * shape-only (`AND NOT \(.+?\)`) accepts ANY predicate body, so a call
    #     site excluding the WRONG pair — say zsh/cmd, which would delete real
    #     operator activity from this panel — passes.
    # Interpolating the helper's real output binds the emitted SQL to it.
    assert re.search(
        r"INTERVAL 2 DAY " + re.escape(clause.strip())
        + r" GROUP BY source ORDER BY source$", sent["sql"]), \
        ("the exclusion is missing, misplaced, or excludes a different pair than "
         "the helper produced: %s" % sent["sql"])


def test_machine_cadence_filter_renders_multiple_pairs_with_OR():
    """🔴 `NOT (A AND B)` excludes NOTHING, so an AND-joined predicate silently
    reverts this panel to reporting a dead bridge as fresh. Only reachable once a
    SECOND timer-driven emitter exists — which deadman's own note invites — so it
    must be pinned before that day, not after.

    The rendering now lives in deadman.cadence_predicate_sql and is merely
    wrapped here; this asserts the wrapper passes a multi-pair set through
    faithfully.
    """
    dm_py = os.path.join(_HERE, "..", "collector", "deadman.py")
    orig = ao.DEADMAN_PATH
    try:
        ao.DEADMAN_PATH = dm_py
        two = ao.machine_cadence_sql_filter(
            cadence=(("a", "beat"), ("b", "tick")))
        empty = ao.machine_cadence_sql_filter(cadence=())
    finally:
        ao.DEADMAN_PATH = orig

    assert "(source = 'a' AND kind = 'beat') OR (source = 'b' AND kind = 'tick')" in two, two
    assert " AND (source = 'b'" not in two, "cadence terms joined with AND: %s" % two
    # Empty set -> no clause at all, which must still leave valid SQL at the call
    # site (this branch is otherwise unreachable while MACHINE_CADENCE is
    # non-empty, so a mutant emitting "AND NOT () " survived without it).
    assert empty == "", repr(empty)


def test_deadman_path_points_at_the_real_sibling():
    """🟢 The constant itself is never exercised — the seam tests override it for
    the sandbox — so a wrong path would mean a permanent error marker on the
    telemetry panel with nothing to catch it. Unlike CHQUERY_PATH, this one is
    not pre-flighted in maybe_refresh_telemetry."""
    # The ROOT as well as the tail: an endswith() check passes for
    # "/nonexistent/scripts/collector/deadman.py". DEVRC_DIR is the module's own
    # notion of the checkout, so pinning the full join catches a repoint without
    # needing the path to exist (it does not, in the check sandbox).
    assert ao.DEADMAN_PATH == os.path.join(
        ao.DEVRC_DIR, "scripts", "collector", "deadman.py"), ao.DEADMAN_PATH
    assert os.path.exists(os.path.join(_HERE, "..", "collector", "deadman.py")), \
        "deadman.py is not where DEADMAN_PATH expects it relative to this repo"


def test_render_telemetry_line_no_rows():
    line = plain(ao._render_telemetry_line({"sources": {}, "generated": 0}))
    assert "telemetry off" in line or "no rows" in line


def test_render_local_health_never_raises_on_junk():
    # totally malformed inputs must degrade, never raise
    ao.render_local_health("junk", "junk", "junk")
    ao.render_local_health(None, None, None)
