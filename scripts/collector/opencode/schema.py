#!/usr/bin/env python3
"""OpenCode-specific SQLite schema detection.

Queries PRAGMA table_info to discover the actual columns in the live DB,
maps them to our expected schema, and reports drift or missing tables.

Usage:
    from opencode.schema import detect_schema
    info = detect_schema()
    if info is None:
        print("No OpenCode DB found")
    elif info.drift:
        print(f"Schema drift: {info.drift}")
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

import _shared as S
from _shared import (
    MESSAGE_TABLE,
    PART_TABLE,
    PROJECT_TABLE,
    SESSION_TABLE,
    get_db,
    opencode_db_path,
)


@dataclass
class TableInfo:
    name: str
    columns: list[str]


@dataclass
class SchemaInfo:
    session: TableInfo | None = None
    message: TableInfo | None = None
    part: TableInfo | None = None
    project: TableInfo | None = None
    drift: list[str] = field(default_factory=list)
    db_path: Path | None = None


def _get_columns(db: sqlite3.Connection, table: str) -> list[str] | None:
    """Return column names for a table, or None if the table doesn't exist."""
    try:
        # Check if table exists first
        cur = db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        )
        if cur.fetchone() is None:
            return None
        cur = db.execute(f"PRAGMA table_info({table})")
        return [row["name"] for row in cur]
    except sqlite3.OperationalError:
        return None


def detect_schema(db: sqlite3.Connection | None = None) -> SchemaInfo | None:
    """Detect the actual OpenCode SQLite schema.

    Returns a SchemaInfo with per-table column lists and any drift warnings.
    Returns None if the DB doesn't exist or can't be opened.
    """
    own_db = False
    if db is None:
        db_path = opencode_db_path()
        if db_path is None:
            return None
        db = get_db(db_path)
        if db is None:
            return None
        own_db = True

    info = SchemaInfo(db_path=opencode_db_path())

    try:
        for attr, table_name in [
            ("session", SESSION_TABLE),
            ("message", MESSAGE_TABLE),
            ("part", PART_TABLE),
            ("project", PROJECT_TABLE),
        ]:
            cols = _get_columns(db, table_name)
            if cols is not None:
                setattr(info, attr, TableInfo(name=table_name, columns=cols))
            else:
                info.drift.append(f"missing table: {table_name}")
    finally:
        if own_db:
            db.close()

    return info
