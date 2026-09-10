"""Shared fixtures for the change-scoped-run suites.

WHY THIS EXISTS
---------------
The scoped-run tests started as ONE 53-test file. `scripts/run-tests.sh` runs
pytest with ``--dist loadfile``, which pins every test in a file to ONE xdist
worker — so that file's whole duration was a SERIAL critical path inside
``scripts/tests``, the biggest target, on the tier that is the bottleneck.
MEASURED at load 58: 187.6s wall / 47.5s CPU, ~148s of it in the 22 tests that
drive a full patched runner.

Shipping that inside a PR whose entire thesis is "the suite is too slow" would
have been self-refuting, so the tests are split across four files by concern.
`--dist loadfile` can then spread them over four workers, and the longest single
file is roughly a third of the original.

The helpers have to live somewhere all four can import: duplicating them would
regenerate the same bug in four places (`claude/RULES.md` -> "one rule, one
place"), and `_scope_line` in particular encodes a rule — LAST match, matching
`gate.sh`'s ``tail -1`` — that is exactly the kind of thing that drifts when
copied.
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
RUN_TESTS = REPO_ROOT / "scripts" / "run-tests.sh"
NODE_TESTS = REPO_ROOT / "scripts" / "run-node-tests.sh"
GATE = REPO_ROOT / "scripts" / "gate.sh"
SCOPED = REPO_ROOT / "scripts" / "scoped-tests.sh"

# The scope vocabulary, owned by run-tests.sh's GUARD 11 and pinned two-way
# against both shell scripts by test_scoped_gate_contract.py.
SCOPE_STATES = ("FULL", "NONE", "PARTIAL", "SCOPED", "UNKNOWN")

# One real entry from each of the runner's two non-pytest registries. The pair
# of family tests needs them to EXIST (an empty family makes "it was skipped"
# vacuous) but not to be exhaustive — the whole SHELL_TESTS set is a measured
# 101s under load, and these suites are not the place to spend that.
CHEAP_HOOK_TEST = "scripts/claude-hooks/tests/test_claude_notify.py"
CHEAP_SHELL_TEST = "scripts/tests/test_release_wrapper.sh"


def run(args: list[str], timeout: int = 600, env: dict | None = None,
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


def out_of(proc: subprocess.CompletedProcess) -> str:
    return proc.stdout + proc.stderr


def scope_line(out: str) -> str | None:
    """The LAST scope line, matching `gate.sh`'s `| tail -1`.

    🔴 `re.search` returns the FIRST match, and that is the rule this whole
    marker's safety rests on getting right: the runner emits its scope from the
    EXIT trap, i.e. last, and anything else printing at column 0 earlier is a
    forgery a first-match reader would take instead. `gate.sh` uses `tail -1`
    and one test drives that path — but this helper is reused by ~12 tests and
    was one reuse away from restating the bug it was written under.
    """
    # 🔴 The trailing space is OPTIONAL, because `gate.sh`'s own reader does not
    # require one. This helper exists to mirror that reader's LAST-match rule,
    # and requiring a detail made it STRICTER than the thing it mirrors: a
    # runner printing a bare `SCOPE: FULL` would be honoured by the gate and be
    # invisible here. The docstring says this helper exists because the rule
    # "is exactly the kind of thing that drifts when copied" — the anchoring
    # drifted instead.
    ms = re.findall(r"^SCOPE: (\w+)\b", out, re.M)
    return ms[-1] if ms else None


# ---------------------------------------------------------------------------
# Fixtures. Shared because more than one of the four suites builds each of
# these states, and a duplicated fixture is a duplicated assumption.
# ---------------------------------------------------------------------------
from testlib.mockbin import write_exec  # noqa: E402
from testlib.runner_patch import (  # noqa: E402
    patch_runner_source,
    write_pytest_suite,
)

def two_target_fixture(tmp_path: Path):
    """Two throwaway targets of DISTINCT sizes, no hook/shell families.

    🔴 THESE TESTS USED TO RIDE ON `--check-targets`, WHICH IS NOW WRONG. That
    flag runs no tests, so it reports `SCOPE: NONE` regardless of narrowing —
    and a scope state is a claim about COVERAGE, which a zero-test invocation
    cannot make. Asserting FULL/PARTIAL/SCOPED therefore needs a run that
    actually collects something. A patched two-target copy costs a few seconds;
    the real target list would cost the SHELL_TESTS' measured 101s."""
    a = tmp_path / "alpha"
    b = tmp_path / "beta"
    write_pytest_suite(a, 3, prefix="test_a1")
    write_pytest_suite(b, 7, prefix="test_b1")
    runner = tmp_path / "run-tests.sh"
    runner.write_text(patch_runner_source(
        RUN_TESTS.read_text(), targets=[str(a), str(b)],
        floors={str(a): 1, str(b): 1}, ack=[], hook_tests=[], shell_tests=[]))
    return a, b, runner


TIER_HEAD = """
echo "=== pytest scripts/fake ==="
echo "======================== SUMMARY (hermetic set) ========================"
echo "  PASS  scripts/fake  (collected=1234 passed=1234 skipped=0 floor=1200)"
echo "  ----"
echo "  TOTAL collected=1234  passed=1234  skipped=0  failed=0"
"""


def tier_runner(path: Path, scope: str | None, verdict: str = "PASS",
                 code: int = 0) -> Path:
    """A stand-in runner that prints a chosen scope and verdict.

    Written through `testlib.mockbin.write_exec`, which owns the shebang: the
    nix sandbox has no `/usr/bin/env`, so a hand-written one is dead in the tier
    the merge is actually gated on.
    """
    body = TIER_HEAD
    if scope is not None:
        body += f'echo "SCOPE: {scope} (fixture)"\n'
    body += f'echo "RESULT: {verdict} (exit={code})"\nexit {code}\n'
    return write_exec(path, body)


def gate(tmp_path: Path, runner: Path, extra_env: dict | None = None):
    # DEVRC_GATE_ALLOW_AMBIENT: gate.sh refuses any gate-weakening variable in
    # its environment, and DEVRC_GATE_PYTEST_RUNNER is one of them. This suite
    # drives that seam on purpose; see gate.sh's pre-flight block.
    env = {"DEVRC_GATE_PYTEST_RUNNER": str(runner),
           "DEVRC_GATE_ALLOW_AMBIENT": "1"}
    if extra_env:
        env.update(extra_env)
    return run([str(GATE), "--tier", "pytest", "--log-dir", str(tmp_path / "logs"),
                 str(REPO_ROOT)], env=env)


def scoped_fixture(tmp_path: Path, floor: int = 400):
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


def ledger_fixture(tmp_path: Path, *, ack=None, expected_skips=None, skip_file=False):
    """A throwaway target of two DISTINCT-sized modules, with real ledgers.

    Sizes 3 and 7: a fixture whose modules collect the same count cannot tell
    "ran the file I named" from "ran either file"."""
    d = tmp_path / "target"
    write_pytest_suite(d, 3, prefix="test_quiet")
    if skip_file:
        # A module holding exactly one skip whose reason the pin below matches.
        (d / "test_skipper.py").write_text(
            "import pytest\n\n\n"
            '@pytest.mark.skip(reason="synthetic pinned reason for the ledger fixture")\n'
            "def test_skipped_on_purpose():\n    assert True\n"
        )
    else:
        write_pytest_suite(d, 7, prefix="test_other")
    runner = tmp_path / "run-tests.sh"
    kw = {}
    if ack is not None:
        kw["ack"] = ack
    if expected_skips is not None:
        kw["expected_skips"] = expected_skips
    runner.write_text(patch_runner_source(
        RUN_TESTS.read_text(), targets=[str(d)], floors={str(d): 1},
        hook_tests=[], shell_tests=[], **kw))
    return d, runner


def throwaway_repo(tmp_path: Path) -> Path:
    """A throwaway git repo. `-c` for identity so the operator's real git config
    is neither read for a committer nor written to (GUARD 10's subject)."""
    r = tmp_path / "repo"
    (r / "scripts" / "tests").mkdir(parents=True)
    subprocess.run(["git", "-C", str(r), "init", "-q", "-b", "main"], check=True)
    subprocess.run(["git", "-C", str(r), "config", "user.email", "t@example.invalid"],
                   check=True)
    subprocess.run(["git", "-C", str(r), "config", "user.name", "T"], check=True)
    return r


def commit_all(r: Path, msg: str = "c") -> None:
    subprocess.run(["git", "-C", str(r), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(r), "commit", "-q", "-m", msg], check=True)


def stub_runner(tmp_path: Path, targets: list[str]) -> Path:
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

