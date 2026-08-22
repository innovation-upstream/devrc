"""Suite-wide telemetry containment: no browser-bridge test writes to the REAL
activity spool.

🔴 THE ISOLATION FIXTURE LIVES HERE, NOT IN A TEST MODULE, AND THAT IS THE WHOLE
POINT. `server.emit_cmd_event()` fires one activity event per handled command;
the row lands in `<ACTIVITY_SPOOL_DIR>/current.log`, and the activity collector
then ships that spool to the production ClickHouse `activity.events`. An
identical `autouse` fixture used to sit inside `test_server.py` carrying a
docstring that claimed it protected "EVERY test's" telemetry writes — but
**pytest scopes a module-declared fixture to that module**, so it covered one of
the NINE test modules here. A docstring claiming a scope its declaration site
cannot deliver is worse than none: it stops anyone looking.

🔴 WHAT THAT ACTUALLY COST, MEASURED — because the honest number is smaller than
the scary one, and the difference is the whole lesson. Running every module
alone at `origin/main` with a fresh spool and counting `source=browser-bridge`
rows:

    test_site_notes.py            17     <- the only sibling that ever emitted
    test_server.py                 0-1   <- the file that HAD the fixture
    the other seven                 0

So exactly ONE sibling was leaking, not five. The other seven were **LATENT, not
leaking** — unisolated, but they never reach an emit. Do not restate this as
"five files wrote real rows to production": that number came from a grep for
files touching `server`, and a grep counts DECLARATIONS, never INSTANCES.

The `test_server.py` row is the interesting one: the file that already had the
fixture still leaked, intermittently (1 row in one observer's run, 0 in mine).
That is the teardown race `_session_spool_backstop` closes — independent
evidence that BOTH fixtures below are load-bearing, and that per-test isolation
alone was never sufficient.

THE LEVER IS THE ENV VAR, NOT THE CACHE RESET. `spool_emit.default_spool_dir()`
reads `ACTIVITY_SPOOL_DIR` **at call time**, so pointing it at a temp dir
redirects the write no matter which emitter module object got loaded or when it
was loaded — and any subprocess inherits the variable, so a future test that
shells out is covered too. (No test does today: nothing here spawns `server.py`,
and neither the `browser` CLI nor `browser-agent` touches the spool at all.)

TWO FIXTURES, TWO DIFFERENT HAZARDS — see each one's own docstring. The
per-test one gives each test its own spool so tests cannot read each other's
rows; the session-scoped backstop exists because the per-test one's TEARDOWN is
itself a hole.

`scripts/browser-bridge/tests/test_spool_isolation.py` is the regression guard,
and it is a SEPARATE FILE on purpose: its first two tests are only meaningful as
evidence that `_isolate_activity_spool` reaches a module other than
`test_server.py`. (Its third test targets the session backstop instead, and so
does not carry that argument — see its own docstring.)
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
    # modules here (test_skill_size, test_browser_agent_parse, …) never import
    # it, and forcing the import on their behalf would be a side effect this
    # fixture has no business causing. A module imported LATER starts with the
    # cache already clear, so nothing is missed.
    server = sys.modules.get("server")
    if server is None:
        return
    # 🔴 WHAT THESE TWO LINES ARE AND ARE NOT. They are DEFENSIVE, not required:
    # deleting them leaves the suite green (753 passed, 0 rows leaked), because
    # both sites that re-point `_SPOOL_EMIT_PATH` — test_server.py's `telemetry`
    # fixture and test_spool_isolation.py's behavioural case — reset the cache
    # themselves. An earlier version of this comment claimed the reset "is what
    # lets a test re-point `_SPOOL_EMIT_PATH`"; that was simply false, and a
    # false rationale is worse than none because it makes the line look load-
    # bearing to anyone auditing it.
    #
    # 🔴 `raising` is left at its DEFAULT (True) on purpose — that is what gives
    # these lines a real job. With `raising=False` a renamed or misspelled
    # attribute is silently CREATED (verified against pytest 9.1.1) and the
    # fixture degrades to a no-op that still reads as protection. At the default
    # a rename of either cache attribute in server.py fails this fixture loudly,
    # so the reset doubles as a rename detector for the day a test does depend
    # on it.
    monkeypatch.setattr(server, "_spool_emit_mod", None)
    monkeypatch.setattr(server, "_spool_emit_tried", False)


# --- GUARD 9: the repository the suite RUNS FROM ----------------------------- #
# 🔴 THE SECOND ENTRY POINT, and it belongs in EVERY test directory a bare
# `pytest <dir>` can be pointed at. `scripts/run-tests.sh` loads the same module
# with `-p testlib.gitenv_plugin` for every target, so this changes nothing
# under the runner; it is what protects a hand-run `pytest`. #683's audit found
# exactly ONE of seven conftests wired, and not the one `gitenv_plugin`'s own
# rationale cites (`test_bash_guard.py::_mkrepo` and `test_guard_core.py`'s
# module-scoped repos, which run during COLLECTION).
# `test_git_repo_isolation.py::test_the_conftest_entry_points_are_a_pinned_ledger`
# fails when a conftest under `scripts/` is added or removed, so the next one
# cannot be forgotten — that is the "asserted ledger of every caller" shape
# claude/RULES.md asks for, rather than a single pinned example.
import sys as _guard9_sys  # noqa: E402
from pathlib import Path as _Guard9Path  # noqa: E402

for _guard9_parent in _Guard9Path(__file__).resolve().parents:
    if (_guard9_parent / "testlib" / "gitenv_plugin.py").is_file():
        if str(_guard9_parent) not in _guard9_sys.path:
            _guard9_sys.path.insert(0, str(_guard9_parent))
        break

from testlib.gitenv_plugin import (  # noqa: E402,F401
    _devrc_git_repo_isolation,
    pytest_collection_finish,
    pytest_configure,
    pytest_runtest_logstart,
    pytest_sessionfinish,
)
