---
clawgate-task: 322
---
# Handoff: git repo-pointer leak + the CI gates that hid it — 2026-08-24

## Run this first — the index, one read-only command
```bash
python3 ~/workspace/devrc/scripts/lib/subsystem_recall.py --repo devrc
```
Terse pointers this doc does not carry, curated by past sessions and outliving it.
🔴 RECALL, NOT LIVE OBSERVATION — every line is a pointer to VERIFY, never a current
reading, and it may describe a gotcha already fixed. `scope-absent`/`scope-empty` means
nothing is recorded yet: ordinary, not an error, and not a clean bill of health.
Non-blocking: if it exits non-zero, print the stderr line and carry on.

⚠ Invoke it with an ABSOLUTE `--repo` or from outside `devrc`: run as `--repo devrc` from
inside the repo it resolved `<cwd>/devrc` and died `cannot change to '…/devrc/devrc'`.

## Goal
Answer clawgate #322's probe (does `--set all` rewrite the branch it pushes?), then fix
what it actually found. It found a family: a leaked `GIT_DIR` aims git-writing programs at
a foreign repository. Three programs, three PRs, all merged.

## State now
- Branch: `main`, clean except other sessions' WIP (`scripts/run-node-tests.sh`, +1 line).
- `origin/main` moved **~15 times** during this session; every gate result below names the
  sha it was measured on.

**MERGED and verified by content**
- **#721** `081838cc` — `commit.sh` strips the git repo-pointer vars. Live on **both** hosts.
- **#767** `a8a7e94f` — `backup.py` (the one with a NETWORK) strips them via the shared
  `testlib.gitenv` ledger. Live on **both** hosts (laptop re-verified 2026-08-25).
- **#801** `0f366a4e` — `dl-router` `Store._write()` retries `SQLITE_BUSY`. **Live on both
  hosts** as of 2026-08-24 22:34 — see below.

**Deploy status — all three programs are LIVE on both hosts (re-measured 2026-08-25T04:49Z)**
- `commit.sh` and `backup.py` run **from the working tree** (`ExecStart=… %h/workspace/…`),
  so `readlink -f` terminates in the repo and the `git pull` made them live. Verified by
  grepping the file the unit actually executes.
- ✅ **`dl-router` is now live too.** A `ship.sh`/switch ran at **2026-08-24 22:34** and
  converged both hosts within 10s of each other. Verified at the CONSUMER, not the deploy:
  the unit's `ExecStart` moved `y2686x2s… → ly00qzvv…`; workbench MainPID `1408184`
  (`active/running`, cgroup `…/app.slice/dl-router.service`) is **executing** that path and
  is the PID holding port **8791** — no orphan serving old code; `_retry_busy` occurs **5×**
  in the `store.py` beside the file it is running. Laptop: same store path, started 22:34:58,
  `_retry_busy` ×5.
  ⚠ The switch-during-live-gates hazard that deferred this is still real (a switch drops
  every `home.packages` binary for ~1s, surfacing as a phantom defect in a sibling branch) —
  it was simply not hit this time. Keep checking for live gates before the next one.
- **Laptop** is converged: `drift-check.sh` → **rc 0**, both hosts on `main` at `324693fd`,
  324 managed symlinks resolving, built-source scopes current.

**Cards filed this session** — 🔴 these are **clawgate card ids, NOT devrc PR numbers**:
`clawgate#343` (backup.py, complete), `clawgate#349` (dl-router, complete),
`clawgate#348` (CI preemption, open, repo `homelab-talos`), `clawgate#355` (timeout
invariant, open, repo `homelab-talos`). Evidence added to `clawgate#337` (open, repo
`civitai/talos-infra`). Re-verified against the live board 2026-08-25T04:49Z.

🔴 **Write `clawgate#N`, never a bare `#N`, in a handoff.** Bare numbers here made
`resume-state.sh` resolve all three OPEN cards as **devrc PRs** — they collide with real
ones — and emit three confident, wrong DRIFT findings: two "MERGED but the handoff frames
it as open/in-flight (do the follow-on)" for cards that are open, and one pointing at devrc
PR #355, an unrelated `🔴 DO-NOT-MERGE-YET` airvpn killswitch branch. The reconciler prints
a cross-repo ref as `PR owner/repo#N`; it can only do that if the doc gave it the qualifier.

## Open investigations — live diagnosis state

### The `pre-push` → `tests-on-push.sh` route has never been exercised end-to-end
- **Symptom + exact repro:** #322's original report — `git push -u origin <branch>` from a
  **linked worktree** hangs ~2min, then the branch HEAD is fixture commits
  (`autocommit: N change(s) in the some-scope analyze-service index`), the real commit gone,
  the index wrecked, working files surviving on disk.
- **Observed (with values):** mechanism proven at the unit level and reproduced end-to-end
  against a decoy. git 2.55.0 exports `GIT_DIR` into a pre-push hook from a **linked
  worktree** but not from a main checkout (which gets
  `GIT_EDITOR`/`GIT_EXEC_PATH`/`GIT_PREFIX` only). ⚠ **This line said "ONLY from a linked
  worktree" and that word was wrong** — measured 2026-08-25, `--separate-git-dir` clones,
  submodules (`<super>/.git/modules/<sub>`) and bare repos (a RELATIVE `.`) export it too, so
  "not a worktree" is NO evidence that `GIT_DIR` is unset. The correction matters here
  specifically because `githooks/pre-push` now routes readers to this doc. With it set,
  `git -C "$scope" rev-parse --show-toplevel` returns `$scope` itself → `scope_repo_state`
  reports "its own repo" → the nesting guard is skipped. Decoy went `2320f0a → 8c952fe`,
  HEAD tree `a.md`/`alpha.md`/`beta.md`.
- **Ruled out:** *`--set all`* — REFUTED. Full battery at `8f7473b5`:
  `before == after == 8f7473b5`, tree clean, `collected=14689 passed=14687 failed=0`.
- **Leading hypothesis:** the fixes close it; the hook path is inference.
- **Next probe:** push a throwaway two-file docs commit from a linked worktree with
  `core.hooksPath` set repo-locally to `<repo>/githooks`, and diff HEAD before/after.
  🔴 Re-measure `core.hooksPath` at that moment — it flipped **three times** in one day.

### Nothing exercises the real store, MinIO, or the systemd namespace
- **Observed:** all three fixes were verified against decoys and `env -i PATH HOME`
  (the unit's environment *shape*), never under a real namespace, and `backup.py` was never
  run against the real store or allowed to upload.
- **Ruled out:** doing it casually — a real run ships client-identifying content off-box.
- **Next probe:** `systemd-run --user` with the unit's exact sandbox properties against a
  **throwaway store**, not the live one. The import-reachability half of this was already
  done that way (`IMPORT OK — ledger has 11 names`, with a negative control).

## Closed since this doc was written (2026-08-24 → 08-25)
- ✅ **dl-router deploy** — done 2026-08-24 22:34, both hosts, verified at the consumer
  (see "Deploy status"). ✅ **Laptop converged** in the same run; `drift-check.sh` rc 0.
- ✅ **`clawgate#355` — the timeout invariant is pinned.** ZacxDev/homelab-infra **#398**,
  merged to `trunk` as `e33a77d2`, card `complete`.
  `scripts/tests/test_finally_reporter_timeout_invariant.py` + a 15-mutant battery.
  🔴 It found the hand-written list was short: **SIX** pipelines carry a `finally` block,
  not five — `naida-ux-audit` was missing, and a hardcoded list would have left it
  unguarded. Three adversarial audit rounds; the recurring defect was **a guard narrower
  than the prose describing it**, three separate times. ⚠ Still unverified: nobody has
  re-measured the original three-way probe (that a `timeouts.tasks` expiry SKIPS
  `finally`) against the live cluster — that premise now sits under a merged test.

## Next steps (ranked)
1. **`clawgate#348` — CI preemption (homelab).** 🔴 **CORRECTED 2026-08-25 — this item as
   originally written named a lever the repo had ALREADY REJECTED, and bundled two
   different clusters.** Read "The `#348` correction" below before acting. Short form: the
   mechanism is now CONFIRMED (live `Preempted` events) and its confirmed preemptor has
   since been moved off devrc's node by homelab-infra PR #396, so criteria 2–5 need
   **re-deciding rather than executing**. Full re-scope is on the card itself.
2. **`clawgate#337` — a DIFFERENT CLUSTER.** `civitai/talos-infra`, 20 nodes, the
   `tekton-build=true` pool. It was bundled with `#348` above; that was wrong, and nothing
   measured on homelab bears on it. Its figures are from the 2026-08-22 scan and are stale;
   the one fact that decides whether this is "restore a node" or "re-plan the build pool"
   is whether the cordoned RMA node `talos-x3r-mnv` is back. Re-measure before acting.
3. **Exercise the `pre-push` → `tests-on-push.sh` route end-to-end** — the first open
   investigation ABOVE. It is the only claim in this doc still resting on inference.

## The `#348` correction — read this before touching CI preemption

🔴 **This doc told you to bound devrc CI concurrency. The repo had already rejected that
with a measurement, three days before I wrote it.** `ci-priority-classes.yaml` records a
cap simulated against the real arrival trace of 106 gate TaskRuns:

```
  cap  queue wait p50/max   runs blowing the gate's 45m deadline
    4       19.2m / 69.1m   53/106 (50%)
    6        0.0m / 26.8m   12/106 (11%)
    8        0.0m / 20.1m    3/106  (3%)
                            (vs 14/106 = 13% killed today)
```

Worse at every cap that would help, because **a queued TaskRun's clock starts at
CREATION** — it burns its own deadline while Pending. Re-propose only with a mechanism
that queues WITHOUT the deadline running (`spec.status: PipelineRunPending` plus a
releaser, which puts a required merge gate behind a bespoke controller). The irony is
exact: the gotcha two sections down says *read the decision record before proposing a
fix*, and this line was written without doing so.

**Mechanism CONFIRMED** 2026-08-25T04:46:13Z (homelab-infra PR #397, still open): five
gate pods, five explicit `Preempted` events, all five `pytests` at `exit=255` within one
second having started minutes apart. 🔴 **The preemptor was `gitops-validate` — CI
preempting CI**, both pinned to the same node while the cluster has four.

**And then the premise moved.** homelab-infra **#396** merged at `05:32:44Z` — *46 minutes
after* that confirming measurement — and gave `gitops-validate` its own node. Verified
live 2026-08-25: gitops-validate on `talos-uvh-gtj`, devrc-ci on `talos-xr6-r7p`,
clawgate-ci on `talos-jkj-deb`. ⚠ #397's header says "re-confirmed post-fix"; that is post
the RIGHT-SIZING fix, **not** post #396. Do not read it as evidence about the current
topology.

🔴 **AND THE OBVIOUS POST-FIX CHECK IS NOT EVIDENCE.** Gate TaskRuns killed
(`exit=255/137`) went **30 of 117 before #396 → 0 of 13 after**. That looks decisive and is
not: `ci-priority-classes.yaml` measured that *kills only ever occur at ≥5 concurrent devrc
gates (0 kills in the 18 runs at ≤4)*, and the post-#396 window peaked at **4**. Zero kills
is exactly what the UNFIXED system produces over that window. The path was not exercised;
#396's effect is **UNMEASURED**. To verify, wait for a window reaching ≥5 concurrent gates
and re-count.

**Rejected-with-a-measurement, do not re-propose:** concurrency capping (above),
ResourceQuota (cannot be scoped safely — scoped to `ci-bulk` it covers the
`notify`/`report`/affinity-assistant pods, which declare no requests, and a compute quota
rejects those outright; losing `report` is the worst failure this platform has), and
`retries` on the gate task (tried, REVERTED as a trap — Tekton retries genuine verdicts
too). **Still-open lever:** request inflation from Tekton summing SEQUENTIAL steps —
`auditloop-ci` was 2.8×/3.2× (fixed by #399), `clawgate-ci` remains 2.4×/1.8×. The
scheduler's message was `Insufficient cpu`, not `Insufficient pods`.

## Gotchas / decisions / dead-ends
- 🔴 **Read the subsystem's decision record before proposing a fix.** I recommended four
  fixes for the CI preemption; `ci-priority-classes.yaml` had already tried and rejected
  **all four** with measurements. Separately I dispatched an agent to right-size pipeline
  requests — already merged as homelab-infra #389 an hour earlier. A
  `# 🔴 was tried and REVERTED` comment is the cheapest thing in the repo to find.
- 🔴 **`ci-bulk` at `-10000` is CORRECT, not a misconfiguration.** Measured: it is CI-only
  (301 pods, all `tekton-ci`), and the pinned node `talos-xr6-r7p` runs cert-manager, the
  Flux image controllers, external-dns and real apps at default priority. Preemption of
  devrc gates *by those* is the system working as designed. **Do not raise it.**
  ⚠ **But that argument does not cover the case actually observed.** The confirmed
  preemptor was `gitops-validate` — **another CI pipeline at priority 0**, not a production
  workload. #396 addressed that by node separation rather than by priority, which is why
  `#348`'s criterion 2 ("stop running below default priority") is now a decision to
  re-make rather than a task to execute. Note that priority `0` + `preemptionPolicy: Never`
  would make the gate non-preemptible by default-priority pods (preemption requires
  STRICTLY higher priority) while still unable to preempt anyone — **UNTESTED; do not act
  on that sentence without testing it.**
- 🔴 **Two tiers disagree in BOTH directions.** #721's hermetic tier was red while dev-host
  passed; #801's dev-host was red while hermetic passed. Read both, always.
- 🔴 **The pipe trap, three ways this session:** `nix build … | tail; echo $?` printed
  `NIX_BUILD_EXIT=0` directly under `RESULT: FAIL`; a wrapper's `…; echo "EXIT=$?"` always
  reports 0; and a `grep … | head -30` truncated a caller list, hiding a test and turning a
  gate round red. Capture status directly; read the `RESULT:` line's content.
- **Every audit finding across three PRs was a claim wider than the code**, never a logic
  bug: a comment calling `GIT_OBJECT_DIRECTORY` harmless when it wrote client content into a
  foreign object store (the test's fingerprint was blind to `objects/`); a docstring naming
  an `& 0xFF` mask no test pinned; a ledger guard that `INSERT OR REPLACE INTO` walked past
  because it substring-matched `INSERT INTO`; a stated 30s bound that measured 40s; and a
  comment promising `Restart=always` when `server.py` actually degrades to an in-memory
  store serving sticky 503s.
- **#349 was filed as a flaky test and was wrong.** Its own verifier showed rows genuinely
  LOST (125 then 25 of 150). The test was right; the `Store` was wrong. Implementing the
  card as filed would have deleted the coverage that found it.
- **Dead end:** reproducing the SQLite flake with CPU load — 0/6 with 20 burners on 24
  cores, 0/6 unloaded. In WAL mode a 10s lock wait means **I/O** stall. The mechanism was
  established with a `busy_timeout` sweep instead.
- **Dead end:** running two pytest roots in one invocation (`dl-router` + `browser-bridge`)
  → 8 collection errors from colliding module names. The runner runs targets separately.
  My instrument, not the tree.
- ⚠ The base clone carries other sessions' uncommitted WIP; it blocked an ff-merge once.
  **Hash a dirty file against recent commits before treating it as WIP** — both were
  genuine, neither a stale orphan. Never `git stash` here.

## How to verify
```bash
# the three fixes are present on main (content, not ancestry — squashes are never ancestors)
git -C ~/workspace/devrc show origin/main:scripts/analyze-service-index/commit.sh | grep -c DEVRC_GIT_REPO_POINTERS
git -C ~/workspace/devrc show origin/main:scripts/analyze-service-index/backup.py   | grep -c strip_repo_pointers
git -C ~/workspace/devrc show origin/main:scripts/dl-router/store.py                | grep -c _retry_busy

# is dl-router LIVE? (runs from /nix/store — a pull does NOT deploy it)
p=$(systemctl --user show dl-router.service -p ExecStart | grep -oE '/[^ ;]+server\.py'); readlink -f "$p"
grep -c _retry_busy "$(dirname "$(readlink -f "$p")")/store.py"   # want: non-zero

# the pointer family is contained (want: HEAD unmoved, 0 foreign objects, scope commits to itself)
# build a decoy repo + worktree, export each of GIT_DIR/GIT_OBJECT_DIRECTORY/GIT_INDEX_FILE/
# GIT_COMMON_DIR/GIT_CEILING_DIRECTORIES/GIT_TEMPLATE_DIR/GIT_CONFIG in turn, run commit.sh

# both tiers — read RESULT:, never an exit code, and never through a pipe
nix develop <wt> --command bash <wt>/scripts/run-tests.sh <wt> --set all
nix build <wt>#checks.x86_64-linux.pytests ; nix build <wt>#checks.x86_64-linux.nodetests
```
