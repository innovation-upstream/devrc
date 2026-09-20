# Porting `prune-memory`, `prune-skill` and `the-algorithm` into `/handoff` + `/resume`

Proposal, 2026-09-20. Scope set by the operator: **doc-corpus lifecycle** and **the guard
stack**, deletion on the table, changes land **inside the two existing skills** — no fourth
`prune-*` sibling.

> **STATUS, 2026-09-20 (updated after implementation started — read before acting on §4):**
> **P4 is MERGED** to `handoff-resume-prune-proposal` (`b50709ca`), **minus its `/resume`
> half, which was deleted** — `/resume` is read-only re-entry and a corpus-maintenance
> pointer there is prose nobody acts on (the-algorithm §1).
> 🔴 **P2 is REFUTED AND DELETED — do not build it.** Measured below. Its premise (gotcha
> bullets go stale unnoticed) is false in this corpus: **max age 39 days, zero over 90**, and
> at the 14-day window used for investigations **55% of bullets would flag** — against the 3%
> that design accepted and the 18% it rejected as "a gate everybody clicks through".
> The archive rule that already exists keeps live docs young, so P2 would have duplicated a
> working mechanism.
>
> 🔴 **P1 AS WRITTEN IS RETRACTED; P1′ SHIPPED** (`97fee3d0`). `claudedocs/refs/` is
> measurably outside the `handoff_search` corpus, and the authoritative playbook forbids
> moving gotchas or open threads there for exactly that reason — so "demote the APPEND
> buckets" meant "delete, for retrieval". The corrected item is the playbook's own step 1:
> `budget_warning()` now prints THIS doc's evictable backlog. Verified live.
>
> 🔴 **P3 IS DELETED TOO** — see its section. The remedy and the exact paste-ready syntax are
> ALREADY printed by the `DOD` block at the right moment; the only increment left is
> auto-filling the arc's finish line, which is the judgement that defines the arc.
>
> **ALL FOUR ITEMS ARE CLOSED. Nothing in §4 remains to build.** Final tally: one shipped as
> specified, one shipped corrected, two deleted by measurement.

---

## 0. TL;DR

The three prune/algorithm skills each contribute one thing the handoff chain lacks:

| from | the transferable part | what it becomes here |
|---|---|---|
| `prune-memory` | a file read **on every use** needs a *target*, not just a cap, and a demotion sink | a per-doc target + `claudedocs/refs/<topic>.md` demotion |
| `prune-skill` | §0 staleness-first, §3 DEMOTE + one routing line, §5 **verbatim line-range slicing** | the eviction *primitive* — move, never delete |
| `the-algorithm` | §1 name the maker and the recurrence; §5 the fix for over-guarding is never another guard | the measurement below, and the constraint on this proposal |

**The single number that decides the design: 70% of the live corpus sits in the three
`APPEND_PREFIXES` buckets, which by construction never shrink.** `## Gotchas` alone is 42.6%.
The `REPLACE` sections are self-limiting and are not the problem.

**And the guard stack is not over-built.** Every refusal in `handoff_doc.py` has fired in
production. The-algorithm §2 finds almost nothing to delete there — which was not the answer
expected going in, and is stated here rather than quietly dropped.

---

## 1. Instruments, and two corrections to my own earlier claims

Three measurements, each with controls, because every reassuring answer below is a zero or a
ratio.

**(a) Guard firing — provenance-separated.** `audit-rule-firing-sweep.py`'s lesson applies
directly: a naive corpus grep for `status=unforced` counts **skill loads**, not firings, since
that string lives in `claude/skills/handoff/SKILL.md` and is injected into every transcript
that loads `/handoff`. So a firing is counted **only from tool output** (`tool_result` /
`toolUseResult`), never from model prose or injected bodies, and deduplicated by
`tool_use_id` (stable across resumes and compaction).

- Population: 1,328 transcripts naming `handoff_doc.py`, of 6,626 on this host.
- Denominator: **7,023 distinct invocations**; 5,095 distinct status-bearing results.
- Negative control: `status=zzz-not-a-real-status` → **0**. Positive control:
  `proposed`+`written` → **3,124**.
- Dedup was load-bearing: the naive count was ~8,500 matches, **40% of them copies**.
- The false instrument, for contrast: the same tokens counted from assistant prose score
  `behind` 136, `proposed` 344 — numbers that describe the skill body, not the tool.

**(b) Closing-condition coverage.** 🔴 **My first pass used a regex of my own and was
WRONG** — it scored `handoff-cairn-phase3.md` as declaring none while the doc names the field
19 times. Re-measured through the authority, `handoff_doc.closing_condition().is_declared`,
with controls built from **real corpus text** rather than a synthetic fixture (the synthetic
one failed the control: the function walks `split_sections`, so a fixture with no `## `
heading cannot declare anything).

**(c) Section byte-weights.** Computed with `handoff_doc.split_sections` +
`handoff_doc.append_bucket` — the tool's own classifier, not a heading grep.

### Retracted
- ❌ *"The archive sink exists and nothing routes to it."* **False.** Only 6 of 97 live docs
  are untouched >30d and **0** are >60d. The age-based archive rule is working. Dead documents
  are not the problem.
- ❌ *"82% undeclared" derived from my own regex.* The figure survived re-measurement through
  the authority, but the first derivation was invalid and is replaced by (b).

---

## 2. What is already built — do not re-propose

| thing | state |
|---|---|
| `scripts/handoff-audit.py` | exists; explicitly "the `/handoff` analogue of `skill-audit.py`"; **pure measurement, no edits** |
| `claudedocs/archive/` | exists, 35 docs (#1627), resolved by `resume-state.sh` |
| `claudedocs/refs/` | exists, 592 KB — the demotion sink |
| per-doc ceiling | `handoff_budget.MAX_BYTES` 65,536 B + an 11-entry grandfather ratchet |
| eviction ladder | prescribed in `handoff_doc.budget_warning()` |
| `claudedocs/proposal-handoff-doc-bloat.md` | 2026-08-29; already argued "prune-skill's method, one level down" |
| archive-is-not-exempt | already reasoned and decided in `test_handoff_doc_size.py` — I checked this as a suspected sink/gate conflict; **it is not one** |

**The gap is routing, not capability.** Grep-verified: neither `handoff/SKILL.md` nor
`resume/SKILL.md` mentions `handoff-audit.py`, `archive/`, `refs/`, or any doc budget. The
only pressure on a growing doc is `budget_warning()`, which fires at the 64 KB ceiling — i.e.
**after** the ×2.55 growth `handoff-audit.py` measured has already happened.

---

## 3. Findings

### F1 — 70% of the corpus is in buckets that cannot shrink

97 live docs, 3,264,227 B in sections:

| bytes | share | bucket |
|---:|---:|---|
| 1,389,966 | **42.6%** | `APPEND: gotchas` |
| 879,515 | **26.9%** | `APPEND: open investigations` |
| 305,081 | 9.3% | `REPLACE: next steps (ranked)` |
| 233,155 | 7.1% | `REPLACE: state now` |
| 141,383 | 4.3% | `REPLACE: how to verify` |

**APPEND total: 2,279,835 B = 70%.** The `REPLACE` buckets are overwritten each update and
self-limit; the three `APPEND` ones are unbounded *by design* and the design is correct — the
value of a ruled-out theory is that it survives verbatim. There is simply no counterweight,
which is what `handoff-audit.py`'s own header says and what its growth series shows
(123 revisions: 121 grew or held; 16 of 18 docs never shrank once; ×2.55 first→latest).

### F2 — the problem is live docs growing, not dead docs lying around

- 6 of 97 live docs untouched >30d; **0** >60d; 23 touched in the last 7 days.
- Largest doc: `handoff-audit-pr-ladder.md`, **194,314 B, touched 5 days ago**.
- 11 docs over the 64 KB ceiling hold **1,224,807 B — 37% of the corpus in 11% of the docs.**
- Median 20,255 B, mean 34,014 B.

So the eviction trigger cannot be age or archival. It has to fire **inside a document that is
still being worked**, which is exactly what `prune-memory` does for `MEMORY.md` — a file that
is also live, also appended to, and also read in full every time.

### F3 — `resume`'s DoD verdict is inert for 4 in 5 docs

`closing_condition().is_declared` over the 97 live docs:

- **80 declare none = 82%**, holding **73% of the bytes**.
- Among the 20 biggest docs: **14 of 20 undeclared**.
- Of the 17 that declare one: 12 `check`, 5 `judgement`.

`/resume` step 5 mandates the DoD verdict **as the report's headline**, and for 82% of docs
the only honest verdict available is **UNMEASURABLE**. The refusal that would fix this
(`undefined-done`) is grandfathered for pre-existing docs by design — correct when it shipped,
but it means the corpus never converges. **Any archive-on-close design built today would be
inert on 73% of the bytes**, which is why it is not item 1 below (it was, before this
measurement).

### F4 — the guard stack is defended; the unanswerable *warning* is not

🔴 **READ THE DENOMINATOR BEFORE THE TABLE — an earlier wording got it wrong.** The `%`
column is over the **5,095 distinct status-bearing tool results**, NOT over the 7,023
invocations named above: `written` is 1788/5095 = 35.1%, and 1788/7023 = 25.5%. This line
previously read "deduped firings over 7,023 invocations" directly above the table, so a
reader recomputing any row from the stated denominator got a different number
(round 0 of #1815, F5). The two counts differ because not every invocation prints a
`status=` line a transcript captured.

⚠ **SCOPE OF THIS SWEEP, which an independent re-run will NOT reproduce:** population =
the **1,328 transcripts whose text names `handoff_doc.py`**, not all 6,607 on the host;
matched on the literal `status=<token>` in tool output only, deduped by `tool_use_id`.
A wider sweep returns **higher** counts (round 0 measured ~2,080 `durable-drop` against
this doc's 915), and the direction is **not** a clean population effect in either
direction: a `Read` of any document QUOTING these warning strings — including this
proposal and its handoff — is itself a tool result carrying the text, so a corpus-wide
literal sweep counts document reads as firings. **Neither figure is clean.** What both
agree on, and what the argument below rests on, is the ORDER OF MAGNITUDE and the
ranking — every refusal has fired, and `durable-drop` fires far more often than the rate
the investigations design rejected.

Firings by status — see the two paragraphs above for the denominator and the scope:

| status | n | % | | status | n | % |
|---|---:|---:|---|---|---:|---:|
| `written` | 1788 | 35.1% | | `failed` | 252 | 4.9% |
| `proposed` | 1336 | 26.2% | | `unforced` | 185 | 3.6% |
| `stale-base` | 350 | 6.9% | | `dated-topic` | 158 | 3.1% |
| `new-doc` | 261 | 5.1% | | `no-change` | 134 | 2.6% |
| `behind` | 259 | 5.1% | | `push-failed` | 110 | 2.2% |
| `rank-growth` | 87 | 1.7% | | `unevidenced` | 83 | 1.6% |
| `undefined-done` | 51 | 1.0% | | `no-advance` | 31 | 0.6% |
| `doc-per-effort` | 10 | 0.2% | | | | |

The-algorithm §1 asks each requirement to name its maker and its recurrence. **Every one of
these can.** The rarest, `doc-per-effort` at 0.2%, guards the operator's own 2026-08-28
decision and each firing prevents a duplicate document; `stale-base` at 6.9% guards an
incident that would have destroyed ~601 lines of a document. §2 says delete what §1 did not
defend — **here that set is empty, and this proposal deletes no refusal.**

The finding is the other column:

| warning | n | % of runs | answerable? |
|---|---:|---:|---|
| `DROPS N line(s) that look DURABLE` | **915** | **18.0%** | **no** |
| over/at the size budget¹ | 185 | 3.6% | only by hand, at 64 KB |
| near the size budget | 28 | 0.5% | as above |

⚠ Same sweep, same scope caveat as the status table above — these are a FLOOR from the
1,328-transcript population, and a corpus-wide literal sweep returns higher numbers it also
cannot clean (document reads count as firings). ¹ This row matched **both** the gated
(`OVER ITS SIZE BUDGET`) and ungated (`SIZE ONLY, NO GATE`) arms, so it is not comparable to
a gated-only count.

**Nearly one run in five is told it is dropping durable content, by a warning that is
explicitly "a WARNING, never a refusal" and explicitly "a FLOOR".** `claude/RULES.md`:
*a permanently-red gate is worse than no gate — it trains everyone to click through.* An
18%-firing advisory with no action attached is that gate. It is the one place in this chain
where the-algorithm's step 2 has a real target — **and the answer is not to promote it to a
refusal** (§5: the fix for over-guarding is never another guard), but to make the drop
*recoverable*, which item 1 does for free.

---

## 4. Proposal

Four changes, ranked. Each names its source skill and the finding that forces it. All land
inside `/handoff` + `/resume` and their two existing tools; **no new skill, no new gate, no
new ratchet.**

### P1 — Demote the APPEND buckets into `claudedocs/refs/<topic>.md`, by verbatim slicing
*From `prune-skill` §3 (DEMOTE_TO_REFERENCE + one routing line) and §5 (verbatim line-range
slicing). Forced by F1.*

`handoff_doc.py` gains a demotion pass on the merged text. When an `APPEND` section exceeds a
per-section target, its **oldest** blocks are moved — by python line-range slice, never
retyped, never reordered — into `claudedocs/refs/<topic>.md`, leaving one routing line in the
doc:

```
## Gotchas
<the N most recent blocks stay here>
📖 23 earlier gotchas (2026-07-14 → 2026-08-30): claudedocs/refs/<topic>.md#gotchas
```

Why slicing rather than summarising: it makes content survival **structural** instead of
trusted — `prune-skill` §5 records two real losses from summarise-then-drop, and the loss mode
looks exactly like good pruning. It is also the direct answer to `budget_warning()`'s own
*"do NOT satisfy it by deleting an open investigation"*: nothing is deleted, so the
instruction and the mechanism stop disagreeing.

This also disarms F4's 18% warning without adding a guard: a durable line that is *moved* and
routed-to is no longer a line that "will be deleted on the next update".

**Blast radius: this rewrites documents.** Mitigations required, from `prune-memory` §2 and
`prune-skill` §5/§7: back up before the cut with `&&`-chained verification and a file count;
gap-audit the union (every source line present in doc ∪ refs) before writing; and the existing
two-run `proposed` → `--confirm --push` shape stays, so the diff is in the transcript.

### P2 — ~~Give the APPEND buckets a clock~~ 🔴 REFUTED AND DELETED, 2026-09-20

**Do not build this.** Kept, struck through, because the measurement that killed it is worth
more than the proposal was — and a deleted section reads as an oversight.

Two findings, in order:

1. **Stamping was the wrong mechanism, and the code I was about to mirror says so.**
   `resume-state.sh`'s own comment records that the introducing-commit pickaxe dated
   **478 of 478** investigation blocks: a stamp is the most PRECISE clock, never the only
   one. Stamping 3,732 gotcha bullets at ~12 B each would have **added 44,784 B to a
   3.3 MB corpus this proposal exists to shrink**.
2. **Then the premise itself failed.** Aged every one of those 3,732 bullets by `git blame`
   (85 ms/doc, one call; validated against the pickaxe on the oldest content — 38d vs 38d,
   exact agreement): **p50 16.5d · p90 28d · max 39d · zero over 90d.** At the
   investigations window of 14d, **55% would flag**. That design chose 14d *because* it
   flagged 3% and explicitly rejected 18% as "a gate that fires on a fifth of every doc is
   one everybody clicks through". 55% is worse than the option it rejected.

Why the corpus is young: the **archive rule already works** (F2 — 0 live docs >60d). P2
would have rebuilt a freshness mechanism on top of one already doing the job.

⚠ **This also retracts the proposal's claim that P2 was a prerequisite for P1.** It is not:
P1 runs at write time inside `handoff_doc.py`, which can blame the doc itself.

<details><summary>the original P2 text, for the record</summary>
*From `prune-skill` §0 — "a prune preserves rot BY CONSTRUCTION". Forced by F1 + the fact that
only `### ` blocks under Open investigations carry `as-of`.*

`## Gotchas` is 42.6% of the corpus and has **no clock at all**, so `resume` cannot age it, and
P1 cannot choose what to demote without one. Extend the existing `stamp_investigations()`
stamping to the other two APPEND buckets, then have `resume-state.sh` report their ages
alongside the `INVESTIGATIONS` block it already prints — same `EXPIRED` (a `-` finding) /
`UNDATED` (a `!` gap) vocabulary, which is already built and already understood.

P2 is a prerequisite for P1 choosing correctly, and is independently useful: a 2026-07 gotcha
presented in the present tense is the failure `resume` step 3 already warns about three times.

</details>

### P3 — ~~Make the DoD field reachable on the 82%~~ 🔴 DELETED, 2026-09-20

**Probe run, hypothesis confirmed, item deleted anyway — and the two facts are compatible.**

The pre-registered probe asked whether grandfathering explains the 82%. It does, decisively:

| cohort (live docs, by first-commit date) | n | declare a closing-condition |
|---|---:|---:|
| born **before** rule (m) landed (2026-09-14, #1646) | 84 | **6%** |
| born **on/after** | 14 | **93%** |

And the legacy cohort does NOT self-heal: **92 of 98 docs were touched within 30 days while
only 18 declare one**, because the grandfather arm is `NEITHER had one → ""` forever, however
many updates follow. So ~21 actively-worked docs are permanently exempt. That is a real
target, and it is why the hypothesis is recorded as CONFIRMED.

**P3 is deleted on a different ground, found by looking at what the tool already prints:**

```
  🔴 this handoff declares NO closing-condition — … UNANSWERABLE here …
     Written before rule (m). Add one in a `## Goal` delta:
     `closing-condition: check|judgement — <the thing itself>`.
```

The fact, the remedy, the exact syntax and the vocabulary are **already on screen at the
moment of the decision** (verified live against `handoff-cross-host-routing.md`). The only
increment P3 could add is pre-filling the *detail* from the doc's `## Goal` — and that detail
IS the arc's finish line, the one judgement rule (m) exists to make a human state. Generating
it would mint a finish line nobody asserted, the same failure `/handoff` step 1 already
forbids for `clawgate-task:` ("a task minted to fill a blank field is a fact nobody
asserted"). **There is nothing left here that can be honestly automated.**

⚠ And the grandfathering is CORRECT, not a defect to close: refusing every pre-rule document
would go red on the first update to all 84, which `claude/RULES.md` calls worse than no gate.

<details><summary>the original P3 text, for the record</summary>
*From `prune-memory` ("archive the moment it ships") — but F3 says the trigger must be built
before the archival it would drive.*

Rather than a new refusal (§5 again), make it a **one-line offer in the flow**: `/resume` step
5 already must report UNMEASURABLE and "offer to add one as the first item" — it currently says
so in prose that 82% of runs apparently do not act on. Make `resume-state.sh`'s `DOD` block
emit the exact `closing-condition:` line to paste, pre-filled from the doc's `## Goal`, and have
`/handoff` step 5 accept it as an ordinary update. Measure the coverage figure again in two
weeks; **if it has not moved, the prose was never the problem and the offer should be deleted
rather than strengthened.**

</details>

### P4 — Route the two skills to the auditor that already exists
*From `prune-memory` §1 / `prune-skill` §1 — audit first, deterministically, before any cut.*

One line in `/handoff` step 5 and one in `/resume` step 2 naming `scripts/handoff-audit.py`,
so the eviction decision is taken against numbers. This is the cheapest item and the one that
makes P1 auditable rather than felt.

### Not proposed, deliberately
- **No `/prune-handoff` skill.** the-algorithm §5, and the skill-listing budget: a new skill
  needs a tier entry and an eviction in the same commit.
- **No deletion of any refusal.** F4: all 15 are defended by firing.
- **No promotion of `durable-drop` to a refusal.** §5. P1 removes its cause instead.
- **No archive-on-close wiring yet.** F3: inert on 73% of bytes until P3 lands.
- **No pruning of the two SKILL bodies.** Out of the scope the operator set, and `resume`'s
  ceiling is pin-bound, not prose-bound.

---

## 5. Implementation plan

| # | change | files | risk | status |
|---|---|---|---|---|
| P4 | routing line (handoff only) | `handoff/SKILL.md`, `handoff-audit.py` | none | **MERGED `b50709ca`** |
| P2 | stamp + age APPEND buckets | — | — | 🔴 **REFUTED, deleted** |
| P3 | paste-ready DoD offer | — | — | 🔴 **DELETED** — remedy already printed |
| P1′ | per-doc evictable backlog in `budget_warning()` | `handoff_doc.py`, `handoff-audit.py` | none (prints only) | **MERGED `97fee3d0`**, verified live |

**Nothing remains to build.** One shipped as specified (P4), one shipped corrected (P1′), two
deleted by measurement (P2, P3).

🔴 **This arc's closing condition cannot be met as written and should not be re-aimed.** It
was frozen at round 1 as "P1 merged AND the APPEND share below 70%" — but the demotion is
forbidden, and the APPEND-share metric can only fall by moving searchable content into an
unsearchable sink. **A byte-share target selected the harmful action as the cheapest way to
satisfy it.** The honest verdict is NOT ADDRESSED-as-written; the work that replaced it
belongs to a new arc. When freezing a closing condition, ask which action most cheaply
satisfies the metric, and whether you would accept that action. Each is its own PR with test coverage; P1 gets a mutation battery
in the shape of `mutation_battery_handoff_archive_and_cap.py`, since its reassuring answer is
a zero ("0 lines lost"), and a zero is indistinguishable from a detector wired to nothing.

⚠ Both SKILL.md bodies are already over target (handoff 20,088 B, resume 20,731 B, budget
12,038 B) and each is ratcheted by its own size test. **P1/P2/P4 all add prose to them**, so
each PR must carry its eviction in the same commit — most likely into the `reference/` dirs
that both skills already route to.

- **closing-condition:** `check` — P1 merged, and `handoff-audit.py` reports the live corpus
  APPEND share below 70% on a re-run. Until P1 lands, this arc is open regardless of how many
  of P2–P4 have merged.
