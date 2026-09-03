---
name: mailbox
description: "Query and operate the self-hosted mail inbox and its mail-actions automation, and SEND email as the operator via Gmail SMTP. Use for: my mail inbox, inbox.zacx.dev, forwarded email, the mail-receiver, the mailbox namespace, querying my email, the action-items queue, invoice/tax archiving, the sent-poller, mail automation, \"email someone on my behalf\", \"send an email as me\"."
---

# self-hosted mail inbox operations

Gmail forwards → an address you control → a durable, queryable Postgres store, fully
self-hosted (no Gmail API, no third party). Point-in-time state: memory
`selfhosted-mail-inbox` (read it first).

**Mail flow (the whole chain):**
```
Gmail ──forward──► me@inbox.zacx.dev
   │ Cloudflare DNS: inbox.zacx.dev MX 10 mx-in.zacx.dev ; mx-in.zacx.dev A <hetzner-gw-ip> (GREY/DNS-only)
   ▼
Hetzner gateway mx-in.zacx.dev:25  (production nebula-gateway nginx stream: listen 0.0.0.0:25 → 10.42.0.10:2525)
   ▼ nebula mesh
homelab gateway 10.42.0.10:2525  (homelab nebula-gateway nginx: proxy_pass mail-receiver.mailbox.svc:2525)
   ▼
mail-receiver (aiosmtpd)  parse → UPSERT (dedup on Message-ID) → Postgres `mail`
```

**Reference file** (repo-absolute; read on demand):
`~/workspace/devrc/claude/skills/mailbox/reference/build-dns-forwarding.md` — rebuilding the
receiver image, the Cloudflare mail-DNS runbook, the postfix-relay test send, Gmail
forwarding setup, and the gateway-restart / cutover-rollback / Mailpit notes. Read it before
touching the image, DNS, the gateway ConfigMap, or forwarding.

## Key facts (verify against live state before asserting)

| Thing | Value |
|---|---|
| Repo | `~/workspace/homelab-talos` (`ZacxDev/homelab-infra`), default branch **`trunk`**, Flux GitOps (merge to trunk = deploy) |
| App manifests | `clusters/homelab/apps/mailbox/` — `namespace`, `postgres.yaml`, `receiver.yaml`, `configmap-schema.yaml`, `secrets.enc.yaml`, `nodeport.yaml`, `kustomization.yaml`, `src/receiver.py` + `src/receiver_test.py`. Flux Kustomization `mailbox` (`root-kustomizations/system/mailbox.yaml`), parent ks `homelab` |
| Receiver image | `harbor.homelab.lan/library/mail-receiver:0.1.2` (BAKED aiosmtpd+asyncpg, runs uid 10001 — NOT runtime-pip). Built from `src/receiver.py` |
| Receiver | ns `mailbox`, Deployment `mail-receiver`, ClusterIP `mail-receiver.mailbox.svc:2525`, NodePort **30026**. Env: `PG_DSN` (secretKeyRef), `MAILPIT_HOST` (empty = onward relay OFF) |
| Postgres | ns `mailbox`, `mailbox-postgres-0` (StatefulSet), ClusterIP `mailbox-postgres:5432`, db/user `mailbox`, PVC `openebs-nvme-1tb`. Password in secret `mailbox-postgres-auth` (key `pg-dsn`). **Also hosts the `initiatives` schema** — same instance, `_db.py` and port-forward; see the `initiatives` skill |
| `mail` schema | `id, message_id (UNIQUE), received_at, date_header, from_addr, to_addrs[], cc_addrs[], subject, headers jsonb, text_body, html_body, raw bytea, size_bytes, labels text[], processed_at, search tsvector (GIN)` |
| Cluster access | mailbox app + receiver: `KUBECONFIG=~/workspace/homelab-talos/homelab-kubeconfig`. Gateway :25 + CF DNS + postfix test-send: `~/workspace/homelab-talos/production-kubeconfig`. **From the laptop** (nebula-only): `KUBECONFIG=~/.kube/homelab-nebula.yaml` (proxy-url SOCKS via the `homelab-kube-tunnel` service → workbench `10.42.0.30`); the rebuild step still needs the workbench (Harbor is LAN) |
| Gateway configs | homelab: `clusters/homelab/apps/nebula/gateway/gateway-nginx-config.yaml` (ConfigMap `nebula-gateway-nginx-config`, the `:2525 → mail-receiver.mailbox.svc` line). Hetzner: `clusters/production/apps/nebula/gateway/gateway-nginx-config.yaml` (`listen 0.0.0.0:25`). Both are the `nebula-gateway` DaemonSet (ns `nebula`, container `nginx-proxy`) |
| Cloudflare | zone `zacx.dev` id `72f00688be30dfc863a2c84fa6ab771c`. Token: secret `cloudflare-api-token`/`cloudflare_api_token` in **production** ns `external-dns` (DNS-edit scope ONLY — NOT Email Routing admin). Mail records managed **directly via CF API**, NOT external-dns (avoids the flap) |

## Email-automation layer — `mail-actions`

Built ON TOP of the `mail` table: **`~/workspace/devrc/scripts/mail-actions/`**. Operate from
the **workbench** (reaches the cluster via `kubectl port-forward`). Deterministic-first; the
only LLM/$ is the extractor's Stage 2 (OpenRouter, survivors only — sub-cent/run).

- **Action-required extractor** — `extract.py run` (needs `OPENROUTER_API_KEY`). Stage-1
  DETERMINISTIC filter (`filter.py`: drops `category=alert`, bulk/`List-*`/`Feedback-ID`,
  operator denylists [github/npm/pagerduty/bugsnag/clickup/nasdaq/avianca/resend-dunning +
  voip "low balance"]; a **billing exemption** rescues invoices that carry bulk headers) →
  LLM (`deepseek/deepseek-v4-flash`) on the few survivors → `mail_actions` rows.
  **Thread-aware: ONE LIVE ACTION PER THREAD** — a newer message supersedes the older OPEN
  action; your reply auto-closes it. `extract.py list` = open actions. `--emit-clawgate`
  posts a Task card each. `--dry-run` / `--limit N`. **MANUAL / on-demand** by operator
  choice — NOT scheduled.
- **Invoice archiver** — `extract.py archive-invoices` (deterministic, no LLM). Scans ALL
  `via_gmail` mail with a PDF + billing signal (full backlog, incl. paid/fyi) → uploads the
  PDF + a JSON sidecar `{vendor,from,date,amount,subject,message_id,mail_id}` to the
  **minio-archive** tenant bucket **`taxes-{year}-invoices`**, key **`{vendor}/{date}-{file}`**.
  Idempotent via the `invoice-archived` label. **SCHEDULED daily 06:00** — workbench
  home-manager systemd user timer `mail-actions-archive.timer` (wrapper
  `scripts/mail-actions/run-archive.sh`; check `systemctl --user list-timers | grep mail-actions`).
- **Sent-mail poller** — captures YOUR sent replies so reconcile auto-closes actions. **LIVE
  in-cluster CronJob** `sent-poller` (ns `mailbox`, `*/10`, image `mail-sent-poller:0.1.1`,
  src `clusters/homelab/apps/mailbox/src/sent_poller.py`). IMAP-pulls Gmail
  `[Gmail]/Sent Mail` (app-password in SOPS secret `mailbox-gmail-imap`, encrypted to
  `age1g0nfddt…dqfdj64t`) → re-injects raw via SMTP into the receiver (deduped). Cold start
  = last `SENT_LOOKBACK_DAYS`=30 only (`UID SEARCH SINCE`); batched (`FETCH_BATCH`=25,
  commits `last_uid` per batch — **the first design did `FETCH 1:*` and OOMKilled**). Cursor
  in `mail_sync_state`.

**Tables auto-created by the tools:**
`mail_actions(mail_id,message_id,from_addr,subject,received_at,who,ask,deadline,amount,confidence,reason,status[open|done|superseded],thread_key,created_at)`;
`mail_sync_state(folder,uidvalidity,last_uid)`.
**`mail.labels` values:** `bulk|fyi|action-required|invoice|superseded|dismissed|sent|invoice-archived`.

**`_db.py` (`MailDB`) — the shared DB access layer** (also used by repo-cos + initiatives).
Default = kubectl **port-forward** on an ephemeral local port (off-cluster workbench tools).
Opt-in in-cluster DIRECT-DB mode: set **`MAILBOX_PG_HOST`** (e.g.
`mailbox-postgres.mailbox.svc.cluster.local`, + optional `MAILBOX_PG_PORT`) **OR**
**`MAILBOX_PG_DIRECT=1`** (uses the DSN's own host) → connects directly, no
kubectl/port-forward/subprocess. Unset → the port-forward default, unchanged.
⚠ Load `_db.py` by **explicit importlib path, NOT `sys.path`** — its `llm.py` shadows
repo-cos's.

```bash
# manual action extraction (workbench; needs OPENROUTER_API_KEY)
nix-shell -p 'python3.withPackages(p:[p.psycopg2 p.requests])' --run \
  'python ~/workspace/devrc/scripts/mail-actions/extract.py run'      # add --emit-clawgate to push cards
nix-shell -p 'python3.withPackages(p:[p.psycopg2 p.requests])' --run \
  'python ~/workspace/devrc/scripts/mail-actions/extract.py list'     # open action items
# open actions straight from PG:
kubectl -n mailbox exec mailbox-postgres-0 -- psql -U mailbox -d mailbox -c \
  "select mail_id,status,left(ask,60) from mail_actions where status='open';"
# sent-poller health:  kubectl -n mailbox get cronjob sent-poller; kubectl -n mailbox logs job/<sent-poller-…>
```

**Automation gotchas:**
1. extractor `--limit` caps rows PULLED most-recent-first (default 150) → to drain a backlog
   use `--limit 4000`.
2. **Invoices are NEVER action items** (auto-paid → archive only).
3. Dead threads / handled items → mark `dismissed` in mail state, **NEVER** a filter rule
   (keeps future mail from that party flowing).
4. The sent-poller needs a Gmail **app-password** (= full-mailbox IMAP) in
   `mailbox-gmail-imap`; cold-start only goes back 30d; a fully-failed inject batch still
   advances `last_uid` (won't wedge, won't retry — Message-ID dedup makes a manual rescan
   safe).
5. The archive vendor-domain key uses a last-2-labels heuristic (wrong for `.co.uk`).
6. **ROTATE**: an OpenRouter key + the Gmail app-password were pasted into a 2026-06-29
   session transcript.

## Send email AS the operator (Gmail SMTP + app-password) — to ANY recipient

The SAME Gmail app-password the sent-poller uses for IMAP **read** also authenticates SMTP
**send** for that account — so an agent can send mail **as `zachlowden1@gmail.com` to
anyone** (real Gmail deliverability; NOT the `@mail.zacx.dev` postfix-relay test-send).
Proven live by `repo-cos` (its weekly digest emails through this path).

- **Credential:** SOPS secret `mailbox-gmail-imap` in homelab-talos trunk — key
  `IMAP_APP_PASSWORD`, user `IMAP_USER` (= `zachlowden1@gmail.com`).
- **Reusable code (don't re-implement):** `~/workspace/devrc/scripts/repo-cos/email_send.py`
  — `load_credentials()` (does the SOPS decrypt), `build_message(subject,body,from_addr,to_addr)`,
  and `_smtp_send()` (Gmail `smtp.gmail.com:587`, STARTTLS with a **verifying** TLS context).
  For a one-off, import it or copy the pattern; override recipient via `send_digest(to_addr=…)`
  or build your own `EmailMessage` and call `_smtp_send`.
```bash
# minimal send-as-Zach (workbench; needs the SOPS age key on PATH via nix-shell)
export SOPS_AGE_KEY_FILE=~/workspace/homelab-talos/.secrets/age.key
nix-shell -p 'python3.withPackages(p:[p.requests])' sops --run 'python3 - <<PY
import sys; sys.path.insert(0, "/home/zach/workspace/devrc/scripts/repo-cos")
import email_send
user, pw = email_send.load_credentials()          # SOPS-decrypts IMAP_USER/IMAP_APP_PASSWORD
msg = email_send.build_message(subject="hi", body="test", from_addr=user, to_addr="someone@example.com")
email_send._smtp_send(msg, user=user, password=pw)
print("sent")
PY'
```
- **Caveats:** recipients see the operator's REAL Gmail address (`From:` = his Gmail); Gmail
  caps ~500 recipients/day; confirm the recipient + body with the operator before sending
  outward-facing mail (this publishes as him). To send AS a `@mail.zacx.dev` domain instead,
  use the postfix relay (reference file), not this path.

## status / query
```bash
export KUBECONFIG=~/workspace/homelab-talos/homelab-kubeconfig
kubectl -n mailbox get pods,svc            # mail-receiver + mailbox-postgres-0 should be Running/1-1
kubectl -n mailbox logs deploy/mail-receiver --tail=20   # "stored id=…" / "dedupe" lines
PSQL='kubectl -n mailbox exec mailbox-postgres-0 -- psql -U mailbox -d mailbox -c'
$PSQL "select count(*) total, max(received_at) latest from mail;"
$PSQL "select id,left(from_addr,40),left(subject,46),received_at from mail order by received_at desc limit 10;"
# full-text search (the STORED tsvector + GIN index):
$PSQL "select id,from_addr,subject from mail where search @@ websearch_to_tsquery('english','sales audit');"
# "important mail" (drops spam AND the loud-but-legit Civitai/GitHub volume):
$PSQL "select to_char(received_at,'MM-DD HH24:MI') t,category,from_addr,subject from mail where via_gmail and category in ('personal','security') order by received_at desc limit 20;"
```
STORED generated columns (auto-computed) + the automation state columns:
- **`via_gmail boolean`** — the DETERMINISTIC spam filter: 100% of real mail is
  Gmail-forwarded (`Delivered-To: <your gmail>`), so `via_gmail=FALSE` = direct-to-MX junk
  (scanners) + the rare setup-confirmation. Filter with `WHERE via_gmail`.
- **`category text`** — coarse sender class (security/alert/notification/personal), a
  HEURISTIC (newsletters may land in `personal`). Refine by editing the CASE in
  `configmap-schema.yaml` (one ALTER).
- `labels text[]` + `processed_at` — claim/track messages.

## ⚠ Gotchas (each cost real time at go-live)
- **aiosmtpd + asyncpg event-loop:** the receiver MUST run the SMTP server on the asyncpg
  pool's loop (`loop.create_server(SMTP(...))`), **NOT** the threaded `Controller` (separate
  loop → every message 451s "another operation in progress"). Keep it that way if you edit
  `receiver.py`.
- **Receiver returns `451` on a Postgres write failure** (so the sending MTA retries — the
  store is authoritative); relay failures are logged, never rejected.
- **Hetzner blocks OUTBOUND :25** for newish accounts (inbound for receiving is open), and
  the local workbench/laptop ISP blocks outbound :25 too — you cannot test inbound by
  sending from here. Use the postfix-relay path (reference file) or real Gmail.
- **Gateway nginx.conf is a `subPath` ConfigMap mount → it does NOT live-reload**; applying a
  change needs `kubectl -n nebula rollout restart daemonset/nebula-gateway`, which briefly
  blips ALL ~25 gateway-fronted services. Rollback + validation steps: reference file.
- **Mailpit is DEAD** (was `192.168.50.250:30025/30825`) → the receiver's onward relay is OFF
  (`MAILPIT_HOST` empty). Set it only to a live host, **NEVER** to the receiver's own front
  door (`10.42.0.10:2525`) — that loops.
- **`mx-in.zacx.dev` A must be DNS-only (`proxied=false`)** — SMTP can't cross the CF proxy;
  external-dns's global `--cloudflare-proxied` default is why these records are managed
  directly in CF. **Cloudflare Email Routing on `zacx.dev` is APEX-ONLY and does NOT
  conflict — leave it alone** (removing it kills `@zacx.dev` apex mail). Detail: reference file.
