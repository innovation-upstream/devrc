# Drive an INTERNAL, in-cluster DEV_MODE naida (`AUDITLOOP_INTERNAL_ALLOW_HOSTS`)

Live 2026-07-18. Read this when driving a **private, in-cluster** target (naida-dev or any
other ClusterIP app), or when changing/auditing the internal-host allowlist.

The hosted auditloop can DRIVE (Phase-3 driver + persona eval) an **internal, in-cluster
DEV_MODE naida** for full goal-directed walkthroughs — viewable/interactive on
auditloop.zacx.dev **without exposing naida publicly**. The prod SSRF guard blocks private
IPs, so a ClusterIP naida is normally unreachable by the driver; a *public* DEV_MODE naida
would be an auth-bypassed app on the internet. A narrow exact-host allowlist keeps naida-dev
fully internal — the safer topology.

## The three PRs
- **auditloop #26 (`8fd2102`) — SSRF-guard internal-host allowlist.** New env
  **`AUDITLOOP_INTERNAL_ALLOW_HOSTS`** (comma-separated **EXACT** hostnames; empty default =
  byte-for-byte prior behavior). In `internal/crawler/ssrf.go` `checkHostIP`, the private-IP
  refusal became **soft** (bypassable) for an exact-allowlisted host, while **metadata /
  link-local / multicast / unspecified stay HARD-blocked** (`isHardBlocked`, fail-safe
  `default:` = hard). **Exact map match (NOT suffix)**, same-domain `AllowedHosts` gate
  unchanged, redirect hops still IP-checked (composes through `intercept.go checkNav`).
  Threaded like `AllowLoopback` through crawl/drive/login/login-save guards.
  Adversarially security-audited: **no 🔴** (metadata-hard-block, exact-host-no-widening,
  redirect composition all hold; the allowlist is **admin env config, NOT user-triggerable**).
  **Reversible** — clear the env var → guard blocks all private again.
- **homelab-infra #138 (`dec1ff5`) — `naida-dev` on the WORKBENCH cluster** (co-located with
  auditloop): `clusters/workbench/apps/naida-dev/` reuses image
  `harbor.homelab.lan/library/naida-ai-demo:latest` with `DEV_MODE=true`+`SEED_DEMO=true`
  (lazy auto-seeds the `/sales-audits` funnel), emptyDir, `/healthz` probes, ClusterIP
  **`naida-dev.naida-dev.svc.cluster.local:8080`**, **NO ingress/DNS** (internal-only), its
  own Flux Kustomization.
- **homelab-infra #139 (`c603ae6`) — set
  `AUDITLOOP_INTERNAL_ALLOW_HOSTS=naida-dev.naida-dev.svc.cluster.local`** in the auditloop
  SOPS secret (`clusters/workbench/apps/auditloop/secrets.enc.yaml`; auditloop loads all
  config via `envFrom` that secret). ⚠ **A secret change alone doesn't restart the pod** →
  needed `kubectl rollout restart deploy/auditloop` (workbench) to pick up the envFrom change.

## Recipe — drive a fresh internal naida-dev walkthrough
On auditloop.zacx.dev (logged in):
1. Create/register a **non-plugin** target with
   `base_url=http://naida-dev.naida-dev.svc.cluster.local:8080/<path>` (the host
   auto-becomes its `verified_domain`, matching the allowlist).
2. Set the audit-config goal + a reachable **success selector** + `driving_enabled=on`
   (leave `allow_real_submit=off` = dry-run).
3. Trigger the walkthrough; then optionally "Evaluate with personas".

⚠ naida-dev **re-seeds on restart** (emptyDir + SEED_DEMO) → prefer a **selector-based**
success assertion (restart-safe) over a URL/id-based one.

Reach naida-dev directly for debugging via `kubectl -n naida-dev port-forward` (no ingress).
Activate/deactivate the whole capability via the `AUDITLOOP_INTERNAL_ALLOW_HOSTS` env in the
auditloop secret.

**Live-verified end-to-end (2026-07-18):** target `naida-dev-internal` (base_url
`…:8080/sales-audits`, `driving_enabled=on`, success_selector `#audit-create-form`), dry-run
→ the hosted auditloop drove the INTERNAL ClusterIP naida → `outcome=success, 2 steps`
(planner clicked `button#new-audit-btn` → `#audit-create-form` visible → success), then
persona eval over the driven trace.
