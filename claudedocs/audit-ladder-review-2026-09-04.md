# How adversarial audit ladders actually ran, 2026-08-28 → 2026-09-04

**Run date: 2026-09-04.** Every number here is a measurement with a timestamp, not a constant.
The corpus accumulates and it moved *during this sweep*: the all-time control read **4,171
dispatches / 245 ladder sessions** at the start of the run and **4,172 / 246** roughly twenty
minutes later, mean deepest 4.45 → 4.44. Re-derive rather than quote.

- **Window:** rows stamped `2026-08-28` … `2026-09-05` (transcript timestamps are UTC, so
  "today" spills into an 09-05 stamp bucket).
- **Repos in scope:** all. The corpus covers whatever project a session ran in — devrc,
  homelab-talos, civit-datapacket-talos on the workbench; vetr, auditloop,
  civitai-gpu-fleet, naida-ai on the laptop.
- **Runtime covered: Claude Code ONLY.** The sweep walks `~/.claude/projects/**/*.jsonl` via
  `scripts/lib/transcript_search.py`, which excludes the `subagents/` tier. **opencode is not
  in this corpus at all**, and it has no per-record skill attribution, so no opencode ladder
  is counted anywhere below.

---

## Instrument validation, before any verdict

`scripts/ladder-depth-sweep.py` refuses to report a zero it cannot distinguish from a broken
filter. Both hosts cleared its positive control, so no number below is a zero-from-nothing.

| host | all-time dispatches walked (positive control) | all-time ladder sessions | in-window dispatches |
|---|---|---|---|
| workbench | 4,171 → 4,172 (moved mid-run) | 245 → 246 | 1,280 → 1,281 |
| laptop | 976 | 65 | 326 |

**`hosts-reporting = 2 of 2.`** The laptop was reached over nebula (`ssh zach@10.42.0.100`),
which has its own `~/workspace/devrc` checkout at `d9f0836c` and its own `python3`. Nothing
below is a workbench number presented as fleet-wide.

A second instrument was written for this review — a bounded-window variant that reuses the
shipped script's corpus walk, `DISPATCH_TOOLS` set and `ROUND` regex by importing the module,
adding only an `--until` bound. Its unbounded run reproduces the shipped script exactly
(modulo the corpus drift noted above), which is its positive control. It exists because the
shipped script has `--since` only, and **its own docstring warns that comparing windows of
different length is biased** — a narrower window truncates ladders that began before it and
mechanically raises mean depth (measured on one unchanged corpus: 4.38 all-time, 4.97 since
08-20, 6.47 since 08-27).

---

## Question 1 — did the stop rule change behaviour?

### The rule did not land at one moment

| mechanism | landed |
|---|---|
| findings-keyed: a clean round ENDS the ladder | #861, 2026-08-25 |
| attribution gate: two consecutive zero-payload rounds ⇒ stop | #900, 2026-08-27 |
| prose escape hatch must state its rationale IN THE SUMMARY | #1157, 2026-08-31 |
| once-per-ladder, range-free count re-derivation | #1109/#1111 era, 2026-08-30 |

So the assigned window sits **entirely after** the two core mechanisms and **straddles** the
third. There is no clean before/after boundary to measure across.

### Equal-length windows, one corpus, run 2026-09-04

Three consecutive 9-day windows, same instrument, same corpus — which controls the
window-length bias:

| window | | sessions | rounds observed | rounds implied | mean deepest | ≥5 rounds | median |
|---|---|---|---|---|---|---|---|
| W1 08-10…08-18 | pre-rule | 28 | 60 | 109 | **3.89** | 32% | 4 |
| W2 08-19…08-27 | transition | 81 | 233 | 388 | **4.79** | 51% | 5 |
| W3 08-28…09-05 | assigned window | 129 | 412 | 572 | **4.43** | 39% | 4 |

Depth histograms:

- W1 `2→3 3→9 4→7 5→6 6→3` (max 6)
- W2 `2→7 3→20 4→13 5→20 6→7 7→5 8→4 9→1 10→3 14→1` (max 14)
- W3 `1→1 2→15 3→35 4→28 5→22 6→9 7→10 8→1 9→5 10→2 17→1` (max 17)

### What that supports, and what it does not

**Mean depth fell from W2 to W3 (4.79 → 4.43) and the ≥5-round share fell hard (51% → 39%).**
Against W1, the pre-rule window, W3 is still *deeper* (3.89 → 4.43).

🔴 **This is not a controlled experiment and should not be read as one.** Ladder volume
**tripled** across the three windows (28 → 81 → 129 sessions). The population being measured
changed more than the metric did, and nothing here separates "the rule changed behaviour"
from "the mix of work changed". A rival mechanism with equal explanatory power: W2 contains
the `audit-pr` development work itself — the deliberately deep ladders that produced the rule
— so W2's elevation may be the authoring sessions, not a general regime.

🔴 **The single strongest counter-observation: the deepest ladder in the ENTIRE corpus ran
INSIDE the window, after every core mechanism shipped.** devrc session `2d734bbf`,
2026-08-31, reached **round 17**, and it is real depth rather than a numbering artifact — 15
of the 17 numbers were observed dispatched (`2,3,4,5,6,7,8,9,10,12,13,14,15,16,17`).

**Verdict: not established.** The distribution moved in the direction the rule intends
between W2 and W3, but with the population tripling underneath it, this measurement cannot
attribute that to the rule. It is consistent with the rule working and equally consistent
with a mix shift.

### Against the dated baseline

`claude/skills/audit-pr/reference/round-ladder-evidence.md` records, for **2026-08-27**:
127 sessions, 306 observed / 541 implied, mean deepest 4.26, 39% ≥5 — restated hours later
the same day as 319 / 556 / 4.38 by an independent re-run. Today (**2026-09-04**) the
all-time corpus reads 246 sessions, 718 observed / 1,092 implied, mean 4.44, 41% ≥5. The
corpus has roughly doubled in eight days; **mean depth is essentially unchanged** (4.26–4.38
→ 4.44) and the ≥5 share moved 39% → 41%.

The same file records blind-spot facts from 2026-08-27 that this review did not re-derive:
231 of 239 missing round-numbers are LEADING absences, 128 sessions have a minimum round of
2+, and exactly ONE session in the corpus has ever dispatched a "round 1".

---

## Question 2 — the waste audit

### The anchors turned out to be recoverable

The dispatching hypothesis was that the `audit-claims` ledger is unused, which would make
per-round anchors unrecoverable. **It is not** (see the ledger finding below), so this was
measured directly rather than reported UNMEASURABLE.

Method, per the skill's own reference file:

```
git log --numstat --format= --remerge-diff <the sha that round audited>..<head> --not <base>
```

with `<base>` = `origin/main`, freshly fetched and cross-checked against the GitHub API
(`d9f0836c` from both, two tools that fail differently). All four read rules enforced in
code, not assumed: **rc 0 · silent stderr · non-empty range · the checkout is standing on the
PR**. The fourth is discharged *by construction*: every range names an EXPLICIT head sha
taken from the API's `headRefOid`, never the ambiguous `HEAD` of whatever branch a worktree
is on. `--not <base>` excludes the bring-in a `merge main` drags along; `--no-merges
--first-parent` was NOT substituted (it reads 0 for a fix merged `--no-ff` from a side
branch, the shape agent worktrees produce).

🔴 **An off-by-one in my own instrument, caught and corrected mid-review — recorded because
the wrong version printed entirely plausible numbers.** The first version assumed a block's
second sha was "the sha that round audited" and measured `to(N)..to(N+1)`. It is not.
`audit-dispatch.py` states the field meaning in the legend it emits and in its module
docstring, and they agree: **`<from>` = the tip THIS round's audit READ; `<to>` = the head
THIS round's FIXES produced.** So a round's own fix churn is the block's *own* range. The
wrong form was off by one round in silence, and it manufactured a fake "empty terminal range"
for every well-formed ladder, which read as a clean final round — an artifact I briefly
believed. Correcting it moved measurability from **134 measured / 18 unmeasurable** to
**153 / 1**, which is itself the evidence the corrected reading is right.

### Payload vs scaffolding was classified BY HAND, per PR

A pathspec cannot make this call, and two shapes in this corpus defeat any extension rule
outright:

- **docs/skill PRs** (#1108, #1207, #1220) where the payload IS the `.md`;
- **#1219**, whose entire *deliverable* lives under `tests/` and `testlib/` — a shared
  store-siting helper plus its ledger. An extension rule reads every round of it as zero
  payload and stops a ladder that is working.

Per the skill's own tie-break, **ambiguous counts as payload** (the gate does not fire and
the ladder continues).

### The table — 20 devrc ladders at depth ≥ 4, `payload lines / scaffolding lines` per round

| PR | depth | per-round payload/scaffolding |
|---|---|---|
| #958 | 12 | r2 668/1401 · r3 18/35 · r3 382/1102 · r4 487/1295 · r5 324/794 · r7 189/1124 · r8 314/1310 · r10 376/1071 · r12 113/530 |
| #989 | 4 | r1 428/733 · r2 286/621 · r3 132/654 · r4 16/367 |
| #998 | 6 | r1 53/186 · r2 116/188 · r3 76/211 · r4 73/181 · r5 54/64 · r6 28/73 |
| #1000 | 4 | r1 274/122 · r2 87/248 · r3 168/181 · r4 107/154 |
| #1046 | 6 | r1 103/66 · r2 67/71 · r3 55/66 · r4 31/62 · r5 43/70 · **r6 0/19** |
| #1064 | 4 | r1 139/146 · r2 87/63 · r3 52/39 · r4 63/32 |
| #1083 | 8 | r1 856/635 · r2 177/311 · r3 97/131 · r4 54/111 · r5 51/75 · r6 137/84 · r7 71/108 · r8 65/130 |
| #1108 | 6 | r2 57/0 · r3 90/0 · r4 49/0 · r5 47/0 · r6 43/0 — *prose PR, gate structurally inert* |
| #1110 | 4 | r1 74/74 · r2 52/40 · r3 40/27 · r4 35/0 |
| #1120 | 5 | r1 343/163 · r2 145/63 · r3 122/97 · r4 19/26 · r5 22/0 |
| #1121 | 5 | r1 555/668 · r2 329/365 · r3 148/447 · r4 146/398 · r5 67/140 |
| #1132 | 6 | r1 63/452 · r2 32/257 · r3 16/113 · r4 31/161 · **r5 0/412 · r6 0/221** ⇐ gate's firing condition |
| #1181 | 4 | r1 183/48 · r2 94/19 · r3 98/3 · r4 52/0 |
| #1207 | 5 | r1 50/0 · r2 37/0 · r3 26/0 · r4 30/0 · r5 8/0 — *prose PR, gate inert* |
| #1209 | 4 | r1 871/907 · r2 928/628 · r3 558/759 · r4 387/663 |
| #1219 | 6 | r2 196/0 · r3 281/0 · r4 156/0 · r5 202/0 · r6 196/0 — *deliverable IS test infra* |
| #1220 | 6 | r1 92/0 · r2 56/0 · r3 67/0 · r4 48/0 · r5 17/0 · r6 33/0 — *prose PR, gate inert* |
| #1233 | 4 | r1 123/291 · r2 36/160 · r4 24/177 — *round 3 posted no block* |
| #1274 | 5 | r1 115/147 · r2 99/166 · r3 7/104 · **r4 0/34** · r5 UNMEASURABLE (empty range — no commit landed for r5) |
| #1286 | 5 (OPEN) | r1 220/227 · r2 69/25 · r3 39/2 · r4 29/14 · r5 17/16 |

### The answer

**Across 102 measured rounds in 20 deep devrc ladders, exactly 4 rounds changed zero payload,
and exactly ONE ladder reached the gate's two-consecutive-zero-payload firing condition.**

**The `civitai/cli` #498 shape did not recur in devrc in this window.** #498 was seven
consecutive rounds (4–10) at 1,051 test lines and zero payload, no round ever clean. Nothing
here resembles it. The characteristic in-window shape is the opposite: payload every round,
**decaying** — #998 runs 53→116→76→73→54→28 payload lines over six rounds; #1286 runs
220→69→39→29→17; #989 runs 428→286→132→16.

**#1132 is the one gate-firing instance, and the ladder stopped exactly there.** Rounds 5 and
6 changed zero payload (412 and 221 scaffolding lines), r6 was the last round, and the gate
says stop after two such rounds. That is the gate's condition and the observed behaviour
agreeing — but **correlation only**; nothing in the transcript record proves the gate is why
it stopped.

🔴 **And that finding is sensitive to one classification call.** I classed
`scripts/testlib/nix_units.py` as scaffolding. Counted as payload instead, #1132 r5 becomes
19 payload lines and only ONE zero-payload round exists — the gate never fires, and the
window contains zero firing instances. The skill's own "ambiguous is not zero" tie-break
points at the second reading. **State this as a judgement, not a measurement.**

**The documented prose blind spot is not an edge case — it is 20% of deep ladders.** Four of
the twenty (#1108, #1207, #1220 prose; #1219 test-infrastructure) ran 6, 5, 6 and 6 rounds
respectively under a gate that **structurally cannot fire**, because every line they touched
is payload by construction. For these the only available stop mechanism is the stated
criterion + the escape hatch.

---

## The ledger-surface hypothesis — CONFIRMED IN THE NARROW, REFUTED IN THE GENERAL

The dispatching session reported 0 `audit-claims` blocks across the last 12 merged devrc PRs
(#1298–#1314), on three surfaces, with a passing positive control (#958=9, #1157=3, #1133=2).

**Re-run, and the method reproduces.** My block-counter (fence regex matching
`audit-dispatch.py`'s own `_FENCE_OPEN`) reads **#958 = 9 blocks**, matching the brief's
control exactly. Two corrections to the brief's own reads: **#1237 carries 1 block in the PR
BODY** (reported as 0), and **#1304 carries 2 blocks** — it is OPEN, not merged, which is why
a merged-only scan misses it.

**The generalisation does not hold.** Over the whole window:

| measurement | value |
|---|---|
| devrc PRs merged 2026-08-28 → 2026-09-04 | **306** (`/search/issues`; the brief's "60" was a `gh pr list` cap artifact) |
| merged PRs carrying ≥1 `audit-claims` block | **41** |
| total blocks on merged PRs | **146** (145 issue-comment + 1 body; **0 review-comment**) |
| PRs with blocks including OPEN ones | **44**, **154 parsed blocks**, 1 unparseable header |

So the ledger mechanism **is** in active use — including today, on open PR #1304. What is
true is narrower and still worth acting on: **no merged devrc PR numbered ≥ 1286 carries a
block** (0 of the 19 merged in that range), while #1286 and #1304, both open, do. That is
either a genuine recent decline or an artifact of what merged in that stretch — mostly
`docs(handoff)` PRs. **Not established which.**

One structural note: **all 146 blocks are on issue comments or the body; zero are review
comments.** `gh pr view --json comments` returns issue comments only, so it happens to see
99.3% of them — but that is luck, not a safe method. Check all three surfaces.

---

## Telemetry cross-check — three numbers, three methods, no reconciliation

| route | count | what it counts |
|---|---|---|
| `ladder-depth-sweep.py` | **166** (128 workbench + 38 laptop) | sessions dispatching a NUMBERED delta re-audit |
| `find-session --skill audit-pr --since 2026-08-28` | **197** | sessions that USED the skill, cross-host |
| ClickHouse `skills_used` map | **159** | sessions whose `session-summary` recorded the skill |

**Report all three; do not average them.** They measure different things and every one is
structurally weak for ladders:

- 🔴 `find-session --skill` **counts SESSIONS, and a skill used only inside a dispatched
  subagent is not counted** — and `/audit-pr` dispatches subagents by design. Its own
  `--help` states this. It also SKIPS the opencode corpus (no attribution there).
- 🔴 ClickHouse `skills_used` is a **derived** surface and is documented to undercount the
  transcripts (measured 2026-09-04 for `signal`: 10 vs 6, a strict subset). Here it reads
  159 against find-session's 197 — a 19% shortfall, the same direction and rough magnitude.
- The sweep sees only **numbered** rounds, and only Claude Code.

The controls the `activity` skill mandates were run and are reported as a pair, not as a bare
number:

| arm | result |
|---|---|
| correct membership test `!= ''` | **159** |
| NEGATIVE CONTROL, predicate removed | **355** — differs, so the filter is not inert |
| the documented always-true trap `IS NOT NULL` | **355** — identical to the whole population, reproducing the trap exactly |

That third arm is worth keeping: it demonstrates live that `JSONExtractString(...) IS NOT
NULL` selects every row, so any statistic built under it is a population statistic wearing a
skill's name.

`find-session` (197) exceeding the sweep (166) is the expected direction — a session can
invoke `/audit-pr` and never dispatch a *numbered* delta round. The gap of 31 is consistent
with that, and also with the sweep's blind spots. **It is a finding, not a discrepancy to
resolve.**

---

## What this measurement structurally CANNOT see

Both directions, and they do not cancel.

**Under-counts of ladder work:**

- **Unnumbered rounds are invisible.** The reference file's 2026-08-27 measurement found 231
  of 239 missing round-numbers were LEADING absences and that **at least 103 of them (45%)
  must be unnumbered DELTA rounds**, not first audits. True depth is therefore HIGHER than
  IMPLIED, not lower.
- **opencode ladders are entirely absent** from the corpus, and opencode has no per-record
  skill attribution.
- **Subagent transcripts are excluded** by `transcript_search.py`. Since `/audit-pr` works by
  dispatching subagents, everything measured here is the *dispatching* side only.
- **Only PRs that POSTED a claims block are in the waste audit.** 41 of 306 merged devrc PRs.
  Ladders that ran without posting a ledger are unmeasured — not zero.
- **The waste audit is devrc-only.** Ladders ran in homelab-talos, civit-datapacket-talos,
  vetr, auditloop, civitai-gpu-fleet and naida-ai; none were churn-measured.

**Over-counts:**

- **IMPLIED depth assumes a ladder reaching round N ran N rounds.** In the window's depth-≥7
  slice: **121 rounds OBSERVED against 160 IMPLIED — implied is 24% high there.** Some of
  that is real (leading absences), but some is interior skips.
- **An "odd rounds only" ladder inflates depth.** devrc `056c842f` dispatched rounds
  `[3,5,7,9]` — audits on odd numbers, fixes on even. "Depth 9" there is four audit rounds,
  not nine.
- **A duplicate round number inflates the round count.** #958 posted **two** `round=3` blocks.

**Cannot see at all:**

- **Why a ladder stopped.** Every stop-attribution here is correlation. Nothing in the
  transcript or PR record states "the gate fired", so no causal claim is made.
- **Whether a round was CLEAN.** The corrected measurement removed the fake "empty terminal
  range" signal I had briefly read as a clean round. Cleanliness lives in the audit's prose
  verdict and was not extracted at scale.
- **Whether the escape hatch's stated rationale was actually written** (#1157's requirement).
  Not measured; it needs prose extraction from every terminal round's summary.
- **Elapsed time and token cost per ladder.** The #498 case history's most damning figures
  (5h32m, 77% of session output) have no counterpart here — this review measured churn and
  depth only.

---

## Open items, each with a closing condition

1. **Re-measure W3 against a W4 of equal length once the population stabilises**, to separate
   the rule's effect from the volume tripling. *Closes when* a 9-day window with comparable
   session volume to W2 is available and the comparison is re-run.
2. **Decide the `scripts/testlib/**` classification** — payload or scaffolding — and write it
   into the reference file. It currently flips the window's only gate-firing instance.
   *Closes when* the reference file states the call with its reason.
3. **Establish whether the ≥1286 block drought is real.** *Closes when* the next 20 merged
   devrc PRs are re-scanned on all three surfaces and the rate is compared against 41/306.
