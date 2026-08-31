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
- **Ranks 1 and 2 are merged; rank 1's measurement is DONE this session** and its answer is below. No code change has landed for it yet.
- **PR #1073** `feat/ci-impact-analysis` — `--targets` subset selection. **MERGED `809486fa`**, verified by content. Four audit rounds.
- **PR #1081** — prior handoff update. **MERGED `a6554bf2`**.
- **PR #1120** `feat/measure-test-readsets` — the read-set measurement. **MERGED `90deea1d`**, verified by content. Five audit rounds.
- **PR #1152** — previous session's close-out doc. **MERGED `9a7c4338`**, verified by content.
- **PR #1154** `docs/handoff-ci-speedup-rank1` — **OPEN**, commit `71b7e549`. Carries the two-file adjudication. 🔴 **Its body states the convicting rule as "a repo path in argv ⇒ ALWAYS-RUN", which is measurably TOO BROAD** — see the correction in Gotchas. Fix the body or the next reader builds the wrong rule.
- **Cluster: baseline**, except the devrc-ci gate budget 45m→60m (`29ccfd69`), deliberately kept.
- **Claim `ci-speedup-1` is HELD.**
- **Open issues:** #1094, #1123, #1124.
- **No `clawgate-task:` field**: `clawgate_handoff.sh resolve` returned **rc 5, nothing resolved**. Positive control confirms the board is reachable (11 links for another session), but an unknown id also answers 200 with an empty array — not a clean bill of health.

### Rank-1 re-measurement, 2026-08-31 — the tree has MOVED since rank 2
Full trace, suite **green: 10987 passed in 638.53s**, `PYTEST_RC=0`.

| | rank 2 (published) | rank 2 seconds (timed subset) | **this run** |
|---|---|---|---|
| measured test files | 144 | 133 timed / 1293.3s total | **149** |
| ALWAYS-RUN | 28 | 28 timed · **193.6s · 15.0%** | **28** |
| OPAQUE | 75 | 74 timed · **1078.3s · 83.4%** | **78** |
| scoped | 41 | 31 timed · **21.4s · 1.7%** | **43** |

**Rank 2's ceiling stands: 1.02x — 98.3% of the suite must run on any change.** Full write-up,
blind spots and the ten-entry defect ledger: `claudedocs/measurement-scripts-tests-readsets.md`.

🔴 **The two runs are NOT comparable file-for-file** — five test files were added to `scripts/tests` between them. Do not read `75 → 78` as three files becoming opaque.
🔴 **THIS RUN HAS NO TIMINGS.** The seconds column is rank 2's, carried forward; this session ran no
`--durations` pass, so **no new ceiling number exists** and none should be quoted from these counts.

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

### browser-bridge test_server.py: spool rows leak across tests under xdist (issue #1124)
- **Symptom + exact repro:** intermittent. `scripts/browser-bridge/tests/test_server.py::test_instances_and_poll_still_do_not_emit` (line 2452) and `::test_an_absent_origin_header_is_not_the_same_as_an_empty_one` (line 3636), both on worker `gw0`, in the **sandbox tier only** (`nix build .#checks.x86_64-linux.pytests`). Not reproducible on demand.
- **Observed (with values):**
  - `AssertionError: assert [{'source': 'browser-bridge', 'kind': 'cmd', 'text': 'x.test', 'duration_ms': '1', ...}] == []` — a row belonging to a *different* test found in this test's own `tmp_path` spool dir.
  - `KeyError: 'session'` — the second test unpacked a row it did not cause.
  - Control, unmodified `origin/main`, same tier: **PASS 890/890.**
  - Reproduction on the same tree that failed: **PASS 890/890.** So 2 of 3 observations green.
  - Wall time is NOT the discriminator: browser-bridge took **388.6s on main vs 396.6s on the failing tree** — within 2%, so this is not load-inflation of one test.
- **Ruled out:** load/saturation (per the 2% wall-time delta above, and every other target ran *faster* in the sandbox tier that day); PR #1073 causing it (the failing file is untouched by it).
- **Leading hypothesis:** a spool-row selection race. This exact family has been patched **three times** — #549, #891, #1074 (`the origin-token test asserted an ORDER the spool never promised`) — and #891/#1074 both targeted the second failing test specifically.
- **Next probe:** run this file ≥50 times in the sandbox tier and count cross-test rows, to get a flake RATE. A single green run is exactly what the three prior fixes had.

### test_git_repo_isolation.py: the co-tenant probe counts an unreaped git child (issue #1123)
- **Symptom + exact repro:** `test_live_cotenants_does_not_count_this_process`, sandbox tier. Passes 3/3 in isolation.
- **Observed (with values):** `AssertionError: the probe counted our own process (or an ancestor) as a co-tenant` / `assert ['103583:git'] == []`. The test builds a fixture repo with git subprocesses, chdirs in, then asserts `live_cotenants([git_dir]) == []` — and caught a `git` child of its own fixture setup.
- **Ruled out:** attribution to PR #1120 — unmodified `origin/main` fails the same tier the same day (on a *different* test), and #1120 touches none of these files.
- **Leading hypothesis:** the probe counts **zombies**. Same class as the documented `test_timeout_reaps_the_whole_process_group` case in `CLAUDE.md`: `os.kill(pid, 0)` succeeds on a zombie and a build container's PID 1 does not reap.
- **Next probe:** read `/proc/<pid>/stat` state in `live_cotenants` and confirm state `Z` for the offending pid during a failing run.

### Is the local sandbox tier trustworthy on the workbench? UNRESOLVED
- **Symptom:** `nix build .#checks.x86_64-linux.pytests` failed on three consecutive runs on 2026-08-30, on **two different tests**, including on unmodified `origin/main`.
- **Observed (with values):** `error: SQLite database '/build/home/.local/share/nix/root/nix/var/nix/db/db.sqlite' is busy`, surfacing as `AssertionError: nix cannot evaluate /build/src/nix/home.nix` in `TestSkillDocsArePinned::test_the_pinned_docs_are_the_DEPLOYED_ones`. Byte-identical on `origin/main` and on a feature branch. Later the same night the same command ran **green** twice.
- **Ruled out:** any specific PR — the control on unmodified `origin/main` is red.
- **Leading hypothesis:** nix store contention from *other* concurrent users on the box. `CLAUDE.md` documents this symptom for one operator building two derivations at once; these runs were sequential, so the contention is external.
- **Next probe:** before believing any local sandbox RED, re-run it against unmodified `origin/main` in the same window. Tekton is the authority; the local tier is not.

### Rank 1: are the two largest OPAQUE files legible, and does that RAISE the ceiling?
- **Symptom + exact repro:** 75 files (1078.3s, 83.4% of suite time) classify OPAQUE because they spawn a child at a repo cwd whose reads the per-interpreter audit hook cannot see. The previous doc names `test_subsystem_store_api.py` and `test_run_tests_floors.py` as the two largest contributors and asks for them to be made legible. Reproduce the per-file exec set with:
  ```bash
  DEVRC_READSET_OUT=<scratch>/rs PYTHONPATH=$DEVRC/scripts \
    nix develop $DEVRC -c python3 -m pytest \
    scripts/tests/test_subsystem_store_api.py scripts/tests/test_run_tests_floors.py \
    -q -p no:cacheprovider -p testlib.nolaunch_plugin -p testlib.spool_plugin \
    -p testlib.gitenv_plugin -p testlib.nogit_plugin -p testlib.readset_plugin \
    -n 4 --dist loadfile
  ```
- **Observed (with values) — BOTH are adjudicable from the recorded argv alone, no child tracer required:**
  - `test_run_tests_floors.py` — 18 bash execs + 1 git. Ten of them are
    `bash /tmp/.../popen-gw2/test_*/run-tests.sh /home/zach/workspace/devrc` —
    **REPO_ROOT is the literal operand**, and `run-tests.sh`'s positional IS a repo
    root. One more is `bash /home/zach/workspace/devrc/scripts/run-tests.sh
    --check-floors`, the real runner at repo root. ⇒ **ALWAYS-RUN, proven.** Not opaque.
  - `test_subsystem_store_api.py` — 45 bash, 23 python3, 3 cp, 1 each seed.sh/git/rm/sort.
    **Every single operand is under `/tmp`**: `seed.sh --store /tmp/…`,
    `seed.sh --stage /tmp/…`, `verify-byte-identity.sh --store|--url`,
    `python3 …/server.py --store /tmp/…`, `cp -a /tmp/…`,
    `python3 -c open('/tmp/…')`, `git ls-remote https://devrc-nogit-guard.invalid/…`.
    The **only** repo paths anywhere in its exec set are the child *executables*:
    `scripts/subsystem-store-api/{seed.sh,verify-byte-identity.sh,server.py}`.
    ⇒ its inputs are outside the repo; its repo dependency is those scripts' own code.
- **Ruled out:**
  - *A universal (any-runtime) child tracer, as a dependency-free option* — **`strace` is not in the flake devShell** (`nix develop $DEVRC -c command -v strace` → not found). Using it would mean adding it to the devShell **and** the check derivation, plus ptrace working inside the nix sandbox, which is unmeasured.
  - *That rank 1 necessarily raises the ceiling* — refuted by the first bullet above. `test_run_tests_floors.py` moving OPAQUE→ALWAYS-RUN **lowers** the best case. This is the one-directional drift the measurement doc already predicted ("every correction that fixed a defect moved files OUT of scoped").
- **Leading hypothesis:** the ceiling will not move materially, for a reason that is structural rather than instrumental — even a *perfectly* scoped `test_subsystem_store_api.py` carries trigger prefixes `scripts/subsystem-store-api` and `scripts/lib`, and **this repo is essentially `scripts/`**, so nearly every commit touches them. Skippability requires triggers that ordinary changes MISS, and this corpus has few.
- **Next probe:** finish the full 144-file trace, then compute a **best-case ceiling** — treat every OPAQUE file as perfectly scoped and see what number falls out. Published best case today is `1293.3 / 193.6 = 6.68x`; every file proven ALWAYS-RUN lowers it. **If the best case converges near 1.0x, rank 3 is dead permanently and this effort closes** — that verdict needs no instrument, only adjudication.

### Rank 1 ANSWERED: what is the OPAQUE bucket actually made of?
- **Symptom + exact repro:** 78 files classify OPAQUE — read set UNKNOWN — because they spawn a child at a repo cwd. The question is whether the argv already recorded can settle any of them without building a child tracer. Reproduce with the full-trace command in "How to verify", then split the bucket by what each opaque exec's **data operands** name (excluding `toks[0]` and the script an interpreter runs, both of which are CODE, not a tree read).
- **Observed (with values) — the 78 split three ways:**
  - **A. REPO_ROOT is a DATA operand ⇒ provably ALWAYS-RUN: 9 files.**
    `test_run_tests_floors.py`, `test_run_tests_targets.py`, `test_run_tests_preconditions.py`,
    `test_run_tests_timing.py`, `test_no_real_launchers_all_targets.py`,
    `test_activity_spool_isolation.py`, `test_gate_exit_truthfulness.py`,
    `test_devshell_satisfies_required_tools.py`, `test_opencode_config.py`.
    Spelling, verbatim: `bash …/run-tests.sh /home/zach/workspace/devrc`,
    `bash …/run-node-tests.sh /home/zach/workspace/devrc`, `git -C /home/zach/workspace/devrc`.
  - **B. a DEEPER repo path as a data operand: 0 files.** The rule has exactly one shape here.
  - **C. every data operand outside the repo: 69 files.**
- 🔴 **Ruled out — "bucket C is 69 scoped candidates". IT IS NOT, and publishing it that way would be defect #11 in `measurement-scripts-tests-readsets.md`'s ledger** — the acquitting-operand fallacy reached by a new route. A child's data operands being outside the repo says nothing about what the child's **own code** reads. Disproof from inside bucket C itself, two files, same bucket, opposite reality:

  | file | in-process paths the tracer ALREADY recorded | trigger prefixes |
  |---|---|---|
  | `test_drift_check.py` | **172** | ~all of `scripts/**`, plus `nix/pkgs`, `nix/home.nix`, `claude/skill-tiers.json` |
  | `test_subsystem_store_api.py` | **16** | `scripts/lib`, `scripts/subsystem-store-api` only |

  `test_drift_check.py` is effectively ALWAYS-RUN on its **in-process** reads alone — its child opacity adds nothing. `test_subsystem_store_api.py` is genuinely narrow. Same bucket, and the bucket does not distinguish them.
- **Leading hypothesis:** the existing `triggers` field already discriminates bucket C better than any operand rule can, because it is *measured* rather than inferred from argv. The remaining true unknown is narrower than 83.4% of suite time — it is only those bucket-C files whose in-process trigger set is SMALL **and** whose child is a repo script.
- **Next probe:** intersect the two — list bucket-C files with fewer than ~30 in-process paths **and** at least one `bash <repo script>` child. That is the only population a child tracer could move, and it is far smaller than 69. Size it before building anything.

### Rank 7 step 0 DONE: the unpin's perf baseline, RE-TAKEN at `requests.cpu: 2`
- **Why it had to be re-taken:** `claude/skills/tekton/reference/pipelines.md` → "Retrying the devrc-ci unpin" states the quoted wins (queue 17–22m → 0.1m, wall clock 39.1m → 17.4m) are **not re-derivable** — the runs are pruned, and they were measured while `requests.cpu` was **4** (`23887675`). `bb62668f` put it back to **2**, which is live. So the entire justification for rank 7 rested on a baseline taken under a different configuration.
- **Observed (with values), 2026-08-31, `requests.cpu: 2` live, n=31 gate TaskRuns / 35 retained PipelineRuns:**
  - **Gate pod-start latency (TaskRun `startTime` → first step `startedAt`): p50 101s, p90 748s, max 1043s.** Slowest three: `devrc-ci-grz6m-gate` 1043s, `-qjxwl-gate` 936s, `-v6p74-gate` 864s.
  - Wall clock (start → completion): **median 23.4m, p90 34.2m, max 42.6m**, against a **45m** gate budget.
  - **Every gate pod landed on `talos-xr6-r7p` — 24 of 24.** The pin is intact and is the whole scheduling surface.
  - PipelineRun creation → `startTime` is **median 0.0s, max 1.0s** — 🔴 **that number is NOT the queue wait and must not be quoted as one**; Tekton starts the run immediately and the wait is entirely in pod scheduling, above.
- **Verdict: the upside is REAL and survives the cpu-2 re-take.** ~12.5 minutes at p90 is pure scheduling wait on a 45m budget, caused by pinning four nodes' worth of demand onto one.
- **Next probe:** the ownership question (below) — nothing about the unpin should be attempted until it is answered on a scratch pipeline.

### Rank 7 blocker: WHO takes the nix store lock? — root cannot EACCES a file it owns
- **Symptom:** the reverted hostPath produced `error: opening lock file "/nix/var/nix/db/big-lock": Permission denied`, 75 occurrences across 42 tests, on every devrc PR (`7839ef54`).
- **Observed (with values) — live gate pod `devrc-ci-gjljc-gate-pod`, `step-pytests`, 2026-08-31:**
  - `id` → **`uid=0(root) gid=0(root)`**
  - `build-users-group = nixbld`, `sandbox = true`, `sandbox-fallback = true`
  - **`ls -d /build` → No such file or directory** — so nix is running builds UNSANDBOXED via fallback, live today. This reproduces SKILL.md gotcha 6(b) exactly, on the PVC arm, on a *working* gate.
  - `stat` → `755 root:root /nix` · `755 root:root /nix/var/nix/db` · **`600 root:root /nix/var/nix/db/big-lock`** — i.e. the volume that WORKS carries the same 0600 root:root lock the failure names.
- 🔴 **The sharpened question, which the reference does not yet state:** a **root** client holds `CAP_DAC_OVERRIDE` and cannot receive `EACCES` on a mode-0600 file it owns — demonstrated by the working gate above, which is root against exactly that file. **Therefore whoever hit "Permission denied" during the hostPath window was NOT root.** That makes `7839ef54`'s "ownership the nix build user can use" theory *directionally* right while its mechanism stays unproven, and it converts the probe from "fix the permissions" into a single measurable question: **which uid takes the store lock, and when does it stop being root?**
- **Ruled out:** "the storage kind is the variable" — already refuted in the reference (`nix-store-cache`'s PV is itself a `hostPath` `DirectoryOrCreate` on the same disk). Also **candidate 1 (root-directory mode)**, refuted 2026-08-30; a `chmod 0777` before the seed measures nothing.
- **Next probe:** on a SCRATCH pipeline only — instrument the identity of the lock-taker rather than patching permissions. Run a build that shells out to `nix-instantiate` and have it print `id -u` from **inside** the builder, against (a) the live PVC and (b) a fresh hostPath. If (a) prints 0 and (b) prints a `nixbld` uid, the mechanism is named and the fix follows. 🔴 Never on `devrc-ci`: `enforce_admins: true` with both legs required means a wrong guess blocks every contributor.

### The `privileged` namespace label grants nothing — and that is independent of the volume
- **Observed:** `kubectl get ns tekton-ci -o jsonpath='{.metadata.labels}'` → `pod-security.kubernetes.io/enforce: privileged` (still `686d6ff0`). But the gate pod requests **no privilege at all**: pod-level `securityContext` is `{}` and all six step containers are empty.
- **Consequence:** PodSecurity *permits*, it does not *grant*. So the label buys nothing **regardless of whether the hostPath ever comes back** — the sharper form of the reference's "buys nothing while the PVC is back". It also explains gotcha 6(b): nix's sandbox cannot engage because the pod never asks for the capability, so it falls back and the `sandbox = true` in `nix config show` is cosmetic.
- **Consequence for ranked item 5:** reverting that label is **not blocked on the unpin**, and re-granting it later would not by itself make a hostPath work.

### Rank 7: the ownership probe RAN. Fresh PVC ≡ fresh hostPath — and the failure did NOT reproduce
- **What was run:** a scratch diagnostic Pod in `tekton-ci`, applied by hand (**not** GitOps, no Flux, no `eventlistener.yaml` edit), `priorityClassName: ci-bulk` (-10000, `preemptionPolicy: Never`) so it could preempt nothing, pinned to `talos-xr6-r7p`. **It never mounted the live `nix-store-cache`** — that PVC is RWO and gate pods on the same node can mount it concurrently, so writing to it would be testing in production. Two arms instead: a fresh 10Gi `local-path` PVC and a fresh `hostPath` `DirectoryOrCreate` at `/var/lib/mnt/disk-1/nixlock-probe-hostpath`, **both seeded by the same `cp -a /nix/. <vol>/` the gate's `seed-nix` step uses**, so the seed is not a variable. Manifest: `scratchpad/nixlock-probe2.yaml`.
- **Observed (with values) — the two arms are INDISTINGUISHABLE on every dimension measured:**

  | measurement | PVC arm | hostPath arm |
  |---|---|---|
  | `stat` /nix · /nix/var/nix/db · /nix/store | `755 root:root` | **identical** |
  | `stat` /nix/var/nix/db/big-lock | **`600 root:root`** | **identical** |
  | `build-users-group` | `nixbld` | identical |
  | `sandbox` / `sandbox-fallback` | `false` / `true` | identical |
  | `/build` exists? | **no** — unsandboxed fallback | identical |
  | ROOT client `nix-instantiate` (positive control) | **`ok`** | **`ok`** |
  | builder can read big-lock | `no` | `no` |
  | `nix-build` of a trivial derivation | `ok` | `ok` |

- 🔴 **THE FAILURE DID NOT REPRODUCE ON EITHER ARM.** That is the result, and it is a negative one: **a freshly-seeded hostPath is byte-identical in ownership and mode to a freshly-seeded PVC**, which extends the reference's already-REFUTED candidate 1 from "the volume ROOT's mode" to *the whole tree*. So neither the volume KIND nor fresh-seed ownership can be the variable, and `7839ef54`'s stated cause is now **less** supported than before, not more.
- 🔴 **TWO THINGS WERE NOT MEASURED — do not fold either into the clean result.**
  - **The unprivileged-client test never ran.** `nixos/nix:2.24.15` ships **no** `su`, `setpriv`, `runuser` or `doas`, so `NIXBLD_CLIENT=NOT_MEASURED` on both arms. The single most direct test of "can a non-root nix client take this lock" is still outstanding.
  - **The builder's uid came back EMPTY** (`BUILDER_UID=`): the build environment's `/bin/sh` is a static busybox with no `id`, so measurement B proved the build succeeds but never identified who ran it.
- **Ruled out (this session):** volume kind; fresh-seed ownership/mode across the whole tree; "the PVC works only because earlier runs populated it" **as far as a fresh volume goes** — a fresh PVC works too, so accumulated history is not what makes the PVC usable.
- **Leading hypothesis, updated:** the variable is not the volume at all. Candidates now: (a) the real incident's hostPath was shared by **concurrent** gate pods on one node while the PVC is effectively serialised; (b) the failing population is specifically **devrc's own tests that shell out to `nix-instantiate` from inside a build**, which a trivial derivation does not exercise; (c) something the gate's `step-capture-etc` does to `/etc/nix` (see the gotcha below).
- **Next probe:** run **devrc's actual nested-nix tests** against both arms, not a synthetic derivation — that is the only population the incident names. Build the arm image with `util-linux` (for `setpriv`) and `coreutils` so both blind spots above close. Still a scratch pod; still never `devrc-ci`.

### Rank 7 SOLVED (mechanism), and `7839ef54`'s hostPath diagnosis is REFUTED
- **The mechanism, reproduced with the exact production error string:**
  **any nix invocation from inside a nix build — i.e. running as an unprivileged
  `nixbld` uid — is denied by the `0600 root:root` store lock.**
  ```
  BUILDER_Uid:  30001 30001 30001 30001
  error: opening lock file '/nix/var/nix/db/big-lock': Permission denied
  ```
  That is byte-identical to the 75 occurrences across 42 tests in `7839ef54`.
- **The full 2×2, measured on a freshly-seeded `local-path` PVC (probe 5):**

  | | pure `--eval -E '1+1'` | instantiate a derivation |
  |---|---|---|
  | **root** | ok | ok |
  | **builder (uid 30001)** | **DENIED** | **DENIED** |

  🔴 **Even PURE EVALUATION is denied.** nix opens the store DB lock at startup
  regardless of the operation, so this is not write-dependent and no
  "read-only nix is fine" carve-out exists.
- 🔴 **THREE THEORIES DIED HERE, TWO OF THEM MINE:**
  1. **The hostPath-specific diagnosis (`7839ef54`) is REFUTED.** The failure
     reproduces **identically on a fresh `local-path` PVC** — the very volume
     kind that is live and green today. Probes 3 and 4 ran both arms side by
     side and every field matched. **The volume kind is not the variable, and
     the unpin was reverted for a reason that does not hold.**
  2. **"The PVC works because earlier runs populated it with usable ownership"
     — REFUTED.** A *fresh* PVC fails exactly the same way, so accumulated
     history is not what makes the live PVC usable.
  3. **My own store-warmth hypothesis — REFUTED by my own A/B, ~30 minutes
     after I formed it.** Probe 4 had root pre-instantiate the EXACT derivation
     the builder would then ask for; the builder was denied anyway
     (`A_PREWARMED=DENIED`). Warmth is not the variable either.
- **Ruled out earlier this session:** volume kind; fresh-seed ownership/mode
  across the whole tree (both arms `755 root:root` throughout, `600 root:root`
  big-lock); root-directory mode (already refuted upstream).
- 🔴 **THE QUESTION HAS FLIPPED, AND THIS IS THE ONE TO CARRY FORWARD.** It is no
  longer *"why did the hostPath break it"*. It is:
  **why is the gate GREEN today at all?** The live `nix-store-cache` was measured
  in a running gate pod carrying the **same** `600 root:root` big-lock, and
  `scripts/tests/test_clawgatectl_version.py` documents that it *evaluates*
  `clawgatectl.nix` with `nix-instantiate` and **FAILS rather than skips** when
  the binary is absent. By the 2×2 above, that test cannot pass as a builder.
  So one of these must be true and none is yet measured:
  (a) the sandbox tier does not run those tests as a build user;
  (b) they are skipped in that tier;
  (c) the gate's `step-capture-etc` changes something that lifts the restriction.
- **Next probe:** answer (a)/(b)/(c) *without* touching `devrc-ci` — run
  `nix build .#checks.x86_64-linux.pytests` on the workbench and print `id -u`
  plus a `nix-instantiate --eval` probe from inside that build. That is the same
  tier the gate runs, reproducible locally, and it needs no cluster at all.
  ⚠ Build the two check derivations ONE AT A TIME (`CLAUDE.md`: a combined
  invocation produces false failures through store contention).
- **The fix this points at, once (a)/(b)/(c) is answered:** make the store lock
  reachable by the build users in `seed-nix` — `chgrp nixbld` + `chmod 0660`, or
  `chmod 0666`, on `/nix/var/nix/db/big-lock`. It is **volume-independent**, so
  it is not an unpin workaround: it closes a latent fragility that exists on the
  PVC today. 🔴 Prove it on a scratch pod first; `enforce_admins: true` with both
  legs required means a wrong guess blocks every contributor.

## Next steps (ranked)
🔴 **RANKS 1–7 ARE THE PRE-EXISTING NUMBERING AND MUST NOT MOVE** — the rank is half a
claim's identity (`claim-work --slug-for <this doc> <rank>`), so inserting an item
mid-list silently re-points every live claim and lets two sessions claim the SAME work
under different slugs. An earlier revision of this doc inserted a new item at rank 2 and
made the node unpin `ci-speedup-8` here while `main`'s copy still called it
`ci-speedup-7` — measured, both slugs derivable at once. **New items go at the END.**

1. **Land the convicting operand rule in `scripts/lib/readset_classify.py`** — in the NARROW form: *a data operand equal to REPO_ROOT convicts; the executable and an interpreter's script argument do NOT*. Measured effect: **9 files OPAQUE → ALWAYS-RUN, 0 files into `scoped`**. Test coverage must include the over-conviction case (`bash <repo script> --store /tmp/x` must NOT convict). Small cleanup — it makes the classifier honest, it does not make CI faster.
   forcing: none
2. **Measure the browser-bridge flake rate (#1124).** ≥50 sandbox-tier runs of `test_server.py`, counting cross-test spool rows. It can redden a REQUIRED check at random, and `main` has `enforce_admins: true` with no admin override.
   forcing: gate
3. **Fix the zombie-git co-tenant probe (#1123).** Ignore `/proc/<pid>/stat` state `Z` and descendants of the test's own process; regression test driven by a deliberately-unreaped child, watched red first.
   forcing: gate
4. **Close #1094** — two guard-thinness findings in `scripts/tests/test_run_tests_targets.py` from #1073's round-4 audit.
   forcing: none
5. **Decide the `tekton-ci` PodSecurity label** (homelab-infra). `686d6ff0` set `enforce: privileged` solely to admit a hostPath now reverted. Another session's commit — **ask before unwinding.**
   forcing: none
6. **Decide whether to revert the gate budget 45m→60m** (`29ccfd69`, homelab-infra).
   forcing: none
7. **Retry the node unpin — THE ONLY ITEM HERE WITH REAL UPSIDE.** Measured when previously unpinned: queue wait 17.2m/22.5m → 0.1m, wall clock **39.1m median → 17.4m**, `scripts/tests` 728s → 417s. Reverted because a `DirectoryOrCreate` hostPath is created root-owned and every test shelling out to nix died `opening lock file "/nix/var/nix/db/big-lock": Permission denied` (75 occurrences, 42 tests, every PR). 🔴 **ONLY behind the nix-store-ownership probe, on a SCRATCH pipeline, NEVER devrc-ci** — three times this effort used CI as the test environment for a change to CI. Probe: hostPath + a `chown`/`chmod` in `seed-nix`, then assert `nix-instantiate --eval -E '1+1'` succeeds **as the sandbox build user** before touching the real gate.
   forcing: none
8. **Size the population a child tracer could actually move** — bucket-C files with a small in-process trigger set AND a `bash <repo script>` child. That is the only population a tracer could move, and it is far smaller than 69. Size it before building anything.
   forcing: none

🔴 **DO NOT BUILD the path→target mapping (the old rank 3).** Unchanged, and rank 1 did not rescue it: the 9 newly-convicted files move time INTO always-run, and bucket C is not the skippable pool it superficially looks like. **The gate's remaining upside is item 7 — pipeline/node parallelism — not test selection.**

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

- **CARRIED FORWARD from the replaced `State now` — the homelab-infra cluster ledger, because these are durable shas and not status.** Three commits landed on `trunk` and two were reverted: `23887675` (`requests.cpu` 2→4, equality with the limit) **reverted by `bb62668f`**; `6bec075e` (per-node hostPath `/nix` cache + node unpin) **reverted by `7839ef54`**; `29ccfd69` (gate budget 45m→60m, `tasks` 55→70m, `pipeline` 1h10m→1h25m) **KEPT**. Net config delta of that night is `29ccfd69` and nothing else. Next-step 7 is a retry of the `6bec075e` half. ⚠ The handoff tool did NOT flag these as a durable drop — they were found by reading the REPLACE diff by hand.
- 🔴 **The rank-2 estimate went 3x → 1.7x → uncertain → 1.49x → 1.08x → 1.01x → 1.02x**, and the three corrections that fixed a DEFECT all moved files OUT of `scoped`. A read-tracer under-records by nature, so anything it cannot see reads as "bounded" and the tool is **optimistic by construction**. Treat any number it produces as an UPPER bound on skippability until someone has tried to break it again.
- 🔴 **The measurement's classifier reproduced the bug it was built to fix TEN times**, always the same class: reading an option-shaped or path-shaped token out of an argv string as evidence about scope, always failing toward under-classification. Full ledger in `claudedocs/measurement-scripts-tests-readsets.md`. If you extend that classifier, assume you will do it again and mutation-sweep for it.
- 🔴 **Three of five audit fix rounds on #1120 introduced their own regression.** One (REPO_ROOT comparing as outside the repo, ALWAYS-RUN silently 22→9) was caught only by **diffing two classification runs against each other** — no test caught it. Budget for several rounds and re-audit the delta each time; that is the rule and it paid off five times here.
- 🔴 **CPython raises NO audit event for `os.stat`/`os.lstat`/`Path.exists()`/`Path.is_dir()`/`os.path.exists`/`Path.resolve()`** (measured on 3.12.14, twice, independently). A tracer that lists `os.stat` as a traced event records nothing while *reading as* coverage.
- **`strict: false` on `main` means a green Tekton check is a claim about the PR's BRANCH, not the merged tree.** Both #1073 and #1120 were gated on a hand-built integration tree before merge. For #1073 this mattered concretely: `main`'s own `ee5b2b7b` had to re-pin `TARGET_FLOORS` for `scripts/tests` 8217→10269 after a merged tree crossed a bound **neither side crossed alone**.
- **`main` moved 6+ times during one session** (`bd1572f3` → … → `2a357c01`). Any merged-tree gate result has a shelf life of minutes; state the base sha you gated against.
- 🔴 **The shared checkout `~/workspace/devrc` gets its branch switched by other sessions mid-work.** Observed twice this session: once found on `docs/handoff-bb-resume-0830`, and a branch cut from it inherited 2 foreign commits. **Run `git branch --show-current` immediately before any commit**, and prefer an isolated worktree.
- **`claim-work` can render a confident verdict from a degraded read.** Observed: `rc 10 ALREADY CLAIMED — DO NOT start this item` with blank `who:`/`where:`, because its `git` had momentarily vanished from `~/.nix-profile`. Re-running gave `rc 12` (mine). Read the `who:`/`where:` fields, not just the rc.
- **`resume-state.sh` cannot see a handoff doc that lives on an unmerged branch.** It reported `handoff-read: working-tree copy (identical to origin/main)` — true, and misleading, while the authoritative doc sat on open PR #1081. It compares tree↔`origin/main` only.
- **A tilde in the `/resume` topic argument does not expand** — `~/workspace/...` resolved nothing and the digest silently fell back to the newest handoff doc (it does flag this as a `!` gap). Pass an absolute path.
- **CARRIED FORWARD, still true:** 120 of 132 files in `scripts/tests/` have zero cross-file imports; only 12 share state (nolaunch / launch_log / spool). `test_subsystem_store_api.py` has no session- or module-scoped fixtures. Splitting is *safe*; it is just not *useful* (see the 1.02x above).

- 🔴 **Rank 1 does not need a child tracer, and building one first would have been wasted work.** Both files the previous doc named were settled by reading the **already-recorded argv and cwd** — the tracer's existing output. The discriminating question is not "what did the child read" but "**does any operand name a path in this repo**". `test_run_tests_floors.py` answers yes (REPO_ROOT is the operand); `test_subsystem_store_api.py` answers no (every operand is `/tmp`). Adjudication before instrumentation.
- 🔴 **A convicting operand rule and an acquitting one are not the same rule with the sign flipped.** The measurement doc's defects 6–10 all came from acquitting on operands — deciding from argv that a command reads *elsewhere*. Convicting (an operand inside the repo ⇒ reads this tree) fails toward ALWAYS-RUN, the safe direction, and is sound even where the acquittal is not: a repo path in argv is positive evidence, whereas its absence was being read as evidence of absence.
- **`strace` is NOT in the flake devShell** — measured `2026-08-31`. So there is no dependency-free tracer that covers `bash` children. A Python-child tracer via `sitecustomize` on `PYTHONPATH` would cover `python3 …` children (and python grandchildren under bash, since env is inherited) but NOT a bash script's own reads. Scoped, not universal — price it accordingly.
- ⚠ **A `sitecustomize` child tracer has a named blind spot before it is written**: a test that builds `env=` from scratch rather than `{**os.environ, …}` drops the propagated variables, so its child goes untraced. That under-credits (safe), and it is detectable — compare the count of python execs the parent recorded against the number of child shards written.
- **The two-file trace ran green** — `PYTEST_RC=0`, shards `rs.gw0..gw3.json`. GUARD 9 (`gitenv`) emitted **observed**-mode reports during it because other sessions were writing `/home/zach/workspace/devrc/.git` concurrently (`refs/remotes/origin/feat/dlrouter-media-accessor-list` moved mid-run). That is attribution being impossible in a shared checkout, not a failure — the prevention half stayed in force.

- 🔴 **I stated the convicting rule too broadly, and the measurement caught it.** "Any repo path in argv ⇒ ALWAYS-RUN" convicts `bash scripts/subsystem-store-api/seed.sh --store /tmp/x`, whose only repo token is **the script being executed**. That is code, not a tree read; the broad rule would convict nearly every file and measure nothing. The rule that survives contact with data is narrow: **a DATA operand equal to REPO_ROOT**, with `toks[0]` and an interpreter's script argument excluded. PR #1154's body carries the broad wording — correct it.
- 🔴 **"Every data operand is outside the repo" is NOT evidence of a bounded read set** — it is the acquitting-operand fallacy (ledger defects 6–10) reached by a third route, and this session nearly published it as "69 scoped candidates". The disproof is inside the bucket: `test_drift_check.py` sits there with **172** in-process paths spanning ~all of `scripts/**`. **A child's operands describe its inputs, never its code's own reads.**
- 🔴 **The plugin truncates argv to the first 6 tokens** (`" ".join(str(a) for a in argv[:6])` in `readset_plugin.py`). An operand in position 7+ is invisible, so the convicting rule **under-convicts**. That direction is safe (a file stays OPAQUE, which consumers already treat as must-run), but it means bucket A is a FLOOR of 9, never an exact count.
- **Rank 1 did not need a child tracer to produce its answer** — both files the previous doc named, and 9 files in total, were settled from argv the tracer already records. Adjudicate before instrumenting.
- **`strace` is NOT in the flake devShell** — measured 2026-08-31. No dependency-free tracer covers `bash` children. A `sitecustomize` tracer would cover `python3` children (and python grandchildren under bash, env being inherited) but not a bash script's own reads.
- ⚠ **A `sitecustomize` child tracer has a named blind spot before it is written:** a test building `env=` from scratch rather than `{**os.environ, …}` drops the propagated variables and its child goes untraced. Under-credits (safe), and detectable — compare python execs recorded against child shards written.
- **GUARD 9 (`gitenv`) fired in observed mode throughout both traces** because other sessions were writing `/home/zach/workspace/devrc/.git` concurrently. Attribution being impossible in a shared checkout, not a failure; the prevention half stayed in force.

- 🔴 **I pushed three times in quick succession to a docs-only PR and put three runs into a shared queue.** Measured while working: **8 `devrc-ci` runs Running/Pending at once**, one gate pod Pending, all on `talos-xr6-r7p`. The `tekton` skill states this plainly — *"PUSHING N BRANCHES IS NOT N INDEPENDENT ACTIONS — IT IS ONE BLAST-RADIUS ACTION … Push, wait for the queue to drain, push"* — and it is not only my own checks that suffer. **Batch handoff updates into one push.**
- 🔴 **`PipelineRun` creation→`startTime` is a decoy metric: median 0.0s.** It reads like "there is no queue" and is the opposite of the truth. The wait lives in TaskRun→pod scheduling (p90 748s). Anyone re-measuring this must use the TaskRun's first-step `startedAt`, not the PipelineRun's `startTime`.
- **The gate's `sandbox = true` is cosmetic today** — `/build` does not exist, so builds run unsandboxed. Read `/build`'s existence, never `nix config show`, exactly as gotcha 6(b) says; this session confirmed it live on a healthy run rather than a broken one.

- 🔴 **v1 of this probe reported `NIXBLD_CLIENT=FAILED` and it was a BROKEN INSTRUMENT, not a finding** — the image has no `su`, so the line read as "the unprivileged client was denied" when nothing had been tested. It was caught only by printing the command's stderr (`su: command not found`) and reading the CONTENT rather than the label. **A probe that reports a denial must prove it could ever have reported success**; v2 carries `ROOT_CLIENT` as an explicit positive control for exactly this, and reports `NOT_MEASURED` rather than a verdict when the tool is missing.
- 🔴 **The gate's nix config is NOT the image's — `step-capture-etc` is in the chain.** Measured: a live gate pod reports `sandbox = true`, while the raw `nixos/nix:2.24.15` image reports **`sandbox = false`**. Same image, different answer, so anything read from a bare image arm is not automatically a statement about the gate. Both agree that `/build` is absent, i.e. builds run unsandboxed either way.
- 🔴 **CORRECTION to this doc's earlier claim that the `privileged` namespace label "grants nothing".** That was half wrong and the half matters: the label grants nothing to the *current* gate (which requests no privilege and uses a PVC), **but hostPath admission genuinely depends on it** — measured via `kubectl apply --dry-run=server`, which warned the probe pod "would violate PodSecurity `restricted:latest` … restricted volume type `hostPath`" and admitted it only because the namespace enforces `privileged`. **So ranked items 5 and 7 are COUPLED: reverting the label would block any hostPath retry at admission.** Do not do item 5 while item 7 is live.
- **Probe residue, stated so nobody hunts it:** an **empty** directory `/var/lib/mnt/disk-1/nixlock-probe-hostpath` remains on `talos-xr6-r7p` (contents removed, `du` = 0). Every other object — both probe pods, the cleanup pod, the `nixlock-probe-pvc` PVC — was deleted, and `nix-store-cache` was verified `Bound 30Gi` afterwards. Removing the empty dir needs a pod mounting its PARENT, which was judged not worth the typo risk against a shared disk.
- **A diagnostic does not need GitOps.** The approved plan was a scratch pipeline via a `homelab-infra` PR; a hand-applied Pod answered the same question with strictly less blast radius — no Flux reconcile, no shared `eventlistener.yaml` edit, nothing persisted. **Put the FIX through GitOps; keep the DIAGNOSIS out of it.**

- 🔴 **A POSITIVE CONTROL CAUGHT A BROKEN INSTRUMENT TWICE IN ONE SESSION, and
  both would have published a false finding.**
  - Probe 1 printed `NIXBLD_CLIENT=FAILED` — which reads as "the unprivileged
    client was denied", the exact conclusion being sought. It was
    `su: command not found`; the image ships no `su`, `setpriv`, `runuser` or
    `doas`. Caught only by printing stderr and reading the CONTENT, not the label.
  - Probe 3 printed `ROOT_INSTANTIATE=FAILED` — because
    **`readlink -f` on a nix tool collapses it to the multi-call `nix` binary**,
    which selects behaviour from `argv[0]` and rejects `-E` with
    "unrecognised flag". Resolve the **directory**, then re-append the tool's
    name: `$(dirname $(readlink -f $(command -v nix-instantiate)))/nix-instantiate`.
  **Both were caught by a control that asserts the probe CAN observe success.**
  A probe reporting a denial must first prove it could ever report an `ok`.
- 🔴 **`nix-instantiate --eval -E '1+1'` is NOT a test of store access.** Probe 2
  used it and got a reassuring `ok` while touching nothing that needs the DB.
  A lock-taking probe must instantiate a derivation… **except that the 2×2 then
  showed even pure eval is denied for a builder**, so the distinction turned out
  not to matter *for the verdict* — but it did matter for whether the earlier
  probe measured anything at all. It did not.
- **A builder's uid is readable with no external binary:**
  `while read -r l; do case "$l" in Uid:*) echo "$l";; esac; done < /proc/self/status`.
  The build env's `/bin/sh` is static busybox with no `id`, which is what made
  probe 2 report `BUILDER_UID=` (empty) — an instrument gap that reads as data.
- **Reaching nix from inside a build needs its ABSOLUTE STORE PATH**, resolved
  outside and interpolated in — legal only because `sandbox = false` (measured on
  both arms). If sandboxing is ever actually engaged, this probe stops working
  and its silence must not be read as a pass.
- **Probe hygiene:** five probe pods, three PVCs and two cleanup pods were created
  and **all deleted**; `nix-store-cache` was verified `Bound 30Gi` after each
  round. The hostPath scratch dir was emptied (`732.3M → 0`). An **empty**
  `/var/lib/mnt/disk-1/nixlock-probe-hostpath` remains on `talos-xr6-r7p`.
  `devrc-ci` was never touched, and no probe ever mounted the live cache.

## How to verify
1. **The full measurement (this run's numbers):**
   ```bash
   DEVRC_READSET_OUT=/tmp/rs PYTHONPATH=$DEVRC/scripts \
     nix develop $DEVRC -c python3 -m pytest scripts/tests -q -p no:cacheprovider \
     -p testlib.nolaunch_plugin -p testlib.spool_plugin \
     -p testlib.gitenv_plugin -p testlib.nogit_plugin \
     -p testlib.readset_plugin -n 4 --dist loadfile
   nix develop $DEVRC -c python3 scripts/lib/readset_classify.py /tmp/rs.*.json --json /tmp/readsets.json
   ```
   Expect `measured test files : 149 / ALWAYS-RUN 28 / OPAQUE 78 / scoped 43` **on this tree**; the counts move as test files are added, so re-derive rather than asserting these.
2. **The bucket-C disproof, in one command** — the claim that decides whether an operand rule can be trusted:
   ```bash
   nix develop $DEVRC -c python3 -c "
   import json; d=json.load(open('/tmp/readsets.json'))
   for f in ['scripts/tests/test_drift_check.py','scripts/tests/test_subsystem_store_api.py']:
       print(f, d[f]['n_paths'], 'paths;', len(d[f]['triggers']), 'triggers')"
   ```
   Expect `test_drift_check.py` ≫ `test_subsystem_store_api.py` (172 vs 16 when measured). Both are OPAQUE and both have all data operands outside the repo — that is the point.
3. **A convicting operand rule, once written, must move files OPAQUE→ALWAYS-RUN and move ZERO files into `scoped`.** That invariant is the rule's own safety test; assert it in the test file, not in prose.
4. **The classifier's guards:** `nix develop $DEVRC -c python3 -m pytest scripts/tests/test_readset_classify.py -q -p no:cacheprovider` → 40 passed.
5. **Cluster is at baseline:** `KUBECONFIG=$KC_HOMELAB kubectl get task devrc-ci-gate -n tekton-ci -o jsonpath='{.spec.volumes}'` must show `persistentVolumeClaim: nix-store-cache`, and `…steps[?(@.name=="pytests")].computeResources.requests.cpu` must be `2`.
