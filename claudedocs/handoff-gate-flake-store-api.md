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
- devrc `main`; no branch of this effort is open. ⚠ Another session's dirty
  `nix/programs/alacritty/default.nix` has been in this shared checkout all session —
  untouched, and checked for collision before every fast-forward.
- **SHIPPED: `innovation-upstream/devrc#1211` → squash `1a4350f3`.** Sites the
  store-api test store on tmpfs. Verified by content: blob `4eb805eb` identical on
  `origin/main`, and the siting, the fstype guard, the 6 new tests and the recorded
  classifier defect all confirmed present in the merged file.
- Gate green on it with real counts: `collected=20345 passed=20342 skipped=3 failed=0`
  (floor 18404), nodetests `1449/1449` (floor 1367).
- 🔴 **NOT the same as "the flake is fixed."** The gate that validated the fix is the
  thing being fixed, so one green cannot distinguish "the fix worked" from "this run
  would not have flaked anyway." **The verifier is the flake RATE over the next stretch
  of PRs**, not this run. See rank 1.

**What was actually established (narrower than "fixed"):**
- fsync on tmpfs does not move under contention — measured, below
- the store lands there when a tmpfs exists, pinned by a test that fails if it silently
  falls back
- the change cannot make things worse: every failure mode degrades to `tmp_path`

**The 8-of-14 red PRs, triaged — it is NOT one phenomenon.** This corrects an earlier
framing in this session that called the gate uniformly "permanently red":
- **6 likely flakes** — 5 in `test_subsystem_store_api.py`
  (`TestTheBackstopNeverSendsASecondResponse`, `TestAHungRoundTripSAYSWhichSideBlocked`
  ×2, `TestTheActorComesFromTheTOKEN` ×2) plus `#1166`
  (`test_release_deletes_the_ref_and_the_slug_becomes_claimable_again`, a claim-work
  test hitting a real git remote, on a docs-only diff).
- 🔴 **2 are the gate WORKING CORRECTLY, and belong to their authors, not here:**
  `#1194` adds `scripts/break-glass-merge.sh` and fails the runtime-shebang scanner;
  `#1177` adds `claude/skills/rig-control/SKILL.md` and fails
  `test_every_shipped_skill_has_exactly_one_ledger_entry` — CLAUDE.md requires a
  `claude/skill-tiers.json` entry in the SAME commit, pinned two-way.

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

## Next steps (ranked)
1. **Measure whether the flake rate actually dropped** — devrc, no files. The verifier
   for `#1211`, which is otherwise a fix nobody has confirmed works. Baseline recorded
   above (5 store-api failures among 14 open PRs, 2026-09-01); probe command in the
   second Open-investigations block. 🔴 Until this runs, `#1211` is a plausible fix, not
   a demonstrated one — do not describe it as having fixed the flake.
   forcing: gate — a required check has been failing PRs whose diff cannot reach it.
2. **Fix the hung-server classifier's path sensitivity** — devrc,
   `scripts/tests/test_subsystem_store_api.py`, `_HUNG_SERVER_RULES` and
   `_why_the_server_did_not_answer`. Scan frames' SOURCE LINES, not filenames. Full
   reproduction in the first Open-investigations block; the defect is already
   documented in-file on `main`, so the code and the comment agree today.
   forcing: gate — it makes the gate's own diagnostic lie, in the direction that sends
   the reader to the wrong mechanism, and agent worktrees are routinely named after
   the bug being fixed.
3. **Decide the `#1166` claim-work flake** —
   `test_release_deletes_the_ref_and_the_slug_becomes_claimable_again` failed a
   docs-only PR. It hits a real git remote, so it is plausibly the same
   wall-clock-under-load shape, but that is UNMEASURED — no reproducer, no control run.
   Start by checking whether it has failed on other shas.
   forcing: gate — same required check, different test; a red gate on a docs-only diff.
4. **Tell the authors of `#1194` and `#1177` their reds are real** — not this effort's
   work and deliberately not done here. `#1194` needs its script to satisfy the
   runtime-shebang scanner; `#1177` needs a `claude/skill-tiers.json` entry in the same
   commit. Both were misread as flakes earlier in this session before triage.
   forcing: none — someone else's PRs; recorded so the misreading is not repeated.

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

## How to verify
```bash
# the shipped change is on main, by CONTENT (a squash never makes the head an ancestor)
git -C $DEVRC show origin/main:scripts/tests/test_subsystem_store_api.py \
  | command grep -c '_tmpfs_dir'                      # non-zero
git -C $DEVRC rev-parse origin/main:scripts/tests/test_subsystem_store_api.py  # 4eb805eb…

# the guards still discriminate — clear __pycache__ or the run may not be honest
nix develop $DEVRC -c env PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  $DEVRC/scripts/tests/test_subsystem_store_api.py \
  -k "TestTheStoreIsSitedOffTheContendedDisk" -q          # 6 passed

# 🔴 do NOT run this from a worktree whose path contains fsync/flock/_EntryLock —
# the classifier will misreport and the failure is the PATH, not the code (rank 2).
```
🔴 Run **both** tiers before claiming a merge is safe, and name the tier and base sha:
`scripts/gate.sh` is the dev host; `nix build .#checks.x86_64-linux.{pytests,nodetests}`
is what Tekton gates on. **Build them ONE AT A TIME**, in the background, redirected to
a file — never piped.
