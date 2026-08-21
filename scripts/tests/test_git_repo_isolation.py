"""GUARD 9 — the suite must not operate on the git repository it RUNS FROM.

🔴 WHAT HAPPENED, 2026-08-21, measured on the operator's real clone and on the
production GitHub remote. A gate run:

  * rewrote `refs/heads/main` with fixture commits (`seed`, `base`, `ahead`,
    `local side`, `un-pushed work stranded on main`, `autocommit: N change(s)
    in the some-scope analyze-service index`),
  * created the fixture branches `side`, `topic`, `trunk`, `master`,
    `only-branch`, `feat/behind-too`,
  * DELETED `refs/heads/main` and repointed `HEAD` at `trunk`,
  * wrote `core.bare=true`, `user.name=T`, `user.email=t@example.invalid`, a
    `core.hooksPath` under `pytest-0/test_install_does_not_depend_o0/` and a
    `remote.origin.url` under `pytest-0/test_fetch_failure_is_rc40/`,
  * and pushed fixture refs to the real remote.

🔴 AND NOT ONE FIXTURE WAS SLOPPY. Every git fixture in this repo passes
`-C <tmp_path>/…`; several also pin `HOME`, `GIT_CONFIG_GLOBAL` and
`GIT_CONFIG_SYSTEM`. `GIT_DIR` overrides `-C`, so one inherited environment
variable defeats all of them simultaneously — which is why the fix is an `unset`
and a plugin rather than a patch in fourteen test files. The tmpdir
paths written into the real config are the tell: the tests computed the RIGHT
value and git wrote it into the WRONG repository.

HOW THIS FILE IS BUILT, and why each layer is not redundant with the next:

  1. LEDGERS. The variable list is owned once in Python (`testlib/gitenv.py`)
     and re-spelled in each of the three shell runners — they need it because
     HOOK_TESTS, SHELL_TESTS and the node tier never load a pytest plugin, and
     because an inherited GIT_DIR breaks ROOT resolution before any Python runs.
     Pinned in BOTH directions for every spelling: a set that only grows on one
     side is a guard that protects twelve targets and not five.
  2. THE DETECTOR, as a unit. Does `snapshot` actually MOVE when a ref moves?
     Reported as a pair with a no-op control, never as a bare zero.
  3. 🔴 THE INCIDENT ITSELF, as a nested-pytest control pair. One run WITHOUT
     the plugin reproduces the damage (a fixture-shaped `git -C <tmp>` writing
     a branch into the ambient repo); one run WITH it does not. A guard nobody
     has watched fail proves nothing, and a fix nobody has watched CHANGE the
     outcome proves nothing either.
  4. REACHABILITY. The violation is asserted by `VIOLATION_TOKEN` — this
     guard's own marker — so a control cannot pass because a DIFFERENT check
     errored first (claude/RULES.md → "prove it REACHABLE, not just breakable").
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
RUN_TESTS = SCRIPTS / "run-tests.sh"
CONFTEST = SCRIPTS / "tests" / "conftest.py"

# Every entry point that resolves a ROOT with `git rev-parse --show-toplevel`
# and then runs tests. All three carry GUARD 9's `unset`, and all three must run
# it BEFORE that line — see test_every_runner_clears_BEFORE_it_resolves_its_root.
#
# 🔴 THREE SPELLINGS RATHER THAN ONE SOURCED FILE, and the reason is measured,
# not aesthetic: `testlib/runner_patch.py` writes a patched COPY of
# `run-tests.sh` into a tmp dir and about fifteen tests drive that copy. A copy
# cannot source a sibling `lib/` that was never copied with it — the first
# version of this guard did exactly that and turned those fifteen red with
# `run-tests: FATAL — cannot source lib/git-repo-pointers.sh`. So the SET is
# owned once, in `testlib/gitenv.py`, and every spelling is pinned to it here.
RUNNERS = (SCRIPTS / "run-tests.sh",
           SCRIPTS / "run-node-tests.sh",
           SCRIPTS / "gate.sh")

sys.path.insert(0, str(SCRIPTS))

from testlib.gitenv import (  # noqa: E402
    PROTECT_ENV,
    REPO_POINTER_VARS,
    VIOLATION_TOKEN,
    diff_snapshots,
    protected_git_dirs,
    resolve_git_dir,
    snapshot,
    strip_repo_pointers,
)

# 🔴 NOT a skipif. `git` is in run-tests.sh's REQUIRED_TOOLS and in the flake's
# pytests check, so its absence is an ERROR: a skipped isolation test reports a
# safety property it never measured, which is the exact failure mode that let
# the incident happen on a green suite.
if shutil.which("git") is None:  # pragma: no cover - the gate puts git on PATH
    raise RuntimeError(
        "git is not on PATH. Add it to REQUIRED_TOOLS in scripts/run-tests.sh "
        "and to the pytests check in flake.nix rather than skipping these tests."
    )

_GIT_ENV = {
    "GIT_CONFIG_SYSTEM": "/dev/null",
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_AUTHOR_NAME": "guard nine",
    "GIT_AUTHOR_EMAIL": "guard9@example.invalid",
    "GIT_COMMITTER_NAME": "guard nine",
    "GIT_COMMITTER_EMAIL": "guard9@example.invalid",
}


def _env(**extra) -> dict:
    e = dict(os.environ)
    e.update(_GIT_ENV)
    e.update({k: str(v) for k, v in extra.items()})
    return e


def _git(repo: Path, *args, env=None) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, env=env or _env(),
    )


def _mkrepo(path: Path, branch: str = "main") -> Path:
    """A real, committed repo at `path` — the stand-in for the operator's clone."""
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", branch, str(path)],
                   check=True, capture_output=True, env=_env())
    (path / "f.txt").write_text("base\n", encoding="utf-8")
    assert _git(path, "add", "f.txt").returncode == 0
    assert _git(path, "commit", "-qm", "base").returncode == 0
    return path


# --------------------------------------------------------------------------- #
# 0. harness self-validation
# --------------------------------------------------------------------------- #
def test_every_file_the_ledgers_read_exists():
    """Every ledger assertion below reads one of these. If one moved, the
    assertion would parse an empty string and pass while measuring nothing."""
    for path in (RUN_TESTS, CONFTEST, *RUNNERS):
        assert path.is_file(), f"{path} missing — the ledger tests are vacuous"


# --------------------------------------------------------------------------- #
# 1. THE LEDGERS — one owner (gitenv.py), four spellings, pinned both ways
# --------------------------------------------------------------------------- #
def _shell_unset_names(runner: Path) -> list[str]:
    """The names `runner`'s GUARD 9 block actually clears.

    Read out of the ARRAY LITERAL, comments stripped, and the array is then
    required to be what the `unset` line expands — so a runner that keeps the
    array for show and unsets something else cannot pass.
    """
    text = runner.read_text(encoding="utf-8")
    m = re.search(r"^DEVRC_GIT_REPO_POINTERS=\(\n(.*?)^\)$", text, re.M | re.S)
    assert m, (
        f"no DEVRC_GIT_REPO_POINTERS array in {runner.name}. GUARD 9's shell half "
        "is what protects the NON-pytest targets (HOOK_TESTS, SHELL_TESTS, the "
        "node tier) and what keeps ROOT resolvable at all under an inherited "
        "GIT_DIR."
    )
    assert 'unset "${DEVRC_GIT_REPO_POINTERS[@]}"' in text, (
        f"{runner.name} declares the array but never unsets it. 🔴 A declaration "
        "is not a code path (claude/RULES.md)."
    )
    names: list[str] = []
    for line in m.group(1).splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            names.extend(line.split())
    return names


@pytest.mark.parametrize("runner", RUNNERS, ids=lambda p: p.name)
def test_the_shell_and_python_pointer_ledgers_agree(runner):
    """🔴 BOTH DIRECTIONS, for EVERY runner. A name in Python and not in a
    runner leaves that runner's non-pytest targets exposed and its ROOT
    resolvable only by luck; a name in a runner and not in Python leaves a bare
    `pytest` exposed. Either way the guard claims coverage it does not have."""
    shell = _shell_unset_names(runner)
    python = list(REPO_POINTER_VARS)
    assert sorted(shell) == sorted(python), (
        f"GUARD 9's ledgers disagree between {runner.name} and gitenv.py.\n"
        f"  only in {runner.name}: {sorted(set(shell) - set(python))}\n"
        f"  only in gitenv.py    : {sorted(set(python) - set(shell))}\n"
        "Update every spelling, in the same commit."
    )


@pytest.mark.parametrize("runner", RUNNERS, ids=lambda p: p.name)
def test_the_shell_ledger_has_no_duplicates(runner):
    """A duplicated name reads as a longer list than it is."""
    shell = _shell_unset_names(runner)
    assert len(shell) == len(set(shell)), f"duplicates in {runner.name}: {shell}"


def test_GIT_DIR_is_on_every_ledger():
    """The one that actually happened. Pinned by name so a future prune of the
    list cannot quietly drop the variable the incident was made of."""
    assert "GIT_DIR" in REPO_POINTER_VARS
    for runner in RUNNERS:
        assert "GIT_DIR" in _shell_unset_names(runner), runner.name


def _root_line(text: str) -> int:
    """The line that ASSIGNS ROOT from `rev-parse --show-toplevel`.

    🔴 Comment lines are skipped, and `ROOT=` is required on the line. The first
    version matched any mention of the phrase and therefore matched GUARD 9's
    own explanatory COMMENT — which sits above the assignment, so all three
    runners reported "clears AFTER ROOT" while being correctly ordered. A
    parser that cannot tell code from prose is measuring the wrong thing.
    """
    for i, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        if "ROOT=" in line and "rev-parse" in line and "--show-toplevel" in line:
            return i
    return -1


def _clear_line(text: str) -> int:
    for i, line in enumerate(text.splitlines(), 1):
        if line.strip() == 'unset "${DEVRC_GIT_REPO_POINTERS[@]}"':
            return i
    return -1


@pytest.mark.parametrize("runner", RUNNERS, ids=lambda p: p.name)
def test_every_runner_clears_BEFORE_it_resolves_its_root(runner):
    """🔴 ORDER, not merely presence — and this is a MEASURED requirement, not a
    tidiness preference.

    All three runners resolve their root with
    `git -C "$(dirname "$0")" rev-parse --show-toplevel`. With GIT_DIR set and
    no GIT_WORK_TREE, git takes the CWD as the top of the work tree, so that
    returns `<repo>/scripts`; the runner then looks for
    `<repo>/scripts/scripts/run-tests.sh` and exits 127 having produced NO
    verdict line. Found by running the gate end-to-end with a poisoned GIT_DIR —
    the first placement of this guard was *after* the ROOT block and the whole
    gate died before reaching it.

    Same class as the `unset CDPATH` sitting a few lines under run-tests.sh's
    ROOT block, and recorded in the same spirit.
    """
    text = runner.read_text(encoding="utf-8")
    clear = _clear_line(text)
    root = _root_line(text)
    assert clear > 0, f"{runner.name} has no GUARD 9 `unset` line at all"
    assert root > 0, f"{runner.name} has no `rev-parse --show-toplevel` line to order against"
    assert clear < root, (
        f"{runner.name} clears the git repo pointers at line {clear}, AFTER it "
        f"resolves ROOT at line {root}. An inherited GIT_DIR then makes ROOT "
        f"`<repo>/scripts` and the run dies exit 127 with no verdict."
    )


def test_every_pytest_target_gets_the_detector():
    """`-p testlib.gitenv_plugin` must sit on the ONE pytest line, beside GUARD
    7's and GUARD 8's. `scripts/run-tests.sh` runs one pytest process per target
    and there are seventeen of them; a plugin registered from a conftest reaches
    one directory (that is #399's and #614's measured failure, twice)."""
    text = re.sub(r"\\\n\s*", " ", RUN_TESTS.read_text(encoding="utf-8"))
    pytest_lines = [ln for ln in text.splitlines()
                    if "python -m pytest" in ln and "$d" in ln]
    assert pytest_lines, "could not find the per-target pytest invocation in run-tests.sh"
    for ln in pytest_lines:
        assert "-p testlib.gitenv_plugin" in ln, (
            "a per-target pytest invocation runs without GUARD 9:\n"
            f"  {ln.strip()}"
        )


def test_the_scripts_tests_conftest_is_the_second_entry_point():
    """A bare `pytest scripts/tests` outside the runner must get the same guard
    — by the same module, not a second copy of the logic.

    🔴 STRUCTURAL, NOT SPELLED. The first version of this test grepped the
    conftest for the strings `testlib.gitenv_plugin` and
    `_devrc_git_repo_isolation`, and a mutant that moved the whole import under
    `if False:` SURVIVED it: both words were still on the page. What pytest acts
    on is the module OBJECT's attributes, so that is what is asserted — and the
    objects must be the plugin's own, not same-named look-alikes.
    """
    import importlib.util

    from testlib import gitenv_plugin

    spec = importlib.util.spec_from_file_location("_devrc_conftest_probe", CONFTEST)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    for attr in ("_devrc_git_repo_isolation", "pytest_collection_finish",
                 "pytest_sessionfinish"):
        got = getattr(module, attr, None)
        assert got is getattr(gitenv_plugin, attr), (
            f"scripts/tests/conftest.py does not re-export `{attr}` from "
            "testlib.gitenv_plugin, so a bare `pytest scripts/tests` runs "
            f"without that half of GUARD 9 (found: {got!r})"
        )


# --------------------------------------------------------------------------- #
# 2. NO SHELL SCRIPT MAY HAND A REPO POINTER TO THE RUNNER
# --------------------------------------------------------------------------- #
# `githooks/pre-push` did exactly this: `GIT_DIR="$(git rev-parse --git-dir)"`.
# In bash, assigning to an ALREADY-EXPORTED name keeps it exported, so that line
# hands this repository's git dir to `tests-on-push.sh` -> `run-tests.sh` ->
# pytest whenever a caller has GIT_DIR in the environment. A concrete route from
# `git push` to the incident, and invisible to every test that existed.
_SHELL_SCRIPTS = ("githooks", "scripts")


def _shell_files() -> list[Path]:
    out: list[Path] = []
    for rel in _SHELL_SCRIPTS:
        base = ROOT / rel
        if not base.is_dir():
            continue
        for p in sorted(base.rglob("*")):
            if not p.is_file():
                continue
            if p.suffix == ".sh" or (p.parent.name == "githooks" and p.suffix == ""):
                out.append(p)
    return out


def test_the_shell_scan_sees_files_at_all():
    """The positive control for the scan below. A zero from a walker that
    matched nothing is indistinguishable from a clean repo."""
    files = _shell_files()
    assert len(files) >= 10, f"the shell-script walk found only {len(files)} files"
    assert any(p.name == "pre-push" for p in files), (
        "githooks/pre-push is not in the scanned set — it is the file this "
        "check exists for."
    )


def test_no_shell_script_that_can_reach_the_runner_assigns_a_git_repo_pointer():
    pattern = re.compile(
        r"^[ \t]*(?:export[ \t]+)?(" + "|".join(REPO_POINTER_VARS) + r")=", re.M)
    offenders: list[str] = []
    for path in _shell_files():
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for m in pattern.finditer(text):
            line = text[:m.start()].count("\n") + 1
            offenders.append(f"{path.relative_to(ROOT)}:{line}: {m.group(1)}=")
    assert not offenders, (
        "a shell script assigns to a git repo-pointer variable:\n  "
        + "\n  ".join(offenders)
        + "\n\nIn bash an assignment to an already-exported name STAYS exported, "
          "so this hands a repository to every child process — including the test "
          "runner, whose fixtures then build themselves inside it. Rename the "
          "variable (githooks/pre-push uses HOOK_GIT_DIR)."
    )


# --------------------------------------------------------------------------- #
# 3. THE SANITISER, measured
# --------------------------------------------------------------------------- #
def test_strip_removes_exactly_the_ledger_and_reports_what_it_removed():
    env = {name: f"/tmp/whatever/{name}" for name in REPO_POINTER_VARS}
    env["GIT_CONFIG_GLOBAL"] = "/tmp/keep-me"     # deliberately NOT stripped
    env["GIT_AUTHOR_NAME"] = "keep me too"
    # 🔴 NOT spelled "PATH". `testlib/launcher_scan.py` AST-scans this directory
    # for keys named PATH and pins every site, because a literal PATH drops the
    # nolaunch stub dir (GUARD 7). This dict never reaches a subprocess, but the
    # scanner cannot know that, and a new pin entry here would read as a real
    # PATH clobber to the next person auditing that list.
    env["UNRELATED_SETTING"] = "kept"
    removed = strip_repo_pointers(env)
    assert sorted(removed) == sorted(REPO_POINTER_VARS)
    assert sorted(env) == ["GIT_AUTHOR_NAME", "GIT_CONFIG_GLOBAL",
                           "UNRELATED_SETTING"], env
    # Report the PAIR: what moved, and what deliberately did not.
    assert removed["GIT_DIR"] == "/tmp/whatever/GIT_DIR"


def test_strip_on_a_clean_environment_removes_nothing():
    """The no-op control. Without it, `removed == {}` above would be
    indistinguishable from a stripper wired to nothing."""
    env = {"UNRELATED_SETTING": "kept"}
    assert strip_repo_pointers(env) == {}
    assert env == {"UNRELATED_SETTING": "kept"}


def test_the_live_environment_is_already_clean_under_this_guard():
    """This suite is itself running under GUARD 9, so nothing should be left."""
    still_set = [n for n in REPO_POINTER_VARS if n in os.environ]
    assert not still_set, (
        f"{still_set} survived into a guarded pytest session — the strip did not "
        "run, or something re-set it afterwards."
    )


# --------------------------------------------------------------------------- #
# 4. THE DETECTOR, as a unit — it must MOVE, and it must stay still
# --------------------------------------------------------------------------- #
def test_the_detector_observes_nothing_when_nothing_happens(tmp_path):
    repo = _mkrepo(tmp_path / "repo")
    git_dir = resolve_git_dir(repo)
    assert git_dir is not None
    before = snapshot([git_dir])
    after = snapshot([git_dir])
    assert diff_snapshots(before, after) == []


@pytest.mark.parametrize("mutation,expect", [
    (("checkout", "-q", "-b", "topic"), "refs/heads/topic"),
    (("branch", "-q", "side"), "refs/heads/side"),
    (("config", "user.name", "T"), "config"),
    (("config", "core.hooksPath", "/tmp/elsewhere"), "config"),
    (("remote", "add", "origin", "/tmp/nowhere.git"), "config"),
])
def test_the_detector_moves_for_every_damage_class_from_the_incident(
        tmp_path, mutation, expect):
    """🔴 REPORTED BESIDE THE NO-OP CONTROL ABOVE. Each row is a shape that was
    actually found in the operator's clone: a branch create, a HEAD move, a
    `user.name`, a `core.hooksPath`, a rewritten remote URL."""
    repo = _mkrepo(tmp_path / "repo")
    git_dir = resolve_git_dir(repo)
    before = snapshot([git_dir])
    assert _git(repo, *mutation).returncode == 0
    deltas = diff_snapshots(before, snapshot([git_dir]))
    assert deltas, f"the detector did not see `git {' '.join(mutation)}`"
    assert any(expect in d for d in deltas), (
        f"expected a delta naming {expect!r}, got:\n" + "\n".join(deltas))


def test_the_detector_sees_a_branch_DELETION(tmp_path):
    """The incident's worst single act: `refs/heads/main` removed outright.
    A detector that only hashes files it finds would report SAME for a file that
    stopped existing (claude/RULES.md → a comparison against an absent operand
    reports SAME, not MISSING)."""
    repo = _mkrepo(tmp_path / "repo")
    git_dir = resolve_git_dir(repo)
    assert _git(repo, "checkout", "-q", "-b", "other").returncode == 0
    before = snapshot([git_dir])
    assert _git(repo, "branch", "-D", "main").returncode == 0
    deltas = diff_snapshots(before, snapshot([git_dir]))
    assert any("DELETED" in d and "refs/heads/main" in d for d in deltas), (
        "a deleted branch was not reported as DELETED:\n" + "\n".join(deltas))


def test_the_detector_covers_a_WORKTREE_via_its_common_dir(tmp_path):
    """A worktree's `.git` is a FILE, and its refs live in the COMMON dir.
    Fingerprinting only the per-worktree dir would miss every ref and config
    write — and `claude/RULES.md` makes worktree isolation the standing default
    for file-modifying agents, so this is the normal case, not the exotic one."""
    repo = _mkrepo(tmp_path / "repo")
    wt = tmp_path / "wt"
    assert _git(repo, "worktree", "add", "-q", "-b", "wt-branch", str(wt)).returncode == 0
    assert (wt / ".git").is_file(), "expected a worktree .git FILE, not a directory"
    dirs = protected_git_dirs([wt])
    assert dirs, "no git dir resolved from inside a worktree"
    before = snapshot(dirs)
    assert _git(wt, "branch", "-q", "made-from-the-worktree").returncode == 0
    deltas = diff_snapshots(before, snapshot(dirs))
    assert any("made-from-the-worktree" in d for d in deltas), (
        "a ref created from a worktree was invisible — the common dir is not "
        "being watched:\n" + "\n".join(deltas))


# --------------------------------------------------------------------------- #
# 5. 🔴 THE INCIDENT, REPRODUCED AND THEN PREVENTED
# --------------------------------------------------------------------------- #
# Both halves run a NESTED pytest in a tmp rootdir, so no conftest of this repo
# is loaded and the only difference between them is the `-p` flag.

_FIXTURE_SHAPED_TEST = '''
"""A fixture written exactly the way every git fixture in this repo is: the
repo path is passed with `-C`, built under tmp_path, never a bare `git`."""
import subprocess


def test_a_hygienic_fixture(tmp_path):
    work = tmp_path / "work"
    subprocess.run(["git", "init", "-q", "-b", "main", str(work)],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(work), "checkout", "-q", "-b", "topic"],
                   check=True, capture_output=True)
'''

_ABSOLUTE_PATH_MUTATOR = '''
"""Writes to the protected repo by ABSOLUTE PATH, so the environment strip
cannot prevent it. This is the escape route the strip does NOT close, and
therefore exactly what the detector has to catch."""
import os
import subprocess


def test_writes_where_it_should_not():
    target = os.environ["GUARD9_TARGET_REPO"]
    subprocess.run(["git", "-C", target, "branch", "-q", "escaped-branch"],
                   check=True, capture_output=True)
'''

_INNOCENT_TEST = '''
def test_touches_nothing():
    assert 1 + 1 == 2
'''

_IMPORT_TIME_MUTATOR = '''
import os
import subprocess

# At MODULE scope — this runs during COLLECTION, like test_bash_guard.py's
# _mkrepo and test_guard_core.py's module-scoped repos.
subprocess.run(["git", "-C", os.environ["GUARD9_TARGET_REPO"],
                "branch", "-q", "escaped-at-import"],
               check=True, capture_output=True)


def test_placeholder():
    assert True
'''


def _nested_pytest(tmp_path, body: str, *, plugin: bool, env_extra: dict) -> subprocess.CompletedProcess:
    rootdir = tmp_path / "nested"
    rootdir.mkdir(exist_ok=True)
    (rootdir / "test_nested.py").write_text(body, encoding="utf-8")
    argv = [sys.executable, "-m", "pytest", str(rootdir / "test_nested.py"),
            "-q", "-p", "no:cacheprovider"]
    if plugin:
        argv += ["-p", "testlib.gitenv_plugin"]
    env = _env(**env_extra)
    env["PYTHONPATH"] = str(SCRIPTS) + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.run(argv, capture_output=True, text=True, env=env,
                          cwd=str(rootdir))


def test_an_inherited_GIT_DIR_makes_a_hygienic_fixture_write_to_the_ambient_repo(tmp_path):
    """🔴 THE BUG, REPRODUCED. Without GUARD 9, a fixture that does everything
    right — `git init <tmp>`, then `git -C <tmp> checkout -b topic` — creates
    `topic` in whatever repo GIT_DIR names. This is the RED half of the pair; it
    is what the operator's clone experienced on 2026-08-21.

    If this test ever stops reproducing the damage, the pair below stops being
    evidence: a fix is only observable against a control that shows the defect.
    """
    ambient = _mkrepo(tmp_path / "ambient")
    proc = _nested_pytest(tmp_path, _FIXTURE_SHAPED_TEST, plugin=False,
                          env_extra={"GIT_DIR": str(ambient / ".git")})
    assert proc.returncode == 0, f"the nested run did not even pass:\n{proc.stdout}\n{proc.stderr}"
    branches = _git(ambient, "branch", "--format=%(refname:short)").stdout.split()
    assert "topic" in branches, (
        "the reproduction no longer reproduces — this control is the only thing "
        "that makes the guarded run below meaningful.\n"
        f"ambient branches: {branches}"
    )


def test_with_GUARD_9_the_same_fixture_leaves_the_ambient_repo_alone(tmp_path):
    """🔴 THE GREEN HALF. Identical run, identical env, one extra `-p`."""
    ambient = _mkrepo(tmp_path / "ambient")
    proc = _nested_pytest(tmp_path, _FIXTURE_SHAPED_TEST, plugin=True,
                          env_extra={"GIT_DIR": str(ambient / ".git")})
    assert proc.returncode == 0, f"{proc.stdout}\n{proc.stderr}"
    branches = _git(ambient, "branch", "--format=%(refname:short)").stdout.split()
    assert "topic" not in branches, (
        f"GUARD 9 did not stop the escape; ambient branches: {branches}\n"
        f"{proc.stdout}"
    )
    assert branches == ["main"], f"the ambient repo moved anyway: {branches}"
    # 🔴 AND THE FIXTURE'S OWN REPO STILL GOT THE BRANCH. Without this the test
    # would pass just as well if the strip had merely BROKEN git — "the ambient
    # repo did not move" is also true when nothing ran at all. The nested test
    # asserted its own `check=True`, but its tmp repo is gone by now, so the
    # standing evidence is the nested run's own green plus the marker below.
    assert "1 passed" in proc.stdout, (
        f"the fixture did not actually run to completion:\n{proc.stdout}")
    assert "gitenv(session)" in proc.stdout, (
        "the guard did not announce itself; this run may not have loaded it at "
        f"all:\n{proc.stdout}"
    )


def test_the_detector_fails_the_test_that_moved_a_ref(tmp_path):
    """🔴 REACHABILITY, not merely breakability. The nested test writes by
    ABSOLUTE PATH, so the environment strip is bypassed and the ONLY thing that
    can go red is the detector. Asserted by this guard's own token, so a failure
    from some neighbouring check cannot be scored as a kill."""
    ambient = _mkrepo(tmp_path / "ambient")
    proc = _nested_pytest(
        tmp_path, _ABSOLUTE_PATH_MUTATOR, plugin=True,
        env_extra={PROTECT_ENV: str(ambient / ".git"),
                   "GUARD9_TARGET_REPO": str(ambient)})
    out = proc.stdout + proc.stderr
    assert proc.returncode != 0, f"the detector stayed green on a real escape:\n{out}"
    assert VIOLATION_TOKEN in out, (
        f"the run failed, but not with GUARD 9's message — green for the wrong "
        f"reason:\n{out}")
    assert "test_writes_where_it_should_not" in out, (
        f"the violation did not name the offending test:\n{out}")
    assert "escaped-branch" in out, (
        f"the violation did not name the ref that moved:\n{out}")


def test_the_detector_is_silent_on_a_test_that_touches_nothing(tmp_path):
    """The other half of the pair. A detector that reds on everything is a
    permanently-red gate, which claude/RULES.md rates worse than no gate."""
    ambient = _mkrepo(tmp_path / "ambient")
    proc = _nested_pytest(
        tmp_path, _INNOCENT_TEST, plugin=True,
        env_extra={PROTECT_ENV: str(ambient / ".git")})
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, f"a harmless test was failed by GUARD 9:\n{out}"
    assert VIOLATION_TOKEN not in out, out
    assert "1 passed" in out, out


def test_the_detector_catches_damage_done_at_IMPORT_time(tmp_path):
    """Two suites in this repo build real git repos at MODULE scope, so their
    git runs during COLLECTION — before any test starts. A per-test-only check
    would attribute that to an arbitrary test, or to none."""
    ambient = _mkrepo(tmp_path / "ambient")
    proc = _nested_pytest(
        tmp_path, _IMPORT_TIME_MUTATOR, plugin=True,
        env_extra={PROTECT_ENV: str(ambient / ".git"),
                   "GUARD9_TARGET_REPO": str(ambient)})
    out = proc.stdout + proc.stderr
    assert proc.returncode != 0, f"import-time damage went unnoticed:\n{out}"
    assert VIOLATION_TOKEN in out, out
    assert "escaped-at-import" in out, out
    assert "collection" in out, (
        f"the violation was not attributed to collection:\n{out}")


def test_the_violation_message_names_the_remediation(tmp_path):
    """A red gate whose message does not say what to do gets clicked through."""
    ambient = _mkrepo(tmp_path / "ambient")
    proc = _nested_pytest(
        tmp_path, _ABSOLUTE_PATH_MUTATOR, plugin=True,
        env_extra={PROTECT_ENV: str(ambient / ".git"),
                   "GUARD9_TARGET_REPO": str(ambient)})
    out = proc.stdout + proc.stderr
    assert "git reflog" in out, f"no recovery instruction in the failure:\n{out}"
    assert "GIT_DIR" in out, f"the failure does not name the known mechanism:\n{out}"
