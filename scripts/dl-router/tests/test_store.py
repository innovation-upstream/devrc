"""Store: alias upsert semantics, concurrent writers, migration, and the route
log. Uses temp SQLite files; no shared state between tests.
"""
from __future__ import annotations

import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from store import SCHEMA_VERSION, Store  # noqa: E402


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
