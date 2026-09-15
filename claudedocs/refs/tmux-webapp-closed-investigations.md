# tmux-webapp — CLOSED investigations (demoted verbatim)

Evicted from `claudedocs/handoff-tmux-webapp.md` on 2026-09-15 under the eviction
playbook in `scripts/tests/test_handoff_doc_size.py` (step 1, evict what has CLOSED;
step 2, demote dated evidence to a sibling `refs/` file and leave a pointer).

🔴 **Every block below reached a VERDICT.** They are kept because the reasoning — and in
several cases the RETRACTION of earlier reasoning — is the value; a future session must
not re-derive them. They are NOT live state: each is dated, and anything it says about
infrastructure was true when written.

⚠ This file is not indexed by `handoff_search` and is exempt from the handoff size
ceiling. That is the same trade a skill makes with its `reference/` directory.

### ✅ RESOLVED — `clawgate-e2e` DID register on #566, and it PASSED
🔴 **This CORRECTS the block below, which is left in place because the mistake is the lesson.**
`tekton/clawgate-e2e` → **success, "clawgate e2e passed — 124 tests, 2 skipped"** on head
`88d53d0d`. It had simply not been *scheduled yet* when I read the check list minutes after
opening the PR. **I read an absence as a fact about the pipeline when it was a fact about the
CLOCK** — the rival mechanism ("not started yet") was never named, and an empty result cannot
distinguish the two. Rank 8b's closing condition IS met.

⚠ **The same reading gives rank 8e its number, from BOTH tiers, and they agree:** CI reported
`124 tests, 2 skipped`; the local full run reported `120 passed / 4 flaky / 2 skipped` in 21.9m,
and the pipeline's own rule is `ran_ok = passed + flaky` = **124**. Applying the existing
derivation ratio (110/118 ≈ 93.2%) gives **`MIN_PASSED: 115`**. The 4 flaky were
`task-comment-delete.spec.ts:44`, `tasks.spec.ts:523`, `tasks.spec.ts:555` and
`tasks-mobile.spec.ts:854` — all pre-existing, none reachable from an e2e-only diff.

### ✅ RESOLVED — `clawgate-ci` "FAILED: go" on #566 is a NETWORK failure, not a code failure
- **Observed (with values):** the pipeline run is `clawgate-ci-nndsh` in ns `tekton-ci`, param
  `revision=88d53d0d…` (MY sha — so it could NOT be dismissed as someone else's), failing step
  `step-verdict` exit 1 over `go fail / extension pass / hook pass`. The `step-go` log ends with
  **5×** `net/http: TLS handshake timeout` fetching `github.com/jackc/pgx/v5@v5.10.0`,
  `github.com/spf13/cobra@v1.10.2` and `github.com/coder/websocket@v1.8.14` from
  `proxy.golang.org`, then `go leg rc=1`. Not one compile or test error.
- **Ruled out:** my diff. It contains **zero** Go files (`git show --name-only` on the commit), and
  the same tree is green locally: `go build ./...` rc 0, `go vet ./...` rc 0 and no output,
  `go test ./...` **20 ok packages / 0 FAIL** (the 20 is the positive control — an empty filtered
  output alone would not have proved the runner ran).
- **How to read it:** this is the same family as the documented docker.io DNS poisoning on this
  LAN — module fetches over TLS timing out. **Re-run the pipeline; do not debug the diff.**
- ⚠ `tekton/gitops-validate` on the same PR says **`COULD NOT RUN: scripts-tests`**, which this
  repo's CLAUDE.md defines as a gate that stopped before a leg reported — also not a verdict on
  the change.

### `clawgate-e2e` has not registered as a check on `ZacxDev/homelab-infra#566`
⚠ **SUPERSEDED — see the RESOLVED block immediately above. The conclusion was WRONG.** Kept
verbatim because the failure mode is worth recognising: an absent check and a not-yet-scheduled
check are byte-identical in `gh pr checks`, and I diagnosed the first without naming the second.
- **Symptom + exact repro:** `gh pr checks 566 --repo ZacxDev/homelab-infra` lists **only**
  `tekton/ux-audit-clawgate` (PENDING). The suite this PR exists to extend is not among them.
- **Observed (with values):** `gh pr view 566 --json statusCheckRollup` → exactly one row,
  `tekton/ux-audit-clawgate  PENDING`. Contrast devrc#1056, where both `tekton/devrc-pytests` and
  `tekton/devrc-nodetests` appear within minutes of a push.
- **Ruled out:** the ERROR-state class that hit devrc#1056 — that showed the checks PRESENT with
  conclusion `ERROR`. Here the check is ABSENT, which is a different failure with a different fix
  (a fresh push clears an ERROR; it will not conjure a check that never triggers).
- **Leading hypothesis:** the `clawgate-e2e` trigger is path- or event-filtered and either has not
  fired yet or does not match a PR touching only `containers/clawgate/e2e/**`. UNPROVEN.
- **Next probe:**
  ```bash
  grep -n 'interceptor\|filter\|cel\|clawgate-e2e' \
    ~/workspace/homelab-talos/clusters/homelab/apps/tekton-pipelines/triggers/clawgate-e2e-pipeline.yaml
  gh pr checks 566 --repo ZacxDev/homelab-infra
  ```
- 🔴 **Rank 8b's closing condition is "green in `clawgate-e2e`", so a check that never runs does
  NOT satisfy it** — an absent check reads as "nothing to see", which is the opposite of what it is.

### ⚠ SUPERSEDED — `test_subsystem_store_api.py` is FLAKY on `main`, and nothing is fixing it
🔴 **BOTH HALVES OF THIS HEADING ARE NOW FALSE, AND ITS "next probe" SENDS YOU DOWN A REFUTED
THREAD. Read `### ✅ DIAGNOSED` below before spending a minute on anything here.** The mechanism is
fsync latency, not seed/ordering, and `fix/xdist-parametrize-values-deterministic` is not the thread
to pull. Kept verbatim because the eliminations below are still true and still useful — it is the
FRAMING that was wrong, which is this doc's own documented failure mode for an open-investigation
block.
- **Symptom + exact repro:** `nix develop ~/workspace/devrc -c python3 -m pytest
  scripts/tests/test_subsystem_store_api.py -q` on a CLEAN `main` checkout.
- **Observed (with values):** `1 failed, 640 passed in 321.94s` — failing
  `TestEnumerationChannelsAreClosed::test_a_scope_FILTERED_snapshot_of_a_denied_scope_ships_nothing`.
  CI on devrc#1056 failed a **DIFFERENT** case in the same file:
  `TestTheActorComesFromTheTOKEN::test_a_FORGED_actor_in_the_body_is_DISCARDED[record0-kkkk…LLLL]`.
  Two different tests across two runs ⇒ the failure moves.
- **Ruled out:** devrc#1056 as the cause — it touches only `scripts/session-manager` and
  `scripts/tests/test_session_manager.py`, neither of which this file imports, and its own 691
  tests pass on the rebased tree. Also ruled out for #1101, which changed ONE markdown file and
  was blocked by it once, then passed on a re-run.
- **Leading hypothesis:** non-deterministic parametrize values (the `[record0-kkkk…LLLL]` id shape
  is generated, not literal). A branch `fix/xdist-parametrize-values-deterministic` EXISTS in a
  local worktree — **but there is NO open PR for it** (`gh pr list --state all` matched nothing on
  `xdist|parametrize|determin`).
- 🔴 **Why this matters more here than elsewhere:** devrc is the one repo in this thread that
  genuinely enforces required checks, with `enforce_admins: true`. An intermittent failure that
  picks a different case each run will keep blocking arbitrary PRs — it blocked two of mine today.
- **Next probe:** `for i in 1 2 3; do nix develop ~/workspace/devrc -c python3 -m pytest
  scripts/tests/test_subsystem_store_api.py -q -p no:cacheprovider; done` on clean `main`, and
  record WHICH case fails each time. If the case moves, seed determinism is confirmed; then open a
  PR for the existing branch rather than starting fresh.

### ⚠ `tekton/clawgate-ci` has never once completed on the 8b branch, for two different reasons
- **Observed (with values):** on `88d53d0d`, `FAILED: go` whose `step-go` log ends in **5×**
  `net/http: TLS handshake timeout` fetching pgx/cobra/coder-websocket from `proxy.golang.org`,
  then `go leg rc=1` — not one compile or test error. On the final sha `c617bdd5`,
  `COULD NOT RUN: clawgate-ci stopped before any leg reported`.
- **Ruled out:** the diff. It contains **zero** Go files, and the same tree is green locally:
  `go build ./...` rc 0, `go vet ./...` rc 0 silent, `go test ./...` **20 ok packages / 0 FAIL**
  (the 20 is the positive control — an empty filtered output would not have proved the runner ran).
- **Leading hypothesis:** cluster congestion/preemption, the documented `tekton` skill class.
  `gitops-validate` on the same sha went from `COULD NOT RUN` to **all 8 legs passed**, which is
  the same transience from the other direction.
- **Consequence, stated plainly:** #566 was merged with `clawgate-ci` red. That is defensible here —
  homelab-infra returns **403** on branch protection so its checks are DETECTORS, not gates; the
  check that covers this change (`clawgate-e2e`) was green on the final sha; and the Go leg was
  verified locally with controls. But "clawgate-ci is green for this change" is a claim NOBODY can
  make, and it should not be inferred later from the merge.

### 🔴 `ZacxDev/homelab-infra#591` (rank 8c) — the CHANGE looks right, the EVIDENCE for it does not
- **Symptom + exact repro:** the subagent that wrote #591 reported an 8-row table of "mutant → red
  with this message". Re-running its site-3 mutant reproduces a **different failure**, in a
  different place, for a different reason.
- **Observed (with values):** in `/home/zach/workspace/ht-8c-574240/containers/clawgate`, mutating
  `internal/api/push_task.go` (exactly 1 match)
  `if a.Status != agents.StatusRunning || a.NoteID == nil {` → `if a.Status != agents.StatusRunning {`
  builds clean (rc 0), then
  `go test ./internal/api/ -run TestProvisioningPushSkipsTasklessAndOperator -count=1` → rc 1 with:
  ```
  --- FAIL: TestProvisioningPushSkipsTasklessAndOperator (0.00s)
  panic: runtime error: invalid memory address or nil pointer dereference
   ... api.(*Server).notifyAgentRunning ... push_task.go:192
   ... api.(*Server).BroadcastAgentChanged ... server.go:2160
   ... api.TestProvisioningPushSkipsTasklessAndOperator ... push_task_test.go:279
  ```
  `push_task.go:192` is `noteID := *a.NoteID`. With `NoteID: nil` seeded, removing the nil half of
  the guard nil-derefs **before** `pushTask` is ever reached. The panic lands at test line **279**
  (`srv.BroadcastAgentChanged(...)`), one line ABOVE the `awaitPushesSettled(t, srv)` on **280**.
- **Ruled out:** that the barrier itself is broken. `awaitPushesSettled` is sound by construction —
  `s.pushInFlight.Add(1)` is at `internal/api/server.go:2010` on the **caller's** goroutine with
  `defer s.pushInFlight.Done()` inside the spawned goroutine, and `goPushBroadcast` is the sole
  spawn site. It also already has its own in-repo guard,
  `TestAwaitPushesSettledWaitsForTheFanOutToFinish`, driven by a `slowPusher` that blocks until
  released. Also ruled out: that the suite is red — independent full run in that worktree is
  **20 `^ok` / 0 `^FAIL`, `go test`'s own exit 0** (counted from the runner's own lines, not piped).
- **Leading hypothesis:** the agent applied a different patch than the one its report describes, OR
  it scored the site from the panic without reading which line failed. Either way the site-3 row is
  not evidence, and 🔴 **that same mutant was the basis for the headline "the barrier is
  load-bearing" control** ("mutant 3 + barrier deleted ⇒ passes 25/25"), which a panicking test
  cannot have produced.
- **Next probe:** the agent has been sent back for (a) the literal patch text it actually applied,
  (b) a site-3 mutant that reaches `len(mp.callsOfType("task")) != 0` instead of panicking upstream
  — removing the dedupe, or admitting a task-less agent while keeping the deref safe, are the
  shapes, (c) the barrier control re-run on that corrected mutant, and (d) **a re-check of the other
  seven sites for the same failure mode** — for each, whether the red came from the test's own
  `t.Fatalf` or from a panic/compile error upstream of the barrier. Site 8
  (`TestSessionCommentDoesNotPush`) at least carries its own non-vacuous control (the machine
  endpoint on the same server DOES push), but note it also has an earlier `if d.armed()` guard at
  test line ~532 that would fire BEFORE the barrier for some mutant shapes — the classic
  "an earlier check always wins so the guard never executes" trap.

### ✅ RESOLVED — `ZacxDev/homelab-infra#591`'s evidence was UNDER-REPORTED, not wrong
🔴 **This CORRECTS the block above, which is left in place because the failure mode is the lesson.**
The site-3 mutant was **two hunks**, and the report described only the first. The omitted hunk was:
```go
-	noteID := *a.NoteID
+	var noteID int64 // MUTANT: nil-safe deref so M3 fails on the ASSERTION, not a panic
+	if a.NoteID != nil {
+		noteID = *a.NoteID
+	}
```
The mutant's own comment names the panic trap, so the author had designed around it and then
under-described the patch. My single-hunk re-run was therefore a **different mutant** — a real
INVALID one — and the panic I measured was correct about the patch I applied and not about theirs.

**Re-verified independently, on the corrected single-hunk form** (which keeps the deref reachable
by assigning the by-value local copy: `agents.Store.GetByName` returns `Agent`, not `*Agent`):
```go
-	if a.Status != agents.StatusRunning || a.NoteID == nil {
+	if a.Status != agents.StatusRunning {
 		return
 	}
+	if a.NoteID == nil {
+		a.NoteID = new(int64) // local copy; keeps the deref below reachable
+	}
```
- mutant **+** barrier → `push_task_test.go:282: task-less agent fired 1 provisioning push(es),
  want 0` — the test's own `t.Fatalf`, **0 panics** (`grep -c '^panic:'` = 0).
- mutant **−** barrier (the `awaitPushesSettled` line deleted from that test only) → `ok`, `-count=50`.
- 🔴 **Positive control for that rc 0**, because a `-run` filter matching nothing also exits 0:
  the same filter on the unmutated tree with `-v` gives **50 `--- PASS`**, and no
  `no tests to run` warning appears in either log. So the assertion is genuinely BLIND without the
  barrier, and the barrier is what catches the mutant.

All eight sites were re-classified as assertion-vs-panic-vs-compile-error: **eight died at their
own `t.Fatalf`, zero panics, zero compile failures.** Site 3 was the only one carrying this hazard,
because its guard is the only one that also protects a pointer deref. Commit amended to
`d687fcaa`, force-pushed with `--force-with-lease`; local HEAD == `origin`'s.

### ✅ RESOLVED — ranks 8c and 8d are MERGED and verified on the MERGED TREE
- **8c** — `ZacxDev/homelab-infra#591`, squash **`d6dc52cf`**. All four checks were green.
- **8d** — `ZacxDev/homelab-infra#592`, squash **`d2d2346e`**.
- **Verified on `origin/trunk` at `d2d2346e`, i.e. the tree the merges created, not either branch:**
  Go `20 ^ok / 0 ^FAIL` (rc read from `go test` itself), bats `67 ok / 0 not ok` (rc read from
  `bats` itself) with the new guard passing as `ok 35` and `ok 67` in the two suites.
- **Content-verified, never by ancestry** (a squash merge makes `--is-ancestor` false forever):
  0 `time.Sleep` left in the two Go test files on trunk; `scan_inert_negated_greps` present 5x in
  each bats suite.

🔴 **#592's `clawgate-ci` was RED at merge time and it was NOT a verdict on the change.** The run
(`clawgate-ci-czshq`, rev `751aabaa`) hit **`TaskRunTimeout`** at the task's 25m budget, consumed
in the **`go`** step, which killed `go`/`extension`/`hook`/`verdict` together — so the check text
read `COULD NOT RUN: clawgate-ci stopped before any leg reported`. Attribution, stated with its
evidence: #592's diff contains **zero Go files**, so it cannot have slowed the `go` step, and
**#591 — which does touch Go — passed the same pipeline nine minutes later** (`clawgate-ci-hsdlk`,
rev `d687fcaa`, Succeeded). `ZacxDev/homelab-infra#572` (raise that budget) is **MERGED** — 2026-08-31 18:36Z. Read the
investigation headed **🔴 `TaskRunTimeout` IN clawgate-ci HAS TWO DISTINCT CAUSES, AND `#572` ONLY
FIXES ONE**, in `claudedocs/handoff-tmux-webapp.md` — NOT the `TaskRunTimeout` investigation in this
file, which is a different one (rank 61 `clawgate-e2e`, node I/O) with a different conclusion —
before recording it as the fix: it addresses one of the two causes.
⚠ **The leg that never ran was `hook` — the one #592 exists to exercise** — so merging on the
"COULD NOT RUN means broken gate" convention alone would have shipped it with zero CI coverage of
the thing it changed. It was merged on a **local reproduction of that exact leg instead**: the same
`docker.io/bats/bats:1.11.1` image the pipeline uses, run on the LAPTOP (the workbench cannot pull
docker.io), `BATS_RC=0`, 67/0. That closed the agent's one self-declared unverified gap
(it had run bats 1.14.0).

### ✅ RESOLVED — the MERGED-TREE CI verdict is in, and BOTH tiers are green
The earlier note that only the dev-host tier had confirmed 8c/8d is now **superseded**.
`clawgate-ci-rerun-z5wj5` on revision **`d2d2346e`** (trunk with both merges):
```
== clawgate-ci summary ==
  go         pass
  extension  pass
  hook       pass
ALL LEGS PASS
```
All ten steps `exit=0`, and the `hook` leg — the one #592 exists to exercise and which had never
run — reaches `ok 67 no test body asserts an absence with a negated grep (use refute_grep)` with
`hook leg rc=0`. So the sandbox tier (a `cp -r` store copy with no `.git`) and the dev-host tier
now agree on the merged tree.

⚠ **It was obtained by RE-RUNNING the pipeline from the failed run's own spec**
(`kubectl -n tekton-ci get pipelinerun <failed> -o json` → strip `metadata.name`/`status`, set
`generateName`, `kubectl create`), NOT by pushing to trunk. Worth knowing: a trunk PipelineRun is
re-runnable without a commit, so a capacity-starved verdict is recoverable later.

### ✅ RESOLVED — both #591 and #592 were audited post-merge; both are sound, and both leaked follow-ups
Audits run 2026-08-31, one read-only subagent each, against the merged squashes. **Neither found a
reason to revert.** Every finding below was independently re-verified here before filing — two of
the auditors' own numbers were reproduced and one was beaten.

**#591 — the barrier is correct at all eight sites, and its PRECONDITION is unpinned.**
`awaitPushesSettled` is valid only while every push DECISION is reached synchronously before the
awaited call returns (`pushInFlight.Add(1)` runs on the caller's goroutine, `server.go:2010`).
Nothing asserts that. The existing seam ledger (`push_fanout_ledger_test.go:39-43`) pins a
DIFFERENT thing — that `push.Broadcast` has one call site — which a notify helper moved into a
`safeGo` satisfies unchanged. **Measured independently, same test, 20 runs each, mutant = invert
the running-status skip AND wrap `notifyAgentRunning` in `safeGo` (the repo's own idiom, 7 existing
sites):**
```
pre-PR  time.Sleep(20ms)      -> 20/20 CAUGHT
post-PR awaitPushesSettled    ->  0/20 CAUGHT
```
The auditor measured 3/20; I measured 0/20 — same direction, stronger. ⚠ **A LOST TRIPWIRE, NOT A
LIVE BUG:** production decides these synchronously today. But it is not hypothetical — production's
coalescer flush runs on `time.AfterFunc` (`push_task.go:60`) and the tests are synchronous only
because the injected fake fires inline, so the precondition is a property of the TEST SEAM, not of
production. 🔴 My first attempt at this mutant did not compile; it was scored **INVALID**, never a
survivor. → rank 14.

**#592 — the guard reds on the real historical violation, and one of its documented premises is
false in the very file it guards.** Confirmed here: `clawgate-stop-hook.bats:860` and `:907` are
`@test` bodies opening `cat > "$TMP/bin/jq" <<'SHIM'`, while line **1306 of that same file** states
*"No test body in either suite uses one."* ⚠ **LATENT, NOT LIVE — I checked:** neither heredoc
contains a column-0 `}` today, so the scanner is not currently blind. One ordinary edit — wrapping
the shim's logic in a shell function, which puts `}` at column 0 — makes `/^}/ { inbody = 0 }` end
the body at the heredoc's brace and silently switch the scanner off for the rest of it. Both
`inbody = 0` sites (`:1080` the pre-existing sibling, `:1332` the new one) share the rule, so the
older `scan_detached_absences` goes dark on the same body. **Both instruments stay green while
blind**: `EXAMINED > 0` still holds and the `BODIES == grep -c '^@test '` equality still holds.
→ rank 15.

⚠ **Neither PR's `clawgate-ci` ever ran the leg that covers it** — #592's timed out before the
`hook` leg (see the investigation headed **🔴 `TaskRunTimeout` IN clawgate-ci HAS TWO DISTINCT
CAUSES, AND `#572` ONLY FIXES ONE**, in `claudedocs/handoff-tmux-webapp.md`). The bats coverage
claim for #592 rests on
a local reproduction of that leg in the same `docker.io/bats/bats:1.11.1` image, not on CI.

### ⚠ SUPERSEDED (its hypothesis only) — `test_subsystem_store_api.py` HAS RECURRED
🔴 **This corrects the Gotchas bullet that ends "Fixed by devrc#996 (`1b1f71ad`…)" and the
open-investigation heading that says "nothing is fixing it".** #996 did not close it.
🔴 **AND ITS OWN "Leading hypothesis"/"Next probe" ARE NOW REFUTED — see `### ✅ DIAGNOSED` below.**
The recurrence recorded here is real and its ruling-out is sound; only the seed/ordering explanation
and the "pull `fix/xdist-parametrize-values-deterministic`" instruction are wrong.

- **Symptom + exact repro:** `devrc#1162` — a **one-markdown-file** PR — was blocked by
  `tekton/devrc-pytests` on
  `TestTheActorComesFromTheTOKEN::test_a_FORGED_actor_in_the_body_is_DISCARDED[record0-…]`.
  That is the **same case name** recorded from `devrc#1056` in **⚠ SUPERSEDED —
  `test_subsystem_store_api.py` is FLAKY on `main`, and nothing is fixing it**, earlier in this
  file.
- **Observed (with values):** the failure is attached to head `74e39bea`
  (`FAILED: pytests — FAILING: TestTheActorComesFromTheTOKEN…`). The **immediately preceding**
  head of the same branch, `e1d1318f`, failed a DIFFERENT test
  (`test_no_unallowlisted_public_ip_literal_is_committed` — a real defect of mine, since fixed), so
  the two reds are unrelated. After a rebase with no content change beyond that fix, head
  `2fd84888` passed: `TOTAL collected=19942 passed=19939 skipped=3 failed=0`.
- **Ruled out — the diff.** #1162 touches exactly ONE file, `claudedocs/handoff-tmux-webapp.md`.
  A markdown file cannot reach a store-api test. This is the doc's own stated discriminator
  ("the discriminator that settled it was a DOCS-ONLY PR failing"), reproduced.
- **Control, and its LIMIT:** `scripts/tests/test_subsystem_store_api.py` on a clean
  `origin/main` worktree ran **3/3 green — 641 passed each — at 301.6s / 293.8s / 296.8s.** The
  tight spread rules out load inflation *in that run*. 🔴 **But it is a weaker control than it
  looks:** it ran on the DEV HOST while the failure is in the nix **sandbox tier** under CI
  concurrency, so it is a second sample of a DIFFERENT environment, not of the failing one. The
  structural argument (one markdown file) is what actually discriminates here; the 3/3 only shows
  the file is not deterministically broken.
- **Leading hypothesis:** unchanged from the original block — non-determinism that surfaces under
  concurrency, with the failing case MOVING between runs (now three distinct cases observed across
  four runs: `TestEnumerationChannelsAreClosed…`, `TestTrustedProxyOverTheRealProcess…`,
  `TestTheActorComesFromTheTOKEN…`). #996 narrowed it; it did not eliminate it.
- **Next probe:** do NOT re-derive this from the dev host again — it passes there. Reproduce in the
  tier that fails: `nix build .#checks.x86_64-linux.pytests` (ONE derivation at a time — a combined
  invocation produces false failures), repeatedly, and record which case fails each time. If the
  case keeps moving, the seed/ordering hypothesis is confirmed and
  `fix/xdist-parametrize-values-deterministic` (branch exists locally, still no PR) is the thread to
  pull.
- 🔴 **Consequence while it stays open:** devrc is the one repo here with `enforce_admins: true` and
  two required checks, so this blocks arbitrary PRs — including docs-only ones — and the only
  remedy is a fresh push. **A red on this file is not evidence about your diff.** Check the case
  name against the three above before spending any time on it.

### ✅ DIAGNOSED — the store-api gate failure is FSYNC CONTENTION, and the seed/ordering hypothesis is REFUTED
🔴 **This supersedes the two blocks above. Read `scripts/ci-repro/README.md` BEFORE re-pushing or
debugging your diff** — it is the canonical write-up and it is maintained; this block is a pointer,
not a copy.

- **The mechanism, measured:** `server.py:_replace_bytes` fsyncs the file and then the parent
  directory **inside the request, before the response is written**; fsync blocks in uninterruptible
  sleep. When one fsync exceeds `HANG_TIMEOUT` the client raises `TimeoutError` and the gate reports
  a **code failure for an I/O stall**. The suite's own classifier names it unprompted:
  `MECHANISM = SERVER_BLOCKED_IN_FSYNC`. Why CI and not here: `devrc-ci` is pinned to one node, so a
  burst of pushes stacks concurrent runs onto one machine's disk.
- **There is now an on-demand reproducer on the dev host** — `scripts/ci-repro/slowfsync.c`, an
  `LD_PRELOAD` shim, with its own instrument-validation step and a control/reproduction pair. It
  reproduced the identical test with the identical parametrisation as a real CI failure, and was
  independently re-run by an auditor.
- **Three fixes have merged** (verified by content, never by ancestry): `devrc#1181` squash
  `0c333846` (the diagnosis + reproducer), `#1190` squash `634c328a` (a raw reader racing the
  server made 12 assertions report an empty read as a SECOND response), `#1193` squash `48a5540e`
  (the hang guard SAMPLED its own arming instead of waiting for it).
- 🔴 **NOT CLOSED — and deliberately not written as "fixed", per this doc's own shelf-life rule.**
  Measured 2026-09-01 **after all three merged**: `devrc#1197` is red on
  `TestAHungRoundTripSAYSWhichSideBlocked::test_a_stall_in_the_FSYNC_region_is_NAMED` — a **fourth**
  distinct case in this file — while `#1199` passed the same tier. The honest status is: mechanism
  identified and reproducible, three contributing defects removed, **no run of consecutive greens in
  the failing tier yet**.
- 🔴 **Unchanged and still the operative advice: a red on this file is not evidence about your
  diff.** What changed is the remedy — do NOT re-derive an ordering theory, and do NOT open a PR for
  `fix/xdist-parametrize-values-deterministic`.
- ⚠ **Two fixes that look right and are not**, both written up in that README: raising
  `HANG_TIMEOUT` again (60.0 is already the symptom fix, raised from 15, and it did not hold), and
  relocating `nix-store-cache` (the stalling write lands on the step container's **ephemeral layer**,
  which that volume does not cover).

### ✅ RESOLVED — the 0.8.21 deploy, and the image-vs-pin trap it walked into
🔴 **A PIN THAT LANDS AFTER YOUR MERGE DOES NOT MEAN THE IMAGE CARRIES IT.** Measured:
`#611`'s squash `5d11d9a7` merged at **19:57**; another session's `0.8.20` pin `eed7db5a` landed at
**19:58**. Trunk therefore looked like it had shipped the change — and the running 0.8.20 page had
**0** occurrences of `sse:tmux.changed`, against a positive control of 6 other `sse:*`
subscriptions in the same page. 0.8.20's image was built BEFORE the merge. This is the documented
"an image built during review silently omits a fix that landed mid-review" shape (the reason 0.8.12
and 0.8.14 were discarded), reached from the timing side rather than the review side.
**The control that settles it costs one command: run the candidate image locally and grep its
rendered page BEFORE pushing.** Measured for 0.8.21 — 1 occurrence in the image, 0 in live 0.8.20.
Do this instead of reasoning from commit timestamps, which cannot see when an image was built.

### ✅ RESOLVED — four audit rounds on #611, and the attribution gate ended them
Round 1 (full) found the guards were **spelled, not structural**: a decoy attribute carrying the
same string let a non-subscribing panel pass the entire Go suite. Round 2 found the FIX introduced
a regression — the AST rewrite traded a walkable-but-spelling-agnostic check for a
precise-but-literal-only one, losing a shape `AutoApproveBanner` already uses deliberately
(`trigger := "…"; hx("hx-trigger", trigger)`); the PRE-fix guard caught it and the post-fix one did
not. Round 3 found the fix's own comment overclaimed. Each round found a real defect created by the
previous round's fix — three times consecutively.
🔴 **The ladder was ended by the ATTRIBUTION GATE, not by a clean round**: round 2's fixes changed
0 executable payload lines (27 comment lines in payload files — ambiguous), round 3's changed 0
payload files at all. Two consecutive zero-payload rounds ⇒ the ladder had left the PR and was
auditing scaffolding it had itself written. The final comment corrections were made directly rather
than as a round 4, because re-auditing a comment edit is the loop the gate exists to stop.

### ✅ RESOLVED — `open_window`'s read-back rejected a window it had already created

Fixed in `e6770754` (parse) + `17ec867f` (the guard for it). Kept because the
mechanism is worth not re-deriving.

- **Observed (with values):** `NEW_WINDOW_FORMAT` is
  `#{pane_id}\t#{session_name}\t#{pane_current_path}`; the read-back did
  `out.strip().splitlines()[0].split("\t")`. `.strip()` removes a TRAILING TAB, so
  a well-formed 3-field line with an empty last field became 2 fields and failed
  the length check — rejecting a window tmux had already created, leaving a stray
  window, and a retry would create a second. `'%2\tscratch20\t'.strip()` is
  byte-identical to the observed failure string `'%2\tscratch20'`.
- **Ruled out — load flake:** wall times of failing and control runs within 0.5 s;
  other tests in the same run did not move. `via: measurement`
- **Ruled out — caused by the arming change:** reproduced on an unmutated tree;
  `scripts/tmux-reply-agent` had zero changed lines in the audit ranges. `via: measurement`
- **Ruled out — tmux genuinely emitting two fields:** the trigger was FORCED —
  11 empty third fields in ~310 warm-server `new-window` creations (~3.6%), 0 in
  60 cold-server creations, and in 11/11 the immediate `display-message -p -t
  <pane>` re-read returned the correct path. `via: measurement`
- 🔴 **The second bug, which the first was hiding:** with the fields split
  correctly `landed_path` is `""`, and `same_directory("", cwd)` does NOT compare
  empty against cwd — `os.path.realpath("")` returns the AGENT'S OWN cwd
  (verified), and the unit runs `WorkingDirectory=~`. "Not readable yet" and
  "tmux fell back to the wrong directory" were sharing a code path.
- **Fix:** split the line before stripping; re-read an empty path for that pane;
  refuse if the re-read is also empty. Both halves pinned; the two previously
  flaky real-tmux tests then ran **20/20 clean**.
- **Residual, NOT fixed:** no refusal path kills the window it created (true of
  the session-mismatch and directory-mismatch branches before this PR too), and
  these branches report `state="failed"`, not `"refused"` — an audit query on
  `state='refused'` will not see them.

### ✅ RESOLVED — `devrc-ci`'s pytests leg was RED ON `main` ITSELF, from a nixpkgs lockfile bump

🔴 **Root cause: the flake lockfile bump `cb86343d` (#1382, 2026-09-07 22:55) moved two
nix-provided tools out from under pins the repo asserts on.** No PR caused any of the 7
failures. Fixed by **#1392** (`fix/toolchain-drift-2026-09`), squash `94f82796`, merged
2026-09-08T18:26:46Z — verified on `origin/main` **by content, not ancestry**:
`PINNED_VERSION = "1.18.29"` in `scripts/tests/test_opencode_engine.py` and
`classify_age_refusal` present ×2 in `scripts/analyze-service-index/restore-verify.py`.

| tool | flake.lock before | after (dev shell **and** nix sandbox) | what it broke |
|---|---|---|---|
| `opencode` | 1.18.21 | 1.18.29 | 1 failure — the version pin |
| `age` / `age-keygen` | 1.3.1 | 1.3.2 | 6 failures — escrow/backup guards keyed to 1.3.1's behaviour |

**Both are working gates, not bad tests.** The version assertion's own docstring already
carried the remedy: *"If flake.lock genuinely moved opencode, do NOT just bump
PINNED_VERSION: re-derive the header's measurements against the new binary first."*
#1392 did exactly that.

**Verified on the MERGED tree, because #1392's own green ran on a branch 2 commits behind
`main`** and that is a claim about the branch, not about what merging produces. Built the
integration tree (`f8641223` + `origin/main`, clean merge) and ran the authoritative tier
`nix build .#checks.x86_64-linux.pytests`:
`PASS scripts/tests (collected=12923 passed=12923 skipped=0 failed=0)` — the exact target
that was `FAIL … failed=7` — and `TOTAL collected=20968 passed=20966 skipped=2 failed=0`,
28/28 per-target floors, `RESULT: PASS (exit=0)`. 🔴 **The anti-vacuity check is the
collected COUNT: 20948 → 20968.** A merged tree that still collected 20948 would have been
the branch tree again, and the pass would have meant nothing.

The evidence below is preserved as recorded — the three-way control and its ruling-out are
sound and are what made this attributable. Only the two bullets that guessed at a mechanism
are corrected in place.

- **Symptom + exact repro:** the `tekton/devrc-pytests` check fails on any devrc PR.
  The gate's own summary line reads
  `FAIL  scripts/tests  (collected=12863 passed=12856 skipped=0 failed=7 errors=0)`
  against `TOTAL collected=20908 passed=20899 skipped=2 failed=7`.
- **Observed (with values):** two distinct error texts in the failing target, both
  from nix-provided tools:
  - `AssertionError: opencode on PATH is '1.18.29', but every 'measured on v1.18.21'
    claim in scripts/opencode/opencode.jsonc, scripts/opencode/README.md and
    scripts/tests/test_opencode_config.py is keyed to '1.18.21'.` → `assert '1.18.29' == '1.18.21'`
  - `age-keygen: error: failed to parse input: error at line 3: unknown identity type`
    (6 occurrences), from
    `/nix/store/b8mq9lqr30vlmx661xhp0cwvhyns29p6-age-1.3.2/bin/age-keygen -y …`
- **Ruled out — my change.** A THREE-WAY control, all on node `talos-xr6-r7p`, all
  byte-identical at `failed=7`, opencode-drift 1, age-keygen 6:
  `devrc-ci-zkcb2` rev `fec498fb5` (main + one markdown file) · `devrc-ci-rerun-ho`
  the same commit re-run from its own spec · **`devrc-ci-ctrl-main` rev `18bc15004`
  = `main` itself, without my commit.** A docs-only diff cannot move an opencode
  version pin. `via: measurement`
- **Ruled out — the node.** All four runs above ran on `talos-xr6-r7p`, and
  `devrc-ci-sljm8` **succeeded** on that same node 7 minutes before mine with
  `failed=0`. So this is not rank 18's device-isolated I/O contention.
  `via: measurement`
- **Ruled out — a flake.** The same commit re-run from its own spec produced the
  identical failure counts. Deterministic, not timing. `via: measurement`
- ⚠ **CORRECTED — the "NOT EXPLAINED" above was a WRONG-FILE error, not a real
  local/CI divergence.** The bullet reasoned from `python3 -m pytest
  scripts/tests/test_opencode_config.py` passing **640/640** locally. That file was
  never the one failing: the assertion lives in
  **`scripts/tests/test_opencode_engine.py::test_engine_is_the_version_every_measurement_is_keyed_to`**,
  a different module. Run the right one and it reproduces on the dev host in under a
  second — `assert '1.18.29' == '1.18.21'`, byte-identical to CI's text. There was no
  divergence to explain. `via: measurement`
- ⚠ **The "leading hypothesis" it produced is therefore REFUTED, and it was the
  expensive kind: plausible, self-consistent, and pointing at the wrong layer.** It
  proposed that the gate's nix shell put different binaries on PATH than an
  interactive shell. False for opencode — both are **1.18.29** — and the nix-shell
  difference is real only for `age` (login shell 1.3.1, dev shell 1.3.2), which is a
  *second* cause, not the explanation for the first.
  🔴 **The reusable tell: a failure text quotes a MODULE, and the doc quoted a
  FILENAME the reader supplied from memory.** Read the failing test's fully-qualified
  node id out of the gate output and run *that*, before theorising about the
  environment. An environment theory built on a wrong-file control is a second sample
  of nothing.
- 🔴 **Both hand-created control PipelineRuns have served their purpose and can be
  deleted:** `devrc-ci-ctrl-main` and `devrc-ci-rerun-ho` in ns `tekton-ci`. They are
  evidence, not scheduled work.
- **What actually generalises, and is worth carrying:** a lockfile bump in this repo
  is a **behaviour change to every version-pinned guard**, and those guards are
  deliberately environment-dependent. When `devrc-pytests` goes red across unrelated
  PRs at once, check `git log -1 flake.lock` before reading any diff.

### ✅ RESOLVED — the wide-kill guards red-lined devrc `main` THREE times in one day, and only one was a real defect

- **Symptom + exact repro:** `tekton/devrc-pytests` red on unrelated devrc PRs.
  `git worktree add --detach /tmp/x origin/main && (cd /tmp/x && python3 -m pytest
  scripts/claude-hooks/tests/test_guard_core.py -k kill -q)`
- **Observed (with values):** three separate reds, three different causes.
  1. `handoff-tmux-scratchpad-bar-statusline.md` — classified upstream by another session
     while I was working. My own added entry was a **duplicate dict key silently shadowing
     theirs**; the mutation sweep is what exposed it (dropping mine changed nothing).
  2. `handoff-tmux-webapp.md` — a **FALSE positive**. `_MENTION_RE = r"kill-s(?:erver|ession)"`
     had no left word boundary, so it matched inside **s·kill-session**, from the claim slug
     `clawgate-skill-session-verbs`. Fixed in **#1534** (`7344e76f`) with
     `(?<![A-Za-z0-9_])`. Measured across every tracked file: **14 → 13** files, removing
     exactly the false positive, matching nothing new.
  3. `handoff-mention-system-repos.md` — a **TRUE** positive, and the one that proved the
     pattern: its offending line is *documenting this very guard*. Writing about the guard
     tripped the guard.
- **Ruled out — "these are unrelated flakes".** Each was reproduced on a clean `origin/main`
  worktree with no PR involved. `via: measurement`
- **Ruled out — "classify each one as it appears".** Composition at the time: **11 of 13**
  ledger entries were real `scripts/` call sites; **all three** failures came from the
  2-entry `claudedocs/` half. That is the permanently-red-gate shape — every PR in the repo
  blocked on an unrelated doc. `via: measurement`
- **The fix (operator's call, 2026-09-12):** scope the LEDGER to executable text; prose stays
  guarded by `test_no_tracked_shell_text_writes_a_kill_this_guard_would_deny`, which scans
  claudedocs too but matches only real shell-command shape and runs each hit through
  `check_tmux_kill_shared_server`. **PR #1549.**
- 🔴 **My first coverage control was VACUOUS and is worth not repeating.** I planted a wide
  kill in `claudedocs/handoff-comic-flex.md` and the guard passed — I could have written
  that up as "narrowing loses nothing". That file **does not exist in this repo**; `git`
  said so in the same output, the scanner skipped it, and the mutant never ran. Redone
  against a file `git ls-files` returns: guard goes **RED**, tree restored byte-identical.
  `via: measurement`
- **Next probe:** none — #1534 merged, #1549 open. If a fourth doc-mention red appears after
  #1549 lands, the scoping did not hold and the exclusion list is wrong.

### ✅ RESOLVED 2026-09-12 — card 517's delivery axis WAS driven, and the axis sticks at `queued`

- **What was run:** scratch pane `%77` (workbench, tmux `scratch15` w6, `/tmp`, plain `zsh`) +
  entry **13317**; a second entry **13337** bound to `%999999` for the failure branch. Both
  resolved, window killed, browser tab closed. No real session's reply control was touched, and
  the operator's screen was never taken (`wake` only, never `activate`).
- **Observed (with values):** the pane received `scratch 517 delivery axis probe` and the shell
  ran it (`scratch: command not found`) — typed **and** Enter pressed. Queue row
  `4mvvGhpX_qxjXiGosgSnLw`, tier `browser`: created→claimed **1.81s**, created→completed
  **2.65s**, `delivered`. Rendered `ready` → `queued` @ **+1.17s**, then **unchanged for 50s**;
  `sent` only after a reload. The failure arm: terminal `failed` @ **+0.17s**, rendered `failed`
  @ **+1.79s**, notice *"NOT delivered. The pane did not receive…"*.
- **Ruled out:** *the state is an optimistic client flip.* At +1.17s the row was still `pending`
  and the control said `queued`, not delivered — criterion 2's discriminating case, live.
  via: measurement
- **Ruled out:** *the `queued` freeze is a wrong state mapping.* A reload of the same entry
  rendered `sent` from the same row. The mapping is right; nothing re-reads it. via: measurement
- **Ruled out:** *the first (trusted-click) attempt failed because the feature is broken.* It
  enqueued nothing because no POST was made — 0 `/ui/term` against 25 `GET /ui/attention` as a
  positive control — the panel having reverted the `hx-confirm` removal before the click.
  via: measurement
- **Leading hypothesis:** no broadcast exists for a termwrite state change, so the single
  post-POST refetch is the only read and usually precedes the outcome. See rank 58.
- **Next probe:** rank 58's closing condition.

### ⚠ SUPERSEDED by the entry above — card 517's delivery axis — authorised, built, never exercised

- **Symptom + exact repro:** the reply control's delivery states cannot be observed without
  submitting a reply, and submitting types into a real tmux pane. Load `https://clawgate.zacx.dev`,
  click through to the tmux page, inspect `[data-reply-state]`.
- **Observed (with values):** cross-site load → `data-reply-state="disabled"` with the server's own
  reason, `Sec-Fetch-Site: cross-site. A reply from here would be refused.` Same-site load →
  `data-reply-state="ready"`, option buttons NOT disabled. Harness built and then destroyed:
  pane `%75` (workbench, `scratch13` window 2, cwd `/tmp`, via `term launch`, state `delivered`)
  and attention entry `13178` (`kind=question`, `priority=low`), resolved `06:17:43Z`.
- **Ruled out:** *the `disabled` state is an optimistic client-side flip.* It is server-computed —
  it carries a server-authored refusal naming the `Sec-Fetch-Site` header, and it changes with the
  navigation rather than with any click. via: measurement
- **Ruled out:** *517 is untestable because the controls are always disabled.* Changing only the
  navigation to same-site flipped it to `ready`. via: measurement
- **Ruled out:** *the delivery axis can be reached without a write.* It cannot — the criterion is
  "selecting a reply option changes the rendered state", so selecting one is the measurement.
  via: code
- **Leading hypothesis:** the axis works; nobody has driven it. The cheapest proof is one reply
  into a disposable pane, watching `data-reply-state` and then confirming the text ARRIVED in the
  pane — a `sent` that never lands is the defect the card exists to catch.
- **Next probe:** rebuild the harness and dispatch the annotated brief:
  `clawgatectl term launch --host workbench --cwd /tmp --text '<label>'`, resolve the NEW pane id
  from `clawgatectl tmux windows` (disambiguate by host + window index — the codename is not
  unique), `clawgatectl attention raise --host workbench --tmux-pane '%<new>' --kind question
  --priority low --title '<scratch>'`, then update the ids in
  `.opencode-dispatch/tmux-ui-verify/brief4-NOT-DISPATCHED.md` and run it.

### `tekton/clawgate-e2e` is TIMING OUT on `trunk` — a gate going permanently red
- as-of: 2026-09-13

- **Symptom + exact repro:** `tekton/clawgate-e2e` reports FAILURE; the TaskRun ends
  `TaskRunTimeout`, "failed to finish within 40m0s", with steps `e2e` and `verdict` both exit 1.
  Reproduce: `KUBECONFIG=$KC_HOMELAB kubectl get pipelinerun -n tekton-ci | grep clawgate-e2e`.
- **Observed (with values):** `becedef44` **Succeeded** 21:59Z · `1a3bdef6e` (#813 on trunk)
  **Succeeded** 01:04Z · `189451188` (#817 head) **Failed/TaskRunTimeout** 01:32Z · `24be212e7`
  (the 0.8.34 pin bump — a TWO-LINE version change) **Failed/TaskRunTimeout** 01:58Z. Box loadavg
  47–54 throughout.
- **Ruled out:** *#817 caused it.* It touches 8 files, all Go `_test.go` under `internal/`/`cmd/`,
  **zero** e2e/TypeScript; `clawgate-e2e` runs Playwright, which never executes them. via: measurement
- **Ruled out:** *a test assertion is failing.* Both failures are `TaskRunTimeout` at the 40m task
  budget, not an assertion. via: measurement
- **Ruled out:** *leaked local postgres containers are still holding resources.* Five orphaned
  `clawgate-e2e-pg-*` (aged 5–30 h) were removed this session after a **validated** probe —
  0 client connections each, with a positive control proving the probe reads 1 when a connection is
  deliberately open. Load moved 52 → 47.7. via: measurement
- 🔴 **NOT RULED OUT, and the subsystem store flagged it against me: a STRANDED ADVISORY LOCK, not
  load.** `homelab-talos/clawgate` carries an `OPEN:` bullet (2026-09-08) titled *"A RED
  `clawgate-e2e` CARRYING `SQLSTATE 57014` ON MIGRATE IS NOT AUTOMATICALLY LOAD — AND THE HARNESS'S
  OWN TEXT PUSHES YOU THE WRONG WAY"*: a stranded session lock on a POOLED connection after a ctx
  cancel makes the next migration die at the 10 s `statement_timeout`, an observable identical to
  contention. It records that the harness's own *"startup slowness under node contention"* string is
  **editorialising**, and that it already led one session to misfile the whole thing as saturation.
  The rival fix (#503/#509) shipped with **no version pin bumped**, so a stale binary is possible.
  **I did not discriminate**: the `step-e2e` pod for `clawgate-e2e-9g5m6` was reaped before I looked,
  so I never checked whether these runs carried `SQLSTATE 57014` at all. via: assumed
- **Leading hypothesis:** cluster/box saturation lengthens the Playwright run past the 40m TaskRun
  budget — the spec carries documented load sensitivity (fixture setup clamped to 45 s) and the same
  cluster timed out a **comment-only** commit. 🔴 **Held weakly, and NOT to be repeated as a
  finding**: it is the exact conclusion the store's open bullet warns is reached by reading the
  harness's own prose, and the two mechanisms share this observable. The discriminator is whether
  `57014` appears on migrate and whether an advisory-lock WAIT is present — read the step logs
  BEFORE the pod is reaped.
- **Next probe:** 🔴 **capture the `step-e2e` logs BEFORE the pod is reaped** — that is what makes
  this discriminable, and it is what tonight lost:
  `KUBECONFIG=$KC_HOMELAB kubectl logs -n tekton-ci <e2e-pod> -c step-e2e --tail=-1 | grep -aE '57014|statement timeout|advisory'`
  A hit ⇒ the stranded-lock mechanism, and check whether the harness's clawgate binary carries
  #503/#509 (no pin was bumped, so version alone will not tell you). A clean miss under a QUIET box
  ⇒ the 40 m budget or the spec is the defect. Then trigger a run and read the reason, not the colour:
  `KUBECONFIG=$KC_HOMELAB kubectl get pipelinerun -n tekton-ci -o json | python3 -c 'import json,sys;[print(i["metadata"]["name"],(i.get("status",{}).get("conditions") or [{}])[0].get("reason")) for i in json.load(sys.stdin)["items"] if "clawgate-e2e" in i["metadata"]["name"]]'`
  If it times out on a quiet box, the 40m budget or the spec is the defect, not the load.

### ✅ RESOLVED 2026-09-14 — rank 61 `clawgate-e2e` `TaskRunTimeout` is NODE I/O, not a stranded lock

- as-of: 2026-09-14
- Supersedes **`tekton/clawgate-e2e` is TIMING OUT on `trunk` — a gate going permanently red**,
  earlier in this file — its next probe is spent.
- **Evidence:** `claudedocs/refs/clawgate-e2e-tasktimeout-node-io.md`
- **Ruled out:** a stranded `pg_advisory_lock` — `57014` hits the migration DDL, not a lock wait, and
  the checkpointer's own fsync ran 130.6s/37.8s/35.2s/32.1s. via: measurement
- **Ruled out:** a code defect — rev `24be212` on an idle node: 224 passed, 7.5m vs a 40m timeout.
  via: measurement
- ✅ **ALL THREE STEPS BELOW ARE DONE — verified 2026-09-14.** `ZacxDev/homelab-infra#819` MERGED
  2026-09-14T04:51:27Z; `claim-work --list` shows **no** `tmux-webapp-61`; rank 18's follow-on
  `#820` MERGED 2026-09-14T15:56:37Z and rank 18 is evicted. 🔴 Read `#820` before trusting any
  mechanism recorded above it: its own title says **the recorded root cause has INVERTED**.
- ~~**IN FLIGHT `ZacxDev/homelab-infra#819`**; claim `tmux-webapp-61` HELD.~~ `Next steps`
  deliberately NOT rewritten — a REPLACE section, so editing rank 61 would re-point every live claim.
- ~~**Next probe:** merge #819, release the claim, then rank 18 — same mechanism, `clawgate-ci`.~~

### ✅ RESOLVED 2026-09-14 — rank 18 `clawgate-ci`, and the burst-preference arc is CLOSED by operator decision

- as-of: 2026-09-14
- **Shipped:** `ZacxDev/homelab-infra#820` (rank 18, squash `d40b45a34`) and `#821` (squash
  `112b52c60`). Rank 18 is evicted to `claudedocs/refs/tmux-webapp-closed-ranks.md` in this same
  change — it survived the first sweep only because it was still open then.
- **Ruled out:** rank 18's recorded root cause — "device-isolated to `talos-uvh-gtj`, a Crucial M500
  at ~90 ms per 4 KB fsync, 0-pass/14-fail". **It has INVERTED.** Re-measured over the 20 retained
  gate taskruns: uvh-gtj **12/12**, `talos-xr6-r7p` **3/6**, io-stall 0.018 vs 0.127 the same minute.
  Following that diagnosis would have meant excluding the healthiest node. via: measurement
- 🔴 **RETRACTED, and it was MINE:** #819/#820's comments, both commit messages, both PR bodies and
  the subsystem store all said `devrc-ci` is **PINNED** to xr6-r7p by a node-local RWO
  `nix-store-cache` PVC. **False when written.** The live `devrc-ci-template` has no `nodeSelector`,
  `NotIn [talos-jkj-deb]` only (four candidates), no preferred rule and one `source` workspace;
  `grep -l claimName` over `triggers/` returns exactly one real hit (gitops-validate on
  `nix-store-cache-2`). Fixed by #821. The 62%-of-1703-pods placement is real; **its mechanism is
  UNDIAGNOSED** — do not restate the PVC story. via: measurement
- 🔴 **THE PREFERENCE IS WEAKER IN PRODUCTION THAN THE PROBE SUGGESTED, AND I GENERALISED FROM ONE
  POINT.** Paired probes against both deployed templates gave 3/3 (`with pref → tekton-ci-1`,
  `control → talos-xr6-r7p`) — but they ran while the burst node was **idle**. `preferred` is a
  SCORING WEIGHT, not a constraint. On the organic runs after both merges, **3 of 4
  preference-carrying runs still landed on `talos-xr6-r7p`**, because `tekton-ci-1` (7.95 CPU) sat at
  **78% requested / 9 pods** and `clawgate-e2e` + `clawgate-ci` fire *simultaneously* on one push —
  two ~2 CPU pods against ~1.7 CPU of headroom. **Measure a preference at both points (burst node
  idle AND full) before quoting its effect.** via: measurement
- **What held:** every post-fix run succeeded; no `TaskRunTimeout` has recurred. ⚠ **The margin is
  thin** — `clawgate-e2e-b9qfk` (the #819 merge commit, no preference, on xr6-r7p) took **35m30s
  against the 40m budget**. "The timeouts stopped" is true; "comfortably" is not.
- 🔴 **OPERATOR DECISION 2026-09-14 — ACCEPT AND STOP.** Do **not** grow `tekton-ci-1`, and do **not**
  chase why an unpinned `devrc-ci` picks xr6-r7p 62% of the time. The preference helps at the margin
  and costs nothing. **Re-open only on a new `TaskRunTimeout`** — that is the trigger, not a hunch
  about the node. Three options were measured and offered (grow the burst node / reduce what lands on
  xr6-r7p / accept); this is the chosen one, so a later session should not re-derive the other two.
- ⚠ **Not swept, deliberately:** the "NO nodeSelector, deliberately" blocks in `clawgate-e2e`,
  `clawgate-ci` and `clawgate-ux-audit` still carry the retracted PVC story, and one asserts
  `grep -n claimName` returns "exactly two non-comment hits" when it returns one. #821 points at it
  rather than widening. That is the one piece of this arc left undone, and it is doc-rot, not risk.

### 🔴 A NARROWED e2e RUN WAS QUOTED AS "THE TIER" TWICE, AND IT LEFT `trunk` BROKEN ONCE
- as-of: 2026-09-15
- **Symptom + exact repro:** `containers/clawgate/e2e/tests/tmux-page.spec.ts` — all 9 tests fail. Repro: `cd containers/clawgate && ./e2e/run.sh tmux-page.spec.ts`.
- **Observed (with values):** on `#826` head `04cef09c3` → `Expected: 1, Received: 18` at `tmux-page.spec.ts:156`, the shared `openTmuxTab` helper asserting `toHaveCount(1)` on `#panel-tmux [data-session-view-choice][aria-pressed="true"]`. Item 3 made that count the CARD count. `tekton/clawgate-e2e` reported `e2e failed: 9 failed, 216 passed, 2 skipped` on that head.
- **Observed — the DISCRIMINATING CONTROL:** clean `origin/trunk` worktree (`#824` merged, none of `#826`) run of the same spec → **4 failed / 5 passed**, the SAME four. So four of the nine predate `#826` and are `#824`'s damage, already on `main`.
- **Ruled out:** "`#826` caused all nine" — the trunk control failed 4 without any of `#826`'s changes. `via: measurement`
- **Ruled out:** "CI cannot see this" — an audit asserted CI runs no e2e; **FALSE**. `tekton/clawgate-e2e` is a SEPARATE check from `clawgate-ci` and caught it. The clawgate skill documents exactly that. `via: measurement`
- **Ruled out:** "expand every group in `openTmuxTab`" as the fix — a page-wide sweep clicks groups in `hidden` host panels and times out; **measured 7 failures instead of 4**. Must be scoped to `[data-tmux-host-tab-panel]:not([hidden])`. `via: measurement`
- **Leading hypothesis:** RESOLVED. Cause is `#824`'s collapsed-by-default: cards are present-but-not-visible, so `.fill()` times out and `intersect once` never fires for the lazy transcript mount. `6fad0e252` fixes all 9 and therefore repairs `main`'s pre-existing break.
- **Next probe:** `gh pr checks 826 --repo ZacxDev/homelab-infra` — confirm `tekton/clawgate-e2e` is green on `6fad0e252` before merging.

### 🔴 TWO GUARDS I WROTE WERE WALKABLE, AND THE PR BODY CITED ONE AS PROOF
- as-of: 2026-09-15
- **Symptom + exact repro:** both guards passed while the thing they claimed to protect was broken.
- **Observed (with values):** (a) item 2's check was `strings.Contains(htmlSrc, "tmux session(s)")` — one literal phrase. Re-adding the header as `<h2>{host}</h2><span>45 windows / 3 sessions</span>` restores the exact duplication and **SURVIVED**. (b) the `data-tmux-host-fresh` "survivor" arm used `strings.Contains(htmlSrc, "data-tmux-host-fresh")` — and `renderTmuxString` renders `tmuxGroupScript`, whose prune guard SPELLS that attribute in a `getAttribute()` call. **Renaming the emitted attribute on the strip left the test GREEN.**
- **Ruled out:** "the arms are fine, the audit misread them" — both mutants were run and both survived. `via: measurement`
- **Leading hypothesis:** RESOLVED in `6fad0e252`. Both now parse the DOM; both mutants die by their own guard's message; control green.
- **Next probe:** none. Recorded because the shape recurs — **a substring match over a whole rendered page can match the JS that READS an attribute rather than the markup that EMITS it.**

