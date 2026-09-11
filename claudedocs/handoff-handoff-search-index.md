# Handoff: handoff-search-index — 2026-09-03

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
Give the handoff corpus a queryable index, because git gave it redundancy but no retrieval.
424+ docs / 8.6 MB across four repos were readable only by knowing the slug.

## State now
🔴 **THE EFFORT IS OVER AND THE ANSWER IS NEGATIVE: the `/resume` step has been RETIRED on the
re-measurement it was waiting for.** The machinery works, adoption is near-total, the self-hit
defect is fully fixed — and yield is **0 of 16**. The falsifier rank 1 wrote down in advance
fired, and it was honoured rather than argued with.

🔴 **THE THREE CLAIMS, EACH MEASURED SEPARATELY — every fix did what it promised, and the thing
they were all in service of never arrived:**
- **Adoption: 16 of 16** post-`#1399` resuming sessions ran the command, all with `--exclude-slug`
  (2 of 11 → 20 of 22 → 16 of 16 across the three fixes).
- **Self-hits: 0 of 48** hit slots were the session's own handoff, against **23 of 60 (38%)**
  before; own doc was the **#1 hit 0 of 16** times, against **13 of 20**. `--exclude-slug` works
  exactly as designed.
- **Yield: 0 of 16.** No session opened, mentioned or acted on a hit. Every one went straight from
  the tool result to `claim-work` / `gh pr view`. Pre-fix this was 1 of 20.

🔴 **THE CAUSE IS RETRIEVAL RECALL, NOT AN EMPTY CORPUS — and that correction matters, because the
falsifier's own stated reason was wrong.** Rank 1 predicted that low yield would mean *"the corpus
has little to offer a resuming session"*. It has plenty; the ranker cannot reach it. **44 of the
48 hits were `[gotcha#0]`** — the same first section of a Gotchas block — with **29 distinct docs
filling 48 slots**, two of them appearing **5× each** across unrelated repos, and ranks clustered
0.97–1.75 (no separation between a good match and a bad one). The decisive case: session
`d775cf17` asked about a store-api fsync stall in the Tekton tier; the corpus **holds that exact
doc** (`devrc/gate-flake-store-api`); `handoff_search` ranked it **11th** — unreachable at the
prescribed `--limit 3` — while `cairn search 'fsync'` had handed the session the right doc
thirteen records earlier. **The surviving step-4 surface is the one that delivered.**

⚠ **SO THE HONEST SUMMARY IS NOT "THE CORPUS IS WORTHLESS".** It is: a section-grained BM25-ish
ranker over 5,500 sections returns generic long sections for topic-shaped queries, and three slots
is not enough budget to survive that. Retiring the step is the response rank 1 pre-committed to
(*"stop paying for the step rather than tune the ranker again"*); it is **not** evidence that a
better retrieval over this corpus would fail. Nobody has tested that, and nobody should start
without a new reason.

- **The tool is NOT deleted.** `scripts/lib/handoff_search.py`, the index, the timer and
  `--exclude-slug` all remain and are supported for ad-hoc use. What was retired is paying ~4 KB
  of every `/resume` for it.

- **Merged:** `devrc#1209` (`45930d644`) index · `#1244` (`1b769b64b`) cairn I/O-stall classifier ·
  `#1264` (`baa95854`) this doc · `#1267` (`d86b4e45`) incomplete-read delete authority ·
  `#1295` (`3e7d79a4`) the `/resume` consumer · `#1307` (`bb6e46ee`) ARM the timer ·
  `#1329` (`4ffb0cc6`) the adoption measurement · `#1332` (`8e9428ef`) the step-4 promotion ·
  **`#1399` (`4a67ea73`) the yield answer + `--exclude-slug`** · **`#1476` (`723ed7c1`) the
  close-out sweep**. Plus `homelab-infra` `d2c9c49a` — the rescued untracked handoff doc.
- 🔴 **`#1399` DEPLOYED AND VERIFIED ON BOTH HOSTS 2026-09-09.** Merged, deployed and live are
  three claims and all three were checked: content on `origin/main` (a squash makes the branch
  head a permanent non-ancestor, so ancestry would read "not merged" forever, plus a negative
  control that no mutant string leaked in); both hosts at `4a67ea73` resolving the IDENTICAL
  store path `…-devrc-claude-skills/resume/SKILL.md`; the deployed copy carrying the flag; and
  the deployed fence parsing (`bash -n` rc 0) on both.
  ⚠ **`ship.sh` reached the laptop only under a manual `REMOTE_SSH=zach@10.42.0.100`** — its
  default LAN address timed out. That split the run in two, so `ship.sh` printed
  **`cross-host agreement NOT COMPARED` both times**; the agreement above was checked BY HAND,
  not by the tool. Filed as rank 2 (was rank 3 before the old rank 1 closed).
- **Live end-to-end on the deployed path:** `in_scope_docs=402` of 403, `excluded=handoff-search-index`,
  and all three hit slots holding documents from a different repo — i.e. the slots the session's
  own handoff used to occupy are now spent on documents it has not read.
- 🔴 **FOUR AUDIT ROUNDS, 23 FINDINGS, ZERO DEPLOY-BLOCKING — and the shape is the lesson.**
  Round 1 found the flag was deletable to **INERT** with 304/304 green (nothing pinned that
  `main()` forwards `exclude`); round 2 found a backwards remedy verb and a cross-backend
  divergence *inside the divergence guard*; rounds 3 and 4 found **no behavioural defect at
  all**. **Three of the four rounds caught coverage CLAIMED and not held** — a seam nothing
  pinned, a "consolidation" that was dead-code-able, a sweep that missed a fifth site, four
  unscoped test counts. Not one was a logic bug; the mechanism has been correct since round 1.
  The ladder was stopped on the **stated prose-payload criterion**, written onto the PR so that
  ending-on-criterion is distinguishable from converging.
- **The measurements, with their as-of stamps — carried forward because a figure without one
  cannot be re-run against.** Adoption **2026-09-07T02:25Z: 4 of 4** post-`#1332` (control
  `cairn recall` 4/4) — the first, small-n reading. Adoption **2026-09-08T17:00Z: 20 of 22 (91%),
  control 21/22**, measuring session excluded from both halves by session id. Pre-fix on the same
  host, `#1295`..`#1332`: **11 runs, 2 queried (18%), control 10**. Yield **2026-09-08: 1 of 20**.
  ⚠ The corpus grows, so a later re-run reports LARGER numbers rather than contradicting these.
- **`#1332`'s own deploy (2026-09-06, both hosts at `8e9428ef`) is SUPERSEDED by `#1399`'s above**
  and was verified the same way — `readlink -f` on both hosts resolving one store path. Kept as a
  pointer only; the method is what carries forward, not the sha.
- **Claim `handoff-search-index-1`: RELEASED** (twice — after `#1332` and again after `#1399`).
  Merging does not release a claim; both were released by hand.
- **Housekeeping done:** the `fix/handoff-search-exclude-own-doc` worktree removed and the branch
  deleted local+remote; base clone re-synced; the cairn `devrc/handoff-index` entry's `OPEN:`
  bullet closed (entry now shows no badges, 26/26 parse).
- ⚠ **NOT verified, and no further work changes it:** anything a real Postgres server does with
  `slug <> ALL(%s)` — no test in this repo reaches a database, so those are structural pins over
  code that was READ — and the transcript-derived `23/60`, `13/20`, `1/20` figures, which are
  judgement calls made reading sessions. Both audits said the same.
- ⚠ **The laptop contributed 0 `/resume` runs throughout**, measured over ssh rather than
  assumed, so every adoption and yield number in this doc is **workbench-only**.

## Open investigations — live diagnosis state

### SUPERSEDED — a repo whose every doc is unreadable has its rows deleted, rc 0, no PARTIAL notice
🔴 **CLOSED by `#1267`. Read "RESOLVED — the unreadable-docs delete path" below instead; this
block is kept only for its values.** It sat here in the present tense, under a heading that says
`live diagnosis state`, while its own resolution sat 55 lines further down — so a `/resume`
following step 3 would have re-derived work that shipped weeks ago. That is precisely the hazard
this doc's own gotcha names ("THE STATUS HEADER IS THE PART THAT GOES STALE AND THE PART NOBODY
SWEEPS"), left standing in the doc that records it. Retired 2026-09-09.

- **Symptom + exact repro:** two repos, one healthy, one whose doc blob is deleted from
  `.git/objects`; then
  `handoff_index.py --repo <good> --repo <bad> --rebuild --write`.
- **Observed (with values):** bad repo derives `unmeasured=None docs=0
  unreadable=('claudedocs/handoff-b.md',)`; `partial_scope_warnings() == ()`;
  `rebuild_refusal() is None`; run exits **rc 0** with `DELETE params =
  [['badrepo','goodrepo']]` and `wrote N section row(s)`. The success line does **not** say
  PARTIAL. The only signal is one `⚠ UNREADABLE` stderr line per doc.
- **Ruled out:** that the new `RepoDerivation.unreadable` field already covers it — it is
  read only on the `handoff_search` rc-7 path, never by `rebuild_delete_labels`.
  via: code
- **Ruled out:** that this was introduced by the P1 work — the classification predates it and
  was filed rather than patched, deliberately. via: measurement
- **Leading hypothesis:** a repo whose docs could not be read is not a repo that MEASURED;
  classifying it UNMEASURED makes the existing partial/refusal machinery cover it.
- **Next probe:** `git grep -n "unmeasured is None" scripts/lib/handoff_index.py` — the two
  sites (`rebuild_delete_labels`, `partial_scope_warnings`) plus the global zero-rows refusal
  in `rebuild_refusal` are the whole surface.

### SUPERSEDED — the same hazard in FOUR spellings: is the shape the open question?
🔴 **ANSWERED by `#1267`. Read "RESOLVED — the four-spelling shape" immediately below**; its
`Next probe` ("ask whether any single invariant would have prevented all four") is the question
that block answers. Kept for the enumeration of the four spellings. Retired 2026-09-09.

- **Symptom + exact repro:** each round of review found one more way for a rebuild to delete
  rows it should not.
- **Observed (with values):** (1) unpredicated `TRUNCATE` — emptied the table when every repo
  failed to resolve, exit 0. (2) delete scope computed over *stored* labels while the warning
  reasoned over *configured* labels — deleted `civitai` under a renamed checkout. (3) the
  refusal checked *unreadable* when the risk was *unconfigured* — with `$DATAPACKET`/`$CIVITAI`
  unset, `--rebuild --prune --write` bound `DELETE ['civitai','datapacket-talos','devrc',
  'homelab-talos']` at rc 0. (4) the unreadable-docs case above.
- **Ruled out:** that these are independent bugs — each was created or left half-closed by the
  previous round's fix. via: measurement
- **Leading hypothesis:** the delete scope is derived from a *config* view while the table
  holds a *stored* view, and every fix so far has patched one crossing of that boundary.
- **Next probe:** ask whether any single invariant ("never delete a label this run did not
  itself measure and re-insert") would have prevented all four.

### RESOLVED — the four-spelling shape: one invariant does close it, at one level
- **Answer:** yes for `derive_repo`'s outputs, no for the level above. The shape was named in
  `#1267`: *DELETE authority was granted by a NEGATIVE predicate — the absence of whichever
  failure had already been seen — instead of a positive demonstration that the run holds a
  complete replacement.* A blacklist re-opens the hole for every unmet failure, which is why
  four rounds never converged. The fix inverts the default in one function.
- **Observed (with values):** a round-3 blind audit enumerated every way a run can fail to hold a
  complete replacement — `unmeasured`, `unreadable`, disk incompleteness, zero-row docs, the
  `errors="replace"` decode — and could construct no fifth spelling through that path.
- **Ruled out:** that a fifth spelling exists in `derive_repo`'s own outputs — the enumeration
  above is exhaustive over its return values. via: measurement
- **Ruled out:** that the label-computation fix widened the residual — collision groups measured
  byte-identical across 15 input shapes before and after. via: measurement
- **Residual, still open:** authority is demonstrated per DERIVATION and exercised per LABEL, so
  two repos sharing a basename let the healthy twin's completeness authorise deleting the broken
  twin's rows. Pre-existing, measured identical at base, NOT a regression. Pinned by
  `test_through_main_the_residual_is_recorded_and_the_report_is_true`, a tripwire that FAILS the
  day it is fixed.

### RESOLVED — the unreadable-docs delete path
- **Answer:** fixed in `#1267` after five audit/fix rounds. A repo whose docs could not be read no
  longer obtains delete authority.
- **Ruled out:** that setting `unmeasured` was the right fix — it would conflate "nothing came
  back" with "not everything came back" (the conflation this module was burned by three times)
  and would hide readable docs from `--offline` search over one bad blob. via: code

### RESOLVED — does `/resume` actually query the index? 1 of 14 runs, and that one was not the step firing
- **Answer:** no. The index is live, correct and unused. This was the effort's own rank-1 test —
  *"the only real test of whether the effort was worth building; everything else is machinery"* —
  and it fails at the call site, not in the machinery.
- **Symptom + exact repro:** count `/resume` runs since `#1295` merged (2026-09-04T17:11:50Z)
  against those that actually invoked the tool, on both hosts. 🔴 **A MENTION IS NOT AN
  INVOCATION** — the deployed `SKILL.md` body *contains* the command string, so
  `grep -l handoff_search` over transcripts matched **24** files and is entirely false. Only a
  Bash `tool_use` whose `command` contains `handoff_search.py --` counts. See "How to verify".
- **Observed (with values):**
  - **14 `/resume` runs** (ran `resume-state.sh`): **8** workbench, **6** laptop.
  - **1 invocation**, workbench session `e3dc23e9`. Laptop: 6 runs, **0** invocations — its one
    `grep`-level match has 0 Bash invocations, verified.
  - 🔴 **That one hit was driven by the staleness alarm, not by the step.** The `SKILL` block told
    that session its loaded copy was 1 commit behind; it then read the step off `origin/main` and
    stated it was following that text rather than the copy it had loaded. Its result — 3 hits,
    best `rank=1.1667`, generic gotcha sections — it judged irrelevant, finding nothing any prior
    session had ruled out. **Yield to date: 0.**
  - ⚠ **The denominators, stated so they reconcile** (an audit found 8 − 1 = 7, not 6): the
    workbench **8** = **1** that fired + **6** analysed as non-firing + **1** that was the
    measuring session itself, excluded as the instrument. All figures are as of
    **2026-09-06T04:00Z**; the corpus grows, so a later re-run reports larger numbers rather
    than contradicting these — a re-derivation ~1 h later measured 10/2 on the same needle.
  - **The 6 workbench non-firing runs all met the trigger.** All 6 ran `claim-work` (1–20×) and 5
    made edits (2–52 `Edit`/`Write` calls) — actively working ranked items, not reporting and
    waiting. (The six docs are client-repo topics and are deliberately not named here; this repo
    is public. Re-derive them from the transcripts with the "How to verify" query.)
  - **5 of those 6 handoffs carry an `## Open investigations` section** — precisely the case the
    step exists for.
  - 🔴 **THE DISCRIMINATOR:** step 3's *sibling* check `git log --since=<doc-date>` — same trigger,
    same block, different tool — fired **0 of 6**. Step 4's `cairn recall` — a numbered,
    unconditional step with a fenced command — fired **5 of 6**.
- **Ruled out:** that the non-firing is correct restraint (the step is conditional on "before
  working any open item", and a resume that reports and waits never reaches it). All six went
  well past that: every one ran `claim-work`, five made edits. via: measurement
- **Ruled out:** that the sessions lacked open items to work — 5 of 6 resumed a doc with an
  `## Open investigations` section. via: measurement
- **Ruled out:** that the deployed skill was stale in those runs, i.e. that they never saw the
  step — the `SKILL` block reports CURRENT, and the one session that WAS behind is the only one
  that fired. via: measurement
- **Ruled out:** that it is specific to `handoff_search` (tool unfamiliarity, cost, output
  distrust) — the co-located `git log --since` check, an ordinary command every session already
  uses, fired 0/6 in the same block. via: measurement
- **Leading hypothesis:** **placement and conditionality, not motivation.** An agent executing
  this skill reliably performs numbered unconditional steps and reliably skips conditionals buried
  in a step's narrative prose, however loud the 🔴. Two independent conditional checks in step 3:
  0/6. One unconditional numbered step next door: 5/6.
- **Next probe:** none needed to establish the finding. 🔴 **The promotion ALREADY LANDED in
  `#1332`** — do not re-do it; what remains is the re-run. Re-run check 1 under "How to verify"
  after ~10 further runs, **raising its `CUT` to `#1332`'s merge time first** (left at `#1295`'s
  it counts the 14 pre-fix runs in the denominator, so a fully successful fix reports ~10/24 and
  reads as a failure). The prediction is that it tracks `cairn recall`'s 5/6, not step 3's 0/6.
- **Residual, SINCE MEASURED — do not re-open this one.** "Whether the index, once queried,
  *yields* anything" was left open here at n=1. It was answered on 2026-09-08 at n=20: **1 of
  20**, with the cause measured. See "RESOLVED — does a hit change what a session does?" below;
  this bullet is kept only so the n=1 reading is not mistaken for the current one.

### RESOLVED (preliminary, n=4; SUPERSEDED at n=22 by the block below) — does the fix change what a session does? Adoption yes
- **Answer:** adoption moved from ~1-in-8 to 4-of-4. This supersedes the earlier block's
  "Next probe", which asked for exactly this re-run — that probe has now been run ONCE, at a
  smaller n than it specified, and its instruction to raise `CUT` was followed.
- **Symptom + exact repro:** re-run check 1 under "How to verify" with `CUT` raised to `#1332`'s
  merge time (`2026-09-06T17:51:19Z`). Left at `#1295`'s the 14 pre-fix runs stay in the
  denominator and a fully successful fix reads as ~10/24, i.e. as a failure.
- **Observed (with values):**
  - workbench, `#1332`..now (8.6 h): **resume runs=4, handoff_search=4 (100%), cairn recall=4/4**.
  - workbench, `#1295`..`#1332`: **resume runs=11, handoff_search=2 (18%), cairn recall=10 (91%)**.
  - laptop, `#1332`..now: **resume runs=0** — contributed nothing.
  - The four post-fix sessions and their queries: `d1a30b84` (a storage-reclaim sweep),
    `c07a10b6` (a PWA caching problem), `9c7cf8e5` (an app-block card issue), `6b4118f0`
    (an SSR CPU regression). All four are TOPIC-shaped, none is an open-item restatement.
- **Ruled out:** that the 4/4 is the measuring session inflating its own numerator — the four
  session ids are distinct from this session's and all sit in a different repo. via: measurement
- **Ruled out:** that the pre-fix rate was as high as post-fix, i.e. that nothing changed — the
  same needle over the immediately preceding window on the same host gives 2/11. via: measurement
- **Ruled out:** that the co-located control is itself broken (which would make 4/4 meaningless) —
  `cairn recall` fired 4/4 post-fix and 10/11 pre-fix, i.e. it behaved as the stable surface the
  prediction was keyed to. via: measurement
- **Leading hypothesis:** the placement fix worked as designed. 4/4 is unlikely under the old
  rate (≈0.001 at 18%, crude binomial), but n=4 is small and the four runs are one repo and
  possibly one operator workflow, so this is a strong direction, not a settled rate.
- **Next probe:** none — run 2026-09-08, at n=20 rather than the 4 it specified. See the block
  below, which supersedes this one's residual.
- **Residual, NOT measured:** the laptop (still 0 runs) and any repo other than
  `datapacket-talos`.

### RESOLVED — does a hit change what a session does? YIELD = 1 of 20, and the cause is structural
- **Answer:** yes, once, and the single case is genuinely the thing the index was built for —
  but 19 of 20 sessions did nothing with the result, and the reason is now measured.
- **Symptom + exact repro:** for every post-fix session that invoked `handoff_search.py --`,
  read the turns AFTER the tool result and record (a) whether the output was reported to the
  operator, (b) whether a hit document was subsequently opened, (c) whether a decision, probe
  or plan moved. Session ids come from check 1; the transcripts already exist.
- **Observed (with values):**
  - **20 post-fix sessions queried the corpus.** In **19**, the next action was `claim-work`
    or `gh pr view` and no hit was opened or mentioned. Exactly one session referred to the
    output at all, to say *"Recall surfaces returned nothing new."*
  - 🔴 **THE ONE THAT PAID OFF, AND IT PAID OFF ~5 h LATER, NOT AT STEP 4.** Session
    `d1a30b84` ignored its hits at resume time like the rest. Later, asked to `recommend`, it
    opened with *"Let me ground this rather than recommend from a corpus echo"*, read the
    doc that had been **hit #2** of its query (`rank=1.6667`, a DIFFERENT effort in the same
    repo), corrected its own first reading of it — it had taken an `## Open investigations`
    line for current state, the exact hazard step 3 warns about — and **changed its
    recommendation away from the entire backlog it was resuming** to an unclaimed
    `forcing: security` item in that other effort. Cross-effort prioritisation is precisely
    the value proposition; the delivery mechanism was **context carryover, not the step**.
  - 🔴 **WHY THE OTHER 19 GOT NOTHING: 23 of 60 hit slots (38%) were the session's OWN
    handoff**, already read in step 3, and it was the **#1 hit in 13 of 20** queries — three
    of three slots in two of them. Arithmetic, not luck: step 4 says query the handoff's
    TOPIC, and the best text match for a doc's topic is that doc. **The re-keying that made
    the step unconditional is the same thing that aimed it at itself.**
- **Ruled out:** that the hits were merely *unreported* while still informing the work — the
  check is not "was it mentioned" but "was a hit DOCUMENT opened", and outside `d1a30b84` no
  session touched a hit doc that was not its own handoff or its own `/handoff` write-back.
  via: measurement
- **Ruled out:** that the self-hits are an artifact of the fixtures or of one repo — the 38%
  spans 20 sessions across four repos, and the two 3-of-3 cases are different repos.
  via: measurement
- **Leading hypothesis:** yield is low because the retrieval budget is spent on the one
  document guaranteed to be redundant. Excluding it frees a third of the slots — and the top
  slot two times in three — for documents the session has not read.
- **Next probe:** RUN — 2026-09-11, at n=16. **The prediction (a rise off 1/20) was WRONG and the
  falsifier fired: yield 0 of 16.** See "RESOLVED — the re-measurement" immediately below, which
  supersedes this block's hypothesis; the exclusion did free the slots exactly as predicted and
  yield did not move.
- 🔴 **THE INSTRUMENT HAD TO BE REBUILT MID-MEASUREMENT, and the tell was a perfect number.**
  The first self-hit pass scanned transcripts for `claudedocs/handoff-<slug>.md` and reported
  **100%** — because the search's own output prints each hit's doc path, so the needle was
  matching the instrument. `claude/RULES.md` → "validate the INSTRUMENT before you read its
  verdict". The 38% is rebuilt from two sources the search cannot write: `resume-state.sh`'s
  `handoff:` line and `claim-work --slug-for` arguments. **A 100% or a 0% is a reason to
  re-check the needle before writing it down.**
- **Residual, SINCE MEASURED:** whether the freed slots are USED. Answered below — they are not.

### RESOLVED — the re-measurement: the slots were freed and yield went to ZERO. Step retired.
- **Answer:** the fix worked and the feature did not. `--exclude-slug` removed the self-hit
  entirely (**0 of 48** slots, **0 of 16** top slots), adoption was total (**16 of 16** sessions
  ran it with the flag), and **0 of 16** sessions opened, mentioned or acted on a hit — down from
  1 of 20. The falsifier written into the previous rank 1 fired, and the step was retired rather
  than the ranker tuned, because that is what the pre-commitment said to do.
- **Symptom + exact repro:** count `--exclude-slug` queries since `#1399` merged
  (`2026-09-09T07:29:22Z`); for each, read the turns AFTER the tool result and record whether a
  hit DOCUMENT was opened. Derive the session's own slug from `resume-state.sh`'s `handoff:` line
  and `claim-work --slug-for` arguments — **never** from the search's own output.
- **Observed (with values):** 19 query calls / 18 with the flag / 16 sessions, across 5 repos.
  48 hit slots. 0 self-hits. 0 sessions acted. **44 of 48 hits were `[gotcha#0]`**; 29 distinct
  docs filled 48 slots; `appblock-tool-calling` and `app-taste-rollout` appeared **5× each**
  across unrelated topics; ranks 0.97–1.75, mean 1.40.
- 🔴 **THE DECISIVE CASE, because it separates "empty corpus" from "bad recall".** `d775cf17`
  queried *"cairn CI intermittent store-api test fails on fsync stall in Tekton pytests tier"*.
  The corpus contains `devrc/gate-flake-store-api`, which is about exactly that. Reproduced live:
  it ranks **11th**. The session got it anyway — from `cairn search 'fsync'`, run 13 records
  BEFORE the handoff_search call. Two retrieval surfaces, same question, one answered it.
- **Ruled out:** that the corpus has nothing to offer (the falsifier's own stated reason) — the
  on-topic document existed and a sibling tool retrieved it. via: measurement
- **Ruled out:** that low yield is an adoption failure in disguise — 16 of 16 sessions ran the
  command, all with the flag, and 0 of 48 slots were self-hits. via: measurement
- **Ruled out:** that the 0 is a dead needle. 🔴 **POSITIVE CONTROL, and it is what makes the
  zero reportable:** the same parser and needle over the PRE-fix window returns **37% self-slots**
  — reproducing the independently-recorded 38%. The instrument can see self-hits; there are none.
  via: measurement
- **Ruled out:** that any session did act and was missed — two apparent hits were substring
  collisions (`clickup-mirror` inside `clickup-mirror-check`, each session's OWN handoff). On an
  exact-filename match it is 0 of 16. via: measurement
- **Leading hypothesis (NOT acted on, and deliberately left as a hypothesis):** a section-grained
  ranker returns long generic sections for topic-shaped queries, and 3 slots cannot survive that.
  A larger `--limit`, or down-weighting the Gotchas block, might move it. **Nobody should start
  that without a new reason** — see the ⚠ in "State now".
- **Shipped:** the `/resume` step-4 command removed; `test_resume_handoff_search_wiring.py`
  replaced by `test_resume_handoff_search_retired.py`, which fails if the command returns and
  hands over the measurement that must be redone first.

## Next steps (ranked)
🔴 **THE OLD RANK 1 IS CLOSED — DO NOT RE-RUN IT.** "Re-measure yield once `--exclude-slug` has
~20 queries behind it" was run on 2026-09-11 at n=16: **yield 0 of 16, self-hits 0 of 48**, the
falsifier fired, and the `/resume` step was retired. Full evidence in "RESOLVED — the
re-measurement". Nothing below is waiting on it.

⚠ **AND NOTHING BELOW IS LOAD-BEARING ANY MORE.** With the step retired, ranks 1–4 are all
polish on a tool that now has only ad-hoc callers. They are kept because each is a real, measured
defect and cheap to close — **not** because anything depends on them. If the answer to "should I
work this?" is not obvious, the honest one is **no**.

1. **A BLANK `--exclude-slug` is still a silent no-op, and it is reachable from the CLI.**
   `--exclude-slug "  "` normalises to `""`, prints `excluded=` with nothing after it, leaves
   `in_scope_docs == indexed_docs`, and returns the document the caller meant to drop — the exact
   class three audit rounds closed everywhere else. The library layer accepts `[""]` too. Round 4
   measured both; this PR deliberately fixed only the *advice* (the refusal no longer offers a
   blank as its remedy) and NOT the behaviour, to keep the audit ladder's last round inside the
   claim-correction criterion it stopped on. 🔴 The fix belongs at `main()` as an input rejection
   (`RC_USAGE` / rc 2), never in the renderers — a `label or "(unnamed)"` there re-introduces the
   falsy-string shape swept out of the decision path.
   **Closing condition:** a merged PR in which `handoff_search.py --exclude-slug "  "` exits 2,
   with a test that watches it fail at the previous commit.
   forcing: none
2. **`ship.sh` cannot reach the laptop off-LAN, and the fallback address it already knows is
   unused.** `host-role.sh` defines `LAPTOP_IP_SECONDARY=10.42.0.100` (nebula) beside the primary
   `192.168.50.155`, but the SSH default derives from the primary only — so from off-network the
   remote leg dies `Connection timed out`, rc 255, and the run reports `incomplete`. Measured
   2026-09-09 shipping `4a67ea73`: the laptop converged only after a manual
   `REMOTE_SSH=zach@10.42.0.100`. It fails honestly rather than silently, so this is an
   ergonomics gap, not a correctness one — **but it splits the run in two, and `ship.sh` then
   prints `cross-host agreement NOT COMPARED`, which is the check that exists to catch the two
   hosts landing on different commits.**
   **Closing condition:** a merged PR where the remote leg falls back to the secondary address
   when the primary is unreachable (or the rc-255 message names `REMOTE_SSH` and the nebula
   address), with a test that watches the fallback fire.
   forcing: none
3. **The label/derivation granularity residual** — `scripts/lib/handoff_index.py`,
   `rebuild_delete_labels`. Its own round, because the fix moves the delete scope. The tripwire
   test `test_through_main_the_residual_is_recorded_and_the_report_is_true` fails the day someone
   does it, which is the intended signal.
   forcing: none
4. **The empty-label display residual** — an empty label renders blank on FOUR surfaces; the
   operator sees THAT rows were deleted, not WHICH. 🔴 The fix belongs at `main` as an input
   rejection (`RC_USAGE`), NEVER in the renderers — a `label or "(unnamed)"` there would
   re-introduce the exact falsy-string shape three audit rounds swept out of the decision path.
   forcing: none

## Gotchas / decisions / dead-ends
- **`--offline` is the useful surface today** — it answers from git refs with no database, and
  a different ranker. It is how everything in this doc was verified.
- **The index is DERIVED and DISPOSABLE** — `--rebuild` truncates by design; git stays the
  system of record. Never treat the table as authoritative.
- **Indexed from git refs, not the working tree.** This box carries ~640 worktrees across four
  repos; a disk scan would index mid-edit branches, the same doc N times, and stale orphans.
- **A doc on disk but NOT in the mainline ref is reported as a durability hole and deliberately
  never indexed** — indexing it would let the search answer *from* the hole and conceal it.
  That is what surfaced `handoff-limewire-torrent-comps.md`, untracked for six days as the sole
  copy; rescued as `homelab-infra` `d2c9c49a`.
- **Rejected: S3/MinIO for the corpus.** 8.6 MB total. Object storage adds a fourth copy and
  key-lookup, not query — git already gives redundancy. A Postgres text column + GIN gives the
  thing that was actually missing.
- **Rejected: adding `.md` to `captured_text_scan.SCANNED_SUFFIXES`.** Its premise is free text
  where a data field should be; handoff docs are prose throughout, so it would fire on every
  doc and be disabled.
- **Rejected: a "will actually write" boolean** to fix plan sentences printed on refusing runs.
  That boolean *is* the gate's verdict in a second spelling, free to drift. Fixed by ordering —
  the plan prints only after every gate passes.
- **Prose is pinned as a WHOLE NORMALISED STRING** (`_EXPECTED_ORPHAN_WARNING`), not
  substrings — a substring pin let a reword mutant survive a green sweep.
- **The cairn CI red was an I/O stall, not a code failure.** `run_cairn` passes `--timeout 5`
  against a `_replace_bytes` that fsyncs file *and* parent dir inside the request — 12× tighter
  than the store-api's 60 s `HANG_TIMEOUT`, which is why that file was the frequent casualty.
  Fixed upstream by store siting (`b4fde334`); `#1244` adds the classifier so a stall stops
  reading as a code failure. Diagnosis, not tolerance — no bound moved, nothing retries.
- **Branch protection is currently OFF on `devrc`** by deliberate operator decision, so nothing
  blocks a merge and the human is the gate. Run both tiers on the MERGED tree and name the base
  sha in the claim.

- 🔴 **A HAND-RUN NEEDS `KUBECONFIG=$KC_HOMELAB`.** This repo leaves `KUBECONFIG` unset on purpose
  so a bare `kubectl` cannot hit prod, so the DSN read dies with `CalledProcessError` on
  `kubectl -n mailbox get secret` — loudly, before touching the database. The UNIT sets it; only
  the hand-run path lacks it. Measured while arming: the first `--write` failed exactly this way.
- **`indexed_docs` and the derivation's `docs=` count different things, both correctly.** 464
  derived vs 381 indexed is NOT slug collisions: sections match EXACTLY at every level, and an
  overwritten doc would have taken its sections with it. The gap is docs that yield zero sections.
- **`indexed_*` are GLOBAL totals; `in_scope_*` are the filtered counts.** Reading a truncated
  line and seeing only the global pair looks exactly like a broken `--repo` filter. It is not.
- **The audit ladder ran 5 rounds on `#1209` and 3 on `#1267`, and EVERY round's findings were
  created or half-closed by the previous round's fix.** Both ladders were stopped on the
  prose-payload criterion, not on a clean round: by the end the payload was ~32 executable lines
  out of 244 added, and most findings were prose claiming more than the code delivered. When a
  defect fix and a reword are the same edit the round-over-round gate is structurally inert.
- **Rejected: S3/MinIO.** 8.6 MB corpus. Object storage adds a fourth copy and key-lookup, not
  query; git already gives redundancy.
- **Rejected: adding `.md` to `captured_text_scan.SCANNED_SUFFIXES`.** Its premise is free text
  where a data field should be; handoff docs are prose throughout, so it would fire on every doc
  and be disabled.

- 🔴 **MEASURING ADOPTION OF A SKILL STEP: A MENTION IS NOT AN INVOCATION, AND THE OVERCOUNT IS
  ~24×.** The deployed `SKILL.md` body contains the very command string you are looking for, so
  every session that merely LOADED the skill matches `grep -l`. Measured 2026-09-06: 24 transcript
  files matched the string, **1** contained a real invocation. Match on a Bash `tool_use`
  `command` field, and tighten the needle to `handoff_search.py --` — a bare `handoff_search`
  also catches the `grep` you are running to do the measurement, which is how the first pass
  reported 2 hits instead of 1. **Generalises to any "is this step being followed" question.**
- 🔴 **AN UNCONDITIONAL NUMBERED STEP IS FOLLOWED; A CONDITIONAL BURIED IN PROSE IS NOT — AND
  🔴 EMPHASIS DOES NOT CLOSE THE GAP.** Measured across 6 sessions in one skill on one day:
  step 4 (`cairn recall`, numbered, unconditional, fenced) **5/6**; step 3's two embedded
  conditional checks (`handoff_search`, `git log --since`, both fenced, both marked 🔴)
  **0/6 each**. The variable is not the tool, not its cost and not how loudly it is marked — the
  two step-3 checks differ from each other in every way except placement. **When a skill step is
  not firing, move it before rewording it.**
- 🔴 **"Live and verified" and "used" are different claims, and the gap between them is
  invisible to every check this effort built.** Six merged PRs, both hosts converged, the timer's
  own run green, the DB path exercised, `backend=postgres` confirmed — and 13 of 14 consumers
  never called it. Deployment verification cannot see adoption; only reading real runs can.

- 🔴 **AN UNCONDITIONAL NUMBERED STEP IS FOLLOWED; A CONDITIONAL IN PROSE IS NOT — AND THE FIX
  IS NOW CONFIRMED FROM BOTH ENDS.** Predicted 5/6 from the `cairn recall` control, measured
  **4/4** post-fix against **2/11** pre-fix on the same host with the same needle. The variable
  was placement, never the tool, its cost, or how loudly it was marked. **When a skill step is
  not firing, MOVE it before rewording it.**
- 🔴 **RE-KEYING WAS HALF THE FIX, AND IT IS THE HALF THAT IS EASY TO MISS.** Moving the command
  to an unconditional step is useless if the query still needs an open item — a session with no
  open item has nothing to type, so the step is conditional in substance while looking
  unconditional. Keying on the handoff's TOPIC is what made it runnable every time; all four
  post-fix queries are topic-shaped. **Ask what INPUT a step needs, not just where it sits.**
- 🔴 **A RETRIEVAL STEP KEYED ON A DOC'S TOPIC RETRIEVES THAT DOC — 38% OF THE BUDGET, AND THE
  TOP SLOT 65% OF THE TIME.** Measured over 20 real queries: 23 of 60 hit slots were the
  session's own handoff, already read minutes earlier in step 3. It is arithmetic, not a
  ranker bug: the best text match for a document's topic is that document. **Whenever a
  retrieval query is keyed on something the caller already holds, ask what fraction of the
  results the caller has ALREADY SEEN** — the answer is the retrieval you are not getting.
  Closed by `--exclude-slug`, which the `/resume` fence now passes.
- 🔴 **EVERY LAYER PINNED, AND THE FLAG STILL DELETABLE TO INERT WITH A GREEN SUITE.** `#1399`
  shipped 13 tests covering `exclusion_slug`, the row filter, both backends' bound params, the
  renderer and the `filtered` flag — and **nothing pinned that `main()` hands `exclude` to
  `run_search` at all.** An audit deleted `exclude=exclude` from the CLI call site: the flag
  became completely inert, the excluded doc came back as hit #1, and **304 of 304 tests
  passed** — 304 being the TWO-FILE scope (`test_handoff_index.py` +
  `test_resume_handoff_search_wiring.py`), not the ~21k-test suite.
  The pre-existing "🔴 THE SEAM" guard proves only that argparse ACCEPTS the flag — it passes
  `--limit 0`, which returns rc 2 *before any store is built*. `claude/RULES.md` → "verified in
  isolation is the new vacuous green" and "a count of DECLARATIONS is not a count of INSTANCES".
  **Ask which line makes the feature REACH the code you tested, and pin that line** — the two
  call sites are now pinned behaviourally (offline) and by an AST ledger that fails if a call
  site is added without `exclude=` as well as if one is removed. 🔴 **And the ledger pins the
  VALUE, not just the keyword** — round 2 wrote `exclude=()` at the postgres site and it
  SURVIVED all 314 tests — again the two-file scope, at that round's tip — while the
  keyword was present and the flag was inert. A guard on a NAME
  is walkable by supplying a different value under the same name.
- 🔴 **I FIXED THE CLASS IN ONE BRANCH AND WROTE A COMMENT SAYING SO, WHILE ITS NEIGHBOUR KEPT
  THE DEFECT.** The `empty-scope` remedy was taught to name only the flags the run passed, with a
  comment citing "the same defect the no-match branch below already fixed once" — and `no-match`
  went on telling exclusion-only runs to "widen `--repo` / `--section`". That is the branch that
  matters: against a 401-doc corpus one exclusion can essentially never empty the scope, so
  `no-match` is what a real `/resume` hits and `empty-scope` needs a one-doc corpus. Now ONE
  function (`active_filter_flags`) answers it for both. **When a commit claims to fix a class,
  sweep every site the way you swept the hardest one** — a comment asserting the sweep is not the
  sweep.
- 🔴 **A SILENT NO-OP IS THE WORST FAILURE A FILTER CAN HAVE, AND THREE INPUT SHAPES HAD IT.** An
  absolute path, a `handoff-x` basename with no `.md`, and a trailing space each derived a garbled
  slug, printed a confident `excluded=<garbage>`, matched nothing and returned the document the
  caller was dropping. **A filter that declines to filter renders identically to one that
  worked** — which is why the skill now tells the reader to check the COUNT, not just the tell.
- 🔴 **TWO OF THIS DOC'S OWN `Open investigations` BLOCKS WERE ANSWERED WEEKS AGO AND STILL READ
  AS LIVE.** Both were closed by `#1267`; both kept a present-tense heading and a `Next probe`
  under a section titled *live diagnosis state*, with their own `RESOLVED —` counterparts 16 and
  55 lines below them. Four `/resume` runs read this doc during the arc and none caught it,
  including mine. **A RESOLVED block does not retire the OPEN one — deleting or re-heading the
  OPEN one is a separate edit, and nothing prompts it.** When you answer an open investigation,
  re-head the block in the SAME commit; `grep -n '^### '` over this file is the whole check.
- 🔴 **AN UNSCOPED TEST COUNT IS A COVERAGE CLAIM, AND THIS PR MADE IT FOUR TIMES.** "the whole
  suite green at 304", "SURVIVED all 314 tests", "a 306-test green suite", "304 of 304 tests
  passed" — every one numerically TRUE and every one naming a two-file scope of ~300 against a
  repo of ~21,000. **FOUR quoted counts, FIVE textual sites** — `SURVIVED all 314 tests` occurs
  twice — and rounds 1-3 each caught one site and fixed that site. Round 4 found the fifth still
  unswept, inside the very test the earlier version of this bullet held up as the exemplar; the
  bullet had already claimed the sweep was complete. 🔴 **A number needs the DENOMINATOR'S NAME,
  not just the numerator** — and when a round fixes an instance of a shape, grep for the shape
  and COUNT the hits, because "I swept it" is itself a claim of exactly the kind this bullet is
  about.
- 🔴 **A PERFECT 100% (OR 0%) IS A REASON TO SUSPECT THE NEEDLE, NOT TO WRITE IT DOWN.** The
  first pass at the number above reported 23-of-23 self-hits, because it grepped transcripts
  for `claudedocs/handoff-<slug>.md` — a string the SEARCH ITSELF PRINTS for every hit, so the
  needle matched the instrument's own output. Rebuilt from `resume-state.sh`'s `handoff:` line
  and `claim-work --slug-for` arguments, which the search cannot write, it is 38%. Same family
  as the `~24×` mention-vs-invocation overcount two bullets down: **both times the tool under
  measurement was emitting the exact string being counted.**
- 🔴 **A MEASUREMENT'S DENOMINATOR MUST NAME ITS OWN INSTRUMENT.** The first adoption reading
  had the measuring session inside the denominator, giving `8 − 1 = 7` where the analysis used
  6; an audit caught it. Every reading since states the accounting (`8 = 1 fired + 6 analysed +
  1 instrument`) and carries an as-of timestamp, because the corpus grows and a later re-run
  reports LARGER numbers rather than contradicting the earlier ones.
- 🔴 **A GUARD'S FIXTURE CAN BE ONE THAT CANNOT FAIL.** `#1332`'s scope pin searched the whole
  wiring block while `EXPECTED_COMMAND` contains `~/workspace/devrc/…`, so its `DEVRC` arm was
  true on every green run; the sweep had picked `DATAPACKET`, the fixture that could only die.
  Narrowing to the paragraph was measured INSUFFICIENT (the paragraph says "a devrc-topic
  query"). Fixed by matching BACKTICKED tokens. **Pick a fixture distinct from every constant
  the assertion already names, and mutate the arm you think is safest.**
- 🔴 **THE STATUS HEADER IS THE PART THAT GOES STALE AND THE PART NOBODY SWEEPS.** Three separate
  sites in THIS doc said "no fix is built" while the PR shipping it was open; two audit rounds
  fixed one site each and a third round found the one they both missed, in `Open investigations
  → Next probe` — exactly the section `/resume` step 3 reads. **When a status changes, grep the
  whole doc for the old claim; fixing the obvious site is not the job.**
- 🔴 **MEASURING ADOPTION OF A SKILL STEP: A MENTION IS NOT AN INVOCATION (~24× overcount).** The
  deployed `SKILL.md` body contains the command string, so every session that merely LOADED the
  skill matches `grep -l`. Match a Bash `tool_use` `command` field and needle on
  `handoff_search.py --` — a bare `handoff_search` also catches the grep doing the measurement.
- **"Live and verified" and "used" are different claims, and the gap is invisible to every check
  this effort built.** Six merged PRs, both hosts converged, the timer green, `backend=postgres`
  confirmed — and 13 of 14 consumers never called it. Deployment verification cannot see
  adoption; only reading real runs can.
- **The audit ladder stopped on the ATTRIBUTION GATE, not on a clean round.** Rounds 2 and 3 both
  changed **zero** payload lines (`claude/skills/resume/SKILL.md` untouched since round 1) — the
  rounds were auditing the guard and the notes they had themselves written. Two consecutive
  zero-payload rounds is the documented stop. Every round-3 finding was fixed first.
- ⚠ **Two mutants remain UNCOVERED by the wiring guard and are named in its docstring:** a gating
  sentence above the fence, and a conditional comment inside it. Both re-create the hazard
  without moving the command. The docstring also says the mutant list is NOT closed — M5 (the
  command commented out) was found only after round 1 called the residual settled.

- 🔴 **A `RESOLVED:` BULLET DOES NOT RETIRE AN `OPEN:` ONE — MEASURED ON THIS EFFORT'S OWN CAIRN
  ENTRY, WHILE FIXING THE SAME CLASS IN THIS DOC.** The `devrc/handoff-index` entry carried
  `OPEN: answer YIELD`. Appending a full `RESOLVED:` bullet answering it left the badge reading
  **`🔴 1 OPEN`** — the store is append-mostly and prune-on-resolve is manual, so the marker sits
  on the OLD bullet and nothing moves it. Closing it needs the OPEN bullet itself rewritten
  (`cairn put`), which is a *second* edit nothing prompts. **Identical shape to the two stale
  `### ` blocks in this doc**: writing the resolution and retiring the open marker are two edits,
  and only the first feels like progress.
- 🔴 **A CLOSURE THAT NAMES NO SHA IS `⚠ UNVERIFIABLE`, AND THE FIRST ATTEMPT SCORED IT.** The
  first `RESOLVED:` bullet said "squash 4a67ea73" in prose; the validator wants a bare sha it can
  `git cat-file -e`, so the entry showed `⚠ 1 UNVERIFIABLE` beside the still-open badge. Both
  fixed in one `cairn put`. **Write `RESOLVED <full-sha>:` — the sha is what makes the closure
  checkable rather than asserted**, and the badge is the only thing that will tell you.
- 🔴 **A WORKTREE HOLDING A BRANCH DEFEATS `gh pr merge --delete-branch` — LOCALLY, AND SILENTLY.**
  After `#1399` merged with `--delete-branch`, the REMOTE branch was gone but the local one
  survived, because `/home/zach/workspace/devrc-selfhit` still had it checked out — a worktree
  pins its branch repo-globally. The tell is confusing: `git branch -a` lists both the live local
  branch and a STALE `remotes/origin/…` tracking ref, so it reads as "the delete failed
  entirely". 🔴 **`git ls-remote --heads origin <branch>` is the authority** — it came back empty,
  i.e. GitHub had deleted it and the push-delete error was deleting something already gone.
  Remove the worktree first, then `branch -D`, then `fetch --prune`.
- ⚠ **A `/handoff` or `/resume` run against this repo's BASE CLONE can be reading a stale doc.**
  Measured at the start of this session: the working-tree copy was **553 lines with 0
  `SUPERSEDED` headings** while `origin/main` held **584 and 2** — one commit behind, and
  `handoff_doc.py` resolves its base from the working tree. Merging a delta there would have
  classified every section as NEW and **replaced the committed doc**. `git rev-list --count
  HEAD..origin/main` plus a content check before drafting; `merge --ff-only` to fix.

- 🔴 **A FALSIFIER'S CONDITION AND ITS STATED REASON ARE TWO CLAIMS — THE CONDITION FIRED AND THE
  REASON WAS WRONG.** Rank 1 pre-committed: *"if yield stays ~1/20 with foreign hits filling every
  slot, the corpus has little to offer and the right response is to stop paying for the step."*
  The condition fired exactly (0/16, 48/48 foreign). The reason did not: the corpus HELD the
  on-topic doc and ranked it 11th, while a sibling tool retrieved it on the same question.
  **Honour the condition — a pre-commitment you renegotiate after seeing the data is not one —
  but do NOT inherit its reasoning**, because the recorded reason is what the next session will
  quote. Here the difference decides whether "retrieval over this corpus is worthless" (false and
  discouraging) or "this ranker at limit 3 is" (true and narrow).
- 🔴 **A GUARD SCOPED TO FENCED BLOCKS MATCHED **ZERO** FENCES, AND PASSED.** The retirement guard
  scanned ```` ^``` ```` anchored at column 0 — but every fence in `resume/SKILL.md` is indented
  three spaces inside a numbered list item, so the scanner returned an EMPTY list and "no
  invocation among the blocks" was vacuously true forever. It went green on the mutant that
  re-added the command verbatim. **The only thing that caught it was the mutation test**, which is
  the whole argument for running one: the guard's own logic, its message and its needle were all
  correct. Fixed, plus a **scanner positive control** (`test_the_fence_scanner_actually_finds_this
  _skills_fences`) that fails if the block list is ever empty again. **When a guard is scoped to a
  SLICE of a document, pin that the slice is non-empty — a scoper that selects nothing turns every
  assertion inside it green.**
- 🔴 **AN OFF-BY-ONE READ WINDOW MANUFACTURED FOUR "NO RESULT" SESSIONS.** The transcript extractor
  looked for each query's `tool_result` in `recs[i:i+6]`; every result sits at delta **6**, which
  that slice excludes. Four of sixteen sessions reported an empty result, and an empty result reads
  as *"the query returned nothing"* — a substantive finding — rather than *"my reader missed it"*.
  `claude/RULES.md` → "an EMPTY RESULT cannot distinguish two mechanisms". **A zero from your own
  extractor is a claim about the extractor first.** The tell was that the zeros were all-or-nothing
  per session rather than distributed.
- 🔴 **SLUG MATCHING BY SUBSTRING GAVE TWO FALSE "A SESSION ACTED ON A HIT" READINGS, BOTH WAYS.**
  `clickup-mirror` (a hit doc) is a prefix of `clickup-mirror-check` (the session's OWN handoff),
  and it is also the name of a script DIRECTORY both sessions were already working in. So a loose
  needle scored a session as acting on recall when it was reading its own doc and its own code.
  **Match the full filename (`handoff-<slug>.md`), and remember that slugs in this corpus nest.**
- 🔴 **TWO RETRIEVAL SURFACES WERE ASKED THE SAME QUESTION AND ONLY ONE ANSWERED — that comparison
  is what made the diagnosis possible.** Session `d775cf17` ran `cairn search 'fsync'` and
  `handoff_search` on the same fsync-stall question, thirteen records apart. Cairn returned the
  right doc; the search ranked it 11th. **A co-located control is worth more than any amount of
  reasoning about a ranker's output** — it separates "nothing to find" from "found by the other
  tool", which no amount of reading the hit list can do.
- 🔴 **ADOPTION AT 100% IS NOT EVIDENCE OF VALUE, AND THIS IS THE CLEANEST DEMONSTRATION OF IT.**
  Three PRs each fixed a real, measured defect — the step did not fire, so it was moved (2/11 →
  20/22); it retrieved the caller's own document, so that was excluded (38% → 0%). Both fixes
  worked completely. Yield went 1/20 → 0/16. **Every intermediate metric can improve while the
  thing they are proxies FOR does not move**, and only the end-to-end read can say so. The
  intermediate metrics are still worth measuring — they are how you know the retirement is about
  the feature's value and not about a broken deployment.

## How to verify
🔴 **READ THIS BEFORE RUNNING CHECK 1 OR 3b: THE STEP IS RETIRED, SO BOTH NOW MEASURE A HISTORICAL
WINDOW, NOT A LIVE ONE.** `/resume` no longer invokes `handoff_search.py`, so after the retirement
ships, adoption goes to **0 by design** and a future run of check 1 reporting `queried=0` is the
system working, not a regression. They are kept because the closed window
(`#1399`..2026-09-11) is what the retirement rests on and must stay re-derivable. **Check 6 is the
one that verifies the retirement itself.**

```bash
# 1. ADOPTION — HISTORICAL after the retirement (see the note above). THIS HOST only; run on both.
#    🔴 CUT is now #1332's merge, NOT #1295's. Left at #1295's, the 14 pre-fix runs stay in the
#    denominator and a fully successful fix reports ~10/24 and reads as a FAILURE.
#    Controls watched to work: a MENTION is not an invocation (needle on `handoff_search.py --`,
#    which also excludes the grep doing the measuring); the measuring session is excluded from
#    the numerator by checking the session ids are distinct.
python3 - <<'PY'
import json, glob, os
CUT = "2026-09-06T17:51:19"   # #1332 merged — raise again after the next such change
runs, hits, cairn = set(), set(), set()
for f in glob.glob(os.path.expanduser("~/.claude/projects/*/*.jsonl")):
    sid = os.path.basename(f)[:-6]
    for line in open(f, errors="replace"):
        if ("resume-state.sh" not in line and "handoff_search.py --" not in line
                and "cairn recall" not in line):
            continue
        try: r = json.loads(line)
        except Exception: continue
        if r.get("timestamp", "")[:19] < CUT: continue
        c = (r.get("message") or {}).get("content")
        if not isinstance(c, list): continue
        for b in c:
            if not isinstance(b, dict) or b.get("type") != "tool_use": continue
            cmd = (b.get("input") or {}).get("command", "")
            if not isinstance(cmd, str): continue
            if "resume-state.sh" in cmd: runs.add(sid)
            if "handoff_search.py --" in cmd: hits.add(sid)
            if "cairn recall" in cmd: cairn.add(sid)
print(f"runs={len(runs)} queried={len(hits & runs)} cairn(control)={len(cairn & runs)}")
PY
#    2026-09-07T02:25Z baseline: workbench 4/4 (control 4/4); laptop 0 runs.
#    2026-09-08T17:00Z re-run:   workbench 20/22 (91%), control 21/22; laptop STILL 0 runs
#                                (measured over ssh, not assumed).
#    🔴 THE SCRIPT ABOVE DOES **NO** SESSION-ID EXCLUSION — it printed 23/21/22, and the
#    20/22 was reached by subtracting the measuring session BY HAND afterwards. Said plainly
#    because an audit read the annotation as describing the script and could not reproduce it.
#    If you want the exclusion mechanised, filter `sid` against your own session id; otherwise
#    report the raw triple and say which one you subtracted.
#    An independent re-run hours later (larger window): runs=36 queried=33 (92%), control 34.
#    Pre-fix same host, #1295..#1332: 11 runs, 2 queried, control 10.
#    ⚠ The laptop half needs the same script run THERE — this one reads only this host's
#    ~/.claude/projects. A workbench-only number is not a fleet number.

# 2. The timer's OWN run — the only thing that tests the unit's environment:
systemctl --user show handoff-index-sync.service -p Result -p ExecMainStatus
journalctl --user -u handoff-index-sync.service --no-pager -n 20

# 3. The DB path answers (backend= is the discriminator, NOT the row count):
#    🔴 `nix develop` IS REQUIRED, NOT JUST THE KUBECONFIG. A bare `python3` here dies with
#    `psycopg2 is required` before it opens anything — the UNIT has the dependency, a hand-run
#    does not, exactly as the unit has the KUBECONFIG a hand-run lacks. This line said
#    `KUBECONFIG=… python3 …` until 2026-09-08 and could not have worked as written.
KUBECONFIG=$KC_HOMELAB nix develop ~/workspace/devrc -c \
  python3 ~/workspace/devrc/scripts/lib/handoff_search.py --query fsync --limit 3
#    expect backend=postgres. backend=memory means it silently fell back and you verified nothing.
#    2026-09-08: backend=postgres, indexed_sections=5125 — the SAME number the timer's own run
#    reported writing, which is what makes the two agree rather than merely both be non-zero.

# 3b. The exclusion actually frees slots (the rank-1 fix). A DIFFERENTIAL — one flag apart:
Q="handoff search index adoption yield"
python3 ~/workspace/devrc/scripts/lib/handoff_search.py --offline --query "$Q" --limit 3 | grep '^──'
python3 ~/workspace/devrc/scripts/lib/handoff_search.py --offline --query "$Q" --limit 3 \
  --exclude-slug claudedocs/handoff-handoff-search-index.md | grep -E '^──|excluded='
#    expect: the first names devrc/handoff-search-index in its top slot(s); the second names it
#    NOWHERE, prints `excluded=handoff-search-index` on the scope line, and reports an
#    in_scope_docs LOWER than indexed_docs. 🔴 Do NOT assert "by one": the slug is excluded in
#    EVERY repo, so two repos sharing a doc basename drop two. Measured 2026-09-08: 0 shared
#    slugs between the reachable repos, so one is the usual case, never the contract.
#    🔴 `excluded=` proves the flag PARSED; the COUNT proves it MATCHED — read both, because an
#    unnormalisable value used to print a confident `excluded=<garbage>` and filter nothing.

# 4. The RETIREMENT is LIVE, not merely merged (readlink is the arbiter).
#    🔴 THE SENSE OF THIS CHECK IS INVERTED FROM WHAT IT WAS: the count must now be ZERO.
readlink -f ~/.claude/skills/resume/SKILL.md          # must resolve into /nix/store
grep -c 'handoff_search.py --offline' ~/.claude/skills/resume/SKILL.md   # must be 0
grep -c 'WAS RETIRED ON A MEASUREMENT' ~/.claude/skills/resume/SKILL.md  # must be >=1
#    🔴 A 0 from the first grep with a 0 from the second is NOT a pass — that is a skill that
#    lost the command AND the explanation, which is how it gets re-added. Read the pair.
#    ⚠ `grep -c` on a nix-store symlink reads the DEPLOYED copy; `merge → pull → switch` first,
#    or you are grepping the previous generation (CLAUDE.md → "Merged ≠ deployed").

# 5. The guard still holds (and its own mutants still die):
nix develop ~/workspace/devrc -c python3 -m pytest \
  ~/workspace/devrc/scripts/tests/test_resume_handoff_search_retired.py -q -p no:cacheprovider
#    🔴 MUTATE IT, don't just run it. Re-add the command to step 4's fence and watch
#    test_no_fenced_command_in_the_resume_skill_invokes_the_corpus_search go RED with its own
#    message. The FIRST draft of this guard survived that mutation — it scanned for fences at
#    column 0 while every fence in the skill is indented three spaces inside a list item, so it
#    asserted over an EMPTY block list. `test_the_fence_scanner_actually_finds_this_skills_fences`
#    is the control that now stops that recurring; a green run without it proves little.

# 6. 🔴 THE YIELD READ — the measurement the retirement rests on, and the only one that could
#    reverse it. Re-derivable because the window is closed: #1399's merge .. 2026-09-11.
#    Method (full script in this session's transcript; the shape is what matters):
#      a. collect every Bash tool_use whose command contains `handoff_search.py --` after the CUT,
#         EXCLUDING your own session id — your measuring commands contain the needle.
#      b. for each, find its tool_result — 🔴 it sits at delta 6 from the tool_use; a window of
#         recs[i:i+6] EXCLUDES it and manufactures four false "no result" sessions.
#      c. parse the `^──` hit lines; derive the session's OWN slug from `resume-state.sh`'s
#         `handoff:` line and `claim-work --slug-for` args — NEVER from the search's own output.
#      d. read the turns AFTER the result: was a hit DOCUMENT opened (exact `handoff-<slug>.md`,
#         not a substring — `clickup-mirror` is a prefix of `clickup-mirror-check`)?
#    🔴 POSITIVE CONTROL, MANDATORY: run the same parser over the PRE-fix window
#    (#1332..#1399). It must return ~37-38% self-hit slots. If it returns 0 there too, your
#    needle is dead and the post-fix 0 means nothing.
#    2026-09-11 result: 16 sessions, 48 slots, 0 self-hits, 0 acted. Control: 37% (recorded 38%).
```
