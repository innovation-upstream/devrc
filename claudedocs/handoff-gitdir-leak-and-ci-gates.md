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
- Branch: `main`, clean apart from two other-session untracked files
  (`nix/system/apply-nebula-443.sh.LOCAL-preserved-2026-08-02`,
  `scripts/dl-router/tests/load_test_store.sh`).
- **Both hosts converged and clean** — `drift-check.sh` rc 0, workbench + laptop both
  `main == origin/main`, 328/331 managed symlinks resolving, 0 dangling.

**MERGED AND VERIFIED BY CONTENT this session** (a squash is never an ancestor — all
checked by reading the file on `origin/main`, never by `merge-base --is-ancestor`):

| PR | what |
|---|---|
| devrc **#813** `a8089a51` | this doc: dl-router marked live; card ids qualified `clawgate#N` |
| devrc **#825** `f0f90d62` | this doc: a REJECTED lever was listed as open; two clusters de-bundled |
| devrc **#830** `3ad7f66a` | **the `GIT_DIR` root cause — identified**, + a canary test |
| homelab-infra **#398** `e33a77d2` | `clawgate#355` — the finally-reporter timeout invariant, pinned |
| homelab-infra **#397** | merged by its owner; my correction comment landed with it |

**clawgate board:** `#322` `#343` `#349` `#355` complete. `#348` and `#337` **open by
design**, both re-scoped with corrected evidence (see Next steps).

**Deploy status:** nothing this session needs a `home-manager switch` — the work was
`claudedocs/`, `scripts/tests/`, `scripts/testlib/` and `githooks/` (and `core.hooksPath`
measured **unset** at every push, so the hook is not installed). Other sessions' skill
changes did need one and have had it; `drift-check` confirms.

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

### The `pre-push` → `tests-on-push.sh` route — TRIGGER identified, end-to-end leg still not walked
- 🔴 **This supersedes the block above that called the root cause unidentified. The
  eliminations there are still true; the framing was wrong.**
- **Observed (with values):** git 2.55.0, parent scrubbed of every `GIT_*` name,
  reproduced twice by me and independently on a second rig by an auditor:

  | push origin | `GIT_DIR` in `pre-push` |
  |---|---|
  | main checkout | *(none)* — `GIT_EXEC_PATH`, `GIT_PREFIX` only |
  | **linked worktree** | `<repo>/.git/worktrees/<name>` |
  | `--separate-git-dir` | `<separate gitdir>` |
  | submodule | `<super>/.git/modules/<sub>` |
  | bare repo | `.` *(RELATIVE)* |

  **git itself exports it; no outer caller is required.** `clawgate#322` was a push from a
  linked worktree — the match is exact. Damage mechanism, reproduced in isolation:
  `git -C <plain-non-repo-dir> rev-parse --show-toplevel` returns `<repoA>` clean and
  `<repoA>/nested/scope` with `GIT_DIR` leaked, so a nesting guard asking "is this its own
  repo?" gets YES for a directory that is not one. The strip restores `<repoA>`.
- **Ruled out:** *"pushing cannot set `GIT_DIR` on its own"* — REFUTED, see above. The old
  live-process scan ("46 carry some `GIT_*`, 0 carry `GIT_DIR`") is **not counter-evidence
  and must not be cited as narrowing it**: git sets the variable only for the duration of
  the hook, where no process scan could ever observe it.
- **Leading hypothesis:** the remaining leg is mechanical, not mysterious — the strip in
  `githooks/tests-on-push.sh` runs before any corruptible read, so the route should be
  closed. Untested end to end.
- **Next probe:** actually walk it. Clone devrc to scratch, add a LOCAL bare remote, make a
  linked worktree, set `core.hooksPath` repo-locally to `<clone>/githooks`, commit two docs
  files, push from the worktree, and diff HEAD before/after plus grep for `autocommit:`
  fixture commits. 🔴 Re-measure `core.hooksPath` at that moment — it read **unset** every
  time this session and the record says it has flipped three times in one day.

### 🔴 The same false claim existed in EIGHT places and I found one
- **Symptom:** the claim *"`git push` does not hand `GIT_DIR` to `pre-push`" / "root cause
  unidentified"* was true only of a main checkout, and had been copied into eight locations.
- **Observed:** I found 1. Audit round 1 found 3 more in live code plus 2 merge-blocking
  🔴. Its closing 🟢 pointed at a 5th (docs, under a **`RETRACTED:`** banner); chasing that
  surfaced a 6th (an *Open investigations* block). The delta re-audit found a **7th and
  8th**. The 7th was in the **module docstring of GUARD 9's own test file** — the first
  thing anyone reads before touching `REPO_POINTER_VARS`, arguing the strip was hygiene
  against an exotic caller — and it cited `test_git_push_does_not_export_GIT_DIR_to_pre_push`,
  a name renamed two commits earlier, so `pytest -k` on it **selected zero tests and exited
  0**. The 8th was created by my own fix: a new scope clause started routing readers to
  `handoff-gitdir-leak-and-ci-gates.md`, which still said "**only** from a linked worktree".
- **Ruled out:** *"grep once and you have them all"* — the phrasings differ per site
  (`is NOT`, `still unknown`, `UNIDENTIFIED`, a test NAME, a `RETRACTED:` banner). Only
  re-grepping after each round converged.
- **Leading hypothesis:** none needed — all eight are corrected on `main`.
- **Next probe:** none open. If a ninth appears, the search that finds it is
  `find . -path ./.git -prune -o -type f -print0 | xargs -0 grep -lniE 'NOT .?GIT_DIR|root cause.*(still )?(unidentified|unknown)'`
  (`grep -r` here is a ugrep function that honours `.gitignore` and will miss generated paths).

### `clawgate#348` — preemption CONFIRMED, then its premise moved 46 minutes later
- **Observed:** confirmed live `2026-08-25T04:46:13Z` (homelab-infra #397): five gate pods,
  five explicit `Preempted` events, all five `pytests` at `exit=255` within one second having
  started minutes apart. 🔴 **The preemptor was `gitops-validate` — CI preempting CI**, both
  pinned to the same node while the cluster has four. Then **#396 merged at `05:32:44Z`** —
  46 minutes *after* the confirming measurement — and gave `gitops-validate` its own node
  (`talos-uvh-gtj`; devrc-ci stays `talos-xr6-r7p`; clawgate-ci `talos-jkj-deb`), verified live.
- 🔴 **Ruled out — the obvious post-fix check is NOT evidence, and I nearly reported it as
  one.** Kills went **30 of 117 before #396 → 0 of 21 after**. But `ci-priority-classes.yaml`
  measured that kills occur only at **≥5 concurrent** devrc gates (0 in the 18 runs at ≤4),
  and matching on that gives **before 11/32 killed at ≥5 (34.4%), after 0/3**. Three clean
  runs at a 34.4% rate happen ~28% of the time by luck. **UNMEASURED, not fixed.**
- **Next probe:** wait for ~8–10 gate runs that *start* at ≥5 concurrency and re-count.
  Reproduce: pull `taskruns -n tekton-ci`, take `devrc-ci-*-gate`, classify killed as any
  step terminated `exitCode` 255 or 137, compute concurrency as gate TaskRuns live at each
  run's own `startTime`, cut at `2026-08-25T05:32:44Z`.

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
1. **Walk the `pre-push` route end-to-end** — the one item fully in this repo's control and
   the last inference in this doc. `devrc`, no files changed; scratch clone + local bare
   remote (recipe in the Open-investigations block above). NOT claimed by anyone.
2. **`clawgate#348` — do NOT act yet; it is gathering data.** Re-run the concurrency query
   above once ≥5-concurrency runs accumulate. 🔴 Changing the priority class now would
   confound the only clean experiment running. `homelab-talos`,
   `clusters/homelab/apps/tekton-pipelines/triggers/ci-priority-classes.yaml`.
   🔴 Concurrency capping, ResourceQuota and `retries` are all
   **REJECTED-WITH-A-MEASUREMENT** — read that file before proposing anything.
3. **`clawgate#337` — needs credentials I did not have.** `civitai/talos-infra`, a DIFFERENT
   cluster from #348 (20 nodes, the `tekton-build=true` pool). Its figures are from the
   2026-08-22 scan and are stale. One fact decides whether it is "restore a node" or
   "re-plan the pool": is the cordoned RMA node `talos-x3r-mnv` back?

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

- 🔴 **`nix build` exits 0 while its log says `RESULT: FAIL`. This fired THREE times in one
  session.** Two separate builds of `checks.x86_64-linux.pytests` at a broken commit both
  reported exit 0. Append a marker line to the command and grep the log for `RESULT:`; the
  failing build also prints `For full logs, run:` while a passing one does not. Verify a
  pass POSITIVELY — the drv's `out` path exists **and** the log carries `TOTAL collected=…
  failed=0` — because a reassuring silence is indistinguishable from a build that did nothing.
  ⚠ One log contained BOTH `RESULT: all good` and `RESULT: FAIL (exit=1)`; a grep that stops
  at the first match reads the failing build as passing.
- 🔴 **A green test FILE is not a green TIER.** My pre-audit verification of #830 was
  "`test_git_repo_isolation.py`: 113 passed" — true, and blind to the fact that the file's
  `#!/usr/bin/env bash` string literal turned a *different* test red and made the hook
  unrunnable in the nix sandbox entirely. `scripts/tests` is `HERMETIC_TARGETS[0]`, so that
  would have been a red required check. **`mockbin.write_exec` owns the shebang**; a body
  written there must be POSIX (`${!v}` is a bash-ism and `mockbin.SH` is `/bin/sh`).
- 🔴 **My own probe reported the OPPOSITE of the truth.** Its `printf`s went to stdout while
  its verdict grepped a file, so it printed `TRIGGER ABSENT` regardless of what git did —
  with the real answer visible two lines above in the raw output. Every instrument now
  carries a positive control: `GIT_EXEC_PATH` is exported to hooks unconditionally, so if it
  is absent the hook never ran and an absent `GIT_DIR` proves nothing.
- 🔴 **zsh ate a git ref.** `$c:scripts/run-node-tests.sh` — the `:s` is a history modifier,
  so the expansion was well-formed and WRONG. Brace it: `${c}:path`.
- **`git merge --ff-only … | tail -1` hid its own error.** The "Updating a..b" line prints
  last; the real message ("local changes would be overwritten") was above it, and I reported
  a sync that had not happened. Read the whole output.
- **Dead end:** running the dev-host tier from a shared checkout. GUARD 10 reports
  `cannot attribute` because sibling sessions write the common-dir `.git/config`
  (`git worktree add` alone does it). Not a defect in the change — use the hermetic tier.
- **`resume-state.sh` resolved bare `#N` card ids as devrc PRs** and emitted three confident,
  wrong DRIFT findings, one pointing at an unrelated `DO-NOT-MERGE-YET` branch. Write
  `clawgate#N` in handoffs; the reconciler prints cross-repo refs as `owner/repo#N` only if
  the doc gave it the qualifier.
- ⚠ **CARRIED FORWARD from the replaced status block — the switch-during-live-gates hazard
  is still real.** A `home-manager switch` writes two profile generations and the
  intermediate one drops every `home.packages` binary for ~1s, so anything invoked by BARE
  COMMAND NAME during a switch dies "command not found" — and it surfaces as a phantom defect
  in someone ELSE's branch. Check for live gate runs before switching; `pgrep -f run-tests.sh`
  matches your own shell, so resolve PIDs and read `/proc/<pid>/cmdline`.
- **Not verified, carried forward:** nobody has re-measured the original three-way probe
  (that a `timeouts.tasks` expiry SKIPS `finally`) against the live cluster. Tekton's
  documented design arguably says the opposite. That premise now sits under a merged test in
  homelab-infra #398. The guard is conservative either way — if the probe is wrong the guard
  is unnecessary, not harmful.

## How to verify
```bash
# the GIT_DIR root cause is pinned on main (content, not ancestry)
git -C ~/workspace/devrc show origin/main:scripts/tests/test_git_repo_isolation.py \
  | grep -c 'def test_git_exports_GIT_DIR_to_pre_push_from_a_worktree_but_not_a_main_checkout'   # want 1

# the canary actually discriminates (both arms), inside the devshell
nix develop ~/workspace/devrc --command python3 -m pytest \
  ~/workspace/devrc/scripts/tests/test_git_repo_isolation.py \
  ~/workspace/devrc/scripts/tests/test_runtime_shebangs.py -q          # want: 122 passed

# the timeout invariant is pinned in homelab-infra (six pipelines, margins printed)
cd ~/workspace/homelab-talos && env CDPATH= nix-shell -p bash kustomize kubeconform \
  findutils gitleaks kubernetes-helm git "(python3.withPackages(ps: [ps.pyyaml]))" \
  --run "bash scripts/tests/run-ci-suite.sh"        # want: RESULT: pass, tests_ran>=187

# 🔴 the gating tier — READ THE LOG, never the exit code
nix build ~/workspace/devrc#checks.x86_64-linux.pytests --no-link
nix log $(nix path-info --derivation ~/workspace/devrc#checks.x86_64-linux.pytests) \
  | grep -E 'RESULT:|TOTAL collected'               # want: RESULT: PASS (exit=0)

# hosts
bash ~/workspace/devrc/scripts/drift-check.sh       # want rc 0, both hosts clean
```
