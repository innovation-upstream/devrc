"""🔴 No target may write to the REAL activity spool — and the guard PROVES it
covered every target, rather than asserting that a variable was set once.

WHY THIS FILE EXISTS
--------------------
`scripts/collector/invocation.py` appends a `source=tool kind=invocation` row to
`<ACTIVITY_SPOOL_DIR>/current.log` on every instrumented tool run, and the
activity collector ships that spool to the production ClickHouse
`activity.events`. `spool_emit.default_spool_dir()` resolves the variable at
call time and otherwise falls back to
`${XDG_STATE_HOME:-~/.local/state}/activity/spool` — so a test that simply runs
a tool writes a real row into the operator's own dataset.

#614 closed `scripts/browser-bridge/tests` with a conftest. A full
`scripts/gate.sh --tier pytest` run still leaked, from targets that have no
conftest at all — the identical shape as #399's no-real-launcher guard, which
was installed from ONE conftest and protected 1 target of 17. So GUARD 8 is
attached where GUARD 7 is: the two env exports in `scripts/run-tests.sh`, on the
single line every target goes through, which also covers the non-pytest
`HOOK_TESTS`/`SHELL_TESTS` that no conftest can ever reach.

WHAT IS REGRESSION COVERAGE HERE AND WHAT IS NOT (RULES.md asks for the label):

  * `test_an_emitting_target_writes_to_the_run_dir_not_the_home_spool` is THE
    regression test for the measured leak. It is RED at origin/main — where the
    planted emit lands in `$HOME/.local/state/activity/spool/current.log` — and
    green here, with `spool-rows=1` as its own positive control that the emit
    really happened.
  * `test_a_target_that_drops_the_variable_is_named_and_red` and
    `test_a_non_pytest_target_that_leaks_is_named_and_red` are REGRESSION
    coverage for the residual hazard the exports alone do not close: code that
    takes `ACTIVITY_SPOOL_DIR` away and drops through to the fallback. At
    origin/main both write to the real spool and the run is green.
  * `test_the_missing_plugin_is_named_and_red` and
    `test_an_unarmed_fallback_trap_is_named_and_red` are the MUTATION tests for
    the two mutants that matter, both of which leave a green suite: the guard
    loading in NO target, and the leak detector pointing at a directory nothing
    writes to. Each asserts THIS guard's own message, not merely that the run
    went red.
  * `test_a_nested_pytest_session_does_not_write_into_the_targets_ledger` is
    REGRESSION coverage for a defect this guard found in ITSELF on its first
    full run: `scripts/tests` reported `plugin=3`, because
    `test_no_real_launchers.py` copies `scripts/tests/conftest.py` into nested
    control/mutant sessions. Its sibling
    `test_the_positive_control_for_that_is_a_nested_session_that_DOES_count`
    exists so the `plugin=1` it asserts is not satisfied by a nested session
    that never loaded the plugin — it removes the nesting flag from the child
    environment and watches the count move to 2.
  * `test_the_control_of_those_mutants_is_green` is the positive control for all
    four, and pins the accounting line the others read.
  * the two-way token pin, the single-invocation pin and the accounting-site
    count are INVARIANT GUARDS. The bug never violated them; they exist so the
    enforcement point cannot be narrowed to nothing by an edit that still looks
    like a guard.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from testlib import spool_plugin  # noqa: E402
from testlib.runner_patch import runner_with_targets, write_pytest_suite  # noqa: E402

RUN_TESTS = SCRIPTS / "run-tests.sh"
RUNNER_SRC = RUN_TESTS.read_text(encoding="utf-8")
# 🔴 CODE ONLY. run-tests.sh is 40% commentary and GUARD 8's header quotes every
# token these pins look for — the variable names, the plugin flag, the marker
# string. A pin read off the raw text answers a question about the PROSE.
RUNNER_CODE = "\n".join(ln for ln in RUNNER_SRC.splitlines()
                        if not ln.lstrip().startswith("#"))

COLLECTOR = SCRIPTS / "collector"

# Assembled, not spelled, so this file's own text cannot satisfy a pin that
# greps the runner for it.
_SPOOL_FLAG = "-p testlib.spool" + "_plugin"
_NOLAUNCH_FLAG = "-p testlib.nolaunch" + "_plugin"


# --------------------------------------------------------------------------- #
# Planted probes. Both go through `invocation.emit_invocation` — the REAL leak
# shape, not a stand-in for it: that is the function whose rows were measured in
# production.
# --------------------------------------------------------------------------- #
_EMITTING_TEST = f'''\
import sys

sys.path.insert(0, {str(COLLECTOR)!r})
import invocation


def test_emits_activity_telemetry():
    # The ordinary shape: a test that drives an instrumented tool. It does not
    # know or care where the spool is; the runner decided that.
    assert invocation.emit_invocation("spool-probe", "ok") != ""
'''

_DROPPING_TEST = f'''\
import os
import sys

sys.path.insert(0, {str(COLLECTOR)!r})
import invocation


def test_emits_after_dropping_the_spool_variable():
    # The residual hazard the exports alone do not close: something takes the
    # variable away (monkeypatch.delenv, a hand-built subprocess env, a fixture
    # teardown restoring an unset ambient) and the write drops to the FALLBACK.
    os.environ.pop("ACTIVITY_SPOOL_DIR", None)
    assert invocation.emit_invocation("spool-probe", "dropped") != ""
'''

_DROPPING_SCRIPT = f'''\
import os
import sys

sys.path.insert(0, {str(COLLECTOR)!r})
import invocation

os.environ.pop("ACTIVITY_SPOOL_DIR", None)
assert invocation.emit_invocation("spool-probe", "dropped") != ""
'''


# A target whose test STARTS ANOTHER PYTEST that loads this plugin — the shape
# `test_no_real_launchers.py` uses for its control/mutant pairs, and the shape
# that first made `scripts/tests` report three session markers. `{extra}` is
# substituted with either "" (inherit the nesting flag) or the code that removes
# it, which is the whole control pair.
_NESTING_TEST = '''\
import os
import subprocess
import sys
from pathlib import Path


def test_starts_a_nested_pytest_session(tmp_path):
    inner = tmp_path / "inner"
    inner.mkdir()
    (inner / "test_inner.py").write_text("def test_ok():\\n    assert True\\n")
    env = dict(os.environ)
    {extra}
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", str(inner), "-q", "-p", "no:cacheprovider",
         "-p", "testlib.spool_plugin", "--no-header"],
        capture_output=True, text=True, env=env)
    # The nested session must really have RUN and really have loaded the plugin,
    # or "it wrote no marker" is satisfied by it never starting.
    assert "1 passed" in proc.stdout, proc.stdout + proc.stderr
'''


def _pytest_invocations(src: str) -> list[str]:
    """The lines that actually RUN pytest — not the ones that talk about it."""
    return [ln for ln in src.splitlines()
            if ln.strip().startswith("python -m pytest")
            and "--version" not in ln]


def _run(args: list[str], env: dict | None = None,
         timeout: int = 300) -> subprocess.CompletedProcess:
    full = {**os.environ, **(env or {})}
    for k, v in list(full.items()):
        if v is None:
            del full[k]
    return subprocess.run(["bash", *args], capture_output=True, text=True,
                          timeout=timeout, cwd=str(REPO_ROOT), env=full)


def _clean_home(tmp_path: Path) -> dict:
    """A scratch HOME with XDG_STATE_HOME REMOVED from the child environment.

    This is the measurement method, and it is sound for one reason: it moves the
    root of the SAME fallback `spool_emit.default_spool_dir()` computes, without
    changing the code path at all. A row that would go to the operator's
    `~/.local/state/activity/spool` goes to `<tmp>/.local/state/activity/spool`
    instead — so "did this leak" becomes a question about a directory this test
    owns, and the production spool is never touched, read or relied upon.

    XDG_STATE_HOME must be removed rather than set: it takes precedence over
    HOME in the fallback, so leaving the outer gate run's value in place would
    point the probe at the OUTER runner's trap and every measurement here would
    read zero for a reason that has nothing to do with the tree under test.
    """
    home = tmp_path / "scratch-home"
    home.mkdir(parents=True, exist_ok=True)
    return {"HOME": str(home), "XDG_STATE_HOME": None,
            "ACTIVITY_SPOOL_DIR": None}


def _home_spool(tmp_path: Path) -> Path:
    return (tmp_path / "scratch-home" / ".local" / "state" / "activity"
            / "spool" / "current.log")


# --------------------------------------------------------------------------- #
# INVARIANT GUARDS — the enforcement point cannot be narrowed to nothing
# --------------------------------------------------------------------------- #
def test_the_single_pytest_invocation_loads_the_spool_plugin():
    """One `python -m pytest` line, carrying BOTH guards' plugins.

    That is what makes "every target" true by construction rather than by two
    dozen conftests that drift — and what protects the target added next month.
    """
    invocations = _pytest_invocations(RUNNER_CODE)
    assert len(invocations) == 1, (
        "run-tests.sh must invoke pytest from exactly ONE place — both guards "
        f"are attached to that line. Found {len(invocations)}: {invocations}")
    assert _SPOOL_FLAG in invocations[0], (
        "the single pytest invocation no longer loads the spool-isolation "
        f"plugin, so no target is accounted:\n  {invocations[0].strip()}")
    assert _NOLAUNCH_FLAG in invocations[0], (
        "the no-real-launcher plugin was dropped from the same line — GUARD 8's "
        f"edit must not cost GUARD 7:\n  {invocations[0].strip()}")


def test_both_levers_are_exported_by_the_runner():
    """ACTIVITY_SPOOL_DIR alone is not the guard.

    It is the direct lever; XDG_STATE_HOME governs the FALLBACK a test reaches
    by taking the direct one away. Exporting only the first leaves that path
    pointed at the operator's real spool — and leaves the leak DETECTOR pointed
    there too, which is the worse half.
    """
    assert re.search(r"^export ACTIVITY_SPOOL_DIR=", RUNNER_CODE, re.M), (
        "run-tests.sh no longer exports ACTIVITY_SPOOL_DIR; every target writes "
        "to whatever the ambient environment says, which in a gate run is the "
        "operator's real spool")
    assert re.search(r"^export XDG_STATE_HOME=", RUNNER_CODE, re.M), (
        "run-tests.sh no longer exports XDG_STATE_HOME; the fallback resolves "
        "to ~/.local/state/activity/spool again")


def test_the_runner_and_the_plugin_agree_on_the_shared_tokens():
    """The three strings crossing the process boundary, pinned BOTH ways.

    The runner greps for them in bash; the plugin writes them in Python.
    Renaming either side alone leaves the accounting matching nothing — and a
    grep that matches nothing prints a clean run, which is the failure that
    looks most like success.
    """
    for var, value in (("SPOOL_SESSION_MARKER", spool_plugin.SESSION_MARKER),
                       ("SPOOL_CONTROL_SOURCE", spool_plugin.CONTROL_SOURCE),
                       ("SPOOL_CONTROL_OK", spool_plugin.CONTROL_EMITTED)):
        assert f'{var}="{value}"' in RUNNER_CODE, (
            f"run-tests.sh's {var} does not match spool_plugin's "
            f"'{value}'. The accounting would silently match nothing.")


def test_every_target_kind_feeds_the_accounting():
    """Three call sites: pytest targets, hook scripts, shell scripts.

    A loop that stopped accounting reports no leaks for the most reassuring
    possible reason. (`TARGETS` membership additionally fails any pytest target
    that produced no record at all.)
    """
    assert RUNNER_CODE.count('_spool_account "') == 3, (
        "expected exactly three sites to feed SPOOL_SEEN (pytest, hook scripts, "
        "shell scripts)")
    assert RUNNER_CODE.count("_spool_mark_before") == 4, (
        "each accounting site needs its matching before-mark (plus the "
        "definition); an unpaired one attributes a leak to the wrong target")


# --------------------------------------------------------------------------- #
# 🔴 THE REGRESSION TEST — the measured leak, in the ordinary shape
# --------------------------------------------------------------------------- #
def test_an_emitting_target_writes_to_the_run_dir_not_the_home_spool(tmp_path):
    """🔴 PLANT A REAL EMIT IN A TARGET AND FOLLOW THE ROW.

    At origin/main this run is GREEN and the row is sitting in
    `$HOME/.local/state/activity/spool/current.log`, on its way to production.
    Here the run is green AND that file does not exist, because the row went to
    the runner's own dir instead.

    `spool-rows=1` is this test's positive control. Without it, "no file under
    HOME" is satisfied by an emit that never happened — the reassuring zero of a
    probe wired to nothing.
    """
    target = tmp_path / "emitting_tests"
    write_pytest_suite(target, 1, prefix="test_filler")
    (target / "test_emit.py").write_text(_EMITTING_TEST, encoding="utf-8")
    runner = runner_with_targets(tmp_path, [str(target)], {str(target): 1},
                                 hook_tests=[], shell_tests=[])
    proc = _run([str(runner), str(REPO_ROOT)], env=_clean_home(tmp_path))
    out = proc.stdout + proc.stderr

    assert proc.returncode == 0, f"an isolated emit should not fail the run:\n{out}"
    assert f"{target}  spool-rows=1" in out, (
        "the planted emit produced no row in the runner's spool dir — the probe "
        f"never fired, so the assertion below would prove nothing:\n{out}")
    leaked = _home_spool(tmp_path)
    assert not leaked.exists(), (
        f"the emit reached the REAL spool fallback at {leaked}:\n"
        f"{leaked.read_text(encoding='utf-8')[:500]}")


def test_a_target_that_drops_the_variable_is_named_and_red(tmp_path):
    """🔴 THE RESIDUAL HAZARD: code that takes ACTIVITY_SPOOL_DIR away.

    The exports cannot stop that — but the fallback TRAP catches it, and the
    accounting names the target. At origin/main the same row lands in the real
    spool and the run is green.
    """
    target = tmp_path / "dropping_tests"
    write_pytest_suite(target, 1, prefix="test_filler")
    (target / "test_drop.py").write_text(_DROPPING_TEST, encoding="utf-8")
    runner = runner_with_targets(tmp_path, [str(target)], {str(target): 1},
                                 hook_tests=[], shell_tests=[])
    proc = _run([str(runner), str(REPO_ROOT)], env=_clean_home(tmp_path))
    out = proc.stdout + proc.stderr

    assert proc.returncode != 0, f"a planted fallback write produced a PASSING run:\n{out}"
    assert "GUARD 8 problem" in out, (
        f"the run failed, but not for the spool reason:\n{out}")
    assert str(target) in out, f"the failure did not NAME the target:\n{out}"
    assert "REAL spool fallback" in out, out
    assert "source=tool" in out, (
        f"the guard reported a leak whose rows it never actually recorded:\n{out}")
    assert not _home_spool(tmp_path).exists(), (
        "the trap did not contain the row — it reached the real fallback")


def test_a_non_pytest_target_that_leaks_is_named_and_red(tmp_path):
    """🔴 THE HALF NO CONFTEST CAN EVER COVER.

    `HOOK_TESTS` are plain scripts, not pytest: they load no plugin and could
    not be reached by a per-directory fixture at all. Their protection is the
    runner's exports, and their accounting is the same trap.
    """
    target = tmp_path / "clean_tests"
    write_pytest_suite(target, 1, prefix="test_clean")
    script = tmp_path / "leaky_hook.py"
    script.write_text(_DROPPING_SCRIPT, encoding="utf-8")
    runner = runner_with_targets(tmp_path, [str(target)], {str(target): 1},
                                 hook_tests=[str(script)], shell_tests=[])
    proc = _run([str(runner), str(REPO_ROOT)], env=_clean_home(tmp_path))
    out = proc.stdout + proc.stderr

    assert proc.returncode != 0, f"a leaking hook script produced a PASSING run:\n{out}"
    assert "GUARD 8 problem" in out, f"the run failed, but not for the spool reason:\n{out}"
    assert str(script) in out, f"the failure did not NAME the script:\n{out}"
    assert not _home_spool(tmp_path).exists()


# --------------------------------------------------------------------------- #
# 🔴 MUTATION TESTS — the two mutants that leave a green suite
# --------------------------------------------------------------------------- #
def test_the_missing_plugin_is_named_and_red(tmp_path):
    """🔴 MUTANT 1: the guard loading in NO target, silently.

    Strip `-p testlib.spool_plugin` — the shape of every "narrow the glob /
    comment it out" mutation. The session marker is what makes it observable:
    without the marker requirement this target's `fallback-leaked=0` is
    indistinguishable from real protection, and the run stays green.
    """
    target = tmp_path / "plain_tests"
    write_pytest_suite(target, 2, prefix="test_plain")
    runner = runner_with_targets(tmp_path, [str(target)], {str(target): 1},
                                 hook_tests=[], shell_tests=[])
    src = runner.read_text(encoding="utf-8")
    # 🔴 The CODE line, not the prose. GUARD 8's own failure message names the
    # flag, so a blanket replace would edit a string literal and leave the
    # invocation untouched — a mutation that never happened, scored SURVIVED.
    hits = _pytest_invocations(src)
    assert len(hits) == 1 and _SPOOL_FLAG in hits[0], (
        f"expected exactly one live site to mutate, found {hits}")
    runner.write_text(src.replace(hits[0], hits[0].replace(" " + _SPOOL_FLAG, "")),
                      encoding="utf-8")

    proc = _run([str(runner), str(REPO_ROOT)], env=_clean_home(tmp_path))
    out = proc.stdout + proc.stderr
    assert proc.returncode != 0, (
        f"a target that ran with NO spool guard produced a PASSING run:\n{out}")
    assert "session marker" in out, (
        f"the run failed, but not because the plugin was missing:\n{out}")


def test_an_unarmed_fallback_trap_is_named_and_red(tmp_path):
    """🔴 MUTANT 2: the leak DETECTOR wired to nothing.

    Drop `export XDG_STATE_HOME` and the fallback resolves to the operator's
    real `~/.local/state/activity/spool` again. Every leak then bypasses the
    trap, and `fallback-leaked=0` becomes the reassuring zero of a counter
    watching an empty directory — while the suite stays green.

    The plugin is FAIL-CLOSED about it: an uncontained fallback means its
    control row is WITHHELD rather than written to production, and the withheld
    control is what the runner turns into this failure. A guard that wrote to
    production to prove it does not write to production would be worse than
    none — so the target here is CLEAN, and nothing plants an emit.
    """
    target = tmp_path / "plain_tests"
    write_pytest_suite(target, 2, prefix="test_plain")
    runner = runner_with_targets(tmp_path, [str(target)], {str(target): 1},
                                 hook_tests=[], shell_tests=[])
    src = runner.read_text(encoding="utf-8")
    mutated, n = re.subn(r'^export XDG_STATE_HOME="\$SPOOL_TRAP"$',
                         ': "$SPOOL_TRAP"', src, count=1, flags=re.M)
    assert n == 1, "the XDG_STATE_HOME export was not found to mutate"
    runner.write_text(mutated, encoding="utf-8")

    proc = _run([str(runner), str(REPO_ROOT)], env=_clean_home(tmp_path))
    out = proc.stdout + proc.stderr
    assert proc.returncode != 0, (
        f"an unarmed fallback trap produced a PASSING run:\n{out}")
    assert "UNARMED" in out, (
        f"the run failed, but not because the detector was pointed away:\n{out}")
    assert not _home_spool(tmp_path).exists(), (
        "the withheld control was not actually withheld — the guard wrote a row "
        "down the very path it was reporting as uncontained")


def _nesting_runner(tmp_path: Path, extra: str):
    target = tmp_path / "nesting_tests"
    write_pytest_suite(target, 1, prefix="test_filler")
    (target / "test_nest.py").write_text(_NESTING_TEST.format(extra=extra),
                                         encoding="utf-8")
    runner = runner_with_targets(tmp_path, [str(target)], {str(target): 1},
                                 hook_tests=[], shell_tests=[])
    proc = _run([str(runner), str(REPO_ROOT)], env=_clean_home(tmp_path))
    return target, proc.stdout + proc.stderr, proc.returncode


def test_a_nested_pytest_session_does_not_write_into_the_targets_ledger(tmp_path):
    """🔴 MEASURED DEFECT, closed: `scripts/tests` reported plugin=3.

    `test_no_real_launchers.py` builds control/mutant pairs by copying
    `scripts/tests/conftest.py` into a throwaway tree and running pytest there.
    Those child sessions load this plugin and inherit the guard dir, so each one
    appended a marker to the runner's ledger — and "3 markers, expected 1" reads
    as "this target ran WITHOUT the guard", which is a false red on the one
    check that exists to catch a true one.

    A nested session is still ISOLATED; it is only silent in the ledger.
    """
    target, out, rc = _nesting_runner(tmp_path, extra="")
    assert rc == 0, f"a target that starts a nested pytest went red:\n{out}"
    assert f"{target}  spool-rows=0  fallback-leaked=0  control=1  plugin=1" in out, (
        f"the nested session polluted the target's accounting:\n{out}")


def test_the_positive_control_for_that_is_a_nested_session_that_DOES_count(tmp_path):
    """The other half of the pair — without it, `plugin=1` above is satisfied by
    a nested session that never loaded the plugin at all.

    Same planted target, one difference: the child environment has the nesting
    flag REMOVED, so that session believes it is a root. The marker count moves
    to 2 and the runner goes red with its own message. That is the number moving
    on a case that MUST produce it.
    """
    target, out, rc = _nesting_runner(
        tmp_path, extra='env.pop("DEVRC_TEST_SPOOL_IN_SESSION", None)')
    assert rc != 0, (
        "a second session marker was accepted — the marker count cannot move, "
        f"so plugin=1 in the sibling test proves nothing:\n{out}")
    assert "emitted 2 session marker(s)" in out, (
        f"the run went red, but not on the marker count:\n{out}")


def test_the_control_of_those_mutants_is_green(tmp_path):
    """The positive control for all four behavioural tests above.

    Unmutated, a plain target with no emit PASSES — and prints the accounting
    line the others read, with the control row beside the zero. Without this,
    "the run went red" is satisfied by the runner being red about anything.
    """
    target = tmp_path / "clean_tests"
    write_pytest_suite(target, 2, prefix="test_clean")
    runner = runner_with_targets(tmp_path, [str(target)], {str(target): 1},
                                 hook_tests=[], shell_tests=[])
    proc = _run([str(runner), str(REPO_ROOT)], env=_clean_home(tmp_path))
    out = proc.stdout + proc.stderr

    assert proc.returncode == 0, f"the clean control run was not green:\n{out}"
    assert (f"{target}  spool-rows=0  fallback-leaked=0  control=1  plugin=1"
            in out), (
        "the per-target spool line is missing or its shape changed — the "
        f"accounting the other tests read is not actually printed:\n{out}")
    assert not _home_spool(tmp_path).exists()
