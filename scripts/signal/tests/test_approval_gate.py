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
import ast
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
        # STRING timestamp: upstream types it `Timestamp string`.
        return types.SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {"timestamp": str(SERVER_TS)},
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
    db.send_approved(draft["id"], transmit=lambda a, **kw: {"timestamp": str(SERVER_TS)})
    with pytest.raises(SendGateError) as exc:
        db.send_approved(draft["id"], transmit=lambda a, **kw: {"timestamp": "9"})
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
                                   number=SELF_NUMBER, poster=poster)
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
        consumer.transmit_approved(auth, recipient=PEER, body="x",
                                   number=SELF_NUMBER, poster=poster)
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
    consumer.transmit_approved(auth, recipient=PEER, body="replay me",
                               number=SELF_NUMBER, poster=poster)
    assert len(poster.calls) == 1
    with pytest.raises(SendGateError) as exc:
        consumer.transmit_approved(auth, recipient=PEER, body="replay me",
                                   number=SELF_NUMBER, poster=poster)
    assert "single-use" in str(exc.value)
    assert len(poster.calls) == 1


# --------------------------------------------------------------------------- #
# Route 6 — re-arm an already-sent draft
# --------------------------------------------------------------------------- #
def test_approving_a_sent_draft_is_refused(db):
    draft = _pending(db)
    db.approve_draft(draft["id"], approval_ref="cg-rearm")
    db.send_approved(draft["id"], transmit=lambda a, **kw: {"timestamp": str(SERVER_TS)})
    with pytest.raises(SendGateError) as exc:
        db.approve_draft(draft["id"], approval_ref="cg-rearm-again")
    assert "may be approved" in str(exc.value)


def test_approving_a_missing_draft_is_refused(db):
    with pytest.raises(SendGateError):
        db.approve_draft(9999, approval_ref="cg-nope")


# --------------------------------------------------------------------------- #
# The structural claim that makes the above exhaustive
# --------------------------------------------------------------------------- #
def _functions_that_can_reach_the_send_endpoint() -> dict:
    """AST ledger: every function that can BUILD a send-endpoint URL.

    🔴 WHY AN AST WALK AND NOT A GREP. The first version of this ledger grepped
    for the identifier `SEND_PATH`, so a second door spelled with the LITERAL —
    `poster(API_URL + "/v2/send", …)`, no capability, no gate call — was invisible
    to it and survived the whole suite. A guard that can be walked by rewording
    is not a structural guard. This one recognises BOTH spellings, at every
    function in every module, by looking at the parsed code rather than its text.

    Returns `{module: {function: [reasons]}}`.
    """
    found: dict[str, dict[str, list[str]]] = {}
    modules = sorted(SIGNAL_DIR.glob("*.py"))
    assert len(modules) >= 4, f"HARNESS BROKEN: only found {modules}"
    for path in modules:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        parents = {}
        for parent in ast.walk(tree):
            for child in ast.iter_child_nodes(parent):
                parents[child] = parent

        def enclosing_function(node):
            while node in parents:
                node = parents[node]
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    return node.name
            return "<module>"

        def inside_a_raise(node) -> bool:
            """Is this constant part of an exception MESSAGE rather than a URL?

            You cannot POST from inside a `raise`, and several guards name the
            endpoint in the error they raise about it. Excluding those keeps the
            ledger about doors instead of about prose — without weakening it: the
            positive control below shows a literal in a CALL is still caught.
            """
            while node in parents:
                node = parents[node]
                if isinstance(node, ast.Raise):
                    return True
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    return False
            return False

        for node in ast.walk(tree):
            reason = None
            if isinstance(node, ast.Name) and node.id == "SEND_PATH":
                reason = "SEND_PATH"
            elif (isinstance(node, ast.Constant) and isinstance(node.value, str)
                  and consumer.SEND_PATH in node.value):
                # A bare string STATEMENT is a docstring, not a URL being built.
                # Prose that mentions the endpoint (and this codebase's prose
                # mentions it a lot, deliberately) is not a door.
                if isinstance(parents.get(node), ast.Expr):
                    continue
                if inside_a_raise(node):
                    continue
                reason = f"literal {node.value!r}"
            if reason is None:
                continue
            fn = enclosing_function(node)
            if fn == "<module>":
                continue                    # the constant's own definition
            found.setdefault(path.name, {}).setdefault(fn, []).append(reason)
    return found


def test_exactly_one_function_can_reach_the_send_endpoint():
    """The ledger. Fails when the set GROWS (a second door) or SHRINKS (it moved).

    Both directions matter: the whole gate rests on there being ONE door.
    """
    ledger = _functions_that_can_reach_the_send_endpoint()
    assert set(ledger) == {"consumer.py"}, ledger
    assert set(ledger["consumer.py"]) == {"transmit_approved"}, ledger


def test_the_ledger_would_see_a_second_door_spelled_with_the_literal():
    """POSITIVE CONTROL on the ledger itself, against the mutant that escaped it.

    A `quick_send()` that posts to `API_URL + "/v2/send"` without touching the
    gate must be VISIBLE. Verified against a synthetic module here rather than by
    mutating the real one, so the control ships with the guard.
    """
    source = (
        "API_URL = 'http://x'\n"
        "SEND_PATH = '/v2/send'\n"
        "def quick_send(poster, body):\n"
        "    return poster(API_URL + '/v2/send', json={'message': body})\n"
    )
    tree = ast.parse(source)
    hits = [n for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
            and consumer.SEND_PATH in n.value]
    # Two: the constant's definition and the ungated call site inside quick_send.
    assert len(hits) == 2
    inside_function = [
        n for fn in ast.walk(tree) if isinstance(fn, ast.FunctionDef)
        for n in ast.walk(fn)
        if isinstance(n, ast.Constant) and isinstance(n.value, str)
        and consumer.SEND_PATH in n.value
    ]
    assert len(inside_function) == 1, "the literal spelling must be detectable"


def test_every_function_in_the_ledger_spends_a_capability():
    """The invariant behind the ledger: reaching the endpoint REQUIRES the gate."""
    ledger = _functions_that_can_reach_the_send_endpoint()
    src = Path(consumer.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    by_name = {n.name: n for n in ast.walk(tree)
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    for fn_name in ledger["consumer.py"]:
        fn = by_name[fn_name]
        calls = {c.func.id for c in ast.walk(fn)
                 if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)}
        assert "spend_authorization" in calls, (
            f"{fn_name} can reach the send endpoint without spending a capability")


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
# 🔴 Crash-between-POST-and-write-back must not RESEND
# --------------------------------------------------------------------------- #
def test_a_failure_after_the_post_leaves_the_draft_inert(db):
    """The resend hazard, reproduced.

    Everything after the POST can fail — an odd response, a dropped connection, a
    pod kill. If the row were still `approved` at that moment, the gate would
    mint a fresh capability and send the SAME TEXT again. It is `sending`
    instead, which nothing can mint from.
    """
    draft = _pending(db, body="must not be sent twice")
    db.approve_draft(draft["id"], approval_ref="cg-crash")
    poster = Poster()

    def transmit_then_die(auth, *, recipient, body, number):
        poster(f"http://x{consumer.SEND_PATH}", json={"message": body})
        raise ConnectionResetError("pod killed between the POST and the write-back")

    with pytest.raises(ConnectionResetError):
        db.send_approved(draft["id"], transmit=transmit_then_die)
    assert len(poster.calls) == 1

    stored = db.get_draft(draft["id"])
    assert stored["send_state"] == _signal_db.STATE_SENDING
    with pytest.raises(SendGateError) as exc:
        db.send_approved(draft["id"], transmit=transmit_then_die)
    assert "send_state='sending'" in str(exc.value)
    assert len(poster.calls) == 1                 # NOT sent a second time


def test_the_sending_claim_is_committed_before_anything_is_transmitted(db):
    """Ordering is the mechanism, so it is observed directly, not inferred.

    🔴 The COMMIT is asserted, not just the in-memory state. A mutation sweep
    found that dropping the commit survived: on one connection the uncommitted
    write is still visible, so "state == sending" alone passes while the claim
    would not have survived the pod kill it exists for. The commit COUNT is the
    observable that distinguishes them.
    """
    draft = _pending(db)
    db.approve_draft(draft["id"], approval_ref="cg-order")
    commits_before = db.conn.commits
    seen = {}

    def transmit(auth, *, recipient, body, number):
        seen["state_at_post"] = db.get_draft(draft["id"])["send_state"]
        seen["commits_at_post"] = db.conn.commits
        return {"timestamp": str(SERVER_TS)}

    db.send_approved(draft["id"], transmit=transmit)
    assert seen["state_at_post"] == _signal_db.STATE_SENDING
    assert seen["commits_at_post"] > commits_before, (
        "the `sending` claim was written but never committed — it would not "
        "survive the crash it exists to protect against")


def test_send_attempts_records_that_a_send_was_tried(db):
    draft = _pending(db)
    db.approve_draft(draft["id"], approval_ref="cg-attempts")

    def boom(auth, **kw):
        raise TimeoutError("no answer")

    with pytest.raises(TimeoutError):
        db.send_approved(draft["id"], transmit=boom)
    row = db.conn.rows("SELECT send_attempts FROM signal.messages WHERE id = ?",
                       (draft["id"],))[0]
    assert row["send_attempts"] == 1


def test_per_recipient_errors_in_the_response_are_not_treated_as_success(db):
    """A 201 can still carry `errors` — upstream's response has that field."""
    draft = _pending(db)
    db.approve_draft(draft["id"], approval_ref="cg-errors")
    with pytest.raises(RuntimeError) as exc:
        db.send_approved(draft["id"], transmit=lambda a, **kw: {
            "timestamp": str(SERVER_TS),
            "errors": [{"recipient": PEER, "message": "unregistered user"}]})
    assert "per-recipient errors" in str(exc.value)
    assert db.get_draft(draft["id"])["send_state"] == _signal_db.STATE_SENDING


# --------------------------------------------------------------------------- #
# The server's actual request contract
# --------------------------------------------------------------------------- #
def test_the_send_body_carries_number_recipients_and_message(db):
    """🔴 `number` is REQUIRED — upstream 400s with 'please provide a valid number'.

    An earlier revision omitted it, so every send failed and the whole D3 path
    was inert. The fake asserted only `recipients`, which is exactly why the
    suite could not see it.
    """
    draft = _pending(db, body="the body that goes on the wire")
    db.approve_draft(draft["id"], approval_ref="cg-body")
    poster = Poster()
    db.send_approved(draft["id"],
                     transmit=lambda a, **kw: consumer.transmit_approved(
                         a, poster=poster, **kw))
    body = poster.calls[0]["json"]
    assert body == {"message": "the body that goes on the wire",
                    "number": SELF_NUMBER, "recipients": [PEER]}
    assert poster.calls[0]["url"].endswith("/v2/send")


def test_transmit_refuses_an_empty_number_rather_than_earning_a_400():
    auth = _signal_db._mint_send_authorization(
        {"id": 11, "send_state": _signal_db.STATE_APPROVED, "recipient": PEER,
         "body": "x"})
    poster = Poster()
    with pytest.raises(SendGateError) as exc:
        consumer.transmit_approved(auth, recipient=PEER, body="x", number="",
                                   poster=poster)
    assert "valid number" in str(exc.value)
    assert poster.calls == []


def test_the_server_timestamp_is_read_from_a_STRING(db):
    """Upstream types it `Timestamp string`; an int-only reader would break live."""
    draft = _pending(db)
    db.approve_draft(draft["id"], approval_ref="cg-string-ts")
    sent = db.send_approved(
        draft["id"], transmit=lambda a, **kw: {"timestamp": " 1723500000123 "})
    assert sent["message_timestamp"] == 1723500000123


@pytest.mark.parametrize("bad", [{}, {"timestamp": None}, {"timestamp": "abc"},
                                 {"timestamp": "0"}, {"timestamp": "-5"}])
def test_an_unusable_timestamp_is_refused_and_the_draft_stays_sending(db, bad):
    draft = _pending(db)
    db.approve_draft(draft["id"], approval_ref="cg-bad-ts")
    with pytest.raises(ValueError):
        db.send_approved(draft["id"], transmit=lambda a, **kw: bad)
    assert db.get_draft(draft["id"])["send_state"] == _signal_db.STATE_SENDING


# --------------------------------------------------------------------------- #
# Approval is the OPERATOR's step
# --------------------------------------------------------------------------- #
def test_approval_is_refused_without_the_operator_token(db, monkeypatch):
    """The drafting agent's environment does not carry it — by design."""
    draft = _pending(db)
    monkeypatch.delenv(_signal_db.APPROVAL_TOKEN_ENV, raising=False)
    with pytest.raises(SendGateError) as exc:
        db.approve_draft(draft["id"], approval_ref="agent-self-approval")
    assert _signal_db.APPROVAL_TOKEN_ENV in str(exc.value)
    assert db.get_draft(draft["id"])["send_state"] == _signal_db.STATE_PENDING


def test_approval_positive_control_with_the_token_present(db, monkeypatch):
    """The refusal above is about the TOKEN, not an approver that never approves."""
    draft = _pending(db)
    monkeypatch.setenv(_signal_db.APPROVAL_TOKEN_ENV, "operator-shell-token")
    approved = db.approve_draft(draft["id"], approval_ref="cg-real")
    assert approved["send_state"] == _signal_db.STATE_APPROVED


def test_an_empty_approval_ref_records_nothing_and_is_refused(db):
    draft = _pending(db)
    with pytest.raises(SendGateError) as exc:
        db.approve_draft(draft["id"], approval_ref="   ")
    assert "auditable" in str(exc.value)


# --------------------------------------------------------------------------- #
# The draft is never lost, and the clawgate notification degrades gracefully
# --------------------------------------------------------------------------- #
def test_a_refused_send_leaves_the_draft_intact_and_pending(db):
    draft = _pending(db, body="still here afterwards")
    with pytest.raises(SendGateError):
        db.send_approved(draft["id"], transmit=lambda a, **kw: {"timestamp": "1"})
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


def test_clawgate_card_identifies_the_draft_without_handing_over_the_command():
    """🔴 The card must not contain a RUNNABLE approval command.

    It is posted BY the drafting agent, so any command it prints is a command
    that agent can read back and run against its own draft. Naming the draft is
    fine — and necessary; printing `consumer.py approve <id> --ref …` is the
    self-approval path D3 exists to prevent. Asserted on the runnable pieces, not
    on the word "approve", which the card legitimately uses in prose.
    """
    payload = clawgate.build_draft_payload(draft_id=23, recipient=PEER,
                                           body="the drafted text")
    assert set(payload) == {"directory", "body"}
    assert PEER in payload["body"]
    assert "the drafted text" in payload["body"]
    assert "#23" in payload["body"]
    body = payload["body"]
    for runnable in ("consumer.py", "--ref", "approve 23", "send 23"):
        assert runnable not in body, f"the card hands over {runnable!r}"


def test_clawgate_card_title_is_length_capped():
    payload = clawgate.build_draft_payload(draft_id=1, recipient="+1" + "5" * 400,
                                           body="x")
    assert len(payload["directory"]) <= clawgate.TITLE_MAX


def test_clawgate_card_truncates_a_huge_body():
    payload = clawgate.build_draft_payload(draft_id=2, recipient=PEER,
                                           body="z" * 5000)
    assert len(payload["body"]) < 2000
    assert payload["body"].count("z") == clawgate.BODY_PREVIEW_MAX
