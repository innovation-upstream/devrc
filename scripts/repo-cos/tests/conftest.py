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
