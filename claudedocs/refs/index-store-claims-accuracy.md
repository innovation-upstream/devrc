# index-store claims accuracy — CLOSED investigation blocks and dated evidence

🔴 **EVICTED FROM `claudedocs/handoff-index-store-claims-accuracy.md` on 2026-09-13** because that
doc went over its size allowance (72,598 B against a 65,536 B ceiling) and
`test_no_handoff_doc_exceeds_its_budget` turned `main` RED. Every block below is either a question
that got ANSWERED — the first remedy that test's own playbook prescribes — or a bullet the core doc
still carries in another copy.

⚠ **A `refs/` file is NOT indexed by `handoff_search`.** That is the trade, and it is why only
CLOSED, DATED material is here and never an open thread. Nothing below is live; each block records
how a question was settled, so a future session does not re-run the probe.

🔴 **Every block below was moved by VERBATIM LINE-RANGE SLICING**, not retyped. The source line
ranges are named against `claudedocs/handoff-index-store-claims-accuracy.md` at `d35c8655`.

## Closed open-investigation blocks

Each of these was verified CLOSED on `origin/main` BY CONTENT before it was moved, not by believing
the block's own status text.

<!-- source lines 60-86 -->
**Closure evidence:** `devrc#1211` (`1a4350f3`) and `devrc#1179` are both on `main`; this
block says so itself and records `Next probe: none needed for this thread`.

### Why devrc-ci went red on PRs whose diff could not reach the failing suite
- **Symptom + exact repro:** `tekton/devrc-pytests` FAILURE on `228b8cea` and `8f1d4531`
  while the same shas were green locally on both tiers.
- **Observed (with values):** run 1 `devrc-ci-5hsmf` — `pytests` exit 0, `nodetests` exit 0,
  `verdict` exit 1, `failed=1` of 19958, in `scripts/tests/test_subsystem_store_api.py`.
  Run 2 `devrc-ci-tfrr6` — `failed=3` in `scripts/browser-bridge/tests/test_browser_agent.py`,
  message: *"the wrapper did not exit within 60s. Spawning 10 trivial processes on this machine
  just now took 0.16s (idle reference 0.10s; stall threshold 0.80s), so the MACHINE is not the
  explanation."* Four other runs on four non-mine shas failed on different tests in the
  store-api file. Locally: 641 passed × 3 standalone, sandbox tier `failed=0`.
- **Ruled out:** CPU/node load — the failing test's OWN control measured process-spawn latency
  at failure time and found the node healthy. via: measurement
- **Ruled out:** a defect in devrc#1132 — the failing test and the server it exercises are
  byte-identical between `3f9c8144` (CI green) and `228b8cea` (CI red). via: command
- **Ruled out (RETRACTED, mine):** "concurrent unsandboxed nix builds share /tmp and the network
  namespace". The unsandboxed observation is REAL (`/build` absent in a live gate pod while
  `nix config show` reports `sandbox = true`) but it was NOT the mechanism for either failure.
  via: doc
- **Leading hypothesis — now RESOLVED by others, and both causes were specific, not systemic:**
  the store-api failures were `_replace_bytes` fsyncing inside the request, exceeding
  `HANG_TIMEOUT` under disk contention — **devrc#1211 (`1a4350f3`)** moved the test store off the
  contended disk. The browser-bridge failures were three flat `elapsed < 1.0` bounds against a
  **5.0s** timeout, i.e. load detectors rather than timeout detectors — **devrc#1179** derives
  each as `TIMEOUT / 2`. With both on `main`, `7d3b6d2a` went green first try.
- **Next probe:** none needed for this thread. If it recurs, read the `verdict` step first —
  `pytests exit 0` + `verdict exit 1` means a test FAILED; a step that emitted no `RESULT:` line
  was KILLED, which is a different problem.

<!-- source lines 132-153 -->
**Closure evidence:** `gh pr view 1428` → MERGED 2026-09-09, squash `13c0791a`;
`RC_CUTOVER_COMPLETE` appears 3× in `origin/main:scripts/cairn-cutover.py`. The block's own
"leading hypothesis" was the wrong half of the choice — see the retained Gotchas bullet.

### `cairn-cutover.py` P3 is blocked by the new guard and cannot complete
- **Symptom + exact repro:** `cairn-cutover.py` P3 invokes `bash seed.sh --store <delta_dir> …`
  with **no `--allow-overwrite`** (`cairn-cutover.py:1379-1382`). Its `plan.shippable` is
  `ADD + SUPERSEDES + MERGED` (`cairn-cutover.py:516`, `:494`).
- **Observed (with values):** `SUPERSEDES` and `MERGED` are BY DEFINITION entries whose pod bytes
  differ, which is exactly what the pre-flight refuses — so P3 exits 8 and pushes nothing the
  moment anything supersedes. Measured on the real store while auditing: **52 of 157** shared
  entries differ today, so this is the normal state, not an edge.
- **Ruled out:** "the cutover tests would have caught it" — `test_cairn_cutover.py` only
  re-extracts the `find` expression from `seed.sh`'s source (`:473-475`); **nothing exercises P3
  against the real script**, so its 85 green tests say nothing about this. via: code
- **Ruled out:** "the refusal predates this PR so the guidance is fine" — the exit-8 refusal does
  predate round 2, but round 2 rewrote the message, and it now tells that caller the local tree
  is "a FROZEN pre-cutover mirror" and to "send it entry-by-entry". Both are FALSE for a curated
  delta that `_materialise` built and whose rollback set `_save_prepush` already wrote. via: code
- **Leading hypothesis:** P3 either needs to pass `--allow-overwrite` (it IS a reviewed delta
  with a rollback set already on disk) or is simply dead post-cutover and should say so. This is
  a decision about the cutover's lifecycle, not a bug fix, which is why it was not taken
  unilaterally.
- **Next probe:** `python3 scripts/cairn-cutover.py --help` and read P3's own description, then
  decide. If P3 is retained, the one-line change is `--allow-overwrite` at
  `cairn-cutover.py:1379-1382` plus a test that exercises P3 against the real `seed.sh`.

<!-- source lines 213-228 -->
**Closure evidence:** `origin/main:scripts/tests/test_subsystem_store_api.py` now asserts
`--allow-overwrite` is present in `seed.sh --help` (and records the 61 → 31 measurement in its
own docstring), so the "ZERO test coverage" half is false today.

### `--help` still drifts, and has ZERO test coverage
- **Symptom + exact repro:** insert one blank line into `seed.sh`'s leading comment header and
  run `bash seed.sh --help`.
- **Observed (with values):** unmodified → **61 lines**, ends on a complete paragraph, contains
  `allow-overwrite`. One blank line inserted at line 33 → **31 lines**, `allow-overwrite`
  occurrences **0**. The `awk`'s `{exit}` fires on the first non-`#` line, and a blank line is
  one. That is verbatim the defect `d7c4c266` exists to fix, silently reintroducible.
- **Ruled out:** "the line-range rot was the whole bug and the `awk` closes it" — the `awk`
  closes the GROWTH shape and leaves the BLANK-LINE shape wide open. via: measurement
- **Ruled out:** "a test guards the fix" — `grep` over `test_subsystem_store_api.py` finds no
  `seed.sh --help` assertion at all; the only `--help` test belongs to
  `verify-byte-identity.sh`. The commit's headline claim is unguarded. via: measurement
- **Leading hypothesis:** `/^#/{...} /^$/{next} {exit}` (or match `^#|^$` and stop only on a
  real non-comment line), plus the missing test asserting `--help` contains `allow-overwrite`
  and ends on a terminator.
- **Next probe:** fold into the same fix commit as the block above, then round 4.

<!-- source lines 275-302 -->
**Closure evidence:** `gh pr view 1453` → MERGED 2026-09-09, squash `eeea9025`; `maintenance`
appears 11× in `origin/main:scripts/tests/test_git_repo_isolation.py`. 🔴 The second block below
is the REFUTED `gc --auto` theory — it is kept verbatim, with its positive control, precisely so
nobody re-derives it.

### `test_git_repo_isolation.py::test_live_cotenants_sees_another_process_in_the_repo` is LOAD-FLAKY on the sandbox tier
- **Symptom + exact repro:** the sandbox `pytests` derivation goes red with `failed=1` while
  the dev-host tier on the same tree is green. Re-running the identical derivation passes.
  `nix build <repo>#checks.x86_64-linux.pytests --no-link --print-build-logs`
- **Observed (with values):** `scripts/tests/test_git_repo_isolation.py:1496` —
  `assert live_cotenants([git_dir]) == []` returned `['126220:git']` on a **brand-new tmp
  repo**, i.e. a live `git` process whose cwd is inside a repo created microseconds earlier.
  Run 1: `SANDBOX_PYTESTS_NIX_RC=1`, `RESULT: FAIL (exit=1)`, 21867 collected / 21863 passed
  / **1 failed**. Run 2, byte-identical derivation: `RC=0`, `RESULT: PASS`, 21867 / 21864 /
  **0 failed**.
- **Ruled out:** "devrc#1304 caused it" — the file is **byte-identical to `origin/main`**
  (`git diff --stat origin/main HEAD -- <file>` empty) and the PR touches four files, none
  of them this one. via: command
- **Ruled out:** "it is deterministic" — same derivation, 1 red / 1 green. via: measurement
- **Ruled out:** "it reproduces on the dev host" — 10 consecutive runs of that single test,
  unloaded, all passed in 0.63–1.34s. Absence at low load is NOT evidence of absence.
  via: measurement
- **Leading hypothesis, NOT confirmed:** `_mkrepo` (`:250-258`) runs `git init` / `add` /
  `commit` via `subprocess.run`, which waits only for the PARENT. `git commit` can fork a
  detached `git gc --auto` whose cwd is the new repo; the co-tenant scan then sees it. Load
  widens the window, which would explain dev-host-green / sandbox-red. A theory that
  explains the failure is not evidence for it — this was never reproduced.
- **Next probe:** the discriminating one is to make `_mkrepo` deterministic rather than to
  re-run: `git -c gc.auto=0 …` on all three commands (or `git init` with
  `core.logAllRefUpdates=false` + an explicit `gc.auto=0` in `_env()`), then re-run the
  sandbox tier under deliberate load. If the flake survives that, the gc theory is wrong and
  the next suspect is the fsmonitor/credential helper. **Do not "fix" it by re-running** —
  a flaky gate trains everyone to click through.

<!-- source lines 304-328 -->
### 🔴 REFUTED — the `gc --auto` theory for the co-tenant flake (supersedes the block above)
- **Symptom + exact repro:** unchanged — the sandbox `pytests` derivation reds with
  `live_cotenants(...)` returning a `git` process on a brand-new tmp repo, and a re-run of
  the byte-identical derivation passes.
- **Observed (with values):** `gc.auto` is unset, i.e. the default **6700** loose objects;
  `_mkrepo` leaves **3** (`git count-objects -v` → `count: 3 size: 12 in-pack: 0`). The
  threshold is unreachable by construction, so the commit CANNOT fork a `gc --auto`.
  Two-arm loop, 80 iterations each at load 31.6 — as-shipped and with `gc.auto=0` forced:
  **0 hits in both arms**.
- **Ruled out:** the `gc --auto` mechanism this doc previously named as the leading
  hypothesis. Refuted twice over: arithmetically (3 objects vs a 6700 threshold) and
  empirically (0/80 in the arm that should show it). via: measurement
- **Ruled out:** "the 0/80 might be a probe wired to nothing" — a positive control ran
  FIRST: spawning a process with cwd in the repo made `live_cotenants` return
  `['<pid>:python3.12']`, and the same harness returned `[]` before the spawn. The probe
  demonstrably sees a co-tenant, so the zero is a real reading. via: measurement
- **Leading hypothesis:** NONE — and that is the honest state. The failure was observed
  once, in the sandbox tier, under `pytest -n 4 --dist loadfile`. `live_cotenants` matches
  on a process whose **cwd** is inside the work tree, and nothing yet explains how a `git`
  process acquired a cwd inside a just-created per-test tmp repo.
- **Next probe:** do NOT re-derive `gc --auto`. Wait for the next red and read the message
  #1340 added — it prints the intruder's `cwd=` and `cmdline=`, which is what separates an
  xdist sibling from a stray host process from one of our own children. `/proc` for a
  transient process is gone by the time anyone reads the log, which is why the capture had
  to move into the assertion.

## The closed ranked item

<!-- source lines 368-375 -->
**Closure evidence:** `gh pr view 1554` → MERGED 2026-09-12, squash `663bc86a`. It was already
marked ✅ CLOSED in the ranked list; the ledger keeps closed items in place by design.

2. ✅ **CLOSED — `devrc#1170`'s 🟡5 and 🟡6, merged as `#1554` (squash `663bc86a`).**
   🟡5: the probe now EMITS `policy: <path>  (<basis>)`, sourced from
   `subsystem_touch.governing_policy` rather than re-derived, and `index-store.md`
   carries the `subsystem-index/SKILL.md` sentence verbatim with a test comparing
   the two docs to each other. The instruction was made satisfiable rather than
   softened. 🟡6: `--template` over an existing entry now REFUSES (exit 2) naming
   what would be destroyed. ⚠ #1170's audit framed 🟡6 as a race; that framing was
   wrong and the single-writer repro is two commands.

## Closed and superseded gotchas

The first three are ✅-closed records whose live residue stays in the core doc as a lean bullet.

<!-- source lines 392-400 -->
- ✅ **THE DASH PREMISE IS TRUE ON THE DEPLOYED POD, AND NO LONGER LOAD-BEARING.**
  Measured 2026-09-10 against the RUNNING image (`subsystem-store-api:0.8.0`, not
  the `Dockerfile`): `/bin/sh -> dash` (`/usr/bin/dash`), and its `echo "a\tb"`
  emits a real TAB. Control: bash emits the literal `a\tb`, so the asymmetry is
  real and the reading is not a no-op. **But every pod-side emit in `seed.sh` is
  now `printf`, not `echo`** — `grep -nE "sh -c .*echo"` returns nothing — so the
  dash-specific behaviour cannot reach the join key any more. The premise held
  AND the code stopped depending on it; verifying it changed no decision, which
  is the honest outcome for a `forcing: none` item.

<!-- source lines 439-449 -->
- ✅ **THE OPENCODE BLINDNESS IS FIXED — `devrc#1365` → squash `14126d94`.**
  `clawgate_resolve` reads `OPENCODE_SESSION_ID` before `CLAUDE_CODE_SESSION_ID`
  (verified on `origin/main` by content: 9 occurrences where the item said 0), and
  refuses outright when `$OPENCODE` is set with no opencode id, because the claude
  id in scope there may be an ancestor's and nothing can tell. 🔴 **This item was
  still telling the next session to do work that had already merged** — the ranked
  list is not self-closing, and the only moment anyone checks is the next writer's.
  ⚠ It buys CORRECTNESS, not capability: `clawgatectl` has no opencode tier
  (measured 2026-09-07, `grep -rl OPENCODE containers/` = 0 against 5 for the claude
  var), so opencode sessions resolve exit 5 rather than their own tasks until that
  lands — a Go change in `homelab-talos`.

<!-- source lines 451-458 -->
- ✅ **P3 IS RETIRED — `devrc#1428` → squash `13c0791a`, and the framing in the old
  ranked item was the wrong half of the choice.** It read "either pass the flag or
  declare P3 dead", calling `--allow-overwrite` the one-line fix. That flag is the
  DATA-LOSS path: `seed.sh`'s tar adds and overwrites but never deletes, so a push
  from a frozen mirror silently reverts every pod-newer entry and reports success.
  The exit-8 refusal was the last guard, not the bug. P3 now refuses with
  `RC_CUTOVER_COMPLETE (19)`, conditioned on state (`refused > 0`) so a genuine
  first cutover still passes through.

<!-- source lines 642-649 -->
Superseded historical closure, carried forward through two `State now` replaces.

- **Carried forward from an earlier `State now` (a REPLACE section, so it would otherwise be
  dropped):** the ORIGINAL rank 1 is CLOSED — the writer was Claude Code sessions themselves
  using `Edit`/`Write` on `~/.claude/analyze-service-index/` (the `0444` freeze is inert against
  them: those tools rewrite-and-rename and need only the containing directory's `0755` bit), 21
  stranded bullets + 2 revisions were reconciled onto the pod and verified at the consumer, and
  the write path was closed by the CREATE verb (`devrc#1254` → `34d00d90`, live as image
  `subsystem-store-api:0.7.0`, verified with `cairn create` returning exit 9 / already-exists
  where it returned 405 read-only before).

### Duplicate bullets — the surviving copy stays in the core doc

🔴 These are not evictions. Each of these bullets appears TWICE in the core doc; only the second
copy was removed, and the first copy is still there and still indexed. They are sliced here so the
removal is structural rather than something you have to trust. The retained copy's source line
range is named against the pre-cut doc.

<!-- source lines 609-615 -->
Duplicate of lines 655-661 (`git checkout -- <file>` DESTROYED UNCOMMITTED WORK), which is
retained.

- 🔴 **A COMMIT MESSAGE WRITTEN FROM MEMORY SHIPPED A FALSE CLAIM, AND THE DEFECT IT SAID WAS
  FIXED WENT WITH IT.** `3c8e37da` asserted a 🔴 fix; the pushed blob contained **none** of it
  (`grep -c OC_LOCK_PID_FILE` = 6 where it should have been 0). Cause: the red-at-base check
  restores with `git checkout HEAD -- <file>`, and it was run BEFORE committing, so `HEAD` was
  the pre-fix commit and the "restore" reverted the uncommitted work. `git add` then staged a
  file that no longer held the change. **Read the claim off the committed blob, never off what
  you remember editing** — and commit before any checkout-based experiment.

<!-- source lines 671-674 -->
Duplicate of lines 616-620 (A CONTROL THAT SHARES THE CONTAMINANT), which is retained.

- 🔴 **A CONTROL THAT SHARES THE CONTAMINANT IS NOT A CONTROL.** A browser-bridge failure
  reproduced on `origin/main` and was reported as "inherited / main is broken". It was neither —
  a machine-global orphaned lock was failing both runs. What worked was removing the suspected
  cause and watching the test pass, not a second sample.

<!-- source lines 675-677 -->
Duplicate of lines 621-623 (THE PIPE TRAP FIRED FOUR TIMES), which is retained.

- 🔴 **THE PIPE TRAP FIRED FOUR TIMES** — `… | tail; echo "rc=$?"` printed `GATE_RC=0` over
  `GATE: RESULT=FAIL exit=1`, and `NIXBUILD_RC=0` over a failed derivation. The runners' own
  `RESULT:` line caught it every time.

<!-- source lines 685-687 -->
Duplicate of lines 637-641 (Concurrent agents corrupt each other's test results), which is
retained.

- **Concurrent agents corrupt each other's results on this box.** Load hit 62 on 24 cores; three
  failures investigated in this effort were other sessions' suites rather than code. Queue behind
  them rather than killing them, and treat any red above ~load 20 as needing a control.

<!-- source lines 699-702 -->
Duplicate of lines 773-776 (`--help` claimed "cannot drift" TWICE), which is retained and is
the newer, correct version — this copy's closing clause "there is no `seed.sh --help` test at
all" is FALSE today.

- 🔴 **`{exit}` on "the first non-comment line" treats a BLANK line as code.** The `--help`
  rewrite traded a rotting line-range for a rule that a single blank line in the header
  silently truncates — measured 61 → 31 lines with `allow-overwrite` gone. A rule that "cannot
  drift" should be tested; there is no `seed.sh --help` test at all.

<!-- source lines 707-710 -->
Duplicate of lines 777-779 (Two independent blind lenses beat one auditor run), which is
retained.

- 🔴 **TWO INDEPENDENT LENSES FOUND THE SAME TAB DEFECT BY DIFFERENT ROUTES** — lens 1 by
  fuzzing the real script, lens 2 by hand at unmutated HEAD. Neither was told what the other was
  looking for. That agreement is the strongest evidence in this round, and it is also the
  argument for splitting a round into lenses rather than running one auditor twice.

<!-- source lines 789-792 -->
Duplicate of lines 415-419 (SHIPPING THE DIAGNOSTIC INSTEAD OF A GUESS), which is retained.

- 🔴 **WHEN THE MECHANISM IS UNKNOWN, SHIP THE DIAGNOSTIC, NOT A GUESS.** `gc.auto=0` was
  one line and would have looked like a resolution while the real cause stayed open —
  strictly worse than nothing, because it would have stopped anyone looking. Making the
  failure self-describing is the honest move when you cannot name the cause.

<!-- source lines 806-808 -->
Duplicate of lines 766-769 (THE PIPE TRAP FIRED TWICE MORE), which is retained and carries the
same `gh pr merge` rc finding.

- **`gh pr merge` rc is not the merge's verdict.** It returned **1** for a failure that was
  only about deleting a LOCAL branch still held by a worktree — the remote merge had
  already succeeded. Remove the worktree first, and verify by content either way.

<!-- source lines 902-905 -->
Duplicate of lines 425-430 (THIS RANKED LIST HAS NOW GONE STALE THREE TIMES) and of the
`## Next steps (ranked)` preamble, both retained.

- ⚠ **A RANKED LIST WENT STALE A FOURTH TIME — by the session that closed it.** It
  claimed the work with *"retire the stale ranked text"* in its own claim subject,
  shipped the fixes, and never returned to the list; rank 2 read "Never started" after
  `#1554` had merged. Closed in `#1636`. Nothing closes a ranked item automatically.

<!-- source lines 906-908 -->
Duplicate of lines 459-465 (FOUR AUDIT ROUNDS, AND THE LAST THREE FOUND MY OWN PROSE) and of
lines 741-746 (FOUR OF SIX ROUNDS), both retained.

- ⚠ **FOUR AUDIT ROUNDS, AND EVERY FIX ROUND INTRODUCED THE NEXT FINDING** — 4 for 4,
  and the finding was usually the fix round's own PROSE, not its code. Round 0 (the
  requirements pass) earned its keep: `ran: 1 · changed the outcome: 1`.

### Closed corrections and a restated finding

Both were moved on the same 2026-09-13 cut. The first is a pair of ✅-closed corrections; the second
is the THIRD copy of a finding the core doc still states in `## Goal` and `## State now`.

<!-- post-cut source lines 591-599 -->

- **Two corrections to this doc, measured 2026-09-05, recorded HERE so a future `State now`
  replace cannot drop them.** (1) The old rank 8 — *"`main` is RED on
  `test_clawgate_task_interview_guard.py`"* — is **CLOSED**: it passes on `origin/main`
  (`1 passed in 0.29s`), fixed by `8c27c5cf` (#1303), "a stale file at the `--body-file`
  path shadowed the heredoc about to overwrite it — the verdict was a property of the HOST".
  The dev-host tier has no known inherited red any more, so a red there now means something.
  (2) Tekton posts on a PR head but `required_status_checks` is **null** and
  `enforce_admins` **false** — re-measured at merge time. Nothing gates; the two-tier local
  run IS the gate.

<!-- post-cut source lines 668-677 -->

- 🔴 **`clawgate_handoff.sh resolve` EXIT 5 CANNOT DISTINGUISH "THIS SESSION TOUCHED NO TASK" FROM "THE SESSION ID IS WRONG", and it answered that way on SIX separate
  handoffs in this effort.** An unknown session returns `200 {"tasks":[]}`, not 404, so the
  empty array is the same observation for both. Every time the positive control confirmed the
  board was reachable (2, 11 and other link counts for OTHER sessions), which is what makes
  the zero a reading about THIS id rather than about the board — and equally why it is not a
  clean result. Per the flow no `clawgate-task:` field was written and none created, six
  times. ⚠ One run exited **6** instead (one linked task, role=`read`, none worked) — a
  different code for a different state, also correctly declining to write. **The six
  near-identical bullets this replaces are MERGED, not lost: they differed only in the
  control's link count.**

### The `no CREATE route` sweep (claim retracted) — the 2026-09-11 figures and the history of wrong ones

🔴 The IMPERATIVE stays in the core doc ("do not quote a count here you have not re-derived with
the repo's OWN scanner"), and so does the whole *"all sites are corrected" was itself false* /
*a string needle cannot close this class* paragraph that follows it. Only the dated figures and the
list of superseded ones moved here.

<!-- source lines 475-486 -->

  costly. ⚠ UPDATED 2026-09-11. Measured by loading the repo's OWN scanner
  (`_normalise_for_scan` + `_RETRACTION_MARKERS` + `_MARKER_WINDOW`) against an
  extracted `8b2b960b` tree, with a positive control returning 1 so the figures
  are not a wired-to-nothing zero: **10 occurrences across 8 files; 9 live and
  unmarked in 7 files; 8 live in 6 files besides the `cairn` SKILL.md.** One
  straddles a newline AND is uppercase, so case-sensitive and line-based greps
  both miss it.
  🔴 **THIS BULLET'S COUNT HAS NOW BEEN WRONG TWICE, WHICH IS THE POINT OF IT.**
  It first said "two comments", then "four live sites in four tracked files"
  (round 1 caught it), then "6 live in 4 files" (round 2 caught that). Each wrong
  figure was produced by a hand sweep and each read as precise. **Do not quote a
  count here you have not re-derived with the scanner.**

### A ✅-closed carry-forward: `devrc#1223`'s `dropped lines:` advisory

Verified by content AND behaviour when it was written; nothing here is open.

<!-- post-cut source lines 474-477 -->

- **Carried forward from the previous `State now` (it would otherwise be dropped by this
  update):** `devrc#1223 → 540e748d`, the `dropped lines:` advisory in `--validate`, was
  verified by content AND behaviour — run against the real 2026-08-19 blob it reports **13
  dropped lines** and flags nuance line 11 as a lost declaration.
