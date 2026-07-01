"""Digest formatter + email sender tests — SMTP is mocked, no network, no SOPS."""
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import digest  # noqa: E402
import email_send  # noqa: E402
import llm  # noqa: E402


def _prop(title="Fix", ci=True):
    return llm.Proposal(
        title=title, repo="devrc", evidence=["devrc/a.py:1"], why="better CI",
        effort="S", approach="do it", ci_verifiable=ci,
    )


# ---- digest -------------------------------------------------------------------

def test_subject_has_week_date():
    s = digest.subject(date(2026, 7, 1))
    assert "week of 2026-07-01" in s


def test_render_includes_evidence_and_marker():
    body = digest.render([_prop()], today=date(2026, 7, 1), candidate_count=3)
    assert "Fix" in body
    assert "devrc/a.py:1" in body
    assert "CI-verifiable" in body
    assert "From 3 deterministic signal(s)" in body


def test_render_empty_is_honest():
    body = digest.render([], today=date(2026, 7, 1))
    assert "No bounded" in body
    assert "high" in body


def test_render_non_ci_marker():
    body = digest.render([_prop(ci=False)], today=date(2026, 7, 1))
    assert "needs judgement" in body


def test_render_numbers_proposals():
    body = digest.render([_prop("A"), _prop("B")], today=date(2026, 7, 1))
    assert "1. A" in body
    assert "2. B" in body


# ---- email --------------------------------------------------------------------

def test_build_message_headers():
    msg = email_send.build_message(
        subject="s", body="b", from_addr="a@x.com", to_addr="z@y.com")
    assert msg["Subject"] == "s"
    assert msg["From"] == "a@x.com"
    assert msg["To"] == "z@y.com"
    assert msg.get_content().strip() == "b"


def test_send_digest_uses_injected_sender_and_creds():
    sent = {}

    def fake_creds():
        return ("user@gmail.com", "app-pw")

    def fake_sender(msg, *, user, password, host=None, port=None):
        sent["user"] = user
        sent["password"] = password
        sent["subject"] = msg["Subject"]
        sent["to"] = msg["To"]

    to = email_send.send_digest(
        subject="🧭 test", body="hello", to_addr="z@y.com",
        _sender=fake_sender, _creds=fake_creds,
    )
    assert to == "z@y.com"
    assert sent["user"] == "user@gmail.com"
    assert sent["password"] == "app-pw"
    assert sent["subject"] == "🧭 test"
    assert sent["to"] == "z@y.com"


def test_load_credentials_env_override(monkeypatch):
    monkeypatch.setenv("REPO_COS_SMTP_USER", "envuser@gmail.com")
    monkeypatch.setenv("REPO_COS_SMTP_PASSWORD", "envpw")
    user, pw = email_send.load_credentials()
    assert user == "envuser@gmail.com"
    assert pw == "envpw"


def test_send_digest_propagates_sender_failure():
    def boom(msg, *, user, password, host=None, port=None):
        raise email_send.EmailError("smtp down")

    with pytest.raises(email_send.EmailError):
        email_send.send_digest(
            subject="s", body="b",
            _sender=boom, _creds=lambda: ("u", "p"),
        )
