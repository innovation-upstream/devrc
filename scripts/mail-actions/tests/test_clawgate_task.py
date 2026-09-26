"""Tests for `clawgate.emit_task` / `build_task_payload`.

Regression guard for the silently-dropped-title bug: clawgate's `POST /api/tasks`
handler reads `directory` (which it renders as the card title) and IGNORES any
`title` key. These tests assert the built payload carries the action title in
`directory` (NOT `title`), and that `emit_task` POSTs exactly that body with the
bearer token — with the HTTP layer mocked (no live clawgate)."""
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import clawgate  # noqa: E402


# -- pure payload builder ------------------------------------------------------

def test_payload_puts_title_in_directory_not_title():
    body = clawgate.build_task_payload(
        who="Acme Billing", ask="Pay invoice", deadline=None, amount=None,
        source_ref="mail#42 billing@acme.com",
    )
    # The server renders `directory` as the card title; `title` is dropped.
    assert "title" not in body
    assert set(body) == {"directory", "body"}
    assert body["directory"].endswith("Acme Billing")
    assert "action-required" in body["directory"]


def test_payload_body_includes_ask_deadline_amount_source():
    body = clawgate.build_task_payload(
        who="Acme", ask="  Approve the PO  ", deadline="2026-08-01", amount="$1,200",
        source_ref="mail#7 po@acme.com",
    )
    lines = body["body"].splitlines()
    assert lines[0] == "Approve the PO"  # ask, stripped
    assert "Deadline: 2026-08-01" in lines
    assert "Amount: $1,200" in lines
    assert "Source: mail#7 po@acme.com" in lines


def test_payload_omits_absent_deadline_and_amount():
    body = clawgate.build_task_payload(
        who="X", ask="do thing", deadline=None, amount=None, source_ref="mail#1 a@b.com",
    )
    assert body["body"] == "do thing\nSource: mail#1 a@b.com"


def test_directory_title_is_length_capped():
    body = clawgate.build_task_payload(
        who="W" * 500, ask="a", deadline=None, amount=None, source_ref="mail#1 a@b.com",
    )
    assert len(body["directory"]) <= clawgate.TITLE_MAX


# -- emit_task (HTTP mocked) ---------------------------------------------------

class _FakeResponse:
    def __init__(self):
        self.raised = False

    def raise_for_status(self):
        self.raised = True


class _FakeRequests:
    """Minimal stand-in for the `requests` module emit_task imports lazily."""

    def __init__(self):
        self.calls = []
        self._resp = _FakeResponse()

    def post(self, url, headers=None, json=None, timeout=None):
        self.calls.append(
            {"url": url, "headers": headers, "json": json, "timeout": timeout}
        )
        return self._resp


def _install_fake_requests(monkeypatch):
    fake = _FakeRequests()
    mod = types.ModuleType("requests")
    mod.post = fake.post
    monkeypatch.setitem(sys.modules, "requests", mod)
    return fake


def test_emit_task_noop_without_token(monkeypatch):
    monkeypatch.delenv("CLAWGATE_HOOK_TOKEN", raising=False)
    fake = _install_fake_requests(monkeypatch)
    assert clawgate.emit_task(
        who="X", ask="a", deadline=None, amount=None, source_ref="mail#1 a@b.com",
    ) is False
    assert fake.calls == []  # graceful no-op — nothing posted


def test_emit_task_posts_directory_payload_with_bearer(monkeypatch):
    monkeypatch.setenv("CLAWGATE_HOOK_TOKEN", "tok-123")
    # 🔴 Both URL keys CLEARED, so this asserts the documented default rather
    # than whatever the operator's shell happens to export.
    monkeypatch.delenv("CLAWGATE_TASK_API_URL", raising=False)
    monkeypatch.delenv("CLAWGATE_API_URL", raising=False)
    fake = _install_fake_requests(monkeypatch)

    ok = clawgate.emit_task(
        who="Acme", ask="Pay invoice", deadline="2026-08-01", amount="$5",
        source_ref="mail#9 x@acme.com",
    )
    assert ok is True
    assert len(fake.calls) == 1
    call = fake.calls[0]
    # A LITERAL, not `clawgate.<whatever the module computes>` — an expectation
    # derived from the implementation under test asserts nothing about it.
    assert call["url"] == "http://192.168.50.250:30302/api/tasks"
    assert call["headers"]["Authorization"] == "Bearer tok-123"
    # The POSTed body carries the title in `directory`, never `title`.
    posted = call["json"]
    assert "title" not in posted
    assert posted["directory"].endswith("Acme")
    assert posted == clawgate.build_task_payload(
        who="Acme", ask="Pay invoice", deadline="2026-08-01", amount="$5",
        source_ref="mail#9 x@acme.com",
    )
    assert fake._resp.raised is True  # raise_for_status() was called


# -- the TASK base URL comes from configuration, not a literal -----------------
# 🔴 RED AT origin/main (adf1ccb6): this module held
# `ENDPOINT = "http://192.168.50.250:30302/api/tasks"` and read nothing but the
# token from the environment, so every assertion below that names a configured
# host failed with the literal's host instead. The task/board service was
# extracted out of the permission router and now runs on its own base URL; a
# producer that cannot follow that keeps posting cards at whichever service the
# literal names.
#
# Fixture hosts are pairwise distinct AND distinct from the default constant, so
# a mutant that collapses the precedence to either the other key or the default
# cannot pass by coincidence.
TASK_BASE = "http://task-api.example:18081"
ROUTER_BASE = "http://router-api.example:27443"
DEFAULT_BASE = "http://192.168.50.250:30302"


def _both_unset(monkeypatch):
    monkeypatch.delenv("CLAWGATE_TASK_API_URL", raising=False)
    monkeypatch.delenv("CLAWGATE_API_URL", raising=False)


def test_task_endpoint_prefers_the_task_specific_variable(monkeypatch):
    """Both set to DIFFERENT hosts: the task key wins, the router key does not."""
    monkeypatch.setenv("CLAWGATE_TASK_API_URL", TASK_BASE)
    monkeypatch.setenv("CLAWGATE_API_URL", ROUTER_BASE)
    assert clawgate.task_endpoint() == TASK_BASE + "/api/tasks", (
        "with both variables set, the card must be posted to the TASK service "
        "(%s), not to the router (%s) and not to the default (%s)"
        % (TASK_BASE, ROUTER_BASE, DEFAULT_BASE))


def test_task_endpoint_falls_back_to_the_router_variable(monkeypatch):
    """The task key unset must resolve to whatever the router key resolves to —
    a host that never heard of the split keeps working unchanged."""
    monkeypatch.delenv("CLAWGATE_TASK_API_URL", raising=False)
    monkeypatch.setenv("CLAWGATE_API_URL", ROUTER_BASE)
    assert clawgate.task_endpoint() == ROUTER_BASE + "/api/tasks", (
        "with CLAWGATE_TASK_API_URL unset the endpoint must fall back to "
        "CLAWGATE_API_URL (%s)" % ROUTER_BASE)


def test_task_endpoint_empty_task_variable_falls_through(monkeypatch):
    """A half-written `CLAWGATE_TASK_API_URL=` is UNSET, as in `${A:-$B}` —
    never a bare path."""
    monkeypatch.setenv("CLAWGATE_TASK_API_URL", "")
    monkeypatch.setenv("CLAWGATE_API_URL", ROUTER_BASE)
    assert clawgate.task_endpoint() == ROUTER_BASE + "/api/tasks"


def test_task_endpoint_strips_a_trailing_slash(monkeypatch):
    """A configured base with a trailing slash must not double the separator."""
    monkeypatch.setenv("CLAWGATE_TASK_API_URL", TASK_BASE + "/")
    monkeypatch.delenv("CLAWGATE_API_URL", raising=False)
    assert clawgate.task_endpoint() == TASK_BASE + "/api/tasks"


def test_task_endpoint_with_neither_variable_set_is_unchanged(monkeypatch):
    """🔴 AN INVARIANT GUARD, NOT A REGRESSION TEST — counted as neither.

    It pins the thing this change must NOT move: on a host told nothing about
    the split, the endpoint is byte for byte what the old literal was. It does
    go red at adf1ccb6, but only because `task_endpoint` does not exist there
    at all — the VALUE it asserts was already what that tree produced. Do not
    read it as evidence the fix works; the two tests above are that."""
    _both_unset(monkeypatch)
    assert clawgate.task_endpoint() == DEFAULT_BASE + "/api/tasks"


def test_emit_task_posts_to_the_CONFIGURED_task_service(monkeypatch):
    """The end-to-end shape: the POST itself follows the configuration."""
    monkeypatch.setenv("CLAWGATE_HOOK_TOKEN", "tok-cfg")
    monkeypatch.setenv("CLAWGATE_TASK_API_URL", TASK_BASE)
    monkeypatch.setenv("CLAWGATE_API_URL", ROUTER_BASE)
    fake = _install_fake_requests(monkeypatch)

    assert clawgate.emit_task(
        who="Acme", ask="a", deadline=None, amount=None,
        source_ref="mail#1 a@b.com") is True
    assert fake.calls[0]["url"] == TASK_BASE + "/api/tasks", (
        "emit_task posted to %r; it must follow CLAWGATE_TASK_API_URL (%s)"
        % (fake.calls[0]["url"], TASK_BASE))


def test_the_precedence_is_the_SHARED_one_not_a_local_respelling():
    """🔴 THE SEAM. The two assertions above would also pass over a private copy
    of the rule living in this module, which is the exact regrowth the shared
    definition exists to prevent. This pins that the module RESOLVES THROUGH the
    shared ledger: feed the shared module's own ledger names and watch this
    module's resolver agree, and confirm it is the shared default it falls back
    to.
    """
    cg = clawgate._load_clawgate_tasks()
    assert cg.TASK_API_URL_VARS == ("CLAWGATE_TASK_API_URL", "CLAWGATE_API_URL")
    specific, general = cg.TASK_API_URL_VARS
    env = {specific: TASK_BASE, general: ROUTER_BASE}
    assert clawgate.task_endpoint(env) == TASK_BASE + "/api/tasks"
    assert clawgate.task_endpoint({general: ROUTER_BASE}) == \
        ROUTER_BASE + "/api/tasks"
    assert clawgate.task_endpoint({}) == cg.DEFAULT_API_URL + "/api/tasks"


def test_emit_task_without_a_token_reads_NOTHING(monkeypatch):
    """The no-op must stay a no-op: no shared-module load, no endpoint read.

    Pinned because resolving the endpoint before the token check would turn a
    benign "no token configured" into an ImportError on any host whose lib/ is
    not where this module expects it.
    """
    monkeypatch.delenv("CLAWGATE_HOOK_TOKEN", raising=False)
    fake = _install_fake_requests(monkeypatch)

    def explode(*a, **k):  # pragma: no cover - fails the test if reached
        raise AssertionError("emit_task resolved the endpoint with no token set")

    monkeypatch.setattr(clawgate, "task_endpoint", explode)
    assert clawgate.emit_task(
        who="X", ask="a", deadline=None, amount=None,
        source_ref="mail#1 a@b.com") is False
    assert fake.calls == []
