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

🔴 WHAT IS **NOT** KNOWN, stated here because #683's body asserted it and it was
relayed onward: nothing has identified WHAT exported `GIT_DIR` into that run.
`git push` does not hand `GIT_DIR` to `pre-push` (measured, git 2.55.0 — see
`test_git_push_does_not_export_GIT_DIR_to_pre_push`), so the hook's old
`GIT_DIR=` line was a route only when an outer caller had already exported the
name. The rename is hygiene, not a diagnosis.

HOW THIS FILE IS BUILT, and why each layer is not redundant with the next:

  1. LEDGERS. The variable lists are owned once in Python (`testlib/gitenv.py`)
     and re-spelled in each of the FIVE shell files that clear them
     (`POINTER_CLEARERS`) — the four entry points need them because HOOK_TESTS,
     SHELL_TESTS and the node tier never load a pytest plugin, and because an
     inherited GIT_DIR breaks ROOT resolution before any Python runs;
     `analyze-service-index/commit.sh` needs them because no test tier reaches
     it at all. Pinned in BOTH directions for every spelling: a set that only
     grows on one side is a guard that protects twelve targets and not five.
     The FILE LIST is pinned against a `git ls-files` sweep too, so a sixth
     clearer added later cannot sit unpinned.
  2. ENTRY POINTS, as a LEDGER rather than an example. Every `conftest.py`
     under `scripts/` must re-export the plugin's hooks, and the SET is pinned,
     so adding a test directory cannot silently leave a bare `pytest <dir>`
     unguarded. #683 wired exactly one of seven — and not the one the plugin's
     own rationale cites.
  3. THE FINGERPRINT'S CONTENT, component by component. Each entry is pinned by
     a mutation that ONLY it can see, because "the detector noticed something"
     is satisfied by any surviving component: five semantic mutants survived
     #683's fully green suite.
  4. 🔴 THE INCIDENT ITSELF, as a nested-pytest control pair. One run WITHOUT
     the plugin reproduces the damage (a fixture-shaped `git -C <tmp>` writing
     a branch into the ambient repo); one run WITH it does not.
  5. REACHABILITY. The violation is asserted by `VIOLATION_TOKEN` — this
     guard's own marker — so a control cannot pass because a DIFFERENT check
     errored first (claude/RULES.md → "prove it REACHABLE, not just breakable").
  6. 🔴 ATTRIBUTION. The detector watches a repository with other writers on it.
     Both directions are pinned: a change made while no test was running must
     NOT be blamed on a test (and must downgrade the session), and a real escape
     must still be caught and named.
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

# Every entry point that resolves a repo root with
# `git rev-parse --show-toplevel` and then runs tests. All four carry GUARD 9's
# `unset`, and all four must run it BEFORE that line — see
# test_every_runner_clears_BEFORE_it_resolves_its_root.
#
# 🔴 `githooks/tests-on-push.sh` IS ONE OF THEM and #683 left it out, while this
# very docstring described it. It sits between `pre-push` and `run-tests.sh` on
# the `git push` path — the path the incident travelled — and resolves
# `REPO_ROOT` with exactly the vulnerable expression.
#
# 🔴 FIVE SPELLINGS RATHER THAN ONE SOURCED FILE, and the reason is measured,
# not aesthetic: `testlib/runner_patch.py` writes a patched COPY of
# `run-tests.sh` into a tmp dir and about fifteen tests drive that copy. A copy
# cannot source a sibling `lib/` that was never copied with it — the first
# version of this guard did exactly that and turned those fifteen red with
# `run-tests: FATAL — cannot source lib/git-repo-pointers.sh`. So the SETS are
# owned once, in `testlib/gitenv.py`, and every spelling is pinned to them here.
RUNNERS = (SCRIPTS / "run-tests.sh",
           SCRIPTS / "run-node-tests.sh",
           SCRIPTS / "gate.sh",
           ROOT / "githooks" / "tests-on-push.sh")

# 🔴 THE WRITER, added 2026-08-22. `analyze-service-index/commit.sh` is the one
# program in this repo whose job is to `git commit`, and it is NOT a runner: it
# resolves no ROOT, and it runs from a systemd timer and from an operator's
# shell, neither of which passes through anything in RUNNERS. It carries the
# same `unset` for the same reason and is pinned to the same owner — MEASURED on
# the pre-fix tree, `GIT_DIR=<decoy>/.git/worktrees/wt commit.sh <store>` put an
# `autocommit: N change(s) in the some-scope analyze-service index` commit on the
# decoy's branch and exited 0, which is the 2026-08-21 incident's damage arriving
# by a route no test tier covers.
#
# It is kept OUT of `RUNNERS` deliberately rather than folded in: the ordering
# test below is about the ROOT resolution those four share and commit.sh has
# none, so a single list would have to weaken that assertion to accommodate it.
COMMIT_SH = SCRIPTS / "analyze-service-index" / "commit.sh"

# Every file that carries a `DEVRC_GIT_REPO_POINTERS` spelling. The ledger pins
# below run over ALL of them; only the ROOT-ordering pin is runner-specific.
POINTER_CLEARERS = (*RUNNERS, COMMIT_SH)

sys.path.insert(0, str(SCRIPTS))

from testlib import gitenv, gitenv_plugin, mockbin  # noqa: E402
from testlib.gitenv import (  # noqa: E402
    CONTROL_VARS,
    FOREIGN_MARKER,
    MODE_ENFORCE,
    MODE_ENV,
    MODE_REPORT,
    OBSERVED_MARKER,
    PROTECT_ENV,
    global_config_paths,
    REPO_POINTER_VARS,
    SESSION_MARKER,
    VIOLATION_TOKEN,
    GitEnvConfigError,
    common_dir_of,
    diff_snapshots,
    live_cotenants,
    protected_git_dirs,
    ref_values,
    requested_mode,
    resolve_git_dir,
    resolve_protect_env,
    snapshot,
    strip_repo_pointers,
)

# 🔴 IMPORTED, not re-implemented. `repo_files` prefers `git ls-files` and falls
# back to a filesystem walk when there is no `.git` — which is exactly the nix
# build sandbox, where `checks.pytests` runs off `cp -r ${./.} src`.
# `captured_text_scan.py` carries a "ONE RULE, ONE PLACE … a third copy of it
# would be a third place for that trap to come back" banner over this helper.
from testlib.public_ip_scan import _is_skipped, repo_files  # noqa: E402

# 🔴 NOT a shebang and NOT a bare "bash". Section 6 drives run-tests.sh as a
# subprocess; an unnarrowed `shutil.which` result would surface as a TypeError
# from inside subprocess, naming the wrong cause.
_BASH = shutil.which("bash")
if _BASH is None:  # pragma: no cover - the gate puts bash on PATH
    raise RuntimeError(
        "bash is not on PATH. It is in run-tests.sh's REQUIRED_TOOLS and in the "
        "flake's pytests check — add it there rather than skipping these tests.")
BASH: str = _BASH

# 🔴 NOT a skipif. `git` is in run-tests.sh's REQUIRED_TOOLS and in the flake's
# pytests check, so its absence is an ERROR: a skipped isolation test reports a
# safety property it never measured, which is the exact failure mode that let
# the incident happen on a green suite.
if shutil.which("git") is None:  # pragma: no cover - the gate puts git on PATH
    raise RuntimeError(
        "git is not on PATH. Add it to REQUIRED_TOOLS in scripts/run-tests.sh "
        "and to the pytests check in flake.nix rather than skipping these tests."
    )

def _require_proc() -> None:
    """🔴 NOT a skip, for the same reason `git` above is not a skipif.

    `live_cotenants` returns `[]` where `/proc` is unreadable, and `[]` is what
    puts the detector in ENFORCE mode — so a SKIPPED co-tenant test reports a
    safety property it never measured, on the exact hosts where the probe is
    silently inert. Both devrc hosts are NixOS and the hermetic gate runs in a
    Linux sandbox, so this is an ERROR, not an environment excuse.
    """
    assert Path("/proc").is_dir(), (
        "/proc is missing, so GUARD 9's co-tenant probe cannot work and every "
        "session would run in ENFORCE mode with no evidence. Port "
        "`live_cotenants` to this platform rather than skipping the control."
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
    for path in (RUN_TESTS, CONFTEST, *POINTER_CLEARERS):
        assert path.is_file(), f"{path} missing — the ledger tests are vacuous"


# --------------------------------------------------------------------------- #
# 1. THE LEDGERS — one owner (gitenv.py), five spellings, pinned both ways
# --------------------------------------------------------------------------- #
def _unset_line_number(text: str, array: str) -> int:
    """The 1-based line that RUNS `unset "${<array>[@]}"`, or -1.

    🔴 EXACT MATCH ON A STRIPPED LINE, never `in text`. A substring test is
    satisfied by `# unset "${DEVRC_GIT_REPO_POINTERS[@]}"` — commenting the guard
    out leaves every word on the page, so the pin stays green while the guard
    stops executing. claude/RULES.md's spelled-guard shape, and it was live here:
    `_clear_line` below did the exact match but ran only over `RUNNERS`, so
    `commit.sh` — which has no ROOT resolution to fall back on — was covered only
    by the substring version. MEASURED: commenting `commit.sh`'s unset out killed
    0 tests before this change and 9 after.
    """
    statement = f'unset "${{{array}[@]}}"'
    for i, line in enumerate(text.splitlines(), 1):
        if line.strip() == statement:
            return i
    return -1


def _shell_array(runner: Path, array: str) -> list[str]:
    """The names in `runner`'s `<array>=( … )` literal, comments stripped."""
    text = runner.read_text(encoding="utf-8")
    m = re.search(rf"^{array}=\(\n(.*?)^\)$", text, re.M | re.S)
    assert m, (
        f"no {array} array in {runner.name}. GUARD 9's shell half is what "
        "protects the NON-pytest targets (HOOK_TESTS, SHELL_TESTS, the node "
        "tier) and what keeps ROOT resolvable at all under an inherited GIT_DIR."
    )
    assert _unset_line_number(text, array) > 0, (
        f"{runner.name} declares {array} but never runs the unset as a LIVE "
        "statement — it is absent, or commented out. 🔴 A declaration is not a "
        "code path (claude/RULES.md), and neither is a comment that reads like "
        "one."
    )
    names: list[str] = []
    for line in m.group(1).splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            names.extend(line.split())
    return names


def _shell_unset_names(runner: Path) -> list[str]:
    return _shell_array(runner, "DEVRC_GIT_REPO_POINTERS")


@pytest.mark.parametrize("runner", POINTER_CLEARERS, ids=lambda p: p.name)
def test_the_shell_and_python_pointer_ledgers_agree(runner):
    """🔴 BOTH DIRECTIONS, for EVERY spelling. A name in Python and not in a
    runner leaves that runner's non-pytest targets exposed and its ROOT
    resolvable only by luck; a name in a runner and not in Python leaves a bare
    `pytest` exposed. Either way the guard claims coverage it does not have.

    `commit.sh` is in this list for a different reason from the four runners:
    nothing there resolves a ROOT, but it is the file that COMMITS, so a name
    missing from its copy is a repository somebody else's content can land in.
    """
    shell = _shell_unset_names(runner)
    python = list(REPO_POINTER_VARS)
    assert sorted(shell) == sorted(python), (
        f"GUARD 9's ledgers disagree between {runner.name} and gitenv.py.\n"
        f"  only in {runner.name}: {sorted(set(shell) - set(python))}\n"
        f"  only in gitenv.py    : {sorted(set(python) - set(shell))}\n"
        "Update every spelling, in the same commit."
    )


@pytest.mark.parametrize("runner", RUNNERS, ids=lambda p: p.name)
def test_the_shell_and_python_CONTROL_ledgers_agree(runner):
    """🔴 GUARD 9's OWN SEAMS ARE ON THE UNSET LIST TOO — finding B of #683's
    audit, and the sharpest one, because the fix reintroduced its own bug.

    `DEVRC_GITENV_PROTECT` redirects the whole detector and no runner cleared
    it. Measured on that code, same escaping test, three spellings:

        <real>/.git      -> protected-git-dirs=1, RED with the guard's token
        ":"              -> protected-git-dirs=0, GREEN, branch really created
        /nonexistent/x   -> protected-git-dirs=1, GREEN, branch really created

    One inherited environment variable defeating every layer is the incident
    this guard exists for; leaving one inside the guard was not acceptable.
    """
    shell = _shell_array(runner, "DEVRC_GITENV_CONTROL_VARS")
    assert sorted(shell) == sorted(CONTROL_VARS), (
        f"GUARD 9's control-var ledgers disagree between {runner.name} and "
        f"gitenv.py.\n  only in {runner.name}: {sorted(set(shell) - set(CONTROL_VARS))}"
        f"\n  only in gitenv.py    : {sorted(set(CONTROL_VARS) - set(shell))}"
    )


@pytest.mark.parametrize("runner", RUNNERS, ids=lambda p: p.name)
def test_the_shell_ledger_has_no_duplicates(runner):
    """A duplicated name reads as a longer list than it is."""
    for array in ("DEVRC_GIT_REPO_POINTERS", "DEVRC_GITENV_CONTROL_VARS"):
        names = _shell_array(runner, array)
        assert len(names) == len(set(names)), f"duplicates in {runner.name}:{array}: {names}"


def test_the_committers_ledger_has_no_duplicates():
    """🔴 `commit.sh` is checked SEPARATELY, and deliberately not folded into the
    parametrised test above.

    That one requires BOTH arrays, and `DEVRC_GITENV_CONTROL_VARS` names the
    DETECTOR's own seams (`DEVRC_GITENV_PROTECT`, `DEVRC_GITENV_MODE`) — which
    exist only inside a test harness. `commit.sh` is not one; it is a systemd
    unit that commits. Adding that array there to satisfy a parametrisation
    would be cargo-culting a guard into a program it cannot apply to, and would
    make the pin read as coverage of something that was never at risk.
    """
    names = _shell_array(COMMIT_SH, "DEVRC_GIT_REPO_POINTERS")
    assert len(names) == len(set(names)), f"duplicates in commit.sh: {names}"
    local = _shell_array(COMMIT_SH, "ASI_LOCAL_GIT_POINTERS")
    assert len(local) == len(set(local)), f"duplicates in ASI_LOCAL_GIT_POINTERS: {local}"
    overlap = set(names) & set(local)
    assert not overlap, (
        f"{sorted(overlap)} is in BOTH of commit.sh's arrays. The local array is "
        "for variables the SHARED ledger deliberately does not carry; a name in "
        "both means one of the two lists is lying about its scope.")


def test_GIT_DIR_is_on_every_ledger():
    """The one that actually happened. Pinned by name so a future prune of the
    list cannot quietly drop the variable the incident was made of."""
    assert "GIT_DIR" in REPO_POINTER_VARS
    for runner in POINTER_CLEARERS:
        assert "GIT_DIR" in _shell_unset_names(runner), runner.name


def test_the_runner_and_python_session_markers_agree():
    """`run-tests.sh` COUNTS the plugin's marker per target, so the token is
    spelled on both sides of a process boundary — pinned both ways, exactly like
    `SPOOL_SESSION_MARKER`. A rename on one side alone would make the count
    match nothing and report a clean run."""
    text = RUN_TESTS.read_text(encoding="utf-8")
    m = re.search(r'^GITENV_SESSION_MARKER="([^"]+)"$', text, re.M)
    assert m, "run-tests.sh no longer spells GITENV_SESSION_MARKER"
    assert m.group(1) == SESSION_MARKER, (
        f"run-tests.sh says {m.group(1)!r}, gitenv.py says {SESSION_MARKER!r}")
    assert f'grep -ac "^$GITENV_SESSION_MARKER"' in text, (
        "run-tests.sh declares the marker but never counts it. 🔴 A declared "
        "positive control that is never read is the reassuring zero itself: "
        "'loaded and saw nothing' and 'never loaded' print identically."
    )


# 🔴 ASSEMBLED, never spelled literally. If this module's own text contained the
# array-opening string, the sweep below would match THIS FILE the moment anyone
# widened its suffix filter — a red for a reason that has nothing to do with the
# guard. Assembling it makes self-exclusion a property of the MARKER rather than
# an accident of the filter, so the filter is free to say only what it means:
# WHERE a bash array can live.
#
# Which is why the sentence above describes the string instead of quoting it.
# The first draft of this very comment spelled it out and put the literal right
# back into the file — `test_this_file_never_spells_the_marker_literally` below
# pins that, because a comment is a claim like any other.
_ARRAY_MARKER = "DEVRC_GIT_REPO_POINTERS" + "=("


def _could_hold_a_shell_array(path: Path) -> bool:
    """SCOPE ONLY — which files could carry a bash array at all.

    `.sh`, or extensionless (the `githooks/` hooks). This is deliberately NOT
    doing double duty as self-exclusion; see `_ARRAY_MARKER`.
    """
    return path.suffix in (".sh", "")


def _sweep_candidates() -> list[Path]:
    """The files the clearer sweep looks at, filtered the way this repo's other
    `repo_files` consumers filter theirs.

    🔴 `_is_skipped` is RE-APPLIED after `repo_files`, for the reason
    `captured_text_scan._candidates` and `public_ip_scan.scan_repo` both spell
    out: `repo_files` only filters on the FILESYSTEM-WALK tier, so on the
    `git ls-files` tier a tracked path under a skip dir comes back. MEASURED
    zero such paths today — this closes a DIVERGENCE between the two tiers, not
    a live miss, and a divergence here would surface as a red in one tier only,
    which is the shape this whole PR keeps being about.

    ⚠ MEASURED, and stated because the honest version is less flattering:
    deleting the `_is_skipped` call kills NO test. There are no tracked files
    under a `SKIP_DIRS` name today, so nothing can observe its absence. It is
    defence-in-depth and consistency with the two established `repo_files`
    consumers — NOT coverage, and it must not be counted as any. The same
    labelling as the "backup is readable" assertions in
    `test_analyze_service_index_commit.py`.
    """
    return [p for p in repo_files(ROOT)
            if _could_hold_a_shell_array(p) and not _is_skipped(p, ROOT)]


def test_POINTER_CLEARERS_is_every_file_that_declares_the_array():
    """🔴 THE FILE LIST IS DERIVED, NOT TRUSTED — both directions.

    `POINTER_CLEARERS` is hand-maintained. Every pin above is parametrised over
    it, so a SIXTH file that grows the array is silently unpinned: its copy could
    drift from the owner, or spell `unset` wrong, and nothing here would notice.
    That is the "a count of DECLARATIONS is not a count of INSTANCES" shape.

    So sweep the tree and require the sets to be EQUAL. Growing fails (a new
    clearer must be pinned); shrinking fails too (a clearer that lost its array
    must be removed here deliberately, not discovered later).

    🔴 THE SWEEP MUST WORK IN BOTH TIERS, and the first version did not. It ran a
    bare `git ls-files` and asserted rc 0 — but `flake.nix` builds
    `checks.pytests` from `cp -r ${./.} src`, which has NO `.git`, so the
    authoritative hermetic tier died `fatal: not a git repository … assert 128
    == 0`. The dev-host `--set all` run could not see it: RULES.md's two-tiers
    rule, exactly as written.

    The repo already owns the fix and it is an IMPORT, not a new helper:
    `testlib.public_ip_scan.repo_files` prefers `git ls-files` and falls back to
    a filesystem walk in the sandbox. `captured_text_scan.py` carries a "ONE
    RULE, ONE PLACE — a third copy of it would be a third place for that trap to
    come back" banner over the same helper; a hand-rolled fallback here would
    have been that third copy.
    """
    names = repo_files(ROOT)
    # A fallback that silently returned nothing would make `found == declared`
    # true by vacuum — the empty-result-reads-as-clean failure this whole PR is
    # about. Floor first, agreement second.
    assert len(names) > 100, (
        f"the repo-file sweep found only {len(names)} files under {ROOT} — it is "
        "not walking the repo, so the agreement below would be vacuous")
    shell = _sweep_candidates()
    assert len(shell) > 10, (
        f"the sweep saw {len(shell)} shell files — the walk is not reaching "
        "`scripts/` or `githooks/`, where every clearer lives")

    found: set[Path] = set()
    for path in shell:
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if _ARRAY_MARKER in text:
            found.add(path)

    declared = set(POINTER_CLEARERS)
    assert found == declared, (
        "POINTER_CLEARERS disagrees with the files that actually declare the "
        "array:\n"
        f"  declares it but NOT pinned: {sorted(str(p.relative_to(ROOT)) for p in found - declared)}\n"
        f"  pinned but does NOT declare it: {sorted(str(p.relative_to(ROOT)) for p in declared - found)}\n"
        "Add or remove it here in the same commit — every ledger pin above is "
        "parametrised over this tuple, so an unlisted clearer is an unchecked one."
    )


def test_this_file_never_spells_the_marker_literally():
    """🔴 SELF-EXCLUSION MUST STAY A PROPERTY OF THE MARKER, not of the filter.

    `_could_hold_a_shell_array` currently admits only `.sh` and extensionless
    files, so this `.py` module is out of scope anyway — today. The moment
    someone widens that filter (to catch a clearer in a `.bash` file, say), any
    literal occurrence of the array-opening string in THIS file makes the sweep
    match itself and go red for a reason unrelated to the guard.

    Measured: the first draft of the comment above defeated the assembly by
    quoting the very string it was explaining.
    """
    src = Path(__file__).read_text(encoding="utf-8")
    assert src.count(_ARRAY_MARKER) == 0, (
        f"this file spells the array marker literally {src.count(_ARRAY_MARKER)} "
        "time(s). Assemble it, or describe it in prose without quoting it — "
        "otherwise widening `_could_hold_a_shell_array` makes the sweep match "
        "itself.")


def test_the_clearer_sweep_can_actually_find_one():
    """🔴 POSITIVE CONTROL for the sweep, and it must hold IN BOTH TIERS.

    `found == declared` is also what a walker that matched NOTHING produces
    against an empty `POINTER_CLEARERS`, and a typo'd marker would make the
    sweep return an empty set that silently agreed with nothing.

    Everything below is filesystem-only — no `git` — so it measures the same
    thing inside the nix sandbox, where the sweep now takes its fallback path.
    """
    # 1. the marker really is in a real clearer, read off disk
    text = (SCRIPTS / "run-tests.sh").read_text(encoding="utf-8")
    assert _ARRAY_MARKER in text, (
        "the marker the sweep greps for is not in run-tests.sh — the sweep is "
        "looking for a string that no longer exists, so it can only ever "
        "return an empty set")

    # 2. the file-list source itself is non-empty and reaches shell files
    names = repo_files(ROOT)
    assert len(names) > 100, f"repo_files({ROOT}) returned {len(names)} files"
    assert any(_could_hold_a_shell_array(p) and p.name == "run-tests.sh"
               for p in names), (
        "repo_files did not return run-tests.sh, so the sweep cannot have "
        "matched it — in this tier the sweep is wired to nothing")

    # 3. and the scope filter admits every shape a clearer takes: a `.sh` file
    #    and an extensionless githooks hook.
    assert _could_hold_a_shell_array(Path("x/run-tests.sh"))
    assert _could_hold_a_shell_array(Path("githooks/tests-on-push"))
    assert not _could_hold_a_shell_array(Path("x/test_thing.py"))


def _root_line(text: str) -> int:
    """The line that ASSIGNS a repo root from `rev-parse --show-toplevel`.

    🔴 Comment lines are skipped, and an assignment (`…ROOT=`) is required on
    the line. The first version matched any mention of the phrase and therefore
    matched GUARD 9's own explanatory COMMENT — which sits above the assignment,
    so all runners reported "clears AFTER ROOT" while being correctly ordered.
    A parser that cannot tell code from prose is measuring the wrong thing.
    """
    for i, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        if "ROOT=" in line and "rev-parse" in line and "--show-toplevel" in line:
            return i
    return -1


def _clear_line(text: str) -> int:
    # ONE implementation of "is the unset live?", shared with the ledger pin —
    # they disagreed once (exact here, substring there) and the weaker one was
    # the only thing covering `commit.sh`.
    return _unset_line_number(text, "DEVRC_GIT_REPO_POINTERS")


@pytest.mark.parametrize("runner", RUNNERS, ids=lambda p: p.name)
def test_every_runner_clears_BEFORE_it_resolves_its_root(runner):
    """🔴 ORDER, not merely presence — and this is a MEASURED requirement, not a
    tidiness preference.

    All four entry points resolve a root with
    `git … rev-parse --show-toplevel`. With GIT_DIR set and no GIT_WORK_TREE,
    git takes the CWD as the top of the work tree, so that returns
    `<repo>/scripts`; the runner then looks for
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
        f"resolves its root at line {root}. An inherited GIT_DIR then makes that "
        f"root `<repo>/scripts` and the run dies exit 127 with no verdict."
    )


def test_every_pytest_target_gets_the_detector():
    """`-p testlib.gitenv_plugin` must sit on the ONE pytest line, beside GUARD
    7's and GUARD 8's. `scripts/run-tests.sh` runs one pytest process per target
    and there are two dozen of them; a plugin registered from a conftest reaches
    one directory (that is #399's and #614's measured failure, twice).

    🔴 The target COUNT is deliberately not written down here. #683 said
    "seventeen" while `--check-targets` listed 25 at the time of the audit and
    26 today — a number nobody re-derives is a claim that rots, and the
    assertion below never depended on it.
    """
    text = re.sub(r"\\\n\s*", " ", RUN_TESTS.read_text(encoding="utf-8"))
    pytest_lines = [ln for ln in text.splitlines()
                    if "python -m pytest" in ln and "$d" in ln]
    assert pytest_lines, "could not find the per-target pytest invocation in run-tests.sh"
    for ln in pytest_lines:
        assert "-p testlib.gitenv_plugin" in ln, (
            "a per-target pytest invocation runs without GUARD 9:\n"
            f"  {ln.strip()}"
        )


# --------------------------------------------------------------------------- #
# 1b. THE SECOND ENTRY POINT — a LEDGER of conftests, not one example
# --------------------------------------------------------------------------- #
# 🔴 #683 wired ONE of seven, and not the one `gitenv_plugin`'s own rationale
# cites: `scripts/claude-hooks/tests/` holds `test_bash_guard.py::_mkrepo` and
# `test_guard_core.py`'s module-scoped repos, which build real git repositories
# during COLLECTION. Pinning one example is a guard that cannot notice the other
# six, or the eighth. claude/RULES.md → "an asserted ledger of every writer,
# failing when the set GROWS *or* SHRINKS".
_PINNED_CONFTESTS = (
    "scripts/browser-bridge/tests/conftest.py",
    "scripts/claude-hooks/tests/conftest.py",
    "scripts/dl-router/tests/conftest.py",
    "scripts/repo-cos/tests/conftest.py",
    "scripts/session-analysis/session_insight/tests/conftest.py",
    "scripts/signal/tests/conftest.py",
    "scripts/tests/conftest.py",
)

# What pytest actually acts on. A conftest that re-exports these IS a second
# entry point; one that merely mentions the module's name is not.
_ENTRY_POINT_ATTRS = ("_devrc_git_repo_isolation", "pytest_collection_finish",
                      "pytest_configure", "pytest_runtest_logstart",
                      "pytest_sessionfinish")


def _discovered_conftests() -> tuple[str, ...]:
    return tuple(sorted(p.relative_to(ROOT).as_posix()
                        for p in SCRIPTS.rglob("conftest.py")
                        if "__pycache__" not in p.parts))


def test_the_conftest_entry_points_are_a_pinned_ledger():
    """🔴 FAILS WHEN THE SET GROWS *OR* SHRINKS. A new test directory arrives
    with a new conftest; without this, it silently joins the population of
    directories a bare `pytest <dir>` can poison."""
    found = _discovered_conftests()
    assert found == _PINNED_CONFTESTS, (
        "the set of conftests under scripts/ changed.\n"
        f"  new     : {sorted(set(found) - set(_PINNED_CONFTESTS))}\n"
        f"  removed : {sorted(set(_PINNED_CONFTESTS) - set(found))}\n"
        "Add GUARD 9's second-entry-point block to any new one (copy it from "
        "scripts/tests/conftest.py) and update _PINNED_CONFTESTS in the same "
        "commit."
    )


@pytest.mark.parametrize("rel", _PINNED_CONFTESTS)
def test_every_conftest_is_a_second_entry_point(rel):
    """A bare `pytest <dir>` outside the runner must get the same guard — by the
    same module, not a second copy of the logic.

    🔴 STRUCTURAL, NOT SPELLED. The first version of this test grepped the
    conftest for the strings `testlib.gitenv_plugin` and
    `_devrc_git_repo_isolation`, and a mutant that moved the whole import under
    `if False:` SURVIVED it: both words were still on the page. What pytest acts
    on is the module OBJECT's attributes, so that is what is asserted — and the
    objects must be the plugin's own, not same-named look-alikes.
    """
    import importlib.util

    path = ROOT / rel
    spec = importlib.util.spec_from_file_location(
        f"_devrc_conftest_probe_{abs(hash(rel))}", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    for attr in _ENTRY_POINT_ATTRS:
        got = getattr(module, attr, None)
        assert got is getattr(gitenv_plugin, attr), (
            f"{rel} does not re-export `{attr}` from testlib.gitenv_plugin, so "
            f"a bare `pytest {Path(rel).parent}` runs without that half of "
            f"GUARD 9 (found: {got!r})"
        )


def test_pytest_configure_IS_called_for_a_collected_directorys_conftest(tmp_path):
    """🔴 THE CLAIM THIS REPLACES WAS FALSE AND COST REAL COVERAGE.

    `scripts/tests/conftest.py` carried a comment asserting pytest does not call
    `pytest_configure` for a conftest loaded during collection, and therefore
    deliberately did NOT re-export it — which cost the second entry point the
    session marker, i.e. the run's only evidence the guard loaded at all.
    Measured here rather than asserted: it IS called.
    """
    d = tmp_path / "probe"
    d.mkdir()
    (d / "conftest.py").write_text(
        'def pytest_configure(config):\n'
        '    print("CONFTEST_CONFIGURE_RAN")\n', encoding="utf-8")
    (d / "test_probe.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", str(d), "-q", "-s", "-p", "no:cacheprovider",
         "--no-header"],
        capture_output=True, text=True, env=_env(), cwd=str(d))
    assert "CONFTEST_CONFIGURE_RAN" in proc.stdout, (
        "pytest_configure was NOT called for a collected directory's conftest — "
        "the comment this test replaces would then have been right, and "
        f"scripts/tests/conftest.py must stop importing it.\n{proc.stdout}")


# --------------------------------------------------------------------------- #
# 2. NO SHELL SCRIPT MAY HAND A REPO POINTER TO THE RUNNER
# --------------------------------------------------------------------------- #
# `githooks/pre-push` did exactly this: `GIT_DIR="$(git rev-parse --git-dir)"`.
# In bash, assigning to an ALREADY-EXPORTED name keeps it exported, so that line
# hands this repository's git dir to `tests-on-push.sh` -> `run-tests.sh` ->
# pytest whenever a caller has GIT_DIR in the environment.
#
# 🔴 THE SCAN CLAIMED MORE THAN IT DID, BOTH WAYS (finding E of #683's audit).
# It walked `*.sh` plus extensionless files whose PARENT was literally named
# `githooks`, so it missed twelve extensionless bash scripts under `scripts/`,
# everything under `nix/` — where an `envExtra`/`shellHook` export would poison
# every shell this operator opens, the likeliest real source of all — and
# `githooks/<subdir>/*`. Its regex missed `declare -x`, `typeset -x`,
# `readonly`, a second statement on a line (`foo; GIT_DIR=…`), and
# `export GIT_DIR` with NO `=`, which is precisely the already-exported-name
# mechanism the whole rationale rests on. And it FALSE-POSITIVED on the harmless
# `GIT_INDEX_FILE=$t git add …` command-prefix idiom, which does not export
# anything.
_SHELL_ROOTS = ("githooks", "scripts", "nix")
_SHELL_SUFFIXES = {".sh", ".bash", ".zsh", ".nix"}
_SKIP_DIRS = {".git", "node_modules", "__pycache__", "result", "dist", "build",
              ".direnv", ".pytest_cache"}
# 🔴 Spelled from character codes, not as a literal. `testlib/shebang_scan.py`
# treats a quote followed by those two bytes as a test writing its own
# shebang, and it is right to — it cannot tell a WRITE from a READ, and the
# false negative it exists to prevent cost two days of red sandbox. Same
# spelling trick that module uses on itself.
_HASHBANG = (chr(35) + chr(33)).encode()
_SHEBANG = re.compile(rb"\b(ba|z|da|k)?sh\b")

_KEYWORDS = re.compile(r"^(?:export|readonly|local|declare|typeset)(?:\s+-\w+)*\s+")
_POINTER_NAMES = "|".join(REPO_POINTER_VARS)
_ASSIGN = re.compile(r"^(" + _POINTER_NAMES + r")=")
_BARE = re.compile(r"^(" + _POINTER_NAMES + r")(?:\[[^\]]*\])?$")
# For `.nix`, where the shell lives inside a nix string literal on one line
# (`envExtra = "export GIT_DIR=/x";`) the shell tokeniser sees one quoted blob.
# An EXPORT keyword is the only shape that can poison a child from there — a
# bare assignment in `envExtra` writes an unexported `.zshenv` line — so this is
# a narrow raw-text pattern, not a second general parser.
_NIX_EXPORT = re.compile(
    r"(?:export|declare\s+-x|typeset\s+-x)\s+(" + _POINTER_NAMES + r")\b")


def _shell_files() -> list[Path]:
    out: list[Path] = []
    for rel in _SHELL_ROOTS:
        base = ROOT / rel
        if not base.is_dir():
            continue
        for p in sorted(base.rglob("*")):
            if any(part in _SKIP_DIRS for part in p.relative_to(ROOT).parts):
                continue
            if p.is_symlink() or not p.is_file():
                continue
            if p.suffix in _SHELL_SUFFIXES:
                out.append(p)
                continue
            if p.suffix == "":
                try:
                    head = p.open("rb").readline(200)
                except OSError:
                    continue
                if head.startswith(_HASHBANG) and _SHEBANG.search(head):
                    out.append(p)
    return out


def _strip_comment(line: str) -> str:
    """Drop a trailing `#` comment, respecting quotes. Cutting too eagerly can
    only cause a MISS, never a false positive, so ambiguity is resolved toward
    keeping text."""
    out: list[str] = []
    quote: str | None = None
    i = 0
    while i < len(line):
        c = line[i]
        if quote:
            if c == quote:
                quote = None
            elif c == "\\" and quote == '"':
                out.append(c)
                i += 1
                if i < len(line):
                    out.append(line[i])
                    i += 1
                continue
        elif c in "'\"":
            quote = c
        elif c == "#" and (not out or out[-1].isspace()):
            break
        out.append(c)
        i += 1
    return "".join(out)


def _statements(line: str) -> list[str]:
    """Split a line into shell statements on unquoted `;`, `&&`, `||`, `|`."""
    parts: list[str] = []
    buf: list[str] = []
    quote: str | None = None
    depth = 0
    i = 0
    while i < len(line):
        c = line[i]
        if quote:
            buf.append(c)
            if c == quote:
                quote = None
            i += 1
            continue
        if c in "'\"":
            quote = c
            buf.append(c)
            i += 1
            continue
        if line.startswith("$(", i) or line.startswith("${", i):
            depth += 1
            buf.append(line[i:i + 2])
            i += 2
            continue
        if c in ")}" and depth:
            depth -= 1
            buf.append(c)
            i += 1
            continue
        if depth == 0 and c in ";&|":
            parts.append("".join(buf))
            buf = []
            while i < len(line) and line[i] in ";&|":
                i += 1
            continue
        buf.append(c)
        i += 1
    parts.append("".join(buf))

    out: list[str] = []
    for part in parts:
        part = part.strip()
        for kw in ("then ", "do ", "else ", "{ ", "( "):
            while part.startswith(kw):
                part = part[len(kw):].strip()
        if part:
            out.append(part)
    return out


def _is_a_command_prefix(rest: str) -> bool:
    """`NAME=value cmd …` scopes the assignment to that ONE command.

    It does not export anything and cannot reach the runner, so flagging it was
    a false positive — and this idiom is used deliberately in this repo
    (`GIT_INDEX_FILE=$t git add …`).
    """
    quote: str | None = None
    depth = 0
    i = 0
    while i < len(rest):
        c = rest[i]
        if quote:
            if c == quote:
                quote = None
            i += 1
            continue
        if c in "'\"":
            quote = c
            i += 1
            continue
        if rest.startswith("$(", i) or rest.startswith("${", i):
            depth += 1
            i += 2
            continue
        if c in ")}" and depth:
            depth -= 1
            i += 1
            continue
        if depth == 0 and c.isspace():
            return rest[i:].strip() != ""
        i += 1
    return False


def pointer_assignments(text: str, *, nix: bool = False) -> list[tuple[int, str, str]]:
    """`(line, variable, statement)` for every shape that EXPORTS a repo pointer."""
    hits: list[tuple[int, str, str]] = []
    for lineno, raw in enumerate(text.splitlines(), 1):
        line = _strip_comment(raw)
        if not line.strip():
            continue
        for stmt in _statements(line):
            keyword = _KEYWORDS.match(stmt)
            body = stmt[keyword.end():].strip() if keyword else stmt
            assign = _ASSIGN.match(body)
            if assign:
                if keyword is None and _is_a_command_prefix(body[assign.end():]):
                    continue
                hits.append((lineno, assign.group(1), stmt))
                continue
            first = body.split()[0] if body.split() else ""
            if keyword and _BARE.match(first):
                hits.append((lineno, first, stmt))
        if nix:
            for m in _NIX_EXPORT.finditer(line):
                if not any(h[0] == lineno and h[1] == m.group(1) for h in hits):
                    hits.append((lineno, m.group(1), line.strip()))
    return hits


def test_the_shell_scan_sees_files_at_all():
    """The positive control for the walker. A zero from a walker that matched
    nothing is indistinguishable from a clean repo — and #683's walker really
    did miss whole classes it claimed to cover."""
    files = _shell_files()
    rels = {p.relative_to(ROOT).as_posix() for p in files}
    assert len(files) >= 50, f"the shell-script walk found only {len(files)} files"
    assert "githooks/pre-push" in rels, (
        "githooks/pre-push is not in the scanned set — it is the file this "
        "check exists for.")
    assert "githooks/tests-on-push.sh" in rels
    assert any(r.startswith("nix/") for r in rels), (
        "nothing under nix/ is scanned. An `envExtra`/`shellHook` export lands "
        "in every shell the operator opens, which makes it the likeliest real "
        "source of an ambient GIT_DIR — and #683's scan could not see it.")
    extensionless = [r for r in rels if "." not in Path(r).name]
    assert len(extensionless) >= 5, (
        "the walk found almost no extensionless scripts; #683's version keyed "
        f"on the parent directory's NAME and missed twelve of them: {sorted(extensionless)}")


@pytest.mark.parametrize("snippet", [
    'GIT_DIR="$(git rev-parse --git-dir)"',
    'export GIT_DIR=/x',
    'export GIT_DIR',                        # 🔴 no `=`: THE mechanism
    '  export   GIT_COMMON_DIR',
    'declare -x GIT_DIR=/x',
    'typeset -x GIT_WORK_TREE=/x',
    'readonly GIT_INDEX_FILE=/x',
    'local GIT_NAMESPACE=/x',
    'foo; GIT_DIR=/x',
    'cd /elsewhere && GIT_DIR=/x',
    'if true; then GIT_DIR=/x; fi',
])
def test_the_pointer_scan_catches_every_export_shape(snippet):
    """🔴 THE NEGATIVE CONTROL FOR THE SCANNER ITSELF (claude/RULES.md →
    "validate the INSTRUMENT before you read its verdict"). Every one of these
    was invisible to #683's regex, which anchored on `^[ \\t]*(export )?NAME=`.
    A scanner that cannot go red on the mechanism its own rationale names is
    reporting a fact about the scanner."""
    assert pointer_assignments(snippet), f"the scan did not flag: {snippet!r}"


@pytest.mark.parametrize("snippet", [
    'GIT_INDEX_FILE="$t" git add -- f.txt',  # command-scoped prefix: harmless
    'GIT_INDEX_FILE=$t git add x',
    '# GIT_DIR="$(git rev-parse --git-dir)"',
    '  GIT_DIR                            # inside an array literal',
    'unset "${DEVRC_GIT_REPO_POINTERS[@]}"',
    'echo "${GIT_DIR:-none}"',
    'HOOK_GIT_DIR="$(git rev-parse --git-dir)"',
    'GIT_DIR_SOMETHING=/x',
    'GIT_CONFIG_GLOBAL=/x',                  # deliberately NOT a repo pointer
])
def test_the_pointer_scan_does_not_flag_harmless_shapes(snippet):
    """The other half of the pair. A scanner that flags the command-prefix
    idiom is a permanently-red gate over code that is correct — and this repo
    uses that idiom on purpose."""
    assert not pointer_assignments(snippet), (
        f"the scan false-positived on: {snippet!r} -> {pointer_assignments(snippet)}")


def test_the_nix_scan_catches_an_export_inside_a_one_line_nix_string():
    """`programs.zsh.envExtra = "export GIT_DIR=…";` is shell inside a nix
    string literal, on one line. The general shell tokeniser sees one quoted
    blob, so `.nix` gets one extra narrow pattern."""
    src = '  programs.zsh.envExtra = "export GIT_DIR=/x";\n'
    assert not pointer_assignments(src), "sanity: the shell parser alone cannot see it"
    assert pointer_assignments(src, nix=True), "the nix pattern missed it"


def test_no_shell_script_that_can_reach_the_runner_assigns_a_git_repo_pointer():
    offenders: list[str] = []
    for path in _shell_files():
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for lineno, name, stmt in pointer_assignments(text, nix=path.suffix == ".nix"):
            offenders.append(f"{path.relative_to(ROOT)}:{lineno}: {name}  ({stmt[:80]})")
    assert not offenders, (
        "a shell script assigns to (or exports) a git repo-pointer variable:\n  "
        + "\n  ".join(offenders)
        + "\n\nIn bash an assignment to an already-exported name STAYS exported, "
          "and `export NAME` with no `=` exports whatever it already holds — so "
          "this hands a repository to every child process, including the test "
          "runner, whose fixtures then build themselves inside it. Rename the "
          "variable (githooks/pre-push uses HOOK_GIT_DIR)."
    )


def test_git_push_does_not_export_GIT_DIR_to_pre_push(tmp_path):
    """🔴 THE CORRECTION, MEASURED, AS A PAIR — #683's body and commit message
    both stated that `githooks/pre-push` handing down `GIT_DIR` explained why
    *pushing* triggered the corruption, and that claim was relayed to another
    session as established fact before it was checked.

    A `git push` from a GIT_DIR-free parent gives `pre-push` only
    `GIT_EXEC_PATH` and `GIT_PREFIX` (plus the caller's own identity/config
    vars). The rename remains correct hygiene — an outer caller's exported
    `GIT_DIR` really does survive a reassignment — but it is NOT the diagnosis,
    and the root cause of the incident is still unknown.
    """
    home = tmp_path / "home"
    home.mkdir()
    gitconfig = home / "gitconfig"
    gitconfig.write_text("[user]\n\tname = g9\n\temail = g9@example.invalid\n",
                         encoding="utf-8")
    base = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    base.update(_GIT_ENV)
    base["GIT_CONFIG_GLOBAL"] = str(gitconfig)

    bare = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True,
                   capture_output=True, env=base)
    work = tmp_path / "work"
    subprocess.run(["git", "init", "-q", "-b", "main", str(work)], check=True,
                   capture_output=True, env=base)
    (work / "f.txt").write_text("x\n", encoding="utf-8")
    assert _git(work, "add", "f.txt", env=base).returncode == 0
    assert _git(work, "commit", "-qm", "c1", env=base).returncode == 0
    assert _git(work, "remote", "add", "origin", str(bare), env=base).returncode == 0

    seen = tmp_path / "hookenv.txt"
    # 🔴 `mockbin.write_exec` OWNS THE SHEBANG. Writing one here would give
    # the hook `/usr/bin/env`, which `test_runtime_shebangs.py` fails on — a
    # guard measured to matter: two files that wrote their own put 27 tests
    # red in the nix sandbox for two days. The body is POSIX sh.
    hook = mockbin.write_exec(
        work / ".git" / "hooks" / "pre-push",
        f"env | grep -E '^GIT' | sort > {seen}\n"
        "cat >/dev/null\nexit 0\n")

    assert _git(work, "push", "-q", "origin", "main", env=base).returncode == 0
    names = {ln.split("=", 1)[0] for ln in seen.read_text(encoding="utf-8").splitlines()}
    assert "GIT_EXEC_PATH" in names, f"the hook did not run as expected: {names}"
    assert "GIT_DIR" not in names, (
        "`git push` DID export GIT_DIR to pre-push on this git version — the "
        "correction in gitenv.py's header and githooks/pre-push is now wrong "
        f"and must be rewritten.\nsaw: {sorted(names)}")

    # The other half: an outer caller's export DOES survive, which is what makes
    # the rename worth keeping.
    poisoned = dict(base)
    poisoned["GIT_DIR"] = str(work / ".git")
    (work / "f.txt").write_text("y\n", encoding="utf-8")
    assert _git(work, "add", "f.txt", env=base).returncode == 0
    assert _git(work, "commit", "-qm", "c2", env=base).returncode == 0
    assert _git(work, "push", "-q", "origin", "main", env=poisoned).returncode == 0
    names = {ln.split("=", 1)[0] for ln in seen.read_text(encoding="utf-8").splitlines()}
    assert "GIT_DIR" in names, (
        "an exported GIT_DIR did NOT reach pre-push, so the rename protects "
        f"nothing and should be reconsidered.\nsaw: {sorted(names)}")


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
# 3b. 🔴 THE SEAMS REFUSE A VALUE THEY CANNOT HONOUR (finding B)
# --------------------------------------------------------------------------- #
def test_an_unset_protect_seam_means_discovery():
    assert resolve_protect_env({}) is None


def test_a_real_git_dir_on_the_protect_seam_is_honoured(tmp_path):
    repo = _mkrepo(tmp_path / "repo")
    got = resolve_protect_env({PROTECT_ENV: str(repo / ".git")})
    assert got == [(repo / ".git").resolve()]


@pytest.mark.parametrize("value,because", [
    (":", "splits to NOTHING — measured `protected-git-dirs=0` and a GREEN run "
          "over a real escape"),
    ("", "set but empty"),
    ("   ", "whitespace only"),
])
def test_a_protect_seam_that_names_no_path_is_a_LOUD_failure(value, because):
    with pytest.raises(GitEnvConfigError) as excinfo:
        resolve_protect_env({PROTECT_ENV: value})
    assert PROTECT_ENV in str(excinfo.value), because


def test_a_protect_seam_naming_an_unresolvable_path_is_a_LOUD_failure(tmp_path):
    """🔴 THE WORST OF THE THREE. On #683's code this produced
    `protected-git-dirs=1` — a marker line asserting healthy coverage — while
    the detector watched a path that cannot hold a repository and the escaping
    test really created its branch. A guard is allowed to fail; it is not
    allowed to report coverage it does not have."""
    for bad in (str(tmp_path / "nonexistent" / "x"), str(tmp_path)):
        with pytest.raises(GitEnvConfigError) as excinfo:
            resolve_protect_env({PROTECT_ENV: bad})
        assert "not a git dir" in str(excinfo.value) or "cannot resolve" in str(excinfo.value)


def test_a_protect_seam_with_one_good_and_one_bad_path_still_fails(tmp_path):
    """Partial credit is how a detector ends up watching half of what its marker
    line claims."""
    repo = _mkrepo(tmp_path / "repo")
    with pytest.raises(GitEnvConfigError):
        resolve_protect_env(
            {PROTECT_ENV: os.pathsep.join([str(repo / ".git"), "/nonexistent/x"])})


@pytest.mark.parametrize("value", ["enfore", "ENFORCE ", "1", "yes", "off"])
def test_an_unknown_mode_is_a_LOUD_failure(value):
    with pytest.raises(GitEnvConfigError):
        requested_mode({MODE_ENV: value})


def test_the_known_modes_round_trip():
    assert requested_mode({}) == "auto"
    for mode in ("auto", "enforce", "report"):
        assert requested_mode({MODE_ENV: mode}) == mode


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
    # 🔴 A REAL HEAD-MOVE ROW. #683's docstring claimed one ("HEAD moves … lands
    # in one of these") and every row was a ref-create or a config write; the
    # incident repointed HEAD at `trunk`, and `symbolic-ref` is that, with no
    # ref of its own moving.
    (("symbolic-ref", "HEAD", "refs/heads/other"), "HEAD"),
])
def test_the_detector_moves_for_every_damage_class_from_the_incident(
        tmp_path, mutation, expect):
    """🔴 REPORTED BESIDE THE NO-OP CONTROL ABOVE. Each row is a shape that was
    actually found in the operator's clone: a branch create, a HEAD repoint, a
    `user.name`, a `core.hooksPath`, a rewritten remote URL."""
    repo = _mkrepo(tmp_path / "repo")
    assert _git(repo, "branch", "-q", "other").returncode == 0
    git_dir = resolve_git_dir(repo)
    before = snapshot([git_dir])
    assert _git(repo, *mutation).returncode == 0
    deltas = diff_snapshots(before, snapshot([git_dir]))
    assert deltas, f"the detector did not see `git {' '.join(mutation)}`"
    assert any(expect in d for d in deltas), (
        f"expected a delta naming {expect!r}, got:\n" + "\n".join(deltas))


# --------------------------------------------------------------------------- #
# 4b. 🔴 EVERY FINGERPRINT COMPONENT IS LOAD-BEARING (finding C)
# --------------------------------------------------------------------------- #
# #683 mutation-swept the PLUMBING and not the CONTENT, and five semantic
# mutants survived a fully green suite: dropping `HEAD`, dropping `packed-refs`,
# dropping `ORIG_HEAD`+`logs/HEAD`, and reducing `starts` to either root alone.
# "The detector noticed something" is satisfied by ANY surviving component, so
# each row below is a mutation that ONLY the named component can see — measured
# with `git` itself, not asserted.
_COMPONENT_ROWS = (
    # (component, setup, mutation, must-appear-in-a-delta)
    ("config", (), ("config", "user.name", "T"), "config"),
    ("HEAD", (("branch", "-q", "other"),), ("symbolic-ref", "HEAD", "refs/heads/other"), "HEAD"),
    ("ORIG_HEAD", (), ("reset", "-q", "HEAD"), "ORIG_HEAD"),
    ("refs (loose)", (), ("branch", "-q", "side"), "refs/heads/side"),
    # 🔴 THE INCIDENT'S WORST ACT, on a repo whose refs are PACKED — which is
    # every repo `git gc` has touched. #683 never packed refs in any fixture, so
    # deleting `packed-refs` from the fingerprint survived while
    # `DELETED refs/heads/main` is exactly what happened.
    ("refs (packed)", (("branch", "-q", "doomed"), ("pack-refs", "--all")),
     ("branch", "-q", "-D", "doomed"), "refs/heads/doomed"),
)


@pytest.mark.parametrize("component,setup,mutation,expect",
                         _COMPONENT_ROWS, ids=[r[0] for r in _COMPONENT_ROWS])
def test_every_fingerprint_component_is_load_bearing(
        tmp_path, component, setup, mutation, expect):
    """🔴 ISOLATED: the delta must name THIS component and nothing else, so the
    row dies when this component is dropped and cannot be kept alive by a
    neighbour (claude/RULES.md → "a mutant that dies for the wrong reason proves
    nothing about the guard")."""
    repo = _mkrepo(tmp_path / "repo")
    for cmd in setup:
        assert _git(repo, *cmd).returncode == 0, cmd
    git_dir = resolve_git_dir(repo)
    before = snapshot([git_dir])
    assert _git(repo, *mutation).returncode == 0, mutation
    deltas = diff_snapshots(before, snapshot([git_dir]))
    assert deltas, (
        f"nothing in the fingerprint moved for `git {' '.join(mutation)}` — the "
        f"{component} component is not being read at all")
    assert all(expect in d for d in deltas), (
        f"the {component} row is not isolated: some delta does not name "
        f"{expect!r}, so dropping {component} could still leave this test "
        "green.\n" + "\n".join(deltas))


def test_repacking_refs_is_NOT_reported_as_damage(tmp_path):
    """🔴 THE FALSE-POSITIVE CLASS THAT MADE THIS A PERMANENTLY-RED GATE.

    `git gc` / `git pack-refs` move every loose ref into `packed-refs`. The
    repository's CONTENT is unchanged; #683's fingerprint hashed the loose ref
    FILES, so it emitted one `DELETED refs/…` line per branch — on the
    operator's clone, hundreds — under a banner announcing the incident had
    recurred. `drift-check.sh` runs `git fetch` against that clone on a 6-hourly
    systemd timer and its own comment records that `fetch` triggers `gc --auto`,
    so this fired on a schedule.
    """
    repo = _mkrepo(tmp_path / "repo")
    for name in ("a", "b", "c", "d", "e"):
        assert _git(repo, "branch", "-q", name).returncode == 0
    git_dir = resolve_git_dir(repo)
    before = snapshot([git_dir])
    assert _git(repo, "pack-refs", "--all").returncode == 0
    assert (git_dir / "packed-refs").is_file(), "the fixture did not actually pack"
    assert not (git_dir / "refs" / "heads" / "a").exists(), "loose ref survived the pack"
    assert diff_snapshots(before, snapshot([git_dir])) == [], (
        "packing refs was reported as damage — the fingerprint is reading ref "
        "FILES rather than ref VALUES")
    # …and the values really were read, not merely skipped.
    assert ref_values(git_dir)["refs/heads/a"], "packed refs are invisible to ref_values"


def test_gc_is_not_reported_as_damage(tmp_path):
    """The same claim end-to-end, through the command that actually runs on the
    timer."""
    repo = _mkrepo(tmp_path / "repo")
    for name in ("a", "b", "c"):
        assert _git(repo, "branch", "-q", name).returncode == 0
    git_dir = resolve_git_dir(repo)
    before = snapshot([git_dir])
    assert _git(repo, "gc", "-q", "--prune=now").returncode == 0
    assert diff_snapshots(before, snapshot([git_dir])) == [], (
        "`git gc` was reported as damage")


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
# 4c. 🔴 BOTH DISCOVERY ROOTS ARE LOAD-BEARING (finding C)
# --------------------------------------------------------------------------- #
# `protected_git_dirs` starts from `[Path.cwd(), Path(__file__).parent]` under a
# comment reading "both are needed". That was a necessity claim with zero
# coverage: reducing the list to EITHER root alone survived the whole suite.
def test_the_cwd_root_is_load_bearing(tmp_path, monkeypatch):
    """Kills the `starts = [Path(__file__)]` mutant. A runner invoked from a
    DIFFERENT repository than the one holding gitenv.py — a nested checkout, a
    worktree, `pytest` run from anywhere — is protected only by the cwd root."""
    monkeypatch.delenv(PROTECT_ENV, raising=False)
    other = _mkrepo(tmp_path / "elsewhere")
    monkeypatch.chdir(other)
    dirs = protected_git_dirs()
    assert (other / ".git").resolve() in dirs, (
        "the repository the suite is RUNNING IN was not discovered — dropping "
        f"Path.cwd() from `starts` would be invisible.\nfound: {dirs}")


def test_the_module_root_is_load_bearing(tmp_path, monkeypatch):
    """Kills the `starts = [Path.cwd()]` mutant. Every nested control in this
    file runs pytest with `cwd` in a tmp dir that is not a repository at all; so
    does any `pytest` launched from `/tmp`. The module root is what protects a
    repo then.

    🔴 This must NOT depend on THIS checkout having a `.git`. It used to assert
    exactly that (`gitenv.py is not inside a git checkout`) and the assertion
    fired — not as a bug in the guard, but because the AUTHORITATIVE runner is
    `nix build .#checks.x86_64-linux.pytests`, whose source is a `/nix/store`
    path with no `.git` at all. So the suite passed on a dev host and failed in
    the one environment that gates a merge, on every branch, for reasons no PR
    had touched. Reproduce it in one line: `rm .git` from a copy of the tree and
    run this test.

    The rule under test is "the module's own directory is consulted", which does
    not need the real repo. `protected_git_dirs` reads `Path(__file__)` — a
    module global resolved at CALL time — so pointing `gitenv.__file__` at a
    repository this test CONSTRUCTS exercises the real default `starts` (no
    `starts=` argument is passed) while owning both roots outright.
    """
    monkeypatch.delenv(PROTECT_ENV, raising=False)
    # The module's home is a repo we control, so the assertion is about the
    # discovery RULE and not about how this file happens to be checked out.
    home = _mkrepo(tmp_path / "module-home")
    monkeypatch.setattr(gitenv, "__file__", str(home / "gitenv.py"))
    # ...and cwd is deliberately in no repository, so the cwd root finds nothing
    # and ONLY the module root can supply the answer.
    outside = tmp_path / "not-a-repo"
    outside.mkdir()
    monkeypatch.chdir(outside)
    assert resolve_git_dir(Path.cwd()) is None, (
        "the tmp cwd resolved to a repository, so this test could pass via the "
        "cwd root and would not kill the mutant it exists for")

    dirs = protected_git_dirs()
    home_git_dir = resolve_git_dir(home)
    assert home_git_dir is not None, "the fixture repo has no git dir (test rig issue)"
    assert home_git_dir in dirs, (
        "the module's own repository was not discovered from a cwd outside any "
        "repository — dropping Path(__file__) from `starts` would be "
        f"invisible.\nfound: {dirs}")


def test_the_module_root_pin_does_not_depend_on_this_tree_being_a_checkout():
    """🔴 THE REGRESSION GUARD FOR THE FIX ABOVE, and it is not a tautology.

    The bug was an environmental assumption hiding inside a behavioural test, and
    the only reason it survived review is that nobody ran the tier that lacks the
    environment. So assert the property directly: the module-root pin must build
    its own repository and must not interrogate the ambient one.

    🔴 CODE ONLY, docstrings and comments STRIPPED. The first version of this
    guard grepped the raw source and matched the PROSE in the test above — which
    quotes the old failure message on purpose — so it failed on a correct tree.
    `_root_line` in this same file carries the identical warning: a parser that
    cannot tell code from prose is measuring the wrong thing.
    """
    import ast

    src = Path(__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    fn = next(n for n in tree.body
              if isinstance(n, ast.FunctionDef)
              and n.name == "test_the_module_root_is_load_bearing")
    body = fn.body[1:] if ast.get_docstring(fn) is not None else fn.body
    code = "\n".join(ast.get_source_segment(src, stmt) or "" for stmt in body)
    assert code.strip(), "could not extract the pinned test's code"

    assert 'monkeypatch.setattr(gitenv, "__file__"' in code, (
        "test_the_module_root_is_load_bearing no longer rebinds the module root, "
        "so it is measuring the ambient tree again — which is exactly what made "
        f"the nix sandbox permanently red.\ncode was:\n{code}")
    assert "_mkrepo(" in code, (
        "the pinned test no longer builds its own repository, so it depends on "
        "the ambient tree being a checkout")
    residual = code.replace('monkeypatch.setattr(gitenv, "__file__"', "")
    assert "gitenv.__file__" not in residual, (
        "the pinned test reads the REAL module location again. That is the "
        "environmental assumption which is false by construction in the nix "
        "sandbox (`cp -r ${./.} src`, no .git)")


# --------------------------------------------------------------------------- #
# 4d. 🔴 THE CO-TENANT PROBE (finding A)
# --------------------------------------------------------------------------- #
def test_live_cotenants_sees_another_process_in_the_repo(tmp_path):
    """The POSITIVE control. Without it, an empty co-tenant list is
    indistinguishable from a probe wired to nothing — and an empty list is what
    puts the detector in ENFORCE mode."""
    _require_proc()
    repo = _mkrepo(tmp_path / "repo")
    git_dir = resolve_git_dir(repo)
    assert live_cotenants([git_dir]) == [], "a brand-new tmp repo already has tenants?"
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"],
                            cwd=str(repo), stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
    try:
        # The child's cwd is set by Popen before exec, so it is already visible.
        found = live_cotenants([git_dir])
        assert any(f.startswith(f"{proc.pid}:") for f in found), (
            f"the co-tenant probe missed a live process in the repo: {found}")
    finally:
        proc.kill()
        proc.wait(timeout=10)
    assert live_cotenants([git_dir]) == [], (
        "the probe still reports a tenant after it exited — it is not reading "
        "live state")


def test_live_cotenants_does_not_count_this_process(tmp_path, monkeypatch):
    """We are always inside the repository we are watching. A probe that counted
    itself would put every session in report mode, i.e. disable the detector
    everywhere — the opposite failure to the one being fixed."""
    _require_proc()
    repo = _mkrepo(tmp_path / "repo")
    monkeypatch.chdir(repo)
    git_dir = resolve_git_dir(repo)
    assert live_cotenants([git_dir]) == [], (
        "the probe counted our own process (or an ancestor) as a co-tenant")


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


def test_touches_nothing_either():
    assert 2 + 2 == 4


def test_still_nothing():
    assert 3 + 3 == 6
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

# 🔴 A WRITER THAT RUNS WHEN NO TEST IS RUNNING. `pytest_runtest_logfinish` fires
# after a test's setup, call AND teardown are complete and before the next
# test's `logstart` — the window the idle probe watches. This is a DETERMINISTIC
# stand-in for the dozens of concurrent sessions on the operator's box: same
# observable (the repository moved while no test body was executing), no timing
# race.
_CONCURRENT_WRITER_CONFTEST = '''
import os
import subprocess

_n = [0]


def pytest_runtest_logfinish(nodeid, location):
    _n[0] += 1
    subprocess.run(["git", "-C", os.environ["GUARD9_TARGET_REPO"],
                    "branch", "-q", f"someone-elses-branch-{_n[0]}"],
                   check=True, capture_output=True)
'''


def _nested_pytest(tmp_path, body: str, *, plugin: bool, env_extra: dict,
                   conftest: str = "") -> subprocess.CompletedProcess:
    rootdir = tmp_path / "nested"
    rootdir.mkdir(exist_ok=True)
    (rootdir / "test_nested.py").write_text(body, encoding="utf-8")
    if conftest:
        (rootdir / "conftest.py").write_text(conftest, encoding="utf-8")
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
    assert SESSION_MARKER in proc.stdout, (
        "the guard did not announce itself; this run may not have loaded it at "
        f"all:\n{proc.stdout}"
    )


def test_the_detector_fails_the_test_that_moved_a_ref(tmp_path):
    """🔴 REACHABILITY, not merely breakability. The nested test writes by
    ABSOLUTE PATH, so the environment strip is bypassed and the ONLY thing that
    can go red is the detector. Asserted by this guard's own token, so a failure
    from some neighbouring check cannot be scored as a kill.

    `MODE_ENV` is pinned to `enforce` so the verdict cannot depend on how busy
    the host happens to be while the suite runs — under `auto` a co-tenant in
    the *outer* checkout would (correctly) downgrade this run and the control
    would measure the mode, not the detector.
    """
    ambient = _mkrepo(tmp_path / "ambient")
    proc = _nested_pytest(
        tmp_path, _ABSOLUTE_PATH_MUTATOR, plugin=True,
        env_extra={PROTECT_ENV: str(ambient / ".git"),
                   MODE_ENV: MODE_ENFORCE,
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
        env_extra={PROTECT_ENV: str(ambient / ".git"), MODE_ENV: MODE_ENFORCE})
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, f"a harmless test was failed by GUARD 9:\n{out}"
    assert VIOLATION_TOKEN not in out, out
    assert OBSERVED_MARKER not in out, out
    assert "3 passed" in out, out


def test_the_detector_catches_damage_done_at_IMPORT_time(tmp_path):
    """Two suites in this repo build real git repos at MODULE scope, so their
    git runs during COLLECTION — before any test starts. A per-test-only check
    would attribute that to an arbitrary test, or to none."""
    ambient = _mkrepo(tmp_path / "ambient")
    proc = _nested_pytest(
        tmp_path, _IMPORT_TIME_MUTATOR, plugin=True,
        env_extra={PROTECT_ENV: str(ambient / ".git"),
                   MODE_ENV: MODE_ENFORCE,
                   "GUARD9_TARGET_REPO": str(ambient)})
    out = proc.stdout + proc.stderr
    assert proc.returncode != 0, f"import-time damage went unnoticed:\n{out}"
    assert VIOLATION_TOKEN in out, out
    assert "escaped-at-import" in out, out
    assert "collection" in out, (
        f"the violation was not attributed to collection:\n{out}")
    # 🔴 AND IT MUST NOT BE AN INTERNALERROR. `pytest.fail` raised from a HOOK
    # crashes pytest: exit 3, zero tests run, the message buried under
    # `INTERNALERROR>`. Measured on #683's code — a NEIGHBOUR's `git branch`
    # landing during collection took the whole suite down that way, which is
    # both a false positive and a total outage. `pytest.exit` reports the same
    # verdict as a decision.
    assert "INTERNALERROR" not in out, (
        "a session-level violation crashed pytest instead of exiting cleanly:\n"
        f"{out}")
    assert proc.returncode == 1, (
        f"expected a clean exit 1, got {proc.returncode} (3 = internal error)")


def test_the_violation_message_leads_with_the_other_writer_hypothesis(tmp_path):
    """🔴 A red gate whose message does not say what to do gets clicked through
    — and one whose FIRST sentence names the wrong cause is worse than that: it
    misdirects during an incident.

    #683's message opened *"test X MUTATED a git repository that is not its own
    tmpdir … This is the 2026-08-21 incident's shape"*, at maximum confidence,
    for any write by any of the dozens of concurrent sessions on that box. The
    ordering is pinned, not just the presence of the words.
    """
    ambient = _mkrepo(tmp_path / "ambient")
    proc = _nested_pytest(
        tmp_path, _ABSOLUTE_PATH_MUTATOR, plugin=True,
        env_extra={PROTECT_ENV: str(ambient / ".git"),
                   MODE_ENV: MODE_ENFORCE,
                   "GUARD9_TARGET_REPO": str(ambient)})
    out = proc.stdout + proc.stderr
    assert "git reflog" in out, f"no recovery instruction in the failure:\n{out}"
    assert "GIT_DIR" in out, f"the failure does not name the known mechanism:\n{out}"
    other = out.find("DID SOMETHING ELSE WRITE TO THIS REPOSITORY")
    incident = out.find("2026-08-21")
    assert other != -1, f"the other-writer hypothesis is not in the message:\n{out}"
    assert incident != -1, f"the incident is no longer described at all:\n{out}"
    assert other < incident, (
        "the message still leads with the incident rather than with the most "
        f"common cause:\n{out}")


# --------------------------------------------------------------------------- #
# 6. 🔴 ATTRIBUTION: a concurrent writer must not be blamed on a test
# --------------------------------------------------------------------------- #
def test_a_write_in_the_idle_window_is_NOT_blamed_on_a_test(tmp_path):
    """🔴 THE FINDING-A REGRESSION TEST, and the reason #683 could not ship.

    Measured on that code: an innocent nested test plus a background
    `git branch` in the protected repo produced *"test
    'test_d.py::test_innocent_one' MUTATED a git repository that is not its own
    tmpdir … This is the 2026-08-21 incident's shape."* — a fail, naming an
    innocent test, asserting the incident.

    Here the write happens in `pytest_runtest_logfinish`, i.e. after a test's
    setup/call/teardown are all complete and before the next test's `logstart`.
    Nothing of the test protocol runs in that window, so the change is provably
    not any test's — and the detector says so instead of failing.
    """
    ambient = _mkrepo(tmp_path / "ambient")
    proc = _nested_pytest(
        tmp_path, _INNOCENT_TEST, plugin=True,
        conftest=_CONCURRENT_WRITER_CONFTEST,
        env_extra={PROTECT_ENV: str(ambient / ".git"),
                   "GUARD9_TARGET_REPO": str(ambient)})
    out = proc.stdout + proc.stderr
    assert "3 passed" in out, f"an innocent test was failed by a foreign write:\n{out}"
    assert proc.returncode == 0, out
    assert VIOLATION_TOKEN not in out, (
        "a concurrent writer was still reported as a GUARD 9 violation — the "
        f"exact permanently-red gate this replaces:\n{out}")
    # 🔴 AND IT IS NOT SILENT. Downgrading to a quiet pass would trade a false
    # positive for a blind spot; the delta is printed in full, with the reason.
    assert FOREIGN_MARKER in out, (
        f"the session never announced that it could not attribute:\n{out}")
    assert OBSERVED_MARKER in out, (
        f"the delta was swallowed instead of reported:\n{out}")
    assert "someone-elses-branch" in out, (
        f"the report does not name what moved:\n{out}")
    assert "unattributed-observations=" in out, (
        f"no session-end summary of what went unattributed:\n{out}")


def test_the_same_run_without_the_foreign_writer_stays_fully_silent(tmp_path):
    """The control for the test above: identical body and identical mode, minus
    the writer. Without it, "no violation" would be satisfied by a detector that
    had simply stopped working."""
    ambient = _mkrepo(tmp_path / "ambient")
    proc = _nested_pytest(
        tmp_path, _INNOCENT_TEST, plugin=True,
        env_extra={PROTECT_ENV: str(ambient / ".git"),
                   "GUARD9_TARGET_REPO": str(ambient)})
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, out
    assert "3 passed" in out, out
    assert FOREIGN_MARKER not in out, (
        f"a quiet repository was declared to have another writer:\n{out}")
    assert OBSERVED_MARKER not in out, out


def test_a_real_escape_is_still_reported_when_attribution_is_impossible(tmp_path):
    """🔴 REPORT MODE IS NOT A BLIND SPOT. The same absolute-path escape, with
    the mode pinned to `report`: it must still be seen and named, with the token
    ABSENT so a reachability control cannot be satisfied by an unattributed
    observation."""
    ambient = _mkrepo(tmp_path / "ambient")
    proc = _nested_pytest(
        tmp_path, _ABSOLUTE_PATH_MUTATOR, plugin=True,
        env_extra={PROTECT_ENV: str(ambient / ".git"),
                   MODE_ENV: MODE_REPORT,
                   "GUARD9_TARGET_REPO": str(ambient)})
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, f"report mode failed the run:\n{out}"
    assert OBSERVED_MARKER in out, f"report mode saw nothing:\n{out}"
    assert "escaped-branch" in out, f"report mode did not name the ref:\n{out}"
    assert VIOLATION_TOKEN not in out, (
        "report mode emitted the violation token, so a control asserting that "
        f"token can pass without any attribution:\n{out}")


def test_the_marker_line_reports_the_mode_and_why(tmp_path):
    """The marker is the run's only evidence the guard loaded. A count of
    protected dirs without the MODE is a number whose meaning changed: `mode=
    report` and `mode=enforce` are different guarantees, and #683's marker
    reported neither."""
    ambient = _mkrepo(tmp_path / "ambient")
    proc = _nested_pytest(
        tmp_path, _INNOCENT_TEST, plugin=True,
        env_extra={PROTECT_ENV: str(ambient / ".git"), MODE_ENV: MODE_ENFORCE})
    line = next((ln for ln in proc.stdout.splitlines() if ln.startswith(SESSION_MARKER)), "")
    assert line, f"no marker line at all:\n{proc.stdout}"
    assert "protected-git-dirs=1" in line, line
    assert f"mode={MODE_ENFORCE}" in line, line
    assert "unattributable=0" in line, line


# --------------------------------------------------------------------------- #
# 6b. THE SETTLE RE-READ, as a unit
# --------------------------------------------------------------------------- #
def test_a_repo_that_keeps_moving_is_declared_foreign_before_anything_fails(monkeypatch):
    """🔴 "RE-READ BEFORE YOU FAIL." Driven at the unit level because the
    behaviour under test is a TIMING one — a repository still being written
    while we look at it — and a real concurrent writer would make this test a
    race. The snapshots are a fixed sequence, so the outcome is deterministic.

    A different value each time means the repo never settles; the plugin must
    conclude "another writer" and REPORT, not `pytest.fail`.
    """
    reads = iter([{"k": "1"}, {"k": "2"}, {"k": "3"}, {"k": "4"}, {"k": "5"}])
    monkeypatch.setattr(gitenv_plugin, "_take", lambda: next(reads))
    monkeypatch.setattr(gitenv_plugin, "SETTLE_SECONDS", 0.0)
    monkeypatch.setattr(gitenv_plugin, "_BASELINE", {"k": "0"})
    monkeypatch.setattr(gitenv_plugin, "_UNATTRIBUTABLE", [])
    monkeypatch.setattr(gitenv_plugin, "_MODE", "auto")
    monkeypatch.setattr(gitenv_plugin, "_OBSERVED", 0)
    monkeypatch.setattr(gitenv_plugin, "_VIOLATIONS", 0)

    gitenv_plugin._check("a unit-level probe")     # must NOT raise Failed

    assert gitenv_plugin._UNATTRIBUTABLE, "a repo that never settled was still attributed"
    assert gitenv_plugin._OBSERVED == 1
    assert gitenv_plugin._VIOLATIONS == 0


def test_a_repo_that_settles_is_still_attributed(monkeypatch):
    """The control. If "keeps moving" were the only path, the settle re-read
    would have disabled enforcement outright rather than narrowed it."""
    reads = iter([{"k": "1"}, {"k": "1"}, {"k": "1"}])
    monkeypatch.setattr(gitenv_plugin, "_take", lambda: next(reads))
    monkeypatch.setattr(gitenv_plugin, "SETTLE_SECONDS", 0.0)
    monkeypatch.setattr(gitenv_plugin, "_BASELINE", {"k": "0"})
    monkeypatch.setattr(gitenv_plugin, "_UNATTRIBUTABLE", [])
    monkeypatch.setattr(gitenv_plugin, "_MODE", "auto")
    monkeypatch.setattr(gitenv_plugin, "_OBSERVED", 0)
    monkeypatch.setattr(gitenv_plugin, "_VIOLATIONS", 0)

    # 🔴 `pytest.fail.Exception` (i.e. `_pytest.outcomes.Failed`) derives from
    # BaseException, so `pytest.raises(Exception)` does NOT catch it — it would
    # let the failure through and report this control as red for the wrong
    # reason.
    with pytest.raises(pytest.fail.Exception) as excinfo:
        gitenv_plugin._check("a unit-level probe")
    assert VIOLATION_TOKEN in str(excinfo.value)
    assert not gitenv_plugin._UNATTRIBUTABLE
    assert gitenv_plugin._VIOLATIONS == 1


# --- the seam with GUARD 10 (`testlib/nogit_plugin.py`) ----------------------
# 🔴 REGRESSION, measured 2026-08-22 on the MERGED tree and visible on neither
# branch alone. GUARD 10 isolates git by pointing `GIT_CONFIG_GLOBAL` at a
# scratch `gitconfig` under `DEVRC_TEST_GIT_GUARD_DIR` and letting tests write
# there. This guard fingerprinted that file, so every target reported
# `DEVRC-GITENV-VIOLATION: CHANGED …/gitconfig` — one guard calling the other's
# correct behaviour an incident.


def test_a_scratch_GIT_CONFIG_GLOBAL_is_not_treated_as_a_config_to_protect(
    tmp_path, monkeypatch
):
    """A redirect the HARNESS made is not the operator's config."""
    guard_dir = tmp_path / "nogit-run"
    guard_dir.mkdir()
    scratch = guard_dir / "gitconfig"
    scratch.write_text("[user]\n\tname = fixture\n", encoding="utf-8")

    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(scratch))
    monkeypatch.setenv("DEVRC_TEST_GIT_GUARD_DIR", str(guard_dir))

    watched = global_config_paths()
    assert scratch not in watched, (
        "the session's own scratch gitconfig is watched, so GUARD 10 writing to "
        "it — which is the entire point of the redirect — reads as a violation"
    )
    # 🔴 And the operator's real files ARE watched. Skipping the scratch file
    # must not become "watch nothing": before this fix ANY override returned
    # early, so a direct write to ~/.gitconfig went unseen.
    assert watched, "a scratch redirect left NOTHING watched"
    assert any(p.name in (".gitconfig", "config") for p in watched), watched


def test_a_GIT_CONFIG_GLOBAL_outside_the_guard_dir_is_still_protected(
    tmp_path, monkeypatch
):
    """The control. Only a redirect INTO the session's own dir is exempt —
    otherwise the exemption would be a hole any test could walk through by
    setting the variable."""
    guard_dir = tmp_path / "nogit-run"
    guard_dir.mkdir()
    elsewhere = tmp_path / "somewhere-else" / "gitconfig"
    elsewhere.parent.mkdir()
    elsewhere.write_text("[user]\n\tname = real\n", encoding="utf-8")

    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(elsewhere))
    monkeypatch.setenv("DEVRC_TEST_GIT_GUARD_DIR", str(guard_dir))

    assert global_config_paths() == [elsewhere]


def test_with_no_guard_dir_an_override_is_protected_exactly_as_before(
    tmp_path, monkeypatch
):
    """No GUARD 10 in the run at all: unchanged behaviour."""
    target = tmp_path / "gitconfig"
    target.write_text("", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(target))
    monkeypatch.delenv("DEVRC_TEST_GIT_GUARD_DIR", raising=False)
    assert global_config_paths() == [target]
# --------------------------------------------------------------------------- #
# 6. 🔴 run-tests.sh's SHELL half, measured END TO END rather than read
# --------------------------------------------------------------------------- #
# Everything in section 1 reads the runner's TEXT: the array agrees with the
# owner, the `unset` line exists, it precedes the ROOT block. All three are
# satisfied by a file that never runs — reading as coverage while providing none
# is the shape claude/RULES.md rates worse than no guard at all.
#
# So this pair drives the real `run-tests.sh` (via the shared patched copy that
# ~15 other suites use) with GIT_DIR poisoned, and asks a NON-PYTEST target what
# it actually received. The non-pytest tier is the point: HOOK_TESTS and
# SHELL_TESTS load no plugin, so the shell `unset` is their ONLY protection —
# testing the pytest tier here would measure `testlib/gitenv_plugin` a second
# time and report it as evidence about the shell.
#
# The probe's damaging step is the incident's own shape: `git -C <dir>` where
# <dir> is NOT a repository. Clean, that is an error. With GIT_DIR inherited it
# is a commit on somebody else's branch.
# 🔴 NO SHEBANG, deliberately. run-tests.sh invokes a SHELL_TESTS entry as
# `bash "$SHELL_TEST"`, so one would be decorative — and test_runtime_shebangs.py
# fails any test that writes its own (it caught this file's first draft).
_SHELL_PROBE = '''\
# Written by test_git_repo_isolation.py into a tmp dir; never part of the repo.
set -u
report="__REPORT__"
: > "$report"
echo "GIT_DIR=${GIT_DIR-<unset>}" >> "$report"
echo "GIT_INDEX_FILE=${GIT_INDEX_FILE-<unset>}" >> "$report"
export GIT_AUTHOR_NAME=probe GIT_AUTHOR_EMAIL=probe@example.invalid
export GIT_COMMITTER_NAME=probe GIT_COMMITTER_EMAIL=probe@example.invalid
git -C "__NOTAREPO__" commit -q --allow-empty -m "probe: fixture-shaped commit" \\
  >> "$report" 2>&1
echo "commit_rc=$?" >> "$report"
# Always 0: a red SHELL_TESTS verdict would fail the run for a reason that is
# not the subject, and an ambiguous red cannot tell the two halves apart.
exit 0
'''

_UNSET_LINE = 'unset "${DEVRC_GIT_REPO_POINTERS[@]}"'


def _decoy_git_dirs(wt: Path) -> list[Path]:
    """The decoy's per-worktree gitdir AND the common dir refs really live in.

    Fingerprinting only the per-worktree dir would miss every ref and config
    write — the exact blind spot `common_dir_of` exists to close.
    """
    git_dir = resolve_git_dir(wt)
    assert git_dir is not None, f"could not resolve a git dir for {wt}"
    out = [git_dir]
    common = common_dir_of(git_dir)
    if common is not None:
        out.append(common)
    assert len(out) == 2, (
        f"the decoy worktree reported no common dir ({git_dir}); the fingerprint "
        "would then be blind to exactly the refs the incident moved")
    return out


def _decoy_with_worktree(tmp_path: Path) -> tuple[Path, Path, Path]:
    """A repo, a LINKED worktree on its own branch, and that worktree's gitdir.

    The linked-worktree shape is not decoration: `git push` from a linked
    worktree is what exports GIT_DIR=<repo>/.git/worktrees/<name> into a pre-push
    hook, which is the route from a push to `run-tests.sh` (githooks/pre-push ->
    tests-on-push.sh -> run-tests.sh). MEASURED on git 2.55.0: a push from the
    MAIN checkout exports no GIT_DIR at all.
    """
    work = _mkrepo(tmp_path / "decoy" / "work", branch="decoy/base")
    wt = tmp_path / "decoy" / "wt"
    assert _git(work, "worktree", "add", str(wt), "-b", "decoy/target", "-q").returncode == 0
    gitdir = work / ".git" / "worktrees" / "wt"
    assert gitdir.is_dir(), f"no linked-worktree gitdir at {gitdir}"
    return work, wt, gitdir


def _drive_runner(tmp_path: Path, *, keep_unset: bool) -> dict:
    """Run a patched copy of run-tests.sh with GIT_DIR poisoned; report facts.

    `keep_unset=False` deletes GUARD 9's `unset` line from the COPY — one
    mutation, in the copy only, so the two halves differ by exactly the line
    under test (claude/RULES.md → "ISOLATE THE MUTATION").
    """
    from testlib.runner_patch import runner_with_targets, write_pytest_suite

    work, wt, gitdir = _decoy_with_worktree(tmp_path)
    before = _git(wt, "rev-parse", "HEAD").stdout.strip()
    assert before, "the decoy worktree has no HEAD — the fixture is broken"
    protected = _decoy_git_dirs(wt)
    # `index` is included here and excluded from the host-repo fingerprint for
    # the same reason: this decoy is read by nobody else between the two
    # snapshots, and the wild damage overwrote the real index.
    baseline = snapshot(protected, extra_files=[d / "index" for d in protected])

    notarepo = tmp_path / "notarepo"
    notarepo.mkdir()
    report = tmp_path / "probe-report.txt"
    probe = tmp_path / "probe.sh"
    probe.write_text(
        _SHELL_PROBE.replace("__REPORT__", str(report))
                    .replace("__NOTAREPO__", str(notarepo)),
        encoding="utf-8")

    target = tmp_path / "trivial_tests"
    write_pytest_suite(target, 1, prefix="test_trivial")
    runner = runner_with_targets(tmp_path, [str(target)], {str(target): 1},
                                 hook_tests=[], shell_tests=[str(probe)])
    if not keep_unset:
        src = runner.read_text(encoding="utf-8")
        assert src.count(_UNSET_LINE) == 1, (
            "the copied runner does not contain exactly one GUARD 9 `unset` "
            f"line, so the mutation cannot be placed: found {src.count(_UNSET_LINE)}")
        runner.write_text(src.replace(_UNSET_LINE, "# MUTANT: unset removed"),
                          encoding="utf-8")

    # 🔴 The OUTER gate's guard bookkeeping is REMOVED from the child, the same
    # way `test_activity_spool_isolation._clean_home` does it. A nested runner
    # that inherits `DEVRC_TEST_LAUNCH_STUB_DIR` / `ACTIVITY_SPOOL_DIR` /
    # `XDG_STATE_HOME` / `DEVRC_TEST_SPOOL_GUARD_DIR` shares the log files whose
    # per-target line counts GUARD 7 and GUARD 8 slice up, and a nested run's
    # lines landing in the outer run's slice misattributes both. Removed rather
    # than pointed elsewhere: each is re-derived by the child from scratch.
    env = _env(GIT_DIR=str(gitdir), HOME=str(tmp_path / "home"))
    for leaked in ("DEVRC_TEST_LAUNCH_STUB_DIR", "DEVRC_TEST_SPOOL_GUARD_DIR",
                   "ACTIVITY_SPOOL_DIR", "XDG_STATE_HOME",
                   "DEVRC_TEST_SPOOL_IN_SESSION"):
        env.pop(leaked, None)
    (tmp_path / "home").mkdir(exist_ok=True)
    # ROOT is passed EXPLICITLY. Without it the runner resolves its own root with
    # `rev-parse --show-toplevel`, which under a poisoned GIT_DIR yields
    # `<repo>/scripts` and exits 127 before any target runs — the mutated half
    # would then die for a reason that is not the subject.
    proc = subprocess.run([BASH, str(runner), str(ROOT)],
                          capture_output=True, text=True, env=env, cwd=str(tmp_path))
    return {
        "proc": proc,
        "report": report.read_text(encoding="utf-8") if report.is_file() else None,
        "before": before,
        "after": _git(wt, "rev-parse", "HEAD").stdout.strip(),
        "work": work,
        "wt": wt,
        "deltas": diff_snapshots(
            baseline,
            snapshot(protected, extra_files=[d / "index" for d in protected])),
    }


def test_without_the_unset_the_runner_hands_a_foreign_repo_to_a_shell_target(tmp_path):
    """🔴 THE RED HALF — and the REACHABILITY proof for the pair.

    A `run-tests.sh` whose GUARD 9 line is deleted passes GIT_DIR straight
    through to a SHELL_TESTS script, whose `git -C <not-a-repo> commit` then
    lands on the decoy's branch. If this ever stops reproducing, the green half
    below stops being evidence about anything.
    """
    r = _drive_runner(tmp_path, keep_unset=False)
    assert r["report"] is not None, (
        "the shell probe never ran, so this control measured nothing:\n"
        f"{r['proc'].stdout[-3000:]}\n{r['proc'].stderr[-3000:]}")
    assert "GIT_DIR=<unset>" not in r["report"], (
        f"the mutated runner stripped GIT_DIR anyway — the mutation did not "
        f"land:\n{r['report']}")
    assert r["after"] != r["before"], (
        "the decoy branch did NOT move even with the guard removed. Either the "
        "probe is wired to nothing or git no longer honours GIT_DIR over -C; "
        f"either way the green half proves nothing.\nreport:\n{r['report']}")
    log = _git(r["wt"], "log", "--oneline").stdout
    assert "probe: fixture-shaped commit" in log, (
        f"the decoy moved, but not for the probe's reason:\n{log}")
    assert r["deltas"], (
        "HEAD moved but the fingerprint saw nothing — the detector used by the "
        "green half is wired to nothing")


def test_the_runner_strips_the_pointers_before_a_non_pytest_target_runs(tmp_path):
    """🔴 THE GREEN HALF. Identical fixture, identical env, unmutated runner.

    Asserts the RELATIONSHIP — the foreign repository is byte-identical
    afterwards — not that some string was printed. The probe's own commit is
    asserted to have FAILED, because "the decoy did not move" is equally true of
    a probe that never executed; the report file's content is what tells the two
    apart.
    """
    r = _drive_runner(tmp_path, keep_unset=True)
    assert r["report"] is not None, (
        "the shell probe never ran, so nothing here was measured:\n"
        f"{r['proc'].stdout[-3000:]}\n{r['proc'].stderr[-3000:]}")
    assert "GIT_DIR=<unset>" in r["report"], (
        f"run-tests.sh handed GIT_DIR to a SHELL_TESTS target:\n{r['report']}")
    assert "GIT_INDEX_FILE=<unset>" in r["report"], r["report"]
    assert "commit_rc=0" not in r["report"], (
        "the probe's `git -C <not-a-repo> commit` SUCCEEDED. With the pointers "
        f"stripped it has no repository to write to and must fail:\n{r['report']}")
    assert r["after"] == r["before"], (
        f"the foreign worktree branch moved: {r['before']} -> {r['after']}\n"
        f"{r['report']}")
    # 🔴 THE WHOLE REPOSITORY, not just the one ref the probe aimed at. HEAD is
    # the damage this probe happens to cause; the incident also rewrote config,
    # created and deleted branches, and overwrote the index. Asserting the
    # fingerprint pins "the foreign repo is untouched", which is the claim.
    assert r["deltas"] == [], (
        "the foreign repository changed even though its HEAD did not:\n"
        + "\n".join(r["deltas"]))


# --------------------------------------------------------------------------- #
# 9. THE PRECONDITION ITSELF — a canary on git, not on devrc
# --------------------------------------------------------------------------- #
def test_git_exports_GIT_DIR_to_pre_push_only_from_a_linked_worktree(tmp_path):
    """🔴 WHY GUARD 9's STRIP IS LOAD-BEARING ON THE ORDINARY PATH.

    `githooks/pre-push` used to conclude that "pushing does not, on its own,
    hand `GIT_DIR` to anything", and that the 2026-08-21 incident's root cause
    "REMAINS UNIDENTIFIED". The measurement behind that was real but taken at
    ONE point on a dimension that changes the answer: which checkout the push
    came from. Measured 2026-08-25 on git 2.55.0 with the parent scrubbed:

        push from the MAIN checkout -> GIT_EXEC_PATH only, no GIT_DIR
        push from a LINKED WORKTREE -> GIT_DIR=<repo>/.git/worktrees/<name>

    git itself exports it. No outer caller is needed — and clawgate#322 was a
    push from a linked worktree, which is the match.

    🔴 THIS IS A CANARY ON GIT'S BEHAVIOUR, NOT A TEST OF OUR CODE. It exists so
    that if a future git stops exporting `GIT_DIR` here, someone is TOLD the
    precondition moved, instead of the comment quietly rotting into the same
    false generalisation it replaced. If it fails because git changed: update
    the comment in `githooks/pre-push`, and do NOT weaken the strip on the
    strength of one version's behaviour.

    Both arms are asserted. The main-checkout arm is the control: without it a
    hook that never ran, or a recorder that never wrote, would look identical to
    "git does not export it".
    """
    repo = _mkrepo(tmp_path / "repo")
    bare = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", str(bare)],
                   check=True, capture_output=True, env=_env())
    assert _git(repo, "remote", "add", "origin", str(bare)).returncode == 0
    assert _git(repo, "push", "-q", "origin", "main").returncode == 0

    hooks = tmp_path / "hooks"
    hooks.mkdir()
    # Every line goes INTO the file. An earlier version of this probe printed to
    # stdout, so the file the assertions read was always empty and the probe
    # reported "not exported" no matter what git did.
    (hooks / "pre-push").write_text(
        "#!/usr/bin/env bash\n"
        'out="${PROBE_OUT:-/dev/null}"\n'
        ': > "$out"\n'
        'for v in GIT_DIR GIT_EXEC_PATH; do\n'
        '  if [ -n "${!v:-}" ]; then printf "%s=%s\\n" "$v" "${!v}" >> "$out"; fi\n'
        'done\n'
        "exit 0\n",
        encoding="utf-8")
    (hooks / "pre-push").chmod(0o755)
    assert _git(repo, "config", "--local", "core.hooksPath", str(hooks)).returncode == 0

    def push_from(cwd: Path, branch: str, tag: str) -> str:
        out = tmp_path / f"env-{tag}.txt"
        env = _env(PROBE_OUT=str(out))
        # Scrub the pointer names from the PARENT, so anything recorded was put
        # there by git and not inherited from this test process.
        for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE",
                     "GIT_COMMON_DIR", "GIT_OBJECT_DIRECTORY"):
            env.pop(name, None)
        subprocess.run(["git", "-C", str(cwd), "push", "-q", "origin", branch],
                       capture_output=True, text=True, env=env)
        return out.read_text(encoding="utf-8") if out.exists() else ""

    # --- arm 1: the MAIN checkout ------------------------------------------
    assert _git(repo, "checkout", "-q", "-b", "from-main").returncode == 0
    (repo / "b.txt").write_text("b\n", encoding="utf-8")
    assert _git(repo, "add", "b.txt").returncode == 0
    assert _git(repo, "commit", "-qm", "b").returncode == 0
    main_env = push_from(repo, "from-main", "main")

    # --- arm 2: a LINKED WORKTREE ------------------------------------------
    wt = tmp_path / "wt"
    assert _git(repo, "worktree", "add", "-q", str(wt), "-b", "from-wt",
                "main").returncode == 0
    (wt / "c.txt").write_text("c\n", encoding="utf-8")
    assert _git(wt, "add", "c.txt").returncode == 0
    assert _git(wt, "commit", "-qm", "c").returncode == 0
    wt_env = push_from(wt, "from-wt", "wt")

    # 🔴 POSITIVE CONTROL FIRST. git exports GIT_EXEC_PATH to hooks
    # unconditionally, so if it is missing the hook never ran or never wrote,
    # and an absent GIT_DIR below would prove nothing at all.
    for tag, text in (("main-checkout", main_env), ("linked-worktree", wt_env)):
        assert "GIT_EXEC_PATH=" in text, (
            f"the {tag} probe recorded no GIT_EXEC_PATH, so the hook did not run "
            f"or did not write. Nothing in this test was measured.\ngot: {text!r}")

    assert "GIT_DIR=" not in main_env, (
        "git exported GIT_DIR to pre-push from the MAIN checkout. The asymmetry "
        "this test pins has changed; re-measure and update the comment in "
        f"githooks/pre-push.\ngot: {main_env!r}")

    assert "GIT_DIR=" in wt_env, (
        "git did NOT export GIT_DIR to pre-push from a LINKED WORKTREE. That is "
        "the precondition behind GUARD 9's strip and behind the clawgate#322 "
        "diagnosis. If git genuinely changed, update the root-cause comment in "
        "githooks/pre-push — do NOT weaken the strip on one version's "
        f"behaviour.\ngot: {wt_env!r}")

    # Name it precisely: it points at the WORKTREE's gitdir, which is why a
    # `-C` lookup elsewhere resolves into this repo rather than the target.
    assert "/worktrees/" in wt_env, (
        "GIT_DIR was exported but does not point at a linked worktree's gitdir; "
        f"the mechanism may differ from the one documented.\ngot: {wt_env!r}")
