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
- **closing-condition:** `check` — every open item of
  `claudedocs/audit-ladder-review-2026-09-04.md` that this arc owns — **4** (stop-rationale),
  **5** (the range-coverage hole) and **6** (the unledgered rounds) — carries a CLOSED marker
  with its residuals, or a written operator decision not to read it. **ALL THREE ARE CLOSED as
  of 2026-09-14, so this condition is MET.** *Checked by:* opening that doc's `## Open items`
  and finding no uncrossed item whose text names range-coverage, stop-rationale or the gap
  census. 🔴 **FROZEN — it does NOT extend to items 1, 2, 7 or 8 there**, which are open and
  are not this arc's; 7 and 8 are the stop-rationale run's own residuals. The instruments are
  shipped and guarded; this arc ended at the ITEMS, not at more measurement.
  ⚠ **Reformatted 2026-09-14 from a bare `closing-condition:` line**, which rule (m)'s parser
  did not read — the write gate reported this doc as declaring none and GRANDFATHERED it. A
  closing condition a tool cannot see is one nobody is held to, and this arc closes ON it.

## State now
- ✅ **THE ARC IS CLOSED BY ITS OWN STATED CLOSING CONDITION, 2026-09-14.** That condition
  names items **4**, **5** and **6** of `claudedocs/audit-ladder-review-2026-09-04.md`; 4 and
  5 were already CLOSED, and **6 is now CLOSED** — the interior unledgered rounds were READ,
  which is the first of the two closures that item offered. Re-check by opening that doc's
  `## Open items` and confirming no uncrossed item names range-coverage, stop-rationale or the
  gap census. 🔴 **Items 1, 2, 7 and 8 there are still open and are NOT this arc's** — 7 and 8
  are the stop-rationale run's own residuals. Do not read a closed arc as a clean document.
- ✅ **Item 6's finding, written where its closing condition lives — not only here.** Over the
  full devrc population (**62 carriers found, 60 measured**) the INTERIOR class is exactly
  **one** commit: `#1326`, round 7 `to` → round 8 `from`, `404ec208..b9848f9d`, **191 lines**,
  the operator's design decisions 9–16 recorded into a proposal. **Never re-audited under any
  later round's block** — round 8 anchors past it and rounds 8→9, 9→head are TIGHT. Totals:
  interior 191 / tail 6,727; gap census INTERIOR `round-ref 0 · merge 0 · unclassified 1`.
- 🔴 **THIS DOC TOLD THREE SESSIONS THE WORK WAS DONE WHILE THE ITEM WAS STILL OPEN.** The
  measurement above was taken on 2026-09-14 and written into THIS doc; the item it closes
  lives in the review doc, and nothing wrote it there for hours. This doc's own rank 3 said in
  terms *"What REMAINS to close item 6: write that finding onto the item"* — correct, and
  still unactioned through two further handoff updates. **A finding recorded in the doc that
  MOTIVATED the work is not recorded where the work CLOSES.** Same shape as the false kickoff
  in the gotchas below, one level up.
- **All seven PRs of this arc remain MERGED and verified by content** on `origin/main`:
  `#1519`→`766c1295`, `#1528`→`b42ac7c3`, `#1552`→`b1abf6b1`, `#1564`→`61f41adf`,
  `#1576`→`3409d325`, `#1649`→`46ebb689`, `#1643`→`e6f05e04`. Plus `#1676` (this doc's own
  truth repair) → merged 2026-09-14T18:11Z.
- ✅ **The original kickoff's ranks 14 and 16 stay CLOSED** — rank 14: `#1287` closed, its
  port `#1505` MERGED 2026-09-11T20:20Z. Rank 16: `#1431` was reopened and then closed
  carrying a written dismissal naming the reader, which is its stated closing condition, met.
  Carried because no other line records them.
- ⚠ **No `clawgate-task:`** — `resolve` exits 5; the positive control answers for another
  session, so the board is reachable, but a wrong id also answers 200 with an empty array.
  Not a clean bill of health.
- ⚠ **The shared clone `$DEVRC`'s branch is VOLATILE** — measured on `main`, then
  `fix/tmux-osc8-hyperlinks`, then `feat/the-algorithm-skill`, then `main` again, all within
  this arc's life. `git -C $DEVRC branch --show-current` immediately before any write.

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
- 🔴 **THIS HYPOTHESIS IS FALSIFIED — see the RESOLVED entry two sections down, and do not
  read it from here without that.** Left in place because the *external* tail is still
  unclassified; but *"most of the tail is ordinary post-ladder development"* was tested on 17
  commits and **1 was a feature**. The heading above it is also stale: it says *"nobody knows
  what it IS"*, which is true only of the external tail now.
- **Leading hypothesis (FALSIFIED for devrc; UNTESTED externally):** most of the tail is
  ordinary post-ladder development, not missed
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

### RESOLVED — the bring-in caveat explained a CLEAN MERGE, and rank 9 is measured
- as-of: 2026-09-14 · ✅ **BOTH MERGED** — `#1649`→`46ebb689` (04:15Z) and `#1643`→`e6f05e04`
  (04:21Z). ⚠ This line read *"in flight … NOT merged"* for ten hours after they landed, and
  that staleness is what produced a false kickoff — see the first gotcha.
- **Ranks 1 and 2 of the list below are both done in that PR.** Rank 1: the caveat is now
  DERIVED from whether every commit contributing the zero-line gap is a MERGE, and prints **no
  explanation at all** for one containing a non-merge commit. The bring-in reading was not
  re-worded but RETIRED from that gate: it selects on `churn_commits`, and a bring-in commit is
  reachable from the base, so `--not <base>` excludes it from that count as well as from the
  lines — the class could not reach the caveat at all. Rank 2: `scripts/ladder-stop-rationale.py`
  + 30 guards classify the terminal round of **201 carriers in 7 repos**; **121 of 191
  terminated ladders (63.4%) state no reason**, and **28 of the 33 that declared a criterion
  (84.8%) wrote the rationale in the same summary** (#1157's check). Numbers, frozen taxonomy
  and residuals are in `claudedocs/audit-ladder-review-2026-09-04.md` open item **4, CLOSED** —
  read them there, not here.
- ⚠ **The population is NOT this review's "42 carriers"** — that was its own merged-devrc
  window. 201 across all states, newest 400 PRs per repo, and three repos hit that limit.

### The bring-in caveat is SHIPPED ON MAIN and gives the wrong reason
- as-of: 2026-09-12 · **superseded by the entry above**
- **Symptom + exact repro:** `scripts/ladder-range-coverage.py:667` gates the "many commits, 0
  lines" caveat on `a.commits`, which `b1abf6b1` changed to mean `churn_commits`. Reproduce:
  ```bash
  nix develop ~/workspace/devrc -c python3 scripts/ladder-range-coverage.py 1064 1274 1046 \
    --repo innovation-upstream/devrc --repo-dir ~/workspace/devrc --no-fetch
  ```
- **Observed (with values):** `#1064` prints `1 commit(s), 0 line(s)` — it printed **125
  commits** before the `churn_commits` change. The caveat still fires (`2 gap(s) in THIS run
  look like that`) and still reads *"A GAP of **many** commits and 0 lines is `--not <base>`
  working: those commits are an upstream bring-in already in the base"*.
- **Ruled out:** *"the caveat is now dead code"* — it is not; it fired on 2 of 3 ladders in the
  run above. via: measurement
- **Ruled out:** *"the counts are wrong"* — they are not. `churn_commits` is the correct number
  to print beside a `--not <base>` line count; that was the whole point of `b1abf6b1`.
  via: measurement
- **Leading hypothesis:** the bring-in population is now excluded from the count BEFORE the
  caveat sees it, so what remains to trigger it is a **clean merge commit contributing no
  diff** — a different mechanism than the text describes. The caveat explains a merge as a
  bring-in.
- **Next probe:** decide whether to re-word it for the merge case or gate it on the raw
  `commits` instead (both counts are on `RangeChurn`). Then check the twin claim in
  `claudedocs/audit-ladder-review-2026-09-04.md`, which carries the same "many commits and 0
  lines" wording.

### Round 2 of #1576's ladder was never run, by operator decision
- as-of: 2026-09-12
- **Symptom + exact repro:** round 1 returned **5 🟡 / 2 🟢 / no 🔴**; the findings-keyed stop
  rule says a round that found things is followed by another. The operator said "merge it".
- **Observed (with values):** the fixes for round 1 landed as `3f5d0694` and are in
  `3409d325`. They are substantially PROSE — the narration-class paragraph, the vacuous-pin
  docstring, three doc corrections.
- **Ruled out:** *"the ladder converged"* — it did not; it was stopped by decision, and the PR
  comment records `ran: 2 · changed the outcome: 2`. via: doc
- **Leading hypothesis:** this repo's measured pattern is that the fix round's own prose is the
  likeliest next finding, and round 1 itself demonstrated that twice (a vacuous pin half, a
  TOTAL row that did not sum). The un-audited delta is `59002824..3f5d0694`.
- **Next probe:** a BLIND delta audit of that range if the operator wants the ladder closed on
  evidence rather than on decision. `scripts/audit-dispatch.py 1576 --round 2` will REFUSE —
  no `audit-claims` block was ever posted (round 0 correctly emits none, and round 1 was a
  first-full audit) — so dispatch it by hand against that range.

## Next steps (ranked)
🔴 **NUMBERING IS STABLE AND CLOSED ITEMS STAY IN PLACE, STRUCK.** The rank is half a
`claim-work` slug's identity, so renumbering silently re-points every live claim — this arc's
worst gotcha, recorded below, was another doc doing exactly that. Do not compact this list.

1. ~~**Fix the shipped bring-in caveat.**~~ **CLOSED 2026-09-14** — `#1649`→`46ebb689`. The
   caveat is now DERIVED from whether every commit contributing the gap is a merge, and it
   explicitly retracts the bring-in reading in its own output. Re-verified behaviourally this
   session, not just by diff: the `1064 1274 1046` run prints *"2 in THIS run are CLEAN
   MERGES"* and *"🔴 NOT an upstream bring-in."*
   forcing: regression — a caveat shipped on `main` explains a clean merge as an upstream bring-in
2. ~~**Rank 9 — mine the stop-rationale prose across every carrier, and publish the rate.**~~
   **CLOSED 2026-09-14** — `#1643`→`e6f05e04`. `scripts/ladder-stop-rationale.py` + 30 guards
   classify the terminal round of **201 carriers in 7 repos**: **121 of 191 terminated ladders
   (63.4%) state no reason at all**, and **28 of the 33 that declared a criterion (84.8%)**
   wrote the rationale in the same summary (`#1157`'s check). Numbers, frozen taxonomy and
   residuals live in `claudedocs/audit-ladder-review-2026-09-04.md` open item **4, CLOSED**.
   forcing: user — named in the kickoff as one of five open items
3. **Close open item 6 — the INTERIOR half is MEASURED and the answer is ONE commit.** Re-ran
   the census over the full devrc carrier population this session (**62 carriers found, 60
   measured**, `--limit 400`): **INTERIOR round-ref 0 · merge 0 · unclassified 1, of 1 interior
   commit total**; TAIL round-ref 7 · merge 18 · unclassified 33, of 58. Totals interior **191**
   / tail **6,727**. The one interior gap is **`#1326`, round 7 `to` → round 8 `from`,
   `404ec208..b9848f9d`, 191 lines** — `docs(proposal): eight of nine open questions answered`,
   the operator's design decisions 9–16 recorded into the proposal. Round 8's block anchored
   PAST it and rounds 8–9 are TIGHT, so **that delta is inside no round's range and was never
   re-audited.** ⚠ **This is a DIFFERENT, LARGER population than the review's** (60 ladders vs
   20, where the census read 1 interior round-ref of 40) — it is a fresh measurement, NOT the
   review's item resolving. What REMAINS to close item 6: write that finding onto the item in
   `claudedocs/audit-ladder-review-2026-09-04.md`. 🔴 Re-running the census again does NOT
   close it — producing the list is not reading it.
   forcing: none
4. **Round 2 of #1576's ladder**, blind, over `59002824..3f5d0694` — only if the ladder should
   close on evidence rather than on the merge decision. See the investigation above.
   forcing: none
5. **Classify more of the 63 unread adjacencies**, if a rate is ever wanted. Neither existing
   sample was sized to estimate one and no rate is claimed anywhere.
   forcing: none

## Gotchas / decisions / dead-ends
- 🔴 **THIS DOC CONTRADICTED ITSELF FOR TEN HOURS AND THE NEXT KICKOFF DREW THE STALE HALF —
  A `/resume` WAS SENT TO REDO WORK ALREADY ON `main`.** `#1649` merged 04:15Z and `#1643`
  04:21Z on 2026-09-14. The doc's **investigation section** had said since 2026-09-13 that
  both were done ("*Ranks 1 and 2 of the list below are both done in that PR*"), but its
  **`State now`** still read *"9 WAS NEVER STARTED"* and its **ranked list** still carried
  both as open items 1 and 2. The kickoff was written from the two stale sections and
  instructed the session to *"fix the stale bring-in caveat first — it is shipped on main and
  gives the wrong reason"*, which had been false for ten hours. **Nothing caught it; the
  session caught it by re-verifying against `origin/main` before starting.** 🔴 **The lesson
  is not "update the doc" — it is that a handoff doc has THREE places that assert status
  (`State now`, the investigation entries, the ranked list) and a reader entering at any one
  of them gets a different answer. An investigation entry marked RESOLVED is not a status
  update until the other two move with it.** The cheap check before writing any kickoff:
  `gh pr view <n> --json mergedAt` on every PR the doc calls in-flight.
- ⚠ **`--find-carriers` on devrc now reports 62 carriers and STILL HITS the 400-PR limit** —
  its own output says so. Every devrc total derived from it is a FLOOR, low by an unknown
  amount, and that is review open item **8**.
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

- 🔴 **I MARKED A FINDING "FIXED" IN A COMMIT MESSAGE WITHOUT FIXING IT, AND ONLY THE
  OPERATOR'S QUESTION SURFACED IT.** Round 1's 🟡 #5 was "the PR title and body headline the
  number the diff forbids quoting". I retitled the PR, posted a correction comment, and wrote
  **"All seven are fixed"** in `3f5d0694` — while the BODY still carried all four retracted
  claims (`supersedes sampling`, `40 … where no round looked`, `Why you can believe the 40`,
  `found it independently`). Verified by grep, then rewritten. **The audit did not miss it; I
  recorded it as done without doing it.** A fix-claim in a commit message is a claim like any
  other, and the cheap check is to grep the artefact for the retracted string.
- 🔴 **`handoff_doc.py --repo $DEVRC` WOULD HAVE COMMITTED THIS DOC TO ANOTHER SESSION'S
  BRANCH.** Measured 2026-09-12: the shared clone was on `fix/tmux-osc8-hyperlinks`, not
  `main`. The remedy the `handoff-index` cairn entry names is the one used here — run it with
  `--repo <a worktree on main>`, which fixes the write target and the read base in one move.
  `git branch --show-current` on the clone before every handoff write, not just before commits.
- 🔴 **A RED CI CHECK ON A BRANCH ~30 COMMITS BEHIND IS A CLAIM ABOUT THE WRONG TREE.**
  `#1576` showed `tekton/devrc-pytests` red on
  `test_every_site_writing_its_OWN_runner_bound_is_in_the_ledger`. Measured: it **passes** on
  current `main` AND on the MERGED tree (built with `git worktree add --detach origin/main` +
  `git merge origin/<branch>`), where the 27-test suite and all 30 battery rows were also
  green. The ledger entry it wanted had landed on `main` after the branch point. **Gate on the
  merged tree — it is what distinguished stale CI from a real conflict, and it took one
  worktree.**
- ⚠ **The scratchpad reports survived, and this doc previously predicted they would not.** An
  earlier revision said the rank-8 `r8-*.txt` reports were "gone — re-run the driver". They
  were still there and are what the tail classification read. A prediction written as an
  observation.
- ⚠ **252 registered worktrees in the shared clone** (observed 2026-09-12), none of them this
  arc's — every one removed as it finished. Other sessions' agent checkouts, nothing prunes
  them, and not this effort's to clean. Recorded because the number is now large enough that
  `git worktree list` is no longer a usable orientation command.

- 🔴 **A CLOSING CONDITION POINTING AT ANOTHER DOCUMENT IS ONLY AS GOOD AS THE WRITE INTO THAT
  DOCUMENT, AND THAT WRITE IS THE STEP THAT GETS SKIPPED.** This arc's condition named three
  items in `audit-ladder-review-2026-09-04.md`. The measurement closing item 6 was taken, was
  correct, and was written into the HANDOFF doc — where it read like completion — while the
  review doc's item 6 stayed uncrossed and its `*Closes when*` clause unmet. Two subsequent
  handoff updates passed over it. **The tell was available the whole time and was written down
  by me:** rank 3 literally said what remained. 🔴 **So: when a closing condition names a
  FOREIGN file, the work is not done until that file changes — and a handoff entry describing
  the finding is evidence of MEASUREMENT, never of CLOSURE.** Check by grepping the foreign
  file for the finding's own identifier (here `#1326` / `404ec208`), not by re-reading the
  handoff.
- ⚠ **The item closed on a DIFFERENT population than it was written against, and that is
  stated rather than smoothed over.** The item said "1 of the 40 on devrc" from a 20-ladder
  census; the closure re-measured over 60 ladders and also found 1. Those are not the same
  claim, and reporting the second as though it confirmed the first would be the
  count-and-measurement-from-different-populations defect this very arc exists to catch.

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
