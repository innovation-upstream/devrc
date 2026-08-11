"""Guards for the TEST GATE'S OWN EXIT STATUS -- `scripts/gate.sh` and the
`RESULT: … (exit=N)` verdict line both runners now emit.

WHY THIS EXISTS
---------------
`run-tests.sh` and `run-node-tests.sh` have always exited truthfully. What kept
failing was READING that status. Both emit thousands of lines, so every consumer
pipes them::

    bash scripts/run-tests.sh 2>&1 | tail -40 ; echo "rc=$?"
    nix build .#checks.x86_64-linux.pytests 2>&1 | tail -60 ; echo "BUILD_RC=$?"

and a pipeline's status is the LAST command's. On 2026-08-11 four agents hit
this independently, reporting ``exit code 0`` over output containing
``RESULT: FAIL`` and ``failed=1``; the same day's rules audit recorded
``BUILD_RC=0`` over a genuinely red ``nix build``. The repo's CLAUDE.md had to
instruct everyone to COUNT ``PASSED``/``FAILED`` lines instead of reading the
status -- a workaround for a bug, promoted to house style.

Two changes, and this file covers both:

  1. the runners put the status IN THE CONTENT, one line, one writer, behind an
     EXIT trap -- so it survives a pipe, and an abort or a `timeout` kill ends
     with a verdict instead of the silence a content-parser reads as "clean";
  2. `scripts/gate.sh` removes the REASON to pipe (full output to a log FILE,
     bounded summary to stdout) and cross-checks the status it observed against
     the verdict it read, refusing to vouch (exit 90) when they disagree.

WHAT IS REGRESSION COVERAGE HERE AND WHAT IS NOT
------------------------------------------------
  * ``test_the_verdict_line_carries_the_exit_code`` and
    ``test_a_verdict_is_emitted_when_the_runner_is_killed`` are REGRESSION
    coverage. Both are RED at origin/main: the verdict there is a bare
    ``RESULT: FAIL`` with no exit code, and a killed runner prints no verdict at
    all.
  * every ``gate.sh`` test is REGRESSION coverage in the sense that the file
    does not exist at origin/main -- but the DISAGREEMENT tests are the load
    -bearing ones, since making an exit code non-zero unconditionally would
    satisfy a bare "it failed" assertion.
  * ``test_the_disagreement_check_is_what_catches_a_lying_runner`` is the
    MUTATION test for gate.sh's cross-check.
"""
from __future__ import annotations

import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))
from testlib.mockbin import write_exec  # noqa: E402
from testlib.runner_patch import runner_with_targets, write_pytest_suite  # noqa: E402

GATE = REPO_ROOT / "scripts" / "gate.sh"
RUN_TESTS = REPO_ROOT / "scripts" / "run-tests.sh"
RUN_NODE = REPO_ROOT / "scripts" / "run-node-tests.sh"


def _fake_runner(path: Path, body: str) -> Path:
    """Write a stand-in runner via `testlib.mockbin.write_exec`.

    NOT a hand-written `#!/usr/bin/env bash`: the nix build sandbox has no
    `/usr/bin/env`, so such a stub execs on a dev host and ENOENTs in the tier
    that gates merges. (Caught here by `test_runtime_shebangs.py` -- the first
    draft of this file did exactly that, and both tiers went red naming line
    65. The bodies below are POSIX sh.)
    """
    return write_exec(path, body)


def _gate(tmp_path: Path, *, pytest_runner: Path, extra: list[str] | None = None,
          timeout: int = 120) -> subprocess.CompletedProcess:
    """Drive gate.sh's pytest tier against a stand-in runner.

    The seam is deliberate and narrow: a REAL 7,200-test run per negative
    control would cost minutes each, and three of the states under test (a hang,
    a truncation panic, a runner that lies about its own status) cannot be
    produced by the real runner at all.
    """
    env = {
        **os.environ,
        "DEVRC_GATE_PYTEST_RUNNER": str(pytest_runner),
    }
    return subprocess.run(
        ["bash", str(GATE), "--tier", "pytest", "--log-dir", str(tmp_path / "logs"),
         *(extra or []), str(REPO_ROOT)],
        cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=timeout, env=env,
    )


GREEN_BODY = """
echo "=== pytest scripts/fake ==="
echo "======================== SUMMARY (hermetic set) ========================"
echo "  PASS  scripts/fake  (collected=1234 passed=1234 skipped=0 floor=1200)"
echo "  ----"
echo "  TOTAL collected=1234  passed=1234  skipped=0  failed=0  (floor: 1200)"
echo "RESULT: PASS (exit=0)"
exit 0
"""

RED_BODY = """
echo "=== pytest scripts/fake ==="
echo "======================== SUMMARY (hermetic set) ========================"
echo "  FAIL  scripts/fake  (collected=1234 passed=1233 skipped=0 failed=1 errors=0)"
echo "  ----"
echo "  TOTAL collected=1234  passed=1233  skipped=0  failed=1  (floor: 1200)"
echo "RESULT: FAIL (exit=1)"
exit 1
"""


# --------------------------------------------------------------------------
# POSITIVE CONTROL first: prove the gate can observe a pass at all, and reports
# a plausible non-zero count. A gate that only ever goes red is not a gate.
# --------------------------------------------------------------------------

def test_a_green_runner_gives_a_green_gate_and_a_nonzero_count(tmp_path):
    r = _fake_runner(tmp_path / "green.sh", GREEN_BODY)
    proc = _gate(tmp_path, pytest_runner=r)
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, f"a green tier did not produce a green gate.\n{out}"
    assert "GATE: RESULT=PASS exit=0" in proc.stdout, out
    m = re.search(r"TOTAL collected=(\d+)", proc.stdout)
    assert m and int(m.group(1)) > 0, (
        "the gate reported no collected count, so a run that tested NOTHING "
        f"would look the same as this one.\n{out}"
    )


# --------------------------------------------------------------------------
# NEGATIVE CONTROLS. Each asserts the PAIR (status AND content), because an
# unconditionally non-zero exit would satisfy either half alone.
# --------------------------------------------------------------------------

def test_a_failing_runner_gives_a_nonzero_gate_and_a_fail_verdict(tmp_path):
    r = _fake_runner(tmp_path / "red.sh", RED_BODY)
    proc = _gate(tmp_path, pytest_runner=r)
    out = proc.stdout + proc.stderr
    assert proc.returncode == 1, f"a failing tier did not fail the gate.\n{out}"
    assert "GATE: RESULT=FAIL exit=1" in proc.stdout, out
    assert "FAIL  pytest  exit=1" in proc.stdout, out


def test_a_runner_that_lies_about_its_status_is_not_vouched_for(tmp_path):
    """THE case the whole change is about, in its purest form: content says
    FAIL, status says 0. The gate must refuse to answer, not pick the
    reassuring side."""
    r = _fake_runner(tmp_path / "liar.sh", RED_BODY.replace("exit 1", "exit 0"))
    proc = _gate(tmp_path, pytest_runner=r)
    out = proc.stdout + proc.stderr
    assert proc.returncode == 90, (
        f"a runner exiting 0 over RESULT: FAIL was accepted.\n{out}"
    )
    assert "GATE: RESULT=UNVOUCHED exit=90" in proc.stdout, out
    assert "exited 0 while printing" in out, out


def test_the_inverse_disagreement_is_also_caught(tmp_path):
    """Status non-zero over a PASS verdict. Not the observed failure, but the
    same instrument being untrustworthy -- and a check that only looked one way
    would be satisfied by `exit 1` on every disagreement."""
    r = _fake_runner(tmp_path / "grump.sh", GREEN_BODY.replace("exit 0", "exit 3"))
    proc = _gate(tmp_path, pytest_runner=r)
    out = proc.stdout + proc.stderr
    assert proc.returncode == 90, out
    assert "exited 3 while printing" in out, out


def test_a_truncated_run_with_no_verdict_is_not_a_pass(tmp_path):
    """A runner that exits 0 having never reached its own summary. Before the
    EXIT trap this was the shape of every killed run: no RESULT line at all, and
    a content-parsing consumer counting FAILED lines finds zero."""
    r = _fake_runner(tmp_path / "trunc.sh", '\necho "=== pytest scripts/fake ==="\nexit 0\n')
    proc = _gate(tmp_path, pytest_runner=r)
    out = proc.stdout + proc.stderr
    assert proc.returncode == 90, f"a runner with no verdict was read as a pass.\n{out}"
    assert "printed NO 'RESULT:' verdict line" in out, out


def test_a_timeout_panic_in_the_output_is_not_a_pass(tmp_path):
    """`panic: test timed out` truncates a run's output, so every count printed
    after it is missing -- yet the summary that survives can still read
    `FAIL=0`. Grepped for explicitly, independently of the status."""
    body = (
        '\necho "panic: test timed out after 10m0s"\n'
        'echo "======================== SUMMARY ========================"\n'
        'echo "  TOTAL collected=12  passed=12  skipped=0  failed=0  (floor: 1)"\n'
        'echo "RESULT: PASS (exit=0)"\nexit 0\n'
    )
    r = _fake_runner(tmp_path / "panic.sh", body)
    proc = _gate(tmp_path, pytest_runner=r)
    out = proc.stdout + proc.stderr
    assert proc.returncode == 90, f"a truncating timeout panic was read as a pass.\n{out}"
    assert "panic: test timed out" in out and "TRUNCATED" in out, out


def test_a_hanging_runner_is_capped_and_reported_as_a_timeout(tmp_path):
    r = _fake_runner(tmp_path / "hang.sh", '\necho "=== pytest scripts/fake ==="\nsleep 120\n')
    started = time.monotonic()
    proc = _gate(tmp_path, pytest_runner=r, extra=["--timeout", "3"], timeout=90)
    elapsed = time.monotonic() - started
    out = proc.stdout + proc.stderr
    assert proc.returncode != 0, f"a hanging tier produced a passing gate.\n{out}"
    assert elapsed < 60, f"the timeout cap did not apply (took {elapsed:.1f}s)"
    assert "timeout after 3s" in proc.stdout or "UNVOUCHED" in proc.stdout, out


# --------------------------------------------------------------------------
# The verdict must survive the pipe every consumer writes.
# --------------------------------------------------------------------------

def test_the_gate_verdict_survives_a_pipe(tmp_path):
    """The literal shape from the incident: `… | tail`. The STATUS is lost --
    that is what a pipe does and no change here alters it -- but the last line
    of the surviving output now names the verdict, so the reader is not left
    with a bare `rc=0`.
    """
    r = _fake_runner(tmp_path / "red.sh", RED_BODY)
    env = {**os.environ, "DEVRC_GATE_PYTEST_RUNNER": str(r)}
    proc = subprocess.run(
        ["bash", "-c",
         f"bash {GATE} --tier pytest --log-dir {tmp_path / 'logs'} {REPO_ROOT} 2>&1 | tail -3"],
        cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=120, env=env,
    )
    assert proc.returncode == 0, "control: `tail` itself should exit 0 here"
    assert "GATE: RESULT=FAIL exit=1" in proc.stdout, (
        "the verdict did not survive `| tail -3`, so a piped invocation still "
        f"reads as a bare success.\n{proc.stdout}"
    )


# --------------------------------------------------------------------------
# MUTATION: prove gate.sh's cross-check is what catches the lying runner.
# --------------------------------------------------------------------------

def test_the_disagreement_check_is_what_catches_a_lying_runner(tmp_path):
    """Break the cross-check on purpose; the lying runner must then be ACCEPTED.

    Without this, `returncode == 90` would also be satisfied by an unrelated
    early exit -- and the assertion would stay green with the check deleted.
    """
    src = GATE.read_text()
    needle = 'disagree="exited 0 while printing \'$verdict\'"'
    assert src.count(needle) == 1, (
        f"expected one disagreement assignment to mutate, found {src.count(needle)}"
    )
    mutated = tmp_path / "gate-mutant.sh"
    mutated.write_text(src.replace(needle, 'disagree=""'))

    r = _fake_runner(tmp_path / "liar.sh", RED_BODY.replace("exit 1", "exit 0"))
    env = {**os.environ, "DEVRC_GATE_PYTEST_RUNNER": str(r)}
    proc = subprocess.run(
        ["bash", str(mutated), "--tier", "pytest", "--log-dir", str(tmp_path / "m"), str(REPO_ROOT)],
        cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=120, env=env,
    )
    assert proc.returncode == 0, (
        "with the status/content cross-check neutered the lying runner is STILL "
        "rejected, so the test above is red for some other reason and proves "
        f"nothing about that check.\n{proc.stdout}{proc.stderr}"
    )


# --------------------------------------------------------------------------
# The REAL runners, not a stand-in: the verdict carries the exit code, and the
# two agree.
# --------------------------------------------------------------------------

def test_the_verdict_line_carries_the_exit_code(tmp_path):
    """REGRESSION. At origin/main the verdict is a bare ``RESULT: FAIL``, so a
    piped reader learns the run failed but not with what status -- and a reader
    who only has the status learns nothing about the content.

    Forced red through the real runner (an empty target), then the number in the
    verdict is compared with the process's ACTUAL exit status. That agreement is
    the deliverable.
    """
    empty = tmp_path / "empty_tests"
    empty.mkdir()
    runner = runner_with_targets(tmp_path, [str(empty)], floors={str(empty): 1})
    proc = subprocess.run(
        ["bash", str(runner), str(REPO_ROOT)],
        cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=300,
    )
    m = re.search(r"^RESULT: (PASS|FAIL) \(exit=(\d+)\)$", proc.stdout, re.M)
    assert m, (
        "the runner printed no exit-carrying verdict line, so its status still "
        f"only exists outside its output.\n{proc.stdout}"
    )
    assert m.group(1) == "FAIL", proc.stdout
    assert int(m.group(2)) == proc.returncode, (
        f"the verdict claims exit={m.group(2)} but the process exited "
        f"{proc.returncode} -- content and status disagree at the source."
    )
    assert proc.returncode != 0


def test_a_green_real_run_says_pass_with_exit_zero(tmp_path):
    """POSITIVE CONTROL for the line above: it must also be able to say PASS.
    A verdict that only ever reads FAIL would satisfy the test above while
    telling a reader nothing."""
    d = tmp_path / "ok_tests"
    write_pytest_suite(d, 5)
    runner = runner_with_targets(tmp_path, [str(d)], floors={str(d): 1})
    proc = subprocess.run(
        ["bash", str(runner), str(REPO_ROOT)],
        cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=300,
        env={**os.environ, "MIN_TESTS": "1"},
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "RESULT: PASS (exit=0)" in proc.stdout, proc.stdout


@pytest.mark.parametrize("runner", [RUN_TESTS, RUN_NODE], ids=["pytest", "node"])
def test_a_verdict_is_emitted_when_the_runner_is_killed(tmp_path, runner):
    """REGRESSION. A TERM'd run used to end in silence -- no verdict, and a
    content-parsing consumer counting FAILED lines finds zero, which is exactly
    the truncation case that reads as clean.

    Not `--check-targets` / `--check-suites`: those finish too fast to catch mid
    -flight. A real run is started and killed a moment in, which is the state a
    `timeout` or a Ctrl-C actually produces.

    Killed by PROCESS GROUP (`start_new_session` + `killpg`), not by PID: the
    runner's `python -m pytest` / `node --test` child would otherwise outlive
    the test and keep churning in the background, which is both a leak and a
    load source that skews every timing measurement after it.
    """
    proc = subprocess.Popen(
        ["bash", str(runner), str(REPO_ROOT)],
        cwd=str(REPO_ROOT), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        start_new_session=True,
    )
    time.sleep(2.0)
    assert proc.poll() is None, "the runner finished before it could be killed"
    os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    out, _ = proc.communicate(timeout=120)
    assert "RESULT: FAIL (exit=143)" in out, (
        "a TERM'd runner printed no verdict, so a truncated run is "
        f"indistinguishable from a clean one in its output.\n...{out[-800:]}"
    )
    assert proc.returncode != 0
