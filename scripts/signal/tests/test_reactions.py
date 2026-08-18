"""🔧 #3 — reactions can arrive BEFORE their target, or target nothing we have.

Delivery ordering is not guaranteed and history is not backfilled, so a hard FK
insert time silently drops reactions. `message_id` is therefore NULLable and
resolved later; the partial index `idx_rx_unresolved` is what keeps the
resolution sweep from scanning the whole table.
"""
import sqlite3

import pytest

import consumer

ALICE = "11111111-1111-4111-8111-111111111111"
CARL = "33333333-3333-4333-8333-333333333333"
DANA = "44444444-4444-4444-8444-444444444444"
TARGET_TS = 1723200000101
NEVER_SEEN_TS = 1723200000999


def _reaction(*, reactor, author, ts, emoji="🔥", remove=False):
    return {"source_uuid": reactor, "target_author_uuid": author,
            "target_sent_timestamp": ts, "emoji": emoji, "is_remove": remove}


def _message(*, uuid, ts, body="target message"):
    return {"message_timestamp": ts, "source_uuid": uuid,
            "message_type": consumer.KIND_MESSAGE, "body": body}


# --------------------------------------------------------------------------- #
def test_reaction_after_its_target_resolves_immediately(db):
    """POSITIVE CONTROL for the whole suite: the in-order case must work."""
    db.upsert_message(_message(uuid=ALICE, ts=TARGET_TS))
    db.upsert_reaction(_reaction(reactor=CARL, author=ALICE, ts=TARGET_TS))
    row = db.conn.rows("SELECT message_id FROM signal.reactions")[0]
    assert row["message_id"] is not None
    assert db.unresolved_reactions() == []


def test_reaction_before_its_target_is_retained_then_resolved(db):
    """The out-of-order case the correction exists for."""
    db.upsert_reaction(_reaction(reactor=CARL, author=ALICE, ts=TARGET_TS))
    assert db.conn.count("reactions") == 1
    pending = db.unresolved_reactions()
    assert len(pending) == 1 and pending[0]["emoji"] == "🔥"

    message_id = db.upsert_message(_message(uuid=ALICE, ts=TARGET_TS))

    assert db.unresolved_reactions() == []
    row = db.conn.rows("SELECT message_id FROM signal.reactions")[0]
    assert row["message_id"] == message_id


def test_reaction_to_a_never_received_message_is_kept_not_dropped(db):
    db.upsert_reaction(_reaction(reactor=DANA, author=ALICE, ts=NEVER_SEEN_TS,
                                 emoji="👀"))
    # An UNRELATED message arriving must not resolve it...
    db.upsert_message(_message(uuid=ALICE, ts=TARGET_TS))
    assert len(db.unresolved_reactions()) == 1
    # ... and the row is still there, with its content intact.
    row = db.conn.rows("SELECT emoji, target_sent_timestamp FROM signal.reactions "
                       "WHERE message_id IS NULL")[0]
    assert row["emoji"] == "👀"
    assert row["target_sent_timestamp"] == NEVER_SEEN_TS


def test_resolution_is_scoped_to_the_right_author(db):
    """A same-timestamp message from a DIFFERENT author must not steal the reaction."""
    db.upsert_reaction(_reaction(reactor=CARL, author=ALICE, ts=TARGET_TS))
    db.upsert_message(_message(uuid=DANA, ts=TARGET_TS, body="coincidence"))
    assert len(db.unresolved_reactions()) == 1
    db.upsert_message(_message(uuid=ALICE, ts=TARGET_TS))
    assert db.unresolved_reactions() == []


def test_remove_reaction_updates_the_existing_row(db):
    db.upsert_message(_message(uuid=ALICE, ts=TARGET_TS))
    db.upsert_reaction(_reaction(reactor=CARL, author=ALICE, ts=TARGET_TS,
                                 emoji="😂"))
    db.upsert_reaction(_reaction(reactor=CARL, author=ALICE, ts=TARGET_TS,
                                 emoji="😂", remove=True))
    assert db.conn.count("reactions") == 1          # updated, not appended
    row = db.conn.rows("SELECT is_remove FROM signal.reactions")[0]
    assert row["is_remove"]


def test_two_people_reacting_are_two_rows(db):
    """POSITIVE CONTROL for the 'one row' claim above."""
    db.upsert_message(_message(uuid=ALICE, ts=TARGET_TS))
    db.upsert_reaction(_reaction(reactor=CARL, author=ALICE, ts=TARGET_TS))
    db.upsert_reaction(_reaction(reactor=DANA, author=ALICE, ts=TARGET_TS,
                                 emoji="🎉"))
    assert db.conn.count("reactions") == 2


def test_redelivered_reaction_does_not_duplicate(db):
    db.upsert_message(_message(uuid=ALICE, ts=TARGET_TS))
    rx = _reaction(reactor=CARL, author=ALICE, ts=TARGET_TS)
    first = db.upsert_reaction(dict(rx))
    second = db.upsert_reaction(dict(rx))
    assert first == second
    assert db.conn.count("reactions") == 1


def test_resolve_pending_reactions_reports_how_many_it_moved(db):
    db.upsert_reaction(_reaction(reactor=CARL, author=ALICE, ts=TARGET_TS))
    db.upsert_reaction(_reaction(reactor=DANA, author=ALICE, ts=TARGET_TS,
                                 emoji="🎉"))
    author_id = db.upsert_contact(signal_uuid=ALICE)
    assert len(db.unresolved_reactions()) == 2

    # Insert the target row directly, so the sweep's OWN return value is what is
    # being read (going through upsert_message would resolve them as a side
    # effect and the count would always be 0 — a zero proving nothing).
    db.conn.raw.execute(
        "INSERT INTO signal.messages (message_timestamp, source_contact_id, "
        "message_type) VALUES (?, ?, 'message')", (TARGET_TS, author_id))
    message_id = db.conn.rows("SELECT id FROM signal.messages")[0]["id"]

    moved = db.resolve_pending_reactions(
        message_id=message_id, target_author_id=author_id,
        target_sent_timestamp=TARGET_TS)
    assert moved == 2
    assert db.resolve_pending_reactions(
        message_id=message_id, target_author_id=author_id,
        target_sent_timestamp=TARGET_TS) == 0        # idempotent
    assert db.unresolved_reactions() == []


def test_the_partial_index_exists_and_is_scoped_to_unresolved_rows(db):
    """The index the resolution sweep depends on, read from the live schema."""
    ddl = db.conn.rows(
        "SELECT sql FROM signal.sqlite_master WHERE name = 'idx_rx_unresolved'")
    assert len(ddl) == 1
    sql = ddl[0]["sql"]
    assert "message_id IS NULL" in sql
    assert "target_author_id" in sql and "target_sent_timestamp" in sql


def test_the_partial_index_is_actually_used_by_the_sweep(db):
    """Not just present — CHOSEN. A partial index nothing plans against is decoration."""
    author_id = db.upsert_contact(signal_uuid=ALICE)
    plan = db.conn.rows(
        "EXPLAIN QUERY PLAN SELECT id FROM signal.reactions "
        "WHERE message_id IS NULL AND target_author_id = ? "
        "AND target_sent_timestamp = ?", (author_id, TARGET_TS))
    detail = " ".join(str(r.get("detail", "")) for r in plan)
    assert "idx_rx_unresolved" in detail, detail


def test_reaction_requires_a_target_timestamp():
    bad = {"source": "+15550303", "timestamp": 1723200000303,
           "dataMessage": {"timestamp": 1723200000303,
                           "reaction": {"emoji": "x", "targetAuthor": "+1"}}}
    with pytest.raises(consumer.MalformedEvent):
        consumer.parse_envelope(bad)


# --------------------------------------------------------------------------- #
# Remote delete — a retraction, not a message
# --------------------------------------------------------------------------- #
def test_remote_delete_tombstones_the_target_instead_of_storing_a_ghost(db):
    """🔴 A `remoteDelete` is the SENDER RETRACTING a message.

    Unrecognised, it falls through to the data-message path and is stored as an
    empty-bodied message — a ghost row that also leaves the retracted text
    sitting in the row it was meant to retract. Both halves are wrong.
    """
    db.upsert_message({**_message(uuid=ALICE, ts=TARGET_TS, body="please forget this"),
                       "attachments": [{"id": "att-doomed", "content_type": "image/png",
                                        "filename": "oops.png"}]})
    assert db.conn.count("attachments") == 1

    event = consumer.parse_envelope({
        "sourceUuid": ALICE, "sourceNumber": "+15550101",
        "timestamp": TARGET_TS + 50,
        "dataMessage": {"timestamp": TARGET_TS + 50,
                        "remoteDelete": {"targetSentTimestamp": TARGET_TS}},
    })
    assert event.kind == consumer.KIND_REMOTE_DELETE
    assert db.apply_remote_delete(event.remote_delete) == 1

    rows = db.conn.rows("SELECT body, message_type, raw_envelope FROM signal.messages")
    assert len(rows) == 1                       # no ghost row was added
    assert rows[0]["body"] is None              # the text is GONE
    assert rows[0]["raw_envelope"] is None      # ... including from the raw copy
    assert rows[0]["message_type"] == "deleted"
    assert db.conn.count("attachments") == 0    # and its attachments with it


def test_a_retraction_made_on_ZACHS_OWN_PHONE_is_honoured(db):
    """🔴 The own-device direction, which arrives wrapped in `syncMessage`.

    The sync branch runs BEFORE the inbound `dataMessage` branch, so a retraction
    Zach makes on his own phone never reached the remote-delete handler: both
    defects it exists to prevent — the ghost row AND the retained retracted text
    — survived in that direction, while SKILL.md claimed retractions were
    honoured full stop.
    """
    self_uuid = "90909090-9090-4909-8909-909090909090"
    target_ts = 1723250000001
    sent = consumer.parse_envelope({
        "sourceUuid": self_uuid, "sourceNumber": "+15559090",
        "timestamp": target_ts,
        "syncMessage": {"sentMessage": {
            "timestamp": target_ts, "destination": "+15550101",
            "message": "typed on my phone, then retracted"}},
    })
    db.upsert_message(sent.message)
    assert db.conn.count("messages") == 1

    retraction = consumer.parse_envelope({
        "sourceUuid": self_uuid, "sourceNumber": "+15559090",
        "timestamp": target_ts + 5,
        "syncMessage": {"sentMessage": {
            "timestamp": target_ts + 5, "destination": "+15550101",
            "remoteDelete": {"targetSentTimestamp": target_ts}}},
    })
    assert retraction.kind == consumer.KIND_REMOTE_DELETE
    assert retraction.message is None
    assert db.apply_remote_delete(retraction.remote_delete) == 1

    rows = db.conn.rows("SELECT body, message_type FROM signal.messages")
    assert len(rows) == 1
    assert rows[0]["body"] is None
    assert rows[0]["message_type"] == "deleted"


def test_a_sync_remote_delete_without_a_target_is_malformed():
    with pytest.raises(consumer.MalformedEvent):
        consumer.parse_envelope({
            "sourceUuid": ALICE, "timestamp": 1,
            "syncMessage": {"sentMessage": {"timestamp": 1, "remoteDelete": {}}}})


def test_remote_delete_for_a_message_we_never_received_is_a_quiet_no_op(db):
    event = consumer.parse_envelope({
        "sourceUuid": ALICE, "timestamp": NEVER_SEEN_TS + 1,
        "dataMessage": {"timestamp": NEVER_SEEN_TS + 1,
                        "remoteDelete": {"targetSentTimestamp": NEVER_SEEN_TS}},
    })
    assert db.apply_remote_delete(event.remote_delete) == 0
    assert db.conn.count("messages") == 0


def test_remote_delete_does_not_touch_a_different_senders_message(db):
    """POSITIVE CONTROL on the scoping: only the retractor's own row moves."""
    db.upsert_message(_message(uuid=DANA, ts=TARGET_TS, body="someone else's message"))
    event = consumer.parse_envelope({
        "sourceUuid": ALICE, "timestamp": TARGET_TS + 60,
        "dataMessage": {"timestamp": TARGET_TS + 60,
                        "remoteDelete": {"targetSentTimestamp": TARGET_TS}},
    })
    assert db.apply_remote_delete(event.remote_delete) == 0
    assert db.conn.rows("SELECT body FROM signal.messages")[0]["body"] == \
        "someone else's message"


def test_remote_delete_without_a_target_is_malformed():
    with pytest.raises(consumer.MalformedEvent):
        consumer.parse_envelope({
            "sourceUuid": ALICE, "timestamp": 1,
            "dataMessage": {"timestamp": 1, "remoteDelete": {}}})


def test_reactions_unique_constraint_is_live(db):
    author = db.upsert_contact(signal_uuid=ALICE)
    reactor = db.upsert_contact(signal_uuid=CARL)
    db.conn.raw.execute(
        "INSERT INTO signal.reactions (target_author_id, target_sent_timestamp, "
        "emoji, contact_id) VALUES (?, ?, '🔥', ?)", (author, TARGET_TS, reactor))
    with pytest.raises(sqlite3.IntegrityError):
        db.conn.raw.execute(
            "INSERT INTO signal.reactions (target_author_id, target_sent_timestamp, "
            "emoji, contact_id) VALUES (?, ?, '🎉', ?)",
            (author, TARGET_TS, reactor))


# --------------------------------------------------------------------------- #
# 🔴 The own-device REACTION direction — the sibling the remote-delete fix missed.
#
# Found by step 7, from LIVE traffic: a reaction Zach sent from his phone arrived
# as `syncMessage.sentMessage.reaction` with `message: None`. The sync branch runs
# BEFORE the inbound `dataMessage` branch and handled only `remoteDelete`, so the
# reaction fell through to `_base_message()` and produced exactly the two defects
# the remote-delete branch exists to prevent: the reaction was DROPPED from
# signal.reactions, and a bodyless ghost row was left in signal.messages.
#
# The live miss nearly read as clean — two OTHER group members had reacted to the
# same message, so a count of reactions carrying that target timestamp said "2,
# fine". Zach's own contact was absent from every one of them. Hence the reactor
# identity assertion below: a count assertion here would not have caught this.
#
# 🔴 VALUES ARE DISTINCT FROM THE CORPUS AND FROM EVERY CONSTANT NAMED BELOW. The
# first version of this file used 🔥 — the emoji the corpus `reaction` fixture
# ALSO uses and the emoji the assertions name — so a mutant hardcoding
# `"emoji": "🔥"` survived all 400 tests while corrupting every stored reaction.
# Same for SELF_UUID/SELF_NUMBER, which were byte-identical to the corpus
# `sync_outbound` fixture. See fixtures/envelopes.json `_README`.

SELF_UUID = "95959595-9595-4959-8959-959595959595"
SELF_NUMBER = "+15559095"
SYNC_EMOJI = "🎯"        # distinct from the corpus 🔥 / 😂 and from REMOVE_EMOJI
REMOVE_EMOJI = "🧊"


def _sync_reaction_envelope(*, target_ts, emoji=SYNC_EMOJI, remove=False, **over):
    reaction = {"emoji": emoji, "isRemove": remove,
                "targetAuthorUuid": ALICE, "targetAuthor": "+15550101",
                "targetSentTimestamp": target_ts}
    reaction.update(over.pop("reaction", {}))
    env = {"source": SELF_NUMBER, "sourceUuid": SELF_UUID,
           "sourceNumber": SELF_NUMBER, "timestamp": target_ts + 11,
           "syncMessage": {"sentMessage": {
               "message": None, "timestamp": target_ts + 11,
               "reaction": reaction}}}
    env.update(over)
    return env


def test_a_reaction_made_on_ZACHS_OWN_PHONE_is_stored_not_dropped(db):
    """Regression: outbound reaction -> a reactions row, NOT a ghost message."""
    target_ts = 1723260000077
    db.upsert_message(_message(uuid=ALICE, ts=target_ts))
    assert db.conn.count("messages") == 1

    event = consumer.parse_envelope(_sync_reaction_envelope(target_ts=target_ts))

    # It is a REACTION, and it carries NO message -- so no ghost row can be written.
    assert event.kind == consumer.KIND_REACTION
    assert event.message is None

    # The reactor is the ACCOUNT ITSELF, not the author of the reacted-to message.
    # This is the assertion the live data needed: other people's reactions on the
    # same target were present, so only the identity distinguishes stored from lost.
    assert event.reaction["source_uuid"] == SELF_UUID
    assert event.reaction["source_number"] == SELF_NUMBER
    assert event.reaction["target_author_uuid"] == ALICE
    assert event.reaction["target_author_number"] == "+15550101"
    assert event.reaction["target_sent_timestamp"] == target_ts
    assert event.reaction["emoji"] == SYNC_EMOJI
    assert event.reaction["is_remove"] is False

    db.upsert_reaction(event.reaction)

    # Stored, resolved to its target, and still exactly one message row.
    assert db.conn.count("messages") == 1
    rows = db.conn.rows("SELECT message_id, emoji FROM signal.reactions")
    assert len(rows) == 1
    assert rows[0]["message_id"] is not None
    assert rows[0]["emoji"] == SYNC_EMOJI
    assert db.unresolved_reactions() == []


def test_an_own_phone_reaction_REMOVAL_is_stored_as_a_removal(db):
    """`isRemove` must survive the sync path, or un-reacting is invisible."""
    event = consumer.parse_envelope(_sync_reaction_envelope(
        target_ts=1723260000088, emoji=REMOVE_EMOJI, remove=True))
    assert event.kind == consumer.KIND_REACTION
    assert event.message is None
    assert event.reaction["emoji"] == REMOVE_EMOJI
    assert event.reaction["is_remove"] is True


def test_a_TRUTHY_non_bool_isRemove_is_coerced_to_a_real_bool():
    """`bool()` is load-bearing: the DB column is boolean, JSON is not typed."""
    event = consumer.parse_envelope(_sync_reaction_envelope(
        target_ts=1723260000089, reaction={"isRemove": "yes"}))
    assert event.reaction["is_remove"] is True


def test_an_own_phone_reaction_identified_only_by_targetAuthorNumber_is_kept():
    """signal-cli exposes THREE identity fields; the first cut read only two.

    A reaction carrying only `targetAuthorNumber` looked unidentifiable and would
    have been rejected by the guard below — silently losing a real reaction.
    """
    event = consumer.parse_envelope(_sync_reaction_envelope(
        target_ts=1723260000090,
        reaction={"targetAuthorUuid": None, "targetAuthor": None,
                  "targetAuthorNumber": "+15550102"}))
    assert event.kind == consumer.KIND_REACTION
    assert event.reaction["target_author_number"] == "+15550102"


# --------------------------------------------------------------------------- #
# 🔴 The guards below are NOT about tidiness. `upsert_reaction` resolves BOTH the
# target author and the reactor through `upsert_contact`, which raises ValueError
# on an unidentifiable contact — and ValueError is neither MalformedEvent nor a
# TRANSIENT_DB_ERROR, so it escapes store(), escapes handle_payload() (which
# wraps only the PARSE) and lands in run()'s reconnect handler. Signal redelivers
# on reconnect, so one such frame is an UNBOUNDED loop that stores nothing and
# takes every later frame on that connection with it. See the seam test in
# test_consumer_resilience.py, which drives that end-to-end.

def test_a_sync_reaction_without_a_target_timestamp_is_malformed():
    with pytest.raises(consumer.MalformedEvent):
        consumer.parse_envelope(_sync_reaction_envelope(
            target_ts=1723260000091, reaction={"targetSentTimestamp": None}))


def test_a_sync_reaction_with_NO_TARGET_AUTHOR_IDENTITY_is_malformed():
    """Neither uuid nor number -> upsert_contact would raise and wedge ingest."""
    # `match=` pins the site label: it is the ONLY forensic trail a skipped
    # frame leaves, and it exists solely to say WHICH site rejected. A mutant
    # swapping it to "inbound" survived an otherwise-green run.
    with pytest.raises(consumer.MalformedEvent, match=r"^sync "):
        consumer.parse_envelope(_sync_reaction_envelope(
            target_ts=1723260000092,
            reaction={"targetAuthorUuid": None, "targetAuthor": None,
                      "targetAuthorNumber": None}))


def test_a_sync_reaction_with_NO_REACTOR_IDENTITY_is_malformed():
    """The reactor goes through upsert_contact too — same wedge, other side."""
    env = _sync_reaction_envelope(target_ts=1723260000093)
    env["sourceUuid"] = None
    env["sourceNumber"] = None
    env["source"] = None
    with pytest.raises(consumer.MalformedEvent):
        consumer.parse_envelope(env)


def test_an_EMPTY_sync_reaction_object_is_malformed_not_silently_a_message():
    """Pins `is not None` over truthiness — the two differ ONLY here.

    With truthiness an empty `reaction: {}` is falsy, falls through, and is stored
    as an ordinary `sync_outbound`; a malformed frame would then be invisible,
    counted as a successful message. `is not None` reports it. Same reasoning the
    remoteDelete branch above documents, and nothing else in the suite
    distinguishes the two idioms — a mutant swapping them survived a green run.

    The trade is deliberate: a hypothetical envelope carrying BOTH an empty
    reaction and a real body loses the body. Signal does not emit that shape, and
    a silently-swallowed malformed frame is the worse failure of the two.
    """
    with pytest.raises(consumer.MalformedEvent):
        consumer.parse_envelope({
            "sourceUuid": SELF_UUID, "sourceNumber": SELF_NUMBER, "timestamp": 1,
            "syncMessage": {"sentMessage": {
                "message": None, "timestamp": 1, "reaction": {}}}})


def test_a_NON_DICT_sync_reaction_is_malformed_not_an_AttributeError():
    """A non-object `reaction` took the same escape route as the identity hole."""
    with pytest.raises(consumer.MalformedEvent):
        consumer.parse_envelope({
            "sourceUuid": SELF_UUID, "sourceNumber": SELF_NUMBER, "timestamp": 1,
            "syncMessage": {"sentMessage": {
                "message": None, "timestamp": 1, "reaction": "not-an-object"}}})


def test_the_INBOUND_reaction_path_has_the_same_guards(db):
    """One parser, both sites (🔴 consolidation).

    The sync site shipped missing guards the inbound site had, because the dict
    was open-coded twice. These pin that the shared helper protects BOTH — if the
    inbound site is ever re-open-coded, this goes red.
    """
    for bad in (
        {"targetAuthorUuid": None, "targetAuthor": None, "targetAuthorNumber": None},
        {"targetSentTimestamp": None},
    ):
        reaction = {"emoji": SYNC_EMOJI, "targetAuthorUuid": ALICE,
                    "targetAuthor": "+15550101",
                    "targetSentTimestamp": 1723260000094}
        reaction.update(bad)
        with pytest.raises(consumer.MalformedEvent):
            consumer.parse_envelope({
                "sourceUuid": CARL, "sourceNumber": "+15550303",
                "timestamp": 1723260000095,
                "dataMessage": {"timestamp": 1723260000095, "reaction": reaction}})


# --------------------------------------------------------------------------- #
# 🔴 FALLBACK ORDER. Both `or` chains below survived a fully green 416-test run
# when their operands were SWAPPED, because every fixture set the two fields to
# the SAME value — so the mutant could not change the output. Operand-order is a
# mutation class that deletes nothing, and a fixture of equal values collapses
# both implementations into identical results. These use pairwise-DISTINCT values
# so the order is actually observable.

def test_sourceNumber_WINS_over_source_for_the_reactor_phone_number():
    """`source` is not always a phone number — newer signal-cli can put a UUID
    there — so taking it first would write a UUID into a phone column."""
    event = consumer.parse_envelope({
        "source": "+15559096",            # deliberately DIFFERENT from sourceNumber
        "sourceNumber": "+15559095",
        "sourceUuid": SELF_UUID, "timestamp": 1723260000101,
        "syncMessage": {"sentMessage": {
            "message": None, "timestamp": 1723260000101,
            "reaction": {"emoji": SYNC_EMOJI, "isRemove": False,
                         "targetAuthorUuid": ALICE, "targetAuthor": "+15550101",
                         "targetSentTimestamp": 1723260000100}}}})
    assert event.reaction["source_number"] == "+15559095"


def test_targetAuthor_WINS_over_targetAuthorNumber_for_the_target_phone_number():
    """Pins which identity field is primary when signal-cli sends both."""
    event = consumer.parse_envelope({
        "sourceUuid": SELF_UUID, "sourceNumber": SELF_NUMBER,
        "timestamp": 1723260000103,
        "syncMessage": {"sentMessage": {
            "message": None, "timestamp": 1723260000103,
            "reaction": {"emoji": SYNC_EMOJI, "isRemove": False,
                         "targetAuthorUuid": ALICE,
                         "targetAuthor": "+15550101",
                         "targetAuthorNumber": "+15550109",   # DISTINCT
                         "targetSentTimestamp": 1723260000102}}}})
    assert event.reaction["target_author_number"] == "+15550101"


def test_a_reactor_identified_ONLY_by_the_legacy_source_field_is_kept():
    """The `or envelope.get("source")` fallback is now REJECT/ACCEPT logic.

    Before the identity guard it merely nulled a stored column; now, dropping it
    makes this envelope malformed and the reaction is lost. A mutant removing it
    survived, because the other reactor test sets BOTH fields and so cannot see
    which one carried the value.
    """
    event = consumer.parse_envelope({
        "source": "+15559097",            # legacy field ONLY -- no sourceNumber
        "sourceUuid": None, "timestamp": 1723260000105,
        "syncMessage": {"sentMessage": {
            "message": None, "timestamp": 1723260000105,
            "reaction": {"emoji": SYNC_EMOJI, "isRemove": False,
                         "targetAuthorUuid": ALICE, "targetAuthor": "+15550101",
                         "targetSentTimestamp": 1723260000104}}}})
    assert event.kind == consumer.KIND_REACTION
    assert event.reaction["source_number"] == "+15559097"


def test_an_EMPTY_INBOUND_reaction_object_is_malformed_too(db):
    """One rule, BOTH sites — the dispatch predicate, not just the parser body.

    Consolidating only `_reaction_from()` left inbound on truthiness, so an empty
    `reaction: {}` was stored there as an ordinary message while the identical
    sync shape was reported malformed. The argument for `is not None` does not
    stop at the site that happened to get audited.
    """
    with pytest.raises(consumer.MalformedEvent, match=r"^inbound "):
        consumer.parse_envelope({
            "sourceUuid": CARL, "sourceNumber": "+15550303",
            "timestamp": 1723260000107,
            "dataMessage": {"timestamp": 1723260000107, "reaction": {}}})


def test_an_ordinary_sync_message_is_STILL_an_outbound_message(db):
    """Guard the narrowing: only reaction-bearing syncs change branch.

    ⚠ An INVARIANT guard, not regression coverage — it passes on the pre-fix code
    too. It is here to catch the reaction branch growing too greedy, which is a
    different failure from the one this file's other tests pin.
    """
    ts = 1723260000099
    event = consumer.parse_envelope({
        "sourceUuid": SELF_UUID, "sourceNumber": SELF_NUMBER, "timestamp": ts,
        "syncMessage": {"sentMessage": {
            "message": "an ordinary outbound message", "timestamp": ts,
            "destination": "+15550101", "destinationUuid": ALICE}},
    })
    assert event.kind == consumer.KIND_SYNC_OUTBOUND
    assert event.message is not None
    assert event.message["is_outbound"] is True
