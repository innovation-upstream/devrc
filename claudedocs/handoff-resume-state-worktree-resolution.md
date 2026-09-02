# Handoff: resume-state-worktree-resolution — 2026-09-01

## Run this first — the index, one read-only command
```bash
python3 ~/workspace/devrc/scripts/lib/subsystem_recall.py --repo ~/workspace/devrc
```
Terse pointers this doc does not carry, curated by past sessions and outliving it.
🔴 RECALL, NOT LIVE OBSERVATION — every line is a pointer to VERIFY, never a current
reading, and it may describe a gotcha already fixed. `scope-absent`/`scope-empty` means
nothing is recorded yet: ordinary, not an error, and not a clean bill of health.
Non-blocking: if it exits non-zero, print the stderr line and carry on.

🔴 **No `clawgate-task:` field.** `clawgate_handoff.sh resolve` exited **6** — one task
(#440) is linked to this session with `role=read` and it is about an unrelated clawgate
deeplink. Filing or reading a task is not doing its work, so per the skill's no-worked rule
nothing was recorded. This says NOTHING about the board.

## Goal
Close **devrc#1164**: `scripts/resume-state.sh` given an explicit path to a handoff doc that
lives in a **linked worktree** reported `NO SUCH FILE`, fell back to newest-of-N, and emitted a
complete, confident digest reconciled against **a different initiative**. Handoff docs land in
linked worktrees by construction — `claude/RULES.md` makes worktree isolation the standing
default for any file-modifying agent — so this fires on the repo's own mandated workflow.

## State now
🔴 **DONE AND MERGED.** devrc#1197 landed as squash **`6421df3c`**, closing **#1164**
(`CLOSED`). Claim `resume-state-worktree-resolution` **RELEASED**. Verified by CONTENT on
`origin/main` — a squash is never an ancestor, so ancestry says nothing: `worktrees_holding`
present (6 refs), the `$mine` gate present (10 refs), `scripts/tests/test_mutation_battery_anchors.py`
present (310 lines).

- **What shipped.** `scripts/resume-state.sh` resolves an explicit handoff path into the
  **linked worktrees of the clone the path NAMED** — never `$PWD`'s — and a handoff-shaped
  path that resolves nowhere no longer falls back to newest-of-N, so the digest reconciles
  NOTHING instead of a different initiative. No exit code was added: the script has none and
  always reports, so the empty-`HANDOFF` branch that already existed carries it.
- **Gate at the merged head `8fce256b`**, both tiers ONE AT A TIME (#1088), read from each
  runner's own `RESULT:` line: `pytests` PASS collected=20414 passed=20411 skipped=3 failed=0
  (floor 18404) · `nodetests` PASS suites=5 files=41 tests=1449 pass=1449 fail=0.
  Both required Tekton checks `success` against that sha itself.
  Battery: **73/73 killed for the RIGHT reason**, 0 NOT APPLIED, control 190/0.
  Skill battery: **38/38 for the right reason**, 0 NOT APPLIED, control 42/0.

🔴 **NOT DEPLOYED, AND THE TWO HALVES DEPLOY DIFFERENTLY — this is the live next step.**
Measured with the arbiter, not inferred:
| path | `readlink -f` | deploys on |
|---|---|---|
| `scripts/resume-state.sh` | **itself** | a plain `git pull` (0 references in `nix/`) |
| `claude/skills/resume/SKILL.md` | `/nix/store/…-devrc-claude-skills/resume/SKILL.md` | a `home-manager switch` |
Pull without switching and the narrowed resolver is LIVE while the deployed prose still
describes the old rule. Pull and switch together.

- **The audit ladder CLOSED after 4 rounds / 24 findings / 0 deploy-blocking.** Executable
  payload by round: 187 → 1 → 0 → 0.

## Open investigations — live diagnosis state
### The mutation battery's 56/56 is the agent's number, not mine
- **Symptom + exact repro:** not a failure — an unverified claim. `claude/RULES.md` says to
  re-verify an auditor's or subagent's self-reported mutation results, and I have not.
- **Observed (with values):** reported `56/56 killed, survived: none`, control 165 passed /
  0 failed, 14 new rows W1-W15 each with a named killer. The agent also reported updating
  M6, M7, M16, M22, M31 whose patterns its re-indent moved — **M16 specifically because the
  bare `if [ -z "$HANDOFF" ]` now occurs 3× and would otherwise report NOT APPLIED**, i.e. a
  silent survivor. That is the right failure mode to have caught, which raises confidence but
  is not verification.
- **Ruled out:** nothing yet.
- **Leading hypothesis:** the battery is sound; the risk is the ANCHOR class it just fixed
  (a pattern matching 0 or >1 times after a re-indent), not the logic.
- **Next probe:** `nix develop ~/workspace/devrc -c python3 scripts/tests/mutation_battery_resume_state.py`
  under `PYTHONDONTWRITEBYTECODE=1`, and grep the output for `NOT APPLIED` before reading any
  `killed` count.
- ✅ **CLOSED at audit round 2 — do not re-run it as an open question.** Probe executed as
  written: `NOT APPLIED` count **0**, control **187 passed / 0 failed** (the abort guard
  did not fire), **73/73 killed, survived: none** — the table has since grown by four rows
  (X14–X17). The ANCHOR risk this bullet named is no longer carried by a hand-run: it is a
  collected test (`test_mutation_battery_anchors.py`), so a 0x/2x anchor now fails the gate.

### Tekton reds that attribute ELSEWHERE — four distinct tests in one session
- **Symptom + exact repro:** no repro; a required check goes red on a test in a file the PR
  does not touch. A Tekton status is NOT re-runnable, so each occurrence costs a fresh push.
- **Observed (with values):** `test_a_FORGED_actor_in_the_body_is_DISCARDED` (4 PRs in one
  window, one of them a single markdown file) · `TestAHungRoundTripSAYSWhichSideBlocked.
  test_a_stall_in_the_FSYNC_region_is_NAMED` (#1197 and #1169, same window) ·
  `test_no_test_writes_a_usr_bin_env_shebang_at_runtime` (#1194) ·
  `test_release_deletes_the_ref_and_the_slug_becomes_claimable_again` (#1166). The first two
  live in `scripts/tests/test_subsystem_store_api.py`, which `devrc/tests.md` already carries
  an `OPEN:` bullet about.
- **Ruled out — with the control, not by assertion:** *a defect in the PR* — for #1166 the
  whole `test_claim_work.py` suite is **114 passed on a pristine `git archive` of
  `origin/main`**, and that test uses a `_bare_origin(tmp_path)` fixture, so it touches no
  real remote and no live claim. For #1197 the same tier on the same tree passed locally with
  an identical `collected=` count, and a second PR failed the identical test in the window.
  via: measurement
- **Leading hypothesis:** load/concurrency in the CI tier, not the changes.
- **Next probe:** capture `kubectl -n tekton-ci logs <run>-gate` while the run still exists —
  Tekton retains ~14 pipelineruns and the GitHub status is truncated at 140 chars with no
  `target_url`, which is why none of these four has a preserved log.

## Next steps (ranked)
1. **Deploy — `scripts/ship.sh`** (converges both hosts: fetch → `merge --ff-only` →
   `home-manager switch` → verify). 🔴 **Read every per-host line, not the final verdict** —
   one skip hides among greens. ⚠ At close-out the base clone was **1 behind and dirty with
   ANOTHER session's files** (`nix/programs/alacritty/default.nix`,
   `nix/system/apply-tmp-churn-retention.sh`); `ship.sh` skips a host it cannot fast-forward
   and leaves it exactly as found, so check that first rather than assuming a clean run.
   forcing: gate — the resolver half goes live on the pull while the skill half waits for the
   switch, so a partial deploy is the one state that actively misleads an agent.
2. **File the CI-flake issue** — four distinct tests, four PRs, one session, each needing a
   fresh push. The Open-investigations block above has the evidence and the next probe.
   A permanently-flaky required check trains everyone to click through red.
   forcing: none
3. **Close devrc#1160** — four `status`→code associations `claude/skills/handoff/SKILL.md`
   documents in prose that nothing pins, plus a stale `MIN_TESTS` ledger comment. Note the
   headroom moved: **#1144 merged (`3d0b77e5`) and raised `MAX_BYTES` to 27,000**, so the
   budget is 26,100 and that file is 25,864 → **236 B**, not the 7 B an earlier note claimed.
   forcing: none
4. **Apply the staged dnsmasq fix** — `sudo ~/workspace/devrc/nix/system/apply-dnsmasq-docker-io-pin.sh`.
   Only the operator can run it.
   forcing: incident — measured 2026-08-29: the LAN router pins `registry-1.docker.io` with a
   487-day TTL and two of those IPs were reassigned to other AWS customers, so every
   `docker build` fails TLS.

## Gotchas / decisions / dead-ends
- 🔴 **`paste -sd' or '` DOES NOT JOIN WITH " or " — `-d` is a LIST OF CHARACTERS it cycles
  through**, so a 13-name join came out spliced with stray `o` and `r` and pytest rejected the
  `-k` expression. It failed loudly here; the same idiom silently produces a WRONG filter when
  the delimiters happen to be valid syntax. Use `awk 'NR==1{printf "%s",$0;next}{printf " or %s",$0}'`.
- 🔴 **A `-k` PATTERN THAT DOES NOT MATCH SILENTLY EXCLUDES THE TEST YOU MOST WANT RED.** My
  first red/green run used `-k 'named_missing_reconciles_NONE'`; the real name is
  `..._named_missing_handoff_reconciles_NONE_of_the_docs_present`, so the killing test was
  never selected and the run reported 1 red where the truth was 12. **Generate the selector
  from the actual `def test_` names in the diff — never type it from memory.**
- 🔴 **A REPRODUCTION CAN STOP REPRODUCING FOR A REASON UNRELATED TO THE FIX.** The original
  failing path (`devrc/claudedocs/handoff-handoff-doc-stale-base-guard.md`) now EXISTS,
  because #1146 merged and put that doc on `main` — so probing it proves nothing about
  #1164 either way. Check the fixture's premise still holds before reading the result; I
  had to go find a doc that lives in exactly one worktree to get a valid live test.
- **`cp -a` of a WORKTREE carries its `.git` POINTER FILE** — `rm -f <copy>/.git` immediately,
  and assert it is gone, before running anything git-shaped inside the copy.
- **`git worktree list` from a LINKED worktree lists the whole clone**, which is what makes
  `worktrees_holding` work from either side.

- 🔴 **A MUTATION BATTERY REWRITES TRACKED SOURCE IN PLACE — reading that file while one
  runs tells you about a MUTANT, not about the commit.** This cost a false commit
  (`60c893b7`) asserting a fix had "shipped open". The tell that should have stopped it: the
  observed defect matched a NAMED MUTANT exactly. When a diagnosis lands precisely on a
  mutant the harness already defines, suspect the harness before the code. Corollary:
  `git status` clean is a fact about an INSTANT — worthless as evidence while any concurrent
  writer exists, including your own background job and a subagent you believe has finished.
- 🔴 **A COMMENT-ONLY COMMIT IS NOT AUTOMATICALLY HARMLESS.** Inserting a comment between
  `*)` and its command split the line a mutation anchor matched verbatim, so the row reported
  `PATTERN OCCURS 0x — NOT APPLIED` and stopped testing the very hole the branch closes.
  Before reformatting ANY line, ask whether a harness anchors on it — `git grep` the line's
  text in `scripts/tests/mutation_battery_*` and `mutants-*.sh`. Put prose ABOVE the
  construct, never inside it.
- 🔴 **A SUBAGENT CONTRADICTING YOU MAY BE RIGHT, AND FALSIFIABLE EVIDENCE IS WHAT SETTLES
  IT.** The fix agent's report was accurate and my override of it was wrong; what resolved it
  was a one-command check (`git diff <a> <b> -- <file>` is comment-only) and an isolated
  `git archive` extraction, not seniority. Ask for the check that would distinguish the two
  claims, then run it yourself.
- 🔴 **A FIX CAN CONVERT A FLAGGED FAILURE INTO A SILENT ONE, WHICH IS STRICTLY WORSE.** F1
  is the worked example: pre-fix the foreign relative token produced no answer AND a gap;
  post-fix it produced a confident wrong answer and NO gap. When widening a resolver, ask
  what it now answers that it previously declined — the regression is invisible to any test
  that only checks "does it resolve".
- 🔴 **`git diff origin/main..HEAD` IS NOT "what my branch changed"** when the branch is
  BEHIND. It is a tree-to-tree difference, so main's commits appear as differences too — it
  showed 50 files for a 5-file PR. Use `$(git merge-base origin/main HEAD)..HEAD`, or the
  PR's own file list; they agreed at 5.
- 🔴 **A number quoted without its SCOPE is the session's most repeated error.** The PR body
  said "12 RED"; the whole-file figure is 15 (10 new + all 5 rewritten). The 12 was a
  `-k`-scoped selection. Third occurrence in one session — state the scope or state nothing.
- 🔴 **The `git commit` PreToolUse guard judges the CALLER's cwd when it cannot resolve a
  `-C` path, and it runs BEFORE the command** — so a directory the same command creates does
  not exist yet, and a `$VAR` it cannot expand reads as your own repo. Build fixture repos
  with `git init -b fixture` in an EARLIER call than the one that commits, and pass literal
  absolute paths.
- **The doc-path gate polices `claude/skills/**` prose**: `test_doc_path_rot.py` rejected a
  SKILL.md example written as `claudedocs/handoff-x.md`; write `claudedocs/handoff-<topic>.md`.
- ⚠ **The locale test's non-vacuity depends on `LOCALE_ARCHIVE` reaching the gating tier.**
  `flake.nix` exports it in the devShell and in `checks.pytests`, and the test hard-fails
  rather than skipping — but it has only been RUN on the dev host so far.

- 🔴 **PART 2 IS SCOPED TO `named_missing`, NEVER TO `unresolved` — do not "simplify" that.**
  Only a handoff-SHAPED path (dir ends `/claudedocs`, basename matches `handoff-*.md` or
  `*HANDOFF*.md`) sets `named_missing` and therefore suppresses the fallback chain. A bare
  basename such as `handoff-alpha-2026-01-01.md` is a SLUG, and `scripts/resume-state.sh`
  records a MEASURED case where the fallback correctly served exactly that doc — widening the
  suppression to `unresolved` would break it. Guarded by
  `test_a_bare_BASENAME_slug_STILL_falls_back_and_resolves` and
  `test_the_civitai_slug_STILL_falls_back_and_resolves`; mutant **W10** (widen the gate to
  any supplied argument) is killed by **47** tests including both. *(This read `X10`, which
  is a different row — the `LC_ALL=C` locale pin. The battery's ids are not sequential across
  families; quote the id from the table, not from memory.)* *(And the count read **42**: the
  commit that corrected `X10`→`W10` carried the old number through, in the same edit that
  deleted an unenforced count on the grounds that a number nothing enforces is one edit from
  being wrong. 47 is MEASURED here — `W10 … f=47` in a full battery run on the round-3 tree,
  CONTROL 190 passed / 0 failed, 0 NOT APPLIED; audit round 3 separately reports the same 47
  at `7285291b` and `cf1b6f81`, which is its measurement and not mine. It is still enforced
  by nothing, so re-run the battery rather than quoting this line.)*
  *(Carried forward by hand: the merge tool warned this line was being dropped from a REPLACE
  section, and it was right — the reason had vanished from the doc while the code kept the
  behaviour.)*

- 🔴 **THE LADDER CLOSED ON A DISTINCTION WORTH REUSING: a defect that ITERATES toward zero
  versus one that REGENERATES by construction.** Rounds 1-3 chased "prose claiming behaviour
  wider than the code provides" and each fix contained the next instance; round 4 swept it and
  could not find it at a new site. What recurred instead was *a commit that corrects a status
  sentence writes a new status sentence its own landing falsifies* — a FIXED POINT. Round 5
  would have done it again. The exit was to change the doc's CONVENTION (never assert commit
  status in prose; point at `git log`), which is a one-line edit, not an audit round. A
  findings-keyed stop rule cannot see that difference; name it explicitly.
- 🔴 **A number written by the commit that CHANGES what it counts is stale on arrival.**
  `107 of 187` was added by the commit that added 3 tests (truth: 110 of 190) — in the same
  commit that was fixing exactly that error one file over. The argument needed "most of the
  suite", not a count. If nothing enforces a number, prefer the invariant to the figure.
- 🔴 **`E ` lines carry the rewritten EXPRESSION REPR, not just the assertion message.** So an
  attribution phrase must be absent from anything the script can PRINT AT RUNTIME, not merely
  from the suite source — otherwise a failing digest comparison echoes it and attributes the
  kill to the wrong row.
- **A subagent contradicting you may be right — three times this session it was**, and each
  time what settled it was a cheap falsifiable check (a comment-only `git diff`, a pristine
  `git archive`, nine files read one by one), never seniority. Ask for the check that
  distinguishes the two claims, then run it yourself.

## How to verify
```bash
# 1. it landed, by CONTENT (a squash is never an ancestor)
git -C ~/workspace/devrc fetch origin main
git -C ~/workspace/devrc show origin/main:scripts/resume-state.sh | grep -c worktrees_holding   # 6
gh pr view 1197 --repo innovation-upstream/devrc --json state,mergeCommit --jq '.state, .mergeCommit.oid'

# 2. WHICH HALF IS LIVE ON THIS HOST — readlink is the only arbiter
readlink -f ~/workspace/devrc/scripts/resume-state.sh          # itself  => live on pull
readlink -f ~/.claude/skills/resume/SKILL.md                   # /nix/store/… => needs a switch

# 3. the behaviour, end to end: a doc that exists in exactly ONE linked worktree,
#    named by the BASE CLONE's path, must resolve and re-anchor `# repo:` to that worktree
bash ~/workspace/devrc/scripts/resume-state.sh \
  ~/workspace/devrc/claudedocs/handoff-<one-that-lives-only-in-a-worktree>.md | grep -E '^# repo:|^  handoff:'

# 4. the guards still re-derive
PYTHONDONTWRITEBYTECODE=1 nix develop ~/workspace/devrc -c \
  python3 ~/workspace/devrc/scripts/tests/mutation_battery_resume_state.py   # 73/73, 0 NOT APPLIED
nix develop ~/workspace/devrc -c python3 -m pytest \
  ~/workspace/devrc/scripts/tests/test_mutation_battery_anchors.py -q        # every anchor 1x
```
