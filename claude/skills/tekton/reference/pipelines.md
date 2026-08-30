# tekton — per-pipeline detail

Read this when you are **debugging, changing or copying a specific pipeline**. The
platform facts, the 5 critical gotchas and the add-a-pipeline checklist are in `SKILL.md`.

## `naida-ux-audit` (ns `tekton-ci`)

naida push → `main` → EventListener → **CEL filter** (`ZacxDev/naida-ai` +
`refs/heads/main`) → mint installation token → **clone** → post GitHub **`pending`**
status → **`nix develop`** walk (`make ux-audit-lms`, boots DEV_MODE naida + chromium) →
**push run to auditloop** (P5 plugin API) → **`fetch-findings.mjs --fail-on-regression`**
gate → post **`success`** / **`failure`** commit status (context **`tekton/ux-audit-lms`**).

- Image **`nixos/nix:2.24.9`** (flakes enabled). naida gets `make` from its flake devShell.
- auditloop creds (push URL/token + read `AUDITLOOP_API_TOKEN`) in the **`auditloop-creds`**
  secret.
- Uses the **persistent node-pinned nix cache** — warm walk-gate ~1m15s, cold ~3m04s. Cache
  PVC `nix-store-cache` on `talos-xr6-r7p`; nuke it
  (`kubectl -n tekton-ci delete pvc nix-store-cache`) to force a clean reseed if it corrupts.
- Gate keys on the **deterministic a11y-rule delta only** (`new_a11y_rules`); the visual
  pixel diff is **advisory, not gated** (naida #149 — sub-1% LMS render non-determinism no
  longer reddens CI). Opt back in with
  `fetch-findings.mjs --fail-on-visual [--visual-threshold N]`.

## `remix-ux-audit` (ns `tekton-ci`) — homelab-infra #123

Same monorepo (`homelab-infra`), CEL-scoped to `refs/heads/trunk` + a commit touching
`containers/remix/**`. Trigger `remix-push-trunk` on the SAME `el-github-listener`.
Push → walk remix (14 surfaces × mobile+desktop) → push run to the **`remix-funnel`** plugin
target → gate on the a11y-rule delta → post commit status **`tekton/ux-audit-remix`** on
`ZacxDev/homelab-infra`. On top of the naida pattern:

- **Postgres + Redis SIDECARS** (Tekton pods have no Docker daemon). remix's harness reaches
  them via **`REMIX_TEST_DATABASE_URL`**
  (`postgres://remix:x@127.0.0.1:5432/remix?sslmode=disable`) + **`REMIX_TEST_REDIS_ADDR`**
  (`127.0.0.1:6379`) — the `internal/dbtest` external hooks + `DockerAvailable()` bypass
  landed in remix via homelab-infra **#122**. A `wait-postgres` step gates the walk on real
  sidecar readiness (the harness connect has no retry loop).
- **CEL PATH filter** —
  `body.commits.exists(c, c.{added,modified,removed}.exists(f, f.startsWith('containers/remix/')))`
  with a **`head_commit` fallback** (GitHub truncates the `commits` array on large pushes →
  also test `head_commit`). Verified against cel-go.
- **Impure nix-shell** — remix's `e2e/run.sh` uses `nix-shell -p ...` (not a flake devShell),
  so the walk runs under an outer `nix-shell -p bash gnumake nix` with **`NIX_PATH` pinned**
  to the repo's `flake.lock` nixpkgs rev. (The base `nixos/nix` image has neither `make` nor
  a guaranteed `bash` on PATH.)
- Reuses the **SHARED `nix-store-cache` PVC** (both pipelines node-pin to `talos-xr6-r7p`, so
  both pods mount the one RWO local PVC; the seed-nix mkdir-lock tolerates concurrent
  same-node seeders). Separate **`remix-auditloop-creds`** SOPS secret (remix's Makefile
  reads `AUDITLOOP_PUSH_TOKEN`, a single string, vs naida's `AUDITLOOP_PUSH_TOKENS` map).
- Gate keys on `diff.new_a11y_rules` only; visual diff advisory (matches naida #149).
- **Verified LIVE end-to-end via the REAL webhook**: a trunk push touching
  `containers/remix/**` (`e4300274`) auto-spawned PipelineRun `remix-ux-audit-99pvx` → walk
  (pushed run `626190dc`) → gate PASS → `tekton/ux-audit-remix` **success**. Plus a pre-merge
  manual PipelineRun (GREEN), cel-go CEL validation (6 cases incl. truncated-tip and
  null-head), and a deterministic RED-path gate proof.
- **Deferred fast-follow (advisory only):** the gate reads `runs/latest`, not the SPECIFIC
  pushed `run_id` — a TOCTOU window where a concurrent push could shift the "latest" read.
  Harden by keying the gate to the `run_id` the walk pushed.

## `clawgate-ci` (ns `tekton-ci`) — the repo's REAL pre-merge gate

**GitHub Actions is billing-blocked repo-wide.** Every workflow run fails in 3–16s with
`steps: 0` and the annotation *"The job was not started because recent account payments have
failed or your spending limit needs to be increased"* (verified via
`gh api repos/ZacxDev/homelab-infra/actions/runs` + the job annotation; the log blob 404s).
**Red Actions checks on this repo are NOISE, not signal** — do not debug them, and do not
read a green/red Actions check as a gate. `.github/workflows/clawgate-ci.yml` survives only
as `name: clawgate e2e (manual)`, `on: workflow_dispatch`, carrying the one e2e job that was
never ported.

Definition: `clusters/homelab/apps/tekton-pipelines/triggers/clawgate-ci-pipeline.yaml` +
`clawgate-ci-triggertemplate.yaml`.
⚠ The three "legs" are **steps of ONE Task** (`clawgate-ci`), NOT three Pipeline tasks —
`mint-token`, `clone`, `status-pending`, `wait-postgres`, `build-css`, then:
- **`go`** — `go build ./... && go vet ./... && go test -race -cover ./...` against a
  **`postgres:16-alpine` SIDECAR** (`CLAWGATE_TEST_DATABASE_URL=…@127.0.0.1:5432/clawgate_test`).
- **`extension`** — `npm install && npm run coverage` in `containers/clawgate/extension`.
- **`hook`** — `bats hook/tests/clawgate-hook.bats hook/tests/clawgate-stop-hook.bats` on
  `bats/bats:1.11.0`.

Then `verdict`, plus a `finally: report` task (`clawgate-ci-report`) posting the commit
status. Trigger `clawgate-ci-push` on the shared `el-github-listener`: **push-only** (no
`pull_request`), **any branch** (`body.ref.startsWith('refs/heads/')`), path-filtered to
`containers/clawgate/` **and** `clusters/homelab/apps/tekton-pipelines/triggers/clawgate-ci-`
(a self-guard, so a change to the pipeline re-tests itself) — the `commits`-array test is
OR'd with a `head_commit` fallback for GitHub's truncation of large pushes.

**Known-open 🟡 (all verified 2026-07-30, none fixed):**
- **Branch creation over-matches.** `git push -u origin newbranch` sends `commits: []` (first
  disjunct false) **plus a pre-existing `head_commit`** — and the second disjunct only checks
  `has(body.head_commit) && != null`, so if that already-tested tip touched
  `containers/clawgate/` a **full run fires on branch creation**. The file's own comment
  covers branch *deletion* (null `head_commit` — correctly safe) and misses creation.
  `auditloop-push-main` carries a `(!has(body.deleted) || body.deleted == false)` guard;
  clawgate-ci has no `created`/`deleted` guard.
- **PVC unpinned.** Per-run **6Gi** RWO `volumeClaimTemplate` with **no**
  `podTemplate.nodeSelector`, while *every* other pipeline pins
  `kubernetes.io/hostname: talos-xr6-r7p` (naida 8Gi, remix 12Gi, gitops-validate 6Gi,
  auditloop 10Gi). It works today (clawgate pods land on `talos-jkj-deb`) because it mounts
  only the one PVC — but it forgoes the shared nix cache and is the odd one out.
- **No concurrency control at all** — every matching push gets an independent PipelineRun and
  its own 6Gi PVC.
- **`error` vs `fail` is implemented for the CSS path ONLY** (`clawgate-ci-pipeline.yaml:329`
  writes `error` when `.css-failed` exists). The `extension` and `hook` legs only ever write
  `pass`/`fail`, so a crashed `npm install` or an unpullable bats image reports **"your
  change is bad"**. Partly compensated at aggregation: a *missing* verdict file is treated as
  the error class, and the summary separates `FAILED:` from `COULD NOT RUN:`.

## Retrying the devrc-ci unpin — what is measured, and the control that is INVALID

Context: `6bec075e` (2026-08-29T22:09Z) replaced the node-pinned `nix-store-cache` PVC with a
per-node hostPath; `7839ef54` (2026-08-30T00:29Z) reverted it after 42 tests failed on every
devrc PR with `error: opening lock file "/nix/var/nix/db/big-lock": Permission denied`. The
SKILL.md gotcha 6(a) carries the summary; this is the diagnosis state.

🔴 **`nix-store-cache` is itself a hostPath** — PV
`hostPath {path: /var/lib/mnt/disk-1/pvc-aef79024…_tekton-ci_nix-store-cache, type: DirectoryOrCreate}`,
the same disk and the same `DirectoryOrCreate` as the reverted
`/var/lib/mnt/disk-1/devrc-ci-nix-cache`. So the storage KIND is not the variable.

**Three measured differences. Which one causes the lock failure is UNKNOWN** — `7839ef54`'s
*"the PVC works only because earlier runs populated its `/nix` with ownership the nix build user
can use"* is **unproven, not refuted**:

1. **Root-directory mode.** `local-path-config`'s setup script is `mkdir -m 0777 -p "$VOL_DIR"`,
   so kubelet's `DirectoryOrCreate` finds the dir already at **0777**; a bare hostPath is created
   **0755 root:root**. ⚠ Weak on its own: both volumes are filled by the identical
   `cp -a /nix/. /nix-cache/` from the identical image, and `cp -a` preserves modes below the
   root — so `/nix/var/nix/db` is the same either way, and 0755 already grants `r-x` to other.
   A bare `chmod 0777` on the root adds write **at the root level only**, which is not where the
   quoted error is. Cheap to try; do not expect it to be sufficient.
2. 🔴 **Build users — and this is what makes the obvious control invalid.** The gate's
   `NIX_CONFIG` is `experimental-features = nix-command flakes` only, so nix drops to an
   unprivileged **`nixbld`** user. `gitops-validate`'s `warm-tools` adds `build-users-group =`,
   which **disables build users**, so it runs as **root**. A fresh `nix-store-cache-2` (created
   `2026-08-25T05:33:47Z`) staying healthy therefore says nothing about build-user ownership:
   root can always take the lock. **Do not cite `gitops-validate` as a control for this.**
3. **A heal that already exists, on the gate only.** The `pytests`/`nodetests` steps run
   `mkdir -p /nix/var/nix/profiles/per-user && chmod 755 /nix/var/nix/profiles …` against the
   same root-owned-store problem one path over, with a MEASURED failure recorded beside it
   (`could not set permissions on '/nix/var/nix/profiles/per-user' to 755: Operation not
   permitted`, probes `devrc-ci-probe8-qbbg4` / `-probe10-b6jzh`) and the note that it
   *"re-heals a fresh cache PVC"*. **Read that comment before designing anything** — it is the
   closest thing to a worked diagnosis of this class that exists, and it shows a fresh volume
   being made usable by an explicit heal rather than by accumulated history.

🔴 **Prove any fix on a scratch pipeline, never on `devrc-ci`.** `7839ef54` deliberately chose a
full revert over a permission patch: *"this volume has now broken CI twice in one evening … the
place to find the third failure mode is not production."* devrc `main` is `enforce_admins: true`
with both gate legs required, so a wrong guess blocks every contributor's merges.

**The perf baseline is not re-usable as recorded.** The unpin's quoted wins — three nodes
reachable instead of one, queue wait **17–22m → 0.1m**, wall clock **39.1m median → 17.4m** — are
quoted from `7839ef54` and are not re-derivable (the runs are pruned). They were taken while
`requests.cpu` was **4** (`23887675`, 2026-08-29 16:03 −05:00); `bb62668f` put it back to **2** at
19:14, 15 min before the revert, and **2 is live today**. `bb62668f`'s own subject — *"the equality
fixed starvation and bought a queue nothing drained"* — targets the same queue the unpin is
credited with draining. **Re-take the baseline at cpu 2 before grading a retry.**
