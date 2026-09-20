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

🔴 **It could only be measured on ONE of the two hosts — and that host is where every
observed click happens.** `picks.jsonl` does not exist on the other one, so it has no pick
history to replay; **measured, it has also never emitted a single click row** (§1.5). So
this is not a half-measured population: the replay covers **100% of observed clicks**.
What could **not** be measured: whether any of these picks was preceded by a typed query
(nothing recorded it until this PR), and what would happen if the operator started
clicking on the other host.

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
above 391, and the ranking that produces that is Tier A's alone.

🔴 **BUT THE OPERATOR WAS NOT CLICKING HERE, AND AN EARLIER DRAFT OF THIS SECTION SAID
THEY MIGHT HAVE BEEN.** It called the two-host asymmetry "the single strongest reason the
complaint could not be answered". **Measured against the activity dataset: all 85
`mention-open` click rows ever recorded — 2026-09-12 to today — came from the host that
HAS the pick log. Zero from this one.** Both rival explanations for that zero were ruled
out rather than assumed: this host emitted **20,649** other tool-invocation rows in 30
days (so its telemetry is alive, most recent minutes ago), and its checkout's emitter
imports and emits. The clicks simply do not happen here. And `host` is auto-filled on
every v1 spool line anyway, so the two-host case was already attributable by a column
that predates this work.

**Two things follow, and the second is good news the first draft buried:**

1. The cross-host argument is **withdrawn**. `tier_b` earns its place on a narrower claim
   — it tracks how much learned preference was in the sort **on the emitting host, over
   time**, which nothing else on the row carries.
2. **§1's replay is stronger than it was reported.** It was framed as "one of two hosts,
   the other unmeasurable". In fact the host it ran on is where **100% of observed clicks
   happen**, so the 109-pick replay covers the entire observed click population — there is
   no unmeasured second population, only a host that does not click.

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
finite and the picker is **110 cols** (`PICKER_COLUMNS`); a marker that is fuzzy-matchable can itself perturb
fzf's scoring (a leading token matches queries), so it would need to sit where the match
cannot reach it or be measured not to.

**(B) `--no-sort`.**
fzf then filters by the query but preserves input order among the survivors, so our rank
survives typing completely.
*Pros:* the only option that actually makes the pre-computed order the final arbiter
under a query; one flag.
*Cons:* throws away fzf's match-quality ordering — type an owner name like `acme` (synthetic;
this repo is public and a real third-party name must not be used as an example) and you get
*our* order among matches rather than the best match first, which on a 394-row universe is a real
loss when Tier A is UNKNOWN for the rows involved. The suite currently bans this flag as
"turns off ranking entirely, which is the pre-#1373 bug" — that ban was written when there
was no pre-sort; now there is one, so the flag means "our ranking wins" rather than "no
ranking". **It is a genuine trade and it should be decided on data, not on the old ban.**

🔴 **REFUSED — operator, 2026-09-20, on the data the paragraph above asked for.** The
trade is not neutral: it is **backwards** for the modal click.

The sort key is `(klass, distance, -score)`, so every `PLAUSIBLE` row sorts ahead of every
`BELOW` row. Measured over the click telemetry (n=71 picks carrying both dims), the class
of the row the operator **actually chose** was:

| class of the picked row | picks |
|---|---|
| `below` | **54** |
| `plausible` | 17 |

Stable across eras (52/68 before `#1775`, 2/3 after), and **not** a stale-table artifact —
`known_ranges.json` is refreshed by a daily timer (394 entries, last run within 5h of the
measurement). So three times in four, the wanted row is one our own primary sort term ranks
*behind* every plausible row. **fzf's match score is currently rescuing the operator from
the class term; `--no-sort` removes the rescue.**

⚠ **Why this was not visible before:** the replayed top-1 figures (`62.6% → 73.0%`) say
where the correct repo *would* rank. They are not where the operator *clicked*. Derived
from the same rows (`rank − pinned_above + 1`), the observed landing position is:

| the pick landed on | picks |
|---|---|
| our 1st row | 9 (12.7%) |
| our **2nd** row | **38 (53.5%)** |
| our 3rd row | 14 |
| 6th / 49th+ | 6 / 4 |

⚠ **The derivation is checked against this arc's own published figures, and it agrees to
within one pick rather than exactly** — said precisely because the gap is the kind of thing
a later reader would otherwise treat as a contradiction. The handoff records the modal pick
under the replaced key as the **second**-ranked row at **29** against **8** for the first;
the same derivation over the table today gives **30** against **8**. The log only grows, so
one further pick landing on the second row between the two readings accounts for it. The
`pinned_above` correction is what makes the derivation work at all: `rank` is 0-indexed and
counts the pinned rows, so reading it raw reports the modal pick as "rank 3" and hides that
it is our *second* universe row.

⚠ **NOT DIAGNOSED, and it should be before anything acts on it:** *why* `below` dominates.
One untested mechanism is that many clicked `#N` are clawgate or ClickUp ids rather than
GitHub numbers, so no repository's range is relevant to them and `below` is the honest
class — in which case the defect is that such clicks are ranked by a range term at all.
**This is a bigger finding than symptom 1 and it has a cheap mechanical test**: the causal
replay harness already exists, so re-running it with the class term demoted and reading
top-1 answers it without an operator judgement.

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
| ENTER, query matched nothing | `<query>\n`, exit **1** | **present** |
| ESC / Ctrl-C abort | **nothing at all**, 0 bytes, exit 130 | **absent — NOT MEASURED** |

🔴 **THE ESC ROW OF THAT TABLE IS OUT OF DATE, AND THE `--bind` SECTION BELOW SUPERSEDES
IT.** Left in place rather than edited because the paragraph under it is a record of a
correction, and silently rewriting the table would leave that record describing a table
that no longer says what it was correcting. **Current contract, measured at fzf 0.74.4:**

| ending | what fzf writes | `queried` |
|---|---|---|
| a selection (PICKED) | `<query>\n<row>\n` | **present** |
| ENTER, query matched nothing | `<query>\n`, exit **1** | **present** |
| **ESC abort** (since `--bind="esc:print-query+abort"`) | `<query>\n`, exit **0** | **present** |
| **any other abort** — `ctrl-c`, `ctrl-g`, `ctrl-q`, `ctrl-d` on an empty query | **nothing at all**, 0 bytes, exit 130 | **absent — NOT MEASURED** |

⚠ **`abort` is FOUR keys, not one** — fzf's keymap reads `abort  ctrl-c  ctrl-g  ctrl-q
esc`. An earlier version of this section, and the code comments it fed, said the residual
gap was "Ctrl-C alone"; that was an exclusivity claim nobody had measured. Caught by
`/audit-pr` round 0 on #1812.

`--print-query` covers the endings where fzf has a result to print; a non-ESC abort is not
one of them. **The first draft of this work asserted that an abort writes the query
alone, in three code comments and the PR body. It does not.** Corrected, and pinned by
`test_an_ESC_abort_RECORDS_a_verdict_and_the_OTHER_aborts_do_not`.

Consequences for reading the data:

* The **picked** rate — which is what symptom 1 is actually about — is complete.
* The **dismissal** arm's `queried` covers the Enter-with-no-match **and ESC** endings.
  🔴 **It is not a rate over that arm and cannot be made into one**: `pick()` files every
  dismissal under one `reason`, and ESC and Enter-with-no-match are byte-identical on
  stdout, so "of ESC dismissals" is not computable. ✅ **The one clean cell** is
  `queried == False`, which only an ESC on an untouched picker can produce. Count that.
* Three-valued is what keeps this honest. A `False` default would file every abort under
  "the operator scrolled", which is the population the whole question is measured from —
  wrong in the reassuring direction.

**Option (D)**, if the abort gap matters: `--bind 'esc:print-query+abort'`. **Measured:
it works** — Esc then writes `<query>\n` (Ctrl-C still writes nothing), the picker closes
exactly as before, and nothing on screen changes. It was **not** taken in this PR because
it rebinds a key on the live click path, and that is the operator's call. It is one flag,
reversible, and the pinned-string/metacharacter guards already cover `PICKER_SH`.

🔴 **DECIDED — operator, 2026-09-20: take (D) now, then (A). (B) is REFUSED on the data
below.** Shipped as `--bind="esc:print-query+abort"` in `PICKER_SH`. Re-measured at fzf
**0.74.4** (the note above was taken at 0.74.3) with both flag states side by side and the
four untouched endings as the probe's own positive control: Esc writes `<query>\n` exit 0,
an Esc on an untouched picker writes a bare `\n` (so **`False` is now reachable on the
dismissal arm**, which retires that arm's "True by construction" caveat), and Ctrl-C is
unchanged at 0 bytes / exit 130.

⚠ **And this doc's claim that Ctrl-C cannot be closed was never measured, and is FALSE.**
`--bind="ctrl-c:print-query+abort"` is accepted at 0.74.4 and makes Ctrl-C write
`<query>\n` too, verified against a control where the shipping config leaves it at 0 bytes.
It was **not** taken: the operator approved the Esc bind specifically, and rebinding the
universal cancel is a separate call. One flag, same shape, whenever the arm should be
complete.

~~**Recommendation: (C) then (A).**~~ ⚠ **SUPERSEDED — this was the recommendation BEFORE
the operator decided.** Kept struck rather than deleted so the decision above reads as a
choice made against a stated alternative, not as the only option anyone offered. The
operator took **(D) then (A)** and refused (B) on 2026-09-20; see those sections. The
original reasoning: (C) because the question was now measurable and was not before; (A)
because it is additive and reversible; (B) not before the `queried` rate is known.

### Symptom 2 — DECIDED: optimise for top-1. The sort key changed.

🔴 **Operator decision, 2026-09-18: optimise for top-1, not mean rank.** The reasoning, on
the record because it is what makes the trade below legible: **top-1 is the only metric that
maps to an action.** At rank 1 the operator presses Enter and is done. At rank 3 they arrow
or they type — and the moment they type, fzf's own score takes over and the pre-computed
ordering stops mattering at all (§3). Mean rank 1.90 improves a number nobody acts on, and
the configuration that produced it is the one that produced the reported symptom.

**The change:** `order_universe`'s key moves Tier B from **above** the distance term to
**below** it — `(klass, -score, distance)` → `(klass, distance, -score)`. Tier B now
separates only what Tier A *cannot*: within `PLAUSIBLE`, the distance term (`max_ref - num`)
decides first and the learned score breaks its ties; in `BELOW`/`UNKNOWN`/`IMPOSSIBLE` the
distance term is 0 for every row, so Tier B still orders them exactly as before — which is
where its tail benefit always came from.

**No constant was moved, and the sweep says none should be.** A confidence floor below which
a learned score does not reorder was measured at 0.5 / 1.0 / 2.0 / 5.0: top-1 is **62.6% at
every one of them**, identical to shipped. That is the same inertness the half-life ×
proximity sweep showed. The structure was the lever; it still is.

#### The numbers — same corpus, before and after

Corpus: **115 picks, 2026-09-11 → 2026-09-18**, 394-row universe, 394-entry range table.
(The log grew from 109 to 115 while this work was in progress; all rows below are the
**current** corpus, re-measured, so they do not match the 109-pick figures reported earlier
in §1.)

| key | top-1 | top-3 | mean rank | p90 |
|---|---|---|---|---|
| **SHIPPED** `(klass, -score, distance)` | 72/115 (**62.6%**) | 103/115 (89.6%) | 3.18 | 3 |
| Tier A only `(klass, distance)` | 84/115 (73.0%) | 104/115 (90.4%) | 9.54 | 2 |
| **NEW** `(klass, distance, -score)` | 84/115 (**73.0%**) | 111/115 (**96.5%**) | **2.75** | 1 |

**top-1 +10.4 pp · top-3 +6.9 pp · mean rank 3.18 → 2.75.**

🔴 **The mean-rank cost the decision was prepared to pay did not arrive** — and that is worth
stating plainly rather than presented as cleverness. The new key recovers *all* of Tier A's
top-1 **and** beats both prior configurations on top-3 **and** improves mean rank. Tier A
alone would have cost mean rank (9.54); keeping Tier B underneath the distance term is what
avoids that. There is no Pareto trade here to disclose because the measurement did not
produce one.

Per-click movement: **27 top-1s won, 15 lost**, 31 ranks improved / 16 worsened / 68
unchanged. Every regression is small — the four worst are **one row** (0 → 1) and the single
worst is two (2 → 4).

#### 🔴 The finding that actually justifies the change: the shipped key DEGRADES as the log grows

| slice | shipped top-1 | new top-1 |
|---|---|---|
| first half (n=57, cold log) | **82.5%** | 68.4% |
| second half (n=58, warmer) | **43.1%** | **77.6%** |
| last 30 | **33.3%** | **66.7%** |
| picker-via only (n=71 — the clicks that actually show a picker) | 49.3% | **80.3%** |
| auto-via only (n=9) | 88.9% | 100.0% |

**On a cold log the shipped key is better** — 82.5% vs 68.4% — and that is a real regression
this change accepts, disclosed rather than buried. But the log only grows, and as it does the
shipped key collapses (82.5% → 43.1% → 33.3%) while the new key climbs (68.4% → 77.6%). The
mechanism is visible in the key itself: every repository the operator has ever picked earns a
non-zero score, so with a warm log *more and more* rows outrank Tier A's correct first choice.
"The wrong repo sits at the top" is therefore not a static defect — **it was getting worse**,
and it would have kept getting worse.

The largest win is on `via=picker` (49.3% → 80.3%), which is precisely the ambiguous
population the operator sees a picker for and complains about.

#### The instrument was validated before its verdict was read

The replay now decides a product change, so it was fed rankers known to be wrong:

| control | top-1 | mean rank |
|---|---|---|
| reversed key | **0.0%** | 391.22 |
| name-only (every signal ignored) | **0.0%** | 176.06 |

Both collapse to zero. A replay that cannot produce a bad number is not measuring the ranker;
this one can, so its 73.0% is a measurement rather than an artefact of the harness.

⚠ Unchanged caveats: 115 picks over 6.7 days on one host, 10 distinct repositories, and the
range table used is **today's**, not the one in force at each pick.

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

🔴 **DECIDED — Operator decision, 2026-09-19: option (2), SHIPPED as devrc#1793.** The
condition above was **NOT** met and that is recorded rather than glossed: `66e51d90`
(#1775, the sort-key fix and the `queried` instrument, one commit) landed
`2026-09-18 19:09:29 -0500`, so at decision time it had **~1 day of a stated 7**, with
**n=1** post-fix pick.

**What decided it instead was the condition's own stated PURPOSE**, quoted above: *"if it
turns out the operator almost always picks the clawgate row on a bare `#N`, (1) is simply
correct"*. That question is answered — **the clawgate row took 1 of 73 recorded picks**,
and the two pinned rows together took 5 (6.8%) while holding the top two slots. #1775
cannot invalidate that: the clawgate row is never ranked, and a better universe ordering
only makes the ranked rows *more* attractive, so 6.8% is an **upper bound** post-fix.

⚠ **What is NOT measured, stated at the scope it was measured:** that the operator, shown
the ranked row at position 2 rather than 3, presses Enter rather than typing. The 73 picks
predate the current sort key; under the key it replaced, the modal pick was the
**second**-ranked row (29) rather than the first (8). Promoting the *first* row is a
prediction from the new key's replayed top-1 rate (62.6% → 73.0%), not a behavioural
measurement. All picks come from one host (the laptop); the workbench has logged none.

⚠ **And the cost this change imposes cannot be read back out of the telemetry.** On the
`audit-pr N` shape the promotion moves the pane guess off row 1, so a correct guess costs
one extra keystroke — but that shape and dead-end-2 both report `pinned_above = 1`, so
"how often was that keystroke paid" is only partially recoverable. A change argued from
telemetry that accepts a cost its telemetry cannot see is worth saying out loud.

🔴 **Scoped twice after `/audit-pr` round 0, and neither scoping is cosmetic.** The
promotion fires only when `order_state == ORDER_APPLIED` — on a stale or missing range
table `_ordered_universe` returns its input **untouched** and `repo_universe` returns it
**sorted**, so an ungated promotion demoted the pane guess beneath the *alphabetically
first* row and the note called it "best-ranked". And it does not fire when the ordering's
own top row **is** the pane guess, which the dedup would otherwise turn into "promote the
runner-up above the winner".

### The test gap named in the brief

*"Nothing pins how many rows sit above the ordered block on the common bare-`#N` shape."*
**Closed in this PR**, because it is the exact seam where "the ranking works" and "the top
of the list is ranked" diverge: `test_the_rows_ABOVE_the_ordered_block_are_pinned_BY_KIND_
and_COUNTED` pins the count **and the kinds** on both shapes, and asserts that the first
ranked row's absolute rank is **not** 0. It is an *invariant guard* — it pins behaviour no
bug has violated — but it is the guard any of the §3 symptom-3 options must go red
against, which is the point of having it before the decision rather than after.

---

## When to read the data, and how much of it there will be

🔴 **Nothing schedules this read, so it needs a named closing condition or it is not a
work item.** The condition: **the operator, or a session resuming from this doc, runs the
query below and records the answer in this file.** It is closed when this section carries
a measured `queried` rate — not when a week has passed.

```
-- the rate symptom 1 turns on, on the PICKED arm
SELECT countIf(JSONExtractBool(payload,'queried')) AS typed,
       countIf(JSONHas(payload,'queried'))          AS measured,
       count()                                      AS picked_rows
FROM activity.events
WHERE source='tool' AND kind='invocation' AND text='mention-open'
  AND JSONExtractString(payload,'outcome')='picked'
  AND ts > '<the deploy date>'
```

### ✅ CLOSED — the rate, measured 2026-09-20

This section's closing condition was *"it is closed when this section carries a measured
`queried` rate"*. Run verbatim against `ts > '2026-09-19 00:09:29'` (the `#1775` deploy):

| typed | measured | picked_rows |
|---|---|---|
| **3** | 3 | 3 |

**3 of 3 post-fix picks were typed**, all on the laptop. Controls, because each of these
numbers is small enough to be an artifact: *positive* — 92 `mention-open` rows exist
across all time, so the emitter fires; *negative* — dropping the `outcome='picked'` filter
returns 4, not 3, so the filter is not inert. The one `dismissed` row carries **no**
`queried`, exactly as §3.1 predicts for an abort — which is the gap (D) has now closed.

🔴 **n=3 IS A DIRECTION, NOT A RATE, AND THE ARM IS FILLING ~10× SLOWER THAN PROJECTED.**
This section predicted ~100+ picked rows within a week; the actual is 3 in 29 hours, with
daily picked volume running 20 → 2 → 1 across 09-18/19/20. That drop is **usage, not a
dead source** — checked rather than assumed: laptop human-presence telemetry is healthy
(3,252 rows on 09-19) and `deadman.py` reports `laptop/tool` **ok** (2.0h silent against a
15.2h budget). Why the operator clicked less was **not** determined.

⚠ **What decided (B) was a different number in the same rows — see the note on (B) above.**

⚠ **The query STRING is deliberately dropped** at `_PICK_QUERIED`, so how many rows a
typed query leaves is **unmeasurable by design**. If queries usually narrow to one row,
symptom 1 is harmless whatever the typed rate says. That is the one measurement that would
settle this, and nothing here can take it.

⚠ **The two arms fill at very different rates — measured, so the week-later reader does
not mistake a 10-row sample for an answer.** Over the 6.7 days to 2026-09-18 the sink took
**85** click rows: **71 picked · 9 auto-open · 5 dismissed**, rising from 3/day to ~21/day.

* **Picked arm** — ~100+ rows within a week of deploying. **This is where the rate lives.**
* **Dismissal arm** — ~5 per week, and `queried` covers only the Enter-with-no-match subset
  of those (§3.1). At this rate it needs **roughly two months** before it says anything.

🔴 **AND UNDER `reason = 'dismissed'`, `queried` IS NOT A RATE AT ALL — DO NOT AVERAGE IT.**
There the dim is present only for the Enter-with-no-match ending (an abort writes nothing),
and on that ending it is `True` **by construction**: the picker always holds rows, so an
empty query always matches something and always yields a selection — a non-empty query is
the only way to reach that ending. Averaging it returns ~100% however the operator behaves.
That is a self-selected sub-population read as a rate, which is **worse than an absent
number**, because it looks like an answer.

⚠ **Scoped to the REASON, not to the arm.** That arm emits two outcomes (`dismissed` and
`no-selection`) across seven reasons, and `unmapped-row` is a counter-example — fzf wrote
both lines there, so `queried` is measured and *can* be `False`. `reason` is an emitted dim,
so filter on it.

Treat a present `queried` under `dismissed` as an **event** — "this click was a typed query
that matched nothing", the most diagnostic dismissal there is — and never as a denominator.
The query above is scoped to `outcome='picked'` for exactly this reason.

So "matters most on the dismissal arm" was right about *diagnostic value per row* and wrong
twice over: about when you can act on it, and about it being a rate.

## Appendix — what was NOT measured

* The ranking quality on the host with no `picks.jsonl`. There is no pick history there,
  so there is no ground truth to replay; an absence is not a score.
* Whether any historical pick was preceded by a typed query. Nothing recorded it. The
  `queried` dim shipped in this PR is what makes the next one answerable.
* Whether reordering `order_universe`'s sort key recovers the top-1 (§ symptom 2, option 2).
* Whether a visible class marker perturbs fzf's own scoring (§ symptom 1, option A).
* The range table in force at each historical pick — the replay used today's.
* Whether a **partially populated** range table has ever occurred. `tier_a` exists to
  detect it (the generator's ranges leg is deliberately non-fatal), but coverage is 100%
  today — 395 universe rows, 395 table entries — so the dim has never had anything to say.
