#!/usr/bin/env python3
"""Signal consumer — receive stream → parsed envelope → Postgres (+ MinIO attachments).

Runs as a long-lived in-cluster Deployment next to `signal-cli-rest-api`
(json-rpc mode). It consumes that server's per-account receive endpoint (see the
route-table note below), turns each frame into a structured event, and stores it
idempotently through `_signal_db.SignalDB`.

WHAT IT MUST SURVIVE (each has a named test in tests/test_consumer_resilience.py)
--------------------------------------------------------------------------------
* a disconnect/reconnect that REDELIVERS messages — dedupe makes replay a
  no-op, so nothing is lost and nothing is duplicated;
* a malformed frame — skipped and counted, never fatal;
* Postgres briefly unavailable — retried with backoff, the event is not dropped;
* a FAILED WRITE poisoning the connection — `autocommit=False` means one failed
  statement aborts the transaction and every later one raises until a rollback,
  so recovery is part of the retry, not an afterthought;
* an attachment fetch failing — the MESSAGE write still stands (attachment
  bytes are fetched AFTER the row is committed, never inside its transaction).

THE SEND PATH (D3, proposal §7)
-------------------------------
`transmit_approved()` is the ONLY function in `scripts/signal/` that posts to the
Signal send endpoint, and it refuses to run without a `SendAuthorization`
capability that `_signal_db._mint_send_authorization()` mints only for a draft
whose stored `send_state` is `approved`. The un-approved path has no code route
here — see tests/test_approval_gate.py, which tries to take it.

Every third-party call (HTTP, Postgres, MinIO) is injectable, which is what makes
the whole suite hermetic.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import psycopg2  # noqa: E402  (real class objects for the retry classifier)

import _signal_db  # noqa: E402
from _signal_db import (  # noqa: E402
    RECONCILE_NOT_SENT,
    RECONCILE_SENT,
    STATE_APPROVED,
    STATE_PENDING,
    STATE_SENDING,
    STATE_SENT,
    SendGateError,
    SignalDB,
    spend_authorization,
)

# --------------------------------------------------------------------------- #
# THE SERVER WE TARGET IS bbernhard/signal-cli-rest-api. The three routes THIS
# MODULE TARGETS were read from upstream `src/main.go` (2026-08-16) rather than
# recalled (the server registers many more; these are the ones we speak):
#
#     v1.GET("/receive/:number")        <- ingest. See below.
#     v1.GET("/attachments/:attachment")
#     v2.POST("/send")
#
# There is NO `/api/v1/events` and no SSE endpoint anywhere in that router — that
# path belongs to AsamK's NATIVE `signal-cli --http` daemon, a different server.
# An earlier revision of this module mixed the two: event-stream ingest from the
# native daemon, send/attachments from bbernhard. Against the server we actually deploy
# that is a 404 on every connect, which `run()`'s reconnect handler turns into a
# silent infinite loop with zero rows ingested.
#
# In `json-rpc` mode (the mode we deploy) `GET /v1/receive/{number}` UPGRADES TO A
# WEBSOCKET and writes one JSON TEXT FRAME per message — upstream
# `src/api/api.go`: `connectionUpgrader.Upgrade(...)` then
# `ws.WriteMessage(websocket.TextMessage, []byte(data))`, where `data` is the
# signal-cli JSON-RPC params object `{"account": …, "envelope": {…}}`.
# (In non-json-rpc mode the same path is a one-shot GET returning a JSON array.
# We do not deploy that mode and do not ship a factory for it — see the note by
# `ws_stream_factory`.)
# --------------------------------------------------------------------------- #
API_URL = os.environ.get("SIGNAL_API_URL", "http://signal-api.signal.svc:8080")
ACCOUNT = os.environ.get("SIGNAL_ACCOUNT", "")
RECEIVE_PATH = "/v1/receive"
ATTACHMENT_PATH = "/v1/attachments"
SEND_PATH = "/v2/send"

# --------------------------------------------------------------------------- #
# The event kinds this consumer emits. tests/test_skill_doc.py DERIVES this list
# from the source text below and fails if SKILL.md does not document one — a
# hand-written ledger could not catch the thing it exists to catch.
# --------------------------------------------------------------------------- #
KIND_MESSAGE = "message"
KIND_GROUP_MESSAGE = "group_message"
KIND_REACTION = "reaction"
KIND_EDIT = "edit"
KIND_SYNC_OUTBOUND = "sync_outbound"
KIND_RECEIPT_DELIVERY = "receipt_delivery"
KIND_RECEIPT_READ = "receipt_read"
KIND_TYPING = "typing"
KIND_REMOTE_DELETE = "remote_delete"
KIND_UNKNOWN = "unknown"

# Counters the daemon reports. Also derived + doc-checked by test_skill_doc.py.
STAT_STORED = "stored"
STAT_IGNORED = "ignored"
STAT_MALFORMED = "malformed"
STAT_RECONNECTS = "reconnects"
STAT_DB_RETRIES = "db_retries"
STAT_DB_RECOVERIES = "db_recoveries"
STAT_DB_RECOVERY_FAILURES = "db_recovery_failures"
STAT_ATTACHMENT_FAILURES = "attachment_failures"

# Kinds that are stored as rows; everything else is observed and counted only.
STORED_KINDS = (KIND_MESSAGE, KIND_GROUP_MESSAGE, KIND_EDIT, KIND_SYNC_OUTBOUND)

# Transient Postgres faults worth retrying. NOT a bare `Exception`: a programming
# error must fail loudly rather than be retried three times and swallowed.
#
# `InFailedSqlTransaction` (SQLSTATE 25P02) is in the list because it is what
# EVERY statement raises after one has failed inside an `autocommit=False`
# transaction. It is genuinely transient — a `rollback()` clears it — and
# `_recover()` issues exactly that between attempts. Left out, the second event
# after any error escapes into the reconnect handler and the pod becomes a zombie
# that logs "reconnecting" forever and stores nothing.
TRANSIENT_DB_ERRORS = (
    psycopg2.OperationalError,
    psycopg2.InterfaceError,
    psycopg2.errors.InFailedSqlTransaction,
)


@dataclass
class ParsedEvent:
    """One received envelope, normalised. `kind` is one of the KIND_* constants."""

    kind: str
    message: dict | None = None
    reaction: dict | None = None
    receipt: dict | None = None
    remote_delete: dict | None = None
    raw_envelope: str | None = None
    notes: list[str] = field(default_factory=list)


class MalformedEvent(ValueError):
    """The received frame was not a usable envelope. Skipped, counted, never fatal."""


# --------------------------------------------------------------------------- #
# Envelope parsing — pure, no I/O, so the whole fixture corpus is a unit test.
# --------------------------------------------------------------------------- #
def _decode_group_id(raw) -> bytes | None:
    """signal-cli hands group ids back base64-encoded; the column is BYTEA."""
    if raw in (None, ""):
        return None
    if isinstance(raw, bytes):
        return raw
    try:
        return base64.b64decode(raw)
    except Exception:
        return str(raw).encode()


def _attachments(data: dict) -> list[dict]:
    out = []
    for a in data.get("attachments") or []:
        out.append({
            "id": a.get("id"),
            "content_type": a.get("contentType"),
            "filename": a.get("filename"),
            "size": a.get("size"),
            "caption": a.get("caption"),
            "is_voice_note": bool(a.get("voiceNote")),
        })
    return out


def _base_message(env: dict, data: dict, *, timestamp: int) -> dict:
    group = data.get("groupInfo") or {}
    quote = data.get("quote") or {}
    return {
        "message_timestamp": timestamp,
        "server_received_at": env.get("serverReceivedTimestamp"),
        "server_delivered_at": env.get("serverDeliveredTimestamp"),
        "source_uuid": env.get("sourceUuid"),
        "source_number": env.get("sourceNumber") or env.get("source"),
        "source_name": env.get("sourceName"),
        "message_type": KIND_MESSAGE,
        "body": data.get("message"),
        "expires_in_seconds": data.get("expiresInSeconds"),
        "view_once": bool(data.get("viewOnce")),
        "edit_target_timestamp": None,
        "group_id": _decode_group_id(group.get("groupId")),
        "group_name": group.get("name"),
        "quote_timestamp": quote.get("id"),
        "quote_author": quote.get("authorUuid") or quote.get("author"),
        "attachments": _attachments(data),
        "is_outbound": False,
    }


def parse_envelope(envelope: dict) -> ParsedEvent:
    """Normalise one signal-cli envelope into a `ParsedEvent`.

    Raises `MalformedEvent` when the payload is not an envelope at all. An
    envelope of a shape we do not handle is NOT an error: it comes back as
    `KIND_UNKNOWN` so it is counted and visible rather than silently dropped.
    """
    if not isinstance(envelope, dict):
        raise MalformedEvent(f"envelope is {type(envelope).__name__}, not an object")
    if "timestamp" not in envelope and not any(
        k in envelope for k in ("dataMessage", "syncMessage", "receiptMessage",
                                "typingMessage", "editMessage")
    ):
        raise MalformedEvent("envelope has neither a timestamp nor any known sub-message")

    raw = json.dumps(envelope, sort_keys=True)
    env_ts = envelope.get("timestamp")

    # -- sync (an outbound message echoed back from another linked device) ----
    sync = envelope.get("syncMessage") or {}
    sent = sync.get("sentMessage")
    if sent:
        # 🔴 A RETRACTION MADE ON ZACH'S OWN PHONE arrives HERE, wrapped in
        # `syncMessage.sentMessage`, not in the inbound `dataMessage` branch
        # below — and this branch runs first. Handled only inbound, both defects
        # the remote-delete branch exists to prevent survived in the own-device
        # direction: a ghost row, and the retracted text left in place.
        sync_remote = sent.get("remoteDelete")
        if sync_remote is not None:
            target_ts = sync_remote.get("targetSentTimestamp")
            if target_ts is None:
                raise MalformedEvent("sync remoteDelete has no targetSentTimestamp")
            return ParsedEvent(
                kind=KIND_REMOTE_DELETE,
                remote_delete={
                    # The retracted message is the ACCOUNT's own, so the target
                    # author is this envelope's source — the linked device.
                    "target_author_uuid": envelope.get("sourceUuid"),
                    "target_author_number": (envelope.get("sourceNumber")
                                             or envelope.get("source")),
                    "target_sent_timestamp": target_ts,
                },
                raw_envelope=raw,
            )
        ts = sent.get("timestamp") or env_ts
        if ts is None:
            raise MalformedEvent("sync sentMessage has no timestamp")
        msg = _base_message(envelope, sent, timestamp=ts)
        msg["message_type"] = KIND_SYNC_OUTBOUND
        msg["is_outbound"] = True
        msg["dest_uuid"] = sent.get("destinationUuid")
        msg["dest_number"] = sent.get("destination")
        msg["raw_envelope"] = raw
        return ParsedEvent(kind=KIND_SYNC_OUTBOUND, message=msg, raw_envelope=raw)

    # -- edit -----------------------------------------------------------------
    edit = envelope.get("editMessage") or {}
    if edit:
        data = edit.get("dataMessage") or {}
        ts = data.get("timestamp") or env_ts
        if ts is None:
            raise MalformedEvent("editMessage has no timestamp")
        msg = _base_message(envelope, data, timestamp=ts)
        msg["message_type"] = KIND_EDIT
        msg["edit_target_timestamp"] = edit.get("targetSentTimestamp")
        msg["raw_envelope"] = raw
        return ParsedEvent(kind=KIND_EDIT, message=msg, raw_envelope=raw)

    # -- data message (DM / group / reaction) ---------------------------------
    data = envelope.get("dataMessage") or {}
    if data:
        # A remote delete is the SENDER RETRACTING a message. Storing it as a
        # message with an empty body (what falling through to the data-message
        # path does) leaves a ghost row AND keeps the retracted text, which is
        # the opposite of what was asked for.
        remote = data.get("remoteDelete")
        if remote is not None:
            # `is not None`, not truthiness: an EMPTY remoteDelete is malformed
            # and must be reported as such, not silently fall through and get
            # stored as an ordinary empty message.
            target_ts = remote.get("targetSentTimestamp")
            if target_ts is None:
                raise MalformedEvent("remoteDelete has no targetSentTimestamp")
            return ParsedEvent(
                kind=KIND_REMOTE_DELETE,
                remote_delete={
                    "target_author_uuid": envelope.get("sourceUuid"),
                    "target_author_number": (envelope.get("sourceNumber")
                                             or envelope.get("source")),
                    "target_sent_timestamp": target_ts,
                },
                raw_envelope=raw,
            )

        reaction = data.get("reaction")
        if reaction:
            rx = {
                "source_uuid": envelope.get("sourceUuid"),
                "source_number": envelope.get("sourceNumber") or envelope.get("source"),
                "target_author_uuid": reaction.get("targetAuthorUuid"),
                "target_author_number": reaction.get("targetAuthor"),
                "target_sent_timestamp": reaction.get("targetSentTimestamp"),
                "emoji": reaction.get("emoji"),
                "is_remove": bool(reaction.get("isRemove")),
            }
            if rx["target_sent_timestamp"] is None:
                raise MalformedEvent("reaction has no targetSentTimestamp")
            return ParsedEvent(kind=KIND_REACTION, reaction=rx, raw_envelope=raw)

        ts = data.get("timestamp") or env_ts
        if ts is None:
            raise MalformedEvent("dataMessage has no timestamp")
        msg = _base_message(envelope, data, timestamp=ts)
        msg["raw_envelope"] = raw
        kind = KIND_GROUP_MESSAGE if msg["group_id"] else KIND_MESSAGE
        msg["message_type"] = kind
        return ParsedEvent(kind=kind, message=msg, raw_envelope=raw)

    # -- receipts / typing ----------------------------------------------------
    receipt = envelope.get("receiptMessage") or {}
    if receipt:
        kind = KIND_RECEIPT_DELIVERY if receipt.get("isDelivery") else KIND_RECEIPT_READ
        return ParsedEvent(
            kind=kind,
            receipt={
                "when": receipt.get("when"),
                "timestamps": list(receipt.get("timestamps") or []),
                "source_uuid": envelope.get("sourceUuid"),
            },
            raw_envelope=raw,
        )

    typing = envelope.get("typingMessage") or {}
    if typing:
        return ParsedEvent(
            kind=KIND_TYPING,
            receipt={"action": typing.get("action"),
                     "source_uuid": envelope.get("sourceUuid")},
            raw_envelope=raw,
        )

    # Handled shape, unknown content: visible, counted, never dropped silently.
    return ParsedEvent(kind=KIND_UNKNOWN, raw_envelope=raw,
                       notes=[f"unhandled envelope keys: {sorted(envelope)}"])


def parse_receive_frame(payload) -> dict:
    """One received frame → the envelope inside it.

    Accepts, deliberately, the three shapes this endpoint can hand back:
      * bbernhard's json-rpc websocket frame — `{"account": …, "envelope": {…}}`;
      * a signal-cli JSON-RPC notification — `{"method":"receive","params":{…}}`;
      * an already-decoded dict, which `iter_frames` passes straight through.
    """
    if isinstance(payload, (bytes, bytearray)):
        payload = payload.decode("utf-8", "replace")
    if isinstance(payload, str):
        try:
            obj = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise MalformedEvent(f"frame is not JSON: {exc}") from exc
    else:
        obj = payload
    if not isinstance(obj, dict):
        raise MalformedEvent(f"frame is {type(obj).__name__}, not an object")
    if obj.get("method") not in (None, "receive"):
        raise MalformedEvent(f"unexpected JSON-RPC method {obj.get('method')!r}")
    nested = obj.get("params")
    params = nested if isinstance(nested, dict) else obj
    env = params.get("envelope")
    if env is None:
        raise MalformedEvent("frame carries no `envelope`")
    return env


def iter_frames(source) -> Iterator[object]:
    """Normalise whatever the receive transport yields into one item per message.

    The websocket yields one text frame per message. Bytes are decoded, blank
    keepalive frames are dropped, and anything already decoded passes through, so
    `handle_payload` downstream only ever sees "one message". Junk is passed
    ALONG rather than swallowed here — counting it is `handle_payload`'s job, and
    a frame that vanished silently at this layer would be invisible to every
    counter.
    """
    for item in source:
        if isinstance(item, (bytes, bytearray)):
            item = item.decode("utf-8", "replace")
        if isinstance(item, str):
            stripped = item.strip()
            if not stripped:
                continue
            yield stripped
            continue
        yield item


# --------------------------------------------------------------------------- #
# The daemon
# --------------------------------------------------------------------------- #
class SignalConsumer:
    """Receive stream → store. Every external call is an injectable seam."""

    def __init__(self, db, *, stream_factory=None, fetch_attachment=None,
                 minio=None, sleep=time.sleep, db_retries: int = 3,
                 backoff: float = 0.5):
        self.db = db
        self._stream_factory = stream_factory
        self._fetch_attachment = fetch_attachment
        self._minio = minio
        self._sleep = sleep
        self._db_retries = db_retries
        self._backoff = backoff
        self.stats = {
            STAT_STORED: 0,
            STAT_IGNORED: 0,
            STAT_MALFORMED: 0,
            STAT_RECONNECTS: 0,
            STAT_DB_RETRIES: 0,
            STAT_DB_RECOVERIES: 0,
            STAT_DB_RECOVERY_FAILURES: 0,
            STAT_ATTACHMENT_FAILURES: 0,
        }

    # -- one event ---------------------------------------------------------
    def handle_payload(self, payload) -> str:
        """Parse + store one receive frame. Returns the resulting kind, or
        `STAT_MALFORMED` when the frame was skipped."""
        try:
            envelope = parse_receive_frame(payload)
            event = parse_envelope(envelope)
        except MalformedEvent:
            self.stats[STAT_MALFORMED] += 1
            return STAT_MALFORMED
        return self.store(event)

    def store(self, event: ParsedEvent) -> str:
        if event.kind == KIND_REMOTE_DELETE and event.remote_delete is not None:
            self._with_db_retry(
                lambda: self._write(self.db.apply_remote_delete,
                                    event.remote_delete))
            self.stats[STAT_STORED] += 1
            return event.kind
        if event.kind in STORED_KINDS and event.message is not None:
            message_id = self._with_db_retry(
                lambda: self._write(self.db.upsert_message, event.message))
            self.stats[STAT_STORED] += 1
            # AFTER the commit, deliberately: an attachment fetch failure must
            # not roll back a message we already have.
            self.download_attachments(message_id, event.message)
            return event.kind
        if event.kind == KIND_REACTION and event.reaction is not None:
            self._with_db_retry(
                lambda: self._write(self.db.upsert_reaction, event.reaction))
            self.stats[STAT_STORED] += 1
            return event.kind
        self.stats[STAT_IGNORED] += 1
        return event.kind

    def _write(self, fn, *args):
        """ONE unit of work: the write AND its commit, together.

        🔴 WHY THEY MUST BE ONE UNIT. Retrying them separately silently DROPS the
        message when the transient fault lands on the COMMIT: recovery rolls back
        — discarding the row already written — and the retried bare `commit()`
        then commits an empty transaction and reports success. Measured: one row
        written, `stored=1`, and `SELECT` returns nothing. That turned a loud
        failure into a silent one, which is the exact class this consumer's
        retry logic exists to remove.

        Retrying the whole unit is safe because every write here is idempotent
        (`ON CONFLICT`), so a replay after a rollback re-creates the same row.
        """
        result = fn(*args)
        self.db.commit()
        return result

    def _with_db_retry(self, fn):
        """Retry one UNIT OF WORK across a transient fault, never dropping the event.

        `fn` must be re-runnable from the top: `_recover()` rolls the aborted
        transaction back between attempts, so anything `fn` had already written
        is gone by the time it runs again. Pass whole units (see `_write`), never
        a bare `commit`.

        `db_retries` counts ATTEMPTS and has a floor of 1: `db_retries=0` used to
        skip the loop entirely and then `raise last` with `last` still None,
        which raises `TypeError: exceptions must derive from BaseException` — a
        misconfiguration surfacing as a nonsense error from inside the retry
        helper. Zero retries now means "call it once, do not retry".
        """
        attempts = max(1, self._db_retries)
        last: BaseException | None = None
        for attempt in range(attempts):
            try:
                return fn()
            except TRANSIENT_DB_ERRORS as exc:
                last = exc
                self.stats[STAT_DB_RETRIES] += 1
                self._recover("transient")
                self._sleep(self._backoff * (2 ** attempt))
            except Exception:
                # 🔴 NOT retried — but the connection still has to be made usable.
                # `autocommit=False` means the FIRST failed statement aborts the
                # transaction, and every later statement then raises
                # `InFailedSqlTransaction`, which is a DIFFERENT error class: it
                # would escape into run()'s reconnect handler and be miscounted
                # as a dropped stream forever, storing nothing. Recover, then let
                # the real error propagate.
                self._recover("fatal")
                raise
        if last is None:  # pragma: no cover - unreachable while attempts >= 1
            raise RuntimeError(
                "_with_db_retry finished its loop without attempting the call")
        raise last

    def _recover(self, why: str) -> str:
        """Roll the aborted transaction back (reconnecting if that is not enough).

        Returns what the DB layer says it did, for the counters and the log.
        """
        try:
            outcome = self.db.recover()
        except Exception as exc:  # noqa: BLE001 — recovery must not mask the cause
            self.stats[STAT_DB_RECOVERY_FAILURES] += 1
            print(f"signal-consumer: DB recovery after a {why} error failed: {exc}",
                  file=sys.stderr)
            return "failed"
        self.stats[STAT_DB_RECOVERIES] += 1
        return outcome

    # -- attachments -------------------------------------------------------
    def download_attachments(self, message_id: int, msg: dict) -> int:
        """Fetch each attachment's bytes → MinIO, stamping the row with the key.

        Isolated per attachment AND from the message write: a failure here is
        counted and logged, never propagated, because the message row is already
        committed and losing it would be the worse outcome.
        """
        stored = 0
        # Bound to locals right after the guard: the DB callback below is a
        # LAMBDA, so `self._minio` would be re-read whenever it runs rather than
        # at the moment the guard held.
        minio, fetch = self._minio, self._fetch_attachment
        if minio is None or fetch is None:
            return stored
        conversation = conversation_key(msg)
        for att in msg.get("attachments") or []:
            try:
                blob = fetch(att["id"])
                key = minio.put_attachment(
                    conversation=conversation,
                    timestamp_ms=msg["message_timestamp"],
                    attachment_id=att["id"],
                    filename=att.get("filename") or att["id"],
                    data=blob,
                    content_type=att.get("content_type") or "application/octet-stream",
                    sidecar={
                        "conversation": conversation,
                        "message_id": message_id,
                        "message_timestamp": msg["message_timestamp"],
                        "content_type": att.get("content_type"),
                        "signal_attachment_id": att["id"],
                    },
                )
                self._with_db_retry(
                    lambda k=key, a=att, b=minio.bucket:
                        self._write(self.db.record_attachment_object,
                                    message_id, a["id"], b, k)
                )
                stored += 1
            except Exception as exc:  # noqa: BLE001 — deliberately broad, see docstring
                self.stats[STAT_ATTACHMENT_FAILURES] += 1
                print(f"signal-consumer: attachment {att.get('id')!r} failed: {exc}",
                      file=sys.stderr)
        return stored

    # -- the stream --------------------------------------------------------
    def run(self, *, max_connections: int | None = None) -> dict:
        """Consume the receive stream, reconnecting on disconnect.

        `max_connections` bounds the loop (the tests pass a small number; the
        Deployment passes None and runs forever).

        🔴 The missing-factory check is OUTSIDE the loop deliberately. Inside, a
        `None` factory raises `TypeError: 'NoneType' object is not callable`,
        which the reconnect handler catches — so a misconfigured consumer would
        spin forever printing "stream ended; reconnecting" instead of saying what
        is wrong. A configuration fault must not be laundered into a retry.
        """
        if self._stream_factory is None:
            raise RuntimeError(
                "SignalConsumer.run() needs a stream_factory; construct it with "
                "stream_factory=consumer.ws_stream_factory(<account>) (or a test seam)")
        connections = 0
        while max_connections is None or connections < max_connections:
            connections += 1
            try:
                for payload in iter_frames(self._stream_factory()):
                    self.handle_payload(payload)
            except Exception as exc:  # noqa: BLE001 — a dropped stream is normal
                self.stats[STAT_RECONNECTS] += 1
                print(f"signal-consumer: stream ended ({exc}); reconnecting",
                      file=sys.stderr)
                self._sleep(self._backoff)
                continue
        return dict(self.stats)


def conversation_key(msg: dict) -> str:
    """Stable per-conversation prefix for MinIO keys."""
    if msg.get("group_id"):
        gid = msg["group_id"]
        digest = base64.urlsafe_b64encode(gid).decode().rstrip("=")[:24]
        return f"group-{digest}"
    ident = msg.get("source_uuid") or msg.get("source_number") or "unknown"
    return str(ident)


# --------------------------------------------------------------------------- #
# The send path — the ONLY route to the Signal send endpoint (D3)
# --------------------------------------------------------------------------- #
def transmit_approved(auth, *, recipient: str, body: str, number: str,
                      poster=None, api_url: str | None = None,
                      timeout: float = 20.0) -> dict:
    """POST an approved draft to the Signal API. Requires a `SendAuthorization`.

    🔴 `spend_authorization()` runs BEFORE anything touches the network, and it
    raises `SendGateError` for a non-capability, a forged look-alike, or a
    capability that has already been spent. There is no keyword that skips it and
    no other send-endpoint call site in `scripts/signal/`.

    `number` is the SENDING account and is REQUIRED by the server: upstream
    `SendV2` rejects an empty one with 400 `"Couldn't process request - please
    provide a valid number"` (read from `src/api/api.go`, not recalled). An
    earlier revision omitted it, so every send would have failed — safely, but
    the whole send path was inert.

    Returns the API's response, from which the caller MUST take the
    server-assigned `timestamp` (🔧 #4 — the sync echo carries that value, and a
    locally generated one would not dedupe). Upstream types that field as a
    STRING (`ds.SendMessageResponse.Timestamp string`), so the caller coerces.
    """
    spend_authorization(auth)
    if not number:
        raise SendGateError(
            "transmit refused: no sending `number` — the server would reject this "
            "with 400 'please provide a valid number' (D3 approval gate)")
    url = (api_url or API_URL).rstrip("/") + SEND_PATH
    payload = {"message": body, "number": number, "recipients": [recipient]}
    if poster is None:  # pragma: no cover - the live path; tests inject a poster
        import requests
        poster = requests.post
    resp = poster(url, json=payload, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def http_attachment_fetcher(api_url: str | None = None, timeout: float = 30.0):
    """Return a `fetch_attachment(id) -> bytes` bound to the live API."""
    base = (api_url or API_URL).rstrip("/")

    def _fetch(attachment_id: str) -> bytes:  # pragma: no cover - live path
        import requests
        resp = requests.get(f"{base}{ATTACHMENT_PATH}/{attachment_id}", timeout=timeout)
        resp.raise_for_status()
        return resp.content

    return _fetch


def receive_url(account: str, *, api_url: str | None = None,
                websocket: bool = False) -> str:
    """The ingest URL for one account: `…/v1/receive/{number}`.

    `websocket=True` swaps the scheme for `ws`/`wss`, which is what json-rpc mode
    needs — the path is the same, the server upgrades the connection.
    """
    if not account:
        raise ValueError(
            "receive_url needs the account number: bbernhard's ingest endpoint is "
            "per-account (`/v1/receive/{number}`), there is no global stream")
    base = (api_url or API_URL).rstrip("/")
    if websocket:
        if base.startswith("https://"):
            base = "wss://" + base[len("https://"):]
        elif base.startswith("http://"):
            base = "ws://" + base[len("http://"):]
    return f"{base}{RECEIVE_PATH}/{account}"


def ws_stream_factory(account: str, api_url: str | None = None,
                      timeout: float = 300.0):
    """Ingest for `json-rpc` mode: a WEBSOCKET on `/v1/receive/{number}`.

    Upstream upgrades the connection and writes one JSON text frame per message.

    🔴 THE IMPORT IS AT BUILD TIME, NOT INSIDE `_open()`. `run()` calls the
    factory once per connect and treats any exception from it as a dropped
    stream, so a lazily-imported missing `websocket-client` became an INFINITE
    silent reconnect loop: `stream ended (No module named 'websocket');
    reconnecting`, forever, zero rows. Measured at 25/25 connects. That is the
    same zombie mode `run()`'s hoisted stream-factory check exists to prevent —
    re-created by a dependency, and defeated by the lazy import. A missing
    dependency is a configuration fault and must fail here, once, loudly.

    The dependency is declared in `scripts/signal/requirements.txt`; the
    consumer image needs it installed. The hermetic suite never reaches this
    function's live path — it injects its own factory.
    """
    url = receive_url(account, api_url=api_url, websocket=True)
    try:
        import websocket  # websocket-client — see requirements.txt
    except ImportError as exc:
        raise RuntimeError(
            "the `websocket-client` package is required for json-rpc ingest "
            f"({url}) and is not installed. It is declared in "
            "scripts/signal/requirements.txt; the consumer image must install "
            "it. Failing here rather than inside the reconnect loop, where a "
            "missing dependency would look like a flapping server forever."
        ) from exc

    def _open():  # pragma: no cover - live path
        conn = websocket.create_connection(url, timeout=timeout)

        def _frames():
            try:
                while True:
                    yield conn.recv()
            finally:
                conn.close()

        return _frames()

    return _open


# NOTE — there is deliberately NO REST-polling factory here. The same path is a
# one-shot GET returning a JSON array in non-json-rpc mode, but we deploy
# json-rpc mode, and `run()` only sleeps in its EXCEPT branch: a factory that
# returns a FINITE iterator makes the loop spin at full speed (measured: 50
# connects, 0 sleeps). An unused function with that hazard in it is worse than no
# function, so it is gone rather than left for someone to reach for.


# --------------------------------------------------------------------------- #
# CLI. tests/test_skill_doc.py derives these names from the source and requires
# each one to appear in claude/skills/signal/SKILL.md.
# --------------------------------------------------------------------------- #
def _fmt_ts(ms) -> str:
    if not ms or int(ms) < 0:
        return "(draft)"
    return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc).strftime(
        "%Y-%m-%d %H:%M")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="consumer.py", description="Signal chat pipeline: consume, query, draft.")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser(
        "run", help="consume /v1/receive/{number} forever (the Deployment)")
    r.add_argument("--account", default=ACCOUNT,
                   help="the account number to receive for (default $SIGNAL_ACCOUNT)")

    q = sub.add_parser("conversations", help="list conversations, newest first")
    q.add_argument("--limit", type=int, default=25)

    s = sub.add_parser("search", help="full-text search over message bodies")
    s.add_argument("query")
    s.add_argument("--limit", type=int, default=25)

    d = sub.add_parser("draft", help="compose an outbound draft (transmits NOTHING)")
    d.add_argument("--to", required=True)
    d.add_argument("--body", required=True)
    d.add_argument("--from-number", default=ACCOUNT,
                   help="the sending account (default $SIGNAL_ACCOUNT)")

    ls = sub.add_parser("drafts", help="list drafts and their send_state")
    ls.add_argument("--state", choices=[STATE_PENDING, STATE_APPROVED,
                                        STATE_SENDING, STATE_SENT])

    a = sub.add_parser("approve", help="record Zach's clawgate approval for a draft")
    a.add_argument("draft_id", type=int)
    a.add_argument("--ref", required=True, help="the clawgate approval reference")

    sd = sub.add_parser("send", help="transmit an APPROVED draft (gated)")
    sd.add_argument("draft_id", type=int)

    rc = sub.add_parser(
        "reconcile",
        help="resolve a draft stranded in `sending` after checking Signal")
    rc.add_argument("draft_id", type=int)
    outcome = rc.add_mutually_exclusive_group(required=True)
    outcome.add_argument("--sent", action="store_true",
                         help="it DID go out; pass --timestamp from the conversation")
    outcome.add_argument("--not-sent", action="store_true",
                         help="it did NOT go out; returns to pending for re-approval")
    rc.add_argument("--timestamp", help="the SERVER timestamp, required with --sent")
    rc.add_argument("--note", help="what you saw, recorded on the row")

    return p


def main(argv=None) -> int:  # pragma: no cover - thin CLI shell over tested units
    args = build_parser().parse_args(argv)
    with SignalDB() as db:
        db.ensure_schema()
        if args.cmd == "run":
            account = args.account or ACCOUNT
            if not account:
                print("run: set SIGNAL_ACCOUNT (or --account) — bbernhard's ingest "
                      "endpoint is per-account", file=sys.stderr)
                return 2
            consumer = SignalConsumer(
                db,
                stream_factory=ws_stream_factory(account),
                fetch_attachment=http_attachment_fetcher(),
                minio=_open_minio(),
            )
            print(json.dumps(consumer.run()))
        elif args.cmd == "conversations":
            for row in db.list_conversations(limit=args.limit):
                print(f"{_fmt_ts(row['last_message_timestamp'])}  "
                      f"{row.get('group_name') or row.get('display_name') or '?'}  "
                      f"({row['message_count']})")
        elif args.cmd == "search":
            for row in db.search(args.query, limit=args.limit):
                print(f"{_fmt_ts(row['message_timestamp'])}  "
                      f"{row.get('display_name') or row.get('phone_number') or '?'}: "
                      f"{(row.get('body') or '')[:120]}")
        elif args.cmd == "draft":
            import clawgate
            draft = db.draft_message(
                recipient=args.to, body=args.body, self_number=args.from_number)
            clawgate.emit_draft_task(
                draft_id=draft["id"], recipient=args.to, body=args.body)
            print(json.dumps(draft))
        elif args.cmd == "drafts":
            print(json.dumps(db.list_drafts(state=args.state), default=str))
        elif args.cmd == "approve":
            print(json.dumps(db.approve_draft(args.draft_id, approval_ref=args.ref),
                             default=str))
        elif args.cmd == "send":
            try:
                print(json.dumps(db.send_approved(args.draft_id), default=str))
            except SendGateError as exc:
                print(f"refused: {exc}", file=sys.stderr)
                return 3
        elif args.cmd == "reconcile":
            # argparse cannot express "--timestamp is required WITH --sent", and
            # `_server_timestamp` raises a plain ValueError — so without this the
            # refusal arrived as a traceback and an exit code nobody scripts on.
            if args.sent and not args.timestamp:
                print("refused: --sent needs --timestamp, the SERVER timestamp "
                      "read from the Signal conversation. Without it the sync "
                      "echo cannot be deduped (🔧 #4), and guessing is worse "
                      "than refusing.", file=sys.stderr)
                return 3
            try:
                print(json.dumps(db.reconcile_send(
                    args.draft_id,
                    outcome=RECONCILE_SENT if args.sent else RECONCILE_NOT_SENT,
                    server_timestamp=args.timestamp, note=args.note), default=str))
            except (SendGateError, ValueError) as exc:
                print(f"refused: {exc}", file=sys.stderr)
                return 3
    return 0


def _open_minio():  # pragma: no cover - live path
    from _minio import MinioSignal
    mc = MinioSignal()
    mc.__enter__()
    return mc


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
