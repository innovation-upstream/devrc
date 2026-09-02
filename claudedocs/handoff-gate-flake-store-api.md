# Handoff: gate-flake-store-api — 2026-09-01

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
Stop `tekton/devrc-pytests` failing PRs whose diff cannot reach the failing test, by
removing the tests' dependence on disk latency rather than by tuning bounds. Distinct
from `handoff-ci-speedup.md`, which is about gate SPEED and is owned elsewhere.

## State now
🔴 **`devrc#1219` MERGED — squash `b4fde334`, 2026-09-02T16:48:50Z, by `ZacxDev` — and it merged
WITHOUT the round-6 delta audit that rank 5 existed to run.** The previous version of this doc
described `#1219` as OPEN because the doc's own PR (`#1231`) merged **17 minutes later**, at
17:05:55Z. The doc was born stale; a session resuming on it was handed a completed item.

- devrc `main` @ `220e7893`. Base clone was 2 behind and has been fast-forwarded (no collision:
  the 2 incoming commits touch none of the 5 dirty paths). ⚠ Another session's dirty
  `nix/programs/alacritty/default.nix`, `nix/system/apply-tmp-churn-retention.sh` and three
  untracked `diagnose-*`/`output.txt` files are still in the shared checkout — untouched.
- Claim held: `gate-flake-store-api-5` (`claim-work --release gate-flake-store-api-5` when done).

**What rank 5 asked for, against what happened:**

| step | status |
|---|---|
| post the round-6 claims block | ❌ never posted — **now posted late**, `#1219` comment `5513658674` |
| audit the delta `1eafc40c..734cb0bc` | ⏳ **IN FLIGHT** — dispatched this session, worktree-isolated, report-only |
| re-run both gate tiers on the merged tree | ✅ satisfied post-merge by Tekton's `main` pipeline |
| merge | ✅ done |

🔴 **THE MERGED TREE IS GREEN ON BOTH REQUIRED TIERS — measured on the merge commit itself,
not on the branch.** `b4fde334`: `tekton/devrc-main-pytests collected=20491 passed=20488
skipped=3 failed=0` (floor 18404); `tekton/devrc-main-nodetests tests=1449 pass=1449 fail=0`
(floor 1367). Identical on the current head `220e7893`. This is *stronger* than the branch-green
the doc worried about — `strict:false` meant the branch check never covered the merged tree — so
the outstanding item is **an unclosed audit ladder, not a known-broken `main`.**

🔴 **The branch head IS the live content — verified by blob OID, not assumed.** All five files
are byte-identical between `734cb0bc` (branch, a MERGE commit) and `b4fde334` (squash on main):
`store_siting.py` `1d968d51`, `test_store_siting_ledger.py` `abad1584`,
`test_subsystem_store_api.py` `673a70be`, `test_cairn_write.py` `8153a785`,
`test_cairn_cli.py` `f9c932aa`. So auditing the branch range audits what is deployed.

**Round-6 delta, correctly sized.** `734cb0bc` is a merge commit (parents `1eafc40c` and
`a6e50641`), so the naive `git diff 1eafc40c..734cb0bc` reads 30 files / +7267 — almost all of it
main flowing in, authored by no round. The round-6 **authored** delta is ~186 lines:
`store_siting.py` +28, `test_store_siting_ledger.py` +158, plus a slice of
`test_subsystem_store_api.py` that must be separated from main's own heavy movement of that file.

**No clawgate task recorded.** `clawgate_handoff.sh resolve` exited **5** — 0 tasks for this
session. An unknown session id answers `200` with an empty array, so that cannot distinguish
"touched no task" from "wrong id". No `clawgate-task:` field written; this is not a clean bill.

## Open investigations — live diagnosis state

### The hung-server classifier matches the CHECKOUT PATH, so it can be confidently wrong
Found by accident while doing the tmpfs work. Documented on `main` in
`scripts/tests/test_subsystem_store_api.py` above `_HUNG_SERVER_RULES`; **not fixed.**

- **Symptom + exact repro:** create a worktree whose path contains `fsync`, then run
  `nix develop ~/workspace/devrc -c env PYTHONDONTWRITEBYTECODE=1 python3 -m pytest
  <that worktree>/scripts/tests/test_subsystem_store_api.py -k
  "test_a_stall_on_the_ENTRY_LOCK_reads_DIFFERENTLY" -q`. It fails asserting
  `MECHANISM = SERVER_BLOCKED_ON_ENTRY_LOCK`, reporting `SERVER_BLOCKED_IN_FSYNC`.
- **Observed (with values):** `_HUNG_SERVER_RULES` is a tuple of substring tokens
  (`fsync`, `flock`, `_EntryLock`, `_audit_lock`) matched against
  `"".join(traceback.format_stack(frame))`. `format_stack` renders each frame's
  FILENAME, so every frame of a checkout at `/home/zach/workspace/devrc-fsync/...`
  contains `fsync` and the first rule wins unconditionally. Measured: identical tree
  at `devrc-fsync` → 1 failed; at `devrc-storetmp` with `__pycache__` cleared →
  4 passed.
- **Ruled out:** *"the tmpfs change broke that test"* — the identical tree passes at a
  path without the token. via: measurement
- **Ruled out:** *"the path theory is refuted because the renamed tree still failed"* —
  that control was CONTAMINATED. `git worktree move` preserves mtime+size, so the
  stale `__pycache__` revalidated and the code objects kept the OLD `co_filename`; the
  frames still rendered `devrc-fsync`. Clearing `__pycache__` is what made the control
  honest. via: measurement
- **Leading hypothesis:** none needed — the mechanism is read directly from the code.
- **Next probe:** none for diagnosis; this is the fix. Scan the frames' SOURCE LINES
  rather than their filenames. The classifier has its own tests
  (`TestAHungRoundTripSAYSWhichSideBlocked`), so they must be re-run, and the fix
  must be shown RED at a `devrc-fsync`-style path before and GREEN after.

### Whether the tmpfs siting actually reduces the flake rate — UNMEASURED
- **Symptom + exact repro:** none yet; this is an open measurement, not a bug.
- **Observed (with values):** disk vs tmpfs, replaying `_replace_bytes`'s sequence
  (mkstemp → write → fsync file → `os.replace` → fsync dir), MAX reported because
  `HANG_TIMEOUT` is breached by a single worst-case call:
  idle — disk median 6.562ms / MAX 12.431ms, tmpfs median 0.017ms / MAX 0.140ms;
  under 3 concurrent fsync writers — disk median 11.725ms / MAX 17.843ms,
  tmpfs median 0.011ms / MAX 0.090ms.
  In the nix build sandbox `TMPDIR=/build` is ext2/ext3 while `/dev/shm` is tmpfs and
  writable (probed). CI builds UNSANDBOXED — its traceback shows `/tmp/nix-build-…`,
  not `/build`.
- **Ruled out:** *"raise HANG_TIMEOUT"* — banned in-file, with the per-hung-call
  arithmetic beside it. via: doc
- **Ruled out:** *"remove one of the fsyncs"* — `_replace_bytes`'s docstring argues the
  directory fsync is not redundant: without it a node losing power after `os.replace`
  can come back with the old name on the old inode, having already answered
  `200 appended`. via: code
- **Ruled out:** *"CPU/memory requests"* — they govern CPU and memory, not IOPS. via: doc
- **Leading hypothesis:** the flake rate on `test_subsystem_store_api.py` drops to ~0
  where a tmpfs is available, and is unchanged where the fallback fires. **Untested.**
- **Next probe, verbatim:** after ~2 weeks, count store-api failures across recent PRs
  and compare to the 5-in-14 baseline recorded above:
  ```bash
  for p in $(gh pr list --repo innovation-upstream/devrc --state all --limit 40 --json number --jq '.[].number'); do
    gh api "repos/innovation-upstream/devrc/commits/$(gh pr view $p --repo innovation-upstream/devrc --json headRefOid --jq .headRefOid)/status" \
      --jq '[.statuses[]?|select(.context=="tekton/devrc-pytests")|select(.state=="failure")|.description]|first' 2>/dev/null
  done | command grep -c "subsystem_store_api\|TestTheActor\|TestAHungRoundTrip\|TestTheBackstop"
  ```

### The store-siting fix is WIDER than the baseline rank 1 would measure it against
Not a bug — a measurement that would mislead if run as written.

- **Symptom + exact repro:** run rank 1's probe today and compare to the recorded
  baseline (5 store-api failures among 14 open PRs, 2026-09-01). A flat rate would read
  as "the fix did nothing".
- **Observed (with values):** the baseline was taken when `#1211` had sited **one** file
  (`test_subsystem_store_api.py`'s `store` fixture) of three. Since then `#1219` sites
  all three plus `scoped_store` (which alone feeds ~110 `running(...)` sites), and the
  audit measured the population it had missed. So baseline and post-fix state differ by
  more than time.
- **Ruled out:** *"the baseline is still comparable"* — it was measured against a suite
  where 2 of 3 files and the largest fixture were still disk-backed. via: measurement
- **Ruled out:** *"one green CI run shows the fix works"* — the gate validating a gate
  fix is not independent evidence; a run that would not have flaked anyway is
  indistinguishable. via: assumed
- **Leading hypothesis:** the rate should drop materially once `#1219` merges, and
  measuring before it merges measures the partial state.
- **Next probe, verbatim:** measure AFTER `#1219` merges, and record the new baseline
  date alongside the count:
  ```bash
  for p in $(gh pr list --repo innovation-upstream/devrc --state all --limit 40 --json number --jq '.[].number'); do
    gh api "repos/innovation-upstream/devrc/commits/$(gh pr view $p --repo innovation-upstream/devrc --json headRefOid --jq .headRefOid)/status" \
      --jq '[.statuses[]?|select(.context=="tekton/devrc-pytests")|select(.state=="failure")|.description]|first' 2>/dev/null
  done | command grep -c "subsystem_store_api\|TestTheActor\|TestAHungRoundTrip\|TestTheBackstop\|TestAppendLands"
  ```

### Round 6's delta has not been audited
- **Symptom + exact repro:** `1eafc40c..734cb0bc` carries the merge plus F1–F4 fixes and
  the predicate narrowing. No round has looked at it.
- **Observed (with values):** round 5's auditor's own guidance — *"if F2–F4 are taken
  together with F1 in one commit, one delta re-audit of that commit is the right amount,
  and a clean result there ends the ladder."* That is exactly the shape of `734cb0bc`.
- **Ruled out:** *"six rounds is enough, stop counting"* — the stop rule is keyed on
  FINDINGS, never a round count; five rounds returned findings that needed fixing. via: doc
- **Leading hypothesis:** the delta is small and the fixes were each mutation-verified,
  so a clean round is plausible — but round 6 introduced a new predicate and a new
  summing derivation, which is exactly where the last five rounds found things.
- **Next probe, verbatim:** post the claims block, then
  `python3 scripts/audit-dispatch.py 1219 --round 6` and dispatch it over
  `1eafc40c..734cb0bc`.

### The round-6 delta audit — dispatched, verdict not yet in
- **Symptom + exact repro:** not a bug — an owed verification. Rounds 1–5 EACH found a defect
  introduced by the PREVIOUS round's fix, five for five; round 6's fixes shipped as `734cb0bc`
  and no round ever read them. The code is now on `main`.
- **Observed (with values):** `#1219` carries 5 comments; before this session the newest was the
  **round-5** claims block (`2026-09-02T07:17:20Z`). No `audit-claims round=6` block existed, and
  `audit-dispatch.py` correctly REFUSES a delta brief without one. Brief assembled after posting:
  15,465 chars, `--round 6`, range `1eafc40c..734cb0bc`.
- **Ruled out:** *"the merged tree is unvalidated"* — both required tiers are green on the merge
  commit with real counts (20488 passed / 1449 passed), quoted in State now. via: measurement
- **Ruled out:** *"auditing the branch audits something not live"* — all five blob OIDs are
  identical between `734cb0bc` and `b4fde334`. via: command
- **Ruled out:** *"six rounds is enough, stop counting"* — the stop rule is keyed on FINDINGS,
  and round 6's findings were never looked at by anyone. via: doc
- **Leading hypothesis:** the two places round 6 introduced NEW code are where a finding is most
  likely — F8's narrowing predicate (a guard that was too wide, now possibly too narrow, and
  keyed on a *root-ish variable name*, i.e. SPELLED rather than structural) and the summing
  derivation, whose conservatism rests on an argument about `-n 4 --dist loadfile` rather than a
  measurement.
- **Next probe:** read the dispatched auditor's verdict. If CLEAN the ladder ENDS — do not run a
  seventh round to confirm it. If it finds anything, the remedy is a **fix-forward PR against
  `main`**, never a push to the merged branch.

### Rank 1's flake-rate probe is still not measurable — now for a NEW reason
- **Symptom + exact repro:** running the rank-1 probe today samples a population that mostly does
  not contain the fix, so a flat rate would again be uninterpretable.
- **Observed (with values):** head-commit dates of the 5 newest open PRs against the
  `b4fde334` merge at 16:48:50Z — `#1233` 16:11:09Z, `#1232` 15:40:05Z, `#1230` 17:23:43Z,
  `#1227` 01:39:33Z, `#1209` 00:52:53Z. Only **1 of 5** postdates the merge.
- **Ruled out:** *"the merge unblocked rank 1"* — `strict:false` on `main` means a PR branch is
  not required to be up to date, so a branch built before `b4fde334` does not carry the fix at
  all, whatever its head date. via: measurement
- **Leading hypothesis:** the probe becomes meaningful once a substantial fraction of open PRs
  have been pushed or rebased after `b4fde334`. Days, not hours.
- **Next probe:** re-run the date comparison above; when most heads postdate `b4fde334`, run the
  probe from the doc's earlier block and **record the new baseline date beside the count**.

## Next steps (ranked)
🔴 Numbering is STABLE — `claim-work --slug-for <this doc> <rank>` derives from it. Rank 5 is
CLAIMED (`gate-flake-store-api-5`) and IN FLIGHT.

1. **Measure whether the flake rate actually dropped** — devrc, no files. 🔴 **Two independent
   reasons it is still not runnable, and the second is new.** (a) The recorded baseline — 5
   store-api failures among 14 open PRs, 2026-09-01 — was measured when only **1 of 3** files was
   sited, so it is not comparable to anything measured now and a flat result would be
   uninterpretable; a fresh baseline is required, not a comparison to that one. (b) It is no
   longer "wait for `#1219` to merge" (it merged) but "wait for open PRs to actually carry the
   fix": only 1 of the 5 newest open PR heads postdates `b4fde334`, and `strict:false` means an
   older branch does not contain it at all. Re-check the dates before running; record a fresh
   baseline date beside the count.
   forcing: gate — a required check has been failing PRs whose diff cannot reach it.
2. **Fix the hung-server classifier's path sensitivity** — devrc,
   `scripts/tests/test_subsystem_store_api.py`, `_HUNG_SERVER_RULES` /
   `_why_the_server_did_not_answer`. Scan frames' SOURCE LINES, not filenames. Reproduction in the
   doc's earlier Open-investigations block; documented in-file on `main`. **Unblocked and
   self-contained — the best next item if rank 5's audit comes back clean.**
   forcing: gate — it makes the gate's own diagnostic lie, and agent worktrees are routinely
   named after the bug being fixed, which is how it was found.
3. **Decide the `#1166` claim-work flake** —
   `test_release_deletes_the_ref_and_the_slug_becomes_claimable_again` failed a docs-only PR.
   Hits a real git remote, so plausibly the same wall-clock shape, but UNMEASURED. Start by
   checking whether it failed on other shas.
   forcing: gate — same required check, different test, red on a docs-only diff.
4. **Tell the authors of `#1194` and `#1177` their reds are real** — both still OPEN with
   `ci=pending` as of 2026-09-02T17:24Z. `#1194` needs its script to satisfy the runtime-shebang
   scanner; `#1177` needs a `claude/skill-tiers.json` entry in the same commit.
   forcing: none — someone else's PRs; recorded so the misreading is not repeated.
5. **Close the audit ladder on `#1219`** — IN FLIGHT, claimed as `gate-flake-store-api-5`.
   The claims block is posted (`#1219` comment `5513658674`); the delta audit over
   `1eafc40c..734cb0bc` is dispatched and its verdict is not yet in. **A CLEAN verdict ENDS the
   ladder — do not run a seventh round to confirm it.** Any finding is a fix-forward PR against
   `main`, never a push to the merged branch. Release the claim when the verdict lands.
   forcing: gate — unaudited code from an unclosed ladder is live on the branch every PR merges
   into.

## Gotchas / decisions / dead-ends
- 🔴 **A CHANGE THAT COULD SILENTLY DO NOTHING NEEDS A TEST THAT FAILS WHEN IT DOES
  NOTHING.** The tmpfs fixture falls back to `tmp_path` on every failure mode, which is
  what makes it safe — and also what would let it be completely inert while the whole
  suite stayed green. `test_the_store_fixture_ACTUALLY_lands_on_tmpfs_when_one_exists`
  is the positive control, and it was watched to fail under a mutant that forces the
  fallback. **Any change whose failure mode is "quietly does nothing" needs this.**
- 🔴 **`traceback.format_stack` RENDERS FILENAMES, so any substring scan over a stack
  also scans the CHECKOUT PATH.** Generalises past this classifier: a diagnostic that
  greps a rendered traceback for a keyword can be flipped by what a directory is named.
  Naming a worktree after the bug you are fixing is the normal case, which is what
  makes it likely rather than exotic.
- 🔴 **STALE `__pycache__` CONTAMINATED A CONTROL AND PRODUCED A FALSE REFUTATION.**
  `git worktree move` preserves mtime and size, so CPython revalidated the cached
  bytecode and the code objects kept the OLD `co_filename` — the renamed tree still
  rendered the old path and still failed, which read as "the path theory is wrong."
  **Clear `__pycache__` or set `PYTHONDONTWRITEBYTECODE=1` before trusting any control
  that depends on a file's identity or location.**
- 🔴 **I PIPED A GATE BUILD THROUGH `tail` AND DESTROYED BOTH THE COUNTS AND THE EXIT
  STATUS.** `nix build … -L | tail -40` gave `[exited with code 0]` from `tail`, not
  from nix, and truncated away the collected counts. The runner's own
  `RESULT: PASS (exit=0)` line survived because it is emitted behind an EXIT trap —
  that is the only reason the run was interpretable. **Redirect to a file and append
  your own `echo "NIX_RC=$?"`; never pipe a build you intend to read a verdict from.**
- 🔴 **THE GATE VALIDATING A GATE FIX IS NOT INDEPENDENT EVIDENCE.** One green run on
  `#1211` cannot separate "the fix worked" from "this run would not have flaked". Said
  in the PR and repeated in rank 1 because it is the single most likely thing for a
  future session to overclaim.
- 🔴 **"8 of 14 PRs are red" WAS NOT ONE PHENOMENON, and calling it a permanently-red
  gate was wrong.** Two of those reds were the gate working correctly on the PR's own
  diff. **Check each red against its own diff before generalising** — the generalisation
  is what licenses clicking through, and it was false here.
- ⚠ **`nix build` needs longer than the 10-minute foreground Bash cap** — run it with
  `run_in_background`, redirected to a file. Build the two check derivations ONE AT A
  TIME; a combined invocation contends on the nix store and produces false failures.
- ⚠ **A red required check posted as `failure` with `COULD NOT RUN: … stopped before
  any leg reported` is a broken gate, not a verdict on the diff.** Read
  `.statuses[].description`, and get a control from an unrelated open PR before
  spending anything on it.
- **Why the store fixture and not the server:** the fsyncs in `_replace_bytes` are a
  deliberate crash-durability guarantee whose docstring argues precisely why neither is
  removable. Moving the fsync after the response would trade durability for CI
  convenience. The tests assert nothing about fsync timing, so relocating their store
  costs no fidelity — and the one test that IS about stalls patches `_fsync_dir`
  explicitly, so it is unaffected by where the store lives.

- 🔴 **THE MERGED TREE FOUND WHAT SIX AUDIT ROUNDS DID NOT.** Every round audited the PR
  branch; the branch was clean and the merge was not. Main added one `tmp_path / <Name>`
  and the ratchet accused it. **Gate on the merged tree — and note the corollary: the
  finding was a FALSE POSITIVE that an earlier round had predicted and I had deferred as
  a 🟢.** A 🟢 that describes a guard being wrong is a latent red, not a nit.
- 🔴 **THREE OF MY OWN "MEASURED" NUMBERS WERE MEASUREMENTS OF SOMETHING ELSE.** (a) apparent
  bytes from a SYNTHETIC store labelled as the real fixture's; (b) the correction to that,
  which double-counted DIRECTORY pages — tmpfs dirs cost zero, and
  `1,253,376 + 3*4096 = 1,265,664` exactly; (c) a floor lowered on the strength of a 🟢
  note without measuring the payload at all, landing BELOW it and opening an ENOSPC
  window. **The fix that held was not a fourth number — it was deriving the value from
  the fixture that produces it.** Ship the derivation, never the transcription.
- 🔴 **A HARDCODED LITERAL WAS REINTRODUCED INSIDE THE FIX FOR HARDCODED LITERALS.**
  `_SEEDED_ENTRIES = 3` — a bare constant about another file's function, pinned by
  nothing — was added by the commit whose entire subject was "a constant checked against
  a constant is not a guard". Now derived by counting write calls outside any loop.
- 🔴 **FOUR GUARDS OF MINE WERE NARROWER THAN THEIR NAMES, ACROSS FOUR ROUNDS.** A regex
  matching a word my own comment spelled; a test named "…takes its store root from the
  shared siting" that only checked *a* call existed; a floor guard that monkeypatched the
  variable it claimed to bound; a derivation that took the MAX of one loop in one file.
  **The pattern is not carelessness — it is that a guard over TEXT, or over one instance,
  is walkable by construction.** The AST/derived versions were available every time.
- 🔴 **A SCRIPTED MASS EDIT SKIPPED 9 OF 19 SITES WHILE ITS OWN ASSERTION PASSED.** It
  asserted the target count reached zero, not that every function got its new parameter.
  Reverted rather than shipped; the sites are held by a ratchet instead. **Assert the
  post-condition you actually need, not the one that is easy to count.**
- ⚠ **`nix build` needs longer than the 10-minute foreground Bash cap** — `run_in_background`,
  redirected to a file, and **never piped**: `| tail` replaces nix's exit status with
  `tail`'s and truncates the counts. That cost the pytests signal on `#1211`; the
  runner's own `RESULT:` line survived only because it is emitted behind an EXIT trap.
- ⚠ **The `audit-dispatch.py` delta brief REFUSES without a posted `audit-claims` block** —
  correctly, since an empty one silently turns a delta into a blind full audit that then
  reads as covered. Post the claims comment first, every round.
- ⚠ **A resumed subagent keeps its context** — round 3's auditor died on a session limit
  after only creating its worktree; `SendMessage` to its id resumed it with the brief
  restated, rather than paying for a fresh dispatch.

- 🔴 **A HANDOFF DOC CAN MERGE AFTER THE PR IT DESCRIBES, SO IT IS BORN STALE.** `#1231` (this
  doc) merged 17 minutes after `#1219`, and shipped saying `#1219` was OPEN with rank 5 as
  in-flight work. The next session's kickoff message repeated it, and the whole item was already
  done. **The doc's own commit timestamp is not evidence of its currency** — reconcile the PRs it
  names against `gh pr view` before acting, which is exactly what `resume-state.sh` DRIFT does and
  what caught this one. Writing the doc last does not help; merging it last is the problem.
- 🔴 **A MERGE COMMIT MAKES `git diff <base>..<head>` A MEASUREMENT OF THE WRONG THING.**
  `734cb0bc` has parents `1eafc40c` and `a6e50641`, so the "delta since round 5" reads 30 files /
  +7267 — main flowing in, authored by no round. Handing that to an auditor turns a delta re-audit
  into a blind full audit that then reads as covered. **Size the authored delta first** (~186
  lines here), and tell the auditor which files main moved underneath it.
- 🔴 **BEFORE AUDITING A MERGED PR'S BRANCH, PROVE THE BRANCH IS THE LIVE CONTENT.** A squash
  merge produces a different commit with different parents, so ancestry says nothing. Compare
  **blob OIDs per file** between the branch head and the squash commit; all five matched here, and
  that is what makes the branch range a valid handle on what is deployed.
- 🔴 **"BOTH TIERS GREEN ON THE MERGED TREE" IS AVAILABLE FOR FREE AFTER THE MERGE, AND IS
  STRONGER THAN THE PRE-MERGE CHECK.** Tekton runs `devrc-main-pytests`/`devrc-main-nodetests` on
  `main` itself, so the merge commit carries a real merged-tree verdict with counts. The doc spent
  effort worrying that `strict:false` left the merged tree ungated; post-merge, `gh api
  repos/.../commits/<merge-sha>/status` answers it directly. That is a *detector*, not a
  substitute for gating before the merge.
- ⚠ **`audit-dispatch.py --round N` warns when the newest claims block is already `round=N`.**
  That warning is a generic re-audit guard and is EXPECTED in the normal flow: the `round=N` block
  states what round N fixed, and round N's audit is what checks it. Read the range it printed
  before treating the warning as a problem.
- ⚠ **A branch name from a previous round of the same effort will still exist locally and on
  origin.** `git worktree add -b docs/handoff-gate-flake-r6` failed because that branch was
  `#1231`, already merged. Pick a fresh name rather than reusing or deleting.

## How to verify
```bash
# #1219 is merged, and the merged tree is green on BOTH required tiers — by content, not ancestry
gh pr view 1219 --repo innovation-upstream/devrc --json state,mergedAt,mergeCommit
gh api repos/innovation-upstream/devrc/commits/b4fde334223ad594308295802f1918b07c101493/status \
  --jq '.state, (.statuses[]|"\(.context) \(.state): \(.description)")'   # both success, real counts

# the branch head is the live content (all five must print SAME)
for f in scripts/testlib/store_siting.py scripts/tests/test_store_siting_ledger.py \
         scripts/tests/test_subsystem_store_api.py scripts/tests/test_cairn_write.py \
         scripts/tests/test_cairn_cli.py; do
  a=$(git -C $DEVRC rev-parse "734cb0bc:$f"); b=$(git -C $DEVRC rev-parse "b4fde334:$f")
  [ "$a" = "$b" ] && echo "SAME  $f" || echo "DIFF  $f"
done

# the round-6 claims block exists (audit-dispatch REFUSES a delta brief without it)
gh pr view 1219 --repo innovation-upstream/devrc --json comments \
  --jq '[.comments[]|select(.body|contains("audit-claims round=6"))]|length'   # 1

# rank 1 is still not measurable — how many open PR heads postdate the merge
for p in $(gh pr list --repo innovation-upstream/devrc --state open --json number --jq '.[].number'); do
  gh api "repos/innovation-upstream/devrc/commits/$(gh pr view $p --repo innovation-upstream/devrc \
    --json headRefOid --jq .headRefOid)" --jq '.commit.committer.date'
done | sort | tail -5      # compare against 2026-09-02T16:48:50Z
```
🔴 Do NOT run the tests from a worktree whose path contains `fsync`, `flock`, `_EntryLock` or
`_audit_lock` — the classifier substring-matches rendered tracebacks, which include FILENAMES,
and the failure is the PATH, not the code (rank 2). Clear `__pycache__` or set
`PYTHONDONTWRITEBYTECODE=1` before trusting any control.
