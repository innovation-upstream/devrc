# How adversarial audit ladders actually ran, 2026-08-28 → 2026-09-04

**Run date: 2026-09-04. Corrected 2026-09-05** after an independent BLIND re-measurement of
the three load-bearing numbers (it was not shown this report). It agreed on the headline —
146 real blocks, and at most one ladder meeting the two-consecutive-zero-payload condition —
and contradicted three claims, each corrected in place below and marked 🔴. Sections carrying
a correction: **the ledger surface** (the `#1237` body block was a false positive and the
`≥1286` drought is retracted), **the gate-firing finding** (now an open three-way
disagreement), and **the empty-range read rule** (I discarded a real datum). The Q1 verdict
and the volume confound are untouched by any of it.

Every number here is a measurement with a timestamp, not a constant.
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
| #1274 | 5 | r1 115/147 · r2 99/166 · r3 7/104 · **r4 0/34** · **r5 = a genuine NO-FIX clean round** (see below) |
| #1286 | 5 (OPEN) | r1 220/227 · r2 69/25 · r3 39/2 · r4 29/14 · r5 17/16 |

### The answer

**The `civitai/cli` #498 shape did not recur in devrc in this window.** #498 was seven
consecutive rounds (4–10) at 1,051 test lines and zero payload, no round ever clean. Nothing
here resembles it. The characteristic in-window shape is the opposite: payload every round,
**decaying** — #998 runs 53→116→76→73→54→28 payload lines over six rounds; #1286 runs
220→69→39→29→17; #989 runs 428→286→132→16.

**Aggregate across all 144 measured rounds: 19,230 payload lines vs 32,393 scaffolding —
a ratio of 1.68 : 1.** Scaffolding dominates, but nowhere near the ∞:1 that #498's rounds
4–10 produced.

#### Reconciling my count against the independent re-measurement — it is SCOPE, not conflict

I reported **4 zero-payload rounds in 102 measured rounds**. A blind re-measurement reports
**9 zero-payload rounds in 144 measured rounds**. These agree exactly:

- **My 102 rounds** are the depth-≥4 subset (20 of the 44 carrier PRs); the 144 covers all
  carriers, shallow ladders included.
- Its 9 are #1002 r3, #1046 r6, #1132 r6, #1148 r3, #1149 r2, #1157 r2, #1233 r4, #1274 r4,
  #1277 r1. **Restricted to my 20 deep ladders that is exactly {#1046 r6, #1132 r6, #1233 r4,
  #1274 r4} = 4.** The five it adds are all shallow PRs outside my table.
- The **only** genuine per-round disagreement in the overlap is **#1132 r5**, which is the
  single-file classification dispute above.

Full round population: **146 blocks → 144 measured, 1 EMPTY_RANGE, 1 UNMEASURABLE**
(#958 round 1, whose header is a bare `audited=abc41024` with no `..to`).

#### 🔴 A methodological correction: an EMPTY RANGE is not always an instrument failure

I reported **#1274 r5 as UNMEASURABLE** on the "non-empty range" read rule. **That was
wrong, and it discarded the single most informative datum in the corpus.** The range
`434ab939..434ab939` is empty *because the round made no fix*, and the block says so itself:

> 1. No fixes were made this round: the audit returned no findings. The head is unchanged
>    from the tip round 5 read.

The "non-empty range" rule in `measure_ledger` exists to catch a **broken measurement** — a
missing ref, a git without `--remerge-diff`, commits not in this checkout. It is not a rule
about a round that genuinely changed nothing. `measure_ledger` already draws that line
(`anchor_is_head` ⇒ "empty BY CONSTRUCTION, a broken question" vs. a real "nothing has
landed"), and I applied the rule more literally than its own source does. **A clean, no-fix
terminal round is a zero you can trust — and it is precisely the observation that proves a
stop rule fired.** Reading it as UNMEASURABLE deletes the evidence you are looking for.

### The uncontested instance is #1274, and it stopped on the FINDINGS rule — not the gate

**devrc #1274 (`feat(main-green)`) is the one ladder in the window that visibly ended on a
stop-rule mechanism, and said so.** Round 5's comment reads:

> ## Audit round 5 — **CLEAN. The ladder ends here.**
> No 🔴, no 🟡, no 🟢. Round 4's fix produced no finding of its own — the first time that has
> been true in this PR — so per the stop rule the ladder stops, **and I am not running a round
> 6 to confirm it.**

That is mechanism 1 (findings-keyed: a clean round ENDS the ladder, never re-confirmed),
cited explicitly. **It is NOT the attribution gate**, and #1274's own round-4 ledger says so
in as many words:

> **Payload lines changed: 0.** Round 3's fix carried one executable payload line (the
> `|| die` guard), so this is **the first zero-payload round, not two consecutive** — the
> ladder continues under the findings rule.

So the session reasoned about the gate, correctly declined to fire it, and then stopped one
round later on findings. My own numbers agree with its ledger (my r4 = 0 payload / 34
scaffolding, `test_main_green_check.py` only).

### 🔴 #1132 is a THREE-WAY DISAGREEMENT, and I am not resolving it

I originally wrote that #1132 was "the one gate-firing instance". Three readings exist and
they do not agree:

| reading | verdict on #1132 |
|---|---|
| **This review** (`scripts/testlib/**` = scaffolding) | rounds 5 and 6 changed zero payload ⇒ **gate fires** |
| **An independent blind re-measurement** (`nix_units.py` substantively scaffolding but ~65/35 **ambiguous**; the skill's "ambiguous is not zero" tie-break then counts it PAYLOAD) | **gate does not fire**; #1132 r6 is a lone zero-payload round |
| **#1132's own posted ledger** | *"Stopped on the payload-attribution gate, not on a clean round. Rounds 6 and 7 both changed zero payload lines."* ⇒ **gate fires** |

**The PR's own ledger is internally inconsistent**, which is why its vote cannot simply be
counted. Its per-round numbers are *"round 5: 31 payload lines, zero executable"* and
*"round 6: 19 payload lines, ~4 executable"* — both non-zero — while its summary says those
rounds "changed zero payload lines". It is silently using **zero *executable* payload** in
the summary and **payload lines** in the ledgers. Those are different gates.

🔴 **The finding that outranks all three verdicts: the ladder classified the SAME FILE both
ways, one round apart.** Reconciling its ledgers against my file-level measurement (its round
labels run one ahead of its block labels):

- its **round 5** = 31 payload lines = md 7 + `flake.nix` 13 + `run-tests.sh` 11 — with
  `nix_units.py`'s **64 lines EXCLUDED** as scaffolding;
- its **round 6** = 19 payload lines — which are **`nix_units.py` ITSELF**, now counted as
  payload.

An arithmetic identity in both directions, one round apart. **The gate's unit is not stable
even within a single ladder**, which is a stronger and more useful result than any verdict
about whether #1132 fired. It also means #1132 ran to at least round 7 by its own numbering —
beyond the 6 blocks I extracted, so my depth for it is a floor.

**Recorded as an open disagreement with all three numbers rather than resolved.** What every
reading agrees on: **the attribution gate is near-inert — at most one firing across 309
merged PRs**, and on the reading I now find most defensible, zero.

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
control exactly. One correction to the brief's own reads: **#1304 carries 2 blocks** — it is
OPEN, not merged, which is why a merged-only scan misses it.

🔴 **CORRECTED 2026-09-05 — I claimed "#1237 carries 1 block in the PR BODY". It does not,
and the error was mixing two of my own instruments.** That number came from a LINE-grep for
the string `audit-claims`; #1237's body matches only in prose — *"`audit-dispatch.py` anchors
on the newest `audit-claims` block it can parse…"*. My own fenced-block scan never listed
#1237, and I quoted the line-grep over it. **The body surface contributes ZERO real blocks
across every in-window PR.** The single body "hit" my fenced scan did report is #958's, and
that is a false positive too: an illustrative example nested inside a **four-backtick
wrapper** (line 50 is ````` ```` `````, line 51 the inner `` ```audit-claims ``). Its range
`997375ec..9f638fd4` RESOLVES and yields plausible churn, so "does the range resolve" cannot
detect it — **counting unclosed fences before the match** can, and that is the check to use.

**The generalisation does not hold.** Over the whole window:

| measurement | value | as of |
|---|---|---|
| devrc PRs merged in window | **306** → **309** | 09-04 → 09-05 |
| merged PRs carrying ≥1 block | **41** → **42** | 09-04 → 09-05 |
| real blocks | **145** (all issue-comment) → **146** | 09-04 → 09-05 |
| body surface | **0 real** (1 false positive, #958) | both |
| review-comment surface | **0** | both |
| PRs with blocks incl. OPEN | **44**, 154 parsed headers, 1 unparseable (#958 round 1, `audited=abc41024` with no `..to`) | 09-04 |

The brief's "60 merged PRs" was a `gh pr list` cap artifact. The 309/42 population is proven
complete by `gh api --paginate` (1,227 closed + 42 open = 1,269 records reaching #1, max
#1319; the 50 gaps in 1…1319 are issues sharing the number namespace).

So the ledger mechanism **is** in active use — including on open PR #1304.

### 🔴 RETRACTED: the "no merged PR ≥1286 carries a block" drought

I wrote that **0 of the 19 merged PRs numbered ≥1286 carry a block**. That was true when
measured on **2026-09-04** and is **false by 2026-09-05**: **#1313 carries a real block**
(`audit-claims round=1 audited=316ccf74..7d94c568`).

**The claim decayed in 21 hours, and the mechanism is exactly this report's own thesis.**
#1313's block was posted **2026-09-05T06:03:38Z** and the PR merged **06:35:05Z** — both
*after* my run. So this is not an instrument gap: the datum did not exist when I measured.
(Note `merged: true` with `mergedAt: null` on the REST endpoint for a very fresh merge —
`.mergedAt` alone would have mis-classified it.) A dated measurement of a moving window
is not a property of the world, which is the claim this document opens with.

**And the real driver is change TYPE, not PR number.** By conventional-commit type across the
window:

| type | carriers / PRs | rate |
|---|---|---|
| `docs` | 4 / 175 | **2.3%** |
| `fix` | 19 / 71 | 26.8% |
| `feat` | 16 / 46 | 34.8% |
| `test` | 2 / 6 | 33.3% |

`docs` is **57% of the window**, and **145 of those 175 are `docs(handoff)`**. The apparently
carrier-free runs (#1003–#1043, #959–#987, #1084–#1107) each sit inside a single day and are
made of handoff docs. So there is no dead range and no decline — it is **"a kind of change
that never gets a ladder"**, which is a different and more actionable fact than "people
stopped posting ledgers".

One structural note that survives unchanged: **every real block is on an issue comment.**
`gh pr view --json comments` returns issue comments only, so it happens to see 100% of them —
but that is luck, not a safe method, and the body surface is where the false positives live.
Check all three surfaces, and parse fences rather than grepping for the string.

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
- **Only PRs that POSTED a claims block are in the waste audit.** 42 of 309 merged devrc PRs.
  Ladders that ran without posting a ledger are unmeasured — not zero. And per the
  change-type table above, `docs(handoff)` PRs almost never post one, so the unmeasured
  remainder is **not a random sample** of ladder work.
- 🔴 **Churn that falls in NO block's range is invisible to this method — a structural hole,
  not a sampling one.** Measuring each block's own `from..to` is correct per the header
  semantics, but it only covers the churn the blocks *chain across*. **#1233 posted blocks for
  rounds 1, 2 and 4** — its round-4 comment is titled "rounds 3 and 4", and round 3's fixes
  sit in `1b5d2e43..eb947328`, which **no block's range contains**. That churn is in no column
  of my table. #958 has the same shape (a 12-round ladder with 9 ranged blocks) but its ranges
  still chain end-to-end, so nothing is lost there. #1108 and #1219 both start at round 2, so
  their round-1 churn is likewise outside every range.
  🔴 **NOW MEASURED, 2026-09-11, and the hole was WIDER AND NARROWER THAN THIS BULLET SAYS —
  `scripts/ladder-range-coverage.py` over these same 20 ladders.** Three corrections, each
  re-derivable by re-running it:
  - **A SECOND interior gap existed and is named nowhere above: #998, round 1 → round 2,
    `34265904..0aecdbf2`, 2 commits / 131 lines.** So the interior hole — the unambiguous kind,
    where a round posted a block, a later round anchored past it, and nobody audited between —
    is **655 raw lines across 2 of 20 ladders** (#1233 524 + #998 131), not one ladder.
  - 🔴 **A WHOLE CLASS THIS BULLET DOES NOT MENTION IS SIX TIMES LARGER: the TAIL.** Churn
    after the LAST block is in no range either, and **11 of 20 ladders carry some — 3,727 raw
    lines**, led by #1046 (1,105), #1000 (987) and #1209 (672). ⚠ **It must NOT be quoted as
    unaudited ladder work.** It conflates fixes posted after the final block (which the ladder
    should have seen) with development that simply continued after the ladder ended (which it
    should not), and nothing in the ranges distinguishes them. The script prints interior and
    tail as separate totals for exactly this reason; read the split, never the 4,382 sum.
  - ⚠ **"9 ranged blocks" for #958 is 8.** Its round-1 block is a BARE `audited=<sha>`, which
    names no range at all — the same hole by a third route, and one with no reportable size.
  - ✅ **This bullet's claim about #958 otherwise HOLDS**: all 8 of its adjacencies are TIGHT,
    0 uncovered. #1219, #1286, #1120, #1181, #1207 and #989 are likewise 0 — a real negative
    control, since a dead detector would also print 0 for them.
  ⚠ **All of the above is RAW lines.** The payload/scaffolding split below was made by hand per
  PR and no pathspec can make it, so **this does not say the `19,230 / 32,393` figures are
  short by 655** — it says the ranges they were computed over missed that much churn.
  ⚠ **And three of the tail gaps are many commits at ZERO lines** (#1064 125 commits, #1274 10,
  #1110 7): `--not <base>` correctly excluding an upstream bring-in. A commit count is not a
  churn count — shape A of the reference file's range table, working.
- **The waste audit is devrc-only.** Ladders ran in homelab-talos, civit-datapacket-talos,
  vetr, auditloop, civitai-gpu-fleet and naida-ai; none were churn-measured.
  ✅ **MEASURED 2026-09-11 — and the population outside devrc is LARGER than devrc's.**
  `scripts/ladder-range-coverage.py --find-carriers` over each repo (limit 400, all states):

  | repo | PRs scanned | carriers | measured | refused | INTERIOR | TAIL |
  |---|---|---|---|---|---|---|
  | homelab-talos | 400 ⚠ hit limit | 68 | 67 | 1 | **88** | 7,314 |
  | civit-datapacket-talos | 400 ⚠ hit limit | 37 | 37 | 0 | **0** | 2,107 |
  | vetr (api) | 152 | 13 | 11 | 2 | **0** | 115 |
  | vetr (app) | 168 | 5 | 5 | 0 | **0** | 18 |
  | civitai-gpu-fleet | 282 | 6 | 6 | 0 | **0** | 498 |
  | naida-ai | 214 | **0** | — | — | UNMEASURABLE | — |
  | auditloop | **0 PRs** | 0 | — | — | UNMEASURABLE | — |

  **129 carriers outside devrc against 70 inside it** (devrc's newest 400 PRs, same command) —
  so the repo this review measured holds about a third of the ladder work, and the other
  two-thirds were unmeasured until now.
  🔴 **THE HEADLINE FINDING INVERTS WHAT devrc SUGGESTS: the interior hole is essentially a
  devrc phenomenon.** 655 uncovered interior lines across 20 devrc ladders, against **88
  across 126 external ones** — and all 88 sit in a single repo (homelab-talos, 2 adjacencies).
  Four of the five measured repos have **zero** interior gaps. The likely mechanism is a devrc
  *authoring* habit rather than a property of the ladder: titling one comment "rounds N and
  N+1" and posting a single block for both, which is exactly #1233's shape.
  🔴 **TAIL churn IS MOSTLY MISSED AUDIT SURFACE — classified 2026-09-11, and the earlier
  "probably ordinary development" reading was WRONG.** This bullet previously said the tail
  conflates post-final-block fixes with development that continued after the ladder ended and
  must not be quoted as unaudited work. The conflation is real; the *proportion* is not what
  was guessed. Read every commit in the five largest devrc tails (#1046, #1000, #1209, #1121,
  #998 — **3,349 of devrc's 3,727 tail lines, 17 commits**) plus the largest external one
  (homelab-talos #707, 1,276 lines):

  | what the commit is | commits |
  |---|---|
  | fixes / audit responses (one says *"audit round 5"* in its subject) | 9 |
  | **merge-conflict RESOLUTIONS** | 4 |
  | docs, handoff and a CI re-trigger | 3 |
  | **ordinary development (a feature)** | **1** |

  **One of seventeen is a feature.** homelab #707's single tail commit is *"withdraw the
  doc-orphan guard, keep the seven orphan fixes"* — an audit response too.
  🔴 **AND BY LINES THE BIGGEST SINGLE ITEM IS A SEMANTIC-CONFLICT RESOLUTION INSIDE A
  `merge main`: 1,039 of #1046's 1,105 lines.** Its own commit message documents renumbering a
  collided exit constant and rewriting 515/487 lines of one test file. That is exactly the
  hazard `claude/RULES.md` names — *a clean git merge is not a clean merge; a semantic conflict
  survives it* — landing in a region no audit round's range covered. `--remerge-diff` is what
  makes it visible at all (shape B of the reference file's range table, working as designed).
  ⚠ **SCOPE: this is the top of the distribution, not all of it** — 6 of 79 tail adjacencies,
  ~34% of the 13,779 tail lines. The remaining 73 are unclassified, and a long tail of small
  gaps may well be more development-heavy than these.
  ⚠ **Two repos are UNMEASURABLE, each for its own reason, and neither is a pass.** naida-ai
  ran 214 PRs and posted **no ledger at all**, so there is nothing to measure coverage
  against — "no ladders ran here" and "ladders ran without blocks" are the same observation
  from this instrument, and so is "they were fine". auditloop has no PR history in scope.
  🔴 **A COMMIT COUNT IN THE 2026-09-11 TABLE ABOVE WAS WRONG IN THE FLATTERING DIRECTION, and
  the same defect is in the SHIPPED BRIEF.** `measure_ledger` takes its commit count from
  `rev-list --count <frm>..<to>` and its line count from the numstat **`--not <base>`** — two
  different populations — and `audit-dispatch.py` printed the first as *"over N commit(s)"*
  directly beside the second's command. MEASURED on #1046's tail: **55 commits reported, 2
  contributing churn.** The other 53 were an upstream bring-in the churn correctly excludes.
  Corrected figures: #1046 55→**2**, #1000 32→**7**, #1209 12→**3**, #1064 125→0-churn,
  #1274 10→0-churn, #1110 7→0-churn. **Every LINE count in this review is unaffected** — the
  churn command always had the exclusion. `RangeChurn` now carries both counts under separate
  names and the brief prints both with the excluded number stated.
  ⚠ **Three caveats on the counts.** (a) Every carrier count is a **FLOOR** — `gh` does not
  return REVIEW comments, so a block posted as a review is invisible, the same blind spot
  `audit-dispatch.py` warns about. (b) Two repos **hit the 400-PR scan limit**, so their
  carrier counts are partial and the real totals are higher. (c) This measures **ALL**
  carriers, not this review's 2026-08-28 → 09-05 window, so it is a superset and not
  comparable to the table above row-for-row.

**Over-counts:**

- **IMPLIED depth assumes a ladder reaching round N ran N rounds.** In the window's depth-≥7
  slice: **121 rounds OBSERVED against 160 IMPLIED — implied is 24% high there.** Some of
  that is real (leading absences), but some is interior skips.
- **An "odd rounds only" ladder inflates depth.** devrc `056c842f` dispatched rounds
  `[3,5,7,9]` — audits on odd numbers, fixes on even. "Depth 9" there is four audit rounds,
  not nine.
- **A duplicate round number inflates the round count.** #958 posted **two** `round=3` blocks.

**Cannot see at all:**

- **Why a ladder stopped — NOT AT SCALE, though it is sometimes stated outright.** I first
  wrote that nothing in the record states why a ladder stopped. That is **too strong**:
  #1274 says *"per the stop rule the ladder stops, and I am not running a round 6 to confirm
  it"*, and #1132 says *"Stopped on the payload-attribution gate, not on a clean round."* The
  real limit is that this is **prose in a comment, extracted by hand for two PRs** — it was
  not mined across all 42 carriers, so no rate is claimed. Note also that a self-report is a
  claim by the session about itself: #1132's is internally inconsistent (above).
- **Whether a round was CLEAN — only where a block says so.** The churn measurement alone
  cannot distinguish a clean round from an uncommitted one; #1274 r5 is legible only because
  its block states *"No fixes were made this round: the audit returned no findings."*
  Cleanliness was not extracted at scale.
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
   into the reference file. It is the whole of the #1132 three-way disagreement, and #1132's
   own ladder called it both ways one round apart. *Closes when* the reference file states
   the call with its reason.
3. ~~Establish whether the ≥1286 block drought is real.~~ **CLOSED 2026-09-05** — it was not.
   #1313 carries a block, and the real driver is change type (`docs` 2.3% vs `feat` 34.8%),
   not PR number.
4. **Mine the stop-rationale prose across all 42 carriers**, rather than the two read by
   hand. This is the only route to a *rate* for "ladders that stopped on a stated mechanism",
   and it is also how #1157's escape-hatch requirement gets checked. *Closes when* the
   terminal round's summary is classified for every carrier and the rate is published.
5. ~~Fix the range-coverage hole.~~ **CLOSED 2026-09-11** — `scripts/ladder-range-coverage.py`
   reports it, per ladder, for the window `[first block's from, head]`, and was run over these
   20: **interior 655 lines in 2 ladders, tail 3,727 in 11**, with the two kept as separate
   totals because only the first is unambiguously unaudited ladder work. Findings are folded
   into the CANNOT-SEE bullet above, including two this item did not anticipate — a second
   interior gap (#998) and the tail class itself. The instrument is committed, with a mutation
   battery (`scripts/tests/mutants-ladder-range-coverage.sh`), *because this review's own churn
   instrument was a scratchpad variant that no longer exists* and none of its numbers can be
   re-derived from the tree today.
   ⚠ **Residual, which this item did NOT ask for and is NOT closed:** the window deliberately
   excludes churn BEFORE the first block, so a ladder whose ledger starts at round 2 (#958,
   #1108, #1219 here) still has its round-1 churn unmeasured. The script says so per ladder
   rather than inventing a number, because what "round 1" means for a ledger that begins at 2
   is a judgement about that PR, not arithmetic.
