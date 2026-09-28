# handoff-search-index — CLOSED `## Open investigations` blocks and dated evidence

🔴 **EVICTED FROM `claudedocs/handoff-handoff-search-index.md` on 2026-09-19** because a required
update — closing ranks 6 and 7, which the ranked list still called OPEN days after `#1791` and
`#1670` merged — would have taken that doc to 66,943 B against a 65,536 B ceiling, turning
`test_no_handoff_doc_exceeds_its_budget` RED on `main` for everyone. Every block below is a
question that got ANSWERED — step 1 of that test's own eviction playbook — verified on
`origin/main` BY CONTENT rather than by believing the block's own status text.

⚠ **A `refs/` file is NOT indexed by `handoff_search`.** That is the trade, and it is why only
CLOSED, DATED material is here and never an open thread. Nothing below is live; each block records
how a question was settled, so a future session does not re-run the probe.

🔴 **Every block below was moved by VERBATIM LINE-RANGE SLICING**, not retyped and not re-ordered
within a block. The source line ranges are named against
`claudedocs/handoff-handoff-search-index.md` at `7ef01c05`.

🔴 **WHAT THESE BLOCKS WERE PROTECTING, and where the guard went.** The core doc's Gotchas bullet
*"TWO OF THIS DOC'S OWN `Open investigations` BLOCKS WERE ANSWERED WEEKS AGO AND STILL READ AS
LIVE"* cites the first two slices below as its worked example, including the literal *"16 and 55
lines below them"* distances. The RULE stays in the indexed doc; its EVIDENCE is here. The two
`RESOLVED —` counterparts those blocks point at both STAY in the doc, so every pointer in the
demoted text still resolves — forward into the doc, not into this file.

## Closed / superseded investigation blocks

<!-- source lines 56-82 -->
**Closure evidence:** `#1267` MERGED 2026-09-04, merge commit `d86b4e45`; its answer is the `RESOLVED — the unreadable-docs delete path` block, which STAYS in the indexed doc. The block says so itself (`CLOSED by #1267`) and is headed SUPERSEDED.

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

<!-- source lines 83-102 -->
**Closure evidence:** `#1267` MERGED 2026-09-04, `d86b4e45`; its `Next probe` ("ask whether any single invariant would have prevented all four") is the question the `RESOLVED — the four-spelling shape` block answers, and that block STAYS. Kept here for the enumeration of the four spellings.

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

<!-- source lines 129-185 -->
**Closure evidence:** `#1332` MERGED 2026-09-06T17:51:19Z, `8e9428ef`. The block's own `Next probe` says the promotion ALREADY LANDED and only the re-run remained; the re-run was done (see the next slice, and the corrected figures in the doc's `State now`).

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

<!-- source lines 186-225 -->
**Closure evidence:** Self-declared `SUPERSEDED at n=22 by the block below`, and that block — `🔴 RETRACTED (the yield NUMBER)` — STAYS in the indexed doc. Its `Next probe` reads `none — run 2026-09-08`. Its one residual is stamped `EXPIRED 2026-09-12` in its own text.

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
  🔴 **EXPIRED 2026-09-12 — this residual no longer holds.** Both of this doc's own
  checkpoints were correct at the time they were made: post-`#1332` through its ~8.6 h
  window (2026-09-07T02:25Z baseline, line ~691 below) the laptop genuinely was 0, and the
  2026-09-08T17:00Z re-run's "laptop STILL 0 runs" (line ~692) was also correct — the first
  laptop `/resume` session post-`#1332` was **2026-09-08 22:45 UTC**, about 5.8 h after that
  re-run. Since then, four laptop `/resume` sessions have landed: 2026-09-08 22:45,
  2026-09-09 06:11, 2026-09-09 16:35, 2026-09-12 06:11 (all UTC) — session id prefixes
  `fcbe7381`, `11cb641d`, `93b78ca8`, `50c962d8`. ⚠ **Whether any of the four actually
  invoked `handoff_search.py` is NOT measured here** — say so rather than implying the
  yield question is answered; this only closes the "0 laptop runs" residual, not the yield
  one.
