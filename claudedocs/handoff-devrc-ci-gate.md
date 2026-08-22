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
- Tekton `devrc-ci-pr` was **DEPLOYED and INERT**: live on the EventListener CR (still 7
  triggers, CEL byte-identical), **0 of 370 pipelineruns**. Root cause found AND fixed
  2026-08-22 — private org membership, not the CEL. Awaiting the first PipelineRun as proof.

## Open investigations — live diagnosis state

### 🔴 The devrc Tekton gate never fires — `author_association` is CONTRIBUTOR, the CEL demands MEMBER
- **Symptom + exact repro:** every devrc PR since the trigger went live (23:32Z) produces no
  PipelineRun. Repro: open any PR against `innovation-upstream/devrc`, then
  `kubectl -n tekton-ci get pipelineruns | grep devrc` → empty.
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
- **Next probe (verbatim), once a fix is chosen** — re-check that the payload, not the API,
  now satisfies the filter:
  ```bash
  bash /tmp/.../scratchpad/payload.sh ~/workspace/homelab-infra   # prints each CEL clause against the real delivery
  kubectl -n tekton-ci get pipelineruns | grep devrc              # must become non-empty
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
gh api /repos/innovation-upstream/devrc/pulls/703 | jq -r .author_association          # MEMBER
curl -sS https://api.github.com/repos/innovation-upstream/devrc/pulls/703 \
  | jq -r .author_association                                                          # CONTRIBUTOR
```
This replaces the `/app/hook/deliveries` route entirely: the unauthenticated read is the
same viewer-less computation the webhook gets, and needs no App private key.

**Three independent confirmations of the mechanism:**
1. Auth vs unauth on the same PR at the same instant: `MEMBER` vs `CONTRIBUTOR`.
2. devrc has **zero direct collaborators** (`?affiliation=direct` → empty), so there was no
   `COLLABORATOR` fallback either — access came solely from the private org-admin role.
3. 🔴 **The one PR trigger that DOES fire discriminates it.** `gitops-validate-pr` carries the
   *same* `['OWNER','MEMBER','COLLABORATOR']` clause and works — because it targets
   `ZacxDev/homelab-infra`, a **personal** repo, where the association is `OWNER` and `OWNER`
   is viewer-independent. devrc is **org-owned**, so it is the only trigger this can reach.
   A rule that works on six triggers and fails on the seventh is about the REPO's ownership,
   not the rule.

**Fix applied — publicize the membership, CEL UNCHANGED:**
```bash
gh api -X PUT /orgs/innovation-upstream/public_members/ZacxDev     # reverse: -X DELETE
```
Verified by re-running the control: PRs 701/702/703 all flipped `CONTRIBUTOR` → `MEMBER`
unauthenticated, with **no change to the CEL, the trigger, or homelab-infra**. The reviewed
security posture is preserved exactly; nothing was widened.

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

### The `devrc-pytests` leg must NOT be made required yet
`tekton/devrc-nodetests` can be required immediately. `devrc-pytests` has **1 known
environment failure**: the nix sandbox does not exist in the pod, and all three levers
(`privileged`, `CAP_SYS_ADMIN`, `seccomp Unconfined`) are rejected by PodSecurity
`baseline:latest` — each verified by an actually-rejected PipelineRun. Requiring it now
would be the permanently-red gate. Options documented in homelab-infra `#370`.

## Next steps (ranked)
1. ✅ **Author gate decided and applied** (block above) — membership publicized, CEL untouched,
   so there is **nothing to merge or reconcile in homelab-infra**. What remains is the
   behavioural confirmation: a PipelineRun. `kubectl -n tekton-ci get pipelineruns | grep devrc`
   must become non-empty. 🔴 The unauth control flipping to `MEMBER` is evidence about the
   API's viewer-less computation — **it is not a PipelineRun**. Do not call this verified
   until a run exists.
2. **Require `tekton/devrc-nodetests` only.** Not `devrc-pytests`.
2b. 🔴 **The SessionStart hook syncs `CLAUDE.md` from `origin/trunk`, but devrc's default
   branch is `main`, and they have DIVERGED** (measured 2026-08-22: 14 commits in `main` not
   in `trunk`, 3 the other way). It lands **staged** in the base clone, so a careless commit
   ships it. Measured damage: it reverted merged PR #702 (`4ea2405d`) — restoring the flat
   claim "a gate SHIPS IN THIS REPO, **uninstalled**" that #702 recorded as *measured FALSE*,
   and deleting the 🔴 #322 warning that a pre-push gate rewrote a branch mid-push. Every
   session in this repo starts with that regression staged. Fix the hook's ref; until then
   `git checkout origin/main -- CLAUDE.md` at session start.
3. **3 commits authored `T <t@example.com>`** are on 3 feature branches (`zach/requires-env-skip-pins`,
   `zach/handoff-doc-rollback-on-blocked-commit`, `fix/guard-msg-convention-not-structural`) —
   one `git commit --amend --author=…` + force-push each, **by their owner**. 26 more are on
   the preserved incident branch and are *supposed* to carry it.
4. **Fix `check-tekton-app-install.sh`'s `CDPATH` bug** (homelab-infra): `HERE="$(cd -- … && pwd)"`
   returns TWO lines when `CDPATH` is set, because bash's `cd` echoes the target. Use
   `cd -- "$d" >/dev/null && pwd`.

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
