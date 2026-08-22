"""Suite-wide hermeticity guard: no test may read the LIVE initiatives store.

`scan.cmd_scan` calls `routing.related_for` to resolve each proposal's `↳ relates to:`
breadcrumb, and its ONLY I/O is `route.load_current()` — a cross-cluster read of the
homelab `mailbox` Postgres (`initiatives.current`) over a kubectl port-forward. That call
is best-effort, so on a box WITHOUT a kubeconfig it fails silently and the suite looks
hermetic; on Zach's box (kubeconfig present) it really does hit the cluster. Four suites
drive the real `cmd_scan` — test_approve, test_dismiss, test_exclusions, test_scan_cli —
so the whole run would otherwise depend on cluster reachability and pay a network
round-trip per test.

Neutralised at the SEAM rather than per-test: `route.load_current` is stubbed to an EMPTY
store for every test, so the routing code still runs for real and simply finds nothing to
relate to. The tests that deliberately exercise the router (test_routing.py) monkeypatch
`load_current` again inside the test body — a later `setattr` on the same function-scoped
`monkeypatch` wins, and both are undone in reverse at teardown.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import routing  # noqa: E402


@pytest.fixture(autouse=True)
def _no_live_initiatives_store(monkeypatch):
    try:
        route = routing._route()
    except Exception:  # noqa: BLE001 - router unloadable here == no store read possible
        return
    monkeypatch.setattr(route, "load_current", lambda: [], raising=False)


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
