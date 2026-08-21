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
                                                     (ns signal, json-rpc mode)
                                        │ WEBSOCKET  GET /v1/receive/{number}
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
| Routes | the three this module speaks (the server has many more): `GET /v1/receive/{number}` (a **websocket** in json-rpc mode), `GET /v1/attachments/{id}`, `POST /v2/send`. It has **no event-stream endpoint** — `/api/v1/events` belongs to AsamK's native daemon, a different server |
| Postgres | `mailbox-postgres-0` in ns `mailbox`, schema **`signal`** (shares the instance + role with `mail`) |
| Attachments | MinIO archive tenant, bucket `signal-attachments`, key `{conversation}/{YYYY-MM-DD}/{attachment_id}_{filename}` + a `.json` sidecar. The id is in the key deliberately — see gotchas |
| Tables | `signal.contacts`, `signal.groups`, `signal.messages`, `signal.attachments`, `signal.reactions`, `signal.consumer_health`, `signal.excluded_groups` |
| Search | `signal.messages.search` — a STORED `to_tsvector('english', body)` generated column with a GIN index |

## Commands (`python3 scripts/signal/consumer.py <cmd>`)

| Command | What it does |
|---|---|
| `run` | consume `/v1/receive/{number}` forever — this is what the Deployment runs |
| `health` | is the consumer alive? exit 0 healthy / 1 stale. Reads the local heartbeat FILE; `--from-db` reads the richer row instead (**not** for a probe — it depends on Postgres) |
| `conversations` | list conversations (group, else the DM peer), newest first |
| `search` | full-text search over message bodies (`websearch_to_tsquery`) |
| `draft` | compose an outbound draft — **stores it, transmits nothing** |
| `drafts` | list drafts with their `send_state` |
| `approve` | record Zach's clawgate approval for one pending draft |
| `send` | transmit an **approved** draft (refuses anything else) |
| `reconcile` | resolve a draft stranded in `sending`, after checking Signal yourself |
| `muted` | list muted groups and how many stored rows each one hides |
| `mute` | hide a group from every read — **stores and deletes nothing** |
| `unmute` | un-hide a muted group; restores it in full |

```bash
cd ~/workspace/devrc/scripts/signal
python3 consumer.py conversations --limit 10
python3 consumer.py search "harbour permit"
python3 consumer.py draft --to +15550100 --body "on my way"   # -> pending + a clawgate card
python3 consumer.py drafts --state pending
python3 consumer.py approve 42 --ref clawgate-task-91
python3 consumer.py send 42

# a draft stuck in `sending` — check Signal FIRST, then say which happened:
python3 consumer.py drafts --state sending
python3 consumer.py reconcile 42 --sent --timestamp 1723000009090   # it did go out
python3 consumer.py reconcile 42 --not-sent --note "no message in the thread"
```

## Muted groups

Some conversations are deliberately filtered out of every read. `signal.excluded_groups`
holds the mute list; **the rows are still there** — muting hides, it never deletes, so
`unmute` restores a conversation exactly. That is why it is the default: this pipeline is
forward-only, so a deleted message can never be re-fetched.

🔴 **It is keyed on the group's BINARY id, never the name** — `signal.groups.name` is
empty (`''`) for every group this consumer has stored, so a name-based filter would match
nothing while looking like it worked.

```bash
python3 consumer.py muted
python3 consumer.py mute <internal_id> --note "why"     # base64, from the API below
python3 consumer.py unmute <internal_id>
```

Get `internal_id` (base64) — the pipeline does not store group names, so this is the only
way to go from a name you recognise to the id the mute list wants:
```bash
kubectl -n signal exec deploy/signal-consumer -- python3 -c "
import os,json,urllib.request
api=os.environ['SIGNAL_API_URL'].rstrip('/'); acct=os.environ['SIGNAL_ACCOUNT']
for g in json.load(urllib.request.urlopen(api+'/v1/groups/'+acct,timeout=20)):
    print(g['internal_id'], repr(g['name']))"
```
Pass `internal_id`, **not** `id` — `id` is the same value wrapped in a `group.`-prefixed
second encoding, and `mute` refuses it rather than muting 32 bytes that match nothing.

🔴 **The filter lives in `SignalDB`'s read methods, so RAW `psql` BYPASSES IT COMPLETELY** —
an application predicate cannot bind a statement typed at a shell. So the body-printing
one-liners below **carry the predicate inline**, as `$MUTED`; a warning above them would
just be prose you scroll past while copying the query. Paste `$MUTED` into any new query
that selects a body. Muting also does **not** stop ingest: the consumer keeps storing that
group's messages, reactions and attachment bytes — it only stops READS returning them.

Direct SQL (the store is authoritative):
```bash
export KUBECONFIG=~/workspace/homelab-talos/homelab-kubeconfig
PSQL='kubectl -n mailbox exec mailbox-postgres-0 -- psql -U mailbox -d mailbox -c'
MUTED='not exists (select 1 from signal.excluded_groups x
        join signal.groups gx on gx.group_id=x.group_id where gx.id=m.group_id)'

$PSQL "select count(*), max(message_timestamp) from signal.messages;"   -- counts: unfiltered on purpose
$PSQL "select m.message_timestamp, c.display_name, left(m.body,60) from signal.messages m
       join signal.contacts c on c.id=m.source_contact_id
       where ${MUTED} order by m.message_timestamp desc limit 20;"
$PSQL "select m.id, m.body from signal.messages m
       where m.search @@ websearch_to_tsquery('english','permit') and ${MUTED};"
$PSQL "select count(*) from signal.reactions where message_id is null;"   -- unresolved
```

## The send path (D3)

`draft_message()` and `send_approved()` are deliberately split so **no single call
composes and transmits**.

1. `draft` writes the message with `is_outbound=true`, `send_state=pending` and a
   **negative provisional timestamp**, then posts a clawgate Task card (a graceful
   no-op returning `False` when `CLAWGATE_HOOK_TOKEN` is unset — the draft is already
   durable, so a missing token costs the notification, never the record).
2. Zach approves in clawgate; `approve <id> --ref <clawgate-ref>` records it. **This
   is the operator's command**, run from the operator's own shell — it needs
   `SIGNAL_APPROVAL_TOKEN`, which no agent environment carries.
3. `send` claims the draft (`send_state=sending`, committed **before** anything is
   transmitted), then mints a single-use `SendAuthorization` — which
   `_mint_send_authorization()` refuses to produce unless the stored `send_state` is
   exactly `approved` — and `consumer.transmit_approved()` refuses to post without
   one. The capability's constructor refuses direct construction; a spent capability
   cannot be replayed.

Every refusal raises **`SendGateError`**. `scripts/signal/tests/test_approval_gate.py`
attempts six documented bypasses and requires each to fail with that error.

🔴 **What D3 does and does not prove.** Structural: composing and transmitting are
separate calls with durable state in between, an un-approved draft has no code route
to the API, and a crash mid-send cannot resend. **Not** structural: nothing inside the
process can prove a *human* called `approve` — the token is a speed bump, not a proof
of humanity. Treat the approval record as an audit trail, and keep the token out of
every agent environment.

**A draft stuck in `sending`** means the POST was attempted and the outcome is
unknown (crash, dropped connection, or per-recipient errors from the API). It is
deliberately inert — nothing can mint from it — so it will never resend on its own,
and nothing but you can get it out again.

Check the Signal conversation, then record what you saw with **`reconcile`** (same
operator token as `approve`):
- `reconcile <id> --sent --timestamp <server-ts>` — it DID go out. Give the server
  timestamp from the conversation so the sync echo still dedupes (🔧 #4).
- `reconcile <id> --not-sent [--note "…"]` — it did not. Returns the draft to
  `pending`, so it needs a **fresh approval** before anything can send it: a retry
  never rides on the old one.

`--sent` refuses a missing or unusable timestamp rather than guessing, and
`reconcile` refuses any draft that is not `sending` — it is not a state editor.

## Event kinds the consumer emits

| Kind | Meaning |
|---|---|
| `message` | a 1:1 message (attachments, quotes, view-once and expiring messages are all this kind, with fields set) |
| `group_message` | a message carrying `groupInfo` |
| `reaction` | an emoji reaction (may arrive BEFORE its target — see gotchas). Covers BOTH directions: someone reacting to you, and 🔴 **your own reactions sent from the phone**, which arrive in the `syncMessage.sentMessage` wrapper and are stored with **the account itself** as `reactions.contact_id` |
| `edit` | an edit of an earlier message (`edit_target_timestamp` points at it) |
| `sync_outbound` | a message Zach sent from another linked device, echoed back |
| `receipt_delivery` | a delivery receipt |
| `receipt_read` | a read receipt |
| `typing` | a typing indicator |
| `remote_delete` | the sender RETRACTED a message — the target row is tombstoned (body and attachments removed), not stored as an empty message |
| `unknown` | a handled envelope shape we do not model — counted and retained, never dropped |

`message`, `group_message`, `edit`, `sync_outbound` and `reaction` are STORED; the rest
are counted only.

## Counters (`run` prints these as JSON)

| Counter | Meaning |
|---|---|
| `stored` | rows written (messages + reactions) |
| `ignored` | events observed but not stored (receipts, typing, unknown) |
| `malformed` | payloads skipped as unparseable — never fatal |
| `reconnects` | receive-stream drops recovered from |
| `db_retries` | transient Postgres faults retried rather than dropped |
| `db_recoveries` | aborted transactions rolled back (or connections re-opened) so the next event can be written |
| `db_recovery_failures` | recovery itself failed — the pod cannot write and needs looking at |
| `attachment_failures` | attachment fetch/upload failures — the MESSAGE row still stands |

## Liveness — how to tell working from dead

🔴 **Before this existed there was no such signal.** The consumer serves no HTTP,
had no probes, and emitted **0 log lines** across 20h of successful ingestion —
so a pod reaching nothing and a pod working perfectly were byte-identical from
outside, and the row count was the only evidence available. That is what made the
step-7 diagnosis take hours.

```bash
kubectl -n signal exec deploy/signal-consumer -- python3 /app/scripts/signal/consumer.py health
kubectl -n signal exec deploy/signal-consumer -- python3 /app/scripts/signal/consumer.py health --from-db --json
```

🔴 **Two sinks, and they answer different questions — do not swap them.**
- the **file** says "this process's thread is still scheduled". It depends on
  nothing external, which is why it is the only safe input to a k8s liveness
  probe. Keying a restart on Postgres reachability would turn a database blip
  into a restart storm against a consumer that was working fine.
- the **row** (`signal.consumer_health`) adds "…and Postgres is reachable", plus
  the counters. Richer, and it is ALLOWED to fail — a DB outage degrades the row
  and leaves liveness intact.

🔴 **NO MIGRATION EXISTS for `signal.consumer_health`, and `ensure_schema()`
will not tell you.** It uses `CREATE TABLE IF NOT EXISTS`, which is silent about
a table that already exists with the WRONG column types. A pre-release build
declared `updated_at`/`last_frame_at` as `TIMESTAMPTZ`; the shipped code writes
`BIGINT` epoch-ms. Against a database that ever ran that build, `ensure_schema()`
reports success, **every beat then fails forever** with `column … is of type
timestamp with time zone but expression is of type bigint`, and `health
--from-db` reports no heartbeat while the FILE probe stays green — so nothing
restarts and nothing alerts. Measured on real Postgres, not reasoned.
**The live homelab database is NOT affected** — verified: the table did not
exist there, so it is born `BIGINT`. If you hit this anywhere else (a dev box, a
rolled-back image), the fix is `DROP TABLE signal.consumer_health;` and let the
next beat recreate it — the table is a single disposable status row, so there is
nothing to preserve.

🔴 **DEPLOY PAIRING — the pod needs a WRITABLE heartbeat path or it will not
start.** `consumer.py run` beats once synchronously before entering the loop, and
an unwritable path is a configuration fault (loud, at startup) rather than
something laundered into a retry. The Deployment sets `readOnlyRootFilesystem:
true` and originally mounted **no volumes at all** — its own comment recorded
that the pod "writes nothing to the filesystem, which is what makes
readOnlyRootFilesystem viable". That invariant is now false. The manifest must
mount an `emptyDir` and point `SIGNAL_HEARTBEAT_PATH` at it **in the same
rollout as the image that introduces the heartbeat**, or the first rebuild
CrashLoopBackOffs a working consumer — before any probe is even wired.

🔴 **`last_frame_at` is diagnosis, NEVER a liveness input.** An idle account
legitimately sends nothing for hours; if silence fed the probe, a quiet weekend
would restart the pod on a loop.

⚠ **The probe restarts only a HUNG process** (stale heartbeat). It deliberately
does not restart on a reconnect storm: that is visible in `reconnects` on the
row, but automating a restart for it risks looping against an outage a restart
cannot fix. Read the row when the numbers look wrong.

## Environment

| Var | Purpose |
|---|---|
| `SIGNAL_PG_DSN` | Postgres DSN; falls back to the k8s secret `mailbox-postgres-auth` |
| `SIGNAL_PG_HOST` | set → connect DIRECTLY (in-cluster), no `kubectl port-forward` |
| `SIGNAL_PG_PORT` | port override for direct mode |
| `SIGNAL_PG_DIRECT` | truthy → direct mode using the DSN's own host/port |
| `SIGNAL_API_URL` | signal-cli-rest-api base URL (default `http://signal-api.signal.svc:8080`) |
| `SIGNAL_ACCOUNT` | the account's own phone number. Required TWICE: the receive endpoint is per-account (`/v1/receive/{number}`) and `/v2/send` rejects a missing `number` with 400 |
| `SIGNAL_APPROVAL_TOKEN` | must be set for `approve` to run. **Operator-only** — deliberately absent from the Deployment and from agent environments |
| `CLAWGATE_HOOK_TOKEN` | clawgate hook token. Resolved by `scripts/lib/clawgate_env.py` with `clawgatectl`'s precedence — `~/.claude/clawgate.env` FIRST, then this variable, which overrides it. Unresolvable → draft cards are a graceful no-op **that warns on stderr**; the draft is stored either way |
| `DEVRC_DIR` | fallback root for locating `scripts/lib/clawgate_env.py` when `clawgate.py` runs away from its sibling `lib/`. Normally unset — the sibling path wins |
| `SIGNAL_HEARTBEAT_PATH` | where the consumer writes its liveness file (default `/tmp/signal-consumer-heartbeat.json`). The k8s probe reads THIS, never Postgres |
| `SIGNAL_HEARTBEAT_INTERVAL` | seconds between heartbeats (default 30) |
| `SIGNAL_HEARTBEAT_MAX_AGE` | seconds before a heartbeat is stale (default 4× the interval — less than a few ticks flaps on scheduling jitter) |
| `MINIO_ARCHIVE_ENDPOINT` | explicit MinIO endpoint (skips the port-forward) |
| `MINIO_ARCHIVE_ACCESS_KEY` | MinIO credentials; else read from `minio-archive-config` |
| `MINIO_ARCHIVE_SECRET_KEY` | MinIO credentials; else read from `minio-archive-config` |

Off-cluster (workbench) the DB and MinIO layers open an ephemeral `kubectl port-forward`
and tear it down on exit — exactly like `mail-actions`.

## ⚠ Gotchas

- **`UNIQUE` does not dedupe over NULL in Postgres.** `messages.source_contact_id` is
  `NOT NULL` and an unresolved sender gets a **deterministic placeholder contact**
  (uuid5 of the identifier). Never relax that column — every redelivery from an
  unknown sender would insert a fresh row.
- **Outbound messages echo back via device sync.** The send path stores the
  **server-assigned** timestamp so `unique_message` catches the echo. A locally
  generated timestamp stores every sent message twice. Related: a draft addressed to a
  bare phone number creates a placeholder contact that is **promoted** to the real uuid
  when that person's envelopes arrive — without promotion the echo lands under a
  different contact and dedupe fails even with the right timestamp.
- **Reactions can precede their target message** (delivery is not ordered, and history is not
  backfilled). `reactions.message_id` is NULLable, resolved later, with the partial index
  `idx_rx_unresolved`. Unresolvable reactions are RETAINED, not dropped.
- 🔴 **Filtering reactions to "what people sent me" must exclude the account's own
  `contact_id`** — your own reactions live in the same table. Before 2026-08-18 they were
  being DROPPED entirely, so any analysis over reactions predating that is missing them.
- 🔴 **When you fix an own-device branch for one message shape, enumerate the others in
  that wrapper.** `syncMessage.sentMessage` carries `remoteDelete`, `reaction` and a plain
  `message`. The remote-delete case was fixed first and the reaction case was still missed
  for weeks — dropped reaction plus a bodyless ghost row in `signal.messages`. Found only by
  real traffic, after six PRs, four audit rounds and 387 green tests. Any UNMODELLED
  `sentMessage` variant with `message: None` (e.g. a nested `editMessage`, `sticker`,
  `payment`) still produces such a ghost row — that is open work, not a solved problem.
- **Redelivery duplicates attachments** without `UNIQUE (message_id,
  signal_attachment_id)`.
- **One person must be ONE contact row, in both arrival orders.** An identity arrives
  as a bare number (a draft) or as a uuid (every envelope). `upsert_contact` looks up
  by phone AND promotes a placeholder to its real uuid, because two rows for one
  person means the echo lands under a different `source_contact_id` and dedupe never
  fires — every agent-sent message stored twice.
- **The attachment id is part of the MinIO key.** Without it, two `Screenshot.png` in
  one conversation on one day collide, the second write is skipped as "already
  there", and its row points at the first file's bytes.
- **A failed statement poisons the whole transaction.** `autocommit=False` +
  Postgres = every later statement raises `InFailedSqlTransaction` until a rollback.
  The consumer recovers inside its retry (`db_recoveries`); without that the pod logs
  "reconnecting" forever and stores nothing.
- **Retention is NOT implemented.** `expires_in_seconds` and `view_once` are STORED
  and never acted on: a disappearing message stays in Postgres forever. `remote_delete`
  IS honoured (the target is tombstoned), but nothing sweeps expiry. If that matters,
  it is a separate piece of work — do not assume the archive respects Signal's timers.
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
