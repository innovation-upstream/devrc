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
🔴 **THE AUDIT LADDER IS CLOSED — by decision, not by a clean round.** `#1219` and its
successor `#1239` are both MERGED. The ladder ran **8 rounds on `#1219` + 2 on `#1239`**;
every round but the last produced findings, and the last was ended deliberately because the
findings had converged on prose accuracy inside a test guard while the effort's actual
verifier (rank 1) had still never been run.

- devrc `main` @ `65f7325b`. Claim `gate-flake-store-api-5` **RELEASED**.
- `#1219` → squash `b4fde334`. `#1234` (doc) → `3d0695c7`. `#1239` → squash **`65f7325b`**.
- Content-verified on `origin/main`: the gap guard
  `test_a_store_root_bound_in_a_pytest_FIXTURE_is_NOT_counted` is present; the three dead
  `PEAK_STORE_*` globals are gone.

**What the last three rounds actually did:**
- **round 6 delta audit** (owed before the merge, run after it): 7 findings, 4 🔴 — the
  worst being `_LARGEST_STORE_BYTES` pinned with **zero headroom** against a file-wide
  write-call census that moved 6 times in 19 commits, plus a nested-loop shape that
  under-reported 1,200 entries as 443 (2.7×). Together: the gate goes red on an unrelated
  commit, someone bumps the constant to unblock, and the ENOSPC guard silently dies.
- **round 7** (`#1239`): stopped parsing source and made `_check_store_budget` **walk the
  real store directory** at every `store_root` teardown. F2/F3/F4/F9 ceased to exist rather
  than being patched. Site count 20 → 33 with no site added.
- **round 8**: its own round-1 audit returned 8 findings (2 🔴); 7 fixed, 1 **disclosed**.

🔴 **GATE EVIDENCE, AT THE SCOPE IT WAS MEASURED — three tiers, base named.**
Merged tree `eaf2c0ca` (= `5a3d7fe7` + `origin/main` `146770ef`), derivations built ONE AT
A TIME, backgrounded, redirected, never piped:
- `nix build …#pytests` (sandbox, the gated tier): `RESULT: PASS (exit=0)`,
  `collected=20504 passed=20501 skipped=3 failed=0`, floor 18404, no timeout panic
- `nix build …#nodetests`: `RESULT: PASS (exit=0)`, `tests=1449 pass=1449 fail=0`, floor 1367
- `scripts/gate.sh --tier both` (dev-host): `GATE: RESULT=PASS exit=0`, both legs' exit
  codes agreeing with the runners' own `RESULT:` lines

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

### 🔴 `main` IS RED, and it is NOT this effort's doing — espanso `:acq` shadows `:rna`
- **Symptom + exact repro:** `scripts/collector/keylog/tests/test_espanso_detect.py:929`,
  `test_live_existing_resolutions_not_made_ambiguous`, fails on **plain `origin/main`**:
  ```bash
  git -C $DEVRC worktree add --detach /tmp/ctl origin/main
  nix develop $DEVRC -c env PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
    /tmp/ctl/scripts/collector/keylog/tests -q -p no:cacheprovider   # 1 failed, 100 passed
  ```
- **Observed (with values):** `AssertionError: search terms regressed (term -> (expected,
  actual, matching snippets)): {'recom': (':rna', None, [':acq', ':rna']), 'recommend':
  (':rna', None, [':acq', ':rna'])}`. A new `:acq` snippet's search terms collide with
  `:rna`, so both `recom` and `recommend` now resolve to **nothing** instead of `:rna`.
  Introduced by `a720d30d` (`espanso`) touching `nix/home.nix` — a bare commit straight to
  `main`, no PR.
- 🔴 **This is a REAL user-facing regression, not a test being fussy.** The espanso audit
  established Zach fires ~100% via Ctrl+Space SEARCH, so `search_terms` ARE the interface:
  typing "recommend" used to reach `:rna` and now reaches an ambiguous set.
- **Confirmed three independent ways:** the merged-tree `gate.sh` run; a control run on
  plain `origin/main`; and Tekton's own `tekton/devrc-main-pytests` on `5a82aaa9` —
  `failure: FAILING: test_live_existing_resolutions_not_made_ambiguous`.
- **Ruled out:** *"`#1239` caused it"* — `#1239` touches only `scripts/testlib/store_siting.py`
  and `scripts/tests/test_store_siting_ledger.py`, which cannot reach espanso resolution;
  and the control on plain `main` fails identically. via: measurement
- **Ruled out:** *"a load flake"* — it fails in 0.22s, deterministically, with a value-bearing
  assertion naming the colliding snippets. via: measurement
- **Leading hypothesis:** `:acq`'s `search_terms` need narrowing so they stop matching
  `recom`/`recommend`, OR `:rna`'s need a disambiguating term. The fix is in `nix/home.nix`,
  and it belongs to whoever added `:acq`.
- **Next probe, verbatim:** `git -C $DEVRC show a720d30d -- nix/home.nix` to read the added
  snippet, then decide which side's `search_terms` move.

## Next steps (ranked)
🔴 Numbering is STABLE — `claim-work --slug-for <this doc> <rank>` derives from it.
**Rank 5 is CLOSED** and is retained, unrenumbered, so live claims keep resolving.

1. **Measure whether the flake rate actually dropped** — devrc, no files. 🔴 **NOW GENUINELY
   UNBLOCKED for the first time**: `#1211` sited 1 of 3 files, `#1219` sited all three plus
   `scoped_store`, and `#1239` replaced the whole census. The old baseline (5 store-api
   failures among 14 open PRs, 2026-09-01) is **not comparable** — it was measured against a
   partially-sited suite. Record a FRESH baseline with its date, and wait until a substantial
   fraction of open PR heads postdate `65f7325b` before reading anything into the number.
   forcing: gate — a required check has been failing PRs whose diff cannot reach it.
2. **Fix the hung-server classifier's path sensitivity** — devrc,
   `scripts/tests/test_subsystem_store_api.py`, `_HUNG_SERVER_RULES` /
   `_why_the_server_did_not_answer`. Scan frames' SOURCE LINES, not filenames. Reproduction in
   the doc's earlier Open-investigations block. **The best next item — self-contained,
   unblocked, and it bit three separate agents this session as a briefing caveat.**
   forcing: gate — it makes the gate's own diagnostic lie, and agent worktrees are routinely
   named after the bug being fixed.
3. **Decide the `#1166` claim-work flake** —
   `test_release_deletes_the_ref_and_the_slug_becomes_claimable_again` failed a docs-only PR.
   Hits a real git remote; plausibly the same wall-clock shape, UNMEASURED.
   forcing: gate — same required check, different test, red on a docs-only diff.
4. **Tell the authors of `#1194` and `#1177` their reds are real** — `#1194` needs its script
   to satisfy the runtime-shebang scanner; `#1177` needs a `claude/skill-tiers.json` entry in
   the same commit.
   forcing: none — someone else's PRs; recorded so the misreading is not repeated.
5. **CLOSED — the store-siting consolidation and its audit ladder.** `#1219` (`b4fde334`) and
   `#1239` (`65f7325b`) both merged and content-verified; ladder ended by decision after 10
   rounds. Five residuals are disclosed in-source and listed under Gotchas. Nothing to do.
   forcing: none — done; retained so the rank numbering stays stable.
6. **Fix the espanso `:acq`/`:rna` collision — `main` IS RED** — devrc, `nix/home.nix`. Full
   diagnosis in the Open-investigations block above. Not this effort's change and deliberately
   not fixed here; it is one snippet's `search_terms`. **Whoever fixes it should run the
   keylog tests, which are the deterministic check.**
   forcing: regression — `main` is red on `tekton/devrc-main-pytests` and a real
   Ctrl+Space search path is broken.

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

- 🔴 **A LADDER CAN END BY DECISION, AND THAT IS NOT THE SAME AS ABANDONING IT.** The stop
  rule is "rounds continue while findings need fixing", which is a rule about *not stopping
  early* — it is not a promise that findings converge to zero. Here they converged on *prose
  accuracy inside a test guard* while rank 1, the effort's actual verifier, had gone unrun
  across ten rounds. **Ending deliberately, with the residuals disclosed and pinned, is a
  legitimate terminal state; the illegitimate one is stopping quietly and calling it clean.**
- 🔴 **THE BEST FIX FOR A CLASS OF PARSING BUGS WAS TO STOP PARSING.** Round 6's ladder kept
  finding new spellings that walked through a syntactic sweep (two-arg `range`, comprehension,
  extracted helper, nested loops, `AsyncFor`). Round 7 made the check **walk the real store
  directory** instead, and four findings ceased to exist rather than being patched. **When
  round N+1 keeps finding new spellings, the sweep is the bug — change what you measure.**
- 🔴 **DISCLOSE-AND-PIN BEATS DISCLOSE.** Round 8 could not close the fixture-bound-store gap
  cheaply (both instances return a tuple the consumer destructures, needing a cross-scope
  resolver; and the shortcut — file-wide indexing — measured **two accidental catches for
  four false accusations**). It wrote the residual down AND added
  `test_a_store_root_bound_in_a_pytest_FIXTURE_is_NOT_counted`, labelled a **gap guard, not
  coverage**. Closing the hole now fails the suite and forces the prose to be updated.
  **A residual in prose rots; a residual as an assertion cannot.**
- 🔴 **"DISJOINT FILES" WAS NOT SAFETY, AND CHECKING IT PAID OFF TWICE.** `main` moved twice
  mid-gate. The first move touched `nix/graphical.nix` — disjoint from the PR's two
  `scripts/` files, yet **nine tests reference `graphical`**, so the re-gate was warranted (it
  came back identical: an answer, not an assumption). The second move touched `nix/home.nix`
  and **the re-gate went RED** — inherited from `main`, but only a control run could say so.
- 🔴 **`main` MOVES FASTER THAN A THREE-TIER GATE COMPLETES, so name the BASE in every claim.**
  Four commits landed during one gate run. Chasing a fully-current merged-tree gate across all
  three tiers does not converge. The proportionate answer: re-run the **full dev-host suite**
  on the current merged tree (cheapest complete coverage) and let the sandbox-tier claim stand
  at its own named base — then say both scopes separately. "The gate passed" is true of one
  run, one tier, one base, and reads as a property of the change.
- 🔴 **A MONITOR CAN OUTLIVE ITS SUBJECT AND REPORT A FALSE TIMEOUT.** A watcher polling an
  agent's task-output file for an RC marker printed `GAVE UP: still running after ~57min` for
  builds that had **already finished and been reported by the agent itself**. Silence and
  "still running" are indistinguishable; so are "subject ended" and "subject hung". **Read the
  authoritative artefact, never the watcher's verdict about it.**
- 🔴 **THE `FAILED`-LINE GREP FOUND NOTHING ON A RUN WITH A REAL FAILURE.** pytest embeds ANSI
  inside `FAILED` lines, so `grep -E "^FAILED"` matched **zero** on a log containing exactly
  one failure. `sed -e 's/\x1b\[[0-9;]*[mGKHF]//g'` first, and cross-check against the
  `TOTAL … failed=N` line — which is what actually located it. A documented trap, hit anyway.
- ⚠ **Do not append `echo "(empty = none)"` to a grep whose output you have not read.** It
  printed a confident "(empty = none)" beneath NINE matching files, twice in one session. The
  label is written before the answer is known, so it asserts the result it hoped for.
- ⚠ **`main` is protected in NAME ONLY, and this is DELIBERATE** — `required_status_checks`
  absent, `enforce_admins: false`, while `GET /branches/main` still says `protected: true`.
  The operator turned it off until a Tekton capacity issue is resolved; `drift-check.sh` rc 24
  reporting it is EXPECTED, not a finding, and it is **not yours to restore**. Now documented
  in `CLAUDE.md`. Consequence: **you are the gate** — a green Tekton check is information, not
  permission.

## How to verify
```bash
# both PRs landed, by CONTENT (a squash never makes the head an ancestor)
git -C $DEVRC show origin/main:scripts/tests/test_store_siting_ledger.py \
  | command grep -c "test_a_store_root_bound_in_a_pytest_FIXTURE_is_NOT_counted"   # >=1
git -C $DEVRC show origin/main:scripts/testlib/store_siting.py \
  | command grep -c "^PEAK_STORE_BYTES"                                            # 0

# the ratchet still discriminates (clear __pycache__ or the run may not be honest)
nix develop $DEVRC -c env PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  $DEVRC/scripts/tests/test_store_siting_ledger.py -q -p no:cacheprovider

# 🔴 main's OWN red — expected until rank 6 is fixed; it is NOT this effort's
nix develop $DEVRC -c env PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  $DEVRC/scripts/collector/keylog/tests -q -p no:cacheprovider    # 1 failed, 100 passed
gh api repos/innovation-upstream/devrc/commits/$(git -C $DEVRC rev-parse origin/main)/status \
  --jq '.statuses[]|"\(.context) \(.state): \(.description[0:100])"'
```
🔴 Do NOT run these from a worktree whose path contains `fsync`, `flock`, `_EntryLock` or
`_audit_lock` — the hung-server classifier substring-matches rendered tracebacks, which
include FILENAMES, and the failure is the PATH, not the code (rank 2).
