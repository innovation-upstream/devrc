"""`scoped-tests.sh` — the diff→files mapper never calls 'nothing to do' a pass.

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


def test_scoped_tests_refuses_when_the_mapping_selects_nothing(tmp_path):
    """🔴 THE CENTRAL REFUSAL. A wrapper that reports "nothing to do" and exits
    0 is precisely how a false green gets believed here."""
    r = throwaway_repo(tmp_path)
    (r / "scripts" / "tests" / "test_a.py").write_text("def test_a():\n    assert True\n")
    commit_all(r)
    # A change no test names.
    (r / "unrelated.conf").write_text("nothing references this\n")
    rec = stub_runner(tmp_path, [str(r / "scripts" / "tests")])
    proc = run([str(SCOPED), "--base", "HEAD", str(r)],
                env={"DEVRC_SCOPED_RUNNER": str(tmp_path / "stub-runner.sh")},
                cwd=r)
    out = out_of(proc)
    assert proc.returncode == 4, f"rc={proc.returncode}\n{out}"
    assert "to ZERO test files" in out, out
    assert "This is NOT a pass" in out, out
    assert not rec.exists(), "the runner was invoked despite an empty selection"


def test_scoped_tests_refuses_an_empty_diff(tmp_path):
    """"Nothing changed" is not "everything passed"."""
    r = throwaway_repo(tmp_path)
    (r / "scripts" / "tests" / "test_a.py").write_text("def test_a():\n    assert True\n")
    commit_all(r)
    stub_runner(tmp_path, [str(r / "scripts" / "tests")])
    proc = run([str(SCOPED), "--base", "HEAD", str(r)],
                env={"DEVRC_SCOPED_RUNNER": str(tmp_path / "stub-runner.sh")},
                cwd=r)
    out = out_of(proc)
    assert proc.returncode == 4, f"rc={proc.returncode}\n{out}"
    assert "the diff is EMPTY" in out, out


def test_scoped_tests_selects_a_changed_test_file_itself(tmp_path):
    r = throwaway_repo(tmp_path)
    (r / "scripts" / "tests" / "test_a.py").write_text("def test_a():\n    assert True\n")
    commit_all(r)
    (r / "scripts" / "tests" / "test_a.py").write_text(
        "def test_a():\n    assert True\n\n\ndef test_b():\n    assert True\n")
    rec = stub_runner(tmp_path, [str(r / "scripts" / "tests")])
    proc = run([str(SCOPED), "--base", "HEAD", str(r)],
                env={"DEVRC_SCOPED_RUNNER": str(tmp_path / "stub-runner.sh")},
                cwd=r)
    out = out_of(proc)
    assert proc.returncode == 0, f"rc={proc.returncode}\n{out}"
    assert rec.exists(), f"the runner was never invoked\n{out}"
    argv = rec.read_text()
    assert "--files" in argv, argv
    assert "scripts/tests/test_a.py" in argv, argv


def test_scoped_tests_selects_a_test_that_names_a_changed_non_test_file(tmp_path):
    """The rule that makes this worth anything: a change to a script maps to the
    tests that NAME it, which is how a one-file edit outside a test dir gets a
    cheap run instead of the 13k-test monolith."""
    r = throwaway_repo(tmp_path)
    # 🔴 NO SHEBANG. This file is never EXECUTED — the mapper only needs a
    # changed path for a test to name — and
    # `test_no_test_writes_a_usr_bin_env_shebang_at_runtime` is a structural scan
    # that does not care whether you meant to run it. MEASURED: an earlier draft
    # wrote `#!/usr/bin/env bash` here and failed the SANDBOX tier (where
    # /usr/bin/env does not exist) while the dev host never noticed.
    (r / "scripts" / "widget-tool.sh").write_text("echo hi\n")
    (r / "scripts" / "tests" / "test_widget.py").write_text(
        'PATH_UNDER_TEST = "scripts/widget-tool.sh"\n\n\ndef test_w():\n    assert True\n')
    (r / "scripts" / "tests" / "test_other.py").write_text(
        "def test_o():\n    assert True\n")
    commit_all(r)
    (r / "scripts" / "widget-tool.sh").write_text("echo bye\n")
    rec = stub_runner(tmp_path, [str(r / "scripts" / "tests")])
    proc = run([str(SCOPED), "--base", "HEAD", str(r)],
                env={"DEVRC_SCOPED_RUNNER": str(tmp_path / "stub-runner.sh")},
                cwd=r)
    out = out_of(proc)
    assert proc.returncode == 0, f"rc={proc.returncode}\n{out}"
    argv = rec.read_text()
    assert "test_widget.py" in argv, argv
    # The discriminating half: the OTHER test must NOT be selected. Without it
    # this passes for a mapper that selects everything.
    assert "test_other.py" not in argv, (
        f"the mapper selected a test that does not name the changed file.\n{argv}")


def test_the_basename_fallback_is_scoped_to_the_changed_files_own_subsystem(tmp_path):
    """REGRESSION on the mapper's precision, not on its safety.

    The looser rule — "some test mentions a file of this name" — over-selects
    badly across a repo where many subsystems have a `server.py` or a
    `config.py`. MEASURED on this repo before the fix: `server.py` matched 27
    test files repo-wide and 3 inside `scripts/dl-router/tests`, the subsystem
    that actually owns it. A mapper that hands back 27 files gives a green whose
    size implies far more than it says.

    The fixture mirrors that shape: two subsystems, both with a `handler.py`
    named by their own tests."""
    r = throwaway_repo(tmp_path)
    for sub in ("alpha", "beta"):
        (r / "scripts" / sub / "tests").mkdir(parents=True)
        (r / "scripts" / sub / "handler.py").write_text("VALUE = 1\n")
        (r / "scripts" / sub / "tests" / f"test_{sub}.py").write_text(
            'MODULE = "handler.py"\n\n\ndef test_x():\n    assert True\n')
    commit_all(r)
    (r / "scripts" / "alpha" / "handler.py").write_text("VALUE = 2\n")
    rec = stub_runner(tmp_path, [str(r / "scripts" / "alpha" / "tests"),
                                  str(r / "scripts" / "beta" / "tests"),
                                  str(r / "scripts" / "tests")])
    proc = run([str(SCOPED), "--base", "HEAD", str(r)],
                env={"DEVRC_SCOPED_RUNNER": str(tmp_path / "stub-runner.sh")},
                cwd=r)
    out = out_of(proc)
    assert proc.returncode == 0, f"rc={proc.returncode}\n{out}"
    argv = rec.read_text()
    assert "alpha/tests/test_alpha.py" in argv, argv
    assert "beta/tests/test_beta.py" not in argv, (
        "the basename fallback reached into a sibling subsystem that merely "
        f"names a file of the same basename.\n{argv}")


def test_scoped_tests_reports_changed_files_it_could_not_map(tmp_path):
    """A heuristic that hides its misses reads as coverage. An unmapped file is
    printed whether or not anything else mapped."""
    r = throwaway_repo(tmp_path)
    (r / "scripts" / "tests" / "test_a.py").write_text("def test_a():\n    assert True\n")
    commit_all(r)
    (r / "scripts" / "tests" / "test_a.py").write_text(
        "def test_a():\n    assert True\n\n\ndef test_b():\n    assert True\n")
    (r / "orphan-artifact.conf").write_text("nothing names this\n")
    stub_runner(tmp_path, [str(r / "scripts" / "tests")])
    proc = run([str(SCOPED), "--base", "HEAD", str(r)],
                env={"DEVRC_SCOPED_RUNNER": str(tmp_path / "stub-runner.sh")},
                cwd=r)
    out = out_of(proc)
    assert proc.returncode == 0, f"rc={proc.returncode}\n{out}"
    assert re.search(r"^  🔴 UNMAPPED", out, re.M), out
    assert re.search(r"^       orphan-artifact\.conf$", out, re.M), out


def test_scoped_tests_dry_run_runs_nothing(tmp_path):
    r = throwaway_repo(tmp_path)
    (r / "scripts" / "tests" / "test_a.py").write_text("def test_a():\n    assert True\n")
    commit_all(r)
    (r / "scripts" / "tests" / "test_a.py").write_text(
        "def test_a():\n    assert True\n\n\ndef test_b():\n    assert True\n")
    rec = stub_runner(tmp_path, [str(r / "scripts" / "tests")])
    proc = run([str(SCOPED), "--base", "HEAD", "--dry-run", str(r)],
                env={"DEVRC_SCOPED_RUNNER": str(tmp_path / "stub-runner.sh")},
                cwd=r)
    out = out_of(proc)
    assert proc.returncode == 0, out
    assert not rec.exists(), "a dry run invoked the runner"
    assert "A dry run is not a verdict" in out, out


def test_scoped_tests_refuses_an_ambient_devrc_targets(tmp_path):
    """REGRESSION, and it exists because a MUTANT CAUGHT ITS ABSENCE: the
    refusal shipped in this round's first pass with no test at all, and
    reverting it left the whole suite green.

    The declared target list is read through `run-tests.sh --check-targets`,
    which HONOURS `DEVRC_TARGETS` — so an exported value makes this mapper's
    search universe a SUBSET, and a changed file whose covering tests live in an
    unselected target maps to nothing. The end state is safe (exit 4, not a
    pass), but the DIAGNOSIS is wrong: "no test names what you changed" when the
    truth is "this mapper was not allowed to look there". A wrong diagnosis on a
    refusal is how someone concludes their change is untested and moves on."""
    r = throwaway_repo(tmp_path)
    (r / "scripts" / "tests" / "test_a.py").write_text("def test_a():\n    assert True\n")
    commit_all(r)
    (r / "scripts" / "tests" / "test_a.py").write_text(
        "def test_a():\n    assert True\n\n\ndef test_b():\n    assert True\n")
    rec = stub_runner(tmp_path, [str(r / "scripts" / "tests")])
    proc = run([str(SCOPED), "--base", "HEAD", str(r)],
               env={"DEVRC_SCOPED_RUNNER": str(tmp_path / "stub-runner.sh"),
                    "DEVRC_TARGETS": "scripts/tests"},
               cwd=r)
    out = out_of(proc)
    assert proc.returncode == 2, f"rc={proc.returncode}\n{out}"
    assert "DEVRC_TARGETS is set" in out, out
    assert not rec.exists(), "the runner was invoked despite the refusal"


def test_scoped_tests_refuses_an_unreadable_target_list(tmp_path):
    """An empty target list would map every change to nothing, and that would
    then be reported as an empty SELECTION — a fault in the caller wearing the
    costume of a clean diff. Refuse instead, naming the command that failed."""
    r = throwaway_repo(tmp_path)
    (r / "scripts" / "tests" / "test_a.py").write_text("def test_a():\n    assert True\n")
    commit_all(r)
    (r / "scripts" / "tests" / "test_a.py").write_text(
        "def test_a():\n    assert True\n\n\ndef test_b():\n    assert True\n")
    broken = write_exec(tmp_path / "broken.sh", "exit 7\n")
    proc = run([str(SCOPED), "--base", "HEAD", str(r)],
                env={"DEVRC_SCOPED_RUNNER": str(broken)}, cwd=r)
    out = out_of(proc)
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

