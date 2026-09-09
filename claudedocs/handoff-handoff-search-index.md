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
🔴 **THE MACHINERY IS LIVE, ADOPTION HELD AT n=22, AND YIELD IS NOW ANSWERED: 1 of 20.** All
three are separate claims and each was measured on its own. **Adoption 20 of 22 (91%)**
post-fix on the workbench, against 2 of 11 (18%) before — the preliminary 4/4 was not a
small-n fluke. **Yield: exactly one of those 20 sessions had a hit change what it did**, and
the cause of the other 19 is now measured rather than guessed — see the block below and rank 1.

- **Merged:** `devrc#1209` (`45930d644`) index · `#1244` (`1b769b64b`) cairn I/O-stall classifier ·
  `#1264` (`baa95854`) this doc · `#1267` (`d86b4e45`) incomplete-read delete authority ·
  `#1295` (`3e7d79a4`) the `/resume` consumer · `#1307` (`bb6e46ee`) ARM the timer ·
  `#1329` (`4ffb0cc6`) the adoption measurement · `#1332` (`8e9428ef`) the rank-1 fix.
  Plus `homelab-infra` `d2c9c49a` — the rescued untracked handoff doc.
- **Deployed and verified 2026-09-06 (`#1332`).** `ship.sh`: "converged + verified — 2 hosts
  compared, both at `8e9428ef`". Both hosts' `readlink -f ~/.claude/skills/resume/SKILL.md`
  resolve to the SAME new store path
  (`/nix/store/sv3b3ylby1dc3wy8arhp0xmgg3dwpsva-devrc-claude-skills/resume/SKILL.md`) and the
  deployed copy carries the query and the scope warning. Merged, deployed and live are three
  claims; all three were checked. Supersedes the 2026-09-04 convergence at `bb6e46ee` (`#1307`),
  which was verified the same way and is simply older.
- 🔴 **The live-Postgres path is EXERCISED.** `--rebuild --write` → `wrote 4647 section row(s) …
  (after DELETE of 4 repo label(s) — one transaction)`. The `GENERATED … STORED` `tsv` column was
  accepted and the GIN index built for the first time; both had only ever been pinned as SQL text.
- 🔴 **The TIMER has run on its own**, which is the only thing that tests the unit's environment:
  `Result=success ExecMainStatus=0`, `wrote 4651 section row(s)`, `warnings: none`, 21 s. Read
  from `journalctl --user -u handoff-index-sync.service`, not inferred from the flag.
- **Query path live:** `backend=postgres`, `indexed_sections=4651`. 🔴 `backend=` IS the
  discriminator; a silent fall-back to `memory` renders identically otherwise.
- 🔴 **ADOPTION MEASURED POST-FIX, 2026-09-07T02:25Z — 8.6 h after `#1332` merged.**
  **Workbench: 4 of 4** `/resume` runs invoked `handoff_search.py` (100%), matching the
  co-located `cairn recall` control at 4/4. Pre-fix on the same host: **2 of 11 (18%)**.
  🔴 **The pre-fix headline this replaces, kept because it is the baseline: 1 of 14 runs across
  BOTH hosts, and that one fired off the staleness alarm rather than the step** — full evidence
  in "Open investigations". The 2/11 here is a wider window on ONE host and includes the
  measuring session, so treat 18% as an upper bound on the old rate, not a restatement of 1/14.
  All four are INDEPENDENT sessions in `datapacket-talos`; none is the measuring session — that
  contamination was checked for, because it distorted the first measurement.
  All four queried a TOPIC, which is what the re-keying was for.
  ⚠ **n=4, not the ~10 this doc asked for**, and the **laptop ran 0 resumes**, so this is a
  workbench-only reading over a third of the pre-fix window.
- 🔴 **YIELD IS ANSWERED — 1 of 20, AND THE CAUSE OF THE OTHER 19 IS MEASURED.** Adoption says
  the command RAN; this says what the answer was worth. **23 of 60 hit slots were the
  session's OWN handoff** — the doc it had just read in step 3 — and it was the **#1 hit in
  13 of 20** queries. Fix shipped in `#1399` (`--exclude-slug`). Full block below.
  ⚠ The 60 is the SUM OF HIT LINES ACTUALLY PARSED, not 20×3 assumed — every query used
  `--limit 3` and the corpus returned three each time, so the two happen to coincide.
- 🔴 **ADOPTION RE-MEASURED 2026-09-08T17:00Z at n=22 — 20 of 22 (91%), control 21/22.** The
  laptop still contributes **0 runs**, measured over SSH rather than assumed, so this remains
  a workbench-only reading. The measuring session is excluded from both halves by session id.
- **Claim `handoff-search-index-1`: RELEASED** (`claim-work: RELEASED refs/heads/claim/…`), after
  `#1332` merged. Merging does not release a claim; this one was released by hand.

## Open investigations — live diagnosis state

### A repo that MEASURES but whose every doc is unreadable has its rows deleted, rc 0, no PARTIAL notice
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

### The same hazard has now appeared in FOUR spellings — the shape, not the instances, is the open question
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
1. **RE-MEASURE YIELD ONCE `--exclude-slug` HAS ~20 QUERIES BEHIND IT.** Rank 1 as it stood is
   ANSWERED (1 of 20) and the structural cause is fixed, not diagnosed-and-filed — do NOT re-run
   the old read as if it were open. Wait for the queries, then repeat the yield read exactly as
   the RESOLVED block describes it, and report the pair: yield, and how many hit slots were
   still the session's own doc (should be ~0; if it is not, the skill wiring is not being
   followed and that is an ADOPTION finding, not a yield one). 🔴 **This is the question the
   whole effort rides on** — an index queried by every session and never useful is a cost, not
   a capability, and adoption cannot distinguish the two. ⚠ **The falsifier is real and should
   be honoured:** if yield stays ~1/20 with foreign hits filling every slot, the corpus has
   little to offer a resuming session, and the right response is to stop paying for the step
   rather than to tune the ranker again.
   forcing: none
2. **The label/derivation granularity residual** — `scripts/lib/handoff_index.py`,
   `rebuild_delete_labels`. Its own round, because the fix moves the delete scope. The tripwire
   test `test_through_main_the_residual_is_recorded_and_the_report_is_true` fails the day someone
   does it, which is the intended signal.
   forcing: none
3. **The empty-label display residual** — an empty label renders blank on FOUR surfaces; the
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
  became completely inert, the excluded doc came back as hit #1, and **304 of 304 tests passed.**
  The pre-existing "🔴 THE SEAM" guard proves only that argparse ACCEPTS the flag — it passes
  `--limit 0`, which returns rc 2 *before any store is built*. `claude/RULES.md` → "verified in
  isolation is the new vacuous green" and "a count of DECLARATIONS is not a count of INSTANCES".
  **Ask which line makes the feature REACH the code you tested, and pin that line** — the two
  call sites are now covered behaviourally (offline) and by an AST ledger that fails if a call
  site is added without `exclude=` as well as if one is removed.
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

## How to verify
```bash
# 1. ADOPTION — the live question. THIS HOST only; run on both.
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

# 4. The consumer is LIVE, not merely merged (readlink is the arbiter):
readlink -f ~/.claude/skills/resume/SKILL.md          # must resolve into /nix/store
grep -c 'handoff_search.py --offline' ~/.claude/skills/resume/SKILL.md   # must be >=1
#    🔴 Proves the text is DEPLOYED. Says NOTHING about whether it is FOLLOWED — that is check 1.

# 5. The guard still holds (and its own mutants still die):
nix develop ~/workspace/devrc -c python3 -m pytest \
  ~/workspace/devrc/scripts/tests/test_resume_handoff_search_wiring.py -q -p no:cacheprovider
```
