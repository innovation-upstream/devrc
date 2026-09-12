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
    """The library that does the scanning is not itself a selectable test."""
    root = _mini_repo(tmp_path, {
        "scripts/lib/scanner.py": """
            from pathlib import Path
            REPO_ROOT = Path(__file__).resolve().parents[2]

            def test_shaped_name_but_not_a_test_module():
                return list(REPO_ROOT.rglob("*"))
        """,
    })
    assert census_scan.census_nodeids(root) == []


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


def test_the_runner_is_tracked_and_executable():
    """A new file that is not `git add`ed is silently omitted by the flake."""
    out = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "ls-files", "--", "scripts/ledger-check.sh"],
        capture_output=True, text=True, timeout=60).stdout.split()
    assert out == ["scripts/ledger-check.sh"], f"not tracked by git: {out!r}"
    assert os.access(LEDGER_CHECK, os.X_OK), "ledger-check.sh is not executable"


# --------------------------------------------------------------- runner guards

def _run_checker(*args: str, env: dict | None = None, timeout: int = 300):
    """Drive `ledger-check.sh` itself.

    🔴 Its own bound, and deliberately short of the 600 s that governs nested
    full suites: every case below is expected to refuse in seconds, so a long
    bound would turn a wedged run into a ten-minute stall for no coverage.
    See `scripts/tests/test_runner_bound_ledger.py` — this site is recorded
    there.
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
