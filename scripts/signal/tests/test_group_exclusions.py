"""The group MUTE list — `signal.excluded_groups` and the one read predicate.

Scope of the claim these tests make, stated up front because it is narrower than
"the group is filtered out":

  * They cover the three PYTHON read surfaces on `SignalDB`. Raw `psql` against
    the schema bypasses every one of them, by construction — a predicate in an
    application query cannot bind a hand-typed statement. `SKILL.md` says so
    where it prints those one-liners.
  * They assert HIDING, never deleting. The rows stay; that is the whole design,
    and `test_muting_deletes_nothing...` is what keeps it true.

The seam these tests exist for: a filter open-coded at three call sites is wrong
at two of them, in the same direction, and the failure is silent. So there is a
behavioural case per surface AND a ledger that fails when the set of read
surfaces grows or shrinks — a structural check alone type-checks past a wrong
argument, and a behavioural check alone cannot see a NEW method that skipped it.
"""
from __future__ import annotations

import base64
import inspect
import re

import pytest

import _signal_db
from _signal_db import SignalDB, not_excluded

import consumer


GROUP_KEEP = b"\x11" * 32       # 32 bytes, like every real Signal group id
GROUP_MUTE = b"\x22" * 32
GROUP_UNSEEN = b"\x33" * 32     # muted before it was ever stored

# The shape of the API's `id` field: `group.` + base64(base64(raw)). GENERATED
# from a repeated byte, never copied from a live response.
#
# 🔴 THIS REPO IS PUBLIC AND NOTHING WOULD CATCH A REAL ONE. A Signal group id
# identifies a real group of real people; the four content gates
# (`test_no_captured_text.py`, `test_no_captured_markup.py`, and the IP and
# hostname ones) scan JSON/JSONL/HTML/TXT and **not** `.py`, so a real id pasted
# into a test file lands in history permanently with no gate between it and
# `main`. An earlier revision of this very file carried one, copied straight out
# of `GET /v1/groups/<account>` while checking what the field looked like.
# Generate fixtures; never paste a response.
GROUP_ID_FIELD_SHAPE = "group." + base64.b64encode(
    base64.b64encode(b"\x44" * 32)).decode()


def _seed(db):
    """Two groups and a DM, each with a distinctive body. Returns row ids.

    🔴 Every body, group id and contact is PAIRWISE DISTINCT, and distinct from
    any constant an assertion names. A fixture whose values collapse into each
    other cannot see a mutant that hardcodes one of them — this pipeline has
    already shipped three such fixtures (two operand-order mutants survived
    because `source` and `sourceNumber` were the same string, and a hardcoding
    mutant survived 400 tests because the fixture emoji equalled the asserted
    constant).
    """
    keep_row = db.upsert_group(group_id=GROUP_KEEP, name="")
    mute_row = db.upsert_group(group_id=GROUP_MUTE, name="")
    ids = {}
    ids["keep"] = db.upsert_message({
        "message_timestamp": 1000,
        "source_uuid": "11111111-1111-4111-8111-111111111111",
        "source_number": "+15550001", "source_name": "Alice",
        "message_type": "group_message", "body": "quarterly kestrel report",
        "group_id": GROUP_KEEP, "is_outbound": False,
    })
    ids["mute"] = db.upsert_message({
        "message_timestamp": 2000,
        "source_uuid": "22222222-2222-4222-8222-222222222222",
        "source_number": "+15550002", "source_name": "Bob",
        "message_type": "group_message", "body": "quarterly kestrel potluck",
        "group_id": GROUP_MUTE, "is_outbound": False,
    })
    ids["dm"] = db.upsert_message({
        "message_timestamp": 3000,
        "source_uuid": "33333333-3333-4333-8333-333333333333",
        "source_number": "+15550003", "source_name": "Carol",
        "message_type": "direct_message", "body": "quarterly kestrel invoice",
        "group_id": None, "is_outbound": False,
    })
    ids["_rows"] = {"keep": keep_row, "mute": mute_row}
    return ids


def _conv_group_rows(db):
    return {r["group_row_id"] for r in db.list_conversations()}


# --------------------------------------------------------------------------- #
# Behaviour, one case per read surface — each with its own positive control
# --------------------------------------------------------------------------- #
def test_conversations_hides_a_muted_group_and_shows_it_again_after_unmute(db):
    ids = _seed(db)
    mute_row = ids["_rows"]["mute"]
    keep_row = ids["_rows"]["keep"]

    # POSITIVE CONTROL: it is visible BEFORE the mute. Without this the "hidden"
    # assertion below is indistinguishable from a query wired to nothing.
    assert mute_row in _conv_group_rows(db)
    assert keep_row in _conv_group_rows(db)

    db.exclude_group(GROUP_MUTE, note="filtered by operator")
    after = _conv_group_rows(db)
    assert mute_row not in after
    assert keep_row in after
    assert None in after, "the DM conversation must survive a group mute"

    # And the rollback story is real, not asserted.
    assert db.unexclude_group(GROUP_MUTE) == 1
    assert _conv_group_rows(db) == {mute_row, keep_row, None}


def test_search_hides_a_muted_group(db):
    _seed(db)
    # All three bodies match this query — that is deliberate. A query matching
    # only the muted row could not tell "filtered correctly" from "matched
    # nothing", which is the empty-result trap.
    before = {r["body"] for r in db.search("quarterly kestrel")}
    assert before == {"quarterly kestrel report", "quarterly kestrel potluck",
                      "quarterly kestrel invoice"}

    db.exclude_group(GROUP_MUTE)
    after = {r["body"] for r in db.search("quarterly kestrel")}
    assert after == {"quarterly kestrel report", "quarterly kestrel invoice"}


def test_get_message_hides_a_muted_message_by_id(db):
    ids = _seed(db)
    assert db.get_message(ids["mute"])["body"] == "quarterly kestrel potluck"

    db.exclude_group(GROUP_MUTE)
    assert db.get_message(ids["mute"]) is None, (
        "the id route must be filtered too — otherwise the mute has a hole in "
        "the shape of anyone who knows a message id")
    # The unmuted neighbours are untouched: this is a filter, not an outage.
    assert db.get_message(ids["keep"])["body"] == "quarterly kestrel report"
    assert db.get_message(ids["dm"])["body"] == "quarterly kestrel invoice"


def test_muting_deletes_nothing_and_unmute_restores_exactly(db):
    ids = _seed(db)
    before_rows = db.conn.count("messages")
    before_search = [dict(r) for r in db.search("quarterly kestrel")]

    db.exclude_group(GROUP_MUTE)
    assert db.conn.count("messages") == before_rows, (
        "muting must not delete — the pipeline is forward-only, so a deleted "
        "message can never be re-fetched and the decision would be irreversible")
    assert db.conn.count("attachments") == 0  # nothing cascaded either

    db.unexclude_group(GROUP_MUTE)
    assert [dict(r) for r in db.search("quarterly kestrel")] == before_search
    assert db.get_message(ids["mute"])["body"] == "quarterly kestrel potluck"


# --------------------------------------------------------------------------- #
# The mute list itself
# --------------------------------------------------------------------------- #
def test_muting_is_idempotent_and_the_note_is_updated_not_duplicated(db):
    _seed(db)
    db.exclude_group(GROUP_MUTE, note="first reason")
    db.exclude_group(GROUP_MUTE, note="second reason")
    rows = db.list_excluded_groups()
    assert len(rows) == 1
    assert rows[0]["note"] == "second reason"


def test_re_muting_WITHOUT_a_note_keeps_the_recorded_reason(db):
    """The note→note case above cannot see this; note→None is the common one.

    `mute <id>` with no --note is the natural way to re-issue a mute, and a bare
    `note = EXCLUDED.note` made that destroy the only record of WHY the group is
    muted — as a side effect of a command that otherwise changes nothing.
    """
    _seed(db)
    db.exclude_group(GROUP_MUTE, note="spam group, muted 2026-08-19")
    db.exclude_group(GROUP_MUTE)
    assert db.list_excluded_groups()[0]["note"] == "spam group, muted 2026-08-19"


def test_a_group_can_be_muted_before_it_has_ever_been_seen(db):
    _seed(db)
    db.exclude_group(GROUP_UNSEEN, note="not stored yet")
    listed = {bytes(r["group_id"]): r for r in db.list_excluded_groups()}
    assert listed[GROUP_UNSEEN]["group_row_id"] is None, (
        "an unseen group is an ordinary state — the mute is the INTENT, and a "
        "FK to signal.groups could not express it")

    # …and it takes effect the moment the group does appear.
    db.upsert_message({
        "message_timestamp": 4000,
        "source_uuid": "44444444-4444-4444-8444-444444444444",
        "source_number": "+15550004", "source_name": "Dave",
        "message_type": "group_message", "body": "quarterly kestrel latecomer",
        "group_id": GROUP_UNSEEN, "is_outbound": False,
    })
    row = db.upsert_group(group_id=GROUP_UNSEEN, name="")
    assert row not in _conv_group_rows(db)
    assert not [r for r in db.search("quarterly kestrel")
                if r["body"] == "quarterly kestrel latecomer"]


def test_hidden_message_count_reports_what_the_mute_is_actually_hiding(db):
    ids = _seed(db)
    db.exclude_group(GROUP_MUTE)
    row = next(r for r in db.list_excluded_groups()
               if bytes(r["group_id"]) == GROUP_MUTE)
    assert row["hidden_message_count"] == 1
    assert row["group_row_id"] == ids["_rows"]["mute"]


def test_unmuting_something_that_was_not_muted_reports_zero(db):
    _seed(db)
    assert db.unexclude_group(GROUP_KEEP) == 0


@pytest.mark.parametrize("bad", [b"", bytearray()])
def test_an_empty_group_id_is_refused(db, bad):
    with pytest.raises(ValueError):
        db.exclude_group(bad)


def test_a_str_group_id_is_refused(db):
    """A base64 STRING silently stored as bytes would mute nothing, forever.

    🔴 `match=` is load-bearing. Without it this passed on the `TypeError` that
    `bytes("…")` raises further down — green for the wrong reason, and still
    green with the isinstance guard deleted. Pin THIS guard's own message.
    """
    with pytest.raises(TypeError, match="raw Signal group id"):
        db.exclude_group("IiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiI=")


# --------------------------------------------------------------------------- #
# The predicate
# --------------------------------------------------------------------------- #
def test_the_predicate_refuses_an_unsafe_alias():
    """The alias is interpolated, so the whitelist is load-bearing."""
    for bad in ("m; DROP TABLE signal.messages --", "M", "1m", "m m", ""):
        with pytest.raises(ValueError):
            not_excluded(bad)


def test_the_predicate_actually_USES_the_alias_it_is_given():
    """🔴 Validating the alias is not the same as using it.

    A mutant that validated `alias` and then hardcoded `m` SURVIVED the whole
    suite: every call site passes `m`, so nothing could tell the two apart. The
    parameter would have silently become decorative, and the first read method
    to use a different alias would get a predicate pointing at the wrong table.
    """
    assert "q.group_id" in not_excluded("q")
    assert "m.group_id" not in not_excluded("q")


def test_the_predicate_is_composable_with_AND():
    """`NOT EXISTS`, never `... IS NULL OR ...`.

    An OR-form predicate ANDed onto a WHERE clause without brackets widens the
    query to every row — silently, and only at the call site that forgot them.
    This pins the shape rather than the wording, so a rewrite that reintroduces
    a top-level OR fails here.
    """
    sql = not_excluded("m")
    assert sql.startswith("NOT EXISTS ("), sql
    # No OR outside the subquery's parens.
    depth, top_level = 0, []
    for tok in re.findall(r"\(|\)|\bOR\b", sql):
        if tok == "(":
            depth += 1
        elif tok == ")":
            depth -= 1
        elif depth == 0:
            top_level.append(tok)
    assert not top_level, f"top-level OR in the predicate: {sql}"


# --------------------------------------------------------------------------- #
# 🔴 THE LEDGER — fails when the set of message-reading surfaces GROWS or SHRINKS
# --------------------------------------------------------------------------- #
# Every method here reads `signal.messages` and returns rows to a caller. Each
# must either apply `not_excluded()` or be listed as EXEMPT with a reason. This
# is the seam guard: the behavioural cases above can only see the surfaces that
# existed when they were written, and a new read method that skipped the filter
# is exactly the failure this pipeline has already shipped once (the sync
# reaction branch, added without the guards its inbound twin already had).
FILTERED_READS = {"list_conversations", "search", "get_message"}

EXEMPT_READS = {
    # Counts what a mute is hiding — it must see muted rows, that is its job.
    "list_excluded_groups":
        "reports the hidden count; filtering it would always print 0",
    # --- WRITES that name signal.messages in a subquery, not read surfaces.
    "upsert_reaction":
        "write: resolves a reaction's target message id",
    "apply_remote_delete":
        "write: blanks a deleted message's body",
    # --- The DRAFT surface (D3). Drafts live in signal.messages with a NEGATIVE
    # provisional timestamp and are addressed to a RECIPIENT, never a group, so
    # `group_id` is NULL on every one of them and the predicate could only ever
    # be a no-op. Filtering the compose/approve/send path by a mute list would
    # also be wrong in principle: muting is about what you READ, and a draft is
    # something you WROTE. 🔴 If drafting to a group is ever added, these move
    # into FILTERED_READS — this test fails when that day comes only because the
    # method set is pinned, so re-read this note then rather than trusting it.
    "get_draft": "draft surface: drafts have no group_id",
    "list_drafts": "draft surface: drafts have no group_id",
    "account_number": "draft surface: reads the sending account off a draft",
}


def _read_methods_touching_messages() -> set[str]:
    """Methods whose body SELECTs from `signal.messages`.

    🔴 SCOPE, stated because this guard is SPELLED rather than structural and
    the difference matters. It greps the method's own source text, so it detects
    exactly one shape: SQL written inline, naming `signal.messages`, in a method
    defined directly on `SignalDB`. Measured evasions — all four confirmed to
    slip past it: SQL held in a module-level constant; `SET search_path` plus an
    unqualified `FROM messages`; delegation to a module-level helper; a method
    inherited from a mixin (absent from `vars(SignalDB)`). It also FALSE-positives
    on a method whose comment merely names both tokens.

    So this catches the copy-paste shape — which is how every read method here
    was actually written, and how the next one will be — and it is not a proof
    of completeness. Claim it as the former.
    """
    found = set()
    for name, fn in vars(SignalDB).items():
        if not callable(fn) or name.startswith("__"):
            continue
        try:
            src = inspect.getsource(fn)
        except (OSError, TypeError):        # pragma: no cover - defensive
            continue
        if re.search(r"\bSELECT\b", src, re.IGNORECASE) and "signal.messages" in src:
            found.add(name)
    return found


def test_the_ledger_of_message_reads_is_pinned_both_ways():
    found = _read_methods_touching_messages()
    assert found, (
        "HARNESS BROKEN: the scan found no message-reading methods at all, so "
        "every assertion below would pass vacuously")
    declared = FILTERED_READS | set(EXEMPT_READS)
    assert found == declared, (
        f"the set of methods that SELECT from signal.messages changed.\n"
        f"  new (add to FILTERED_READS and apply not_excluded(), or to "
        f"EXEMPT_READS with a reason): {sorted(found - declared)}\n"
        f"  gone (drop from the ledger): {sorted(declared - found)}")


@pytest.mark.parametrize("name", sorted(FILTERED_READS))
def test_every_filtered_read_actually_calls_the_predicate(name):
    src = inspect.getsource(getattr(SignalDB, name))
    assert "not_excluded(" in src, (
        f"SignalDB.{name} SELECTs from signal.messages without the mute "
        f"predicate — muted rows leak through it")


def test_get_draft_refuses_a_device_sync_ECHO_not_just_a_draft(db):
    """🔴 The exemption's REASON, measured on the population it actually returns.

    `test_a_draft_really_has_no_group_id` only inspects a row that
    `draft_message()` created, so it can never see this: `is_outbound` alone
    also matches every device-sync echo of a message sent from the phone, and
    those DO carry a `group_id`. Before `send_state IS NOT NULL`, `get_draft`
    returned a muted group's body in full while `get_message` returned None for
    the very same row.
    """
    ids = _seed(db)
    db.exclude_group(GROUP_MUTE)
    # The seeded muted-group row is inbound; make an OUTBOUND one in that group,
    # exactly as a sync echo arrives — no send_state, because nothing drafted it.
    echo_id = db.upsert_message({
        "message_timestamp": 5000,
        "source_uuid": "66666666-6666-4666-8666-666666666666",
        "source_number": "+15550006", "source_name": "me",
        "message_type": "group_message", "body": "SECRET body in a muted group",
        "group_id": GROUP_MUTE, "is_outbound": True,
    })
    assert db.get_message(echo_id) is None, "positive control: get_message hides it"
    assert db.get_draft(echo_id) is None, (
        "get_draft returned a device-sync echo — so `is_outbound` alone is not "
        "'a draft', and the ledger's exemption reason is false")
    assert ids["mute"] is not None


def test_a_draft_really_has_no_group_id(db):
    """The draft surface's EXEMPTION, measured instead of asserted in prose.

    `EXEMPT_READS` claims drafts carry no `group_id`, so the mute predicate on
    them could only ever be a no-op. A comment is a claim like any other; this
    reads the column. If drafting to a group is ever added, this goes red and
    the exemption has to be revisited rather than inherited.
    """
    draft = db.draft_message(recipient="+15550009", body="drafted, never sent",
                             self_number="+15550000")
    rows = db.conn.rows("SELECT group_id FROM signal.messages WHERE id = ?",
                        (draft["id"],))
    assert rows and rows[0]["group_id"] is None


def test_the_ledger_would_notice_an_unfiltered_read():
    """Negative control on the ledger itself: can it go red?

    Without this, `test_the_ledger_...` passing proves only that the CURRENT
    code matches the CURRENT list — not that the check can ever fail.
    """
    found = _read_methods_touching_messages()
    pretend_new = found | {"list_archived_messages"}
    assert pretend_new != (FILTERED_READS | set(EXEMPT_READS))


# --------------------------------------------------------------------------- #
# Operator input decoding
# --------------------------------------------------------------------------- #
def test_decode_internal_id_round_trips_a_canonical_id():
    text = base64.b64encode(GROUP_MUTE).decode()
    assert consumer._decode_internal_id(text) == GROUP_MUTE
    assert consumer._fmt_group_id(GROUP_MUTE) == text


def test_fmt_group_id_emits_the_STANDARD_alphabet_not_urlsafe():
    """🔴 `GROUP_MUTE` cannot see this — its encoding contains no `+` or `/`.

    A mutant switching `_fmt_group_id` to `urlsafe_b64encode` SURVIVED, because
    every fixture that reached it was a repeated byte whose base64 has neither
    character. The output is what an operator pastes back into `mute`, so a
    urlsafe rendering round-trips through our own tolerant decoder and diverges
    from what the Signal API prints. Use bytes that CAN tell the two apart.
    """
    raw = b"\xfb\xff" * 16
    std = base64.b64encode(raw).decode()
    assert "+" in std and "/" in std, (
        "HARNESS BROKEN: this fixture cannot distinguish the two alphabets")
    assert consumer._fmt_group_id(raw) == std


def test_decode_internal_id_tolerates_the_urlsafe_alphabet():
    raw = b"\xfb\xff" * 16                      # encodes to '+/' repeated
    std = base64.b64encode(raw).decode()
    urlsafe = base64.urlsafe_b64encode(raw).decode()
    assert "+" in std or "/" in std, (
        "HARNESS BROKEN: this fixture does not exercise the +/ characters, so "
        "the urlsafe branch is never reached and this test proves nothing")
    assert consumer._decode_internal_id(urlsafe) == raw


@pytest.mark.parametrize("bad", [
    "",
    "   ",
    "not base64 at all!",
    GROUP_ID_FIELD_SHAPE,          # the `id` field, not `internal_id`
    "Team",                        # a display name that IS valid base64
    "deadbeef",                    # 6 bytes — decodes cleanly, is not a group id
])
def test_decode_internal_id_refuses_anything_non_canonical(bad):
    """🔴 The ingest decoder falls back to `str.encode()` rather than failing.

    That is right for a frame (never kill ingest over one field) and wrong for
    operator input, where it turns a typo into a mute of 32 bytes that match
    nothing — a filter that reports success and hides nothing. This pins the
    strict half; `_decode_group_id` keeps the lenient half.
    """
    with pytest.raises(ValueError):
        consumer._decode_internal_id(bad)


def test_a_NON_CANONICAL_32_byte_encoding_is_refused():
    """🔴 The only case that reaches the round-trip check — found by a mutant.

    Adding the 16/32 length check made every other bad input die on LENGTH, so
    disabling the round-trip check entirely left the suite GREEN: a guard with
    no case that reaches it. (Round 1's battery killed that mutant; round 2's
    did not, because round 2's own fix had made it unreachable. A fix round
    resets the gate.)

    Base64's last data character carries only 4 significant bits for a 2-byte
    tail, so the low 2 bits are ignored on decode: `v` and `v+1` decode to the
    SAME 32 bytes while only `v` re-encodes to itself. That is a string of the
    right length, decoding to the right size, which is still not what the API
    emitted — exactly what the round-trip check is for and nothing else catches.
    """
    alphabet = ("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
                "0123456789+/")
    canonical = base64.b64encode(GROUP_MUTE).decode()
    assert canonical.endswith("=") and canonical[-2] in alphabet
    v = alphabet.index(canonical[-2])
    assert v % 4 == 0, "HARNESS BROKEN: canonical tail already has low bits set"
    variant = canonical[:-2] + alphabet[v + 1] + "="

    # Positive control: it really is the same 32 bytes, so this is NOT caught by
    # the length check — it can only be caught by the round trip.
    assert base64.b64decode(variant) == GROUP_MUTE
    assert len(base64.b64decode(variant)) == 32
    assert variant != canonical

    with pytest.raises(ValueError, match="canonical"):
        consumer._decode_internal_id(variant)


def test_the_two_decoders_genuinely_disagree_on_the_same_input():
    """Positive control for the paragraph above: the leniency is real."""
    bad = "not base64 at all!"
    assert consumer._decode_group_id(bad) == bad.encode()
    with pytest.raises(ValueError):
        consumer._decode_internal_id(bad)


# --------------------------------------------------------------------------- #
# The group NAME — found while checking the claim that names are never stored
# --------------------------------------------------------------------------- #
def _group_envelope(*, group_info: dict) -> dict:
    return {
        "timestamp": 9000,
        "sourceUuid": "55555555-5555-4555-8555-555555555555",
        "sourceNumber": "+15550005",
        "sourceName": "Erin",
        "dataMessage": {"message": "hello", "timestamp": 9000,
                        "groupInfo": group_info},
    }


def test_the_parser_reads_groupName_which_is_what_real_envelopes_carry():
    """🔴 Regression: `groupInfo.name` was read and is never present.

    Measured on the live store before this fix: 34 of 34 stored group envelopes
    carried a non-empty `groupInfo.groupName`, and NONE carried `groupInfo.name`
    — so every group was stored with an empty name. Nothing caught it because no
    test and no output ever asserted on a group name.
    """
    parsed = consumer.parse_envelope(_group_envelope(group_info={
        "type": "DELIVER", "groupId": "IiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiI=",
        "revision": 3, "groupName": "Widget Testers",
    }))
    assert parsed.message["group_name"] == "Widget Testers"


def test_groupName_WINS_over_the_legacy_spelling():
    """The two keys hold DISTINCT values here, deliberately.

    A fixture setting both to the same string collapses the two operand orders
    into identical output, and an order-swap mutant then survives a fully green
    suite — which is exactly how two such mutants survived 416 tests in this
    same module's history.
    """
    parsed = consumer.parse_envelope(_group_envelope(group_info={
        "type": "DELIVER", "groupId": "IiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiI=",
        "revision": 3, "groupName": "Widget Testers", "name": "Sprocket Fans",
    }))
    assert parsed.message["group_name"] == "Widget Testers"


def test_the_legacy_spelling_is_still_honoured_when_it_is_the_only_one():
    parsed = consumer.parse_envelope(_group_envelope(group_info={
        "type": "DELIVER", "groupId": "IiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiI=",
        "revision": 3, "name": "Sprocket Fans",
    }))
    assert parsed.message["group_name"] == "Sprocket Fans"


def test_a_LATER_nameless_envelope_does_not_WIPE_a_stored_group_name(db):
    """🔴 Two envelopes, because one cannot see this.

    `upsert_group` binds `name or ""` (the column is NOT NULL), so `EXCLUDED.name`
    is never NULL — it is `''` — and the original `COALESCE(EXCLUDED.name, …)`
    could therefore never fire. It was dead code that READ as protection: a later
    frame for a known group carrying no name (a sync echo, an edit, a plain
    DELIVER with only `groupId`) overwrote the stored name with `''`. The name
    would appear and then silently revert, and nothing asserted on group names
    over time, so a mutant DELETING the whole COALESCE survived all 508 tests.
    """
    gid = "IiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiI="
    db.upsert_message(consumer.parse_envelope(_group_envelope(group_info={
        "type": "DELIVER", "groupId": gid, "revision": 3,
        "groupName": "Widget Testers"})).message)
    assert "Widget Testers" in {r["group_name"] for r in db.list_conversations()}

    # A second frame for the SAME group, carrying no name at all.
    second = _group_envelope(group_info={"type": "DELIVER", "groupId": gid,
                                         "revision": 4})
    second["timestamp"] = 9100
    second["dataMessage"]["timestamp"] = 9100
    db.upsert_message(consumer.parse_envelope(second).message)

    assert "Widget Testers" in {r["group_name"] for r in db.list_conversations()}, (
        "a nameless envelope wiped the stored group name back to ''")


def test_a_stored_group_keeps_the_name_the_envelope_carried(db):
    """End-to-end through `upsert_message` -> `upsert_group`, not just the parser.

    The parser and the DB layer were each clean on their own the last time this
    module shipped a defect; the bug lived in the seam. One fixture builds both.
    """
    parsed = consumer.parse_envelope(_group_envelope(group_info={
        "type": "DELIVER", "groupId": "IiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiI=",
        "revision": 3, "groupName": "Widget Testers",
    }))
    db.upsert_message(parsed.message)
    names = {r["group_name"] for r in db.list_conversations()}
    assert "Widget Testers" in names


# --------------------------------------------------------------------------- #
# Schema
# --------------------------------------------------------------------------- #
def test_the_mute_table_is_in_the_schema_and_survives_translation():
    ddl = [s for s in _signal_db.SCHEMA_STATEMENTS
           if "excluded_groups" in s and "CREATE TABLE" in s]
    assert len(ddl) == 1, "the mute table must be declared exactly once"
    import fakepg
    translated = fakepg.translate_ddl(ddl[0])
    assert translated and "BLOB" in translated, (
        "BYTEA must translate to BLOB or the hermetic suite is testing a "
        "different column type than production")


def test_the_mute_table_is_keyed_on_the_binary_id_not_the_name():
    """`signal.groups.name` is EMPTY for every group this consumer has stored.

    A name-keyed mute would match nothing while looking like a working filter,
    which is the exact shape of a silent zero.
    """
    ddl = next(s for s in _signal_db.SCHEMA_STATEMENTS
               if "excluded_groups" in s and "CREATE TABLE" in s)
    assert re.search(r"group_id\s+BYTEA\s+PRIMARY KEY", ddl), ddl
    # 🔴 Read the COLUMN LIST, not the statement. The previous version did
    # `ddl.split("(", 1)[1]`, which split on a `(` inside the COMMENT block
    # above the DDL, so `body` was prose; and its assertion was
    # `X or "group_id" in body` whose right side is unconditionally true. Two
    # independent ways of asserting nothing, in one line, both invisible.
    cols = ddl[ddl.index("CREATE TABLE"):]
    cols = cols[cols.index("(") + 1:cols.rindex(")")]
    assert "group_id" in cols
    assert "name" not in cols, (
        f"the mute list must not key on a group NAME — column list was: {cols}")
