"""GUARD 9's pytest half: strip the git repo pointers, then WATCH.

Loaded two ways, exactly like `nolaunch_plugin` and `spool_plugin`:

  * `scripts/run-tests.sh` puts `-p testlib.gitenv_plugin` on the ONE pytest
    line every target goes through, so all of them get it rather than the one
    directory that happens to have a conftest.
  * a conftest in each test directory that builds real git repositories imports
    it, so a bare `pytest <dir>` outside the runner is protected too — by the
    same module, not a second copy. The ledger of those conftests is pinned by
    `test_the_conftest_entry_points_are_a_pinned_ledger`.

Two layers, and they answer different questions:

  LAYER 1 — SANITISE (prevention), a second time and deliberately so. The
  shell side already cleared these at the top of whichever runner started this
  process, before it resolved its ROOT; this repeats it for a BARE `pytest` that
  never went through a runner. Strips `REPO_POINTER_VARS` from `os.environ`
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

🔴 LAYER 2 HAS TWO MODES, AND THE REASON IS MEASURED (#683 audit, finding A).
The detector watches a repository it does not own; on the operator's box that
clone has dozens of concurrent writers. Blaming a test for their writes, in a
message that leads with "This is the 2026-08-21 incident's shape", is a
permanently-red gate that misdirects during the very incident it names. So the
detector now looks for POSITIVE EVIDENCE of another writer, from three
independent probes, and downgrades to reporting when it finds any:

  * `live_cotenants()` at import — a live process, not one of our ancestors,
    whose CWD is inside a protected work tree.
  * THE IDLE PROBE — a delta observed between `pytest_runtest_logfinish` and
    the next `pytest_runtest_logstart`. Nothing of the test protocol runs in
    that window (setup, call and teardown all sit between logstart and
    logfinish), so a change there was made by somebody else. This is the one
    airtight probe of the three.
  * THE SETTLE RE-READ — on any delta, the fingerprint is taken again
    immediately and again after `SETTLE_SECONDS`, with no test running. A repo
    that keeps moving has another writer.

Report mode prints the FULL delta under `OBSERVED_MARKER` and counts it into
the session-end summary; it never fails a test, and it deliberately does not
carry `VIOLATION_TOKEN`. Prevention (layer 1) is untouched by the mode.
"""
from __future__ import annotations

import os
import time

import pytest

from testlib.gitenv import (  # noqa: F401  (re-exported for tests)
    CONTROL_VARS,
    FOREIGN_MARKER,
    MODE_AUTO,
    MODE_ENFORCE,
    MODE_ENV,
    MODE_REPORT,
    OBSERVED_MARKER,
    PROTECT_ENV,
    REPO_POINTER_VARS,
    SESSION_MARKER,
    VIOLATION_TOKEN,
    attribution_evidence,
    diff_snapshots,
    global_config_paths,
    observation_message,
    protected_git_dirs,
    requested_mode,
    snapshot,
    strip_repo_pointers,
    violation_message,
)

# How long to wait before the second settle re-read. Paid ONLY when a delta was
# seen, which on a healthy run is never — so this is not a per-test cost.
SETTLE_SECONDS = 0.25

# --- LAYER 1, at import ------------------------------------------------------ #
STRIPPED: dict[str, str] = strip_repo_pointers()

# --- LAYER 2's state --------------------------------------------------------- #
# Both of these RAISE on a malformed seam rather than degrading to a green run
# under a reassuring marker line (gitenv.GitEnvConfigError; see finding B).
_GIT_DIRS = protected_git_dirs()
_MODE = requested_mode()
_EXTRA = global_config_paths()

# Reasons attribution is impossible. Seeded from the import-time probe and
# appended to when the idle probe or a settle re-read proves another writer.
_UNATTRIBUTABLE: list[str] = attribution_evidence(_GIT_DIRS)

_OBSERVED = 0      # deltas reported without attribution
_VIOLATIONS = 0    # deltas attributed to a test (enforce mode only)


def _take() -> "dict[str, str]":
    return snapshot(_GIT_DIRS, _EXTRA)


# 🔴 ARMED AT IMPORT, not in `pytest_configure`. Damage done while a module is
# being imported during collection has to be measured against a baseline that
# predates the import. `pytest_configure` merely re-arms it and prints the
# marker.
_BASELINE: dict[str, str] = _take()


_CONFIG = None  # set in pytest_configure; used only to bypass output capture


def _say(text: str) -> None:
    """Print OUTSIDE pytest's output capture.

    🔴 MEASURED, and it is the difference between a report and a swallowed one:
    a delta is normally detected in the autouse fixture's teardown, and pytest
    discards captured output for a test that PASSED — which report mode's tests
    do, by design. A plain `print` there produced `unattributed-observations=1`
    in the session summary and not one word about what actually moved. The
    capture manager is asked to stand down for the duration, exactly as pytest's
    own live-log and warning plugins do; if it is unavailable (a bare
    `pytest -p no:capture`, another plugin), the print still happens.
    """
    manager = None
    if _CONFIG is not None:
        manager = _CONFIG.pluginmanager.getplugin("capturemanager")
    if manager is None:
        print(text)
        return
    with manager.global_and_fixture_disabled():
        print(text)


def _effective_mode() -> str:
    if _MODE != MODE_AUTO:
        return _MODE
    return MODE_REPORT if _UNATTRIBUTABLE else MODE_ENFORCE


def _mark_foreign(reason: str) -> None:
    """Record PROVEN evidence of another writer and announce the downgrade once."""
    global _UNATTRIBUTABLE
    was_attributable = not _UNATTRIBUTABLE
    _UNATTRIBUTABLE.append(reason)
    if was_attributable:
        _say(f"{FOREIGN_MARKER} {reason}\n"
             f"{FOREIGN_MARKER} GUARD 9 can no longer attribute a change in a "
             "protected repository to a test; it will REPORT deltas instead of "
             "failing on them. The pointer strip (prevention) is unaffected.")


def _report(where: str, deltas: "list[str]", *, in_a_test: bool = True) -> None:
    """Fail or report, according to the mode.

    🔴 `in_a_test=False` is not cosmetic. `pytest.fail` raised from
    `pytest_collection_finish` or `pytest_sessionfinish` is an exception thrown
    from a HOOK, which pytest reports as `INTERNALERROR>` and exits 3 with
    **zero tests run** — measured on #683's code, where one neighbouring
    `git branch` landing during collection aborted the entire suite that way.
    `pytest.exit` is the supported route: same non-zero exit, same message,
    reported as a decision instead of a crash.
    """
    global _OBSERVED, _VIOLATIONS
    if _effective_mode() == MODE_ENFORCE:
        _VIOLATIONS += 1
        message = violation_message(where, deltas, _GIT_DIRS)
        if in_a_test:
            pytest.fail(message, pytrace=False)
        _say(message)
        pytest.exit(f"{VIOLATION_TOKEN}: see the message above ({where}).",
                    returncode=1)
    _OBSERVED += 1
    _say(observation_message(where, deltas, _GIT_DIRS, _UNATTRIBUTABLE))


def _check(where: str, *, in_a_test: bool = True) -> None:
    """Compare against the baseline and act on any delta.

    🔴 THE BASELINE IS RE-ARMED ON EVERY CALL, not only after a violation. With
    a rolling baseline that only moved on failure, the window a delta could have
    arrived in was the WHOLE SESSION, so the first test to finish after any
    change — a concurrent agent's `git branch` included — wore it. Re-arming
    every time makes the window one test.

    🔴 AND THE DELTA IS RE-READ BEFORE ANYTHING IS ASSERTED. Twice: immediately,
    and again after `SETTLE_SECONDS`. No test body is running during either, so
    a repository that moves again has, provably, another writer.
    """
    global _BASELINE
    after = _take()
    deltas = diff_snapshots(_BASELINE, after)
    if not deltas:
        _BASELINE = after
        return

    # 🔴 THE SETTLE COSTS NOTHING ONCE THE ANSWER IS KNOWN. Its only job is to
    # DISCOVER a co-writer; if one has already been proven, re-reading buys
    # nothing and the sleep is per-delta. Measured: a 40-test run against a
    # repository being written every 40ms took 12.2s with the settle applied
    # unconditionally and 2.3s with this guard — the same verdict either way.
    if _effective_mode() == MODE_ENFORCE:
        for label, pause in (("an immediate re-read", 0.0),
                             (f"a {SETTLE_SECONDS}s settle", SETTLE_SECONDS)):
            if pause:
                time.sleep(pause)
            again = _take()
            if diff_snapshots(after, again):
                _mark_foreign(
                    f"the repository changed AGAIN during {label}, while no test was "
                    "running — something outside this pytest session is writing to it"
                )
            after = again
    deltas = diff_snapshots(_BASELINE, after)
    _BASELINE = after
    if not deltas:  # everything settled back to the baseline
        return
    _report(where, deltas, in_a_test=in_a_test)


def pytest_configure(config):
    global _BASELINE, _CONFIG
    _CONFIG = config
    _BASELINE = _take()
    # ONE marker line per pytest session, and `run-tests.sh` COUNTS it per
    # target: a target that never prints it is a target this guard never loaded
    # in, and "no marker" is otherwise indistinguishable from "loaded, saw
    # nothing" — the reassuring zero claude/RULES.md's positive-control rule is
    # about. It reports the PAIR: what was stripped, how many repos are watched,
    # and — because a count alone cannot say whether the count is trustworthy —
    # the mode and why.
    stripped = ",".join(sorted(STRIPPED)) or "none"
    print(f"{SESSION_MARKER} stripped={stripped} "
          f"protected-git-dirs={len(_GIT_DIRS)} "
          f"mode={_effective_mode()}({_MODE}) "
          f"unattributable={len(_UNATTRIBUTABLE)}")
    for reason in _UNATTRIBUTABLE:
        print(f"{SESSION_MARKER}   cannot attribute: {reason}")


def pytest_collection_finish(session):
    # Module-scope repo building happens HERE, not inside a test, so a check
    # that only ran per-test would attribute import-time damage to whichever
    # test happened to run first — or miss it entirely in a collect-only run.
    _check("collection (a module-level fixture, at import)", in_a_test=False)


def pytest_runtest_logstart(nodeid, location):
    """🔴 THE IDLE PROBE — the one airtight "it wasn't us" measurement.

    Setup, call and teardown all happen between `logstart` and `logfinish`, so
    the window between the previous test's `logfinish` and this `logstart` runs
    no test code at all. A delta observed here was written by another process,
    full stop — and that PROVES this repository has a co-writer for the rest of
    the session.
    """
    global _BASELINE
    after = _take()
    deltas = diff_snapshots(_BASELINE, after)
    _BASELINE = after
    if not deltas:
        return
    _mark_foreign(
        "a protected repository changed BETWEEN tests, when no test body, "
        "fixture or teardown was running"
    )
    _report(f"the idle window before `{nodeid}`", deltas, in_a_test=False)


@pytest.fixture(autouse=True)
def _devrc_git_repo_isolation(request):
    yield
    _check(f"test `{request.node.nodeid}`")


def pytest_sessionfinish(session, exitstatus):
    _check("session teardown (after the last test)", in_a_test=False)
    if _OBSERVED or _VIOLATIONS:
        print(f"{SESSION_MARKER} summary: attributed-violations={_VIOLATIONS} "
              f"unattributed-observations={_OBSERVED} "
              f"mode={_effective_mode()}({_MODE})")
