# Handoff: ferdium-server

## Goal
Deploy Ferdium Server (self-hosted multi-messenger backend) on the homelab Talos cluster, exposed at `ferdium.zacx.dev` behind Authelia, so the Ferdium desktop client can sync without a cloud account.

## State now
- **Branch / PR**: devrc `fix/ferdium-client-packages` → **PR #1240 OPEN** (MERGEABLE / UNSTABLE, checks running). homelab-infra `feat/ferdium-server` exists with a worktree at `/home/zach/workspace/homelab-ferdium` — a subagent is building it; **its PR number is not recorded here because this doc was written before that agent reported.**
- **Clawgate task**: none recorded. `clawgate_handoff.sh resolve` exited 5 (0 tasks for this session). That cannot distinguish "touched no task" from "wrong session id", so no `clawgate-task:` field was written — it is not a clean bill of health.

### What's DONE this session
- **Landed the client-side packages that were deployed but never committed** — devrc PR #1240, branch `fix/ferdium-client-packages`, built in a worktree off `origin/main`.
  🔴 **The previous version of this doc recorded that step as done with a ✅, and that was half true.** The `home-manager switch` had genuinely succeeded — `~/.nix-profile/bin/ferdium` resolves today — but the edit to `nix/graphical.nix` was **in no commit**. It existed only in the workbench's working tree. Consequences, both silent: the laptop was never going to receive it (`ship.sh` converges to `origin/main`), and any `git checkout` in that shared clone would have erased it while the already-built store path kept resolving, so nothing would have looked wrong. **"Deployed" and "committed" are independent claims and only one had been made.**
- Verified the edit by evaluation, not by parsing: `nix eval` of `homeConfigurations.zach.config.home.packages` returns `ferdium-7.1.2`, `noto-fonts-2026.08.01`, `noto-fonts-color-emoji-2.051`, `liberation-fonts-2.1.5`, `nerd-fonts-jetbrains-mono-3.5.0+2.304` **and `deep-search`**. That last entry is the real check — the list now opens with `with pkgs;` whose scope extends across the `++ lib.optional (!isLaptop)` tail, so a shadowed `lib` would have dropped the wrapper silently. `nix-instantiate --parse` passes too but could not have caught that. Measured on the workbench (`isLaptop = false`); the laptop branch of that conditional was **not** evaluated.
- **Verified port 8117 is free on BOTH gateway configs** (rather than inheriting the claim): highest in that band is 8116 on each of `clusters/{homelab,production}/apps/nebula/gateway/gateway-nginx-config.yaml`. Positive-controlled — the same grep pattern finds 8115.
- Confirmed **no ferdium manifests exist anywhere** in homelab-infra, so rank 1 was genuinely unstarted, and swept both repos' open PRs for a duplicate (none).
- Base clone `~/workspace/devrc` fast-forwarded `30b0e7dc → 946a51f0`; it had never received this doc.
- Work claimed under the shared-queue lock as slug **`ferdium-server-1`** — release it with `claim-work --release ferdium-server-1` when rank 1 lands.

### What's IN FLIGHT
- **Rank 1–5 manifests**, in the `feat/ferdium-server` worktree. Brief given: homelab app dir + Flux Kustomization, both nginx gateway blocks, production Service/Endpoints + IngressRoute + DNS Ingress, the Authelia rule. **Nothing applied to any cluster** — commit-to-trunk IS deploy in that repo, so it lands behind a PR a human merges.

### Deploy / verify status
- **Client**: deployed on the workbench, and now committed. **Not** deployed to the laptop — that needs #1240 merged then `scripts/ship.sh`.
- **Server**: not built, not deployed, not verified. No cluster has been touched.

## Architecture (researched, not yet implemented)

```
Ferdium client → Cloudflare → production nebula gateway (10.0.0.2)
  → homelab nebula gateway (10.42.0.10:8117) → Ferdium Server (ferdium.svc:3333)
```

Authelia runs on the production cluster. Cloudflare handles TLS termination. The homelab nebula gateway proxies traffic to internal services.

## Next steps (ranked)
🔴 Ranks 1–7 are UNCHANGED from the previous version of this doc on purpose — the rank is half a `claim-work` slug's identity and re-ranking silently re-points live claims.

1. **IN FLIGHT** (`homelab-infra` `feat/ferdium-server`, claim `ferdium-server-1`) — Ferdium Server manifests on the homelab cluster: `clusters/homelab/apps/ferdium/{namespace,deployment,service,pvc,kustomization}.yaml` + `clusters/homelab/flux-system/root-kustomizations/system/ferdium.yaml`, registered in that dir's `kustomization.yaml`. **Check for the open PR before starting anything here.**
   forcing: user
2. Ferdium proxy block on the homelab nebula gateway — `listen 10.42.0.10:8117` → `ferdium.ferdium.svc.cluster.local:80`. File: `clusters/homelab/apps/nebula/gateway/gateway-nginx-config.yaml`. Likely folded into rank 1's PR.
   forcing: user
3. `homelab-ferdium` Service + Endpoints on the production cluster, port 8117 → `10.0.0.2`. File: `clusters/production/apps/nebula/gateway/services.yaml`, plus the matching `listen 0.0.0.0:8117` → `proxy_pass http://10.42.0.10:8117` block in that cluster's `gateway-nginx-config.yaml`.
   forcing: user
4. `clusters/production/apps/nebula/gateway/ferdium-ingress.yaml` — IngressRoute with the authelia middleware + the external-dns DNS Ingress. Model on `comics-ingress.yaml`.
   forcing: user
5. `ferdium.zacx.dev` → `one_factor` for `user:zach` in Authelia access control. File: `clusters/production/flux-system/charts/authelia/authelia.yaml`.
   forcing: user
6. Merge the manifest PR, `flux reconcile`, verify pod health and reach `https://ferdium.zacx.dev`. **This is the step that deploys** — everything above only stages.
   forcing: user
7. Open the Ferdium desktop client → set the custom server URL → register the account → flip `IS_REGISTRATION_ENABLED` to `false` and redeploy.
   forcing: user
8. **NEW** — merge devrc **PR #1240** and run `scripts/ship.sh`, so the laptop actually receives the client packages. Until this lands the two hosts differ, and the workbench's copy is a store path with no committed source behind it on either host.
   forcing: regression

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
