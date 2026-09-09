"""The `--files` selection contract: what a scoped run runs, and what it refuses.

Split out of a single 53-test file. `run-tests.sh` uses `--dist loadfile`, which
pins every test in ONE file to ONE xdist worker — so that file's whole duration
(MEASURED: 187.6s wall / 47.5s CPU at load 58) was a serial critical path inside
`scripts/tests`, the biggest target, on the tier that is the bottleneck. Shipping
that inside a PR whose thesis is "the suite is too slow" would be self-refuting.
Shared fixtures live in `scripts/testlib/scoped_harness.py`."""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))
from testlib.mockbin import write_exec  # noqa: E402,F401
from testlib.runner_patch import (  # noqa: E402,F401
    patch_runner_source,
    write_pytest_suite,
)
from testlib.scoped_harness import (  # noqa: E402,F401
    CHEAP_HOOK_TEST,
    CHEAP_SHELL_TEST,
    GATE,
    RUN_TESTS,
    SCOPE_STATES,
    SCOPED,
    commit_all,
    gate,
    ledger_fixture,
    out_of,
    run,
    scope_line,
    scoped_fixture,
    stub_runner,
    throwaway_repo,
    tier_runner,
    two_target_fixture,
)


def test_an_empty_files_selection_is_fatal_not_a_full_run():
    """REGRESSION. `--files "$(map_changes)"` with a mapper that found nothing
    must never become a run that tests something else and exits 0.

    🔴 THE SECOND ASSERTION IS NOT DECORATION — it is what makes this test able
    to see the guard it names. MEASURED: mutating the `--files` empty check to
    `-lt 0` (i.e. deleting it) left this test GREEN when it asserted only the
    first line, because the run then falls through to the target-subset block,
    whose own empty check fires with a BYTE-IDENTICAL first line — it
    interpolates `ONLY_TARGETS_SOURCE`, which this path has already set to
    `--files`. A different guard's error killing the test is the textbook
    green-for-the-wrong-reason: the mutant SURVIVED a fully green suite.

    The follow-up line below is emitted by the `--files` check ALONE. The layered
    defence is real and welcome; this test now pins the near layer specifically."""
    proc = run([str(RUN_TESTS), "--files", "", "--check-targets", str(REPO_ROOT)])
    out = out_of(proc)
    assert proc.returncode == 3, (
        "an empty --files selection did not abort.\n"
        f"rc={proc.returncode}\n{out}"
    )
    assert "--files was given but resolved to NOTHING" in out, out
    assert "If a change-mapper produced this" in out, (
        "the abort came from the target-subset block's empty check, not the "
        "--files one — same first line, different guard. This test cannot "
        f"vouch for the guard it names.\n{out}"
    )


def test_a_file_under_no_declared_target_is_fatal():
    """A path no target owns would run outside the per-target guard accounting,
    so its result would be a claim about nothing.

    ⚠ The path must be pytest-COLLECTABLE or the family check below fires first
    and this test passes on a different guard's error. `test_bash_guard.py` is a
    real `test_*.py` whose directory is not a declared target (only specific
    FILES in `scripts/claude-hooks/tests/` are), so it reaches ownership."""
    orphan = "scripts/claude-hooks/tests/test_bash_guard.py"
    assert (REPO_ROOT / orphan).is_file(), f"{orphan} moved — pick another orphan"
    proc = run([str(RUN_TESTS), "--files", orphan,
                 "--check-targets", str(REPO_ROOT)])
    out = out_of(proc)
    assert proc.returncode == 3, f"rc={proc.returncode}\n{out}"
    assert "lies under NO declared" in out, out


def test_a_SHELL_TEST_is_refused_by_family_not_misdiagnosed():
    """REGRESSION. `SHELL_TESTS` are `scripts/tests/*.sh`, which sit under the
    `scripts/tests` DIRECTORY target — so the ownership check ACCEPTED them.
    Measured before this fix: `--files scripts/tests/test_resume_state.sh` was
    accepted, handed to pytest, collected 0, and reported as "A collection error
    or an import breakage, not a pass" — a wrong diagnosis for a file that is a
    perfectly good test in a family `--files` cannot address at all.

    (`HOOK_TESTS` need no separate arm: they live under
    `scripts/claude-hooks/tests/`, which is not a declared directory target, so
    the ownership check already refuses them — verified by the test above.)"""
    shell_test = "scripts/tests/test_resume_state.sh"
    assert (REPO_ROOT / shell_test).is_file(), f"{shell_test} moved — pick another"
    proc = run([str(RUN_TESTS), "--files", shell_test,
                 "--check-targets", str(REPO_ROOT)])
    out = out_of(proc)
    assert proc.returncode == 3, f"rc={proc.returncode}\n{out}"
    assert "which pytest cannot collect" in out, out
    assert "SHELL_TESTS" in out, (
        f"the refusal did not name the family, so it reads as a bad path.\n{out}")


def test_a_nonexistent_file_is_fatal_not_silently_dropped():
    """Dropping an unresolvable path is how a scope shrinks toward zero while
    still exiting 0 — the same shape `--targets` made fatal for a typo'd
    target."""
    proc = run([str(RUN_TESTS), "--files", "scripts/tests/test_does_not_exist.py",
                 "--check-targets", str(REPO_ROOT)])
    out = out_of(proc)
    assert proc.returncode == 3, f"rc={proc.returncode}\n{out}"
    assert "which is not a file in this repo" in out, out


def test_a_duplicate_file_is_fatal():
    """A duplicate runs one file twice and counts it twice, inflating both the
    collected total and the derived scoped floor — the identical defect the
    `--targets` block measured for duplicate targets."""
    f = "scripts/tests/test_scoped_runs.py"
    proc = run([str(RUN_TESTS), "--files", f"{f} {f}",
                 "--check-targets", str(REPO_ROOT)])
    out = out_of(proc)
    assert proc.returncode == 3, f"rc={proc.returncode}\n{out}"
    assert "more than once" in out, out


def test_files_cannot_be_combined_with_a_target_subset():
    """Two narrowings for one run means one of them is silently discarded."""
    proc = run([str(RUN_TESTS), "--files", "scripts/tests/test_scoped_runs.py",
                 "--check-targets", str(REPO_ROOT)],
                env={"DEVRC_TARGETS": "scripts/tests"})
    out = out_of(proc)
    assert proc.returncode == 3, f"rc={proc.returncode}\n{out}"
    assert "--files cannot be combined with" in out, out
    # 🔴 NAME THE KNOB THAT IS ACTUALLY SET. The remedy for an ambient env var is
    # `unset`, and advice that only mentions the flag is advice the operator has
    # already followed.
    assert "DEVRC_TARGETS" in out, out


def test_repeating_the_files_flag_is_fatal_not_last_wins():
    proc = run([str(RUN_TESTS), "--files", "a", "--files", "b",
                 "--check-targets", str(REPO_ROOT)])
    out = out_of(proc)
    assert proc.returncode == 3, f"rc={proc.returncode}\n{out}"
    assert "--files given more than once" in out, out


def test_a_scoped_run_executes_only_the_named_file(tmp_path):
    """REGRESSION. At the parent commit `--files` was swallowed as a ROOT and the
    whole target ran, collecting 10."""
    d, runner = scoped_fixture(tmp_path, floor=1)
    proc = run([str(runner), str(REPO_ROOT), "--files", str(d / "test_beta.py")],
                env={"MIN_TESTS": "1"})
    out = out_of(proc)
    assert proc.returncode == 0, f"rc={proc.returncode}\n{out}"
    m = re.search(r"^  TOTAL collected=(\d+) ", out, re.M)
    assert m, out
    assert m.group(1) == "7", (
        f"a scoped run collected {m.group(1)}, not the 7 tests of the one named "
        f"file (the other module holds 3).\n{out}"
    )


def test_a_scoped_run_is_not_failed_by_the_targets_own_floor(tmp_path):
    """REGRESSION, and the reason a scoped mode could not be built on
    `--targets`: a target floor describes the whole target. Applied to a slice
    it is a permanently-red gate — 400 tests demanded of a 7-test file."""
    d, runner = scoped_fixture(tmp_path, floor=400)
    proc = run([str(runner), str(REPO_ROOT), "--files", str(d / "test_beta.py")])
    out = out_of(proc)
    assert proc.returncode == 0, (
        "a scoped run was failed by the whole target's floor.\n"
        f"rc={proc.returncode}\n{out}"
    )
    assert re.search(r"^  TOTAL collected=7 .*SCOPED floor: 1", out, re.M), out


def test_a_scoped_run_whose_files_collect_nothing_is_not_a_pass(tmp_path):
    """🔴 THE REASSURING ZERO, at the last place it could still appear. A file
    that exists and collects no tests must not produce `collected=0 … PASS`."""
    d = tmp_path / "target"
    write_pytest_suite(d, 3, prefix="test_alpha")
    empty = d / "test_empty.py"
    empty.write_text("# no tests here\n")
    runner = tmp_path / "run-tests.sh"
    runner.write_text(patch_runner_source(
        RUN_TESTS.read_text(), targets=[str(d)], floors={str(d): 1},
        ack=[], hook_tests=[], shell_tests=[],
    ))
    proc = run([str(runner), str(REPO_ROOT), "--files", str(empty)],
                env={"MIN_TESTS": "1"})
    out = out_of(proc)
    assert proc.returncode != 0, (
        f"a scoped run that collected nothing exited 0.\n{out}"
    )
    assert "collected 0 tests" in out, out


def test_the_scoped_floor_is_REACHABLE_and_fires_on_a_partly_empty_selection(tmp_path):
    """🔴 REACHABILITY, which "I broke it and a test failed" does not prove.

    The scoped floor (one test per selected file) sits BEHIND the `collected < 1`
    branch, which owns zero on its own. So a single empty file can never reach
    it — the earlier check always wins, and a mutation of the scoped floor would
    die for the wrong reason. The only shape that reaches it is a selection
    where SOME file contributed and the total still falls short: three files,
    one test between them. Floor 3, collected 1.

    Counts chosen so the numbers cannot coincide: 1 collected vs 3 selected, and
    neither is a multiple of the other or of the other module's size."""
    d = tmp_path / "target"
    write_pytest_suite(d, 1, prefix="test_one")
    (d / "test_empty_a.py").write_text("# deliberately holds no tests\n")
    (d / "test_empty_b.py").write_text("# deliberately holds no tests\n")
    runner = tmp_path / "run-tests.sh"
    runner.write_text(patch_runner_source(
        RUN_TESTS.read_text(), targets=[str(d)], floors={str(d): 1},
        ack=[], hook_tests=[], shell_tests=[],
    ))
    sel = " ".join(str(d / n) for n in
                   ("test_one.py", "test_empty_a.py", "test_empty_b.py"))
    proc = run([str(runner), str(REPO_ROOT), "--files", sel],
                env={"MIN_TESTS": "1"})
    out = out_of(proc)
    assert proc.returncode != 0, (
        "3 selected files collecting 1 test between them was reported as a pass.\n"
        f"{out}"
    )
    # 🔴 THIS guard's own message, not merely "something went red". A different
    # guard's error killing the test would leave it green with this one deleted.
    assert "A scoped run floors at one test per selected file" in out, out
    assert re.search(r"^  FAIL .*SCOPED collected=1 below 3 selected file", out, re.M), out


def test_the_scoped_summary_banner_says_it_is_not_a_gate_run(tmp_path):
    """The banner is where `gate.sh` STARTS reading, so it is the surface a
    human actually sees. A run that tested a slice of one target has to say so
    in a sentence, next to the numbers a reader is about to believe."""
    d, runner = scoped_fixture(tmp_path, floor=400)
    target_file = d / "test_beta.py"
    proc = run([str(runner), str(REPO_ROOT), "--files", str(target_file)])
    out = out_of(proc)
    assert proc.returncode == 0, out
    # Anchored at column 0 and matched as whole lines: `tmp_path` is named after
    # this test and appears in the output, so a substring search for a word in
    # this test's own name would match the path rather than the banner.
    assert re.search(r"^={8,} SUMMARY .* — SCOPED: 1 file\(s\) in 1 of 1 ", out, re.M), out
    assert re.search(r"^  🔴 SCOPED RUN — NOT A GATE RUN\.", out, re.M), out
    assert re.search(r"^     file: " + re.escape(str(target_file)) + r"$", out, re.M), out
    assert scope_line(out) == "SCOPED", out


def test_a_scoped_row_never_prints_the_targets_floor(tmp_path):
    """A `floor=400` on a row covering one file of a target is a coverage claim
    about the whole target — the same false-label defect GUARD 5's and
    --check-floors' subset messages each had to grow a branch for."""
    d, runner = scoped_fixture(tmp_path, floor=400)
    proc = run([str(runner), str(REPO_ROOT), "--files", str(d / "test_beta.py")])
    out = out_of(proc)
    assert proc.returncode == 0, out
    row = [ln for ln in out.splitlines() if ln.startswith("  PASS  ")]
    assert row, out
    assert "floor=400" not in "\n".join(row), (
        f"a scoped row quoted the whole target's floor.\n{row}"
    )
    assert "SCOPED" in row[0] and "NOT a claim about" in row[0], row


def test_a_scoped_run_uses_one_pytest_invocation_per_owning_target(tmp_path):
    """REGRESSION-BY-CONSTRUCTION for the guard accounting. GUARDs 7/8/9/10
    bracket exactly ONE pytest invocation per target and bound the session
    markers by that invocation's worker count; splitting a target across one
    call per file would multiply the markers and fail every one of them for a
    condition the SELECTION created. Two files of one target, one `=== pytest`
    header, and the run stays green."""
    d, runner = scoped_fixture(tmp_path, floor=400)
    proc = run([str(runner), str(REPO_ROOT), "--files",
                 f"{d / 'test_alpha.py'} {d / 'test_beta.py'}"])
    out = out_of(proc)
    assert proc.returncode == 0, f"rc={proc.returncode}\n{out}"
    headers = re.findall(r"^=== pytest ", out, re.M)
    assert len(headers) == 1, f"expected ONE pytest invocation, got {len(headers)}\n{out}"
    assert re.search(r"^  TOTAL collected=10 ", out, re.M), out

