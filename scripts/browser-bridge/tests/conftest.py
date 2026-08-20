"""Suite-wide telemetry containment: no browser-bridge test writes to the REAL
activity spool.

🔴 THE ISOLATION FIXTURE LIVES HERE, NOT IN A TEST MODULE, AND THAT IS THE WHOLE
POINT. `server.emit_cmd_event()` fires one activity event per handled command;
the row lands in `<ACTIVITY_SPOOL_DIR>/current.log`, and the activity collector
then ships that spool to the production ClickHouse `activity.events`. An
identical `autouse` fixture used to sit inside `test_server.py` carrying a
docstring that claimed it protected "EVERY test's" telemetry writes — but
**pytest scopes a module-declared fixture to that module**, so it protected one
file out of six and the five siblings appended real rows to production
(`test_site_notes.py` alone: +17 production rows per run, reproduced twice). A
docstring claiming a scope its declaration site cannot deliver is worse than
none — it stops anyone looking.

THE LEVER IS THE ENV VAR, NOT THE CACHE RESET. `spool_emit.default_spool_dir()`
reads `ACTIVITY_SPOOL_DIR` **at call time**, so pointing it at a temp dir
redirects the write no matter which emitter module object got loaded, when it
was loaded, or whether the emitting code runs in this process at all — a
subprocess (`browser` CLI, `browser-agent`) inherits the environment too. The
`_spool_emit_mod`/`_spool_emit_tried` reset is the secondary half: it makes each
test load the emitter fresh under the current env, which is what lets a test
re-point `_SPOOL_EMIT_PATH` at the in-repo emitter.

TWO FIXTURES, TWO DIFFERENT HAZARDS — see each one's own docstring. The
per-test one gives each test its own spool so tests cannot read each other's
rows; the session-scoped backstop exists because the per-test one's TEARDOWN is
itself a hole.

`scripts/browser-bridge/tests/test_spool_isolation.py` is the regression guard,
and it is a SEPARATE FILE on purpose — it is only meaningful as evidence that
this conftest reaches modules other than `test_server.py`.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture(scope="session", autouse=True)
def _session_spool_backstop(tmp_path_factory):
    """🔴 THE TEARDOWN BACKSTOP. Without this, isolation has a RACE-SHAPED HOLE.

    `emit_cmd_event()` runs deliberately OFF the critical path — after the HTTP
    response is already sent (see server.py's BEST-EFFORT CONTRACT, ~line 951).
    So a handler thread can still be on its way to the emit when the test body
    ends. The per-test `monkeypatch.setenv` below is then UNDONE at teardown,
    restoring `ACTIVITY_SPOOL_DIR` to its AMBIENT value — and the late row lands
    THERE. In a gate run the ambient value is unset, so the fallback is
    `~/.local/state/activity/spool`: production.

    MEASURED, not theoretical — but NOT at the rate first supposed, and the
    difference decides the fix. Repeating the suspect shape is a poor detector:
    `test_spool_isolation.py test_site_notes.py` leaked once in 15 runs for one
    observer and 0 times in 20 for another. A DETERMINISTIC probe (a non-daemon
    thread that emits after a set delay, so the interpreter joins it at exit)
    separates two windows that look identical in a leak count:

      * emit lands WHILE ANOTHER TEST RUNS -> it goes to that test's tmp_path.
        Cross-test contamination, never production. Not what leaked.
      * emit lands when NO per-test fixture is active — the inter-test gap, and
        above all the TAIL after the session's last teardown -> ambient, i.e.
        `~/.local/state/activity/spool`. THIS is the production leak, and its
        rarity is just how narrow that window is.

    No in-test assertion can catch either: the row is written after the
    assertions have run. `pytest.MonkeyPatch()` is instantiated by hand because
    the `monkeypatch` FIXTURE is function-scoped and cannot be requested here.

    🔴 Do NOT "fix" the race by joining or quiescing server threads per test:
    that fights the deliberate off-critical-path design and would have to be
    repeated at every call site. This is one rule in one place.

    🔴 AND IT DELIBERATELY NEVER UNDOES ITSELF. A `yield` + `mp.undo()` here —
    the obvious spelling, and the one this fixture shipped with for one round —
    reopens the identical hole one level up: session teardown restores the
    ambient value, and a thread that has not fired yet still lands in
    production. MEASURED with a deterministic probe (a non-daemon thread that
    emits 1.5s later, so the interpreter joins it at exit): WITHOUT the backstop
    1 row reached the ambient dir; WITH the backstop *and* `mp.undo()` 1 row
    reached it still — byte-identical result, i.e. the fix was inert for this
    case. Without the undo: 0.

    Nothing needs the restore. The variable is process-local, the process is
    exiting, and `os.environ` changes do not escape it — so undoing buys
    tidiness in a process nobody will observe again, and costs the guarantee.
    """
    pytest.MonkeyPatch().setenv(
        "ACTIVITY_SPOOL_DIR",
        str(tmp_path_factory.mktemp("activity-spool-backstop")))


@pytest.fixture(autouse=True)
def _isolate_activity_spool(tmp_path, monkeypatch):
    """Narrow the spool to a PER-TEST temp dir, so no test can read another's
    rows. Teardown restores to the session backstop above, never to ambient."""
    monkeypatch.setenv("ACTIVITY_SPOOL_DIR", str(tmp_path / "activity-spool"))
    # Only reset the lazy emitter cache on an ALREADY-imported `server`. Several
    # files in this directory (test_skill_size, test_browser_agent_parse, …)
    # never import it, and forcing the import on their behalf would be a side
    # effect the fixture has no business causing. A module imported LATER starts
    # with the cache already clear, so nothing is missed — and the env var above
    # covers the write either way.
    server = sys.modules.get("server")
    if server is None:
        return
    monkeypatch.setattr(server, "_spool_emit_mod", None, raising=False)
    monkeypatch.setattr(server, "_spool_emit_tried", False, raising=False)
