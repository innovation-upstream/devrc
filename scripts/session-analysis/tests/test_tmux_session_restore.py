"""Unit tests for tmux-session-restore PURE logic (no live tmux / claude / grep).

Run:
  nix-shell -p 'python3.withPackages(p:[p.pytest])' \
      --run 'python -m pytest scripts/session-analysis/tests/test_tmux_session_restore.py -q'

Covers: scratch-slot codename parsing, project-dir encoding, display naming, the
claim-based unique-session assignment (no two windows share a session, uncertain ->
picker), and cheat-sheet rendering. tmux/grep/capture-pane I/O is stubbed.
"""
import importlib.util
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE.parent.parent / "tmux-session-restore.py"
_spec = importlib.util.spec_from_file_location("tmux_session_restore", SCRIPT)
tsr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tsr)


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
# build_plan — claim-based unique assignment
# --------------------------------------------------------------------------- #
def _panes():
    return [
        {"session": "8", "window": "1", "cwd": "/r", "title": "wedge"},
        {"session": "8", "window": "3", "cwd": "/r", "title": "buffer"},
        {"session": "scratch4", "window": "2", "cwd": "/r", "title": "faro"},
    ]


def test_build_plan_assigns_unique_sessions(monkeypatch):
    monkeypatch.setattr(tsr, "live_claude_panes", _panes)
    monkeypatch.setattr(tsr, "codenames", lambda: {"scratch4": "Vapor"})
    monkeypatch.setattr(tsr, "first_user_line", lambda sid, cwd: "")
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
