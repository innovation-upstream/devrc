# Handoff: ferdium-server

## Goal
Deploy Ferdium Server (self-hosted multi-messenger backend) on the homelab Talos cluster, exposed at `ferdium.zacx.dev` behind Authelia, so the Ferdium desktop client can sync without a cloud account.

## State now
- **Branch**: `main` (devrc) / `trunk` (homelab-talos) — both behind origin by 2 commits
- **What's DONE this session**:
  - Added `ferdium` (client app) + `noto-fonts` + `noto-fonts-color-emoji` + `liberation_ttf` to `nix/graphical.nix` — deployed via `home-manager switch` ✅
  - Researched Ferdium Server: Docker image `ferdium/ferdium-server:latest`, port 3333, SQLite default, env-var config only (config.txt deprecated)
  - Mapped homelab cluster architecture: 4 nodes, Cilium CNI, Traefik ingress, cert-manager, nebula mesh (production↔homelab)
  - Identified deployment pattern: raw manifests (no Helm chart), namespace-per-app convention
  - Identified port allocation: `8117` on homelab nebula gateway for Ferdium
  - Produced full deployment plan (3 clusters × files, see "Next steps")
- **What's IN FLIGHT**: No manifests written yet — the plan is complete but unimplemented
- **Deploy/verify status**: Client deployed; server not deployed

## Architecture (researched, not yet implemented)

```
Ferdium client → Cloudflare → production nebula gateway (10.0.0.2)
  → homelab nebula gateway (10.42.0.10:8117) → Ferdium Server (ferdium.svc:3333)
```

Authelia runs on the production cluster. Cloudflare handles TLS termination. The homelab nebula gateway proxies traffic to internal services.

## Next steps (ranked)
1. Create Ferdium Server manifests on homelab cluster: `clusters/homelab/apps/ferdium/{namespace,deployment,service,pvc,kustomization}.yaml` + Flux Kustomization CRD + register in root kustomization list. Files: `clusters/homelab/apps/ferdium/`, `clusters/homelab/flux-system/root-kustomizations/system/ferdium.yaml`, `clusters/homelab/flux-system/root-kustomizations/system/kustomization.yaml`. forcing: user
2. Add Ferdium proxy block to homelab nebula gateway nginx config (`listen 10.42.0.10:8117` → `ferdium.ferdium.svc:3333`). File: `clusters/homelab/apps/nebula/gateway/gateway-nginx-config.yaml`. forcing: user
3. Add `homelab-ferdium` Service + Endpoints to production cluster nebula gateway (pointing to `10.0.0.2:8117`). File: `clusters/production/apps/nebula/gateway/services.yaml`. forcing: user
4. Create `ferdium-ingress.yaml` on production cluster: IngressRoute with authelia middleware + DNS Ingress for external-dns/Cloudflare. File: `clusters/production/apps/nebula/gateway/ferdium-ingress.yaml`. forcing: user
5. Add `ferdium.zacx.dev` → `one_factor` for `user:zach` in Authelia access control. File: `clusters/production/flux-system/charts/authelia/authelia.yaml`. forcing: user
6. Commit all homelab-talos changes, Flux reconcile, verify pod health + `curl https://ferdium.zacx.dev/health`. forcing: user
7. Open Ferdium desktop client → set server URL → register account → disable `IS_REGISTRATION_ENABLED` and redeploy. forcing: user

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

## How to verify
1. `flux reconcile kustomization ferdium -n flux-system` (homelab cluster)
2. `KUBECONFIG=$KC_HOMELAB kubectl -n ferdium get pods` — pod should be Running
3. `curl -s https://ferdium.zacx.dev/health` — should return OK (after Authelia redirect)
4. Ferdium desktop → custom server → `https://ferdium.zacx.dev` → register → add WhatsApp recipe
