"""Guards for `scripts/run-tests.sh`'s PRECONDITIONS and its empty-array handling.

WHY THIS EXISTS
---------------
Two measured holes, both of which let the runner produce output that reads
reassuring (or merely confusing) rather than naming what is wrong.

1. ``REQUIRED_TOOLS`` could not express its own most important precondition.
   It is a list of BINARIES checked with ``command -v``. pytest is not a binary
   this runner calls -- it is a MODULE (``python -m pytest``) -- so the one
   guard whose whole job is "the thing that runs the tests is present" was
   structurally unable to check pytest. It also asserted ``python3`` while the
   runner actually invokes ``python``.

   MEASURED 2026-08-03 with every REQUIRED_TOOLS binary present but no pytest
   importable: all 17 targets printed ``could not parse pytest's summary`` and
   the run ended ``TOTAL collected=0 … RESULT: FAIL``, exit 1.

   Being precise, because it changes how much this matters: the gate did NOT go
   green. GUARD 4 and GUARD 3 both fired. The defect is DIAGNOSTIC -- seventeen
   copies of a message blaming pytest's OUTPUT FORMAT for what was actually a
   missing dependency, which is the #276 shape (a real finding that reads like
   an environment fault).

2. ``declare -a RESULTS`` / ``declare -a SKIP_LINES`` leave the arrays DECLARED
   BUT UNSET. Under ``set -u`` the first ``${#arr[@]}`` on a still-empty array
   aborts the command with ``unbound variable`` (measured on bash 5.3.15).
   With zero skips this printed a raw
   ``run-tests.sh: line 479: SKIP_LINES: unbound variable`` where the skip list
   belonged, and the unpinned-skip loop below it never ran. No ``set -e``, so
   the script continued and the skip-TOTAL accounting still fired -- the damage
   was confined to the diagnostic path, at exactly the moment someone is trying
   to read why the gate is red.

WHAT IS REGRESSION COVERAGE HERE AND WHAT IS NOT
------------------------------------------------
Per claude/RULES.md:

  * ``test_missing_pytest_module_is_named``, ``test_python_is_a_required_tool``
    and ``test_empty_arrays_are_initialised_assigned`` are REGRESSION coverage.
    Each is red at origin/main for its own reason (see the PR matrix).

  * ``test_an_empty_target_directory_is_loud`` and
    ``test_a_typod_target_entry_is_loud`` are REACHABILITY proofs for guards
    that already existed before this PR. They are NOT regression coverage for
    this change -- they were requested as known-bad-state validation, and they
    pass at origin/main too. Their value is that nothing had ever proven those
    two paths could actually fire.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
RUN_TESTS = REPO_ROOT / "scripts" / "run-tests.sh"


def _run(args: list[str], env: dict | None = None, timeout: int = 300):
    return subprocess.run(
        ["bash", *args],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
    )


def _runner_with_targets(tmp_path: Path, targets: list[str]) -> Path:
    """Copy run-tests.sh with HERMETIC_TARGETS replaced wholesale.

    Replacing the whole block (rather than injecting one entry) keeps these
    tests FAST: the copy runs only the target under test instead of the full
    5792-test suite, which is what makes a per-target reachability proof
    affordable at all.
    """
    src = RUN_TESTS.read_text()
    body = "\n".join(f"  {t}" for t in targets)
    patched, n = re.subn(
        r"^HERMETIC_TARGETS=\(.*?^\)", f"HERMETIC_TARGETS=(\n{body}\n)", src, count=1, flags=re.S | re.M
    )
    assert n == 1, "failed to replace HERMETIC_TARGETS in the copied runner"
    assert "HERMETIC_TARGETS=(\n  " in patched, "replacement produced an empty target list"
    dst = tmp_path / "run-tests.sh"
    dst.write_text(patched)
    return dst


# --------------------------------------------------------------------------
# Guard the guard.
# --------------------------------------------------------------------------

def test_the_runner_copy_helper_actually_works(tmp_path):
    """POSITIVE CONTROL for _runner_with_targets.

    Every mutation test below rests on this rewrite landing. If the regex
    stopped matching, the copies would silently be the UNMODIFIED runner and the
    'known-bad state' tests would be exercising the real target list instead --
    passing or failing for reasons that have nothing to do with the case they
    claim to cover. So prove the rewrite both applies and takes effect.
    """
    runner = _runner_with_targets(tmp_path, ["scripts/tests"])
    text = runner.read_text()
    assert "scripts/dl-router/tests" not in text.split("HERMETIC_TARGETS=(")[1].split("\n)")[0]
    proc = _run([str(runner), "--check-targets", str(REPO_ROOT)])
    assert proc.returncode == 0, f"{proc.stdout}\n{proc.stderr}"
    assert "all 1 hermetic target(s) resolve" in proc.stdout, (
        f"the copied runner did not use the rewritten 1-entry list.\n{proc.stdout}"
    )


# --------------------------------------------------------------------------
# REGRESSION: hole 2 -- the pytest module precondition.
# --------------------------------------------------------------------------

def test_python_is_a_required_tool():
    """The runner invokes `python -m pytest`; it must assert `python`.

    origin/main listed `python3` -- a binary this script never calls.
    """
    src = RUN_TESTS.read_text()
    m = re.search(r"^REQUIRED_TOOLS=\((.*?)\)", src, re.M | re.S)
    assert m, "could not find REQUIRED_TOOLS in run-tests.sh"
    tools = m.group(1).split()
    assert "python" in tools, (
        f"REQUIRED_TOOLS does not include `python`, but the runner calls "
        f"`python -m pytest`. Got: {tools}"
    )


def test_missing_pytest_module_is_named(tmp_path):
    """RED at origin/main. A pytest-less environment must fail with ONE named error.

    Drives the real runner under a PATH whose `python` cannot import pytest, and
    asserts the failure names the MODULE -- not pytest's output format. The
    negative assertion is the point: at origin/main this run produced 17 copies
    of 'could not parse pytest's summary', a diagnosis pointing at the wrong
    subsystem entirely.
    """
    # A `python` shim that exists and runs but has no pytest. Written without a
    # `#!/usr/bin/env` shebang: the nix build sandbox has no /usr/bin/env, and a
    # stub that is dead in one tier is how #306's portability defect shipped.
    bindir = tmp_path / "bin"
    bindir.mkdir()
    real_bash = shutil.which("bash")
    shim = bindir / "python"
    shim.write_text(
        f"#!{real_bash}\n"
        'if [ "$1" = "-m" ] && [ "$2" = "pytest" ]; then\n'
        '  echo "/usr/bin/python: No module named pytest" >&2; exit 1\n'
        "fi\n"
        "exit 0\n"
    )
    shim.chmod(0o755)

    env = dict(os.environ)
    env["PATH"] = f"{bindir}{os.pathsep}{env.get('PATH', '')}"

    proc = _run([str(RUN_TESTS), str(REPO_ROOT)], env=env)
    out = proc.stdout + proc.stderr

    assert proc.returncode == 2, (
        f"expected the precondition to abort with exit 2, got {proc.returncode}.\n{out}"
    )
    assert "python -m pytest` is not runnable" in out, (
        f"the failure did not name the pytest MODULE precondition.\n{out}"
    )
    # THIS guard's own reason, not a neighbour's: the old, misleading message
    # must be gone, and no suite may have been attempted.
    assert "could not parse pytest's summary" not in out, (
        "the runner still blames pytest's OUTPUT FORMAT for a missing "
        f"dependency.\n{out}"
    )
    assert "=== pytest " not in proc.stdout, (
        f"the runner started a suite despite the precondition failing.\n{proc.stdout}"
    )


# --------------------------------------------------------------------------
# REGRESSION: hole 3 -- set -u vs. an empty array.
# --------------------------------------------------------------------------

def test_empty_arrays_are_initialised_assigned():
    """RED at origin/main. `declare -a foo` is unset under `set -u`.

    Pinned as source structure rather than behaviour because reproducing it
    needs a full zero-skip run of the real suite. The mechanism is exact and
    measured (bash 5.3.15): `declare -a A; echo ${#A[@]}` under `set -u` aborts
    with 'A: unbound variable', while `A=()` does not.
    """
    src = RUN_TESTS.read_text()
    offenders = re.findall(r"^\s*declare -a (\w+)\s*$", src, re.M)
    assert not offenders, (
        f"{offenders} are declared with bare `declare -a`, which leaves them "
        "UNSET under `set -u`; the first ${#arr[@]} on a still-empty array "
        "aborts with 'unbound variable'. Use `NAME=()` instead."
    )
    for name in ("RESULTS", "SKIP_LINES"):
        assert re.search(rf"^{name}=\(\)\s*$", src, re.M), (
            f"{name} is not initialised with an explicit `{name}=()`"
        )


def test_set_u_empty_array_mechanism_holds():
    """POSITIVE CONTROL for the test above.

    The source-structure pin is only meaningful if the bash behaviour it
    describes is real on the bash actually running the gate. Assert BOTH halves
    -- `declare -a` fails, `=()` succeeds -- so this cannot pass on a bash where
    neither form errors and the pin above would be guarding nothing.
    """
    bad = subprocess.run(
        ["bash", "-c", 'set -uo pipefail; declare -a A; echo "${#A[@]}"'],
        capture_output=True, text=True,
    )
    good = subprocess.run(
        ["bash", "-c", 'set -uo pipefail; A=(); echo "${#A[@]}"'],
        capture_output=True, text=True,
    )
    assert bad.returncode != 0 and "unbound variable" in bad.stderr, (
        "`declare -a A` + `${#A[@]}` did NOT error under set -u on this bash; "
        f"the pin above may be guarding a non-issue here.\n{bad.stderr}"
    )
    assert good.returncode == 0 and good.stdout.strip() == "0", (
        f"`A=()` did not behave as the fix requires.\n{good.stderr}"
    )


# --------------------------------------------------------------------------
# REACHABILITY for pre-existing guards (NOT regression coverage for this PR).
# --------------------------------------------------------------------------

def test_an_empty_target_directory_is_loud(tmp_path):
    """A target that EXISTS but holds no tests must fail, not pass.

    GUARD 5 accepts it (the directory is real), so the only thing standing
    between an emptied suite and a green gate is the per-directory
    ``collected < 1`` floor. Nothing had ever proven that path could fire.
    """
    empty = tmp_path / "empty_tests"
    empty.mkdir()
    runner = _runner_with_targets(tmp_path, [str(empty)])
    proc = _run([str(runner), str(REPO_ROOT)])
    out = proc.stdout + proc.stderr
    assert proc.returncode != 0, (
        f"an EMPTY target directory produced a passing run.\n{out}"
    )
    assert "collected 0 tests" in out, (
        f"the run failed but not for the empty-directory reason.\n{out}"
    )
    assert "RESULT: FAIL" in proc.stdout, out


def test_a_typod_target_entry_is_loud(tmp_path):
    """A misspelled target must be named, not silently dropped."""
    typo = "scripts/dl-rooter/tests"
    runner = _runner_with_targets(tmp_path, [typo])
    proc = _run([str(runner), "--check-targets", str(REPO_ROOT)])
    out = proc.stdout + proc.stderr
    assert proc.returncode == 2, f"a typo'd target did not abort GUARD 5.\n{out}"
    assert typo in out, f"GUARD 5 failed but never named {typo!r}.\n{out}"
    assert "does not exist" in out, f"GUARD 5 named {typo!r} but not why.\n{out}"


def test_a_failing_run_never_exits_zero(tmp_path):
    """`RESULT: FAIL` and a zero exit must be impossible.

    The report that motivated this PR said a bare worktree run printed
    ``RESULT: FAIL`` and exited 0. That did NOT reproduce (measured: exit 1),
    and the structure forbids it -- ``RESULT: FAIL`` is printed only when
    ``fail != 0`` and the very next statement is ``exit "$fail"``. The most
    likely origin is reading the status through a pipeline (``| tail``), which
    yields the LAST command's status, not the runner's.

    This pins the invariant end-to-end anyway, against a run forced red by the
    empty-directory case, so the claim can never quietly become true.
    """
    empty = tmp_path / "empty_tests"
    empty.mkdir()
    runner = _runner_with_targets(tmp_path, [str(empty)])
    proc = _run([str(runner), str(REPO_ROOT)])
    if "RESULT: FAIL" in proc.stdout:
        assert proc.returncode != 0, (
            "the runner printed RESULT: FAIL and exited 0 — a reassuring exit "
            f"code over a failed run.\nexit={proc.returncode}\n{proc.stdout}"
        )
    else:
        pytest.fail(
            "could not force a RESULT: FAIL, so this invariant was never "
            f"exercised — the test would pass vacuously.\n{proc.stdout}"
        )
