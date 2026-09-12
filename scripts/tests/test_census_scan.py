"""The derivation behind `scripts/ledger-check.sh`, and the runner's refusals.

🔴 WHAT IS BEING PROTECTED, AND WHY IT IS NOT A LIST
----------------------------------------------------
`main` went red twice in one session from one shape — a repo-census guard
broken by a file arriving without its ledger row. The fix is a check that runs
ONLY those guards, in minutes rather than the full tier's ~20.

The obvious implementation is a list of "the ledger tests". That list is itself
a ledger, and it rots exactly the way `_KILL_MENTION_LEDGER` and
`_OWN_BOUND_LEDGER` did — the failure being fixed, one level up. So
`testlib/census_scan.py` DERIVES the set from the AST, and the tests below are
about the derivation, not about its output.

Two kinds of test here, deliberately:

  * **synthetic** — a miniature repo built in `tmp_path`, where the expected
    answer is known by construction rather than read off this repo. These are
    what a mutation has to kill, and they are milliseconds.
  * **anchored** — run against THIS repo and assert the two incidents are still
    selected. That is a POSITIVE CONTROL (a lower bound proving the derivation
    still sees what it was built for), never the source of the set. If the
    derivation silently stopped working, every synthetic test would still pass
    and only these would go red.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from testlib import census_scan  # noqa: E402
# 🔴 `mockbin.write_exec` OWNS the shebang (`/bin/sh`) and RAISES if a call site
# supplies its own. The controls below need a file on disk that is merely
# PRESENT and executable — they never run it — but giving a runtime-written stub
# an env-resolved shebang is still a defect: that resolver does not exist in the
# nix build sandbox, and `patchShebangs` cannot reach a file a test writes while
# running. That is the SAME tier-blindness this block was added to fix, one
# level in — it went red here on exactly that, after the fix above.
# Pinned repo-wide by `test_runtime_shebangs.py`.
# 🔴 The prose above deliberately does NOT spell the offending token: this repo
# has reddened `main` repeatedly because a guard matching "a file mentions X" is
# tripped by the documentation of that guard.
from testlib import mockbin  # noqa: E402

LEDGER_CHECK = REPO_ROOT / "scripts" / "ledger-check.sh"

#: 🔴 THE TWO INCIDENTS, BY NODEID. Not a ledger of what to run — the runner
#: never reads this — but the positive control on the derivation. Each entry is
#: the exact test that held `main` red for hours on 2026-09-11/12.
INCIDENT_ANCHORS = (
    (
        "scripts/claude-hooks/tests/test_guard_core.py"
        "::test_every_kill_server_call_site_in_the_repo_is_classified"
    ),
    (
        "scripts/tests/test_runner_bound_ledger.py"
        "::test_every_site_writing_its_OWN_runner_bound_is_in_the_ledger"
    ),
)


# --------------------------------------------------------------- synthetic repo

def _mini_repo(tmp_path: Path, modules: dict[str, str]) -> Path:
    """Write `{repo-relative path: source}` into a throwaway tree.

    `analyze()` only ever looks under `<root>/scripts`, so this is the whole
    universe it sees — no `.git`, no conftest, nothing inherited from the real
    checkout.
    """
    for rel, src in modules.items():
        target = tmp_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(textwrap.dedent(src), encoding="utf-8")
    (tmp_path / "scripts").mkdir(exist_ok=True)
    return tmp_path


def test_a_test_that_walks_the_repo_root_is_selected(tmp_path):
    """The simplest census shape: `REPO_ROOT.rglob(...)` in the test itself."""
    root = _mini_repo(tmp_path, {
        "scripts/tests/test_alpha.py": """
            from pathlib import Path
            REPO_ROOT = Path(__file__).resolve().parents[2]

            def test_every_file_is_fine():
                assert list(REPO_ROOT.rglob("*.py")) is not None
        """,
    })
    assert census_scan.census_nodeids(root) == [
        "scripts/tests/test_alpha.py::test_every_file_is_fine"
    ]


def test_a_test_that_walks_a_TMP_PATH_is_NOT_selected(tmp_path):
    """🔴 THE NARROWING THAT MAKES THIS RUNNABLE.

    A scanner's own behavioural tests walk a fixture tree, not this repo, and no
    file landing on `main` can redden them. Measured on this repo: keeping them
    took the selection from 240 nodeids to 281 and the run from ~2.5 min to
    ~6 min, for zero added coverage of the class.
    """
    root = _mini_repo(tmp_path, {
        "scripts/tests/test_beta.py": """
            from pathlib import Path
            REPO_ROOT = Path(__file__).resolve().parents[2]

            def test_the_walker_works(tmp_path):
                assert list(tmp_path.rglob("*")) == []
        """,
    })
    assert census_scan.census_nodeids(root) == []


def test_the_closure_follows_a_SHARED_LISTER_across_modules(tmp_path):
    """🔴 WITHOUT THIS, BOTH INCIDENTS ARE INVISIBLE.

    This repo owns ONE two-tier file lister
    (`testlib.public_ip_scan.repo_files`) and most census guards reach the tree
    only through it. A derivation that looked for literal `rglob` calls in test
    bodies would select neither `_KILL_MENTION_LEDGER` nor `_OWN_BOUND_LEDGER`.

    Note the lister walks its own PARAMETER, which is why the enumerator seed is
    root-BLIND and the root test happens at the call site.
    """
    root = _mini_repo(tmp_path, {
        "scripts/testlib/lister.py": """
            def repo_files(where):
                return sorted(where.rglob("*"))
        """,
        "scripts/tests/test_gamma.py": """
            from pathlib import Path
            from testlib.lister import repo_files
            REPO_ROOT = Path(__file__).resolve().parents[2]

            def _tracked():
                return repo_files(REPO_ROOT)

            def test_the_ledger_is_two_way():
                assert _tracked() is not None

            def test_the_lister_handles_an_empty_tree(tmp_path):
                assert repo_files(tmp_path) == []
        """,
    })
    assert census_scan.census_nodeids(root) == [
        "scripts/tests/test_gamma.py::test_the_ledger_is_two_way"
    ]


def test_a_zero_argument_helper_still_counts_as_a_census(tmp_path):
    """`_scan_kill_sites()` takes no arguments and closes over `_REPO`.

    A call site that names no root cannot be screened out on that basis without
    dropping the `_KILL_MENTION_LEDGER` incident, so a zero-argument call to an
    enumerator propagates.
    """
    root = _mini_repo(tmp_path, {
        "scripts/tests/test_delta.py": """
            from pathlib import Path
            _REPO = Path(__file__).resolve().parents[2]

            def _scan():
                return {p.name for p in _REPO.rglob("*")}

            def test_every_site_is_classified():
                assert _scan() is not None
        """,
    })
    assert census_scan.census_nodeids(root) == [
        "scripts/tests/test_delta.py::test_every_site_is_classified"
    ]


def test_a_DEFAULT_ARGUMENT_root_is_reached_by_a_bare_call(tmp_path):
    """🔴 WHAT THE ZERO-ARGUMENT RULE IS ACTUALLY FOR.

    `def scan(root=REPO_ROOT)` enumerates its own PARAMETER, so the root test
    cannot fire inside it; the only place the repo is named is the default, and
    the call site passes nothing. Screening zero-argument calls out — the
    obvious tightening — makes this shape invisible.
    """
    root = _mini_repo(tmp_path, {
        "scripts/tests/test_lambda.py": """
            from pathlib import Path
            REPO_ROOT = Path(__file__).resolve().parents[2]

            def scan(where=REPO_ROOT):
                return sorted(where.rglob("*.py"))

            def test_the_ledger_matches():
                assert scan() is not None

            def test_the_scanner_handles_an_empty_tree(tmp_path):
                assert scan(tmp_path) == []
        """,
    })
    assert census_scan.census_nodeids(root) == [
        "scripts/tests/test_lambda.py::test_the_ledger_matches"
    ]


def test_a_DOCSTRING_mentioning_ls_files_is_not_a_scan(tmp_path):
    """A docstring enumerates nothing.

    Several helpers in `testlib` document the shared lister by name; counting a
    docstring would classify half of it as a census and drag the run back up.
    """
    root = _mini_repo(tmp_path, {
        "scripts/tests/test_epsilon.py": """
            from pathlib import Path
            REPO_ROOT = Path(__file__).resolve().parents[2]

            def test_nothing_is_scanned():
                '''Prefers git ls-files over a walk, says the prose.'''
                assert REPO_ROOT.name
        """,
    })
    assert census_scan.census_nodeids(root) == []


def test_a_git_ls_files_argv_naming_the_root_IS_a_scan(tmp_path):
    """The third enumeration shape, inline rather than behind the lister."""
    root = _mini_repo(tmp_path, {
        "scripts/tests/test_zeta.py": """
            import subprocess
            from pathlib import Path
            REPO_ROOT = Path(__file__).resolve().parents[2]

            def test_no_tracked_file_offends():
                out = subprocess.run(
                    ["git", "-C", str(REPO_ROOT), "ls-files"],
                    capture_output=True, text=True).stdout
                assert out is not None
        """,
    })
    assert census_scan.census_nodeids(root) == [
        "scripts/tests/test_zeta.py::test_no_tracked_file_offends"
    ]


def test_a_ls_files_FIXTURE_STRING_with_no_root_is_not_a_scan(tmp_path):
    """The documented over-classification, screened by the root half.

    `testlib/readset_plugin.py` records a regex classifier calling 32 of 139
    files repo-wide scanners because `git ls-files` appeared in a shell snippet
    fed to the script under test. Both halves — the token AND a repo root named
    in the same function — are required here.
    """
    root = _mini_repo(tmp_path, {
        "scripts/tests/test_eta.py": """
            import subprocess
            from pathlib import Path
            REPO_ROOT = Path(__file__).resolve().parents[2]

            def test_the_script_handles_a_bare_tree(tmp_path):
                script = "cd \\"$1\\" && git ls-files"
                assert subprocess.run(
                    ["bash", "-c", script, "x", str(tmp_path)]).returncode == 0
        """,
    })
    assert census_scan.census_nodeids(root) == []


def test_a_class_based_test_gets_a_THREE_PART_nodeid(tmp_path):
    """🔴 A two-part nodeid for a method is a pytest USAGE ERROR (exit 4).

    Measured: 24 selected tests lived in `class Test…:` bodies, and passing
    `file.py::method` for any one of them made pytest refuse the WHOLE
    invocation — 0 tests run, and an exit code that is not "a test failed".
    """
    root = _mini_repo(tmp_path, {
        "scripts/tests/test_theta.py": """
            from pathlib import Path
            REPO_ROOT = Path(__file__).resolve().parents[2]

            class TestTheLedger:
                def test_it_lists_real_tracked_files(self):
                    assert list(REPO_ROOT.rglob("*")) is not None
        """,
    })
    assert census_scan.census_nodeids(root) == [
        "scripts/tests/test_theta.py::TestTheLedger::test_it_lists_real_tracked_files"
    ]


def test_a_MODULE_LEVEL_census_selects_the_WHOLE_module(tmp_path):
    """Import-time reads are real reads, and they run during COLLECTION.

    A module that scans at import can fail before any per-test selection is
    meaningful, so the whole file is taken.
    """
    root = _mini_repo(tmp_path, {
        "scripts/tests/test_iota.py": """
            from pathlib import Path
            REPO_ROOT = Path(__file__).resolve().parents[2]
            CORPUS = sorted(REPO_ROOT.rglob("*.md"))

            def test_one():
                assert CORPUS is not None

            def test_two():
                assert True
        """,
    })
    result = census_scan.analyze(root)
    assert result.nodeids == ["scripts/tests/test_iota.py"]
    assert result.whole_modules == ["scripts/tests/test_iota.py"]


def test_a_census_FIXTURE_pulls_in_the_tests_that_request_it(tmp_path):
    """A test can depend on the tree without naming anything that reads it."""
    root = _mini_repo(tmp_path, {
        "scripts/tests/test_kappa.py": """
            from pathlib import Path
            import pytest
            REPO_ROOT = Path(__file__).resolve().parents[2]

            @pytest.fixture
            def corpus():
                return sorted(REPO_ROOT.rglob("*.py"))

            def test_uses_it(corpus):
                assert corpus is not None

            def test_does_not(tmp_path):
                assert tmp_path.exists()
        """,
    })
    assert census_scan.census_nodeids(root) == [
        "scripts/tests/test_kappa.py::test_uses_it"
    ]


def test_a_NON_test_module_is_never_emitted_as_a_nodeid(tmp_path):
    """The library that does the scanning is not itself a selectable test.

    🔴 THE FIXTURE CARRIES BOTH SIDES ON PURPOSE, and the first version did not.
    Asserting `== []` over a tree containing only the non-test module is
    VACUOUS: a mutant that widened the filter so nothing at all was selected
    produced exactly `[]` and SURVIVED a green run. Measured — it is mutant M9
    of this change's battery, and it survived until this fixture gained a module
    that MUST be selected, so the expectation can no longer equal the broken
    answer.
    """
    root = _mini_repo(tmp_path, {
        "scripts/lib/scanner.py": """
            from pathlib import Path
            REPO_ROOT = Path(__file__).resolve().parents[2]

            def test_shaped_name_but_not_a_test_module():
                return list(REPO_ROOT.rglob("*"))
        """,
        "scripts/tests/test_real_one.py": """
            from pathlib import Path
            REPO_ROOT = Path(__file__).resolve().parents[2]

            def test_the_ledger_is_two_way():
                assert list(REPO_ROOT.rglob("*")) is not None
        """,
    })
    assert census_scan.census_nodeids(root) == [
        "scripts/tests/test_real_one.py::test_the_ledger_is_two_way"
    ]


def test_an_unparseable_module_is_REPORTED_not_swallowed(tmp_path):
    """A file the derivation could not read is a hole in the answer.

    Counting it as "nothing to select" would make a syntax error upstream read
    as a clean census.
    """
    root = _mini_repo(tmp_path, {
        "scripts/tests/test_broken.py": "def test_x(:\n    pass\n",
    })
    result = census_scan.analyze(root)
    assert result.unparseable == ["scripts/tests/test_broken.py"]


# ------------------------------------------------------------------- anchored

@pytest.fixture(scope="module")
def live() -> census_scan.CensusResult:
    """One analysis of THIS repo, shared — it is the expensive call here."""
    return census_scan.analyze(REPO_ROOT)


@pytest.mark.parametrize("anchor", INCIDENT_ANCHORS)
def test_the_derivation_still_sees_BOTH_historical_incidents(anchor, live):
    """🔴 POSITIVE CONTROL. Every synthetic test above passes on a derivation
    that has quietly stopped resolving this repo's real indirections; only this
    one goes red.

    If it fails because the test was legitimately renamed, update the anchor —
    but read the derivation first: the likelier cause is that the closure lost
    the path to `public_ip_scan.repo_files`.
    """
    assert anchor in live.nodeids, (
        f"{anchor} is no longer derived as a repo-census test. That test held "
        "`main` red for hours, and this check exists to catch it before a merge."
    )


def test_the_derivation_observes_a_population_worth_running(live):
    """A floor, so no assertion above can pass over near-nothing.

    Measured 240 at `10ae4da0`. The floor sits far below that — it is a
    wired-to-nothing detector, not a ratchet on repo growth.
    """
    assert len(live.nodeids) >= 60, (
        f"the derivation selected {len(live.nodeids)} nodeid(s); below this "
        "floor it is not describing the census population any more."
    )
    assert live.parsed >= 300, f"only {live.parsed} module(s) parsed"
    assert not live.unparseable, f"unparseable: {live.unparseable}"


def test_the_runners_floor_is_below_what_the_derivation_actually_returns(live):
    """The two numbers must not be able to drift apart silently.

    `ledger-check.sh` refuses (exit 3) below `MIN_NODEIDS`. If that literal ever
    exceeded the real population, the runner would refuse on every clean tree —
    a permanently-red gate, which this repo's rules call worse than none.
    """
    text = LEDGER_CHECK.read_text(encoding="utf-8")
    line = next(
        l for l in text.splitlines() if l.startswith("MIN_NODEIDS=")
    )
    floor = int(line.split("-")[-1].rstrip('}"'))
    assert floor < len(live.nodeids), (
        f"ledger-check.sh refuses below {floor} nodeids but the derivation "
        f"returns {len(live.nodeids)}: the runner can never pass."
    )


# 🔴 THE TWO TIERS RUN THIS FILE IN DIFFERENT TREES, AND ONLY ONE HAS A `.git`.
# `nix build .#checks.x86_64-linux.pytests` builds from a store copy with no git
# dir, so `git ls-files` exits **128** and an assertion reading that as "not
# tracked" turns the gate red on a message that is FALSE. This test did exactly
# that on `tekton/devrc-pytests` for this PR's own head (7d19f4c7): `not tracked
# by git: []` against a file that is tracked at mode 100755. Same shape already
# solved in `scripts/opencode/tests/test_dispatch.py` and
# `scripts/tests/test_load_test_harness.py`; this mirrors them.
def git_dir_present(root: Path) -> bool:
    """Which tier are we in? A FUNCTION of the tree, not a module constant.

    🔴 Deliberately not cached into a `GIT_DIR_PRESENT` global. A constant is a
    fixture that can only ever produce the value an assertion about it names, so
    a mutant hardcoding it to `True` survives on a dev host — where the probe
    returns `True` anyway — and the tier switch goes unpinned exactly where it
    matters. Every caller computes the tier from the tree it is looking at.

    🔴 `.git` is a FILE inside a worktree (it holds `gitdir: …`), and this repo
    is developed in worktrees, so `.exists()` and never `.is_dir()`.
    """
    return (root / ".git").exists()


def runner_ship_problems(root: Path, rel: str, git_present: bool) -> list[str]:
    """Reasons `rel` would not reach a consumer as a runnable script. [] == fine.

    Three checks, and only ONE of them is tier-conditional:

      * EXISTENCE is the meaningful proof INSIDE the sandbox. The store copy is
        built from tracked files, so an untracked file is simply not there.
      * The EXECUTABLE bit is meaningful in BOTH tiers — measured: `git archive`
        and the nix store copy each preserve mode 100755 — so it is asserted
        unconditionally rather than thrown away with the git half.
      * TRACKEDNESS is meaningful only on a DEV HOST, where the file exists on
        disk whether or not git has heard of it. That is the real hazard: the
        switch succeeds and the flake silently omits the file.

    `git_present` is a PARAMETER, not a module-level read, so both branches are
    exercisable from either tier — see the controls below.
    """
    path = root / rel
    if not path.is_file():
        return [f"{rel} is missing from this tree"]
    problems = []
    if not os.access(path, os.X_OK):
        problems.append(f"{rel} is not executable — `chmod +x` it")
    if not git_present:
        return problems          # nix sandbox tier: no .git, nothing more to ask
    p = subprocess.run(
        ["git", "-C", str(root), "ls-files", "--error-unmatch", "--", rel],
        capture_output=True, text=True, timeout=60)
    if p.returncode != 0:
        problems.append(f"{rel} is not git-tracked — `git add` it")
    return problems


def test_the_runner_is_tracked_and_executable():
    """A new file that is not `git add`ed is silently omitted by the flake.

    The switch SUCCEEDS and the file is simply not there — which for this file
    means `scoped-tests.sh` shells out to a missing checker.
    """
    assert runner_ship_problems(
        REPO_ROOT, "scripts/ledger-check.sh", git_dir_present(REPO_ROOT)) == []


def test_the_tier_probe_answers_BOTH_ways_from_either_tier(tmp_path: Path):
    """🔴 The pin on the tier switch, built so it cannot agree with itself.

    Both fixtures are constructed here, so this runs identically in the sandbox
    and on a dev host, and a mutant hardcoding the probe to either constant dies
    in one of the two arms.
    """
    (tmp_path / "with").mkdir()
    (tmp_path / "with" / ".git").write_text("gitdir: /elsewhere\n")   # worktree
    (tmp_path / "without").mkdir()
    assert git_dir_present(tmp_path / "with") is True
    assert git_dir_present(tmp_path / "without") is False


# --- controls on the tier guard -------------------------------------------- #
# 🔴 These build their own fixtures, so they run identically in BOTH tiers. A
# guard exercisable only on a dev host is a guard nobody re-checks after the
# sandbox breaks it — which is the failure this whole block is fixing.

def test_a_missing_runner_is_reported_in_either_tier(tmp_path: Path):
    """POSITIVE CONTROL. Without it, the `== []` above is indistinguishable from
    a helper wired to nothing."""
    assert runner_ship_problems(tmp_path, "nope.sh", False) == \
        ["nope.sh is missing from this tree"]
    assert runner_ship_problems(tmp_path, "nope.sh", True) == \
        ["nope.sh is missing from this tree"]


def test_a_DIRECTORY_at_the_runner_path_is_not_a_shipped_file(tmp_path: Path):
    """The claim is "this FILE ships". A directory standing where the script
    should be satisfies `exists()` — hence `is_file()`."""
    (tmp_path / "ledger-check.sh").mkdir()
    assert runner_ship_problems(tmp_path, "ledger-check.sh", False) == \
        ["ledger-check.sh is missing from this tree"]


def test_a_NON_EXECUTABLE_runner_is_reported_in_either_tier(tmp_path: Path):
    """🔴 The half that must NOT have been thrown away with the git half.

    `scoped-tests.sh` invokes the checker as `bash <path>`, but the deployed
    contract is an executable script; a lost mode bit is a real regression the
    sandbox CAN observe, so it is asserted there too.
    """
    script = mockbin.write_exec(tmp_path / "ledger-check.sh", "exit 0\n")
    script.chmod(0o644)          # write_exec leaves 0755; this is the point
    assert runner_ship_problems(tmp_path, "ledger-check.sh", False) == \
        ["ledger-check.sh is not executable — `chmod +x` it"]


def test_an_untracked_runner_is_reported_when_git_is_present(tmp_path: Path):
    """The check the sandbox CANNOT make, made here against a real git repo —
    proving the dev-host tier still catches the hazard the `.git` guard skips."""
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True,
                   capture_output=True)
    for name in ("tracked.sh", "untracked.sh"):
        mockbin.write_exec(tmp_path / name, "exit 0\n")
    subprocess.run(["git", "-C", str(tmp_path), "add", "tracked.sh"], check=True,
                   capture_output=True)
    assert runner_ship_problems(tmp_path, "tracked.sh", True) == []
    assert runner_ship_problems(tmp_path, "untracked.sh", True) == \
        ["untracked.sh is not git-tracked — `git add` it"]


def test_a_present_executable_runner_in_a_GIT_FREE_tree_reports_NOTHING(
        tmp_path: Path):
    """🔴 THE REGRESSION CASE — this is the sandbox, reproduced.

    It fails in an ORDINARY checkout the moment the `.git` guard is deleted,
    which is what stops the CI-red from silently coming back.
    """
    mockbin.write_exec(tmp_path / "ledger-check.sh", "exit 0\n")
    assert not (tmp_path / ".git").exists()
    assert runner_ship_problems(tmp_path, "ledger-check.sh", False) == []


# --------------------------------------------------------------- runner guards

def _run_checker(*args: str, env: dict | None = None, timeout: int = 300):
    """Drive `ledger-check.sh` itself.

    ⚠ `ledger-check.sh` is NOT one of `_RUNNER_TOKENS`, so this spawn is
    invisible to `test_runner_bound_ledger.py` and needs no ledger row. Do not
    "fix" that by adding one — a row for a site the discovery pass cannot see
    would go red as a phantom the first time the ledger is compared.

    The bound is its own and deliberately short of the 600 s that governs
    nested full suites: every case below refuses in seconds, so a long bound
    would turn a wedged run into a ten-minute stall for no coverage.
    """
    merged = dict(os.environ)
    merged.update(env or {})
    return subprocess.run(
        ["bash", str(LEDGER_CHECK), *args],
        capture_output=True, text=True, timeout=timeout, env=merged,
        cwd=str(REPO_ROOT))


def test_a_short_derivation_is_COULD_NOT_MEASURE_not_a_pass():
    """🔴 NEGATIVE CONTROL ON THE RUNNER. A selection that collapsed to a
    handful would run in a second and report a confident green over nothing.
    Driven by raising the floor rather than by breaking the derivation, so the
    refusal path is exercised without a mutation.
    """
    proc = _run_checker("--list", env={"DEVRC_LEDGER_MIN_NODEIDS": "100000"})
    assert proc.returncode == 3, (
        f"expected exit 3, got {proc.returncode}\n{proc.stdout}\n{proc.stderr}")
    assert "COULD NOT MEASURE" in proc.stderr
    assert "RESULT: COULD-NOT-MEASURE" in proc.stdout


def test_list_mode_does_not_RUN_the_selection():
    """A listing is not a verdict, and it must not cost one.

    🔴 RED BEFORE THE FIX: the re-exec into the dev shell forwarded `$@` AFTER
    the argument parser had shifted it empty, so `--list` re-entered as a full
    run and printed a 215-second "listing".
    """
    proc = _run_checker("--list")
    assert proc.returncode == 0, proc.stderr
    assert "LISTED ONLY — nothing ran" in proc.stdout
    assert "RESULT:" not in proc.stdout, (
        "list mode printed a verdict line; it ran the suite")
    for anchor in INCIDENT_ANCHORS:
        assert anchor in proc.stdout, f"{anchor} missing from --list output"


def test_an_unknown_argument_is_refused_rather_than_ignored():
    proc = _run_checker("--tier", "both")
    assert proc.returncode == 2
    assert "unknown argument" in proc.stderr


def test_a_run_that_printed_NO_VERDICT_is_refused_even_when_pytest_exits_0():
    """🔴 READ THE CONTENT, NOT THE EXIT CODE — driven, not asserted.

    `--collect-only` makes pytest print a collection line and exit **0** while
    running nothing. An exit-code check would call that a pass. Measured: 386
    tests collected, `pytest exit=0`, and the checker refuses at 3.

    This is the only case here that pays a real collection (~15 s of the ~50 s),
    and it is worth it: it is the exact shape — a green exit over a run that
    produced no verdict — that this repo has been burned by repeatedly.
    """
    proc = _run_checker("-j", "0", "--", "--collect-only", timeout=900)
    assert proc.returncode == 3, f"{proc.stdout[-2000:]}\n{proc.stderr[-2000:]}"
    assert "printed no parseable summary" in proc.stderr
    assert "(exit=0)" in proc.stderr, (
        "the refusal must show that pytest itself exited 0 — that is the point")
    assert "RESULT: COULD-NOT-MEASURE" in proc.stdout


def test_the_dev_shell_reexec_forwards_the_ORIGINAL_arguments():
    """STRUCTURAL PIN, and its limit is stated rather than papered over.

    🔴 WHAT IT CANNOT DO: the re-exec only fires when `python3 -m pytest` is
    NOT importable, and every test here runs inside the dev shell where it is.
    So no test in this file can EXERCISE that branch, and the regression it
    guards — the parser shifting `$@` empty before `exec` forwarded it, turning
    `--list` into a 215-second full run — is reachable only from an operator's
    direnv shell.

    Reading the source is therefore the strongest check available, and it is
    labelled as such: it pins that the exec forwards a variable captured BEFORE
    the parse loop, not that the forwarding works.
    """
    src = LEDGER_CHECK.read_text(encoding="utf-8")
    capture = src.index('ORIG_ARGS=("$@")')
    parse = src.index("while [ $# -gt 0 ]; do")
    exec_line = next(
        l for l in src.splitlines() if l.strip().startswith("exec nix develop"))
    assert capture < parse, (
        "ORIG_ARGS is captured AFTER the parser has already shifted $@ away")
    assert "ORIG_ARGS" in exec_line, (
        f"the re-exec does not forward the captured arguments: {exec_line!r}")
    assert '"$@"' not in exec_line, (
        f're-exec forwards a `$@` the parser has emptied: {exec_line!r}')
