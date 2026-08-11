"""🔴 The guard: no test in this suite may reach a REAL host launcher.

This file exists because a GREEN suite was firing real desktop toasts and
creating real transient systemd timers on the operator's machine — 15 launches
in 29 passing tests, measured on the dev host at a67f795. The nix build sandbox
that gates merges has none of those binaries, so it can never observe the
class; these tests are written to fail in BOTH tiers.

What is asserted, and why each is not enough alone:

  * RESOLUTION  — `which(<launcher>)` lands inside the stub dir. On the dev host
    that IS the behavioural claim (a real binary exists to be shadowed); in the
    sandbox it degenerates to "a stub exists", which is why the others follow.
  * ORDERING    — the stub dir is PATH[0]. "On PATH" is not the property that
    matters: an entry AFTER the ambient dirs shadows nothing.
  * BEHAVIOUR   — invoking a launcher through a shell RECORDS to the stub log
    and exits 0.
  * THE SEAM    — the two real hazard paths (monitor-blackout's `systemd-run`
    scheduling, rig-control's `openrgb` + `notify-send`) land in the stub log.
    A component-scoped check would pass with the fixture deleted as long as
    some stub file existed somewhere; these two pin the RELATIONSHIP between
    the scripts under test and the fixture.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

from testlib import nolaunch  # noqa: E402
from testlib.mockbin import write_exec  # noqa: E402

# Same reasoning as the other suites here: resolve the interpreter once, to an
# absolute path, because `/usr/bin/env` does not exist in the nix sandbox and a
# bare "bash" would be looked up in the child's (stub-only) PATH.
_BASH = shutil.which("bash")
if _BASH is None:  # pragma: no cover — both tiers ship bash
    raise RuntimeError("bash not found on PATH; this suite cannot run hermetically")
_SH = "/bin/sh"


# --------------------------------------------------------------------------- #
# The ledger
# --------------------------------------------------------------------------- #
def test_the_stubbed_launcher_set_is_pinned():
    """Fails when the set GROWS **or** SHRINKS — both must be deliberate.

    Shrinking is the dangerous direction (a launcher silently becomes reachable
    again); growing is welcome, and updating this list is the acknowledgement.
    `systemctl` is deliberately absent — see nolaunch.py for the measurement
    and the reason.
    """
    assert set(nolaunch.HOST_LAUNCHERS) == {
        "systemd-run", "notify-send", "dunstify", "openrgb", "ddcutil",
        "xdg-open", "i3-msg", "xdotool", "espanso",
    }
    assert "systemctl" not in nolaunch.HOST_LAUNCHERS


# --------------------------------------------------------------------------- #
# Resolution + ordering
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("launcher", nolaunch.HOST_LAUNCHERS)
def test_every_launcher_resolves_into_the_stub_dir(launcher, no_real_launchers):
    """`which` must find the STUB, never the real binary.

    Not `which(...) is not None` — that is satisfied by the real thing. The
    assertion is on the resolved PARENT directory, which is what a real binary
    earlier on PATH would break.
    """
    resolved = shutil.which(launcher)
    assert resolved is not None, (
        f"{launcher} resolves to nothing — the stub dir is not on PATH, so a "
        f"host that HAS {launcher} would reach the real one")
    assert Path(resolved).parent == no_real_launchers, (
        f"{launcher} resolves to {resolved}, outside the stub dir "
        f"{no_real_launchers} — a real binary is winning the PATH lookup")


def test_the_stub_dir_is_first_on_path(no_real_launchers):
    """FIRST, not merely present.

    This is the assertion the sandbox tier can still make: there, no real
    launcher exists to shadow, so a stub dir appended to the END of PATH would
    satisfy every resolution check above while providing zero protection on the
    dev host — where the real binaries live in `/run/current-system/sw/bin` and
    `~/.nix-profile/bin`.
    """
    entries = os.environ["PATH"].split(os.pathsep)
    assert entries[0] == str(no_real_launchers), (
        "the stub dir must be the FIRST PATH entry; PATH starts with "
        f"{entries[:3]}")


# --------------------------------------------------------------------------- #
# Behaviour
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("launcher", nolaunch.HOST_LAUNCHERS)
def test_invoking_a_launcher_records_and_launches_nothing(launcher, no_real_launchers):
    """A shell — the way every script under test invokes these — reaches the stub.

    The recorded line is the positive control: a log that never moves is
    indistinguishable from a harness wired to nothing.
    """
    before = len(nolaunch.recorded(no_real_launchers))
    marker = f"--guard-probe-{launcher}"
    p = subprocess.run(
        [_SH, "-c", f"{launcher} {marker}"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        timeout=15, env=dict(os.environ),
    )
    assert p.returncode == 0, (
        f"{launcher} stub exited {p.returncode}: {p.stdout}{p.stderr}")

    lines = nolaunch.recorded(no_real_launchers)
    assert len(lines) == before + 1, (
        f"expected exactly one new recorded launch, got {lines[before:]}")
    assert lines[-1] == f"{launcher} {marker}", lines[-1]


def test_the_stubs_exit_zero_because_a_failing_stub_would_change_the_script(
        no_real_launchers):
    """Exit status 0 is part of the contract, not an accident.

    monitor-blackout.sh and rig-control.sh run under `set -e` and treat these
    launchers as fire-and-forget. A stub that exited non-zero would abort
    `blackout()` at the `systemd-run` line, so every later assertion in
    test_monitor_blackout.py would be measuring the STUB instead of the script.
    """
    for launcher in nolaunch.HOST_LAUNCHERS:
        p = subprocess.run(
            [str(no_real_launchers / launcher), "probe"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=15,
            env=dict(os.environ))
        assert p.returncode == 0, f"{launcher} stub exited {p.returncode}"


# --------------------------------------------------------------------------- #
# The seam: the REAL hazard paths must land in the stub
# --------------------------------------------------------------------------- #
def _canonical_copy(tmp_path, script_name):
    """Copy `scripts/<script_name>` to `<tmp>/workspace/devrc/scripts/`.

    monitor-blackout.sh refuses to run from anywhere but
    `${HOME}/workspace/devrc/scripts/monitor-blackout.sh` (#374), and this
    suite must exercise the path that is ACCEPTED — that is where the timer
    creation lives. Same construction as test_monitor_blackout.py.
    """
    home = tmp_path / "canon-home"
    dest = home / "workspace" / "devrc" / "scripts" / script_name
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(SCRIPTS / script_name, dest)
    return home, dest


def test_monitor_blackout_scheduling_reaches_the_stub_not_systemd(
        tmp_path, no_real_launchers):
    """🔴 The exact launch that created 8h timers on the operator's machine.

    Fails in BOTH tiers if the fixture is removed: on the dev host the real
    `systemd-run` would take the call (and create the timer) leaving the log
    empty; in the sandbox there is no `systemd-run` at all, so the script dies
    and the log is empty just the same.
    """
    home, canon = _canonical_copy(tmp_path, "monitor-blackout.sh")
    # This test's OWN stubs, in tmp_path, which sits before the session stub dir
    # in the child PATH — the two mechanisms compose, they do not fight.
    write_exec(tmp_path / "ddcutil", textwrap.dedent("""\
        case "$*" in
            *detect*) echo "i2c-5" ;;
            *getvcp*10*) echo "VCP 10 100 75" ;;
            *) echo "ok" ;;
        esac
        exit 0
    """))
    write_exec(tmp_path / "systemctl", 'exit 0\n')

    before = len(nolaunch.recorded(no_real_launchers))
    env = dict(os.environ)
    env["HOME"] = str(home)
    env["PATH"] = str(tmp_path) + os.pathsep + env["PATH"]
    env["XDG_RUNTIME_DIR"] = str(tmp_path)
    p = subprocess.run([_BASH, str(canon), "2h"],
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                       timeout=60, env=env)
    assert p.returncode == 0, f"{p.stdout}{p.stderr}"

    new = nolaunch.recorded(no_real_launchers)[before:]
    scheduled = [ln for ln in new if ln.startswith("systemd-run ")]
    assert len(scheduled) == 1, (
        f"expected monitor-blackout.sh's schedule_restore to hit the stub; "
        f"recorded: {new}")
    assert "--unit=monitor-blackout-restore-v2" in scheduled[0], scheduled[0]
    assert "--on-active=2h" in scheduled[0], scheduled[0]


def test_rig_control_notify_and_rgb_reach_the_stub(tmp_path, no_real_launchers):
    """rig-control's other two real launches: `openrgb` and the toast.

    `rgb-off` is the shortest path that fires both, and it needs no monitor
    stubbing at all — which is the point: nothing in this test asks for
    protection, it inherits it.
    """
    before = len(nolaunch.recorded(no_real_launchers))
    env = dict(os.environ)
    env["XDG_CACHE_HOME"] = str(tmp_path)
    p = subprocess.run([_BASH, str(SCRIPTS / "rig-control.sh"), "rgb-off"],
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                       timeout=30, env=env)
    assert p.returncode == 0, f"{p.stdout}{p.stderr}"

    new = nolaunch.recorded(no_real_launchers)[before:]
    assert any(ln.startswith("openrgb ") and "000000" in ln for ln in new), new
    assert any(ln.startswith("notify-send ") and "Chassis RGB off" in ln
               for ln in new), new
