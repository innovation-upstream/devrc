# Handoff: find-session-arc-resolution — 2026-09-18

## Run this first — the index, one command
```bash
cairn recall --repo ~/workspace/devrc
```
Terse pointers this doc does not carry, curated by past sessions and outliving it.
🔴 RECALL, NOT LIVE OBSERVATION — every line is a pointer to VERIFY, never a current
reading, and it may describe a gotcha already fixed. `scope-absent`/`scope-empty` means
nothing is recorded yet: ordinary, not an error, and not a clean bill of health.
Non-blocking: if it exits non-zero, print the stderr line and carry on.

## Goal
Make `find-session` resolve a whole ARC — every session that worked one handoff doc —
instead of one session at a time. The operator's reported pain: find an old session,
resume it, discover it ended in a `/handoff`, then re-run `find-session` to locate the
session that picked the work back up. Sibling efforts on the same tool, different docs:
`handoff-find-session-archive-window.md` (the 12-day window),
`handoff-find-session-live-first.md` (the `--live` inversion),
`handoff-find-session-opencode.md` (the second corpus).
- **closing-condition:** `check` — `python3 scripts/find-session.py --arc
  handoff-handoff-resume-skill-trace` returns at least the two sessions recoverable from
  that doc's committed trailers, each role-tagged, AND `scripts/gate.sh --tier pytest`
  passes on the merged tree. 🔴 FROZEN AT ROUND 1.

## State now
- **`#1777` now carries the round-0 audit fixes: `3d810fe7`.** The FIRST merged-tree gate run
  **FAILED (3 tests)**; all three were mine and all three are fixed. A clean re-run on a fresh
  merged tree (`4cd35276` = `origin/main` `97c20d06` + `3d810fe7`) was in flight at session end.
  🔴 **No gate pass is claimed.**
- **`#1778`** carries this doc. **Task `#626` is still `in_progress`** — the completion comment
  waits on the clean gate, and will flip to **`ready_for_review`, never `complete`** (this
  session wrote the criteria).
- Round 0 of `/audit-pr 1777` ran and produced real findings: ledger `round 0 · requirements:
  14 (unattributed: 2) · deletion candidates: 8`. **The nine correctness axes have NOT been
  run** — round 0 reports and cannot end a ladder, so the checklist pass is still outstanding.
- 908 tests green across the named affected modules INCLUDING `test_transcript_search.py`,
  which the audit added to that set and which my own first run had omitted. That is still a
  set of modules I NAMED, not a tier.

## Open investigations — live diagnosis state

### Two handoff PRs have been stuck 24h on a red neither diff can reach
- as-of: 2026-09-18
- **Symptom + exact repro:** `#1754` (closes the `handoff-resume-skill-trace` arc —
  declares and meets its closing-condition) and `#1753` (a third Tekton congestion shape)
  have both sat OPEN since 2026-09-17, each red on `tekton/devrc-pytests` for
  `TestRuleFDidNotMoveTheExitCodes.test_the_prose_quotes_the_CONSTANT_not_a_stale_literal`.
  ```bash
  gh pr checks 1754 --repo innovation-upstream/devrc
  git -C ~/workspace/devrc merge-base --is-ancestor 61faa675 960e9805   # false
  ```
- **Observed (with values):** control at `origin/main` — **6 passed**, green. `#1750`
  (`d2844af6`) pruned `claude/skills/handoff/SKILL.md` and broke 17 pinned sentences;
  `#1756` (`61faa675`) restored them. `merge-base --is-ancestor 61faa675 <head>` is **false
  for both heads** — neither branch contains the fix. #1754 is 19 commits behind main,
  #1753 is 23. Merged-tree control, `origin/main` + #1754: **451 passed**.
- **Ruled out:** that either diff can reach the test — #1754 touches one `claudedocs/`
  doc, #1753 touches `claude/skills/tekton/SKILL.md`; the test reads the handoff skill's
  prose. via: measurement (control run at `origin/main` + merged-tree run)
- **Leading hypothesis:** nothing is wrong with either PR. Updating each branch onto
  current `main` clears the red. #1754 is the higher-value one: until it merges, the
  `handoff-resume-skill-trace` doc on `main` carries NO `closing-condition:`, so every
  future `/resume` reports that closed arc as UNANSWERABLE and re-opens it.
- **Next probe:** none needed — update both branches and merge. The diagnosis is complete.

### Is the `genesis` discriminator stable enough to be the reader's primary index?
- as-of: 2026-09-18
- **Symptom + exact repro:** arc membership is currently inferable only from prose.
  ```bash
  python3 ~/workspace/devrc/scripts/find-session.py handoff-handoff-resume-skill-trace \
    --all-time --any --claude-only --limit 60 --json
  ```
- **Observed (with values):** 48 sessions match the slug; filtering on the `genesis` field
  already present in that JSON leaves **3**, and those 3 are exactly the arc's resuming
  sessions. ~6% precision raw, 100% on this arc after the filter. The 45 rejects are
  single-hit mentions in unrelated repos, mostly one cairn index bullet recalled elsewhere.
- **Ruled out:** short-sha search as a join key — 5–11 hits per sha, cross-repo noise.
  via: measurement
- **Ruled out:** searching for the write command (`--topic <slug>`) — returns the same
  three resumers and finds nobody new; it does NOT find the originating session.
  via: measurement
- **Leading hypothesis:** genesis is a sound primary index for READERS and structurally
  cannot see WRITERS or the ORIGINATOR (a session that creates a doc never resumed from
  it). That is why the design unions it with the git trailer walk rather than choosing one.
- **Next probe:** confirm the kickoff string `Canonical handoff (read first): <path>` is
  emitted unconditionally by `/handoff` step 3, since the whole reader leg keys on it.
  If it is ever reworded the reader must degrade to scanning for a
  `claudedocs/handoff-*.md` path, NOT silently return fewer rows.

### The handoff write-back guard fires on a doc name that only exists in a TEST FIXTURE
- as-of: 2026-09-18
- **Symptom + exact repro:** at session end the Stop guard reported
  `this session read handoff-x-y.md` and demanded a handoff for it. **No such document
  exists.** `handoff-x-y.md` is a synthetic string in
  `scripts/tests/test_find_session_arc.py`, used to exercise the annotation predicate.
  ```bash
  grep -rn "handoff-x-y.md" ~/workspace/devrc-arc-impl/scripts/tests/
  git -C ~/workspace/devrc ls-files 'claudedocs/**/handoff-x-y.md'   # empty — no such doc
  ```
- **Observed (with values):** the guard named `handoff-x-y.md` with a read timestamp of
  `2026-09-18T22:49:45Z`, which is when the test file carrying that fixture was being edited.
  The earlier firing in the same session correctly named the real
  `handoff-handoff-resume-skill-trace.md`, so the guard is not simply broken — it is matching
  a doc-shaped STRING rather than a doc that exists.
- **Ruled out:** that a doc by that name was created and deleted — it appears in no commit on
  this branch and `git ls-files` finds nothing. via: command
- **Ruled out:** that it came from the arc reader's output — the reader prints real basenames
  from git, and this name has never been in any repo. via: code + measurement
- **Leading hypothesis:** the guard's doc detector scans session activity for a
  `handoff-*.md`-shaped token without checking the file EXISTS. Writing tests ABOUT handoff
  docs therefore arms it against fixtures. Self-referential and cheap to fix: require the path
  to resolve in a repo before counting it as a read.
- **Next probe:** read the detector in `scripts/claude-hooks/handoff-write-guard.py` and check
  whether the candidate is existence-tested. If not, gate it on `git ls-files` (or a `Path.exists`
  against the repo's `claudedocs/`), and add a fixture-shaped negative control — a test whose
  body contains a plausible `handoff-*.md` string that must NOT arm the guard.

### The write-back guard's fixture false positive — CONFIRMED, second instance
- as-of: 2026-09-18
- **Symptom + exact repro:** the Stop guard has now fired TWICE in one session naming a
  handoff doc that has never existed — first `handoff-x-y.md`, then `handoff-same.md`. Both
  are synthetic strings inside `scripts/tests/test_find_session_arc.py`.
  ```bash
  grep -rn "handoff-x-y.md\|handoff-same.md" ~/workspace/devrc-arc-impl/scripts/tests/
  git -C ~/workspace/devrc ls-files 'claudedocs/**/handoff-x-y.md' 'claudedocs/**/handoff-same.md'
  ```
- **Observed (with values):** `git ls-files` returns EMPTY for both. The second firing came
  minutes after adding `test_writer_counts_walk_each_DISTINCT_doc_once`, whose fixture rows are
  `{"genesis": "claudedocs/handoff-same.md"}`. The guard's real firing earlier in the session
  (on `handoff-handoff-resume-skill-trace.md`) was correct, so the detector works — it simply
  does not check that the doc EXISTS.
- **Ruled out:** that one of these was a real doc created and deleted — neither appears in any
  commit on any branch of this work. via: command (`git ls-files`, `git log --all`)
- **Ruled out:** that it is reading the arc reader's OUTPUT — the reader prints basenames
  derived from `git log`, and neither name has ever been in a repo. via: code + measurement
- 🔴 **CONFIRMED mechanism (upgraded from hypothesis by the second instance):** the detector
  matches a `handoff-*.md`-shaped TOKEN in session activity without an existence check. Writing
  tests ABOUT handoff docs therefore arms it against its own fixtures — and this PR's whole
  subject is handoff docs, so it will keep firing for as long as this work continues.
- **Next probe:** read the candidate extraction in
  `scripts/claude-hooks/handoff-write-guard.py`; gate it on the path resolving in a repo
  (`git ls-files` or `Path.exists()` under `claudedocs/`). Add a negative control: a test whose
  body contains a plausible `handoff-*.md` string that must NOT arm the guard — without that
  control the fix is unfalsifiable, since the guard's normal state is silence.

## Next steps (ranked)
1. **Read the clean merged-tree gate (`4cd35276`) and finish task #626.** 🔴 **Read the
   GATE's own verdict line, not a shell exit status** — see the Gotchas entry below; the first
   run's completion notification said `exit code 0` over `GATE: RESULT=FAIL exit=1`. Then post
   the per-criterion completion comment and flip to `ready_for_review`.
   forcing: gate — a shipped PR whose last full-tier run FAILED, with fixes since.
2. **Run the nine correctness axes on `#1777`** (`/audit-pr 1777`). Round 0 is not a substitute
   and explicitly cannot end a ladder.
   forcing: gate — an unaudited PR whose round 0 found 3 defects in code that was already green
   on the modules its author chose to run.
3. **Fix the write-back guard's fixture false positive** per the confirmed investigation above.
   forcing: none
4. **Update and merge `#1754` then `#1753`** — still one branch-update from green.
   forcing: gate — two PRs blocked on a red proven inherited.

## Gotchas / decisions / dead-ends
- 🔴 **`git log --format='%(trailers:key=Claude-Session-Id)'` IS THE WRONG READER AND IT
  FAILS SILENTLY.** Git's trailer parser returns EMPTY when a squash body places a `*`
  bullet after the trailer block. MEASURED on the handoff-doc corpus: it reported
  **197 of 593 (33%)** where a `^Claude-Session-Id:` grep over `%B` reports
  **327 of 593 (55%)** — a 40% relative undercount, on commits whose trailer is plainly
  visible in `git log -1 --format=%B`. `scripts/lib/session_trailer.py:383` already
  documents this trap and I hit it anyway, mid-analysis, and quoted the wrong number
  before catching it. **Read the BODY, never the trailer parser.**
- 🔴 **The edge this whole design needs ALREADY EXISTS — measure before building.** The
  first framing of this work was "there is no session↔doc link, so capture one". False:
  `Claude-Session-Id:` trailers have been on commits since ~August (`session_trailer.py`,
  `install-session-stamp.sh`), they **survive squash merge** (`702c1c32` is a PR squash
  carrying `c5b40e88…`), and September coverage is **290 of 332 (87%)**, 126 distinct
  sessions reachable. The task became a READER plus one gap-closer, not a capture system.
- 🔴 **ANY transcript-mining join key POLLUTES THE CORPUS IT SEARCHES.** This session now
  matches every commit sha it probed, because it printed them. That is not a flaw in one
  query — it is a property of keyword archaeology over a corpus that records your own
  archaeology. It is the argument for a structural key (a trailer) over a textual one.
- 🔴 **DO NOT ARM THE `prepare-commit-msg` HOOK GLOBALLY — this is the obvious "fix" for
  the missing 13% and it is forbidden.** `scripts/install-session-stamp.sh`'s own header
  records why: `githooks/install.sh` points `core.hooksPath` GLOBALLY, so a file placed
  there is armed on every repo on the box for anyone who ever ran that installer, and a
  suite once rewrote the branch it was pushing. The chosen route is the opposite direction
  — `handoff_doc.py` stamps its OWN commit, so the tool guarantees the trailer without a
  hook being installed anywhere.
- **The coverage gap is per-CLONE, not per-worktree.** `install-session-stamp.sh` installs
  into `git rev-parse --git-common-dir`/hooks, which worktrees share. A separate clone has
  no hook — which is why this arc's originating commit (`c337765e`) is unstamped while the
  commit 61 minutes later (`44d8847d`) is stamped.
- **A new `find-session` flag has TWO two-way ledgers**, and neither is obvious:
  `ARCHIVE_ONLY_FLAGS` (`scripts/find-session.py:606`) and `EXIT_CONTRACT` (`:183`), both
  pinned against the prose table in `claude/skills/find-session/SKILL.md` by
  `scripts/tests/test_find_session_skill_contract.py`. Add a flag without updating both and
  the suite goes red in a file the diff never names.
- **Session UUIDs are safe in this PUBLIC repo; transcript PATHS are not.** 327 UUIDs are
  already committed as trailers. Paths name client repos
  (`-home-zach-workspace-civit-…`), so `--arc` output must carry ids + repo labels only.
- **Decision, with the reason:** no backfill of the 266 unstamped historical commits. A
  time-bracket attribution (transcript span contains commit time) was considered and
  declined by the operator — on a box running dozens of concurrent sessions it can name the
  wrong one, and a chain that looks complete and is not is the failure mode this repo's
  rules keep naming. The gap is reported as a count instead.
- ⚠ **`handoff_doc.py` pushes wherever the checkout sits, `main` included**, and devrc
  forbids committing to `main` in either host checkout. This session drafted in a worktree
  on a feature branch for exactly that reason. Check `git branch --show-current` before
  step 5, every time — the tool will not check it for you.

- 🔴 **MY OWN MUTATION BATTERY WAS WIRED TO NOTHING ON ITS FIRST RUN, AND IT LOOKED LIKE A
  CLEAN SWEEP.** Every row printed blank. Cause: the row `T="$A $B"` then `pytest $T` — and
  **zsh does not word-split**, so pytest received ONE bogus path, collected nothing, and never
  printed a `passed`/`failed` line for the grep to find. The documented trap, hit while
  building the instrument that was supposed to catch defects. **The fix that mattered was
  running an unmutated CONTROL row first and requiring it to print `0 failed / 48 passed`** —
  a battery whose control prints nothing is measuring nothing, and every row after it is a
  fabricated SURVIVED.
- 🔴 **A MUTATION FIXTURE MUST BE COPIED FROM REALITY, NOT RECONSTRUCTED FROM THE STORY YOU
  TELL ABOUT IT.** The first `SQUASH_BODY` put the `Claude-Session-Id:` trailer LAST — a
  perfectly plausible squash shape, and one git's own parser reads correctly. The control went
  GREEN for both readers and *correctly reported itself as wired to nothing*. The real shape
  (`f9ef66e4`) has the trailer **mid-message with ordinary prose after it**, which is what
  makes the final block a non-trailer block. The trailing prose IS the mechanism. Had the
  control been written to simply assert the reader works, the wrong fixture would have shipped
  and the premise would have rested on nothing.
- 🔴 **`git log --format='%(trailers:key=…)'` IS THE WRONG READER AND UNDERCOUNTS SILENTLY.**
  197 of 593 (33%) against a `^Claude-Session-Id:` scan of `%B` at 327 (55%) — a 40% relative
  undercount on commits whose trailer is plainly visible. `session_trailer.py:383` already
  documented it; I hit it anyway and quoted 33% before catching it. Read the BODY.
- 🔴 **A PERMISSIVE PARSER MADE A WHOLE BRANCH UNREACHABLE, SILENTLY.** `doc_basename` accepts a
  bare topic (`foo` -> `handoff-foo.md`) — so it accepted a UUID too and returned
  `handoff-<uuid>.md`, a doc that cannot exist. Ordering the UUID test AFTER it made the
  session-id seed branch dead code, and the failure mode was a *clean* "no repo holds that doc"
  for a session id that resolves perfectly. Found only because a test asserted the resolved
  value rather than that the call did not crash. **When two parsers can both accept an input,
  the more specific one runs first.**
- 🔴 **THE BASH GUARD JUDGED THE WRONG REPO BECAUSE MY COMMAND USED `$W`.** `git -C $W commit`
  was blocked as a commit to `main` — the guard cannot resolve a shell variable, so it judged
  the CWD (`~/workspace/devrc`, on `main`) instead of the worktree. The guard says so in its own
  message. **Pass `-C` an absolute path for any git write from a worktree**, or the protection
  fires on the wrong subject and the real subject goes unchecked.
- **Decision: exit 5, not a widened exit 4.** `run_arc`'s could-not-measure case initially
  returned `EXIT_UNAVAILABLE`, which reddened
  `test_every_EXIT_UNAVAILABLE_source_is_on_the_tail_path` — a STRUCTURAL guard keeping exit 4's
  "`--tail` ONLY" claim true of code nobody has written yet. The ledger offered "change
  EXIT_CONTRACT and the doc together"; taking that would have deleted a real guarantee to save
  a constant. A fifth code was registered instead. **This is a deliberate deviation from the
  task's own criteria, made against the criteria and in favour of the existing contract.**
- **Five ledgers broke on one new flag, and all five were REGISTERED rather than exempted:**
  `ARCHIVE_ONLY_FLAGS`, `EXIT_USAGE_SITE_COUNT` (10 -> 11), `EXIT_2_CAUSES`,
  `test_the_contract_codes_are_the_scripts_own_EXIT_constants`, and the two pinned SKILL.md
  tables. Each failure message named its own fix — they are worth reading rather than working
  around.
- **The seam nobody owns, closed on purpose:** `test_the_arc_reader_can_read_back_what_this_tool_wrote`
  is the only test that builds the combined state of the writer (`handoff_doc.commit_message`)
  and the reader (`handoff_arc.resolve_arc`). Both were hermetically tested apart; a trailer
  written in a form the reader cannot parse is a defect neither module's own suite can see.
- ⚠ **A test docstring of mine over-claimed and was corrected before merge.** The hook-compose
  test said its stand-in hook "appends unconditionally — so a blind append would read 2". The
  hook actually checks first, and the M7 mutation proved the point: a naive appender left that
  test GREEN and was killed only by the unit test. The docstring now states exactly what the
  test does and does not cover. **A guard's description is a claim; check it is as wide as its
  body.**

- 🔴 **A BACKGROUND TASK'S "exit code 0" WAS MY OWN `echo`, AND IT SAT OVER A FAILING GATE.**
  The run was `gate.sh > log 2>&1; echo "GATE_RC=$?" >> log`. The harness reports the exit of
  the COMPOUND command — the `echo` — so the completion notification read `exit code 0` while
  the log's own last lines read `GATE: RESULT=FAIL exit=1`, 3 tests failed. This is the
  documented "a trailing command swallows the status" trap, reached through a *notification*
  rather than a pipe, which is a route the rule's own wording does not picture. 🔴 **Read the
  runner's own `RESULT:`/`GATE:` line. Never a wrapper's exit status, and never a task
  notification's.**
- 🔴 **I MUTATED THE TREE A GATE WAS READING, MID-RUN.** While verifying an audit finding I
  copied a fixed `find-session.py` into the merged worktree the gate was walking. Its verdict is
  therefore about no single tree and is void whatever it said. **A gate run owns its worktree
  for its whole duration** — verify a finding in a SEPARATE tree, and if you have touched the
  one under test, throw the run away and rebuild. The re-run uses a fresh worktree for exactly
  this reason.
- 🔴 **ROUND 0 FOUND A DEFECT CLASS THE GATE STRUCTURALLY COULD NOT: A PROMISE THE CODE COULD
  NOT KEEP.** `arc_counts` was threaded through `arc_annotation` and supplied by no production
  caller, so the `(N sessions)` the OPERATOR selected in an `AskUserQuestion` could never print
  — while both branches were covered by tests and every suite was green. **Dead parameter
  surface with test coverage on the unreachable branch reads as a feature.** The tell is not a
  red test; it is asking who the requirement's author was and whether the shipped code satisfies
  it.
- 🔴 **FIXING THAT SURFACED A SECOND BUG IN THE FIX — the dedup keyed on the RESULT map rather
  than a `seen` set**, so a doc resolving to NO repo was re-walked once per hit (50 hits naming
  one absent doc = 50 lookups, 0 answers). Caught by a test asserting the call COUNT, not the
  return value. An audit fix resets the verification gate; budget for the fix's own defect.
- 🔴 **A ROLE LABEL IS A CLAIM. `ORIGINATED` WAS AN INFERENCE PRINTED AS A FACT.** It came from
  commit order and is unsound exactly when a writer is missing — 45% of corpus doc commits are
  unstamped — so on an older doc it asserted who started an effort on the same screen as a line
  saying some writers are invisible. Now `earliest-stamped` whenever coverage is incomplete.
  **The negative control is the load-bearing half**: without a test proving the demotion is
  CAUSED by the missing writer, deleting the whole inference would pass.
- 🔴 **A PRIVATE GLOB OF A SHARED CORPUS IS A LEDGER VIOLATION, AND IT WAS ALSO A BUG.**
  `session_genesis` globbed `*/{id}.jsonl` itself; `JSONL_GLOB_SITES` pins corpus globbing to
  ONE module and two tests went red on the merged tree. `transcript_search.find_transcript` is
  the existing spelling AND is stricter — it applies `is_corpus_member`, so a `subagents/` id
  resolves to nothing, where the raw glob returned one. **Deleting the duplicate was cheaper
  than registering it, and it fixed a defect nobody had reported.**
- ⚠ **My own count was understated, not overstated, and an audit corrected it:** the PR says
  "48 new tests"; measured across both new modules it is ~60. Recorded because the reflex is to
  assume a self-reported number is inflated.
- **Decision, recorded so it is not re-litigated:** the annotation's count is rendered as a
  FLOOR (`3+ sessions`) rather than exact. It counts git writers only; readers need a corpus
  walk and paying one PER HIT would cost more than the query. A bare `(3 sessions)` would be a
  precise-looking undercount, which is the failure the coverage line exists to refuse.

## How to verify
```bash
# the clean merged-tree gate — read the GATE line, never a shell status
grep -E 'RESULT:|GATE:|TOTAL collected' <the gate log named in its own output>

# the guard false positive: two docs the guard demanded, neither of which exists
git -C ~/workspace/devrc ls-files 'claudedocs/**/handoff-x-y.md' 'claudedocs/**/handoff-same.md'

# the demoted role and the named excluded corpus
python3 ~/workspace/devrc-arc-impl/scripts/find-session.py --arc handoff-handoff-resume-skill-trace
```
