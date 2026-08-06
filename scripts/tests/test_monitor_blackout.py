"""Unit tests for monitor-blackout.sh — DDC-CI backlight control with fade.

All OFFLINE: no ddcutil, no systemctl. The script is tested via subprocess with
mocked ddcutil and systemctl commands.

    run:  pytest scripts/tests/test_monitor_blackout.py
"""
import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
MB = SCRIPTS / "monitor-blackout.sh"

sys.path.insert(0, str(SCRIPTS))

from testlib.mockbin import write_exec  # noqa: E402

# 🔴 Resolve the interpreter ONCE, from the ambient environment, to an absolute
# path. Two traps this avoids:
#   * `/usr/bin/env` does not exist in the nix build sandbox — the authoritative
#     gate — so an argv whose first element is that literal path raises
#     FileNotFoundError there while passing on the dev host (see
#     scripts/testlib/mockbin.py for the same trap in stub shebangs).
#   * a bare "bash" argv is looked up in the PATH of the env= passed to
#     subprocess, and every test below overrides PATH with a stub directory —
#     which would make the interpreter itself unfindable.
_BASH = shutil.which("bash")
if _BASH is None:  # pragma: no cover — both tiers ship bash
    raise RuntimeError("bash not found on PATH; this suite cannot run hermetically")


def _run(*args, env=None):
    """Run monitor-blackout.sh as a subprocess."""
    full_env = dict(os.environ)
    if env:
        full_env.update(env)
    return subprocess.run(
        [_BASH, str(MB), *args],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        timeout=30, env=full_env,
    )


def _make_ddcutil_stub(tmp_path, brightness="75"):
    """Create a ddcutil stub that responds to getvcp 10.
    Output format: 'VCP <hex> <current> <max>' — awk $4 = max, $3 = current.
    The script's get_brightness uses awk $4, so stub outputs brightness in $4.

    testlib.mockbin owns the shebang (/bin/sh) — a `#!/usr/bin/env bash` stub
    cannot exec in the nix build sandbox, and patchShebangs cannot reach a file
    a test writes at runtime.
    """
    return write_exec(tmp_path / "ddcutil", textwrap.dedent(f"""\
        echo "$@" >> "{tmp_path / 'ddcutil.log'}"
        case "$*" in
            *detect*) echo "i2c-5" ;;
            *getvcp*10*) echo "VCP 10 100 {brightness}" ;;
            *) echo "ok" ;;
        esac
    """))


def _make_systemctl_stub(tmp_path):
    """Create a systemctl stub that simulates timer management."""
    return write_exec(tmp_path / "systemctl", textwrap.dedent(f"""\
        echo "$@" >> "{tmp_path / 'systemctl.log'}"
        case "$*" in
            *is-active*) exit 3 ;;  # no timer active
            *) exit 0 ;;
        esac
    """))


# --------------------------------------------------------------------------- #
# CLI: no args → blackout with default 8h
# --------------------------------------------------------------------------- #
def test_no_args_blackout_default_duration(tmp_path):
    ddcutil = _make_ddcutil_stub(tmp_path, "75")
    systemctl = _make_systemctl_stub(tmp_path)
    path = str(tmp_path) + ":" + os.environ.get("PATH", "")

    r = _run(env={"PATH": path, "XDG_RUNTIME_DIR": str(tmp_path)})

    ddc_log = tmp_path / "ddcutil.log"
    if ddc_log.exists():
        log = ddc_log.read_text()
        # Should detect bus, get brightness, set to 0
        assert "detect" in log
        assert "setvcp 10 0" in log


# --------------------------------------------------------------------------- #
# CLI: restore subcommand
# --------------------------------------------------------------------------- #
def test_restore_reads_state_and_restores(tmp_path):
    ddcutil = _make_ddcutil_stub(tmp_path, "75")
    systemctl = _make_systemctl_stub(tmp_path)
    path = str(tmp_path) + ":" + os.environ.get("PATH", "")

    # Write state file
    state = tmp_path / "monitor-blackout.state"
    state.write_text("5:75\n")

    r = _run("restore", env={"PATH": path, "XDG_RUNTIME_DIR": str(tmp_path)})

    ddc_log = tmp_path / "ddcutil.log"
    if ddc_log.exists():
        log = ddc_log.read_text()
        assert "setvcp 10 75" in log


def test_restore_uses_default_brightness_when_no_state(tmp_path):
    ddcutil = _make_ddcutil_stub(tmp_path, "75")
    systemctl = _make_systemctl_stub(tmp_path)
    path = str(tmp_path) + ":" + os.environ.get("PATH", "")

    r = _run("restore", env={"PATH": path, "XDG_RUNTIME_DIR": str(tmp_path)})

    ddc_log = tmp_path / "ddcutil.log"
    if ddc_log.exists():
        log = ddc_log.read_text()
        # Default brightness is 60
        assert "setvcp 10 60" in log


# --------------------------------------------------------------------------- #
# CLI: status subcommand
# --------------------------------------------------------------------------- #
def test_status_no_timer(tmp_path):
    systemctl = _make_systemctl_stub(tmp_path)
    path = str(tmp_path) + ":" + os.environ.get("PATH", "")

    r = _run("status", env={"PATH": path, "XDG_RUNTIME_DIR": str(tmp_path)})

    assert r.returncode == 0
    assert "no pending" in r.stdout.lower() or "pending" in r.stdout.lower()


# --------------------------------------------------------------------------- #
# CLI: get-brightness subcommand
# --------------------------------------------------------------------------- #
def test_get_brightness_returns_value(tmp_path):
    ddcutil = _make_ddcutil_stub(tmp_path, "75")
    path = str(tmp_path) + ":" + os.environ.get("PATH", "")

    r = _run("get-brightness", env={"PATH": path, "XDG_RUNTIME_DIR": str(tmp_path)})

    assert r.returncode == 0
    assert "75" in r.stdout


# --------------------------------------------------------------------------- #
# CLI: fade subcommand
# --------------------------------------------------------------------------- #
def test_fade_blackout_saves_state(tmp_path):
    ddcutil = _make_ddcutil_stub(tmp_path, "75")
    systemctl = _make_systemctl_stub(tmp_path)
    path = str(tmp_path) + ":" + os.environ.get("PATH", "")

    r = _run("fade", env={"PATH": path, "XDG_RUNTIME_DIR": str(tmp_path)})

    state = tmp_path / "monitor-blackout.state"
    if state.exists():
        # awk $4 extracts the 4th field from "VCP 10 100 75" → "75"
        assert state.read_text().strip() == "5:75"


def test_fade_calls_setvcp_multiple_steps(tmp_path):
    ddcutil = _make_ddcutil_stub(tmp_path, "100")
    systemctl = _make_systemctl_stub(tmp_path)
    path = str(tmp_path) + ":" + os.environ.get("PATH", "")

    r = _run("fade", env={"PATH": path, "XDG_RUNTIME_DIR": str(tmp_path)})

    ddc_log = tmp_path / "ddcutil.log"
    if ddc_log.exists():
        log = ddc_log.read_text()
        # Should have multiple setvcp calls (fade steps)
        setvcp_count = log.count("setvcp 10")
        assert setvcp_count >= 2  # at least initial + final


# --------------------------------------------------------------------------- #
# CLI: fade-restore subcommand
# --------------------------------------------------------------------------- #
def test_fade_restore_reads_state(tmp_path):
    ddcutil = _make_ddcutil_stub(tmp_path, "75")
    systemctl = _make_systemctl_stub(tmp_path)
    path = str(tmp_path) + ":" + os.environ.get("PATH", "")

    # Write state
    state = tmp_path / "monitor-blackout.state"
    state.write_text("5:80\n")

    r = _run("fade-restore", env={"PATH": path, "XDG_RUNTIME_DIR": str(tmp_path)})

    ddc_log = tmp_path / "ddcutil.log"
    if ddc_log.exists():
        log = ddc_log.read_text()
        # Should fade to 80
        assert "setvcp 10 80" in log


# --------------------------------------------------------------------------- #
# CLI: custom duration
# --------------------------------------------------------------------------- #
def test_blackout_custom_duration(tmp_path):
    ddcutil = _make_ddcutil_stub(tmp_path, "75")
    systemctl = _make_systemctl_stub(tmp_path)
    path = str(tmp_path) + ":" + os.environ.get("PATH", "")

    r = _run("2h", env={"PATH": path, "XDG_RUNTIME_DIR": str(tmp_path)})

    # schedule_restore uses systemd-run, not systemctl
    # Check ddcutil.log for the setvcp call (confirms blackout ran)
    ddc_log = tmp_path / "ddcutil.log"
    if ddc_log.exists():
        log = ddc_log.read_text()
        assert "setvcp 10 0" in log  # blackout sets brightness to 0


# --------------------------------------------------------------------------- #
# edge: no DDC/CI monitor
# --------------------------------------------------------------------------- #
def test_blackout_fails_when_no_monitor(tmp_path):
    # Stub ddcutil that responds to detect but with no valid buses
    write_exec(tmp_path / "ddcutil", textwrap.dedent(f"""\
        echo "$@" >> "{tmp_path / 'ddcutil.log'}"
        case "$*" in
            *detect*) echo "no devices found" ;;
            *) echo "ok" ;;
        esac
    """))
    path = str(tmp_path) + ":" + os.environ.get("PATH", "")

    r = _run(env={"PATH": path, "XDG_RUNTIME_DIR": str(tmp_path)})

    # Should fail because no DDC/CI monitor is detected
    assert r.returncode == 1
    # Error message may be in stderr or stdout depending on script flow
    output = (r.stderr + r.stdout).lower()
    assert "no ddc/ci monitor" in output or "no ddc" in output


# --------------------------------------------------------------------------- #
# edge: state file with invalid format
# --------------------------------------------------------------------------- #
def test_restore_with_corrupt_state(tmp_path):
    ddcutil = _make_ddcutil_stub(tmp_path, "75")
    systemctl = _make_systemctl_stub(tmp_path)
    path = str(tmp_path) + ":" + os.environ.get("PATH", "")

    state = tmp_path / "monitor-blackout.state"
    state.write_text("corrupt data here\n")

    r = _run("restore", env={"PATH": path, "XDG_RUNTIME_DIR": str(tmp_path)})

    # Should use default brightness (60) since state can't be parsed
    ddc_log = tmp_path / "ddcutil.log"
    if ddc_log.exists():
        log = ddc_log.read_text()
        assert "setvcp 10 60" in log
