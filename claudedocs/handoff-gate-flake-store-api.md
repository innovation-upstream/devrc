# Handoff: gate-flake-store-api — 2026-09-01

## Run this first — the index, one command
```bash
cairn recall --repo ~/workspace/devrc
```
⚠ **This block used to prescribe the deleted `scripts/lib/subsystem_recall.py`** — `#1508` moved the
reader into the pinned package, so the old spelling now fails with a file-not-found. **It is not
just this doc: see rank 9 for the population, which is the single place those counts live.** Only
this doc's invocation is fixed here; fixing one site and implying the rest is the partial-sweep
failure. ⚠ **This block deliberately carries NO counts.** It previously restated them and got two wrong in
the direction rank 9 exists to prevent — quoting the bare-path figures for *prescribing*, and listing
five source files where rank 9, in the same commit, said six and named the Dockerfile the list had
dropped. **A second copy of a count is how one of them becomes wrong, and the fix is to have one
copy, not two agreeing ones: rank 9 owns these.**
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
✅ **2026-09-12 — RANK 1 HAS BEEN RUN. The verifier this doc was written for finally has a
number, and the number relocates the problem.** The store-api fsync flake is named in **0 of 99**
`tekton/devrc-pytests` verdicts on heads that CARRY `ce9b55c3` against **12 of 298** that do not
(P(0) ≈ 0.017), measured by **ancestry**. 🔴 **And what the same read found instead: the gate's
remaining red is dominated by DETERMINISTIC ledger censuses over tracked text — 27 of 99 post-fix
verdicts, ~7× this flake at its worst — of which 22 were the kill-mention ledger, closed at the
source by `#1561` (`c0bbd6d9`), and 5 the runner-bound ledger, closed by `#1567` (`6f1867b1`).**
Both reddened `main` itself; both are fixed, confirmed at `origin/main` `337114e0` (**5 passed** on
`test_runner_bound_ledger.py`, **1 passed** on the kill guard). 🔴 **The CLASS is not fixed: at least
two instances in two days, and nothing prevents the next.** That is rank 8 — closed as an instance,
retained for the class. ⚠ **The 27 / 22 / 5 figures are a READ-TIME population that cannot be
re-derived** (rank 1 says why); do not treat a later disagreement as a refutation.
⚠ **This paragraph said `main` IS RED RIGHT NOW for about an hour after `#1567` merged**, while rank
8 five hundred lines below already said CLOSED — and `State now` is the block a resuming session
reads first. **Four other sites were swept for that phrasing and this one, the most-read, was
missed.** Full table, controls and residuals in rank 1; claim `gate-flake-store-api-1` released.

🔴 **THE AUDIT LADDER IS CLOSED — by decision, not by a clean round.** `#1219` and its
successor `#1239` are both MERGED. The ladder ran **8 rounds on `#1219` + 2 on `#1239`**;
every round but the last produced findings, and the last was ended deliberately because the
findings had converged on prose accuracy inside a test guard while the effort's actual
verifier (rank 1) had still never been run.

- 🔴 **There is deliberately NO "devrc `main` @ \<sha\>" line here any more.** It read `65f7325b` —
  the `#1239` squash — until 2026-09-12, by which point this doc's own later text had moved past it
  twice. The replacement was written as `8114a124` and **was stale before it was pushed**: `main`
  took `#1564` in the interval, which is the second time in one session. **A bare tip sha in a
  State-now section is a claim that expires on the next merge, and nothing in this repo updates
  it.** Every sha here is instead pinned to the commit it is ABOUT — `ce9b55c3`, `c0bbd6d9`, and
  `8114a124`/`4e970998` as named measurement POINTS, never as "the tip". Claims:
  `gate-flake-store-api-5` **RELEASED**; `gate-flake-store-api-1` released 2026-09-12 once the
  measurement landed.
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

### CLOSED 2026-09-12 — whether the tmpfs siting reduces the flake rate: MEASURED, and it did
🔴 **This block's heading read "UNMEASURED" and its "Next probe, verbatim" below told the next
session to run a probe that HAS NOW BEEN RUN.** Read rank 1 for the reading — 0 of 99 verdicts on
heads carrying `ce9b55c3` against 12 of 298 that do not, P(0) ≈ 0.017 — and do **not** re-run the
snippet below. It is retained for ONE reason and it is not "use it": its **predicate is a LIMIT, not
a baseline** — `--limit 40` newest-first with no ancestry test at all, which is the date-shaped
sampling rank 1 says gives a wrong denominator. The closing reading used ancestry over 400 heads.
🔴 **AND ITS CLASSIFIER CANNOT SEE THE ONE TEST THIS DOC IS ABOUT — an earlier revision of this very
paragraph praised it, which would have sent someone to re-use it.** The snippet greps status
DESCRIPTIONS for `subsystem_store_api\|TestTheActor\|TestAHungRoundTrip\|TestTheBackstop`. A
description **never carries a filename**, so `subsystem_store_api` cannot fire at all; and it carries
a `Class.test_name` only sometimes — over the 101 failure verdicts rank 1 collected, **27 of the 100
named rows are `Class.test_name` and 73 are a bare `test_…` name**, so a class-keyed token misses a
test reported bare even when its class is listed.
🔴 **TWO DIFFERENT POPULATIONS, AND AN EARLIER REVISION OF THIS PARAGRAPH CONFLATED THEM AND
CONTRADICTED ITSELF.** The **12** rows naming the fsync flake (`TestARefusedWrite…`) — rank 1's entire
pre-window count — match **0**. The **21** rows naming *any* class that lives in
`test_subsystem_store_api.py` match **4** (`TestTheActor…` ×3, `TestAHungRoundTrip…` ×1). Calling the
first set "the store-api rows" while quoting the second as a positive control asserted 0 and 4 about
the same thing.
⚠ So the grep is **not** wired to nothing, and **"it returns 0 in both arms" was wrong** — on the
pre-window it returns **4** and on the post-window **0**, which is worse than a flat zero: it reads
as a rate that fell to nil while being blind to the only test in question. ⚠ **Adding
`TestARefusedWrite…` to the token list does not fix it** — that is the fix-direction trap, because
73 of 100 rows name no class at all. via: measurement
⚠ **What is still UNMEASURED is the block's own hypothesis at its second point:** it predicted the
rate "drops to ~0 where a tmpfs is available, and is **unchanged where the fallback fires**". The
reading confirms the first half and says nothing about the second — **nothing has measured whether
the GATE container has a usable tmpfs**, so a 0 is consistent with both "sited" and "the mechanism
stopped firing for some other reason". That is rank 1's `[0, 22]` bound's sibling: recorded, not
closed.
✅ **The residual is NARROWER than "it might change nothing in CI" — but read the three corrections
below before quoting the narrowing, because the first version of this paragraph overstated what it
had read.** `store_siting.py:221` sets `_MIN_FREE_BYTES = 4 MiB`.
🔴 **(a) `/dev/shm` is the DEFAULT candidate, not the only one.** `:270` is
`for candidate in (os.environ.get(_CANDIDATE_ENV), _DEFAULT_CANDIDATE)` and `_CANDIDATE_ENV` at
`:66` is **`DEVRC_TEST_TMPFS`**, consulted FIRST. An earlier revision here said `:67` "makes the only
candidate `/dev/shm`" — wrong, and it made the grep below look exhaustive when it could not match
the env override at all.
🔴 **(b) The read must cover TWO manifests, and the first version named one.** Besides
`devrc-ci-pipeline.yaml`, the `pytests` pod spec also lives in
`devrc-ci-triggertemplate.yaml` (it carries its own `podTemplate:`). Both were re-read: **0
occurrences of `shm`, 0 of `medium:`, and 0 of `DEVRC_TEST_TMPFS` across both** — so the conclusion
*"nothing overrides `/dev/shm` for the `pytests` step"* **holds**, but on the wider read, not the
first one. ⚠ **The strong control was available and unused:** sibling pipelines DO set
`medium: Memory` (`clawgate-ci-pipeline.yaml`, `auditloop-ci-pipeline.yaml` and three others), so the
zero is a real absence rather than a pattern that never matches anything.
🔴 **(c) The manifest read is itself unpinned and HAS ALREADY MOVED** — the same defect as the
verdict census above. Verified exactly at `homelab-talos` **`b2f42ef5`** (2,482 lines, `pytests` 59,
`emptyDir` 3, all three in comments). `origin/trunk` has since taken `3c53d618` — *"/nix becomes a
per-run emptyDir"* — and AT `3c53d618` reads **2,584 lines, `emptyDir` 12**, i.e. it added real
emptyDirs where the control had found only comments. **Re-read both manifests at a named sha; do not
quote these numbers.**
⚠ **And two halves, only the first a reading of this cluster:** "nothing overrides it" is measured;
"therefore 64 MiB" is the *containerd/Docker* default that `store_siting.py`'s own docstring cites,
and **no node on this cluster was read for it** — a runtime or node setting can change it. Against a 4 MiB floor the
check passes by 16× if that default holds, so the fallback needs `/dev/shm` **absent, read-only, or
already ~94% consumed** — and the last of those is the concurrent-writer case the docstring already
says cannot be closed from in-process. **What stays owed is one reading of a real gate container**;
`kubectl` cannot take it after the fact (gate pods are ephemeral, pipelineruns pruned hourly), so
the other surface is a `store:` path in any FUTURE store-api failure log — `devrc-store-*` = sited,
`pytest-of-*` = fell back.

The original block, unchanged below.

#### Whether the tmpfs siting actually reduces the flake rate — as written before the measurement
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

1. ✅ **MEASURED 2026-09-12 — THE READING EXISTS. The store-api flake is named in 0 of 99
   verdicts on heads that CARRY `ce9b55c3`, against 12 of 298 that do not; and the gate's
   remaining red is not this flake, nor any flake.** This rank asked for a number and the
   number is below. What is left of it is a *different* item — see "What this does NOT close".

   **Population and instrument.** Newest `tekton/devrc-pytests` verdict per PR head, over
   **400** devrc PR heads (`#1162`–`#1566`, every state); **397** carried a verdict. Source is
   the GitHub combined-status endpoint, which returns the latest status per context — the only
   surviving record, because the pipelineruns are pruned hourly (`keep: 20`). Predicate is
   **ancestry**: every `refs/pull/*/head` fetched into a throwaway repo (`--filter=tree:0`) and
   `git merge-base --is-ancestor ce9b55c3 <head>` run locally, so the shared clone's ref
   namespace was not written to. **0 heads were ancestry-unmeasurable.**
   🔴 **Instrument validated before the verdict was read**: the three known reds
   (`#1458`@`a6dd11eb`, `#1462`@`dc972398`, `#1454`@`c5e3123e`) all come back `failure` with
   the failing test named, so a zero elsewhere is a reading and not a collector wired to
   nothing. via: measurement

   | window (ancestry) | verdicts | success | failure | error | pending | store-api test named |
   |---|---|---|---|---|---|---|
   | does **not** carry `ce9b55c3` | 298 | 192 | 64 | 41 | 1 | **12** (4.03%) |
   | **carries** `ce9b55c3` | 99 | 29 | 37 | 29 | 4 | **0** |

   At the pre-window rate the expected count in 99 verdicts is ~4.0, so
   **P(observing 0) ≈ 0.017** — against **0.23** for the only prior reading (`#1512`, 4/125 →
   0/45). 🔴 **The zero is still not what establishes the fix; the mechanism being gone is.**
   Content-checked on `origin/main` so the zero cannot be a deleted test:
   `TestARefusedWriteIsIndistinguishableFromAnAbsentOne` is present (`:14543`), the test is
   present, `sited_root` appears on **106** lines, and the **1** surviving
   `tmp_path / "store"` is at **`:449`** — prose narrating the fix, not a siting.
   ⚠ **`:367` is what rank 22 and earlier revisions of this line both say, and it is STALE** —
   the line moved. Re-derive a line number before quoting one; the file has taken commits since
   `ce9b55c3`. via: measurement

   🔴 **EVERY PER-TEST COUNT HERE IS A LOWER BOUND, AND THAT IS ALREADY MEASURED IN-REPO —
   do not upgrade the 0 into "it did not fail".** A GitHub status description is capped at 140
   characters; **100 of the 101 failure descriptions in this population are 138 characters, i.e.
   truncated**, and `scripts/main-status-watch.py:232-239` records the measurement that matters: a
   row described `failed=7` while naming ONE test, a row named none, and the row that named
   *this* flake was cut mid-word at `…_CAN_see_the_dif`. A description can prove "at least one
   test failed and here is its name"; it can never prove "these are all of them".
   **The bias runs the right way, which is why the comparison survives it:** post-window failing
   runs average **1.83** failures each against the pre-window's **3.60**, so a failure is *less*
   likely to be hidden post-fix, not more. **Hard bound, stated rather than glossed:** 22 of the
   99 post-window verdicts carry ≥1 unnamed failure (29 slots), so the true post count is in
   **[0, 22]**; 42 of the 66 result-bearing post verdicts prove they held no store-api failure.
   via: measurement

   ⚠ **THE DATE PREDICATE WAS WRONG AND CHANGED NOTHING HERE — record that, because the
   opposite assumption would discard `#1512`.** Splitting the same 397 verdicts by status
   timestamp instead of ancestry reclassifies **5**, and **0 of the 101 failures**. So `#1512`'s
   table is not corrupted by its predicate; it was merely underpowered. Ancestry is still the
   predicate to use — this is one sample, not a proof the two agree in general. via: measurement

   🔴 **WHAT THE MEASUREMENT FOUND INSTEAD, AND IT IS THE REASON TO READ THIS RANK: the gate's
   red is now dominated by ledger censuses over tracked text, which are DETERMINISTIC, not
   flakes.** Post-window reds by class: store-api fsync **0** (0.0% of verdicts) · rank 7's
   `run-tests.sh` bound **3** (3.0%) · **tracked-text / ledger census 27 (27.3%)** · other 7
   (7.1%). Pre-window: 12 (4.0%) · 7 (2.3%) · 12 (4.0%) · 33 (11.1%). **The census class is now
   ~7× this rank's flake at its worst, and re-running cannot fix any of it.**
   ⚠ **Split that 27 before acting on it, because half of it is already closed and half is
   live.** **22** are `scripts/claude-hooks/tests/test_guard_core.py`'s kill-mention ledger, and
   **every one predates `c0bbd6d9`** (`#1561`, 2026-09-12T03:16:36Z) exempting `claudedocs/` —
   closed at the source, so that 22 does not forecast. **5** are
   `scripts/tests/test_runner_bound_ledger.py`, and **4 of those POSTDATE `c0bbd6d9`**
   (03:16:50Z–03:46:19Z): the same design class, in a second ledger `#1561` does not cover.
   🔴 **EVERY NUMBER IN THIS WHOLE ITEM IS READ-TIME-ONLY AND CANNOT BE RE-DERIVED LATER — say so
   wherever you quote it.** GitHub keeps **one** status per context per commit and a superseding run
   overwrites it, so the rows this reading counted are progressively destroyed as PRs re-run. A
   re-read hours later already disagreed: 20 kill-ledger reds rather than 22, and 9 runner-bound with
   8 after the fix rather than 5 with 4 — **same direction, strengthening, different values**, with
   the original rows gone. So `27 of 99` is a reading taken 2026-09-12 ~04:00Z over 400 PR heads and
   is **not** a quantity a later session can check. **That is an argument for citing this paragraph
   rather than re-measuring, and for never treating a disagreement with it as a refutation.**
   ✅ **That one was neither a flake nor a stale-base echo — `main` ITSELF WAS RED ON IT, and it is
   now FIXED: `#1567` merged `6f1867b1` at 2026-09-12T05:07:06Z.** Red measured at three successive
   tips (`4e970998` **1 failed, 1655 passed**; `8114a124` and `61f41adf` **1 failed, 4 passed**
   each, that file alone), then **5 passed** at `origin/main` `337114e0` after the merge. It is **rank 8**,
   closed. via: measurement

   ⚠ **THE GATE'S OWN OBSERVABILITY IS WORSE THAN ITS RED RATE: `error` is 29 of 99 post-window
   verdicts (29%) against 41 of 298 (14%) pre. A gate producing no verdict is not a green one, and
   a rate computed over verdicts cannot see the runs that never reported.**
   🔴 **THIS IS NOT A NEW OBSERVATION AND THIS DOC SHOULD NOT RE-MEASURE IT — it is
   `handoff-gate-speed-and-ci-signal.md`'s, at a larger sample and with the decomposition** — its
   bullet beginning *"Only ~21–22 of 100 `main` commits get an authoritative verdict"* (59.5%
   superseded, 15% KILLED, 5% NO GATE POD), and, split on this very fix, the one beginning
   *"`main` is a useless population for this question and that is structural"* (of 43 post-fix
   verdicts, **40 artefacts**, **3** authoritative — *"Use PR heads."*). **Read those, not a count
   taken here.** The four artefact strings (`NO CAPACITY` / `NO GATE POD` / `KILLED` /
   `BROKEN GATE`) are a SHIPPED mechanism, not undiagnosed noise — see
   `handoff-cairn-task-linkage.md`'s bullet *"Four mechanisms now post four strings"*; they make the
   loss legible rather than recovering it.
   🔴 **Those are quoted by their OPENING WORDS, not by line number, and that is deliberate** — an
   earlier revision of this paragraph cited `:363`, having just "corrected" it from `:365`, and then
   **invalidated its own citation by inserting 25 lines above the target in the same commit**. A
   line number in a sibling doc that the same PR edits is an expiring claim. Quote the sentence.

   🔴 **THE CENSUS THAT REPLACED A WRONG COUNT WAS ALSO WRONG, AND THE SECOND MISS IS THE
   INSTRUCTIVE ONE.** The original said `main`'s "last 8 commits" held *six* `superseded`, two
   `NO GATE POD`, newest pending. Its first correction said **four**, one `KILLED`, one pending.
   **Re-read 2026-09-12T05:2xZ over the same named range `457a5dc7~1..8114a124`: FIVE superseded,
   two `NO GATE POD`, one `KILLED`, and ZERO pending.** The headline — *no completed verdict across
   those 8* — held every time; the breakdown was wrong twice.
   ⚠ **Naming a sha range fixed the wrong axis.** A range pins the COMMITS; it does not pin their
   VERDICTS, and the verdict is the mutable thing: `8114a124` read `pending` when the first
   correction was written and had flipped to `superseded` by 04:32:10Z — **24 minutes before that
   commit was even authored**. GitHub overwrites a status per context, so the earlier rows are
   *gone* and no later reader can reproduce either number. 🔴 **So a verdict census needs a READ
   TIME as well as a range, and it is never re-derivable afterwards — which is the argument for not
   writing one here at all.** Kept only as the worked example of the class.

   **What this does NOT close:** nothing here was measured on the OSS copy
   (`ZacxDev/cairn`'s identical 18-open-coded / 5-sited split), and the `[0, 22]` bound above is
   the residual this instrument cannot narrow — the pipelineruns that could have are pruned.
   ⚠ **And the item's own placement is now wrong, stated plainly rather than left as a mood:** a
   store-api fsync flake measured at 0 is no longer this doc's Goal, and rank 9 is not either — it
   is "what is CI's signal worth", which `handoff-gate-speed-and-ci-signal.md` owns and already
   tracks (its Open-investigation on the census family, and its own 140-byte-cap finding).
   🔴 **This is NOT a work item and is deliberately not dressed as one.** An earlier revision called
   moving them *"a doc-surgery task with a real closing condition"* — it named no doc, no owner and
   no mechanical check, and *"nothing cross-referencing them twice"* is not decidable, so by
   `claude/RULES.md`'s own test it was an object nobody could close. **It is an observation for
   whoever next restructures these two docs.** Rank numbering stays stable regardless; claims are
   keyed to it.

   🔴 **THE PRESCRIPTIVE HALF OF THIS ITEM NOW LIVES IN `## How to verify`, NOT HERE.** The
   runnable form — anchor, ancestry predicate, the three positive-control PRs — is one block, at
   the bottom of this doc. What follows below is kept as PROVENANCE (why the anchor is
   `ce9b55c3` and not `65f7325b`, and why a date filter is wrong), not as instructions: the
   measurement has been taken, so read it as the record of a decision rather than a recipe.
   Duplicating a recipe is what `claude/RULES.md` calls one rule in two places.

   — superseded text. ⚠ **Its stated justification did NOT survive re-reading and is corrected
   here:** this was kept as "a caveat still true of any FUTURE baseline", but it is a warning about
   one specific PAST baseline, and rank 1's reading now supplies its own pre-window baseline
   (12/298), so the 2026-09-01 figure is no longer anything's comparison point. The NUMBER is kept
   because `supersede.md` says values age well; the claim about its scope is withdrawn. The old
   baseline (5 store-api failures among 14 open PRs, 2026-09-01) was **not comparable** — it was
   measured against a partially-sited suite.
   🔴 **THE ANCHOR MOVED AGAIN — `65f7325b` IS NO LONGER THE LAST INTERVENTION.** `#1458`
   (squash **`ce9b55c3`**, 2026-09-10) found that `#1211`/`#1219`/`#1239` sited **5** store
   roots in `test_subsystem_store_api.py` and left **18** open-coded on disk, including the one
   test that kept reddening the gate — and sited all 18. **Verified: `ce9b55c3^` has exactly 5
   `store_siting.store_root(` sites and 18 `tmp_path / "store"`; after it, 1 remains (a
   docstring).** Use **`ce9b55c3`** as the anchor. 🔴 **The contaminated set is "carries
   `65f7325b`" with NO upper bound** — it mixes heads carrying one intervention with heads
   carrying two. Heads carrying the first and not the second are fine to count *as* the first.
   🔴 **AND THE PREDICATE IS ANCESTRY, NOT DATE — every remaining "postdates" in this doc is the
   wrong test.** A branch cut before an intervention and never rebased has commits dated after it
   and does **not** carry it, so a date filter gives a wrong denominator on the one measurement
   this rank exists to produce. Use `git merge-base --is-ancestor <sha> <head>`. Say which anchor
   any recorded number is against — the 2026-09-12 reading above is against `ce9b55c3`, by
   ancestry, and it also reports what the date predicate would have given on the same 397
   verdicts (5 reclassified, 0 of them failures). Full evidence:
   `handoff-cairn-oss-multi-instance.md` rank 22.
   ⚠ **AND THE POPULATION IS NO LONGER ONE BOUND — see rank 7.** A store-api red and a
   `run-tests.sh` subprocess timeout are both "a check red on a diff that cannot reach it", so a
   rate that counts reds without classifying them will read rank 7's flake as this one failing
   to close. **Classify by the failing TEST before counting.** ⚠ Do **not** read that as "and
   the causes are unrelated" — rank 7 records why that is unestablished.
   forcing: **none — done.** ⚠ **This read `forcing: gate — a check has been failing PRs whose diff
   cannot reach it` until 2026-09-12, and rank 1's OWN payload disproves it for this flake: 0 of 99.
   A forcing function that survives the measurement retiring it keeps the item at position 1 of a
   ranked list and is exactly how a finished item attracts a session.** The forcing claim did not
   vanish, it MOVED — it is now rank 8's (`main` red on the runner-bound census) and rank 9's, both
   below. Retained, unrenumbered, so live `claim-work` refs keep resolving — see rank 5's precedent.
   ⚠ **And the advisory qualifier still applies to every gate item here, so it is kept:** measured
   2026-09-10 and re-measured 2026-09-11 — `main` has no required status
   checks, no rulesets, `enforce_admins: false`. See this doc's Gotchas for the qualifiers
   (the state is deliberate and is not yours to restore). 🔴 **`claude/skills/tekton/SKILL.md`
   says the OPPOSITE** — "requires both", "`enforce_admins: true`" — and is STALE; an earlier
   draft of this line cited it as corroboration. Re-read the setting live; cite no doc for it.
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
6. ✅ **CLOSED 2026-09-12 — the espanso `:acq`/`:rna` collision is GONE and `main` is NOT red on it.**
   Measured at `origin/main` `337114e0` by the criterion this item itself names as the deterministic
   check: the keylog suite → **104 passed** at `origin/main` `337114e0`. The underlying terms are now disjoint in `nix/home.nix`
   (`:acq` = ask/clarify/clarifying/questions; `:rna` = recommend/recom/next/actions/rank/leverage).
   Not fixed by this effort — it was someone else's one-line `search_terms` change; recorded because
   the item asserted a live red.
   ⚠ **It carried `forcing: regression — `main` is red` while the test was green, and `forcing:` is
   the machine-readable field a `claim-work` session sorts on** — so the stale value was not cosmetic,
   it advertised a gate-forcing regression with nothing to fix behind it. A bash comment 300 lines
   away telling the reader to re-verify does not undo that. **Retire the `forcing:` in the same edit
   as the red.**
   forcing: none — fixed; the diagnosis above is retained as the worked example.

7. **A FIXED 120 s SUBPROCESS BOUND IN A FILE WHOSE WALL TIME IS NOT STABLE —
   `scripts/tests/test_run_tests_targets.py`.** Its tests spawn a nested `run-tests.sh`,
   SIGKILLed at the bound (`TimeoutExpired`, rc `-9`). **Six bound sites** — `_run` `:107`,
   `_run_env` `:862`, inline `:658`/`:804`/`:830`/`:961` — reaching 26 of 29 test functions
   (31 collected). via: code
   - 🔴 **THE CAUSE IS NOT ESTABLISHED, AND THREE EARLIER DRAFTS OF THIS ITEM ASSERTED ONE
     ANYWAY.** Do not write a cause in here without a signal that DISCRIMINATES. Rivals
     checked so far: **`#1429` (`a0839ec4`, 2026-09-09) is REFUTED for this tier** — it changed
     the worker budget from `min(nproc, 4)` to `min(nproc, cgroup quota, 8)`, and the
     `devrc-ci-gate` `pytests` step sets `limits.cpu: "4"`, so both formulas yield **4**. It
     doubled the ceiling only on an unquotaed host. Node contention at the moment of a kill was
     never measured, and the pipelineruns were pruned. via: measurement
   - 🔴 **THE OBSERVED SPREAD IS THE HAZARD — DO NOT QUOTE A POINT WALL TIME FROM HERE.**
     Four measurements of this file: **137.69 s, 164.27 s, 205 s, 428.40 s — a 3.11x spread**,
     same tree, no code change. The four spawn-bearing tests, each observed twice or more:
     `:418` `test_a_pinned_skip_whose_TARGET_did_not_run_does_not_count` **43-71 s** (the worst),
     `:988` SUMMARY_BANNER **41-57 s**, `:723` subset-note **39-84 s** (two spawns),
     `:582` `test_a_partial_run_is_declared_where_gate_sh_actually_LOOKS` **38-70 s**.
     **A fixed 120 s bound sits inside that spread.** via: measurement
   - 🔴 **RAISE EVERY SITE; DO NOT REMOVE THE BOUND, AND DO NOT MAKE THE NESTED RUNS
     CONCURRENT.** The bound is a detector — `:74`/`:274` cite it as what caught the original
     defect, a swallowed flag making every spawn run the FULL set, which `run-tests.sh:3836`
     measures at **1194 s serial** against these ~40-70 s one-target spawns. A raised bound
     still catches that; removing it does not. And `run-tests.sh:3889` is 🔴 **"NESTED RUNS MUST
     BE SERIAL"**, enforced at `:4043` and guarded by
     `test_a_nested_pytest_session_does_not_write_into_the_targets_ledger` — so concurrency is
     not available as a remedy. via: code
   - **RATE, measured 2026-09-12 by rank 1's instrument and split on the same anchor:** this
     file's tests are named in **7 of 298** verdicts on heads that do not carry `ce9b55c3`
     (2.3%) and **3 of 99** on heads that do (3.0%) — `#1454`, `#1462` and `#1499`, all
     `test_the_SUMMARY_BANNER_names_the_real_selection_source`. **Unchanged, not closed**, and
     n=3 cannot distinguish 3.0% from 2.3%. Same lower-bound caveat as rank 1: a 138-character
     status description names a subset. via: measurement
   - **Three occurrences, two tests, all merged red:** `#1458`@`a6dd11eb` (subset-note),
     `#1462`@`dc972398` and `#1454`@`c5e3123e` (SUMMARY_BANNER); the last two posted **4 s
     apart**. ⚠ The test FILE has not changed since 2026-08-30 (`ca088e70` `#289`, `809486fa`
     `#1073`) — but **`scripts/run-tests.sh`, which the spawns execute, has 21 commits since**,
     so "nothing changed" is false and was wrongly asserted here once. via: measurement
   - ⚠ **If it recurs, pull the log before the hourly pruner (`keep: 20`):**
     `KUBECONFIG=$KC_HOMELAB kubectl -n tekton-ci get pipelineruns -o json`, filter
     `.spec.params[] | select(.name=="revision")`, then `KUBECONFIG=$KC_HOMELAB kubectl -n
     tekton-ci logs pod/<run>-gate-pod -c step-pytests`. There is **deliberately no default
     `KUBECONFIG`**; a copy of this recipe without the prefix does not run.
   **Closing condition:** all six sites raised — sized against the **upper** end of the observed
   spread, not a point value. 🔴 **Verify with the detector's own positive control, not an
   absence:** after raising, confirm a **full-set** spawn still breaches the new bound. "No test
   appears in a `FAILING:` line" is not a check — that string is a 140-char GitHub status
   description that already truncates mid-token, and a rename, skip or deselect satisfies it
   with nothing fixed.
   ⚠ **WHY IT IS KEYED TO ALL SIX SITES AND NOT TO A TEST — a retracted draft, kept so nobody
   re-derives it.** An earlier version closed on one test name. A second exposed test
   (`:988`, through `_run_env` rather than `_run`) would have walked straight past it, and a
   later version keyed to `_run` alone would have missed that same test — which is **2 of the
   3** observed occurrences. Narrowing this key is how the condition gets satisfied with the
   hazard intact.
   forcing: gate — it reddened `#1454`, `#1458` and `#1462`, all merged with
   `tekton/devrc-pytests` RED because of it.

8. ✅ **CLOSED 2026-09-12 — `main` WAS RED, DETERMINISTICALLY, ON THE RUNNER-BOUND LEDGER, AND
   `#1567` FIXED IT (`6f1867b1`, merged 05:07:06Z).** Closing condition met and checked the
   mechanical way: `scripts/tests/test_runner_bound_ledger.py` → **5 passed** at `origin/main` after
   the merge, against **1 failed, 4 passed** before it. 🔴 **Kept at full length because the CLASS is
   what matters — it is the class rank 1's measurement found has replaced the flake this doc was
   written to chase. At least TWO instances in two days, both enumerated in rank 1 (kill-mention 22, runner-bound 5); no third is enumerated anywhere, so do not write one.**
   Measured 2026-09-12 by running the file directly at `origin/main` `4e970998` in a clean worktree:
   `…::test_every_site_writing_its_OWN_runner_bound_is_in_the_ledger`
   → **1 failed, 1655 passed in 251.49s**, and re-measured after each of the two times `main` moved
   under this work — `8114a124` (**1 failed, 4 passed in 19.56s**) and `61f41adf` (**1 failed, 4
   passed in 17.57s**), that file alone — so the red was a property of the tree across THREE points,
   not of one run. On the `new` arm:
   `{('scripts/tests/test_scoped_tests_shared_surface.py', 'ABSENT'): 1}`. That file spawns a
   runner with **no bound of its own** and has no `_OWN_BOUND_LEDGER` row, so the two-way seam
   ledger fires exactly as designed. **Not a flake, not a stale-base echo** — the control is that
   the only diff in that worktree was two `claudedocs/` files, which these scanners do not read.
   via: measurement
   - **Rate, same instrument and anchor as rank 1:** named in **5 of 99** verdicts on heads
     carrying `ce9b55c3`, **4 of them after `c0bbd6d9`** (`#1561`) closed the *sibling* census —
     `#1524`@03:16:50Z, `#1556`/`#1559`/`#1522`@03:40–03:46Z — plus `#1494`@03:14:37Z.
     via: measurement
   - ✅ **FIXED BY `#1567`** (`fix/scoped-surface-runner-bound`, opened 2026-09-12T03:58:34Z, head
     `57030bfd`, **merged `6f1867b1` at 05:07:06Z** — 1 h 08 m later), which had the same diagnosis
     and took the right arm. 🔴 **The pre-create sweep is what caught this** — the red was measured and filed here
     at the same hour `#1567` was opened, and the lock could not have seen it, because nothing
     was claimed. This is the class `design-claim-by-push.md` lists as NOT covered.
   - **The fork in the fix, and `#1567` resolves it the right way.** The assertion offers two
     arms: add a ledger row **with its reason**, or route the spawn through
     `testlib.scoped_harness.run()` so it takes `RUNNER_TIMEOUT_S`. A row would have *recorded*
     an unbounded suite that can hang forever; the harness *removes* it. `#1567` routes, and
     positive-controls the fix by reverting to the unbounded spawn and watching the guard return
     to `[ABSENT] x1`. Provenance it also names: the file arrived with `#1532` and reddened the
     guard on landing.
   ✅ **Closing condition MET 2026-09-12:** `#1567` merged (`6f1867b1`) **and**
   `test_every_site_writing_its_OWN_runner_bound_is_in_the_ledger` green on `origin/main` `337114e0`
   — run, not assumed: **5 passed**. ⚠ **The window from filing to closed was 1 h 08 m, and this item spent
   most of it asserting `main` is red "RIGHT NOW" in four places.** That is the same defect class as
   the tip-sha line in `State now`: a present-tense claim about a mutable state, with nothing to
   expire it. **Write the measurement and its timestamp; let the closing condition carry the
   present tense.**
   forcing: none — fixed. It reddened the gate on 5 PR heads and `main` itself; retained because the
   CLASS (a census over tracked text reddening `main` for everyone) is `claude/RULES.md`'s
   "a permanently-red gate is worse than no gate" and recurred at least twice in two days.

9. ⚠ **`#1508` DELETED `scripts/lib/subsystem_recall.py` AND 61 HANDOFF DOCS STILL PRESCRIBE
   RUNNING IT.** Measured 2026-09-12 at `origin/main` `8114a124`, by `git grep` against the ref
   rather than a working tree: the **command** spelling
   `python3 ~/workspace/devrc/scripts/lib/subsystem_recall.py` appears **65** times in **61** files
   under `claudedocs/` — each a "Run this first" block that now fails file-not-found. (The bare
   PATH appears 71 times in 63 files; the extra 6 are prose about the path, not invocations, which
   is why the closing condition below is keyed to the command form.) **After the PR that filed this
   item, the condition's own grep counts 64 across 60 files** — this doc's invocation fixed, its
   remaining prose mentions excluded by path. Quote whichever you mean; they are not the same number.
   The replacement is
   `cairn recall --repo <path>`. Positive control on the same read: `cairn recall` already appears
   54 times in `claudedocs/`, so the corrected spelling is in use and the grep can see it.
   via: measurement
   - ✅ **No code path is broken — all 6 source-file references are comments or docstrings**:
     `scripts/lib/handoff_search.py`, `scripts/present/measure.py`,
     `scripts/tests/test_subsystem_recall.py`, `scripts/tests/test_repo_path_guard.py`,
     `scripts/tests/test_store_root_ledger.py`, and `scripts/subsystem-store-api/Dockerfile` —
     whose `:36` is **already correct** (*"there is nothing at `scripts/lib/subsystem_recall.py` to
     COPY"*) and whose `COPY` at `:61` takes a vendored `.cairn-lib/` copy. ⚠ **An earlier count
     here said 5 and missed the Dockerfile**, because the sweep was an extension-filtered `find`
     rather than a `git grep` over the ref. Enumerate from the ref.
   - 🔴 **THIS IS NOT NEW — the index has carried it as an `OPEN:` bullet since 2026-09-01, and
     that bullet names this exact remedy.** `cairn recall --ref subsystem-store-api --scope devrc`,
     the `2026-09-01: OPEN:` bullet *"THE CUTOVER LEFT TWO STORES AND `/resume` READS THE DEAD
     ONE"*, whose third listed fix is *"rewrite the prescribed command in the
     `resume`/`subsystem-index` skills and both handoff docs to `cairn recall`"*. **Read that
     bullet before working this** — it also records the frozen-mirror half, which this item does
     not, and it is the older object. What this rank adds is only the POPULATION (65 invocations /
     61 files, re-counted at `8114a124`) and the fact that `#1508` has since made the command fail
     outright rather than merely read a stale store. ⚠ **Found by `cairn recall` at resume time,
     not by searching** — the recall surface answered a question this doc had not asked.
   - **Found while editing one of those docs, not by looking for it.** Only
     `handoff-gate-flake-store-api.md`'s own invocation is fixed (in the PR that filed this); the
     other 64 are untouched, deliberately — a one-site fix that reads as a sweep is the failure
     `claude/RULES.md` names under "one rule, one place".
   - ⚠ **Same CLASS as `handoff-cairn-oss-multi-instance.md` rank 23 (stale spellings the
     consolidation left behind), DIFFERENT population** — rank 23 is `subsystem_touch.py
     --validate` under `claude/skills/`; this is `subsystem_recall.py` under `claudedocs/`. Do not
     close either by the other's grep.
   - 🔴 **A doc's "Run this first" line is the one command a resuming session runs before anything
     else**, so this fails at the moment it is least expected and reads as a broken environment.
   **Closing condition:**
   ```
   git grep -c 'python3 ~/workspace/devrc/scripts/lib/subsystem_recall.py' origin/main \
     -- 'claudedocs/*' ':(exclude)claudedocs/handoff-gate-flake-store-api.md'
   ```
   sums to **0**. ⚠ **READ THAT AS "60 of the 61 docs", NOT "no doc prescribes the dead path".** The
   exclude buys satisfiability at the cost of coverage, and the excluded file is the very one whose
   "Run this first" block was the motivating instance — so this condition can read 0 while THIS doc
   prescribes it. Mechanically confirmed in both directions on a throwaway copy: adding the dead
   invocation to any OTHER `claudedocs/` file moves it 64 → 65; adding it to this file leaves 64.
   A narrower exclusion (key on the fenced block rather than the path) would close that; the two
   surviving occurrences here genuinely must be printed.
   🔴 **THE EXCLUDE IS LOAD-BEARING AND AN EARLIER VERSION OF THIS CONDITION WAS
   UNSATISFIABLE WITHOUT IT — while asserting, in the same breath, that it was not.** That version
   read *"keyed to the invocation, not the bare path — docs that merely discuss the dead path
   (including this item) must not make the condition unsatisfiable"*, which was false: this item
   discusses the dead path **using the full invocation spelling**, three times. Measured at that
   revision: the grep returned **67** across 61 files, of which **3** were this file's own prose.
   **A condition keyed on a literal string that the item MUST print can never reach zero** — the
   invocation-vs-path rewrite moved the defect between spellings instead of removing it. Mechanical;
   checked by running that grep, and re-check the exclude still names this file if it is renamed.
   forcing: none — nothing is broken at runtime; it wastes a session's first command.

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

# rank 6's keylog red — NO LONGER RED. Measured 2026-09-12 at origin/main 337114e0: 104 passed.
#   The sha is the point: an unpinned origin/main reading is the defect rank 1 names.
#   ⚠ This line read `# 1 failed, 100 passed` with "expected until rank 6 is fixed"; rank 6 itself
#   still says `main` IS RED on it. Re-verify rank 6 before acting on it — this is the fourth
#   present-tense red claim in these docs found stale in one session.
nix develop $DEVRC -c env PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  $DEVRC/scripts/collector/keylog/tests -q -p no:cacheprovider    # expect 104 passed
gh api repos/innovation-upstream/devrc/commits/$(git -C $DEVRC rev-parse origin/main)/status \
  --jq '.statuses[]|"\(.context) \(.state): \(.description[0:100])"'
#   ⚠ This can show NO completed verdict at all, and that is the NORMAL case, measured at n=100 in
#   handoff-gate-speed-and-ci-signal.md — its bullet "Only ~21–22 of 100 `main` commits get an
#   authoritative verdict". A gate producing no verdict is not a green one: read the state, never
#   just the absence of a `failure`, and prefer PR heads to main for any rate (that doc's bullet
#   "`main` is a useless population for this question"). Quoted by opening words, not line number —
#   both targets moved when this PR edited that file.

# rank 8 — CLOSED by #1567 (6f1867b1). This is its closing condition, so expect 5 PASSED.
#   A `1 failed` here means the census has regressed, NOT that the recipe is stale.
#   ⚠ This line said `expect 1 failed` for the 70 minutes rank 8 was open, which would have read
#   as a broken recipe the moment it was fixed. A verify-block expectation must track the
#   closing condition, not the state at the time of writing.
nix develop $DEVRC -c env PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  $DEVRC/scripts/tests/test_runner_bound_ledger.py -q -p no:cacheprovider

# rank 1 — re-run the rate read. The predicate is ANCESTRY; a date split is the wrong test
# (it reclassified 5 of 397 verdicts and 0 of 101 failures on 2026-09-12, which is luck, not
# licence). Heads come from refs/pull/*/head; fetch them into a THROWAWAY repo so the shared
# clone's ref namespace is not written to, then per head:
#   git merge-base --is-ancestor ce9b55c3 <head>       # rc 0 = carries it
#   gh api repos/innovation-upstream/devrc/commits/<head>/status   # newest verdict per context
# 🔴 Positive-control the collector on `#1458`@`a6dd11eb`, `#1462`@`dc972398`,
#   `#1454`@`c5e3123e` — all three must come back `failure` WITH a test named, or a zero
#   elsewhere is a collector wired to nothing rather than a reading.
```
🔴 Do NOT run these from a worktree whose path contains `fsync`, `flock`, `_EntryLock` or
`_audit_lock` — the hung-server classifier substring-matches rendered tracebacks, which
include FILENAMES, and the failure is the PATH, not the code (rank 2).
