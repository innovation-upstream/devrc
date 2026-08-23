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

**Merged, verified on `main` by content (squash breaks ancestry — content is the check):**

| PR | what |
|---|---|
| devrc `#613` | the hosted-vs-local DECISION — local authoritative, hosted an entry-level advisory |
| devrc `#622` | a real client subdomain committed to a PUBLIC repo, redacted to `*.example.test` |
| devrc `#623` | 3 guard-core tests took their verdict from the runner's branch |
| devrc `#629` | a REPLACE silently deleted durable findings — classify the drop above the diff |
| devrc `#647` | a marker on a CONTINUATION line reached no surface; auto window escalation |
| devrc `#677` | the base-ref ladder had no `trunk`; stale-base detection for the handoff writer |
| homelab-infra `#370` | the devrc CI pipeline, trigger, CEL |

Closed as **superseded** — both fixed better, concurrently, by other sessions: `#651`
(beaten by `#650`, which added a `doc.count(...) == 1` unambiguity guard mine lacked) and
`#656` (beaten by `#649`, which isolates by relocating `HOME` rather than an env override —
incompatible mechanisms, and `#649`'s covers every consumer of `~`).

**Deploy/verify status, stated separately:**
- `ship.sh` converged BOTH hosts and was verified **at the consumer**, not just at the
  deploy: skill store path moved, byte-identical to `origin/main`, new rule present, with a
  positive control on the grep pattern first.
- Tekton `devrc-ci-pr` is **LIVE AND FIRING** as of 2026-08-22. It was deployed-and-inert at
  **0 of 370 pipelineruns**; root cause was private org membership, not the CEL (below).
  🔴 **Verified behaviourally, not by the control:** PR `#706` was opened at 17:45:36Z and
  `devrc-ci-djm7n` was created at **17:45:38Z — two seconds later**. Two more runs followed
  for concurrent PRs (`87vck`, `mnxfh`), so it fires for other sessions' PRs too, and devrc
  PRs now carry **2 status checks** where the repo had 0 checks on every PR ever.
- **First-run result — the split is real, and it is NOT what this doc predicted:**
  | leg | verdict | detail |
  |---|---|---|
  | `tekton/devrc-nodetests` | **SUCCESS** | suites=4 files=33 tests=1119 pass=1119 (floor 1098) |
  | `tekton/devrc-pytests` | **FAILURE** | collected=14689 passed=14686 skipped=2 **failed=1** (floor 13400) |
  🔴 **The single pytests failure is a STALE FLOOR, not the nix-sandbox blocker.** Verbatim:
  `scripts/signal/tests collected 717 tests but its floor is only 553 … Raise the TARGET_FLOORS
  entry to "scripts/signal/tests|682"`. The gate computed its own replacement (717 through the
  documented `m - min(50, max(1, m/20))` rule) so it needs no measurement. **The sandbox
  blocker described under "the `devrc-pytests` leg" below is GONE** — `seed-nix` exited 0 and
  14,686 tests ran in-pod. Re-read that section as history before acting on it.
  ⚠ Note the shape: every *step* exited `rc=0` and only `verdict` exited `rc=1`. The pipeline
  derives its verdict from **parsed output**, not exit codes — so never read this gate's
  result off a step's exit status.

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

## Next steps (ranked)
1. ✅ **Author gate decided and applied** (block above) — membership publicized, CEL untouched,
   so there is **nothing to merge or reconcile in homelab-infra**. What remains is the
   behavioural confirmation: a PipelineRun. `kubectl -n tekton-ci get pipelineruns | grep devrc`
   must become non-empty. 🔴 The unauth control flipping to `MEMBER` is evidence about the
   API's viewer-less computation — **it is not a PipelineRun**. Do not call this verified
   until a run exists.
2. **Requiring a check — NOT YET, and the blocker is now a different one.** `nodetests` has
   been 1149/1149 on every run. `pytests` reached `BOTH TIERS PASS` (15150/0) on
   `devrc-ci-vl88r`, 2026-08-23T04:00Z. The remaining blocker is 2e below, not test health.
   🔴 **The nix-sandbox premise below is FALSE and stayed false** — `seed-nix` exits 0 and
   ~15,150 tests run in-pod. Read that section as history.
   🔴 **What the first day actually taught: FOUR separate defects made the gate red, none of
   them in any PR being gated, and every one invisible to a dev-host run.** That is the
   pattern to expect, not an unlucky streak:
   | defect | fixed by | why local runs missed it |
   |---|---|---|
   | `scripts/signal/tests` floor 164 behind | `#708` | the drift ceiling only runs in the gate |
   | `os.kill(pid,0)` says a ZOMBIE is alive | `#722` | a dev host's PID 1 is systemd, which reaps |
   | module root asserted THIS tree has a `.git` | `#732` | the runner's source is a `/nix/store` path |
   | audit-line race left at 2 of 3 sites | `#735` | needs a slow handler; 0/12 red when idle |
   **So: do not read a red devrc check as a verdict on the PR until you have looked.** Three
   of those four were diagnosed only by reading the step log.
   ⚠ **Concurrency cost, measured: SIX pieces of work this session were superseded or nearly
   clobbered** — `#651`, `#656`, `#713` (identical floor value to `#708`), the zombie fix
   (another session's was better and handled PID REUSE), the `CLAUDE.md` rewrite, and the
   guards/gates trace (`#728`). One of those was a near-miss where a `cp` between branches
   would have **silently deleted** 109 lines of a better fix; only diffing before committing
   caught it. **`git log origin/main -3` and a `gh pr list` before starting on a defect costs
   nothing next to a wasted branch — or a deleted one.**
2e. 🔴 **THE BLOCKER ON REQUIRING ANY CHECK — a timed-out run posts NOTHING and the PR sits on
   `pending` forever.** When a PipelineRun hits `timeouts.tasks`, the `finally` report task
   does not run at all, so no status is ever posted. Measured on `devrc-ci-nnt6f` and
   `devrc-ci-9p6mf`: `childReferences` `[notify, gate]` only, checks still `pending` hours
   later. **A required check in that state is unsatisfiable and no re-run clears it** — only a
   fresh push does. Fix the reporting path (or raise the budget) BEFORE marking anything
   required.
   ⚠ **`enforce_admins: true` is a red herring — I raised it as a lockout risk and was wrong.**
   `required_status_checks` is `null` and there are 0 rulesets, so there is nothing to enforce
   and admins are not blocked by anything today. It only becomes real once a check is required.
2d. 🔴 **The gate CONGESTS: right-size its requests.** The first day it fired, 8 runs queued
   and **5 sat `Pending` 15+ min** on `Insufficient memory` while the node idled at 26% mem /
   20% CPU. Every devrc run is pinned to ONE node (shared `nix-cache` RWO PVC + the
   pipeline's `volumeClaimTemplate` → Tekton's affinity assistant), and at 4Gi+2Gi per run a
   32Gi node fits five. Measured peak is **823Mi / 1043m** for the pytests leg — a ~5×
   over-request. And because Tekton steps run **sequentially**, the pytests and nodetests
   requests are never both in use yet are both summed into the pod request. Fixed in
   homelab-infra **`#378`** (requests only; every limit untouched, so nothing can OOM that
   could not before). Per-run 6Gi→3Gi, 3→1.5 CPU ≈ 10 concurrent instead of 5.
2c. **`CLAUDE.md` is now falsified by this work and its own gate cannot see it.** On
   `origin/main` it still asserts "**NO AUTOMATED GATE IS RUNNING**", "no Tekton trigger names
   devrc", and "`statusCheckRollup` returns **0 checks** on every PR" — all three measurably
   false now. `scripts/tests/test_ci_claim_matches_reality.py` will **not** catch it: it
   accepts `{none, other}` when no GitHub Actions workflow exists and its header says it
   cannot see Tekton. So flip the marker to `<!-- merge-gate: other -->` (the value that
   exists precisely for a gate this test cannot observe) and rewrite those sentences by hand.
2b. ✅ **FIXED — a stale `origin/HEAD`, NOT a hook bug.** Symptom: the SessionStart hook synced
   `CLAUDE.md` from `origin/trunk` though devrc's default branch is `main`, and the two had
   **DIVERGED** (measured 2026-08-22: 14 commits in `main` not in `trunk`, 3 the other way).
   It lands **staged** in the base clone, so a careless commit ships it. Measured damage: it
   reverted merged PR #702 (`4ea2405d`) — restoring the flat claim "a gate SHIPS IN THIS REPO,
   **uninstalled**" that #702 recorded as *measured FALSE*, and deleting the 🔴 #322 warning
   that a pre-push gate rewrote a branch mid-push.
   🔴 **The obvious fix was the wrong one.** The hook — `~/.claude/local-hooks/base-clone-
   staleness.sh:40-43`, which is **outside this repo, unmanaged and per-host** — hardcodes no
   ref at all. It prefers the branch's `@{upstream}` and falls back to
   `refs/remotes/origin/HEAD`. Two conditions had to coincide: the base clone was on a
   **detached HEAD** (so no `@{upstream}`), and its `refs/remotes/origin/HEAD` was **stale at
   `origin/trunk`**. Editing the hook would have broken it for every other repo.
   **Fix applied:** `git -C ~/workspace/devrc remote set-head origin -a` → now `origin/main`.
   ⚠ Per-clone and per-host: the laptop, and any fresh clone, can still inherit the wrong
   pointer. `git symbolic-ref --short refs/remotes/origin/HEAD` is the check.
3. **Fixture-author commits — 2 of the 3 branches are GONE; re-measure before acting.**
   Measured 2026-08-22 with `git ls-remote --exit-code --heads origin <branch>`:
   `zach/requires-env-skip-pins` **still exists** (1 commit); `zach/handoff-doc-rollback-on-
   blocked-commit` and `fix/guard-msg-convention-not-structural` are **gone from origin**. So
   only one `git commit --amend --author=…` + force-push remains, **by its owner**. 26 more
   are on the preserved incident branch and are *supposed* to carry it.
4. **Fix `check-tekton-app-install.sh`'s `CDPATH` bug** (homelab-infra): `HERE="$(cd -- … && pwd)"`
   returns TWO lines when `CDPATH` is set, because bash's `cd` echoes the target. Use
   `cd -- "$d" >/dev/null && pwd`.

### ✅ `origin/trunk` was TEST-FIXTURE DEBRIS on a PUBLIC repo — DELETED 2026-08-22
The wrong-`origin/HEAD` in 2b only did damage because the branch it pointed at exists. What it
is, measured 2026-08-22:
- Tip `ff6b2ca3` — author **`t <t@t>`**, message `seed`, adds a single file `seed.py`. That is
  a fixture commit, pushed **2026-08-21**, the date of the test-tier incidents (#673/#683/#689
  — "a test rewrote the operator's clone and pushed fixture commits to the REAL origin").
  `seed.py` is absent from `main`.
- Underneath it sit **two genuine commits** — `0bf1b324`, `276d56eb` (`handoff_doc` work).
- 🔴 **They are already in `main`, and ancestry says otherwise.** `merge-base --is-ancestor`
  answers **NO** for both, because they landed by **squash**. Checked by CONTENT instead: all
  **9** symbols they introduce (`_undo_write`, `_block_commits`,
  `TestBlockedCommitLeavesNoTrace` + 6 tests) are present in `main`. Nothing is orphaned.
  This is the squash/ancestry trap in `claude/RULES.md` firing on a real branch — the "NO"
  reads as "unmerged, don't delete", and it is wrong.
- **Deleted** after re-verifying at the moment of the destructive step (tip still `ff6b2ca3`,
  0 of the 9 symbols missing from `main`) — not from the earlier survey, per the stale-
  observation rule. **Recovery sha:** `ff6b2ca39b5d228aa77243dee2f930a2cfe025fd`, also kept
  locally as `refs/recovery/trunk-ff6b2ca3` on the workbench →
  `git push origin ff6b2ca3:refs/heads/trunk`.

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

## How to verify
```bash
# the gate is live on the CR (pod age tells you NOTHING — triggers are hot-read)
KUBECONFIG=$KC_HOMELAB kubectl -n tekton-ci get eventlistener github-listener \
  -o jsonpath='{range .spec.triggers[*]}{.name}{"\n"}{end}'      # want: devrc-ci-pr among 7

# has it EVER fired? (this is the open item — currently 0)
KUBECONFIG=$KC_HOMELAB kubectl -n tekton-ci get pipelineruns | grep devrc

# the App can see devrc (carries its own positive control; refuses to report if it fails)
cd <homelab-infra checkout> && env -u CDPATH SOPS_AGE_KEY_FILE=$HOME/workspace/homelab-talos/.secrets/age.key \
  nix-shell -p sops yq-go openssl curl jq --run "env -u CDPATH bash scripts/check-tekton-app-install.sh"
#   want: control ZacxDev/homelab-infra 200, subject innovation-upstream/devrc 200

# devrc's own gate, from a CONTAINED sandbox only (see the recipe above)
cd <scratch>/gate && nix develop --command bash scripts/run-tests.sh .
#   read RESULT: and collected=/passed=/failed= from the LOG CONTENT, never an exit code through a pipe
```
