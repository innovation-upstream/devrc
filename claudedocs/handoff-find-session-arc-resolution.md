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
- Branch: `docs/handoff-find-session-arc` (worktree `~/workspace/devrc-fs-arc`, pushed).
  Base clone is on `main` and was NOT committed to — devrc forbids it.
- **Nothing is implemented.** This session produced the design and its measurements only.
  A clawgate task body is drafted and was awaiting operator approval at session end
  (scratch path below); the task may or may not have been posted.
- 🔴 NO `clawgate-task:` field: `clawgate_handoff.sh resolve` exited **5** — 0 tasks for
  this session. An unknown session id answers 200 with an EMPTY ARRAY, so this cannot
  distinguish "touched no task" from "wrong id". It is NOT a statement that the board is fine.
- Four design decisions settled WITH THE OPERATOR 2026-09-18, not open: reader **and**
  trailer-write in the first cut · annotate ordinary results (not `--arc`-only) · all four
  repos with origin labels · report the unstamped gap as a count, never guess.
- Task body draft: `<scratchpad>/task-body.md` (session 8951d8f0). If the task was not
  posted, that body is the spec — re-derive nothing.

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

## Next steps (ranked)
1. **Update and merge `#1754`, then `#1753`.** Both are one branch-update from green;
   the diagnosis above is complete. #1754 first — it is what stops the closed
   `handoff-resume-skill-trace` arc being re-opened by every future `/resume`.
   forcing: gate — two PRs blocked 24h on a red proven to be inherited, with the
   merged-tree control already green at 451 passed.
2. **Implement `find-session --arc` + the `handoff_doc.py` trailer**, to the spec in the
   drafted clawgate task body. Touches `scripts/find-session.py`,
   `scripts/lib/handoff_doc.py`, `claude/skills/find-session/SKILL.md`, and new tests.
   forcing: none
3. **Confirm the `/handoff` kickoff string is unconditional** (the probe above). Cheap,
   and it is the assumption rank 2's reader leg rests on.
   forcing: none

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

## How to verify
```bash
# the two stuck PRs — is the red still inherited?
git -C ~/workspace/devrc fetch origin -q
gh pr checks 1754 --repo innovation-upstream/devrc
git -C ~/workspace/devrc merge-base --is-ancestor 61faa675 \
  $(git -C ~/workspace/devrc rev-parse origin/docs/handoff-arc-closed)   # false ⇒ inherited

# the trailer-parser trap, both instruments, on the same corpus
git -C ~/workspace/devrc log --format='%(trailers:key=Claude-Session-Id,valueonly=true)' \
  -- 'claudedocs/handoff-*.md' | grep -c .        # UNDERCOUNTS
git -C ~/workspace/devrc log --format='%B' -- 'claudedocs/handoff-*.md' \
  | grep -c '^Claude-Session-Id:'                 # the honest reader

# the genesis discriminator (expect a large raw set, 3 after the filter)
python3 ~/workspace/devrc/scripts/find-session.py handoff-handoff-resume-skill-trace \
  --all-time --any --claude-only --limit 60 --json
```
