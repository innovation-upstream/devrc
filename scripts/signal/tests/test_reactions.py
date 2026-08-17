"""🔧 #3 — reactions can arrive BEFORE their target, or target nothing we have.

SSE ordering is not guaranteed and history is not backfilled, so a hard FK at
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
