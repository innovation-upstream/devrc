"""Tests for opencode/session_tailer.py — session rollup and tailer.

Run: python -m pytest scripts/collector/opencode/tests/test_session_tailer.py -v
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# Ensure the collector parent is on sys.path for collector.parse_line
COLLECTOR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(COLLECTOR))
# Also add keylog for spool_emit import
KEYLOG = COLLECTOR / "keylog"
sys.path.insert(0, str(KEYLOG))
# And the opencode dir itself
OPENCODE = COLLECTOR / "opencode"
sys.path.insert(0, str(OPENCODE))

import _shared as S  # noqa: E402
import collector as C  # noqa: E402
import session_tailer as T  # noqa: E402


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
@pytest.fixture
def env(tmp_path, monkeypatch):
    """Set up isolated env dirs for tests."""
    spool = tmp_path / "spool"
    spool.mkdir()
    monkeypatch.setenv("ACTIVITY_SPOOL_DIR", str(spool))
    return {"spool": spool}


@pytest.fixture
def sample_db(tmp_path):
    """Create a temporary SQLite DB with the real OpenCode schema and test data."""
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

    # Session 1: typical session with 60s duration, cost 0.05
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

    # Session 2: empty session (no messages)
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
            "ses_002", "proj_a", "ws_1", None, "empty-repo",
            "/home/zach/workspace/empty-repo", None, "Empty session",
            "1.0.0", None, 0, 0, 0, None, None, 0.0,
            0, 0, 0, 0, 0,
            None, None, "build",
            '{"id":"gpt-4o","providerID":"openai"}',
            1700001000000, 1700001030000,
        ),
    )

    # --- Messages for ses_001 ---
    # User message
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
    # Assistant message
    conn.execute(
        "INSERT INTO message VALUES (?,?,?,?,?)",
        (
            "msg_002", "ses_001", 1700000002000, 1700000003000,
            json.dumps({
                "role": "assistant",
                "time": {"created": 1700000002000, "completed": 1700000003000},
                "agent": "build",
                "model": {"providerID": "openrouter", "modelID": "deepseek/deepseek-v4-flash"},
                "cost": 0.001,
                "tokens": {"input": 500, "output": 100, "reasoning": 50},
            }),
        ),
    )
    # Second user message (to test multiple user messages)
    conn.execute(
        "INSERT INTO message VALUES (?,?,?,?,?)",
        (
            "msg_003", "ses_001", 1700000004000, 1700000004000,
            json.dumps({
                "role": "user",
                "time": {"created": 1700000004000},
                "agent": "build",
            }),
        ),
    )

    # --- Parts for ses_001 ---
    # Text part (user message content)
    conn.execute(
        "INSERT INTO part VALUES (?,?,?,?,?,?)",
        (
            "part_001", "msg_001", "ses_001", 1700000001000, 1700000001000,
            json.dumps({"type": "text", "text": "Hello, please help me."}),
        ),
    )
    # Bash tool part (git commit)
    conn.execute(
        "INSERT INTO part VALUES (?,?,?,?,?,?)",
        (
            "part_002", "msg_002", "ses_001", 1700000002000, 1700000003000,
            json.dumps({
                "type": "tool", "tool": "bash",
                "state": {"status": "completed"},
                "command": "git commit -m 'feat: add feature'",
            }),
        ),
    )
    # Bash tool part (git push)
    conn.execute(
        "INSERT INTO part VALUES (?,?,?,?,?,?)",
        (
            "part_003", "msg_002", "ses_001", 1700000002100, 1700000003000,
            json.dumps({
                "type": "tool", "tool": "bash",
                "state": {"status": "completed"},
                "command": "git push origin main",
            }),
        ),
    )
    # Edit tool part (Python file)
    conn.execute(
        "INSERT INTO part VALUES (?,?,?,?,?,?)",
        (
            "part_004", "msg_002", "ses_001", 1700000002200, 1700000003000,
            json.dumps({
                "type": "tool", "tool": "edit",
                "state": {"status": "completed"},
                "file_path": "/home/zach/workspace/my-repo/src/main.py",
            }),
        ),
    )
    # Edit tool part (TypeScript file)
    conn.execute(
        "INSERT INTO part VALUES (?,?,?,?,?,?)",
        (
            "part_005", "msg_002", "ses_001", 1700000002300, 1700000003000,
            json.dumps({
                "type": "tool", "tool": "edit",
                "state": {"status": "completed"},
                "file_path": "/home/zach/workspace/my-repo/src/app.ts",
            }),
        ),
    )
    # Bash tool part with error
    conn.execute(
        "INSERT INTO part VALUES (?,?,?,?,?,?)",
        (
            "part_006", "msg_002", "ses_001", 1700000002400, 1700000003000,
            json.dumps({
                "type": "tool", "tool": "bash",
                "state": {"status": "error", "error": "command not found: xyz"},
            }),
        ),
    )
    # MCP tool part
    conn.execute(
        "INSERT INTO part VALUES (?,?,?,?,?,?)",
        (
            "part_007", "msg_002", "ses_001", 1700000002500, 1700000003000,
            json.dumps({
                "type": "tool", "tool": "mcp__github__list_issues",
                "state": {"status": "completed"},
            }),
        ),
    )
    # Text part (assistant response)
    conn.execute(
        "INSERT INTO part VALUES (?,?,?,?,?,?)",
        (
            "part_008", "msg_002", "ses_001", 1700000002600, 1700000003000,
            json.dumps({"type": "text", "text": "Here is the solution."}),
        ),
    )

    conn.commit()
    conn.close()
    return db_path


# --------------------------------------------------------------------------- #
# build_rollup — typical session
# --------------------------------------------------------------------------- #
class TestBuildRollupTypical:
    def test_all_fields_populated(self, sample_db):
        conn = sqlite3.connect(str(sample_db))
        conn.row_factory = sqlite3.Row
        try:
            sessions = list(S.iter_sessions(conn))
            session = sessions[0]  # ses_001
            messages = list(S.iter_messages(conn, session["id"]))
            all_parts = []
            for msg in messages:
                all_parts.extend(S.iter_parts(conn, msg["id"]))

            rollup = T.build_rollup(session, messages, all_parts)

            assert rollup["cost"] == 0.05
            assert rollup["input_tokens"] == 1000
            assert rollup["output_tokens"] == 500
            assert rollup["reasoning_tokens"] == 200
            assert rollup["cache_read_tokens"] == 300
            assert rollup["cache_write_tokens"] == 100
            assert rollup["start_ts"] == "2023-11-14 22:13:20.000"
            assert rollup["end_ts"] == "2023-11-14 22:14:20.000"
            assert rollup["duration_minutes"] == 1.0
            assert rollup["unreadable"] is False
            assert rollup["models"] == ["deepseek/deepseek-v4-flash"]
        finally:
            conn.close()


# --------------------------------------------------------------------------- #
# build_rollup — empty messages
# --------------------------------------------------------------------------- #
class TestBuildRollupEmpty:
    def test_unreadable_when_no_messages(self, sample_db):
        conn = sqlite3.connect(str(sample_db))
        conn.row_factory = sqlite3.Row
        try:
            sessions = list(S.iter_sessions(conn))
            session = [s for s in sessions if s["id"] == "ses_002"][0]
            messages = list(S.iter_messages(conn, session["id"]))
            all_parts = []
            for msg in messages:
                all_parts.extend(S.iter_parts(conn, msg["id"]))

            rollup = T.build_rollup(session, messages, all_parts)

            assert rollup["unreadable"] is True
            assert rollup["user_message_count"] == 0
            assert rollup["assistant_message_count"] == 0
        finally:
            conn.close()


# --------------------------------------------------------------------------- #
# build_rollup — tool counting
# --------------------------------------------------------------------------- #
class TestBuildRollupToolCounts:
    def test_tool_counts(self, sample_db):
        conn = sqlite3.connect(str(sample_db))
        conn.row_factory = sqlite3.Row
        try:
            sessions = list(S.iter_sessions(conn))
            session = sessions[0]
            messages = list(S.iter_messages(conn, session["id"]))
            all_parts = []
            for msg in messages:
                all_parts.extend(S.iter_parts(conn, msg["id"]))

            rollup = T.build_rollup(session, messages, all_parts)

            tc = rollup["tool_counts"]
            assert tc["bash"] == 3  # git commit, git push, error bash
            assert tc["edit"] == 2  # two edit parts
            assert tc["mcp__github__list_issues"] == 1
        finally:
            conn.close()


# --------------------------------------------------------------------------- #
# build_rollup — git detection
# --------------------------------------------------------------------------- #
class TestBuildRollupGit:
    def test_git_commit_detected(self, sample_db):
        conn = sqlite3.connect(str(sample_db))
        conn.row_factory = sqlite3.Row
        try:
            sessions = list(S.iter_sessions(conn))
            session = sessions[0]
            messages = list(S.iter_messages(conn, session["id"]))
            all_parts = []
            for msg in messages:
                all_parts.extend(S.iter_parts(conn, msg["id"]))

            rollup = T.build_rollup(session, messages, all_parts)

            assert rollup["git_commits"] == 1
            assert rollup["git_pushes"] == 1
        finally:
            conn.close()


# --------------------------------------------------------------------------- #
# build_rollup — language detection
# --------------------------------------------------------------------------- #
class TestBuildRollupLanguages:
    def test_languages_from_edit_parts(self, sample_db):
        conn = sqlite3.connect(str(sample_db))
        conn.row_factory = sqlite3.Row
        try:
            sessions = list(S.iter_sessions(conn))
            session = sessions[0]
            messages = list(S.iter_messages(conn, session["id"]))
            all_parts = []
            for msg in messages:
                all_parts.extend(S.iter_parts(conn, msg["id"]))

            rollup = T.build_rollup(session, messages, all_parts)

            assert rollup["languages"] == {"Python": 1, "TypeScript": 1}
            assert rollup["files_modified"] == 2
        finally:
            conn.close()


# --------------------------------------------------------------------------- #
# build_rollup — cost aggregation
# --------------------------------------------------------------------------- #
class TestBuildRollupCost:
    def test_cost_from_session_data(self, sample_db):
        conn = sqlite3.connect(str(sample_db))
        conn.row_factory = sqlite3.Row
        try:
            sessions = list(S.iter_sessions(conn))
            session = sessions[0]
            messages = list(S.iter_messages(conn, session["id"]))
            all_parts = []
            for msg in messages:
                all_parts.extend(S.iter_parts(conn, msg["id"]))

            rollup = T.build_rollup(session, messages, all_parts)

            assert rollup["cost"] == 0.05
        finally:
            conn.close()


# --------------------------------------------------------------------------- #
# build_rollup — token aggregation
# --------------------------------------------------------------------------- #
class TestBuildRollupTokens:
    def test_tokens_from_session_data(self, sample_db):
        conn = sqlite3.connect(str(sample_db))
        conn.row_factory = sqlite3.Row
        try:
            sessions = list(S.iter_sessions(conn))
            session = sessions[0]
            messages = list(S.iter_messages(conn, session["id"]))
            all_parts = []
            for msg in messages:
                all_parts.extend(S.iter_parts(conn, msg["id"]))

            rollup = T.build_rollup(session, messages, all_parts)

            assert rollup["input_tokens"] == 1000
            assert rollup["output_tokens"] == 500
            assert rollup["reasoning_tokens"] == 200
            assert rollup["cache_read_tokens"] == 300
            assert rollup["cache_write_tokens"] == 100
        finally:
            conn.close()


# --------------------------------------------------------------------------- #
# build_rollup — duration
# --------------------------------------------------------------------------- #
class TestBuildRollupDuration:
    def test_duration_minutes(self, sample_db):
        conn = sqlite3.connect(str(sample_db))
        conn.row_factory = sqlite3.Row
        try:
            sessions = list(S.iter_sessions(conn))
            session = sessions[0]
            messages = list(S.iter_messages(conn, session["id"]))
            all_parts = []
            for msg in messages:
                all_parts.extend(S.iter_parts(conn, msg["id"]))

            rollup = T.build_rollup(session, messages, all_parts)

            # time_updated - time_created = 60000 ms = 1 minute
            assert rollup["duration_minutes"] == 1.0
        finally:
            conn.close()


# --------------------------------------------------------------------------- #
# Signature / state persistence
# --------------------------------------------------------------------------- #
class TestSignature:
    def test_signature_computed_from_session(self, sample_db):
        conn = sqlite3.connect(str(sample_db))
        conn.row_factory = sqlite3.Row
        try:
            sessions = list(S.iter_sessions(conn))
            sig = T.signature(sessions[0])
            assert sig == "1700000060000:0.05:1000"
        finally:
            conn.close()


class TestStatePersistence:
    def test_signature_unchanged_skip(self, sample_db, tmp_path):
        state_file = tmp_path / "state.json"
        conn = sqlite3.connect(str(sample_db))
        conn.row_factory = sqlite3.Row
        try:
            sessions = list(S.iter_sessions(conn))
            sig = T.signature(sessions[0])

            # Save state with current signature
            T.save_state(state_file, {sessions[0]["id"]: sig})

            # Load and verify it matches
            loaded = T.load_state(state_file)
            assert loaded[sessions[0]["id"]] == sig

            # If prev[sid] == sig, session should be skipped
            prev = T.load_state(state_file)
            assert prev.get(sessions[0]["id"]) == sig
        finally:
            conn.close()

    def test_signature_changed_reemit(self, sample_db, tmp_path):
        state_file = tmp_path / "state.json"
        conn = sqlite3.connect(str(sample_db))
        conn.row_factory = sqlite3.Row
        try:
            sessions = list(S.iter_sessions(conn))
            old_sig = T.signature(sessions[0])

            # Save with a different (old) signature
            T.save_state(state_file, {sessions[0]["id"]: "old_sig"})

            # Load and verify it differs
            loaded = T.load_state(state_file)
            assert loaded[sessions[0]["id"]] != old_sig
        finally:
            conn.close()

    def test_new_session_emit(self, sample_db, tmp_path):
        state_file = tmp_path / "state.json"
        conn = sqlite3.connect(str(sample_db))
        conn.row_factory = sqlite3.Row
        try:
            sessions = list(S.iter_sessions(conn))
            sid = sessions[0]["id"]

            # Empty state — no previous signatures
            T.save_state(state_file, {})
            loaded = T.load_state(state_file)
            assert sid not in loaded
        finally:
            conn.close()

    def test_deleted_session_pruned(self, sample_db, tmp_path):
        state_file = tmp_path / "state.json"

        # State has a session that no longer exists
        T.save_state(state_file, {"ses_deleted": "old:0:0", "ses_001": "1700000060000:0.05:1000"})

        conn = sqlite3.connect(str(sample_db))
        conn.row_factory = sqlite3.Row
        try:
            seen = set()
            for s in S.iter_sessions(conn):
                seen.add(s["id"])

            # Prune: keep only seen sessions
            state = T.load_state(state_file)
            pruned = {sid: sig for sid, sig in state.items() if sid in seen}
            T.save_state(state_file, pruned)

            final = T.load_state(state_file)
            assert "ses_deleted" not in final
            assert "ses_001" in final
        finally:
            conn.close()


# --------------------------------------------------------------------------- #
# Checkpoint every 25 emits
# --------------------------------------------------------------------------- #
class TestCheckpoint:
    def test_checkpoint_every_25(self):
        assert T.CHECKPOINT_EVERY == 25


# --------------------------------------------------------------------------- #
# Full roundtrip: create DB → run() → read spool → parse_line → verify
# --------------------------------------------------------------------------- #
class TestFullRoundtrip:
    def test_roundtrip_through_run(self, sample_db, env, monkeypatch):
        """Create DB, run the tailer, verify events in spool parse correctly."""
        monkeypatch.setenv("OPENCODE_SESSION_STATE", str(env["spool"] / "state.json"))

        # Monkeypatch get_db to return a writable connection
        def mock_get_db(path=None):
            db = path or sample_db
            if db is None or not db.exists():
                return None
            conn = sqlite3.connect(str(db))
            conn.row_factory = sqlite3.Row
            return conn

        monkeypatch.setattr(S, "get_db", mock_get_db)

        # Run the tailer
        rc = T.run(db_path=sample_db)
        assert rc == 0

        # Read spool and parse events
        spool_file = env["spool"] / "current.log"
        assert spool_file.exists()
        lines = spool_file.read_text().strip().splitlines()
        assert len(lines) == 2  # ses_001 + ses_002

        # Parse first event (ses_001)
        ev1 = C.parse_line(lines[0])
        assert ev1 is not None
        assert ev1["source"] == "opencode"
        assert ev1["kind"] == "session-summary"
        assert ev1["session"] == "ses_001"
        assert ev1["project"] == "my-repo"
        assert ev1["app"] == "opencode"
        # Verify payload is valid JSON
        payload1 = json.loads(ev1["payload"])
        assert payload1["cost"] == 0.05
        assert payload1["input_tokens"] == 1000
        assert payload1["tool_counts"]["bash"] == 3
        assert payload1["git_commits"] == 1
        assert payload1["git_pushes"] == 1
        assert payload1["languages"]["Python"] == 1
        assert payload1["languages"]["TypeScript"] == 1
        assert payload1["files_modified"] == 2
        assert payload1["unreadable"] is False
        assert payload1["duration_minutes"] == 1.0

        # Parse second event (ses_002 — empty session)
        ev2 = C.parse_line(lines[1])
        assert ev2 is not None
        assert ev2["session"] == "ses_002"
        payload2 = json.loads(ev2["payload"])
        assert payload2["unreadable"] is True

    def test_idempotent_rerun(self, sample_db, env, monkeypatch):
        """Running twice should only emit once (signature unchanged)."""
        monkeypatch.setenv("OPENCODE_SESSION_STATE", str(env["spool"] / "state.json"))

        def mock_get_db(path=None):
            db = path or sample_db
            if db is None or not db.exists():
                return None
            conn = sqlite3.connect(str(db))
            conn.row_factory = sqlite3.Row
            return conn

        monkeypatch.setattr(S, "get_db", mock_get_db)

        # First run
        T.run(db_path=sample_db)
        spool_file = env["spool"] / "current.log"
        lines1 = spool_file.read_text().strip().splitlines()
        assert len(lines1) == 2

        # Second run — should emit nothing new
        T.run(db_path=sample_db)
        lines2 = spool_file.read_text().strip().splitlines()
        assert len(lines2) == 2  # no new lines appended


# --------------------------------------------------------------------------- #
# Integration with _shared.spool_emit — v1 line format
# --------------------------------------------------------------------------- #
class TestSpoolEmitIntegration:
    def test_event_has_v1_format(self, sample_db, env, monkeypatch):
        """Verify emitted lines are valid v1 format parseable by collector."""
        monkeypatch.setenv("OPENCODE_SESSION_STATE", str(env["spool"] / "state.json"))

        def mock_get_db(path=None):
            db = path or sample_db
            if db is None or not db.exists():
                return None
            conn = sqlite3.connect(str(db))
            conn.row_factory = sqlite3.Row
            return conn

        monkeypatch.setattr(S, "get_db", mock_get_db)

        T.run(db_path=sample_db)

        spool_file = env["spool"] / "current.log"
        lines = spool_file.read_text().strip().splitlines()
        for line in lines:
            # Must start with v1
            assert line.startswith("v1\t")
            # Must be parseable
            ev = C.parse_line(line)
            assert ev is not None, f"Failed to parse: {line}"
            # Must have required fields
            assert ev["source"] == "opencode"
            assert ev["kind"] == "session-summary"
            assert "ts" in ev
            assert "session" in ev


# --------------------------------------------------------------------------- #
# lang_for_path helpers
# --------------------------------------------------------------------------- #
class TestLangForPath:
    def test_python(self):
        assert T.lang_for_path("foo.py") == "Python"

    def test_typescript(self):
        assert T.lang_for_path("bar.tsx") == "TypeScript"

    def test_nix(self):
        assert T.lang_for_path("flake.nix") == "Nix"

    def test_dockerfile(self):
        assert T.lang_for_path("Dockerfile") == "Dockerfile"

    def test_makefile(self):
        assert T.lang_for_path("Makefile") == "Makefile"

    def test_unknown(self):
        assert T.lang_for_path("foo.xyz") is None

    def test_empty(self):
        assert T.lang_for_path("") is None

    def test_none(self):
        assert T.lang_for_path(None) is None


# --------------------------------------------------------------------------- #
# git detection helpers
# --------------------------------------------------------------------------- #
class TestGitDetection:
    def test_is_git_commit(self):
        assert T.is_git_commit("git commit -m 'feat: add'") is True

    def test_is_git_commit_with_flags(self):
        assert T.is_git_commit("git -C /path commit -m 'msg'") is True

    def test_is_not_git_commit(self):
        assert T.is_git_commit("git status") is False

    def test_is_git_push(self):
        assert T.is_git_push("git push origin main") is True

    def test_is_not_git_push(self):
        assert T.is_git_push("git pull") is False

    def test_empty_string(self):
        assert T.is_git_commit("") is False
        assert T.is_git_push("") is False


# --------------------------------------------------------------------------- #
# categorize_tool_error
# --------------------------------------------------------------------------- #
class TestCategorizeToolError:
    def test_timeout(self):
        assert T.categorize_tool_error("timed out after 30s") == "Timeout"

    def test_file_not_found(self):
        assert T.categorize_tool_error("no such file: /tmp/foo") == "File Not Found"

    def test_permission_denied(self):
        assert T.categorize_tool_error("Permission denied") == "Permission Denied"

    def test_command_failed(self):
        assert T.categorize_tool_error("exit code 1") == "Command Failed"

    def test_other(self):
        assert T.categorize_tool_error("something weird") == "Other"

    def test_empty(self):
        assert T.categorize_tool_error("") == "Other"


# --------------------------------------------------------------------------- #
# build_event
# --------------------------------------------------------------------------- #
class TestBuildEvent:
    def test_event_shape(self):
        rollup = {
            "start_ts": "2024-01-15 22:13:20.000",
            "cost": 0.05,
            "unreadable": False,
        }
        ev = T.build_event("ses_001", "/home/zach/workspace/my-repo", rollup)
        assert ev["source"] == "opencode"
        assert ev["kind"] == "session-summary"
        assert ev["session"] == "ses_001"
        assert ev["project"] == "my-repo"
        assert ev["cwd"] == "/home/zach/workspace/my-repo"
        assert ev["ts"] == "2024-01-15 22:13:20.000"
        assert ev["app"] == "opencode"
        payload = json.loads(ev["payload"])
        assert payload["cost"] == 0.05
