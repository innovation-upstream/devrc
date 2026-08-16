"""🔴 D3 — an un-approved draft must have NO CODE ROUTE to the Signal API.

The proposal is explicit that a documented convention ("call approve first") is
not good enough, and that a green-but-bypassable gate is worse than no gate. So
this suite does not check that the happy path works; it TRIES TO GET AROUND the
gate, six ways, and requires each attempt to fail with the gate's OWN error type
(`SendGateError`) — not "some exception", which a typo or a different guard would
also produce.

The routes attempted:
  1. `send_approved()` on a pending draft;
  2. `transmit_approved()` with no capability at all;
  3. `transmit_approved()` with a hand-rolled look-alike object;
  4. constructing a `SendAuthorization` directly;
  5. re-using a spent capability (replay);
  6. approving a draft that was already sent, to re-arm it.

Plus a STRUCTURAL check that there is exactly one call site posting to the send
endpoint in the whole of `scripts/signal/` — the property that makes the five
behavioural checks above exhaustive rather than a sample.
"""
import re
import sys
import types
from pathlib import Path

import pytest

import clawgate
import consumer
import _signal_db
from _signal_db import SendGateError

SIGNAL_DIR = Path(consumer.__file__).resolve().parent
PEER = "+15550101"
SELF_NUMBER = "+15559090"
SERVER_TS = 1723500000001


class Poster:
    """Records every HTTP post it is asked to make. It must stay EMPTY on refusal."""

    def __init__(self):
        self.calls = []

    def __call__(self, url, json=None, timeout=None):
        self.calls.append({"url": url, "json": json, "timeout": timeout})
        return types.SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {"timestamp": SERVER_TS},
        )


def _pending(db, body="an unapproved message"):
    return db.draft_message(recipient=PEER, body=body, self_number=SELF_NUMBER)


# --------------------------------------------------------------------------- #
# Route 1 — the obvious one
# --------------------------------------------------------------------------- #
def test_send_approved_refuses_a_pending_draft(db):
    draft = _pending(db)
    poster = Poster()
    with pytest.raises(SendGateError) as exc:
        db.send_approved(draft["id"],
                         transmit=lambda a, **kw: consumer.transmit_approved(
                             a, poster=poster, **kw))
    assert "send_state='pending'" in str(exc.value)
    assert "D3 approval gate" in str(exc.value)
    assert poster.calls == []                     # nothing reached the wire


def test_send_approved_refuses_a_draft_that_does_not_exist(db):
    with pytest.raises(SendGateError):
        db.send_approved(4242)


def test_send_approved_refuses_an_already_sent_draft(db):
    draft = _pending(db)
    db.approve_draft(draft["id"], approval_ref="cg-once")
    db.send_approved(draft["id"], transmit=lambda a, **kw: {"timestamp": SERVER_TS})
    with pytest.raises(SendGateError) as exc:
        db.send_approved(draft["id"], transmit=lambda a, **kw: {"timestamp": 9})
    assert "send_state='sent'" in str(exc.value)


# --------------------------------------------------------------------------- #
# Routes 2 + 3 — go around the DB layer entirely
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("forged", [
    None,
    "approved",
    {"draft_id": 1, "recipient": PEER, "body": "x", "_nonce": "deadbeef"},
    types.SimpleNamespace(draft_id=1, recipient=PEER, body="x", _nonce="deadbeef"),
])
def test_transmit_refuses_anything_that_is_not_a_real_capability(forged):
    """🔴 The refusal must come from the TYPE guard, named explicitly.

    A mutation sweep caught this test passing for the wrong reason: with the
    `isinstance` check disabled, the single-use NONCE check refused these
    forgeries instead — a different guard's error, so the mutant survived a green
    suite. Asserting the type guard's own wording (and that it is NOT the
    spent-nonce wording) is what makes the kill real.
    """
    poster = Poster()
    with pytest.raises(SendGateError) as exc:
        consumer.transmit_approved(forged, recipient=PEER, body="bypass attempt",
                                   poster=poster)
    message = str(exc.value)
    assert "D3 approval gate" in message
    assert "a SendAuthorization minted from an approved draft is required" in message
    assert f"got {type(forged).__name__}" in message
    assert "already been spent" not in message      # ... not the OTHER guard
    assert poster.calls == []


def test_transmit_refuses_a_subclass_forged_with_a_guessed_nonce():
    """Even the right TYPE is not enough — the nonce must have been issued."""
    auth = object.__new__(_signal_db.SendAuthorization)
    object.__setattr__(auth, "draft_id", 1)
    object.__setattr__(auth, "recipient", PEER)
    object.__setattr__(auth, "body", "x")
    object.__setattr__(auth, "_nonce", "0" * 32)
    poster = Poster()
    with pytest.raises(SendGateError) as exc:
        consumer.transmit_approved(auth, recipient=PEER, body="x", poster=poster)
    assert "already been spent" in str(exc.value)
    assert poster.calls == []


# --------------------------------------------------------------------------- #
# Route 4 — mint one yourself
# --------------------------------------------------------------------------- #
def test_send_authorization_cannot_be_constructed_directly():
    with pytest.raises(SendGateError) as exc:
        _signal_db.SendAuthorization(draft_id=1, recipient=PEER, body="x")
    assert "cannot be constructed directly" in str(exc.value)


def test_minting_refuses_every_non_approved_state():
    for state in (None, "pending", "sent", "", "APPROVED", "approved ",):
        with pytest.raises(SendGateError):
            _signal_db._mint_send_authorization({"id": 7, "send_state": state})


def test_minting_positive_control_an_approved_draft_yields_a_capability():
    """The refusals above are about the STATE, not a minter that never mints."""
    auth = _signal_db._mint_send_authorization(
        {"id": 8, "send_state": _signal_db.STATE_APPROVED, "recipient": PEER,
         "body": "ok"})
    assert isinstance(auth, _signal_db.SendAuthorization)
    assert auth.draft_id == 8


# --------------------------------------------------------------------------- #
# Route 5 — replay
# --------------------------------------------------------------------------- #
def test_an_approved_draft_transmits_exactly_once(db):
    draft = _pending(db, body="approved and sent once")
    db.approve_draft(draft["id"], approval_ref="cg-exactly-once")
    poster = Poster()

    sent = db.send_approved(
        draft["id"],
        transmit=lambda a, **kw: consumer.transmit_approved(a, poster=poster, **kw))
    assert len(poster.calls) == 1
    assert sent["message_timestamp"] == SERVER_TS

    # The state moved to `sent`, so a second send is refused ...
    with pytest.raises(SendGateError):
        db.send_approved(draft["id"],
                         transmit=lambda a, **kw: consumer.transmit_approved(
                             a, poster=poster, **kw))
    assert len(poster.calls) == 1                 # ... and still one call


def test_a_spent_capability_cannot_be_replayed():
    auth = _signal_db._mint_send_authorization(
        {"id": 9, "send_state": _signal_db.STATE_APPROVED, "recipient": PEER,
         "body": "replay me"})
    poster = Poster()
    consumer.transmit_approved(auth, recipient=PEER, body="replay me", poster=poster)
    assert len(poster.calls) == 1
    with pytest.raises(SendGateError) as exc:
        consumer.transmit_approved(auth, recipient=PEER, body="replay me",
                                   poster=poster)
    assert "single-use" in str(exc.value)
    assert len(poster.calls) == 1


# --------------------------------------------------------------------------- #
# Route 6 — re-arm an already-sent draft
# --------------------------------------------------------------------------- #
def test_approving_a_sent_draft_is_refused(db):
    draft = _pending(db)
    db.approve_draft(draft["id"], approval_ref="cg-rearm")
    db.send_approved(draft["id"], transmit=lambda a, **kw: {"timestamp": SERVER_TS})
    with pytest.raises(SendGateError) as exc:
        db.approve_draft(draft["id"], approval_ref="cg-rearm-again")
    assert "may be approved" in str(exc.value)


def test_approving_a_missing_draft_is_refused(db):
    with pytest.raises(SendGateError):
        db.approve_draft(9999, approval_ref="cg-nope")


# --------------------------------------------------------------------------- #
# The structural claim that makes the above exhaustive
# --------------------------------------------------------------------------- #
def test_exactly_one_call_site_posts_to_the_send_endpoint():
    """A ledger of send-endpoint call sites, derived by reading every module.

    Fails when the set GROWS (a second, ungated send path) or SHRINKS (the gated
    one moved or was deleted). Both directions matter: the whole gate rests on
    there being ONE door.
    """
    modules = sorted(p for p in SIGNAL_DIR.glob("*.py"))
    assert len(modules) >= 4, f"HARNESS BROKEN: only found {modules}"
    senders = {}
    for path in modules:
        src = path.read_text(encoding="utf-8")
        for match in re.finditer(r"SEND_PATH", src):
            line = src[:match.start()].count("\n") + 1
            senders.setdefault(path.name, []).append(line)
    assert set(senders) == {"consumer.py"}, senders
    # Two mentions in consumer.py: the constant's definition and its one use.
    assert len(senders["consumer.py"]) == 2, senders


def test_the_only_send_call_site_is_inside_transmit_approved():
    src = Path(consumer.__file__).read_text(encoding="utf-8")
    body = src.split("def transmit_approved(")[1].split("\ndef ")[0]
    assert "SEND_PATH" in body
    assert "spend_authorization(auth)" in body
    # The gate runs BEFORE the URL is even built.
    assert body.index("spend_authorization(auth)") < body.index("SEND_PATH")


def test_clawgate_module_cannot_transmit():
    """The notifier must be exactly that — it must not become a second door."""
    src = Path(clawgate.__file__).read_text(encoding="utf-8")
    assert "SEND_PATH" not in src
    assert "/v2/send" not in src
    assert clawgate.ENDPOINT.endswith("/api/tasks")


# --------------------------------------------------------------------------- #
# The draft is never lost, and the clawgate notification degrades gracefully
# --------------------------------------------------------------------------- #
def test_a_refused_send_leaves_the_draft_intact_and_pending(db):
    draft = _pending(db, body="still here afterwards")
    with pytest.raises(SendGateError):
        db.send_approved(draft["id"], transmit=lambda a, **kw: {"timestamp": 1})
    stored = db.get_draft(draft["id"])
    assert stored["send_state"] == _signal_db.STATE_PENDING
    assert stored["body"] == "still here afterwards"
    assert db.list_drafts(state=_signal_db.STATE_PENDING)


def test_a_draft_is_persisted_before_any_approval_exists(db):
    draft = _pending(db, body="durable from the first moment")
    assert db.conn.count("messages") == 1
    row = db.conn.rows("SELECT is_outbound, send_state, body FROM signal.messages")[0]
    assert row["is_outbound"]
    assert row["send_state"] == _signal_db.STATE_PENDING
    assert row["body"] == "durable from the first moment"
    assert draft["message_timestamp"] < 0


def test_draft_to_a_phone_number_transmits_to_that_number_not_a_placeholder(db):
    """A draft addressed to a bare number must not be sent to a synthetic uuid."""
    draft = _pending(db)
    db.approve_draft(draft["id"], approval_ref="cg-recipient")
    poster = Poster()
    db.send_approved(draft["id"],
                     transmit=lambda a, **kw: consumer.transmit_approved(
                         a, poster=poster, **kw))
    assert poster.calls[0]["json"]["recipients"] == [PEER]


def test_emit_draft_task_is_a_graceful_noop_without_a_token(monkeypatch):
    monkeypatch.delenv("CLAWGATE_HOOK_TOKEN", raising=False)
    posted = []
    module = types.ModuleType("requests")
    module.post = lambda *a, **k: posted.append(a)
    monkeypatch.setitem(sys.modules, "requests", module)
    assert clawgate.emit_draft_task(draft_id=1, recipient=PEER, body="x") is False
    assert posted == []


def test_emit_draft_task_posts_the_card_when_a_token_is_set(monkeypatch):
    monkeypatch.setenv("CLAWGATE_HOOK_TOKEN", "tok-signal-1")
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

    assert clawgate.emit_draft_task(draft_id=17, recipient=PEER,
                                    body="please approve") is True
    call = calls[0]
    assert call["url"] == clawgate.ENDPOINT
    assert call["headers"]["Authorization"] == "Bearer tok-signal-1"
    assert "title" not in call["json"]              # clawgate ignores `title`
    assert "17" in call["json"]["directory"]
    assert "raised" in calls


def test_clawgate_card_carries_the_recipient_and_the_approval_command():
    payload = clawgate.build_draft_payload(draft_id=23, recipient=PEER,
                                           body="the drafted text")
    assert set(payload) == {"directory", "body"}
    assert PEER in payload["body"]
    assert "the drafted text" in payload["body"]
    assert "approve 23" in payload["body"]
    assert "send 23" in payload["body"]


def test_clawgate_card_title_is_length_capped():
    payload = clawgate.build_draft_payload(draft_id=1, recipient="+1" + "5" * 400,
                                           body="x")
    assert len(payload["directory"]) <= clawgate.TITLE_MAX


def test_clawgate_card_truncates_a_huge_body():
    payload = clawgate.build_draft_payload(draft_id=2, recipient=PEER,
                                           body="z" * 5000)
    assert len(payload["body"]) < 2000
    assert payload["body"].count("z") == clawgate.BODY_PREVIEW_MAX
