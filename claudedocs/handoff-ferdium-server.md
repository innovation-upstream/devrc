# Handoff: ferdium-server

## Goal
Deploy Ferdium Server (self-hosted multi-messenger backend) on the homelab Talos cluster, exposed at `ferdium.zacx.dev` behind Authelia, so the Ferdium desktop client can sync without a cloud account.

## State now
- **Branch / PR**: devrc `fix/ferdium-client-packages` → **PR #1240 OPEN** (client packages). devrc `docs/handoff-ferdium-server-pickup` → **PR #1241 OPEN** (this doc). homelab-infra `feat/ferdium-server` → **PR #647 OPEN**, commit `71c7d475`, rebased onto `origin/trunk` `8cfa319d` — 16 files, +633/-2. **Not merged. Nothing applied to any cluster or node.**
- **Clawgate task**: none recorded. `clawgate_handoff.sh resolve` exited 5 (0 tasks for this session). That cannot distinguish "touched no task" from "wrong session id", so no `clawgate-task:` field was written — it is not a clean bill of health.

### What's DONE
- **Ranks 1-5 built and staged as homelab-infra PR #647.** Homelab app dir (`namespace`/`pvc`/`deployment`/`service`/`kustomization`) + Flux root Kustomization, both nginx gateway blocks, production Service/Endpoints + IngressRoute + DNS Ingress, and the Authelia `one_factor` rule for `user:zach`.
- **Landed the client-side packages that were deployed but never committed** — devrc PR #1240. The prior version of this doc marked that step done with a ✅: the `home-manager switch` had genuinely succeeded (`~/.nix-profile/bin/ferdium` resolves) but the `nix/graphical.nix` edit was **in no commit**, so the laptop was never going to receive it and any `git checkout` would have erased it while the built store path kept resolving. **"Deployed" and "committed" are independent claims and only one had been made.**
- Verified that edit by **evaluation, not parsing**: `nix eval` of `homeConfigurations.zach.config.home.packages` returns all five packages **and `deep-search`** — the real check, since the list now opens with `with pkgs;` whose scope reaches across the `++ lib.optional (!isLaptop)` tail. Measured on the workbench (`isLaptop = false`) only.
- **Port 8117 verified free on both gateway configs** (8116 is the highest in that band on each; positive-controlled against 8115), and the PR diff independently confirmed to move **exactly one token** in the relay deny-list and add exactly two `listen` lines with their matching `proxy_pass` — no other port moved.
- Confirmed the PR diff contains **no `cam` / `clowden4077` / argon2 hash** — the unrelated uncommitted Authelia user stayed out.
- Base clone `~/workspace/devrc` fast-forwarded `30b0e7dc → 946a51f0`; it had never received this doc.
- Claimed under the shared-queue lock as **`ferdium-server-1`** — `claim-work --release ferdium-server-1` once #647 merges.

### Two additions beyond the original plan, both required
1. **`8117` added to the `diffsona` arm of `k0s/host-firewall/relay-firewall.sh`** — see Gotchas. Without it the port is internet-open past Authelia.
2. **`nebula` root Kustomization now `dependsOn: [comic-flex-pwa, ferdium]`**, matching the `:8115` precedent — the gateway nginx config has no `resolver`, so a missing Service arms a latent gateway-WIDE outage rather than failing at apply time.

### Deploy / verify status
- **Client**: deployed on the workbench and now committed; **not** on the laptop until #1240 merges and `scripts/ship.sh` runs.
- **Server**: staged only. Not merged, not reconciled, not deployed, not verified. No cluster or node has been touched.

## Architecture (researched, not yet implemented)

```
Ferdium client → Cloudflare → production nebula gateway (10.0.0.2)
  → homelab nebula gateway (10.42.0.10:8117) → Ferdium Server (ferdium.svc:3333)
```

Authelia runs on the production cluster. Cloudflare handles TLS termination. The homelab nebula gateway proxies traffic to internal services.

## Next steps (ranked)
🔴 Ranks 1-7 keep their original numbering on purpose — the rank is half a `claim-work` slug's identity and re-ranking silently re-points live claims. Ranks 1-5 are now all carried by **PR #647**.

1. **STAGED as homelab-infra PR #647** — review and merge it. `/audit-pr 647` is worth running first: it carries an Authelia access-control rule and an internet-facing relay port. Merging IS deploying.
   forcing: user
2. (in #647) homelab nebula gateway block, `listen 10.42.0.10:8117` → `ferdium.ferdium.svc.cluster.local:80`.
   forcing: user
3. (in #647) production `homelab-ferdium` Service + Endpoints and the `listen 0.0.0.0:8117` → `10.42.0.10:8117` block.
   forcing: user
4. (in #647) `ferdium-ingress.yaml` — IngressRoute + external-dns DNS Ingress.
   forcing: user
5. (in #647) `ferdium.zacx.dev` → `one_factor` for `user:zach` in Authelia.
   forcing: user
6. **After merging #647: copy `k0s/host-firewall/relay-firewall.sh` to the relay node and apply it.** 🔴 That file is NOT Flux-reconciled, so the merge does not deliver it — until this is done, `8117` is open on the node's public interface, bypassing Authelia. Then `flux reconcile`, verify pod health, and check `https://ferdium.zacx.dev` 302s to `login.zacx.dev`.
   forcing: security
7. Open the Ferdium desktop client → custom server URL → register → then flip `IS_REGISTRATION_ENABLED` to `false` and redeploy. **Watch for background sync failing with 302s while a browser works** — that is the untested forward-auth gap in Gotchas, and the fix is a scoped bypass, not deleting the Authelia rule.
   forcing: user
8. Merge devrc **PR #1240** and run `scripts/ship.sh` so the laptop receives the client packages.
   forcing: regression
9. Digest-pin `ferdium/ferdium-server:latest` — upstream publishes no version tag, so the deployed image can change under a reconcile.
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

## How to verify
```bash
# Client half — the packages resolve from the committed source, and the
# conditional tail survived the `with pkgs;` widening (this is the real check):
nix eval --impure --raw \
  '/home/zach/workspace/devrc#homeConfigurations.zach.config.home.packages' \
  --apply 'ps: builtins.concatStringsSep "\n" (map (p: p.name or "?") ps)' \
  | grep -E 'ferdium|noto|liberation|deep-search'

# Client half — actually delivered to BOTH hosts (not just built):
git -C /home/zach/workspace/devrc log --oneline -1 origin/main -- nix/graphical.nix
scripts/drift-check.sh          # read every per-host line, never the final verdict

# Server half — ONLY meaningful after the manifest PR is merged and reconciled:
flux reconcile kustomization ferdium -n flux-system          # homelab cluster
KUBECONFIG=$KC_HOMELAB kubectl -n ferdium get pods           # expect Running
curl -sS -o /dev/null -w '%{http_code}\n' https://ferdium.zacx.dev/
#   expect a 302 to login.zacx.dev while unauthenticated — a 200 here would mean
#   the Authelia middleware is NOT in the path, which is a finding, not a pass.

# End to end: Ferdium desktop -> custom server -> https://ferdium.zacx.dev
# -> register -> add the WhatsApp recipe. Nothing short of this proves the goal.
```
