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
    fake = _install_fake_requests(monkeypatch)

    ok = clawgate.emit_task(
        who="Acme", ask="Pay invoice", deadline="2026-08-01", amount="$5",
        source_ref="mail#9 x@acme.com",
    )
    assert ok is True
    assert len(fake.calls) == 1
    call = fake.calls[0]
    assert call["url"] == clawgate.ENDPOINT
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
