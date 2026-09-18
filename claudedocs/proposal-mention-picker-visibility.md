# The mention picker's repo ranking is invisible in practice — measurement and options

Status: **proposal — three decisions, all the operator's.** Nothing here is applied.
The only thing shipped alongside this doc is INSTRUMENTATION (`ordering`, `tier_a`,
`tier_b`, `queried` on the click row); the picker's behaviour and its UX are unchanged.

> 🔴 **Privacy.** This repo is PUBLIC and the two files behind every number here —
> `~/.config/mention-open/known_ranges.json` and `picks.jsonl` — name **private
> repositories**. Nothing below is a repository name, an owner, a URL, a path or a
> hostname. Only counts, rates, date ranges and distributions. The replay that
> produced the accuracy table ran on the host that owns the log and returned
> aggregates; no file derived from either was copied into this repo.

---

## 0. The problem, restated

The two-tier ranker (Tier A: number-range plausibility; Tier B: learned picks) is
landed, runs, and demonstrably orders the list — measured live in §1.5, a `#1761` click
ranks **4** repositories above 391. The operator reports three symptoms anyway:

| # | Symptom | Status before this work |
|---|---|---|
| 1 | "I end up typing the repo name anyway" | Confirmed in code. Our order reaches fzf only as **input order**, which `--tiebreak=end` consults on an exact **tie**. The first keystroke hands ordering to fzf's own score. |
| 2 | "Wrong repo sits at the top" | Unmeasured. **Now measured — see §1.** |
| 3 | "The top rows are clawgate/pane guesses, not repos" | Confirmed in code. **Now quantified — see §2.** |

Symptoms 1 and 2 produced the **same telemetry row**, which is why neither could be
answered. That is what the instrumentation in this PR fixes.

---

## 1. Is the ranking any good? — MEASURED

**Method.** Causal offline replay: for each historical pick, score the universe using
only the picks *strictly before* it, then ask where the ranker would have placed the
repository the operator actually chose. Tier A reads the range table; Tier B reads the
prior picks at that row's own timestamp.

**Sample.** 109 picks, **2026-09-11 → 2026-09-18 (6.7 days)**, 10 distinct repositories,
reference numbers 86–4895, against a 394-row universe and a 394-entry range table.
Provenance split: 65 `picker`, 9 `auto`, 35 untagged (pre-`via` rows). 0 unparseable.

🔴 **It could only be measured on ONE of the two hosts.** `picks.jsonl` **does not
exist** on the other one, so Tier B is structurally inert there and *that host has no
pick history to replay at all*. Every number below describes the host that has a log.
What could **not** be measured: the other host's ranking quality (no ground truth
exists), and whether any of these picks was preceded by a typed query (nothing recorded
it until this PR).

### 1.1 Accuracy

| Configuration | top-1 | top-3 | mean rank | p90 rank |
|---|---|---|---|---|
| **Tier A + Tier B (shipped)** | **66.1%** (72/109) | **91.7%** (100/109) | 1.90 | 2 |
| Tier A only (Tier B disabled) | **76.1%** (83/109) | **94.5%** (103/109) | 5.17 | 1 |
| Tier A + Tier B, auto-opens excluded from the log | 66.1% | 92.7% | 1.83 | 2 |

Absent-from-universe: 0. top-10 is 99.1% with Tier B and 95.4% without.

### 1.2 The headline: **Tier B costs 10 points of top-1 and buys the tail**

Per-click comparison, Tier A alone vs Tier A + Tier B:

* Tier B **flipped a top-1 away**: 26 clicks
* Tier B **won a top-1**: 15 clicks
* rank improved 20 · **worsened 31** · unchanged 58

Net **−11 top-1 (−10.0 pp)**. But: on 18 of the 26 flipped clicks the right row moved
from position 0 to position **1** — one arrow-down — while the mean rank fell from
5.17 to 1.90, i.e. Tier B removes a long tail where Tier A alone buries the answer.

**Verdict: the weighting is defensible, and it is not obviously right.** It is a
deliberate-looking trade (small, cheap regression at the head; large win in the tail)
that nobody actually chose — it is a side effect of the sort key putting the score above
the `max_ref − num` distance term. A consumer reading top-1 alone would call it a
regression.

### 1.3 The parameters are **not** mis-tuned — the structure is

Sweeping the two constants the design flags as untuned:

| half-life (d) | proximity scale | top-1 | top-3 |
|---|---|---|---|
| 7 | 10 | 66.1% | 89.9% |
| 7 | 100 | 66.1% | 91.7% |
| 7 | 1000 | 66.1% | 89.0% |
| 30 | 10 | 66.1% | 89.9% |
| **30** | **100** | **66.1%** | **91.7%** ← shipped |
| 30 | 1000 | 66.1% | 89.0% |
| 90 | 10 | 66.1% | 89.9% |
| 90 | 100 | 66.1% | 91.7% |
| 90 | 1000 | 66.1% | 89.9% |

top-1 is **identical at every setting** across a 13× half-life range and a 100× proximity
range. (Positive control: top-3 *does* move, 89.0–91.7%, so the sweep can see a
difference — a flat top-1 is a finding, not a broken instrument.)

**So "the proximity scale or half-life is mis-tuned" is ruled out.** What decides top-1 is
*whether a repository has any non-zero score at all*: any prior pick floats a repo above
the `max_ref − num` distance tiebreak within its class, regardless of how the score is
weighted. Re-tuning the constants cannot move this number; changing the **sort key** can.

### 1.4 Tier A is doing nearly all of the work, and it is very sharp

* Class of the row the operator chose: **104 plausible, 5 below, 0 unknown, 0 impossible.**
* Size of the *plausible* class per distinct clicked number: **min 1, median 2, max 27**
  (over 80 distinct numbers, from a 394-row universe).

That median of **2** is the important number: after Tier A, the answer is typically one
of ~2 rows out of 394. The ranking is not the weak link.

### 1.5 The other host, measured live through the new instrumentation

Run on the host with **no** `picks.jsonl`, for reference `#1761`, reporting counts only:

```
universe rows = 395   ordering state = applied   table age = 0.28 d   table entries = 395
tier_a (rows the table can classify) = 395
tier_b (rows carrying a pick score)  = 0
class sizes for #1761: plausible 4 · below 159 · impossible 232
```

So on this host **Tier A classifies every row and Tier B is inert** — 4 repositories rank
above 391, and the ranking that produces that is Tier A's alone. This is the state the
operator may have been clicking in, and until this PR the row it emitted was byte-identical
to one from the host where Tier B *is* running. That asymmetry is the single strongest
reason the complaint could not be answered.

---

## 2. Symptom 3, quantified: how many rows sit above the ordered block

Measured through `main()` on the two commonest ambiguous shapes (and now pinned by
`test_the_rows_ABOVE_the_ordered_block_are_pinned_BY_KIND_and_COUNTED`):

| Shape | rows pinned above the ranked block | what they are (by kind) | first ranked row is at |
|---|---|---|---|
| bare `#N`, nothing attributes it | **1** | clawgate task | row 1 |
| bare `#N`, the tmux pane attributes it | **2** | clawgate task, pane-guessed GitHub row | row 2 |

So **`rank = 0` is unreachable for a ranked row on either shape.** Combine that with
§1.4 — the answer is usually 1 of ~2 plausible rows — and symptom 3 and symptom 1 are
plausibly *the same complaint*: the operator looks at the top of the list, sees a
clawgate row and a pane guess, concludes the ordering is not working, and types.

### The original rationale for pinning, from the code

Both arms say the same thing in their own comments, and it is a real argument:

* Dead end 2 (`mention-open.py`, the bare-`#N` arm): *"The clawgate candidate STAYS
  FIRST so the common case is still one Enter away; the universe is appended as the way
  to say 'no, GitHub, this repo'."*
* The guessed arm: *"THE MEASURED ROWS STAY ON TOP, and that is the whole reason this is
  an APPEND rather than a replace. The guess is the most likely answer and it stays one
  or two Enters away, so the common case does not get slower; the universe below it is
  what makes the uncommon case possible at all."*

**Does it still hold?** Partly. The pinned rows are *evidence about the reference*
(a clawgate task with that id; a repository the pane names) while the ranked rows are
*options*, and that distinction is sound. What has changed since is that the ordered
block is now good enough (§1.4) that the rows immediately under the pins are usually the
answer — so the cost of the pinning is no longer "one Enter", it is "the top of the list
does not look like the ranking is working".

---

## 3. Options

### Symptom 1 — fzf re-sorts the moment the operator types

What is already **measured and settled**. ⚠ These two are **not this doc's measurements** —
they were taken 2026-09-11 and are pinned by
`test_adding_index_to_the_TIEBREAK_would_change_NOTHING_on_a_TYPED_query` and
`test_REAL_fzf_lets_our_PRECOMPUTED_ORDER_decide_a_TIE`, which is where the numbers live.
Quoted here so nobody re-derives them; read the tests, not this line, if they matter:

* `--tiebreak=end,index` is a **no-op**: 520 of 520 (corpus, query) pairs byte-identical
  to `--tiebreak=end`; positive control `--tiebreak=length` differed on 438. fzf appends
  `index` implicitly.
* Input order decides an **exact tie** and nothing else.

One measurement here **is** this doc's own: `--print-query` does **not** change fzf's
ranking — same corpus, same query, byte-identical output once the query line is dropped,
with `--tiebreak=length` as the positive control (fzf 0.74.3).

So there is no tiebreak flag to add. The real options:

**(A) Make the class/rank visible in the row text.**
Prefix each universe row with its Tier A class (and/or its rank), e.g. a short marker
before the existing `github <N> — <url>` text.
*Pros:* survives a typed query — the marker is in the row, so a narrowed list still shows
which rows the ranker likes; costs no behaviour change; no new disclosure surface (a class
name is not a repository name); reversible in one line.
*Cons:* does not make fzf *order* by our rank, it only makes our opinion legible — an
operator who types a 3-char query still gets fzf's order among the matches; row width is
finite and the picker is 120 cols; a marker that is fuzzy-matchable can itself perturb
fzf's scoring (a leading token matches queries), so it would need to sit where the match
cannot reach it or be measured not to.

**(B) `--no-sort`.**
fzf then filters by the query but preserves input order among the survivors, so our rank
survives typing completely.
*Pros:* the only option that actually makes the pre-computed order the final arbiter
under a query; one flag.
*Cons:* throws away fzf's match-quality ordering — type `civitai` and you get *our* order
among matches rather than the best match first, which on a 394-row universe is a real
loss when Tier A is UNKNOWN for the rows involved. The suite currently bans this flag as
"turns off ranking entirely, which is the pre-#1373 bug" — that ban was written when there
was no pre-sort; now there is one, so the flag means "our ranking wins" rather than "no
ranking". **It is a genuine trade and it should be decided on data, not on the old ban.**

**(C) Do nothing yet, and read the `queried` rate first.**
The instrumentation in this PR now records, per click, whether a query was typed. If most
picks are made with an **empty** query, symptom 1 is rare and A/B are not worth their
cost. If most are typed, the rate says which.
*Pros:* the cheapest correct order of operations, and the only one that does not guess.
*Cons:* costs a few days of clicks before anything changes.
🔴 *And it has a measured blind spot — read §3.1 before relying on the rate.*

**(D) `--bind 'esc:print-query+abort'` — close the abort blind spot.** See §3.1.

#### 3.1 🔴 `queried` does not cover every click — measured, not assumed

Driven through a pty against **fzf 0.74.3**, three samples per ending plus a Ctrl-C and a
positive control:

| ending | what fzf writes | `queried` |
|---|---|---|
| a selection (PICKED) | `<query>\n<row>\n` — query may be empty | **present** |
| ENTER, query matched nothing | `<query>\n`, exit 0 | **present** |
| ESC / Ctrl-C abort | **nothing at all**, 0 bytes, exit 130 | **absent — NOT MEASURED** |

`--print-query` covers the two endings where fzf has a result to print; an abort is not
one of them. **The first draft of this work asserted that an abort writes the query
alone, in three code comments and the PR body. It does not.** Corrected, and pinned by
`test_a_real_ABORT_records_NO_query_verdict_at_all`.

Consequences for reading the data:

* The **picked** rate — which is what symptom 1 is actually about — is complete.
* The **dismissal** arm's `queried` rate covers the Enter-with-no-match ending only. That
  ending is the most diagnostic dismissal there is ("I typed the repo name and the list
  went empty" — the exact case `pick()`'s docstring has always named as unanswerable), so
  it is not a trivial slice; but do **not** read it as "of all dismissals".
* Three-valued is what keeps this honest. A `False` default would file every abort under
  "the operator scrolled", which is the population the whole question is measured from —
  wrong in the reassuring direction.

**Option (D)**, if the abort gap matters: `--bind 'esc:print-query+abort'`. **Measured:
it works** — Esc then writes `<query>\n` (Ctrl-C still writes nothing), the picker closes
exactly as before, and nothing on screen changes. It was **not** taken in this PR because
it rebinds a key on the live click path, and that is the operator's call. It is one flag,
reversible, and the pinned-string/metacharacter guards already cover `PICKER_SH`.

**Recommendation: (C) then (A).** (C) because the question is now measurable and was not
before; (A) because it is additive, reversible, and helps *both* while the operator
scrolls and after they type. (B) is worth keeping on the table but should not be taken
before the `queried` rate is known — it trades away the fuzzy narrowing that makes a
394-row picker usable at all.

### Symptom 2 — is the weighting defensible?

Yes, with a caveat, and the evidence is §1.2–§1.4. Two candidate changes, both
**not recommended without the operator's call**:

1. **Leave it.** Tier B's cost is one arrow-down on ~24% of clicks and its benefit is a
   mean rank of 1.90 instead of 5.17. On a 6.7-day, 10-repository sample that is a
   defensible trade.
2. **Move Tier B below the distance term** in `order_universe`'s sort key
   (`(klass, distance, -score)` instead of `(klass, -score, distance)`). On this sample
   that recovers the 10 points of top-1 while keeping Tier B as the tiebreak among rows
   the distance term cannot separate. **Untested** — the replay above measured
   *Tier B on/off*, not *key reordering* — so it is a hypothesis with a measurement
   attached, not a recommendation.

Do **not** re-tune `PICKS_HALF_LIFE_DAYS` or `PICKS_PROXIMITY_SCALE`: §1.3 shows top-1 is
completely insensitive to both.

⚠ Sample caveats, stated rather than buried: 109 picks over 6.7 days on one host, 10
distinct repositories, and the range table used for the replay is **today's**, not the one
in force at each pick. A `stale` table would have produced an unordered list that the
replay scores as if it were ordered, in the *optimistic* direction.

### Symptom 3 — should the pinned rows still sit on top?

🔴 **This is the operator's call, not mine.** The rationale in §2 is real and the cost is
now real too. Three positions:

1. **Keep the pinning.** The clawgate row is *evidence*, the universe rows are *options*,
   and a bare `#N` most often means the clawgate task. Cost: the first ranked row is never
   at the top, so the list never *looks* ranked.
2. **Put the top-ranked repository row above the pane guess** (not above the clawgate
   row). The pane guess is the weakest rung of the attribution ladder — the module's own
   comment calls it "evidence about the WINDOW" — while the top ranked row is evidence
   about the reference. This is the smallest change that makes the list look ranked, and
   it only demotes a row the code already refuses to auto-open.
3. **Interleave by class:** put ranked rows whose class is `plausible` above the pane
   guess, and everything else below it. Most targeted; most machinery.

**My recommendation is (2)**, weakly, and only after the `queried`/`rank` data from this
PR has a week of clicks behind it — because if it turns out the operator almost always
picks the clawgate row on a bare `#N`, (1) is simply correct and nothing should move.

### The test gap named in the brief

*"Nothing pins how many rows sit above the ordered block on the common bare-`#N` shape."*
**Closed in this PR**, because it is the exact seam where "the ranking works" and "the top
of the list is ranked" diverge: `test_the_rows_ABOVE_the_ordered_block_are_pinned_BY_KIND_
and_COUNTED` pins the count **and the kinds** on both shapes, and asserts that the first
ranked row's absolute rank is **not** 0. It is an *invariant guard* — it pins behaviour no
bug has violated — but it is the guard any of the §3 symptom-3 options must go red
against, which is the point of having it before the decision rather than after.

---

## Appendix — what was NOT measured

* The ranking quality on the host with no `picks.jsonl`. There is no pick history there,
  so there is no ground truth to replay; an absence is not a score.
* Whether any historical pick was preceded by a typed query. Nothing recorded it. The
  `queried` dim shipped in this PR is what makes the next one answerable.
* Whether reordering `order_universe`'s sort key recovers the top-1 (§ symptom 2, option 2).
* Whether a visible class marker perturbs fzf's own scoring (§ symptom 1, option A).
* The range table in force at each historical pick — the replay used today's.
