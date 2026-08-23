---
name: check-clickup-addressed
description: Verify if ClickUp tasks were fully addressed by searching session transcripts for completion signals. Use when checking if recent work on assigned tasks is complete, or when asked to verify task completion status.
---

# /check-clickup-addressed — verify if ClickUp tasks were fully addressed

Deterministic check: given the N most recent comments not from you on your assigned
tasks, find the sessions that worked on those tasks, read the transcripts, and report
what's done vs what's still open.

## Run the cheap pass first — `clickup`'s `awaiting`

🔴 **If the question is *which* tasks are waiting on you, this is the wrong tool.** The
`clickup` skill answers that on its own, with no transcript reads:

```bash
node query.mjs awaiting        # from the clickup skill dir; --max bounds the fan-out
```

`awaiting` decides on ONE predicate — the newest comment on the task was not authored by
the token owner — and sweeps the whole assigned queue in ~45s at one API request per
task. It cannot tell you whether anything got *done*; that is the question this skill
exists for, and answering it costs a transcript read per task.

So: `awaiting` to get the list, this skill on the few entries that matter. The two are
complementary, not alternatives, and the split is by QUESTION (which vs whether), not by
speed. Running both does duplicate one ClickUp fan-out — step 1 below makes its own pass —
which is cheap for a handful of tasks and wasteful across the whole queue.

## Quick start

The scripts live in **devrc**, not in this deployed skill dir — `~/.claude/skills/` is a
read-only nix-store copy of `claude/skills/`, and only the docs ship there. Run them from
the repo:

🔴 **Transcript scanning is OPT-IN since 2026-08-22 (`--transcripts`).** Measured upstream
over the three tickets in that day's report, the four evidence sources scored: ClickUp
status **3/3** useful; newest comment **3/3 and decisive in every case**; transcript scan
**0/3**, one of them actively misleading (a `✓ PR merged` whose own snippet said the bug was
still unfixed); cited-PR resolution **0/3 resolved, 2/3 with FALSE explanations**. The scan
is also ~60s of the ~90s runtime and the origin of every false verdict this tool has
shipped. The ClickUp-side default runs in **~21s** and produced only correct output on the
same input.

```bash
CCUA=~/workspace/devrc/scripts/check-clickup-addressed

# Default: top 3 comments, ClickUp status + newest comment (~21s). NOT the FOUR
# transcript-dependent flags — the run names every one of them and says it skipped
# them. `--since`/`--include-self-runs`/`--no-resolve-prs`/`--verbose` are inert
# here and warn on stderr rather than being silently ignored.
python3 "$CCUA/check-addressed.py"

# Add the transcript scan and completion verdicts (~90s; read every snippet)
python3 "$CCUA/check-addressed.py" --transcripts

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
   is **omitted entirely** when the user id could not be resolved, or when a date on one of
   *your* comments would not parse — absent means "the check could not run", which is not the
   same fact as a present `null` ("I looked; you never replied"). Both consumers branch on
   which. Beside the two display dates it also emits the raw epoch-ms they were formatted
   from (`date_ms`, `my_latest_reply_ms`), because `format_date` throws the seconds away and
   the "have I answered?" comparison then runs at minute resolution. Each is emitted **only
   when it parses** — absent, never `0`, and `my_latest_reply_ms` is nested under
   `my_latest_reply` so a precise instant can never outlive the reply it refines.
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
| `recent-comments.py --limit N` | ClickUp API | `--json`: task_id, task_name, task_status, task_priority, date, author, snippet, text, and **`my_latest_reply` only when the user id resolved AND every comment of yours could be ranked** — its ABSENCE is meaningful (the check could not run), so read with `in`, never `.get()`. Plus `date_ms` / `my_latest_reply_ms`, the raw epoch-ms behind the two display dates, each present only when it parsed. The tabular printer emits a different, smaller set — task_id, task_name, date, author, snippet — which `check-completion.py` parses for field 0 only. |
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
| `not_scanned` | **The DEFAULT since 2026-08-22.** No transcript was read at all, so no verdict was formed. Distinct from every row above: those are *searched* outcomes, this is the absence of a search. The record also **omits `mentions_found` and `sessions_searched`** rather than reporting `0` — an unsearched zero is not a searched one. `--transcripts` is what produces the other rows |

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

🔴 **A fix for the self-run guard was TRIED and REVERTED (2026-08-22) — recorded so nobody
re-derives it.** The guard drops any transcript containing this skill's markers, which
measured as **67% / 80% / 100%** of matching transcripts on three live tickets. That looks
like catastrophic evidence loss, so the guard was rewritten to redact the checker's *output
messages* and keep the session. It **recovered** the transcripts (3→8, 2→9, 0→8) **and made
every verdict worse**: all three tickets went to `partially_addressed` on completion signals
mined from sessions that were *working on this checker*. One such snippet was literally a
test-corpus example — a sentence of the form *"the fix landed in #NNNN and is live"* — scored
as completion evidence for an unrelated infrastructure ticket. Every recovered session for
one of the three opened with either `/check-clickup-addressed` or *"read and evaluate the
check-clickup-addressed skill"*. **The crude guard was reaching the right outcome by the
wrong mechanism, and the ~81% it discards is overwhelmingly noise.** The real lesson is the
one that made the scan opt-in: **lexical proximity cannot separate a session that WORKED the
ticket from a session that TALKED ABOUT it**, and no amount of guard-tuning changes that.
*(Ticket ids and the client's stack are omitted here — this repo is public. The measurement
is what carries; the identifiers were the client's.)*

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
  returning a real but unrelated PR ("merged 2024-03-18"). 🔴 **Round 5 split the failure
  into three precise messages; round 7 (2026-08-22) COLLAPSED two of them back, on
  measurement.** `repo 'X' is not in KNOWN_REPOS` asserts that a repo was named and is
  spelled `X` — and on a live upstream run **2 of the 3 cited PRs** rendered as `repo
  'their' …` and `repo 'which' …`, the lookbehind having captured the preceding *English
  word* while the comment plainly named a repo. The precise message was wrong more often
  than the vague one it replaced, and it sends the reader to add an English word to a repo
  table. `word` is simply whatever token precedes the `#`, and nothing short of enumerating
  the world separates `devrc` from `landed` — so the tool no longer claims to know. Both now
  render `unresolved (could not determine the repo; if one is named here, add it to
  KNOWN_REPOS…)` — a **conditional**, true either way, which keeps the affordance without
  the false premise. `ambiguous — A, B both in range` survives: repos genuinely *were* named
  there, so naming them is a true diagnosis. `KNOWN_REPOS` is a closed vocabulary, so
  **widening it is not the fix** (same shape as the status sets below); verify owners with
  `gh repo view` — `devrc` is **innovation-upstream**/devrc, and `civitai/devrc` does not
  exist.
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
   `my_latest_reply` (stale producer, a failed user-id lookup, or a date on one of YOUR
   comments the producer could not read) fires but says the check never ran, rather than
   silently deciding either way.
   🔴 **Compared on the raw epoch-ms, not on the displayed minute** (2026-08-22, round 7).
   `format_date` threw the seconds away before anything compared them, so a reply written
   **20 seconds BEFORE** the question rendered identical to it and counted as an answer — a
   waiting colleague dropped from the report. Whether a tie means "answered" was a judgement
   call argued to opposite conclusions from the same evidence; the producer HAS the raw ms,
   so it is now a question of fact and a tie means the same millisecond. `date_ms` /
   `my_latest_reply_ms` ride beside the display fields and are used **only when BOTH are
   present** — comparing one raw instant against a rounded one is worse than comparing two
   rounded ones — otherwise the minute-age comparison is used unchanged. An unreadable date
   yields an **absent** ms, never a `0`: zero is 1970, which would make every reply look
   newer than every question.
   🔴 **A SUPPRESSED flag now prints one line of its own, and that is where the bot-identity
   caveat lives.** **ClickUp has no bot identity**: every comment posted through the `pk_`
   token comes back authored as the token's owner whoever typed it, so *"you answered"* and
   *"an agent answered as you"* are the **same observable** — an agent-posted comment sets
   `my_latest_reply` and suppresses this flag. Suppression used to print nothing at all, so a
   genuinely-waiting colleague left the report with no trace — and with the transcripts
   finding nothing, the flag that would have caught it was the one being suppressed. The line
   goes in its **own** block (`## Answered already — no action, but check who answered`),
   never in **Needs a decision** — a "no action" line in the act-on-this block is how a block
   stops being read — and the caveat leads that block **once**, not per line (199 chars ×
   every line was ~11 KB in a `--limit 20` report, in the one block whose justification is
   that volume kills a block). Each note keeps the caveat's *consequence*.
   🔴 **The note is bounded, and it must be able to PROVE it is bounded.** It carries the same
   recency window as the flag; the bound falls back to `date_ms` when the display date is
   unreadable (otherwise a drifted `format_date` kills the bound for every record while the ms
   path keeps deciding — measured: a 2019 ticket printed an unbounded note); and if NO bound
   can be evaluated from either field the record falls through to the **flag** instead, since
   an un-expirable "no action needed" line is exactly the permanent noise this is guarding
   against, while a call to action should survive an unreadable date.
   🔴 **The note states other coverage as a COMPUTED fact, never an assumed one.** It used to
   assert that nothing else in the report named the ticket, *because* `mentions_found == 0` —
   false in four reproduced shapes (unknown status / RESOLVED-reading comment / keep-open veto
   / open cited PR, each printing a **Needs a decision** line about the same ticket directly
   above it), and a non-sequitur besides: not one of those four rules reads `mentions_found`.
   It now runs `disagreements([r])` on that ONE record and says what came back — `[r]`, not
   the whole result list, or every note in the report inherits one ticket's flag.
   🔴 **The seam is crossed for TOP-LEVEL comments only.** `recent-comments.py` calls the CLI
   without `--threads`, and `/task/{id}/comment` returns top-level comments only — replies
   live behind `getThreadedComments`. So an answer you wrote *inside a comment thread* is
   still invisible and the ticket still reads as unanswered. Narrower than D12, same shape.
   (Verified 2026-08-22 that the two answers on `868gz0hhh` are top-level, so the motivating
   case really is fixed — but do not read that as the general case.)
   🔴 **THIS FLAG HAS ITS OWN CORPUS — SCORE IT, DO NOT ARGUE FROM THE EXAMPLE.**
   `tests/test_waiting_corpus.py` holds **21 labelled RECORDS** (pre-port 10/21, shipped
   21/21). `test_corpus.py` cannot help here: it scores comment TEXT and this flag reads
   FIELDS. Verdicts are read off the **claim** — "nobody has answered" vs "the check did not
   run" vs "the date was unreadable" vs the suppression note vs silence — because "the flag
   fired" is satisfied by four lines that tell a reader four different things.
   🔴 **New false-SILENCE direction, deliberately unbounded by transcripts:** reply *"I'll
   look next week"* and the flag is silenced for that comment even though the transcripts
   showed no work — which is the gap this flag was added to fill. (Do NOT restate that as
   *"`mentions_found == 0` means no other rule covers the ticket"*: that is a non-sequitur,
   retracted 2026-08-22. A zero mention count says the TRANSCRIPTS are empty; the status,
   comment and PR rules never read it and can each still flag the ticket.) Bounded only by a
   *newer* colleague comment re-firing it. Acknowledging is not doing.

🔴 **FOUR of these checks NEED the transcript scan, and it is OFF by default — the run names
every one of them.** The cited-PR resolution (source 3), the waiting flag (source 4),
*"ClickUp `<done>` but open signals remain"*, and *"transcripts read as done while ClickUp is
still open"*. The first two of those are gated on a **SEARCHED** zero; the other two need a
completion verdict and an evidence list that a scan-less run never builds. Since the scan
became opt-in the scan-less record carries **no**
`mentions_found` at all — deliberately, because an unsearched zero is not a searched one —
so in the default invocation those rules are *structurally unable to fire*. Upstream that
was **silent**, and when nothing else disagreed the **Needs a decision** heading was not
printed at all: measured by driving the tool, an empty block reading as *checked, nothing
disagrees* when it meant *rules never ran*. Exactly the failure round 4's unknown-status
announcement exists for, one axis over, and it arrived with a change that was individually
correct. A default run now prints **one** line naming every such rule and how to enable them.
Pass `--transcripts` and the line disappears.
🔴 **The list is BUILT from a ledger (`SCAN_ONLY_RULES`), not restated in prose, because the
first version of that announcement named TWO of the four.** Naming a subset is worse than
naming none — it implies the unnamed ones ran, which is the same defect one axis further
over. The ledger pairs each rule with its flag's own unique tail, and a test diffs a
fully-armed fixture across both modes: it fails when a fifth scan-dependent rule is ADDED and
when one is REMOVED. A second test forbids a rule's announcement NAME from containing its own
TAIL, so `"<tail>" in output` can never be true in a run where that flag did not fire — the
instrument-matching-its-own-announcement trap that cost upstream a false failure.
🔴 The two guards deliberately key on **different** facts, and neither is a typo: the flags
gate on the STATE (`"mentions_found" in r`) because they ACT on evidence and a renamed status
sentinel must not be able to fire them; the announcement keys on the run's own declaration
(`status == "not_scanned"`) because it DESCRIBES the run, and a record merely missing the key
(a stale producer, a partial write) is not evidence that the whole scan was skipped.

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
| default (no `--transcripts`) | **~21s** | ClickUp only; no per-task transcript sweep |
| `--limit 5 --transcripts` | **~85–100s** | All assigned tasks; one transcript search per task |

⚠️ **Every figure in this table is upstream's, and the two `--transcripts` rows were measured
before the scan became opt-in.** Re-measured upstream 2026-08-21: **98.5s** and **85.4s**
wall (two runs); the `~41s` quoted until then was a 2026-08-19 figure that had silently
doubled as the transcript corpus grew. The ~21s default is upstream's single measurement of
the same day. The ClickUp fetch dominates the cheap path, but the per-task sweep scales with
`~/.claude/projects`, so expect drift on both — and this host's corpus is not upstream's.
**Re-time it rather than quoting it.**

## Tests

Run all tests (**226** collected, measured 2026-08-22). `run_all.py` exits non-zero on
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
(`scripts/run-tests.sh`). Both runners see the same 226; `run_all.py` survives because it
purges `__pycache__` and reports an import failure as a FAILURE, which pytest's summary
line does not distinguish as loudly. **Raise the floor when you add tests.**

🔴 **The mutation battery lives in the repo now**: `tests/mutation_sweep.py` holds every
mutant as DATA plus a runner (`--list`, an id filter, `--check` to fail on a stale one).
Earlier rounds each ran a sweep, reported "0 survived", and threw the driver away — the
number was then unreproducible from the repo, and the next round re-invented the list and
re-discovered the same sites. A blind re-audit of one such "51 mutants, 0 non-killed" found
**seven more at the same delta sites**, one of which reverted that round's headline fix.
**Extend the list; do not start a new one.** Currently **82 rows (81 mutants + a positive
control) + a NULL CONTROL, 0 non-KILLED** — the driver prints `82 mutant(s); non-KILLED: 0`.
🔴 **`--check`'s other job is catching a mutant whose ANCHOR you moved.** Measured
2026-08-23: one edit to `suppressed_notes` silently stranded `M49` and `M-SCOPE` at
`NOT APPLIED` — and `M-SCOPE` is the mutant a blind re-audit once found surviving a green
sweep. `--check` went red and named both. A `NOT APPLIED` row reads exactly like a pass in a
long list; only the exit code tells them apart.
🔴 That null control is not decoration: it copies the skill, mutates NOTHING and must report
SURVIVED. On this battery's first run every mutant scored KILLED *because*
`test_no_real_identifiers.py` cannot resolve the repo root from a temp copy and reddened
them all for free. Read the killer NAME, not just the verdict.

```bash
PYTHONDONTWRITEBYTECODE=1 python3 "$CCUA/tests/mutation_sweep.py" --check
```

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
  green suite. Pin the SEAM, not only the parts. It also carries the round-7 set: the
  `UNIDENTIFIED` sentinel for an unreadable date on one of YOUR comments, the raw-ms
  comparison, and the suppression note — including
  `test_main_actually_PRINTS_both_blocks_end_to_end`, which drives `main()` and reads STDOUT
  because deleting its whole print loop left the suite green.
- `test_waiting_corpus.py` — 🔴 **the second labelled corpus, 21 whole RECORDS.**
  `test_corpus.py` scores comment TEXT; the waiting flag reads FIELDS, so none of those 49
  cases can see it — and this flag had by then been argued from single anecdotes three
  rounds running. Verdicts are read off the **claim** the report makes (`WAITING` /
  `ANNOUNCE` / `UNREADABLE` / `ANSWERED` / `SILENT`), never off whether something fired.
  Pre-port scores 10/21, shipped 21/21. Same rule as the other corpus: **add your motivating
  case, with the verdict a human would give, BEFORE you change code.**
- `test_bounds_and_parsing.py` — the round-8 set. **Section 1 IS regression coverage**: the
  scan-less announcement, its one-line-per-run bound, the state-gated open-signals flag, and
  an end-to-end `main()` drive in BOTH modes — red before the transcript opt-in landed.
  Everything after it is the other-coverage sentence's SCOPING, the DISPLAY half of the
  two-ages split, and `UNANSWERED_COMMENT_DAYS` pinned ON its boundary (14.0) and at a
  NON-multiple overshoot (20) — the old fixtures sat at 13 and 30, and 30 is more than 2×14,
  so a doubling mutant passed straight through the gap. ⚠️ Those are **mutation-coverage
  guards, not regression coverage**: each SURVIVED a full green battery on a tree where the
  behaviour was already right. The file says so, and says why its red against pre-port
  `main` does not count.
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
