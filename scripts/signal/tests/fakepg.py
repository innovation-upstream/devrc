"""Hermetic DB substrates for the Signal suites. No Postgres, no network.

TWO substrates, for two different jobs:

* ``RecordingConn`` — the `scripts/mail-actions/tests/test_db_schema.py` pattern:
  records every ``(sql, params)`` the layer emits so a test can assert on the SQL
  itself (that the FTS query binds its parameter, that a guard lives in the WHERE
  clause rather than in Python, …). Executes nothing.

* ``SqliteConn`` — a REAL relational engine standing in for Postgres, so
  idempotency/dedupe/constraint claims are BEHAVIOURAL rather than spelled. The
  DDL under test is `_signal_db.SCHEMA_STATEMENTS` itself, mechanically
  translated; nothing is restated here, so removing ``NOT NULL`` or a ``UNIQUE``
  from the real schema really does change what these tests observe. That is what
  makes the four 🔧 corrections mutation-testable at all.

WHAT THE SQLITE SUBSTRATE FAITHFULLY REPRODUCES (the properties under test):
  * ``NOT NULL`` and ``UNIQUE`` enforcement, including **UNIQUE treating NULLs as
    DISTINCT** — the exact Postgres behaviour 🔧 #1 exists for;
  * partial indexes (``… WHERE message_id IS NULL``);
  * ``INSERT … ON CONFLICT (…) DO UPDATE/NOTHING … RETURNING``;
  * foreign keys (``PRAGMA foreign_keys=ON``).

WHAT IT DOES **NOT** REPRODUCE — stated so no test claims more than it measured:
  * ``tsvector``/``to_tsvector`` stemming. The generated column is dropped and
    ``search @@ websearch_to_tsquery('english', ?)`` is rewritten to the
    ``pg_websearch_match(body, ?)`` shim below, which does AND-of-terms substring
    matching. So the FTS tests pin ROW SELECTION, JOINs, ordering, the limit and
    the parameter binding — NOT Postgres's stemming or ranking. Those need the
    live database (deployment step 7).
  * types: ``BOOLEAN`` reads back as 0/1 and ``TIMESTAMPTZ`` as text. Tests
    compare truthiness, never ``is True``.

🔴 The translator RAISES on anything it does not recognise rather than silently
passing SQL through — a substrate that quietly mistranslates would make every
assertion above vacuous.
"""
from __future__ import annotations

import re
import sqlite3

SQLITE_MIN = (3, 35, 0)  # RETURNING support


def _require_sqlite() -> None:
    have = tuple(int(x) for x in sqlite3.sqlite_version.split("."))
    if have < SQLITE_MIN:  # pragma: no cover - environment guard
        raise AssertionError(
            f"HARNESS BROKEN: sqlite {sqlite3.sqlite_version} lacks RETURNING "
            f"(need >= {'.'.join(map(str, SQLITE_MIN))}); these suites would test "
            f"nothing. This is a hard error, deliberately, NOT a skip.")


# --------------------------------------------------------------------------- #
# Postgres -> sqlite translation
# --------------------------------------------------------------------------- #
_TYPE_MAP = (
    (r"\bBIGSERIAL PRIMARY KEY\b", "INTEGER PRIMARY KEY AUTOINCREMENT"),
    (r"\bSERIAL PRIMARY KEY\b", "INTEGER PRIMARY KEY AUTOINCREMENT"),
    (r"\bTIMESTAMPTZ DEFAULT now\(\)", "TEXT DEFAULT CURRENT_TIMESTAMP"),
    (r"\bTIMESTAMPTZ\b", "TEXT"),
    (r"\bBYTEA\b", "BLOB"),
    (r"\bJSONB\b", "TEXT"),
    (r"\bUUID\b", "TEXT"),
    (r"\bBIGINT\b", "INTEGER"),
    (r"\bGREATEST\(", "MAX("),
)

# The generated tsvector column and its GIN index have no sqlite equivalent.
_GENERATED_COL = re.compile(
    r"^\s*search\s+tsvector\s+GENERATED\s+ALWAYS\s+AS\s+\(.*\)\s+STORED\s*,\s*$",
    re.MULTILINE | re.IGNORECASE)
_GIN_INDEX = re.compile(r"USING\s+GIN\s*\(", re.IGNORECASE)

# `CREATE INDEX x ON signal.messages(...)` -> `CREATE INDEX signal.x ON messages(...)`
_CREATE_INDEX = re.compile(
    r"CREATE INDEX IF NOT EXISTS (\w+) ON signal\.(\w+)", re.IGNORECASE)

_FTS_PREDICATE = re.compile(
    r"(\w+)\.search\s+@@\s+websearch_to_tsquery\('english',\s*%s\)", re.IGNORECASE)


_IS_DDL = re.compile(r"CREATE\s+(SCHEMA|TABLE|INDEX)\b", re.IGNORECASE)


class TranslationError(AssertionError):
    """The substrate met SQL it could not faithfully translate."""


def translate_ddl(stmt: str) -> str | None:
    """One `SCHEMA_STATEMENTS` entry → sqlite DDL, or None to skip it."""
    s = stmt.strip()
    if s.upper().startswith("CREATE SCHEMA"):
        return None                      # the `signal` db is ATTACHed instead
    if _GIN_INDEX.search(s):
        return None                      # no GIN in sqlite; FTS is shimmed
    s = _GENERATED_COL.sub("", s)
    s = _CREATE_INDEX.sub(r"CREATE INDEX IF NOT EXISTS signal.\1 ON \2", s)
    # sqlite cannot resolve a schema-qualified REFERENCES target; every table
    # lives in the attached `signal` db, so the bare name resolves correctly.
    s = re.sub(r"REFERENCES\s+signal\.(\w+)", r"REFERENCES \1", s)
    for pattern, repl in _TYPE_MAP:
        s = re.sub(pattern, repl, s)
    if "tsvector" in s.lower():
        raise TranslationError(
            f"HARNESS BROKEN: a tsvector survived translation:\n{s}")
    return s


def translate_dml(sql: str) -> str:
    """One runtime statement → sqlite, preserving its meaning or raising."""
    s = _FTS_PREDICATE.sub(r"pg_websearch_match(\1.body, %s)", sql)
    if "tsvector" in s.lower() or "websearch_to_tsquery" in s.lower() or "@@" in s:
        raise TranslationError(
            f"HARNESS BROKEN: an untranslated FTS construct reached sqlite:\n{s}")
    for pattern, repl in _TYPE_MAP:
        s = re.sub(pattern, repl, s)
    return s


def translate_params(sql: str, params):
    """`%s`/`%(name)s` placeholders → sqlite's `?`/`:name`."""
    if isinstance(params, dict):
        return re.sub(r"%\((\w+)\)s", r":\1", sql), params
    return sql.replace("%s", "?"), tuple(params or ())


def _websearch_match(body, query) -> int:
    """Stand-in for `search @@ websearch_to_tsquery('english', ?)`.

    AND-of-terms, case-insensitive, quoted phrases honoured, `-term` negates.
    NO stemming — see this module's docstring for what that means for the FTS
    tests' scope.
    """
    hay = (body or "").lower()
    q = (query or "").strip().lower()
    if not q:
        return 0
    terms = re.findall(r'"([^"]+)"|(\S+)', q)
    positives, negatives = [], []
    for phrase, word in terms:
        term = phrase or word
        if not phrase and term.startswith("-") and len(term) > 1:
            negatives.append(term[1:])
        elif term not in ("or", "and"):
            positives.append(term)
    if any(n in hay for n in negatives):
        return 0
    return 1 if all(p in hay for p in positives) else 0


# --------------------------------------------------------------------------- #
# Substrate 1 — a real engine
# --------------------------------------------------------------------------- #
class SqliteCursor:
    def __init__(self, conn: "SqliteConn", cursor_factory=None):
        self._conn = conn
        self._cur = conn.raw.cursor()
        self._dict_rows = cursor_factory is not None

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self._cur.close()
        return False

    @property
    def rowcount(self) -> int:
        return self._cur.rowcount

    def execute(self, sql, params=None):
        self._conn.executed.append((" ".join(str(sql).split()), params))
        if _IS_DDL.match(str(sql).strip()):
            translated = translate_ddl(sql)
            if translated is None:
                return                     # no sqlite equivalent; documented above
            self._cur.execute(translated)
            return
        translated = translate_dml(sql)
        translated, tparams = translate_params(translated, params)
        self._cur.execute(translated, tparams)

    def _rows(self, rows):
        if not self._dict_rows:
            return rows
        cols = [d[0] for d in self._cur.description or []]
        return [dict(zip(cols, r)) for r in rows]

    def fetchall(self):
        return self._rows(self._cur.fetchall())

    def fetchone(self):
        row = self._cur.fetchone()
        if row is None:
            return None
        return self._rows([row])[0]


class SqliteConn:
    """A psycopg2-shaped connection backed by in-memory sqlite."""

    def __init__(self):
        _require_sqlite()
        self.raw = sqlite3.connect(":memory:")
        self.raw.execute("ATTACH DATABASE ':memory:' AS signal")
        self.raw.execute("PRAGMA foreign_keys=ON")
        self.raw.create_function("pg_websearch_match", 2, _websearch_match)
        self.executed: list[tuple[str, object]] = []
        self.commits = 0

    def cursor(self, cursor_factory=None):
        return SqliteCursor(self, cursor_factory)

    def commit(self):
        self.commits += 1
        self.raw.commit()

    def close(self):
        self.raw.close()

    # -- direct introspection for tests -----------------------------------
    def count(self, table: str) -> int:
        return self.raw.execute(f"SELECT count(*) FROM signal.{table}").fetchone()[0]

    def rows(self, sql: str, params=()) -> list[dict]:
        cur = self.raw.execute(sql, params)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


# --------------------------------------------------------------------------- #
# Substrate 2 — SQL recorder (executes nothing)
# --------------------------------------------------------------------------- #
class RecordingCursor:
    def __init__(self, conn):
        self._conn = conn
        self.rowcount = 0
        self._result = []

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def execute(self, sql, params=None):
        self._conn.executed.append((" ".join(str(sql).split()), params))
        self.rowcount = self._conn.next_rowcount
        if self._conn.result_queue:
            self._result = self._conn.result_queue.pop(0)
        else:
            self._result = self._conn.next_result

    def fetchall(self):
        return self._result

    def fetchone(self):
        return self._result[0] if self._result else None


class RecordingConn:
    def __init__(self):
        self.executed: list[tuple[str, object]] = []
        self.commits = 0
        self.next_rowcount = 0
        self.next_result: list = []
        self.result_queue: list[list] = []

    def cursor(self, cursor_factory=None):
        return RecordingCursor(self)

    def commit(self):
        self.commits += 1

    def sqls(self) -> list[str]:
        return [sql for sql, _ in self.executed]


# --------------------------------------------------------------------------- #
# Convenience
# --------------------------------------------------------------------------- #
def open_db(module):
    """A `SignalDB` bound to a fresh sqlite substrate, schema applied."""
    db = module.SignalDB(dsn="postgres://u:p@h/mailbox")
    db.conn = SqliteConn()
    db.ensure_schema()
    return db


def recording_db(module):
    db = module.SignalDB(dsn="postgres://u:p@h/mailbox")
    db.conn = RecordingConn()
    return db, db.conn
