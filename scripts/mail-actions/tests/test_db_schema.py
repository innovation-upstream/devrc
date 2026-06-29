"""SQL-level tests for the new _db.MailDB methods, using a mock psycopg2 connection.

Offline: no port-forward, no Postgres. We hand MailDB a fake connection whose
cursor records every executed (sql, params) so we can assert on the emitted SQL —
the thread_key migration, the supersede/close/insert statements, and the timestamp
guard living in the WHERE clause (not just in Python)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import _db  # noqa: E402


class FakeCursor:
    def __init__(self, conn):
        self._conn = conn
        self.rowcount = 0
        self._result = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self._conn.executed.append((" ".join(sql.split()), params))
        # let a test preload a rowcount/result for the next fetch
        self.rowcount = self._conn.next_rowcount
        self._result = self._conn.next_result

    def fetchall(self):
        return self._result

    def fetchone(self):
        return self._result[0] if self._result else None


class FakeConn:
    def __init__(self):
        self.executed = []
        self.commits = 0
        self.next_rowcount = 0
        self.next_result = []

    def cursor(self, cursor_factory=None):
        return FakeCursor(self)

    def commit(self):
        self.commits += 1


def _db_with_conn():
    db = _db.MailDB(dsn="postgres://u:p@h/mailbox")
    db.conn = FakeConn()
    return db, db.conn


def _sqls(conn):
    return [sql for sql, _ in conn.executed]


def test_ensure_schema_issues_add_column_migration():
    db, conn = _db_with_conn()
    db.ensure_schema()
    sqls = _sqls(conn)
    # The CREATE TABLE carries thread_key ...
    assert any("CREATE TABLE IF NOT EXISTS mail_actions" in s and "thread_key text" in s
               for s in sqls)
    # ... AND the idempotent migration for the pre-existing live table is issued.
    assert any("ALTER TABLE mail_actions ADD COLUMN IF NOT EXISTS thread_key text" in s
               for s in sqls)
    assert conn.commits == 1


def test_insert_action_includes_thread_key_column_and_param():
    db, conn = _db_with_conn()
    conn.next_rowcount = 1
    ok = db.insert_action({
        "mail_id": 1, "message_id": "<m>", "from_addr": "a@b.com", "subject": "s",
        "received_at": 100, "who": "w", "ask": "a", "deadline": None, "amount": None,
        "confidence": 0.9, "reason": "r", "thread_key": "tk1",
    })
    assert ok is True
    sql, params = conn.executed[-1]
    assert "thread_key" in sql
    assert "%(thread_key)s" in sql
    assert params["thread_key"] == "tk1"


def test_insert_action_defaults_thread_key_null_when_absent():
    db, conn = _db_with_conn()
    conn.next_rowcount = 1
    db.insert_action({
        "mail_id": 2, "message_id": "<m>", "from_addr": "a@b.com", "subject": "s",
        "received_at": 100, "who": "w", "ask": "a", "deadline": None, "amount": None,
        "confidence": 0.9, "reason": "r",
    })
    _sql, params = conn.executed[-1]
    assert params["thread_key"] is None


def test_supersede_open_actions_sql_has_timestamp_guard():
    db, conn = _db_with_conn()
    conn.next_rowcount = 1
    n = db.supersede_open_actions(thread_key="tk1", before_received_at=300)
    assert n == 1
    sql, params = conn.executed[-1]
    assert "UPDATE mail_actions SET status = 'superseded'" in sql
    assert "thread_key = %s" in sql
    assert "status = 'open'" in sql
    assert "received_at < %s" in sql  # the timestamp guard
    assert params == ("tk1", 300)


def test_close_actions_done_sql_and_empty_noop():
    db, conn = _db_with_conn()
    # empty → no SQL, returns 0
    assert db.close_actions_done([]) == 0
    assert conn.executed == []
    # non-empty → ANY(%s) update
    conn.next_rowcount = 2
    n = db.close_actions_done([5, 6])
    assert n == 2
    sql, params = conn.executed[-1]
    assert "UPDATE mail_actions SET status = 'done'" in sql
    assert "id = ANY(%s)" in sql
    assert params == ([5, 6],)


def test_fetch_open_actions_min_projection():
    db, conn = _db_with_conn()
    conn.next_result = [{"id": 1, "thread_key": "tk", "received_at": 100}]
    rows = db.fetch_open_actions_min()
    assert rows == [{"id": 1, "thread_key": "tk", "received_at": 100}]
    sql, _ = conn.executed[-1]
    assert "FROM mail_actions WHERE status = 'open'" in sql
    assert "thread_key" in sql


def test_fetch_owner_messages_not_restricted_to_via_gmail():
    db, conn = _db_with_conn()
    conn.next_result = [{"id": 9, "headers": {}, "message_id": "<x>", "received_at": 1}]
    rows = db.fetch_owner_messages(["zach@civitai.com"])
    assert rows and rows[0]["id"] == 9
    sql, params = conn.executed[-1]
    assert "FROM mail WHERE lower(from_addr) = ANY(%s)" in sql
    assert "via_gmail" not in sql  # owner sent mail may be via_gmail=false
    assert params == (["zach@civitai.com"],)


def test_fetch_owner_messages_empty_addrs_short_circuits():
    db, conn = _db_with_conn()
    assert db.fetch_owner_messages([]) == []
    assert conn.executed == []
