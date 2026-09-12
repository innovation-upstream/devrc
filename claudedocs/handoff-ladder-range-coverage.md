# Handoff: ladder-range-coverage — 2026-09-11

## Run this first — the index, one command
```bash
cairn recall --repo /home/zach/workspace/devrc
```
Terse pointers this doc does not carry, curated by past sessions and outliving it.
🔴 RECALL, NOT LIVE OBSERVATION — every line is a pointer to VERIFY, never a current
reading, and it may describe a gotcha already fixed. `scope-absent`/`scope-empty` means
nothing is recorded yet: ordinary, not an error, and not a clean bill of health.
Non-blocking: if it exits non-zero, print the stderr line and carry on.

## Goal
Measure the churn that an audit ladder's `audit-claims` blocks do NOT cover, in devrc and
then outside it. Came in as ranks 10 and 8 of `handoff-audit-pr-ladder.md`; that doc has
since renumbered and dropped both, so this is the durable home.

## State now
- **Branch / PR:** `#1552` OPEN (`fix/churn-commit-population`, worktree
  `/home/zach/workspace/devrc-tailclass`) — the commit-population fix plus the tail
  classification. Previous two both MERGED: `#1519` → `766c1295`, `#1528` → `b42ac7c3`.
- ✅ **Ranked item 1 DONE** — `feat/ladder-carrier-enumerator` landed as `#1528`; the rank-8
  table is committed, no longer working-tree-only.
- **CARRIED FORWARD (the previous `State now` would have dropped it):** open items **5** (the
  range-coverage hole) and the devrc-only half of the CANNOT-SEE bullet in
  `claudedocs/audit-ladder-review-2026-09-04.md` are marked CLOSED in that doc, each with its
  residual stated. That doc — not this one — is the durable home for the measurement results.
- ✅ **Ranked item 2 DONE (pending #1552's merge)** — the tail is classified, and the answer
  REVERSES this doc's own leading hypothesis. See the investigation below.
- 🔴 **A defect found in the SHIPPED brief, fixed in `#1552`:** `measure_ledger` paired a
  commit count from `<frm>..<to>` with a line count from the same range `--not <base>`, and
  `audit-dispatch.py` printed the former as *"over N commit(s)"* beside the latter's command.
  Measured: #1046's tail reported **55** commits when **2** contributed churn. Line counts are
  unaffected; only the commit counts were wrong, and always in the flattering direction.
- **Deploy/verify status:** nothing in `#1519`/`#1528`/`#1552` needs a `home-manager switch`
  except `#1519`'s one managed path (`claude/skills/audit-pr/reference/round-ladder-evidence.md`),
  which is **deployed and byte-verified on BOTH hosts** — `md5 cfaf3169` identical across
  `origin/main`, workbench and laptop. `#1552` touches `scripts/` and `claudedocs/` only.
- ⚠ Still no `clawgate-task:` field — `clawgate_handoff.sh resolve` exits **5** (nothing
  resolved), which cannot distinguish "touched no task" from "wrong id". Not a clean bill.

## Open investigations — live diagnosis state

### The TAIL is 10,052 lines externally and nobody knows what it IS — the one open question
- **Symptom + exact repro:** rank 8 is measured, but its largest number is uninterpretable.
  Re-derive any repo with:
  ```bash
  nix develop /home/zach/workspace/devrc -c python3 \
    /home/zach/workspace/devrc-ladder-find/scripts/ladder-range-coverage.py \
    --find-carriers --repo <slug> --repo-dir <checkout> --limit 400
  ```
  Slugs → checkouts: `ZacxDev/homelab-infra` → `~/workspace/homelab-talos`;
  `civitai/talos-infra` → `~/workspace/civit/datapacket-talos`;
  `vetrllc/vetr-api` → `~/workspace/vetr-api`; `vetrllc/vetr-app` → `~/workspace/vetr-app`;
  `civitai/gpu-fleet-infra` → `~/workspace/civit/civitai-gpu-fleet`.
- **Observed (with values):** INTERIOR totals are 88 (homelab-talos, 2 adjacencies) and **0**
  in all four other measured repos, against 655 across 20 devrc ladders. TAIL totals are
  7,314 / 2,107 / 115 / 18 / 498 — **10,052 lines, and 126 of 129 external ladders measured**
  (3 REFUSED on the positive control: their commits are not in the local checkout). Full
  per-repo table in `claudedocs/audit-ladder-review-2026-09-04.md`.
- **Ruled out:** *"the interior hole generalises outside devrc"* — it does not. 88 lines across
  126 external ladders, all of it in one repo, versus 655 across 20 devrc ones. via: measurement
- **Ruled out:** *"a zero carrier count means the repo is clean"* — naida-ai ran 214 PRs and
  posted **no** ledger at all, so there is nothing to measure coverage against; the script
  reports UNMEASURABLE with that reason rather than 0. via: measurement
- **Ruled out:** *"TAIL churn is unaudited ladder work"* — NOT established, and the script
  refuses to say so. It conflates fixes posted after the final block with development that
  simply continued after the ladder ended. via: code (the `r_to is None` split in
  `measure_ladder`, and the caveat `render` prints)
- **Leading hypothesis:** most of the tail is ordinary post-ladder development, not missed
  audit surface — which would mean the range-coverage hole is a real but SMALL defect (88
  lines outside devrc) and the 10,052 is mostly noise. devrc's interior gaps are probably a
  local *authoring* habit: titling one comment "rounds N and N+1" and posting one block for
  both, which is exactly #1233's shape.
- **Next probe:** read the COMMITS behind the three largest tail gaps (homelab-talos leads at
  7,314 lines) and classify each as post-final-block FIXES vs ordinary development. That is
  the one question the ranges cannot answer, and the whole reason interior and tail are
  reported separately.

### `main` is red on a gate that no diff can fix, and it is getting worse by itself
- **Symptom + exact repro:** `tekton/devrc-pytests` fails on
  `test_every_kill_server_call_site_in_the_repo_is_classified`.
  ```bash
  nix develop /home/zach/workspace/devrc -c python3 -m pytest \
    scripts/claude-hooks/tests/test_guard_core.py -k kill_server_call_site -q
  ```
- **Observed (with values):** on a clean `origin/main` worktree at `50e8a71a` it fails with
  `added: ['claudedocs/handoff-tmux-webapp.md']`; on a feature branch off `289c0193` it fails
  with `added: ['claudedocs/handoff-tmux-scratchpad-bar-statusline.md']`. Different files,
  same assertion: `set(mentions) == set(_KILL_MENTION_LEDGER)` in
  `scripts/claude-hooks/tests/test_guard_core.py:2625`.
- **Ruled out:** *"PR #1519 broke it"* — that diff never mentions `kill_server`
  (`git diff origin/main...<branch> | grep kill_server` is empty) and the failure reproduces
  on an unmodified `origin/main` checkout. via: measurement
- **Leading hypothesis:** the ledger enumerates FILES that mention a wide tmux kill, so every
  new handoff doc that mentions one is an unclassified entry. It is on track to be a
  permanently-red gate, which `claude/RULES.md` rates worse than no gate.
- **Next probe:** decide whether the ledger should enumerate `claudedocs/` at all — a doc
  cannot execute a kill — or whether the scan should be scoped to executable paths. That is
  the fix; adding each new doc to the ledger is the treadmill.

### RESOLVED — the TAIL is missed audit surface, not development. This doc's own hypothesis was WRONG.
- **Symptom + exact repro:** the tail was 13,779 lines across 79 adjacencies and
  uninterpretable. Classified by reading every commit in the range, with the exclusion the
  measurement applies:
  ```bash
  git -C <repo> log --format='  parents=%p :: %s' <frm>..<to> --not <base>
  ```
  🔴 **Omitting `--not <base>` there is the trap I nearly published from** — without it the
  listing shows `main`'s own squash commits and #1046's tail reads as 55 unrelated PRs.
- **Observed (with values):** five largest devrc tails (#1046, #1000, #1209, #1121, #998 —
  3,349 of devrc's 3,727 tail lines, **17 commits**) plus homelab-talos #707 (1,276 lines, 1
  commit): **9** fixes/audit responses (one names *"audit round 5"* in its subject), **4**
  merge-conflict resolutions, **3** docs/handoff/CI-retrigger, **1** feature.
- **Ruled out:** *"most of the tail is ordinary post-ladder development"* — this doc's leading
  hypothesis, **falsified**. One of seventeen commits is a feature. via: measurement
- **Ruled out:** *"`--remerge-diff` inflates a main-merge's churn"* — suspected when one merge
  contributed 1,039 of #1046's 1,105 lines. It does not: that merge's own message documents a
  renumbered exit constant and 515/487 rewritten lines in one test file. Real hand resolution,
  shape B of the reference table working as designed. via: measurement
- **Leading hypothesis (the part still open):** the unclassified 73 adjacencies — a long tail
  of small gaps — may be more development-heavy than the top 6. The top of a distribution is
  not the distribution.
- **Next probe:** classify a RANDOM sample of 10 of the remaining 73, not the next-largest
  ones, so the sample is not length-biased the way this one is by construction.

## Next steps (ranked)
1. **Merge `#1552`** and release claim `ladder-range-coverage-2`. It carries the commit-count
   fix for the shipped brief, so every `audit-dispatch.py` ledger keeps mis-stating its commit
   population until this lands.
   forcing: regression — the brief prints a commit count from the wrong population on every round
2. **Classify a RANDOM 10 of the 73 unclassified tail adjacencies.** The 6 already done were
   picked by size, so they are length-biased by construction and cannot speak for the rest.
   forcing: none
3. **Fix or rescope `_KILL_MENTION_LEDGER`** so a new handoff doc cannot red the gate.
   `tekton/devrc-pytests` is red on `main` itself; reproduced on a clean `origin/main`
   worktree at `50e8a71a`.
   forcing: gate — red on `main` today, for every PR
4. **Rank 9 — mine the stop-rationale prose across every carrier.** `--find-carriers
   --list-only` now produces the population (199 across seven repos) that used to be
   hand-built. Durable home: open item **4** of `claudedocs/audit-ladder-review-2026-09-04.md`.
   forcing: none

## Gotchas / decisions / dead-ends
- 🔴 **THE RANKED LIST OF `handoff-audit-pr-ladder.md` WAS RENUMBERED AND THREE ITEMS
  VANISHED — one of them while I held its claim.** That doc's preamble said *"Numbering is
  STABLE — the rank is half a `claim-work` slug's identity"*; the rewrite (merged as
  `bfcc3b20`, #1511) replaced ranks 1–16 with an unrelated 5-item list. Old **8**
  (churn-measure outside devrc), **9** (stop-rationale) and **10** (range-coverage) have
  **zero** traces in it — grepped for `range-coverage`, `Churn-measure the ladders OUTSIDE`,
  `stop-rationale`, `42 block-carrying`. Old 14 and 16 were genuinely resolved and are
  visible in its `State now`. **The slug namespace and the list drifted apart silently:** I
  was holding `audit-pr-ladder-10` against a rank that no longer existed, and a stable-slug
  promise is worth nothing without a guard. Recorded on #1511 as a comment.
- 🔴 **RUNNING THE INSTRUMENT AGAINST A SECOND CORPUS IS WHAT FOUND ITS DEFECT.** The
  zero-line-gap caveat shipped in `#1519` as the literal *"Three of the 20 ladders look like
  that"* — the devrc figure — and then printed that sentence verbatim under a 5-ladder run of
  `vetrllc/vetr-app`. A count in prose beside a measurement it is not computed from is the
  exact class the report exists to find, committed inside the report. Now derived and pinned.
  **A tool validated on one corpus is validated on one corpus.**
- 🔴 **THE TWO CALLERS OF A SHARED CHURN CORE WANT OPPOSITE THINGS FROM AN EMPTY RANGE, and
  that is why the extraction is a separate function rather than a parameter.** For a delta
  round an empty range is a broken question (`measure_ledger` spends three branches
  diagnosing it); for the gap between two blocks it is the HEALTHY answer. A core enforcing
  the delta rule would report every well-formed ladder as unmeasurable. Both directions have
  their own mutant row, because one mutant cannot show both.
- 🔴 **A GAP OF MANY COMMITS AND ZERO LINES IS THE INSTRUMENT WORKING, NOT A BUG.** #1064
  (125 commits), #1274 (10), #1110 (7) all measure 0 lines: `--not <base>` excluding an
  upstream bring-in, shape A of `round-ladder-evidence.md`'s range table. **A commit count is
  not a churn count** — and a reader who conflates them will "fix" the exclusion that is
  doing its job.
- 🔴 **INTERIOR AND TAIL MUST NOT SHARE A HEADLINE.** devrc: interior 655, tail 3,727.
  homelab: 88 vs 7,314. A single total gets quoted as an under-count it does not support,
  because only the interior half is unambiguously churn nobody audited.
- **A CI red is weak evidence; the cheap control settles it in one command.** `#1519` went red
  on a test in a file its diff never touched. Running that same test on a clean `origin/main`
  worktree took under a minute and proved it inherited — against a documented ~42% noise rate
  on this check, the control is always cheaper than the reasoning.
- **`gh pr list --json comments` returns comment bodies, so the carrier scan is ONE call.**
  Asking per PR is ~300 calls against a secondary rate limit, which is why the carrier
  population had been hand-built twice. ⚠ It does **not** return REVIEW comments, so every
  carrier count from it is a **FLOOR, not a census** — the same blind spot
  `audit-dispatch.py` warns about when it cannot see a block a human can.
- **The content gates read `git ls-files`, so they are blind to unstaged files.** I ran them
  once before staging and once after; only the second run is evidence about the new files.
  A green gate over files it could not see is not a green gate.
- **A merged branch is gone from the remote, so follow-on work needs a fresh one.** `--delete-branch`
  removed `fix/ladder-range-coverage`; branching the next change off its local tip would have
  re-introduced the already-squashed commits as unmerged. Copied the three modified files
  aside with `cp -a`, made a new worktree off `origin/main`, copied them in.
- ⚠ **`--find-carriers` fetches `refs/pull/<n>/head` into the target checkout**, which for the
  client repos means writing objects into shared clones. Additive only — it moves no ref and
  touches no working tree — but it is a write, and two of those repos are client-owned.

- 🔴 **A COUNT AND A MEASUREMENT BESIDE IT CAN COME FROM DIFFERENT POPULATIONS, AND THE SHIPPED
  LEDGER DID IT FOR ITS WHOLE LIFE.** `rev-list --count A..B` without `--not <base>`, printed
  beside a numstat that has it. #1046: **55 reported, 2 real**. The tell is structural and
  cheap to check — **two git invocations, one flag apart, whose outputs get printed in one
  sentence.** Ask of any "N commits produced M lines": were N and M selected the same way?
- 🔴 **THE WRONG NUMBER WAS THE ONE THAT MADE THE FINDING LOOK BORING.** 55-commits-1105-lines
  reads as drift; 2-commits-one-of-them-a-conflict-resolution is a finding. A measurement
  error that flatters the null is the one nobody chases.
- 🔴 **I NEARLY CLASSIFIED FROM THE WRONG COMMIT SET.** My first `git log` of #1046's tail
  omitted `--not origin/main` and showed 12+ of `main`'s own squash commits — which looked
  exactly like "the PR kept developing", i.e. it CONFIRMED the hypothesis I held. The
  confirming evidence was an artifact of dropping one flag. **List the population the
  measurement measures, not the range it names.**
- 🔴 **A TEST KEYED ON AN ABSOLUTE CALL ORDINAL BREAKS SILENTLY AND MISLEADINGLY.**
  `test_a_failed_cumulative_measurement_does_not_print_a_false_cause` failed the SECOND
  `rev-list` to target the cumulative measurement; adding one helper call re-pointed that at
  the PER-ROUND one, so the test failed for a reason unrelated to its subject and the failure
  message pointed at the wrong thing. Re-keyed to count only calls WITHOUT `--not` — one per
  `measure_ledger`. **When two callers share a helper, key on an argument, not a count.**
- **The assert-it-applied driver earned its place twice in one session.** Two mutants'
  target strings were moved by my own edits; the battery printed `MUTATION DID NOT APPLY —
  result meaningless` instead of a clean pass for mutants that never executed.
- ⚠ **My own fixture expectation was wrong and the code was right.** The first version of the
  new guard asserted the churn population was 1; it is 2, because a clean merge commit is not
  reachable from the base either and so belongs to the population while contributing zero
  lines. Corrected in the test, with the reason recorded there — that is the same shape as
  #1046, where the merge contributed 1,039 lines rather than none.
- ⚠ **CORRECTION to this doc's own earlier claim:** it said the rank-8 scratchpad reports
  "are gone — re-run the driver rather than hunting for them". They were still there. That
  was a prediction written as an observation; the reports survived and were what the
  classification read.

## How to verify
```bash
# the instrument's own guards
nix develop ~/workspace/devrc -c python3 -m pytest \
  scripts/tests/test_ladder_range_coverage.py -q          # expect 20 passed
nix develop ~/workspace/devrc -c bash \
  scripts/tests/mutants-ladder-range-coverage.sh          # expect 20 row(s), all as expected

# reproduce the original symptom: the gap the review named, sized
nix develop ~/workspace/devrc -c python3 scripts/ladder-range-coverage.py 1233 \
  --repo innovation-upstream/devrc --repo-dir ~/workspace/devrc
# expect: 🔴 GAP round 2 `to` → round 4 `from`: 1b5d2e43..eb947328 — 524 line(s)

# the negative control, on real data — a dead detector prints 0 for these too
nix develop ~/workspace/devrc -c python3 scripts/ladder-range-coverage.py 958 1219 \
  --repo innovation-upstream/devrc --repo-dir ~/workspace/devrc
# expect: every adjacency TIGHT, UNCOVERED 0, positive control NON-zero
```
🔴 Read the `positive control:` line on every ladder. `UNCOVERED: 0` with a zero control is a
REFUSAL (exit 4), not a clean ladder — the PR's commits are simply not in that checkout.
