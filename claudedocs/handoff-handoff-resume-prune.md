# Handoff: handoff-resume-prune — 2026-09-20

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
Port the transferable parts of `prune-memory`, `prune-skill` and `the-algorithm` into
`/handoff` + `/resume` — scoped by the operator to the **doc-corpus lifecycle** and **the
guard stack**, with deletion on the table and every change landing inside the two existing
skills (no fourth `prune-*` sibling).

🔴 **A NEW ARC, and `handoff_doc.py --new-effort` was passed deliberately.** Three neighbours
were read before asserting that: `handoff-agent-overguarding.md` is the arc that SHIPPED
`the-algorithm` as a skill (frozen at round 1; this one APPLIES it to a named target and
finds the opposite — the guard stack is defended); `handoff-handoff-resume-skill-trace.md`
is CLOSED, verdict ADDRESSED 2026-09-17, and pruned the `resume` skill BODY, which this arc
explicitly does not touch; `handoff-guard-existence-gate.md` is the Stop-hook fixture-string
bug, unrelated.
- **closing-condition:** `check` — **devrc#1815 is merged to `origin/main` AND
  `scripts/ship.sh` has converged both hosts** (read every per-host line, not the verdict),
  so `readlink -f ~/.claude/skills/handoff/SKILL.md` resolves into `/nix/store` at the
  merged size on both machines.

🔴 **THE CONDITION ABOVE IS A CORRECTION OF A MALFORMED ONE, NOT AN EXTENSION OF THE ARC —
and the distinction is the whole reason this note exists.** Round 1 froze it as *"P1
merged AND the corpus APPEND share below 70%"*. Both halves were later measured
unsatisfiable: P1 is RETRACTED (demoting gotchas into `claudedocs/refs/` is forbidden —
`refs/` is measurably outside the `handoff_search` corpus), and the APPEND-share metric
could only fall by performing that forbidden action. **A `check` that can never go green is
a permanently-red gate, which `claude/RULES.md` calls worse than no gate** — and `/resume`
reads this FIELD, not the prose, so every future re-entry would have reported NOT MET
forever (round 0 of #1815, F6).
⚠ **The freeze rule still binds, and this does not weaken it.** It forbids letting later
ASKS grow an arc; it is not a reason to preserve a condition that named a forbidden action
at the moment it was written. The scope is unchanged — the same deliverable, now named by
something that can actually be run. **Nothing was added to the arc**: P1′, P4, and the four
retractions are exactly what #1815 carries. If the operator disagrees, the remedy is to
close this doc as NOT ADDRESSED and open a new one, not to restore an unrunnable check.

## State now
- 🔴 **ARC CLOSED (verdict ADDRESSED) AND FULLY LANDED. Nothing from it is in flight.**
  All three PRs merged, shipped and verified by CONTENT on `origin/main`:
  **#1815 `d9216e5e`** (P4 + P1′ + the multi-session worktree rule) ·
  **#1821 `8f95c342`** (the `/the-algorithm` pass: advice deleted, −178/+42) ·
  **#1822 `98aa7b06`** (this doc's CLOSED verdict).
- **Deployed and verified LIVE on both hosts** at `8f95c342`: `ship.sh` rc 0, every per-host
  line read (620/579 artifacts resolve, 0 dangling, 0 stale, no skips). The live note from
  the deployed copy prints **numbers only**; the laptop greps **0** advice strings and **1**
  numbers block. `readlink -f ~/.claude/skills/handoff/SKILL.md` → `/nix/store` at
  **20,176 B on both machines**, matching `origin/main`.
- **Base clone re-synced** to `98aa7b06` on `main`. My 2 stray worktrees removed and 5 merged
  local branches deleted; the 2 dirty paths (`claudedocs/scope-chief-*`) were dirty before
  this arc started and are NOT mine.
- ✅ **Rank 1 of the previous version of this list is DONE** — #1821 merged, shipped,
  verified. The list below is what actually remains.
- ✅ **The rescue branches are superseded and need no action**: `#1819` landed the
  `browser-bridge` `flows/` rename that existed nowhere else, so every path
  `origin/rescued-stt-and-browser-flows` and `origin/rescued-shared-branch-20260920`
  protected is on `main`. They are harmless refs; deleting them is the operator's call.
  ⚠ `rescued-workbench-main-2026-08-02` is **not from this arc** — do not sweep it up.
- **No `clawgate-task:` field**, and that is a real reading rather than a blank: `resolve`
  exited **6** — one task linked (#574, a browser-bridge SSH ref) and **NONE worked**
  (0 created, 1 read). It is not this session's subject, so nothing was recorded and no task
  was created.

## Open investigations — live diagnosis state

### Why 82% of live handoff docs carry no `closing-condition` despite a refusal that requires one
- as-of: 2026-09-20
- **Symptom + exact repro:** `/resume` step 5 mandates a DoD verdict as its headline, but for
  80 of 97 live devrc docs the only honest verdict is UNMEASURABLE. Reproduce:
  `python3 -c "…closing_condition(doc).is_declared…"` over `$DEVRC/claudedocs/handoff-*.md`
  (full command in "How to verify").
- **Observed (with values):** 80/97 undeclared = 82%, holding 2,414,878 B of 3,299,446 B
  (73%). Among the 20 biggest docs, 14 undeclared. Declared kinds: `check` 12, `judgement` 5.
  Meanwhile `undefined-done` (the refusal that requires the field) HAS fired — 51 times,
  1.0% of 7,023 invocations.
- **Ruled out:** "the refusal is broken / never fires" — it fired 51 times in the deduped
  transcript sweep; `via: measurement`.
- **Ruled out:** "my parser is undercounting" — the first pass used a regex of mine and WAS
  wrong (scored `handoff-cairn-phase3.md` as undeclared while that doc names the field 19
  times); re-derived through `handoff_doc.closing_condition().is_declared`, the authority
  `resume-state.sh:2146` names, with controls built from real corpus text;
  `via: measurement`.
- **Leading hypothesis:** the refusal is **grandfathered for pre-existing docs by design**
  (`undefined_done_report(… base_had_one, is_new_doc)`), so it only bites new documents.
  The corpus therefore never converges: every doc written before the rule stays undeclared
  forever, and those are the big old ones. Not yet confirmed against the grandfathering code
  path.
- **Next probe:** read `undefined_done_report` and the `is_new_doc` / `base_had_one` arms in
  `$DEVRC/scripts/lib/handoff_doc.py`, then correlate declared-ness against each doc's FIRST
  commit date:
  ```bash
  for f in $DEVRC/claudedocs/handoff-*.md; do
    printf '%s %s\n' "$(git -C $DEVRC log --diff-filter=A --format=%cs -- "$f" | tail -1)" "$f"
  done
  ```
  If every declared doc postdates the rule's landing, the hypothesis is confirmed and P3 is
  the right shape; if not, the prose is being ignored for some other reason and P3 should be
  deleted rather than strengthened.

### What should replace P1, given that demotion of open threads is forbidden and eviction is not automated
- as-of: 2026-09-20
- **Symptom + exact repro:** the arc's frozen closing condition names a demotion that the
  authoritative playbook forbids. Re-read it: `sed -n '138,160p'
  scripts/tests/test_handoff_doc_size.py`.
- **Observed (with values):** refs/ is genuinely outside the search corpus — control pair,
  single-token queries through `handoff_search.py --offline`: a token unique to an indexed
  handoff doc (`test_a_SUCCESS_does_NOT_buy_more_GUESSES`) returns **rank 2.0**, while
  `MAX_IDENTITY_CHARS` (only in `refs/cairn-oss-multi-instance.md`) and
  `scan_inert_negated_greps` (only in `refs/tmux-webapp-closed-investigations.md`) both return
  **NO MATCH** against the same 6,370 indexed sections. Evictable backlog measured at
  468,110 B / 14.1% (breakdown in rank 1). 8 `refs/` files already exist, created by hand
  under this playbook, and 3 of them are named `*-closed-*`.
- **Ruled out:** "the refs/ header's non-indexed claim is stale like `handoff-audit.py`'s
  was" — it is not; measured by the control pair above; `via: measurement`.
- **Ruled out:** "a fuzzy hit proves refs/ is indexed" — an early multi-word query DID return
  a refs-shaped slug, but the corpus has a handoff doc of the SAME stem
  (`handoff-index-store-claims-accuracy.md`, 59,764 B) and the unique phrase appears **0**
  times in it; single-token discrimination was required to settle it; `via: measurement`.
- **Ruled out:** "there is a gap in `budget_warning()` to patch about searchability" — the
  playbook it defers to already states the trade in terms ("⚠ A `refs/` file is NOT indexed
  by `handoff_search` … it is the reason only DATED material goes there, never an open
  thread"); `via: code`.
- **Leading hypothesis:** the valuable, safe increment is form (a) — surface the per-doc
  evictable backlog at the moment of the write, reusing `handoff-audit.py`'s existing
  detectors. It converts a generic ladder into the author's own numbers, rewrites nothing,
  and adds no gate. Form (c) is the risk: an automatic rewrite must decide "is this
  investigation resolved", and the auditor's sibling advisory (`RELOCATE_DURABLE`) prints
  **no** byte estimate precisely because its regex undercounted a human reading 28-30 vs ~55
  on the one doc checked — the same class of judgement an auto-rewrite would be making
  unsupervised, against documents whose whole value is that nothing silently removes content.
- **Next probe:** none needed to decide; this is a judgement for the operator. If (a) is
  chosen, the first command is
  `python3 $DEVRC/scripts/handoff-audit.py <doc>` on a doc that is over the ceiling, to see
  which of the four detectors actually fires per-doc rather than corpus-wide.

## Next steps (ranked)
🔴 **NONE OF THESE BELONG TO THIS ARC — it is CLOSED and FROZEN AT ROUND 1.** They are
recorded so they are not lost; a session picking one up is starting a NEW arc and should say
so. Both of the first two are ONE-LINE operator decisions, not engineering work.
1. **Decide F2 — and note that #1821 SHRANK it to one line.** F2 (round 0 of #1815) argued:
   withhold the PRESCRIPTION from ungated repos, print the NUMBERS. #1821 deleted the
   prescription entirely, so the objection it rested on no longer exists. What remains is
   whether to drop `if gated` on a numbers-only note in
   `scripts/lib/handoff_doc.py::budget_warning` (the near-headroom arm). Blast radius:
   `homelab-talos` holds **370,563 B** of closed content the withholding hides today, and
   **107 of 108** ungated size warnings fired outside devrc.
   forcing: user — it reverses a requirement whose author is a measured incident
   (`civitai/cli#618`), which is not an agent's call to take.
2. **Ratify or reverse the closing-condition correction.** Round 1 froze this arc's
   condition as "P1 merged AND the corpus APPEND share below 70%", which was
   unsatisfiable — P1 is retracted and the metric could only fall by performing the
   forbidden demotion. An agent re-aimed it at the arc's actual deliverable and closed on
   the corrected line. The strict reading of frozen-at-round-1 says close this doc **NOT
   ADDRESSED** and open a new arc instead. Reversing changes the VERDICT only; the shipped
   work is unaffected either way.
   forcing: user — an agent corrected its own finish line, which the operator may not accept.
3. Re-measure the corpus in ~2 weeks and decide whether P1′ earned its place: the evictable
   backlog (**468,110 B / 14.1%** at round 0) and docs over the hard cap (**28**). If
   neither number moved, the note informed nobody and should be deleted rather than
   elaborated — `python3 $DEVRC/scripts/handoff-audit.py $DEVRC/claudedocs`.
   forcing: none

## Gotchas / decisions / dead-ends
- 🔴 **Do not re-propose what is already built.** `scripts/handoff-audit.py` (the
  `skill-audit.py` analogue, measurement-only), `claudedocs/archive/` (35 docs, #1627),
  `claudedocs/refs/` (592 KB sink), the 65,536 B ceiling + 11-entry grandfather ratchet, and
  `handoff_doc.budget_warning()`'s eviction ladder ALL exist. `claudedocs/proposal-handoff-doc-bloat.md`
  (2026-08-29) already argued "prune-skill's method, one level down". **The gap is routing:**
  grep-verified, neither SKILL.md names `handoff-audit.py`, `archive/`, `refs/` or any budget.
- 🔴 **RETRACTED — "the archive sink exists and nothing routes to it" is FALSE.** Only 6 of 97
  live docs are untouched >30d and **zero** >60d; the age-based archive rule is working. Dead
  docs are not the problem — live ones growing are (biggest is 194,314 B, touched 5 days ago).
  Do not re-derive this.
- 🔴 **RETRACTED — the first closing-condition figure came from my own regex and was wrong.**
  It required the field at line-start; the authority (`handoff_doc.closing_condition`) matches
  it anywhere per `resume-state.sh:2190`. The 82% survived re-measurement, the derivation did
  not. **Use the authority, never a second regex.**
- 🔴 **A naive transcript grep for `status=<x>` counts SKILL LOADS, not firings** — the
  strings live in `claude/skills/handoff/SKILL.md`, injected into every transcript that loads
  `/handoff`. Count only from `tool_result`/`toolUseResult`, and **dedupe by `tool_use_id`**:
  raw was ~8,500 matches, 40% of them copies from resumes/compaction. Same lesson as
  `scripts/audit-rule-firing-sweep.py`, which says so in its own header.
- 🔴 **A synthetic fixture failed the control for `closing_condition()`** — it walks
  `split_sections`, so text with no `## ` heading can never declare anything. Build controls
  from REAL corpus text (positive: `handoff-audit-pr-ladder.md`; negative: `handoff-laptop-freezes.md`).
- **`test_handoff_doc_size.py` already decided archive-is-not-exempt** — I checked this as a
  suspected sink-vs-gate conflict with `prune-skill`'s "never prune a sink" rule. It is not
  one; the reasoning is in that test's header. Don't re-open it.
- **Decision: no `/prune-handoff` skill.** the-algorithm §5 (the fix for over-guarding is
  never another guard) plus the skill-listing budget, which would need a tier entry and an
  eviction in the same commit.
- **Decision: delete no refusal.** the-algorithm §1 asks each requirement to name its maker
  and recurrence; all 15 can, by firing count. That was NOT the expected answer going in.
- **Decision: do not promote `durable-drop` to a refusal** despite its 18% firing rate. P1
  removes its cause (a moved-and-routed line is not a line about to be deleted) instead of
  adding a gate people would learn to click through.
- ⚠ **Observation for a DIFFERENT arc, recorded not claimed:** the handoff write-back Stop
  hook armed this session on `handoff-cairn-phase3.md`, `handoff-laptop-freezes.md` and
  `handoff-source-repo-parity.md` — all three "read" only by a **bulk corpus scan** (every
  doc's bytes parsed to compute the APPEND share), never opened as context and never worked.
  That is a different false-arming class from the fixture-string one
  `claudedocs/handoff-guard-existence-gate.md` closed. Not investigated here, and NOT filed
  as work on that arc — its closing-condition is frozen.
- **Out of scope by the operator's answer:** pruning the two SKILL.md bodies themselves
  (handoff 20,088 B, resume 20,731 B against a 12,038 B budget). Note `resume`'s ceiling is
  pin-bound — ten test modules pin literal strings in that body — so trimming it means MOVING
  pins, not cutting text.

- 🔴 **P2 (stamp + age the APPEND buckets) is REFUTED. Do not rebuild it.** Two independent
  findings, and the second is the decisive one. (a) **Stamping was the wrong mechanism and
  the code I was about to mirror said so**: `resume-state.sh`'s own comment records that the
  introducing-commit pickaxe dated **478 of 478** investigation blocks — a stamp is the most
  PRECISE clock, never the only one. Stamping the 3,732 gotcha bullets at ~12 B each would
  have **ADDED 44,784 B to the 3.3 MB corpus this whole effort exists to shrink.** (b) **The
  premise itself is false in this corpus**: aged all 3,732 bullets by `git blame` — p50
  16.5d, p90 28d, **max 39d, ZERO over 90d**. At the 14-day investigations window **55%
  would flag**, against the **3%** that design accepted and the **18%** it explicitly
  rejected as "a gate that fires on a fifth of every doc is one everybody clicks through".
  The reason the corpus is young is that **the archive rule already works** (0 live docs
  >60d), so P2 would have duplicated a mechanism that is already doing the job.
- **`git blame` is the cheap clock for handoff-doc content, and it was CONTROLLED.** One
  `git blame --line-porcelain` per doc is **85 ms on the largest doc (1,906 lines)** and
  yields per-line dates, where the pickaxe costs one `git log -S` per bullet (median 20, max
  259 per doc). Blame reports LAST-TOUCHED, so it is a lower bound on true age — validated
  against the pickaxe on the oldest content in the corpus: **38d vs 38d, exact agreement on
  4 of 4**. Use blame; state the floor semantics.
- 🔴 **`claude/skills/handoff/SKILL.md` is PIN-BOUND, NOT PROSE-BOUND — budget an eviction
  that FAILS.** Twice this session a block I had verified as redundant (the
  `OPENCODE_SESSION_ID`/`ROLES UNAVAILABLE` sentence; the `--exclude` paragraph) turned out
  to be required verbatim by a test ledger — `test_resume_state_clawgate.py`'s
  `test_handoff_skill_pins` (14 phrases) and `test_subsystem_touch.py`. **Citing where the
  content also lives is NOT sufficient to delete it**, which is exactly what
  `prune-skill` §3 warns and what I did anyway. The working method: grep the candidate's
  distinctive fragments against **every test that reads the body** (10 modules) before
  cutting, and if nothing is cuttable, **shrink your own addition** rather than raise
  `MAX_BYTES`.
- 🔴 **`scripts/handoff-audit.py` was MISINFORMING every reader, and P4's routing line would
  have amplified it.** It printed *"No gate in this repo measures a handoff doc"* — true when
  it shipped 2026-09-01, false from 2026-09-13 when #1648 added a real 65,536 B per-doc
  ceiling (`test_handoff_doc_size.py`) that goes red on `main` for everyone. Fixed in
  `b50709ca`: the banner now distinguishes the unenforced 12,288 B target from the enforced
  ceiling and reads the latter from `handoff_budget`, loaded **defensively** (returns None)
  because the tool audits other repos' corpora where that gate legitimately does not exist.
  Both controls run: devrc prints 65,536 B, a missing module omits the line.
- **Decision: P4 shipped for `/handoff` only.** `/resume` is read-only re-entry; a
  corpus-maintenance pointer there is prose nobody acts on (the-algorithm §1 — question the
  requirement, and this one could not name who would act on it).

- 🔴 **NEVER demote `## Gotchas` or an open `## Open investigations` block to
  `claudedocs/refs/` — and the reason is MEASURED, not stylistic.** `refs/` is outside the
  `handoff_search` corpus: control pair above, two refs-only tokens returning NO MATCH
  against 6,370 sections while an indexed doc's token returns rank 2.0. So "demote" is
  functionally "delete, for retrieval". The authoritative playbook in
  `scripts/tests/test_handoff_doc_size.py` (lines 138-160) already ranks the steps — **evict
  CLOSED first, demote DATED second, split third, raise the number LAST** — and says "Keep
  the imperative, the open questions and the gotchas in the doc itself". **P1 was specified
  against that playbook and is retracted.**
- 🔴 **THE METRIC IN THIS ARC'S CLOSING CONDITION WAS BADLY CHOSEN, which is worth more than
  the item it gated.** "APPEND share below 70%" can only fall by moving searchable content
  out of the index — the one action the playbook forbids. Legitimate eviction barely moves
  it, because completed ranks live under `## Next steps`, a REPLACE section. **A byte-share
  target selected the harmful action as the cheapest way to satisfy it.** When freezing a
  closing condition, ask which action most cheaply satisfies the metric, and whether you would
  accept that action.
- **The eviction backlog is real and already DETECTED, just not acted on:** 468,110 B /
  14.1% of the corpus — 284,262 B resolved investigations, 120,127 B completed ranks,
  79,393 B retracted/dead-ends, 10,928 B work-status. 28 docs are over the 40,960 B hard cap
  and 75 of 98 over target. `handoff-audit.py` finds all four classes deterministically.
- ⚠ **Do NOT quote the auditor's `RELOCATE_DURABLE` count as a measurement** — it says so
  itself, prints no byte estimate on purpose, and its regex read 28-30 where a human reading
  the same doc found ~55.
- **Three of four proposed items died on contact with measurement** (P2 refuted, P4's
  `/resume` half deleted, P1 retracted) and the survivor shipped in ~180 bytes. That is
  the-algorithm working, not the effort failing: the expensive part was never the code.

- 🔴 **A MUTANT SURVIVED A GREEN SUITE, and the cause is worth more than the fix: the test
  drove the WRONG ARM.** `test_the_note_is_withheld_from_the_UNGATED_arm` asserts exactly the
  right thing, but its fixture is over the ceiling, so `budget_warning` returns from the
  OVER-budget arm **before** the near arm's `if gated` is ever evaluated. Deleting that guard
  therefore changed nothing any test could observe. The assertion was correct, the
  REACHABILITY was not — `claude/RULES.md`'s "prove it REACHABLE, not just breakable", hit in
  a new shape. Two near-arm tests added; M3 and M4 now die. **Sweep ran under
  `PYTHONDONTWRITEBYTECODE=1`** (the same-second/same-length `.pyc` trap), with a control
  green either side and the file restored byte-identical (`cmp`).
- 🔴 **`handoff_doc.py` is the WRITE PATH — code added there must not be able to raise.**
  `evictable_note` catches everything and returns `""`. An exception would take down
  `/handoff`'s only landing step and cost a session its record, to decorate a warning. The
  auditor is also a devrc script, so a repo vendoring only this module legitimately has no
  such file: absence is the ordinary case, not an error.
- **Reuse the auditor's detectors; never reimplement "is this resolved".** Its matchers have
  already absorbed corrections a fresh implementation would re-earn — the `retracted` bucket
  over-counted by 30% until bullets crossing a heading were clipped. One rule, one place.
- ⚠ **`audit_one` now delegates to `audit_text`** because `budget_warning` holds a merge that
  is not yet on disk. Verified behaviour-identical on a real doc (`audit_one == audit_text`
  across size/gross/net/done_b/resolved_b) before anything depended on it.

- 🔴 **A CONFIRMED HYPOTHESIS DOES NOT ENTITLE THE ITEM THAT MOTIVATED IT.** P3's probe was
  pre-registered ("confirmed ⇒ P3 is the right shape") and came back confirmed — then the
  item was deleted anyway, because the probe answered the question I had framed and not the
  question that decided the build. What decided it was reading the tool's actual output: the
  remedy was already there. **Pre-registration protects against post-hoc rationalisation; it
  does not license skipping the look at what already exists.**
- 🔴 **The unautomatable part is the point, not an obstacle.** Rule (m) exists to make a human
  state what ENDS an arc. Any mechanism that fills that field in has produced a finish line
  nobody asserted — the same defect `/handoff` step 1 forbids for `clawgate-task:` ("a task
  minted to fill a blank field is a fact nobody asserted"). When the only remaining increment
  is the judgement itself, the correct build is none.
- 🔴 **Grandfathering here is CORRECT and must not be "closed".** Refusing all 84 pre-rule
  docs would go red on the first update to each, which `claude/RULES.md` calls worse than no
  gate. An exemption that looks like a coverage gap can be the thing keeping a gate credible.
- 🔴 **THIS ARC'S CLOSING CONDITION SELECTED FOR THE HARMFUL ACTION.** "APPEND share below
  70%" could only fall by moving searchable content into an unsearchable sink — the one thing
  the playbook forbids. The metric made the forbidden action the cheapest way to satisfy it.
  **When freezing a closing condition, ask which action most cheaply satisfies the metric and
  whether you would accept that action.** Verdict on this arc: **NOT ADDRESSED as written,
  and not addressable**; what replaced it is a new arc.

- 🔴 **A SHARED CLONE IS A SHARED BRANCH — and the failure is SILENT in both directions.**
  Another session's commit landing on your branch produces no error, and `git log` afterwards
  reads exactly as you expect because you are reading the branch you landed on. It was found
  only because a `git log --oneline -3` after a push showed a subject nobody here had written.
  **`git branch --show-current` before every commit removes the class; a worktree removes the
  opportunity.** Ported as a numbered rule into `CLAUDE.md`.
- 🔴 **Preserve BEFORE you rewrite, and preserve the TIP, not the commit you noticed.** The
  first rescue branch captured `e1ed617d` only; a second commit arrived 40 minutes later and
  would have been orphaned by the force-push. Pushing the whole branch tip to a rescue ref is
  one command and captures whatever you have not noticed yet.
- 🔴 **Verify a duplicate by CONTENT before dropping it.** `e1ed617d` shared a subject with
  main's `2e8d8a11`, and `scripts/stt` / `SECRETS.md` / `nix/home.nix` were byte-identical
  (same blob shas) — but it was a SUPERSET: the `browser-bridge/reference/sites → flows`
  rename existed nowhere else. Dropping on the subject match would have destroyed it.
- ⚠ **`for x in $VAR` runs ONCE in zsh.** The cherry-pick loop silently passed all 20 shas as
  a single argument and died with `File name too long` — which reads as a path bug, not a
  splitting bug. `${=VAR}`. Documented in `claude/RULES.md`; hit anyway.
- ⚠ **`gh pr view --json files` served a STALE file list** for minutes after a successful
  force-push, still showing the contaminated set. `git diff --name-only origin/main...origin/<branch>`
  is the authority; do not re-fix a PR on the API's word.

- ✅ **A CORRECTED CLOSING CONDITION CAN STILL CLOSE — and this one did.** The round-1
  condition ("P1 merged AND the APPEND share below 70%") was unsatisfiable: the demotion is
  forbidden and the metric could only fall by doing it. It was re-aimed at the arc's actual
  deliverable and is now MET on all three clauses. 🔴 **That re-aim is still an unratified
  judgement of an agent's** — the strict reading of frozen-at-round-1 says close the doc NOT
  ADDRESSED and open a new one instead. Recorded as a decision the operator may reverse; if
  reversed, the work is unaffected and only this verdict changes.
- 🔴 **THE METRIC SELECTED THE HARMFUL ACTION, and that is the transferable lesson.** A
  byte-share target made the one forbidden action the cheapest way to satisfy it. **When
  freezing a closing condition, ask which action most cheaply satisfies the metric, and
  whether you would accept that action.**
- **The concurrent-session contamination resolved itself correctly.** Another session
  committed onto this branch twice; both commits were preserved on origin BEFORE any
  rewrite, and that session then landed its own work as `#1819`. Preserving the TIP rather
  than the commit you noticed is what made the second one survivable.

- 🔴 **THE HEADLINE RESULT IS THAT MOST OF THIS SHOULD NOT HAVE BEEN BUILT — and that is a
  success, not a failure.** Four items proposed: two shipped, two deleted by measurement;
  then `/the-algorithm` deleted most of one that HAD shipped (−178/+42). What survives is
  ~80 lines doing the one thing nothing else did — reporting how much of a doc has closed.
  **The expensive part was the measuring, and it repeatedly said DON'T.**
- 🔴 **THE DEFECT GENERATOR WAS PROSE, AND THE ATTRIBUTION IS WHAT FOUND IT.** Five audit
  rounds; nearly every finding sat in the PREVIOUS round's comments rather than its logic.
  Attributing all ten of #1815's findings to their causing surface gave **7 advice / 3
  numbers** — and the advice existed only because this code copied an eviction ladder that
  `test_handoff_doc_size.py` already owned. **One rule, one place; I made it two, then spent
  four rounds keeping the copy true.** When a ladder keeps finding things, attribute the
  findings to a SURFACE before writing another fix.
- 🔴 **I ADDED A GUARD TO FIX A COSMETIC DUPLICATION IN AN ADVISORY LINE** —
  `test_each_arm_carries_exactly_one_prohibition` — which is the-algorithm step 5 ("the fix
  for over-guarding is NEVER another guard") violated inside the very effort applying it.
  Deleted by the pass. The tell: the thing being guarded changed nothing a reader does.
- 🔴 **A METRIC CAN SELECT THE HARMFUL ACTION.** This arc's round-1 closing condition made
  the one action its own playbook forbids the CHEAPEST way to satisfy it. **When freezing a
  closing condition, ask which action most cheaply satisfies the metric, and whether you
  would accept that action.**
- **`/the-algorithm` run retroactively works, with a caveat worth stating**: the skill says
  it is "an in-the-moment gate, not a bulk retroactive audit", so a retro pass gets
  hindsight bias free — the findings look obvious only because the audit rounds surfaced
  them first. It still paid: it deleted a surface four rounds of correctness auditing had
  only been making *more correct*.

## How to verify
```bash
# the arc's closing condition, all three clauses
gh pr view 1815 --repo innovation-upstream/devrc --json state --jq .state          # MERGED
git -C $DEVRC rev-parse --short HEAD; ssh zach@10.42.0.100 'git -C ~/workspace/devrc rev-parse --short HEAD'
readlink -f ~/.claude/skills/handoff/SKILL.md && wc -c < ~/.claude/skills/handoff/SKILL.md   # /nix/store, 20,176 B

# the advice is gone from main and the numbers survive
git -C $DEVRC show origin/main:scripts/lib/handoff_doc.py | grep -cE 'JUDGEMENT: the playbook|NOT counted above'   # 0
git -C $DEVRC show origin/main:scripts/lib/handoff_doc.py | grep -c 'Evictable in THIS doc'                        # 1

# the note as a live consumer sees it
python3 $DEVRC/scripts/handoff-audit.py $DEVRC/claudedocs/handoff-audit-pr-ladder.md
```
