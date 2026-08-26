"""🔴 The guard covers EVERY target, not one directory — and it is reachable.

WHY THIS FILE EXISTS
--------------------
#399 built the no-real-launcher mechanism and installed it from
`scripts/tests/conftest.py`. `scripts/run-tests.sh` runs ONE pytest process per
target and there are 17 of them, so the guard protected **1 of 17** while
reading as systemic. `claude/RULES.md` → "a count of DECLARATIONS is not a count
of INSTANCES": the number that mattered was never the number of conftests.

WHAT IS REGRESSION COVERAGE HERE AND WHAT IS NOT (RULES.md asks for the label):

  * `test_a_target_that_reaches_a_launcher_is_named_and_red` and
    `test_a_target_running_without_the_plugin_is_named_and_red` are REGRESSION
    coverage. Both are RED at origin/main — where a target outside
    `scripts/tests` reaching `notify-send` produces a green run and a real
    desktop toast — and green here.
  * `test_the_absolute_path_reach_is_intercepted_in_process` is REGRESSION
    coverage for the reach a PATH stub structurally cannot cover
    (`browser-bridge`'s `_I3_MSG_FALLBACKS`). At origin/main the probe binary
    RUNS; here it does not.
  * `test_the_acknowledged_target_must_actually_intercept` is the MUTATION test
    for the mutant that matters: a guard that silently covers ZERO targets while
    the suite stays green. Nothing else in the tree observes it.
  * the ledger/target two-way pins and the `-p` flag pin are INVARIANT GUARDS.
    The bug never violated them; they exist so the enforcement point cannot be
    narrowed to nothing by an edit that still looks like a guard.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from testlib import nolaunch  # noqa: E402
from testlib.mockbin import write_exec  # noqa: E402
from testlib.runner_patch import runner_with_targets, write_pytest_suite  # noqa: E402

RUN_TESTS = SCRIPTS / "run-tests.sh"
RUNNER_SRC = RUN_TESTS.read_text(encoding="utf-8")
# 🔴 CODE ONLY. run-tests.sh is 40% commentary and its comments quote the very
# tokens these pins look for — `python -m pytest`, the plugin flag, the name of
# the env var that must NOT be used. A pin read off the raw text answers a
# question about the PROSE. Same reason `test_no_real_launchers.py` assembles
# its needles from pieces to avoid self-matching.
RUNNER_CODE = "\n".join(ln for ln in RUNNER_SRC.splitlines()
                        if not ln.lstrip().startswith("#"))

# Assembled, not spelled, so this file's own text is not a match for the pin in
# `test_the_plugin_is_loaded_by_flag_not_by_environment`.
_PLUGIN_ENV_VAR = "PYTEST" + "_PLUGINS"
_PLUGIN_FLAG = "-p testlib.nolaunch" + "_plugin"
_NOGIT_FLAG = "-p testlib.nogit" + "_plugin"

# A probe test that reaches a REAL host launcher by name. This is what a future
# test in any target might innocently do; the point of the guard is that it
# becomes a named failure instead of a toast on the operator's desktop.
_REACHING_TEST = '''\
import subprocess


def test_reaches_a_launcher():
    subprocess.run(["notify-send", "NOLAUNCH-PROBE", "planted reach"], check=False)
'''


def _run(args: list[str], timeout: int = 600) -> subprocess.CompletedProcess:
    return subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          text=True, timeout=timeout, cwd=str(REPO_ROOT))


def _pytest_invocations(src: str) -> list[str]:
    """The lines that actually RUN pytest — not the ones that talk about it.

    `run-tests.sh` both invokes pytest and prints diagnostics quoting the same
    command, and GUARD 7's own failure message names the plugin flag. Anchoring
    on "the statement starts with the command" is the difference between reading
    the code and reading the prose about the code.
    """
    return [ln for ln in src.splitlines()
            if ln.strip().startswith("python -m pytest")
            and "--version" not in ln]


def _bash_array(name: str) -> list[str]:
    """The entries of a `NAME=( … )` literal in run-tests.sh, unquoted."""
    m = re.search(rf"^{name}=\((.*?)^\)", RUNNER_SRC, re.S | re.M)
    assert m, f"{name} not found in run-tests.sh — the pin is reading nothing"
    out = []
    for raw in m.group(1).splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        out.append(line.strip('"'))
    return out


# --------------------------------------------------------------------------- #
# The enforcement point is ONE line, and it is on the line every target uses
# --------------------------------------------------------------------------- #
def test_the_single_pytest_invocation_loads_the_plugin():
    """🔴 The whole design in one assertion: there is exactly ONE `python -m
    pytest` invocation in the runner, and it carries the plugin.

    That is what makes "all 17 targets" true BY CONSTRUCTION rather than by
    seventeen copies of a conftest that drift — and it is what makes an
    18th target protected the day it is added, which is the half a per-directory
    copy can never do.
    """
    invocations = _pytest_invocations(RUNNER_CODE)
    assert len(invocations) == 1, (
        "run-tests.sh must invoke pytest from exactly ONE place — the guard is "
        f"attached to that line. Found {len(invocations)}: {invocations}")
    assert _PLUGIN_FLAG in invocations[0], (
        "the single pytest invocation no longer loads the no-real-launcher "
        f"plugin, so every target runs unprotected:\n  {invocations[0].strip()}")
    assert _NOGIT_FLAG in invocations[0], (
        "the git-isolation plugin (GUARD 9) was dropped from the same line — "
        "GUARD 7's edit must not cost it; the complete plugin set is pinned "
        "both ways in test_nogit_isolation.py:\n  "
        f"{invocations[0].strip()}")


def test_the_plugin_is_loaded_by_flag_not_by_environment():
    """`-p`, never `PYTEST_PLUGINS=` — and this is not style.

    `PYTEST_PLUGINS` is INHERITED by child processes, and
    `test_no_real_launchers.py` runs nested pytest sessions as control/mutant
    pairs whose mutant half MUST be unprotected. Exporting the plugin would
    protect the mutant and turn those pins green on their own mutants — the
    "green for the wrong reason" shape from claude/RULES.md.
    """
    assert _PLUGIN_ENV_VAR not in RUNNER_CODE, (
        "run-tests.sh exports PYTEST_PLUGINS; that leaks the guard into the "
        "nested sessions that must run WITHOUT it")


def test_every_acknowledgement_names_a_real_target():
    """The ledger and the target list pin each other, both ways.

    An acknowledgement that outlives its target is an exemption nobody can see
    — which is how a wildcard arrives one rename at a time. Kept as a parse of
    the shipped file rather than a runtime check in the runner, because as a
    runtime check it PREEMPTED ten existing tests that substitute the target
    list (an earlier check that always wins; RULES.md → unreachable guards).
    """
    targets = _bash_array("HERMETIC_TARGETS")
    acked = [e.split("|", 1)[0] for e in _bash_array("NOLAUNCH_ACK")]
    assert acked, "NOLAUNCH_ACK is empty — nothing then requires the guard to fire"
    unknown = [a for a in acked if a not in targets]
    assert not unknown, (
        f"NOLAUNCH_ACK acknowledges targets that are in no target list: {unknown}")


def test_the_acknowledgement_is_the_narrowest_it_can_be():
    """Exactly ONE target may record intercepts, and it is `scripts/tests`.

    A second entry is not forbidden — it is a decision someone has to make in
    the open. This pin is what stops the ledger being widened quietly until it
    exempts everything, which is the wildcard mutant one edit at a time.
    """
    acked = [e.split("|", 1)[0] for e in _bash_array("NOLAUNCH_ACK")]
    assert acked == ["scripts/tests"], (
        "the acknowledgement ledger changed. Every entry is a target allowed to "
        f"reach a real launcher; there should be exactly one: {acked}")


def test_the_non_pytest_targets_are_covered_and_named():
    """The hook scripts and the shell script are targets too.

    They load no plugin — they are not pytest — so their only protection is the
    stub dir the runner puts first on PATH for the whole script. That the
    runner ACCOUNTS them (`NOLAUNCH_SEEN`) is what makes the protection
    observable rather than assumed.
    """
    hooks = _bash_array("HOOK_TESTS")
    shells = _bash_array("SHELL_TESTS")
    assert "scripts/claude-hooks/tests/test_claude_notify.py" in hooks
    # A DELIBERATE LEDGER, not a count — adding an entry is a decision made in
    # the open, exactly as the NOLAUNCH_ACK pin above describes. Second entry
    # added 2026-08-21 (#684): `test_resume_state.sh` had been run by no gate at
    # all since it was written and was RED on main, its assertions silently
    # split between "fails" and "passes because the helper returns false for
    # every input". It touches no launcher and no spool — it sources
    # resume-state.sh and asserts pure extraction helpers on fixture strings.
    # Third entry: `test_base_clone_staleness.sh`, registered when the suite
    # LANDED rather than after being found ungated — the only difference between
    # it and the two above. It touches no launcher: it drives
    # `scripts/claude-hooks/base-clone-staleness.sh` against local bare-repo
    # fixtures under a mktemp dir, and every fixture commit carries its identity
    # via `git -c user.email=…`, so it reaches no real remote and needs nothing
    # from the operator's gitconfig.
    assert shells == [
        "scripts/tests/test_release_wrapper.sh",
        "scripts/tests/test_resume_state.sh",
        "scripts/tests/test_base_clone_staleness.sh",
    ], shells
    for rel in hooks + shells:
        assert (REPO_ROOT / rel).is_file(), f"{rel} is listed but does not exist"
    # Both loops must feed the accounting, or their zeros mean nothing.
    assert RUNNER_SRC.count("NOLAUNCH_SEEN+=(") == 3, (
        "expected exactly three sites to append to NOLAUNCH_SEEN (pytest, hook "
        "scripts, shell scripts); a loop that stopped accounting reports no "
        "intercepts for the most reassuring possible reason")


# --------------------------------------------------------------------------- #
# BEHAVIOUR — the guard fires, in a target that is not scripts/tests
# --------------------------------------------------------------------------- #
def test_a_target_that_reaches_a_launcher_is_named_and_red(tmp_path):
    """🔴 PLANT A REACH IN A TARGET, WATCH THE GUARD CATCH IT.

    A throwaway target whose only test calls `notify-send`. At origin/main this
    is a GREEN run and a real toast; here the run is RED, the target is NAMED,
    and the intercepted argv is printed. The recorded line is the positive
    control: a guard that failed the run without recording anything would be
    reporting something it never observed.
    """
    target = tmp_path / "reaching_tests"
    write_pytest_suite(target, 1, prefix="test_filler")
    (target / "test_reach.py").write_text(_REACHING_TEST, encoding="utf-8")
    runner = runner_with_targets(tmp_path, [str(target)], {str(target): 1},
                                 hook_tests=[], shell_tests=[])
    proc = _run(["bash", str(runner), str(REPO_ROOT)])
    out = proc.stdout + proc.stderr

    assert proc.returncode != 0, f"a planted reach produced a PASSING run:\n{out}"
    assert "GUARD 7 problem" in out, (
        f"the run failed, but not for the launcher reason:\n{out}")
    assert str(target) in out, f"the failure did not NAME the target:\n{out}"
    # 🔴 The `[<worker>]` tag is matched as a PATTERN, not a literal, and that is
    # not laziness: it is whatever `PYTEST_XDIST_WORKER` the nested runner
    # inherited from THIS session's worker, so it is `gw2` under the gate and
    # `main` under a bare serial `pytest` — a literal would pin the harness's
    # own parallelism rather than the guard's report. What IS pinned is that the
    # tag is present and sits between the launcher name and the argv, because
    # that placement is what keeps `run-tests.sh`'s ^-anchored classifiers from
    # reading the line as something else entirely.
    assert re.search(
        r"notify-send \[[^\]\s]+\] NOLAUNCH-PROBE planted reach", out), (
        f"the guard reported a reach it never actually recorded:\n{out}")


def test_a_target_running_without_the_plugin_is_named_and_red(tmp_path):
    """🔴 THE MUTANT THAT MATTERS: the guard covering ZERO targets, silently.

    Strip `-p testlib.nolaunch_plugin` from the copy — the shape of every
    "narrow the glob / comment it out / invert the membership test" mutation,
    whose whole danger is that the suite stays green. The session marker is what
    makes it observable: no marker means the guard never loaded there, and
    without this check that target's `intercepted=0` is indistinguishable from
    real protection.
    """
    target = tmp_path / "plain_tests"
    write_pytest_suite(target, 2, prefix="test_plain")
    runner = runner_with_targets(tmp_path, [str(target)], {str(target): 1},
                                 hook_tests=[], shell_tests=[])
    src = runner.read_text(encoding="utf-8")
    code_hits = _pytest_invocations(src)
    assert len(code_hits) == 1 and _PLUGIN_FLAG in code_hits[0], (
        f"expected exactly one live site to mutate, found {code_hits}")
    runner.write_text(
        src.replace(code_hits[0], code_hits[0].replace(" " + _PLUGIN_FLAG, "")),
        encoding="utf-8")

    proc = _run(["bash", str(runner), str(REPO_ROOT)])
    out = proc.stdout + proc.stderr
    assert proc.returncode != 0, (
        f"a target that ran with NO guard at all produced a PASSING run:\n{out}")
    assert "session marker" in out, (
        f"the run failed, but not because the plugin was missing:\n{out}")


def test_the_control_of_that_mutant_is_green(tmp_path):
    """The positive control for the two tests above: unmutated, a plain target
    with no reach PASSES. Without this, "the run went red" is satisfied by the
    runner being red about anything at all."""
    target = tmp_path / "clean_tests"
    write_pytest_suite(target, 2, prefix="test_clean")
    runner = runner_with_targets(tmp_path, [str(target)], {str(target): 1},
                                 hook_tests=[], shell_tests=[])
    proc = _run(["bash", str(runner), str(REPO_ROOT)])
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, f"the clean control run was not green:\n{out}"
    assert f"{target}  intercepted=0" in out, (
        f"the per-target intercept line is missing — the accounting that the "
        f"other tests read is not actually printed:\n{out}")
    assert "plugin=1" in out, f"the plugin marker was not recorded:\n{out}"


def test_the_acknowledged_target_must_actually_intercept(tmp_path):
    """🔴 A LEDGER THAT ONLY PERMITS IS GREEN UNDER THE MUTANT THAT MATTERS.

    Acknowledge a target that reaches nothing. If the ledger merely tolerated
    intercepts, this would pass — and so would a guard that had been narrowed
    until it covered nothing at all, because "no intercepts anywhere" is the
    observable those two states SHARE. The required direction is what tells them
    apart, and it is the reason `scripts/tests` is the acknowledged one: it is
    the target that provably drives launchers into the stub.
    """
    target = tmp_path / "quiet_tests"
    write_pytest_suite(target, 2, prefix="test_quiet")
    runner = runner_with_targets(
        tmp_path, [str(target)], {str(target): 1},
        ack=[f"{target}|planted acknowledgement for a target that reaches nothing"],
        hook_tests=[], shell_tests=[])
    proc = _run(["bash", str(runner), str(REPO_ROOT)])
    out = proc.stdout + proc.stderr
    assert proc.returncode != 0, (
        f"an acknowledged target that intercepted NOTHING was accepted — the "
        f"ledger cannot tell a live guard from a dead one:\n{out}")
    assert "intercepted NOTHING" in out, (
        f"the run failed, but not for the dead-guard reason:\n{out}")


def test_the_runner_puts_the_stub_dir_FIRST_not_merely_on_path(tmp_path):
    """🔴 A SURVIVING MUTANT, closed: `PATH=$PATH:$NOLAUNCH_DIR`.

    "On PATH" is not the property that matters — an entry AFTER the ambient
    directories shadows nothing. The pytest targets do not observe this (their
    plugin prepends for itself); the NON-pytest targets do, because the runner's
    PATH is their ONLY protection.

    Measured without touching a real binary: a DECOY directory carrying its own
    `notify-send` is placed on PATH before the runner starts. Whoever wins the
    lookup is then observable — the stub (witness absent, intercepted=1) or the
    decoy (witness present). Under the mutant the decoy wins, which is exactly
    what a real launcher would do.
    """
    decoy = tmp_path / "decoy"
    decoy.mkdir()
    witness = tmp_path / "DECOY-WON"
    write_exec(decoy / "notify-send", f'printf won > "{witness}"\nexit 0\n')

    shell_test = tmp_path / "shell_probe.sh"
    shell_test.write_text('notify-send probe-from-a-shell-target\nexit 0\n',
                          encoding="utf-8")
    target = tmp_path / "plain_tests"
    write_pytest_suite(target, 1, prefix="test_plain")
    runner = runner_with_targets(tmp_path, [str(target)], {str(target): 1},
                                 hook_tests=[], shell_tests=[str(shell_test)])

    env = {**os.environ, "PATH": f"{decoy}{os.pathsep}{os.environ['PATH']}"}
    proc = subprocess.run(["bash", str(runner), str(REPO_ROOT)],
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          text=True, timeout=600, cwd=str(REPO_ROOT), env=env)
    out = proc.stdout + proc.stderr

    assert not witness.exists(), (
        "a directory that was on PATH BEFORE the runner started won the lookup, "
        "so the stub dir is merely ON PATH rather than FIRST — every non-pytest "
        f"target is unprotected:\n{out}")
    assert f"{shell_test}  intercepted=1" in out, (
        f"the shell target's reach was not intercepted and accounted:\n{out}")


# --------------------------------------------------------------------------- #
# The reach a PATH stub structurally CANNOT cover
# --------------------------------------------------------------------------- #
def test_the_absolute_path_reach_is_intercepted_in_process(tmp_path,
                                                           no_real_launchers):
    """🔴 PATH cannot shadow an ABSOLUTE path — so the guard stops keying on PATH.

    `scripts/browser-bridge/server.py` resolves `i3-msg` through
    `_I3_MSG_FALLBACKS` (`/run/current-system/sw/bin/i3-msg`,
    `~/.nix-profile/bin/i3-msg`) whenever `shutil.which` misses — which is
    exactly what a test that CLOBBERS PATH causes. No stub directory can ever
    shadow that, in either tier.

    The probe is a real executable of our own named `i3-msg`, invoked by
    absolute path. Its side effect (a file) is the positive control: if the
    in-process layer is not there, the file EXISTS — the same measurement at
    origin/main, where it does.
    """
    fake_dir = tmp_path / "elsewhere"
    fake_dir.mkdir()
    witness = tmp_path / "IT-RAN"
    write_exec(fake_dir / "i3-msg", f'printf ran > "{witness}"\nexit 0\n')

    before = len(nolaunch.recorded(no_real_launchers))
    subprocess.run([str(fake_dir / "i3-msg"), "workspace", "9"], check=False,
                   capture_output=True)

    assert not witness.exists(), (
        "the absolute-path launcher RAN. PATH cannot shadow an absolute path, "
        "so this is the reach the stub dir is structurally blind to")
    lines = nolaunch.recorded(no_real_launchers)[before:]
    assert any(ln.startswith("i3-msg(abs)") for ln in lines), (
        f"nothing was recorded for the absolute-path reach: {lines}")


def test_the_absolute_path_sites_are_pinned():
    """The set of ABSOLUTE launcher paths in the tree, pinned both ways.

    A stub directory cannot shadow these and the in-process layer only covers
    launches made by the pytest process itself — a CHILD process execing one is
    covered by NEITHER layer. So the SET is the guard: it fails when it grows
    (a new hole) and when it shrinks (the scan stopped seeing anything, which
    would make its reassuring emptiness worthless).

    ⚠ Stated limit, because this pin can be believed too much: the scan matches
    string LITERALS. `_I3_MSG_FALLBACKS`' second entry is assembled at runtime
    (`Path.home() / ".nix-profile" / "bin" / "i3-msg"`) and is invisible to it.
    """
    from testlib import launcher_scan
    sites = launcher_scan.absolute_launcher_sites(SCRIPTS)
    # 🔴 SELF-MATCH, handled explicitly rather than by excluding this file from
    # the scan. The pin below QUOTES the literals it pins, so this module is
    # itself a hit — the same trap `shebang_scan` builds its needles from
    # character codes to avoid. Popping it silently would be an exemption
    # nobody could see, so it is asserted: if this file's own literals change,
    # this line goes red too.
    own = sites.pop("tests/test_no_real_launchers_all_targets.py", None)
    assert own == ["/run/current-system/sw/bin/i3-msg", "/usr/bin/i3-msg"], (
        "this file's own quoted literals changed — the pin below and this "
        f"self-match acknowledgement have drifted apart: {own}")
    assert sites == {
        "browser-bridge/server.py": ["/run/current-system/sw/bin/i3-msg"],
        "browser-bridge/tests/test_server.py": [
            "/run/current-system/sw/bin/i3-msg", "/usr/bin/i3-msg"],
    }, (
        "the set of absolute-path launcher sites changed. Each one is a reach no "
        "PATH stub can shadow; a new entry needs a decision, not an update.\n"
        f"{sites}")


def test_the_in_process_layer_leaves_ordinary_subprocesses_alone(tmp_path):
    """The negative control for the interception above.

    A guard that rewrote argv indiscriminately would silently corrupt thousands
    of unrelated tests, and its damage would read as flakiness. Only a
    LEDGERED basename at an ABSOLUTE path is touched.
    """
    witness = tmp_path / "ordinary-ran"
    tool = tmp_path / "definitely-not-a-launcher"
    write_exec(tool, f'printf ran > "{witness}"\nexit 0\n')
    subprocess.run([str(tool)], check=False, capture_output=True)
    assert witness.read_text() == "ran"


@pytest.mark.parametrize("argv", [
    (["notify-send", "bare-name"]),              # PATH lookup — L1's job
    ([]),                                        # degenerate
])
def test_the_interception_declines_what_is_not_its_business(argv, tmp_path):
    """`_redirect_argv` is deliberately narrow; these must not be rewritten.

    A bare name is a PATH lookup, which the stub dir already owns — rewriting it
    here would HIDE the stub dir having stopped working, which is the one
    failure this file most needs to stay able to see.
    """
    from testlib import nolaunch_plugin
    assert nolaunch_plugin._redirect_argv(argv, tmp_path) is None
