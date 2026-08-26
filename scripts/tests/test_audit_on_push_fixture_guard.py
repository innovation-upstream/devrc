"""githooks/audit-on-push.sh must never spend a headless `claude` call grading a
pytest fixture repo, or a synthetic test ref pushed at a throwaway remote.

🔴 WHAT THIS IS FOR — measured, not hypothesised. Over the 14 days to
2026-08-25 the global pre-push hook fired FIVE audit runs. THREE of them were
launched from inside cwds shaped like

    /tmp/.../pytest-of-zach/pytest-0/test_the_far_side_fixture_woul0/…

— a devrc test fixture built a throwaway repo, pushed it at a throwaway bare
remote, and the worker graded the FIXTURE's diff for real. One of the runs
graded the branch `test/prepush-pc-r3`, which has never existed upstream
(`git ls-remote --heads origin 'test/prepush-pc*'` is empty). The four
non-productive runs cost 167,977 output tokens, summed from their telemetry
`output_tokens`.

🔴 THE HARNESS IS THE THING TO DISTRUST HERE, because the reassuring observable
— "no audit ran" — is exactly what a harness wired to nothing produces. So:

  * POSITIVE CONTROL — `test_the_harness_can_observe_an_audit_actually_running`
    drives the SAME plumbing on a case with no guard against it and asserts the
    audit DID run (the `claude` stub was invoked AND `running audit` was
    logged). If it ever goes quiet, every skip test here is measuring nothing.
  * NEGATIVE CONTROL / RED-AT-BASE — `test_deleting_the_guard_makes_every_skip_
    case_audit_again` deletes exactly the lines between the `GUARD:…` sentinels
    in the shipped script and re-runs the four skip cases against the result.
    All four must audit. That is the guard's own mutation score, re-taken every
    run rather than asserted once in prose.

🔴 NO `git`-METADATA READS OF THIS REPO. `nix flake check` builds
`checks.pytests` from a tracked-file COPY with no `.git`, so `git show
origin/main:…` exits 128 there while the pre-push tier (which has `.git`) stays
green — the two-tier blind spot documented in `test_conditional_skip_pins.py`.
An earlier draft of this file took its baseline from `origin/main` and would
have SKIPPED in the hermetic tier, which is also an unpinned skip against
`EXPECTED_SKIPS`. The sentinel-strip baseline runs identically in both tiers and
adds ZERO skips. (Fixture repos built under `tmp_path` are a different thing and
are fine — `pkgs.git` is in the check's `nativeBuildInputs` for exactly that.)

🔴 NO REAL `claude` CALL CAN ESCAPE THIS FILE. Every run gets a PATH whose first
entry holds a recording `claude` stub, and an env built from scratch rather than
copied from `os.environ` — so `AUDIT_CONF_FILE`/`CLAWGATE_CONF_FILE` point at
non-existent paths (the operator's `~/.claude/audit-on-push.env` could otherwise
set `AUDIT_ON_PUSH=on` and make a live POST) and no ambient `PYTEST_*` or
`TMPDIR` value leaks in to move a guard arm behind the test's back.

MEASURED RED-AT-BASE, by hand, against the real pre-change file
(`git show origin/main:githooks/audit-on-push.sh` swapped in on disk):
all SIX skip tests FAILED; the two negative-case tests PASSED. Those two are
INVARIANT GUARDS — green at base by construction, since the pre-change worker
audits everything — and are labelled as such below rather than counted as
regression coverage.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
AUDIT = ROOT / "githooks" / "audit-on-push.sh"

ZERO_SHA = "0" * 40
REAL_SHA = "1a2b3c4d5e6f70819283a4b5c6d7e8f901234567"
REAL_URL = "git@github.com:ZacxDev/devrc.git"

# The sentinel pairs the shipped script carries so this file can delete exactly
# the guard and nothing around it.
GUARDS = ("GUARD:fixture-tree", "GUARD:synthetic-ref")

# A file big enough to clear AUDIT_MIN_LINES (40) so gate 2 never masks a
# gate-0.5/1.5 result: if the guard under test does not fire, the run reaches
# the audit, which is what the positive control reads.
BIG_BLOB = "".join(f"line {i}\n" for i in range(60))


# --------------------------------------------------------------------------- #
# harness
# --------------------------------------------------------------------------- #
def _git_env(home: Path) -> dict[str, str]:
    """A git environment that cannot reach the operator's config or remotes."""
    return {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(home),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": str(home / "gitconfig"),
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid",
    }


def _git(repo: Path, home: Path, *args: str) -> str:
    p = subprocess.run(["git", "-C", str(repo), *args],
                       capture_output=True, text=True, env=_git_env(home))
    assert p.returncode == 0, f"git {args}: {p.stderr}"
    return p.stdout


def _make_repo(where: Path, home: Path, branch: str) -> Path:
    """A real repo with a `main` base commit and `branch` carrying a 60-line diff."""
    where.mkdir(parents=True, exist_ok=True)
    home.mkdir(parents=True, exist_ok=True)
    _git(where, home, "init", "-q", "-b", "main")
    (where / "base.txt").write_text("base\n", encoding="utf-8")
    _git(where, home, "add", "base.txt")
    _git(where, home, "commit", "-q", "-m", "base")
    _git(where, home, "checkout", "-q", "-b", branch)
    (where / "feature.txt").write_text(BIG_BLOB, encoding="utf-8")
    _git(where, home, "add", "feature.txt")
    _git(where, home, "commit", "-q", "-m", "feature")
    return where


def _stub_claude(bindir: Path) -> Path:
    """A recording `claude` that answers CLEAN. Its LOG is the positive control."""
    bindir.mkdir(parents=True, exist_ok=True)
    calls = bindir / "claude-calls.log"
    stub = bindir / "claude"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        f'printf "%s\\n" "$*" >> {calls}\n'
        'printf "VERDICT:SAFE\\nCLEAN\\n"\n'
        "exit 0\n",
        encoding="utf-8",
    )
    stub.chmod(0o755)
    return calls


class Run:
    def __init__(self, proc, log_text: str, claude_calls: list[str]):
        self.proc = proc
        self.log = log_text
        self.claude_calls = claude_calls

    @property
    def audited(self) -> bool:
        """Did the run actually reach the headless audit? Two independent reads,
        one from the script's own log and one from the stub it would have to
        execute — a single read of either could be satisfied by the other
        failing silently."""
        return bool(self.claude_calls) and "running audit" in self.log


def _run(script: Path, work: Path, *, repo_root: str, branch: str,
         url: str = REAL_URL, remote_sha: str = REAL_SHA,
         env_extra: dict[str, str] | None = None) -> Run:
    work.mkdir(parents=True, exist_ok=True)
    home = work / "home"
    home.mkdir(exist_ok=True)
    bindir = work / "bin"
    calls = _stub_claude(bindir)
    log = work / "audit.log"

    env = _git_env(home)
    env["PATH"] = f"{bindir}{os.pathsep}{env['PATH']}"
    env["AUDIT_ON_PUSH"] = "shadow"
    env["AUDIT_LOG_FILE"] = str(log)
    # Point the two config sources at paths that do not exist, so the operator's
    # own ~/.claude/*.env can never flip this run to `on` and POST for real.
    env["AUDIT_CONF_FILE"] = str(work / "no-such-audit.env")
    env["CLAWGATE_CONF_FILE"] = str(work / "no-such-clawgate.env")
    # 🔴 TMPDIR is deliberately ABSENT unless a test sets it: it selects one of
    # gate 0.5's arms, and inheriting the ambient value would make WHICH arm
    # fires depend on the tier (the nix sandbox relocates TMPDIR).
    if env_extra:
        env.update(env_extra)

    stdin = f"refs/heads/{branch} {REAL_SHA} refs/heads/{branch} {remote_sha}\n"
    proc = subprocess.run(
        ["bash", str(script), "origin", url, repo_root],
        input=stdin, capture_output=True, text=True, env=env, timeout=120)
    log_text = log.read_text(encoding="utf-8") if log.exists() else ""
    call_lines = (calls.read_text(encoding="utf-8").splitlines()
                  if calls.exists() else [])
    return Run(proc, log_text, call_lines)


def _strip_guards(text: str) -> str:
    """Delete exactly the lines between each `>>> GUARD:x` / `<<< GUARD:x` pair.

    This is the guard-removed BASELINE. The helper functions the guards call sit
    OUTSIDE the sentinels on purpose: a mutant that deletes a guard together
    with its machinery dies with `command not found` — the wrong reason — and
    would score a kill this file has not earned.
    """
    out, dropped, depth = [], 0, 0
    for line in text.splitlines(keepends=True):
        s = line.strip()
        if depth == 0 and s.startswith("# >>> GUARD:"):
            depth = 1
            continue
        if depth == 1 and s.startswith("# <<< GUARD:"):
            depth = 0
            continue
        if depth:
            dropped += 1
            continue
        out.append(line)
    assert depth == 0, "unbalanced GUARD sentinels in audit-on-push.sh"
    assert dropped > 0, "no guard lines were removed — the strip is inert"
    return "".join(out)


@pytest.fixture
def guard_removed_script(tmp_path) -> Path:
    """The shipped worker with both guards deleted — the red-at-base baseline."""
    text = AUDIT.read_text(encoding="utf-8")
    for name in GUARDS:
        assert f"# >>> {name}" in text and f"# <<< {name}" in text, (
            f"{name} sentinels are missing from {AUDIT} — the baseline this "
            "file builds would silently be the SHIPPED script, and the "
            "red-at-base test would pass while proving nothing")
    stripped = _strip_guards(text)
    # 🔴 Match the CODE, not the words: the same phrases appear in the header
    # comment ("… is NOT a pytest temp fixture tree"), so a bare substring check
    # fails on a correctly-stripped file. These are the three `log` calls only
    # the guards make, and only a CODE line can spell them.
    for gone in ('log "repo_root=$REPO_ROOT is a pytest temp fixture tree',
                 'log "repo_root=$REPO_ROOT pushed from inside a running pytest',
                 'log "branch=$BRANCH remote=$URL is a throwaway/temp-tree remote',
                 'log "branch=$BRANCH is a synthetic local test ref'):
        assert gone not in stripped, f"the strip left the guard behind: {gone}"
    p = tmp_path / "audit-on-push.baseline.sh"
    p.write_text(stripped, encoding="utf-8")
    p.chmod(0o755)
    syn = subprocess.run(["bash", "-n", str(p)], capture_output=True, text=True)
    assert syn.returncode == 0, f"stripped baseline is not valid bash: {syn.stderr}"
    return p


@pytest.fixture
def outside_tmp():
    """A directory whose path does NOT look like a pytest tree.

    `tmp_path` is rooted at `<basetemp>/pytest-of-<user>/…`, which gate 0.5
    correctly rejects — so the NEGATIVE case cannot be built there without
    measuring the guard instead of the absence of it.
    """
    d = Path(tempfile.mkdtemp(prefix="devrc-auditguard-real-"))
    assert "pytest-of" not in str(d) and not str(d).startswith("/tmp/pytest-"), d
    try:
        yield d
    finally:
        shutil.rmtree(d, ignore_errors=True)


# --------------------------------------------------------------------------- #
# 0. harness self-validation — read this before believing anything below
# --------------------------------------------------------------------------- #
def test_the_worker_exists_and_is_executable():
    assert AUDIT.is_file(), f"{AUDIT} missing — every test below is vacuous"
    assert os.access(AUDIT, os.X_OK), f"{AUDIT} is not executable"


def test_the_harness_can_observe_an_audit_actually_running(tmp_path, outside_tmp):
    """POSITIVE CONTROL for the whole file: a case that MUST reach the audit.

    Without this, every `not run.audited` assertion below is indistinguishable
    from a harness that never invokes the script at all.
    """
    repo = _make_repo(outside_tmp / "repo", tmp_path / "home", "fix/real-thing")
    run = _run(AUDIT, tmp_path, repo_root=str(repo), branch="fix/real-thing")
    assert run.audited, (
        "the harness never reached the audit even on a case with no guard "
        "against it — the skip assertions in this file are measuring nothing.\n"
        f"log:\n{run.log}\ncalls: {run.claude_calls}")


# --------------------------------------------------------------------------- #
# 1. GUARD CONDITION 1 — the repository root is a pytest temp fixture tree
# --------------------------------------------------------------------------- #
def test_skips_when_the_repo_root_is_under_a_pytest_of_tree(tmp_path):
    """The measured shape: <basetemp>/pytest-of-<user>/pytest-N/<testname>/…

    The `pytest-of-` component is built explicitly rather than inherited from
    `tmp_path`, so the test still pins the pattern under `--basetemp=<dir>`,
    where `tmp_path` carries no such component at all.
    """
    root = tmp_path / "pytest-of-zach" / "pytest-0" / "test_the_far_side0"
    repo = _make_repo(root / "repo", tmp_path / "home", "fix/real-thing")
    run = _run(AUDIT, tmp_path, repo_root=str(repo), branch="fix/real-thing")
    assert not run.audited, f"audited a pytest fixture repo:\n{run.log}"
    assert "pytest temp fixture tree; skip" in run.log, run.log


def test_skips_when_the_repo_root_is_under_slash_tmp_slash_pytest(tmp_path):
    """The literal `/tmp/pytest-*` arm — pytest's default basetemp root.

    No directory is created for it, and that is not a shortcut: gate 0.5 runs
    BEFORE the worker `cd`s to REPO_ROOT, so a guard that has moved below the
    `cd` fails this test instead of silently passing on the `cd`'s own exit.
    """
    run = _run(AUDIT, tmp_path, repo_root="/tmp/pytest-4242/test_x0/repo",
               branch="fix/real-thing")
    assert not run.audited, f"audited a /tmp/pytest-* repo:\n{run.log}"
    assert "pytest temp fixture tree; skip" in run.log, run.log


def test_skips_when_the_repo_root_is_under_a_relocated_TMPDIR(tmp_path, outside_tmp):
    """The `$TMPDIR/pytest-*` arm — pytest roots its basetemp at $TMPDIR when
    that is set, and the literal `/tmp` arm above cannot see it. The repo lives
    at a path NO other arm matches, so this measures the TMPDIR arm alone."""
    faketmp = outside_tmp / "relocated-tmp"
    repo = _make_repo(faketmp / "pytest-3" / "repo", tmp_path / "home",
                      "fix/real-thing")
    run = _run(AUDIT, tmp_path, repo_root=str(repo), branch="fix/real-thing",
               env_extra={"TMPDIR": str(faketmp)})
    assert not run.audited, f"audited a $TMPDIR/pytest-* repo:\n{run.log}"
    assert "pytest temp fixture tree; skip" in run.log, run.log


@pytest.mark.parametrize("var", ["PYTEST_CURRENT_TEST", "PYTEST_VERSION"])
def test_skips_when_pushed_from_inside_a_running_pytest(tmp_path, outside_tmp, var):
    """`--basetemp=<dir>` defeats every path pattern, so the env is the half
    that actually closes the class. The repo here is at a path NO path arm
    matches — proving the ENV arm, not the path arm."""
    repo = _make_repo(outside_tmp / "repo", tmp_path / "home", "fix/real-thing")
    val = ("scripts/tests/test_x.py::test_y (call)"
           if var == "PYTEST_CURRENT_TEST" else "9.1.1")
    run = _run(AUDIT, tmp_path, repo_root=str(repo), branch="fix/real-thing",
               env_extra={var: val})
    assert not run.audited, f"audited a push made from inside pytest:\n{run.log}"
    assert "running pytest" in run.log, run.log


# --------------------------------------------------------------------------- #
# 2. GUARD CONDITION 2 — synthetic test ref / throwaway fixture remote
# --------------------------------------------------------------------------- #
def test_skips_when_the_push_destination_is_a_temp_tree_remote(tmp_path, outside_tmp):
    """(a), the STRUCTURAL half: a fixture's throwaway bare remote. The REPO is
    at a perfectly ordinary path, so only the DESTINATION can be what fires."""
    repo = _make_repo(outside_tmp / "repo", tmp_path / "home", "fix/real-thing")
    run = _run(AUDIT, tmp_path, repo_root=str(repo), branch="fix/real-thing",
               url="/tmp/pytest-of-zach/pytest-0/test_push0/remote.git")
    assert not run.audited, f"audited a push at a temp-tree remote:\n{run.log}"
    assert "throwaway/temp-tree remote; skip" in run.log, run.log


def test_skips_the_synthetic_test_ref_that_was_actually_graded(tmp_path, outside_tmp):
    """(b): the exact branch from the incident — `test/prepush-pc-r3`, no
    upstream, all-zero remote sha (git's own "the remote does not have this
    ref"). Gate 1's allowlist waves it through on its trailing `*/*` arm, and
    both the repo path and the destination URL here are ordinary."""
    repo = _make_repo(outside_tmp / "synth", tmp_path / "home",
                      "test/prepush-pc-r3")
    run = _run(AUDIT, tmp_path, repo_root=str(repo), branch="test/prepush-pc-r3",
               remote_sha=ZERO_SHA)
    assert not run.audited, f"audited a synthetic test ref:\n{run.log}"
    assert "synthetic local test ref" in run.log, run.log


# --------------------------------------------------------------------------- #
# 3. THE NEGATIVE CASE — a real repo on a real branch still gets audited
#    🔴 INVARIANT GUARDS, not regression coverage: both are green at base by
#    construction (the pre-change script audits everything). They exist to fail
#    the DAY the guard is widened past its evidence.
# --------------------------------------------------------------------------- #
def test_a_real_repo_on_a_real_branch_is_still_audited(tmp_path, outside_tmp):
    repo = _make_repo(outside_tmp / "repo", tmp_path / "home", "fix/real-thing")
    run = _run(AUDIT, tmp_path, repo_root=str(repo), branch="fix/real-thing")
    assert run.audited, (
        "the guard swallowed a REAL feature-branch push — this is the feature, "
        f"not the noise.\nlog:\n{run.log}")
    assert "running audit" in run.log, run.log


def test_the_first_push_of_a_real_feature_branch_is_still_audited(tmp_path, outside_tmp):
    """🔴 THE FAIL-DIRECTION CONTROL, and the reason gate 1.5 is not keyed on
    "no upstream" alone. A first `git push -u origin fix/…` ALSO has no upstream
    and ALSO names a ref the remote lacks (all-zero remote sha) — and it is the
    single most valuable push to audit. A guard keyed on absence alone would
    read as "quieter" while deleting the feature outright."""
    repo = _make_repo(outside_tmp / "repo", tmp_path / "home", "fix/brand-new")
    run = _run(AUDIT, tmp_path, repo_root=str(repo), branch="fix/brand-new",
               remote_sha=ZERO_SHA)
    assert run.audited, (
        "a first push of a real feature branch was skipped — gate 1.5 has been "
        f"widened past its evidence.\nlog:\n{run.log}")


# --------------------------------------------------------------------------- #
# 4. RED AT BASE — the matrix, re-measured every run rather than written down
# --------------------------------------------------------------------------- #
def _skip_cases(tmp_path, outside_tmp) -> dict[str, dict]:
    real = _make_repo(outside_tmp / "repo", tmp_path / "home", "fix/real-thing")
    synth = _make_repo(outside_tmp / "synth", tmp_path / "home",
                       "test/prepush-pc-r3")
    faketmp = outside_tmp / "relocated-tmp"
    reloc = _make_repo(faketmp / "pytest-3" / "repo", tmp_path / "home",
                       "fix/real-thing")
    fixture = _make_repo(tmp_path / "pytest-of-zach" / "pytest-0" / "t0" / "repo",
                         tmp_path / "home", "fix/real-thing")
    return {
        "pytest-of path": dict(repo_root=str(fixture), branch="fix/real-thing"),
        "relocated TMPDIR": dict(repo_root=str(reloc), branch="fix/real-thing",
                                 env_extra={"TMPDIR": str(faketmp)}),
        "inside a running pytest": dict(
            repo_root=str(real), branch="fix/real-thing",
            env_extra={"PYTEST_CURRENT_TEST": "a.py::b (call)"}),
        "temp-tree remote": dict(
            repo_root=str(real), branch="fix/real-thing",
            url="/tmp/pytest-of-zach/pytest-0/remote.git"),
        "synthetic test ref": dict(
            repo_root=str(synth), branch="test/prepush-pc-r3",
            remote_sha=ZERO_SHA),
    }


def test_deleting_the_guard_makes_every_skip_case_audit_again(
        guard_removed_script, tmp_path, outside_tmp):
    """🔴 A test never watched to FAIL proves nothing. This deletes exactly the
    guard (the lines between the `GUARD:…` sentinels) and asserts the resulting
    worker audits ALL FIVE cases the guard skips — then asserts the SHIPPED
    worker skips all five. Both halves are required: "the mutant audits them"
    alone would still hold if the shipped script audited them too."""
    cases = _skip_cases(tmp_path, outside_tmp)
    audited_without_guard, audited_with_guard = [], []
    # 🔴 Work-dir names are INDEXED, not `hash(name)`: PYTHONHASHSEED makes str
    # hashing nondeterministic per process, and a nondeterministic on-disk name
    # is the exact defect PR #855 closed elsewhere in this tree.
    for i, (name, kw) in enumerate(cases.items()):
        if _run(guard_removed_script, tmp_path / f"nog-{i}", **kw).audited:
            audited_without_guard.append(name)
        if _run(AUDIT, tmp_path / f"head-{i}", **kw).audited:
            audited_with_guard.append(name)

    assert audited_without_guard == list(cases), (
        "with the guard DELETED the worker did not audit every case — so those "
        "cases are not regression coverage, they are invariant guards.\n"
        f"audited without the guard: {audited_without_guard}")
    assert audited_with_guard == [], (
        f"the SHIPPED worker still audits: {audited_with_guard}")


def test_the_guard_removal_does_not_also_break_the_untouched_paths(
        guard_removed_script, tmp_path, outside_tmp):
    """The strip's own control: the baseline must still be a WORKING worker.

    A mutant that is merely broken would make the test above pass for the wrong
    reason (nothing audits because nothing runs). So the guard-removed copy is
    also driven on the negative case, where it must behave exactly like the
    shipped one — audit it.
    """
    repo = _make_repo(outside_tmp / "repo", tmp_path / "home", "fix/real-thing")
    run = _run(guard_removed_script, tmp_path / "ctl",
               repo_root=str(repo), branch="fix/real-thing")
    assert run.audited, (
        "the guard-removed baseline cannot audit anything at all — it is "
        f"broken, not merely unguarded.\nlog:\n{run.log}\nstderr:{run.proc.stderr}")
