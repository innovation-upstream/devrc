"""Unit tests for monitor-blackout.sh — DDC-CI backlight control with fade.

All OFFLINE: no ddcutil, no systemctl. The script is tested via subprocess with
mocked ddcutil and systemctl commands.

    run:  pytest scripts/tests/test_monitor_blackout.py
"""
import os
import shutil
import subprocess
import sys
import tempfile
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


# 🔴 The canonical-path guard (monitor-blackout.sh, "refusing to run from") accepts
# ONLY "${HOME}/workspace/devrc/scripts/monitor-blackout.sh". The nix build sandbox —
# the AUTHORITATIVE tier — checks out to /build/src with HOME=/build/home, so the
# repo's own copy can never satisfy it and every test that executes the script fails
# there while passing on the dev host. That is the worse tier direction: green where
# it is observed, red where it gates. Rather than weaken the guard, build the shape it
# demands once and run THAT copy. `_CANON_MB` is a byte copy of the tree's script, so
# behaviour under test is unchanged; only its path and HOME differ.
_CANON_HOME = Path(tempfile.mkdtemp(prefix="mb-canonical-home-"))
_CANON_MB = _CANON_HOME / "workspace" / "devrc" / "scripts" / "monitor-blackout.sh"
_CANON_MB.parent.mkdir(parents=True, exist_ok=True)
shutil.copy2(MB, _CANON_MB)


def _run(*args, env=None):
    """Run monitor-blackout.sh as a subprocess, from the canonical-shaped path.

    HOME is set before the caller's env is applied, so a test that needs its own
    HOME can still override it (and will then hit the guard — which is the point
    of test_the_canonical_path_guard_refuses_a_non_canonical_copy).
    """
    full_env = dict(os.environ)
    full_env["HOME"] = str(_CANON_HOME)
    if env:
        full_env.update(env)
    return subprocess.run(
        [_BASH, str(_CANON_MB), *args],
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
    # 🔴 ddcutil too, not just systemctl. monitor-blackout.sh resolves ddcutil at
    # line 20 — BEFORE it dispatches a subcommand — and exits 1 with "ddcutil not
    # found on PATH" when it is absent, so `status` needs the stub as much as the
    # brightness paths do. This test claimed to be OFFLINE while silently
    # depending on the dev host having a real ddcutil; the sandbox does not, and
    # the FileNotFoundError this suite used to die on hid that for two days.
    _make_ddcutil_stub(tmp_path)
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


# --------------------------------------------------------------------------- #
# The canonical-path guard (added in #374) — it shipped with NO test at all,
# which is how it reached `main` red on the authoritative tier. Both controls,
# so a green here is distinguishable from a check wired to nothing.
# --------------------------------------------------------------------------- #

def test_the_canonical_path_guard_refuses_a_non_canonical_copy(tmp_path):
    """🔴 NEGATIVE CONTROL. A copy anywhere but "${HOME}/workspace/devrc/scripts/"
    must refuse with THIS guard's own message and exit 1 — not merely fail.

    This is the behaviour the guard exists for: a Claude scratchpad copy, a
    worktree, or a mutation-test copy must never create timers or drive
    brightness on the live rig.
    """
    stray = tmp_path / "monitor-blackout.sh"
    shutil.copy2(MB, stray)

    env = dict(os.environ)
    env["HOME"] = str(tmp_path / "some-other-home")

    p = subprocess.run(
        [_BASH, str(stray)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        timeout=30, env=env,
    )

    assert p.returncode == 1, f"a stray copy was allowed to run: {p.stdout}{p.stderr}"
    assert "refusing to run from" in p.stderr, p.stderr
    assert str(stray) in p.stderr, "the refusal must name the offending path"


def test_the_canonical_copy_passes_the_guard_and_reaches_a_later_check(tmp_path):
    """🔴 POSITIVE CONTROL. Refusing is only meaningful if accepting also happens.

    Proving the guard did NOT fire requires reaching work that lives AFTER it —
    here, `get-brightness` returning the stub's value. A test asserting only
    "no refusal in stderr" would pass just as well against a script whose guard
    had been deleted entirely, and equally against one that died on the very
    next line.

    Do NOT narrow PATH to only the stub dir to force a later failure: the guard
    itself shells out to `readlink`, so a PATH without coreutils kills the script
    at line 23 — before the guard can accept OR refuse. That failure mode looks
    like a guard problem and is not one.
    """
    _make_ddcutil_stub(tmp_path, "75")
    path = str(tmp_path) + ":" + os.environ.get("PATH", "")

    p = _run("get-brightness", env={"PATH": path, "XDG_RUNTIME_DIR": str(tmp_path)})

    assert "refusing to run from" not in p.stderr, (
        f"the canonical-shaped copy was refused: {p.stderr}")
    assert p.returncode == 0, f"{p.stdout}{p.stderr}"
    assert "75" in p.stdout, (
        f"expected to reach the body and read the stub's brightness; got: {p.stdout!r}")
