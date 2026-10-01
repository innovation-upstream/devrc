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
import json
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

#: An env-file path that CANNOT exist, for the assertions that must be decided
#: by their `env` mapping alone. `task_endpoint()` reads the real
#: ~/.claude/clawgate.env by default, and this host has one — a test that does
#: not redirect it asserts against whatever the developer happens to carry.
_ABSENT_ENV_FILE = "/nonexistent/devrc-signal-tests/clawgate.env"
SELF_NUMBER = "+15559090"
SERVER_TS = 1723500000001


def _approved_row(draft_id, recipient, body, mentions=None):
    """A hand-built APPROVED draft row, digest included.

    🔴 The digest is COMPUTED, never a literal: `_mint_send_authorization()`
    fails closed on a row whose payload does not hash to what approval recorded,
    so a fixture that hard-coded one would go stale silently the first time the
    canonical form changed and every test here would refuse for the wrong reason.
    """
    row = {"id": draft_id, "send_state": _signal_db.STATE_APPROVED,
           "recipient": recipient, "body": body, "mentions": mentions or []}
    # 🔴 DERIVED FROM THE MODULE, over the WHOLE ROW. The digest now covers a
    # CANONICAL recipient identity (`recipient_identity()`), not the rendered
    # `recipient` string — a fixture that recomputed it from `recipient` alone
    # would encode this test's own idea of the canonical form and stay green
    # while the two sides disagreed.
    row["approved_digest"] = _signal_db.draft_payload_digest(row)
    return row


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
        _approved_row(8, PEER, "ok"))
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
        _approved_row(9, PEER, "replay me"))
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
    """🔴 The gate call, PINNED WHOLE — arguments included, not just the name.

    It used to assert the literal `spend_authorization(auth)`. That was exactly
    as wide as the capability was: draft identity and nothing about the message.
    Now that the capability BINDS the payload, a call that passed only `auth`
    would not compile — but a call that passed `recipient` and `body` and
    quietly dropped `mentions` WOULD, and it would send an unbound mention array
    while every other test in this file stayed green. So the whole call is
    pinned, normalised for line wrapping, rather than its name.
    """
    src = Path(consumer.__file__).read_text(encoding="utf-8")
    body = src.split("def transmit_approved(")[1].split("\ndef ")[0]
    assert "SEND_PATH" in body
    flat = " ".join(body.split())
    call = ("spend_authorization(auth, recipient=recipient, body=body, "
            "mentions=mentions)")
    assert call in flat, (
        "the gate call is not the pinned one — every component of the payload "
        "must be passed, or that component is transmitted UNBOUND")
    # The gate runs BEFORE the URL is even built.
    assert flat.index(call) < flat.index("SEND_PATH")


def test_clawgate_module_cannot_transmit():
    """The notifier must be exactly that — it must not become a second door."""
    src = Path(clawgate.__file__).read_text(encoding="utf-8")
    assert "SEND_PATH" not in src
    assert "/v2/send" not in src
    # Resolved, not spelled: the base comes from configuration, the PATH is the
    # notifier's own and must stay the task-create route.
    assert clawgate.TASKS_PATH == "/api/tasks"
    assert clawgate.task_endpoint({}, _ABSENT_ENV_FILE).endswith("/api/tasks")


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

    def transmit_then_die(auth, *, recipient, body, number, mentions=None):
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

    def transmit(auth, *, recipient, body, number, mentions=None):
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

    def transmit(auth, *, recipient, body, number, mentions=None):
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

    def transmit(auth, *, recipient, body, number, mentions=None):
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

    def transmit(auth, *, recipient, body, number, mentions=None):
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
    """🔴 RED at 9fb6de75 — it asserted the REPLACEMENT its own name denied.

    Round-2 audit F3, the `reconcile_send` half. `_stranded()` approves with
    `approval_ref="cg-strand"`, so a passing assertion of
    `== "checked the thread, nothing there"` was a measurement that the approval
    reference had been ERASED — `COALESCE(note, approval_ref)` returns the note.
    The test name claimed the property; the assertion pinned its opposite, and
    read as coverage while providing none.

    Doubles as the POSITIVE CONTROL for the two preservation tests above: the
    note is not merely ignored, and both halves of the trail are named here.
    """
    draft = _stranded(db)
    row = db.reconcile_send(draft["id"], outcome=_signal_db.RECONCILE_NOT_SENT,
                            note="checked the thread, nothing there")
    assert row["approval_ref"] == (
        "cg-strand" + _signal_db.APPROVAL_REF_SEPARATOR
        + "checked the thread, nothing there")


def test_reconcile_SENT_with_a_note_also_appends_rather_than_replacing(db):
    """🔴 RED at 9fb6de75 (it returned the bare note). Both branches, F3.

    The `--sent` branch carried the identical `COALESCE(%s, approval_ref)`, so
    recording "it did go out" WITH a note destroyed the approval reference the
    transmitted message went out under — the worst of the three, because that
    row is the audit record of a message a third party actually received.
    """
    draft = _stranded(db)
    row = db.reconcile_send(draft["id"], outcome=_signal_db.RECONCILE_SENT,
                            server_timestamp="1723900000333",
                            note="saw it in the thread")
    assert row["approval_ref"] == (
        "cg-strand" + _signal_db.APPROVAL_REF_SEPARATOR + "saw it in the thread")


def test_an_EMPTY_note_leaves_the_trail_untouched_rather_than_appending_a_separator(db):
    """A blank `--note` is no note — not a bare separator, and not an erasure.

    `_appended_note()` normalises it to NULL so the `CASE` leaves the column
    alone. At 9fb6de75 `COALESCE('', approval_ref)` returned `''`, wiping the
    trail entirely — so this is RED there too, on a different symptom.
    """
    draft = _stranded(db)
    row = db.reconcile_send(draft["id"], outcome=_signal_db.RECONCILE_NOT_SENT,
                            note="   ")
    assert row["approval_ref"] == "cg-strand"


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
        _approved_row(11, PEER, "x"))
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
# 🔴 THE TASK BASE URL IS CONFIGURATION, NOT A LITERAL
#
# RED at 79a9b22a (origin/main): this module held
# `ENDPOINT = "http://192.168.50.250:30302/api/tasks"` — the PERMISSION ROUTER —
# and read nothing from the environment but the token. The task/board service
# was extracted out of the router into a separate process on a separate base
# URL, and measured live 2026-09-29 the router answers `POST /api/tasks` with
# **404**: every card this producer emitted was silently dropped.
#
# Its mail-actions twin was fixed in #1878; this half was deferred there with
# the note "the deployed pod never runs `draft`". True of the POD, false of the
# DEFECT — `consumer.py draft` is an operator command run from the workbench
# CLI, where ~/.claude/clawgate.env exists and names the task service.
#
# 🔴 EVERY TEST BELOW DRIVES BOTH LAYERS EXPLICITLY. `task_endpoint()` reads the
# real ~/.claude/clawgate.env by default, and the host that runs this gate HAS
# one — a test that does not redirect it is green or red for reasons that have
# nothing to do with this code.
#
# Fixture hosts are PAIRWISE DISTINCT and distinct from `DEFAULT_BASE`, so a
# mutant that collapses the precedence onto any other key, onto the other layer,
# or onto the default cannot pass by returning a coincidentally equal value.
# --------------------------------------------------------------------------- #
FILE_TASK_BASE = "http://file-task.example:18081"
FILE_ROUTER_BASE = "http://file-router.example:27443"
ENV_TASK_BASE = "http://env-task.example:34567"
ENV_ROUTER_BASE = "http://env-router.example:41213"
#: The shared module's fallback, spelled as a LITERAL. Deriving it from the
#: module under test would assert nothing about it.
DEFAULT_BASE = "http://192.168.50.250:30302"
TASKS_PATH = "/api/tasks"


def _env_file(monkeypatch, tmp_path, contents=None):
    """Redirect ~/.claude/clawgate.env into `tmp_path`; return its path."""
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    path = home / ".claude" / "clawgate.env"
    if contents is not None:
        path.write_text(contents, encoding="utf-8")
    return path


def _clear_env(monkeypatch):
    monkeypatch.delenv("CLAWGATE_TASK_API_URL", raising=False)
    monkeypatch.delenv("CLAWGATE_API_URL", raising=False)


def test_the_env_file_redirect_actually_takes_effect(monkeypatch, tmp_path):
    """🔴 HARNESS CONTROL for the file-layer assertions below. If `HOME` were not
    what `~/.claude/clawgate.env` expands to, the redirect would be inert: every
    "the file wins" assertion would be measuring the absence of a file, and every
    "no file" assertion would be reading the developer's real one."""
    _clear_env(monkeypatch)
    path = _env_file(monkeypatch, tmp_path,
                     "CLAWGATE_TASK_API_URL=%s\n" % FILE_TASK_BASE)
    assert clawgate.task_endpoint() == FILE_TASK_BASE + TASKS_PATH, (
        "the env-file layer was not read — HOME redirect inert, so every "
        "file-layer assertion here is vacuous")
    path.unlink()
    assert clawgate.task_endpoint() == DEFAULT_BASE + TASKS_PATH, (
        "deleting the redirected file changed nothing — the resolver is not "
        "reading the path this harness controls")


def test_the_env_file_alone_decides_when_the_process_has_nothing(monkeypatch,
                                                                 tmp_path):
    """🔴 THE DEFECT THIS FIX CLOSES, in the configuration of the host that
    actually runs `consumer.py draft`: the file names the task service, the
    process environment names nothing. The old constant answered `DEFAULT_BASE`
    here — the router — and the card went nowhere."""
    _clear_env(monkeypatch)
    _env_file(monkeypatch, tmp_path,
              "CLAWGATE_HOOK_TOKEN=unused\n"
              "CLAWGATE_TASK_API_URL=%s\nCLAWGATE_API_URL=%s\n"
              % (FILE_TASK_BASE, FILE_ROUTER_BASE))
    assert clawgate.task_endpoint() == FILE_TASK_BASE + TASKS_PATH, (
        "with nothing in the process environment the env file's "
        "CLAWGATE_TASK_API_URL (%s) must decide; %s means the file was not read "
        "at all and %s means the router key won"
        % (FILE_TASK_BASE, DEFAULT_BASE, FILE_ROUTER_BASE))


def test_the_process_environment_OVERRIDES_the_env_file(monkeypatch, tmp_path):
    """A one-off `CLAWGATE_TASK_API_URL=… consumer.py draft …` must still win —
    that is the whole point of keeping a process layer at all."""
    _env_file(monkeypatch, tmp_path,
              "CLAWGATE_TASK_API_URL=%s\nCLAWGATE_API_URL=%s\n"
              % (FILE_TASK_BASE, FILE_ROUTER_BASE))
    monkeypatch.setenv("CLAWGATE_TASK_API_URL", ENV_TASK_BASE)
    monkeypatch.delenv("CLAWGATE_API_URL", raising=False)
    assert clawgate.task_endpoint() == ENV_TASK_BASE + TASKS_PATH


def test_the_file_TASK_key_outranks_a_process_ROUTER_key(monkeypatch, tmp_path):
    """🔴 THE LAYERS MERGE BEFORE THE LEDGER IS APPLIED. A process layer winning
    WHOLESALE would let a bare `CLAWGATE_API_URL` exported in a shell beat the
    file's `CLAWGATE_TASK_API_URL` and silently un-split the two services."""
    _env_file(monkeypatch, tmp_path,
              "CLAWGATE_TASK_API_URL=%s\n" % FILE_TASK_BASE)
    monkeypatch.delenv("CLAWGATE_TASK_API_URL", raising=False)
    monkeypatch.setenv("CLAWGATE_API_URL", ENV_ROUTER_BASE)
    assert clawgate.task_endpoint() == FILE_TASK_BASE + TASKS_PATH, (
        "a process-environment CLAWGATE_API_URL (%s) must NOT beat the file's "
        "CLAWGATE_TASK_API_URL (%s) — that un-splits the services"
        % (ENV_ROUTER_BASE, FILE_TASK_BASE))


def test_no_task_key_anywhere_falls_back_to_the_router_key(monkeypatch,
                                                           tmp_path):
    """A host that never heard of the split must behave exactly as before."""
    _clear_env(monkeypatch)
    _env_file(monkeypatch, tmp_path,
              "CLAWGATE_API_URL=%s\n" % FILE_ROUTER_BASE)
    assert clawgate.task_endpoint() == FILE_ROUTER_BASE + TASKS_PATH


def test_with_no_configuration_at_all_the_endpoint_is_unchanged(monkeypatch,
                                                                tmp_path):
    """The fallback is the shared default — byte-identical to the URL the old
    literal named. A host told nothing about the split resolves to exactly what
    it resolved to before, so this change cannot break one."""
    _clear_env(monkeypatch)
    _env_file(monkeypatch, tmp_path)              # no file at all
    assert clawgate.task_endpoint() == DEFAULT_BASE + TASKS_PATH


def test_an_empty_process_value_does_not_MASK_the_file(monkeypatch, tmp_path):
    _env_file(monkeypatch, tmp_path,
              "CLAWGATE_TASK_API_URL=%s\n" % FILE_TASK_BASE)
    monkeypatch.setenv("CLAWGATE_TASK_API_URL", "")
    monkeypatch.delenv("CLAWGATE_API_URL", raising=False)
    assert clawgate.task_endpoint() == FILE_TASK_BASE + TASKS_PATH


def test_a_trailing_slash_does_not_double_the_separator(monkeypatch, tmp_path):
    _env_file(monkeypatch, tmp_path)
    monkeypatch.setenv("CLAWGATE_TASK_API_URL", ENV_TASK_BASE + "/")
    assert clawgate.task_endpoint() == ENV_TASK_BASE + TASKS_PATH


def test_the_precedence_is_the_SHARED_one_not_a_local_respelling():
    """🔴 THE SEAM. Everything above would also pass over a private copy of the
    rule living in this module — the exact regrowth the shared definition exists
    to prevent. This pins that the module RESOLVES THROUGH the shared ledger:
    feed the shared module's OWN ledger names and watch this module's resolver
    follow them, and confirm it is the shared default it falls back to."""
    cg = clawgate._load_clawgate_tasks()
    assert cg.TASK_API_URL_VARS == ("CLAWGATE_TASK_API_URL", "CLAWGATE_API_URL")
    specific, general = cg.TASK_API_URL_VARS
    assert clawgate.task_endpoint({specific: ENV_TASK_BASE,
                                   general: ENV_ROUTER_BASE},
                                  _ABSENT_ENV_FILE) == ENV_TASK_BASE + TASKS_PATH
    assert clawgate.task_endpoint({general: ENV_ROUTER_BASE},
                                  _ABSENT_ENV_FILE) == ENV_ROUTER_BASE + TASKS_PATH
    assert clawgate.task_endpoint({}, _ABSENT_ENV_FILE) == \
        cg.DEFAULT_API_URL + TASKS_PATH


def test_the_shared_module_is_loaded_by_EXPLICIT_PATH_not_sys_path():
    """`scripts/signal/` is on `sys.path` (conftest puts it there, and the image
    runs with WORKDIR there), so a plain `import clawgate_tasks` would be
    shadowed by any same-named file that lands beside these modules. The loader
    must name the file."""
    src = Path(clawgate.__file__).read_text(encoding="utf-8")
    assert "SourceFileLoader" in src
    assert "DEVRC_DIR" in src
    assert "raise ImportError" in src, (
        "an unloadable shared module must RAISE, never degrade to a local copy "
        "of the precedence — two copies, and the silent one goes stale")


def test_an_UNLOADABLE_shared_module_raises_rather_than_guessing(monkeypatch):
    """The NO-FALLBACK-COPY policy, driven rather than asserted about. With the
    memo cleared and both candidate paths pointed at nothing, resolving must
    raise — an honest miss, not a card posted at a guessed host."""
    monkeypatch.setattr(clawgate, "_CG", None)
    monkeypatch.setattr(clawgate, "SHARED_TASKS_MODULE",
                        "scripts/lib/no_such_module_xyzzy.py")
    monkeypatch.setattr(clawgate, "DEVRC_DIR", "/nonexistent/devrc")
    with pytest.raises(ImportError) as exc:
        clawgate.task_endpoint({}, _ABSENT_ENV_FILE)
    assert "no_such_module_xyzzy.py" in str(exc.value)


def test_emit_draft_task_is_a_graceful_noop_without_a_token(monkeypatch):
    """🔴 `path=` IS NOT DECORATION HERE. Deleting the variable from the process
    environment stopped being enough the moment the token started resolving
    through ~/.claude/clawgate.env as well: without an absent env-file path this
    reads the DEVELOPER'S real file, finds a real token and posts a card."""
    monkeypatch.delenv("CLAWGATE_HOOK_TOKEN", raising=False)
    posted = []
    module = types.ModuleType("requests")
    module.post = lambda *a, **k: posted.append(a)
    monkeypatch.setitem(sys.modules, "requests", module)
    assert clawgate.emit_draft_task(draft_id=1, recipient=PEER, body="x",
                                    env={}, path=_ABSENT_ENV_FILE) is False
    assert posted == []


def test_emit_draft_task_without_a_token_resolves_NO_ENDPOINT(monkeypatch):
    """The no-op must not resolve a base URL it has no credential to use.

    🔴 THIS DOCSTRING USED TO CLAIM MORE THAN THE CODE NOW DOES, and the weaker
    claim is the honest one. It read "no shared module is loaded and
    ~/.claude/clawgate.env is never opened", which held only while the token
    came from `os.environ`. The token lives in that file, so "is there a token"
    cannot be answered without reading it through that module — the load and the
    read moved AHEAD of the token check deliberately (see `emit_draft_task`).
    What survives, and is what this asserts, is that the ENDPOINT is not
    resolved on a path that posts nothing."""
    monkeypatch.delenv("CLAWGATE_HOOK_TOKEN", raising=False)
    posted = []
    module = types.ModuleType("requests")
    module.post = lambda *a, **k: posted.append(a)
    monkeypatch.setitem(sys.modules, "requests", module)

    def explode(*a, **k):
        raise AssertionError("task_endpoint() was called on the no-token path")

    monkeypatch.setattr(clawgate, "task_endpoint", explode)
    assert clawgate.emit_draft_task(draft_id=1, recipient=PEER, body="x",
                                    env={}, path=_ABSENT_ENV_FILE) is False
    assert posted == []


# --------------------------------------------------------------------------- #
# 🔴 THE ORIGINAL DEFECT, pinned so it cannot come back in this module.
# --------------------------------------------------------------------------- #
def _reintroduced_endpoint_constant(line: str) -> bool:
    """A base URL and the tasks path welded together in one module constant.
    The bare default constant in the shared module is fine and is not this."""
    stripped = line.strip()
    if stripped.startswith("#") or stripped.startswith("*"):
        return False              # a comment ABOUT the old literal is fine
    return stripped.startswith("ENDPOINT") and "=" in stripped


def test_the_producer_does_not_hardcode_a_task_endpoint_again():
    text = Path(clawgate.__file__).read_text(encoding="utf-8")
    for i, line in enumerate(text.splitlines(), 1):
        assert not _reintroduced_endpoint_constant(line), (
            "%s:%d re-introduced a hardcoded endpoint constant: %s\n"
            "Resolve the base from configuration instead (task_endpoint)."
            % (clawgate.__file__, i, line.strip()))


def test_the_hardcoded_endpoint_needle_can_actually_fire():
    """POSITIVE CONTROL for the scan above: a guard reporting zero on a clean
    tree is indistinguishable from one wired to nothing."""
    assert _reintroduced_endpoint_constant('ENDPOINT = "http://host:1/api/tasks"')
    assert _reintroduced_endpoint_constant("    ENDPOINT='http://host:1/api/tasks'")
    assert not _reintroduced_endpoint_constant(
        '# ENDPOINT = "http://host:1/api/tasks" (removed)')
    assert not _reintroduced_endpoint_constant('TASKS_PATH = "/api/tasks"')


def test_emit_draft_task_posts_the_card_when_a_token_is_set(monkeypatch):
    """🔴 `path=_ABSENT_ENV_FILE` ADDED, and it was not cosmetic. Without it this
    test set both values in the PROCESS environment and left the env-file layer
    pointed at the developer's real ~/.claude/clawgate.env — so it was green only
    because the process layer OUTRANKS the file. A precedence-inversion mutant
    made it fail against the developer's own task host (`…:30306`) rather than
    against anything this test meant to assert. Pinning the path makes the
    assertion decided by what the test supplies."""
    monkeypatch.setenv("CLAWGATE_HOOK_TOKEN", "tok-signal-1")
    monkeypatch.setenv("CLAWGATE_TASK_API_URL", ENV_TASK_BASE)
    monkeypatch.delenv("CLAWGATE_API_URL", raising=False)
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
                                    body="please approve",
                                    path=_ABSENT_ENV_FILE) is True
    call = calls[0]
    # 🔴 THE POST GOES WHERE CONFIGURATION SAYS. Asserting `== clawgate.ENDPOINT`
    # (what this line used to do) could not fail: it compared the module's own
    # constant against the URL built from that same constant, so it stayed green
    # across the entire lifetime of the hardcoded router address.
    assert call["url"] == ENV_TASK_BASE + TASKS_PATH, (
        "the card was posted at %s; configuration named %s. A card posted at "
        "the permission router is silently dropped — that base answers "
        "POST /api/tasks with 404." % (call["url"], ENV_TASK_BASE + TASKS_PATH))
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


# --------------------------------------------------------------------------- #
# 🔴 THE HOOK TOKEN IS CONFIGURATION TOO — task #307
#
# The base URL was fixed on 2026-09-29 and the token was left behind. This
# producer read it with a bare `os.environ.get("CLAWGATE_HOOK_TOKEN")`, so on
# the one host that runs `consumer.py draft` — an OPERATOR command, where
# ~/.claude/clawgate.env carries the token and no unit exports any `CLAWGATE_*`
# — it resolved `None`, skipped the card, and printed NOTHING. The operator saw
# a draft stored, no card, and no reason: indistinguishable from a card that
# posted and a board that lost it.
#
# The precedence is `clawgatectl`'s, LOWEST to HIGHEST
# (`containers/clawgate/cmd/clawgatectl/config.go` `resolveConfig`):
#
#     ~/.claude/clawgate.env  ->  process environment
#
# ...with a later source overriding an earlier one ONLY when it supplies a
# value. Both directions are pinned below: the file alone decides (the defect),
# and the process environment wins when it has something (the precedence).
#
# 🔴 EVERY TEST BELOW PINS THE ENV-FILE PATH, by `path=` or by the HOME
# redirect. `hook_token()` reads the real ~/.claude/clawgate.env by default and
# this host HAS one, carrying a real token — a test that does not pin the path
# is green or red for reasons that have nothing to do with this code. That is
# not hypothetical: `test_emit_draft_task_is_a_graceful_noop_without_a_token`
# above had to be given an absent `path=` for exactly that reason.
#
# Fixture tokens are PAIRWISE DISTINCT and distinct from every other literal in
# this module, so a mutant that collapses the two layers onto each other, or
# onto a URL key, cannot pass by returning a coincidentally equal value.
# --------------------------------------------------------------------------- #
FILE_TOKEN = "tok-env-file-9fd1"
ENV_TOKEN = "tok-process-env-4c7e"


def _token_file(tmp_path, token=FILE_TOKEN, extra=""):
    """Write an env file carrying `token`; return its path. No HOME involved."""
    path = tmp_path / "clawgate.env"
    body = "" if token is None else "CLAWGATE_HOOK_TOKEN=%s\n" % token
    path.write_text(body + extra, encoding="utf-8")
    return str(path)


# -- the resolver, both layers ------------------------------------------------ #

def test_the_env_file_ALONE_supplies_the_token(tmp_path):
    """🔴 THE DEFECT THIS FIX CLOSES, in the live configuration of the host that
    runs `consumer.py draft`: the token is in the file and the process
    environment has none. An `os.environ`-only read answers `None` here and the
    card is skipped in silence."""
    assert clawgate.hook_token({}, _token_file(tmp_path)) == FILE_TOKEN, (
        "the env-file layer did not supply the token: ~/.claude/clawgate.env "
        "carries CLAWGATE_HOOK_TOKEN and nothing is exported, which is the "
        "exact state in which every draft card was being skipped")


def test_the_process_environment_OVERRIDES_the_env_file_token(tmp_path):
    """🔴 THE PRECEDENCE, NOT JUST THE SOURCE. Both layers carry a token and the
    process one wins — `clawgatectl` applies the file FIRST and the environment
    ON TOP (`resolveConfig`), so a one-off `CLAWGATE_HOOK_TOKEN=… cmd` must beat
    a stale value in the file. A resolver with the two layers SWAPPED passes
    every file-alone assertion above and fails here."""
    got = clawgate.hook_token({"CLAWGATE_HOOK_TOKEN": ENV_TOKEN},
                              _token_file(tmp_path))
    assert got == ENV_TOKEN, (
        "the process environment must OVERRIDE the env file: expected %r, got "
        "%r — and %r would mean the two layers are applied in the wrong order"
        % (ENV_TOKEN, got, FILE_TOKEN))


def test_an_empty_process_token_does_not_MASK_the_file(tmp_path):
    """Empty means UNSET everywhere, matching the Go side's "only overrides when
    it actually supplies a value". `CLAWGATE_HOOK_TOKEN= cmd` must fall THROUGH
    to the file rather than erase it."""
    got = clawgate.hook_token({"CLAWGATE_HOOK_TOKEN": ""},
                              _token_file(tmp_path))
    assert got == FILE_TOKEN, (
        "an empty process variable masked the file's token (got %r)" % (got,))


def test_an_empty_token_in_the_FILE_is_unset_not_a_bare_bearer(tmp_path):
    """An empty value is "no token" on the file layer too — otherwise the header
    goes out as `Bearer ` and the server answers 401 instead of the producer
    saying it had no credential."""
    assert clawgate.hook_token({}, _token_file(tmp_path, token="")) is None


def test_no_token_in_either_layer_is_None(tmp_path):
    assert clawgate.hook_token({}, _ABSENT_ENV_FILE) is None
    assert clawgate.hook_token({}, _token_file(tmp_path, token=None)) is None


def test_an_UNOPENABLE_env_file_resolves_to_None_rather_than_raising(tmp_path):
    """A producer must degrade, never raise, on a file it cannot read — a
    missing token degrades notification and never the record.

    🔴 A DIRECTORY, not `chmod 000`: a mode-based test is decided by the uid the
    suite runs under (root opens an 0o000 file happily), while
    `IsADirectoryError` is the same `OSError` family and uid-independent."""
    path = tmp_path / "clawgate.env"
    path.mkdir()
    assert clawgate.hook_token({}, str(path)) is None


def test_the_DEFAULT_env_file_path_is_the_one_under_HOME(monkeypatch, tmp_path):
    """🔴 HARNESS CONTROL for the `path=`-less default. Everything above pins the
    path explicitly, which proves the LAYERING but not that the layer it reads
    by default is `~/.claude/clawgate.env`. Prove the default path is the HOME
    one — a value only this file can supply comes back — and that removing the
    file changes the answer."""
    monkeypatch.delenv("CLAWGATE_HOOK_TOKEN", raising=False)
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    path = home / ".claude" / "clawgate.env"
    path.write_text("CLAWGATE_HOOK_TOKEN=%s\n" % FILE_TOKEN, encoding="utf-8")
    assert clawgate.hook_token() == FILE_TOKEN, (
        "hook_token() did not read ~/.claude/clawgate.env by default — HOME "
        "redirect inert, so this assertion would be vacuous")
    path.unlink()
    assert clawgate.hook_token() is None, (
        "deleting the redirected file changed nothing — the resolver is not "
        "reading the path this harness controls")


# -- criterion 2: the card actually posts off the file-only token ------------- #

def test_emit_draft_task_POSTS_when_the_TOKEN_IS_ONLY_IN_THE_ENV_FILE(
        monkeypatch, tmp_path):
    """🔴 THE HEADLINE ASSERTION. The exact condition that failed: token present
    only in ~/.claude/clawgate.env, absent from the process environment. The
    card must post, with that token as the bearer."""
    monkeypatch.delenv("CLAWGATE_HOOK_TOKEN", raising=False)
    calls = []

    class Resp:
        def raise_for_status(self):
            calls.append("raised")

    module = types.ModuleType("requests")
    module.post = lambda url, headers=None, json=None, timeout=None: (
        calls.append({"url": url, "headers": headers, "json": json}) or Resp())
    monkeypatch.setitem(sys.modules, "requests", module)
    path = _token_file(tmp_path,
                       extra="CLAWGATE_TASK_API_URL=%s\n" % FILE_TASK_BASE)

    ok = clawgate.emit_draft_task(draft_id=307, recipient=PEER,
                                  body="please approve", env={}, path=path)
    assert ok is True, (
        "the card was NOT posted with the token present in the env file and "
        "absent from the environment — that is the defect, unfixed")
    call = calls[0]
    assert call["headers"]["Authorization"] == "Bearer " + FILE_TOKEN, (
        "the bearer was %r; the env file's token (%r) must be the credential"
        % (call["headers"]["Authorization"], FILE_TOKEN))
    assert call["url"] == FILE_TASK_BASE + TASKS_PATH


# -- criterion 3: one clear stderr line, no raise, record intact -------------- #

def test_no_token_anywhere_WARNS_ON_STDERR_and_returns_False(monkeypatch,
                                                             capsys):
    """One line, on stderr, naming WHAT was skipped and WHERE it looked — and no
    raise. Silence was the actual operator-visible symptom."""
    monkeypatch.delenv("CLAWGATE_HOOK_TOKEN", raising=False)
    posted = []
    module = types.ModuleType("requests")
    module.post = lambda *a, **k: posted.append(a)
    monkeypatch.setitem(sys.modules, "requests", module)

    assert clawgate.emit_draft_task(draft_id=99, recipient=PEER, body="x",
                                    env={}, path=_ABSENT_ENV_FILE) is False
    assert posted == []
    err = capsys.readouterr().err
    lines = [ln for ln in err.strip().splitlines() if ln.strip()]
    assert len(lines) == 1, (
        "expected exactly ONE stderr line, got %d:\n%s" % (len(lines), err))
    line = lines[0]
    assert "CLAWGATE_HOOK_TOKEN" in line, (
        "the warning must NAME the variable; got %r" % line)
    assert _ABSENT_ENV_FILE in line, (
        "the warning must name WHERE it looked (%s); got %r"
        % (_ABSENT_ENV_FILE, line))
    assert "99" in line, (
        "the warning must name WHAT was skipped (draft #99); got %r" % line)


def test_the_warning_never_carries_the_TOKEN_itself(monkeypatch, tmp_path,
                                                    capsys):
    """🔴 CRITERION 7, driven rather than asserted about. A warning built by
    formatting the resolved config would leak the credential into a log the
    moment one layer had a token and the resolver still refused. Feed a token
    the resolver MUST reject — empty on top of empty — and confirm no secret
    reaches either stream."""
    monkeypatch.delenv("CLAWGATE_HOOK_TOKEN", raising=False)
    module = types.ModuleType("requests")
    module.post = lambda *a, **k: None
    monkeypatch.setitem(sys.modules, "requests", module)

    assert clawgate.emit_draft_task(
        draft_id=5, recipient=PEER, body="x",
        env={"CLAWGATE_HOOK_TOKEN": ""},
        path=_token_file(tmp_path, token="")) is False
    cap = capsys.readouterr()
    for stream, text in (("stderr", cap.err), ("stdout", cap.out)):
        assert FILE_TOKEN not in text and ENV_TOKEN not in text, \
            "a token reached %s: %r" % (stream, text)


def test_the_POSTED_card_carries_the_token_ONLY_in_the_bearer_header(
        monkeypatch, tmp_path, capsys):
    """🔴 CRITERION 7 on the SUCCESS path. The token goes in one place: the
    Authorization header. Never in the URL, never in the JSON body, never on
    stdout or stderr."""
    monkeypatch.delenv("CLAWGATE_HOOK_TOKEN", raising=False)
    calls = []

    class Resp:
        def raise_for_status(self):
            pass

    module = types.ModuleType("requests")
    module.post = lambda url, headers=None, json=None, timeout=None: (
        calls.append({"url": url, "headers": headers, "json": json}) or Resp())
    monkeypatch.setitem(sys.modules, "requests", module)

    assert clawgate.emit_draft_task(draft_id=6, recipient=PEER, body="x",
                                    env={},
                                    path=_token_file(tmp_path)) is True
    call = calls[0]
    assert FILE_TOKEN not in call["url"], "the token is in the URL: %r" % call["url"]
    assert FILE_TOKEN not in json.dumps(call["json"]), \
        "the token is in the POSTed body"
    assert call["headers"]["Authorization"] == "Bearer " + FILE_TOKEN
    cap = capsys.readouterr()
    assert FILE_TOKEN not in cap.err and FILE_TOKEN not in cap.out


# -- criterion 1: ONE definition, and this module is not a second one --------- #

def test_the_token_precedence_is_the_SHARED_one_not_a_local_respelling(tmp_path):
    """🔴 THE SEAM. Every test above would also pass over a private copy of the
    rule living in this module — the exact regrowth the shared definition exists
    to prevent. This pins that the module RESOLVES THROUGH the shared module:
    the variable name comes from the shared constant, and this producer's
    resolver and the shared one agree on the same inputs."""
    cg = clawgate._load_clawgate_tasks()
    assert cg.HOOK_TOKEN_VAR == "CLAWGATE_HOOK_TOKEN"
    path = _token_file(tmp_path)
    assert clawgate.hook_token({}, path) == cg.hook_token({}, path)
    assert clawgate.hook_token({cg.HOOK_TOKEN_VAR: ENV_TOKEN}, path) \
        == cg.hook_token({cg.HOOK_TOKEN_VAR: ENV_TOKEN}, path) == ENV_TOKEN
    assert clawgate.hook_token({}, _ABSENT_ENV_FILE) \
        is cg.hook_token({}, _ABSENT_ENV_FILE) is None


def _os_environ_token_reads(src: str) -> list:
    """Line numbers where `src` reads the hook token straight out of the process
    environment — `os.environ[...]`, `os.environ.get(...)` or `os.getenv(...)`.

    🔴 AN AST WALK, NOT A TEXTUAL NEEDLE, and the difference is load-bearing:
    every docstring in these producers QUOTES
    `os.environ.get("CLAWGATE_HOOK_TOKEN")` to record why it is gone, so a grep
    fires on the prose that documents the fix and the guard becomes noise
    somebody deletes. A parse sees a Call or a Subscript, or nothing.
    """
    hits = []
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Call):
            target = node.func
            names = [a.value for a in node.args
                     if isinstance(a, ast.Constant) and isinstance(a.value, str)]
        elif isinstance(node, ast.Subscript):
            target = node.value
            s = node.slice
            names = ([s.value] if isinstance(s, ast.Constant)
                     and isinstance(s.value, str) else [])
        else:
            continue
        if "CLAWGATE_HOOK_TOKEN" not in names:
            continue
        dumped = ast.dump(target)
        if "environ" in dumped or "getenv" in dumped:
            hits.append(node.lineno)
    return hits


def test_the_producer_reads_the_token_through_the_SHARED_resolver_ONLY():
    """🔴 CRITERION 1, structurally. One shared helper is the ONLY place this
    producer reads the token — so a re-grown `os.environ.get(...)` beside it,
    which would pass every behavioural test above by shadowing nothing, fails
    here instead."""
    src = Path(clawgate.__file__).read_text(encoding="utf-8")
    hits = _os_environ_token_reads(src)
    assert hits == [], (
        "%s reads CLAWGATE_HOOK_TOKEN out of the process environment at line(s) "
        "%s. Resolve it through scripts/lib/clawgate_tasks.hook_token instead — "
        "an environment-only read is blind to ~/.claude/clawgate.env, which is "
        "where the token actually lives." % (clawgate.__file__, hits))


def test_the_token_read_needle_can_actually_fire():
    """POSITIVE AND NEGATIVE CONTROL for the scan above. A guard reporting zero
    on a clean tree is indistinguishable from one wired to nothing — and this
    one in particular must NOT fire on the three near-misses, or it reports a
    finding on its own documentation."""
    assert _os_environ_token_reads(
        'import os\nt = os.environ.get("CLAWGATE_HOOK_TOKEN")\n') == [2]
    assert _os_environ_token_reads(
        'import os\nt = os.environ["CLAWGATE_HOOK_TOKEN"]\n') == [2]
    assert _os_environ_token_reads(
        'import os\nt = os.getenv("CLAWGATE_HOOK_TOKEN")\n') == [2]
    # the prose that documents the fix is NOT a finding
    assert _os_environ_token_reads(
        '"""Not os.environ.get("CLAWGATE_HOOK_TOKEN") any more."""\n') == []
    # a DIFFERENT variable read from the environment is not this guard's business
    assert _os_environ_token_reads(
        'import os\nu = os.environ.get("CLAWGATE_API_URL")\n') == []
    # the shared resolver, reached by name, is the thing we WANT
    assert _os_environ_token_reads(
        'x = cg.hook_token(env, path)  # CLAWGATE_HOOK_TOKEN\n') == []


# -- criterion 6: D3 — the DRAFT survives a missing token -------------------- #

def test_a_tokenless_draft_is_STILL_STORED_and_the_command_returns(db, monkeypatch,
                                                                   capsys):
    """🔴 DECISION D3, driven end to end rather than asserted about. With no
    token resolvable from either layer, `consumer.py draft` must store the draft
    row and return normally — a missing token degrades NOTIFICATION, never the
    record. The pre-fix code got this right by accident (it never looked at the
    file at all); the fix must not trade it away for the new file read."""
    monkeypatch.delenv("CLAWGATE_HOOK_TOKEN", raising=False)
    posted = []
    module = types.ModuleType("requests")
    module.post = lambda *a, **k: posted.append(a)
    monkeypatch.setitem(sys.modules, "requests", module)

    draft = _pending(db, body="durable with no clawgate token at all")
    emitted = clawgate.emit_draft_task(
        draft_id=draft["id"], recipient=PEER,
        body="durable with no clawgate token at all",
        env={}, path=_ABSENT_ENV_FILE)

    assert emitted is False            # no card
    assert posted == []                # nothing left the process
    # ...and the RECORD is intact and still approvable.
    stored = db.get_draft(draft["id"])
    assert stored["send_state"] == _signal_db.STATE_PENDING
    assert stored["body"] == "durable with no clawgate token at all"
    assert db.list_drafts(state=_signal_db.STATE_PENDING)
    # the skip was NAMED, not silent
    assert "CLAWGATE_HOOK_TOKEN" in capsys.readouterr().err
