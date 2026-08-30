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
    return {"id": draft_id, "send_state": _signal_db.STATE_APPROVED,
            "recipient": recipient, "body": body, "mentions": mentions or [],
            "approved_digest": _signal_db.payload_digest(
                recipient=recipient, body=body, mentions=mentions or [])}


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


def test_ensure_schema_twice_is_a_no_op(db):
    """🔴 The migration is `ADD COLUMN IF NOT EXISTS` — idempotent, like the rest.

    `ensure_schema()` runs on every consumer start. A migration that raised the
    second time would crash the pod on restart, and the restart is precisely the
    moment it runs.
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
    """Approval is `pending`-only, so re-approving means resetting the state first.

    Done through the substrate rather than through `reconcile_send()` because the
    draft never entered `sending` — the refusal happened before the claim.
    """
    with db.conn.cursor() as cur:
        cur.execute("UPDATE signal.messages SET send_state = %s WHERE id = %s",
                    (_signal_db.STATE_PENDING, draft_id))
    db.conn.commit()
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
