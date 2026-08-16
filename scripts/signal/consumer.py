#!/usr/bin/env python3
"""Signal consumer — SSE stream → parsed envelope → Postgres (+ MinIO attachments).

Runs as a long-lived in-cluster Deployment next to `signal-cli-rest-api`
(json-rpc-native mode). It consumes `GET /api/v1/events`, turns each JSON-RPC
`receive` notification into a structured event, and stores it idempotently
through `_signal_db.SignalDB`.

WHAT IT MUST SURVIVE (each has a named test in tests/test_consumer_resilience.py)
--------------------------------------------------------------------------------
* an SSE disconnect/reconnect that REDELIVERS events — dedupe makes replay a
  no-op, so nothing is lost and nothing is duplicated;
* a malformed event — skipped and counted, never fatal;
* Postgres briefly unavailable — retried with backoff, the event is not dropped;
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
    STATE_APPROVED,
    STATE_PENDING,
    STATE_SENT,
    SendGateError,
    SignalDB,
    spend_authorization,
)

API_URL = os.environ.get("SIGNAL_API_URL", "http://signal-api.signal.svc:8080")
EVENTS_PATH = "/api/v1/events"
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
KIND_UNKNOWN = "unknown"

# Counters the daemon reports. Also derived + doc-checked by test_skill_doc.py.
STAT_STORED = "stored"
STAT_IGNORED = "ignored"
STAT_MALFORMED = "malformed"
STAT_RECONNECTS = "reconnects"
STAT_DB_RETRIES = "db_retries"
STAT_ATTACHMENT_FAILURES = "attachment_failures"

# Kinds that are stored as rows; everything else is observed and counted only.
STORED_KINDS = (KIND_MESSAGE, KIND_GROUP_MESSAGE, KIND_EDIT, KIND_SYNC_OUTBOUND)

# Transient Postgres faults worth retrying. NOT a bare `Exception`: a programming
# error must fail loudly rather than be retried three times and swallowed.
TRANSIENT_DB_ERRORS = (psycopg2.OperationalError, psycopg2.InterfaceError)


@dataclass
class ParsedEvent:
    """One SSE envelope, normalised. `kind` is one of the KIND_* constants."""

    kind: str
    message: dict | None = None
    reaction: dict | None = None
    receipt: dict | None = None
    raw_envelope: str | None = None
    notes: list[str] = field(default_factory=list)


class MalformedEvent(ValueError):
    """The SSE payload was not a usable envelope. Skipped, counted, never fatal."""


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


def parse_sse_payload(payload: str) -> dict:
    """One SSE `data:` payload → the envelope object inside it.

    signal-cli-rest-api wraps envelopes in a JSON-RPC notification
    (`{"method":"receive","params":{"envelope":{…}}}`); a bare `{"envelope":{…}}`
    is also accepted.
    """
    try:
        obj = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise MalformedEvent(f"payload is not JSON: {exc}") from exc
    if not isinstance(obj, dict):
        raise MalformedEvent(f"payload is {type(obj).__name__}, not an object")
    if obj.get("method") not in (None, "receive"):
        raise MalformedEvent(f"unexpected JSON-RPC method {obj.get('method')!r}")
    params = obj.get("params") or obj
    env = params.get("envelope")
    if env is None:
        raise MalformedEvent("payload carries no `envelope`")
    return env


def sse_events(lines) -> Iterator[str]:
    """Yield the `data:` payloads of an SSE byte/text line stream.

    A generator over already-decoded lines, so the tests drive it with a plain
    list and the daemon drives it with `requests`' `iter_lines`.
    """
    for line in lines:
        if isinstance(line, bytes):
            line = line.decode("utf-8", "replace")
        line = line.rstrip("\n")
        if not line or line.startswith(":"):
            continue
        if line.startswith("data:"):
            yield line[len("data:"):].strip()


# --------------------------------------------------------------------------- #
# The daemon
# --------------------------------------------------------------------------- #
class SignalConsumer:
    """SSE → store. Every external call is an injectable seam."""

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
            STAT_ATTACHMENT_FAILURES: 0,
        }

    # -- one event ---------------------------------------------------------
    def handle_payload(self, payload: str) -> str:
        """Parse + store one SSE payload. Returns the resulting kind, or
        `STAT_MALFORMED` when the payload was skipped."""
        try:
            envelope = parse_sse_payload(payload)
            event = parse_envelope(envelope)
        except MalformedEvent:
            self.stats[STAT_MALFORMED] += 1
            return STAT_MALFORMED
        return self.store(event)

    def store(self, event: ParsedEvent) -> str:
        if event.kind in STORED_KINDS and event.message is not None:
            message_id = self._with_db_retry(
                lambda: self.db.upsert_message(event.message)
            )
            self._with_db_retry(self.db.commit)
            self.stats[STAT_STORED] += 1
            # AFTER the commit, deliberately: an attachment fetch failure must
            # not roll back a message we already have.
            self.download_attachments(message_id, event.message)
            return event.kind
        if event.kind == KIND_REACTION and event.reaction is not None:
            self._with_db_retry(lambda: self.db.upsert_reaction(event.reaction))
            self._with_db_retry(self.db.commit)
            self.stats[STAT_STORED] += 1
            return event.kind
        self.stats[STAT_IGNORED] += 1
        return event.kind

    def _with_db_retry(self, fn):
        """Retry a DB call across a transient fault instead of dropping the event.

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
                self._sleep(self._backoff * (2 ** attempt))
        if last is None:  # pragma: no cover - unreachable while attempts >= 1
            raise RuntimeError(
                "_with_db_retry finished its loop without attempting the call")
        raise last

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
                        self.db.record_attachment_object(
                            message_id, a["id"], b, k)
                )
                self._with_db_retry(self.db.commit)
                stored += 1
            except Exception as exc:  # noqa: BLE001 — deliberately broad, see docstring
                self.stats[STAT_ATTACHMENT_FAILURES] += 1
                print(f"signal-consumer: attachment {att.get('id')!r} failed: {exc}",
                      file=sys.stderr)
        return stored

    # -- the stream --------------------------------------------------------
    def run(self, *, max_connections: int | None = None) -> dict:
        """Consume the SSE stream, reconnecting on disconnect.

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
                "stream_factory=consumer.http_stream_factory() (or a test seam)")
        connections = 0
        while max_connections is None or connections < max_connections:
            connections += 1
            try:
                for payload in sse_events(self._stream_factory()):
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
def transmit_approved(auth, *, recipient: str, body: str, poster=None,
                      api_url: str | None = None, timeout: float = 20.0) -> dict:
    """POST an approved draft to the Signal API. Requires a `SendAuthorization`.

    🔴 `spend_authorization()` runs BEFORE anything touches the network, and it
    raises `SendGateError` for a non-capability, a forged look-alike, or a
    capability that has already been spent. There is no keyword that skips it and
    no other send-endpoint call site in `scripts/signal/`.

    Returns the API's response, from which the caller MUST take the
    server-assigned `timestamp` (🔧 #4 — the sync echo carries that value, and a
    locally generated one would not dedupe).
    """
    spend_authorization(auth)
    url = (api_url or API_URL).rstrip("/") + SEND_PATH
    payload = {"message": body, "recipients": [recipient]}
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


def http_stream_factory(api_url: str | None = None, timeout: float = 300.0):
    """Return a `stream_factory() -> line iterator` bound to the live SSE endpoint."""
    base = (api_url or API_URL).rstrip("/")

    def _open():  # pragma: no cover - live path
        import requests
        resp = requests.get(f"{base}{EVENTS_PATH}", stream=True, timeout=timeout)
        resp.raise_for_status()
        return resp.iter_lines()

    return _open


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

    sub.add_parser("run", help="consume the SSE stream forever (the Deployment)")

    q = sub.add_parser("conversations", help="list conversations, newest first")
    q.add_argument("--limit", type=int, default=25)

    s = sub.add_parser("search", help="full-text search over message bodies")
    s.add_argument("query")
    s.add_argument("--limit", type=int, default=25)

    d = sub.add_parser("draft", help="compose an outbound draft (transmits NOTHING)")
    d.add_argument("--to", required=True)
    d.add_argument("--body", required=True)
    d.add_argument("--from-number", default=os.environ.get("SIGNAL_ACCOUNT"))

    ls = sub.add_parser("drafts", help="list drafts and their send_state")
    ls.add_argument("--state", choices=[STATE_PENDING, STATE_APPROVED, STATE_SENT])

    a = sub.add_parser("approve", help="record Zach's clawgate approval for a draft")
    a.add_argument("draft_id", type=int)
    a.add_argument("--ref", required=True, help="the clawgate approval reference")

    sd = sub.add_parser("send", help="transmit an APPROVED draft (gated)")
    sd.add_argument("draft_id", type=int)

    return p


def main(argv=None) -> int:  # pragma: no cover - thin CLI shell over tested units
    args = build_parser().parse_args(argv)
    with SignalDB() as db:
        db.ensure_schema()
        if args.cmd == "run":
            consumer = SignalConsumer(
                db,
                stream_factory=http_stream_factory(),
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
    return 0


def _open_minio():  # pragma: no cover - live path
    from _minio import MinioSignal
    mc = MinioSignal()
    mc.__enter__()
    return mc


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
