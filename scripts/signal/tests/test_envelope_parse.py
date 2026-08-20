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
MIN_FIXTURES = 12          # 16 today; a floor, not the contract


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
def test_fixture_remote_delete_fields(name):
    case = CORPUS[name]
    expected = case["expect"].get("remote_delete")
    if expected is None:
        return
    event = consumer.parse_envelope(case["envelope"])
    assert event.remote_delete is not None
    assert event.message is None          # NOT stored as an empty-bodied message
    for field, want in expected.items():
        assert event.remote_delete[field] == want, f"{name}.{field}"


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
def test_malformed_receive_frames_raise_malformed_event(bad):
    with pytest.raises(consumer.MalformedEvent):
        consumer.parse_receive_frame(bad)


def test_wellformed_receive_frame_positive_control():
    """The rejections above are about the payloads, not a parser that says no to all."""
    import json
    ok = json.dumps({"method": "receive",
                     "params": {"envelope": CORPUS["dm"]["envelope"]}})
    assert consumer.parse_receive_frame(ok)["timestamp"] == 1723000000101


def test_bbernhard_websocket_frame_shape_is_accepted():
    """The shape the server we deploy ACTUALLY sends: `{account, envelope}`.

    Read from upstream `src/api/api.go`: in json-rpc mode `/v1/receive/{number}`
    upgrades to a websocket and writes the signal-cli params object per message.
    An earlier revision parsed event-stream `data:` lines from another server, so
    nothing would ever have been ingested.
    """
    import json
    frame = json.dumps({"account": "+15559090",
                        "envelope": CORPUS["dm"]["envelope"]})
    assert consumer.parse_receive_frame(frame)["timestamp"] == 1723000000101


def test_receive_frame_accepts_an_already_decoded_dict():
    """The REST-array path decodes once and hands dicts straight through."""
    env = consumer.parse_receive_frame({"envelope": CORPUS["group"]["envelope"]})
    assert env["timestamp"] == 1723000000202


@pytest.mark.parametrize("bad", [
    [],
    "a string",
    {"dataMessage": {"message": "no timestamp anywhere"}},
])
def test_malformed_envelopes_raise_malformed_event(bad):
    with pytest.raises(consumer.MalformedEvent):
        consumer.parse_envelope(bad)


def test_iter_frames_yields_one_item_per_websocket_frame():
    """The websocket writes one text frame per message; bytes are decoded."""
    ws_frames = ['{"envelope": {"timestamp": 1}}', b'{"envelope": {"timestamp": 2}}']
    assert list(consumer.iter_frames(ws_frames)) == [
        '{"envelope": {"timestamp": 1}}', '{"envelope": {"timestamp": 2}}']


def test_iter_frames_passes_already_decoded_objects_through():
    frame = {"envelope": {"timestamp": 3}}
    assert list(consumer.iter_frames([frame])) == [frame]


def test_iter_frames_skips_blank_frames_and_passes_junk_through_to_be_counted():
    """A junk frame must reach `handle_payload` (which counts it), not vanish here."""
    out = list(consumer.iter_frames(["", "   ", "not json", "[unclosed"]))
    assert out == ["not json", "[unclosed"]


def test_receive_url_is_per_account_and_upgrades_scheme_for_websockets():
    base = "http://signal-api.signal.svc:8080"
    assert (consumer.receive_url("+15559090", api_url=base)
            == "http://signal-api.signal.svc:8080/v1/receive/+15559090")
    assert (consumer.receive_url("+15559090", api_url=base, websocket=True)
            == "ws://signal-api.signal.svc:8080/v1/receive/+15559090")
    assert consumer.receive_url("+1", api_url="https://x.example",
                                websocket=True).startswith("wss://")


def test_a_missing_websocket_package_fails_at_FACTORY_BUILD_not_in_the_loop(
        monkeypatch):
    """🔴 A missing dependency must not become an infinite silent reconnect.

    `run()` calls the factory once per connect and treats any exception from it
    as a dropped stream. With the import INSIDE `_open()`, an absent
    `websocket-client` produced `stream ended (No module named 'websocket');
    reconnecting` forever, zero rows — MEASURED at 25/25 connects. That is the
    same zombie mode `run()`'s hoisted stream-factory check exists to prevent.
    """
    import sys as _sys

    monkeypatch.setitem(_sys.modules, "websocket", None)
    with pytest.raises(RuntimeError) as exc:
        consumer.ws_stream_factory("+15559090")
    message = str(exc.value)
    assert "websocket-client" in message
    assert "requirements.txt" in message


def test_the_websocket_dependency_is_DECLARED_not_just_imported():
    """It was declared nowhere — the only trace was a trailing comment."""
    requirements = (Path(consumer.__file__).parent / "requirements.txt")
    assert requirements.is_file(), "scripts/signal/requirements.txt is missing"
    listed = {line.split("#")[0].strip().lower()
              for line in requirements.read_text(encoding="utf-8").splitlines()
              if line.strip() and not line.strip().startswith("#")}
    assert "websocket-client" in listed
    for also_imported in ("psycopg2-binary", "minio", "requests"):
        assert also_imported in listed, f"{also_imported} is imported but undeclared"


def test_the_factory_builds_when_the_package_IS_present(monkeypatch):
    """POSITIVE CONTROL: the refusal above is about the package, not the function."""
    import sys as _sys
    import types as _types

    fake = _types.ModuleType("websocket")
    fake.create_connection = lambda *a, **k: None      # never called here
    monkeypatch.setitem(_sys.modules, "websocket", fake)
    factory = consumer.ws_stream_factory("+15559090")
    assert callable(factory)


def test_no_rest_polling_factory_ships():
    """It was referenced nowhere and hot-loops if used (`run()` only sleeps on error)."""
    assert not hasattr(consumer, "rest_poll_stream_factory")


def test_receive_url_refuses_an_empty_account():
    """There is no global stream on this server — the path carries the number."""
    with pytest.raises(ValueError) as exc:
        consumer.receive_url("")
    assert "per-account" in str(exc.value)


def test_the_module_targets_the_bbernhard_route_table_only():
    """🔴 A ledger of the endpoints this module speaks, pinned to upstream's router.

    Upstream `src/main.go` registers exactly `v1.GET("/receive/:number")`,
    `v1.GET("/attachments/:attachment")` and `v2.POST("/send")`. There is no
    endpoint and no `/api/v1/events` — that path belongs to AsamK's native
    daemon, a DIFFERENT server. This test fails if anyone reintroduces one.
    """
    src = Path(consumer.__file__).read_text(encoding="utf-8")
    assert consumer.RECEIVE_PATH == "/v1/receive"
    assert consumer.ATTACHMENT_PATH == "/v1/attachments"
    assert consumer.SEND_PATH == "/v2/send"
    # Scanned as CODE, not as prose: the module's own comment explains why
    # `/api/v1/events` is wrong, and a naive substring check would trip on the
    # explanation. Quoted string literals and identifiers are what would make it
    # real again.
    for foreign in ('"/api/v1/events"', "'/api/v1/events'", "text/event-stream",
                    "EVENTS_PATH", "iter_lines("):
        assert foreign not in src, f"{foreign!r} belongs to a different server"


def test_no_stale_event_stream_WORDING_survives_anywhere_in_the_package():
    """🔴 The transport is documented in prose, and prose drifts silently.

    The guard above bans the foreign IDENTIFIERS, which is why eleven stale "SSE"
    mentions survived a purge and drifted for a whole round — including one in
    `_signal_db.py` that told a reader the device-sync echo "carries back through
    the SSE stream", a factual misstatement about the deployed transport sitting
    in the same file as the dedupe design that depends on it.

    Exactly ONE mention is allowed: the line in `consumer.py` that exists to say
    the endpoint does NOT exist. Anything else is drift.
    """
    package = Path(consumer.__file__).parent
    this_file = Path(__file__).name
    offenders = []
    for path in sorted(package.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        if path.name == this_file:
            continue          # the guard must quote the token it bans
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            # 🔴 WORD BOUNDARIES. A bare `"SSE" not in line` is a substring test,
            # and `PASSED` contains SSE — so an ordinary sentence about a test
            # passing was reported as stale transport wording. Measured: this
            # guard went red on `# … EVERY NAMED KILLER ACTUALLY RAN AND PASSED`
            # in an unrelated file. A guard that fires on a different word is the
            # mirror image of one satisfied by a different word: both make the
            # verdict a fact about spelling rather than about the hazard.
            if not re.search(r"\bSSE\b", line):
                continue
            if path.name == "consumer.py" and "no SSE endpoint anywhere" in line:
                continue                      # the disclaimer, deliberately kept
            offenders.append(f"{path.name}:{lineno}: {line.strip()[:80]}")
    assert not offenders, "stale event-stream wording:\n" + "\n".join(offenders)


def test_the_stale_wording_guard_does_not_fire_on_a_WORD_CONTAINING_sse():
    """🔴 NEGATIVE CONTROL, added because the guard did exactly this.

    `PASSED` contains SSE. A substring test therefore reported an ordinary
    sentence about a test passing as stale transport wording — and the pressure
    that creates is to reword the innocent line, which leaves the guard wrong
    and teaches the next person to route around it. Words that would trip it:
    PASSED, ASSESS, GLASSES, PASSENGER.
    """
    for innocent in ("            if \" PASSED\" in ln:",
                     "    # we ASSESS the result", "he wore GLASSES"):
        assert not re.search(r"\bSSE\b", innocent), (
            f"the guard's pattern fires on {innocent!r}, which is not about the "
            "transport at all")


def test_the_stale_wording_guard_can_actually_fire(tmp_path):
    """POSITIVE CONTROL: the scan above is not passing because it reads nothing."""
    (tmp_path / "drifted.py").write_text(
        '"""consumes the SSE stream"""\n', encoding="utf-8")
    hits = [line for path in tmp_path.rglob("*.py")
            for line in path.read_text(encoding="utf-8").splitlines()
            if "SSE" in line]
    assert len(hits) == 1
