"""Per-target ledgers under a scoped run: what is suspended, and what is NOT.

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


def test_the_cheap_registry_entries_this_file_leans_on_still_exist():
    """INVARIANT GUARD. `CHEAP_HOOK_TEST`/`CHEAP_SHELL_TEST` are patched into a
    copied runner; a renamed or deleted entry would make that copy die on its
    own "does not exist" error, killing the two tests below for a reason
    unrelated to their subject — and, in the skip direction, making an absence
    assertion vacuously true."""
    for p in (CHEAP_HOOK_TEST, CHEAP_SHELL_TEST):
        assert (REPO_ROOT / p).is_file(), f"{p} is gone — update the constant"


def test_a_scoped_run_skips_the_hook_and_shell_families_and_SAYS_SO(tmp_path):
    """REGRESSION on the lever itself, and on the honesty that pays for it.

    MEASURED on this box with a 1-target `--targets` subset: 172s total, of
    which the selected target was 17s and the five SHELL_TESTS were 101s. Those
    are hand-rolled scripts, not pytest targets, so no `--files` selection can
    ever name them — a scoped run that still paid for them would be dominated by
    tests unrelated to the change.

    🔴 Both halves are asserted. Skipping them silently would be the #276 shape
    (a family of tests that stops running while the gate stays green), so the
    banner has to NAME what did not run."""
    d, runner = scoped_fixture(tmp_path, floor=400)
    # Give the copy ONE real entry in each family, so "they were skipped" is a
    # claim about something that exists — empty families would make this pass
    # vacuously. One rather than all five because the full SHELL_TESTS set is
    # 101s under load, and adding a two-minute test to `scripts/tests` while
    # shipping a change whose whole purpose is a cheaper run would be absurd.
    runner.write_text(patch_runner_source(
        RUN_TESTS.read_text(), targets=[str(d)], floors={str(d): 400}, ack=[],
        hook_tests=[CHEAP_HOOK_TEST], shell_tests=[CHEAP_SHELL_TEST]))
    proc = run([str(runner), str(REPO_ROOT), "--files", str(d / "test_beta.py")])
    out = out_of(proc)
    assert proc.returncode == 0, f"rc={proc.returncode}\n{out}"
    assert re.search(r"^     NOT RUN in this mode", out, re.M), out
    assert re.search(r"^       - \d+ hand-rolled hook test script\(s\)$", out, re.M), out
    assert re.search(r"^       - \d+ shell test script\(s\)$", out, re.M), out
    # The discriminating half: they really did not run. `=== hook test` /
    # `=== shell test` headers are what a real run prints for each one.
    # `=== script <path>` is what a real run prints for each hook/shell entry.
    assert not re.search(r"^=== script scripts/claude-hooks/", out, re.M), out
    assert not re.search(r"^=== script scripts/tests/.*\.sh ", out, re.M), out


def test_a_full_run_still_runs_the_hook_and_shell_families(tmp_path):
    """POSITIVE CONTROL for the test above. Without it, "no hook headers in the
    output" is satisfied by a runner that never ran them in ANY mode — the
    assertion would be about nothing."""
    d, runner = scoped_fixture(tmp_path, floor=1)
    runner.write_text(patch_runner_source(
        RUN_TESTS.read_text(), targets=[str(d)], floors={str(d): 1}, ack=[],
        hook_tests=[CHEAP_HOOK_TEST], shell_tests=[CHEAP_SHELL_TEST]))
    proc = run([str(runner), str(REPO_ROOT)], env={"MIN_TESTS": "1"})
    out = out_of(proc)
    assert re.search(r"^=== script scripts/claude-hooks/", out, re.M), (
        "a FULL run of the same copy ran no hook tests, so the scoped test's "
        f"absence assertion proves nothing.\n{out[-3000:]}")
    assert re.search(r"^=== script scripts/tests/.*\.sh ", out, re.M), out[-3000:]
    assert re.search(r"^     NOT RUN in this mode", out, re.M) is None, out


def test_a_narrowed_run_NAMES_the_work_it_left_for_ci(tmp_path):
    """🔴 THE POINT OF A NARROWED RUN, now that there is no local full-suite
    mandate and branch protection is off: the advisory `tekton/devrc-*` checks
    are the only automated signal and they land minutes later. So the useful
    thing this run can print is not what it covered — it is WHICH WORK IT HANDED
    TO CI, named, so the operator knows what is still unknown.

    Every number in the block is DERIVED from what the run selected against the
    declared list it read, never asserted, so it cannot drift the way a
    hand-written "CI also runs X" sentence would.

    The fixture has TWO targets of DIFFERENT sizes and selects one file of one:
    equal sizes could not tell "counted the right target" from "counted either"."""
    a = tmp_path / "alpha"
    b = tmp_path / "beta"
    write_pytest_suite(a, 3, prefix="test_a1")
    write_pytest_suite(a, 7, prefix="test_a2")
    write_pytest_suite(a, 5, prefix="test_a3")   # 3 files in alpha
    write_pytest_suite(b, 11, prefix="test_b1")  # 1 file in beta
    runner = tmp_path / "run-tests.sh"
    runner.write_text(patch_runner_source(
        RUN_TESTS.read_text(), targets=[str(a), str(b)],
        floors={str(a): 1, str(b): 1}, ack=[], hook_tests=[], shell_tests=[]))
    proc = run([str(runner), str(REPO_ROOT), "--files", str(a / "test_a2.py")],
                env={"MIN_TESTS": "1"})
    out = out_of(proc)
    assert proc.returncode == 0, f"rc={proc.returncode}\n{out}"
    assert re.search(r"^  ---- what this run did NOT cover \(the gap CI closes\) ----$",
                     out, re.M), out
    # The unrun TARGET is named, not merely counted — a count cannot be acted on.
    assert re.search(r"^       - 1 of 2 pytest target\(s\) never ran:$", out, re.M), out
    assert re.search(r"^           " + re.escape(str(b)) + r"$", out, re.M), out
    # 🔴 AND the unrun FILES inside the target that DID run. A gap block counting
    # only whole targets would report the selected target as fully covered — for
    # `scripts/tests` that is one file of 185 reported as a covered target.
    assert re.search(r"^       - 2 other collectable file\(s\) inside the selected target",
                     out, re.M), (
        "the gap block did not count the unrun files inside the selected target "
        f"(alpha holds 3 files; 1 was selected).\n{out}")
    assert re.search(r"^       - the NODE tier ", out, re.M), out


def test_a_FULL_run_prints_no_ci_gap_block(tmp_path):
    """POSITIVE CONTROL by contrast. Without it, every assertion above is
    satisfied by a block that prints unconditionally — including on a run that
    left no gap at all, which would make it noise the reader learns to skip."""
    a = tmp_path / "alpha"
    write_pytest_suite(a, 3, prefix="test_a1")
    runner = tmp_path / "run-tests.sh"
    runner.write_text(patch_runner_source(
        RUN_TESTS.read_text(), targets=[str(a)], floors={str(a): 1},
        ack=[], hook_tests=[], shell_tests=[]))
    proc = run([str(runner), str(REPO_ROOT)], env={"MIN_TESTS": "1"})
    out = out_of(proc)
    assert proc.returncode == 0, f"rc={proc.returncode}\n{out}"
    assert not re.search(r"^  ---- what this run did NOT cover", out, re.M), (
        f"a FULL run printed a CI-gap block it has no gap for.\n{out}")


def test_a_target_subset_also_names_the_targets_it_skipped(tmp_path):
    """The gap block is not scoped-only: a `--targets` PARTIAL run leaves the
    same kind of hole and the operator has the same question about it."""
    a = tmp_path / "alpha"
    b = tmp_path / "beta"
    write_pytest_suite(a, 3, prefix="test_a1")
    write_pytest_suite(b, 11, prefix="test_b1")
    runner = tmp_path / "run-tests.sh"
    runner.write_text(patch_runner_source(
        RUN_TESTS.read_text(), targets=[str(a), str(b)],
        floors={str(a): 1, str(b): 1}, ack=[], hook_tests=[], shell_tests=[]))
    proc = run([str(runner), str(REPO_ROOT), "--targets", str(a)],
                env={"MIN_TESTS": "1"})
    out = out_of(proc)
    assert proc.returncode == 0, f"rc={proc.returncode}\n{out}"
    assert re.search(r"^       - 1 of 2 pytest target\(s\) never ran:$", out, re.M), out
    assert re.search(r"^           " + re.escape(str(b)) + r"$", out, re.M), out
    # A target subset ran its targets WHOLE, so there is no intra-target file gap
    # to report — claiming one would overstate the hole.
    assert not re.search(r"other collectable file\(s\) inside", out, re.M), out


def test_a_scoped_run_suspends_GUARD_7s_required_ack_direction(tmp_path):
    """REGRESSION, reproduced on the real repo before the fix:

        --files scripts/tests/test_ship_detect_role.py
        -> 12 passed, 0 failed, `GUARD 7: scripts/tests intercepted NOTHING`, exit 1

    An acknowledgement says "*this target*, RUN IN FULL, drives launchers into
    the stub" — it names three seam files out of 181. A slice that does not
    happen to include one intercepts nothing, and that is correct, not a defect.
    The verdict was a coin flip on the selection."""
    d, runner = ledger_fixture(
        tmp_path, ack=[f"{tmp_path / 'target'}|synthetic seam reason"])
    proc = run([str(runner), str(REPO_ROOT), "--files", str(d / "test_other.py")],
                env={"MIN_TESTS": "1"})
    out = out_of(proc)
    assert proc.returncode == 0, (
        "a scoped run was failed by GUARD 7's whole-target acknowledgement.\n"
        f"rc={proc.returncode}\n{out}"
    )
    assert re.search(r"required direction SUSPENDED", out), out
    # 🔴 SUSPENSION MUST BE ANNOUNCED, not silent — a guard that quietly stops
    # applying is the #276 shape this runner exists to refuse.
    assert re.search(r"^  ---- whole-target expectations SUSPENDED by this SCOPED run ----$",
                     out, re.M), out
    assert re.search(r"^      - GUARD 7's REQUIRED direction ", out, re.M), out


def test_a_FULL_run_still_ENFORCES_GUARD_7s_required_ack_direction(tmp_path):
    """🔴 THE CONTROL THAT MAKES THE TEST ABOVE MEAN SOMETHING. Same copy, same
    ledger, no `--files`: an acknowledged target that intercepts nothing must
    still be a hard failure. Without this, deleting the required direction
    outright would satisfy the scoped test."""
    d, runner = ledger_fixture(tmp_path, ack=[f"{tmp_path / 'target'}|synthetic seam reason"])
    proc = run([str(runner), str(REPO_ROOT)], env={"MIN_TESTS": "1"})
    out = out_of(proc)
    assert proc.returncode != 0, (
        "a FULL run of an acknowledged target that intercepted NOTHING passed — "
        f"the required direction is disabled, not suspended.\n{out}")
    assert "intercepted NOTHING" in out, out
    assert not re.search(r"SUSPENDED", out), (
        f"a FULL run reported a suspension it has no business having.\n{out}")


def test_a_scoped_run_suspends_GUARD_2s_skip_TOTAL(tmp_path):
    """REGRESSION, reproduced on the real repo before the fix:

        --files scripts/signal/tests/test_search.py
        -> 15 passed, 0 skipped, `0 test(s) skipped, but 2 of 3 pinned entries
           apply here`, exit 1

    A pin names a DIRECTORY and a reason; it cannot say which FILE produces the
    skip. So a scoped run whose target is present but sliced counted pins whose
    file never ran. Every scoped run inside that 920-test target was red unless
    the selection happened to include one specific file."""
    d, runner = ledger_fixture(
        tmp_path, skip_file=True,
        expected_skips=[f"{tmp_path / 'target'}|synthetic pinned reason"])
    # Select the module WITHOUT the skip: 3 tests, 0 skips, 1 applicable pin.
    proc = run([str(runner), str(REPO_ROOT), "--files", str(d / "test_quiet.py")],
                env={"MIN_TESTS": "1"})
    out = out_of(proc)
    assert proc.returncode == 0, (
        "a scoped run was failed by GUARD 2's whole-target skip total.\n"
        f"rc={proc.returncode}\n{out}"
    )
    assert re.search(r"^      - GUARD 2's skip TOTAL ", out, re.M), out
    assert "pinned entries apply here" not in out, out


def test_a_FULL_run_still_ENFORCES_GUARD_2s_skip_TOTAL(tmp_path):
    """🔴 THE MATCHING CONTROL. Same copy, same pin, no `--files`, and a pin that
    NOTHING satisfies — the module holding the skip is absent, so a full run
    observes 0 skips against 1 applicable pin and must fail. Without this,
    deleting the total check would satisfy the scoped test above."""
    d, runner = ledger_fixture(
        tmp_path, skip_file=False,
        expected_skips=[f"{tmp_path / 'target'}|a reason nothing here ever emits"])
    proc = run([str(runner), str(REPO_ROOT)], env={"MIN_TESTS": "1"})
    out = out_of(proc)
    assert proc.returncode != 0, (
        "a FULL run with an applicable pin and zero skips passed — the skip "
        f"total is disabled, not suspended.\n{out}")
    assert "pinned entries apply here" in out, out


def test_a_scoped_run_still_FAILS_an_UNPINNED_skip(tmp_path):
    """🔴 THE HALF THAT IS *NOT* SUSPENDED, and the reason the suspension is
    narrow rather than "turn GUARD 2 off in scoped mode".

    The UNPINNED check is evaluated per OBSERVED skip and does not care how much
    of the target ran. A scoped run that selects a file whose test skips for an
    unexplained reason must still be red — that is coverage silently collapsing,
    which is what GUARD 2 exists for."""
    d, runner = ledger_fixture(tmp_path, skip_file=True, expected_skips=[])
    proc = run([str(runner), str(REPO_ROOT), "--files", str(d / "test_skipper.py")],
                env={"MIN_TESTS": "1"})
    out = out_of(proc)
    assert proc.returncode != 0, (
        "a scoped run absorbed an UNPINNED skip — suspending the TOTAL must not "
        f"suspend the per-observation check.\n{out}")
    assert "UNPINNED skip group" in out, out


def test_a_scoped_run_still_FAILS_an_unacknowledged_real_launcher(tmp_path):
    """🔴 GUARD 7's OTHER non-suspended half, asserted for the same reason.

    The PERMITTED direction — no unacknowledged target may reach a real host
    launcher — is per-observation. A scoped run that trips it must be red; this
    is the direction that stops a test touching the operator's machine, and it
    would be the worst possible thing to suspend along with the ack."""
    d = tmp_path / "target"
    d.mkdir(parents=True)
    # A test that really invokes a stubbed launcher. GUARD 7's stub intercepts
    # it and records the attempt; with NO ack entry for this target, the record
    # is a finding.
    (d / "test_launcher.py").write_text(
        "import subprocess\n\n\n"
        "def test_reaches_a_launcher():\n"
        "    subprocess.run(['systemd-run', '--user', '--on-active=1', 'true'],\n"
        "                   capture_output=True)\n"
    )
    runner = tmp_path / "run-tests.sh"
    runner.write_text(patch_runner_source(
        RUN_TESTS.read_text(), targets=[str(d)], floors={str(d): 1},
        ack=[], hook_tests=[], shell_tests=[]))
    proc = run([str(runner), str(REPO_ROOT), "--files", str(d / "test_launcher.py")],
                env={"MIN_TESTS": "1"})
    out = out_of(proc)
    assert proc.returncode != 0, (
        "a scoped run reached a REAL host launcher from an unacknowledged "
        f"target and passed.\n{out}")
    assert "REAL host launcher" in out, out

