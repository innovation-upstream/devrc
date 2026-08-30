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
2. **Never treat a local checkout as authoritative for cluster state** — verify against
   **origin/trunk** AND the **LIVE cluster** (a running controller pod, not just a CRD's
   presence). A stale `~/workspace/homelab-talos` once caused a wrong "Tekton is already
   installed" conclusion. 🔴 **But do NOT carry a currency claim in prose — this line said
   "~100 commits behind" and was measured 2026-08-24 at ZERO behind, while
   `~/workspace/homelab-infra` was 3 behind: the advice had inverted.** Both directories are
   clones of the SAME repo (`ZacxDev/homelab-infra`) despite the `homelab-talos` name, so
   neither name tells you which is fresher. Measure at the moment you act:
   `git -C <dir> fetch origin && git -C <dir> rev-list --count HEAD..origin/trunk`.
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
   🔴 **AND PUSHING N BRANCHES IS NOT N INDEPENDENT ACTIONS — IT IS ONE BLAST-RADIUS ACTION.**
   Node-pinning means every run lands on `talos-xr6-r7p`, so a burst of pushes stacks full
   pipeline runs onto one node with **no concurrency control** (the known-open issue below).
   Measured 2026-08-23: five branches pushed in quick succession took that node to 77% CPU
   requests / 237% limits with 424 Completed + 97 Error pods resident, the cluster began
   emitting `ExceededNodeResources: Insufficient resources to schedule pod`, and steps were
   **killed with exit 255**. The rate moved **2/54 (4%) → 8/34 (24%)**. It is not your branch:
   **anyone else's PR checks in that window die too.** Push, wait for the queue to drain, push.
   🔴 **`exited with code 255` is the CONGESTION signature, and it names whichever step was
   running** — it appeared in `step-clone` AND `step-pytests` in the same burst, which reads as
   two unrelated bugs and is one. The check then posts `NOT RUN: <leg> — the gate stopped
   before this leg reported`: **a broken gate, not a bad change — do not debug your diff
   against it.** The tell that it is congestion rather than code: it heals when the queue
   drains, and a code cause does not.
   🔴 **But 255 is NOT the same claim as "no test failed" — READ WHETHER THE STEP PRINTED A
   VERDICT.** "It heals when the queue drains" is a true tell and a slow one; the log answers
   in one read. Measured 2026-08-24 over all completed `devrc-ci` gate TaskRuns (classified by
   which step died and with what code, NOT by the PipelineRun verdict): the failures split
   **~27 KILLED steps** — 25× `step-pytests` 255, 1× `step-nodetests` 255, 1× `137`/OOMKilled,
   which reported no test result at all — against **~27 GENUINE single-test failures**, ~1 test
   in ~15,500, surfacing as `verdict exit=1`. The genuine ones do **NOT** correlate cleanly
   with concurrency (several ran with ≤1 overlap), so congestion is an **amplifier, not the
   cause**, and "the gate is just flaky under load" will walk you straight past a real bug.
   Discriminator: a step that emitted `RESULT:` / `<leg> verdict=` **failed a test**; one that
   emitted neither was **killed**. 25 of the 27 kills had ≥4 gate TaskRuns overlapping.
   🔴 **FIXED 2026-08-25 BY homelab-infra #396 — the paragraphs below are HISTORY, kept so
   the signature is recognisable if it ever returns. Do not go hunting this.** #396 gave
   `gitops-validate` its own nix cache and node, so the two pipelines no longer share one.
   Measured 11h later: **before, 30/121 runs killed (24.8%); after, 0/9 (100% passed)**, with
   `devrc-ci` on `talos-xr6-r7p` and `gitops-validate` on `talos-uvh-gtj`. ⚠ n=9 is
   suggestive, not conclusive — ~2 kills would be expected in 9 runs at the old rate, so a
   clean 9 happens by luck about 1 in 12; re-measure before treating it as settled.
   🔴 **THE 255s WERE SCHEDULER PREEMPTION, AND `gitops-validate` WAS THE PREEMPTOR — CI
   PREEMPTED CI.** Confirmed 2026-08-25T04:46:13Z by catching a burst live: five gate pods,
   five explicit `Preempted` events, three preemptors, all five `pytests` steps terminating
   `exit=255` within ONE SECOND having started minutes apart. The preemptors are priority-**0**
   `gitops-validate` pods blocked on `0/4 nodes are available: 1 Insufficient cpu, 3 node(s)
   didn't match Pod's node affinity/selector` — **both pipelines `nodeSelector`-pin to the SAME
   single node** out of four, so gitops-validate cannot fit and the scheduler evicts the
   `ci-bulk` (**-10000**) devrc gate pods to make room. One-directional by design: devrc-ci is
   denied the reverse (`preemption: not eligible due to preemptionPolicy=Never`). It often does
   not even buy the preemptor a slot — `not eligible due to a terminating pod on the nominated
   node` recurs, so victims die and the preemptor stays Pending. Full evidence:
   `<homelab-infra>/claudedocs/handoff-devrc-ci-kills-are-simultaneous.md`.
   🔴 **DO NOT DESIGN A FIX FROM THIS SKILL — the analysis lives in the manifests, and three
   obvious fixes are ALREADY REJECTED WITH MEASUREMENTS.** Read the comments in
   `triggers/ci-priority-classes.yaml` (~100), `triggers/gitops-validate-triggertemplate.yaml`
   (~98) and `triggers/devrc-ci-pipeline.yaml` (~1130) before proposing anything. Rejected
   there: **concurrency capping** (simulated on the real arrival trace — worse at every cap
   that helps, because a queued TaskRun's clock starts at CREATION and burns its own
   deadline), **ResourceQuota** (cannot be scoped safely — scoped to `ci-bulk` it covers the
   `notify`/`report`/affinity pods that declare no requests, and losing `report` is the worst
   failure here), and **`retries`** (tried for this, REVERTED as a trap — Tekton retries any
   non-cancelled failure, so it re-runs genuine verdicts). The lever actually taken was
   right-sizing gitops-validate's requests (4.65 → 2.40 CPU), which said in writing that it
   does **not** end preemption.
   ⚠ **Measured at 04:46Z and MOOT since #396 that morning: the binding predicate was CPU,
   not pod count** (`0/4 nodes are available: 1 Insufficient cpu`). Kept only because a
   `pods`-predicate kill looks IDENTICAL to a CPU one — same `reason=Preempted`, same exit
   137/255 — while every CPU number reads healthy, so that is the discrimination to redo if
   this ever returns. It does **not** make right-sizing `auditloop-ci` (2.8×) or `clawgate-ci`
   (2.4×) urgent: that argument rested on contention which #396 removed.
   ⚠ Also measured-absent, so don't reach for it: moving devrc-ci's requests/limits. #393 put
   `step-pytests` at **0.006, not throttled**, and a per-pod limit cannot kill five pods of
   different ages in the same second anyway.
   🔴 **The real slowness mechanism, when it IS present: CFS QUOTA STARVATION, and AVERAGE CPU
   HIDES IT.** A step's own `limits.cpu` is enforced per 100ms CFS period, so a suite of
   short-lived multi-threaded processes drains a 1-CPU quota in a few ms and stalls for the
   rest of every period — averaging ~0.32 cores, which reads as *idle*, while throttled in
   **100%** of periods. **Low mean CPU + high throttle ratio is the signature**; read
   `container_cpu_cfs_throttled_periods_total / container_cpu_cfs_periods_total`
   (namespace=`tekton-ci`), never average CPU, and never node pressure alone. Measured
   2026-08-24 (#393): `scripts-tests` **1.00**, `gitleaks` 0.80, `clickup-mirror` 0.74 — all
   starved and since raised — while `kustomize` and `render-diff` sat at **0.00 on the same
   2-CPU limit**. That contrast is the point: it is starvation of specific BURSTY steps, not
   "the limits are too low", so only measured steps should move.
   🔴 **Raising the limit alone can HALF-WORK on a Go binary** — Go before 1.25 sizes
   `GOMAXPROCS` from the HOST's cores, not from the cgroup quota, so it oversubscribes and
   stays partly throttled at any cap. Pin `GOMAXPROCS` to the limit as well.
   🔴 **Do NOT measure a flake rate from inside a burst you are causing.** That mistake was
   made here and written into a handoff as a property of the CI tier before it was caught —
   `claude/RULES.md` → *"a control that SHARES the step you doubt"*. Take the baseline from a
   window with no pushes of your own in it.
   🔴 **THE CLEAN COUNTER-DATAPOINT — read it before treating the 24% above as expected
   behaviour.** 2026-08-23 22:11Z, observed from OUTSIDE (someone else's branch series, none
   of the SHAs ours): **10 `devrc-ci` runs, 6 within 12 seconds** — a *larger* burst than the
   5-branch incident — against a node at 86% CPU / **94% memory** requests. Outcome:
   `ExceededNodeResources` on 4 gate TaskRuns, **3 pods Pending ~4 min, ZERO exit-255 kills**,
   and a full drain to 55% CPU / 0 pending within minutes. So the system **queued and
   recovered**; it did not eat anyone's checks. The 4%→24% figure is the contaminated one and
   the only evidence that a burst *kills* runs, so **do not build concurrency control on the
   strength of it** — Tekton 1.12 has no native limit, and a `ResourceQuota` is the wrong tool
   here (it converts queueing into hard pod-creation failures, i.e. it manufactures the
   outcome you are trying to prevent; `tekton-ci` deliberately has neither a quota nor a
   LimitRange). Re-measure from outside a self-caused window first. **Memory, not CPU, is the
   binding constraint** on the pinned node — that is the number to watch.
   ⚠ **"Queued and recovered" is not "harmless" — QUEUEING COSTS A VERDICT WHENEVER THE WAIT
   EATS THE BUDGET, and the two findings are compatible.** Measured on `gitops-validate`
   (2026-08-23): main-task pod-start latency **p50 1m07s, p90 11m09s, max 19m11s** (n=98,
   plus 16 TaskRuns whose pod never created a container) — against a **20m** ceiling. Result:
   **21 of 113 runs (18.6%) posted NOTHING.** Five of them had actually started running; one
   got through 10 of 13 steps before the clock ran out. So the burst does not kill the pod, it
   *delays* it — and whether that becomes a lost verdict is decided by the pipeline's own
   headroom, not by the burst. devrc (50m budget) drains and survives; gitops-validate, with
   the tightest budget and the worst latency, is the one that loses. **The lever is scheduling
   pressure, not the timeout** (#378 family). Note `clawgate-ci` — the one pipeline with **no
   `nodeSelector`** — has a pod-start max of **18s** rather than 19 minutes.
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
8. 🔴 **A pipeline-level timeout SKIPS `finally` — so a timed-out run posts NOTHING and the PR
   sits on `pending` forever.** Put the limit on the **PipelineTask** (`spec.tasks[].timeout`),
   never on `timeouts.tasks`/`timeouts.pipeline`. Measured three ways on v1.12.0 (a `sleep 300`
   task + a `finally` echoing a marker): `timeouts.tasks: 40s` → `PipelineRunTimeout`,
   children `[slow]`, **reporter TaskRun never CREATED**; `timeouts.pipeline: 40s` → identical;
   task-level `timeout: 40s` → `Failed`, children `[slow,reporter]`, **finally RAN**. The
   reserved `timeouts.finally` is **not honoured** on the budget-expiry path — reserving it
   looks like protection and is not. Across 447 retained PipelineRuns: **25 timeouts, 0 ran
   their report.** devrc was fixed in homelab-infra **#385**; the remaining five in **#386**,
   **MERGED + Flux-reconciled 2026-08-23** — confirmed on the LIVE Pipeline objects, which is
   the claim that matters, since a run executes the DEPLOYED Pipeline (gotcha #7):
   `clawgate-ci 25m · gitops-validate 20m · auditloop-ci 40m · remix 40m · naida 40m ·
   devrc gate 45m / notify 2m`. **All six are fixed; nothing here is outstanding.**
   🔴 **#386 KEEPS the outer `timeouts.*` budgets and adds task-level ones INSIDE them** — a
   backstop plus a bound that fires first. Safe only while the arithmetic holds, so verify it
   rather than eyeballing the diff: **Σ(`spec.tasks[].timeout`) < `timeouts.tasks`** (else the
   budget wins and `finally` is skipped again) **and `tasks + finally ≤ pipeline`**. Verified on
   #386 across all six: 5-minute margin each, envelope closed.
   🔴 **`retries:` re-opens the defect with every timeout value looking untouched** — Tekton
   restarts the timeout clock per ATTEMPT, so the first term is really
   **Σ(attempts × timeout) + startOffset**. `retries: 1` on `gitops-validate` would mean
   2×20m against a 25m budget. Unset on all six today; check before adding one.
   🔴 **NOTHING ENFORCES THAT ARITHMETIC — you are the only checker.** A server-side dry-run
   happily accepts `timeout: 99m` under `tasks: 30m`. Tekton *does* validate
   `tasks + finally ≤ pipeline` (`"30m0s + 5m0s should be <= pipeline duration"`) — but **NOT
   on a TriggerTemplate's `resourcetemplates`**, which is exactly where these budgets live. An
   invalid budget there applies cleanly and fails only when the EventListener tries to create
   the run ⇒ **no status posted**: this very bug, by another door. To actually validate one,
   lift the `timeouts` block into a standalone PipelineRun and `--dry-run=server` it.
   🔴 **The `finally` reporter is deliberately left UNBOUND — not an oversight.** `7038c77d`
   raised its budget after finding the reporter is slow to **SCHEDULE**, not slow to run (node
   congestion, gotcha #3). Strangling it re-creates the exact "posts NOTHING" bug the fix exists
   to close. Bound every `spec.tasks[]`; leave the reporter room.
   The numbers, because the intuitive fix is the wrong one: across ~490 report TaskRuns the
   `report-status` step has **never exceeded 28s**, while its pod-start latency reaches
   **282s**. `remix-ux-audit-f6vks` lost its verdict to a **5m** `finally` on a run only 9m48s
   long; `clawgate-ci-7smtg` survived by **15s**. Hence `finally: 10m` everywhere (#386).
   ⚠ **Do NOT "fix" this by adding `--max-time` to the reporter's curls** — that was the first
   diagnosis and an audit refuted it; it would have saved neither run. The curls genuinely are
   unbounded (2 per reporter, 3 for devrc), just not what costs verdicts.
   🔴 Bound EVERY task, not just the slow one — the task deadline is
   `taskStart + timeout` while the budget is `runStart + tasks`, so an unbounded early task
   (devrc's `notify` inherited the cluster's 1h default) lets them cross and re-opens this.
9. ⚠ **Gotcha 6 is scoped to `homelab-infra`, not to Tekton.** `innovation-upstream/devrc` is a
   DIFFERENT repo on a plan where protection works, and since 2026-08-23 it requires **both**
   `tekton/devrc-nodetests` and `tekton/devrc-pytests` (measured — re-measure, this moved
   twice in one day). A required check `ERROR`/`PENDING` ⇒ `mergeStateStatus=BLOCKED`,
   both `SUCCESS` ⇒ `CLEAN`. So on devrc a Tekton check **is** a gate — on either tier;
   the earlier nodetests-only window let pytests-red PRs read `UNSTABLE` and merge.
   🔴 `enforce_admins: true` there means a wedged
   Tekton blocks everyone with no override; the escape hatch is
   `gh api -X DELETE /repos/innovation-upstream/devrc/branches/main/protection/required_status_checks`.
10. 🔴 **RENAMING A REPO SILENTLY KILLS ITS TRIGGER.** Every trigger CEL-matches
    `body.repository.full_name`, so a renamed repo's webhooks stop matching and post-merge CI
    just… stops — no error, no red check, and a repo with no pushes looks identical. Measured
    2026-08-26 renaming `ZacxDev/auditloop` → `auditloop-private`. Grep the triggers dir for the
    OLD name and update the literal **and** the comments. 🔴 **If the freed name is re-used by
    another repo, "restoring" the old literal points the trigger at THAT repo** — which has no
    webhook into this EventListener, so it re-breaks CI the same silent way. Say so in a comment
    at the filter.
    ⚠ **`ci.zacx.dev/repo` labels are derived from the repo name, so they change at the rename
    too**: runs before and after carry DIFFERENT labels (measured `{auditloop: 16,
    auditloop-private: 1}`). A `kubectl get pipelinerun -l ci.zacx.dev/repo=<old>` therefore
    returns a confident **wrong zero** — which reads exactly like "the trigger is broken". The
    EventListener log is what discriminates: `kubectl -n tekton-ci logs -l eventlistener --since=15m`
    shows `"/trigger":"<name>"` and `ResolvedParams` for an event that DID match.

6. **A gate pod rejected at ADMISSION posts a FAILED TEST, so it reads as a bad change.**
   Two ways this has bitten, both on `devrc-ci`, both 2026-08-29/30:
   **(a) PodSecurity.** `tekton-ci` carried no `pod-security.kubernetes.io/*` label, so it
   inherited the cluster default `baseline`, which forbids **hostPath** outright. When
   `6bec075e` swapped the gate's RWO PVC for a per-node hostPath nix cache, every gate pod
   died with `pods "devrc-ci-<id>-gate-pod" is forbidden: violates PodSecurity
   "baseline:latest": hostPath volumes (volume "nix-cache")`. Admission failure does **not**
   retry into success — the pod is never created, the `gate` TaskRun reads
   `PodAdmissionFailed` while `notify`/`report` succeed, and both required checks post
   `COULD NOT RUN`, so with `enforce_admins: true` **nothing could merge**. Fixed by
   `pod-security.kubernetes.io/enforce: privileged` on the namespace (`686d6ff0`).
   ⚠ That is a **broader** grant than the 7 infra namespaces already carrying it — this one
   runs webhook-triggered CI. The narrow fix is a baseline-compatible cache.
   🔴 **THAT hostPath IS GONE — `6bec075e` was REVERTED by `7839ef54` 2h19m later
   (2026-08-29T22:09Z → 2026-08-30T00:29Z), so gotcha #3's node-pinning and
   §"Adding a new pipeline / new repo" step 4 describe the LIVE gate, not history.**
   Re-measured 2026-08-30 on `devrc-ci-vchxk-gate-pod`:
   `nodeSelector kubernetes.io/hostname=talos-xr6-r7p`, volume `nix-cache` →
   `persistentVolumeClaim: nix-store-cache` (30Gi, Bound, selected-node `talos-xr6-r7p`);
   `gitops-validate` on `talos-uvh-gtj` with its own `nix-store-cache-2`. A read taken
   *during* that window is a correct measurement of a state that no longer exists — check the
   volume live before quoting either shape. ⚠ **And the window you could have OBSERVED it in
   is shorter than the git interval: ~1h41m, not 2h19m** — for the first 39 minutes
   (22:09Z→22:48Z) admission rejected every gate pod, so there was nothing to read.
   🔴 **The revert was NOT the admission failure above — it is a SECOND, INDEPENDENT failure
   of the same volume, and it is the one to solve first if the unpin is ever retried.** Nothing
   in the sandbox could take the nix DB lock:
   `error: opening lock file "/nix/var/nix/db/big-lock": Permission denied` — **75
   occurrences across 42 tests, on EVERY devrc PR**, byte-identical on two different branches
   (`devrc-ci-csfzb`/#1057, `devrc-ci-kdcmr`/#1059), against a pre-change run
   (`devrc-ci-q4d5m`) of `failed=0` over 18,557 passed. *(Those figures are quoted from
   `7839ef54`; the runs are pruned, so they are not re-derivable — the mechanism below is.)*
   🔴 **`nix-store-cache` IS ITSELF A hostPath, so "PVC vs hostPath" is not the difference it
   reads as** — its PV is `hostPath {path: /var/lib/mnt/disk-1/pvc-aef79024…_tekton-ci_nix-store-cache,
   type: DirectoryOrCreate}`, same disk and same type as `6bec075e`'s. **Three differences are
   measured, which one causes the lock failure is UNKNOWN, and `7839ef54`'s stated cause is
   UNPROVEN rather than refuted** — the candidates, the invalid control to avoid, and the perf
   baseline's `requests.cpu` caveat are in
   `~/workspace/devrc/claude/skills/tekton/reference/pipelines.md` → "Retrying the devrc-ci
   unpin" — which records one candidate as **REFUTED**, so read it before proposing an
   experiment. 🔴 Retry only
   with the ownership question answered FIRST and **proven on a scratch pipeline**: `7839ef54`
   chose a full revert over a permission patch because *"the place to find the third failure mode
   is not production"*, and with `enforce_admins: true` on devrc `main` a wrong guess blocks
   **everyone's** merges. `686d6ff0`'s `privileged` exemption buys nothing while the PVC is back
   — measured 2026-08-30 over **every live `tekton-ci` pod**: **zero** use hostPath,
   hostNetwork/PID/IPC, privileged, added caps, hostPort or sysctls. ⚠ Re-run that sweep rather
   than quoting a COUNT: the pod total is ~95% terminal and drifted 358 → 457 inside one
   session, and even "distinct pipelines" is population-dependent (live pod labels **11**,
   PipelineRuns 12, Pipeline CRs 13) — so say which you counted. 🔴 Its own
   in-file comment is now STALE and says the opposite —
   `<homelab-infra>/clusters/homelab/apps/tekton-pipelines/triggers/namespace.yaml` still reads
   *"REQUIRED by the gate's hostPath nix cache"* over a precondition already satisfied.
   **(b) `sandbox = false` in the CI pod.** The `nix build .#checks…` tier is only hermetic
   where nix's sandbox is ON. With it off, the *same derivation hash* produced `failed=1` on
   the dev host and `failed=43` in CI — identical inputs, different output, i.e. impure.
   🔴 **Before debugging any diff against a red `devrc-ci` run, check both:**
   `kubectl get taskrun <run>-gate -n tekton-ci -o jsonpath='{.status.conditions[*].message}'`
   and `kubectl exec -n tekton-ci <gate-pod> -c step-pytests -- sh -c 'nix config show | grep "^sandbox "'`.
   A red check whose cause is either of these is a **broken gate, not a bad change**.

## What / where

- **Tekton Operator v0.80.0** → Pipelines **1.12.2** / Triggers **0.36.0** / Dashboard
  **0.68.0**. **Chains + Results OFF.** Pruner keep-**100**, daily. Dashboard **read-only**.
  🔴 **`prune-per-resource: true`, so keep-100 is PER Pipeline/Task — NOT per namespace.**
  With 6 pipelines the designed steady state is **~600 PipelineRuns**, and a raw namespace
  count in the hundreds is the pruner WORKING, not falling behind. This was written up once
  as a backlog item ("451 PipelineRuns against a keep:100 pruner") and re-investigated a day
  later; both times the count was inside its envelope. **The positive control is a pipeline
  sitting at EXACTLY 100** — `remix-ux-audit` did, on both measurements, which is what proves
  the cron runs and hits its target. Measure per-pipeline before concluding anything:
  `kubectl -n tekton-ci get pipelineruns -o jsonpath='{range .items[*]}{.spec.pipelineRef.name}{"\n"}{end}' | sort | uniq -c | sort -rn`
  🔴 **And terminal pods are NOT the pressure.** 2026-08-23: `talos-xr6-r7p` held **676**
  Completed/Error pods against **76** non-terminated. Terminal pods hold no CPU and no memory
  (`Allocated resources` counts non-terminated only) and do not count toward `max-pods`
  (110 → 34 free). Deleting them cuts apiserver/etcd load and makes `kubectl get pods`
  usable — it relieves **zero** scheduling pressure. Do not reach for it as remediation.
- Namespaces: **`tekton-pipelines`** (control plane) + **`tekton-ci`** (CI workloads,
  EventListener, PipelineRuns).
- GitOps via **Flux**, repo **`ZacxDev/homelab-infra`** branch **`trunk`**, under
  `clusters/homelab/apps/tekton-pipelines/`.
- Kubeconfig: `~/workspace/homelab-talos/homelab-kubeconfig` (handle: `$KC_HOMELAB`).
  🔴 **`homelab-talos`, NOT `homelab-infra`** — the line above names the GitOps *repo*
  (`homelab-infra`), and this file used to reuse that name for the *kubeconfig*, giving a
  path that does not exist on disk. `kubectl` then falls back to `localhost:8080` and every
  read dies with `connection refused` — an error that names a port appearing nowhere in the
  skill, so it reads as a cluster outage rather than a bad path. Measured 2026-08-23 while
  debugging the devrc gate. Six sibling skills (`signal`, `activity`, `mailbox`, `sglang`,
  `standup`) all spell `homelab-talos`; this was the only file that did not.

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

`clawgate-ci` is the repo's **real pre-merge gate**: GitHub Actions is billing-blocked **for
`homelab-infra` and the other repos in THIS skill's scope**, so **red Actions checks on
`homelab-infra` are noise, not signal** — don't debug them and don't read them as a gate.
🔴 **That is a per-repo fact, NOT a fleet-wide one — do not carry it to a repo this skill does
not cover.** Carve-out, measured 2026-08-25: in **`vetrllc/vetr-app` GitHub Actions is LIVE and
is the ONLY gate** (`tests` + `e2e (hermetic)`, real successes on `main`; `gh run list`), so a
red check there is **real signal — debug it**. Sibling `vetrllc/vetr-api` is the third case:
all three workflows are `disabled_manually` (`gh workflow list --all`), so it has **no Actions
gate at all** — checks are ABSENT, not red, and nothing runs its Pest suite automatically.
Neither vetr repo is wired to Tekton. **Check the repo before applying the noise rule:
`gh workflow list --all` + `gh run list`.** Four known-open 🟡 issues (branch-creation over-match,
unpinned PVC, no concurrency control, `error`-vs-`fail` on the CSS path only) are in the
reference file. **Seven** triggers share `el-github-listener`: `naida-push-main`,
`remix-push-trunk`, `gitops-validate-pr`, `gitops-validate-push-trunk`, `clawgate-ci-push`,
`auditloop-push-main`, `devrc-ci-pr`. 🔴 **`devrc-ci-pr` is the only one whose check actually
BLOCKS a merge** (gotcha #9) — the rest are detectors, because their repos cannot configure a
required check at all. Read the count off the CR, never off this line:
`kubectl -n tekton-ci get eventlistener github-listener -o jsonpath='{.spec.triggers[*].name}'`
— this file said "six" for the whole period `devrc-ci-pr` was live and gating.

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
