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
- **Branch:** `feat/ladder-carrier-enumerator` at `405c4b45`, pushed, **no PR yet**.
  Worktree `/home/zach/workspace/devrc-ladder-find`, clean, branched off `058c90c2`.
- ✅ **Rank 10 DONE and MERGED — `#1519` → squash `766c1295`**, verified by CONTENT on
  `origin/main` (`scripts/ladder-range-coverage.py`, `scripts/tests/mutants-ladder-range-coverage.sh`,
  and `measure_range_churn` all present), never by ancestry.
  - `scripts/ladder-range-coverage.py` classifies every adjacency in a ladder's chain of
    block ranges — TIGHT / GAP / OVERLAP / UNRELATED, plus the tail — and reports the
    uncovered churn for the window `[first block's from, head]`.
  - `measure_range_churn` was EXTRACTED from `audit-dispatch.py::measure_ledger` and is
    shared, so the report cannot drift from the command the skill tells an auditor to run.
  - Open item **5** of `claudedocs/audit-ladder-review-2026-09-04.md` is marked CLOSED in
    that doc, with its residual stated.
- ✅ **devrc measured (the review's own 20 ladders):** INTERIOR **655** lines in 2 ladders,
  TAIL **3,727** in 11. Commands and per-ladder rows in the PR body of `#1519`.
- ✅ **Rank 8 DONE — all six named repos churn-measured or UNMEASURABLE with a reason**, its
  closing condition met. Results written into the `CANNOT-SEE` section of
  `claudedocs/audit-ladder-review-2026-09-04.md` (uncommitted at time of writing; see ranked
  item 1). **129 carriers outside devrc against 70 inside**; INTERIOR **88** total across 126
  measured external ladders, TAIL 10,052. The per-repo table is in that doc, not repeated here.
  ⚠ The raw per-repo reports were scratchpad files and are gone — re-run the commands in the
  investigation block below rather than hunting for them.
- **Deploy/verify status:** `766c1295` is merged and **NOT deployed** — no `ship.sh` run this
  session. It adds a script and a test; nothing a consumer runs changed, and
  `ladder-range-coverage.py` is invoked by path, not from `~/.claude`. `405c4b45` is pushed
  only: not merged, not deployed.
- ⚠ **No `clawgate-task:` field recorded.** `clawgate_handoff.sh resolve` exited **5**
  (nothing resolved). An unknown session id answers 200 with an empty array, so that cannot
  distinguish "this session touched no task" from "the id is wrong" — it is NOT a clean bill
  of health, and no task was created to fill the blank.

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

## Next steps (ranked)
1. **Land `feat/ladder-carrier-enumerator`** — `405c4b45` plus the uncommitted rank-8 table in
   `claudedocs/audit-ladder-review-2026-09-04.md`, in worktree
   `/home/zach/workspace/devrc-ladder-find`. **An unmerged pushed branch is invisible to
   `ship.sh` and to the next `/resume`, and the rank-8 numbers exist ONLY in that working
   tree** — `claude/RULES.md` calls that unsaved work one routine `checkout` from deletion.
   Claim `audit-pr-ladder-8` is HELD by this session; release it when this lands.
   forcing: regression — the measurement is uncommitted and its scratch reports are already gone
2. **Classify the three largest TAIL gaps by reading their commits** — post-final-block fixes
   vs ordinary development. Until this is done, the 3,727-line devrc tail and the
   7,314-line homelab-talos one must NOT be quoted as unaudited ladder work, and the
   range-coverage defect's real size is unknown (it may be as small as 88 lines externally).
   forcing: none
3. **Fix or rescope `_KILL_MENTION_LEDGER`** so a new handoff doc cannot red the gate. See the
   investigation below for the two candidate fixes.
   forcing: gate — `tekton/devrc-pytests` is red on `main` itself today, for every PR
4. **Rank 9 — mine the stop-rationale prose across every carrier.** Now cheap: `--find-carriers
   --list-only` produces the population that used to be hand-built (199 carriers across the
   seven repos). Survives durably as open item **4** of
   `claudedocs/audit-ladder-review-2026-09-04.md`.
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
