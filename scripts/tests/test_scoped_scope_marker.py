"""The `SCOPE:` marker — positive, on every exit path, in one vocabulary.

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


def test_a_full_run_states_its_scope_positively(tmp_path):
    """REGRESSION + POSITIVE CONTROL for every scope assertion below.

    At the parent commit no run printed a scope at all, so `gate.sh` could not
    tell a full run from a narrowed one. If this ever stops reporting FULL, the
    partial-detection tests below become vacuous — they would be asserting that
    a marker nothing emits is absent."""
    _a, _b, runner = two_target_fixture(tmp_path)
    proc = run([str(runner), str(REPO_ROOT)], env={"MIN_TESTS": "1"})
    out = out_of(proc)
    assert proc.returncode == 0, out
    assert scope_line(out) == "FULL", f"a full run did not report SCOPE: FULL\n{out}"


def test_a_target_subset_reports_partial_not_full(tmp_path):
    a, _b, runner = two_target_fixture(tmp_path)
    proc = run([str(runner), str(REPO_ROOT), "--targets", str(a)],
                env={"MIN_TESTS": "1"})
    out = out_of(proc)
    assert proc.returncode == 0, out
    assert scope_line(out) == "PARTIAL", out


def test_a_file_selection_reports_scoped_not_partial(tmp_path):
    """SCOPED and PARTIAL are different claims: a PARTIAL run still ran whole
    targets under their own floors; a SCOPED one ran a slice of one under no
    target floor at all."""
    a, _b, runner = two_target_fixture(tmp_path)
    proc = run([str(runner), str(REPO_ROOT), "--files", str(a / "test_a1.py")],
                env={"MIN_TESTS": "1"})
    out = out_of(proc)
    assert proc.returncode == 0, out
    assert scope_line(out) == "SCOPED", out


def test_a_ZERO_TEST_invocation_reports_NONE_never_FULL():
    """🔴 REGRESSION on a false green in the content contract this PR exists to
    establish. All three check-only invocations validate a table and exit 0 in
    about two seconds having collected NOTHING — and they printed
    `SCOPE: FULL` + `RESULT: PASS (exit=0)`, which is the full-gate-shaped pair.

    `gate.sh` cannot be driven into it (it passes no such flag), so this was
    never a false green THROUGH the gate. It is a false green in the pair
    CLAUDE.md now tells readers to parse, and the entire value of `SCOPE: FULL`
    is that it can be believed without re-running anything.

    ⚠ NONE wins over narrowing on purpose: `--check-targets --targets X` ran no
    tests either, and a scope state is a claim about coverage."""
    for args in (["--check-targets"], ["--check-floors"],
                 ["--targets", "scripts/collector/i3/tests", "--check-targets"]):
        proc = run([str(RUN_TESTS), *args, str(REPO_ROOT)])
        out = out_of(proc)
        assert proc.returncode == 0, f"{args}: rc={proc.returncode}\n{out}"
        assert scope_line(out) == "NONE", (
            f"{args} ran no tests but reported SCOPE: {scope_line(out)}\n{out}")
    node_runner = REPO_ROOT / "scripts" / "run-node-tests.sh"
    proc = run([str(node_runner), "--check-suites", str(REPO_ROOT)])
    out = out_of(proc)
    assert proc.returncode == 0, out
    assert scope_line(out) == "NONE", (
        f"--check-suites ran no tests but reported SCOPE: {scope_line(out)}\n{out}")


def test_a_run_that_aborts_before_resolving_its_scope_says_unknown():
    """🔴 THE DIRECTION THAT MATTERS. An abort must not be indistinguishable
    from a full run — the default is UNKNOWN, and `gate.sh` treats anything that
    is not FULL as not-a-gate."""
    proc = run([str(RUN_TESTS), "--files", "", "--check-targets", str(REPO_ROOT)])
    out = out_of(proc)
    assert proc.returncode == 3, out
    assert scope_line(out) == "UNKNOWN", out


def test_the_scope_line_precedes_the_result_line():
    """INVARIANT GUARD (not regression coverage). RESULT is the line every
    consumer reads LAST; the ordering is owned by `_emit_verdict` so the happy
    path and the EXIT trap cannot disagree about it."""
    out = out_of(run([str(RUN_TESTS), "--check-targets", str(REPO_ROOT)]))
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
    out = out_of(run([str(RUN_TESTS), "--check-targets", str(REPO_ROOT)]))
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


def test_the_node_runner_reports_FULL_on_a_REAL_run():
    """REGRESSION + the POSITIVE CONTROL that keeps the NONE assertion honest.

    gate.sh's scope check is uniform across tiers on purpose: a per-tier
    exemption is the shape RULES.md calls "a guard narrower than its
    description", and it would stop covering the node tier the moment that
    runner grew a selection flag.

    🔴 THIS DRIVES A REAL RUN, not `--check-suites`. Since that flag now reports
    NONE, a test asserting only NONE would be satisfied by a runner that emits
    NONE unconditionally — i.e. one that can never say FULL, which would make
    `gate.sh` unable to pass the node tier at all. The two states have to be
    observed separately or neither is pinned."""
    node_runner = REPO_ROOT / "scripts" / "run-node-tests.sh"
    proc = run([str(node_runner), str(REPO_ROOT)], timeout=900)
    out = out_of(proc)
    assert proc.returncode == 0, f"the real node tier is red\n{out[-3000:]}"
    assert scope_line(out) == "FULL", f"a real node run did not report FULL\n{out[-2000:]}"

