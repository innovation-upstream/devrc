# Migration to devrc — 2026-08-22

The skill moved out of `datapacket-talos/.claude/skills/check-clickup-addressed/` (project-
scoped, gated by nothing) into devrc: docs at `claude/skills/check-clickup-addressed/`,
code + suite at `scripts/check-clickup-addressed/`. It is now a global skill on both hosts
and a registered target of `scripts/run-tests.sh`, which had never run it before.

**The move broke a guard, silently, and that is the round's finding.** `_selfrun.py`'s
anchored markers are PATH FRAGMENTS. One of them was `check-clickup-addressed/scripts/` —
the talos layout. The devrc layout reverses those two segments
(`scripts/check-clickup-addressed/`), so after the move no invocation matched, the prior-run
guard would have stopped recognising its own runs, and the checker would have gone straight
back to the D4 failure it was built to prevent — reading yesterday's report as today's
evidence. Nothing errors when this happens; the report just stops printing
`(ignored N transcript(s) …)`.

The old literal stays in `SELF_RUN_MARKERS` (transcripts written before the move still say
it) and the new layout is matched by `NEW_LAYOUT_RE`. The test was watched RED on the
migrated tree before the marker existed. Suite: 155 → 157.

🔴 **And the obvious fix was measured WRONG, which is the more useful half.** The natural
counterpart to the old marker is the directory, `scripts/check-clickup-addressed/`. That
re-creates the exact defect this file already rejects the bare name for. devrc's `CLAUDE.md`
carries a subsystem table mapping `scripts/<dir>/` to its owning skill, a project `CLAUDE.md`
is injected into every session in that repo, and the gate prints a per-target line naming the
same directory. Measured over the 761 transcripts these scripts actually walk:

| candidate | transcripts matched | verdict |
|---|---|---|
| `check-clickup-addressed` (bare name) | 96 (12.6%) | unusable — the known one |
| `scripts/repo-cos/` (sibling CLAUDE.md row, as a proxy) | 83 (10.9%) | what the directory spelling would cost |
| `scripts/session-analysis/` (ditto) | 72 (9.5%) | ” |
| `check-clickup-addressed/scripts/` (old marker) | 11 (1.4%) | ✅ anchored |
| `scripts/check-clickup-addressed/[file].py` (shipped) | — | ✅ anchored: requires a FILE |

So the marker requires a `.py` file immediately after the directory. A run matches; the
CLAUDE.md row and the gate's `…/tests` line do not. `test_a_mention_of_the_scripts_DIRECTORY_
is_not_a_self_run` uses both of those as REAL fixtures, and the directory-spelling mutant dies
on that test alone — verified by substituting it and watching 1 failed / 156 passed.

**Generalise:** the over-broad direction has no error and no test of its own unless you write
one. Ask what OTHER document in the new repo spells your marker, and measure the candidate
against the real corpus before shipping it.

**Generalise:** a marker built out of a path is a claim about where the code lives. Moving
the code invalidates it without touching it, and the tell is a guard that goes quiet rather
than a test that goes red.

---

# Validation record — 2026-08-19

Three defects made this checker emit confident false verdicts. All three were found by
reading the tool's own output against ClickUp ground truth, not by a failing test.

## Defects

**D1 — the fallback that manufactured `partially_addressed`.**
`check-completion.py` fell back to a full-text scan of the whole corpus when a task ID
appeared nowhere, scoring every signal at a hardcoded `proximity: 0.5`. The "close" tier
requires `> 0.5`, so those results could never reach it and always fell through to
`elif completion and open_items → "partially_addressed"`. Any unmatched task, in a corpus
containing one `#N merged` and one `still running`, was labelled partially-addressed **by
construction**, citing other tasks' work. Observed on `868kt8pfu`: 5 completion + 5 open
signals, `mentions_found: 0`, every snippet about unrelated work. One of the five
"completion" signals literally read *"PR #346 is still unmerged."*

**D2 — ±2000-char windows bleed across a multi-task triage table.**
Asking about `868krn3y1` returned `868kr07fu`'s merge at **proximity 1.5** — the task-ID
boost fired because the *target's* ID also fell inside the 80-char snippet, making the
misattributed signal the highest-confidence one in the report.

**D3 — the tool read its own transcript.**
`search-sessions.py` run against the four task IDs returned exactly one session: the one
running the check, which mentions every task ID under test by construction.

**D4 (found while fixing) — `sessions_found: 0` on every multi-task run.**
`check-addressed.py` merged all task IDs plus four words from each task *name* into one
≤8-term bag; `search_sessions` ANDs its terms, so the query demanded a single session
mentioning four unrelated tasks. It always returned 0, `check-completion.py` silently fell
back to its own per-task scan, and the report still printed `sessions_found: 0` —
describing a search whose result nothing used.

## Fixes

- `check_task` returns **`no_mentions_found`** with empty signal lists; the full-text
  fallback is deleted.
- `extract_signals_from_windows` keeps a signal only when the task under test is the
  ClickUp ID lexically nearest to it (`_nearest_task_id`).
- `extract_text_windows` returns `(window, distance, mention_offset)` and matches the
  **exact** ID only; the 6-char-prefix pass is removed, along with its `already_covered`
  check (an `any()` over `range(len(text))` whose predicate ignored the loop variable).
- `--exclude-session` on both scripts, defaulting to `$CLAUDE_CODE_SESSION_ID`.
- `check-addressed.py` searches **per task** and reports `sessions_by_task`.

## Red-at-base / green-at-HEAD

Base = the pre-fix scripts, copied aside before editing. Suite: `python3 scripts/check-clickup-addressed/tests/run_all.py`.

| Test | At base | At HEAD |
|---|---|---|
| `test_no_mentions_is_not_partially_addressed` | ✗ `got 'partially_addressed'` | ✓ |
| `test_rival_task_signal_is_not_attributed_to_target` | ✗ (see note) | ✓ |
| `test_own_signal_survives_the_attribution_guard` | ✗ (see note) | ✓ |
| `test_nearest_task_id_picks_the_closer_of_two` | ✗ `no attribute '_nearest_task_id'` | ✓ |
| `test_excluded_session_is_not_read` | ✗ `unexpected keyword 'exclude_sessions'` | ✓ |
| `test_search_sessions_honours_exclude` | ✗ `unexpected keyword 'exclude_sessions'` | ✓ |
| `test_windows_carry_their_mention_offset` | ✗ `expected 3, got 2` | ✓ |
| `test_prefix_matching_does_not_invent_windows` | ✗ (window invented) | ✓ |
| `test_no_mentions_positive_control` | ✓ | ✓ |

**Note — two of those reds are weak.** The attribution tests fail at base on the tuple
*shape* (`too many values to unpack`), not on the behaviour, so they died for the wrong
reason. The behaviour was therefore proven separately against the baseline's own 2-tuple
API: asking about `868krn3y1` over a two-row table returned
`[PR merged] prox=1.5 | 868kr07fu | talos-infra #1065 merged 08-16 …`. That is the defect,
measured at base.

`test_no_mentions_positive_control` passes at base and is an **invariant guard**, not
regression coverage: it exists so D1's fix cannot pass by returning nothing for everything.

Full suite at HEAD: **45 passed, 0 failed, rc=0** — read by counting `✓`/`✗` lines, not
from a piped exit code (`run_all.py | tail` reports *tail's* status; that misread happened
once during this work).

## Mutation sweep

Each guard broken in a pristine copy; the suite must fail **by name**.
`PYTHONDONTWRITEBYTECODE=1`, `__pycache__` excluded from the copy.

| Mutant | Verdict | Killed by |
|---|---|---|
| IDENTITY (harness control) | OK — 45 passed | — |
| M1 drop attribution guard | KILLED | `test_rival_task_signal_is_not_attributed_to_target` |
| M2 `no_mentions_found` → `partially_addressed` | KILLED | `test_no_mentions_is_not_partially_addressed` |
| M3 `check_task` ignores exclude list | KILLED | `test_excluded_session_is_not_read` |
| M4 `search_sessions` ignores exclude | KILLED | `test_search_sessions_honours_exclude` |
| M5 nearest → farthest | KILLED | `test_nearest_task_id_picks_the_closer_of_two` (+2 collateral) |
| M6 prefix matching reinstated | KILLED | `test_prefix_matching_does_not_invent_windows` |

The IDENTITY row is the harness's positive control: it proves the sweep can report
"all pass", so a `SURVIVED` verdict would have been meaningful. Harness:
`scratchpad/mutate.py` (session-local, not committed).

## End-to-end: same four tasks, before and after

Ground truth taken from each ticket's newest ClickUp comment and live status, plus
`gh pr view` on every PR cited.

| Task | Before | After | Ground truth |
|---|---|---|---|
| `868ktvqf9` | partially_addressed (5c/4o) | partially_addressed (5c/4o) | ✅ `in progress`; #4102 merged, headline defect open |
| `868kt8pfu` | partially_addressed (5c/5o, **all false**) | **open** (0c/1o) | ✅ nothing shipped; blocked on `workflow` OAuth scope |
| `868kr07fu` | partially_addressed (5c/3o, **all cross-task**) | **likely_addressed** (1c/0o) | ✅ *"Resolved … Recommend closing"* |
| `868krn3y1` | partially_addressed (3c/3o, **all cross-task**) | **unclear** (0c/0o) | ✅ no work recorded since 08-15 |

(These are the round-1 verdicts. Round 2 reads more text — user messages — so `868kr07fu`
moves to `partially_addressed`; its resolved state is surfaced by the disagreement flag
instead. See the round-2 section.)

`sessions_found: 0` → `sessions_by_task: {868ktvqf9: 2, 868kt8pfu: 2, 868kr07fu: 4,
868krn3y1: 2}`.

🔴 **Honest residual:** `868kr07fu`'s single surviving signal is
``#1065 … merged 08-16 (I confirmed). `868kr07fu` is u[nfixed]``. Nearest-ID attribution
is now correct, but the sentence is about a *different repo's* PR and the clause it
actually attaches to says **un**fixed. Right verdict, wrong reason. Lexical proximity
cannot fix this; see "Still weak" in `SKILL.md`.

---

# Round 2 — 2026-08-19 (acting on the evaluation)

Structural false positives were gone after round 1, but the tool still could not see the
authority (the ticket) and believed every PR citation. Five changes:

- **ClickUp status + priority + newest comment** carried through `recent-comments.py` into
  every verdict line.
- **`disagreements()`** cross-checks ticket status, newest comment, and transcripts.
- **PR references resolved** via `gh pr view`, cached and deduped.
- **`OPEN_PATTERNS` tightened** — "still running"/"still waiting" removed from the bare
  alternation (100% noise in a real run) and kept only in a ticket-scoped pattern; a
  `blocked on <permission|scope|token|…>` pattern added.
- **Seam closed**: `check-completion.py` now reads user text too. `search-sessions.py`
  always did, so a session selected on a user-only mention previously reported
  `mentions_found: 0`, and a human writing "this is still broken" was invisible.
- **`extract_signals()` deleted** — dead once the fallback went, with 3 tests exercising
  unreachable code.

## Behavioural red at base (round 2)

Three round-2 tests fail at base only on the tuple shape, so the behaviour was measured
directly against the pre-fix API:

```
BASE — process noise scored as TICKET-OPEN signals:
  1 hit(s) <- the throwaway Postgres is still running on port 5543
  1 hit(s) <- CI is still running rather than failing
  1 hit(s) <- The mirror agent is still running; I'll report when
BASE — user-message read?  no_sessions_found
```

## Suite and mutation

**55 passed, 0 failed, rc=0** (read unpiped). **15/15 mutants KILLED, 0 survivors**,
IDENTITY control clean.

Two findings the sweep produced that review had not:

- **M12 SURVIVED twice.** A markdown-stripping step (a `re.sub` over emphasis characters) had no
  killing mutation. First response was to add the test it lacked
  (`test_adjacency_breaks_a_repo_tie_through_markdown`); it survived again, because the
  bounded lookbehind already saw past the emphasis. The step was **deleted** — it was
  doing nothing. M12 now mutates `REPO_LOOKBEHIND` instead, and dies.
- **M9 NO-APPLY** — the mutant still targeted a line an earlier refactor had removed, so
  it was silently testing nothing. A mutation harness needs its own "did this even apply"
  check; ours prints `NO-APPLY` and exits non-zero.

## End-to-end after round 2

The report now leads with the thing that actually needed a human:

```
## Needs a decision
⚠️  868kr07fu: newest comment reads as RESOLVED but the ticket is still `to do`
    — close it, or say why it stays open.
```

🔴 **A false positive introduced and caught in the same session.** The first repo-inference
rule scanned the whole snippet for a known repo name. "civitai" appears in nearly every
snippet in this corpus (org name, sibling repos, prose), so every bare `#N` resolved to
`civitai/civitai` and reported real-but-unrelated PRs — including
`civitai/civitai#1080: merged 2024-03-18`. Replaced with a 30-character lookbehind
(`REPO_LOOKBEHIND`); pinned by `test_distant_repo_name_does_not_claim_a_bare_ref`. This is
the hazard the code comments warn about, walked into while writing the warning.

Runtime unchanged at ~41–45s for `--limit 5` (PR lookups are cached and deduped).

---

# Round 3 — 2026-08-20

## D4: the checker read its OWN PREVIOUS RUNS

Round 1 stopped the checker reading the transcript it was being written into
(`$CLAUDE_CODE_SESSION_ID`). It did nothing about **yesterday's run**, which is the same
defect one day later and strictly worse: a prior report prints each task ID directly beside
`likely_addressed`, `✓ resolved` and `merged`, so it is not merely noise — it is the exact
lexical shape the proximity scorer is built to reward, and it ranks first.

Found by running the tool for real. Both tasks in the report scored `likely_addressed`; the
only substantive session behind either verdict was the previous run (`acf5d5fb…`, 14 hits).
The cited evidence was the tool's own caveat text and an unrelated `scripts/obs-read` path
lesson. Removing that one session:

| Task | Verdict citing the prior run | Verdict without it | Ground truth |
|---|---|---|---|
| `868kr0799` | ✅ `likely_addressed` | ❓ `unclear` (2 mentions) | ✅ open — comment reads *"Still live, do not close"* |
| `868kuam02` | ✅ `likely_addressed` | 🔍 `no_mentions_found` (0 mentions) | ✅ untouched; two cover-image comments unanswered |

Left alone this is self-reinforcing: each run writes the evidence the next run reads.

## Marker selection — the numbers that ruled out the obvious choices

Counted over `~/.claude/projects` (~250 transcripts) before anything was added:

| Candidate marker | Transcripts matched | Verdict |
|---|---|---|
| `check-clickup-addressed` | 213¹ | unusable — the skill catalog is injected into every session |
| `/check-clickup-addressed` | 32 | unusable — substring of the skill's own **path** |
| `<command-name>/check-clickup-addressed</command-name>` | 2 | ✅ the slash invocations |
| `check-clickup-addressed/scripts/` | 6 | ✅ pipeline runs + the sessions that BUILT the skill |
| `## Task Completion Status` | 1 | ✅ the report header, for a pasted-output session |

¹ 🔴 **These counts are WRONG and are kept only as the historical record.** They came from a recursive `grep` over `~/.claude/projects`, which walks `<session>/subagents/` and `tool-results/` — files these scripts never open. Re-measured 2026-08-21 over the population they actually walk (735 transcripts): bare name **67 (9.1%)**, bare slash form **24 (3.3%)**, real anchored markers **4**. The verdict survives; the figures did not. Current numbers live in `SKILL.md` and `scripts/check-clickup-addressed/_selfrun.py`.

The `check-clickup-addressed/scripts/` marker also catches the skill's own build sessions,
which is correct rather than incidental: their test fixtures hardcode real task IDs
(`868krn3y1`, `868kr07fu`) next to mock completion text.

Failure direction is deliberate. A false positive drops a session and degrades a verdict to
`unclear`/`no_mentions_found` — which this skill already documents as *no evidence either
way*. A false negative restores the confident-✅-over-a-live-ticket bug.

## D5: "resolved" in a comment that says DO NOT CLOSE

Round 2's headline feature — flag a ticket whose newest comment reads as resolved — fired
on `868kr0799`, telling the operator to close it. The comment:

> **Still live, do not close.** … MeiliSearch: P95 > 5s (Saturation Burst) fired and
> **resolved** repeatedly, A=18.97 at 7:55

"resolved" there describes an *alert cycling*. No keyword can separate those two senses, so
an explicit refusal to close (`KEEP_OPEN_RE`) now **vetoes** the flag outright rather than
being weighed against it — the flag is an instruction to a human, not a score.

## Red-at-base / green-at-HEAD

Base = the same tree with the guard neutered (`is_self_run` → `return False`;
`keep_open` → `None`), which is exactly the pre-change program.

| Test | At base | At HEAD |
|---|---|---|
| `test_prior_run_is_not_read_as_evidence` | ✗ `read back as evidence: 'likely_addressed'` | ✓ |
| `test_search_sessions_drops_prior_runs` | ✗ `prior run leaked: ['prior-run', 'real-work']` | ✓ |
| `test_each_self_run_marker_fires_on_its_own` | ✗ `marker never fires` | ✓ |
| `test_skill_tool_invocation_is_a_self_run` | ✗ `Skill tool_use not recognised` | ✓ |
| `test_keep_open_comment_vetoes_the_close_flag` | ✗ `told the operator to close a ticket whose comment says not to` | ✓ |
| `test_prior_run_positive_control` | ✓ | ✓ |
| `test_ordinary_work_session_is_still_read` | ✓ | ✓ |
| `test_skill_catalog_mention_is_not_a_self_run` | ✓ | ✓ |
| `test_self_run_marker_set_has_not_drifted` | ✓ | ✓ |
| `test_keep_open_veto_does_not_silence_genuine_resolutions` | ✓ | ✓ |

🔴 The last four are **false-positive controls, not regression coverage** — they pass at
base by construction (base never flags anything as a self-run, so it trivially never
mis-flags one). They earn their place under mutation, below, not here.

## Mutation sweep

| Mutant | Verdict | Killed by (own message) |
|---|---|---|
| BASELINE (guard neutered — harness control) | 4 red / 25 green | see table above |
| M13 drop the `## Task Completion Status` marker | KILLED | `test_each_self_run_marker_fires_on_its_own` + the defect returns as `likely_addressed` |
| M14 drop the `<command-name>…` marker | KILLED | `test_each_self_run_marker_fires_on_its_own`, `test_self_run_marker_set_has_not_drifted` |
| M15 widen marker to bare `check-clickup-addressed` | KILLED | `test_skill_catalog_mention_is_not_a_self_run` (the over-broad direction) |
| M16 `keep_open` → `None` | KILLED | `test_keep_open_comment_vetoes_the_close_flag` |

M15 is the one that matters for the controls: it is the only mutant testing the *widening*
direction, and it dies on a test that is green at base.

🔴 **`test_each_self_run_marker_fires_on_its_own` iterates `EXPECTED_SELF_RUN_MARKERS`, a
literal list in the test file — deliberately NOT `selfrun.SELF_RUN_MARKERS`.** Iterating the
implementation's own tuple makes "delete a marker" pass vacuously: the deleted marker simply
stops being tested. `test_self_run_marker_set_has_not_drifted` pins the tuple as a ledger, so
the set cannot grow or shrink without a deliberate edit to the test.

A marker escape also had to be fixed: the first `SELF_RUN_RE` matched only raw JSON
(`"skill":"…"`), missing the backslash-escaped form a transcript uses when it quotes a call
inside message text. Caught by the marker test, not by review.

## End-to-end after round 3

```
❓ **868kr0799** — transcripts say `unclear` (searched 2 sessions, 2 mentions)
     ClickUp says: **to do** / prio urgent
🔍 **868kuam02** — transcripts say `no_mentions_found` (searched 1 sessions, 0 mentions)
     (ignored 1 transcript(s) that are prior runs of this checker …)
## Summary: 0 addressed, 0 partial, 0 open, 2 unclear
```

The skip count is printed rather than swallowed: a guard nobody can watch fire is
indistinguishable from one wired to nothing.

---

# Round 4 — 2026-08-21

Found by reading the code against its own documentation, not by a failing test. Both
defects are **silent no-ops**: a feature the docs describe, that runs, and does nothing.

## D6: an unrecognised ClickUp status disabled every ticket/comment cross-check

`disagreements()` gates all three of its checks on `OPEN_STATUSES` / `DONE_STATUSES` — nine
hardcoded words. ClickUp statuses are per-list and arbitrary. Measured at base:

| `clickup_status` | flags emitted |
|---|---|
| `to do` | ✅ "newest comment reads as RESOLVED but the ticket is still `to do`" |
| `open` | ✅ same |
| `in review` | **[]** |
| `blocked` | **[]** |
| `needs qa` | **[]** |
| `review` | **[]** |

The keep-open veto — round 3's headline safety fix — is inside the same gate, so it was off
too. Nothing was printed either way, and an empty **Needs a decision** block reads as
*checked, nothing disagrees*.

Fix: an unrecognised (or missing) status now emits a flag naming the status, saying the
cross-check **DID NOT RUN**, and listing the two sets to add it to; an explicit "do not
close" in the comment is surfaced regardless of status, since that direction is safe and it
is the reporter's own instruction. 🔴 **Widening the sets is deliberately not the fix** — it
only moves the silence to the next unknown word.

## D7: the proximity tier was unreachable, and its confidence marker was a constant

`proximity = 1.0/(1.0 + distance)`, ×1.5 when the task ID is inside the signal's own
±40-char snippet. But `extract_text_windows` — the only producer of windows in production —
hardcodes `distance = 0`:

```
windows.append((text[window_start:window_end], 0, pos - window_start))
```

So production proximity is only ever **1.0 or 1.5**, both above the `> 0.5` "close"
threshold. Consequences, all measured:

- `close_completion` was always `== completion` ⇒ the three branches behind the tier
  (`elif completion and not open_items` …) were **unreachable**.
- The `●` / `○` marker printed **`●` for every signal ever emitted**, including one 2000
  characters from the mention. Confirmed on a live run of `868kr07fu`: every line `●`.
- The two tests covering proximity (`test_proximity_scoring`,
  `test_extract_signals_from_windows`) hand-build windows with `distance` 100 and 500 —
  values production cannot produce. Green over a path that does not exist. One of them is
  additionally wrapped in `if open_items:` and can pass vacuously.

The tier is **vestigial**: the round-1 full-text fallback, deleted for D1, was the only
thing that ever set a non-unit proximity (a hardcoded 0.5). Round 1 removed the fallback
and left its scoring machinery behind.

Fix: delete the tier (four branches remain); keep the ×1.5 weight, which is real and does
vary, and rename what it means — `confidence_marker()` reports **adjacency**: `●` the task
ID is inside this signal's own snippet, `○` merely somewhere in the ±2000-char window.

**Verdict-neutrality was measured, not argued** — round-3 code vs round-4 code, same live
corpus, same second:

| Task | round 3 | round 4 |
|---|---|---|
| `868kr07fu` | partially_addressed 8/7/2c/1o | identical |
| `868krn3y1` | unclear 5/6/0c/0o | identical |
| `868ktvqf9` | partially_addressed 6/25/5c/5o | identical |
| `868kt8pfu` | partially_addressed 4/29/3c/4o | identical |
| `868kr0799` | unclear 5/2/0c/0o | identical |
| `868kuam02` | no_mentions_found 1/0/0c/0o | identical |

And the marker now discriminates: on `868kt8pfu` **all seven** signals are `○` — none of
that evidence has the task ID in its own snippet, which is exactly what the operator needed
to know and previously could not see.

## D8 (bonus): `--include-self-runs` was documented on the entry point and never parsed

`check-addressed.py`'s arg loop ended in `else: i += 1`, swallowing every unrecognised flag.
Base behaviour, measured by capturing the argv handed to each sub-process:

```
recent-comments.py:  [--limit 3 --json]
search-sessions.py:  [--limit 5 --json --exclude-session <id> <task>]
check-completion.py: [--task <task> --json --exclude-session <id>]
'--include-self-runs' forwarded anywhere?  False
```

The run completed and printed a normal report. Fix: `parse_args()` handles every flag and
**exits 2 on an unknown one**, and the flag is forwarded to both sub-scripts.

## Red-at-base / green-at-HEAD

Base = the round-3 commit (`f5434a399`, pushed as `acf9eb0b9`).

| Test | At base | At HEAD |
|---|---|---|
| `test_unknown_clickup_status_is_flagged_not_silently_skipped` | ✗ `status 'in review': cross-check silently skipped` | ✓ |
| `test_missing_clickup_status_is_flagged_too` | ✗ `a missing ClickUp status silently skipped the cross-check` | ✓ |
| `test_keep_open_is_surfaced_even_on_an_unknown_status` | ✗ `'do not close' never reached the operator: []` | ✓ |
| `test_confidence_marker_distinguishes_adjacent_from_same_window` | ✗ (see note) | ✓ |
| `test_include_self_runs_is_recognised_by_the_orchestrator` | ✗ (see note) | ✓ |
| `test_include_self_runs_is_forwarded_to_both_subscripts` | ✗ (see note) | ✓ |
| `test_known_flags_still_parse` / `test_unknown_flag_is_rejected_not_swallowed` | ✗ (see note) | ✓ |
| `test_production_windows_are_all_distance_zero` | ✓ | ✓ |
| `test_every_production_signal_scores_above_the_deleted_threshold` | ✓ | ✓ |
| `test_deleting_the_tier_preserves_every_verdict` | ✓ | ✓ |
| `test_known_open_status_still_flags_a_resolved_comment` | ✓ | ✓ |
| `test_known_status_veto_still_wins` | ✓ | ✓ |
| `test_agreeing_ticket_still_produces_no_flag` | ✓ | ✓ |
| `test_self_run_guard_is_on_by_default` | ✓ | ✓ |

🔴 **Note — five of those reds are weak**, exactly as in round 1: they fail at base on
`AttributeError` (`no attribute 'confidence_marker'` / `'parse_args'`), i.e. on API shape,
not behaviour. Both behaviours were therefore measured directly against the base program
instead — the all-`●` live run for the marker, and the captured sub-process argv above for
the flag. Those measurements are the evidence; the tests are the ratchet.

The seven ✓-at-base rows are **INVARIANT GUARDS / false-positive controls**, not regression
coverage. Two of them carry real weight anyway: `test_production_windows_are_all_distance_zero`
is the premise that licenses D7's deletion (M25 proves it is live), and
`test_self_run_guard_is_on_by_default` is the only test of the *widening* direction (M24).

## Mutation sweep

Pristine copy per mutant, `__pycache__` excluded, `PYTHONDONTWRITEBYTECODE=1` in the child.
Harness reports `NO-APPLY` and fails when a mutant's target text is absent.

| Mutant | Verdict | Killed by (own message) |
|---|---|---|
| IDENTITY (harness control) | OK — 80 passed | — |
| M17 unknown-status branch never fires | KILLED | `test_unknown_clickup_status_is_flagged_not_silently_skipped` |
| M18 keep-open not surfaced on unknown status | KILLED | `test_keep_open_is_surfaced_even_on_an_unknown_status` |
| M19 marker threshold back to the dead `0.5` | KILLED | `test_confidence_marker_distinguishes_adjacent_from_same_window` |
| M20 unknown flag swallowed again | KILLED | `test_unknown_flag_is_rejected_not_swallowed` |
| M21 `--include-self-runs` not parsed | KILLED | `test_include_self_runs_is_recognised_by_the_orchestrator` |
| M22 not forwarded to search-sessions | KILLED | `test_include_self_runs_is_forwarded_to_both_subscripts` |
| M23 not forwarded to check-completion | KILLED | `test_include_self_runs_is_forwarded_to_both_subscripts` |
| M24 forwarded UNCONDITIONALLY (widening) | KILLED | `test_self_run_guard_is_on_by_default` |
| M25 producer emits a non-zero distance | KILLED | `test_production_windows_are_all_distance_zero` |

M22 and M23 are separated deliberately: one test claims the flag reaches **both** stages, so
each stage needs its own mutant or the claim is half-checked. Their kill messages differ
(`reached: {'check-completion.py'}` vs `{'search-sessions.py'}`), which is what proves the
test is not passing for the other stage's reason.

🔴 **The sweep found a defect in the TEST HARNESS that no test could have.** M21 first came
back `HARNESS ERROR — no total line`: `parse_args` calls `sys.exit(2)`, and `SystemExit` is
a `BaseException`, so `run_all.py`'s `except Exception` did not catch it. It escaped the
loop and killed the entire run — no remaining test files, no `Total:` line, and an exit code
this skill's own docs tell you not to read. **A suite that dies prints nothing, which is not
distinguishable from a suite that has nothing to say.** `run_all.py` now catches
`SystemExit` and scores it as a failure naming the test; M21 then died by name.

## Suite

**80 passed, 0 failed** (counted, unpiped). Harness: `/tmp/ccua-mutate.py`, session-local,
not committed.

---

# Round 5 — 2026-08-21

Found by **running the tool for real** after round 4 shipped, not by reading it and not by a
failing test — the suite was 80/0 green over every one of these. Round 4's own lesson,
one layer up: the defects live where nothing was looking.

## D9: `_repo_for_ref` has THREE failure modes and reported them all as one — the wrong one

`_repo_for_ref` returns `None` in more than one situation and `annotate_pr_refs` rendered
every one as `unresolved (repo not named)`. Measured live on the report's own output for
`868ktvqf9`, snippet ``| **#4181**, **devrc #591** | merged, verified by content |``:

```
  ✓ PR merged: red **both inherited**; unaudited | | **#4181**, **devrc #591** | merged, verifi
      ↳ #4181: unresolved (repo not named)
      ↳ #591:  unresolved (repo not named)      <-- FALSE. the repo IS named.
```

The regex captured `word='devrc'` for the second one — the repo was named; `devrc` simply
is not one of the six `KNOWN_REPOS` keys. Both PRs resolve by hand:
`civitai/civitai#4181` **MERGED**, `innovation-upstream/devrc#591` **MERGED** (both
`gh pr view`, 2026-08-20). So round 2's "is this cited PR actually still open?"
cross-check — the whole point of the `gh` lookups — contributed **nothing** on the only
task in the run that had completion signals, while reporting a cause that was wrong.

Same closed-vocabulary silent-miss class as D6, but worse: **a wrong explanation stops the
reader looking**, where an honest "I don't know this repo" sends them to add it.

Fix, in three parts:

1. `_repo_for_ref` returns `(repo, reason)` and `_unresolved_state` renders the reason —
   `repo not named` (correct, and kept: the refusal to guess is the round-2 fix),
   `repo 'X' is not in KNOWN_REPOS — add it`, and `ambiguous — A, B both in range`.
   Resolution ORDER is byte-for-byte the pre-change control flow, so **no citation changes
   repo**; only the text of a failure changes.
2. `KNOWN_REPOS` gains the three hub repos it was missing —
   `storage-resolver` → `civitai/storage-resolver`, `flipt-state` → `civitai/flipt-state`,
   `devrc` → **`innovation-upstream/devrc`**. Every owner verified with
   `gh repo view <owner>/<name>`; `civitai/devrc` **does not exist**, and the owner is
   precisely the part a guess gets wrong.
3. 🔴 **Widening the dict is explicitly not the fix**, and the code says so. It only moves
   the silence to the next repo nobody thought of. The message is the durable half.

The word on the `#` is reported **as written, unclassified**. Nothing short of enumerating
the world separates `devrc` from `landed`, and a reader separates them at a glance where a
heuristic cannot — so the honest output is "here is the word that sat on the `#`, and it is
not in my table", noise included.

## D10: round 4's own fix was half-delivered — the marker never reached the report

Round 4 built `confidence_marker()` (`●` = the task ID is inside the signal's own ±40-char
snippet, `○` = merely in the ±2000-char window) and wired it into **`check-completion.py`'s
own printer only**. `check-addressed.py` — the entry point the Quick start tells you to
run, and the only one anyone runs — never called it. Its `✓`/`○` mark
completion-vs-open, not confidence.

Base rendering, measured with the printer's own code over two signals that differ ONLY in
adjacency:

```
  ✓ PR merged: ADJACENT     (proximity 1.5)
  ✓ PR merged: FAR AWAY     (proximity 1.0)   <-- identical
  ○ still open: ADJ         (proximity 1.5)
  ○ still open: FAR         (proximity 1.0)   <-- identical
```

This is the "a guard's DESCRIPTION claims coverage wider than its implementation" defect,
walked into **while fixing that defect class**. A marker nobody sees is the same no-op as a
marker that cannot vary; round 4 fixed one half and left the other.

Fix: `signal_line()` renders adjacency as a **second, bracketed column** —
`✓ [●] …` / `○ [○] …` — because `○` already meant "open item" in the first column, and
merging two orthogonal facts into one glyph makes the report unreadable rather than silent,
which is not an improvement. `check-addressed.py` **imports** the marker from
`check-completion.py` rather than re-deriving it; one threshold, one place. A legend is
printed once per report.

## D11: the highest-signal state in the tool produced no flag at all

`disagreements()` emitted nothing for a task that is OPEN, has **zero** transcript
evidence, and carries a recent comment from someone else. Measured live on `868kuam02`:
`to do` / high, comment from @Ellie King 2026-08-20, `mentions_found: 0` — and the
**Needs a decision** block said nothing about it.

Every existing rule stayed quiet because **nothing disagrees**: the ticket is open, the
transcripts are empty, and they agree with each other. But "a colleague asked you something
and no work exists anywhere" is arguably the most actionable thing this tool can detect,
and it is exactly the reassuring-nothing the whole skill exists to police.

Fix: `_waiting_on_a_human()`, three conditions, and the **bounding choice matters more than
the detection**:

| Condition | Implementation | Why this shape |
|---|---|---|
| zero evidence | `r.get("mentions_found") != 0 → skip` | the STATE, not the word — `no_sessions_found` is the same zero, and a guard spelled against `no_mentions_found` passes while the hazard exists in the other's shape. **Present and zero**, never `get(…, 0)`: see the finding below. |
| not done | `cu in DONE_STATUSES → skip` | deliberately NOT `cu in OPEN_STATUSES` — that would rebuild D6's blind spot, silently losing the flag on `in review` / `blocked`. Safe in either direction, like the keep-open veto. |
| recent comment | `age > 14d → skip` | **recency, not priority** |

🔴 **Why recency and not priority.** Priority is a property of the *ticket*: set once, often
stale, and completely unchanged by anyone asking anything — so a priority bound would fire
on every unstarted high-priority backlog item on every run, and a permanently-noisy block
trains the reader to skip it (the same failure as a permanently-red gate). Recency is a
property of the *interaction*: it says a human is waiting **now**, it self-clears as the
comment ages so the flag's false-positive volume is bounded by construction, and it
composes with the tool's own sampling — the report already takes the N most recent
comments. The comment is guaranteed not to be the user's own; `recent-comments.py` drops
every comment whose author id equals `me` before any of this runs.

## Also fixed

`SKILL.md` claimed `--limit 5` takes **~41s**. Re-measured 2026-08-21 over two runs:
**98.5s** and **85.4s** wall. Corrected to ~85–100s, with a note to re-time rather than
quote — the per-task transcript sweep scales with `~/.claude/projects`, so it will keep
drifting.

## Red-at-base / green-at-HEAD

Base = `f736c0611` (round 4), extracted clean with `git archive` — **not** read from the
primary clone. HEAD tests run against those pristine base scripts:
**80 passed, 23 failed**. At HEAD: **103 passed, 0 failed**.

| Test | At base | Strength |
|---|---|---|
| `test_a_named_but_unknown_repo_is_not_reported_as_unnamed` | ✗ `a NAMED repo was reported as unnamed — the message is false: 'unresolved (repo not named)'` | **strong** |
| `test_the_unknown_repo_message_says_what_to_do` | ✗ `the message does not name the table to add the repo to` | **strong** |
| `test_devrc_now_resolves_to_innovation_upstream` | ✗ `devrc #591 did not resolve to innovation-upstream/devrc: []` | **strong** |
| `test_the_hub_repos_are_all_present_and_correctly_owned` | ✗ `KNOWN_REPOS drifted` (3 missing) | **strong** (ledger) |
| `test_ambiguous_repos_are_named_rather_than_called_unnamed` | ✗ `ambiguity reported as absence` | **strong** |
| `test_ambiguous_repo_stays_unresolved` (round-2 test, message updated) | ✗ `'unresolved (repo not named)'` | **strong** |
| `test_distant_repo_name_does_not_claim_a_bare_ref` (round-2 test, message updated) | ✗ `the word on the '#' is not reported` | **strong** |
| `test_both_marker_values_reach_a_real_report` | ✗ `live-shaped adjacent signal not marked ●: '  ✓ PR merged: ADJACENT'` | **strong** (through `main()`) |
| `test_the_marker_legend_is_printed_in_the_report` | ✗ `no legend glyphs in the report` | **strong** (through `main()`) |
| `test_disagreements_still_works_without_an_explicit_now` | ✗ `lost its default clock and stopped flagging a fresh comment` | **strong** (positional call) |
| `test_report_lines_carry_the_adjacency_marker` | ✗ `no attribute 'signal_line'` | 🔴 **WEAK** |
| `test_the_adjacency_marker_does_not_collide_with_the_open_glyph` | ✗ `no attribute 'signal_line'` | 🔴 **WEAK** |
| `test_the_entry_point_agrees_with_check_completion_at_every_boundary` | ✗ `no attribute 'confidence_marker'` | 🔴 **WEAK** |
| ×10 D11 tests | ✗ `disagreements() got an unexpected keyword argument 'now'` | 🔴 **WEAK** |
| `test_a_genuinely_unnamed_ref_still_says_not_named` | ✓ | INVARIANT GUARD |
| `test_an_unknown_repo_is_still_never_guessed` | ✓ | INVARIANT GUARD / widening control |
| `test_a_stale_backlog_comment_does_not_fire` | ✓ | INVARIANT GUARD / widening control |
| `test_evidence_in_the_transcripts_suppresses_the_flag` | ✓ | INVARIANT GUARD / widening control |
| `test_a_closed_ticket_does_not_fire` | ✓ | INVARIANT GUARD / widening control |
| `test_a_record_with_no_mention_count_does_not_fire` | ✓ | INVARIANT GUARD / widening control |
| `test_a_record_with_no_comment_date_does_not_fire` | ✓ | INVARIANT GUARD / widening control |
| `test_round_four_flags_all_still_fire` | ✗ (kwarg) | 🔴 WEAK; round-4 regression control |

🔴 **Thirteen of those reds are WEAK** — they die at base on `AttributeError` /
`TypeError` (API shape), not on behaviour, exactly as in rounds 1 and 4. Every one of the
three behaviours was therefore **measured directly against the unmodified base program**
instead, and those measurements are the evidence; the tests are the ratchet:

```
BASE annotate_pr_refs on the live snippet ->
    {'ref': '#4181', 'state': 'unresolved (repo not named)'}
    {'ref': '#591',  'state': 'unresolved (repo not named)'}   <-- regex captured word='devrc'

BASE report rendering (printer code copied verbatim from check-addressed.py main()):
    ✓ PR merged: ADJACENT (prox 1.5)   /   ✓ PR merged: FAR AWAY (prox 1.0)   <-- identical
    ○ still open: ADJ     (prox 1.5)   /   ○ still open: FAR     (prox 1.0)   <-- identical

BASE disagreements() for the live 868kuam02 shape -> []
BASE live run's "Needs a decision" block -> one flag (868kr0799 keep-open), nothing about 868kuam02
```

The seven ✓-at-base rows are **false-positive controls, not regression coverage** — base
flags nothing and resolves nothing, so it trivially never mis-flags. They earn their place
under mutation (M29, M30, M36–M40), which is the only place a widening defect can die.

## Mutation sweep

Pristine copy per mutant, `__pycache__` excluded from the copy, `PYTHONDONTWRITEBYTECODE=1`
in the child. **18/18 KILLED, 0 survivors.** Harness `/tmp/ccua-mutate5.py`, session-local,
not committed.

| Mutant | Verdict | Killed by (own message) |
|---|---|---|
| IDENTITY (harness control) | OK — 103 passed, 0 failed | — |
| M26 `devrc` dropped from `KNOWN_REPOS` | KILLED | `test_devrc_now_resolves_to_innovation_upstream` — `did not resolve to innovation-upstream/devrc: []` |
| M27 unknown-repo message reverts to the false "not named" | KILLED | `test_a_named_but_unknown_repo_is_not_reported_as_unnamed` — `a NAMED repo was reported as unnamed` |
| M28 ambiguous message reverts to "not named" | KILLED | `test_ambiguous_repos_are_named_rather_than_called_unnamed` — `ambiguity reported as absence` |
| **M29 WIDENING**: an unknown word is GUESSED under `civitai/` | KILLED | `test_distant_repo_name_does_not_claim_a_bare_ref` — `a distant repo name claimed a bare ref: [('civitai/landed', '1080')]` |
| **M30 WIDENING**: the genuinely-unnamed case relabelled "unknown" | KILLED | `test_a_genuinely_unnamed_ref_still_says_not_named` — `the honest not-named case was relabelled` |
| M31 report line drops the adjacency marker | KILLED | `test_report_lines_carry_the_adjacency_marker` — `adjacent signal is not marked ●: '  ✓ PR merged: ADJACENT'` |
| M32 marker merged into the kind glyph (no brackets) | KILLED | `test_the_adjacency_marker_does_not_collide_with_the_open_glyph` — `the two ○ meanings are not separable` |
| M33 legend never printed | KILLED | `test_the_marker_legend_is_printed_in_the_report` — `no legend glyphs in the report` |
| M34 entry point re-implements the marker at the dead `0.5` | KILLED | `test_report_lines_carry_the_adjacency_marker` — `a same-window-only signal was marked ●: '  ✓ [●] PR merged: FAR AWAY'` |
| M35 waiting flag never fires | KILLED | `test_an_unanswered_comment_with_zero_evidence_is_flagged` — `produced no flag: []` |
| **M36 WIDENING**: waiting flag fires unconditionally | KILLED (9 red) | `test_no_flag_when_ticket_and_comment_agree` — `false disagreement raised: ['868krn3y1: @x is WAITING — unconditional']` |
| M37 recency bound removed | KILLED | `test_a_stale_backlog_comment_does_not_fire` — `every unstarted backlog item with an old comment would be flagged forever` |
| M38 evidence no longer suppresses the flag | KILLED | `test_evidence_in_the_transcripts_suppresses_the_flag` — `the flag fired over a task the transcripts actually discuss` |
| M39 a DONE ticket reported as waiting | KILLED | `test_a_closed_ticket_does_not_fire` — `a closed ticket was reported as waiting` |
| M40 mention count defaulted to zero when absent | KILLED | `test_a_record_with_no_mention_count_does_not_fire` — `a record that never reported a mention count was read as zero evidence` |
| M41 an unreadable comment date is swallowed | KILLED | `test_an_unparseable_comment_date_is_surfaced_not_swallowed` — `silently disabled the flag` |
| M42 D6 blind spot rebuilt: gated on `OPEN_STATUSES` | KILLED | `test_an_unknown_clickup_status_does_not_disable_the_waiting_flag` — `an unrecognised status silently disabled the waiting flag — D6 all over again` |
| M43 zero-evidence gate spelled against one status word | KILLED | `test_no_sessions_found_is_the_same_zero_evidence_state` — `no_sessions_found is zero evidence too` |
| NO-APPLY CONTROL (target text deliberately absent) | OK — harness refused, as designed | — |

### 🔴 Two things the sweep found that review and a green suite did not

**M42 SURVIVED the first sweep, over a 103/0 green suite.** The mutant rebuilds D6's exact
blind spot — gating the new flag on `OPEN_STATUSES` instead of `not in DONE_STATUSES`, so
`in review` silently loses it. `test_an_unknown_clickup_status_does_not_disable_the_waiting_flag`
asserted only `assert flags` (non-empty), and `in review` **also** trips round 4's
unknown-status announcement — so the test was **green for the other guard's reason** and
stayed green with the thing it was written to protect deleted. Fixed by asserting the
`WAITING` flag specifically, plus a positive control that round 4's announcement is *also*
present, so it cannot pass by having quietly suppressed that one instead. This is the
"a DIFFERENT guard's error kills your test" trap, and only the sweep could see it.

**The first draft of the D11 flag fired on a round-2 control**, `test_no_flag_when_ticket_and_comment_agree`,
whose fixture omits `mentions_found` entirely and has no comment date. The draft used
`r.get("mentions_found", 0) != 0`, i.e. it inferred *"no work exists anywhere"* — the
strongest claim the flag makes — from a key that was simply **absent**. Corrected to
require the count be **present and zero**, and to treat an ABSENT comment date (no
interaction) differently from a MALFORMED one (formatter drift, which announces itself).
An existing false-positive control caught a real design error in a new feature; that is
what they are for.

## End-to-end: the same live run, before and after

Same corpus, `--limit 5`, ~15 minutes apart.

**Before** (`f736c0611`, 98.5s):
```
  ✓ PR merged: red **both inherited**; unaudited | | **#4181**, **devrc #591** | merged, verifi
      ↳ #4181: unresolved (repo not named)
      ↳ #591: unresolved (repo not named)
  ○ still open: fact instead of reasoning about it.  ## Still open, unchanged  - **`868ktvqf9`**
  ○ needs action: commands** (`inbox-*`, `doc-comments`) need a JWT in the account, not   just a t

## Needs a decision
⚠️  868kr0799: newest comment says "Still live" — do NOT close. …
```

**After** (85.4s):
```
Evidence lines: `✓` completion signal / `○` open signal · then `[●]` the task ID is inside
that signal's own snippet, `[○]` merely somewhere in the same ±2000-char window — distrust `[○]`.

  ✓ [●] PR merged: red **both inherited**; unaudited | | **#4181**, **devrc #591** | merged, verifi
      ↳ #4181: unresolved (repo not named)
      ↳ innovation-upstream/devrc#591: merged 2026-08-20
  ○ [●] still open: fact instead of reasoning about it.  ## Still open, unchanged  - **`868ktvqf9`**
  ○ [○] needs action: commands** (`inbox-*`, `doc-comments`) need a JWT in the account, not   just a t

## Needs a decision
⚠️  868kr0799: newest comment says "Still live" — do NOT close. …
⚠️  868kuam02: @Ellie King is WAITING — the ticket is `to do`, and the task ID appears in NO
    transcript, so no work exists anywhere. Commented 1d ago; nobody has answered. Read it.
```

Round 4 behaviour confirmed intact in the same run: the keep-open veto still fires on
`868kr0799`, and the unknown-status announcement still fires (verified separately by
direct call on `in review` / `blocked` / `needs qa` — the live run could not exercise it,
because both real statuses that day were in the vocabulary).

`#4181` correctly **stays** `repo not named`: nothing repo-ish sits within 30 characters of
it. That is the one honest arm of the old message, and it is preserved.

## Residual — honest

- 🔴 **`#4181` is a real, resolvable PR (`civitai/civitai#4181`, merged) that this tool
  still reports as unresolvable**, and it is right not to guess. A 4-digit number in this
  corpus is *almost always* `civitai/civitai`, but "almost always" is what produced
  `civitai/civitai#1080: merged 2024-03-18` in round 2. The message now at least tells the
  reader the citation named no repo, rather than mislabelling why.
- **Naming the word on the `#` is noisy by design.** `issue #45` reports
  `repo 'issue' is not in KNOWN_REPOS`. Every alternative requires classifying which words
  are repo-shaped, which means enumerating the world; a reader discards `'issue'` at a
  glance and acts on `'devrc'`. Noisy-and-true beats quiet-and-false.
- **The waiting flag cannot see whether you already replied in ClickUp.** It reads the
  *newest* comment only, and `recent-comments.py` filters out your own — so a ticket where
  you answered and the colleague then commented again looks identical to one you ignored.
  The 14-day bound limits the damage; a real fix needs comment threading.
- **`UNANSWERED_COMMENT_DAYS = 14` is a judgement, not a measurement.** It was not tuned
  against a labelled set of "should have flagged" tickets, because none exists. It is
  chosen to be shorter than a sprint and long enough to survive a week off.
- The round-1 residual is unchanged: **a signal is attributed by lexical proximity, not by
  meaning.** The new `[●]` marker makes the *attribution distance* visible, which is the
  most a lexical tool can offer; it still cannot tell you the adjacent clause is about a
  different thing.

## Suite

**103 passed, 0 failed** (counted from the `Total:` line, unpiped). Up from 80: 21 new
tests in `test_repo_vocab_and_waiting.py`, plus two round-2 tests whose assertions were
updated to the new message text (their behavioural claim — *nothing was resolved* — is
unchanged and still asserted).

---

# Round 8 — 2026-08-21 (veto tiering, re-attempted fresh from trunk)

The keep-open veto redesign that rounds 4–7 could not land, rebuilt from `origin/trunk`
(`d11e67e87`) rather than cherry-picked from the abandoned branch `zach/ccua-self-run-guard`.
Those rounds each fixed the previous round's regression, so the design converged while the
diff carrying it did not; only the converged design is here.

## The defect has TWO directions and one cause

Trunk's veto is absolute. Measured with one instrument over 17 comments, on a `to do` ticket
whose transcripts read `likely_addressed`:

| Comment | trunk `d11e67e87` | round 8 |
|---|---|---|
| "Resolved on both counts, recommend closing. (The follow-up PR is still open but unrelated.)" | 🔴 do NOT close | READ IT |
| "One review thread is not resolved on the PR, but the ticket itself is done. Recommend closing." | 🔴 do NOT close | READ IT |
| "The alert fired and was not resolved automatically. Ticket work is done — recommend closing." | 🔴 do NOT close | READ IT |
| "The fix landed in #1234 and is live. The follow-up PR is still open but unrelated." | 🔴 do NOT close | READ IT |
| "The issue isn't resolved." | 🔴 **close it** | do NOT close |
| "The issue is never resolved." | 🔴 **close it** | do NOT close |
| "This won't be resolved until next sprint." | 🔴 **close it** | do NOT close |
| "This is not fully resolved." | 🔴 **close it** | do NOT close |
| "This is unresolved." | 🔴 close it or re-check¹ | do NOT close |
| "I do not recommend closing this yet." | 🔴 **close it** | do NOT close |
| "This is still open. I have not had time to look at it." | do NOT close | do NOT close |
| "The issue is not resolved." | do NOT close | do NOT close |
| "This is still open. I haven't done anything yet." | do NOT close | do NOT close |
| "This is still open. The bug is not fixed." | do NOT close | do NOT close |
| "This is still open — nothing has been merged." | do NOT close | do NOT close |
| "Still live, do not close." | do NOT close | do NOT close |
| "Resolved on both counts. Recommend closing." | close it | close it |

9 rows move, 8 hold. **The second block is the dangerous one and it is live on trunk today**:
six comments that refuse a close draw an affirmative instruction to close.

¹ 🔴 **CORRECTED by audit — this row is the odd one out and the original wording overstated
it.** `RESOLVED_COMMENT_RE` is `\bresolved\b`, and `unresolved` has no word boundary before
`resolved`, so trunk's COMMENT flag never fires on it: at `transcript_status="unclear"` trunk
emits **nothing at all**, and the "close it or re-check" above comes from the TRANSCRIPT
branch. So it is **5 of 6** comments drawing the comment-level close-it, not 6 of 6 — the
sixth draws an affirmative close instruction by a different mechanism. Same family as the
probe bug below: the first rebuild fixed the READ/close-it confusion and left the
comment-flag/transcript-flag confusion in place. **Every measurement in this table should be
taken at `transcript_status="unclear"`, where the transcript branch cannot contribute.**

🔴 **The instrument was wrong first.** The probe classified verdicts with
`"READ" in v.upper()`, which matches `reads as RESOLVED` inside the close-it line — so it
labelled the close-it flag as an ambiguity flag, in BOTH columns, silently. The table above
was only readable after the labels were rebuilt from disjoint literals.

## The fix: one mechanism, two consequences

**Negation decided once, at clause level.** `closure_claims()` matches the closure vocabulary
plainly, then asks whether the CLAUSE it sits in carries a negator. A new closure word
inherits negation handling for free — the thing per-word lookbehinds could never do, and the
reason rounds 6 and 7 each shipped a regression enumerating them. Contractions match by
**shape** (`\w+n't`), not by a stem list: `won't` has the stem "wo", so any enumeration is
incomplete when written. `unresolved` is handled as morphological negation.

**Two tiers.** STRONG is an instruction about this ticket and stays absolute. WEAK is
`still open` plus any negated closure claim; it vetoes when it is the comment's only word on
closure, and downgrades to "READ IT and decide" only when another clause carries an
un-negated closure claim. That is the round-5 lesson kept: *WEAK means ambiguous alongside a
closure claim, never ignorable* — the plainest keep-open phrasing must still veto.

Clause scope is load-bearing in **both** directions. Comment-wide scope lets one "not"
silence every closure word and makes the ambiguous tier unreachable. 🔴 **The claim that
followed here — "commas are not boundaries, because that would strand a negator away from its
target" — was REFUTED by measurement in round 8c below; commas ARE boundaries and the
interrupted-negation case is handled by a carry rule instead.** Parens are not boundaries.

`CLOSURE_VOCAB_RE` (wide) is deliberately NOT merged into `RESOLVED_COMMENT_RE` (narrow):
only the narrow one may instruct a human to close a live ticket, so widening it manufactures
close-it flags, while widening the wide one can only add a veto or downgrade one to "read it".
`test_widening_ambiguity_did_not_widen_the_close_it_trigger` is the control.

## Red-at-base / green-at-HEAD

**Of 27 new tests: 16 red at `origin/trunk` d11e67e87 for a BEHAVIOURAL reason, 3 red only
structurally (`AttributeError` on symbols this change introduces — they could never have been
red for a behavioural reason and are not regression coverage), 8 green at base. All 27 green
at HEAD.** An earlier draft of this section said "13 of 19 red" without that split, which
overstated regression coverage by 3. The 8 that pass
at base are labelled INVARIANT GUARD or CONTROL in their docstrings and are counted as
neither regression coverage nor evidence: they exist so the tiering cannot quietly eat
behaviour trunk already had right (the close-it flag firing on a clean resolution, a lone weak
refusal still vetoing, both tiers staying gated on an open status).

🔴 **One of them was vacuous and passed for the wrong reason.** `test_strong_wins_the_quote_
over_weak` asserted `"do not close" in j.lower()`, which the flag's own boilerplate `— do NOT
close.` satisfies. It passed at base *while base quoted the WEAK phrase*, and would have
passed with the tiers reversed. Rewritten to assert the quoted SPAN. This is the third round
running that found a test asserting a substring the surrounding sentence also contains.

## 🔴 The live run found what the table could not

The 17-case table above is synthetic, and it was green when the tool was run for real against
ClickUp — where it **downgraded a live keep-open**. `868ktt2ct`'s newest comment opens
*"Status check during ClickUp triage 2026-08-21 — **staying open**, and mostly not verifiable
from this repo"*, and its only closure vocabulary is one "shipped" describing a sub-item that
landed in a **different repo**. The tier read that as ambiguity and emitted "READ IT and
decide"; trunk's absolute veto had it right, by luck of a later "still open".

`stay(s|ing) open` / `remains open` / `leaving it open` are now STRONG. They belong in the
strong tier rather than the weak one for the reason round 6 used to demote `not resolved`,
running the other way: those phrasings are **not this domain's vocabulary for anything else**
— a PR or an alert is "open", never "staying open" — so a deliberate declaration about what
happens to THIS ticket has no second reading.

This is round 6's N1 in mirror image. There, the ambiguity vocabulary was too narrow against
real prose and the weak tier was nearly inert; here the STRONG vocabulary was too narrow
against real prose and a real refusal fell through it. **A vocabulary tested only against
sentences its author wrote is a claim about the author.**

## Mutation sweep — 18 mutants, all killed

Controls first: **CONTROL-identity SURVIVED 141/0** (the harness can report a survivor);
**CONTROL-positive KILLED**. A mutant whose anchor no longer applies, or whose substitution is
a no-op, is scored a **FAILURE**, not a survivor.

The first sweep left **3 survivors**, each a real gap rather than a missing assertion:

| Survivor | What it exposed | Closed by |
|---|---|---|
| stripping `but\|however\|…` from the clause splitter | every ambiguous fixture *also* carried a sentence boundary doing the same work, so the conjunction boundary was never exercised | a fixture where the negator and the closure claim share one sentence |
| dropping the negation filter on the close-it branch | it is the **IDENTITY** — unreachable, because every phrase `RESOLVED_COMMENT_RE` matches contains a `CLOSURE_VOCAB_RE` word, so a negated trigger is already vetoed one branch earlier | **deleted**, with `test_every_close_it_trigger_phrase_is_also_ambiguity_vocabulary` pinning the premise that licenses the deletion |
| ambiguity reading the snippet instead of the full comment | the two windows differ deliberately and nothing pinned it | a fixture whose closure claim sits past the 200-char display truncation |

🔴 **An unreachable guard reads as protection while providing none** — the same finding as
round 7's A4, reached independently. It is deleted rather than tested, and what replaces it is
a test of its PREMISE: add a phrase to `RESOLVED_COMMENT_RE` whose words are outside
`CLOSURE_VOCAB_RE` and that test goes red, which is the signal to bring the filter back.

The re-sweep kills all 18. The premise mutant was checked to kill the premise test **by its
own assertion message**, not merely to make the suite red — 10 tests fail under it, and a
mutant that dies to a neighbour's assertion is a green for the wrong reason.

## Suite

**141 passed, 0 failed**, up from 122 (`Total:` line, from a tree whose `__pycache__` was
purged by `run_all.py` — the round-7 stale-bytecode trap). Verified live end-to-end against
ClickUp, which is what caught the `868ktt2ct` downgrade above.

## Residual risk

- **`CLOSURE_VOCAB_RE` is a guess, like every vocabulary in this tool.** A comment that says
  the work is finished in words outside it ("wrapped up", "sorted", "no longer an issue")
  will not create ambiguity, so a weak refusal beside it still vetoes. That is the safe
  direction and it is why the tier downgrades rather than flips.
- **Clause splitting is punctuation-based, not a parser.** A comment written as one long
  comma-spliced sentence collapses to a single clause and reads as fully negated if any
  negator appears in it. Over-veto, not over-close.
- **`no` is a negator.** "There is no regression. Resolved — recommend closing." works only
  because the two sit in different sentences; written as one clause it would veto.
- **The STRONG vocabulary is still an enumeration**, and the live run proved one round of
  enumeration is not enough. Negation was moved to a shape rule precisely because enumerating
  loses; the keep-open phrasings have no equivalent shape, so they stay a list — and the way
  to find the next gap is to run the tool against real comments, not to add more fixtures.

---

# Round 8b — 2026-08-21 (blind audit of round 8, and the regression it caught)

Round 8 was audited BLIND — the auditor was given the worktree and the tool's priority order,
no conclusions. It confirmed the tiering idea and the deleted-identity-guard reasoning, and
then found that **round 8's own fix had introduced a defect worse than the one it fixed.**
🔴 That is the fifth consecutive round in this file where the previous round's fix shipped a
regression. An audit fix RESETS the verification gate; budget for it.

## 🔴 F1 — negation was clause-scoped but NOT order-scoped, and inverted plain resolutions

`closure_claims()` asked `NEGATOR_RE.search(clause)` — position-independent — and commas are
deliberately not clause boundaries. So any trailing `no` / `nothing` reached BACKWARDS:

| comment (ticket `to do`) | trunk | round 8 | round 8b |
|---|---|---|---|
| "Resolved, no further action needed." | close it | 🔴 **do NOT close** | close it |
| "Recommend closing, no further work planned." | close it | 🔴 **do NOT close** | close it |
| "Confirmed resolved, no repro since Tuesday." | close it | 🔴 **do NOT close** | close it |
| "This is done, nothing else outstanding." | close it | 🔴 **do NOT close** | close it |

**12 of 12** ordinary "work is finished, nothing outstanding" comments regressed. The
ambiguity tier could not rescue them — the whole comment is one clause, so `affirmed` is
empty and the veto is absolute. Round 8 would have traded **4 wrongly-suppressed close-its
for 12 wrongly-created vetoes**, which is a net loss on this tool's own priority.

The wrong premise was written down, in round 8's own code comment: *"widening this one can
only ever downgrade a veto to 'read it' or ADD A VETO. Both directions are the safe one."*
Adding a veto over "Recommend closing, no objections" is not safe — it is an affirmative
wrong instruction. **A "safe direction" argument is only as good as the enumeration of
directions.** Fixed: a negator negates only closure words that FOLLOW it in the clause.

## 🔴 F3 — `()` as a clause boundary was the exact defect the file forbids for commas

`test_a_bare_comma_is_not_a_clause_boundary` exists because splitting on commas *"would
strand `resolved` in a clause of its own and produce a close-it over a refusal"*. Parens did
precisely that, one line away, and README asserted them as boundaries as though justified:

| comment | round 8 | round 8b |
|---|---|---|
| "This is not (yet) resolved." | 🔴 **close it** | do NOT close |
| "This is not (fully) resolved." | 🔴 **close it** | do NOT close |
| "This is anything but resolved." | 🔴 **close it** | do NOT close |

Removing the arm costs nothing: the round-8 paren fixture is already split by its full stop
(pinned by `test_the_parenthesised_aside_is_still_read_for_ambiguity`). `anything but` needed
BOTH a negator entry and a lookbehind in the splitter — neither half works alone.

## 🔴 F5 — the guard on the highest-value flag was watching a DIFFERENT flag

`test_the_close_it_flag_still_fires_on_a_clean_resolution` asserted `"close it" in j` at the
helper's default `transcript_status="likely_addressed"`. The TRANSCRIPT branch emits
`"— close it or re-check"`, whose boilerplate contains that substring. **Measured: replacing
`RESOLVED_COMMENT_RE` with a never-matching pattern — deleting the comment-level close-it
flag outright — left 17 of the 19 tests in the file GREEN.** The two that fired both assert
on the regex object, not on behaviour.

This is the **second** instance of this class inside round 8 alone (the first was
`"do not close" in j.lower()` matching the flag's own boilerplate) and the third round
running in this file. The author had even written the cure — one test passes
`transcript_status="unclear"` specifically to avoid the interference — and did not
generalise it. **Never assert a substring the surrounding sentence can also produce**, and
when you find one instance, sweep the file for the rest.

## The rest

- **F4** — 144 of 672 generated keep-open × closure combinations downgrade to "READ IT".
  Mostly by design; the exception is `the ticket is still open`, which passes the same
  no-second-reading test that promoted `staying open` to STRONG (a PR is not "the ticket").
  Promoted. The generic downgrades stand: "READ IT" is not an instruction to close.
- **F2** — a negator found AFTER the closure word printed manufactured quotations
  (`says "no … resolved"` for "Confirmed resolved, no repro since Tuesday"): words the
  comment contains, in an order it never used. Order-scoped negation makes the elided form an
  honest elision. Two mutants on that quoting guard had survived; one of them emptied the
  span, which makes `if veto_phrase:` falsy and **silently drops the whole veto** — the tier
  decision no longer depends on the phrase being truthy.
- **F8** — `cannot`, `far from` and `anything but` negate without containing a negator word;
  all three drew a close-it over a refusal. Added. Apostrophe-less `isnt`/`wont`/`cant`
  remain unhandled: `\w+nt\b` also matches *important*, *went*, *current*, so the shape rule
  does not extend there. Residual risk, stated rather than half-fixed.
- **F9** — the em-dash clause arm and the unknown-status ambiguity output were both
  load-bearing and pinned by nothing; deleting either left the suite green. Both now pinned.

## Sweep

**26 mutants, all killed.** CONTROL-identity SURVIVED 149/0; CONTROL-positive KILLED. Seven
mutants went **NO-APPLY (stale anchor)** after the F1/F3 rewrites and were scored FAILURES,
then re-anchored to the new code and re-run — a stale anchor reports SURVIVED-looking success
for a mutation that never happened. The battery now covers every region the audit named.

**149 passed, 0 failed** (was 141).

🔴 **The honest reading of "18/18 killed" in round 8 above: it was a true claim about those
18 mutants, and the audit's own 14-mutant sweep on the same code found 5 survivors.** A
mutation sweep measures the mutants you imagined, and round 8's were shaped by the findings
it had just fixed — exactly the correction round 5 already recorded, re-learned.


---

# Round 8c — 2026-08-21 (delta re-audit of the audit fixes, and the scoreboard that ended it)

The round-8b fixes were audited BLIND again. The re-audit re-derived the 17-case table, the
red-at-base split and the suite count exactly — and found that **8b's fix for F1 had shipped
the MIRROR IMAGE of the defect it removed.** Seven consecutive rounds, seven fixes, seven new
defects created by the previous fix.

## 🔴 The mirror: order-scoping fixed one word order and left the other wide open

8b made a negator negate only what FOLLOWS it, which fixed "Resolved, no further action
needed". But in a bug ticket the dominant shape states the SYMPTOM — negated, because that is
what a bug is — and then the RESOLUTION, in one comma-spliced sentence. With commas not a
clause boundary, the symptom's negator reached the resolution:

| comment | trunk | round 8b | round 8c |
|---|---|---|---|
| "Users cannot upload avatars, fixed in #4421 and deployed." | (silent) | 🔴 **do NOT close** | (silent) |
| "The alert wasn't firing, resolved by the rule fix." | close it | 🔴 **do NOT close** | close it |
| "The job did not run on Sunday, resolved — I re-ran it." | close it | 🔴 **do NOT close** | close it |
| "Cannot reproduce this anymore, resolved." | close it | 🔴 **do NOT close** | close it |

10 of 10. Round 8b had measured "12 of 12" in ONE order and shipped; the other order was
never measured. Its own headline lesson — *"a safe-direction argument is only as good as its
enumeration of directions"* — applied to itself and went unnoticed for exactly one round.

## What actually ended it: a labelled corpus, not a better argument

Eight rounds were argued one clever sentence at a time. Instead, every case from the round-8
table, both audits and the live ticket was labelled with the verdict a human reading it would
give, and every candidate was scored against the same set:

| implementation | score |
|---|---|
| `origin/trunk` | **24 / 42** |
| round 8b (commas not a boundary, order-scoped) | **28 / 42** |
| commas ARE a boundary | 36 / 42 |
| + drop `ticket\|task still open` from STRONG | 38 / 42 |
| + carry a clause-TRAILING negator (shipped) | **39 / 42** |

That table is now `scripts/check-clickup-addressed/tests/test_corpus.py`, with the score
pinned, the three known failures held as a LEDGER so the set cannot silently grow, and a guard
that no `VETO`-labelled case may ever produce a close-it. Its own controls were run: reverting
commas-as-boundary drops it to 30/42 and it says so by name.

## The other findings

- **Commas ARE boundaries** — the round-8 rationale for excluding them ("This is not, in my
  view, resolved must keep its negator") was true about one sentence and false about ticket
  prose. That sentence is handled instead by a narrow **carry** rule: a clause that ENDS on
  its negator is an interrupted negation and carries into the next clause. A clause ending in
  anything else ("Users cannot upload avatars,") is a finished thought and carries nothing.
- **`(?:ticket|task) (?:is |remains? )?still open` removed from STRONG.** Added one round
  earlier on the argument that "only THIS object is *the ticket*" — false in ClickUp, where
  comments reference siblings constantly ("the duplicate task is still open, closing this
  one"). It hard-vetoed 4 of 4 such comments where the weak tier correctly said READ IT. This
  is the round-8 motivating case with the noun changed, re-introduced by its own fix.
- **Two guards no test distinguished**, both found by mutation: the positional filter (making
  it position-independent left the suite green) and clearing `carry` on a consuming clause.
  Both are now pinned. The positional filter is kept for a narrower reason than it was
  written for — it never fabricates a reversed quotation — and the verdict it produces on
  "Resolved and not verified in prod." is **wrong**, which is recorded rather than hidden.

## Sweep and suite

**30 mutants, all killed.** CONTROL-identity SURVIVED 156/0; CONTROL-positive KILLED. Stale
anchors after each rewrite were scored FAILURES and re-anchored — three separate times across
8b and 8c. **156 passed, 0 failed** (122 at the start of round 8).

## 🔴 The lesson worth keeping

Every round here produced a rule that was locally right and globally untested, and the
review that caught it was always someone else's. The rules were not the problem — the
**absence of a scoreboard** was. Score the corpus before and after; a candidate that reads
worse but scores higher wins.


---

# Round 8d — 2026-08-21 (third blind audit: the corpus itself was audited)

The third audit was pointed primarily at the **42 labels**, on the reasoning that the corpus
had just become the authority for this subsystem — future rounds score against it — so a wrong
LABEL is more dangerous than a wrong line of code: it steers every later round toward a wrong
answer and nobody re-derives it. The author of the labels was also the author of the code.

That was the right place to look.

## 🔴 The corpus was structurally blind to the class that produces the WORST outcome

Measured mechanically: across all 42 cases there were **18 negator→closure pairs and every
one put the negator FIRST**. So a negator that FOLLOWS the closure word it denies appeared
nowhere — and `test_no_new_case_fails_in_the_close_it_direction`, the file's declared 🔴
guard, **could not fail on that class however bad it got**:

| comment | trunk | shipped |
|---|---|---|
| "The ticket says resolved but it isn't." | close it | close it |
| "This was marked resolved, but it is not." | close it | close it |
| "Claimed resolved in standup, but it never was." | close it | close it |

Not a regression — trunk is equally wrong — but it is the tool's worst outcome, and the
corpus omitted its own worst class. **A guard that cannot fail on a shape nobody wrote down
reads as coverage while providing none.** The class is now in `KNOWN_FAILURES`, counted
rather than described, and the close-it guard exempts exactly those recorded cases and no
others.

🔴 **And a docstring asserted coverage that did not exist.** It said *"the corpus records the
failing case rather than hiding it"* — `grep -c` returned **0** — and justified the design
with *"its failure mode needs the writer to use 'and', where a comma or full stop is far more
common"*, which is false on all six separators measured (`and` / `,` / `.` / `but` / `;` /
`—`, every one drawing "close it"). Both corrected.

## 🔴 `MIN_SCORE` was self-fulfilling: a mutant survives the whole suite and flips five refusals

The audit's sharpest finding. Restricting a negator to closure words within 30 characters —
touching no pinned regex — left **156 passed, 0 failed and the corpus still at 39/42**, while
turning five plain refusals into "close it":

    "This is not something I would describe as anywhere near resolved."   -> close it
    "There is no evidence at all in the logs that this is resolved."      -> close it

Because the largest negator→closure distance anywhere in the corpus was **27 characters**,
the scoreboard actively *rewarded* shrinking the window. At a 19-character window the corpus
test **passes at 41/42** and the ledger's own failure message instructs the next author to
accept it and raise the bar behind it. A scoreboard is only as good as the spread of its
cases; a corpus whose cases cluster on one axis silently rewards over-fitting that axis.

## 🔴 The `carry` rule — written by round 8c — was a new regression, and is DELETED

`carry` propagated a clause-trailing negator into the next clause. "Ends on a negator" does
not distinguish an interrupted negation from how engineers write a clean status:

| comment | trunk | with carry | now |
|---|---|---|---|
| "Downtime: none. Resolved and deployed." | close it | 🔴 **do NOT close** | close it |
| "Impact: none, resolved by the rollback." | close it | 🔴 **do NOT close** | close it |
| "Regressions found: none. Resolved." | close it | 🔴 **do NOT close** | close it |

It also reintroduced the manufactured quotation `negated_phrase` exists to prevent —
`says "none … Resolved"`, two words from different sentences — and was cleared only by a
clause containing a closure word, so it survived arbitrarily many intervening clauses. It
bought **one** corpus case and cost **seven** realistic close-its.

Deleted. The case it was written for is now a stated KNOWN FAILURE. **Say what is not handled
rather than half-handling it** — that is the second rule this round deleted rather than
extended, and both deletions scored better than the rules they replaced.

## Still open: six labels the audit disputes, and it is a PRODUCT decision, not a bug

The audit disputes 11 labels, **11 of 11 in the direction that flatters the implementation** —
one-directional, which is the corruption signature. The substantive six are every `READ` case,
which it argues should be `CLOSE`:

> "Fix merged. The duplicate task is still open, closing this one." — labelled READ. The
> comment literally says *closing this one*.

The argument is that the reporter explicitly **disclaims** the aside ("but unrelated",
"tracked separately", "closing this one"), so a human wants "close it", and the corpus has
encoded the implementation's inability to discount a disclaimed aside as though it were the
preferred behaviour. If that is right, the ambiguity tier is much narrower than built.

**Not resolved here, because it changes what the tool should DO, not whether it does it
correctly.** But it was MEASURED, which settles the only question that blocked shipping —
scored over all 49 cases, with the disputed labels flipped to CLOSE as the audit argues:

| implementation | these labels | the audit's labels |
|---|---|---|
| `origin/trunk` | 27/49 | 24/49 |
| **shipped (ambiguity tier)** | **41/49** | 34/49 |
| variant: disclaimed aside -> close it | 37/49 | **38/49** |

🔴 **The shipped code beats trunk under BOTH labellings** (41 vs 27, and 34 vs 24), so the
dispute decides which of two improvements is better, not whether to ship one. That is why it
did not block the merge, and why it should not be settled in a hurry.

Two further measurements worth having before anyone re-opens it: the variant produces **zero
collateral changes** — it moves only disputed cases, nothing else — and it still does NOT
satisfy the audit on 2 of the 6, including its sharpest example. "Fix merged. The duplicate
task is still open, closing this one." stays READ even with the tier removed, because
`RESOLVED_COMMENT_RE` does not match a bare `closing`. So the audit's preferred behaviour is
NOT reachable by deleting the tier; it needs the close-it trigger widened too, which is the
one direction this file has repeatedly established as unsafe. Anyone taking this on should
start there, not at the tier.

Recorded for the human decision. The other five disputes are `not-VETO` labels
that should be `CLOSE`; `not-VETO` passes on CLOSE, READ *or silence*, so it converts a miss
into a pass — genuinely load-bearing in only 1 of its 6 cases.

## Suite and corpus

Corpus **49 cases** (42 + 4 negator-after + 3 carry controls), **41/49**, MIN_SCORE 41,
8 recorded known failures. On the original 42, the score moves 39 → **38**: the one point the
deleted carry rule was buying. **155 passed, 0 failed.**

## 🔴 The lesson

Round 8c concluded "a scoreboard ended it". One round later the scoreboard itself was the
thing most in need of auditing — blind to its own worst class, and shaped so that over-fitting
one axis scored *higher*. **A corpus is an instrument, and an instrument gets validated like
any other: ask which shapes it structurally cannot see, and who wrote the labels.**
