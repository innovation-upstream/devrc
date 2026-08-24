# Handoff: devrc-ci-gate — 2026-08-21

## Run this first — the index, one read-only command
```bash
python3 ~/workspace/devrc/scripts/lib/subsystem_recall.py --repo ~/workspace/devrc
```
Terse pointers this doc does not carry, curated by past sessions and outliving it.
🔴 RECALL, NOT LIVE OBSERVATION — every line is a pointer to VERIFY, never a current
reading, and it may describe a gotcha already fixed. `scope-absent`/`scope-empty` means
nothing is recorded yet: ordinary, not an error, and not a clean bill of health.
Non-blocking: if it exits non-zero, print the stderr line and carry on.

⚠ **No `clawgate-task:` field on purpose.** `clawgate_handoff.sh resolve` returned
`NOTHING RESOLVED — 0 tasks for this session`. An unknown session id also answers `200`
with an empty array, so that result cannot distinguish "touched no task" from "wrong id".
No field was written and none should be invented.

## Goal
devrc had **no automated pre-merge gate at all** (`<!-- merge-gate: none -->`): no CI, no
branch protection, the gate run by hand only when someone chose to. Give it a real one via
the homelab Tekton platform. Secondary: land the subsystem-store hosted-vs-local decision.

## State now

🔴 **THE GATE IS REAL AND BLOCKING as of 2026-08-23.** devrc went from
`<!-- merge-gate: none -->` and a trigger that had **never fired once** (0 of 370
pipelineruns) to a required, enforced pre-merge check.

- **`required_status_checks.contexts = ["tekton/devrc-nodetests"]`** on `main`,
  `enforce_admins: true`, `strict: false`.
- **Verified behaviourally, not from the setting:** nodetests `ERROR`/`PENDING` ⇒
  `mergeStateStatus=BLOCKED`; `SUCCESS` ⇒ `CLEAN`; pytests red + nodetests green ⇒
  `UNSTABLE` (mergeable). pytests **reports**, nodetests **gates**.
- **Proven end-to-end on a live PR:** `#748` went `BLOCKED` → gate green → `CLEAN` →
  merged (`27fa67f9`). First merge in this repo held by anything but a human choosing
  to wait.

**Merged this session** — devrc `#706` (df8d0319), `#732` (5a2a7b21), `#735`
(2cd378a8), `#748` (27fa67f9); homelab-infra `#378` (0740ad10), `#383` (b340bd26),
`#385` (15092256). Closed deliberately: `#380` (node choice refuted by audit),
`#713` (superseded by `#708`).

**Deploy/verify status, stated separately:** all homelab-infra changes reconciled via
`flux reconcile kustomization tekton-triggers` and verified **at the consumer** —
live `Pipeline devrc-ci-pipeline` shows `notify timeout=2m0s`, `gate timeout=45m0s`;
`PriorityClass ci-bulk` exists at `-10000/Never`; a real gate pod carries
`prio=-10000 class=ci-bulk`.

## Open investigations — live diagnosis state

### ~~SUPERSEDED 2026-08-22 — RESOLVED~~ The devrc Tekton gate never fires (kept ONLY for its ruled-out list)
🔴 **Read the resolution below first — this block is history, not live state.** The gate now
fires; `devrc-ci-djm7n` ran for the very PR that recorded this. Nothing in this block is a
current reading. It is kept because its *ruled-out* entries are still load-bearing (they stop
the next reader re-walking delivery, HMAC and interceptor theories) and because its wrong
first diagnosis is worth not repeating.
- **Symptom + repro, AS IT WAS:** every devrc PR from the trigger going live (23:32Z) until
  2026-08-22 produced no PipelineRun. `kubectl -n tekton-ci get pipelineruns | grep devrc`
  → empty. **No longer reproducible** — that command is now the success check, not the
  symptom.
- **Observed (values):** the CEL live on the CR is
  ```
  header.match('X-GitHub-Event','pull_request') && body.repository.full_name == 'innovation-upstream/devrc'
  && body.pull_request.base.ref == 'main' && body.action in ['opened','reopened','synchronize','ready_for_review']
  && body.pull_request.draft == false && has(body.pull_request.head.repo) && body.pull_request.head.repo != null
  && body.pull_request.head.repo.full_name == 'innovation-upstream/devrc'
  && body.pull_request.author_association in ['OWNER','MEMBER','COLLABORATOR']
  ```
  The **delivered webhook payloads** (GitHub `/app/hook/deliveries`, 6 consecutive devrc
  `pull_request` events) all read `assoc=CONTRIBUTOR user=ZacxDev`. `CONTRIBUTOR` is not in
  the allowed set ⇒ filtered. Deliveries are `status=OK(202)`; the EL response body names
  `eventListener:github-listener, namespace:tekton-ci`, so it reaches the right listener and
  is dropped silently.
- 🔴 **The trap that cost the most time: the REST API and the webhook payload DISAGREE.**
  `gh api repos/innovation-upstream/devrc/pulls/697` reports `author_association: MEMBER`.
  The payload says `CONTRIBUTOR`. **The filter only ever sees the payload**, so validating a
  CEL against API data proves nothing. The agent's cel-go harness passed 14 cases for this
  reason.
- **Ruled out — delivery.** GitHub delivered every event, `OK(202)`, repo id `506731826`
  (= devrc). Not a webhook, DNS, HMAC or App-installation problem.
- **Ruled out — HMAC / interceptor.** All 7 triggers share `github-app/webhookSecret`, and
  `gitops-validate-pr` proves the `pull_request` interceptor path works.
- 🔴 **Ruled out — my own first diagnosis, which was WRONG.** I concluded "devrc events are
  not arriving" from the EventListener log showing 0 devrc mentions. **The EL only logs when
  a trigger MATCHES** (every line is `Generating resource`), so its silence cannot
  distinguish *didn't arrive* from *arrived and matched nothing*. The delivery log is the
  upstream signal that discriminates.
- **Leading hypothesis:** none needed — the cause is measured. What is *open* is the
  DECISION, below.
- ~~**Next probe**~~ — the scratchpad `payload.sh` it named is **gone** (a session-scoped temp
  path), so it was never runnable "verbatim" by a later reader. Superseded by the auth/unauth
  control in the resolution below, which needs no App key and no scratch file. The remaining
  half is now just the success check:
  ```bash
  kubectl -n tekton-ci get pipelineruns | grep devrc   # non-empty since 2026-08-22
  ```

### ✅ DECIDED 2026-08-22 — the author gate was never wrong; the MEMBERSHIP was invisible
🔴 **The framing above was a false dilemma, and both of its options were wrong fixes.**
`author_association` is computed **with viewer context**. ZacxDev's `innovation-upstream`
membership was **PRIVATE**, so a payload — which carries no viewer — could not see it and
fell back to `CONTRIBUTOR`. The CEL was stating the correct policy all along.

**The discriminating control (cheap, first-hand, repeatable — no App JWT needed):** read the
SAME PR's `author_association` with and without auth. Different answers ⇒ visibility, not
policy.
```bash
gh api /repos/innovation-upstream/devrc/pulls/703 | jq -r .author_association     # was: MEMBER
curl -sS https://api.github.com/repos/innovation-upstream/devrc/pulls/703 \
  | jq -r .author_association                                                     # was: CONTRIBUTOR
```
This replaces the `/app/hook/deliveries` route entirely: the unauthenticated read is the
same viewer-less computation the webhook gets, and needs no App private key.

🔴 **Those annotated outputs are HISTORICAL (before 2026-08-22) and will NOT reproduce today.**
Post-fix both lines return `MEMBER` — re-measured, twice. A reader who runs the block now gets
a matching pair and would wrongly conclude the mechanism claim was bogus. **The split
reappears only if the membership is re-privatised**, which is exactly the failure mode flagged
at the end of this section — so this block is the right diagnostic to reach for *then*, and
proves nothing while the gate is healthy.

**Three independent confirmations of the mechanism:**
1. Auth vs unauth on the same PR at the same instant: `MEMBER` vs `CONTRIBUTOR`.
2. devrc has **zero direct collaborators** (`?affiliation=direct` → empty), so there was no
   `COLLABORATOR` fallback either — access came solely from the private org-admin role.
3. 🔴 **The one OTHER PR trigger discriminates it.** `gitops-validate-pr` contains the
   *identical* `['OWNER','MEMBER','COLLABORATOR']` sub-expression and works — because it
   targets `ZacxDev/homelab-infra`, a **personal** repo, where the association is `OWNER`, and
   `OWNER` is viewer-independent. devrc is **org-owned**, so it is the only trigger this can
   reach. Two precisions that matter if you diff the two filters:
   - The triggers are **not** otherwise identical — `gitops-validate-pr` adds
     `|| (user.login == 'renovate[bot]' && user.type == 'Bot')`. That escape hatch is **not**
     what carries it: homelab-infra PRs #368–#377 are all authored by `ZacxDev`, so those runs
     genuinely passed through the association clause.
   - Only **2 of 7** triggers carry an `author_association` clause at all; the other five are
     `push` triggers, for which the field does not apply. So the shape is "the rule holds on
     the one personal-repo PR trigger and failed on the one org-repo PR trigger" — not
     "six versus one".

**Fix applied — publicize the membership, CEL UNCHANGED:**
```bash
gh api -X PUT /orgs/innovation-upstream/public_members/ZacxDev     # reverse: -X DELETE
```
Verified by re-running the control: PRs 701/702/703 all flipped `CONTRIBUTOR` → `MEMBER`
unauthenticated, with **no change to the CEL, the trigger, or homelab-infra**.

🔴 **Phrase the effect precisely — "nothing was widened" is true of the CEL and overstated as
a whole.** The *policy* was not widened: not one token of the filter changed. But the set of
principals who can actually fire a code-executing pipeline went from **∅ to {ZacxDev}**. That
is the entire intended effect, and it is still a change in effective reach. Say "the policy
was not widened; the set it admits went from empty to its intended member."

⚠ **The cost this fix carries, which must not be left implicit:** publicizing org membership
is a **permanent, unauthenticated, public disclosure** linking a personal GitHub identity to a
client-bearing org, across every public repo the org owns. That cuts against this repo's own
posture — the table above records `#622` *redacting a real client subdomain from this same
public repo*. It was accepted knowingly; it is not free, and it is reversible
(`gh api -X DELETE /orgs/innovation-upstream/public_members/ZacxDev`).

🔴 **A better option existed and was NOT considered at decision time** (found by the
post-merge audit, recorded so it is not lost): keep the association clause **and add a named
exception as a disjunct** — precisely the shape `gitops-validate-pr` already uses for
`renovate[bot]`:
```
(body.pull_request.author_association in ['OWNER','MEMBER','COLLABORATOR']
 || body.pull_request.user.login == 'ZacxDev')
```
This has none of the failure modes listed under "Rejected" below — the association clause
stays load-bearing, so a genuine second org member still gets a run — **and** it needs no
public disclosure. The precedent was sitting in the very CEL cited as confirmation #3. If the
disclosure is ever judged too expensive, this is the swap to make.
A third, unmeasured candidate: adding `ZacxDev` as a **direct collaborator** would yield
`COLLABORATOR`, already in the allowed set, also without org-wide disclosure — untested,
because verifying it requires mutating collaborators.

**Rejected, and why — keep these written down, they will be re-proposed:**
- *Add `'CONTRIBUTOR'`.* Safe only because `head.repo.full_name` pins the branch in-repo
  (which needs write access). That makes ONE clause solely load-bearing and the author clause
  decorative — and the day fork PRs are supported, it becomes a live hole silently. It also
  encodes a false statement about who is allowed.
- *Pin `body.pull_request.user.login`.* Hardcodes a personal identity into GitOps, and any
  second author or bot then gets **no run, no gate, and no signal** — the same unprotected
  merge this whole effort exists to remove, just quieter.

⚠ **Consequence to remember:** the gate now depends on a GitHub **org-membership visibility
setting**, which lives nowhere in this repo and no test can see. If the gate goes inert
again, run the auth-vs-unauth control FIRST — before touching any CEL.

### ~~The `devrc-pytests` leg must NOT be made required yet~~ — STALE as of the first real run
🔴 **This section's premise was overtaken by evidence; do not act on it without re-measuring.**
It read: `devrc-pytests` has 1 known environment failure — the nix sandbox does not exist in
the pod, and all three levers (`privileged`, `CAP_SYS_ADMIN`, `seccomp Unconfined`) are
rejected by PodSecurity `baseline:latest`, each verified by an actually-rejected PipelineRun;
requiring it would be the permanently-red gate. Options documented in homelab-infra `#370`.

**What the first real run measured instead (2026-08-22, `devrc-ci-djm7n`):** `seed-nix` exited
**0** and **14,686 pytests passed in-pod**. Whatever closed the sandbox gap, it is closed. The
single failure was a stale `TARGET_FLOORS` entry, not the environment. The *conclusion* may
still be right — one run is not a stability claim — but the *reason* recorded here is no
longer true, and a permanently-red gate is no longer the expected outcome. See step 2.

### ✅ RESOLVED — a timed-out run posted NOTHING and left checks on `pending` forever
- **Was:** a PipelineRun exceeding `timeouts.tasks` never ran its `finally` report task,
  so no status was posted and the PR's checks sat on `pending` indefinitely. No re-run
  clears it; only a fresh push. This was the blocker on requiring any check.
- **Evidence:** `devrc-ci-nnt6f` / `devrc-ci-9p6mf` — `childReferences [notify, gate]`,
  no report, `completionTime` exactly `startTime + 50m`; checks still `pending` 13h
  later. homelab-infra `#378`'s own gitops run left `f2479e73` with **zero statuses**.
- 🔴 **Root cause was NOT a misconfiguration — `finally: 10m` WAS correctly reserved.**
  The discriminating control: `gitops-validate` has a `report` finally task too and
  **all 21** of its retained timeouts also lack it. Platform-wide Tekton v1.12.0
  behaviour, not a devrc bug.
- **Three-way probe (minimal PipelineRuns, `sleep 300` + a `finally` echoing a marker):**
  | config | reason | children | finally |
  |---|---|---|---|
  | `timeouts.tasks: 40s` | `PipelineRunTimeout` | `[slow]` | **never ran** |
  | `timeouts.pipeline: 40s` | `PipelineRunTimeout` | `[slow]` | **never ran** |
  | task-level `timeout: 40s` | `Failed` | `[slow,reporter]` | **RAN** |
  A pipeline-level budget expiring TERMINATES the run and the reserved
  `timeouts.finally` is not honoured on that path; the reporter TaskRun is never even
  CREATED. A task-level timeout is an ordinary failure, which is what `finally` is for.
- **Fixed:** `#385` — `timeout: "45m"` on `gate`, plus `timeout: "2m"` on `notify` so the
  50m budget is unreachable by *arithmetic* rather than by assertion (the deadlines cross
  whenever notify exceeds 5m, and notify previously inherited the cluster's 1h default).
- ✅ **CLOSED for all six.** homelab-infra **`#386`** landed the other five on 2026-08-23 —
  merged, Flux-reconciled, and verified on the **LIVE Pipeline objects** (a run executes the
  DEPLOYED Pipeline, so the file landing is not the claim that matters):
  `clawgate-ci 25m · gitops-validate 20m · auditloop-ci 40m · remix 40m · naida 40m`.
  Each task timeout is set to EXACTLY its previous `tasks` budget, with `tasks` +5m and
  `pipeline` +10m — the effective deadline is deliberately **unmoved**, only the layer
  enforcing it. 🔴 Deliberately NOT tightened the way `#385` tightened devrc 50m→45m:
  `gitops-validate` has completed runs at 18m06s under a 20m ceiling, so the same move would
  have killed runs that pass today.
- 🔴 **`#386` also closed a SECOND, independent no-verdict path that this section never
  named: `timeouts.finally` killing the REPORTER.** `remix-ux-audit-f6vks` lost its verdict
  that way on a run only **9m48s** long against a 45m budget, and `clawgate-ci-7smtg`
  survived by **15s**. `finally` is now 10m everywhere.
  ⚠ **Do NOT "fix" that by bounding the reporter's `curl` calls** — that was the first
  diagnosis, it was wrong, and an audit caught it. Across ~490 report TaskRuns the
  `report-status` step has **never exceeded 28s**, while its pod-start latency reaches
  **282s**. The reporter is slow to SCHEDULE, never slow to run. The curls genuinely are
  unbounded (2 per reporter, 3 for devrc) — just not what costs verdicts.
- 🔴 **The arithmetic is a RELATIONSHIP and nothing enforces it:**
  `Σ(attempts × timeout) + startOffset < tasks`. Adding `retries:` restarts the clock per
  attempt and re-opens the defect **with every timeout value looking untouched**. A
  server-side dry-run accepts `timeout: 99m` under `tasks: 30m` without complaint. Tekton
  *does* validate `tasks + finally ≤ pipeline` — but **not on a TriggerTemplate's
  `resourcetemplates`**, which is exactly where these budgets live, so an invalid budget
  there applies cleanly and fails only at EventListener time ⇒ no status posted, i.e. this
  same bug by another door. To validate one, lift the `timeouts` block into a standalone
  PipelineRun and `--dry-run=server` **that**.
- ⚠ **What `#386` does NOT fix, and this is the bigger number.** `gitops-validate` loses
  **21 of 113 runs (18.6%)** — ten times devrc's 2 — because its pods wait **p50 1m07s,
  p90 11m09s, max 19m11s** against a **20m** ceiling. Five of the 21 had actually started
  running; one got through **10 of 13 steps**. `#386` only makes such a run post a legible
  `error` instead of silence. **The lever is scheduling pressure (the `#378` family), not
  the timeout** — note `clawgate-ci`, the one pipeline with **no `nodeSelector`**, has a
  pod-start max of **18s** rather than 19 minutes.
  🔴 This reconciles with the skill's "a burst queued and recovered, zero checks eaten"
  counter-datapoint rather than contradicting it: a burst **delays** the pod, and whether the
  delay costs a verdict is decided by the pipeline's own headroom. devrc at 50m drains and
  survives; `gitops-validate`, tightest budget and worst latency, is the one that loses.
- ⚠ **Three audit rounds were needed and each found a real defect — in the fix's own
  COMMENTS, not its values.** Round 1: two wrong diagnoses baked into config, one of which
  had reversed the fix. Round 2: ten comments misstating the change itself, a
  self-contradicting max, and a deleted caveat that still applied. Round 3: a corrected
  figure contradicted a sibling file **in the same PR**. Budget for this shape.

## Next steps (ranked)

1. ✅ **DONE — homelab-infra `#386`, merged + deployed + verified live.** See the resolved
   section above for what it covers and, more importantly, what it does not.
2. ⚠️ **OVERTAKEN — `tekton/devrc-pytests` IS required now, and this doc advised against
   it.** Measured 2026-08-24:
   `required_status_checks.contexts = ["tekton/devrc-nodetests","tekton/devrc-pytests"]`
   with `enforce_admins: true`. Someone made it required anyway; recorded here because the
   advice below was not withdrawn, it was simply overrun, and the reasoning still stands as
   the risk that was accepted:
   > it first reached `BOTH TIERS PASS` (pytests 15150/0, nodetests 1149/0) on
   > `devrc-ci-vl88r` at 2026-08-23T04:00Z — but it was red for **four independent reasons**
   > on day one. That wants a stretch of consecutive greens, not one observation.

   🔴 **The consequence is now live and worth knowing before you need it:** with both tiers
   required and `enforce_admins: true`, a wedged Tekton blocks every merge with no admin
   override. The escape hatch is
   `gh api -X DELETE /repos/innovation-upstream/devrc/branches/main/protection/required_status_checks`.
   ✅ The specific unsatisfiable case this doc flagged as the blocker on requiring anything —
   a timed-out run posting NOTHING and sitting `pending` forever — **is the thing `#385`/`#386`
   fixed**, so requiring both tiers is far safer now than when this was written. That is
   probably why it happened.
3. **Revisit the 45m gate timeout.** Headroom over the observed max is **3m32s (8.5%)**,
   and every measured run had a WARM nix cache — the budget's own stated worst case (a
   cold run after a nixpkgs bump) is absent from the evidence.
4. **Right-size the other pipelines' requests.** `gitops-validate` alone asks
   **4.65 CPU / 4.688Gi across 8 SEQUENTIAL steps** — the same inflation `#378` fixed
   for devrc, and Tekton sums them into the pod request.
5. **Watch for a `Preempted` event.** `ci-bulk` is live but has **never fired**, so its
   mechanism is verified as *configured*, not as *working*:
   `kubectl -n tekton-ci get events --field-selector reason=Preempted`.
6. **CARRIED FORWARD, still open — 1 fixture-author commit.** `zach/requires-env-skip-pins`
   still exists on origin with a commit authored `T <t@example.com>`; one
   `git commit --amend --author=…` + force-push, **by its owner**. (The other two branches
   named in the previous revision are gone from origin — re-measure before acting.) 26 more
   are on the preserved incident branch and are *supposed* to carry it.
7. **CARRIED FORWARD, still open — `check-tekton-app-install.sh`'s `CDPATH` bug**
   (homelab-infra): `HERE="$(cd -- … && pwd)"` returns TWO lines when `CDPATH` is set,
   because bash's `cd` echoes the target. Use `cd -- "$d" >/dev/null && pwd`.

## Gotchas / decisions / dead-ends

- 🔴 **`isolation: "worktree"` puts every file-modifying subagent under
  `<base>/.claude/worktrees/`, which SHARES the base clone's git dir.** A `git config`
  without `--global` there writes the OPERATOR'S `.git/config`. devrc's test tier mutates
  real repos; this corrupted the clone (`core.bare=true`, HEAD detached, identity replaced
  by a fixture author). **113 worktrees share that common dir, 64 under `.claude/worktrees/`.**
  Nobody chooses this — it is the dispatch default. The fix has to reach the default, not
  operator discipline.
- ✅ **The safe gating recipe, proven behaviourally:**
  ```bash
  git clone https://github.com/innovation-upstream/devrc.git <scratch>/gate   # fresh, not from the base clone
  git -C <scratch>/gate remote remove origin
  git -C <scratch>/gate rev-parse --path-format=absolute --git-common-dir     # MUST be inside <scratch>
  ```
  🔴 **The `--path-format=absolute` is load-bearing** — the relative form returns `.git`,
  which a checker joins to its own cwd, resolving to the base clone and reporting SHARED for
  a contained sandbox. 🔴 **The real proof is behavioural, not the path check:** write
  `git config --local user.email` in the sandbox and confirm the operator's clone is
  unchanged. That is the exact write that corrupts a worktree.
- 🔴 **Your git uses diff3 conflict style — there is a FOURTH marker, `|||||||`,** and the
  region between it and `=======` is the BASE, which must be **discarded**. Stripping only
  the three familiar markers keeps the base content and duplicates it. Caught only because
  the leftover `|||||||` was a syntax error.
- 🔴 **`gh pr view --json mergeable` trails reality.** It reported `MERGEABLE/CLEAN` while a
  real add/add conflict existed, then `UNKNOWN` after a push. A local merge trial is the
  authority on a fast-moving branch.
- **Gate the MERGED tree, not the branch.** Three of four merged-tree gates went red for
  reasons unrelated to the PR gated — an inherited `main` breakage, a shared-state harness
  bug, a byte-ceiling red. Twice the branch was green and `main` was the broken side.
- 🔴 **A sweep that greps for a pattern is itself a process containing that pattern; a sweep
  that tests kinship runs on a box where everything is kin** (one tmux parents every
  session, so "shares an ancestor with me" is vacuous). Both return confident wrong answers
  inside checks built to be careful. The cheap discriminators: *does the hit survive the
  next command*, and *require the OWNING boundary, not any shared one*.
- **Concurrency is the dominant cost.** `main` moved 11+ times during this session; two of
  my PRs were superseded mid-flight by better fixes from other sessions. Check whether
  someone is already on a defect before dispatching an agent at it.

- 📌 **CARRIED FORWARD from the previous revision's `Next steps`, which this update replaces.**
  These are durable *findings*, not pending actions, and would otherwise have been deleted —
  the merge tool flagged the drop, which is the only reason they are here:
  - ✅ **`origin/trunk` was TEST-FIXTURE DEBRIS on a PUBLIC repo — DELETED 2026-08-22.** Tip
    `ff6b2ca3`, author **`t <t@t>`**, message `seed`, adding one file `seed.py`; pushed
    2026-08-21, the date of the test-tier incidents (#673/#683/#689). Two genuine commits sat
    underneath (`0bf1b324`, `276d56eb`). 🔴 **`merge-base --is-ancestor` said NO for both
    because they landed by SQUASH** — checked by CONTENT instead: all 9 symbols they introduce
    are in `main`. Recovery sha `ff6b2ca39b5d228aa77243dee2f930a2cfe025fd`, also held locally
    as `refs/recovery/trunk-ff6b2ca3`.
  - ✅ **The SessionStart hook synced `CLAUDE.md` from the WRONG REF — a stale `origin/HEAD`,
    not a hook bug.** `~/.claude/local-hooks/base-clone-staleness.sh` hardcodes no ref; it
    prefers `@{upstream}` and falls back to `refs/remotes/origin/HEAD`. Two conditions had to
    coincide: a **detached HEAD** in the base clone (no upstream) and `origin/HEAD` **stale at
    `origin/trunk`** — the two refs had **DIVERGED: 14 commits in `main` not in `trunk`, 3 the
    other way** (measured 2026-08-22). It landed **staged**, reverting merged #702. Fix:
    `git remote set-head origin -a`. ⚠ Per-clone and per-host — the laptop and any fresh clone
    can still inherit it; `git symbolic-ref --short refs/remotes/origin/HEAD` is the check.
  - ✅ **The gate CONGESTED on day one** — 8 runs queued, 5 `Pending` 15+ min on
    `Insufficient memory` while the node idled at 26% mem / 20% CPU. Every devrc run is pinned
    to ONE node (shared `nix-cache` RWO PVC + `volumeClaimTemplate` → affinity assistant).
    Measured peak **823Mi / 1043m** against a 4Gi/2-CPU request; Tekton sums SEQUENTIAL steps
    into the pod request. Fixed in homelab-infra **#378** (requests only, every limit
    untouched): per-run 6Gi→3Gi, 3→1.5 CPU.

- 🔴 **FOUR times this session a ZERO of mine came from a BROKEN INSTRUMENT, not an
  absence** — and every one looked like evidence:
  - `grep` for an old assertion string matched **my own docstring quoting it**, reading
    as "the fix didn't apply". Fix: grep for a *live* `assert`, not the sentence.
  - `kubectl get $p` with the resource type stripped → `FINALLY-RAN=0` from a command
    that errored. The `error: the server doesn't have a resource type` line beside it
    was the tell.
  - `grep 'timeout: "45m"'` found nothing because **kustomize strips the quotes**.
  - `rc=$?` after a pipe read `tail`'s status, not the command's (twice — including the
    clawgate resolve in this very handoff).
  **The positive control separated real zeros from broken ones every time.** Before
  quoting a zero, prove the pattern CAN match.
- 🔴 **`core.hooksPath` volatility CONFIRMED LIVE, not just recorded.** It read `(unset)`
  before every push this session — checked each time — and was then found set
  **repo-locally** to `/home/zach/workspace/devrc/githooks` on a later push, with
  `global` still unset. Nothing here set it. Checking once per session would have been
  wrong. The push was verified intact afterwards (local == remote HEAD, 0 `autocommit`
  fixture commits) — no #322 recurrence.
- 🔴 **`scripts/kustomize-validate.sh` does NOT cover
  `clusters/homelab/apps/tekton-pipelines/triggers`** — it defaults to
  `ROOTS=(clusters/*/flux-system)`, and all 23 roots it builds are under those. **So
  `gitops-validate` does not validate that directory either.** I cited its PASS as
  evidence on three PRs before catching it. Correct instrument for that path:
  `kustomize build <root>` + `kubeconform -strict -kubernetes-version 1.35.2`, **plus a
  baseline run on `origin/trunk`** to separate pre-existing invalids (4 SOPS Secrets)
  from new ones.
- 🔴 **TWICE my own FIX was the next defect, caught only by an adversarial audit.**
  (a) `retries: 1` on the gate task looked obviously right; Tekton retries **any**
  non-cancelled failure, and **27 of 27** completed gate TaskRuns that day were
  `StepFailed` — real verdicts — so every run would have executed twice (~1.76× node
  occupancy) *and* some would have landed in the no-status-at-all state. Reverted, and
  recorded as a 🔴 DO-NOT at the task because it will be re-proposed.
  (b) The node choice in `#380` was made on **resource requests**, a metric that
  **inverts** the real ranking: the target node was the most *physically* loaded in the
  cluster and hosts the entire Tekton control plane plus 7 Postgres databases on 111GB
  of unquota'd disk. PR closed.
- 🔴 **SIX pieces of work superseded or nearly clobbered by concurrent sessions** —
  `#651`, `#656`, `#713`, the zombie fix (another session's handled PID REUSE, which
  mine did not), the `CLAUDE.md` rewrite, and the guards/gates trace (`#728`). One was a
  near-miss where a `cp` between branches would have **silently deleted 109 lines** of a
  better fix; only diffing before committing caught it. **`git log origin/main -3` and a
  `gh pr list` before starting on a defect is not optional in this repo.**
- **The gate's permanent character:** four defects made it red on day one, **none in any
  PR being gated**, every one invisible to a dev-host run (stale floor → `#708`; zombie
  `os.kill(pid,0)` → `#722`; module root asserting a `.git` → `#732`; audit-line race →
  `#735`). **Read the step log before believing a red devrc check** — three of the four
  were only diagnosable that way.

## How to verify

```bash
# the requirement is live AND enforced (read it, don't assume)
gh api /repos/innovation-upstream/devrc/branches/main/protection \
  --jq '{required:.required_status_checks.contexts, strict:.required_status_checks.strict,
         enforce_admins:.enforce_admins.enabled}'
#   want: ["tekton/devrc-nodetests"], strict=false, enforce_admins=true

# it actually BLOCKS — the only check that matters
gh pr list --repo innovation-upstream/devrc --state open \
  --json number,mergeStateStatus,statusCheckRollup \
  --jq '.[]|"#\(.number) \(.mergeStateStatus) [\([.statusCheckRollup[]?|"\(.context)=\(.state)"]|join(" "))]"'
#   want: nodetests ERROR/PENDING => BLOCKED ; SUCCESS => CLEAN ;
#         pytests red + nodetests green => UNSTABLE (mergeable)

# the timeout fix is live at the CONSUMER (merged != deployed)
KUBECONFIG=$KC_HOMELAB kubectl -n tekton-ci get pipeline devrc-ci-pipeline \
  -o jsonpath='{range .spec.tasks[*]}{.name}={.timeout}{"\n"}{end}'
#   want: notify=2m0s  gate=45m0s

# 🔴 ESCAPE HATCH if Tekton is down — enforce_admins:true means NO admin override
gh api -X DELETE /repos/innovation-upstream/devrc/branches/main/protection/required_status_checks
```
