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

## Tests

```sh
nix-shell -p 'python3.withPackages(p:[p.pytest p.requests p.psycopg2])' --run \
  'python -m pytest scripts/mail-actions/tests -q'
```

- `test_filter.py` — Stage-1 filter vs scrubbed real fixtures (`tests/fixtures/mail_headers.json`):
  asserts the genuine action threads survive and the noise (alert/github/npm/bugsnag/
  newsletter-with-List-Unsubscribe) is dropped, and that a no-reply password-expiry mail
  survives to the LLM rather than being blanket-dropped.
- `test_llm.py` — output parser/validator: good/fenced/prose-wrapped/malformed JSON, the
  sanity guard, confidence clamping, and the single-retry behaviour (mocked caller, no key).
- `test_idempotency.py` — fake in-memory DB: first pass labels+stamps; second pass sees an
  empty delta and is a no-op; `ON CONFLICT` insert is a no-op.

Fixtures are scrubbed to **headers + from + subject only** — no personal email bodies are
committed.
```
