"""Guards for `scripts/run-tests.sh`'s PER-TARGET TIMING CENSUS.

WHY THIS EXISTS
---------------
The runner reported COUNTS (``TOTAL collected=… passed=…``) and a VERDICT
(``RESULT: PASS (exit=0)``) and no durations at all. The only time signal
anywhere was the Tekton gate's whole-run wall clock against its 45m deadline.

Measured 2026-08-27 over the twelve most recent completed ``devrc-ci`` runs:
805s-1293s, i.e. 30-48% of that deadline with a 1.6x run-to-run spread, and
nothing in the output could attribute either the spread or a future overrun to a
target. The first signal of a target that doubles would be a hard kill at 45m
carrying no evidence about which one did it.

The per-target shape WAS known -- the xdist header in the runner records
``scripts/tests`` at 677s of 1194 (57%), browser-bridge 234s, dl-router 126s --
but hand-measured once, on 2026-08-25, against the SERIAL runner that devrc#841
then replaced. A number in a comment ages silently; a number the run prints does
not.

WHAT IS REGRESSION COVERAGE HERE AND WHAT IS NOT
------------------------------------------------
Per `claude/RULES.md` ("a guard that pins an invariant the bug never violated is
an INVARIANT GUARD -- label it as one"):

  * ``test_the_census_prints_a_row_per_target`` is the POSITIVE CONTROL. Every
    other test here reads content out of this block, so prove it can produce
    content at all before reading any absence from it.

  * ``test_a_target_that_returns_before_pytest_is_still_counted`` is the one
    test the WRAPPER DESIGN exists for, and the only one that is red against the
    obvious implementation. ``run_pytest`` has five terminal paths, three of
    which return before pytest is invoked. A census that records where the
    counts are computed misses all three and still looks complete.

  * ``test_a_dropped_record_is_reported_as_incomplete`` is the MUTATION test for
    the completeness control, run in BOTH arms: it asserts the warning is absent
    from an unmutated run and present in a mutated one. The absent half is what
    stops "the warning is in the output" being satisfied by a block that always
    prints it.

  * ``test_the_census_does_not_change_the_verdict`` is the FAIL-OPEN property,
    measured rather than asserted, using the same mutation. A census is an
    observer; an observer that can turn a green run red is worse than none.

  * ``test_shell_tests_are_in_the_census`` is an INVARIANT GUARD. No bug ever
    omitted them -- the census was written including them. It exists because the
    block presents itself as an accounting of the run, and a population it
    silently dropped would make every percentage in it wrong in one direction.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))
from testlib.runner_patch import (  # noqa: E402
    patch_runner_source,
    write_pytest_suite,
)

RUN_TESTS = REPO_ROOT / "scripts" / "run-tests.sh"

# The exact record line in the pytest wrapper. Mutating it is how the
# completeness control is exercised; naming it as a constant means a rename in
# the runner fails the assertion below LOUDLY rather than making the mutation a
# silent no-op that scores as SURVIVED.
PYTEST_RECORD = 'TIMINGS+=("$(( $(date +%s) - _t_t0 ))"$\'\\t\'"$_t_rc"$\'\\t\'"$_t_target")'

TIMING_LINE = re.compile(
    r"^  TIMING accounted=(\d+)s over (\d+) target\(s\)  run=(\d+)s  unaccounted=(-?\d+)s",
    re.M,
)
ROW = re.compile(r"^ {4}\s*(\d+)s\s+(\d+)%\s+(\S+)(  \(rc=(\d+)\))?$", re.M)


def _runner(tmp_path: Path, targets: list[str], *, shell_tests: list[str],
            mutate=None) -> Path:
    """A patched copy of the runner. `mutate` gets the source and returns it."""
    src = patch_runner_source(
        RUN_TESTS.read_text(),
        targets,
        {t: 1 for t in targets},
        shell_tests=shell_tests,
        hook_tests=[],
    )
    if mutate is not None:
        src = mutate(src)
    dst = tmp_path / "run-tests.sh"
    dst.write_text(src)
    return dst


def _run(runner: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(runner), str(REPO_ROOT)],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=900,
        env={**os.environ, "MIN_TESTS": "1"},
    )


def _summary(proc: subprocess.CompletedProcess) -> str:
    out = proc.stdout + proc.stderr
    i = out.find("======================== SUMMARY")
    assert i >= 0, f"the run never reached its SUMMARY, so there is nothing to read.\n{out[-3000:]}"
    return out[i:]


# --------------------------------------------------------------------------
# POSITIVE CONTROL
# --------------------------------------------------------------------------

def test_the_census_prints_a_row_per_target(tmp_path):
    """Two targets in, two rows out, and the header's own total agrees with them.

    Reading the accounted total back out of the ROWS is the point: a header that
    printed a number the rows did not sum to would be a self-contradicting
    census, which is worse than none.
    """
    a, b = tmp_path / "t_a", tmp_path / "t_b"
    write_pytest_suite(a, 3, prefix="test_a")
    write_pytest_suite(b, 4, prefix="test_b")
    proc = _run(_runner(tmp_path, [str(a), str(b)], shell_tests=[]))
    s = _summary(proc)

    m = TIMING_LINE.search(s)
    assert m, f"no TIMING header -- the census produced nothing to test.\n{s[:2000]}"
    accounted, n_targets, run, unaccounted = (int(g) for g in m.groups())
    assert n_targets == 2, f"expected 2 recorded targets, got {n_targets}\n{s[:2000]}"

    rows = ROW.findall(s)
    assert len(rows) == 2, f"expected 2 ranking rows, got {len(rows)}: {rows}\n{s[:2000]}"
    assert sum(int(r[0]) for r in rows) == accounted, (
        "the header's accounted total is not the sum of the rows it printed.\n"
        f"header={accounted} rows={[r[0] for r in rows]}"
    )
    assert {r[2] for r in rows} == {str(a), str(b)}, rows
    assert unaccounted == run - accounted, (
        f"unaccounted is not run-accounted: {unaccounted} != {run}-{accounted}"
    )
    assert unaccounted >= 0, (
        "accounted MORE time than the run took, which means the two clocks are "
        f"not measuring the same thing.\nrun={run} accounted={accounted}"
    )


# --------------------------------------------------------------------------
# REGRESSION -- the property the wrapper exists for
# --------------------------------------------------------------------------

def test_a_target_that_returns_before_the_counts_is_still_counted(tmp_path):
    """A target whose pytest summary is unparseable takes `run_pytest`'s
    unparseable-summary early return -- BEFORE `TOT_COLLECTED` and friends are
    touched -- and must still appear in the census.

    This is the whole reason the record is taken by a wrapper. The obvious
    implementation, recording next to where the counts are accumulated, is GREEN
    on every other test in this file and RED here.

    🔴 WHY THIS BRANCH AND NOT THE OBVIOUS ONE. The first draft used a MISSING
    target, which is `run_pytest`'s first early return and reads far better in a
    test name. It is UNREACHABLE: a precondition block validates the whole
    hermetic target list and exits 2 with "unusable entr(ies) in the hermetic
    target list" long before the loop starts, so the run never reaches SUMMARY
    and the guard under test never executes. That is the "an earlier check
    always wins so the guard never runs" case in `claude/RULES.md`, and it was
    caught only by running the test rather than by reading the branch. The
    unparseable-summary path has no such gatekeeper: the target EXISTS, so the
    precondition passes, and pytest is what fails.

    A broken `conftest.py` is the forcing function -- pytest aborts during
    collection and prints no summary line at all.
    """
    ok = tmp_path / "t_ok"
    write_pytest_suite(ok, 3)
    bad = tmp_path / "t_unparseable"
    bad.mkdir()
    (bad / "conftest.py").write_text("raise RuntimeError('conftest explodes on import')\n")
    (bad / "test_x.py").write_text("def test_x():\n    assert True\n")

    proc = _run(_runner(tmp_path, [str(ok), str(bad)], shell_tests=[]))
    s = _summary(proc)
    assert "(unparseable summary)" in s, (
        "the fixture did not reach the branch this test exists for, so a pass "
        "here would prove nothing.\n" + s[:2000]
    )

    m = TIMING_LINE.search(s)
    assert m, f"no TIMING header.\n{s[:2000]}"
    assert int(m.group(2)) == 2, (
        "the target that returned early is MISSING from the census, so the "
        "ranking is a subset that reads as a total.\n" + s[:2000]
    )
    rows = {r[2]: r for r in ROW.findall(s)}
    assert str(bad) in rows, f"no row for the early-return target.\n{rows}"
    assert rows[str(bad)][4] == "1", (
        "the early-return target is recorded with rc=0, so a failed dispatch is "
        f"indistinguishable from an instant one.\nrow={rows[str(bad)]}"
    )
    assert "TIMING ⚠ INCOMPLETE" not in s, (
        "the completeness control fired on a run where every dispatch DID leave "
        "a record -- it is counting something other than records.\n" + s[:2000]
    )


# --------------------------------------------------------------------------
# MUTATION -- both arms
# --------------------------------------------------------------------------

def _drop_pytest_record(src: str) -> str:
    assert src.count(PYTEST_RECORD) == 1, (
        "the pytest wrapper's record line is not where this test thinks it is, "
        "so the mutation below would be a silent no-op and score as SURVIVED. "
        "Update PYTEST_RECORD to match the runner."
    )
    return src.replace(PYTEST_RECORD, ": # record dropped by the mutation test")


def test_a_dropped_record_is_reported_as_incomplete(tmp_path):
    """Break the record and the census must SAY it is a subset.

    Both arms are asserted. Without the clean arm, "the warning appears" would
    also be satisfied by a block that prints it unconditionally, which is a
    control that cannot fail.
    """
    d = tmp_path / "t_one"
    write_pytest_suite(d, 3)

    clean = _summary(_run(_runner(tmp_path, [str(d)], shell_tests=[])))
    assert "TIMING ⚠ INCOMPLETE" not in clean, (
        "the incomplete warning is present on an UNMUTATED run, so it says "
        "nothing about completeness.\n" + clean[:2000]
    )

    broken_dir = tmp_path / "broken"
    broken_dir.mkdir()
    mutated = _summary(_run(_runner(
        broken_dir, [str(d)], shell_tests=[], mutate=_drop_pytest_record)))
    assert "TIMING ⚠ INCOMPLETE" in mutated, (
        "a dispatch left no record and the census still presented itself as "
        "complete -- the control is inert.\n" + mutated[:2000]
    )
    assert "1 dispatch(es) but 0 record(s)" in mutated, (
        "the warning fired but does not name the mismatch it found.\n" + mutated[:2000]
    )


def test_the_census_does_not_change_the_verdict(tmp_path):
    """FAIL-OPEN. The same mutation, checked for the property that matters more
    than the warning: a broken census must not fail a green run."""
    d = tmp_path / "t_one"
    write_pytest_suite(d, 3)
    proc = _run(_runner(tmp_path, [str(d)], shell_tests=[], mutate=_drop_pytest_record))
    out = proc.stdout + proc.stderr
    assert "RESULT: PASS (exit=0)" in out, (
        "a broken TIMING census turned an otherwise-green run red. It is an "
        f"observer; it may never do this.\nrc={proc.returncode}\n{out[-3000:]}"
    )
    assert proc.returncode == 0, f"rc={proc.returncode}\n{out[-3000:]}"


# --------------------------------------------------------------------------
# INVARIANT GUARD
# --------------------------------------------------------------------------

def test_shell_tests_are_in_the_census(tmp_path):
    """The census covers the SHELL_TESTS loop too, not just pytest targets.

    Not regression coverage -- it was written this way. It is pinned because the
    block claims to account for the run: a silently-omitted population would
    make every percentage a share of the wrong denominator, in the direction
    that overstates whatever remains.
    """
    d = tmp_path / "t_one"
    write_pytest_suite(d, 3)
    script = tmp_path / "a_shell_test.sh"
    script.write_text("#!/usr/bin/env bash\nexit 0\n")
    proc = _run(_runner(tmp_path, [str(d)], shell_tests=[str(script)]))
    s = _summary(proc)

    m = TIMING_LINE.search(s)
    assert m, f"no TIMING header.\n{s[:2000]}"
    assert int(m.group(2)) == 2, (
        "the shell test is not in the census, so the accounting excludes a "
        "population it presents itself as covering.\n" + s[:2000]
    )
    assert str(script) in {r[2] for r in ROW.findall(s)}, (
        "no ranking row for the shell test.\n" + s[:2000]
    )
