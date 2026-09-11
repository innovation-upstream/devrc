"""Unit tests for tmux-session-restore PURE logic (no live tmux / claude / grep).

Run:
  nix develop ~/workspace/devrc -c python3 -m pytest \
      scripts/session-analysis/tests/test_tmux_session_restore.py -q

Covers: scratch-slot codename parsing, project-dir encoding, display naming, the
per-pane LEDGER binding and each of its four validations, the ledger-before-grep
ordering (both the "the grep never runs" performance claim and the "a guess cannot
steal a certain id" correctness claim), the claim-based unique-session assignment
(no two windows share a session, uncertain -> picker), cheat-sheet rendering and
its per-entry source badge, the loader's "an unusable module degrades to None"
promise, and `cmd_save`'s two summary lines.
tmux/grep/capture-pane I/O and the ledger directory are stubbed; nothing here reads
or writes the real `~/.cache/agent-ledger`, `~/.claude/projects` or
`~/.config/initiatives/restore-plan.json`.
"""
import importlib.util
import json
import os
import re
import sys
import types
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
SCRIPT = HERE.parent.parent / "tmux-session-restore.py"
_spec = importlib.util.spec_from_file_location("tmux_session_restore", SCRIPT)
tsr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tsr)

# Synthetic ids — this repo is public, so no fixture ever carries a real one.
SID_A = "11111111-2222-4333-8444-555555555555"
SID_B = "66666666-7777-4888-8999-aaaaaaaaaaaa"
SID_C = "bbbbbbbb-cccc-4ddd-8eee-ffffffffffff"
SERVER_PID = "424242"


# --------------------------------------------------------------------------- #
# codenames / naming / encoding
# --------------------------------------------------------------------------- #
def test_codenames_parses_slot_table(tmp_path, monkeypatch):
    slots = tmp_path / "tmux-scratch-slots.sh"
    slots.write_text(
        'SCRATCH_SLOTS=(\n'
        '    "scratch4:V:#83a598:Vapor"\n'
        '    "scratch11:w:#ebdbb2:wheat"\n'
        ')\n')
    monkeypatch.setattr(tsr, "SLOTS_FILE", slots)
    assert tsr.codenames() == {"scratch4": "Vapor", "scratch11": "wheat"}


def test_codenames_missing_file_is_empty(monkeypatch):
    monkeypatch.setattr(tsr, "SLOTS_FILE", Path("/no/such/slots.sh"))
    assert tsr.codenames() == {}


def test_display_session_codename_else_main():
    codes = {"scratch4": "Vapor"}
    assert tsr.display_session("scratch4", codes) == "Vapor"
    assert tsr.display_session("8", codes) == "main:8"


def test_project_dir_encoding(monkeypatch):
    monkeypatch.setattr(tsr, "PROJECTS", Path("/home/u/.claude/projects"))
    assert tsr.project_dir_for("/home/u/workspace/devrc") == \
        Path("/home/u/.claude/projects/-home-u-workspace-devrc")


# --------------------------------------------------------------------------- #
# live_claude_panes — the pane_id the ledger is keyed on must survive parsing
# --------------------------------------------------------------------------- #
def test_live_claude_panes_carries_pane_id_and_drops_non_claude(monkeypatch):
    rows = "\n".join([
        "%7\tscratch4\t2\t/w/repo\tclaude\tfaro work",
        "%8\tscratch4\t3\t/w/repo\tzsh\tjust a shell",
        "%9\t8\t1\t/w/other\tclaude\t",           # empty title still parses
        "%10\t8\t2\t/w/other",                     # short row: ignored
    ])
    monkeypatch.setattr(tsr, "run", lambda cmd: rows)
    panes = tsr.live_claude_panes()
    assert [p["pane_id"] for p in panes] == ["%7", "%9"]
    assert panes[0] == {"pane_id": "%7", "session": "scratch4", "window": "2",
                        "cwd": "/w/repo", "title": "faro work"}
    assert panes[1]["cwd"] == "/w/other" and panes[1]["title"] == ""


# --------------------------------------------------------------------------- #
# ledger_binding — the deterministic per-pane record and its four validations
# --------------------------------------------------------------------------- #
CWD = "/w/repo"
OTHER_CWD = "/w/other-repo"


def _ledger_world(tmp_path, monkeypatch, *, sid=SID_A, cwd=CWD,
                  transcript_cwd=None, write_transcript=True,
                  tmux_pid=SERVER_PID, pane="%7", record=True):
    """A throwaway projects tree + ledger dir; returns (ledger_dir, transcript).

    Every knob here is one of the validations, so a test flips exactly one field
    and leaves the others in their valid state — that is what makes each guard
    reachable by a case no EARLIER guard rejects.
    """
    projects = tmp_path / "projects"
    monkeypatch.setattr(tsr, "PROJECTS", projects)
    tdir = projects / (transcript_cwd or cwd).replace("/", "-")
    tdir.mkdir(parents=True, exist_ok=True)
    transcript = tdir / f"{sid or 'none'}.jsonl"
    if write_transcript:
        transcript.write_text("")
    ledger = tmp_path / "agent-ledger"
    ledger.mkdir(exist_ok=True)
    if record:
        (ledger / tsr._AL.pane_filename("claude", pane)).write_text(json.dumps({
            "schema": 1, "runtime": "claude", "session_id": sid,
            "last_activity_ts": "2026-09-04T23:47:01Z", "pane_id": pane,
            "window_id": "@61", "tmux_pid": tmux_pid,
            "transcript_path": str(transcript),
        }) + "\n")
    monkeypatch.setattr(tsr, "LEDGER_DIR", ledger)
    return ledger, transcript


def test_ledger_binding_accepts_a_valid_record(tmp_path, monkeypatch):
    _ledger_world(tmp_path, monkeypatch)
    assert tsr.ledger_binding("%7", CWD, SERVER_PID) == (SID_A, "ok")


def test_ledger_binding_rejects_a_missing_record_file(tmp_path, monkeypatch):
    _ledger_world(tmp_path, monkeypatch, record=False)
    assert tsr.ledger_binding("%7", CWD, SERVER_PID) == ("", "no-record")


def test_ledger_binding_rejects_an_unparseable_record(tmp_path, monkeypatch):
    ledger, _ = _ledger_world(tmp_path, monkeypatch)
    (ledger / tsr._AL.pane_filename("claude", "%7")).write_text("{not json\n")
    assert tsr.ledger_binding("%7", CWD, SERVER_PID) == ("", "no-record")


def test_ledger_binding_rejects_an_empty_session_id(tmp_path, monkeypatch):
    # Everything else is valid, so this guard is the ONLY thing that can reject.
    _ledger_world(tmp_path, monkeypatch, sid="")
    assert tsr.ledger_binding("%7", CWD, SERVER_PID) == ("", "no-session-id")


def test_ledger_binding_rejects_an_absent_transcript(tmp_path, monkeypatch):
    # The path is spelled correctly (right project dir) — only the FILE is gone,
    # so deleting this guard would make the record read `ok`, not another reason.
    _ledger_world(tmp_path, monkeypatch, write_transcript=False)
    assert tsr.ledger_binding("%7", CWD, SERVER_PID) == ("", "transcript-missing")


def test_ledger_binding_rejects_a_different_tmux_generation(tmp_path, monkeypatch):
    _ledger_world(tmp_path, monkeypatch, tmux_pid="999999")
    assert tsr.ledger_binding("%7", CWD, SERVER_PID) == ("", "generation-mismatch")


def test_ledger_binding_rejects_an_unmeasured_generation(tmp_path, monkeypatch):
    """Unable to check a generation is NOT the same as having checked it."""
    _ledger_world(tmp_path, monkeypatch)
    assert tsr.ledger_binding("%7", CWD, "") == ("", "generation-unmeasured")
    _ledger_world(tmp_path, monkeypatch, tmux_pid="")
    assert tsr.ledger_binding("%7", CWD, SERVER_PID) == ("", "generation-unmeasured")


def test_ledger_binding_rejects_a_transcript_from_another_repo(tmp_path, monkeypatch):
    """🔴 The cross-repo mis-bind guard: right pane, right generation, WRONG repo.

    The record is valid in every other respect — a live session id, a transcript
    that exists on disk, this server's pid — so nothing upstream rejects it. Only
    the encoded-cwd comparison stands between this pane and resuming another
    repo's conversation in a window that looks correct.
    """
    _ledger_world(tmp_path, monkeypatch, transcript_cwd=OTHER_CWD)
    assert tsr.ledger_binding("%7", CWD, SERVER_PID) == ("", "project-mismatch")


def test_ledger_binding_needs_a_pane_id(tmp_path, monkeypatch):
    _ledger_world(tmp_path, monkeypatch)
    assert tsr.ledger_binding("", CWD, SERVER_PID) == ("", "no-pane-id")


def test_ledger_binding_rejects_a_record_that_is_not_an_object(tmp_path, monkeypatch):
    """A JSON ARRAY parses fine and has no `.get` — only `isinstance` stops it.

    Reachable past every earlier check: the file exists and line 1 is well-formed
    JSON, so `no-record`'s parse arm cannot fire. Without the type guard this is
    an `AttributeError` propagating out of `cmd_save`, not a fallback to the grep.
    """
    _ledger, transcript = _ledger_world(tmp_path, monkeypatch)
    (_ledger / tsr._AL.pane_filename("claude", "%7")).write_text(json.dumps(
        [{"schema": 1, "runtime": "claude", "session_id": SID_A,
          "tmux_pid": SERVER_PID, "transcript_path": str(transcript)}]) + "\n")
    try:
        got = tsr.ledger_binding("%7", CWD, SERVER_PID)
    except Exception as exc:      # noqa: BLE001 — this IS the failure under test
        pytest.fail(f"a non-object ledger record raised {exc!r} instead of "
                    f"returning ('', 'no-record') — the isinstance guard is gone")
    assert got == ("", "no-record")


# --------------------------------------------------------------------------- #
# the loader's "no fallback spelling" promise — including a module that IMPORTS
# --------------------------------------------------------------------------- #
def _stub_ledger_module(tmp_path, body: str) -> Path:
    p = tmp_path / "stub_agent_ledger.py"
    p.write_text(body)
    return p


def test_a_ledger_module_without_pane_filename_loads_as_none(tmp_path):
    """`None` is the promise, not "imported and half-usable".

    This reader borrows exactly one symbol. A module that imports cleanly while
    lacking it is the SAME case as an absent file, and the loader must say so —
    otherwise the `_AL is None` arm in `ledger_binding` never runs.
    """
    mod = _stub_ledger_module(tmp_path, "LEDGER_DIR = '/nonexistent'\n")
    assert tsr._load_agent_ledger(mod) is None, (
        "a ledger module lacking `pane_filename` was returned as usable — "
        "`ledger_binding` will then raise AttributeError out of `cmd_save`")


_USABLE_STUB = ("LEDGER_DIR = '/nonexistent-ledger'\n"
                "def pane_filename(runtime, pane_id):\n"
                "    return 'stub-%s-%s.json' % (runtime, pane_id)\n")


def test_a_usable_ledger_module_still_loads(tmp_path):
    """The positive control: the guard must not reject a WORKING module.

    Carries EVERY name in `_BORROWED`, so it is the case that proves the loader
    still says yes — without it, a guard that rejected everything would look as
    green as a correct one.
    """
    mod = _stub_ledger_module(tmp_path, _USABLE_STUB)
    loaded = tsr._load_agent_ledger(mod)
    assert loaded is not None, "the guard rejected a module that HAS every symbol"
    assert loaded.pane_filename("claude", "%7") == "stub-claude-%7.json"


def test_a_ledger_module_without_ledger_dir_loads_as_none(tmp_path):
    """The MIRROR of the `pane_filename` case — `LEDGER_DIR` is borrowed too.

    `pane_filename` is not "the one symbol this file borrows": the module-level
    `LEDGER_DIR` comes from the same module, and a module carrying one name but
    not the other is exactly as unusable as a module carrying neither.
    """
    mod = _stub_ledger_module(
        tmp_path, "def pane_filename(runtime, pane_id):\n"
                  "    return 'stub-%s-%s.json' % (runtime, pane_id)\n")
    assert tsr._load_agent_ledger(mod) is None, (
        "a ledger module lacking `LEDGER_DIR` was returned as usable — this "
        "reader then invents a directory the writer does not write to")


def test_a_missing_ledger_dir_reports_the_deploy_token_not_no_record(tmp_path,
                                                                     monkeypatch):
    """🔴 THE POINT OF THE GUARD: a deploy break must not report as the benign case.

    `cmd_save`'s legend reads `no-ledger-module` as a deploy problem and
    `no-record` as "nothing to fix". If the reader accepted a module whose
    directory contract it could not borrow, it would look in a directory of its
    own invention, EVERY pane would answer `no-record`, and the tally would
    announce a broken deploy with the one token that tells the operator to ignore
    it. `LEDGER_DIR` is pointed at an empty scratch dir so the assertion measures
    the guard rather than the state of the real ledger.
    """
    monkeypatch.setattr(
        tsr, "_AL",
        tsr._load_agent_ledger(_stub_ledger_module(
            tmp_path, "def pane_filename(runtime, pane_id):\n"
                      "    return 'stub-%s-%s.json' % (runtime, pane_id)\n")))
    empty = tmp_path / "empty-ledger"
    empty.mkdir()
    monkeypatch.setattr(tsr, "LEDGER_DIR", empty)
    got = tsr.ledger_binding("%7", CWD, SERVER_PID)
    assert got == ("", "no-ledger-module"), (
        f"a ledger module missing `LEDGER_DIR` reported {got[1]!r}; "
        f"`no-record` is the token the legend calls 'nothing to fix', so a "
        f"deploy break would be reported as the benign case")


def test_borrowed_lists_every_symbol_this_file_reads_off_the_module():
    """Two-way pin: `_BORROWED` == the attributes actually read off `_AL`.

    An INVARIANT guard, not regression coverage — it fails when the set GROWS (a
    third borrowed symbol added without listing it, which re-opens the exact hole
    `LEDGER_DIR` was) or SHRINKS (a name listed that nothing reads). The check the
    loader performs is only as wide as this tuple, so the tuple has to be checked
    against the source rather than trusted.

    🔴 READ THE AST, NOT THE TEXT — and the reason is this guard's own history.
    The first version matched `\\b_AL\\.(\\w+)`, which sees `_AL.LEDGER_DIR` but is
    BLIND to `getattr(_AL, "LEDGER_DIR", <default>)`. That is not a hypothetical
    spelling: it is exactly how `LEDGER_DIR` was written at `2eb06a6d`, i.e. the
    guard could not see the very hole it was introduced to close, while its
    docstring claimed it could. Measured: a mutant borrowing a third symbol via
    `getattr` SURVIVED a fully green run. Both spellings are collected here, so
    the tuple is pinned against what the module actually reads. A comment merely
    MENTIONING `_AL.SOMETHING` is also no longer counted — the text scan flagged
    prose as a borrow.
    """
    import ast

    read: set[str] = set()
    for node in ast.walk(ast.parse(SCRIPT.read_text())):
        # `_AL.NAME`
        if (isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name) and node.value.id == "_AL"):
            read.add(node.attr)
        # `getattr(_AL, "NAME"[, default])`
        elif (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name) and node.func.id == "getattr"
                and len(node.args) >= 2
                and isinstance(node.args[0], ast.Name) and node.args[0].id == "_AL"
                and isinstance(node.args[1], ast.Constant)
                and isinstance(node.args[1].value, str)):
            read.add(node.args[1].value)
    assert read == set(tsr._BORROWED), (
        f"`_BORROWED` is {sorted(tsr._BORROWED)} but this file reads "
        f"{sorted(read)} off the ledger module — the loader guards only what "
        f"`_BORROWED` names, so anything missing here loads unguarded")


def test_a_ledger_module_missing_pane_filename_degrades_not_raises(tmp_path,
                                                                   monkeypatch):
    """🔴 `cmd_save` runs unattended every ~15 min from `tmux-post-save.sh` into a
    log nobody reads. A traceback here freezes the restore plan silently, which is
    the exact "snapshot nothing refreshes" failure this tool has already had
    twice — so an unusable module must produce `no-ledger-module`, never an
    exception.
    """
    monkeypatch.setattr(
        tsr, "_AL",
        tsr._load_agent_ledger(_stub_ledger_module(tmp_path, "SCHEMA = 1\n")))
    try:
        got = tsr.ledger_binding("%7", CWD, SERVER_PID)
    except Exception as exc:      # noqa: BLE001 — this IS the failure under test
        pytest.fail(f"ledger_binding raised {exc!r} for a ledger module missing "
                    f"`pane_filename`; it must degrade to ('', 'no-ledger-module')")
    assert got == ("", "no-ledger-module")


# --------------------------------------------------------------------------- #
# build_plan — claim-based unique assignment
# --------------------------------------------------------------------------- #
def _panes():
    return [
        {"session": "8", "window": "1", "cwd": "/r", "title": "wedge"},
        {"session": "8", "window": "3", "cwd": "/r", "title": "buffer"},
        {"session": "scratch4", "window": "2", "cwd": "/r", "title": "faro"},
    ]


def _no_grep(target, cwd):
    raise AssertionError(
        "unique_match_sids was called for a ledger-bound pane — the whole point "
        "of the ledger is that the 145-transcript grep never runs for it")


def _plan_env(monkeypatch, panes, matcher, server_pid=SERVER_PID):
    monkeypatch.setattr(tsr, "live_claude_panes", lambda: panes)
    monkeypatch.setattr(tsr, "codenames", lambda: {"scratch4": "Vapor"})
    monkeypatch.setattr(tsr, "first_user_line", lambda sid, cwd: "")
    monkeypatch.setattr(tsr, "tmux_server_pid", lambda: server_pid)
    monkeypatch.setattr(tsr, "unique_match_sids", matcher)


def test_build_plan_binds_from_the_ledger_without_ever_grepping(tmp_path, monkeypatch):
    """The performance claim, pinned behaviourally rather than asserted in prose."""
    _ledger_world(tmp_path, monkeypatch)
    _plan_env(monkeypatch,
              [{"pane_id": "%7", "session": "scratch4", "window": "2",
                "cwd": CWD, "title": "faro"}],
              _no_grep)
    plan = tsr.build_plan()
    assert plan[0]["session_id"] == SID_A
    assert plan[0]["bind_source"] == "ledger"


@pytest.mark.parametrize("kw", [
    {"record": False},                   # no record file
    {"sid": ""},                         # empty session_id
    {"write_transcript": False},         # transcript gone
    {"tmux_pid": "999999"},              # different tmux server generation
    {"transcript_cwd": OTHER_CWD},       # another repo's project dir
])
def test_each_rejected_record_falls_back_to_the_grep(tmp_path, monkeypatch, kw):
    """Every validation failure independently hands the pane to the fallback."""
    _ledger_world(tmp_path, monkeypatch, **kw)
    _plan_env(monkeypatch,
              [{"pane_id": "%7", "session": "scratch4", "window": "2",
                "cwd": CWD, "title": "faro"}],
              lambda target, cwd: [SID_B])
    plan = tsr.build_plan()
    assert plan[0]["session_id"] == SID_B
    assert plan[0]["bind_source"] == "fuzzy"


def test_ledger_wins_a_conflict_with_the_grep(tmp_path, monkeypatch):
    """Same pane, two answers: the RECORD beats the INFERENCE (which never runs)."""
    _ledger_world(tmp_path, monkeypatch)
    _plan_env(monkeypatch,
              [{"pane_id": "%7", "session": "scratch4", "window": "2",
                "cwd": CWD, "title": "faro"}],
              lambda target, cwd: [SID_B])   # the grep would say something else
    plan = tsr.build_plan()
    assert plan[0]["session_id"] == SID_A and plan[0]["bind_source"] == "ledger"


def test_a_guess_cannot_steal_a_session_the_ledger_owns(tmp_path, monkeypatch):
    """The mixed case: ledger pane and fuzzy pane both want SID_A.

    Pane %7 has a record for SID_A. Pane %8 has none, and its top content match is
    also SID_A. Ledger claims first, so %8 falls to its second candidate rather
    than resuming %7's conversation.
    """
    _ledger_world(tmp_path, monkeypatch)
    _plan_env(monkeypatch,
              [{"pane_id": "%8", "session": "8", "window": "1",
                "cwd": CWD, "title": "wedge"},         # listed FIRST on purpose
               {"pane_id": "%7", "session": "scratch4", "window": "2",
                "cwd": CWD, "title": "faro"}],
              lambda target, cwd: [SID_A, SID_C])
    plan = tsr.build_plan()
    by_win = {(e["codename"], e["window"]): e for e in plan}
    assert by_win[("Vapor", "2")]["session_id"] == SID_A
    assert by_win[("Vapor", "2")]["bind_source"] == "ledger"
    assert by_win[("main:8", "1")]["session_id"] == SID_C
    assert by_win[("main:8", "1")]["bind_source"] == "fuzzy"
    sids = [e["session_id"] for e in plan]
    assert len(sids) == len(set(sids))    # never the same conversation twice


def test_two_panes_cannot_share_one_ledger_session(tmp_path, monkeypatch):
    """Two ledger records naming one session: one binds, the other falls back."""
    ledger, transcript = _ledger_world(tmp_path, monkeypatch)
    (ledger / tsr._AL.pane_filename("claude", "%8")).write_text(
        (ledger / tsr._AL.pane_filename("claude", "%7")).read_text())   # same session_id, other pane
    _plan_env(monkeypatch,
              [{"pane_id": "%7", "session": "scratch4", "window": "2",
                "cwd": CWD, "title": "faro"},
               {"pane_id": "%8", "session": "8", "window": "1",
                "cwd": CWD, "title": "wedge"}],
              lambda target, cwd: [])
    plan = tsr.build_plan()
    sids = [e["session_id"] for e in plan if e["session_id"]]
    assert sids == [SID_A]
    assert [e["session_id"] for e in plan if not e["session_id"]] == [""]


def test_neither_source_yields_the_picker_not_a_guess(tmp_path, monkeypatch):
    _ledger_world(tmp_path, monkeypatch, record=False)
    _plan_env(monkeypatch,
              [{"pane_id": "%7", "session": "scratch4", "window": "2",
                "cwd": CWD, "title": "faro"}],
              lambda target, cwd: [])
    plan = tsr.build_plan()
    assert plan[0]["session_id"] == "" and plan[0]["bind_source"] == ""


def test_build_plan_records_the_ledger_reason_for_every_pane(tmp_path, monkeypatch):
    """The reason tokens must LEAVE `build_plan`.

    They justify themselves as the distinction between `no-ledger-module` (a
    deploy problem), `generation-mismatch` (the server restarted) and `no-record`
    (nothing to fix) — and `cmd_save` can only print what the plan carries. Two
    panes with DIFFERENT outcomes, so a constant would not satisfy this.
    """
    _ledger_world(tmp_path, monkeypatch)          # a valid record for %7 only
    _plan_env(monkeypatch,
              [{"pane_id": "%7", "session": "scratch4", "window": "2",
                "cwd": CWD, "title": "faro"},
               {"pane_id": "%9", "session": "8", "window": "1",
                "cwd": CWD, "title": "wedge"}],
              lambda target, cwd: [])
    by_win = {e["window"]: e for e in tsr.build_plan()}
    assert by_win["2"].get("ledger_reason") == "ok"
    assert by_win["1"].get("ledger_reason") == "no-record"


def test_build_plan_assigns_unique_sessions(monkeypatch):
    monkeypatch.setattr(tsr, "live_claude_panes", _panes)
    monkeypatch.setattr(tsr, "codenames", lambda: {"scratch4": "Vapor"})
    monkeypatch.setattr(tsr, "first_user_line", lambda sid, cwd: "")
    monkeypatch.setattr(tsr, "tmux_server_pid", lambda: SERVER_PID)
    # Each pane content-matches its own distinct session.
    cand = {"8:1": ["sidA"], "8:3": ["sidB"], "scratch4:2": ["sidC"]}
    monkeypatch.setattr(tsr, "unique_match_sids", lambda target, cwd: cand[target])

    plan = tsr.build_plan()
    by_loc = {(e["codename"], e["window"]): e["session_id"] for e in plan}
    assert by_loc[("main:8", "1")] == "sidA"
    assert by_loc[("main:8", "3")] == "sidB"
    assert by_loc[("Vapor", "2")] == "sidC"
    assert len({e["session_id"] for e in plan}) == 3  # all distinct


def test_build_plan_never_double_assigns_a_session(monkeypatch):
    # Two panes whose top candidate is the SAME session -> only one claims it, the
    # other falls through (empty -> picker), never a duplicate.
    monkeypatch.setattr(tsr, "live_claude_panes", _panes)
    monkeypatch.setattr(tsr, "codenames", lambda: {"scratch4": "Vapor"})
    monkeypatch.setattr(tsr, "first_user_line", lambda sid, cwd: "")
    monkeypatch.setattr(tsr, "tmux_server_pid", lambda: SERVER_PID)
    cand = {"8:1": ["dup"], "8:3": ["dup"], "scratch4:2": ["dup", "own"]}
    monkeypatch.setattr(tsr, "unique_match_sids", lambda target, cwd: cand[target])

    plan = tsr.build_plan()
    sids = [e["session_id"] for e in plan if e["session_id"]]
    assert len(sids) == len(set(sids))          # no duplicates
    assert "dup" in sids and "own" in sids       # 2nd candidate used when 1st claimed
    empties = [e for e in plan if not e["session_id"]]
    assert len(empties) == 1                      # the one with no free candidate


def test_build_plan_empty_when_no_match(monkeypatch):
    monkeypatch.setattr(tsr, "live_claude_panes",
                        lambda: [{"session": "8", "window": "1", "cwd": "/r", "title": "x"}])
    monkeypatch.setattr(tsr, "codenames", lambda: {})
    monkeypatch.setattr(tsr, "first_user_line", lambda sid, cwd: "")
    monkeypatch.setattr(tsr, "tmux_server_pid", lambda: SERVER_PID)
    monkeypatch.setattr(tsr, "unique_match_sids", lambda target, cwd: [])
    plan = tsr.build_plan()
    assert plan[0]["session_id"] == ""            # uncertain -> picker at restore


# --------------------------------------------------------------------------- #
# cheat-sheet rendering
# --------------------------------------------------------------------------- #
def test_cheat_sheet_shows_resume_command_and_picker_fallback():
    plan = [
        {"codename": "Vapor", "window": "2", "cwd": "/r", "session_id": "abc",
         "title": "faro work", "hint": "continue faro"},
        {"codename": "main:8", "window": "1", "cwd": "/r", "session_id": "",
         "title": "unknown", "hint": ""},
    ]
    txt = tsr.cheat_sheet(plan)
    assert "claude --resume abc" in txt
    assert "Vapor:2" in txt and "main:8:1" in txt
    assert "pick from the list" in txt           # empty id -> picker guidance


def test_cheat_sheet_labels_each_binding_with_its_own_source():
    """The badge must follow the entry, not a single default for the whole sheet."""
    txt = tsr.cheat_sheet([
        {"codename": "Vapor", "window": "2", "cwd": "/r", "session_id": SID_A,
         "bind_source": "ledger", "title": "t", "hint": ""},
        {"codename": "main:8", "window": "1", "cwd": "/r", "session_id": SID_B,
         "bind_source": "fuzzy", "title": "u", "hint": ""},
    ])
    assert f"claude --resume {SID_A}`  (ledger)" in txt
    assert f"claude --resume {SID_B}`  (fuzzy)" in txt


def test_cheat_sheet_labels_an_unrecorded_source_as_a_guess():
    """🔴 A plan with NO `bind_source` is a PRE-LEDGER plan — every id in one came
    from the pane-content grep, so the default has to be `fuzzy`.

    Labelling it `(ledger)` would stamp this PR's certainty badge on exactly the
    guesses the PR exists to distinguish, and the sheet is what an operator
    eyeballs before letting `restore` resume 40 conversations. No field in the
    fixture spells either label, so the rendered word can only come from the
    default.
    """
    txt = tsr.cheat_sheet([{"codename": "Vapor", "window": "2", "cwd": "/r",
                            "session_id": SID_A, "title": "t", "hint": ""}])
    assert f"claude --resume {SID_A}`  (fuzzy)" in txt, (
        "an entry with no recorded bind_source did not render as a guess")
    assert "(ledger)" not in txt, (
        "an entry with no recorded bind_source rendered as `(ledger)` — a guess "
        "must never inherit the ledger's certainty")


# --------------------------------------------------------------------------- #
# cmd_save — the operator-facing summary
# --------------------------------------------------------------------------- #
def test_cmd_save_summary_counts_sources_and_ledger_reasons(tmp_path, monkeypatch,
                                                            capsys):
    """Both summary lines, on a plan whose three source counts are DISTINCT.

    🔴 Distinctness is the point: at 1/1/1 a summary that counted `fuzzy` as
    `ledger` would print exactly the correct line and survive. The reason tally is
    what makes `0 ledger` actionable — `no-ledger-module` and
    `generation-mismatch` are operator problems, `no-record` is not.

    🔴 Every path is under `tmp_path`; this never touches the real
    `~/.config/initiatives/restore-plan.json`.
    """
    state = tmp_path / "state"
    monkeypatch.setattr(tsr, "STATE_DIR", state)
    monkeypatch.setattr(tsr, "PLAN", state / "restore-plan.json")
    monkeypatch.setattr(tsr, "CHEAT", state / "restore-cheatsheet.md")

    def entry(win, sid, source, reason):
        return {"session": "8", "window": str(win), "codename": "main:8",
                "cwd": "/r", "session_id": sid, "bind_source": source,
                "ledger_reason": reason, "title": f"w{win}", "hint": ""}

    monkeypatch.setattr(tsr, "build_plan", lambda: [
        entry(1, SID_A, "ledger", "ok"),
        entry(2, SID_B, "ledger", "ok"),
        entry(3, SID_C, "fuzzy", "no-record"),
        entry(4, "", "", "no-record"),
        entry(5, "", "", "generation-mismatch"),
        entry(6, "", "", "project-mismatch"),
    ])
    rc = tsr.cmd_save()
    out = capsys.readouterr().out
    assert rc == 0
    assert "bound: 2 ledger, 1 pane-content, 3 unbound" in out, (
        "the per-source counts do not match the plan — a source is being "
        "counted under the wrong label")
    assert ("ledger reasons: generation-mismatch=1, no-record=2, ok=2, "
            "project-mismatch=1") in out, (
        "the reason tally is missing or reshaped — `0 ledger` is then "
        "indistinguishable between a missing module, a restarted server and "
        "simply no records")
    assert (state / "restore-plan.json").exists()


# --------------------------------------------------------------------------- #
# restore — custom plan path + no-clobber guard
# --------------------------------------------------------------------------- #
def test_cmd_restore_reads_custom_plan_and_renders_send(tmp_path, monkeypatch, capsys):
    plan = tmp_path / "p.json"
    plan.write_text(json.dumps([{"session": "s", "window": "1", "codename": "Vapor",
                                 "cwd": "/r", "session_id": "abc", "title": "t", "hint": ""}]))
    monkeypatch.setattr(tsr, "tmux_session_exists", lambda n: True)
    monkeypatch.setattr(tsr, "window_state", lambda t: (True, "zsh"))  # bare shell
    rc = tsr.cmd_restore(dry_run=True, plan_path=plan)
    out = capsys.readouterr().out
    assert rc == 0
    assert "claude --resume abc" in out and "would send" in out


def test_cmd_restore_skips_window_already_running_claude(tmp_path, monkeypatch, capsys):
    plan = tmp_path / "p.json"
    plan.write_text(json.dumps([{"session": "s", "window": "1", "codename": "Vapor",
                                 "cwd": "/r", "session_id": "abc", "title": "t", "hint": ""}]))
    monkeypatch.setattr(tsr, "tmux_session_exists", lambda n: True)
    monkeypatch.setattr(tsr, "window_state", lambda t: (True, "claude"))  # already running
    tsr.cmd_restore(dry_run=True, plan_path=plan)
    assert "claude already running" in capsys.readouterr().out


# --------------------------------------------------------------------------- #
# staleness check
# --------------------------------------------------------------------------- #
def test_plan_staleness_returns_none_when_no_plan(tmp_path, monkeypatch):
    monkeypatch.setattr(tsr, "PLAN", tmp_path / "missing.json")
    assert tsr.plan_staleness_hours() is None


def test_plan_staleness_falls_back_to_wall_clock_without_a_state_file(tmp_path, monkeypatch):
    """Migrated from `plan_age_hours`, which measured wall clock unconditionally.

    Wall clock is now the FALLBACK, reached only when the resurrect state file
    cannot be read — so this fixture must point `RESURRECT_LAST` at nothing.
    Leaving it unset would read the operator's real `~/.tmux/resurrect/last`
    and make the result depend on their live workspace.
    """
    import time
    plan = tmp_path / "restore-plan.json"
    plan.write_text("[]")
    old = time.time() - 3 * 3600
    os.utime(plan, (old, old))
    monkeypatch.setattr(tsr, "PLAN", plan)
    monkeypatch.setattr(tsr, "RESURRECT_LAST", tmp_path / "no-such-state")
    gap, basis = tsr.plan_staleness_hours()
    assert basis == "wall"
    assert 2.9 < gap < 3.1


def test_cmd_restore_rejects_stale_plan(tmp_path, monkeypatch, capsys):
    import time
    plan = tmp_path / "restore-plan.json"
    plan.write_text(json.dumps([{"session": "s", "window": "1", "codename": "Vapor",
                                 "cwd": "/r", "session_id": "abc", "title": "t", "hint": ""}]))
    # Set mtime to 5 hours ago
    old = time.time() - 5 * 3600
    os.utime(plan, (old, old))
    monkeypatch.setattr(tsr, "PLAN", plan)
    # 🔴 Hermeticity: without this the staleness measure reads the operator's
    # real `~/.tmux/resurrect/last`, so the verdict would depend on their live
    # workspace. Pointing it at nothing selects the wall-clock fallback, which
    # is what this test's 5h-old plan is written against.
    monkeypatch.setattr(tsr, "RESURRECT_LAST", tmp_path / "no-such-state")
    monkeypatch.setattr(tsr, "tmux_session_exists", lambda n: True)
    monkeypatch.setattr(tsr, "window_state", lambda t: (True, "zsh"))
    # Default staleness limit is 2h — should reject (no custom plan_path)
    rc = tsr.cmd_restore(dry_run=False, plan_path=None, staleness_hours=2.0)
    assert rc == 1
    assert "stale" in capsys.readouterr().err.lower()


def test_cmd_restore_accepts_fresh_plan(tmp_path, monkeypatch, capsys):
    plan = tmp_path / "p.json"
    plan.write_text(json.dumps([{"session": "s", "window": "1", "codename": "Vapor",
                                 "cwd": "/r", "session_id": "abc", "title": "t", "hint": ""}]))
    # Fresh plan (just created)
    monkeypatch.setattr(tsr, "tmux_session_exists", lambda n: True)
    monkeypatch.setattr(tsr, "window_state", lambda t: (True, "zsh"))
    rc = tsr.cmd_restore(dry_run=True, plan_path=plan, staleness_hours=2.0)
    assert rc == 0
    assert "would send" in capsys.readouterr().out


def test_cmd_restore_staleness_check_skips_when_no_plan(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(tsr, "PLAN", tmp_path / "missing.json")
    rc = tsr.cmd_restore(dry_run=False, staleness_hours=2.0)
    assert rc == 1
    assert "no restore plan" in capsys.readouterr().err.lower()


def test_staleness_check_cli_parsing(monkeypatch, capsys, tmp_path):
    # 🔴 Hermeticity: without this the staleness measure reads the operator's
    # real `~/.tmux/resurrect/last`, so this test goes RED whenever production
    # is broken — measured: a state file >2h old fails it. That is the same
    # leak fixed in `test_cmd_restore_rejects_stale_plan`, one test below it.
    monkeypatch.setattr(tsr, "RESURRECT_LAST", tmp_path / "no-such-state")
    monkeypatch.setattr(tsr, "resurrect_last_path", lambda: tmp_path / "no-such-state")
    """--staleness-check without a number uses the 2h default."""
    import tempfile
    # Parse argv, not actually restoring — just verify the flag is consumed
    argv = ["restore", "--staleness-check"]
    # We need a plan to get past the initial check; use a fake one
    fd, path = tempfile.mkstemp(suffix=".json")
    try:
        with open(fd, "w") as f:
            json.dump([], f)
        # Override PLAN to point at our temp file
        monkeypatch.setattr(tsr, "PLAN", Path(path))
        monkeypatch.setattr(tsr, "tmux_session_exists", lambda n: True)
        monkeypatch.setattr(tsr, "window_state", lambda t: (True, "zsh"))
        # Should parse without error and use default 2h limit
        rc = tsr.main(argv)
        # Empty plan → no windows to process, but should not error
        assert rc == 0
    finally:
        os.unlink(path)


# ---------------------------------------------------------------------------
# Staleness is measured against the LAYOUT, not the wall clock.
#
# 🔴 The bug these pin: the wall-clock measure counted POWERED-OFF time against
# the plan, so `restore --staleness-check 2` refused after any shutdown longer
# than the limit — "plan is 8.0h old" for a plan written 0.02h before the
# reboot, with nothing about it changed. Being switched off is the one interval
# in which reality cannot diverge from the plan.
#
# The replacement compares the plan to the resurrect state file it is racing.
# Both are written by one chain (resurrect save -> post-save-all ->
# tmux-post-save.sh -> `save`), so their mtimes sit seconds apart under a
# working autosave, however long the host is then off.
# ---------------------------------------------------------------------------

HOUR = 3600.0


def _staleness_fixture(tmp_path, monkeypatch, *, plan_age_s, state_age_s=None,
                       uptime_h=10_000.0):
    """A plan and (optionally) a resurrect state file at chosen ages."""
    import time
    now = time.time()
    plan = tmp_path / "restore-plan.json"
    plan.write_text("[]")
    os.utime(plan, (now - plan_age_s, now - plan_age_s))
    monkeypatch.setattr(tsr, "PLAN", plan)

    if state_age_s is None:
        target = tmp_path / "no-such-state"
    else:
        target = tmp_path / "tmux_resurrect_stub.txt"
        target.write_text("stub")
        os.utime(target, (now - state_age_s, now - state_age_s))
    monkeypatch.setattr(tsr, "RESURRECT_LAST", target)
    # Route the tmux lookup too — otherwise these read the live `@resurrect-dir`.
    monkeypatch.setattr(tsr, "resurrect_last_path", lambda: target)
    # 🔴 Uptime must be injected or every liveness case measures THIS host's
    # uptime (753h here), which makes the boot-time cases untestable.
    monkeypatch.setattr(tsr, "uptime_hours", lambda: uptime_h)
    return plan


def test_a_long_power_off_does_not_make_a_contemporaneous_plan_stale(tmp_path, monkeypatch):
    """THE REGRESSION. Plan and layout saved together, then the host sat off for a day."""
    # uptime 45s: this is a restore just after boot, which is the only moment
    # the systemd unit runs. A 24h-old plan is fresh THEN and not later — the
    # liveness term is what draws that distinction.
    _staleness_fixture(tmp_path, monkeypatch, plan_age_s=24 * HOUR,
                       state_age_s=24 * HOUR + 30,  # written 30s apart, both a day ago
                       uptime_h=45 / 3600)
    gap, basis = tsr.plan_staleness_hours()
    assert gap < 0.05, (
        f"a plan written 30s from its layout measured {gap:.2f}h stale — the "
        "powered-off interval is being counted against it again, which is the "
        "exact bug this measure replaced (restore then exits 1 after any "
        "overnight shutdown)")


def test_a_plan_that_stopped_refreshing_while_the_layout_kept_saving_is_stale(tmp_path, monkeypatch):
    """The REAL 2026-08-05 outage: plan frozen at Jul 5, resurrect running to Jul 29.

    The guard has to keep catching this — it is how that outage was found.
    """
    _staleness_fixture(tmp_path, monkeypatch, plan_age_s=600 * HOUR, state_age_s=1 * HOUR)
    gap, basis = tsr.plan_staleness_hours()
    assert basis == "layout"
    assert gap > 2, (
        f"a plan {gap:.1f}h out of step with the layout was not flagged — this "
        "is the broken-autosave case; restoring it relaunches a stale workspace")


def test_a_layout_older_than_the_plan_is_also_stale(tmp_path, monkeypatch):
    """The mirror image — continuum stopped saving while `save` kept running.

    Pinned separately because an implementation without `abs()` passes the case
    above and silently accepts this one.
    """
    _staleness_fixture(tmp_path, monkeypatch, plan_age_s=1 * HOUR, state_age_s=600 * HOUR)
    gap, basis = tsr.plan_staleness_hours()
    assert basis == "layout"
    assert gap > 2, (
        f"a layout {gap:.1f}h out of step with the plan was not flagged — the "
        "workspace being restored is not the one the plan describes")


def test_without_a_state_file_it_falls_back_to_wall_clock_and_says_so(tmp_path, monkeypatch):
    """A fresh host, or `@resurrect-dir` pointed elsewhere.

    The fallback carries the powered-off flaw by construction, so the BASIS is
    part of the answer — a caller printing a bare number cannot be honest.
    """
    _staleness_fixture(tmp_path, monkeypatch, plan_age_s=5 * HOUR, state_age_s=None)
    gap, basis = tsr.plan_staleness_hours()
    assert basis == "wall", f"expected the wall fallback, got {basis!r}"
    assert 4.9 < gap < 5.1, gap


def test_no_plan_measures_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(tsr, "PLAN", tmp_path / "absent.json")
    assert tsr.plan_staleness_hours() is None


def test_restore_names_the_basis_when_it_refuses(tmp_path, monkeypatch, capsys):
    """A bare "8.0h" means different things per basis; the refusal must say which."""
    _staleness_fixture(tmp_path, monkeypatch, plan_age_s=600 * HOUR, state_age_s=1 * HOUR)
    rc = tsr.cmd_restore(dry_run=True, staleness_hours=2)
    err = capsys.readouterr().err
    assert rc == 1, "a plan out of step with its layout must refuse"
    assert "basis=layout" in err, f"refusal did not name its basis: {err!r}"


def test_restore_runs_on_a_contemporaneous_plan_after_a_long_power_off(tmp_path, monkeypatch, capsys):
    """End of the regression: the same 24h-off plan must now RUN, not exit 1."""
    _staleness_fixture(tmp_path, monkeypatch, plan_age_s=24 * HOUR,
                       state_age_s=24 * HOUR + 30, uptime_h=45 / 3600)
    rc = tsr.cmd_restore(dry_run=True, staleness_hours=2)
    err = capsys.readouterr().err
    assert rc == 0, f"restore refused a contemporaneous plan after a power-off: {err!r}"
    assert "too stale" not in err


# ---------------------------------------------------------------------------
# 🔴 LIVENESS — contemporaneity alone is blind to the chain dying WHOLE.
#
# The plan and the layout have ONE writer, so when it dies they freeze together
# and their gap stays constant forever. A contemporaneity-only gate then calls a
# 1400h-old plan fresh. That is the 2026-08-05 outage shape (continuum's
# interpolation clobbered -> resurrect stopped saving at all), and the
# wall-clock measure this change replaced caught it as a side effect.
# ---------------------------------------------------------------------------

def test_a_totally_frozen_chain_is_stale_even_though_the_two_agree(tmp_path, monkeypatch):
    """🔴 THE REGRESSION THIS SECTION EXISTS FOR.

    Plan and layout 30s apart — perfectly contemporaneous — but both written
    1400h ago on a host that has been up 753h. Contemporaneity says 0.008h.
    Liveness says 753h. The gate must take the worse one.
    """
    _staleness_fixture(tmp_path, monkeypatch, plan_age_s=1400 * HOUR,
                       state_age_s=1400 * HOUR + 30, uptime_h=753.0)
    gap, basis = tsr.plan_staleness_hours()
    assert basis == "liveness", (
        f"a chain frozen for 1400h reported basis={basis!r} — contemporaneity "
        "cannot see a TOTAL freeze, so a liveness term must dominate here")
    assert gap > 2, (
        f"a 1400h-dead chain measured {gap:.3f}h — restore would relaunch a "
        "58-day-old plan across every window")


def test_liveness_is_capped_by_uptime_so_a_power_off_still_does_not_count(tmp_path, monkeypatch):
    """The liveness term must not reintroduce the bug this PR fixes.

    Same 24h-off plan, but 45s after boot: the newest artefact is 24h old by
    wall clock and the cap must reduce it to the uptime.
    """
    _staleness_fixture(tmp_path, monkeypatch, plan_age_s=24 * HOUR + 30,
                       state_age_s=24 * HOUR, uptime_h=45 / 3600)
    gap, _ = tsr.plan_staleness_hours()
    assert gap < 0.1, (
        f"measured {gap:.3f}h 45s after boot — the uptime cap is not being "
        "applied, so powered-off time is counted again")


def test_an_unreadable_uptime_fails_TOWARDS_refusing(monkeypatch):
    """A cap that cannot be read must not silently switch the liveness check off.

    +inf means the cap never binds, so the raw wall figure stands — which can
    only refuse MORE, never less. The opposite default (0) would disable the
    guard exactly when the system is in an unexpected state.
    """
    monkeypatch.setattr("builtins.open", _raise_oserror)
    assert tsr.uptime_hours() == float("inf")


def _raise_oserror(*a, **k):
    raise OSError("simulated")


def test_the_wall_basis_refusal_does_not_claim_a_layout_comparison(tmp_path, monkeypatch, capsys):
    """A surviving mutant: the refusal prose was hardcoded to the layout wording.

    On the wall fallback there IS no layout comparison, so saying "out of step
    with the saved layout" would assert a check that never ran.
    """
    _staleness_fixture(tmp_path, monkeypatch, plan_age_s=9 * HOUR, state_age_s=None)
    rc = tsr.cmd_restore(dry_run=True, staleness_hours=2)
    err = capsys.readouterr().err
    assert rc == 1
    assert "basis=wall" in err, err
    assert "out of step with the saved layout" not in err, (
        f"the wall-basis refusal claimed a layout comparison it never made: {err!r}")


def test_state_mtime_follows_the_symlink_rather_than_reading_the_link_itself(tmp_path, monkeypatch):
    """A surviving mutant: `stat()` -> `lstat()`.

    Every other fixture uses a regular file, so nothing held the code to
    following `last`. The target's mtime is when the LAYOUT was captured; the
    link's own is when it was repointed.
    """
    import time
    target = tmp_path / "tmux_resurrect_real.txt"
    target.write_text("layout")
    old = time.time() - 500 * HOUR
    os.utime(target, (old, old))
    link = tmp_path / "last"
    link.symlink_to(target)
    monkeypatch.setattr(tsr, "RESURRECT_LAST", link)
    monkeypatch.setattr(tsr, "resurrect_last_path", lambda: link)
    got = tsr.resurrect_state_mtime()
    assert got is not None and abs(got - old) < 5, (
        "resurrect_state_mtime read the SYMLINK's mtime, not the layout's — "
        "`lstat` would report a fresh layout for a 500h-old capture")


def test_a_dangling_last_degrades_to_the_wall_basis(tmp_path, monkeypatch):
    """The case where stat-vs-lstat decides correctness.

    `lstat` on a dangling link succeeds, and would report `basis=layout` for a
    layout that no longer exists. `stat` raises, and we fall back honestly.
    """
    link = tmp_path / "last"
    link.symlink_to(tmp_path / "gone.txt")
    monkeypatch.setattr(tsr, "RESURRECT_LAST", link)
    monkeypatch.setattr(tsr, "resurrect_last_path", lambda: link)
    assert tsr.resurrect_state_mtime() is None


def test_an_unreadable_state_file_degrades_rather_than_raising(tmp_path, monkeypatch):
    """A surviving mutant: `except OSError` -> `except FileNotFoundError`.

    A `last` that exists but cannot be stat'd (permissions, ELOOP) must reach
    the fallback, not escape and kill the systemd unit.
    """
    class _Boom:
        def stat(self):
            raise PermissionError("simulated")
    monkeypatch.setattr(tsr, "resurrect_last_path", lambda: _Boom())
    assert tsr.resurrect_state_mtime() is None


def test_resurrect_dir_is_read_from_tmux_not_hardcoded(tmp_path, monkeypatch):
    """🔴 A moved `@resurrect-dir` must not leave us reading a frozen default.

    Hardcoding it means the old path freezes at the switchover and every later
    run refuses permanently while asserting `basis=layout` about a file nobody
    writes.
    """
    moved = tmp_path / "xdg-resurrect"
    moved.mkdir()
    monkeypatch.setattr(tsr, "run", lambda cmd: str(moved) + "\n")
    assert tsr.resurrect_last_path() == moved / "last"


def test_resurrect_dir_unset_falls_back_to_the_module_default(tmp_path, monkeypatch):
    """Positive control for the test above — an empty option must not win."""
    monkeypatch.setattr(tsr, "run", lambda cmd: "")
    monkeypatch.setattr(tsr, "RESURRECT_LAST", tmp_path / "default-last")
    assert tsr.resurrect_last_path() == tmp_path / "default-last"


# ---------------------------------------------------------------------------
# Round-2 audit: three guards that were mutation-SURVIVABLE, i.e. unpinned.
# ---------------------------------------------------------------------------

def test_a_liveness_refusal_does_not_call_itself_wall_clock(tmp_path, monkeypatch, capsys):
    """🔴 The basis vocabulary grew to three; the message's if/else stayed binary.

    A liveness refusal announced "older than (wall clock)" — the very measure
    this change rejects. It lands in the worst place: a liveness refusal is
    reachable ONLY when the save chain has stopped, so the wording sent the
    operator hunting the powered-off bug instead of the dead chain.
    """
    _staleness_fixture(tmp_path, monkeypatch, plan_age_s=1400 * HOUR,
                       state_age_s=1400 * HOUR + 30, uptime_h=753.0)
    rc = tsr.cmd_restore(dry_run=True, staleness_hours=2)
    err = capsys.readouterr().err
    assert rc == 1
    assert "basis=liveness" in err, err
    assert "wall clock" not in err, (
        f"a liveness refusal described itself as wall clock: {err!r}")
    assert "silent" in err, f"the refusal does not say the chain stopped: {err!r}"


def test_a_backwards_clock_reports_liveness_as_UNMEASURED_not_as_a_number(tmp_path, monkeypatch):
    """🔴 Both obvious answers to clock skew FABRICATE a liveness value.

    `since = 0` claims the chain just wrote (silently disables the guard).
    `since = inf` claims it never did — and is worse, because
    `min(inf, uptime)` collapses to UPTIME: a one-second step back on a
    long-uptime host then REFUSES a healthy chain and calls it "silent for
    753h". Contemporaneity never reads `now`, so it survives skew; the basis
    must say liveness was not evaluated.
    """
    # Artefacts dated in the FUTURE; the chain is HEALTHY (30s apart).
    _staleness_fixture(tmp_path, monkeypatch, plan_age_s=-2 * HOUR,
                       state_age_s=-2 * HOUR - 30, uptime_h=753.0)
    gap, basis = tsr.plan_staleness_hours()
    assert basis == "skew", (
        f"backward skew reported basis={basis!r} — it must name liveness as "
        "unmeasured rather than invent a value in either direction")
    assert gap < 0.05, (
        f"a healthy chain measured {gap:.4f}h under skew — this is the false "
        "refusal that `since = inf` produced (it collapses to uptime)")


def test_backward_skew_does_not_refuse_a_healthy_chain(tmp_path, monkeypatch, capsys):
    """The regression `since = inf` introduced: rc 1 on a chain that just wrote."""
    _staleness_fixture(tmp_path, monkeypatch, plan_age_s=-1, state_age_s=-31,
                       uptime_h=753.0)
    rc = tsr.cmd_restore(dry_run=True, staleness_hours=2)
    err = capsys.readouterr().err
    assert rc == 0, (
        f"a one-second backward step refused a healthy chain: {err!r}")


def test_backward_skew_still_refuses_when_the_LAYOUT_disagrees(tmp_path, monkeypatch, capsys):
    """Skew must not become a bypass — contemporaneity is still enforced.

    Reachability: this is the case a `return (gap, "skew")` could have made
    unconditionally passing.
    """
    _staleness_fixture(tmp_path, monkeypatch, plan_age_s=-1 * HOUR,
                       state_age_s=600 * HOUR, uptime_h=753.0)
    rc = tsr.cmd_restore(dry_run=True, staleness_hours=2)
    err = capsys.readouterr().err
    assert rc == 1, "skew must not bypass the contemporaneity check"
    assert "basis=skew" in err and "NOT evaluated" in err, (
        f"the skew refusal does not say liveness went unevaluated: {err!r}")
    # `since < 0` fires on ANY future mtime — a restored backup, `touch -d`, an
    # rsync preserving a bad stamp. Naming "the clock" asserts a cause this code
    # never measured, which is the failure this whole branch exists to avoid.
    assert "clock moved backwards" not in err, (
        f"the skew refusal named a cause it never measured: {err!r}")
    assert "mtime is in the future" in err, err


def test_the_wall_basis_refusal_names_wall_clock_and_not_a_dead_chain(tmp_path, monkeypatch, capsys):
    """The sibling arm the previous round left unpinned in the POSITIVE direction.

    A negative assertion alone let the `wall` value claim the liveness cause.
    """
    _staleness_fixture(tmp_path, monkeypatch, plan_age_s=9 * HOUR, state_age_s=None)
    rc = tsr.cmd_restore(dry_run=True, staleness_hours=2)
    err = capsys.readouterr().err
    assert rc == 1 and "basis=wall" in err
    assert "wall clock" in err, f"the wall refusal does not name wall clock: {err!r}"
    assert "silent" not in err, (
        f"the wall refusal claimed a dead chain, which it never measured: {err!r}")


def test_a_negative_wall_age_is_clamped_rather_than_printed(tmp_path, monkeypatch):
    """No layout to fall back to on this basis, so a negative must not print."""
    _staleness_fixture(tmp_path, monkeypatch, plan_age_s=-5 * HOUR, state_age_s=None)
    gap, basis = tsr.plan_staleness_hours()
    assert basis == "wall" and gap == 0.0, (gap, basis)


def test_resurrect_dir_expands_the_plugin_s_variables_not_just_a_leading_tilde(tmp_path, monkeypatch):
    """🔴 `helpers.sh:resurrect_dir()` expands $HOME/$HOSTNAME/~ ANYWHERE.

    `os.path.expanduser` handles only a leading `~`. `$HOME/state/$HOSTNAME/...`
    is the documented multi-host idiom; leaving it literal makes the path
    un-stat-able, which silently selects the `wall` basis and reintroduces the
    powered-off flaw this change exists to remove.
    """
    import platform as _pf
    monkeypatch.setattr(tsr, "run", lambda cmd: "$HOME/state/$HOSTNAME/resurrect\n")
    got = tsr.resurrect_last_path()
    assert "$HOME" not in str(got) and "$HOSTNAME" not in str(got), (
        f"unexpanded variable survived into the path: {got}")
    assert str(got).startswith(os.path.expanduser("~")), got
    assert _pf.node() in str(got), got
    assert got.name == "last"


# --------------------------------------------------------------------------- #
# The cold-boot loss, MEASURED on the reboot of 2026-09-06
#
# The unit sent 43 `claude --resume` lines and the operator found 54 windows
# sitting at bare shells; the unit exited 0 with Result=success.
#
# 🔴 THE FIRST DIAGNOSIS WAS WRONG AND IS PINNED HERE SO IT IS NOT RE-DERIVED.
# It read "the sends landed in panes that were not ready and were DISCARDED",
# from the observation that the sent text appears nowhere in any scrollback.
# That observation is an ABSENCE, and it is equally consistent with a second
# story, so it cannot choose between them.
#
# What actually happened, from the journal and then confirmed by experiment:
# `Started tmux child pane N launched by process <pid>` arrived in TWO cohorts —
# 17 lines naming the pid the UNIT itself started, then 56 lines 24s later
# naming a different pid. On a cold boot nothing else starts tmux, so the
# script's own `new-session` created the server, INSIDE the unit's cgroup. The
# sends were delivered successfully. Then ExecStart returned and systemd tore
# the cgroup down (`Type=oneshot`, `RemainAfterExit=no`,
# `KillMode=control-group`), taking the server and all 43 claude processes.
#
# Three layers are pinned below, in the order they fire:
#   REFUSAL    — the fix. With no server to restore into, do not manufacture
#   one; a server this process creates cannot outlive it. Exits 0 (a skip, not
#   a failure) because until the unit triggers on the tmux socket instead of a
#   fixed timer, "no server" is the STANDING cold-boot state and a non-zero
#   exit would fire the DND-bypassing OnFailure toast on every boot.
#   SETTLE     — secondary. Once a server DOES exist, resurrect may still be
#   replaying into it; wait for the pane set to hold still rather than guess.
#   DETECTION  — `tmux send-keys` exits 0 for keystrokes that go nowhere, so
#   "sent" is a claim about this process, never about the workspace. Without the
#   verify pass the unit reports success having started nothing.
# --------------------------------------------------------------------------- #

def test_a_pane_set_that_holds_still_is_reported_as_settled(monkeypatch):
    fake = ["1 zsh\n2 zsh"] * 10
    it = iter(fake)
    # monkeypatch, NOT a bare `tsr.pane_fingerprint = …`: a bare assignment has
    # no teardown, so it leaks into every test that runs after it in the same
    # process and the leak is invisible until ordering changes.
    monkeypatch.setattr(tsr, "pane_fingerprint", lambda: next(it))
    ok, waited = tsr.wait_for_workspace_to_settle(settle=3, timeout=30, sleep=lambda s: None)
    assert ok is True
    assert waited == 3.0


def test_a_pane_set_still_being_rebuilt_is_NOT_settled(monkeypatch):
    """Resurrect is still creating and respawning panes, so the fingerprint
    keeps moving. This is the SECONDARY guard — sending into a mid-respawn pane
    is a plausible way to lose a keystroke, but it is NOT what happened on
    2026-09-06; see `test_a_restore_with_no_tmux_server_REFUSES` for that."""
    seq = iter(range(10_000))
    monkeypatch.setattr(tsr, "pane_fingerprint", lambda: f"{next(seq)} zsh")
    ok, waited = tsr.wait_for_workspace_to_settle(settle=3, timeout=10, sleep=lambda s: None)
    assert ok is False
    assert waited == 10.0


def test_no_tmux_server_is_not_a_vacuously_settled_workspace(monkeypatch):
    """🔴 An empty fingerprint is CONSTANT, so an equality-only check would call
    a dead tmux server 'settled' and send into nothing."""
    monkeypatch.setattr(tsr, "pane_fingerprint", lambda: "")
    ok, _ = tsr.wait_for_workspace_to_settle(settle=2, timeout=6, sleep=lambda s: None)
    assert ok is False


def test_the_settle_wait_returns_rather_than_raising_so_a_restore_is_still_attempted(monkeypatch):
    seq = iter(range(10_000))
    monkeypatch.setattr(tsr, "pane_fingerprint", lambda: f"{next(seq)} zsh")
    result = tsr.wait_for_workspace_to_settle(settle=2, timeout=4, sleep=lambda s: None)
    assert isinstance(result, tuple) and result[0] is False


def test_verify_reports_a_send_that_never_started_claude(monkeypatch):
    """tmux accepted the keys and the pane is not running claude. Deliberately
    does NOT assert WHY — an empty pane cannot distinguish 'discarded by an
    unready pane' from 'delivered into a server that was then destroyed', and
    asserting the first is the error this arc actually made."""
    monkeypatch.setattr(tsr, "window_state", lambda t: (True, "zsh"))
    landed, lost = tsr._verify_sends([("Gold:2", "scratch2:2")], attempts=2,
                                     sleep=lambda s: None)
    assert landed == 0
    assert [n for n, _ in lost] == ["Gold:2"]


def test_verify_counts_a_send_that_did_start_claude(monkeypatch):
    monkeypatch.setattr(tsr, "window_state", lambda t: (True, "claude"))
    landed, lost = tsr._verify_sends([("Gold:2", "scratch2:2")], attempts=2,
                                     sleep=lambda s: None)
    assert landed == 1
    assert lost == []


def test_verify_POLLS_so_a_slow_claude_is_not_mis_reported_as_lost(monkeypatch):
    """claude's startup scales with the transcript it resumes; a single fixed
    wait would call the slow ones lost."""
    calls = {"n": 0}

    def slow(_target):
        calls["n"] += 1
        return (True, "claude" if calls["n"] >= 4 else "zsh")

    monkeypatch.setattr(tsr, "window_state", slow)
    landed, lost = tsr._verify_sends([("Gold:2", "scratch2:2")], attempts=10,
                                     sleep=lambda s: None)
    assert landed == 1, f"gave up after {calls['n']} polls"
    assert lost == []


def test_no_tmux_server_bails_EARLY_instead_of_burning_the_whole_timeout(monkeypatch):
    """🔴 MEASURED IN THE SANDBOX TIER, where there is no tmux server: the wait
    ran its full 120s and the target took 121.25s. No panes at all is not
    'not settled yet' — there is no workspace to wait for, and blocking a boot
    for two minutes to learn that is a defect, not caution."""
    monkeypatch.setattr(tsr, "pane_fingerprint", lambda: "")
    ok, waited = tsr.wait_for_workspace_to_settle(
        settle=5, timeout=120, no_server_after=10, sleep=lambda s: None)
    assert ok is False
    assert waited == 10.0, f"burned {waited}s of a 120s timeout"


def test_an_empty_plan_neither_waits_nor_fails(monkeypatch, tmp_path, capsys):
    """🔴 THE SANDBOX-ONLY REGRESSION. With no work to do there is nothing to
    wait for and nothing that can be lost, but the settle wait ran anyway,
    timed out, and the unsettled penalty turned rc 0 into rc 1."""
    plan = tmp_path / "empty.json"
    plan.write_text("[]")
    called = {"waited": False}

    def must_not_run(*a, **k):
        called["waited"] = True
        return (False, 120.0)

    monkeypatch.setattr(tsr, "wait_for_workspace_to_settle", must_not_run)
    monkeypatch.setattr(tsr, "RESURRECT_LAST", tmp_path / "nope")
    monkeypatch.setattr(tsr, "resurrect_last_path", lambda: tmp_path / "nope")
    rc = tsr.cmd_restore(dry_run=False, plan_path=plan)
    assert rc == 0, capsys.readouterr().err
    assert called["waited"] is False, "waited for a workspace with nothing to send"


# --- the REFUSAL: the actual fix for 2026-09-06 ---------------------------- #
#
# 🔴 Every test below monkeypatches `no_tmux_server_to_restore_into` rather
# than depending on whether a tmux server happens to exist. That is not
# fastidiousness: the dev-host tier HAS a live server and the nix sandbox tier
# has NONE, so a test that reads the real thing asserts a different branch in
# each tier and is structurally incapable of failing in one of them. This arc
# has already shipped two defects through exactly that gap.

def _plan_of(tmp_path, n=2):
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps([
        {"session": f"s{i}", "window": i, "cwd": str(tmp_path),
         "session_id": f"id{i}", "codename": f"Gold{i}"} for i in range(n)]))
    return plan


def test_a_restore_with_no_tmux_server_REFUSES(tmp_path, monkeypatch, capsys):
    """🔴 THE REGRESSION TEST FOR THE MEASURED LOSS. With no server, the old
    code ran `tmux new-session` itself, sent into the server it had just
    created inside its own cgroup, and systemd killed it on exit. Nothing may
    be sent, and no session may be created.

    🔴 THE rc == 0 IS STILL DELIBERATE, AND ITS REASON HAS NOW BEEN WRONG TWICE.
    Round 1 justified it with "no server is the normal COLD-BOOT state, because
    the unit fires on a 45s timer" — that trigger is gone. Round 2 replaced it
    with "what is left is a rare race: a stale socket, or a server with zero
    sessions", and the FREQUENCY half of that was false: EVERY tmux server has
    zero sessions for its first ~100ms, which is exactly the window
    `PathChanged=` fires in.

    `wait_for_tmux_server()` now covers that window, and the POST-FIX frequency
    of this branch is UNMEASURED — establishing it needs reboots this change has
    not had. The conclusion does not depend on it: `OnFailure=notify-failure@%n`
    bypasses DND, and a branch reachable by a race must not raise an alarm
    indistinguishable from a real failure, however often the race happens. See
    the comment at the refusal in `cmd_restore` for the full argument.
    """
    monkeypatch.setattr(tsr, "no_tmux_server_to_restore_into", lambda: True)
    # 🔴 WITHOUT THIS THE TEST SLEEPS THE WHOLE POLL BOUND ON THE DEV-HOST TIER.
    # `wait_for_tmux_server` bails instantly when the SOCKET is absent, and the
    # nix sandbox has none — but the dev host has a live one, so the poll would
    # find it and wait out `TMUX_SERVER_WAIT_SECONDS` against a probe stubbed to
    # "absent" forever. Pointing the socket at a path that does not exist is the
    # per-tier-identical fixture; it is the same reason the comment above this
    # block gives for stubbing the probe at all.
    monkeypatch.setattr(tsr, "tmux_socket_path", lambda: tmp_path / "no-socket")
    monkeypatch.setattr(tsr, "resurrect_last_path", lambda: tmp_path / "nope")

    ran: list[list[str]] = []
    monkeypatch.setattr(tsr, "run", lambda cmd: ran.append(cmd) or "")
    monkeypatch.setattr(tsr, "tmux_session_exists", lambda n: False)

    def must_not_wait(*a, **k):
        raise AssertionError("waited instead of refusing")

    monkeypatch.setattr(tsr, "wait_for_workspace_to_settle", must_not_wait)

    rc = tsr.cmd_restore(dry_run=False, plan_path=_plan_of(tmp_path))

    assert rc == 0, (
        "the refusal must not fire the DND-bypassing OnFailure toast. The "
        "conclusion does not rest on how OFTEN this branch is reached — that "
        "frequency is UNMEASURED post-poll, and the two previous attempts to "
        "state it were both wrong. It rests on the branch being reachable by a "
        "RACE at all: such an alarm is indistinguishable from a real failure."
    )
    assert ran == [], f"ran tmux commands while refusing: {ran}"
    assert not any("new-session" in c for cmd in ran for c in cmd)
    assert not any("send-keys" in c for cmd in ran for c in cmd)
    err = capsys.readouterr().err
    assert "REFUSING" in err
    assert "cgroup" in err, "the log must say WHY, or the operator retries forever"


def test_the_refusal_does_NOT_fire_when_a_server_exists(tmp_path, monkeypatch, capsys):
    """The positive control. Without this, a guard hardcoded to refuse always
    would pass the test above while disabling restore entirely."""
    monkeypatch.setattr(tsr, "no_tmux_server_to_restore_into", lambda: False)
    monkeypatch.setattr(tsr, "resurrect_last_path", lambda: tmp_path / "nope")
    monkeypatch.setattr(tsr, "wait_for_workspace_to_settle", lambda *a, **k: (True, 5.0))
    monkeypatch.setattr(tsr, "tmux_session_exists", lambda n: True)
    monkeypatch.setattr(tsr, "window_state", lambda t: (True, "zsh"))
    sent: list[list[str]] = []
    monkeypatch.setattr(tsr, "run", lambda cmd: sent.append(cmd) or "")
    monkeypatch.setattr(tsr, "_verify_sends", lambda t, **k: (len(t), []))

    rc = tsr.cmd_restore(dry_run=False, plan_path=_plan_of(tmp_path))

    assert rc == 0, capsys.readouterr().err
    assert any("send-keys" in c for cmd in sent for c in cmd), \
        "the guard refused a workspace that HAD a server"


def test_the_refusal_is_checked_BEFORE_the_send_loop_creates_anything(tmp_path, monkeypatch):
    """Ordering is the whole fix. A refusal evaluated after the loop's own
    `tmux new-session` would report correctly and still have destroyed the
    workspace."""
    monkeypatch.setattr(tsr, "resurrect_last_path", lambda: tmp_path / "nope")
    # See `test_a_restore_with_no_tmux_server_REFUSES` — absent socket => the
    # poll bails on its first probe, so `order` also pins that the poll does not
    # spin against a stubbed-absent server.
    monkeypatch.setattr(tsr, "tmux_socket_path", lambda: tmp_path / "no-socket")
    order: list[str] = []
    monkeypatch.setattr(tsr, "no_tmux_server_to_restore_into",
                        lambda: order.append("guard") or True)
    monkeypatch.setattr(tsr, "tmux_session_exists",
                        lambda n: order.append("session-exists") or False)
    monkeypatch.setattr(tsr, "run", lambda cmd: order.append(" ".join(cmd)) or "")

    tsr.cmd_restore(dry_run=False, plan_path=_plan_of(tmp_path))

    assert order == ["guard"], f"something ran after/before the guard: {order}"


def test_a_dry_run_is_never_refused(tmp_path, monkeypatch, capsys):
    """A dry run sends nothing, so it cannot manufacture a server — and it is
    the operator's pre-reboot check. Refusing it would remove the only way to
    inspect the plan from a machine with no server."""
    monkeypatch.setattr(tsr, "no_tmux_server_to_restore_into", lambda: True)
    monkeypatch.setattr(tsr, "resurrect_last_path", lambda: tmp_path / "nope")
    monkeypatch.setattr(tsr, "window_state", lambda t: (False, ""))
    rc = tsr.cmd_restore(dry_run=True, plan_path=_plan_of(tmp_path))
    assert rc == 0
    assert "REFUSING" not in capsys.readouterr().err


def test_the_server_guard_reads_has_session_and_treats_failure_as_absent(monkeypatch):
    """Pins the predicate itself, in both directions — the tests above stub it
    out, so without this nothing checks that it reads tmux at all."""
    seen: list[list[str]] = []
    rc = {"v": 1}

    class _R:
        def __init__(self, code): self.returncode = code

    def fake(cmd, **kw):
        seen.append(cmd)
        return _R(rc["v"])

    monkeypatch.setattr(tsr.subprocess, "run", fake)
    assert tsr.no_tmux_server_to_restore_into() is True
    rc["v"] = 0
    assert tsr.no_tmux_server_to_restore_into() is False
    assert seen and seen[0][:2] == ["tmux", "has-session"], seen


# --- the trigger's observable is not this script's precondition ------------ #
#
# 🔴 THE DEPLOY-BLOCKER ROUND 1 FOUND. `tmux-session-restore.path` fires when
# the SOCKET FILE appears. `no_tmux_server_to_restore_into` needs a SESSION.
# tmux creates the socket in `server_start()` BEFORE sourcing its config, and
# the first session is queued behind the whole config — three blocking
# `run-shell` plugin loads plus continuum's replay.
#
# MEASURED 2026-09-07 (continuum EXCLUDED, so every figure is a LOWER bound):
# socket at t0+0.009s; first session at t_sock+0.098–0.112s; the path-triggered
# ExecStart reached `has-session` at t_sock+0.065s in one run and +0.288s in
# another. THE OUTCOME FLIPPED BETWEEN RUNS. When it loses: refusal, exit 0,
# `Result=success`, no `OnFailure`, and NO RETRY — the socket is created once,
# so no second event ever comes. Silent no-restore, the exact failure this unit
# exists to prevent.
#
# 🔴 EVERY TEST HERE INJECTS BOTH `sleep` AND THE SOCKET PATH. Real sleeps would
# make the suite take the bound; a real socket lookup would take a DIFFERENT
# branch on each tier (the dev host has a live socket, the nix sandbox has none)
# — the structural blindness the comment above `_plan_of` describes.

def _poll(monkeypatch, tmp_path, answers, socket_exists=True):
    """Drive `wait_for_tmux_server` with a scripted probe and a fake clock.

    `answers` is the sequence `no_tmux_server_to_restore_into` returns (True =
    absent). It is padded with its last value, so a caller can say "absent
    forever" with `[True]`. Returns `(result, slept)`.
    """
    seq = list(answers)
    calls = {"n": 0}

    def probe():
        i = calls["n"]
        calls["n"] += 1
        return seq[i] if i < len(seq) else seq[-1]

    monkeypatch.setattr(tsr, "no_tmux_server_to_restore_into", probe)
    sock = tmp_path / ("sock" if socket_exists else "nothing-here")
    if socket_exists:
        sock.write_text("")   # a plain file: the poll asks only whether it EXISTS
    monkeypatch.setattr(tsr, "tmux_socket_path", lambda: sock)
    slept: list[float] = []
    return tsr.wait_for_tmux_server(sleep=slept.append), slept


def test_the_poll_returns_at_once_when_a_session_already_exists(tmp_path, monkeypatch):
    """The common case must cost nothing. A poll that always slept one step
    would add latency to every healthy boot and would still pass every other
    test in this block."""
    (found, waited, why), slept = _poll(monkeypatch, tmp_path, [False])
    assert (found, waited, why) == (True, 0.0, "already-running")
    assert slept == [], f"slept before probing: {slept}"


def test_the_poll_bails_INSTANTLY_when_there_is_no_socket(tmp_path, monkeypatch):
    """🔴 THE #1351 REGRESSION, IN A NEW PLACE. An earlier revision of this arc
    burned a full 120s timeout in the nix build sandbox — which has no tmux
    server — and turned an empty-plan restore into a failure. The socket is the
    free discriminator: it is what the path unit triggers on, so its ABSENCE
    means nothing is starting and there is nothing to wait for."""
    (found, waited, why), slept = _poll(monkeypatch, tmp_path, [True],
                                        socket_exists=False)
    # 🔴 THE SLEEP ASSERTION GOES FIRST, DELIBERATELY. A mutation sweep measured
    # the earlier ordering "killing" the removed-bail mutant on the tuple
    # comparison, which carries no message — a kill for the wrong reason, and
    # indistinguishable in the log from a kill by a different guard. The claim
    # this test is ABOUT is "it did not wait", so that is what asserts first.
    assert slept == [], (
        f"waited {sum(slept)}s for a server that cannot be coming — there is no "
        "socket, so nothing is starting. This is the #1351 shape: a full "
        "timeout burned in the nix build sandbox, turning a no-op into a hang."
    )
    assert (found, waited, why) == (False, 0.0, "no-socket"), (found, waited, why)


def test_the_poll_WAITS_for_a_session_that_appears_after_the_socket(tmp_path, monkeypatch):
    """🔴 THE REGRESSION FOR THE DEPLOY-BLOCKER ITSELF. Before this poll, a
    first probe of "absent" was final: the unit refused and exited 0, and no
    second trigger event ever came. Here the socket exists and the session
    appears on the third probe — the measured shape."""
    (found, waited, why), slept = _poll(monkeypatch, tmp_path,
                                        [True, True, False])
    assert found is True, "refused a session that appeared while tmux read its config"
    assert why == "appeared"
    assert waited > 0, "reported no wait for a session it had to wait for"
    assert len(slept) == 2, f"probe/sleep interleaving is wrong: {slept}"


def test_the_poll_gives_up_at_the_BOUND_and_says_which_way(tmp_path, monkeypatch):
    """Bounded, and the reason is distinguishable. `timeout` (a socket existed
    and nothing answered) and `no-socket` (nothing was ever coming) are
    different faults; the refusal message prints them differently so an operator
    is not left guessing which one a silent boot was."""
    (found, waited, why), slept = _poll(monkeypatch, tmp_path, [True])
    assert (found, why) == (False, "timeout")
    assert waited >= tsr.TMUX_SERVER_WAIT_SECONDS, waited
    assert sum(slept) >= tsr.TMUX_SERVER_WAIT_SECONDS, slept
    # Bounded ABOVE too: a poll that overshot its own bound would be a different
    # bug, invisible to the assertion above.
    assert waited < tsr.TMUX_SERVER_WAIT_SECONDS + 1.0, waited


def test_the_poll_bound_leaves_room_for_the_UNMEASURED_replay_term(tmp_path):
    """AN INVARIANT GUARD, LABELLED AS ONE — no bug ever violated it.

    It pins the ARGUMENT for the constant rather than the constant: the
    measurement that produced 0.098–0.112s deliberately EXCLUDED continuum, so
    it is a lower bound on a production boot that also replays ~45 panes from a
    cold cache. A bound set near the measurement would be set against a number
    that does not describe the case it has to cover. 5s is ~45x it and is the
    floor below which the constant is provably arguing from the wrong figure.
    """
    assert tsr.TMUX_SERVER_WAIT_SECONDS >= 5.0, (
        f"TMUX_SERVER_WAIT_SECONDS={tsr.TMUX_SERVER_WAIT_SECONDS} is close to "
        "the 0.11s first-session measurement, which was taken with continuum "
        "EXCLUDED and is therefore a LOWER bound on the window this must cover. "
        "Losing the race is a SILENT no-restore with no retry; overshooting "
        "costs latency on a path that then does nothing."
    )


def test_cmd_restore_does_NOT_refuse_a_session_that_appears_during_the_poll(
    tmp_path, monkeypatch, capsys
):
    """🔴 THE SEAM, NOT THE COMPONENT. `wait_for_tmux_server` can be perfect and
    `cmd_restore` still refuse, if it does not consult it — which is precisely
    what the code did before this round. This drives the whole command."""
    seq = [True, True, False]
    calls = {"n": 0}

    def probe():
        i = calls["n"]
        calls["n"] += 1
        return seq[i] if i < len(seq) else seq[-1]

    sock = tmp_path / "sock"
    sock.write_text("")
    monkeypatch.setattr(tsr, "no_tmux_server_to_restore_into", probe)
    monkeypatch.setattr(tsr, "tmux_socket_path", lambda: sock)
    # 🔴 THE REAL POLL, WITH A NO-OP CLOCK — not a stub. Stubbing the poll here
    # would test that `cmd_restore` believes whatever it is told, which is not
    # the seam this test is about. Captured BEFORE patching so the wrapper does
    # not call itself.
    real_wait = tsr.wait_for_tmux_server
    monkeypatch.setattr(tsr, "wait_for_tmux_server",
                        lambda **k: real_wait(sleep=lambda s: None, socket=sock))
    monkeypatch.setattr(tsr, "resurrect_last_path", lambda: tmp_path / "nope")
    monkeypatch.setattr(tsr, "wait_for_workspace_to_settle", lambda *a, **k: (True, 5.0))
    monkeypatch.setattr(tsr, "tmux_session_exists", lambda n: True)
    monkeypatch.setattr(tsr, "window_state", lambda t: (True, "zsh"))
    sent: list[list[str]] = []
    monkeypatch.setattr(tsr, "run", lambda cmd: sent.append(cmd) or "")
    monkeypatch.setattr(tsr, "_verify_sends", lambda t, **k: (len(t), []))

    rc = tsr.cmd_restore(dry_run=False, plan_path=_plan_of(tmp_path))
    out = capsys.readouterr()

    assert rc == 0, out.err
    assert "REFUSING" not in out.err, (
        "cmd_restore refused a workspace whose session appeared 2 probes in — "
        "the trigger fires on the SOCKET, which tmux creates before its config"
    )
    assert any("send-keys" in c for cmd in sent for c in cmd), \
        "nothing was resumed even though a server was found"
    assert "waited" in out.out, (
        "the wait is not reported. A restore that had to wait and one that did "
        "not are different boots, and the journal is where that is read."
    )


def test_the_refusal_names_the_wait_and_which_fault_it_was(tmp_path, monkeypatch, capsys):
    """A refusal after a full-bound wait and a refusal with no socket at all are
    different faults with the same one-line symptom. The message must separate
    them, or the operator's next step is a guess."""
    sock = tmp_path / "sock"
    sock.write_text("")
    monkeypatch.setattr(tsr, "no_tmux_server_to_restore_into", lambda: True)
    monkeypatch.setattr(tsr, "tmux_socket_path", lambda: sock)
    monkeypatch.setattr(tsr, "wait_for_tmux_server",
                        lambda **k: (False, tsr.TMUX_SERVER_WAIT_SECONDS, "timeout"))
    monkeypatch.setattr(tsr, "resurrect_last_path", lambda: tmp_path / "nope")
    monkeypatch.setattr(tsr, "run", lambda cmd: (_ for _ in ()).throw(
        AssertionError("ran a tmux command while refusing")))

    rc = tsr.cmd_restore(dry_run=False, plan_path=_plan_of(tmp_path))
    err = capsys.readouterr().err

    assert rc == 0
    assert "REFUSING" in err
    assert f"waited {tsr.TMUX_SERVER_WAIT_SECONDS:.1f}s" in err, err
    assert "no server answered" in err, (
        "the timeout fault reads identically to 'the socket was already gone'; "
        f"got: {err}"
    )


# --- the two exit-1 branches, previously unpinned (mutants SURVIVED) ------- #

def test_a_send_that_never_reached_claude_exits_1(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(tsr, "no_tmux_server_to_restore_into", lambda: False)
    monkeypatch.setattr(tsr, "resurrect_last_path", lambda: tmp_path / "nope")
    monkeypatch.setattr(tsr, "wait_for_workspace_to_settle", lambda *a, **k: (True, 5.0))
    monkeypatch.setattr(tsr, "tmux_session_exists", lambda n: True)
    monkeypatch.setattr(tsr, "window_state", lambda t: (True, "zsh"))
    monkeypatch.setattr(tsr, "run", lambda cmd: "")
    monkeypatch.setattr(tsr, "_verify_sends", lambda t, **k: (0, list(t)))

    rc = tsr.cmd_restore(dry_run=False, plan_path=_plan_of(tmp_path))

    err = capsys.readouterr().err
    assert rc == 1, err
    assert "did NOT start claude" in err
    # 🔴 The message must NOT re-assert the refuted mechanism.
    assert "discarded by the pane" not in err


def test_an_unsettled_workspace_exits_1_even_when_every_send_landed(tmp_path, monkeypatch, capsys):
    """'All landed' is measured against a workspace that was still moving, so
    it is luck rather than correctness — and the operator must be told."""
    monkeypatch.setattr(tsr, "no_tmux_server_to_restore_into", lambda: False)
    monkeypatch.setattr(tsr, "resurrect_last_path", lambda: tmp_path / "nope")
    monkeypatch.setattr(tsr, "wait_for_workspace_to_settle", lambda *a, **k: (False, 120.0))
    monkeypatch.setattr(tsr, "tmux_session_exists", lambda n: True)
    monkeypatch.setattr(tsr, "window_state", lambda t: (True, "zsh"))
    monkeypatch.setattr(tsr, "run", lambda cmd: "")
    monkeypatch.setattr(tsr, "_verify_sends", lambda t, **k: (len(t), []))

    rc = tsr.cmd_restore(dry_run=False, plan_path=_plan_of(tmp_path))

    err = capsys.readouterr().err
    assert rc == 1, err
    assert "never settled" in err


def test_a_settled_workspace_with_every_send_landed_exits_0(tmp_path, monkeypatch, capsys):
    """The discriminating control for the two tests above: without it, a
    mutant returning 1 unconditionally passes both."""
    monkeypatch.setattr(tsr, "no_tmux_server_to_restore_into", lambda: False)
    monkeypatch.setattr(tsr, "resurrect_last_path", lambda: tmp_path / "nope")
    monkeypatch.setattr(tsr, "wait_for_workspace_to_settle", lambda *a, **k: (True, 5.0))
    monkeypatch.setattr(tsr, "tmux_session_exists", lambda n: True)
    monkeypatch.setattr(tsr, "window_state", lambda t: (True, "zsh"))
    monkeypatch.setattr(tsr, "run", lambda cmd: "")
    monkeypatch.setattr(tsr, "_verify_sends", lambda t, **k: (len(t), []))

    rc = tsr.cmd_restore(dry_run=False, plan_path=_plan_of(tmp_path))
    out = capsys.readouterr()
    assert rc == 0, out.err
    assert "verified: 2 of 2" in out.out, out.out


# --------------------------------------------------------------------------- #
# cmd_save GENERATIONS — the 2026-09-06 data-loss defect
#
# 🔴 WHAT THESE PIN, AND WHY THEY ARE PHRASED THE WAY THEY ARE.
#
# MEASURED on the workbench 2026-09-06:
#   21:47:17  a good plan — 47 entries, 46 carrying a bound session id
#   21:54:21  the tmux server died, taking 47 claude conversations
#   22:09:40  a continuum autosave fired on the DEGRADED post-crash workspace
#             and `cmd_save` overwrote the plan with 10 entries. The cheat-sheet
#             went in the same second. NO BACKUP EXISTED.
# The conversations were recovered only from tmux-resurrect's TIMESTAMPED saves.
#
# The regression tests below therefore assert the OPERATOR'S OBSERVABLE — "after
# a shrinking save the previous bindings are still recoverable from the state
# directory" — and NOT "a file named restore-plan_<stamp>.json exists". Written
# the second way they would go red at base with an AttributeError, which proves
# only that a new function was added; written this way they go red at base on
# the data loss itself.
#
# 🔴 Every fixture here repoints STATE_DIR/PLAN/CHEAT into `tmp_path`. Nothing
# under this heading may touch `~/.config/initiatives` — that is the operator's
# live recovery state, and writing to it is the very failure under test.
# --------------------------------------------------------------------------- #
def _gen_entry(win, sid):
    return {"session": "main", "window": str(win), "codename": "Vapor",
            "cwd": "/r", "session_id": sid, "bind_source": "ledger" if sid else "",
            "ledger_reason": "ok" if sid else "no-record",
            "title": f"w{win}", "hint": ""}


def _plan_with(n_entries, n_bound, first=0):
    """A plan of `n_entries`, the first `n_bound` of which carry a session id.

    Ids are distinct per (index, offset) so two plans built with different
    `first` share no bindings — a fixture whose ids collided could not see a
    mutant that ignored the previous plan entirely.
    """
    return [_gen_entry(i, f"{first + i:08d}-2222-4333-8444-555555555555"
                          if i < n_bound else "")
            for i in range(n_entries)]


@pytest.fixture
def state(tmp_path, monkeypatch):
    """Repoint every state path into tmp_path and hand back the directory."""
    d = tmp_path / "initiatives"
    monkeypatch.setattr(tsr, "STATE_DIR", d)
    monkeypatch.setattr(tsr, "PLAN", d / "restore-plan.json")
    monkeypatch.setattr(tsr, "CHEAT", d / "restore-cheatsheet.md")
    return d


def _save(monkeypatch, plan):
    monkeypatch.setattr(tsr, "build_plan", lambda: plan)
    return tsr.cmd_save()


def _bound_ids(plan):
    """The test's OWN copy of `bound_ids`, and deliberately not the module's.

    🔴 The regression tests below must go red at base ON THE DATA LOSS. Calling
    `tsr.bound_ids` there makes them red at base with an `AttributeError`
    instead, which proves only that a new function was added and says nothing
    about whether the bindings survived. Measured: with `tsr.bound_ids` in the
    assertion, two of the three fell to AttributeError before reaching it.
    """
    return {e["session_id"] for e in plan if e.get("session_id")}


def _recoverable_ids(state_dir):
    """Every session id recoverable from ANY plan-shaped JSON under `state_dir`.

    Deliberately implementation-blind: it walks the directory rather than
    knowing where generations live, so it measures "is the binding still on
    disk" and not "was it filed the way I expected".
    """
    ids = set()
    for p in state_dir.rglob("*.json"):
        try:
            data = json.loads(p.read_text())
        except (OSError, ValueError):
            continue
        if isinstance(data, list):
            ids |= {e.get("session_id") for e in data
                    if isinstance(e, dict) and e.get("session_id")}
    return ids


def test_generations_live_beside_the_pointer_never_in_the_real_state_dir(state):
    """🔴 The safety property every other test here depends on.

    `generations_dir()` must derive from `PLAN.parent`. Bound to the module's
    own `STATE_DIR` at import time instead, every test below would write into
    the operator's LIVE `~/.config/initiatives` while still passing.
    """
    assert tsr.generations_dir().parent == tsr.PLAN.parent, (
        "the generations directory is not derived from PLAN.parent — a test "
        "that repoints PLAN would write generations into the real "
        "~/.config/initiatives, which is live recovery state")
    assert str(tsr.generations_dir()).startswith(str(state)), (
        f"generations_dir() escaped the test state dir: {tsr.generations_dir()}")


def test_a_shrinking_save_does_not_destroy_the_previous_bindings(state, monkeypatch,
                                                                 capsys):
    """🔴 THE REGRESSION. RED AT BASE — this is the 2026-09-06 loss.

    47 entries / 46 bound, then a degraded 10-entry save. Under the old
    single-mutable-file writer the 46 ids were gone from disk the instant the
    second save returned; here they must still be recoverable.
    """
    good = _plan_with(47, 46, first=100)
    assert _save(monkeypatch, good) == 0
    capsys.readouterr()

    degraded = _plan_with(10, 10, first=900)
    assert _save(monkeypatch, degraded) == 0
    capsys.readouterr()

    survived = _recoverable_ids(state)
    lost = _bound_ids(good) - survived
    assert not lost, (
        f"{len(lost)} of the previous save's 46 bound session ids are no longer "
        f"recoverable from anywhere under {state} — a degraded save destroyed "
        "the good plan, which is exactly the 2026-09-06 defect")


def test_a_shrinking_save_does_not_destroy_the_previous_cheat_sheet(state,
                                                                    monkeypatch,
                                                                    capsys):
    """🔴 RED AT BASE. Same writer, same second, same loss — the cheat-sheet.

    Asserted on a resume COMMAND, not on the file's name: the cheat-sheet's
    whole value is that each entry carries a runnable `claude --resume <id>`,
    and that is what the operator rebuilt 24 conversations from.
    """
    good = _plan_with(47, 46, first=100)
    assert _save(monkeypatch, good) == 0
    capsys.readouterr()
    wanted = f"claude --resume {good[0]['session_id']}"

    assert _save(monkeypatch, _plan_with(10, 10, first=900)) == 0
    capsys.readouterr()

    found = [p for p in state.rglob("*.md") if wanted in p.read_text()]
    assert found, (
        f"no file under {state} still carries `{wanted}` — the previous "
        "cheat-sheet was overwritten by the degraded save, so the resume "
        "commands for the dropped conversations are gone")


def test_a_pre_generations_plan_is_preserved_by_the_first_new_save(state,
                                                                   monkeypatch,
                                                                   capsys):
    """🔴 RED AT BASE. The DEPLOY of this change must not be the bad save.

    A host upgrading has a REGULAR FILE at the pointer path holding the last
    plan the old writer wrote — possibly the only good one. Repointing that path
    without copying it in first unlinks it.
    """
    state.mkdir(parents=True)
    good = _plan_with(47, 46, first=100)
    tsr.PLAN.write_text(json.dumps(good))
    tsr.CHEAT.write_text(tsr.cheat_sheet(good))
    assert not tsr.PLAN.is_symlink(), "fixture must start as a real file"

    assert _save(monkeypatch, _plan_with(10, 10, first=900)) == 0
    capsys.readouterr()

    lost = _bound_ids(good) - _recoverable_ids(state)
    assert not lost, (
        f"{len(lost)} bound ids from the pre-existing regular-file plan were "
        "lost when the pointer was first swapped — the migration must adopt it "
        "as a generation before moving the pointer")


def test_the_pointer_still_reads_as_the_current_plan(state, monkeypatch, capsys):
    """Reader compatibility: `cmd_restore`, `plan_staleness_hours`,
    `tmux-restore-observe.sh` and `cmd_show` all read the two fixed paths."""
    plan = _plan_with(4, 3, first=500)
    assert _save(monkeypatch, plan) == 0
    capsys.readouterr()

    assert tsr.PLAN.exists(), "restore-plan.json does not resolve after a save"
    assert json.loads(tsr.PLAN.read_text()) == plan, (
        "restore-plan.json does not read back as the plan just saved — every "
        "existing reader goes through this path")
    assert tsr.CHEAT.exists() and "claude --resume" in tsr.CHEAT.read_text(), (
        "restore-cheatsheet.md does not resolve to the current cheat-sheet")
    assert tsr.cmd_show() == 0
    assert "claude --resume" in capsys.readouterr().out


def test_a_shrinking_save_is_written_not_refused(state, monkeypatch, capsys):
    """🔴 AN INVARIANT GUARD, NOT A REGRESSION TEST — green at base by design.

    It exists because the tempting fix is a guard that REFUSES to write a
    smaller plan, and that guard would fire on the operator closing windows,
    i.e. on ordinary use. A refusal here would leave the current pointer on a
    workspace that no longer exists. Labelled so nobody counts it as coverage of
    the loss.
    """
    assert _save(monkeypatch, _plan_with(47, 46, first=100)) == 0
    capsys.readouterr()
    small = _plan_with(3, 1, first=900)
    rc = _save(monkeypatch, small)
    capsys.readouterr()

    assert rc == 0, "a legitimately smaller save was refused"
    assert json.loads(tsr.PLAN.read_text()) == small, (
        "the pointer was not advanced to the smaller plan — a shrink must be "
        "WARNED about, never blocked")


def test_a_save_dropping_bound_ids_names_the_recovery_command(state, monkeypatch,
                                                              capsys):
    """🔴 RED AT BASE. The warning must hand over a command that WORKS.

    Not just "something was dropped": the path it names is opened and checked to
    carry the dropped ids, because a warning pointing at the wrong file is worse
    than none.
    """
    good = _plan_with(47, 46, first=100)
    assert _save(monkeypatch, good) == 0
    capsys.readouterr()
    assert _save(monkeypatch, _plan_with(10, 10, first=900)) == 0
    err = capsys.readouterr().err

    assert "DROPS 46 bound session id(s)" in err, (
        f"the shrink report did not name the 46 dropped bindings:\n{err}")
    assert "47 entries → 10" in err, (
        f"the shrink report did not name both entry counts:\n{err}")
    m = re.search(r"restore --plan (\S+)", err)
    assert m, f"the shrink report named no recovery command:\n{err}"
    named = Path(m.group(1))
    assert named.exists(), f"the recovery command names a path that does not exist: {named}"
    assert tsr.bound_ids(json.loads(named.read_text())) >= tsr.bound_ids(good), (
        f"the plan the recovery command names ({named}) does not carry the "
        "dropped bindings — it points at the wrong generation")


def test_a_shrink_that_drops_no_bindings_stays_quiet(state, monkeypatch, capsys):
    """The DISCRIMINATING CONTROL for the report above.

    Without it, a report keyed on the ENTRY COUNT instead of on bound ids passes
    the previous test exactly. Closing five unbound windows loses nothing
    resumable, and a line that fires there becomes noise and stops being read.
    """
    assert _save(monkeypatch, _plan_with(8, 3, first=100)) == 0
    capsys.readouterr()
    # Same three bindings, five unbound windows closed: entries 8 -> 3.
    assert _save(monkeypatch, _plan_with(3, 3, first=100)) == 0
    err = capsys.readouterr().err

    assert "DROPS" not in err, (
        "a shrink that dropped NO bound session id was reported as dropping "
        f"bindings — the report is keyed on the entry count, not on ids:\n{err}")


def test_pruning_keeps_exactly_the_newest_generations(state, monkeypatch, capsys):
    """🔴 THE POSITIVE CONTROL for retention: the count must MOVE.

    Five saves under a keep of 3 must leave 3, and they must be the NEWEST 3 —
    a mutant pruning the wrong end leaves the right count. `1 pruned` is
    asserted on the run that first exceeds the cap, so a pruner wired to nothing
    (which would leave 5 and report 0) cannot pass.
    """
    monkeypatch.setattr(tsr, "KEEP_GENERATIONS", 3)
    seen = []
    for i in range(5):
        assert _save(monkeypatch, _plan_with(2, 2, first=100 * (i + 1))) == 0
        out = capsys.readouterr().out
        seen.append(tsr.list_generations()[-1])
        if i < 3:
            assert "0 pruned" in out, f"pruned below the cap on save {i}:\n{out}"
        else:
            assert "1 pruned" in out, f"did not prune above the cap on save {i}:\n{out}"

    kept = tsr.list_generations()
    assert len(kept) == 3, f"kept {len(kept)} generations under a cap of 3: {kept}"
    assert kept == seen[-3:], (
        f"retention kept the wrong end — kept {kept}, newest three were {seen[-3:]}")


def test_pruning_never_deletes_the_generation_the_pointer_is_on(state, monkeypatch,
                                                                capsys):
    """🔴 The guard that makes pruning unable to recreate the defect.

    At `keep=0` every stamp is doomed, so only `protect` can save the one the
    pointer was just aimed at. Without it the save returns 0 having left
    `restore-plan.json` DANGLING and no current plan on disk — a bad save
    destroying the plan, from inside the mechanism built to stop that.
    """
    assert _save(monkeypatch, _plan_with(4, 4, first=100)) == 0
    capsys.readouterr()
    monkeypatch.setattr(tsr, "KEEP_GENERATIONS", 0)
    plan = _plan_with(4, 4, first=200)
    assert _save(monkeypatch, plan) == 0
    capsys.readouterr()

    assert tsr.PLAN.exists(), (
        "restore-plan.json is dangling after a save — pruning deleted the "
        "generation the pointer had just been aimed at")
    assert json.loads(tsr.PLAN.read_text()) == plan
    assert tsr.list_generations(), "pruning removed the current generation"


def test_prune_generations_returns_what_it_removed(state, monkeypatch):
    """`prune_generations` in isolation, incl. the `protect` refusal it reports.

    The removed list is what `cmd_save` prints, so a pruner that deleted files
    and reported nothing would make the summary line silently false.
    """
    d = tsr.generations_dir()
    d.mkdir(parents=True)
    stamps = ["20260101T000001", "20260101T000002", "20260101T000003"]
    for s in stamps:
        for p in tsr.generation_paths(s):
            p.write_text("[]")

    removed = tsr.prune_generations(keep=1, protect=(stamps[0],))
    assert removed == [stamps[1]], (
        f"expected only the middle stamp removed, got {removed}")
    assert tsr.list_generations() == [stamps[0], stamps[2]], (
        "a protected stamp was deleted, or the newest was not kept")
    assert not tsr.generation_paths(stamps[1])[1].exists(), (
        "the cheat-sheet of a pruned generation was left behind — retention "
        "would then be unbounded in the .md half")


def test_two_saves_in_one_second_do_not_share_a_generation(state, monkeypatch,
                                                           capsys):
    """🔴 The stamp has one-second resolution; the incident was a same-second write.

    A manual `save` racing the 15-minute continuum hook lands in the same
    second. Sharing a stamp would overwrite the previous generation — the
    mutable-file defect reintroduced inside its own fix.

    The clock is frozen on `tsr`'s OWN `time` reference, never on the real
    module — patching that would hand a frozen clock to pytest itself.
    """
    import time as _real_time
    # 🔴 `gmtime` is the one the stamp uses — the stamp is UTC, deliberately, so
    # that it stays monotonic across a DST fall-back (see `generation_stamp`).
    # `localtime`/`mktime` are kept only because other call sites still use them;
    # a stub that omitted `gmtime` is what caught this change, which is the stub
    # doing its job rather than a reason to reach back for local time.
    monkeypatch.setattr(tsr, "time", types.SimpleNamespace(
        time=lambda: 1757282857.0,
        gmtime=_real_time.gmtime,
        localtime=_real_time.localtime,
        strftime=_real_time.strftime,
        strptime=_real_time.strptime,
        mktime=_real_time.mktime))

    good = _plan_with(47, 46, first=100)
    assert _save(monkeypatch, good) == 0
    capsys.readouterr()
    assert _save(monkeypatch, _plan_with(10, 10, first=900)) == 0
    capsys.readouterr()

    assert len(tsr.list_generations()) == 2, (
        f"two saves in one second produced {len(tsr.list_generations())} "
        "generation(s) — the second overwrote the first")
    assert not (tsr.bound_ids(good) - _recoverable_ids(state)), (
        "the same-second save destroyed the previous generation's bindings")


def test_a_new_stamp_is_never_older_than_an_existing_generation(state):
    """🔴 The invariant `list_generations`'s name-sort and all of pruning rest on.

    A FREED slot must not be handed back. Measured while writing these tests:
    pruning frees the oldest stamp, a same-second save is handed that slot, the
    new generation sorts OLDEST, pruning selects it as doomed, and the cap stops
    being enforced (`4 kept (max 3), 0 pruned`). A backwards clock step
    reproduces it with no race at all — which is why this asks for a stamp from
    a time BEFORE everything on disk, a case no timing can excuse.
    """
    tsr.generations_dir().mkdir(parents=True)
    for p in tsr.generation_paths("20260601T120000"):
        p.write_text("[]")

    # 2026-01-01, i.e. five months older than the generation already present.
    stamp = tsr.free_generation_stamp(when=1767268800.0)
    assert stamp > "20260601T120000", (
        f"free_generation_stamp returned {stamp}, which sorts BEFORE an "
        "existing generation — pruning would then treat the newest save as the "
        "oldest and the retention cap would stop being enforced")


def test_a_failed_write_leaves_the_previous_file_whole(state, monkeypatch):
    """🔴 `write_text` truncates first; `_write_atomic` must not.

    A save killed mid-write would otherwise leave a zero-byte plan — the same
    loss in a smaller shape. Simulated by failing the rename, which is the only
    step that can touch the destination.
    """
    state.mkdir(parents=True)
    target = state / "gen.json"
    target.write_text('["intact"]')

    def boom(*a, **k):
        raise OSError("no space left on device")

    monkeypatch.setattr(tsr.os, "replace", boom)
    with pytest.raises(OSError):
        tsr._write_atomic(target, "x" * 5)
    assert target.read_text() == '["intact"]', (
        "a failed write truncated or damaged the destination — the write is "
        "not going through a temp file + rename")


def test_an_empty_plan_still_writes_no_generation(state, monkeypatch, capsys):
    """Pre-existing behaviour, now load-bearing for retention.

    A tmux-less moment must not spend a generation slot: at the cap, empty saves
    would push real plans off the oldest end.
    """
    assert _save(monkeypatch, _plan_with(4, 4, first=100)) == 0
    capsys.readouterr()
    before = tsr.list_generations()
    assert _save(monkeypatch, []) == 1
    capsys.readouterr()
    assert tsr.list_generations() == before, (
        "an empty plan created a generation — repeated, that evicts real ones")


# --------------------------------------------------------------------------- #
# Audit round 1 (2026-09-09) — four findings, each pinned BEHAVIOURALLY.
#
# 🔴 THE FIRST ONE IS A MEASURED DATA LOSS AND IT IS NOT A CLOCK-FIXTURE
# CURIOSITY. `free_generation_stamp` anchored on a LOCAL-time parse, so inside a
# DST fall-back the anchor landed behind `now`, `stamp > newest` could never be
# satisfied, and the fall-through returned an OCCUPIED stamp — destroying a bound
# session id and its cheat-sheet at rc 0 with nothing printed.
# --------------------------------------------------------------------------- #


def _dst_clock(monkeypatch, now):
    """Freeze `tsr`'s own `time` at `now`, with the REAL zone-aware functions.

    The zone matters: these tests set TZ explicitly rather than trusting the
    host's, so they assert the same thing on a CI box in UTC as on the operator's
    workbench in America/Winnipeg. Without that they would pass vacuously
    wherever the local zone has no DST.
    """
    import time as _real_time
    monkeypatch.setattr(tsr, "time", types.SimpleNamespace(
        time=lambda: now,
        gmtime=_real_time.gmtime,
        localtime=_real_time.localtime,
        strftime=_real_time.strftime,
        strptime=_real_time.strptime,
        mktime=_real_time.mktime))


@pytest.mark.parametrize("tz", ["America/Winnipeg", "UTC"])
def test_a_generation_stamp_is_monotonic_across_a_DST_fall_back(state, monkeypatch, tz):
    """🔴 The stamp must not repeat when the LOCAL clock repeats an hour.

    2026-11-01 in America/Winnipeg runs 01:00-01:59 CDT and then 01:00-01:59 CST
    again. A local-time stamp produces the SAME name for two instants an hour
    apart, and `list_generations` sorts on that name — so the second save's
    generation sorts as though it were the first's, and the anchor built from it
    lands behind `now`.

    Pinned in UTC as well as in the DST zone, so the test cannot pass merely
    because the runner happens to sit somewhere without DST.
    """
    monkeypatch.setenv("TZ", tz)
    import time as _real_time
    _real_time.tzset()
    # 🔴 DERIVED, NOT GUESSED. The first draft used an epoch constant that was
    # a day off (2026-10-31), so the fixture never entered the repeated hour and
    # BOTH parametrisations passed at base — a vacuous regression test that read
    # as coverage. Verified: at these two instants the LOCAL stamps are both
    # `20261101T010000` (collide) while the UTC stamps are `…T060000` /
    # `…T070000` (do not).
    first = 1793512800.0            # 2026-11-01 06:00:00 UTC = 01:00 CDT
    second = first + 3600           # 2026-11-01 07:00:00 UTC = 01:00 CST
    a = tsr.generation_stamp(first)
    b = tsr.generation_stamp(second)
    assert a != b, (
        f"two instants an hour apart produced the SAME generation stamp {a!r} "
        f"in TZ={tz} — one save would overwrite the other")
    assert b > a, (
        f"the later instant produced the EARLIER-sorting stamp ({b!r} <= {a!r}) "
        f"in TZ={tz} — list_generations sorts on the name, so pruning would "
        "delete the wrong end")


@pytest.mark.parametrize("tz", ["America/Winnipeg", "UTC"])
def test_a_save_inside_a_repeated_local_hour_does_not_destroy_a_generation(
        state, monkeypatch, capsys, tz):
    """The data loss end to end through `cmd_save` — an INVARIANT GUARD.

    🔴 LABELLED, NOT COUNTED AS REGRESSION COVERAGE, BECAUSE IT IS **GREEN AT
    BASE** IN BOTH ZONES — measured, not assumed. The deterministic red-at-base
    guard for this class is
    `test_a_generation_stamp_is_monotonic_across_a_DST_fall_back`, which fails at
    base under `America/Winnipeg`.

    Why this one does not go red at base: with a local-time stamp the two saves
    DO collide on `20261101T010000`, but the base implementation's anchor then
    calls `time.mktime` on that ambiguous local string, and glibc's tie-break for
    a repeated hour is unspecified. In this harness it resolves to the CDT
    reading, so the anchor steps forward and the second save is handed a free
    stamp anyway. The round-1 audit measured it going BOTH ways depending on
    prior calls in the process, and measured the losing direction in the
    production call sequence — so the loss is real but not deterministic from a
    test, and a test that pretends otherwise would be flaky rather than
    protective.

    It stays because it pins the PROPERTY that matters — the first save's bound
    ids remain recoverable after a save inside the repeated hour — on the real
    `cmd_save` path, and it will hold that property against any future rework of
    the stamping. It just must not be read as evidence that the bug is caught.
    """
    monkeypatch.setenv("TZ", tz)
    import time as _real_time
    _real_time.tzset()
    first = 1793512800.0            # 01:00 CDT — see the derivation above
    second = first + 3600           # 01:00 CST, the SAME local wall-clock time

    _dst_clock(monkeypatch, first)
    good = _plan_with(47, 46, first=100)
    assert _save(monkeypatch, good) == 0
    capsys.readouterr()

    _dst_clock(monkeypatch, second)
    assert _save(monkeypatch, _plan_with(10, 10, first=900)) == 0
    capsys.readouterr()

    lost = _bound_ids(good) - _recoverable_ids(state)
    assert not lost, (
        f"{len(lost)} of the first save's bound session ids are no longer "
        f"recoverable after a save inside the repeated local hour (TZ={tz}) — "
        "the second save overwrote the first's generation")


def test_an_exhausted_stamp_search_REFUSES_instead_of_overwriting(state, monkeypatch):
    """🔴 The fall-through used to return an OCCUPIED stamp and clobber it.

    A save that fails loudly costs one save. A save that clobbers costs the bound
    plan it existed to protect. Pinned on the REFUSAL, and on the existing
    generations surviving it.

    🔴 GETTING THE FIXTURE RIGHT IS THE WHOLE TEST, and the obvious version does
    NOT reach this path: occupying a contiguous run of stamps does not exhaust
    the search, because the anchor JUMPS PAST `newest` and the slot after the
    newest generation is free by definition. Written that way first, and it
    scored DID NOT RAISE — the guard was unreachable, not working.

    The path is reachable only when the directory holds stamps that this
    caller's `list_generations` did not see — i.e. another process claimed them
    between the listing and the claim. That is precisely the race
    `tmux-post-save.sh` creates by backgrounding `save` with no lock, so it is
    simulated here by holding the listing empty while the files exist.
    """
    state.mkdir(parents=True, exist_ok=True)
    tsr.generations_dir().mkdir(parents=True, exist_ok=True)
    now = 1757282857.0
    for i in range(0, 8):
        tsr.generation_paths(tsr.generation_stamp(now + i))[0].write_text("[]")
    survivors_before = sorted(p.name for p in tsr.generations_dir().iterdir())
    # The stale view: this caller saw an empty directory a moment ago.
    monkeypatch.setattr(tsr, "list_generations", lambda: [])

    with pytest.raises(RuntimeError) as e:
        tsr.free_generation_stamp(now, limit=5)
    assert "REFUSING" in str(e.value), (
        f"the refusal must say what it refused to do; got {e.value!r}")
    assert sorted(p.name for p in tsr.generations_dir().iterdir()) == survivors_before, (
        "the refusing path still modified the generations directory")


def test_a_concurrent_save_cannot_take_a_stamp_another_save_claimed(state, monkeypatch):
    """🟡 `tmux-post-save.sh` backgrounds `save` with NO lock, so this races.

    An `exists()` check cannot close it — both callers see the slot free. The
    claim is `O_CREAT|O_EXCL`, so the second caller must be handed a DIFFERENT
    stamp even though nothing was written between the two calls.
    """
    state.mkdir(parents=True, exist_ok=True)
    now = 1757282857.0
    a = tsr.free_generation_stamp(now)
    b = tsr.free_generation_stamp(now)
    assert a != b, (
        f"two callers in the same second were both handed {a!r} — one save "
        "would overwrite the other's generation")


def test_write_atomic_temp_names_are_per_process(state, monkeypatch, tmp_path):
    """🟡 A fixed `.tmp` is shared state between two concurrent saves.

    Measured on a fixed name: the second `os.replace` raised FileNotFoundError
    because the first had already renamed the shared temp away. Asserted by
    catching the temp name in the act, so it pins the NAME rather than the
    absence of a crash.
    """
    seen = []
    target = tmp_path / "x.json"
    real_replace = os.replace

    def spy(src, dst):
        seen.append(str(src))
        return real_replace(src, dst)

    monkeypatch.setattr(tsr.os, "replace", spy)
    tsr._write_atomic(target, "hello")
    assert seen, "the write did not go through os.replace at all"
    assert str(os.getpid()) in seen[0], (
        f"the temp name {seen[0]!r} does not carry the pid — two concurrent "
        "saves would share it")


def test_prune_reports_only_what_it_actually_deleted(state, monkeypatch, capsys):
    """🟡 The unlink OSError was swallowed and the stamp appended regardless.

    Measured: `2 kept (max 1), 1 pruned` while NOTHING had been pruned. A
    persistent unlink failure gives unbounded growth reported as healthy
    retention on every save.
    """
    state.mkdir(parents=True, exist_ok=True)
    tsr.generations_dir().mkdir(parents=True, exist_ok=True)
    for stamp in ("20260101T000001", "20260101T000002"):
        tsr.generation_paths(stamp)[0].write_text("[]")

    real_unlink = Path.unlink

    def refuse(self, *a, **k):
        if _GEN_PLAN_RE_TEST.match(self.name):
            raise PermissionError("read-only mount")
        return real_unlink(self, *a, **k)

    monkeypatch.setattr(Path, "unlink", refuse)
    removed = tsr.prune_generations(keep=1)
    assert removed == [], (
        f"prune reported {removed} as removed while every unlink was refused — "
        "a reassuring count from a pruner wired to nothing")
    assert len(tsr.list_generations()) == 2, "the fixture did not hold"


_GEN_PLAN_RE_TEST = re.compile(r"^restore-plan_\d{8}T\d{6}\.json$")


def test_the_shrink_report_never_names_a_file_this_save_just_pruned(
        state, monkeypatch, capsys):
    """🟡 `protect=(stamp,)` did not cover `previous_gen`.

    The report tells the operator to restore from the previous generation; at a
    low cap the same call had already deleted it. This file's own rule is that a
    warning pointing at the wrong file is worse than no warning.
    """
    monkeypatch.setattr(tsr, "KEEP_GENERATIONS", 1)
    good = _plan_with(47, 46, first=100)
    assert _save(monkeypatch, good) == 0
    capsys.readouterr()
    assert _save(monkeypatch, _plan_with(10, 10, first=900)) == 0
    err = capsys.readouterr().err

    m = re.search(r"restore --plan (\S+)", err)
    assert m, f"the shrink report did not name a recovery command:\n{err}"
    named = Path(m.group(1))
    assert named.exists(), (
        f"the shrink report names {named}, which this same save deleted — a "
        "dangling recovery command is worse than none")


# --------------------------------------------------------------------------- #
# 2026-09-11 INCIDENT — a tmux server died with 52 bound conversations, and the
# automated restore recovered ONE. Three defects, each pinned below on the
# behaviour that actually failed, not on the shape of the fix.
# --------------------------------------------------------------------------- #


class _FakeTmux:
    """A tmux stand-in that reproduces the REAL `display-message` fallback.

    🔴 THE WHOLE POINT: `display-message -t <session>:<missing>` does NOT fail.
    It answers about the session's CURRENT window and exits 0. A double that
    returned '' for a missing window would make the old code look correct and
    the regression untestable — the same class as #1467's stub server accepting
    what production refuses.

    Measured on the live server (scratch3 had only window 1):
        display-message -p -t scratch3:1  -> '1:zsh'  rc=0
        display-message -p -t scratch3:87 -> '1:zsh'  rc=0   <- the lie
    """

    def __init__(self, windows):
        self.windows = dict(windows)          # {"sess:idx": "command"}
        self.sent = []

    def _resolve(self, target):
        """Where tmux ACTUALLY delivers a target: the window if it exists, else
        the session's first window (the documented fallback)."""
        if target in self.windows:
            return target
        sess = target.partition(":")[0]
        for k in self.windows:
            if k.startswith(f"{sess}:"):
                return k
        return target

    def __call__(self, argv):
        if argv[:2] == ["tmux", "display-message"]:
            target = argv[argv.index("-t") + 1]
            sess = target.partition(":")[0]
            if target in self.windows:
                return self.windows[target]
            # THE FALLBACK: any index in a live session resolves to its first.
            for k, v in self.windows.items():
                if k.startswith(f"{sess}:"):
                    return v
            return ""
        if argv[:2] == ["tmux", "list-windows"]:
            sess = argv[argv.index("-t") + 1]
            return "\n".join(f"{k.split(':')[1]}\t{v}"
                             for k, v in self.windows.items()
                             if k.startswith(f"{sess}:"))
        if argv[:2] == ["tmux", "send-keys"]:
            # 🔴 send-keys RESOLVES THE SAME WAY display-message DOES. Recording
            # the target as PASSED would make this double more forgiving than
            # tmux: on the pre-fix code every send is addressed to a missing
            # window and tmux delivers them ALL to the session's first window,
            # overwriting each other. That collapse IS the incident, so the
            # double has to reproduce it or the end-to-end test is vacuous —
            # measured: it passed at base until this resolution was added.
            target = argv[argv.index("-t") + 1]
            self.sent.append((self._resolve(target), argv[-2]))
            return ""
        if argv[:2] == ["tmux", "new-window"]:
            t = argv[argv.index("-t") + 1]
            self.windows[t] = "zsh"
            return ""
        return ""


def test_window_state_does_not_report_a_MISSING_window_as_present(monkeypatch):
    """🔴 THE 2026-09-11 ROOT CAUSE, pinned directly.

    `scratch3` has one window. Asking about index 87 must say MISSING. The old
    implementation asked `display-message`, which answered about window 1 and
    exited 0, so the predicate returned `(True, 'zsh')` for a window that does
    not exist — `new-window` was therefore never called and 50 resumes piled
    into the windows that did exist.
    """
    fake = _FakeTmux({"scratch3:1": "zsh"})
    monkeypatch.setattr(tsr, "run", fake)

    assert tsr.window_state("scratch3:1") == (True, "zsh"), "a REAL window must be found"
    exists, cmd = tsr.window_state("scratch3:87")
    assert exists is False, (
        f"window_state said a MISSING window exists (got {(exists, cmd)!r}) — this is the "
        "defect that cost 51 of 52 conversations on 2026-09-11")


def test_window_state_is_exact_and_not_a_prefix_match(monkeypatch):
    """A session with windows 1 and 12 must not let `:1` answer for `:12`.

    Guards the enumerate-then-compare against a future 'simplification' to a
    substring or prefix test, which would reintroduce the same class.
    """
    fake = _FakeTmux({"s:1": "zsh", "s:12": "claude"})
    monkeypatch.setattr(tsr, "run", fake)
    assert tsr.window_state("s:1") == (True, "zsh")
    assert tsr.window_state("s:12") == (True, "claude")
    assert tsr.window_state("s:2")[0] is False, "':2' must not match ':12'"


def test_a_missing_window_is_CREATED_before_the_resume_is_sent(monkeypatch, state, capsys):
    """🔴 THE END-TO-END CONSEQUENCE — the assertion that would have caught it.

    continuum restored one window per session; the plan wanted several. Every
    absent window must be created, and each resume must land on its OWN target.
    On the pre-fix code all sends collapse onto the single existing window.
    """
    fake = _FakeTmux({"scratch3:1": "zsh"})
    monkeypatch.setattr(tsr, "run", fake)
    monkeypatch.setattr(tsr, "no_tmux_server_to_restore_into", lambda: False)
    monkeypatch.setattr(tsr, "wait_for_workspace_to_settle", lambda *a, **k: (True, 0.0))
    monkeypatch.setattr(tsr, "_verify_sends", lambda t, **k: (list(t), []))
    monkeypatch.setattr(tsr, "tmux_session_exists", lambda s: True)
    plan = [{"session": "scratch3", "window": str(w), "cwd": "/tmp",
             "session_id": f"sid-{w}", "codename": "Gold", "bind_source": "ledger"}
            for w in (1, 2, 3, 4)]
    (state).mkdir(parents=True, exist_ok=True)
    tsr.PLAN.write_text(json.dumps(plan))

    assert tsr.cmd_restore() == 0
    capsys.readouterr()
    targets = [t for t, _ in fake.sent]
    assert sorted(targets) == ["scratch3:1", "scratch3:2", "scratch3:3", "scratch3:4"], (
        f"the resumes did not land on four distinct windows: {targets} — on the "
        "pre-fix predicate they all collapse onto scratch3:1")


def test_the_resume_uses_an_ABSOLUTE_claude_path(monkeypatch, state, capsys):
    """🔴 THE SECOND 2026-09-11 FAILURE. Even correct sends died with
    `claude: command not found`: a continuum-restored pane does not re-run the
    login profile, so its PATH can predate the current generation. Resolve the
    binary in THIS process, which systemd starts with a known-good PATH.
    """
    fake = _FakeTmux({"s:1": "zsh"})
    monkeypatch.setattr(tsr, "run", fake)
    monkeypatch.setattr(tsr, "no_tmux_server_to_restore_into", lambda: False)
    monkeypatch.setattr(tsr, "wait_for_workspace_to_settle", lambda *a, **k: (True, 0.0))
    monkeypatch.setattr(tsr, "_verify_sends", lambda t, **k: (list(t), []))
    monkeypatch.setattr(tsr, "tmux_session_exists", lambda s: True)
    # 🔴 RED AT BASE *BEHAVIOURALLY*, not via an AttributeError on a new symbol.
    # A real `claude` on PATH means the pre-fix code still sends the bare name,
    # so this fails on the SEND LINE — the thing that actually broke — rather
    # than on the import surface. (The two tests below this one are new-symbol
    # tests and are red at base with AttributeError; labelled, not counted as
    # behavioural coverage.)
    bindir = state.parent / "fakebin"
    bindir.mkdir(parents=True, exist_ok=True)
    fake_claude = bindir / "claude"
    fake_claude.write_text("#!/bin/sh\nexit 0\n")
    fake_claude.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}:{os.environ.get('PATH','')}")
    (state).mkdir(parents=True, exist_ok=True)
    tsr.PLAN.write_text(json.dumps([{"session": "s", "window": "1", "cwd": "/tmp",
                                     "session_id": "sid", "codename": "Gold",
                                     "bind_source": "ledger"}]))
    assert tsr.cmd_restore() == 0
    capsys.readouterr()
    line = fake.sent[0][1]
    assert f"{fake_claude} --resume sid" in line, (
        f"the send used a bare binary name and would die 'command not found' in a "
        f"pane with a stale PATH: {line!r}")


def test_an_unresolvable_claude_falls_back_to_the_bare_name(monkeypatch):
    """The fallback is load-bearing: a bare `claude` is what shipped for months
    and works wherever PATH is intact. An unresolvable lookup must not turn a
    working restore into no restore."""
    monkeypatch.setattr(tsr.shutil, "which", lambda n: None)
    assert tsr.claude_command() == "claude"


def test_a_richer_generation_is_reported_when_the_pointer_moved_to_a_poorer_plan(
        state, monkeypatch, capsys):
    """🔴 THE THIRD 2026-09-11 FAILURE, and the one that nearly ended recovery
    at 37 of 52. Mid-recovery the 15-minute autosave saw a half-restored
    workspace and repointed the plan at a fresh, POORER generation. A restore
    after that moment recovers the smaller set and reports success.
    """
    state.mkdir(parents=True, exist_ok=True)
    tsr.generations_dir().mkdir(parents=True, exist_ok=True)
    rich = [{"session": "s", "window": str(i), "cwd": "/tmp", "session_id": f"sid-{i}",
             "codename": "Gold", "bind_source": "ledger"} for i in range(6)]
    poor = rich[:2]
    tsr.generation_paths("20260911T000000")[0].write_text(json.dumps(rich))
    tsr.generation_paths("20260911T001000")[0].write_text(json.dumps(poor))
    tsr.PLAN.write_text(json.dumps(poor))

    found = tsr.richer_generation(tsr.PLAN)
    assert found is not None, "a generation with 4 extra bound ids was not reported"
    gplan, missing = found
    assert gplan.name == "restore-plan_20260911T000000.json"
    assert len(missing) == 4, f"expected the 4 lost ids, got {sorted(missing)}"


def test_no_richer_generation_is_reported_when_nothing_was_lost(state):
    """POSITIVE CONTROL for the test above: it must stay quiet in the ordinary
    case, or the warning becomes noise and stops being read."""
    state.mkdir(parents=True, exist_ok=True)
    tsr.generations_dir().mkdir(parents=True, exist_ok=True)
    plan = [{"session": "s", "window": "1", "cwd": "/tmp", "session_id": "sid-1",
             "codename": "Gold", "bind_source": "ledger"}]
    tsr.generation_paths("20260911T000000")[0].write_text(json.dumps(plan))
    tsr.PLAN.write_text(json.dumps(plan))
    assert tsr.richer_generation(tsr.PLAN) is None, (
        "reported a richer generation when the current plan carries every id")
