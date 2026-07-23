#!/usr/bin/env python3
"""Sync the on-demand initiative-scan into the homelab `mailbox` Postgres.

PHASE 1 of the "initiatives consolidation" feature: turn the expensive, on-demand
`initiative-scan.py` report into a durable, live store that later apps (a viewer +
a router) can query cheaply. This script ONLY does the sync — no viewer, no router.

Pipeline (per run):
  1. Shell out to `scripts/session-analysis/initiative-scan.py --days N --json`
     (WITHOUT --tmux — the ephemeral tmux overlay is deliberately excluded) and parse
     stdout. We treat that report as the CONTRACT: we never re-derive its logic and we
     never import its internals. The scan degrades gracefully with no ClickHouse creds
     (telemetry_available=false) — so does this sync.
  2. Ensure the `initiatives` schema/tables/view exist (idempotent, self-migrating DDL).
     The tables live in their OWN schema inside the `mailbox` database so a future
     router can natively JOIN them against `mail_actions`.
  3. Insert one `initiatives.snapshots` row + one `initiatives.initiative_snapshot`
     row per initiative (append-only snapshots; `initiatives.current` is the live view).

The transform (report dict -> insert-row dicts) is a PURE function (`report_to_rows`),
separate from all I/O (the subprocess scan + the DB write), so it is unit-testable
without a live scan or a live DB — mirroring how initiative-scan.py separates pure
logic from I/O.

Requires (for a REAL write, not --dry-run):
    KUBECONFIG  — homelab kubeconfig (the DB is only reachable via kubectl port-forward)
    kubectl     — on PATH
    psycopg2    — python dep
On NixOS run under:
    nix-shell -p "python3.withPackages(p:[p.psycopg2 p.requests])" \
      --run "python scripts/initiatives/sync.py --dry-run"

CLICKHOUSE_* creds are read from the environment by the scan; unset -> telemetry-off
(still useful — handoff+git+session data — but momentum/ev degrade).
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import socket
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path

# The scan we shell out to (the data-source contract). Absolute so systemd/nix-shell
# invocations (any cwd) resolve it; the scan manages its OWN sys.path internally.
SCAN_PATH = Path(__file__).resolve().parents[1] / "session-analysis" / "initiative-scan.py"

# The shared mailbox-Postgres helper (kubectl port-forward + psycopg2 + DSN-from-secret).
MAILDB_PATH = Path(__file__).resolve().parents[1] / "mail-actions" / "_db.py"

# --------------------------------------------------------------------------- #
# Schema — self-migrating, additive-only DDL (idempotent on every run)
# --------------------------------------------------------------------------- #
# One namespaced schema in the `mailbox` DB so a future router can JOIN against
# `mail_actions`. `snapshots` = one row per sync run; `initiative_snapshot` = one
# row per initiative per run (append-only). `current` = DISTINCT ON latest per
# (repo, slug), which tolerates a partially-failed snapshot (older rows survive).
#
# The child FK is ON DELETE CASCADE so the 90-day retention prune (delete old
# `snapshots`) removes the child `initiative_snapshot` rows with them.
TABLES_DDL = """
CREATE SCHEMA IF NOT EXISTS initiatives;

CREATE TABLE IF NOT EXISTS initiatives.snapshots (
    id                   serial PRIMARY KEY,
    captured_at          timestamptz DEFAULT now(),
    host                 text,
    days_window          int,
    telemetry_available  boolean
);

CREATE TABLE IF NOT EXISTS initiatives.initiative_snapshot (
    id                   serial PRIMARY KEY,
    snapshot_id          int REFERENCES initiatives.snapshots(id) ON DELETE CASCADE,
    host                 text,
    repo                 text,
    slug                 text,
    title                text,
    summary              text,
    doc_date             date,
    momentum             text,
    last_touch           timestamptz,
    next_step            text,
    commits              int,
    commits_unknown      boolean,
    merged_prs           int,
    open_prs             jsonb,
    session_count        int,
    telem_events         int,
    telem_last           timestamptz,
    current_doc          text,
    open_investigations  jsonb,
    docs                 jsonb
);

-- Additive migration for pre-existing installs: CREATE TABLE IF NOT EXISTS won't add
-- a column to a table that already exists, so bring `summary` (nullable, deterministic
-- goal/what-this-is line) in explicitly. Idempotent — a no-op once the column exists.
ALTER TABLE initiatives.initiative_snapshot
    ADD COLUMN IF NOT EXISTS summary text;

-- Support the `current` view's DISTINCT ON (repo, slug) … JOIN snapshots …
-- ORDER BY repo, slug, captured_at DESC, plus the FK join / retention scans.
CREATE INDEX IF NOT EXISTS initiative_snapshot_repo_slug_snap_idx
    ON initiatives.initiative_snapshot (repo, slug, snapshot_id);
CREATE INDEX IF NOT EXISTS initiative_snapshot_snapshot_id_idx
    ON initiatives.initiative_snapshot (snapshot_id);
CREATE INDEX IF NOT EXISTS snapshots_captured_at_idx
    ON initiatives.snapshots (captured_at);
"""

# The views are guarded separately (see ensure_schema / _ensure_view): a recreate is
# DROP VIEW IF EXISTS + CREATE VIEW (NOT CREATE OR REPLACE — it can't reorder columns,
# which the v2 `summary` append does; see _ensure_view) and takes an ACCESS EXCLUSIVE
# lock, so we only (re)create a view when it's absent or its version marker differs.
# Bump the matching *_VERSION whenever its DDL changes so a deploy recreates it.
#
# `current` — newest row per (repo, slug) across ALL history. This deliberately
# includes initiatives that have aged out of the scan's N-day window (until the
# 90-day retention prunes them): the ROUTER wants to match a signal against
# recently-dormant work, so ghosts are a feature there.
# v2: the base table gained a `summary` column. A view's `SELECT i.*` is expanded to
# an explicit column list AT CREATE TIME (Postgres freezes it), so the new column does
# NOT appear until the view is recreated — bump the marker to force that on deploy.
VIEW_VERSION = "v2"
VIEW_COMMENT = f"initiatives-sync view {VIEW_VERSION}"
VIEW_DDL = """
CREATE VIEW initiatives.current AS
SELECT DISTINCT ON (i.repo, i.slug)
       i.*, s.captured_at
  FROM initiatives.initiative_snapshot i
  JOIN initiatives.snapshots s ON s.id = i.snapshot_id
 ORDER BY i.repo, i.slug, s.captured_at DESC;
"""

# `latest` — rows from the MOST RECENT snapshot only. This is the correct view for
# a live VIEWER: it shows exactly what the last scan saw, with NO aged-out ghosts
# (an initiative that dropped out of the scan's window simply isn't in the newest
# snapshot, so it disappears here even though `current` still carries it). Carries
# `captured_at` so the viewer can render an "updated Xm ago" freshness footer.
# v2: recreate to expose the new `summary` column (see VIEW_VERSION note above).
LATEST_VIEW_VERSION = "v2"
LATEST_VIEW_COMMENT = f"initiatives-sync view latest {LATEST_VIEW_VERSION}"
LATEST_VIEW_DDL = """
CREATE VIEW initiatives.latest AS
SELECT i.*, s.captured_at
  FROM initiatives.initiative_snapshot i
  JOIN initiatives.snapshots s ON s.id = i.snapshot_id
 WHERE i.snapshot_id = (SELECT max(id) FROM initiatives.snapshots);
"""

# Arbitrary constant key for the schema advisory lock (pg_advisory_xact_lock), so a
# manual run can't race the timer on the not-fully-race-safe CREATE … IF NOT EXISTS.
SCHEMA_LOCK_KEY = 0x1417_1717

# Append-only retention: prune snapshots (and, via ON DELETE CASCADE, their child
# rows) older than this on every write, so the shared prod DB doesn't grow unbounded.
RETENTION_DAYS = 90

# Column order for the per-initiative insert (snapshot_id is prepended at write time).
ROW_COLUMNS = [
    "host", "repo", "slug", "title", "summary", "doc_date", "momentum", "last_touch",
    "next_step", "commits", "commits_unknown", "merged_prs", "open_prs",
    "session_count", "telem_events", "telem_last", "current_doc",
    "open_investigations", "docs",
]
# Columns stored as JSONB (wrapped in psycopg2.extras.Json at write time).
JSONB_COLUMNS = {"open_prs", "open_investigations", "docs"}


# --------------------------------------------------------------------------- #
# Pure transform (report dict -> insert rows). No I/O — unit-tested directly.
# --------------------------------------------------------------------------- #
def _epoch_to_dt(v) -> datetime | None:
    """UNIX epoch seconds (float|int|str) -> UTC-aware datetime; None -> None.

    `--json` runs with default=str, so an epoch usually arrives as a JSON number,
    but we accept a stringified number too, and return None for null / unparseable.
    Storing UTC keeps the column unambiguous (the scan's epochs are wall-clock UTC)."""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return datetime.fromtimestamp(f, tz=timezone.utc)


def _to_date(v) -> date | None:
    """A handoff's authored date string (e.g. '2026-07-13') -> date; None/blank -> None.

    Tolerant: takes the leading YYYY-MM-DD and returns None on anything unparseable so
    a malformed `date` can never crash the sync."""
    if not v:
        return None
    try:
        return date.fromisoformat(str(v).strip()[:10])
    except ValueError:
        return None


def _to_int(v, default: int = 0) -> int:
    if v is None:
        return default
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _initiative_to_row(ini: dict, host: str) -> dict:
    """One scan `initiative` dict -> one `initiative_snapshot` insert-row dict.

    Epoch floats -> UTC timestamptz; the authored `date` -> a real date; the list/dict
    fields stay Python objects (wrapped as JSONB only at DB-write time, so the pure
    transform is inspectable / assertable without psycopg2)."""
    return {
        "host": host,
        "repo": ini.get("repo"),
        "slug": ini.get("slug"),
        "title": ini.get("title"),
        "summary": ini.get("summary"),
        "doc_date": _to_date(ini.get("date")),
        "momentum": ini.get("momentum"),
        "last_touch": _epoch_to_dt(ini.get("last_touch")),
        "next_step": ini.get("next_step"),
        "commits": _to_int(ini.get("commits")),
        "commits_unknown": bool(ini.get("commits_unknown")),
        "merged_prs": _to_int(ini.get("merged_prs")),
        "open_prs": ini.get("open_prs") or [],
        "session_count": _to_int(ini.get("session_count")),
        "telem_events": _to_int(ini.get("telem_events")),
        "telem_last": _epoch_to_dt(ini.get("telem_last")),
        "current_doc": ini.get("current_doc"),
        "open_investigations": ini.get("open_investigations") or [],
        "docs": ini.get("docs") or [],
    }


def report_to_rows(report: dict, host: str) -> tuple[dict, list[dict]]:
    """PURE: a scan `--json` report dict -> (snapshot-meta, [initiative-row, ...]).

    Flattens `by_repo` (every repo's initiatives) into one host-tagged row list. The
    ephemeral tmux overlay and the report-level catchall are intentionally NOT stored
    (Phase 1 is the durable per-initiative payload only). An empty / telemetry-off
    report yields an empty row list and telemetry_available=False — never raises."""
    meta = {
        "host": host,
        "days_window": report.get("days"),
        "telemetry_available": bool(report.get("telemetry_available")),
    }
    rows: list[dict] = []
    for _repo, inis in (report.get("by_repo") or {}).items():
        for ini in inis or []:
            rows.append(_initiative_to_row(ini, host))
    return meta, rows


def resolve_host() -> str:
    """Host tag for the rows. ACTIVITY_HOST wins (BOTH devrc hosts are hostname
    `nixos`, so the raw hostname can't disambiguate); else a meaningful gethostname();
    else default to 'workbench' (Phase 1 runs workbench-only)."""
    env = os.environ.get("ACTIVITY_HOST", "").strip()
    if env:
        return env
    hn = socket.gethostname().strip()
    if hn and hn != "nixos":
        return hn
    return "workbench"


# --------------------------------------------------------------------------- #
# I/O — the scan subprocess, the DB import, schema DDL, and the write
# --------------------------------------------------------------------------- #
# Ceiling on the (expensive) scan so a manual invocation with a hung gh/kubectl can't
# hang forever — systemd's TimeoutStartSec only covers the timer path.
SCAN_TIMEOUT_SEC = 240


def run_scan(days: int, scan_path: Path = SCAN_PATH, env: dict | None = None,
             timeout: int = SCAN_TIMEOUT_SEC) -> dict:
    """Shell out to initiative-scan.py --days N --json (NO --tmux) and parse stdout.

    Runs with the SAME interpreter (sys.executable) so the nix-shell python that has
    `requests` is used for the scan's ClickHouse read. Inherits the environment
    (CLICKHOUSE_* etc.); the scan degrades to telemetry-off if they're unset. Bounded
    by `timeout` seconds so a hung gh/kubectl child can't wedge a manual run."""
    cmd = [sys.executable, str(scan_path), "--days", str(days), "--json"]
    out = subprocess.check_output(cmd, text=True, env=env or os.environ.copy(),
                                  timeout=timeout)
    return json.loads(out)


def _import_maildb():
    """Load MailDB from scripts/mail-actions/_db.py by EXPLICIT importlib path.

    Do NOT put mail-actions/ on sys.path — its `llm.py` shadows other modules and
    breaks callers (documented in the repo CLAUDE.md; repo-cos/feedback.py hits the
    same trap). `_db.py` imports only stdlib+psycopg2, so a standalone load is safe."""
    spec = importlib.util.spec_from_file_location("initiatives_maildb", MAILDB_PATH)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {MAILDB_PATH}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.MailDB


def _ensure_view(cur, view: str, ddl: str, comment: str) -> None:
    """(Re)create a guarded view ONLY when it's missing or its version marker differs.

    Uses DROP VIEW IF EXISTS + CREATE VIEW, NOT CREATE OR REPLACE VIEW: `CREATE OR
    REPLACE` cannot reorder or rename an existing view's output columns, and the v2
    schema appends `summary` to `initiative_snapshot`, so `SELECT i.*, s.captured_at`
    now places `summary` before `captured_at` — Postgres would reject the replace with
    `cannot change name of view column "captured_at" to "summary"` and, since the whole
    ensure_schema runs in one advisory-locked transaction, roll the ENTIRE sync back
    (freezing an existing v1 store). Dropping first sidesteps the column-shape lock.
    Safe: nothing depends on `latest`/`current` and they don't depend on each other.

    Still gated on a version-marker `COMMENT ON VIEW` so the DROP+CREATE (ACCESS
    EXCLUSIVE) only runs when the marker actually changes — steady state (view present,
    marker matches) does nothing. `view` is a trusted module constant (never user input)
    so interpolating it into the introspection/DROP SQL is safe. Bump the view's
    *_VERSION to force a recreate after a hand-edit to its DDL."""
    cur.execute(f"SELECT to_regclass('{view}')")
    exists = cur.fetchone()[0] is not None
    need_view = True
    if exists:
        cur.execute(f"SELECT obj_description('{view}'::regclass, 'pg_class')")
        row = cur.fetchone()
        need_view = (row[0] if row else None) != comment
    if need_view:
        cur.execute(f"DROP VIEW IF EXISTS {view}")
        cur.execute(ddl)
        cur.execute(f"COMMENT ON VIEW {view} IS %s", (comment,))


def ensure_schema(conn) -> None:
    """Create the schema/tables/indexes/views idempotently (self-migrating, additive-only).

    Wrapped in a transaction-scoped advisory lock so a manual run racing the timer
    can't collide on the not-fully-race-safe CREATE … IF NOT EXISTS. Each view
    (`current` for the router, `latest` for the viewer) is only (re)created when
    absent or its version marker differs — steady state skips re-taking ACCESS
    EXCLUSIVE on either every run."""
    with conn.cursor() as cur:
        # Serialize concurrent schema setup (released at COMMIT below).
        cur.execute("SELECT pg_advisory_xact_lock(%s)", (SCHEMA_LOCK_KEY,))
        cur.execute(TABLES_DDL)
        _ensure_view(cur, "initiatives.current", VIEW_DDL, VIEW_COMMENT)
        _ensure_view(cur, "initiatives.latest", LATEST_VIEW_DDL, LATEST_VIEW_COMMENT)
    conn.commit()


def prune_old_snapshots(conn, retain_days: int = RETENTION_DAYS) -> int:
    """Delete snapshots older than `retain_days` (child rows cascade). Returns the
    number of snapshots removed. Keeps the append-only store bounded on shared prod."""
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM initiatives.snapshots "
            "WHERE captured_at < now() - make_interval(days => %s)",
            (retain_days,),
        )
        n = cur.rowcount
    conn.commit()
    return n


def write_snapshot(conn, meta: dict, rows: list[dict]) -> int:
    """Insert one snapshots row + one initiative_snapshot row per initiative.

    JSONB columns are wrapped in psycopg2.extras.Json; datetimes/dates adapt natively.
    Returns the new snapshot id. Commits once at the end (all-or-nothing per run)."""
    from psycopg2.extras import Json  # local import so the pure path needs no psycopg2

    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO initiatives.snapshots (host, days_window, telemetry_available) "
            "VALUES (%s, %s, %s) RETURNING id",
            (meta["host"], meta["days_window"], meta["telemetry_available"]),
        )
        snapshot_id = cur.fetchone()[0]

        cols = ["snapshot_id"] + ROW_COLUMNS
        placeholders = ", ".join(["%s"] * len(cols))
        sql = (
            f"INSERT INTO initiatives.initiative_snapshot ({', '.join(cols)}) "
            f"VALUES ({placeholders})"
        )
        for r in rows:
            vals = [snapshot_id]
            for c in ROW_COLUMNS:
                v = r[c]
                vals.append(Json(v) if c in JSONB_COLUMNS else v)
            cur.execute(sql, vals)
    conn.commit()
    return snapshot_id


# --------------------------------------------------------------------------- #
# Dry-run rendering
# --------------------------------------------------------------------------- #
def _short_repo(repo: str | None) -> str:
    return os.path.basename(str(repo).rstrip("/")) if repo else "?"


def _jsonable(row: dict) -> dict:
    """A row with datetimes/dates isoformatted, for readable dry-run JSON."""
    out = {}
    for k, v in row.items():
        if isinstance(v, (datetime, date)):
            out[k] = v.isoformat()
        else:
            out[k] = v
    return out


def _iso_short(dt: datetime | None) -> str:
    return dt.strftime("%Y-%m-%d %H:%M") if isinstance(dt, datetime) else "-"


def render_dry_run(meta: dict, rows: list[dict]) -> str:
    """Human-readable, COMPLETE preview of exactly what a real run would insert:
    the snapshot meta, a scannable table, the row count, and the full per-row JSON."""
    out: list[str] = []
    out.append("=== initiatives sync DRY-RUN (no DB write) ===")
    out.append(
        f"snapshot: host={meta['host']}  days_window={meta['days_window']}  "
        f"telemetry_available={meta['telemetry_available']}  "
        f"captured_at≈{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')}Z"
    )
    out.append("target:   initiatives.snapshots + initiatives.initiative_snapshot "
               "(mailbox DB)")
    if not meta["telemetry_available"]:
        out.append("NOTE: telemetry OFF (CLICKHOUSE_* unset/unreachable) — momentum/ev "
                   "degrade to handoff+git+session signal only.")
    out.append(f"rows to insert: {len(rows)}")
    out.append("")

    # Scannable summary table.
    hdr = (f"{'#':>2}  {'repo':<16} {'slug':<34} {'momentum':<8} "
           f"{'last_touch':<16} {'cmt':>4} {'mPR':>3} {'oPR':>3} {'sess':>4} {'ev':>5}")
    out.append(hdr)
    out.append("-" * len(hdr))
    for i, r in enumerate(rows, 1):
        out.append(
            f"{i:>2}  {_short_repo(r['repo']):<16.16} {str(r['slug'] or ''):<34.34} "
            f"{str(r['momentum'] or ''):<8.8} {_iso_short(r['last_touch']):<16} "
            f"{r['commits']:>4} {r['merged_prs']:>3} {len(r['open_prs'] or []):>3} "
            f"{r['session_count']:>4} {r['telem_events']:>5}"
        )
    out.append("")

    # Full, complete rows (nothing hidden) so the shape can be eyeballed.
    out.append("--- full rows (exactly what would be inserted, one per initiative) ---")
    for i, r in enumerate(rows, 1):
        out.append(f"[{i}] {_short_repo(r['repo'])} / {r['slug']}")
        out.append(json.dumps(_jsonable(r), indent=2, ensure_ascii=False))
    return "\n".join(out)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Sync initiative-scan --json into the mailbox Postgres "
                    "(initiatives schema).")
    p.add_argument("--days", type=int, default=4,
                   help="trailing window passed to initiative-scan (default 4, "
                        "matching the ledger default)")
    p.add_argument("--dry-run", action="store_true",
                   help="do everything EXCEPT the DB write; print the rows that "
                        "WOULD be inserted (table + count + full JSON)")
    return p.parse_args(argv)


def main(argv=None) -> int:
    a = parse_args(argv)
    if a.days <= 0:
        print("error: --days must be positive", file=sys.stderr)
        return 2

    host = resolve_host()
    try:
        report = run_scan(a.days)
    except subprocess.TimeoutExpired:
        print(f"error: initiative-scan timed out after {SCAN_TIMEOUT_SEC}s",
              file=sys.stderr)
        return 1
    except subprocess.CalledProcessError as exc:
        print(f"error: initiative-scan failed (exit {exc.returncode})", file=sys.stderr)
        return 1
    except json.JSONDecodeError as exc:
        print(f"error: could not parse initiative-scan --json output: {exc}",
              file=sys.stderr)
        return 1

    meta, rows = report_to_rows(report, host)

    if a.dry_run:
        print(render_dry_run(meta, rows))
        return 0

    MailDB = _import_maildb()
    with MailDB() as db:
        ensure_schema(db.conn)
        snapshot_id = write_snapshot(db.conn, meta, rows)
        pruned = prune_old_snapshots(db.conn)

    tel = "on" if meta["telemetry_available"] else "OFF (handoff+git only)"
    pruned_note = f", pruned {pruned} snapshot(s) >{RETENTION_DAYS}d" if pruned else ""
    print(f"initiatives-sync: wrote snapshot #{snapshot_id} — {len(rows)} initiative "
          f"rows (host={host}, days={a.days}, telemetry {tel}){pruned_note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
