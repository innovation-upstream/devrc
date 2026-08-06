# mailbox — rebuild, DNS, test-send, Gmail forwarding

Read this when you are **rebuilding the receiver image**, **changing the Cloudflare
mail DNS**, **proving the public path end-to-end**, or **(re)wiring Gmail forwarding**.
Day-to-day status/query/automation lives in `SKILL.md`.

## Rebuild the receiver (after editing `src/receiver.py`)

The image is **baked** (no runtime pip) — a code change needs a rebuild + tag bump.
Build context `/tmp/mail-receiver-build/` (`Dockerfile`, `receiver.py`, `requirements.txt`
= `aiosmtpd==1.4.6` + `asyncpg==0.30.0`). `docker` on the workbench is already authed to
`harbor.homelab.lan`. Harbor is LAN-only, so this step needs the **workbench**.

```bash
# 1. edit clusters/homelab/apps/mailbox/src/receiver.py (in a worktree off trunk — the
#    main homelab-talos tree carries heavy pre-existing drift; NEVER git add -A there)
cp <edited>/receiver.py /tmp/mail-receiver-build/receiver.py
docker build -t harbor.homelab.lan/library/mail-receiver:<NEWTAG> /tmp/mail-receiver-build && \
  docker push harbor.homelab.lan/library/mail-receiver:<NEWTAG>
# 2. bump the image tag in clusters/homelab/apps/mailbox/receiver.yaml, commit → PR → merge trunk
# 3. flux reconcile source git flux-system && flux reconcile kustomization mailbox
#    kubectl -n mailbox rollout status deploy/mail-receiver
# 4. Test before relying: send to NodePort 30026 from the LAN, confirm a row lands.
```

## Test send (no Gmail needed — proves the real public path)

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

⚠ **Hetzner blocks OUTBOUND :25** for newish accounts (inbound for receiving is open), and
the **local workbench/laptop ISP also blocks outbound :25** — so you cannot test inbound by
sending from here. Use the postfix-relay path above, or real Gmail.

## DNS (Cloudflare, managed directly via API)

Zone `zacx.dev` id `72f00688be30dfc863a2c84fa6ab771c`. Token: secret
`cloudflare-api-token` / key `cloudflare_api_token` in the **production** ns `external-dns`
(DNS-edit scope ONLY — NOT Email Routing admin).

```bash
KUBECONFIG=~/workspace/homelab-talos/production-kubeconfig
CFT=$(kubectl -n external-dns get secret cloudflare-api-token -o jsonpath='{.data.cloudflare_api_token}' | base64 -d)
ZID=72f00688be30dfc863a2c84fa6ab771c
curl -s -H "Authorization: Bearer $CFT" "https://api.cloudflare.com/client/v4/zones/$ZID/dns_records?type=MX&per_page=100"
# REQUIRED state: inbox.zacx.dev MX 10 mx-in.zacx.dev ; mx-in.zacx.dev A <hetzner-gw-ip> with proxied=FALSE
#   (the gateway's public IP is deliberately NOT in this PUBLIC repo — read it from the
#    live A record above, or from the `server:` URL in $KC_PROD)
# (PATCH proxied:false on the A record id if it's ever orange — SMTP can't cross the CF proxy)
```

- **`mx-in.zacx.dev` A must be DNS-only (`proxied=false`).** external-dns has a global
  `--cloudflare-proxied` default that orange-clouds records; SMTP then can't reach the
  gateway. That is why these records are managed **directly in CF, NOT external-dns**.
  Don't re-add an external-dns DNSEndpoint for them — two external-dns instances flap the
  shared `zacx.dev` zone.
- **Cloudflare Email Routing on `zacx.dev` is APEX-ONLY** (`route1/2/3.mx.cloudflare.net`),
  independent of the `inbox` subdomain — it does NOT conflict; leave it alone (removing it
  kills any `@zacx.dev` apex mail). A `dig` mid-flap once showed a misleading `_dc-mx` MX:
  **trust the CF API record list, not a transient dig.**

## Gmail forwarding (operator's manual step — NOT API-accessible)

The Gmail-side toggle is a Settings action no API/MCP exposes (the Gmail integration can
only draft/label/search). Operator: Gmail → Settings → Forwarding and POP/IMAP → add
`<anything>@inbox.zacx.dev`. Gmail sends a verification email that **lands in Postgres** —
pull the confirm link/code:

```bash
$PSQL "select id,from_addr,subject from mail where from_addr='forwarding-noreply@google.com' order by received_at desc;"
$PSQL "select text_body from mail where id=<id>;"   # the 'vf-…' link CONFIRMS; the 'uf-…' link CANCELS
```

The operator must click the `vf-` link in a real browser — WebFetch reaches the page but
can't press the final Confirm button (needs the Gmail session). Then set "Forward a copy…"
(or a filter) in Gmail.

## Gateway / cutover

- **Gateway nginx.conf is a `subPath` ConfigMap mount → it does NOT live-reload.** A
  gateway config change (e.g. the `:2525` target) needs a **DS pod restart**:
  `kubectl -n nebula rollout restart daemonset/nebula-gateway`. This briefly blips ALL ~25
  gateway-fronted services (Grafana/clawgate/ClickHouse/…). Validate first (nginx resolves
  `.svc` targets at parse; the pod must be able to resolve `mail-receiver.mailbox.svc`),
  then verify recovery after.
- **Rollback for the cutover** = restore `proxy_pass 192.168.50.250:30025;` on the homelab
  `:2525` block + restart the DS.
- **Mailpit is DEAD** (was at `192.168.50.250:30025/30825`, gone from both clusters). The
  pre-existing `:25→Mailpit` path was already blackholed; the receiver's onward relay is
  therefore OFF (`MAILPIT_HOST` empty). Set it only to a live host, **NEVER** to the
  receiver's own front door (`10.42.0.10:2525`) — that loops.
