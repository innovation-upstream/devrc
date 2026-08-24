"""Store: alias upsert semantics, concurrent writers, migration, and the route
log. Uses temp SQLite files; no shared state between tests.
"""
from __future__ import annotations

import ast
import sqlite3
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from store import SCHEMA_VERSION, Store, is_busy_error  # noqa: E402


# --- schema ---------------------------------------------------------------- #
def test_migrate_sets_the_user_version(store):
    assert store.version() == SCHEMA_VERSION


def test_migrate_is_idempotent(tmp_path, clock):
    path = tmp_path / "s.sqlite3"
    first = Store(path, clock=clock)
    first.upsert_alias("janedoe", "Jane Doe")
    first.close()
    second = Store(path, clock=clock)
    assert second.version() == SCHEMA_VERSION
    assert second.alias("janedoe") == "Jane Doe"
    second.close()


def test_a_fresh_database_starts_at_version_zero(tmp_path):
    import sqlite3
    path = tmp_path / "raw.sqlite3"
    conn = sqlite3.connect(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 0
    conn.close()
    st = Store(path)
    assert st.version() == SCHEMA_VERSION
    st.close()


# --- aliases --------------------------------------------------------------- #
def test_alias_upsert_bumps_hits_and_repoints(store, clock):
    store.upsert_alias("janedoe", "Jane Doe")
    clock.advance(10)
    store.upsert_alias("janedoe", "john-smith")
    assert store.alias("janedoe") == "john-smith"
    row = store.conn.execute(
        "SELECT hits, created_at, updated_at FROM aliases "
        "WHERE key='janedoe' AND site=''").fetchone()
    assert row["hits"] == 2
    assert row["updated_at"] > row["created_at"]


def test_site_and_global_aliases_are_separate_rows(store):
    store.upsert_alias("jd", "Jane Doe", "")
    store.upsert_alias("jd", "john-smith", "example-site.test")
    assert store.alias("jd", "") == "Jane Doe"
    assert store.alias("jd", "example-site.test") == "john-smith"
    assert store.alias_count() == 2


def test_alias_map_is_keyed_the_way_the_matcher_expects(store):
    store.upsert_alias("jd", "Jane Doe", "example-site.test")
    assert store.alias_map() == {("jd", "example-site.test"): "Jane Doe"}


def test_delete_alias(store):
    store.upsert_alias("jd", "Jane Doe")
    store.delete_alias("jd")
    assert store.alias("jd") is None


def test_missing_alias_is_none(store):
    assert store.alias("nothing") is None


# --- concurrency ----------------------------------------------------------- #
def test_concurrent_writers_do_not_lose_rows(tmp_path):
    st = Store(tmp_path / "concurrent.sqlite3")
    errors = []

    def writer(n):
        try:
            for i in range(25):
                st.upsert_alias(f"key{n}-{i}", "Jane Doe")
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=writer, args=(n,)) for n in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert st.alias_count() == 150
    st.close()


def test_concurrent_upserts_of_the_same_key_converge(tmp_path):
    st = Store(tmp_path / "same-key.sqlite3")

    def writer():
        for _ in range(30):
            st.upsert_alias("shared", "Jane Doe")

    threads = [threading.Thread(target=writer) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert st.alias("shared") == "Jane Doe"
    row = st.conn.execute("SELECT hits FROM aliases WHERE key='shared'").fetchone()
    assert row["hits"] == 120
    st.close()


# --- SQLITE_BUSY durability ------------------------------------------------- #
#
# The two tests above are the ones that FOUND this: they went red in CI with
# `OperationalError: database is locked`, and the row counts said what that
# error costs — a writer whose `busy_timeout` expires does not retry, so its
# thread dies and every remaining write it owed is silently never made. On the
# pre-fix store, 6 threads x 25 upserts with the busy_timeout dialled down lost
# 25 rows at 0.5s and 125 rows at 0.05s and below.
#
# The tests below pin that as a CONTRACT rather than a race. They hold SQLite's
# write lock from a second connection for a fixed span, so "the write was
# blocked past its busy_timeout" is arranged, not hoped for.

def _hold_write_lock(path, hold_seconds, acquired, released, stop=None):
    """Hold SQLite's write lock on `path`, then let go.

    `BEGIN IMMEDIATE` takes the write lock at once (rather than on first write),
    which is what makes the block deterministic instead of timing-dependent.
    `acquired` fires only once the lock is genuinely held, so the test never
    races the blocker.

    `stop` releases it EARLY. A test that only needs "the lock is held while I
    make this call" sets it as soon as the call returns, so the test costs what
    the call costs rather than `hold_seconds`; `hold_seconds` then serves as the
    upper bound that keeps a broken run from hanging. A test that needs the lock
    to free itself on a schedule passes no `stop` and gets the timed release.
    """
    conn = sqlite3.connect(str(path), timeout=30.0)
    try:
        conn.execute("BEGIN IMMEDIATE")
        acquired.set()
        if stop is None:
            time.sleep(hold_seconds)
        else:
            stop.wait(hold_seconds)
        conn.rollback()
    finally:
        conn.close()
        released.set()


def test_a_write_blocked_past_its_busy_timeout_still_lands(tmp_path):
    """THE durability contract: a contended write COMPLETES, never vanishes.

    busy_timeout is 20ms and the lock is held for 400ms — twenty times longer,
    so the first attempt is guaranteed to raise SQLITE_BUSY. Pre-fix that error
    reached the caller and the row was never written; the retry makes the write
    wait for the lock and land.
    """
    path = tmp_path / "held.sqlite3"
    st = Store(path, timeout=0.02)
    acquired, released = threading.Event(), threading.Event()
    blocker = threading.Thread(target=_hold_write_lock,
                               args=(path, 0.4, acquired, released))
    blocker.start()
    try:
        assert acquired.wait(10), "the blocker never took the write lock"
        started = time.monotonic()
        st.upsert_alias("contended", "Jane Doe")
        elapsed = time.monotonic() - started
    finally:
        blocker.join(60)
    assert st.alias("contended") == "Jane Doe"
    assert st.alias_count() == 1
    # The write really did wait for the lock rather than sailing through before
    # the blocker got there — otherwise this is a plain write test wearing a
    # concurrency name. Only a lower bound: load can only make it larger.
    assert elapsed >= 0.3, f"the write was never actually blocked ({elapsed:.3f}s)"
    st.close()


def test_without_the_retry_that_same_write_is_lost(tmp_path):
    """POSITIVE CONTROL for the test above — this harness CAN see the loss.

    `write_attempts=1` is exactly the pre-fix code path: one attempt, no retry.
    Same lock, same timeout, and the write is gone — so a green result in the
    previous test is the retry working, not the contention failing to happen.
    """
    path = tmp_path / "held-no-retry.sqlite3"
    st = Store(path, timeout=0.02, write_attempts=1)
    acquired, released, stop = (threading.Event(), threading.Event(),
                                threading.Event())
    blocker = threading.Thread(target=_hold_write_lock,
                               args=(path, 30.0, acquired, released, stop))
    blocker.start()
    try:
        assert acquired.wait(10), "the blocker never took the write lock"
        with pytest.raises(sqlite3.OperationalError) as excinfo:
            st.upsert_alias("contended", "Jane Doe")
    finally:
        stop.set()
        blocker.join(60)
    assert "locked" in str(excinfo.value)
    assert st.alias_count() == 0      # the row is GONE, not merely delayed
    st.close()


def test_six_writers_with_a_tiny_busy_timeout_still_land_every_row(tmp_path):
    """The CI failure, reproduced by shrinking the timeout instead of the box.

    Identical to `test_concurrent_writers_do_not_lose_rows` except that the
    busy_timeout is 5ms rather than 10s. On the pre-fix store this failed 3/3
    with 5 errors and 25 of 150 rows; the CI failure is the same shape with a
    10-second threshold and an I/O-stalled node supplying the contention.
    """
    st = Store(tmp_path / "tiny-timeout.sqlite3", timeout=0.005)
    errors = []

    def writer(n):
        try:
            for i in range(25):
                st.upsert_alias(f"key{n}-{i}", "Jane Doe")
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=writer, args=(n,)) for n in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert st.alias_count() == 150
    st.close()


def test_a_permanent_error_propagates_instead_of_being_retried(store):
    """🔴 The control that matters more than the happy path.

    A retry that cannot tell BUSY from a real defect turns an immediate
    traceback into a 30-second hang and then the same traceback — with the real
    error dressed up as contention. `no such table` must come straight back.
    """
    # Without this the timing bound below could pass vacuously: the claim is
    # "it returned in a fraction of the retry budget", which needs a budget.
    assert store._write_deadline >= 15.0
    store._write(lambda conn: conn.execute("DROP TABLE aliases"))
    started = time.monotonic()
    with pytest.raises(sqlite3.OperationalError) as excinfo:
        store.upsert_alias("jd", "Jane Doe")
    elapsed = time.monotonic() - started
    assert "no such table" in str(excinfo.value)
    # A fifth of the budget, so a contended CI node cannot turn "returned at
    # once" into a failure while a genuine retry (which would take the FULL
    # budget) still cannot slip under it.
    assert elapsed < store._write_deadline / 5, \
        f"a permanent error was retried for {elapsed:.2f}s"


def test_is_busy_error_reads_the_code_sqlite_set(store, tmp_path):
    """Both branches of the predicate, against REAL sqlite3 exceptions.

    Hand-built `OperationalError`s carry no `sqlite_errorcode`, so they would
    exercise the pre-3.11 message fallback rather than the structural path this
    interpreter actually takes.
    """
    with pytest.raises(sqlite3.OperationalError) as permanent:
        store.conn.execute("SELECT * FROM no_such_table")
    assert permanent.value.sqlite_errorcode == 1        # SQLITE_ERROR
    assert is_busy_error(permanent.value) is False

    path = tmp_path / "codes.sqlite3"
    st = Store(path, timeout=0.02, write_attempts=1)
    acquired, released, stop = (threading.Event(), threading.Event(),
                                threading.Event())
    blocker = threading.Thread(target=_hold_write_lock,
                               args=(path, 30.0, acquired, released, stop))
    blocker.start()
    try:
        assert acquired.wait(10), "the blocker never took the write lock"
        with pytest.raises(sqlite3.OperationalError) as busy:
            st.upsert_alias("k", "Jane Doe")
    finally:
        stop.set()
        blocker.join(60)
    assert busy.value.sqlite_errorcode == 5             # SQLITE_BUSY
    assert is_busy_error(busy.value) is True
    st.close()


def test_an_exhausted_retry_re_raises_rather_than_going_quiet(tmp_path):
    """A write that truly cannot land must still be LOUD.

    The retry is bounded; the whole point is that it turns a *transient* block
    into a completed write, not that it hides a permanent one. When the bounds
    run out the last BUSY error is re-raised — swallowing it would recreate the
    original bug with the error message removed as well as the row.
    """
    path = tmp_path / "never-free.sqlite3"
    st = Store(path, timeout=0.01, write_deadline=0.1)
    acquired, released, stop = (threading.Event(), threading.Event(),
                                threading.Event())
    # The lock is held until this test lets go, so "it gave up" cannot be the
    # lock quietly freeing itself. 60s is the hang-guard, never the schedule.
    blocker = threading.Thread(target=_hold_write_lock,
                               args=(path, 60.0, acquired, released, stop))
    blocker.start()
    try:
        assert acquired.wait(10), "the blocker never took the write lock"
        started = time.monotonic()
        with pytest.raises(sqlite3.OperationalError):
            st.upsert_alias("doomed", "Jane Doe")
        elapsed = time.monotonic() - started
    finally:
        stop.set()
        blocker.join(60)
    # It kept retrying for roughly the deadline rather than giving up on the
    # first attempt. There is no upper bound here on purpose: the lock was still
    # held when the call returned, which is the fact that matters, and any upper
    # bound would be an assertion about how loaded the machine is.
    assert elapsed >= 0.05, f"gave up after {elapsed:.3f}s — no retry happened"
    assert st.alias_count() == 0
    st.close()


def test_write_attempts_bounds_the_number_of_attempts_made(tmp_path):
    """`write_attempts=N` must mean exactly N attempts.

    Counted rather than timed: an off-by-one here shows up as a few tens of
    milliseconds, which no wall-clock assertion can separate from noise, and it
    silently widens or narrows the bound the sidecar's write latency rests on.
    The failures are real SQLITE_BUSY errors — the lock is genuinely held for
    the whole test.
    """
    path = tmp_path / "count.sqlite3"
    st = Store(path, timeout=0.01, write_deadline=30.0, write_attempts=3)
    attempts = []

    def work(conn):
        attempts.append(1)
        conn.execute("INSERT INTO host_prior (site, dir, ts) "
                     "VALUES ('example-site.test', 'Jane Doe', 0)")

    acquired, released, stop = (threading.Event(), threading.Event(),
                                threading.Event())
    blocker = threading.Thread(target=_hold_write_lock,
                               args=(path, 60.0, acquired, released, stop))
    blocker.start()
    try:
        assert acquired.wait(10), "the blocker never took the write lock"
        with pytest.raises(sqlite3.OperationalError):
            st._write(work)
    finally:
        stop.set()
        blocker.join(60)
    assert len(attempts) == 3
    st.close()


def test_a_retry_rolls_back_the_partial_transaction_first(store):
    """A multi-statement write must not apply its first statement TWICE.

    `record_screened` already does insert-then-read inside one transaction, and
    a write with two INSERTs is one commit away. Without the rollback the first
    statement's work survives into the retry and lands a second time — silent
    duplication, which is the same class of damage as the lost row this whole
    change is about, in the opposite direction.
    """
    class _Busy(sqlite3.OperationalError):
        # A real BUSY as far as `is_busy_error` is concerned: it reads
        # `sqlite_errorcode`, and a class attribute answers that. Arranging a
        # genuine mid-transaction BUSY is not something a test can schedule.
        sqlite_errorcode = 5

    raised = []

    def work(conn):
        conn.execute("INSERT INTO routes (ts, dir) VALUES (1, 'Jane Doe')")
        if not raised:
            raised.append(1)
            raise _Busy("database is locked")

    store._write(work)
    assert raised == [1], "the retry path was never exercised"
    assert len(store.recent_routes()) == 1


def test_every_mutating_method_routes_through_the_write_helper():
    """One rule, one place — an asserted ledger, not a pinned example.

    A new write method that does its own `conn.execute` + `commit()` would
    reintroduce exactly this bug at one site while every other site stays
    correct, and nothing else in the suite would notice. This fails when the
    set of mutating methods GROWS past the set that goes through `_write`.
    """
    source = (Path(__file__).resolve().parent.parent / "store.py").read_text(
        encoding="utf-8")
    tree = ast.parse(source)
    store_cls = next(node for node in tree.body
                     if isinstance(node, ast.ClassDef) and node.name == "Store")
    # `_write` IS the helper; `_run_migration_step` only ever runs inside one
    # (see `migrate`), and takes its connection as an argument for that reason.
    exempt = {"_write", "_run_migration_step"}
    mutating = ("INSERT INTO", "UPDATE ", "DELETE FROM", "ALTER TABLE",
                "DROP TABLE", "PRAGMA USER_VERSION=")
    offenders = []
    for node in store_cls.body:
        if not isinstance(node, ast.FunctionDef) or node.name in exempt:
            continue
        body = ast.get_source_segment(source, node) or ""
        statements = "".join(
            n.value for n in ast.walk(ast.parse(body))
            if isinstance(n, ast.Constant) and isinstance(n.value, str)).upper()
        if any(kw in statements for kw in mutating) \
                and "self._write(" not in body:
            offenders.append(node.name)
    assert offenders == []


# --- examples + routes ----------------------------------------------------- #
def test_examples_round_trip(store):
    store.add_example({"page": {"tags": ["Jane Doe"]}}, "Jane Doe",
                      auto_dir="other", created_new=True)
    ex = store.examples()[0]
    assert ex["chosen_dir"] == "Jane Doe"
    assert ex["auto_dir"] == "other"
    assert ex["created_new"] == 1


def test_route_log_is_newest_first_and_bounded(store):
    for i in range(5):
        store.log_route(dir_name=f"d{i}", confidence=i / 10, reason=f"r{i}")
    rows = store.recent_routes(3)
    assert [r["dir"] for r in rows] == ["d4", "d3", "d2"]


def test_route_log_round_trips_the_dup_payload(store):
    store.log_route(dir_name="Jane Doe", dup={"where": "library",
                                              "relpath": "john-smith/a.mp4"})
    assert store.recent_routes(1)[0]["dup"]["where"] == "library"


def test_route_log_auto_flag_is_boolean(store):
    store.log_route(dir_name="Jane Doe", auto=True)
    assert store.recent_routes(1)[0]["auto"] is True


# --- host prior ------------------------------------------------------------ #
def test_host_prior_upsert(store, clock):
    store.set_host_prior("example-site.test", "Jane Doe")
    clock.advance(5)
    store.set_host_prior("example-site.test", "john-smith")
    assert store.host_prior("example-site.test") == "john-smith"


def test_host_prior_ignores_an_empty_site(store):
    store.set_host_prior("", "Jane Doe")
    assert store.host_prior("") is None
