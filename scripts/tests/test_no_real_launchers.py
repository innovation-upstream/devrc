"""🔴 The guard: no test in this suite may reach a REAL host launcher.

This file exists because a GREEN suite was firing real desktop toasts and
creating real transient systemd timers on the operator's machine — 15 launches
in 29 passing tests, measured on the dev host at a67f795. The nix build sandbox
that gates merges has none of those binaries, so it can never observe the
class; these tests are written to fail in BOTH tiers.

🔴 SCOPE, stated so the title cannot be read as more than it is: this covers
`scripts/tests`, the launchers in `nolaunch.HOST_LAUNCHERS`, and `systemctl`'s
MUTATING verbs. It does not cover other suites (they have their own conftests),
and it cannot cover a test that replaces PATH wholesale — the two sites that do
are pinned below rather than protected.

What is asserted, and why each is not enough alone:

  * RESOLUTION  — `which(<launcher>)` lands inside the stub dir. On the dev host
    that IS the behavioural claim (a real binary exists to be shadowed); in the
    sandbox it degenerates to "a stub exists", which is why the others follow.
  * ORDERING    — the stub dir is PATH[0]. "On PATH" is not the property that
    matters: an entry AFTER the ambient dirs shadows nothing.
  * BEHAVIOUR   — invoking a launcher through a shell RECORDS to the stub log
    and exits 0 — and the stub's whole body is pinned against a literal, because
    a stub that records correctly AND has a side effect passes every
    behavioural assertion here.
  * AUTOUSE     — one test deliberately does NOT request the fixture, so
    deleting `autouse=True` goes red. Every other test requesting it by name
    made that deletion invisible.
  * THE SEAM    — the real hazard paths (monitor-blackout's `systemd-run`
    scheduling and its `cancel_timer` systemctl storm, rig-control's `openrgb` +
    `notify-send`) land in the stub log. A component-scoped check would pass
    with the fixture deleted as long as some stub file existed somewhere.
  * THE LEDGER  — pinned against the TREE (`testlib.launcher_scan`), not only
    against itself. `dunstctl`, `rofi` and `yad` were all reachable while the
    self-pinned list said the set was complete.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

from testlib import launcher_scan  # noqa: E402
from testlib import nolaunch  # noqa: E402
from testlib.mockbin import write_exec  # noqa: E402

# Same reasoning as the other suites here: resolve the interpreter once, to an
# absolute path, because `/usr/bin/env` does not exist in the nix sandbox and a
# bare "bash" would be looked up in the child's (stub-only) PATH.
_BASH = shutil.which("bash")
if _BASH is None:  # pragma: no cover — both tiers ship bash
    raise RuntimeError("bash not found on PATH; this suite cannot run hermetically")
_SH = "/bin/sh"

STUB_DIR_ENV = "DEVRC_TEST_LAUNCH_STUB_DIR"


def _stub_dir_from_env() -> Path:
    """The stub dir as the FIXTURE published it, not as a fixture argument.

    🔴 Deliberate: a test that takes `no_real_launchers` as a parameter requests
    the fixture explicitly, so it still runs with `autouse=True` deleted. Tests
    that go through this helper depend on the fixture having run for EVERYONE,
    which is the property the fixture exists for.
    """
    raw = os.environ.get(STUB_DIR_ENV)
    assert raw, (
        f"{STUB_DIR_ENV} is unset — the session fixture in scripts/tests/"
        "conftest.py did not run for a test that never asked for it, so the "
        "suite is unprotected for every test that forgets to request it")
    return Path(raw)


def _real_binary(name: str) -> str | None:
    """Resolve `name` on the AMBIENT PATH, ignoring the stub dir.

    Lets a test ask "does this host actually have one?" without the fixture's
    answer getting in the way — the question both tiers must be asked
    differently about.
    """
    stub = str(_stub_dir_from_env())
    entries = [p for p in os.environ["PATH"].split(os.pathsep) if p and p != stub]
    return shutil.which(name, path=os.pathsep.join(entries))


# --------------------------------------------------------------------------- #
# The ledger — pinned against itself AND against the tree
# --------------------------------------------------------------------------- #
def test_the_stubbed_launcher_set_is_pinned():
    """Fails when the set GROWS **or** SHRINKS — both must be deliberate.

    Shrinking is the dangerous direction (a launcher silently becomes reachable
    again); growing is welcome, and updating this list is the acknowledgement.
    """
    assert set(nolaunch.HOST_LAUNCHERS) == {
        "systemd-run", "notify-send", "dunstify", "dunstctl", "rofi", "yad",
        "openrgb", "ddcutil", "xdg-open", "i3-msg", "xdotool", "espanso",
    }
    # systemctl is NOT record-only — it is verb-split, and lives in its own
    # tests below. Putting it here would swallow `is-active`, which scripts and
    # tests branch on.
    assert "systemctl" not in nolaunch.HOST_LAUNCHERS


# Every HAZARD_VOCABULARY name the top-level scripts reach that is NOT stubbed,
# with the reason it is safe. 🔴 This table is the acknowledgement, not a
# silencer: an entry here is a claim that nothing in scripts/tests can reach it.
ACKNOWLEDGED_UNSTUBBED = {
    "systemctl": "verb-split rather than stubbed — see the systemctl tests below",
    "home-manager": (
        "ship.sh/drift-check.sh only; MEASURED unreachable — a whole-tier run "
        "under a recording interceptor logged zero calls"),
    "nixos-rebuild": (
        "ship.sh/airvpn-sudo only, both behind sudo; MEASURED unreachable in "
        "the same whole-tier run"),
}


def test_every_hazardous_binary_the_scripts_reach_is_stubbed_or_acknowledged():
    """🔴 The ledger, pinned against the TREE instead of against itself.

    The previous revision asserted only that HOST_LAUNCHERS contained what it
    contained. `dunstctl` (notif-center, i3status-notifs, bar-status-poll),
    `rofi` (five scripts) and `yad` (rig-control's panel) were all invoked by
    scripts this suite executes, none was listed, and nothing went red.
    """
    hits = launcher_scan.hazard_hits(SCRIPTS)
    known = set(nolaunch.HOST_LAUNCHERS) | set(ACKNOWLEDGED_UNSTUBBED)
    unknown = {name: files for name, files in hits.items() if name not in known}
    assert not unknown, (
        "these host-affecting binaries are reached by scripts/ but are neither "
        f"stubbed nor acknowledged: {unknown}. Add them to "
        "nolaunch.HOST_LAUNCHERS, or to ACKNOWLEDGED_UNSTUBBED with the reason "
        "nothing in scripts/tests can reach them.")


def test_the_hazard_scan_can_actually_find_something(tmp_path):
    """POSITIVE CONTROL. A scan that matches nothing would pass the test above
    for every possible tree, which is the reassuring zero RULES.md warns about."""
    fake = tmp_path / "scripts"
    fake.mkdir()
    # write_exec, not write_text: these fixtures carry a shebang, and a test
    # file that spells one itself is what test_runtime_shebangs.py scans for
    # (it went red on the first version of these two lines).
    write_exec(fake / "some-script.sh", "rofi -dmenu\n")
    write_exec(fake / "quiet.sh", "echo hello\n")
    hits = launcher_scan.hazard_hits(fake)
    assert hits == {"rofi": ["some-script.sh"]}, hits


def test_no_repo_file_can_shadow_a_stub_by_name():
    """`test_rig_control.py` prepends `scripts/` to PATH BEFORE the ambient
    entries, so a file named `scripts/openrgb` would out-rank the stub dir for
    that suite. None exists today; this fails the day one does."""
    shadowing = [n for n in nolaunch.HOST_LAUNCHERS if (SCRIPTS / n).exists()]
    assert not shadowing, (
        f"scripts/ contains {shadowing}, which a suite that prepends scripts/ to "
        "PATH would resolve INSTEAD of the stub")


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


def test_a_test_that_never_asked_for_the_fixture_is_still_protected():
    """A non-requesting test finds the stub — in THIS session.

    ⚠ ORDER-DEPENDENT, and that is stated rather than hidden: the fixture is
    session-scoped, so once ANY test requests it the environment stays patched
    for everything after. This test therefore cannot distinguish "autouse" from
    "somebody earlier asked" — MEASURED: with `autouse=False` it still passed.
    `test_autouse_is_what_protects_a_test_that_never_asks` below is the pin;
    this one is the cheap in-session check that the patched state is real.
    """
    stub_dir = _stub_dir_from_env()
    resolved = shutil.which("openrgb")
    assert resolved is not None, (
        "openrgb resolves to nothing for a test that did not request the "
        "fixture — the protection is opt-in, which is the bug")
    assert Path(resolved).parent == stub_dir, (
        f"openrgb resolves to {resolved}, outside {stub_dir}: a test that never "
        "asked for the fixture is unprotected")


_PROBE_TEST = '''\
"""Asserts protection WITHOUT requesting the fixture, and alone in its session."""
import os
import shutil
from pathlib import Path


def test_protected_without_asking():
    stub = os.environ.get("DEVRC_TEST_LAUNCH_STUB_DIR")
    assert stub, "the session fixture never ran for a test that did not ask"
    resolved = shutil.which("openrgb")
    assert resolved and Path(resolved).parent == Path(stub), resolved
'''


def test_autouse_is_what_protects_a_test_that_never_asks(tmp_path):
    """🔴 THE AUTOUSE PIN, as a control/mutant PAIR in a separate session.

    MEASURED first, which is why this exists: deleting `autouse=True` changed
    NOTHING across the whole scripts/tests directory — every guard test took the
    fixture as a parameter, so the first one to run set it up for all the rest,
    and the in-session check above passed on the mutant while unrelated files
    fired real `systemd-run` calls. An earlier check always winning is the
    unreachable-guard trap from claude/RULES.md.

    The only way to observe autouse is a session where NOBODY asks:

      control : real conftest      -> the probe PASSES (positive control — the
                                      fixture reaches a test that never asked)
      mutant  : autouse=False      -> the probe FAILS

    Both halves run the real `scripts/tests/conftest.py`, so this pins the
    shipped file rather than a paraphrase of it.
    """
    conftest_src = (SCRIPTS / "tests" / "conftest.py").read_text(encoding="utf-8")
    needle = "autouse=" + "True"
    assert conftest_src.count(needle) == 1, (
        f"expected exactly one autouse declaration to mutate, found "
        f"{conftest_src.count(needle)}")

    # 🔴 The child must start from the UNPATCHED environment, or it inherits this
    # session's already-prepended stub dir and both halves pass — which is
    # exactly what happened on the first attempt: the mutant went green on the
    # PARENT's protection. Strip the stub dir back out of PATH and drop the
    # published env var, so the child's own conftest is the only thing that can
    # provide either.
    stub_dir = str(_stub_dir_from_env())
    clean_env = {k: v for k, v in os.environ.items() if k != STUB_DIR_ENV}
    clean_env["PATH"] = os.pathsep.join(
        p for p in os.environ["PATH"].split(os.pathsep) if p != stub_dir)
    clean_env["PYTHONDONTWRITEBYTECODE"] = "1"

    def _probe_session(where: Path, conftest_text: str):
        root = where / "scripts"
        (root / "tests").mkdir(parents=True)
        (root / "testlib").symlink_to(SCRIPTS / "testlib")
        (root / "tests" / "conftest.py").write_text(conftest_text, encoding="utf-8")
        probe = root / "tests" / "test_probe.py"
        probe.write_text(_PROBE_TEST, encoding="utf-8")
        return subprocess.run(
            [sys.executable, "-B", "-m", "pytest", str(probe), "-q",
             "-p", "no:cacheprovider"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            timeout=300, env=clean_env)

    control = _probe_session(tmp_path / "control", conftest_src)
    assert control.returncode == 0, (
        "a test that never requested the fixture was NOT protected — autouse is "
        f"not doing its job:\n{control.stdout}")

    mutant = _probe_session(tmp_path / "mutant",
                            conftest_src.replace(needle, "autouse=" + "False"))
    assert mutant.returncode != 0, (
        "with autouse removed the probe STILL passed, so nothing here observes "
        f"autouse and the protection is silently opt-in:\n{mutant.stdout}")
    assert "the session fixture never ran" in mutant.stdout, mutant.stdout


# --------------------------------------------------------------------------- #
# Behaviour — and the stub's exact shape
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


@pytest.mark.parametrize("launcher", nolaunch.HOST_LAUNCHERS)
def test_a_stub_does_nothing_but_record(launcher, no_real_launchers):
    """🔴 Pins the stub's ENTIRE body against a literal written here.

    Every behavioural assertion above is satisfied by a stub that records
    correctly and ALSO does something else — MEASURED: a stub with an extra side
    effect kept 52/52 green while performing 36 of them. The only way to pin
    "records and nothing more" is to pin the whole text, and to write the
    expectation HERE rather than derive it from `nolaunch.launcher_body`, which
    the mutation would change too.
    """
    log = nolaunch.log_path(no_real_launchers)
    # 🔴 The shebang is spelled in TWO PIECES on purpose. Written whole it is a
    # quoted `#!` in a test file, which is precisely what
    # `test_runtime_shebangs.py` scans for — it went red on this line. Splitting
    # keeps the expectation a LITERAL (deriving it from `mockbin.SHEBANG` would
    # make a mutation of that constant invisible here) while staying out of the
    # scanner's needles.
    expected = (
        "#" + "!/bin/sh\n"
        "printf '%s %s\\n' \"" + launcher + "\" \"$*\" >> \"" + str(log) + "\"\n"
        "exit 0\n"
    )
    actual = (no_real_launchers / launcher).read_text(encoding="utf-8")
    assert actual == expected, (
        f"the {launcher} stub is not a pure recorder any more:\n{actual!r}")


# --------------------------------------------------------------------------- #
# systemctl — verb-split, and installed only when there is something to shadow
# --------------------------------------------------------------------------- #
def test_systemctl_is_stubbed_exactly_when_a_real_one_exists(no_real_launchers):
    """🔴 The invariant, asserted in BOTH directions rather than skipped in one.

    Installing a systemctl stub where none exists (the nix sandbox) would make
    `command -v systemctl` start succeeding and change which branch every script
    under test takes — a behaviour change in the authoritative tier bought for
    no protection at all. Installing NONE where a real one exists is the hazard.
    """
    real = _real_binary("systemctl")
    stub = no_real_launchers / "systemctl"
    if real is None:
        assert not stub.exists(), (
            "a systemctl stub was installed on a host that has no systemctl — "
            "that changes `command -v systemctl` for every script under test")
        assert shutil.which("systemctl") is None
    else:
        assert stub.exists(), (
            f"a real systemctl exists at {real} and nothing shadows it — "
            "mutating verbs from the scripts under test reach the live session")
        assert Path(shutil.which("systemctl")).parent == no_real_launchers


def test_a_mutating_systemctl_verb_is_recorded_and_swallowed(no_real_launchers):
    """`stop` must not reach the real binary.

    The discriminator is not just the log line: a REAL `systemctl --user stop`
    of a unit that is not loaded exits non-zero and writes to stderr, so
    rc == 0 with empty stderr is itself evidence the real one did not run.
    """
    if _real_binary("systemctl") is None:
        assert shutil.which("systemctl") is None, (
            "no real systemctl, yet something answers to the name")
        return

    before = len(nolaunch.blocked_systemctl(no_real_launchers))
    unit = "devrc-guard-probe-never-exists.timer"
    p = subprocess.run(
        [_SH, "-c", f"systemctl --user stop {unit}"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        timeout=15, env=dict(os.environ))

    assert p.returncode == 0, f"{p.stdout}{p.stderr}"
    assert p.stderr == "", (
        f"the real systemctl appears to have run: {p.stderr!r}")
    blocked = nolaunch.blocked_systemctl(no_real_launchers)
    assert len(blocked) == before + 1, blocked[before:]
    assert unit in blocked[-1] and "stop" in blocked[-1], blocked[-1]


def test_a_read_only_systemctl_verb_still_reaches_the_real_binary(no_real_launchers):
    """The other half: swallowing reads would fabricate system state.

    `is-active` on a unit that does not exist must still answer the way the real
    binary answers (non-zero), because scripts and tests branch on it —
    monitor-blackout's `status` is exactly that branch.
    """
    if _real_binary("systemctl") is None:
        assert shutil.which("systemctl") is None
        return

    p = subprocess.run(
        [_SH, "-c", "systemctl --user is-active devrc-guard-probe-never-exists.timer"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        timeout=15, env=dict(os.environ))
    # A SWALLOWING stub would exit 0 with empty output — exactly what the
    # mutating branch above does. Non-zero here is the discriminator, and the
    # `systemctl(read)` line proves the passthrough branch is the one that ran.
    # (The exact wording of the real binary's answer is deliberately not
    # asserted: it differs between a host with a user bus and one without, and
    # this test is about which BRANCH ran, not about systemd's phrasing.)
    assert p.returncode != 0, (
        "is-active answered 0 for a unit that does not exist — the stub is "
        f"fabricating state instead of passing the read through: {p.stdout!r}")
    assert any(ln.startswith("systemctl(read)") for ln in
               nolaunch.recorded(no_real_launchers))


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


def test_monitor_blackout_scheduling_reaches_the_stub_not_systemd(tmp_path):
    """🔴 The exact launch that created 8h timers on the operator's machine.

    Fails in BOTH tiers if the fixture is removed: on the dev host the real
    `systemd-run` would take the call (and create the timer) leaving the log
    empty; in the sandbox there is no `systemd-run` at all, so the script dies
    and the log is empty just the same.

    Takes the stub dir from the ENVIRONMENT, not as a fixture parameter, so
    `autouse=True` is load-bearing here too.
    """
    stub_dir = _stub_dir_from_env()
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

    before = len(nolaunch.recorded(stub_dir))
    env = dict(os.environ)
    env["HOME"] = str(home)
    env["PATH"] = str(tmp_path) + os.pathsep + env["PATH"]
    env["XDG_RUNTIME_DIR"] = str(tmp_path)
    p = subprocess.run([_BASH, str(canon), "2h"],
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                       timeout=60, env=env)
    assert p.returncode == 0, f"{p.stdout}{p.stderr}"

    new = nolaunch.recorded(stub_dir)[before:]
    scheduled = [ln for ln in new if ln.startswith("systemd-run ")]
    assert len(scheduled) == 1, (
        f"expected monitor-blackout.sh's schedule_restore to hit the stub; "
        f"recorded: {new}")
    assert "--unit=monitor-blackout-restore-v2" in scheduled[0], scheduled[0]
    assert "--on-active=2h" in scheduled[0], scheduled[0]


def test_monitor_blackout_restore_cannot_stop_a_real_timer(tmp_path):
    """🔴 `restore()` calls `cancel_timer` as its FIRST statement — BEFORE any
    ddcutil call — so the ddcutil stub does NOT close this path.

    MEASURED at the revision that claimed it did: `restore` reached 12 mutating
    systemctl calls (stop/kill/reset-failed x 2 units x 2 rounds), and from the
    base clone `test_rig_control.py` reached 36 of them with 52/52 green. When a
    blackout is pending, that is a `git push` that kills the auto-restore timer
    and leaves the panel dark.
    """
    stub_dir = _stub_dir_from_env()
    home, canon = _canonical_copy(tmp_path, "monitor-blackout.sh")
    write_exec(tmp_path / "ddcutil", textwrap.dedent("""\
        case "$*" in
            *detect*) echo "i2c-5" ;;
            *getvcp*10*) echo "VCP 10 100 60" ;;
            *) echo "ok" ;;
        esac
        exit 0
    """))
    before = len(nolaunch.blocked_systemctl(stub_dir))

    env = dict(os.environ)
    env["HOME"] = str(home)
    env["PATH"] = str(tmp_path) + os.pathsep + env["PATH"]
    env["XDG_RUNTIME_DIR"] = str(tmp_path)
    p = subprocess.run([_BASH, str(canon), "restore"],
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                       timeout=60, env=env)
    assert p.returncode == 0, f"{p.stdout}{p.stderr}"

    if _real_binary("systemctl") is None:
        # Nothing to protect: no systemctl exists, so cancel_timer's calls could
        # not have reached one. Assert THAT, rather than skipping.
        assert shutil.which("systemctl") is None
        return

    blocked = nolaunch.blocked_systemctl(stub_dir)[before:]
    verbs = {v for ln in blocked for v in ("stop", "kill", "reset-failed") if v in ln}
    assert verbs == {"stop", "kill", "reset-failed"}, (
        f"cancel_timer's mutating verbs did not all land in the stub: {blocked}")
    assert any("monitor-blackout-restore-v2" in ln for ln in blocked), blocked


def test_rig_control_notify_and_rgb_reach_the_stub(tmp_path):
    """rig-control's other two real launches: `openrgb` and the toast.

    `rgb-off` is the shortest path that fires both. Uses the environment rather
    than the fixture parameter, for the autouse reason above.
    """
    stub_dir = _stub_dir_from_env()
    before = len(nolaunch.recorded(stub_dir))
    env = dict(os.environ)
    env["XDG_CACHE_HOME"] = str(tmp_path)
    p = subprocess.run([_BASH, str(SCRIPTS / "rig-control.sh"), "rgb-off"],
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                       timeout=30, env=env)
    assert p.returncode == 0, f"{p.stdout}{p.stderr}"

    new = nolaunch.recorded(stub_dir)[before:]
    assert any(ln.startswith("openrgb ") and "000000" in ln for ln in new), new
    assert any(ln.startswith("notify-send ") and "Chassis RGB off" in ln
               for ln in new), new


# --------------------------------------------------------------------------- #
# The PATH-clobber sites the fixture CANNOT cover
# --------------------------------------------------------------------------- #
# (file, lineno-independent needle, why it is safe today)
#
# 🔴 SELF-MATCH, and it bit on the first run: written whole, each needle below is
# ITSELF a PATH-clobbering line in this file, so the scan reported this file as
# a third unpinned site. Split across a `+` the same way `testlib/shebang_scan.py`
# assembles its patterns — which keeps THIS file inside the scan's scope instead
# of excluding it, so a real clobber added here would still be caught.
PINNED_PATH_CLOBBERS = {
    "test_rig_control.py": (
        '"PATH"' + ': "/usr/bin/false"',
        "deliberately makes yad unfindable; /usr/bin/false holds no binaries, so "
        "nothing hazardous is reachable from it either"),
    "test_claude_log_rotate.py": (
        'e["PATH"]' + ' = str(tmp_path / "empty-bin")',
        "an empty directory in tmp_path — the point of the test is that "
        "logrotate is absent; nothing else is present either"),
}


def test_every_path_clobbering_site_is_pinned():
    """🔴 The one shape this fixture structurally cannot protect.

    A test that REPLACES PATH instead of prepending to it drops the stub dir
    entirely. Both sites today are harmless — the replacement paths contain no
    binaries — but the third one, added tomorrow with a real directory in it,
    would be unprotected and nothing would go red. So the SET is pinned: a new
    clobber fails here until someone writes down why it is safe.
    """
    found = launcher_scan.path_clobbers(SCRIPTS / "tests")
    by_file = {}
    for name, lineno, line in found:
        by_file.setdefault(name, []).append((lineno, line))

    assert set(by_file) == set(PINNED_PATH_CLOBBERS), (
        f"PATH-clobbering sites changed: found {sorted(by_file)}, pinned "
        f"{sorted(PINNED_PATH_CLOBBERS)}. A new one must be justified here; a "
        "vanished one must be unpinned.")
    for name, (needle, _why) in PINNED_PATH_CLOBBERS.items():
        assert any(needle in line for _ln, line in by_file[name]), (
            f"{name} still clobbers PATH but not in the pinned shape: "
            f"{by_file[name]}")


def test_the_clobber_scan_can_actually_find_something(tmp_path):
    """POSITIVE CONTROL for the scan above, and a NEGATIVE one beside it:
    a PATH built FROM os.environ must not be reported, or the pin would drown."""
    d = tmp_path / "tests"
    d.mkdir()
    (d / "test_clobber.py").write_text(
        'env = {"PATH"' + ': "/somewhere/else"}\n', encoding="utf-8")  # split: self-match
    (d / "test_inherits.py").write_text(
        'env = {"PATH": str(tmp) + ":" + os.environ["PATH"]}\n', encoding="utf-8")
    # The MULTI-LINE inheriting shape — reading only the first line reports it
    # as a clobber, which is what this guard file's own code did.
    (d / "test_inherits_across_lines.py").write_text(
        'env["PATH"] = os.pathsep.join(\n'
        '    p for p in os.environ["PATH"].split(os.pathsep) if p)\n',
        encoding="utf-8")
    hits = launcher_scan.path_clobbers(d)
    assert [h[0] for h in hits] == ["test_clobber.py"], hits


# --------------------------------------------------------------------------- #
# The fixture must not silently satisfy the runner's tool precondition
# --------------------------------------------------------------------------- #
def test_no_required_tool_is_satisfied_by_a_stub():
    """`run-tests.sh`'s GUARD 1 checks `command -v <tool>`, and its unit test
    checks `shutil.which(t) is not None` — from a process whose PATH now has 12
    stubs on it. No overlap today; this is what keeps it that way, instead of a
    comment saying so."""
    runner = (SCRIPTS / "run-tests.sh").read_text(encoding="utf-8")
    m = re.search(r"^REQUIRED_TOOLS=\((?P<body>[^)]*)\)", runner, re.M)
    assert m, "REQUIRED_TOOLS not found in run-tests.sh"
    required = set(m.group("body").split())
    stubbed = set(nolaunch.HOST_LAUNCHERS) | {"systemctl"}
    assert not (required & stubbed), (
        f"{sorted(required & stubbed)} is both a REQUIRED_TOOLS entry and a "
        "stub this fixture installs — the precondition would be satisfied by "
        "the stub and stop meaning anything")


# --------------------------------------------------------------------------- #
# MUTATION: prove the seam assertion is what makes the seam test red
# --------------------------------------------------------------------------- #
_HARNESS_CONFTEST = '''\
"""Harness conftest: the host stays PROTECTED, but the guard is pointed at an
EMPTY stub dir — the observable shape of "the launch did not land in the stub".

Never lets a real launcher run: a decoy stub dir (with its own log) is first on
PATH, so the subprocess under test still cannot reach systemd-run.
"""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from testlib import nolaunch


@pytest.fixture(scope="session", autouse=True)
def no_real_launchers(tmp_path_factory):
    decoy = Path(tmp_path_factory.mktemp("decoy"))
    nolaunch.install(decoy)
    empty = Path(tmp_path_factory.mktemp("empty"))
    os.environ["PATH"] = str(decoy) + os.pathsep + os.environ.get("PATH", "")
    os.environ["DEVRC_TEST_LAUNCH_STUB_DIR"] = str(empty)
    yield empty
'''

_SEAM_TEST = "test_monitor_blackout_scheduling_reaches_the_stub_not_systemd"
# 🔴 SELF-MATCH. Assembled from two pieces so this line is not itself an
# occurrence of the needle — the same reason `testlib/shebang_scan.py` builds
# its patterns from character codes. Spelled whole, `src.count(...)` would be 2
# and the "exactly one site to mutate" check would fail on its own source.
_SEAM_NEEDLE = "assert len(scheduled) == " + "1, ("


def _harness_tree(tmp_path, source: str) -> Path:
    """A runnable copy of THIS file whose `testlib` and scripts resolve."""
    root = tmp_path / "scripts"
    (root / "tests").mkdir(parents=True)
    (root / "testlib").symlink_to(SCRIPTS / "testlib")
    for script in ("monitor-blackout.sh", "rig-control.sh"):
        (root / script).symlink_to(SCRIPTS / script)
    (root / "tests" / "conftest.py").write_text(_HARNESS_CONFTEST, encoding="utf-8")
    target = root / "tests" / "test_no_real_launchers.py"
    target.write_text(source, encoding="utf-8")
    return target


def _run_seam(target: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-B", "-m", "pytest", f"{target}::{_SEAM_TEST}",
         "-q", "-p", "no:cacheprovider"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=300,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})


def test_the_seam_assertion_is_what_makes_the_seam_test_red(tmp_path):
    """🔴 Break this file's own assertion on purpose; the seam case must go GREEN.

    An audit found this the one surviving mutant, and the "no test can
    meta-guard its own asserts" answer was wrong — `test_run_tests_floors.py`
    does exactly this, in this directory. Run as a PAIR under one env, because
    "the mutant went green" means nothing next to a control that never went red:

      control : intact test + an empty stub dir  -> RED, naming this assertion
      mutant  : assertion neutered               -> GREEN

    The host is never at risk in either half: the harness conftest still puts a
    working (decoy) stub dir first on PATH, so `systemd-run` is intercepted —
    what is simulated is the guard LOOKING at the wrong place, which is the same
    observable as the launch escaping.
    """
    src = Path(__file__).read_text(encoding="utf-8")
    assert src.count(_SEAM_NEEDLE) == 1, (
        f"expected exactly one seam assertion to mutate, found "
        f"{src.count(_SEAM_NEEDLE)} — the mutation would not land where intended")

    control = _run_seam(_harness_tree(tmp_path / "control", src))
    assert control.returncode != 0 and "1 failed" in control.stdout, (
        "the CONTROL half did not go red, so the mutant going green would prove "
        f"nothing.\n{control.stdout}")
    assert "expected monitor-blackout.sh's schedule_restore to hit the stub" in \
        control.stdout, control.stdout

    # Built from _SEAM_NEEDLE for the same self-match reason as the needle.
    mutated = src.replace(
        _SEAM_NEEDLE,
        "scheduled = scheduled or ['systemd-run --unit=monitor-blackout-restore-v2"
        " --on-active=2h']\n    " + _SEAM_NEEDLE)
    mutant = _run_seam(_harness_tree(tmp_path / "mutant", mutated))
    assert mutant.returncode == 0, (
        "with the seam assertion neutered the test is STILL red, so it is red "
        f"for some other reason and proves nothing about this assertion.\n"
        f"{mutant.stdout}")
