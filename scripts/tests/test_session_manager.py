"""Unit tests for scripts/session-manager — the cross-host tmux/agent view.

🔴 HERMETIC BY CONSTRUCTION, AND PROVEN SO
------------------------------------------
This suite runs on a live workbench with ~45 real tmux windows holding the
operator's actual work, and ~400 fuzzyclaw task files. So NOTHING here may
reach the real world. Two mechanisms enforce that rather than asserting it:

  1. `_no_real_subprocess` (autouse) replaces `sm._default_runner` — the ONLY
     subprocess seam in the script — with a function that RAISES. Any test that
     forgets to inject a runner fails loudly instead of running `tmux`.
  2. `_no_real_socket` (autouse) replaces `sm.make_ch_client` with a raiser, so
     a forgotten `use_ch=False` cannot open an HTTP connection to ClickHouse.

`test_hermeticity_fixture_is_actually_installed` is the POSITIVE CONTROL on
both: a guard nobody has watched work is not a guard. It asserts the raisers
are in place AND that they really raise — because an autouse fixture that
silently failed to apply would leave every other test in this file free to
shell out, and the suite would still be green.

The section numbering below maps 1:1 onto `claudedocs/kickoff-session-manager.md`
§3, so a reader can check coverage against the spec without inferring it.
"""
from __future__ import annotations

import copy
import importlib.machinery
import importlib.util
import json
import os

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPT = os.path.normpath(os.path.join(_HERE, "..", "session-manager"))

# session-manager has no .py extension -> load it by explicit path.
_spec = importlib.util.spec_from_loader(
    "session_manager",
    importlib.machinery.SourceFileLoader("session_manager", _SCRIPT))
sm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sm)


# =========================================================================== #
# Hermeticity harness
# =========================================================================== #
class _Forbidden(RuntimeError):
    """Raised when a test reaches for the real world."""


@pytest.fixture(autouse=True)
def _no_real_subprocess(monkeypatch):
    def _boom(argv, timeout):
        raise _Forbidden(f"test tried to run a real subprocess: {argv!r}")
    monkeypatch.setattr(sm, "_default_runner", _boom)


@pytest.fixture(autouse=True)
def _no_real_socket(monkeypatch):
    def _boom(*a, **k):
        raise _Forbidden("test tried to build a real ClickHouse client")
    monkeypatch.setattr(sm, "make_ch_client", _boom)


def test_hermeticity_fixture_is_actually_installed():
    """POSITIVE CONTROL on the two autouse guards above.

    Without this, a fixture that stopped applying would disarm every safety
    claim in this file's docstring while the suite stayed green.
    """
    with pytest.raises(_Forbidden):
        sm._default_runner(["tmux", "kill-server"], 1)
    with pytest.raises(_Forbidden):
        sm.make_ch_client()


def test_the_default_ch_factory_is_resolved_at_CALL_time_not_bound_as_a_default():
    """🔴 The hole this test exists for was real in the first draft.

    `def gather(..., ch_client_factory=make_ch_client)` captures the ORIGINAL
    function at def time, so `monkeypatch.setattr(sm, "make_ch_client", ...)`
    would NOT be honoured on the one path with no injection point —
    `main()` -> `gather()`. The suite's "no real network" guard would have been
    bypassed exactly where it mattered, and every other test would still pass.

    So: call gather with CH enabled and NO factory, and prove the patched
    module attribute is what runs.
    """
    import inspect
    assert (inspect.signature(sm.gather).parameters["ch_client_factory"].default
            is None)
    report = base_gather(use_ch=True, ch_client_factory=None)
    assert report["clickhouse"]["status"] == "unavailable"
    assert "_Forbidden" in report["clickhouse"]["error"]
    assert "real ClickHouse client" in report["clickhouse"]["error"]


def test_the_module_under_test_is_the_real_script():
    """INSTRUMENT CHECK: the suite must be pointed at the shipped file.

    A suite that loads a stub, or a path that has moved, reports green about
    nothing at all.
    """
    assert os.path.basename(_SCRIPT) == "session-manager"
    assert os.path.isfile(_SCRIPT)
    assert os.access(_SCRIPT, os.X_OK), "session-manager must be executable"
    assert callable(sm.gather) and callable(sm.filter_live_tasks)


# =========================================================================== #
# FIXTURES — every field value is PAIRWISE DISTINCT (see §3 test 9)
# =========================================================================== #
NOW = 1786449600.0  # 2026-08-11T12:00:00Z, fixed so nothing depends on wall time

# Live task: window_id @41 exists in LIVE_WINDOW_IDS below.
TASK_LIVE = {
    "task": "task-alpha-text",
    "window_id": "@41",
    "tmux_session": "scratch7",
    "window_index": 3,
    "status": "waiting",
    "cwd": "/home/zach/workspace/repo-alpha",
    "claude_session": "11111111-2222-4333-8444-555555555555",
    "started": "2026-08-11T09:00:00+00:00",
    "last_activity": "2026-08-11T11:30:00+00:00",   # -> age 1800s at NOW
    "summary": "summary-alpha",
    "transcript_path": "/home/zach/.claude/projects/proj-alpha/alpha.jsonl",
}
# Stale task: window_id @997 is NOT live. Every other field is well-formed, so
# ONLY the intersection can reject it (that is what makes test 6 reachable).
TASK_STALE = {
    "task": "task-bravo-text",
    "window_id": "@997",
    "tmux_session": "scratch2",
    "window_index": 8,
    "status": "done",
    "cwd": "/home/zach/workspace/repo-bravo",
    "claude_session": "99999999-8888-4777-8666-333333333222",
    "started": "2026-06-05T19:25:50-05:00",
    "last_activity": "2026-06-05T19:31:20-05:00",
    "summary": "summary-bravo",
    "transcript_path": "/home/zach/.claude/projects/proj-bravo/bravo.jsonl",
}

LIVE_WINDOW_IDS = {"@41", "@52", "@63"}

BRAILLE = "⠙"   # busy spinner
SPARKLE = "✳"   # idle sparkle

WORKBENCH_PANES = "\n".join([
    f"%11|1001|scratch7|3|win-alpha|/home/zach/workspace/repo-alpha|claude"
    f"|{BRAILLE} Working on alpha",
    "%12|1002|scratch7|3|win-alpha|/home/zach/workspace/repo-alpha|zsh|zsh-title",
    "%13|1003|misc|5|win-charlie|/home/zach/tmp|zsh|plain charlie title",
])
LAPTOP_PANES = (
    f"%21|2001|naida-dev|1|win-delta|/home/zach/workspace/naida|claude"
    f"|{SPARKLE} Fix build on laptop"
)
WORKBENCH_WINDOW_IDS = "@41\n@52\n@63\n"
LAPTOP_WINDOW_IDS = "@7\n"

SLOT_TABLE = '\n'.join([
    'SCRATCH_SLOTS=(',
    '  "scratch7:S:#b8bb26:Grove"',
    '  "scratch2:T:#83a598:Vapor"',
    ')',
])


def make_runner(local_panes=WORKBENCH_PANES, local_windows=WORKBENCH_WINDOW_IDS,
                remote_panes=LAPTOP_PANES, remote_windows=LAPTOP_WINDOW_IDS,
                remote_rc=0, remote_err="", local_rc=0, local_err="",
                calls=None):
    """A fake `_default_runner`. Returns (rc, stdout, stderr); records argv."""
    calls = calls if calls is not None else []

    def runner(argv, timeout):
        calls.append(list(argv))
        remote = argv and argv[0] == "ssh"
        joined = " ".join(argv)
        windows = "list-windows" in joined
        if remote:
            if remote_rc != 0:
                return remote_rc, "", remote_err
            return 0, (remote_windows if windows else remote_panes), ""
        if local_rc != 0:
            return local_rc, "", local_err
        return 0, (local_windows if windows else local_panes), ""

    runner.calls = calls
    return runner


# Captured BEFORE any test monkeypatches sm.gather — otherwise a test that
# replaces gather() with a fake calling base_gather() recurses forever.
_REAL_GATHER = sm.gather


def base_gather(**kw):
    """gather() with every impure source injected. Never touches the machine."""
    defaults = dict(
        hosts=("workbench", "laptop"),
        local_host="workbench",
        runner=make_runner(),
        use_ch=False,
        use_fuzzyclaw=True,
        now=NOW,
        fuzzyclaw_texts=[json.dumps(TASK_LIVE), json.dumps(TASK_STALE)],
        codenames={"scratch7": "Grove", "scratch2": "Vapor"},
    )
    defaults.update(kw)
    return _REAL_GATHER(**defaults)


# =========================================================================== #
# §3.1 — parse_panes on the 8-field pipe format
# =========================================================================== #
def test_parse_panes_reads_all_eight_fields():
    panes = sm.parse_panes(
        "%9|4242|scratch4|2|devrc|/home/zach/workspace/devrc|claude|A task")
    assert panes == [{
        "pane_id": "%9", "pane_pid": 4242, "session": "scratch4",
        "window_index": "2", "window_name": "devrc",
        "path": "/home/zach/workspace/devrc", "command": "claude",
        "title": "A task",
    }]


def test_parse_panes_title_containing_pipes_is_not_truncated():
    """pane_title is LAST and carries the agent's task — which may contain '|'.
    A naive split would silently drop everything after the first pipe."""
    panes = sm.parse_panes(
        "%9|1|s|1|w|/p|claude|grep foo | wc -l | tee out")
    assert panes[0]["title"] == "grep foo | wc -l | tee out"


def test_parse_panes_empty_fields_do_not_crash_or_drop_the_row():
    panes = sm.parse_panes("%1|7|sess|0|||zsh|")
    assert len(panes) == 1
    assert panes[0]["path"] == ""
    assert panes[0]["command"] == "zsh"
    assert panes[0]["title"] == ""


def test_parse_panes_seven_fields_degrades_to_empty_title():
    panes = sm.parse_panes("%1|7|sess|0|win|/p|zsh")
    assert len(panes) == 1 and panes[0]["title"] == ""


@pytest.mark.parametrize("junk", [
    "",                       # blank line
    "not a pane line at all",
    "%1|notanumber|s|1|w|/p|zsh|t",   # pane_pid must be numeric
    "%1|2|too|few",
])
def test_parse_panes_drops_junk_without_raising(junk):
    assert sm.parse_panes(junk) == []


def test_parse_window_ids():
    assert sm.parse_window_ids("@1\n@5\n\n  @9  \n") == {"@1", "@5", "@9"}


# =========================================================================== #
# §3.2 — codename resolution via _SLOT_RE
# =========================================================================== #
def test_codenames_parsed_from_a_valid_slot_line():
    got = sm.load_scratch_codenames(paths=["/x"], reader=lambda p: SLOT_TABLE)
    assert got == {"scratch7": "Grove", "scratch2": "Vapor"}


@pytest.mark.parametrize("bad", [
    'SCRATCH_SLOTS=(\n  "scratch7:S:b8bb26:Grove"\n)',   # color missing '#'
    'SCRATCH_SLOTS=(\n  scratch7:S:#b8bb26:Grove\n)',    # unquoted
    'SCRATCH_SLOTS=(\n  "scratch7:S:#b8bb26"\n)',        # no codename field
    'nothing resembling a slot table',
])
def test_malformed_slot_line_yields_no_codenames_and_never_raises(bad):
    assert sm.load_scratch_codenames(paths=["/x"], reader=lambda p: bad) == {}


def test_codename_lookup_falls_through_to_the_next_path():
    seen = []

    def reader(path):
        seen.append(path)
        return None if path == "/deployed" else SLOT_TABLE

    got = sm.load_scratch_codenames(paths=["/deployed", "/repo"], reader=reader)
    assert seen == ["/deployed", "/repo"]
    assert got == {"scratch7": "Grove", "scratch2": "Vapor"}


# =========================================================================== #
# §3.3 — stale classification MEASURED AT TWO POINTS
# One point is not a claim about a threshold: a `>` and a `>=` implementation
# agree everywhere except the boundary, and a `<` inversion agrees nowhere
# except at one arbitrary sample. So the boundary AND the interior of both
# sides are pinned, and the named points are in the assertion text.
# =========================================================================== #
@pytest.mark.parametrize("threshold", [600, 3600])
def test_stale_threshold_at_the_boundary_and_well_either_side(threshold):
    # point 1: EXACTLY at the threshold -> stale (the boundary is inclusive)
    assert sm.classify_status(True, threshold, threshold) == "stale"
    # point 2: one second inside -> NOT stale, falls back to the busy glyph
    assert sm.classify_status(True, threshold - 1, threshold) == "busy"
    # point 3: deep inside the fresh side
    assert sm.classify_status(False, threshold / 10.0, threshold) == "idle"
    # point 4: deep outside
    assert sm.classify_status(False, threshold * 10.0, threshold) == "stale"


def test_stale_wins_over_the_busy_glyph():
    """A stale window still renders a spinner if the process hung — age wins."""
    assert sm.classify_status(True, 99999, 3600) == "stale"


def test_unknown_when_there_is_neither_a_glyph_nor_an_age():
    assert sm.classify_status(None, None, 3600) == "unknown"


def test_no_age_means_the_glyph_decides_not_stale():
    """age_secs is None for REMOTE windows (fuzzyclaw is local-only). A missing
    measurement must never be rendered as 'stale' — that is a fabricated fact."""
    assert sm.classify_status(True, None, 3600) == "busy"
    assert sm.classify_status(False, None, 3600) == "idle"


@pytest.mark.parametrize("value,expected", [
    ("2026-08-11T11:30:00+00:00", 1786447800.0),
    ("2026-08-11T11:30:00Z", 1786447800.0),
    ("garbage", None),
    ("", None),
    (None, None),
    (12345, None),
])
def test_parse_iso_epoch(value, expected):
    assert sm.parse_iso_epoch(value) == expected


# =========================================================================== #
# §3.4 / §3.5 / §3.6 — THE fuzzyclaw LIVE-WINDOW INTERSECTION
#
# 🔴 This is the load-bearing guard. Measured on the workbench 2026-08-11:
# 400 task files, 44 live windows, 43 intersect, 357 stale (89%), 0 unparseable.
# Without the intersection, 89% of every row emitted would describe a window
# that no longer exists.
# =========================================================================== #
def test_task_file_pointing_at_a_LIVE_window_is_included():
    res = sm.filter_live_tasks([json.dumps(TASK_LIVE)], LIVE_WINDOW_IDS)
    assert [t["window_id"] for t in res["tasks"]] == ["@41"]
    assert res["files_seen"] == 1 and res["files_live"] == 1


def test_task_file_pointing_at_a_DEAD_window_is_excluded():
    """§3 test 5 — the one the mutation test (test 6) must turn red.

    The assertion names the excluded window explicitly, so deleting the
    intersection produces `['@41', '@997'] == ['@41']` — a failure that names
    @997, i.e. THIS guard's failure and not some other check's error.
    """
    res = sm.filter_live_tasks(
        [json.dumps(TASK_LIVE), json.dumps(TASK_STALE)], LIVE_WINDOW_IDS)
    assert [t["window_id"] for t in res["tasks"]] == ["@41"]
    assert res["files_seen"] == 2
    assert res["files_live"] == 1
    assert res["files_unparseable"] == 0


def test_the_intersection_is_REACHABLE_and_is_the_only_thing_excluding_it():
    """§3 test 6 — the reachability half of the mutation test.

    A mutation test is worthless if an EARLIER check would have rejected the
    fixture anyway: the guard would never execute, and killing it would still
    look green. So: the byte-identical stale fixture is fed twice, and the ONLY
    difference between the two runs is whether its window_id is in the live set.
    Included in one, excluded in the other => nothing upstream rejects it, and
    the intersection is the sole cause of the exclusion.

    (The destructive half — deleting the guard and watching the test above go
    red — is performed by hand and reported in the PR; a suite cannot delete
    its own subject's source.)
    """
    body = json.dumps(TASK_STALE)
    excluded = sm.filter_live_tasks([body], LIVE_WINDOW_IDS)
    included = sm.filter_live_tasks([body], LIVE_WINDOW_IDS | {"@997"})
    assert excluded["files_live"] == 0
    assert included["files_live"] == 1
    assert included["tasks"][0]["window_id"] == "@997"
    # and it is well-formed by every OTHER standard, so no earlier check fires:
    parsed = json.loads(body)
    assert isinstance(parsed, dict)
    assert sm.FUZZYCLAW_FIELDS.issubset(set(parsed))


def test_an_empty_live_set_excludes_everything():
    """The degenerate direction: if tmux told us nothing, we claim nothing."""
    res = sm.filter_live_tasks(
        [json.dumps(TASK_LIVE), json.dumps(TASK_STALE)], set())
    assert res["tasks"] == [] and res["files_seen"] == 2


def test_measured_stale_ratio_shape_is_handled_at_scale():
    """A miniature of the real 400/43 distribution — the guard must scale and
    must not be accidentally quadratic on membership."""
    bodies = [json.dumps(dict(TASK_STALE, window_id=f"@{9000 + i}"))
              for i in range(357)]
    bodies += [json.dumps(dict(TASK_LIVE, window_id=f"@{i}"))
               for i in range(43)]
    live = {f"@{i}" for i in range(43)}
    res = sm.filter_live_tasks(bodies, live)
    assert (res["files_seen"], res["files_live"]) == (400, 43)


@pytest.mark.parametrize("body", [
    "{not json",
    "",
    "[]",             # valid JSON, wrong shape
    '"a string"',
    "null",
])
def test_unparseable_or_wrong_shaped_task_file_is_skipped_not_fatal(body):
    """§3 test 7."""
    res = sm.filter_live_tasks([body, json.dumps(TASK_LIVE)], LIVE_WINDOW_IDS)
    assert res["files_live"] == 1
    assert res["files_seen"] == 2
    assert res["files_unparseable"] == 1


def test_a_zero_from_no_files_is_distinguishable_from_a_zero_from_all_stale():
    """SILENT-ZERO, fuzzyclaw edition. Both produce `tasks == []`; only the
    counters say which happened."""
    none_at_all = sm.filter_live_tasks([], LIVE_WINDOW_IDS)
    all_stale = sm.filter_live_tasks([json.dumps(TASK_STALE)] * 5,
                                     LIVE_WINDOW_IDS)
    assert none_at_all["tasks"] == all_stale["tasks"] == []
    assert none_at_all["files_seen"] == 0
    assert all_stale["files_seen"] == 5
    assert none_at_all != all_stale


# --------------------------------------------------------------------------- #
# §3.8 — THE FIELD LEDGER (fails if the consumed key set GROWS or SHRINKS)
# --------------------------------------------------------------------------- #
# Typed here independently of the implementation. Measured against the live
# files 2026-08-11: 396/400 carry exactly these 11 keys; 4 predate
# `transcript_path`. The spec's §2.1 field list omitted transcript_path — the
# ledger exists so that kind of drift is loud.
EXPECTED_FUZZYCLAW_FIELDS = {
    "task", "window_id", "tmux_session", "window_index", "status", "cwd",
    "claude_session", "started", "last_activity", "summary", "transcript_path",
}


def test_field_ledger_is_exactly_this_set():
    assert set(sm.FUZZYCLAW_FIELDS) == EXPECTED_FUZZYCLAW_FIELDS, (
        "the consumed task-file key set changed. GROWTH widens the trust "
        "surface of a source CLAUDE.md marks UNTRUSTED; SHRINKAGE turns a real "
        "field into a permanent null. Either way, account for it here."
    )
    assert len(sm.FUZZYCLAW_FIELDS) == 11


def test_field_ledger_matches_what_the_code_ACTUALLY_READS():
    """Behavioural half — a constant can drift from the code that ignores it.

    A key-tracking mapping records every key `task_from_file_obj` touches, so
    growth or shrinkage is caught in the CODE, not just in the constant.
    """
    class Tracking(dict):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            self.read = set()

        def get(self, key, default=None):
            self.read.add(key)
            return super().get(key, default)

        def __getitem__(self, key):
            self.read.add(key)
            return super().__getitem__(key)

    probe = Tracking(TASK_LIVE)
    sm.task_from_file_obj(probe)
    assert probe.read == EXPECTED_FUZZYCLAW_FIELDS


def test_window_id_the_guard_keys_on_is_itself_in_the_ledger():
    """`filter_live_tasks` reads `window_id` before projecting. If that key
    ever left the ledger the guard would be silently comparing None."""
    assert "window_id" in sm.FUZZYCLAW_FIELDS


def test_task_projection_carries_every_value_through_unchanged():
    got = sm.task_from_file_obj(TASK_LIVE)
    assert got == {k: TASK_LIVE[k] for k in EXPECTED_FUZZYCLAW_FIELDS}


def test_task_projection_of_a_missing_key_is_None_not_a_KeyError():
    got = sm.task_from_file_obj({"window_id": "@1"})
    assert got["transcript_path"] is None
    assert set(got) == EXPECTED_FUZZYCLAW_FIELDS


# --------------------------------------------------------------------------- #
# §3.9 — fixtures are PAIRWISE DISTINCT, so a wrong-field bug cannot pass
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("fixture,label", [(TASK_LIVE, "TASK_LIVE"),
                                           (TASK_STALE, "TASK_STALE")])
def test_fixture_field_values_are_pairwise_distinct(fixture, label):
    values = [str(v) for v in fixture.values()]
    assert len(set(values)) == len(values), (
        f"{label} has two fields sharing a value; a transposed-field bug would "
        f"pass every assertion in this file")


def test_the_two_task_fixtures_share_no_value():
    assert not (set(map(str, TASK_LIVE.values()))
                & set(map(str, TASK_STALE.values())))


def test_index_tasks_by_window_keys_on_session_and_index_as_strings():
    idx = sm.index_tasks_by_window([TASK_LIVE, TASK_STALE])
    assert set(idx) == {("scratch7", "3"), ("scratch2", "8")}
    assert idx[("scratch7", "3")]["claude_session"] == TASK_LIVE["claude_session"]


def test_index_tasks_skips_entries_missing_a_join_key():
    assert sm.index_tasks_by_window([{"tmux_session": "s"}, {"window_index": 1}]) == {}


# =========================================================================== #
# §3.10 — the ClickHouse SQL is a PINNED CONTRACT
# The expected string below is typed from the kickoff brief, NOT derived from
# the implementation. Only whitespace runs are normalised.
# =========================================================================== #
EXPECTED_SQL = (
    "SELECT session, argMax(project, ingested_at) AS project, "
    "argMinIf(text, ts, kind = 'prompt') AS first_msg, max(ts) AS last_seen "
    "FROM activity.events "
    "WHERE source IN ('claude','opencode') AND ts > now() - INTERVAL 1 DAY "
    "GROUP BY session ORDER BY last_seen DESC LIMIT 20"
)


def _norm(sql):
    return " ".join((sql or "").split())


def test_recent_sessions_sql_is_the_verified_contract():
    assert _norm(sm.SQL_RECENT_SESSIONS) == _norm(EXPECTED_SQL)


def test_recent_sessions_sql_does_not_reference_the_nonexistent_column():
    """There is no `first_message` column in activity.events — the original
    draft's query failed outright with `Code: 47 … UNKNOWN_IDENTIFIER`. The
    first prompt is RECONSTRUCTED with argMinIf."""
    assert "first_message" not in sm.SQL_RECENT_SESSIONS
    assert "argMinIf(text, ts, kind = 'prompt')" in sm.SQL_RECENT_SESSIONS


def test_session_history_sql_quotes_the_session_id():
    sql = sm.sql_session_history("abc'; DROP TABLE events; --")
    # chquery.sql_quote backslash-escapes the embedded quote, so the injected
    # statement can never close the literal and become SQL.
    assert "abc\\'; DROP TABLE events; --" in sql
    assert "session = 'abc\\'" in sql


def test_session_history_sql_limit_is_an_int_not_interpolated_text():
    assert "LIMIT 5" in sm.sql_session_history("s", limit=5)


# =========================================================================== #
# §3.11 — THE SILENT-ZERO GUARD
# "0 rows" and "the query never answered" are different facts. They must be
# distinguishable in BOTH output modes.
# =========================================================================== #
class FakeCH:
    def __init__(self, rows=None, raise_=None):
        self._rows, self._raise = rows or [], raise_

    def rows(self, sql):
        if self._raise is not None:
            raise self._raise
        return list(self._rows)


CH_ROW = {"session": "11111111-2222-4333-8444-555555555555",
          "project": "devrc", "first_msg": "Add session-manager",
          "last_seen": "2026-08-11 11:59:00"}


def test_ch_genuinely_empty_is_status_ok_with_no_rows():
    res = sm.ch_query(FakeCH(rows=[]), "SELECT 1")
    assert res["status"] == "ok" and res["rows"] == [] and res["error"] is None


def test_ch_unreachable_is_NOT_an_empty_result():
    err = sm._chq.CHUnreachable("URLError: [Errno 111] Connection refused")
    res = sm.ch_query(FakeCH(raise_=err), "SELECT 1")
    assert res["status"] == "unreachable"
    assert res["rows"] == []
    assert "Connection refused" in res["error"]


def test_ch_query_error_code_47_surfaces_with_its_code():
    err = sm._chq.CHQueryError(
        "ClickHouse HTTP 500: Code: 47. DB::Exception: Unknown identifier "
        "first_message", code=47)
    res = sm.ch_query(FakeCH(raise_=err), "SELECT first_message FROM x")
    assert res["status"] == "query_error"
    assert res["code"] == 47
    assert "Code: 47" in res["error"]
    assert res["rows"] == []


def test_ch_three_outcomes_are_mutually_distinguishable():
    empty = sm.ch_query(FakeCH(rows=[]), "q")
    unreach = sm.ch_query(FakeCH(raise_=sm._chq.CHUnreachable("down")), "q")
    bad = sm.ch_query(FakeCH(raise_=sm._chq.CHQueryError("Code: 47", code=47)), "q")
    assert len({empty["status"], unreach["status"], bad["status"]}) == 3
    assert empty["rows"] == unreach["rows"] == bad["rows"] == []


@pytest.mark.parametrize("outcome,needle", [
    ("empty", "0 rows"),
    ("unreachable", "QUERY FAILED [unreachable]"),
    ("query_error", "QUERY FAILED [query_error]"),
])
def test_table_mode_distinguishes_the_three_ch_outcomes(outcome, needle):
    """SILENT-ZERO in the TABLE renderer — the mode a human actually reads."""
    if outcome == "empty":
        ch = sm.ch_query(FakeCH(rows=[]), "q")
    elif outcome == "unreachable":
        ch = sm.ch_query(FakeCH(raise_=sm._chq.CHUnreachable("boom")), "q")
    else:
        ch = sm.ch_query(FakeCH(raise_=sm._chq.CHQueryError("Code: 47", code=47)), "q")
    report = base_gather()
    report["clickhouse"] = dict(ch, sql="q")
    text = sm.render_table(report)
    assert needle in text
    if outcome != "empty":
        assert "this is NOT zero sessions" in text


def test_json_mode_distinguishes_the_three_ch_outcomes():
    """SILENT-ZERO in JSON — the mode an agent actually parses."""
    statuses = set()
    for exc in (None, sm._chq.CHUnreachable("boom"),
                sm._chq.CHQueryError("Code: 47", code=47)):
        report = base_gather(use_ch=True,
                             ch_client_factory=lambda e=exc: FakeCH(raise_=e))
        blob = json.loads(json.dumps(report, default=str))
        statuses.add(blob["clickhouse"]["status"])
        if blob["clickhouse"]["status"] != "ok":
            assert blob["clickhouse"]["error"]
    assert statuses == {"ok", "unreachable", "query_error"}


def test_ch_client_that_cannot_even_be_built_is_status_unavailable():
    def boom():
        raise RuntimeError("CLICKHOUSE_URL not set")

    report = base_gather(use_ch=True, ch_client_factory=boom)
    assert report["clickhouse"]["status"] == "unavailable"
    assert "CLICKHOUSE_URL not set" in report["clickhouse"]["error"]
    assert report["clickhouse"]["rows"] == []


# =========================================================================== #
# §3.12 — --no-ch: the client is NEVER CONSTRUCTED (asserted, not inferred)
# =========================================================================== #
def test_no_ch_never_constructs_the_client():
    built = []

    def factory():
        built.append(1)
        return FakeCH(rows=[CH_ROW])

    report = base_gather(use_ch=False, ch_client_factory=factory)
    assert built == [], "a CH client was built despite --no-ch"
    assert report["clickhouse"]["status"] == "skipped"
    assert report["clickhouse"]["rows"] == []


def test_positive_control_the_factory_IS_called_when_ch_is_enabled():
    """The counterpart to the test above. A recorder that never fires proves
    nothing until it has been watched to fire."""
    built = []

    def factory():
        built.append(1)
        return FakeCH(rows=[CH_ROW])

    report = base_gather(use_ch=True, ch_client_factory=factory)
    assert built == [1]
    assert report["clickhouse"]["status"] == "ok"
    assert report["clickhouse"]["rows"] == [CH_ROW]


def test_cli_no_ch_flag_reaches_gather(monkeypatch):
    seen = {}

    def fake_gather(**kw):
        seen.update(kw)
        return base_gather()

    monkeypatch.setattr(sm, "gather", fake_gather)
    monkeypatch.setattr(sm, "local_host_label", lambda *a, **k: "workbench")
    sm.main(["scan", "--json", "--no-ch"])
    assert seen["use_ch"] is False
    seen.clear()
    sm.main(["scan", "--json"])
    assert seen["use_ch"] is True


# =========================================================================== #
# §3.13 — THE SSH TARGET LITERAL
# 🔴 `10.42.0.10` is the homelab GATEWAY. Pointing at it does not fail: SSH
# succeeds against a real host and reports the gateway's tmux state as the
# laptop's. Only a literal catches that.
# =========================================================================== #
def test_laptop_ssh_target_is_the_laptop_not_the_homelab_gateway():
    assert sm.LAPTOP_SSH_TARGET == "zach@10.42.0.100"
    user, _, host = sm.LAPTOP_SSH_TARGET.partition("@")
    assert user == "zach"
    # equality, NOT `"10.42.0.10" not in target` — that substring test passes
    # for the correct value too and would be a guard that cannot fail.
    assert host == "10.42.0.100"
    assert host != "10.42.0.10"


def test_ssh_wrap_puts_the_pinned_target_in_the_argv():
    argv = sm.ssh_wrap(["tmux", "list-panes"])
    assert argv[0] == "ssh"
    assert "zach@10.42.0.100" in argv
    assert argv.index("zach@10.42.0.100") == len(argv) - 2


def test_ssh_wrap_quotes_the_tmux_format_for_the_remote_shell():
    """The remote side runs a SHELL: the format string's `{`/`}`/`#`/`|` must
    arrive quoted or the remote tmux receives a mangled -F argument."""
    argv = sm.ssh_wrap(list(sm.TMUX_PANES_ARGV))
    remote = argv[-1]
    assert sm.PANE_FORMAT in remote
    assert remote.startswith("tmux list-panes -a -F ")
    assert remote.rstrip().endswith(("'", '"')), "format was not quoted"


def test_ssh_uses_batchmode_so_a_prompt_can_never_hang_the_scan():
    assert "BatchMode=yes" in sm.SSH_OPTS
    assert any(o.startswith("ConnectTimeout=") for o in sm.SSH_OPTS)


def test_local_host_is_never_reached_over_ssh():
    runner = make_runner()
    base_gather(runner=runner, local_host="workbench")
    local_calls = [c for c in runner.calls if c[0] != "ssh"]
    ssh_calls = [c for c in runner.calls if c[0] == "ssh"]
    assert local_calls and ssh_calls
    assert all("zach@10.42.0.100" in c for c in ssh_calls)


# =========================================================================== #
# §3.14 / §3.15 — cross-host success and failure
# =========================================================================== #
def test_ssh_success_parses_remote_panes_and_tags_them_laptop():
    report = base_gather()
    laptop = report["hosts"]["laptop"]
    assert laptop["reachable"] is True
    assert laptop["error"] is None
    assert laptop["ssh_target"] == "zach@10.42.0.100"
    assert [w["session"] for w in laptop["windows"]] == ["naida-dev"]
    row = laptop["windows"][0]
    assert row["host"] == "laptop"
    assert row["task"] == "Fix build on laptop"
    assert row["claude"] is True
    assert row["busy"] is False
    assert row["status"] == "idle"


def test_ssh_failure_marks_the_laptop_unreachable_and_keeps_workbench_data():
    runner = make_runner(remote_rc=255,
                         remote_err="ssh: connect to host port 22: No route to host")
    report = base_gather(runner=runner)
    laptop = report["hosts"]["laptop"]
    assert laptop["reachable"] is False
    assert "No route to host" in laptop["error"]
    assert laptop["windows"] == []
    # partial data survives — the whole scan is not lost to one dead host
    assert report["hosts"]["workbench"]["reachable"] is True
    assert len(report["hosts"]["workbench"]["windows"]) == 2
    assert report["summary"]["hosts_unreachable"] == ["laptop"]
    assert report["summary"]["hosts_reachable"] == ["workbench"]


def test_unreachable_host_is_VISIBLE_in_both_output_modes_and_exits_zero():
    """§3 test 14. A host that vanishes silently is the 'reports nothing to do
    instead of erroring' failure mode."""
    runner = make_runner(remote_rc=255, remote_err="ssh: Connection timed out")
    report = base_gather(runner=runner)
    # JSON
    blob = json.loads(json.dumps(report, default=str))
    assert blob["hosts"]["laptop"]["reachable"] is False
    assert "timed out" in blob["hosts"]["laptop"]["error"]
    assert blob["summary"]["hosts_unreachable"] == ["laptop"]
    # TABLE
    text = sm.render_table(report)
    assert "LAPTOP: UNREACHABLE" in text
    assert "this is NOT zero windows" in text
    assert "UNREACHABLE HOSTS: laptop" in text
    # exit 0 with partial data
    assert sm.exit_code_for(report) == sm.EXIT_OK == 0


def test_ssh_timeout_exception_is_caught_as_unreachable_not_a_crash():
    def runner(argv, timeout):
        if argv[0] == "ssh":
            raise TimeoutError("timed out after 12s")
        return 0, WORKBENCH_PANES if "list-panes" in argv else WORKBENCH_WINDOW_IDS, ""

    report = base_gather(runner=runner)
    assert report["hosts"]["laptop"]["reachable"] is False
    assert "TimeoutError" in report["hosts"]["laptop"]["error"]


def test_tmux_not_running_is_REACHABLE_with_zero_windows():
    """🔴 The subtlest silent-zero here: `tmux` exits non-zero saying "no server
    running" when nothing is up. That is a MEASURED zero on a reachable host,
    not a failure to measure — and the two get different exit codes."""
    runner = make_runner(remote_rc=1, remote_err="no server running on /tmp/tmux-1000/default")
    report = base_gather(runner=runner)
    assert report["hosts"]["laptop"]["reachable"] is True
    assert report["hosts"]["laptop"]["error"] is None
    assert report["hosts"]["laptop"]["windows"] == []


def test_remote_fuzzyclaw_is_not_fabricated():
    """fuzzyclaw is LOCAL-only. A remote row must carry nulls, never the local
    host's task joined onto a same-named remote session."""
    panes = ("%31|3001|scratch7|3|win-alpha|/p|claude|"
             f"{BRAILLE} remote lookalike")
    report = base_gather(runner=make_runner(remote_panes=panes))
    row = report["hosts"]["laptop"]["windows"][0]
    assert row["fuzzyclaw"] is None
    assert row["claude_session_id"] is None
    assert row["age_secs"] is None


def test_host_filter_scans_only_the_requested_host():
    runner = make_runner()
    report = base_gather(hosts=("workbench",), runner=runner)
    assert set(report["hosts"]) == {"workbench"}
    assert not [c for c in runner.calls if c[0] == "ssh"]


# =========================================================================== #
# §3.16 — --json golden, with LITERAL expected values
# =========================================================================== #
def test_json_golden_schema_and_values():
    report = base_gather()
    blob = json.loads(json.dumps(report, default=str))

    assert blob["ts"] == "2026-08-11T12:00:00Z"
    assert blob["local_host"] == "workbench"
    assert blob["stale_threshold_secs"] == 3600
    assert set(blob) == {"ts", "local_host", "stale_threshold_secs", "hosts",
                         "clickhouse", "fuzzyclaw", "summary"}
    assert set(blob["hosts"]) == {"workbench", "laptop"}

    wb = blob["hosts"]["workbench"]
    assert set(wb) == {"reachable", "error", "ssh_target", "windows",
                       "live_window_ids"}
    assert wb["ssh_target"] is None
    assert wb["live_window_ids"] == ["@41", "@52", "@63"]

    row = wb["windows"][0]
    assert row == {
        "host": "workbench",
        "session": "scratch7",
        "window_index": "3",
        "window_name": "win-alpha",
        "codename": "Grove",
        "pane_id": "%11",
        "path": "/home/zach/workspace/repo-alpha",
        "command": "claude",
        "task": "Working on alpha",
        "claude": True,
        "busy": True,
        "age_secs": 1800.0,
        "status": "busy",
        "claude_session_id": "11111111-2222-4333-8444-555555555555",
        "fuzzyclaw": {
            "task": "task-alpha-text",
            "window_id": "@41",
            "tmux_session": "scratch7",
            "window_index": 3,
            "status": "waiting",
            "cwd": "/home/zach/workspace/repo-alpha",
            "claude_session": "11111111-2222-4333-8444-555555555555",
            "started": "2026-08-11T09:00:00+00:00",
            "last_activity": "2026-08-11T11:30:00+00:00",
            "summary": "summary-alpha",
            "transcript_path":
                "/home/zach/.claude/projects/proj-alpha/alpha.jsonl",
        },
        "panes": 2,
    }

    second = wb["windows"][1]
    assert (second["session"], second["window_index"]) == ("misc", "5")
    assert second["claude"] is False
    assert second["codename"] is None
    assert second["status"] == "unknown"
    assert second["age_secs"] is None
    assert second["fuzzyclaw"] is None

    assert blob["fuzzyclaw"] == {
        "status": "ok",
        "tasks": [dict(TASK_LIVE)],
        "files_seen": 2, "files_live": 1, "files_unparseable": 0,
    }
    assert blob["summary"] == {
        "total_sessions": 3, "claude": 2, "busy": 1, "idle": 1, "stale": 0,
        "unknown": 1,
        "hosts_reachable": ["laptop", "workbench"],
        "hosts_unreachable": [],
        "fuzzyclaw_live": 1,
    }


def test_two_panes_in_one_window_collapse_to_one_row_led_by_the_claude_pane():
    report = base_gather()
    wb = report["hosts"]["workbench"]["windows"]
    assert len(wb) == 2, "panes must fold to windows, not stay per-pane"
    assert wb[0]["panes"] == 2
    assert wb[0]["pane_id"] == "%11"          # the claude pane, not %12
    assert wb[0]["command"] == "claude"


def test_no_fuzzyclaw_flag_skips_the_source_and_says_so():
    report = base_gather(use_fuzzyclaw=False)
    assert report["fuzzyclaw"]["status"] == "skipped"
    assert report["fuzzyclaw"]["files_seen"] == 0
    row = report["hosts"]["workbench"]["windows"][0]
    assert row["fuzzyclaw"] is None and row["claude_session_id"] is None
    assert "skipped: --no-fuzzyclaw" in sm.render_table(report)


def test_stale_threshold_flows_from_the_argument_into_the_rows():
    """Measured at TWO thresholds against ONE fixture (age 1800s)."""
    fresh = base_gather(threshold=3600)
    stale = base_gather(threshold=1800)
    assert fresh["hosts"]["workbench"]["windows"][0]["status"] == "busy"
    assert stale["hosts"]["workbench"]["windows"][0]["status"] == "stale"
    assert fresh["summary"]["stale"] == 0
    assert stale["summary"]["stale"] == 1


# =========================================================================== #
# §3.17 — the table renderer survives the degenerate cases
# =========================================================================== #
def test_table_renders_zero_sessions_plus_an_unreachable_host():
    runner = make_runner(local_panes="", local_windows="",
                         remote_rc=255, remote_err="ssh: no route")
    report = base_gather(runner=runner)
    text = sm.render_table(report)
    assert "TMUX WINDOWS (0)" in text
    assert "LAPTOP: UNREACHABLE" in text
    assert "0 windows" in text


def test_table_says_NOTHING_WAS_MEASURED_when_no_host_answered():
    """The distinction the whole exit contract rests on, rendered for humans."""
    runner = make_runner(local_rc=255, local_err="tmux: command not found",
                         remote_rc=255, remote_err="ssh: no route")
    report = base_gather(runner=runner)
    text = sm.render_table(report)
    assert "no host answered — nothing was measured" in text
    assert "(none)" not in text


def test_table_renders_a_completely_empty_report_without_raising():
    empty = {"ts": None, "local_host": None, "hosts": {},
             "clickhouse": {}, "fuzzyclaw": {}, "summary": {}}
    assert isinstance(sm.render_table(empty), str)


def test_table_renders_ch_rows():
    report = base_gather(use_ch=True,
                         ch_client_factory=lambda: FakeCH(rows=[CH_ROW]))
    text = sm.render_table(report)
    assert "devrc" in text and "Add session-manager" in text


def test_table_does_not_crash_on_none_valued_fields():
    report = base_gather()
    row = report["hosts"]["workbench"]["windows"][0]
    for key in ("task", "codename", "status", "age_secs", "session"):
        mutated = copy.deepcopy(report)
        mutated["hosts"]["workbench"]["windows"][0][key] = None
        assert isinstance(sm.render_table(mutated), str)
    assert row  # the untouched original is still intact


# =========================================================================== #
# §3.18 — DISTINCT exit codes for "found nothing" vs "could not run"
# =========================================================================== #
def test_exit_codes_are_three_distinct_values():
    assert len({sm.EXIT_OK, sm.EXIT_EMPTY, sm.EXIT_UNAVAILABLE}) == 3
    assert sm.EXIT_OK == 0


def test_exit_ok_when_windows_were_found():
    assert sm.exit_code_for(base_gather()) == sm.EXIT_OK


def test_exit_empty_when_hosts_answered_with_a_real_zero():
    runner = make_runner(local_panes="", local_windows="",
                         remote_panes="", remote_windows="")
    report = base_gather(runner=runner)
    assert report["summary"]["hosts_reachable"] == ["laptop", "workbench"]
    assert sm.exit_code_for(report) == sm.EXIT_EMPTY


def test_exit_unavailable_when_NO_host_could_be_reached():
    runner = make_runner(local_rc=255, local_err="tmux: not found",
                         remote_rc=255, remote_err="ssh: no route")
    report = base_gather(runner=runner)
    assert report["summary"]["total_sessions"] == 0
    assert sm.exit_code_for(report) == sm.EXIT_UNAVAILABLE


def test_the_two_zeroes_produce_DIFFERENT_exit_codes():
    """The headline of the exit contract, asserted as one comparison."""
    ran_found_nothing = base_gather(
        runner=make_runner(local_panes="", local_windows="",
                           remote_panes="", remote_windows=""))
    could_not_run = base_gather(
        runner=make_runner(local_rc=255, local_err="x", remote_rc=255,
                           remote_err="y"))
    assert (ran_found_nothing["summary"]["total_sessions"]
            == could_not_run["summary"]["total_sessions"] == 0)
    assert sm.exit_code_for(ran_found_nothing) != sm.exit_code_for(could_not_run)


def test_exit_code_of_an_empty_hosts_map_is_unavailable_not_ok():
    assert sm.exit_code_for({"hosts": {}, "summary": {"total_sessions": 0}}) \
        == sm.EXIT_UNAVAILABLE


# =========================================================================== #
# tail — Phase 3, one-shot only
# =========================================================================== #
def test_tail_argv_is_a_read_only_capture_pane():
    argv = sm.tail_argv("scratch7:3", lines=50)
    assert argv == ["tmux", "capture-pane", "-t", "scratch7:3", "-p", "-e",
                    "-S", "-50"]
    # 🔴 nothing in this argv may WRITE to, signal, or kill a window.
    assert not ({"kill-window", "kill-session", "kill-server", "send-keys",
                 "kill-pane", "respawn-pane"} & set(argv))


@pytest.mark.parametrize("bad", [
    "no-colon", "", "   ", "scratch7:3; rm -rf /", "$(id):1", "a:b:c",
    "scratch7:3 && curl evil", "`whoami`:1", "sess:1|tee",
])
def test_tail_rejects_a_target_that_is_not_session_colon_window(bad):
    with pytest.raises(ValueError):
        sm.validate_target(bad)


@pytest.mark.parametrize("good", ["scratch7:3", "naida-dev:1", "s_1:%2",
                                  "a.b:0", "sess:win-name"])
def test_tail_accepts_wellformed_targets(good):
    assert sm.validate_target(good) == good


def test_tail_local_does_not_go_over_ssh():
    runner = make_runner()
    calls = []

    def rec(argv, timeout):
        calls.append(list(argv))
        return 0, "captured output\n", ""

    res = sm.tail_window("scratch7:3", "workbench", "workbench", runner=rec)
    assert res["text"] == "captured output\n"
    assert res["reachable"] is True
    assert calls[0][0] == "tmux"
    assert runner.calls == []


def test_tail_remote_goes_over_ssh_to_the_pinned_target():
    calls = []

    def rec(argv, timeout):
        calls.append(list(argv))
        return 0, "remote output\n", ""

    res = sm.tail_window("naida-dev:1", "laptop", "workbench", runner=rec)
    assert res["text"] == "remote output\n"
    assert calls[0][0] == "ssh"
    assert "zach@10.42.0.100" in calls[0]
    assert "capture-pane" in calls[0][-1]


def test_tail_failure_is_reported_not_rendered_as_empty_output():
    def rec(argv, timeout):
        return 1, "", "can't find window: nope"

    res = sm.tail_window("nope:9", "workbench", "workbench", runner=rec)
    assert res["reachable"] is False
    assert "can't find window" in res["error"]
    assert res["text"] == ""


# =========================================================================== #
# detail — narrowing must not erase reachability facts
# =========================================================================== #
def test_detail_filters_to_one_window():
    report = sm.filter_report(base_gather(), "scratch7", "3")
    rows = [r for h in report["hosts"].values() for r in h["windows"]]
    assert len(rows) == 1
    assert rows[0]["session"] == "scratch7"
    assert report["summary"]["total_sessions"] == 1


def test_detail_keeps_an_unreachable_host_visible():
    """Narrowing must not turn 'the laptop never answered' into 'not found'."""
    runner = make_runner(remote_rc=255, remote_err="ssh: no route")
    report = sm.filter_report(base_gather(runner=runner), "scratch7", "3")
    assert report["hosts"]["laptop"]["reachable"] is False
    assert report["summary"]["hosts_unreachable"] == ["laptop"]


def test_detail_of_a_nonexistent_window_is_an_empty_not_a_crash():
    report = sm.filter_report(base_gather(), "nosuch", "99")
    assert report["summary"]["total_sessions"] == 0
    assert sm.exit_code_for(report) == sm.EXIT_EMPTY


# =========================================================================== #
# CLI wiring
# =========================================================================== #
def test_cli_defaults_to_scan_over_all_hosts():
    args = sm.build_parser().parse_args([])
    assert args.subcommand == "scan"
    assert args.host == "all"
    assert args.stale_threshold == 3600
    assert args.json is False


def test_cli_rejects_an_unknown_subcommand():
    with pytest.raises(SystemExit):
        sm.build_parser().parse_args(["nuke"])


@pytest.mark.parametrize("forbidden", ["signal", "kill"])
def test_destructive_subcommands_are_NOT_implemented(forbidden):
    """Deliberately deferred (kickoff §1). This pins the deferral so a later
    edit cannot quietly add a window-killing verb to a read-only tool."""
    with pytest.raises(SystemExit):
        sm.build_parser().parse_args([forbidden, "scratch7:3"])
    assert not hasattr(sm, "kill_window")
    assert not hasattr(sm, "signal_window")


def test_main_tail_without_a_target_is_a_usage_error(capsys):
    assert sm.main(["tail"]) == sm.EXIT_USAGE
    assert "requires" in capsys.readouterr().err


def test_main_tail_with_a_malformed_target_is_a_usage_error(capsys):
    assert sm.main(["tail", "rm -rf /"]) == sm.EXIT_USAGE
    assert "<session>:<window>" in capsys.readouterr().err


def test_main_detail_without_a_target_is_a_usage_error(monkeypatch, capsys):
    monkeypatch.setattr(sm, "gather", lambda **kw: base_gather())
    monkeypatch.setattr(sm, "local_host_label", lambda *a, **k: "workbench")
    assert sm.main(["detail"]) == sm.EXIT_USAGE
    assert "requires" in capsys.readouterr().err


def test_main_json_prints_parseable_json_and_returns_the_exit_code(
        monkeypatch, capsys):
    monkeypatch.setattr(sm, "gather", lambda **kw: base_gather())
    monkeypatch.setattr(sm, "local_host_label", lambda *a, **k: "workbench")
    rc = sm.main(["scan", "--json", "--no-ch"])
    blob = json.loads(capsys.readouterr().out)
    assert blob["summary"]["total_sessions"] == 3
    assert rc == sm.EXIT_OK


def test_main_table_mode_prints_the_frame(monkeypatch, capsys):
    monkeypatch.setattr(sm, "gather", lambda **kw: base_gather())
    monkeypatch.setattr(sm, "local_host_label", lambda *a, **k: "workbench")
    rc = sm.main(["scan", "--no-ch"])
    out = capsys.readouterr().out
    assert "CROSS-HOST SESSION MANAGER" in out
    assert "TMUX WINDOWS (3)" in out
    assert rc == sm.EXIT_OK


def test_main_list_subcommand_skips_clickhouse(monkeypatch):
    seen = {}
    monkeypatch.setattr(sm, "gather",
                        lambda **kw: (seen.update(kw), base_gather())[1])
    monkeypatch.setattr(sm, "local_host_label", lambda *a, **k: "workbench")
    sm.main(["list", "--json"])
    assert seen["use_ch"] is False


def test_main_host_flag_narrows_the_host_tuple(monkeypatch):
    seen = {}
    monkeypatch.setattr(sm, "gather",
                        lambda **kw: (seen.update(kw), base_gather())[1])
    monkeypatch.setattr(sm, "local_host_label", lambda *a, **k: "workbench")
    sm.main(["scan", "--json", "--host", "laptop"])
    assert seen["hosts"] == ("laptop",)


def test_local_host_label_prefers_the_env_var():
    assert sm.local_host_label(env={"ACTIVITY_HOST": "laptop"}) == "laptop"
    assert sm.local_host_label(env={"ACTIVITY_HOST": "WORKBENCH"}) == "workbench"


def test_local_host_label_rejects_a_bogus_value_and_falls_back(tmp_path):
    envf = tmp_path / "env"
    envf.write_text("ACTIVITY_HOST=mars\n")
    assert sm.local_host_label(env={}, env_file=str(envf)) == "workbench"


def test_local_host_label_reads_the_collector_env_file(tmp_path):
    envf = tmp_path / "env"
    envf.write_text("# comment\nCLICKHOUSE_URL=x\nACTIVITY_HOST='laptop'\n")
    assert sm.local_host_label(env={}, env_file=str(envf)) == "laptop"


def test_local_host_label_defaults_to_workbench_with_no_sources(tmp_path):
    assert sm.local_host_label(env={},
                               env_file=str(tmp_path / "missing")) == "workbench"


# =========================================================================== #
# Misc pure helpers
# =========================================================================== #
@pytest.mark.parametrize("title,expected", [
    (f"{BRAILLE} Investigate 500s", "Investigate 500s"),
    (f"{SPARKLE} Waiting for input", "Waiting for input"),
    ("nixos", "nixos"),
    ("", ""),
    (None, ""),
    ("multi\nline title", "multi line title"),
])
def test_strip_status_glyph(title, expected):
    assert sm.strip_status_glyph(title) == expected


@pytest.mark.parametrize("title,expected", [
    (f"{BRAILLE} working", True),
    (f"{SPARKLE} idle", False),
    ("plain", None),
    ("", None),
    (None, None),
])
def test_busy_from_title(title, expected):
    assert sm.busy_from_title(title) is expected


@pytest.mark.parametrize("command,expected", [
    ("claude", True), ("Claude", True), (".claude-wrapped", True),
    ("zsh", False), ("", False), (None, False),
])
def test_pane_is_claude(command, expected):
    assert sm.pane_is_claude({"command": command}) is expected


@pytest.mark.parametrize("secs,expected", [
    (None, "—"), (0, "0s"), (59, "59s"), (60, "1m"), (3599, "59m"),
    (3600, "1h"), (86399, "23h"), (86400, "1d"),
])
def test_fmt_age(secs, expected):
    assert sm._fmt_age(secs) == expected


def test_read_fuzzyclaw_texts_returns_empty_for_a_missing_directory(tmp_path):
    assert sm.read_fuzzyclaw_texts(str(tmp_path / "nope")) == []


def test_read_fuzzyclaw_texts_reads_only_json_files(tmp_path):
    (tmp_path / "a.json").write_text('{"window_id": "@1"}')
    (tmp_path / "b.txt").write_text("ignored")
    got = sm.read_fuzzyclaw_texts(str(tmp_path))
    assert got == ['{"window_id": "@1"}']


def test_summarize_counts_every_status_bucket():
    report = base_gather()
    s = sm.summarize(report)
    assert s["busy"] + s["idle"] + s["stale"] + s["unknown"] == s["total_sessions"]
