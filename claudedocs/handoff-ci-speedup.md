---
---
# Handoff: CI pipeline speedup analysis — 2026-08-29

## Run this first — the index, one read-only command
```bash
python3 ~/workspace/devrc/scripts/lib/subsystem_recall.py --repo ~/workspace/devrc
```
Terse pointers this doc does not carry, curated by past sessions and outliving it.
🔴 RECALL, NOT LIVE OBSERVATION — every line is a pointer to VERIFY, never a current
reading, and it may describe a gotcha already fixed. `scope-absent`/`scope-empty` means
nothing is recorded yet: ordinary, not an error, and not a clean bill of health.
Non-blocking: if it exits non-zero, print the stderr line and carry on.

## Goal
Cut the devrc-ci gate from ~25 min. This session **refuted the previous handoff's diagnosis**, spent the cluster levers, reverted them, and shipped the first devrc-side mechanism.

## State now
- **PR #1073** `feat/ci-impact-analysis` — `--targets` subset selection for `run-tests.sh`. Head `bf4e904f`, both Tekton checks SUCCESS at that sha. **A fix round for the round-1 audit is UNCOMMITTED in the worktree `/home/zach/workspace/devrc-ci-impact`** (+413/-13 across `scripts/run-tests.sh` and `scripts/tests/test_run_tests_targets.py`).
- **homelab-infra `trunk`** — three commits landed and two were reverted. Net config delta for the night: the devrc-ci gate budget 45m → 60m (`29ccfd69`) and nothing else.
- `claim-work ci-speedup-1` is HELD by this session. Release it or take it over.

### What's DONE
1. **`23887675`** `requests.cpu` 2→4 (equality with the limit) — **reverted by `bb62668f`**.
2. **`29ccfd69`** gate budget 45m→60m, `tasks` 55→70m, `pipeline` 1h10m→1h25m — **KEPT**.
3. **`6bec075e`** per-node hostPath /nix cache + node unpin — **reverted by `7839ef54`**.
4. **PR #1073** opened; round-1 adversarial audit run and all 10 findings fixed (uncommitted).
5. **PR #1041 closed** as superseded (another session closed it concurrently; I added the measurement it lacked).

### IN FLIGHT
- Dev-host + sandbox tier run of the fix round (`scratchpad/full6.log`, `sandbox3.log`). Sandbox passed on the *pre-F9* tree: 18841 collected / 0 failed.
- **Not yet done on #1073**: commit the fix round, push, post the round-1 `audit-claims` block with `--audited bf4e904f`, dispatch the **blind** delta re-audit.

## Open investigations — live diagnosis state

### Why does the gate take 25 minutes?
- **Symptom**: Each devrc-ci pipeline run takes ~25 min wall clock. Queue of 10+ runs growing.
- **Observed**: Gate task steps timing from `devrc-ci-4875k-gate`:
  - clone: 35s
  - seed-nix: <1s
  - pytests: 21m 10s (nix build + test execution)
  - nodetests: 36s
  - verdict: <1s
- **pytests breakdown** (28 separate pytest invocations):
  - `scripts/tests` (10,032 tests): 12m 42s → ~3:29 with xdist-4
  - `scripts/dl-router/tests` (1,005 tests): 2m 54s (does NOT scale with xdist)
  - `scripts/browser-bridge/tests` (836 tests): 2m 33s (does NOT scale with xdist)
  - 25 other dirs (~4,700 tests): ~2 min combined
- **Ruled out**: Node resource exhaustion (talos-xr6-r7p at 27% CPU, 18% memory). The constraint is the sequential pipeline structure, not node capacity.
- **Leading hypothesis**: The architecture is serial-by-design — one task, one pod, sequential steps. The fix is parallel execution across pods.
- **Next probe**: Implement Phase 1 (split `scripts/tests/` into subsystem targets) and measure the impact.

### Why does `scripts/tests` take 12:42?
- **Symptom**: 10,032 tests in one pytest invocation taking 12:42 serial, ~3:29 with xdist-4.
- **Observed**: `test_subsystem_store_api.py` (13,935 lines, 621 test classes/methods) is the biggest file. The top3 files by size: `test_subsystem_store_api.py` (13,935), `test_subsystem_touch.py` (12,115), `test_session_manager.py` (11,665).
- **Ruled out**: Cross-file dependencies — verified zero imports between test files.
- **Leading hypothesis**: The monolith runs as one pytest invocation, limiting xdist's effectiveness. Splitting into independent subsystem suites would allow parallel execution across pods.
- **Next probe**: Split the 132 files into ~8-10 subsystem directories and add them as separate targets in `run-tests.sh`.

### Flaky test: `test_a_FORGED_actor_in_the_body_is_DISCARDED`
- **Symptom**: Same test failing across multiple PRs (#1026, #1025, and in the batch of 4 failed devrc-ci runs).
- **Observed**: `TestTheActorComesFromTheTOKEN.test_a_FORGED_actor_in_the_body_is_DISCARDED[record0-kkkkkkkkkkkkkkkkkkkkLLLLLLLLLLLLLLLLLLLLmmmmmmmm-kelp-forest-zach]` in `test_subsystem_store_api.py`.
- **Ruled out**: Nothing yet — this is a flaky test, not a code defect.
- **Leading hypothesis**: Timing-dependent or state-dependent flake in the token/actor validation logic.
- **Next probe**: Run the test in isolation with `--count=100` to measure flake rate.

### Is the starvation flake actually gone? UNRESOLVED — n too small
- **Symptom:** `test_subsystem_store_api.py` fails `TimeoutError` out of `socket.py` on a **loopback** HTTP round-trip.
- **Observed:** the wait that times out is `urlopen(req, timeout=HANG_TIMEOUT)` with `HANG_TIMEOUT = 60.0`. Runs whose trees already contain that 60s bound STILL timed out — `devrc-ci-4875k`, `-xnzd8`, `-69gz8` (verified by `git show <rev>:scripts/tests/test_subsystem_store_api.py | grep 'HANG_TIMEOUT = 60.0'`). A localhost call starved 60s is a capacity fact.
- **Failure mix, 19 readable failed runs:** 11 store-api starvation family, 8 genuine per-PR defects.
- **Ruled out:** raising the timeout (15→60 already landed via #1023/#1033 and did not fix it).
- **Leading hypothesis:** node saturation. `talos-xr6-r7p` measured **93% CPU / 90% of allocatable requested** while the previous handoff asserted 27% and "ruled out" node exhaustion.
- **Next probe:** classify 20+ consecutive post-revert runs by `TaskRunCancelled` / `TaskRunTimeout` / `StepFailed`, and for `StepFailed` pull `pytests verdict=` to separate the store-api family from real defects.

### The node pin is the throughput ceiling — retry is BLOCKED on nix store ownership
- **Symptom:** devrc-ci was pinned to one node by the RWO node-local `nix-store-cache` PVC.
- **Observed, unpinned:** queue wait **17.2m / 22.5m → 0.1m**, wall clock **39.1m median → 17.4m**, `scripts/tests` 728s → 417s, three nodes reachable instead of one. Cold `seed-nix` 513s once per node, then ~1.7 min warm.
- **Why it was reverted:** a `DirectoryOrCreate` hostPath is created root-owned, so every test shelling out to nix died with `error: opening lock file "/nix/var/nix/db/big-lock": Permission denied` — **75 occurrences, 42 tests, on every PR**. Control: PVC-era runs `2ln72`/`m68vc` had **0** such errors; hostPath-era `csfzb`/`jqx2b` had **75** each.
- **Ruled out:** that it was pre-existing — `test_git_autostash_disabled.py` was added 08-02 and the pre-change run `q4d5m` reported `failed=0`.
- **Leading hypothesis:** the PVC's `/nix` carried usable ownership from prior runs; a fresh hostPath does not.
- **Next probe:** on a SCRATCH pipeline, not devrc-ci — hostPath + a `chown`/`chmod` in `seed-nix`, then assert `nix-instantiate --eval -E '1+1'` succeeds *as the sandbox build user* before touching the real gate.

### Free cluster capacity swings ~6x within 30 min
- **Observed:** free CPU by request, two readings 30 min apart — `uvh-gtj` 1.9 → 11.2, `xr6-r7p` 1.5 → 0.4, `jkj-deb` 5.0 → 3.6.
- **Consequence:** no static node choice stays correct. Also `talos-jkj-deb` is a control-plane node **and is untainted**.
- **Next probe:** sample free-CPU-by-request every 5 min for an hour before sizing anything on it.

## Next steps (ranked)
1. **Finish PR #1073** (`/home/zach/workspace/devrc-ci-impact`): commit the uncommitted fix round, push, post the round-1 claims block with `--audited bf4e904f`, then dispatch a **BLIND** delta re-audit. The fix round changed ~145 payload lines, so the ladder is live.
2. **Decompose `scripts/tests`** (devrc) — separate the repo-wide scanners from subsystem-specific tests. This is the gate on everything else: a path→target mapping is worth **~1.7x** alone but **~3.6x** after. Classifier draft measured 32 of 139 files as repo-wide, but see Gotchas — that number is not trustworthy.
3. **Path→target mapping** (devrc, `scripts/lib/`) — only after 2. Must be fail-safe (unknown path ⇒ run everything) and two-way pinned like `TARGET_FLOORS`.
4. **Decide `tekton-ci` PodSecurity** (homelab-infra) — `686d6ff0` set `pod-security.kubernetes.io/enforce: privileged` solely to admit the hostPath that is now reverted. It buys nothing today. Another session's commit; ask before unwinding.
5. **Retry the unpin** only with the nix-store-ownership probe above green on a scratch pipeline first.

## Gotchas / decisions / dead-ends
- **xdist is already at 4 workers** — the code caps at `min(nproc, 4)`. Going to 8+ helps but has diminishing returns. The real win is splitting the monolith.
- **`--dist loadfile` is load-bearing** — several suites share module-level state. Changing to `load` would race tests.
- **Nested runs must be serial** — `PYTEST_CURRENT_TEST` forces `PYTEST_JOBS=1` in nested invocations. This is correct and must not be bypassed.
- **12 files use shared state** (nolaunch/launch_log/spool) — they must stay in the same directory or share a state directory.
- **The nix derivation copies only tracked files** — new test directories must be `git add`ed or they're silently absent from the gate.
- **`run-tests.sh` is 4192 lines** with 10 structural guards — changing the target list requires updating `HERMETIC_TARGETS`, `TARGET_FLOORS`, `EXPECTED_SKIPS`, and the floor table's two-way pin.
- **The gate timeout is 45 min** — current usage is 30-48% of budget. Splitting reduces this to ~10%.

- 🔴 **The previous handoff's ranked #1 and #4 are BOTH wrong — do not re-derive them.** #1 (split `scripts/tests` into subsystem dirs) is neutral-to-negative: the suite is already within 20% of its `-n4` floor (1367s of work, 342s ideal, 417s actual). #4 (raise xdist 4→16) is worse: one file is 316s of 1367s, so `-n8` is floor-bound on it. Its per-suite numbers were the SERIAL figures `run-tests.sh:2997` explicitly flags "⚠ do not quote as current".
- 🔴 **`nix build .#checks…pytests` builds from `cp -r ${./.}` with NO `.git`** — impact analysis CANNOT be computed inside the gated tier. It must be computed in the Tekton clone step and written into the tree before the build.
- 🔴 **The `source` workspace PVC cannot become an `emptyDir`** — `devrc-ci-report` reads `verdict-*`/`detail-*`/`log-*` files the gate wrote there, so every run would post `COULD NOT RUN: no verdict was written`.
- 🔴 **`test_conditional_skip_pins.py` extracts blocks from `run-tests.sh` by UNIQUE TEXT ANCHORS** and asserts each occurs once. A comment quoting one verbatim took 11 tests red. It also runs `_skip_entry_applies` standalone under `bash -u`, so bare `$ONLY_TARGETS` aborts exit 127 — hence `${ONLY_TARGETS:-}`.
- 🔴 **The `RESULT:` line is a machine contract** — `test_gate_exit_truthfulness.py` matches `^RESULT: (PASS|FAIL) \(exit=(\d+)\)$`, anchored BOTH ends. Do not append to it. The subset note goes in the SUMMARY banner, which is where `gate.sh` starts reading.
- 🔴 **The repo-wide-scanner classifier is NOT trustworthy.** A regex for `git ls-files`/`REPO_ROOT.rglob` said 32 of 139 files (394s, 29%); positive and negative controls passed, but it over-classifies — `test_drift_check.py` (139s) lands there because it *uses* `git ls-files`, not because an unrelated file breaks it. The true always-run set is between ~8s and 394s and needs **empirical** measurement (change an unrelated file, see what fails), not another regex.
- **Estimate history, so it is not re-derived optimistically:** 3x → 1.7x → uncertain. Three revisions is why step 2 must be measured before step 3 is built.
- **Cost of the night, stated:** 13 devrc-ci runs broken by my changes — 10 `PodAdmissionFailed` (hostPath vs a cluster-wide PodSecurity default I inferred absent from missing NAMESPACE labels) and 3 `TaskRunTimeout` from the concurrency the CPU-request change gave up.
- **Pattern worth not repeating:** three times I used CI as the test environment for a change to CI. A scratch pipeline would have caught each in minutes.
- **CARRIED FORWARD from the replaced `State now`, because it is a measurement and not status:** 120 of 132 files in `scripts/tests/` have **zero cross-file imports**; only 12 share state (nolaunch / launch_log / spool). Confirmed independently for the biggest file — `test_subsystem_store_api.py` has **no session- or module-scoped fixtures** (`store` is `tmp_path`-based), so splitting it is safe. It is just not *useful* until worker count rises (see the `-n4` floor above).

## How to verify
1. **PR #1073, both tiers** — dev-host `nix develop ~/workspace/devrc -c bash <worktree>/scripts/run-tests.sh <worktree>`; sandbox `nix build <worktree>#checks.x86_64-linux.pytests`. Expect `RESULT: PASS`, 0 failed. Name the tier in any claim.
2. **The subset mechanism is honest:** `nix develop ~/workspace/devrc -c bash <worktree>/scripts/gate.sh --tier pytest` with `DEVRC_TARGETS=scripts/collector/i3/tests` exported — the SUMMARY region gate.sh prints must say `PARTIAL RUN`. Before the fix it read `SUMMARY (hermetic set)` + `GATE: RESULT=PASS` over 13 tests.
3. **Cluster is back to baseline:** `KUBECONFIG=$KC_HOMELAB kubectl get task devrc-ci-gate -n tekton-ci -o jsonpath='{.spec.volumes}'` must show `persistentVolumeClaim: nix-store-cache`, and `…steps[?(@.name=="pytests")].computeResources.requests.cpu` must be `2`.
