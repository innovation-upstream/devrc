"""Tests for opencode/schema.py — schema detection.

Run: python -m pytest scripts/collector/opencode/tests/test_schema.py -v
"""
import sqlite3
import sys
from pathlib import Path

import pytest

COLLECTOR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(COLLECTOR))
OPENCODE = COLLECTOR / "opencode"
sys.path.insert(0, str(OPENCODE))

import _shared as S  # noqa: E402
from schema import SchemaInfo, TableInfo, detect_schema  # noqa: E402


def _make_db(tmp_path, tables: dict[str, list[str]] | None = None) -> Path:
    """Create a temporary DB with specified tables and columns."""
    db_path = tmp_path / "schema_test.db"
    conn = sqlite3.connect(db_path)
    if tables is None:
        tables = {
            "session": [
                "id", "project_id", "directory", "title", "version", "cost",
                "tokens_input", "tokens_output", "tokens_reasoning",
                "tokens_cache_read", "tokens_cache_write", "agent", "model",
                "time_created", "time_updated", "parent_id",
            ],
            "message": ["id", "session_id", "time_created", "time_updated", "data"],
            "part": ["id", "message_id", "session_id", "time_created", "time_updated", "data"],
            "project": ["id", "worktree", "name", "time_created", "time_updated"],
        }
    for table, cols in tables.items():
        col_defs = ", ".join(f"{c} TEXT" for c in cols)
        conn.execute(f"CREATE TABLE {table} ({col_defs})")
    conn.commit()
    conn.close()
    return db_path


class TestDetectSchema:
    def test_full_schema(self, tmp_path):
        db_path = _make_db(tmp_path)
        conn = S.get_db(db_path)
        try:
            info = detect_schema(conn)
            assert info is not None
            assert info.session is not None
            assert info.message is not None
            assert info.part is not None
            assert info.project is not None
            assert "id" in info.session.columns
            assert "project_id" in info.session.columns
            assert "time_created" in info.session.columns
        finally:
            conn.close()

    def test_missing_table_detected(self, tmp_path):
        tables = {
            "session": ["id", "project_id", "time_created"],
            "message": ["id", "session_id", "data"],
            # part table missing
        }
        db_path = _make_db(tmp_path, tables)
        conn = S.get_db(db_path)
        try:
            info = detect_schema(conn)
            assert info is not None
            assert info.session is not None
            assert info.message is not None
            assert info.part is None  # table doesn't exist
            assert any("missing table: part" in d for d in info.drift)
        finally:
            conn.close()

    def test_empty_db_no_tables(self, tmp_path):
        db_path = tmp_path / "empty.db"
        conn = sqlite3.connect(db_path)
        conn.close()
        conn2 = S.get_db(db_path)
        try:
            info = detect_schema(conn2)
            assert info is not None
            # All tables missing → None for each
            assert info.session is None
            assert info.message is None
            assert info.part is None
            assert info.project is None
            assert len(info.drift) == 4
        finally:
            conn2.close()

    def test_table_info_dataclass(self):
        ti = TableInfo(name="session", columns=["id", "name"])
        assert ti.name == "session"
        assert len(ti.columns) == 2

    def test_schema_info_dataclass(self):
        si = SchemaInfo(drift=["test drift"])
        assert si.session is None
        assert si.drift == ["test drift"]
