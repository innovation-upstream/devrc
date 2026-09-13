# Handoff — email-automation layer (`mail-actions`) shipped + closed — 2026-06-30

## What this session was
Resumed from `handoff-qa-automation-2026-06-29.md`, which queued **email automation on the live `mail` table** as the original-but-untouched "heavy automation" goal. Picked it up and **shipped it end-to-end**: a deterministic-first email-automation layer (`mail-actions`) over the self-hosted mailbox — action-required extraction, invoice→tax archiving, thread reconciliation, and a Gmail Sent-mail poller — all verified live. The close-the-loop thread is now **CLOSED**.

Operate everything via the **`mailbox` skill** (updated this session). Cross-session detail: memory `selfhosted-mail-inbox` (updated), ledger `close-the-loop/STATE.md` (updated, top entry).

## State now (all merged + live unless noted)
**Tool: `~/workspace/devrc/scripts/mail-actions/` (devrc `main`).** Reaches the homelab cluster via `kubectl port-forward`. Deterministic-first; the only LLM/$ is the action-extractor's Stage 2 (OpenRouter `deepseek/deepseek-v4-flash`, survivors only, ~sub-cent/run).

- **Action-required extractor** (devrc #31, #33, #34): Stage-1 deterministic filter (`filter.py` — drops `category=alert`, bulk/`List-*`/`Feedback-ID`, operator denylists [github/npm/pagerduty/bugsnag/clickup/nasdaq/avianca/resend-dunning + voip "low balance"]; **billing exemption** rescues invoices carrying bulk headers) → LLM on the few survivors → `mail_actions` rows. **Thread-aware:** one live action per thread (newer-msg supersede + owner-reply auto-close, via `thread_key` = References root). **MANUAL** (`extract.py run`; `list`; `--emit-clawgate`; `--dry-run`/`--limit`). Tuned live **3534→6 survivors** by operator adjudication. Verified live: full run → 4 actions → collapsed to 1 clean per-thread action.
- **Invoice→tax archiver** (devrc #32, #33): deterministic (no LLM). Scans ALL via_gmail mail w/ a PDF + billing signal → PDF + JSON sidecar to MinIO **minio-archive** bucket `taxes-{year}-invoices`. Idempotent via `invoice-archived` label. **SCHEDULED daily 06:00** — workbench home-manager systemd user timer `mail-actions-archive.timer` (devrc #35; wrapper `scripts/mail-actions/run-archive.sh`). Verified live: Hetzner + Cloudflare invoices in `taxes-2026-invoices`; timer ran green under systemd.
- **Sent-mail IMAP poller** (homelab-infra #80, #81): **LIVE in-cluster CronJob** `sent-poller` (ns `mailbox`, `*/10`, image `mail-sent-poller:0.1.1`). IMAP-pulls Gmail `[Gmail]/Sent Mail` (app-password in SOPS secret `mailbox-gmail-imap`) → re-injects raw via SMTP into the receiver. Cold start = last **30 days** (`SENT_LOOKBACK_DAYS`), **batched** (`FETCH_BATCH=25`). Verified live: 47 sent msgs ingested, no OOM; re-opened the naida action → reconcile **auto-closed** it (full chain proven).

**New PG tables (auto-created):** `mail_actions(...,status[open|done|superseded],thread_key)`, `mail_sync_state(folder,uidvalidity,last_uid)`. **New `mail.labels`:** `bulk|fyi|action-required|invoice|superseded|dismissed|sent|invoice-archived`.

**Autonomy:** archiver (daily) + sent-poller (`*/10`) self-run; **action-extractor stays manual by operator choice** (the only $ piece).

## Next steps (ranked)
1. **Next close-the-loop run should pick a NEW thread** — this loop is closed. A still-pending verifier from the prior thread: run **`activity-scan --days 7`** (~a week out) to see if manual vetr/naida QA browser-time dropped.
2. **Optional polish (only if it earns it):** schedule the action-extractor too (a sibling cron, ~sub-cent/day); thread-level dedup in the extractor (it's per-message — a thread with N new msgs in one run already dedups, but cross-day it relies on supersede); `--emit-clawgate` to push actions to the phone/Tasks queue.
3. **Operator declined key rotation** — but an OpenRouter key + the Gmail app-password are in the 2026-06-29 transcript. Re-offer if security posture matters.

## Gotchas / decisions
- **Invoices are NEVER action items** (operator: auto-paid by card; only needed for tax records) → archiver only; the extractor routes invoice survivors to `invoice`/fyi, never the LLM.
- **Dead threads / handled items → mark `dismissed` in mail state, NOT a filter rule** — keeps future legit mail from that party flowing (did this for the dead Zen Payments thread + a replied Stripe). Same principle drove avianca (denylist the marketing subdomain only) and LinkedIn Sales-Nav (dropped the over-broad `billing-*@` exemption, not the sender).
- **`Feedback-ID`** is the high-value bulk signal (zero genuine action threads carry it; halved the survivor set). Billing exemption runs BEFORE the bulk drop so invoices survive.
- **Extractor `--limit` caps rows PULLED most-recent-first** (default 150) — to drain a backlog use `--limit 4000`.
- **Sent-poller**: needs a Gmail **app-password = full-mailbox IMAP** (no sent-only scope) in `mailbox-gmail-imap`; the first design did `UID FETCH 1:*` and **OOMKilled** → fixed with 30-day cold-start lookback + batched fetch (commit `last_uid` per batch). A fully-failed inject batch still advances `last_uid` (won't wedge; Message-ID dedup makes a manual rescan safe).
- **The system is blind to mail you send from other clients unless the poller captures it** — it only sees Gmail Sent. Reconcile matches owner `from_addr` regardless of `via_gmail` (sent mail is `via_gmail=false`).
- **Archive vendor-domain key** uses a last-2-labels heuristic (wrong for `.co.uk` — no Public Suffix List).
- **Uncommitted in devrc working tree:** the `CLAUDE.md` Layout line added this session (+ pre-existing drift noted in the prior handoff + this handoff doc). Commit/push when tidying devrc.

## How to verify
```bash
export KUBECONFIG=~/workspace/homelab-talos/homelab-kubeconfig
# open action queue:
kubectl -n mailbox exec mailbox-postgres-0 -- psql -U mailbox -d mailbox -c \
  "select mail_id,status,left(ask,60) from mail_actions where status='open';"
# sent-poller running + ingesting:
kubectl -n mailbox get cronjob sent-poller; kubectl -n mailbox exec mailbox-postgres-0 -- \
  psql -U mailbox -d mailbox -tAc "select count(*) from mail where 'sent'=any(labels); select * from mail_sync_state;"
# archiver timer scheduled:  systemctl --user list-timers | grep mail-actions
# manual action run (needs a FRESH OPENROUTER_API_KEY):
OPENROUTER_API_KEY=... nix-shell -p 'python3.withPackages(p:[p.psycopg2 p.requests])' --run \
  'python ~/workspace/devrc/scripts/mail-actions/extract.py run'
# tests:  nix-shell -p 'python3.withPackages(p:[p.pytest p.psycopg2 p.requests p.minio])' --run \
#           'python -m pytest ~/workspace/devrc/scripts/mail-actions/tests -q'   # 100 pass
```
