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
import fakepg

ALICE = "11111111-1111-4111-8111-111111111111"


def _payload(ts, body="stream fixture", *, uuid=ALICE, attachments=()):
    env = {
        "source": "+15550101", "sourceNumber": "+15550101", "sourceUuid": uuid,
        "timestamp": ts,
        "dataMessage": {"timestamp": ts, "message": body,
                        "attachments": list(attachments)},
    }
    return json.dumps({"account": "+15559090", "envelope": env})


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
        "{this is not json",
        _payload(1723400000031, "after the bad one"),
    ])
    c = _consumer(db, streams)
    c.run(max_connections=1)
    assert c.stats[consumer.STAT_MALFORMED] == 1
    assert c.stats[consumer.STAT_STORED] == 1
    assert db.conn.count("messages") == 1


def test_unknown_kinds_are_counted_as_ignored_not_stored(db):
    typing = json.dumps({"account": "+15559090", "envelope": {
        "sourceUuid": ALICE, "timestamp": 1723400000041,
        "typingMessage": {"action": "STARTED"}}})
    c = _consumer(db, Streams([typing]))
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


class FailOnCommit:
    """A transient fault landing on the COMMIT rather than on the write."""

    def __init__(self, inner, failures):
        self._inner = inner
        self.remaining = failures
        self.commits_attempted = 0

    def commit(self):
        self.commits_attempted += 1
        if self.remaining > 0:
            self.remaining -= 1
            raise psycopg2.OperationalError("server closed the connection")
        return self._inner.commit()

    def __getattr__(self, name):
        return getattr(self._inner, name)


def test_a_transient_fault_ON_THE_COMMIT_does_not_drop_the_message(db):
    """🔴 The retry must not turn a loud failure into a silent one.

    The write and its commit used to be retried SEPARATELY. When the fault landed
    on the commit, recovery rolled back — discarding the row already written —
    and the retried bare `commit()` then committed an empty transaction and
    reported success. MEASURED before the fix: `rows=[]`, `stored=1`. The message
    was gone, the counter said otherwise, and the module docstring promised "the
    event is not dropped".
    """
    flaky = FailOnCommit(db, failures=1)
    c = _consumer(flaky, Streams([_payload(1723700000001, "must survive")]))
    c.run(max_connections=1)

    rows = db.conn.rows("SELECT body FROM signal.messages")
    assert [r["body"] for r in rows] == ["must survive"]
    assert c.stats[consumer.STAT_STORED] == 1
    assert c.stats[consumer.STAT_DB_RETRIES] == 1
    assert flaky.commits_attempted == 2       # failed once, then succeeded


def test_positive_control_the_commit_path_can_still_fail_for_real(db):
    """The rescue above is a retry, not a swallow: an endless fault still raises."""
    flaky = FailOnCommit(db, failures=99)
    c = _consumer(flaky, Streams([]), db_retries=2)
    with pytest.raises(psycopg2.OperationalError):
        c.handle_payload(_payload(1723700000011))
    assert db.conn.count("messages") == 0


def test_a_reaction_write_is_also_one_unit_with_its_commit(db):
    """The same hazard on the OTHER write paths, not just messages."""
    flaky = FailOnCommit(db, failures=1)
    reaction = json.dumps({"account": "+15559090", "envelope": {
        "sourceUuid": "33333333-3333-4333-8333-333333333333",
        "timestamp": 1723700000021,
        "dataMessage": {"timestamp": 1723700000021, "reaction": {
            "emoji": "🔥", "targetAuthorUuid": ALICE,
            "targetSentTimestamp": 1723700000020, "isRemove": False}}}})
    c = _consumer(flaky, Streams([reaction]))
    c.run(max_connections=1)
    assert db.conn.count("reactions") == 1


def test_a_persistent_postgres_failure_is_not_swallowed(db):
    """Retry must have a floor: an endless outage has to surface, not vanish."""
    flaky = FlakyDB(db, failures=99)
    c = _consumer(flaky, Streams([_payload(1723400000061)]), db_retries=2)
    with pytest.raises(psycopg2.OperationalError):
        c.handle_payload(_payload(1723400000061))
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
        c.handle_payload(_payload(1723400000071))
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
        # Mirrors the real key layout, INCLUDING the attachment id — a fake that
        # dropped it would hide the collision the real one exists to prevent.
        return (f"{kw['conversation']}/2026-01-01/"
                f"{kw['attachment_id']}_{kw['filename']}")


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
        c.handle_payload(_payload(1723400000151))
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


# --------------------------------------------------------------------------- #
# 🔴 The aborted-transaction zombie. `autocommit=False` + one failed statement =
# every later statement raises InFailedSqlTransaction until someone rolls back.
# --------------------------------------------------------------------------- #
def test_substrate_reproduces_postgres_transaction_abort(db):
    """INSTRUMENT VALIDATION — without this the recovery tests are theatre.

    sqlite alone happily continues after a failed statement. The substrate
    emulates the Postgres rule, so the tests below can actually reach the bug.
    """
    import sqlite3

    with pytest.raises(sqlite3.IntegrityError):
        db.conn.cursor().execute(
            "INSERT INTO signal.messages (message_timestamp, source_contact_id, "
            "message_type) VALUES (1723500000001, NULL, 'message')")
    # Every LATER statement now fails, on a connection that is otherwise fine.
    with pytest.raises(psycopg2.errors.InFailedSqlTransaction):
        db.conn.cursor().execute("SELECT 1 FROM signal.messages")
    db.rollback()                                     # ... and recovery clears it
    db.conn.cursor().execute("SELECT 1 FROM signal.messages")


def test_in_failed_sql_transaction_is_classified_transient():
    """It must be RETRIED, not escape into the reconnect handler.

    Left out of `TRANSIENT_DB_ERRORS`, the event after any failure escapes into
    `run()`'s broad `except`, is miscounted as a dropped stream, and the pod logs
    "reconnecting" forever while storing nothing.
    """
    assert issubclass(psycopg2.errors.InFailedSqlTransaction, Exception)
    assert any(issubclass(psycopg2.errors.InFailedSqlTransaction, cls)
               for cls in consumer.TRANSIENT_DB_ERRORS)


def test_a_failed_write_does_not_poison_the_next_event(db):
    """END TO END: a bad frame, then a good one, on ONE connection.

    This is the zombie-pod scenario. Before `recover()` existed, the good frame
    raised `InFailedSqlTransaction`, escaped into the reconnect handler and was
    counted as a dropped stream — for the life of the process.
    """
    import sqlite3

    # A statement fails on this connection — exactly as a real write can.
    with pytest.raises(sqlite3.IntegrityError):
        db.conn.cursor().execute(
            "INSERT INTO signal.messages (message_timestamp, source_contact_id, "
            "message_type) VALUES (1723500000011, NULL, 'message')")

    good = _payload(1723500000012, "stored after the failure")
    c = _consumer(db, Streams([good]))
    c.run(max_connections=1)

    assert db.conn.count("messages") == 1
    rows = db.conn.rows("SELECT body FROM signal.messages")
    assert rows[0]["body"] == "stored after the failure"
    assert c.stats[consumer.STAT_DB_RECOVERIES] >= 1
    assert c.stats[consumer.STAT_RECONNECTS] == 0      # NOT miscounted as a drop


def test_recover_rolls_back_rather_than_reconnecting_when_it_can(db):
    db.conn.aborted = True
    assert db.recover() == "rolled-back"
    assert db.conn.rollbacks == 1
    assert db.conn.aborted is False


def test_recover_reconnects_when_the_socket_is_gone(monkeypatch):
    """A rollback cannot fix a closed connection — recovery must re-open it."""
    import _signal_db

    fresh = fakepg.SqliteConn()
    opened = []

    class Dead:
        closed = 1
        rollbacks = 0

        def rollback(self):  # pragma: no cover - must not be reached
            raise AssertionError("rollback on a closed connection")

    target = _signal_db.SignalDB(dsn="postgres://u:p@h/mailbox")
    target.conn = Dead()

    def fake_connect():
        opened.append(True)
        target.conn = fresh

    monkeypatch.setattr(target, "_connect", fake_connect)
    assert target.recover() == "reconnected"
    assert opened == [True]
    assert target.conn is fresh


def test_recover_failure_is_counted_and_does_not_mask_the_cause(db):
    """If recovery itself fails, the ORIGINAL error still surfaces."""
    class Unrecoverable:
        def __init__(self, inner):
            self._inner = inner

        def recover(self):
            raise RuntimeError("kubectl port-forward is gone")

        def upsert_message(self, msg):
            raise psycopg2.OperationalError("server closed the connection")

        def __getattr__(self, name):
            return getattr(self._inner, name)

    c = _consumer(Unrecoverable(db), Streams([]), db_retries=2)
    with pytest.raises(psycopg2.OperationalError):
        c.handle_payload(_payload(1723500000021))
    assert c.stats[consumer.STAT_DB_RECOVERY_FAILURES] == 2
    assert c.stats[consumer.STAT_DB_RECOVERIES] == 0


def test_a_non_transient_error_still_recovers_the_connection(db):
    """A programming error is NOT retried — but the transaction must still clear."""
    class Broken:
        def __init__(self, inner):
            self._inner = inner
            self.recoveries = 0

        def recover(self):
            self.recoveries += 1
            return "rolled-back"

        def upsert_message(self, msg):
            raise psycopg2.ProgrammingError("column does not exist")

        def __getattr__(self, name):
            return getattr(self._inner, name)

    broken = Broken(db)
    c = _consumer(broken, Streams([]), db_retries=3)
    with pytest.raises(psycopg2.ProgrammingError):
        c.handle_payload(_payload(1723500000031))
    assert broken.recoveries == 1
    assert c.stats[consumer.STAT_DB_RETRIES] == 0     # not retried ...
    assert c.stats[consumer.STAT_DB_RECOVERIES] == 1  # ... but recovered


def test_a_remote_delete_frame_tombstones_its_target_END_TO_END(db):
    """🔴 Through the CONSUMER, not by calling the DB method directly.

    A mutation sweep disabled the `remote_delete` branch in `store()` and nothing
    failed: every retraction test called `apply_remote_delete()` itself, so the
    dispatch — the part that decides whether a retraction is honoured at all —
    was never exercised. Disabled, the frame falls through and is stored as an
    ordinary empty message beside the text it was meant to retract.
    """
    target_ts = 1723600000001
    original = _payload(target_ts, "please forget this")
    retraction = json.dumps({"account": "+15559090", "envelope": {
        "source": "+15550101", "sourceNumber": "+15550101", "sourceUuid": ALICE,
        "timestamp": target_ts + 10,
        "dataMessage": {"timestamp": target_ts + 10,
                        "remoteDelete": {"targetSentTimestamp": target_ts}},
    }})

    c = _consumer(db, Streams([original, retraction]))
    c.run(max_connections=1)

    rows = db.conn.rows("SELECT body, message_type FROM signal.messages")
    assert len(rows) == 1, "the retraction was stored as a message of its own"
    assert rows[0]["body"] is None
    assert rows[0]["message_type"] == "deleted"
    assert c.stats[consumer.STAT_STORED] == 2      # the message and the retraction


def test_positive_control_without_the_retraction_the_text_stays(db):
    """The `None` above only means something because this leaves the body intact."""
    c = _consumer(db, Streams([_payload(1723600000002, "kept on purpose")]))
    c.run(max_connections=1)
    assert db.conn.rows("SELECT body FROM signal.messages")[0]["body"] == \
        "kept on purpose"


def test_run_returns_its_counters(db):
    c = _consumer(db, Streams([_payload(1723400000131)]))
    stats = c.run(max_connections=1)
    assert set(stats) == {
        consumer.STAT_STORED, consumer.STAT_IGNORED, consumer.STAT_MALFORMED,
        consumer.STAT_RECONNECTS, consumer.STAT_DB_RETRIES,
        consumer.STAT_DB_RECOVERIES, consumer.STAT_DB_RECOVERY_FAILURES,
        consumer.STAT_ATTACHMENT_FAILURES,
    }
