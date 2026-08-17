#!/usr/bin/env python3
"""DB access for the Signal chat pipeline (`signal` schema).

Same Postgres instance as the mailbox (`mailbox-postgres-0`), a SEPARATE schema.
This module is a deliberate clone of `scripts/mail-actions/_db.py` — same
context-manager shape, same two connection modes:

  * **Port-forward (default, OFF-cluster).** Workbench tools reach the in-cluster
    ClusterIP through an ephemeral `kubectl port-forward`, torn down on exit.
  * **Direct (IN-cluster).** The consumer Deployment reaches
    `mailbox-postgres.mailbox.svc.cluster.local:5432` directly and must NOT spawn a
    port-forward per query. Opt in with `SIGNAL_PG_HOST` (optionally
    `SIGNAL_PG_PORT`) or the explicit `SIGNAL_PG_DIRECT=1`. See `_direct_target()`.

Credentials come from `SIGNAL_PG_DSN` / the `dsn=` arg when provided, else the k8s
secret `mailbox-postgres-auth` (key `pg-dsn`) — the mailbox and signal schemas share
one database role.

Every message body is passed as a BOUND PARAMETER; nothing is shell-escaped into
`psql -c`.

THE SEND SURFACE IS SPLIT IN TWO (decision D3, proposal §7)
-----------------------------------------------------------
`draft_message()` composes and STORES; `send_approved()` is the only thing that can
transmit, and it can only do so by minting a `SendAuthorization` capability that
`consumer.transmit_approved()` demands. The capability's constructor refuses direct
construction, and `_mint_send_authorization()` refuses any draft whose `send_state`
is not `approved`. So an un-approved draft has NO CODE ROUTE to the Signal API —
the gate is a missing edge in the call graph, not a documented convention.
"""
from __future__ import annotations

import contextlib
import os
import socket
import subprocess
import time
import uuid
from urllib.parse import urlparse

try:
    import psycopg2
    import psycopg2.extras
except ImportError as exc:  # pragma: no cover - import guard
    raise SystemExit(
        "psycopg2 is required. On NixOS run under:\n"
        "  nix-shell -p \"python3.withPackages(p:[p.psycopg2 p.requests p.minio])\" "
        "--run 'python scripts/signal/consumer.py'"
    ) from exc

NAMESPACE = "mailbox"          # the signal SCHEMA lives on the mailbox Postgres
SERVICE = "svc/mailbox-postgres"
DSN_SECRET = "mailbox-postgres-auth"
DSN_KEY = "pg-dsn"
SCHEMA = "signal"

# Deterministic placeholder identity for a sender the API has not resolved to a
# UUID yet. See `placeholder_uuid()` — this is half of 🔧 correction #1.
PLACEHOLDER_NS = uuid.UUID("6f9c0d1e-2b3a-4c5d-8e7f-0a1b2c3d4e5f")

# Outbound draft lifecycle. `pending` -> `approved` -> `sent`; a draft is never
# deleted, so it cannot be silently lost.
STATE_PENDING = "pending"
STATE_APPROVED = "approved"
# 🔴 `sending` exists so a crash BETWEEN the POST and the write-back cannot cause
# a RESEND. The row moves to `sending` and is committed BEFORE the POST; only
# `approved` can mint a capability, so a draft found in `sending` is inert until a
# human reconciles it against Signal. Fail-safe direction chosen deliberately:
# "may need manual re-approval" beats "may send the same text twice".
STATE_SENDING = "sending"
STATE_SENT = "sent"

# Must be present in the environment for `approve_draft()` to run. An operator-only
# variable: absent from the consumer Deployment and from agent environments. A
# speed bump against an agent approving its own draft, NOT a proof of humanity —
# `approve_draft()`'s docstring says exactly how far it goes.
APPROVAL_TOKEN_ENV = "SIGNAL_APPROVAL_TOKEN"

# The two outcomes an operator can record for a draft stranded in `sending`.
RECONCILE_SENT = "sent"
RECONCILE_NOT_SENT = "not-sent"


# --------------------------------------------------------------------------- #
# Schema — the single source of truth. Tests DERIVE from these statements rather
# than restating them, and the hermetic substrate translates them for sqlite.
#
# Four corrections against the original draft DDL are marked 🔧. Each one is a
# named suite in scripts/signal/tests/ and each has been mutation-tested.
# --------------------------------------------------------------------------- #
SCHEMA_STATEMENTS: tuple[str, ...] = (
    "CREATE SCHEMA IF NOT EXISTS signal",

    """
    CREATE TABLE IF NOT EXISTS signal.contacts (
        id SERIAL PRIMARY KEY,
        signal_uuid UUID UNIQUE NOT NULL,
        phone_number TEXT,
        display_name TEXT,
        profile_name TEXT,
        is_placeholder BOOLEAN DEFAULT false,
        created_at TIMESTAMPTZ DEFAULT now()
    )
    """,

    """
    CREATE TABLE IF NOT EXISTS signal.groups (
        id SERIAL PRIMARY KEY,
        group_id BYTEA UNIQUE NOT NULL,
        name TEXT NOT NULL,
        revision INTEGER DEFAULT 0,
        created_at TIMESTAMPTZ DEFAULT now()
    )
    """,

    # 🔧 #1  `source_contact_id NOT NULL`. A UNIQUE over a NULLable column does
    # NOT dedupe in Postgres (NULLs compare distinct), so an envelope from an
    # unresolved sender would insert a fresh row on EVERY redelivery.
    # Unknown senders get a deterministic placeholder contact instead
    # (`placeholder_uuid()`), which is what makes `unique_message` bite.
    #
    # 🔧 #4 (behavioural half) lives here too: `message_timestamp` for an
    # outbound message MUST be the SERVER-assigned timestamp, because that is
    # what the device-sync echo carries back on the receive stream. Un-sent
    # drafts hold a NEGATIVE provisional timestamp, which can never collide with
    # a real (positive, epoch-ms) server timestamp.
    """
    CREATE TABLE IF NOT EXISTS signal.messages (
        id BIGSERIAL PRIMARY KEY,
        message_timestamp BIGINT NOT NULL,
        server_received_at BIGINT,
        server_delivered_at BIGINT,
        source_contact_id INTEGER NOT NULL REFERENCES signal.contacts(id),
        dest_contact_id INTEGER REFERENCES signal.contacts(id),
        message_type TEXT NOT NULL,
        body TEXT,
        expires_in_seconds INTEGER,
        view_once BOOLEAN DEFAULT false,
        edit_target_timestamp BIGINT,
        group_id INTEGER REFERENCES signal.groups(id),
        is_outbound BOOLEAN DEFAULT false,
        send_state TEXT,
        send_attempts INTEGER DEFAULT 0,
        approval_ref TEXT,
        raw_envelope JSONB,
        search tsvector GENERATED ALWAYS AS (to_tsvector('english', coalesce(body, ''))) STORED,
        CONSTRAINT unique_message UNIQUE (source_contact_id, message_timestamp)
    )
    """,

    # 🔧 #2  Without `unique_attachment`, a redelivery of the same message
    # duplicates every attachment row.
    """
    CREATE TABLE IF NOT EXISTS signal.attachments (
        id BIGSERIAL PRIMARY KEY,
        message_id BIGINT REFERENCES signal.messages(id) ON DELETE CASCADE,
        signal_attachment_id TEXT NOT NULL,
        content_type TEXT NOT NULL,
        filename TEXT,
        size_bytes BIGINT,
        caption TEXT,
        is_voice_note BOOLEAN DEFAULT false,
        minio_bucket TEXT,
        minio_key TEXT,
        created_at TIMESTAMPTZ DEFAULT now(),
        CONSTRAINT unique_attachment UNIQUE (message_id, signal_attachment_id)
    )
    """,

    # 🔧 #3  `message_id` is NULLable and resolved LATER: a reaction can arrive
    # BEFORE its target message (delivery is not ordered), or target one we never
    # received at all. A hard FK at insert time drops those on the floor.
    """
    CREATE TABLE IF NOT EXISTS signal.reactions (
        id BIGSERIAL PRIMARY KEY,
        message_id BIGINT REFERENCES signal.messages(id) ON DELETE CASCADE,
        target_author_id INTEGER NOT NULL REFERENCES signal.contacts(id),
        target_sent_timestamp BIGINT NOT NULL,
        emoji TEXT NOT NULL,
        contact_id INTEGER NOT NULL REFERENCES signal.contacts(id),
        is_remove BOOLEAN DEFAULT false,
        CONSTRAINT unique_reaction UNIQUE (contact_id, target_author_id, target_sent_timestamp)
    )
    """,

    "CREATE INDEX IF NOT EXISTS idx_msg_ts ON signal.messages(message_timestamp DESC)",
    "CREATE INDEX IF NOT EXISTS idx_msg_dm ON signal.messages(source_contact_id, message_timestamp)",
    "CREATE INDEX IF NOT EXISTS idx_msg_group ON signal.messages(group_id, message_timestamp) WHERE group_id IS NOT NULL",
    "CREATE INDEX IF NOT EXISTS idx_msg_fts ON signal.messages USING GIN(search)",
    "CREATE INDEX IF NOT EXISTS idx_att_msg ON signal.attachments(message_id)",
    # The partial index that makes the unresolved-reaction sweep cheap (🔧 #3).
    "CREATE INDEX IF NOT EXISTS idx_rx_unresolved ON signal.reactions(target_author_id, target_sent_timestamp) WHERE message_id IS NULL",
)


class SendGateError(RuntimeError):
    """The D3 approval gate refused a transmit attempt.

    Its own error type so a test can assert THIS guard fired, rather than
    accepting any exception (a different guard's error, or a happy-path
    resolution, would otherwise read as a kill).
    """


class SendAuthorization:
    """Unforgeable capability: proof that ONE approved draft may be transmitted once.

    Direct construction is refused. The only producer is
    `_mint_send_authorization()`, which will not mint for a draft whose
    `send_state` is not `approved`. `consumer.transmit_approved()` demands one of
    these AND checks it against the live issue registry, so a hand-rolled
    look-alike object cannot stand in for it.
    """

    __slots__ = ("draft_id", "recipient", "body", "_nonce")

    def __init__(self, *_a, **_kw):  # pragma: no cover - exercised via the gate tests
        raise SendGateError(
            "SendAuthorization cannot be constructed directly — it is minted only "
            "by _signal_db._mint_send_authorization() for a draft whose send_state "
            "is 'approved' (D3 approval gate)"
        )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<SendAuthorization draft={self.draft_id} nonce=…>"


# Nonces of authorizations that have been minted and NOT yet spent. A capability
# is single-use: `consumer.transmit_approved()` pops the nonce before sending, so
# replaying the same object cannot transmit twice.
_ISSUED_NONCES: set[str] = set()


def _mint_send_authorization(draft: dict) -> SendAuthorization:
    """Mint a one-shot send capability for an APPROVED draft row.

    🔴 This is the whole gate. Raises `SendGateError` — naming the offending
    state — for any draft that is not `approved`, so an un-approved draft has no
    way to obtain the object `transmit_approved()` requires.
    """
    state = (draft or {}).get("send_state")
    if state != STATE_APPROVED:
        raise SendGateError(
            f"draft {(draft or {}).get('id')!r} has send_state={state!r}; only "
            f"{STATE_APPROVED!r} drafts may be transmitted (D3 approval gate)"
        )
    auth = object.__new__(SendAuthorization)
    object.__setattr__(auth, "draft_id", draft["id"])
    object.__setattr__(auth, "recipient", draft.get("recipient"))
    object.__setattr__(auth, "body", draft.get("body"))
    nonce = uuid.uuid4().hex
    object.__setattr__(auth, "_nonce", nonce)
    _ISSUED_NONCES.add(nonce)
    return auth


def spend_authorization(auth: object) -> None:
    """Consume a capability, or raise `SendGateError`.

    Called by `consumer.transmit_approved()` immediately before it touches the
    network. Rejects (a) anything that is not a real `SendAuthorization` and
    (b) a capability that has already been spent — which is what makes an
    approved draft transmit EXACTLY once.
    """
    if not isinstance(auth, SendAuthorization):
        raise SendGateError(
            "transmit refused: a SendAuthorization minted from an approved draft "
            "is required (D3 approval gate); got "
            f"{type(auth).__name__}"
        )
    nonce = getattr(auth, "_nonce", None)
    if nonce not in _ISSUED_NONCES:
        raise SendGateError(
            "transmit refused: this SendAuthorization has already been spent "
            "(D3 approval gate — capabilities are single-use)"
        )
    _ISSUED_NONCES.discard(nonce)


# `message_type` of a row whose sender retracted it. The row is KEPT (its
# timestamp still has to dedupe redeliveries) but carries no content.
TYPE_DELETED = "deleted"


def _server_timestamp(result) -> int:
    """The server-assigned send timestamp, coerced and sanity-checked (🔧 #4).

    🔴 Upstream types this field as a **string** — `ds.SendMessageResponse`'s
    `Timestamp string \\`json:"timestamp"\\`` — even though it holds epoch-ms. A
    fake that returns an int would let a caller which only handles ints pass the
    suite and fail against the real server, so both are accepted here and the
    string form is what the tests feed.
    """
    if not isinstance(result, dict) or "timestamp" not in result:
        raise ValueError(
            f"the Signal API response carries no `timestamp`: {result!r}; without "
            "it the sync echo cannot be deduped (🔧 #4)"
        )
    raw = result["timestamp"]
    try:
        server_ts = int(str(raw).strip())
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"the Signal API returned a non-numeric timestamp {raw!r}; storing it "
            "would break sync-echo dedupe (🔧 #4)"
        ) from exc
    if server_ts <= 0:
        raise ValueError(
            f"the Signal API returned a non-positive timestamp {server_ts!r}; "
            "storing it would break sync-echo dedupe (🔧 #4)"
        )
    return server_ts


def _returned_id(cur, what: str) -> int:
    """The id from an `INSERT … RETURNING id`, or an error that NAMES the write.

    Every RETURNING in this module follows an `ON CONFLICT … DO UPDATE`, which
    always yields a row — so this is a diagnostic guard, not a live branch. It
    exists because the bare `cur.fetchone()[0]` it replaces surfaces any driver
    or statement surprise as `TypeError: 'NoneType' object is not subscriptable`,
    naming nothing.
    """
    row = cur.fetchone()
    if row is None:
        raise RuntimeError(f"{what}: INSERT … RETURNING id produced no row")
    return row[0]


def placeholder_uuid(identifier: str) -> str:
    """Deterministic placeholder UUID for an unresolved sender (🔧 #1).

    The SAME unresolved identifier always maps to the SAME contact row, so a
    redelivered envelope from that sender hits `unique_message` instead of
    inserting a second copy. Derived (uuid5) rather than random for exactly that
    reason — a random uuid here would reintroduce the NULL-dedupe hole under a
    different name.
    """
    return str(uuid.uuid5(PLACEHOLDER_NS, f"signal:{identifier}"))


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


def _dsn_connect_kwargs(dsn: str, *, host: str | None = None,
                        port: int | None = None) -> dict:
    """Parse a postgres:// DSN → psycopg2 connect kwargs.

    `host`/`port` OVERRIDE the DSN's own values when provided; None keeps the
    DSN's own. Falls back to 5432 when neither specifies a port.
    """
    u = urlparse(dsn)
    if u.scheme not in ("postgres", "postgresql"):
        raise ValueError(f"unexpected DSN scheme: {u.scheme!r}")
    dbname = (u.path or "/").lstrip("/") or "mailbox"
    return {
        "host": host if host is not None else u.hostname,
        "port": port if port is not None else (u.port or 5432),
        "user": u.username,
        "password": u.password,
        "dbname": dbname,
        "connect_timeout": 10,
    }


def _truthy(val: str | None) -> bool:
    return (val or "").strip().lower() in ("1", "true", "yes", "on")


def _direct_target() -> tuple[str | None, int | None] | None:
    """Decide whether to connect DIRECTLY (no kubectl port-forward), and to where.

    None → port-forward (the off-cluster default). Otherwise `(host_override,
    port_override)`, either of which may be None to mean "keep the DSN's value".

      * `SIGNAL_PG_HOST` set → direct to that host; `SIGNAL_PG_PORT` overrides
        the port (default: the DSN's, else 5432).
      * `SIGNAL_PG_DIRECT` truthy → direct using the DSN's OWN host/port.
    """
    host = os.environ.get("SIGNAL_PG_HOST") or None
    direct = _truthy(os.environ.get("SIGNAL_PG_DIRECT"))
    if not host and not direct:
        return None
    port_s = os.environ.get("SIGNAL_PG_PORT")
    port = int(port_s) if port_s and port_s.strip() else None
    return host, port


class SignalDB:
    """Context manager: (optional) port-forward → psycopg2 connection, torn down on exit."""

    def __init__(self, dsn: str | None = None, ready_timeout: float = 20.0):
        self._dsn = dsn or os.environ.get("SIGNAL_PG_DSN")
        self._ready_timeout = ready_timeout
        self._pf: subprocess.Popen | None = None
        self.conn: "psycopg2.extensions.connection | None" = None

    # -- lifecycle ---------------------------------------------------------
    def __enter__(self) -> "SignalDB":
        self._connect()
        return self

    def _connect(self) -> None:
        """Open (or RE-open) the connection. Called by `__enter__` and `recover()`.

        Extracted so a dead socket can be replaced in place: a long-lived daemon
        outlives any single connection, and `recover()` is the only thing standing
        between a dropped socket and a pod that stores nothing forever.
        """
        direct = _direct_target()
        if direct is not None:
            dsn = self._dsn or _read_dsn_from_secret()
            host_override, port_override = direct
            kwargs = _dsn_connect_kwargs(dsn, host=host_override, port=port_override)
            self.conn = psycopg2.connect(**kwargs)
            self.conn.autocommit = False
            return

        dsn = self._dsn or _read_dsn_from_secret()
        if self._pf is not None:
            # A reconnect through a dead port-forward needs a NEW forward.
            with contextlib.suppress(Exception):
                self._pf.terminate()
            self._pf = None
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
        kwargs = _dsn_connect_kwargs(dsn, host="127.0.0.1", port=local_port)
        self.conn = psycopg2.connect(**kwargs)
        self.conn.autocommit = False

    def __exit__(self, *_exc) -> None:
        with contextlib.suppress(Exception):
            if self.conn is not None:
                self.conn.close()
        if self._pf is not None:
            self._pf.terminate()
            with contextlib.suppress(Exception):
                self._pf.wait(timeout=5)

    @property
    def _c(self):
        if self.conn is None:
            raise RuntimeError("SignalDB used outside its context manager (no connection)")
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

    # -- transaction recovery ----------------------------------------------
    def rollback(self) -> None:
        """Abandon the open transaction. Safe to call when there is nothing to undo."""
        if self.conn is not None:
            self.conn.rollback()

    def recover(self) -> str:
        """Make the connection usable again after a failed statement.

        🔴 WHY THIS EXISTS. The connections here run `autocommit=False`, and in
        Postgres the FIRST failed statement aborts the whole transaction: every
        later statement then raises `InFailedSqlTransaction` until someone rolls
        back. A daemon without this stores nothing for the rest of its life while
        logging as though it were merely reconnecting.

        Returns `rolled-back` or `reconnected` so the caller can count and log
        which one happened — the two mean very different things about the pod.
        """
        conn = self.conn
        if conn is None:
            self._connect()
            return "reconnected"
        if getattr(conn, "closed", 0):
            self._connect()
            return "reconnected"
        try:
            conn.rollback()
            return "rolled-back"
        except Exception:
            # The socket itself is gone — a rollback cannot fix that.
            with contextlib.suppress(Exception):
                conn.close()
            self._connect()
            return "reconnected"

    # -- schema ------------------------------------------------------------
    def ensure_schema(self) -> None:
        """Create the `signal` schema. Idempotent — every statement is IF NOT EXISTS."""
        with self._c.cursor() as cur:
            for stmt in SCHEMA_STATEMENTS:
                cur.execute(stmt)
        self._c.commit()

    # -- contacts / groups -------------------------------------------------
    def upsert_contact(self, *, signal_uuid: str | None = None,
                       phone_number: str | None = None,
                       display_name: str | None = None,
                       profile_name: str | None = None) -> int:
        """Insert-or-update one contact, returning its id.

        🔴 ONE PERSON MUST RESOLVE TO ONE ROW IN BOTH ARRIVAL ORDERS. A Signal
        identity reaches us two ways — as a bare phone number (what an agent
        types into a draft) and as a uuid (what every envelope carries) — and
        `unique_message` keys on `source_contact_id`, so two rows for one person
        means the outbound sync echo (🔧 #4) never collides and EVERY sent message
        is stored twice. The two orders are both real and both handled here:

          draft-then-echo: the number makes a PLACEHOLDER, the uuid arrives later
                           -> `_promote_placeholder()` gives that row the uuid.
          echo-then-draft: the uuid row already exists (true from day two of any
                           deployment), then a draft supplies only the number
                           -> the phone LOOKUP below finds it. Without that
                           lookup a second placeholder is created and promotion
                           correctly declines (the uuid is taken), which is
                           exactly the silent-duplicate case.

        🔧 #1: only when NEITHER a uuid nor an existing row can be found is a
        DETERMINISTIC placeholder derived, so a redelivered envelope from an
        unresolved sender still lands on one row.
        """
        is_placeholder = False
        if not signal_uuid:
            ident = phone_number or display_name or profile_name
            if not ident:
                raise ValueError(
                    "upsert_contact needs a signal_uuid or some stable identifier; "
                    "an unidentifiable sender would break message dedupe (🔧 #1)"
                )
            if phone_number:
                existing = self.contact_id_by_phone(phone_number)
                if existing is not None:
                    # A row for this number already exists — real or placeholder.
                    # Reuse it rather than minting a rival identity.
                    self._enrich_contact(existing, display_name=display_name,
                                         profile_name=profile_name)
                    return existing
            signal_uuid = placeholder_uuid(ident)
            is_placeholder = True
        elif phone_number:
            self._promote_placeholder(signal_uuid, phone_number)
        with self._c.cursor() as cur:
            cur.execute(
                """
                INSERT INTO signal.contacts
                    (signal_uuid, phone_number, display_name, profile_name, is_placeholder)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (signal_uuid) DO UPDATE SET
                    phone_number = COALESCE(EXCLUDED.phone_number, contacts.phone_number),
                    display_name = COALESCE(EXCLUDED.display_name, contacts.display_name),
                    profile_name = COALESCE(EXCLUDED.profile_name, contacts.profile_name)
                RETURNING id
                """,
                (signal_uuid, phone_number, display_name, profile_name, is_placeholder),
            )
            return _returned_id(cur, "upsert_contact")

    def contact_id_by_phone(self, phone_number: str) -> int | None:
        """The contact row for a phone number, preferring a REAL one over a placeholder.

        `phone_number` is not UNIQUE (a placeholder and a promoted row could
        briefly race), so the ordering is explicit rather than incidental: a
        resolved contact wins, then the oldest row. Deterministic either way —
        an arbitrary pick here would reintroduce split identities.
        """
        if not phone_number:
            return None
        with self._c.cursor() as cur:
            cur.execute(
                "SELECT id FROM signal.contacts WHERE phone_number = %s "
                "ORDER BY is_placeholder, id LIMIT 1",
                (phone_number,),
            )
            row = cur.fetchone()
        return row[0] if row else None

    def _enrich_contact(self, contact_id: int, *, display_name: str | None,
                        profile_name: str | None) -> None:
        """Fill in names we have learned WITHOUT blanking ones we already had."""
        if display_name is None and profile_name is None:
            return
        with self._c.cursor() as cur:
            cur.execute(
                "UPDATE signal.contacts SET "
                "display_name = COALESCE(%s, display_name), "
                "profile_name = COALESCE(%s, profile_name) WHERE id = %s",
                (display_name, profile_name, contact_id),
            )

    def _promote_placeholder(self, signal_uuid: str, phone_number: str) -> int:
        """Give a placeholder contact its real uuid, preserving its row id.

        The `NOT EXISTS` guard is load-bearing: if a real contact with that uuid
        already exists, promoting would violate `contacts.signal_uuid UNIQUE`.
        In that case the placeholder is simply left alone. Returns the rowcount.
        """
        with self._c.cursor() as cur:
            cur.execute(
                "UPDATE signal.contacts SET signal_uuid = %s, is_placeholder = false "
                "WHERE phone_number = %s AND is_placeholder "
                "AND NOT EXISTS (SELECT 1 FROM signal.contacts c2 "
                "                WHERE c2.signal_uuid = %s)",
                (signal_uuid, phone_number, signal_uuid),
            )
            return cur.rowcount

    def upsert_group(self, *, group_id: bytes, name: str | None = None,
                     revision: int = 0) -> int:
        with self._c.cursor() as cur:
            cur.execute(
                """
                INSERT INTO signal.groups (group_id, name, revision)
                VALUES (%s, %s, %s)
                ON CONFLICT (group_id) DO UPDATE SET
                    name = COALESCE(EXCLUDED.name, groups.name),
                    revision = GREATEST(EXCLUDED.revision, groups.revision)
                RETURNING id
                """,
                (group_id, name or "", revision),
            )
            return _returned_id(cur, "upsert_group")

    # -- messages ----------------------------------------------------------
    def upsert_message(self, msg: dict) -> int:
        """Store one parsed message idempotently; returns its row id.

        `msg` is a `consumer.parse_envelope()` result rendered as a dict. Resolves
        the sender (placeholder if needed — 🔧 #1), the group, the attachments
        (🔧 #2), and any reactions that arrived BEFORE this message (🔧 #3).
        """
        source_id = self.upsert_contact(
            signal_uuid=msg.get("source_uuid"),
            phone_number=msg.get("source_number"),
            display_name=msg.get("source_name"),
        )
        dest_id = None
        if msg.get("dest_uuid") or msg.get("dest_number"):
            dest_id = self.upsert_contact(
                signal_uuid=msg.get("dest_uuid"),
                phone_number=msg.get("dest_number"),
            )
        group_row_id = None
        if msg.get("group_id"):
            group_row_id = self.upsert_group(
                group_id=msg["group_id"], name=msg.get("group_name"),
            )
        params = {
            "message_timestamp": msg["message_timestamp"],
            "server_received_at": msg.get("server_received_at"),
            "server_delivered_at": msg.get("server_delivered_at"),
            "source_contact_id": source_id,
            "dest_contact_id": dest_id,
            "message_type": msg.get("message_type") or "unknown",
            "body": msg.get("body"),
            "expires_in_seconds": msg.get("expires_in_seconds"),
            "view_once": bool(msg.get("view_once")),
            "edit_target_timestamp": msg.get("edit_target_timestamp"),
            "group_id": group_row_id,
            "is_outbound": bool(msg.get("is_outbound")),
            "raw_envelope": msg.get("raw_envelope"),
        }
        with self._c.cursor() as cur:
            cur.execute(
                """
                INSERT INTO signal.messages
                    (message_timestamp, server_received_at, server_delivered_at,
                     source_contact_id, dest_contact_id, message_type, body,
                     expires_in_seconds, view_once, edit_target_timestamp,
                     group_id, is_outbound, raw_envelope)
                VALUES
                    (%(message_timestamp)s, %(server_received_at)s,
                     %(server_delivered_at)s, %(source_contact_id)s,
                     %(dest_contact_id)s, %(message_type)s, %(body)s,
                     %(expires_in_seconds)s, %(view_once)s,
                     %(edit_target_timestamp)s, %(group_id)s, %(is_outbound)s,
                     %(raw_envelope)s)
                ON CONFLICT (source_contact_id, message_timestamp) DO UPDATE SET
                    server_delivered_at = COALESCE(EXCLUDED.server_delivered_at,
                                                   messages.server_delivered_at),
                    body = COALESCE(messages.body, EXCLUDED.body)
                RETURNING id
                """,
                params,
            )
            message_id = _returned_id(cur, "upsert_message")

        for att in msg.get("attachments") or []:
            self.upsert_attachment(message_id, att)

        # 🔧 #3 — a reaction may have arrived BEFORE this message.
        self.resolve_pending_reactions(
            message_id=message_id,
            target_author_id=source_id,
            target_sent_timestamp=msg["message_timestamp"],
        )
        return message_id

    def upsert_attachment(self, message_id: int, att: dict) -> None:
        """🔧 #2 — idempotent on `(message_id, signal_attachment_id)`."""
        with self._c.cursor() as cur:
            cur.execute(
                """
                INSERT INTO signal.attachments
                    (message_id, signal_attachment_id, content_type, filename,
                     size_bytes, caption, is_voice_note, minio_bucket, minio_key)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (message_id, signal_attachment_id) DO UPDATE SET
                    minio_bucket = COALESCE(EXCLUDED.minio_bucket,
                                            attachments.minio_bucket),
                    minio_key = COALESCE(EXCLUDED.minio_key,
                                         attachments.minio_key)
                """,
                (
                    message_id, att.get("id"), att.get("content_type") or "",
                    att.get("filename"), att.get("size"), att.get("caption"),
                    bool(att.get("is_voice_note")), att.get("minio_bucket"),
                    att.get("minio_key"),
                ),
            )

    def record_attachment_object(self, message_id: int, attachment_id: str,
                                 bucket: str, key: str) -> int:
        """Stamp where an attachment's bytes landed in MinIO. Returns rowcount."""
        with self._c.cursor() as cur:
            cur.execute(
                "UPDATE signal.attachments SET minio_bucket = %s, minio_key = %s "
                "WHERE message_id = %s AND signal_attachment_id = %s",
                (bucket, key, message_id, attachment_id),
            )
            return cur.rowcount

    # -- reactions ---------------------------------------------------------
    def upsert_reaction(self, rx: dict) -> int:
        """Store one reaction, resolving its target message if we already have it.

        🔧 #3 — the target may not exist yet (delivery is not ordered) or may never
        arrive. `message_id` stays NULL in that case and is filled in later by
        `resolve_pending_reactions()`; the reaction is RETAINED either way.
        """
        author_id = self.upsert_contact(
            signal_uuid=rx.get("target_author_uuid"),
            phone_number=rx.get("target_author_number"),
        )
        reactor_id = self.upsert_contact(
            signal_uuid=rx.get("source_uuid"),
            phone_number=rx.get("source_number"),
        )
        with self._c.cursor() as cur:
            cur.execute(
                "SELECT id FROM signal.messages "
                "WHERE source_contact_id = %s AND message_timestamp = %s",
                (author_id, rx["target_sent_timestamp"]),
            )
            row = cur.fetchone()
            target_message_id = row[0] if row else None
            cur.execute(
                """
                INSERT INTO signal.reactions
                    (message_id, target_author_id, target_sent_timestamp, emoji,
                     contact_id, is_remove)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (contact_id, target_author_id, target_sent_timestamp)
                DO UPDATE SET
                    emoji = EXCLUDED.emoji,
                    is_remove = EXCLUDED.is_remove,
                    message_id = COALESCE(EXCLUDED.message_id,
                                          reactions.message_id)
                RETURNING id
                """,
                (
                    target_message_id, author_id, rx["target_sent_timestamp"],
                    rx.get("emoji") or "", reactor_id, bool(rx.get("is_remove")),
                ),
            )
            return _returned_id(cur, "upsert_reaction")

    def resolve_pending_reactions(self, *, message_id: int, target_author_id: int,
                                  target_sent_timestamp: int) -> int:
        """Attach previously-unresolvable reactions to a message that just arrived.

        🔧 #3 — the WHERE clause is exactly the partial index
        `idx_rx_unresolved (target_author_id, target_sent_timestamp) WHERE
        message_id IS NULL`. Returns the number of reactions resolved.
        """
        with self._c.cursor() as cur:
            cur.execute(
                "UPDATE signal.reactions SET message_id = %s "
                "WHERE message_id IS NULL AND target_author_id = %s "
                "AND target_sent_timestamp = %s",
                (message_id, target_author_id, target_sent_timestamp),
            )
            return cur.rowcount

    def unresolved_reactions(self) -> list:
        """Reactions whose target message has never been received."""
        with self._c.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT id, target_author_id, target_sent_timestamp, emoji, "
                "contact_id, is_remove FROM signal.reactions WHERE message_id IS NULL"
            )
            return [dict(r) for r in cur.fetchall()]

    # -- reads -------------------------------------------------------------
    def list_conversations(self, limit: int = 50) -> list:
        """One row per CONVERSATION — a group, else the PEER of a DM.

        🔴 Grouping by `source_contact_id` (the obvious thing) is wrong in two
        directions at once: a DM comes back as TWO rows, one per direction, and
        every outbound message in the whole database collapses into a single "me"
        row. The conversation key is therefore the group when there is one, and
        otherwise the OTHER PARTY — the destination for an outbound message, the
        sender for an inbound one.
        """
        with self._c.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT
                    conv.group_row_id,
                    g.name AS group_name,
                    conv.contact_id,
                    c.display_name,
                    c.phone_number,
                    conv.last_message_timestamp,
                    conv.message_count
                FROM (
                    SELECT
                        m.group_id AS group_row_id,
                        CASE WHEN m.group_id IS NOT NULL THEN NULL
                             WHEN m.is_outbound THEN m.dest_contact_id
                             ELSE m.source_contact_id
                        END AS contact_id,
                        max(m.message_timestamp) AS last_message_timestamp,
                        count(*) AS message_count
                    FROM signal.messages m
                    GROUP BY 1, 2
                ) conv
                LEFT JOIN signal.groups g ON g.id = conv.group_row_id
                LEFT JOIN signal.contacts c ON c.id = conv.contact_id
                ORDER BY conv.last_message_timestamp DESC
                LIMIT %s
                """,
                (limit,),
            )
            return [dict(r) for r in cur.fetchall()]

    def search(self, query: str, limit: int = 25) -> list:
        """Full-text search over message bodies (the STORED tsvector + GIN index).

        The query text is a BOUND PARAMETER — never interpolated into SQL.
        """
        with self._c.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT m.id, m.message_timestamp, m.body, m.is_outbound,
                       c.display_name, c.phone_number
                FROM signal.messages m
                LEFT JOIN signal.contacts c ON c.id = m.source_contact_id
                WHERE m.search @@ websearch_to_tsquery('english', %s)
                ORDER BY m.message_timestamp DESC
                LIMIT %s
                """,
                (query, limit),
            )
            return [dict(r) for r in cur.fetchall()]

    def get_message(self, message_id: int) -> dict | None:
        with self._c.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT id, message_timestamp, source_contact_id, dest_contact_id, "
                "message_type, body, is_outbound, send_state, approval_ref "
                "FROM signal.messages WHERE id = %s",
                (message_id,),
            )
            row = cur.fetchone()
            return dict(row) if row else None

    # -- the SPLIT send surface (D3) ---------------------------------------
    def draft_message(self, *, recipient: str, body: str,
                      self_uuid: str | None = None,
                      self_number: str | None = None,
                      approval_ref: str | None = None,
                      provisional_timestamp: int | None = None) -> dict:
        """Compose and STORE an outbound draft. **Transmits nothing.**

        The draft is persisted immediately with `send_state='pending'` so it can
        never be silently lost, and with a NEGATIVE provisional timestamp — a real
        server timestamp is positive epoch-ms, so the provisional value can never
        be mistaken for one, and `send_approved()` overwrites it with the SERVER's
        (🔧 #4).
        """
        if not (self_uuid or self_number):
            raise ValueError("draft_message needs the sending account's uuid or number")
        source_id = self.upsert_contact(
            signal_uuid=self_uuid, phone_number=self_number, display_name="me",
        )
        dest_id = self.upsert_contact(phone_number=recipient) \
            if not _looks_like_uuid(recipient) else self.upsert_contact(signal_uuid=recipient)
        ts = provisional_timestamp if provisional_timestamp is not None \
            else -int(time.time() * 1000)
        if ts >= 0:
            raise ValueError(
                "a draft's provisional timestamp must be NEGATIVE so it cannot "
                "collide with a server-assigned timestamp (🔧 #4)"
            )
        with self._c.cursor() as cur:
            cur.execute(
                """
                INSERT INTO signal.messages
                    (message_timestamp, source_contact_id, dest_contact_id,
                     message_type, body, is_outbound, send_state, approval_ref)
                VALUES (%s, %s, %s, 'draft', %s, true, %s, %s)
                RETURNING id
                """,
                (ts, source_id, dest_id, body, STATE_PENDING, approval_ref),
            )
            draft_id = _returned_id(cur, "draft_message")
        self._c.commit()
        return {
            "id": draft_id, "recipient": recipient, "body": body,
            "send_state": STATE_PENDING, "message_timestamp": ts,
            "source_contact_id": source_id, "dest_contact_id": dest_id,
            "approval_ref": approval_ref,
        }

    def approve_draft(self, draft_id: int, *, approval_ref: str) -> dict:
        """Record Zach's clawgate approval for a pending draft.

        Only a `pending` draft may be approved; approving anything else raises
        `SendGateError` so a `sent` draft cannot be re-armed.

        🔴 HONEST LIMIT — READ THIS BEFORE TRUSTING D3. Nothing *inside this
        process* can prove that a human, rather than the agent that wrote the
        draft, called this method. What is enforced here is a speed bump, not a
        proof: `SIGNAL_APPROVAL_TOKEN` must be present in the environment, and it
        is deliberately NOT in the consumer Deployment's env or in any agent's —
        it lives in the operator's shell. An agent that can read the operator's
        environment has already won, and this does not pretend otherwise.

        The part that IS structural is narrower and worth stating exactly:
        composing and transmitting are separate calls with durable state in
        between, so a send is always preceded by a recorded approval decision
        that a human can audit after the fact.
        """
        if not os.environ.get(APPROVAL_TOKEN_ENV):
            raise SendGateError(
                f"approval refused: {APPROVAL_TOKEN_ENV} is not set. Approval is "
                f"the OPERATOR's step — it is deliberately unavailable to the "
                f"consumer Deployment and to drafting agents (D3 approval gate)"
            )
        if not (approval_ref or "").strip():
            raise SendGateError(
                "approval refused: an empty approval_ref records nothing auditable "
                "(D3 approval gate)")
        row = self._draft_or_raise(draft_id)
        if row["send_state"] != STATE_PENDING:
            raise SendGateError(
                f"draft {draft_id!r} has send_state={row['send_state']!r}; only "
                f"{STATE_PENDING!r} drafts may be approved (D3 approval gate)"
            )
        with self._c.cursor() as cur:
            cur.execute(
                "UPDATE signal.messages SET send_state = %s, approval_ref = %s "
                "WHERE id = %s",
                (STATE_APPROVED, approval_ref, draft_id),
            )
        self._c.commit()
        return self._draft_or_raise(draft_id)

    def _draft_or_raise(self, draft_id: int) -> dict:
        """`get_draft`, but a missing row is the gate's own error, not a `None`.

        Both callers below treat "no such draft" as a refusal, and both then
        SUBSCRIPT the result — so returning `None` here would surface a real
        refusal case as `TypeError: 'NoneType' object is not subscriptable`.
        """
        row = self.get_draft(draft_id)
        if row is None:
            raise SendGateError(f"draft {draft_id!r} does not exist (D3 approval gate)")
        return row

    def get_draft(self, draft_id: int) -> dict | None:
        with self._c.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT m.id, m.message_timestamp, m.body, m.send_state,
                       m.approval_ref, m.source_contact_id, m.dest_contact_id,
                       CASE WHEN d.is_placeholder THEN d.phone_number
                            ELSE COALESCE(d.signal_uuid, d.phone_number)
                       END AS recipient
                FROM signal.messages m
                LEFT JOIN signal.contacts d ON d.id = m.dest_contact_id
                WHERE m.id = %s AND m.is_outbound
                """,
                (draft_id,),
            )
            row = cur.fetchone()
            return dict(row) if row else None

    def list_drafts(self, state: str | None = None) -> list:
        sql = ("SELECT id, message_timestamp, body, send_state, approval_ref "
               "FROM signal.messages WHERE is_outbound AND send_state IS NOT NULL")
        params: tuple = ()
        if state is not None:
            sql += " AND send_state = %s"
            params = (state,)
        sql += " ORDER BY id"
        with self._c.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            return [dict(r) for r in cur.fetchall()]

    def send_approved(self, draft_id: int, *, transmit=None) -> dict:
        """Transmit an APPROVED draft. The ONLY path from this module to the API.

        🔴 The gate: `_mint_send_authorization()` refuses to produce the
        capability unless the stored `send_state` is `approved`, and
        `consumer.transmit_approved()` refuses to send without one. There is no
        argument to this function that skips the check, and no other function in
        `scripts/signal/` posts to the send endpoint.

        🔧 #4: the SERVER-assigned timestamp returned by the API is written back
        onto the draft row, so the device-sync echo of this very message collides
        with `unique_message` instead of inserting a duplicate.
        """
        draft = self._draft_or_raise(draft_id)
        # 🔴 EVERY precondition that can REFUSE is resolved BEFORE the claim, so a
        # refusal leaves the draft `approved` and re-sendable. `account_number()`
        # used to be an ARGUMENT to `transmit(...)`, which evaluates after the
        # claim has committed: a draft whose sender carried no phone number
        # (`draft_message(self_uuid=…)`, a supported signature) ended up
        # `sending` with nothing transmitted — stranded, and unsendable forever.
        number = self.account_number(draft_id)
        auth = _mint_send_authorization(draft)
        if transmit is None:
            from consumer import transmit_approved as transmit  # local import: no cycle

        # 🔴 CLAIM THE DRAFT BEFORE THE POST, and COMMIT that claim. Everything
        # after the POST can fail — an odd response shape, a dropped connection,
        # a pod kill between the reply and the UPDATE. If the row were still
        # `approved` at that moment the gate would happily mint a fresh
        # capability and SEND THE SAME TEXT AGAIN. `sending` cannot be minted
        # from, so the worst case is a draft a human must reconcile, never a
        # duplicate message. `send_attempts` records that it was tried.
        self._claim_for_sending(draft_id)

        result = transmit(auth, recipient=draft["recipient"], body=draft["body"],
                          number=number)
        # The per-recipient errors are read BEFORE the timestamp: a response that
        # carries both an error and an unusable timestamp must report the ERROR,
        # which says what went wrong, not a timestamp complaint that hides it.
        errors = (result or {}).get("errors")
        if errors:
            raise RuntimeError(
                f"the Signal API reported per-recipient errors for draft "
                f"{draft_id!r}: {errors!r}; the draft stays in {STATE_SENDING!r} "
                f"for manual reconciliation — see `reconcile`"
            )
        server_ts = _server_timestamp(result)
        # The terminal update carries the SAME state predicate as the claim. This
        # sender owns the row only while it is still `sending`; if an operator
        # reconciled it in the meantime, stamping over their decision silently is
        # the wrong answer — the sender is the one that should be told.
        self._transition(
            draft_id,
            "UPDATE signal.messages SET message_timestamp = %s, send_state = %s, "
            "message_type = 'message' WHERE id = %s AND send_state = %s",
            (server_ts, STATE_SENT, draft_id, STATE_SENDING),
            expected=STATE_SENDING,
            what="complete the send",
        )
        return self._draft_or_raise(draft_id)

    def _transition(self, draft_id: int, sql: str, params: tuple, *,
                    expected: str, what: str) -> None:
        """Run a guarded `send_state` transition and REFUSE if it did not apply.

        🔴 Every write that moves `send_state` names the state it expects and
        reads the rowcount, so a caller that lost the row is told instead of
        overwriting whatever the winner decided. Without this an in-flight sender
        completing after an operator reconciled would silently stamp its own
        timestamp over the operator's, and a `--not-sent` reconcile landing
        mid-flight could be re-approved into a SECOND transmit of the same body.
        `_claim_for_sending`'s promise of "never a duplicate message" depends on
        every one of these, not just on the claim.
        """
        with self._c.cursor() as cur:
            cur.execute(sql, params)
            changed = cur.rowcount
        self._c.commit()
        if changed != 1:
            current = self.get_draft(draft_id)
            raise SendGateError(
                f"could not {what} for draft {draft_id!r}: it is no longer "
                f"{expected!r} (now "
                f"{(current or {}).get('send_state')!r}) — someone else moved it "
                f"first. D3 approval gate."
            )

    def _claim_for_sending(self, draft_id: int) -> None:
        """ATOMICALLY move an approved draft to `sending`, and COMMIT the claim.

        🔴 THE STATE PREDICATE IS THE LOCK. Without `AND send_state = 'approved'`
        plus the rowcount check, two concurrent `send_approved()` calls both read
        `approved`, both mint a capability and both transmit — reproduced, two
        sends of one draft. `_ISSUED_NONCES` cannot help: it is per-process, so
        two pods or two shells share nothing. The database row is the only thing
        both callers can contend on, so the transition has to happen there, in
        one statement, with the loser told it lost.
        """
        with self._c.cursor() as cur:
            cur.execute(
                "UPDATE signal.messages SET send_state = %s, "
                "send_attempts = COALESCE(send_attempts, 0) + 1 "
                "WHERE id = %s AND send_state = %s",
                (STATE_SENDING, draft_id, STATE_APPROVED),
            )
            claimed = cur.rowcount
        self._c.commit()
        if claimed != 1:
            raise SendGateError(
                f"draft {draft_id!r} could not be claimed for sending — it is no "
                f"longer {STATE_APPROVED!r} (another sender claimed it first, or "
                f"it was already sent). D3 approval gate."
            )

    def reconcile_send(self, draft_id: int, *, outcome: str,
                       server_timestamp: int | str | None = None,
                       note: str | None = None) -> dict:
        """Resolve a draft stranded in `sending`. The OPERATOR's call, after looking.

        🔴 WHY THIS EXISTS. `sending` is deliberately inert — nothing can mint a
        capability from it, which is what stops a crash mid-send from resending.
        But inert with no way out is a dead end: `approve_draft` refuses (not
        `pending`) and `send_approved` refuses (not `approved`), so a draft that
        strands is unsendable forever and the documented "reconcile by hand" was
        a listing command and nothing else.

        The operator checks Signal and says which happened:

          `sent`     — it did go out. Record the SERVER timestamp read from the
                       conversation, so the sync echo still dedupes (🔧 #4).
          `not-sent` — it did not. Returns to `pending`, so it needs a FRESH
                       approval before it can be sent: the human stays in the
                       loop rather than a retry slipping through on the strength
                       of the old one.

        Gated by the same operator token as `approve_draft`, and refuses any
        draft that is not `sending` — this is not a general state-editing tool.
        """
        if not os.environ.get(APPROVAL_TOKEN_ENV):
            raise SendGateError(
                f"reconcile refused: {APPROVAL_TOKEN_ENV} is not set. Reconciling "
                f"a stranded send is the OPERATOR's step (D3 approval gate)"
            )
        if outcome not in (RECONCILE_SENT, RECONCILE_NOT_SENT):
            raise ValueError(
                f"outcome must be {RECONCILE_SENT!r} or {RECONCILE_NOT_SENT!r}, "
                f"not {outcome!r}"
            )
        row = self._draft_or_raise(draft_id)
        if row["send_state"] != STATE_SENDING:
            raise SendGateError(
                f"draft {draft_id!r} has send_state={row['send_state']!r}; only "
                f"{STATE_SENDING!r} drafts are reconciled (D3 approval gate)"
            )
        if outcome == RECONCILE_SENT:
            server_ts = _server_timestamp({"timestamp": server_timestamp})
            self._transition(
                draft_id,
                "UPDATE signal.messages SET message_timestamp = %s, "
                "send_state = %s, message_type = 'message', "
                "approval_ref = COALESCE(%s, approval_ref) "
                "WHERE id = %s AND send_state = %s",
                (server_ts, STATE_SENT, note, draft_id, STATE_SENDING),
                expected=STATE_SENDING,
                what="reconcile as sent",
            )
        else:
            # COALESCE in BOTH branches. A bare `approval_ref = %s` here erased
            # the recorded approval whenever the operator passed no `--note` —
            # deleting the audit record of the very approval the attempt rode on,
            # which is the thing `approve_draft`'s docstring stakes D3 on. A note
            # ADDS to the record; it never replaces it.
            self._transition(
                draft_id,
                "UPDATE signal.messages SET send_state = %s, "
                "approval_ref = COALESCE(%s, approval_ref) "
                "WHERE id = %s AND send_state = %s",
                (STATE_PENDING, note, draft_id, STATE_SENDING),
                expected=STATE_SENDING,
                what="reconcile as not-sent",
            )
        return self._draft_or_raise(draft_id)

    def account_number(self, draft_id: int) -> str:
        """The SENDING account's phone number for a draft — required by `/v2/send`."""
        with self._c.cursor() as cur:
            cur.execute(
                "SELECT c.phone_number FROM signal.messages m "
                "JOIN signal.contacts c ON c.id = m.source_contact_id "
                "WHERE m.id = %s",
                (draft_id,),
            )
            row = cur.fetchone()
        number = row[0] if row else None
        if not number:
            raise SendGateError(
                f"draft {draft_id!r} has no sending phone number on its contact row; "
                f"`/v2/send` requires `number` and would 400 (D3 approval gate)"
            )
        return number

    # -- retention ---------------------------------------------------------
    def apply_remote_delete(self, rd: dict) -> int:
        """Honour a sender's remote delete: TOMBSTONE the target, keep no text.

        Storing a `remoteDelete` as an ordinary (empty-bodied) message — which is
        what happens if it is not recognised — leaves a ghost row AND leaves the
        retracted text sitting in the row it was meant to retract. Returns the
        number of rows tombstoned (0 when we never received the target).
        """
        author_id = self.upsert_contact(
            signal_uuid=rd.get("target_author_uuid"),
            phone_number=rd.get("target_author_number"),
        )
        with self._c.cursor() as cur:
            cur.execute(
                "UPDATE signal.messages SET body = NULL, raw_envelope = NULL, "
                "message_type = %s WHERE source_contact_id = %s "
                "AND message_timestamp = %s",
                (TYPE_DELETED, author_id, rd["target_sent_timestamp"]),
            )
            deleted = cur.rowcount
        with self._c.cursor() as cur:
            cur.execute(
                "DELETE FROM signal.attachments WHERE message_id IN "
                "(SELECT id FROM signal.messages WHERE source_contact_id = %s "
                " AND message_timestamp = %s)",
                (author_id, rd["target_sent_timestamp"]),
            )
        return deleted

    def commit(self) -> None:
        self._c.commit()


def _looks_like_uuid(value: str) -> bool:
    try:
        uuid.UUID(str(value))
        return True
    except (ValueError, AttributeError, TypeError):
        return False
