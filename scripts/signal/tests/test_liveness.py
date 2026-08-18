"""🔴 The signal this service did not have.

`signal-consumer` serves no HTTP, declared no probes, and emitted **0 log lines**
across 20h in which it successfully ingested 5 messages, 5 contacts, a group and
2 reactions. So "reaching nothing" and "working perfectly" produced byte-
identical observations and the row count was the only health signal in
existence — which is why the step-7 diagnosis took hours rather than a glance.

The tests that matter here are the ones that pin the heartbeat firing when
NOTHING ELSE IS HAPPENING. A heartbeat that only beats under traffic re-creates
the exact blind spot it was built to close.
"""
import json
import time

import pytest

import consumer
from test_consumer_resilience import Streams, _consumer, _payload


class FakeConsumer:
    """Just the surface `Heartbeat.payload()` reads."""

    def __init__(self, **over):
        self.stream_connected = over.pop("stream_connected", True)
        self.connections = over.pop("connections", 3)
        self.last_frame_at = over.pop("last_frame_at", None)
        self.stats = {
            consumer.STAT_RECONNECTS: over.pop("reconnects", 7),
            consumer.STAT_STORED: over.pop("stored", 11),
            consumer.STAT_MALFORMED: over.pop("malformed", 5),
        }


def _hb_path(tmp_path):
    return str(tmp_path / "hb.json")


# --------------------------------------------------------------------------- #
# The freshness predicate — ONE definition, and `None` is not "fine"

def test_a_MISSING_heartbeat_is_NOT_fresh():
    """The state the whole feature exists to catch must not default to healthy.

    A consumer that never wrote a heartbeat is indistinguishable from one that
    died before its first beat. Scoring an absent reading as fresh is a dead
    measuring apparatus reporting all-clear.
    """
    assert consumer.heartbeat_is_fresh(None) is False


def test_freshness_is_bounded_at_BOTH_ends_of_the_window():
    """Measured at a boundary AND a middle, not one point."""
    assert consumer.heartbeat_is_fresh(0.0, max_age=100) is True
    assert consumer.heartbeat_is_fresh(50.0, max_age=100) is True
    assert consumer.heartbeat_is_fresh(100.0, max_age=100) is True    # inclusive
    assert consumer.heartbeat_is_fresh(100.001, max_age=100) is False
    assert consumer.heartbeat_is_fresh(10_000.0, max_age=100) is False


def test_the_default_max_age_is_a_MULTIPLE_of_the_write_interval():
    """A probe tripping at less than a few ticks flaps on scheduling jitter.

    Pins the RELATIONSHIP, not the literals — the numbers may be retuned, but a
    max-age at or below one interval means a healthy pod gets restarted for
    being a moment late, and that is never the intent.
    """
    assert consumer.HEARTBEAT_MAX_AGE >= 2 * consumer.HEARTBEAT_INTERVAL


# --------------------------------------------------------------------------- #
# The file sink — the liveness contract

def test_a_beat_writes_a_readable_file_and_its_age_is_measured(tmp_path):
    path = _hb_path(tmp_path)
    beat = consumer.Heartbeat(FakeConsumer(), path=path, clock=lambda: 1000.0)
    beat.tick()

    got = consumer.read_heartbeat_file(path, now=1005.0)
    assert got is not None
    assert got["age_seconds"] == pytest.approx(5.0)
    assert got["stream_connected"] is True
    assert got["reconnects"] == 7
    assert got["stored"] == 11
    assert got["connections"] == 3


def test_a_DISCONNECTED_consumer_reports_stream_connected_FALSE(tmp_path):
    """🔴 The health row must not claim a connection that does not exist.

    Every other fixture here sets `stream_connected=True`, which makes a mutant
    that hardcodes the field invisible — it survived a green 443-test run. The
    control is mechanical: feed the value the constant CANNOT equal and watch the
    output move. A consumer that always reports "connected" is exactly the
    confident lie this whole module exists to stop telling.
    """
    path = _hb_path(tmp_path)
    beat = consumer.Heartbeat(FakeConsumer(stream_connected=False), path=path)
    beat.tick()
    assert consumer.read_heartbeat_file(path)["stream_connected"] is False


def test_stream_state_is_read_LIVE_at_each_beat_not_frozen_at_construction(tmp_path):
    """The flag flips as streams open and drop; a beat must report NOW."""
    path = _hb_path(tmp_path)
    fake = FakeConsumer(stream_connected=True)
    beat = consumer.Heartbeat(fake, path=path)
    beat.tick()
    assert consumer.read_heartbeat_file(path)["stream_connected"] is True
    fake.stream_connected = False
    beat.tick()
    assert consumer.read_heartbeat_file(path)["stream_connected"] is False


def test_the_file_write_is_ATOMIC_so_a_probe_cannot_read_a_torn_beat(tmp_path):
    """Pinned structurally: after the write, no temp file survives and the
    target parses. A probe that read a half-written file would be scored as
    "no heartbeat" and restart a healthy pod — an outage caused by the
    instrument."""
    path = _hb_path(tmp_path)
    beat = consumer.Heartbeat(FakeConsumer(), path=path)
    beat.tick()
    leftovers = [p.name for p in tmp_path.iterdir() if p.name != "hb.json"]
    assert leftovers == []
    json.loads((tmp_path / "hb.json").read_text())


@pytest.mark.parametrize("content", ["", "{", "not json", '{"no_written_at": 1}'])
def test_an_UNREADABLE_heartbeat_reads_as_ABSENT_not_as_healthy(tmp_path, content):
    """Absent, truncated and garbage all mean "no current measurement"."""
    path = tmp_path / "hb.json"
    path.write_text(content)
    assert consumer.read_heartbeat_file(str(path)) is None
    assert consumer.heartbeat_is_fresh(None) is False


def test_a_MISSING_file_reads_as_absent(tmp_path):
    assert consumer.read_heartbeat_file(str(tmp_path / "nope.json")) is None


# --------------------------------------------------------------------------- #
# 🔴 The two sinks answer different questions, and the DB one is ALLOWED to fail

def test_a_POSTGRES_OUTAGE_DOES_NOT_BREAK_LIVENESS(tmp_path):
    """🔴 THE anti-cascade property.

    Keying a k8s restart on Postgres reachability turns a database blip into a
    restart storm that takes down a consumer which was working fine. The file
    is written FIRST and unconditionally; the row failure is counted, printed
    and swallowed.
    """
    path = _hb_path(tmp_path)

    def exploding_db():
        raise RuntimeError("postgres is down")

    beat = consumer.Heartbeat(FakeConsumer(), db_factory=exploding_db,
                              path=path, clock=lambda: 500.0)
    beat.tick()          # must NOT raise

    got = consumer.read_heartbeat_file(path, now=501.0)
    assert got is not None, "the liveness file must survive a DB outage"
    assert consumer.heartbeat_is_fresh(got["age_seconds"], max_age=60) is True
    assert beat.row_failures == 1


def test_the_row_IS_written_when_the_database_is_healthy(tmp_path):
    """POSITIVE CONTROL for the test above.

    Without this, `row_failures == 1` is indistinguishable from a heartbeat
    wired to nothing — the assertion would hold with the row write deleted.
    """
    written = []

    class FakeDB:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def record_heartbeat(self, hb): written.append(hb)
        def commit(self): written.append("commit")

    beat = consumer.Heartbeat(FakeConsumer(), db_factory=FakeDB,
                              path=_hb_path(tmp_path))
    beat.tick()
    assert beat.row_failures == 0
    assert len(written) == 2 and written[1] == "commit"
    assert written[0]["stored"] == 11


# --------------------------------------------------------------------------- #
# 🔴 The property the whole feature turns on

def test_the_heartbeat_BEATS_WHILE_THE_STREAM_IS_IDLE(tmp_path):
    """🔴 THE POINT. `run()` blocks in `iter_frames` — on an idle account, for
    HOURS. A heartbeat written per frame fires only when traffic exists, i.e.
    only in the case that never needed one. This drives beats with ZERO frames
    and asserts the reading still advances."""
    path = _hb_path(tmp_path)
    now = {"t": 0.0}
    beat = consumer.Heartbeat(FakeConsumer(stored=0, last_frame_at=None),
                              path=path, clock=lambda: now["t"])

    beat.tick()
    first = consumer.read_heartbeat_file(path, now=now["t"])
    now["t"] = 120.0
    beat.tick()                                   # still no frames at all
    second = consumer.read_heartbeat_file(path, now=now["t"])

    assert first["written_at"] == 0.0
    assert second["written_at"] == 120.0          # it advanced with no traffic
    assert second["stored"] == 0
    assert consumer.heartbeat_is_fresh(second["age_seconds"], max_age=60) is True


def test_an_IDLE_account_is_not_reported_unhealthy_for_having_no_frames(tmp_path):
    """`last_frame_at` is diagnosis, NEVER a liveness input.

    An idle Signal account legitimately sends nothing for hours. If silence fed
    the probe, a quiet weekend would restart the pod repeatedly.
    """
    path = _hb_path(tmp_path)
    beat = consumer.Heartbeat(FakeConsumer(last_frame_at=None, stored=0),
                              path=path, clock=lambda: 900.0)
    beat.tick()
    got = consumer.read_heartbeat_file(path, now=901.0)
    assert got["last_frame_at"] is None
    assert consumer.heartbeat_is_fresh(got["age_seconds"], max_age=60) is True


def test_a_STALE_heartbeat_goes_unhealthy(tmp_path):
    """Negative control: the probe CAN go red, or it is testing nothing."""
    path = _hb_path(tmp_path)
    beat = consumer.Heartbeat(FakeConsumer(), path=path, clock=lambda: 0.0)
    beat.tick()
    got = consumer.read_heartbeat_file(path, now=10_000.0)
    assert consumer.heartbeat_is_fresh(got["age_seconds"], max_age=120) is False


# --------------------------------------------------------------------------- #
# Thread behaviour

def test_start_BEATS_ONCE_BEFORE_WAITING(tmp_path):
    """Otherwise a probe firing inside the first interval sees no file and
    restarts a pod that had merely just booted."""
    path = _hb_path(tmp_path)
    beat = consumer.Heartbeat(FakeConsumer(), path=path, interval=3600)
    try:
        beat.start()
        assert consumer.read_heartbeat_file(path) is not None
        assert beat.ticks >= 1
    finally:
        beat.stop()


def test_the_thread_SURVIVES_a_beat_that_fails_AFTER_it_started(tmp_path):
    """A thread that dies on the first transient is a liveness signal that
    reports death for exactly the fault it was supposed to ride out.

    The first beat succeeds; the sink is then pointed somewhere unwritable, so
    every LATER beat raises. Deterministic on purpose — an earlier version of
    this test deleted the directory out from under a 10ms thread and raced it,
    which made the TEST flaky and said nothing about the code.
    """
    path = str(tmp_path / "hb.json")
    beat = consumer.Heartbeat(FakeConsumer(), path=path, interval=0.01)
    try:
        beat.start()
        assert consumer.read_heartbeat_file(path) is not None
        beat._path = "/nonexistent-dir/hb.json"      # every later write fails
        # Let any beat already IN FLIGHT finish against the old path before
        # snapshotting. Without this settle the counters race the swap — which is
        # how the first two versions of this test were green for the wrong reason.
        time.sleep(0.1)                               # >= several intervals
        # 🔴 Assert on ATTEMPTS, not ticks. `ticks` counts SUCCESSFUL writes, so
        # once the sink is broken it can never grow; asserting the loop still
        # runs therefore has to read the counter that increments BEFORE the I/O.
        attempts_before = beat.attempts
        ticks_before = beat.ticks
        deadline = time.time() + 2
        while beat.attempts <= attempts_before + 1 and time.time() < deadline:
            time.sleep(0.01)
        assert beat.attempts > attempts_before + 1, "the loop stopped trying"
        assert beat.ticks == ticks_before, "a write to an unwritable path 'succeeded'"
        assert beat._thread is not None and beat._thread.is_alive()
    finally:
        beat.stop()


def test_an_UNWRITABLE_path_FAILS_FAST_at_start_rather_than_pretending(tmp_path):
    """🔴 The deliberate asymmetry, and it is the opposite of the test above.

    A path that can never be written is a CONFIGURATION fault, not a transient,
    and this module's rule is that a configuration fault must not be laundered
    into a retry — a consumer that silently loops writing nowhere would report
    itself dead forever while looking like it was trying. It must fail loudly at
    startup, where the operator sees it. Transients, once running, are survived.
    """
    beat = consumer.Heartbeat(FakeConsumer(), path="/nonexistent-dir/hb.json")
    with pytest.raises(OSError):
        beat.start()


def test_stop_is_idempotent_and_safe_before_start(tmp_path):
    consumer.Heartbeat(FakeConsumer(), path=_hb_path(tmp_path)).stop()


# --------------------------------------------------------------------------- #
# 🔴 THE SEAM. Everything above drives `Heartbeat` against a FAKE consumer, and
# `run()` is tested elsewhere against a real one. Both can be green while the
# attribute names drift apart — the heartbeat would then report a frozen
# `stream_connected=False` forever and nobody's test would notice, because no
# fixture ever loads BOTH surfaces. That is the shape of the last defect this
# pipeline shipped. These build the combined state.

def test_run_MOVES_the_state_the_heartbeat_reports(db):
    """A REAL consumer through a REAL run(), read by a REAL Heartbeat."""
    c = _consumer(db, Streams([_payload(1723900000001, "hello")]))

    # Before running: not connected, nothing seen.
    assert c.stream_connected is False
    assert c.last_frame_at is None
    assert c.connections == 0

    c.run(max_connections=1)

    # After a completed connection the flag must be BACK to false — the `finally`
    # is what guarantees a dropped stream cannot leave the row asserting a
    # connection that ended.
    assert c.stream_connected is False
    assert c.connections == 1
    assert c.last_frame_at is not None, "run() never recorded a frame arrival"


def test_the_heartbeat_reads_a_REAL_consumers_counters(db, tmp_path):
    """Names, not just shapes: a rename on either side breaks this."""
    c = _consumer(db, Streams([_payload(1723900000002, "hi")]))
    c.run(max_connections=1)

    path = _hb_path(tmp_path)
    consumer.Heartbeat(c, path=path).tick()
    got = consumer.read_heartbeat_file(path)

    assert got["connections"] == c.connections == 1
    assert got["stored"] == c.stats[consumer.STAT_STORED]
    assert got["reconnects"] == c.stats[consumer.STAT_RECONNECTS]
    assert got["last_frame_at"] == c.last_frame_at
    assert got["stored"] >= 1, "the fixture stored nothing — the seam proves nothing"


def test_a_DROPPED_stream_leaves_connected_FALSE_and_counts_the_reconnect(db):
    """The failure direction: an exception mid-stream must not strand the flag
    at True, or the health row advertises a connection that is gone."""
    c = _consumer(db, Streams([RuntimeError("connection reset")]))
    c.run(max_connections=1)
    assert c.stream_connected is False
    assert c.stats[consumer.STAT_RECONNECTS] == 1


def test_a_factory_that_RAISES_never_claims_the_stream_was_connected(db):
    """🔴 Pins the ORDER of two adjacent lines, which a comment asserted and
    nothing tested.

    `stream_connected` is set AFTER `self._stream_factory()` returns, because a
    factory that raises never opened anything — signal-api down, DNS gone, auth
    rejected. Setting the flag first makes the health row assert a connection
    that was never established, which is the precise failure this row exists to
    expose. Hoisting the assignment one line up survived a green 448-test run.

    The factory records what the flag said AT THE MOMENT it ran, then fails.
    """
    seen = []

    def exploding_factory():
        seen.append(c.stream_connected)
        raise RuntimeError("signal-api unreachable")

    c = _consumer(db, exploding_factory)
    c.run(max_connections=2)

    assert seen == [False, False], (
        "the consumer claimed stream_connected before the stream existed")
    assert c.stream_connected is False
    assert c.stats[consumer.STAT_RECONNECTS] == 2


# --------------------------------------------------------------------------- #
# 🔴 THE PROBE MUST NOT TOUCH POSTGRES. `main()` runs every other command inside
# `with SignalDB()`, which connects AND runs ensure_schema(). Routing the probe
# through that would make k8s liveness depend on the database — a blip would
# restart a healthy consumer, which is precisely the cascade the two-sink design
# avoids in the daemon. It was reintroduced in the CLI once already.

def test_health_answers_WITHOUT_opening_a_database_connection(tmp_path, monkeypatch, capsys):
    """Proven by making the database EXPLODE. If `health` touches it, this raises.

    A test that merely asserts the exit code would pass with the connection
    still being opened — the DB is reachable in most environments, so the
    dependency would be invisible exactly until the outage that matters.
    """
    path = str(tmp_path / "hb.json")
    monkeypatch.setenv("SIGNAL_HEARTBEAT_PATH", path)
    monkeypatch.setattr(consumer, "HEARTBEAT_PATH", path)

    def detonate(*a, **kw):
        raise AssertionError("health opened a database connection")

    monkeypatch.setattr(consumer, "SignalDB", detonate)

    consumer.Heartbeat(FakeConsumer(), path=path).tick()
    assert consumer.main(["health"]) == 0
    assert "HEALTHY" in capsys.readouterr().out


def test_health_exits_NONZERO_when_there_is_no_heartbeat(tmp_path, monkeypatch, capsys):
    """Negative control: the probe can go red, or it gates nothing.

    Without this, the test above is satisfied by a `health` that always exits 0.
    """
    path = str(tmp_path / "absent.json")
    monkeypatch.setattr(consumer, "HEARTBEAT_PATH", path)
    monkeypatch.setattr(consumer, "SignalDB",
                        lambda *a, **kw: (_ for _ in ()).throw(
                            AssertionError("health opened a database connection")))

    assert consumer.main(["health"]) == 1
    assert "UNHEALTHY" in capsys.readouterr().out


def test_health_exits_NONZERO_when_the_heartbeat_is_STALE(tmp_path, monkeypatch, capsys):
    """The state a probe actually fires on: present, but old."""
    path = str(tmp_path / "hb.json")
    monkeypatch.setattr(consumer, "HEARTBEAT_PATH", path)
    monkeypatch.setattr(consumer, "SignalDB",
                        lambda *a, **kw: (_ for _ in ()).throw(
                            AssertionError("health opened a database connection")))

    consumer.Heartbeat(FakeConsumer(), path=path,
                       clock=lambda: time.time() - 9999).tick()
    assert consumer.main(["health"]) == 1
    assert "UNHEALTHY" in capsys.readouterr().out
