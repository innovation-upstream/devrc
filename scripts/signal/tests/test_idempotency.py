"""🔧 #1 + 🔧 #2 — SSE redelivery must be a no-op, including for unknown senders.

The SSE stream redelivers on every reconnect, so "the same envelope twice" is the
NORMAL case, not an edge case. Three claims, each behavioural against the sqlite
substrate rather than asserted about the SQL text:

* the same envelope twice → ONE message row;
* an envelope from a sender the API has NOT resolved to a uuid → still ONE row
  (the NULL-contact path: `UNIQUE` does not dedupe over NULL, so an unresolved
  sender must get a deterministic PLACEHOLDER contact, never a NULL);
* a redelivered message → ONE attachment row per attachment.

🔴 Every "exactly one" claim below is preceded by a POSITIVE CONTROL that makes
the same counter move — a bare 1 is indistinguishable from a harness wired to
nothing.
"""
import consumer
import _signal_db


def _dm(*, ts, uuid=None, number=None, body="fixture body", attachments=()):
    return {
        "message_timestamp": ts,
        "source_uuid": uuid,
        "source_number": number,
        "message_type": consumer.KIND_MESSAGE,
        "body": body,
        "attachments": list(attachments),
    }


# --------------------------------------------------------------------------- #
# Positive controls first: the counters CAN move.
# --------------------------------------------------------------------------- #
def test_positive_control_two_distinct_envelopes_make_two_rows(db):
    db.upsert_message(_dm(ts=1723100000001, uuid="a1111111-0000-4000-8000-000000000001"))
    db.upsert_message(_dm(ts=1723100000002, uuid="a1111111-0000-4000-8000-000000000001"))
    assert db.conn.count("messages") == 2


def test_positive_control_two_distinct_attachments_make_two_rows(db):
    mid = db.upsert_message(_dm(
        ts=1723100000003, uuid="a2222222-0000-4000-8000-000000000002",
        attachments=[
            {"id": "att-alpha", "content_type": "image/png", "filename": "alpha.png"},
            {"id": "att-beta", "content_type": "image/gif", "filename": "beta.gif"},
        ]))
    assert mid
    assert db.conn.count("attachments") == 2


# --------------------------------------------------------------------------- #
# The claims
# --------------------------------------------------------------------------- #
def test_same_envelope_twice_yields_one_row(db):
    msg = _dm(ts=1723100000011, uuid="b1111111-0000-4000-8000-000000000011",
              body="redelivered exactly once")
    first = db.upsert_message(dict(msg))
    second = db.upsert_message(dict(msg))
    assert first == second
    assert db.conn.count("messages") == 1


def test_unresolved_sender_still_dedupes_the_NULL_contact_path(db):
    """🔧 #1 — the case the original DDL got wrong.

    The envelope carries only a phone number. If the sender were stored as NULL,
    `unique_message` would not fire (NULLs compare distinct) and every
    redelivery would insert another copy. The placeholder contact is what closes
    it — and it must be DETERMINISTIC, or the same hole reopens under a
    different name.
    """
    msg = _dm(ts=1723100000021, number="+15557000021", body="from an unknown sender")
    db.upsert_message(dict(msg))
    db.upsert_message(dict(msg))
    assert db.conn.count("messages") == 1
    assert db.conn.count("contacts") == 1
    rows = db.conn.rows("SELECT source_contact_id FROM signal.messages")
    assert rows[0]["source_contact_id"] is not None


def test_placeholder_uuid_is_deterministic_and_identifier_specific():
    a = _signal_db.placeholder_uuid("+15557000021")
    again = _signal_db.placeholder_uuid("+15557000021")
    other = _signal_db.placeholder_uuid("+15557000022")
    assert a == again          # deterministic → the redelivery finds the same row
    assert a != other          # ... but not a single bucket for every stranger


def test_unresolved_sender_and_a_known_sender_do_not_collide(db):
    db.upsert_message(_dm(ts=1723100000031, number="+15557000031", body="stranger"))
    db.upsert_message(_dm(ts=1723100000031,
                          uuid="c1111111-0000-4000-8000-000000000031", body="known"))
    assert db.conn.count("messages") == 2      # same timestamp, different senders


def test_redelivered_attachment_yields_one_attachment_row(db):
    """🔧 #2 — without `unique_attachment` this doubles on every reconnect."""
    msg = _dm(ts=1723100000041, uuid="d1111111-0000-4000-8000-000000000041",
              attachments=[{"id": "att-redelivered", "content_type": "image/heic",
                            "filename": "photo.heic", "size": 4096}])
    db.upsert_message(dict(msg))
    db.upsert_message(dict(msg))
    assert db.conn.count("messages") == 1
    assert db.conn.count("attachments") == 1


def test_attachment_rows_carry_their_signal_id_and_metadata(db):
    db.upsert_message(_dm(
        ts=1723100000051, uuid="e1111111-0000-4000-8000-000000000051",
        attachments=[{"id": "att-meta-51", "content_type": "application/pdf",
                      "filename": "statement.pdf", "size": 91733,
                      "caption": "march", "is_voice_note": False}]))
    row = db.conn.rows("SELECT * FROM signal.attachments")[0]
    assert row["signal_attachment_id"] == "att-meta-51"
    assert row["content_type"] == "application/pdf"
    assert row["filename"] == "statement.pdf"
    assert row["size_bytes"] == 91733
    assert row["caption"] == "march"


def test_redelivery_does_not_erase_a_body_we_already_have(db):
    """The conflict UPDATE must not overwrite stored content with a thinner copy."""
    db.upsert_message(_dm(ts=1723100000061,
                          uuid="f1111111-0000-4000-8000-000000000061",
                          body="the original text"))
    thin = _dm(ts=1723100000061, uuid="f1111111-0000-4000-8000-000000000061",
               body=None)
    thin["server_delivered_at"] = 1723100000099
    db.upsert_message(thin)
    row = db.conn.rows("SELECT body, server_delivered_at FROM signal.messages")[0]
    assert row["body"] == "the original text"
    assert row["server_delivered_at"] == 1723100000099   # ... but late facts land


def test_contact_upsert_enriches_rather_than_blanks(db):
    uid = "01111111-0000-4000-8000-000000000071"
    first = db.upsert_contact(signal_uuid=uid, display_name="Nadia Okoro")
    second = db.upsert_contact(signal_uuid=uid, phone_number="+15557000071")
    assert first == second
    row = db.conn.rows("SELECT * FROM signal.contacts")[0]
    assert row["display_name"] == "Nadia Okoro"     # not blanked by the 2nd upsert
    assert row["phone_number"] == "+15557000071"    # ... and the new fact landed
