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
import sys
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
