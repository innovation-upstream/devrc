---
name: signal
description: "Query and operate the self-hosted Signal chat pipeline (Zach's phone -> signal-cli-rest-api -> homelab Postgres `signal` schema + MinIO attachments), and DRAFT outbound replies for clawgate approval. Use for: my Signal messages, signal chat, who messaged me on Signal, search my Signal history, Signal conversations/groups/reactions/attachments, the signal-consumer or signal-api pod, \"draft a Signal reply\", \"text someone on Signal\". Email and the mail inbox are the sibling `mailbox` skill, not this one."
---

# Signal chat operations

Signal messages land in a durable, queryable Postgres store the same way mail does —
receive → store → query → automate. Same Postgres instance as the mailbox, a separate
`signal` schema.

```
Zach's phone (Signal)  ──linked secondary device──►  signal-cli-rest-api
                                                     (ns signal, json-rpc-native)
                                                          │ SSE /api/v1/events
                                                          ▼
                                             scripts/signal/consumer.py
                                          ├─► Postgres  signal.*  (mailbox-postgres-0)
                                          └─► MinIO     signal-attachments
```

🔴 **Outbound is DRAFT → APPROVE → SEND, never direct-send** (decision D3). See
[The send path](#the-send-path-d3) — the gate is structural, so "just send it" has no
code route and will raise `SendGateError`.

## Key facts (verify against live state before asserting)

| Thing | Value |
|---|---|
| Code | `~/workspace/devrc/scripts/signal/` — `consumer.py` (CLI + daemon), `_signal_db.py`, `_minio.py`, `clawgate.py` |
| Tests | `scripts/signal/tests/` — hermetic (sqlite substrate + fixtures), in the pytest gate |
| Manifests | `homelab-talos: clusters/homelab/apps/signal/`, parent ks `clusters/homelab/flux-system/root-kustomizations/system/signal.yaml` |
| API | `signal-cli-rest-api`, ns `signal`, ClusterIP `signal-api.signal.svc:8080`, **pinned tag** (never `:latest`), `replicas: 1` |
| Postgres | `mailbox-postgres-0` in ns `mailbox`, schema **`signal`** (shares the instance + role with `mail`) |
| Attachments | MinIO archive tenant, bucket `signal-attachments`, key `{conversation}/{YYYY-MM-DD}/{filename}` + a `.json` sidecar |
| Tables | `signal.contacts`, `signal.groups`, `signal.messages`, `signal.attachments`, `signal.reactions` |
| Search | `signal.messages.search` — a STORED `to_tsvector('english', body)` generated column with a GIN index |

## Commands (`python3 scripts/signal/consumer.py <cmd>`)

| Command | What it does |
|---|---|
| `run` | consume the SSE stream forever — this is what the Deployment runs |
| `conversations` | list conversations (group, else the DM peer), newest first |
| `search` | full-text search over message bodies (`websearch_to_tsquery`) |
| `draft` | compose an outbound draft — **stores it, transmits nothing** |
| `drafts` | list drafts with their `send_state` |
| `approve` | record Zach's clawgate approval for one pending draft |
| `send` | transmit an **approved** draft (refuses anything else) |

```bash
cd ~/workspace/devrc/scripts/signal
python3 consumer.py conversations --limit 10
python3 consumer.py search "harbour permit"
python3 consumer.py draft --to +15550100 --body "on my way"   # -> pending + a clawgate card
python3 consumer.py drafts --state pending
python3 consumer.py approve 42 --ref clawgate-task-91
python3 consumer.py send 42
```

Direct SQL (the store is authoritative):
```bash
export KUBECONFIG=~/workspace/homelab-talos/homelab-kubeconfig
PSQL='kubectl -n mailbox exec mailbox-postgres-0 -- psql -U mailbox -d mailbox -c'
$PSQL "select count(*), max(message_timestamp) from signal.messages;"
$PSQL "select m.message_timestamp, c.display_name, left(m.body,60) from signal.messages m
       join signal.contacts c on c.id=m.source_contact_id order by m.message_timestamp desc limit 20;"
$PSQL "select id, body from signal.messages where search @@ websearch_to_tsquery('english','permit');"
$PSQL "select count(*) from signal.reactions where message_id is null;"   -- unresolved
```

## The send path (D3)

`draft_message()` and `send_approved()` are deliberately split so **no single call
composes and transmits**.

1. `draft` writes the message with `is_outbound=true`, `send_state=pending` and a
   **negative provisional timestamp**, then posts a clawgate Task card (a graceful
   no-op returning `False` when `CLAWGATE_HOOK_TOKEN` is unset — the draft is already
   durable, so a missing token costs the notification, never the record).
2. Zach approves in clawgate; `approve <id> --ref <clawgate-ref>` records it.
3. `send` mints a single-use `SendAuthorization` — which `_mint_send_authorization()`
   refuses to produce unless the stored `send_state` is exactly `approved` — and
   `consumer.transmit_approved()` refuses to post without one. The capability's
   constructor refuses direct construction; a spent capability cannot be replayed.

Every refusal raises **`SendGateError`**. `scripts/signal/tests/test_approval_gate.py`
attempts six documented bypasses and requires each to fail with that error.

## Event kinds the consumer emits

| Kind | Meaning |
|---|---|
| `message` | a 1:1 message (attachments, quotes, view-once and expiring messages are all this kind, with fields set) |
| `group_message` | a message carrying `groupInfo` |
| `reaction` | an emoji reaction (may arrive BEFORE its target — see gotchas) |
| `edit` | an edit of an earlier message (`edit_target_timestamp` points at it) |
| `sync_outbound` | a message Zach sent from another linked device, echoed back |
| `receipt_delivery` | a delivery receipt |
| `receipt_read` | a read receipt |
| `typing` | a typing indicator |
| `unknown` | a handled envelope shape we do not model — counted and retained, never dropped |

`message`, `group_message`, `edit`, `sync_outbound` and `reaction` are STORED; the rest
are counted only.

## Counters (`run` prints these as JSON)

| Counter | Meaning |
|---|---|
| `stored` | rows written (messages + reactions) |
| `ignored` | events observed but not stored (receipts, typing, unknown) |
| `malformed` | payloads skipped as unparseable — never fatal |
| `reconnects` | SSE stream drops recovered from |
| `db_retries` | transient Postgres faults retried rather than dropped |
| `attachment_failures` | attachment fetch/upload failures — the MESSAGE row still stands |

## Environment

| Var | Purpose |
|---|---|
| `SIGNAL_PG_DSN` | Postgres DSN; falls back to the k8s secret `mailbox-postgres-auth` |
| `SIGNAL_PG_HOST` | set → connect DIRECTLY (in-cluster), no `kubectl port-forward` |
| `SIGNAL_PG_PORT` | port override for direct mode |
| `SIGNAL_PG_DIRECT` | truthy → direct mode using the DSN's own host/port |
| `SIGNAL_API_URL` | signal-cli-rest-api base URL (default `http://signal-api.signal.svc:8080`) |
| `SIGNAL_ACCOUNT` | the account's own phone number, used as the sender of a draft |
| `CLAWGATE_HOOK_TOKEN` | clawgate hook token; unset → draft cards are a graceful no-op |
| `MINIO_ARCHIVE_ENDPOINT` | explicit MinIO endpoint (skips the port-forward) |
| `MINIO_ARCHIVE_ACCESS_KEY` | MinIO credentials; else read from `minio-archive-config` |
| `MINIO_ARCHIVE_SECRET_KEY` | MinIO credentials; else read from `minio-archive-config` |

Off-cluster (workbench) the DB and MinIO layers open an ephemeral `kubectl port-forward`
and tear it down on exit — exactly like `mail-actions`.

## ⚠ Gotchas

- **`UNIQUE` does not dedupe over NULL in Postgres.** `messages.source_contact_id` is
  `NOT NULL` and an unresolved sender gets a **deterministic placeholder contact**
  (uuid5 of the identifier). Never relax that column — every SSE redelivery from an
  unknown sender would insert a fresh row.
- **Outbound messages echo back via device sync.** The send path stores the
  **server-assigned** timestamp so `unique_message` catches the echo. A locally
  generated timestamp stores every sent message twice. Related: a draft addressed to a
  bare phone number creates a placeholder contact that is **promoted** to the real uuid
  when that person's envelopes arrive — without promotion the echo lands under a
  different contact and dedupe fails even with the right timestamp.
- **Reactions can precede their target message** (SSE is not ordered, and history is not
  backfilled). `reactions.message_id` is NULLable, resolved later, with the partial index
  `idx_rx_unresolved`. Unresolvable reactions are RETAINED, not dropped.
- **Redelivery duplicates attachments** without `UNIQUE (message_id,
  signal_attachment_id)`.
- **Attachments are fetched AFTER the message row is committed** — a failed download
  must never roll back a message. `minio_key IS NULL` is the honest marker for "row
  stored, bytes not archived".
- **`replicas: 1`, hard.** signal-cli key material conflicts across instances.
- **signal-cli breaks if >3 months old** (the protocol rotates) — the tag is pinned
  deliberately, so bump it monthly rather than floating on `:latest`.
- **The account must `receive` regularly** or it gets flagged as spam; register from the
  home IP, and it uses one of five linked-device slots.
- **Read receipts and typing indicators are DBus-only upstream** — the REST API surfaces
  them in the stream but there is no send equivalent.
- **Capture is total by default** (decision D1: full bodies + attachments). A group
  allowlist is the natural mitigation if that is ever revisited.
