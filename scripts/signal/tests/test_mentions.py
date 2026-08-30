"""🔴 Mentions on outbound Signal messages — offsets, refusals, and the binding.

THREE THINGS ARE UNDER TEST, and they fail in three different ways:

1. **UTF-16 OFFSETS.** `start`/`length` are UTF-16 code units. Every assertion
   here that involves an emoji exists because a `len()`-in-code-points
   implementation passes the ASCII cases and is silently wrong the moment a
   non-BMP character appears earlier in the body — the receiving client then
   replaces the WRONG span of text. `test_an_emoji_before_the_mention_shifts_…`
   is the whole point of this file; the ASCII cases cannot see that defect.

2. **THE REFUSAL MATRIX.** Six ways a `--mention` can fail to name exactly one
   real member of the target group. Each has its OWN exception class and its own
   test asserting THAT class — not `pytest.raises(ValueError)`, which every
   other refusal would also satisfy. Refusing rather than dropping is the
   product decision: a mention notifies a third party through their mute
   settings, so sending fewer than were asked for is a different act from the
   one the operator approved.

3. **THE CAPABILITY BINDING.** Until this change the send capability authorised
   "a transmit for draft N" and nothing about the message; `auth.recipient` and
   `auth.body` were populated at mint and read by NOBODY. Anything that wrote to
   the row between approval and the POST — a concurrent writer, a second agent,
   a bug — sent text a human never saw under an approval a human really gave.
   `test_a_draft_mutated_between_approve_and_send_is_REFUSED` is the regression
   test for that; it fails on pre-change code.

Hermetic like its neighbours: the sqlite substrate, an injected group-membership
fetcher, an injected poster. Nothing here touches Postgres, MinIO or the network,
and NO test in this file transmits a real Signal message.
"""
import base64
import json
import types
from pathlib import Path

import pytest

import clawgate
import consumer
import _mentions
import _signal_db
from _mentions import (
    MentionNameAmbiguous,
    MentionNameNotFound,
    MentionNotAMember,
    MentionResolvesToPlaceholder,
    MentionSpanMissing,
    MentionsRequireAGroup,
)
from _signal_db import SendGateError

SELF_NUMBER = "+15559090"
PEER = "+15550101"

# A real group `id` (the double-base64 `--to` form), built through the module's
# own encoder so this fixture cannot disagree with what `draft_message` decodes.
GROUP_RAW = b"\x51" * 32
GROUP_ADDRESS = _signal_db._group_id_to_address(GROUP_RAW)

# The measured live shape: members[] is MIXED — E.164 and bare UUID. Five of the
# seven members of the real 'Vetr app group' are uuid-only, so a fixture where
# everyone has a phone number would not exercise the join that matters.
ANN_UUID = "11111111-1111-4111-8111-111111111111"
BOB_UUID = "22222222-2222-4222-8222-222222222222"
CAI_UUID = "33333333-3333-4333-8333-333333333333"
DEE_NUMBER = "+15550444"
OUTSIDER_UUID = "99999999-9999-4999-8999-999999999999"

MEMBERS = [ANN_UUID, BOB_UUID, CAI_UUID, DEE_NUMBER]

CONTACTS = [
    {"signal_uuid": ANN_UUID, "phone_number": None, "display_name": "Ann",
     "profile_name": None, "is_placeholder": False},
    {"signal_uuid": BOB_UUID, "phone_number": None, "display_name": "Bob",
     "profile_name": None, "is_placeholder": False},
    # 🔴 Cai shares Bob's DISPLAY NAME. Ambiguity is not a hypothetical: two
    # people in one group answering to "Bob" is ordinary, and picking either one
    # silently is how a message pings a stranger.
    {"signal_uuid": CAI_UUID, "phone_number": None, "display_name": "Bob",
     "profile_name": None, "is_placeholder": False},
    # 🔴 Dee is a PLACEHOLDER — a synthetic identity minted by
    # `placeholder_uuid()` for a sender the pipeline could not identify.
    {"signal_uuid": _signal_db.placeholder_uuid(DEE_NUMBER),
     "phone_number": DEE_NUMBER, "display_name": "Dee",
     "profile_name": None, "is_placeholder": True},
]


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
    """Records every POST. Must stay EMPTY whenever the gate refuses."""

    def __init__(self, ts=1723500000777):
        self.calls = []
        self._ts = ts

    def __call__(self, url, json=None, timeout=None):
        self.calls.append({"url": url, "json": json})
        return types.SimpleNamespace(raise_for_status=lambda: None,
                                     json=lambda: {"timestamp": str(self._ts)})


def _resolve(identifiers, body, *, members=MEMBERS, contacts=CONTACTS,
             is_group=True):
    return _mentions.resolve_mentions(identifiers, body=body, members=members,
                                      contacts=contacts, is_group=is_group)


# --------------------------------------------------------------------------- #
# 1. UTF-16 OFFSETS
# --------------------------------------------------------------------------- #
def test_utf16_len_counts_code_UNITS_not_code_POINTS():
    """The primitive, pinned at three points — BMP, non-BMP, and mixed.

    An INVARIANT GUARD, not a regression test: nothing shipped that got this
    wrong, because nothing computed it at all. It is here so the two spellings
    that DO differ are stated as literals rather than derived from `len()`.
    """
    assert _mentions.utf16_len("hello") == 5           # ASCII: units == points
    assert _mentions.utf16_len("é") == 1               # BMP: still one unit
    assert _mentions.utf16_len("\U0001F415") == 2      # 🐕 non-BMP: TWO units
    assert len("\U0001F415") == 1                      # ... but ONE code point
    assert _mentions.utf16_len("a\U0001F415b") == 4


def test_a_mention_at_the_start_of_the_body_spans_from_zero():
    out = _resolve(["Ann"], "@Ann can you look at this?")
    assert out == [{"author": ANN_UUID, "start": 0, "length": 4}]


def test_a_mention_that_is_NOT_at_index_zero_carries_its_real_offset():
    """A resolver that always emitted 0 would pass the case above."""
    body = "hey @Ann can you look"
    out = _resolve(["Ann"], body)
    assert out == [{"author": ANN_UUID, "start": 4, "length": 4}]
    assert body[4:8] == "@Ann"


def test_an_emoji_before_the_mention_shifts_start_by_TWO_not_one():
    """🔴 THE CASE THIS WHOLE FILE EXISTS FOR.

    `"🐕 @Ann"`. In Python code points `@Ann` starts at index 2. In UTF-16 code
    units — which is what the wire format specifies — it starts at 3, because the
    dog is a non-BMP character and occupies TWO units. A naive `body.find()`
    passes every ASCII test in this file and emits 2 here, and the receiving
    client then replaces one character too few: the message renders as
    `🐕@AnnAnn`-shaped garbage with the ping pointing at the wrong text.

    The two numbers are written as LITERALS, and their difference asserted
    explicitly, so a `len()`-based implementation cannot make this test agree
    with itself.
    """
    body = "\U0001F415 @Ann look at this"
    out = _resolve(["Ann"], body)
    assert out == [{"author": ANN_UUID, "start": 3, "length": 4}]
    assert body.find("@Ann") == 2, "the CODE POINT index — deliberately different"
    assert out[0]["start"] == body.find("@Ann") + 1


def test_an_emoji_INSIDE_the_mention_span_lengthens_it_too():
    """The same defect on `length` rather than on `start`.

    A body whose mention text itself contains a non-BMP character: `length` is
    UTF-16 units of the needle, so a `len()` implementation under-counts here
    even when `start` happens to be right.
    """
    emoji_named = [{"signal_uuid": ANN_UUID, "phone_number": None,
                    "display_name": "Ann\U0001F415", "profile_name": None,
                    "is_placeholder": False}]
    body = "ping @Ann\U0001F415 now"
    out = _resolve(["Ann\U0001F415"], body, contacts=emoji_named)
    assert out == [{"author": ANN_UUID, "start": 5, "length": 6}]
    assert len("@Ann\U0001F415") == 5, "FIVE code points, SIX utf-16 units"


def test_two_mentions_in_one_body_get_distinct_spans_in_order():
    body = "@Ann and @Bob please review"
    out = _resolve(["Ann", "Bob"], body,
                   contacts=[c for c in CONTACTS if c["display_name"] != "Bob"
                             or c["signal_uuid"] == BOB_UUID])
    assert out == [
        {"author": ANN_UUID, "start": 0, "length": 4},
        {"author": BOB_UUID, "start": 9, "length": 4},
    ]


def test_two_mentions_with_an_emoji_between_them_both_shift():
    """Both offsets, and the SECOND one is where a units bug compounds."""
    body = "@Ann \U0001F415\U0001F415 @Bob"
    out = _resolve(["Ann", "Bob"], body,
                   contacts=[c for c in CONTACTS if c["display_name"] != "Bob"
                             or c["signal_uuid"] == BOB_UUID])
    assert out == [
        {"author": ANN_UUID, "start": 0, "length": 4},
        # 4 for "@Ann", 1 space, 2+2 for the dogs, 1 space -> 10
        {"author": BOB_UUID, "start": 10, "length": 4},
    ]
    assert body.find("@Bob") == 8, "code points again — two behind the truth"


def test_the_same_name_mentioned_twice_takes_successive_occurrences():
    """Both mentions pointing at the SAME span would ping once and mis-render."""
    body = "@Ann and again @Ann"
    out = _resolve(["Ann", "Ann"], body)
    assert [(m["start"], m["length"]) for m in out] == [(0, 4), (15, 4)]


# --------------------------------------------------------------------------- #
# 2. THE REFUSAL MATRIX — one test per distinct error
# --------------------------------------------------------------------------- #
def test_refusal_name_not_found():
    with pytest.raises(MentionNameNotFound) as exc:
        _resolve(["Zoe"], "hello @Zoe")
    assert "no member of the target group is named 'Zoe'" in str(exc.value)


def test_refusal_ambiguous_name():
    """Two members answer to 'Bob'; picking one silently pings a stranger."""
    with pytest.raises(MentionNameAmbiguous) as exc:
        _resolve(["Bob"], "hello @Bob")
    message = str(exc.value)
    assert "AMBIGUOUS" in message
    assert "matches 2 members" in message


def test_refusal_placeholder_contact():
    """A `placeholder_uuid()` identity is SYNTHETIC — it addresses nobody."""
    with pytest.raises(MentionResolvesToPlaceholder) as exc:
        _resolve(["Dee"], "hello @Dee")
    assert "PLACEHOLDER" in str(exc.value)


def test_refusal_placeholder_reached_by_its_uuid_too():
    """The same refusal on the DIRECT-identifier branch, not just the name one.

    A guard on the name path alone would be walked by typing the synthetic uuid.
    """
    fake = _signal_db.placeholder_uuid(DEE_NUMBER)
    with pytest.raises(MentionResolvesToPlaceholder):
        _resolve([fake], f"hello @{fake}",
                 members=MEMBERS + [fake])


def test_refusal_identifier_is_not_a_member_of_this_group():
    with pytest.raises(MentionNotAMember) as exc:
        _resolve([OUTSIDER_UUID], f"hello @{OUTSIDER_UUID}")
    assert "NOT a member of the target group" in str(exc.value)


def test_refusal_the_at_substring_is_absent_from_the_body():
    """A resolvable name, but the body never says `@Ann` — nothing to replace."""
    with pytest.raises(MentionSpanMissing) as exc:
        _resolve(["Ann"], "hello Ann, no at-sign here")
    assert "does not contain '@Ann'" in str(exc.value)


def test_refusal_mentions_for_a_non_group_recipient():
    with pytest.raises(MentionsRequireAGroup) as exc:
        _resolve(["Ann"], "hi @Ann", is_group=False)
    assert "mentions are a GROUP feature" in str(exc.value)


def test_refusal_an_empty_membership_is_a_refusal_not_an_empty_array():
    """🔴 A silent zero. An empty `members[]` means the LOOKUP failed."""
    with pytest.raises(MentionNameNotFound) as exc:
        _resolve(["Ann"], "hi @Ann", members=[])
    assert "reported NO members" in str(exc.value)


def test_a_name_that_matches_someone_OUTSIDE_the_group_does_not_resolve():
    """Candidacy is scoped to MEMBERSHIP, not to the whole contacts table."""
    outsider = {"signal_uuid": OUTSIDER_UUID, "phone_number": None,
                "display_name": "Zoe", "profile_name": None,
                "is_placeholder": False}
    with pytest.raises(MentionNameNotFound):
        _resolve(["Zoe"], "hi @Zoe", contacts=CONTACTS + [outsider])


def test_no_mentions_asked_for_is_an_empty_list_even_for_a_non_group():
    """The refusal fires only when mentions were ACTUALLY requested."""
    assert _resolve([], "a plain message", is_group=False) == []
    assert _resolve(None, "a plain message", is_group=False) == []


def test_an_explicit_member_uuid_and_E164_both_resolve_to_themselves():
    """`author` accepts either form; both must survive as typed."""
    assert _resolve([ANN_UUID], f"hi @{ANN_UUID}")[0]["author"] == ANN_UUID
    ok = [c for c in CONTACTS if not c["is_placeholder"]] + [
        {"signal_uuid": None, "phone_number": DEE_NUMBER, "display_name": "Dee2",
         "profile_name": None, "is_placeholder": False}]
    assert _resolve([DEE_NUMBER], f"hi @{DEE_NUMBER}",
                    contacts=ok)[0]["author"] == DEE_NUMBER


def test_a_profile_name_resolves_as_well_as_a_display_name():
    contacts = [{"signal_uuid": ANN_UUID, "phone_number": None,
                 "display_name": None, "profile_name": "Annie",
                 "is_placeholder": False}]
    assert _resolve(["Annie"], "hi @Annie",
                    contacts=contacts)[0]["author"] == ANN_UUID


# --------------------------------------------------------------------------- #
# 3. PERSISTENCE AND THE MIGRATION
# --------------------------------------------------------------------------- #
MENTIONS_FIXTURE = [{"author": ANN_UUID, "start": 0, "length": 4}]


def _group_draft(db, body="@Ann please look", mentions=None):
    return db.draft_message(recipient=GROUP_ADDRESS, body=body,
                            self_number=SELF_NUMBER,
                            mentions=MENTIONS_FIXTURE if mentions is None
                            else mentions)


def test_ensure_schema_twice_is_a_no_op_ON_THE_SQLITE_SUBSTRATE(db):
    """The migration is idempotent — **as emulated by `fakepg`**, not on Postgres.

    🔴 RENAMED, BECAUSE THE OLD NAME CLAIMED COVERAGE THIS DOES NOT HAVE. The
    hermetic substrate is SQLite, which has no `ADD COLUMN IF NOT EXISTS`;
    `fakepg` *emulates* the clause. So what runs here asserts that the emulation
    is idempotent, and would stay green against a real-Postgres spelling error
    the emulation happens to forgive. The real check is
    `test_pg_type_compat.py::test_ensure_schema_is_idempotent_on_real_postgres`,
    which executes the shipped DDL against a live server and SKIPS (loudly,
    rather than passing vacuously) without `SIGNAL_PG_DSN`.

    What this test DOES still cover, and it is worth keeping: `ensure_schema()`
    runs on every consumer start, and a second call must not raise or destroy the
    column's contents — the restart is precisely when it runs.
    """
    db.ensure_schema()
    db.ensure_schema()
    draft = _group_draft(db)
    assert db.get_draft(draft["id"])["mentions"] == MENTIONS_FIXTURE


def test_the_mentions_column_exists_and_is_declared_in_SCHEMA_STATEMENTS():
    """Derived from the module's own DDL, never restated here."""
    alters = [s for s in _signal_db.SCHEMA_STATEMENTS
              if s.strip().upper().startswith("ALTER TABLE")]
    assert any("ADD COLUMN IF NOT EXISTS mentions" in s for s in alters), alters


def test_a_draft_persists_its_mentions_and_reads_them_back_as_a_LIST(db):
    draft = _group_draft(db)
    stored = db.get_draft(draft["id"])
    assert stored["mentions"] == MENTIONS_FIXTURE
    assert isinstance(stored["mentions"], list)


def test_the_mentions_column_holds_JSON_not_a_postgres_ARRAY(db):
    """psycopg2 adapts a bare `list` to `text[]`, which JSONB rejects at runtime.

    Read from the substrate directly rather than through `get_draft`, whose
    normalisation would hide a wrong storage shape.
    """
    draft = _group_draft(db)
    raw = db.conn.rows("SELECT mentions FROM signal.messages WHERE id = ?",
                       (draft["id"],))[0]["mentions"]
    assert json.loads(raw) == MENTIONS_FIXTURE


def test_a_mention_free_draft_stores_NULL_and_reads_back_as_an_empty_list(db):
    """Indistinguishable from every draft written before the column existed."""
    draft = db.draft_message(recipient=PEER, body="no pings here",
                             self_number=SELF_NUMBER)
    raw = db.conn.rows("SELECT mentions FROM signal.messages WHERE id = ?",
                       (draft["id"],))[0]["mentions"]
    assert raw is None
    assert db.get_draft(draft["id"])["mentions"] == []
    assert draft["mentions"] == []


def test_contacts_by_identifiers_matches_uuids_AND_phone_numbers(db):
    """🔴 members[] is MIXED. Querying one column finds 5 of 7 in the real group."""
    uid = db.upsert_contact(signal_uuid=ANN_UUID, display_name="Ann")
    pid = db.upsert_contact(phone_number="+15550777", display_name="Pat")
    found = db.contacts_by_identifiers([ANN_UUID, "+15550777", OUTSIDER_UUID])
    assert {r["id"] for r in found} == {uid, pid}
    assert db.contacts_by_identifiers([]) == []


# --------------------------------------------------------------------------- #
# 4. THE WIRE PAYLOAD
# --------------------------------------------------------------------------- #
def _send(db, draft, poster):
    db.approve_draft(draft["id"], approval_ref="cg-mentions")
    return db.send_approved(
        draft["id"],
        transmit=lambda a, **kw: consumer.transmit_approved(a, poster=poster, **kw))


def test_the_send_payload_carries_the_mentions_array_EXACTLY(db):
    """Exact equality on the whole body, like its mention-free sibling."""
    draft = _group_draft(db, body="\U0001F415 @Ann look",
                         mentions=[{"author": ANN_UUID, "start": 3, "length": 4}])
    poster = Poster()
    _send(db, draft, poster)
    assert poster.calls[0]["json"] == {
        "message": "\U0001F415 @Ann look",
        "number": SELF_NUMBER,
        "recipients": [GROUP_ADDRESS],
        "mentions": [{"author": ANN_UUID, "start": 3, "length": 4}],
    }


def test_each_wire_mention_has_EXACTLY_author_start_length(db):
    """A fourth key is dropped silently by the server; a missing one mis-renders."""
    draft = _group_draft(db)
    poster = Poster()
    _send(db, draft, poster)
    for mention in poster.calls[0]["json"]["mentions"]:
        assert tuple(mention) == _mentions.MENTION_KEYS
        assert isinstance(mention["author"], str)
        assert isinstance(mention["start"], int)
        assert isinstance(mention["length"], int)


def test_a_stray_key_on_a_stored_mention_never_reaches_the_wire(db):
    """The payload is REBUILT from the three fields, not forwarded verbatim."""
    draft = _group_draft(
        db, mentions=[{"author": ANN_UUID, "start": 0, "length": 4,
                       "note": "not part of the wire contract"}])
    poster = Poster()
    _send(db, draft, poster)
    assert poster.calls[0]["json"]["mentions"] == [
        {"author": ANN_UUID, "start": 0, "length": 4}]


def test_a_mention_free_send_carries_NO_mentions_key_at_all(db):
    """The pre-existing request shape, byte for byte. Nothing new goes on the wire."""
    draft = db.draft_message(recipient=PEER, body="plain", self_number=SELF_NUMBER)
    poster = Poster()
    _send(db, draft, poster)
    assert poster.calls[0]["json"] == {
        "message": "plain", "number": SELF_NUMBER, "recipients": [PEER]}
    assert "mentions" not in poster.calls[0]["json"]


# --------------------------------------------------------------------------- #
# 5. THE CAPABILITY BINDING — approve, mutate, send
#
# 🔴 TWO DISTINCT WINDOWS, and only one of them is the one that matters in
# practice. Both are tested, and they are guarded in different places:
#
#   approve -> [WINDOW A] -> send        closed by the APPROVAL DIGEST, checked
#                                        in `_mint_send_authorization()`
#   mint    -> [WINDOW B] -> POST        closed by the PAYLOAD BINDING, checked
#                                        in `spend_authorization()`
#
# Window A is the real one: `approve` and `send` are separate CLI invocations,
# minutes or hours apart, and in between the draft is an ordinary row anything
# with the connection can rewrite. A capability minted at SEND time out of
# whatever the row says THEN cannot detect that — it would be comparing the row
# against itself. That is exactly why the pre-change `auth.recipient` and
# `auth.body` fields were dead code rather than a guard, and why the fix records
# a digest at APPROVAL rather than binding harder at send.
# --------------------------------------------------------------------------- #
def _tamper(db, draft_id, **columns):
    """Rewrite a draft row directly, as a second writer would."""
    for column, value in columns.items():
        with db.conn.cursor() as cur:
            cur.execute(f"UPDATE signal.messages SET {column} = %s WHERE id = %s",
                        (value, draft_id))
    db.conn.commit()


def test_a_draft_mutated_between_approve_and_send_is_REFUSED(db):
    """🔴 THE REGRESSION TEST. RED on origin/main — measured, not assumed.

    On origin/main this exact sequence transmits: `send_approved()` re-reads the
    row, mints a capability out of the values it finds, and posts them. The
    approval was recorded against different text, and nothing compares the two.
    The witness run on origin/main posted the mutated body under the operator's
    approval ref.

    WINDOW A. The mutation happens where a real one would — after the human
    approved and before they (or a scheduler) ran `send` — with no monkeypatching
    of the code under test.
    """
    draft = _group_draft(db, body="@Ann can you review the small one",
                         mentions=MENTIONS_FIXTURE)
    db.approve_draft(draft["id"], approval_ref="cg-mutate")
    _tamper(db, draft["id"], body="@Ann approve the WIRE TRANSFER")

    poster = Poster()
    with pytest.raises(SendGateError) as exc:
        db.send_approved(
            draft["id"],
            transmit=lambda a, **kw: consumer.transmit_approved(a, poster=poster,
                                                                **kw))
    message = str(exc.value)
    assert "CHANGED after it was approved" in message
    assert "approve/mutate/send binding" in message
    assert poster.calls == [], "the tampered body REACHED THE NETWORK"
    # ... and the draft is left where the operator can see and re-approve it.
    assert db.get_draft(draft["id"])["send_state"] == _signal_db.STATE_APPROVED


def test_a_RE_POINTED_mention_after_approval_is_refused(db):
    """The same window, on the field that notifies third parties.

    Body and recipient unchanged; only the ping moves, from Ann to Bob. A binding
    that covered recipient and body alone would let this through — and
    re-pointing a mention is how an approved message notifies someone who was
    never on the card the human read.
    """
    draft = _group_draft(db, body="@Ann please look", mentions=MENTIONS_FIXTURE)
    db.approve_draft(draft["id"], approval_ref="cg-repoint")
    _tamper(db, draft["id"],
            mentions=json.dumps([{"author": BOB_UUID, "start": 0, "length": 4}]))

    poster = Poster()
    with pytest.raises(SendGateError) as exc:
        db.send_approved(
            draft["id"],
            transmit=lambda a, **kw: consumer.transmit_approved(a, poster=poster,
                                                                **kw))
    assert "CHANGED after it was approved" in str(exc.value)
    assert poster.calls == []


def test_mentions_ADDED_after_approval_are_refused(db):
    """The nastiest direction: a card that showed NO pings, sent WITH one."""
    draft = db.draft_message(recipient=GROUP_ADDRESS, body="@Ann fyi",
                             self_number=SELF_NUMBER)
    db.approve_draft(draft["id"], approval_ref="cg-added")
    _tamper(db, draft["id"], mentions=json.dumps(MENTIONS_FIXTURE))

    poster = Poster()
    with pytest.raises(SendGateError):
        db.send_approved(
            draft["id"],
            transmit=lambda a, **kw: consumer.transmit_approved(a, poster=poster,
                                                                **kw))
    assert poster.calls == []


def test_a_MOVED_mention_offset_is_refused_even_with_the_same_author(db):
    """Same person, different span — a different message on screen.

    The digest covers start/length, not just `author`: a mention slid onto other
    text renders as a ping on words the operator never approved.
    """
    draft = _group_draft(db, body="hi @Ann and Ann",
                         mentions=[{"author": ANN_UUID, "start": 3, "length": 4}])
    db.approve_draft(draft["id"], approval_ref="cg-moved")
    _tamper(db, draft["id"],
            mentions=json.dumps([{"author": ANN_UUID, "start": 11, "length": 4}]))
    poster = Poster()
    with pytest.raises(SendGateError):
        db.send_approved(
            draft["id"],
            transmit=lambda a, **kw: consumer.transmit_approved(a, poster=poster,
                                                                **kw))
    assert poster.calls == []


def test_a_CLEARED_approval_digest_fails_CLOSED(db):
    """🔴 "No digest" and "digest wiped by the writer we guard against" are the
    SAME observation, so the absent case must refuse, not pass.

    A guard that only fired on a MISMATCH would be walked by any writer that
    clears the column along with the body — one `UPDATE` away.
    """
    draft = _group_draft(db)
    db.approve_draft(draft["id"], approval_ref="cg-cleared")
    _tamper(db, draft["id"], approved_digest=None,
            body="@Ann approve the WIRE TRANSFER")
    poster = Poster()
    with pytest.raises(SendGateError) as exc:
        db.send_approved(
            draft["id"],
            transmit=lambda a, **kw: consumer.transmit_approved(a, poster=poster,
                                                                **kw))
    assert "NO approval digest" in str(exc.value)
    assert poster.calls == []


def test_re_approving_the_mutated_draft_is_the_documented_way_out(db):
    """POSITIVE CONTROL on the refusals above — the operator is not stuck.

    Without this, a binding that refused unconditionally would satisfy every
    test in this section while making the send path inert.
    """
    draft = _group_draft(db)
    db.approve_draft(draft["id"], approval_ref="cg-recover")
    _tamper(db, draft["id"], body="@Ann a genuinely revised message")
    poster = Poster()
    with pytest.raises(SendGateError):
        db.send_approved(draft["id"],
                         transmit=lambda a, **kw: consumer.transmit_approved(
                             a, poster=poster, **kw))
    # The operator reads the new text and approves THAT.
    _reapprove(db, draft["id"], "cg-recover-2")
    sent = db.send_approved(
        draft["id"],
        transmit=lambda a, **kw: consumer.transmit_approved(a, poster=poster,
                                                            **kw))
    assert sent["send_state"] == _signal_db.STATE_SENT
    assert poster.calls[0]["json"]["message"] == "@Ann a genuinely revised message"


def _reapprove(db, draft_id, ref):
    """The operator's recovery, through the PUBLIC API only.

    🔴 THIS USED TO BE `UPDATE signal.messages SET send_state='pending'` — raw
    SQL against the substrate, which meant this "positive control" exercised a
    path NO OPERATOR HAD. `approve_draft()` is pending-only, `reconcile_send()`
    is sending-only, and there was no third subcommand: the state this helper
    manufactured was unreachable from the CLI, so the test proved the operator
    was not stuck by doing something the operator could not do. `unapprove_draft`
    is the real route, and this helper now takes it.
    """
    db.unapprove_draft(draft_id, note=f"withdrawn before {ref}")
    return db.approve_draft(draft_id, approval_ref=ref)


def test_a_payload_mutated_between_MINT_and_POST_is_refused():
    """WINDOW B, at its own level, with no database in the way.

    Distinct from window A and guarded elsewhere: this is the capability itself
    refusing to be spent on a payload it was not minted for, which is what stops
    a caller between `_mint_send_authorization()` and `transmit_approved()` from
    swapping the arguments.
    """
    auth = _signal_db._mint_send_authorization(
        _approved_row(45, PEER, "the approved text"))
    poster = Poster()
    with pytest.raises(SendGateError) as exc:
        consumer.transmit_approved(auth, recipient=PEER,
                                   body="a different text",
                                   number=SELF_NUMBER, poster=poster)
    assert "does not match what was approved" in str(exc.value)
    assert poster.calls == []


def test_MENTIONS_mutated_between_mint_and_post_are_refused():
    """Window B on the mentions array specifically."""
    auth = _signal_db._mint_send_authorization(
        _approved_row(46, PEER, "hi", mentions=MENTIONS_FIXTURE))
    poster = Poster()
    with pytest.raises(SendGateError) as exc:
        consumer.transmit_approved(
            auth, recipient=PEER, body="hi", number=SELF_NUMBER, poster=poster,
            mentions=[{"author": BOB_UUID, "start": 0, "length": 4}])
    assert "mentions" in str(exc.value)
    assert BOB_UUID in str(exc.value)
    assert poster.calls == []


def test_transmitting_a_body_the_capability_was_not_minted_for_is_refused():
    """The binding at its own level, with no database in the way."""
    auth = _signal_db._mint_send_authorization(
        _approved_row(41, PEER, "the approved text"))
    poster = Poster()
    with pytest.raises(SendGateError) as exc:
        consumer.transmit_approved(auth, recipient=PEER, body="a different text",
                                   number=SELF_NUMBER, poster=poster)
    assert "does not match what was approved" in str(exc.value)
    assert poster.calls == []


def test_transmitting_to_a_recipient_the_capability_was_not_minted_for_is_refused():
    auth = _signal_db._mint_send_authorization(
        _approved_row(42, PEER, "hi"))
    poster = Poster()
    with pytest.raises(SendGateError) as exc:
        consumer.transmit_approved(auth, recipient="+15550999", body="hi",
                                   number=SELF_NUMBER, poster=poster)
    assert "recipient" in str(exc.value)
    assert poster.calls == []


def test_a_REFUSED_payload_does_NOT_spend_the_capability():
    """🔴 A mismatch is a refusal, not a consumed attempt.

    Burning the nonce would strand a draft that nothing is wrong with. Proven by
    sending the CORRECT payload afterwards with the same capability and watching
    it succeed — a spent nonce would refuse with the single-use wording instead.
    """
    auth = _signal_db._mint_send_authorization(
        _approved_row(43, PEER, "the approved text"))
    poster = Poster()
    with pytest.raises(SendGateError):
        consumer.transmit_approved(auth, recipient=PEER, body="wrong",
                                   number=SELF_NUMBER, poster=poster)
    consumer.transmit_approved(auth, recipient=PEER, body="the approved text",
                               number=SELF_NUMBER, poster=poster)
    assert len(poster.calls) == 1


def test_the_matching_payload_still_sends_end_to_end(db):
    """POSITIVE CONTROL for every refusal above: the happy path is unbroken.

    Without this, a binding that refused EVERYTHING would satisfy the whole
    section — every mismatch test would pass and the feature would be inert.
    """
    draft = _group_draft(db)
    poster = Poster()
    sent = _send(db, draft, poster)
    assert sent["send_state"] == _signal_db.STATE_SENT
    assert len(poster.calls) == 1
    assert poster.calls[0]["json"]["mentions"] == MENTIONS_FIXTURE


def test_canonical_mentions_treats_none_and_empty_as_the_same_thing():
    assert _mentions.canonical_mentions(None) == ()
    assert _mentions.canonical_mentions([]) == ()
    a = [{"author": ANN_UUID, "start": 0, "length": 4}]
    b = [{"author": BOB_UUID, "start": 0, "length": 4}]
    assert _mentions.canonical_mentions(a) != _mentions.canonical_mentions(b)
    # ORDER matters: two mentions swapped is a different wire message.
    assert _mentions.canonical_mentions(a + b) != _mentions.canonical_mentions(b + a)


def test_spend_authorization_cannot_be_called_without_the_payload():
    """🔴 The seam, closed MECHANICALLY rather than by convention.

    The three binding arguments are required keywords with no defaults, so a
    call site cannot opt out of the check by omitting them — which is exactly
    how the old `spend_authorization(auth)` shape let the payload go unbound.
    """
    auth = _signal_db._mint_send_authorization(
        _approved_row(44, PEER, "x"))
    with pytest.raises(TypeError):
        _signal_db.spend_authorization(auth)


# --------------------------------------------------------------------------- #
# 6. THE RESOLUTION SEAM — CLI -> group API -> contacts -> mentions
# --------------------------------------------------------------------------- #
def test_resolve_draft_mentions_joins_the_group_API_to_the_contacts_table(db):
    """End to end through the seam, with the network injected.

    The three pieces are each tested above IN ISOLATION; this is the case none of
    them can see — that the membership the API returns is the set the contacts
    lookup is scoped to.
    """
    db.upsert_contact(signal_uuid=ANN_UUID, display_name="Ann")
    db.upsert_contact(signal_uuid=BOB_UUID, display_name="Bob")
    calls = []

    def fetcher(number, address):
        calls.append((number, address))
        return [ANN_UUID, BOB_UUID]

    out = consumer.resolve_draft_mentions(
        db, recipient=GROUP_ADDRESS, body="@Ann and @Bob", identifiers=["Bob"],
        number=SELF_NUMBER, member_fetcher=fetcher)
    assert out == [{"author": BOB_UUID, "start": 9, "length": 4}]
    assert calls == [(SELF_NUMBER, GROUP_ADDRESS)]


def test_no_mentions_means_NO_group_api_call_at_all(db):
    """The unchanged path stays unchanged — and stays offline."""
    def fetcher(number, address):  # pragma: no cover - must never run
        raise AssertionError("the group API was called for a mention-free draft")

    assert consumer.resolve_draft_mentions(
        db, recipient=GROUP_ADDRESS, body="hi", identifiers=[],
        number=SELF_NUMBER, member_fetcher=fetcher) == []


def test_a_non_group_recipient_is_refused_WITHOUT_a_membership_fetch(db):
    """The URL would be built out of a phone number; the error must name the truth."""
    def fetcher(number, address):  # pragma: no cover - must never run
        raise AssertionError("a membership fetch was attempted for a person")

    with pytest.raises(MentionsRequireAGroup):
        consumer.resolve_draft_mentions(
            db, recipient=PEER, body="hi @Ann", identifiers=["Ann"],
            number=SELF_NUMBER, member_fetcher=fetcher)


@pytest.mark.parametrize("detail,expected", [
    ({"members": [ANN_UUID, DEE_NUMBER]}, [ANN_UUID, DEE_NUMBER]),
    ([{"members": [ANN_UUID]}], [ANN_UUID]),
    ({"members": [{"uuid": ANN_UUID, "number": None},
                  {"uuid": None, "number": DEE_NUMBER}]}, [ANN_UUID, DEE_NUMBER]),
    ({"members": []}, []),
    ({}, []),
])
def test_fetch_group_members_flattens_every_shape_the_server_returns(detail,
                                                                    expected):
    """Two reply shapes and two member shapes, like `_normalize_send_response`.

    NOT a claim about which one the deployed server sends — it is a claim that
    none of them silently produces a WRONG membership, which would become a
    resolution error rather than a crash.
    """
    seen = {}

    def getter(url, timeout=None):
        seen["url"] = url
        return types.SimpleNamespace(raise_for_status=lambda: None,
                                     json=lambda: detail)

    assert consumer.fetch_group_members(SELF_NUMBER, GROUP_ADDRESS,
                                        getter=getter,
                                        api_url="http://api") == expected
    assert seen["url"].startswith("http://api/v1/groups/")


def test_fetch_group_members_url_encodes_the_group_id():
    """A group `id` is base64 and can contain `/` and `+`. Unencoded, it breaks the path."""
    seen = {}

    def getter(url, timeout=None):
        seen["url"] = url
        return types.SimpleNamespace(raise_for_status=lambda: None,
                                     json=lambda: {"members": []})

    address = "group." + base64.b64encode(b"\xff\xfe" * 16).decode()
    consumer.fetch_group_members(SELF_NUMBER, address, getter=getter,
                                 api_url="http://api")
    assert "/" not in seen["url"].split("/v1/groups/")[1].split("/")[1]
    assert "+" not in seen["url"].rsplit("/", 1)[1]


def test_fetch_group_members_is_a_GET_not_a_send():
    """🔴 This module has ONE door to the network that writes; this is not it."""
    src = Path(consumer.__file__).read_text(encoding="utf-8")
    body = src.split("def fetch_group_members(")[1].split("\ndef ")[0]
    assert "SEND_PATH" not in body
    assert "requests.get" in " ".join(body.split())


# --------------------------------------------------------------------------- #
# 7. THE APPROVAL CARD — the operator has to SEE who gets pinged
# --------------------------------------------------------------------------- #
def test_the_clawgate_card_names_every_person_the_message_will_ping():
    """🔴 Approving a message without seeing who it notifies is the failure mode.

    `@Ann` in the preview is indistinguishable from ordinary prose — it IS
    ordinary prose until the `mentions` array turns it into a notification that
    bypasses Ann's mute settings. So the resolved ids go on the card explicitly.
    """
    payload = clawgate.build_draft_payload(
        draft_id=31, recipient=GROUP_ADDRESS, body="@Ann please look",
        mentions=[{"author": ANN_UUID, "start": 0, "length": 4},
                  {"author": DEE_NUMBER, "start": 0, "length": 4}])
    body = payload["body"]
    assert ANN_UUID in body
    assert DEE_NUMBER in body
    assert "mute settings" in body
    assert "PINGS 2 member(s)" in body


def test_the_card_shape_is_unchanged_and_still_hands_over_no_command():
    """The pre-existing exact assertion, kept exact, now with mentions present."""
    payload = clawgate.build_draft_payload(
        draft_id=23, recipient=GROUP_ADDRESS, body="the drafted text",
        mentions=[{"author": ANN_UUID, "start": 0, "length": 4}])
    assert set(payload) == {"directory", "body"}
    assert "the drafted text" in payload["body"]
    assert "#23" in payload["body"]
    for runnable in ("consumer.py", "--ref", "approve 23", "send 23"):
        assert runnable not in payload["body"], f"the card hands over {runnable!r}"


def test_a_mention_free_card_says_nothing_about_pings():
    """No warning where there is nothing to warn about — a warning that always
    fires is one nobody reads."""
    payload = clawgate.build_draft_payload(draft_id=24, recipient=PEER,
                                           body="a plain message")
    assert "PINGS" not in payload["body"]
    assert "mute settings" not in payload["body"]


# --------------------------------------------------------------------------- #
# 8. ROUND-1 AUDIT FIXES — every test below was watched RED at 182c3280
# --------------------------------------------------------------------------- #

# --- finding 1: a digest refusal was a TERMINAL DEAD END -------------------- #
def test_a_refused_draft_is_recoverable_THROUGH_THE_CLI_ALONE(db, monkeypatch,
                                                              capsys):
    """🔴 THE DEAD END, walked end to end through `consumer.main()` only.

    RED at 182c3280: `main(["unapprove", …])` exited 2 — argparse `invalid
    choice`, because the subcommand did not exist. `approve` is pending-only and
    `reconcile` is sending-only, so a draft the digest refused could not be moved
    by ANY CLI invocation; the refusal text and SKILL.md both told the operator
    to "re-approve it", which the CLI could not do.

    Driven through `main()` rather than the DB methods on purpose: the finding is
    about the surface an operator touches, and a test against `unapprove_draft()`
    alone would pass with the subcommand still unwired.
    """
    draft = _group_draft(db, body="@Ann please look")
    monkeypatch.setattr(consumer, "SignalDB", lambda *a, **k: _NoCloseDB(db))
    monkeypatch.setattr(clawgate, "emit_draft_task",
                        lambda **kw: False)

    assert consumer.main(["approve", str(draft["id"]), "--ref", "cg-1"]) == 0
    _tamper(db, draft["id"], body="@Ann approve the WIRE TRANSFER")

    # `send` refuses, and the row is left `approved` — the dead end's shape.
    assert consumer.main(["send", str(draft["id"])]) == 3
    assert db.get_draft(draft["id"])["send_state"] == _signal_db.STATE_APPROVED

    # The route the refusal NAMES, and it exists.
    assert consumer.main(["unapprove", str(draft["id"]),
                          "--note", "body changed"]) == 0
    assert db.get_draft(draft["id"])["send_state"] == _signal_db.STATE_PENDING
    assert consumer.main(["approve", str(draft["id"]), "--ref", "cg-2"]) == 0

    poster = Poster()
    sent = db.send_approved(
        draft["id"],
        transmit=lambda a, **kw: consumer.transmit_approved(a, poster=poster, **kw))
    assert sent["send_state"] == _signal_db.STATE_SENT
    assert poster.calls[0]["json"]["message"] == "@Ann approve the WIRE TRANSFER"


class _NoCloseDB:
    """Hand `main()` the test's live `db` without letting its `with` close it."""

    def __init__(self, db):
        self._db = db

    def __enter__(self):
        return self._db

    def __exit__(self, *exc):
        return False

    def __getattr__(self, name):
        return getattr(self._db, name)


def test_the_refusal_text_NAMES_a_command_the_CLI_really_has(db):
    """🔴 A guard on the STATE, not on a word: the command named in the error
    must be a real subparser choice.

    RED at 182c3280: the text said "Re-approve it", and the assertion below —
    that every backticked command in the refusal is in the parser's own choice
    set — had nothing to find. This reads the CLI's choices from argparse rather
    than restating them, so renaming the subcommand fails here.
    """
    draft = _group_draft(db)
    db.approve_draft(draft["id"], approval_ref="cg-text")
    _tamper(db, draft["id"], body="something else entirely")
    with pytest.raises(SendGateError) as exc:
        db.send_approved(draft["id"], transmit=lambda a, **kw: None)

    message = str(exc.value)
    choices = set(consumer.build_parser()._subparsers._group_actions[0].choices)
    named = {word.strip("`") for word in message.split()
             if word.startswith("`")} & choices
    assert "unapprove" in named, message
    assert "approve" in message and "SIGNAL_APPROVAL_TOKEN" in message


def test_unapprove_needs_the_operator_token_EXACTLY_like_approve(db, monkeypatch):
    """The recovery route must not be a hole in the gate it recovers from."""
    draft = _group_draft(db)
    db.approve_draft(draft["id"], approval_ref="cg-tok")
    monkeypatch.delenv("SIGNAL_APPROVAL_TOKEN", raising=False)
    with pytest.raises(SendGateError) as exc:
        db.unapprove_draft(draft["id"])
    assert "SIGNAL_APPROVAL_TOKEN" in str(exc.value)
    assert db.get_draft(draft["id"])["send_state"] == _signal_db.STATE_APPROVED


def test_unapprove_CLEARS_the_stale_digest(db):
    """A pending row carrying an old digest would be checked against a digest
    the next `approve` did not write."""
    draft = _group_draft(db)
    db.approve_draft(draft["id"], approval_ref="cg-clear")
    assert db.get_draft(draft["id"])["approved_digest"]
    db.unapprove_draft(draft["id"])
    assert db.get_draft(draft["id"])["approved_digest"] is None


def test_unapprove_refuses_anything_that_is_not_approved(db):
    """Not a state editor — `sending` belongs to `reconcile`, `pending` to nobody."""
    draft = _group_draft(db)
    with pytest.raises(SendGateError) as exc:      # still pending
        db.unapprove_draft(draft["id"])
    assert "may be unapproved" in str(exc.value)

    db.approve_draft(draft["id"], approval_ref="cg-state")
    db._claim_for_sending(draft["id"])             # now `sending`
    with pytest.raises(SendGateError) as exc:
        db.unapprove_draft(draft["id"])
    assert "reconcile" in str(exc.value)
    assert db.get_draft(draft["id"])["send_state"] == _signal_db.STATE_SENDING


def test_unapprove_does_not_erase_the_approval_ref_when_given_no_note(db):
    """The audit record of the approval the attempt rode on is ADDED to, never
    replaced. GREEN at 9fb6de75 — this half always worked."""
    draft = _group_draft(db)
    db.approve_draft(draft["id"], approval_ref="clawgate-task-777")
    db.unapprove_draft(draft["id"])
    assert db.get_draft(draft["id"])["approval_ref"] == "clawgate-task-777"


def test_unapprove_WITH_A_NOTE_appends_rather_than_erasing_the_approval_ref(db):
    """🔴 RED at 9fb6de75 — it returned the bare note. Round-2 audit F3.

    THE DOCUMENTED USAGE. `SKILL.md` tells the operator to run
    `unapprove 42 --note "body was edited after approval"` after a refused send,
    and `COALESCE(%s, approval_ref)` returns its FIRST non-null argument — so
    the note REPLACED `clawgate-task-778`, destroying the audit record of the
    approval that the refused attempt rode on, in the exact scenario the command
    exists for. The docstring above the SQL said the trail was "added to, never
    erased".

    The test that shipped with the command only exercised the no-note path while
    its docstring claimed the property for both. This is the note path.
    """
    draft = _group_draft(db)
    db.approve_draft(draft["id"], approval_ref="clawgate-task-778")
    db.unapprove_draft(draft["id"], note="body was edited after approval")
    ref = db.get_draft(draft["id"])["approval_ref"]
    assert ref == ("clawgate-task-778" + _signal_db.APPROVAL_REF_SEPARATOR
                   + "body was edited after approval")
    # BOTH halves named, so an implementation that keeps only one cannot pass.
    assert "clawgate-task-778" in ref
    assert "body was edited after approval" in ref
    # 🔴 The separator is pinned as a LITERAL here, once. Every other assertion
    # in this file builds its expectation FROM `APPROVAL_REF_SEPARATOR`, so a
    # mutant that empties it would weld two entries into `"cg-778body was …"`
    # and survive them all — the constant cannot check itself.
    assert _signal_db.APPROVAL_REF_SEPARATOR == " | "


def test_the_trail_appends_WITHIN_a_cycle_and_a_fresh_approve_RESETS_it(db):
    """🔴 THE EXACT SCOPE OF "APPEND ONLY", stated so nothing wider is claimed.

    `unapprove` and `reconcile` APPEND — that is F3, and it is what makes the
    approval a refused attempt rode on survivable. `approve_draft` still writes
    `approval_ref = %s`, i.e. it RESETS: a new approval is a new decision under a
    new clawgate reference, and `SKILL.md` documents it that way. So the column
    accumulates within one approve→withdraw cycle and starts over at the next
    `approve`. NOT changed here — the audit asked for the append, not for an
    unbounded log in a single TEXT column.

    RED at 9fb6de75 on the second assertion (it read `"note-2"` alone, the
    reference erased). The first is an INVARIANT GUARD on the reset, green there.
    """
    draft = _group_draft(db)
    db.approve_draft(draft["id"], approval_ref="cg-1")
    db.unapprove_draft(draft["id"], note="note-1")
    assert db.get_draft(draft["id"])["approval_ref"] == (
        "cg-1" + _signal_db.APPROVAL_REF_SEPARATOR + "note-1")

    db.approve_draft(draft["id"], approval_ref="cg-2")
    assert db.get_draft(draft["id"])["approval_ref"] == "cg-2", \
        "a fresh approve opens a new decision and REPLACES the reference"
    db.unapprove_draft(draft["id"], note="note-2")
    assert db.get_draft(draft["id"])["approval_ref"] == (
        "cg-2" + _signal_db.APPROVAL_REF_SEPARATOR + "note-2")


# --- finding 2: the digest bound a RENDERED PROJECTION ---------------------- #
def test_a_PLACEHOLDER_PROMOTION_between_approve_and_send_does_NOT_refuse(db):
    """🔴 THE FIRING-WITH-NO-ATTACKER CASE. Red at 182c3280.

    The ordinary "message someone new" path: draft a DM to a number we have
    never seen, approve it, and then that person's first envelope arrives.
    `_promote_placeholder()` — unattended background ingest — gives the contact
    row its real uuid, and `get_draft()`'s recipient CASE flips from printing the
    NUMBER to printing the UUID. At 182c3280 the digest was taken over that
    printed string, so `send` refused with "CHANGED after it was approved" — no
    adversary, no second writer, and NO CHANGE TO WHO THE MESSAGE GOES TO. With
    finding 1 unfixed the draft was then bricked.

    The promotion is performed by `upsert_message()`, the real ingest path, not
    by a hand-written UPDATE: the point is that ROUTINE traffic did this.
    """
    stranger = "+15550555"
    stranger_uuid = "44444444-4444-4444-8444-444444444444"
    draft = db.draft_message(recipient=stranger, body="hi, this is Zach",
                             self_number=SELF_NUMBER)
    contact_id = db.get_draft(draft["id"])["dest_contact_id"]
    db.approve_draft(draft["id"], approval_ref="cg-promote")
    assert db.get_draft(draft["id"])["recipient"] == stranger

    # ... their first message arrives, carrying both identifiers.
    db.upsert_message({"message_timestamp": 1723500001234,
                       "source_uuid": stranger_uuid,
                       "source_number": stranger,
                       "source_name": "Stranger", "body": "hello?",
                       "message_type": "message", "is_outbound": False})
    promoted = db.get_draft(draft["id"])
    assert promoted["recipient"] == stranger_uuid, (
        "the fixture did not actually promote — this test would then pass "
        "vacuously")
    assert promoted["dest_contact_id"] == contact_id, "the row id must be stable"

    poster = Poster()
    sent = db.send_approved(
        draft["id"],
        transmit=lambda a, **kw: consumer.transmit_approved(a, poster=poster, **kw))
    assert sent["send_state"] == _signal_db.STATE_SENT
    assert poster.calls[0]["json"]["recipients"] == [stranger_uuid]


def test_the_digest_is_over_a_STABLE_identity_not_the_rendered_recipient():
    """The property directly, at the unit level. Red at 182c3280.

    Two rows that address the SAME contact row and differ only in how
    `get_draft` rendered it must hash the same; two rows addressing DIFFERENT
    contacts must not. Both halves are asserted so a `recipient_identity` that
    returned a constant would fail the second.
    """
    as_number = {"dest_contact_id": 7, "recipient": "+15550555", "body": "hi",
                 "mentions": []}
    as_uuid = {"dest_contact_id": 7, "recipient": ANN_UUID, "body": "hi",
               "mentions": []}
    other = {"dest_contact_id": 8, "recipient": ANN_UUID, "body": "hi",
             "mentions": []}
    assert (_signal_db.draft_payload_digest(as_number)
            == _signal_db.draft_payload_digest(as_uuid))
    assert (_signal_db.draft_payload_digest(other)
            != _signal_db.draft_payload_digest(as_uuid))
    # The GROUP branch, pinned separately. Without this a `recipient_identity`
    # that ignored `group_id` entirely survived every other test, because a group
    # draft then fell through to the address string — which happens to be stable,
    # so nothing could tell the two apart. Measured: that mutant SURVIVED a
    # 663-test run until this pair was added.
    grp_a = {"group_id": 3, "recipient": GROUP_ADDRESS, "body": "hi",
             "mentions": []}
    grp_b = {"group_id": 3, "recipient": "group.SOMETHINGELSE=", "body": "hi",
             "mentions": []}
    grp_c = {"group_id": 4, "recipient": GROUP_ADDRESS, "body": "hi",
             "mentions": []}
    assert (_signal_db.draft_payload_digest(grp_a)
            == _signal_db.draft_payload_digest(grp_b))
    assert (_signal_db.draft_payload_digest(grp_a)
            != _signal_db.draft_payload_digest(grp_c))


def test_re_addressing_a_draft_to_a_DIFFERENT_contact_is_still_refused(db):
    """🔴 THE GUARD MUST NOT HAVE BEEN LOOSENED. Finding 2's fix relaxes WHAT is
    hashed, so the case it must still catch is pinned explicitly: a second writer
    pointing the draft at somebody else entirely.

    GREEN at 182c3280 — an INVARIANT GUARD, not regression coverage. It exists
    because the fix to finding 2 could have been "stop hashing the recipient",
    which every finding-2 test would also accept.
    """
    draft = db.draft_message(recipient=PEER, body="see you at 6",
                             self_number=SELF_NUMBER)
    db.approve_draft(draft["id"], approval_ref="cg-readdress")
    victim = db.upsert_contact(phone_number="+15550999", display_name="Someone Else")
    _tamper(db, draft["id"], dest_contact_id=victim)

    poster = Poster()
    with pytest.raises(SendGateError) as exc:
        db.send_approved(
            draft["id"],
            transmit=lambda a, **kw: consumer.transmit_approved(a, poster=poster,
                                                                **kw))
    assert "CHANGED after it was approved" in str(exc.value)
    assert poster.calls == []


# --- finding 3: `@name` was a bare substring match -------------------------- #
ANNA_UUID = "55555555-5555-4555-8555-555555555555"
PREFIX_MEMBERS = [ANN_UUID, ANNA_UUID]
PREFIX_CONTACTS = [
    {"signal_uuid": ANN_UUID, "phone_number": None, "display_name": "Ann",
     "profile_name": None, "is_placeholder": False},
    {"signal_uuid": ANNA_UUID, "phone_number": None, "display_name": "Anna",
     "profile_name": None, "is_placeholder": False},
]


def test_a_mention_does_NOT_land_inside_a_LONGER_members_name():
    """🔴 THE WRONG-PERSON PING. Red at 182c3280 — it emitted `(3, 4)`.

    Members `Ann` and `Anna`, body `"hi @Anna and @Ann ok"`. At 182c3280
    `body.find("@Ann")` returned 3, which sits INSIDE `@Anna`: Ann receives the
    notification while Anna's name is the text on screen, and the receiving
    client rewrites four characters out of the middle of someone else's name.

    The literals are written out and cross-checked against the body so a resolver
    that always emitted 0, or that emitted the code-point index, cannot agree
    with this test.
    """
    body = "hi @Anna and @Ann ok"
    out = _resolve(["Ann"], body, members=PREFIX_MEMBERS,
                   contacts=PREFIX_CONTACTS)
    assert out == [{"author": ANN_UUID, "start": 13, "length": 4}]
    assert body[13:17] == "@Ann"
    assert body[3:7] == "@Ann", "the WRONG span 182c3280 chose — inside '@Anna'"


def test_the_prefix_pair_in_BOTH_directions_gets_two_distinct_spans():
    """`--mention Ann --mention Anna` yielded two OVERLAPPING spans at offset 3."""
    body = "hi @Anna and @Ann ok"
    out = _resolve(["Ann", "Anna"], body, members=PREFIX_MEMBERS,
                   contacts=PREFIX_CONTACTS)
    assert out == [
        {"author": ANN_UUID, "start": 13, "length": 4},
        {"author": ANNA_UUID, "start": 3, "length": 5},
    ]


def test_a_mention_at_the_very_END_of_the_body_still_matches():
    """🔴 The boundary is "non-word char OR end of string".

    A `(?=\\W)` lookahead — the obvious spelling — would break EVERY message
    ending in the mention, which is the most ordinary shape there is. This is the
    positive control on finding 3's fix: without it, a boundary check that
    refused end-of-string would satisfy the two tests above and make the feature
    unusable.

    GREEN at 182c3280 — an INVARIANT GUARD. It pins what the fix must NOT break.
    """
    assert _resolve(["Ann"], "please look @Ann") == [
        {"author": ANN_UUID, "start": 12, "length": 4}]


def test_OVERLAPPING_spans_are_refused_with_their_own_error():
    """🔴 Boundary-legal and still overlapping. Red at 182c3280 (it sent both).

    ONE person carrying TWO stored names — `display_name="Ann"` and
    `profile_name="Ann Smith"` — and a body `"@Ann Smith please"`. `@Ann` is
    followed by a SPACE, so the word boundary is satisfied, and the two spans
    are (0,4) and (0,10). Two rewrites of the same characters; what the
    recipient renders is undefined.

    🔴 THE FIXTURE MOVED, AND THE REASON MATTERS. It used to give the two names
    to two DIFFERENT members. Round-2 audit F2 made a name that is a prefix of
    another MEMBER's name refuse earlier and more precisely, as
    `MentionSpanMissing` — see
    `test_a_member_whose_name_is_a_SPACE_separated_prefix_of_another_members_is_refused`,
    which pins that. `_colliding_needles()` deliberately does NOT veto a
    person's own longer name against their own ping, so the same-author shape
    still reaches this guard — and it is a real one: a promoted contact really
    can carry both a display name and a longer profile name.
    """
    contacts = [
        {"signal_uuid": ANN_UUID, "phone_number": None, "display_name": "Ann",
         "profile_name": "Ann Smith", "is_placeholder": False},
        {"signal_uuid": BOB_UUID, "phone_number": None,
         "display_name": "Bob", "profile_name": None,
         "is_placeholder": False},
    ]
    with pytest.raises(_mentions.MentionSpansOverlap) as exc:
        _resolve(["Ann", "Ann Smith"], "@Ann Smith please",
                 members=[ANN_UUID, BOB_UUID], contacts=contacts)
    message = str(exc.value)
    assert "OVERLAP" in message
    assert "0–4" in message and "0–10" in message


def test_two_ADJACENT_non_overlapping_mentions_are_NOT_refused():
    """Positive control on the overlap guard: touching is not overlapping.

    `"@Ann@Bob"` — spans (0,4) and (4,4) share the boundary index and nothing
    else. A guard written with `<=` instead of `<` would refuse this.

    GREEN at 182c3280 — an INVARIANT GUARD on the NEW overlap check, not
    regression coverage.
    """
    body = "@Ann@Bob"
    out = _resolve(["Ann", "Bob"], body,
                   contacts=[c for c in CONTACTS if c["display_name"] != "Bob"
                             or c["signal_uuid"] == BOB_UUID])
    assert [(m["start"], m["start"] + m["length"]) for m in out] == [(0, 4), (4, 8)]


# --- finding 7c: resolution lowercases, the body search did not ------------- #
def test_a_DIFFERENTLY_CASED_mention_resolves_AND_finds_its_span():
    """🔴 Red at 182c3280 with `MentionSpanMissing`.

    `_resolve_one()` matches names via `.lower()`, so `--mention ANN` resolved
    fine and then died in the span search, which was exact. The two halves of one
    argument disagreed about what it meant.
    """
    assert _resolve(["ANN"], "hi @Ann there") == [
        {"author": ANN_UUID, "start": 3, "length": 4}]
    assert _resolve(["ann"], "hi @Ann there")[0]["author"] == ANN_UUID


def test_case_insensitive_matching_did_not_break_the_utf16_offsets():
    """🔴 The offsets must be computed on the ORIGINAL body.

    `str.lower()` and `str.casefold()` CHANGE LENGTH on real characters, so the
    obvious implementation — fold the body, `find()` in the folded copy — reports
    an offset into a string that is not the one being sent. Measured on this very
    body: the true `@Ann` is at 9, a `.lower()` implementation says 10, and a
    `.casefold()` one says 11. All three numbers are asserted as literals so a
    folding implementation cannot make this test agree with itself.
    """
    body = "İ straße @Ann"
    assert body.find("@Ann") == 9
    assert body.lower().find("@ann") == 10, "the premise: .lower() SHIFTS it"
    assert body.casefold().find("@ann") == 11, "and .casefold() shifts it further"
    assert _resolve(["ann"], body) == [
        {"author": ANN_UUID, "start": 9, "length": 4}]


# --- round-2 F1: the cursor was case-SENSITIVE, the matcher case-INSENSITIVE - #
def test_two_DIFFERENTLY_CASED_mentions_of_one_person_take_SUCCESSIVE_spans():
    """🔴 RED at 9fb6de75 with `MentionSpansOverlap`. Round-2 audit F1.

    `_needle_pattern` compiles with `re.IGNORECASE`, but `resolve_mentions`
    keyed its per-needle search cursor on the RAW `"@" + ident`. `--mention Ann
    --mention ANN` therefore created TWO dict entries, both starting from
    cursor 0, and both matched the SAME first occurrence — two identical spans,
    which the overlap guard then refused with "Give each mention its own `@who`
    text in --body" while the body already had two distinct ones.

    A draft that WORKED at 182c3280 (`[(3,4), (12,4)]`) was refused at
    9fb6de75, so this is a regression test, not an invariant guard. The two
    spans are pinned as literals: an implementation that folds the key but not
    the cursor arithmetic cannot make these numbers agree.
    """
    out = _resolve(["Ann", "ANN"], "hi @Ann and @ANN ok")
    assert out == [{"author": ANN_UUID, "start": 3, "length": 4},
                   {"author": ANN_UUID, "start": 12, "length": 4}]


def test_the_case_folded_cursor_still_REFUSES_when_there_is_only_one_occurrence():
    """The fold must move the cursor, not disable it.

    A key-folding change that also stopped ADVANCING the cursor would make the
    test above pass and silently point two mentions at one `@Ann` again. With
    only one occurrence in the body the second `--mention` must still run out of
    text.

    RED at 9fb6de75 — MEASURED, not assumed: it raised `MentionSpansOverlap`
    there, because both mentions claimed span (3,4). The refusal was right by
    accident and for the wrong reason; the class asserted here is the one that
    says what is actually wrong with the draft.
    """
    with pytest.raises(MentionSpanMissing):
        _resolve(["Ann", "ANN"], "hi @Ann ok")


# --- round-2 F2: `(?!\w)` is narrower than "a prefix of another member" ----- #
@pytest.mark.parametrize("other_name, body", [
    ("Ann-Marie", "hi @Ann-Marie ok"),
    ("Ann.Smith", "hi @Ann.Smith ok"),
    ("Ann'Marie", "hi @Ann'Marie ok"),
])
def test_a_PUNCTUATED_member_name_no_longer_steals_a_shorter_members_ping(
        other_name, body):
    """🔴 RED at 9fb6de75 — it returned `{author: Ann, start: 3, length: 4}`.

    Round-2 audit F2. `(?!\\w)` blocks only WORD characters, so a hyphen, a dot
    or an apostrophe in another member's name satisfied the boundary and
    `--mention Ann` landed on the first four characters of THEIR name: Ann is
    pinged, the text on screen is corrupted mid-word, and nothing refused. That
    is the exact failure the boundary docstring claimed to have removed.

    Parametrised over three separators on purpose — a fix that special-cases the
    hyphen passes one case and fails the other two.
    """
    contacts = [
        {"signal_uuid": ANN_UUID, "phone_number": None, "display_name": "Ann",
         "profile_name": None, "is_placeholder": False},
        {"signal_uuid": BOB_UUID, "phone_number": None,
         "display_name": other_name, "profile_name": None,
         "is_placeholder": False},
    ]
    with pytest.raises(MentionSpanMissing) as exc:
        _resolve(["Ann"], body, members=[ANN_UUID, BOB_UUID], contacts=contacts)
    assert "another member's name" in str(exc.value)

    # POSITIVE CONTROL, same fixture: the LONGER name is still mentionable, and
    # a real `@Ann` later in the same body is still found. Without these two a
    # `raise MentionSpanMissing` at the top of `find_span` would pass the above.
    assert _resolve([other_name], body, members=[ANN_UUID, BOB_UUID],
                    contacts=contacts) == [
        {"author": BOB_UUID, "start": 3, "length": len(other_name) + 1}]
    assert _resolve(["Ann"], body.replace(" ok", " and @Ann ok"),
                    members=[ANN_UUID, BOB_UUID], contacts=contacts) == [
        {"author": ANN_UUID, "start": len(other_name) + 9, "length": 4}]


def test_a_member_whose_name_is_a_SPACE_separated_prefix_of_another_members_is_refused():
    """🔴 RED at 9fb6de75 — it SENT `{author: Ann, start: 0, length: 4}`.

    The space-separated case of the same defect, and the one the old
    `test_OVERLAPPING_spans_are_refused_with_their_own_error` fixture only ever
    caught when BOTH names were mentioned. With `--mention Ann` alone against
    members `Ann` and `Ann Smith` and a body `"@Ann Smith please"`, 9fb6de75
    returned a span over `Ann Smith`'s first four characters with no refusal at
    all — the overlap guard never ran, because there was only one mention.
    """
    contacts = [
        {"signal_uuid": ANN_UUID, "phone_number": None, "display_name": "Ann",
         "profile_name": None, "is_placeholder": False},
        {"signal_uuid": BOB_UUID, "phone_number": None,
         "display_name": "Ann Smith", "profile_name": None,
         "is_placeholder": False},
    ]
    with pytest.raises(MentionSpanMissing):
        _resolve(["Ann"], "@Ann Smith please", members=[ANN_UUID, BOB_UUID],
                 contacts=contacts)


def test_a_members_OWN_longer_name_does_not_veto_their_own_ping():
    """🔴 The collision rule must be scoped to OTHER authors.

    One contact carrying `display_name="Ann"` and `profile_name="Ann Smith"` is
    ONE person — a routine shape after `_promote_placeholder()` learns a profile
    name. A collision rule that ignored authorship would refuse `--mention Ann`
    against `"@Ann Smith please"` on the strength of that person's own other
    name, i.e. refuse a ping at the very person who was asked for.

    GREEN at 9fb6de75 (no collision rule existed) — an INVARIANT GUARD on the
    new rule's scope, not regression coverage.
    """
    contacts = [
        {"signal_uuid": ANN_UUID, "phone_number": None, "display_name": "Ann",
         "profile_name": "Ann Smith", "is_placeholder": False},
    ]
    assert _resolve(["Ann"], "@Ann Smith please", members=[ANN_UUID],
                    contacts=contacts) == [
        {"author": ANN_UUID, "start": 0, "length": 4}]


def test_ordinary_punctuation_after_a_mention_still_matches():
    """🔴 The collision rule is MEMBER-AWARE, not a wider punctuation ban.

    Refusing every non-word character after the needle would have been the cheap
    fix, and it breaks ordinary English: `"thanks @Ann."` and `"@Ann-please"`
    have no colliding member behind them and must still resolve. This is the
    negative control that separates "member-aware" from "punctuation-phobic".

    GREEN at 9fb6de75 — an INVARIANT GUARD, not regression coverage.
    """
    for body, start in [("thanks @Ann.", 7), ("@Ann-please", 0), ("@Ann's dog", 0)]:
        assert _resolve(["Ann"], body) == [
            {"author": ANN_UUID, "start": start, "length": 4}], body


# --- finding 5: refusal messages leaked the whole group roster -------------- #
def test_a_not_a_member_refusal_does_NOT_enumerate_the_roster():
    """🔴 Red at 182c3280 — it interpolated `sorted(member_set)`.

    `draft` needs no approval token, so this refusal is a free, repeatable probe:
    any caller that can run the CLI could read the FULL membership — uuids and
    phone numbers — of any group address it can guess, including a MUTED one.
    """
    with pytest.raises(MentionNotAMember) as exc:
        _resolve([OUTSIDER_UUID], f"hello @{OUTSIDER_UUID}")
    message = str(exc.value)
    assert "NOT a member of the target group" in message
    for member in MEMBERS:
        assert member not in message, f"the refusal leaked {member!r}"
    assert f"{len(MEMBERS)} member(s)" in message, (
        "the count is what makes the message honest about what it withheld")


def test_a_name_not_found_refusal_TRUNCATES_the_name_list():
    """🔴 Red at 182c3280 — every stored display/profile name was interpolated.

    Truncation rather than removal: a couple of names help an operator spot a
    typo. The count of withheld names is stated so the truncation is visible.
    """
    many = [{"signal_uuid": f"{i:08d}-1111-4111-8111-111111111111",
             "phone_number": None, "display_name": f"Member{i:02d}",
             "profile_name": None, "is_placeholder": False}
            for i in range(12)]
    with pytest.raises(MentionNameNotFound) as exc:
        _resolve(["Zoe"], "hi @Zoe",
                 members=[c["signal_uuid"] for c in many], contacts=many)
    message = str(exc.value)
    # `_contact_names` lower-cases, so that is the form the message carries.
    shown = [c["display_name"] for c in many
             if c["display_name"].lower() in message]
    assert len(shown) == _mentions.NAME_HINT_MAX, shown
    assert f"+{12 - _mentions.NAME_HINT_MAX} not shown" in message


# --- finding 6: the approval card showed a raw UUID ------------------------- #
def test_the_card_carries_the_resolved_DISPLAY_NAME_not_just_a_uuid():
    """🔴 Red at 182c3280 — the line was `<uuid> (chars 0–4)`.

    The card exists to tell a HUMAN who a draft will ping. Five of the seven
    members of the real group are uuid-only, so for most mentions the card said
    nothing checkable.
    """
    payload = clawgate.build_draft_payload(
        draft_id=32, recipient=GROUP_ADDRESS, body="@Ann please look",
        mentions=[{"author": ANN_UUID, "start": 0, "length": 4}],
        author_names={ANN_UUID: "Ann"})
    assert "Ann <" + ANN_UUID + ">" in payload["body"]


def test_the_card_says_UTF16_UNITS_not_chars():
    """🔴 "chars" was inaccurate, and inaccurate in the direction that matters:
    with an emoji earlier in the body the two numbers genuinely differ."""
    payload = clawgate.build_draft_payload(
        draft_id=33, recipient=GROUP_ADDRESS, body="\U0001F415 @Ann",
        mentions=[{"author": ANN_UUID, "start": 3, "length": 4}],
        author_names={ANN_UUID: "Ann"})
    assert "UTF-16 units 3–7" in payload["body"]
    assert "chars" not in payload["body"]


def test_an_author_with_no_stored_name_says_so_rather_than_going_blank():
    """A missing name must not silently render as an empty label."""
    payload = clawgate.build_draft_payload(
        draft_id=34, recipient=GROUP_ADDRESS, body="@Ann",
        mentions=[{"author": ANN_UUID, "start": 0, "length": 4}])
    assert "(no stored name)" in payload["body"]
    assert ANN_UUID in payload["body"]


def test_mention_author_names_resolves_through_the_contacts_table(db):
    """The seam: `main()` must actually FEED the card names, not just accept them."""
    db.upsert_contact(signal_uuid=ANN_UUID, display_name="Ann")
    assert consumer.mention_author_names(
        db, [{"author": ANN_UUID, "start": 0, "length": 4}]) == {ANN_UUID: "Ann"}
    assert consumer.mention_author_names(db, []) == {}


def test_the_draft_command_passes_the_names_it_resolved_to_the_card(db, monkeypatch):
    """🔴 Red at 182c3280: `emit_draft_task` took no names, so this asserted a
    keyword that did not exist. The SEAM — a card that can render a name is
    useless if the CLI never supplies one.
    """
    db.upsert_contact(signal_uuid=ANN_UUID, display_name="Ann")
    seen = {}
    monkeypatch.setattr(consumer, "SignalDB", lambda *a, **k: _NoCloseDB(db))
    monkeypatch.setattr(clawgate, "emit_draft_task",
                        lambda **kw: seen.update(kw) or True)
    monkeypatch.setattr(consumer, "fetch_group_members",
                        lambda number, address: [ANN_UUID])
    assert consumer.main(["draft", "--to", GROUP_ADDRESS, "--body",
                          "@Ann please look", "--from-number", SELF_NUMBER,
                          "--mention", "Ann"]) == 0
    assert seen["author_names"] == {ANN_UUID: "Ann"}
    assert ("Ann <" + ANN_UUID + ">"
            in clawgate.build_draft_payload(
                draft_id=1, recipient=GROUP_ADDRESS, body="@Ann please look",
                mentions=seen["mentions"],
                author_names=seen["author_names"])["body"])


# --- finding 7a: a malformed stored `mentions` raised a bare TypeError ------ #
@pytest.mark.parametrize("bad", [
    [{"author": ANN_UUID, "start": None, "length": 4}],
    [{"author": ANN_UUID, "length": 4}],
    ["not an object"],
    [None],
])
def test_an_unreadable_stored_mention_is_a_MentionError_not_a_TypeError(bad):
    """🔴 Red at 182c3280 with `TypeError`, which NO CLI handler catches —
    `send` catches `SendGateError`, `draft` catches `ValueError` — so a bad row
    in the database surfaced as a traceback and exit 1."""
    with pytest.raises(_mentions.MentionError):
        _mentions.canonical_mentions(bad)
    with pytest.raises(_mentions.MentionError):
        _mentions.describe_mentions(bad)


def test_a_draft_with_an_unreadable_mentions_column_REFUSES_as_a_SendGateError(db):
    """The same defect on the send path, where it decides an exit code.

    Red at 182c3280: `TypeError: int() argument must be…` escaped
    `send_approved()` as a traceback.
    """
    draft = _group_draft(db)
    db.approve_draft(draft["id"], approval_ref="cg-bad-mentions")
    _tamper(db, draft["id"],
            mentions=json.dumps([{"author": ANN_UUID, "start": None,
                                  "length": 4}]))
    poster = Poster()
    with pytest.raises(SendGateError) as exc:
        db.send_approved(
            draft["id"],
            transmit=lambda a, **kw: consumer.transmit_approved(a, poster=poster,
                                                                **kw))
    assert "unreadable" in str(exc.value)
    assert poster.calls == []


def test_spend_authorization_refuses_an_unreadable_mentions_argument():
    """Window B's half of the same translation."""
    auth = _signal_db._mint_send_authorization(_approved_row(47, PEER, "hi"))
    with pytest.raises(SendGateError) as exc:
        _signal_db.spend_authorization(auth, recipient=PEER, body="hi",
                                       mentions=[{"author": ANN_UUID}])
    assert "unreadable" in str(exc.value)


# --- finding 7b: a group-API failure escaped `draft` as a traceback --------- #
def test_a_failing_group_lookup_is_a_REFUSAL_not_a_traceback(db):
    """🔴 Red at 182c3280: `requests.HTTPError` is not a `ValueError`, so
    `main()`'s draft handler did not catch it and the operator got a stack trace
    and exit 1 — indistinguishable from the interpreter dying."""
    class _HTTPError(Exception):
        pass

    def fetcher(number, address):
        raise _HTTPError("404 Client Error for url: /v1/groups//<gid>")

    with pytest.raises(_mentions.MentionGroupLookupFailed) as exc:
        consumer.resolve_draft_mentions(
            db, recipient=GROUP_ADDRESS, body="@Ann", identifiers=["Ann"],
            number=SELF_NUMBER, member_fetcher=fetcher)
    assert "_HTTPError" in str(exc.value)
    assert isinstance(exc.value, ValueError), (
        "the draft handler catches ValueError; anything else still escapes")


def test_an_EMPTY_from_number_is_refused_BEFORE_the_404(db):
    """The ordinary way to hit finding 7b: `--from-number ''` builds
    `/v1/groups//<gid>`. Named at the argument, not at the HTTP status."""
    calls = []

    def fetcher(number, address):
        calls.append((number, address))
        raise AssertionError("a membership fetch was attempted with no number")

    with pytest.raises(_mentions.MentionGroupLookupFailed) as exc:
        consumer.resolve_draft_mentions(
            db, recipient=GROUP_ADDRESS, body="@Ann", identifiers=["Ann"],
            number="", member_fetcher=fetcher)
    # 🔴 BOTH halves, because either alone is green for the wrong reason. A
    # mutant deleting the empty-number guard let the fetch run, its AssertionError
    # got wrapped into a `MentionGroupLookupFailed` whose generic text also says
    # "--from-number", and the test SURVIVED — measured. So: the fetch must not
    # have happened, and the message must name the EMPTY ARGUMENT, not an HTTP
    # failure.
    assert calls == [], "the refusal must come BEFORE the doomed lookup"
    assert "--from-number is empty" in str(exc.value)
    assert "GET /v1/groups" in str(exc.value)


def test_a_failed_group_lookup_exits_3_from_main_and_drafts_NOTHING(db, monkeypatch):
    """End to end at the surface the operator touches. Red at 182c3280 (exit 1,
    traceback)."""
    monkeypatch.setattr(consumer, "SignalDB", lambda *a, **k: _NoCloseDB(db))
    monkeypatch.setattr(clawgate, "emit_draft_task",
                        lambda **kw: False)
    before = db.conn.count("messages")
    assert consumer.main(["draft", "--to", GROUP_ADDRESS, "--body", "@Ann hi",
                          "--from-number", "", "--mention", "Ann"]) == 3
    assert db.conn.count("messages") == before, "a draft was left behind"
