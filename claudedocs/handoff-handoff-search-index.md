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
🔴 **RANKS 6 AND 7 ARE NOW CLOSED — AND THEY READ `OPEN` IN THIS DOC FOR DAYS AFTER SHIPPING.**
Both were fixed and merged while the ranked list still described them as live work. A `/resume`
reading this doc in that window would have claimed rank 7 and re-derived a bug already fixed.
**This is the doc's own recorded hazard, committed a third time by the session that recorded it.**

### Standing results — CARRIED FORWARD (this heading REPLACES; restating is how they survive)
- 🔴 **The yield figure was wrong TWICE; both retracted.** `1 of 20`, then `0 of 16`, same broken
  instrument. `#1518` (which retired the step on that zero) was **closed unmerged**. Step stays.
  🔴 **DO NOT RE-OPEN the yield measurement** — two attempts, wrong in opposite directions, cost a
  shipped-then-closed PR, two audits and a correction PR. **ASK THE OPERATOR.**
- 🔴 **Corrected, same criterion both windows (2026-09-11T23:30Z):** pre-fix **12 of 38 (32%)**,
  post-fix **9 of 17 (53%)**. ⚠ OVERCOUNTS (step 5 mandates reporting hits); strict hand-verified
  **3 firm of 17** (`5320d1bf`, `8152b7fc`, `9b6235dd`).
- 🔴 **The cost question closed BY DELETION (2026-09-12):** command 180 B, defending prose 6,220 B.
- **`#1399`/`#1571` merged, deployed, verified live on both hosts** by CONTENT with a negative control.

### This session (2026-09-13 → 09-19)
- **RANK 1 SHIPPED — `#1637` (`67295ffba`).** A blank `--exclude-slug` exits 2, keyed on the DERIVED
  SLUG not the spelling (so `claudedocs`, `/`, `handoff-.md` are caught; none is blank).
  Mutation re-verified independently: `not v.strip()` dies on `'claudedocs' exited 0`.
- **RANK 7 SHIPPED — `#1670`.** `subsystem_touch.DEFAULT_STORE_ROOT` defaulted to the FROZEN mirror;
  now resolves via `subsystem_read_store.read_store_root()`, the shared resolver
  `subsystem_recall`/`service_recon`/`subsystem-audit` already used. **Measured live: 238 entries via
  the default vs 148 via the mirror** — a 90-entry blind spot in the index WRITER.
  Explicit `--store` stays permissive; the default refuses an undateable store (exit 4).
- **RANK 6 SHIPPED + the ranker — `#1791` (`d8362875`).** Two defects in one PR:
  - **The offline ranker answered on ROW SIZE.** `base = len(present)/len(wanted)` divided by QUERY
    length only, no row normalisation, no stopwords. **Measured: 94% of top-3 slots came from 6.3%
    of the index** (`gotcha`), ~15× enrichment tracking token count. Fix = one-sided length discount
    (`max(1, row_tokens/300)`, pivot ≈ corpus p90) + `STOPWORDS` on both sides.
    **Verified independently before merge:** control `"the and of to a is it that"` 5 hits @ rank
    2.0000 → **0 hits**; a real query went **5/5 `gotcha#0` → 5/5 `investigation#N`**.
    Replay: `Ruled out:` slots **16/66 → 34/66**; queries with ≥1 such hit **10/22 → 20/22**;
    all-`gotcha` queries **8/22 → 0/22**; median returned row **1,274 → 222** tokens.
  - **An archived doc's exclusion filtered NOTHING.** `#1627` archived 35 docs; the corpus holds 34
    nested `archive/` slugs. `resume-state.sh` printed a bare basename, so the prescribed value
    no-opped at rc 0. Now prints the `claudedocs/`-relative path. The false docstring claim
    *"zero nested handoff docs … so this is latent"* is corrected — it is why nobody re-checked.
- **Health measured (2026-09-16), and it is good:** 24 post-merge `/resume` runs, 20 queried the
  corpus; **from 2026-09-14T05:30 onward 15 of 15 (100%)**. **0 actual rc-2 refusals** in real
  sessions — the new input rejection has broken nobody. 0 self-hits, 0 mislabelled provenance.
- **The oversize spillover (`#1650`) and two doc updates (`#1639`, `#1651`) also landed.** All six
  PRs merged; every claim released.
- ⚠ **`clawgate_handoff.sh resolve` returned rc 5 — NOTHING RESOLVED, so this doc carries NO
  `clawgate-task:` field.** An unknown session id answers 200 with an empty array, so that zero
  cannot distinguish "touched no task" from "wrong id". **Not a clean bill of health.**

## Open investigations — live diagnosis state

🔴 **FOUR CLOSED BLOCKS WERE DEMOTED 2026-09-19 to
`claudedocs/refs/handoff-search-index-closed-investigations.md`** — two `SUPERSEDED` and two
`RESOLVED`, all four closed on `origin/main` by CONTENT (`#1267` `d86b4e45`, `#1332` `8e9428ef`),
none of them an open thread. They came out so a required rank-6/rank-7 closure could land under
`test_no_handoff_doc_exceeds_its_budget`. ⚠ That file is **not indexed by `handoff_search`** — if
you need the values (the `DELETE` bindings, the four spellings, the 14-run and 4/4-vs-2/11
adoption reads), open it by path.
- **The guards those blocks carried, restated so they survive the move:**
  - 🔴 **The step-3 → step-4 PROMOTION ALREADY LANDED in `#1332` — do not re-do it.** What the
    demoted block asked for was the re-run, and the re-run has been done; the current figures are
    in `State now`, not in that block.
  - 🔴 **DO NOT RE-OPEN the yield measurement.** Both the `1 of 20` and the `0 of 16` readings are
    retracted — see the `RETRACTED (the yield NUMBER)` block still below, and `State now`.
  - The `n=4` block's own residual ("the laptop, still 0 runs") is stamped **EXPIRED 2026-09-12**
    in the demoted text; it is closed, not pending.
  - The four-spelling ENUMERATION and the unreadable-docs VALUES are the only reason those two
    `SUPERSEDED` blocks were kept at all. Their answers — the two `RESOLVED —` blocks immediately
    below — STAY here and are still indexed.

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

### 🔴 RETRACTED (the yield NUMBER) — does a hit change what a session does? `1 of 20` was an
### INSTRUMENT ARTIFACT; the self-hit half of this block still stands
- 🔴 **Answer (CORRECTED 2026-09-11):** the `1 of 20` here is an INSTRUMENT ARTIFACT — it counted
  only sessions that OPENED a hit document, so every session that MENTIONED a hit, reported it
  under step 5's own `from handoff docs` label, or wrote it into its own handoff was scored zero.
  The true rate is higher; see "State now" for the corrected figures and the three hand-verified
  cases. **The self-hit half of this block (23 of 60 slots, #1 hit in 13 of 20) was measured a
  different way and STANDS** — it is parsed from the search's own hit lines, which no truncation
  or prefix bug touched. Keep reading this block for that half only.
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
- 🔴 **NOT ruled out — THIS BULLET IS THE ERROR, and it is the one that cost the most.** It read:
  *"that the hits were merely unreported while still informing the work — the check is not 'was it
  mentioned' but 'was a hit DOCUMENT opened'."* **That is a definition being used to dismiss the
  case it defines away.** Narrowing the check to "opened" is exactly what made every yielding
  session invisible, because sessions do not open a recalled doc — they report it under step 5's
  `from handoff docs` label and write a pointer into their own handoff. **Both hand-verified
  yielding cases were "mentioned", never "opened".** A `Ruled out:` bullet that rules something out
  BY DEFINITION has ruled out nothing; it has only renamed the question. Retracted 2026-09-11.
- **Ruled out:** that the self-hits are an artifact of the fixtures or of one repo — the 38%
  spans 20 sessions across four repos, and the two 3-of-3 cases are different repos.
  via: measurement
- **Leading hypothesis:** yield is low because the retrieval budget is spent on the one
  document guaranteed to be redundant. Excluding it frees a third of the slots — and the top
  slot two times in three — for documents the session has not read.
- **Next probe:** re-run the yield read after `--exclude-slug` has been live for ~20 further
  queries. The prediction is a rise off 1/20; the falsifier is that yield stays ~1/20 with
  foreign hits in every slot, which would mean the corpus itself has little to offer a
  resuming session and the honest response is to stop paying for the step.
- 🔴 **THE INSTRUMENT HAD TO BE REBUILT MID-MEASUREMENT, and the tell was a perfect number.**
  The first self-hit pass scanned transcripts for `claudedocs/handoff-<slug>.md` and reported
  **100%** — because the search's own output prints each hit's doc path, so the needle was
  matching the instrument. `claude/RULES.md` → "validate the INSTRUMENT before you read its
  verdict". The 38% is rebuilt from two sources the search cannot write: `resume-state.sh`'s
  `handoff:` line and `claim-work --slug-for` arguments. **A 100% or a 0% is a reason to
  re-check the needle before writing it down.**
- **Residual, NOT measured:** whether the freed slots are USED — this closes the "the top hit
  is the doc you already read" defect, and says nothing about whether the corpus's second and
  third choices are worth reading. That is the next probe above, and it is the real question.

## Next steps (ranked)
🔴 **NUMBERING IS STABLE — the rank is half a live claim's identity.** Closed items keep their numbers.

1. ✅ **CLOSED — `#1637` (`67295ffba`).**
   forcing: none
2. ✅ **CLOSED — `ship.sh` already falls back to nebula (`#1439`).** Do not re-open from a kickoff
   block predating the retraction.
   forcing: none
3. **The label/derivation granularity residual** — `scripts/lib/handoff_index.py`,
   `rebuild_delete_labels`. Tripwire `test_through_main_the_residual_is_recorded_and_the_report_is_true`
   fails the day someone does it.
   forcing: none
4. **The empty-label display residual** — an empty label renders blank on FOUR surfaces. 🔴 Fix at
   `main` as an input rejection (`RC_USAGE`), NEVER in the renderers.
   forcing: none
5. **The library layer still accepts `exclude=[""]`** — `_exclusion_list` in `handoff_index.py`.
   `#1637` shut the only argparse door and named this rather than widening into it.
   **Closing condition:** a merged PR rejecting a value deriving no slug at the library layer.
   forcing: none
6. ✅ **CLOSED — `#1791` (`d8362875`).** Archived-doc exclusion now works; three-arm control
   re-run live (no exclusion 449 / basename 449 no-op / qualified 448).
   forcing: none
7. ✅ **CLOSED — `#1670`.** The index writer no longer defaults to the frozen mirror.
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

- 🔴 **THIS METRIC WAS WRONG TWICE, IN OPPOSITE DIRECTIONS, AND THE INSTRUMENT WAS THE CAUSE BOTH
  TIMES.** `1 of 20`, then `0 of 16`, then — with the needle fixed — 32% and 53% on the same two
  windows. Nothing about the system changed between readings; only the reader did. **When a
  measurement drives a RETIREMENT decision, the instrument deserves the same adversarial pass as
  the code**: the PR that retired the step was written, tested, mutation-verified, audited and
  merged-ready before anyone checked whether the number underneath it was real.
- 🔴 **THE CLAIM WAS STATED WIDER THAN THE INSTRUMENT, AND THE GAP IS WHERE THE TRUTH LIVED.** The
  method step read *"was a hit DOCUMENT **opened**"*; the sentence everyone quoted read *"opened,
  **mentioned** or acted on"*. Sessions do not OPEN a recalled doc — they report it under step 5's
  `from handoff docs` label and write a pointer into their own handoff. **Every single yielding
  case lived in the two verbs the instrument did not implement.** `claude/RULES.md` → "a guard's
  DESCRIPTION claims COVERAGE — check the implementation is as wide as the sentence." **Read your
  own claim back and underline each verb; then point at the line that implements it.**
- 🔴 **A TRANSCRIPT READER THAT TRUNCATES IS A SILENT FILTER ON WHAT YOU CAN CONCLUDE.** Three
  flaws compounded: a needle requiring a `handoff-` prefix the reports never use (they write
  `<repo>/<slug>`); assistant text cut at 400 chars while the mentions sat at offset ~3,100; and
  `Write`/`Edit` captured by `file_path` only, so a hit written INTO a doc was invisible. Each
  looks like a reasonable budget. Together they made a 53% read as 0%. **Never truncate the
  haystack in a pass whose output is a count — truncate the DISPLAY, search the whole record.**
- 🔴 **A ZERO AND A PERFECT SCORE ARE THE SAME TELL, AND THIS DOC ALREADY SAID SO.** It carried
  "A PERFECT 100% (OR 0%) IS A REASON TO SUSPECT THE NEEDLE" — written after the self-hit needle
  matched its own instrument — and then a `0 of 16` was written down, shipped into a skill, a
  README, a commit message and a test's failure message without that rule firing. **A lesson in
  the same document is not a control.** The positive control that DID run covered the self-hit
  half (37% pre-fix, reproducing 38%) and I treated it as validating the whole read; it validated
  one half. **A control validates the needle it was run on, and nothing else.**
- 🔴 **THE CO-LOCATED COMPARISON CUTS BOTH WAYS, AND ONE INSTANCE IS NOT A DIRECTION.** `d775cf17`
  (cairn found the fsync doc, the search ranked it 11th) became "cairn is the surviving surface and
  it is the one that delivered". `5320d1bf` is the exact mirror: it recorded `cairn recall` as
  returning nothing relevant and the handoff-corpus hit as the useful one. **Two instances, opposite
  directions — which is no direction at all.** The first was generalised because it agreed with the
  conclusion already forming.
- ⚠ **AN AUDIT CAUGHT THIS, AND ONLY BECAUSE IT RE-DERIVED THE HEADLINE RATHER THAN CHECKING THE
  DIFF.** Both rounds found the same counterexample independently, in a PR whose code was correct
  and whose tests passed. **The finding was not in the diff at all** — it was in the sentence the
  diff existed to act on. When a change's whole justification is one number, audit the NUMBER.

- 🔴 **A RETRACTION IS NOT DONE WHEN THE DOC IS FIXED — THE STORE IS A SECOND COPY, AND IT IS THE
  ONE `/resume` READS FIRST.** `#1541` retracted `1 of 20` here on 2026-09-11; the cairn
  `devrc/handoff-index` entry went on serving it as a `RESOLVED:` bullet until 2026-09-12, alongside
  a `## Pointers` line about step 3 that `#1332` had falsified six days earlier. **Same shape this
  doc already records** ("a `RESOLVED:` bullet does not retire an `OPEN:` one"), committed by the
  session that wrote it down. **Grep the store for any figure you retract, in the same turn.**
- 🔴 **A SHARED CLONE ON SOMEONE ELSE'S BRANCH IS THE NORMAL STATE, NOT AN ANOMALY — CHECK BEFORE
  `/handoff`.** Measured 2026-09-12: `$DEVRC` sat on `fix/tmux-osc8-hyperlinks` (another session's),
  and `handoff_doc.py --confirm --push` commits to **whatever branch the checkout is on**. This
  handoff was landed from `git worktree add <path> main` instead. The skill says this; the thing
  that makes it bite is that the branch is not yours and nothing announces the switch.
- ⚠ **`ship.sh` rc 19 / rc 11 on a BUSY repo are races, not defects.** `main` moved three times
  inside one run; each host landed internally-consistent on a different commit and every per-host
  line was green. The documented fix (re-run) worked both times. 🔴 **And a backgrounded `ship.sh`
  reported `exit code 0` while its own `SHIP_RC` was 19** — read the run's own status line, never
  the wrapper's.
- 🔴 **WHEN A LEDGER KEEPS NEEDING NEW ENTRIES, DELETE THE COUPLING — an external worked example
  landed mid-session.** `test_every_kill_server_call_site_in_the_repo_is_classified` reddened `main`
  four times in two hours (`0b5ee924` → `6395ce1f` → `b62d1bf1`), each fix adding another
  `claudedocs/` file to a ledger, one of them *"a doc quoting this scanner's own output"* and
  another *"my own merged handoff became the sixth offender"*. `c0bbd6d9` (#1561) stopped the
  scanner reading `claudedocs/` at all and it has not recurred. **Same move that closed rank 1
  here** — stop the mechanism reading prose rather than keep annotating the prose.
- ⚠ **A stale observation reported three times.** This session reported that kill-ledger red as an
  open loose end in three consecutive replies; it had been fixed by `#1561` before the first. It was
  measured once, in a worktree pinned to an older `main`, and never re-checked. **Re-verify at the
  moment you ACT, not when you formed the plan** — and a "loose end" you are handing over is an act.

- 🔴 **A WORKAROUND APPLIED ON A DOC'S AUTHORITY IS NOT EVIDENCE FOR THE DOC — I fabricated a
  "strengthened" finding this way and pushed it.** Rank 2 said `ship.sh` could not reach the laptop
  off-LAN, so I passed `REMOTE_SSH=zach@10.42.0.100` on four consecutive runs and then wrote
  *"EVIDENCE STRENGTHENED — hit on FOUR consecutive runs"* into this doc. **I never once ran it
  without the workaround.** The fallback had shipped three days earlier in `#1439`, and one run
  without `REMOTE_SSH` prints it firing. The tell was available and ignored: `drift-check.sh`
  printed its OWN successful fallback in the same session, which I wrote up as *"the two sibling
  scripts disagree"* rather than as *"maybe mine works too."* 🔴 **Before citing a ranked item as
  confirmed, run the thing ONCE without your workaround** — and when a doc predicts a failure you
  never saw, that is the doc talking to itself.
- 🔴 **A GREP OF THE WRONG FILE IS A CONFIDENT ZERO.** `git show origin/main:scripts/ship.sh |
  grep -c LAPTOP_IP_SECONDARY` returns **0** and reads as "the fallback is absent". It lives in
  `scripts/lib/host-role.sh`, which `ship.sh` sources. **Grep the DEPENDENCY, not just the entry
  point** — and this doc's own closing condition named `host-role.sh` explicitly, so the answer was
  already written down.
- ⚠ **A branch name colliding on `git worktree add -b` is a FINDING, not an obstacle.**
  `fix/ship-nebula-fallback` already existed — 5 commits, unpushed, from the PR that had already
  merged. The collision is what surfaced that the work was done; treating it as a naming nuisance
  and picking `-2` would have hidden it and produced a duplicate implementation.

- 🔴 **A RETRACTION INSIDE A DOC DOES NOT REACH THE KICKOFF BLOCK THAT POINTS AT IT.** This doc
  retracted the rank-2 "four consecutive runs" evidence on 2026-09-12, in the same session that
  fabricated it. The kickoff block generated alongside that write still asserted rank 2 was the
  highest-value next action and restated the retracted evidence as fact — and that is what opened
  the next session. **The kickoff is a SEPARATE ARTIFACT, generated once, and nothing re-derives it
  when the doc is corrected.** The doc was right and the instruction pointing at it was wrong.
  Generalises past this arc: **any summary written from a document is a fork of it, and only the
  document gets fixed.** The cheap defence is the one that worked here — the session read the doc
  before acting on the kickoff and the contradiction was visible in the first two minutes.
- 🔴 **THE FAILURE MODE COMPOUNDS: a fabricated finding survived into a THIRD artifact.** The
  original error was reporting a workaround back as confirmation ("the doc talking to itself"). It
  was caught and retracted in the doc — but by then it had been copied into a kickoff block, where
  it was still live a day later. **Retracting a claim means sweeping every copy of it, and a
  retraction that names only the doc has swept one.** Same shape this doc already records for the
  cairn store carrying `1 of 20` for a day after the doc was fixed — **third instance, third
  surface.**
- 🔴 **`| tail` ATE AN EXIT CODE IN THIS SESSION, ON THE ONE COMMAND THE WHOLE VERIFICATION TURNED
  ON.** The first read of `--exclude-slug "  "` was piped to `tail -5` and reported `rc=0`, which
  reads as "the fix does not work". Three unpiped checks beside it returned 2 correctly. This trap
  is documented in `CLAUDE.md` and in this doc's own history and it still landed. **When rc IS the
  assertion, redirect to /dev/null and echo `$?` — never pipe.** ⚠ zsh's `$PIPESTATUS` is also
  wrong here: it is `$pipestatus[1]`, and the bash spelling silently returns empty.
- 🔴 **AN AGENT'S MUTATION RESULTS WERE RE-RUN AND HELD — BUT THE RE-RUN IS WHAT MAKES THAT
  SAYABLE.** `claude/RULES.md` mandates re-verifying a self-reported mutation sweep. Two of the five
  claimed mutants were re-applied independently from a `cp -a` copy with `.git` removed; both died
  on this guard's own rc-2 assertions, matching the report. **The value was not catching a lie — it
  was that "killed by THIS guard's specific error" is now a first-hand claim rather than a quoted
  one.** A green re-run is the expected outcome and is still worth its cost.
- ✅ **ARCHIVED HANDOFF DOCS ARE STILL INDEXED — the corpus did NOT shrink when `#1627` landed.**
  The risk was flagged before checking (the index globs `claudedocs/handoff-*.md`, and 35 docs had
  just moved under `claudedocs/archive/`). Measured: `handoff_index.py` walks subdirectories and
  slugs them WITH the subdirectory — a hit comes back as `devrc/archive/<topic>`. **The consequence
  is a slug change, not a coverage loss**, and it is rank 6: anything holding a bare old slug now
  names one that does not resolve. **Flagging a risk is not measuring it; this one took one command
  and was benign.**
- ⚠ **The base clone was `behind 1` at handoff time and `origin/main` moved again during the
  session** (`30bad70c` → `db548c47`). This doc was authored from a fresh worktree off `origin/main`
  because the base clone sits on `main`, which this repo forbids committing to — the same route this
  doc's own gotcha recorded on 2026-09-12. **`main` is busy enough that a base-clone read is stale
  within minutes**; `handoff_doc.py` resolves its base from the working tree, so authoring from a
  stale clone would merge into an out-of-date document and report success.

- 🔴 **SHRINKING A DOC CAN REDDEN `main` — the grandfather ledger is a RATCHET, and going UNDER is a
  failure exactly like going OVER.** `test_no_handoff_doc_exceeds_its_budget` caps every
  `claudedocs/**/handoff-*.md` at once; a doc listed in the ledger that now FITS must have its entry
  DELETED, and until someone does, the suite is red for everybody. Measured 2026-09-13: this doc
  dropped 67,076 → 59,805 B in a routine `/handoff` update and the gate went red on a **second**
  finding nobody had touched. **Two consequences: the gate fails on a file you did not edit** (it is
  fleet-wide, not per-doc), **and a size REDUCTION is a ledger event.** Check the gate after any
  `State now` replace that cuts a lot.
- 🔴 **AN INHERITED RED IS PROVABLE, AND THE PROOF IS CHEAP — `main`'s own status, plus ancestry of
  the fix.** Both PRs showed `devrc-pytests` red. The discriminator was not re-reading the diff: it
  was `gh api /commits/<main-sha>/statuses`, which showed **`main` red on the same two tests**, and
  `git merge-base --is-ancestor <fix> <branch>`, which showed both branches LACKED the fixes (7-8
  commits behind). Merging `origin/main` in turned one test green and left a failure naming a file
  neither diff touches. **Three commands beat any amount of staring at your own change** — and the
  commit subjects on `main` said it outright (`fix(clawgate skill): main is RED on the size ceiling`).
- 🔴 **I CALLED TWO FAILURES "MINE" BEFORE CHECKING, AND BOTH WERE INHERITED.** The failing test
  names *looked* reachable — one mentioned a skill and I had edited a skill; the other mentioned
  handoff-doc size and I had edited a handoff doc. Both inferences were wrong: the first guards the
  **clawgate** skill (which the diff never touches) and my doc had SHRUNK. **A test name that sounds
  like your change is not evidence it is your change** — map the test to the file it actually asserts
  on before claiming it.
- 🔴 **A VERIFICATION CAN BE RIGHT BY LUCK — I confirmed "no byte ceiling on `resume/SKILL.md`" with
  the grep `CLAUDE.md` explicitly warns is incomplete** (`MIN_HEADROOM|st_size <=`, documented there
  as "NOT provably complete", wrong three times before). The conclusion survived — the growth test
  guards clawgate, not resume — but the method could not have established it, and what made me
  re-check was a CI failure, not the verification. **When a file's own docs say the discovery method
  is incomplete, a clean result from it is not a measurement.**
- ⚠ **`gh pr merge` printed NOTHING on success — no sha, no confirmation, no error.** Silence is not
  a verdict either way; the merge state came from a separate `gh pr view --json state,mergeCommit`,
  and the CONTENT check on `origin/main` is what actually proved it landed.
- 🔴 **A `claudedocs/` doc is normally an eviction SINK and `prune-skill` says never to prune one** —
  pruning a sink deletes what a previous prune put there. This one was the exception because it is
  **gated by a hard ceiling** and was reddening `main`. Stated because it was a knowing departure
  from a rule, not an oversight: **ask whether the doc is GATED before applying the sink rule.**
- 🔴 **TRIMMING A DOC IS A PUBLIC-REPO EVENT, because slicing is how client identifiers PROPAGATE.**
  `handoff-index-store-claims-accuracy.md` already contains a client path and real hostnames; a
  verbatim line-range slice into a NEW file would have copied them into a fresh public artifact and
  no reviewer would have looked twice at a "pure move". The new `claudedocs/refs/` file was scanned
  independently (0 hits across seven identifier patterns) and the repo's three content gates re-run.
  **Scan what you slice, not just what you write.**
- 🔴 **VERBATIM LINE-RANGE SLICING MAKES CONTENT SURVIVAL STRUCTURAL — and the gap audit is what
  proves it.** `#1650` was checked by an independent line-level accounting: **892 of 893** original
  non-blank lines byte-identical in the doc or the refs file, with the single miss being a declared
  ranked-list renumber. The loss mode this catches is the one slicing does NOT: a block *summarised*
  into the core and sliced into no sidecar is silently gone **and looks like good pruning**.

- 🔴 **THIRD RECURRENCE, BY THE SESSION THAT RECORDED IT: ranks 6 and 7 shipped and this doc went on
  calling them OPEN for days.** The doc already carries *"a RESOLVED bullet does not retire an OPEN
  one"* and *"the status header is the part that goes stale and the part nobody sweeps."* Both were
  written here, by me, and then re-committed. **The mechanism is that shipping and re-heading are
  two edits and only the first feels like progress** — merging a PR gives every completion signal
  (green, merged, claim released) while the ranked list, which is what the NEXT session reads, is
  untouched. **Retire the rank in the same turn as the merge, not at handoff time.**
- 🔴 **AN EXIT CODE WAS DESTROYED BY THE THING PRINTING IT — FOUR TIMES IN ONE SESSION, EVERY ONE
  MINE, NONE IN THE CODE.** (1) `| tail -5` made a working rc-2 guard read `rc=0`. (2) A
  `$(printf …)` substitution *inside* the `echo` reset `$?`, making a healthy fix read as a total
  regression. (3) `deadman.py | tail` reported `rc=0` when it was **rc=1 with 3 dead sources**.
  (4) `git merge --ff-only … | tail -1` showed `Updating a..b` while the merge had **ABORTED** —
  git prints that line AFTER the error, so the tail captured the reassuring half.
  🔴 **The pattern is not "remember not to pipe": every one of these was a probe I wrote to CHECK
  something, and each failed in the reassuring direction.** Capture `rc=$?` on its own line, and
  when a probe's answer is suspiciously clean, suspect the probe.
- 🔴 **A GREP OF A GITIGNORE-BLIND WRAPPER UNDERCOUNTED 3×, AND THE MENTION/INVOCATION TRAP
  OVERCOUNTED ON THE SAME QUESTION.** Asking "has the new rc-2 guard fired in real sessions?", the
  `grep` wrapper (ugrep, `--ignore-files`) found **2** where GNU grep found **6** — and all six were
  *mentions* (the source, the help text and this doc all contain the phrase), while the real answer
  was **0 firings**. Two opposite errors stacked on one question. **Match a `tool_result`, not a
  file; and use `command grep -r` when a zero matters.**
- 🔴 **`--ff-only` REFUSED CORRECTLY AND THAT WAS THE POINT — another session's WIP was in the base
  clone.** `nix/pkgs/lang/default.nix` modified plus three untracked `claudedocs/` files, none mine.
  The documented remedy is exactly right: **do not stash** (repo-global, reaches into their work) —
  verify from a clean worktree off `origin/main` instead. **A "live" probe against the base clone is
  a claim about a tree someone else is editing.**
- 🔴 **I NEARLY REPORTED A SHIPPED FIX AS BROKEN because I probed a stale working tree.** After
  merging `#1791` the base clone still answered with OLD behaviour (control 5 hits, not 0). Not a
  regression — `HEAD` was `bb15bcad`, the ff-merge had aborted, and the file on disk lacked the fix.
  **Checking by CONTENT (`git show origin/main:<path> | grep -c`) is what separated "the merge did
  not land" from "the fix does not work."** Merged ≠ present in your tree.
- ⚠ **A measured magnitude retired a fix I was about to build.** I proposed teaching
  `deadman.PRESENCE_SOURCES` to discriminate SSH-driven presence. Another session's `#1785` had
  already found the sharper version (`i3` is indirectly agent-drivable via the bridge's `wake`,
  which raises a window) **and measured it: 352 of 2,251 buckets co-occur with a bridge command, but
  only 10 lack a `keys` row — mechanism real, magnitude 10 buckets in 30 days.** I did not dispatch.
  **Re-verifying before acting is what caught it; the queue is shared and three days had passed.**
- 🔴 **MY OWN MEASUREMENT COUNTED ITSELF.** Reporting "21 of 21 queries passed `--exclude-slug`", the
  scan matched the heredoc I was measuring WITH — it contained the literal flag. Real population was
  22 across 22 sessions. **Same family as the two instrument bugs this doc already records: the tool
  under measurement emitting the exact string being counted.**
- ⚠ **`handoff_doc.py` merged into a STALE base once here** and the durable-drop warning is a FLOOR,
  not a detector: it counts base lines under a REPLACE heading and cannot see that a delta restates
  them. Carrying the standing results forward by hand is what keeps them; the warning will fire
  either way.
- 🔴 **DEMOTING A CLOSED BLOCK CAN ORPHAN A GOTCHA THAT CITES IT, AND A LINE-LEVEL GAP AUDIT SCORES
  THAT 694/694.** The four `Open investigations` blocks demoted on 2026-09-19 include the two this
  doc's own *"TWO OF THIS DOC'S OWN `Open investigations` BLOCKS WERE ANSWERED WEEKS AGO"* bullet
  uses as its worked example — down to the literal *"16 and 55 lines below them"*, a distance that
  no longer measures anything here. Content survival, resolving pointers and a green gate can all
  hold while the PAIRING breaks. **Ask what each demoted block was PROTECTING, not just whether its
  bytes survived**: the evidence is in `claudedocs/refs/handoff-search-index-closed-investigations.md`
  and is named from both ends.

## How to verify
```bash
# RANK 1 — the fix, end to end. rc is the whole assertion; do NOT pipe (| tail eats the status).
W=<worktree-or-repo>
python3 $W/scripts/lib/handoff_search.py --offline --query x --limit 3 --exclude-slug "  " >/dev/null 2>&1; echo "rc=$?"   # 2
python3 $W/scripts/lib/handoff_search.py --offline --query x --limit 3 --exclude-slug ""   >/dev/null 2>&1; echo "rc=$?"   # 2
python3 $W/scripts/lib/handoff_search.py --offline --query x --limit 3 --exclude-slug claudedocs >/dev/null 2>&1; echo "rc=$?"  # 2 — the NON-blank empty-slug road
python3 $W/scripts/lib/handoff_search.py --offline --query "handoff search index" --limit 3 \
  --exclude-slug handoff-handoff-search-index.md 2>&1 | grep -E 'excluded=|indexed_docs'
#   expect rc 0, `excluded=handoff-search-index`, and in_scope_docs LOWER than indexed_docs.
#   🔴 Read the PAIR: `excluded=` proves the flag PARSED, the COUNT proves it MATCHED.

# RANK 1 — the guard is not walkable. Re-run the mutants rather than trusting a report.
#   Copy the tree, `rm -f <copy>/.git` (a worktree's .git is a FILE — a cp -a copy commits to the
#   REAL branch), clear __pycache__ between mutants, PYTHONDONTWRITEBYTECODE=1.
#   predicate -> `not v.strip()`  MUST die on `'claudedocs' exited 0` / assert 0 == 2
#   `if unusable:` -> `if False`  MUST die on rc 2, three assertions
nix develop ~/workspace/devrc -c env PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  scripts/tests/test_handoff_index.py -q -p no:cacheprovider \
  -k TestABlankExclusionIsRefusedAtTheINPUT          # control: 4 passed

# RANK 2 — the closure, checked at the DEPENDENCY. A grep of ship.sh returns 0 and reads as absent.
grep -n LAPTOP_IP_SECONDARY ~/workspace/devrc/scripts/lib/host-role.sh   # the fallback lives HERE
#   And the only real evidence: run `scripts/ship.sh` with NO `REMOTE_SSH` and watch it print
#   `did not answer — falling back to …`. A run WITH the workaround proves nothing about the gap.

# RANK 6 — archived docs are still indexed, under an `archive/` slug:
python3 ~/workspace/devrc/scripts/lib/handoff_search.py --offline --query "browser bridge extension" --limit 6 \
  2>&1 | grep -E '^──|indexed_docs'
#   expect a hit spelled `devrc/archive/<topic>` — the prefix IS the finding.
```
## Defects (batched)
🔴 **Findings from the 2026-09-16 corpus-mechanism evaluation. Fix as ONE round — an audit finding
is a DEFECT, not a rank.** The evaluation deliberately did NOT measure yield.
- **`ts_rank` carries the SAME length bias the offline ranker just lost.** `#1791` fixed only the
  memory store; `ts_rank(tsv, q, 0)` (the default flag) applies no length normalisation. The fix is
  `ts_rank(tsv, q, 2)`. **Deliberately NOT taken:** nothing in this repo can exercise the indexed
  backend (its only consumer runs `--offline`; no test reaches a live DB), so it would be a ranking
  change nobody could watch work. **Blocked on a way to measure the Postgres path, not on effort.**
- **21 of 22 real runs destroy the exit code** with `2>&1 | head -N`, so `$?` is `head`'s. The
  skill's *"read the rc, not the prose"* is unsatisfiable as written in 95% of runs. No clean
  tool-side fix exists — the rc is destroyed by the pipeline. Absorbed today only because the
  renderer's statuses share no opening phrase, i.e. the tool defending against its caller.
- **A bare slug that itself starts `handoff-` normalises wrong.** `exclusion_slug('handoff-search-index')`
  → `'search-index'`, filtering nothing at rc 0, because the stripper cannot tell a slug from a
  basename. Bites only docs named `handoff-handoff-*` (this one is). The skill says to pass step 2's
  printed value, which is correct — so narrow, but it is the silent-no-op class again.
- **The non-blocking failure contract is UNEXERCISED.** All 22 post-merge runs returned `hit` at
  rc 0 — zero `NO MATCH`, zero error rcs. It cannot be called working; no session has had to.
  Adjacent negative signal: the 2 sessions whose exclusion no-opped were told to check
  `in_scope_docs < indexed_docs` and neither did.
- **Coverage boundary — TAKEN in `#1791`.** The scope line now names the repos the corpus covers on
  every status (`repos=civitai,datapacket-talos,devrc,homelab-talos`), with a negative-control test.
  2 of 22 runs had queried from a repo outside `REPO_ENV_HANDLES` and nothing said so.
