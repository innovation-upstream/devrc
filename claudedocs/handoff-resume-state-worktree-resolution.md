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
🔴 **BOTH EFFORTS ARE MERGED AND CLOSED. The only thing left is the DEPLOY, which has not
happened.**

- **devrc#1197** → squash `6421df3c`, closing **#1164** (`CLOSED`). The resolver fix.
- **devrc#1146** → squash `edbc596f`, closing **#1093** and **#1115** (both `CLOSED`).
- **devrc#1166** → squash `e2a9f781`. Carried both efforts' close-out docs.
- All verified by CONTENT on `origin/main`, never by ancestry — a squash makes the branch head
  permanently NOT an ancestor, so `merge-base --is-ancestor` says "not merged" forever.
- **Both claims RELEASED** (`devrc-1093-1115-scaffolding`, `resume-state-worktree-resolution`).
- **Subsystem index written for BOTH efforts** and validated (`devrc/tests.md`; the effort-2
  entry was initially MISSED and added on a completion audit — see the gotcha below).
- **Session artifacts cleaned**: three worktrees (`devrc-rsw`, `devrc-merged`,
  `devrc-scaffold`) removed, `/tmp` fixtures removed, 0 of mine remaining.

✅ **DEPLOYED to both hosts 2026-09-02.** `ship.sh` converged workbench + laptop to
`aa1c2abf` (workbench `1cc720ba`→, laptop `26e16eca`→ — the laptop was 4 commits further
behind), 0 dangling managed symlinks on either, both switched, no host skipped.

The two halves go live on DIFFERENT triggers, which is why a partial deploy was the hazard.
Measured with the arbiter, not inferred — BEFORE the switch and AFTER:
| path | `readlink -f` | goes live on | after the switch |
|---|---|---|---|
| `scripts/resume-state.sh` | **itself** | a plain `git pull` — 0 references in `nix/` | `worktrees_holding` ×6 |
| `claude/skills/resume/SKILL.md` | `/nix/store/…-devrc-claude-skills/resume/SKILL.md` | `home-manager switch` | store path moved `p900zd0v…`→`iy8zczaj…`, now byte-identical to `origin/main` |
It DIFFERED from the repo copy before the switch, so the skill half was genuinely stale.
The misleading half-deployed state never existed on either host.

🔴 **Behaviour verified end to end on the DEFECT, not just the artifact.** Naming a
handoff-shaped path absent from the base clone but present in linked worktrees now refuses
to choose instead of falling back to newest-of-N:
```
! requested handoff "…/handoff-discord-embed-ext-rescue.md" — NO SUCH FILE, and … exists
  in 28 worktrees of the clone that path resolves against (…, and 24 more), so NONE was
  chosen. NOTHING was reconciled; the DRIFT section below is about no document at all.
```
⚠ **The verifier recipe below asks for a doc in EXACTLY ONE worktree and no such doc
existed** — the one available lives in 28. The multi-holder case exercises the same
suppression and is the stronger test; `resume-state.sh` was not among the tree's dirty
paths, so this is evidence about committed source, not a deployed copy.

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

### 🔴 RETRACTED — "Tekton reds that attribute ELSEWHERE" was ALREADY DIAGNOSED by another session
- **Symptom + exact repro:** the block above this one treats four Tekton reds
  (`test_a_FORGED_actor_in_the_body_is_DISCARDED`, `TestAHungRoundTripSAYSWhichSideBlocked.
  test_a_stall_in_the_FSYNC_region_is_NAMED`, `test_no_test_writes_a_usr_bin_env_shebang_at_
  runtime`, `test_release_deletes_the_ref_and_the_slug_becomes_claimable_again`) as an
  unexplained flake and proposes filing an issue. **Do not file it.**
- **Observed (with values):** `scripts/ci-repro/README.md` is ON `origin/main` and names the
  mechanism: `server.py:_replace_bytes` fsyncs the file and then the parent directory INSIDE
  the request, before the response is written; `devrc-ci` is pinned to one node, so stacked
  runs contend on one disk. It states outright that it hits **PRs whose diff cannot reach it,
  docs-only included**. The `devrc/tests.md` index entry carries the same finding dated
  2026-09-01, measured across two docs-only PRs: **4 reds, 3 DIFFERENT tests**, `scripts/tests`
  targets 464–530 s, each passing **3/3 locally in ~5 s** with a `--collect-only` positive
  control proving the failing test was actually selected.
- **Ruled out:** *that this needed a new issue* — the diagnosis, the reproduction harness and
  the written warning all already exist on `main`.
  via: doc
- **Leading hypothesis:** n/a — root-caused upstream, not by this effort.
- **Next probe:** none from here. 🔴 **Read `scripts/ci-repro/README.md` BEFORE re-pushing or
  debugging your diff** when a required check goes red on a test your PR cannot reach.

### ✅ CI reds: mechanism CONFIRMED from CI's own logs, and the retention claim above is WRONG
- **Symptom + exact repro:** supersedes the "Next probe" two blocks up, which reads "Tekton
  retains ~14 pipelineruns … which is why none of these four has a preserved log". That figure
  is what stopped anyone looking, and it is false.
- **Observed (with values):** MEASURED 2026-09-02 — **235 pipelineruns** in `tekton-ci`,
  `devrc-ci` history back **16 hours**, `-gate-pod` containers still present with logs readable
  hours after the run ended. Two retained reds (`devrc-ci-v54t7`, `devrc-ci-89m9l`, both started
  15:40 UTC) failed on the SAME test on different xdist workers, and the in-tree diagnostic named
  the arm outright, with no theorising required:
  `MECHANISM = SERVER_BLOCKED_IN_FSYNC (handler threads=1, accept loop parked=True)` →
  `_fsync_dir(path.parent)` → `os.fsync(fd)` at `server.py:1996`, on
  `TestTheAppendRequestIsValidated.test_a_text_at_the_LIMIT_is_accepted`
  (`collected=20474 passed=20470 failed=1`).
  Those two reds are **PRE-FIX**: `#1219` (`b4fde334`, 16:48:49 UTC) sites `scoped_store` — the
  fixture that stalled — ~68 min after they started; before it that fixture used plain
  `tmp_path`, i.e. real disk. Every `devrc-ci` run since is green (16:49, 17:06, 17:24).
- **Ruled out:** *that the tmpfs mitigation silently falls back in CI*, which
  `scripts/testlib/store_siting.py` explicitly flags as possible ("CI builds UNSANDBOXED …
  `/dev/shm` is the container's own mount — 64Mi by default, possibly absent or read-only. All
  of those land on the fallback"). On `talos-xr6-r7p` — the single node all 25 `devrc-ci` gate
  pods are pinned to, with no `/dev/shm` override in the Tekton pod spec —
  `shm /dev/shm tmpfs rw,seclabel,relatime,size=65536k` and WRITABLE. 64 MiB against the 4 MiB
  `_MIN_FREE_BYTES` floor; CI runs `-n 4`, so peak demand is 4 × `_LARGEST_STORE_BYTES`
  (1,875,968 B) ≈ **7.2 MiB, ~9× headroom**. The module's accumulation hazard (a SIGKILLed run
  skips the `finally`, leaving `/dev/shm/devrc-store-*` on a persistent container) does not
  apply — Tekton gives every PipelineRun a fresh pod, so `/dev/shm` starts empty.
  via: measurement
- **Leading hypothesis:** closed for the store-api arm. `_why_the_server_did_not_answer` works —
  **read its `MECHANISM =` line before forming any hypothesis about a store-api hang.**
- **Next probe:** ⚠ **TWO of the four named tests are NOT explained by this mechanism** and no
  evidence for them survives. `test_no_test_writes_a_usr_bin_env_shebang_at_runtime`
  (`scripts/tests/test_runtime_shebangs.py`) and
  `test_release_deletes_the_ref_and_the_slug_becomes_claimable_again`
  (`scripts/tests/test_claim_work.py`) contain **0** references to `build_server` or
  `store_siting`, so the fsync/tmpfs story cannot reach them. Status is *2 of 4 explained* — do
  not restate it as "the flake is solved". If either recurs, capture its `-gate-pod` log the
  same day; retention is hours, not the ~14 runs this doc used to claim.
- ⚠ **Scope:** 3 clearly post-fix green runs is consistent with a fix, **not proof** against a
  load-dependent flake. What is established is the mechanism, that the mitigation reaches the
  fixture, and that its precondition holds in CI.

## Next steps (ranked)
1. **Close devrc#1160** — four `status`→code associations `claude/skills/handoff/SKILL.md`
   documents in prose that nothing pins (`written`⇒0, `failed`⇒3, `push-failed`⇒3,
   behind-but-usable⇒0), plus a stale `MIN_TESTS` ledger comment. The issue carries its own
   closing condition. ⚠ The byte budget MOVED: **#1144 merged (`3d0b77e5`) and raised
   `MAX_BYTES` to 27,000**, so the budget is 26,100 and that file is 25,864 → **236 B** of
   headroom, not the 7 B an earlier note claimed.
   forcing: none
2. ✅ **The staged dnsmasq fix is ALREADY APPLIED — do NOT run the script.** Re-measured
   2026-09-02 before acting, per `claude/RULES.md` → "Memory Is a Hypothesis".
   `/etc/nixos/configuration.nix:76` already reads
   `servers = ["/docker.io/<public-resolver>" "<lan-router>" "<public-resolver>"];` (the
   domain-specific entry wins, so docker.io alone bypasses the router) and the script's own
   precondition (exactly 1 match for the pre-fix line) now matches **0** — it would print
   `already applied — nothing to do`. It is LIVE, not merely edited: via `127.0.0.1`,
   `registry-1.docker.io` answers TTL **49 s** with **8/8** serving `*.docker.com`, and
   `registry-1.docker.io/v2/` returns `http=401 tls=0`. Laptop equally healthy (TTL 41 s,
   `*.docker.com`, `http=401 tls=0`) — confirmed behaviourally; its `/etc/nixos` is not
   readable without sudo.
   🔴 **BUT THE ROOT CAUSE IS LIVE AND HAS GOTTEN WORSE, and that is the real remaining
   work:** `dig @192.168.50.1 registry-1.docker.io` still returns the pin — TTL
   **41,697,864 s ≈ 482 days**, counting down ~4 days from the original 487. Its eight
   addresses today are **0 correct / 2 third-party certs / 6 no handshake**
   — the two that DO complete a handshake present certificates for an unrelated company's
   staging Grafana and an unrelated personal site, neither of them Docker's (the addresses
   and hostnames are deliberately not recorded here; this repo is PUBLIC and they are third
   parties' — re-measure with `dig @<lan-router>` and read the certs yourself). That is
   against 4/2/2 when measured on 2026-08-29. Any LAN device without
   the host-side bypass now fails **every** pull, not half. Clearing that entry on the
   router is the actual repair — the script's own header says so; it is a host-side bypass
   for two machines, not a fix.
   forcing: none from these two hosts — router admin, and both hosts are already covered.

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

- 🔴 **READ THE SUBSYSTEM INDEX BEFORE RE-DERIVING A CI FAILURE.** This effort measured the
  same flake FOUR times across four PRs — attribution, a pristine-`main` control, a
  same-window sibling PR — and wrote a ranked step proposing to file it. The answer was
  already in `devrc/tests.md` and in `scripts/ci-repro/README.md`, both on `main`, with a
  deeper diagnosis than any of those four measurements produced. `/resume` step 4 exists for
  exactly this and running it costs one command.
- 🔴 **THE INDEX WRITE IS PER-EFFORT, AND A MULTI-EFFORT SESSION WILL SKIP THE SECOND ONE.**
  `/handoff` step 4 ran for effort 1 and not for effort 2; nothing noticed until a completion
  audit asked "is every objective addressed?" — the doc, the PRs and the claims were all
  clean. **If a session lands more than one PR, run `subsystem_touch --pr <n>` once per
  effort**, and check the store for each before declaring done.
- ⚠ **`status=unevidenced` (exit 10) is a NEW refusal** — rule (k), shipped by devrc#1144 on
  2026-09-01. Every `Ruled out:` bullet now needs `via: <kind>` (`change`/`code`/`command`/
  `doc`/`measurement`, or `assumed` for reasoning). It refused this very doc's first write.

- 🔴 **A HANDOFF IS A MUTABLE FILE ON A BRANCH THAT MOVES — re-read it from `origin/main`
  before acting on its ranked steps, even when a kickoff message quotes them.** 2026-09-02: a
  session resumed from a kickoff quoting a rank 2 of "file the CI-flake issue". That rank had
  ALREADY been retracted on `origin/main` — the retraction block says "Do not file it" — and the
  session re-derived the whole diagnosis from CI logs anyway. It is the FIFTH occurrence of the
  bullet above about re-deriving a CI failure, and the first where the answer was in *this very
  document*. `git -C <repo> show origin/main:claudedocs/handoff-<topic>.md` costs one command.
  ⚠ It did produce three genuinely new facts (the retention figure being wrong, the `MECHANISM =`
  confirmation, the `/dev/shm` probe) — so the lesson is *read first*, not *don't look*.
- 🔴 **`main` HAS NO MERGE GATE, THIS IS DELIBERATE, AND `drift-check.sh` rc 24 IS EXPECTED —
  NOT A FINDING.** Measured 2026-09-02: `required_status_checks` absent from the protection
  object entirely and `enforce_admins: false`, while `GET /branches/main` still reports
  `protected: true`. The operator turned it off because the gate was slowing work down; it
  stays off until the Tekton capacity issue is addressed, which a **different session owns**.
  `CLAUDE.md` is the authority — read it there, do not re-litigate it here, and **do not
  "restore" it**. Tekton still RUNS (both checks post on a PR head), it just does not block.
  ⚠ **This session got the framing wrong first**: it read `required_status_checks: null` plus
  `enforce_admins: false` as the signature of a break-glass window closed with a partial `PUT`,
  and filed it as rank 1 to be restored. The API shape is identical either way — a deliberate
  disable and a botched restore are **indistinguishable from the endpoint alone**. Ask whether
  someone turned it off before concluding something broke.
  🔴 **The live consequence is real regardless of intent: run BOTH tiers yourself before
  merging** — `scripts/gate.sh --tier both` AND `nix build .#checks.x86_64-linux.{pytests,
  nodetests}` one at a time, on the MERGED tree — and name the tier and base sha in the claim.
  A direct `commit` onto `main` in the workbench checkout pushed successfully in this window
  (`git reflog` → `HEAD@{0}: commit:`); required checks would have rejected it.
- ⚠ **`drift-check.sh | tail` REPORTS rc 0 FOR A RUN THAT EXITED 24.** The pipe returns `tail`'s
  status — the documented `nix build … | tail` trap, one level over. The script prints its own
  `drift-check: DRIFT (rc=24)` verdict line, which is the only reason it was caught; the echoed
  number was still wrong. **Redirect to a file and read the verdict line, never `| tail`.**
- 🔴 **A `kubectl get <resource> | tail -20` OF A 235-ROW LIST READS EXACTLY LIKE THE WHOLE
  POPULATION**, and that is how the "~14 pipelineruns" retention claim survived. It was
  reproduced live while checking it: the tail showed ~20 rows, the oldest 106 min old, and the
  obvious reading — "retention is about two hours" — was wrong by 16 hours and 215 rows.
  **Count before concluding a window:** `--no-headers | wc -l`.
- 🔴 **`handoff_doc.py` IS THE DOC'S ONLY WRITER, AND HAND-EDITING BYPASSES REAL GATES.** Editing
  the markdown directly skips rule (k) `status=unevidenced` (exit 10), the durable-line
  drop-warning, and the `forcing:` audit. Measured here: a hand edit looked complete and the tool
  then refused it for an inherited `- **Ruled out:** nothing yet.` carrying no `via:` field.
  🔴 **Bucketing is not uniform and it changes what you must supply:** `State now`,
  `Next steps` and `How to verify` are **REPLACE** (supply the section whole, or lose what you
  omit), while `Open investigations` and `Gotchas` are **APPEND** (supply ONLY new content, or
  you duplicate the section). The tool prints the buckets — read that line before writing.

## How to verify
```bash
# 1. all three PRs landed, by CONTENT (a squash is never an ancestor)
git -C ~/workspace/devrc fetch origin main
for p in 1146 1166 1197; do
  gh pr view $p --repo innovation-upstream/devrc --json number,state,mergeCommit \
    --jq '"#\(.number) \(.state) \(.mergeCommit.oid)"'
done
git -C ~/workspace/devrc show origin/main:scripts/resume-state.sh | grep -c worktrees_holding  # 6

# 2. the issues they closed
for i in 1093 1115 1164; do gh issue view $i --repo innovation-upstream/devrc --json number,state; done

# 3. WHICH HALF IS LIVE ON THIS HOST — readlink is the only arbiter, never a diff
readlink -f ~/workspace/devrc/scripts/resume-state.sh   # itself      => live on pull
readlink -f ~/.claude/skills/resume/SKILL.md            # /nix/store/ => needs a switch
# post-switch the skill half must be byte-identical to the branch, not merely present:
diff <(cat ~/.claude/skills/resume/SKILL.md) \
     <(git -C ~/workspace/devrc show origin/main:claude/skills/resume/SKILL.md) && echo SAME

# 3b. BOTH HOSTS actually converged (read every per-host line, never the final verdict)
~/workspace/devrc/scripts/drift-check.sh > /tmp/drift.txt 2>&1; echo "rc=$?"  # NEVER `| tail`
grep -E '^\[(workbench|laptop)\].*(BEHIND|AHEAD|DIVERG|clean)' /tmp/drift.txt

# 4. the guards still re-derive (batteries are author instruments; the gate never runs them)
PYTHONDONTWRITEBYTECODE=1 nix develop ~/workspace/devrc -c \
  python3 ~/workspace/devrc/scripts/tests/mutation_battery_resume_state.py   # 73/73, 0 NOT APPLIED
nix develop ~/workspace/devrc -c python3 -m pytest \
  ~/workspace/devrc/scripts/tests/test_mutation_battery_anchors.py -q        # every anchor 1x
```
