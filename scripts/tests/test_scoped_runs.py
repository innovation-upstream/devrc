"""Change-scoped test runs, and the guards that keep one from being read as a gate.

WHY THIS FILE EXISTS
--------------------
The pytest gate tier's median wall time is 20.1 min over 237 real runs. The
cause is not that any suite is slow: ~27 concurrent agent sessions each run the
FULL ~21k-test suite on one 24-core box, and runs bucketed by overlap go from
14.5 min (no other runs) to 48.9 min (6+). So the lever is running FEWER FULL
SUITES, which means an iteration-time run that is cheap and change-scoped.

Cheap and change-scoped is also the most dangerous thing that can be added to a
test runner, because the failure mode is a green nobody can tell apart from the
real one. Everything here pins one of three properties:

  1. A narrowed run SAYS SO, positively, on every exit path (``SCOPE:``).
  2. ``gate.sh`` cannot emit a gate PASS off a run that is not ``SCOPE: FULL`` —
     including a run that printed no scope at all. Absence is not FULL.
  3. A selection that resolves to NOTHING is FATAL, never a quiet exit 0.

MEASURED AT THE PARENT COMMIT, and this is what property 2 exists for::

    DEVRC_TARGETS=scripts/collector/i3/tests scripts/gate.sh --tier pytest
    -> GATE: RESULT=PASS exit=0      (1 of 28 targets, 12 tests, 3m04s)

``GATE: RESULT=PASS`` is the line everyone quotes as "the gate passed".

🔴 ANCHORED MATCHING THROUGHOUT. pytest names ``tmp_path`` after the running
test, so a bare ``assert "SCOPED" in out`` in a test whose own name contains
``scoped`` matches the tmp path echoed back in the output and passes against a
feature that does not exist. Every assertion here matches a whole line at
column 0 instead.
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
from testlib.mockbin import write_exec  # noqa: E402
from testlib.runner_patch import (  # noqa: E402
    patch_runner_source,
    write_pytest_suite,
)

RUN_TESTS = REPO_ROOT / "scripts" / "run-tests.sh"
GATE = REPO_ROOT / "scripts" / "gate.sh"
SCOPED = REPO_ROOT / "scripts" / "scoped-tests.sh"

# The scope vocabulary, owned by run-tests.sh's GUARD 11. Pinned two-way against
# both scripts by test_the_scope_vocabulary_is_pinned_two_way below.
SCOPE_STATES = ("FULL", "PARTIAL", "SCOPED", "UNKNOWN")


def _run(args: list[str], timeout: int = 600, env: dict | None = None,
         cwd: Path | None = None) -> subprocess.CompletedProcess:
    full_env = None
    if env is not None:
        full_env = {**os.environ, **env}
        for k, v in list(full_env.items()):
            if v is None:
                del full_env[k]
    return subprocess.run(
        ["bash", *args],
        cwd=str(cwd or REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=timeout,
        env=full_env,
    )


def _out(proc: subprocess.CompletedProcess) -> str:
    return proc.stdout + proc.stderr


def _scope_line(out: str) -> str | None:
    m = re.search(r"^SCOPE: (\w+) ", out, re.M)
    return m.group(1) if m else None


# ==========================================================================
# 1. `--files` refuses every selection mistake, loudly.
#
# 🔴 EVERY CASE HERE IS RED AT THE PARENT COMMIT FOR THE SAME REASON, and it is
# the reason the flag had to be added rather than emulated: at the parent
# commit `--files` is an UNKNOWN flag, and run-tests.sh's argument loop ends
# `*) ROOT="$1"` — so an unknown flag and its value are silently absorbed as a
# repo root, the last positional wins, and the run proceeds as a FULL run at
# exit 0. A caller that believed it had narrowed the run got the whole suite and
# no word said. Each test below therefore asserts the SPECIFIC message, not just
# a non-zero code.
# ==========================================================================

def test_an_empty_files_selection_is_fatal_not_a_full_run():
    """REGRESSION. `--files "$(map_changes)"` with a mapper that found nothing
    must never become a run that tests something else and exits 0."""
    proc = _run([str(RUN_TESTS), "--files", "", "--check-targets", str(REPO_ROOT)])
    out = _out(proc)
    assert proc.returncode == 3, (
        "an empty --files selection did not abort.\n"
        f"rc={proc.returncode}\n{out}"
    )
    assert "--files was given but resolved to NOTHING" in out, out


def test_a_file_under_no_declared_target_is_fatal():
    """A path no target owns would run outside the per-target guard accounting,
    so its result would be a claim about nothing."""
    proc = _run([str(RUN_TESTS), "--files", "scripts/gate.sh",
                 "--check-targets", str(REPO_ROOT)])
    out = _out(proc)
    assert proc.returncode == 3, f"rc={proc.returncode}\n{out}"
    assert "lies under NO declared" in out, out


def test_a_nonexistent_file_is_fatal_not_silently_dropped():
    """Dropping an unresolvable path is how a scope shrinks toward zero while
    still exiting 0 — the same shape `--targets` made fatal for a typo'd
    target."""
    proc = _run([str(RUN_TESTS), "--files", "scripts/tests/test_does_not_exist.py",
                 "--check-targets", str(REPO_ROOT)])
    out = _out(proc)
    assert proc.returncode == 3, f"rc={proc.returncode}\n{out}"
    assert "which is not a file in this repo" in out, out


def test_a_duplicate_file_is_fatal():
    """A duplicate runs one file twice and counts it twice, inflating both the
    collected total and the derived scoped floor — the identical defect the
    `--targets` block measured for duplicate targets."""
    f = "scripts/tests/test_scoped_runs.py"
    proc = _run([str(RUN_TESTS), "--files", f"{f} {f}",
                 "--check-targets", str(REPO_ROOT)])
    out = _out(proc)
    assert proc.returncode == 3, f"rc={proc.returncode}\n{out}"
    assert "more than once" in out, out


def test_files_cannot_be_combined_with_a_target_subset():
    """Two narrowings for one run means one of them is silently discarded."""
    proc = _run([str(RUN_TESTS), "--files", "scripts/tests/test_scoped_runs.py",
                 "--check-targets", str(REPO_ROOT)],
                env={"DEVRC_TARGETS": "scripts/tests"})
    out = _out(proc)
    assert proc.returncode == 3, f"rc={proc.returncode}\n{out}"
    assert "--files cannot be combined with" in out, out
    # 🔴 NAME THE KNOB THAT IS ACTUALLY SET. The remedy for an ambient env var is
    # `unset`, and advice that only mentions the flag is advice the operator has
    # already followed.
    assert "DEVRC_TARGETS" in out, out


def test_repeating_the_files_flag_is_fatal_not_last_wins():
    proc = _run([str(RUN_TESTS), "--files", "a", "--files", "b",
                 "--check-targets", str(REPO_ROOT)])
    out = _out(proc)
    assert proc.returncode == 3, f"rc={proc.returncode}\n{out}"
    assert "--files given more than once" in out, out


# ==========================================================================
# 2. The SCOPE marker: positive, on every exit path.
# ==========================================================================

def test_a_full_run_states_its_scope_positively():
    """REGRESSION + POSITIVE CONTROL for every scope assertion below.

    At the parent commit no run printed a scope at all, so `gate.sh` could not
    tell a full run from a narrowed one. If this ever stops reporting FULL, the
    partial-detection tests below become vacuous — they would be asserting that
    a marker nothing emits is absent."""
    proc = _run([str(RUN_TESTS), "--check-targets", str(REPO_ROOT)])
    out = _out(proc)
    assert proc.returncode == 0, out
    assert _scope_line(out) == "FULL", f"a full run did not report SCOPE: FULL\n{out}"


def test_a_target_subset_reports_partial_not_full():
    proc = _run([str(RUN_TESTS), "--targets", "scripts/collector/i3/tests",
                 "--check-targets", str(REPO_ROOT)])
    out = _out(proc)
    assert proc.returncode == 0, out
    assert _scope_line(out) == "PARTIAL", out


def test_a_file_selection_reports_scoped_not_partial():
    """SCOPED and PARTIAL are different claims: a PARTIAL run still ran whole
    targets under their own floors; a SCOPED one ran a slice of one under no
    target floor at all."""
    proc = _run([str(RUN_TESTS), "--files", "scripts/tests/test_scoped_runs.py",
                 "--check-targets", str(REPO_ROOT)])
    out = _out(proc)
    assert proc.returncode == 0, out
    assert _scope_line(out) == "SCOPED", out


def test_a_run_that_aborts_before_resolving_its_scope_says_unknown():
    """🔴 THE DIRECTION THAT MATTERS. An abort must not be indistinguishable
    from a full run — the default is UNKNOWN, and `gate.sh` treats anything that
    is not FULL as not-a-gate."""
    proc = _run([str(RUN_TESTS), "--files", "", "--check-targets", str(REPO_ROOT)])
    out = _out(proc)
    assert proc.returncode == 3, out
    assert _scope_line(out) == "UNKNOWN", out


def test_the_scope_line_precedes_the_result_line():
    """INVARIANT GUARD (not regression coverage). RESULT is the line every
    consumer reads LAST; the ordering is owned by `_emit_verdict` so the happy
    path and the EXIT trap cannot disagree about it."""
    out = _out(_run([str(RUN_TESTS), "--check-targets", str(REPO_ROOT)]))
    lines = out.splitlines()
    scope_i = [i for i, ln in enumerate(lines) if ln.startswith("SCOPE: ")]
    result_i = [i for i, ln in enumerate(lines) if ln.startswith("RESULT: ")]
    assert scope_i and result_i, out
    assert scope_i[-1] < result_i[-1], f"SCOPE must precede RESULT\n{out}"


def test_the_result_line_format_is_unchanged_by_the_scope_work():
    """INVARIANT GUARD. `test_gate_exit_truthfulness.py` matches the verdict
    ANCHORED AT BOTH ENDS. A draft of the earlier subset work appended to that
    line and the anchored assertion was the only thing that objected; this one
    keeps that true while a second machine-readable line is added beside it."""
    out = _out(_run([str(RUN_TESTS), "--check-targets", str(REPO_ROOT)]))
    assert re.search(r"^RESULT: PASS \(exit=0\)$", out, re.M), out


def test_the_scope_vocabulary_is_pinned_two_way():
    """INVARIANT GUARD. The states run-tests.sh can EMIT and the states gate.sh
    RECOGNISES must be the same set, in both directions.

    🔴 The hazard is one-directional drift: adding a state to the emitter that
    the reader does not know turns into `scope=''`, which the reader treats as
    not-FULL — safe. Adding one the reader accepts but nobody emits is the
    dangerous half, because it reads as coverage. Pin both."""
    runner_src = RUN_TESTS.read_text()
    gate_src = GATE.read_text()

    # What the runner can actually emit: the literal assignments to SCOPE_STATE.
    emitted = set(re.findall(r'^\s*SCOPE_STATE="(\w+)"', runner_src, re.M))
    assert emitted, "found no SCOPE_STATE assignments — the extraction is broken"

    # What gate.sh matches. One alternation, so a state missing here silently
    # becomes the empty string on the reader's side.
    m = re.search(r"\^SCOPE: \(([A-Z|]+)\)", gate_src)
    assert m, "gate.sh no longer carries a SCOPE alternation to match against"
    recognised = set(m.group(1).split("|"))

    assert emitted == set(SCOPE_STATES), (
        "run-tests.sh emits a scope state this file does not know about "
        f"(or stopped emitting one): emitted={sorted(emitted)} "
        f"expected={sorted(SCOPE_STATES)}"
    )
    assert recognised == set(SCOPE_STATES), (
        "gate.sh recognises a different set of scope states than run-tests.sh "
        f"emits: recognised={sorted(recognised)} expected={sorted(SCOPE_STATES)}"
    )


def test_the_node_runner_also_states_a_scope():
    """REGRESSION. gate.sh's scope check is uniform across tiers on purpose: a
    per-tier exemption is the shape RULES.md calls "a guard narrower than its
    description", and it would stop covering the node tier the moment that
    runner grew a selection flag."""
    node_runner = REPO_ROOT / "scripts" / "run-node-tests.sh"
    proc = _run([str(node_runner), "--check-suites", str(REPO_ROOT)])
    out = _out(proc)
    assert proc.returncode == 0, out
    assert _scope_line(out) == "FULL", f"the node runner reported no scope\n{out}"


# ==========================================================================
# 3. gate.sh will not report a gate PASS off a narrowed run.
# ==========================================================================

_TIER_HEAD = """
echo "=== pytest scripts/fake ==="
echo "======================== SUMMARY (hermetic set) ========================"
echo "  PASS  scripts/fake  (collected=1234 passed=1234 skipped=0 floor=1200)"
echo "  ----"
echo "  TOTAL collected=1234  passed=1234  skipped=0  failed=0"
"""


def _tier_runner(path: Path, scope: str | None, verdict: str = "PASS",
                 code: int = 0) -> Path:
    """A stand-in runner that prints a chosen scope and verdict.

    Written through `testlib.mockbin.write_exec`, which owns the shebang: the
    nix sandbox has no `/usr/bin/env`, so a hand-written one is dead in the tier
    the merge is actually gated on.
    """
    body = _TIER_HEAD
    if scope is not None:
        body += f'echo "SCOPE: {scope} (fixture)"\n'
    body += f'echo "RESULT: {verdict} (exit={code})"\nexit {code}\n'
    return write_exec(path, body)


def _gate(tmp_path: Path, runner: Path, extra_env: dict | None = None):
    env = {"DEVRC_GATE_PYTEST_RUNNER": str(runner)}
    if extra_env:
        env.update(extra_env)
    return _run([str(GATE), "--tier", "pytest", "--log-dir", str(tmp_path / "logs"),
                 str(REPO_ROOT)], env=env)


def test_gate_reports_pass_for_a_full_scope_runner(tmp_path):
    """🔴 POSITIVE CONTROL, and every refusal below is meaningless without it.
    A check that can only ever say "not a gate" is not a check."""
    r = _tier_runner(tmp_path / "full.sh", "FULL")
    proc = _gate(tmp_path, r)
    out = _out(proc)
    assert proc.returncode == 0, f"a FULL-scope green runner was not accepted.\n{out}"
    assert re.search(r"^GATE: RESULT=PASS exit=0$", out, re.M), out


def test_gate_refuses_to_report_pass_off_a_target_subset(tmp_path):
    """REGRESSION. Measured at the parent commit: a 1-of-28-target run through
    gate.sh printed `GATE: RESULT=PASS exit=0`."""
    r = _tier_runner(tmp_path / "part.sh", "PARTIAL")
    proc = _gate(tmp_path, r)
    out = _out(proc)
    assert proc.returncode == 91, f"rc={proc.returncode}\n{out}"
    assert re.search(r"^GATE: RESULT=PARTIAL exit=91$", out, re.M), out
    assert not re.search(r"^GATE: RESULT=PASS", out, re.M), out


def test_gate_refuses_to_report_pass_off_a_file_scope(tmp_path):
    r = _tier_runner(tmp_path / "sc.sh", "SCOPED")
    proc = _gate(tmp_path, r)
    out = _out(proc)
    assert proc.returncode == 91, f"rc={proc.returncode}\n{out}"
    assert re.search(r"^GATE: RESULT=PARTIAL exit=91$", out, re.M), out


def test_gate_does_not_read_a_MISSING_scope_line_as_full(tmp_path):
    """🔴 THE POSITIVE-CONTROL HALF, and the one an absence check would fail.

    "Warn when narrowed, treat silence as full" would make an old runner, a
    truncated log and a renamed marker all read as a full gate. A runner that
    says nothing has not told us it ran everything."""
    r = _tier_runner(tmp_path / "silent.sh", None)
    proc = _gate(tmp_path, r)
    out = _out(proc)
    assert proc.returncode == 91, (
        "a runner that printed NO scope line was accepted as a full gate.\n"
        f"rc={proc.returncode}\n{out}"
    )
    assert "printed no SCOPE line" in out, out


def test_gate_treats_an_unknown_scope_as_not_a_gate(tmp_path):
    r = _tier_runner(tmp_path / "unk.sh", "UNKNOWN")
    proc = _gate(tmp_path, r)
    assert proc.returncode == 91, _out(proc)


def test_a_real_failure_outranks_a_partial_scope(tmp_path):
    """INVARIANT GUARD, not regression coverage — MEASURED green at the parent
    commit too, because a red runner already exited 1 there and the new PARTIAL
    branch simply never gets the chance. It pins the PRECEDENCE the new exit
    code could have broken: FAIL(1) beats PARTIAL(91), because a genuine failure
    is the more actionable finding and narrowing should only decide the verdict
    of a run that would otherwise have passed."""
    r = _tier_runner(tmp_path / "redpart.sh", "PARTIAL", verdict="FAIL", code=1)
    proc = _gate(tmp_path, r)
    out = _out(proc)
    assert proc.returncode == 1, f"rc={proc.returncode}\n{out}"
    assert re.search(r"^GATE: RESULT=FAIL exit=1$", out, re.M), out


def test_unvouched_outranks_a_partial_scope(tmp_path):
    """INVARIANT GUARD, not regression coverage — green at the parent commit for
    the same reason as the test above. It pins that a gate which cannot trust
    its own instrument reports UNVOUCHED and nothing else, PARTIAL included."""
    r = _tier_runner(tmp_path / "liar.sh", "PARTIAL", verdict="FAIL", code=0)
    proc = _gate(tmp_path, r)
    out = _out(proc)
    assert proc.returncode == 90, f"rc={proc.returncode}\n{out}"
    assert re.search(r"^GATE: RESULT=UNVOUCHED exit=90$", out, re.M), out


def test_gate_refuses_an_ambient_devrc_targets_before_running_anything(tmp_path):
    """REGRESSION. gate.sh has no --targets flag, so the only way its pytest
    tier gets narrowed is an exported DEVRC_TARGETS — a value nobody typed for
    THIS command. The post-hoc scope check catches it too, but only after paying
    for the run; this catches it in milliseconds and names the variable."""
    marker = tmp_path / "ran"
    r = write_exec(
        tmp_path / "canary.sh",
        f'touch {marker}\necho "SCOPE: FULL (fixture)"\necho "RESULT: PASS (exit=0)"\n',
    )
    proc = _gate(tmp_path, r, {"DEVRC_TARGETS": "scripts/collector/i3/tests"})
    out = _out(proc)
    assert proc.returncode == 2, f"rc={proc.returncode}\n{out}"
    assert "DEVRC_TARGETS is set in this environment" in out, out
    assert not marker.exists(), (
        "gate.sh started the runner before refusing — the whole point of the "
        "pre-flight is that it costs nothing."
    )


# ==========================================================================
# 4. A scoped run behaves: it runs the named files, under no target floor.
# ==========================================================================

def _scoped_fixture(tmp_path: Path, floor: int = 400):
    """A throwaway target holding two modules of known, DISTINCT sizes.

    🔴 The two counts are deliberately not equal and neither is a multiple of
    the other: a fixture whose modules collect the same number cannot see a
    mutant that runs the wrong file, and would survive a fully green suite.
    """
    d = tmp_path / "target"
    write_pytest_suite(d, 3, prefix="test_alpha")
    write_pytest_suite(d, 7, prefix="test_beta")
    runner = tmp_path / "run-tests.sh"
    runner.write_text(patch_runner_source(
        RUN_TESTS.read_text(), targets=[str(d)], floors={str(d): floor},
        ack=[], hook_tests=[], shell_tests=[],
    ))
    return d, runner


def test_a_scoped_run_executes_only_the_named_file(tmp_path):
    """REGRESSION. At the parent commit `--files` was swallowed as a ROOT and the
    whole target ran, collecting 10."""
    d, runner = _scoped_fixture(tmp_path, floor=1)
    proc = _run([str(runner), str(REPO_ROOT), "--files", str(d / "test_beta.py")],
                env={"MIN_TESTS": "1"})
    out = _out(proc)
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
    d, runner = _scoped_fixture(tmp_path, floor=400)
    proc = _run([str(runner), str(REPO_ROOT), "--files", str(d / "test_beta.py")])
    out = _out(proc)
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
    proc = _run([str(runner), str(REPO_ROOT), "--files", str(empty)],
                env={"MIN_TESTS": "1"})
    out = _out(proc)
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
    proc = _run([str(runner), str(REPO_ROOT), "--files", sel],
                env={"MIN_TESTS": "1"})
    out = _out(proc)
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
    d, runner = _scoped_fixture(tmp_path, floor=400)
    target_file = d / "test_beta.py"
    proc = _run([str(runner), str(REPO_ROOT), "--files", str(target_file)])
    out = _out(proc)
    assert proc.returncode == 0, out
    # Anchored at column 0 and matched as whole lines: `tmp_path` is named after
    # this test and appears in the output, so a substring search for a word in
    # this test's own name would match the path rather than the banner.
    assert re.search(r"^={8,} SUMMARY .* — SCOPED: 1 file\(s\) in 1 of 1 ", out, re.M), out
    assert re.search(r"^  🔴 SCOPED RUN — NOT A GATE RUN\.", out, re.M), out
    assert re.search(r"^     file: " + re.escape(str(target_file)) + r"$", out, re.M), out
    assert _scope_line(out) == "SCOPED", out


def test_a_scoped_row_never_prints_the_targets_floor(tmp_path):
    """A `floor=400` on a row covering one file of a target is a coverage claim
    about the whole target — the same false-label defect GUARD 5's and
    --check-floors' subset messages each had to grow a branch for."""
    d, runner = _scoped_fixture(tmp_path, floor=400)
    proc = _run([str(runner), str(REPO_ROOT), "--files", str(d / "test_beta.py")])
    out = _out(proc)
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
    d, runner = _scoped_fixture(tmp_path, floor=400)
    proc = _run([str(runner), str(REPO_ROOT), "--files",
                 f"{d / 'test_alpha.py'} {d / 'test_beta.py'}"])
    out = _out(proc)
    assert proc.returncode == 0, f"rc={proc.returncode}\n{out}"
    headers = re.findall(r"^=== pytest ", out, re.M)
    assert len(headers) == 1, f"expected ONE pytest invocation, got {len(headers)}\n{out}"
    assert re.search(r"^  TOTAL collected=10 ", out, re.M), out


# ==========================================================================
# 5. scoped-tests.sh: the mapper never reports "nothing to do" as success.
# ==========================================================================

def _repo(tmp_path: Path) -> Path:
    """A throwaway git repo. `-c` for identity so the operator's real git config
    is neither read for a committer nor written to (GUARD 10's subject)."""
    r = tmp_path / "repo"
    (r / "scripts" / "tests").mkdir(parents=True)
    subprocess.run(["git", "-C", str(r), "init", "-q", "-b", "main"], check=True)
    subprocess.run(["git", "-C", str(r), "config", "user.email", "t@example.invalid"],
                   check=True)
    subprocess.run(["git", "-C", str(r), "config", "user.name", "T"], check=True)
    return r


def _commit(r: Path, msg: str = "c") -> None:
    subprocess.run(["git", "-C", str(r), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(r), "commit", "-q", "-m", msg], check=True)


def _stub_runner(tmp_path: Path, targets: list[str]) -> Path:
    """Stand in for run-tests.sh: answers --check-targets, and on a real
    invocation records the argv it was handed so a test can read the selection
    back rather than infer it from a verdict."""
    rec = tmp_path / "argv.txt"
    body = (
        'case "$*" in\n'
        '  *--check-targets*)\n'
        + "".join(f'    echo "  dir   {t}"\n' for t in targets)
        + '    echo "RESULT: PASS (exit=0)"\n'
        '    exit 0\n'
        '    ;;\n'
        'esac\n'
        f'printf "%s\\n" "$*" > {rec}\n'
        'echo "SCOPE: SCOPED (fixture)"\n'
        'echo "RESULT: PASS (exit=0)"\n'
    )
    write_exec(tmp_path / "stub-runner.sh", body)
    return rec


def test_scoped_tests_refuses_when_the_mapping_selects_nothing(tmp_path):
    """🔴 THE CENTRAL REFUSAL. A wrapper that reports "nothing to do" and exits
    0 is precisely how a false green gets believed here."""
    r = _repo(tmp_path)
    (r / "scripts" / "tests" / "test_a.py").write_text("def test_a():\n    assert True\n")
    _commit(r)
    # A change no test names.
    (r / "unrelated.conf").write_text("nothing references this\n")
    rec = _stub_runner(tmp_path, [str(r / "scripts" / "tests")])
    proc = _run([str(SCOPED), "--base", "HEAD", str(r)],
                env={"DEVRC_SCOPED_RUNNER": str(tmp_path / "stub-runner.sh")},
                cwd=r)
    out = _out(proc)
    assert proc.returncode == 4, f"rc={proc.returncode}\n{out}"
    assert "to ZERO test files" in out, out
    assert "This is NOT a pass" in out, out
    assert not rec.exists(), "the runner was invoked despite an empty selection"


def test_scoped_tests_refuses_an_empty_diff(tmp_path):
    """"Nothing changed" is not "everything passed"."""
    r = _repo(tmp_path)
    (r / "scripts" / "tests" / "test_a.py").write_text("def test_a():\n    assert True\n")
    _commit(r)
    _stub_runner(tmp_path, [str(r / "scripts" / "tests")])
    proc = _run([str(SCOPED), "--base", "HEAD", str(r)],
                env={"DEVRC_SCOPED_RUNNER": str(tmp_path / "stub-runner.sh")},
                cwd=r)
    out = _out(proc)
    assert proc.returncode == 4, f"rc={proc.returncode}\n{out}"
    assert "the diff is EMPTY" in out, out


def test_scoped_tests_selects_a_changed_test_file_itself(tmp_path):
    r = _repo(tmp_path)
    (r / "scripts" / "tests" / "test_a.py").write_text("def test_a():\n    assert True\n")
    _commit(r)
    (r / "scripts" / "tests" / "test_a.py").write_text(
        "def test_a():\n    assert True\n\n\ndef test_b():\n    assert True\n")
    rec = _stub_runner(tmp_path, [str(r / "scripts" / "tests")])
    proc = _run([str(SCOPED), "--base", "HEAD", str(r)],
                env={"DEVRC_SCOPED_RUNNER": str(tmp_path / "stub-runner.sh")},
                cwd=r)
    out = _out(proc)
    assert proc.returncode == 0, f"rc={proc.returncode}\n{out}"
    assert rec.exists(), f"the runner was never invoked\n{out}"
    argv = rec.read_text()
    assert "--files" in argv, argv
    assert "scripts/tests/test_a.py" in argv, argv


def test_scoped_tests_selects_a_test_that_names_a_changed_non_test_file(tmp_path):
    """The rule that makes this worth anything: a change to a script maps to the
    tests that NAME it, which is how a one-file edit outside a test dir gets a
    cheap run instead of the 13k-test monolith."""
    r = _repo(tmp_path)
    (r / "scripts" / "widget-tool.sh").write_text("#!/usr/bin/env bash\necho hi\n")
    (r / "scripts" / "tests" / "test_widget.py").write_text(
        'PATH_UNDER_TEST = "scripts/widget-tool.sh"\n\n\ndef test_w():\n    assert True\n')
    (r / "scripts" / "tests" / "test_other.py").write_text(
        "def test_o():\n    assert True\n")
    _commit(r)
    (r / "scripts" / "widget-tool.sh").write_text("#!/usr/bin/env bash\necho bye\n")
    rec = _stub_runner(tmp_path, [str(r / "scripts" / "tests")])
    proc = _run([str(SCOPED), "--base", "HEAD", str(r)],
                env={"DEVRC_SCOPED_RUNNER": str(tmp_path / "stub-runner.sh")},
                cwd=r)
    out = _out(proc)
    assert proc.returncode == 0, f"rc={proc.returncode}\n{out}"
    argv = rec.read_text()
    assert "test_widget.py" in argv, argv
    # The discriminating half: the OTHER test must NOT be selected. Without it
    # this passes for a mapper that selects everything.
    assert "test_other.py" not in argv, (
        f"the mapper selected a test that does not name the changed file.\n{argv}")


def test_scoped_tests_reports_changed_files_it_could_not_map(tmp_path):
    """A heuristic that hides its misses reads as coverage. An unmapped file is
    printed whether or not anything else mapped."""
    r = _repo(tmp_path)
    (r / "scripts" / "tests" / "test_a.py").write_text("def test_a():\n    assert True\n")
    _commit(r)
    (r / "scripts" / "tests" / "test_a.py").write_text(
        "def test_a():\n    assert True\n\n\ndef test_b():\n    assert True\n")
    (r / "orphan-artifact.conf").write_text("nothing names this\n")
    _stub_runner(tmp_path, [str(r / "scripts" / "tests")])
    proc = _run([str(SCOPED), "--base", "HEAD", str(r)],
                env={"DEVRC_SCOPED_RUNNER": str(tmp_path / "stub-runner.sh")},
                cwd=r)
    out = _out(proc)
    assert proc.returncode == 0, f"rc={proc.returncode}\n{out}"
    assert re.search(r"^  🔴 UNMAPPED", out, re.M), out
    assert re.search(r"^       orphan-artifact\.conf$", out, re.M), out


def test_scoped_tests_dry_run_runs_nothing(tmp_path):
    r = _repo(tmp_path)
    (r / "scripts" / "tests" / "test_a.py").write_text("def test_a():\n    assert True\n")
    _commit(r)
    (r / "scripts" / "tests" / "test_a.py").write_text(
        "def test_a():\n    assert True\n\n\ndef test_b():\n    assert True\n")
    rec = _stub_runner(tmp_path, [str(r / "scripts" / "tests")])
    proc = _run([str(SCOPED), "--base", "HEAD", "--dry-run", str(r)],
                env={"DEVRC_SCOPED_RUNNER": str(tmp_path / "stub-runner.sh")},
                cwd=r)
    out = _out(proc)
    assert proc.returncode == 0, out
    assert not rec.exists(), "a dry run invoked the runner"
    assert "A dry run is not a verdict" in out, out


def test_scoped_tests_refuses_an_unreadable_target_list(tmp_path):
    """An empty target list would map every change to nothing, and that would
    then be reported as an empty SELECTION — a fault in the caller wearing the
    costume of a clean diff. Refuse instead, naming the command that failed."""
    r = _repo(tmp_path)
    (r / "scripts" / "tests" / "test_a.py").write_text("def test_a():\n    assert True\n")
    _commit(r)
    (r / "scripts" / "tests" / "test_a.py").write_text(
        "def test_a():\n    assert True\n\n\ndef test_b():\n    assert True\n")
    broken = write_exec(tmp_path / "broken.sh", "exit 7\n")
    proc = _run([str(SCOPED), "--base", "HEAD", str(r)],
                env={"DEVRC_SCOPED_RUNNER": str(broken)}, cwd=r)
    out = _out(proc)
    assert proc.returncode == 2, f"rc={proc.returncode}\n{out}"
    assert "could not read the declared target list" in out, out


def test_scoped_tests_is_executable_and_syntactically_valid():
    """INVARIANT GUARD. A new file must be `git add`ed or the flake silently
    omits it from the deploy; this at least fails loudly if the file stops
    parsing."""
    assert SCOPED.exists(), f"{SCOPED} is missing"
    assert os.access(SCOPED, os.X_OK), f"{SCOPED} is not executable"
    proc = subprocess.run(["bash", "-n", str(SCOPED)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
