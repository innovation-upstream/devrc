# Proposal: Signal Chat Skill

**Date:** 2026-08-15
**Status:** Draft
**Author:** opencode session

---

## Problem

We have a self-hosted mail pipeline (`mailbox` skill) that receives, stores, queries, and automates over email. There is no equivalent for Signal — Zach's primary messaging app. Agent-driven Signal workflows (reading messages, replying, searching conversations, tracking reactions) currently require manual browser/phone interaction.

## Goal

Build a Signal chat skill that mirrors the mailbox skill pattern: **receive → store → query → automate → emit to activity pipeline**. Agents can query Signal conversations, search message history, send replies, and track attachments — all from the CLI.

## Architecture

```
Signal App (Zach's phone, linked as secondary device)
       │
       ▼
signal-cli-rest-api (Docker, json-rpc-native mode, K8s Deployment)
  │ SSE stream + REST API
  ▼
signal-consumer.py (Python, K8s Deployment)
  ├── Postgres (signal schema on mailbox-postgres-0)
  ├── MinIO (archive tenant, signal-attachments bucket)
  └── activity spool (source=signal)
```

### Component Mapping (Email → Signal)

| Email Pattern | Signal Equivalent |
|---|---|
| `aiosmtpd` receiver | `signal-cli-rest-api` in `json-rpc-native` mode |
| SMTP envelope parse | JSON envelope parse (Python consumer) |
| `mail` table | `signal.messages` + `signal.contacts` + `signal.groups` |
| `mail.labels` | `message_type` + `group_id` + future label system |
| `mail.search` (tsvector/GIN) | Same: `to_tsvector('english', body)` |
| `_db.py` (shared DB layer) | `_signal_db.py` (same pattern, same Postgres instance) |
| `MinioArchive` (invoice archiver) | `MinioSignal` (attachment archiver) |
| Sent-mail poller (IMAP) | `signal-cli send` + store outbound in same table |
| `emit` (activity spool) | `activity_emit.py` → v1 lines to spool |

## Components

### 1. Flux Manifests — `clusters/homelab/apps/signal/`

| File | Purpose |
|---|---|
| `kustomization.yaml` | Flux Kustomization |
| `namespace.yaml` | `signal` namespace |
| `deployment.yaml` | `bbernhard/signal-cli-rest-api:latest`, `json-rpc-native` mode, 1 replica |
| `service.yaml` | ClusterIP `signal-api.signal.svc:8080` |
| `pvc.yaml` | 1Gi `openebs-nvme-1tb` for signal-cli state |
| `configmap-schema.yaml` | `signal` schema SQL for init job |
| `secrets.enc.yaml` | SOPS-encrypted Postgres DSN |

Parent Flux Kustomization added to `root-kustomizations/system/signal.yaml`.

### 2. Consumer — `scripts/signal/consumer.py`

SSE-driven daemon. Pseudocode:

```python
class SignalConsumer:
    def run(self):
        with SignalDB() as db:
            db.ensure_schema()
            for event in sse_stream(f"{API_URL}/api/v1/events"):
                if event.method == "receive":
                    msg = parse_envelope(event.params.envelope)
                    db.insert_message(msg)
                    download_attachments(msg)  # → MinIO
                    emit_activity(msg)          # → spool
```

Responsibilities:
- Consume SSE stream from signal-cli-rest-api
- Parse Signal envelope JSON → structured message
- Download attachments via `GET /api/v1/attachments/{id}` → upload to MinIO
- Store message in Postgres via `_signal_db.py`
- Emit activity event to spool for every message

### 3. DB Layer — `scripts/signal/_signal_db.py`

Clone of `scripts/mail-actions/_db.py`:
- Same `SignalDB` context manager pattern
- Port-forward vs direct connection (`SIGNAL_PG_HOST` / `SIGNAL_PG_DIRECT`)
- Namespace `signal`, same Postgres instance (`mailbox-postgres-0`)
- Methods: `ensure_schema()`, `insert_message()`, `insert_contact()`, `insert_reaction()`, `list_conversations()`, `search()`, `send_message()`

### 4. Postgres Schema — `signal` Schema

Separate schema on `mailbox-postgres-0` (same instance as `mail`):

```sql
CREATE SCHEMA IF NOT EXISTS signal;

CREATE TABLE signal.contacts (
    id SERIAL PRIMARY KEY,
    signal_uuid UUID UNIQUE NOT NULL,
    phone_number TEXT,
    display_name TEXT,
    profile_name TEXT,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE signal.groups (
    id SERIAL PRIMARY KEY,
    group_id BYTEA UNIQUE NOT NULL,
    name TEXT NOT NULL,
    revision INTEGER DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE signal.messages (
    id BIGSERIAL PRIMARY KEY,
    message_timestamp BIGINT NOT NULL,
    server_received_at BIGINT,
    server_delivered_at BIGINT,
    source_contact_id INTEGER REFERENCES signal.contacts(id),
    message_type TEXT NOT NULL,
    body TEXT,
    expires_in_seconds INTEGER,
    view_once BOOLEAN DEFAULT false,
    edit_target_timestamp BIGINT,
    group_id INTEGER REFERENCES signal.groups(id),
    is_outbound BOOLEAN DEFAULT false,
    raw_envelope JSONB,
    search tsvector GENERATED ALWAYS AS (to_tsvector('english', coalesce(body, ''))) STORED,
    CONSTRAINT unique_message UNIQUE (source_contact_id, message_timestamp)
);

CREATE TABLE signal.attachments (
    id BIGSERIAL PRIMARY KEY,
    message_id BIGINT REFERENCES signal.messages(id) ON DELETE CASCADE,
    content_type TEXT NOT NULL,
    filename TEXT,
    size_bytes BIGINT,
    caption TEXT,
    is_voice_note BOOLEAN DEFAULT false,
    minio_bucket TEXT,
    minio_key TEXT,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE signal.reactions (
    id BIGSERIAL PRIMARY KEY,
    message_id BIGINT REFERENCES signal.messages(id) ON DELETE CASCADE,
    emoji TEXT NOT NULL,
    contact_id INTEGER REFERENCES signal.contacts(id),
    target_sent_timestamp BIGINT NOT NULL,
    is_remove BOOLEAN DEFAULT false
);

-- Indexes
CREATE INDEX idx_msg_ts ON signal.messages(message_timestamp DESC);
CREATE INDEX idx_msg_dm ON signal.messages(source_contact_id, message_timestamp);
CREATE INDEX idx_msg_group ON signal.messages(group_id, message_timestamp) WHERE group_id IS NOT NULL;
CREATE INDEX idx_msg_fts ON signal.messages USING GIN(search);
CREATE INDEX idx_att_msg ON signal.attachments(message_id);
```

### 5. Attachment Storage — `scripts/signal/_minio.py`

Clone of `scripts/mail-actions/_minio.py` targeting archive tenant:
- Bucket: `signal-attachments` (auto-created on first use)
- Key: `{contact_or_group}/{YYYY-MM-DD}/{filename}`
- Sidecar JSON: `{filename}.json` with contact, timestamp, message_id, content_type

### 6. Activity Pipeline — `scripts/signal/activity_emit.py`

Appends v1-format lines to the activity spool:

```python
emit(
    source="signal",
    kind="message",
    text=f"{'in' if not is_outbound else 'out'} {contact_name}: {body_preview}",
    project=group_name or contact_name,
    payload=json.dumps({
        "contact": contact_name,
        "group": group_name,
        "has_attachments": bool(attachments),
        "message_type": message_type,
    })
)
```

Add `"signal"` to `EXPECTED_SOURCES` in `scripts/validation/invariants.py`.

### 7. Skill File — `claude/skills/signal/SKILL.md`

Same structure as `claude/skills/mailbox/SKILL.md`:
- Key facts table
- Query examples (FTS, conversation listing, recent messages)
- Send example (via signal-cli REST API)
- Gotchas section

## Key Design Decisions

| Decision | Rationale |
|---|---|
| `signal-cli-rest-api` over raw `signal-cli` | Docker-ready, REST+SSE, MIT, 2.8k stars, `json-rpc-native` mode avoids JVM startup per request |
| Same Postgres instance as mailbox | Single DB to manage, port-forward already works, `_db.py` pattern is proven |
| Separate `signal` schema | Isolation from `mail` schema, independent migrations, shared instance |
| MinIO archive tenant for attachments | Same pattern as invoice archiver, already deployed and operational |
| `json-rpc-native` mode over `json-rpc` | Persistent daemon, lower memory, no subprocess per request |
| 1 replica (always) | Signal key material conflicts on multi-instance — non-negotiable |
| Link as secondary device | Keeps Zach's phone as primary, bot occupies 1 of 5 linked device slots |

## Deployment Sequence

1. Register phone — link as secondary device via QR code scan
2. Deploy `signal-cli-rest-api` — PVC + Deployment + Service via Flux
3. Create Postgres schema — `CREATE SCHEMA signal` + tables init
4. Build + deploy consumer — SSE consumer as K8s Deployment
5. Wire activity pipeline — emit events, add to `EXPECTED_SOURCES`
6. Write skill — `SKILL.md` with query/send examples
7. Verify — send test message from another device, check Postgres + MinIO + spool

## Gotchas

| Gotcha | Mitigation |
|---|---|
| signal-cli breaks if >3 months old (protocol rotates) | Pin version in Deployment, monthly update cadence |
| 1 replica max — key conflicts on multi-instance | `replicas: 1` hard constraint |
| Must `receive` regularly or account flagged spam | CronJob calling `/api/v1/receive` every 6h |
| Cloud IPs may be pre-flagged | Use home IP for initial registration |
| Max 5 linked devices per phone | Uses 1 slot — dedicated number if scaling needed |
| Rate limits undocumented (~100 msgs/day to new contacts) | Exponential backoff, respect `Retry-After` |
| Read receipts / typing indicators DBus-only | Accept limitation, no REST equivalent |
| Postgres shared with mailbox | Separate schema, distinct namespace, no collision |

## Open Questions

| Question | Options |
|---|---|
| Consumer runtime | K8s Deployment (always-on) vs CronJob (poll every N min)? |
| Send workflow | Agent direct-send, or Zach-reviewed only? |
| Group capture | All groups, or configurable allowlist? |
| Activity pipeline depth | Just message events, or also reactions/edits/group-changes? |
| Phone number | Existing personal, or dedicated bot number? |

## Estimated Effort

| Component | Size |
|---|---|
| Flux manifests | Small (copy + adapt mailbox pattern) |
| Consumer (SSE → parse → store) | Medium (~300-400 lines) |
| `_signal_db.py` | Small (clone `_db.py`, adapt methods) |
| Postgres schema | Small (SQL above, run once) |
| `_minio.py` | Small (clone `_minio.py`, change bucket) |
| Activity emit | Trivial (~30 lines) |
| Skill file | Small (clone mailbox skill, rewrite for Signal) |
| Testing + iteration | Medium (SSE edge cases, attachment paths, group messages) |

**Total: ~1-2 days of focused work** after the phone is linked.

## References

- [signal-cli](https://github.com/AsamK/signal-cli) — 4.9k stars, GPLv3, v0.14.7
- [signal-cli-rest-api](https://github.com/bbernhard/signal-cli-rest-api) — 2.8k stars, MIT
- [signal-cli-rest-api docs](https://bbernhard.github.io/signal-cli-rest-api/) — Swagger, config, Docker usage
- [signalbot](https://github.com/sergdort/signalbot) — Python framework (decorator-based handlers)
