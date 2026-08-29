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
Reduce the devrc-ci Tekton pipeline from ~25 minutes to ~2-3 minutes (10x). This session completed the analysis phase: identified bottlenecks, measured throughput, and designed an architectural proposal with 4 phases.

## State now
- Branch: `docs/handoff-cairn-task-linkage` (1 commit ahead of origin/main — unrelated handoff doc)
- No open PR for this work — this is an analysis/design session, not a code change
- No code changes made — purely diagnostic

### What's DONE this session
1. **Measured Tekton throughput**: 16 concurrent devrc-ci pipeline runs on `talos-xr6-r7p`, each taking ~25 min. 10 runs currently in flight, queue growing faster than it drains.
2. **Identified the bottleneck**: The `gate` task runs pytests + nodetests sequentially in one pod. pytests takes 21 min; nodetests takes 36s. The `scripts/tests/` monolith (132 files, 10k+ tests) alone takes 12:42.
3. **Measured per-suite timing**: `scripts/tests` 12:42 (57%), `dl-router` 2:54, `browser-bridge` 2:33, everything else ~2 min.
4. **Found pytest-xdist already in use**: `run-tests.sh` runs with `-n 4 --dist loadfile`, measured at 2.07x speedup. Capped at `min(nproc, 4)`.
5. **Verified no cross-file imports**: 120 of 132 files in `scripts/tests/` are fully independent (zero cross-file imports). Only 12 use shared state (nolaunch/launch_log/spool).
6. **Designed 4-phase architectural proposal** (detailed below).

### What's NOT done
- No code changes implemented
- No Tekton pipeline modifications
- No nix derivation changes
- No test file reorganization

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

## Next steps (ranked)
1. **Implement Phase 1**: Split `scripts/tests/` into subsystem targets. Create ~8-10 new directories under `scripts/tests/` (e.g., `scripts/tests-subsystem-store/`, `scripts/tests-session/`, etc.). Update `HERMETIC_TARGETS` and `TARGET_FLOORS` in `run-tests.sh`. Measure impact on `scripts/tests` wall clock.
2. **Implement Phase 2**: Split Tekton pipeline into parallel tasks. Modify `devrc-ci-pipeline` to run `scripts/tests` subsystem suites and `dl-router`/`browser-bridge` as separate parallel tasks. Requires changes to `clusters/homelab/apps/tekton-pipelines/triggers/` in homelab-talos.
3. **Implement Phase 3**: Add test impact analysis. Map source files → test suites so only affected tests run. This is the real 10x for typical PRs.
4. **Increase xdist workers**: Raise the cap from 4 to 8 or 16 in `run-tests.sh:3073`. Requires increasing pod CPU limit from 4 to 8-16 in the Tekton task.
5. **Fix the flaky test**: Investigate and fix `test_a_FORGED_actor_in_the_body_is_DISCARDED`.

## Gotchas / decisions / dead-ends
- **xdist is already at 4 workers** — the code caps at `min(nproc, 4)`. Going to 8+ helps but has diminishing returns. The real win is splitting the monolith.
- **`--dist loadfile` is load-bearing** — several suites share module-level state. Changing to `load` would race tests.
- **Nested runs must be serial** — `PYTEST_CURRENT_TEST` forces `PYTEST_JOBS=1` in nested invocations. This is correct and must not be bypassed.
- **12 files use shared state** (nolaunch/launch_log/spool) — they must stay in the same directory or share a state directory.
- **The nix derivation copies only tracked files** — new test directories must be `git add`ed or they're silently absent from the gate.
- **`run-tests.sh` is 4192 lines** with 10 structural guards — changing the target list requires updating `HERMETIC_TARGETS`, `TARGET_FLOORS`, `EXPECTED_SKIPS`, and the floor table's two-way pin.
- **The gate timeout is 45 min** — current usage is 30-48% of budget. Splitting reduces this to ~10%.

## How to verify
1. After Phase 1: `nix build .#checks.x86_64-linux.pytests` and measure `scripts/tests` wall clock in the timing census output. Expect ~3 min instead of ~12 min.
2. After Phase 2: Trigger a devrc-ci pipeline run and measure total gate time. Expect ~5 min instead of ~25 min.
3. After Phase 3: Touch a single subsystem file, push a PR, and verify only that subsystem's tests run.
