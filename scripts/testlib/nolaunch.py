#!/usr/bin/env python3
"""Record-only stubs for the binaries that would DO SOMETHING TO THE HOST.

🔴 THE HAZARD (MEASURED 2026-08-11 on the workbench, at a67f795)
----------------------------------------------------------------
`scripts/tests/test_rig_control.py` + `scripts/tests/test_monitor_blackout.py`
build the child PATH as `str(tmp_path) + ":" + os.environ["PATH"]` and stub only
the binaries they ASSERT on (`ddcutil`, `systemctl`, `openrgb`,
`monitor-blackout.sh`). Everything else fell through to the REAL binary
inherited from the operator's session. With a recording interceptor first on
PATH, those two files fired **15 real launches** in 29 passing tests:

    systemd-run --user --unit=monitor-blackout-restore-v2 --on-active=8h …   x3
    systemd-run --user --unit=monitor-blackout-restore-v2 --on-active=2h …   x1
    openrgb --device 2 --mode static --color 000000                          x3
    openrgb --device 2 --mode static --color FFFFFF                          x3
    notify-send -t 2500 rig-control …                                        x5

i.e. running the test suite created REAL transient systemd timers with a FIXED
unit name, scheduled 2-8 hours into the future, fired REAL desktop toasts, and
drove the chassis RGB. Worse in the base clone than in a worktree, MEASURED
separately at 22 launches: from `~/workspace/devrc` (where githooks/pre-push
runs the suite) monitor-blackout's canonical-path guard ACCEPTS, so seven
`ddcutil detect --brief` calls reach the real DDC/CI panel too. That reach is
the measurement; `setvcp 10 0` after it is what the script does next, NOT
something observed — the panel was never actually driven, because the
measurement's own ddcutil stub answered first.

🔴 WHY NO GATE SAW IT
---------------------
`nix build .#checks.x86_64-linux.pytests` runs in a sandbox with none of these
binaries and no session bus, so the suite is green there no matter what. The
hazard is structurally invisible in the tier that gates merges and only exists
in the tier a human runs. That is the two-tier hazard from claude/RULES.md, and
it is why the fix is a stub dir rather than "be careful in these two files".

WHAT THIS MODULE DOES
---------------------
`install()` writes one record-only stub per launcher into a directory, using
`testlib.mockbin.write_exec` so the shebang execs in BOTH tiers. A caller
(`scripts/tests/conftest.py`) prepends that directory to `os.environ["PATH"]`
for the whole session, so a test that inherits the ambient PATH — which is all
of them — can no longer reach the real thing.

DELIBERATELY NOT STUBBED: `systemctl`. It is the one binary here whose exit
status and stdout carry meaning that both scripts and tests BRANCH on
(`is-active` -> 3, `list-timers` output), so a blanket stub would fabricate
system state rather than merely swallow an effect. Its only measured mutating
reach — monitor-blackout's `cancel_timer` — is closed upstream by the `ddcutil`
stub here: with no DDC/CI bus detected the script exits before scheduling
anything. Re-measured after the fix: 0 unstubbed `systemctl` reaches in
scripts/tests. If that changes, stub it with per-verb semantics, not exit 0.
"""
from __future__ import annotations

from pathlib import Path

from .mockbin import write_exec

# The launchers a devrc script may invoke that CHANGE THE OPERATOR'S MACHINE.
# 🔴 A ledger, not a convenience list: scripts/tests/test_no_real_launchers.py
# pins this exact set, so it fails when the set GROWS or SHRINKS. Growing it is
# fine and expected — do it deliberately, in the same commit as the script that
# needs it.
HOST_LAUNCHERS = (
    "systemd-run",   # creates REAL transient units/timers, hours into the future
    "notify-send",   # real desktop toast
    "dunstify",      # real desktop toast (dunst's own client)
    "openrgb",       # drives the chassis RGB headers
    "ddcutil",       # drives the panel backlight over DDC/CI
    "xdg-open",      # opens a real application / browser tab
    "i3-msg",        # moves the operator's real windows
    "xdotool",       # injects real keystrokes and clicks
    "espanso",       # the real text expander (start/stop/inject)
)

LOG_NAME = "launches.log"


def log_path(stub_dir: Path) -> Path:
    """Where `install(stub_dir)` records intercepted argv, one line per launch."""
    return Path(stub_dir) / LOG_NAME


def install(stub_dir: Path) -> Path:
    """Write a record-only stub for every HOST_LAUNCHERS entry into `stub_dir`.

    Returns the log path. Each stub appends `<name> <argv…>` to that log and
    exits 0.

    🔴 EXIT 0 IS LOAD-BEARING, not laziness. These scripts run under `set -e`
    and treat a launcher as fire-and-forget; a stub that exited non-zero would
    abort `blackout()` at the `systemd-run` line and every later assertion
    would be measuring the STUB rather than the script under test. The guard
    test pins the exit status for that reason.
    """
    stub_dir = Path(stub_dir)
    stub_dir.mkdir(parents=True, exist_ok=True)
    log = log_path(stub_dir)
    for name in HOST_LAUNCHERS:
        # POSIX sh body; mockbin.write_exec owns the shebang (/bin/sh), which is
        # the one interpreter path that exists in the nix build sandbox too.
        write_exec(
            stub_dir / name,
            'printf \'%s %s\\n\' "{name}" "$*" >> "{log}"\nexit 0\n'.format(
                name=name, log=log),
        )
    return log


def recorded(stub_dir: Path) -> list[str]:
    """Every intercepted launch so far, as `<name> <argv…>` lines."""
    log = log_path(stub_dir)
    if not log.exists():
        return []
    return [ln for ln in log.read_text(encoding="utf-8").splitlines() if ln.strip()]
