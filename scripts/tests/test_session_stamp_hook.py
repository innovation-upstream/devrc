#!/usr/bin/env python3
"""`scripts/claude-hooks/session-stamp.py` — the RECORDING half.

🔴 WHY THIS FILE EXISTS. The round-1 audit found this half had **zero** test
coverage: `test_session_stamp_seam.py` exercised the git hook and the installer,
and `test_session_trailer.py` exercised the library, but nothing ran the hook
that actually writes the state. That is the isolation-seam rule applied to the
half the author did not cover — and a real defect was living in exactly that gap
(the hook resolved a git dir from its own cwd, so a commit into ANOTHER repo
recorded state against the wrong one).

🔴 FAIL-OPEN IS THE WHOLE CONTRACT. A PreToolUse hook that raises, denies or
hangs blocks the agent's Bash call. Every test here asserts exit 0 and no
stdout; the recording is a side effect, never a verdict.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
HOOK = REPO / "scripts" / "claude-hooks" / "session-stamp.py"

UUID = "d8c216f2-b51d-4c2c-a559-5a5ab4163848"


def run_hook(payload, root, extra_env=None):
    env = dict(os.environ)
    env["DEVRC_SESSION_TRAILER_ROOT"] = str(root)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps(payload), capture_output=True, text=True, env=env,
    )


def payload(command, session_id=UUID, tool="Bash"):
    return {"tool_name": tool, "tool_input": {"command": command},
            "session_id": session_id, "cwd": str(REPO)}


def recorded(root):
    d = Path(root)
    if not d.exists():
        return {}
    out = {}
    for f in d.glob("*.json"):
        out[f.stem] = json.loads(f.read_text())
    return out


class TestTheTrigger:
    """Which commands cause a record. Over-triggering costs a fork per call;
    under-triggering costs a trailer."""

    @pytest.mark.parametrize("command", [
        "git commit -m x",
        "git commit",
        'git -C /some/path commit -m "a message"',      # the form CLAUDE.md mandates
        "git -c user.name=x commit -m y",
        "git commit --amend --no-edit",
    ])
    def test_commit_shaped_commands_trigger(self, command):
        import importlib.util
        spec = importlib.util.spec_from_file_location("session_stamp", HOOK)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        assert mod.looks_like_commit(command) is True

    @pytest.mark.parametrize("command", [
        "ls -la",
        "git log --oneline -5",
        "git status",
        "echo 'we should commit to this plan'",   # the WORD, not the command
        "",
    ])
    def test_non_commit_commands_do_not_trigger(self, command):
        import importlib.util
        spec = importlib.util.spec_from_file_location("session_stamp", HOOK)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        assert mod.looks_like_commit(command) is False

    def test_the_pattern_is_linear_not_exponential(self):
        """🔴 A PreToolUse hang blocks the agent's Bash call. The first pattern
        nested a quantifier over an OPTIONAL group, which made token
        partitioning ambiguous and backtracked super-linearly (~1.65x per added
        dash-token). This asserts the cost stays flat on the adversarial input.
        """
        import importlib.util
        import time
        spec = importlib.util.spec_from_file_location("session_stamp", HOOK)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        hostile = "git " + "-x " * 60 + "nope"
        start = time.monotonic()
        mod.looks_like_commit(hostile)
        assert time.monotonic() - start < 1.0


class TestRecording:
    def test_a_commit_command_records_the_session_id(self, tmp_path):
        out = run_hook(payload("git commit -m x"), tmp_path)
        assert out.returncode == 0
        assert out.stdout == ""
        got = recorded(tmp_path)
        assert len(got) == 1
        assert next(iter(got.values()))["session_id"] == UUID

    def test_a_non_commit_command_records_nothing(self, tmp_path):
        out = run_hook(payload("git status"), tmp_path)
        assert out.returncode == 0
        assert recorded(tmp_path) == {}

    def test_a_non_Bash_tool_records_nothing(self, tmp_path):
        out = run_hook(payload("git commit -m x", tool="Read"), tmp_path)
        assert out.returncode == 0
        assert recorded(tmp_path) == {}

    def test_an_id_that_could_corrupt_a_message_is_refused(self, tmp_path):
        out = run_hook(payload("git commit -m x", session_id="bad\nid"), tmp_path)
        assert out.returncode == 0
        assert recorded(tmp_path) == {}

    def test_a_non_uuid_id_is_recorded_verbatim(self, tmp_path):
        """Opaque-string discipline, end to end through the hook."""
        out = run_hook(payload("git commit -m x", session_id="ses_01ABC"), tmp_path)
        assert out.returncode == 0
        assert next(iter(recorded(tmp_path).values()))["session_id"] == "ses_01ABC"

    def test_it_records_a_starttime_so_a_recycled_pid_cannot_inherit_it(self, tmp_path):
        """🔴 pid_max here is 4194304 and live pids already span the range, so
        recycling is routine. Without this field `lookup()` cannot tell a
        recycled pid from the original."""
        run_hook(payload("git commit -m x"), tmp_path)
        rec = next(iter(recorded(tmp_path).values()))
        assert isinstance(rec.get("starttime"), int)

    def test_the_state_file_is_not_world_readable(self, tmp_path):
        """It names a session and may carry a transcript path."""
        run_hook(payload("git commit -m x"), tmp_path)
        f = next(Path(tmp_path).glob("*.json"))
        assert oct(f.stat().st_mode)[-3:] == "600"


class TestFailOpen:
    @pytest.mark.parametrize("bad", ["", "not json at all", "[]", "null"])
    def test_malformed_stdin_exits_0_silently(self, bad, tmp_path):
        env = dict(os.environ)
        env["DEVRC_SESSION_TRAILER_ROOT"] = str(tmp_path)
        out = subprocess.run([sys.executable, str(HOOK)], input=bad,
                             capture_output=True, text=True, env=env)
        assert out.returncode == 0
        assert out.stdout == ""

    def test_an_unwritable_state_root_exits_0(self, tmp_path):
        ro = tmp_path / "ro"
        ro.mkdir()
        ro.chmod(0o500)
        try:
            out = run_hook(payload("git commit -m x"), ro / "nested")
            assert out.returncode == 0
        finally:
            ro.chmod(0o700)

    def test_it_never_emits_a_permission_decision(self, tmp_path):
        """This hook must not be able to DENY a Bash call — it has no opinion."""
        out = run_hook(payload("git commit -m x"), tmp_path)
        assert "permissionDecision" not in out.stdout
        assert out.stdout.strip() == ""
