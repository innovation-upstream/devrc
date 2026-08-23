---
name: check-clickup-addressed
description: Verify if ClickUp tasks were fully addressed by searching session transcripts for completion signals. Use when checking if recent work on assigned tasks is complete, or when asked to verify task completion status.
---

# /check-clickup-addressed — verify if ClickUp tasks were fully addressed

Deterministic check: given the N most recent comments not from you on your assigned
tasks, find the sessions that worked on those tasks, read the transcripts, and report
what's done vs what's still open.

## Quick start

The scripts live in **devrc**, not in this deployed skill dir — `~/.claude/skills/` is a
read-only nix-store copy of `claude/skills/`, and only the docs ship there. Run them from
the repo:

```bash
CCUA=~/workspace/devrc/scripts/check-clickup-addressed

# Default: top 3 most recent comments (~30s)
python3 "$CCUA/check-addressed.py"

# Fast mode: samples only the 10 most recently updated tasks (~10s)
python3 "$CCUA/check-addressed.py" --fast

# Top 5, JSON output
python3 "$CCUA/check-addressed.py" --limit 5 --json

# Only sessions since a date
python3 "$CCUA/check-addressed.py" --since 2026-08-15

# Verbose; skip the gh PR lookups
python3 "$CCUA/check-addressed.py" --verbose --no-resolve-prs
```

🔴 **Editing this file needs a `home-manager switch`; editing the scripts does not.**
`claude/skills/check-clickup-addressed/SKILL.md` + `reference/` deploy through nix, so a
prose change is invisible to a session until the switch. `scripts/check-clickup-addressed/`
is run straight out of the working tree — the edit is live immediately, and it is gated by
`scripts/run-tests.sh` (target `scripts/check-clickup-addressed/tests`).

## What it does (deterministic pipeline)

1. **`recent-comments.py`** — queries ClickUp for all comments on your assigned tasks,
   filters out your own, sorts by date, returns the top N with task ID/name, ticket
   status/priority, author, snippet + full `text`, and `my_latest_reply` — the date of *your*
   newest comment on that task, computed **before** your comments are dropped, because the
   consumer otherwise cannot tell an answered ticket from an abandoned one. That last key
   is **omitted entirely** when the user id could not be resolved — absent means "the
   check could not run", which is not the same fact as a present `null` ("I looked; you
   never replied"). Both consumers branch on which.
2. **`search-sessions.py`** — searches `~/.claude/projects/**/*.jsonl` transcripts for
   sessions mentioning a task ID, ranks by hit count. Terms are **ANDed**, so it is run
   **once per task**, never with several tasks' terms in one query.
3. **`check-completion.py`** — for each task, reads the matched sessions' assistant text
   and pattern-matches for completion signals (merged/shipped/verified) vs open signals
   (still open/not yet done/needs action), **within ±2000 chars of a mention of that
   task's ID, and only where that task is the nearest ID to the match**.
4. **`check-addressed.py`** — orchestrates the above, produces a unified report.

Both search and completion skip the **current session** (`$CLAUDE_CODE_SESSION_ID`),
which necessarily mentions every task ID under test. Override with `--exclude-session ID`
(repeatable) or, on `check-completion.py`, `--include-self`.

🔴 **They also skip every PRIOR RUN of this checker** — recognised structurally by
`scripts/check-clickup-addressed/_selfrun.py`, not by session id. A previous
report prints each task ID directly beside `likely_addressed` / `✓ resolved` / `merged`,
which is precisely the shape the proximity scorer rewards, so without this the tool reads
yesterday's verdict back as today's evidence and re-confirms it forever. The report prints
`(ignored N transcript(s) …)` when the guard fires; `--include-self-runs` disables it.

## Individual scripts

All scripts accept `--json` for machine-readable output. Two of them make network calls:
`recent-comments.py` needs ClickUp credentials, and PR resolution shells out to `gh pr
view` — **on by default in `check-addressed.py`** (`--no-resolve-prs` opts out), off by
default in `check-completion.py` (`--resolve-prs` opts in). Only `search-sessions.py` is
unconditionally hermetic. The PR cache is per-process and the orchestrator forks one
process per task, so lookups dedupe within a task, not across the run.

| Script | Input | Output |
|--------|-------|--------|
| `recent-comments.py --limit N` | ClickUp API | `--json`: task_id, task_name, task_status, task_priority, date, author, snippet, text, and **`my_latest_reply` only when the user id resolved** — its ABSENCE is meaningful (the check could not run), so read with `in`, never `.get()`. The tabular printer emits a different, smaller set — task_id, task_name, date, author, snippet — which `check-completion.py` parses for field 0 only. |
| `search-sessions.py term1 [term2 ...]` | `~/.claude/projects/` | session_id, date, project, hits |
| `check-completion.py --task ID` | session transcripts | status, completion signals, open items |
| `check-addressed.py --limit N` | all of the above | unified report |

## Interpreting results

| Status | Meaning |
|--------|---------|
| `likely_addressed` | Completion signals found, no open signals |
| `partially_addressed` | Both completion and open signals — something shipped, something remains |
| `open` | Only open signals found — work not completed |
| `unclear` | Sessions mention the task, but no signal of either kind is attributable to it |
| `no_sessions_found` | No session transcript mentions this task at all |
| `no_mentions_found` | Sessions were read, but the task ID appears in none of them — **no evidence either way**, never an implied "partly done" |

🔴 **`partially_addressed` is the status to be most suspicious of** — it was, until
2026-08-19, the state an unmatched task fell into *by construction* (see Limitations).
Check `mentions_found > 0` before believing any verdict, and read the snippets: a signal
is attributed by lexical proximity, so it can be a real sentence about the right task
that still says nothing about whether the ticket is done.

## Completion signals detected

- PR merged/landed/shipped (references like `#1234 merged`)
- Verified on main/trunk
- Fix shipped/deployed
- Resolution markers

## Open signals detected

- Still open/pending/waiting
- Left open (deliberately)
- Not yet done/fixed/deployed
- Premise refuted (scope changed)
- Needs action/decision
- Deployed ≠ verified

## Limitations

🔴 **This tool reports which words appeared near a task ID. It does not know whether the
work is done.** Treat every verdict as a pointer to a transcript to read, never as an
answer. Three structural false-positive generators were fixed on 2026-08-19, a fourth —
the checker reading its own previous runs — on 2026-08-20, and on 2026-08-21 two **silent
no-ops**: an unrecognised ClickUp status disabling every ticket/comment cross-check, and an
inert proximity tier whose confidence marker printed the same symbol for every signal.
Round 5, the same day, fixed a **false explanation** (a named-but-unknown repo reported as
"repo not named"), the repaired marker never reaching the report, and the missing
"nobody is on it and someone is waiting" flag (matrix in
`claude/skills/check-clickup-addressed/reference/validation-history.md`). Round 6, on 2026-08-22, fixed
that new flag's own blind spot — it could not see **your** replies, so a ticket you had
already answered was reported unanswered forever. What remains is listed under "Still weak".

🔴 **Every round so far has been found by RUNNING the tool and checking its verdict against
reality, never by reading the code.** Round 6's defect shipped *with* a fresh adversarial
test suite that had five controls on the very function that was wrong — all five green, all
five scoped to one side of a seam. If you are about to trust a verdict here, open the ticket.

- Session search is keyword-based, not semantic — a session that addressed the task
  without ever writing its ID is invisible to this tool. **A `no_mentions_found` or
  `unclear` verdict is not evidence that nothing was done.**
- Completion detection is pattern-based — it catches common phrases ("merged", "shipped",
  "verified on main") but may miss unusual phrasings.
- The pipeline runs sequentially (ClickUp API → transcript search → pattern match), so
  it's bounded by API latency for the comment fetch (~30s for 50 tasks).

### Still weak (measured 2026-08-19, not fixed)

- **A signal is attributed by lexical proximity, not by meaning.** Nearest-ID keeps a
  neighbouring *task's* verdict out, but not a neighbouring *clause's*: ``#1065 … merged
  08-16 (I confirmed). `868gx0aaa` is u[nfixed]`` still scores as completion for
  `868gx0aaa`, because the merge sentence really is adjacent to it. **Read the snippet.**
- **A PR reference the tool cannot pin to a repo is reported `unresolved`, not guessed** —
  a snippet-wide scan was tried and attributed every bare `#N` to `civitai/civitai`,
  returning a real but unrelated PR ("merged 2024-03-18"). 🔴 **Three distinct ways to fail,
  and the message now says which** (until 2026-08-21 all three printed `repo not named`,
  true of only one): `repo not named` — nothing repo-ish within `REPO_LOOKBEHIND`=30 chars;
  `repo 'X' is not in KNOWN_REPOS` — a repo **was** named, *go add it*; `ambiguous — A, B
  both in range`. The word on the `#` is reported as written and **not classified** —
  nothing short of enumerating the world separates `devrc` from `landed`, and a reader
  separates them at a glance. `KNOWN_REPOS` is a closed vocabulary, so **widening it is not
  the fix** (same shape as the status sets below); verify owners with `gh repo view` —
  `devrc` is **innovation-upstream**/devrc, and `civitai/devrc` does not exist.
- **`--fast` only inspects the 10 most recently updated tasks**, so a stale-but-important
  task is invisible to it. That is a sampling window, not a full check.

### What the report now cross-checks for you

Three independent sources, and the **Needs a decision** block fires on their disagreements —
plus one state where nothing disagrees and that is exactly the problem (4):

1. **ClickUp status + priority**, printed beside every verdict.
2. **The ticket's newest comment** — usually the real record of what happened. A comment
   reading "Resolved / recommend closing" over a ticket still at `to do` is flagged; that
   exact case (`868gx0aaa`, `to do`/urgent) is why the check exists. 🔴 **An explicit
   refusal to close VETOES that flag**: on 2026-08-20 an alert-cycling clause (a service
   whose queue-depth alert *fired and **resolved** repeatedly*) tripped the keyword on `868gx0bbb`,
   whose opening clause refused closure outright — the report told the operator to close it. The
   reporter's own words outrank both the keyword scan and the transcripts.
   🔴 **The veto has TWO TIERS** — an absolute one was wrong in *both* directions (round 8;
   17-case before/after table in the validation doc). **STRONG** ("do not close", "still
   live", "staying/remains open", "reopening") is about *this* ticket and is absolute. **WEAK**
   ("still open", plus any *negated* closure claim) is as often about a PR, an alert or a
   sibling ticket: it vetoes when it is the comment's only word on closure, and drops to
   **"READ IT and decide"** when another clause carries an un-negated closure claim. Untiered,
   *"Resolved… recommend closing. (The follow-up PR is still open but unrelated.)"* emitted
   **do NOT close**.
   🔴 **Negation is decided ONCE, scoped to the CLAUSE *and to word order*** (`closure_claims`)
   — never per-word lookbehinds, which guard only the spellings they enumerate and lost this
   twice on the abandoned branch. Untreated, `isn't`/`never`/`won't`/`not fully`/`unresolved`/
   `cannot`/`far from` and *"I do not recommend closing"* all drew an affirmative **close it**
   over a comment refusing exactly that. Contractions match by **shape** (`\w+n't`): any stem
   list is already missing `won't` the day it is written.
   🔴 **Commas ARE clause boundaries; parens are not; a negator negates only what FOLLOWS it.**
   Each of those was wrong at least once, each wrong version shipped an affirmative wrong
   instruction, and two blind audits found them. A trailing "no" reaching backwards turned
   *"Resolved, no further action needed"* into **do NOT close** (12 of 12); a symptom's negator
   reaching forward across a comma turned *"The alert wasn't firing, resolved by the rule fix"*
   into **do NOT close** (10 of 10); parens stranding a negator turned *"This is not (yet)
   resolved"* into **close it**.
   🔴 **DO NOT ARGUE THE NEXT CHANGE FROM AN EXAMPLE — SCORE IT.**
   `scripts/check-clickup-addressed/tests/test_corpus.py` holds 49 labelled comments,
   every one from a measurement, and 8 recorded KNOWN FAILURES. Trunk scores 24/42 on the
   original set, what shipped 38/42. Add your motivating case with the verdict a human would
   give, *then* change code.
   🔴 **AND AUDIT THE CORPUS ITSELF — it was blind to its own worst class.** All 18
   negator/closure pairs in the first 42 put the negator FIRST, so a negator that FOLLOWS the
   word it denies (*"The ticket says resolved but it isn't"* -> **close it**, on trunk and
   now) appeared nowhere and the close-it guard could not fail on it. A mutant narrowing the
   negation window to 30 chars kept the suite green AND the score unchanged while flipping
   five refusals to "close it", because the corpus's cases all clustered under 27 chars.
   **Ask which shapes your instrument structurally cannot see, and who wrote the labels.**
3. **Cited PRs, resolved against GitHub** (`gh pr view`) rather than believed on sight. A
   completion signal quoting a PR that is actually still **open** is flagged — on
   2026-08-19 that shape was real (talos-infra #1073). Disable with `--no-resolve-prs`.
4. **Nobody is on it and someone is waiting** — a not-done ticket + **zero** transcript
   evidence (`mentions_found == 0`) + a comment from someone else within
   `UNANSWERED_COMMENT_DAYS` (14). No rule produced this before 2026-08-21 because nothing
   *disagrees*: ticket open, transcripts empty, and they agree. Measured live on
   `868gz0hhh` (`to do`/high, colleague comment, 0 mentions) the block said nothing.
   🔴 Bounded by **recency, not priority** — priority is a stale property of the *ticket*
   and would re-fire on every unstarted backlog item forever, training the reader to skip
   the block; recency is a property of the *interaction*, says a human is waiting **now**,
   and self-clears. Fires whatever the status word reads (safe direction, like the veto).
   🔴 **It could not see YOUR OWN replies until 2026-08-22, so an answered ticket stayed
   flagged forever** — the unbounded noise the recency bound exists to prevent. Measured on
   `868gz0hhh`: "Commented 2d ago; nobody has answered" over a ticket **two** sessions had
   already answered, the later of them 11 h earlier, and acting on it duplicated an analysis
   already in the thread. A **seam** defect, each side correct alone — `recent-comments.py`
   drops your comments (right: the report is about what *others* said), and the flag concludes
   nobody answered (right, given the only evidence it is handed). Neither can see the other's
   assumption, so the fix crosses the seam: the producer now emits `my_latest_reply` per task
   and the flag suppresses when your newest reply is **at or after** the comment. Compared,
   not counted — a reply *predating* the question does not answer it. An **absent**
   `my_latest_reply` (stale producer, or a failed user-id lookup) fires but says the check
   never ran, rather than silently deciding either way.
   🔴 **The seam is crossed for TOP-LEVEL comments only.** `recent-comments.py` calls the CLI
   without `--threads`, and `/task/{id}/comment` returns top-level comments only — replies
   live behind `getThreadedComments`. So an answer you wrote *inside a comment thread* is
   still invisible and the ticket still reads as unanswered. Narrower than D12, same shape.
   (Verified 2026-08-22 that the two answers on `868gz0hhh` are top-level, so the motivating
   case really is fixed — but do not read that as the general case.)
   🔴 **New false-SILENCE direction, deliberately unbounded by transcripts:** reply *"I'll
   look next week"* and the flag is silenced for that comment even though `mentions_found ==
   0` means no other rule covers the ticket either — which is the gap this flag was added to
   fill. Bounded only by a *newer* colleague comment re-firing it. Acknowledging is not doing.

🔴 **Sources 1 and 2 are gated on a hardcoded nine-word status vocabulary, and ClickUp
statuses are per-list and arbitrary.** Until 2026-08-21 a miss was **silent**: measured,
`in review` / `blocked` / `needs qa` / `review` each disabled every ticket/comment check
including the keep-open veto, and printed nothing — an empty **Needs a decision** block
reads as *checked, nothing disagrees* when it means *not checked*. An unrecognised status
now announces itself there and names the two sets to add it to. **Widening the sets is not
the fix on its own** — it moves the silence to the next unknown word; keep the
announcement.

## Improvements over naive approach

- **Windowed search**: only looks for signals within ±N characters of each task ID
  mention, not across the entire session transcript
- **Adjacency weighting (`[●]` / `[○]`)**: a signal whose own ±40-char snippet contains the
  task ID (`[●]`) outranks one merely somewhere in the ±2000-char window (`[○]`). **`[○]` is
  the one to distrust** — "same window" can mean a different paragraph about different work.
  In the report it is a **second, bracketed column**: first glyph = `✓` completion / `○`
  open (WHAT the signal says), bracketed = adjacency (HOW MUCH to trust it). Two orthogonal
  facts, deliberately not merged — `○` already meant "open item" here. 🔴 **Inert until
  2026-08-21, then invisible for a day**: first a `proximity > 0.5` tier ranked signals by a
  `distance` the only production producer hardcodes to `0`, so everything scored ≥ 1.0 and
  the marker printed `●` for every signal (tier deleted; verdicts unchanged on all six live
  tasks); then the repaired marker was wired only into `check-completion.py`'s printer, not
  into `check-addressed.py` — the entry point everyone runs — so it still reached no report.
  **A marker nobody sees is the same no-op as one that cannot vary.** Live 2026-08-21:
  `868gy0ddd` shows both values, `868gy0eee` is all `[○]`.
- **Deduplication**: same signal from multiple windows is only reported once
- **Cross-task attribution guard**: a signal is kept only if the task under test is the
  ClickUp ID lexically *nearest* to it. Without this, a triage table listing several
  tasks reports every task's verdict as every other task's.
- **Exact task-ID matching only**: a 6-character-prefix pass was removed on 2026-08-19 —
  it matched neighbouring tasks sharing a prefix and never fired usefully, since ClickUp
  always supplies the full ID.
- **Self-exclusion**: the session running the check is skipped, so the tool cannot read
  its own output back as evidence.
- **Prior-run exclusion** (2026-08-20): every transcript that is a *run* of this checker is
  skipped too, recognised by anchored markers rather than session id. The markers had to be
  anchored. **Measured 2026-08-21 over the 735 transcripts these scripts actually walk**
  (`CLAUDE_DIR.iterdir()` then `glob("*.jsonl")`, top level — *not* a recursive grep, which
  also sees 4559 files under `<session>/subagents/` that are never opened): a bare
  `check-clickup-addressed` matches **67 (9.1%)** because the skill catalog is injected into
  every session, and a bare `/check-clickup-addressed` matches **24 (3.3%)** because it is a
  substring of the skill's own path — against **4** for the real anchored markers. An earlier
  "213 of ~250 / 32" came from the recursive walk and was wrong on both numerator and
  denominator; the verdict survived re-measurement, the figures did not.
  🔴 **These drift — 746/62/23 one day, 735/67/24 the next. Re-measure before quoting.**
  Failure direction is deliberate: a false positive drops a session and degrades a verdict
  to `unclear`; a false negative brings back a confident ✅ over a live ticket.
- 🔴 **Self-classification creep, and it now has a REAL victim.** The markers appear in this
  SKILL.md, so any session that loads the skill, reads this file, or reviews its diff becomes
  a permanent self-run — and a session that both *did work* and *ran the checker* is dropped
  from tomorrow's evidence. Measured 2026-08-21: **5 task IDs are visible only inside
  dropped transcripts, and one of them is a LIVE ticket**, not a test fixture — its only
  mentions sit in a work session that also ran the checker. An earlier note claiming "real
  loss is currently zero" was true when written and is no longer. The drop lands in the safe
  direction (→ `no_mentions_found`, i.e. *no evidence*, never a false ✅) but it lands on the
  session most likely to contain the work. Nothing ages a marker out; the set only grows.
  `--include-self-runs` is the escape hatch when you suspect this.

## Performance

| Mode | Time | Notes |
|------|------|-------|
| `--fast` | ~10s | Samples only the 10 most recently updated tasks |
| `--limit 5` | **~85–100s** | All assigned tasks; one transcript search per task |

Re-measured 2026-08-21: **98.5s** and **85.4s** wall (two runs). The `~41s` quoted until
then was a 2026-08-19 figure that had silently doubled as the transcript corpus grew — the
ClickUp fetch dominates, but the per-task sweep scales with `~/.claude/projects`, so expect
further drift. **Re-time it rather than quoting it.**

## Tests

Run all tests (**180** collected, measured 2026-08-22). `run_all.py` exits non-zero on
failure — but read the `Total: N passed, M failed` line, not a piped exit code. 🔴 **If that
line is missing at all, the run died — treat it as a failure, never as "no output".** A
`sys.exit()` from code under test is a `SystemExit`, which a bare `except Exception` does
not catch; it used to escape the runner and kill every remaining test file silently (found
by the round-4 mutation sweep, fixed in `run_all.py`).

```bash
CCUA=~/workspace/devrc/scripts/check-clickup-addressed
PYTHONDONTWRITEBYTECODE=1 python3 "$CCUA/tests/run_all.py"
```

The same files are a **pytest** target of devrc's gate — `scripts/check-clickup-addressed/tests`
in `HERMETIC_TARGETS`, with its collected-count floor in `TARGET_FLOORS`
(`scripts/run-tests.sh`). Both runners see the same 180; `run_all.py` survives because it
purges `__pycache__` and reports an import failure as a FAILURE, which pytest's summary
line does not distinguish as loudly. **Raise the floor when you add tests.**

Test files:
- `test_attribution.py` — the 2026-08-19 regression set (cross-task attribution, the
  `no_mentions_found` contract, self-exclusion, signal precision, PR-ref resolution,
  ticket/comment disagreements) plus the 2026-08-20 set (D4 prior-run exclusion, the
  keep-open veto). Read `claude/skills/check-clickup-addressed/reference/validation-history.md`
  before changing any of it — several tests there are false-positive controls that pass at
  base by construction and are labelled as such.
- `test_status_and_tiers.py` — the 2026-08-21 round-4 set: the unknown-ClickUp-status blind
  spot, the deleted proximity tier and the invariant guards that license deleting it, and
  the entry point's flag parsing/forwarding.
- `test_repo_vocab_and_waiting.py` — the round-5 set: the three distinct repo-resolution
  failures (+ the `KNOWN_REPOS` owner ledger), the adjacency marker reaching the actual
  report, and the waiting-on-a-human flag with its widening controls. 🔴
  `test_an_unknown_clickup_status_does_not_disable_the_waiting_flag` asserts the **WAITING**
  flag specifically: a bare `assert flags` was green for round 4's announcement instead, and
  let a mutant rebuilding the D6 blind spot SURVIVE a fully green suite.
- `test_own_reply_answers.py` — the round-6 set (D12): the waiting flag reading your own
  replies. 🔴 `test_collect_computes_the_reply_over_the_UNFILTERED_comment_list` exists
  **because a mutation sweep found the wiring uncovered** — `latest_reply_by` and
  `build_record` were each pinned in isolation while `_collect`, the only thing that joins
  them, was not, so `latest_reply_by([], my_id)` made the whole fix inert against a fully
  green suite. Pin the SEAM, not only the parts.
- `test_check_completion.py` — windowed signal extraction. ⚠️ Its two proximity tests hand
  `extract_signals_from_windows` a non-zero `distance`, which no production producer emits;
  they cover the formula, not any reachable path. The reachable premise is pinned in
  `test_status_and_tiers.py` instead.
- `test_search_sessions.py` — session search, ranking, date filtering
- `test_recent_comments.py` — ClickUp API parsing, filtering, sorting
- `test_no_real_identifiers.py` — 🔴 **this repo is PUBLIC.** Two pinned LEDGERS over both
  halves of the skill: every task-ID-shaped token, and every fixture comment author. An
  unregistered value is red by default, because a synthetic ID and a real one are the same
  nine characters and nothing can tell them apart by looking. Regenerate the fixture
  SYNTHETIC — preserving the shape the case was chosen for — then write it down. It reads
  the working tree only, like devrc's four sibling content gates: **it says nothing about
  what is already in git history.**
