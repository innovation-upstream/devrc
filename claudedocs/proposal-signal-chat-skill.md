# Proposal: Signal Chat Skill

**Date:** 2026-08-15 (draft, opencode session) · **Revised:** 2026-08-16 after evaluation against the tree
**Status:** Ready to dispatch — all three blocking decisions resolved 2026-08-16 (§Decisions)

---

## Problem

We have a self-hosted mail pipeline (`mailbox` skill) that receives, stores, queries, and
automates over email. There is no equivalent for Signal — Zach's primary messaging app.
Agent-driven Signal workflows (reading messages, replying, searching conversations, tracking
reactions) currently require manual browser/phone interaction.

## Goal

Build a Signal chat skill that mirrors the mailbox skill pattern: **receive → store → query →
automate**. Agents can query Signal conversations, search message history, send replies, and
track attachments — all from the CLI.

🔴 **The original draft's fifth stage, "emit to activity pipeline", is CUT from v1.** See
§Cut from v1 for why, and what it would take to revisit.

## What changed in this revision

Six findings from checking the draft against `devrc` and `homelab-talos`. Every one is
verified in-tree; the file:line evidence is in the bullets.

1. 🔴 **§6 (activity emission) was architecturally impossible as written — CUT.** The spool is
   `~/.local/state/activity/spool` (`scripts/collector/collector.py:18,36`), a **per-host local
   directory** drained by a per-host collector. Every existing emitter runs host-side (keylog,
   i3, browser-ext receiver, claude session-tailer, browser-bridge server, `invocation.py`).
   The draft deployed the consumer as a K8s Deployment *and* had it call `emit_activity(msg)`.
   Both cannot be true. Downstream, `EXPECTED_HOSTS = {"workbench","laptop"}` is **enforced**
   (`scripts/validation/invariants.py:61,340` via `eval_unexpected_set`), so an in-cluster
   emitter either trips the host invariant or has to lie about its host.
2. 🔴 **The draft's Email→Signal mapping row for activity was fabricated symmetry.** The left
   column is "Email Pattern", and that row claimed one existed. **Mailbox emits nothing to the
   activity pipeline** — grepping `scripts/mail-actions/*.py` for activity/spool/emit returns
   only `emit_task` to *clawgate*. (The pattern matches those clawgate lines, so this is a real
   zero, not a broken grep.) So §6 had no analogue to clone, and its "Trivial (~30 lines)"
   estimate described code with no working precedent in the repo.
3. 🔴 **"Add `signal` to `EXPECTED_SOURCES`" was never a one-liner — it enrolls the source in
   the deadman.** `scripts/collector/deadman.py` evaluates per `(source, host)` pairs against a
   2-active-hour floor / 48-active-hour cap, and `deadman.py:268` carries an explicit 🔴: a
   source a human drives directly must also go in `PRESENCE_SOURCES` (`deadman.py:276`).
   Outbound Signal is human-driven; inbound is not — and the draft emitted both under one
   source name. Un-navigated that goes one of two bad ways: a quiet day reads as "signal is
   DEAD", or an inbound message at 03:00 marks Zach present and corrupts active-time
   accounting for **every other source**.
4. **The Flux parent-kustomization path did not exist.** Real path is
   `clusters/homelab/flux-system/root-kustomizations/system/mailbox.yaml`; the draft wrote
   `root-kustomizations/system/signal.yaml`. Corrected in §1.
5. **The work spans two repos and the draft did not say so.** §1 lands in `homelab-talos`,
   where committing to trunk **is** deploying (its own `CLAUDE.md` declares it). Everything
   else is `devrc`, which is feature-branch-and-PR. Two PRs, two repos, an ordering
   dependency, different git rules. Now stated in §Deployment Sequence.
6. **The one irreversible decision was missing from the decisions table.** Now §Decisions
   Required.

Additionally, four **schema defects** found while reviewing the DDL — all now fixed in §4 and
each one is a named test in §Test Plan: the `UNIQUE`/NULL idempotency hole, the reaction FK
ordering trap, unconstrained attachment re-delivery, and the outbound sync echo.

## Verified sound (checked, not assumed)

- `scripts/mail-actions/_db.py` (20.8 KB) and `_minio.py` (7.7 KB) exist — the clone targets
  are real.
- `mailbox-postgres` StatefulSet → pod **`mailbox-postgres-0`** ✓ (`postgres.yaml:22,24`).
- `openebs-nvme-1tb` is a real storage class and the most-used one in the cluster (13 uses) ✓.
- `clusters/homelab/apps/mailbox/` has almost exactly the file shape §1 copies.
- No `scripts/signal/` or `claude/skills/signal/` — clean greenfield.

## Architecture (v1)

```
Signal App (Zach's phone, linked as secondary device)
       │
       ▼
signal-cli-rest-api (Docker, json-rpc-native mode, K8s Deployment, replicas: 1)
  │ SSE stream + REST API
  ▼
signal-consumer.py (Python, K8s Deployment)
  ├── Postgres (signal schema on mailbox-postgres-0)
  └── MinIO (archive tenant, signal-attachments bucket)
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

*(The draft's eighth row, `emit` → activity spool, is removed: it had no left-hand side.)*

## Components

### 1. Flux Manifests — `homelab-talos: clusters/homelab/apps/signal/`

| File | Purpose |
|---|---|
| `kustomization.yaml` | Flux Kustomization |
| `namespace.yaml` | `signal` namespace |
| `deployment.yaml` | `bbernhard/signal-cli-rest-api`, **pinned tag** (not `:latest`), `json-rpc-native`, `replicas: 1` |
| `service.yaml` | ClusterIP `signal-api.signal.svc:8080` |
| `pvc.yaml` | 1Gi `openebs-nvme-1tb` for signal-cli state |
| `configmap-schema.yaml` | `signal` schema SQL for the init job |
| `secrets.enc.yaml` | SOPS-encrypted Postgres DSN |
| `consumer.yaml` | the consumer Deployment (image built from `devrc: scripts/signal/`) |

Parent Flux Kustomization at **`clusters/homelab/flux-system/root-kustomizations/system/signal.yaml`**
(corrected path — mirror `mailbox.yaml` there).

⚠ `:latest` is replaced with a pinned tag deliberately: the draft's own gotcha table says
signal-cli breaks if >3 months old, which is an argument for a *deliberate* monthly bump, not
for floating.

### 2. Consumer — `devrc: scripts/signal/consumer.py`

SSE-driven daemon.

```python
class SignalConsumer:
    def run(self):
        with SignalDB() as db:
            db.ensure_schema()
            for event in sse_stream(f"{API_URL}/api/v1/events"):
                if event.method == "receive":
                    msg = parse_envelope(event.params.envelope)
                    db.upsert_message(msg)       # idempotent — see §4
                    download_attachments(msg)    # → MinIO, idempotent
```

Responsibilities: consume the SSE stream · parse envelope JSON → structured message · download
attachments via `GET /api/v1/attachments/{id}` → MinIO · store via `_signal_db.py`.

**Must survive:** SSE disconnect/reconnect with redelivery, a malformed event, Postgres
briefly unavailable, and an attachment fetch failing independently of the message write.

### 3. DB Layer — `devrc: scripts/signal/_signal_db.py`

Clone of `scripts/mail-actions/_db.py`: same context-manager pattern, port-forward vs direct
(`SIGNAL_PG_HOST` / `SIGNAL_PG_DIRECT`), namespace `signal`, same Postgres instance.
Methods: `ensure_schema()`, `upsert_message()`, `upsert_contact()`, `upsert_reaction()`,
`list_conversations()`, `search()`, `draft_message()`, `send_approved()` — the send surface is
split in two by D3 (§7), so that no single call composes *and* transmits.

### 4. Postgres Schema — `signal` Schema

Separate schema on `mailbox-postgres-0`. **Four corrections against the draft DDL, marked 🔧.**

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
    -- 🔧 NOT NULL: a UNIQUE over a NULLable column does NOT dedupe in Postgres
    -- (NULLs compare distinct), so an envelope from an unresolved contact would
    -- insert a fresh row on every SSE redelivery. Unknown senders get a
    -- placeholder contact row instead.
    source_contact_id INTEGER NOT NULL REFERENCES signal.contacts(id),
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
    signal_attachment_id TEXT NOT NULL,   -- 🔧 the API's own id
    content_type TEXT NOT NULL,
    filename TEXT,
    size_bytes BIGINT,
    caption TEXT,
    is_voice_note BOOLEAN DEFAULT false,
    minio_bucket TEXT,
    minio_key TEXT,
    created_at TIMESTAMPTZ DEFAULT now(),
    -- 🔧 without this, a redelivered message duplicates every attachment row
    CONSTRAINT unique_attachment UNIQUE (message_id, signal_attachment_id)
);

CREATE TABLE signal.reactions (
    id BIGSERIAL PRIMARY KEY,
    -- 🔧 NULLable + resolved later: a reaction can arrive BEFORE its target
    -- message (out-of-order SSE) or target a message we never received. A hard
    -- FK at insert time drops those on the floor.
    message_id BIGINT REFERENCES signal.messages(id) ON DELETE CASCADE,
    target_author_id INTEGER NOT NULL REFERENCES signal.contacts(id),
    target_sent_timestamp BIGINT NOT NULL,
    emoji TEXT NOT NULL,
    contact_id INTEGER NOT NULL REFERENCES signal.contacts(id),
    is_remove BOOLEAN DEFAULT false,
    CONSTRAINT unique_reaction UNIQUE (contact_id, target_author_id, target_sent_timestamp)
);

CREATE INDEX idx_msg_ts        ON signal.messages(message_timestamp DESC);
CREATE INDEX idx_msg_dm        ON signal.messages(source_contact_id, message_timestamp);
CREATE INDEX idx_msg_group     ON signal.messages(group_id, message_timestamp) WHERE group_id IS NOT NULL;
CREATE INDEX idx_msg_fts       ON signal.messages USING GIN(search);
CREATE INDEX idx_att_msg       ON signal.attachments(message_id);
CREATE INDEX idx_rx_unresolved ON signal.reactions(target_author_id, target_sent_timestamp) WHERE message_id IS NULL;
```

🔧 **The fourth correction is behavioural, not DDL — the outbound sync echo.** A message sent
via the REST API comes *back* through the SSE stream as a sync message from Zach's own linked
devices. Without dedupe that stores every sent message twice. `unique_message` covers it only
if the send path writes the same `(source_contact_id, message_timestamp)` the sync echo will
carry — so the send path must record the server-assigned timestamp, not a locally generated
one. This is the single most likely thing to be silently wrong in v1; it has its own test.

### 5. Attachment Storage — `devrc: scripts/signal/_minio.py`

Clone of `scripts/mail-actions/_minio.py` targeting the archive tenant. Bucket
`signal-attachments` (auto-created on first use). Key `{contact_or_group}/{YYYY-MM-DD}/{filename}`.
Sidecar JSON `{filename}.json` with contact, timestamp, message_id, content_type. Re-upload of
the same key is a no-op, not a duplicate.

### 6. Skill File — `devrc: claude/skills/signal/SKILL.md`

Same structure as `claude/skills/mailbox/SKILL.md`: key-facts table, query examples (FTS,
conversation listing, recent messages), send example, gotchas.

🔴 Its description line is **routing surface, not documentation** — key use case first, then
the literal phrases Zach says, then disambiguation from `mailbox`. The listing is budgeted at
1% of the context window and silently drops descriptions on overflow.

## Cut from v1: activity-pipeline emission

Removed for three independent reasons, any one of which is sufficient:

- **Architecturally blocked** as designed (finding 1) — needs a host-side emitter, which is a
  different component in a different place from the K8s consumer.
- **No precedent to clone** (finding 2) — mailbox does not do this.
- **Requires renegotiating a live invariant** (finding 3) — the inbound/outbound presence
  split is a real design question about what "active time" means, not a config edit.

Nothing else in v1 depends on it. **If revisited**, the shape is: a host-side emitter (not the
cluster consumer), **outbound only**, added to `EXPECTED_SOURCES` but deliberately *not* to
`PRESENCE_SOURCES`, with the reasoning written at the constant the way the existing ones are.
That is its own proposal.

## Decisions (RESOLVED 2026-08-16, operator)

| # | Decision | Answer | Consequence |
|---|---|---|---|
| D1 | Durability/privacy of message content | **(a) full bodies + attachments** | §4 DDL stands as written — no schema change. Accepted knowingly: this is the irreversible one. |
| D2 | Phone number | **(a) existing personal number** | Uses 1 of 5 linked-device slots. Register from the home IP (see Gotchas). The bot sees everything the phone sees, which interacts with D1 — capture is total by default. |
| D3 | Send workflow | **(b) draft → clawgate approval** | The write surface **exists but is gated**. See §7. Adds a component the draft did not have; `test_outbound_echo.py` stays in scope, and a new approval-gate suite joins it. |

**Deferred, not blocking:** group capture allowlist (a filter, addable later — note it is also
the natural mitigation if D1 is ever revisited), activity depth (downstream of a cut feature).

### 7. Send path — draft → clawgate approval (D3)

Not direct-send. The skill composes a draft; the message reaches Signal only after Zach
approves it in the existing clawgate UI.

**Precedent to clone:** `scripts/mail-actions/clawgate.py` — `emit_task(...)`, which posts a
Task card and is a **graceful no-op returning `False`** when `CLAWGATE_HOOK_TOKEN` is unset.
Mirror that shape, including the no-op.

🔴 **The gate must be structural, not procedural.** `send_message()` must be unable to reach
the Signal API without an approval token — not merely documented as "call approve first". The
failure mode this guards against is an agent calling the send helper directly and bypassing a
convention. Design it so the un-approved path has no code route to the API, and pin that with
a test that tries to take it.

Un-approved drafts are stored with `is_outbound=true` and a pending state so a draft is never
silently lost; the sync-echo dedupe (§4 🔧) applies once the approved message actually sends.

## Deployment Sequence

🔴 **Two repos, different rules.** `homelab-talos` is GitOps-reconciled — committing to trunk
**is** deploying. `devrc` is feature-branch → PR → `scripts/ship.sh`.

| # | Step | Repo |
|---|---|---|
| 1 | Register phone — link as secondary device via QR (**operator, manual**) | — |
| 2 | Deploy `signal-cli-rest-api` — PVC + Deployment + Service via Flux | homelab-talos |
| 3 | Create `signal` schema — init job from `configmap-schema.yaml` | homelab-talos |
| 4 | Build consumer + `_signal_db.py` + `_minio.py` + tests | devrc (PR) |
| 5 | Deploy consumer Deployment | homelab-talos |
| 6 | Write `claude/skills/signal/SKILL.md` | devrc (same PR as 4) |
| 7 | Verify — send a test message from another device, confirm Postgres row + MinIO object | — |

Steps 4+6 are one devrc PR. Steps 2/3/5 are homelab-talos commits, and **5 depends on 4
merging first** (the image is built from devrc source).

## Test Plan

🔴 **Hermetic.** The new target runs in the nix sandbox — no live Postgres, no live MinIO, no
Signal API, no network. Fakes/fixtures only, on the `scripts/mail-actions/tests/` model
(`test_db_schema.py`, `test_db_direct_mode.py`, `test_idempotency.py`, `fixtures/`).

### Registering the target — both edits or the gate fails naming it

`scripts/signal/tests` must be added to **`HERMETIC_TARGETS`** (`scripts/run-tests.sh:288`)
**and** to **`TARGET_FLOORS`** (`:431`) — they pin each other two-way, validated by
`--check-floors`.

🔴 **The floor is MEASURED, never computed.** `git add` the new files first (an untracked test
is silently absent from the flake source, so the gate reports the old count), run the
authoritative gate, read that target's `collected=`, and write what the gate prints:
`collected - min(50, max(1, collected/20))`. Do not do arithmetic across a conflict.

### Suites

| File | Covers |
|---|---|
| `test_db_schema.py` | `ensure_schema()` is idempotent; every table/index/constraint present; the generated `search` tsvector actually populates |
| `test_idempotency.py` | 🔧 the same envelope twice → **one** row; **the NULL-contact path specifically** (an unresolved sender must not bypass `unique_message`); attachment redelivery → one attachment row |
| `test_envelope_parse.py` | fixture corpus: DM · group · reaction · edit · attachment · quote · delivery receipt · read receipt · typing · **sync (outbound from another linked device)** · view-once · expiring · **unknown/unhandled type** |
| `test_outbound_echo.py` | 🔧 send → the sync echo arrives → **one** row, `is_outbound=true`; and the send path records the *server* timestamp, not a local one |
| `test_reactions.py` | 🔧 reaction arriving **before** its target message resolves later; reaction to a never-received message is retained, not dropped; remove-reaction; the partial index is used |
| `test_minio_signal.py` | key layout; sidecar JSON contents; bucket auto-create; re-upload of the same key is a no-op |
| `test_search.py` | FTS returns the right rows — **and a positive control**: a query that MUST match something, so a zero is distinguishable from a miswired query |
| `test_consumer_resilience.py` | SSE disconnect→reconnect with redelivery loses and duplicates nothing; malformed event is skipped, not fatal; Postgres unavailable → retry without dropping; attachment fetch failure does not roll back the message write |
| `test_approval_gate.py` | 🔴 **D3**: an un-approved draft has **no code route to the Signal API** — assert the attempt fails with the gate's own specific error, not merely that a convention was skipped; approval token absent → graceful no-op (`False`), mirroring `mail-actions/clawgate.py`; an approved draft transmits exactly once; a draft is never lost |
| `test_skill_doc.py` | every status/command the module emits is documented in `SKILL.md`, **derived from the source** (`re.findall` over the module), never from a hand-written literal |

### Discipline the implementer must follow (repo-standard, non-negotiable)

- 🔴 **Every regression test shown RED at base, GREEN at HEAD** — report the matrix. A test
  that only ever passed proves nothing. A guard pinning an invariant the bug never violated is
  an *invariant guard*: label it, don't count it as regression coverage.
- 🔴 **Mutation-test each of the four 🔧 corrections *and* the D3 approval gate** (five in
  total): break it on purpose, confirm a test fails **with that guard's specific error**, and
  confirm the case is *reachable* (no earlier check short-circuits it). A mutant that survives
  a green suite is the finding. The approval gate is the one where a survivor matters most —
  a bypassable gate that tests green is worse than no gate.
- 🔴 **Fixture values pairwise distinct, and distinct from any constant an assertion names** —
  a fixture that can only produce the constant's own value cannot catch a hardcoded literal.
- 🔴 **Validate the instrument before reading its verdict**: for any "0 duplicates" / "0
  matches" assertion, first feed a case that MUST produce a non-zero count and watch the number
  move. Report the pair, never the zero alone.
- 🔴 **`test_skill_doc.py` must DERIVE its list from the module.** A ledger that restates a
  hand-written literal cannot catch the thing it was written for.
- Read the gate's verdict with **`scripts/gate.sh`** — its exit status is authoritative, exit
  **90 = could-not-vouch** (read the log), and never read a status through a pipe or a trailing
  `echo`.

## Gotchas (carried forward, plus the new ones)

| Gotcha | Mitigation |
|---|---|
| signal-cli breaks if >3 months old (protocol rotates) | **Pinned tag** + monthly bump cadence |
| 1 replica max — key conflicts on multi-instance | `replicas: 1`, hard constraint |
| Must `receive` regularly or the account is flagged spam | CronJob calling `/api/v1/receive` every 6h |
| Cloud IPs may be pre-flagged | Register from the home IP |
| Max 5 linked devices per phone | Uses 1 slot — see D2 |
| Rate limits undocumented (~100 msgs/day to new contacts) | Exponential backoff, respect `Retry-After` |
| Read receipts / typing indicators DBus-only | Accept; no REST equivalent |
| Postgres shared with mailbox | Separate schema, distinct namespace |
| 🔧 **`UNIQUE` does not dedupe over NULL in Postgres** | `source_contact_id NOT NULL` + placeholder contacts |
| 🔧 **Outbound messages echo back via device sync** | Store the server timestamp so `unique_message` catches the echo |
| 🔧 **Reactions can precede their target message** | Nullable `message_id`, resolved later, partial index |
| 🔧 **Redelivery duplicates attachments** | `UNIQUE (message_id, signal_attachment_id)` |
| ⚠ Every gotcha above except the 🔧 ones is **recall from upstream docs**, unverified here | Confirm against the live deployment at step 7 |

## Estimated Effort

| Component | Size |
|---|---|
| Flux manifests | Small (copy + adapt mailbox) |
| Consumer (SSE → parse → store) | Medium (~300-400 lines) |
| `_signal_db.py` | Small (clone `_db.py`) |
| Postgres schema | Small (SQL above) |
| `_minio.py` | Small (clone `_minio.py`) |
| Skill file | Small (clone mailbox skill) |
| Send path + clawgate approval gate (D3) | Small-medium (clone `mail-actions/clawgate.py`) |
| **Tests (10 suites + fixture corpus)** | **Medium-large — the largest single item** |
| Testing + iteration (SSE edge cases, attachments, groups) | Medium |

**~2-3 days of focused work** after the phone is linked. The draft's 1-2 days predated the
test plan and the four schema corrections; the storage half alone is still ~1-2.

## References

- [signal-cli](https://github.com/AsamK/signal-cli) — GPLv3
- [signal-cli-rest-api](https://github.com/bbernhard/signal-cli-rest-api) — MIT
- [signal-cli-rest-api docs](https://bbernhard.github.io/signal-cli-rest-api/) — Swagger, config, Docker
- [signalbot](https://github.com/sergdort/signalbot) — Python framework, decorator handlers
