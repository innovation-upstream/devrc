"""The envelope corpus: every shape signal-cli emits, parsed without I/O.

Driven by `fixtures/envelopes.json`, whose entries each carry their own `expect`
block — so adding a fixture adds coverage without touching this file.

Two harness guards come first, because a corpus-driven suite is exactly the shape
that passes vacuously when the loader breaks:

* the corpus must be non-empty and plausibly sized;
* the KINDS the corpus exercises must cover every `KIND_*` constant DERIVED from
  `consumer.py`'s source — so a new kind added to the module without a fixture
  fails here rather than shipping unexercised.
"""
import re
from pathlib import Path

import pytest

import consumer
from conftest import load_corpus

CORPUS = load_corpus()
MIN_FIXTURES = 12          # 15 today; a floor, not the contract


def _kind_constants() -> set:
    """Every KIND_* value, read out of consumer.py's SOURCE text."""
    src = Path(consumer.__file__).read_text(encoding="utf-8")
    found = set(re.findall(r'^KIND_[A-Z_]+ = "([a-z_]+)"', src, re.MULTILINE))
    if not found:
        raise AssertionError(
            "HARNESS BROKEN: no KIND_* constants parsed out of consumer.py — the "
            "coverage assertion below would be vacuous")
    return found


def test_corpus_is_loaded_and_plausibly_sized():
    assert len(CORPUS) >= MIN_FIXTURES
    assert all("envelope" in c and "expect" in c for c in CORPUS.values())


def test_corpus_covers_every_kind_the_module_can_emit():
    exercised = {consumer.parse_envelope(c["envelope"]).kind for c in CORPUS.values()}
    missing = _kind_constants() - exercised
    assert not missing, f"kinds with no fixture: {sorted(missing)}"


def test_corpus_values_are_pairwise_distinct():
    """A fixture that reuses another's timestamp cannot see a mutant that swaps them."""
    stamps = [c["envelope"]["timestamp"] for c in CORPUS.values()]
    assert len(set(stamps)) == len(stamps)
    uuids = [c["envelope"].get("sourceUuid") for c in CORPUS.values()]
    assert len(set(uuids)) == len(uuids)


@pytest.mark.parametrize("name", sorted(CORPUS))
def test_fixture_parses_to_its_expected_kind(name):
    case = CORPUS[name]
    event = consumer.parse_envelope(case["envelope"])
    assert event.kind == case["expect"]["kind"]


@pytest.mark.parametrize("name", sorted(CORPUS))
def test_fixture_message_fields(name):
    case = CORPUS[name]
    expected = case["expect"].get("message")
    if expected is None:
        return
    event = consumer.parse_envelope(case["envelope"])
    assert event.message is not None, f"{name}: expected a message payload"
    for field, want in expected.items():
        if field == "group_id_b64":
            import base64
            assert event.message["group_id"] == base64.b64decode(want)
        else:
            assert event.message[field] == want, f"{name}.{field}"


@pytest.mark.parametrize("name", sorted(CORPUS))
def test_fixture_reaction_fields(name):
    case = CORPUS[name]
    expected = case["expect"].get("reaction")
    if expected is None:
        return
    event = consumer.parse_envelope(case["envelope"])
    assert event.reaction is not None
    for field, want in expected.items():
        assert event.reaction[field] == want, f"{name}.{field}"


@pytest.mark.parametrize("name", sorted(CORPUS))
def test_fixture_attachment_fields(name):
    case = CORPUS[name]
    expected = case["expect"].get("attachments")
    if expected is None:
        return
    event = consumer.parse_envelope(case["envelope"])
    got = event.message["attachments"]
    assert len(got) == len(expected)
    for want, have in zip(expected, got):
        for field, value in want.items():
            assert have[field] == value, f"{name}.{field}"


@pytest.mark.parametrize("name", sorted(CORPUS))
def test_every_fixture_keeps_its_raw_envelope(name):
    """Nothing is dropped on the floor: the original JSON is always retained."""
    event = consumer.parse_envelope(CORPUS[name]["envelope"])
    assert event.raw_envelope
    assert str(CORPUS[name]["envelope"]["timestamp"]) in event.raw_envelope


# --------------------------------------------------------------------------- #
# The two cases the proposal calls out by name
# --------------------------------------------------------------------------- #
def test_sync_outbound_is_marked_outbound_and_keeps_the_destination():
    event = consumer.parse_envelope(CORPUS["sync_outbound"]["envelope"])
    assert event.kind == consumer.KIND_SYNC_OUTBOUND
    assert event.message["is_outbound"] is True
    assert event.message["dest_number"] == "+15550101"
    # The SOURCE of an outbound sync is the account itself, not the recipient —
    # getting this backwards would file every sent message under the wrong peer.
    assert event.message["source_number"] == "+15559090"


def test_unknown_message_type_is_surfaced_not_dropped():
    event = consumer.parse_envelope(CORPUS["unknown_type"]["envelope"])
    assert event.kind == consumer.KIND_UNKNOWN
    assert event.message is None and event.reaction is None
    assert event.notes and "callMessage" in event.notes[0]
    assert event.raw_envelope        # still retained for later forensics


def test_group_and_dm_are_distinguished():
    dm = consumer.parse_envelope(CORPUS["dm"]["envelope"])
    grp = consumer.parse_envelope(CORPUS["group"]["envelope"])
    assert dm.kind == consumer.KIND_MESSAGE and dm.message["group_id"] is None
    assert grp.kind == consumer.KIND_GROUP_MESSAGE and grp.message["group_id"]


# --------------------------------------------------------------------------- #
# Malformed input — skipped, never fatal
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("bad", [
    "not json at all",
    "[]",
    '{"method":"send","params":{"envelope":{}}}',
    '{"params":{}}',
])
def test_malformed_sse_payloads_raise_malformed_event(bad):
    with pytest.raises(consumer.MalformedEvent):
        consumer.parse_sse_payload(bad)


def test_wellformed_sse_payload_positive_control():
    """The rejections above are about the payloads, not a parser that says no to all."""
    import json
    ok = json.dumps({"method": "receive",
                     "params": {"envelope": CORPUS["dm"]["envelope"]}})
    assert consumer.parse_sse_payload(ok)["timestamp"] == 1723000000101


@pytest.mark.parametrize("bad", [
    [],
    "a string",
    {"dataMessage": {"message": "no timestamp anywhere"}},
])
def test_malformed_envelopes_raise_malformed_event(bad):
    with pytest.raises(consumer.MalformedEvent):
        consumer.parse_envelope(bad)


def test_sse_events_skips_comments_and_blank_lines():
    lines = [": keep-alive", "", "data: {\"a\": 1}", "event: message",
             b"data: {\"b\": 2}"]
    assert list(consumer.sse_events(lines)) == ['{"a": 1}', '{"b": 2}']
