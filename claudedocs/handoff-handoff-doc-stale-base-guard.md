# Handoff: handoff-doc-stale-base-guard — 2026-08-30

## Run this first — the index, one read-only command
```bash
python3 ~/workspace/devrc/scripts/lib/subsystem_recall.py --repo ~/workspace/devrc
```
Terse pointers this doc does not carry, curated by past sessions and outliving it.
🔴 RECALL, NOT LIVE OBSERVATION — every line is a pointer to VERIFY, never a current
reading, and it may describe a gotcha already fixed. `scope-absent`/`scope-empty` means
nothing is recorded yet: ordinary, not an error, and not a clean bill of health.
Non-blocking: if it exits non-zero, print the stderr line and carry on.

🔴 **No `clawgate-task:` field.** `clawgate_handoff.sh resolve` exited **5** — 0 tasks for
this session — with its positive control confirming the board answered 2 links for another
session. A wrong session id ALSO answers `200` with an empty array, so this is a real reading
of the board and **not** proof the id is right. No task was created.

## Goal
`handoff_doc.py` DETECTED the case where the doc is absent locally but present on the
mainline — "every section will merge as NEW and the committed document will be replaced by
this delta" — and exited **0**. That was survivable while a human answered a y/N; the prompt
was retired 2026-08-23, leaving the warning as the only thing between that diff and a pushed
commit. Turn it into a refusal.

## State now
- **devrc#1046 MERGED** — squash `e9437342`, verified by CONTENT on `origin/main` (a squash
  never makes the head an ancestor).
- 🔴 **RANK 1 IS DONE AND #1146 IS MERGE-READY — it is NOT merged, and must be sequenced
  with #1144 first (see rank 2).** `fix/audit-ladder-scaffolding-gaps`, head `b8850d77`,
  8 commits, `mergeable=MERGEABLE mergeStateStatus=CLEAN`, `closes 1093,1115`.
- **Gate at `b8850d77`** — both sandbox tiers ONE AT A TIME (#1088), read from each runner's
  own `RESULT:` line: `pytests` PASS (collected=19534 passed=19532 skipped=2 failed=0,
  floor 18145) · `nodetests` PASS (suites=5 files=41 tests=1441 pass=1441 fail=0, floor 1367).
  **Both required Tekton checks are `success` against `b8850d77` ITSELF**, confirmed via
  `/commits/<sha>/status` rather than the PR-level rollup.
  `mutants-handoff-cap.sh`: **47 rows, 0 failures**, baseline clean (measured at `53991450`;
  the change since is comment-only, mechanically verified 0 non-comment lines).
- **The mutants live IN THE TREE** as new sections of `scripts/tests/mutants-handoff-cap.sh`,
  not a second harness — that file already owns the named-killer check, the collected-test
  floor, the DID-NOT-APPLY diff and the restore. Needed a third pristine copy
  (`$T/suite.orig`) because the round-4 negative control is the first row to mutate the SUITE.
- 🔴 **THE AUDIT LADDER IS CLOSED BY THE ATTRIBUTION GATE, not by a clean round.**
  round 1: 6 findings, fixes changed **2** payload lines · round 2: 6 findings, **0** ·
  round 3: 5 findings, **0**. Two consecutive zero-payload rounds ⇒ the ladder had left the
  PR and was auditing its own scaffolding. **No round 4 was run.** The remainder is filed as
  **devrc#1160** with a closing condition — which is exactly how #1093/#1115 were created.
- **Two of round 3's five findings were FIXED rather than filed**, deliberately:
  `claude/RULES.md` names *a comment whose falsity would lead a maintainer to delete the
  guard it described*. Round 2's comment claimed deleting `assert len(codes) == 9` would
  still score `ok`; MEASURED, the row scores **WRONG-KILLER**.
- **Claim:** still held as `devrc-1093-1115-scaffolding`. **Release it when #1146 merges.**
- Earlier sessions: **ZacxDev/homelab-infra#460** (`26d98f1b`) and **#519** merged;
  **subsystem-store-api 0.6.0 DEPLOYED and VERIFIED** (pod `…-97gnp`, `_audit_lock` = 3,
  `/healthz` 200, 0 restarts).

## Open investigations — live diagnosis state

### `test_live_cotenants_sees_another_process_in_the_repo` fails intermittently under full-suite load
- **Symptom + exact repro:** no reliable repro. `scripts/tests/test_git_repo_isolation.py`,
  assertion `assert live_cotenants([git_dir]) == []` fails with e.g. `['92981:git']` —
  "a brand-new tmp repo already has tenants?"
- **Observed (with values):** failed on 2 of 3 full sandbox runs on trees containing the
  branch; **passed on a re-run of the IDENTICAL tree at essentially the same load**
  (31.83 vs 32.89). 0 of 2 on `main`-only runs. Passes in isolation. Running the PR's
  128 `git`-spawning test calls alongside it at `-n 4`: **3/3 green**.
- **Ruled out:** *caused by this PR* — it touches 3 files, none in `testlib/gitenv.py` or
  that test; `_mkrepo` uses `subprocess.run`, which waits, so the "unreaped own `git init`"
  theory is dead. *Load alone* — it passes at equal load.
- **Leading hypothesis:** a race in the co-tenant probe itself; `live_cotenants` excludes own
  lineage and sibling xdist workers, then requires a foreign process's `cwd` to resolve INSIDE
  that tmp repo.
- **Next probe:** run `test_git_repo_isolation.py` alone in a loop under induced CPU pressure
  (`systemd-run --user --scope -p CPUQuota=25%`) rather than more full-suite runs — repeats of
  an intermittent are not a control.

### Tekton reported ~45 non-passing at `0c3d30d0`; never explained
- **Observed (with values):** `tekton/devrc-pytests` = `failure`, `FAILING: TestSkillDocsArePinned.test_the_pinned_docs_are_the_DEPLOYED_ones | TOTAL collected=18574 passed=18529`.
  Locally that test passes and the whole pinning file is 875 passed unfiltered.
- **Ruled out:** *a real defect in the change* — at `c6f64e4b` Tekton's numbers were
  **byte-identical** to the local sandbox tier (`18578/18576/2/0`), and both required checks
  passed through to the merge.
- **Leading hypothesis:** transient. The same check posted `error — COULD NOT RUN` twice the
  same day.
- **Next probe:** none open. 🔴 The failing run's log was **garbage-collected** before it could
  be read — Tekton retains ~14 pipelineruns and none was that sha. If it recurs, capture
  `kubectl -n tekton-ci logs <run>-gate` while the run still exists; the GitHub status is
  truncated at 140 chars and carries no `target_url`.

## Next steps (ranked)
1. **Merge devrc#1146, then `claim-work --release devrc-1093-1115-scaffolding`.** Everything
   is green; the only open question is ordering against #1144 (rank 2). Nothing else is owed.
   forcing: gate — both required checks are green on the head sha now, and `strict: false`
   means that claim ages the moment `main` moves.
2. 🔴 **Sequence #1146 against devrc#1144 before merging EITHER.** #1144
   (`feat/handoff-elimination-evidence`, head `a960d43a`) adds `EXIT_UNEVIDENCED = 10`;
   #1146 asserts `len(codes) == 9`. Whichever merges second fails a required check until the
   count is updated — **by design**, and it now reports a proper collision message rather
   than a bare `assert 10 == 9`. The byte-ceiling half of this warning is **RETRACTED**:
   27,510 B was the CONFLICTED file. Union resolution is 25,861 B against a merged budget of
   26,100 (#1144 raises `MAX_BYTES` to 27,000) — **+239 B, no eviction needed**.
   ⚠ #1144 is another session's live claim (`linux-cpu-profiling`) — coordinate, do not edit it.
   forcing: gate — a required check fails for whoever merges second.
3. **Apply the staged dnsmasq fix** — `sudo ~/workspace/devrc/nix/system/apply-dnsmasq-docker-io-pin.sh`.
   Only the operator can run it.
   forcing: incident — measured 2026-08-29: the LAN router pins `registry-1.docker.io` with a
   487-day TTL and two of those IPs were reassigned to other AWS customers, so every
   `docker build` fails TLS. Worked around once with `--add-host`; unfixed.
4. **File the `resume-state.sh` sibling-worktree gap.** Given an explicit path to a handoff
   living in a LINKED WORKTREE, the run reports NO SUCH FILE and falls back to newest-of-N,
   producing a digest for a different initiative. **MEASURED against devrc#1159's fix
   (`dae5ac23`), which does NOT close it** — that fix re-anchors on the repo ROOT, and a
   sibling worktree is a different root. Closing condition: `resume-state.sh <path-in-a-
   linked-worktree>` resolves that doc instead of falling back, checked by a merged PR.
   forcing: none
5. **Fix `subsystem-audit.py`'s `EVICTABLE` classifier** — it verifies the target EXISTS and
   labels that "its content has a home". Measured on both of `devrc/tests.md`'s evictable
   bullets: one's commit carried none of the 3 later additions; the other's named sha carried
   **0 of 9** markers. 8 more so-labelled bullets store-wide.
   forcing: none
6. **Resume `claudedocs/handoff-subsystem-index-per-host.md` ranks 3, 5, 6, 7** — including
   filing the `FORGED_actor` flake, which is no longer hypothetical: it red-lit `afe8e190`
   and three other PRs in one window, one of them a single-markdown-file PR.
   forcing: none

## Gotchas / decisions / dead-ends
- 🔴 **`strict: false` means a green branch check is a claim about the BRANCH.** Eight audit
  rounds on #1046 could not see two defects that only appeared when `main` moved: #962 claimed
  `EXIT_DOC_PER_EFFORT = 7` against my `EXIT_STALE_BASE = 7` (the obvious "keep both sides"
  resolution ships two constants equal to 7 — the doc/code guard scrapes `status=` tokens and
  the exit-code pin stopped at 6, so **nothing** caught it); and #962's rule (i) **shadowed**
  the new refusal with **no conflict marker at all**, because both PRs appended returns to one
  `main()`. Gate the MERGED tree, by hand, every time.
- 🔴 **A fixture that is unrealistically empty is the blind spot.** Every test in the new class
  built a repo with NO other handoff docs, which is the only reason rule (i) never fired in
  them. Real repos always have others — talos-infra has 100+.
- 🔴 **The audit ladder's attribution gate is real and it fired.** Rounds 6 and 7 both changed
  ZERO payload lines ⇒ stop, file the rest. Rounds 1–8 produced 26 findings; rounds 3–5 each
  found the previous fix was right about one member of an equivalence class and wrong about
  another, including a **fail-open** hole (`bool(sections)`) invisible to 190 green tests.
- 🔴 **`nix build --rebuild` is not a re-run.** It verifies reproducibility of a SUCCESSFUL
  build and errors `outputs are not valid` after a failed one — a confident non-zero that reads
  exactly like a test failure. Cost one false "third failure".
- 🔴 **Build the two check derivations ONE AT A TIME** (main's #1088) — together they produce
  false failures.
- **An auditor's stop recommendation is not the stop condition.** Round 4's advised ending the
  ladder because its findings were not *caused by* the previous fix; the rule is a round with
  **no findings**. Round 5 then found a fail-open hole. Round 4 also filed the empty-mainline
  bug as prose-only; it was a payload defect.
- **`audit-dispatch.py --round N` derives `<from>` from the last posted claims block.** After a
  ladder closes and reopens (a merge), it generates a range spanning the whole merge — 52 of
  someone else's commits. Scope the dispatch by hand.
- **The bash guard resolves the repo from the session CWD, not `git -C`.** In a multi-worktree
  session it blocked a commit as "on main" when the target worktree was on a feature branch.
  `git commit -F <file>` is the documented way through, and is preferred over heredocs anyway.
- `SKILL.md` now has **2 bytes** of headroom against the enforced 25,500. The next addition
  needs an eviction and there is essentially nothing left to give.

- 🔴 **A handoff doc committed only to a feature branch in a WORKTREE is unfindable from the
  base clone, and `/resume` does not fail loudly on that.** This doc lives at
  `~/workspace/devrc-scaffold/claudedocs/…`, not `~/workspace/devrc/claudedocs/…`. Given the
  base-clone path, `resume-state.sh` reported NO SUCH FILE and **fell back to the newest of 90
  docs** (`handoff-mention-detection.md`) — it prints that as a `!` gap and withdraws the DRIFT
  all-clear, but the digest it produced was about a different initiative entirely. **Kickoff
  blocks for worktree-resident docs must name the WORKTREE path.**
- 🔴 **`claim-work` ownership is per GIT-DIR, so the same session gets rc 10 from its own
  worktree and rc 12 from its base clone.** This item was claimed 5h earlier from
  `/home/zach/workspace/devrc/.git`; checking from `devrc-scaffold` (whose git-dir is
  `…/devrc/.git/worktrees/devrc-scaffold`) returns **rc 10 DO NOT START**, which reads as a
  peer holding it. That is the strict sibling-worktree predicate working as designed. Resolve
  it by matching the ref's `owner-id:` trailer against `owner_id_for()` over
  `<common-dir>` + `<common-dir>/worktrees/*` rather than by guessing — the ref's `clone-id:`
  confirms same-clone-same-host first.
- ⚠ **The same item was claimed under a REWORDED slug**, so the canonical
  `claim-work --slug-for <doc> 1` came back FREE. The exact-slug match is the hard lock; the
  `--list` subject column is the only thing that catches this, and only if you read it.
- 🔴 **`nix build` writes the build log to STDERR, not stdout.** `pytests.out` was 0 bytes
  throughout a healthy run — that is not a stalled build.

- 🔴 **A guard's ORDER decides which defect it names, and the wrong order recreates the very
  bug you are fixing.** `assert len(codes) == 9` placed above an injectivity loop turned a
  constant COLLISION into `assert 10 == 9`, whose obvious remedy is to bump the literal —
  after which the real defect appears only on a second run. That is #1093.1 ("the assertion
  that runs first misdirects") reintroduced in a sibling test, in the same PR that fixed it.
  **When two assertions can both fire, the one that names the more specific defect goes first.**
- 🔴 **A prose guard that asserts a NUMBER without its SUBJECT is walkable.** `f"({const})"`
  asks only whether a digit appears somewhere in a 25 KB file, and any other status spelling
  the same number satisfies it. MEASURED: the exact drift the test existed to prevent left
  all 297 tests green. Bind the token to the value — `` `status=new-doc` (7) `` — not the value alone.
- 🔴 **zsh ate `$H:scripts/...` as a history modifier** (`:s`), so `git show "$H:path"` failed
  with `bad substitution` and one probe silently reported 0 constants. **Brace it: `${H}:path`.**
- 🔴 **A `cmp -s`/COULD-NOT-STAGE guard is what makes a broken control visible.** The round-4
  negative control's first regex matched ZERO times — the assertion message is an
  implicit-concat tuple whose last line carries a trailing comma. The harness reported
  `COULD NOT STAGE — the row above is UNVALIDATED` instead of passing, which is exactly
  #1115.1's original finding recurring in new code.
- 🔴 **Never grep a whole pytest run for a message.** pytest prints the failing test's SOURCE
  as traceback context, so a test whose COMMENTS quote the old message matches a naive grep
  and reports a misdirect that never happened. Read `^E ` lines only.
- ⚠ **A test-merge probe leaves rerere PREIMAGES.** Three were recorded by the #1144 probe;
  each has `preimage` and no `postimage`, so there is no stored resolution to replay. Worth
  checking after any probe merge, given this repo's recorded rerere incident.
- **The base clone `~/workspace/devrc` is SHARED and moves under you** — it was on
  `feat/memory-detail-click` at session start and on `main` hours later, switched by another
  session. It was 0 ahead of `origin/main` throughout, so `ship.sh` was never blocked.

- 🔴 **"INVARIANT GUARD" IS A CLAIM ABOUT THE SUITE, NOT ABOUT THE HARNESS — and collapsing
  the two produced a FALSE comment in both directions across two rounds.** A mutant already
  killed at the merge-base means the row does not show the suite would otherwise miss it.
  It does NOT mean the new assertion is redundant: a harness that demands each row's own
  NAMED killer still binds it. MEASURED — delete `assert len(codes) == 9` and the row scores
  **WRONG-KILLER**, not `ok`. State both halves or state neither.
- 🔴 **The same defect class recurred FOUR times in one PR: a claim of coverage wider than
  the code provides.** As the original #1093/#1115 findings, then in the harness header, then
  in round 1's fix for that header, then in round 2's fix for THAT. Every round caught the
  previous round's version — which is the argument for keying the ladder to FINDINGS, never
  to a round count. The tell each time was a comment or docstring naming a RELATIONSHIP while
  the code inspected one side.
- 🔴 **The attribution gate is what ends a ladder that keeps finding real things.** Rounds 2
  and 3 both produced genuine findings AND changed zero payload lines. Findings-keyed stopping
  alone would have run forever; the gate is what says "these are findings about scaffolding
  the ladder itself wrote".
- 🔴 **A `git worktree`'s `FETCH_HEAD` is per-worktree.** `git -C <repo> fetch` then
  `git -C <worktree> merge FETCH_HEAD` fails `could not open … FETCH_HEAD`. Fetch into a
  namespaced remote-tracking ref and merge THAT. The failure is loud, but the numbers printed
  by the steps AFTER it describe an unmerged tree and read as a successful measurement.
- 🔴 **A PreToolUse hook blocks the WHOLE Bash call**, so a heredoc that writes a file in the
  same call as the blocked command never runs. `gh issue create --body-file $VAR` is refused
  twice over: the gate cannot expand `$VAR`, and the file it names was never created. Write
  the body with the Write tool, then pass a LITERAL path.
- 🔴 **`git merge-file` output is not a resolution.** Sizing a merged file from the conflicted
  artefact counts the markers and the duplicated region — it read 27,510 B where a union
  resolution is 25,861 B, a 1.6 KB overstatement that would have sent the next merger on an
  unnecessary prose eviction. Model it additively from the two diffs and say it is an estimate.
- ⚠ **Pyright noise in this repo is expected outside the devShell** — `Import "pytest" could
  not be resolved` and a handful of unused-arg nits are pre-existing, not your edit.

## How to verify
```bash
# 1. the PR is green ON ITS HEAD SHA, not on a stale one (the rollup can lag)
PH=$(gh pr view 1146 --repo innovation-upstream/devrc --json headRefOid --jq .headRefOid)
gh api "/repos/innovation-upstream/devrc/commits/${PH}/status" --jq '.state'   # success

# 2. BOTH sandbox tiers, ONE AT A TIME (#1088) — read each runner's own RESULT: line
nix build ~/workspace/devrc-scaffold#checks.x86_64-linux.pytests   --no-link --print-build-logs
nix build ~/workspace/devrc-scaffold#checks.x86_64-linux.nodetests --no-link --print-build-logs

# 3. every mutant #1093/#1115 recorded as surviving — IN THE TREE, no scratch file.
#    MUST run under the devShell: the harness calls bare `python3 -m pytest`, and
#    `.envrc` is `use opencode`, which does not provide it.
nix develop ~/workspace/devrc -c bash \
  ~/workspace/devrc-scaffold/scripts/tests/mutants-handoff-cap.sh   # 47 rows, 0 failures

# 4. the comment that was false is now true: deleting the pin scores WRONG-KILLER, not ok
#    (delete `assert len(codes) == 9`, apply s|^EXIT_BEHIND = 6$|EXIT_BEHIND = "6"|,
#     run test_handoff_doc.py — test_no_two_exit_constants_share_a_value must NOT be a killer)

# 5. SKILL.md is under its enforced ceiling (26,400 - 900 = 25,500)
wc -c ~/workspace/devrc-scaffold/claude/skills/handoff/SKILL.md     # 25,493
```
