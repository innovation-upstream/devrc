#!/usr/bin/env python3
"""GUARD 9's sibling-worker exclusion, pinned as BEHAVIOUR.

🔴 WHY THIS FILE EXISTS. This predicate shipped INERT — unable to return True at
all — and a full green gate did not notice, twice. An audit mutation sweep then
showed the exact inert behaviour (`return False`) still SURVIVING the whole
suite, so the same defect was one edit away from shipping a third time.

What it decides is not cosmetic: with siblings misread as foreign co-tenants,
every worker drops out of ENFORCE and GUARD 9 stops failing the test that
touched the repository. Measured, cwd inside the protected repo at -n 4:

    working:  gw0..gw3  cotenants=0  -> all four workers ENFORCE
    inert:    gw0..gw3  cotenants=3  -> all four workers REPORT (guard off)

So the tests below pin BOTH directions. A predicate that always says False and
one that always says True must each fail here.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from testlib.gitenv import (  # noqa: E402
    _is_sibling_xdist_worker,
    _own_xdist_run_id,
    _ppid_of,
)


def test_a_process_sharing_our_parent_IS_a_sibling() -> None:
    """/proc/self has our ppid by definition, so it must match the predicate.

    🔴 THE `return False` MUTANT DIES HERE. That is the exact behaviour the
    shipped-inert version had, and nothing in the suite caught it before.
    """
    assert _is_sibling_xdist_worker(Path("/proc/self"), "any-id", os.getppid())


def test_a_process_we_SPAWNED_is_not_a_sibling() -> None:
    """🔴 THE `return True` MUTANT DIES HERE, and this is the case that matters
    for correctness rather than for the speedup.

    A subprocess a TEST spawns inherits our whole environment. If the predicate
    excludes it, the detector goes blind to a real writer — which is precisely
    what `test_live_cotenants_sees_another_process_in_the_repo` constructs on
    purpose and requires the probe to SEE.
    """
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(5)"])
    try:
        # settle: /proc/<pid>/stat is readable as soon as the pid exists, but
        # give the exec a moment so `comm` and the stat layout are stable.
        time.sleep(0.1)
        assert _ppid_of(Path(f"/proc/{child.pid}")) == os.getpid(), (
            "fixture precondition: the child's parent must be THIS process"
        )
        assert not _is_sibling_xdist_worker(
            Path(f"/proc/{child.pid}"), "any-id", os.getppid()
        )
    finally:
        child.kill()
        child.wait(timeout=10)


def test_an_unreadable_proc_entry_claims_nothing() -> None:
    """Fail toward SEEING a candidate: an entry we cannot read is not ours."""
    assert not _is_sibling_xdist_worker(Path("/proc/nonexistent-pid"), "id",
                                        os.getppid())


def test_the_predicate_does_not_read_the_run_id_from_another_proc() -> None:
    """The bug that made it inert, pinned as a property.

    xdist assigns PYTEST_XDIST_TESTRUNUID at RUNTIME, so it is NOT in
    /proc/<pid>/environ. Any implementation that consults that file for the id
    can never return True — so passing a deliberately wrong id must not change
    the verdict.
    """
    ours = _is_sibling_xdist_worker(Path("/proc/self"), "id-a", os.getppid())
    other = _is_sibling_xdist_worker(Path("/proc/self"), "id-b", os.getppid())
    assert ours is other is True


@pytest.mark.skipif(os.environ.get("PYTEST_XDIST_WORKER") is None,
                    reason="only meaningful inside a real xdist worker")
def test_a_real_worker_reports_a_run_id() -> None:
    """Positive control for the caller's gate, live inside a worker."""
    assert _own_xdist_run_id() is not None


def test_an_INHERITED_run_id_does_not_make_us_a_worker() -> None:
    """🔴 A worker's children inherit the id; they are not workers.

    Without this, a nested pytest launched from a test would take the worker
    branch and exclude its parent worker's other children. The discriminator is
    that xdist sets the id at RUNTIME (absent from /proc/self/environ) while an
    inherited one arrived through exec (present in both).
    """
    env = {**os.environ, "PYTEST_XDIST_TESTRUNUID": "inherited-through-exec"}
    out = subprocess.run(
        [sys.executable, "-c",
         "import sys; sys.path.insert(0, %r);"
         "from testlib.gitenv import _own_xdist_run_id;"
         "print(_own_xdist_run_id())" % str(REPO_ROOT / "scripts")],
        capture_output=True, text=True, env=env, timeout=60)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "None", (
        f"a process that INHERITED the run id through exec must not be treated "
        f"as a worker, got {out.stdout.strip()!r}"
    )
