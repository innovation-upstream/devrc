"""pytest plugin: record which lines of the guard files under test executed.

Loaded by `scripts/dead-guard-scan.py` via `-p dead_guard_plugin`. Reads
`DGS_TARGETS` (os.pathsep-separated absolute paths) and writes
`{path: [linenos]}` JSON to `DGS_OUT` at interpreter exit.

🔴 `coverage.py` IS NOT INSTALLED ON THIS HOST and is not a dependency of this
repo, so this uses stdlib `sys.settrace`. `threading.settrace` is set too --
without it a guard that scans in a worker thread reads as entirely dead, which
is the exact false positive that would discredit the tool on first contact.

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


def pytest_configure(config):
    if _TARGETS and _OUT:
        threading.settrace(_tracer)
        sys.settrace(_tracer)


@atexit.register
def _dump():
    if not _OUT:
        return
    sys.settrace(None)
    threading.settrace(None)
    pathlib.Path(_OUT).write_text(
        json.dumps({k: sorted(v) for k, v in _executed.items()}),
        encoding="utf-8")
