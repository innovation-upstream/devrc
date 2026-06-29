# mail-actions — action-required email extractor

Mines the self-hosted inbox (Gmail → homelab Postgres `mail` table) for the handful of
genuinely **action-required** emails buried in thousands of alerts/notifications, and
records them as structured rows in a `mail_actions` table (plus, optionally, a clawgate
Task card per new item).

This is an **MVP**. Its loop closes on an *artifact verifier* — extraction correctness
checked against the source email — not on human adoption. See "Verifying the artifact".

## Pipeline

```
mail (via_gmail, processed_at IS NULL)        ← delta read
  │
  ├─ Stage 1  deterministic noise DROP   filter.py   (pure, NO LLM, high-precision)
  │     drop on: category=alert · List-Unsubscribe/List-Id/Auto-Submitted present ·
  │              Precedence in {bulk,list,junk} · short sender denylist
  │     → label 'bulk', stamp processed_at
  │
  ├─ Stage 2  LLM extraction             llm.py      (OpenRouter, survivors only)
  │     → strict JSON {action_required, who, ask, deadline, amount, confidence, reason}
  │     retry once on malformed; sanity guard: action_required + empty ask → fyi
  │
  ├─ Stage 3  persist + mark state       _db.py      (idempotent)
  │     action → INSERT mail_actions (ON CONFLICT mail_id DO NOTHING), label 'action-required'
  │     non-action → label 'fyi'
  │     all processed rows get processed_at = now()  → re-runs are a no-op
  │
  └─ Stage 4  surface (OPTIONAL, --emit-clawgate)    clawgate.py
        one clawgate Task card per NEW action item
```

**Stage-1 design contract:** it must *never* drop a genuine action item. It only drops on
unambiguous, header-driven signals; ambiguous survivors (incl. `no-reply@`
verification/security mail) go to the LLM. The denylist is intentionally short — header
signals do the heavy lifting.

Measured on the live inbox (2026-06-29): 3,537 unprocessed via_gmail rows → 3,457 dropped
by Stage 1 → **80 survivors** for the LLM pass (est. LLM cost ~$0.02 with the default
cheap model).

## Requirements / env

| var | needed for | notes |
|-----|------------|-------|
| `KUBECONFIG` | DB access (all stages) | `~/workspace/homelab-talos/homelab-kubeconfig` |
| `OPENROUTER_API_KEY` | Stage 2 (LLM) | not needed for `--dry-run` or tests |
| `MAIL_ACTIONS_MODEL` | optional | default `deepseek/deepseek-v4-flash` |
| `CLAWGATE_HOOK_TOKEN` | optional Stage 4 | if unset, `--emit-clawgate` is a graceful no-op |

No secrets are hardcoded — keys are read from env only.

**Python deps** (`psycopg2`, `requests`): this is NixOS, so run under a nix-shell rather
than assuming a global install:

```sh
nix-shell -p 'python3.withPackages(p:[p.psycopg2 p.requests])' --run \
  'python scripts/mail-actions/extract.py run --dry-run'
```

DB access uses `kubectl -n mailbox port-forward svc/mailbox-postgres` on an ephemeral
local port (the pod is ClusterIP-only); reads AND writes go through psycopg2 with bound
parameters — never `kubectl exec … psql -c` (unsafe to escape email bodies).

## Usage

```sh
# Stage-1 only: counts + which survivors WOULD go to the LLM. No key, no writes.
python scripts/mail-actions/extract.py run --dry-run [--limit N] [--json]

# Full pipeline (needs OPENROUTER_API_KEY). --limit caps LLM cost (default 150).
python scripts/mail-actions/extract.py run [--limit N] [--model NAME] [--emit-clawgate] [--json]

# Print open action items (the artifact, for verification).
python scripts/mail-actions/extract.py list [--json]
```

## First live run (Zach runs this)

The build + offline tests + `--dry-run` were exercised in-session. The live Stage-2 pass
was **not** run (no `OPENROUTER_API_KEY` in the build session). To do the first live
extraction:

```sh
export KUBECONFIG=~/workspace/homelab-talos/homelab-kubeconfig
export OPENROUTER_API_KEY=sk-or-...                       # your key

# Start SMALL to eyeball quality + cost before processing all 80 survivors:
nix-shell -p 'python3.withPackages(p:[p.psycopg2 p.requests])' --run \
  'python scripts/mail-actions/extract.py run --limit 10'

# Then the full delta:
nix-shell -p 'python3.withPackages(p:[p.psycopg2 p.requests])' --run \
  'python scripts/mail-actions/extract.py run'
```

Note: `--limit` pulls the N most-recent *unprocessed* rows; because Stage 1 drops most of
them, a small `--limit` may yield few survivors. The first run labels everything it touches,
so subsequent runs only see new mail.

## Verifying the artifact (the loop's verifier)

The artifact is the `mail_actions` table. Verify extraction correctness against source mail:

```sh
# 1. List what was extracted.
nix-shell -p 'python3.withPackages(p:[p.psycopg2 p.requests])' --run \
  'python scripts/mail-actions/extract.py list'

# 2. For any item, open the source email and check who/ask/deadline/amount match.
export KUBECONFIG=~/workspace/homelab-talos/homelab-kubeconfig
kubectl -n mailbox exec mailbox-postgres-0 -- psql -U mailbox -d mailbox -c \
  "SELECT a.who, a.ask, a.deadline, a.amount, a.confidence, m.from_addr, m.subject
     FROM mail_actions a JOIN mail m ON m.id = a.mail_id
    ORDER BY a.created_at DESC;"

# 3. Spot-check the body of a flagged row to confirm the ask is real (not hallucinated):
kubectl -n mailbox exec mailbox-postgres-0 -- psql -U mailbox -d mailbox -c \
  "SELECT left(text_body, 1200) FROM mail WHERE id = <mail_id>;"
```

Correctness checks:
- **Precision** — every `mail_actions` row is a real ask (no hallucinated/FYI items). The
  genuine threads to expect: Zen Payments merchant onboarding, Stripe `[Action required]`
  on vetr, naida sales (lauren@naidacom.com), Hetzner/DataPacket invoices.
- **Recall** — none of those genuine threads are missing (i.e. Stage 1 didn't wrongly drop
  them, and Stage 2 didn't mark them fyi). Cross-check against the `--dry-run` survivor list.
- **Idempotency** — a second `run` reports `survivors: 0` (nothing left unprocessed).

## State / re-runs

`mail.labels` (`'bulk' | 'fyi' | 'action-required'`) + `mail.processed_at` are the
idempotency mechanism. Re-runs only touch `processed_at IS NULL` rows. To re-process a row
(e.g. after a model change), clear its state:

```sql
UPDATE mail SET processed_at = NULL,
  labels = array_remove(array_remove(array_remove(labels,'bulk'),'fyi'),'action-required')
WHERE id = <mail_id>;
DELETE FROM mail_actions WHERE mail_id = <mail_id>;
```

## Thread reconciliation (auto-supersede + auto-close)

Beyond the within-run thread-dedup (one action per thread per run), `run` keeps the
`mail_actions` table in sync with how a thread evolves over time, using the
`mail_actions.thread_key` column (the RFC 5322 thread root; see `thread_key()`).

**Feature 1 — cross-run supersede (works today, no setup).** When a NEWER message of a
thread becomes an action, the older OPEN action for that same thread is set
`status='superseded'` before the new row is inserted, so the reply's action is the
single live one. A timestamp guard (`received_at <`) means an older message can never
retire a newer open action. This fires across runs (it reads existing rows), not just
within one run.

**Feature 2 — auto-close on the OWNER's reply (needs operator setup; see below).** At
the START of each `run`, any OPEN action whose thread the **owner** has since replied in
is set `status='done'` (owner reply ⇒ handled). Matching is by `thread_key`, with a
timestamp guard so only actions that PREDATE the owner's reply are closed (a stale owner
message can't close a fresh action from a later inbound reply). Legacy rows with a NULL
`thread_key` are skipped. The run also defensively labels any inbound survivor whose
sender is an owner address `sent` and never sends it to the LLM.

Owner addresses come from `MAIL_ACTIONS_OWNER_ADDRS` (comma-separated), defaulting to
`zachlowden1@gmail.com,zach@civitai.com,zacxdev@gmail.com`.

### Operator prerequisite for Feature 2 (auto-close) — REQUIRED, not built here

Feature 2 only fires when the owner's **sent** replies actually land in the `mail`
table. **Gmail does not cleanly auto-forward Sent mail** — Gmail filters act on
*received* mail, so a normal "forward to inbox.zacx.dev" filter never sees your
outbound replies. Until you arrange for sent mail to reach the inbox, **Feature 2 is
inert** (Feature 1 works regardless). Two ways to wire it up:

- **BCC habit / client rule:** BCC `<anything>@inbox.zacx.dev` on replies you want
  auto-closed (a send-time client rule or a manual habit). This is the simplest path.
- **Apps Script:** a small Google Apps Script that forwards messages under the `Sent`
  label to `inbox.zacx.dev` on a timer.

**`via_gmail` caveat:** BCC'd / forwarded sent mail often arrives with
`via_gmail=false`. That is *deliberately* why the reconcile pass scans owner mail with
`SELECT … FROM mail WHERE lower(from_addr) = ANY(%s)` and does **NOT** restrict to
`via_gmail` — otherwise the owner's own replies would be invisible to it.

### Migration / backfill

`ensure_schema` adds `thread_key text` to the live table via
`ALTER TABLE mail_actions ADD COLUMN IF NOT EXISTS thread_key text` (idempotent).
Pre-existing rows keep `thread_key = NULL` and are skipped by Feature 2 until the
operator backfills them, e.g.:

```sql
-- backfill thread_key for existing open actions from their source mail's headers
-- (done by the operator during verification; new rows get it automatically)
```

## Invoice archiver (`archive-invoices`)

A **separate, deterministic** loop (no LLM) that mines the inbox for **PDF invoice
attachments** and archives them — plus a JSON metadata sidecar — to a homelab MinIO
bucket, so a downstream tax agent can reconcile them.

It is **independent of the action-required pipeline above**: scope is *all* invoices
across the full backlog, regardless of `processed_at`. A paid invoice the LLM marked
`fyi` is still a tax document. Idempotency uses a **dedicated `invoice-archived`
label** (NOT `processed_at`) — a mail may be both fyi-processed AND archived.

```
mail (via_gmail, raw IS NOT NULL, NOT labelled 'invoice-archived')   ← delta read
  │
  ├─ parse raw RFC822 (stdlib email) → PDF attachments (content-type OR .pdf name)
  │
  ├─ candidate iff ≥1 PDF AND (filter._billing_exempt(from,subject)  ← reused, not duped
  │                            OR an attachment named /invoice|receipt|statement/i)
  │
  ├─ per PDF → upload to  s3://taxes-{YEAR}-invoices/{vendor}/{YYYY-MM-DD}-{file}
  │            + a .json sidecar {vendor, from_addr, date, amount, subject,
  │                               message_id, mail_id}  (amount best-effort, may be null)
  │
  └─ AFTER all of a mail's PDFs+sidecars upload OK → label mail 'invoice-archived'
        (on any upload error: NOT labelled → retried next run; error counted, keep going)
```

- `year` = year of `date_header` (else `received_at`). `vendor` = registrable domain of
  the sender — last two dotted labels (`billing@hetzner.com` → `hetzner.com`,
  `noreply@notify.cloudflare.com` → `cloudflare.com`); a HEURISTIC that is wrong for
  multi-label public suffixes (`co.uk`), noted in `archive.py`.
- `amount` is best-effort: it is *not* extracted here (the PDF / tax agent is
  authoritative); if a `mail_actions` row already carries an amount for that mail it is
  passed through, else `null`.
- MinIO is reached the same way Postgres is: `kubectl -n minio-archive port-forward
  svc/minio 0:80` on an ephemeral local port, then the `minio` python client at
  `http://127.0.0.1:<port>` with **path-style** addressing (`_minio.py`).

### Env

| var | needed for | notes |
|-----|------------|-------|
| `KUBECONFIG` | DB + MinIO | `~/workspace/homelab-talos/homelab-kubeconfig` |
| `MINIO_ARCHIVE_ACCESS_KEY` / `MINIO_ARCHIVE_SECRET_KEY` | MinIO auth (optional) | if unset, read from k8s secret `minio-archive-config` key `config.env` (parses `export MINIO_ROOT_USER`/`MINIO_ROOT_PASSWORD`) |
| `MINIO_ARCHIVE_ENDPOINT` | optional | use a verbatim S3 endpoint (e.g. an in-cluster runner) instead of starting a port-forward |

No secrets are hardcoded.

### Usage

```sh
export KUBECONFIG=~/workspace/homelab-talos/homelab-kubeconfig

# Dry-run: list invoice candidates + their target bucket/key + detected PDF names.
# NO uploads, NO label writes, NO bucket creation.
nix-shell -p 'python3.withPackages(p:[p.minio p.psycopg2 p.requests])' --run \
  'python scripts/mail-actions/extract.py archive-invoices --dry-run [--limit N] [--json]'

# Live: upload PDFs + JSON sidecars, create buckets as needed, label archived mail.
nix-shell -p 'python3.withPackages(p:[p.minio p.psycopg2 p.requests])' --run \
  'python scripts/mail-actions/extract.py archive-invoices [--limit N] [--json]'
```

### Verify

```sh
# A second --dry-run after a live run reports 0 candidates (idempotency).
# List what landed (the `mc` client; alias `archive` set up to the tenant):
mc ls --recursive archive/taxes-2026-invoices
# or via the python client / MinIO console.
```

## Tests

```sh
nix-shell -p 'python3.withPackages(p:[p.pytest p.requests p.psycopg2 p.minio])' --run \
  'python -m pytest scripts/mail-actions/tests -q'
```

- `test_archive.py` — invoice archiver, all offline (no DB/MinIO/network): PDF
  attachment extraction from a synthetic `multipart/mixed` RFC822 message; candidate
  detection (billing-sender, invoice-filename signal, negative cases); bucket/vendor/
  object-key derivation incl. sanitization + message_id fallback; sidecar JSON shape;
  the `invoice-archived` idempotency predicate; and the `archive-invoices` orchestration
  with a **mocked MinIO client + fake DB** (upload+label, dry-run writes nothing, an
  upload error skips the label so the mail retries).

- `test_filter.py` — Stage-1 filter vs scrubbed real fixtures (`tests/fixtures/mail_headers.json`):
  asserts the genuine action threads survive and the noise (alert/github/npm/bugsnag/
  newsletter-with-List-Unsubscribe) is dropped, and that a no-reply password-expiry mail
  survives to the LLM rather than being blanket-dropped.
- `test_llm.py` — output parser/validator: good/fenced/prose-wrapped/malformed JSON, the
  sanity guard, confidence clamping, and the single-retry behaviour (mocked caller, no key).
- `test_idempotency.py` — fake in-memory DB: first pass labels+stamps; second pass sees an
  empty delta and is a no-op; `ON CONFLICT` insert is a no-op.
- `test_db_schema.py` — SQL-level (mock psycopg2 connection): `ensure_schema` emits the
  `ADD COLUMN IF NOT EXISTS thread_key` migration; `insert_action` carries `thread_key`;
  `supersede_open_actions`/`close_actions_done`/`fetch_open_actions_min`/
  `fetch_owner_messages` emit the expected SQL incl. the `received_at <` timestamp guard
  and the `via_gmail`-unrestricted owner scan; empty-list/empty-addrs short-circuits.
- `test_reconcile.py` — fake in-memory DB with the reconcile surface, synthetic
  `References` chains. Feature 1: a newer message supersedes an existing older open
  action, but does NOT supersede a newer one (timestamp guard); summary counts it.
  Feature 2 (`reconcile_owner_replies`): an owner reply AFTER the action closes it; an
  owner message OLDER than the action does not; an unrelated thread does not; a legacy
  NULL-`thread_key` action is skipped; runs at the start of `cmd_run` and counts.
  Owner-from inbound survivor → labeled `sent`, no LLM. `owner_addrs()` env override +
  default.

Fixtures are scrubbed to **headers + from + subject only** — no personal email bodies are
committed.
```
