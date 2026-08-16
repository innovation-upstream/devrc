"""Full-text search — what is actually verified here, and what is NOT.

🔴 SCOPE, stated up front so no assertion below is read for more than it measured.
The substrate is sqlite, which has no `tsvector`. `search @@
websearch_to_tsquery('english', %s)` is rewritten by `fakepg` to a
`pg_websearch_match(body, ?)` shim doing AND-of-terms substring matching. So:

* VERIFIED here — the query targets the generated `search` column via
  `websearch_to_tsquery`, the user's text is a BOUND PARAMETER (never
  interpolated), the join/order/limit are right, and matching rows come back
  while non-matching ones do not.
* NOT verified here — Postgres stemming ("meeting" matching "meetings"),
  ranking, or the GIN index being chosen. Those need the live database
  (deployment step 7) and no test in this repo claims them.

Every zero below is preceded by a positive control on the same query path.
"""
import consumer

ALICE = "11111111-1111-4111-8111-111111111111"
BOB = "22222222-2222-4222-8222-222222222222"


def _seed(db):
    db.upsert_contact(signal_uuid=ALICE, display_name="Alice Adler",
                      phone_number="+15550101")
    db.upsert_contact(signal_uuid=BOB, display_name="Bo Brennan",
                      phone_number="+15550202")
    rows = [
        (ALICE, 1723300000001, "the harbour permit expires in march"),
        (ALICE, 1723300000002, "bring the projector to the workshop"),
        (BOB, 1723300000003, "workshop moved to the annex"),
        (BOB, 1723300000004, "nothing to do with any of that"),
    ]
    for uuid, ts, body in rows:
        db.upsert_message({"message_timestamp": ts, "source_uuid": uuid,
                           "message_type": consumer.KIND_MESSAGE, "body": body})
    return rows


# --------------------------------------------------------------------------- #
# Instrument validation: the query path CAN return non-zero, and CAN return zero.
# --------------------------------------------------------------------------- #
def test_positive_control_a_term_that_must_match_returns_rows(db):
    _seed(db)
    hits = db.search("projector")
    assert len(hits) == 1
    assert hits[0]["body"] == "bring the projector to the workshop"


def test_a_term_present_in_no_message_returns_zero(db):
    """Meaningful only because the control above moved the same counter."""
    _seed(db)
    assert db.search("submarine") == []


# --------------------------------------------------------------------------- #
# Behaviour
# --------------------------------------------------------------------------- #
def test_search_returns_every_matching_row(db):
    _seed(db)
    hits = db.search("workshop")
    assert len(hits) == 2
    assert {h["message_timestamp"] for h in hits} == {1723300000002, 1723300000003}


def test_search_orders_newest_first(db):
    _seed(db)
    hits = db.search("workshop")
    stamps = [h["message_timestamp"] for h in hits]
    assert stamps == sorted(stamps, reverse=True)


def test_search_respects_its_limit(db):
    _seed(db)
    assert len(db.search("workshop", limit=1)) == 1


def test_search_joins_the_sender_identity(db):
    _seed(db)
    hit = db.search("harbour")[0]
    assert hit["display_name"] == "Alice Adler"
    assert hit["phone_number"] == "+15550101"


def test_search_finds_outbound_messages_too(db):
    """A sent message must be searchable, or half the conversation is invisible."""
    _seed(db)
    draft = db.draft_message(recipient="+15550101", body="I filed the harbour permit",
                             self_number="+15559090")
    db.approve_draft(draft["id"], approval_ref="cg-search")
    db.send_approved(draft["id"],
                     transmit=lambda auth, recipient, body: {"timestamp": 1723300009999})
    bodies = [h["body"] for h in db.search("permit")]
    assert "I filed the harbour permit" in bodies
    assert any(h["is_outbound"] for h in db.search("permit"))


# --------------------------------------------------------------------------- #
# The SQL itself — the parts sqlite cannot speak for
# --------------------------------------------------------------------------- #
def test_search_sql_targets_the_generated_column_via_websearch_to_tsquery(recording):
    db, conn = recording
    db.search("anything at all")
    sql, params = conn.executed[-1]
    assert "m.search @@ websearch_to_tsquery('english', %s)" in sql
    assert "FROM signal.messages m" in sql


def test_search_binds_the_query_text_rather_than_interpolating_it(recording):
    """The injection guard. A f-string here would put user text in the SQL."""
    db, conn = recording
    nasty = "'); DROP TABLE signal.messages; --"
    db.search(nasty, limit=7)
    sql, params = conn.executed[-1]
    assert nasty not in sql
    assert params == (nasty, 7)


def test_list_conversations_groups_by_conversation_and_orders_by_recency(db):
    _seed(db)
    convs = db.list_conversations()
    assert len(convs) == 2                         # two DM peers, no groups
    stamps = [c["last_message_timestamp"] for c in convs]
    assert stamps == sorted(stamps, reverse=True)
    by_name = {c["display_name"]: c for c in convs}
    assert by_name["Alice Adler"]["message_count"] == 2
    assert by_name["Bo Brennan"]["message_count"] == 2


def test_list_conversations_separates_a_group_from_its_members_dms(db):
    _seed(db)
    db.upsert_message({"message_timestamp": 1723300000005, "source_uuid": ALICE,
                       "message_type": consumer.KIND_GROUP_MESSAGE,
                       "body": "in the group now",
                       "group_id": b"group-annex", "group_name": "Annex Crew"})
    convs = db.list_conversations()
    assert len(convs) == 3
    assert any(c["group_name"] == "Annex Crew" for c in convs)


def test_list_conversations_respects_its_limit(db):
    _seed(db)
    assert len(db.list_conversations(limit=1)) == 1
