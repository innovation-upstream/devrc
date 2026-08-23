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

🔴 `systemctl` IS NOT A RECORD-ONLY LAUNCHER — IT IS SPLIT BY VERB
------------------------------------------------------------------
An earlier revision of this file did not stub it at all, and said the reason was
that its only mutating reach was "closed upstream by the ddcutil stub: with no
DDC/CI bus detected the script exits before scheduling anything". That was
FALSE, and a review measured it false: `monitor-blackout.sh`'s `restore()` and
`fade_restore()` call `cancel_timer` as their FIRST statement, BEFORE any
ddcutil call. In the state this fixture creates:

    monitor-blackout.sh restore        -> 12 mutating systemctl calls
    monitor-blackout.sh fade-restore   ->  6
    monitor-blackout.sh 2h             ->  0   <- the only path ddcutil closes
    monitor-blackout.sh status         ->  1, read-only

and from the BASE CLONE, where the canonical-path guard accepts and
`rig-control.sh` delegates `restore` to it, **36 real mutating `systemctl --user`
calls** — `stop`, `kill` and `reset-failed` against `monitor-blackout-restore-v2`
and its legacy twin. A no-op when nothing is pending — and, when the operator HAS
blacked out their monitor, a `git push` that silently kills the auto-restore
timer and leaves the panel dark.

⚠ An earlier revision of this paragraph said "with 52/52 tests green". That
number was NOT reproducible and is retracted: `test_rig_control.py` collects 16
and produces those 36 calls on its own; rig + monitor-blackout is 29. The 36
reproduces exactly in both places, the 52 came from nowhere. Numbers in this file
are measurements or they are deleted.

So `systemctl` is stubbed, but not record-only: its exit status and stdout are
things both scripts and tests BRANCH on, and fabricating them would trade one
lie for another. The split is FAIL-CLOSED and keys on the VERB — the first
non-flag token, exactly as systemd parses it — so an ARGUMENT that happens to
spell a read verb cannot promote a mutation (`systemctl --user stop status` is
`stop`). Anything else, including a verb this list has never heard of, is
recorded and swallowed.

🔴 WHY `systemctl` IS THE ONLY CONDITIONAL STUB (and the record-only ones are not)
---------------------------------------------------------------------------------
The principle is NOT "never install a stub where no real binary exists" — it is
**never fabricate an answer**. The two kinds of stub differ on exactly that:

  * a record-only launcher is FIRE-AND-FORGET. Its callers use `notify-send …
    || true`, discard its output, and branch on nothing it says. Exit 0 with no
    output is the truthful outcome of "nothing was launched", so installing one
    everywhere costs no honesty — and it BUYS tier parity: the same branch runs
    in the sandbox and on the dev host, instead of the sandbox silently
    exercising the "binary absent" path the operator's machine never takes.
  * `systemctl` is asked QUESTIONS. `is-active` returning 3, `list-timers`
    printing rows — scripts and tests branch on those. Where no real binary
    exists there is nothing to forward to, so a stub could only make an answer
    up. That is why it is installed only when a real one exists to shadow, and
    why the guard test pins the invariant in BOTH directions rather than
    skipping on one of them.

⚠ Consequence, stated because nothing in the repo hits it: a bare `systemctl`
(or one with only flags) has no verb, so it is recorded and swallowed with exit
0 and no output, where the real binary would list units. Every call site in
this tree passes a verb.
"""
from __future__ import annotations

import shutil
from pathlib import Path

from .mockbin import write_exec

# The launchers a devrc script may invoke that CHANGE THE OPERATOR'S MACHINE.
# 🔴 A ledger, not a convenience list: scripts/tests/test_no_real_launchers.py
# pins this exact set, so it fails when the set GROWS or SHRINKS, and a separate
# scan (testlib.launcher_scan) fails when the SCRIPTS reach for a hazardous
# binary this ledger has never heard of — which is how `dunstctl`, `rofi` and
# `yad` got here: all three were reachable, none was listed, and nothing noticed
# until a human read the tree.
HOST_LAUNCHERS = (
    "systemd-run",   # creates REAL transient units/timers, hours into the future
    "notify-send",   # real desktop toast
    "dunstify",      # real desktop toast (dunst's own client)
    "dunstctl",      # mutes/clears/re-displays the operator's REAL notifications
    "rofi",          # opens a real menu over whatever they are doing
    "yad",           # opens a real GTK dialog (rig-control's panel)
    "openrgb",       # drives the chassis RGB headers
    "ddcutil",       # drives the panel backlight over DDC/CI
    "xdg-open",      # opens a real application / browser tab
    "i3-msg",        # moves the operator's real windows
    "xdotool",       # injects real keystrokes and clicks
    "espanso",       # the real text expander (start/stop/inject)
)

# `systemctl` verbs that only READ. Everything not on this list is blocked —
# unknown verbs included, because the alternative (block a known-bad list, pass
# the rest) fails OPEN on the next verb someone uses.
SYSTEMCTL_READ_VERBS = (
    "is-active", "is-enabled", "is-failed", "is-system-running",
    "show", "show-environment", "status", "cat",
    "list-units", "list-unit-files", "list-timers", "list-sockets",
    "list-jobs", "list-dependencies", "get-default",
)

# Flags that read and exit whatever else is on the line, so they are safe in ANY
# position (unlike a verb, which is positional).
SYSTEMCTL_READ_FLAGS = ("--version", "--help", "-h")

LOG_NAME = "launches.log"


def log_path(stub_dir: Path) -> Path:
    """Where `install(stub_dir)` records intercepted argv, one line per launch."""
    return Path(stub_dir) / LOG_NAME


def launcher_body(name: str, log: Path) -> str:
    """The POSIX-sh body of a record-only stub. No shebang — write_exec owns it.

    🔴 EXIT 0 IS LOAD-BEARING, not laziness. These scripts run under `set -e`
    and treat a launcher as fire-and-forget; a stub that exited non-zero would
    abort `blackout()` at the `systemd-run` line and every later assertion would
    be measuring the STUB rather than the script under test.

    🔴 And RECORDING IS ALL IT DOES. The guard test pins this text against a
    literal copy of itself, because a stub that records correctly AND has a side
    effect passes every behavioural assertion in the suite.
    """
    return 'printf \'%s %s\\n\' "{name}" "$*" >> "{log}"\nexit 0\n'.format(
        name=name, log=log)


def systemctl_body(real: str, log: Path) -> str:
    """The POSIX-sh body of the verb-splitting `systemctl` stub.

    Read verbs exec the REAL binary (so `is-active` still returns 3 for an
    inactive unit, which tests and scripts branch on); everything else is
    recorded and swallowed with exit 0 — the same fire-and-forget contract as
    the record-only stubs, and for the same `set -e` reason.

    🔴 IT CLASSIFIES THE VERB, NOT THE ARGV. The first version scanned EVERY
    argument against the read list, which is not what systemd does and is not
    what this module's own docstring claimed. Measured on that version, all four
    of these reached the real binary:

        systemctl --user stop status      systemctl --user restart cat
        systemctl --user kill status      systemctl --user stop show

    i.e. a UNIT NAMED after a read verb promoted a mutation to a passthrough.
    Nothing in this tree is named that way (`monitor-blackout-restore*`,
    `keylog.service`), so there was no live reach — but the guard's whole value
    is that it does not depend on what units happen to be called.

    The rule now matches systemd's own parse: skip leading options (`--user`,
    `-M host`, `--type=timer`), take the FIRST non-flag token as the verb, and
    decide on that alone. `--version`/`--help` are handled separately because
    they read and exit regardless of position.
    """
    verbs = "|".join(SYSTEMCTL_READ_VERBS)
    flags = "|".join(SYSTEMCTL_READ_FLAGS)
    return (
        # Position-independent read flags first: they print and exit whatever
        # else is on the line.
        'for a in "$@"; do\n'
        '  case "$a" in\n'
        f'    {flags})\n'
        f'      printf \'%s %s\\n\' "systemctl(read)" "$*" >> "{log}"\n'
        f'      exec "{real}" "$@" ;;\n'
        '  esac\n'
        'done\n'
        # The VERB: the first token that is not an option. `-M host` leaves
        # `host` as the first non-flag token, which is not a read verb, so it
        # blocks — fail-closed, the direction this must err in.
        'verb=""\n'
        'for a in "$@"; do\n'
        '  case "$a" in\n'
        '    -*) continue ;;\n'
        '  esac\n'
        '  verb="$a"\n'
        '  break\n'
        'done\n'
        'case "$verb" in\n'
        f'  {verbs})\n'
        f'    printf \'%s %s\\n\' "systemctl(read)" "$*" >> "{log}"\n'
        f'    exec "{real}" "$@" ;;\n'
        'esac\n'
        f'printf \'%s %s\\n\' "systemctl(blocked)" "$*" >> "{log}"\n'
        'exit 0\n'
    )


def install(stub_dir: Path) -> Path:
    """Write the stubs into `stub_dir`; return the log path.

    🔴 CALL THIS BEFORE PREPENDING `stub_dir` TO PATH. The `systemctl` stub
    resolves the real binary through `shutil.which`, so a stub dir already on
    PATH would make it exec ITSELF — an infinite loop rather than a passthrough.
    The assertion below turns that ordering mistake into a named failure instead
    of a hang.
    """
    stub_dir = Path(stub_dir)
    stub_dir.mkdir(parents=True, exist_ok=True)
    log = log_path(stub_dir)
    for name in HOST_LAUNCHERS:
        write_exec(stub_dir / name, launcher_body(name, log))

    real_systemctl = shutil.which("systemctl")
    if real_systemctl is not None:
        if Path(real_systemctl).parent == stub_dir:
            raise AssertionError(
                "install() resolved systemctl to its own stub dir — it was "
                "called AFTER stub_dir was put on PATH, and the stub would exec "
                "itself forever")
        write_exec(stub_dir / "systemctl", systemctl_body(real_systemctl, log))
    return log


def recorded(stub_dir: Path) -> list[str]:
    """Every intercepted launch so far, as `<name> <argv…>` lines."""
    log = log_path(stub_dir)
    if not log.exists():
        return []
    return [ln for ln in log.read_text(encoding="utf-8").splitlines() if ln.strip()]


def blocked_systemctl(stub_dir: Path) -> list[str]:
    """The recorded systemctl calls that were SWALLOWED rather than executed."""
    return [ln for ln in recorded(stub_dir) if ln.startswith("systemctl(blocked)")]


def main(argv: list[str] | None = None) -> int:
    """`python -m testlib.nolaunch <dir>` — install the stubs, print the log path.

    Exists so `scripts/run-tests.sh` can install ONE stub dir for the whole run,
    before any target starts, and put it first on PATH for everything it
    invokes — including the targets that are NOT pytest and therefore load no
    plugin. Same `install()` the plugin calls; the shell needed a door into it,
    not a second implementation of it.
    """
    import sys as _sys
    args = list(_sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        print("usage: python -m testlib.nolaunch <stub-dir>", file=_sys.stderr)
        return 2
    print(install(Path(args[0])))
    return 0


if __name__ == "__main__":  # pragma: no cover — exercised via run-tests.sh
    raise SystemExit(main())
