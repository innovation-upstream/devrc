# When a Claude Code session stops, what kind of fork is it?

**24,105 stops measured. Corpus 2026-07-06 → 2026-09-16, both hosts.**

> **Privacy:** counts, distributions and structural signal names only. No prompt, response,
> paraphrase or example appears below. Cluster "shapes" are statistics over derived features
> (word counts, first-token category, punctuation flags), never quotations. The extractor
> derives features in memory and writes no text to disk.

> **Provenance:** produced by a dispatched mining agent, 2026-09-16. Its central claim (§3) was
> re-verified independently against the hook source before this report was committed — see the
> verification note in §3. Its other numbers are the agent's own measurements and are labelled
> as such; where it flagged a limitation, that flag is preserved rather than smoothed away.

## 1. The partition — it changes the denominator by 7×

Subagent transcripts live at `<project>/<parent-uuid>/subagents/agent-*.jsonl`; operator sessions
at `<project>/<uuid>.jsonl`. **The discriminator is path shape** — structural, correct by
construction.

| population | files | share | bytes | **stops emitted** |
|---|---:|---:|---:|---:|
| subagent transcripts | 6,375 | **84.2%** | 5.35 GB | **0** |
| top-level, ≥1 human prompt — **operator** | 989 | 13.1% | 3.35 GB | **24,105** |
| top-level, 0 human prompts (aborted/SDK) | 206 | 2.7% | 0.006 GB | 4 |

**Validated three independent ways, zero disagreement:**

| check | operator | subagent |
|---|---|---|
| path shape | `<project>/<uuid>.jsonl` | `.../subagents/agent-*.jsonl` |
| transcript fields (800-file sample) | `isSidechain` false everywhere; `origin.kind=human` in 326/400 | `isSidechain` true on 106,158 rows; `origin.kind=human` in **0/400** |
| **ClickHouse `activity.events`** | **989/989** appear in `kind=prompt` | **0/1,000** sampled appear — in `kind=prompt` or **any** row |

Reconciliation: the extractor counts 24,671 prompt-carrying rows in those sessions; ClickHouse
counts 24,560 — **0.45% apart** (the collector's 5-min timer lags).

**Two findings fall straight out:**

- **Subagents emit no Stop-hook events at all** (0 `stop_hook_summary` across 6,375 files).
  Cross-checked with grep: 6 files contain the token, all 6 as message *text*, 0 as a real
  record. They are structurally invisible to the Stop hook — **no exclusion logic is needed.**
- **The telemetry pipeline cannot make the distinction that matters.** ClickHouse `kind=prompt`
  conflates operator-typed prompts (12,240) with agent task-notifications (12,340) — a near-even
  split. **Transcripts are the only surface separating them.**

## 2. Instrument validation

A synthetic fixture with hand-known ground truth: **38 positive-control assertions, all passing**
(each confirms a counter moves off zero to its exact expected value), plus a negative control.
Re-run after every extractor edit; two edits reset the gate and were re-validated.

**Two instrument bugs were caught by controls, not by inspection:**

- A cross-check grep returned `0` for the population under test *and* the positive control.
  Cause: `xargs -0 command grep` — xargs cannot exec the shell builtin `command`, and
  `2>/dev/null` hid every failure. Re-run against the resolved binary, the control moved to
  848/1021.
- Three counters read exactly zero. The subagent tool is named **`Agent`**, not `Task` — a naming
  bug worth 4,784 stops. **`TodoWrite` and `ExitPlanMode` do not exist in this Claude Code
  version**, so two candidate signals named in the brief are simply unavailable.

## 3. 🔴 `AskUserQuestion` never reaches the Stop hook

The brief called it "the one already-deterministic case". **It is not a stop case at all.**
Over 527 occurrences in 400 sessions:

| event immediately following | count |
|---|---:|
| `tool_result` (answered inline, turn continues) | 524 (99.4%) |
| another tool call | 2 |
| a typed prompt | 1 |
| **Stop-hook event** | **0** |

**Independently re-verified against the source before committing this report**, by a different
route than the agent used: the Stop hook hardcodes `--arg kind idle`
(`hook/clawgate-stop-hook.sh:296`), unconditionally, with no branch on anything — so it *cannot*
emit `question`. `kind=question` is raised by the **sibling** hook
(`hook/clawgate-hook.sh:301`), which branches on `AskUserQuestion)` at `:376` — i.e. at
tool-call time, not at stop. **Two different hooks, two different lifecycle moments.** That is
exactly why the queue reads 24 idle / 3 question.

Also constant, and therefore useless as signal: `stop_reason`=`end_turn` 100%,
`level`=`suggestion` 100%, `preventedContinuation` false 100%, final message contains a tool
call 0.0%.

## 4. The taxonomy, derived bottom-up

Clustered on **continuation shape** (18 features, standardised, pure-Python k-means, 5 restarts,
k swept 4→10; k=7 at the elbow). n = 9,589.

| fork type | share | defining structure |
|---|---:|---|
| **Bare approval / acknowledgement** | **40.0%** | 1 word, ~6.5 chars; 98% match a closed affirmative lexicon |
| Short unclassified utterance | 23.0% | median 3 words; matches *no* lexicon |
| **Short directive** | 21.1% | ~4 words; **100%** open with an imperative verb |
| Short question | 6.7% | ~14 words; **100%** end `?` |
| Wh-question | 4.6% | ~9 words; **100%** open with a wh/aux word |
| Bulleted multi-item brief | 3.5% | ~23 words, 3.2 lines; **99%** bulleted |
| Long re-scoping brief | 1.1% | ~1,137 chars, 16.6 lines; 60% paths, 56% multi-paragraph |

**Honest limitation, preserved from the agent:** the 23.0% cluster is a **lexical residual, not a
discovered fork type** — short text matching none of the closed lexicons. A content-aware
classifier would likely split it across the approval and directive clusters. Reported as
unresolved rather than named. Notably, **explicit corrections are near-absent** (~0.4%).

## 5. 🔴 Deterministic coverage — the headline

**Predicting *what* the operator types: essentially zero.**

```
H(class)                    = 2.1975 bits  (majority 40.0%)
majority-class baseline     = 40.0%
decision tree, 23 signals   = 40.1%
LIFT                        = +0.1 percentage points
```

| signal | share of class entropy |
|---|---:|
| final text contains `?` | **1.07%** |
| final text ends `?` | 0.61% |
| final text has bullets | 0.59% |
| **`AskUserQuestion` in turn** | **0.04%** |
| tool error in turn | 0.01% |

**No signal carries more than 1.07% of the entropy.**

**The negative result is not an artefact of the target** — five alternative targets, same protocol:

| target | baseline | tree | lift |
|---|---:|---:|---:|
| **T1 — does this stop need the operator at all?** | 60.2% | 74.8% | **+14.6 pp** |
| T2 — one-tap approval vs substantive typing? | 60.4% | 62.0% | +1.6 pp |
| T3 — fast vs slow continuation? | 82.4% | 82.6% | +0.2 pp |
| T4 — short vs long brief? | 81.1% | 81.0% | −0.1 pp |
| T5 — is it a question? | 88.3% | 88.3% | +0.0 pp |
| T6 — session's final stop? | 96.0% | 96.2% | +0.2 pp |

**The split is sharp: *whether* the operator is needed is partly predictable; *what* they will
do is not.**

T1's drivers (mechanistic, not leakage):

| signal | share | P(needs human \| true) | \| false |
|---|---:|---:|---:|
| **`Agent` dispatched this turn** | 19.8% | **5.9%** | 48.2% |
| final text ends `?` | 8.2% | **81.2%** | 36.1% |
| hook reported an error | 2.1% | **0.0%** | 40.6% |

### The residual

| band | rule | stops | share | **P(needs human)** |
|---|---|---:|---:|---:|
| AUTONOMOUS | `Agent` dispatched, or hook error | 5,171 | 21.5% | **5.5%** |
| NEEDS-HUMAN | ends `?`, or has `?` and not short | 4,460 | 18.5% | **76.3%** |
| **UNCERTAIN** | everything else | **14,474** | **60.0%** | **40.8%** |
| *base rate* | | 24,105 | | *39.8%* |

> **60.0% of stops fall in UNCERTAIN, and inside it the signals are worth one percentage point
> over chance on 14,474 samples.** This is not "40% coverage that could be tuned up" — the
> information required to classify a stop **is not present in the structural record when the
> Stop hook fires**. Separately, **50.3% of continued stops carry no structural signal at all.**

**Caveat the agent flagged and this report keeps:** the two NEEDS-HUMAN signals are
**punctuation/suffix heuristics** — exactly what the standing rules disfavour. Reported because
it is what the data contains, **not recommended**. Structural alternative in §8.

## 6. The buckets that are not "continue"

**60.2% of stops get no in-session human continuation** — but 80.3% of those are the session
*continuing autonomously* (next event = task-notification), not the operator leaving.

**"Never returned to" barely exists.** Of 970 session-final stops:

| after the final stop | count | share |
|---|---:|---:|
| new session in same project **within 5 min** | 237 | 24.4% |
| 5–60 min later | 411 | 42.4% |
| 1–24 h later | 263 | 27.1% |
| >24 h later | 29 | 3.0% |
| **no later session at all** | **30** | **3.1%** |

**96.9% of session endings are followed by more work in the same project.** A large
dismiss/archive bucket was expected when this was dispatched — **it does not exist** (0.12% of
all stops). The missing affordance is **session handoff**: a quarter of endings are followed by a
fresh session within five minutes.

**Time-to-continuation** (n=9,589; right-skewed, a mean would be meaningless):

| p10 | p25 | **p50** | p75 | p90 | p99 |
|---:|---:|---:|---:|---:|---:|
| 64 s | 193 s | **706 s (11.8 min)** | 44 min | 3.6 h | 22 h |

**Only 17.6% arrive within two minutes; 20.9% take over an hour.** This is a **triage queue, not
a quick-reply box.**

## 7. Is `/api/suggest` alive? — ingest yes, generation dead by policy

| | |
|---|---|
| **The ~96%-dead figure reproduces, and is FIXED** | Original window 2026-06-14→08-25: 853 ok vs 23,969 payload failures (**96.6%**). **Post-fix 2026-08-26→09-16: 13,742 ok, 0 failures.** Last failure `2026-08-25 16:42:19`. ~99.95% success. |
| **But generation is ~dead by policy** | Opt-in is a Postgres row (`project_settings.auto_suggest`, default **false**). **Exactly one project opted in: `auditloop`** — which has **3 of 471** sessions. **27 suggestions ever**, all `auditloop`, none in 6 days; **zero generations** in the pod's uptime. |
| Endpoint | Live and busy (122× HTTP 200 in 3h27m) but never *returns* suggestions — it responds `{"ok":true}` and spawns detached. |

**"Do we need it" comes before hardening.** The ingest chokepoints are genuinely fixed; the
feature is unused because one low-traffic project is enabled.

**`Kind`** is a plain `string` field (not a Go type), closed 3-value list validated in Go *and* a
SQL `CHECK`. Live: **24 idle / 3 question open**; 1,574 / 729 all-time; **`manual` has 0 rows
ever**. There is **no metadata/tag map** on `Entry` — a finer label needs a new column.

## 8. Proposal

**Two orthogonal axes**, because they behave completely differently:

- **Axis A — does this stop need the operator?** Deterministically triageable (§5).
- **Axis B — what continuation is owed?** *Not* deterministically available (§4).

**Deterministic layer (ships first, no model call):** `Agent` dispatched and outstanding →
AUTONOMOUS; hook error → AUTONOMOUS; else UNCERTAIN. The `?`-suffix rules are **deliberately
excluded** despite being the strongest positive signal — they are a punctuation heuristic on
model prose that will drift with phrasing and encode nothing about intent.

**The structural alternative, and the thing worth actually fixing:** the model *does* have a
structured way to say "I need input" — `AskUserQuestion` — and it never reaches the Stop hook
because it resolves inline. Make a genuine "blocked on the operator" state **expressible at stop
time as a structured claim the model emits**, rather than a regex over its output. That converts
the strongest signal from a heuristic into a contract.

**For the 60% residual:** deterministic signals are worth ~1 pp there — **do not build a bigger
rule table.** That band is the only place a generative suggestion can help, because it reads
*content*, which is exactly what the structural record lacks. Evaluate `/api/suggest` **scoped to
UNCERTAIN**, not the whole population where 21.5% of stops need nobody.

**Where it runs — the Stop hook, host-side.** It already has everything needed: `transcript_path`,
a 2,000-line/512 KB tail, and the last assistant message (65,536 chars). Consequence: host-side
logic ships via home-manager, so **merge → pull → `home-manager switch` → restart the consumer**.
A merge alone changes nothing.

**UI layer:**

| class | surface |
|---|---|
| **AUTONOMOUS** (21.5%) | **Do not raise an entry at all** — the biggest win available |
| **NEEDS-HUMAN** (18.5%) | Top of queue: final-turn tail + text box |
| **UNCERTAIN** (60.0%) | Queue body by recency — where suggestions earn their place and get measured |
| **Approve fork** (40.0% of continuations) | A one-tap affirm control. It cannot be *predicted* but can always be *offered* — an unused button costs nothing |
| **Session-final** (3.9%) | Not "archive" — a **resume** affordance |

**On extending `Kind` — recommend NOT to**, argued from measurement: the two axes are orthogonal
(an entry has both a triage band and, once known, a fork type), so folding either into a single
enum forces a cross-product and loses the fact that 60% have no fork type at all. A **separate
nullable sub-classification column** keeps `Kind` stable, represents the residual honestly as
*absent* rather than a fabricated value, and is a **strictly smaller change**, since no existing
consumer branches on a column that doesn't yet exist (`0030` is the precedent; widening `kind`
would touch five documented "one rule, one place" consumers plus a frozen migration, two indexes,
the UI and metric cardinality).

**Build first — suppress the AUTONOMOUS band.** One rule ("final turn dispatched an `Agent` that
hasn't reported"), from data the hook already has, suppressing **21.5% of stops** that need a
human **5.5%** of the time. No new field, no `Kind` change, no model call, no UI work. Then:
(1) **instrument the queue** — record whether each entry was ever acted on, since everything here
is inferred post-hoc and the queue cannot currently report its own precision; (2) make "blocked"
structurally expressible; (3) add the one-tap affirm; (4) *only then* evaluate suggestions on
UNCERTAIN.

## 9. What could NOT be measured

- **The 23.0% cluster is unresolved** — naming it needs content, which the privacy constraint
  forbids showing here.
- **The privacy constraint prevented showing any shape example.** Cluster names can be audited
  against feature profiles (all reported), not against instances.
- **Continuation is within-session.** Cross-session links (§6) are **association by project +
  time proximity, not proven handoff**.
- **Time-to-continuation is wall-clock** — it cannot distinguish thinking from sleeping. The
  >4 h buckets (9.5%) are almost certainly the latter.
- **`bg_sess` is a cumulative launch count, not liveness** — reported descriptively, excluded
  from the triage rule. Only `agents_live` is a true outstanding-work measure.
- **Corpus starts 2026-07-06** (older transcripts pruned; ClickHouse retains prompts to
  2026-04-27), so **no pre-July behaviour change is visible**.
- **Host skew:** workbench is 85.4% of top-level sessions.
- **`auditloop` end-to-end generation was not fired** (would mean a real write + OpenRouter call);
  the generator seam is unexercised since the current pod started.
- **The laptop's 0 hook failures is a window artefact** — its log begins entirely post-fix.
- **One projection, labelled:** "the queue should shrink by about a fifth" is inferred from the
  21.5% share, not measured on the live queue.

## 10. Reproducibility

The extraction, clustering and prediction scripts were written to a session scratchpad, not to
this repo — the mining agent produced a report, not production code. **They are therefore
ephemeral.** If any number here needs re-deriving, the instrument must be rebuilt; the method is
described above in enough detail to do so (path-shape partition, 18 standardised features,
pure-Python k-means with 5 restarts, decision tree over 23 signals, 38-assertion positive-control
fixture). Committing them is a separate decision — they read transcripts, and this repo is
public, so they would need review against the captured-text gates first.
