"""SQLite store: aliases, labelled examples, route log, host prior.

Route provenance lives HERE and nowhere else — deliberately no `.dlmeta.json`
sidecar files next to the media, which would pollute the directories and risk
confusing qBittorrent and media scanners.

Concurrency: the sidecar is threaded (one thread per HTTP request) and the CLI
runs out-of-process against the same file, so WAL + a busy timeout + one
connection per thread is the required shape. Schema changes go through
`MIGRATIONS`; `user_version` is the version marker.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

SCHEMA_VERSION = 2

MIGRATIONS = {
    1: [
        """CREATE TABLE IF NOT EXISTS aliases (
              key        TEXT NOT NULL,
              site       TEXT NOT NULL DEFAULT '',
              dir        TEXT NOT NULL,
              hits       INTEGER NOT NULL DEFAULT 1,
              created_at REAL NOT NULL,
              updated_at REAL NOT NULL,
              PRIMARY KEY (key, site)
           )""",
        "CREATE INDEX IF NOT EXISTS aliases_dir ON aliases(dir)",
        """CREATE TABLE IF NOT EXISTS examples (
              id          INTEGER PRIMARY KEY AUTOINCREMENT,
              ts          REAL NOT NULL,
              context     TEXT NOT NULL,
              chosen_dir  TEXT NOT NULL,
              auto_dir    TEXT,
              created_new INTEGER NOT NULL DEFAULT 0
           )""",
        """CREATE TABLE IF NOT EXISTS routes (
              id         INTEGER PRIMARY KEY AUTOINCREMENT,
              ts         REAL NOT NULL,
              url        TEXT,
              site       TEXT,
              filename   TEXT,
              dir        TEXT NOT NULL,
              confidence REAL NOT NULL DEFAULT 0,
              reason     TEXT,
              auto       INTEGER NOT NULL DEFAULT 0,
              dup        TEXT
           )""",
        "CREATE INDEX IF NOT EXISTS routes_ts ON routes(ts DESC)",
        """CREATE TABLE IF NOT EXISTS host_prior (
              site TEXT PRIMARY KEY,
              dir  TEXT NOT NULL,
              ts   REAL NOT NULL
           )""",
    ],
    # v2 -- the provenance the /relocate guard needs.
    #
    # /relocate is the one endpoint that moves a PRE-EXISTING file, and the
    # library root is a live qBittorrent seeding target. Without provenance it
    # would happily rename a torrent payload (breaking seeding) on nothing more
    # than a relative path supplied by the extension. `routes.download_id` ties
    # a routing decision to the browser's DownloadItem, and `routed_files` is
    # the ledger of paths this router is allowed to move.
    2: [
        "ALTER TABLE routes ADD COLUMN download_id TEXT NOT NULL DEFAULT ''",
        "CREATE INDEX IF NOT EXISTS routes_download ON routes(download_id)",
        """CREATE TABLE IF NOT EXISTS routed_files (
              rel_path    TEXT PRIMARY KEY,
              download_id TEXT NOT NULL DEFAULT '',
              dir         TEXT NOT NULL,
              ts          REAL NOT NULL
           )""",
    ],
}


class Store:
    """Thread-safe SQLite wrapper (one connection per thread)."""

    def __init__(self, path, *, clock=time.time, timeout: float = 10.0):
        self.path = Path(path)
        self._clock = clock
        self._timeout = timeout
        self._local = threading.local()
        if str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._shared = None
        if str(self.path) == ":memory:":
            # An in-memory DB is per-connection, so tests get ONE shared handle.
            self._shared = self._connect()
        self.migrate()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path), timeout=self._timeout,
                               check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=%d" % int(self._timeout * 1000))
        if str(self.path) != ":memory:":
            conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    @property
    def conn(self) -> sqlite3.Connection:
        if self._shared is not None:
            return self._shared
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = self._connect()
            self._local.conn = conn
        return conn

    def close(self) -> None:
        conn = self._shared or getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
        self._shared = None
        self._local.conn = None

    # --- schema ------------------------------------------------------------ #
    def migrate(self) -> int:
        conn = self.conn
        cur = conn.execute("PRAGMA user_version")
        version = int(cur.fetchone()[0])
        for target in range(version + 1, SCHEMA_VERSION + 1):
            for stmt in MIGRATIONS[target]:
                conn.execute(stmt)
            conn.execute(f"PRAGMA user_version={target}")
            conn.commit()
        return SCHEMA_VERSION

    def version(self) -> int:
        return int(self.conn.execute("PRAGMA user_version").fetchone()[0])

    # --- aliases ----------------------------------------------------------- #
    def upsert_alias(self, key: str, dir_name: str, site: str = "") -> None:
        """Insert or re-point an alias, bumping its hit count.

        A correction re-points an existing alias rather than accumulating a
        second row: the user's latest choice is authoritative.
        """
        now = self._clock()
        self.conn.execute(
            """INSERT INTO aliases (key, site, dir, hits, created_at, updated_at)
               VALUES (?, ?, ?, 1, ?, ?)
               ON CONFLICT(key, site) DO UPDATE SET
                 dir = excluded.dir,
                 hits = aliases.hits + 1,
                 updated_at = excluded.updated_at""",
            (key, site or "", dir_name, now, now))
        self.conn.commit()

    def alias(self, key: str, site: str = ""):
        row = self.conn.execute(
            "SELECT dir FROM aliases WHERE key=? AND site=?",
            (key, site or "")).fetchone()
        return row["dir"] if row else None

    def alias_map(self) -> dict:
        """`{(key, site): dir}` — the shape `Matcher` expects."""
        rows = self.conn.execute("SELECT key, site, dir FROM aliases").fetchall()
        return {(r["key"], r["site"]): r["dir"] for r in rows}

    def alias_count(self) -> int:
        return int(self.conn.execute(
            "SELECT COUNT(*) AS n FROM aliases").fetchone()["n"])

    def delete_alias(self, key: str, site: str = "") -> None:
        self.conn.execute("DELETE FROM aliases WHERE key=? AND site=?",
                          (key, site or ""))
        self.conn.commit()

    # --- labelled examples ------------------------------------------------- #
    def add_example(self, context: dict, chosen_dir: str, auto_dir=None,
                    created_new: bool = False) -> int:
        cur = self.conn.execute(
            """INSERT INTO examples (ts, context, chosen_dir, auto_dir, created_new)
               VALUES (?, ?, ?, ?, ?)""",
            (self._clock(), json.dumps(context, ensure_ascii=False,
                                       separators=(",", ":")),
             chosen_dir, auto_dir, 1 if created_new else 0))
        self.conn.commit()
        return int(cur.lastrowid)

    def examples(self, limit: int = 100) -> list:
        rows = self.conn.execute(
            "SELECT * FROM examples ORDER BY id DESC LIMIT ?",
            (int(limit),)).fetchall()
        return [dict(r) for r in rows]

    # --- route log --------------------------------------------------------- #
    def log_route(self, *, url="", site="", filename="", dir_name="",
                  confidence=0.0, reason="", auto=False, dup=None,
                  download_id="") -> int:
        cur = self.conn.execute(
            """INSERT INTO routes (ts, url, site, filename, dir, confidence,
                                   reason, auto, dup, download_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (self._clock(), url, site, filename, dir_name, float(confidence),
             reason, 1 if auto else 0,
             json.dumps(dup, separators=(",", ":")) if dup else None,
             str(download_id or "")))
        self.conn.commit()
        return int(cur.lastrowid)

    def route_for_download(self, download_id):
        """The most recent routing decision for a browser download id.

        This is the ONLY evidence the sidecar has that a file under the library
        root was put there by this router rather than by qBittorrent. An empty
        id never matches, so an omitted `downloadId` cannot prove anything.
        """
        download_id = str(download_id or "")
        if not download_id:
            return None
        row = self.conn.execute(
            "SELECT * FROM routes WHERE download_id=? ORDER BY id DESC LIMIT 1",
            (download_id,)).fetchone()
        return dict(row) if row else None

    # --- routed-file ledger ------------------------------------------------- #
    def record_routed_file(self, rel_path: str, download_id: str,
                           dir_name: str) -> None:
        """Remember that `rel_path` is a file this router created."""
        self.conn.execute(
            """INSERT INTO routed_files (rel_path, download_id, dir, ts)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(rel_path) DO UPDATE SET
                 download_id = excluded.download_id,
                 dir = excluded.dir,
                 ts = excluded.ts""",
            (rel_path, str(download_id or ""), dir_name, self._clock()))
        self.conn.commit()

    def routed_file(self, rel_path: str):
        row = self.conn.execute(
            "SELECT * FROM routed_files WHERE rel_path=?",
            (rel_path,)).fetchone()
        return dict(row) if row else None

    def move_routed_file(self, old_rel: str, new_rel: str,
                         dir_name: str) -> None:
        """Follow a file through a relocate, so a second correction still has
        provenance for it."""
        row = self.routed_file(old_rel)
        download_id = row["download_id"] if row else ""
        self.conn.execute("DELETE FROM routed_files WHERE rel_path=?",
                          (old_rel,))
        self.conn.commit()
        self.record_routed_file(new_rel, download_id, dir_name)

    def routed_file_count(self) -> int:
        return int(self.conn.execute(
            "SELECT COUNT(*) AS n FROM routed_files").fetchone()["n"])

    def recent_routes(self, limit: int = 50) -> list:
        rows = self.conn.execute(
            "SELECT * FROM routes ORDER BY id DESC LIMIT ?",
            (int(limit),)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["auto"] = bool(d["auto"])
            if d.get("dup"):
                try:
                    d["dup"] = json.loads(d["dup"])
                except ValueError:
                    d["dup"] = None
            out.append(d)
        return out

    # --- host prior -------------------------------------------------------- #
    def set_host_prior(self, site: str, dir_name: str) -> None:
        if not site:
            return
        self.conn.execute(
            """INSERT INTO host_prior (site, dir, ts) VALUES (?, ?, ?)
               ON CONFLICT(site) DO UPDATE SET
                 dir = excluded.dir, ts = excluded.ts""",
            (site, dir_name, self._clock()))
        self.conn.commit()

    def host_prior(self, site: str):
        if not site:
            return None
        row = self.conn.execute("SELECT dir FROM host_prior WHERE site=?",
                                (site,)).fetchone()
        return row["dir"] if row else None
