"""🔧 #4 — the outbound sync echo. The single most likely thing to be silently wrong.

A message sent through `/v2/send` comes BACK on the receive stream as a sync
message from the account's own linked devices. `unique_message` catches that echo
only if the send path recorded the SAME `(source_contact_id, message_timestamp)`
the echo will carry — i.e. the SERVER-assigned timestamp, not a locally generated
one. Two independent halves, both tested:

* the send path stores the server's timestamp (and refuses a non-positive one);
* the echo, fed end to end through `parse_envelope` → `upsert_message`, lands on
  the row that already exists — same row id, `is_outbound` still true.

The identity half matters as much as the timestamp half: the draft's sending
contact and the echo's sender must resolve to the SAME contact row, or the
composite key differs and the echo inserts a second copy anyway.
"""
import json

import pytest

import consumer
import _signal_db

SELF_UUID = "90909090-9090-4909-8909-909090909090"
SELF_NUMBER = "+15559090"
PEER_NUMBER = "+15550101"
SERVER_TS = 1723000009090        # what the API assigns and the echo carries
LOCAL_TS = 1723000007777         # deliberately DIFFERENT from SERVER_TS

# 🔴 The API returns the timestamp as a STRING — upstream types it
# `ds.SendMessageResponse.Timestamp string`. A fake returning an int would let a
# caller that only handles ints pass here and fail against the real server, so
# every fake below returns the string form.


def _approved_draft(db, *, body="sent from my laptop"):
    draft = db.draft_message(recipient=PEER_NUMBER, body=body,
                             self_number=SELF_NUMBER)
    return db.approve_draft(draft["id"], approval_ref="clawgate-echo-1")


def _echo_envelope(*, timestamp, body="sent from my laptop"):
    return {
        "source": SELF_NUMBER,
        "sourceNumber": SELF_NUMBER,
        "sourceUuid": SELF_UUID,
        "timestamp": timestamp,
        "syncMessage": {
            "sentMessage": {
                "timestamp": timestamp,
                "destination": PEER_NUMBER,
                "message": body,
            }
        },
    }


def _transmit_returning(ts, *, as_string=True):
    def _t(auth, *, recipient, body, number):
        assert number, "the server requires the sending `number` and 400s without it"
        return {"timestamp": str(ts) if as_string else ts}
    return _t


# --------------------------------------------------------------------------- #
def test_send_records_the_server_timestamp_not_a_local_one(db):
    draft = _approved_draft(db)
    assert draft["message_timestamp"] < 0          # provisional, pre-send
    sent = db.send_approved(draft["id"], transmit=_transmit_returning(SERVER_TS))
    assert sent["message_timestamp"] == SERVER_TS
    assert sent["send_state"] == _signal_db.STATE_SENT


def test_the_sync_echo_does_not_duplicate_the_sent_message(db):
    """The whole point: send, then let the echo arrive the way it really does."""
    draft = _approved_draft(db)
    db.send_approved(draft["id"], transmit=_transmit_returning(SERVER_TS))
    assert db.conn.count("messages") == 1

    event = consumer.parse_envelope(_echo_envelope(timestamp=SERVER_TS))
    echoed_id = db.upsert_message(event.message)

    assert db.conn.count("messages") == 1
    assert echoed_id == draft["id"]
    row = db.conn.rows("SELECT * FROM signal.messages")[0]
    assert row["is_outbound"]
    assert row["message_timestamp"] == SERVER_TS


def test_positive_control_an_unrelated_sync_message_does_insert(db):
    """A bare 1 above would be indistinguishable from a substrate that stores nothing."""
    draft = _approved_draft(db)
    db.send_approved(draft["id"], transmit=_transmit_returning(SERVER_TS))
    other = consumer.parse_envelope(_echo_envelope(timestamp=SERVER_TS + 1,
                                                   body="a different message"))
    db.upsert_message(other.message)
    assert db.conn.count("messages") == 2


def test_the_echo_reuses_the_senders_contact_row(db):
    """The identity half of 🔧 #4.

    The draft addressed the account by NUMBER (placeholder contact); the echo
    arrives carrying the account's real UUID. If those became two contact rows,
    the composite key would differ and dedupe would fail even with a correct
    timestamp.
    """
    draft = _approved_draft(db)
    db.send_approved(draft["id"], transmit=_transmit_returning(SERVER_TS))
    contacts_before = db.conn.count("contacts")
    event = consumer.parse_envelope(_echo_envelope(timestamp=SERVER_TS))
    db.upsert_message(event.message)
    assert db.conn.count("contacts") == contacts_before
    rows = db.conn.rows("SELECT signal_uuid, is_placeholder FROM signal.contacts "
                        "WHERE phone_number = ?", (SELF_NUMBER,))
    assert len(rows) == 1
    assert rows[0]["signal_uuid"] == SELF_UUID      # promoted, not duplicated
    assert not rows[0]["is_placeholder"]


def test_a_locally_generated_timestamp_would_duplicate(db):
    """The failure this correction prevents, demonstrated rather than described.

    Storing a LOCAL timestamp (what the draft proposal's send path would have
    done) and then receiving the echo produces TWO rows. This is the shape of the
    bug, made visible — an invariant guard on the alternative design.
    """
    draft = _approved_draft(db)
    db.send_approved(draft["id"], transmit=_transmit_returning(LOCAL_TS))
    event = consumer.parse_envelope(_echo_envelope(timestamp=SERVER_TS))
    db.upsert_message(event.message)
    assert db.conn.count("messages") == 2


def test_send_refuses_a_non_positive_server_timestamp(db):
    """A zero/negative timestamp would silently collide with the draft range."""
    draft = _approved_draft(db)
    with pytest.raises(ValueError) as exc:
        db.send_approved(draft["id"], transmit=_transmit_returning(0))
    assert "sync-echo dedupe" in str(exc.value)


def test_draft_provisional_timestamp_cannot_be_positive(db):
    with pytest.raises(ValueError) as exc:
        db.draft_message(recipient=PEER_NUMBER, body="nope",
                         self_number=SELF_NUMBER, provisional_timestamp=SERVER_TS)
    assert "NEGATIVE" in str(exc.value)


def test_two_pending_drafts_do_not_collide(db):
    """Distinct provisional timestamps, or the second draft would be swallowed."""
    a = db.draft_message(recipient=PEER_NUMBER, body="first",
                         self_number=SELF_NUMBER, provisional_timestamp=-11)
    b = db.draft_message(recipient=PEER_NUMBER, body="second",
                         self_number=SELF_NUMBER, provisional_timestamp=-12)
    assert a["id"] != b["id"]
    assert db.conn.count("messages") == 2


def test_echo_of_a_message_sent_from_another_device_is_stored_once(db):
    """No draft involved at all: Zach types on his phone, the echo arrives twice."""
    env = _echo_envelope(timestamp=1723000004242, body="typed on the phone")
    first = db.upsert_message(consumer.parse_envelope(env).message)
    second = db.upsert_message(consumer.parse_envelope(env).message)
    assert first == second
    assert db.conn.count("messages") == 1
    row = db.conn.rows("SELECT is_outbound, body FROM signal.messages")[0]
    assert row["is_outbound"]
    assert row["body"] == "typed on the phone"


def test_the_echo_then_draft_ORDER_also_resolves_to_one_contact(db):
    """🔴 The ordering production actually takes, from day two.

    The account's real uuid is already in `contacts` (any earlier sync echo put
    it there). Then an agent drafts, supplying only the NUMBER. Without a lookup
    by phone that mints a SECOND placeholder contact — and promotion correctly
    declines, because the uuid is taken — so the send and the echo carry
    different `source_contact_id`s and `unique_message` never fires. Every
    agent-sent message would be stored twice.

    The earlier test starts from an EMPTY contacts table, which is why it passed
    while this case was broken.
    """
    # Day one: the account's own uuid is already known.
    seed = _echo_envelope(timestamp=1723000003030, body="sent from the phone")
    db.upsert_message(consumer.parse_envelope(seed).message)
    contacts_after_seed = db.conn.count("contacts")
    assert contacts_after_seed == 2                  # the account and the peer

    # Day two: an agent drafts, knowing only the number.
    draft = _approved_draft(db, body="drafted on day two")
    assert db.conn.count("contacts") == contacts_after_seed   # NO rival identity
    db.send_approved(draft["id"], transmit=_transmit_returning(SERVER_TS))

    # And the echo of that send collapses onto the row it just wrote.
    echo = _echo_envelope(timestamp=SERVER_TS, body="drafted on day two")
    db.upsert_message(consumer.parse_envelope(echo).message)
    assert db.conn.count("messages") == 2            # the seed and the sent one
    assert db.conn.count("contacts") == contacts_after_seed


def test_contact_lookup_by_phone_prefers_a_real_contact_over_a_placeholder(db):
    """Deterministic, and in the right direction — an arbitrary pick would split."""
    placeholder = db.upsert_contact(phone_number="+15557654321")
    real = db.upsert_contact(signal_uuid="70707070-7070-4707-8707-707070707070",
                             phone_number="+15557654321")
    # The placeholder is PROMOTED rather than duplicated, so these are one row.
    assert placeholder == real
    assert db.contact_id_by_phone("+15557654321") == real


def test_contact_lookup_is_deterministic_when_two_rows_share_a_number(db):
    """Two rows CAN share a number, and the pick must not be arbitrary.

    Reachable without contrivance: a real contact holds the number, then a second
    real uuid arrives carrying the same number (a re-registration). Promotion
    correctly declines — the first row is not a placeholder — so two rows exist.
    An unordered `LIMIT 1` would then split the identity differently run to run.
    """
    first = db.upsert_contact(signal_uuid="60606060-6060-4606-8606-606060606060",
                              phone_number="+15551239876")
    second = db.upsert_contact(signal_uuid="61616161-6161-4616-8616-616161616161",
                               phone_number="+15551239876")
    assert first != second                      # genuinely two rows
    assert db.conn.count("contacts") == 2
    for _ in range(5):
        assert db.contact_id_by_phone("+15551239876") == first   # stable, oldest


def test_contact_lookup_by_phone_returns_none_for_an_unknown_number(db):
    assert db.contact_id_by_phone("+15550000000") is None
    assert db.contact_id_by_phone("") is None


def test_raw_envelope_of_the_echo_is_retained(db):
    env = _echo_envelope(timestamp=1723000004343)
    db.upsert_message(consumer.parse_envelope(env).message)
    stored = db.conn.rows("SELECT raw_envelope FROM signal.messages")[0]
    assert json.loads(stored["raw_envelope"])["timestamp"] == 1723000004343
