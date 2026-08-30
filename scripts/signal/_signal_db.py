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

import base64
import contextlib
import hashlib
import json
import os
import re
import socket
import subprocess
import sys
import time
import uuid
from urllib.parse import urlparse

# Pure, dependency-free, and imports nothing from this module — no cycle.
import _mentions

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

    """
    -- 🔴 THE HEALTH ROW. This consumer serves no HTTP, has no probes, and emitted
    -- ZERO log lines across 20h of successful ingestion — so "reaching nothing"
    -- and "working perfectly" produced byte-identical observations, and row count
    -- was the only health signal in existence. That is what made the step-7
    -- diagnosis take hours: no upstream signal disagreed between the rival
    -- explanations, so an empty table could not identify which was true.
    --
    -- ONE ROW, upserted on a timer (id is pinned to 1 by the primary key + the
    -- upsert's ON CONFLICT). It is deliberately NOT a history table: the question
    -- is "is it alive NOW", and an append-only log of heartbeats would need its
    -- own retention story to answer it.
    -- 🔴 EPOCH-MS BIGINT, like message_timestamp and server_received_at, NOT
    -- timestamptz. The first cut declared these TIMESTAMPTZ and wrote a Python
    -- float into them, so the upsert raised `is of type timestamp with time zone
    -- but expression is of type numeric` on EVERY beat once the first frame
    -- arrived — the row was correct only while nothing worked, and broke the
    -- moment it did. Nothing caught it because no test executed this SQL.
    -- BIGINT also keeps the statement free of `now()`/`EXTRACT`, which the
    -- sqlite substrate cannot translate; that is what makes it TESTABLE, and
    -- untestable was the root cause, not the type.
    CREATE TABLE IF NOT EXISTS signal.consumer_health (
        id SMALLINT PRIMARY KEY DEFAULT 1 CHECK (id = 1),
        updated_at BIGINT NOT NULL,
        pod TEXT,
        stream_connected BOOLEAN NOT NULL,
        -- 🔴 NOT a liveness input. An idle Signal account legitimately sends
        -- nothing for hours, so "no frame recently" is NORMAL and must never
        -- restart anything. It is here because it is the first thing a human
        -- wants when diagnosing, and conflating it with liveness is precisely
        -- the mistake that would make a probe flap on a quiet weekend.
        last_frame_at BIGINT,
        connections BIGINT NOT NULL DEFAULT 0,
        reconnects BIGINT NOT NULL DEFAULT 0,
        stored BIGINT NOT NULL DEFAULT 0,
        malformed BIGINT NOT NULL DEFAULT 0
    )
    """,

    """
    -- 🔴 THE MUTE LIST. Rows named here are HIDDEN FROM READS, never dropped
    -- from ingest and never deleted — see `not_excluded()` for the one predicate
    -- that enforces it. Hiding rather than dropping is what makes the decision
    -- REVERSIBLE: delete a row here and the whole conversation reappears, which
    -- is impossible if the consumer had refused to store it (the pipeline is
    -- forward-only, so nothing can re-fetch history it declined).
    --
    -- 🔴 KEYED ON THE SIGNAL BINARY GROUP ID, not on `signal.groups.id` and NOT
    -- on the name. Two measured reasons, either one fatal on its own:
    --   * a NAME IS NOT AN IDENTITY. It is operator-visible text: a group can be
    --     renamed at any time by any member, two groups can share a name, and
    --     the stored value is only as good as the last envelope that carried
    --     one. The binary id never changes.
    --     (🔴 RETRACTED, 2026-08-21: this bullet used to read "`signal.groups.name`
    --     is EMPTY ('') for every group this consumer has stored — it never
    --     captured names". That is FALSE. `upsert_group()` below persists the
    --     envelope's `groupName`, `test_a_stored_group_keeps_the_name_the_envelope_carried`
    --     pins it, and the live store has populated names — MEASURED 2026-08-21
    --     against prod `signal.groups`, which holds 'Vetr app group' and
    --     'Family Winnipeg'. (Stated as a measurement because an auditor
    --     re-derived the opposite from `consumer.py`'s parse and doubted it;
    --     reading the code cannot settle what the table contains.)
    --     The advice was right
    --     for the wrong reason — which is worse than no reason, because a
    --     maintainer who checks the claim, finds names present, and concludes
    --     the whole warning is stale is one step from keying a filter on a
    --     column anyone can edit.)
    --   * the exclusion must be settable BEFORE the group has ever been seen;
    --     a FK to `signal.groups(id)` cannot express "mute this from now on"
    --     for a group with no row yet.
    -- The binary id is the stable Signal identity and is what `signal.groups`
    -- itself is UNIQUE on, so the join in `not_excluded()` is exact.
    CREATE TABLE IF NOT EXISTS signal.excluded_groups (
        group_id BYTEA PRIMARY KEY,
        note TEXT,
        created_at TIMESTAMPTZ DEFAULT now()
    )
    """,

    # 🔴 A MIGRATION, NOT A COLUMN IN THE `CREATE TABLE` ABOVE. Every other
    # statement here is `CREATE … IF NOT EXISTS`, which is a no-op against an
    # EXISTING table — so adding `mentions` to `signal.messages`'s CREATE body
    # would take effect on a fresh database (the hermetic substrate, which is
    # built from scratch every test) and do NOTHING to prod, where the table
    # already exists. The suite would be green and the deployed schema unchanged.
    # `ADD COLUMN IF NOT EXISTS` is idempotent in Postgres (>= 9.6), so
    # `ensure_schema()` stays safe to run on every start.
    #
    # JSONB, holding the wire array verbatim: `[{"author","start","length"}, …]`.
    # It is what was APPROVED and what is SENT, so it has to be durable for the
    # same reason the body is — and the send-authorization binding compares
    # against it (see `_mint_send_authorization`).
    "ALTER TABLE signal.messages ADD COLUMN IF NOT EXISTS mentions JSONB",

    # 🔴 WHAT THE HUMAN ACTUALLY APPROVED, as a digest. `approve_draft()` writes
    # it; `_mint_send_authorization()` refuses to mint when the row no longer
    # hashes to it.
    #
    # It has to be DURABLE and it has to be written at APPROVAL, because that is
    # where the window is. `approve` and `send` are separate CLI invocations
    # minutes or hours apart, and everything in between sees an ordinary row: a
    # capability minted at SEND time out of whatever the row says then agrees
    # with itself no matter what changed, which is precisely the tautology that
    # made the old `auth.recipient`/`auth.body` fields dead code. Comparing
    # against a value recorded BEFORE the wait is the only thing that can
    # disagree.
    "ALTER TABLE signal.messages ADD COLUMN IF NOT EXISTS approved_digest TEXT",

    "CREATE INDEX IF NOT EXISTS idx_msg_ts ON signal.messages(message_timestamp DESC)",
    "CREATE INDEX IF NOT EXISTS idx_msg_dm ON signal.messages(source_contact_id, message_timestamp)",
    "CREATE INDEX IF NOT EXISTS idx_msg_group ON signal.messages(group_id, message_timestamp) WHERE group_id IS NOT NULL",
    "CREATE INDEX IF NOT EXISTS idx_msg_fts ON signal.messages USING GIN(search)",
    "CREATE INDEX IF NOT EXISTS idx_att_msg ON signal.attachments(message_id)",
    # The partial index that makes the unresolved-reaction sweep cheap (🔧 #3).
    "CREATE INDEX IF NOT EXISTS idx_rx_unresolved ON signal.reactions(target_author_id, target_sent_timestamp) WHERE message_id IS NULL",
)


# --------------------------------------------------------------------------- #
# The mute predicate — ONE PLACE
# --------------------------------------------------------------------------- #
# 🔴 EVERY read that can surface message CONTENT must apply this, and it exists
# exactly once so it cannot diverge between call sites. This pipeline has already
# paid for the alternative: the reaction dict was open-coded at two sites and
# that is WHY the sync site shipped without the guards the inbound site had.
# A filter open-coded at three read sites would be wrong at two of them, in the
# same direction, and the failure is SILENT — a leaked conversation looks
# exactly like a conversation you meant to keep.
#
# `test_group_exclusions.py` pins the ledger of read methods against this
# predicate and fails when that set GROWS or SHRINKS, so a new read surface
# added without the filter is a red test rather than a quiet leak.
_SAFE_ALIAS = re.compile(r"^[a-z][a-z0-9_]{0,15}$")


def not_excluded(alias: str = "m") -> str:
    """SQL predicate: `alias` is a `signal.messages` row NOT in a muted group.

    Written as `NOT EXISTS` and never as `alias.group_id IS NULL OR ...` — the
    OR form needs parenthesising at every call site to survive being ANDed onto
    an existing WHERE clause, and forgetting those brackets at ONE site silently
    widens that query to every row in the database. `NOT EXISTS` composes with
    `AND` unconditionally and is already correct for a DM (`group_id IS NULL`
    matches no exclusion row, so the predicate is true).

    `alias` is interpolated, so it is validated against a strict whitelist — it
    is always a literal from this module, and the whitelist keeps it that way.
    Every VALUE in these queries is still a bound parameter.
    """
    if not _SAFE_ALIAS.match(alias):
        raise ValueError(f"unsafe SQL alias: {alias!r}")
    return (
        "NOT EXISTS (SELECT 1 FROM signal.excluded_groups x "
        "JOIN signal.groups gx ON gx.group_id = x.group_id "
        f"WHERE gx.id = {alias}.group_id)"
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

    __slots__ = ("draft_id", "recipient", "body", "mentions", "_nonce")

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


def payload_digest(*, recipient, body, mentions) -> str:
    """A stable fingerprint of the three things that make up a sent message.

    Canonical JSON with sorted keys so the digest depends on the VALUES and not
    on dict ordering or on whether `mentions` came back as `None`, `[]` or a JSON
    string — `canonical_mentions()` flattens all three to the same tuple.

    Not a security primitive: nothing here is defending against an attacker who
    can already write to the row AND recompute the digest. It defends against
    the realistic case — a second writer, a buggy job, an agent editing a draft
    it did not approve — where the row changes and the digest does not.
    """
    payload = json.dumps(
        {"recipient": recipient, "body": body,
         "mentions": [list(m) for m in _mentions.canonical_mentions(mentions)]},
        sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# 🔴 THE ROUTE OUT, NAMED IN THE ERROR THAT SENDS YOU DOWN IT. Both digest
# refusals used to end "Re-approve it" — an instruction the CLI could not carry
# out: `approve` is `pending`-only, `reconcile` is `sending`-only, and there was
# no third subcommand, so a refused draft was stuck in `approved` FOREVER. The
# text and `unapprove_draft()` ship together; `test_skill_doc` pins the command
# into SKILL.md and `test_mentions` walks this exact sequence with no raw SQL.
_REAPPROVE_HINT = (
    "run `unapprove {draft_id}` to return it to `pending`, then "
    "`approve {draft_id} --ref <new-clawgate-ref>` — both are operator commands "
    "and need SIGNAL_APPROVAL_TOKEN"
)


def recipient_identity(draft) -> str:
    """The STABLE identity of a draft's recipient, for the approval digest.

    🔴 WHY NOT `draft["recipient"]`. That field is a RENDERED PROJECTION, not an
    identity: `get_draft()`'s `CASE` prints a contact's `phone_number` while the
    row is a placeholder and its `signal_uuid` once it is not. `_promote_
    placeholder()` performs exactly that swap as UNATTENDED BACKGROUND INGEST —
    the moment an envelope arrives from a number we had only a placeholder for.

    So hashing the rendered string made the ordinary "message someone new" path
    fire the tamper guard: draft a DM to an unknown number → approve → that
    person's first envelope arrives → the projection changes → `send` refuses,
    with no adversary, no second writer, and NO CHANGE TO WHO THE MESSAGE GOES
    TO. The promotion PRESERVES the contact row id (that is the documented point
    of its `NOT EXISTS` guard), so the row id is stable across exactly the event
    the rendered form is not.

    Falls back to the address string only for a dict that carries no row ids —
    the hand-built rows in the unit tests, which have no substrate behind them.
    """
    d = draft or {}
    if d.get("group_id") is not None:
        return f"group:{d['group_id']}"
    if d.get("dest_contact_id") is not None:
        return f"contact:{d['dest_contact_id']}"
    return f"address:{d.get('recipient')}"


def draft_payload_digest(draft) -> str:
    """The approval digest OF A DRAFT ROW — the one definition, used by both sides.

    `approve_draft()` writes it and `_mint_send_authorization()` re-checks it. A
    single function so the two can never compute it over different fields, which
    is the shape that would make the guard either vacuous or permanently red.
    """
    d = draft or {}
    return payload_digest(recipient=recipient_identity(d), body=d.get("body"),
                          mentions=d.get("mentions"))


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
    # 🔴 THE APPROVE -> MUTATE -> SEND WINDOW, CLOSED HERE. `approve` and `send`
    # are separate invocations; in between, the draft is an ordinary row that
    # anything with the connection can rewrite. `approve_draft()` recorded a
    # digest of exactly what it showed the human, and this refuses to mint a
    # capability for a row that no longer hashes to it — so a body, a recipient
    # or a MENTION added, removed or re-pointed after approval cannot be
    # transmitted under that approval. FAILS CLOSED: a missing digest is a
    # refusal, not a pass, because "no digest" and "digest cleared by the writer
    # we are guarding against" are the same observation.
    recorded = (draft or {}).get("approved_digest")
    try:
        current = draft_payload_digest(draft)
    except _mentions.MentionError as exc:
        # A stored `mentions` column that cannot be read is a REFUSAL on the send
        # path, in the error type this path's CLI handler catches — not a bare
        # TypeError escaping as a traceback.
        raise SendGateError(
            f"draft {draft.get('id')!r} carries an unreadable `mentions` column, "
            f"so what was approved cannot be recomputed: {exc} (D3 approval gate)"
        ) from exc
    if not recorded:
        raise SendGateError(
            f"draft {draft.get('id')!r} is approved but carries NO approval "
            f"digest, so there is nothing to check the payload against. Either "
            f"it was approved before this binding existed, or the digest was "
            f"cleared — the two are indistinguishable from here. "
            + _REAPPROVE_HINT.format(draft_id=draft.get("id"))
            + " (D3 approval gate — approve/mutate/send binding)"
        )
    if recorded != current:
        raise SendGateError(
            f"draft {draft.get('id')!r} CHANGED after it was approved: the row "
            f"now hashes to {current} but the approval recorded {recorded}. "
            f"Nothing was sent. Read the draft, then, if the new text is what "
            f"you want, "
            + _REAPPROVE_HINT.format(draft_id=draft.get("id"))
            + " (D3 approval gate — approve/mutate/send binding)"
        )
    auth = object.__new__(SendAuthorization)
    object.__setattr__(auth, "draft_id", draft["id"])
    object.__setattr__(auth, "recipient", draft.get("recipient"))
    object.__setattr__(auth, "body", draft.get("body"))
    # 🔴 The MENTIONS are part of what was approved. A mention pushes a
    # notification through a mute setting and names a third party, so a payload
    # that gained, lost or re-pointed one after approval is a DIFFERENT act from
    # the one on the card. Bound here; verified in `spend_authorization()`.
    object.__setattr__(auth, "mentions",
                       _mentions.canonical_mentions(draft.get("mentions")))
    nonce = uuid.uuid4().hex
    object.__setattr__(auth, "_nonce", nonce)
    _ISSUED_NONCES.add(nonce)
    return auth


def spend_authorization(auth: object, *, recipient, body, mentions) -> None:
    """Consume a capability FOR A SPECIFIC PAYLOAD, or raise `SendGateError`.

    Called by `consumer.transmit_approved()` immediately before it touches the
    network. Rejects (a) anything that is not a real `SendAuthorization`,
    (b) a capability that has already been spent — which is what makes an
    approved draft transmit EXACTLY once — and (c) a payload that is not the one
    the capability was minted for.

    🔴 (c) IS THE NEW HALF, AND WHY THE ARGUMENTS ARE REQUIRED KEYWORDS. Until
    it existed the capability authorised "a transmit for draft N" and NOTHING
    about the message: `auth.recipient` and `auth.body` were populated at mint
    and then read by nobody, so anything that changed the row between approval
    and the POST — a concurrent writer, a second agent, a bug — sent text a human
    never saw, under an approval a human really did give. Mentions make that
    window materially worse: they notify third parties through mute settings, so
    a mutated mention array is an act against someone who is not in the
    conversation you approved.

    The three parameters have NO DEFAULTS on purpose. A default would let a call
    site silently opt out of the binding by omitting them, which is exactly the
    seam this closes; a `TypeError` at import-time-adjacent call sites is the
    point. This function is also the ONLY place the comparison lives — see
    `test_the_only_send_call_site_is_inside_transmit_approved`, which pins that
    `transmit_approved()` passes all three.
    """
    if not isinstance(auth, SendAuthorization):
        raise SendGateError(
            "transmit refused: a SendAuthorization minted from an approved draft "
            "is required (D3 approval gate); got "
            f"{type(auth).__name__}"
        )
    # 🔴 THE THREE CHECKS RUN TYPE -> NONCE -> PAYLOAD, and the order is load
    # bearing in BOTH directions. Nonce before payload, so a forgery that only
    # half-populates the slots is refused as the forgery it is rather than as a
    # payload mismatch (and so the pre-existing "already been spent" wording
    # still wins for a replayed capability). Payload before the DISCARD, so a
    # mismatch is a REFUSAL and not a consumed attempt: burning the capability
    # here would strand a draft that nothing is wrong with, and the operator's
    # remedy — look at the row, re-approve, re-send — needs it still sendable.
    nonce = getattr(auth, "_nonce", None)
    if nonce not in _ISSUED_NONCES:
        raise SendGateError(
            "transmit refused: this SendAuthorization has already been spent "
            "(D3 approval gate — capabilities are single-use)"
        )
    mismatches = []
    if auth.recipient != recipient:
        mismatches.append(f"recipient {auth.recipient!r} -> {recipient!r}")
    if auth.body != body:
        mismatches.append(f"body {auth.body!r} -> {body!r}")
    try:
        want_mentions = _mentions.canonical_mentions(mentions)
    except _mentions.MentionError as exc:
        # Same translation as the mint side: an unreadable mentions array is a
        # refusal in the type `send`'s CLI handler catches, not a raw TypeError.
        raise SendGateError(
            f"transmit refused: the mentions array offered for draft "
            f"{auth.draft_id!r} is unreadable — {exc} (D3 approval gate)") from exc
    have_mentions = getattr(auth, "mentions", ())
    if have_mentions != want_mentions:
        mismatches.append(f"mentions {list(have_mentions)!r} -> "
                          f"{list(want_mentions)!r}")
    if mismatches:
        raise SendGateError(
            f"transmit refused: the payload does not match what was approved for "
            f"draft {auth.draft_id!r} — {'; '.join(mismatches)}. The draft row "
            f"changed between approval and transmission; nothing was sent and the "
            f"capability was NOT spent (D3 approval gate — approve/mutate/send "
            f"binding)"
        )
    _ISSUED_NONCES.discard(nonce)


def _decode_mentions(value) -> list:
    """A stored `mentions` column → a list. NULL/''/'null' all mean none.

    psycopg2 already decodes JSONB; the sqlite substrate hands back the raw TEXT.
    Both reach this, so both engines produce the same Python shape.
    """
    if value is None or value == "":
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        value = bytes(value).decode("utf-8")
    if isinstance(value, str):
        decoded = json.loads(value)
        return list(decoded) if decoded else []
    raise ValueError(f"unreadable mentions column: {value!r}")


# `message_type` of a row whose sender retracted it. The row is KEPT (its
# timestamp still has to dedupe redeliveries) but carries no content.
TYPE_DELETED = "deleted"


def _normalize_send_response(result) -> list:
    """The `/v2/send` reply as a list of per-recipient dicts.

    🔴 MEASURED, not assumed. Against signal-cli-rest-api in **json-rpc** mode
    the success reply is a LIST, one entry per recipient::

        201  [{"timestamp":"1787331796630"}]

    A bare dict is also accepted, because upstream documents
    `ds.SendMessageResponse` as an object and other modes/versions return that
    shape. Anything else raises, deliberately: the caller has already COMMITTED
    `send_state=sending` before the POST, so a surprise here leaves a draft a
    human reconciles — never a silent duplicate send.

    Exactly one entry is required. Every caller sends to a single recipient
    (`recipients: [recipient]`), so a longer list means the wire contract
    changed and picking an entry would be a guess about which message the stored
    timestamp belongs to — which is precisely what breaks sync-echo dedupe.
    """
    if isinstance(result, dict):
        entries = [result]
    elif isinstance(result, list):
        entries = result
    else:
        raise ValueError(
            f"unrecognised /v2/send response shape {type(result).__name__}: "
            f"{result!r}; expected a list of per-recipient objects (json-rpc "
            f"mode) or a single object"
        )
    if not entries:
        raise ValueError(
            "the Signal API returned an EMPTY /v2/send response; without a "
            "per-recipient entry there is no timestamp to dedupe the sync echo"
        )
    if len(entries) != 1:
        raise ValueError(
            f"the Signal API returned {len(entries)} /v2/send entries for a "
            f"single recipient: {entries!r}; refusing to guess which timestamp "
            f"to store"
        )
    if not all(isinstance(e, dict) for e in entries):
        raise ValueError(
            f"the Signal API returned non-object entries in /v2/send: {entries!r}"
        )
    return entries


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
        """Create the `signal` schema. Idempotent — every statement is IF NOT EXISTS.

        Including the `ALTER TABLE … ADD COLUMN IF NOT EXISTS` migration, which
        is idempotent for the same reason and is pinned by
        `test_mentions.py::test_ensure_schema_twice_is_a_no_op`.
        """
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

    def contacts_by_identifiers(self, identifiers) -> list:
        """Contact rows for a MIXED list of uuids and phone numbers.

        `GET /v1/groups/.../members` returns both forms in one array, so a lookup
        that queried only `signal_uuid` would find nothing for the E.164 members
        and a lookup that queried only `phone_number` would find nothing for the
        uuid-only ones — and in the measured 7-member group, 5 members are
        uuid-only. Both columns are matched, in ONE query, and
        `is_placeholder` comes back with the row because a placeholder is a
        REFUSAL upstream, not something to filter out silently here.
        """
        idents = [str(i).strip() for i in (identifiers or []) if str(i or "").strip()]
        if not idents:
            return []
        # Two separate `IN` lists rather than one: `signal_uuid` is `uuid` in
        # Postgres and comparing it to a phone number like '+15550100' raises
        # `invalid input syntax for type uuid`. CASTING the column to text keeps
        # both engines happy and keeps the whole thing one round trip.
        placeholders = ", ".join(["%s"] * len(idents))
        with self._c.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT id, CAST(signal_uuid AS text) AS signal_uuid, "
                "phone_number, display_name, profile_name, is_placeholder "
                "FROM signal.contacts "
                f"WHERE CAST(signal_uuid AS text) IN ({placeholders}) "
                f"   OR phone_number IN ({placeholders})",
                tuple(idents) * 2,
            )
            return [dict(r) for r in cur.fetchall()]

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

    def group_exists(self, group_id: bytes) -> bool:
        """Has this binary group id EVER been stored?

        Read-only, and separate from `upsert_group` on purpose: the upsert
        cannot answer it, because `ON CONFLICT … DO UPDATE` returns a row id
        identically whether it inserted or updated. `draft_message()` needs the
        distinction to warn that it is minting a group nobody has seen.
        """
        with self._c.cursor() as cur:
            cur.execute("SELECT 1 FROM signal.groups WHERE group_id = %s",
                        (group_id,))
            return cur.fetchone() is not None

    def upsert_group(self, *, group_id: bytes, name: str | None = None,
                     revision: int = 0) -> int:
        with self._c.cursor() as cur:
            cur.execute(
                """
                INSERT INTO signal.groups (group_id, name, revision)
                VALUES (%s, %s, %s)
                ON CONFLICT (group_id) DO UPDATE SET
                    -- 🔴 NULLIF, not a bare COALESCE. The bind below is
                    -- `name or ""` because the column is NOT NULL, so
                    -- `EXCLUDED.name` is NEVER NULL — it is `''` — and the
                    -- COALESCE that was here could therefore never fire. It was
                    -- dead code that READ as protection: a later envelope for a
                    -- known group carrying no name (a sync echo, an edit, a
                    -- plain DELIVER frame with only `groupId`) overwrote the
                    -- stored name with `''`, so a name would appear and then
                    -- silently revert. Measured: a mutant deleting the whole
                    -- COALESCE survived all 508 tests, because nothing could
                    -- tell the two apart.
                    name = COALESCE(NULLIF(EXCLUDED.name, ''), groups.name),
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

    # -- health ------------------------------------------------------------
    def record_heartbeat(self, hb: dict) -> None:
        """Upsert THE single health row.

        🔴 Call this on a connection of its OWN, never the ingest connection.
        These connections run `autocommit=False`, so a heartbeat issued from the
        heartbeat thread on the shared connection would land inside whatever
        transaction the ingest thread has open — committing a half-written
        message batch, or being rolled back with it. `consumer.Heartbeat` opens
        its own `SignalDB` for exactly this reason.
        """
        with self._c.cursor() as cur:
            cur.execute(
                """
                INSERT INTO signal.consumer_health
                    (id, updated_at, pod, stream_connected, last_frame_at,
                     connections, reconnects, stored, malformed)
                VALUES (1, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET
                    updated_at       = EXCLUDED.updated_at,
                    pod              = EXCLUDED.pod,
                    stream_connected = EXCLUDED.stream_connected,
                    last_frame_at    = COALESCE(EXCLUDED.last_frame_at,
                                                signal.consumer_health.last_frame_at),
                    connections      = EXCLUDED.connections,
                    reconnects       = EXCLUDED.reconnects,
                    stored           = EXCLUDED.stored,
                    malformed        = EXCLUDED.malformed
                """,
                (int(hb["written_at_ms"]), hb.get("pod"),
                 bool(hb.get("stream_connected")),
                 None if hb.get("last_frame_at") is None else int(hb["last_frame_at"]),
                 int(hb.get("connections") or 0),
                 int(hb.get("reconnects") or 0), int(hb.get("stored") or 0),
                 int(hb.get("malformed") or 0)),
            )

    def read_heartbeat(self, now_ms: int | None = None) -> dict | None:
        """The health row, with `age_seconds` derived from `now_ms`.

        🔴 No `now()`/`EXTRACT` in the statement, deliberately. Those are the
        constructs the sqlite substrate refuses, and a statement no test can
        execute is how the TIMESTAMPTZ/float mismatch shipped. Portable SQL here
        buys an executing test, which is worth more than computing the age
        server-side.

        The trade is stated rather than hidden: `age_seconds` is measured against
        the READER's clock, so a reader whose clock has drifted from the writer's
        misjudges it. That is acceptable because the load-bearing liveness path
        never comes through here — the probe reads the FILE, written and read in
        the same process, where no skew is possible. This path is the human /
        deadman view.
        """
        with self._c.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT pod, stream_connected, connections, reconnects, stored, "
                "malformed, updated_at, last_frame_at "
                "FROM signal.consumer_health WHERE id = 1"
            )
            row = cur.fetchone()
            if row is None:
                return None
            out = dict(row)
            # 🔴 Normalise the boolean. psycopg2 hands back a real bool, sqlite
            # hands back 0/1, and a caller writing `is True` would then be
            # correct against one and silently wrong against the other. The
            # substrate exposed this — the API's type must not depend on which
            # driver answered.
            out["stream_connected"] = bool(out["stream_connected"])
            now = int(time.time() * 1000) if now_ms is None else int(now_ms)
            out["age_seconds"] = (now - int(out["updated_at"])) / 1000.0
            lf = out.get("last_frame_at")
            out["frame_age_seconds"] = None if lf is None else (now - int(lf)) / 1000.0
            return out

    # -- the mute list -----------------------------------------------------
    def exclude_group(self, group_id: bytes, *, note: str | None = None) -> None:
        """Mute a group by its Signal binary id. Idempotent; stores nothing else.

        Accepts a group that has never been seen — the row is the intent, and
        `not_excluded()` joins it to `signal.groups` only when one exists.
        """
        if not isinstance(group_id, (bytes, bytearray, memoryview)):
            raise TypeError("group_id must be the raw Signal group id as bytes")
        if not bytes(group_id):
            raise ValueError("group_id is empty — that would mute nothing, silently")
        with self._c.cursor() as cur:
            cur.execute(
                "INSERT INTO signal.excluded_groups (group_id, note) VALUES (%s, %s) "
                # COALESCE, not a bare assignment: `mute <id>` with no --note is
                # the natural way to re-issue a mute, and a bare assignment made
                # that WIPE the recorded reason — destroying the only record of
                # why, as a side effect of a command that changes nothing else.
                # Pass a new --note to replace it; omit it to keep what is there.
                "ON CONFLICT (group_id) DO UPDATE SET "
                "note = COALESCE(EXCLUDED.note, excluded_groups.note)",
                (bytes(group_id), note),   # raw bytes, exactly like upsert_group
            )

    def unexclude_group(self, group_id: bytes) -> int:
        """Un-mute. Returns rows removed (0 when it was not muted).

        This is the whole rollback story: the messages were never deleted, so
        removing the row restores the conversation exactly.
        """
        with self._c.cursor() as cur:
            cur.execute("DELETE FROM signal.excluded_groups WHERE group_id = %s",
                        (bytes(group_id),))
            return cur.rowcount

    def list_excluded_groups(self) -> list:
        """Muted groups, joined to `signal.groups` when the group is known.

        `group_row_id` is None for a group muted before it was ever seen — an
        ordinary state, not a dangling reference.
        """
        with self._c.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT x.group_id, x.note, gx.id AS group_row_id, gx.name AS group_name, "
                "(SELECT count(*) FROM signal.messages m WHERE m.group_id = gx.id) "
                "AS hidden_message_count "
                "FROM signal.excluded_groups x "
                "LEFT JOIN signal.groups gx ON gx.group_id = x.group_id "
                "ORDER BY gx.id"
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

        Muted groups are filtered INSIDE the inner aggregate, so their rows never
        reach `count(*)`/`max(timestamp)` at all. Filtering the outer query would
        give the same visible answer today and would quietly stop doing so the
        first time the grouping key widens.
        """
        with self._c.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                f"""
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
                    WHERE {not_excluded('m')}
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
        Muted groups (`signal.excluded_groups`) never appear in results.
        """
        with self._c.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                f"""
                SELECT m.id, m.message_timestamp, m.body, m.is_outbound,
                       c.display_name, c.phone_number
                FROM signal.messages m
                LEFT JOIN signal.contacts c ON c.id = m.source_contact_id
                WHERE m.search @@ websearch_to_tsquery('english', %s)
                  AND {not_excluded('m')}
                ORDER BY m.message_timestamp DESC
                LIMIT %s
                """,
                (query, limit),
            )
            return [dict(r) for r in cur.fetchall()]

    def get_message(self, message_id: int) -> dict | None:
        """One message by id, or None — including when it is MUTED.

        🔴 A muted message is indistinguishable from a nonexistent one here, and
        that is deliberate: "filter it out entirely" has to mean the id route
        too, or `search` hiding a row while `get_message` serves it in full is a
        filter with a hole in the shape of anyone who knows an id. The cost is
        that a muted id reads as "no such message"; the mute list is the place
        to look when that is surprising.
        """
        with self._c.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT m.id, m.message_timestamp, m.source_contact_id, "
                "m.dest_contact_id, m.message_type, m.body, m.is_outbound, "
                "m.send_state, m.approval_ref "
                f"FROM signal.messages m WHERE m.id = %s AND {not_excluded('m')}",
                (message_id,),
            )
            row = cur.fetchone()
            return dict(row) if row else None

    # -- the SPLIT send surface (D3) ---------------------------------------
    def draft_message(self, *, recipient: str, body: str,
                      self_uuid: str | None = None,
                      self_number: str | None = None,
                      approval_ref: str | None = None,
                      mentions: list | None = None,
                      provisional_timestamp: int | None = None) -> dict:
        """Compose and STORE an outbound draft. **Transmits nothing.**

        The draft is persisted immediately with `send_state='pending'` so it can
        never be silently lost, and with a NEGATIVE provisional timestamp — a real
        server timestamp is positive epoch-ms, so the provisional value can never
        be mistaken for one, and `send_approved()` overwrites it with the SERVER's
        (🔧 #4).

        🔴 A GROUP recipient resolves to a GROUP, exactly as the inbound path
        already does. `upsert_message()` calls `upsert_group()` and sets
        `messages.group_id`; this used to send EVERY recipient through
        `upsert_contact()` instead, which produced two silent faults at once:

          * `group_id` stayed NULL, so `_muted_predicate` — which keys on
            `m.group_id` — could not see the row. A draft to a MUTED group was
            returned in full by every read that believed it was filtering, and
            mute is the privacy control here. `idx_msg_group` and every
            group-scoped read missed our own sent messages too, so a group
            conversation read as inbound-only.
          * a PHANTOM CONTACT was created — a fake person whose `phone_number`
            was the group address — one per group ever drafted to.

        The phantom was load-bearing, which is why removing it needed
        `get_draft()` to derive the recipient from the group row FIRST (see the
        LEFT JOIN there): `send_approved()` reads the recipient string back out
        of the draft, so dropping the contact without rewiring that derivation
        would have left every group send addressed to `None`.
        """
        if not (self_uuid or self_number):
            raise ValueError("draft_message needs the sending account's uuid or number")
        source_id = self.upsert_contact(
            signal_uuid=self_uuid, phone_number=self_number, display_name="me",
        )
        dest_id = None
        group_row_id = None
        group_created = False
        if _looks_like_group_address(recipient):
            # `upsert_group`, not a lookup that refuses an unknown group: the
            # inbound path creates the row the same way, and refusing here would
            # make a group drafted to before its first message ever arrived
            # undraftable. A BAD address is still refused — `_group_address_to_id`
            # raises rather than inventing 32 bytes.
            gid = _group_address_to_id(recipient)
            # 🔴 SAY SO WHEN THIS MINTS A GROUP. The strict decoder rejects a
            # MALFORMED address; it cannot reject a canonically-encoded but WRONG
            # 32 bytes, which decodes perfectly, creates a group that never
            # existed, and sends into the void while reporting success. That is
            # the silent-zero shape `mute` was deliberately hardened against
            # (it EXITS 4 rather than report a mute that hides nothing).
            #
            # A warning, NOT a refusal: forward-only creation is deliberate and
            # correct — a group can legitimately be drafted to before its first
            # message has been ingested, and refusing would make that group
            # undraftable. Only the SILENCE was wrong. The row is written either
            # way; this just refuses to let it happen without saying so.
            group_created = not self.group_exists(gid)
            group_row_id = self.upsert_group(group_id=gid)
            if group_created:
                print(
                    f"WARNING: {recipient} matched NO stored group, so a new one "
                    f"was created (row {group_row_id}). If you expected an "
                    f"existing conversation, this address is wrong — a "
                    f"canonically-encoded but incorrect id decodes perfectly and "
                    f"cannot be caught by validation. The draft will send into a "
                    f"group nobody is in. Check `internal_id` against "
                    f"GET /v1/groups/<account> before approving. (If this group "
                    f"genuinely has not been seen yet, this is expected — the "
                    f"pipeline is forward-only.)",
                    file=sys.stderr)
        elif _looks_like_uuid(recipient):
            dest_id = self.upsert_contact(signal_uuid=recipient)
        elif _looks_like_bare_group_internal_id(recipient):
            # 🔴 THE `mute`/`draft` MIX-UP, REFUSED RATHER THAN DOCUMENTED.
            # `_looks_like_group_address` is a bare `group.` PREFIX test, so a
            # bare `internal_id` — the form `mute` takes — has no prefix, is not
            # a uuid, and used to fall through to `upsert_contact()`. That
            # recreated EXACTLY the defect this whole change exists to remove: a
            # phantom contact whose phone_number is a group id, `group_id` NULL,
            # no warning, exit 0 — and a row that no mute can ever see, because
            # `not_excluded()` keys on `group_id`. It was worse than the original
            # bug, because SKILL.md had begun telling operators these two
            # commands take different halves of the same value.
            #
            # Refusing is safe because the shapes cannot collide: a Signal
            # recipient is `+E164` or a uuid, and `_decode_internal_id` rejects
            # both (measured at both E.164 length extremes and for upper/lower
            # uuids — see test_no_REAL_recipient_shape_is_mistaken_for_a_group_id).
            # Only 24 or 44 characters of canonical base64 reach here.
            # Hand back the exact string that WOULD have worked: a refusal that
            # names the fix costs nothing and is the difference between a
            # correction and a dead end.
            from consumer import _decode_internal_id  # local import: no cycle

            correct = _group_id_to_address(_decode_internal_id(recipient))
            raise ValueError(
                f"{recipient!r} is a bare group `internal_id` — that is what "
                f"`mute` takes. `draft --to` needs the `id` form: {correct!r}. "
                f"Drafting to the bare form would silently create a CONTACT "
                f"whose phone number is a group id (the phantom-contact defect "
                f"this refusal exists to prevent), store the message with "
                f"group_id NULL, and put it beyond the reach of every mute."
            )
        else:
            dest_id = self.upsert_contact(phone_number=recipient)
        ts = provisional_timestamp if provisional_timestamp is not None \
            else -int(time.time() * 1000)
        if ts >= 0:
            raise ValueError(
                "a draft's provisional timestamp must be NEGATIVE so it cannot "
                "collide with a server-assigned timestamp (🔧 #4)"
            )
        # 🔴 Stored as TEXT-encoded JSON, never as a Python list handed to the
        # driver. psycopg2 adapts a `list` to a Postgres ARRAY, not to JSONB, so
        # passing it raw would raise `column "mentions" is of type jsonb but
        # expression is of type text[]` at runtime — and the sqlite substrate,
        # which stores JSONB as TEXT, could not see it. `None` stays NULL: a
        # mention-free draft must be indistinguishable from every draft written
        # before this column existed.
        mentions_json = json.dumps(list(mentions)) if mentions else None
        with self._c.cursor() as cur:
            cur.execute(
                """
                INSERT INTO signal.messages
                    (message_timestamp, source_contact_id, dest_contact_id,
                     group_id, message_type, body, is_outbound, send_state,
                     approval_ref, mentions)
                VALUES (%s, %s, %s, %s, 'draft', %s, true, %s, %s, %s)
                RETURNING id
                """,
                (ts, source_id, dest_id, group_row_id, body, STATE_PENDING,
                 approval_ref, mentions_json),
            )
            draft_id = _returned_id(cur, "draft_message")
        self._c.commit()
        return {
            "id": draft_id, "recipient": recipient, "body": body,
            "send_state": STATE_PENDING, "message_timestamp": ts,
            "source_contact_id": source_id, "dest_contact_id": dest_id,
            "group_id": group_row_id, "group_created": group_created,
            "approval_ref": approval_ref,
            "mentions": list(mentions) if mentions else [],
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
        # 🔴 RECORD WHAT IS BEING APPROVED, not merely THAT it was approved. The
        # digest is computed from the row we just read — the exact recipient,
        # body and mentions the clawgate card described — and
        # `_mint_send_authorization()` refuses to send a row that no longer
        # hashes to it. Written in the SAME statement as the state flip so an
        # approved draft can never exist without one.
        # 🔴 Over `recipient_identity(row)`, NOT `row["recipient"]` — see that
        # function: the printed recipient is a projection that background ingest
        # rewrites, and hashing it made an ordinary placeholder promotion look
        # like tampering.
        try:
            digest = draft_payload_digest(row)
        except _mentions.MentionError as exc:
            raise SendGateError(
                f"approval refused: draft {draft_id!r} carries an unreadable "
                f"`mentions` column ({exc}), so there is nothing coherent to "
                f"approve (D3 approval gate)") from exc
        with self._c.cursor() as cur:
            cur.execute(
                "UPDATE signal.messages SET send_state = %s, approval_ref = %s, "
                "approved_digest = %s WHERE id = %s",
                (STATE_APPROVED, approval_ref, digest, draft_id),
            )
        self._c.commit()
        return self._draft_or_raise(draft_id)

    def unapprove_draft(self, draft_id: int, *, note: str | None = None) -> dict:
        """Return an APPROVED draft to `pending` so it can be approved again.

        🔴 WHY THIS EXISTS — IT CLOSES A TERMINAL DEAD END. The approval digest
        makes `_mint_send_authorization()` refuse a draft whose payload changed
        after approval, and that refusal happens BEFORE `_claim_for_sending()`,
        so the row stays `approved`. But `approve_draft()` is `pending`-only and
        `reconcile_send()` is `sending`-only, and there was no third command:
        the refused draft was unsendable forever, and both the refusal text and
        SKILL.md told the operator to "re-approve it" — something the CLI had no
        way to do. The only route back was hand-editing the row, which is
        precisely the second writer the digest exists to catch.

        Gated on `SIGNAL_APPROVAL_TOKEN` **exactly as `approve_draft` is**: this
        un-does an operator decision, so it is an operator decision. It does NOT
        send, does not re-approve, and CLEARS `approved_digest` — a `pending`
        row must never carry a stale one, or the next `approve` would be checked
        against a digest it did not write.

        `note` is recorded on `approval_ref` when given, and COALESCEd like
        `reconcile_send`'s so the audit trail is added to, never erased.
        """
        if not os.environ.get(APPROVAL_TOKEN_ENV):
            raise SendGateError(
                f"unapprove refused: {APPROVAL_TOKEN_ENV} is not set. Withdrawing "
                f"an approval is the OPERATOR's step, on the same token as "
                f"`approve` — it is deliberately unavailable to the consumer "
                f"Deployment and to drafting agents (D3 approval gate)"
            )
        row = self._draft_or_raise(draft_id)
        if row["send_state"] != STATE_APPROVED:
            raise SendGateError(
                f"draft {draft_id!r} has send_state={row['send_state']!r}; only "
                f"{STATE_APPROVED!r} drafts may be unapproved. A draft in "
                f"{STATE_SENDING!r} is reconciled with `reconcile`, not here — "
                f"the POST was attempted and its outcome is unknown (D3 approval "
                f"gate)"
            )
        # The SAME guarded transition every other state move uses: names the
        # state it expects and reads the rowcount, so a concurrent `send` that
        # claimed the row first is told it lost rather than silently overwritten.
        self._transition(
            draft_id,
            "UPDATE signal.messages SET send_state = %s, approved_digest = NULL, "
            "approval_ref = COALESCE(%s, approval_ref) "
            "WHERE id = %s AND send_state = %s",
            (STATE_PENDING, note, draft_id, STATE_APPROVED),
            expected=STATE_APPROVED,
            what="withdraw the approval",
        )
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
        """One DRAFT by id — and `is_outbound` alone does not mean "draft".

        🔴 `send_state IS NOT NULL` is what makes this a draft surface. Without
        it the predicate was `m.is_outbound` alone, which also matches every
        device-sync ECHO of a message sent from the phone — and those DO carry a
        `group_id`, so this method returned a muted group's body in full while
        `get_message` correctly returned None for the same row. Nothing exploited
        it (every caller goes through `_draft_or_raise`, and the D3 gate rejects
        `send_state=None` before printing). A guard whose justification the code
        contradicts is one refactor away from being a real leak.

        🔴 THE EXEMPTION'S REASON CHANGED — read it, do not inherit it. The mute
        ledger used to exempt this method because "drafts have no group_id".
        Since `draft_message()` links a group draft to its group row that is NO
        LONGER TRUE, and the exemption now rests on a different, narrower claim:
        this is the OUTBOUND surface, and the only rows it can return are ones
        `send_state IS NOT NULL` — i.e. bodies the OPERATOR composed, never a
        third party's. Muting is about what you READ; a draft is what you WROTE.
        Filtering here would also be actively harmful: `approve_draft()`,
        `send_approved()` and `reconcile_send()` all reach this through
        `_draft_or_raise()`, so a mute landing mid-flight would strand a draft in
        `sending` with no route out and report it as "does not exist" — an
        accidental seventh refusal path through the D3 gate.

        What DOES have to hold, and is pinned by
        `test_group_exclusions.py::test_a_muted_group_draft_is_hidden_from_every_filtered_read`:
        every read that carries `not_excluded()` must hide a muted group's draft.
        Those are the surfaces that can surface someone else's conversation.
        """
        with self._c.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT m.id, m.message_timestamp, m.body, m.send_state,
                       m.approval_ref, m.source_contact_id, m.dest_contact_id,
                       m.mentions AS mentions, m.approved_digest,
                       -- `signal_uuid` is uuid, `phone_number` is text, and
                       -- Postgres type-checks the WHOLE CASE regardless of which
                       -- branch would run — so an uncast COALESCE here raises
                       -- DatatypeMismatch for EVERY draft, breaking approve(),
                       -- send() and reconcile() alike (they all reach this).
                       -- The hermetic suite cannot see it: its substrate is
                       -- SQLite, which is dynamically typed and accepts the
                       -- uncast form.
                       -- 🔴 Use ANSI `CAST(... AS text)`, NOT Postgres's `::`.
                       -- SQLite cannot parse `::`, so the pg-only spelling
                       -- turns 53 hermetic tests red — measured, not guessed.
                       -- The cast must satisfy BOTH engines or one of the two
                       -- gates goes blind.
                       CASE WHEN d.is_placeholder THEN d.phone_number
                            ELSE COALESCE(CAST(d.signal_uuid AS text), d.phone_number)
                       END AS recipient,
                       -- A GROUP draft has NO dest contact, so the CASE above is
                       -- NULL for it and the recipient is rebuilt in Python from
                       -- the group's binary id. It is not built in SQL because
                       -- the address is a DOUBLE base64 encoding, which neither
                       -- engine spells the same way — and doing it here would be
                       -- a second encoder alongside `_group_id_to_address`.
                       m.group_id AS group_id,
                       g.group_id AS group_signal_id
                FROM signal.messages m
                LEFT JOIN signal.contacts d ON d.id = m.dest_contact_id
                LEFT JOIN signal.groups g ON g.id = m.group_id
                WHERE m.id = %s AND m.is_outbound AND m.send_state IS NOT NULL
                """,
                (draft_id,),
            )
            row = cur.fetchone()
            if row is None:
                return None
            out = dict(row)
            # 🔴 The GROUP wins over the contact join, and the raw id never
            # escapes: a `memoryview`/`bytes` in the returned dict would reach
            # `json.dumps` in the CLI and the send payload alike.
            group_signal_id = out.pop("group_signal_id", None)
            if group_signal_id is not None:
                out["recipient"] = _group_id_to_address(group_signal_id)
            # 🔴 ALWAYS A LIST, never NULL and never a JSON string. psycopg2
            # decodes a real JSONB column to a Python list already; the sqlite
            # substrate (JSONB -> TEXT) hands back the raw string. Normalising
            # here means every caller — the send payload, the binding check, the
            # CLI's `json.dumps` — sees one shape, on both engines.
            out["mentions"] = _decode_mentions(out.get("mentions"))
            return out

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

        # 🔴 RE-READ, deliberately, and send THIS row — not the `draft` dict the
        # capability was minted from. Sending the minted dict would make the
        # binding in `spend_authorization()` a tautology: it would be comparing
        # the capability against the very values it was built from, and could
        # never disagree. Reading the row again is what turns the binding into a
        # real check on the approve→mutate→send window, because anything that
        # wrote to the row in between shows up HERE and nowhere else.
        live = self._draft_or_raise(draft_id)
        result = transmit(auth, recipient=live["recipient"], body=live["body"],
                          mentions=live.get("mentions") or [], number=number)
        # 🔴 The live server returns a LIST, one entry per recipient — measured
        # 2026-08-21 against signal-cli-rest-api in json-rpc mode:
        #     201  [{"timestamp":"1787331796630"}]
        # The previous `(result or {}).get("errors")` assumed a dict and raised
        # `AttributeError: 'list' object has no attribute 'get'` AFTER a
        # SUCCESSFUL send — the worst shape available, because a delivered
        # message reports as a failure and invites a duplicate resend. Normalise
        # first; refuse an unrecognised shape loudly rather than guessing.
        entries = _normalize_send_response(result)
        # The per-recipient errors are read BEFORE the timestamp: a response that
        # carries both an error and an unusable timestamp must report the ERROR,
        # which says what went wrong, not a timestamp complaint that hides it.
        # 🔴 Collect the errors payload WHOLE, never flattened. Upstream shapes it
        # as an object (`{"recipients": [{"message": "rate limited"}]}`) as well
        # as a list, and iterating an object yields its KEYS — which would report
        # `['recipients']` and silently discard the reason the operator needs to
        # reconcile. Pinned by
        # test_approval_gate::test_an_error_response_reports_the_ERROR_not_a_timestamp_complaint.
        errors = [entry["errors"] for entry in entries if entry.get("errors")]
        errors += [entry["error"] for entry in entries if entry.get("error")]
        if errors:
            # 🔴 Carry the server timestamp into the error. A partly-failed GROUP
            # send DID go out to the members that succeeded, and the reply
            # carried the timestamp `reconcile --sent --timestamp` needs. Without
            # it here the value is discarded and the operator has to hunt it in
            # the Signal thread — which is exactly the position draft 51 left
            # its operator in.
            ts_hint = entries[0].get("timestamp")
            raise RuntimeError(
                f"the Signal API reported per-recipient errors for draft "
                f"{draft_id!r}: {errors!r}; the draft stays in {STATE_SENDING!r} "
                f"for manual reconciliation — see `reconcile`"
                + (f". The response carried timestamp {ts_hint!r}; if the message "
                   f"DID reach the thread, reconcile with "
                   f"`--sent --timestamp {ts_hint}`" if ts_hint else "")
            )
        server_ts = _server_timestamp(entries[0])
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


# --------------------------------------------------------------------------- #
# GROUP ADDRESSES — the `group.` recipient the send endpoint takes
# --------------------------------------------------------------------------- #
# `/v2/send` addresses a group with the API's own `id` field, which is
# `group.` + base64(base64(raw 16- or 32-byte group id)) — a DOUBLE encoding, so
# the text after the prefix is base64 of the group's base64 `internal_id`. The
# inner value is exactly what `signal.groups.group_id` stores as BYTEA and what
# the mute list is keyed on, which is what lets an outbound draft join to the
# same group row the inbound path writes.
GROUP_ADDRESS_PREFIX = "group."


def _looks_like_group_address(recipient) -> bool:
    """True for a `group.`-prefixed recipient — a GROUP, not a person.

    Deliberately a prefix test and nothing more: whether the rest DECODES is
    `_group_address_to_id`'s job, and conflating the two would turn a malformed
    group address into a silently-created contact — the exact defect this
    replaces. A phone number or a uuid can never carry this prefix (`+` and hex
    digits only), so there is no ambiguity to resolve.
    """
    return isinstance(recipient, str) and recipient.startswith(GROUP_ADDRESS_PREFIX)


def _looks_like_bare_group_internal_id(recipient) -> bool:
    """True for the `mute` form of a group id — bare base64, no `group.` prefix.

    🔴 THIS EXISTS TO REFUSE, NOT TO RESOLVE. `mute` takes `internal_id` and
    `draft --to` takes `id` (the same bytes wrapped in a second base64 layer
    behind a `group.` prefix), so the two commands want opposite halves of one
    value and an operator WILL cross them. Without this, the bare form fell
    through to `upsert_contact(phone_number=...)` and recreated the phantom
    contact — silently, exit 0, with `group_id` NULL and therefore invisible to
    every mute.

    🔴 WHY THIS CANNOT SWALLOW A REAL RECIPIENT. It reuses the strict operator
    decoder, which requires a canonical base64 round-trip AND a 16- (GroupV1) or
    32-byte (GroupV2) result. A Signal recipient is `+E164` or a uuid: E.164 is
    at most 16 characters and never a multiple of 4, and a uuid's `-` separators
    put it at 27 bytes when urlsafe-folded — all rejected. Measured for both
    E.164 length extremes, upper- and lower-case uuids, a username and a display
    name in `test_no_REAL_recipient_shape_is_mistaken_for_a_group_id`, which is
    what keeps this claim true rather than merely argued. Callers must still
    check `_looks_like_uuid` FIRST; this is the last branch before the
    contact fallback for exactly that reason.
    """
    if not isinstance(recipient, str) or _looks_like_group_address(recipient):
        return False
    from consumer import _decode_internal_id  # local import: no cycle

    try:
        _decode_internal_id(recipient)
        return True
    except (ValueError, TypeError):
        return False


def _group_address_to_id(recipient: str) -> bytes:
    """`group.<base64(base64(raw))>` → the raw BYTEA group id.

    🔴 ONE RULE, ONE PLACE: the inner decode is `consumer._decode_internal_id`,
    the same strict reader the `mute` CLI uses, NOT a second base64 reader living
    here. It round-trips the encoding and demands 16 or 32 bytes, so a truncated
    paste or a display name is refused rather than resolved to some other 32
    bytes.

    🔴 WHAT THIS DOES **NOT** CATCH — stated because an earlier version of this
    docstring claimed it did. Validation rejects a MALFORMED address. It cannot
    reject a WRONG one: any canonically-encoded 32 bytes decodes perfectly, and
    there is no way from here to tell a real group id from a plausible one. Such
    an address reaches `upsert_group`, which CREATES the group, and the draft
    then sends into a conversation nobody is in — while reporting success.
    `draft_message()` therefore WARNS on stderr whenever it mints a
    previously-unseen group; that warning, not this function, is what covers the
    wrong-but-well-formed case. It is a warning rather than a refusal because
    drafting to a not-yet-ingested group is legitimate (the pipeline is
    forward-only), so refusing would make that group undraftable.

    Raises `ValueError` on anything that is not a canonical group address.
    """
    from consumer import _decode_internal_id  # local import: no cycle

    outer = recipient[len(GROUP_ADDRESS_PREFIX):]
    try:
        internal = base64.b64decode(outer, validate=True).decode("ascii")
    except Exception as exc:
        raise ValueError(
            f"{recipient!r} is not a group address: the text after "
            f"{GROUP_ADDRESS_PREFIX!r} must be base64 of the group's base64 "
            f"`internal_id` (the API's `id` field is double-encoded) — {exc}"
        ) from exc
    return _decode_internal_id(internal)


def _group_id_to_address(raw) -> str:
    """A raw BYTEA group id back to the `group.` recipient `/v2/send` takes.

    The inverse of `_group_address_to_id`, and the reason the phantom contact can
    be retired: the recipient string is DERIVED from `signal.groups.group_id`
    rather than read back out of a fake person whose `phone_number` was the
    address. `bytes()` because psycopg2 hands BYTEA back as a `memoryview` and
    the sqlite substrate as `bytes`.
    """
    return GROUP_ADDRESS_PREFIX + base64.b64encode(
        base64.b64encode(bytes(raw))).decode()
