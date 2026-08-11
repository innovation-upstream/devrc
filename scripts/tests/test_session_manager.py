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

# Live task: @41 is live AND holds scratch7:3 in LIVE_WINDOWS below,
# so BOTH halves of the guard accept it.
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

# 🔴 The live-window fact is now a MAPPING, not a set of ids: {window_id:
# (session, index)}. A bare set cannot answer "does @41 still sit in
# scratch7:3?", and that question is the whole guard — see the module header of
# session-manager. @41 is TASK_LIVE's window AND holds scratch7:3, so the
# relationship holds for it and only for it.
LIVE_WINDOWS = {"@41": ("scratch7", "3"),
                "@52": ("misc", "5"),
                "@63": ("other", "1")}

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
# `#{window_id}|#{window_index}|#{session_name}` — session LAST so it may
# contain '|'. Consistent with LIVE_WINDOWS above and with WORKBENCH_PANES.
WORKBENCH_WINDOWS = "@41|3|scratch7\n@52|5|misc\n@63|1|other\n"
LAPTOP_WINDOWS = "@7|1|naida-dev\n"

SLOT_TABLE = '\n'.join([
    'SCRATCH_SLOTS=(',
    '  "scratch7:S:#b8bb26:Grove"',
    '  "scratch2:T:#83a598:Vapor"',
    ')',
])


def make_runner(local_panes=WORKBENCH_PANES, local_windows=WORKBENCH_WINDOWS,
                remote_panes=LAPTOP_PANES, remote_windows=LAPTOP_WINDOWS,
                local_capture="local captured output\n",
                remote_capture="remote captured output\n",
                remote_rc=0, remote_err="", local_rc=0, local_err="",
                local_windows_rc=None, local_windows_err=None,
                remote_windows_rc=None, remote_windows_err=None,
                local_capture_rc=None, local_capture_err=None,
                remote_capture_rc=None, remote_capture_err=None,
                calls=None):
    """A fake `_default_runner` that answers PER SUBCOMMAND. Records argv.

    🔴 THE FIXTURE BLIND SPOT THIS EXISTS TO CLOSE. The first version returned
    ONE rc/stderr for every call on a host, so no test could distinguish the
    `list-panes` subprocess from the `list-windows` one. That is precisely why
    two mutants survived the audit's sweep with the suite green: reading a
    host's `reachable`, or its `error`, off the WRONG subprocess result changed
    nothing any fixture could see. A harness that cannot tell two calls apart
    cannot pin which one a fact came from — so it certifies nothing about that
    fact's provenance, however many tests are green.

    `*_windows_rc` / `*_capture_rc` (and their `_err` twins) default to the
    host-wide `*_rc` / `*_err`, so every existing call site keeps its exact
    meaning. Pass them to fail EXACTLY ONE subcommand.
    """
    calls = calls if calls is not None else []

    def _or(specific, general):
        return general if specific is None else specific

    table = {
        ("local", "panes"): (local_rc, local_panes, local_err),
        ("local", "windows"): (_or(local_windows_rc, local_rc), local_windows,
                               _or(local_windows_err, local_err)),
        ("local", "capture"): (_or(local_capture_rc, local_rc), local_capture,
                               _or(local_capture_err, local_err)),
        ("remote", "panes"): (remote_rc, remote_panes, remote_err),
        ("remote", "windows"): (_or(remote_windows_rc, remote_rc),
                                remote_windows,
                                _or(remote_windows_err, remote_err)),
        ("remote", "capture"): (_or(remote_capture_rc, remote_rc),
                                remote_capture,
                                _or(remote_capture_err, remote_err)),
    }

    def runner(argv, timeout):
        calls.append(list(argv))
        where = "remote" if (argv and argv[0] == "ssh") else "local"
        joined = " ".join(argv)
        what = ("windows" if "list-windows" in joined else
                "capture" if "capture-pane" in joined else "panes")
        rc, out, err = table[(where, what)]
        return (rc, "", err) if rc != 0 else (0, out, "")

    runner.calls = calls
    return runner


def test_make_runner_can_distinguish_the_two_subprocesses():
    """POSITIVE CONTROL on the fixture above — the instrument, before its verdict.

    The mutants that survived did so because this was NOT true. So prove it can
    go different ways for the two calls before trusting any test that relies on
    it: same host, same runner, one subcommand red and the other green.
    """
    runner = make_runner(local_windows_rc=1, local_windows_err="windows blew up")
    panes = runner(list(sm.TMUX_PANES_ARGV), 5)
    windows = runner(list(sm.TMUX_WINDOWS_ARGV), 5)
    assert panes[0] == 0 and panes[1] == WORKBENCH_PANES and panes[2] == ""
    assert windows[0] == 1 and windows[1] == ""
    assert windows[2] == "windows blew up"
    # ...and the mirror image, so neither direction is hardcoded.
    other = make_runner(local_rc=1, local_err="panes blew up",
                        local_windows_rc=0)
    assert other(list(sm.TMUX_PANES_ARGV), 5)[0] == 1
    assert other(list(sm.TMUX_WINDOWS_ARGV), 5)[1] == WORKBENCH_WINDOWS
    # capture-pane is a THIRD distinguishable call, not folded into panes
    cap = make_runner(local_capture="scrollback\n")
    assert cap(sm.tail_argv("scratch7:3"), 5)[1] == "scrollback\n"


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


# --------------------------------------------------------------------------- #
# 🔴 THE tmux -F CONTRACT FOR list-windows.
#
# A mutation sweep found this UNPINNED: reverting WINDOW_FORMAT to the old
# `'#{window_id}'` left the whole suite green, because every fixture feeds
# parse_windows a pre-rendered string and never goes through the format. Against
# a REAL tmux that revert makes every line unparseable, so `live_window_ids`
# empties, every task file looks stale, and the tool reports a confident,
# measured, wrong zero. Typed here independently of the implementation.
# --------------------------------------------------------------------------- #
EXPECTED_WINDOW_FORMAT = "#{window_id}|#{window_index}|#{session_name}"


def test_window_format_is_the_pinned_contract_with_tmux():
    assert sm.WINDOW_FORMAT == EXPECTED_WINDOW_FORMAT
    assert sm.TMUX_WINDOWS_ARGV == ("tmux", "list-windows", "-a", "-F",
                                    sm.WINDOW_FORMAT)


def test_the_window_format_and_its_parser_AGREE_ON_FIELD_ORDER():
    """🔴 STRUCTURAL, not spelled: render a line the way tmux would from THIS
    format string, then parse it. Format and parser are checked against each
    other, so neither can drift alone — an equality on the string would pass a
    matched pair of wrong edits, and this does not."""
    rendered = (sm.WINDOW_FORMAT
                .replace("#{window_id}", "@41")
                .replace("#{window_index}", "3")
                .replace("#{session_name}", "scratch7"))
    assert sm.parse_windows(rendered) == {"@41": ("scratch7", "3")}, (
        f"format {sm.WINDOW_FORMAT!r} renders {rendered!r}, which its own "
        "parser does not read back as scratch7:3")


def test_the_window_format_survives_the_ssh_quoting_it_must_pass_through():
    """It contains `{`, `}`, `#` and `|` and the remote side runs a SHELL."""
    argv = sm.ssh_wrap(list(sm.TMUX_WINDOWS_ARGV))
    remote = argv[-1]
    assert sm.WINDOW_FORMAT in remote
    assert remote.startswith("tmux list-windows -a -F ")
    assert remote.rstrip().endswith(("'", '"')), "format was not quoted"


def test_the_pane_and_window_formats_agree_on_the_SLOT_fields():
    """The join needs `(session_name, window_index)` from BOTH calls under the
    same names. If one format renamed a field the join would silently miss."""
    for field in ("#{session_name}", "#{window_index}"):
        assert field in sm.PANE_FORMAT
        assert field in sm.WINDOW_FORMAT
    assert "#{window_id}" in sm.WINDOW_FORMAT
    assert "#{window_id}" not in sm.PANE_FORMAT, (
        "if list-panes ever carried window_id, the join should use it directly "
        "instead of going through the slot — revisit index_tasks_by_window")


def test_parse_windows_carries_the_SLOT_not_just_the_id():
    """🔴 The value is the point. A set of ids cannot pin a relationship."""
    got = sm.parse_windows("@1|0|alpha\n@5|12|bravo\n\n  @9|3|charlie  \n")
    assert got == {"@1": ("alpha", "0"), "@5": ("bravo", "12"),
                   "@9": ("charlie", "3")}


def test_parse_windows_session_name_may_contain_pipes():
    """session_name is LAST and absorbs the remainder, same as pane_title."""
    assert sm.parse_windows("@4|2|weird|name") == {"@4": ("weird|name", "2")}


@pytest.mark.parametrize("bad", [
    "@1",              # id only — the OLD format; not enough to pin a slot
    "@1|3",            # no session
    "nonsense",
    "|3|alpha",        # no id
    "x1|3|alpha",      # id is not a tmux @n
    "@1||alpha",       # no index
    "",
])
def test_parse_windows_drops_a_line_that_cannot_pin_a_slot(bad):
    """A half-parsed window is not a window we can check a relationship against,
    so it is not reported live. Dropping it errs toward rejecting a task file,
    never toward attaching one to a window we could not verify."""
    assert sm.parse_windows(bad) == {}


def test_parse_window_ids_is_derived_from_the_one_parser():
    assert sm.parse_window_ids("@1|0|a\n@5|1|b\n") == {"@1", "@5"}
    assert sm.parse_window_ids(WORKBENCH_WINDOWS) == {"@41", "@52", "@63"}


def test_slots_to_window_ids_inverts_the_mapping():
    assert sm.slots_to_window_ids(LIVE_WINDOWS) == {
        ("scratch7", "3"): "@41", ("misc", "5"): "@52", ("other", "1"): "@63"}
    assert sm.slots_to_window_ids(None) == {}


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
    res = sm.filter_live_tasks([json.dumps(TASK_LIVE)], LIVE_WINDOWS)
    assert [t["window_id"] for t in res["tasks"]] == ["@41"]
    assert res["files_seen"] == 1 and res["files_live"] == 1
    assert (res["files_stale"], res["files_mismatched"]) == (0, 0)
    assert res["status"] == "ok"


def test_task_file_pointing_at_a_DEAD_window_is_excluded():
    """§3 test 5 — the one the mutation test (test 6) must turn red.

    The assertion names the excluded window explicitly, so deleting the
    intersection produces `['@41', '@997'] == ['@41']` — a failure that names
    @997, i.e. THIS guard's failure and not some other check's error.
    """
    res = sm.filter_live_tasks(
        [json.dumps(TASK_LIVE), json.dumps(TASK_STALE)], LIVE_WINDOWS)
    assert [t["window_id"] for t in res["tasks"]] == ["@41"]
    assert res["files_seen"] == 2
    assert res["files_live"] == 1
    assert res["files_unparseable"] == 0
    assert res["files_stale"] == 1        # @997 is gone, not merely moved
    assert res["files_mismatched"] == 0


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
    excluded = sm.filter_live_tasks([body], LIVE_WINDOWS)
    included = sm.filter_live_tasks(
        [body], dict(LIVE_WINDOWS, **{"@997": ("scratch2", "8")}))
    assert excluded["files_live"] == 0
    assert included["files_live"] == 1
    assert included["tasks"][0]["window_id"] == "@997"
    # and it is well-formed by every OTHER standard, so no earlier check fires:
    parsed = json.loads(body)
    assert isinstance(parsed, dict)
    assert sm.FUZZYCLAW_FIELDS.issubset(set(parsed))


def test_an_empty_live_set_excludes_everything():
    """The degenerate direction: tmux answered, and it has no windows."""
    res = sm.filter_live_tasks(
        [json.dumps(TASK_LIVE), json.dumps(TASK_STALE)], {})
    assert res["tasks"] == [] and res["files_seen"] == 2
    # MEASURED zero: status ok, count 0 — not the unmeasured case below.
    assert res["status"] == "ok" and res["files_live"] == 0


# --------------------------------------------------------------------------- #
# 🔴 THE RELATIONSHIP HALF OF THE GUARD (the audit's F1)
#
# Measured on the workbench 2026-08-11 with the OLD id-only guard: 43 of 400
# files survived it, but only 32 had a `window_id` that still resolved to the
# `(session, index)` the file recorded. 7 named a slot now held by a DIFFERENT
# live window, and 5 slots were claimed by more than one survivor (silent
# last-wins). Two rendered rows therefore carried another window's
# `claude_session_id` — the one carrier of the session id into ClickHouse.
# After the fix, the same host measures 32 live, 357 stale, 11 slot-mismatched.
# --------------------------------------------------------------------------- #
def test_a_LIVE_window_id_in_the_WRONG_slot_is_rejected():
    """🔴 The exact defect. @41 is alive, but it has been renumbered to
    scratch7:9 — so the task file's claim about scratch7:3 is about a window
    that is no longer there. Existence alone would have accepted it."""
    moved = dict(LIVE_WINDOWS, **{"@41": ("scratch7", "9")})
    res = sm.filter_live_tasks([json.dumps(TASK_LIVE)], moved)
    assert res["tasks"] == []
    assert res["files_mismatched"] == 1
    assert res["files_stale"] == 0, (
        "a moved window is NOT the same fact as a dead one; collapsing them "
        "hides a renumber storm")
    # and it IS accepted when the relationship holds — same file, same live id,
    # only the SLOT differs between the two runs. So nothing else rejects it.
    assert sm.filter_live_tasks([json.dumps(TASK_LIVE)],
                                LIVE_WINDOWS)["files_live"] == 1


def test_a_task_whose_SESSION_moved_is_rejected_even_though_the_index_matches():
    """Half a match is not a match: the index still says 3, the session does
    not. `renumber-windows` moves indexes, but a window can also be moved
    between sessions."""
    moved = dict(LIVE_WINDOWS, **{"@41": ("some-other-session", "3")})
    res = sm.filter_live_tasks([json.dumps(TASK_LIVE)], moved)
    assert res["tasks"] == [] and res["files_mismatched"] == 1


def test_the_index_comparison_is_string_normalised_not_type_sensitive():
    """fuzzyclaw writes `window_index` as an int; tmux reports it as text. A
    type-sensitive compare would reject EVERY task file — a guard so strict it
    is equivalent to deleting the feature, and it would look like a clean 0."""
    assert TASK_LIVE["window_index"] == 3 and isinstance(
        TASK_LIVE["window_index"], int)
    res = sm.filter_live_tasks([json.dumps(TASK_LIVE)], LIVE_WINDOWS)
    assert res["files_live"] == 1, "int 3 must match tmux's '3'"


def test_two_files_claiming_ONE_slot_are_BOTH_dropped_not_last_wins():
    """🔴 5 slots on the live host were contested. Last-wins attached an
    arbitrary one of two contradictory records — and a wrong `claude_session_id`
    reads as measured data, so it is worse than no record at all."""
    a = dict(TASK_LIVE, claude_session="aaaaaaaa-1111-4111-8111-111111111111",
             summary="claimant-a")
    b = dict(TASK_LIVE, claude_session="bbbbbbbb-2222-4222-8222-222222222222",
             summary="claimant-b")
    idx = sm.index_tasks_by_window([a, b])
    assert idx["index"] == {}, "a contested slot must resolve to NOTHING"
    assert idx["conflicts"] == [{"session": "scratch7", "window_index": "3",
                                 "claimants": 2, "window_ids": ["@41"]}]


def test_a_third_claimant_is_counted_and_still_drops_the_slot():
    """Off-by-one control on the conflict path: the 2->3 step must not restore
    a winner, and the claimant count must actually move."""
    claims = [dict(TASK_LIVE, summary=f"claimant-{i}") for i in range(3)]
    idx = sm.index_tasks_by_window(claims)
    assert idx["index"] == {}
    assert idx["conflicts"][0]["claimants"] == 3


def test_an_uncontested_slot_is_unaffected_by_the_conflict_logic():
    """Positive control: conflict detection must not eat the normal case."""
    idx = sm.index_tasks_by_window([TASK_LIVE, TASK_STALE])
    assert idx["conflicts"] == []
    assert idx["index"][("scratch7", "3")]["summary"] == "summary-alpha"
    assert idx["index"][("scratch2", "8")]["summary"] == "summary-bravo"


def test_an_UNMEASURED_live_set_is_not_an_empty_one():
    """🔴 F2/F3, at the unit. None means "we never asked". It may only produce
    `status: unmeasured` and `files_live: None` — never a measured 0."""
    bodies = [json.dumps(TASK_LIVE), json.dumps(TASK_STALE), "{not json"]
    unmeasured = sm.filter_live_tasks(bodies, None)
    measured_zero = sm.filter_live_tasks(bodies, {})

    assert unmeasured["status"] == "unmeasured"
    assert unmeasured["files_live"] is None
    assert unmeasured["files_stale"] is None
    assert unmeasured["files_mismatched"] is None
    assert unmeasured["error"]
    # the file-level facts ARE still measurements and survive
    assert unmeasured["files_seen"] == 3
    assert unmeasured["files_unparseable"] == 1

    assert measured_zero["status"] == "ok"
    assert measured_zero["files_live"] == 0
    # 🔴 and the two are DISTINGUISHABLE, which is the entire point
    assert unmeasured["files_live"] is not measured_zero["files_live"]
    assert unmeasured["status"] != measured_zero["status"]


def test_the_unmeasured_reason_is_carried_through_verbatim():
    res = sm.filter_live_tasks([], None, unmeasured_reason="ssh ate it")
    assert res["error"] == "ssh ate it"


@pytest.mark.parametrize("ids", [
    {"@41", "@52"},          # the OLD argument shape — a bare set of ids
    ["@41"],
    ("@41",),
])
def test_passing_a_bare_SET_of_ids_is_a_TypeError_not_a_silent_downgrade(ids):
    """🔴 The old signature took exactly this. Accepting it now would silently
    restore the weaker existence-only check — the defect, re-entering through
    the door marked "backwards compatible"."""
    with pytest.raises(TypeError) as e:
        sm.filter_live_tasks([json.dumps(TASK_LIVE)], ids)
    assert "parse_windows" in str(e.value)


def test_measured_stale_ratio_shape_is_handled_at_scale():
    """A miniature of the real 400/43 distribution — the guard must scale and
    must not be accidentally quadratic on membership."""
    bodies = [json.dumps(dict(TASK_STALE, window_id=f"@{9000 + i}"))
              for i in range(357)]
    # each live file gets its OWN slot, so nothing is contested
    bodies += [json.dumps(dict(TASK_LIVE, window_id=f"@{i}",
                               tmux_session="scratch7", window_index=i))
               for i in range(43)]
    live = {f"@{i}": ("scratch7", str(i)) for i in range(43)}
    res = sm.filter_live_tasks(bodies, live)
    assert (res["files_seen"], res["files_live"]) == (400, 43)
    assert (res["files_stale"], res["files_mismatched"]) == (357, 0)


@pytest.mark.parametrize("body", [
    "{not json",
    "",
    "[]",             # valid JSON, wrong shape
    '"a string"',
    "null",
])
def test_unparseable_or_wrong_shaped_task_file_is_skipped_not_fatal(body):
    """§3 test 7."""
    res = sm.filter_live_tasks([body, json.dumps(TASK_LIVE)], LIVE_WINDOWS)
    assert res["files_live"] == 1
    assert res["files_seen"] == 2
    assert res["files_unparseable"] == 1


def test_a_zero_from_no_files_is_distinguishable_from_a_zero_from_all_stale():
    """SILENT-ZERO, fuzzyclaw edition. Both produce `tasks == []`; only the
    counters say which happened."""
    none_at_all = sm.filter_live_tasks([], LIVE_WINDOWS)
    all_stale = sm.filter_live_tasks([json.dumps(TASK_STALE)] * 5,
                                     LIVE_WINDOWS)
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

    # 🔴 REACHABILITY of the probe. Every value in TASK_LIVE is TRUTHY, so a
    # SHORT-CIRCUITED extra read — `obj.get("summary") or obj.get("pid")` — is
    # never evaluated and the probe above stays green while the code really does
    # consume an off-ledger key on other inputs. A mutation sweep found exactly
    # that. So run it again with every value FALSY, which forces the right-hand
    # side of any `or` to execute.
    falsy = Tracking({k: None for k in EXPECTED_FUZZYCLAW_FIELDS})
    sm.task_from_file_obj(falsy)
    assert falsy.read == EXPECTED_FUZZYCLAW_FIELDS, (
        "a key was consumed only on the falsy path — the ledger must cover "
        "EVERY branch that reads the untrusted task file, not just the happy one")

    # ...and with the keys ABSENT entirely, the third shape a real file takes
    # (4 of 400 live files predate `transcript_path`).
    empty = Tracking({})
    sm.task_from_file_obj(empty)
    assert empty.read == EXPECTED_FUZZYCLAW_FIELDS


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
    idx = sm.index_tasks_by_window([TASK_LIVE, TASK_STALE])["index"]
    assert set(idx) == {("scratch7", "3"), ("scratch2", "8")}
    assert idx[("scratch7", "3")]["claude_session"] == TASK_LIVE["claude_session"]


def test_index_tasks_skips_entries_missing_a_join_key():
    res = sm.index_tasks_by_window([{"tmux_session": "s"}, {"window_index": 1}])
    assert res["index"] == {} and res["conflicts"] == []


def test_index_tasks_returns_a_COUNTED_result_never_a_bare_mapping():
    """"no task for this slot" and "two files disagreed, so we refuse to guess"
    are different facts and must not both be an absent key."""
    res = sm.index_tasks_by_window([])
    assert set(res) == {"index", "conflicts"}


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
    """🔴 A SPELLED guard is not a structural one. The first version asserted
    only that some option `startswith("ConnectTimeout=")`, which passes for
    `ConnectTimeout=400` — a value that lets one unreachable laptop hang a scan
    for nearly seven minutes, i.e. the exact hazard the option exists to
    prevent. The number is the guard; assert the number.
    """
    opts = list(sm.SSH_OPTS)
    assert "BatchMode=yes" in opts
    assert "ConnectTimeout=4" in opts
    assert "StrictHostKeyChecking=accept-new" in opts
    # ...and it is an `-o` VALUE, not a stray positional that ssh would ignore
    assert opts[opts.index("ConnectTimeout=4") - 1] == "-o"
    assert opts[opts.index("BatchMode=yes") - 1] == "-o"

    # The whole-scan bound: SSH_TIMEOUT is the outer kill, ConnectTimeout the
    # inner one, and the inner must be strictly smaller or it never fires.
    ct = float(next(o for o in opts
                    if o.startswith("ConnectTimeout=")).split("=", 1)[1])
    assert ct == 4.0
    assert sm.SSH_TIMEOUT == 12.0
    assert 0 < ct < sm.SSH_TIMEOUT
    assert sm.LOCAL_TIMEOUT == 5.0


def test_ssh_opts_are_bounded_enough_to_matter():
    """The pinned values above are only meaningful as a BOUND. Stated at the
    scope measured: a 2-host scan issues 2 SSH calls, so the worst case the
    laptop can impose is 2 x SSH_TIMEOUT."""
    assert sm.SSH_TIMEOUT * 2 <= 30, (
        "a scan must not be able to block for half a minute on a dead laptop")


# --------------------------------------------------------------------------- #
# 🔴 make_ch_client — the ONE function that loads credentials, and it had ZERO
# coverage: the autouse `_no_real_socket` fixture replaces it with a raiser, so
# nothing ever ran the real body. Captured BEFORE that fixture can patch it.
# --------------------------------------------------------------------------- #
_REAL_MAKE_CH_CLIENT = sm.make_ch_client


def test_the_real_make_ch_client_is_the_one_under_test_here():
    """INSTRUMENT CHECK. If this captured the raiser instead of the real
    function, every test below would be measuring the fixture."""
    assert _REAL_MAKE_CH_CLIENT is not sm.make_ch_client
    assert _REAL_MAKE_CH_CLIENT.__name__ == "make_ch_client"
    with pytest.raises(_Forbidden):
        sm.make_ch_client()          # the patched module attribute still raises


def test_make_ch_client_reads_the_endpoint_from_the_env_FILE(
        tmp_path, monkeypatch):
    envf = tmp_path / "env"
    envf.write_text("CLICKHOUSE_URL=http://ch.invalid:8123/\n"
                    "CLICKHOUSE_USER=file_user\n"
                    "CLICKHOUSE_PASSWORD='file_pw'\n"
                    "# a comment\n\nCLICKHOUSE_DATABASE=activity\n")
    for k in ("CLICKHOUSE_URL", "CLICKHOUSE_USER", "CLICKHOUSE_PASSWORD",
              "CLICKHOUSE_DATABASE"):
        monkeypatch.delenv(k, raising=False)
    client = _REAL_MAKE_CH_CLIENT(env_file=str(envf), opener=lambda *a, **k: None)
    assert client.conn.url == "http://ch.invalid:8123"   # trailing / stripped
    assert client.conn.user == "file_user"
    assert client.conn.password == "file_pw"             # quotes stripped
    assert client._opener is not None


def test_make_ch_client_lets_the_PROCESS_ENV_win_over_the_file(
        tmp_path, monkeypatch):
    """🔴 The documented precedence, pinned. `reference/clickhouse-queries.md`
    tells an operator they can override the endpoint for one invocation without
    editing a chmod-600 credentials file — that promise is `env.setdefault`,
    which is trivially invertible to `env.update` and would silently make the
    documented workflow a no-op. Both values are distinct, so whichever wins is
    named by the assertion.
    """
    envf = tmp_path / "env"
    envf.write_text("CLICKHOUSE_URL=http://from-file.invalid:8123\n"
                    "CLICKHOUSE_USER=file_user\n"
                    "CLICKHOUSE_PASSWORD=file_pw\n")
    monkeypatch.setenv("CLICKHOUSE_URL", "http://from-env.invalid:9000")
    monkeypatch.setenv("CLICKHOUSE_USER", "env_user")
    monkeypatch.delenv("CLICKHOUSE_PASSWORD", raising=False)

    client = _REAL_MAKE_CH_CLIENT(env_file=str(envf), opener=lambda *a, **k: None)
    assert client.conn.url == "http://from-env.invalid:9000", "process env wins"
    assert client.conn.user == "env_user"
    # ...and the file still FILLS THE GAPS — otherwise "env wins" could just be
    # "the file is ignored", which is a different behaviour that also passes the
    # two assertions above.
    assert client.conn.password == "file_pw"


def test_make_ch_client_with_no_endpoint_anywhere_RAISES(tmp_path, monkeypatch):
    """It must not return a half-built client pointed at nothing — `gather`
    turns the raise into `clickhouse.status: "unavailable"`, which is the
    discriminated failure. A silent default endpoint would be a fabricated ok."""
    monkeypatch.delenv("CLICKHOUSE_URL", raising=False)
    with pytest.raises(Exception) as e:
        _REAL_MAKE_CH_CLIENT(env_file=str(tmp_path / "missing"),
                             opener=lambda *a, **k: None)
    assert "CLICKHOUSE_URL" in str(e.value)


def test_make_ch_client_never_hardcodes_an_endpoint_or_password():
    """🔴 Public repo. The source must not carry a real host or credential."""
    src = open(_SCRIPT, encoding="utf-8").read()
    assert "CLICKHOUSE_PASSWORD" not in src.replace(
        "CLICKHOUSE_URL / CLICKHOUSE_USER", "")
    assert "http://" not in src and "https://" not in src


def test_gather_turns_a_credential_failure_into_a_DISCRIMINATED_status():
    """The seam between the two: a raising factory must become `unavailable`,
    never `ok` with zero rows."""
    def boom():
        raise RuntimeError("CLICKHOUSE_URL not set")
    report = base_gather(use_ch=True, ch_client_factory=boom)
    assert report["clickhouse"]["status"] == "unavailable"
    assert report["clickhouse"]["rows"] == []
    assert "CLICKHOUSE_URL not set" in report["clickhouse"]["error"]
    assert "QUERY FAILED [unavailable]" in sm.render_table(report)


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
        return 0, WORKBENCH_PANES if "list-panes" in argv else WORKBENCH_WINDOWS, ""

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
# 🔴 TWO tmux CALLS, TWO INDEPENDENT MEASUREMENTS (the audit's C, D and F3)
#
# `gather` runs `list-panes` AND `list-windows` per host. Which result each
# published fact is read from is invisible to any fixture that answers both the
# same way — which is exactly what the old `make_runner` did, and exactly why
# the mutants below survived a green sweep. Every test here makes the two calls
# DISAGREE, so the provenance of each fact is observable.
# =========================================================================== #
def test_reachable_is_read_off_list_PANES_not_list_windows():
    """MUTANT C. `reachable = bool(wins_res["reachable"])` cannot be seen unless
    the two calls disagree. Panes fail, windows succeed => UNREACHABLE."""
    runner = make_runner(local_rc=255, local_err="tmux: command not found",
                         local_windows_rc=0)
    report = base_gather(runner=runner)
    wb = report["hosts"]["workbench"]
    assert wb["reachable"] is False, (
        "reachability is the PANES call's fact; list-windows succeeding says "
        "nothing about whether the pane data was measured")
    assert wb["windows"] == []
    assert wb["windows_measured"] is True   # ...and the other call is separate
    # the mirror image, so this is not one-directional:
    other = make_runner(local_windows_rc=1, local_windows_err="windows died")
    wb2 = base_gather(runner=other)["hosts"]["workbench"]
    assert wb2["reachable"] is True and wb2["windows_measured"] is False


def test_the_host_error_is_the_PANES_error_not_the_windows_error():
    """MUTANT D. Two DISTINCT stderr strings, so the assertion names which
    subprocess the published error actually came from."""
    runner = make_runner(local_rc=255, local_err="PANES-CALL-EXPLODED",
                         local_windows_rc=1,
                         local_windows_err="WINDOWS-CALL-EXPLODED")
    wb = base_gather(runner=runner)["hosts"]["workbench"]
    assert wb["error"] == "PANES-CALL-EXPLODED"
    assert wb["windows_error"] == "WINDOWS-CALL-EXPLODED"
    assert wb["error"] != wb["windows_error"], (
        "the fixture must be able to tell the two apart — if these were equal "
        "this test would pass for either wiring")


def test_a_failed_list_windows_does_NOT_publish_a_measured_empty_id_set():
    """🔴 F3. `list-panes` succeeds, `list-windows` fails. The old code read
    only `wins_res["stdout"]`, so `live_window_ids` published as a measured
    `[]`, every task dropped, `claude_session_id` went null, fuzzyclaw stayed
    `"ok"` and the process exited 0. A fabricated zero, three fields wide."""
    runner = make_runner(local_windows_rc=1,
                         local_windows_err="lost server 500 lines")
    report = base_gather(runner=runner)
    wb = report["hosts"]["workbench"]

    assert wb["reachable"] is True, "the panes call DID answer"
    assert wb["windows_measured"] is False
    assert "lost server" in wb["windows_error"]
    assert wb["live_window_ids"] is None, (
        "None = never measured. [] would be a claim that the host has no "
        "windows, which is a different — and false — fact")

    fz = report["fuzzyclaw"]
    assert fz["status"] == "unmeasured"
    assert fz["files_live"] is None
    assert fz["files_seen"] == 2, "the FILES were still really counted"
    assert "list-windows" in fz["error"]

    assert report["summary"]["fuzzyclaw_status"] == "unmeasured"
    assert report["summary"]["fuzzyclaw_live"] is None
    assert report["summary"]["windows_unmeasured"] == ["workbench"]

    # and it is LOUD in the table, not merely absent
    text = sm.render_table(report)
    assert "LIVE COUNT UNMEASURED" in text
    assert "this is NOT zero live tasks" in text
    assert "WINDOW LIST UNMEASURED ON: workbench" in text


def test_the_measured_and_unmeasured_fuzzyclaw_zeroes_are_DIFFERENT_OUTPUT():
    """The discriminating control. Same files, same panes; only whether
    list-windows answered differs — and the two must not render the same."""
    unmeasured = base_gather(runner=make_runner(local_windows_rc=1,
                                                local_windows_err="died"))
    measured_zero = base_gather(runner=make_runner(local_windows=""))

    assert measured_zero["fuzzyclaw"]["status"] == "ok"
    assert measured_zero["fuzzyclaw"]["files_live"] == 0
    assert measured_zero["hosts"]["workbench"]["live_window_ids"] == []

    assert unmeasured["fuzzyclaw"]["status"] == "unmeasured"
    assert unmeasured["fuzzyclaw"]["files_live"] is None
    assert unmeasured["hosts"]["workbench"]["live_window_ids"] is None

    a, b = sm.render_table(unmeasured), sm.render_table(measured_zero)
    assert a != b
    assert "UNMEASURED" in a and "UNMEASURED" not in b


def test_scanning_ONLY_the_remote_host_never_fabricates_a_fuzzyclaw_zero():
    """🔴 F2. With `--host laptop` the local host never enters the loop, so the
    live-window set is never measured — yet the task files were read. The old
    code filtered 400 files against an empty set and reported
    `files_seen: 400, files_live: 0, status: "ok"`: a measurement that never
    happened, labelled ok."""
    report = base_gather(hosts=("laptop",), local_host="workbench")
    fz = report["fuzzyclaw"]

    assert fz["status"] == "unmeasured"
    assert fz["files_live"] is None
    assert fz["files_seen"] == 2, "the files really were read and counted"
    assert "was not scanned" in fz["error"] and "workbench" in fz["error"]
    assert report["summary"]["fuzzyclaw_status"] == "unmeasured"
    assert report["summary"]["fuzzyclaw_live"] is None

    # positive control: scanning the local host DOES measure it, so the
    # unmeasured verdict above is caused by the host filter and nothing else.
    local = base_gather(hosts=("workbench",), local_host="workbench")
    assert local["fuzzyclaw"]["status"] == "ok"
    assert local["fuzzyclaw"]["files_live"] == 1


def test_scanning_only_the_remote_host_still_exits_OK_with_its_windows():
    """The fuzzyclaw column being unmeasured must not poison the host scan: the
    laptop's windows WERE measured, so this is a real 0-exit with real rows."""
    report = base_gather(hosts=("laptop",), local_host="workbench")
    assert report["hosts"]["laptop"]["reachable"] is True
    assert len(report["hosts"]["laptop"]["windows"]) == 1
    assert sm.exit_code_for(report) == sm.EXIT_OK


def test_each_host_gets_its_OWN_window_ids_never_the_other_hosts():
    """A cross-host bind error: the laptop's rows must carry laptop window ids.
    The two fixtures share no id, so a swapped bind is visible."""
    report = base_gather()
    assert report["hosts"]["workbench"]["live_window_ids"] == ["@41", "@52",
                                                               "@63"]
    assert report["hosts"]["laptop"]["live_window_ids"] == ["@7"]
    assert report["hosts"]["laptop"]["windows"][0]["window_id"] == "@7"
    assert report["hosts"]["workbench"]["windows"][0]["window_id"] == "@41"


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
                       "live_window_ids", "windows_measured", "windows_error"}
    assert wb["ssh_target"] is None
    assert wb["live_window_ids"] == ["@41", "@52", "@63"]
    assert wb["windows_measured"] is True
    assert wb["windows_error"] is None

    row = wb["windows"][0]
    assert row == {
        "host": "workbench",
        "session": "scratch7",
        "window_index": "3",
        "window_id": "@41",
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
    assert second["window_id"] == "@52"
    assert second["claude"] is False
    assert second["codename"] is None
    assert second["status"] == "unknown"
    assert second["age_secs"] is None
    assert second["fuzzyclaw"] is None

    assert blob["fuzzyclaw"] == {
        "status": "ok", "error": None,
        "tasks": [dict(TASK_LIVE)],
        "files_seen": 2, "files_live": 1, "files_unparseable": 0,
        "files_stale": 1, "files_mismatched": 0, "slot_conflicts": [],
    }
    assert blob["summary"] == {
        "total_sessions": 3, "claude": 2, "busy": 1, "idle": 1, "stale": 0,
        "unknown": 1,
        "hosts_reachable": ["laptop", "workbench"],
        "hosts_unreachable": [],
        "fuzzyclaw_live": 1,
        "fuzzyclaw_status": "ok",
        "windows_unmeasured": [],
    }


def test_every_joined_row_names_the_window_its_task_describes():
    """🔴 F1, asserted END-TO-END on the whole report rather than per unit.

    This is the RELATIONSHIP the guard exists to establish, restated where a
    consumer can see it: for every row that carries a fuzzyclaw task, the tmux
    window id of the slot the row occupies IS the window id the task file names.
    Before the fix this was violated by 2 of 44 live rows on this host, and the
    violated field travelled as `claude_session_id`.
    """
    report = base_gather()
    joined = [r for h in report["hosts"].values() for r in h["windows"]
              if r.get("fuzzyclaw")]
    assert joined, "positive control: the fixture must produce a joined row"
    for r in joined:
        assert r["window_id"] == r["fuzzyclaw"]["window_id"], (
            f"row {r['session']}:{r['window_index']} sits in window "
            f"{r['window_id']} but carries a task file describing "
            f"{r['fuzzyclaw']['window_id']} — including its claude_session_id")


def test_a_row_whose_window_list_is_unmeasured_has_a_NULL_window_id():
    """An unknown id is null, never a guess, and never carried over from the
    other host's window list."""
    runner = make_runner(local_windows_rc=1, local_windows_err="tmux died")
    report = base_gather(runner=runner)
    for r in report["hosts"]["workbench"]["windows"]:
        assert r["window_id"] is None


def test_two_panes_in_one_window_collapse_to_one_row():
    report = base_gather()
    wb = report["hosts"]["workbench"]["windows"]
    assert len(wb) == 2, "panes must fold to windows, not stay per-pane"
    assert wb[0]["panes"] == 2
    assert wb[0]["pane_id"] == "%11"
    assert wb[0]["command"] == "claude"


# --------------------------------------------------------------------------- #
# 🔴 MUTANT E — a fixture that cannot distinguish two implementations.
#
# The test above says "the claude pane, not %12" — but in WORKBENCH_PANES the
# claude pane IS pane 0, so `next(p for p in members if pane_is_claude(p))` and
# a plain `members[0]` produce the SAME row. The comment named a guarantee the
# fixture could not observe. These use a window whose claude pane is SECOND, so
# the two implementations disagree and the assertion picks one.
# --------------------------------------------------------------------------- #
CLAUDE_SECOND_PANES = "\n".join([
    "%80|8001|scratch7|3|win-alpha|/home/zach/tmp|zsh|zsh-pane-title",
    f"%81|8002|scratch7|3|win-alpha|/home/zach/workspace/repo-alpha|claude"
    f"|{BRAILLE} the claude pane",
])


def test_the_lead_pane_is_the_CLAUDE_pane_even_when_it_is_not_the_first():
    """Every field the row takes from the lead must come from %81, not %80."""
    report = base_gather(runner=make_runner(local_panes=CLAUDE_SECOND_PANES))
    row = report["hosts"]["workbench"]["windows"][0]
    assert row["panes"] == 2
    assert row["pane_id"] == "%81", "members[0] would have given %80"
    assert row["command"] == "claude"
    assert row["path"] == "/home/zach/workspace/repo-alpha"
    assert row["task"] == "the claude pane"
    assert row["busy"] is True, "the busy glyph is read off the CLAUDE pane"
    assert row["status"] == "busy"


def test_the_control_the_first_pane_leads_when_NO_pane_is_claude():
    """Positive control on the selector: with no claude pane it must fall back
    to members[0], so the test above is measuring the claude preference and not
    simply 'always the last pane'."""
    panes = "\n".join([
        "%90|9001|scratch7|3|win-alpha|/first|zsh|first title",
        "%91|9002|scratch7|3|win-alpha|/second|bash|second title",
    ])
    row = base_gather(runner=make_runner(local_panes=panes)
                      )["hosts"]["workbench"]["windows"][0]
    assert row["pane_id"] == "%90" and row["path"] == "/first"
    assert row["claude"] is False


def test_only_the_FIRST_claude_pane_leads_when_there_are_several():
    """Off-by-one control: two claude panes must not silently pick the last."""
    panes = "\n".join([
        "%95|9501|scratch7|3|win-alpha|/zsh-pane|zsh|zsh title",
        "%96|9502|scratch7|3|win-alpha|/claude-one|claude|first claude",
        "%97|9503|scratch7|3|win-alpha|/claude-two|claude|second claude",
    ])
    row = base_gather(runner=make_runner(local_panes=panes)
                      )["hosts"]["workbench"]["windows"][0]
    assert row["pane_id"] == "%96" and row["path"] == "/claude-one"
    assert row["panes"] == 3


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
    """A dead host: nothing was measured, and no claim is made about the target."""
    def rec(argv, timeout):
        return 255, "", "ssh: connect to host: No route to host"

    res = sm.tail_window("nope:9", "laptop", "workbench", runner=rec)
    assert res["reachable"] is False
    assert res["found"] is None, (
        "an unreached host has said NOTHING about whether the target exists")
    assert "No route to host" in res["error"]
    assert res["text"] == ""


# --------------------------------------------------------------------------- #
# 🔴 "no such window" is NOT "host unreachable" (the audit's tail finding)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("stderr", [
    "can't find window: nope",
    "can't find pane: nope:9",
    "can't find session: nope",
    "no such window",
    "session not found: nope",
])
def test_a_missing_target_is_REACHABLE_but_NOT_FOUND(stderr):
    """The host answered. Saying "unreachable" states a FALSE FACT about it and
    sends the reader to debug SSH for a typo in a window name."""
    res = sm.tail_window("nope:9", "workbench", "workbench",
                         runner=lambda argv, t: (1, "", stderr))
    assert res["reachable"] is True
    assert res["found"] is False
    assert res["error"] and res["text"] == ""


def test_the_two_tail_failures_are_DISTINGUISHABLE_in_message_and_exit_code(
        monkeypatch, capsys):
    """🔴 Both used to print "unreachable" and return 4. They are different
    facts, so they get different messages AND different exit codes."""
    monkeypatch.setattr(sm, "local_host_label", lambda *a, **k: "workbench")

    monkeypatch.setattr(sm, "_default_runner",
                        lambda argv, t: (1, "", "can't find window: nope"))
    rc_missing = sm.main(["tail", "nope:9", "--host", "workbench"])
    err_missing = capsys.readouterr().err

    monkeypatch.setattr(sm, "_default_runner",
                        lambda argv, t: (255, "", "ssh: No route to host"))
    rc_dead = sm.main(["tail", "nope:9", "--host", "laptop"])
    err_dead = capsys.readouterr().err

    assert rc_missing == sm.EXIT_USAGE == 2
    assert "no such window" in err_missing
    assert "unreachable" not in err_missing, (
        "the host answered; calling it unreachable is a false claim")

    assert rc_dead == sm.EXIT_UNAVAILABLE == 4
    assert "unreachable" in err_dead
    assert "no such window" not in err_dead

    assert rc_missing != rc_dead


# --------------------------------------------------------------------------- #
# 🔴 THE tail SUCCESS PATH — the PR's headline exit claim, for tail
#
# Not one test called main(["tail", ...]) on a SUCCESSFUL capture, so the
# 0-vs-3 contract was entirely unverified for the one subcommand that has its
# own exit path. Both zeroes, measured at both ends.
# --------------------------------------------------------------------------- #
def test_main_tail_success_prints_the_capture_and_exits_zero(
        monkeypatch, capsys):
    monkeypatch.setattr(sm, "local_host_label", lambda *a, **k: "workbench")
    monkeypatch.setattr(sm, "_default_runner",
                        make_runner(local_capture="line one\nline two\n"))
    rc = sm.main(["tail", "scratch7:3", "--host", "workbench"])
    out = capsys.readouterr().out
    assert out == "line one\nline two\n", "the capture is written verbatim"
    assert rc == sm.EXIT_OK == 0


@pytest.mark.parametrize("blank", ["", "\n", "   \n\t\n"])
def test_main_tail_of_an_EMPTY_window_is_the_MEASURED_zero_not_success(
        monkeypatch, capsys, blank):
    """🔴 tail's own silent zero: the host answered, the window exists, and its
    scrollback is genuinely empty. That is EXIT_EMPTY (3), not EXIT_OK."""
    monkeypatch.setattr(sm, "local_host_label", lambda *a, **k: "workbench")
    monkeypatch.setattr(sm, "_default_runner",
                        make_runner(local_capture=blank))
    rc = sm.main(["tail", "scratch7:3", "--host", "workbench"])
    capsys.readouterr()
    assert rc == sm.EXIT_EMPTY == 3
    assert rc != sm.EXIT_OK and rc != sm.EXIT_UNAVAILABLE


def test_main_tail_json_mode_carries_the_full_discriminated_result(
        monkeypatch, capsys):
    monkeypatch.setattr(sm, "local_host_label", lambda *a, **k: "workbench")
    monkeypatch.setattr(sm, "_default_runner",
                        make_runner(local_capture="captured\n"))
    rc = sm.main(["tail", "scratch7:3", "--host", "workbench", "--json"])
    blob = json.loads(capsys.readouterr().out)
    assert blob["reachable"] is True and blob["found"] is True
    assert blob["text"] == "captured\n"
    assert blob["host"] == "workbench" and blob["target"] == "scratch7:3"
    assert blob["host_defaulted"] is False
    assert rc == sm.EXIT_OK


def test_main_tail_lines_flag_reaches_the_capture_argv(monkeypatch, capsys):
    """--lines must not be inert: the scrollback depth is the point of tail."""
    seen = []
    monkeypatch.setattr(sm, "local_host_label", lambda *a, **k: "workbench")
    monkeypatch.setattr(sm, "_default_runner",
                        lambda argv, t: (seen.append(list(argv)),
                                         (0, "x\n", ""))[1])
    sm.main(["tail", "scratch7:3", "--host", "workbench", "--lines", "250"])
    capsys.readouterr()
    assert seen[0] == ["tmux", "capture-pane", "-t", "scratch7:3", "-p", "-e",
                       "-S", "-250"]


def test_main_tail_defaults_to_the_LOCAL_host_and_RECORDS_that_it_did(
        monkeypatch, capsys):
    """`--host all` is the default and is meaningless for a single-window
    command. It resolves to the local host — but the JSON says so, and the
    not-found message names the host searched plus how to search the other."""
    monkeypatch.setattr(sm, "local_host_label", lambda *a, **k: "workbench")
    monkeypatch.setattr(sm, "_default_runner",
                        make_runner(local_capture="local text\n"))
    rc = sm.main(["tail", "scratch7:3", "--json"])          # no --host
    blob = json.loads(capsys.readouterr().out)
    assert blob["host"] == "workbench"
    assert blob["host_defaulted"] is True
    assert rc == sm.EXIT_OK

    monkeypatch.setattr(sm, "_default_runner",
                        lambda argv, t: (1, "", "can't find window: nope"))
    assert sm.main(["tail", "nope:9"]) == sm.EXIT_USAGE
    err = capsys.readouterr().err
    assert "--host defaulted to the local host" in err
    assert "--host laptop" in err, "say how to search the OTHER host"


def test_main_tail_with_an_explicit_host_is_not_marked_defaulted(
        monkeypatch, capsys):
    """Negative control on the flag above — it must be able to be False."""
    monkeypatch.setattr(sm, "local_host_label", lambda *a, **k: "workbench")
    monkeypatch.setattr(sm, "_default_runner",
                        make_runner(remote_capture="remote text\n"))
    sm.main(["tail", "naida-dev:1", "--host", "laptop", "--json"])
    blob = json.loads(capsys.readouterr().out)
    assert blob["host"] == "laptop" and blob["host_defaulted"] is False
    assert blob["text"] == "remote text\n"


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


# --------------------------------------------------------------------------- #
# 🔴 detail_history — the consumer of sql_session_history.
#
# `sql_session_history` was defined and called from NOWHERE, while
# `reference/clickhouse-queries.md` documented it as "Query 2 — per-session
# prompt history" and described a join the code did not perform. It is wired up
# now, and these tests are what make the doc true rather than aspirational.
# --------------------------------------------------------------------------- #
HISTORY_ROW = {"ts": "2026-08-11 11:59:00", "kind": "prompt",
               "snippet": "fix the join key"}


def test_sql_session_history_IS_reachable_from_main():
    """The dead-code check, structurally: name the caller, don't assume one."""
    import inspect
    src = inspect.getsource(sm.detail_history)
    assert "sql_session_history(" in src
    assert "detail_history(" in inspect.getsource(sm.main)


def test_detail_history_queries_the_session_id_of_the_narrowed_window():
    seen = {}

    class _CH:
        def rows(self, sql):
            seen["sql"] = sql
            return [HISTORY_ROW]

    report = sm.filter_report(base_gather(), "scratch7", "3")
    hist = sm.detail_history(report, ch_client_factory=lambda: _CH())
    assert hist["status"] == "ok"
    assert hist["rows"] == [HISTORY_ROW]
    assert hist["session"] == TASK_LIVE["claude_session"]
    # 🔴 the id is QUOTED by chquery's one quoter, never f-strung raw
    assert f"'{TASK_LIVE['claude_session']}'" in seen["sql"]
    assert "LIMIT 10" in seen["sql"]
    assert hist["sql"] == seen["sql"]


def test_detail_history_quotes_a_hostile_session_id():
    """The id reaches SQL. It comes from an UNTRUSTED task file."""
    seen = {}

    class _CH:
        def rows(self, sql):
            seen["sql"] = sql
            return []

    evil = "abc' OR 1=1 --"
    report = sm.filter_report(base_gather(), "scratch7", "3")
    for h in report["hosts"].values():
        for r in h["windows"]:
            r["claude_session_id"] = evil
    sm.detail_history(report, ch_client_factory=lambda: _CH())
    sql = seen["sql"]
    # chquery escapes with a BACKSLASH, so the injected quote must arrive as
    # \' — i.e. it never closes the literal and never starts a new clause.
    assert "= 'abc\\' OR 1=1 --'" in sql
    assert " OR 1=1" not in sql.replace("abc\\' OR 1=1 --", ""), (
        "the injected clause escaped its string literal")
    assert sm.sql_session_history(evil) == sql
    # positive control on the escaper: a benign id gets NO backslash, so the
    # assertion above is observing escaping and not a constant.
    assert "\\" not in sm.sql_session_history(TASK_LIVE["claude_session"])


@pytest.mark.parametrize("kw,needle", [
    (dict(use_ch=False), "--no-ch"),
    (dict(), "no claude_session_id"),
])
def test_detail_history_skips_are_LABELLED_not_silently_empty(kw, needle):
    """🔴 Three different facts would otherwise all render as "no history":
    CH was off, the window has no session id, and the query returned nothing."""
    report = sm.filter_report(base_gather(), "misc", "5")   # no task -> no id
    if kw.get("use_ch") is False:
        report = sm.filter_report(base_gather(), "scratch7", "3")
    hist = sm.detail_history(report, **kw)
    assert hist["status"] == "skipped"
    assert needle in hist["reason"]
    assert hist["rows"] == []
    assert needle in sm.render_session_history(hist)


def test_detail_history_genuine_zero_is_DISTINCT_from_both_skips():
    report = sm.filter_report(base_gather(), "scratch7", "3")
    real_zero = sm.detail_history(report, ch_client_factory=lambda: FakeCH([]))
    no_ch = sm.detail_history(report, use_ch=False)
    assert real_zero["status"] == "ok" and real_zero["rows"] == []
    assert no_ch["status"] == "skipped"
    a = sm.render_session_history(real_zero)
    b = sm.render_session_history(no_ch)
    assert a != b
    assert "the query ran and returned nothing" in a
    assert "skipped" in b


def test_detail_history_failure_is_NOT_an_empty_history():
    report = sm.filter_report(base_gather(), "scratch7", "3")
    err = sm._chq.CHUnreachable("URLError: Connection refused")
    hist = sm.detail_history(report,
                             ch_client_factory=lambda: FakeCH(raise_=err))
    assert hist["status"] == "unreachable"
    text = sm.render_session_history(hist)
    assert "QUERY FAILED [unreachable]" in text
    assert "this is NOT zero prompts" in text


def test_detail_history_unbuildable_client_is_unavailable_not_ok():
    def boom():
        raise RuntimeError("CLICKHOUSE_URL not set")
    report = sm.filter_report(base_gather(), "scratch7", "3")
    hist = sm.detail_history(report, ch_client_factory=boom)
    assert hist["status"] == "unavailable"
    assert "CLICKHOUSE_URL" in hist["error"]
    assert hist["session"] == TASK_LIVE["claude_session"]


def test_detail_history_factory_is_resolved_at_CALL_time_not_bound():
    """Same hole as M19, in the second function that builds a CH client. A
    def-time default would bypass the suite's no-network guard here too."""
    import inspect
    assert (inspect.signature(sm.detail_history)
            .parameters["ch_client_factory"].default is None)
    report = sm.filter_report(base_gather(), "scratch7", "3")
    hist = sm.detail_history(report)          # no factory -> patched attribute
    assert hist["status"] == "unavailable"
    assert "_Forbidden" in hist["error"]


def test_main_detail_attaches_the_history_and_never_builds_CH_under_no_ch(
        monkeypatch, capsys):
    """End-to-end through main(), both the wiring and the --no-ch guarantee."""
    built = []
    monkeypatch.setattr(sm, "gather", lambda **kw: base_gather())
    monkeypatch.setattr(sm, "local_host_label", lambda *a, **k: "workbench")
    monkeypatch.setattr(sm, "make_ch_client",
                        lambda *a, **k: built.append(1) or FakeCH([HISTORY_ROW]))

    rc = sm.main(["detail", "scratch7:3", "--json"])
    blob = json.loads(capsys.readouterr().out)
    assert blob["session_history"]["status"] == "ok"
    assert blob["session_history"]["rows"] == [HISTORY_ROW]
    assert rc == sm.EXIT_OK
    assert built == [1]

    built.clear()
    sm.main(["detail", "scratch7:3", "--json", "--no-ch"])
    blob = json.loads(capsys.readouterr().out)
    assert blob["session_history"]["status"] == "skipped"
    assert built == [], "a CH client was built despite --no-ch"


def test_main_detail_table_mode_prints_the_history_block(monkeypatch, capsys):
    monkeypatch.setattr(sm, "gather", lambda **kw: base_gather())
    monkeypatch.setattr(sm, "local_host_label", lambda *a, **k: "workbench")
    monkeypatch.setattr(sm, "make_ch_client",
                        lambda *a, **k: FakeCH([HISTORY_ROW]))
    sm.main(["detail", "scratch7:3"])
    out = capsys.readouterr().out
    assert "SESSION PROMPT HISTORY" in out
    assert "fix the join key" in out


def test_scan_does_NOT_carry_a_session_history_key(monkeypatch, capsys):
    """Negative control: the history is a `detail` concern only, so a scan's
    golden schema is unchanged by this wiring."""
    monkeypatch.setattr(sm, "gather", lambda **kw: base_gather())
    monkeypatch.setattr(sm, "local_host_label", lambda *a, **k: "workbench")
    sm.main(["scan", "--json", "--no-ch"])
    assert "session_history" not in json.loads(capsys.readouterr().out)


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
