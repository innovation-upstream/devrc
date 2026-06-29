#!/usr/bin/env python3
"""DB access for the mail-actions extractor.

The mailbox Postgres lives in the homelab cluster and is only reachable in-cluster
(ClusterIP `mailbox-postgres:5432`). We bridge to it by starting a `kubectl
port-forward` on an ephemeral local port, connecting with psycopg2 over 127.0.0.1,
and tearing the forward down on exit. Reads AND writes go through this so email
bodies are passed as bound parameters — never shell-escaped into `psql -c`.

Usage:
    with MailDB() as db:
        rows = db.fetch_unprocessed()
        db.mark_processed(mail_id, label="fyi")

Requires in PATH/env:
    KUBECONFIG  — homelab kubeconfig
    kubectl     — on PATH
    psycopg2    — python dep (psycopg2-binary is fine); see README for nix-shell.
"""
from __future__ import annotations

import contextlib
import os
import socket
import subprocess
import time
from urllib.parse import urlparse

try:
    import psycopg2
    import psycopg2.extras
except ImportError as exc:  # pragma: no cover - import guard
    raise SystemExit(
        "psycopg2 is required. On NixOS run under:\n"
        "  nix-shell -p \"python3.withPackages(p:[p.psycopg2 p.requests])\" "
        "--run 'python scripts/mail-actions/extract.py ...'"
    ) from exc

NAMESPACE = "mailbox"
SERVICE = "svc/mailbox-postgres"
# Idempotency label for the invoice archiver (distinct from action-triage state).
ARCHIVED_LABEL = "invoice-archived"
DSN_SECRET = "mailbox-postgres-auth"
DSN_KEY = "pg-dsn"


def _free_local_port() -> int:
    """Ask the OS for a free TCP port (bind to 0, read it back, release)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _read_dsn_from_secret() -> str:
    """Read the Postgres DSN out of the k8s secret via kubectl + base64 decode."""
    import base64

    out = subprocess.check_output(
        [
            "kubectl", "-n", NAMESPACE, "get", "secret", DSN_SECRET,
            "-o", f"jsonpath={{.data.{DSN_KEY}}}",
        ],
        text=True,
    ).strip()
    return base64.b64decode(out).decode().strip()


def _rewrite_dsn_host(dsn: str, host: str, port: int) -> dict:
    """Parse a postgres:// DSN and return psycopg2 connect kwargs pointing at host:port."""
    u = urlparse(dsn)
    if u.scheme not in ("postgres", "postgresql"):
        raise ValueError(f"unexpected DSN scheme: {u.scheme!r}")
    dbname = (u.path or "/").lstrip("/") or "mailbox"
    return {
        "host": host,
        "port": port,
        "user": u.username,
        "password": u.password,
        "dbname": dbname,
        "connect_timeout": 10,
    }


class MailDB:
    """Context manager: port-forward → psycopg2 connection, torn down on exit."""

    def __init__(self, dsn: str | None = None, ready_timeout: float = 20.0):
        self._dsn = dsn or os.environ.get("MAILBOX_PG_DSN")
        self._ready_timeout = ready_timeout
        self._pf: subprocess.Popen | None = None
        self.conn: "psycopg2.extensions.connection | None" = None

    # -- lifecycle ---------------------------------------------------------
    def __enter__(self) -> "MailDB":
        dsn = self._dsn or _read_dsn_from_secret()
        local_port = _free_local_port()
        self._pf = subprocess.Popen(
            [
                "kubectl", "-n", NAMESPACE, "port-forward", SERVICE,
                f"{local_port}:5432",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        self._wait_for_port("127.0.0.1", local_port)
        kwargs = _rewrite_dsn_host(dsn, "127.0.0.1", local_port)
        self.conn = psycopg2.connect(**kwargs)
        self.conn.autocommit = False
        return self

    def __exit__(self, *_exc) -> None:
        with contextlib.suppress(Exception):
            if self.conn is not None:
                self.conn.close()
        if self._pf is not None:
            self._pf.terminate()
            with contextlib.suppress(Exception):
                self._pf.wait(timeout=5)

    @property
    def _c(self) -> "psycopg2.extensions.connection":
        """The live connection, or a clear error if used outside the context manager."""
        if self.conn is None:
            raise RuntimeError("MailDB used outside its context manager (no connection)")
        return self.conn

    def _wait_for_port(self, host: str, port: int) -> None:
        deadline = time.monotonic() + self._ready_timeout
        while time.monotonic() < deadline:
            if self._pf and self._pf.poll() is not None:
                err = self._pf.stderr.read().decode() if self._pf.stderr else ""
                raise RuntimeError(f"kubectl port-forward exited early:\n{err}")
            with contextlib.suppress(OSError):
                with socket.create_connection((host, port), timeout=1):
                    return
            time.sleep(0.25)
        raise TimeoutError(f"port-forward to {host}:{port} not ready in time")

    # -- schema ------------------------------------------------------------
    def ensure_schema(self) -> None:
        with self._c.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS mail_actions (
                    id          serial PRIMARY KEY,
                    mail_id     int REFERENCES mail(id) UNIQUE,
                    message_id  text,
                    from_addr   text,
                    subject     text,
                    received_at timestamptz,
                    who         text,
                    ask         text,
                    deadline    text,
                    amount      text,
                    confidence  real,
                    reason      text,
                    status      text DEFAULT 'open',
                    created_at  timestamptz DEFAULT now()
                )
                """
            )
        self._c.commit()

    # -- reads -------------------------------------------------------------
    def fetch_unprocessed(self, limit: int | None = None):
        """Delta read: via_gmail mail not yet processed by this pipeline."""
        sql = (
            "SELECT id, message_id, from_addr, subject, received_at, category, "
            "headers, text_body "
            "FROM mail WHERE via_gmail AND processed_at IS NULL "
            "ORDER BY received_at DESC"
        )
        params: tuple = ()
        if limit is not None:
            sql += " LIMIT %s"
            params = (limit,)
        with self._c.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            return cur.fetchall()

    def fetch_unarchived(self, limit: int | None = None):
        """Invoice-archiver delta: via_gmail mail with a raw message that has NOT yet
        been archived (no 'invoice-archived' label). Independent of processed_at — a
        mail may be both fyi-processed AND not-yet-archived. Returns the row INCLUDING
        the `raw` bytea, converted from psycopg2's memoryview to plain bytes.
        """
        sql = (
            "SELECT id, message_id, from_addr, subject, received_at, date_header, "
            "raw "
            "FROM mail "
            "WHERE via_gmail AND raw IS NOT NULL "
            "AND NOT (%s = ANY(coalesce(labels, '{}'))) "
            "ORDER BY received_at DESC"
        )
        params: tuple = (ARCHIVED_LABEL,)
        if limit is not None:
            sql += " LIMIT %s"
            params = (ARCHIVED_LABEL, limit)
        with self._c.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
        for r in rows:
            if isinstance(r.get("raw"), memoryview):
                r["raw"] = r["raw"].tobytes()
        return rows

    def amount_for_mail(self, mail_id: int) -> str | None:
        """Best-effort: the `amount` from a mail_actions row for this mail, if one
        exists (the action pipeline may have extracted it). None otherwise — including
        when the mail_actions table has never been created (action pipeline unrun)."""
        with self._c.cursor() as cur:
            cur.execute("SELECT to_regclass('public.mail_actions')")
            reg = cur.fetchone()
            if reg is None or reg[0] is None:
                return None
            cur.execute(
                "SELECT amount FROM mail_actions WHERE mail_id = %s", (mail_id,)
            )
            row = cur.fetchone()
        return row[0] if row else None

    def list_open_actions(self):
        with self._c.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT id, mail_id, from_addr, subject, received_at, who, ask, "
                "deadline, amount, confidence, status, created_at "
                "FROM mail_actions WHERE status = 'open' ORDER BY created_at DESC"
            )
            return cur.fetchall()

    # -- writes ------------------------------------------------------------
    def mark_processed(self, mail_id: int, label: str) -> None:
        """Append `label` to mail.labels (dedup) and stamp processed_at=now()."""
        with self._c.cursor() as cur:
            cur.execute(
                """
                UPDATE mail
                   SET labels = (
                         SELECT array_agg(DISTINCT x)
                         FROM unnest(coalesce(labels, '{}') || ARRAY[%s]::text[]) AS x
                       ),
                       processed_at = now()
                 WHERE id = %s
                """,
                (label, mail_id),
            )

    def add_label(self, mail_id: int, label: str) -> None:
        """Append `label` to mail.labels (dedup) WITHOUT touching processed_at.

        Distinct from mark_processed: archival state (`invoice-archived`) is
        orthogonal to action-triage state, so it must not stamp processed_at."""
        with self._c.cursor() as cur:
            cur.execute(
                """
                UPDATE mail
                   SET labels = (
                         SELECT array_agg(DISTINCT x)
                         FROM unnest(coalesce(labels, '{}') || ARRAY[%s]::text[]) AS x
                       )
                 WHERE id = %s
                """,
                (label, mail_id),
            )

    def insert_action(self, row: dict) -> bool:
        """Insert a mail_actions row. Returns True if inserted, False on conflict."""
        with self._c.cursor() as cur:
            cur.execute(
                """
                INSERT INTO mail_actions
                    (mail_id, message_id, from_addr, subject, received_at,
                     who, ask, deadline, amount, confidence, reason)
                VALUES
                    (%(mail_id)s, %(message_id)s, %(from_addr)s, %(subject)s,
                     %(received_at)s, %(who)s, %(ask)s, %(deadline)s, %(amount)s,
                     %(confidence)s, %(reason)s)
                ON CONFLICT (mail_id) DO NOTHING
                """,
                row,
            )
            return cur.rowcount > 0

    def commit(self) -> None:
        self._c.commit()
