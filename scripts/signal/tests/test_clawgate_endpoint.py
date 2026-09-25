"""The Signal notifier's TASK base URL comes from configuration, not a literal.

🔴 RED AT origin/main (bf28baa99). `scripts/signal/clawgate.py` held
`ENDPOINT = "http://192.168.50.250:30302/api/tasks"` and read nothing from the
environment but the hook token, so every assertion here that names a configured
host failed with the literal's host instead. The task/board service was
extracted out of the permission router and the two now run on two base URLs; a
card producer that cannot follow the split keeps posting at whichever service
the literal happens to name.

🔴 WHY THIS MODULE CARRIES ITS OWN COPY OF THE PRECEDENCE. It runs from an image
whose Dockerfile COPYs this directory's modules BY NAME (`COPY . .` is refused —
devrc is public) and whose dockerignore denies `**` and re-admits only those
names; two build-time controls assert the image's file set exactly, in both
directions. The shared definition in the repo's lib directory is therefore not
importable here without widening a deliberately narrow, security-motivated
allowlist. The copy is pinned to the shared one by
`scripts/tests/test_clawgate_task_base_url_single_source.py`, which compares the
ledger, the default AND the resolution behaviour; what THIS file pins is that
the module reads configuration at all, and with which precedence.

Fixture hosts are pairwise distinct AND distinct from the default constant, so a
mutant that collapses the precedence to the other key, or to the default, cannot
pass by coincidence.
"""
from __future__ import annotations

import sys
import types

import clawgate

TASK_BASE = "http://task-api.example:18081"
ROUTER_BASE = "http://router-api.example:27443"
DEFAULT_BASE = "http://192.168.50.250:30302"


def _install_fake_requests(monkeypatch):
    calls = []

    class Resp:
        def raise_for_status(self):
            calls.append("raised")

    module = types.ModuleType("requests")

    def post(url, headers=None, json=None, timeout=None):
        calls.append({"url": url, "headers": headers, "json": json})
        return Resp()

    module.post = post
    monkeypatch.setitem(sys.modules, "requests", module)
    return calls


def test_task_endpoint_prefers_the_task_specific_variable(monkeypatch):
    """Both set to DIFFERENT hosts: the task key wins."""
    monkeypatch.setenv("CLAWGATE_TASK_API_URL", TASK_BASE)
    monkeypatch.setenv("CLAWGATE_API_URL", ROUTER_BASE)
    assert clawgate.task_endpoint() == TASK_BASE + "/api/tasks", (
        "with both variables set, the draft card must be posted to the TASK "
        "service (%s), not to the router (%s) and not to the default (%s)"
        % (TASK_BASE, ROUTER_BASE, DEFAULT_BASE))


def test_task_endpoint_falls_back_to_the_router_variable(monkeypatch):
    """The task key unset resolves to whatever the router key resolves to."""
    monkeypatch.delenv("CLAWGATE_TASK_API_URL", raising=False)
    monkeypatch.setenv("CLAWGATE_API_URL", ROUTER_BASE)
    assert clawgate.task_endpoint() == ROUTER_BASE + "/api/tasks", (
        "with CLAWGATE_TASK_API_URL unset the endpoint must fall back to "
        "CLAWGATE_API_URL (%s)" % ROUTER_BASE)


def test_task_endpoint_empty_task_variable_falls_through(monkeypatch):
    """A half-written `CLAWGATE_TASK_API_URL=` is UNSET, as in `${A:-$B}`."""
    monkeypatch.setenv("CLAWGATE_TASK_API_URL", "")
    monkeypatch.setenv("CLAWGATE_API_URL", ROUTER_BASE)
    assert clawgate.task_endpoint() == ROUTER_BASE + "/api/tasks"


def test_task_endpoint_strips_a_trailing_slash(monkeypatch):
    monkeypatch.setenv("CLAWGATE_TASK_API_URL", TASK_BASE + "/")
    monkeypatch.delenv("CLAWGATE_API_URL", raising=False)
    assert clawgate.task_endpoint() == TASK_BASE + "/api/tasks"


def test_task_endpoint_with_neither_variable_set_is_unchanged(monkeypatch):
    """🔴 An INVARIANT GUARD, not a regression test — GREEN at origin/main too,
    deliberately. It pins what this change must NOT move: a pod told nothing
    about the split posts exactly where the old literal pointed."""
    monkeypatch.delenv("CLAWGATE_TASK_API_URL", raising=False)
    monkeypatch.delenv("CLAWGATE_API_URL", raising=False)
    assert clawgate.task_endpoint() == DEFAULT_BASE + "/api/tasks"


def test_task_endpoint_takes_an_explicit_mapping():
    """The resolver is drivable without touching the process environment, which
    is what lets the cross-module drift guard compare behaviours directly."""
    assert clawgate.task_endpoint(
        {"CLAWGATE_TASK_API_URL": TASK_BASE,
         "CLAWGATE_API_URL": ROUTER_BASE}) == TASK_BASE + "/api/tasks"
    assert clawgate.task_endpoint({}) == DEFAULT_BASE + "/api/tasks"


def test_emit_draft_task_posts_to_the_CONFIGURED_task_service(monkeypatch):
    """The end-to-end shape: the POST itself follows the configuration."""
    monkeypatch.setenv("CLAWGATE_HOOK_TOKEN", "tok-cfg")
    monkeypatch.setenv("CLAWGATE_TASK_API_URL", TASK_BASE)
    monkeypatch.setenv("CLAWGATE_API_URL", ROUTER_BASE)
    calls = _install_fake_requests(monkeypatch)

    assert clawgate.emit_draft_task(draft_id=31, recipient="+15550001111",
                                    body="please approve") is True
    assert calls[0]["url"] == TASK_BASE + "/api/tasks", (
        "emit_draft_task posted to %r; it must follow CLAWGATE_TASK_API_URL (%s)"
        % (calls[0]["url"], TASK_BASE))


def test_emit_draft_task_without_a_token_reads_NOTHING(monkeypatch):
    """The no-op must stay a no-op: the endpoint is not even resolved."""
    monkeypatch.delenv("CLAWGATE_HOOK_TOKEN", raising=False)
    calls = _install_fake_requests(monkeypatch)

    def explode(*a, **k):  # pragma: no cover - fails the test if reached
        raise AssertionError(
            "emit_draft_task resolved the endpoint with no token set")

    monkeypatch.setattr(clawgate, "task_endpoint", explode)
    assert clawgate.emit_draft_task(draft_id=1, recipient="+15550001111",
                                    body="x") is False
    assert calls == []
