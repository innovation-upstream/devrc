"""Suite-wide telemetry containment: no browser-bridge test writes to the REAL
activity spool.

🔴 THIS FIXTURE LIVES HERE, NOT IN A TEST MODULE, AND THAT IS THE WHOLE POINT.
`server.emit_cmd_event()` fires one activity event per handled command; the row
lands in `<ACTIVITY_SPOOL_DIR>/current.log`, and the activity collector then
ships that spool to the production ClickHouse `activity.events`. An identical
`autouse` fixture used to sit inside `test_server.py` carrying a docstring that
claimed it protected "EVERY test's" telemetry writes — but pytest scopes a
module-declared fixture to THAT MODULE, so it protected exactly one file out of
six. `scripts/browser-bridge/tests/` was the only test directory in the repo
with no `conftest.py`, and the five siblings leaked real rows into production:
`test_site_notes.py` alone was MEASURED at +17 production rows per run
(reproduced twice). A fixture whose docstring claims a scope its declaration
site cannot deliver is worse than no fixture — it stops anyone looking.

THE LEVER IS THE ENV VAR, NOT THE CACHE RESET. `spool_emit.default_spool_dir()`
reads `ACTIVITY_SPOOL_DIR` **at call time**, so pointing it at a per-test
`tmp_path` redirects the write no matter which emitter module object got loaded,
when it was loaded, or whether the emitting code runs in this process at all —
a subprocess (`browser` CLI, `browser-agent`) inherits the environment too. The
`_spool_emit_mod`/`_spool_emit_tried` reset is the secondary half: it makes each
test load the emitter fresh under the current env, which is what lets a test
re-point `_SPOOL_EMIT_PATH` at the in-repo emitter.

`scripts/browser-bridge/tests/test_spool_isolation.py` is the regression guard,
and it is a SEPARATE FILE on purpose — it is only meaningful as evidence that
this conftest reaches modules other than `test_server.py`.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture(autouse=True)
def _isolate_activity_spool(tmp_path, monkeypatch):
    """Redirect EVERY test's activity telemetry to a per-test temp spool."""
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
