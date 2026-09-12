"""`scoped-tests.sh` must run the repo-census guards when the FILE SET moves.

WHY, measured 2026-09-11/12. The scoped mapper selects test files that NAME what
you changed. A BRAND-NEW FILE NAMES NOTHING — so it maps to zero covering tests
while a census guard elsewhere goes red the moment it lands. `main` was red for
hours, twice in one session, on exactly that: `_KILL_MENTION_LEDGER` and
`_OWN_BOUND_LEDGER`, each broken by a file arriving without its row.

🔴 THE TRIGGER IS THE FILE SET, NOT THE DIFF. An edit-only iteration must pay
NOTHING — a check that taxed every save is one that gets switched off, and this
repo's rules already say a permanently-expensive gate trains everyone to bypass
it. So `test_a_MODIFY_only_diff_does_not_run_the_census_guards` is as
load-bearing as the positive cases: without it, a trigger that fired on
EVERYTHING would satisfy every other test here and be wrong.

🔴 THE REAL CHECKER IS NEVER RUN FROM HERE. Every case drives a STUB
`ledger-check.sh` inside a throwaway repo, which records that it was called and
exits with whatever the case needs. The real one takes minutes and its verdict
is about this repo, not about the wiring — a test that ran it would be slow AND
measuring the wrong thing.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCOPED = REPO_ROOT / "scripts" / "scoped-tests.sh"

sys.path.insert(0, str(REPO_ROOT / "scripts"))

from testlib import scoped_harness  # noqa: E402
from testlib.mockbin import write_exec  # noqa: E402

MARKER = "ledger-check-was-called"


def _run_scoped(repo: Path, *args: str) -> subprocess.CompletedProcess:
    """🔴 Through `scoped_harness.run`, never a bare `subprocess.run`.

    The harness supplies `RUNNER_TIMEOUT_S`, the ONE bound for runner traffic.
    A direct spawn with no `timeout=` scores `ABSENT` and reddens
    `_OWN_BOUND_LEDGER` the moment this file lands — which is the very incident
    this module is about, and it has already happened once to a file in this
    directory.
    """
    return scoped_harness.run(
        [str(SCOPED), "--base", "HEAD", *args, str(repo)], cwd=repo)


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, timeout=60)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A throwaway repo with a stub runner AND a stub ledger-check."""
    r = tmp_path / "r"
    (r / "scripts" / "tests").mkdir(parents=True)
    (r / "scripts" / "dl-router" / "tests").mkdir(parents=True)
    (r / "scripts" / "dl-router" / "server.py").write_text("# server\n")
    (r / "scripts" / "dl-router" / "tests" / "test_server.py").write_text(
        '"""Covers scripts/dl-router/server.py."""\n\n\ndef test_server():\n    pass\n'
    )
    (r / "scripts" / "tests" / "test_placeholder.py").write_text(
        "def test_placeholder():\n    pass\n"
    )
    write_exec(
        r / "scripts" / "run-tests.sh",
        'for a in "$@"; do\n'
        '  if [ "$a" = "--check-targets" ]; then\n'
        '    printf "  dir   scripts/tests\\n"\n'
        '    printf "  dir   scripts/dl-router/tests\\n"\n'
        "    exit 0\n"
        "  fi\n"
        "done\n"
        "echo 'stub runner ran'\n"
    )
    _stub_ledger(r, rc=0)
    _git(r, "init", "-q")
    _git(r, "add", "-A")
    _git(r, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "base")
    return r


def _stub_ledger(repo: Path, *, rc: int) -> None:
    """A ledger-check that records the call and exits `rc`."""
    write_exec(
        repo / "scripts" / "ledger-check.sh",
        f'echo "stub ledger-check"\ntouch "$(dirname "$0")/../{MARKER}"\nexit {rc}\n',
    )


def _was_called(repo: Path) -> bool:
    return (repo / MARKER).exists()


# ------------------------------------------------------------------ positives

def test_an_ADDED_file_runs_the_census_guards(repo: Path):
    """The exact shape of both incidents: a new file nobody's test names."""
    (repo / "scripts" / "tests" / "test_brand_new.py").write_text(
        '"""Covers scripts/dl-router/server.py."""\n\n\ndef test_new():\n    pass\n'
    )
    proc = _run_scoped(repo)
    assert _was_called(repo), (
        "a file entered the tree and the census guards did not run:\n"
        f"{proc.stdout}\n{proc.stderr}")
    assert "entered or left the tree" in proc.stdout


def test_a_DELETED_file_runs_the_census_guards(repo: Path):
    """A ledger pinned two-way fails when the set SHRINKS as well as grows."""
    (repo / "scripts" / "dl-router" / "server.py").unlink()
    (repo / "scripts" / "tests" / "test_placeholder.py").write_text(
        '"""Covers scripts/dl-router/server.py."""\n\n\ndef test_p():\n    pass\n'
    )
    proc = _run_scoped(repo)
    assert _was_called(repo), f"{proc.stdout}\n{proc.stderr}"


# ------------------------------------------------------------ negative control

def test_a_MODIFY_only_diff_does_not_run_the_census_guards(repo: Path):
    """🔴 WITHOUT THIS, A TRIGGER MATCHING EVERYTHING SCORES GREEN.

    It is also the cost argument: an iteration loop is almost entirely modifies,
    and paying minutes on every save is how this gets turned off.
    """
    (repo / "scripts" / "dl-router" / "server.py").write_text("# server v2\n")
    proc = _run_scoped(repo)
    assert not _was_called(repo), (
        "a content-only edit paid for the census guards:\n"
        f"{proc.stdout}\n{proc.stderr}")
    assert "tracked file SET is unchanged" in proc.stdout


# ------------------------------------------------------------------- refusals

def test_a_FAILING_census_guard_stops_the_run_before_anything_is_scoped(repo: Path):
    """🔴 STOPS, rather than reporting alongside.

    The script ends in `exec`, so a check placed after the runner could never
    run at all. Placed before, its failure must not be survivable: a scoped
    PASS printed under a red census guard is the false green this exists to
    prevent.
    """
    _stub_ledger(repo, rc=1)
    (repo / "scripts" / "tests" / "test_brand_new.py").write_text(
        '"""Covers scripts/dl-router/server.py."""\n\n\ndef test_new():\n    pass\n'
    )
    proc = _run_scoped(repo)
    assert proc.returncode == 1, f"{proc.stdout}\n{proc.stderr}"
    assert "STOPPED at the repo-census guards" in proc.stderr
    assert "stub runner ran" not in proc.stdout, (
        "the scoped run executed anyway after a red census guard")


def test_a_COULD_NOT_MEASURE_census_result_is_PROPAGATED_not_flattened(repo: Path):
    """rc 3 is "the check could not tell you", which is not rc 1 and not a pass.

    Collapsing it to 1 would send someone hunting a ledger row that is fine;
    collapsing it to 0 would hand them a green off an instrument that never ran.
    """
    _stub_ledger(repo, rc=3)
    (repo / "scripts" / "tests" / "test_brand_new.py").write_text(
        '"""Covers scripts/dl-router/server.py."""\n\n\ndef test_new():\n    pass\n'
    )
    proc = _run_scoped(repo)
    assert proc.returncode == 3, f"{proc.stdout}\n{proc.stderr}"


def test_no_ledgers_skips_LOUDLY_rather_than_silently(repo: Path):
    """An opt-out that says nothing is how the next red window starts."""
    (repo / "scripts" / "tests" / "test_brand_new.py").write_text(
        '"""Covers scripts/dl-router/server.py."""\n\n\ndef test_new():\n    pass\n'
    )
    proc = _run_scoped(repo, "--no-ledgers")
    assert not _was_called(repo)
    assert "repo-census guards SKIPPED" in proc.stdout
    assert "reddens main" in proc.stdout


def test_a_DRY_RUN_says_it_would_run_them_and_does_not(repo: Path):
    """A dry run must not cost minutes, and must not imply it checked."""
    (repo / "scripts" / "tests" / "test_brand_new.py").write_text(
        '"""Covers scripts/dl-router/server.py."""\n\n\ndef test_new():\n    pass\n'
    )
    proc = _run_scoped(repo, "--dry-run")
    assert not _was_called(repo)
    assert "would run scripts/ledger-check.sh" in proc.stdout


def test_a_MISSING_checker_says_so_rather_than_exiting_127(repo: Path):
    """🔴 FAILS OPEN, LOUDLY, and the loudness is the tested half.

    Several existing mapper fixtures are throwaway repos with no
    `ledger-check.sh`, and `bash <missing>` exits 127 — which would have failed
    two unrelated tests in `test_scoped_mapper.py` for a reason that has nothing
    to do with them. It did, on the first pass of this change.

    In THIS repo the checker is tracked and executable, pinned by
    `test_census_scan.py::test_the_runner_is_tracked_and_executable`, so the
    open direction is only ever reachable from a foreign tree.
    """
    (repo / "scripts" / "ledger-check.sh").unlink()
    (repo / "scripts" / "tests" / "test_brand_new.py").write_text(
        '"""Covers scripts/dl-router/server.py."""\n\n\ndef test_new():\n    pass\n'
    )
    proc = _run_scoped(repo)
    assert proc.returncode == 0, f"{proc.stdout}\n{proc.stderr}"
    assert "is MISSING — repo-census guards NOT run" in proc.stdout, proc.stdout


def test_the_shared_surface_refusal_still_wins_over_the_census_trigger(repo: Path):
    """Ordering: a shared-surface diff gets NO scoped verdict at all.

    Running the census guards first there would spend minutes and then refuse
    anyway, and would read as "something was checked".
    """
    (repo / "nix").mkdir(exist_ok=True)
    (repo / "nix" / "home.nix").write_text("{ }\n")
    proc = _run_scoped(repo)
    assert proc.returncode == 4, f"{proc.stdout}\n{proc.stderr}"
    assert not _was_called(repo)
    assert "SHARED SURFACE" in proc.stderr
