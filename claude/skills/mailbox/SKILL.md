---
name: mailbox
description: Operate the self-hosted personal mail inbox AND its email-automation layer. Inbox: Gmail forwards to me@inbox.zacx.dev → Cloudflare DNS (MX) → Hetzner gateway :25 → nebula → homelab gateway → aiosmtpd mail-receiver → Postgres `mail` table (deduped, full-text). Automation (`mail-actions`): deterministic filter → LLM action-required extraction → `mail_actions` queue (one-live-action-per-thread, auto-closes on your reply); invoice→`taxes-{year}-invoices` MinIO archiver; a Gmail Sent-mail IMAP poller. Status, query the mail/actions, run/trigger the extractor or archiver, troubleshoot a stalled path or the sent-poller, rebuild images, manage DNS/gateway/Gmail-forwarding. Use when the user mentions the mail inbox, inbox.zacx.dev, forwarded email, the mail-receiver, the mailbox namespace, querying their email data, the action-items queue, invoice/tax archiving, the sent-poller, or mail automation. ALSO: SEND email AS the operator (from their Gmail, to ANY recipient) via Gmail SMTP + the app-password — use when asked to "email someone on my behalf", "send an email as me", or draft-and-send outbound mail.
---

# self-hosted mail inbox operations

Forward Gmail → an address you control → a durable, queryable Postgres store, fully self-hosted (no Gmail API, no third party). Built 2026-06-24 and verified end-to-end with a real MX-routed delivery. Full point-in-time state lives in memory `selfhosted-mail-inbox` (read it first).

**Mail flow (the whole chain):**
```
Gmail ──forward──► me@inbox.zacx.dev
   │ Cloudflare DNS: inbox.zacx.dev MX 10 mx-in.zacx.dev ; mx-in.zacx.dev A 5.161.118.55 (GREY/DNS-only)
   ▼
Hetzner gateway 5.161.118.55:25  (production nebula-gateway nginx stream: listen 0.0.0.0:25 → 10.42.0.10:2525)
   ▼ nebula mesh
homelab gateway 10.42.0.10:2525  (homelab nebula-gateway nginx: proxy_pass mail-receiver.mailbox.svc:2525)
   ▼
mail-receiver (aiosmtpd)  parse → UPSERT (dedup on Message-ID) → Postgres `mail`
```

## Key facts (verify against live state before asserting)

| Thing | Value |
|---|---|
| Repo | `~/workspace/homelab-talos` (`ZacxDev/homelab-infra`), default branch **`trunk`**, Flux GitOps (merge to trunk = deploy) |
| App manifests | `clusters/homelab/apps/mailbox/` — `namespace`, `postgres.yaml`, `receiver.yaml`, `configmap-schema.yaml`, `secrets.enc.yaml`, `nodeport.yaml`, `kustomization.yaml`, `src/receiver.py` + `src/receiver_test.py`. Flux Kustomization `mailbox` (`root-kustomizations/system/mailbox.yaml`), parent ks `homelab` |
| Receiver image | `harbor.homelab.lan/library/mail-receiver:0.1.2` (BAKED aiosmtpd+asyncpg, runs uid 10001 — NOT runtime-pip). Built from `src/receiver.py` |
| Receiver | ns `mailbox`, Deployment `mail-receiver`, ClusterIP `mail-receiver.mailbox.svc:2525`, NodePort **30026**. Env: `PG_DSN` (secretKeyRef), `MAILPIT_HOST` (empty = onward relay OFF) |
| Postgres | ns `mailbox`, `mailbox-postgres-0` (StatefulSet), ClusterIP `mailbox-postgres:5432`, db/user `mailbox`, PVC `openebs-nvme-1tb`. Password in secret `mailbox-postgres-auth` (key `pg-dsn`). **This same DB now ALSO hosts the `initiatives` schema** (store/recaps/assistant_log) — see the `initiatives` skill; the two schemas share the instance, `_db.py`, and the port-forward |
| `mail` schema | `id, message_id (UNIQUE), received_at, date_header, from_addr, to_addrs[], cc_addrs[], subject, headers jsonb, text_body, html_body, raw bytea, size_bytes, labels text[], processed_at, search tsvector (GIN)` |
| Cluster access | mailbox app + receiver: `KUBECONFIG=~/workspace/homelab-talos/homelab-kubeconfig`. Gateway :25 + CF DNS + postfix test-send: `~/workspace/homelab-talos/production-kubeconfig`. **From the laptop** (nebula-only): `KUBECONFIG=~/.kube/homelab-nebula.yaml` (proxy-url SOCKS via the `homelab-kube-tunnel` service → workbench `10.42.0.30`); rebuild step still needs the workbench (Harbor is LAN) |
| Gateway configs | homelab: `clusters/homelab/apps/nebula/gateway/gateway-nginx-config.yaml` (ConfigMap `nebula-gateway-nginx-config`, the `:2525 → mail-receiver.mailbox.svc` line). Hetzner: `clusters/production/apps/nebula/gateway/gateway-nginx-config.yaml` (`listen 0.0.0.0:25`). Both are the `nebula-gateway` DaemonSet (ns `nebula`, container `nginx-proxy`) |
| Cloudflare | zone `zacx.dev` id `72f00688be30dfc863a2c84fa6ab771c`. Token: secret `cloudflare-api-token`/`cloudflare_api_token` in **production** ns `external-dns` (DNS-edit scope ONLY — NOT Email Routing admin). Mail records managed **directly via CF API** (NOT external-dns — avoids the flap) |
| Build context | `/tmp/mail-receiver-build/` this session (`Dockerfile`, `receiver.py`, `requirements.txt` = `aiosmtpd==1.4.6` + `asyncpg==0.30.0`). `docker` on workbench is already authed to `harbor.homelab.lan` |

## Email-automation layer — `mail-actions` (shipped 2026-06-29/30)
Built ON TOP of this `mail` table: **`~/workspace/devrc/scripts/mail-actions/`** (devrc, on `main`; PRs #31–35 + homelab-infra #80/#81). Operate from the **workbench** (reaches the cluster via `kubectl port-forward`). Deterministic-first; the only LLM/$ is the action-extractor's Stage 2 (OpenRouter, survivors only — sub-cent/run).

**Three capabilities:**
- **Action-required extractor** — `extract.py run` (needs `OPENROUTER_API_KEY`). Stage-1 DETERMINISTIC filter (`filter.py`: drops `category=alert`, bulk/`List-*`/`Feedback-ID`, operator denylists [github/npm/pagerduty/bugsnag/clickup/nasdaq/avianca/resend-dunning + voip "low balance"]; **billing exemption** rescues invoices that carry bulk headers) → LLM (`deepseek/deepseek-v4-flash`) on the few survivors → `mail_actions` rows. **Thread-aware:** one live action per thread (newer msg supersedes older OPEN action; your reply auto-closes). `extract.py list` = open actions. `--emit-clawgate` posts a Task card each. `--dry-run` / `--limit N`. **MANUAL / on-demand** (operator's choice — NOT scheduled).
- **Invoice archiver** — `extract.py archive-invoices` (deterministic, no LLM). Scans ALL via_gmail mail w/ a PDF + billing signal (full backlog, incl. paid/fyi) → uploads PDF + JSON sidecar `{vendor,from,date,amount,subject,message_id,mail_id}` to the **minio-archive** tenant bucket `taxes-{year}-invoices` (key `{vendor}/{date}-{file}`). Idempotent via `invoice-archived` label. **SCHEDULED daily 06:00** — workbench home-manager systemd user timer `mail-actions-archive.timer` (wrapper `scripts/mail-actions/run-archive.sh`; check `systemctl --user list-timers | grep mail-actions`).
- **Sent-mail poller** — captures YOUR sent replies so reconcile auto-closes actions. **LIVE in-cluster CronJob** `sent-poller` (ns `mailbox`, `*/10`, image `mail-sent-poller:0.1.1`, src `clusters/homelab/apps/mailbox/src/sent_poller.py`). IMAP-pulls Gmail `[Gmail]/Sent Mail` (app-password in SOPS secret `mailbox-gmail-imap`, encrypted to `age1g0nfddt…dqfdj64t`) → re-injects raw via SMTP into the receiver (deduped). Cold start = last `SENT_LOOKBACK_DAYS`=30 only (`UID SEARCH SINCE`); batched (`FETCH_BATCH`=25, commits `last_uid` per batch — the first design did `FETCH 1:*` and OOMKilled). Cursor in `mail_sync_state`.

**New tables (auto-created by the tools):** `mail_actions(mail_id,message_id,from_addr,subject,received_at,who,ask,deadline,amount,confidence,reason,status[open|done|superseded],thread_key,created_at)`; `mail_sync_state(folder,uidvalidity,last_uid)`. **New `mail.labels`:** `bulk|fyi|action-required|invoice|superseded|dismissed|sent|invoice-archived`.

**`_db.py` (`MailDB`) — the shared DB access layer** (also used by repo-cos + the initiatives subsystem). Default = kubectl **port-forward** on an ephemeral local port (off-cluster workbench tools). PR #156 added an additive, opt-in **in-cluster DIRECT-DB mode** for a future in-cluster consumer (the initiatives agent): set **`MAILBOX_PG_HOST`** (e.g. `mailbox-postgres.mailbox.svc.cluster.local`, +optional `MAILBOX_PG_PORT`) OR **`MAILBOX_PG_DIRECT=1`** (uses the DSN's own host) → connects directly, **no kubectl/port-forward/subprocess**. Unset (existing callers) → the port-forward default, unchanged. Load `_db.py` by **explicit importlib path, NOT `sys.path`** (its `llm.py` shadows repo-cos's).

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

**Automation gotchas:** (1) extractor `--limit` caps rows PULLED most-recent-first (default 150) → to drain a backlog use `--limit 4000`. (2) **invoices are NEVER action items** (auto-paid → archive only). (3) dead threads / handled items → mark `dismissed` in mail state, **NEVER** a filter rule (keeps future mail from that party flowing). (4) sent-poller needs a Gmail **app-password** (= full-mailbox IMAP) in `mailbox-gmail-imap`; cold-start only goes back 30d; a fully-failed inject batch still advances `last_uid` (won't wedge, won't retry — Message-ID dedup makes a manual rescan safe). (5) archive vendor-domain key uses a last-2-labels heuristic (wrong for `.co.uk`). (6) **ROTATE**: an OpenRouter key + the Gmail app-password were pasted into a 2026-06-29 session transcript.

## send email AS the operator (Gmail SMTP + app-password) — to ANY recipient
The SAME Gmail app-password the sent-poller uses for IMAP **read** also authenticates SMTP
**send** for that account — so an agent can send mail **as `zachlowden1@gmail.com` to
anyone** (real Gmail deliverability; NOT the `@mail.zacx.dev` postfix-relay test-send below).
This is the outbound counterpart to the inbound flow. Proven live by `repo-cos` (its weekly
digest emails through this path).

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
  outward-facing mail (this publishes as him). To send AS a `@mail.zacx.dev` domain
  instead, use the postfix relay (see "test send"), not this path.

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
# automation state columns: labels text[] + processed_at (claim/track messages)
# SPAM / NOISE classification (STORED generated cols, auto-computed):
#   via_gmail boolean — DETERMINISTIC spam filter: 100% of real mail is Gmail-
#     forwarded (Delivered-To: <your gmail>), so via_gmail=FALSE = direct-to-MX
#     junk (scanners) + the rare setup-confirmation. Filter: WHERE via_gmail.
#   category text — coarse sender class (security/alert/notification/personal),
#     a HEURISTIC (newsletters may land in 'personal'). Edit the CASE in
#     configmap-schema.yaml (one ALTER) to refine.
# "important mail" (drops spam AND the loud-but-legit Civitai/GitHub volume):
$PSQL "select to_char(received_at,'MM-DD HH24:MI') t,category,from_addr,subject from mail where via_gmail and category in ('personal','security') order by received_at desc limit 20;"
```

## rebuild the receiver (after editing src/receiver.py)
The image is **baked** (no runtime pip) — a code change needs a rebuild + tag bump.
```bash
# 1. edit clusters/homelab/apps/mailbox/src/receiver.py (in a worktree off trunk — the
#    main homelab-talos tree carries heavy pre-existing drift; NEVER git add -A there)
cp <edited>/receiver.py /tmp/mail-receiver-build/receiver.py
docker build -t harbor.homelab.lan/library/mail-receiver:<NEWTAG> /tmp/mail-receiver-build && \
  docker push harbor.homelab.lan/library/mail-receiver:<NEWTAG>
# 2. bump the image tag in clusters/homelab/apps/mailbox/receiver.yaml, commit → PR → merge trunk
# 3. flux reconcile source git flux-system && flux reconcile kustomization mailbox
#    kubectl -n mailbox rollout status deploy/mail-receiver
# Test before relying: send to NodePort 30026 from the LAN, confirm a row lands (see "test send")
```

## test send (no Gmail needed — proves the real public path)
```bash
# From a PRODUCTION pod via the postfix relay → MX-routes through the real chain.
# Relay allows From @mail.zacx.dev (ALLOWED_SENDER_DOMAINS); MYNETWORKS covers pods.
KUBECONFIG=~/workspace/homelab-talos/production-kubeconfig kubectl -n nebula run mailtest-$RANDOM \
  --rm -i --restart=Never --image=python:3.12-slim --command -- python3 -c '
import smtplib; from email.message import EmailMessage
m=EmailMessage(); m["Message-ID"]="<t@mail.zacx.dev>"; m["From"]="probe@mail.zacx.dev"
m["To"]="test@inbox.zacx.dev"; m["Subject"]="E2E test"; m.set_content("x")
s=smtplib.SMTP("postfix-relay.nebula.svc.cluster.local",587,timeout=25); s.send_message(m); s.quit()
print("submitted")'
# then check Postgres for subject "E2E test". A LAN-local quick test: SMTP to <nodeIP>:30026 directly.
```

## DNS (Cloudflare, managed directly via API)
```bash
KUBECONFIG=~/workspace/homelab-talos/production-kubeconfig
CFT=$(kubectl -n external-dns get secret cloudflare-api-token -o jsonpath='{.data.cloudflare_api_token}' | base64 -d)
ZID=72f00688be30dfc863a2c84fa6ab771c
# list mail records:
curl -s -H "Authorization: Bearer $CFT" "https://api.cloudflare.com/client/v4/zones/$ZID/dns_records?type=MX&per_page=100"
# REQUIRED state: inbox.zacx.dev MX 10 mx-in.zacx.dev ; mx-in.zacx.dev A 5.161.118.55 with proxied=FALSE
# (PATCH proxied:false on the A record id if it's ever orange — SMTP can't cross the CF proxy)
```

## Gmail forwarding (operator's manual step — NOT API-accessible)
The Gmail-side toggle is a Settings action no API/MCP exposes (the Gmail integration can only draft/label/search). Operator: Gmail → Settings → Forwarding and POP/IMAP → add `<anything>@inbox.zacx.dev`. Gmail sends a verification email that **lands in Postgres** — pull the confirm link/code:
```bash
$PSQL "select id,from_addr,subject from mail where from_addr='forwarding-noreply@google.com' order by received_at desc;"
$PSQL "select text_body from mail where id=<id>;"   # the 'vf-…' link CONFIRMS; the 'uf-…' link CANCELS
```
The operator must click the `vf-` link in a real browser — WebFetch reaches the page but can't press the final Confirm button (needs the Gmail session). Then set "Forward a copy…" (or a filter) in Gmail.

## ⚠ Gotchas (each cost real time at go-live)
- **Gateway nginx.conf is a `subPath` ConfigMap mount → it does NOT live-reload.** A gateway config change (e.g. the `:2525` target) needs a **DS pod restart** to apply: `kubectl -n nebula rollout restart daemonset/nebula-gateway`. This briefly blips ALL ~25 gateway-fronted services (Grafana/clawgate/ClickHouse/…). Validate first (nginx resolves `.svc` targets at parse; the pod must be able to resolve `mail-receiver.mailbox.svc`), then verify recovery after.
- **Rollback for the cutover** = restore `proxy_pass 192.168.50.250:30025;` on the homelab `:2525` block + restart the DS.
- **`mx-in.zacx.dev` A must be DNS-only (proxied=false).** external-dns has a global `--cloudflare-proxied` default that orange-clouds records; SMTP then can't reach the gateway. That's why these records are managed directly in CF, not external-dns. (Don't re-add an external-dns DNSEndpoint for them — two external-dns instances flap the shared zacx.dev zone.)
- **Cloudflare Email Routing on zacx.dev is APEX-ONLY** (`route1/2/3.mx.cloudflare.net`), independent of the `inbox` subdomain — it does NOT conflict, leave it alone (removing it kills any `@zacx.dev` apex mail). A `dig` mid-flap once showed a misleading `_dc-mx` MX; trust the CF API record list, not a transient dig.
- **Mailpit is DEAD** (was at `192.168.50.250:30025/30825`, gone from both clusters). The pre-existing `:25→Mailpit` path was already blackholed; the receiver's onward relay is therefore OFF (`MAILPIT_HOST` empty). Set it only to a live host, NEVER to the receiver's own front door (`10.42.0.10:2525`) — that loops.
- **aiosmtpd + asyncpg event-loop:** the receiver MUST run the SMTP server on the asyncpg pool's loop (`loop.create_server(SMTP(...))`), NOT the threaded `Controller` (separate loop → every message 451s "another operation in progress"). Keep it that way if you edit `receiver.py`.
- **Hetzner blocks OUTBOUND :25** for newish accounts (inbound for receiving is open). The **local workbench/laptop ISP also blocks outbound :25**, so you cannot test inbound by sending from here — use the postfix-relay path above or real Gmail.
- **Receiver returns `451` on a Postgres write failure** (so the sending MTA retries — the store is authoritative); relay failures are logged, never rejected.
