---
name: tekton
description: Operate the homelab Tekton CI/CD platform. Tekton Operator + Pipelines/Triggers/Dashboard on the homelab cluster, the public GitHub webhook (tekton-webhook.zacx.dev → el-github-listener), the naida-ux-audit auto-detection pipeline (push→main → walk → push to auditloop → regression gate → commit status), the **clawgate-ci** pipeline (the repo's real pre-merge gate now that GitHub Actions is billing-blocked), the read-only Dashboard, and adding a new pipeline/repo. Use when the user mentions Tekton, the homelab CI platform, the ux-audit auto-detection pipeline, clawgate-ci, the Tekton webhook/dashboard/EventListener, adding a Tekton pipeline, a trigger that did not fire, or the hostNetwork gateway host-port collision. Cross-refs the auditloop + ux-audit-loops skills. Built the 2026-07 session.
---

# Tekton — homelab CI/CD platform

GitOps CI on the **homelab** cluster (Flux-managed). Runs the **naida-ux-audit** pipeline: a naida push to `main` auto-walks the app, pushes the run to auditloop, and gates on regressions. Cross-ref: `auditloop` (read API + push), `ux-audit-loops` (the walk + `--fail-on-regression` gate).

## 🔴 CRITICAL GOTCHAS — read first

1. **hostNetwork gateway host-port collisions.** The nebula public gateway (BOTH prod + homelab) is `hostNetwork: true`, so any new nginx `listen` port must be verified free at the **HOST** level, NOT just in the nginx config. Decode `/proc/net/tcp` on the **LIVE** gateway pods (hex port column) — do not trust the configmap. **k0s reserves 9099; node-exporter binds 9100.** Using **9099 CRASHED the shared gateway and took down every public app this session.** Pick a high, verified-unused port (**19100**, matching MinIO's `19099` convention). The **homelab** gateway nginx has **no reloader sidecar** → after a configmap change `kubectl -n nebula rollout restart ds/nebula-gateway`, or it serves 502. Keep `git revert` + `flux reconcile` staged to restore the gateway in ~2 min.
2. **Stale `~/workspace/homelab-talos` checkout (~100 commits behind).** NEVER read/edit it as authoritative — it caused a wrong "Tekton is already installed" conclusion. Verify cluster state against **origin/trunk of `homelab-infra`** AND the **LIVE cluster** (a running controller pod, not just a CRD's presence).
3. **No RWX storage** — only local-path / openebs (RWO, node-pinned). Two RWO local-path PVCs in one pod would **DEADLOCK scheduling** (each binds a different node) UNLESS the pod is **node-pinned** (`taskRunTemplate.podTemplate.nodeSelector`), which forces both PVCs onto one node. **This is exactly how the persistent nix cache now works** (PR #115): a static `nix-store-cache` PVC + the per-run `source` PVC both pinned to `talos-xr6-r7p`. A `seed-nix` step copies the image's `/nix` into the PVC once (sentinel `.seeded` + atomic mkdir lock, self-healing on a dead seeder), then `walk-gate` overmounts `/nix`. MEASURED walk-gate **cold 3m04s → warm 1m15s** (~1m50s saved) — the old "~2–15 min cold" figure was **wrong** (cache.nixos.org is fast here; cold ≈ 3 min). Tradeoff: if `talos-xr6-r7p` is down, runs Pend.
4. **Placeholder imagePullSecret breaks ALL pulls.** A `harbor-cred` dockerconfigjson with a non-base64 `auth` placeholder makes every pod fail image pull ("illegal base64 data"). **Do NOT attach a placeholder imagePullSecret** to the pipeline SA — public images (`nixos/nix`) need none.
5. **Operator uninstall leaves orphans.** Deleting the Flux-managed operator does NOT remove operator-CREATED components/CRDs/webhooks. A clean teardown must manually delete the operator CRs (force-remove finalizers — no controller left to run them), admission webhooks, CRDs, namespaces, and cluster RBAC.

## What / where

- **Tekton Operator v0.80.0** → Pipelines **1.12.2** / Triggers **0.36.0** / Dashboard **0.68.0**. **Chains + Results OFF.** Pruner keep-**100**, daily. Dashboard **read-only**.
- Namespaces: **`tekton-pipelines`** (control plane) + **`tekton-ci`** (CI workloads, EventListener, PipelineRuns).
- GitOps via **Flux**, repo **`ZacxDev/homelab-infra`** branch **`trunk`**, under `clusters/homelab/apps/tekton-pipelines/`.
- Kubeconfig: `~/workspace/homelab-infra/homelab-kubeconfig`.

## GitHub App — `tekton-homelab`

- App ID **4320115**, installation **147102541**, **org-wide**. Perms: **Contents:R + Commit-statuses:RW + Metadata:R**. Events: **push + pull_request**.
- Creds sealed in **`github-app.enc.yaml`** (SOPS). Used to **mint installation tokens** at pipeline time → clone private repos + post commit statuses.
- Org-wide install ⇒ **ALWAYS scope a trigger to a specific repo + branch via CEL** (see Adding a pipeline).

## Public webhook

`tekton-webhook.zacx.dev` → **`el-github-listener`** EventListener (`tekton-ci`), **HMAC-authed** (secret token), on **host port 19100** (see gotcha #1).

Path: GitHub → Cloudflare → **prod Traefik** → prod nginx `0.0.0.0:19100` → `10.42.0.10:19100` → **homelab nginx** → `el-github-listener:8080`.

## The ux-audit pipeline — `naida-ux-audit` (ns `tekton-ci`)

naida push → `main` → EventListener → **CEL filter** (`ZacxDev/naida-ai` + `refs/heads/main`) → mint installation token → **clone** → post GitHub **`pending`** status → **`nix develop`** walk (`make ux-audit-lms`, boots DEV_MODE naida + chromium) → **push run to auditloop** (P5 plugin API) → **`fetch-findings.mjs --fail-on-regression`** gate → post **`success`** / **`failure`** commit status (context **`tekton/ux-audit-lms`**).

- Image **`nixos/nix:2.24.9`** (flakes enabled).
- auditloop creds (push URL/token + read `AUDITLOOP_API_TOKEN`) in the **`auditloop-creds`** secret.
- Walk-gate uses the **persistent node-pinned nix cache** (gotcha #3) — warm walk-gate ~1m15s (cold ~3m04s). Cache PVC `nix-store-cache` on `talos-xr6-r7p`; nuke it (`kubectl -n tekton-ci delete pvc nix-store-cache`) to force a clean reseed if it ever corrupts.
- Gate keys on the **deterministic a11y-rule delta only** (`new_a11y_rules`); visual pixel diff is **advisory, not gated** (fixed in naida #149 — the sub-1% LMS render non-determinism no longer reddens CI). Opt back into visual gating with `fetch-findings.mjs --fail-on-visual [--visual-threshold N]`. See `ux-audit-loops`.

## 2nd pipeline — `remix-ux-audit` (ns `tekton-ci`) — BUILT + verified (homelab-infra #123)

Same monorepo (`homelab-infra`), CEL-scoped to `refs/heads/trunk` + a commit touching
`containers/remix/**`. Trigger `remix-push-trunk` on the SAME `el-github-listener` as naida.
A push → walk remix (14 surfaces × mobile+desktop) → push run to the **`remix-funnel`** plugin
target → gate on the a11y-rule delta → post commit status **`tekton/ux-audit-remix`** on
`ZacxDev/homelab-infra`. On top of the naida pattern:
- **Postgres + Redis SIDECARS** (Tekton pods have no Docker daemon). remix's harness reaches
  them via **`REMIX_TEST_DATABASE_URL`** (`postgres://remix:x@127.0.0.1:5432/remix?sslmode=disable`)
  + **`REMIX_TEST_REDIS_ADDR`** (`127.0.0.1:6379`) — the `internal/dbtest` external hooks +
  `DockerAvailable()` bypass landed in remix via homelab-infra **#122**. A `wait-postgres` step
  gates the walk on real sidecar readiness (the harness connect has no retry loop).
- **CEL PATH filter** — `body.commits.exists(c, c.{added,modified,removed}.exists(f, f.startsWith('containers/remix/')))` with a **`head_commit` fallback** (GitHub truncates the `commits` array on large pushes → also test `head_commit`) (verified against cel-go).
- **Impure nix-shell** — remix's `e2e/run.sh` uses `nix-shell -p ...` (not a flake devShell),
  so the walk runs under an outer `nix-shell -p bash gnumake nix` with **`NIX_PATH` pinned** to
  the repo's `flake.lock` nixpkgs rev. (Base `nixos/nix` image has neither `make` nor a
  guaranteed `bash` on PATH — naida gets `make` from its flake devShell instead.)
- Reuses the **SHARED `nix-store-cache` PVC** (both pipelines node-pin to `talos-xr6-r7p`, so
  both pods mount the one RWO local PVC; the seed-nix mkdir-lock already tolerates concurrent
  same-node seeders). Separate **`remix-auditloop-creds`** SOPS secret (remix Makefile reads
  `AUDITLOOP_PUSH_TOKEN`, a single string, vs naida's `AUDITLOOP_PUSH_TOKENS` map).
- Gate keys on the **deterministic a11y-rule delta only** (`diff.new_a11y_rules`); visual pixel
  diff is advisory (matches naida #149).
- **Verified LIVE end-to-end** via the REAL webhook: a trunk push touching `containers/remix/**`
  (`e4300274`) auto-spawned PipelineRun `remix-ux-audit-99pvx` → walk (pushed run `626190dc`) →
  gate PASS → `tekton/ux-audit-remix` **success** commit status. Plus a pre-merge manual PipelineRun
  (GREEN), cel-go CEL validation (6 cases incl. truncated-tip + null-head), and a deterministic
  RED-path gate proof. Adversarially audited (safe, no 🔴).
- **Deferred fast-follow (advisory-only):** the gate reads `runs/latest`, not the SPECIFIC pushed `run_id` — a TOCTOU window where a concurrent push could shift the "latest" read. Harden by keying the gate to the `run_id` the walk pushed.

Cross-ref: `auditloop` skill (remix producer + a11y-id contract), `ux-audit-loops` skill.

## 3rd pipeline — `clawgate-ci` (ns `tekton-ci`) — the repo's REAL pre-merge gate

**GitHub Actions is billing-blocked repo-wide.** Every workflow run fails in 3–16s with `steps: 0` and the annotation *"The job was not started because recent account payments have failed or your spending limit needs to be increased"* (verified 2026-07-30 via `gh api repos/ZacxDev/homelab-infra/actions/runs` + the job annotation; the log blob 404s). **Red Actions checks on this repo are noise, not signal** — do not debug them, and do not read a green/red Actions check as a gate. `.github/workflows/clawgate-ci.yml` survives only as `name: clawgate e2e (manual)`, `on: workflow_dispatch`, carrying the one e2e job that wasn't ported.

Definition: `clusters/homelab/apps/tekton-pipelines/triggers/clawgate-ci-pipeline.yaml` + `clawgate-ci-triggertemplate.yaml`. ⚠ The three "legs" are **steps of ONE Task** (`clawgate-ci`), not three Pipeline tasks — `mint-token`, `clone`, `status-pending`, `wait-postgres`, `build-css`, then:
- **`go`** — `go build ./... && go vet ./... && go test -race -cover ./...` against a **`postgres:16-alpine` SIDECAR** (`CLAWGATE_TEST_DATABASE_URL=…@127.0.0.1:5432/clawgate_test`).
- **`extension`** — `npm install && npm run coverage` in `containers/clawgate/extension`.
- **`hook`** — `bats hook/tests/clawgate-hook.bats hook/tests/clawgate-stop-hook.bats` on `bats/bats:1.11.0`.

Then `verdict`, plus a `finally: report` task (`clawgate-ci-report`) posting the commit status. Trigger `clawgate-ci-push` on the shared `el-github-listener`: **push-only** (no `pull_request`), **any branch** (`body.ref.startsWith('refs/heads/')`), path-filtered to `containers/clawgate/` **and** `clusters/homelab/apps/tekton-pipelines/triggers/clawgate-ci-` (a self-guard, so a change to the pipeline re-tests itself) — the `commits`-array test is OR'd with a `head_commit` fallback for GitHub's truncation of large pushes.

**Known-open 🟡 (all verified 2026-07-30, none fixed):**
- **Branch creation over-matches.** `git push -u origin newbranch` sends `commits: []` (first disjunct false) **plus a pre-existing `head_commit`** — and the second disjunct only checks `has(body.head_commit) && != null`, so if that already-tested tip touched `containers/clawgate/` a **full run fires on branch creation**. The file's own comment covers branch *deletion* (null head_commit — correctly safe) and misses creation. `auditloop-push-main` carries a `(!has(body.deleted) || body.deleted == false)` guard; clawgate-ci has no `created`/`deleted` guard.
- **PVC unpinned.** Per-run **6Gi** RWO `volumeClaimTemplate` with **no** `podTemplate.nodeSelector`, while *every* other pipeline pins `kubernetes.io/hostname: talos-xr6-r7p` (naida 8Gi, remix 12Gi, gitops-validate 6Gi, auditloop 10Gi). It works today (clawgate pods land on `talos-jkj-deb`) because it mounts only the one PVC — but it forgoes the shared nix cache and is the odd one out.
- **No concurrency control at all** — every matching push gets an independent PipelineRun and its own 6Gi PVC.
- **`error` vs `fail` is implemented for the CSS path ONLY** (`clawgate-ci-pipeline.yaml:329` writes `error` when `.css-failed` exists). The `extension` and `hook` legs only ever write `pass`/`fail`, so a crashed `npm install` or an unpullable bats image reports **"your change is bad"**. Partly compensated at aggregation: a *missing* verdict file is treated as the error class and the summary separates `FAILED:` from `COULD NOT RUN:`.

## 🔴 Merging a trigger does NOT fire the first run — reconcile FIRST

The single most expensive Tekton gotcha here. **The webhook arrives within seconds of the merge; the Flux `tekton-triggers` Kustomization reconciles on a 5-minute interval; GitHub does not retry.** So the push that "should" have started CI hits an EventListener that does not yet carry the trigger, and is **silently dropped**. Absence of a PipelineRun is not evidence that anything is fine.

**Correct sequence:** merge → `flux reconcile kustomization tekton-triggers` → confirm the trigger is live on the CR (`kubectl -n tekton-ci get eventlistener github-listener -o jsonpath='{.spec.triggers[*].name}'`) → **then** push, or create a PipelineRun by hand.

**Editing `spec.triggers` does NOT restart the EventListener sink pod** — triggers are hot-read from the CR. Verified: `el-github-listener-…` was **14d old with 0 restarts, deployment generation 1**, while `eventlistener.yaml` had been committed three times in the preceding day; the pod was serving all six triggers and clawgate PipelineRuns fired on it. So "the pod is old" tells you nothing about whether a trigger is live — **read the CR**, not the pod.

## ⚠ The shared `eventlistener.yaml` is NOT safe to hand-edit

One file serves **six** live triggers — `naida-push-main`, `remix-push-trunk`, `gitops-validate-pr`, `gitops-validate-push-trunk`, `clawgate-ci-push`, `auditloop-push-main` — each a multi-line CEL expression, several structurally near-identical (the same `(c.added + c.modified + c.removed).exists(f, f.startsWith('containers/<app>/'))` shape). A blind `count=1` text replace cannot be assumed to land on the trigger you meant, and a paren-level mistake in one trigger's CEL is a config error that can take **all six** down. **Put a `cel-go` harness in the loop for any CEL change** (the remix filter was validated that way over 6 cases incl. truncated-tip and null-head), and confirm by `git diff` which occurrence actually moved.

## Dashboard

Read-only. `kubectl -n tekton-pipelines port-forward svc/tekton-dashboard 9097:9097` → http://localhost:9097. Public exposure (`tekton.zacx.dev` + Authelia) is **DEFERRED**.

## Deploy / change pattern

- Work in a **worktree off `homelab-infra` origin/trunk** (NOT the stale `homelab-talos` checkout — gotcha #2) → PR → **Flux reconciles**.
- `flux` is **not on PATH**: `nix-shell -p fluxcd --run "flux reconcile source git flux-system && flux reconcile kustomization <name>"`.
- SOPS edits: `nix-shell -p sops --run "sops <file>.enc.yaml"`, **age key** `~/workspace/homelab-talos/.secrets/age.key` (the .secrets dir is fine; only the talos *manifests* are stale).
- After a gateway configmap change: `kubectl -n nebula rollout restart ds/nebula-gateway` (no reloader — gotcha #1).

## Adding a new pipeline / new repo

1. Add a **Trigger** on `el-github-listener` with a **CEL filter** scoping the exact `repository.full_name` + `ref` (the App is org-wide → always scope, gotcha under GitHub App). Validate the CEL with `cel-go` before merging — the file is shared by six triggers (see the hand-edit warning above). Guard branch **creation** (`commits: []` + a live `head_commit`) and **deletion** (null `head_commit`) explicitly; clawgate-ci gets the creation case wrong.
2. TriggerTemplate → PipelineRun (clone via minted installation token; public base image, **no imagePullSecret** — gotcha #4).
3. To pull results back into a gate, use the **auditloop read API** (`AUDITLOOP_API_TOKEN`, `fetch-findings.mjs`) — see the `auditloop` + `ux-audit-loops` skills.
4. Ephemeral workspace only (no RWX — gotcha #3); expect cold runs.
