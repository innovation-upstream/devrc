"""`gate.sh` will not report a gate PASS off a run that is not `SCOPE: FULL`.

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
    TIER_HEAD,
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


def test_gate_reports_pass_for_a_full_scope_runner(tmp_path):
    """🔴 POSITIVE CONTROL, and every refusal below is meaningless without it.
    A check that can only ever say "not a gate" is not a check."""
    r = tier_runner(tmp_path / "full.sh", "FULL")
    proc = gate(tmp_path, r)
    out = out_of(proc)
    assert proc.returncode == 0, f"a FULL-scope green runner was not accepted.\n{out}"
    assert re.search(r"^GATE: RESULT=PASS exit=0$", out, re.M), out


def test_gate_refuses_to_report_pass_off_a_target_subset(tmp_path):
    """REGRESSION. Measured at the parent commit: a 1-of-28-target run through
    gate.sh printed `GATE: RESULT=PASS exit=0`."""
    r = tier_runner(tmp_path / "part.sh", "PARTIAL")
    proc = gate(tmp_path, r)
    out = out_of(proc)
    assert proc.returncode == 91, f"rc={proc.returncode}\n{out}"
    assert re.search(r"^GATE: RESULT=PARTIAL exit=91$", out, re.M), out
    assert not re.search(r"^GATE: RESULT=PASS", out, re.M), out


def test_gate_refuses_to_report_pass_off_a_file_scope(tmp_path):
    r = tier_runner(tmp_path / "sc.sh", "SCOPED")
    proc = gate(tmp_path, r)
    out = out_of(proc)
    assert proc.returncode == 91, f"rc={proc.returncode}\n{out}"
    assert re.search(r"^GATE: RESULT=PARTIAL exit=91$", out, re.M), out


def test_gate_does_not_read_a_MISSING_scope_line_as_full(tmp_path):
    """🔴 THE POSITIVE-CONTROL HALF, and the one an absence check would fail.

    "Warn when narrowed, treat silence as full" would make an old runner, a
    truncated log and a renamed marker all read as a full gate. A runner that
    says nothing has not told us it ran everything."""
    r = tier_runner(tmp_path / "silent.sh", None)
    proc = gate(tmp_path, r)
    out = out_of(proc)
    assert proc.returncode == 91, (
        "a runner that printed NO scope line was accepted as a full gate.\n"
        f"rc={proc.returncode}\n{out}"
    )
    assert "printed no SCOPE line" in out, out


def test_gate_treats_an_unknown_scope_as_not_a_gate(tmp_path):
    r = tier_runner(tmp_path / "unk.sh", "UNKNOWN")
    proc = gate(tmp_path, r)
    assert proc.returncode == 91, out_of(proc)


def test_a_real_failure_outranks_a_partial_scope(tmp_path):
    """INVARIANT GUARD, not regression coverage — MEASURED green at the parent
    commit too, because a red runner already exited 1 there and the new PARTIAL
    branch simply never gets the chance. It pins the PRECEDENCE the new exit
    code could have broken: FAIL(1) beats PARTIAL(91), because a genuine failure
    is the more actionable finding and narrowing should only decide the verdict
    of a run that would otherwise have passed."""
    r = tier_runner(tmp_path / "redpart.sh", "PARTIAL", verdict="FAIL", code=1)
    proc = gate(tmp_path, r)
    out = out_of(proc)
    assert proc.returncode == 1, f"rc={proc.returncode}\n{out}"
    assert re.search(r"^GATE: RESULT=FAIL exit=1$", out, re.M), out


def test_unvouched_outranks_a_partial_scope(tmp_path):
    """INVARIANT GUARD, not regression coverage — green at the parent commit for
    the same reason as the test above. It pins that a gate which cannot trust
    its own instrument reports UNVOUCHED and nothing else, PARTIAL included."""
    r = tier_runner(tmp_path / "liar.sh", "PARTIAL", verdict="FAIL", code=0)
    proc = gate(tmp_path, r)
    out = out_of(proc)
    assert proc.returncode == 90, f"rc={proc.returncode}\n{out}"
    assert re.search(r"^GATE: RESULT=UNVOUCHED exit=90$", out, re.M), out


def test_a_forged_scope_line_earlier_in_the_stream_cannot_win(tmp_path):
    """🔴 THE SAME HAZARD `test_result_grammar_is_reserved.py` exists for, on the
    new grammar. run-tests.sh inlines its HOOK_TESTS/SHELL_TESTS stdout straight
    into its own stream with no prefixing, and a pytest PLUGIN can reach column 0
    too — so a `SCOPE: FULL` printed by something other than the runner is
    physically possible.

    What makes it non-exploitable is the SELECTION RULE, not the absence of a
    forger: the runner emits its own scope from the EXIT trap, i.e. LAST, and
    gate.sh takes `| tail -1`. A first-match reader would take the forgery.
    This pins the rule with a runner that forges FULL early and reports SCOPED
    at the end.

    ⚠ NOT the structural population scan `RESULT:` gets. Extending that scanner
    to a second grammar is a separate change; this pins the mechanism that makes
    the forgery lose, which is the half that decides the verdict."""
    body = (
        'echo "SCOPE: FULL (forged by a registry entry)"\n'
        + TIER_HEAD
        + 'echo "SCOPE: SCOPED (the runner\'s own, from the EXIT trap)"\n'
          'echo "RESULT: PASS (exit=0)"\n'
    )
    r = write_exec(tmp_path / "forge.sh", body)
    proc = gate(tmp_path, r)
    out = out_of(proc)
    assert proc.returncode == 91, (
        "a forged `SCOPE: FULL` earlier in the stream was taken over the "
        f"runner's own trailing SCOPED.\nrc={proc.returncode}\n{out}"
    )


def test_gate_refuses_an_ambient_devrc_targets_before_running_anything(tmp_path):
    """REGRESSION. gate.sh has no --targets flag, so the only way its pytest
    tier gets narrowed is an exported DEVRC_TARGETS — a value nobody typed for
    THIS command. The post-hoc scope check catches it too, but only after paying
    for the run; this catches it in milliseconds and names the variable.

    🔴 ALL FOUR gate-weakening variables are refused, not just that one. The
    runner-replacement pair is the worst of them: a canary that prints a green
    verdict having run nothing yields a green gate, and NO later check can see
    it — which is why this test drives the refusal WITHOUT
    `DEVRC_GATE_ALLOW_AMBIENT` (every other gate test here sets it, because they
    exist to drive that seam deliberately)."""
    marker = tmp_path / "ran"
    r = write_exec(
        tmp_path / "canary.sh",
        f'touch {marker}\necho "SCOPE: FULL (fixture)"\necho "RESULT: PASS (exit=0)"\n',
    )
    for env, want in (
        ({"DEVRC_TARGETS": "scripts/collector/i3/tests"}, "DEVRC_TARGETS="),
        ({"MIN_TESTS": "1"}, "MIN_TESTS="),
        ({}, "DEVRC_GATE_PYTEST_RUNNER="),   # the runner seam itself
    ):
        if marker.exists():
            marker.unlink()
        proc = run([str(GATE), "--tier", "pytest", "--log-dir", str(tmp_path / "logs"),
                     str(REPO_ROOT)],
                    env={"DEVRC_GATE_PYTEST_RUNNER": str(r), **env})
        out = out_of(proc)
        assert proc.returncode == 2, f"{want}: rc={proc.returncode}\n{out}"
        assert "gate-weakening variable(s) set" in out, out
        assert want in out, f"the refusal did not name {want}\n{out}"
        assert not marker.exists(), (
            f"{want}: gate.sh started the runner before refusing — the whole "
            "point of the pre-flight is that it costs nothing.")

