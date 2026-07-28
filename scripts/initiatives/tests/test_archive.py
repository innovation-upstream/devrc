"""Unit tests for the standalone archived-lifecycle store (scripts/initiatives/archive.py).

Hermetic: NO real Postgres. A fake DB context-manager + cursor is injected via each
function's `db_factory` param, so archive/unarchive/list_archived/read_archived are exercised
against recorded SQL — never a live port-forward. Pins the contract the board depends on:
UPSERT refreshes archived_at (re-archive), DELETE removes, reads project the right columns,
and EVERYTHING is best-effort (a DB error → a failure result / empty list, NEVER an exception).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import archive  # noqa: E402


# --------------------------------------------------------------------------- #
# A fake MailDB-shaped context manager: `with factory() as db: db.conn` yields a
# connection whose `.cursor()` is a context manager recording every execute().
# --------------------------------------------------------------------------- #
class _FakeCursor:
    def __init__(self, conn):
        self._conn = conn

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self._conn.executed.append((" ".join(sql.split()), params))
        # emulate the read shapes the module expects
        low = sql.lower()
        if "to_regclass" in low:
            self._conn._last = [(self._conn.regclass,)]
        elif "select" in low and "from initiatives.archived" in low:
            self._conn._last = list(self._conn.rows)
        else:
            self._conn._last = []

    def fetchone(self):
        return self._conn._last[0] if self._conn._last else None

    def fetchall(self):
        return self._conn._last


class _FakeConn:
    def __init__(self, rows=None, regclass="initiatives.archived", raise_on=None):
        self.executed = []
        self.committed = 0
        self.rolled_back = 0
        self.rows = rows or []
        self.regclass = regclass
        self._last = []
        self._raise_on = raise_on  # a substring; execute raises if the SQL contains it

    def cursor(self, cursor_factory=None):
        if self._raise_on is not None:
            raise _FakeDBError(f"boom near {self._raise_on}")
        return _FakeCursor(self)

    def commit(self):
        self.committed += 1

    def rollback(self):
        self.rolled_back += 1


class _FakeDB:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _FakeDBError(Exception):
    pass


def _factory(conn):
    """A zero-arg callable returning a fresh context manager over `conn` (the MailDB shape)."""
    return lambda: _FakeDB(conn)


# --- archive (UPSERT) ------------------------------------------------------- #
def test_archive_upserts_and_commits():
    conn = _FakeConn()
    res = archive.archive("/repo/devrc", "board-archive", "Board archive P2", "done",
                          db_factory=_factory(conn))
    assert res == {"ok": True}
    assert conn.committed == 1
    # self-heals the table then runs the UPSERT with ON CONFLICT DO UPDATE
    joined = " || ".join(sql for sql, _ in conn.executed)
    assert "CREATE TABLE IF NOT EXISTS initiatives.archived" in joined
    assert "INSERT INTO initiatives.archived" in joined
    assert "ON CONFLICT (repo, slug) DO UPDATE" in joined
    # the row values are bound (not interpolated) — repo/slug/title/reason
    ins = next(p for sql, p in conn.executed if "INSERT INTO initiatives.archived" in sql)
    assert ins == ("/repo/devrc", "board-archive", "Board archive P2", "done")


def test_archive_defaults_reason_to_done_and_title_to_empty():
    conn = _FakeConn()
    archive.archive("/repo/devrc", "slug-x", db_factory=_factory(conn))
    ins = next(p for sql, p in conn.executed if "INSERT INTO initiatives.archived" in sql)
    assert ins == ("/repo/devrc", "slug-x", "", "done")


def test_archive_refresh_uses_now_and_excluded_on_conflict():
    # The UPSERT contract: re-archiving refreshes archived_at (now()) + reason + title.
    conn = _FakeConn()
    archive.archive("/repo/devrc", "slug-x", "T", "dropped", db_factory=_factory(conn))
    upsert = next(sql for sql, _ in conn.executed if "INSERT INTO initiatives.archived" in sql)
    assert "SET archived_at = now()" in upsert
    assert "reason = EXCLUDED.reason" in upsert
    assert "title = EXCLUDED.title" in upsert


def test_archive_missing_fields_is_a_failure_result_no_db():
    conn = _FakeConn()
    assert archive.archive("", "slug", db_factory=_factory(conn))["ok"] is False
    assert archive.archive("/repo", "", db_factory=_factory(conn))["ok"] is False
    assert conn.executed == []  # never touched the DB


def test_archive_never_raises_on_db_failure():
    conn = _FakeConn(raise_on="cursor")  # .cursor() blows up
    res = archive.archive("/repo/devrc", "slug", db_factory=_factory(conn))
    assert res["ok"] is False and "error" in res  # a failure RESULT, not an exception
    assert conn.committed == 0


def test_archive_never_raises_when_factory_itself_raises():
    def boom_factory():
        raise RuntimeError("no port-forward")
    res = archive.archive("/repo/devrc", "slug", db_factory=boom_factory)
    assert res["ok"] is False and res["error"] == "RuntimeError"


# --- unarchive (DELETE) ----------------------------------------------------- #
def test_unarchive_deletes_and_commits():
    conn = _FakeConn()
    res = archive.unarchive("/repo/devrc", "slug-x", db_factory=_factory(conn))
    assert res == {"ok": True}
    assert conn.committed == 1
    delete = next((p for sql, p in conn.executed
                   if "DELETE FROM initiatives.archived" in sql), None)
    assert delete == ("/repo/devrc", "slug-x")


def test_unarchive_missing_fields_is_failure_no_db():
    conn = _FakeConn()
    assert archive.unarchive("", "s", db_factory=_factory(conn))["ok"] is False
    assert conn.executed == []


def test_unarchive_never_raises_on_db_failure():
    conn = _FakeConn(raise_on="cursor")
    res = archive.unarchive("/repo/devrc", "slug", db_factory=_factory(conn))
    assert res["ok"] is False and "error" in res


# --- read_archived / list_archived (READ) ----------------------------------- #
def _sample_rows():
    return [
        {"repo": "/repo/devrc", "slug": "a", "title": "A", "reason": "done",
         "archived_at": "2026-07-22T10:00:00+00:00"},
        {"repo": "/repo/devrc", "slug": "b", "title": "B", "reason": "dropped",
         "archived_at": "2026-07-21T10:00:00+00:00"},
    ]


def test_read_archived_projects_rows():
    conn = _FakeConn(rows=_sample_rows())
    got = archive.read_archived(conn)
    assert [r["slug"] for r in got] == ["a", "b"]
    assert got[0]["reason"] == "done" and got[1]["reason"] == "dropped"
    # the SELECT pulls exactly the Done-view columns (incl. title, so an aged-out card renders)
    sel = next(sql for sql, _ in conn.executed if "FROM initiatives.archived" in sql
               and "SELECT" in sql)
    for col in ("repo", "slug", "title", "reason", "archived_at"):
        assert col in sel


def test_read_archived_missing_table_returns_empty():
    conn = _FakeConn(rows=_sample_rows(), regclass=None)  # to_regclass → NULL
    assert archive.read_archived(conn) == []


def test_read_archived_never_raises_and_rolls_back_on_error():
    # A DB error mid-read degrades to [] and rolls back so the connection stays usable.
    import psycopg2

    class _RaiseConn(_FakeConn):
        def cursor(self, cursor_factory=None):
            raise psycopg2.Error("read blew up")

    conn = _RaiseConn()
    assert archive.read_archived(conn) == []
    assert conn.rolled_back == 1


def test_list_archived_wraps_read_over_its_own_connection():
    conn = _FakeConn(rows=_sample_rows())
    got = archive.list_archived(db_factory=_factory(conn))
    assert [r["slug"] for r in got] == ["a", "b"]


def test_list_archived_never_raises_on_factory_failure():
    def boom_factory():
        raise RuntimeError("down")
    assert archive.list_archived(db_factory=boom_factory) == []
