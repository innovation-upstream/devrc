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
- **BUILT AND PUSHED: `#1777`** (`feat/find-session-arc`, commit `4020db48`, worktree
  `~/workspace/devrc-arc-impl`). Implements clawgate task **#626**, which this session also
  authored and then picked up. Task is `in_progress`; it will finish at **`ready_for_review`,
  never `complete`** — this session wrote the acceptance criteria, and grading an exam you
  wrote is what the status gate exists to stop.
- **11 of 12 acceptance criteria verified.** The outstanding one is criterion 12, the pytest
  gate on the MERGED tree (`a3aeed07` = `origin/main` `97c20d06` + `4020db48`); it was still
  running at session end. 🔴 **Do not claim the gate passed — read it.**
- 48 new tests; **866 passing** across `test_find_session_arc.py`,
  `test_handoff_doc_session_trailer.py`, `test_find_session_skill_{contract,cli}.py`,
  `test_find_session_live.py`, `test_handoff_doc.py`, `test_session_trailer.py`,
  `test_session_stamp_seam.py`. That is a set of modules I NAMED, not a tier — it cannot
  support a claim about `main`.
- `#1776` (the doc that opened this arc) MERGED as `d5d009de`.
- 🔴 NO `clawgate-task:` field: `clawgate_handoff.sh resolve` exited **5** — 0 tasks for this
  session, which cannot distinguish "touched none" from "wrong id". Task #626 is nonetheless
  this session's, by creation; the field is absent because the resolver could not confirm it.

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

## Next steps (ranked)
1. **Read the merged-tree gate result and finish task #626.** The run is
   `scripts/gate.sh --tier pytest` on `a3aeed07`; its log path is printed in its own output.
   🔴 Its exit status is authoritative, and **90 (could-not-vouch) and 91 (PARTIAL) are not
   passes**. Then post the completion comment on #626 with per-criterion evidence and flip to
   `ready_for_review`.
   forcing: gate — a shipped PR with one acceptance criterion unmeasured.
2. **Fix the write-back guard's fixture false positive** per the investigation above.
   forcing: none
3. **Update and merge `#1754` then `#1753`** — still open, still one branch-update from green;
   the inherited-red diagnosis is in `handoff-handoff-resume-skill-trace.md` and is complete.
   forcing: gate — two PRs blocked on a red proven inherited, merged tree measured green.

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

## How to verify
```bash
# the arc, end to end (expect 3 members and the coverage line)
python3 ~/workspace/devrc-arc-impl/scripts/find-session.py --arc handoff-handoff-resume-skill-trace

# the two readers on the same commit — the premise, measured both ways
git -C ~/workspace/devrc log --format='%(trailers:key=Claude-Session-Id,valueonly=true)' \
  -- 'claudedocs/handoff-*.md' | grep -c .      # UNDERCOUNTS (33%)
git -C ~/workspace/devrc log --format='%B' -- 'claudedocs/handoff-*.md' \
  | grep -c '^Claude-Session-Id:'               # the honest reader (55%)

# the guard false positive: a doc the guard named, that does not exist
git -C ~/workspace/devrc ls-files 'claudedocs/**/handoff-x-y.md'   # empty
```
