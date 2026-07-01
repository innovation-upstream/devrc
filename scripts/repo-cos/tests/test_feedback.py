"""REPLY-FEEDBACK tests — quoted-text stripping + IMAP fetch, all mocked (no network).

Covers: strip_quoted removes ">" quotes AND the "On … wrote:" attribution block; a genuine
reply is found + cleaned; no-reply → None; IMAP error → None (never raises); the original
digest (no Re:/In-Reply-To) is NOT mistaken for a reply; subject-core matching.
"""
import email
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import feedback  # noqa: E402
import digest  # noqa: E402


# ---- strip_quoted ------------------------------------------------------------------

def test_strip_quoted_removes_quotes_and_attribution():
    body = (
        "Drop the civitai 3D one, I'm not touching that.\n"
        "Focus on the kubeclaw test fixes instead.\n"
        "\n"
        "On Mon, 1 Jul 2026 at 08:00, Zach Lowden <zachlowden1@gmail.com> wrote:\n"
        "> 🧭 Repo proposals — week of 2026-07-01\n"
        "> 1. Implement report modal for Model3D in civitai\n"
        ">    why: completes a feature\n"
    )
    cleaned = feedback.strip_quoted(body)
    assert "Drop the civitai 3D one" in cleaned
    assert "Focus on the kubeclaw test fixes" in cleaned
    # everything from the attribution onward is gone
    assert "Repo proposals" not in cleaned
    assert "Model3D" not in cleaned
    assert ">" not in cleaned
    assert "wrote:" not in cleaned


def test_strip_quoted_drops_bare_quoted_lines_without_attribution():
    body = "New note here.\n> quoted old line\n> another quote\nAnd more new text."
    cleaned = feedback.strip_quoted(body)
    assert "New note here." in cleaned
    assert "And more new text." in cleaned
    assert "quoted old line" not in cleaned


def test_strip_quoted_keeps_legit_line_ending_wrote():
    # a real reply line that merely ends in "wrote:" must NOT be cut as an attribution —
    # only a "On … wrote:" attribution triggers the cut.
    body = "Re the doc I wrote:\nkeep this, it's my actual reply."
    cleaned = feedback.strip_quoted(body)
    assert "Re the doc I wrote:" in cleaned
    assert "keep this, it's my actual reply." in cleaned


def test_strip_quoted_stops_at_signature():
    body = "My reply.\n--\nZach\nSent from phone"
    cleaned = feedback.strip_quoted(body)
    assert cleaned == "My reply."


def test_strip_quoted_caps_length():
    body = "x" * (feedback.MAX_REPLY_CHARS + 500)
    assert len(feedback.strip_quoted(body)) == feedback.MAX_REPLY_CHARS


def test_strip_quoted_empty():
    assert feedback.strip_quoted("") == ""
    assert feedback.strip_quoted(None) == ""


# ---- fake IMAP server --------------------------------------------------------------

def _mk_msg(*, subject, frm, body, in_reply_to=None):
    msg = email.message.EmailMessage()
    msg["Subject"] = subject
    msg["From"] = frm
    msg["To"] = "zachlowden1@gmail.com"
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
        msg["References"] = in_reply_to
    msg.set_content(body)
    return msg


class FakeIMAP:
    """Minimal imaplib.IMAP4_SSL stand-in. `messages` maps mailbox name → list of
    EmailMessage; SEARCH returns sequence numbers for messages whose subject contains the
    searched SUBJECT token; FETCH returns the RFC822 bytes."""

    def __init__(self, messages):
        self._messages = messages
        self._sel = None
        self.logged_in = False
        self.logged_out = False

    def login(self, user, password):
        self.logged_in = True
        return ("OK", [b"ok"])

    def select(self, mailbox, readonly=False):
        name = mailbox.strip('"')
        self._sel = name
        if name not in self._messages:
            return ("NO", [b"no such mailbox"])
        return ("OK", [str(len(self._messages[name])).encode()])

    def search(self, charset, *criteria):
        # criteria e.g. ("SINCE", "30-Jun-2026", "SUBJECT", '"Repo proposals"')
        subj_token = ""
        for i, c in enumerate(criteria):
            if c == "SUBJECT" and i + 1 < len(criteria):
                subj_token = criteria[i + 1].strip('"')
        msgs = self._messages.get(self._sel, [])
        hits = [str(i + 1).encode() for i, m in enumerate(msgs)
                if subj_token.lower() in str(m["Subject"]).lower()]
        return ("OK", [b" ".join(hits)])

    def fetch(self, num, spec):
        idx = int(num) - 1
        msgs = self._messages.get(self._sel, [])
        if idx < 0 or idx >= len(msgs):
            return ("NO", [None])
        return ("OK", [(b"1 (RFC822)", msgs[idx].as_bytes())])

    def logout(self):
        self.logged_out = True
        return ("BYE", [b"bye"])


@pytest.fixture
def latest(tmp_path, monkeypatch):
    """Point feedback at a temp latest.json describing last week's digest."""
    import json
    data = {
        "generated_at": "2026-07-01T08:00:00-05:00",
        "subject": "🧭 Repo proposals — week of 2026-07-01",
        "proposals": [
            {"title": "Implement report modal for Model3D in civitai",
             "evidence": ["civitai/docs/3d-models-followups.md:103"]},
            {"title": "Fix skipped tests in baseball-manitoba-pitch",
             "evidence": ["baseball-manitoba-pitch/api/x_test.go:33"]},
        ],
    }
    p = tmp_path / "latest.json"
    p.write_text(json.dumps(data))
    monkeypatch.setattr(feedback, "PERSIST_LATEST", p)
    return p


def _creds():
    return ("zachlowden1@gmail.com", "app-pw")


def test_fetch_finds_reply_and_strips(latest):
    reply = _mk_msg(
        subject="Re: 🧭 Repo proposals — week of 2026-07-01",
        frm="Zach Lowden <zachlowden1@gmail.com>",
        body=("Skip the civitai 3D report modal — not doing that.\n"
              "\n"
              "On Mon, 1 Jul 2026 at 08:00, Zach <zachlowden1@gmail.com> wrote:\n"
              "> 1. Implement report modal for Model3D\n"),
        in_reply_to="<digest-msg-id@mail>",
    )
    fake = FakeIMAP({"INBOX": [reply]})
    fb = feedback.fetch_last_feedback(_creds=_creds, _imap_factory=lambda: fake)
    assert fb is not None
    assert "Skip the civitai 3D report modal" in fb.reply_text
    assert "Model3D" not in fb.reply_text  # quoted part stripped
    assert fb.replied_at == "2026-07-01T08:00:00-05:00"
    assert len(fb.prev_proposals) == 2
    assert fake.logged_out is True
    # prev_summary renders "title — evidence"
    summ = fb.prev_summary()
    assert any("Model3D" in s and "3d-models-followups.md:103" in s for s in summ)


def test_fetch_ignores_original_digest_not_a_reply(latest):
    # The original digest is Zach→Zach but has NO Re:/In-Reply-To → must NOT be treated
    # as feedback (else the model gets fed its own prior output).
    original = _mk_msg(
        subject="🧭 Repo proposals — week of 2026-07-01",
        frm="zachlowden1@gmail.com",
        body="1. Implement report modal for Model3D\n",
    )
    fake = FakeIMAP({"INBOX": [original], feedback.ALL_MAIL: [original]})
    fb = feedback.fetch_last_feedback(_creds=_creds, _imap_factory=lambda: fake)
    assert fb is None


def test_fetch_no_matching_mail_returns_none(latest):
    other = _mk_msg(subject="Re: dinner plans", frm="zachlowden1@gmail.com",
                    body="sure", in_reply_to="<x>")
    fake = FakeIMAP({"INBOX": [other], feedback.ALL_MAIL: [other]})
    fb = feedback.fetch_last_feedback(_creds=_creds, _imap_factory=lambda: fake)
    assert fb is None


def test_fetch_falls_back_to_all_mail(latest):
    reply = _mk_msg(
        subject="Re: 🧭 Repo proposals — week of 2026-07-01",
        frm="zachlowden1@gmail.com",
        body="Focus on kubeclaw tests.\n",
        in_reply_to="<digest@mail>",
    )
    # only in All Mail, not INBOX (e.g. archived)
    fake = FakeIMAP({"INBOX": [], feedback.ALL_MAIL: [reply]})
    fb = feedback.fetch_last_feedback(_creds=_creds, _imap_factory=lambda: fake)
    assert fb is not None
    assert "Focus on kubeclaw tests." in fb.reply_text


def test_fetch_imap_error_returns_none(latest):
    def boom():
        raise OSError("connection refused")
    fb = feedback.fetch_last_feedback(_creds=_creds, _imap_factory=boom)
    assert fb is None  # never raises


def test_fetch_no_creds_returns_none(latest):
    def bad_creds():
        raise RuntimeError("no app password")
    fb = feedback.fetch_last_feedback(_creds=bad_creds, _imap_factory=lambda: FakeIMAP({}))
    assert fb is None


def test_fetch_no_latest_json_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(feedback, "PERSIST_LATEST", tmp_path / "missing.json")
    fb = feedback.fetch_last_feedback(_creds=_creds, _imap_factory=lambda: FakeIMAP({}))
    assert fb is None


def test_fetch_picks_most_recent_reply(latest):
    old = _mk_msg(subject="Re: 🧭 Repo proposals — week of 2026-07-01",
                  frm="zachlowden1@gmail.com", body="old reply", in_reply_to="<a>")
    new = _mk_msg(subject="Re: 🧭 Repo proposals — week of 2026-07-01",
                  frm="zachlowden1@gmail.com", body="new reply wins", in_reply_to="<b>")
    fake = FakeIMAP({"INBOX": [old, new]})  # search returns ascending → newest last
    fb = feedback.fetch_last_feedback(_creds=_creds, _imap_factory=lambda: fake)
    assert fb is not None
    assert "new reply wins" in fb.reply_text


def test_subject_core_present_in_digest_subject():
    # feedback matches on this ASCII fragment — it MUST be in what digest actually sends.
    from datetime import date
    assert feedback.digest.SUBJECT_CORE in digest.subject(date(2026, 7, 6))
    assert feedback.digest.SUBJECT_CORE == "Repo proposals"


def test_strip_quoted_keeps_bottom_posted_reply():
    # bottom-posting: quote FIRST, new text below the attribution — must NOT be dropped
    # (the audit's 🟡: `break` at the attribution silently discarded the whole reply).
    body = (
        "On Mon, 1 Jul 2026 at 08:00, Zach <zachlowden1@gmail.com> wrote:\n"
        "> 🧭 Repo proposals — week of 2026-07-01\n"
        "> 1. Implement report modal for Model3D\n"
        "\n"
        "Yes to #1, and add auth to the assets controller too.\n"
    )
    cleaned = feedback.strip_quoted(body)
    assert "Yes to #1, and add auth to the assets controller" in cleaned
    assert "Model3D" not in cleaned      # quoted digest still stripped
    assert "wrote:" not in cleaned       # attribution line removed


def test_reply_from_owner_requires_exact_address():
    ok = _mk_msg(subject="Re: 🧭 Repo proposals — week of 2026-07-01",
                 frm="Zach Lowden <zachlowden1@gmail.com>", body="x", in_reply_to="<a>")
    assert feedback._is_reply_from_owner(ok) is True
    # lookalike domain (address as a substring) must be REJECTED
    look = _mk_msg(subject="Re: 🧭 Repo proposals — week of 2026-07-01",
                   frm="zachlowden1@gmail.com.evil.com", body="x", in_reply_to="<a>")
    assert feedback._is_reply_from_owner(look) is False
    # owner address only in the DISPLAY NAME; real addr-spec is the attacker's
    spoof = _mk_msg(subject="Re: 🧭 Repo proposals — week of 2026-07-01",
                    frm='"zachlowden1@gmail.com" <attacker@evil.com>', body="x", in_reply_to="<a>")
    assert feedback._is_reply_from_owner(spoof) is False
