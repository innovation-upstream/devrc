"""Unit tests for the OpenCode validation integration (Phase 5).

Tests the reference readers (read_opencode_sessions, read_opencode_messages),
reconcile_opencode, and that EXPECTED_SOURCES includes "opencode".
"""
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import invariants as I  # noqa: E402
import refsources as RS  # noqa: E402
import reconcile as R  # noqa: E402


# --------------------------------------------------------------------------- #
# Fixture: temporary OpenCode SQLite DB
# --------------------------------------------------------------------------- #
def _create_opencode_db(tmp_path: Path, sessions=None, messages=None) -> Path:
    """Create a minimal OpenCode SQLite DB with the expected schema."""
    db_path = tmp_path / "test-opencode.db"
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE session (
            id TEXT PRIMARY KEY,
            project_id TEXT,
            directory TEXT,
            title TEXT,
            agent TEXT,
            model TEXT,
            version TEXT,
            cost REAL,
            tokens_input INTEGER,
            tokens_output INTEGER,
            tokens_reasoning INTEGER,
            tokens_cache_read INTEGER,
            tokens_cache_write INTEGER,
            time_created INTEGER,
            time_updated INTEGER,
            parent_id TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE message (
            id TEXT PRIMARY KEY,
            session_id TEXT,
            data TEXT,
            time_created INTEGER,
            time_updated INTEGER
        )
    """)

    cur.execute("""
        CREATE TABLE part (
            id TEXT PRIMARY KEY,
            message_id TEXT,
            session_id TEXT,
            data TEXT,
            time_created INTEGER,
            time_updated INTEGER
        )
    """)

    if sessions:
        for s in sessions:
            cur.execute(
                "INSERT INTO session (id, project_id, directory, title, agent, "
                "model, version, cost, tokens_input, tokens_output, "
                "tokens_reasoning, tokens_cache_read, tokens_cache_write, "
                "time_created, time_updated, parent_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    s["id"], s.get("project_id", ""), s.get("directory", ""),
                    s.get("title", ""), s.get("agent", ""), s.get("model", None),
                    s.get("version", ""), s.get("cost", 0.0),
                    s.get("tokens_input", 0), s.get("tokens_output", 0),
                    s.get("tokens_reasoning", 0), s.get("tokens_cache_read", 0),
                    s.get("tokens_cache_write", 0), s["time_created"],
                    s.get("time_updated", s["time_created"]),
                    s.get("parent_id", None),
                ),
            )

    if messages:
        for m in messages:
            cur.execute(
                "INSERT INTO message (id, session_id, data, time_created, "
                "time_updated) VALUES (?,?,?,?,?)",
                (
                    m["id"], m["session_id"],
                    json.dumps(m.get("data", {})),
                    m["time_created"],
                    m.get("time_updated", m["time_created"]),
                ),
            )

    conn.commit()
    conn.close()
    return db_path


# --------------------------------------------------------------------------- #
# read_opencode_sessions
# --------------------------------------------------------------------------- #
def test_read_opencode_sessions_empty(tmp_path):
    """Returns [] when no DB exists."""
    result = RS.read_opencode_sessions(tmp_path / "nonexistent.db")
    assert result == []


def test_read_opencode_sessions_with_data(tmp_path):
    """Returns correct event shapes from a real DB."""
    db = _create_opencode_db(
        tmp_path,
        sessions=[
            {
                "id": "sess-abc",
                "project_id": "proj-1",
                "directory": "/home/user/projects/my-repo",
                "title": "Test Session",
                "agent": "coder",
                "model": None,
                "version": "1.0",
                "cost": 0.05,
                "tokens_input": 1000,
                "tokens_output": 500,
                "tokens_reasoning": 0,
                "tokens_cache_read": 0,
                "tokens_cache_write": 0,
                "time_created": 1700000000000,
                "time_updated": 1700000060000,
            },
            {
                "id": "sess-def",
                "project_id": "proj-2",
                "directory": "/home/user/projects/other-repo",
                "title": "Another Session",
                "agent": "coder",
                "model": None,
                "version": "1.0",
                "cost": 0.01,
                "tokens_input": 200,
                "tokens_output": 100,
                "tokens_reasoning": 0,
                "tokens_cache_read": 0,
                "tokens_cache_write": 0,
                "time_created": 1700000100000,
                "time_updated": 1700000120000,
            },
        ],
    )
    events = RS.read_opencode_sessions(db)
    assert len(events) == 2
    sessions = {e["session"]: e for e in events}
    assert "sess-abc" in sessions
    assert "sess-def" in sessions

    ev = sessions["sess-abc"]
    assert ev["source"] == "opencode"
    assert ev["kind"] == "session-summary"
    assert ev["project"] == "my-repo"
    assert ev["cwd"] == "/home/user/projects/my-repo"
    assert ev["app"] == "opencode"
    assert "ts" in ev and isinstance(ev["ts"], str)


# --------------------------------------------------------------------------- #
# read_opencode_messages
# --------------------------------------------------------------------------- #
def test_read_opencode_messages_with_data(tmp_path):
    """Returns correct event shapes for user/assistant messages."""
    db = _create_opencode_db(
        tmp_path,
        sessions=[
            {
                "id": "sess-1",
                "directory": "/home/user/projects/test-repo",
                "time_created": 1700000000000,
                "time_updated": 1700000060000,
            },
        ],
        messages=[
            {
                "id": "msg-1",
                "session_id": "sess-1",
                "data": {"role": "user"},
                "time_created": 1700000005000,
            },
            {
                "id": "msg-2",
                "session_id": "sess-1",
                "data": {"role": "assistant"},
                "time_created": 1700000010000,
            },
            {
                "id": "msg-3",
                "session_id": "sess-1",
                "data": {"role": "system"},
                "time_created": 1700000015000,
            },
        ],
    )
    events = RS.read_opencode_messages(db)
    # system role is filtered out
    assert len(events) == 2
    kinds = {e["kind"] for e in events}
    assert kinds == {"prompt", "assistant-turn"}
    for ev in events:
        assert ev["source"] == "opencode"
        assert ev["session"] == "sess-1"
        assert ev["project"] == "test-repo"
        assert ev["app"] == "opencode"
        assert "ts" in ev


def test_read_opencode_messages_empty(tmp_path):
    """Returns [] when DB has no messages."""
    db = _create_opencode_db(
        tmp_path,
        sessions=[{
            "id": "sess-empty",
            "directory": "/tmp",
            "time_created": 1700000000000,
        }],
    )
    events = RS.read_opencode_messages(db)
    assert events == []


# --------------------------------------------------------------------------- #
# reconcile_opencode
# --------------------------------------------------------------------------- #
class FakeClient:
    def __init__(self, rows_val=None):
        self._rows = rows_val or []

        class _Conn:
            fq_table = "activity.events"
            url = "http://fake"
        self.conn = _Conn()

    def scalar(self, sql):
        return 0

    def rows(self, sql):
        return self._rows


def test_reconcile_opencode_no_events(tmp_path):
    """Graceful handling when CH has no opencode events and ref has none."""
    db = _create_opencode_db(tmp_path)
    r = R.reconcile_opencode(client=FakeClient(), db_path=db)
    assert r.skipped is True
    assert "no opencode data" in r.reason


def test_reconcile_opencode_matches(tmp_path):
    """Ref and CH match → clean Recon with 0 missing/extra."""
    db = _create_opencode_db(
        tmp_path,
        sessions=[{
            "id": "sess-1",
            "directory": "/tmp/test",
            "time_created": 1700000000000,
        }],
        messages=[
            {"id": "msg-1", "session_id": "sess-1", "data": {"role": "user"}, "time_created": 1700000005000},
            {"id": "msg-2", "session_id": "sess-1", "data": {"role": "assistant"}, "time_created": 1700000010000},
        ],
    )
    # CH has 2 events (1 prompt + 1 assistant-turn)
    client = FakeClient(rows_val=[
        {"kind": "prompt", "cnt": 1},
        {"kind": "assistant-turn", "cnt": 1},
    ])
    r = R.reconcile_opencode(client=client, db_path=db)
    assert r.skipped is False
    assert r.collected == 2
    assert r.reference == 2
    assert r.matched == 2
    assert r.missing == 0
    assert r.extra == 0


def test_reconcile_opencode_missing(tmp_path):
    """Ref has events CH doesn't → missing list populated."""
    db = _create_opencode_db(
        tmp_path,
        sessions=[{
            "id": "sess-1",
            "directory": "/tmp/test",
            "time_created": 1700000000000,
        }],
        messages=[
            {"id": "msg-1", "session_id": "sess-1", "data": {"role": "user"}, "time_created": 1700000005000},
            {"id": "msg-2", "session_id": "sess-1", "data": {"role": "assistant"}, "time_created": 1700000010000},
            {"id": "msg-3", "session_id": "sess-1", "data": {"role": "user"}, "time_created": 1700000015000},
        ],
    )
    # CH only has 1 event (ref has 3)
    client = FakeClient(rows_val=[{"kind": "prompt", "cnt": 1}])
    r = R.reconcile_opencode(client=client, db_path=db)
    assert r.skipped is False
    assert r.collected == 1
    assert r.reference == 3
    assert r.matched == 1
    assert r.missing == 2  # 3 ref - 1 matched
    assert r.extra == 0


def test_reconcile_opencode_query_error_skipped(tmp_path):
    """Query error → graceful skip."""
    class BoomClient(FakeClient):
        def rows(self, sql):
            raise RuntimeError("connection refused")

    db = _create_opencode_db(
        tmp_path,
        sessions=[{
            "id": "sess-1",
            "directory": "/tmp",
            "time_created": 1700000000000,
        }],
        messages=[
            {"id": "msg-1", "session_id": "sess-1", "data": {"role": "user"}, "time_created": 1700000005000},
        ],
    )
    r = R.reconcile_opencode(client=BoomClient(), db_path=db)
    assert r.skipped is True
    assert "error" in r.reason


# --------------------------------------------------------------------------- #
# EXPECTED_SOURCES invariant
# --------------------------------------------------------------------------- #
def test_expected_sources_includes_opencode():
    assert "opencode" in I.EXPECTED_SOURCES
    ev = I.eval_unexpected_set(I.EXPECTED_SOURCES, "source")
    assert ev([{"value": "opencode", "count": 3}])[0] is True
