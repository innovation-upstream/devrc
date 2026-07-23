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
        # A multi-query method (e.g. fetch_current_initiatives issues a to_regclass
        # guard THEN a SELECT) can preload a per-execute result queue; otherwise every
        # execute sees the single `next_result`.
        if self._conn.result_queue:
            self._result = self._conn.result_queue.pop(0)
        else:
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
        self.result_queue = []

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
    # ... AND the additive surface-only initiative-router column migration.
    assert any(
        "ALTER TABLE mail_actions ADD COLUMN IF NOT EXISTS related_initiative text" in s
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


def test_insert_action_includes_related_initiative_column_and_param():
    db, conn = _db_with_conn()
    conn.next_rowcount = 1
    ok = db.insert_action({
        "mail_id": 3, "message_id": "<m>", "from_addr": "a@b.com", "subject": "s",
        "received_at": 100, "who": "w", "ask": "a", "deadline": None, "amount": None,
        "confidence": 0.9, "reason": "r", "thread_key": "tk1",
        "related_initiative": "clawgate-chat-polish",
    })
    assert ok is True
    sql, params = conn.executed[-1]
    assert "related_initiative" in sql
    assert "%(related_initiative)s" in sql
    assert params["related_initiative"] == "clawgate-chat-polish"


def test_insert_action_defaults_thread_key_and_related_null_when_absent():
    db, conn = _db_with_conn()
    conn.next_rowcount = 1
    db.insert_action({
        "mail_id": 2, "message_id": "<m>", "from_addr": "a@b.com", "subject": "s",
        "received_at": 100, "who": "w", "ask": "a", "deadline": None, "amount": None,
        "confidence": 0.9, "reason": "r",
    })
    _sql, params = conn.executed[-1]
    assert params["thread_key"] is None
    assert params["related_initiative"] is None


def test_fetch_current_initiatives_absent_view_returns_empty():
    db, conn = _db_with_conn()
    # to_regclass('initiatives.current') → NULL when the Phase-1 sync isn't deployed.
    conn.next_result = [(None,)]
    assert db.fetch_current_initiatives() == []
    sql, _ = conn.executed[-1]
    assert "to_regclass('initiatives.current')" in sql


def test_fetch_current_initiatives_reads_view_when_present():
    db, conn = _db_with_conn()
    # Execute #1 (to_regclass) → a non-NULL regclass tuple; execute #2 (SELECT) → rows.
    row = {"slug": "clawgate-chat-polish", "repo": "/r/devrc",
           "title": "Clawgate chat polish"}
    conn.result_queue = [[("initiatives.current",)], [row]]
    rows = db.fetch_current_initiatives()
    assert rows == [row]
    sql, _ = conn.executed[-1]
    assert "SELECT slug, repo, title FROM initiatives.current" in sql


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
