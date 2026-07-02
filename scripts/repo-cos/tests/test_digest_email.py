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
        subject="🧭 test", body="hello", to_addr="z@y.com", mode="gmail",
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
            subject="s", body="b", mode="gmail",
            _sender=boom, _creds=lambda: ("u", "p"),
        )


# ---- relay send path (DEFAULT) ------------------------------------------------

def test_build_message_sets_reply_to():
    msg = email_send.build_message(
        subject="s", body="b", from_addr="repo-cos@mail.zacx.dev",
        to_addr="z@y.com", reply_to="repo-cos@inbox.zacx.dev")
    assert msg["Reply-To"] == "repo-cos@inbox.zacx.dev"


def test_relay_is_default_and_sets_headers(monkeypatch):
    # No REPO_COS_SEND → relay path. Capture the message handed to the relay sender.
    monkeypatch.delenv("REPO_COS_SEND", raising=False)
    monkeypatch.delenv("REPO_COS_FROM", raising=False)
    monkeypatch.delenv("REPO_COS_REPLY_TO", raising=False)
    captured = {}

    def fake_relay(msg):
        captured["from"] = msg["From"]
        captured["to"] = msg["To"]
        captured["reply_to"] = msg["Reply-To"]
        captured["subject"] = msg["Subject"]

    to = email_send.send_digest(
        subject="🧭 test", body="hello", to_addr="z@y.com", _relay=fake_relay)
    assert to == "z@y.com"
    assert captured["from"] == email_send.DEFAULT_FROM  # repo-cos@mail.zacx.dev
    assert "mail.zacx.dev" in captured["from"]
    assert captured["reply_to"] == "repo-cos@inbox.zacx.dev"
    assert captured["to"] == "z@y.com"
    assert captured["subject"] == "🧭 test"


def test_relay_env_overrides_from_and_reply_to(monkeypatch):
    monkeypatch.setenv("REPO_COS_FROM", "custom <cos@mail.zacx.dev>")
    monkeypatch.setenv("REPO_COS_REPLY_TO", "reply@inbox.zacx.dev")
    captured = {}

    def fake_relay(msg):
        captured["from"] = msg["From"]
        captured["reply_to"] = msg["Reply-To"]

    email_send.send_digest(subject="s", body="b", mode="relay", _relay=fake_relay)
    assert captured["from"] == "custom <cos@mail.zacx.dev>"
    assert captured["reply_to"] == "reply@inbox.zacx.dev"


def test_gmail_mode_uses_gmail_path_not_relay(monkeypatch):
    monkeypatch.setenv("REPO_COS_SEND", "gmail")
    monkeypatch.delenv("REPO_COS_REPLY_TO", raising=False)
    used = {"relay": False, "gmail": False}

    def fake_relay(msg):
        used["relay"] = True

    def fake_sender(msg, *, user, password, host=None, port=None):
        used["gmail"] = True
        used["reply_to"] = msg["Reply-To"]

    email_send.send_digest(
        subject="s", body="b",
        _relay=fake_relay, _sender=fake_sender, _creds=lambda: ("u@g", "pw"))
    assert used["gmail"] is True
    assert used["relay"] is False
    # Reply-To is set on the gmail path too → replies still route to inbox.zacx.dev
    assert used["reply_to"] == "repo-cos@inbox.zacx.dev"


def test_unknown_send_mode_raises():
    with pytest.raises(email_send.EmailError):
        email_send.send_digest(subject="s", body="b", mode="carrier-pigeon")


def test_relay_send_uses_unverified_starttls(monkeypatch):
    # The relay presents a mail.zacx.dev cert but we hit 127.0.0.1 → hostname-verify OFF.
    # Assert the SMTP.starttls context is CERT_NONE (mock out the port-forward + smtplib).
    import ssl as _ssl

    class FakePF:
        stderr = None
        def poll(self):
            return None
        def terminate(self):
            pass
        def wait(self, timeout=None):
            return 0

    class FakeSMTP:
        def __init__(self, host, port, timeout=None):
            self.started = None
            self.sent = None
        def starttls(self, context=None):
            self.started = context
        def send_message(self, msg):
            self.sent = msg
        def quit(self):
            pass

    fake_smtp = FakeSMTP("127.0.0.1", 1)
    monkeypatch.setattr(email_send.subprocess, "Popen", lambda *a, **k: FakePF())
    monkeypatch.setattr(email_send, "_wait_for_port", lambda *a, **k: None)
    monkeypatch.setattr(email_send.smtplib, "SMTP", lambda *a, **k: fake_smtp)
    # PROD_KUBECONFIG.exists() must pass — point it at a file we know exists (this test).
    monkeypatch.setattr(email_send, "PROD_KUBECONFIG", Path(__file__))

    msg = email_send.build_message(
        subject="s", body="b", from_addr="repo-cos@mail.zacx.dev",
        to_addr="z@y.com", reply_to="repo-cos@inbox.zacx.dev")
    email_send._relay_send(msg)

    assert isinstance(fake_smtp.started, _ssl.SSLContext)
    assert fake_smtp.started.check_hostname is False
    assert fake_smtp.started.verify_mode == _ssl.CERT_NONE
    assert fake_smtp.sent is msg
