"""Guards for `scripts/run-tests.sh`'s PER-TARGET collected-test floors.

WHY THIS EXISTS
---------------
The runner used to carry ONE literal, ``MIN_TESTS=<exact total>``, and it was
the most conflict-prone line in the repo. On 2026-08-11 alone it took eleven
values across eight PRs -- 6643, 6770, 6897, 6960, 6993, 7122, 7127, 7138,
7143, 7147, 7168 -- because a total is BASE-DEPENDENT: every branch measured it
against a base the others never saw, so every value was correct when written and
stale within hours. Three agents reconciled it by hand in one day.

Worse, ``rerere.enabled`` is true in this repo. On one sync it replayed a
resolution recorded against a DIFFERENT merge of the same conflict and silently
wrote 7168 -- the total for a four-way integration tree -- onto a two-way tree
whose real total was 7143, announcing only ``Resolved ... using previous
resolution``. rerere matches conflict-hunk TEXT, not tree membership; on a
base-dependent constant that is reliably wrong while looking like a clean
auto-resolve, and the direction it was wrong in (floor ABOVE the real count) is
a FALSE RED.

The literal is gone. Each target carries its own floor, set below its measured
count by ``min(50, max(1, m/20))``, and the global floor is the SUM -- computed,
never written.

WHAT IS REGRESSION COVERAGE HERE AND WHAT IS NOT
------------------------------------------------
Per `claude/RULES.md` ("a guard that pins an invariant the bug never violated is
an INVARIANT GUARD -- label it as one"):

  * ``test_no_hand_written_global_total_remains`` and
    ``test_the_global_floor_is_the_sum_of_the_per_target_floors`` are REGRESSION
    coverage for the defect above. Both are RED at origin/main, where
    ``MIN_TESTS="${MIN_TESTS:-7168}"`` is a hand-written total and there is no
    ``--check-floors`` at all.

  * ``test_a_target_below_its_floor_is_named`` and
    ``test_a_target_far_above_its_floor_is_named`` are REACHABILITY proofs for
    the two new guards: each forces the exact state and asserts the run goes red
    with THAT guard's own message, not merely that it went red.

  * ``test_the_collapse_guard_is_what_makes_that_test_red`` is the MUTATION
    test. It breaks the comparison in a copy and asserts the collapse case goes
    GREEN -- without it, "the run failed" would also be satisfied by any of the
    five other guards firing first.

  * ``test_ordinary_growth_needs_no_hand_edit`` is the property the whole change
    was made FOR, measured rather than asserted: a target that grows stays green
    with the floor untouched.

  * the two-way-pin tests are INVARIANT GUARDS. The bug never violated them (the
    table did not exist), so they are not regression coverage for it; they exist
    because an unpinned target would run under no floor at all, which is the
    ungated-suite shape this repo has now been bitten by four times.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))
from testlib.runner_patch import (  # noqa: E402
    patch_runner_source,
    runner_with_targets,
    write_pytest_suite,
)

RUN_TESTS = REPO_ROOT / "scripts" / "run-tests.sh"


def _run(args: list[str], timeout: int = 300, env: dict | None = None) -> subprocess.CompletedProcess:
    full_env = None
    if env is not None:
        full_env = {**os.environ, **env}
    return subprocess.run(
        ["bash", *args],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=timeout,
        env=full_env,
    )


# The global floor is the SUM of the selected targets' floors, so on a
# single-target copy it equals that target's floor and fires on exactly the same
# collapse. That redundancy is real coverage in the full run -- but it makes a
# single-target fixture unable to say WHICH guard went red. Setting MIN_TESTS=1
# (the documented one-off override) silences the global one so the per-target
# guard is measured on its own.
ONLY_PER_TARGET = {"MIN_TESTS": "1"}


def _suggested_floor(m: int) -> int:
    """The rule, restated here ONLY so the test can check the script agrees.

    This is a second, independent implementation on purpose: if it silently
    tracked the shell version there would be nothing pinning the shell version
    at all.
    """
    return m - min(50, max(1, m // 20))


# --------------------------------------------------------------------------
# The two-way pin (INVARIANT GUARDS).
# --------------------------------------------------------------------------

def test_check_floors_accepts_the_real_table():
    """POSITIVE CONTROL. Every other test here rests on --check-floors being
    able to report success, so prove it can before reading any red from it."""
    proc = _run([str(RUN_TESTS), "--check-floors", str(REPO_ROOT)])
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, f"--check-floors is red on the real table.\n{out}"
    assert "floor(s) pin a" in proc.stdout, out
    assert "GLOBAL floor" in proc.stdout, out


def test_the_global_floor_is_the_sum_of_the_SELECTED_per_target_floors():
    """REGRESSION. The total must be DERIVED, never written.

    Red at origin/main: there is no --check-floors, and the total is the
    hand-written literal whose eleven values in one day motivated this change.

    🔴 SELECTED, not all — and this test USED to say "all", which was true only
    by accident. The floor table pins every KNOWN target (hermetic + dev-host)
    while the global floor sums the targets `--set` actually runs; those two
    sets were identical for as long as DEVHOST_TARGETS was empty, so the
    distinction never showed. The moment it gained an entry this went red with
    `printed=13034 sum=13038` — a correct global floor failing a test that had
    quietly assumed a second invariant nobody had stated.

    So the listing now MARKS the unsummed lines and this asserts the real
    property, in both directions: the sum of the unmarked floors IS the printed
    global, and the marked ones are genuinely excluded from it. The second half
    is what stops the marker becoming a way to hide a floor from the sum.
    """
    proc = _run([str(RUN_TESTS), "--check-floors", str(REPO_ROOT)])
    assert proc.returncode == 0, proc.stdout + proc.stderr
    summed = [int(m) for m in
              re.findall(r"^  floor (\d+)  \S+$", proc.stdout, re.M)]
    excluded = [int(m) for m in
                re.findall(r"^  floor (\d+)  \S+  \[NOT SUMMED: ", proc.stdout, re.M)]
    assert summed, ("parsed NO summed per-target floors -- the parser is broken, "
                    f"not the table.\n{proc.stdout}")
    m = re.search(r"GLOBAL floor \(sum over the \w+ set\) = (\d+)", proc.stdout)
    assert m, proc.stdout
    assert int(m.group(1)) == sum(summed), (
        "the printed global floor is not the sum of the SELECTED per-target "
        "floors, so it is a second hand-maintained number after all.\n"
        f"printed={m.group(1)} sum={sum(summed)} of {len(summed)} selected "
        f"targets ({len(excluded)} excluded)"
    )
    # …and the exclusions are real. Without this, marking every line would make
    # the assertion above pass over an empty sum.
    all_floors = [int(m) for m in re.findall(r"^  floor (\d+)  ", proc.stdout, re.M)]
    assert sorted(summed + excluded) == sorted(all_floors), (
        "some floor line matched neither the summed nor the excluded shape — "
        f"the parser has drifted from the output.\n{proc.stdout}")
    assert int(m.group(1)) + sum(excluded) == sum(all_floors), (
        "an excluded floor leaked into the global sum, or a summed one is "
        f"missing from it.\n{proc.stdout}")


def test_a_devhost_floor_is_listed_but_NOT_in_the_hermetic_global():
    """🔴 THE ASYMMETRY, asserted rather than left implicit.

    A dev-host floor must be VISIBLE in the table — that is what stops it
    rotting into an entry describing a suite that is gone — and must NOT be in
    the hermetic global, because `--set hermetic` does not run those tests and a
    floor demanding them would be a permanently-red gate.

    Driven against BOTH sets, so the claim carries its own scope: the same floor
    is excluded under `hermetic` and included under `all`, and the two globals
    differ by exactly that floor.
    """
    herm = _run([str(RUN_TESTS), "--check-floors", str(REPO_ROOT)])
    allset = _run([str(RUN_TESTS), "--check-floors", "--set", "all",
                   str(REPO_ROOT)])
    assert herm.returncode == 0, herm.stdout + herm.stderr
    assert allset.returncode == 0, allset.stdout + allset.stderr

    dev = re.search(r"^  floor (\d+)  (\S*devhost\S*)  \[NOT SUMMED: ",
                    herm.stdout, re.M)
    assert dev, ("no dev-host floor is listed as excluded under --set hermetic. "
                 "If DEVHOST_TARGETS is empty again this test has nothing to "
                 f"measure and must be reconsidered, not deleted.\n{herm.stdout}")
    floor, name = int(dev.group(1)), dev.group(2)

    assert re.search(r"^  floor %d  %s$" % (floor, re.escape(name)),
                     allset.stdout, re.M), (
        "%s is still excluded under `--set all`, so the dev-host tier is "
        "floored by nothing in the tier that runs it\n%s" % (name, allset.stdout))

    gh = int(re.search(r"GLOBAL floor \(sum over the \w+ set\) = (\d+)",
                       herm.stdout).group(1))
    ga = int(re.search(r"GLOBAL floor \(sum over the \w+ set\) = (\d+)",
                       allset.stdout).group(1))
    assert ga - gh == floor, (
        "the two globals differ by %d, not by %s's floor of %d — the dev-host "
        "set is not being added to the sum exactly once"
        % (ga - gh, name, floor))


def test_no_hand_written_global_total_remains():
    """REGRESSION, and deliberately TEXTUAL: the property being pinned is the
    absence of a constant in the source, which nothing else can observe.

    ``MIN_TESTS`` may still be READ from the environment (a documented one-off
    override) -- what must not come back is a literal default beside it.
    """
    src = RUN_TESTS.read_text()
    bad = re.findall(r'MIN_TESTS="\$\{MIN_TESTS:-\d+\}"', src)
    assert not bad, (
        "a hand-written global test total is back in run-tests.sh: "
        f"{bad}. That line took ELEVEN values in one day and rerere replayed a "
        "wrong one onto a tree it never described. The global floor must be the "
        "SUM of TARGET_FLOORS."
    )
    assert 'MIN_TESTS="${MIN_TESTS:-$MIN_TESTS_COMPUTED}"' in src, (
        "the derived global floor is gone; MIN_TESTS is no longer computed from "
        "the per-target table."
    )


def test_a_target_with_no_floor_is_named(tmp_path):
    """A target nobody floored would run under NO floor -- the ungated-suite
    shape (#276/#298/#306). It must be a loud, named failure."""
    d = tmp_path / "orphan_tests"
    write_pytest_suite(d, 3)
    src = patch_runner_source(
        RUN_TESTS.read_text(),
        targets=[str(d)],
        floors={"scripts/tests": 1},   # a floor for a target that is not selected
    )
    runner = tmp_path / "run-tests.sh"
    runner.write_text(src)
    proc = _run([str(runner), str(REPO_ROOT)])
    out = proc.stdout + proc.stderr
    assert proc.returncode == 2, f"an unpinned target did not abort the pin.\n{out}"
    assert str(d) in out and "NO entry in TARGET_FLOORS" in out, (
        f"the pin fired but never named the unpinned target.\n{out}"
    )


def test_a_floor_for_an_unknown_target_is_named(tmp_path):
    """The other direction: a floor describing a target that no longer exists is
    accounting for nothing, and hides that the suite went away."""
    d = tmp_path / "real_tests"
    write_pytest_suite(d, 3)
    src = patch_runner_source(
        RUN_TESTS.read_text(),
        targets=[str(d)],
        floors={str(d): 1, "scripts/ghost/tests": 99},
    )
    runner = tmp_path / "run-tests.sh"
    runner.write_text(src)
    proc = _run([str(runner), str(REPO_ROOT)])
    out = proc.stdout + proc.stderr
    assert proc.returncode == 2, f"an orphaned floor did not abort the pin.\n{out}"
    assert "scripts/ghost/tests" in out and "in NO target list" in out, out


# --------------------------------------------------------------------------
# REACHABILITY: each new guard, forced into its own state, red for its OWN
# reason.
# --------------------------------------------------------------------------

def test_a_target_below_its_floor_is_named(tmp_path):
    """The collapse this floor exists for: a suite that still collects, but far
    less than it used to.

    Deliberately 8 tests, not 0. Zero is owned by the earlier ``collected 0``
    branch, so a 0-test fixture would prove that guard reachable and leave THIS
    one unexercised -- the "an earlier check always wins so the guard never
    executes" case in claude/RULES.md.
    """
    d = tmp_path / "shrunk_tests"
    write_pytest_suite(d, 8)
    runner = runner_with_targets(tmp_path, [str(d)], floors={str(d): 200})
    proc = _run([str(runner), str(REPO_ROOT)], env=ONLY_PER_TARGET)
    out = proc.stdout + proc.stderr
    assert proc.returncode != 0, f"a target 192 tests below its floor passed.\n{out}"
    assert "collected 8 tests, its floor is 200" in out, (
        f"the run failed but not for the below-floor reason.\n{out}"
    )
    assert "below floor 200" in proc.stdout, out
    assert "RESULT: FAIL (exit=1)" in proc.stdout, out
    assert "only 8 tests were collected" not in out, (
        "the GLOBAL floor fired too, so this case does not isolate the "
        f"per-target guard.\n{out}"
    )


def test_the_global_sum_floor_also_catches_a_collapse(tmp_path):
    """The derived total is a SECOND layer over the same collapse, not a
    decoration -- measured here so the redundancy is on the record, and so a
    change that quietly stops summing is visible."""
    d = tmp_path / "shrunk_tests"
    write_pytest_suite(d, 8)
    runner = runner_with_targets(tmp_path, [str(d)], floors={str(d): 200})
    proc = _run([str(runner), str(REPO_ROOT)])          # no MIN_TESTS override
    out = proc.stdout + proc.stderr
    assert proc.returncode != 0, out
    assert "only 8 tests were collected, floor is 200" in out, (
        f"the derived global floor did not fire on a collapse.\n{out}"
    )


def test_a_collapse_to_zero_is_still_caught(tmp_path):
    """The 0 case stays owned by its own branch, with its own diagnosis.

    Both messages matter: "collected 0" means a collection error or an import
    breakage, "below floor" means the suite shrank. Collapsing them into one
    message would send the reader to the wrong place.
    """
    d = tmp_path / "empty_tests"
    d.mkdir()
    runner = runner_with_targets(tmp_path, [str(d)], floors={str(d): 5})
    proc = _run([str(runner), str(REPO_ROOT)])
    out = proc.stdout + proc.stderr
    assert proc.returncode != 0, out
    assert "collected 0 tests" in out, out


def test_a_target_far_above_its_floor_is_named(tmp_path):
    """The OTHER direction, and the one the single literal kept failing at
    silently: a floor so far below the real count that a whole suite could
    vanish underneath it (5638 once stood against a real 6545).
    """
    d = tmp_path / "grown_tests"
    write_pytest_suite(d, 200)
    runner = runner_with_targets(tmp_path, [str(d)], floors={str(d): 20})
    proc = _run([str(runner), str(REPO_ROOT)])
    out = proc.stdout + proc.stderr
    assert proc.returncode != 0, f"a floor with 180 tests of slack passed.\n{out}"
    assert "collected 200 tests but its floor is only 20" in out, out
    assert f'"{d}|{_suggested_floor(200)}"' in out, (
        "the drift error did not print the exact replacement floor, so bumping "
        f"it is a measurement exercise again.\n{out}"
    )


def test_the_suggested_floor_is_one_the_gate_accepts(tmp_path):
    """ROUND TRIP. The number the error message SUGGESTS must be a number the
    same run would ACCEPT -- otherwise the guidance sends you into a second red.

    This is what makes `_suggested_floor` worth having as a shell function
    rather than a sentence in a comment.
    """
    d = tmp_path / "grown_tests"
    write_pytest_suite(d, 200)
    suggested = _suggested_floor(200)
    runner = runner_with_targets(tmp_path, [str(d)], floors={str(d): suggested})
    proc = _run([str(runner), str(REPO_ROOT)])
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, (
        f"the floor the gate itself suggests ({suggested}) is not one it "
        f"accepts.\n{out}"
    )
    assert "RESULT: PASS (exit=0)" in proc.stdout, out


def test_ordinary_growth_needs_no_hand_edit(tmp_path):
    """THE POINT OF THE WHOLE CHANGE, measured rather than argued.

    A target grows -- the case that forced eleven reconciliations in a day --
    and the gate stays green with the floor untouched. Measured at two points
    (30 and 40 collected against one floor of 20), because a single point would
    not distinguish "tolerates growth" from "happened to be above the floor".
    """
    d = tmp_path / "growing_tests"
    write_pytest_suite(d, 30, prefix="test_first")
    runner = runner_with_targets(tmp_path, [str(d)], floors={str(d): 20})

    first = _run([str(runner), str(REPO_ROOT)])
    assert first.returncode == 0, first.stdout + first.stderr
    assert "collected=30" in first.stdout, first.stdout

    write_pytest_suite(d, 10, prefix="test_second")
    second = _run([str(runner), str(REPO_ROOT)])
    assert second.returncode == 0, (
        "adding 10 tests turned the gate red with no floor edit -- the floor is "
        f"fighting ordinary growth again.\n{second.stdout}{second.stderr}"
    )
    assert "collected=40" in second.stdout, second.stdout


# --------------------------------------------------------------------------
# MUTATION: prove the collapse test observes THIS guard and not another.
# --------------------------------------------------------------------------

def test_the_collapse_guard_is_what_makes_that_test_red(tmp_path):
    """Break the comparison on purpose; the collapse case must go GREEN.

    ``test_a_target_below_its_floor_is_named`` asserts a specific message, which
    already rules out a different guard's error killing it. This closes the
    complement: that the assertion is not passing for a reason that survives the
    guard's removal. Run as a PAIR under one env, because "the mutant went
    green" only means something next to "the control went red":

      control : intact runner   -> red, naming this guard
      mutant  : comparison dead -> GREEN

    Both under MIN_TESTS=1, so the derived global floor (a genuine second layer,
    see the test above) cannot be the thing keeping the mutant red.
    """
    d = tmp_path / "shrunk_tests"
    write_pytest_suite(d, 8)
    src = RUN_TESTS.read_text()
    needle = '[ "$floor" -ge 1 ] && [ "$collected" -lt "$floor" ]'
    assert src.count(needle) == 1, (
        f"expected exactly one collapse comparison to mutate, found "
        f"{src.count(needle)} -- the mutation would not land where intended."
    )

    control = tmp_path / "control.sh"
    control.write_text(patch_runner_source(src, targets=[str(d)], floors={str(d): 200}))
    c = _run([str(control), str(REPO_ROOT)], env=ONLY_PER_TARGET)
    assert c.returncode != 0 and "its floor is 200" in (c.stdout + c.stderr), (
        "the CONTROL half did not go red, so the mutant going green would prove "
        f"nothing.\n{c.stdout}{c.stderr}"
    )

    mutated = src.replace(needle, '[ "$floor" -ge 1 ] && [ "$collected" -lt 0 ]')
    runner = tmp_path / "run-tests.sh"
    runner.write_text(patch_runner_source(mutated, targets=[str(d)], floors={str(d): 200}))
    proc = _run([str(runner), str(REPO_ROOT)], env=ONLY_PER_TARGET)
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, (
        "with the collapse comparison neutered the run is STILL red, so the "
        "collapse test above is red for some other reason and proves nothing "
        f"about this guard.\n{out}"
    )
    assert "its floor is 200" not in out, out


# --------------------------------------------------------------------------
# The floors must actually describe the suites they claim to.
# --------------------------------------------------------------------------

def test_every_floor_is_a_positive_integer_below_a_sane_ceiling():
    """INVARIANT GUARD. A floor of 0 pins nothing; a floor above any plausible
    suite size is a false red waiting to happen (which is exactly what rerere
    wrote: 7168 over a 7143 tree)."""
    src = RUN_TESTS.read_text()
    m = re.search(r"^TARGET_FLOORS=\((.*?)^\)", src, re.S | re.M)
    assert m, "no TARGET_FLOORS block in run-tests.sh"
    entries = re.findall(r'^\s*"([^"|]+)\|(\d+)"\s*$', m.group(1), re.M)
    assert len(entries) >= 15, (
        f"parsed only {len(entries)} floor entries -- the parser is broken, or "
        "targets have been dropped from the table."
    )
    for target, value in entries:
        assert int(value) >= 1, f"{target} is pinned at {value}, which floors nothing"
        assert int(value) < 100_000, f"{target}'s floor {value} is not a plausible suite size"


@pytest.mark.parametrize("m,expected", [(1, 0), (13, 12), (20, 19), (200, 190), (991, 942), (1923, 1873), (5000, 4950)])
def test_the_shell_and_python_floor_rules_agree(m, expected):
    """The rule is written twice -- once in shell (`_suggested_floor`), once
    here -- and they must agree, or the number the gate suggests is not the
    number it accepts. Values chosen to straddle BOTH clamps: the `max(1, ...)`
    floor at small m and the `min(50, ...)` cap above m=1000."""
    assert _suggested_floor(m) == expected
    proc = subprocess.run(
        ["bash", "-c",
         f'source <(sed -n "/^_suggested_floor()/,/^}}/p" {RUN_TESTS}); _suggested_floor {m}'],
        capture_output=True, text=True, cwd=str(REPO_ROOT),
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == str(expected), (
        f"shell says {proc.stdout.strip()!r}, python says {expected} for m={m}"
    )
