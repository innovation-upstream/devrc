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
- 🔴 **PR #1197 is OPEN** — `fix/resume-state-worktree-resolution`, head `cf1b6f81`,
  closing **#1164**. Branched off `d86e5f81`. Not merged. *(This line said `6f4d748b`,
  which is two commits back — `6f4d748b` was the head when the sentence was written and
  `f8309584`/`cf1b6f81` landed after it. A recorded sha ages; re-read
  `git rev-parse HEAD` rather than this line.)* **Audit round 2's fixes ARE COMMITTED, as
  `7285291b` on top of `cf1b6f81`.** *(This read "in the working tree, uncommitted" — a state
  its own commit falsified, and the sha above already carries a self-disclaimer this sentence
  did not. Round 3's fixes sit on top of `7285291b`; corrected by hand, because
  `handoff_doc.py` cannot make a factual correction inside a REPLACE section.)*
- 🔴 **I MISDIAGNOSED A MUTANT AS SHIPPED CODE, committed the false claim as
  `60c893b7`, and retracted it in `6f4d748b`.** I ran the mutation battery — which
  rewrites `scripts/resume-state.sh` IN PLACE, once per mutant — in this worktree while a
  subagent was still active in it, then `sed`-read the file and attributed what I saw to the
  commit. What I read was mutant X1 mid-flight, whose definition is exactly
  `[ -n "$mine" ] && ` removed from that re-anchor: the "diagnosis" matched a known mutant
  because it WAS the mutant. `git status` had been checked clean three tool calls earlier —
  **a cleanliness check is a fact about an INSTANT, not a property of a run.** PROVEN
  WRONG by `git archive`-ing `d756a1f8` into an isolated dir: 180 passed, both tests green,
  condition already gated.
- 🔴 **AND `60c893b7` DID REAL HARM — the harm I did NOT announce.** It was comment-only,
  so I called it harmless. Splitting `*) if …; then` across two lines to insert that comment
  made X1's mutation anchor match **0x**; the next run reported
  `X1 … !! PATTERN OCCURS 0x — NOT APPLIED`, i.e. the row guarding the exact hole this
  branch closes silently stopped testing anything. **That is #1115.1's defect class,
  reproduced by my own hand in a different harness eleven commits later.** `6f4d748b`
  restores the line byte-exact and puts the warning ABOVE the `case`.
- **The subagent's pushback was right and mine was wrong.** It produced falsifiable evidence
  (the diff is comment-only; here is the byte-identical condition), which is what settled
  it — not authority in either direction.
- **Audit round 1 returned SEVEN findings (5 payload); all seven are fixed in `d756a1f8`.**
  A delta re-audit of `3e42bb04..d756a1f8` is still owed.
- 🔴 **F1 WAS OURS, AND IT WAS THE HARM #1164 EXISTS TO REMOVE.** The relative branch
  called `worktrees_holding "$root" "$base"` with `$root` = the CWD's repo, so a relative
  token naming a FOREIGN tree was served out of the standing clone's worktrees — **silently,
  with no gap**. That converted a *flagged* miss into a *silent wrong-clone resolution*.
  REPRODUCED AND RE-VERIFIED BY ME on a fixture at `/tmp/f1v` (`devrc` + linked worktree
  `devrc-topic` holding the doc, plus a sibling `other-repo` that does not):
  at `3e42bb04` → resolves, `# repo: …/devrc-topic`, gaps EMPTY; at `d756a1f8` →
  `(none found — git-only)` + `NO SUCH FILE`. The three legitimate shapes
  (repo-prefixed, bare `claudedocs/`, absolute) all still resolve — checked individually.
- 🔴 **The obvious fix does NOT work, and this is the reusable part.** "If `<Y>` resolves to
  a git repo, scope there" falls through, because in the reproduction `other-repo` is a
  SIBLING of the repo and does not resolve from the cwd at all. The shipped discriminator:
  `$root` is used only when `<Y>` is empty or `${ydir##*/}` == `${root##*/}` — #1159's
  kickoff shape. It also closes the PRE-EXISTING single-tree half of the same bug.
- **Deliberate narrowing, documented in the script and SKILL.md:** a relative token naming a
  SIBLING WORKTREE of the same clone (`devrc-topic/claudedocs/x.md` from inside `devrc`) now
  misses. Nothing separates it by name from a foreign `other-repo/…`, so the safe direction
  was taken; the absolute form works and the gap names what it could not find.
- **Other fixes in `d756a1f8`:** F2 the ambiguity gap now says "of the clone that path
  resolves against" (verified NOT already true after F1 — a bare `claudedocs/<base>` names
  no clone at all); F3 `SKILL.md:20`'s "never the one you are standing in" corrected per
  input class; F4 `LC_ALL=C sort -u` plus a human-named-before-`agent-*` display preference;
  F5 tokens containing `* ? [` are dropped from `named_missing`; F7 a vacuous assertion
  replaced with the whole-`out` form.
- 🔴 **F7's "proven reachable under a mutant" WAS AN UNBACKED CLAIM WHEN IT WAS WRITTEN,
  and is backed now.** Round 2's auditor traced every battery row that reddens
  `test_a_prose_path_that_does_NOT_exist_is_not_taken` and found the `handoff_line(out)`
  equality on the line ABOVE fires first in each — W9, for one: the fallback runs, so the
  `handoff:` line names the doc — so no row demonstrated the whole-`out` line was ever the
  killer. Closed by new mutant **X17**, the first TWO-SITE row in this battery: the
  fallback runs for a NAMED path (body) AND the `handoff:` printer still emits
  `(none found — git-only)` (header). MEASURED under it: the equality PASSES and the
  failure is `assert 'SESSION-HANDOFF.md' not in '## resume-s…'`, i.e. line 649 itself.
  **A claim that an assertion is reachable is a mutation result; write it after you have
  the killer, not after you have the intent.**
- 🔴 **THE MUTATION SWEEP FOUND A REAL SURVIVOR — X1** — dropping the gate from the
  *re-anchor* while keeping it on the *worktree search*. Every foreign-token fixture put the
  doc in a WORKTREE, so the plain re-anchor had nothing to find and no test could see it.
  Closed by a new test whose doc is in the BASE CLONE with no worktree; confirmed to kill X1
  and only X1 (178 others green under it), and red at `3e42bb04`. **Ask what your fixture
  omits that every real instance has.**
- **F4's premise needed correcting, and was.** Under `LC_ALL=C` the human-named worktree
  sorts FIRST, so the locale pin alone fixes the measured instance; the display preference
  remains because C order still puts `<repo>/.claude/…` above a sibling sorting after
  `<repo>/`.
- **Battery, re-run BY ME on a QUIESCENT worktree (the condition that was missing before):
  69/69 killed, 0 NOT APPLIED, survived: none**, X1 killed by its named test. This confirms
  the fix round's original 69/69, which I had wrongly disputed.
- **Suite: 180 passed.** `bash -n` clean. Behaviour at `6f4d748b` is identical to
  `d756a1f8` — the diff between them is comment-only, checked mechanically.

### Audit round 2 (committed as `7285291b`, on top of `cf1b6f81`) — 6 findings, all closed
- 🔴 **🟡-1 the deploy claim was WRONG in the direction that inverts the fix** — see Next
  steps 2. `scripts/resume-state.sh` is a plain working-tree file, `SKILL.md` is nix-managed.
- 🔴 **🟡-2 the DO-NOT-REFORMAT comment is no longer the guard.** New COLLECTED test
  `scripts/tests/test_mutation_battery_anchors.py` asserts every `MUTANTS` row's `old`
  pattern occurs EXACTLY ONCE in the script it targets, for BOTH Python batteries, reading
  their tables by import so a new row is covered automatically. It carries its own two-way
  ledger of batteries, a 0x/2x negative control and a non-empty positive control.
  **NEGATIVE CONTROL AGAINST THE REAL DEFECT:** run against
  `git show 60c893b7:scripts/resume-state.sh` it reports exactly one offender —
  `X1: occurs 0x` — and 0 offenders at `cf1b6f81`/HEAD. The source comment is kept but
  shortened; it now points at the test rather than being the mechanism.
- 🔴 **🟡-3 SKILL.md over-claimed and now states the residual.** "Anything else relative is
  neither searched nor re-anchored" is stronger than a LAST-COMPONENT comparison:
  `backup/devrc/claudedocs/<base>` and `../elsewhere/devrc/claudedocs/<base>` re-anchor and
  resolve THIS repo's copy with no gap. **The discriminator was NOT widened** — no cheap
  correct one exists that keeps #1159's kickoff shape working (`-d` is ruled out by the
  foreign-SIBLING shape). Instead: corrected in SKILL.md, stated in the source comment, and
  PINNED by `test_a_FOREIGN_tree_whose_LAST_COMPONENT_matches_this_repo_STILL_re_anchors`
  (killed by X6 and X5) plus `test_the_residual_is_BOUNDED_by_the_absolute_and_case_rules`
  (killed by X3). Invariant guards, not regression tests — said so in the module.
- **🟢-4 `<repo>//claudedocs/<base>` now resolves.** `$ydir`'s trailing slashes are stripped
  before the name compare; it cannot widen the discriminator (stripping `/` can only expose
  a component that was already there). New mutant **X16**; RED before the strip with the
  measured symptom (`NO SUCH FILE`), plus a control that `other-repo//claudedocs/…` still
  misses.
- **🟢-7 the classification PATTERN is now varied.** New mutants **X14** (`*/.claude/*`) and
  **X15** (`*/agent-*`) — MEASURED SURVIVING all 186 pre-existing tests, exactly as the
  auditor predicted. Killed by the one new fixture
  `test_the_EPHEMERAL_classification_needs_the_WHOLE_agent_worktree_PATH`, which holds a
  human worktree under `.claude/` and one with an `agent-…` component outside it.
- **Round-2 numbers: battery 73/73 killed, 0 NOT APPLIED, survived: none**, control
  187 passed / 0 failed (it ABORTS on a red baseline; it did not). Resolution suite 187
  passed. `bash -n` clean. Run on a QUIESCENT worktree; `scripts/resume-state.sh` verified
  byte-identical to its pre-battery snapshot afterwards (`cmp`).
- **Tiers at `60c893b7`** (comment-only different from HEAD): `pytests` PASS
  collected=20105 passed=20102 skipped=3 failed=0 (floor 18383) · `nodetests` PASS
  suites=5 files=41 tests=1449 pass=1449 fail=0.
- 🔴 **MERGED-TREE gate IN FLIGHT** — `devrc-merged` worktree, `origin/main` `76bb7507`
  merged with the branch, **0 conflicts**, merged head `9bd136a2`. Nobody had run either
  tier on a merged tree before this; `strict: false` means a green branch check is a claim
  about the BRANCH.
- 🔴 **Tekton pytests went RED on this PR and it ATTRIBUTES ELSEWHERE — measured three
  ways.** Failing test `TestAHungRoundTripSAYSWhichSideBlocked.test_a_stall_in_the_FSYNC_
  region_is_NAMED` lives in `scripts/tests/test_subsystem_store_api.py`, which this PR does
  NOT touch (5-file diff, cross-checked against GitHub's own file list); **#1169 failed the
  identical test in the same window**; and my local run of the same tier on the same tree
  passed with the identical `collected=20090`. Same file as the `FORGED_actor` flake that
  red-lit four PRs earlier the same day, and as `devrc/tests.md`'s open bullet. A Tekton
  status is not re-runnable — the fix round's push re-triggers it.
- **Claim held:** `resume-state-worktree-resolution`. Release when the PR merges.

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

## Next steps (ranked)
1. **Commit ROUND 3's working-tree fixes, re-run BOTH tiers on the branch AND on a fresh
   merged tree, then delta re-audit ROUND 3's diff (`7285291b..HEAD`), then merge.**
   *(This step used to name round 2 and `cf1b6f81..HEAD`. Round 2 was committed as
   `7285291b` and audited: round 3 returned 7 findings — 4 of them prose over-claims, which
   is this branch's recurring defect — and all 7 were fixed. Those fixes are in the working
   tree, UNCOMMITTED, on top of `7285291b`; nothing below has been re-measured against a
   commit yet. ⚠ **One round-3 finding was itself partly wrong, and the fix says so rather
   than encoding it:** F3 asserted `mutants-dead-guard-exclude.sh` and `mutants-install-sh.sh`
   carry no did-not-apply control "in any spelling". Both do — `mutation did not apply
   uniquely` and `🔴 NOT-APPLIED` respectively, verified file by file — so what was actually
   broken was only the enumerator command, and writing the claimed exception into the
   docstring would have shipped a new false sentence inside the fix for a false sentence.)*
   The ladder CONTINUES — an audit fix resets the verification gate, and the
   delta to audit is round 3's own, not `cf1b6f81..7285291b`. Stop on the first round that
   returns no finding.
   ⚠ The merged-tree result recorded below was measured at `9bd136a2` and is now stale:
   rounds 2 and 3 touched 6 files (measured, `cf1b6f81..HEAD` plus the working tree) and this
   worktree's `origin/main` ref already reads `836bac03`, not `07a22f14` — **re-fetch rather
   than trusting either sha; both age.**
   forcing: gate — both required checks block the merge with `enforce_admins: true`.
2. 🔴 **After merge run `scripts/ship.sh` — the two halves of this change deploy
   DIFFERENTLY, and doing only one INVERTS the fix.** This doc previously said both are
   nix-managed. **`scripts/resume-state.sh` is not.** MEASURED 2026-09-01 with the arbiter
   `claude/RULES.md` names:
   ```
   readlink -f scripts/resume-state.sh          -> itself      (working tree = LIVE)
   readlink -f ~/.claude/skills/resume/SKILL.md -> /nix/store/…-devrc-claude-skills/…
   command grep -rn resume-state nix/           -> 0 matches
   ```
   `/resume` invokes `bash ~/workspace/devrc/scripts/resume-state.sh` — the working-tree
   file — so a plain `git pull` makes the NARROWED resolver live immediately, while the
   deployed `SKILL.md` still says "searches the worktrees of the clone the path NAMED".
   New behaviour under old instructions is worse than either. `git pull` + `home-manager
   switch` on BOTH hosts, i.e. `scripts/ship.sh`.
   forcing: gate — a pull alone ships the new behaviour under the old instructions.
3. **Close devrc#1160** — four `status`→code associations `claude/skills/handoff/SKILL.md`
   documents in prose that nothing pins, plus a stale `MIN_TESTS` ledger comment. The
   byte budget MOVED and this doc had the old numbers: **#1144 merged (squash `3d0b77e5`)
   and raised `MAX_BYTES` to 27,000**, so on `origin/main` the enforced budget is
   `27,000 − 900 = 26,100` against a 25,864 B file — **236 B**, not 7 B against 25,500
   (those are this BRANCH's stale values: `MAX_BYTES = 26,400`, file 25,496). Re-derive
   from `scripts/tests/test_handoff_skill_size.py` on the tree you are editing, never from
   this line; it is still a byte-budget decision, with more room than it looked.
   forcing: none
4. **Apply the staged dnsmasq fix** — `sudo ~/workspace/devrc/nix/system/apply-dnsmasq-docker-io-pin.sh`.
   Only the operator can run it.
   forcing: incident — measured 2026-08-29: the LAN router pins `registry-1.docker.io` with a
   487-day TTL and two of those IPs were reassigned to other AWS customers, so every
   `docker build` fails TLS. Worked around once with `--add-host`; unfixed.

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

## How to verify
```bash
# 1. F1, on a fixture where the doc exists ONLY in a linked worktree and a sibling repo
#    does not hold it. The FOREIGN relative token must MISS; the other three must resolve.
cd /tmp/f1v/devrc   # build per the Gotchas note: `git init -b fixture`, commit in a LATER call
for t in other-repo/claudedocs/handoff-only-in-worktree.md \
         devrc/claudedocs/handoff-only-in-worktree.md \
         claudedocs/handoff-only-in-worktree.md \
         /tmp/f1v/devrc/claudedocs/handoff-only-in-worktree.md; do
  bash ~/workspace/devrc-rsw/scripts/resume-state.sh "$t" | grep -E '^# repo:|^  handoff:'
done

# 2. the battery — grep NOT APPLIED BEFORE reading any kill count
PYTHONDONTWRITEBYTECODE=1 nix develop ~/workspace/devrc -c \
  python3 ~/workspace/devrc-rsw/scripts/tests/mutation_battery_resume_state.py

# 3. BOTH sandbox tiers, ONE AT A TIME (#1088), on the branch AND on the merged tree
nix build ~/workspace/devrc-rsw#checks.x86_64-linux.pytests   --no-link --print-build-logs
nix build ~/workspace/devrc-rsw#checks.x86_64-linux.nodetests --no-link --print-build-logs

# 4. the PR's real file list (NOT `git diff origin/main..HEAD`)
git -C ~/workspace/devrc-rsw diff --name-only \
  $(git -C ~/workspace/devrc-rsw merge-base origin/main HEAD)..HEAD
gh pr view 1197 --repo innovation-upstream/devrc --json files --jq '.files[].path'
```
