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
🔴 **`devrc#1219` is the live artefact: OPEN, `mergeable=MERGEABLE`, `mergeStateStatus=CLEAN`,
both required checks SUCCESS at `734cb0bc`.** It consolidates the store siting that `#1211`
applied to one file out of three, and it has been through **six audit rounds**.

- devrc `main` @ `a6e50641`. Worktree `~/workspace/devrc-r6` holds the branch
  `fix/consolidate-store-siting`; the shared checkout was never committed to.
- ⚠ Another session's dirty `nix/programs/alacritty/default.nix` has been in the shared
  checkout all session, untouched, and checked for collision before every fast-forward.

**What `#1219` ships** — `scripts/testlib/store_siting.py` (new) plus
`test_store_siting_ledger.py` (new seam guard), with `test_subsystem_store_api.py`,
`test_cairn_write.py` and `test_cairn_cli.py` taking their store root from it.
Merged-tree suite: **782 passed, rc 0**.

**🔴 THE SINGLE MOST USEFUL EVENT WAS THE MERGE, NOT AN AUDIT ROUND.** `#1219` went
CONFLICTING; on merging `origin/main` the ratchet immediately read **21 against a
constant of 20**, because main had added `(tmp_path / name).write_text(body)` — a scratch
file named `"wrapped.md"`. The guard fired on real divergence **and was wrong**: round 3's
audit had called the unrestricted `tmp_path / <Name>` arm "a false accusation" and I left
it as a 🟢. It stopped being hypothetical the moment main moved. Narrowed in round 6 to
expressions actually USED as a store root; merged tree now reads 20 == 20.

**Audit ladder, six rounds, every round's findings caused by the previous round's fixes:**
| round | outcome |
|---|---|
| 1 (full) | 4 🟡 — shadowed-mount fstype, ENOSPC on a full tmpfs, a spelled membership scanner, files-not-fixtures |
| 2 | all 9 claims verified; 3 new 🟡 — my three fixes shipped **unpinned** and all survived mutation |
| 3 | 1 🔴 **caused by my round-3 fix**: I lowered the floor below the payload |
| 4 | 1 MEDIUM — the peak constant was pinned to nothing |
| 5 | F1 (the derivation under-reported silently in 3 shapes) + my replacement measurement was also wrong |
| 6 | merge + all of round 5's findings; **delta not yet audited** |

**🔴 Rank 1's baseline is now known to be measuring the wrong thing — see rank 1.**

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

## Next steps (ranked)
🔴 Numbering is STABLE — `claim-work --slug-for <this doc> <rank>` derives from it.

1. **Measure whether the flake rate actually dropped** — devrc, no files. 🔴 **DO NOT run
   it against the recorded baseline as-is**: that baseline (5 store-api failures among 14
   open PRs, 2026-09-01) was measured when only 1 of 3 files was sited, so a flat result
   would be uninterpretable. Measure AFTER `#1219` merges and record a fresh baseline
   date. Probe in the first Open-investigations block.
   forcing: gate — a required check has been failing PRs whose diff cannot reach it.
2. **Fix the hung-server classifier's path sensitivity** — devrc,
   `scripts/tests/test_subsystem_store_api.py`, `_HUNG_SERVER_RULES` /
   `_why_the_server_did_not_answer`. Scan frames' SOURCE LINES, not filenames. Full
   reproduction in the doc's earlier Open-investigations block; documented in-file on
   `main`, so code and comment agree today.
   forcing: gate — it makes the gate's own diagnostic lie, and agent worktrees are
   routinely named after the bug being fixed, which is how it was found.
3. **Decide the `#1166` claim-work flake** —
   `test_release_deletes_the_ref_and_the_slug_becomes_claimable_again` failed a docs-only
   PR. Hits a real git remote, so plausibly the same wall-clock shape, but UNMEASURED —
   no reproducer, no control. Start by checking whether it failed on other shas.
   forcing: gate — same required check, different test, red on a docs-only diff.
4. **Tell the authors of `#1194` and `#1177` their reds are real** — not this effort's
   work. `#1194` needs its script to satisfy the runtime-shebang scanner; `#1177` needs a
   `claude/skill-tiers.json` entry in the same commit. Both were misread as flakes before
   triage.
   forcing: none — someone else's PRs; recorded so the misreading is not repeated.
5. **Audit round 6's delta, then merge `#1219`** — IN FLIGHT: `innovation-upstream/devrc#1219`.
   Delta is `1eafc40c..734cb0bc`. Claims block not yet posted. If that round is clean the
   ladder ENDS (do not run a seventh to confirm); then re-run BOTH gate tiers on the
   merged tree — the current Tekton green is on the branch and `strict` is false — and
   merge.
   forcing: gate — the PR is the mitigation for a required check that fails unrelated PRs.

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

## How to verify
```bash
# the PR, and that its checks are on the CURRENT head
gh pr view 1219 --repo innovation-upstream/devrc --json mergeable,mergeStateStatus,headRefOid
gh api "repos/innovation-upstream/devrc/commits/$(gh pr view 1219 --repo innovation-upstream/devrc \
  --json headRefOid --jq .headRefOid)/status" --jq '.statuses[]|"\(.context) \(.state): \(.description)"'

# the merged-tree ratchet — the number that caught main's new site
git -C $DEVRC worktree add /tmp/v6 fix/consolidate-store-siting && \
  nix develop $DEVRC -c env PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  /tmp/v6/scripts/tests/test_store_siting_ledger.py -q -p no:cacheprovider   # 8 passed

# the four affected files on the merged tree
nix develop $DEVRC -c env PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  /tmp/v6/scripts/tests/{test_store_siting_ledger,test_cairn_write,test_cairn_cli,test_subsystem_store_api}.py \
  -q -p no:cacheprovider                                                      # 782 passed
```
🔴 Run **both** tiers before claiming a merge is safe, and name the tier and base sha.
Build the two `nix` checks **ONE AT A TIME**, backgrounded, redirected to a file, never
piped. The current Tekton green is a claim about the PR BRANCH; `strict` is false, so
gating the merged tree is still manual.
