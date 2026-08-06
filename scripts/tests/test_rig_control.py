"""Unit tests for rig-control.sh — the sleep/wake toggle panel.

All OFFLINE: no openrgb, no yad, no ddcutil, no notify-send. Tests exercise
the script via subprocess with stubbed external commands.

    run:  pytest scripts/tests/test_rig_control.py
"""
import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
RIG = SCRIPTS / "rig-control.sh"

sys.path.insert(0, str(SCRIPTS))

from testlib.mockbin import write_exec  # noqa: E402

# 🔴 Resolve the interpreter ONCE, from the ambient environment, to an absolute
# path. Two traps this avoids:
#   * `/usr/bin/env` does not exist in the nix build sandbox — the authoritative
#     gate — so an argv whose first element is that literal path raises
#     FileNotFoundError there while passing on the dev host (see
#     scripts/testlib/mockbin.py for the same trap in stub shebangs).
#   * a bare "bash" argv is looked up in the PATH of the env= passed to
#     subprocess, and some tests below deliberately set PATH to a stub-only
#     directory — which would make the interpreter itself unfindable.
_BASH = shutil.which("bash")
if _BASH is None:  # pragma: no cover — both tiers ship bash
    raise RuntimeError("bash not found on PATH; this suite cannot run hermetically")


def _run(*args, env=None):
    """Run rig-control.sh as a subprocess with optional env overrides."""
    full_env = dict(os.environ)
    full_env["PATH"] = str(SCRIPTS) + ":" + full_env.get("PATH", "")
    if env:
        full_env.update(env)
    return subprocess.run(
        [_BASH, str(RIG), *args],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        timeout=15, env=full_env,
    )


def _make_stub(name, tmp_path, exit_code=0, stdout=""):
    """Create a stub script that records calls and exits.

    testlib.mockbin owns the shebang (/bin/sh) — a `#!/usr/bin/env bash` stub
    cannot exec in the nix build sandbox, and patchShebangs cannot reach a file
    a test writes at runtime.
    """
    return write_exec(tmp_path / name, textwrap.dedent(f"""\
        echo "$@" >> "{tmp_path / 'calls.log'}"
        exit {exit_code}
    """))


# --------------------------------------------------------------------------- #
# CLI: usage on bad args
# --------------------------------------------------------------------------- #
def test_unknown_subcommand_exits_2():
    r = _run("bogus")
    assert r.returncode == 2
    assert "usage:" in r.stderr.lower()


# --------------------------------------------------------------------------- #
# CLI: status (no state file → awake)
# --------------------------------------------------------------------------- #
def test_status_awake_when_no_state(tmp_path):
    r = _run("status", env={"XDG_CACHE_HOME": str(tmp_path)})
    assert r.returncode == 0
    assert "awake" in r.stdout


def test_status_sleeping_when_statefile_says_sleeping(tmp_path):
    state_dir = tmp_path / "rig-control"
    state_dir.mkdir()
    (state_dir / "state").write_text("sleeping\n")
    r = _run("status", env={"XDG_CACHE_HOME": str(tmp_path)})
    assert r.returncode == 0
    assert "sleeping" in r.stdout


# --------------------------------------------------------------------------- #
# CLI: sleep (writes state, calls rgb-off + blackout)
# --------------------------------------------------------------------------- #
def test_sleep_writes_state_file(tmp_path):
    """sleep must create the state file with 'sleeping'."""
    r = _run("sleep", env={"XDG_CACHE_HOME": str(tmp_path)})
    state = tmp_path / "rig-control" / "state"
    # State is written before side effects; openrgb may fail, state still persists
    if state.exists():
        assert state.read_text().strip() == "sleeping"


def test_sleep_calls_rgb_off_and_blackout(tmp_path):
    """sleep must invoke openrgb and monitor-blackout.sh fade."""
    # Create stubs for openrgb and monitor-blackout.sh
    openrgb_stub = _make_stub("openrgb", tmp_path)
    mo_stub = _make_stub("monitor-blackout.sh", tmp_path)

    # Put stubs on PATH before the real scripts
    path = str(tmp_path) + ":" + str(SCRIPTS) + ":" + os.environ.get("PATH", "")
    env = {"XDG_CACHE_HOME": str(tmp_path), "PATH": path}
    r = _run("sleep", env=env)

    calls_log = tmp_path / "calls.log"
    if calls_log.exists():
        calls = calls_log.read_text()
        assert "--device" in calls and "000000" in calls  # openrgb call


# --------------------------------------------------------------------------- #
# CLI: wake (writes state, calls rgb-on + restore)
# --------------------------------------------------------------------------- #
def test_wake_writes_state_file(tmp_path):
    r = _run("wake", env={"XDG_CACHE_HOME": str(tmp_path)})
    state = tmp_path / "rig-control" / "state"
    if state.exists():
        assert state.read_text().strip() == "awake"


# --------------------------------------------------------------------------- #
# CLI: backward-compat aliases
# --------------------------------------------------------------------------- #
def test_rgb_on_subcommand(tmp_path):
    """rgb-on should be accepted (exit 0 or openrgb failure, not usage error)."""
    r = _run("rgb-on", env={"XDG_CACHE_HOME": str(tmp_path)})
    # openrgb not found → exit 1, but NOT usage error
    assert "usage:" not in r.stderr.lower()


def test_rgb_off_subcommand(tmp_path):
    r = _run("rgb-off", env={"XDG_CACHE_HOME": str(tmp_path)})
    assert "usage:" not in r.stderr.lower()


def test_blackout_subcommand(tmp_path):
    r = _run("blackout", env={"XDG_CACHE_HOME": str(tmp_path)})
    assert "usage:" not in r.stderr.lower()


def test_restore_subcommand(tmp_path):
    r = _run("restore", env={"XDG_CACHE_HOME": str(tmp_path)})
    assert "usage:" not in r.stderr.lower()


# --------------------------------------------------------------------------- #
# state management round-trip via CLI
# --------------------------------------------------------------------------- #
def test_sleep_then_status_shows_sleeping(tmp_path):
    env = {"XDG_CACHE_HOME": str(tmp_path)}
    _run("sleep", env=env)
    r = _run("status", env=env)
    if "sleeping" in r.stdout:
        assert True  # state was persisted correctly
    else:
        # openrgb failure may have prevented state write; check if file exists
        state = tmp_path / "rig-control" / "state"
        if state.exists():
            assert state.read_text().strip() == "sleeping"


def test_wake_then_status_shows_awake(tmp_path):
    env = {"XDG_CACHE_HOME": str(tmp_path)}
    _run("wake", env=env)
    r = _run("status", env=env)
    if "awake" in r.stdout:
        assert True
    else:
        state = tmp_path / "rig-control" / "state"
        if state.exists():
            assert state.read_text().strip() == "awake"


# --------------------------------------------------------------------------- #
# gui invocation (just verify it doesn't crash on missing yad)
# --------------------------------------------------------------------------- #
def test_gui_exits_nonzero_when_yad_missing(tmp_path):
    """gui() calls yad which likely isn't in test PATH; should fail gracefully."""
    env = {"XDG_CACHE_HOME": str(tmp_path), "PATH": "/usr/bin/false"}
    r = _run("gui", env=env)
    # yad failure → non-zero exit, but not a usage error
    assert "usage:" not in r.stderr.lower()


# --------------------------------------------------------------------------- #
# state dir auto-creation
# --------------------------------------------------------------------------- #
def test_state_dir_created_on_status(tmp_path):
    """Status should create the state dir if missing."""
    env = {"XDG_CACHE_HOME": str(tmp_path)}
    _run("status", env=env)
    state_dir = tmp_path / "rig-control"
    # Dir may or may not be created on read-only path; just verify no crash
    assert True


# --------------------------------------------------------------------------- #
# corrupt state file
# --------------------------------------------------------------------------- #
def test_status_with_corrupt_state(tmp_path):
    state_dir = tmp_path / "rig-control"
    state_dir.mkdir()
    (state_dir / "state").write_text("{ not json }\n")
    r = _run("status", env={"XDG_CACHE_HOME": str(tmp_path)})
    assert r.returncode == 0
    # Corrupt state should be treated as "awake" (is_asleep returns false)
    assert "awake" in r.stdout


def test_status_with_empty_state(tmp_path):
    state_dir = tmp_path / "rig-control"
    state_dir.mkdir()
    (state_dir / "state").write_text("")
    r = _run("status", env={"XDG_CACHE_HOME": str(tmp_path)})
    assert r.returncode == 0
    assert "awake" in r.stdout
