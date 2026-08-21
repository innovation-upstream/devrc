---
name: tekton
description: "Operate the homelab Tekton CI/CD platform — Pipelines/Triggers/Dashboard, the GitHub webhook (tekton-webhook.zacx.dev), the naida-ux-audit pipeline, and clawgate-ci (the real pre-merge gate). Use for: Tekton, the homelab CI platform, clawgate-ci, the Tekton webhook/dashboard/EventListener, adding a pipeline, a trigger that did not fire, the hostNetwork host-port collision."
---

# Tekton — homelab CI/CD platform

GitOps CI on the **homelab** cluster (Flux-managed). Cross-ref: `auditloop` (read API +
push), `ux-audit-loops` (the walk + `--fail-on-regression` gate).

**Reference file** (repo-absolute; read on demand):
`~/workspace/devrc/claude/skills/tekton/reference/pipelines.md` — full per-pipeline detail
for `naida-ux-audit`, `remix-ux-audit` and `clawgate-ci` (steps, sidecars, CEL filters,
creds, gate semantics, and clawgate-ci's four known-open 🟡 issues). Read it before
debugging, changing or copying a specific pipeline.

## 🔴 CRITICAL GOTCHAS — read first

1. **hostNetwork gateway host-port collisions.** The nebula public gateway (BOTH prod +
   homelab) is `hostNetwork: true`, so any new nginx `listen` port must be verified free at
   the **HOST** level, NOT just in the nginx config. Decode `/proc/net/tcp` on the **LIVE**
   gateway pods (hex port column) — do not trust the configmap. **k0s reserves 9099;
   node-exporter binds 9100.** Using **9099 CRASHED the shared gateway and took down every
   public app.** Pick a high, verified-unused port (**19100**, matching MinIO's `19099`
   convention). The **homelab** gateway nginx has **no reloader sidecar** → after a configmap
   change `kubectl -n nebula rollout restart ds/nebula-gateway`, or it serves 502. Keep
   `git revert` + `flux reconcile` staged to restore the gateway in ~2 min.
2. **Stale `~/workspace/homelab-talos` checkout (~100 commits behind).** NEVER read/edit it
   as authoritative — it caused a wrong "Tekton is already installed" conclusion. Verify
   cluster state against **origin/trunk of `homelab-infra`** AND the **LIVE cluster** (a
   running controller pod, not just a CRD's presence).
3. **No RWX storage** — only local-path / openebs (RWO, node-pinned). Two RWO local-path PVCs
   in one pod would **DEADLOCK scheduling** (each binds a different node) UNLESS the pod is
   **node-pinned** (`taskRunTemplate.podTemplate.nodeSelector`), which forces both PVCs onto
   one node. **That is exactly how the persistent nix cache works** (PR #115): a static
   `nix-store-cache` PVC + the per-run `source` PVC both pinned to `talos-xr6-r7p`. A
   `seed-nix` step copies the image's `/nix` into the PVC once (sentinel `.seeded` + atomic
   mkdir lock, self-healing on a dead seeder), then `walk-gate` overmounts `/nix`. MEASURED
   walk-gate **cold 3m04s → warm 1m15s** (~1m50s saved) — the old "~2–15 min cold" figure was
   **wrong** (cache.nixos.org is fast here; cold ≈ 3 min). Tradeoff: if `talos-xr6-r7p` is
   down, runs Pend.
4. **Placeholder imagePullSecret breaks ALL pulls.** A `harbor-cred` dockerconfigjson with a
   non-base64 `auth` placeholder makes every pod fail image pull ("illegal base64 data").
   **Do NOT attach a placeholder imagePullSecret** to the pipeline SA — public images
   (`nixos/nix`) need none.
5. **Operator uninstall leaves orphans.** Deleting the Flux-managed operator does NOT remove
   operator-CREATED components/CRDs/webhooks. A clean teardown must manually delete the
   operator CRs (force-remove finalizers — no controller left to run them), admission
   webhooks, CRDs, namespaces, and cluster RBAC.

6. 🔴 **These pipelines are DETECTORS, not GATES — nothing can block a merge on them.**
   `homelab-infra` is private on a plan where **branch protection AND rulesets both return
   403** (`"Upgrade to GitHub Pro or make this repository public"`, on
   `/branches/trunk/protection` and `/rulesets`), so a **required status check cannot be
   configured at all**. Measured 2026-08-21 on four PRs: three were **merged BEFORE** their
   `tekton/gitops-validate` status settled (#338 by 109 s, #339 by 110 s, #341 by 66 s).
   So "it has a green check" and "it was validated before it landed" are different claims —
   never write a comment asserting the second. Consequence worth acting on: the **trunk-push**
   leg is the one that reliably observes anything, which makes the CEL path list in
   `eventlistener.yaml` load-bearing rather than cosmetic — **when you add a leg reading a path
   outside `clusters/**`, add that path to the filter in the SAME commit** (two legs were
   missing from it as of #369).
7. 🔴 **A PipelineRun executes the DEPLOYED Task, not the PR's version — so a PR that adds a
   leg cannot exercise that leg.** The `Task` is a Flux-reconciled cluster object; only the
   *scripts it runs* come from the PR checkout. Measured on #369: the PR's own green
   `gitops-validate` ran an **11-step** Task with no `clickup-mirror` step, and the merge's own
   trunk-push run did too (it fired before Flux reconciled). Verify a new leg by reading the
   live object — `kubectl -n tekton-ci get task gitops-validate -o jsonpath='{range
   .spec.steps[*]}{.name}{"\n"}{end}'` — and then watch the **first run after** the reconcile.
   A green check on the PR that adds a leg is not evidence about the leg.

## What / where

- **Tekton Operator v0.80.0** → Pipelines **1.12.2** / Triggers **0.36.0** / Dashboard
  **0.68.0**. **Chains + Results OFF.** Pruner keep-**100**, daily. Dashboard **read-only**.
- Namespaces: **`tekton-pipelines`** (control plane) + **`tekton-ci`** (CI workloads,
  EventListener, PipelineRuns).
- GitOps via **Flux**, repo **`ZacxDev/homelab-infra`** branch **`trunk`**, under
  `clusters/homelab/apps/tekton-pipelines/`.
- Kubeconfig: `~/workspace/homelab-infra/homelab-kubeconfig`.

## GitHub App — `tekton-homelab`

- App ID **4320115**, installation **147102541**, **org-wide**. Perms: **Contents:R +
  Commit-statuses:RW + Metadata:R**. Events: **push + pull_request**.
- Creds sealed in **`github-app.enc.yaml`** (SOPS). Used to **mint installation tokens** at
  pipeline time → clone private repos + post commit statuses.
- Org-wide install ⇒ **ALWAYS scope a trigger to a specific repo + branch via CEL.**

## Public webhook

`tekton-webhook.zacx.dev` → **`el-github-listener`** EventListener (`tekton-ci`),
**HMAC-authed** (secret token), on **host port 19100** (see gotcha #1).

Path: GitHub → Cloudflare → **prod Traefik** → prod nginx `0.0.0.0:19100` →
`10.42.0.10:19100` → **homelab nginx** → `el-github-listener:8080`.

## Pipelines (detail in `~/.claude/skills/tekton/reference/pipelines.md`)

| Pipeline | Trigger / scope | Gate + commit status |
|---|---|---|
| `naida-ux-audit` | `naida-push-main` — `ZacxDev/naida-ai` + `refs/heads/main` | a11y-rule delta → `tekton/ux-audit-lms` |
| `remix-ux-audit` | `remix-push-trunk` — `homelab-infra` + `refs/heads/trunk` + path `containers/remix/**` | a11y-rule delta → `tekton/ux-audit-remix` |
| `clawgate-ci` | `clawgate-ci-push` — push-only, ANY branch, path `containers/clawgate/` | go / extension / hook legs → commit status |

`clawgate-ci` is the repo's **real pre-merge gate**: GitHub Actions is billing-blocked
repo-wide, so **red Actions checks on `homelab-infra` are noise, not signal** — don't debug
them and don't read them as a gate. Four known-open 🟡 issues (branch-creation over-match,
unpinned PVC, no concurrency control, `error`-vs-`fail` on the CSS path only) are in the
reference file. Six triggers share `el-github-listener`: `naida-push-main`,
`remix-push-trunk`, `gitops-validate-pr`, `gitops-validate-push-trunk`, `clawgate-ci-push`,
`auditloop-push-main`.

### `gitops-validate` — the gitleaks leg (hardened #265)

`clusters/homelab/apps/tekton-pipelines/triggers/gitops-validate-pipeline.yaml`. Legs:
kustomize + kubeconform + gitleaks + helm render-diff + sops-rules.

- **Baseline line-drift now has its OWN verdict.** The status description reads
  `BASELINE DRIFT: gitleaks` instead of masquerading as `FAILED: gitleaks`. That masquerade
  is what made the gate look permanently broken. (Drift still sets the commit state to
  `failure` — only the description distinguishes it.) Third class: `COULD NOT RUN: <leg>`.
- **`<homelab-talos>/scripts/check-gitleaks-baseline.py`**
  (+ `<homelab-talos>/scripts/tests/test-check-gitleaks-baseline.sh`).
  rc: `0` clean · `1` drift ONLY · `2` usage/environment error · `3` drift **AND** a finding
  with no baseline counterpart. 🔴 **It fails CLOSED** — `die()` is `NoReturn`/rc=2 for a
  missing, empty, unreadable or malformed baseline. An earlier revision let an `OSError`
  escape as an unhandled traceback → Python rc=1 → which this very scheme maps to the
  *benign* drift verdict: a broken guard reporting itself as routine bookkeeping. Every
  failure path must go through `die()`, never a traceback.
- **Fixture suppression = rule + path + one exact value** (was 11 globally-scoped literals,
  10 of which were never findings). 🔴 **A GLOBAL `paths` allowlist exempts the file
  wholesale even with `condition = "AND"`** — planted creds inside it go undetected; only a
  rule-scoped `[[rules.allowlists]]` honours the AND. Measurement table is in `.gitleaks.toml`.
- 🔴 **gitleaks 8.30.1 detects NO AWS key shape at all** (`useDefault = true`; neither `AKIA…`
  nor a 40-char secret) — an AWS-shaped negative test is a **permanent false green**. Prove
  the gate still fires with a `ghp_` token, generic-api-key, or a private-key header.

## 🔴 Merging a trigger does NOT fire the first run — reconcile FIRST

The single most expensive Tekton gotcha here. **The webhook arrives within seconds of the
merge; the Flux `tekton-triggers` Kustomization reconciles on a 5-minute interval; GitHub
does not retry.** So the push that "should" have started CI hits an EventListener that does
not yet carry the trigger, and is **silently dropped**. Absence of a PipelineRun is not
evidence that anything is fine.

**Correct sequence:** merge → `flux reconcile kustomization tekton-triggers` → confirm the
trigger is live on the CR
(`kubectl -n tekton-ci get eventlistener github-listener -o jsonpath='{.spec.triggers[*].name}'`)
→ **then** push, or create a PipelineRun by hand.

**Editing `spec.triggers` does NOT restart the EventListener sink pod** — triggers are
hot-read from the CR. Verified: `el-github-listener-…` was **14d old with 0 restarts,
deployment generation 1**, while `eventlistener.yaml` had been committed three times in the
preceding day; the pod was serving all six triggers and clawgate PipelineRuns fired on it.
So "the pod is old" tells you nothing about whether a trigger is live — **read the CR**, not
the pod.

## ⚠ The shared `eventlistener.yaml` is NOT safe to hand-edit

One file serves **six** live triggers, each a multi-line CEL expression, several structurally
near-identical (the same
`(c.added + c.modified + c.removed).exists(f, f.startsWith('containers/<app>/'))` shape). A
blind `count=1` text replace cannot be assumed to land on the trigger you meant, and a
paren-level mistake in one trigger's CEL is a config error that can take **all six** down.
**Put a `cel-go` harness in the loop for any CEL change** (the remix filter was validated
that way over 6 cases incl. truncated-tip and null-head), and confirm by `git diff` which
occurrence actually moved.

## Dashboard

Read-only. `kubectl -n tekton-pipelines port-forward svc/tekton-dashboard 9097:9097` →
http://localhost:9097. Public exposure (`tekton.zacx.dev` + Authelia) is **DEFERRED**.

## Deploy / change pattern

- Work in a **worktree off `homelab-infra` origin/trunk** (NOT the stale `homelab-talos`
  checkout — gotcha #2) → PR → **Flux reconciles**.
- `flux` is **not on PATH**:
  `nix-shell -p fluxcd --run "flux reconcile source git flux-system && flux reconcile kustomization <name>"`.
- SOPS edits: `nix-shell -p sops --run "sops <file>.enc.yaml"`, **age key**
  `~/workspace/homelab-talos/.secrets/age.key` (the `.secrets` dir is fine; only the talos
  *manifests* are stale).
- After a gateway configmap change: `kubectl -n nebula rollout restart ds/nebula-gateway`
  (no reloader — gotcha #1).

## Adding a new pipeline / new repo

1. Add a **Trigger** on `el-github-listener` with a **CEL filter** scoping the exact
   `repository.full_name` + `ref` (the App is org-wide → always scope). Validate the CEL with
   `cel-go` before merging — the file is shared by six triggers (see the hand-edit warning).
   Guard branch **creation** (`commits: []` + a live `head_commit`) and **deletion** (null
   `head_commit`) explicitly; clawgate-ci gets the creation case wrong.
2. TriggerTemplate → PipelineRun (clone via minted installation token; public base image,
   **no imagePullSecret** — gotcha #4).
3. To pull results back into a gate, use the **auditloop read API** (`AUDITLOOP_API_TOKEN`,
   `fetch-findings.mjs`) — see the `auditloop` + `ux-audit-loops` skills.
4. Ephemeral workspace only (no RWX — gotcha #3); expect cold runs unless you node-pin to
   `talos-xr6-r7p` and mount the shared `nix-store-cache` PVC.
5. Merge → **`flux reconcile kustomization tekton-triggers` → confirm on the CR → THEN push.**
