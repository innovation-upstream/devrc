# Handoff: ferdium-server

## Goal
Deploy Ferdium Server (self-hosted multi-messenger backend) on the homelab Talos cluster, exposed at `ferdium.zacx.dev` behind Authelia, so the Ferdium desktop client can sync without a cloud account.

## State now
**SHIPPED AND VERIFIED LIVE.** Ferdium Server is deployed on the homelab cluster, reachable at `ferdium.zacx.dev` behind Authelia, and the desktop client + fonts are on both hosts.

| Merged | What |
|---|---|
| devrc **#1240** (`146770ef`) | client packages that were deployed but never committed |
| devrc **#1241** | prior handoff update |
| homelab-infra **#647** (`59ba0f11`) | Ferdium Server manifests, both gateways, Authelia rule, relay firewall |
| homelab-infra **#651** | INCIDENT FIX — two dead promptver upstreams crashing the gateway |

**Open, not merged:** homelab-infra **#653** — mounts the gateway nginx config as a directory instead of `subPath`. Deliberately unmerged: applying it rolls the gateway pod on each cluster, which is an outage window the operator schedules.

### Verified live (each with the control that makes it a claim)
- **Full relay chain** prod nginx `127.0.0.1:8117` → nebula → homelab nginx → `ferdium.ferdium.svc:80` → pod `:3333` returns `{"api":"success","db":"success"}`.
- **`ferdium.zacx.dev` unauthenticated → HTTP 302 → `login.zacx.dev`.** Authelia is in the path; a 200 would have been the finding.
- **8117 is DROPPED from the public internet while listening.** Three-way control from off-mesh: 8117 = 6041ms timeout, 8114 (known-guarded) = 6016ms timeout, 8118 (unguarded, closed) = 50ms RST. The new port behaves like an established guarded port and unlike an unguarded one — measured both before and after nginx began binding it.
- **Laptop received the client**: `ferdium`, 2 colour-emoji fonts, 24 Liberation fonts. It had none before; that was the whole point of #1240.
- `ship.sh`: both hosts at `146770ef`, both switched, 0 dangling / 0 stale managed artifacts each.

### 🔴 Verified-ADJACENT, NOT verified — do not upgrade these
- **`{"api":"success","db":"success"}` is HARDCODED.** `HealthController` returns it with an upstream `TODO` and never queries the database. It proves the ROUTE, not the DB, not sync.
- **A Ready pod is not evidence sync works**, for the same reason.
- **The desktop client's background sync through Authelia is UNTESTED.** See Open investigations.

## Architecture (researched, not yet implemented)

```
Ferdium client → Cloudflare → production nebula gateway (10.0.0.2)
  → homelab nebula gateway (10.42.0.10:8117) → Ferdium Server (ferdium.svc:3333)
```

Authelia runs on the production cluster. Cloudflare handles TLS termination. The homelab nebula gateway proxies traffic to internal services.

## Next steps (ranked)
🔴 Ranks 1-7 from the previous version are **ALL CLOSED** (all landed in #647). They keep their numbers rather than being re-cut, because a rank is half a `claim-work` slug's identity. The claim `ferdium-server-1` is RELEASED. New work starts at 8.

8. Open the Ferdium desktop client → custom server `https://ferdium.zacx.dev` → register the account → then flip `IS_REGISTRATION_ENABLED` to `false` in `clusters/homelab/apps/ferdium/deployment.yaml` and let Flux reconcile. 🔴 **The flag is `true` right now**; Authelia is the only thing standing in front of an open signup form. Watch for the sync gap in Open investigations while doing this.
   forcing: security
9. Merge homelab-infra **#653** (subPath → directory mount) during a window where a brief mesh interruption per cluster is acceptable. Until then every future gateway nginx change still requires a pod restart, and each restart re-runs startup resolution — the exact mechanism behind this session's outage.
   forcing: incident — the 2026-09-02 gateway CrashLoopBackOff; #651 cleared the known trigger, this removes the blindness that hid it
10. Digest-pin `ferdium/ferdium-server:latest` in the deployment. Upstream publishes no version tag, so the running image can change under any reconcile or restart with no diff in git.
    forcing: none
11. Commit or discard `nix/programs/alacritty/default.nix`, uncommitted in `~/workspace/devrc` and **baked into the workbench's built generation**. `ship.sh` reports both hosts at the same sha while the workbench's artifact is `origin/main` PLUS that file, so the two machines are not actually identical. Also untracked there: `output.txt`, `scripts/diagnose-disk-accounting.sh`, `scripts/diagnose-nix-disk.sh`.
    forcing: regression
12. Consider a gate asserting every `proxy_pass … .svc.cluster.local` in both gateway configs resolves to a Service defined in the repo. This session found the promptver pair by checking all 37 by hand; nothing would have caught it at PR time. Deterministic, cheap, and it closes the class rather than the instance.
    forcing: none

## Ferdium Server config (reference)

| Env var | Value |
|---|---|
| `NODE_ENV` | `production` |
| `APP_URL` | `https://ferdium.zacx.dev` |
| `DATA_DIR` | `/data` |
| `DB_CONNECTION` | `sqlite` |
| `DB_SQLITE_JOURNAL_MODE` | `WAL` |
| `DB_SQLITE_SYNCHRONOUS` | `FULL` |
| `IS_REGISTRATION_ENABLED` | `true` (enable first, then `false`) |
| `IS_DASHBOARD_ENABLED` | `true` |
| `JWT_USE_PEM` | `true` |
| `CONNECT_WITH_FRANZ` | `false` |

Port: 3333 (container) → 8117 (nebula gateway) → homelab Service port 80.

## Port allocation
| Port | Service | Status |
|---|---|---|
| 8117 | Ferdium Server | planned |

## Gotchas / decisions / dead-ends
- Ferdium client login prompt can be bypassed by self-hosting Ferdium Server — the client has a "custom server" option
- No official Helm chart for Ferdium Server — use raw manifests (same pattern as demo-site)
- SQLite is fine for personal use (single replica, RWO PVC)
- `noto-fonts-emoji` was renamed to `noto-fonts-color-emoji` in current nixpkgs — caught during home-manager switch
- The homelab cluster's Authelia access control is in the **production** cluster's Flux (not homelab) — it runs on the production cluster
- The `homelab-*` services in production cluster point to `10.0.0.2` (production gateway nebula IP), which routes to `10.42.0.10` (homelab gateway nebula IP)
- Authelia has an existing `cam` user addition in an uncommitted diff on homelab-talos trunk — unrelated to this work but same file

- 🔴 **A `home-manager switch` succeeding says NOTHING about whether the source was committed.** This effort produced a textbook instance: a ✅ in a handoff, a resolving binary, and an edit in no commit. Where a deploy step reads from a working tree, "it works here" and "it is in the commit" are separate claims — make both. Caught only because `git status` showed `M nix/graphical.nix` on a session that expected a clean tree.
- 🔴 **`noto-fonts-emoji` is `noto-fonts-color-emoji`** in current nixpkgs. The old name fails the switch with an attribute error, not a warning.
- 🔴 **Widening a nix list to `with pkgs;` extends that scope across the whole expression, `++ lib.optional (…)` included.** `nix-instantiate --parse` cannot see a shadowed `lib`; only an eval that checks the *tail* still resolves can. Assert the conditional entry (`deep-search` here), not just the packages you added.
- 🔴 **`~/workspace/devrc` carries unrelated dirty files** — `nix/programs/alacritty/default.nix` and `nix/system/apply-tmp-churn-retention.sh` — plus untracked `output.txt` and two `scripts/diagnose-*.sh`. None are this effort's. They are a latent `ship.sh` fast-forward blocker, which is the failure mode where a host silently stops receiving every future change while still looking healthy. Left as found deliberately; worth triaging before the next ship.
- The base clone had **never received this doc** (it lived only on `origin/main`), so `handoff_doc.py` would have resolved an absent base and refused with `stale-base`. Fast-forward the base clone before running `/handoff` against a doc a worktree authored.
- **Port 8117 was confirmed, not assumed.** Both gateway configs top out at 8116 in that band.
- The `cam` Authelia user sitting uncommitted on homelab-talos `trunk` is **unrelated** to this work but lives in the same file as rank 5. Branching off `origin/trunk` excludes it; a worktree off the dirty checkout would not.
- **Do not `git stash` in homelab-talos**, and do not copy an `.envrc` into a worktree of it — `.envrc` is *tracked* there, a documented exception to the usual worktree recipe.

- 🔴 **An 81xx port on the production relay is INTERNET-OPEN unless it is in the `diffsona` deny-list of `k0s/host-firewall/relay-firewall.sh`** — that list is the only filter on the node's public interface, and adding `listen 0.0.0.0:<port>` without the matching DROP publishes an unauthenticated path straight past Authelia. This is the measured `8102` failure the file documents: a SYN to `:8102` was answered **from the host** 71µs later. `scripts/check-relay-guard.py` gates it. 🔴 **That script is NOT Flux-reconciled** — merging the PR does not deliver it; copying it to the relay node is an owed human step.
- 🔴 **Ferdium Server's `/health` never touches the database.** It is a real route (upstream `start/routes/web.ts`, outside every middleware group) but `HealthController` returns a hardcoded `{api,db}:success` with an upstream `TODO`. **A Ready pod is not evidence that sync works.** Related: the fallback route `/*` redirects to `/`, so a typo'd probe path returns 302 and reads as success.
- **No WebSockets** — no `ws`/`socket.io` dependency and no upgrade handler in `server.ts`; client sync is plain REST on `/v1/...`. So neither nginx block carries `Upgrade` headers, deliberately.
- **`APP_KEY` is deliberately unset.** Setting it on a fresh volume takes the entrypoint's `else` branch, which `cat`s a nonexistent file, and the env-schema check then rejects the empty key.
- 🔴 **The biggest UNVERIFIED risk: whether the Ferdium desktop client's background sync survives an Authelia forward-auth gate.** The client holds its own bearer token; as an Electron app its top-level navigation can follow the 302 and log in, but whether background requests then carry the `zacx.dev` session cookie was never tested. **If sync fails with 302s to login.zacx.dev while a browser works fine, that is this gap** — and the fix is a scoped bypass for the client's API prefix, NOT deleting the Authelia rule.
- **`ferdium/ferdium-server:latest` is a mutable tag** — upstream publishes no version tag. A digest pin is an owed follow-up.
- **`scripts-tests` was already red on `origin/trunk`** before any of this work: a pristine baseline worktree produced byte-identical verdict lines (`files_run=54 tests_ran=1316`, same failed/broken sets). Do not attribute it to the ferdium diff.

- 🔴 **`subPath` ConfigMap mounts NEVER update, and that is how a gateway config stays wrong for days while every check is green.** kubelet refreshes whole-volume mounts only. The homelab gateway served a config resolved BEFORE the promptver teardown for 2d3h: Flux reconciled green, the pod was healthy, traffic flowed. The error existed only at restart. **Anything that reads the running system cannot see this class of defect** — compare the ConfigMap to the file in the container, not to the reconcile status. Fix open as #653.
- 🔴 **nginx resolves static `proxy_pass` hostnames AT STARTUP and refuses to boot if one fails** (no `resolver` directive in these configs). So **deleting a service means deleting its gateway block IN THE SAME CHANGE** — leaving one behind breaks nothing today and arms the gateway to fail the next time its pod is replaced, for reasons that look unrelated to whoever restarts it. Cost on 2026-09-02: `nginx-proxy` CrashLoopBackOff, ~6 min across every mesh-relayed service.
- 🔴 **Prefer `nginx -s reload` over replacing the gateway pod.** A reload validates the config and keeps the existing workers if it is bad; a restart re-runs startup resolution with no fallback. That difference is what turns one dead upstream into a gateway-wide outage. (Requires #653 first — with `subPath`, a reload re-reads the same frozen file.)
- 🔴 **A PR can be red for a reason that is not in its diff: check how far behind the base is.** #647's `scripts-tests` failure was entirely because it branched off `8cfa319d`, the last commit before **#646 "fix(ci): unbreak trunk"** landed as `ccbd6b81`. A rebase fixed it with zero code change. The tell was that OTHER open PRs (#645, #639) were passing the same leg.
- 🔴 **An agent's "the gate is red on trunk too" can be true of ITS tier and false of CI's.** The manifest agent ran the suite on the dev host, where ~30 tests were `missing-module-yaml` and never executed; CI ran 1581 tests where the dev host ran 756. Its conclusion was directionally right and its evidence could not support it. **Read the CI tier's own log** — here, via the Tekton PipelineRun pod: find the run by `gitrevision` param, then `kubectl logs -n tekton-ci <pipelinerun-pod> --all-containers`.
- 🔴 **Two probes in this session returned confident numbers while measuring nothing, and both were caught only by a control.** (a) A `bash /dev/tcp` port check returned the identical result for a guarded port, an unguarded port and 443 — it discriminated nothing; `nc -vz` with timing separated DROP (6s timeout) from RST (50ms) cleanly. (b) A `grep -c 8117 /etc/nginx/conf.d/default.conf` returned 0 — but so did `grep -c 8115`, and comics demonstrably works, so the file was simply not the one nginx reads (`/etc/nginx/nginx.conf`, via `subPath`). **Always include a value the instrument MUST find.**
- **`ferdium/ferdium-server:latest` first run clones a recipes repo**, so readiness legitimately takes ~2 min after a 2m7s image pull. A `connection refused` readiness probe during that window is expected, not a defect.
- **`APP_KEY` is deliberately unset** — setting it on a fresh volume takes the entrypoint's `else` branch, which `cat`s a nonexistent file, and the env-schema check then rejects the empty key.
- **No WebSockets** in Ferdium Server — no `ws`/`socket.io` dependency, no upgrade handler; sync is plain REST on `/v1/...`. Neither nginx block carries `Upgrade` headers, deliberately.
- **Storage class is `openebs-nvme-1tb`**, chosen over `local-path` because of that provisioner's open SIGKILL investigation.
- **`k0s/host-firewall/relay-firewall.sh` is NOT Flux-reconciled** — it reaches the node via `k0sctl` (`k0s/diffsona.yaml` copies it to `/root/` and runs `systemctl enable --now relay-firewall.service`). This session applied it by `scp` + `systemctl restart`, verified from a fresh SSH connection. **Merging a change to that file deploys nothing.**
- **An 81xx port on the production relay is internet-open unless it is in the `diffsona` deny-list.** That list is the only filter on the node's public interface. Measured precedent (`8102`): a SYN was answered from the host 71µs later, straight past Authelia. Guard the port BEFORE the `listen` block goes live — the documented 8115/8116 ordering.

## How to verify
```bash
# 1. Client half — packages resolve from committed source, and the conditional
#    tail survived the `with pkgs;` widening (deep-search is the real check):
nix eval --impure --raw '/home/zach/workspace/devrc#homeConfigurations.zach.config.home.packages' \
  --apply 'ps: builtins.concatStringsSep "\n" (map (p: p.name or "?") ps)' \
  | grep -E 'ferdium|noto|liberation|deep-search'
ssh zach@192.168.50.155 'ls -l ~/.nix-profile/bin/ferdium'   # the laptop half

# 2. Server half — the pod, and the FULL relay chain (Authelia bypassed):
KUBECONFIG=$KC_HOMELAB kubectl -n ferdium get pods            # Running, 1/1
KP=~/workspace/homelab-talos/production-kubeconfig
P=$(KUBECONFIG=$KP kubectl -n nebula get pods -l app=nebula-gateway -o jsonpath='{.items[0].metadata.name}')
KUBECONFIG=$KP kubectl -n nebula exec $P -c nginx -- \
  wget -qO- --timeout=15 http://127.0.0.1:8117/health
#   -> {"api":"success","db":"success"}  🔴 HARDCODED — proves the route, not the DB.

# 3. The edge MUST redirect. A 200 here means Authelia is not in the path:
curl -sSI https://ferdium.zacx.dev/ | grep -iE '^(HTTP|location)'
#   -> HTTP/2 302 ; location: https://login.zacx.dev/?rd=...

# 4. 🔴 The security check. 8117 listens on the relay and must NOT be reachable
#    from the internet. Read all three — one alone proves nothing:
nix-shell -p netcat-openbsd --run 'for p in 8117 8114 8118; do
  nc -vz -w 6 5.161.118.55 $p; done'
#   8117 timeout (dropped) · 8114 timeout (guarded control) · 8118 refused (unguarded control)

# 5. Collateral: the gateway fronts everything. All three must be 302.
for h in clawgate.zacx.dev comics.zacx.dev auditloop.zacx.dev; do
  curl -sS -o /dev/null -w "$h %{http_code}\n" https://$h/; done

# 6. End to end, the only thing that proves the GOAL — nothing above does:
#    Ferdium desktop -> custom server https://ferdium.zacx.dev -> register
#    -> add the WhatsApp recipe -> confirm sync survives an idle period.
```
## Open investigations — live diagnosis state
### Does the Ferdium desktop client's background sync survive the Authelia forward-auth gate?
- **Symptom + exact repro:** unknown — not yet exercised. Open the Ferdium desktop client → set custom server `https://ferdium.zacx.dev` → register → add a recipe → leave it running and watch whether sync keeps working after the Authelia session goes idle.
- **Observed (with values):** the browser path is confirmed good — unauthenticated `HEAD /` returns `HTTP/2 302`, `location: https://login.zacx.dev/?rd=https%3A%2F%2Fferdium.zacx.dev%2F&rm=HEAD`. The backend behind that gate is confirmed reachable: `{"api":"success","db":"success"}` from inside the prod gateway, bypassing Authelia. So both halves work; only their COMBINATION with a non-browser client is untested. via: measurement
- **Ruled out:** "the relay chain is broken" — eliminated by the in-cluster `/health` fetch through `127.0.0.1:8117` returning 200 with a JSON body. via: measurement
- **Ruled out:** "Authelia is not actually gating the host" — eliminated by the unauthenticated 302 to `login.zacx.dev` carrying the correct `rd=` parameter. via: measurement
- **Leading hypothesis:** the client is Electron, so its top-level navigation CAN follow the redirect and complete a login in a window; the risk is that its background REST calls to `/v1/...` carry its own bearer token but not the `zacx.dev` Authelia session cookie, and get 302s instead of JSON.
- **Next probe:** launch the client, register, then watch the gateway access log for 302s on `/v1/` paths: `KUBECONFIG=$KC_HOMELAB kubectl -n nebula logs <gateway-pod> -c nginx-proxy --tail=100 | grep ' /v1/'`. 🔴 **If it fails, the fix is a scoped Authelia bypass for the client's API prefix — NOT deleting the `ferdium.zacx.dev` rule**, which would expose the whole server.
