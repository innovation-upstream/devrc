"""Tests for opencode/__main__.py — CLI entry point.

Run: python -m pytest scripts/collector/opencode/tests/test_cli.py -v
"""
from __future__ import annotations

import importlib.util
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
import tailer as T  # noqa: E402
import session_tailer as ST  # noqa: E402

# Import __main__ via importlib to avoid pytest's own __main__ module conflict
_spec = importlib.util.spec_from_file_location("opencode_main", str(OPENCODE / "__main__.py"))
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
M = _mod


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

    # User message for ses_001
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
    # Assistant message for ses_001
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

    # User message for ses_002
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
# test_main_runs_all
# --------------------------------------------------------------------------- #
class TestMainRunsAll:
    def test_both_tailers_execute(self, sample_db, env, monkeypatch):
        monkeypatch.setattr(S, "get_db", _mock_get_db(sample_db))
        rc = M.run(["--mode", "all", "--db", str(sample_db)])
        assert rc == 0
        spool_file = env["spool"] / "current.log"
        lines = spool_file.read_text().strip().splitlines()
        # Should have session summaries + message events
        assert len(lines) > 0


# --------------------------------------------------------------------------- #
# test_main_runs_tailer_only
# --------------------------------------------------------------------------- #
class TestMainRunsTailerOnly:
    def test_only_message_tailer(self, sample_db, env, monkeypatch):
        monkeypatch.setattr(S, "get_db", _mock_get_db(sample_db))
        rc = M.run(["--mode", "tailer", "--db", str(sample_db)])
        assert rc == 0
        spool_file = env["spool"] / "current.log"
        lines = spool_file.read_text().strip().splitlines()
        assert len(lines) > 0


# --------------------------------------------------------------------------- #
# test_main_runs_session_only
# --------------------------------------------------------------------------- #
class TestMainRunsSessionOnly:
    def test_only_session_tailer(self, sample_db, env, monkeypatch):
        monkeypatch.setattr(S, "get_db", _mock_get_db(sample_db))
        rc = M.run(["--mode", "session", "--db", str(sample_db)])
        assert rc == 0
        spool_file = env["spool"] / "current.log"
        lines = spool_file.read_text().strip().splitlines()
        assert len(lines) >= 2  # at least 2 sessions


# --------------------------------------------------------------------------- #
# test_backfill_clears_state
# --------------------------------------------------------------------------- #
class TestBackfillClearsState:
    def test_state_files_cleared_before_run(self, sample_db, env, monkeypatch):
        monkeypatch.setattr(S, "get_db", _mock_get_db(sample_db))
        # Write some state first
        T.save_state(Path(env["tmp_path"] / "tailer-state.json"), {"seen": ["a"]})
        ST.save_state(Path(env["tmp_path"] / "session-state.json"), {"ses_old": "sig"})

        assert (env["tmp_path"] / "tailer-state.json").exists()
        assert (env["tmp_path"] / "session-state.json").exists()

        rc = M.run(["--backfill", "--db", str(sample_db)])
        assert rc == 0

        # State files re-created by tailers after emission
        assert (env["tmp_path"] / "tailer-state.json").exists()
        assert (env["tmp_path"] / "session-state.json").exists()

        # Verify old state was cleared — new state should contain actual session IDs
        tailer_state = T.load_state(Path(env["tmp_path"] / "tailer-state.json"))
        assert "a" not in tailer_state  # old garbage cleared


# --------------------------------------------------------------------------- #
# test_dry_run_prints
# --------------------------------------------------------------------------- #
class TestDryRunPrints:
    def test_dry_run_outputs_json(self, sample_db, env, monkeypatch, capsys):
        monkeypatch.setattr(S, "get_db", _mock_get_db(sample_db))
        rc = M.run(["--dry-run", "--db", str(sample_db)])
        assert rc == 0
        captured = capsys.readouterr()
        lines = [l for l in captured.out.strip().splitlines() if l and not l.startswith("dry-run:")]
        assert len(lines) > 0
        for line in lines:
            ev = json.loads(line)
            assert "source" in ev
            assert "kind" in ev

    def test_dry_run_with_mode(self, sample_db, env, monkeypatch, capsys):
        monkeypatch.setattr(S, "get_db", _mock_get_db(sample_db))
        rc = M.run(["--dry-run", "--mode", "tailer", "--db", str(sample_db)])
        assert rc == 0
        captured = capsys.readouterr()
        lines = [l for l in captured.out.strip().splitlines() if l and not l.startswith("dry-run:")]
        assert len(lines) > 0


# --------------------------------------------------------------------------- #
# test_custom_db_path
# --------------------------------------------------------------------------- #
class TestCustomDbPath:
    def test_db_flag_passed_to_tailer(self, sample_db, env, monkeypatch):
        monkeypatch.setattr(S, "get_db", _mock_get_db(sample_db))
        rc = M.run(["--db", str(sample_db)])
        assert rc == 0

    def test_nonexistent_db(self, tmp_path, env, monkeypatch, capsys):
        # Non-existent DB should not crash, just print message
        rc = M.run(["--db", str(tmp_path / "nonexistent.db"), "--dry-run"])
        assert rc == 0
        captured = capsys.readouterr()
        assert "no OpenCode DB found" in captured.out
