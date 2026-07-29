"""Tests for opencode/_shared.py — shared utilities for the OpenCode activity source.

Run: python -m pytest scripts/collector/opencode/tests/test_shared.py -v
"""
import json
import os
import sqlite3
import sys
from pathlib import Path

import pytest

# Ensure the collector parent is on sys.path for collector.parse_line
COLLECTOR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(COLLECTOR))
# Also add keylog for spool_emit import
KEYLOG = COLLECTOR / "keylog"
sys.path.insert(0, str(KEYLOG))
# And the opencode dir itself so 'from _shared import ...' works
OPENCODE = COLLECTOR / "opencode"
sys.path.insert(0, str(OPENCODE))

import _shared as S  # noqa: E402
import collector as C  # noqa: E402


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
@pytest.fixture
def env(tmp_path, monkeypatch):
    """Set up isolated env dirs for tests."""
    spool = tmp_path / "spool"
    spool.mkdir()
    opencode_dir = tmp_path / "opencode"
    opencode_dir.mkdir()
    monkeypatch.setenv("ACTIVITY_SPOOL_DIR", str(spool))
    monkeypatch.setenv("OPENCODE_DATA_DIR", str(opencode_dir))
    return {"spool": spool, "opencode_dir": opencode_dir}


@pytest.fixture
def sample_db(tmp_path):
    """Create a temporary SQLite DB with the real OpenCode schema and test data."""
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE session (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            workspace_id TEXT,
            parent_id TEXT,
            slug TEXT NOT NULL,
            directory TEXT NOT NULL,
            path TEXT,
            title TEXT NOT NULL,
            version TEXT NOT NULL,
            share_url TEXT,
            summary_additions INTEGER,
            summary_deletions INTEGER,
            summary_files INTEGER,
            summary_diffs TEXT,
            metadata TEXT,
            cost REAL DEFAULT 0 NOT NULL,
            tokens_input INTEGER DEFAULT 0 NOT NULL,
            tokens_output INTEGER DEFAULT 0 NOT NULL,
            tokens_reasoning INTEGER DEFAULT 0 NOT NULL,
            tokens_cache_read INTEGER DEFAULT 0 NOT NULL,
            tokens_cache_write INTEGER DEFAULT 0 NOT NULL,
            revert TEXT,
            permission TEXT,
            agent TEXT,
            model TEXT,
            time_created INTEGER NOT NULL,
            time_updated INTEGER NOT NULL,
            time_compacting INTEGER,
            time_archived INTEGER
        )
    """)
    conn.execute("""
        CREATE TABLE message (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            time_created INTEGER NOT NULL,
            time_updated INTEGER NOT NULL,
            data TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE part (
            id TEXT PRIMARY KEY,
            message_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            time_created INTEGER NOT NULL,
            time_updated INTEGER NOT NULL,
            data TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE project (
            id TEXT PRIMARY KEY,
            worktree TEXT NOT NULL,
            vcs TEXT,
            name TEXT,
            icon_url TEXT,
            icon_url_override TEXT,
            icon_color TEXT,
            time_created INTEGER NOT NULL,
            time_updated INTEGER NOT NULL,
            time_initialized INTEGER,
            sandboxes TEXT NOT NULL,
            commands TEXT
        )
    """)

    # Insert test sessions (use named columns to avoid positional mismatch)
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
            "ses_002", "proj_a", "ws_1", None, "another-repo",
            "/home/zach/workspace/another-repo", None, "Second session",
            "1.0.0", None, 0, 0, 0, None, None, 0.01,
            200, 100, 0, 50, 50,
            None, None, "build",
            '{"id":"gpt-4o","providerID":"openai"}',
            1700001000000, 1700001030000,
        ),
    )

    # Insert test messages for ses_001
    conn.execute(
        "INSERT INTO message VALUES (?,?,?,?,?)",
        (
            "msg_001", "ses_001", 1700000001000, 1700000001000,
            json.dumps({
                "role": "user",
                "time": {"created": 1700000001000},
                "agent": "build",
                "model": {"providerID": "openrouter", "modelID": "deepseek/deepseek-v4-flash"},
            }),
        ),
    )
    conn.execute(
        "INSERT INTO message VALUES (?,?,?,?,?)",
        (
            "msg_002", "ses_001", 1700000002000, 1700000003000,
            json.dumps({
                "role": "assistant",
                "time": {"created": 1700000002000, "completed": 1700000003000},
                "agent": "build",
                "cost": 0.001,
                "tokens": {"input": 500, "output": 100, "reasoning": 50},
            }),
        ),
    )

    # Insert test parts for msg_001
    conn.execute(
        "INSERT INTO part VALUES (?,?,?,?,?,?)",
        (
            "part_001", "msg_001", "ses_001", 1700000001000, 1700000001000,
            json.dumps({"type": "text", "text": "Hello, please help me."}),
        ),
    )
    conn.execute(
        "INSERT INTO part VALUES (?,?,?,?,?,?)",
        (
            "part_002", "msg_002", "ses_001", 1700000002000, 1700000003000,
            json.dumps({"type": "tool", "tool": "bash", "callID": "call_123",
                        "state": {"status": "completed"}}),
        ),
    )
    conn.execute(
        "INSERT INTO part VALUES (?,?,?,?,?,?)",
        (
            "part_003", "msg_002", "ses_001", 1700000002500, 1700000003000,
            json.dumps({"type": "text", "text": "Here is the solution."}),
        ),
    )

    conn.commit()
    conn.close()
    return db_path


# --------------------------------------------------------------------------- #
# project_basename
# --------------------------------------------------------------------------- #
class TestProjectBasename:
    def test_normal_path(self):
        assert S.project_basename("/home/zach/workspace/my-repo") == "my-repo"

    def test_trailing_slash(self):
        assert S.project_basename("/home/zach/workspace/my-repo/") == "my-repo"

    def test_empty_string(self):
        assert S.project_basename("") == ""

    def test_none(self):
        assert S.project_basename(None) == ""

    def test_single_segment(self):
        assert S.project_basename("my-repo") == "my-repo"

    def test_nested_path(self):
        assert S.project_basename("/a/b/c/d/deep-repo") == "deep-repo"


# --------------------------------------------------------------------------- #
# to_ch_ts
# --------------------------------------------------------------------------- #
class TestToChTs:
    def test_epoch_zero(self):
        result = S.to_ch_ts(0)
        assert result == "1970-01-01 00:00:00.000"

    def test_recent_timestamp(self):
        # 2024-01-01 00:00:00 UTC = 1704067200000 ms
        result = S.to_ch_ts(1704067200000)
        assert result == "2024-01-01 00:00:00.000"

    def test_milliseconds_preserved(self):
        # 2024-01-01 00:00:00.123 UTC
        result = S.to_ch_ts(1704067200123)
        assert result == "2024-01-01 00:00:00.123"

    def test_large_timestamp(self):
        # 2026-07-29 15:00:00.456 UTC
        result = S.to_ch_ts(1785337200456)
        assert result == "2026-07-29 15:00:00.456"


# --------------------------------------------------------------------------- #
# to_ch_ts_from_iso
# --------------------------------------------------------------------------- #
class TestToChTsFromIso:
    def test_utc_z(self):
        result = S.to_ch_ts_from_iso("2024-01-01T00:00:00Z")
        assert result == "2024-01-01 00:00:00.000"

    def test_utc_offset(self):
        result = S.to_ch_ts_from_iso("2024-01-01T00:00:00+00:00")
        assert result == "2024-01-01 00:00:00.000"

    def test_with_milliseconds(self):
        result = S.to_ch_ts_from_iso("2024-01-01T00:00:00.500Z")
        assert result == "2024-01-01 00:00:00.500"

    def test_local_to_utc(self):
        # +05:00 → subtract 5h → 2024-01-01 00:00:00 UTC
        result = S.to_ch_ts_from_iso("2024-01-01T05:00:00+05:00")
        assert result == "2024-01-01 00:00:00.000"

    def test_empty_string(self):
        assert S.to_ch_ts_from_iso("") is None

    def test_none(self):
        assert S.to_ch_ts_from_iso(None) is None

    def test_invalid(self):
        assert S.to_ch_ts_from_iso("not-a-date") is None


# --------------------------------------------------------------------------- #
# opencode_data_dir
# --------------------------------------------------------------------------- #
class TestOpencodeDataDir:
    def test_default(self, monkeypatch):
        monkeypatch.delenv("OPENCODE_DATA_DIR", raising=False)
        result = S.opencode_data_dir()
        assert result == Path.home() / ".local" / "share" / "opencode"

    def test_env_override(self, monkeypatch, tmp_path):
        custom = tmp_path / "custom-opencode"
        monkeypatch.setenv("OPENCODE_DATA_DIR", str(custom))
        assert S.opencode_data_dir() == custom


# --------------------------------------------------------------------------- #
# opencode_db_path
# --------------------------------------------------------------------------- #
class TestOpencodeDbPath:
    def test_stable_db_found(self, env, monkeypatch):
        db = env["opencode_dir"] / "opencode-stable.db"
        db.touch()
        monkeypatch.delenv("OPENCODE_DB_PATH", raising=False)
        result = S.opencode_db_path()
        assert result == db

    def test_explicit_env(self, env, monkeypatch, tmp_path):
        db = tmp_path / "explicit.db"
        db.touch()
        monkeypatch.setenv("OPENCODE_DB_PATH", str(db))
        result = S.opencode_db_path()
        assert result == db

    def test_env_nonexistent_returns_none(self, env, monkeypatch):
        monkeypatch.setenv("OPENCODE_DB_PATH", "/nonexistent/path.db")
        assert S.opencode_db_path() is None

    def test_glob_fallback(self, env, monkeypatch):
        monkeypatch.delenv("OPENCODE_DB_PATH", raising=False)
        db = env["opencode_dir"] / "some-other.db"
        db.touch()
        result = S.opencode_db_path()
        assert result == db

    def test_no_db_returns_none(self, env, monkeypatch):
        monkeypatch.delenv("OPENCODE_DB_PATH", raising=False)
        assert S.opencode_db_path() is None


# --------------------------------------------------------------------------- #
# get_db
# --------------------------------------------------------------------------- #
class TestGetDb:
    def test_returns_readonly_conn(self, sample_db):
        conn = S.get_db(sample_db)
        assert conn is not None
        try:
            # Verify readonly by trying to write
            with pytest.raises(sqlite3.OperationalError):
                conn.execute("INSERT INTO session (id) VALUES ('x')")
        finally:
            conn.close()

    def test_row_factory(self, sample_db):
        conn = S.get_db(sample_db)
        try:
            row = conn.execute("SELECT id FROM session LIMIT 1").fetchone()
            assert isinstance(row, sqlite3.Row)
            assert row["id"] == "ses_001"
        finally:
            conn.close()

    def test_no_db_returns_none(self, tmp_path):
        result = S.get_db(tmp_path / "nonexistent.db")
        assert result is None


# --------------------------------------------------------------------------- #
# iter_sessions
# --------------------------------------------------------------------------- #
class TestIterSessions:
    def test_yields_all_sessions(self, sample_db):
        conn = S.get_db(sample_db)
        try:
            sessions = list(S.iter_sessions(conn))
            assert len(sessions) == 2
            assert sessions[0]["id"] == "ses_001"
            assert sessions[1]["id"] == "ses_002"
        finally:
            conn.close()

    def test_normalized_fields(self, sample_db):
        conn = S.get_db(sample_db)
        try:
            s = list(S.iter_sessions(conn))[0]
            assert s["title"] == "Test session"
            assert s["agent"] == "build"
            assert s["cost"] == 0.05
            assert s["tokens"]["input"] == 1000
            assert s["tokens"]["cache"]["read"] == 300
            assert s["model"]["id"] == "deepseek/deepseek-v4-flash"
        finally:
            conn.close()

    def test_since_ts_filter(self, sample_db):
        conn = S.get_db(sample_db)
        try:
            # ses_001 created at 1700000000000, ses_002 at 1700001000000
            sessions = list(S.iter_sessions(conn, since_ts=1700000500000))
            assert len(sessions) == 1
            assert sessions[0]["id"] == "ses_002"
        finally:
            conn.close()

    def test_empty_db(self, tmp_path):
        db = tmp_path / "empty.db"
        conn = sqlite3.connect(db)
        conn.execute("""
            CREATE TABLE session (
                id TEXT PRIMARY KEY, project_id TEXT, workspace_id TEXT,
                parent_id TEXT, slug TEXT, directory TEXT, path TEXT,
                title TEXT, version TEXT, share_url TEXT,
                summary_additions INTEGER, summary_deletions INTEGER,
                summary_files INTEGER, summary_diffs TEXT, metadata TEXT,
                cost REAL, tokens_input INTEGER, tokens_output INTEGER,
                tokens_reasoning INTEGER, tokens_cache_read INTEGER,
                tokens_cache_write INTEGER, revert TEXT, permission TEXT,
                agent TEXT, model TEXT, time_created INTEGER,
                time_updated INTEGER, time_compacting INTEGER,
                time_archived INTEGER
            )
        """)
        conn.commit()
        c = S.get_db(db)
        try:
            assert list(S.iter_sessions(c)) == []
        finally:
            c.close()


# --------------------------------------------------------------------------- #
# iter_messages
# --------------------------------------------------------------------------- #
class TestIterMessages:
    def test_yields_messages_for_session(self, sample_db):
        conn = S.get_db(sample_db)
        try:
            msgs = list(S.iter_messages(conn, "ses_001"))
            assert len(msgs) == 2
            assert msgs[0]["role"] == "user"
            assert msgs[1]["role"] == "assistant"
        finally:
            conn.close()

    def test_filters_by_session_id(self, sample_db):
        conn = S.get_db(sample_db)
        try:
            msgs = list(S.iter_messages(conn, "ses_002"))
            assert len(msgs) == 0
        finally:
            conn.close()

    def test_parsed_data_fields(self, sample_db):
        conn = S.get_db(sample_db)
        try:
            msgs = list(S.iter_messages(conn, "ses_001"))
            m = msgs[0]
            assert m["id"] == "msg_001"
            assert m["agent"] == "build"
            assert m["model"]["modelID"] == "deepseek/deepseek-v4-flash"
            # Assistant message has cost and tokens
            a = msgs[1]
            assert a["cost"] == 0.001
            assert a["tokens"]["input"] == 500
        finally:
            conn.close()


# --------------------------------------------------------------------------- #
# iter_parts
# --------------------------------------------------------------------------- #
class TestIterParts:
    def test_yields_parts_for_message(self, sample_db):
        conn = S.get_db(sample_db)
        try:
            parts = list(S.iter_parts(conn, "msg_002"))
            assert len(parts) == 2
            assert parts[0]["type"] == "tool"
            assert parts[0]["tool"] == "bash"
            assert parts[1]["type"] == "text"
        finally:
            conn.close()

    def test_filters_by_message_id(self, sample_db):
        conn = S.get_db(sample_db)
        try:
            parts = list(S.iter_parts(conn, "msg_nonexistent"))
            assert len(parts) == 0
        finally:
            conn.close()

    def test_text_part_has_text(self, sample_db):
        conn = S.get_db(sample_db)
        try:
            parts = list(S.iter_parts(conn, "msg_001"))
            assert len(parts) == 1
            assert parts[0]["text"] == "Hello, please help me."
        finally:
            conn.close()

    def test_tool_part_has_state(self, sample_db):
        conn = S.get_db(sample_db)
        try:
            parts = list(S.iter_parts(conn, "msg_002"))
            tool_part = parts[0]
            assert tool_part["call_id"] == "call_123"
            assert tool_part["state"]["status"] == "completed"
        finally:
            conn.close()


# --------------------------------------------------------------------------- #
# spool_emit roundtrip
# --------------------------------------------------------------------------- #
class TestSpoolEmitRoundtrip:
    def test_roundtrip_through_parse_line(self, env):
        fields = {
            "source": "opencode",
            "kind": "session-summary",
            "project": "my-repo",
            "text": "hello world",
            "session": "ses_001",
        }
        line = S.spool_emit(fields, spool_dir=env["spool"])
        assert line is not None

        # Read back from spool
        cur = env["spool"] / "current.log"
        assert cur.exists()
        lines = cur.read_text().strip().splitlines()
        assert len(lines) == 1

        # Parse with collector
        ev = C.parse_line(lines[0])
        assert ev is not None
        assert ev["source"] == "opencode"
        assert ev["kind"] == "session-summary"
        assert ev["project"] == "my-repo"
        assert ev["text"] == "hello world"
        assert ev["session"] == "ses_001"

    def test_arbitrary_content_survives(self, env):
        nasty = 'rm -rf "$X"\twith\ttabs\nand a newline \\back\\slash 你好 password123!'
        fields = {"source": "opencode", "kind": "test", "text": nasty}
        S.spool_emit(fields, spool_dir=env["spool"])
        lines = (env["spool"] / "current.log").read_text().strip().splitlines()
        ev = C.parse_line(lines[0])
        assert ev["text"] == nasty

    def test_multiple_emits_append(self, env):
        for i in range(3):
            S.spool_emit(
                {"source": "opencode", "kind": "test", "text": f"msg_{i}"},
                spool_dir=env["spool"],
            )
        lines = (env["spool"] / "current.log").read_text().strip().splitlines()
        assert len(lines) == 3
        texts = [C.parse_line(l)["text"] for l in lines]
        assert texts == ["msg_0", "msg_1", "msg_2"]
