"""pytest plugin: record which lines of the guard files under test executed.

Loaded by `scripts/dead-guard-scan.py` via `-p dead_guard_plugin`. Reads
`DGS_TARGETS` (os.pathsep-separated absolute paths) and writes
`{"executed": {path: [linenos]}, "clobbered": bool, "tests": int}` JSON to
`DGS_OUT` at interpreter exit.

🔴 `coverage.py` IS NOT INSTALLED ON THIS HOST and is not a dependency of this
repo, so this uses stdlib `sys.settrace`. `threading.settrace` is set too --
without it a guard that scans in a worker thread reads as entirely dead, which
is the exact false positive that would discredit the tool on first contact.

🔴 THE TRACER IS RE-ARMED BEFORE EVERY TEST, AND ITS REMOVAL IS DETECTED.
`sys.settrace` is a single global slot: ANY test that installs its own tracer
and then clears it -- `sys.settrace(None)` in a `finally`, a debugger, a
profiler -- disarms this one for the whole REST of the session. Every guard
file collected after that point then reports its live branches as DEAD, on a
GREEN run, with nothing indicating it. That is the tool's worst failure mode
(condemning working code) arriving in its most believable disguise.

Arming once in `pytest_configure` was exactly that bug: this repo's own
`scripts/tests/test_dead_guard_scan.py` clears the tracer, and the census
escaped corruption only because that file happened to sort LAST -- an unpinned
invariant that one registry row would have broken. So:
  * re-arm in `pytest_runtest_setup`, before each test's call phase;
  * ALSO record whether the slot was found empty or foreign at that moment,
    and again at exit, and surface it as `clobbered` so the caller can refuse
    to publish rather than publish a false census. Re-arming alone is not
    enough -- a tracer cleared part-way THROUGH a test still loses that test's
    lines.

🔴 STATED BLIND SPOT: a test that SAVES AND RESTORES the tracer around its own
is invisible to this. The slot holds our tracer at every boundary we can
inspect, yet target lines executed inside that window were never recorded. That
is not hypothetical -- save-and-restore is the well-behaved pattern, and it is
what this repo's own suite does. The consequence is under-recording, i.e. a
FALSE POSITIVE against live code, and nothing here detects it. If a guard's
tests install a tracer, scan them separately or expect flags you must
adjudicate by hand.

🔴 THE TRACE IS WRITTEN FROM `atexit`, NOT FROM A pytest HOOK. A session that
dies on a collection error never reaches `pytest_sessionfinish`, and an absent
output file is indistinguishable from "nothing executed" -- a zero that reads
as a finding. `atexit` fires on both paths, so the caller can tell an empty
trace (real) from a missing one (the run never started).

Scoped to `DGS_TARGETS` only: tracing everything would cost minutes per run and
report ordinary application branches, which are not guards.
"""

import atexit
import json
import os
import pathlib
import sys
import threading

_TARGETS = {str(pathlib.Path(p).resolve())
            for p in os.environ.get("DGS_TARGETS", "").split(os.pathsep) if p}
_OUT = os.environ.get("DGS_OUT", "")
_executed: dict[str, set[int]] = {}
_state = {"clobbered": False, "tests": 0}


def _tracer(frame, event, arg):
    fn = frame.f_code.co_filename
    if fn in _TARGETS:
        if event == "line":
            _executed.setdefault(fn, set()).add(frame.f_lineno)
        return _tracer
    # Returning None declines to trace this frame's lines, but `call` events
    # for frames it invokes are still offered -- so a target called from a
    # non-target module is still seen.
    return None


def _arm():
    threading.settrace(_tracer)
    sys.settrace(_tracer)


def pytest_configure(config):
    if _TARGETS and _OUT:
        _arm()


def pytest_runtest_setup(item):
    """Re-arm per test, and remember if someone had taken the slot."""
    if not (_TARGETS and _OUT):
        return
    _state["tests"] += 1
    if sys.gettrace() is not _tracer:
        # Someone cleared or replaced our tracer during a previous test. Any
        # target executed in the interim was not recorded, so the whole trace
        # is now a LOWER BOUND and must not be published as a measurement.
        _state["clobbered"] = True
    _arm()


def pytest_runtest_teardown(item):
    """🔴 CHECK AFTER EACH TEST TOO, NOT AT INTERPRETER EXIT.

    Detection at `pytest_runtest_setup` alone can never see a clobber in the
    LAST test -- there is no next setup -- which is exactly the "it only
    survived because that file sorted last" shape this detector exists for.

    🔴 BUT THE CHECK MUST NOT LIVE IN `atexit`. `atexit` is LIFO, so any
    cleanup the TARGET REPO registered after this module was imported runs
    BEFORE the dump -- and an ordinary `atexit.register(lambda:
    sys.settrace(None))` in a library then trips the detector on a run whose
    trace was complete and correct. Measured: identical executed-line set to a
    clean baseline, yet `clobbered=true`, so the scan returned UNDECIDABLE and
    blamed "a test cleared sys.settrace" for something no test did. A
    permanently-red gate with a false cause. Teardown fires inside the run,
    before any of that.
    """
    if _TARGETS and _OUT and sys.gettrace() is not _tracer:
        _state["clobbered"] = True


@atexit.register
def _dump():
    if not _OUT:
        return
    sys.settrace(None)
    threading.settrace(None)
    pathlib.Path(_OUT).write_text(
        json.dumps({"executed": {k: sorted(v) for k, v in _executed.items()},
                    "clobbered": _state["clobbered"],
                    "tests": _state["tests"]}),
        encoding="utf-8")
