"""Regression + invariant guards for `scripts/run-tests.sh`'s TARGET LIST.

WHY THIS EXISTS
---------------
#276 added a FILE to the runner's target list:

    scripts/claude-hooks/tests/test_guard_core.py

`run_pytest()` guarded with `if [ ! -d "$d" ]`, so it rejected the file and
reported it as ``FAIL … (missing directory)``. Consequences, all measured:

  * the pytest gate went **RED on main** and stayed red;
  * the **913 tests** in that file never ran;
  * the failure text ("missing directory") read like an ENVIRONMENT fault, so
    the natural reading was "the new #284 gate is noisy", not "a real target is
    being dropped on the floor".

The last point is the reason this file is a *named* guard rather than a bare
existence check. The next person to add a file, a glob, or a typo'd path must
get a failure that names their entry and says what is wrong with it.

WHAT IS REGRESSION COVERAGE HERE AND WHAT IS NOT
------------------------------------------------
Being honest about this, per claude/RULES.md ("a guard that pins an invariant
the bug never violated is an INVARIANT GUARD -- label it as one, don't count it
as regression coverage"):

  * ``test_check_targets_accepts_the_real_target_list`` is REGRESSION coverage.
    It is red on ``origin/main`` (see the module docstring's matrix in the PR):
    at ``origin/main`` the runner has no ``--check-targets`` at all, so the flag
    is swallowed as ROOT and the run dies ``cannot cd to ROOT=--check-targets``.

  * ``test_a_file_target_is_accepted`` is the DIRECT pin on the #276 symptom --
    it drives the runner's real acceptance path with a file target and asserts
    it is not rejected. Red before the fix for THIS guard's own reason.

  * ``test_hermetic_list_still_names_the_guard_core_file`` is an INVARIANT GUARD.
    It pins that nobody "fixes" a future failure by deleting the entry -- which
    is exactly what the runner's own error message warns against. The bug never
    violated it, so it is not regression coverage.

  * the bogus-entry / glob tests are REACHABILITY proofs for GUARD 5: they
    mutate a COPY of the runner and assert it goes red **naming that entry**,
    so a green result from the real list means the guard can actually fire.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
RUN_TESTS = REPO_ROOT / "scripts" / "run-tests.sh"

# The entry #276 added and the gate then silently refused to run.
GUARD_CORE_TARGET = "scripts/claude-hooks/tests/test_guard_core.py"


def _require_check_targets(runner: Path) -> None:
    """Fail FAST if the runner has no --check-targets flag.

    🔴 Without this the pre-fix behaviour is not just red, it is red SLOWLY and
    for the wrong reason: the old arg parser has no `--check-targets` case, so
    the flag falls through to `*) ROOT="$1"` and is silently swallowed — then the
    trailing ROOT argument overwrites it, `cd` succeeds, and the runner executes
    THE ENTIRE SUITE. Measured while building this test: each invocation ran the
    full hermetic set and blew the 120s subprocess timeout, so eight tests turned
    into eight full suite runs.

    A capability check on the source turns that into an instant, named failure.
    """
    src = runner.read_text()
    assert "--check-targets" in src, (
        f"{runner} has no --check-targets flag, so the target-list guard "
        "(GUARD 5) does not exist in this revision. This is the PRE-FIX state: "
        "run_pytest() guarded with `[ ! -d \"$d\" ]` and rejected the file "
        "target scripts/claude-hooks/tests/test_guard_core.py as a 'missing "
        "directory', taking the gate red with 913 tests unrun."
    )


def _run(args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", *args],
        cwd=str(cwd or REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=120,
    )


def _hermetic_targets() -> list[str]:
    """Parse HERMETIC_TARGETS out of the runner.

    Deliberately parsed from the shell source rather than re-listed here: a
    second hand-maintained copy of the list is how the list and its guard drift
    apart, which is the defect class this whole file is about.
    """
    src = RUN_TESTS.read_text()
    m = re.search(r"^HERMETIC_TARGETS=\((.*?)^\)", src, re.S | re.M)
    assert m, (
        "could not find a HERMETIC_TARGETS=( ... ) block in run-tests.sh. "
        "If the array was renamed again, update this parser -- do NOT delete "
        "the test, or the target list goes back to being unguarded."
    )
    targets = []
    for raw in m.group(1).splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        targets.append(line)
    assert targets, "parsed HERMETIC_TARGETS as EMPTY -- the parser is broken, not the list"
    return targets


# --------------------------------------------------------------------------
# Guard the guard: the parser must actually see the list.
# --------------------------------------------------------------------------

def test_parser_finds_a_plausible_target_list():
    """A positive control for _hermetic_targets().

    If this regex quietly stopped matching, every assertion below would iterate
    an empty list and pass vacuously -- a harness reporting success while
    testing nothing.
    """
    targets = _hermetic_targets()
    assert len(targets) >= 10, (
        f"only {len(targets)} targets parsed; the real list is much longer, so "
        "the parser is matching the wrong thing"
    )
    assert all(t.startswith("scripts/") for t in targets), targets


# --------------------------------------------------------------------------
# REGRESSION: the real list must be accepted by the real runner.
# --------------------------------------------------------------------------

def test_check_targets_accepts_the_real_target_list():
    """RED at origin/main (no --check-targets), GREEN after the fix.

    This is the end-to-end statement of the bug: every entry in the shipped
    target list must resolve to something pytest can run.
    """
    _require_check_targets(RUN_TESTS)
    proc = _run([str(RUN_TESTS), "--check-targets", str(REPO_ROOT)])
    assert proc.returncode == 0, (
        "run-tests.sh --check-targets rejected the shipped target list.\n"
        f"exit={proc.returncode}\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )


def test_hermetic_list_still_names_the_guard_core_file():
    """INVARIANT GUARD (not regression coverage).

    The runner's GUARD 5 error tells the reader "do NOT delete the entry to make
    this pass". This pins that advice so the 913 guard-core tests cannot be
    dropped from the gate as a way of turning it green.
    """
    targets = _hermetic_targets()
    assert GUARD_CORE_TARGET in targets, (
        f"{GUARD_CORE_TARGET} is no longer in HERMETIC_TARGETS. It holds ~913 "
        "tests covering the shared guard core behind bash-guard.py and "
        "opencode's plugin/guard.js. If the suite genuinely moved, update this "
        "constant; do not simply drop the entry."
    )
    assert (REPO_ROOT / GUARD_CORE_TARGET).is_file()


def test_a_file_target_is_accepted():
    """DIRECT pin on the #276 symptom: a FILE target must not be rejected.

    Drives the real runner's real acceptance path against a copy whose list is
    reduced to a single FILE. Before the fix this reported
    ``FAIL … (missing directory)``; the assertion names that string so a
    regression fails for THIS guard's reason and not a neighbouring one.
    """
    targets = _hermetic_targets()
    _require_check_targets(RUN_TESTS)
    file_targets = [t for t in targets if t.endswith(".py")]
    assert file_targets, (
        "the shipped list no longer contains any FILE target, so this test "
        "would silently stop covering the #276 case"
    )
    proc = _run([str(RUN_TESTS), "--check-targets", str(REPO_ROOT)])
    assert "missing directory" not in (proc.stdout + proc.stderr), (
        "the runner still describes a target as a 'missing directory' -- the "
        f"file/dir conflation is back.\n{proc.stdout}\n{proc.stderr}"
    )
    assert proc.returncode == 0, f"{proc.stdout}\n{proc.stderr}"


# --------------------------------------------------------------------------
# REACHABILITY: prove GUARD 5 can go red, naming the offending entry.
# --------------------------------------------------------------------------

def _runner_copy_with_extra_target(tmp_path: Path, entry: str) -> Path:
    """Copy run-tests.sh, injecting one extra entry into HERMETIC_TARGETS."""
    dst = tmp_path / "run-tests.sh"
    src = RUN_TESTS.read_text()
    patched, n = re.subn(
        r"^HERMETIC_TARGETS=\(\n",
        f"HERMETIC_TARGETS=(\n  {entry}\n",
        src,
        count=1,
        flags=re.M,
    )
    assert n == 1, "failed to inject a target into the copied runner"
    dst.write_text(patched)
    return dst


@pytest.mark.parametrize(
    "entry,needle",
    [
        ("scripts/does/not/exist/tests", "does not exist"),
        # A glob that matches NOTHING. A matching glob is expanded by bash
        # inside the array literal (measured: injecting scripts/tests/test_*.py
        # took the list 15 -> 32) and is harmless; the dangerous one is the
        # unmatched glob, which survives as a literal `*`.
        ("scripts/tests/test_zzz_no_such_*.py", "GLOB"),
        # Exists, is a regular file, is not pytest-collectable.
        ("scripts/run-tests.sh", "pytest-collectable"),
    ],
)
def test_check_targets_rejects_a_bad_entry_by_name(tmp_path, entry, needle):
    """Break it on purpose and confirm GUARD 5 goes red FOR ITS OWN REASON.

    Each case asserts BOTH that the run fails AND that the offending entry is
    named in the output -- "it failed" alone would also be satisfied by an
    unrelated guard tripping first, which is the failure mode claude/RULES.md
    calls out ("a DIFFERENT guard's error kills your test").
    """
    _require_check_targets(RUN_TESTS)
    runner = _runner_copy_with_extra_target(tmp_path, entry)
    proc = _run([str(runner), "--check-targets", str(REPO_ROOT)])
    out = proc.stdout + proc.stderr
    assert proc.returncode == 2, (
        f"expected GUARD 5 to fail with exit 2 for {entry!r}, got "
        f"{proc.returncode}.\n{out}"
    )
    assert entry in out, f"GUARD 5 failed but never named {entry!r}.\n{out}"
    assert needle in out, (
        f"GUARD 5 named {entry!r} but not WHY (expected {needle!r}).\n{out}"
    )


def test_check_targets_is_cheap_and_runs_no_tests(tmp_path):
    """--check-targets must not execute a suite.

    If it ever started running pytest, the reachability tests above would take
    minutes each and would be silently disabled by the first person who noticed.
    MEASURED at the pre-fix revision: the flag was swallowed as ROOT and every
    invocation ran the FULL hermetic set, blowing a 120s timeout per test.
    """
    _require_check_targets(RUN_TESTS)
    proc = _run([str(RUN_TESTS), "--check-targets", str(REPO_ROOT)])
    assert "=== pytest " not in proc.stdout, (
        "--check-targets ran a suite; it is supposed to validate the list only.\n"
        f"{proc.stdout}"
    )


# --------------------------------------------------------------------------
# The DEV-HOST split, which had no behavioural coverage while it was empty.
# --------------------------------------------------------------------------

def _devhost_targets() -> list[str]:
    """Parse DEVHOST_TARGETS out of the runner, same contract as its sibling."""
    src = RUN_TESTS.read_text()
    m = re.search(r"^DEVHOST_TARGETS=\((.*?)^\)", src, re.S | re.M)
    assert m, ("could not find a DEVHOST_TARGETS=( ... ) block in run-tests.sh. "
               "If the array was renamed, update this parser — do NOT delete "
               "the test, or the dev-host tier goes back to being unguarded.")
    return [ln.strip() for ln in m.group(1).splitlines()
            if ln.strip() and not ln.strip().startswith("#")]


def test_a_devhost_target_is_selected_by_set_all_and_NOT_by_set_hermetic(tmp_path):
    """🔴 THE MECHANISM, not the list. `DEVHOST_TARGETS` sat EMPTY from the day
    it was written until 2026-08-21, so nothing had ever shown that a target
    placed in it (a) runs under `--set all` and (b) does NOT run under
    `--set hermetic`. Both halves matter and they fail in opposite directions:
    lose (a) and a suite is declared dev-host and then never runs anywhere;
    lose (b) and it reds the hermetic flake gate it was moved out of.

    Driven against a patched runner with two THROWAWAY targets of different,
    known sizes, so the claim is read off the collected counts rather than off
    the presence of a name in a log.
    """
    from testlib.runner_patch import runner_with_targets, write_pytest_suite

    herm = tmp_path / "herm_suite"
    dev = tmp_path / "dev_suite"
    write_pytest_suite(herm, 3, prefix="test_h")
    write_pytest_suite(dev, 7, prefix="test_d")

    runner = runner_with_targets(
        tmp_path,
        [str(herm)],
        floors={str(herm): 1, str(dev): 1},
        devhost=[str(dev)],
    )

    only = _run([str(runner), "--set", "hermetic", str(REPO_ROOT)])
    both = _run([str(runner), "--set", "all", str(REPO_ROOT)])

    def collected(proc):
        m = re.search(r"TOTAL collected=(\d+)", proc.stdout + proc.stderr)
        assert m, ("no TOTAL collected= line — the patched runner did not "
                   f"complete:\n{proc.stdout}\n{proc.stderr}")
        return int(m.group(1))

    assert collected(only) == 3, (
        "the hermetic set ran %d tests, not the 3-test hermetic target alone — "
        "a dev-host target is leaking into the tier that gates merges\n%s"
        % (collected(only), only.stdout)
    )
    assert collected(both) == 10, (
        "`--set all` ran %d tests, not 3 + 7 — the dev-host target was declared "
        "and then never executed by any tier, which is worse than not declaring "
        "it\n%s" % (collected(both), both.stdout)
    )


def test_the_activation_order_suite_is_gated_by_the_HERMETIC_tier():
    """🔴 THIS TEST USED TO ASSERT THE OPPOSITE, and the inversion is the finding.

    It pinned `scripts/tests-devhost` INTO the dev-host list, on the argument
    that the activation-DAG guard "cannot be hermetic" because `nix eval` cannot
    run in the build sandbox. The mechanism was real; the conclusion was not —
    the DAG is a pure value, so flake.nix evaluates it and injects the JSON, and
    the suite is now `scripts/tests/test_activation_order.py`.

    What the old arrangement cost, measured 2026-08-21: a dev-host target runs
    in ONE tier (`--set all`, the pre-push hook), that hook was not installed on
    the workbench, and its file filter excluded `nix/` — the very directory the
    guard watches. A test pinned into a tier that does not run is not a weaker
    guard than a hermetic one; it is not a guard.

    So the assertion is now that the file exists in the hermetic tier's reach
    and NOT in the dev-host list. `scripts/tests` is a directory target, so
    membership is checked by the file being under it — a name check against the
    list alone would pass for a file that had been deleted.
    """
    suite = REPO_ROOT / "scripts" / "tests" / "test_activation_order.py"
    assert suite.is_file(), (
        "the activation-order suite is gone. ZERO steps in the delete/relink "
        "window would then be pinned only by a text match on nix/home.nix, "
        "which is the state PR #630 exists to end.")
    assert "scripts/tests" in _hermetic_targets(), (
        "scripts/tests is no longer a hermetic target, so the activation-order "
        "suite is collected by no tier: %s" % _hermetic_targets())
    assert "scripts/tests-devhost" not in _devhost_targets(), (
        "scripts/tests-devhost is back in the dev-host list. That tier runs "
        "only under `--set all` — the pre-push hook — which is exactly the "
        "single point of failure this was moved out of: %s" % _devhost_targets())
    assert "DEVRC_ACTIVATION_DAG_JSON" in (REPO_ROOT / "flake.nix").read_text(), (
        "flake.nix no longer injects the evaluated DAG, so the suite above "
        "cannot measure anything in the sandbox — it would fall back to a "
        "`nix eval` that cannot work there.")
