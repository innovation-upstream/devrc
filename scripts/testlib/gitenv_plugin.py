"""GUARD 9's pytest half: strip the git repo pointers, then WATCH.

Loaded two ways, exactly like `nolaunch_plugin` and `spool_plugin`:

  * `scripts/run-tests.sh` puts `-p testlib.gitenv_plugin` on the ONE pytest
    line every target goes through, so all seventeen get it rather than the one
    directory that happens to have a conftest.
  * `scripts/tests/conftest.py` imports it, so a bare `pytest scripts/tests`
    outside the runner is protected too — by the same module, not a second copy.

Two layers, and they answer different questions:

  LAYER 1 — SANITISE (prevention). Strips `REPO_POINTER_VARS` from `os.environ`
  at IMPORT time, before pytest collects anything. Import time matters: several
  suites build real git repos at MODULE scope (`test_bash_guard.py::_mkrepo`,
  `test_guard_core.py`'s module-scoped cwd fixtures), which run during
  collection, and a fixture-scoped strip would land after them.

  LAYER 2 — DETECT (attribution). Fingerprints the protected repositories and
  compares around collection, around every test, and at session end. Layer 1
  closes the mechanism we MEASURED; layer 2 is what catches the next one, by
  any mechanism, and names the test that did it. Neither is sufficient: a strip
  with no detector is a claim, and a detector with no strip is an incident
  report.

🔴 The strip runs at import, NOT in `pytest_configure`, and that ordering is
load-bearing rather than stylistic — see above.
"""
from __future__ import annotations

import os

import pytest

from testlib.gitenv import (  # noqa: F401  (re-exported for tests)
    PROTECT_ENV,
    REPO_POINTER_VARS,
    SESSION_MARKER,
    VIOLATION_TOKEN,
    diff_snapshots,
    global_config_paths,
    protected_git_dirs,
    snapshot,
    strip_repo_pointers,
    violation_message,
)

# --- LAYER 1, at import ------------------------------------------------------ #
STRIPPED: dict[str, str] = strip_repo_pointers()

# --- LAYER 2's state --------------------------------------------------------- #
_GIT_DIRS = protected_git_dirs()
_EXTRA = global_config_paths()


def _take() -> "dict[str, str]":
    return snapshot(_GIT_DIRS, _EXTRA)


# 🔴 ARMED AT IMPORT, not in `pytest_configure`. `pytest_configure` is an
# INITIALISATION hook: pytest calls it for plugins given with `-p` and for the
# rootdir conftest, but NOT for a conftest loaded later during collection. The
# conftest entry point (`scripts/tests/conftest.py`) is exactly that case, so a
# baseline taken only in `pytest_configure` would be an empty dict there and the
# first `_check` would report every protected file as CREATED. Taking it here
# makes both entry points identical, and `pytest_configure` merely re-arms it
# and prints the marker.
_BASELINE: dict[str, str] = _take()


def _check(where: str) -> None:
    """Compare against the baseline and FAIL LOUDLY on any delta.

    The baseline is re-armed after a violation is reported, deliberately: the
    next test is not responsible for damage this one did, and a guard that
    re-reports the same delta for every remaining test buries the attribution
    it exists to provide.
    """
    global _BASELINE
    if not _GIT_DIRS and not _EXTRA:
        return
    after = _take()
    deltas = diff_snapshots(_BASELINE, after)
    if not deltas:
        return
    _BASELINE = after
    pytest.fail(violation_message(where, deltas, _GIT_DIRS), pytrace=False)


def pytest_configure(config):
    global _BASELINE
    _BASELINE = _take()
    # ONE marker line per pytest session. A target that never prints it is a
    # target this guard never loaded in, and "no marker" is otherwise
    # indistinguishable from "loaded, saw nothing" — the reassuring zero
    # claude/RULES.md's positive-control rule is about. It also reports the
    # PAIR: what was stripped, and how many repos are being watched.
    stripped = ",".join(sorted(STRIPPED)) or "none"
    watching = len(_GIT_DIRS)
    print(f"{SESSION_MARKER} stripped={stripped} protected-git-dirs={watching}")


def pytest_collection_finish(session):
    # Module-scope repo building happens HERE, not inside a test, so a check
    # that only ran per-test would attribute import-time damage to whichever
    # test happened to run first — or miss it entirely in a collect-only run.
    _check("collection (a module-level fixture, at import)")


@pytest.fixture(autouse=True)
def _devrc_git_repo_isolation(request):
    yield
    _check(f"test `{request.node.nodeid}`")


def pytest_sessionfinish(session, exitstatus):
    _check("session teardown (after the last test)")


def _os_environ_is_clean() -> bool:
    """Exposed for the controls: nothing re-set a pointer after the strip."""
    return not any(name in os.environ for name in REPO_POINTER_VARS)
