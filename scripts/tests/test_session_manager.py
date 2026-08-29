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

import ast
import copy
import glob
import importlib.machinery
import importlib.util
import json
import os
import re
import subprocess
import sys

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


@pytest.fixture(autouse=True)
def _no_real_blocked_cache(monkeypatch):
    """🔴 THE THIRD GUARD, added because it caught a live breach.

    `gather()` reads the clawgate bar-status cache, and with no guard the
    golden-schema test silently picked up the OPERATOR'S REAL pending count
    (12) off `~/.cache/bar-status/clawgate.json` mid-run. That is two defects
    at once: a suite that reaches the machine it runs on, and an assertion
    whose expected value changes whenever the operator approves something.

    It is a SEPARATE seam from `_read_text` on purpose — three tests below
    legitimately read real tmp files through `_read_text`, so forbidding that
    function wholesale would have forced them to be weakened instead.
    """
    def _boom(path):
        raise _Forbidden(f"test tried to read the real blocked cache: {path!r}")
    monkeypatch.setattr(sm, "_read_blocked_text", _boom)


@pytest.fixture
def absent_blocked_cache(monkeypatch):
    """Opt-in fake for the `main()` -> `gather()` path, which has NO injection
    point for the clawgate cache.

    Requested by name rather than made autouse, so the raiser above stays the
    default: a new end-to-end test that forgets this fails loudly instead of
    quietly reading `~/.cache` on whatever machine the suite runs on.
    """
    monkeypatch.setattr(sm, "_read_blocked_text", lambda path: None)


def test_hermeticity_fixture_is_actually_installed():
    """POSITIVE CONTROL on the three autouse guards above.

    Without this, a fixture that stopped applying would disarm every safety
    claim in this file's docstring while the suite stayed green.
    """
    with pytest.raises(_Forbidden):
        sm._default_runner(["tmux", "kill-server"], 1)
    with pytest.raises(_Forbidden):
        sm.make_ch_client()
    with pytest.raises(_Forbidden):
        sm._read_blocked_text(sm.BLOCKED_CACHE)


def test_the_blocked_cache_seam_is_separate_from_the_general_file_reader():
    """🔴 The guard above is only real if the two seams are actually distinct.

    If `read_clawgate_queue` were changed to call `_read_text` directly, the
    autouse raiser would stop covering it and every later test would be free to
    read the live machine again — with the suite still green, because the
    positive control above only proves the raiser is INSTALLED, not that the
    code path goes through it. So prove the path: with the seam patched and no
    reader injected, the read must raise.
    """
    with pytest.raises(_Forbidden):
        sm.read_clawgate_queue()
    # ...and an INJECTED reader must bypass the seam entirely, or every test
    # below is testing the raiser rather than the parser.
    got = sm.read_clawgate_queue(path="/nope", reader=lambda p: None)
    assert got["status"] == "absent"


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
# The PARSED form of exactly the table above. Deliberately derived by hand
# rather than by calling the parser, so a broken parser cannot define its own
# expectation. Keys, colours and codenames are pairwise distinct — a fixture
# whose fields coincide cannot show WHICH field a value came from.
SLOTS_FIXTURE = {
    "scratch7": {"codename": "Grove", "key": "S", "color": "#b8bb26"},
    "scratch2": {"codename": "Vapor", "key": "T", "color": "#83a598"},
}


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
        # 🔴 The agent activity ledger is OFF in the SHARED fixture, deliberately.
        # It ships ON, and its own tests below turn it on with injected per-host
        # output. Leaving it on here would silently re-source `age_secs` and
        # `claude_session_id` for every pre-existing assertion in this file, so
        # each of those tests would stop pinning what its name says it pins. The
        # ledger's default-on behaviour is asserted directly instead — see
        # `test_the_ledger_is_ON_by_default_in_gathers_signature`.
        use_ledger=False,
        now=NOW,
        fuzzyclaw_texts=[json.dumps(TASK_LIVE), json.dumps(TASK_STALE)],
        slots=SLOTS_FIXTURE,
        # The clawgate cache: absent by default, so no test inherits a value
        # that depends on the operator's real queue. Override per test.
        clawgate_reader=lambda p: None,
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
# §3.2b — LABEL RESOLUTION (the three tiers, and the tie they cannot break)
# =========================================================================== #
# `codename` is null for every session outside the slot table; measured on the
# workbench 2026-08-13 that was 9 of ~30 windows, all of them rendering `—`.
# `resolve_label` fills that in from the cwd, and `label_source` says which tier
# answered — because "labelled `main` because the cwd is a repo called main" and
# "labelled `main` because the cwd said nothing" are different facts.
SYNTH_HOME = "/home/synthuser"


def test_tier1_a_slot_table_session_is_labelled_by_its_CODENAME_AND_HOTKEY():
    """🔴 The hotkey is the ACTIONABLE half. `Grove` says which session it is;
    `S` is what the operator presses to get there."""
    got = sm.resolve_label("scratch7", "/w/synth-alpha", SLOTS_FIXTURE,
                           home=SYNTH_HOME)
    assert got == {"label": "Grove", "label_source": "codename", "hotkey": "S"}


def test_tier1_BEATS_the_cwd_rather_than_merging_with_it():
    """PRECEDENCE, stated as its own case. The codename is what the hotkeys, the
    colours and the ledger already call this session; a cwd-derived name for the
    same window would be a second vocabulary for one thing."""
    with_cwd = sm.resolve_label("scratch7", "/w/synth-bravo", SLOTS_FIXTURE,
                                home=SYNTH_HOME)
    without = sm.resolve_label("scratch7", "", SLOTS_FIXTURE, home=SYNTH_HOME)
    assert with_cwd == without == {"label": "Grove", "label_source": "codename",
                                   "hotkey": "S"}


def test_tier2_a_session_with_no_codename_is_labelled_by_its_CWD():
    got = sm.resolve_label("8", "/w/synth-charlie", {}, home=SYNTH_HOME)
    assert got == {"label": "synth-charlie", "label_source": "path",
                   "hotkey": None}


def test_tier2_a_trailing_slash_is_the_same_directory():
    """`basename("/w/x/")` is `""`. Without the rstrip this row would fall all
    the way to `main` — pinned because a mutation sweep can otherwise delete the
    rstrip and nothing goes red."""
    assert sm.resolve_label("8", "/w/synth-india/", {}, home=SYNTH_HOME) == {
        "label": "synth-india", "label_source": "path", "hotkey": None}


def test_tier2_an_EMPTY_codename_does_not_win():
    """A slot entry with a falsy codename is not a name, and returning it would
    render a blank cell that looks like a bug in the table rather than a missing
    slot entry. Its key must not leak out either — a hotkey with no label is
    exactly as useless as a label with no hotkey."""
    got = sm.resolve_label("8", "/w/synth-delta",
                           {"8": {"codename": "", "key": "Q"}}, home=SYNTH_HOME)
    assert got == {"label": "synth-delta", "label_source": "path",
                   "hotkey": None}


@pytest.mark.parametrize("path", [
    SYNTH_HOME, SYNTH_HOME + "/", "~", "/", "//", "", "   ", None,
])
def test_tier3_a_cwd_that_yields_nothing_falls_back_to_main(path):
    got = sm.resolve_label("8", path, {}, home=SYNTH_HOME)
    assert got == {"label": "main", "label_source": "fallback", "hotkey": None}


def test_tier3_is_DISTINGUISHABLE_from_a_directory_actually_called_main():
    """SILENT-ZERO, label edition. Both rows read `main`; only `label_source`
    says which one was measured and which one is a shrug."""
    real = sm.resolve_label("8", "/w/main", {}, home=SYNTH_HOME)
    shrug = sm.resolve_label("9", SYNTH_HOME, {}, home=SYNTH_HOME)
    assert real["label"] == shrug["label"] == "main"
    assert real["label_source"] == "path"
    assert shrug["label_source"] == "fallback"
    assert real != shrug


def test_a_tilde_path_resolves_against_the_given_home():
    assert sm.resolve_label("8", "~/workspace/synth-echo", {},
                            home=SYNTH_HOME) == {
        "label": "synth-echo", "label_source": "path", "hotkey": None}


def test_label_source_is_always_one_of_the_declared_set():
    for path in ("/w/synth-fox", SYNTH_HOME, "", "/"):
        for names in ({}, {"8": {"codename": "Grove", "key": "S"}}):
            got = sm.resolve_label("8", path, names, home=SYNTH_HOME)
            assert got["label_source"] in sm.LABEL_SOURCES
            assert got["label"]          # never None, never empty
            # 🔴 A key only ever accompanies tier 1, and is None (not "")
            # otherwise — the shape a consumer branches on.
            assert (got["hotkey"] is not None) is (
                got["label_source"] == "codename")
            assert got["hotkey"] != ""
    assert set(sm.LABEL_SOURCES) == {"codename", "path", "fallback"}


# --------------------------------------------------------------------------- #
# THE HOTKEY, and where it comes from
# --------------------------------------------------------------------------- #
def test_the_hotkey_comes_from_the_SLOT_TABLE_not_a_second_map():
    """🔴 A SEAM guard, not a spelling one. The slot table is hand-edited and is
    the single source of truth for session <-> hotkey <-> colour <-> codename;
    a hardcoded map here — or in the script — would agree today and drift the
    first time a slot is rekeyed. So the expectation is READ OUT OF the table
    text, and the answer must track an edit to it.
    """
    slots = sm.load_scratch_slots(paths=["/x"], reader=lambda p: SLOT_TABLE)
    assert slots == SLOTS_FIXTURE
    for session, entry in slots.items():
        got = sm.resolve_label(session, "/w/synth-whatever", slots,
                               home=SYNTH_HOME)
        assert got["hotkey"] == entry["key"]
        assert got["label"] == entry["codename"]

    # ...and REKEYING the table moves the answer. A hardcoded map passes every
    # assertion above and fails this one.
    rekeyed = SLOT_TABLE.replace('"scratch7:S:', '"scratch7:X:')
    assert rekeyed != SLOT_TABLE
    moved = sm.load_scratch_slots(paths=["/x"], reader=lambda p: rekeyed)
    assert sm.resolve_label("scratch7", "/w/x", moved,
                            home=SYNTH_HOME)["hotkey"] == "X"


def test_load_scratch_codenames_is_a_PROJECTION_of_one_parse():
    """One rule, one place: the codename map must be derived from the slot
    parse, not from a second regex pass that can disagree with it."""
    slots = sm.load_scratch_slots(paths=["/x"], reader=lambda p: SLOT_TABLE)
    names = sm.load_scratch_codenames(paths=["/x"], reader=lambda p: SLOT_TABLE)
    assert names == {s: v["codename"] for s, v in slots.items()}


def test_the_colour_rides_along_too_but_is_not_rendered():
    slots = sm.load_scratch_slots(paths=["/x"], reader=lambda p: SLOT_TABLE)
    assert slots["scratch2"]["color"] == "#83a598"


@pytest.mark.parametrize("row,expect", [
    ({"label": "Gold", "hotkey": "G"}, "Gold (G)"),
    ({"label": "synth-longer-repo", "hotkey": None}, "synth-longer-repo"),
    ({"label": "main", "hotkey": None}, "main"),
    ({"label": "x", "hotkey": ""}, "x"),        # never an empty `()`
    ({}, "—"),
])
def test_render_label_never_fabricates_or_empties_the_parens(row, expect):
    assert sm.render_label(row) == expect


def test_the_label_is_the_LEAF_not_the_repo_root_and_that_is_documented():
    """🔴 A STATED LIMIT, pinned so the docstring cannot drift away from it.
    `session-manager` sees a path STRING from another machine, so it does not
    resolve a repo root — a local `git rev-parse` would answer about whatever
    happens to sit at that path on THIS host. `tmux-autoname-session.sh` does
    resolve it, because it runs where the directory lives."""
    got = sm.resolve_label("8", "/w/synth-golf/nix/system", {}, home=SYNTH_HOME)
    assert got == {"label": "system", "label_source": "path", "hotkey": None}
    assert "LEAF, not the git repo root" in sm.label_from_path.__doc__


def test_TWO_SESSIONS_IN_ONE_REPO_share_a_label_and_stay_addressable():
    """🔴 THE TIE, which is the whole reason `label` is additive.

    On the workbench today `0` and `8` are both sitting in one repo, so they
    resolve to the SAME label. A consumer that switched from `session:window` to
    `label` would silently address the wrong window; the rows must still be
    told apart by the identifier, and every renderer must carry it.
    """
    panes = sm.parse_panes("\n".join([
        "%1|1|0|1|w-one|/w/synth-hotel|zsh|first",
        "%2|2|8|1|w-two|/w/synth-hotel|zsh|second",
    ]))
    rows = sm.fold_windows(panes, "workbench", slots={}, now=NOW)
    assert [r["label"] for r in rows] == ["synth-hotel", "synth-hotel"]
    assert [r["label_source"] for r in rows] == ["path", "path"]
    # ...and the identifier still separates them, in the data AND on screen.
    ids = {(r["session"], r["window_index"]) for r in rows}
    assert ids == {("0", "1"), ("8", "1")}
    report = base_gather()
    report["hosts"]["workbench"]["windows"] = rows
    report["hosts"]["laptop"]["windows"] = []
    text = sm.render_table(report)
    body = [ln for ln in text.splitlines() if "synth-hotel" in ln]
    assert len(body) == 2
    assert any(re.search(r"\b0\b.*\b1\b", ln) for ln in body)
    assert any(re.search(r"\b8\b.*\b1\b", ln) for ln in body)
    assert body[0] != body[1], "two rows rendered identically — unaddressable"


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


# 🔴 The vacuous `assert "LIMIT 5" in sql_session_history("s", limit=5)` that
# used to live here has been REPLACED, not merely supplemented — it stayed green
# with `int()` deleted (f"{5}" == f"{int(5)}"), so its name claimed a coercion it
# never pinned. The structural replacement is §4.4 at the bottom of this file.


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
    # 🔴 Anchored on the FUZZYCLAW banner, not the bare word. "UNMEASURED" is
    # now also how the `waiting` roll-up spells its own unmeasured case, so a
    # substring test on the word alone stopped discriminating the two sections
    # it was written to discriminate — it would have passed on a report whose
    # fuzzyclaw zero was silently fabricated, as long as `waiting` said
    # UNMEASURED somewhere else on the page.
    assert "LIVE COUNT UNMEASURED" in a
    assert "LIVE COUNT UNMEASURED" not in b


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
                         "clickhouse", "fuzzyclaw", "ledger", "filters",
                         "caveats", "summary", "clawgate_queue",
                         # what this report contains NOTHING about, derived from
                         # the keys above rather than written down
                         "not_measured"}
    assert set(blob["hosts"]) == {"workbench", "laptop"}

    wb = blob["hosts"]["workbench"]
    assert set(wb) == {"reachable", "error", "ssh_target", "windows",
                       "live_window_ids", "windows_measured", "windows_error",
                       "captures_measured", "captures_status", "captures_seen"}
    assert wb["ssh_target"] is None
    assert wb["live_window_ids"] == ["@41", "@52", "@63"]
    assert wb["windows_measured"] is True
    assert wb["windows_error"] is None

    row = wb["windows"][0]
    assert row == {
        # the entity axis — `tmux` here because `fold_windows` folds panes
        "kind": "tmux",
        "host": "workbench",
        "session": "scratch7",
        "window_index": "3",
        "window_id": "@41",
        "window_name": "win-alpha",
        "codename": "Grove",
        # tier 1: the slot table names this session, so the cwd never gets a
        # vote — `repo-alpha` would be a DIFFERENT name for a window the
        # hotkeys, the HUD and the ledger all already call `Grove`.
        "label": "Grove",
        "label_source": "codename",
        # ...and the key that actually gets the operator there. `S` is this
        # fixture's, not the real table's — pinned so a hardcoded map cannot
        # satisfy it.
        "hotkey": "S",
        # 🔴 ...and the CHORD, derived from it in one place. `S` is UPPERCASE in
        # this fixture, so the shifted spelling is the correct one — and the
        # lower-case sibling (`Alt+v` from `v`) is pinned separately, because a
        # single-case golden cannot tell `f"Alt+Shift+{k}"` from a function that
        # returns the shifted form unconditionally.
        "hotkey_display": "Alt+Shift+S",
        "pane_id": "%11",
        "path": "/home/zach/workspace/repo-alpha",
        "command": "claude",
        "task": "Working on alpha",
        "claude": True,
        "busy": True,
        "age_secs": 1800.0,
        # `use_ledger=False` in this fixture, so fuzzyclaw is the only writer
        # that answered — and the row SAYS so rather than leaving the reader to
        # infer which source an age came from.
        "age_source": "fuzzyclaw",
        "status": "busy",
        # 🔴 The capture batch RAN (make_runner answers it) but its output
        # carries no markers, so this pane is `uncaptured` — measured absence
        # of THIS pane's text, not a measured "nothing is waiting". Both the
        # boolean and the signal list are None, never False/[].
        "waiting_probable": None,
        "waiting_signals": None,
        "waiting_status": "uncaptured",
        # 🔴 The FOURTH signal inherits the SAME not-measured path, and its null
        # is readable only because the status rides beside it. `uncaptured`, not
        # `ok`: this pane's screen was never parsed, so "no unsent prompt" is
        # not a thing this row is entitled to say.
        "unsent_prompt": None,
        "unsent_prompt_status": "uncaptured",
        # 🔴 `disabled`, NOT `uncaptured`, and the difference is the point: this
        # fixture does not pass `--pane-preview`, so the text was never asked
        # for. The sibling field above says `uncaptured` because the unsent
        # scrape WAS asked for and this pane's screen did not reach it. Two
        # fields riding one capture, reporting two different reasons for the
        # same null — which is exactly what a consumer needs to tell "nobody
        # asked" from "asked and missed".
        "pane_preview": None,
        "pane_preview_status": "disabled",
        "claude_session_id": "11111111-2222-4333-8444-555555555555",
        # 🔴 `runtime` names WHICH agent recorded the window. Null here because
        # this fixture runs `use_ledger=False`, so no writer answered — and
        # `claude: true` above is the pane's COMMAND matching /claude/, which is
        # a different fact. An opencode window is `claude: false` with
        # `runtime: "opencode"`, which is why the row needs both.
        "runtime": None,
        "ledger": None,
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
    # ...and this is the row the whole feature exists for: no codename, and a
    # cwd (`/home/zach/tmp`) that names it anyway.
    assert (second["label"], second["label_source"]) == ("tmp", "path")
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
        "total_sessions": 3, "claude": 2, "shell": 1,
        # 🔴 LITERAL, and the whole point: this fixture's 3 windows are one busy
        # claude, one idle claude and one unknown SHELL. No bucket publishes a
        # bare number, and no flat `idle` key exists to be read as an agent
        # count. `claude_only` false -> `excluded_shells` is None, not 0, and
        # so is `kinds_excluded_by_filter`: no filter ran, so "which kinds did
        # it remove" was never measured and `[]` would be a fake measurement.
        "status": {
            "busy": {"claude": 1, "shell": 0, "total": 1},
            "idle": {"claude": 1, "shell": 0, "total": 1},
            "stale": {"claude": 0, "shell": 0, "total": 0},
            "unknown": {"claude": 0, "shell": 1, "total": 1},
        },
        "claude_only": False,
        "excluded_shells": None,
        "kinds_excluded_by_filter": None,
        # 🔴 The `--match` trio under the SAME null-not-zero rule: no filter was
        # asked for, so the terms are null (never `[]`), the field list is null
        # (never the default tuple — publishing it would assert a search that
        # never ran) and the excluded count is null, never `0`.
        "match": None,
        "match_fields": None,
        "excluded_by_match": None,
        # 🔴 MIRRORED FROM `filters.matched`, and it is NOT `total_sessions` —
        # on a `detail` report those two disagree by construction. `None` here
        # because no row filter ran.
        "matched": None,
        "hosts_reachable": ["laptop", "workbench"],
        "hosts_unreachable": [],
        "fuzzyclaw_live": 1,
        "fuzzyclaw_status": "ok",
        "windows_unmeasured": [],
        # 🔴 LITERAL, and it is the unmeasured shape: the capture batch ran but
        # returned no markers, so 0 of 3 rows were scraped. `probable` is None
        # and `per_signal` is None — a `0` on either would be this report
        # answering "is anything waiting on me" with a look nobody took.
        # `unmeasured_reasons` says WHICH rows and why, so the None is
        # actionable rather than merely honest.
        "waiting": {
            "probable": None, "measured": 0, "unmeasured": 3,
            "per_signal": None,
            "unmeasured_reasons": {"uncaptured": 2, "not_claude": 1},
        },
        # 🔴 THE FOURTH SIGNAL, IN THE GOLDEN, AND IT IS THE UNMEASURED SHAPE
        # FOR THE SAME REASON — not a copy of the block above but the same
        # capture failing, reported under its own name. `count: None` is the
        # load-bearing literal: a `0` here would be this report answering "is
        # any work parked one Enter away" with a look nobody took, which is
        # precisely how five such windows went unreported on 79 live panes.
        "unsent_prompt": {
            "count": None, "measured": 0, "unmeasured": 3,
            "unmeasured_reasons": {"uncaptured": 2, "not_claude": 1},
        },
        # The clawgate queue was never read (no reader injected in this
        # fixture's environment), so the count is None with its discriminant —
        # and so is `stuck_count`, which is the same rule applied to the
        # stuck-dispatch half rather than a second, weaker one.
        "clawgate_queue": {"count": None, "status": "absent",
                           "stuck_count": None, "schema_ok": False},
        # 🔴 THE #419 METER, in the golden. One of these three windows has an
        # age and it came from fuzzyclaw — because this fixture runs
        # `use_ledger=False`. The two `none` rows are the shape the SHIPPED
        # default had for every row between #419 and the ledger: a null age with
        # nothing anywhere in the output naming which writer failed to supply
        # it. Pinned literally so a regression that re-zeroes the ages cannot
        # pass by leaving some unrelated total unchanged.
        "age_sources": {"fuzzyclaw": 1, "none": 2},
        # The entity axis, DERIVED from the rows. All three are tmux. Pinned
        # literally for the same reason as `age_sources`: it is the number that
        # moves when a row is built without a `kind`, and `total_sessions`
        # alone would not.
        "kind": {"tmux": 3},
        "rows_with_age": 1,
        "rows_with_session_id": 1,
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
    """🔴 Every COUNT is None under --no-fuzzyclaw, not 0.

    The directory is never read, so `files_seen: 0` would be a fabricated
    measurement — the same class of silent zero the `unmeasured` status exists
    to refuse. `status: "skipped"` discriminates it, but a discriminated lie is
    still a lie in the count, and a caller that reads `files_seen` without the
    status (which is exactly what the status exists to stop) gets a measured 0.
    """
    report = base_gather(use_fuzzyclaw=False)
    fz = report["fuzzyclaw"]
    assert fz["status"] == "skipped"
    for field in ("files_seen", "files_live", "files_unparseable",
                  "files_stale", "files_mismatched"):
        assert fz[field] is None, (
            f"{field} must be None under --no-fuzzyclaw: nothing was measured")
    assert fz["tasks"] == [] and fz["slot_conflicts"] == []
    # the summary carries the same None + its discriminant
    assert report["summary"]["fuzzyclaw_live"] is None
    assert report["summary"]["fuzzyclaw_status"] == "skipped"
    row = report["hosts"]["workbench"]["windows"][0]
    assert row["fuzzyclaw"] is None and row["claude_session_id"] is None
    # The banner names the flag that turns the source ON, because OFF is now
    # the default: telling a reader "--no-fuzzyclaw" blamed a flag they never
    # passed for an absence they did not ask for.
    rendered = sm.render_table(report)
    assert "opt in with --fuzzyclaw" in rendered
    assert "--no-fuzzyclaw" not in rendered


def test_the_skipped_and_measured_fuzzyclaw_zeroes_are_DIFFERENT_FACTS():
    """DISCRIMINATING CONTROL for the test above — the positive half.

    A genuinely measured zero must still report 0, or the fix above would have
    "solved" the fabricated zero by making every zero unreadable. Same call,
    only whether the source was read differs.
    """
    skipped = base_gather(use_fuzzyclaw=False)
    measured = base_gather(fuzzyclaw_texts=[])
    assert skipped["fuzzyclaw"]["files_seen"] is None
    assert measured["fuzzyclaw"]["status"] == "ok"
    assert measured["fuzzyclaw"]["files_seen"] == 0
    assert measured["fuzzyclaw"]["files_live"] == 0


def test_stale_threshold_flows_from_the_argument_into_the_rows():
    """Measured at TWO thresholds against ONE fixture (age 1800s)."""
    fresh = base_gather(threshold=3600)
    stale = base_gather(threshold=1800)
    assert fresh["hosts"]["workbench"]["windows"][0]["status"] == "busy"
    assert stale["hosts"]["workbench"]["windows"][0]["status"] == "stale"
    assert fresh["summary"]["status"]["stale"]["total"] == 0
    assert stale["summary"]["status"]["stale"]["total"] == 1
    # the row that moved is a CLAUDE row, so it moved on the claude half
    assert stale["summary"]["status"]["stale"] == {"claude": 1, "shell": 0,
                                                  "total": 1}


# =========================================================================== #
# §3.16b — the misnomer, banned structurally
# =========================================================================== #
def _all_keys(obj, out=None):
    """Every dict key anywhere in a nested payload."""
    if out is None:
        out = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.add(k)
            _all_keys(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _all_keys(v, out)
    return out


def test_no_key_named_blocked_on_me_survives_at_ANY_depth():
    """\U0001f534 THE MISNOMER, BANNED STRUCTURALLY, BECAUSE THE PROSE MITIGATION FAILED.

    `report["blocked_on_me"]` held the CLAWGATE APPROVAL QUEUE, not "everything
    waiting on you". This tool already carried a caveat saying exactly that,
    whose own text called the wrong reading "the misread this entry exists to
    prevent" — and a reader made that misread anyway, in a brief that then
    shipped to three subagents. A field NAME is read a hundred times for every
    once its caveat is. RULES.md: prefer the deterministic/structural fix over
    the prose one. The name is now `clawgate_queue`; this is what stops the old
    one coming back.

    Walked over the WHOLE payload rather than the top level, because the
    top-level key set is already pinned by the golden test while `lean_report`
    is a SEPARATE builder — a key resurrected inside `summary` or inside the
    lean projection would be invisible to that pin. Both surfaces here.

    \U0001f534 POSITIVE CONTROL, and not decoration: a walker wired to nothing
    returns an empty set, and `"blocked_on_me" not in set()` passes happily. The
    control asserts the walker really finds the key that REPLACED the misnomer,
    so an empty result cannot be read as a clean one.
    """
    report = base_gather()
    for label, payload in (("full", report), ("lean", lean_of(report))):
        keys = _all_keys(json.loads(json.dumps(payload, default=str)))
        assert keys, "%s: the walker found NO keys — it is wired to nothing" % label
        assert "clawgate_queue" in keys, (
            "%s: POSITIVE CONTROL FAILED — the walker cannot see the key that "
            "replaced the misnomer, so its verdict on the misnomer is worthless"
            % label)
        assert "blocked_on_me" not in keys, label


def test_the_clawgate_queue_and_the_tmux_WAITING_count_stay_SEPARATE_populations():
    """\U0001f534 A RELATIONSHIP, not a value. The rename fixes what the field is
    CALLED; this pins what it must not BECOME.

    The two answer different questions from different stores: `clawgate_queue`
    is the approval queue read out of the bar-status cache, and
    `summary.waiting.probable` is panes whose own tail looks like it is asking a
    human something. Summing them, or sourcing either from the other, rebuilds
    the exact conflation the rename removed.

    🔴 BOTH POPULATIONS ARE DRIVEN TO A REAL, MEASURED NUMBER, and that is the
    whole point. The default fixture leaves BOTH counts `None`, and the first
    version of this test asserted a "separation" that `None == None` satisfied
    trivially. The SECOND version fixed only half of it: it populated the cache
    (12 / stuck 1) but still ran on panes nobody scraped, so `waiting.probable`
    was `None` and the closing assertion reduced to `None != 12` — true no
    matter what. Measured: sourcing `summary.waiting.probable` from the clawgate
    `stuck_count` SURVIVED it, and that is precisely the cross-population
    conflation this test exists to ban.

    So the panes are scraped too: `%11` sits on a modal and `%21` is out of
    context, both Claude panes flag, and `waiting.probable` is a measured 2
    against a queue of 12 / pending 11 / stuck 1. The values are pairwise
    distinct AND the fixture-integrity loop below asserts that NO number this
    queue publishes equals 2 — so a mutant sourcing `probable` from ANY field of
    it moves the number and dies. (`schema=3` for exactly that reason: the
    canonical `_cache()` ships `schema: 2`, which would have collided with the
    expected count. 3 is a valid future bump — see
    `test_the_schema_gate_is_measured_either_side_of_the_boundary`.)

    🔴 `summary.clawgate_queue` IS a deliberate projection of the top-level
    field, not a duplicate to be removed — the summary carries count WITH its
    discriminant so a roll-up reader can tell "none pending" from "never
    measured". So the invariant is that the projection MIRRORS, on every field
    it republishes; a hardcoded value in either place breaks it.
    """
    report = waiting_gather(local={"%11": PANE_MENU},
                            remote={"%21": PANE_CTX_ZERO},
                            clawgate_reader=_cache(schema=3), now=NOW)
    top = report["clawgate_queue"]
    proj = report["summary"]["clawgate_queue"]
    waiting = report["summary"]["waiting"]

    # The cache really did land — without this the mirror assertions below could
    # be comparing None to None and pass with the reader unwired.
    assert top["count"] == 12 and top["stuck_count"] == 1, top
    assert top["pending_count"] == 11 and top["schema"] == 3, top

    # The projection mirrors, field for field.
    for field in ("count", "status", "stuck_count", "schema_ok"):
        assert proj[field] == top[field], (field, proj[field], top[field])

    # 🔴 The tmux waiting population is MEASURED — from the panes, not from the
    # queue. Both Claude panes flag, so this is a real 2 and never a None that
    # every inequality below would satisfy for free.
    assert set(waiting) >= {"probable", "measured", "unmeasured"}
    assert waiting["measured"] == 2, waiting
    assert waiting["probable"] == 2, waiting

    # 🔴 FIXTURE-INTEGRITY LOOP — what makes the assertion above able to SEE a
    # conflation mutant. No number the clawgate queue publishes may equal the
    # expected waiting count, so `probable = <any field of the queue>` changes
    # it and goes red. If a later fixture edit collapses that, this fails HERE,
    # loudly, instead of quietly making the test vacuous again.
    for field, value in sorted(top.items()):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        assert value != waiting["probable"], (
            "FIXTURE COLLAPSE: clawgate `%s` == the expected waiting count "
            "(%r), so a mutant sourcing one from the other would pass"
            % (field, value))

    # ...and the two stay structurally apart: a separate key, a separate shape.
    assert waiting["probable"] != top["count"]
    assert "waiting" not in top and "probable" not in proj


# A near-miss arm is only granted to tokens and keys of at least this length.
# Below it the distance-1 neighbourhood of a key stops being "a typo of a key"
# and becomes ordinary short prose: the payload's 2-char key `ts` sits one edit
# from `is`/`as`/`to`/`tsx`, and claiming those as payload pointers is the very
# false-red this helper was narrowed to remove. Measured (see
# `test_a_stale_SINGLE_WORD_pointer_is_caught_not_just_an_underscored_one`):
# with the floor at 4, every ordinary word tried sits >=2 edits from every
# top-level key, while every plausible drift of a real key sits at 1.
_NEAR_MISS_MIN_LEN = 4


def _within_one_edit(a, b):
    """True iff `a` becomes `b` under at most ONE edit — insert, delete,
    substitute, or TRANSPOSE two adjacent characters (restricted Damerau).

    🔴 TRANSPOSITION IS PART OF THE RULE AND IS NOT FREE-RIDING ON THE OTHER
    THREE. `sumamry`/`summary`, `hsots`/`hosts`, `ledegr`/`ledger`,
    `caevats`/`caveats` and `clikchouse`/`clickhouse` are every one of them
    Levenshtein distance TWO, so a plain-Levenshtein rule misses the single
    commonest real typo shape while claiming to cover "the drift shapes that
    actually occur" — measured, 5 of 5 transposition drifts tried were missed.
    The widening is not speculative either: measured against cracklib-small
    (50,692 entries matching `[a-z]+`), adding transposition to this payload's
    key set drags in ZERO further ordinary English words — the transposition
    neighbourhood of every key here is entirely non-words. Ledger and re-derive
    recipe: `test_the_near_miss_arm_PRICES_the_false_reds_it_buys`.
    """
    la, lb = len(a), len(b)
    if abs(la - lb) > 1:
        return False
    if la == lb:
        diff = [i for i, (x, y) in enumerate(zip(a, b)) if x != y]
        if len(diff) <= 1:
            return True              # at most ONE substitution...
        return (len(diff) == 2 and diff[1] == diff[0] + 1     # ...or exactly
                and a[diff[0]] == b[diff[1]]                  # one ADJACENT
                and a[diff[1]] == b[diff[0]])                 # transposition
    if la > lb:                      # normalise so `a` is the shorter one
        a, b, la, lb = b, a, lb, la
    i = 0
    while i < la and a[i] == b[i]:   # skip the common prefix...
        i += 1
    return a[i:] == b[i + 1:]        # ...the rest must match past one insert


def _names_a_top_level_key(tok, top_keys):
    """Is this bare word a pointer AT the payload — naming a top-level key, or
    one edit away from naming one (a typo, a plural, a small rename)?

    🔴 THE NEAR-MISS ARM IS OFFERED ONLY BY KEYS THAT CARRY NO UNDERSCORE,
    and that restriction costs nothing while removing the sharpest false red
    this rule had. Reasoning: the arm can only ever be reached by an
    underscore-FREE token — twice over, because the clause of
    `_payload_paths_in` that reaches it matches `[a-z]+` only, and anything
    carrying an underscore is already extracted one clause earlier and
    unconditionally. So against
    an underscored key the arm's only reachable shape is UNDERSCORE DELETION —
    and `localhost` is exactly one underscore-deletion from the key
    `local_host`, i.e. the ordinary vocabulary of a note about hosts was being
    read as a stale pointer. What is given up in exchange is the case of a note
    writing a snake_case key with its underscore simply dropped
    (`clawgatequeue` for `clawgate_queue`), which is not a typo shape that
    occurs; a real stale pointer at an underscored key KEEPS its underscore and
    is already caught outright by the earlier clause — those 4 keys were the
    only ones the pre-near-miss rule could see at all. Measured: the blind set
    stays exactly `['ts']` with the restriction in place.

    The `len(k) >= _NEAR_MISS_MIN_LEN` clause below is DEFENCE, not live cover:
    for this payload it is unreachable (the only sub-floor key is the 2-char
    `ts`, which the token-length guard plus the length-difference test in
    `_within_one_edit` already reject). It is kept so that a future 3-char key
    cannot silently open a near-miss neighbourhood over short prose, and it is
    made reachable — and mutation-killable — by a direct unit assertion in
    `test_the_near_miss_arm_PINS_its_own_width_and_preconditions`.
    """
    if tok in top_keys:
        return True
    if len(tok) < _NEAR_MISS_MIN_LEN:
        return False
    return any("_" not in k and len(k) >= _NEAR_MISS_MIN_LEN
               and _within_one_edit(tok, k)
               for k in top_keys)


def _payload_paths_in(text, report):
    """Backticked tokens in a note that CLAIM to be payload paths.

    Structural, not lexical. A token qualifies if it is (a) a DOTTED snake_case
    path, (b) a bare snake_case word carrying an UNDERSCORE, or (c) a bare word
    that NAMES A TOP-LEVEL KEY of `report`, or sits one edit from naming one.
    Prose like `list-panes` (hyphen) and `--claude-only` (dashes) is not
    mistaken for a key. What comes back is a list of things the note tells a
    JSON reader to go and look at.

    🔴 CLAUSE (c) IS DERIVED FROM THE REAL PAYLOAD, NEVER FROM A LITERAL LIST,
    and `report` is REQUIRED for exactly that reason — an optional argument
    would let a future call site silently drop back to the underscore-only rule
    that this docstring used to describe. Measured against a real
    `base_gather()` report: of its 12 top-level keys only 4 carry an
    underscore, so (a)+(b) alone left `caveats`, `clickhouse`, `filters`,
    `fuzzyclaw`, `hosts`, `ledger`, `summary` and `ts` unextractable — a note
    misspelling any of them was NOT CHECKED AT ALL. With (c) that is 11 of 12
    (all but the 2-char `ts`, below `_NEAR_MISS_MIN_LEN`).

    🔴 WHY A NEAR MISS AND NOT AN EXACT MATCH: an exact-match-only clause (c)
    is worthless as a guard. A token that exactly names a live key always
    resolves, so it can never go red; the pointer this guard exists to catch is
    by definition one that NO LONGER names a key. One edit covers the drift
    shapes that actually occur — a typo (`summry`), a lost character
    (`clickhous`), a plural gained or lost (`caveat`, `ledgers`), a
    transposition (`sumamry`, `hsots`).

    🔴 THE PRICE, MEASURED AGAINST THE RIGHT POPULATION. An earlier wording
    priced this at "one word wide — `host`, the ONLY such collision in the
    payload's 60 single-word segments". That sentence is TRUE and it is the
    wrong measurement: the population that can turn this gate red is the
    ENGLISH PROSE a future note is written in, not the payload's own key
    segments. Re-measured against cracklib-small (50,692 entries matching
    `[a-z]+`), the distance-1 neighbourhood of this payload's non-underscored
    top-level keys contains SEVENTEEN ordinary English words — `caveat`,
    `costs`, `falters`, `fillers`, `filter`, `fitters`, `ghosts`, `hoists`,
    `hoots`, `hoses`, `host`, `ledge`, `ledgers`, `ledges`, `leger`, `lodger`,
    `posts` — an order of magnitude more than "one word". Six of them
    (`caveat`, `costs`, `filter`, `ghosts`, `host`, `posts`) already occur in
    this repo's own prose, and each of them, planted into a real not_measured
    note, turns this gate red today. That ledger is pinned, tied to the key set
    it was measured against, in
    `test_the_near_miss_arm_PRICES_the_false_reds_it_buys` — so it cannot
    silently grow, and a changed key set forces a human to re-measure.

    🔴 THE TRADEOFF WAS TAKEN DELIBERATELY, NOT OVERLOOKED. The obvious
    mitigation — denylist ordinary English — is REJECTED, because the two
    populations overlap at exactly the words that matter: `caveat` is both an
    ordinary English word AND the most plausible typo of the key `caveats`, and
    so are `ledgers` for `ledger` and `filter` for `filters`. A denylist would
    buy back false-red budget by reintroducing blindness at the drift shape
    this rule was widened to catch. The asymmetry decides it: a false red costs
    ONE reword by the author who is editing that very note (the failure message
    names the token), while a false green ships a stale machine-readable
    pointer to every `--json` consumer, which is the failure this gate exists
    for. So the price is paid, stated, and pinned rather than reduced.

    🔴 ONE SHAPE OF THE PRICE *WAS* OVERLOOKED AND IS NOW REMOVED: the arm
    silently spanned UNDERSCORE DELETION from an underscored key, so
    `localhost` — one deletion from `local_host`, and precisely the vocabulary
    a note about hosts would use — was claimed. The near-miss arm is now
    offered only by keys carrying no underscore; see `_names_a_top_level_key`
    for why that costs nothing.

    🔴 WHY TOP-LEVEL KEYS AND NOT EVERY KEY SEGMENT — measured, not assumed.
    The same report has 136 distinct key segments, 60 of them single words.
    Anchoring on those instead is not a stricter version of this rule, it is a
    different and much worse one: `count`, `note`, `open`, `path`, `stale`,
    `status`, `waiting`, `error`, `task` and ~8 more are ordinary English that
    the tool's OWN caveat prose already backticks, and none of them resolve
    from the root, so every one would be a false red. Widening to one edit over
    that vocabulary is worse still — it swallows `state`, `mode` and `node`.
    Against the 12 TOP-LEVEL keys the separation is clean: every ordinary word
    tried is >=2 edits away (`standup` 6, `stalled` 6), every real drift is 1.

    🔴 WHAT THIS STILL DOES NOT CATCH — stated because the claim it replaces
    ("an underscore keeps the case this guard exists for") was measurably
    false, and then restated because ITS OWN first version was incomplete too.
    Three gaps, all measured:

      1. A WHOLESALE rename that leaves no similar key behind — `hosts`
         becoming `by_host`, 4 edits — is invisible to any payload-derived
         rule, because after the rename the old name is simply not in the
         payload to be recognised. The predecessor rule (`[a-z_]{3,}`) caught
         that only by treating EVERY bare word as a pointer, which is what made
         backticking `standup` or `stalled` fail this gate with no defect
         present.
      2. Any key shorter than `_NEAR_MISS_MIN_LEN`: `ts`, 2 chars. Pinned as
         the exact blind set in the test below, so it fails if it grows.
      3. TWO edits. One transposition IS covered (that gap was real and is
         closed — see `_within_one_edit`), but `hoods` for `hosts` (two
         substitutions), `clickhou` for `clickhouse` (two deletions) and a
         transposition-plus-substitution are not. Widening to two edits is not
         a free extension of the same argument: at distance 1 the separation
         from ordinary prose is clean (`standup` and `stalled` sit 6 edits
         out), and every additional edit multiplies the English neighbourhood
         that the paragraph above already prices at seventeen words. The width
         is pinned in
         `test_the_near_miss_arm_PINS_its_own_width_and_preconditions`.

    This helper buys back the typo/transposition/plural/small-rename population
    without buying back the `standup`-goes-red false red; 1–3 are known,
    accepted, and now asserted gaps rather than prose.
    """
    import re as _re
    top_keys = set(report)
    out = []
    for tok in _re.findall(r"`([^`]+)`", text):
        if _re.fullmatch(r"[a-z]+(?:_[a-z]+)*(?:\.[a-z]+(?:_[a-z]+)*)+", tok):
            out.append(tok)        # a dotted path is unambiguous on its own
        elif _re.fullmatch(r"[a-z]+(?:_[a-z]+)+", tok):
            out.append(tok)        # a bare word may carry the underscore...
        elif _re.fullmatch(r"[a-z]+", tok) and _names_a_top_level_key(tok, top_keys):
            out.append(tok)        # ...or name (or nearly name) a real key
    return out


def _resolves(report, path):
    node = report
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return False
        node = node[part]
    return True


def test_every_payload_path_a_not_measured_NOTE_points_at_actually_EXISTS():
    """\U0001f534 A CAVEAT IS A MACHINE-READABLE CLAIM, so its POINTERS are claims too.

    These notes are what a `--json` consumer reads to find out what this tool did
    not measure and where to look instead. This repo has already been bitten by a
    caveat that went stale the moment the code changed
    (`CAVEATS["fuzzyclaw_scope"]` told every consumer that remote rows carry null
    `age_secs` — exactly the field the ledger had just started filling). A note
    that says "see `summary.waiting.probable`" is worthless the day that key is
    renamed, and nothing until now checked it.

    Resolves each path against a REAL report rather than against a list of
    expected names, so this cannot be satisfied by keeping a stale allowlist in
    step with a stale note.

    \U0001f534 THREE CONTROLS. Positive: the extractor must find paths at all, and
    they must be MORE than one, or a regex that quietly stopped matching would
    read as "every pointer is valid". Negative: a note containing a path that
    does NOT exist must be reported — otherwise `_resolves` returning True for
    everything would look identical to a clean run. And the OTHER direction: an
    ordinary backticked word is not a pointer, so a future note that backticks
    a skill name cannot fail this gate with no defect present.
    """
    report = base_gather()
    notes = [p.get("note", "") for p in report["not_measured"]]
    assert notes, "no not_measured entries — nothing was checked"

    found = [pth for n in notes for pth in _payload_paths_in(n, report)]
    assert len(found) > 1, (
        "POSITIVE CONTROL FAILED — the extractor found %d payload paths across "
        "%d notes; a pointer guard that finds nothing passes vacuously"
        % (len(found), len(notes)))

    broken = [pth for pth in found if not _resolves(report, pth)]
    assert broken == [], (
        "a not_measured note points at payload path(s) that do not exist: %s"
        % broken)

    # NEGATIVE CONTROL: a planted bad pointer must be caught by the same code.
    planted = _payload_paths_in(
        "see `summary.waiting.probable` and `no_such_key`", report)
    assert "no_such_key" in planted, planted
    assert [p for p in planted if not _resolves(report, p)] == ["no_such_key"]

    # 🔴 THE OTHER DIRECTION, and the failure the old `[a-z_]{3,}` rule was one
    # wording away from: an ordinary backticked WORD is not a payload pointer.
    # Backticking a skill name or a plain word in a future note must NOT turn
    # this gate red — the whole point of a delta guard is that it fires on a
    # defect, not on prose.
    assert _payload_paths_in(
        "see `standup` for PRs, whether it is `stalled`, and `list-panes`",
        report) == []
    # ...and the discriminant is not "no bare word ever counts": a key that was
    # RENAMED AWAY still carries its underscore, and a note still pointing at
    # it is exactly the stale pointer this guard exists to catch.
    stale = _payload_paths_in("for approvals see `blocked_on_me`", report)
    assert stale == ["blocked_on_me"], stale
    assert not _resolves(report, "blocked_on_me")


def test_a_stale_SINGLE_WORD_pointer_is_caught_not_just_an_underscored_one():
    """\U0001f534 THE BLINDNESS ITSELF, pinned so it cannot silently come back.

    The guard above was once narrowed to "a bare word needs an UNDERSCORE",
    which removed a real false red but bought it with measured blindness: only
    4 of this payload's 12 top-level keys carry an underscore, so a note
    misspelling any of the other 8 was not merely un-caught, it was never even
    extracted. The justification offered at the time — "a key that was RENAMED
    AWAY still carries an underscore (`blocked_on_me`)" — is true of that one
    key and false for 8 of the tool's own 12.

    So this test asserts the property directly: a note pointing at a
    NONEXISTENT SINGLE-WORD payload key must be caught. It derives both the
    keys and the corruptions from a REAL report, so it cannot be satisfied by a
    hardcoded list drifting alongside a hardcoded rule.
    """
    report = base_gather()
    top = sorted(report)

    # 1. POSITIVE CONTROL on the near-miss primitive itself, both directions —
    #    a predicate nobody has watched return False is not a predicate.
    assert _within_one_edit("summary", "summry")      # one deletion
    assert _within_one_edit("ledger", "ledgers")      # one insertion
    assert _within_one_edit("hosts", "hests")         # one substitution
    assert not _within_one_edit("summary", "standup")
    assert not _within_one_edit("stale", "stalled")   # two edits, not one

    # 2. The literal pointers that SURVIVED the underscore-only rule. Each is
    #    one edit from a real top-level key and resolves against nothing.
    for bad, real in (("summry", "summary"), ("clickhous", "clickhouse"),
                      ("caveat", "caveats"), ("ledgers", "ledger")):
        assert real in report, (real, top)
        got = _payload_paths_in("the rows are in `%s`" % bad, report)
        assert got == [bad], (
            "BLINDNESS RETURNED — `%s` (one edit from the real top-level key "
            "`%s`) was not extracted as a payload pointer, so a note "
            "misspelling that key would pass this gate unnoticed; got %r"
            % (bad, real, got))
        assert not _resolves(report, bad), bad

    # 3. Now EVERY top-level key, enumerated — not a sample. Drop the last
    #    character of each and record which corruptions the rule fails to
    #    catch. The blind set is asserted EXACTLY, so it fails if it grows (a
    #    new key the rule cannot see) or shrinks (`ts` renamed) — either way a
    #    human re-reads `_NEAR_MISS_MIN_LEN` instead of inheriting it.
    blind = []
    for key in top:
        corrupt = key[:-1]
        if corrupt in report:
            continue                 # a corruption that lands on another real
                                     # key is not a stale pointer at all
        caught = (_payload_paths_in("see `%s`" % corrupt, report) == [corrupt]
                  and not _resolves(report, corrupt))
        if not caught:
            blind.append(key)
    assert blind == ["ts"], (
        "the set of top-level keys whose misspelling this gate CANNOT see "
        "changed: expected exactly ['ts'] (2 chars, under the %d-char "
        "near-miss floor), got %r out of %r"
        % (_NEAR_MISS_MIN_LEN, blind, top))

    # 4. THE CONTRAST THAT MAKES 3 A NUMBER AND NOT A VIBE. Under the
    #    underscore-only rule — written out literally here, not read off the
    #    implementation — 8 of those same corruptions were invisible: the 7
    #    real keys clause (c) buys back, plus `ts`, which neither rule sees.
    def _underscore_only(tok):
        return bool(re.fullmatch(r"[a-z]+(?:_[a-z]+)*(?:\.[a-z]+(?:_[a-z]+)*)+",
                                 tok)
                    or re.fullmatch(r"[a-z]+(?:_[a-z]+)+", tok))

    old_blind = [k for k in top
                 if k[:-1] not in report and not _underscore_only(k[:-1])]
    assert sorted(old_blind) == ["caveats", "clickhouse", "filters",
                                 "fuzzyclaw", "hosts", "ledger", "summary",
                                 "ts"], old_blind
    assert len(old_blind) == 8 and len(blind) == 1, (old_blind, blind)

    # 5. AND THE FALSE RED STAYS FIXED. The whole reason the underscore rule
    #    existed: an ordinary backticked word must not become a pointer. These
    #    sit 6 edits from the nearest top-level key, so the widened rule has
    #    margin, not luck.
    assert _payload_paths_in(
        "see `standup`, whether it is `stalled`, run `list-panes`", report) == []
    for word in ("standup", "stalled"):
        assert not _names_a_top_level_key(word, set(top)), word

    # 6. THE COST OF THE NEAR MISS, PINNED so the docstring's measurement
    #    cannot rot. Widening to one edit necessarily drags in words that are
    #    not top-level keys. Enumerate every single-word key segment at EVERY
    #    depth and assert which ones the rule now claims: the top-level ones,
    #    plus exactly `host` (one edit from `hosts`). If that set grows, a new
    #    key has put an ordinary word inside a near-miss neighbourhood and the
    #    false-red budget needs re-reading.
    segments = set()

    def _walk(node):
        if isinstance(node, dict):
            for k, v in node.items():
                if isinstance(k, str):
                    segments.add(k)
                _walk(v)
        elif isinstance(node, (list, tuple)):
            for v in node:
                _walk(v)

    _walk(report)
    single = sorted(s for s in segments if re.fullmatch(r"[a-z]+", s))
    assert len(single) >= 50, len(single)      # the enumeration really ran
    claimed = sorted(s for s in single if _payload_paths_in("`%s`" % s, report))
    assert claimed == sorted(set(top) & set(single) | {"host"}), (
        "the near-miss neighbourhood changed: the rule claims %r out of %d "
        "single-word key segments; expected the top-level keys plus 'host'"
        % (claimed, len(single)))


# The ordinary ENGLISH words that the near-miss arm claims as payload pointers,
# measured -- not guessed -- against cracklib-small (50,692 entries matching
# `[a-z]+`) over the non-underscored top-level keys of a real `base_gather()`
# report. This is the gate's FALSE-RED BUDGET: backticking any of these in a
# future not_measured note turns the pointer gate red with no defect present.
# Re-derive after any change to the key set or to `_within_one_edit`:
#     report = base_gather(); top = set(report)
#     [w for w in open(DICT) if re.fullmatch(r"[a-z]+", w.strip())
#      and _names_a_top_level_key(w.strip(), top) and w.strip() not in top]
_MEASURED_ENGLISH_FALSE_REDS = (
    "caveat", "costs", "falters", "fillers", "filter", "fitters", "ghosts",
    "hoists", "hoots", "hoses", "host", "ledge", "ledgers", "ledges", "leger",
    "lodger", "posts")

# The key set those 17 were measured against. If this moves, the ledger above
# is stale and a human must re-run the recipe -- that is the whole mechanism
# that stops the false-red budget growing in silence.
_LEDGER_KEY_SET = ("caveats", "clawgate_queue", "clickhouse", "filters",
                   "fuzzyclaw", "hosts", "ledger", "local_host",
                   "not_measured", "stale_threshold_secs", "summary", "ts")


def test_the_near_miss_arm_PRICES_the_false_reds_it_buys():
    """\U0001f534 THE COST OF CLAUSE (c), MEASURED OVER THE POPULATION THAT CAN PAY IT.

    The near-miss arm was priced in prose at "one word wide -- `host`, the ONLY
    such collision in the payload's 60 single-word segments". That sentence is
    true and it is the wrong measurement: nothing forces a future note to be
    written out of the payload's own key names. The population that can turn
    this gate red is ENGLISH PROSE, and over English the neighbourhood is
    SEVENTEEN words, not one -- an order of magnitude more.

    The tradeoff was then taken deliberately rather than mitigated: a denylist
    of "ordinary words" would buy false-red budget back by re-blinding the gate
    at exactly the drift shapes it was widened to catch (`caveat` is both an
    English word and the likeliest typo of the key `caveats`; so are `ledgers`
    for `ledger` and `filter` for `filters`). A false red costs one reword by
    the author editing that note; a false green ships a stale machine-readable
    pointer to every `--json` consumer. So the price stays -- and this test
    exists so that it is KNOWN and cannot grow unnoticed.
    """
    report = base_gather()
    top = set(report)

    # 1. THE LEDGER IS TIED TO THE KEY SET IT WAS MEASURED AGAINST. A new or
    #    renamed top-level key moves the whole neighbourhood, so the 17 below
    #    stop being a measurement and become a leftover -- fail loudly instead.
    assert tuple(sorted(top)) == _LEDGER_KEY_SET, (
        "the top-level key set changed, so the measured English false-red "
        "ledger is STALE -- re-run the recipe above `_MEASURED_ENGLISH_FALSE_"
        "REDS` and update it. expected %r, got %r"
        % (list(_LEDGER_KEY_SET), sorted(top)))

    # 2. EVERY LEDGER WORD IS REALLY CLAIMED, and really fails to resolve. An
    #    aspirational list nobody executed is not a measurement; this is the
    #    positive control on the ledger itself.
    for word in _MEASURED_ENGLISH_FALSE_REDS:
        assert _payload_paths_in("see `%s`" % word, report) == [word], (
            "%r is in the measured false-red ledger but the rule no longer "
            "claims it -- the ledger has drifted from the code" % word)
        assert not _resolves(report, word), word
    assert len(_MEASURED_ENGLISH_FALSE_REDS) == 17, (
        "the ledger holds %d words, not the 17 that were measured -- it has "
        "been edited without re-running the recipe above it: %r"
        % (len(_MEASURED_ENGLISH_FALSE_REDS), _MEASURED_ENGLISH_FALSE_REDS))

    # 3. NEGATIVE CONTROL on the same predicate, so 2 is not "it claims
    #    everything". These sit >=2 edits from every key and stay prose.
    for word in ("standup", "stalled", "session", "windows"):
        assert _payload_paths_in("see `%s`" % word, report) == [], word

    # 4. THE ONE FALSE RED THAT WAS NOT PRICED BUT REMOVED: `localhost` is a
    #    single underscore-deletion from the key `local_host`, so the arm --
    #    whose entire justification was written about single-WORD keys -- was
    #    silently spanning underscore removal, and claiming the exact
    #    vocabulary a note about hosts would use. The near-miss arm is now
    #    offered only by keys with no underscore.
    assert _payload_paths_in("reachable over `localhost`", report) == [], (
        "`localhost` is claimed as a payload pointer again -- the near-miss "
        "arm is spanning underscore-deletion from `local_host`")
    assert "local_host" in report and "localhost" not in report
    assert not _names_a_top_level_key("localhost", top)

    # 5. ...and removing it cost no coverage: an underscored key's stale
    #    pointer keeps its underscore, so clause (b) catches it outright,
    #    near-miss arm or not.
    for underscored in sorted(k for k in top if "_" in k):
        corrupt = underscored + "s"
        assert corrupt not in report
        assert _payload_paths_in("see `%s`" % corrupt, report) == [corrupt], (
            "clause (b) no longer catches a stale pointer at the underscored "
            "key %r, so restricting the near-miss arm DID cost coverage"
            % underscored)


def test_the_near_miss_arm_PINS_its_own_width_and_preconditions():
    """\U0001f534 HOW WIDE "ONE EDIT" IS -- asserted, because it was never exercised.

    The only negative controls the near-miss primitive had were `summary` vs
    `standup` (distance 6) and `stale` vs `stalled` (rejected by the LENGTH
    guard, never reaching the substitution arm). So no same-length distance-2
    pair was ever fed to it, and mutating `len(diff) <= 1` to `<= 2` -- i.e.
    doubling the neighbourhood the test above prices at 17 English words --
    survived the entire suite.

    Also pins the two things the docstrings assert about the rule but nothing
    executed: that a TRANSPOSITION is one edit (5 of 5 real transposition
    drifts were previously missed, all being Levenshtein distance 2), and that
    the `len(k) >= _NEAR_MISS_MIN_LEN` guard on the KEY side actually excludes
    a short key -- unreachable for this payload, so it is exercised directly.
    """
    report = base_gather()
    top = set(report)
    qualifying = sorted(k for k in top
                        if "_" not in k and len(k) >= _NEAR_MISS_MIN_LEN)
    assert len(qualifying) >= 6, qualifying      # the enumeration really ran

    # 1. TRANSPOSITION IS ONE EDIT. Every one of these is Levenshtein 2, so
    #    before this they were invisible while the docstring claimed "one edit
    #    covers the drift shapes that actually occur".
    for bad, real in (("sumamry", "summary"), ("hsots", "hosts"),
                      ("ledegr", "ledger"), ("caevats", "caveats"),
                      ("clikchouse", "clickhouse")):
        assert real in report, real
        assert _within_one_edit(bad, real), (
            "%r is one ADJACENT TRANSPOSITION from the real key %r and must "
            "count as one edit" % (bad, real))
        assert _payload_paths_in("the rows are in `%s`" % bad, report) == [bad]
        assert not _resolves(report, bad), bad

    # 2. ...AND STOPS THERE. Same length, two substitutions, no swap: these
    #    must NOT be one edit. This is the assertion that a `<= 1` -> `<= 2`
    #    mutant dies on.
    for a, b in (("hoods", "hosts"), ("summers", "summary"),
                 ("folders", "filters"), ("lodges", "ledger")):
        assert not _within_one_edit(a, b), (
            "%r is TWO substitutions from %r and must not count as one edit -- "
            "the substitution arm has been widened past one edit, which "
            "multiplies the measured English false-red neighbourhood" % (a, b))
        assert _payload_paths_in("see `%s`" % a, report) == [], (a, b)

    # 3. A TRANSPOSITION MUST BE ADJACENT. Swapping two characters that are not
    #    neighbours is two edits, not one.
    for key in qualifying:
        chars = list(key)
        for i in range(len(chars)):
            for j in range(i + 2, len(chars)):
                if chars[i] == chars[j]:
                    continue
                swapped = list(chars)
                swapped[i], swapped[j] = swapped[j], swapped[i]
                tok = "".join(swapped)
                if any(_within_one_edit(tok, k) for k in qualifying):
                    continue         # lands inside some other key's real D1
                assert not _names_a_top_level_key(tok, top), (
                    "%r is a NON-ADJACENT swap in %r -- two edits -- and must "
                    "not be claimed" % (tok, key))

    # 4. THE KEY-SIDE LENGTH FLOOR IS REAL, exercised directly because this
    #    payload cannot reach it: its only sub-floor key is the 2-char `ts`,
    #    which `_within_one_edit`'s length test already rejects against any
    #    >=4-char token. Kept as defence against a future 3-char key opening a
    #    near-miss neighbourhood over short prose.
    assert _NEAR_MISS_MIN_LEN == 4, _NEAR_MISS_MIN_LEN
    assert _within_one_edit("abcd", "abc"), "precondition: one deletion apart"
    assert not _names_a_top_level_key("abcd", {"abc"}), (
        "a 3-char key (< _NEAR_MISS_MIN_LEN) must not offer a near-miss arm; "
        "the len(k) guard in _names_a_top_level_key has been dropped")
    assert _names_a_top_level_key("abcd", {"abcd"}), (
        "control: an EXACT match must still be claimed regardless of length")
    assert not _names_a_top_level_key("tsx", top), "tsx / ts: below the floor"


def test_the_near_miss_neighbourhood_is_EXACTLY_one_edit_by_construction():
    """\U0001f534 THE NEIGHBOURHOOD ENUMERATED, NOT SAMPLED, IN BOTH DIRECTIONS.

    The tests above feed the rule hand-picked tokens, which pins the shapes
    someone thought of. This one generates the exact distance-1 neighbourhood
    of every qualifying key -- delete, substitute, insert, adjacent-transpose,
    over the whole alphabet -- and requires the rule to claim ALL of it, then
    generates distance-2 tokens of each shape and requires it to claim NONE.
    A rule quietly narrowed (some drift shape no longer covered) and a rule
    quietly widened (the English false-red ledger silently doubling) are
    different mutants, and this is the assertion that separates them.
    """
    report = base_gather()
    top = set(report)
    qualifying = sorted(k for k in top
                        if "_" not in k and len(k) >= _NEAR_MISS_MIN_LEN)
    alphabet = "abcdefghijklmnopqrstuvwxyz"

    def _d1(word):
        out = set()
        for i in range(len(word)):
            out.add(word[:i] + word[i + 1:])                      # delete
            for c in alphabet:
                if c != word[i]:
                    out.add(word[:i] + c + word[i + 1:])          # substitute
            if i + 1 < len(word) and word[i] != word[i + 1]:
                out.add(word[:i] + word[i + 1] + word[i]          # transpose
                        + word[i + 2:])
        for i in range(len(word) + 1):
            for c in alphabet:
                out.add(word[:i] + c + word[i:])                  # insert
        out.discard(word)
        return out

    near = set()
    for key in qualifying:
        near |= _d1(key)
    near = {t for t in near if len(t) >= _NEAR_MISS_MIN_LEN}
    assert len(near) > 1500, len(near)          # the construction really ran
    unclaimed = sorted(t for t in near if not _names_a_top_level_key(t, top))
    assert unclaimed == [], (
        "%d of %d constructed distance-1 neighbours are NOT claimed, so the "
        "near-miss arm is NARROWER than one edit and some real drift shape is "
        "no longer covered: %r" % (len(unclaimed), len(near), unclaimed[:12]))

    far = set()
    for key in qualifying:
        for i in range(len(key)):
            for j in range(i + 1, len(key)):
                if key[i] != "x" and key[j] != "x":
                    far.add(key[:i] + "x" + key[i + 1:j] + "x" + key[j + 1:])
                far.add(key[:i] + key[i + 1:j] + key[j + 1:])      # two deletes
        far.add("qz" + key)                                        # two inserts
        far.add(key + "qz")
    far = {t for t in far
           if len(t) >= _NEAR_MISS_MIN_LEN and t not in near and t not in top}
    assert len(far) > 100, len(far)             # the construction really ran
    claimed_far = sorted(t for t in far if _names_a_top_level_key(t, top))
    assert claimed_far == [], (
        "%d of %d constructed distance-2 tokens ARE claimed, so the rule is "
        "WIDER than one edit and the measured 17-word English false-red ledger "
        "no longer bounds the cost: %r"
        % (len(claimed_far), len(far), claimed_far[:12]))


def test_payload_paths_in_REQUIRES_a_report_and_cannot_default_to_none():
    """\U0001f534 THE ARGUMENT THAT MAKES CLAUSE (c) PAYLOAD-DERIVED, PINNED IN CODE.

    `_payload_paths_in`'s docstring says `report` is REQUIRED "for exactly that
    reason -- an optional argument would let a future call site silently drop
    back to the underscore-only rule". That was asserted in prose only:
    mutating the signature to `report={}` survived the whole suite, because
    every existing call site happens to pass one. A defaulted `report` gives an
    empty `top_keys`, so clause (c) can never fire and the guard silently
    reverts to the blindness it was widened to remove -- with no test failing.
    """
    import inspect

    sig = inspect.signature(_payload_paths_in)
    assert list(sig.parameters) == ["text", "report"], sig
    assert sig.parameters["report"].default is inspect.Parameter.empty, (
        "`report` acquired a default (%r) -- a call site that omits it would "
        "get an empty key set, silently disabling clause (c) and reverting the "
        "guard to the underscore-only rule with no test going red"
        % (sig.parameters["report"].default,))
    with pytest.raises(TypeError):
        _payload_paths_in("see `summry`")

    # ...and the reason it matters, executed: with an empty report the
    # single-word arm is dead, which is exactly the pre-fix blindness.
    report = base_gather()
    assert _payload_paths_in("see `summry`", report) == ["summry"]
    assert _payload_paths_in("see `summry`", {}) == []



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


def test_table_shows_the_LABEL_column_and_the_frame_did_not_get_wider():
    """🔴 The trade is the assertion. CODENAME (9 wide) became LABEL (14) and
    TASK gave up exactly those 5 characters, so the frame is byte-for-byte the
    width it was — checked against the rule line the renderer prints, not
    against a number restated here.
    """
    text = sm.render_table(base_gather())
    header = next(ln for ln in text.splitlines() if ln.strip().startswith("HOST"))
    rule = next(ln for ln in text.splitlines() if "─" in ln)
    assert "LABEL" in header and "CODENAME" not in header
    assert len(rule.strip()) == 112

    # 🔴 The width claim, read off the FORMAT STRING rather than off one
    # rendered line — a line's width depends on the data in it, so a row that
    # happens to be short would satisfy a length check while the frame grew.
    widths = [int(w) for w in re.findall(r"\{:<(\d+)\}", sm._ROW_FMT)]
    assert widths == [9, 12, 3, 14, 25, 6, 8, 4]     # host ses win LABEL task …
    assert sum(widths) == 9 + 12 + 3 + 9 + 30 + 6 + 8 + 4, (
        "the table got wider. The pre-change columns were "
        "host9 session12 win3 CODENAME9 task30 kind6 status8 age4; LABEL may "
        "only grow by what another column gives up")

    # Nothing was LOST by dropping the column: for a slot session the label IS
    # the codename — and it now carries the HOTKEY too, which CODENAME never
    # did and which is the half that gets the operator to the window.
    assert "Grove (S)" in text
    # ...and a row that used to render `—` now says where it is — with NO
    # fabricated key, because none exists for a non-slot session.
    assert "tmp" in text
    assert "tmp (" not in text
    # The longest possible slot cell still fits the column uncut.
    assert len(sm.render_label({"label": "Yarrow", "hotkey": "Y"})) <= 14
    # The header and the rows come from ONE format string, so they cannot skew.
    assert sm._ROW_FMT.count("{") == 9
    assert header == sm._ROW_FMT.format("HOST", "SESSION", "WIN", "LABEL",
                                        "TASK", "CLASS", "STATUS", "AGE", "WAIT")


def test_table_does_not_crash_on_none_valued_fields():
    report = base_gather()
    row = report["hosts"]["workbench"]["windows"][0]
    for key in ("task", "codename", "label", "label_source", "status",
                "age_secs", "session"):
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


@pytest.mark.parametrize("host,stderr,exit_name,reachable,found,no_server", [
    ("laptop",    "ssh: No route to host",     "EXIT_UNAVAILABLE",
     False, None,  False),
    ("workbench", "no server running on /tmp/x", "EXIT_NO_SERVER",
     True,  False, True),
    ("workbench", "can't find window: nope",   "EXIT_USAGE",
     True,  False, False),
])
def test_main_tail_json_prints_the_payload_on_the_FAILURE_exits(
        monkeypatch, capsys, host, stderr, exit_name, reachable, found,
        no_server):
    """🔴 `--json` returned BEFORE the print on exits 2, 4 and 5.

    So the three outcomes whose whole point is discrimination
    (`reachable`/`found`/`no_server`) printed NOTHING on stdout, while SKILL.md
    documented `found: null` as "the host never answered" and
    `reference/cross-host.md` documented a payload for each row of its 5-row
    table. A machine consumer got an empty stdout and an exit code — the exit
    code alone cannot distinguish "wrong spelling" from "server down" without
    re-parsing English off stderr.

    The exit codes and the stderr sentences are unchanged; only stdout gains.
    """
    monkeypatch.setattr(sm, "local_host_label", lambda *a, **k: "workbench")
    monkeypatch.setattr(sm, "_default_runner", lambda argv, t: (1, "", stderr))
    rc = sm.main(["tail", "nope:9", "--host", host, "--json"])
    cap = capsys.readouterr()
    blob = json.loads(cap.out)          # would raise on the old empty stdout
    assert rc == getattr(sm, exit_name)
    assert blob["reachable"] is reachable
    assert blob["found"] is found
    assert blob["no_server"] is no_server
    assert blob["host"] == host and blob["target"] == "nope:9"
    assert stderr in (blob["error"] or ""), "the payload carries the real error"
    assert cap.err, "the human sentence still goes to stderr"


@pytest.mark.parametrize("host,stderr", [
    ("laptop", "ssh: No route to host"),
    ("workbench", "no server running on /tmp/x"),
    ("workbench", "can't find window: nope"),
])
def test_main_tail_WITHOUT_json_keeps_stdout_empty_on_those_exits(
        monkeypatch, capsys, host, stderr):
    """NEGATIVE CONTROL for the test above: the payload is printed because
    `--json` was asked for, not unconditionally. A plain `tail` that dumped JSON
    into a pipe expecting scrollback would be a new defect."""
    monkeypatch.setattr(sm, "local_host_label", lambda *a, **k: "workbench")
    monkeypatch.setattr(sm, "_default_runner", lambda argv, t: (1, "", stderr))
    sm.main(["tail", "nope:9", "--host", host])
    cap = capsys.readouterr()
    assert cap.out == ""
    assert cap.err


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
    assert sum(s["status"][b]["total"] for b in sm.STATUS_BUCKETS) \
        == s["total_sessions"]
    # ...and each bucket's CLASS KEYS account for its own total, so a row cannot
    # be counted in a bucket without also being counted in some class.
    #
    # 🔴 Summed over whatever class keys the bucket actually has, NOT over
    # `claude + shell` by name. Naming the two would have quietly stopped being
    # an accounting check the moment a third class appeared: the sum would still
    # be computed, still be compared, and simply omit the new class from both
    # sides of nothing. This fixture is tmux-only, so the classes here ARE
    # claude/shell — the generality is what stops the guard rotting when they
    # are not (see the cluster tests in §kind).
    for b in sm.STATUS_BUCKETS:
        cell = s["status"][b]
        assert sum(v for k, v in cell.items() if k != "total") == cell["total"]
    assert s["claude"] + s["shell"] == s["total_sessions"]
    # This fixture really is tmux-only — stated, so the line above is read as
    # "no cluster rows here", not as a claim that the two always sum.
    assert s["kind"] == {"tmux": s["total_sessions"]}


# =========================================================================== #
# §4 — THE SECOND FIX ROUND
#
# Everything below pins behaviour the FIRST fix round introduced or left. Each
# test names the mutation it kills, because a test whose mutant was never run
# is a claim, not evidence.
# =========================================================================== #

# 🔴 A second live window id sharing @41's slot. This is the shape real tmux
# CANNOT produce — a slot belongs to exactly one window — so this fixture builds
# the impossible on purpose to exercise the drop from both directions. The shape
# that IS reachable (two task FILES carrying one `window_id`) has its own
# fixture and its own gather-level test further down; do not read this one as
# proof that `slot_conflicts` is unreachable in production.
#
# Every value is pairwise distinct from TASK_LIVE's EXCEPT the slot itself —
# the one field that must collide for the conflict to exist.
_TASK_LIVE_TWIN = {
    "task": "task-twin-text",
    "window_id": "@52",
    "tmux_session": "scratch7",
    "window_index": 3,
    "status": "running",
    "cwd": "/home/zach/workspace/repo-twin",
    "claude_session": "77777777-6666-4555-8444-333333333333",
    "started": "2026-08-11T10:00:00+00:00",
    "last_activity": "2026-08-11T11:45:00+00:00",
    "summary": "summary-twin",
    "transcript_path": "/home/zach/.claude/projects/proj-twin/twin.jsonl",
}
# @41 AND @52 both report slot scratch7:3, so both task files pass the
# relationship guard and then collide at the index.
_CONTESTED_WINDOWS = "@41|3|scratch7\n@52|3|scratch7\n@63|1|other\n"


def _contested_report():
    return base_gather(
        runner=make_runner(local_windows=_CONTESTED_WINDOWS),
        fuzzyclaw_texts=[json.dumps(TASK_LIVE), json.dumps(_TASK_LIVE_TWIN)])


def test_the_contested_slot_fixture_actually_produces_a_conflict():
    """INSTRUMENT CHECK before any verdict is read off this fixture.

    If the relationship guard rejected one of the two files, every conflict test
    below would pass vacuously against an empty list.
    """
    report = _contested_report()
    assert report["fuzzyclaw"]["files_live"] == 2, "both files must SURVIVE"
    assert report["fuzzyclaw"]["files_stale"] == 0
    assert report["fuzzyclaw"]["files_mismatched"] == 0
    assert len(report["fuzzyclaw"]["slot_conflicts"]) == 1


# --------------------------------------------------------------------------- #
# §4.1 — 🟡 A: `tail` against a host whose tmux SERVER is down
#
# `run_tmux` classifies "no server running" as REACHABLE — correct for a
# whole-host scan (a live host with zero windows). But `tail_window`'s
# `if res["reachable"]` branch fired on it FIRST and published
# `found: True, text: ""` -> EXIT_EMPTY. Two defects in one: `found: True` is a
# false fact (nothing was found), and EXIT_EMPTY is documented to mean "the
# window exists and its scrollback is empty" — which this is not.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("stderr", [
    "no server running on /tmp/tmux-1000/default",
    "error connecting to /tmp/tmux-1000/default (No such file or directory)",
])
def test_tail_against_a_DOWN_SERVER_is_not_found_not_an_empty_window(stderr):
    """🔴 KILLS: moving the no_server branch below the `reachable` return.

    The distinguishing fact is `found`, not the text: both cases produce "".
    """
    res = sm.tail_window("scratch7:3", "workbench", "workbench",
                         runner=lambda argv, t: (1, "", stderr))
    assert res["reachable"] is True, "the host itself answered"
    assert res["found"] is False, (
        "no window was found — a down server has zero windows, so reporting "
        "found: True states a fact that was never established")
    assert res["no_server"] is True
    assert res["text"] == "", (
        "tmux's stderr is not scrollback and must never be handed back as if "
        "it were the window's output")
    assert stderr in res["error"], (
        "quote the REAL tmux line — a generic fallback throws away which "
        "socket it was looking for, which is the whole diagnosis")


def test_main_tail_plain_text_says_when_it_DEFAULTED_to_the_local_host(
        monkeypatch, capsys):
    """🟢 `host_defaulted` was recorded in the JSON only, so a plain-text tail
    that silently searched the wrong machine read as a quiet window.

    The note goes to STDERR, so piping the scrollback is unaffected.
    """
    monkeypatch.setattr(sm, "local_host_label", lambda *a, **k: "workbench")
    monkeypatch.setattr(sm, "_default_runner",
                        make_runner(local_capture="scrollback text\n"))
    rc = sm.main(["tail", "scratch7:3"])                   # no --host
    cap = capsys.readouterr()
    assert rc == sm.EXIT_OK
    assert cap.out == "scrollback text\n", "stdout stays pure capture"
    assert "defaulted to the local host" in cap.err
    assert "workbench" in cap.err


def test_main_tail_with_an_EXPLICIT_host_does_not_claim_it_defaulted(
        monkeypatch, capsys):
    """POSITIVE CONTROL: a note that always prints tells the reader nothing."""
    monkeypatch.setattr(sm, "local_host_label", lambda *a, **k: "workbench")
    monkeypatch.setattr(sm, "_default_runner",
                        make_runner(local_capture="scrollback text\n"))
    sm.main(["tail", "scratch7:3", "--host", "workbench"])
    assert "defaulted" not in capsys.readouterr().err


def test_tail_of_a_REAL_EMPTY_WINDOW_still_reports_found():
    """POSITIVE CONTROL for the test above.

    Both cases yield text == "". If the fix had made every empty capture
    `found: False` it would have "fixed" the false fact by breaking the true
    one. Same empty text, opposite `found`.
    """
    res = sm.tail_window("scratch7:3", "workbench", "workbench",
                         runner=lambda argv, t: (0, "", ""))
    assert res["reachable"] is True
    assert res["found"] is True, "the window exists; its scrollback is empty"
    assert res["no_server"] is False
    assert res["text"] == ""


def test_the_down_server_and_empty_window_zeroes_get_DIFFERENT_EXIT_CODES(
        monkeypatch, capsys):
    """🔴 THE DISCRIMINATING CONTROL, measured at BOTH points.

    Before the fix both of these exited 3. The auditor measured exactly that:
        server down          -> found: True, text: '' -> exit 3
        window exists, empty -> found: True, text: '' -> exit 3
    One exit code cannot carry two facts, and SKILL.md's table claimed the
    second meaning for both.
    """
    monkeypatch.setattr(sm, "local_host_label", lambda *a, **k: "workbench")

    monkeypatch.setattr(sm, "_default_runner",
                        lambda argv, t: (1, "", "no server running on /tmp/x"))
    rc_down = sm.main(["tail", "scratch7:3", "--host", "workbench"])
    err_down = capsys.readouterr().err

    monkeypatch.setattr(sm, "_default_runner", lambda argv, t: (0, "", ""))
    rc_empty = sm.main(["tail", "scratch7:3", "--host", "workbench"])
    capsys.readouterr()

    assert rc_empty == sm.EXIT_EMPTY == 3
    assert rc_down == sm.EXIT_NO_SERVER == 5
    assert rc_down != rc_empty, (
        "the whole finding: one exit code for two different facts")
    # ...and it is not folded into the OTHER neighbouring meanings either
    assert rc_down != sm.EXIT_USAGE, "the target may be spelled perfectly"
    assert rc_down != sm.EXIT_UNAVAILABLE, "the host DID answer"
    assert rc_down != sm.EXIT_OK

    assert "no tmux server" in err_down
    assert "no such window" not in err_down, (
        "sending the operator to re-check spelling is the wrong repair")
    # 🔴 KILLS: `{res['error']}` -> any constant in the CLI's no-server line.
    # `tail_window` pins the stderr it CARRIES, but the operator-facing message
    # was pinned only on the words "no tmux server", which a constant satisfies
    # — so the line could quote the wrong error with the suite green. The socket
    # path is the diagnosis (wrong TMUX_TMPDIR vs a genuinely dead server).
    assert "no server running on /tmp/x" in err_down, (
        "the REAL tmux line must reach the operator, not a generic sentence")


def test_run_tmux_carries_the_no_server_discriminant_without_changing_scan():
    """The SEAM: `run_tmux` had to start reporting `no_server` for `tail` to be
    able to branch on it, WITHOUT changing what a whole-host scan sees."""
    down = sm.run_tmux(["tmux", "ls"], "workbench", "workbench",
                       runner=lambda argv, t: (1, "", "no server running"))
    assert down["reachable"] is True, "a scan still sees a live, empty host"
    assert down["error"] is None, "and still reports no error for it"
    assert down["no_server"] is True

    ok = sm.run_tmux(["tmux", "ls"], "workbench", "workbench",
                     runner=lambda argv, t: (0, "out", ""))
    assert ok["no_server"] is False and ok["stdout"] == "out"

    dead = sm.run_tmux(["tmux", "ls"], "laptop", "workbench",
                       runner=lambda argv, t: (255, "", "No route to host"))
    assert dead["reachable"] is False and dead["no_server"] is False


def test_a_down_server_scan_still_reports_a_reachable_host_with_zero_windows():
    """REGRESSION FENCE around the fix: the scan path must be untouched."""
    report = base_gather(runner=make_runner(
        local_rc=1, local_err="no server running on /tmp/x",
        local_windows_rc=1, local_windows_err="no server running on /tmp/x"))
    wb = report["hosts"]["workbench"]
    assert wb["reachable"] is True, "no server running is a REACHABLE host"
    assert wb["windows"] == []
    assert wb["windows_measured"] is True
    assert wb["live_window_ids"] == [], "measured, and genuinely empty"


# --------------------------------------------------------------------------- #
# §4.2 — 🟡 B: `detail_history` stated a MEASURED negative over an UNMEASURED set
#
# One hardcoded reason answered every no-session-id case, so a `detail --json`
# printed `LIVE COUNT UNMEASURED` and then asserted a measured absence about
# that same unmeasured set a few lines later.
# --------------------------------------------------------------------------- #
_MEASURED_ABSENCE = "no live fuzzyclaw task file joined to it"


def test_no_session_reason_measured_absence_is_the_ONLY_measured_branch():
    """The baseline: fuzzyclaw ran, the local window simply has no task."""
    report = base_gather(fuzzyclaw_texts=[])
    narrowed = sm.filter_report(report, "misc", "5")
    reason = sm.no_session_reason(narrowed)
    assert _MEASURED_ABSENCE in reason
    assert "NOT a measured absence" not in reason


@pytest.mark.parametrize("kw,marker", [
    (dict(use_fuzzyclaw=False), "--no-fuzzyclaw"),
    (dict(runner=make_runner(local_windows_rc=1,
                             local_windows_err="list-windows died")),
     "intersection never ran"),
])
def test_no_session_reason_refuses_to_claim_a_measured_absence(kw, marker):
    """🔴 KILLS: reverting `no_session_reason` to the single hardcoded string.

    Both of these leave `fuzzyclaw` UNMEASURED. The old code emitted the
    measured-absence sentence unchanged for each.
    """
    report = base_gather(**kw)
    narrowed = sm.filter_report(report, "scratch7", "3")
    reason = sm.no_session_reason(narrowed)
    assert marker in reason
    assert "NOT a measured absence" in reason
    assert _MEASURED_ABSENCE not in reason, (
        "nothing was measured, so no absence can be reported as measured")


def test_the_unmeasured_reason_carries_WHY_the_intersection_never_ran():
    """"Nothing was measured" without the CAUSE is half a fact.

    KILLS: dropping `fz["error"]` from the unmeasured branch. The reason is the
    only place a `detail` reader learns the intersection failed because
    list-windows died, rather than because the host was never scanned.
    """
    report = base_gather(runner=make_runner(
        local_windows_rc=1, local_windows_err="WINDOWS-STDERR-b41c"))
    narrowed = sm.filter_report(report, "scratch7", "3")
    reason = sm.no_session_reason(narrowed)
    assert "WINDOWS-STDERR-b41c" in reason, (
        "the underlying list-windows failure must be quoted, not summarised "
        "away — it is the difference between two distinct causes")
    assert "reason unrecorded" not in reason


def test_a_scan_that_never_touched_the_local_host_lands_on_the_REMOTE_branch():
    """RENAMED, because the old name claimed a branch it never exercised.

    It was `..._names_an_UNSCANNED_local_host_distinctly` and was presented as
    the second way to reach the `unmeasured` branch. Its fixture cannot reach
    that branch: `--host laptop` produces laptop-only rows, and the REMOTE
    branch is checked first, so the reason names fuzzyclaw's local-only limit.
    The cause the old name advertised is in fact structurally unreachable here —
    no local scan means no local row, so the remote branch always wins. Renamed
    rather than rewritten: the ORDERING it actually pins is worth keeping.

    🔴 KILLS: deleting the remote branch (the reason then reads "the fuzzyclaw
    intersection never ran"), and moving it below the `unmeasured` branch.
    """
    report = base_gather(hosts=("laptop",))
    assert report["fuzzyclaw"]["status"] == "unmeasured", (
        "fixture sanity: the local host was never scanned, so the intersection "
        "genuinely did not run — and the remote branch still answers first")
    narrowed = sm.filter_report(report, "naida-dev", "1")
    reason = sm.no_session_reason(narrowed)
    assert "LOCAL host only" in reason and "laptop" in reason
    assert "intersection never ran" not in reason
    assert "NOT a measured absence" in reason
    assert _MEASURED_ABSENCE not in reason


# 🔴 One `session:index` present on BOTH hosts — the DEFAULT `--host all` shape,
# and the common one: measured on the live hosts 2026-08-11, 13 of the laptop's
# 20 windows shared a `session:index` with a workbench window (`0:1`, `0:2`,
# `0:3`, `scratch:1`, `scratch2:1`, `scratch3:1`, …). Scratch sessions collide
# by construction, so this is not an edge case.
_SHARED_SLOT_LOCAL = (
    f"%31|3001|scratch|1|win-shared|/home/zach/workspace/repo-alpha|claude"
    f"|{BRAILLE} workbench side of a shared slot")
_SHARED_SLOT_REMOTE = (
    f"%32|3002|scratch|1|win-shared|/home/zach/workspace/repo-alpha|claude"
    f"|{SPARKLE} laptop side of the SAME session:index")


def test_a_slot_present_on_BOTH_hosts_is_never_a_measured_absence():
    """🔴 KILLS: `if remote:` -> `if all(r.host != local for r in rows)`.

    The predicate used to be `all(...)`. With rows from both hosts it was False,
    so the function fell through to the measured-absence sentence — a MEASURED
    negative covering a laptop row that fuzzyclaw never searched, since the task
    files are local state. `any` remote row is enough to forbid the claim.
    """
    report = base_gather(
        runner=make_runner(local_panes=_SHARED_SLOT_LOCAL,
                           remote_panes=_SHARED_SLOT_REMOTE),
        fuzzyclaw_texts=[])
    narrowed = sm.filter_report(report, "scratch", "1")
    rows = [r for h in narrowed["hosts"].values() for r in h["windows"]]
    assert sorted(r["host"] for r in rows) == ["laptop", "workbench"], (
        "fixture sanity: this is the MIXED row set, not an all-remote one")
    assert narrowed["fuzzyclaw"]["status"] == "ok", (
        "fixture sanity: the local host WAS searched — so an `all()` predicate "
        "reaches the measured-absence branch, which is the defect")
    reason = sm.no_session_reason(narrowed)
    assert "LOCAL host only" in reason and "laptop" in reason
    assert "NOT a measured absence" in reason
    assert _MEASURED_ABSENCE not in reason


def test_an_UNRECOGNISED_fuzzyclaw_status_cannot_become_a_measured_absence():
    """🔴 KILLS: dropping the `st != "ok"` gate before the measured absence.

    The measured absence used to be a FALLTHROUGH: every status the branches
    above do not name became a measured negative by default, so a status added
    later would silently start asserting one. Positive control below: the SAME
    report with `ok` still reaches the measured absence, so this is a gate, not
    a blanket refusal.
    """
    report = base_gather(fuzzyclaw_texts=[])
    report["fuzzyclaw"]["status"] = "partial"        # a status invented later
    reason = sm.no_session_reason(sm.filter_report(report, "misc", "5"))
    assert "partial" in reason
    assert "NOT a measured absence" in reason
    assert _MEASURED_ABSENCE not in reason

    report["fuzzyclaw"]["status"] = "ok"
    assert _MEASURED_ABSENCE in sm.no_session_reason(
        sm.filter_report(report, "misc", "5"))


def test_no_session_reason_for_a_REMOTE_window_names_the_local_only_limit():
    """fuzzyclaw is LOCAL-only, so a laptop row could never have had a session
    id. The old string called that a measured absence too."""
    report = base_gather()
    narrowed = sm.filter_report(report, "naida-dev", "1")
    rows = [r for h in narrowed["hosts"].values() for r in h["windows"]]
    assert [r["host"] for r in rows] == ["laptop"], "fixture sanity"
    reason = sm.no_session_reason(narrowed)
    assert "LOCAL host only" in reason and "laptop" in reason
    assert "NOT a measured absence" in reason
    assert _MEASURED_ABSENCE not in reason


def test_no_session_reason_when_no_window_matched_the_target():
    report = base_gather()
    narrowed = sm.filter_report(report, "no-such-session", "99")
    reason = sm.no_session_reason(narrowed)
    assert "matched the requested target" in reason
    assert "NOT a measured absence" in reason
    assert _MEASURED_ABSENCE not in reason


def test_no_session_reason_for_a_slot_the_guard_DROPPED():
    """A contested slot resolves to no task at all. "No file joined" is true but
    misleading — files DID claim it and were dropped, which is a different and
    actionable fact."""
    report = _contested_report()
    assert report["fuzzyclaw"]["slot_conflicts"], "fixture sanity"
    narrowed = sm.filter_report(report, "scratch7", "3")
    reason = sm.no_session_reason(narrowed)
    assert "claimed by 2 task files" in reason
    assert "ALL were dropped" in reason
    assert "NOT a measured absence" in reason


def test_the_unmeasured_reasons_are_PAIRWISE_DISTINCT():
    """🔴 A reason set is only useful if the branches actually differ. One
    return statement for all of them is exactly the defect."""
    reasons = {
        "measured": sm.no_session_reason(
            sm.filter_report(base_gather(fuzzyclaw_texts=[]), "misc", "5")),
        "skipped": sm.no_session_reason(
            sm.filter_report(base_gather(use_fuzzyclaw=False), "scratch7", "3")),
        "remote": sm.no_session_reason(
            sm.filter_report(base_gather(), "naida-dev", "1")),
        "unmeasured": sm.no_session_reason(sm.filter_report(
            base_gather(runner=make_runner(local_windows_rc=1,
                                           local_windows_err="died")),
            "scratch7", "3")),
        "no_rows": sm.no_session_reason(
            sm.filter_report(base_gather(), "nope", "9")),
        "contested": sm.no_session_reason(
            sm.filter_report(_contested_report(), "scratch7", "3")),
    }
    assert len(set(reasons.values())) == len(reasons), (
        "two cases share a sentence: " + repr(reasons))


def test_detail_history_EMITS_the_selected_reason_not_a_constant():
    """🔴 THE SEAM. `no_session_reason` being correct proves nothing about
    `detail_history` — the old constant lived at the call site, not in a helper,
    so a correct helper nobody called would leave the defect intact.

    KILLS: `detail_history` re-inlining any fixed string for the no-sid case.
    The autouse `_no_real_socket` guard also proves no client was built.
    """
    report = base_gather(use_fuzzyclaw=False)
    narrowed = sm.filter_report(report, "scratch7", "3")
    hist = sm.detail_history(narrowed, use_ch=True)
    assert hist["status"] == "skipped"
    assert hist["reason"] == sm.no_session_reason(narrowed)
    assert "--no-fuzzyclaw" in hist["reason"]
    assert _MEASURED_ABSENCE not in hist["reason"]


def test_detail_history_no_ch_still_wins_over_the_reason_selection():
    """--no-ch is the operator's own explicit choice and must not be masked by
    a fuzzyclaw story about a query that was never going to run."""
    narrowed = sm.filter_report(base_gather(use_fuzzyclaw=False),
                                "scratch7", "3")
    assert sm.detail_history(narrowed, use_ch=False)["reason"] == "--no-ch"


def test_a_detail_json_never_claims_a_measured_absence_it_did_not_measure(
        monkeypatch, capsys, absent_blocked_cache):
    """🔴 END-TO-END, the exact contradiction the auditor read in ONE payload:
    `LIVE COUNT UNMEASURED` in the report and a measured absence beside it."""
    monkeypatch.setattr(sm, "local_host_label", lambda *a, **k: "workbench")
    monkeypatch.setattr(sm, "_default_runner",
                        make_runner(local_windows_rc=1,
                                    local_windows_err="list-windows died"))
    monkeypatch.setattr(sm, "read_fuzzyclaw_texts",
                        lambda *a, **k: [json.dumps(TASK_LIVE)])
    monkeypatch.setattr(sm, "load_scratch_slots", lambda *a, **k: {})
    # 🔴 `--fuzzyclaw` is now REQUIRED to reach this code path at all: the join
    # is opt-in, so without it the status would be `skipped` and this test
    # would pass vacuously against a source that was never read.
    sm.main(["detail", "scratch7:3", "--host", "workbench", "--json",
             "--no-ch", "--fuzzyclaw"])
    blob = json.loads(capsys.readouterr().out)
    assert blob["fuzzyclaw"]["status"] == "unmeasured"
    assert blob["fuzzyclaw"]["files_live"] is None
    # --no-ch is the honest first answer here; the point is that the payload
    # never asserts a MEASURED absence over the unmeasured set above it.
    assert _MEASURED_ABSENCE not in json.dumps(blob), (
        "the report says the live count was never measured, so nothing in the "
        "same payload may assert a measured absence over it")
    assert blob["session_history"]["reason"] == "--no-ch"


# --------------------------------------------------------------------------- #
# §4.3 — 🟡 C: the first fix round's OWN new output paths, which had no coverage
#
# Four mutants survived the auditor's independently-built sweep. Each test below
# names the one it kills, and asserts STATE rather than a word the code types.
# --------------------------------------------------------------------------- #
def test_the_unmeasured_reason_quotes_the_LIST_WINDOWS_stderr_verbatim():
    """🔴 KILLS: `local_windows_error = panes_res["error"]` (gather:867).

    The pre-existing test asserted only `"list-windows" in fz["error"]` — a
    prefix THE CODE TYPES ITSELF, so it stayed green while the operator-facing
    reason degraded to "...unknown error". Same "fact read off the wrong
    subprocess" class as the original F3, one level deeper.

    So assert the value came from the RIGHT call: the two subprocesses get
    PAIRWISE-DISTINCT stderr strings and only one of them may appear.
    """
    report = base_gather(runner=make_runner(
        local_windows_rc=1,
        local_windows_err="WINDOWS-CALL-FAILED-9f3a: server lost"))
    err = report["fuzzyclaw"]["error"]
    assert "WINDOWS-CALL-FAILED-9f3a" in err, (
        "the reason must quote the list-windows stderr, not a typed prefix")
    assert "unknown error" not in err, (
        "the mutant reads panes_res['error'] — None on a healthy panes call — "
        "and degrades to this placeholder")


def test_the_unmeasured_reason_does_not_quote_the_LIST_PANES_stderr():
    """The mirror image, so neither direction is hardcoded: BOTH calls fail,
    with distinct messages, and only the list-windows one may be quoted."""
    report = base_gather(runner=make_runner(
        local_rc=1, local_err="PANES-CALL-FAILED-11bd: panes exploded",
        local_windows_rc=1,
        local_windows_err="WINDOWS-CALL-FAILED-9f3a: server lost"))
    err = report["fuzzyclaw"]["error"]
    assert "WINDOWS-CALL-FAILED-9f3a" in err
    assert "PANES-CALL-FAILED-11bd" not in err, (
        "reading the fact off the wrong subprocess is the defect")
    # and the host's own two facts stay attached to their own calls
    wb = report["hosts"]["workbench"]
    assert "PANES-CALL-FAILED-11bd" in wb["error"]
    assert "WINDOWS-CALL-FAILED-9f3a" in wb["windows_error"]


def test_the_PER_HOST_window_list_unmeasured_banner_reaches_the_table():
    """🔴 KILLS: deleting the `elif h.get("windows_measured") is False:` arm
    (render_table:961-967).

    Only the FOOTER line ("WINDOW LIST UNMEASURED ON: workbench") was asserted.
    The per-host banner the first fix round added was unpinned, so removing it
    entirely left the suite green.

    🔴 Assert on THE BANNER LINE, not on the whole table. A first version of
    this test checked `"WINDOWS-STDERR-4c2e" in text` and a mutant that made the
    banner quote the PANES error survived it — the string was still in the
    output, printed by the unrelated FUZZYCLAW section. That is a spelled guard:
    it passed while the hazard existed in a different shape.
    """
    report = base_gather(runner=make_runner(
        local_rc=0, local_err="",
        local_windows_rc=1, local_windows_err="WINDOWS-STDERR-4c2e"))
    text = sm.render_table(report)
    lines = text.splitlines()
    banner = next((ln for ln in lines
                   if "WORKBENCH: WINDOW LIST UNMEASURED" in ln), None)
    assert banner is not None, "the per-host banner must render at all"
    assert "WINDOWS-STDERR-4c2e" in banner, (
        "the BANNER itself must quote the list-windows stderr — checking the "
        "whole table lets the fuzzyclaw section satisfy this for it")
    assert "unknown error" not in banner, (
        "reading h['error'] (None here, panes succeeded) degrades to this")
    assert "panes were read; window ids were NOT" in text
    assert "NOT zero live windows" in text
    # the banner is ABOVE the window table, not the footer line at the bottom
    assert (text.index("▸ WORKBENCH: WINDOW LIST UNMEASURED")
            < text.index("▸ TMUX WINDOWS"))
    # and the host is NOT mislabelled unreachable — list-panes answered
    assert "WORKBENCH: UNREACHABLE" not in text


def test_the_banner_quotes_the_WINDOWS_error_not_the_PANES_error():
    """The pairwise-distinct discriminator for the banner's provenance.

    Both calls fail with DIFFERENT stderr, so the banner cannot satisfy this by
    accident: it must carry the list-windows one and not the list-panes one.
    """
    report = base_gather(runner=make_runner(
        local_rc=1, local_err="PANES-STDERR-77aa",
        local_windows_rc=1, local_windows_err="WINDOWS-STDERR-4c2e"))
    # list-panes failed, so this host renders UNREACHABLE rather than the
    # partial banner — the two errors must still not be swapped.
    lines = sm.render_table(report).splitlines()
    unreachable = next(ln for ln in lines if "WORKBENCH: UNREACHABLE" in ln)
    assert "PANES-STDERR-77aa" in unreachable
    assert "WINDOWS-STDERR-4c2e" not in unreachable
    # and the fuzzyclaw reason carries the WINDOWS one, not the panes one
    fz_err = report["fuzzyclaw"]["error"]
    assert "WINDOWS-STDERR-4c2e" in fz_err and "PANES-STDERR-77aa" not in fz_err


def test_the_per_host_banner_is_ABSENT_when_the_window_list_WAS_measured():
    """POSITIVE CONTROL: a banner that always renders pins nothing."""
    text = sm.render_table(base_gather())
    assert "WINDOW LIST UNMEASURED" not in text
    assert "panes were read; window ids were NOT" not in text


def test_the_unmeasured_FUZZYCLAW_BANNER_quotes_the_REAL_reason():
    """🔴 KILLS: `fz.get('error') or 'unknown reason'` -> the literal
    `'unknown reason'` in render_table's unmeasured branch.

    The underlying `fuzzyclaw.error` and the per-host WINDOW LIST banner were
    both pinned, but the line an operator actually reads when `list-windows`
    dies was asserted only to EXIST. Replacing it with the fallback left the
    suite green while the screen stopped naming the cause — and "the
    intersection never ran" without the cause is half a fact.

    Both halves: the real reason must render, and the fallback must still be
    reachable when there genuinely is no reason recorded.
    """
    report = base_gather(runner=make_runner(
        local_windows_rc=1, local_windows_err="WINDOWS-STDERR-5d17"))
    assert report["fuzzyclaw"]["status"] == "unmeasured", "fixture sanity"
    text = sm.render_table(report)
    assert "LIVE COUNT UNMEASURED" in text
    assert "WINDOWS-STDERR-5d17" in text, (
        "the operator's line must quote the cause, not a placeholder")
    assert "unknown reason" not in text

    # POSITIVE CONTROL for the fallback itself: it is reachable, so the
    # assertion above is about the CHOICE, not about a dead branch.
    report["fuzzyclaw"]["error"] = None
    assert "unknown reason" in sm.render_table(report)


def test_slot_conflicts_reach_the_REPORT_through_gather():
    """🔴 KILLS: `report["fuzzyclaw"]["slot_conflicts"] = []` (gather:884).

    Before this, `slot_conflicts` was pinned only at the pure-function level and
    was COMPLETELY unpinned across the gather -> report -> render seam — the
    "verified in isolation, broken at the seam" shape.
    """
    report = _contested_report()
    assert report["fuzzyclaw"]["slot_conflicts"] == [{
        "session": "scratch7", "window_index": "3",
        "claimants": 2, "window_ids": ["@41", "@52"],
    }]
    # both files SURVIVED the liveness guard — the drop happens at the index
    assert report["fuzzyclaw"]["files_live"] == 2
    assert report["fuzzyclaw"]["status"] == "ok"


def test_a_contested_slot_carries_NO_session_id_onto_the_rendered_row():
    """The BEHAVIOURAL half. A structural check on the conflicts list would
    type-check past a row that still carried a stranger's session id — which is
    the unrecoverable failure the drop exists to prevent."""
    report = _contested_report()
    rows = report["hosts"]["workbench"]["windows"]
    row = next(r for r in rows if (r["session"], r["window_index"])
               == ("scratch7", "3"))
    assert row["fuzzyclaw"] is None, "attaching an arbitrary claimant is worse"
    assert row["claude_session_id"] is None
    assert row["age_secs"] is None, "age is derived from the dropped task file"
    # neither claimant's session id leaked onto any row
    ids = {r["claude_session_id"] for r in rows}
    assert TASK_LIVE["claude_session"] not in ids
    assert _TASK_LIVE_TWIN["claude_session"] not in ids


def test_the_SLOT_CONFLICT_line_reaches_the_TABLE():
    """🔴 KILLS: `for c in ()` instead of `slot_conflicts` (render_table:1038).

    Nothing asserted the warning ever reached the operator's screen.
    """
    text = sm.render_table(_contested_report())
    line = next((ln for ln in text.splitlines() if "SLOT CONFLICT" in ln), None)
    assert line is not None, "the conflict must be VISIBLE, not only in JSON"
    assert "scratch7:3" in line
    assert "2 task files" in line
    assert "@41" in line and "@52" in line
    assert "ALL DROPPED" in line


def test_the_conflict_line_distinguishes_DUPLICATE_FILES_from_CONTENTION():
    """`claimants` counts FILES, `window_ids` is a deduplicated set, so two
    files describing ONE window rendered as "claimed by 2 task files (@41)" —
    which reads like a contention the relationship guard can no longer produce.

    KILLS: dropping the distinct-id count from the rendered line.
    """
    report = base_gather()
    report["fuzzyclaw"]["slot_conflicts"] = [
        {"session": "scratch7", "window_index": "3",
         "claimants": 2, "window_ids": ["@41"]}]
    line = next(ln for ln in sm.render_table(report).splitlines()
                if "SLOT CONFLICT" in ln)
    assert "2 task files" in line
    assert "1 distinct window id" in line, (
        "2 files naming 1 window is DUPLICATE FILES, not two windows contending")


def test_no_slot_conflict_line_when_there_are_none():
    """POSITIVE CONTROL: the loop must be able to render nothing."""
    assert "SLOT CONFLICT" not in sm.render_table(base_gather())


# 🔴 The REACHABLE conflict shape: a SECOND FILE for the SAME window. Identical
# `window_id` and slot to TASK_LIVE, everything else distinct. Nothing on disk
# forbids it — the files are `<index>.json`, not `<window_id>.json`, and
# CLAUDE.md marks the directory UNTRUSTED — so the docs may not call
# `slot_conflicts` unreachable.
_TASK_LIVE_DUPLICATE_FILE = dict(
    TASK_LIVE,
    task="task-duplicate-file-text",
    claude_session="44444444-3333-4222-8111-000000000000",
    summary="summary-duplicate-file",
    transcript_path="/home/zach/.claude/projects/proj-dup/dup.jsonl",
)


def test_TWO_FILES_NAMING_ONE_WINDOW_reach_slot_conflicts_through_gather():
    """🔴 The conflict the relationship guard does NOT remove, driven end to end.

    An earlier round documented `slot_conflicts` as "UNREACHABLE in production
    today", reasoning only about two DISTINCT live window ids in one slot. Two
    task files carrying ONE `window_id` is a different shape and is reachable:
    both resolve to the single slot @41 really holds, both survive
    `filter_live_tasks`, and they collide at the index.

    So this is the evidence behind the corrected claim in the module docstring,
    `index_tasks_by_window`'s docstring and SKILL.md. It also pins the
    behavioural half: neither duplicate's `claude_session` may reach a row.
    """
    report = base_gather(fuzzyclaw_texts=[json.dumps(TASK_LIVE),
                                          json.dumps(_TASK_LIVE_DUPLICATE_FILE)])
    fz = report["fuzzyclaw"]
    assert fz["status"] == "ok"
    assert (fz["files_live"], fz["files_stale"], fz["files_mismatched"]) \
        == (2, 0, 0), "both files must SURVIVE the guard; the drop is at the index"
    assert fz["slot_conflicts"] == [{
        "session": "scratch7", "window_index": "3",
        "claimants": 2, "window_ids": ["@41"],
    }], "2 FILES, 1 window id — the duplicate-files shape, not contention"

    row = next(r for r in report["hosts"]["workbench"]["windows"]
               if (r["session"], r["window_index"]) == ("scratch7", "3"))
    assert row["fuzzyclaw"] is None and row["claude_session_id"] is None
    ids = {r["claude_session_id"]
           for r in report["hosts"]["workbench"]["windows"]}
    assert TASK_LIVE["claude_session"] not in ids
    assert _TASK_LIVE_DUPLICATE_FILE["claude_session"] not in ids

    line = next(ln for ln in sm.render_table(report).splitlines()
                if "SLOT CONFLICT" in ln)
    assert "scratch7:3" in line and "2 task files" in line
    assert "1 distinct window id" in line and "ALL DROPPED" in line


# --------------------------------------------------------------------------- #
# §4.4 — 🟡 D: replacing a VACUOUS test
#
# `assert "LIMIT 5" in sm.sql_session_history("s", limit=5)` stayed GREEN with
# `int()` deleted, because f"{5}" == f"{int(5)}". It pinned the spelling, not
# the coercion its name claimed.
# --------------------------------------------------------------------------- #
def test_session_history_sql_limit_REJECTS_a_non_int_rather_than_interpolating():
    """🔴 STRUCTURAL. KILLS: deleting `int()` from sql_session_history:222.

    Latent today — `limit` is only ever `DEFAULT_HISTORY_LIMIT` and no CLI flag
    reaches it — so this pins the guard, not a live injection path. That is
    exactly why it must not be a spelling check: a bad instrument counted as
    coverage is worse than no coverage.
    """
    hostile = "10 UNION SELECT password FROM secrets --"
    with pytest.raises(ValueError):
        sm.sql_session_history("s", limit=hostile)
    with pytest.raises(TypeError):
        sm.sql_session_history("s", limit=None)
    with pytest.raises(TypeError):
        sm.sql_session_history("s", limit=["10"])
    # a float is COERCED, not interpolated verbatim
    assert sm.sql_session_history("s", limit=5.9).endswith("LIMIT 5")
    # and the ordinary path still produces the literal
    assert sm.sql_session_history("s", limit=5).endswith("LIMIT 5")


def test_session_history_sql_limit_never_reaches_the_string_uncoerced():
    """The POSITIVE half: prove the hostile text CANNOT appear in any output.

    A `pytest.raises` alone would also pass if the function raised for an
    unrelated reason, so also assert the thing that must never happen.
    """
    hostile = "10 UNION SELECT password FROM secrets --"
    try:
        sql = sm.sql_session_history("s", limit=hostile)
    except (ValueError, TypeError):
        sql = ""
    assert "UNION" not in sql and "secrets" not in sql


# =========================================================================== #
# §5 — THE AGENT/SHELL SPLIT, --claude-only, AND THE CAVEATS IN THE OUTPUT
#
# Measured blind-dogfooding 2026-08-11: 61 windows = 41 claude + 20 non-claude,
# and `summary.idle = 17` was 12 agent windows + 5 bare shells rendered
# identically as `● idle`. The row data was already correct — every row carries
# `claude` — so everything below is about the ROLL-UP and the RENDERER, and
# nothing here touches detection.
#
# The fixture is built so a claude<->shell SWAP is visible (2 vs 1, never 1 vs
# 1) and so claude-ness is DECORRELATED from every other field a wrong
# predicate might key on: the claude panes' titles never contain "claude", one
# SHELL pane's title does, and claude rows land in two different status
# buckets.
# =========================================================================== #
BRAILLE2 = "⠹"    # a second spinner, distinct from BRAILLE
SPARKLE2 = "✻"    # a second sparkle, distinct from SPARKLE

IDLE_MIX_PANES = "\n".join([
    # 1. idle CLAUDE — the row the headline question is actually about
    f"%31|3001|hollow|2|win-echo|/home/zach/workspace/repo-echo|claude"
    f"|{SPARKLE} Awaiting review",
    # 2. a SECOND idle claude, so idle is 2 claude + 1 shell: a swapped split
    #    reads 1+2 and is caught. 1+1 would be swap-invisible.
    f"%32|3002|quarry|9|win-foxtrot|/home/zach/workspace/repo-foxtrot|claude"
    f"|{SPARKLE2} Rebase finished",
    # 3. idle BARE SHELL — and its TITLE contains "claude" while its command
    #    does not, so a predicate reading the title instead of the command
    #    keeps this row and is caught.
    "%33|3003|ridge|4|win-golf|/home/zach/tmp/golf|zsh|* tail -f claude.log",
    # 4. BUSY shell — proves the split is not hardcoded to the idle bucket.
    f"%34|3004|thicket|7|win-hotel|/home/zach/tmp/hotel|bash"
    f"|{BRAILLE2} nix build",
    # 5. UNKNOWN claude (no glyph, no age) — the bucket that measured 15/15
    #    non-claude on the live host. A claude row lands here as soon as its
    #    title carries no glyph, so that measurement was a snapshot.
    "%35|3005|hedge|6|win-india|/home/zach/workspace/repo-india|claude"
    "|plain india title",
])
IDLE_MIX_WINDOWS = "@71|2|hollow\n@72|9|quarry\n@73|4|ridge\n@74|7|thicket\n" \
                   "@75|6|hedge\n"

SHELLS_ONLY_PANES = (
    "%41|4001|copse|1|win-juliet|/home/zach/tmp/juliet|zsh|* juliet prompt")
SHELLS_ONLY_WINDOWS = "@81|1|copse\n"

# 🔴 The REMOTE host needs a shell of its own, or "filter the local host only"
# is a mutation no fixture can see: the stock laptop fixture is 100% claude.
LAPTOP_MIX_PANES = "\n".join([
    LAPTOP_PANES,
    "%22|2002|thistle|4|win-kilo|/home/zach/tmp/kilo|zsh|* kilo prompt",
])
LAPTOP_MIX_WINDOWS = "@7|1|naida-dev\n@8|4|thistle\n"


def mix_gather(**kw):
    """A LOCAL-only scan of IDLE_MIX_PANES, with no fuzzyclaw texts so every
    row's age is None and the status comes from the glyph alone."""
    defaults = dict(hosts=("workbench",), local_host="workbench",
                    runner=make_runner(local_panes=IDLE_MIX_PANES,
                                       local_windows=IDLE_MIX_WINDOWS),
                    fuzzyclaw_texts=[], slots={})
    defaults.update(kw)
    return base_gather(**defaults)


def test_the_mix_fixture_is_what_it_claims_and_its_fields_are_distinct():
    """INSTRUMENT CHECK before any verdict is read off this fixture.

    Every assertion below depends on this fixture really containing an idle
    agent AND an idle bare shell. A fixture that quietly produced two claude
    rows would make the split tests pass for the wrong reason.
    """
    rows = mix_gather()["hosts"]["workbench"]["windows"]
    got = {(r["session"], r["claude"], r["status"]) for r in rows}
    assert got == {
        ("hollow", True, "idle"),      # agent, waiting
        ("quarry", True, "idle"),      # agent, waiting
        ("ridge", False, "idle"),      # BARE SHELL at a prompt
        ("thicket", False, "busy"),
        ("hedge", True, "unknown"),
    }
    # claude-ness is decorrelated from the title text...
    ridge = next(r for r in rows if r["session"] == "ridge")
    assert "claude" in ridge["task"] and ridge["claude"] is False
    assert all("claude" not in r["task"]
               for r in rows if r["claude"] is True)
    # ...and every pane field value is pairwise distinct across the fixture.
    for field in ("pane_id", "session", "window_index", "window_name", "path",
                  "task"):
        vals = [r[field] for r in rows]
        assert len(set(vals)) == len(vals), f"{field} repeats: {vals}"


# --------------------------------------------------------------------------- #
# §5.1 — the split itself
# --------------------------------------------------------------------------- #
def test_an_idle_AGENT_and_an_idle_SHELL_are_never_merged_into_one_count():
    """🔴 THE HEADLINE DEFECT. KILLS: collapsing the split back to one bucket
    (`counts[status] += 1`), and swapping the claude/shell halves.

    `idle` here is 2 agents + 1 shell. There is no key anywhere in the summary
    whose value is the mixed 3 without the word `total` on it.
    """
    s = mix_gather()["summary"]
    assert s["status"]["idle"] == {"claude": 2, "shell": 1, "total": 3}
    assert s["status"]["idle"]["claude"] == 2, (
        "the agent count must NOT absorb the bare shell")
    # the mixed number exists only where it is spelled `total`
    assert s["status"]["idle"]["total"] == 3
    # 🔴 STRUCTURAL: every integer at the TOP level of the summary is either a
    # whole-set total or already kind-qualified by its own name. A new mixed
    # per-status integer cannot appear here without failing this.
    int_keys = {k for k, v in s.items()
                if isinstance(v, int) and not isinstance(v, bool)}
    assert int_keys == {"total_sessions", "claude", "shell",
                        "fuzzyclaw_live",   # a FILE count, not a window count
                        # Whole-set totals in the same class as
                        # `total_sessions`, NOT per-status buckets: they count
                        # rows carrying a fact, across every status. The split
                        # this guard protects is claude-vs-shell inside a
                        # STATUS, and neither of these is one — the breakdown
                        # that matters for an age is by WRITER, and it lives in
                        # `age_sources`, which is a dict and so cannot be
                        # misread as a bucket count.
                        "rows_with_age", "rows_with_session_id"}


def test_the_flat_mixed_status_INTEGERS_are_gone_not_kept_alongside():
    """🔴 KILLS: re-adding `"idle": counts["idle"]` "for compatibility".

    Keeping the flat ints would leave every existing reader on the mixed
    number — the defect — while the fix sat in keys they never learned. The
    break is deliberate and must stay loud: a KeyError, not a wrong integer.
    """
    s = mix_gather()["summary"]
    for bucket in sm.STATUS_BUCKETS:
        assert bucket not in s, (
            f"summary[{bucket!r}] is back, and it is a MIXED count")
        assert isinstance(s["status"][bucket], dict)
    with pytest.raises(KeyError):
        s["idle"]


def test_EVERY_bucket_splits_not_only_idle():
    """🔴 KILLS: splitting `idle` alone and leaving the other three mixed.

    `unknown` measured 15/15 non-claude on the live host, which is a SNAPSHOT:
    this fixture puts a claude row in `unknown` and a shell row in `busy`.
    """
    s = mix_gather()["summary"]
    assert s["status"]["busy"] == {"claude": 0, "shell": 1, "total": 1}
    assert s["status"]["unknown"] == {"claude": 1, "shell": 0, "total": 1}
    assert s["status"]["stale"] == {"claude": 0, "shell": 0, "total": 0}
    assert set(s["status"]) == set(sm.STATUS_BUCKETS)
    assert s["claude"] == 3 and s["shell"] == 2 and s["total_sessions"] == 5


def test_the_bucket_tuple_and_the_classifier_cannot_drift_apart():
    """A bucket `classify_status` can return but `STATUS_BUCKETS` omits would
    be counted into a bucket the renderer never prints — invisible."""
    produced = {sm.classify_status(b, age, 3600)
                for b in (True, False, None) for age in (None, 10, 99999)}
    assert produced == set(sm.STATUS_BUCKETS)


def test_the_table_distinguishes_an_idle_AGENT_from_an_idle_SHELL():
    """🔴 KILLS: dropping the CLASS column, or deriving it from the wrong field.

    Both rows render `● idle`; the row must still say which one it is, and the
    footer must never print a bare `idle=3`.
    """
    text = sm.render_table(mix_gather())
    lines = text.splitlines()
    agent = next(ln for ln in lines if "hollow" in ln)
    shell = next(ln for ln in lines if "ridge" in ln)
    assert "● idle" in agent and "● idle" in shell, "both are idle rows"
    assert "claude" in agent.split("● idle")[0]
    assert "shell" in shell.split("● idle")[0]
    # 🔴 `CLASS`, not `KIND`: the row now carries a real `kind` field with a
    # DIFFERENT vocabulary (tmux/cluster), and one word for two axes on the
    # two surfaces a reader compares is a misread waiting to happen.
    assert "CLASS" in text
    assert "KIND" not in text, "the old header collides with the kind field"
    # the footer carries both halves, labelled, and never the mixed integer
    assert "by status (claude+shell):" in text
    assert "idle=2+1" in text
    assert "idle=3" not in text
    assert "claude=3" in text and "shell=2" in text


def test_a_table_row_KIND_is_read_off_the_claude_boolean_not_the_command():
    """🔴 STRUCTURAL, not spelled. KILLS: `"claude" in row["command"]` or
    `"claude" in row["task"]` in the renderer.

    The shell row whose TITLE says "claude" must still render `shell`.
    """
    text = sm.render_table(mix_gather())
    shell = next(ln for ln in text.splitlines() if "ridge" in ln)
    assert "claude.log" in shell, "the title text is still shown"
    kind_cell = shell.split("* tail -f claude.log")[-1]
    assert " shell " in kind_cell and " claude " not in kind_cell


# --------------------------------------------------------------------------- #
# §5.2 — --claude-only
# --------------------------------------------------------------------------- #
def test_claude_only_drops_the_shells_on_EVERY_host_and_keeps_the_agents():
    """🔴 KILLS: filtering only the LOCAL host, and inverting the predicate.

    Both hosts carry a shell here. The stock laptop fixture is 100% claude, so
    against it "filter the local host only" is a mutant no assertion can see.
    """
    runner = make_runner(remote_panes=LAPTOP_MIX_PANES,
                         remote_windows=LAPTOP_MIX_WINDOWS)
    report = base_gather(claude_only=True, runner=runner)
    wb = report["hosts"]["workbench"]["windows"]
    lt = report["hosts"]["laptop"]["windows"]
    assert [r["session"] for r in wb] == ["scratch7"], "misc:5 is a zsh window"
    assert [r["session"] for r in lt] == ["naida-dev"], "thistle:4 is a shell"
    assert all(r["claude"] is True for r in wb + lt)
    assert report["summary"]["excluded_shells"] == 2, "one PER HOST"
    # ...and the unfiltered scan still carries both shells, so the drop is
    # caused by the flag and by nothing else (positive control on the filter).
    unfiltered = base_gather(runner=make_runner(
        remote_panes=LAPTOP_MIX_PANES, remote_windows=LAPTOP_MIX_WINDOWS))
    assert len(unfiltered["hosts"]["workbench"]["windows"]) == 2
    assert len(unfiltered["hosts"]["laptop"]["windows"]) == 2


def test_claude_only_filters_on_the_claude_BOOLEAN_not_a_lookalike_field():
    """🔴 KILLS: filtering on the title, the command string, or the status.

    The shell whose title contains "claude" must be dropped; the claude window
    whose title does NOT contain it must be kept; and the claude row sitting in
    `unknown` must survive a status-shaped predicate.
    """
    rows = mix_gather(claude_only=True)["hosts"]["workbench"]["windows"]
    kept = {r["session"] for r in rows}
    assert kept == {"hollow", "quarry", "hedge"}
    assert "ridge" not in kept, "a title containing 'claude' is not an agent"
    assert "hedge" in kept, "an `unknown` claude row is still an agent"


def test_claude_only_summary_counts_the_FILTERED_set_and_says_what_it_dropped():
    """🔴 KILLS: summarizing the UNFILTERED set (the silent trap), and
    reporting `excluded_shells` as 0 when the filter never ran.

    Counting the unfiltered set would publish `total_sessions: 5` beside 3
    printed rows. The dropped count is what keeps the filtered zero from
    reading as "the host had nothing".
    """
    s = mix_gather(claude_only=True)["summary"]
    assert s["total_sessions"] == 3, "the summary describes what is rendered"
    assert s["claude"] == 3 and s["shell"] == 0
    assert s["status"]["idle"] == {"claude": 2, "shell": 0, "total": 2}
    assert s["status"]["busy"]["total"] == 0, "the busy SHELL is gone"
    assert s["claude_only"] is True
    assert s["excluded_shells"] == 2

    off = mix_gather()["summary"]
    assert off["claude_only"] is False
    assert off["excluded_shells"] is None, (
        "nothing was excluded because nothing was ever counted — not 0")


def test_claude_only_composes_with_host_and_counts_only_the_scanned_host():
    report = base_gather(claude_only=True, hosts=("laptop",),
                         local_host="workbench")
    assert [r["host"] for r in report["hosts"]["laptop"]["windows"]] \
        == ["laptop"]
    assert report["summary"]["excluded_shells"] == 0, (
        "the laptop fixture has no shell window — a MEASURED zero")
    assert report["summary"]["total_sessions"] == 1
    # the workbench's zsh window was never scanned, so it is not in the count
    assert "workbench" not in report["hosts"]


def test_claude_only_over_a_shell_only_host_is_a_MEASURED_zero_not_a_success():
    """🔴 The silent-zero direction, pinned at the exit code.

    Zero AGENT windows is a real zero and must exit EXIT_EMPTY. Had the summary
    counted the unfiltered set, this same run would exit 0 — "ran, found
    windows" — over an empty table.
    """
    report = mix_gather(
        claude_only=True,
        runner=make_runner(local_panes=SHELLS_ONLY_PANES,
                           local_windows=SHELLS_ONLY_WINDOWS))
    assert report["summary"]["total_sessions"] == 0
    assert sm.exit_code_for(report) == sm.EXIT_EMPTY == 3
    assert sm.exit_code_for(report) != sm.EXIT_OK
    assert report["summary"]["excluded_shells"] == 1, (
        "and the zero says the host was NOT empty — 1 shell was dropped")
    # unfiltered, the very same scan is a non-empty EXIT_OK
    unfiltered = mix_gather(
        runner=make_runner(local_panes=SHELLS_ONLY_PANES,
                           local_windows=SHELLS_ONLY_WINDOWS))
    assert sm.exit_code_for(unfiltered) == sm.EXIT_OK


def test_claude_only_is_STATED_in_the_table_beside_the_counts_it_changed():
    text = sm.render_table(mix_gather(claude_only=True))
    assert "FILTER --claude-only" in text
    assert "2 shell window(s) excluded" in text
    assert "FILTER --claude-only" not in sm.render_table(mix_gather())
    # ...and on a tmux-only scan NO kind vanished, so the second FILTER line —
    # the one that names a removed kind — must not appear. Printing it with an
    # empty slot would assert a removal that did not happen.
    assert "removed EVERY kind=" not in text


def test_cli_claude_only_flag_reaches_gather_and_defaults_off(monkeypatch):
    seen = {}

    def fake_gather(**kw):
        seen.update(kw)
        return base_gather()

    monkeypatch.setattr(sm, "gather", fake_gather)
    monkeypatch.setattr(sm, "local_host_label", lambda *a, **k: "workbench")
    sm.main(["scan", "--json", "--no-ch", "--claude-only"])
    assert seen["claude_only"] is True
    seen.clear()
    sm.main(["scan", "--json", "--no-ch"])
    assert seen["claude_only"] is False


def test_main_json_end_to_end_carries_the_split_and_the_filter(
        monkeypatch, capsys, absent_blocked_cache):
    """END-TO-END through main(), because every test above injects gather()."""
    monkeypatch.setattr(sm, "local_host_label", lambda *a, **k: "workbench")
    monkeypatch.setattr(sm, "_default_runner",
                        make_runner(local_panes=IDLE_MIX_PANES,
                                    local_windows=IDLE_MIX_WINDOWS))
    monkeypatch.setattr(sm, "read_fuzzyclaw_texts", lambda *a, **k: [])
    rc = sm.main(["scan", "--json", "--no-ch", "--host", "workbench",
                  "--claude-only"])
    blob = json.loads(capsys.readouterr().out)
    assert rc == sm.EXIT_OK
    assert blob["summary"]["status"]["idle"] == {"claude": 2, "shell": 0,
                                                 "total": 2}
    assert blob["summary"]["excluded_shells"] == 2
    assert blob["filters"] == {"claude_only": True, "excluded_shells": 2,
                               # the filter RAN and removed no whole kind —
                               # `[]`, not None, and not absent
                               "kinds_excluded_by_filter": [],
                               # 🔴 ...and the OTHER row filter did NOT run, so
                               # every one of its keys is null. `matched: 0`
                               # here would say "a --match ran and matched
                               # nothing" over a scan that returned two rows.
                               "match": None, "match_fields": None,
                               "matched": None, "excluded_by_match": None,
                               # `--claude-only` DID run, but this scan is not a
                               # `detail`, so the pre-filter index map was never
                               # sampled. `None`, not `{}`: "not sampled" and
                               # "sampled and empty" are different facts.
                               "prefilter_window_indices": None}


def test_detail_under_claude_only_explains_its_own_emptiness(monkeypatch):
    """A `detail` on a SHELL window under --claude-only finds nothing. The
    reason must be in the payload, not left to be inferred from an empty list.
    """
    report = mix_gather(claude_only=True)
    narrowed = sm.filter_report(report, "ridge", "4")
    rows = [r for h in narrowed["hosts"].values() for r in h["windows"]]
    assert rows == []
    assert narrowed["summary"]["claude_only"] is True
    assert narrowed["summary"]["excluded_shells"] == 2
    assert "NOT a measured absence" in sm.no_session_reason(narrowed)


def test_claude_only_says_it_does_nothing_for_tail_rather_than_ignoring_it(
        monkeypatch, capsys):
    """A silently ignored flag is how a caller concludes it was honoured."""
    monkeypatch.setattr(sm, "local_host_label", lambda *a, **k: "workbench")
    monkeypatch.setattr(sm, "_default_runner",
                        make_runner(local_capture="scrollback\n"))
    rc = sm.main(["tail", "scratch7:3", "--host", "workbench", "--claude-only"])
    cap = capsys.readouterr()
    assert rc == sm.EXIT_OK
    assert cap.out == "scrollback\n", "stdout stays the capture, verbatim"
    assert "--claude-only has no effect on `tail`" in cap.err
    # ...and it is NOT printed when the flag was not passed
    sm.main(["tail", "scratch7:3", "--host", "workbench"])
    assert "--claude-only" not in capsys.readouterr().err


# --------------------------------------------------------------------------- #
# §5.2b — --claude-only AGAINST THE `kind` AXIS
#
# Two defects, one flag. `r.get("claude")` was a correct SPELLING of "drop the
# shells" only while every row was a tmux pane, and the filter runs BEFORE both
# `summarize` and `measured_caveats`, so whatever it removed was then attributed
# to the BUILD rather than to the FILTER.
#
# 🔴 NO REAL SCAN CAN REACH EITHER ONE. Writer 3 is still gated (measured on the
# live board: `in_progress: 0`, `agent` non-null on 0 of 29 tasks), so every real
# scan is 100% tmux. A test that waited for a cluster row would be green today,
# green with the whole predicate deleted, and green on the day writer 3 lands
# wrong. So the rows are CONSTRUCTED and injected at the `fold_windows` seam,
# which is the last point before the filter runs.
# --------------------------------------------------------------------------- #
def gather_with_injected(monkeypatch, *rows, **kw):
    """A REAL `gather` whose local `fold_windows` also emits `rows`.

    🔴 THE SEAM MATTERS: appending to `report["hosts"][h]["windows"]` AFTER
    `gather` returns — which is what `with_cluster_rows` does — is too late.
    The `--claude-only` block, `summarize` and `measured_caveats` have all
    already run, so such a row exercises none of them. Wrapping `fold_windows`
    puts the constructed row in front of the filter, which is the code under
    test.
    """
    real = sm.fold_windows

    def folded(panes, host, **fkw):
        out = real(panes, host, **fkw)
        if host == "workbench":
            out.extend(copy.deepcopy(r) for r in rows)
        return out

    monkeypatch.setattr(sm, "fold_windows", folded)
    return mix_gather(**kw)


def test_the_injection_seam_reaches_the_filter_and_the_baseline_is_tmux_only(
        monkeypatch):
    """INSTRUMENT CHECK, before any verdict is read off this seam.

    Two things every test below depends on, neither of which is observable from
    the assertions themselves: the constructed row really arrives (so a seam
    that silently stopped applying would not leave the suite green), and the
    UNINJECTED baseline really is tmux-only (so the deltas are measured against
    a known starting point rather than an unknown one).
    """
    plain = mix_gather()
    assert {r["kind"] for r in plain["hosts"]["workbench"]["windows"]} == {"tmux"}
    assert plain["summary"]["kind"] == {"tmux": 5}

    rep = gather_with_injected(monkeypatch, cluster_row(status="busy"))
    kinds = [r["kind"] for r in rep["hosts"]["workbench"]["windows"]]
    assert kinds.count("cluster") == 1, (
        "the fold_windows seam did not deliver the constructed row: %r" % kinds)
    # ...and it went through the REAL summarize, i.e. it was present before the
    # roll-up ran rather than bolted on afterwards.
    assert rep["summary"]["kind"] == {"tmux": 5, "cluster": 1}


# --- the PREDICATE, unit --------------------------------------------------- #
def test_the_claude_only_predicate_is_the_CLASS_axis_not_the_claude_FLAG():
    """🔴 THE DEFECT. `row["claude"]` is `pane_current_command =~ /claude/` — a
    fact about a PANE. A cluster dispatch has no pane, so `claude` is None,
    which is FALSY, and `--claude-only` silently reclassified an AGENT as a
    shell and deleted it. That is the identical conflation `row_class` exists
    to prevent, one operation further along.

    Every case is pinned, including the two that must be UNCHANGED, so a fix
    that merely inverts something cannot pass.
    """
    claude_pane = {"kind": "tmux", "claude": True}
    shell_pane = {"kind": "tmux", "claude": False}
    # a pane whose command did not parse: genuinely "not a claude pane"
    unparsed_pane = {"kind": "tmux", "claude": None}
    dispatch = cluster_row()
    kindless = {"kind": None, "claude": None}

    assert sm.dropped_by_claude_only(shell_pane) is True
    assert sm.dropped_by_claude_only(unparsed_pane) is True
    assert sm.dropped_by_claude_only(claude_pane) is False
    # 🔴 THE REGRESSION. `not row.get("claude")` returns True here.
    assert sm.dropped_by_claude_only(dispatch) is False, (
        "a cluster dispatch is an AGENT, not a shell — the flag must keep it")
    assert dispatch["claude"] is None, (
        "fixture no longer exercises the falsy-claude path this pins")
    # ...and a cluster row that DOES assert claude:True is kept for the same
    # reason, so the answer comes from `kind` and not from the flag either way.
    assert sm.dropped_by_claude_only(cluster_row(claude=True)) is False
    # a row whose kind nobody set is a BUG; filtering it out deletes the
    # evidence, so it survives and stays visible in `unknown_kind`.
    assert sm.dropped_by_claude_only(kindless) is False
    assert sm.row_class(kindless) == "unknown_kind"


def test_the_predicate_is_DERIVED_from_row_class_not_a_second_copy_of_it():
    """🔴 ONE RULE, ONE PLACE — asserted BEHAVIOURALLY, because a structural
    check would type-check past a copy that merely happens to agree today.

    Move `row_class`'s answer to a value the predicate cannot coincidentally
    equal and watch the predicate follow. A second open-coded classifier stays
    put and fails here.
    """
    row = {"kind": "tmux", "claude": False}
    assert sm.dropped_by_claude_only(row) is True    # control: it drops today
    seen = []

    def fake_class(r):
        seen.append(r)
        return "claude"

    orig = sm.row_class
    sm.row_class = fake_class
    try:
        assert sm.dropped_by_claude_only(row) is False, (
            "the predicate ignored row_class — it holds a second copy of the "
            "claude/shell rule, and the two will disagree")
    finally:
        sm.row_class = orig
    assert seen == [row], "row_class was not consulted with the row itself"


def test_gather_applies_the_ONE_predicate_rather_than_open_coding_it(
        monkeypatch):
    """The seam between the flag and its definition, pinned. `gather` used to
    spell the rule inline (`if r.get("claude")`), which is how the definition
    and its only caller drifted apart in the first place."""
    calls = []
    real = sm.dropped_by_claude_only

    def spy(row):
        calls.append(row)
        return real(row)

    monkeypatch.setattr(sm, "dropped_by_claude_only", spy)
    rep = mix_gather(claude_only=True)
    assert len(calls) == 5, (
        "gather did not consult the predicate for every row: %d call(s)"
        % len(calls))
    assert rep["summary"]["excluded_shells"] == 2
    # POSITIVE CONTROL on the spy: with the predicate forced to keep
    # everything, the filter must keep everything. A spy nothing calls would
    # leave this identical to the line above.
    monkeypatch.setattr(sm, "dropped_by_claude_only", lambda r: False)
    kept_all = mix_gather(claude_only=True)
    assert kept_all["summary"]["total_sessions"] == 5
    assert kept_all["summary"]["excluded_shells"] == 0


# --- cluster rows SURVIVE the filter, end to end --------------------------- #
def test_claude_only_KEEPS_a_cluster_dispatch_and_still_drops_the_shells(
        monkeypatch):
    """🔴 THE HEADLINE REGRESSION, through the real `gather` filter.

    The mix fixture is 3 agents + 2 bare shells. Adding one cluster dispatch
    must leave the shells dropped and the dispatch present — under the old
    predicate the dispatch went with the shells, and `excluded_shells` counted
    it as one of them.
    """
    rep = gather_with_injected(monkeypatch, cluster_row(status="busy"),
                               claude_only=True)
    rows = rep["hosts"]["workbench"]["windows"]
    kinds = sorted(r["kind"] for r in rows)
    assert kinds == ["cluster", "tmux", "tmux", "tmux"], (
        "the cluster dispatch was filtered out with the shells")
    assert sorted(sm.row_class(r) for r in rows) == [
        "claude", "claude", "claude", "cluster"]
    # exactly the two BARE SHELLS went, and the count says two — not three
    assert rep["summary"]["excluded_shells"] == 2, (
        "the dispatch was counted among the excluded shells")
    assert rep["summary"]["total_sessions"] == 4
    assert rep["summary"]["kind"] == {"tmux": 3, "cluster": 1}
    assert rep["summary"]["status"]["busy"] == {
        "claude": 0, "shell": 0, "cluster": 1, "total": 1}


def test_a_cluster_only_host_under_claude_only_is_not_an_empty_scan(
        monkeypatch):
    """The direction that changes the EXIT CODE. A host whose only agent work
    is a cluster dispatch used to filter down to nothing and exit EXIT_EMPTY —
    "no agent windows" over a live dispatch."""
    rep = gather_with_injected(
        monkeypatch, cluster_row(status="busy"), claude_only=True,
        runner=make_runner(local_panes=SHELLS_ONLY_PANES,
                           local_windows=SHELLS_ONLY_WINDOWS))
    assert rep["summary"]["total_sessions"] == 1
    assert sm.exit_code_for(rep) == sm.EXIT_OK
    assert sm.exit_code_for(rep) != sm.EXIT_EMPTY
    assert rep["summary"]["excluded_shells"] == 1


# --- ATTRIBUTION: what the filter removed, said out loud ------------------- #
def test_a_kind_the_FILTER_removed_is_never_attributed_to_the_build(
        monkeypatch):
    """🔴 THE SECOND DEFECT, and it is REACHABLE TODAY with no cluster row
    anywhere: `--claude-only` over a host whose tmux rows are all bare shells
    removes the LAST `tmux` row, so `kinds_produced` measures `[]`.

    The scan produced tmux rows. The filter removed them. Every claim the
    caveat could make about `tmux` from `kinds_produced` alone would therefore
    be about the wrong actor, and the reader most likely to be misled is the one
    reading that line to find out what this build emits.
    """
    rep = gather_with_injected(
        monkeypatch, cluster_row(status="busy"), claude_only=True,
        runner=make_runner(local_panes=SHELLS_ONLY_PANES,
                           local_windows=SHELLS_ONLY_WINDOWS))
    kd = rep["caveats"]["kind_scope"]
    assert kd["kinds_produced"] == ["cluster"]
    assert kd["kinds_excluded_by_filter"] == ["tmux"]
    assert rep["summary"]["kinds_excluded_by_filter"] == ["tmux"]
    line = next(ln for ln in sm.render_caveats(rep) if "kind_scope" in ln)
    # the false sentence the old code rendered, banned BY NAME
    assert "tmux is ENUMERATED but NOT PRODUCED" not in line
    assert "a FILTER REMOVED every kind=tmux row this scan produced" in line


def test_the_filter_records_an_EMPTY_removal_set_distinctly_from_NO_FILTER():
    """🔴 `[]` AND `None` ARE DIFFERENT FACTS, one level down from the rule this
    file already applies to `excluded_shells`. `[]` says a filter ran and
    removed no whole kind; `None` says nothing was ever measured. Collapsing
    them lets a future reader treat an unfiltered scan as one that was checked
    and came back clean."""
    filtered = mix_gather(claude_only=True)
    assert filtered["filters"]["kinds_excluded_by_filter"] == []
    assert filtered["summary"]["kinds_excluded_by_filter"] == []
    assert filtered["caveats"]["kind_scope"]["kinds_excluded_by_filter"] == []

    off = mix_gather()
    assert off["filters"]["kinds_excluded_by_filter"] is None
    assert off["summary"]["kinds_excluded_by_filter"] is None
    # 🔴 and the CAVEAT omits the key entirely rather than carrying a null, so
    # the JSON reader sees the same not-measured-vs-measured-none distinction
    # here as in the two surfaces above. A null would spell "a filter ran"
    # inside a structure whose other two keys spell "none ran".
    #
    # 🔴 THE RENDERER DELIBERATELY DOES NOT DISTINGUISH THEM, and an earlier
    # version of this comment claimed it did ("`_fmt_kind_scope` branches on its
    # presence") — it does not; it is `kd.get(...) or []`. Pinned as behaviour
    # by `test_the_kind_scope_RENDER_collapses_missing_and_empty_deliberately`,
    # with the reasoning there.
    assert "kinds_excluded_by_filter" not in off["caveats"]["kind_scope"]


def test_the_kind_scope_RENDER_collapses_missing_and_empty_deliberately():
    """🔴 THE PROSE ASKS A NARROWER QUESTION THAN THE JSON, and that is the
    whole reason the two surfaces differ. `_fmt_kind_scope` asks only WHICH
    WHOLE KINDS A FILTER TOOK — every clause it can emit about the filter is
    guarded on a non-empty list. "No filter ran" and "a filter ran and took no
    whole kind" give the same answer to that question, so rendering them
    differently would mean inventing a sentence about a filter that removed
    nothing.

    Contrast `kinds_produced` one field over, where missing and empty ARE
    different sentences ("no scan happened, describe the build" vs "a scan
    measured zero rows") and an `or` would be the literal-masquerading-as-a-
    measurement bug that field's guard exists to prevent. The asymmetry is the
    point; this pins it so the docstring stating it is machine-checked.
    """
    base = {"kinds_produced": ["tmux"], "kinds_enumerated": ["cluster", "tmux"]}
    missing = sm._fmt_kind_scope(dict(base))
    empty = sm._fmt_kind_scope(dict(base, kinds_excluded_by_filter=[]))
    assert missing == empty, (missing, empty)
    # ...and a POSITIVE CONTROL, or the equality above is satisfiable by a
    # renderer that ignores the field entirely.
    populated = sm._fmt_kind_scope(
        dict(base, kinds_excluded_by_filter=["cluster"]))
    assert populated != missing
    assert "a FILTER REMOVED every kind=cluster row this scan produced" in populated
    assert "a FILTER REMOVED" not in missing


def test_an_unfiltered_scan_still_renders_the_UNCHANGED_caveat_sentence():
    """CONTROL. Every assertion above is about what changes under the flag; this
    pins that nothing changes without it, so the new clause cannot leak into the
    ordinary render."""
    line = next(ln for ln in sm.render_caveats(mix_gather())
                if "kind_scope" in ln)
    assert line == (
        "  caveat[kind_scope]: rows in this scan are kind=tmux — cluster is "
        "ENUMERATED but NOT PRODUCED, so no such row appears and its absence "
        "is NOT a measured zero; clawgate is reported separately under "
        "CLAWGATE QUEUE")


def test_the_FILTER_line_in_the_table_names_the_kind_it_removed():
    """The rendered claim, pinned WHOLE. A word check on this line has been
    walked before: "shell" and "kind" both appear in its own static prose."""
    rep = mix_gather(
        claude_only=True,
        runner=make_runner(local_panes=SHELLS_ONLY_PANES,
                           local_windows=SHELLS_ONLY_WINDOWS))
    lines = [ln for ln in sm.render_table(rep).splitlines()
             if "FILTER --claude-only" in ln]
    assert lines == [
        "  FILTER --claude-only: 1 shell window(s) excluded from every count "
        "above",
        "  FILTER --claude-only: it removed EVERY kind=tmux row this scan "
        "produced — their absence above is the FILTER's doing, not a measured "
        "absence",
    ]
    # ...and the second line is absent when no whole kind went (positive
    # control on the pair: the SAME flag, one row different).
    assert [ln for ln in sm.render_table(mix_gather(claude_only=True)).splitlines()
            if "FILTER --claude-only" in ln] == [
        "  FILTER --claude-only: 2 shell window(s) excluded from every count "
        "above"]


def test_measured_caveats_does_not_SHARE_the_excluded_kinds_with_the_report():
    """🔴 The purity claim, extended to the field this change adds. `cav` is
    deep-copied from CAVEATS, but the excluded-kinds list comes from
    `report["filters"]` — a DIFFERENT object, so the deepcopy says nothing
    about it. Handing the caller the report's own list lets a consumer writing
    through the returned caveats mutate the report it was derived from."""
    rep = {"hosts": {}, "filters": {"kinds_excluded_by_filter": ["cluster"]}}
    out = sm.measured_caveats(rep)
    got = out["kind_scope"]["kinds_excluded_by_filter"]
    assert got == ["cluster"]
    assert got is not rep["filters"]["kinds_excluded_by_filter"]
    got.append("POISONED")
    assert rep["filters"]["kinds_excluded_by_filter"] == ["cluster"]


def test_measured_caveats_reads_the_excluded_kinds_where_they_are_RECORDED():
    """🔴 It cannot recompute them: by the time this function runs the removed
    rows are GONE, and only the filter ever saw them. So the derivation is a
    lookup, and this feeds values that CANNOT coincide with anything derivable
    from the rows — which is the trap this file has walked into five times."""
    rep = {"hosts": {"h": {"windows": [{"kind": "tmux"}]}},
           "filters": {"kinds_excluded_by_filter": ["zzz", "qqq"]}}
    out = sm.measured_caveats(rep)["kind_scope"]
    assert out["kinds_produced"] == ["tmux"]
    assert out["kinds_excluded_by_filter"] == ["qqq", "zzz"], "sorted"
    # ...and the render moves with it, so the field is not merely stored
    line = sm._fmt_kind_scope(out)
    assert "a FILTER REMOVED every kind=qqq/zzz row this scan produced" in line


# --------------------------------------------------------------------------- #
# §5.3 — the two caveats, in the OUTPUT
# --------------------------------------------------------------------------- #
def test_the_caveats_are_in_the_json_as_structured_fields_not_prose():
    """🔴 KILLS: dropping `caveats` from the report, or reducing it to a
    string a consumer would have to parse."""
    cav = mix_gather()["caveats"]
    assert set(cav) == {"claude_detection", "fuzzyclaw_scope", "ledger_scope",
                        "waiting_signal", "unsent_prompt", "kind_scope",
                        "pane_preview"}
    det = cav["claude_detection"]
    assert det["method"] == "pane_current_command_regex"
    assert det["pattern"] == sm.CLAUDE_RE.pattern == "claude"
    fz = cav["fuzzyclaw_scope"]
    assert fz["scope"] == "local_host_only"
    # 🔴 NARROWED, and the narrowing is the point. This list used to include
    # `claude_session_id` and `age_secs`, which stopped being true the moment
    # the ledger shipped — it is read per host, so a remote row carries both.
    # The caveat is the MACHINE-READABLE one: a `--json` consumer following the
    # old list would discard exactly the fields the ledger exists to add.
    assert fz["null_fields_on_remote_rows"] == ["fuzzyclaw"]
    assert cav["ledger_scope"]["scope"] == "per_host"
    for entry in cav.values():
        assert entry["note"] and isinstance(entry["note"], str)


def test_the_remote_null_field_LEDGER_is_true_of_a_real_remote_row():
    """🔴 A RELATIONSHIP guard, not a spelling one: the ledger is checked
    against what the code actually nulls, in both directions.

    The claimed fields must be null on a REMOTE row (or the caveat overstates)
    and non-null on the equivalent LOCAL row (or the caveat names fields that
    are always null and the remote-vs-local distinction it exists to draw is
    vacuous — the positive control).

    🔴 RUN IN BOTH LEDGER CONFIGURATIONS, and that is not thoroughness — it is
    the fix for how this guard went blind. It reads `base_gather()`, which is
    pinned `use_ledger=False`, so when the ledger arrived this test could no
    longer see the two fields it had been asserting. A caveat naming
    `age_secs`/`claude_session_id` as null-on-remote would have stayed green
    here forever while the tool printed the opposite. Re-adding either to the
    list now fails on the ledger-ON pass.
    """
    for report in (base_gather(),
                   ledger_gather(laptop=[led_rec(window_id="@7", ago=120,
                                                 tmux_pid=LEDGER_PID_LAPTOP)],
                                 use_fuzzyclaw=True)):
        claimed = (report["caveats"]["fuzzyclaw_scope"]
                   ["null_fields_on_remote_rows"])
        remote = report["hosts"]["laptop"]["windows"][0]
        assert remote["host"] == "laptop"
        for field in claimed:
            assert remote[field] is None, f"{field} is not null on a remote row"
        local = next(r for r in report["hosts"]["workbench"]["windows"]
                     if r["session"] == "scratch7")
        for field in claimed:
            assert local[field] is not None, (
                f"{field} is null LOCALLY too — the ledger draws no "
                "distinction")


def test_the_LEDGER_scope_caveat_is_true_of_a_real_remote_row():
    """🔴 The other half, and the direction the old caveat got WRONG: it stated
    a remote row is "NEVER labelled `stale`" and carries no session id. The
    ledger is read per host, so both are false — asserted here against a real
    remote row rather than against the sentence.

    Positive control built in: the SAME fixture with no laptop record leaves
    that row null, so this is a claim about the ledger supplying the fields and
    not about the row being non-null for some unrelated reason.
    """
    with_rec = ledger_gather(
        laptop=[led_rec(window_id="@7", session_id="ledger-remote",
                        ago=9000, tmux_pid=LEDGER_PID_LAPTOP)],
        use_fuzzyclaw=False)
    remote = next(r for r in rows_of(with_rec) if r["host"] == "laptop")
    assert remote["age_secs"] == 9000.0
    assert remote["claude_session_id"] == "ledger-remote"
    assert remote["status"] == "stale", (
        "the old caveat said a remote row is NEVER stale; it can be")

    without = ledger_gather(laptop=[], use_fuzzyclaw=False)
    bare = next(r for r in rows_of(without) if r["host"] == "laptop")
    assert bare["age_secs"] is None and bare["claude_session_id"] is None


def test_the_caveats_are_printed_in_the_table_UNCONDITIONALLY(
        monkeypatch, capsys, absent_blocked_cache):
    """🔴 KILLS: printing them only when a remote host is scanned, and dropping
    the footer. An agent that runs this cold gets no skill body."""
    for report in (mix_gather(),                    # local-only scan
                   base_gather(),                   # both hosts
                   mix_gather(claude_only=True)):   # filtered
        text = sm.render_table(report)
        assert "caveat[claude_detection]:" in text
        assert "pane_current_command =~ /claude/" in text
        assert "wrapper shell" in text
        assert "caveat[fuzzyclaw_scope]:" in text
        assert "local_host_only" in text
        # 🔴 The corrected pair, asserted TOGETHER. The fuzzyclaw line used to
        # end "and are never `stale`" — a claim about age/session-id/stale that
        # the per-host ledger falsifies. Printing the correction beside it is
        # what stops both readings staying available.
        assert "caveat[ledger_scope]:" in text
        assert "per_host" in text
        assert "CAN be `stale`" in text
        assert "never `stale`" not in text, (
            "the retracted claim is back in the footer")
    # ...including the degenerate reports the renderer must survive
    empty = {"hosts": {}, "summary": {}, "clickhouse": {}, "fuzzyclaw": {}}
    assert "caveat[claude_detection]:" in sm.render_table(empty)

    # and END-TO-END, on the path a cold caller actually takes
    monkeypatch.setattr(sm, "local_host_label", lambda *a, **k: "workbench")
    monkeypatch.setattr(sm, "_default_runner", make_runner())
    monkeypatch.setattr(sm, "read_fuzzyclaw_texts", lambda *a, **k: [])
    sm.main(["scan", "--no-ch", "--host", "workbench"])
    assert "caveat[fuzzyclaw_scope]:" in capsys.readouterr().out


def test_the_caveat_footer_is_one_line_per_CAVEAT_pinned_both_ways():
    """Compactness is part of the requirement — a caveat that bloats the table
    gets deleted by the next person.

    🔴 ONE LINE PER CAVEAT, derived from CAVEATS rather than pinned to a
    literal count: a hardcoded `== 2` is a number the next caveat has to edit,
    and editing it is indistinguishable from silencing a caveat that stopped
    rendering. The relationship — every structured caveat gets exactly one
    footer line — is what actually needs pinning, and it fails when the set
    GROWS *or* SHRINKS.
    """
    assert len(sm.render_caveats(mix_gather())) == len(sm.CAVEATS)
    for key in sm.CAVEATS:
        assert any(f"caveat[{key}]:" in ln
                   for ln in sm.render_caveats(mix_gather())), key
    with_cav = sm.render_table(mix_gather()).splitlines()
    row_lines = [ln for ln in with_cav if "hollow" in ln or "ridge" in ln]
    assert len(row_lines) == 2, "one line per window, still"
    assert not any("caveat[" in ln for ln in row_lines)


# =========================================================================== #
# §A — THE `waiting` SIGNAL
#
# 🔴 THE FIXTURES BELOW ARE REALISTIC, NOT TOY, AND THAT IS LOAD-BEARING.
# They reproduce the structure captured off 40 live panes on 2026-08-12: a `❯`
# echo of the SUBMITTED user prompt in the scrollback, `●`-led assistant turns,
# a `✻ Baked for …` / `※ recap:` status line, the input box drawn between two
# box-drawing rules, and a `ctx: NN%` + `⏸ manual mode on` footer. A detector
# can pass on a textbook three-line fixture and miss every one of those,
# because the whole difficulty is telling the agent's last SENTENCE from the
# five kinds of chrome that surround it.
#
# 🔴 EVERY STRING IS SYNTHETIC. This repo is PUBLIC and the real panes hold
# client work; the SHAPES are copied, the words are invented, and the values
# are pairwise distinct so a test cannot pass by matching the wrong row.
#
# 🔴 THAT CLAIM WAS FALSE ONCE, AND SAYING IT LOUDER IS NOT THE FIX. Four REAL
# operator-typed drafts were quoted verbatim in
# `reference/waiting-signal.md` and re-used as fixtures below, under this very
# header. The route they took is mechanical — a string that lands in the doc
# recording the dogfood AND in the test reproducing it — so that route is what
# is now checked, by `test_no_FIXTURE_DRAFT_string_appears_in_a_shipped_doc`.
# NEVER PASTE A CAPTURED DRAFT INTO A COMMITTED FILE: report counts, lengths and
# shapes instead.
# =========================================================================== #
_RULE = "─" * 60
CAP_NONCE = "deadbeefcafe1234"


def _pane(*body):
    return "\n".join(body)


# Genuinely idle: finished cleanly, awaiting instruction. THE NEGATIVE CONTROL.
# Two traps are built in on purpose:
#   * the user's own submitted prompt, echoed in the scrollback, ENDS IN `?`
#   * the footer contains a `?` (`new task? /clear …`) — a real live footer
# Neither is the agent asking anything, and neither may flag.
PANE_IDLE = _pane(
    "❯ can you double-check the fixture ordering?",
    "",
    "● Checked. The ordering is stable and the two helpers agree.",
    "",
    "  I pushed the change and the branch is green.",
    "",
    "✻ Baked for 4m 02s",
    "",
    "※ recap: Goal was stabilising fixture order. Done and pushed. "
    "(disable recaps in /config)",
    "",
    _RULE,
    "❯ ",
    _RULE,
    "  ctx: 34%                          new task? /clear to save 120.0k tokens",
    "  ⏸ manual mode on · ← 2 agents",
)

# Asked a direct question. Same chrome, one different sentence.
PANE_QUESTION = _pane(
    "❯ deploy it",
    "",
    "● Deployed. The rollout finished and both replicas are ready.",
    "",
    "  Want me to run the post-deploy check before I close this out?",
    "",
    "✻ Baked for 1m 18s",
    "",
    _RULE,
    "❯ ",
    _RULE,
    "  ctx: 52%",
    "  ⏸ manual mode on",
)

# Hard-blocked on a modal. The modal REPLACES the input box, so there is only
# ONE rule line and the options sit below it — which is why the chrome cut
# cannot assume a pair.
PANE_MENU = _pane(
    "● I can take either route here.",
    "",
    "✻ Baked for 9m 41s",
    "",
    _RULE,
    "  This session is 12h 06m old and 88.4k tokens.",
    "",
    "  Resuming the full session will consume a large share of your limits.",
    "",
    "  ❯ 1. Resume from summary (recommended)",
    "    2. Resume the full session as-is",
    "    3. Do not ask me again",
    "",
    "  Enter to confirm · Esc to cancel",
)

# Out of context. NOTHING else about the pane says so — only the footer does,
# and the agent's last sentence is a perfectly ordinary statement.
PANE_CTX_ZERO = _pane(
    "❯ keep going",
    "",
    "● Continuing with the migration notes.",
    "",
    "  The remaining items are listed in the tracking file.",
    "",
    "✻ Baked for 22m 07s",
    "",
    _RULE,
    "❯ ",
    _RULE,
    "  ctx: 0%                           new task? /clear to save 610.0k tokens",
    "  ⏸ manual mode on · ← 3 agents",
)

# 🔴 THE EXCLUDED CASE — text sitting at the `❯` prompt inside the input box.
# See CAVEATS["waiting_signal"]["excluded"] and reference/waiting-signal.md.
# This fixture is mid-turn (live spinner), which is precisely the state where a
# detector keying on box text would invent a `waiting` row for a window that is
# working fine.
PANE_TYPED_AT_PROMPT = _pane(
    "❯ start the refactor",
    "",
    "● Working through the call sites now.",
    "",
    "  Three of nine done.",
    "",
    "* Calculating… (31s · ↓ 2.1k tokens · esc to interrupt)",
    "",
    _RULE,
    "❯ then open the PR",
    _RULE,
    "  ctx: 61%",
    "  ⏸ manual mode on",
)

# A bare shell. Its last line ends in `?`, which is exactly why a Claude-shaped
# detector must never be pointed at one.
PANE_SHELL = _pane(
    "$ git status -s",
    "$ gh pr list",
    "no open pull requests. run `gh pr create`?",
)


def cap_runner(captures_by_pane, **kw):
    """A `make_runner` whose capture batch answers with REAL marker framing.

    🔴 This is the instrument for every end-to-end §A test, so it is validated
    by `test_cap_runner_frames_captures_the_way_the_parser_expects` before any
    verdict is read off it. A framer that did not frame its output the way
    `parse_captures` expects would make every pane read `uncaptured`, and a
    suite asserting "nothing is waiting" against it would be green, unanimous
    and about nothing.
    """
    nonce = kw.pop("nonce", CAP_NONCE)
    body = "".join(
        "%s%s%s%s\n%s\n" % (sm.CAPTURE_MARK_PREFIX, nonce, pid,
                            sm.CAPTURE_MARK_PREFIX, text)
        for pid, text in captures_by_pane.items())
    kw.setdefault("local_capture", body)
    return make_runner(**kw)


def test_cap_runner_frames_captures_the_way_the_parser_expects():
    """POSITIVE CONTROL on the §A instrument, read before any of its verdicts.

    Prove the fake capture output ROUND-TRIPS through the real parser, and that
    the parser can also come back EMPTY — an instrument that always parsed, or
    never did, certifies nothing in either direction.
    """
    r = cap_runner({"%11": PANE_QUESTION, "%13": PANE_IDLE})
    raw = r(sm.capture_argv(["%11", "%13"], CAP_NONCE), 5)[1]
    got = sm.parse_captures(raw, CAP_NONCE)
    assert set(got) == {"%11", "%13"}
    assert got["%11"] == PANE_QUESTION
    assert got["%13"] == PANE_IDLE
    # ...and the negative control on the parser: a DIFFERENT nonce must find
    # nothing, or the nonce is decoration and a pane could forge a marker.
    assert sm.parse_captures(raw, "0000ffff0000ffff") == {}


# --------------------------------------------------------------------------- #
# §A.1 — the capture protocol (ONE tmux call per host, marker-delimited)
# --------------------------------------------------------------------------- #
def test_the_marker_never_interpolates_a_pane_id_into_a_tmux_format():
    """🔴 THE BUG THIS PINS IS SILENT AND WAS MEASURED ON LIVE TMUX.

    A tmux format string is strftime-expanded, so a literal `%68` in it is NOT
    the pane id — it is `%B` (full month name) padded to width 68. Verified
    2026-08-12: `tmux display-message -p 'A%68B'` prints `A`, 62 spaces, then
    `August`. The marker therefore carries the id via `#{pane_id}` and `-t`,
    never by our own interpolation, and this fails the moment someone
    "simplifies" it back.
    """
    mark = sm.capture_mark("abc123")
    assert "#{pane_id}" in mark
    assert "%" not in mark, "a literal % in a tmux format is strftime, not an id"
    argv = sm.capture_argv(["%68"], "abc123")
    fmt = argv[argv.index("-t") + 2]
    assert fmt == mark and "%68" not in fmt
    # the id travels as a `-t` TARGET, which is not format-expanded
    assert "%68" in argv


def test_capture_argv_is_empty_for_no_panes_rather_than_a_bare_tmux():
    """🔴 A bare `["tmux"]` is not a no-op — it runs tmux's default command.

    This script is read-only BY CONSTRUCTION, and "the pane list came back
    empty" is exactly the path where an argv degenerates unnoticed.
    """
    assert sm.capture_argv([], "n1") == []
    assert sm.capture_argv(None, "n1") == []
    assert sm.capture_argv(["", None], "n1") == []


def test_the_capture_argv_contains_only_READ_ONLY_tmux_subcommands():
    """🔴 THE SAFETY INVARIANT, as an ALLOWLIST rather than a denylist.

    A denylist ("no send-keys") passes for every mutating subcommand nobody
    thought to name. This enumerates what MAY appear, so a new verb fails
    closed. `session-manager` gets pointed at a live machine holding 40+
    windows of the operator's real work; that is only safe while this holds.
    """
    argv = sm.capture_argv(["%1", "%2", "%3"], "n2")
    verbs = {argv[0]}
    for i, tok in enumerate(argv):
        if tok == ";":
            verbs.add(argv[i + 1])
    assert verbs == {"tmux", "display-message", "capture-pane"}
    assert argv[1] == "display-message"


def test_one_tmux_invocation_covers_every_pane():
    """The whole reason for the marker protocol: 40 panes over SSH must not be
    40 round trips."""
    argv = sm.capture_argv(["%1", "%2", "%3", "%4"], "n3")
    assert argv.count("capture-pane") == 4
    assert argv[0] == "tmux" and argv.count("tmux") == 1


def test_the_capture_argv_survives_the_ssh_quoting_it_must_pass_through():
    """The remote side runs a SHELL. The format contains `#`, `{` and `}`, and
    the command separator is a bare `;` — all of which a shell would eat
    unquoted. Same hazard and same test shape as the window-format contract."""
    import shlex
    argv = sm.capture_argv(["%9"], "n4")
    assert shlex.split(shlex.join(argv)) == argv
    wrapped = sm.ssh_wrap(argv)
    assert wrapped[0] == "ssh" and wrapped[-2] == sm.LAPTOP_SSH_TARGET
    assert shlex.split(wrapped[-1]) == argv


def test_parse_captures_discards_text_before_the_first_marker():
    """Output belonging to no pane is DROPPED, never attributed to one — tmux
    can emit a warning ahead of the first command's output."""
    raw = ("some preamble tmux wrote first\n"
           f"{sm.CAPTURE_MARK_PREFIX}n5%7{sm.CAPTURE_MARK_PREFIX}\n"
           "real pane text\n")
    assert sm.parse_captures(raw, "n5") == {"%7": "real pane text"}


def test_parse_captures_is_empty_on_empty_or_unmarked_input():
    assert sm.parse_captures("", "n6") == {}
    assert sm.parse_captures(None, "n6") == {}
    assert sm.parse_captures("no markers at all\njust text\n", "n6") == {}


# --------------------------------------------------------------------------- #
# §A.2 — detect_waiting: three signals, each reachable and each isolated
# --------------------------------------------------------------------------- #
def _sigs(text):
    return {s["signal"] for s in sm.detect_waiting(text)["signals"]}


def test_POSITIVE_CONTROL_the_detector_produces_a_NON_ZERO_count():
    """🔴 MANDATORY. A classifier returning 0 is indistinguishable from one
    wired to nothing, so the number has to be watched to MOVE.

    Quote this as a PAIR wherever it is reported: N on the positive control,
    M under test. Here N = 3 flagged of 6 realistic panes.
    """
    panes = [PANE_IDLE, PANE_QUESTION, PANE_MENU, PANE_CTX_ZERO,
             PANE_TYPED_AT_PROMPT]
    flagged = [p for p in panes if sm.detect_waiting(p)["probable"]]
    assert len(flagged) == 3, "positive control must be NON-ZERO and exact"
    assert PANE_QUESTION in flagged
    assert PANE_MENU in flagged
    assert PANE_CTX_ZERO in flagged


def test_NEGATIVE_CONTROL_a_genuinely_idle_window_is_not_flagged():
    """🔴 The other half. `PANE_IDLE` carries TWO `?` traps — the user's own
    submitted prompt echoed into the scrollback, and the live `new task?`
    footer — and a detector firing on either is worse than none, because it
    manufactures work at the moment the operator most trusts the output."""
    got = sm.detect_waiting(PANE_IDLE)
    assert got["probable"] is False
    assert got["signals"] == []


def test_the_EXCLUDED_case_text_typed_at_the_prompt_never_flags():
    """🔴 THE OPEN DECISION, settled and pinned.

    A dogfood run read four windows' prompt text as "unsent instructions one
    Enter away" and could not rule out placeholder chrome. Investigated
    2026-08-12: it IS distinguishable, by POSITION rather than dimness — a
    placeholder cannot coexist with typed text, and a queued message renders
    above the box rather than in it. Both facts are read off a MINIFIED bundle
    at one version, and the queued case was never observed live (0 hits across
    29 panes), so the signal would rest on an unobserved negative whose failure
    mode is a false `waiting` row on a window that is working fine — note this
    fixture is mid-turn, with a live spinner.

    EXCLUDED. This test fails if anyone adds it back without the observation.
    """
    got = sm.detect_waiting(PANE_TYPED_AT_PROMPT)
    assert got["probable"] is False, "text at the ❯ prompt is not a signal"
    assert got["signals"] == []


def test_signal_trailing_question_fires_ALONE_on_its_own_pane():
    """Isolation matters: if this pane also matched another signal, a mutation
    that broke THIS one could still be masked by the other going green."""
    assert _sigs(PANE_QUESTION) == {"trailing_question"}
    line = sm.detect_waiting(PANE_QUESTION)["signals"][0]["line"]
    assert line == "Want me to run the post-deploy check before I close this out?"


def test_signal_selection_menu_fires_ALONE_on_its_own_pane():
    assert _sigs(PANE_MENU) == {"selection_menu"}
    line = sm.detect_waiting(PANE_MENU)["signals"][0]["line"]
    assert line == "❯ 1. Resume from summary (recommended)"


def test_signal_context_exhausted_fires_ALONE_on_its_own_pane():
    assert _sigs(PANE_CTX_ZERO) == {"context_exhausted"}
    line = sm.detect_waiting(PANE_CTX_ZERO)["signals"][0]["line"]
    assert line.startswith("ctx: 0%")


def test_the_signals_are_INDEPENDENT_not_short_circuited():
    """🔴 REACHABILITY. An early `return` on the first hit would leave the later
    signals unreachable — and every isolated test above would STILL pass,
    because each fires first on its own fixture.

    The evaluation order is menu -> ctx -> question, so TWO REALISTIC PAIRS
    give complete coverage of that class: a return after `menu` kills both of
    the others (caught by pair 1), and a return after `ctx` kills `question`
    (caught by pair 2). A return after `question` is a no-op — it is last.

    Pairs, not a triple, because a triple is not a shape live tmux produces: a
    modal REPLACES the input box, so a pane showing `❯ 1.` has no `ctx: NN%`
    footer at all. Verified against the live modals on this host 2026-08-12. A
    fixture asserting an impossible layout would pin the detector against a
    screen it will never see.
    """
    # pair 1 — the agent asks, then puts up the modal. Live shape (a real pane
    # this run had "…Want me to?" above a resume prompt).
    menu_and_question = _pane(
        "● I can take either route here.",
        "",
        "  Worth a second pair of eyes before I merge. Want me to?",
        "",
        "✻ Baked for 6m 12s",
        "",
        _RULE,
        "  Resuming the full session will consume a large share of your limits.",
        "",
        "  ❯ 1. Resume from summary (recommended)",
        "    2. Resume the full session as-is",
        "",
        "  Enter to confirm · Esc to cancel",
    )
    assert _sigs(menu_and_question) == {"selection_menu", "trailing_question"}

    # pair 2 — out of context AND asking. Ordinary idle chrome.
    ctx_and_question = _pane(
        "● I have run out of room to keep going.",
        "",
        "  Do you want me to write the handoff before you clear this?",
        "",
        "✻ Baked for 41m 09s",
        "",
        _RULE,
        "❯ ",
        _RULE,
        "  ctx: 0%                        new task? /clear to save 610.0k tokens",
        "  ⏸ manual mode on",
    )
    assert _sigs(ctx_and_question) == {"context_exhausted", "trailing_question"}

    # ...and together the two pairs cover every declared signal, so this is not
    # two tests that happen to exercise the same one.
    assert (_sigs(menu_and_question) | _sigs(ctx_and_question)
            == set(sm.WAITING_SIGNALS))


def test_every_declared_signal_is_actually_reachable():
    """🔴 THE LEDGER, both directions. A name in WAITING_SIGNALS no input can
    produce reads `0` forever and gets mistaken for a measurement; a signal the
    detector emits but the tuple omits vanishes from every roll-up. Neither is
    visible from a per-signal test."""
    produced = set()
    for pane in (PANE_QUESTION, PANE_MENU, PANE_CTX_ZERO):
        produced |= _sigs(pane)
    assert produced == set(sm.WAITING_SIGNALS)


@pytest.mark.parametrize("footer,expect", [
    ("  ctx: 0%", True),
    ("  ctx: 0%   new task? /clear to save 610.0k tokens", True),
    ("  ctx: 10%", False),
    ("  ctx: 20%", False),
    ("  ctx: 100%", False),
    ("  ctx: 0.4%", False),
])
def test_context_exhausted_matches_ZERO_and_not_its_neighbours(footer, expect):
    """🔴 Measured AT the boundary and on BOTH sides of it. `ctx: 10%` contains
    the characters `0%`, so a substring test would fire on every window whose
    context happens to end in a zero — 10%, 20%, 100%. That is a `/clear`
    recommendation for a window with plenty of room left."""
    text = _pane("● done.", "", _RULE, "❯ ", _RULE, footer)
    assert ("context_exhausted" in _sigs(text)) is expect


def test_a_lone_numbered_line_in_prose_is_not_a_modal():
    """🔴 A SECOND OPTION IS REQUIRED, and this is why. Agents quote menus back
    at the operator in ordinary prose all day; a live modal always has at least
    two choices. Without this the signal fires on any transcript containing
    `❯ 1. …`."""
    prose = _pane(
        "● I would have picked the first option:",
        "",
        "  ❯ 1. Retitle and post the nudge",
        "",
        "  ...but I did not, because you asked me to hold.",
        "",
        _RULE, "❯ ", _RULE, "  ctx: 40%",
    )
    assert "selection_menu" not in _sigs(prose)


def test_a_two_option_modal_IS_a_modal_the_mirror_of_the_test_above():
    """The positive half — otherwise the test above also passes for a detector
    that never fires at all."""
    modal = _pane(
        "● Proceed?",
        "",
        "  ❯ 1. Yes",
        "    2. No",
        "",
        "  Enter to confirm · Esc to cancel",
    )
    assert "selection_menu" in _sigs(modal)


def test_last_assistant_line_cuts_the_input_box_STRUCTURALLY():
    """🔴 The chrome is found by the RULES that draw it, not by the words it
    contains. A keyword cut (`ctx:`, `⏸`, `❯`) breaks the moment upstream
    restyles a footer — and upstream restyles footers."""
    assert sm.last_assistant_line(PANE_IDLE) == (
        "  I pushed the change and the branch is green.")
    assert sm.last_assistant_line(PANE_QUESTION) == (
        "  Want me to run the post-deploy check before I close this out?")


def test_last_assistant_line_strips_the_between_turn_STATUS_lines():
    """`✻ Baked for …`, `* Calculating…`, `※ recap:` and the braille spinner sit
    BELOW the last thing the agent said. Left in place each becomes "the last
    line", and the question-mark test then reads a status line."""
    for status in ("✻ Baked for 3m 01s", "* Calculating… (12s · ↓ 900 tokens)",
                   "※ recap: something happened. (disable recaps in /config)",
                   "⠙ Thinking…"):
        text = _pane("● The answer is yes.", "", status, "",
                     _RULE, "❯ ", _RULE, "  ctx: 44%")
        assert sm.last_assistant_line(text) == "● The answer is yes.", status


def test_last_assistant_line_is_None_when_there_is_no_prose_at_all():
    assert sm.last_assistant_line("") is None
    assert sm.last_assistant_line(None) is None
    assert sm.last_assistant_line(_pane(_RULE, "❯ ", _RULE, "  ctx: 9%")) is None


def test_the_detector_alone_CAN_be_fooled_by_a_shell_which_is_why_it_is_gated():
    """🔴 Stated rather than implied away. `detect_waiting` is shape-matching
    and a shell whose last line ends in `?` DOES fool it — which is exactly why
    `gather` never points it at one, and why a non-Claude row is `not_claude`
    rather than `False`. The guard that matters is the gate, pinned in §A.3;
    this pins that the gate is load-bearing rather than decorative."""
    assert sm.detect_waiting(PANE_SHELL)["probable"] is True


# --------------------------------------------------------------------------- #
# §A.3 — the TRI-STATE end to end. `waiting_probable` is True / False / None,
# and every None carries a `waiting_status` naming which of the four reasons.
# This is the half that stops a `no` from being manufactured out of a pane
# nobody looked at.
# --------------------------------------------------------------------------- #
def _framed(captures, nonce=CAP_NONCE):
    return "".join(
        "%s%s%s%s\n%s\n" % (sm.CAPTURE_MARK_PREFIX, nonce, pid,
                            sm.CAPTURE_MARK_PREFIX, text)
        for pid, text in captures.items())


def waiting_gather(local=None, remote=None, **kw):
    """base_gather with BOTH hosts' capture batches really framed.

    %11 is the local Claude pane (scratch7:3) and %21 the remote one
    (naida-dev:1) in the shared fixtures at the top of this file.
    """
    runner = make_runner(
        local_capture=_framed(local if local is not None else {}),
        remote_capture=_framed(remote if remote is not None else {}))
    kw.setdefault("runner", runner)
    kw.setdefault("capture_nonce", CAP_NONCE)
    return base_gather(**kw)


def _row(report, host, session, widx):
    for r in report["hosts"][host]["windows"]:
        if r["session"] == session and r["window_index"] == widx:
            return r
    raise AssertionError(f"no row {host} {session}:{widx}")


def test_a_waiting_row_carries_the_flag_the_signals_AND_the_matched_line():
    """END-TO-END: a real pane shape goes in, a judgeable row comes out.

    The MATCHED LINE is the point. `waiting_probable: true` alone is a verdict
    this cannot honestly issue about a TUI upstream restyles at will — the row
    ships the evidence so a consumer can disagree with it.
    """
    rep = waiting_gather(local={"%11": PANE_QUESTION})
    row = _row(rep, "workbench", "scratch7", "3")
    assert row["waiting_probable"] is True
    assert row["waiting_status"] == "ok"
    assert [s["signal"] for s in row["waiting_signals"]] == ["trailing_question"]
    assert row["waiting_signals"][0]["line"].endswith("close this out?")


def test_a_scraped_idle_row_is_FALSE_which_is_a_measurement():
    """The other side of the same coin: `False` is only ever published for a
    pane that was actually captured and read."""
    rep = waiting_gather(local={"%11": PANE_IDLE})
    row = _row(rep, "workbench", "scratch7", "3")
    assert row["waiting_probable"] is False
    assert row["waiting_status"] == "ok"
    assert row["waiting_signals"] == []


def test_a_NON_CLAUDE_row_is_never_False_it_is_not_claude():
    """🔴 A bare zsh is never scraped, so `False` would be a verdict about a
    pane nobody looked at. `misc:5` is the shell window in the shared fixture.

    The discriminating control is right beside it: the CLAUDE row in the same
    report, from the same runner, IS measured. If the gate were inverted or
    absent, one of these two assertions breaks.
    """
    rep = waiting_gather(local={"%11": PANE_IDLE})
    shell = _row(rep, "workbench", "misc", "5")
    assert shell["claude"] is False
    assert shell["waiting_probable"] is None
    assert shell["waiting_signals"] is None
    assert shell["waiting_status"] == "not_claude"
    assert _row(rep, "workbench", "scratch7", "3")["waiting_status"] == "ok"


def test_a_claude_row_missing_from_a_batch_that_RAN_is_uncaptured():
    """🔴 `{}` vs `None`. The batch ran and answered; this pane simply is not in
    it. That is a fact about the PANE, and it must not be reported as the
    host's failure nor as a measured `no`."""
    rep = waiting_gather(local={"%99": PANE_QUESTION})   # a different pane
    row = _row(rep, "workbench", "scratch7", "3")
    assert row["waiting_probable"] is None
    assert row["waiting_status"] == "uncaptured"
    assert rep["hosts"]["workbench"]["captures_measured"] is True


def test_no_capture_makes_every_row_None_and_NEVER_False():
    """🔴 `--no-capture`. Nothing was scraped, so nothing may be reported as
    scraped — the status is `skipped`, not a quiet `no` on every row."""
    rep = base_gather(use_capture=False)
    rows = [r for h in rep["hosts"].values() for r in h["windows"]]
    assert rows, "fixture must produce rows or this passes vacuously"
    for r in rows:
        assert r["waiting_probable"] is None
        assert r["waiting_signals"] is None
    claude_rows = [r for r in rows if r["claude"]]
    assert claude_rows and all(r["waiting_status"] == "skipped"
                               for r in claude_rows)
    for host in rep["hosts"].values():
        assert host["captures_measured"] is False
        assert host["captures_status"] == "skipped"
        assert host["captures_seen"] is None


def test_a_FAILED_capture_batch_is_error_not_skipped_and_not_a_zero():
    """🔴 The two unmeasured reasons are DIFFERENT FACTS. `skipped` means the
    operator asked not to look; `error` means we looked and the host did not
    answer. Rendering them the same tells an operator their `--no-capture` is
    responsible for an SSH failure."""
    runner = make_runner(local_capture_rc=1, local_capture_err="capture died")
    rep = base_gather(runner=runner, capture_nonce=CAP_NONCE)
    assert rep["hosts"]["workbench"]["captures_status"] == "error"
    assert rep["hosts"]["workbench"]["captures_measured"] is False
    assert rep["hosts"]["workbench"]["captures_seen"] is None
    row = _row(rep, "workbench", "scratch7", "3")
    assert row["waiting_probable"] is None and row["waiting_status"] == "error"
    # ...and the OTHER host's batch, which did answer, is unaffected: one call
    # failing says nothing about the other.
    assert rep["hosts"]["laptop"]["captures_status"] == "ok"


def test_a_host_with_no_claude_panes_is_a_MEASURED_empty_capture_set():
    """`{}` with `status: ok` — we know every pane on that host and none of
    them is Claude. Distinct from `None`, and no tmux capture call is made."""
    calls = []
    runner = make_runner(local_panes=(
        "%13|1003|misc|5|win-charlie|/home/zach/tmp|zsh|plain title"),
        calls=calls)
    rep = base_gather(runner=runner, hosts=("workbench",),
                      capture_nonce=CAP_NONCE)
    wb = rep["hosts"]["workbench"]
    assert wb["captures_measured"] is True
    assert wb["captures_status"] == "ok"
    assert wb["captures_seen"] == 0
    assert not any("capture-pane" in " ".join(c) for c in calls), (
        "no Claude panes means no capture call at all")


def test_only_CLAUDE_pane_ids_are_ever_sent_to_the_capture_batch():
    """🔴 The gate, pinned on the ARGV rather than on the output. A detector
    that never sees a shell pane cannot invent a signal from one — and reading
    this off the row alone would not distinguish "not scraped" from "scraped
    and suppressed"."""
    calls = []
    base_gather(runner=make_runner(calls=calls, local_capture=_framed({})),
                hosts=("workbench",), capture_nonce=CAP_NONCE)
    cap = [c for c in calls if "capture-pane" in " ".join(c)]
    assert len(cap) == 1, "one batched call per host"
    argv = cap[0]
    assert "%11" in argv, "the claude pane must be captured"
    assert "%12" not in argv and "%13" not in argv, "shells must not be"


# --------------------------------------------------------------------------- #
# §A.4 — the roll-up. `probable` is None when nothing was scraped.
# --------------------------------------------------------------------------- #
def test_the_summary_waiting_count_MOVES_positive_control_and_under_test():
    """🔴 THE PAIR, in one test, so neither number can be quoted alone.

    A `waiting: 0` from a classifier wired to nothing is indistinguishable from
    a real 0 — so the same fixture is run twice, once with a pane that MUST
    flag and once with a pane that must not, and both numbers are asserted.
    """
    positive = waiting_gather(local={"%11": PANE_MENU})
    under_test = waiting_gather(local={"%11": PANE_IDLE})
    assert positive["summary"]["waiting"]["probable"] == 1     # N
    assert under_test["summary"]["waiting"]["probable"] == 0   # M
    # both scraped the same number of panes, so the difference is the SIGNAL
    # and not the sample.
    assert (positive["summary"]["waiting"]["measured"]
            == under_test["summary"]["waiting"]["measured"] == 1)


def test_the_summary_waiting_is_None_not_0_when_nothing_was_scraped():
    """🔴 The sentence this tool must never emit is "nothing is waiting on you"
    off a look that never happened. `--no-capture` scrapes nothing, so the
    headline number is None and the renderer says UNMEASURED."""
    rep = base_gather(use_capture=False)
    w = rep["summary"]["waiting"]
    assert w["probable"] is None
    assert w["per_signal"] is None
    assert w["measured"] == 0
    assert w["unmeasured"] == 3
    assert w["unmeasured_reasons"] == {"skipped": 2, "not_claude": 1}


def test_the_per_signal_breakdown_is_derived_from_the_declared_ledger():
    """🔴 The tuple and the roll-up pin each other. A signal `detect_waiting`
    emits but WAITING_SIGNALS omits would be counted per-row and invisible in
    every total; one declared but never emitted reads 0 forever and gets taken
    for a measurement."""
    rep = waiting_gather(local={"%11": PANE_CTX_ZERO})
    per = rep["summary"]["waiting"]["per_signal"]
    assert set(per) == set(sm.WAITING_SIGNALS)
    assert per["context_exhausted"] == 1
    assert per["trailing_question"] == 0 and per["selection_menu"] == 0


def test_unmeasured_reasons_distinguishes_the_four_ways_a_row_goes_None():
    """The None is only actionable if it says WHY. Three reasons in one report:
    a shell (`not_claude`), a claude pane absent from the batch
    (`uncaptured`), and a remote host whose batch also answered."""
    rep = waiting_gather(local={"%99": PANE_IDLE}, remote={"%21": PANE_MENU})
    reasons = rep["summary"]["waiting"]["unmeasured_reasons"]
    assert reasons == {"not_claude": 1, "uncaptured": 1}
    assert rep["summary"]["waiting"]["measured"] == 1     # the remote row
    assert rep["summary"]["waiting"]["probable"] == 1


def test_the_renderer_says_UNMEASURED_rather_than_printing_a_zero():
    """🔴 The plain-text reader gets the same discipline as the JSON one. A
    `waiting: 0` line under `--no-capture` is the whole failure in one line of
    output."""
    text = sm.render_table(base_gather(use_capture=False))
    assert "waiting: UNMEASURED" in text
    assert "waiting: 0 probable" not in text
    # ...and the measured case really does print a number, or the assertion
    # above passes for a renderer that never prints the line at all.
    measured = sm.render_table(waiting_gather(local={"%11": PANE_IDLE}))
    assert "waiting: 0 probable of 1 scraped" in measured


def test_the_renderer_prints_the_matched_line_for_every_flagged_row():
    """The evidence has to reach the human, not only the `--json` consumer —
    the four states a `waiting` row can be in need four different actions, and
    only the matched line says which."""
    text = sm.render_table(waiting_gather(local={"%11": PANE_MENU}))
    assert "⚠ WAITING" in text
    assert "[selection_menu]" in text
    assert "❯ 1. Resume from summary (recommended)" in text
    # and an unflagged report must not print the block at all
    assert "⚠ WAITING" not in sm.render_table(
        waiting_gather(local={"%11": PANE_IDLE}))


def test_the_WAIT_column_never_spells_an_unmeasured_row_as_no():
    """🔴 `?uncaptured` and `no` are six characters apart and mean opposite
    things. The cell for an unscraped pane must not be readable as a
    measurement."""
    text = sm.render_table(waiting_gather(local={"%99": PANE_IDLE}))
    row = [ln for ln in text.splitlines() if "scratch7" in ln][0]
    assert "?uncaptured" in row
    assert not row.endswith(" no")


def test_the_caveat_footer_names_the_three_signals_and_the_exclusion():
    """The enumeration is what turns `WAIT: no` from "nothing needed here" into
    "none of these three matched" — which is all it ever meant."""
    line = [ln for ln in sm.render_caveats(base_gather())
            if "waiting_signal" in ln][0]
    for sig in sm.WAITING_SIGNALS:
        assert sig in line
    assert "EXCLUDED" in line


def test_the_waiting_caveat_is_structured_for_json_consumers_not_prose():
    cav = base_gather()["caveats"]["waiting_signal"]
    assert cav["signals"] == list(sm.WAITING_SIGNALS)
    assert cav["scope"] == "claude_rows_only"
    assert "prompt_buffer_text" in cav["excluded"]
    # the exclusion states its REASON, so nobody re-litigates it from scratch
    assert "never observed" in cav["excluded"]["prompt_buffer_text"]


# =========================================================================== #
# §B — CLAWGATE QUEUE (the clawgate approval queue)
#
# 🔴 The failure this closes was MEASURED, not imagined: a dogfood agent read
# an accurate line saying `agent-ops` has no JSON API, correctly preferred this
# script, never opened agent-ops, and so missed 11 pending approvals — four of
# them credential-exposure or cross-user-data-leak.
# =========================================================================== #
#
# 🔴 THE SECOND FAILURE, closed by the same section: the poller this cache comes
# from used to count ONLY {open, ready_for_review}, so an `in_progress` task
# whose agent had been dead for four hours contributed 0 here — invisible on
# every surface at once, because this script reads the cache rather than the
# API. `schema` now discriminates a cache that measured stuck dispatches from
# one that never looked; the latter reports None, never 0.
#
# Every id/title below is SYNTHETIC (public repo; real titles are client work).
# =========================================================================== #
def _cache(**kw):
    """A CURRENT (schema-2) bar-status cache: 10 open + 1 review + 1 stuck."""
    body = {"schema": 2, "count": 12, "state": "Warning", "ts": NOW - 30,
            "pending_count": 11, "stuck_count": 1,
            "open": [{"id": 160 + i, "title": "queued item %d" % i}
                     for i in range(10)],
            "ready_for_review": [{"id": 172, "title": "finished item"}],
            "stuck": [{"id": 173, "title": "wedged dispatch",
                       "reasons": ["agent_idle"], "agent_idle_secs": 14400}],
            "threshold_secs": 900,
            "detail": "12 need you (10 open, 1 review, 1 stuck): !#173, #172, "
                      "#160, #161, #162, #163 (+6 more)",
            "detail_shown": 6, "detail_total": 12, "detail_truncated": True,
            "source": "clawgate"}
    body.update(kw)
    return lambda path: json.dumps(body)


def _legacy_cache(**kw):
    """A cache written by a poller from BEFORE the stuck predicate. It has no
    `schema` key, no lists, and its `detail` truncates without saying so."""
    body = {"count": 11, "state": "Warning", "ts": NOW - 30,
            "detail": "11 task(s) awaiting: #171, #170, #169, #168, #160, #165",
            "source": "clawgate"}
    body.update(kw)
    return lambda path: json.dumps(body)


def test_a_fresh_cache_is_ok_and_carries_the_count():
    got = sm.read_clawgate_queue(reader=_cache(), now=NOW)
    assert got["status"] == "ok"
    assert got["count"] == 12
    assert got["cache_age_secs"] == 30


# --------------------------------------------------------------------------- #
# 🔴 THE STUCK-DISPATCH HALF. A live board with zero `in_progress` tasks returns
# zero stuck, and that zero is indistinguishable from a reader wired to nothing
# — so every assertion below is driven by a CONSTRUCTED cache that must report
# one, plus the twin that must not.
# --------------------------------------------------------------------------- #
def test_a_schema2_cache_surfaces_the_stuck_dispatch_with_its_idle_time():
    got = sm.read_clawgate_queue(reader=_cache(), now=NOW)
    assert got["schema_ok"] is True and got["schema_note"] is None
    assert got["stuck_count"] == 1
    assert got["stuck"] == [{"id": 173, "title": "wedged dispatch",
                             "reasons": ["agent_idle"],
                             "agent_idle_secs": 14400}]
    assert got["pending_count"] == 11
    # count = pending + stuck, and both discriminants travel with it.
    assert got["count"] == got["pending_count"] + got["stuck_count"]


def test_a_LEGACY_cache_reports_stuck_as_UNMEASURED_never_as_zero():
    """🔴 The exact substitution this whole script refuses, on the newest field.
    A poller that never looked for stuck dispatches did not find zero of them."""
    got = sm.read_clawgate_queue(reader=_legacy_cache(), now=NOW)
    assert got["status"] == "ok", "the COUNT it does carry is still usable"
    assert got["count"] == 11
    assert got["schema"] is None and got["schema_ok"] is False
    assert got["stuck_count"] is None, "a queue nobody looked at is not empty"
    assert got["stuck"] is None and got["ready_for_review"] is None
    assert got["schema_note"] and "UNMEASURED" in got["schema_note"]


@pytest.mark.parametrize("schema,ok", [
    (None, False), (0, False), (1, False),      # older than the predicate
    (2, True), (3, True),                       # current, and a future bump
    ("2", False), (True, False),                # junk must not read as a version
])
def test_the_schema_gate_is_measured_either_side_of_the_boundary(schema, ok):
    body = {} if schema is None else {"schema": schema}
    got = sm.read_clawgate_queue(reader=_cache(**body) if schema is not None
                                 else _legacy_cache(), now=NOW)
    assert got["schema_ok"] is ok, schema


def test_a_schema2_cache_enumerates_ready_for_review_rather_than_folding_it_in():
    """🔴 Three finished tasks once appeared in NO list on any surface: the
    count moved and nothing said what had finished."""
    got = sm.read_clawgate_queue(reader=_cache(), now=NOW)
    assert got["ready_for_review"] == [{"id": 172, "title": "finished item"}]
    text = "\n".join(sm.render_clawgate_queue(got))
    assert "#172 REVIEW finished item" in text
    # and every open row is named too — not just the six the detail string caps
    for i in range(10):
        assert "#%d" % (160 + i) in text


def test_the_rendered_stuck_row_carries_the_idle_time_beside_the_flag():
    text = "\n".join(sm.render_clawgate_queue(
        sm.read_clawgate_queue(reader=_cache(), now=NOW)))
    assert "1 STUCK dispatch(es)" in text
    assert "#173 idle 4h [agent_idle]" in text
    # 🔴 and it says what the measure cannot see, in the output itself
    assert "within one in-flight turn" in text.lower()


def test_a_stuck_row_at_sixteen_minutes_reads_differently_from_one_at_four_hours():
    """A bare boolean would render these identically; only one is worth acting
    on, and the number is what tells them apart."""
    near = _cache(stuck=[{"id": 173, "title": "wedged dispatch",
                          "reasons": ["agent_idle"], "agent_idle_secs": 960}])
    far = _cache()
    a = "\n".join(sm.render_clawgate_queue(sm.read_clawgate_queue(reader=near, now=NOW)))
    b = "\n".join(sm.render_clawgate_queue(sm.read_clawgate_queue(reader=far, now=NOW)))
    assert "idle 16m" in a and "idle 4h" in b
    assert a != b


def test_a_schema2_cache_MISSING_the_stuck_key_is_still_None_not_empty():
    """🔴 Found by a surviving mutant (`obj.get("stuck") or []`). A cache that
    ANNOUNCES schema 2 but arrives without the list — truncated write, partial
    upgrade, hand-edited file — must not have an empty measurement invented for
    it. `[]` here would read as "measured, nothing stuck"; the truth is that
    nothing was read."""
    body = {"schema": 2, "count": 3, "ts": NOW - 30, "state": "Warning",
            "detail": "3 need you"}
    got = sm.read_clawgate_queue(reader=lambda p: json.dumps(body), now=NOW)
    assert got["status"] == "ok" and got["count"] == 3
    assert got["stuck"] is None and got["stuck_count"] is None
    assert got["ready_for_review"] is None and got["open"] is None


def test_the_RENDERER_also_says_UNMEASURED_for_a_schema2_cache_with_no_lists():
    """🔴 THE SAME BUG ONE LAYER DOWN, and the reason a reader test was not
    enough. `read_clawgate_queue` preserves the None correctly (test above) — and
    then `_render_blocked_rows` did `b.get("stuck") or []`, collapsing exactly
    the distinction the reader had just protected. Measured: this cache rendered
    a clean "3 clawgate task(s) needing you" with NO warning anywhere, while the
    OLDER schema-1 path warned correctly. The newer, supported shape was the
    silent one, which is the worse way round.

    So the assertion is on the RENDERED TEXT, not on the parsed dict.
    """
    body = {"schema": 2, "count": 3, "ts": NOW - 30, "state": "Warning",
            "detail": "3 need you"}
    got = sm.read_clawgate_queue(reader=lambda p: json.dumps(body), now=NOW)
    text = "\n".join(sm.render_clawgate_queue(got))
    assert "NOT measured" in text or "NOT enumerated" in text, text
    assert "stuck" in text.lower()
    # …and it must not read as a measured all-clear.
    assert "0 STUCK" not in text


def test_the_renderer_distinguishes_a_MEASURED_empty_queue_from_an_ABSENT_one():
    """🔴 THE DISCRIMINATING PAIR for the renderer. A measured-empty board and a
    cache that carries no lists at all must not produce the same text — that
    equality IS the bug."""
    measured = sm.read_clawgate_queue(
        reader=_cache(stuck=[], stuck_count=0, open=[], ready_for_review=[],
                      count=0), now=NOW)
    absent_lists = sm.read_clawgate_queue(
        reader=lambda p: json.dumps({"schema": 2, "count": 3, "ts": NOW - 30,
                                     "state": "Warning", "detail": "3 need you"}),
        now=NOW)
    a = "\n".join(sm.render_clawgate_queue(measured))
    b = "\n".join(sm.render_clawgate_queue(absent_lists))
    assert a != b
    assert "NOT measured" not in a and "NOT enumerated" not in a
    assert "NOT measured" in b or "NOT enumerated" in b


# 🔴 The key names are IMPORTED, not re-spelled. Writing them out as
# `["stuck", "open", "ready_for_review"]` puts a literal superset of the
# operator-pending state set in this file, and the repo-wide single-source guard
# fails on it — correctly: the cache's list keys ARE the pending statuses (plus
# `stuck`), so spelling them here would be the third copy of the predicate that
# guard exists to prevent. This also follows the definition if it ever changes.
@pytest.mark.parametrize("missing",
                         sorted(sm.CG.PENDING_TASK_STATES) + ["stuck"])
def test_EACH_absent_list_announces_itself_separately(missing):
    # Per-list, so a fix that only covers `stuck` cannot pass. Each key is
    # dropped on its own from an otherwise complete schema-2 cache. `count` must
    # stay positive: a measured ZERO board short-circuits before the row
    # renderer, so a zero fixture would pass without exercising anything.
    full = json.loads(_cache(
        stuck=[], stuck_count=0,
        open=[{"id": 191, "title": "queued alpha"}],
        ready_for_review=[{"id": 192, "title": "queued beta"}],
        count=2, pending_count=2)("p"))
    del full[missing]
    got = sm.read_clawgate_queue(reader=lambda p: json.dumps(full), now=NOW)
    text = "\n".join(sm.render_clawgate_queue(got))
    assert missing in text, text
    assert "NOT measured" in text or "NOT enumerated" in text


def test_a_schema2_cache_with_zero_stuck_is_a_MEASURED_zero():
    """The other half of the discriminating control: `0` and `None` must not
    render the same, or the field is worthless."""
    got = sm.read_clawgate_queue(
        reader=_cache(stuck=[], stuck_count=0, count=11), now=NOW)
    assert got["stuck_count"] == 0 and got["schema_ok"] is True
    text = "\n".join(sm.render_clawgate_queue(got))
    assert "STUCK" not in text
    assert "UNMEASURED" not in text


def test_an_ABSENT_cache_is_None_and_NEVER_zero():
    """🔴 THE TOOL'S ENTIRE THESIS, applied to its newest source. A missing file
    rendering as "nothing is blocked on you" is the exact substitution every
    other section of this script exists to refuse."""
    got = sm.read_clawgate_queue(reader=lambda p: None, now=NOW)
    assert got["status"] == "absent"
    assert got["count"] is None, "an unread queue is not an empty queue"
    assert got["error"] and "not measured" in got["error"].lower()


def test_an_UNPARSEABLE_cache_is_None_and_says_so():
    for body in ("{not json", "", "null", "[]", '{"state":"Warning"}',
                 '{"count":"12"}', '{"count":true}'):
        got = sm.read_clawgate_queue(reader=lambda p, b=body: b, now=NOW)
        assert got["status"] == "unparseable", body
        assert got["count"] is None, body


@pytest.mark.parametrize("age,status", [
    (0, "ok"),
    (299, "ok"),
    (300, "ok"),          # the boundary is EXCLUSIVE: 300 is not yet stale
    (301, "stale"),
    (4200, "stale"),
])
def test_the_staleness_boundary_is_measured_at_more_than_one_point(age, status):
    """🔴 One measurement is not a claim about a threshold. Measured AT the
    boundary, one second either side, and well outside it — a comparison that
    is off by one is invisible from a single point."""
    got = sm.read_clawgate_queue(reader=_cache(ts=NOW - age), now=NOW)
    assert got["status"] == status
    assert got["cache_age_secs"] == age


def test_a_STALE_cache_still_carries_its_number_but_never_calls_it_current():
    """The count is real, just possibly old — dropping it would lose signal,
    and publishing it as `ok` would fabricate freshness. Both, labelled."""
    got = sm.read_clawgate_queue(reader=_cache(ts=NOW - 9999), now=NOW)
    assert got["status"] == "stale"
    assert got["count"] == 12
    assert "out of date" in got["error"]


@pytest.mark.parametrize("ts", [None, "1786556281", True, [], {}])
def test_a_cache_whose_AGE_cannot_be_established_is_stale_not_ok(ts):
    """🔴 The conservative direction. A count whose freshness is unknowable is
    not a fresh count; calling it `ok` is a guess about the writer."""
    got = sm.read_clawgate_queue(reader=_cache(ts=ts), now=NOW)
    assert got["status"] == "stale"
    assert got["cache_age_secs"] is None
    assert "freshness" in got["error"]


def test_the_detail_string_is_NEVER_parsed_for_a_count():
    """🔴 THE TRUNCATION TRAP, pinned. The cached `detail` names ~6 ids however
    many are pending, and the ids it drops have included `ready_for_review`
    items — finished work awaiting review. So the fixture deliberately
    DISAGREES with itself: 11 pending, 6 named. Anything deriving the number
    from the string reports 6 and loses five tasks silently.
    """
    got = sm.read_clawgate_queue(reader=_legacy_cache(), now=NOW)
    assert got["count"] == 11, "the count is the measurement, not the string"
    assert got["detail"].count("#") == 6, "fixture must actually be truncated"
    assert got["detail_truncated"] is True
    assert "TRUNCATE" in got["detail_note"].upper()


def test_a_schema2_detail_string_STATES_its_own_truncation():
    """🔴 Item 5. The old string named six of eleven with no hint any were
    missing, and the dropped tail has included `ready_for_review` work. The new
    one says `(+N more)` AND the full sets travel structurally beside it."""
    got = sm.read_clawgate_queue(reader=_cache(), now=NOW)
    assert "(+6 more)" in got["detail"]
    assert got["detail_shown"] == 6 and got["detail_total"] == 12
    # the note is gone precisely because the lists are present
    assert got["detail_note"] is None
    assert len(got["open"]) + len(got["ready_for_review"]) + len(got["stuck"]) \
        == got["count"], "the enumerated rows account for the whole count"


def test_a_real_measured_zero_is_distinguishable_from_an_absent_cache():
    """🔴 THE DISCRIMINATING CONTROL. Both are "no pending approvals" to a
    careless reader; only one of them is a measurement, and the renderer must
    not spell them the same."""
    zero = sm.read_clawgate_queue(
        reader=_cache(count=0, pending_count=0, stuck_count=0, open=[],
                      ready_for_review=[], stuck=[], detail=None), now=NOW)
    absent = sm.read_clawgate_queue(reader=lambda p: None, now=NOW)
    assert zero["status"] == "ok" and zero["count"] == 0
    assert absent["status"] == "absent" and absent["count"] is None
    a = "\n".join(sm.render_clawgate_queue(zero))
    b = "\n".join(sm.render_clawgate_queue(absent))
    assert a != b
    assert "a measured zero" in a and "UNMEASURED" not in a
    assert "UNMEASURED" in b and "NOT zero pending" in b


def test_the_clawgate_queue_section_is_rendered_in_EVERY_state():
    """Printed unconditionally, because the failure being closed is a reader
    who never learned the queue exists. A section that appears only sometimes
    is one nobody learns to look for."""
    for reader in (_cache(), _cache(count=0), _cache(ts=NOW - 9999),
                   lambda p: None, lambda p: "{broken"):
        rep = base_gather(clawgate_reader=reader, now=NOW)
        text = sm.render_table(rep)
        assert "▸ CLAWGATE QUEUE" in text
        # 🔴 and the RETIRED title is gone. `▸ BLOCKED ON ME` is the exact
        # string that produced the misread the rename exists to close — a
        # human reading "12 things are blocked on me" off the clawgate queue.
        # Asserting only the new title would pass with both printed.
        assert "BLOCKED ON ME" not in text


def test_the_rendered_queue_points_the_reader_AT_A_LIVE_SURFACE_for_the_full_list():
    """🔴 Unit B's other half. This script has the COUNT, not the enumerated
    queue with titles, so the render must name somewhere to GET that list.

    ⚠ It used to name `agent-ops`, and this test asserted the literal string
    "agent-ops" appeared. That tool is now RETIRED — and the test kept passing
    after its two pointer strings were rewritten, because the word still
    occurred elsewhere in the render. A guard that a stray mention can satisfy
    is the "spelled, not structural" failure: it would have certified a render
    pointing readers at a tool that no longer exists. So assert the CONTRACT
    (the pointer names a surface that is still live) and assert the retired
    name is ABSENT, which is the half that can actually go red.

    ⚠⚠ The FIRST fix was still only HALF structural: `any(live)` was applied to
    the UNMEASURED branch only, and the measured branches asserted nothing but
    the absence of `agent-ops`. Measured: deleting `, or the clawgate API,`
    from `BLOCKED_DETAIL_NOTE` SURVIVED the whole 508-test file, while the same
    deletion in the unmeasured render died. `BLOCKED_DETAIL_NOTE` renders on
    the branch a reader hits when there ARE pending approvals and the cache is
    too old to enumerate them — i.e. the count is real, the `detail` truncates,
    and that note is the ONLY pointer to somewhere the full queue lives. It
    could be stripped with a green suite.

    So the contract is now stated per RENDER, because the three differ in what
    they owe the reader:
      * schema-ok + a count -> the queue is ENUMERATED here; no pointer is owed
        (and asserting one would be asserting a string, not a contract).
      * legacy cache + a count -> truncated detail, no lists: a live surface
        MUST be named. This is the render the surviving mutant lived in.
      * unmeasured -> a live surface MUST be named; that is the whole render.
    """
    live = ("clawgate API", "clawgate pill")
    text = sm.render_table(base_gather(clawgate_reader=_cache(), now=NOW))
    assert "12 task(s) needing you" in text
    assert "agent-ops" not in text, "the render points at a retired tool"
    # what this render owes instead of a pointer: the queue itself, enumerated
    assert "#172 REVIEW finished item" in text, text
    assert "#173 idle 4h" in text, text

    # 🔴 the branch the mutant survived in: a real count whose detail truncates
    legacy = sm.render_table(base_gather(clawgate_reader=_legacy_cache(), now=NOW))
    assert "11 task(s) needing you" in legacy
    assert "agent-ops" not in legacy
    assert any(s in legacy for s in live), legacy

    # and the same when the count could NOT be read — that is exactly when a
    # reader most needs somewhere else to look
    absent = sm.render_table(base_gather(clawgate_reader=lambda p: None))
    assert "agent-ops" not in absent
    assert any(s in absent for s in live), absent


def test_the_summary_carries_the_count_WITH_its_discriminant():
    """The count never travels alone, in the roll-up any consumer reads first."""
    ok = base_gather(clawgate_reader=_cache(), now=NOW)
    assert ok["summary"]["clawgate_queue"] == {"count": 12, "status": "ok",
                                               "stuck_count": 1,
                                               "schema_ok": True}
    gone = base_gather(clawgate_reader=lambda p: None, now=NOW)
    assert gone["summary"]["clawgate_queue"] == {"count": None,
                                                 "status": "absent",
                                                 "stuck_count": None,
                                                 "schema_ok": False}
    # 🔴 And a legacy cache: a real count, but `stuck_count` None — a roll-up
    # reader must be able to tell "no wedged dispatches" from "never looked".
    old = base_gather(clawgate_reader=_legacy_cache(), now=NOW)
    assert old["summary"]["clawgate_queue"] == {"count": 11, "status": "ok",
                                                "stuck_count": None,
                                                "schema_ok": False}


def test_the_blocked_reader_is_resolved_at_CALL_time_not_bound_as_a_default():
    """🔴 The same hole `ch_client_factory` had, on a new seam. A default of
    `clawgate_reader=_read_blocked_text` would capture the ORIGINAL function at
    def time, so the autouse hermeticity raiser would NOT be honoured on the
    one path with no injection point — `main()` -> `gather()` — and the suite
    would quietly read the operator's live queue while staying green."""
    import inspect
    params = inspect.signature(sm.gather).parameters
    assert params["clawgate_reader"].default is None
    assert params["clawgate_path"].default is None
    with pytest.raises(_Forbidden):
        base_gather(clawgate_reader=None)


def test_the_cache_path_is_the_bar_pollers_and_is_not_hardcoded_elsewhere():
    """One constant, so the poller and the reader cannot drift apart."""
    assert sm.BLOCKED_CACHE.endswith("/.cache/bar-status/clawgate.json")
    seen = {}
    sm.read_clawgate_queue(reader=lambda p: seen.setdefault("path", p) and None,
                           now=NOW)
    assert seen["path"] == sm.BLOCKED_CACHE


# =========================================================================== #
# §D — the four small ones
# =========================================================================== #
def test_tail_plain_drops_the_ANSI_flag_at_the_SOURCE():
    """🔴 `--plain` removes tmux's `-e`, it does not post-process. Every
    consumer was re-inventing the same `sed 's/\\x1b\\[[0-9;]*m//g'`, which
    silently leaves behind every escape that is not an SGR. Not emitting them
    is the fix; stripping them is the workaround."""
    assert "-e" in sm.tail_argv("scratch7:3")
    assert "-e" not in sm.tail_argv("scratch7:3", plain=True)
    # everything else about the call is unchanged, so the default caller's
    # bytes do not move under them
    plain = sm.tail_argv("scratch7:3", lines=42, plain=True)
    assert plain == ["tmux", "capture-pane", "-t", "scratch7:3", "-p",
                     "-S", "-42"]


def test_tail_plain_reaches_the_subprocess_and_is_recorded_in_the_json(
        monkeypatch, capsys, absent_blocked_cache):
    """END-TO-END through main(): the flag must change the ARGV, and the JSON
    must say which mode produced `text` — a consumer cannot tell an ANSI-free
    pane from a stripped one by looking at it."""
    calls = []
    monkeypatch.setattr(sm, "local_host_label", lambda *a, **k: "workbench")
    monkeypatch.setattr(sm, "_default_runner",
                        make_runner(calls=calls, local_capture="clean text\n"))
    assert sm.main(["tail", "scratch7:3", "--host", "workbench", "--plain",
                    "--json"]) == sm.EXIT_OK
    blob = json.loads(capsys.readouterr().out)
    assert blob["plain"] is True
    assert "-e" not in calls[-1]

    calls.clear()
    assert sm.main(["tail", "scratch7:3", "--host", "workbench",
                    "--json"]) == sm.EXIT_OK
    blob = json.loads(capsys.readouterr().out)
    assert blob["plain"] is False
    assert "-e" in calls[-1]


def test_fuzzyclaw_is_OFF_by_default_and_publishes_None_not_zero():
    """🔴 Measured 2026-08-12: 29 live of 401 files, 363 stale, 9
    slot-mismatched, and every live row reading `paused` — including a window
    demonstrably running an agent. 29 rows, zero contribution, from a source
    `CLAUDE.md` marks UNTRUSTED. Off by default; the counts stay None so the
    absence is never a measured zero."""
    import inspect
    assert inspect.signature(sm.gather).parameters["use_fuzzyclaw"].default \
        is False
    rep = _REAL_GATHER(hosts=("workbench",), local_host="workbench",
                       runner=make_runner(), use_ch=False, now=NOW,
                       slots={}, clawgate_reader=lambda p: None)
    assert rep["fuzzyclaw"]["status"] == "skipped"
    assert rep["fuzzyclaw"]["files_seen"] is None
    assert rep["summary"]["fuzzyclaw_live"] is None


@pytest.mark.parametrize("argv,expect_on", [
    ([], False),
    (["--fuzzyclaw"], True),
    (["--no-fuzzyclaw"], False),
    (["--fuzzyclaw", "--no-fuzzyclaw"], False),
    (["--no-fuzzyclaw", "--fuzzyclaw"], False),
])
def test_the_fuzzyclaw_flags_compose_with_explicit_OFF_winning(
        monkeypatch, capsys, absent_blocked_cache, argv, expect_on):
    """🔴 `--no-fuzzyclaw` KEEPS WORKING — it now names the default rather than
    changing it, so every existing invocation behaves identically. Passing both
    is not an error; OFF wins and `main` says so, because a silently ignored
    flag is how a caller concludes it was honoured."""
    seen = {}
    monkeypatch.setattr(sm, "local_host_label", lambda *a, **k: "workbench")

    def fake_gather(**kw):
        seen.update(kw)
        return _REAL_GATHER(**dict(kw, runner=make_runner(),
                                   fuzzyclaw_texts=[], slots={},
                                   clawgate_reader=lambda p: None, now=NOW))
    monkeypatch.setattr(sm, "gather", fake_gather)
    sm.main(["scan", "--host", "workbench", "--no-ch", *argv])
    assert seen["use_fuzzyclaw"] is expect_on
    err = capsys.readouterr().err
    assert ("OFF wins" in err) is ("--fuzzyclaw" in argv
                                   and "--no-fuzzyclaw" in argv)


def test_no_capture_flag_reaches_gather(monkeypatch, absent_blocked_cache):
    seen = {}
    monkeypatch.setattr(sm, "local_host_label", lambda *a, **k: "workbench")

    def fake_gather(**kw):
        seen.update(kw)
        return _REAL_GATHER(**dict(kw, runner=make_runner(), slots={},
                                   clawgate_reader=lambda p: None, now=NOW))
    monkeypatch.setattr(sm, "gather", fake_gather)
    sm.main(["scan", "--host", "workbench", "--no-ch", "--no-capture"])
    assert seen["use_capture"] is False
    sm.main(["scan", "--host", "workbench", "--no-ch"])
    assert seen["use_capture"] is True


def test_the_documented_JSON_ROW_PATH_is_where_the_rows_actually_are():
    """🔴 Unit D.3, pinned rather than merely written down. The skill body
    detailed `summary` and never said rows live at `hosts.<host>.windows`,
    which cost a dogfood agent a wasted call. A doc line drifts; an assertion
    does not.
    """
    rep = base_gather()
    for host in ("workbench", "laptop"):
        assert isinstance(rep["hosts"][host]["windows"], list)
    assert rep["hosts"]["workbench"]["windows"], "path must be non-empty here"
    # the path is stated in the module docstring, so the doc and the code are
    # checked against each other and not just against a reader's memory
    assert 'report["hosts"][<"workbench"|"laptop">]["windows"]' in sm.__doc__


def test_the_row_FIELD_LEDGER_fails_when_it_grows_or_shrinks():
    """🔴 Both directions. A field that disappears turns a consumer's read into
    a permanent None; one that appears undocumented is a contract nobody
    reviewed. The docstring enumerates them, so it is the docstring that is
    checked."""
    row = base_gather()["hosts"]["workbench"]["windows"][0]
    expected = {
        "kind",
        "host", "session", "window_index", "window_id", "window_name",
        "codename", "label", "label_source", "hotkey",
        # 🔴 The DERIVED chord, riding with `hotkey` for the same reason
        # `label_source` rides with `label`. The raw key alone left the
        # `v`-vs-`V` case rule in the CONSUMER's head, and a consumer performed
        # it wrong against a live fleet — see `sm.hotkey_display`.
        "hotkey_display",
        "pane_id", "path",
        "command",
        "task", "claude", "busy", "age_secs", "age_source", "status",
        "waiting_probable", "waiting_signals", "waiting_status",
        # The FOURTH signal. It rides the same capture as `waiting` but is a
        # separate pair of fields on purpose — see `UNSENT_STATUSES`.
        "unsent_prompt", "unsent_prompt_status",
        # The captured SCREEN, off unless `--pane-preview`. Present on every row
        # either way — `disabled` is a status, not an absence, so a consumer can
        # tell "not asked" from "asked and not measured". See
        # `PREVIEW_STATUSES`.
        "pane_preview", "pane_preview_status",
        "claude_session_id", "runtime", "ledger", "fuzzyclaw",
        "panes",
    }
    assert set(row) == expected
    for field in expected:
        assert field in sm.__doc__, f"{field} is undocumented in the header"


# =========================================================================== #
# §9 — the agent activity ledger (spec: claudedocs/spec-agent-activity-ledger.md)
#
# 🔴 THE DEFECT THIS SECTION EXISTS FOR. #419 switched fuzzyclaw off on a
# dogfood finding that it "contributes nothing" — true of its `status` field,
# false of the SOURCE. It was also the only supplier of `age_secs`, of the
# `stale` bucket derived from it, and of the `claude_session_id` the ClickHouse
# join needs. Measured on the workbench 2026-08-12, the SHIPPED default view:
# `rows with an age: 0`, `claude_session_id` on 0 rows, no `stale` bucket at all.
# Nothing in the output said so, which is why it survived weeks of green scans.
# =========================================================================== #
LEDGER_PID = "4025325"           # the workbench's tmux server, per the fixture
LEDGER_PID_LAPTOP = "3737"       # a DIFFERENT one — hosts must not share a pid


def led_rec(window_id="@41", session_id="ledger-sess-alpha", ago=600,
            tmux_pid=LEDGER_PID, runtime="claude", pane_id="%11", **kw):
    """A ledger record, aged `ago` seconds before the suite's fixed NOW.

    `pane_id` is the FILE key and `window_id` the JOIN key — two different jobs,
    given two distinct values here so a mutant that confuses them cannot satisfy
    an assertion by accident.
    """
    return sm.AL.build_record(
        runtime=runtime, session_id=session_id,
        last_activity_ts=sm.AL.now_iso(NOW - ago),
        window_id=window_id, pane_id=pane_id, tmux_pid=tmux_pid, **kw)


def led_out(*records, pid=LEDGER_PID):
    """The bytes a host's ledger read actually returns: the sentinel line
    carrying the tmux server pid, then one JSON line per record."""
    head = "%s %s" % (sm.AL.SENTINEL, pid) if pid else sm.AL.SENTINEL
    return "\n".join([head] + [json.dumps(r) for r in records]) + "\n"


def ledger_gather(workbench=(), laptop=(), wb_pid=LEDGER_PID,
                  lt_pid=LEDGER_PID_LAPTOP, **kw):
    """`base_gather` with the ledger ON and both hosts' output injected."""
    outputs = {"workbench": led_out(*workbench, pid=wb_pid),
               "laptop": led_out(*laptop, pid=lt_pid)}
    outputs.update(kw.pop("ledger_outputs", {}))
    return base_gather(use_ledger=True, ledger_outputs=outputs, **kw)


def lean_of(report):
    """The lean projection, via the SHIPPING function."""
    return sm.lean_report(report)


def rows_of(report):
    return [r for h in report["hosts"].values() for r in h["windows"]]


def test_the_ledger_is_ON_by_default_in_gathers_signature():
    """🔴 The shared fixture pins it OFF so the pre-existing assertions keep
    their meaning, which leaves the SHIPPED default unasserted by every one of
    them. This is that assertion. KILLS: flipping the default to False, which
    would silently restore the #419 view while every other test stayed green."""
    import inspect
    sig = inspect.signature(sm.gather)
    assert sig.parameters["use_ledger"].default is True
    assert "--no-ledger" in sm.build_parser().format_help()


def test_THE_REGRESSION_the_ledger_restores_age_session_id_and_stale():
    """🔴 THE HEADLINE TEST, reported as a PAIR: the same fixture with the ledger
    off, then on. The 'off' half is not decoration — a reader that returns 0 ages
    is indistinguishable from one wired to nothing, so the 'on' number means
    something only beside a control that moved.

    KILLS: dropping the ledger join in `fold_windows`; sourcing `age_secs` from
    fuzzyclaw only; leaving `claude_session_id` on the fuzzyclaw field.
    """
    # --- the control: exactly what shipped after #419 ---------------------
    off = base_gather(use_ledger=False, use_fuzzyclaw=False)
    assert off["summary"]["rows_with_age"] == 0
    assert off["summary"]["rows_with_session_id"] == 0
    assert off["summary"]["status"]["stale"]["total"] == 0
    assert all(r["age_source"] is None for r in rows_of(off))

    # --- the same scan with the ledger on ---------------------------------
    on = ledger_gather(
        workbench=[led_rec(window_id="@41", ago=600)],
        laptop=[led_rec(window_id="@7", session_id="ledger-sess-laptop",
                        ago=9000, tmux_pid=LEDGER_PID_LAPTOP)],
        use_fuzzyclaw=False)
    assert on["summary"]["rows_with_age"] == 2
    assert on["summary"]["rows_with_session_id"] == 2
    # 3 rows in this fixture: 2 joined to a ledger record, 1 (the bare shell in
    # `misc:5`) with no writer at all — and `none` is the literal string, never
    # a null key.
    assert on["summary"]["age_sources"] == {"ledger": 2, "none": 1}

    wb = next(r for r in rows_of(on) if r["window_id"] == "@41")
    assert wb["age_secs"] == 600.0 and wb["age_source"] == "ledger"
    assert wb["claude_session_id"] == "ledger-sess-alpha"
    assert wb["status"] == "busy"

    # ...and `stale` is a bucket again, because there is an age to derive it
    # from. 9000s > the 3600s threshold, and `stale` WINS over the glyph.
    lt = next(r for r in rows_of(on) if r["window_id"] == "@7")
    assert lt["age_secs"] == 9000.0 and lt["status"] == "stale"
    assert on["summary"]["status"]["stale"]["claude"] == 1


def test_a_REMOTE_row_gets_an_age_which_fuzzyclaw_structurally_could_not_give_it():
    """🔴 THE STRUCTURAL WIN, pinned. fuzzyclaw task files are LOCAL state, so
    every remote row carried a null age by construction — the tool's own caveat
    says so. The ledger is read per host over the same SSH transport, so a laptop
    row is not a second-class row. KILLS: reading the ledger once and reusing the
    local host's index for every host.
    """
    rep = ledger_gather(laptop=[led_rec(window_id="@7", ago=120,
                                        tmux_pid=LEDGER_PID_LAPTOP)],
                        use_fuzzyclaw=False)
    lt = next(r for r in rows_of(rep) if r["host"] == "laptop")
    assert lt["age_secs"] == 120.0 and lt["age_source"] == "ledger"
    # ...and the workbench, whose ledger answered with NO records, is a measured
    # zero rather than an error.
    assert rep["ledger"]["hosts"]["workbench"]["seen"] == 0
    assert rep["ledger"]["hosts"]["workbench"]["status"] == "ok"


def test_the_ledger_read_goes_to_EVERY_host_not_just_the_local_one():
    """The transport claim behind the test above, at the argv level: two hosts,
    two reads, and the remote one is wrapped for SSH."""
    calls = []
    base_gather(use_ledger=True, runner=make_runner(calls=calls))
    ledger_calls = [c for c in calls if sm.AL.SENTINEL in " ".join(c)]
    assert len(ledger_calls) == 2, ledger_calls
    assert sum(1 for c in ledger_calls if c[0] == "ssh") == 1


def test_the_ledger_WINS_over_fuzzyclaw_when_both_describe_one_window():
    """🔴 Precedence, not a merge. Both writers are live during the supersede
    phase (spec §6) and they can disagree; two ages averaged or picked by
    recency would be a number neither writer ever measured.

    Pinned on BOTH fields, because `claude_session_id` is the one carrier of the
    session id into the ClickHouse join — a stale fuzzyclaw value winning here
    resolves a live window's history to a session that ended days ago.
    """
    rep = ledger_gather(workbench=[led_rec(window_id="@41", ago=600)],
                        use_fuzzyclaw=True)
    row = next(r for r in rows_of(rep) if r["window_id"] == "@41")
    assert row["age_source"] == "ledger"
    assert row["age_secs"] == 600.0          # NOT fuzzyclaw's 1800.0
    assert row["claude_session_id"] == "ledger-sess-alpha"
    # ...and the fuzzyclaw record is still CARRIED, not deleted: the supersede
    # phase keeps both visible so a disagreement can be seen rather than
    # resolved in silence.
    assert row["fuzzyclaw"]["claude_session"].startswith("11111111")
    assert row["ledger"]["session_id"] == "ledger-sess-alpha"


def test_fuzzyclaw_still_answers_for_a_window_the_ledger_has_never_seen():
    """The fallback half of the precedence above. KILLS: making the ledger
    authoritative for the ABSENCE of a record — during the supersede phase a
    window whose agent predates the hook has no ledger record and must not lose
    the age it already had."""
    rep = ledger_gather(workbench=[], use_fuzzyclaw=True)
    row = next(r for r in rows_of(rep) if r["window_id"] == "@41")
    assert row["age_source"] == "fuzzyclaw" and row["age_secs"] == 1800.0


def test_a_record_from_an_OLDER_TMUX_SERVER_never_reaches_a_row():
    """🔴 THE GENERATION GUARD, end-to-end. After a reboot tmux window ids
    restart at `@0`, so yesterday's `@41` record and today's `@41` window
    collide — and `tmux-task-resume.sh` rebuilding the workspace is exactly when
    that happens. Without the pid check the fresh window inherits a dead
    session's id and a multi-day age.

    KILLS: joining on `window_id` alone; comparing the pid as an int against a
    str; treating a mismatch as `not_live` (which would report the wrong reason).
    """
    rep = ledger_gather(
        workbench=[led_rec(window_id="@41", tmux_pid="999999", ago=300)],
        use_fuzzyclaw=False)
    row = next(r for r in rows_of(rep) if r["window_id"] == "@41")
    assert row["age_secs"] is None and row["ledger"] is None
    assert row["claude_session_id"] is None
    wb = rep["ledger"]["hosts"]["workbench"]
    assert wb["generation_mismatch"] == 1 and wb["not_live"] == 0
    assert wb["live"] == 0


def test_a_record_for_a_DEAD_window_never_reaches_a_row():
    """The other rejection, kept distinct from the one above so the report says
    WHICH happened."""
    rep = ledger_gather(workbench=[led_rec(window_id="@998")],
                        use_fuzzyclaw=False)
    assert all(r["ledger"] is None for r in rows_of(rep))
    assert rep["ledger"]["hosts"]["workbench"]["not_live"] == 1
    assert rep["ledger"]["hosts"]["workbench"]["generation_mismatch"] == 0


def test_a_host_whose_ledger_read_FAILED_is_partial_and_says_which_host():
    """🔴 A total summed across a host that never answered counts it as
    contributing zero records — the same lie as `files_live: 0` under
    `--no-fuzzyclaw`. `partial` is its own status, and the other host's rows are
    unaffected. KILLS: folding a failed host into `ok`; zero-filling its counts.
    """
    rep = ledger_gather(laptop=[led_rec(window_id="@7",
                                        tmux_pid=LEDGER_PID_LAPTOP, ago=60)],
                        use_fuzzyclaw=False,
                        ledger_outputs={"workbench": None})
    assert rep["ledger"]["status"] == "partial"
    assert "workbench" in rep["ledger"]["error"]
    assert rep["ledger"]["hosts"]["workbench"]["status"] == "error"
    assert rep["ledger"]["hosts"]["workbench"]["seen"] is None
    # the laptop still measured, and its row still got its age
    assert rep["ledger"]["records_live"] == 1
    lt = next(r for r in rows_of(rep) if r["host"] == "laptop")
    assert lt["age_secs"] == 60.0


def test_a_host_that_answered_NEITHER_tmux_call_is_not_asked_a_third_time():
    """The skip exists so an offline laptop does not cost a third
    `ConnectTimeout=4` on every scan. It must remain an ERROR — nothing was
    measured — and the reason must say the read was never ATTEMPTED, which is a
    different fact about the host from "tried and failed".

    KILLS: reporting the skipped host as `ok` (a fabricated `0 live of 0` for a
    machine that is down — the exact class this module refuses), and KILLS:
    reporting it as `no_sentinel`, which would blame the protocol for a host
    that was never asked.
    """
    calls = []
    rep = base_gather(use_ledger=True, use_fuzzyclaw=False,
                      runner=make_runner(calls=calls, remote_rc=1,
                                         remote_err="ssh: connect timed out"))
    lt = rep["ledger"]["hosts"]["laptop"]
    assert lt["status"] == "error"
    assert "not attempted" in lt["error"]
    for key in ("seen", "live", "not_live", "generation_mismatch"):
        assert lt[key] is None, key
    # the THIRD command was never issued to that host...
    ledger_calls = [c for c in calls if c and sm.AL.SENTINEL in " ".join(c)]
    assert [c for c in ledger_calls if c[0] == "ssh"] == [], ledger_calls
    # ...and the REACHABLE host was still asked, so this is a per-host skip and
    # not a switch that turned the whole feature off. Asserted on the ARGV
    # rather than on the status: `make_runner` answers pane text for any argv it
    # does not recognise, so the local read honestly comes back `no_sentinel`
    # here — which is the fixture's answer, not the host's.
    assert len([c for c in ledger_calls if c[0] != "ssh"]) == 1, ledger_calls


def test_a_host_that_answered_EITHER_tmux_call_is_still_read():
    """🔴 The OTHER direction of the skip, and it was unpinned: only
    `not wins_res["reachable"]` was killable, so `not panes_res["reachable"]`
    could be dropped from the conjunction with the suite green — and the ledger
    would then be skipped for a host whose `list-windows` demonstrably answered.

    The guard's own justification is "answered NEITHER tmux call", so both
    halves have to be asserted or the sentence is only half true. Both
    asymmetric cases here, because a conjunction pinned at one operand is not
    pinned.
    """
    for kw in ({"local_rc": 1, "local_err": "panes blew up",
                "local_windows_rc": 0},
               {"local_windows_rc": 1, "local_windows_err": "windows blew up"}):
        calls = []
        base_gather(use_ledger=True, use_fuzzyclaw=False,
                    runner=make_runner(calls=calls, **kw))
        local_ledger = [c for c in calls
                        if c and c[0] != "ssh"
                        and sm.AL.SENTINEL in " ".join(c)]
        assert len(local_ledger) == 1, (kw, local_ledger)


def test_a_host_with_NO_TMUX_SERVER_is_still_read():
    """🔴 `no_server` is REACHABLE with empty output — the host answered, it
    simply has no tmux running. It must NOT be caught by the skip above: a
    machine with a dead tmux server can still hold a ledger (records survive the
    server that produced them), and skipping it would publish `error` for a host
    that was perfectly able to answer.
    """
    rep = base_gather(
        use_ledger=True, use_fuzzyclaw=False,
        ledger_outputs={"workbench": led_out(pid=None),
                        "laptop": led_out(pid=None)},
        runner=make_runner(remote_rc=1, remote_err="no server running on /tmp/x",
                           local_rc=1, local_err="no server running on /tmp/x"))
    for host in ("workbench", "laptop"):
        assert rep["hosts"][host]["reachable"] is True, host
        assert rep["ledger"]["hosts"][host]["status"] == "ok", host
        assert rep["ledger"]["hosts"][host]["seen"] == 0, host


def test_output_without_the_SENTINEL_is_NO_SENTINEL_not_zero_records():
    """🔴 THE FABRICATED ZERO. Empty stdout from a swallowed command and a host
    with no records are the same bytes. KILLS: reading `records: []` off a read
    whose protocol never confirmed it ran."""
    rep = ledger_gather(use_fuzzyclaw=False,
                        ledger_outputs={"workbench": "garbage\n"})
    wb = rep["ledger"]["hosts"]["workbench"]
    assert wb["status"] == "no_sentinel"
    assert wb["seen"] is None and wb["live"] is None
    assert rep["ledger"]["status"] == "partial"


def test_a_host_whose_WINDOW_LIST_is_unmeasured_joins_nothing_and_says_so():
    """The ledger read succeeded and the intersection could not run. Records
    exist, none is joinable, and every count that would describe the join is
    None rather than 0."""
    rep = ledger_gather(workbench=[led_rec(window_id="@41")],
                        use_fuzzyclaw=False,
                        runner=make_runner(local_windows_rc=1,
                                           local_windows_err="boom"))
    wb = rep["ledger"]["hosts"]["workbench"]
    assert wb["status"] == "unmeasured"
    assert wb["seen"] == 1 and wb["live"] is None
    assert "list-windows" in wb["error"]
    assert all(r["age_source"] is None for r in rows_of(rep)
               if r["host"] == "workbench")


def test_no_ledger_publishes_NULLS_not_zeroes():
    """🔴 The same rule `--no-fuzzyclaw` already obeys: under `--no-ledger` the
    directory is never read, so `records_live: 0` would be a fabricated
    measurement. `status: skipped` discriminates it, but a discriminated lie is
    still a lie in the count."""
    rep = base_gather(use_ledger=False)
    assert rep["ledger"]["status"] == "skipped"
    for key in ("records_seen", "records_live", "records_unparseable"):
        assert rep["ledger"][key] is None, key
    assert rep["ledger"]["hosts"] == {}


def test_the_ledger_section_is_in_the_report_even_when_it_was_skipped():
    """The skeleton is present from the first byte, so a consumer branches on a
    field rather than on whether a key exists."""
    assert "ledger" in base_gather(use_ledger=False)
    assert "ledger" in ledger_gather()


def test_a_record_from_the_FUTURE_clamps_to_zero_rather_than_going_negative():
    """🔴 The clamp is load-bearing and was untested. A record whose timestamp is
    ahead of this host's clock yields a NEGATIVE age, and `classify_status` reads
    `age >= threshold` — so a negative age silently passes as fresh, and worse,
    `AGE` renders as a negative duration.

    Cross-host clock skew only became REACHABLE because the ledger is the first
    source of remote ages: the record is written on the laptop and the arithmetic
    happens here. Measured at two points — 300s ahead and exactly now — because
    one point is not a claim about a clamp.

    KILLS: dropping `max(0.0, …)` from either the ledger or the fuzzyclaw branch.
    """
    ahead = ledger_gather(
        laptop=[led_rec(window_id="@7", ago=-300, tmux_pid=LEDGER_PID_LAPTOP)],
        use_fuzzyclaw=False)
    row = next(r for r in rows_of(ahead) if r["host"] == "laptop")
    assert row["age_secs"] == 0.0, "a future record must clamp, not go negative"
    assert row["age_source"] == "ledger"

    exact = ledger_gather(
        laptop=[led_rec(window_id="@7", ago=0, tmux_pid=LEDGER_PID_LAPTOP)],
        use_fuzzyclaw=False)
    assert next(r for r in rows_of(exact)
                if r["host"] == "laptop")["age_secs"] == 0.0


def test_a_FUZZYCLAW_task_from_the_future_clamps_too():
    """The same clamp on the other branch — pinned separately, because a mutant
    that drops only one of the two would otherwise be caught by neither."""
    future = dict(TASK_LIVE, last_activity="2026-08-11T12:05:00+00:00")
    rep = base_gather(use_fuzzyclaw=True,
                      fuzzyclaw_texts=[json.dumps(future)])
    row = next(r for r in rows_of(rep) if r["window_id"] == "@41")
    assert row["age_secs"] == 0.0 and row["age_source"] == "fuzzyclaw"


def test_a_generation_that_could_not_be_CHECKED_is_kept_and_declared():
    """🔴 KEPT, and visible. A host with no tmux server prints the sentinel with
    no pid; those records are being TRUSTED, and the count says how many live
    rows rest on that. KILLS: counting them as verified, and KILLS: dropping
    them."""
    rep = ledger_gather(workbench=[led_rec(window_id="@41", tmux_pid=None)],
                        wb_pid=None, use_fuzzyclaw=False)
    wb = rep["ledger"]["hosts"]["workbench"]
    assert wb["live"] == 1 and wb["generation_unchecked"] == 1
    assert wb["tmux_pid"] is None
    row = next(r for r in rows_of(rep) if r["window_id"] == "@41")
    assert row["age_source"] == "ledger"


# --------------------------------------------------------------------------- #
# §9.1 — the ledger in the rendered table
# --------------------------------------------------------------------------- #
def test_the_table_states_how_many_rows_have_an_age_and_from_which_writer():
    """🔴 The line that would have made #419 visible on the day it shipped. A
    `stale=0+0` bucket means one of two completely different things — nothing is
    stale, or nothing has an AGE — and the by-status line cannot tell them
    apart. KILLS: rendering the summary without the ages line."""
    text = sm.render_table(ledger_gather(
        workbench=[led_rec(window_id="@41", ago=600)], use_fuzzyclaw=False))
    assert "ages: 1 of 3 row(s) have one" in text
    assert "ledger=1" in text and "none=2" in text
    assert "session ids: 1" in text


def test_the_table_reports_a_host_that_did_not_answer_as_such():
    """KILLS: rendering a failed read as an empty section — the exact shape the
    ClickHouse section is already forbidden from taking."""
    text = sm.render_table(ledger_gather(
        use_fuzzyclaw=False, ledger_outputs={"workbench": None}))
    assert "⚠ PARTIAL" in text
    assert "workbench  ERROR" in text
    # ...and the reason travels with it, so the operator is not left to guess
    # whether the host is down or the read is broken.
    assert "no injected ledger output" in text


def test_a_ledger_CONFLICT_reaches_the_REPORT_through_gather():
    """🔴 THE SEAM, not the function. `index_by_window` detecting a conflict is
    pinned at the pure-function level, and that proves nothing about whether the
    finding survives the trip through `gather` into the payload — the exact gap a
    prior round already found and closed for fuzzyclaw's identically-shaped
    `slot_conflicts` path, which this one was written without.

    The co-tenancy fix's whole claim is that contention "is now visible instead
    of being decided by write order". Delete the aggregation and it silently is
    not, while the window still resolves to one arbitrary agent of two.

    KILLS: `report["ledger"]["conflicts"] = []`.
    """
    rep = ledger_gather(
        workbench=[led_rec(pane_id="%11", session_id="agent-a", ago=900),
                   led_rec(pane_id="%12", session_id="agent-b", ago=120)],
        use_fuzzyclaw=False)
    conflicts = rep["ledger"]["conflicts"]
    assert len(conflicts) == 1, conflicts
    assert conflicts[0]["window_id"] == "@41"
    assert conflicts[0]["claimants"] == 2
    assert conflicts[0]["session_ids"] == ["agent-a", "agent-b"]
    # the host travels WITH the conflict — two hosts can each have one
    assert conflicts[0]["host"] == "workbench"
    # ...and the window still gets exactly one row, resolved to the NEWEST
    row = next(r for r in rows_of(rep) if r["window_id"] == "@41")
    assert row["claude_session_id"] == "agent-b" and row["age_secs"] == 120.0


def test_a_ledger_CONFLICT_is_PRINTED_not_only_carried():
    """The other half of the seam. A `--json` consumer and a human reader must
    both be told; the plain-text reader is the one who would otherwise see a
    single confident row. KILLS: dropping the conflict loop from
    `render_ledger`."""
    text = sm.render_table(ledger_gather(
        workbench=[led_rec(pane_id="%11", session_id="agent-a"),
                   led_rec(pane_id="%12", session_id="agent-b")],
        use_fuzzyclaw=False))
    assert "⚠ LEDGER CONFLICT" in text
    assert "@41" in text and "claimed by 2 record(s)" in text
    assert "agent-a" in text and "agent-b" in text


def test_no_conflict_prints_NOTHING_so_the_warning_stays_meaningful():
    """The negative control: a warning that appears on every scan is one a
    reader learns to skip."""
    text = sm.render_table(ledger_gather(
        workbench=[led_rec(pane_id="%11")], use_fuzzyclaw=False))
    assert "LEDGER CONFLICT" not in text


def test_the_table_survives_a_skipped_ledger():
    text = sm.render_table(base_gather(use_ledger=False))
    assert "AGENT LEDGER (skipped" in text
    assert "null, not 0" in text


def test_the_table_names_the_rejections_and_the_unverified_separately():
    """🔴 `generation_unchecked` is NOT a rejection — those records were kept.
    Rendering them inside the rejected list would tell an operator that records
    were dropped when they were in fact trusted."""
    text = sm.render_table(ledger_gather(
        workbench=[led_rec(window_id="@998"),
                   led_rec(window_id="@41", tmux_pid="999")],
        use_fuzzyclaw=False))
    assert "rejected:" in text
    assert "1 window gone" in text and "1 older tmux server" in text
    assert "unverified generation" not in text


# --------------------------------------------------------------------------- #
# §10 — the output is priced for an AGENT, because that is the only consumer
# --------------------------------------------------------------------------- #
def test_the_json_is_COMPACT_because_the_only_consumer_pays_by_the_token(
        monkeypatch, capsys, absent_blocked_cache):
    """🔴 MEASURED, and the measurement is the whole argument. This tool has 0
    interactive shell invocations in 30 days against 55 agent references (spec
    §7), confirmed by the operator 2026-08-14: an AGENT is the only consumer,
    and an agent pays for output by the token.

    Indented at 2 spaces, a 75-row scan emitted 86,066 B against 60,631 B
    compact — 29% of every payload was whitespace for a reader who does not
    exist, about 6,400 tokens per call.

    KILLS: restoring `indent=2` (or any indent) at any of the three emit sites.
    Asserted as a PROPERTY of the bytes rather than by grepping the source for
    `indent`, so a differently-spelled reformat is caught too.
    """
    monkeypatch.setattr(sm, "local_host_label", lambda *a, **k: "workbench")
    monkeypatch.setattr(sm, "_default_runner", make_runner())
    monkeypatch.setattr(sm, "read_fuzzyclaw_texts", lambda *a, **k: [])
    sm.main(["scan", "--no-ch", "--no-ledger", "--host", "workbench", "--json"])
    out = capsys.readouterr().out

    # it parses...
    parsed = json.loads(out)
    assert parsed["local_host"] == "workbench"
    # ...on ONE line, and with no run of spaces an indent would produce
    assert out.count("\n") == 1, "the payload is not a single line"
    assert '": ' not in out, "a key/value separator carries indent padding"
    assert "  " not in out, "the payload carries multi-space padding"

    # ...and the compaction is real, not cosmetic: re-encoding it indented is
    # measurably larger, which is the cost this refuses to pay.
    indented = json.dumps(parsed, sort_keys=True, indent=2, default=str)
    assert len(indented) > len(out.strip()) * 1.2, (
        "compaction saved less than 20% — the claim in the source comment "
        "no longer holds for this payload shape")


def test_sort_keys_SURVIVES_the_compaction():
    """🔴 The half that must NOT be dropped while trimming bytes. `sort_keys`
    costs nothing and is what makes two scans diffable — an agent comparing a
    before/after would otherwise see key reordering as change."""
    report = base_gather()
    blob = json.dumps(report, sort_keys=True, default=str,
                      separators=(",", ":"))
    top = [k.strip('"') for k in
           __import__("re").findall(r'"([a-z_]+)":', blob)[:4]]
    assert top == sorted(top), top


# --------------------------------------------------------------------------- #
# §10.1 — the LEAN view
# --------------------------------------------------------------------------- #
def test_the_LEAN_row_field_ledger_fails_when_it_grows_or_shrinks():
    """🔴 Both directions, same rule as the full row ledger. A field that
    vanishes turns a consumer's read into a permanent absence; one that appears
    is a contract nobody reviewed — and here it also silently re-inflates the
    payload the view exists to shrink."""
    rep = lean_of(ledger_gather(workbench=[led_rec(window_id="@41", ago=600)],
                                use_fuzzyclaw=False))
    row = rep["hosts"]["workbench"]["windows"][0]
    assert set(row) == set(sm.LEAN_ROW_FIELDS)
    assert set(sm.LEAN_ROW_FIELDS) == {
        "kind",
        "host", "session", "window_index", "label", "label_source", "hotkey",
        # 🔴 The chord, derived ONCE. This is the AGENT-shaped view, and the
        # agent is exactly the reader that rendered `hotkey: v` as
        # `Alt+Shift+V`; dropping it here puts the derivation back where it
        # already failed.
        "hotkey_display",
        "path", "task", "runtime", "claude", "busy", "status", "age_secs",
        "age_source",
        "waiting_probable", "waiting_signals", "waiting_status",
        # 🔴 The TEXT, not just a flag. The lean view's sole consumer is an
        # agent triaging without opening panes, and a boolean "something is
        # parked here" costs it a `tail` per row — the LOSSY-table failure this
        # view replaces. Its status rides with it for the same reason
        # `waiting_status` does: it is what makes the null readable.
        "unsent_prompt", "unsent_prompt_status",
        "claude_session_id",
    }
    # 🔴 `label_source` is here because an audit caught the view's own rule
    # contradicting its field list: "duplication and human-facing identity go,
    # a measurement's PROVENANCE never does" — and `label_source` is provenance,
    # the same shape as `age_source` two entries along. Dropping it made a row
    # labelled from a real directory indistinguishable from one labelled
    # because the cwd yielded nothing. The rule won over the list.
    assert "label_source" in sm.LEAN_ROW_FIELDS
    assert "age_source" in sm.LEAN_ROW_FIELDS


def test_the_lean_view_keeps_EVERY_null_vs_zero_discriminator():
    """🔴 THE CONSTRAINT THAT SHAPES THIS VIEW. A cheap payload that can LIE is
    worse than an expensive one. Trimming is allowed to remove duplication and
    human-facing identity; it is never allowed to remove a measurement's
    provenance, because then a consumer cannot tell a measured zero from a
    measurement nobody took — which is the defect this whole tool exists to
    refuse.

    KILLS: dropping `caveats` (the cheapest-looking cut, ~586 tokens, and the
    one thing standing between a cold agent and a fabricated zero), dropping
    `summary.waiting`'s tri-state, or trimming a host down to its rows.
    """
    full = ledger_gather(use_fuzzyclaw=False,
                         ledger_outputs={"workbench": None})
    lean = lean_of(full)

    for key in ("summary", "caveats", "clawgate_queue", "ledger", "fuzzyclaw",
                "filters", "local_host", "ts"):
        assert key in lean, key
    # the caveats survive IN FULL, not as a pointer to somewhere else
    assert lean["caveats"] == full["caveats"]
    # the waiting tri-state and its reasons
    assert lean["summary"]["waiting"] == full["summary"]["waiting"]
    # ...and the ledger's per-host failure is still legible
    assert lean["ledger"]["hosts"]["workbench"]["status"] == "error"
    for host in ("workbench", "laptop"):
        h = lean["hosts"][host]
        for key in ("reachable", "windows_measured", "captures_status"):
            assert key in h, (host, key)


def test_the_lean_view_is_LOSSLESS_on_every_field_it_keeps():
    """🔴 The property the TABLE cannot offer at any price. The table truncates
    task text at 25 characters with no way for the reader to recover it —
    measured, 45 of 75 rows on a live scan. Lean passes values through
    untouched, so it is cheaper than the full payload AND more faithful than
    the cheap one."""
    full = ledger_gather(workbench=[led_rec(window_id="@41", ago=600)],
                         use_fuzzyclaw=False)
    lean = lean_of(full)
    # 🔴 PAIRED BY IDENTITY AND COUNTED, because `zip` alone is length-blind: an
    # audit's mutant that dropped the LAST row of every host (`[:-1]`) survived
    # all 456 tests, since zip truncates to the shorter list and nothing
    # compared counts. This tool's headline question is "is anything waiting on
    # me"; a projection that silently drops a window hides exactly the row being
    # looked for.
    total_full = sum(len(h["windows"]) for h in full["hosts"].values())
    total_lean = sum(len(h["windows"]) for h in lean["hosts"].values())
    assert total_lean == total_full == full["summary"]["total_sessions"]
    for host in full["hosts"]:
        fulls = {(r["session"], r["window_index"]): r
                 for r in full["hosts"][host]["windows"]}
        leans = {(r["session"], r["window_index"]): r
                 for r in lean["hosts"][host]["windows"]}
        assert set(leans) == set(fulls), host
        for key, lr in leans.items():
            for k in sm.LEAN_ROW_FIELDS:
                assert lr[k] == fulls[key][k], (host, key, k)


def test_a_long_task_survives_the_lean_view_UNTRUNCATED():
    """The lossless claim, made concrete against the failure it replaces: a task
    longer than the table's 25-char column comes back whole."""
    long_task = "OC | a task title comfortably past the table's column width"
    panes = WORKBENCH_PANES.replace("Working on alpha", long_task)
    lean = lean_of(base_gather(use_ledger=False,
                               runner=make_runner(local_panes=panes)))
    tasks = [r["task"] for r in lean["hosts"]["workbench"]["windows"]]
    assert any(long_task in t for t in tasks), tasks
    assert not any("…" in t for t in tasks), tasks


def test_the_lean_view_drops_the_sub_objects_that_DUPLICATE_row_fields():
    """🔴 `ledger` was the single biggest row field at 7,039 B of 60,631 B — a
    full embedded record whose useful contents are already flat on the row, and
    it was added in the same change that added the duplication. KILLS: putting
    either sub-object back."""
    lean = lean_of(ledger_gather(workbench=[led_rec(window_id="@41")],
                                 use_fuzzyclaw=True))
    row = lean["hosts"]["workbench"]["windows"][0]
    assert "ledger" not in row and "fuzzyclaw" not in row
    # ...but everything the dropped record CARRIED is still on the row
    assert row["claude_session_id"] and row["age_secs"] is not None
    assert row["age_source"] == "ledger"


def test_the_payload_NAMES_the_fields_it_CARRIES():
    """🔴 Without this, a consumer that finds no `window_id` cannot tell "this
    view omits it" from "this scan measured it as null" — which reintroduces
    exactly the ambiguity the rest of this tool removes. The list travels IN the
    payload and must match the rows it describes."""
    lean = lean_of(base_gather(use_ledger=False))
    assert lean["view"] == "lean"
    assert lean["lean_row_fields"] == list(sm.LEAN_ROW_FIELDS)
    row = lean["hosts"]["workbench"]["windows"][0]
    assert set(row) == set(lean["lean_row_fields"])


def test_the_lean_view_shrinks_the_ROWS_which_is_where_the_payload_lives():
    """🔴 ASSERTED ON THE ROWS, not on the whole payload, and the first draft of
    this test got that wrong. Rows are 86% of a real payload (52,564 B of
    60,631 B on a 75-row scan) and are the only part lean trims — the fixed
    sections (`caveats`, `clawgate_queue`, `summary`) are kept ON PURPOSE.

    So whole-payload saving SCALES WITH ROW COUNT: 36% measured live at 75
    rows, but only ~12% on this 3-row fixture, where the retained fixed cost
    dominates. A ratio asserted against the whole payload therefore encodes the
    fixture's size rather than the view's behaviour, and would have to be
    re-tuned every time the fixture changed — indistinguishable from silencing
    it. The row-section ratio is the claim that is actually true at any size.

    A property, not a literal byte count, for the same reason.
    """
    full = ledger_gather(workbench=[led_rec(window_id="@41")],
                         use_fuzzyclaw=True)
    enc = lambda o: json.dumps(o, sort_keys=True, default=str,
                               separators=(",", ":"))
    lean = lean_of(full)

    def rowbytes(rep):
        return sum(len(enc(r)) for h in rep["hosts"].values()
                   for r in h["windows"])

    assert rowbytes(lean) < rowbytes(full) * 0.7, (rowbytes(lean),
                                                   rowbytes(full))
    # ...and the retained fixed sections really are byte-identical, so the
    # saving above came from trimming rows and nothing else.
    for key in ("caveats", "summary", "clawgate_queue"):
        assert enc(lean[key]) == enc(full[key]), key


def test_the_FULL_view_is_untouched_when_lean_is_not_asked_for():
    """The flag must not leak into the default payload."""
    full = base_gather(use_ledger=False)
    assert "view" not in full and "lean_row_fields" not in full
    assert "ledger" in full["hosts"]["workbench"]["windows"][0]
    assert "live_window_ids" in full["hosts"]["workbench"]


def test_lean_without_json_says_so_rather_than_being_ignored(
        monkeypatch, capsys, absent_blocked_cache):
    """A silently ignored flag is how a caller concludes it was honoured — the
    same rule `--plain` and `--claude-only` already follow."""
    monkeypatch.setattr(sm, "local_host_label", lambda *a, **k: "workbench")
    monkeypatch.setattr(sm, "_default_runner", make_runner())
    monkeypatch.setattr(sm, "read_fuzzyclaw_texts", lambda *a, **k: [])
    sm.main(["scan", "--no-ch", "--no-ledger", "--host", "workbench", "--lean"])
    cap = capsys.readouterr()
    assert "--lean" in cap.err and "no effect without" in cap.err
    # ...and it printed the TABLE, not a lean payload
    assert "TMUX WINDOWS" in cap.out


def test_the_LEAN_HOST_field_ledger_fails_when_it_grows_or_shrinks():
    """🔴 The host list carries most of this view's measurement-provenance
    promise, and it had NO ledger while the row list had one. An audit's
    mutants dropping `captures_seen`, `captures_measured`, `windows_error` or
    `error` — and one RE-ADDING `live_window_ids`/`ssh_target`, silently
    re-inflating the payload — all survived the suite.

    Both directions, and the payload's own `lean_host_fields` must agree with
    what the entries actually carry.
    """
    lean = lean_of(base_gather(use_ledger=False))
    assert set(sm.LEAN_HOST_FIELDS) == {
        "reachable", "error", "windows_measured", "windows_error",
        "captures_measured", "captures_status", "captures_seen",
    }
    assert lean["lean_host_fields"] == list(sm.LEAN_HOST_FIELDS)
    for host, h in lean["hosts"].items():
        assert set(h) == set(sm.LEAN_HOST_FIELDS) | {"windows"}, host
    # ...and the two dropped keys stay dropped
    assert "live_window_ids" not in lean["hosts"]["workbench"]
    assert "ssh_target" not in lean["hosts"]["workbench"]


def test_a_NULL_host_field_survives_the_projection():
    """🔴 KILLS: `{k: h.get(k) ... if h.get(k) is not None}`, which an audit
    found survives — and which deletes precisely the values that ARE the
    discriminators. `captures_seen: null` means the capture batch never
    answered; `windows_error: null` means `list-windows` succeeded. Dropping a
    null here does not shrink a payload, it removes a measurement's provenance.
    """
    lean = lean_of(base_gather(use_ledger=False, use_capture=False))
    wb = lean["hosts"]["workbench"]
    assert "captures_seen" in wb and wb["captures_seen"] is None
    assert "windows_error" in wb and wb["windows_error"] is None


def test_the_retained_TOP_LEVEL_set_is_pinned_in_both_directions():
    """🔴 Three sections were unpinned and all three are load-bearing:
    `clickhouse` (a status-discriminated section), `stale_threshold_secs` (what
    makes `status: stale` mean anything at all), and — on `detail` —
    `session_history`. Mutants dropping each survived."""
    lean = lean_of(base_gather(use_ledger=False))
    assert set(lean) == {
        "ts", "local_host", "stale_threshold_secs", "hosts", "clickhouse",
        "fuzzyclaw", "ledger", "filters", "caveats", "summary",
        # 🔴 `not_measured` SURVIVES THE LEAN PROJECTION, and it is the cheapest
        # thing here to justify keeping: it is the only key that tells a cold
        # agent what this payload contains NOTHING about. A lean view that
        # dropped it would be a smaller payload that reads as more complete —
        # the exact trade this view's own rule forbids.
        "not_measured",
        "clawgate_queue", "view", "lean_row_fields", "lean_host_fields",
    }


# --------------------------------------------------------------------------- #
# §10.2 — the CLI SEAM. The pure projection and the flag are each tested; the
# WIRE between them was tested by nothing.
# --------------------------------------------------------------------------- #
def test_the_lean_FLAG_actually_reaches_the_projection(
        monkeypatch, capsys, absent_blocked_cache):
    """🔴 THE ISOLATION SEAM, and an audit proved it open: mutating the call
    site to `if False and args.lean` — or inverting it to `if not args.lean` —
    left ALL 456 tests green while `scan --json --lean` emitted the full
    payload. Every lean test called `sm.lean_report` directly; the only test
    that went through `main` with the flag was the *without*-`--json` case.

    "Verified in isolation" is the new vacuous green: two components each
    tested, and the defect lives in the seam nobody owns. This drives the REAL
    CLI and reads the REAL stdout.

    KILLS: disconnecting the flag from the projection, in either direction.
    """
    monkeypatch.setattr(sm, "local_host_label", lambda *a, **k: "workbench")
    monkeypatch.setattr(sm, "_default_runner", make_runner())
    monkeypatch.setattr(sm, "read_fuzzyclaw_texts", lambda *a, **k: [])
    rc = sm.main(["scan", "--no-ch", "--no-ledger", "--host", "workbench",
                  "--json", "--lean"])
    payload = json.loads(capsys.readouterr().out)

    assert payload["view"] == "lean"
    assert payload["lean_row_fields"] == list(sm.LEAN_ROW_FIELDS)
    row = payload["hosts"]["workbench"]["windows"][0]
    assert set(row) == set(sm.LEAN_ROW_FIELDS)
    # the fields the view exists to drop are really gone off the wire
    for dropped in ("window_id", "ledger", "pane_id", "panes"):
        assert dropped not in row, dropped
    assert "live_window_ids" not in payload["hosts"]["workbench"]
    # ...and the exit contract is unchanged by the view
    assert rc == sm.EXIT_OK


def test_WITHOUT_the_flag_the_same_command_emits_the_FULL_payload(
        monkeypatch, capsys, absent_blocked_cache):
    """The other half of the seam — without this, the test above passes on a
    binary that emits the lean view unconditionally."""
    monkeypatch.setattr(sm, "local_host_label", lambda *a, **k: "workbench")
    monkeypatch.setattr(sm, "_default_runner", make_runner())
    monkeypatch.setattr(sm, "read_fuzzyclaw_texts", lambda *a, **k: [])
    sm.main(["scan", "--no-ch", "--no-ledger", "--host", "workbench", "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert "view" not in payload and "lean_row_fields" not in payload
    row = payload["hosts"]["workbench"]["windows"][0]
    assert "window_id" in row and "ledger" in row
    assert "live_window_ids" in payload["hosts"]["workbench"]


def test_INVARIANT_lean_and_full_agree_on_an_unreachable_fleet(
        monkeypatch, capsys, absent_blocked_cache):
    """🔴 LABELLED AS AN INVARIANT GUARD, because that is what it is. A delta
    re-audit showed it CANNOT fail: reverting `return code` to
    `return exit_code_for(report)` leaves the whole suite green, because
    EXIT_UNAVAILABLE is the one outcome where the two call sites provably agree
    whatever `LEAN_HOST_FIELDS` holds. An earlier name claimed it covered the
    ordering; it never did, and counting an invariant guard as regression
    coverage is how a gap stays invisible.

    The real regression coverage for the move is the test below.
    """
    monkeypatch.setattr(sm, "local_host_label", lambda *a, **k: "workbench")
    monkeypatch.setattr(sm, "_default_runner",
                        make_runner(local_rc=1, local_err="down",
                                    remote_rc=1, remote_err="down"))
    monkeypatch.setattr(sm, "read_fuzzyclaw_texts", lambda *a, **k: [])
    lean_rc = sm.main(["scan", "--no-ch", "--no-ledger", "--json", "--lean"])
    capsys.readouterr()
    full_rc = sm.main(["scan", "--no-ch", "--no-ledger", "--json"])
    capsys.readouterr()
    assert lean_rc == full_rc == sm.EXIT_UNAVAILABLE


def test_the_exit_code_is_computed_BEFORE_the_projection(
        monkeypatch, capsys, absent_blocked_cache):
    """🔴 THE ACTUAL REGRESSION COVERAGE for the ordering, and it has to force
    the coupling to bite. `exit_code_for` reads `hosts[*].reachable`; run AFTER
    the projection it would read the LEAN report, so the exit contract would
    silently depend on `reachable` staying in `LEAN_HOST_FIELDS`.

    🔴 THE HAZARD RUNS THE OTHER WAY from what an earlier comment claimed. A
    missing `reachable` makes `exit_code_for` return EXIT_UNAVAILABLE MORE
    often, so the failure is a HEALTHY fleet reporting UNAVAILABLE — not a dead
    one reporting success. Stating a guard's direction backwards is how the
    wrong test gets written to defend it.

    So: drop `reachable` from the projection, scan a REACHABLE host, and require
    the code to be unchanged. With the computation after the projection this
    returns EXIT_UNAVAILABLE and the assertion fails; before it, the projection
    cannot reach the answer at all.
    """
    monkeypatch.setattr(sm, "local_host_label", lambda *a, **k: "workbench")
    monkeypatch.setattr(sm, "_default_runner", make_runner())
    monkeypatch.setattr(sm, "read_fuzzyclaw_texts", lambda *a, **k: [])
    monkeypatch.setattr(sm, "LEAN_HOST_FIELDS",
                        tuple(f for f in sm.LEAN_HOST_FIELDS
                              if f != "reachable"))
    rc = sm.main(["scan", "--no-ch", "--no-ledger", "--host", "workbench",
                  "--json", "--lean"])
    payload = json.loads(capsys.readouterr().out)

    assert "reachable" not in payload["hosts"]["workbench"], (
        "the monkeypatch did not take — this test would pass vacuously")
    assert rc == sm.EXIT_OK, (
        "the exit code was read off the PROJECTED report: a reachable host "
        "with rows reported as unavailable")


def test_tail_says_lean_has_no_effect_rather_than_ignoring_it(
        monkeypatch, capsys, absent_blocked_cache):
    """`tail` RETURNS before the shared notice, so it needed its own — the rule
    this flag's own justification cites, with the precedent (`--claude-only`)
    sitting one line above it."""
    monkeypatch.setattr(sm, "local_host_label", lambda *a, **k: "workbench")
    monkeypatch.setattr(sm, "_default_runner", make_runner())
    sm.main(["tail", "scratch7:3", "--json", "--lean"])
    assert "--lean has no effect on `tail`" in capsys.readouterr().err


def test_DETAIL_lean_keeps_the_prompt_history_that_detail_exists_for(
        monkeypatch, capsys, absent_blocked_cache):
    """🔴 THE PR'S OWN THESIS, APPLIED TO ITSELF. A delta re-audit found that
    dropping `session_history` in `lean_report` survives the whole suite — no
    test combined `detail` with `--lean` at all, so `detail <t> --json --lean`
    could silently lose the prompt history, which is the only reason `detail`
    exists. Same shape as the CLI seam this PR was written to close: each part
    tested, the combination by nothing.

    KILLS: excluding `session_history` from the projection.
    """
    monkeypatch.setattr(sm, "local_host_label", lambda *a, **k: "workbench")
    monkeypatch.setattr(sm, "_default_runner", make_runner())
    monkeypatch.setattr(sm, "read_fuzzyclaw_texts", lambda *a, **k: [])
    monkeypatch.setattr(sm, "detail_history",
                        lambda *a, **k: {"status": "ok", "session": "s-1",
                                         "rows": [{"ts": "t", "kind": "user",
                                                   "snippet": "hello"}]})
    sm.main(["detail", "scratch7:3", "--no-ch", "--no-ledger", "--json",
             "--lean"])
    payload = json.loads(capsys.readouterr().out)

    assert payload["view"] == "lean"
    assert "session_history" in payload, (
        "the lean projection dropped the payload detail exists to produce")
    assert payload["session_history"]["rows"][0]["snippet"] == "hello"
    # ...and the narrowing still happened, so this is `detail`, not a scan
    rows = [r for h in payload["hosts"].values() for r in h["windows"]]
    assert len(rows) == 1 and rows[0]["session"] == "scratch7"


def test_a_PLAIN_tail_also_says_lean_has_no_effect(
        monkeypatch, capsys, absent_blocked_cache):
    """One-dimension coverage: the existing notice test passes `--json`, so
    `tail --lean` without it was unobserved. The notice is about the SUBCOMMAND,
    not about the output format."""
    monkeypatch.setattr(sm, "local_host_label", lambda *a, **k: "workbench")
    monkeypatch.setattr(sm, "_default_runner", make_runner())
    sm.main(["tail", "scratch7:3", "--lean"])
    assert "--lean has no effect on `tail`" in capsys.readouterr().err


def test_the_row_RUNTIME_carries_the_VALUE_not_just_the_key(monkeypatch):
    """🔴 THE PR'S HEADLINE FIELD, and an audit found it pinned by NAME in three
    places and by VALUE in none. Two mutants survived the whole 9,849-test gate:
    `"runtime": None` (the feature ships completely inert) and
    `.get("session_id")` (a WRONG value on every row). The three tests naming
    `runtime` were the two field ledgers — which assert key names — and a golden
    that asserts `None` on a `use_ledger=False` fixture, which every mutant also
    produces. "A type declaration is not a code path."

    So: a row joined to a record must carry that record's RUNTIME, and it must
    be distinguishable from the session id — the fixture's two values are
    deliberately different strings.
    """
    rep = ledger_gather(
        workbench=[led_rec(window_id="@41", session_id="ses_9911",
                           runtime="opencode")],
        use_fuzzyclaw=False)
    row = next(r for r in rows_of(rep) if r["window_id"] == "@41")
    assert row["runtime"] == "opencode"
    assert row["claude_session_id"] == "ses_9911"
    assert row["runtime"] != row["claude_session_id"]
    # ...and it survives the lean projection, which is the view agents read
    lean = lean_of(rep)
    lrow = next(r for r in rows_of(lean) if r["session"] == row["session"])
    assert lrow["runtime"] == "opencode"


def test_a_row_no_writer_recorded_has_a_NULL_runtime_not_a_guess():
    """The other direction: `runtime` is null when nothing recorded the window,
    never inferred from the pane command. `claude` (the command matching
    /claude/) and `runtime` (which writer answered) are different facts."""
    rep = ledger_gather(workbench=[], use_fuzzyclaw=False)
    for row in rows_of(rep):
        assert row["runtime"] is None


def test_a_CROSS_RUNTIME_conflict_names_the_runtimes_in_the_TABLE():
    """🔴 `runtimes` was computed and never rendered — the line printed two
    UUIDs, which is the exact thing the change adding the field said it fixed.
    The commonest real conflict is cross-runtime, and `claude, opencode` is what
    makes the ⚠ actionable rather than alarming."""
    text = sm.render_table(ledger_gather(
        workbench=[led_rec(pane_id="%11", session_id="7f3a-claude",
                           runtime="claude", ago=900),
                   led_rec(pane_id="%12", session_id="ses_9911",
                           runtime="opencode", ago=60)],
        use_fuzzyclaw=False))
    assert "⚠ LEDGER CONFLICT" in text
    assert "claude, opencode" in text
    # the session ids stay too — they are how you find the actual sessions
    assert "7f3a-claude" in text and "ses_9911" in text


# =========================================================================== #
# §kind — THE ENTITY AXIS
#
# `kind` says WHAT a row is: a tmux pane (`tmux`) or a dispatch with no pane
# (`cluster`). Only `tmux` rows are produced today; `cluster` is enumerated
# ahead of writer 3 so the roll-ups are decided BEFORE such a row can appear,
# not patched after one silently miscounts.
#
# 🔴 EVERY CLUSTER TEST BELOW CONSTRUCTS ITS OWN ROW, because no fixture and no
# code path in this build produces one. A test that waited for a real cluster
# row would be VACUOUS — green today, green with the whole classifier deleted,
# and still green on the day writer 3 lands wrong. Constructing the row is what
# makes these fail on pre-`kind` code.
# =========================================================================== #
def cluster_row(status="busy", **kw):
    """A row shaped the way spec §4 says a clawgate dispatch will be shaped:
    the tmux-only fields null, `claude` null because there is no pane whose
    command could be read, `kind` the discriminant that says so.

    🔴 IT SETS NEITHER `waiting_status` NOR `unsent_prompt_status`, AND THAT
    SYMMETRY IS LOAD-BEARING. It used to set `waiting_status="not_tmux"` while
    leaving `unsent_prompt_status` absent, and that asymmetry hid a real crash
    for a whole PR: `_unsent_rollup` grew an `or "none"` coercion whose source
    comment correctly generalised the diagnosis to "a row built anywhere ELSE
    need not set the field", and `_waiting_rollup` — which has the identical
    histogram over the identical class of row — was left uncoerced. No fixture
    could see it, because this one always handed waiting a string. A row built
    outside `fold_windows` carries NEITHER field, so this one carries neither.
    """
    row = dict(kind="cluster", host=None, session=None, window_index=None,
               window_id=None, claude=None, busy=None, status=status,
               age_secs=42.0, age_source="clawgate", runtime="clawgate",
               claude_session_id=None, task="a wedged dispatch",
               waiting_probable=None,
               waiting_signals=None, path="", command="", panes=0,
               label="cg", label_source="none", hotkey=None, codename=None,
               window_name="", pane_id=None, ledger=None, fuzzyclaw=None)
    row.update(kw)
    return row


def with_cluster_rows(rep, *rows):
    """Append constructed cluster rows to a real gathered report, in place."""
    rep["hosts"]["workbench"]["windows"].extend(rows)
    return rep


def test_the_mix_fixture_really_produces_only_tmux_rows():
    """INSTRUMENT CHECK, before any cluster claim is read off this fixture.

    Every test below asserts what changes when a cluster row is ADDED. That is
    only meaningful if the baseline has none — otherwise the deltas are
    measured against an unknown starting point.
    """
    rows = mix_gather()["hosts"]["workbench"]["windows"]
    assert rows, "empty fixture would make every assertion below vacuous"
    assert {r["kind"] for r in rows} == {"tmux"}


def test_every_row_carries_a_kind_and_it_is_never_null():
    """🔴 `kind` is the one field on a row whose null IS a bug. `runtime` is
    null all the time and means something; `kind` is known at construction."""
    for rep in (base_gather(), mix_gather()):
        rows = [r for h in rep["hosts"].values() for r in h["windows"]]
        assert rows
        for r in rows:
            assert "kind" in r, "a row was built without the discriminant"
            assert r["kind"] is not None
            assert r["kind"] in sm.KINDS


def test_KINDS_is_a_closed_vocabulary_that_fails_if_it_GROWS_or_SHRINKS():
    """The same ledger idiom as FUZZYCLAW_FIELDS/WAITING_SIGNALS/STUCK_REASONS.
    Both directions are silent breakage: a kind removed here while rows still
    carry it turns `row_class` into `unknown_kind`, and a kind added without
    the roll-up being taught about it is the miscount this axis exists to stop.
    """
    assert sm.KINDS == ("tmux", "cluster")


# --------------------------------------------------------------------------- #
# 🔴 THE DEFECT THIS AXIS EXISTS TO PREVENT
# --------------------------------------------------------------------------- #
def test_a_CLUSTER_row_is_NOT_counted_as_a_SHELL():
    """🔴 THE ONE THAT MATTERS. `claude` on a row is
    `pane_current_command =~ /claude/` — a fact about a PANE. A dispatch with no
    pane has `claude: None`, which is FALSY, so the old
    `"claude" if row["claude"] else "shell"` counted an agent as a bare shell:
    an agent filed under the bucket that means "nobody is working here".

    That is the claude/shell conflation one axis over — the same defect that
    published `idle: 17` for 12 agents + 5 shells. Every fixture in this suite
    is tmux-only, so nothing else here can see it.
    """
    rep = with_cluster_rows(mix_gather(), cluster_row(status="busy"))
    s = sm.summarize(rep)
    busy = s["status"]["busy"]
    # the bare shell that was already busy in the fixture is still the ONLY one
    assert busy["shell"] == 1, "the cluster dispatch leaked into `shell`"
    assert busy["cluster"] == 1
    assert busy["claude"] == 0
    # ...and it is not absorbed at the top level either. The fixture has TWO
    # bare shells (ridge idle, thicket busy) and three agents; adding a cluster
    # dispatch must move neither number.
    assert s["shell"] == 2
    assert s["claude"] == 3


def test_a_CLUSTER_row_is_not_quietly_counted_as_CLAUDE_either():
    """The mirror image. Overcounting agents is the friendlier direction and
    still wrong: `claude` is what the operator reads as "my Claude windows"."""
    rep = with_cluster_rows(mix_gather(),
                            cluster_row(status="busy", claude=True))
    s = sm.summarize(rep)
    # `kind` decides, NOT the `claude` flag — a cluster row asserting claude:True
    # must still not land in the claude count.
    assert s["status"]["busy"]["cluster"] == 1
    assert s["status"]["busy"]["claude"] == 0
    assert s["claude"] == 3


def test_NO_cluster_key_is_published_while_no_cluster_row_exists():
    """🔴 THE FAKE ZERO. Pre-seeding `cluster: 0` in every bucket would publish
    a measurement of a population this build structurally cannot produce, and a
    reader cannot tell that zero from "wired to nothing" — which is the exact
    failure this tool spends most of its output guarding against.

    So the class key is created ON DEMAND, and a tmux-only summary is
    byte-identical to what it was before `kind` existed.
    """
    s = sm.summarize(mix_gather())
    for b in sm.STATUS_BUCKETS:
        assert set(s["status"][b]) == {"claude", "shell", "total"}, (
            "a tmux-only scan must not publish a cluster count")
    # the caveat is what tells the reader why, instead of the absent key.
    # 🔴 The CONSTANT carries no `kinds_produced` at all — that key is a
    # MEASUREMENT and a literal there was a fake one. What the build produces
    # without measuring lives in its own named constant.
    assert "kinds_produced" not in sm.CAVEATS["kind_scope"]
    assert sm.KINDS_PRODUCED_BY_CONSTRUCTION == ("tmux",)
    # a real scan supplies it
    assert mix_gather()["caveats"]["kind_scope"]["kinds_produced"] == ["tmux"]


def test_a_row_with_NO_kind_gets_its_OWN_class_and_never_becomes_cluster():
    """A row built by code that forgot the field is a BUG in that code. Folding
    it into `cluster` would dress it as a real measurement — the same mistake
    one level down from counting a cluster row as a shell."""
    assert sm.row_class({"claude": True}) == "unknown_kind"
    assert sm.row_class({"kind": None, "claude": False}) == "unknown_kind"
    assert sm.row_class({"kind": "wat"}) == "unknown_kind"
    rep = with_cluster_rows(mix_gather(), cluster_row(status="busy", kind=None))
    s = sm.summarize(rep)
    assert s["status"]["busy"].get("cluster") is None
    assert s["status"]["busy"]["unknown_kind"] == 1
    # and it is visible in the histogram rather than absorbed
    assert s["kind"]["none"] == 1


def test_row_class_keys_on_KIND_not_on_a_null_claude():
    """A tmux pane whose command did not parse also has `claude: None`, and
    that row genuinely IS "not a claude pane" — it belongs in `shell`. Two
    different nulls, and only `kind` separates them. Keying on `claude is None`
    would have collapsed both into one class and looked correct."""
    assert sm.row_class({"kind": "tmux", "claude": None}) == "shell"
    assert sm.row_class({"kind": "tmux", "claude": False}) == "shell"
    assert sm.row_class({"kind": "tmux", "claude": True}) == "claude"
    assert sm.row_class({"kind": "cluster", "claude": None}) == "cluster"


def test_the_bucket_TOTAL_accounts_for_the_cluster_class_too():
    """🔴 The total is summed over whatever class keys exist, never over
    `claude + shell` by name. Named summation would have kept returning a
    plausible number that silently omitted the new class."""
    rep = with_cluster_rows(mix_gather(), cluster_row(status="idle"),
                            cluster_row(status="idle"))
    s = sm.summarize(rep)
    idle = s["status"]["idle"]
    assert idle["cluster"] == 2
    assert idle["total"] == idle["claude"] + idle["shell"] + idle["cluster"]
    assert sum(s["status"][b]["total"] for b in s["status"]) \
        == s["total_sessions"]


def test_the_top_level_totals_use_the_SAME_predicate_as_the_buckets():
    """One rule, one place. `claude`/`shell` were open-coded at two sites with
    different expressions (`r["claude"]` and `not r["claude"]`), which is the
    shape that ends up wrong at exactly one site."""
    rep = with_cluster_rows(mix_gather(), cluster_row(status="busy"),
                            cluster_row(status="idle"))
    s = sm.summarize(rep)
    rows = [r for h in rep["hosts"].values() for r in h["windows"]]
    assert s["claude"] == sum(1 for r in rows if sm.row_class(r) == "claude")
    assert s["shell"] == sum(1 for r in rows if sm.row_class(r) == "shell")
    # the two no longer sum to the whole set — the cluster rows are neither
    assert s["claude"] + s["shell"] == s["total_sessions"] - 2


def test_the_kind_histogram_is_DERIVED_from_the_rows():
    """Derived, not hardcoded, so a kind cannot exist on a row and be missing
    from the summary — the rule `age_sources` and `waiting.per_signal` follow."""
    rep = with_cluster_rows(mix_gather(), cluster_row(), cluster_row())
    s = sm.summarize(rep)
    assert s["kind"] == {"tmux": 5, "cluster": 2}
    assert sum(s["kind"].values()) == s["total_sessions"]


def test_the_lean_view_carries_kind():
    """`kind` is what tells a lean reader whether a null `session` is "not
    measured" or "this entity has no session". Dropping it would recreate the
    ambiguity the lean field lists exist to remove."""
    assert "kind" in sm.LEAN_ROW_FIELDS
    lean = sm.lean_report(mix_gather())
    assert "kind" in lean["lean_row_fields"]
    for r in lean["hosts"]["workbench"]["windows"]:
        assert r["kind"] == "tmux"


# --------------------------------------------------------------------------- #
# the caveat — a machine-readable CLAIM, and claims go stale
# --------------------------------------------------------------------------- #
def test_the_kind_scope_caveat_is_rendered_and_names_the_unproduced_kind():
    """🔴 THIS TEST USED TO BE SPELLED RATHER THAN STRUCTURAL, and an audit
    found two mutants surviving a fully green suite because of it.

    It asserted `"tmux" in line and "cluster" in line` — and BOTH words also
    appear in the sentence's STATIC prose, so neither computed slot was ever
    read. Swapping `kinds_produced` for `kinds_enumerated` (rendering the false
    claim "every row is kind=tmux/cluster"), or inverting the set difference
    (rendering the self-contradictory "tmux is ENUMERATED but NOT PRODUCED"),
    both passed. A guard is spelled when it can pass while the hazard exists in
    a different shape; the fix is to assert the COMPUTED substrings, with the
    surrounding punctuation that pins which slot they came from.
    """
    lines = sm.render_caveats(base_gather())
    line = next(ln for ln in lines if "caveat[kind_scope]" in ln)
    # the two computed slots, each anchored so a swap cannot satisfy the other
    assert "kind=tmux —" in line, "the produced-kinds slot"
    assert "— cluster is ENUMERATED" in line, "the unproduced-kinds slot"
    # ...and the inversions the old spelling allowed are now excluded by name
    assert "kind=tmux/cluster" not in line
    assert "tmux is ENUMERATED" not in line
    assert "NOT PRODUCED" in line
    # it must point at where clawgate IS reported, or the reader concludes the
    # tool cannot say anything about cluster work at all
    assert "CLAWGATE QUEUE" in line


def test_the_kind_scope_caveat_SLOTS_ARE_LIVE_not_static_prose():
    """The positive control the spelled version never had: change the inputs
    and watch BOTH slots move. If either is hardcoded in the sentence, one of
    these renders identically to the default and this fails."""
    default = sm._fmt_kind_scope({"kinds_produced": ["tmux"],
                                  "kinds_enumerated": ["tmux", "cluster"]})
    swapped = sm._fmt_kind_scope({"kinds_produced": ["cluster"],
                                  "kinds_enumerated": ["tmux", "cluster"]})
    assert "kind=cluster —" in swapped and "— tmux is ENUMERATED" in swapped
    assert swapped != default


def test_the_caveat_does_not_degrade_when_EVERY_kind_is_produced():
    """🔴 THE EMPTY SET IS REACHABLE — `gather` derives `kinds_produced` from
    the rows, so once writer 3 lands, the unproduced set is empty. The
    single-sentence version then rendered `—  is ENUMERATED but NOT PRODUCED`:
    a blank subject in the sentence whose only job is naming what is missing.

    A caveat that degrades into a grammatically-broken half-claim is worse than
    one that says nothing, because it still reads as a claim.
    """
    line = sm._fmt_kind_scope({"kinds_produced": ["cluster", "tmux"],
                               "kinds_enumerated": ["tmux", "cluster"]})
    assert "NOT PRODUCED" not in line
    assert "  is ENUMERATED" not in line, "blank subject"
    assert "every enumerated kind IS produced" in line
    # the pointer to where clawgate lives survives BOTH branches
    assert "CLAWGATE QUEUE" in line


def test_the_caveat_is_MEASURED_from_the_rows_not_asserted_from_the_constant():
    """🔴 A caveat is a machine-readable claim, and this one used to be a
    literal in CAVEATS — so it said "every row is kind=tmux" no matter what the
    rows were, and would have kept saying it on the day writer 3 shipped. That
    is precisely how `fuzzyclaw_scope` went stale in #471.

    Derived from the report's own rows, so the line cannot disagree with the
    table printed above it.
    """
    rep = with_cluster_rows(mix_gather(), cluster_row())
    rep["summary"] = sm.summarize(rep)
    rep["caveats"] = dict(rep["caveats"])
    rep["caveats"]["kind_scope"] = dict(rep["caveats"]["kind_scope"])
    rep["caveats"]["kind_scope"]["kinds_produced"] = sorted(
        rep["summary"]["kind"])
    line = next(ln for ln in sm.render_caveats(rep) if "kind_scope" in ln)
    assert "cluster/tmux" in line, "the caveat ignored the cluster row"
    assert "every enumerated kind IS produced" in line
    # and the REAL gather path does the same derivation, not just this test
    live = base_gather()
    assert live["caveats"]["kind_scope"]["kinds_produced"] == ["tmux"]


def test_measured_caveats_DERIVES_from_rows_where_derived_differs_from_the_constant():
    """🔴 THE CONTROL THAT THE PREVIOUS TEST CANNOT PROVIDE.

    Asserting `kinds_produced == ["tmux"]` off a real gather is satisfied
    equally by the derivation and by a hardcoded `["tmux"]` — every real scan
    is tmux-only, so the two are byte-identical and a mutant replacing one with
    the other SURVIVED a green suite. A fixture whose values coincide with the
    constant collapses distinct implementations into one observation.

    So this feeds rows whose kinds CANNOT be the constant, and watches the
    output move.
    """
    def rep_of(*kinds):
        return {"hosts": {"h": {"windows": [{"kind": k} for k in kinds]}}}

    assert sm.measured_caveats(
        rep_of("cluster"))["kind_scope"]["kinds_produced"] == ["cluster"]
    assert sm.measured_caveats(
        rep_of("cluster", "tmux"))["kind_scope"]["kinds_produced"] \
        == ["cluster", "tmux"]
    # deduplicated and sorted, so the render is deterministic
    assert sm.measured_caveats(
        rep_of("tmux", "cluster", "tmux"))["kind_scope"]["kinds_produced"] \
        == ["cluster", "tmux"]
    # a row with NO kind is a bug, and the caveat is where it surfaces —
    # dropping it would let the line claim a scope it did not measure
    assert sm.measured_caveats(
        rep_of("tmux", None))["kind_scope"]["kinds_produced"] \
        == ["none", "tmux"]
    # empty report: no rows measured, so no kinds claimed
    assert sm.measured_caveats(
        {"hosts": {}})["kind_scope"]["kinds_produced"] == []


def test_the_derived_kinds_are_SORTED_deterministically():
    """🔴 A DETERMINISTIC KILL FOR A NONDETERMINISM MUTANT.

    Dropping `sorted()` leaves `list({...})`, whose order depends on
    PYTHONHASHSEED — which is RANDOM per process by default. The literal
    assertion above catches that only on the seeds that happen to order the set
    wrongly, so it is a coin-flip kill, not a kill: a mutation sweep scored it
    SURVIVED on one run purely because that run's seed put `cluster` first.

    A flaky guard is fixable rather than re-runnable, so the seed dependency is
    removed instead of tolerated: seed 2 is one under which
    `list({"cluster", "tmux"})` yields `tmux` FIRST (measured), so with
    `sorted()` this returns `["cluster", "tmux"]` and without it `["tmux",
    "cluster"]` — every run, not most runs.
    """
    prog = (
        "import importlib.machinery as m, importlib.util as u, json;"
        "l=m.SourceFileLoader('sm', %r);"
        "sp=u.spec_from_file_location('sm', %r, loader=l);"
        "mod=u.module_from_spec(sp); sp.loader.exec_module(mod);"
        "rep={'hosts':{'h':{'windows':[{'kind':'tmux'},{'kind':'cluster'}]}}};"
        "print(json.dumps("
        "mod.measured_caveats(rep)['kind_scope']['kinds_produced']))"
    ) % (_SCRIPT, _SCRIPT)
    env = dict(os.environ, PYTHONHASHSEED="2", PYTHONDONTWRITEBYTECODE="1")
    out = subprocess.run([sys.executable, "-c", prog], capture_output=True,
                         text=True, env=env, timeout=60)
    assert out.returncode == 0, out.stderr
    # POSITIVE CONTROL: under this seed the RAW set order really is tmux-first,
    # so a passing assertion below proves `sorted()` ran — not that the seed
    # happened to agree with it.
    raw = subprocess.run(
        [sys.executable, "-c", "print(list({'cluster','tmux'}))"],
        capture_output=True, text=True, env=env, timeout=60)
    assert raw.stdout.strip() == "['tmux', 'cluster']", (
        "seed 2 no longer orders this set tmux-first; pick another seed or "
        "this test proves nothing")
    assert json.loads(out.stdout) == ["cluster", "tmux"]


def test_a_ZERO_ROW_scan_does_not_CLAIM_a_kind_it_never_measured():
    """🔴 REACHABLE TODAY, with no cluster row anywhere. `kinds_produced` is
    measured, so a scan with no rows measures `[]` — and `[] or ["tmux"]` is
    `["tmux"]`, because an empty list is FALSY. The line then read `rows in
    this scan are kind=tmux` over `summary: 0 windows`: a literal wearing a
    measurement's clothes, which is the defect measuring it was meant to kill.

    The reader who hits this is the one whose hosts are unreachable — exactly
    the reader who must not be told what "this scan" contained.
    """
    line = sm._fmt_kind_scope({"kinds_produced": [],
                               "kinds_enumerated": ["tmux", "cluster"]})
    assert "kind=tmux" not in line
    assert "NO rows were measured" in line
    assert "NOT a claim that any kind is absent" in line
    # A MISSING key is different from a measured empty one, and still falls
    # back — that is the bare-constant render, not a scan. It must say so:
    # phrasing it as "in this scan" was the very contradiction between this
    # branch's comment and its own output.
    missing = sm._fmt_kind_scope({"kinds_enumerated": ["tmux", "cluster"]})
    assert "kind=tmux rows by construction" in missing
    assert "in this scan are" not in missing


def test_the_zero_row_scan_reaches_that_branch_through_the_REAL_render():
    """The unit test above proves the branch; this proves it is REACHED. A
    scan where no host answers produces zero rows through the ordinary path."""
    rep = base_gather(runner=make_runner(local_rc=1, remote_rc=1))
    assert rep["summary"]["total_sessions"] == 0, "fixture did not go empty"
    assert rep["caveats"]["kind_scope"]["kinds_produced"] == []
    line = next(ln for ln in sm.render_caveats(rep) if "kind_scope" in ln)
    assert "NO rows were measured" in line
    assert "kind=tmux" not in line


def test_the_DETAIL_path_re_derives_the_caveats_it_re_summarizes():
    """🔴 `filter_report` re-runs `summarize` and renders through
    `render_caveats`. Inheriting the full scan's caveats made `kind_scope` a
    claim about every row, printed under a ONE-ROW table — the disagreement
    between line and table that measuring it was supposed to make impossible.
    One rule enforced at one of its two call sites."""
    rep = with_cluster_rows(mix_gather(), cluster_row())
    rep["summary"] = sm.summarize(rep)
    rep["caveats"] = sm.measured_caveats(rep)
    assert rep["caveats"]["kind_scope"]["kinds_produced"] == ["cluster", "tmux"]
    # narrow to one TMUX window — the cluster row is filtered out, so the
    # caveat must stop claiming cluster was produced
    one = sm.filter_report(rep, "hollow", "2")
    assert one["summary"]["total_sessions"] == 1
    assert one["caveats"]["kind_scope"]["kinds_produced"] == ["tmux"]
    line = next(ln for ln in sm.render_caveats(one) if "kind_scope" in ln)
    assert "cluster is ENUMERATED but NOT PRODUCED" in line


def test_measured_caveats_reads_EVERY_host_not_just_the_first():
    """A cluster row on the second host would otherwise vanish from
    `kinds_produced`, and the caveat would deny rows that ARE in the payload."""
    rep = {"hosts": {"a": {"windows": [{"kind": "tmux"}]},
                     "b": {"windows": [{"kind": "cluster"}]}}}
    assert sm.measured_caveats(rep)["kind_scope"]["kinds_produced"] \
        == ["cluster", "tmux"]


def test_measured_caveats_detaches_EVERY_caveat_from_the_module_constant():
    """🔴 Copying only `kind_scope` left the other four as the SAME objects as
    CAVEATS, so a consumer writing through the returned dict — which the
    "returns a new dict, mutates nothing" docstring invites — poisoned the
    process-global constant. A purity claim true of one key and false of its
    siblings is worse than none: it is specific enough to be trusted."""
    rep = {"hosts": {}}
    out = sm.measured_caveats(rep)
    assert set(out) == set(sm.CAVEATS)
    for key in sm.CAVEATS:
        assert out[key] is not sm.CAVEATS[key], f"{key} is still shared"
    before = list(sm.CAVEATS["waiting_signal"]["signals"])
    out["waiting_signal"]["signals"] = ["POISONED"]
    assert sm.CAVEATS["waiting_signal"]["signals"] == before


# 🔴 THE WHOLE SENTENCE, PINNED — because a PARTIAL assertion on prose is
# satisfied by prose that says something else. Two mutants walked the earlier
# feature-based guards by REWORDING the banned claim: one put
# "every row here is a tmux pane and the cluster kind never appears" into the
# note (the banned substring was "every row is a tmux pane"), the other slipped
# a false parenthetical into the zero-row sentence while keeping all three
# asserted phrases. Both passed the full suite.
#
# So each branch's exact output is pinned. The trade is explicit and accepted:
# a cosmetic reword fails this test. That is the price of a guard a reword
# cannot walk, and these four sentences are the tool's machine-readable claims
# about what it did and did not measure.
_CG = ("clawgate is reported separately under CLAWGATE QUEUE")
KIND_SCOPE_SENTENCES = {
    # no measurement supplied — the bare-constant render. MUST NOT say "scan".
    "missing": (
        "this tool produces kind=tmux rows by construction (no scan measured "
        "here) — cluster is ENUMERATED but NOT PRODUCED, so no such row "
        "appears and its absence is NOT a measured zero; " + _CG),
    # measured ZERO rows — must name no kind as observed
    "empty": (
        "NO rows were measured in this scan, so no kind was observed — this "
        "is NOT a claim that any kind is absent, and the enumerated kinds "
        "(tmux/cluster) say nothing about what a reachable host holds; " + _CG),
    # today's ordinary scan
    "tmux": (
        "rows in this scan are kind=tmux — cluster is ENUMERATED but NOT "
        "PRODUCED, so no such row appears and its absence is NOT a measured "
        "zero; " + _CG),
    # 🔴 by-construction head with NOTHING unproduced — the combination the
    # shared builder rendered self-contradicting ("no scan measured here …
    # so this scan covers"). Its tail must speak of the BUILD.
    "by_construction_all_produced": (
        "this tool produces kind=tmux rows by construction (no scan measured "
        "here) — every enumerated kind IS produced, so this build covers the "
        "whole entity axis; clawgate approval state is still reported "
        "separately under CLAWGATE QUEUE"),
    # after writer 3
    "both": (
        "rows in this scan are kind=cluster/tmux — every enumerated kind IS "
        "produced, so this scan covers the whole entity axis; clawgate "
        "approval state is still reported separately under CLAWGATE QUEUE"),
    # 🔴 A FILTER REMOVED A WHOLE KIND. Without its own clause this rendered
    # "tmux is ENUMERATED but NOT PRODUCED" — three false claims in one
    # sentence, about rows the scan had just measured and thrown away.
    "filtered_out": (
        "rows in this scan are kind=cluster — a FILTER REMOVED every kind=tmux "
        "row this scan produced, so their absence above is the FILTER's doing "
        "and NOT a measured absence of that work; " + _CG),
    # a filter removed one kind AND another was never produced — both causes
    # are named, and neither is allowed to absorb the other
    "filtered_and_unproduced": (
        "rows in this scan are kind=tmux — a FILTER REMOVED every kind=zzz row "
        "this scan produced, so their absence above is the FILTER's doing and "
        "NOT a measured absence of that work; cluster is ENUMERATED but NOT "
        "PRODUCED, so no such row appears and its absence is NOT a measured "
        "zero; " + _CG),
    # 🔴 ZERO SURVIVING ROWS BECAUSE OF THE FILTER. "NO rows were measured in
    # this scan" is FALSE here and blames the hosts for what the flag did —
    # reachable TODAY, as `--claude-only` over a shell-only host.
    "filtered_to_empty": (
        "every row this scan measured was REMOVED BY A FILTER (kind=tmux), so "
        "no kind survives to be reported — rows were measured and dropped, NOT "
        "absent, and this says nothing about what an unfiltered scan holds; "
        + _CG),
}


def test_every_kind_scope_sentence_is_pinned_WHOLE_not_by_feature():
    """🔴 Feature-based assertions on this sentence have been walked TWICE by
    mutants that reworded the claim while keeping the asserted fragments. The
    whole string is the only thing a reword cannot satisfy."""
    E = ["tmux", "cluster"]
    got = {
        "missing": sm._fmt_kind_scope({"kinds_enumerated": E}),
        "empty": sm._fmt_kind_scope({"kinds_produced": [],
                                     "kinds_enumerated": E}),
        "tmux": sm._fmt_kind_scope({"kinds_produced": ["tmux"],
                                    "kinds_enumerated": E}),
        "both": sm._fmt_kind_scope({"kinds_produced": ["cluster", "tmux"],
                                    "kinds_enumerated": E}),
        # 🔴 THE COMBINATION THE SHARED BUILDER MADE FALSE. The by-construction
        # head with NOTHING unproduced took a tail hardcoded to "so THIS SCAN
        # covers the whole entity axis" — contradicting the head's own "no scan
        # measured here" eight words earlier. Unreachable until
        # KINDS_PRODUCED_BY_CONSTRUCTION covers the enumerated set, i.e. the
        # moment writer 3 lands, which is what this caveat is FOR.
        "by_construction_all_produced": sm._fmt_kind_scope(
            {"kinds_enumerated": ["tmux"]}),
        # 🔴 THE FILTERED BRANCHES. `kinds_excluded_by_filter` is the field that
        # separates "this build never emitted that kind" from "this run threw
        # those rows away", and both of those render out of `kinds_produced`
        # alone as the SAME sentence.
        "filtered_out": sm._fmt_kind_scope(
            {"kinds_produced": ["cluster"], "kinds_enumerated": E,
             "kinds_excluded_by_filter": ["tmux"]}),
        # `zzz` cannot be a member of KINDS, so the filter slot is proved LIVE
        # rather than coincident with a constant this module already holds.
        "filtered_and_unproduced": sm._fmt_kind_scope(
            {"kinds_produced": ["tmux"], "kinds_enumerated": ["tmux", "cluster",
                                                              "zzz"],
             "kinds_excluded_by_filter": ["zzz"]}),
        "filtered_to_empty": sm._fmt_kind_scope(
            {"kinds_produced": [], "kinds_enumerated": E,
             "kinds_excluded_by_filter": ["tmux"]}),
    }
    assert got == KIND_SCOPE_SENTENCES
    # INSTRUMENT CHECK: all eight are genuinely distinct, so a builder that
    # collapsed two branches into one could not satisfy this by accident.
    assert len(set(got.values())) == 8
    # the three that must never phrase themselves as a measurement of a scan
    assert "in this scan are" not in got["missing"]
    assert "kind=" not in got["empty"]
    assert "this scan" not in got["by_construction_all_produced"]
    # 🔴 the two claims a filtered branch must NEVER make about a kind the
    # filter removed, banned by name in the branches that could make them
    assert "tmux is ENUMERATED but NOT PRODUCED" not in got["filtered_out"]
    assert "every enumerated kind IS produced" not in got["filtered_out"]
    assert "NO rows were measured" not in got["filtered_to_empty"]


def test_the_BY_CONSTRUCTION_slot_IS_LIVE_not_a_coincident_literal(monkeypatch):
    """🔴 THE SAME TRAP, ONE SLOT OVER — and it survived the commit that fixed
    the neighbouring one.

    `_fmt_kind_scope` renders `list(KINDS_PRODUCED_BY_CONSTRUCTION)`, and that
    constant is `("tmux",)`. So the lookup and a hardcoded `["tmux"]` produce
    byte-identical output, and a mutant replacing one with the other SURVIVES
    the whole suite. Every pin asserts `kind=tmux` — the value both spellings
    give.

    That matters precisely at writer 3: someone updating the constant to
    `("tmux", "cluster")` would update the two tests that pin it, see the
    `missing` whole-string pin still green, and ship a sentence that still says
    `kind=tmux`. The constant's own comment promises "writer 3 updates THIS
    tuple"; nothing proved the render reads it.

    So: move the constant to a value it cannot coincidentally equal, and watch
    the sentence move.
    """
    monkeypatch.setattr(sm, "KINDS_PRODUCED_BY_CONSTRUCTION", ("zzz",))
    line = sm._fmt_kind_scope({"kinds_enumerated": ["zzz", "qqq"]})
    assert "produces kind=zzz rows by construction" in line
    assert "tmux" not in line, "the render ignored the constant"
    assert "qqq is ENUMERATED but NOT PRODUCED" in line
    # and the writer-3 shape: the constant covering the whole enumerated set
    # must reach the all-produced tail, with the BUILD subject
    monkeypatch.setattr(sm, "KINDS_PRODUCED_BY_CONSTRUCTION", ("tmux", "cluster"))
    w3 = sm._fmt_kind_scope({"kinds_enumerated": ["tmux", "cluster"]})
    assert "produces kind=tmux/cluster rows by construction" in w3
    assert "so this build covers the whole entity axis" in w3
    assert "this scan covers" not in w3


def test_the_kinds_enumerated_SLOT_IS_LIVE_not_defaulted_to_KINDS():
    """🔴 THE FIXTURE-COINCIDES-WITH-THE-CONSTANT TRAP, which this file names
    in its own comments and which I then walked into.

    Every pin above uses `["tmux", "cluster"]` — which IS `KINDS`. So
    `kd.get("kinds_enumerated") or KINDS` and a hardcoded `KINDS` produce
    byte-identical output, and two mutants replacing the lookup with the
    constant SURVIVED the whole suite. A fixture whose value equals the
    constant cannot distinguish "read the argument" from "ignored it".

    So this passes an enumerated set that CANNOT be KINDS.
    """
    line = sm._fmt_kind_scope({"kinds_produced": ["tmux"],
                              "kinds_enumerated": ["tmux", "zzz"]})
    assert "zzz is ENUMERATED but NOT PRODUCED" in line
    assert "cluster" not in line, "the argument was ignored in favour of KINDS"
    # the zero-row branch interpolates it too, and had the same blind spot
    zero = sm._fmt_kind_scope({"kinds_produced": [],
                               "kinds_enumerated": ["zzz", "qqq"]})
    assert "(zzz/qqq)" in zero
    assert "tmux" not in zero and "cluster" not in zero
    # ...and so does the by-construction branch
    bc = sm._fmt_kind_scope({"kinds_enumerated": ["tmux", "zzz"]})
    assert "zzz is ENUMERATED but NOT PRODUCED" in bc
    assert "cluster" not in bc


def test_the_kind_scope_NOTE_names_NO_kind_at_all():
    """🔴 STRUCTURAL, replacing a spelled guard an auditor walked.

    The old assertion banned the literal string "every row is a tmux pane", and
    a mutant saying "every row here is a tmux pane and the cluster kind never
    appears" passed. Any ban-list of phrasings is walkable, so this bans
    naming a KIND at all: `kinds_produced` is the measured field and the note's
    job is to point AT it, so the moment the note names a kind it is making the
    standing claim that field exists to replace.

    🔴 THIS GUARD IS NOT SUFFICIENT ON ITS OWN, and saying so is the point of
    this paragraph. An audit walked it with a SYNONYM: "every row here is a
    terminal pane and the second enumerated entity never appears" names no
    member of KINDS, keeps every required token, and clears the length floor.
    The WHOLE-STRING pin in `test_the_kind_scope_NOTE_is_pinned_WHOLE` is what
    actually kills that; deleting the pin lets it through and deleting this
    guard does not. So this one adds no kill power TODAY — it exists to
    constrain a future edit that legitimately updates the pin, where "the new
    text must still name no kind" is the invariant a human re-pinning the
    string would otherwise drop. Documented rather than deleted, and
    documented as redundant rather than as protection.
    """
    note = sm.CAVEATS["kind_scope"]["note"]
    for k in sm.KINDS:
        assert k not in note, (
            f"the note names the kind {k!r}; that is a standing claim which "
            f"will contradict the measured `kinds_produced` after writer 3")
    # ...and it must still do its job, or "names no kind" is satisfied by ""
    assert "MEASURED" in note and "kinds_produced" in note
    assert "clawgate_queue" in note
    assert len(note) > 200


def test_the_kind_scope_NOTE_is_pinned_WHOLE():
    """The structural guard above bans naming a kind; this pins everything
    else, so a reword that keeps the kinds out but drops the pointer to
    `clawgate_queue` — or reintroduces a scope claim in other words — fails."""
    assert sm.CAVEATS["kind_scope"]["note"] == (
        "`kinds_produced` is MEASURED from this scan's rows — read it rather "
        "than assuming. A kind that is enumerated but absent from "
        "`kinds_produced` was NOT LOOKED FOR by this build, so its absence is "
        "NOT a measured absence of that work — UNLESS it is named in "
        "`kinds_excluded_by_filter`, which is where a kind removed by a filter "
        "is reported instead of being silently attributed to the build. For "
        "clawgate use report.clawgate_queue (tasks needing the operator, and "
        "its `stuck_count` for wedged dispatches), a different population from "
        "these rows that is never double-counted with them.")


def test_measured_caveats_detaches_at_EVERY_DEPTH_not_just_the_first():
    """🔴 FIXED ONCE AT DEPTH 1, WALKED AT DEPTH 2. `{k: dict(v)}` copies each
    caveat but leaves every nested dict and list SHARED with the module
    constant: `waiting_signal["excluded"]` is a dict, and `signals` /
    `kinds_enumerated` / `null_fields_on_remote_rows` are lists. Writing into
    any of them in place poisoned CAVEATS process-wide, and the contamination
    surfaced in a LATER scan's rendered line.

    The earlier purity test asserted a top-level REBIND, which a shallow copy
    survives — precisely one level too shallow to see this.
    """
    out = sm.measured_caveats({"hosts": {}})
    # nested dict, mutated IN PLACE (not rebound)
    before_excl = dict(sm.CAVEATS["waiting_signal"]["excluded"])
    out["waiting_signal"]["excluded"]["prompt_buffer_text"] = "POISONED"
    assert sm.CAVEATS["waiting_signal"]["excluded"] == before_excl
    # nested list, mutated IN PLACE
    before_sig = list(sm.CAVEATS["waiting_signal"]["signals"])
    out["waiting_signal"]["signals"].append("POISONED")
    assert sm.CAVEATS["waiting_signal"]["signals"] == before_sig
    before_enum = list(sm.CAVEATS["kind_scope"]["kinds_enumerated"])
    out["kind_scope"]["kinds_enumerated"].append("POISONED")
    assert sm.CAVEATS["kind_scope"]["kinds_enumerated"] == before_enum
    before_null = list(sm.CAVEATS["fuzzyclaw_scope"]["null_fields_on_remote_rows"])
    out["fuzzyclaw_scope"]["null_fields_on_remote_rows"].append("POISONED")
    assert sm.CAVEATS["fuzzyclaw_scope"]["null_fields_on_remote_rows"] \
        == before_null
    # and a SECOND caller still sees the clean constant
    assert sm.measured_caveats({"hosts": {}})["waiting_signal"]["excluded"] \
        == before_excl


def test_the_kind_scope_NOTE_does_not_restate_the_measured_field():
    """🔴 The note ships in the same `--json` payload as `kinds_produced`. It
    used to assert "every row is a tmux pane … cluster is ENUMERATED but NOT
    PRODUCED" — a standing claim that would contradict its own measured
    neighbour the day writer 3 lands. That adjacent-fields-disagree failure is
    what `fuzzyclaw_scope` shipped in #471."""
    note = sm.CAVEATS["kind_scope"]["note"]
    assert "MEASURED" in note
    assert "every row is a tmux pane" not in note
    assert "cluster is ENUMERATED but NOT PRODUCED" not in note
    # it still has to say the durable part, or the field loses its meaning
    assert "NOT a measured absence" in note
    assert "clawgate_queue" in note


def _under_seed(seed, expr):
    """Evaluate `expr` against a freshly-imported session-manager in a
    subprocess with PYTHONHASHSEED pinned. Set iteration order is seed-derived,
    so this is the only way to kill a dropped-`sorted()` mutant DETERMINISTICALLY
    rather than on the runs whose seed happens to disagree."""
    prog = (
        "import importlib.machinery as m, importlib.util as u, json;"
        "l=m.SourceFileLoader('sm', %r);"
        "sp=u.spec_from_file_location('sm', %r, loader=l);"
        "sm=u.module_from_spec(sp); sp.loader.exec_module(sm);"
        "print(json.dumps(%s))" % (_SCRIPT, _SCRIPT, expr))
    env = dict(os.environ, PYTHONHASHSEED=str(seed),
               PYTHONDONTWRITEBYTECODE="1")
    out = subprocess.run([sys.executable, "-c", prog], capture_output=True,
                         text=True, env=env, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def test_summary_classes_sorts_the_non_lead_classes_DETERMINISTICALLY():
    """🔴 SAME NONDETERMINISM TRAP AS THE OTHER SORT, and it was left unguarded
    while the sibling got a seed-pinned harness — rigor applied to one of two
    identical hazards, which is how the unguarded one ships.

    Two non-lead classes are needed to observe order at all, and no other test
    produces two. Under seed 3 the RAW set yields `unknown_kind` first
    (measured), so a `sorted()` that ran returns `cluster` first and a dropped
    one returns `unknown_kind` first — every run, not most.
    """
    summ = {"status": {"busy": {"unknown_kind": 1, "cluster": 1, "claude": 0,
                                "shell": 0, "total": 2}}}
    # POSITIVE CONTROL: seed 3 really does order this set the other way, so a
    # pass below means `sorted()` ran — not that the seed agreed with it.
    raw = subprocess.run(
        [sys.executable, "-c", "print(list({'cluster','unknown_kind'}))"],
        capture_output=True, text=True, timeout=60,
        env=dict(os.environ, PYTHONHASHSEED="3"))
    assert raw.stdout.strip() == "['unknown_kind', 'cluster']", (
        "seed 3 no longer orders this set unknown_kind-first; pick another "
        "seed or this test proves nothing")
    assert _under_seed(3, "sm._summary_classes(%r)" % (summ,)) == [
        "claude", "shell", "cluster", "unknown_kind"]
    # in-process too, so the ordinary path is covered as well
    assert sm._summary_classes(summ) == ["claude", "shell", "cluster",
                                         "unknown_kind"]
    # ...and it is robust to a summary with no status at all
    assert sm._summary_classes({}) == ["claude", "shell"]


def test_the_summary_line_NAMES_every_class_even_when_the_count_is_missing():
    """🔴 The difference between `.get(c, 0)` and the `if c in summ` filter is
    invisible unless a class is ABSENT from the summary — which `render_table`
    reaches on a bare report, the same path that made removing the filter
    outright raise `KeyError`. Filtering SKIPS the class, so the summary line
    silently loses a column the by-status legend one line below still names."""
    text = sm.render_table({"hosts": {}, "summary": {"total_sessions": 0},
                            "fuzzyclaw": {}, "ledger": {}})
    line = next(ln for ln in text.splitlines() if "summary:" in ln)
    assert "claude=0" in line and "shell=0" in line, (
        "a missing count must render as 0, not vanish from the line")


def test_measured_caveats_is_PURE_and_does_not_touch_its_input():
    rep = {"hosts": {"h": {"windows": [{"kind": "cluster"}]}}}
    out = sm.measured_caveats(rep)
    assert out["kind_scope"]["kinds_produced"] == ["cluster"]
    # the constant never GAINS the measured key
    assert "kinds_produced" not in sm.CAVEATS["kind_scope"]
    assert out["kind_scope"] is not sm.CAVEATS["kind_scope"]


def test_deriving_the_caveat_does_not_MUTATE_the_module_constant():
    """`report["caveats"]` is the module-level CAVEATS dict by reference.
    Writing the derived value through it would leak one scan's measurement into
    every later caller in the process — and the tests would still pass, because
    each one gathers its own report."""
    assert "kinds_produced" not in sm.CAVEATS["kind_scope"]
    rep = base_gather()
    rep["caveats"]["kind_scope"]["kinds_produced"] = ["MUTATED"]
    # the constant never gains the key by being written through a report
    assert "kinds_produced" not in sm.CAVEATS["kind_scope"]
    assert base_gather()["caveats"]["kind_scope"]["kinds_produced"] == ["tmux"]


# --------------------------------------------------------------------------- #
# the TABLE — the JSON was made class-generic and the screen was not
# --------------------------------------------------------------------------- #
def test_the_table_summary_line_ACCOUNTS_for_every_row():
    """🔴 `summarize` totals over whatever class keys exist; `render_table`
    summed `claude`/`shell` BY NAME, so a row in any third class vanished from
    a line that still looked like a complete accounting —
    `7 windows claude=3 shell=2`, two rows unmentioned, no discriminant."""
    rep = with_cluster_rows(mix_gather(), cluster_row(status="busy"),
                            cluster_row(status="idle"))
    rep["summary"] = sm.summarize(rep)
    text = sm.render_table(rep)
    line = next(ln for ln in text.splitlines() if "summary:" in ln)
    assert "7 windows" in line
    # every class named, and the parts account for the whole
    assert "claude=3" in line and "shell=2" in line and "cluster=2" in line
    parts = [int(p.split("=")[1]) for p in line.split() if "=" in p]
    assert sum(parts) == 7


def test_the_by_status_line_carries_the_cluster_class_and_its_legend():
    rep = with_cluster_rows(mix_gather(), cluster_row(status="idle"))
    rep["summary"] = sm.summarize(rep)
    text = sm.render_table(rep)
    line = next(ln for ln in text.splitlines() if "by status" in ln)
    assert "(claude+shell+cluster)" in line, "the legend must name every part"
    assert "idle=2+1+1" in line
    assert "idle=4" not in line, "never the mixed integer"


def test_the_table_CLASS_column_renders_cluster_not_shell():
    """The one-rule-one-place failure made visible on one screen: the table
    said `shell` for a row the summary underneath counted as `cluster`."""
    rep = with_cluster_rows(mix_gather(), cluster_row(status="idle",
                                                      task="wedged dispatch"))
    rep["summary"] = sm.summarize(rep)
    text = sm.render_table(rep)
    row = next(ln for ln in text.splitlines() if "wedged dispatch" in ln)
    assert "cluster" in row
    assert "shell" not in row


def test_the_table_totals_stay_IDENTICAL_for_a_tmux_only_scan():
    """The class-generic render must not change today's output. Pinned against
    the literal strings a reader has been reading for months."""
    text = sm.render_table(mix_gather())
    assert "summary: 5 windows  claude=3  shell=2" in text
    assert "by status (claude+shell): " in text
    assert "idle=2+1" in text
    # scoped to the two TOTAL lines — the kind_scope caveat legitimately says
    # "cluster" further down, and asserting over the whole tail would have made
    # this fail for a reason that is not the one it is testing
    totals = "\n".join(ln for ln in text.splitlines()
                       if "summary:" in ln or "by status" in ln)
    assert "cluster" not in totals


def test_the_kind_scope_caveat_MATCHES_what_the_code_actually_produces():
    """🔴 A caveat is a machine-readable claim, and `fuzzyclaw_scope` proved it
    goes stale the moment the code changes — while the guard that should have
    caught it was blinded by a fixture default.

    So this reads the kinds from a REAL gather rather than from a constant, and
    from BOTH shared fixtures, so no single fixture's defaults can make it
    agree by accident. The day writer 3 produces cluster rows, this fails and
    `kinds_produced` must be updated in the same commit.
    """
    produced = set()
    for rep in (base_gather(), mix_gather()):
        produced |= {r["kind"] for h in rep["hosts"].values()
                     for r in h["windows"]}
    assert produced, "no rows measured — this guard would pass vacuously"
    # 🔴 Compared against each report's OWN measured caveat, not against a
    # constant. The constant carries no `kinds_produced` at all now — a
    # literal there was a fake measurement, and comparing to it was comparing
    # the code to a copy of itself.
    for rep in (base_gather(), mix_gather()):
        assert set(rep["caveats"]["kind_scope"]["kinds_produced"]) == {
            r["kind"] for h in rep["hosts"].values() for r in h["windows"]}
    assert set(sm.CAVEATS["kind_scope"]["kinds_enumerated"]) == set(sm.KINDS)
    assert produced < set(sm.KINDS), (
        "cluster is enumerated but must not be produced yet")
    # what the build produces WITHOUT measuring agrees with what it measured
    assert set(sm.KINDS_PRODUCED_BY_CONSTRUCTION) == produced


# =========================================================================== #
# §C — `unsent_prompt`: THE FOURTH SIGNAL, REPORTED SEPARATELY
#
# 🔴 THE MEASUREMENT THAT MOTIVATED IT. A blind dogfood of this tool
# hand-verified its answer against all 79 panes on both hosts and found FIVE
# panes holding text typed at the prompt and never sent — real work one Enter
# from running, some of it hours old. The one-call answer reported none of them.
#
# 🔴 AND THE MEASUREMENT THAT CONSTRAINS IT. The same sweep measured
# `waiting_probable` at 11 flagged / 11 TRUE POSITIVES / ZERO false positives,
# pane by pane. That precision is the most valuable property this tool has, so
# the new signal is published BESIDE `waiting` and never inside it. The first
# test below is the guard on exactly that.
#
# Every fixture here is SYNTHETIC — this repo is PUBLIC. 🔴 Three of them were
# NOT, for the length of this PR: they were real captured drafts, copied out of
# `reference/waiting-signal.md` where the dogfood had quoted them verbatim. §C.4
# below turns this sentence from an assertion into a check, and states the rule
# it protects — NEVER PASTE A CAPTURED DRAFT INTO A COMMITTED FILE.
# =========================================================================== #

# 🔴 MEASURED AT THE BASE SHA (56c0a72), BEFORE `unsent_prompt` EXISTED, and
# transcribed here as a LITERAL. It is deliberately NOT derived from the code it
# guards: a golden computed by calling `detect_waiting` would agree with any
# `detect_waiting`, including a broken one. Every value below was produced by
# running the base-sha module over these same fixtures.
WAITING_GOLDEN_AT_BASE = {
    "PANE_CTX_ZERO": {
        "probable": True,
        "signals": [{"signal": "context_exhausted",
                     "line": "ctx: 0%                           new task? "
                             "/clear to save 610.0k tokens"}]},
    "PANE_IDLE": {"probable": False, "signals": []},
    "PANE_MENU": {
        "probable": True,
        "signals": [{"signal": "selection_menu",
                     "line": "❯ 1. Resume from summary (recommended)"}]},
    "PANE_QUESTION": {
        "probable": True,
        "signals": [{"signal": "trailing_question",
                     "line": "Want me to run the post-deploy check before I "
                             "close this out?"}]},
    "PANE_SHELL": {
        "probable": True,
        "signals": [{"signal": "trailing_question",
                     "line": "no open pull requests. run `gh pr create`?"}]},
    "PANE_TYPED_AT_PROMPT": {"probable": False, "signals": []},
}

# The same snapshot, one layer up: what a REAL scan's waiting roll-up and rows
# looked like at the base sha for `waiting_gather`'s fixture pair.
WAITING_ROLLUP_AT_BASE = {
    "probable": 1, "measured": 2, "unmeasured": 1,
    "per_signal": {"selection_menu": 0, "context_exhausted": 0,
                   "trailing_question": 1},
    "unmeasured_reasons": {"not_claude": 1},
}
WAITING_CELLS_AT_BASE = ["YES trailing_question", "no", "?not_claude"]


def _all_panes():
    return {
        "PANE_CTX_ZERO": PANE_CTX_ZERO,
        "PANE_IDLE": PANE_IDLE,
        "PANE_MENU": PANE_MENU,
        "PANE_QUESTION": PANE_QUESTION,
        "PANE_SHELL": PANE_SHELL,
        "PANE_TYPED_AT_PROMPT": PANE_TYPED_AT_PROMPT,
    }


def _waiting_scan():
    """The scan the waiting-invariance assertions read, so they cannot disagree
    about which run they are describing.

    🔴 THE LOCAL PANE CARRIES A PARKED DRAFT AND THE REMOTE ONE A REAL WAITING
    SIGNAL, and that pairing is what makes the golden DISCRIMINATING rather than
    merely stable. A scan with no draft in it cannot see the single most
    dangerous mutation — folding the parked count into `waiting.probable` —
    because with nothing parked the two roll-ups agree by accident. Here that
    mutation moves `probable` from 1 to 2 and the golden catches it.
    """
    return waiting_gather(local={"%11": PANE_TYPED_AT_PROMPT},
                          remote={"%21": PANE_QUESTION})


def test_the_existing_WAITING_SET_is_byte_identical_after_the_fourth_signal():
    """🔴 THE HARD GUARD ON CHANGE 1, AND THE REASON IT IS THE FIRST TEST HERE.

    `waiting_probable` measured 11 flagged / 11 true positives / ZERO false
    positives across 79 live panes. A new, noisier signal sharing the same
    capture, the same roll-up module and the same table is one careless `or`
    away from degrading that — and the degradation would be invisible, because
    every existing waiting test asserts what waiting SHOULD say, not that it
    still says exactly what it said yesterday.

    So this pins the whole waiting surface against values MEASURED AT THE BASE
    SHA (56c0a72), before `unsent_prompt` existed:

      * `WAITING_SIGNALS` — the closed enumeration, unchanged;
      * `detect_waiting` on all six fixtures — flag AND matched line, whole;
      * `summary.waiting` — the roll-up dict, whole;
      * the per-row waiting triple through a real `gather`;
      * the rendered WAIT cells.

    KILLS: adding `unsent_prompt` to `WAITING_SIGNALS`; summing the parked count
    into `waiting.probable`; letting a draft raise `waiting_probable`; changing
    which line `detect_waiting` reports. Any of those changes a value here.
    """
    assert sm.WAITING_SIGNALS == ("selection_menu", "context_exhausted",
                                  "trailing_question")
    for name, text in sorted(_all_panes().items()):
        assert sm.detect_waiting(text) == WAITING_GOLDEN_AT_BASE[name], name

    rep = _waiting_scan()
    assert rep["summary"]["waiting"] == WAITING_ROLLUP_AT_BASE
    rows = [r for h in sorted(rep["hosts"])
            for r in rep["hosts"][h]["windows"]]
    assert [(r["waiting_probable"], r["waiting_signals"], r["waiting_status"])
            for r in rows] == [
        # laptop naida-dev:1 — a real trailing question
        (True, [{"signal": "trailing_question",
                 "line": "Want me to run the post-deploy check before I close "
                         "this out?"}], "ok"),
        # 🔴 workbench scratch7:3 — THE PARKED DRAFT. `False`, MEASURED, with an
        # EMPTY signal list. This is the row the whole guard is about: at the
        # base sha it read exactly this, and it must still.
        (False, [], "ok"),
        (None, None, "not_claude"),
    ]
    assert [sm._fmt_waiting(r) for r in rows] == WAITING_CELLS_AT_BASE
    # ...and the parked draft really IS there, or the row above is `False` for
    # the boring reason and this fixture proves nothing about the fold.
    assert rows[1]["unsent_prompt"] == "then open the PR"


def test_the_fourth_signal_is_not_IN_the_waiting_signal_set_at_any_layer():
    """🔴 THE STRUCTURAL HALF of the guard above, checked at every layer the two
    signals touch — because the golden pins VALUES and a value can coincide.

    A parked draft must not be reachable from `waiting` through the enumeration,
    the per-signal histogram, or the roll-up's key set.
    """
    assert "unsent_prompt" not in sm.WAITING_SIGNALS
    rep = waiting_gather(local={"%11": PANE_TYPED_AT_PROMPT})
    w = rep["summary"]["waiting"]
    assert set(w) == {"probable", "measured", "unmeasured", "per_signal",
                      "unmeasured_reasons"}
    assert "unsent" not in json.dumps(w)
    # ...and the two roll-ups are SIBLINGS, not nested — nesting is one
    # refactor away from being summed.
    assert "unsent_prompt" in rep["summary"]
    assert "unsent_prompt" not in rep["summary"]["waiting"]


def test_a_PARKED_DRAFT_alone_never_raises_waiting_probable():
    """🔴 THE BEHAVIOURAL HALF, and the one that actually protects the 11/11.

    `PANE_TYPED_AT_PROMPT` is mid-turn with a live spinner and a draft in the
    box: precisely the window a naive detector calls `waiting` and precisely the
    window that is working fine. It must come back `waiting_probable: False`
    (MEASURED, not null — the pane was scraped) while carrying the draft.
    """
    rep = waiting_gather(local={"%11": PANE_TYPED_AT_PROMPT})
    row = _row(rep, "workbench", "scratch7", "3")
    assert row["waiting_probable"] is False
    assert row["waiting_signals"] == []
    assert row["waiting_status"] == "ok"
    # ...and the fact is NOT lost, which is the whole point of the change
    assert row["unsent_prompt"] == "then open the PR"
    assert row["unsent_prompt_status"] == "ok"
    # the roll-ups disagree in exactly the intended direction
    assert rep["summary"]["waiting"]["probable"] == 0
    assert rep["summary"]["unsent_prompt"]["count"] == 1


# --------------------------------------------------------------------------- #
# §C.1 — the detector: scoped to the pane's OWN input line
# --------------------------------------------------------------------------- #
# 🔴 A pane DISPLAYING ANOTHER SESSION'S TRANSCRIPT. This is not a hypothetical
# shape: a live, documented false positive of exactly this class already bit the
# `waiting` scrape — a pane showing another window's output tripped a signal on
# text that was not its own state. Every `❯` line here belongs to somebody else,
# and this pane's OWN input box is empty.
PANE_SHOWING_ANOTHER_TRANSCRIPT = _pane(
    "❯ tail the other window",
    "",
    "● Here is what that session has on screen:",
    "",
    "    ❯ been a couple of days, see whether the patch shipped",
    "    ❯ look at the nightly job",
    "    ❯ park 907 until review",
    "",
    "  That is the end of the captured region.",
    "",
    "✻ Baked for 2m 11s",
    "",
    _RULE,
    "❯ ",
    _RULE,
    "  ctx: 44%",
)

# A draft SO TALL the input box grows past the rule pair. Under-reported as
# `no_input_box` — UNMEASURED — never as an empty box.
PANE_TALL_DRAFT = _pane(
    "● Ready when you are.",
    "",
    _RULE,
    "❯ first line of a long draft",
    "  second line of a long draft",
    "  third line of a long draft",
    "  fourth line of a long draft",
    _RULE,
    "  ctx: 12%",
)

# A two-line draft that DOES fit the box pair — the boundary on the other side.
PANE_TWO_LINE_DRAFT = _pane(
    "● Ready when you are.",
    "",
    _RULE,
    "❯ work out what caused",
    "  the queue backlog",
    _RULE,
    "  ctx: 12%",
)

# 🔴 THE BOUNDARY ITSELF, AND THE ONLY VALUE THAT MEASURES IT. `_input_box_span`
# accepts `rules[-1] - rules[-2] <= 3`. The two fixtures above straddle it at 3
# (in) and 5 (out), so `<= 3` and `<= 4` agree on BOTH of them — the mutant
# survived a fully green suite. Here the rules are exactly 4 apart: the one
# value where the two constants disagree.
PANE_THREE_LINE_DRAFT = _pane(
    "● Ready when you are.",
    "",
    _RULE,
    "❯ first line of the draft",
    "  second line of the draft",
    "  third line of the draft",
    _RULE,
    "  ctx: 12%",
)

# 🔴 A MODAL DRAWN WITH **TWO** RULES. `PANE_MENU` models the shape seen live —
# one rule — which `_input_box_span` rejects outright, so the two-rule variant
# was never exercised: its `❯ 1. …` sits INSIDE a valid box pair and read as a
# parked draft whose text was the option label. Not observed live (both live
# `selection_menu` panes reported `no_input_box`), which is why it is a fixture
# and not a bug report.
PANE_MODAL_TWO_RULES = _pane(
    "● I can take either route here.",
    "",
    "✻ Baked for 9m 41s",
    "",
    _RULE,
    "❯ 1. Resume from summary (recommended)",
    "  2. Resume the full session as-is",
    _RULE,
    "  Enter to confirm · Esc to cancel",
)

# 🔴 THE HARDEST VERSION OF THE "ANOTHER SESSION'S TRANSCRIPT" CLAIM.
# `PANE_SHOWING_ANOTHER_TRANSCRIPT` carries five stray `❯` lines but no stray
# box RULES, so it cannot see `_input_box_span` reading the wrong pair: swapping
# `rules[-2], rules[-1]` for `rules[0], rules[1]` survived it. Here the tailed
# session's own box is rendered too, so the capture holds FOUR rules and only
# the LAST pair is this pane's own box.
PANE_TAILING_A_RENDERED_BOX = _pane(
    "❯ show me that other window",
    "",
    "● That session's screen, verbatim:",
    "",
    "    " + _RULE,
    "    ❯ the other window's own parked draft",
    "    " + _RULE,
    "    ctx: 71%",
    "",
    "  That is the end of the captured region.",
    "",
    "✻ Baked for 3m 40s",
    "",
    _RULE,
    "❯ ",
    _RULE,
    "  ctx: 44%",
)

# A located box whose FIRST interior line is chrome and whose SECOND is the
# input line. `_PROMPT_MARK_RE.match(interior[0])` -> `any(...)` survived every
# fixture, because the only box-we-cannot-read fixture has a ONE-line interior
# and `any` and `match` agree on a single line.
PANE_PROMPT_ON_THE_SECOND_INTERIOR_LINE = _pane(
    "● done",
    "",
    _RULE,
    "  some other chrome",
    "❯ typed after the chrome",
    _RULE,
    "ctx: 9%",
)


def test_INSTRUMENT_the_unsent_fixtures_are_what_they_claim():
    """🔴 INSTRUMENT CHECK before any verdict is read off these fixtures.

    Every assertion below depends on the transcript fixture really containing
    `❯` lines OUTSIDE its own box and an EMPTY box of its own. A fixture that
    quietly had a draft in the box would make the false-positive test pass for
    the wrong reason, and one with no stray `❯` at all would make it vacuous.
    """
    body_prompts = [l for l in PANE_SHOWING_ANOTHER_TRANSCRIPT.splitlines()
                    if "❯" in l]
    assert len(body_prompts) == 5, "need stray ❯ lines or the test is vacuous"
    span = sm._input_box_span(PANE_SHOWING_ANOTHER_TRANSCRIPT.splitlines())
    assert span is not None, "fixture must have a real input box"
    top, bottom = span
    interior = PANE_SHOWING_ANOTHER_TRANSCRIPT.splitlines()[top + 1:bottom]
    assert interior == ["❯ "], "this pane's OWN box must be empty"
    # and the tall draft really is taller than the pair rule
    assert sm._input_box_span(PANE_TALL_DRAFT.splitlines()) is None
    assert sm._input_box_span(PANE_TWO_LINE_DRAFT.splitlines()) is not None


def test_POSITIVE_CONTROL_the_unsent_detector_produces_a_NON_ZERO_count():
    """🔴 MANDATORY, and quoted as a PAIR wherever it is reported. A detector
    returning 0 is indistinguishable from one wired to nothing, so the number is
    watched to MOVE against a set that contains both kinds of pane.

    Here: 2 parked of 8 realistic panes.
    """
    panes = dict(_all_panes(),
                 PANE_SHOWING_ANOTHER_TRANSCRIPT=PANE_SHOWING_ANOTHER_TRANSCRIPT,
                 PANE_TWO_LINE_DRAFT=PANE_TWO_LINE_DRAFT)
    parked = {n for n, t in panes.items()
              if sm.detect_unsent_prompt(t)["text"]}
    assert parked == {"PANE_TYPED_AT_PROMPT", "PANE_TWO_LINE_DRAFT"}
    assert len(parked) == 2, "positive control must be NON-ZERO and exact"


def test_the_detector_reads_the_panes_OWN_input_line_NOT_any_matching_line():
    """🔴 THE FALSE-POSITIVE CLASS THIS DESIGN EXISTS TO AVOID, and it is not a
    hypothetical — a pane displaying another session's transcript already
    tripped the `waiting` scrape on text that was not its own state.

    `PANE_SHOWING_ANOTHER_TRANSCRIPT` carries FIVE `❯` lines, four of them with
    text after them, none of them in its own input box. A detector scanning "any
    line starting with ❯ that has text after it" reports a draft here. The
    structural one reports a MEASURED EMPTY box.

    KILLS: scanning the whole capture; scanning above the box; using the FIRST
    `❯` line rather than the one inside the box.
    """
    got = sm.detect_unsent_prompt(PANE_SHOWING_ANOTHER_TRANSCRIPT)
    assert got == {"status": "ok", "text": None}
    # PANE_IDLE is the same hazard in its commonest form: the operator's OWN
    # submitted prompt, echoed into the scrollback above the box.
    assert sm.detect_unsent_prompt(PANE_IDLE) == {"status": "ok", "text": None}
    # ...and the positive control on the same instrument, so "None" is not
    # simply what this function always returns.
    assert sm.detect_unsent_prompt(PANE_TYPED_AT_PROMPT) == {
        "status": "ok", "text": "then open the PR"}


def test_the_detector_returns_the_TEXT_so_an_operator_can_triage():
    """🔴 A BOOLEAN WOULD NOT HAVE CLOSED THE DOGFOOD'S GAP. Five parked panes
    with five different drafts need five different decisions, and "something is
    typed here" costs a `tail` per pane to resolve — which is the manual sweep
    this signal exists to replace.

    Pinned as WHOLE strings, and the two fixtures carry DIFFERENT text so a
    mutant returning a constant, or the wrong pane's draft, cannot satisfy both.
    """
    assert sm.detect_unsent_prompt(PANE_TYPED_AT_PROMPT)["text"] == \
        "then open the PR"
    assert sm.detect_unsent_prompt(PANE_TWO_LINE_DRAFT)["text"] == \
        "work out what caused the queue backlog"


def test_a_MODAL_pane_is_no_input_box_and_NOT_a_measured_empty_draft():
    """🔴 THE NULL-VS-ZERO RULE, ONE SIGNAL OVER. A modal REPLACES the input
    box, so there is no box to read — and `text: None, status: "ok"` would
    publish "this window has nothing parked" about a window whose box was never
    on screen. `no_input_box` is the honest answer and it is NOT counted as
    measured.
    """
    got = sm.detect_unsent_prompt(PANE_MENU)
    assert got == {"status": "no_input_box", "text": None}
    # ...and `waiting` measured that SAME capture perfectly well, which is why
    # the two signals need separate statuses rather than one shared one.
    assert sm.detect_waiting(PANE_MENU)["probable"] is True


def test_a_draft_TALLER_than_the_box_is_UNMEASURED_never_empty():
    """🔴 THE STATED LIMIT, PINNED. `_input_box_span` accepts a rule pair up to
    three lines apart, so a draft occupying more than two rendered lines makes
    the box taller than that and is not recognised.

    Under-reporting into the UNMEASURED bucket is the safe direction; the
    failure this must never have is answering `ok`/None — "I looked in the box
    and it was empty" — about a box holding four lines of work.

    The two-line fixture is the control on the other side of the boundary: one
    point is not a claim about a threshold.
    """
    assert sm.detect_unsent_prompt(PANE_TALL_DRAFT) == {
        "status": "no_input_box", "text": None}
    assert sm.detect_unsent_prompt(PANE_TWO_LINE_DRAFT)["status"] == "ok"
    assert sm.detect_unsent_prompt(PANE_TWO_LINE_DRAFT)["text"]


def test_the_box_HEIGHT_boundary_is_pinned_AT_FOUR_the_only_value_that_moves():
    """🔴 ONE MEASUREMENT IS NOT A CLAIM ABOUT A THRESHOLD, and this threshold
    had two measurements that could not see it. `_input_box_span` accepts
    `rules[-1] - rules[-2] <= 3`; the existing pair measures 3 (accepted) and 5
    (rejected), and `<= 4` gives the SAME answer on both — so the mutant
    survived 553 green tests.

    Four is the boundary. It is pinned at BOTH consumers of the constant,
    because the same span now decides where `last_assistant_line` cuts: a future
    one-off edit here would silently retune `waiting_probable` too, and a test
    that watched only the draft would not notice.
    """
    lines = PANE_THREE_LINE_DRAFT.splitlines()
    rules = [i for i, l in enumerate(lines) if sm._RULE_RE.match(l)]
    # INSTRUMENT: the fixture really is the boundary, or this proves nothing
    assert len(rules) == 2 and rules[1] - rules[0] == 4, rules

    assert sm._input_box_span(lines) is None
    assert sm.detect_unsent_prompt(PANE_THREE_LINE_DRAFT) == {
        "status": "no_input_box", "text": None}
    # ...and the OTHER consumer moves with it. With no span the cut falls at the
    # bottom rule, so the transcript still ends inside the unrecognised box;
    # under `<= 4` the cut moves to the TOP rule and this becomes the assistant
    # sentence above it. Two different strings, one constant.
    assert sm.last_assistant_line(PANE_THREE_LINE_DRAFT) == \
        "  third line of the draft"
    # the three points, so the claim carries its own scope: 3 in, 4 out, 5 out
    assert sm._input_box_span(PANE_TWO_LINE_DRAFT.splitlines()) is not None
    assert sm._input_box_span(PANE_TALL_DRAFT.splitlines()) is None


def test_a_MODAL_drawn_with_TWO_rules_is_still_no_input_box_not_a_draft():
    """🔴 `❯ 1. Resume …` IS A MENU'S SELECTED OPTION, NOT TYPED WORK, and
    `_PROMPT_MARK_RE` cannot tell them apart — a menu row opens with the same
    marker. The live modal is drawn with ONE rule, which `_input_box_span`
    rejects for free; a two-rule variant puts the option INSIDE a valid box pair
    and it was read as a parked draft whose text was the option label.

    Not observed live — both live `selection_menu` panes reported
    `no_input_box`, as do bordered `│ ❯ …` modals — so this is a fixture for a
    shape the repo modelled only in its easy form.

    KILLS: dropping the menu guard; keying it on the selected marker ALONE (a
    lone `❯ 1.` is ordinary typing and must stay a draft). It does NOT kill the
    other half — see `test_the_menu_guard_needs_the_MARKER_half_too_...` below,
    which is where the marker check is pinned.
    """
    # INSTRUMENT: it really is a box pair, i.e. the span check does NOT already
    # answer this — otherwise the guard below is unreachable and untested.
    assert sm._input_box_span(PANE_MODAL_TWO_RULES.splitlines()) is not None
    assert sm.detect_unsent_prompt(PANE_MODAL_TWO_RULES) == {
        "status": "no_input_box", "text": None}
    # ...and `waiting` still calls it what it is, from the same two lines
    assert [s["signal"] for s in
            sm.detect_waiting(PANE_MODAL_TWO_RULES)["signals"]] == \
        ["selection_menu"]
    # 🔴 THE CONTROL THE GUARD MUST NOT SWALLOW: a lone numbered line in the box
    # with NO second option is a draft, and stays one.
    lone = _pane("● ok", "", _RULE, "❯ 1. rewrite the intro paragraph", _RULE,
                 "  ctx: 5%")
    assert sm.detect_unsent_prompt(lone) == {
        "status": "ok", "text": "1. rewrite the intro paragraph"}


def test_the_menu_guard_needs_the_MARKER_half_too_not_only_the_SECOND_OPTION():
    """🔴 HALF THE GUARD'S CONJUNCTION WAS UNGUARDED, and it is the half with
    the blast radius. Replacing `_MENU_SELECTED_RE.match(interior[0])` with a
    tautology SURVIVED all 563 tests: every fixture that reaches the option
    count either has a menu marker in the box (so the mutant agrees) or fewer
    than two numbered lines (so the count decides). Nothing built the shape
    where the two disagree.

    That shape is COMMON, not exotic: an ordinary parked draft in a pane whose
    transcript happens to hold two numbered lines — an agent listing options,
    quoting a checklist, printing a `1.`/`2.` plan. With the marker check gone,
    every one of those becomes `no_input_box`, i.e. the draft is silently
    unmeasured, with a fully green suite. This is the guard on the behaviour
    this PR exists to add, so it gets its own fixture.

    KILLS: `_MENU_SELECTED_RE.match(interior[0])` -> `True` (or its deletion,
    leaving the option count alone to decide).
    """
    drafting_below_a_quoted_list = _pane(
        "● I was going to offer you these:",
        "",
        "  1. rebuild the index from scratch",
        "  2. leave the stale entries alone",
        "",
        "  ...but you already said which one you wanted.",
        "",
        _RULE,
        "❯ carry on with the second one",
        _RULE,
        "  ctx: 33%",
    )
    lines = drafting_below_a_quoted_list.splitlines()
    # INSTRUMENT, both halves — or the mutant is unreachable and this is vacuous
    span = sm._input_box_span(lines)
    assert span is not None, "the box must be FOUND, or the guard never runs"
    interior = lines[span[0] + 1:span[1]]
    assert not sm._MENU_SELECTED_RE.match(interior[0]), (
        "the box's first line must NOT look like a selected option, or the "
        "mutant and the real code agree here and the kill proves nothing")
    assert sum(1 for l in lines if sm._MENU_OPTION_RE.match(l)) >= 2, (
        "two numbered lines must be in the capture, or the OTHER half of the "
        "conjunction rejects this pane and the marker check is never reached")

    # the behaviour: a draft, read whole, NOT swallowed as a modal
    assert sm.detect_unsent_prompt(drafting_below_a_quoted_list) == {
        "status": "ok", "text": "carry on with the second one"}


def test_the_SECOND_OPTION_is_counted_ANYWHERE_in_the_capture_not_in_the_box():
    """🔴 THE SCOPE OF THE OPTION COUNT IS A CLAIM, AND IT WAS UNMEASURED.
    Narrowing `sum(... for l in lines ...)` to `... for l in interior ...`
    SURVIVED all 563 — `PANE_MODAL_TWO_RULES` puts BOTH options inside the box,
    so the two scopes agree on it and no other fixture reaches the count with a
    marker in the box.

    That narrowing is a real semantic change, not a refactor: it removes the
    documented cost (a draft that really begins `1. …` beside another numbered
    line reads as UNMEASURED) and it breaks the coupling the source comment
    claims — that `detect_unsent_prompt` and `detect_waiting` use the SAME
    definition of a menu, `selected marker + a second numbered option ANYWHERE
    in the capture`, so the two signals cannot form independent opinions about
    what a modal is. Both halves are asserted here rather than described.

    KILLS: `lines` -> `interior` in the option count.
    """
    # A draft that really begins `1. …`, with the second numbered line OUTSIDE
    # the box. This is the documented COST of the wide scope, so pinning it is
    # pinning the scope.
    cost = _pane(
        "● Two things came up:",
        "",
        "  1. the index rebuild",
        "  2. the stale worktrees",
        "",
        _RULE,
        "❯ 1. rebuild the index",
        _RULE,
        "  ctx: 33%",
    )
    lines = cost.splitlines()
    span = sm._input_box_span(lines)
    assert span is not None
    interior = lines[span[0] + 1:span[1]]
    # INSTRUMENT: the two scopes must DISAGREE on this fixture, or narrowing the
    # scope changes nothing here and a green is green for the wrong reason.
    assert sum(1 for l in lines if sm._MENU_OPTION_RE.match(l)) >= 2
    assert sum(1 for l in interior if sm._MENU_OPTION_RE.match(l)) < 2
    assert sm._MENU_SELECTED_RE.match(interior[0]), (
        "the marker half must be TRUE here, or the conjunction short-circuits "
        "before the count and the scope is never exercised")

    assert sm.detect_unsent_prompt(cost) == {
        "status": "no_input_box", "text": None}
    # 🔴 THE COUPLING, MEASURED RATHER THAN ASSERTED: `detect_waiting` reads the
    # same two lines and reaches the same verdict about the same pane. If the
    # scopes ever diverge, these two disagree and this line fails.
    assert [s["signal"] for s in sm.detect_waiting(cost)["signals"]] == \
        ["selection_menu"]
    # ...and the `lone` control still holds under the wide scope: one numbered
    # line in the whole capture is a draft, not a menu.
    lone = _pane("● ok", "", _RULE, "❯ 1. rewrite the intro paragraph", _RULE,
                 "  ctx: 5%")
    assert sum(1 for l in lone.splitlines()
               if sm._MENU_OPTION_RE.match(l)) == 1
    assert sm.detect_unsent_prompt(lone)["status"] == "ok"


def test_a_pane_tailing_another_sessions_RENDERED_BOX_reads_its_OWN_box():
    """🔴 THE SAME FALSE-POSITIVE CLASS AS `PANE_SHOWING_ANOTHER_TRANSCRIPT`, in
    the version that fixture could not reach. That one carries five stray `❯`
    lines and NO stray box rules, so it cannot see `_input_box_span` picking the
    WRONG PAIR — `rules[-2], rules[-1]` -> `rules[0], rules[1]` survived it.

    Here the tailed session's own input box is on screen, so the capture holds
    FOUR rules. The real code handles this correctly today; this pins working
    behaviour rather than reporting a bug.
    """
    lines = PANE_TAILING_A_RENDERED_BOX.splitlines()
    rules = [i for i, l in enumerate(lines) if sm._RULE_RE.match(l)]
    # INSTRUMENT: four rules, two candidate pairs, or the mutant is unreachable
    assert len(rules) == 4, rules
    assert rules[1] - rules[0] <= 3 and rules[3] - rules[2] <= 3, (
        "BOTH pairs must look like a box, or picking the first is not a "
        "temptation the code could fall for")
    assert sm._input_box_span(lines) == (rules[2], rules[3])
    assert sm.detect_unsent_prompt(PANE_TAILING_A_RENDERED_BOX) == {
        "status": "ok", "text": None}
    # the other window's draft text is REALLY in the capture, so "None" above is
    # a scope decision and not an empty fixture
    assert "the other window's own parked draft" in PANE_TAILING_A_RENDERED_BOX


def test_the_prompt_marker_must_be_the_boxs_FIRST_interior_line_not_ANY_of_them():
    """🔴 `match(interior[0])` -> `any(match(l) for l in interior)` SURVIVED,
    because the only box-we-cannot-read fixture has a ONE-LINE interior, where
    the two are the same function.

    A two-line interior whose SECOND line carries the marker is the case that
    separates them. It must be `no_input_box`: we located a box we do not
    understand, and gluing its chrome to the text after the marker would publish
    `some other chrome ❯ typed after the chrome` as a draft.
    """
    lines = PANE_PROMPT_ON_THE_SECOND_INTERIOR_LINE.splitlines()
    span = sm._input_box_span(lines)
    assert span is not None, "the box must be FOUND, or the guard is unreachable"
    interior = lines[span[0] + 1:span[1]]
    # INSTRUMENT: two lines, marker on the second only — the shape `any` needs
    assert len(interior) == 2
    assert not sm._PROMPT_MARK_RE.match(interior[0])
    assert sm._PROMPT_MARK_RE.match(interior[1])
    assert sm.detect_unsent_prompt(PANE_PROMPT_ON_THE_SECOND_INTERIOR_LINE) == {
        "status": "no_input_box", "text": None}


def test_a_box_whose_first_line_is_not_the_input_line_is_unmeasured():
    """A located box we cannot read is `no_input_box`, never a draft. KILLS:
    dropping the prompt-marker check and treating any box interior as text."""
    weird = _pane("● done", "", _RULE, "  some other chrome", _RULE, "ctx: 9%")
    assert sm.detect_unsent_prompt(weird) == {"status": "no_input_box",
                                              "text": None}
    # an empty capture has no box at all
    assert sm.detect_unsent_prompt("") == {"status": "no_input_box",
                                           "text": None}
    assert sm.detect_unsent_prompt(None) == {"status": "no_input_box",
                                             "text": None}


def test_the_input_box_span_is_the_ONE_definition_shared_with_the_transcript_cut():
    """🔴 ONE RULE, ONE PLACE — and here it is a correctness property, not
    hygiene. `last_assistant_line` cuts the transcript at the box's TOP rule and
    `detect_unsent_prompt` reads BETWEEN the rules; two independent opinions
    about where the box is would put a draft in the transcript or an assistant
    sentence in the draft.

    So: for every fixture with a box, the span's top is exactly where the
    transcript cut lands, proven by the assistant line never being a box line.
    """
    for name, text in sorted(_all_panes().items()):
        lines = text.splitlines()
        span = sm._input_box_span(lines)
        if span is None:
            continue
        top, bottom = span
        last = sm.last_assistant_line(text)
        assert last is None or last in lines[:top], (
            f"{name}: the assistant line came from inside/below the box")
        # and the draft, if any, comes from strictly inside the box
        draft = sm.detect_unsent_prompt(text)["text"]
        if draft:
            assert any(draft in l for l in lines[top + 1:bottom]), name


# --------------------------------------------------------------------------- #
# §C.2 — the row fields, the statuses, and the roll-up
# --------------------------------------------------------------------------- #
def test_the_unsent_status_vocabulary_is_CLOSED_and_every_value_is_REACHABLE():
    """🔴 A STRUCTURAL LEDGER TYPE-CHECKS PAST A STATUS NOTHING CAN EMIT, so
    both halves are here: the enumeration is pinned, AND every value in it gets
    a scan that MUST produce it.

    `error` is the one that would otherwise be declared-and-dead — it needs a
    capture batch that fails on a REACHABLE host, which no other test builds.
    """
    assert sm.UNSENT_STATUSES == ("ok", "no_input_box", "uncaptured",
                                  "not_claude", "skipped", "error")
    produced = {}
    # ok + not_claude: a normal framed scan (misc:5 is the bare shell)
    rep = waiting_gather(local={"%11": PANE_IDLE})
    produced["ok"] = _row(rep, "workbench", "scratch7", "3")
    produced["not_claude"] = _row(rep, "workbench", "misc", "5")
    # no_input_box: the same pane captured on a modal
    produced["no_input_box"] = _row(
        waiting_gather(local={"%11": PANE_MENU}), "workbench", "scratch7", "3")
    # uncaptured: the batch ran, this pane's marker is absent from it
    produced["uncaptured"] = _row(
        waiting_gather(local={"%99": PANE_IDLE}), "workbench", "scratch7", "3")
    # skipped: --no-capture
    produced["skipped"] = _row(base_gather(use_capture=False),
                               "workbench", "scratch7", "3")
    # error: the host answered list-panes but the capture call failed
    produced["error"] = _row(
        base_gather(runner=make_runner(local_capture_rc=1,
                                       local_capture_err="capture blew up")),
        "workbench", "scratch7", "3")
    got = {want: row["unsent_prompt_status"] for want, row in produced.items()}
    assert got == {k: k for k in got}, got
    assert set(got) == set(sm.UNSENT_STATUSES), (
        "a declared status has no reachability case: %r"
        % sorted(set(sm.UNSENT_STATUSES) ^ set(got)))
    # 🔴 and NONE of them carries a text — every non-`ok` status must be null,
    # or the status stops being the discriminant that makes the null readable.
    for want, row in produced.items():
        if want != "ok":
            assert row["unsent_prompt"] is None, want


def test_the_unsent_rollup_count_is_None_NEVER_zero_when_nothing_was_measured():
    """🔴 THE SENTENCE THIS TOOL MUST NEVER EMIT, applied to the fourth signal.

    "0 windows have work parked" and "0 windows had their input box read" are
    different facts, and the first is the one an operator acts on. Five parked
    windows went unreported on 79 live panes; a fabricated zero here would
    re-create that blind spot while ASSERTING the check had been done.

    Three ways to measure nothing, all of which must be None — and the measured
    zero as the control, which must be 0 and not None.
    """
    for rep in (base_gather(use_capture=False),
                base_gather(runner=make_runner(local_capture_rc=1,
                                               remote_capture_rc=1)),
                waiting_gather(local={"%99": PANE_IDLE},
                               remote={"%98": PANE_IDLE})):
        u = rep["summary"]["unsent_prompt"]
        assert u["count"] is None, u
        assert u["measured"] == 0
        assert u["unmeasured"] > 0
        assert u["unmeasured_reasons"], "a None must name its reason"
    # 🔴 THE CONTROL: boxes really were read and really held nothing. This one
    # is 0, NOT None — without it, a mutant hardcoding None would survive every
    # assertion above.
    measured_empty = waiting_gather(local={"%11": PANE_IDLE},
                                    remote={"%21": PANE_QUESTION})
    u = measured_empty["summary"]["unsent_prompt"]
    assert u["count"] == 0
    assert u["measured"] == 2


def test_the_unsent_rollup_counts_only_boxes_it_READ_not_panes_it_captured():
    """🔴 `measured` HERE IS STRICTER THAN `waiting`'s, and that is the point of
    giving the signal its own status rather than reusing `waiting_status`.

    A pane sitting on a modal captured fine — `waiting` measured it — and its
    input box was never on screen. Counting it as a read box would publish a
    denominator that overstates what was looked at.
    """
    rep = waiting_gather(local={"%11": PANE_MENU}, remote={"%21": PANE_IDLE})
    assert rep["summary"]["waiting"]["measured"] == 2
    assert rep["summary"]["unsent_prompt"]["measured"] == 1
    assert rep["summary"]["unsent_prompt"]["unmeasured_reasons"] == {
        "no_input_box": 1, "not_claude": 1}


def test_a_row_MISSING_the_status_field_is_counted_loudly_not_crashed_on():
    """🔴 REGRESSION, and it was a real crash — IN BOTH ROLL-UPS. `_unsent_
    rollup` and `_waiting_rollup` each histogram a status field; a row built
    OUTSIDE `fold_windows` (a `cluster` row from writer 3 is the live example)
    carries neither key, and a `None` histogram key is unsortable beside the
    real string keys — `render_table` raised `TypeError` on a report that was
    otherwise fine.

    🔴 THE FIRST FIX TOOK ONE OF THE TWO. Its own comment generalised the
    diagnosis correctly ("a row built anywhere ELSE need not set the field") and
    then coerced `_unsent_rollup` only; `_waiting_rollup` kept the identical
    crash, invisible because `cluster_row` used to hand waiting a string while
    leaving unsent's field absent. Both are asserted here, off ONE symmetric
    fixture, so neither can be fixed alone again.

    Coerced to `"none"`, the same idiom as `age_sources` and `row_kind`: a row
    missing the field is a BUG in whatever built it, so it is COUNTED and NAMED
    in the not-measured bucket rather than dropped or fatal.
    """
    rep = with_cluster_rows(mix_gather(), cluster_row(status="busy"))
    rep["summary"] = sm.summarize(rep)
    # INSTRUMENT: the injected row really carries NEITHER field, and the report
    # really holds a string-keyed reason beside it — a histogram of `{None: 1}`
    # alone sorts fine, so a fixture without the string key cannot crash.
    injected = [r for r in rep["hosts"]["workbench"]["windows"]
                if r.get("kind") == "cluster"]
    assert len(injected) == 1
    assert "waiting_status" not in injected[0]
    assert "unsent_prompt_status" not in injected[0]
    for key in ("waiting", "unsent_prompt"):
        reasons = rep["summary"][key]["unmeasured_reasons"]
        assert reasons.get("none") == 1, (key, reasons)
        assert set(reasons) - {"none"}, (
            "need a real string key beside `none`, or `sorted()` never has "
            "two types to compare and this fixture cannot see the crash: %r"
            % reasons)
    # and the renderer survives it — the actual regression
    text = sm.render_table(rep)
    assert "unsent prompts:" in text


def test_every_rollup_UNMEASURED_equals_the_sum_of_its_OWN_reason_histogram():
    """🔴 THE NUMBER AND THE HISTOGRAM BESIDE IT MUST AGREE, and nothing pinned
    that. Computing `summary.unsent_prompt.unmeasured` off `waiting_status`
    instead of `unsent_prompt_status` stayed green and rendered

        0 parked of 1 box(es) read (1 not read: no_input_box=1, not_claude=1)

    — "1 not read", two reasons — which is a self-contradicting line an operator
    reads as a bug in the tool, or worse, does not notice.

    The two roll-ups take their unmeasured set from DIFFERENT fields, so the
    fixture is chosen to make the two numbers differ.

    🔴 CORRECTED — THIS DOCSTRING CLAIMED A SYMMETRY THAT IS FALSE IN ONE
    DIRECTION, and it was false as MEASURED, not as a quibble. It used to end
    "cross-wiring either one to the other's field breaks its own sum". True of
    `_unsent_rollup`, whose set and key are the SAME field. Not true of
    `_waiting_rollup`: it FILTERS on `waiting_probable is None` and only KEYS on
    `waiting_status`, so the row SET is field-independent and the sum holds
    whichever field supplies the key. Cross-wiring that key to
    `unsent_prompt_status` SURVIVES all 563 tests, this one included.

    What it breaks is the VOCABULARY, not the arithmetic:
    `summary.waiting.unmeasured_reasons` would report the other signal's status
    names while the counts still add up. That is what
    `test_each_rollups_unmeasured_reasons_are_keyed_by_its_OWN_status_field`
    below pins, and it is where the cross-wire is killed — this test is an
    invariant guard on the sum, and only on the sum.
    """
    # a modal locally (waiting CAN read it, unsent cannot) + a shell row
    rep = waiting_gather(local={"%11": PANE_MENU}, remote={"%21": PANE_IDLE})
    w = rep["summary"]["waiting"]
    u = rep["summary"]["unsent_prompt"]
    # 🔴 THE TWO DIFFER ON PURPOSE (1 vs 2). A fixture where they coincide
    # cannot see a roll-up reading the other's field — the fixture-equals-the-
    # constant trap, in its cross-wiring form.
    assert (w["unmeasured"], u["unmeasured"]) == (1, 2)
    for key in ("waiting", "unsent_prompt"):
        roll = rep["summary"][key]
        assert roll["unmeasured"] == sum(roll["unmeasured_reasons"].values()), (
            key, roll)
    # ...and again on a scan whose unmeasured set is EVERYTHING, so the
    # invariant is not pinned only at a small number
    for rep2 in (base_gather(use_capture=False),
                 with_cluster_rows(mix_gather(), cluster_row())):
        rep2["summary"] = sm.summarize(rep2)
        for key in ("waiting", "unsent_prompt"):
            roll = rep2["summary"][key]
            assert roll["unmeasured"] == sum(
                roll["unmeasured_reasons"].values()), (key, roll)
    # POSITIVE CONTROL ON THE INVARIANT: it CAN fail. A histogram that dropped
    # one bucket must break the equality, or `sum(...)` proves nothing here.
    broken = dict(u, unmeasured_reasons={"not_claude": 1})
    assert broken["unmeasured"] != sum(broken["unmeasured_reasons"].values())


def test_each_rollups_unmeasured_reasons_are_keyed_by_its_OWN_status_field():
    """🔴 THE SUM CANNOT SEE THIS, AND THE SUM IS ALL THERE WAS. `_waiting_
    rollup` filters its unmeasured set on `waiting_probable is None` and keys
    the histogram on `waiting_status` — two different fields — so swapping the
    KEY to `unsent_prompt_status` leaves the row set, and therefore the sum,
    identical. That mutant SURVIVED all 563 tests. The damage it does is a
    histogram of the OTHER signal's status names printed under
    `summary.waiting.unmeasured_reasons`, with a total that still adds up.

    No pane can build the disagreement: `fold_windows` sets `waiting_status` and
    `unsent_prompt_status` to the SAME value on every path where nothing was
    scraped (`not_claude`/`uncaptured`/`skipped`/`error`), and the one status
    that is unsent's alone — `no_input_box` — only occurs on rows where waiting
    WAS measured, so they are absent from waiting's set entirely. The
    disagreement lives exactly where the last two histogram bugs lived: a row
    built OUTSIDE `fold_windows`, carrying one field and not the other. That is
    not hypothetical — `cluster_row` used to be shaped that way, with
    `waiting_status="not_tmux"` and no `unsent_prompt_status`, and that
    asymmetry hid a `TypeError` for a whole PR.

    So the fixture is one row of each asymmetry, and the assertion is on the
    reason NAMES. Both directions, because a guard that pins one is half a
    guard.

    KILLS: `_waiting_rollup`'s histogram keyed on `unsent_prompt_status`;
    `_unsent_rollup`'s keyed on `waiting_status`.
    """
    rep = with_cluster_rows(
        mix_gather(),
        # writer 3 set waiting's field and not unsent's — the real shape
        cluster_row(waiting_status="not_tmux"),
        # ...and the mirror, so neither direction is pinned alone
        cluster_row(unsent_prompt_status="no_input_box"),
    )
    rep["summary"] = sm.summarize(rep)
    w = rep["summary"]["waiting"]["unmeasured_reasons"]
    u = rep["summary"]["unsent_prompt"]["unmeasured_reasons"]

    # INSTRUMENT: the two vocabularies must be DISJOINT on this fixture, or a
    # cross-wire produces the same histogram and the assertions below are
    # vacuous. `not_tmux` is deliberately outside `UNSENT_STATUSES` and
    # `no_input_box` is deliberately outside anything waiting can emit.
    assert "not_tmux" not in sm.UNSENT_STATUSES
    assert w.get("not_tmux") == 1, w
    assert "not_tmux" not in u, u
    assert u.get("no_input_box") == 1, u
    assert "no_input_box" not in w, w
    # the field-less halves land in `none` on the OTHER roll-up, loudly
    assert w.get("none") == 1 and u.get("none") == 1, (w, u)

    # ...and the sum invariant still holds, so this is a claim ABOUT the names
    # and not a second copy of the test above
    for key in ("waiting", "unsent_prompt"):
        roll = rep["summary"][key]
        assert roll["unmeasured"] == sum(roll["unmeasured_reasons"].values())
    # the renderer survives a status outside either vocabulary
    assert "unsent prompts:" in sm.render_table(rep)


def test_the_parked_text_reaches_the_LEAN_view_untruncated():
    """The lean view's sole consumer is an agent triaging without opening panes.
    A flag it cannot act on would be the LOSSY-table failure this view replaces.
    """
    rep = waiting_gather(local={"%11": PANE_TYPED_AT_PROMPT})
    lean = sm.lean_report(rep)
    row = [r for r in lean["hosts"]["workbench"]["windows"]
           if r["session"] == "scratch7"][0]
    assert row["unsent_prompt"] == "then open the PR"
    assert row["unsent_prompt_status"] == "ok"
    assert "unsent_prompt" in lean["summary"]


# --------------------------------------------------------------------------- #
# §C.3 — the rendered surfaces, pinned WHOLE
# --------------------------------------------------------------------------- #
def _lines(text):
    return [l.strip() for l in text.splitlines()]


_UNSENT_HEADING = ("✎ UNSENT PROMPT — typed and never sent; NOT 'waiting on "
                   "you', but work parked one Enter away:")
_WAITING_HEADING = "⚠ WAITING — the matched line, so you can disagree with it:"


def _block_after(lines, heading):
    """The rows of ONE rendered block: every line after `heading` up to the
    first blank one. Returned WHOLE, so a membership claim about a block is an
    equality and not an `in` — an `in` cannot see an extra row.
    """
    assert heading in lines, f"no such block: {heading!r}"
    out = []
    for line in lines[lines.index(heading) + 1:]:
        if not line:
            break
        out.append(line)
    return out


def test_the_unsent_TABLE_SECTION_is_pinned_as_a_WHOLE_normalised_string():
    """🔴 A WORD CHECK IS WALKABLE BY A REWORD, and four prose guards in this
    repo have been walked exactly that way — two satisfied by the sentence's own
    STATIC prose, one by a reword, one by a synonym. So the heading and the row
    are pinned WHOLE.

    The cost is real and accepted: a cosmetic reword fails this test. That is
    the trade for a machine-readable claim — and the claim here is load-bearing,
    because the heading is what stops a reader filing these under "waiting".
    """
    text = sm.render_table(waiting_gather(local={"%11": PANE_TYPED_AT_PROMPT}))
    lines = _lines(text)
    assert ("✎ UNSENT PROMPT — typed and never sent; NOT 'waiting on you', "
            "but work parked one Enter away:") in lines
    assert "workbench:scratch7:3  then open the PR" in lines
    # 🔴 and it is a SEPARATE block from the waiting evidence, not merged into
    # it — the one surface a human actually reads is where re-conflating the
    # two signals would cost the most.
    assert "⚠ WAITING — the matched line, so you can disagree with it:" \
        not in lines
    # an unparked scan prints no block at all
    assert not [l for l in _lines(sm.render_table(
        waiting_gather(local={"%11": PANE_IDLE}))) if "UNSENT PROMPT" in l]


def test_the_unsent_TABLE_SECTION_excludes_a_WAITING_row_with_no_draft():
    """🔴 THE HEADLINE GUARANTEE, AT THE ONE SURFACE A HUMAN ACTUALLY READS —
    and it was untested there. Widening the row filter to
    `r.get("unsent_prompt") or r.get("waiting_probable")` passed the ENTIRE
    suite and rendered a waiting row, holding no draft at all, underneath a
    heading that says "typed and never sent".

    The pinned-whole-string test could not see it: its fixture is a single pane
    with `waiting_probable: False`, so the `or` had nobody to add. This one uses
    the scan that carries BOTH — a real waiting row on one host and a parked
    draft on the other — and asserts the block WHOLE, so an extra row fails.

    That is what separation means at this surface: not that the two never
    co-occur (they may, and a row carrying both is correct), but that neither
    list can be populated from the other's signal.
    """
    rep = _waiting_scan()
    rows = [r for h in sorted(rep["hosts"]) for r in rep["hosts"][h]["windows"]]
    # INSTRUMENT, both halves — a fixture missing either makes this vacuous
    waiting_only = [r for r in rows
                    if r.get("waiting_probable") and not r.get("unsent_prompt")]
    parked_only = [r for r in rows
                   if r.get("unsent_prompt") and not r.get("waiting_probable")]
    assert len(waiting_only) == 1 and waiting_only[0]["session"] == "naida-dev"
    assert len(parked_only) == 1 and parked_only[0]["session"] == "scratch7"

    lines = _lines(sm.render_table(rep))
    # 🔴 WHOLE-BLOCK EQUALITY, not `in`. The waiting row would render as a bare
    # `laptop:naida-dev:1` with an empty draft column, which every `in` check in
    # this file would step straight past.
    assert _block_after(lines, _UNSENT_HEADING) == [
        "workbench:scratch7:3  then open the PR"]
    # ...and the mirror, so the guard is not one-directional: the parked-only
    # row must not appear under the WAITING evidence either.
    waiting_block = _block_after(lines, _WAITING_HEADING)
    assert waiting_block == [
        "laptop:naida-dev:1  [trailing_question]  Want me to run the "
        "post-deploy check before I close this out?"]
    # POSITIVE CONTROL ON THE BLOCK READER: it really can see a second row, so
    # the two one-element results above are measurements, not a broken parser.
    both = _block_after(
        _lines(sm.render_table(waiting_gather(
            local={"%11": PANE_TYPED_AT_PROMPT},
            remote={"%21": PANE_TWO_LINE_DRAFT}))),
        _UNSENT_HEADING)
    assert len(both) == 2, both


def test_a_row_carrying_BOTH_signals_is_listed_under_BOTH_headings():
    """🔴 CO-OCCURRENCE IS CORRECT AND MUST NOT BE SUPPRESSED. The agent asked a
    question and the operator half-typed a reply — that is one window with two
    true facts about it, and a build that dropped either would be strictly worse
    than one that prints both.

    An earlier version of the reference doc promoted "0 rows flagged BOTH" on
    one live run to the separation claim; the next run measured 1. Separation is
    that neither signal can RAISE the other, which the tests above pin. This one
    pins the other half: when a row genuinely has both, both blocks say so.
    """
    both = _pane(
        "❯ deploy it",
        "",
        "● Deployed. The rollout finished and both replicas are ready.",
        "",
        "  Want me to run the post-deploy check before I close this out?",
        "",
        _RULE,
        "❯ yes and tag the release",
        _RULE,
        "  ctx: 61%",
    )
    rep = waiting_gather(local={"%11": both})
    row = _row(rep, "workbench", "scratch7", "3")
    assert row["waiting_probable"] is True
    assert row["unsent_prompt"] == "yes and tag the release"
    # counted ONCE in each roll-up, never summed together
    assert rep["summary"]["waiting"]["probable"] == 1
    assert rep["summary"]["unsent_prompt"]["count"] == 1
    lines = _lines(sm.render_table(rep))
    assert _block_after(lines, _UNSENT_HEADING) == [
        "workbench:scratch7:3  yes and tag the release"]
    assert _block_after(lines, _WAITING_HEADING) == [
        "workbench:scratch7:3  [trailing_question]  Want me to run the "
        "post-deploy check before I close this out?"]


def test_the_unsent_SUMMARY_LINE_is_pinned_whole_in_BOTH_states():
    """🔴 BOTH BRANCHES, WHOLE. The measured line and the UNMEASURED line say
    opposite things and are two format strings apart; pinning only one leaves
    the other free to render `unsent: 0` for a look nobody took.
    """
    # 🔴 `count` AND `measured` DIFFER HERE ON PURPOSE (1 of 2). A fixture where
    # they coincide cannot see a mutant that renders one in the other's slot —
    # the fixture-equals-the-constant trap, in its arithmetic form.
    one_of_two = sm.render_table(waiting_gather(
        local={"%11": PANE_TYPED_AT_PROMPT}, remote={"%21": PANE_IDLE}))
    assert ("unsent prompts: 1 parked of 2 box(es) read "
            "(1 not read: not_claude=1)") in _lines(one_of_two)
    # ...and both parked, so the count MOVES while the denominator does not
    two_of_two = sm.render_table(waiting_gather(
        local={"%11": PANE_TYPED_AT_PROMPT},
        remote={"%21": PANE_TWO_LINE_DRAFT}))
    assert ("unsent prompts: 2 parked of 2 box(es) read "
            "(1 not read: not_claude=1)") in _lines(two_of_two)
    unmeasured = sm.render_table(base_gather(use_capture=False))
    assert ("unsent prompts: UNMEASURED — 0 of 3 window(s) had an input box "
            "read (not_claude=1, skipped=2)") in _lines(unmeasured)
    # 🔴 THE WORD THAT MUST NOT APPEAR IN THE UNMEASURED BRANCH. `0 parked` is
    # the whole failure, spelled.
    assert "0 parked" not in unmeasured


def test_the_unsent_CAVEAT_line_is_pinned_whole_and_names_the_separation():
    """🔴 PINNED WHOLE, because this line is the ONE place a cold reader learns
    that a `waiting: no` window may still be holding work — and that the null is
    not a zero. A word check here is walkable by a reword; the claim is not.
    """
    line = [l for l in sm.render_caveats(base_gather())
            if "caveat[unsent_prompt]" in l]
    assert len(line) == 1
    assert line[0].strip() == (
        "caveat[unsent_prompt]: text typed at the `❯` prompt and never sent IS "
        "measured, separately from `waiting` and never summed into it, on "
        "claude_rows_only — `unsent_prompt: null` is an EMPTY BOX only when "
        "`unsent_prompt_status` is `ok` "
        "(ok/no_input_box/uncaptured/not_claude/skipped/error); a shell pane is "
        "never scraped and a modal reports `no_input_box`")


def test_the_waiting_caveat_no_longer_implies_the_fact_is_DISCARDED():
    """🔴 A COMMENT IS A CLAIM TOO, AND THIS ONE WENT FROM TRUE TO MISLEADING.

    The waiting caveat says text at the `❯` prompt is EXCLUDED — still true of
    `waiting`. Left alone, the honest reading of it became "so nothing reports
    it", which is now false and would send an operator back to opening panes by
    hand: the exact cost the measurement was taken to remove. Both the rendered
    line and the machine-readable caveat must point at where it went.
    """
    line = [l for l in sm.render_caveats(base_gather())
            if "caveat[waiting_signal]" in l][0]
    assert "EXCLUDED" in line
    assert "is reported under `unsent_prompt` instead" in line
    excl = (base_gather()["caveats"]["waiting_signal"]["excluded"]
            ["prompt_buffer_text"])
    assert "NO LONGER DISCARDED" in excl
    assert "`unsent_prompt`" in excl


def test_the_unsent_caveat_is_STRUCTURED_for_json_consumers_not_prose():
    cav = base_gather()["caveats"]["unsent_prompt"]
    assert cav["scope"] == "claude_rows_only"
    assert cav["statuses"] == list(sm.UNSENT_STATUSES)
    # 🔴 the separation is a FIELD, not a sentence a consumer has to parse
    assert cav["separate_from"] == "waiting_probable"
    assert cav["method"] == "capture_pane_input_box"


# --------------------------------------------------------------------------- #
# §C.4 — the drafts are INVENTED, and that is enforced rather than asserted
# --------------------------------------------------------------------------- #
_REPO_ROOT = os.path.normpath(os.path.join(_HERE, "..", ".."))
_SKILL_DIR = os.path.join(_REPO_ROOT, "claude", "skills", "session-manager")

# 🔴 DERIVED, NOT HAND-LISTED, because the hand-listed version could not see the
# file that actually leaked. It named `SKILL.md` and `reference/waiting-signal.md`
# only; `claudedocs/kickoff-waiting-signal.md` held two of the same real drafts
# and was scrubbed by hand, outside the guard, while the rule the guard enforces
# is scoped in prose to "any file that gets committed". Now: every `.md` under
# the skill (a NEW `reference/*.md` is covered the day it is added) plus this
# feature's kickoff doc, which lives outside the skill tree.
#
# 🔴 DELIBERATELY NOT THE WHOLE REPO. Measured 2026-08-17: `deploy it` and
# `keep going` — two of the fixture drafts below — already occur as ordinary
# prose in `scripts/mail-actions/`, `nix/system/` and three test files. A
# repo-wide scope would fail on five files that never saw a draft, and a guard
# that cries wolf gets deleted. The scope is "the docs this feature ships",
# which is the route all six real drafts actually took.
_SKILL_BODY = os.path.join(_SKILL_DIR, "SKILL.md")
_WAITING_REF = os.path.join(_SKILL_DIR, "reference", "waiting-signal.md")
_CH_REF = os.path.join(_SKILL_DIR, "reference", "clickhouse-queries.md")
_KICKOFF_DOC = os.path.join(_REPO_ROOT, "claudedocs", "kickoff-waiting-signal.md")

# 🔴 THE LEDGER OF FIELDS THAT PUBLISH OPERATOR-TYPED TEXT, and the reason it is
# a ledger rather than a sentence. The NEVER-PASTE rule named `unsent_prompt`
# alone while `clickhouse.rows[].first_msg` — the opening prompt of every recent
# session, shipped by a query that runs by DEFAULT — sat in the same payload
# with nothing said about it anywhere in the skill tree.
#
# Every member must be NAMED in the core's rule (asserted below), and every
# member is bound to the code that produces it, so this list cannot rot into
# fields that no longer exist. 🔴 ITS HONEST LIMIT, stated rather than implied:
# it cannot see a THIRD such field appearing in the payload on its own — nothing
# derives "carries operator text" from the code. What it does buy is that adding
# one here, which is where anyone widening the rule starts, fails until the
# docs name it too.
_OPERATOR_TEXT_FIELDS = ("unsent_prompt", "clickhouse.rows[].first_msg")
# Six files at the time of writing, against the two the hand-list named. The
# three above are NAMED as well as derived because the prose guard below asserts
# a specific sentence in a specific one of them — a positional index into a
# glob is a bug waiting for the next `reference/*.md`.
_SHIPPED_DOCS = tuple(
    sorted(glob.glob(os.path.join(_SKILL_DIR, "**", "*.md"), recursive=True))
    + [_KICKOFF_DOC])


def _derive_fixture_draft_strings(source: str) -> set:
    """Every operator-typed line the `_pane(...)` fixtures in `source` carry.

    🔴 DERIVED FROM THE FIXTURES THEMSELVES so the claim beside the tuple cannot
    rot. The hand-listed version held 9 strings under a comment reading "every
    draft string the fixtures in this file carry, whole and in halves" — the
    file carried 29. Eleven of the twenty it missed are drafts (`deploy it`,
    `keep going`, `start the refactor`, `yes and tag the release`, the
    second/third-line halves of the multi-line fixtures, …), and a new fixture
    added tomorrow would have been uncovered too, silently. A count of
    DECLARATIONS is not a count of INSTANCES.

    The extraction is the shape a draft has in a fixture: a string literal
    argument to `_pane()` whose stripped form begins with the `❯` marker and has
    text after it, plus the CONTINUATION lines that follow it inside the same
    call — consecutive string literals that are neither blank nor a new `❯`
    line. `_RULE` and `"    " + _RULE` are not string literals, so a box rule
    ends a draft for free.
    """
    out = set()
    for node in ast.walk(ast.parse(source)):
        if not (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "_pane"):
            continue
        args, i = node.args, 0
        while i < len(args):
            arg = args[i]
            if not (isinstance(arg, ast.Constant)
                    and isinstance(arg.value, str)
                    and arg.value.strip().startswith("❯")):
                i += 1
                continue
            body = arg.value.strip()[1:].strip()
            if not body:
                i += 1
                continue
            out.add(body)
            i += 1
            while i < len(args):
                nxt = args[i]
                if not (isinstance(nxt, ast.Constant)
                        and isinstance(nxt.value, str)):
                    break
                cont = nxt.value.strip()
                if not cont or cont.startswith("❯"):
                    break
                out.add(cont)
                i += 1
    return out


# 🔴 EXCLUDED, AND IT IS AN ENUMERATION RATHER THAN A PATTERN: these are labels
# CLAUDE CODE DRAWS, not text an operator typed, and a shipped doc quotes them
# legitimately — `2. No` is in `reference/waiting-signal.md` today, so deriving
# without this list fails on a real, correct sentence. Excluding by shape
# instead (anything matching `_MENU_OPTION_RE`) would also drop
# `1. rewrite the intro paragraph`, which is a DRAFT built to look like an
# option, and losing coverage of the ambiguous case is the wrong trade. An entry
# here must be a label the UI renders; anything else is a draft by default.
_MODAL_OPTION_LABELS_NOT_DRAFTS = frozenset((
    "1. Resume from summary (recommended)",
    "2. Resume the full session as-is",
    "3. Do not ask me again",
    "1. Retitle and post the nudge",
    "1. Yes",
    "2. No",
))

# Every draft string the fixtures in this file carry, whole and in halves.
# 🔴 ALL INVENTED. Four REAL operator-typed drafts were quoted verbatim in
# `reference/waiting-signal.md` and re-used as three of these fixtures, beneath
# two headers that each asserted "every string is SYNTHETIC" — in a PUBLIC repo,
# for a feature whose entire job is capturing what the operator types.
_FIXTURE_DRAFT_STRINGS = tuple(sorted(
    _derive_fixture_draft_strings(open(
        os.path.join(_HERE, "test_session_manager.py"), encoding="utf-8").read())
    - _MODAL_OPTION_LABELS_NOT_DRAFTS))


def _draft_strings_found_in(text) -> set:
    return {s for s in _FIXTURE_DRAFT_STRINGS if s in text}


def test_no_FIXTURE_DRAFT_string_appears_in_a_shipped_doc():
    """🔴 THE INVARIANT THE PROSE ASSERTED AND NOTHING CHECKED. Two headers in
    this file claim every fixture string is synthetic ("the words are
    invented"); both were FALSE, and the tell was mechanical — the same four
    drafts appeared verbatim in `reference/waiting-signal.md` AND as fixtures
    here. That is the shape a captured draft takes when it gets copied in: it
    lands in the doc that records the dogfood and in the test that reproduces
    it.

    So the shape is what is banned. A string cannot be in both places. This does
    not prove a string is invented — nothing can, from inside the repo — but it
    fails on the exact route the four real ones took, and it is a deterministic
    guard where the alternative is a sentence asking people to be careful.

    The prose guard lives beside it: both shipped docs must carry the
    never-paste rule, pinned whole in the test below.

    🔴 BOTH SIDES ARE DERIVED NOW, and the file that leaked is the reason. The
    needle list was hand-written and 9 long against 29 fixture drafts; the
    haystack was two hand-named paths, and the third file holding two of the
    same real drafts — `claudedocs/kickoff-waiting-signal.md` — was not one of
    them, so the one file with a demonstrated leak was the one this guard could
    not see. See `_derive_fixture_draft_strings` and `_SHIPPED_DOCS`.
    """
    for path in _SHIPPED_DOCS:
        assert os.path.isfile(path), path
        with open(path, encoding="utf-8") as fh:
            doc = fh.read()
        found = _draft_strings_found_in(doc)
        assert not found, (
            "%s quotes a fixture draft verbatim — a real captured draft copied "
            "into a committed file takes exactly this shape, and this repo is "
            "PUBLIC: %r" % (os.path.basename(path), sorted(found)))
    # 🔴 POSITIVE CONTROL ON THE CHECKER, because a "0 matches" from a scanner
    # wired to nothing is indistinguishable from a clean result. Feed it a doc
    # that DOES contain one and watch the number move.
    planted = "prose prose prose then open the PR prose prose"
    assert _draft_strings_found_in(planted) == {"then open the PR"}
    # ...and the needles really are the fixtures' drafts, not a stale list.
    # BOTH of this pane's drafts, because the derived set sees the scrollback
    # echo the hand-written tuple missed.
    assert _draft_strings_found_in(PANE_TYPED_AT_PROMPT) == {
        "start the refactor", "then open the PR"}
    assert _draft_strings_found_in(PANE_TWO_LINE_DRAFT) == {
        "work out what caused", "the queue backlog"}
    assert _draft_strings_found_in(PANE_SHOWING_ANOTHER_TRANSCRIPT) == {
        "tail the other window",
        "been a couple of days, see whether the patch shipped",
        "look at the nightly job",
        "park 907 until review"}


def test_INSTRUMENT_the_leak_guards_NEEDLES_and_HAYSTACK_are_both_derived():
    """🔴 VALIDATE THE INSTRUMENT BEFORE BELIEVING ITS ZERO. The test above
    reports "no fixture draft is in a shipped doc". That sentence is
    indistinguishable from "the needle list is empty" and from "the file list
    walked nothing" — both of which were TRUE ENOUGH to matter: the needles
    covered 9 of 29 fixture drafts, and the haystack omitted the file that
    leaked. So both derivations are pinned here, and both are proven able to
    move.

    Four claims, none of which the guard above can make about itself:
      1. the haystack really contains the three files, and every entry EXISTS;
      2. the needle set really contains drafts the old hand-list missed, and is
         far bigger than the 9 it held;
      3. a NEW fixture draft is picked up automatically — the whole point of
         deriving, and the fourth mutant an audit found surviving;
      4. every excluded modal label is one the derivation actually produces, so
         the exclusion list cannot rot into a silent hole.
    """
    # 1 — the haystack, including the file the hand-list could not see
    names = {os.path.relpath(p, _REPO_ROOT) for p in _SHIPPED_DOCS}
    assert {"claude/skills/session-manager/SKILL.md",
            "claude/skills/session-manager/reference/waiting-signal.md",
            "claudedocs/kickoff-waiting-signal.md"} <= names, sorted(names)
    for path in _SHIPPED_DOCS:
        assert os.path.isfile(path), path

    # 2 — the needles, and eleven the hand-written tuple did not hold
    assert len(_FIXTURE_DRAFT_STRINGS) >= 20, _FIXTURE_DRAFT_STRINGS
    for missed in ("deploy it", "keep going", "start the refactor",
                   "yes and tag the release", "show me that other window",
                   "tail the other window", "first line of a long draft",
                   "second line of a long draft", "second line of the draft",
                   "can you double-check the fixture ordering?",
                   "1. rewrite the intro paragraph"):
        assert missed in _FIXTURE_DRAFT_STRINGS, missed

    # 3 — POSITIVE CONTROL ON THE DERIVATION: a fixture that does not exist yet.
    # This is the mutant "add a fixture draft with no guard coverage": under the
    # old hand-list it survived by construction, because the list could not
    # change without a human. Feed the extractor a fresh `_pane(...)` and watch
    # BOTH the marker line and its continuation appear.
    grown = _derive_fixture_draft_strings(
        'x = _pane("● hi", "", _RULE,\n'
        '          "❯ a brand new draft nobody guarded",\n'
        '          "  and its second rendered line",\n'
        '          _RULE, "  ctx: 7%")\n')
    assert grown == {"a brand new draft nobody guarded",
                     "and its second rendered line"}, grown
    # ...and it does NOT invent needles out of chrome: an empty box, a rule and
    # a footer yield nothing, or every doc would match something.
    assert _derive_fixture_draft_strings(
        'y = _pane(_RULE, "❯ ", _RULE, "  ctx: 9%")\n') == set()

    # 4 — the exclusion ledger is pinned TWO-WAY against the fixtures. A label
    # reworded in a fixture, or one deleted outright, must fail here rather than
    # quietly widen the hole it was cut for.
    raw = _derive_fixture_draft_strings(open(
        os.path.join(_HERE, "test_session_manager.py"), encoding="utf-8").read())
    assert _MODAL_OPTION_LABELS_NOT_DRAFTS <= raw, (
        "an excluded label is no longer produced by any fixture — a stale "
        "exclusion is a hole nobody can see: %r"
        % sorted(_MODAL_OPTION_LABELS_NOT_DRAFTS - raw))
    # ...and the exclusion is NARROW: it removes six labels, not the corpus.
    assert len(raw) - len(_MODAL_OPTION_LABELS_NOT_DRAFTS) == \
        len(_FIXTURE_DRAFT_STRINGS) >= 20, (len(raw), len(_FIXTURE_DRAFT_STRINGS))
    # 🔴 AND IT DOES NOT SWALLOW THE AMBIGUOUS CASE: `1. rewrite the intro
    # paragraph` is a DRAFT shaped like an option, and it stays a needle. An
    # exclusion by SHAPE (`_MENU_OPTION_RE`) would have dropped it silently.
    assert "1. rewrite the intro paragraph" not in \
        _MODAL_OPTION_LABELS_NOT_DRAFTS


def test_EVERY_field_that_publishes_OPERATOR_TEXT_is_named_by_the_NEVER_PASTE_rule():
    """🔴 THE GUARD WAS NARROWER THAN ITS OWN SENTENCE, which is the defect it
    now exists to stop.

    The original rule — and the original version of this test — named
    `unsent_prompt` alone, from the day it landed (2026-08-17).
    `clickhouse.rows[].first_msg` is the opening prompt of every recent session,
    shipped in the SAME payload by a query that runs by DEFAULT (~17 KB of
    operator-typed text in an ordinary scan), and it had been there since the
    tool's first commit (2026-08-11) with no document in the skill tree
    mentioning it. Nothing about the sentence was false; it was NARROWER THAN
    THE PAYLOAD IT GOVERNED, and a rule that reads as complete is worse than
    none because it stops anyone looking.
    `claude/RULES.md`: "a guard's DESCRIPTION claims COVERAGE — check the
    implementation is as wide as the sentence."

    So the width is now the thing that is CHECKED, not the thing that is
    written. `_OPERATOR_TEXT_FIELDS` is an asserted LEDGER: it fails when the
    set grows (a third such field cannot ship without a doc naming it) and when
    it shrinks (a field cannot be quietly dropped from the rule while the
    payload still carries it), and each member is bound to the code that
    produces it below, so the ledger cannot drift from reality either.

    Pinned as WHOLE normalised strings. A word check here is walkable by a
    reword — four prose guards in this repo were walked exactly that way, one by
    a synonym — and this claim is load-bearing enough to pay a cosmetic-reword
    failure for.
    """
    def _norm(text):
        return " ".join(text.split())

    # 🔴 BY NAME, NEVER BY INDEX. These used to be `_SHIPPED_DOCS[0]` and
    # `[1]`, which silently became `reference/clickhouse-queries.md` and
    # `cross-host.md` the moment that tuple was derived from a glob — a guard
    # asserting a sentence into whichever file sorted first.
    with open(_SKILL_BODY, encoding="utf-8") as fh:
        skill = _norm(fh.read())
    with open(_WAITING_REF, encoding="utf-8") as fh:
        ref = _norm(fh.read())
    with open(_CH_REF, encoding="utf-8") as fh:
        ch_ref = _norm(fh.read())

    assert ("🔴 **NEVER PASTE CAPTURED OPERATOR TEXT INTO A COMMITTED FILE.** "
            "TWO fields carry **text the operator typed** — `unsent_prompt` "
            "(the draft) and `clickhouse.rows[].first_msg` (the opening prompt "
            "of every recent session) — and devrc is a **PUBLIC** repo, as is "
            "every `claudedocs/` note, commit message, PR body, comment or "
            "test fixture an agent writes into it. Report either as a "
            "**count, a length or a shape**, never verbatim.") in skill
    assert ("### 🔴 NEVER PASTE CAPTURED OPERATOR TEXT INTO A COMMITTED "
            "FILE") in ref
    assert ("report a draft as a **count, a length or a shape**, never "
            "verbatim, in any file that gets committed") in ref

    # 🔴 AND WHERE THE SECOND FIELD ACTUALLY LIVES. A reader who opens the
    # ClickHouse reference to write a query never passes the `unsent_prompt`
    # section, so the rule has to be on the query's own page too — the same
    # "put it where the reader hits it" argument that put it in two files
    # originally, applied to the file the original pass missed.
    assert ("🔴 **NEVER PASTE CAPTURED OPERATOR TEXT INTO A COMMITTED FILE.** "
            "`first_msg` is **text the operator typed**") in ch_ref

    # 🔴 THE WIDTH CHECK. Every ledgered field must be NAMED in the core's rule
    # — not merely present somewhere in the file, which `unsent_prompt` would
    # satisfy from its own section heading while the rule ignored it.
    rule = skill.split("NEVER PASTE CAPTURED OPERATOR TEXT")[1].split("never verbatim.")[0]
    for field in _OPERATOR_TEXT_FIELDS:
        assert field in rule, (
            f"`{field}` publishes operator-typed text and the core's "
            f"NEVER-PASTE rule does not name it. Widen the rule (and its pin in "
            f"test_session_manager_skill_size.py) in the SAME commit — or, if "
            f"the field no longer carries operator text, remove it from "
            f"_OPERATOR_TEXT_FIELDS and say why.")

    # 🔴 AND THE LEDGER IS BOUND TO THE CODE, so it cannot drift into a list of
    # fields that no longer exist while a real one ships uncovered. Each member
    # is checked against the thing that PRODUCES it, not against another doc.
    assert "first_msg" in sm.SQL_RECENT_SESSIONS, (
        "the ledger names `clickhouse.rows[].first_msg`, but the query no "
        "longer selects it — drop it from _OPERATOR_TEXT_FIELDS if the payload "
        "genuinely stopped carrying operator text")
    assert "unsent_prompt" in sm.LEAN_ROW_FIELDS, (
        "the ledger names `unsent_prompt`, but no row carries it")
    # 🔴 AND THE CO-OCCURRENCE CORRECTION, in the same pass: the reference used
    # to promote "0 rows flagged BOTH" on ONE live run to the separation claim.
    # The next run measured 1, and suppressing co-occurrence would make the tool
    # WORSE. The doc must now say what separation actually means.
    assert "**Rows flagged BOTH: 0 on that run, 1 on the next.**" in ref
    assert ("**Separation means the two signals are COUNTED separately and "
            "neither can raise the other**") in ref
    assert "The two signals were disjoint on live data" not in ref
    # the skill body carries the same correction, for the reader who never
    # opens the reference
    assert ("🔴 **A row can carry BOTH, and that is correct**") in skill


def test_the_docstring_documents_the_shell_pane_DECISION_not_just_the_behaviour():
    """🔴 THE BRIEF REQUIRED THE CHOICE BE STATED, and a decision recorded only
    in a test is a decision the next reader re-litigates. Both halves of the
    reasoning must be in the detector's own docstring: that it does not fire for
    shell panes, and WHY (a half-typed shell command is a different, noisier
    thing — not merely that the capture happens to skip them).
    """
    doc = sm.detect_unsent_prompt.__doc__
    assert "CLAUDE ROWS ONLY, AND NOT ON SHELL PANES" in doc
    assert "noisier" in doc
    assert "not_claude" in doc


# =========================================================================== #
# §D — `not_measured`: the populations this tool structurally CANNOT see
#
# 🔴 THE MEASUREMENT. The same blind dogfood found 60 open PRs invisible to this
# tool, one CONFLICTING for eleven days and one open since February. Its verdict:
# the tool "is precise about what it measured but does not tell a cold reader
# what it did NOT measure".
#
# 🔴 AND THE CONSTRAINT. This file has shipped a constant masquerading as a
# measurement FIVE times — `kinds_produced` was a literal `["tmux"]` that kept
# asserting itself whatever the rows were, and the third recurrence landed
# INSIDE the commit that fixed the second. So the claim is DERIVED from the
# report's own keys, and the tests below pin that derivation two ways: a
# structural `ast` ledger (the shape `test_drift_check.py`'s phase-2 reason-token
# ledger uses) and a behavioural control that watches the output MOVE.
# =========================================================================== #
_GATHER_REPORT_KEYS = {
    "ts", "local_host", "stale_threshold_secs", "hosts", "clickhouse",
    "fuzzyclaw", "ledger", "filters", "clawgate_queue", "caveats", "summary",
    "not_measured",
}

# 🔴 THE LEDGER'S MEMBERSHIP, AS A LITERAL THAT DOES NOT COME FROM THE LEDGER.
# Measured by an audit: deleting the whole `cluster_alerts` entry left the suite
# at 553 PASSED. GROW was genuinely pinned (a new `report[...]` key fails the
# ast test), but SHRINK was not — only three of the five populations were named
# anywhere, and the one count assertion read `1 + len(NOT_MEASURED_POPULATIONS)`,
# derived from the very constant it claimed to guard. That is the
# fixture-equals-the-constant trap this file has now hit SIX times, and the
# control for it is mechanical: state the expected values somewhere the constant
# cannot reach, and watch a deletion move them.
_EXPECTED_NOT_MEASURED = {
    "pull_requests": ("pull_requests", "standup"),
    "mail_queue": ("mail_queue", "mailbox"),
    "cluster_alerts": ("cluster_alerts", "standup"),
    "initiative_board": ("initiative_board", "initiatives"),
    "gui_windows_outside_tmux": ("gui_windows", "i3"),
}


def _sm_ast():
    import ast
    return ast.parse(open(_SCRIPT, encoding="utf-8").read())


def _gather_report_keys() -> set:
    """Every top-level `report[...]` key `gather` touches, read from its OWN
    source: the keys of the `report = {...}` literal plus every constant-string
    subscript of the name `report`.

    🔴 STATICALLY, NOT BY RUNNING A SCAN. A runtime read would only ever see the
    keys a scan happens to produce, which is the fixture-equals-the-constant
    trap one level up: it could not tell "gather does not write this key" from
    "this fixture did not reach the branch that writes it".
    """
    import ast
    fn = next(n for n in _sm_ast().body
              if isinstance(n, ast.FunctionDef) and n.name == "gather")
    keys = set()
    for node in ast.walk(fn):
        if (isinstance(node, ast.Subscript)
                and isinstance(node.value, ast.Name)
                and node.value.id == "report"
                and isinstance(node.slice, ast.Constant)
                and isinstance(node.slice.value, str)):
            keys.add(node.slice.value)
        if (isinstance(node, ast.Assign)
                and isinstance(node.value, ast.Dict)
                and any(isinstance(t, ast.Name) and t.id == "report"
                        for t in node.targets)):
            for k in node.value.keys:
                if isinstance(k, ast.Constant) and isinstance(k.value, str):
                    keys.add(k.value)
    return keys


def test_the_not_measured_ledger_is_PINNED_to_the_keys_gather_actually_WRITES():
    """🔴 THE STRUCTURAL FIX FOR THE WHOLE CLASS, not for the one instance —
    the same shape as `test_drift_check.py::test_the_phase2_reason_token_ledger_
    is_pinned_to_the_fields_read`, and here for the same reason.

    Two sets, both extracted from the source, pinned two-way:

      * the top-level keys `gather` writes must be exactly `_GATHER_REPORT_KEYS`;
      * every ledger `report_key` must be DISJOINT from that set.

    Add PR querying that writes `report["pull_requests"]` and the first
    assertion fails; updating it to green then fails the second, whose only fix
    is deleting the `pull_requests` entry — which is exactly the step that does
    not happen on its own. Delete a key `gather` writes and the first fails too.

    🔴 WHAT IT DOES **NOT** GUARD, stated because the earlier version of this
    docstring claimed it did: this test pins `gather`'s KEYS, not the ledger's
    MEMBERSHIP. Deleting a whole ledger entry passes every assertion here — it
    was measured, at 553 green. `test_the_not_measured_POPULATION_SET_cannot_
    silently_SHRINK` is the one that fails on that.

    A static list of "things we do not measure" needs no such guard and is
    wrong the day someone adds the measurement — the one day a reader is most
    likely to trust it.
    """
    keys = _gather_report_keys()
    assert keys == _GATHER_REPORT_KEYS, (
        "the keys `gather` writes drifted from the ledger:\n"
        "  only in source: %r\n  only in ledger: %r"
        % (sorted(keys - _GATHER_REPORT_KEYS),
           sorted(_GATHER_REPORT_KEYS - keys)))
    claimed = {spec["report_key"]
               for spec in sm.NOT_MEASURED_POPULATIONS.values()}
    overlap = claimed & keys
    assert not overlap, (
        "a population is claimed UNMEASURED while `gather` writes its key — "
        "the claim is already false: %r" % sorted(overlap))
    # ...and the ledger's own shape, so an entry cannot ship half-filled
    for name, spec in sm.NOT_MEASURED_POPULATIONS.items():
        assert set(spec) == {"report_key", "owner_skill", "note"}, name
        assert spec["note"] and isinstance(spec["note"], str), name


def test_the_ast_key_extractor_can_SEE_a_key_it_is_supposed_to_catch():
    """🔴 POSITIVE CONTROL ON THE INSTRUMENT, read before its verdict. The
    assertion above is a `==` between two sets, and a walker that silently found
    NOTHING would make it a comparison of two empty-ish shapes — or worse, would
    make the disjointness check vacuously true forever.

    So prove the walker sees BOTH shapes it must see: a key from the `report =
    {...}` literal and a key from a later `report[...] = ` assignment.
    """
    keys = _gather_report_keys()
    assert keys, "the extractor found nothing — every verdict off it is vacuous"
    assert "clickhouse" in keys, "missed a key from the dict literal"
    assert "not_measured" in keys, "missed a key from a later assignment"
    assert "pull_requests" not in keys, (
        "gather does not write this — if it now does, the ledger entry must go")


def test_the_not_measured_POPULATION_SET_cannot_silently_SHRINK():
    """🔴 THE HALF THE AST PIN DOES NOT COVER, and it was measured OPEN:
    deleting the entire `cluster_alerts` entry left the suite at 553 PASSED,
    while both the source comment and the PR body claimed "the set cannot drift
    in either direction … deleting an entry fails too".

    Three reasons it survived, all of them the same reason: only three of the
    five populations were named by any test; the count assertion was `1 +
    len(NOT_MEASURED_POPULATIONS)`, i.e. derived from the constant it guarded;
    and the ast test pins `gather`'s keys, which a ledger deletion does not
    touch.

    So membership is pinned against `_EXPECTED_NOT_MEASURED` — a literal in THIS
    file, which the module constant cannot influence. Deleting an entry fails
    here. Adding one fails here AND on the ast test. Re-pointing an entry's
    `report_key` or `owner_skill` fails here too, which nothing else saw.
    """
    got = {name: (spec["report_key"], spec["owner_skill"])
           for name, spec in sm.NOT_MEASURED_POPULATIONS.items()}
    assert got == _EXPECTED_NOT_MEASURED, (
        "the not_measured ledger drifted:\n  only in source: %r\n"
        "  only in this test: %r"
        % (sorted(set(got) - set(_EXPECTED_NOT_MEASURED)),
           sorted(set(_EXPECTED_NOT_MEASURED) - set(got))))
    # 🔴 AND THE DERIVED OUTPUT MOVES WITH IT, against a LITERAL count. `5` is a
    # number the constant cannot supply; `len(NOT_MEASURED_POPULATIONS)` is the
    # trap this test exists to close, so it must not appear on this line.
    assert len(base_gather()["not_measured"]) == 5
    assert len(sm.render_not_measured(base_gather())) == 6  # heading + 5 rows


def test_every_not_measured_population_names_a_skill_that_EXISTS():
    """🔴 A POINTER TO A SKILL THAT IS NOT THERE IS WORSE THAN NO POINTER: it
    costs a hop to discover, and it discovers it at the moment the reader has
    already decided to trust the output.

    Checked against the repo, not against a list — a list would be a second copy
    of the same claim.
    """
    skills = os.path.normpath(os.path.join(_HERE, "..", "..",
                                           "claude", "skills"))
    assert os.path.isdir(skills), skills
    for name, spec in sorted(sm.NOT_MEASURED_POPULATIONS.items()):
        body = os.path.join(skills, spec["owner_skill"], "SKILL.md")
        assert os.path.isfile(body), (
            f"{name} points at /{spec['owner_skill']}, which has no {body}")
    # POSITIVE CONTROL on this check: it must be able to FAIL. A path built the
    # same way for a skill that does not exist must not be a file.
    assert not os.path.isfile(
        os.path.join(skills, "no-such-skill-xyz", "SKILL.md"))


def test_not_measured_is_DERIVED_where_derived_DIFFERS_from_the_constant():
    """🔴 THE FIXTURE-EQUALS-THE-CONSTANT TRAP, CLOSED. Every realistic report
    is missing every one of these keys, so a mutant replacing the derivation
    with the module constant's own contents produces byte-identical output on
    every real fixture and SURVIVES a green suite. A fixture whose value
    coincides with the constant cannot tell "derived" from "hardcoded".

    So the ledger is injected with populations whose names the constant does NOT
    contain, and the output is watched to follow the INJECTED set rather than
    the module's.
    """
    ledger = {
        "zulu_population": {"report_key": "zulu_key",
                            "owner_skill": "standup", "note": "n1"},
        "yankee_population": {"report_key": "yankee_key",
                              "owner_skill": "mailbox", "note": "n2"},
    }
    got = sm.measured_not_measured({"ts": "x"}, ledger=ledger)
    assert [p["population"] for p in got] == ["yankee_population",
                                              "zulu_population"]
    # none of the MODULE's populations leaked in — the mutant that returns the
    # constant fails right here
    assert not ({p["population"] for p in got}
                & set(sm.NOT_MEASURED_POPULATIONS))
    # and the entries are projected, not the raw ledger dicts
    assert got[0] == {"population": "yankee_population",
                      "report_key": "yankee_key",
                      "owner_skill": "mailbox", "note": "n2"}


def test_ADDING_a_measurement_REMOVES_the_claim_that_it_is_unmeasured():
    """🔴 THE WHOLE POINT OF THE DERIVATION, as a behavioural control — and the
    number is watched to MOVE, not merely to be non-zero.

    A report carrying `pull_requests` must stop claiming PRs are unmeasured,
    with no edit to the ledger. Measured as a PAIR: N without the key, N-1 with
    it, and the population that vanished is the right one.
    """
    rep = base_gather()
    before = {p["population"] for p in rep["not_measured"]}
    assert "pull_requests" in before, "baseline must claim it, or this is vacuous"

    after = {p["population"]
             for p in sm.measured_not_measured(dict(rep, pull_requests=[]))}
    assert "pull_requests" not in after
    assert after == before - {"pull_requests"}
    assert len(after) == len(before) - 1
    # 🔴 the key is the coupling, and it is the DECLARED one — not the
    # population name. `gui_windows_outside_tmux` is keyed on `gui_windows`, so
    # a derivation matching on the name would fail here.
    assert "gui_windows_outside_tmux" in before
    assert "gui_windows_outside_tmux" not in {
        p["population"] for p in sm.measured_not_measured(
            dict(rep, gui_windows=[]))}
    assert "gui_windows_outside_tmux" in {
        p["population"] for p in sm.measured_not_measured(
            dict(rep, gui_windows_outside_tmux=[]))}, (
        "the derivation matched the POPULATION name, not its report_key")


def test_the_not_measured_key_is_in_the_report_and_names_the_two_required_ones():
    """The brief's floor: `pull_requests` -> standup and `mail_queue` ->
    mailbox, both present in a real scan's payload with their owners."""
    pops = {p["population"]: p for p in base_gather()["not_measured"]}
    assert pops["pull_requests"]["owner_skill"] == "standup"
    assert pops["mail_queue"]["owner_skill"] == "mailbox"
    # 🔴 the note carries the EVIDENCE, not just the label — a reader deciding
    # whether to spend a hop needs to know what is at stake behind the name
    assert "conflicting" in pops["pull_requests"]["note"].lower()
    # and clawgate is NOT re-listed: `clawgate_queue` measures it
    assert "clawgate" not in pops
    # nor is the opencode misclassification, which is a measured population
    # reported under the wrong class, not an unmeasured one
    assert not [p for p in pops if "opencode" in p]


def test_the_NOT_MEASURED_section_is_pinned_as_a_WHOLE_normalised_string():
    """🔴 PINNED WHOLE. The heading is the entire mechanism: a reader who takes
    "these are NOT zero, they were never looked at" as "these are zero" is the
    reader this section exists for, and a word check is walkable by a reword.
    """
    heading = ("▸ NOT MEASURED HERE — these are NOT zero, they were never "
               "looked at; the owning skill has each:")
    lines = _lines(sm.render_table(base_gather()))
    assert heading in lines
    # 🔴 EVERY population, as a WHOLE block. Naming three of five was how a
    # deleted entry stayed green: the two nobody spelled were the two free to
    # vanish. Equality, not `in` — an `in` per row cannot see one go missing.
    assert _block_after(lines, heading) == [
        "cluster_alerts             -> /standup",
        "gui_windows_outside_tmux   -> /i3",
        "initiative_board           -> /initiatives",
        "mail_queue                 -> /mailbox",
        "pull_requests              -> /standup",
    ]


def test_render_not_measured_tells_an_ABSENT_key_from_an_EMPTY_list():
    """🔴 THE NULL-VS-ZERO RULE, APPLIED TO THIS SECTION ITSELF. A report with
    no `not_measured` key had no derivation run — which is NOT "everything is
    measured" — and a heading printed over no rows reads as a failed render
    rather than as an all-clear. Three states, three different sentences.
    """
    absent = sm.render_not_measured({"ts": "x"})
    assert absent == ["▸ NOT MEASURED HERE: UNKNOWN — this report carries no "
                      "`not_measured` key, so the scope of what was left out "
                      "was never derived (this is NOT a claim that nothing "
                      "was)"]
    empty = sm.render_not_measured({"not_measured": []})
    assert empty == ["▸ NOT MEASURED HERE: none — every enumerated population "
                     "now has a measurement in this report"]
    assert absent != empty
    populated = sm.render_not_measured(base_gather())
    # 🔴 A LITERAL, NOT `1 + len(sm.NOT_MEASURED_POPULATIONS)`. That expression
    # was this assertion for the whole PR, and it is the reason deleting an
    # entry stayed green: both sides shrank together. See
    # `test_the_not_measured_POPULATION_SET_cannot_silently_SHRINK`.
    assert len(populated) == 6


def test_the_not_measured_section_is_printed_in_EVERY_state_including_empty():
    """Same rule as CLAWGATE QUEUE: a section that appears only sometimes is one
    a reader learns not to expect, and the run where it matters is the run where
    everything else looked clean."""
    for rep in (base_gather(),
                base_gather(runner=make_runner(local_rc=1, remote_rc=1)),
                mix_gather(claude_only=True)):
        assert "▸ NOT MEASURED HERE" in sm.render_table(rep)


def test_measured_not_measured_is_PURE_and_shares_nothing_with_the_constant():
    """🔴 A purity claim is only as true as its deepest shared object, and this
    file has been bitten at depth 2 before (`measured_caveats`). Writing through
    a returned entry must not poison `NOT_MEASURED_POPULATIONS` for every later
    caller in the process."""
    before = copy.deepcopy(sm.NOT_MEASURED_POPULATIONS)
    out = sm.measured_not_measured({})
    for entry in out:
        entry["note"] = "clobbered"
        entry["owner_skill"] = "clobbered"
    assert sm.NOT_MEASURED_POPULATIONS == before
    assert sm.measured_not_measured({})[0]["note"] != "clobbered"


# =========================================================================== #
# §14 — LIVE-FIRST LOOKUP: `hotkey_display`, `--match`, and the LOUD detail miss
#
# 🔴 THE THREE DEFECTS THIS SECTION PINS, all measured on one real run
# (2026-08-28, "find this thing I lost track of"): 127 s, 9 tool calls, 5 of
# them pure flailing.
#
#   1. `detail <addr>` that matched NOTHING returned a silent empty window list
#      — byte-identical to "found it, the window is idle". The run guessed index
#      `3` from the session name `scratch3`; the real index was `2`, and nothing
#      said so. (`test_a_detail_miss_*`)
#   2. The row carried `hotkey: v` and the answer rendered `Alt+Shift+V`. Per
#      `scripts/tmux-scratch-slots.sh`, `M-v` is scratch3/violet and `M-V` is
#      scratch4/Vapor — DIFFERENT sessions, so the operator was sent to a real
#      window that was the wrong one. (`test_hotkey_display_*`)
#   3. There was no way to ask the live scan "which window is about X", so the
#      30 s transcript archive was used for a question about NOW. (`test_match_*`)
#
# 🔴 WHICH OF THESE ARE REGRESSION COVERAGE. Everything below was watched RED at
# 9e452d34 EXCEPT the tests named in `INVARIANT_GUARDS_ADDED_HERE`, which pin
# behaviour the pre-change tree already had. They are listed rather than left to
# be assumed, because counting an invariant guard as a fixed bug is how a suite
# claims coverage it does not have.
# =========================================================================== #
#
# MEASURED, not asserted: replayed against a detached worktree at 9e452d34 with
# this exact file copied in — 45 collected §14 nodes, 38 RED and 7 GREEN. The 7
# are precisely the set below.
INVARIANT_GUARDS_ADDED_HERE = frozenset({
    # `exit_code_for` already returned EXIT_EMPTY for a detail miss over a
    # reachable fleet, and EXIT_UNAVAILABLE when nothing answered. What was
    # missing was the MESSAGE and the structured count, not the code. Pinned so
    # a later refactor of the miss path cannot quietly fold the two together.
    "test_the_detail_exit_CODES_were_already_right_and_stay_right",
    # `render_label` is UNCHANGED by this work. This asserts it stayed that way.
    "test_render_label_states_the_KEY_and_never_a_CHORD",
    # `filter_report` already copied the report before narrowing it; the new
    # `detail_*` bookkeeping is what could have broken that.
    "test_filter_report_does_not_MUTATE_the_report_it_narrows",
    # A POSITIVE CONTROL on the `--match` fixture, not a bug: it asserts the
    # UNFILTERED shape, so it passes wherever `fold_windows` works. Without it a
    # `0 rows` verdict below could not be told from a fixture that built none.
    "test_the_match_fixture_produces_the_rows_the_probes_below_assume",
    # A NEGATIVE control on the miss message: a `detail` that FINDS its window
    # printed nothing before this change and prints nothing after it.
    "test_main_detail_HIT_prints_no_miss_line",
    # This ledger's own gate.
    "test_the_invariant_guard_ledger_names_only_tests_that_exist",
    # `detail --json` always returned the full report shape; the observed run
    # assumed otherwise. Pinning a shape that was always right is not a fix —
    # it is what makes the reference doc's new claim machine-checked.
    "test_detail_json_is_the_FULL_REPORT_SHAPE_not_a_bare_window",
})


def test_the_invariant_guard_ledger_names_only_tests_that_exist():
    """A ledger that names a deleted test asserts nothing while reading as if it
    does. Both directions are not available here (a regression test is not
    enumerated), so at minimum every name must resolve."""
    for name in INVARIANT_GUARDS_ADDED_HERE:
        assert name in globals(), (
            f"{name!r} is listed as an invariant guard but no such test exists")


# --------------------------------------------------------------------------- #
# hotkey_display — ONE writer for a chord whose CASE selects the session
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("key,expect", [
    ("v", "Alt+v"),
    ("V", "Alt+Shift+V"),
    # A second letter, so a mutant that hardcodes the `v`/`V` pair dies. The
    # fixture letters are pairwise distinct from each other AND from every
    # letter the assertions elsewhere in this file name.
    ("q", "Alt+q"),
    ("Q", "Alt+Shift+Q"),
    # Neither upper nor lower: `bind -n M-7` needs no Shift.
    ("7", "Alt+7"),
    # 🔴 `resolve_label` returns None on tiers 2 and 3 and that is load-bearing.
    (None, None),
    # ...and "" must NOT become `Alt+`, which would read as a real chord.
    ("", None),
])
def test_hotkey_display_maps_CASE_to_the_shift_modifier(key, expect):
    assert sm.hotkey_display(key) == expect


def test_the_two_CASES_of_one_letter_are_DIFFERENT_CHORDS():
    """🔴 THE DEFECT, stated as the thing that must not be true again.

    Both cases are asserted AND asserted to DIFFER. A mutant that returns
    `f"Alt+Shift+{k}"` unconditionally, or that normalises the key's case,
    satisfies exactly one of the first two lines and fails the third.
    """
    lower, upper = sm.hotkey_display("v"), sm.hotkey_display("V")
    assert lower == "Alt+v"
    assert upper == "Alt+Shift+V"
    assert lower != upper, (
        "the two cases collapsed to one chord — `M-v` is scratch3/violet and "
        "`M-V` is scratch4/Vapor, so this sends the operator to the wrong "
        "live window")
    # ...and the case is PRESERVED, not normalised: `Alt+V` (no Shift) and
    # `Alt+Shift+v` are each a chord bound to nothing.
    assert "Alt+V" not in lower and upper != "Alt+Shift+v"


def test_the_case_significance_is_READ_OUT_OF_the_slot_table_not_asserted_here():
    """🔴 A SEAM guard: "case selects a different session" is a fact about
    `scripts/tmux-scratch-slots.sh`, and this reads it there.

    A hand-written pair here would agree with the table today and drift the
    first time a slot is rekeyed — the same reason
    `test_the_hotkey_comes_from_the_SLOT_TABLE_not_a_second_map` reads its
    expectation out of the table text rather than restating it.
    """
    table = os.path.normpath(os.path.join(_HERE, "..", "tmux-scratch-slots.sh"))
    slots = sm.load_scratch_slots(paths=[table])
    assert slots, "positive control: the real slot table parsed as EMPTY"
    by_key = {}
    for session, entry in slots.items():
        by_key.setdefault(entry["key"], []).append(session)
    pairs = [(k, k.upper()) for k in sorted(by_key)
             if k.islower() and k.upper() in by_key]
    assert pairs, (
        "no lower/upper key pair exists in the slot table any more, so the case "
        "rule `hotkey_display` encodes is no longer load-bearing — re-justify "
        "the function or delete it; do not leave this test asserting a dead fact")
    for lo, up in pairs:
        assert by_key[lo] != by_key[up], (
            f"{lo!r} and {up!r} map to the same session, so case selects nothing")
        assert sm.hotkey_display(lo) != sm.hotkey_display(up)


def test_hotkey_display_is_None_on_the_TIERS_THAT_HAVE_NO_KEY():
    """Tiers 2 and 3 carry `hotkey: None`; the derived chord must be None too —
    never `""`, never invented."""
    rows = sm.fold_windows(
        sm.parse_panes("%91|9001|no-slot-here|1|w-n|/w/synth-juliet|zsh|title"),
        "workbench", slots={}, now=NOW)
    assert rows[0]["label_source"] == "path"
    assert rows[0]["hotkey"] is None
    assert rows[0]["hotkey_display"] is None


def test_hotkey_display_rides_on_every_row_and_in_the_LEAN_view():
    """The derivation is performed ONCE, by the producer — so the consumer that
    got it wrong never has to perform it at all."""
    report = base_gather()
    rows = [r for h in report["hosts"].values() for r in h["windows"]]
    assert rows, "positive control: the fixture must produce rows"
    for r in rows:
        assert r["hotkey_display"] == sm.hotkey_display(r["hotkey"])
    assert "hotkey_display" in sm.LEAN_ROW_FIELDS
    lean = sm.lean_report(report)
    for r in [x for h in lean["hosts"].values() for x in h["windows"]]:
        assert "hotkey_display" in r


def test_render_label_states_the_KEY_and_never_a_CHORD():
    """INVARIANT GUARD — `render_label` is deliberately NOT routed through
    `hotkey_display`, and this pins the two surfaces apart.

    The LABEL column is 14 characters (`_trunc(render_label(r), 14)`), and
    `Yarrow (Alt+Shift+Y)` is 20 — routing it through would produce a truncated
    stub MORE ambiguous than the raw key, not less. So the table states the key
    with its case intact and the chord travels on the row.
    """
    assert sm.render_label({"label": "violet", "hotkey": "v"}) == "violet (v)"
    assert sm.render_label({"label": "Vapor", "hotkey": "V"}) == "Vapor (V)"
    for row in ({"label": "violet", "hotkey": "v"},
                {"label": "Vapor", "hotkey": "V"}):
        assert "Alt+" not in sm.render_label(row), (
            "the table started spelling a chord; if that is deliberate, check "
            "the 14-char LABEL column still fits it before changing this test")
    assert len(sm.render_label({"label": "Yarrow", "hotkey": "Y"})) <= 14


# --------------------------------------------------------------------------- #
# --match — the FIELD SET is the feature, and `path` is not in it
# --------------------------------------------------------------------------- #
# 🔴 THE FIXTURE IS THE `29 of 72` SHAPE AT THREE-ROW SCALE. Both non-slot
# windows sit under the same `zzpapaya` directory and neither one's LABEL or
# TASK says that word (their labels are the deeper leaves `zzsigma`/`zztheta`):
#
#   `zzkiwi`    -> 1 row  (a task, and only one of them)
#   `zzsigma`   -> 1 row  (a label)
#   `zzpapaya`  -> 0 rows by default, 2 rows under --match-path
#
# Every token is pairwise distinct and none appears in any constant the
# assertions name, so a mutant that hardcodes a field name or a literal cannot
# survive by coincidence.
MATCH_PANES = "\n".join([
    f"%31|3001|match-one|1|w-one|/home/zach/workspace/zzpapaya/zzsigma|claude"
    f"|{BRAILLE} refactor the zzkiwi cache",
    f"%32|3002|match-two|4|w-two|/home/zach/workspace/zzpapaya/zztheta|claude"
    f"|{SPARKLE} unrelated zzmango work",
    # A slot session, so `codename` is populated and tier 1 is exercised.
    f"%33|3003|scratch2|9|w-three|/home/zach/tmp|claude|{SPARKLE} third thing",
])
MATCH_WINDOWS = "@31|1|match-one\n@32|4|match-two\n@33|9|scratch2\n"


def match_gather(**kw):
    """`base_gather` over MATCH_PANES, with an EMPTY but REACHABLE laptop.

    Reachable-and-empty rather than absent: every exit-code assertion below
    depends on "the fleet answered", and a fixture that could not tell that from
    "the fleet is down" would grade the wrong thing.
    """
    defaults = dict(
        runner=make_runner(local_panes=MATCH_PANES,
                           local_windows=MATCH_WINDOWS,
                           remote_panes="", remote_windows=""),
        use_fuzzyclaw=False)
    defaults.update(kw)
    return base_gather(**defaults)


def _sessions(report):
    return sorted(r["session"] for h in report["hosts"].values()
                  for r in h["windows"])


def test_the_match_fixture_produces_the_rows_the_probes_below_assume():
    """POSITIVE CONTROL on the instrument, before any of its verdicts.

    A zero from `--match` is indistinguishable from a zero produced by a fixture
    that never built the rows. This asserts the unfiltered shape — three rows,
    the labels the path leaves give them, the codename tier 1 supplies, and that
    the shared path segment appears in NO other matched field.
    """
    rows = {r["session"]: r for h in match_gather()["hosts"].values()
            for r in h["windows"]}
    assert sorted(rows) == ["match-one", "match-two", "scratch2"]
    assert rows["match-one"]["label"] == "zzsigma"
    assert rows["match-two"]["label"] == "zztheta"
    assert rows["scratch2"]["codename"] == "Vapor"
    for r in rows.values():
        assert "zzpapaya" not in (r["task"] or "")
        assert "zzpapaya" not in (r["label"] or "")
        assert "zzpapaya" not in (r["codename"] or "")
    assert sum("zzpapaya" in r["path"] for r in rows.values()) == 2


def test_match_does_NOT_search_path_by_default():
    """🔴 THE MEASUREMENT, AS A TEST. On the live fleet one query substring hit
    1 of 72 rows on `task` and 29 of 72 on `path`, because nearly every window
    shares a repo path. A filter whose answer is 40% of the fleet is the
    unfiltered scan wearing a filter's authority."""
    assert _sessions(match_gather(match=["zzpapaya"])) == []
    assert "path" not in sm.match_fields()
    assert sm.MATCH_FIELDS == ("task", "label", "codename")


def test_match_path_ADDS_path_and_is_the_positive_control_for_the_zero_above():
    """🔴 Without this, the `0 rows` above is indistinguishable from a filter
    wired to nothing. Same term, same fixture, one flag — and the number moves
    from 0 to 2."""
    got = match_gather(match=["zzpapaya"], match_path=True)
    assert _sessions(got) == ["match-one", "match-two"]
    assert sm.match_fields(True) == ("task", "label", "codename", "path")
    assert got["summary"]["match_fields"] == ["task", "label", "codename", "path"]


@pytest.mark.parametrize("term,expect", [
    ("zzkiwi", ["match-one"]),          # task
    ("ZZKIWI", ["match-one"]),          # ...case-insensitively
    ("zzsigma", ["match-one"]),         # label (tier 2, the cwd leaf)
    ("zzmango", ["match-two"]),         # a DIFFERENT row's task
    ("vapor", ["scratch2"]),            # tier-1 codename/label, lower-cased
    ("zznothinghere", []),
])
def test_match_searches_task_label_and_codename(term, expect):
    assert _sessions(match_gather(match=[term])) == expect


def test_match_terms_are_ANDed_like_find_sessions_default():
    """Two tools read as one instrument; an OR here against the archive's AND
    would return different sets for the same words with nothing saying so."""
    assert _sessions(match_gather(match=["zzkiwi"])) == ["match-one"]
    assert _sessions(match_gather(match=["zzmango"])) == ["match-two"]
    # Together they match nothing, because no row carries both.
    assert _sessions(match_gather(match=["zzkiwi", "zzmango"])) == []
    # A pair that DOES co-occur on one row still matches — across two DIFFERENT
    # fields, which is the shape an OR-vs-AND mutant cannot fake.
    assert _sessions(match_gather(match=["zzkiwi", "zzsigma"])) == ["match-one"]


def test_row_matches_finds_a_CODENAME_that_disagrees_with_the_LABEL():
    """🔴 STATED HONESTLY, AND NOT COUNTED AS REGRESSION COVERAGE.

    On today's rows `label` EQUALS `codename` whenever a codename exists
    (`resolve_label` tier 1 returns the codename AS the label), so `codename` in
    `MATCH_FIELDS` adds no reach against production rows right now. It is in the
    set so that a change to that precedence — a path label winning over a slot
    name — cannot silently make a session unfindable by the name its hotkeys
    use. This drives the predicate directly with a row where the two DISAGREE,
    so the field is proven wired rather than assumed.
    """
    row = {"task": "zzalpha", "label": "zzbravo", "codename": "zzcharlie",
           "path": "zzdelta"}
    assert sm.row_matches(row, ["zzcharlie"]) is True
    assert sm.row_matches(row, ["zzbravo"]) is True
    assert sm.row_matches(row, ["zzalpha"]) is True
    assert sm.row_matches(row, ["zzdelta"]) is False
    assert sm.row_matches(row, ["zzdelta"], sm.match_fields(True)) is True


def test_the_span_fixture_matches_each_field_ALONE():
    """POSITIVE CONTROL for the span battery below: if neither field matched on
    its own, "the concatenation does not match" would be true of a predicate
    wired to nothing."""
    row = {"task": "abc", "label": "def", "codename": None, "path": None}
    assert sm.row_matches(row, ["abc"]) is True
    assert sm.row_matches(row, ["def"]) is True


# 🔴 EVERY PLAUSIBLE JOIN, NOT ONE. The first version of this guard asserted
# only `"abcdef"` — a ZERO-separator join — while its docstring claimed the
# fields are "never joined into one string". An audit mutated `row_matches` to a
# SPACE-joined haystack and all 653 tests stayed green: the description claimed
# coverage the body did not provide, which `claude/RULES.md` calls worse than no
# coverage. The separator set is what makes this a claim about the CLASS.
SPAN_SEPARATORS = (
    ("empty", ""), ("space", " "), ("pipe", "|"), ("newline", "\n"),
    ("tab", "\t"), ("nul", "\x00"), ("comma-space", ", "), ("em-dash", " — "),
)


@pytest.mark.parametrize("sep", [s for _, s in SPAN_SEPARATORS],
                         ids=[i for i, _ in SPAN_SEPARATORS])
def test_a_term_may_not_SPAN_two_fields(sep):
    """Joining the fields into one haystack would match text that exists in no
    field — a hit no reader can explain."""
    row = {"task": "abc", "label": "def", "codename": None, "path": None}
    assert sm.row_matches(row, ["abc" + sep + "def"]) is False, (
        f"a term spanning the field boundary matched under a {sep!r} join")


def test_an_EMPTY_term_list_matches_everything_rather_than_nothing():
    """"No filter was requested" and "a filter that rejects the world" are
    different facts. The CALLER decides whether a filter ran."""
    assert sm.row_matches({"task": "anything"}, []) is True
    got = match_gather(match=[])
    assert len(_sessions(got)) == 3
    assert got["filters"]["match"] is None
    assert got["summary"]["excluded_by_match"] is None


def test_every_count_and_caveat_describes_the_MATCHED_set():
    """🔴 The rule `--claude-only` already follows: a filtered report may not
    print a summary describing the unfiltered scan."""
    got = match_gather(match=["zzkiwi"])
    assert got["summary"]["total_sessions"] == 1
    assert got["summary"]["claude"] == 1
    assert sum(b["total"] for b in got["summary"]["status"].values()) == 1
    assert got["summary"]["kind"] == {"tmux": 1}
    assert got["summary"]["match"] == ["zzkiwi"]
    assert got["summary"]["match_fields"] == ["task", "label", "codename"]
    assert got["summary"]["excluded_by_match"] == 2
    assert got["filters"]["matched"] == 1
    # ...and the CAVEATS were RE-DERIVED, not inherited. When the filter removes
    # every row, `kinds_produced` must be empty rather than still claiming
    # `tmux` — the caveat line and the table it sits under cannot disagree.
    empty = match_gather(match=["zznothinghere"])
    assert empty["caveats"]["kind_scope"]["kinds_produced"] == []
    assert empty["caveats"]["kind_scope"]["kinds_excluded_by_filter"] == ["tmux"]


def test_match_and_claude_only_COMPOSE_and_share_one_kinds_excluded_answer():
    """Two row filters over one row set, and ONE answer to "which whole kinds
    did a filter remove" — computed across both rather than once per flag."""
    got = match_gather(match=["zzkiwi"], claude_only=True)
    assert _sessions(got) == ["match-one"]
    assert got["filters"]["excluded_shells"] == 0     # every fixture row is claude
    assert got["filters"]["excluded_by_match"] == 2
    assert got["filters"]["kinds_excluded_by_filter"] == []


def test_the_filters_key_says_a_filter_RAN_so_zero_rows_is_never_zero_windows():
    """A consumer reading an empty row list must be able to tell "nothing
    matched these words in these fields" from "the fleet has no windows"."""
    got = match_gather(match=["zznothinghere"])
    assert got["filters"]["match"] == ["zznothinghere"]
    assert got["filters"]["match_fields"] == ["task", "label", "codename"]
    assert got["filters"]["matched"] == 0
    assert got["filters"]["excluded_by_match"] == 3
    # ...and with NO filter every one of those is null, never 0/[].
    unfiltered = match_gather()
    for key in ("match", "match_fields", "matched", "excluded_by_match"):
        assert unfiltered["filters"][key] is None, key


def test_a_match_with_zero_hits_on_a_REACHABLE_fleet_is_EMPTY_not_OK():
    """🔴 `EXIT_OK` here would tell a caller the scan succeeded AND found the
    thing. The rows are gone; the hosts answered; that is EXIT_EMPTY."""
    assert sm.exit_code_for(match_gather(match=["zznothinghere"])) == sm.EXIT_EMPTY
    assert sm.exit_code_for(match_gather(match=["zzkiwi"])) == sm.EXIT_OK


def test_a_match_with_zero_hits_on_an_UNREACHABLE_fleet_is_UNAVAILABLE():
    """The zero is UNMEASURED there, and it must not read as a real zero."""
    down = make_runner(local_rc=1, local_err="tmux: connection failed",
                       remote_rc=255, remote_err="ssh: no route")
    got = base_gather(runner=down, use_fuzzyclaw=False, match=["zzkiwi"])
    assert sm.exit_code_for(got) == sm.EXIT_UNAVAILABLE


def test_main_match_end_to_end_through_the_CLI(monkeypatch, capsys,
                                               absent_blocked_cache):
    """END-TO-END, because every test above injects `gather()`."""
    monkeypatch.setattr(sm, "local_host_label", lambda *a, **k: "workbench")
    monkeypatch.setattr(sm, "_default_runner",
                        make_runner(local_panes=MATCH_PANES,
                                    local_windows=MATCH_WINDOWS))
    monkeypatch.setattr(sm, "read_fuzzyclaw_texts", lambda *a, **k: [])
    rc = sm.main(["scan", "--json", "--no-ch", "--lean", "--host", "workbench",
                  "--match", "zzkiwi"])
    blob = json.loads(capsys.readouterr().out)
    assert rc == sm.EXIT_OK
    rows = [r for h in blob["hosts"].values() for r in h["windows"]]
    assert [r["session"] for r in rows] == ["match-one"]
    assert blob["filters"]["match"] == ["zzkiwi"]
    assert blob["filters"]["match_fields"] == ["task", "label", "codename"]
    assert blob["summary"]["excluded_by_match"] == 2
    # the lean row carries the chord, which is what the live-first caller reads
    assert "hotkey_display" in rows[0]

    rc_empty = sm.main(["scan", "--json", "--no-ch", "--host", "workbench",
                        "--match", "zznothinghere"])
    capsys.readouterr()
    assert rc_empty == sm.EXIT_EMPTY


def test_the_TABLE_states_the_match_filter_and_the_fields_it_searched():
    """An empty table under a `--match` is otherwise indistinguishable from an
    empty fleet — and a reader who cannot see that `path` was excluded cannot
    tell why their repo-shaped query found nothing."""
    text = sm.render_table(match_gather(match=["zzkiwi"]))
    assert "FILTER --match 'zzkiwi'" in text
    assert "1 row(s) matched, 2 excluded" in text
    assert "fields searched = task, label, codename" in text
    # ...and no such line at all when no filter ran.
    assert "FILTER --match" not in sm.render_table(match_gather())


def test_match_has_no_effect_on_tail_and_SAYS_so(monkeypatch, capsys):
    """A silently ignored flag is how a caller concludes it was honoured."""
    monkeypatch.setattr(sm, "local_host_label", lambda *a, **k: "workbench")
    monkeypatch.setattr(sm, "_default_runner", make_runner())
    sm.main(["tail", "scratch7:3", "--host", "workbench", "--match", "zzkiwi"])
    assert "no effect on `tail`" in capsys.readouterr().err


# --------------------------------------------------------------------------- #
# detail — a miss must be LOUD, and must name the indices that DO exist
# --------------------------------------------------------------------------- #
# 🔴 THE OBSERVED FAILURE, REPRODUCED EXACTLY: a session named `scratch3` whose
# real window index is `2`, asked for as `scratch3:3` because the reader took
# the digit out of the session NAME.
SCRATCH3_PANES = "\n".join([
    "%41|4001|scratch3|1|w-a|/home/zach/workspace/zzsigma|zsh|a bare shell",
    f"%42|4002|scratch3|2|w-b|/home/zach/workspace/zztheta|claude"
    f"|{BRAILLE} the work that was lost track of",
])
SCRATCH3_WINDOWS = "@41|1|scratch3\n@42|2|scratch3\n"


def scratch3_gather(**kw):
    defaults = dict(
        runner=make_runner(local_panes=SCRATCH3_PANES,
                           local_windows=SCRATCH3_WINDOWS,
                           remote_panes="", remote_windows=""),
        use_fuzzyclaw=False)
    defaults.update(kw)
    return base_gather(**defaults)


def test_a_detail_miss_NAMES_THE_INDICES_THAT_DO_EXIST():
    """🔴 THE FIX FOR THE OBSERVED RUN. `scratch3:3` does not exist; `1` and `2`
    do, and saying so is what turns a dead end into a next step."""
    report = sm.filter_report(scratch3_gather(), "scratch3", "3")
    msg = sm.detail_not_found_message(report)
    assert msg is not None, "a miss returned NO message — the silent empty is back"
    assert "NO SUCH WINDOW 'scratch3:3'" in msg
    assert "session 'scratch3' has windows ['1', '2']" in msg
    assert "you asked for index '3'" in msg
    # ...and the same facts structurally, so a --json consumer never parses prose
    assert report["filters"]["detail_target"] == "scratch3:3"
    assert report["filters"]["detail_matched"] == 0
    assert report["filters"]["detail_sibling_indices"] == ["1", "2"]


def test_the_sibling_indices_are_sorted_NUMERICALLY_not_lexically():
    """`[1, 2, 10]`, not `[1, 10, 2]` — in a message whose whole job is to be
    read. The fixture indices overshoot the single digits deliberately: a set
    that never crosses 9 cannot tell the two sorts apart."""
    panes = "\n".join(
        f"%5{i}|500{i}|manywin|{i}|w-{i}|/w/synth-kilo|zsh|t" for i in (2, 10, 1))
    windows = "".join(f"@5{i}|{i}|manywin\n" for i in (2, 10, 1))
    report = sm.filter_report(
        base_gather(runner=make_runner(local_panes=panes, local_windows=windows,
                                       remote_panes="", remote_windows=""),
                    use_fuzzyclaw=False),
        "manywin", "99")
    assert report["filters"]["detail_sibling_indices"] == ["1", "2", "10"]


def test_a_detail_miss_on_an_UNKNOWN_SESSION_says_that_instead():
    """Two different next steps: fix the index, or find the right session."""
    report = sm.filter_report(scratch3_gather(), "zznosuchsession", "1")
    msg = sm.detail_not_found_message(report)
    assert "no session named 'zznosuchsession'" in msg
    assert "has windows" not in msg
    assert report["filters"]["detail_sibling_indices"] == []


def test_a_detail_that_FINDS_its_window_says_NOTHING():
    """NEGATIVE CONTROL. Without it, "report every detail as missing" is a
    mutation that passes every probe above."""
    report = sm.filter_report(scratch3_gather(), "scratch3", "2")
    assert report["filters"]["detail_matched"] == 1
    assert sm.detail_not_found_message(report) is None


def test_a_detail_miss_over_an_UNREACHABLE_FLEET_is_UNMEASURED_not_NOT_FOUND():
    """🔴 An address that could not be CHECKED is not an address that does not
    EXIST. Saying "no such window" here states a fact about a machine nobody
    talked to, and sends the reader to re-check their spelling instead of their
    SSH."""
    down = make_runner(local_rc=1, local_err="tmux: connection failed",
                       remote_rc=255, remote_err="ssh: no route")
    report = sm.filter_report(base_gather(runner=down, use_fuzzyclaw=False),
                              "scratch3", "3")
    assert sm.detail_not_found_message(report) is None
    assert report["filters"]["detail_matched"] is None
    assert report["filters"]["detail_sibling_indices"] is None, (
        "an EMPTY sibling list here would assert a measured absence of windows "
        "on hosts that never answered")
    assert sm.exit_code_for(report) == sm.EXIT_UNAVAILABLE


def test_a_PARTIAL_fleet_miss_names_the_host_it_could_not_search():
    """One host answered and one did not: the miss is real on the first and
    UNMEASURED on the second, and the message says both."""
    runner = make_runner(local_panes=SCRATCH3_PANES,
                         local_windows=SCRATCH3_WINDOWS,
                         remote_rc=255, remote_err="ssh: no route")
    report = sm.filter_report(base_gather(runner=runner, use_fuzzyclaw=False),
                              "scratch3", "3")
    msg = sm.detail_not_found_message(report)
    assert "searched: workbench" in msg
    assert "NOT searched: laptop" in msg
    assert "not a measured absence on that host" in msg
    # the siblings still come from the host that DID answer
    assert report["filters"]["detail_sibling_indices"] == ["1", "2"]


def test_the_detail_exit_CODES_were_already_right_and_stay_right():
    """INVARIANT GUARD — NOT regression coverage.

    `exit_code_for` already separated a reachable miss (3) from an unmeasured
    one (4) before this change; what was missing was the message and the
    structured count. Pinned so a refactor of the miss path cannot fold the two
    codes together while the new message keeps looking right.
    """
    hit = sm.filter_report(scratch3_gather(), "scratch3", "2")
    miss = sm.filter_report(scratch3_gather(), "scratch3", "3")
    down = sm.filter_report(
        base_gather(runner=make_runner(local_rc=1, local_err="tmux: down",
                                       remote_rc=255, remote_err="ssh: no route"),
                    use_fuzzyclaw=False),
        "scratch3", "3")
    assert sm.exit_code_for(hit) == sm.EXIT_OK
    assert sm.exit_code_for(miss) == sm.EXIT_EMPTY
    assert sm.exit_code_for(down) == sm.EXIT_UNAVAILABLE


def test_main_detail_MISS_is_loud_on_STDERR_and_leaves_stdout_parseable(
        monkeypatch, capsys):
    """END-TO-END. The human line goes to stderr so a `--json` consumer's stdout
    is untouched — and the same facts sit in `filters` for a consumer that never
    reads stderr at all."""
    monkeypatch.setattr(sm, "gather", lambda **kw: scratch3_gather())
    monkeypatch.setattr(sm, "local_host_label", lambda *a, **k: "workbench")
    rc = sm.main(["detail", "scratch3:3", "--json", "--no-ch"])
    cap = capsys.readouterr()
    assert rc == sm.EXIT_EMPTY
    assert "NO SUCH WINDOW 'scratch3:3'" in cap.err
    assert "['1', '2']" in cap.err
    blob = json.loads(cap.out)
    assert blob["filters"]["detail_matched"] == 0
    assert blob["filters"]["detail_sibling_indices"] == ["1", "2"]


def test_main_detail_HIT_prints_no_miss_line(monkeypatch, capsys):
    """NEGATIVE CONTROL on the CLI wiring, not just on the pure function."""
    monkeypatch.setattr(sm, "gather", lambda **kw: scratch3_gather())
    monkeypatch.setattr(sm, "local_host_label", lambda *a, **k: "workbench")
    rc = sm.main(["detail", "scratch3:2", "--json", "--no-ch"])
    cap = capsys.readouterr()
    assert rc == sm.EXIT_OK
    assert "NO SUCH WINDOW" not in cap.err


def test_filter_report_does_not_MUTATE_the_report_it_narrows():
    """INVARIANT GUARD. `filters` is written through on the narrowed COPY; the
    original must not grow `detail_*` keys, or a second `filter_report` over the
    same report would inherit the first one's verdict."""
    report = scratch3_gather()
    before = copy.deepcopy(report["filters"])
    sm.filter_report(report, "scratch3", "3")
    assert report["filters"] == before


def test_detail_json_is_the_FULL_REPORT_SHAPE_not_a_bare_window(monkeypatch,
                                                                capsys):
    """🔴 THE FIRST OF THE FIVE WASTED CALLS IN THE OBSERVED RUN: it assumed
    `detail --json` returns `{"window": {...}}`. It returns the whole report
    with `hosts[*].windows` NARROWED — hosts keep their reachability facts,
    because dropping them would turn "unreachable" into "not found".

    INVARIANT GUARD on the shape (it was always this), pinned because the
    reference doc now states it and a claim nothing checks is the failure mode
    this repo keeps re-finding.
    """
    monkeypatch.setattr(sm, "gather", lambda **kw: scratch3_gather())
    monkeypatch.setattr(sm, "local_host_label", lambda *a, **k: "workbench")
    sm.main(["detail", "scratch3:2", "--json", "--no-ch"])
    blob = json.loads(capsys.readouterr().out)
    assert "window" not in blob, "detail grew a bare `window` key"
    assert set(blob) >= {"hosts", "summary", "filters", "caveats",
                         "session_history"}
    assert set(blob["hosts"]) == {"workbench", "laptop"}
    row = blob["hosts"]["workbench"]["windows"][0]
    # ...and the two row keys the same run got wrong.
    assert row["window_index"] == "2" and "window" not in row
    assert row["path"] and "cwd" not in row


# =========================================================================== #
# §15 — AUDIT FIX ROUND 1 (against tip a6f09d5a)
#
# 🔴 THE TWO DEPLOY-BLOCKERS AN ADVERSARIAL AUDIT FOUND, and three smaller ones.
# Only the session-manager half is here; `test_find_session_live.py` §2 carries
# the `find-session.py` half.
#
#   R1-2  A `detail` MISS UNDER A ROW FILTER ASSERTED A FALSE MEASURED ABSENCE.
#         `gather` filters rows BEFORE `filter_report` narrows, so the sibling
#         list enumerated only the survivors. Reproduced live:
#           detail scratch3:1 --match datapacket
#           -> "session 'scratch3' has windows ['2']"
#         while window 1 existed on BOTH searched hosts. `(searched: laptop,
#         workbench)` made it read as authoritative — worse than the silent
#         empty it replaced, which at least asserted nothing.
#   R1-5  `filters.matched` / `.excluded_by_match` published a measured `0` over
#         a fleet where NO host answered, while `detail_matched` was already
#         `None` in that exact state.
#   R1-6  The table's filter line quoted `total_sessions`, which is a DIFFERENT
#         number from `filters.matched` on a `detail` — and `filters.matched`
#         was read by nothing.
#   R1-7  The span guard was spelled to a zero-separator join (fixed above).
#
# 🔴 WHICH OF THESE IS REGRESSION COVERAGE. MEASURED at NODE level (params
# counted individually), by collecting both files at both shas and running the
# head tests against base source: **66 new nodes across the two files, 46 RED
# and 20 GREEN at a6f09d5a.**
#
# ⚠ AN EARLIER REVISION OF THIS PARAGRAPH SAID "56 nodes … 10 GREEN", AND ITS
# COMPLETENESS SENTENCE WAS FALSE. The red count was exact; the node and green
# counts were taken from a FUNCTION-level sweep that skipped `parametrize`
# expansion and skipped functions whose NAME already existed at base. The 10
# it missed were the 8 `test_a_term_may_not_SPAN_two_fields[…]` params (green at
# base — disclosed in prose, absent from the machine ledger) and the 2
# `test_the_R1_invariant_guard_ledger_names_only_tests_that_exist` nodes. A
# ledger whose sentence claims to name every green while naming 10 of 20 is the
# "description wider than the implementation" shape both of round 1's blockers
# had.
#
# So the parametrised entries are now DERIVED from the same tuple the
# `parametrize` reads (`SPAN_SEPARATORS`), not retyped: adding a separator
# extends the ledger automatically and cannot drift from the test it counts.
#
# ⚠ ONE FINDING IN ROUND 1 WAS A GUARD GAP, NOT A CODE DEFECT. The span guard
# passes at a6f09d5a in both its old and new forms, because `row_matches` was
# already per-field — the auditor's space-joined MUTANT is what the old spelling
# could not see. Its evidence is the mutation sweep (N27/N28), not a red-at-tip
# run. That is why all 8 of its params are GREEN below.
# =========================================================================== #
R1_INVARIANT_GUARDS = frozenset({
    # A partially reachable fleet already published real counts; R1-5 is only
    # about the NO-host-answered state.
    "test_a_PARTIALLY_reachable_fleet_still_publishes_real_counts",
    # The fixture control for the R1-2 probes.
    "test_the_scratch3_fixture_really_has_BOTH_windows_before_any_filter",
    # The positive control for the span battery.
    "test_the_span_fixture_matches_each_field_ALONE",
    # This ledger's own gate — it has no behaviour to regress.
    "test_the_R1_invariant_guard_ledger_names_only_tests_that_exist",
}) | {
    # 🔴 DERIVED, not retyped. See the paragraph above: these 8 were green at
    # base and missing from the hand-written ledger.
    f"test_a_term_may_not_SPAN_two_fields[{ident}]" for ident, _ in SPAN_SEPARATORS
}

# 🔴 RED AT BASE FOR A HARNESS REASON, NOT A BEHAVIOURAL ONE — a THIRD category,
# because calling these regression coverage overstates it and calling them
# invariant guards contradicts the measurement.
#
# `test_the_prefilter_map_excludes_hosts_that_never_answered` counts RED at
# a6f09d5a only because the `prefilter_index_map` kwarg did not exist there, so
# `gather` raised `TypeError` before any assertion ran. Its own docstring calls
# it an invariant guard, and that is the truthful description of what it PINS;
# the red is an artefact of a new parameter name. Both statements are true and
# the disagreement between them was worth reconciling rather than picking one.
R1_RED_ONLY_BECAUSE_A_SYMBOL_IS_NEW = frozenset({
    "test_the_prefilter_map_excludes_hosts_that_never_answered",
})


@pytest.mark.parametrize("ledger,label", [
    (R1_INVARIANT_GUARDS, "R1_INVARIANT_GUARDS"),
    (R1_RED_ONLY_BECAUSE_A_SYMBOL_IS_NEW, "R1_RED_ONLY_BECAUSE_A_SYMBOL_IS_NEW"),
], ids=["invariant-guards", "harness-red"])
def test_the_R1_invariant_guard_ledger_names_only_tests_that_exist(ledger, label):
    """Every ledger entry must resolve to a real test — a name that does not is
    a claim about coverage that nothing backs.

    Parametrised node ids (`name[param]`) are checked on their FUNCTION half;
    the param half is derived from the same tuple the `parametrize` reads, so it
    cannot name an id that does not exist.
    """
    assert ledger, f"{label} is empty — the gate is wired to nothing"
    for entry in ledger:
        func = entry.split("[", 1)[0]
        assert func in globals(), (
            f"{entry!r} is listed in {label} but no such test exists")


def scratch3_detail(target_index, **kw):
    """A `detail` report over the scratch3 fixture, WITH the pre-filter map.

    `prefilter_index_map=True` mirrors what `main()` passes for the `detail`
    subcommand — the parameter exists so a 1-row `--match` scan does not carry a
    whole fleet's index map back.
    """
    kw.setdefault("prefilter_index_map", True)
    return sm.filter_report(scratch3_gather(**kw), "scratch3", target_index)


def test_the_scratch3_fixture_really_has_BOTH_windows_before_any_filter():
    """POSITIVE CONTROL. Every R1-2 assertion is about a window the filter
    removes; if the fixture never built it, they would all pass vacuously."""
    rows = [r for h in scratch3_gather()["hosts"].values() for r in h["windows"]]
    assert sorted((r["session"], r["window_index"]) for r in rows) == [
        ("scratch3", "1"), ("scratch3", "2")]
    # ...and window 1 is the SHELL, so `--claude-only` is what removes it.
    by_idx = {r["window_index"]: r for r in rows}
    assert by_idx["1"]["claude"] is False
    assert by_idx["2"]["claude"] is True


@pytest.mark.parametrize("kw,flag", [
    (dict(claude_only=True), "--claude-only"),
    # a term that only the CLAUDE window's task carries, so the shell row goes
    (dict(match=["lost track"]), "--match 'lost track'"),
], ids=["claude-only", "match"])
def test_a_detail_MISS_CAUSED_BY_A_ROW_FILTER_says_so_instead_of_NO_SUCH_WINDOW(
        kw, flag):
    """🔴 R1-2, THE HEADLINE. The window EXISTS; a row filter removed its row.
    Calling that "NO SUCH WINDOW" is a flat falsehood, and the `(searched: …)`
    clause makes it read as authoritative."""
    report = scratch3_detail("1", **kw)
    msg = sm.detail_not_found_message(report)
    assert msg is not None
    assert "WINDOW 'scratch3:1' EXISTS but a ROW FILTER" in msg
    assert flag in msg
    assert "NOT a measured absence" in msg
    assert "NO SUCH WINDOW" not in msg, (
        "the filtered-out case still claims the window does not exist")
    # ...and structurally, for a consumer that never reads stderr
    assert report["filters"]["detail_filtered_out"] is True
    assert report["filters"]["detail_matched"] == 0


@pytest.mark.parametrize("kw", [
    dict(claude_only=True),
    dict(match=["lost track"]),
], ids=["claude-only", "match"])
def test_the_sibling_indices_are_measured_BEFORE_the_row_filter(kw):
    """🔴 R1-2, the other half: the enumerated list must be a fact about the
    SCAN, not about the filter. `['2']` was the shipped answer."""
    report = scratch3_detail("9", **kw)
    assert report["filters"]["detail_sibling_indices"] == ["1", "2"], (
        "the sibling list is filter-scoped again — it enumerates survivors")
    msg = sm.detail_not_found_message(report)
    assert "session 'scratch3' has windows ['1', '2']" in msg
    # ...and the reader is told a filter was in play, so the count above and the
    # empty table below cannot be read as disagreeing.
    assert "measured BEFORE the row filter" in msg
    assert report["filters"]["detail_filtered_out"] is False


def test_WITHOUT_a_row_filter_the_message_makes_NO_filter_claim():
    """NEGATIVE CONTROL. Without it, "always mention a filter" passes both
    probes above while lying on the common path."""
    report = scratch3_detail("9")
    msg = sm.detail_not_found_message(report)
    assert "NO SUCH WINDOW 'scratch3:9'" in msg
    assert "ROW FILTER" not in msg and "BEFORE the row filter" not in msg
    assert report["filters"]["detail_filtered_out"] is False
    # ...and nothing was sampled, because no filter ran.
    assert report["filters"]["prefilter_window_indices"] is None


def test_an_unknown_SESSION_under_a_filter_still_says_no_such_session():
    """The third miss shape keeps its own wording, and still discloses the
    filter — a `[]` sibling list under a filter is a pre-filter measurement and
    the reader has to be able to tell."""
    report = scratch3_detail("1", claude_only=True)
    report["filters"]["detail_target"] = "zznosuch:1"
    report["filters"]["detail_sibling_indices"] = []
    report["filters"]["detail_filtered_out"] = False
    msg = sm.detail_not_found_message(report)
    assert "no session named 'zznosuch'" in msg
    assert "measured BEFORE the row filter --claude-only" in msg


def test_the_prefilter_map_is_OPT_IN_so_a_match_scan_does_not_carry_it():
    """The map exists for `detail`. A 1-row `--match` scan carrying a whole
    fleet's index map would defeat the flag's entire purpose."""
    scan = match_gather(match=["zzkiwi"])
    assert scan["filters"]["prefilter_window_indices"] is None
    detail_side = match_gather(match=["zzkiwi"], prefilter_index_map=True)
    pre = detail_side["filters"]["prefilter_window_indices"]
    assert pre is not None
    # every session the scan SAW, not just the one row that survived
    assert sorted(pre) == ["match-one", "match-two", "scratch2"]
    assert pre["match-one"] == ["1"]
    # ...and it is never sampled when NO filter ran, because then the rows
    # themselves are the unfiltered set.
    assert match_gather(prefilter_index_map=True)[
        "filters"]["prefilter_window_indices"] is None


@pytest.mark.parametrize("kw,label", [
    (dict(remote_rc=255, remote_err="ssh: no route"), "ssh-failure"),
    (dict(remote_rc=1, remote_err="tmux: connection failed"), "tmux-failure"),
    (dict(remote_rc=127, remote_err="command not found: tmux"), "no-tmux-binary"),
], ids=["ssh-failure", "tmux-failure", "no-tmux-binary"])
def test_an_unreachable_host_carries_NO_rows_which_is_what_makes_the_maps_safe(
        kw, label):
    """🔴 THE PRECONDITION, PINNED — AND WHAT THIS CAN AND CANNOT SEE.

    Both the pre-filter index map and `filter_report`'s row-derived fallback
    rely on a host that never answered contributing no window indices. This
    asserts that PROPERTY across three unreachable modes.

    🔴 IT DOES **NOT** PIN ANY ONE ENFORCEMENT OF IT, and an earlier revision of
    this docstring claimed it did ("where a change to `gather` really would
    red"). That sentence was false. The property is OVER-DETERMINED: `run_tmux`
    returns `stdout: ""` on every unreachable path so `fold_windows` yields no
    rows, AND `gather`'s population loop skips a host that did not answer. An
    audit deleted the second and all 649 tests stayed green — correctly, because
    the first still held. So this is an INVARIANT guard on the property; it
    cannot attribute the property to a mechanism, and a green run here is not
    licence to delete the remaining enforcement.

    What it does catch is the property actually breaking — e.g. a `gather` that
    marks an unreachable host `reachable` (mutant N10), or any future path that
    populates rows for a host with no answer.
    """
    runner = make_runner(local_panes=SCRATCH3_PANES,
                         local_windows=SCRATCH3_WINDOWS, **kw)
    got = base_gather(runner=runner, use_fuzzyclaw=False)
    # THE INVARIANT, over every host rather than one named one: no row may exist
    # for any host that did not answer.
    #
    # 🔴 THE OBSERVATION IS MATERIALISED BEFORE IT IS JUDGED, so an empty sweep
    # cannot pass. A mutation sweep caught the earlier `for name in unreachable:`
    # form SURVIVING a `[:0]` slice — the loop body never ran and every other
    # assertion in the test still held, which is a guard that reads as coverage
    # while executing nothing.
    unreachable = [n for n, h in got["hosts"].items() if not h.get("reachable")]
    assert unreachable, f"fixture {label}: no host went unreachable"
    observed = {n: got["hosts"][n]["windows"] for n in unreachable}
    assert len(observed) == len(unreachable) >= 1, (
        "the sweep visited fewer hosts than it claimed to")
    for name, windows in observed.items():
        assert windows == [], (
            f"unreachable host {name} carries rows — the pre-filter index map "
            "and `filter_report`'s sibling fallback both rely on this being "
            "impossible")
    # positive control: a REACHABLE host in the same scan does carry rows, so
    # the assertion above is not observing an empty scan.
    assert any(got["hosts"][n]["windows"] for n in got["hosts"]
               if n not in unreachable)


def test_the_prefilter_map_excludes_hosts_that_never_answered():
    """The consequence of the precondition above, observed on the map itself.

    INVARIANT GUARD, not regression coverage: it holds whether or not the map
    filters on reachability, because there is nothing to filter.
    """
    runner = make_runner(local_panes=SCRATCH3_PANES,
                         local_windows=SCRATCH3_WINDOWS,
                         remote_rc=255, remote_err="ssh: no route")
    got = base_gather(runner=runner, use_fuzzyclaw=False, claude_only=True,
                      prefilter_index_map=True)
    assert got["filters"]["prefilter_window_indices"] == {"scratch3": ["1", "2"]}


def test_a_detail_over_an_UNREACHABLE_fleet_leaves_filtered_out_None_too():
    """The fourth field joins the same rule: nothing was measured, so it is not
    `False` (which would assert the filter did not remove it)."""
    down = make_runner(local_rc=1, local_err="tmux: connection failed",
                       remote_rc=255, remote_err="ssh: no route")
    report = sm.filter_report(
        base_gather(runner=down, use_fuzzyclaw=False, claude_only=True,
                    prefilter_index_map=True),
        "scratch3", "1")
    assert report["filters"]["detail_filtered_out"] is None
    assert report["filters"]["detail_matched"] is None
    assert report["filters"]["detail_sibling_indices"] is None
    assert sm.detail_not_found_message(report) is None


def test_main_detail_under_a_filter_is_LOUD_about_the_FILTER(monkeypatch,
                                                             capsys):
    """END-TO-END, and it pins that `main()` asks `gather` for the pre-filter
    map on the `detail` subcommand — without that the message regresses."""
    seen = {}

    def fake_gather(**kw):
        seen.update(kw)
        return scratch3_gather(claude_only=kw.get("claude_only", False),
                               prefilter_index_map=kw.get(
                                   "prefilter_index_map", False))

    monkeypatch.setattr(sm, "gather", fake_gather)
    monkeypatch.setattr(sm, "local_host_label", lambda *a, **k: "workbench")
    rc = sm.main(["detail", "scratch3:1", "--json", "--no-ch", "--claude-only"])
    cap = capsys.readouterr()
    assert seen["prefilter_index_map"] is True, (
        "main() did not ask for the pre-filter map on a `detail`")
    assert rc == sm.EXIT_EMPTY
    assert "EXISTS but a ROW FILTER" in cap.err
    blob = json.loads(cap.out)
    assert blob["filters"]["detail_filtered_out"] is True


def test_main_SCAN_does_not_ask_for_the_prefilter_map(monkeypatch, capsys):
    """NEGATIVE CONTROL on the same wiring — a `scan` must not pay for it."""
    seen = {}

    def fake_gather(**kw):
        seen.update(kw)
        return match_gather(match=kw.get("match"))

    monkeypatch.setattr(sm, "gather", fake_gather)
    monkeypatch.setattr(sm, "local_host_label", lambda *a, **k: "workbench")
    sm.main(["scan", "--json", "--no-ch", "--match", "zzkiwi"])
    capsys.readouterr()
    assert seen["prefilter_index_map"] is False


# --------------------------------------------------------------------------- #
# R1-5 — a count over a fleet nobody reached is not a zero
# --------------------------------------------------------------------------- #
def test_match_counts_are_None_not_ZERO_over_an_unreachable_fleet():
    """🔴 R1-5. `matched: 0` there says "the filter ran and matched nothing"
    about a scan that measured nothing. `detail_matched` was already `None` in
    exactly this state; these now agree with it."""
    down = make_runner(local_rc=1, local_err="tmux: connection failed",
                       remote_rc=255, remote_err="ssh: no route")
    got = base_gather(runner=down, use_fuzzyclaw=False, match=["zzkiwi"])
    assert got["filters"]["matched"] is None
    assert got["filters"]["excluded_by_match"] is None
    assert got["summary"]["matched"] is None
    assert got["summary"]["excluded_by_match"] is None
    # ...but WHICH filter was requested is known regardless, so the terms and
    # the field list are still published. Dropping them would lose the only
    # thing that distinguishes this from an unfiltered unreachable scan.
    assert got["filters"]["match"] == ["zzkiwi"]
    assert got["filters"]["match_fields"] == ["task", "label", "codename"]
    assert sm.exit_code_for(got) == sm.EXIT_UNAVAILABLE


def test_match_counts_ARE_real_zeros_when_the_fleet_ANSWERED():
    """The measured counterpart, so the None above is a filter decision and not
    a field wired to null. A reachable fleet with no match publishes `0`."""
    got = match_gather(match=["zznothinghere"])
    assert got["filters"]["matched"] == 0
    assert got["filters"]["excluded_by_match"] == 3
    assert got["summary"]["matched"] == 0


def test_a_PARTIALLY_reachable_fleet_still_publishes_real_counts():
    """One host answering is a measurement. The null applies only when NOTHING
    answered — otherwise the flag would go null on the fleet's most common
    degraded state and stop being readable at all."""
    runner = make_runner(local_panes=MATCH_PANES, local_windows=MATCH_WINDOWS,
                         remote_rc=255, remote_err="ssh: no route")
    got = base_gather(runner=runner, use_fuzzyclaw=False, match=["zzkiwi"])
    assert got["filters"]["matched"] == 1
    assert got["filters"]["excluded_by_match"] == 2


# --------------------------------------------------------------------------- #
# R1-6 — the table's filter line quoted the wrong number
# --------------------------------------------------------------------------- #
def test_the_table_filter_line_quotes_MATCHED_not_total_sessions():
    """🔴 R1-6. On a `detail` the two disagree by construction: the row filter
    matched 2 and `filter_report` then narrowed to 1, so `total_sessions` is 1.
    The line describes the FILTER, so it must quote the filter's number — which
    was sitting unread in `filters.matched`."""
    scan = match_gather(match=["zz"])
    assert scan["filters"]["matched"] == 2, "fixture: --match zz must hit 2 rows"
    narrowed = sm.filter_report(scan, "match-one", "1")
    assert narrowed["summary"]["total_sessions"] == 1, (
        "fixture broken: the two numbers must DIFFER or this proves nothing")
    text = sm.render_table(narrowed)
    assert "2 row(s) matched" in text
    assert "1 row(s) matched" not in text


def test_the_table_filter_line_says_UNMEASURED_rather_than_printing_a_null():
    """Over an unreachable fleet both counts are None; the sentence must read as
    a sentence, not print `None row(s) matched`."""
    down = make_runner(local_rc=1, local_err="tmux: connection failed",
                       remote_rc=255, remote_err="ssh: no route")
    text = sm.render_table(
        base_gather(runner=down, use_fuzzyclaw=False, match=["zzkiwi"]))
    assert "an unmeasured number of row(s) matched" in text
    assert "None row(s)" not in text


def test_summary_matched_is_MIRRORED_from_filters_not_recomputed():
    """One writer. A second derivation here is how `total_sessions` came to be
    quoted in the first place."""
    scan = match_gather(match=["zz"])
    assert scan["summary"]["matched"] == scan["filters"]["matched"] == 2
    narrowed = sm.filter_report(scan, "match-one", "1")
    assert narrowed["summary"]["matched"] == 2
    assert narrowed["summary"]["total_sessions"] == 1


def test_the_active_row_filter_list_is_ONE_definition():
    """Both miss messages and the tests name the same set; a second copy would
    describe filters the first does not."""
    assert sm._active_row_filters({}) == []
    assert sm._active_row_filters({"claude_only": True}) == ["--claude-only"]
    assert sm._active_row_filters({"match": ["a", "b"]}) == ["--match 'a' 'b'"]
    assert sm._active_row_filters(
        {"claude_only": True, "match": ["a"]}) == ["--claude-only", "--match 'a'"]


# =========================================================================== #
# §16 — AUDIT FIX ROUND 2 (against tip 9f9dcbde), session-manager half
#
#   R2-F5  `test_an_unreachable_host_carries_NO_rows_…` claimed to pin an
#          invariant "where a change to `gather` really would red". It does not:
#          the property is OVER-DETERMINED — `run_tmux` returns empty stdout on
#          every unreachable path AND the population loop skips such a host — so
#          an audit deleted the second enforcement and all 649 tests stayed
#          green, correctly. The CODE was never weakened; the SENTENCE was
#          wider than its assertion, which is the shape of both round-0
#          blockers. The docstring now says what it can and cannot see, and the
#          assertion is widened to the property across three unreachable modes
#          and EVERY host rather than one named one.
#   R2-F4  The node ledger said 56 nodes / 10 green; the measurement is 66 / 20.
#          The parametrised entries are DERIVED from `SPAN_SEPARATORS` now.
# =========================================================================== #

# 🔴 MEASURED AT NODE LEVEL against 9f9dcbde — 29 NEW nodes across the two
# files, 16 RED and 13 GREEN. EIGHT of the green are from THIS file. They are
# all controls or prose/ledger guards; none is evidence a bug was fixed.
#
# ⚠ `test_the_unreachable_host_precondition_docstring_does_not_OVERCLAIM` is
# green at EVERY sha BY CONSTRUCTION: the artifact it inspects is a docstring in
# THIS file, so a run against older SOURCE still reads the new prose. It pins
# that a retracted overclaim stays retracted; it can never be red-at-base and is
# not offered as regression coverage.
R2_INVARIANT_GUARDS = frozenset({
    # The precondition itself held at 9f9dcbde — F5 was about the SENTENCE, not
    # the property. All three unreachable modes are green there.
    *(f"test_an_unreachable_host_carries_NO_rows_which_is_what_makes_the_maps_safe[{i}]"
      for i in ("ssh-failure", "tmux-failure", "no-tmux-binary")),
    # Ledger gates and prose guards — structural, no behaviour to regress.
    "test_the_R1_invariant_guard_ledger_names_only_tests_that_exist",
    "test_the_R1_ledgers_do_not_OVERLAP",
    "test_the_span_ledger_entries_are_DERIVED_from_the_parametrize_source",
    "test_the_unreachable_host_precondition_docstring_does_not_OVERCLAIM",
})


def test_the_R2_ledger_names_only_tests_that_exist():
    assert R2_INVARIANT_GUARDS, "the ledger is empty — the gate is wired to nothing"
    for entry in R2_INVARIANT_GUARDS:
        assert entry.split("[", 1)[0] in globals(), (
            f"{entry!r} is listed as an R2 invariant guard but no such test exists")


def test_the_R1_ledgers_do_not_OVERLAP():
    """A test cannot be both an invariant guard and red-only-for-a-symbol. The
    two ledgers partition; an entry in both would make either claim unfalsifiable."""
    assert not (R1_INVARIANT_GUARDS & R1_RED_ONLY_BECAUSE_A_SYMBOL_IS_NEW)


def test_the_span_ledger_entries_are_DERIVED_from_the_parametrize_source():
    """🔴 R2-F4, structurally. The 8 span params were green at base and absent
    from the hand-written ledger while its sentence claimed to name every green.
    Deriving them from the SAME tuple the `parametrize` reads is what stops the
    two drifting again — this pins that they still are derived, and that the
    count matches."""
    span = {e for e in R1_INVARIANT_GUARDS
            if e.startswith("test_a_term_may_not_SPAN_two_fields[")}
    assert len(span) == len(SPAN_SEPARATORS) == 8
    assert span == {f"test_a_term_may_not_SPAN_two_fields[{i}]"
                    for i, _ in SPAN_SEPARATORS}
    # ...and the ids really are the ones pytest will generate: no duplicates,
    # and each separator distinct, so a param cannot silently collapse.
    idents = [i for i, _ in SPAN_SEPARATORS]
    assert len(set(idents)) == len(idents)
    assert len({s for _, s in SPAN_SEPARATORS}) == len(SPAN_SEPARATORS)


def test_the_unreachable_host_precondition_docstring_does_not_OVERCLAIM():
    """🔴 R2-F5 AS A PROSE GUARD. The retracted sentence promised that a change
    to `gather` "really would red" — it does not, because the property is
    double-enforced. A docstring wider than its assertion is what this whole PR
    keeps being audited for, so the retraction is pinned rather than trusted.
    """
    doc = test_an_unreachable_host_carries_NO_rows_which_is_what_makes_the_maps_safe.__doc__
    assert "OVER-DETERMINED" in doc
    assert "cannot attribute the property to a mechanism" in doc
    assert "where a change to `gather` really would red" not in doc, (
        "the retracted overclaim is back in the docstring")


def test_the_gather_comment_names_BOTH_enforcements_of_the_precondition():
    """A maintainer reading only `gather` must not conclude a red gate protects
    the second enforcement. The comment names both mechanisms and says deleting
    both is undetected."""
    import inspect
    src = inspect.getsource(sm.gather)
    assert "OVER-DETERMINED" in src
    assert "do not read a green suite as licence to delete" in src.lower()


# =========================================================================== #
# §P — `pane_preview`: the captured screen this tool already had and threw away
#
# 🔴 THE DEFECT CLASS THIS SECTION EXISTS FOR. `waiting_probable` and
# `unsent_prompt` are both DERIVED from the capture batch, which then discarded
# the screen. Publishing it is cheap — the text is already in `captures` — but it
# introduces a THIRD field pair riding one capture, and the first two got their
# own statuses precisely because sharing one is how "not measured" becomes
# "measured empty". These tests pin that the third does not regress that.
#
# Every test here drives `fold_windows` through a real `gather`, because the
# statuses are decided by which of four capture paths a row took, and a test that
# calls the builder directly cannot see a path it was never routed down.
# =========================================================================== #

def test_the_preview_carries_the_pane_SCREEN_VERBATIM_when_asked():
    """END-TO-END: capture goes in, the screen comes out on the row.

    🔴 Asserts the WHOLE captured string, not a substring of it. A `in` check
    passes against a preview that dropped every line but one, which is exactly
    what a truncation bug produces — and a screen-dump field whose contract is
    "this is what the pane shows" cannot be pinned by a fragment.
    """
    rep = waiting_gather(local={"%11": PANE_IDLE}, pane_preview=True)
    row = _row(rep, "workbench", "scratch7", "3")
    assert row["pane_preview"] == PANE_IDLE
    assert row["pane_preview_status"] == "ok"


def test_WITHOUT_the_flag_the_status_is_disabled_NOT_a_measured_empty_screen():
    """🔴 THE NULL-VS-NULL DISTINCTION, and it is the whole reason the status
    field exists. Off by default, `pane_preview` is null on every row — and a
    consumer must be able to tell that from a fleet whose panes are genuinely
    blank. `disabled` says NOTHING WAS ASKED.

    KILLS: emitting the fields only when the flag is on (absence is not a
    readable status), and defaulting the status to `ok` or `not_claude`.
    """
    rep = waiting_gather(local={"%11": PANE_IDLE})
    row = _row(rep, "workbench", "scratch7", "3")
    assert row["pane_preview"] is None
    assert row["pane_preview_status"] == "disabled"
    # ...and the field PAIR is present on every row, not just the claude one.
    for host in ("workbench", "laptop"):
        for r in rep["hosts"][host]["windows"]:
            assert r["pane_preview_status"] == "disabled"
            assert r["pane_preview"] is None


def test_disabled_BEATS_every_other_reason_including_ones_that_were_measured():
    """🔴 PRECEDENCE, stated as a test because the implementation computes the
    other statuses FIRST and then overwrites them.

    The sibling `unsent_prompt_status` on the SAME rows reports the real capture
    path (`ok` for the captured claude pane, `not_claude` for a shell). If
    `pane_preview_status` ever reported those instead, a consumer would read
    `not_claude` and conclude the fleet holds no Claude panes — when in fact
    nobody asked for the text. The two fields must DISAGREE here, and that
    disagreement is correct.
    """
    rep = waiting_gather(local={"%11": PANE_IDLE})
    claude = _row(rep, "workbench", "scratch7", "3")
    assert claude["unsent_prompt_status"] == "ok"        # measured, capture ran
    assert claude["pane_preview_status"] == "disabled"   # ...but never asked
    shells = [r for r in rep["hosts"]["workbench"]["windows"] if not r["claude"]]
    assert shells, "fixture must contain a shell row for this to mean anything"
    for r in shells:
        assert r["unsent_prompt_status"] == "not_claude"
        assert r["pane_preview_status"] == "disabled"


def test_a_SHELL_row_reports_not_claude_never_an_empty_screen():
    """The capture batch is Claude-panes-only, so a shell was never read. `""`
    would say "this pane's screen is blank", which is a measurement nobody
    took."""
    rep = waiting_gather(local={"%11": PANE_IDLE}, pane_preview=True)
    shells = [r for r in rep["hosts"]["workbench"]["windows"] if not r["claude"]]
    assert shells
    for r in shells:
        assert r["pane_preview"] is None
        assert r["pane_preview_status"] == "not_claude"


def test_a_claude_pane_MISSING_from_the_batch_reports_uncaptured():
    """The batch ran and answered, and this pane was not in it — distinct from
    the batch not running at all. Same four-path discipline as `waiting`."""
    rep = waiting_gather(local={}, pane_preview=True)   # batch ran, no %11 in it
    row = _row(rep, "workbench", "scratch7", "3")
    assert row["pane_preview"] is None
    assert row["pane_preview_status"] == "uncaptured"


def test_no_capture_propagates_the_BATCHS_OWN_reason_to_the_preview():
    """🔴 `skipped` is not `uncaptured`. With `--no-capture` the batch never ran,
    so the reason belongs to the batch; reporting `uncaptured` would blame the
    pane for a decision the caller made."""
    rep = base_gather(use_capture=False, pane_preview=True)
    row = _row(rep, "workbench", "scratch7", "3")
    assert row["pane_preview"] is None
    assert row["pane_preview_status"] == "skipped"
    assert row["unsent_prompt_status"] == "skipped"      # the sibling agrees here


def test_the_preview_status_VOCABULARY_IS_CLOSED():
    """🔴 Same rule as UNSENT_STATUSES: a status outside the enumeration is one
    no consumer branches on, and it would be published silently. Swept across
    every path this file can drive, flag ON and OFF."""
    reps = [
        waiting_gather(local={"%11": PANE_IDLE}, pane_preview=True),
        waiting_gather(local={"%11": PANE_IDLE}),
        waiting_gather(local={}, pane_preview=True),
        base_gather(use_capture=False, pane_preview=True),
        base_gather(runner=make_runner(local_rc=1, remote_rc=1),
                    pane_preview=True),
    ]
    seen = set()
    for rep in reps:
        for host in rep["hosts"].values():
            for r in host["windows"]:
                seen.add(r["pane_preview_status"])
    assert seen, "swept nothing — the fixtures produced no rows"
    assert seen <= set(sm.PREVIEW_STATUSES), f"undeclared status: {seen}"
    # POSITIVE CONTROL: the sweep really did reach more than one path, so a
    # vocabulary that happened to hold one value is not what made this pass.
    assert len(seen) >= 3, seen


def test_an_EMPTY_captured_screen_is_ok_with_an_empty_string_NOT_null():
    """🔴 A pane that was READ and showed nothing is a measurement, and it must
    not collapse into the null that means "not measured". This is the one case
    where `pane_preview` is falsy while its status is `ok`, so a consumer
    testing truthiness rather than the status gets it wrong — which is why the
    status is the documented discriminator."""
    assert sm.build_pane_preview("") == {"text": "", "status": "ok"}
    assert sm.build_pane_preview(None) == {"text": "", "status": "ok"}


def test_the_preview_TRUNCATES_and_SAYS_SO_rather_than_going_quietly_short():
    """🔴 The bound needs BOTH halves — what it admits and what it rejects — and
    the fixtures are LITERAL sizes, never `"x" * MAX_PANE_PREVIEW_BYTES`. A
    fixture derived from the constant scales with it, so raising the constant to
    a billion would keep this green while allocating a gigabyte.
    """
    # ADMITS: exactly at the bound is whole, not truncated.
    at = sm.build_pane_preview("y" * 64, max_bytes=64)
    assert at == {"text": "y" * 64, "status": "ok"}
    # REJECTS: one byte over is cut TO the bound and labelled.
    over = sm.build_pane_preview("y" * 65, max_bytes=64)
    assert over["status"] == "truncated"
    assert over["text"] == "y" * 64
    assert len(over["text"].encode()) == 64


def test_truncation_cuts_on_a_CHARACTER_boundary_not_a_byte_offset():
    """🔴 THE CASE THE REAL FLEET IS MADE OF. Every Claude Code pane is box-
    drawing glyphs, which are 3 bytes each in UTF-8 — so a byte-offset slice
    lands mid-sequence on almost every truncation that will ever happen here.

    10 glyphs x 3 bytes = 30 bytes; a cap of 20 falls INSIDE the 7th. The result
    must be 6 whole glyphs (18 bytes), never a partial sequence, a replacement
    character, or a UnicodeDecodeError.
    """
    screen = "─" * 10
    assert len(screen.encode()) == 30           # the fixture is what I think
    out = sm.build_pane_preview(screen, max_bytes=20)
    assert out["status"] == "truncated"
    assert out["text"] == "─" * 6
    assert len(out["text"].encode()) == 18      # under the cap, on a boundary
    assert "�" not in out["text"]          # no replacement char smuggled in


def test_the_per_pane_cap_is_pinned_to_a_LITERAL():
    """🔴 Pin the CONSTANT, not just behaviour derived from it. Measured
    2026-08-28 the largest real pane was 6,616 bytes, so 16 KiB is ~2.4x the
    observed maximum — a guard against a pathological pane, not a routine cut.
    Changing it is a payload-budget decision that should fail here first."""
    assert sm.MAX_PANE_PREVIEW_BYTES == 16384


def test_the_caveat_names_the_FLAG_and_the_scrollback_EXCLUSION():
    """🔴 The caveat is the only thing telling a cold reader why every preview is
    null, and where scrollback went. Pins the two claims a reader acts on, not
    the prose around them."""
    cav = base_gather()["caveats"]["pane_preview"]
    assert cav["opt_in_flag"] == "--pane-preview"
    assert cav["scope"] == "claude_rows_only"
    assert cav["max_bytes_per_pane"] == sm.MAX_PANE_PREVIEW_BYTES
    assert list(cav["statuses"]) == list(sm.PREVIEW_STATUSES)
    assert "scrollback" in cav["note"]
    # the footer renders it, so a text-mode reader sees it too
    line = [ln for ln in sm.render_caveats(base_gather())
            if "caveat[pane_preview]" in ln]
    assert len(line) == 1
    assert "--pane-preview" in line[0] and "scrollback" in line[0]


def test_the_flag_reaches_gather_and_is_OFF_in_its_signature():
    """🔴 The wiring, pinned at the seam. A flag parsed and never passed is the
    shape that ships an inert feature — and `pane_preview` defaulting to True in
    `gather` would make every existing consumer pay the 2.9x silently."""
    import inspect
    assert (inspect.signature(sm.gather).parameters["pane_preview"].default
            is False)
    assert (inspect.signature(sm.fold_windows).parameters["pane_preview"].default
            is False)
    p = sm.build_parser()
    assert p.parse_args(["scan", "--json"]).pane_preview is False
    assert p.parse_args(["scan", "--json", "--pane-preview"]).pane_preview is True
