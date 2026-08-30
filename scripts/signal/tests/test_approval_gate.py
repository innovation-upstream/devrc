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

Plus a STRUCTURAL ledger of the functions that can build a send-endpoint URL.
🔴 It is a TRIPWIRE over enumerated spellings, NOT a proof that no other door can
exist — an earlier version claimed the latter and was then walked by four
spellings a blind audit found in minutes. What it supports is narrower and true:
the door this codebase HAS is the gated one, every function in the ledger is
asserted to spend a capability, and adding an obvious second door fails the
suite. The unconditional guarantee is the capability check inside
`transmit_approved()`, which no caller can skip.
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
# The structural ledger — a tripwire, scoped in its own docstring
# --------------------------------------------------------------------------- #
def _functions_that_can_reach_the_send_endpoint(root: Path = SIGNAL_DIR) -> dict:
    """AST ledger: every function that can BUILD a send-endpoint URL.

    🔴 WHAT THIS IS AND IS NOT. It is a TRIPWIRE over the spellings enumerated
    below, not a proof that no other door exists — a determined `exec()` or a URL
    assembled from runtime data would pass it, and no static check of this size
    can say otherwise. The claim it supports is narrow and worth stating exactly:
    *the door this codebase actually has is the gated one, and adding an obvious
    second one fails the suite.* The real guarantee is the capability check
    inside `transmit_approved()`, which every function in this ledger is
    separately asserted to call.

    An earlier version grepped for the identifier `SEND_PATH` and was walked by
    FOUR spellings a blind audit found in minutes. Recognised now:

      * the `SEND_PATH` name, and any local ALIAS of it (`from consumer import
        SEND_PATH as _SP`);
      * `getattr(consumer, "SEND_PATH")`;
      * a string literal containing the path;
      * a SPLIT or FORMATTED literal — the constant parts of any expression are
        joined, so `"/v2" + "/send"` and `"%s/v2/%s" % …` are caught too.

    Walks `rglob`, not `glob`: a module in a subdirectory was never visited.
    Returns `{module: {function: [reasons]}}`.
    """
    found: dict[str, dict[str, list[str]]] = {}
    modules = sorted(p for p in root.rglob("*.py")
                     if "tests" not in p.parts and "__pycache__" not in p.parts)
    assert modules, f"HARNESS BROKEN: no modules under {root}"
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

        # Local names that ALIAS the constant: `from consumer import SEND_PATH as
        # _SP`, `_SP = SEND_PATH`. Without these a one-line rename walks the guard.
        aliases = {"SEND_PATH"}
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    if alias.name == "SEND_PATH":
                        aliases.add(alias.asname or alias.name)
            elif isinstance(node, ast.Assign) and isinstance(node.value, ast.Name):
                if node.value.id in aliases:
                    for target in node.targets:
                        if isinstance(target, ast.Name):
                            aliases.add(target.id)

        def joined_constants(node) -> str:
            """Every string constant inside an expression, concatenated.

            Catches a SPLIT or FORMATTED path — `"/v2" + "/send"`,
            `"%s/v2/%s" % (...)`, an f-string — which a whole-literal check misses.
            """
            return "".join(
                n.value for n in ast.walk(node)
                if isinstance(n, ast.Constant) and isinstance(n.value, str))

        for node in ast.walk(tree):
            reason = None
            if isinstance(node, ast.Name) and node.id in aliases:
                reason = f"name {node.id}"
            elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                  and node.func.id == "getattr"
                  and any(isinstance(a, ast.Constant) and a.value == "SEND_PATH"
                          for a in node.args)):
                reason = 'getattr(..., "SEND_PATH")'
            elif isinstance(node, (ast.BinOp, ast.JoinedStr)):
                merged = joined_constants(node)
                if consumer.SEND_PATH in merged or (
                        "/v2" in merged and "send" in merged):
                    if inside_a_raise(node):
                        continue
                    reason = f"assembled path {merged!r}"
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


# Five ungated back doors, one per spelling. A blind audit walked the previous
# ledger with four of these; each is now a named case run through the REAL
# function.
BACKDOOR_SPELLINGS = {
    "plain_literal": '    return poster(API_URL + "/v2/send", json={"m": body})',
    "aliased_name": "    return poster(API_URL + _SP, json={'m': body})",
    "split_literal": '    return poster(API_URL + "/v2" + "/send", json={"m": body})',
    "getattr_lookup":
        '    return poster(API_URL + getattr(consumer, "SEND_PATH"), json={"m": body})',
    "percent_format": '    return poster("%s/v2/%s" % (API_URL, "send"), json={"m": body})',
}


def _write_backdoor_module(tmp_path: Path, name: str, body_line: str) -> Path:
    module = tmp_path / f"backdoor_{name}.py"
    module.write_text(
        "import consumer\n"
        "from consumer import SEND_PATH as _SP\n"
        'API_URL = "http://x"\n'
        f"def quick_send(poster, body):\n{body_line}\n",
        encoding="utf-8")
    return module


@pytest.mark.parametrize("spelling", sorted(BACKDOOR_SPELLINGS))
def test_the_ledger_catches_every_spelling_of_a_second_door(tmp_path, spelling):
    """🔴 NEGATIVE CONTROL, run through the REAL ledger function.

    The previous control re-implemented a mini walk over synthetic source, so it
    validated the IDEA and not the INSTRUMENT — and the instrument was in fact
    walked by four of these five spellings while the suite stayed green. Each
    case now writes a module to a temp dir and calls
    `_functions_that_can_reach_the_send_endpoint()` on it.
    """
    _write_backdoor_module(tmp_path, spelling, BACKDOOR_SPELLINGS[spelling])
    ledger = _functions_that_can_reach_the_send_endpoint(tmp_path)
    assert ledger, f"the {spelling!r} back door was INVISIBLE to the ledger"
    functions = {fn for mod in ledger.values() for fn in mod}
    assert "quick_send" in functions, ledger


def test_the_ledger_is_quiet_on_a_module_with_no_door(tmp_path):
    """POSITIVE CONTROL the other way: it does not flag everything it reads."""
    (tmp_path / "innocent.py").write_text(
        'API_URL = "http://x"\n'
        "def fetch(getter):\n"
        '    return getter(API_URL + "/v1/attachments/abc")\n',
        encoding="utf-8")
    assert _functions_that_can_reach_the_send_endpoint(tmp_path) == {}


def test_the_ledger_walks_subdirectories(tmp_path):
    """`glob` is not `rglob` — a module one directory down was never visited."""
    nested = tmp_path / "deeper"
    nested.mkdir()
    _write_backdoor_module(nested, "nested", BACKDOOR_SPELLINGS["plain_literal"])
    ledger = _functions_that_can_reach_the_send_endpoint(tmp_path)
    assert ledger, "a back door in a SUBDIRECTORY was invisible to the ledger"


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


@pytest.mark.parametrize("errors", [
    # The shape upstream actually sends (`SendMessageErrors{Recipients: …}`) ...
    {"recipients": [{"recipient": PEER, "message": "unregistered user"}]},
    # ... and a bare list, in case the encoding differs by version.
    [{"recipient": PEER, "message": "unregistered user"}],
])
def test_per_recipient_errors_in_the_response_are_not_treated_as_success(db, errors):
    """A 201 can still carry `errors` — upstream's response has that field.

    Parametrised over BOTH shapes: the earlier test fed a bare list while
    upstream sends an object, and "both are truthy so the guard holds" is a
    reason to test the real shape, not a reason to skip it.
    """
    draft = _pending(db)
    db.approve_draft(draft["id"], approval_ref="cg-errors")
    with pytest.raises(RuntimeError) as exc:
        db.send_approved(draft["id"], transmit=lambda a, **kw: {
            "timestamp": str(SERVER_TS), "errors": errors})
    assert "per-recipient errors" in str(exc.value)
    assert db.get_draft(draft["id"])["send_state"] == _signal_db.STATE_SENDING


def test_an_error_response_reports_the_ERROR_not_a_timestamp_complaint(db):
    """Ordering: the reason must survive, not be masked by a parsing gripe.

    A response carrying both an error and an unusable timestamp used to raise the
    timestamp `ValueError`, hiding the per-recipient reason — the one piece of
    information the operator needs to reconcile.
    """
    draft = _pending(db)
    db.approve_draft(draft["id"], approval_ref="cg-error-order")
    with pytest.raises(RuntimeError) as exc:
        db.send_approved(draft["id"], transmit=lambda a, **kw: {
            "timestamp": "", "errors": {"recipients": [{"message": "rate limited"}]}})
    assert "rate limited" in str(exc.value)
    assert "sync-echo dedupe" not in str(exc.value)


def test_a_sender_without_a_number_is_refused_BEFORE_the_claim(db):
    """🔴 A refusal must not strand the draft.

    `account_number()` used to be an ARGUMENT to `transmit(...)`, which evaluates
    AFTER the claim has committed — so a draft whose sender had no phone number
    (`draft_message(self_uuid=…)`, a supported signature) ended `sending` with
    nothing transmitted: stranded, and unsendable forever.
    """
    draft = db.draft_message(recipient=PEER, body="no sender number",
                             self_uuid="90909090-9090-4909-8909-909090909090")
    db.approve_draft(draft["id"], approval_ref="cg-no-number")
    poster = Poster()

    with pytest.raises(SendGateError) as exc:
        db.send_approved(draft["id"],
                         transmit=lambda a, **kw: consumer.transmit_approved(
                             a, poster=poster, **kw))
    assert "no sending phone number" in str(exc.value)
    assert poster.calls == []
    # Still APPROVED — a refusal is recoverable, a strand is not.
    assert db.get_draft(draft["id"])["send_state"] == _signal_db.STATE_APPROVED
    row = db.conn.rows("SELECT send_attempts FROM signal.messages WHERE id = ?",
                       (draft["id"],))[0]
    assert row["send_attempts"] == 0            # the claim never ran


def test_two_senders_that_BOTH_minted_still_transmit_once(db):
    """🔴 The claim is the lock, and it has to be atomic.

    This is the real race, and the one `_ISSUED_NONCES` cannot cover: both
    senders read `approved` and BOTH mint a valid capability before either
    claims. (The nonce registry is per-process — two pods or two shells share
    nothing.) The database row is the only thing both can contend on, so the
    transition has to be a single conditional statement with the loser told.
    """
    draft = _pending(db, body="exactly once, please")
    db.approve_draft(draft["id"], approval_ref="cg-race")

    auth_a = _signal_db._mint_send_authorization(db.get_draft(draft["id"]))
    auth_b = _signal_db._mint_send_authorization(db.get_draft(draft["id"]))
    assert auth_a is not auth_b          # both are genuine, both would transmit

    db._claim_for_sending(draft["id"])   # sender A wins the row
    with pytest.raises(SendGateError) as exc:
        db._claim_for_sending(draft["id"])   # sender B loses AT THE DATABASE
    assert "could not be claimed" in str(exc.value)

    poster = Poster()
    consumer.transmit_approved(auth_a, recipient=PEER, body="exactly once, please",
                               number=SELF_NUMBER, poster=poster)
    assert len(poster.calls) == 1


def test_a_re_entrant_send_of_the_same_draft_is_refused(db):
    """End to end: whichever guard gets there first, only ONE send happens."""
    draft = _pending(db, body="exactly once, end to end")
    db.approve_draft(draft["id"], approval_ref="cg-reentrant")
    sends = []
    second = {}

    def transmit(auth, *, recipient, body, number):
        sends.append(body)
        if "attempted" not in second:
            second["attempted"] = True
            try:
                db.send_approved(draft["id"], transmit=transmit)
            except SendGateError as exc:
                second["refused"] = str(exc)
        return {"timestamp": str(SERVER_TS)}

    db.send_approved(draft["id"], transmit=transmit)
    assert sends == ["exactly once, end to end"]
    assert "D3 approval gate" in second["refused"]


def test_the_claim_refuses_a_draft_that_is_no_longer_approved(db):
    draft = _pending(db)
    db.approve_draft(draft["id"], approval_ref="cg-claim")
    db._claim_for_sending(draft["id"])
    with pytest.raises(SendGateError) as exc:
        db._claim_for_sending(draft["id"])
    assert "could not be claimed" in str(exc.value)


# --------------------------------------------------------------------------- #
# Reconciling a stranded send — the operator's way out of `sending`
# --------------------------------------------------------------------------- #
def _stranded(db, body="stranded draft"):
    draft = _pending(db, body=body)
    db.approve_draft(draft["id"], approval_ref="cg-strand")

    def die(auth, **kw):
        raise ConnectionResetError("pod killed mid-send")

    with pytest.raises(ConnectionResetError):
        db.send_approved(draft["id"], transmit=die)
    assert db.get_draft(draft["id"])["send_state"] == _signal_db.STATE_SENDING
    return draft


def test_reconcile_sent_records_the_server_timestamp(db):
    """It DID go out — so the row must carry the timestamp the echo will bring."""
    draft = _stranded(db)
    row = db.reconcile_send(draft["id"], outcome=_signal_db.RECONCILE_SENT,
                            server_timestamp="1723800000001")
    assert row["send_state"] == _signal_db.STATE_SENT
    assert row["message_timestamp"] == 1723800000001


def test_reconcile_sent_refuses_an_unusable_timestamp(db):
    """Guessing here would break sync-echo dedupe silently."""
    draft = _stranded(db)
    for bad in (None, "", "not-a-number", "0"):
        with pytest.raises(ValueError):
            db.reconcile_send(draft["id"], outcome=_signal_db.RECONCILE_SENT,
                              server_timestamp=bad)
    assert db.get_draft(draft["id"])["send_state"] == _signal_db.STATE_SENDING


def test_reconcile_not_sent_returns_the_draft_to_pending_for_RE_APPROVAL(db):
    """It did not go out — and a retry must not ride on the old approval."""
    draft = _stranded(db)
    row = db.reconcile_send(draft["id"], outcome=_signal_db.RECONCILE_NOT_SENT,
                            note="nothing in the thread")
    assert row["send_state"] == _signal_db.STATE_PENDING
    # It cannot be sent until a human approves again ...
    with pytest.raises(SendGateError):
        db.send_approved(draft["id"], transmit=lambda a, **kw: {"timestamp": "1"})
    # ... and once they do, it sends normally.
    db.approve_draft(draft["id"], approval_ref="cg-second-look")
    poster = Poster()
    db.send_approved(draft["id"],
                     transmit=lambda a, **kw: consumer.transmit_approved(
                         a, poster=poster, **kw))
    assert len(poster.calls) == 1


def test_reconcile_refuses_a_draft_that_is_not_sending(db):
    draft = _pending(db)
    with pytest.raises(SendGateError) as exc:
        db.reconcile_send(draft["id"], outcome=_signal_db.RECONCILE_NOT_SENT)
    assert "are reconciled" in str(exc.value)


def test_reconcile_needs_the_operator_token(db, monkeypatch):
    draft = _stranded(db)
    monkeypatch.delenv(_signal_db.APPROVAL_TOKEN_ENV, raising=False)
    with pytest.raises(SendGateError) as exc:
        db.reconcile_send(draft["id"], outcome=_signal_db.RECONCILE_NOT_SENT)
    assert _signal_db.APPROVAL_TOKEN_ENV in str(exc.value)


def test_an_in_flight_sender_cannot_stamp_over_an_OPERATORS_reconcile(db):
    """🔴 F1 — the terminal update needs the same predicate as the claim.

    The operator reconciles a draft they believe was lost; the original sender
    then completes. With an unconditional `WHERE id = %s` the sender silently
    overwrote the operator's decision with its own timestamp and nobody read the
    rowcount. Now the sender is TOLD it lost.
    """
    draft = _pending(db, body="whose timestamp wins")
    db.approve_draft(draft["id"], approval_ref="cg-inflight")
    operator_ts = 1723900000111
    api_ts = 1723900000999

    def transmit(auth, *, recipient, body, number):
        # While this send is in flight, the operator reconciles it as sent.
        db.reconcile_send(draft["id"], outcome=_signal_db.RECONCILE_SENT,
                          server_timestamp=str(operator_ts))
        return {"timestamp": str(api_ts)}

    with pytest.raises(SendGateError) as exc:
        db.send_approved(draft["id"], transmit=transmit)
    assert "complete the send" in str(exc.value)

    row = db.get_draft(draft["id"])
    assert row["send_state"] == _signal_db.STATE_SENT
    assert row["message_timestamp"] == operator_ts       # the operator's, not the API's


def test_a_not_sent_reconcile_mid_flight_cannot_become_a_SECOND_transmit(db):
    """🔴 F1 — the resend this round's own escape hatch would otherwise open.

    `_claim_for_sending` promises "never a duplicate message". This round added
    the supported path OUT of `sending`; without a predicate on the terminal
    update, a `--not-sent` reconcile landing mid-flight could be re-approved and
    the same body transmitted twice.
    """
    draft = _pending(db, body="exactly one on the wire")
    db.approve_draft(draft["id"], approval_ref="cg-midflight")
    poster = Poster()

    def transmit(auth, *, recipient, body, number):
        poster(f"http://x{consumer.SEND_PATH}", json={"message": body})
        db.reconcile_send(draft["id"], outcome=_signal_db.RECONCILE_NOT_SENT,
                          note="looked empty at the time")
        return {"timestamp": str(SERVER_TS)}

    with pytest.raises(SendGateError):
        db.send_approved(draft["id"], transmit=transmit)

    # Re-approved and sent again — the SECOND transmit is the hazard, so count it.
    db.approve_draft(draft["id"], approval_ref="cg-midflight-2")
    db.send_approved(draft["id"],
                     transmit=lambda a, **kw: consumer.transmit_approved(
                         a, poster=poster, **kw))
    assert len(poster.calls) == 2, (
        "one deliberate re-send after an explicit human decision is expected; "
        "what must never happen is the FIRST attempt landing twice")
    assert db.get_draft(draft["id"])["send_state"] == _signal_db.STATE_SENT


def test_reconcile_refuses_when_the_row_moved_under_it(db):
    """A second reconcile is refused — by the PYTHON precondition, note.

    Labelled honestly: this exercises the read-then-check, not the SQL predicate.
    A mutation sweep proved the difference — stripping `AND send_state = …` from
    both reconcile writes left this test green, because the Python check
    short-circuits first. The test below is the one that reaches the predicate.
    """
    draft = _stranded(db)
    db.reconcile_send(draft["id"], outcome=_signal_db.RECONCILE_NOT_SENT)
    with pytest.raises(SendGateError):
        db.reconcile_send(draft["id"], outcome=_signal_db.RECONCILE_NOT_SENT)


@pytest.mark.parametrize("outcome", [_signal_db.RECONCILE_SENT,
                                     _signal_db.RECONCILE_NOT_SENT])
def test_reconcile_refuses_when_the_row_MOVES_between_the_read_and_the_write(
        db, monkeypatch, outcome):
    """🔴 The TOCTOU window the SQL predicate exists for, made reachable.

    `reconcile_send` reads the row, checks it in Python, then writes. Everything
    interesting happens in the gap: another actor moving the row there is exactly
    what the predicate catches and what the Python check cannot. Simulated by
    making the READ report `sending` while the real row has already moved on —
    with the predicate the write matches nothing and the caller is told; without
    it the write lands and the loser silently overwrites the winner.
    """
    draft = _pending(db)                       # the REAL row is `pending`
    stale = dict(draft, send_state=_signal_db.STATE_SENDING)
    monkeypatch.setattr(type(db), "_draft_or_raise", lambda self, i: stale)

    with pytest.raises(SendGateError) as exc:
        db.reconcile_send(draft["id"], outcome=outcome,
                          server_timestamp="1723900000333")
    assert "no longer 'sending'" in str(exc.value)
    # The row is untouched: the loser wrote nothing at all.
    assert db.get_draft(draft["id"])["send_state"] == _signal_db.STATE_PENDING


def test_reconcile_without_a_note_PRESERVES_the_approval_record(db):
    """🔴 F2 — the audit record D3 stakes its claim on.

    `--not-sent` used a bare `approval_ref = %s` while `--sent` nine lines up
    used `COALESCE`, so reconciling without `--note` NULLed the reference to the
    approval the attempt actually rode on — and the test only ever exercised the
    with-`--note` case. `approve_draft`'s docstring stakes D3 on "a recorded
    approval decision that a human can audit after the fact".
    """
    draft = _pending(db)
    db.approve_draft(draft["id"], approval_ref="clawgate-task-4242")

    def die(auth, **kw):
        raise ConnectionResetError("dropped")

    with pytest.raises(ConnectionResetError):
        db.send_approved(draft["id"], transmit=die)

    row = db.reconcile_send(draft["id"], outcome=_signal_db.RECONCILE_NOT_SENT)
    assert row["approval_ref"] == "clawgate-task-4242"


def test_reconcile_sent_without_a_note_also_preserves_it(db):
    """Both branches, so the two cannot drift apart again."""
    draft = _pending(db)
    db.approve_draft(draft["id"], approval_ref="clawgate-task-5150")

    def die(auth, **kw):
        raise ConnectionResetError("dropped")

    with pytest.raises(ConnectionResetError):
        db.send_approved(draft["id"], transmit=die)

    row = db.reconcile_send(draft["id"], outcome=_signal_db.RECONCILE_SENT,
                            server_timestamp="1723900000222")
    assert row["approval_ref"] == "clawgate-task-5150"


def test_a_note_ADDS_to_the_record_rather_than_replacing_it(db):
    """POSITIVE CONTROL: the preservation above is not "the note is ignored"."""
    draft = _stranded(db)
    row = db.reconcile_send(draft["id"], outcome=_signal_db.RECONCILE_NOT_SENT,
                            note="checked the thread, nothing there")
    assert row["approval_ref"] == "checked the thread, nothing there"


def test_reconcile_rejects_an_unknown_outcome(db):
    draft = _stranded(db)
    with pytest.raises(ValueError):
        db.reconcile_send(draft["id"], outcome="probably-sent")


def test_a_stranded_draft_is_reachable_from_the_drafts_listing(db):
    """The operator has to be able to FIND it before reconciling it."""
    draft = _stranded(db)
    listed = db.list_drafts(state=_signal_db.STATE_SENDING)
    assert [d["id"] for d in listed] == [draft["id"]]


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


# --------------------------------------------------------------------------- #
# 🔴 THE TOKEN NOW HAS TWO SOURCES (clawgate task #307), so every test below
# must pin BOTH — and must pin $HOME. `emit_draft_task` resolves through
# `scripts/lib/clawgate_env`, whose file tier is `~/.claude/clawgate.env`. A test
# that only unsets the environment variable would read the OPERATOR'S REAL TOKEN
# on a dev host and pass or fail on an ambient fact about the machine (green in
# the nix sandbox, which has no such file, and posting a live card off the dev
# host). `claude/RULES.md`: a suite whose config pins a dimension is blind on it —
# here the dimension is the whole defect.
# --------------------------------------------------------------------------- #
def _isolate_clawgate_env(monkeypatch, tmp_path, *, file_token=None):
    """Point the resolver's FILE tier at a tmp home and clear its ENV tier.

    Returns the env-file path (which may not exist — that is the "no token
    anywhere" case). `$HOME` is moved rather than a constant patched, so the real
    `~/.claude/clawgate.env` construction is what gets exercised.
    """
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True, exist_ok=True)
    env_file = home / ".claude" / "clawgate.env"
    if file_token is not None:
        env_file.write_text("CLAWGATE_HOOK_TOKEN=%s\n" % file_token,
                            encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("CLAWGATE_HOOK_TOKEN", raising=False)
    return env_file


def test_emit_draft_task_is_a_graceful_noop_without_a_token(monkeypatch, tmp_path,
                                                            capsys):
    _isolate_clawgate_env(monkeypatch, tmp_path)     # no file, no env var
    posted = []
    module = types.ModuleType("requests")
    module.post = lambda *a, **k: posted.append(a)
    monkeypatch.setitem(sys.modules, "requests", module)
    assert clawgate.emit_draft_task(draft_id=1, recipient=PEER, body="x") is False
    assert posted == []
    # 🔴 D3 UNREGRESSED, AND NO LONGER SILENT: it still returns rather than
    # raising (the draft row is already stored by the time this runs), but it
    # says what it skipped.
    err = capsys.readouterr().err
    assert err.count("\n") == 1, "expected exactly ONE stderr line, got %r" % err
    assert "#1" in err and "CLAWGATE_HOOK_TOKEN" in err


def test_emit_draft_task_posts_when_the_token_is_ONLY_in_the_env_FILE(
        monkeypatch, tmp_path, capsys):
    """🔴 THE DEFECT. This is the exact configuration of the host: the token sits
    in `~/.claude/clawgate.env` and is absent from the process environment. The
    old `os.environ.get` produced False and posted nothing, in silence — a real
    draft was created with no card."""
    _isolate_clawgate_env(monkeypatch, tmp_path, file_token="tok-from-file")
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

    assert clawgate.emit_draft_task(draft_id=41, recipient=PEER,
                                    body="please approve") is True
    assert calls[0]["headers"]["Authorization"] == "Bearer tok-from-file"
    assert "41" in calls[0]["json"]["directory"]
    assert capsys.readouterr().err == "", "a successful post must be silent"


def test_emit_draft_task_prefers_the_ENVIRONMENT_over_the_file(monkeypatch,
                                                               tmp_path):
    """The precedence, asserted at the PRODUCER and not only in the resolver's own
    suite: `clawgatectl`'s chain is file -> environment, later overriding
    earlier, so an exported token wins."""
    _isolate_clawgate_env(monkeypatch, tmp_path, file_token="tok-from-file")
    monkeypatch.setenv("CLAWGATE_HOOK_TOKEN", "tok-from-environ")
    seen = []

    class Resp:
        def raise_for_status(self):
            pass

    module = types.ModuleType("requests")
    module.post = lambda url, headers=None, json=None, timeout=None: (
        seen.append(headers["Authorization"]) or Resp())
    monkeypatch.setitem(sys.modules, "requests", module)

    assert clawgate.emit_draft_task(draft_id=42, recipient=PEER, body="x") is True
    assert seen == ["Bearer tok-from-environ"], (
        "precedence is inverted: the exported token must override the file's")


def test_emit_draft_task_reads_the_token_ONLY_through_the_shared_resolver():
    """🔴 THE SEAM, structurally. The fix is worthless if a later edit reaches for
    `os.environ` again — that is how the two producers came to be wrong in the
    same direction in the first place. `CLAWGATE_HOOK_TOKEN` must appear in this
    module NOWHERE outside a comment/docstring, and the resolver call must be
    present."""
    src = Path(clawgate.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    # Docstrings legitimately NAME the variable (they explain the precedence);
    # only a string constant in CODE would be a second read of it.
    prose = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                 ast.AsyncFunctionDef)):
            continue
        body = getattr(node, "body", None) or []
        if (body and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)):
            prose.add(id(body[0].value))
    for node in ast.walk(tree):
        if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                and id(node) not in prose):
            assert "CLAWGATE_HOOK_TOKEN" not in node.value, (
                "scripts/signal/clawgate.py names the token variable in code at "
                "line %s — it must resolve through "
                "scripts/lib/clawgate_env.resolve_hook_token, which is the ONE "
                "place either producer reads it" % getattr(node, "lineno", "?"))
    assert "resolve_hook_token(" in src, (
        "the producer no longer calls the shared resolver")


def test_the_shared_resolver_the_producer_loads_is_the_repo_one():
    """POSITIVE CONTROL for the loader. The assertions above would all pass with
    a resolver that was never found — the ImportError branch also returns False —
    so prove the explicit-path load actually resolves, and to the file in this
    repo."""
    mod = clawgate._clawgate_env()
    assert mod.TOKEN_VAR == "CLAWGATE_HOOK_TOKEN"
    assert Path(mod.__file__).resolve() == \
        (SIGNAL_DIR.parent / "lib" / "clawgate_env.py").resolve()


def test_emit_draft_task_posts_the_card_when_a_token_is_set(monkeypatch, tmp_path):
    _isolate_clawgate_env(monkeypatch, tmp_path)
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


# --------------------------------------------------------------------------- #
# Route 12 — the LIVE wire shape, driven through send_approved()
#
# 🔴 Every other `transmit=` fixture in this file returns a bare DICT, because
# the fixture authors read the upstream TYPE (`ds.SendMessageResponse`, an
# object) rather than the wire. The live server in json-rpc mode returns a
# LIST — measured 2026-08-21:
#
#     POST /v2/send  ->  201  [{"timestamp":"1787331796630"}]
#
# That mismatch shipped a defect where a SUCCESSFUL send raised AttributeError
# and stranded the draft in `sending`, inviting a duplicate resend. Verifying
# the normaliser in isolation was NOT enough: an adversarial audit ran two
# mutants that survived the whole suite because nothing drove `send_approved`
# with a list. These tests close that seam.
# --------------------------------------------------------------------------- #
def test_send_approved_accepts_the_LIVE_list_shape(db):
    """The end-to-end happy path with the shape the real server sends."""
    draft = _pending(db)
    db.approve_draft(draft["id"], approval_ref="cg-live-list")
    sent = db.send_approved(
        draft["id"], transmit=lambda a, **kw: [{"timestamp": str(SERVER_TS)}])
    assert sent["send_state"] == "sent"
    assert sent["message_timestamp"] == SERVER_TS, (
        "the server timestamp must be stored from the list entry — a locally "
        "generated one would not dedupe the sync echo (🔧 #4)")


def test_a_LIST_response_carrying_errors_is_NOT_recorded_as_sent(db):
    """🔴 The inverse of the bug this seam fixed, and the worse direction.

    Upstream sets `Errors` for any non-SUCCESS recipient while still returning
    201 (`client/client.go` -> `api/api.go`). If the errors check were skipped
    on the list path, a FAILED send would be recorded as `sent` — silently, and
    unrecoverably, because nothing would remain to reconcile.

    A mutant that checked errors only on the dict path SURVIVED the entire
    suite before this test existed.
    """
    draft = _pending(db)
    db.approve_draft(draft["id"], approval_ref="cg-live-list-errors")
    with pytest.raises(RuntimeError) as exc:
        db.send_approved(draft["id"], transmit=lambda a, **kw: [
            {"timestamp": str(SERVER_TS),
             "errors": {"recipients": [{"message": "rate limited"}]}}])
    assert "rate limited" in str(exc.value), (
        "the per-recipient reason must survive — it is what an operator needs "
        "in order to reconcile")
    assert db.get_draft(draft["id"])["send_state"] == "sending", (
        "a failed send must stay in `sending` for manual reconciliation, never "
        "be recorded as sent")


def test_a_singular_error_key_in_the_list_shape_also_blocks_the_send(db):
    """The `error` (singular) branch had ZERO coverage — deleting it survived."""
    draft = _pending(db)
    db.approve_draft(draft["id"], approval_ref="cg-live-list-singular")
    with pytest.raises(RuntimeError) as exc:
        db.send_approved(draft["id"], transmit=lambda a, **kw: [
            {"error": "Invalid identifier", "timestamp": str(SERVER_TS)}])
    assert "Invalid identifier" in str(exc.value)
    assert db.get_draft(draft["id"])["send_state"] == "sending"


def test_the_error_message_carries_the_timestamp_the_response_returned(db):
    """A partly-failed GROUP send DID go out, and the reply carried its ts.

    Without it in the error, the operator has to hunt the timestamp in the
    Signal thread before they can `reconcile --sent`. Draft 51 was exactly that
    situation.
    """
    draft = _pending(db)
    db.approve_draft(draft["id"], approval_ref="cg-live-list-ts-in-error")
    with pytest.raises(RuntimeError) as exc:
        db.send_approved(draft["id"], transmit=lambda a, **kw: [
            {"timestamp": "1787331796630",
             "errors": {"recipients": [{"message": "unregistered"}]}}])
    assert "1787331796630" in str(exc.value), (
        "the server timestamp must appear in the error — it is what "
        "`reconcile --sent --timestamp` needs and it is otherwise lost")


def test_send_approved_refuses_an_EMPTY_response_rather_than_indexing_it(db):
    """A malformed reply must raise the NORMALISER's error, not an IndexError.

    Kills the `bypass-normaliser` mutant: replacing the call with
    `result if isinstance(result, list) else [result]` produces identical
    entries for well-formed input, so it survives every happy-path test. It
    diverges only here — and it diverges into `entries[0]` on an empty list.
    """
    draft = _pending(db)
    db.approve_draft(draft["id"], approval_ref="cg-empty-response")
    with pytest.raises(ValueError, match="EMPTY"):
        db.send_approved(draft["id"], transmit=lambda a, **kw: [])
    assert db.get_draft(draft["id"])["send_state"] == "sending"


def test_send_approved_refuses_a_MULTI_ENTRY_response_instead_of_guessing(db):
    """🔴 The dangerous half of the same mutant.

    Bypassing the normaliser on a two-entry reply does NOT raise — it silently
    takes `entries[0]`, stores that timestamp and marks the draft `sent`. The
    stored timestamp may belong to the OTHER message, which breaks sync-echo
    dedupe (🔧 #4) exactly the way a locally generated one would.
    """
    draft = _pending(db)
    db.approve_draft(draft["id"], approval_ref="cg-multi-response")
    with pytest.raises(ValueError, match="refusing to guess"):
        db.send_approved(draft["id"], transmit=lambda a, **kw: [
            {"timestamp": "1787331796630"}, {"timestamp": "1787331796999"}])
    assert db.get_draft(draft["id"])["send_state"] == "sending", (
        "a response we cannot interpret must leave the draft for manual "
        "reconciliation, never be recorded as sent")
