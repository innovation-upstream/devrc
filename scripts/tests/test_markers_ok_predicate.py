#!/usr/bin/env python3
"""GUARD 7/8/10's session-marker predicate, pinned by its TRUTH TABLE.

🔴 WHY THIS FILE EXISTS. `run-tests.sh` used to assert `markers -ne 1` at three
open-coded sites. The xdist change replaced all three with one `_markers_ok`
helper whose parallel branch accepts a RANGE — and an audit found the loosened
predicate had no test at all, which meant these mutants survived the full
15,887-test suite:

  * `-ge 1` -> `-ge 0`  : ZERO markers pass. That is precisely the state all
    three guards exist to catch — "this target ran without the plugin, so its
    clean result is a claim about nothing".
  * upper bound raised  : a stray un-flagged NESTED pytest session, which the
    *_IN_SESSION flags exist to prevent, stops being visible.
  * the two branches swapped: only a serial run notices, and CI does not run one.

Deleting the plugin to watch a guard fire (the mutation that WAS run) only
exercises the far end of the lower bound. This pins the whole table.

🔴 IT EXERCISES THE REAL CODE, not a transcription. The functions are extracted
from `run-tests.sh` itself and sourced, so editing the predicate in the runner
without updating this table fails here. A copy of the logic would pass forever.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))
RUN_TESTS = REPO_ROOT / "scripts" / "run-tests.sh"


def _extract(name: str) -> str:
    """The literal text of one shell function, from `run-tests.sh` on disk."""
    src = RUN_TESTS.read_text(encoding="utf-8")
    m = re.search(rf"^{re.escape(name)}\(\) \{{\n(.*?)^\}}\n", src,
                  re.MULTILINE | re.DOTALL)
    assert m, (
        f"could not find `{name}()` in {RUN_TESTS}. If it was renamed or "
        f"reshaped, update this extractor — do NOT delete the test, which would "
        f"silently drop the only coverage this predicate has."
    )
    return f"{name}() {{\n{m.group(1)}}}\n"


def _ask(jobs: int, count: int) -> bool:
    """Run the REAL `_markers_ok` at `PYTEST_JOBS=jobs` against `count`."""
    script = (
        "set -u\n"
        f"PYTEST_JOBS={jobs}\n"
        + _extract("_markers_ok")
        + f'if _markers_ok {count}; then echo OK; else echo NO; fi\n'
    )
    out = subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                         timeout=30)
    assert out.returncode == 0, out.stderr
    verdict = out.stdout.strip().splitlines()[-1]
    assert verdict in ("OK", "NO"), out.stdout
    return verdict == "OK"


# (jobs, count, accepted)
TABLE = [
    # --- serial: EXACTLY one, unchanged from before parallelism existed ------
    (1, 0, False),   # the plugin never loaded — the whole point of the guard
    (1, 1, True),
    (1, 2, False),   # a stray nested session pollutes this target's ledger
    (1, 5, False),
    # --- parallel: one marker per worker that ran a test, so 1..JOBS ---------
    (4, 0, False),   # 🔴 the `-ge 1` -> `-ge 0` mutant dies here
    (4, 1, True),    # fewer than JOBS is legitimate: loadfile can idle a worker
    (4, 3, True),
    (4, 4, True),
    (4, 5, False),   # 🔴 the old `JOBS + 1` bound admitted this; it cannot exist
    (4, 9, False),
    (2, 2, True),
    (2, 3, False),
]


@pytest.mark.parametrize(("jobs", "count", "accepted"), TABLE)
def test_markers_ok_truth_table(jobs: int, count: int, accepted: bool) -> None:
    got = _ask(jobs, count)
    assert got is accepted, (
        f"_markers_ok at PYTEST_JOBS={jobs} with {count} marker(s) returned "
        f"{'accept' if got else 'reject'}, expected "
        f"{'accept' if accepted else 'reject'}."
    )


def test_zero_is_rejected_at_every_job_count() -> None:
    """The one row that must never become permissive, stated separately.

    A regression here is not "the bound is slightly off" — it is every one of
    GUARD 7, 8 and 10 passing a target the plugin never loaded in.
    """
    for jobs in (1, 2, 4, 8, 16):
        assert _ask(jobs, 0) is False, f"zero markers accepted at jobs={jobs}"


def test_expected_string_does_not_promise_a_controller_marker() -> None:
    """The operator-facing string must not restate a mechanism that is false.

    An earlier revision said "controller + up to N xdist workers". The markers
    come from session-scoped autouse fixtures, and the xdist controller runs no
    tests, so it never emits one — measured count at -n 4 is exactly 4.
    """
    script = ("set -u\nPYTEST_JOBS=4\n" + _extract("_markers_expected")
              + "_markers_expected\n")
    out = subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                         timeout=30)
    assert out.returncode == 0, out.stderr
    text = out.stdout.strip()
    assert "controller" not in text.lower(), text
    assert "5" not in text, f"upper bound must be JOBS (4), not JOBS+1: {text}"
    assert "4" in text, text
