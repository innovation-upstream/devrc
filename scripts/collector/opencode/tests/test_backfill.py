"""Tests for opencode/backfill.py — backfill script.

Run: python -m pytest scripts/collector/opencode/tests/test_backfill.py -v
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

COLLECTOR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(COLLECTOR))
KEYLOG = COLLECTOR / "keylog"
sys.path.insert(0, str(KEYLOG))
OPENCODE = COLLECTOR / "opencode"
sys.path.insert(0, str(OPENCODE))

import _shared as S  # noqa: E402
import collector as C  # noqa: E402
import tailer as T  # noqa: E402
import session_tailer as ST  # noqa: E402

sys.path.insert(0, str(OPENCODE))
import backfill as B  # noqa: E402


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
@pytest.fixture
def env(tmp_path, monkeypatch):
    spool = tmp_path / "spool"
    spool.mkdir()
    monkeypatch.setenv("ACTIVITY_SPOOL_DIR", str(spool))
    monkeypatch.setenv("OPENCODE_TAILER_STATE", str(tmp_path / "tailer-state.json"))
    monkeypatch.setenv("OPENCODE_SESSION_STATE", str(tmp_path / "session-state.json"))
    return {"spool": spool, "tmp_path": tmp_path}


@pytest.fixture
def sample_db(tmp_path):
    db_path = tmp_path / "opencode-stable.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("""CREATE TABLE session (
        id TEXT PRIMARY KEY, project_id TEXT, workspace_id TEXT, parent_id TEXT,
        slug TEXT, directory TEXT, path TEXT, title TEXT, version TEXT,
        share_url TEXT, summary_additions INTEGER, summary_deletions INTEGER,
        summary_files INTEGER, summary_diffs TEXT, metadata TEXT,
        cost REAL, tokens_input INTEGER, tokens_output INTEGER,
        tokens_reasoning INTEGER, tokens_cache_read INTEGER,
        tokens_cache_write INTEGER, revert TEXT, permission TEXT,
        agent TEXT, model TEXT,
        time_created INTEGER, time_updated INTEGER,
        time_compacting INTEGER, time_archived INTEGER
    )""")
    conn.execute("""CREATE TABLE message (
        id TEXT PRIMARY KEY, session_id TEXT, time_created INTEGER,
        time_updated INTEGER, data TEXT
    )""")
    conn.execute("""CREATE TABLE part (
        id TEXT PRIMARY KEY, message_id TEXT, session_id TEXT,
        time_created INTEGER, time_updated INTEGER, data TEXT
    )""")

    conn.execute(
        """INSERT INTO session (
            id, project_id, workspace_id, parent_id, slug, directory, path,
            title, version, share_url, summary_additions, summary_deletions,
            summary_files, summary_diffs, metadata, cost, tokens_input,
            tokens_output, tokens_reasoning, tokens_cache_read,
            tokens_cache_write, revert, permission, agent, model,
            time_created, time_updated
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            "ses_001", "proj_a", "ws_1", None, "my-repo",
            "/home/zach/workspace/my-repo", None, "Test session",
            "1.0.0", None, 10, 5, 3, None, None, 0.05,
            1000, 500, 200, 300, 100,
            None, None, "build",
            '{"id":"deepseek/deepseek-v4-flash","providerID":"openrouter"}',
            1700000000000, 1700000060000,
        ),
    )
    conn.execute(
        """INSERT INTO session (
            id, project_id, workspace_id, parent_id, slug, directory, path,
            title, version, share_url, summary_additions, summary_deletions,
            summary_files, summary_diffs, metadata, cost, tokens_input,
            tokens_output, tokens_reasoning, tokens_cache_read,
            tokens_cache_write, revert, permission, agent, model,
            time_created, time_updated
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            "ses_002", "proj_b", "ws_1", None, "another-repo",
            "/home/zach/workspace/another-repo", None, "Second session",
            "1.0.0", None, 0, 0, 0, None, None, 0.01,
            200, 100, 0, 50, 50,
            None, None, "build",
            '{"id":"gpt-4o","providerID":"openai"}',
            1700001000000, 1700001030000,
        ),
    )

    # --- Messages for ses_001 ---
    conn.execute(
        "INSERT INTO message VALUES (?,?,?,?,?)",
        (
            "msg_001", "ses_001", 1700000001000, 1700000001000,
            json.dumps({"role": "user", "time": {"created": 1700000001000}, "agent": "build"}),
        ),
    )
    conn.execute(
        "INSERT INTO part VALUES (?,?,?,?,?,?)",
        (
            "part_001", "msg_001", "ses_001", 1700000001000, 1700000001000,
            json.dumps({"type": "text", "text": "Hello, please help me."}),
        ),
    )
    conn.execute(
        "INSERT INTO message VALUES (?,?,?,?,?)",
        (
            "msg_002", "ses_001", 1700000002000, 1700000003000,
            json.dumps({
                "role": "assistant", "time": {"created": 1700000002000, "completed": 1700000003000},
                "agent": "build",
                "model": {"providerID": "openrouter", "modelID": "deepseek-v4-flash"},
                "cost": 0.001, "tokens": {"input": 500, "output": 100, "reasoning": 50},
            }),
        ),
    )
    conn.execute(
        "INSERT INTO part VALUES (?,?,?,?,?,?)",
        (
            "part_002", "msg_002", "ses_001", 1700000002100, 1700000003000,
            json.dumps({"type": "text", "text": "Here is the solution."}),
        ),
    )

    # --- Messages for ses_002 ---
    conn.execute(
        "INSERT INTO message VALUES (?,?,?,?,?)",
        (
            "msg_s2_001", "ses_002", 1700001001000, 1700001001000,
            json.dumps({"role": "user", "time": {"created": 1700001001000}, "agent": "build"}),
        ),
    )
    conn.execute(
        "INSERT INTO part VALUES (?,?,?,?,?,?)",
        (
            "part_s2_001", "msg_s2_001", "ses_002", 1700001001000, 1700001001000,
            json.dumps({"type": "text", "text": "Do something in the other repo"}),
        ),
    )

    conn.commit()
    conn.close()
    return db_path


def _mock_get_db(sample_db):
    """Return a mock get_db that returns writable connections."""
    def mock_get_db(path=None):
        db = path or sample_db
        if db is None or not db.exists():
            return None
        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row
        return conn
    return mock_get_db


# --------------------------------------------------------------------------- #
# test_backfill_emits_all_sessions
# --------------------------------------------------------------------------- #
class TestBackfillEmitsAllSessions:
    def test_all_sessions_emitted(self, sample_db, env, monkeypatch):
        monkeypatch.setattr(S, "get_db", _mock_get_db(sample_db))
        rc = B.run(db_path=sample_db)
        assert rc == 0

        spool_file = env["spool"] / "current.log"
        lines = spool_file.read_text().strip().splitlines()
        # Find session-summary events
        session_events = []
        for line in lines:
            ev = C.parse_line(line)
            if ev and ev["kind"] == "session-summary":
                session_events.append(ev)

        sessions = {ev["session"] for ev in session_events}
        assert "ses_001" in sessions
        assert "ses_002" in sessions
        assert len(session_events) >= 2


# --------------------------------------------------------------------------- #
# test_backfill_emits_all_messages
# --------------------------------------------------------------------------- #
class TestBackfillEmitsAllMessages:
    def test_all_messages_emitted(self, sample_db, env, monkeypatch):
        monkeypatch.setattr(S, "get_db", _mock_get_db(sample_db))
        rc = B.run(db_path=sample_db)
        assert rc == 0

        spool_file = env["spool"] / "current.log"
        lines = spool_file.read_text().strip().splitlines()
        # Find message-level events (prompt or assistant-turn)
        message_events = []
        for line in lines:
            ev = C.parse_line(line)
            if ev and ev["kind"] in ("prompt", "assistant-turn"):
                message_events.append(ev)

        # ses_001: user msg + assistant msg = 2 events
        # ses_002: user msg = 1 event
        assert len(message_events) >= 3

        sessions = {ev["session"] for ev in message_events}
        assert "ses_001" in sessions
        assert "ses_002" in sessions


# --------------------------------------------------------------------------- #
# test_backfill_idempotent
# --------------------------------------------------------------------------- #
class TestBackfillIdempotent:
    def test_running_twice_same_events(self, sample_db, env, monkeypatch):
        monkeypatch.setattr(S, "get_db", _mock_get_db(sample_db))

        # First backfill
        B.run(db_path=sample_db)
        spool_file = env["spool"] / "current.log"
        lines1 = spool_file.read_text().strip().splitlines()
        assert len(lines1) > 0

        # Second backfill — state is cleared again, same events emitted
        # (spool appends, so we verify the SAME events are emitted, not same count)
        B.run(db_path=sample_db)
        lines2 = spool_file.read_text().strip().splitlines()
        assert len(lines2) == len(lines1) * 2  # doubled due to append

        # Verify the second batch matches the first
        first_batch = lines1
        second_batch = lines2[len(lines1):]
        assert first_batch == second_batch

    def test_state_cleared_between_runs(self, sample_db, env, monkeypatch):
        monkeypatch.setattr(S, "get_db", _mock_get_db(sample_db))

        # First run populates state
        B.run(db_path=sample_db)
        assert (env["tmp_path"] / "tailer-state.json").exists()
        assert (env["tmp_path"] / "session-state.json").exists()

        # Second run: state cleared → re-emitted
        B.run(db_path=sample_db)
        # State files re-created after second run
        assert (env["tmp_path"] / "tailer-state.json").exists()
        assert (env["tmp_path"] / "session-state.json").exists()


# --------------------------------------------------------------------------- #
# test_backfill_with_empty_db
# --------------------------------------------------------------------------- #
class TestBackfillWithEmptyDb:
    def test_empty_db_no_crash(self, tmp_path, env, monkeypatch):
        db_path = tmp_path / "empty.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("""CREATE TABLE session (
            id TEXT PRIMARY KEY, project_id TEXT, workspace_id TEXT, parent_id TEXT,
            slug TEXT, directory TEXT, path TEXT, title TEXT, version TEXT,
            share_url TEXT, summary_additions INTEGER, summary_deletions INTEGER,
            summary_files INTEGER, summary_diffs TEXT, metadata TEXT,
            cost REAL, tokens_input INTEGER, tokens_output INTEGER,
            tokens_reasoning INTEGER, tokens_cache_read INTEGER,
            tokens_cache_write INTEGER, revert TEXT, permission TEXT,
            agent TEXT, model TEXT,
            time_created INTEGER, time_updated INTEGER,
            time_compacting INTEGER, time_archived INTEGER
        )""")
        conn.execute("""CREATE TABLE message (
            id TEXT PRIMARY KEY, session_id TEXT, time_created INTEGER,
            time_updated INTEGER, data TEXT
        )""")
        conn.execute("""CREATE TABLE part (
            id TEXT PRIMARY KEY, message_id TEXT, session_id TEXT,
            time_created INTEGER, time_updated INTEGER, data TEXT
        )""")
        conn.commit()
        conn.close()

        monkeypatch.setattr(S, "get_db", _mock_get_db(db_path))
        rc = B.run(db_path=db_path)
        assert rc == 0

        spool_file = env["spool"] / "current.log"
        if spool_file.exists():
            lines = spool_file.read_text().strip().splitlines()
            assert len(lines) == 0

    def test_nonexistent_db(self, tmp_path, env, monkeypatch):
        monkeypatch.setattr(S, "get_db", _mock_get_db(tmp_path / "nonexistent.db"))
        rc = B.run(db_path=tmp_path / "nonexistent.db")
        assert rc == 0
