"""Tests for opencode/tailer.py — per-message activity event tailer.

Run: python -m pytest scripts/collector/opencode/tests/test_tailer.py -v
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


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
@pytest.fixture
def env(tmp_path, monkeypatch):
    spool = tmp_path / "spool"
    spool.mkdir()
    monkeypatch.setenv("ACTIVITY_SPOOL_DIR", str(spool))
    return {"spool": spool}


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

    # Session 1: typical session
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

    # Session 2: different project
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
    # User message with text
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
    # Assistant message with tool calls
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
    # User noise message (very short)
    conn.execute(
        "INSERT INTO message VALUES (?,?,?,?,?)",
        (
            "msg_noisy", "ses_001", 1700000005000, 1700000005000,
            json.dumps({
                "role": "user",
                "time": {"created": 1700000005000},
                "agent": "build",
            }),
        ),
    )
    # User slash-command message
    conn.execute(
        "INSERT INTO message VALUES (?,?,?,?,?)",
        (
            "msg_cmd", "ses_001", 1700000006000, 1700000006000,
            json.dumps({
                "role": "user",
                "time": {"created": 1700000006000},
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
    # Assistant text part
    conn.execute(
        "INSERT INTO part VALUES (?,?,?,?,?,?)",
        (
            "part_002", "msg_002", "ses_001", 1700000002100, 1700000003000,
            json.dumps({"type": "text", "text": "Here is the solution."}),
        ),
    )
    # Bash tool part (assistant)
    conn.execute(
        "INSERT INTO part VALUES (?,?,?,?,?,?)",
        (
            "part_003", "msg_002", "ses_001", 1700000002000, 1700000003000,
            json.dumps({
                "type": "tool", "tool": "bash",
                "state": {"status": "completed"},
                "command": "git commit -m 'feat: add feature'",
            }),
        ),
    )
    # Edit tool part (assistant)
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
    # Noisy user message — empty text part
    conn.execute(
        "INSERT INTO part VALUES (?,?,?,?,?,?)",
        (
            "part_noisy", "msg_noisy", "ses_001", 1700000005000, 1700000005000,
            json.dumps({"type": "text", "text": "x"}),
        ),
    )
    # Slash-command user message
    conn.execute(
        "INSERT INTO part VALUES (?,?,?,?,?,?)",
        (
            "part_cmd", "msg_cmd", "ses_001", 1700000006000, 1700000006000,
            json.dumps({"type": "text", "text": "/status"}),
        ),
    )

    # --- Messages for ses_002 ---
    conn.execute(
        "INSERT INTO message VALUES (?,?,?,?,?)",
        (
            "msg_s2_001", "ses_002", 1700001001000, 1700001001000,
            json.dumps({
                "role": "user",
                "time": {"created": 1700001001000},
                "agent": "build",
            }),
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


# --------------------------------------------------------------------------- #
# classify
# --------------------------------------------------------------------------- #
class TestClassify:
    def test_typed_message(self):
        assert T.classify("Hello, help me with this") == ("typed", "Hello, help me with this")

    def test_slash_command(self):
        assert T.classify("/status") == ("command", "/status")

    def test_slash_command_with_args(self):
        assert T.classify("/model deepseek") == ("command", "/model deepseek")

    def test_empty_string(self):
        assert T.classify("") is None

    def test_none(self):
        assert T.classify(None) is None

    def test_single_char(self):
        assert T.classify("x") is None

    def test_whitespace_only(self):
        assert T.classify("   ") is None

    def test_strips_whitespace(self):
        assert T.classify("  hello  ") == ("typed", "hello")


# --------------------------------------------------------------------------- #
# clean_text
# --------------------------------------------------------------------------- #
class TestCleanText:
    def test_strips_whitespace(self):
        assert T.clean_text("  hello  ") == "hello"

    def test_empty_string(self):
        assert T.clean_text("") == ""

    def test_none(self):
        assert T.clean_text(None) == ""

    def test_no_change(self):
        assert T.clean_text("already clean") == "already clean"


# --------------------------------------------------------------------------- #
# message_id
# --------------------------------------------------------------------------- #
class TestMessageId:
    def test_format(self):
        msg = {"session_id": "ses_001", "id": "msg_002"}
        assert T.message_id(msg) == "ses_001:msg_002"

    def test_different_sessions(self):
        m1 = {"session_id": "ses_a", "id": "msg_1"}
        m2 = {"session_id": "ses_b", "id": "msg_1"}
        assert T.message_id(m1) != T.message_id(m2)


# --------------------------------------------------------------------------- #
# extract_text
# --------------------------------------------------------------------------- #
class TestExtractText:
    def test_single_text_part(self):
        parts = [{"type": "text", "text": "hello"}]
        assert T.extract_text(parts) == "hello"

    def test_multiple_parts(self):
        parts = [
            {"type": "tool", "tool": "bash"},
            {"type": "text", "text": "response text"},
        ]
        assert T.extract_text(parts) == "response text"

    def test_no_text_parts(self):
        parts = [{"type": "tool", "tool": "bash"}, {"type": "reasoning"}]
        assert T.extract_text(parts) == ""

    def test_empty_list(self):
        assert T.extract_text([]) == ""

    def test_text_part_with_none(self):
        parts = [{"type": "text", "text": None}]
        assert T.extract_text(parts) == ""


# --------------------------------------------------------------------------- #
# extract_tool_calls
# --------------------------------------------------------------------------- #
class TestExtractToolCalls:
    def test_tool_invocation_parts(self):
        parts = [
            {
                "type": "tool", "tool": "bash",
                "state": {"status": "completed"},
                "_data": {"type": "tool", "tool": "bash", "command": "ls", "state": {"status": "completed"}},
            },
        ]
        calls = T.extract_tool_calls(parts)
        assert len(calls) == 1
        assert calls[0]["tool_name"] == "bash"
        assert calls[0]["state"] == {"status": "completed"}

    def test_mixed_part_types(self):
        parts = [
            {"type": "text", "text": "response"},
            {
                "type": "tool", "tool": "edit",
                "state": {"status": "completed"},
                "_data": {"type": "tool", "tool": "edit", "file_path": "foo.py"},
            },
            {"type": "reasoning"},
        ]
        calls = T.extract_tool_calls(parts)
        assert len(calls) == 1
        assert calls[0]["tool_name"] == "edit"

    def test_empty_parts(self):
        assert T.extract_tool_calls([]) == []

    def test_tool_invocation_type(self):
        parts = [
            {
                "type": "tool-invocation", "tool": "webfetch",
                "state": {"status": "completed"},
                "_data": {"type": "tool-invocation", "tool": "webfetch", "url": "https://example.com"},
            },
        ]
        calls = T.extract_tool_calls(parts)
        assert len(calls) == 1
        assert calls[0]["tool_name"] == "webfetch"

    def test_args_extracted(self):
        parts = [
            {
                "type": "tool", "tool": "bash",
                "state": {"status": "completed"},
                "_data": {"type": "tool", "tool": "bash", "command": "echo hi", "state": {"status": "completed"}},
            },
        ]
        calls = T.extract_tool_calls(parts)
        assert calls[0]["args"] == {"command": "echo hi"}


# --------------------------------------------------------------------------- #
# build_event
# --------------------------------------------------------------------------- #
class TestBuildEvent:
    def test_user_message_is_prompt(self):
        msg = {
            "id": "msg_001", "session_id": "ses_001",
            "role": "user", "time_created": 1700000001000,
            "agent": "build", "model": None, "cost": None,
            "tokens": {},
        }
        parts = [{"type": "text", "text": "Hello world"}]
        session = {"directory": "/home/zach/workspace/my-repo", "agent": "build"}
        ev = T.build_event(msg, parts, session)
        assert ev is not None
        assert ev["kind"] == "prompt"

    def test_assistant_message_is_turn(self):
        msg = {
            "id": "msg_002", "session_id": "ses_001",
            "role": "assistant", "time_created": 1700000002000,
            "agent": "build", "model": {"id": "deepseek"}, "cost": 0.001,
            "tokens": {"input": 500, "output": 100},
        }
        parts = [{"type": "text", "text": "Here is the answer."}]
        session = {"directory": "/home/zach/workspace/my-repo", "agent": "build"}
        ev = T.build_event(msg, parts, session)
        assert ev is not None
        assert ev["kind"] == "assistant-turn"

    def test_filters_noise_user_message(self):
        msg = {
            "id": "msg_x", "session_id": "ses_001",
            "role": "user", "time_created": 1700000005000,
            "agent": "build", "model": None, "cost": None,
            "tokens": {},
        }
        parts = [{"type": "text", "text": "x"}]
        session = {"directory": "/home/zach/workspace/my-repo"}
        assert T.build_event(msg, parts, session) is None

    def test_filters_empty_user_message(self):
        msg = {
            "id": "msg_x", "session_id": "ses_001",
            "role": "user", "time_created": 1700000005000,
            "agent": "build", "model": None, "cost": None,
            "tokens": {},
        }
        parts = []
        session = {"directory": "/home/zach/workspace/my-repo"}
        assert T.build_event(msg, parts, session) is None

    def test_filters_unknown_role(self):
        msg = {
            "id": "msg_x", "session_id": "ses_001",
            "role": "system", "time_created": 1700000005000,
            "agent": "build", "model": None, "cost": None,
            "tokens": {},
        }
        parts = [{"type": "text", "text": "system message"}]
        session = {"directory": "/home/zach/workspace/my-repo"}
        assert T.build_event(msg, parts, session) is None

    def test_payload_fields(self):
        msg = {
            "id": "msg_002", "session_id": "ses_001",
            "role": "assistant", "time_created": 1700000002000,
            "agent": "build",
            "model": {"providerID": "openrouter", "modelID": "deepseek-v4-flash"},
            "cost": 0.001,
            "tokens": {"input": 500, "output": 100},
        }
        parts = [
            {"type": "text", "text": "Done."},
            {
                "type": "tool", "tool": "bash",
                "state": {"status": "completed"},
                "_data": {"command": "ls"},
            },
        ]
        session = {"directory": "/home/zach/workspace/my-repo", "agent": "build"}
        ev = T.build_event(msg, parts, session)
        payload = json.loads(ev["payload"])
        assert payload["role"] == "assistant"
        assert payload["cost"] == 0.001
        assert payload["tokens_input"] == 500
        assert payload["tokens_output"] == 100
        assert payload["tool_count"] == 1
        assert payload["tool_calls"] == [{"name": "bash", "state": {"status": "completed"}}]

    def test_ts_matches_message_creation_time(self):
        msg = {
            "id": "msg_001", "session_id": "ses_001",
            "role": "user", "time_created": 1700000001000,
            "agent": "build", "model": None, "cost": None,
            "tokens": {},
        }
        parts = [{"type": "text", "text": "Hello"}]
        session = {"directory": "/home/zach/workspace/my-repo"}
        ev = T.build_event(msg, parts, session)
        assert ev["ts"] == S.to_ch_ts(1700000001000)

    def test_project_from_directory(self):
        msg = {
            "id": "msg_s2_001", "session_id": "ses_002",
            "role": "user", "time_created": 1700001001000,
            "agent": "build", "model": None, "cost": None,
            "tokens": {},
        }
        parts = [{"type": "text", "text": "Hello"}]
        session = {"directory": "/home/zach/workspace/another-repo"}
        ev = T.build_event(msg, parts, session)
        assert ev["project"] == "another-repo"
        assert ev["cwd"] == "/home/zach/workspace/another-repo"


# --------------------------------------------------------------------------- #
# State persistence
# --------------------------------------------------------------------------- #
class TestState:
    def test_load_empty(self, tmp_path):
        assert T.load_state(tmp_path / "nonexistent.json") == set()

    def test_save_and_load(self, tmp_path):
        path = tmp_path / "state.json"
        T.save_state(path, {"a", "b", "c"})
        loaded = T.load_state(path)
        assert loaded == {"a", "b", "c"}

    def test_atomic_write(self, tmp_path):
        path = tmp_path / "state.json"
        T.save_state(path, {"x"})
        # .tmp should not exist after atomic replace
        assert not (tmp_path / "state.json.tmp").exists()


# --------------------------------------------------------------------------- #
# State idempotency — run twice, second emits nothing
# --------------------------------------------------------------------------- #
class TestIdempotency:
    def test_rerun_emits_nothing(self, sample_db, env, monkeypatch):
        monkeypatch.setenv("OPENCODE_TAILER_STATE", str(env["spool"] / "state.json"))

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
        lines1 = spool_file.read_text().strip().splitlines()
        count1 = len(lines1)

        T.run(db_path=sample_db)
        lines2 = spool_file.read_text().strip().splitlines()
        assert len(lines2) == count1

    def test_new_message_emitted_after_state_update(self, sample_db, env, monkeypatch, tmp_path):
        state_file = env["spool"] / "state.json"
        monkeypatch.setenv("OPENCODE_TAILER_STATE", str(state_file))

        def mock_get_db(path=None):
            db = path or sample_db
            if db is None or not db.exists():
                return None
            conn = sqlite3.connect(str(db))
            conn.row_factory = sqlite3.Row
            return conn

        monkeypatch.setattr(S, "get_db", mock_get_db)

        # First run: emits all existing messages
        T.run(db_path=sample_db)
        spool_file = env["spool"] / "current.log"
        lines1 = spool_file.read_text().strip().splitlines()
        count1 = len(lines1)
        assert count1 > 0

        # Add a new message to the DB
        conn = sqlite3.connect(str(sample_db))
        conn.execute(
            "INSERT INTO message VALUES (?,?,?,?,?)",
            (
                "msg_new", "ses_001", 1700000099000, 1700000099000,
                json.dumps({
                    "role": "user",
                    "time": {"created": 1700000099000},
                    "agent": "build",
                }),
            ),
        )
        conn.execute(
            "INSERT INTO part VALUES (?,?,?,?,?,?)",
            (
                "part_new", "msg_new", "ses_001", 1700000099000, 1700000099000,
                json.dumps({"type": "text", "text": "A brand new message after state saved"}),
            ),
        )
        conn.commit()
        conn.close()

        # Second run: should emit the new message
        T.run(db_path=sample_db)
        lines2 = spool_file.read_text().strip().splitlines()
        assert len(lines2) == count1 + 1

        # Parse the new event
        ev = C.parse_line(lines2[-1])
        assert ev is not None
        assert ev["text"] == "A brand new message after state saved"


# --------------------------------------------------------------------------- #
# Full roundtrip: create DB → run() → read spool → parse_line → verify
# --------------------------------------------------------------------------- #
class TestFullRoundtrip:
    def test_roundtrip_user_and_assistant(self, sample_db, env, monkeypatch):
        monkeypatch.setenv("OPENCODE_TAILER_STATE", str(env["spool"] / "state.json"))

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
        assert spool_file.exists()
        lines = spool_file.read_text().strip().splitlines()
        assert len(lines) > 0

        for line in lines:
            assert line.startswith("v1\t")
            ev = C.parse_line(line)
            assert ev is not None
            assert ev["source"] == "opencode"
            assert ev["kind"] in ("prompt", "assistant-turn")
            assert ev["app"] == "opencode"

    def test_user_message_parsed_correctly(self, sample_db, env, monkeypatch):
        monkeypatch.setenv("OPENCODE_TAILER_STATE", str(env["spool"] / "state.json"))

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

        # Find the first user prompt event
        user_events = []
        for line in lines:
            ev = C.parse_line(line)
            if ev and ev["kind"] == "prompt":
                user_events.append(ev)

        assert len(user_events) >= 1
        first = user_events[0]
        assert first["text"] == "Hello, please help me."
        assert first["project"] == "my-repo"
        assert first["session"] == "ses_001"
        payload = json.loads(first["payload"])
        assert payload["role"] == "user"

    def test_assistant_turn_parsed_correctly(self, sample_db, env, monkeypatch):
        monkeypatch.setenv("OPENCODE_TAILER_STATE", str(env["spool"] / "state.json"))

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

        # Find the assistant turn event
        assistant_events = []
        for line in lines:
            ev = C.parse_line(line)
            if ev and ev["kind"] == "assistant-turn":
                assistant_events.append(ev)

        assert len(assistant_events) >= 1
        first = assistant_events[0]
        assert first["text"] == "Here is the solution."
        payload = json.loads(first["payload"])
        assert payload["role"] == "assistant"
        assert payload["tool_count"] == 2


# --------------------------------------------------------------------------- #
# Multiple sessions — messages correctly associated
# --------------------------------------------------------------------------- #
class TestMultipleSessions:
    def test_messages_across_sessions(self, sample_db, env, monkeypatch):
        monkeypatch.setenv("OPENCODE_TAILER_STATE", str(env["spool"] / "state.json"))

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

        sessions_seen = set()
        for line in lines:
            ev = C.parse_line(line)
            if ev:
                sessions_seen.add(ev["session"])

        assert "ses_001" in sessions_seen
        assert "ses_002" in sessions_seen


# --------------------------------------------------------------------------- #
# Tool calls in payload — verify structure
# --------------------------------------------------------------------------- #
class TestToolCallsInPayload:
    def test_assistant_tool_calls_structure(self, sample_db, env, monkeypatch):
        monkeypatch.setenv("OPENCODE_TAILER_STATE", str(env["spool"] / "state.json"))

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
            ev = C.parse_line(line)
            if ev and ev["kind"] == "assistant-turn":
                payload = json.loads(ev["payload"])
                assert "tool_calls" in payload
                assert "tool_count" in payload
                assert payload["tool_count"] == 2
                assert len(payload["tool_calls"]) == 2
                names = {tc["name"] for tc in payload["tool_calls"]}
                assert "bash" in names
                assert "edit" in names
                for tc in payload["tool_calls"]:
                    assert "name" in tc
                    assert "state" in tc
                break


# --------------------------------------------------------------------------- #
# Empty parts list — handled gracefully
# --------------------------------------------------------------------------- #
class TestEmptyParts:
    def test_user_with_no_parts_filtered(self):
        msg = {
            "id": "msg_x", "session_id": "ses_001",
            "role": "user", "time_created": 1700000001000,
            "agent": "build", "model": None, "cost": None,
            "tokens": {},
        }
        session = {"directory": "/home/zach/workspace/my-repo"}
        assert T.build_event(msg, [], session) is None

    def test_assistant_with_no_parts(self):
        msg = {
            "id": "msg_y", "session_id": "ses_001",
            "role": "assistant", "time_created": 1700000002000,
            "agent": "build", "model": None, "cost": 0.001,
            "tokens": {},
        }
        session = {"directory": "/home/zach/workspace/my-repo"}
        ev = T.build_event(msg, [], session)
        assert ev is not None
        assert ev["kind"] == "assistant-turn"
        assert ev["text"] == ""
        payload = json.loads(ev["payload"])
        assert payload["tool_count"] == 0
        assert payload["tool_calls"] == []
