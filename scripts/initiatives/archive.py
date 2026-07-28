#!/usr/bin/env python3
"""Standalone archived-lifecycle store for the initiatives board (Phase 2).

The board's manual "done / drop" cleanup: an owner taps `[✓ done]` (or `[drop]`) on a
card and it is recorded here, in a STANDALONE `initiatives.archived` table, so the card is
suppressed from the board AND the choice PERSISTS across the 15-min sync (it will not
re-derive). Recoverable via the board's "Done" view (`[↺ unarchive]`).

Why standalone (like `initiatives.recaps` / `initiatives.assistant_log`): `archived` is NOT
part of the `latest`/`current` views and touches NEITHER `initiative_snapshot` NOR the views,
so it needs NO VIEW_VERSION bump — just an idempotent `CREATE TABLE IF NOT EXISTS`. The board
joins it in at read time (`read_archived`), exactly as the viewer joins the recaps cache.

Everything is BEST-EFFORT and NEVER RAISES on the write/read path (mirroring
`scripts/repo-cos/clawgate.py` + the viewer's `/api/dispatch`): a store outage degrades to a
failure RESULT (`{"ok": False, "error": ...}`) or an empty list, never an exception the
board has to catch. The writes go through the shared mailbox-Postgres helper (`_db.py`,
loaded by EXPLICIT importlib path — never `sys.path`, whose `mail-actions/llm.py` would
shadow siblings; documented in the repo CLAUDE.md).
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

# The shared mailbox-Postgres helper (`MailDB`), loaded by EXPLICIT importlib path — the same
# module + full-creds path the viewer uses to write `initiatives.assistant_log`.
MAILDB_PATH = Path(__file__).resolve().parents[1] / "mail-actions" / "_db.py"

# STANDALONE table (not in any view → NO view-version bump). `CREATE SCHEMA IF NOT EXISTS`
# so a first write self-heals even if the sync has never run (mirrors the assistant log DDL).
ARCHIVED_DDL = """
CREATE SCHEMA IF NOT EXISTS initiatives;

CREATE TABLE IF NOT EXISTS initiatives.archived (
    repo        text NOT NULL,
    slug        text NOT NULL,
    title       text,
    reason      text,
    archived_at timestamptz DEFAULT now(),
    PRIMARY KEY (repo, slug)
);
"""

# The columns the board reads back for the "Done" view (title lets an aged-out card — one no
# longer in `latest` — still render there).
_ARCHIVED_COLUMNS = ["repo", "slug", "title", "reason", "archived_at"]

_maildb_mod = None


def _maildb():
    """Load `MailDB` from mail-actions/_db.py by EXPLICIT importlib path (NOT via sys.path —
    its sibling `llm.py` shadows other modules; documented in the repo CLAUDE.md). Cached.
    `_db.py` imports only stdlib + psycopg2, so a standalone load is side-effect-free."""
    global _maildb_mod
    if _maildb_mod is None:
        spec = importlib.util.spec_from_file_location("initiatives_archive_maildb", MAILDB_PATH)
        if spec is None or spec.loader is None:
            raise ImportError(f"cannot load {MAILDB_PATH}")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _maildb_mod = mod.MailDB
    return _maildb_mod


def _factory(db_factory):
    """Resolve the DB context-manager factory: the injected one (tests) or the live MailDB
    class (called with no args → a context manager yielding `.conn`). Kept injectable so the
    archive/unarchive/list functions are unit-testable with a fake DB — no live Postgres."""
    return db_factory if db_factory is not None else _maildb()


def create_archived_table(cur) -> None:
    """Idempotently create the standalone `initiatives.archived` table (+ its schema) on an
    OPEN cursor. Callable both from a write's self-heal AND (harmlessly) elsewhere — a no-op
    once the table exists. STANDALONE table → NO view-version bump (mirrors
    `recap.create_recaps_table` / `assistant.create_assistant_log_table`)."""
    cur.execute(ARCHIVED_DDL)


def archive(repo: str, slug: str, title: str = "", reason: str = "done",
            *, db_factory=None) -> dict:
    """UPSERT one (repo, slug) archived row. Re-archiving REFRESHES `archived_at`/`reason`/
    `title` (ON CONFLICT DO UPDATE) so the resurface clock restarts. Self-heals the table
    first (works before the first sync). BEST-EFFORT: any DB failure returns
    `{"ok": False, "error": ...}` — it NEVER raises (the board's write endpoint 502s on a
    falsey `ok`, never 500s)."""
    if not repo or not slug:
        return {"ok": False, "error": "missing repo/slug"}
    try:
        factory = _factory(db_factory)
        with factory() as db:
            conn = db.conn
            with conn.cursor() as cur:
                create_archived_table(cur)
                cur.execute(
                    """
                    INSERT INTO initiatives.archived (repo, slug, title, reason, archived_at)
                    VALUES (%s, %s, %s, %s, now())
                    ON CONFLICT (repo, slug) DO UPDATE
                       SET archived_at = now(),
                           reason      = EXCLUDED.reason,
                           title       = EXCLUDED.title
                    """,
                    (repo, slug, title or "", reason or "done"),
                )
            conn.commit()
        return {"ok": True}
    except Exception as exc:  # noqa: BLE001 - archive is best-effort; never raise
        sys.stderr.write(f"archive: archive({slug!r}) failed: {type(exc).__name__}: {exc}\n")
        return {"ok": False, "error": type(exc).__name__}


def unarchive(repo: str, slug: str, *, db_factory=None) -> dict:
    """DELETE one (repo, slug) archived row (the board's `[↺ unarchive]`). Self-heals the
    table first so a delete before any insert is a harmless no-op. BEST-EFFORT: returns
    `{"ok": False, "error": ...}` on any DB failure, NEVER raises."""
    if not repo or not slug:
        return {"ok": False, "error": "missing repo/slug"}
    try:
        factory = _factory(db_factory)
        with factory() as db:
            conn = db.conn
            with conn.cursor() as cur:
                create_archived_table(cur)
                cur.execute(
                    "DELETE FROM initiatives.archived WHERE repo = %s AND slug = %s",
                    (repo, slug),
                )
            conn.commit()
        return {"ok": True}
    except Exception as exc:  # noqa: BLE001 - unarchive is best-effort; never raise
        sys.stderr.write(f"archive: unarchive({slug!r}) failed: {type(exc).__name__}: {exc}\n")
        return {"ok": False, "error": type(exc).__name__}


def read_archived(conn) -> list[dict]:
    """Read the archived rows on an ALREADY-OPEN connection → list of dicts (the shape the
    board joins in at read time, exactly like `attach_recaps` over the recaps cache). Guarded
    with `to_regclass` + a rollback on error so a missing table / read hiccup degrades to `[]`
    and keeps the connection usable — the board then suppresses nothing and still renders.
    Does NOT create the table (read path stays side-effect-free; the WRITE path self-heals)."""
    import psycopg2
    import psycopg2.extras

    try:
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass('initiatives.archived')")
            reg = cur.fetchone()
            if reg is None or reg[0] is None:
                return []
        cols = ", ".join(_ARCHIVED_COLUMNS)
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(f"SELECT {cols} FROM initiatives.archived")
            return [dict(r) for r in cur.fetchall()]
    except psycopg2.Error:
        try:
            conn.rollback()
        except Exception:  # noqa: BLE001 - a failed rollback must not raise from a read
            pass
        return []


def list_archived(*, db_factory=None) -> list[dict]:
    """The full archived set (opens its own mailbox-Postgres connection; wraps `read_archived`).
    BEST-EFFORT → `[]` on any failure. Used by callers that don't already hold a connection;
    the board itself reads via `read_archived` on the same connection it loads `latest` with,
    so it opens no second port-forward."""
    try:
        factory = _factory(db_factory)
        with factory() as db:
            return read_archived(db.conn)
    except Exception as exc:  # noqa: BLE001 - list is best-effort; never raise
        sys.stderr.write(f"archive: list_archived failed: {type(exc).__name__}: {exc}\n")
        return []
