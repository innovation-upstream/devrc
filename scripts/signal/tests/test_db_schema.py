"""The `signal` schema: idempotent, complete, and the substrate that proves it.

Two halves:

1. **INSTRUMENT VALIDATION.** Everything else in this directory reads verdicts
   off `fakepg.SqliteConn`. Before any of those verdicts mean anything, this
   module shows the substrate can go RED for each property the other suites rely
   on — NOT NULL, UNIQUE, UNIQUE-treats-NULLs-as-DISTINCT (the exact Postgres
   behaviour 🔧 #1 exists for), partial indexes, and ON CONFLICT … RETURNING. A
   substrate that silently enforced nothing would make every "one row" assertion
   in this suite pass vacuously.
2. **THE SCHEMA ITSELF** — idempotence, and each of the four 🔧 corrections
   present as a real constraint rather than as a word in a comment.
"""
import sqlite3

import pytest

import _signal_db
import fakepg

# --------------------------------------------------------------------------- #
# 1. Instrument validation — the substrate must be able to fail
# --------------------------------------------------------------------------- #


def test_substrate_enforces_not_null():
    """NEGATIVE CONTROL: a NOT NULL violation must raise, or 🔧 #1 is untestable."""
    conn = fakepg.SqliteConn()
    conn.raw.execute("CREATE TABLE signal.t (a INTEGER NOT NULL)")
    with pytest.raises(sqlite3.IntegrityError):
        conn.raw.execute("INSERT INTO signal.t (a) VALUES (NULL)")
    conn.raw.execute("INSERT INTO signal.t (a) VALUES (7)")   # positive control
    assert conn.raw.execute("SELECT count(*) FROM signal.t").fetchone()[0] == 1


def test_substrate_unique_treats_nulls_as_distinct():
    """The whole premise of 🔧 #1, demonstrated in the substrate itself.

    Two rows with a NULL in the UNIQUE column BOTH insert (NULLs compare
    distinct); two rows with the same non-NULL value do not. If this substrate
    deduped NULLs, the 🔧 #1 tests would be measuring the wrong engine.
    """
    conn = fakepg.SqliteConn()
    conn.raw.execute("CREATE TABLE signal.u (a INTEGER, b INTEGER, UNIQUE (a, b))")
    conn.raw.execute("INSERT INTO signal.u (a, b) VALUES (NULL, 5)")
    conn.raw.execute("INSERT INTO signal.u (a, b) VALUES (NULL, 5)")
    assert conn.raw.execute("SELECT count(*) FROM signal.u").fetchone()[0] == 2
    conn.raw.execute("INSERT INTO signal.u (a, b) VALUES (3, 5)")
    with pytest.raises(sqlite3.IntegrityError):
        conn.raw.execute("INSERT INTO signal.u (a, b) VALUES (3, 5)")


def test_substrate_supports_returning_and_partial_indexes():
    conn = fakepg.SqliteConn()
    conn.raw.execute("CREATE TABLE signal.r (id INTEGER PRIMARY KEY, x INTEGER)")
    conn.raw.execute("CREATE INDEX signal.rp ON r(x) WHERE x IS NULL")
    row = conn.raw.execute(
        "INSERT INTO signal.r (x) VALUES (12) RETURNING id").fetchone()
    assert row[0] == 1


def test_translator_refuses_what_it_cannot_translate():
    """NEGATIVE CONTROL on the translator: it raises rather than passing through."""
    with pytest.raises(fakepg.TranslationError):
        fakepg.translate_dml("SELECT * FROM signal.messages WHERE search @@ 'x'")


def test_translated_ddl_keeps_the_constraints_it_is_read_for():
    """The translation must not silently DROP a NOT NULL or a UNIQUE.

    Dropping either would turn every 🔧 assertion green-for-free — this is the
    positive control that the constraints survive the trip into sqlite.
    """
    messages = [s for s in _signal_db.SCHEMA_STATEMENTS
                if "CREATE TABLE IF NOT EXISTS signal.messages" in s][0]
    out = fakepg.translate_ddl(messages)
    assert "source_contact_id INTEGER NOT NULL" in out
    assert "UNIQUE (source_contact_id, message_timestamp)" in out
    assert "tsvector" not in out.lower()


# --------------------------------------------------------------------------- #
# 2. The schema
# --------------------------------------------------------------------------- #
EXPECTED_TABLES = {"contacts", "groups", "messages", "attachments", "reactions"}


def _tables(db) -> set:
    return {r["name"] for r in db.conn.rows(
        "SELECT name FROM signal.sqlite_master WHERE type='table'")}


def _indexes(db) -> set:
    return {r["name"] for r in db.conn.rows(
        "SELECT name FROM signal.sqlite_master WHERE type='index' "
        "AND name IS NOT NULL")}


def test_ensure_schema_creates_every_table(db):
    assert EXPECTED_TABLES <= _tables(db)


def test_ensure_schema_is_idempotent(db):
    before = _tables(db)
    db.ensure_schema()
    db.ensure_schema()
    assert _tables(db) == before
    # And the data survives a re-run — IF NOT EXISTS, never CREATE OR REPLACE.
    db.upsert_contact(signal_uuid="aaaaaaaa-0000-4000-8000-000000000001",
                      display_name="Persisted")
    db.ensure_schema()
    assert db.conn.count("contacts") == 1


def test_every_declared_index_is_created(db):
    """Derived from SCHEMA_STATEMENTS, not from a hand-written list.

    The GIN index is the documented exception (no sqlite equivalent); it is
    asserted structurally below instead.
    """
    declared = set()
    for stmt in _signal_db.SCHEMA_STATEMENTS:
        s = stmt.strip()
        if s.upper().startswith("CREATE INDEX") and "USING GIN" not in s:
            declared.add(s.split()[5])          # CREATE INDEX IF NOT EXISTS <name>
    assert declared, "HARNESS BROKEN: no non-GIN indexes parsed out of the schema"
    assert declared <= _indexes(db)


def test_source_contact_id_is_not_null_in_the_live_schema(db):
    """🔧 #1, asserted as a CONSTRAINT and not as a comment.

    A raw insert with a NULL sender must be rejected by the database. Removing
    `NOT NULL` from SCHEMA_STATEMENTS makes this pass silently — which is the
    mutation this test exists to kill.
    """
    with pytest.raises(sqlite3.IntegrityError):
        db.conn.raw.execute(
            "INSERT INTO signal.messages (message_timestamp, source_contact_id, "
            "message_type) VALUES (1723000000001, NULL, 'message')")
    # POSITIVE CONTROL: the same insert with a real contact succeeds, so the
    # failure above is about the NULL and not about the statement's shape.
    cid = db.upsert_contact(signal_uuid="bbbbbbbb-0000-4000-8000-000000000002")
    db.conn.raw.execute(
        "INSERT INTO signal.messages (message_timestamp, source_contact_id, "
        "message_type) VALUES (1723000000001, ?, 'message')", (cid,))
    assert db.conn.count("messages") == 1


def test_unique_message_constraint_is_live(db):
    cid = db.upsert_contact(signal_uuid="cccccccc-0000-4000-8000-000000000003")
    db.conn.raw.execute(
        "INSERT INTO signal.messages (message_timestamp, source_contact_id, "
        "message_type) VALUES (1723000000007, ?, 'message')", (cid,))
    with pytest.raises(sqlite3.IntegrityError):
        db.conn.raw.execute(
            "INSERT INTO signal.messages (message_timestamp, source_contact_id, "
            "message_type) VALUES (1723000000007, ?, 'message')", (cid,))


def test_unique_attachment_constraint_is_live(db):
    """🔧 #2 as a constraint: the same (message, attachment id) cannot repeat."""
    cid = db.upsert_contact(signal_uuid="dddddddd-0000-4000-8000-000000000004")
    mid = db.upsert_message({"message_timestamp": 1723000000009,
                             "source_uuid": "dddddddd-0000-4000-8000-000000000004",
                             "message_type": "message"})
    assert cid == 1
    db.conn.raw.execute(
        "INSERT INTO signal.attachments (message_id, signal_attachment_id, "
        "content_type) VALUES (?, 'att-dup', 'image/png')", (mid,))
    with pytest.raises(sqlite3.IntegrityError):
        db.conn.raw.execute(
            "INSERT INTO signal.attachments (message_id, signal_attachment_id, "
            "content_type) VALUES (?, 'att-dup', 'image/png')", (mid,))


def test_reaction_message_id_is_nullable(db):
    """🔧 #3 as a constraint: an unresolved reaction must be STORABLE."""
    cid = db.upsert_contact(signal_uuid="eeeeeeee-0000-4000-8000-000000000005")
    db.conn.raw.execute(
        "INSERT INTO signal.reactions (message_id, target_author_id, "
        "target_sent_timestamp, emoji, contact_id) "
        "VALUES (NULL, ?, 1723000000011, '👀', ?)", (cid, cid))
    assert db.conn.count("reactions") == 1


def test_generated_tsvector_column_is_declared(db):
    """The FTS column is Postgres-only, so it is asserted on the DDL text.

    Labelled honestly: this is an INVARIANT GUARD on the schema source, not
    evidence that Postgres populates the column — that is deployment step 7.
    """
    messages = [s for s in _signal_db.SCHEMA_STATEMENTS
                if "CREATE TABLE IF NOT EXISTS signal.messages" in s][0]
    assert "search tsvector GENERATED ALWAYS AS" in messages
    assert "to_tsvector('english', coalesce(body, ''))" in messages
    gin = [s for s in _signal_db.SCHEMA_STATEMENTS if "USING GIN(search)" in s]
    assert len(gin) == 1


def test_ensure_schema_emits_every_statement_and_commits_once(recording):
    db, conn = recording
    db.ensure_schema()
    assert len(conn.executed) == len(_signal_db.SCHEMA_STATEMENTS)
    assert conn.commits == 1


def test_returned_id_names_the_write_when_returning_yields_nothing():
    """A RETURNING that produced no row must say WHICH write, not TypeError.

    The bare `cur.fetchone()[0]` this replaced raised `'NoneType' object is not
    subscriptable`, which names neither the statement nor the table.
    """
    class EmptyCursor:
        def fetchone(self):
            return None

    with pytest.raises(RuntimeError) as exc:
        _signal_db._returned_id(EmptyCursor(), "upsert_contact")
    assert "upsert_contact" in str(exc.value)

    class OneRow:
        def fetchone(self):
            return (77,)

    assert _signal_db._returned_id(OneRow(), "upsert_contact") == 77   # control


# --------------------------------------------------------------------------- #
# 3. The connection-mode contract (cloned from mail-actions/_db.py)
# --------------------------------------------------------------------------- #
def test_no_env_means_port_forward(monkeypatch):
    for var in ("SIGNAL_PG_HOST", "SIGNAL_PG_PORT", "SIGNAL_PG_DIRECT"):
        monkeypatch.delenv(var, raising=False)
    assert _signal_db._direct_target() is None


def test_signal_pg_host_selects_direct_mode(monkeypatch):
    monkeypatch.setenv("SIGNAL_PG_HOST", "mailbox-postgres.mailbox.svc.cluster.local")
    monkeypatch.delenv("SIGNAL_PG_PORT", raising=False)
    monkeypatch.delenv("SIGNAL_PG_DIRECT", raising=False)
    assert _signal_db._direct_target() == (
        "mailbox-postgres.mailbox.svc.cluster.local", None)


def test_signal_pg_port_overrides_the_dsn_port(monkeypatch):
    monkeypatch.setenv("SIGNAL_PG_HOST", "pg.internal")
    monkeypatch.setenv("SIGNAL_PG_PORT", "6543")
    assert _signal_db._direct_target() == ("pg.internal", 6543)


@pytest.mark.parametrize("value,expected", [
    ("1", True), ("true", True), ("YES", True), ("on", True),
    ("0", False), ("", False), ("no", False), ("maybe", False),
])
def test_direct_flag_truthiness(monkeypatch, value, expected):
    monkeypatch.delenv("SIGNAL_PG_HOST", raising=False)
    monkeypatch.delenv("SIGNAL_PG_PORT", raising=False)
    monkeypatch.setenv("SIGNAL_PG_DIRECT", value)
    assert (_signal_db._direct_target() is not None) is expected


def test_dsn_kwargs_keep_the_dsn_values_when_no_override():
    kw = _signal_db._dsn_connect_kwargs("postgresql://u:pw@dbhost:6000/mailbox")
    assert kw["host"] == "dbhost" and kw["port"] == 6000
    assert kw["user"] == "u" and kw["password"] == "pw"
    assert kw["dbname"] == "mailbox"


def test_dsn_kwargs_apply_the_port_forward_override():
    kw = _signal_db._dsn_connect_kwargs("postgres://u:pw@dbhost/mailbox",
                                        host="127.0.0.1", port=45678)
    assert kw["host"] == "127.0.0.1" and kw["port"] == 45678


def test_dsn_kwargs_reject_a_non_postgres_scheme():
    with pytest.raises(ValueError):
        _signal_db._dsn_connect_kwargs("mysql://u:pw@dbhost/mailbox")


def test_db_used_outside_its_context_manager_is_a_clear_error():
    db = _signal_db.SignalDB(dsn="postgres://u:p@h/mailbox")
    with pytest.raises(RuntimeError) as exc:
        db.ensure_schema()
    assert "context manager" in str(exc.value)


def test_namespace_and_service_point_at_the_mailbox_postgres():
    """The signal schema deliberately shares the mailbox instance."""
    assert _signal_db.NAMESPACE == "mailbox"
    assert _signal_db.SERVICE == "svc/mailbox-postgres"
    assert _signal_db.SCHEMA == "signal"
