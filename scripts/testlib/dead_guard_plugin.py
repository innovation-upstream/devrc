"""pytest plugin: record which lines of the guard files under test executed.

Loaded by `scripts/dead-guard-scan.py` via `-p dead_guard_plugin`. Reads
`DGS_TARGETS` (os.pathsep-separated absolute paths) and writes
`{"executed": {path: [linenos]}, "clobbered": bool, "tests": int}` JSON to
`DGS_OUT` at interpreter exit.

🔴 `coverage.py` IS NOT INSTALLED ON THIS HOST and is not a dependency of this
repo, so this uses stdlib `sys.settrace`. `threading.settrace` is set too --
without it a guard that scans in a worker thread reads as entirely dead, which
is the exact false positive that would discredit the tool on first contact.

🔴 THE TRACER IS ARMED ONCE AND ITS REMOVAL IS DETECTED AT SESSION END.
`sys.settrace` is a single global slot: ANY test that installs its own tracer
and then clears it -- `sys.settrace(None)` in a `finally`, a debugger, a
profiler -- disarms this one for the rest of the session. Every guard file
collected after that point then reports its live branches as DEAD, on a GREEN
run, with nothing indicating it. That is the tool's worst failure mode
(condemning working code) in its most believable disguise, so a run in which it
happened is REFUSED rather than published. See `pytest_sessionfinish` for why
that is one check and not four.


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
_state = {"clobbered": False}


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


def pytest_sessionfinish(session, exitstatus):
    """🔴 ONE DETECTION SITE, BECAUSE THE OTHER THREE WERE REDUNDANT.

    An earlier revision armed the tracer again before EVERY test and checked
    the slot at `pytest_runtest_setup`, at `pytest_runtest_teardown` AND at
    interpreter exit. A mutation sweep showed each of those individually
    deletable with the whole suite still green -- they were catching the same
    cases as one another, which is the redundant-guard trap: "each died" is a
    much weaker claim than "each died for its own reason".

    The reasoning that collapses them: a clobber this instrument can DETECT is
    one that is never put back, and such a clobber persists to session end. So
    checking once here catches every detectable case -- a clobber in a test
    body, in a fixture finalizer, in the last test (where teardown-only
    checking was blind), and one left by a plugin. Per-test re-arming bought
    nothing, because a clobbered run is REFUSED, never repaired: this reports,
    it does not recover.

    🔴 NOT AT `atexit`. `atexit` is LIFO, so a target module's own
    `atexit.register(lambda: sys.settrace(None))` -- ordinary library cleanup
    -- runs BEFORE the dump and tripped the detector on a run whose trace was
    complete and correct, returning UNDECIDABLE with a false cause. Session end
    is inside the run, before any of that.

    🔴 STATED BLIND SPOT: a test that SAVES AND RESTORES the tracer around its
    own is invisible here and to any slot inspection -- the slot holds our
    tracer at every boundary, while target lines executed inside that window
    went unrecorded. That is the well-behaved pattern, and it is what this
    repo's own suite does. The consequence is under-recording, i.e. a FALSE
    POSITIVE against live code. If a guard's tests install a tracer, scan them
    separately or adjudicate their flags by hand.
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
                    "clobbered": _state["clobbered"]}),
        encoding="utf-8")
