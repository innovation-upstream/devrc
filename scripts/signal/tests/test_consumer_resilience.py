"""What the daemon must survive. Every failure is INJECTED, nothing is live.

The four hazards from the proposal, each measured rather than asserted about:

* SSE disconnect → reconnect WITH redelivery: nothing lost, nothing duplicated;
* a malformed event: skipped and counted, the stream keeps going;
* Postgres briefly unavailable: retried, the event is not dropped;
* an attachment fetch failing: the MESSAGE row still stands.
"""
import json

import psycopg2
import pytest

import consumer

ALICE = "11111111-1111-4111-8111-111111111111"


def _payload(ts, body="stream fixture", *, uuid=ALICE, attachments=()):
    env = {
        "source": "+15550101", "sourceNumber": "+15550101", "sourceUuid": uuid,
        "timestamp": ts,
        "dataMessage": {"timestamp": ts, "message": body,
                        "attachments": list(attachments)},
    }
    return "data: " + json.dumps({"method": "receive", "params": {"envelope": env}})


class Streams:
    """A stream_factory that hands out a scripted sequence of connections.

    Each connection is a list of lines; a `RuntimeError` inside the list is
    RAISED mid-iteration, which is what an SSE disconnect looks like from here.
    """

    def __init__(self, *connections):
        self._connections = list(connections)
        self.opened = 0

    def __call__(self):
        self.opened += 1
        if not self._connections:
            return iter(())
        lines = self._connections.pop(0)

        def gen():
            for line in lines:
                if isinstance(line, Exception):
                    raise line
                yield line

        return gen()


def _consumer(db, streams, **kw):
    return consumer.SignalConsumer(db, stream_factory=streams,
                                   sleep=lambda _s: None, **kw)


# --------------------------------------------------------------------------- #
# Reconnect + redelivery
# --------------------------------------------------------------------------- #
def test_disconnect_midstream_reconnects_and_loses_nothing(db):
    streams = Streams(
        [_payload(1723400000001, "first"), RuntimeError("connection reset")],
        [_payload(1723400000001, "first"),      # redelivered after reconnect
         _payload(1723400000002, "second")],
    )
    c = _consumer(db, streams)
    c.run(max_connections=2)

    assert streams.opened == 2
    assert c.stats[consumer.STAT_RECONNECTS] == 1
    bodies = {r["body"] for r in db.conn.rows("SELECT body FROM signal.messages")}
    assert bodies == {"first", "second"}          # nothing lost ...
    assert db.conn.count("messages") == 2         # ... and nothing duplicated


def test_positive_control_the_stream_actually_stores_anything(db):
    """A 'nothing duplicated' claim is worthless if the pipe stores nothing."""
    c = _consumer(db, Streams([_payload(1723400000011, "solo")]))
    c.run(max_connections=1)
    assert db.conn.count("messages") == 1
    assert c.stats[consumer.STAT_STORED] == 1


def test_a_clean_stream_end_is_not_counted_as_a_reconnect(db):
    c = _consumer(db, Streams([_payload(1723400000021)]))
    c.run(max_connections=1)
    assert c.stats[consumer.STAT_RECONNECTS] == 0


# --------------------------------------------------------------------------- #
# Malformed events
# --------------------------------------------------------------------------- #
def test_malformed_event_is_skipped_and_the_stream_continues(db):
    streams = Streams([
        "data: {this is not json",
        _payload(1723400000031, "after the bad one"),
    ])
    c = _consumer(db, streams)
    c.run(max_connections=1)
    assert c.stats[consumer.STAT_MALFORMED] == 1
    assert c.stats[consumer.STAT_STORED] == 1
    assert db.conn.count("messages") == 1


def test_unknown_kinds_are_counted_as_ignored_not_stored(db):
    typing = json.dumps({"method": "receive", "params": {"envelope": {
        "sourceUuid": ALICE, "timestamp": 1723400000041,
        "typingMessage": {"action": "STARTED"}}}})
    c = _consumer(db, Streams(["data: " + typing]))
    c.run(max_connections=1)
    assert c.stats[consumer.STAT_IGNORED] == 1
    assert c.stats[consumer.STAT_STORED] == 0
    assert db.conn.count("messages") == 0


# --------------------------------------------------------------------------- #
# Postgres briefly unavailable
# --------------------------------------------------------------------------- #
class FlakyDB:
    """Wraps the real substrate; fails `upsert_message` `n` times first."""

    def __init__(self, inner, failures):
        self._inner = inner
        self.remaining = failures
        self.upserts = 0

    def upsert_message(self, msg):
        self.upserts += 1
        if self.remaining > 0:
            self.remaining -= 1
            raise psycopg2.OperationalError("server closed the connection unexpectedly")
        return self._inner.upsert_message(msg)

    def __getattr__(self, name):
        return getattr(self._inner, name)


def test_transient_postgres_failure_is_retried_and_the_event_survives(db):
    flaky = FlakyDB(db, failures=2)
    c = _consumer(flaky, Streams([_payload(1723400000051, "survived the retry")]),
                  db_retries=3)
    c.run(max_connections=1)
    assert flaky.upserts == 3                       # two failures, then success
    assert c.stats[consumer.STAT_DB_RETRIES] == 2
    assert db.conn.count("messages") == 1


def test_a_persistent_postgres_failure_is_not_swallowed(db):
    """Retry must have a floor: an endless outage has to surface, not vanish."""
    flaky = FlakyDB(db, failures=99)
    c = _consumer(flaky, Streams([_payload(1723400000061)]), db_retries=2)
    with pytest.raises(psycopg2.OperationalError):
        c.handle_payload(_payload(1723400000061)[len("data: "):])
    assert c.stats[consumer.STAT_DB_RETRIES] == 2


def test_a_programming_error_is_not_retried(db):
    """Only TRANSIENT faults are retried — a bug must fail on the first attempt."""
    class Broken(FlakyDB):
        def upsert_message(self, msg):
            self.upserts += 1
            raise psycopg2.ProgrammingError("column does not exist")

    broken = Broken(db, failures=0)
    c = _consumer(broken, Streams([]), db_retries=5)
    with pytest.raises(psycopg2.ProgrammingError):
        c.handle_payload(_payload(1723400000071)[len("data: "):])
    assert broken.upserts == 1
    assert c.stats[consumer.STAT_DB_RETRIES] == 0


# --------------------------------------------------------------------------- #
# Attachment failures are isolated from the message write
# --------------------------------------------------------------------------- #
class FakeMinio:
    bucket = "signal-attachments"

    def __init__(self):
        self.calls = []

    def put_attachment(self, **kw):
        self.calls.append(kw)
        return f"{kw['conversation']}/2026-01-01/{kw['filename']}"


def _attachment_payload(ts, att_id="att-resilience"):
    return _payload(ts, "with an attachment", attachments=[
        {"id": att_id, "contentType": "image/png", "filename": "shot.png",
         "size": 12}])


def test_attachment_fetch_failure_does_not_roll_back_the_message(db):
    def boom(_attachment_id):
        raise TimeoutError("attachment endpoint timed out")

    c = _consumer(db, Streams([_attachment_payload(1723400000081)]),
                  fetch_attachment=boom, minio=FakeMinio())
    c.run(max_connections=1)

    assert db.conn.count("messages") == 1           # the message SURVIVES
    assert c.stats[consumer.STAT_ATTACHMENT_FAILURES] == 1
    assert c.stats[consumer.STAT_STORED] == 1
    row = db.conn.rows("SELECT minio_key FROM signal.attachments")[0]
    assert row["minio_key"] is None                 # ... and is honestly unstamped


def test_positive_control_a_working_attachment_is_stamped(db):
    """The None above only means something because this path writes a key."""
    minio = FakeMinio()
    c = _consumer(db, Streams([_attachment_payload(1723400000091)]),
                  fetch_attachment=lambda _i: b"png-bytes", minio=minio)
    c.run(max_connections=1)
    assert len(minio.calls) == 1
    row = db.conn.rows("SELECT minio_bucket, minio_key FROM signal.attachments")[0]
    assert row["minio_bucket"] == "signal-attachments"
    assert row["minio_key"].endswith("shot.png")
    assert c.stats[consumer.STAT_ATTACHMENT_FAILURES] == 0


def test_one_failing_attachment_does_not_stop_the_others(db):
    payload = _payload(1723400000101, "two attachments", attachments=[
        {"id": "att-bad", "contentType": "image/png", "filename": "bad.png"},
        {"id": "att-good", "contentType": "image/png", "filename": "good.png"},
    ])

    def selective(attachment_id):
        if attachment_id == "att-bad":
            raise OSError("truncated download")
        return b"good-bytes"

    minio = FakeMinio()
    c = _consumer(db, Streams([payload]), fetch_attachment=selective, minio=minio)
    c.run(max_connections=1)
    assert c.stats[consumer.STAT_ATTACHMENT_FAILURES] == 1
    keyed = db.conn.rows("SELECT signal_attachment_id, minio_key "
                         "FROM signal.attachments ORDER BY signal_attachment_id")
    assert [r["signal_attachment_id"] for r in keyed] == ["att-bad", "att-good"]
    assert keyed[0]["minio_key"] is None
    assert keyed[1]["minio_key"].endswith("good.png")


def test_attachments_are_fetched_after_the_message_is_committed(db):
    """Ordering is the mechanism, so it is asserted directly."""
    order = []

    class Watched:
        def __init__(self, inner):
            self._inner = inner

        def upsert_message(self, msg):
            order.append("upsert")
            return self._inner.upsert_message(msg)

        def commit(self):
            order.append("commit")
            return self._inner.commit()

        def __getattr__(self, name):
            return getattr(self._inner, name)

    def fetch(_i):
        order.append("fetch")
        return b"bytes"

    c = _consumer(Watched(db), Streams([_attachment_payload(1723400000111)]),
                  fetch_attachment=fetch, minio=FakeMinio())
    c.run(max_connections=1)
    assert order.index("commit") < order.index("fetch")


def test_no_minio_configured_means_attachments_are_simply_not_fetched(db):
    c = _consumer(db, Streams([_attachment_payload(1723400000121)]))
    c.run(max_connections=1)
    assert db.conn.count("attachments") == 1
    assert c.stats[consumer.STAT_ATTACHMENT_FAILURES] == 0


def test_zero_retries_still_makes_one_attempt(db):
    """`db_retries=0` means "do not RETRY", not "do not CALL".

    It used to skip the loop and then `raise last` with `last` still None, so a
    misconfigured consumer died with `TypeError: exceptions must derive from
    BaseException` from inside the retry helper — an error naming neither the
    configuration nor the database.
    """
    c = _consumer(db, Streams([_payload(1723400000141, "attempted once")]),
                  db_retries=0)
    c.run(max_connections=1)
    assert db.conn.count("messages") == 1
    assert c.stats[consumer.STAT_DB_RETRIES] == 0


def test_zero_retries_propagates_the_real_database_error(db):
    """... and the failure that surfaces is the DB's, not a TypeError."""
    flaky = FlakyDB(db, failures=1)
    c = _consumer(flaky, Streams([]), db_retries=0)
    with pytest.raises(psycopg2.OperationalError):
        c.handle_payload(_payload(1723400000151)[len("data: "):])
    assert flaky.upserts == 1


def test_run_without_a_stream_factory_fails_loudly_instead_of_spinning(db):
    """A configuration fault must not be laundered into an endless reconnect.

    Inside the loop a `None` factory raises `TypeError: 'NoneType' object is not
    callable`, which the reconnect handler catches — with the daemon's default
    `max_connections=None` that is an infinite "stream ended; reconnecting" spin.
    """
    c = consumer.SignalConsumer(db, sleep=lambda _s: None)
    with pytest.raises(RuntimeError) as exc:
        c.run(max_connections=1)
    assert "stream_factory" in str(exc.value)
    assert c.stats[consumer.STAT_RECONNECTS] == 0      # not swallowed into a retry


def test_run_returns_its_counters(db):
    c = _consumer(db, Streams([_payload(1723400000131)]))
    stats = c.run(max_connections=1)
    assert set(stats) == {
        consumer.STAT_STORED, consumer.STAT_IGNORED, consumer.STAT_MALFORMED,
        consumer.STAT_RECONNECTS, consumer.STAT_DB_RETRIES,
        consumer.STAT_ATTACHMENT_FAILURES,
    }
