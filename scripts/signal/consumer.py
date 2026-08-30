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
import socket
import sys
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
# Aliased: `_base_message()` binds a LOCAL named `quote` (the envelope's reply
# quote), and an unaliased import would read as that in every other function.
from urllib.parse import quote as _urlquote

sys.path.insert(0, str(Path(__file__).resolve().parent))

import psycopg2  # noqa: E402  (real class objects for the retry classifier)

import _mentions  # noqa: E402
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
# 🔴 A READ. `GET /v1/groups/<account>/<group-id>` returns the group's detail,
# including `members[]` — which arrives MIXED: some entries are E.164, some are
# bare UUIDs (measured against the deployed signal-cli-rest-api for the 7-member
# 'Vetr app group': 5 of 7 were uuid-only). Anything joining that list against
# `signal.contacts` must therefore try BOTH columns; see `_mentions._contact_ids`.
GROUPS_PATH = "/v1/groups"

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


def _decode_internal_id(text: str) -> bytes:
    """An OPERATOR-typed base64 group id → bytes, refusing anything ambiguous.

    Deliberately stricter than `_decode_group_id`, which serves a different
    requirement: on the ingest path a group id that will not decode must never
    kill the frame, so it falls back to the raw bytes. Applied to operator input
    that same fallback turns a typo into a mute of some other 32 bytes that
    matches nothing — a filter that reports success and hides no rows.

    So: decode through the ONE decoder, then require that re-encoding reproduces
    the input exactly. The round-trip is what rejects the fallback path and any
    non-canonical spelling, without this function owning a second base64 reader.
    """
    if not isinstance(text, str) or not text.strip():
        raise ValueError("group id is empty")
    s = text.strip().replace("-", "+").replace("_", "/")   # tolerate urlsafe
    raw = _decode_group_id(s)
    if not raw or base64.b64encode(raw).decode() != s:
        raise ValueError(
            f"{text!r} is not a canonical base64 group id. Copy `internal_id` "
            "verbatim from GET /v1/groups/<account> — not `id` (which is the "
            "`group.`-prefixed double encoding) and not the display name.")
    # 🔴 LENGTH IS THE ONLY THING THAT REJECTS A SHORT WORD. The round-trip above
    # is satisfied by any string that is valid base64, and plenty of display
    # names are: `Team` decodes to 3 bytes and `deadbeef` to 6, both round-trip
    # perfectly, and both were ACCEPTED — muting nothing while printing success.
    # GroupV2 ids are 32 bytes and legacy GroupV1 ids 16; nothing else is a
    # group id, so anything else is a typo caught here instead of at 3am.
    if len(raw) not in (16, 32):
        raise ValueError(
            f"{text!r} decodes to {len(raw)} bytes; a Signal group id is 32 "
            "(GroupV2) or 16 (legacy GroupV1). This is almost certainly a "
            "display name or a truncated paste — muting it would hide nothing "
            "and report success.")
    return raw


def _fmt_group_id(raw) -> str:
    """A BYTEA group id back to the base64 an operator can paste."""
    return base64.b64encode(bytes(raw)).decode()


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
        # 🔴 `groupName`, not `name`. Measured on the live store 2026-08-19: 34
        # of 34 stored group envelopes carry a NON-EMPTY `groupInfo.groupName`
        # and NONE carries `groupInfo.name`, so this read `None` every time and
        # `upsert_group` stored `''` for every group that has ever arrived. The
        # consequence was invisible because nothing asserted on a group name —
        # `conversations` just printed the DM peer or `?`, and it took wanting
        # to filter a group BY NAME to notice that no name had ever been stored.
        # Both spellings are read (the field was renamed upstream at some point
        # and an older server may still send `name`); `groupName` wins because
        # that is the one real envelopes carry.
        "group_name": group.get("groupName") or group.get("name"),
        "quote_timestamp": quote.get("id"),
        "quote_author": quote.get("authorUuid") or quote.get("author"),
        "attachments": _attachments(data),
        "is_outbound": False,
    }


def _reaction_from(envelope: dict, reaction, *, where: str) -> dict:
    """Build a reaction row from `reaction`, wherever in the envelope it sat.

    🔴 ONE PLACE, because there are TWO sites — inbound `dataMessage.reaction`
    and own-device `syncMessage.sentMessage.reaction` — and the second was added
    only after live traffic showed outbound reactions being dropped. Open-coding
    the same dict twice is what let the sync site ship missing the guards the
    inbound site had; keeping it open-coded would guarantee the next divergence.

    🔴 EVERY guard here exists because the alternative is NOT a skipped frame but
    a WEDGED CONSUMER. `upsert_reaction` resolves BOTH the target author and the
    reactor through `upsert_contact`, which raises `ValueError` on an
    unidentifiable contact — and `ValueError` is neither `MalformedEvent` nor a
    TRANSIENT_DB_ERROR, so it escapes `store()`, escapes `handle_payload()`
    (which wraps only the PARSE), and lands in `run()`'s reconnect handler,
    which counts it as a dropped stream. Signal then REDELIVERS the same frame
    on reconnect: an unbounded loop that stores nothing and takes every later
    frame on the connection down with it. Raising `MalformedEvent` here instead
    keeps the module's promise — skipped and counted, never fatal.
    """
    if not isinstance(reaction, dict):
        raise MalformedEvent(f"{where} reaction is not an object")
    rx = {
        "source_uuid": envelope.get("sourceUuid"),
        "source_number": envelope.get("sourceNumber") or envelope.get("source"),
        "target_author_uuid": reaction.get("targetAuthorUuid"),
        # signal-cli exposes THREE identity fields and the first version of this
        # read only two: `targetAuthorNumber` appears in real envelopes and was
        # silently ignored, so a reaction carrying only it looked unidentifiable.
        "target_author_number": (reaction.get("targetAuthor")
                                 or reaction.get("targetAuthorNumber")),
        "target_sent_timestamp": reaction.get("targetSentTimestamp"),
        "emoji": reaction.get("emoji"),
        "is_remove": bool(reaction.get("isRemove")),
    }
    if rx["target_sent_timestamp"] is None:
        raise MalformedEvent(f"{where} reaction has no targetSentTimestamp")
    if not (rx["target_author_uuid"] or rx["target_author_number"]):
        raise MalformedEvent(f"{where} reaction has no target author identity")
    if not (rx["source_uuid"] or rx["source_number"]):
        raise MalformedEvent(f"{where} reaction has no reactor identity")
    return rx


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
        # 🔴 A REACTION MADE ON ZACH'S OWN PHONE arrives HERE too, in the same
        # `syncMessage.sentMessage` wrapper and for the same reason as the
        # retraction above: this branch runs BEFORE the inbound `dataMessage`
        # branch. Without this case it fell through to `_base_message()` and hit
        # BOTH defects the remote-delete case exists to prevent — the reaction
        # dropped from signal.reactions, and a bodyless ghost row left in
        # signal.messages (`message` is None on a reaction-only sync).
        #
        # Found in LIVE traffic, not by review: two OTHER members had reacted to
        # the same message, so the reaction COUNT for that target looked right
        # and only the reactor identity showed the account's own was missing.
        sync_reaction = sent.get("reaction")
        if sync_reaction is not None:
            # The reactor is the ACCOUNT ITSELF — this envelope's source is the
            # linked device — while targetAuthor is whoever wrote the message
            # being reacted to.
            rx = _reaction_from(envelope, sync_reaction, where="sync")
            return ParsedEvent(kind=KIND_REACTION, reaction=rx, raw_envelope=raw)

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

        # `is not None`, NOT truthiness — the same predicate as the sync site
        # above, deliberately. Consolidating only the parser BODY left this
        # dispatch divergent: an inbound `reaction: {}` was stored as an ordinary
        # message while the identical sync shape was reported malformed. A
        # malformed frame counted as a successful message is the failure the
        # remoteDelete branch documents; one rule, both sites.
        reaction = data.get("reaction")
        if reaction is not None:
            rx = _reaction_from(envelope, reaction, where="inbound")
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


# --------------------------------------------------------------------------- #
# Liveness — the signal this service did not have
#
# 🔴 WHY THIS EXISTS, measured not theorised. `signal-consumer` serves no HTTP,
# declares no probes, and emitted **0 log lines** across 20h in which it
# successfully ingested 5 messages, 5 contacts, a group and 2 reactions. So a pod
# reaching NOTHING and a pod working perfectly produce byte-identical
# observations, and the row count was the only health signal that existed. That
# is not a gap in monitoring, it is the reason a diagnosis took hours: an empty
# result cannot distinguish two mechanisms, and no upstream signal disagreed
# between them.
#
# 🔴 THE HEARTBEAT MUST NOT LIVE IN THE FRAME LOOP. `run()` blocks in
# `for payload in iter_frames(...)` — on an idle account that blocks for HOURS.
# A heartbeat written per frame would therefore fire only when traffic exists,
# i.e. only in the case that never needed a heartbeat. It runs on its own thread.
#
# 🔴 TWO SINKS, DELIBERATELY, AND THEY ANSWER DIFFERENT QUESTIONS.
#   * the FILE  — "this process's thread is still scheduled". Depends on nothing
#     external, so it is the only safe input to a k8s liveness probe: keying a
#     restart on Postgres reachability would turn a database blip into a
#     restart storm, taking down a consumer that was working fine.
#   * the ROW   — "…and Postgres is reachable, and here are the counters". This
#     is the human/deadman signal. It is richer and it is ALLOWED to fail.
# A DB outage degrades the row and leaves the file (and therefore liveness)
# intact. That asymmetry is the entire point of having two.

#: Where the liveness file is written. Env-overridable so the probe and the
#: consumer cannot disagree about the path by editing one of them.
HEARTBEAT_PATH = os.environ.get("SIGNAL_HEARTBEAT_PATH", "/tmp/signal-consumer-heartbeat.json")

#: How often the heartbeat thread ticks.
HEARTBEAT_INTERVAL = float(os.environ.get("SIGNAL_HEARTBEAT_INTERVAL", "30"))

#: How old a heartbeat may be and still mean "alive".
#:
#: 🔴 Derived from the interval, not chosen by taste: a probe that trips at less
#: than a small multiple of the write period fires on ordinary scheduling jitter
#: and restarts a healthy pod. Four ticks means four consecutive misses.
HEARTBEAT_MAX_AGE = float(os.environ.get("SIGNAL_HEARTBEAT_MAX_AGE",
                                         str(HEARTBEAT_INTERVAL * 4)))


def heartbeat_is_fresh(age_seconds: float | None, max_age: float = None) -> bool:
    """THE freshness predicate. One definition, every caller.

    `None` (no heartbeat at all) is NOT fresh — a consumer that has never written
    one is exactly the state this exists to catch, and defaulting a missing
    reading to "fine" is how a dead measuring apparatus reports all-clear.
    """
    if age_seconds is None:
        return False
    limit = HEARTBEAT_MAX_AGE if max_age is None else max_age
    # 🔴 A NEGATIVE age is not "very fresh", it is a BROKEN clock — the writer's
    # wall clock stepped backwards (NTP correction, suspend/resume) after the
    # beat was written. Treating it as fresh would let an arbitrarily stale
    # heartbeat read as healthy, which is the one answer this predicate must
    # never give by accident.
    if age_seconds < 0:
        return False
    return age_seconds <= limit


def write_heartbeat_file(payload: dict, path: str = None) -> None:
    """Write the liveness file ATOMICALLY (tmp + rename).

    A probe reading a half-written file would parse-fail and be scored as "no
    heartbeat" — a restart caused purely by the instrument.
    """
    target = Path(path or HEARTBEAT_PATH)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    tmp.replace(target)


def read_heartbeat_file(path: str = None, now: float = None) -> dict | None:
    """The heartbeat file plus its age, or None if absent/unreadable/corrupt.

    Every failure collapses to None on purpose: absent, truncated and garbage all
    mean the same thing to a probe — "no current measurement" — and a caller that
    had to distinguish them would grow its own freshness opinion.
    """
    try:
        raw = Path(path or HEARTBEAT_PATH).read_text(encoding="utf-8")
        payload = json.loads(raw)
        written = float(payload["written_at"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    payload["age_seconds"] = (time.time() if now is None else now) - written
    return payload


class Heartbeat:
    """Ticks the file and the row on SEPARATE threads.

    🔴 IT OPENS ITS OWN DATABASE CONNECTION. `SignalDB` runs `autocommit=False`,
    so a heartbeat issued on the ingest connection would land inside whatever
    transaction the ingest loop has open — committing a partial batch or being
    rolled back with it. Sharing the connection is the bug, not an optimisation.

    🔴 TWO THREADS, AND THAT IS THE WHOLE ANTI-CASCADE PROPERTY. The first cut
    wrote the file and then the row on ONE thread, reasoning that the file came
    first so a database fault could not affect it. That is true only for a
    database that fails FAST. A STALLED Postgres — node partitioned after the
    socket was established, storage I/O hang on the PV, failover, another
    session holding the row lock — blocks inside the row write, and the next
    FILE write never happens. Measured on real Postgres: with the row locked by
    a second session the file aged past its max-age and the probe went unhealthy,
    which under a k8s liveness probe kills a consumer that was working fine and
    then kills its replacement 30s later, for the duration of the incident. The
    outage shape the original test used — a factory that raises instantly — is
    the one shape that was already safe.

    So the file loop now touches nothing but the local filesystem, and the row
    loop is free to block for as long as it likes without anyone caring.
    """

    def __init__(self, consumer, *, db_factory=None, interval: float = None,
                 path: str = None, clock=time.time):
        self._consumer = consumer
        self._db_factory = db_factory
        self._interval = HEARTBEAT_INTERVAL if interval is None else interval
        self._path = path or HEARTBEAT_PATH
        self._clock = clock
        self._stop = threading.Event()
        self._thread = None
        self._row_thread = None
        #: beats ATTEMPTED vs beats that actually wrote the file. They differ
        #: precisely when the sink is failing, which is the one time an operator
        #: needs to tell "the thread is wedged" from "the thread is trying and
        #: the disk is refusing" — the same working/dead distinction this module
        #: exists to make, one level down.
        self.attempts = 0
        self.ticks = 0
        self.row_attempts = 0
        self.row_writes = 0
        self.row_failures = 0

    def payload(self) -> dict:
        c = self._consumer
        written = self._clock()
        return {
            "written_at": written,
            # The row's columns are epoch-MS BIGINT (see the DDL comment). Carried
            # alongside rather than converted inside the DB layer so there is one
            # obvious place where the unit changes.
            "written_at_ms": int(written * 1000),
            "pod": socket.gethostname(),
            "stream_connected": bool(getattr(c, "stream_connected", False)),
            "last_frame_at": getattr(c, "last_frame_at", None),
            "connections": int(getattr(c, "connections", 0)),
            "reconnects": int(c.stats.get(STAT_RECONNECTS, 0)),
            "stored": int(c.stats.get(STAT_STORED, 0)),
            "malformed": int(c.stats.get(STAT_MALFORMED, 0)),
        }

    # -- the liveness sink: local filesystem ONLY, never the network ---------
    def tick(self) -> dict:
        """One FILE beat. Touches nothing that can block on a network.

        This is the liveness contract. Keep it that way: anything added here
        that can hang re-creates the cascade the two threads exist to prevent.
        """
        self.attempts += 1
        hb = self.payload()
        write_heartbeat_file(hb, self._path)
        self.ticks += 1
        return hb

    # -- the observability sink: allowed to be slow, allowed to fail ---------
    def tick_row(self) -> None:
        """One ROW beat, on its own thread.

        🔴 Failures are counted and printed, never raised: a Postgres blip must
        not kill the thread, and it must never reach the file loop. A thread
        that dies on the first outage is a signal that reports death for exactly
        the fault it was supposed to ride out.
        """
        if self._db_factory is None:
            return
        self.row_attempts += 1
        try:
            with self._db_factory() as db:
                db.record_heartbeat(self.payload())
                db.commit()
            self.row_writes += 1
        except Exception as exc:  # noqa: BLE001 — see docstring
            self.row_failures += 1
            print(f"signal-consumer: heartbeat row failed ({exc})", file=sys.stderr)

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception as exc:  # noqa: BLE001 — the thread must outlive a bad beat
                print(f"signal-consumer: heartbeat failed ({exc})", file=sys.stderr)
            self._stop.wait(self._interval)

    def _row_loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick_row()
            except Exception as exc:  # noqa: BLE001 — belt and braces; tick_row swallows
                print(f"signal-consumer: heartbeat row loop ({exc})", file=sys.stderr)
            self._stop.wait(self._interval)

    def start(self) -> "Heartbeat":
        self.tick()                      # beat ONCE before the first wait, so a
                                         # probe has a reading immediately
        self._thread = threading.Thread(target=self._loop, name="signal-heartbeat",
                                        daemon=True)
        self._thread.start()
        if self._db_factory is not None:
            self._row_thread = threading.Thread(target=self._row_loop,
                                                name="signal-heartbeat-row",
                                                daemon=True)
            self._row_thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        for t in (self._thread, self._row_thread):
            if t is not None:
                # 🔴 A SHORT join, and daemon=True, precisely because the row
                # thread may be wedged inside a stalled database call. Waiting on
                # it would make shutdown hang for the same reason liveness used
                # to; the interpreter reaps a daemon thread regardless.
                t.join(timeout=5)


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
        # Stream state the heartbeat reports. Initialised HERE, not lazily in
        # run(), so `Heartbeat.payload()` can never read a missing attribute off
        # a consumer that has not started yet — a health writer that raises is a
        # health writer that reports nothing.
        self.stream_connected = False
        self.connections = 0
        self.last_frame_at = None

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
            self.connections = connections
            try:
                stream = self._stream_factory()
                # Set only AFTER the factory returns: a factory that raises never
                # opened anything, and reporting `connected` for it would make the
                # health row assert a connection that does not exist.
                self.stream_connected = True
                for payload in iter_frames(stream):
                    self.last_frame_at = int(time.time() * 1000)
                    self.handle_payload(payload)
            except Exception as exc:  # noqa: BLE001 — a dropped stream is normal
                self.stats[STAT_RECONNECTS] += 1
                print(f"signal-consumer: stream ended ({exc}); reconnecting",
                      file=sys.stderr)
                self._sleep(self._backoff)
                continue
            finally:
                self.stream_connected = False
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
                      mentions: list | None = None,
                      poster=None, api_url: str | None = None,
                      timeout: float = 20.0) -> dict | list:
    """POST an approved draft to the Signal API. Requires a `SendAuthorization`.

    🔴 `spend_authorization()` runs BEFORE anything touches the network, and it
    raises `SendGateError` for a non-capability, a forged look-alike, a
    capability that has already been spent, OR a payload that is not the one the
    capability was minted for. There is no keyword that skips it and no other
    send-endpoint call site in `scripts/signal/`.

    `mentions` is the wire array (`[{"author","start","length"}, …]`) and is part
    of what the capability binds, so it cannot be added, removed or re-pointed
    between approval and this call.

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
    spend_authorization(auth, recipient=recipient, body=body, mentions=mentions)
    if not number:
        raise SendGateError(
            "transmit refused: no sending `number` — the server would reject this "
            "with 400 'please provide a valid number' (D3 approval gate)")
    url = (api_url or API_URL).rstrip("/") + SEND_PATH
    payload = {"message": body, "number": number, "recipients": [recipient]}
    # 🔴 OMITTED, not sent empty, when there are no mentions. Upstream's
    # `SendMessageV2` unmarshals `mentions` into `[]data.MessageMention`; a
    # mention-free send has never carried the key and does not start now, so the
    # request byte-for-byte matches the one this path has been making all along.
    # Each entry is rebuilt with EXACTLY the three keys the server's struct has
    # — a fourth would be dropped silently, and a stray key from a stored row is
    # not something to discover on the wire.
    if mentions:
        payload["mentions"] = [
            {"author": str(m["author"]), "start": int(m["start"]),
             "length": int(m["length"])}
            for m in mentions
        ]
    if poster is None:  # pragma: no cover - the live path; tests inject a poster
        import requests
        poster = requests.post
    resp = poster(url, json=payload, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def fetch_group_members(number: str, group_address: str, *, getter=None,
                        api_url: str | None = None,
                        timeout: float = 20.0) -> list:
    """`GET /v1/groups/<number>/<group-id>` → the group's `members[]`.

    Read-only, and the ONLY reason this function exists is mention resolution: a
    display name is meaningless without the membership to resolve it against,
    and resolving against the whole `signal.contacts` table would let a name that
    matches somebody in a DIFFERENT conversation become an `author` here.

    Returns the raw list — a MIX of E.164 strings and bare UUID strings. It is
    deliberately not normalised: `_mentions` owns that, so there is one place
    that decides what a member identifier means.

    A member entry may also be an OBJECT (`{"number": …, "uuid": …}`) depending
    on the upstream version; both shapes are flattened rather than assumed, for
    the same reason `_normalize_send_response` accepts two reply shapes — a
    surprise here would otherwise read as "this group has no members", which is
    a REFUSAL in `_mentions.resolve_mentions` and not a silent empty array.
    """
    if getter is None:  # pragma: no cover - the live path; tests inject a getter
        import requests
        getter = requests.get
    url = "{}{}/{}/{}".format((api_url or API_URL).rstrip("/"), GROUPS_PATH,
                              _urlquote(number, safe=""),
                              _urlquote(group_address, safe=""))
    resp = getter(url, timeout=timeout)
    resp.raise_for_status()
    detail = resp.json()
    if isinstance(detail, list):  # some versions return a single-element list
        detail = detail[0] if detail else {}
    out = []
    for member in (detail or {}).get("members") or []:
        if isinstance(member, dict):
            ident = member.get("uuid") or member.get("number")
        else:
            ident = member
        if ident:
            out.append(str(ident))
    return out


def resolve_draft_mentions(db, *, recipient: str, body: str, identifiers,
                           number: str, member_fetcher=None) -> list:
    """`--mention` values → the wire mentions array for one draft, or raise.

    The seam between the three pieces, and the reason it is a named function
    rather than four lines inside `main()`: a test can drive it with an injected
    `member_fetcher` and the hermetic DB, which is the only way the refusal
    matrix is reachable without a network.

    Returns `[]` immediately when nothing was asked for — so the no-mention path
    makes NO group API call at all, and `draft --to +15550100` keeps working with
    no membership lookup and no behaviour change.
    """
    if not identifiers:
        return []
    is_group = _signal_db._looks_like_group_address(recipient)
    if not is_group:
        # Refuse WITHOUT a membership fetch: there is no group to fetch, and the
        # error must name the real problem rather than an HTTP 404 from a URL
        # built out of a phone number.
        return _mentions.resolve_mentions(identifiers, body=body, members=[],
                                          contacts=[], is_group=False)
    # 🔴 REFUSED HERE, NOT AS A 404. An empty `--from-number` builds
    # `/v1/groups//<gid>`, which the server answers 404 — and the 404 escaped
    # `resp.raise_for_status()` as an `HTTPError`, which is NOT a `ValueError`,
    # so `main()`'s draft handler did not catch it and the operator got a
    # traceback and exit 1 for a missing argument.
    if not str(number or "").strip():
        raise _mentions.MentionGroupLookupFailed(
            "mentions need the SENDING account number to look the group's "
            "membership up (`GET /v1/groups/<number>/<id>`), and --from-number "
            "is empty. Pass --from-number, or set $SIGNAL_ACCOUNT.")
    fetch = member_fetcher or fetch_group_members
    try:
        members = fetch(number, recipient)
    except (_mentions.MentionError, AssertionError):
        # `MentionError` is already the right shape. `AssertionError` is
        # re-raised because the suite's "this must never be called" fetchers
        # raise it: swallowing one would turn a guard that FIRED into a
        # `MentionGroupLookupFailed` the surrounding test happily accepts —
        # green for exactly the wrong reason.
        raise
    except Exception as exc:  # noqa: BLE001 - deliberately wide; see below
        # 🔴 ANY failure of the membership lookup is a REFUSAL, in the type the
        # `draft` handler catches. The transport raises `requests` exceptions,
        # the JSON decode raises its own, and neither is a `ValueError` — so
        # every one of them reached the operator as a traceback. Re-raised with
        # the original type NAMED and chained, so nothing is hidden.
        raise _mentions.MentionGroupLookupFailed(
            f"could not read the target group's membership from "
            f"`GET /v1/groups/<number>/<id>` ({type(exc).__name__}: {exc}), so no "
            f"--mention can be resolved. Nothing was drafted. Check "
            f"--from-number and that --to is the group `id`, not its "
            f"`internal_id`.") from exc
    contacts = db.contacts_by_identifiers(members)
    return _mentions.resolve_mentions(identifiers, body=body, members=members,
                                      contacts=contacts, is_group=True)


def mention_author_names(db, mentions) -> dict:
    """`{author: display name}` for a resolved mentions array. For the CARD.

    The card's job is to tell a HUMAN who a draft will ping, and `author` is
    usually a bare uuid.

    🔴 A SECOND QUERY, NOT THE RESOLVER'S. It goes through the same
    `contacts_by_identifiers` METHOD, but with a different identifier list — the
    resolved `author` ids, not the group's full membership — so it is a separate
    round trip against a table that ordinary ingest is writing to concurrently.
    The docstring used to claim the two "come from one query"; they do not, and
    a promotion landing in between can legitimately make the card print a name
    the resolver did not see. That is a display-time difference only: the
    `author` on the wire is the one the resolver returned, and it is what the
    approval digest binds. Named rather than papered over, because a reader who
    believed "one query" would treat a disagreement as impossible.
    """
    if not mentions:
        return {}
    authors = [str(m.get("author")) for m in mentions if m.get("author")]
    return _mentions.author_names(mentions, db.contacts_by_identifiers(authors))


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

    h = sub.add_parser(
        "health",
        help="is the consumer alive? (exit 0 healthy, 1 stale) — the k8s probe")
    h.add_argument("--max-age", type=float, default=None,
                   help=f"seconds before a heartbeat is stale (default {HEARTBEAT_MAX_AGE:g})")
    h.add_argument("--from-db", action="store_true",
                   help="read the health ROW instead of the local file. Richer, but "
                        "depends on Postgres — do NOT use this as a liveness probe")
    h.add_argument("--json", action="store_true", help="machine-readable output")

    q = sub.add_parser("conversations", help="list conversations, newest first")
    q.add_argument("--limit", type=int, default=25)

    s = sub.add_parser("search", help="full-text search over message bodies")
    s.add_argument("query")
    s.add_argument("--limit", type=int, default=25)

    sub.add_parser("muted", help="list muted groups and how many rows each hides")

    mu = sub.add_parser(
        "mute",
        help="hide a group from every read (stores and deletes NOTHING)")
    mu.add_argument("internal_id",
                    help="the group's base64 `internal_id` from "
                         "GET /v1/groups/<account> — NOT the display name, which "
                         "this pipeline does not capture")
    mu.add_argument("--note", help="why, recorded on the row")
    mu.add_argument("--allow-unseen", action="store_true",
                    help="accept a group id that matches nothing stored yet. "
                         "Without this, muting an unknown id EXITS 4 — a typo "
                         "and a deliberate mute-before-first-message look "
                         "identical otherwise, and one of them hides nothing")

    um = sub.add_parser("unmute", help="un-hide a muted group; restores it entirely")
    um.add_argument("internal_id")

    d = sub.add_parser("draft", help="compose an outbound draft (transmits NOTHING)")
    d.add_argument("--to", required=True,
                   help="a PERSON (+15550100 or a uuid), or a GROUP as "
                        "`group.<double-base64>` — the `id` field from "
                        "GET /v1/groups/<account>, NOT the `internal_id` that "
                        "`mute` takes. The two commands want opposite halves of "
                        "the same value; see `internal_id vs id` in SKILL.md")
    d.add_argument("--body", required=True)
    d.add_argument("--from-number", default=ACCOUNT,
                   help="the sending account (default $SIGNAL_ACCOUNT)")
    d.add_argument("--mention", action="append", default=[], metavar="WHO",
                   help="ping a GROUP member. Repeatable. WHO is the member's "
                        "display name, or their bare uuid / +E.164. --body MUST "
                        "already contain the literal text `@WHO` — a Signal "
                        "mention REPLACES an existing span of the message, so "
                        "the span has to exist. Anything that cannot be resolved "
                        "to exactly one real member of THIS group is REFUSED "
                        "(exit 3), never dropped: a mention pushes a "
                        "notification through the recipient's mute settings, so "
                        "silently sending fewer than you asked for is a "
                        "different act from the one you approved")

    ls = sub.add_parser("drafts", help="list drafts and their send_state")
    ls.add_argument("--state", choices=[STATE_PENDING, STATE_APPROVED,
                                        STATE_SENDING, STATE_SENT])

    a = sub.add_parser("approve", help="record Zach's clawgate approval for a draft")
    a.add_argument("draft_id", type=int)
    a.add_argument("--ref", required=True, help="the clawgate approval reference")

    ua = sub.add_parser(
        "unapprove",
        help="withdraw an approval: approved -> pending, so it can be approved "
             "again (the ONLY route out of a digest refusal)")
    ua.add_argument("draft_id", type=int)
    ua.add_argument("--note", help="why, recorded on the row's approval_ref")

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


def _report_health(row, source: str, args) -> int:
    """Format one health reading and return the PROCESS EXIT CODE.

    Shared by both sources so the file and the row cannot drift into two
    different opinions about what "healthy" means.
    """
    age = row.get("age_seconds") if row else None
    report = dict(row) if row else {}
    report["source"] = source
    fresh = heartbeat_is_fresh(None if age is None else float(age), args.max_age)
    report["age_seconds"] = None if age is None else round(float(age), 1)
    report["healthy"] = fresh
    if args.json:
        print(json.dumps(report, default=str))
    elif not row:
        # 🔴 Say WHICH question came back empty. "no heartbeat" from the file
        # means the process never wrote one; from the db it can ALSO mean
        # Postgres is unreachable — different faults, and a bare "unhealthy"
        # sends the reader hunting the wrong one.
        print(f"UNHEALTHY: no heartbeat ({source})")
    else:
        print(f"{'HEALTHY' if fresh else 'UNHEALTHY'}: "
              f"heartbeat {report['age_seconds']}s old "
              f"(connected={row.get('stream_connected')}, "
              f"reconnects={row.get('reconnects')}, "
              f"stored={row.get('stored')}, pod={row.get('pod')})")
    return 0 if fresh else 1


def main(argv=None) -> int:  # pragma: no cover - thin CLI shell over tested units
    args = build_parser().parse_args(argv)

    # 🔴 ANSWERED BEFORE ANY DATABASE CONNECTION EXISTS, and that is the whole
    # point. Every other command runs inside `with SignalDB()`, which connects
    # AND runs `ensure_schema()` — so routing the probe through it would make
    # k8s liveness depend on Postgres being up, and a database blip would
    # restart a consumer that was working perfectly. That is the exact cascade
    # the two-sink design exists to prevent; it would have been reintroduced
    # here, in the CLI, after being carefully avoided in the daemon.
    if args.cmd == "health" and not args.from_db:
        return _report_health(read_heartbeat_file(), "file", args)

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
            # 🔴 `db_factory=SignalDB` — a NEW connection per beat, never `db`.
            # See Heartbeat's docstring: autocommit=False makes a shared
            # connection a transaction hazard, not a saving.
            beat = Heartbeat(consumer, db_factory=SignalDB).start()
            try:
                print(json.dumps(consumer.run()))
            finally:
                beat.stop()
        elif args.cmd == "health":
            # Only reachable with --from-db; the file path returned above,
            # before this connection was ever opened.
            return _report_health(db.read_heartbeat(), "db", args)
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
        elif args.cmd == "muted":
            rows = db.list_excluded_groups()
            if not rows:
                print("no groups are muted")
            for row in rows:
                print(f"{_fmt_group_id(row['group_id'])}  "
                      f"row={row['group_row_id'] if row['group_row_id'] is not None else '-'}  "
                      f"hides={row['hidden_message_count'] or 0}  "
                      f"{row.get('note') or ''}")
        elif args.cmd in ("mute", "unmute"):
            try:
                gid = _decode_internal_id(args.internal_id)
            except ValueError as exc:
                print(f"refused: {exc}", file=sys.stderr)
                return 3
            if args.cmd == "mute":
                db.exclude_group(gid, note=args.note)
                db.commit()
                # 🔴 REPORT WHAT WAS MATCHED, never a bare "muted". A mistyped
                # base64 id still decodes to a perfectly valid 32 bytes, so no
                # length or alphabet check can catch it — the only thing that
                # discriminates a real id from a plausible one is whether it
                # joins to a stored group, and how many rows it now hides.
                hit = next((r for r in db.list_excluded_groups()
                            if bytes(r["group_id"]) == gid), None)
                if hit and hit["group_row_id"] is not None:
                    print(f"muted group row {hit['group_row_id']} — now hiding "
                          f"{hit['hidden_message_count'] or 0} stored message(s)")
                elif args.allow_unseen:
                    print("muted an as-yet-unseen group; it applies from the "
                          "first message that arrives.")
                else:
                    # 🔴 NON-ZERO. The prose above used to say all this and exit
                    # 0 — the same code as a mute that hid 18 messages — so no
                    # script, hook or agent could tell a working mute from one
                    # that matched nothing. The report has to carry in the exit
                    # status, not only in the text a human might read.
                    print("REFUSING to report success: no stored group has this "
                          "id, so this mute hides NOTHING. Either the id is "
                          "wrong (check `internal_id` from GET /v1/groups/"
                          "<account>), or you meant to mute a group not yet "
                          "seen — in which case pass --allow-unseen. The row "
                          "HAS been written either way; `unmute` removes it.",
                          file=sys.stderr)
                    return 4
            else:
                removed = db.unexclude_group(gid)
                db.commit()
                print(f"unmuted ({removed} row(s) removed)" if removed
                      else "that group was not muted — nothing changed")
        elif args.cmd == "draft":
            import clawgate
            try:
                # 🔴 RESOLVED BEFORE THE ROW IS WRITTEN. Every mention refusal is
                # a `ValueError` subclass and lands in the handler below, so a
                # bad --mention leaves NO draft behind — an operator who fixes
                # the name and re-runs gets one draft, not two, and the clawgate
                # queue never shows a card for a message that cannot be sent.
                mentions = resolve_draft_mentions(
                    db, recipient=args.to, body=args.body,
                    identifiers=args.mention, number=args.from_number)
                draft = db.draft_message(
                    recipient=args.to, body=args.body,
                    self_number=args.from_number, mentions=mentions)
            except ValueError as exc:
                # 🔴 EXIT 3, like every sibling. `mute` catches ValueError and
                # `send`/`reconcile` catch SendGateError, both exiting 3; this
                # branch alone let a bad `--to` escape as an uncaught traceback
                # and exit 1 — which a caller cannot distinguish from the
                # interpreter dying for an unrelated reason.
                print(f"refused: {exc}", file=sys.stderr)
                return 3
            clawgate.emit_draft_task(
                draft_id=draft["id"], recipient=args.to, body=args.body,
                mentions=mentions,
                author_names=mention_author_names(db, mentions))
            print(json.dumps(draft))
        elif args.cmd == "drafts":
            print(json.dumps(db.list_drafts(state=args.state), default=str))
        elif args.cmd == "approve":
            try:
                print(json.dumps(db.approve_draft(args.draft_id,
                                                  approval_ref=args.ref),
                                 default=str))
            except SendGateError as exc:
                # Exit 3 like every sibling refusal. `approve` alone let a
                # missing token / wrong-state refusal escape as a traceback.
                print(f"refused: {exc}", file=sys.stderr)
                return 3
        elif args.cmd == "unapprove":
            try:
                print(json.dumps(db.unapprove_draft(args.draft_id,
                                                    note=args.note),
                                 default=str))
            except SendGateError as exc:
                print(f"refused: {exc}", file=sys.stderr)
                return 3
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
