<!-- handoff: gate speed + CI signal -->
# Handoff: gate speed, and what CI's signal is actually worth — 2026-09-11

## Run this first — the index, one read-only command

```bash
gh pr view 1429 --repo innovation-upstream/devrc --json state,mergeCommit --jq '{state,c:.mergeCommit.oid}'   # merged a0839ec4
gh pr view 1445 --repo innovation-upstream/devrc --json state,mergeCommit --jq '{state,c:.mergeCommit.oid}'   # merged cace96d9
gh pr view 1469 --repo innovation-upstream/devrc --json state,mergeCommit --jq '{state,c:.mergeCommit.oid}'   # merged 86b1ddec
gh pr view 792  --repo ZacxDev/homelab-infra    --json state --jq .state                                      # OPEN — needs review
systemctl --user list-timers main-status-watch.timer --all                                                    # is #1469 actually LIVE?
```

## Goal

The operator asked one question: *"work in this repo is constantly waiting on 'gates' — what is that, do we really need it, and can we make it faster."*

Answered and shipped. An iteration run went from a **~20-minute median to 18 seconds**. The
pre-merge local full-suite ritual is **deleted**. Branch protection stays **off permanently by
operator decision** (solo-contributor repo; they require the ability to ship immediately).

## State now

- **RANK 10 IS COMPLETE — merged, shipped to BOTH hosts, and VERIFIED RUNNING. Ranks 7 CLOSED.
  RANK 11 has had TWO reds, both real, both fixed (`323c6b6b`), CI verdict UNREAD.**
  🔴 **AND THE FIRST LIVE SWEEP PRODUCED A CONFIRMED FALSE `INHERITED` — which is the criterion
  rank 10's own unit comment calls DISQUALIFYING for arming.** New rank 13.
- **Commit ledger** (`State now` is REPLACED every update — re-carry it or it is lost):
  `#1429` a0839ec4 · `#1445` cace96d9 · `#1469` 86b1ddec · `#1471` 4ab87a64 · `#1482` 972fbcbd ·
  `#1488` d835fe51 · `#1489` b315cdd3 · `#1502` ffef57bc · `#1512` 189689c1 · `#1567` 6f1867b1 ·
  `#1524` 58bfb747 · **`homelab-infra#799` 0b14768a** · **`#1600` f99d3c1b**. Closed unmerged on
  purpose: `#1558`, `#1559` (superseded by `#1561`).
- **`devrc#1600` — MERGED `f99d3c1b`, SHIPPED, and the consumer is VERIFIED RUNNING.** Gated on a
  real merged-tree run first: **8184 passed / 0 failed** over the **72** test files that read
  `nix/home.nix`, with the merged-tree claim re-checked AT merge time (`origin/main` `7e000e6b` both
  when the tree was built and when the merge ran; tested tree's second parent == the PR's live head
  `18701e53`). Landing verified by CONTENT, not ancestry.
  **Deploy:** first `ship.sh` exited **rc 7** — workbench SKIPPED on another session's live WIP in
  `claude/skills/clawgate/SKILL.md`. That cleared on its own; the **second** run exited **rc 0**,
  `converged + verified — 2 hosts compared, both at b55720e8`.
  **Consumer, MEASURED on the workbench** (these are separate claims and all three were made):
  timer `UnitFileState=enabled`, `ActiveState=active`, **next fire 19:29:55, 2h interval** as
  designed; the deployed `ExecStart` still carries `--comment-mode dry-run` (read off the UNIT, not
  the source); it **has already run once** — `Finished`, `Result=success`, `ExecMainStatus=10`,
  35.5s wall against a 600s timeout, so `SuccessExitStatus=10` behaves as designed.
  **First real verdict:** 56 PRs read · 24 red · **5 INHERITED** (`#1603 #1450 #1286 #1194 #1038`) ·
  2 NOT EXPLAINED · 17 COULD NOT MEASURE · 4 broken gate. Five `DRY-RUN would comment on` lines and
  **nothing written to GitHub**.
- 🔴 **`devrc#1603` HAD TWO REDS, BOTH REAL, BOTH CAUSED BY THIS PR — and the second was caused by
  the fix for the first.** (1) `test_the_runner_is_tracked_and_executable` called `git ls-files` in
  the no-`.git` sandbox tier; (2) the controls added to fix it wrote runtime stubs with an
  env-resolved shebang, which `test_no_test_writes_a_usr_bin_env_shebang_at_runtime` correctly
  caught — that resolver does not exist in the sandbox either. **Both are the same tier-blindness,
  one level apart.** Fixed at `8a88f255` then `323c6b6b`; 38 passed on the dev host
  (`test_runtime_shebangs.py` + `test_census_scan.py`). CI verdict on `323c6b6b` **UNREAD**.
- **`devrc#1613` — the handoff PR** (`2b5344e0`, `621074c4`). Open; CI pending.
- **Claims held:** `gate-speed-and-ci-signal-10`, `gate-speed-and-ci-signal-11`.
- **No `clawgate-task:` field recorded** — `resolve` exited **5** (0 tasks); its positive control
  shows the board reachable, which is explicitly NOT a clean bill of health.

## 🔴 Gotchas, measured — these are the ones that cost time

- **`/commits/{sha}/status` (SINGULAR) LIES.** It maps `error`→`failure`, and every non-code outcome
  here is `error`. Through it **48–49 of 60** main pushes read red; per-status, **9 rows in 200**
  were. A watcher built on it fires on ~80% of pushes on day one. Use `/commits/{sha}/statuses`
  (plural) — it returns a bare array with no roll-up field, so the mistake is *unavailable*, not
  merely avoided. Same family: **`/check-runs` returns NOTHING** for these checks — that zero is an
  instrument artefact. And the statuses list is **newest-first**, so a last-wins dict over it gives
  you the OLDEST post per context (one agent shipped that; it reported a known-green head as
  `pending`). Three separate agents hit this family.
- **Only ~21–22 of 100 `main` commits get an authoritative verdict** (59.5% superseded, 15% KILLED,
  5% NO GATE POD). "main's status" is usually *not* a verdict. Absence must never read as green.
- **CI's not-success rate is ~42–48%**, and the decomposition matters more than the number:
  ~30% genuine failure, **~18 points pure artefact** (runs finishing after their PR merged, dying
  `clone rc 128`, posting `BROKEN GATE`), and 0% never-resolving (that bucket is already closed —
  earlier samples of 10–17% do not reproduce). ⚠ **#792 does NOT move the rate** — a cancelled run's
  `finally` reporter posts `error` too. It buys compute and legibility (`superseded` instead of
  `BROKEN GATE`, which today is indistinguishable from a real break).
- **46% of failure heads traced to two windows where `main` ITSELF was red**, not to flaky tests —
  every PR branched in the window inherits it. That is what #1469 attacks (detection ~50 min vs up
  to 5.5h), and it is detection, never prevention.
- **Capacity is NOT the constraint** and has not been: 0 exit-255 across 2,179 recorded step
  terminations in ~18h, 0 OOMKills, 0 admission failures, devrc-ci node at 14% CPU / 9% memory
  requests, `NO CAPACITY` once in 80 heads. What is slow is **wall time** — median PipelineRun
  19.2 min, `pytests` 90–95% of it. A suite-duration problem wearing a capacity costume.
- **The contention table in earlier notes is RETRACTED.** "0 others → 14.5 min, 6+ → 48.9 min, 3.4x"
  was length-biased: a long run overlaps more runs BY CONSTRUCTION, and a null Monte Carlo with zero
  interaction reproduces the shape, the 14.5-min baseline and ~2.06x of the 3.4x. Contention is real;
  that dataset cannot size it. **Do not re-derive it.**
- **"100 of 100 gate runs died on missing tools" is RETRACTED too** — that population was defined by
  the failure's own cause (`LOG_DIR` defaults to `mktemp`, so only runs launched OUTSIDE the dev
  shell land in bare `/tmp`). Honest figure: **101 of 382 (26%)**.
- **A scoped run is NOT a gate**, and three per-target ledgers are suspended for a slice: GUARD 7's
  `NOLAUNCH_ACK` REQUIRED direction and GUARD 2's skip TOTAL are listed in the run's own
  `whole-target expectations SUSPENDED` block; **GUARD 3's floor is REPLACED, not suspended**, and
  says so on the `TOTAL … (SCOPED floor: N)` line. Read both places.
- **`assertEqual(nan, nan)` FAILS** — `_baseAssertEqual` does `if not first == second: raise`, no
  identity short-circuit. I asserted the opposite in a brief and an auditor measured me wrong; had it
  believed me it would have filed a genuine NaN detector as vacuous. Verified on 3.12.14.
- **`chmod 0o500` blocks create/unlink but NOT modify.** A fixture relying on it to make a directory
  unwritable does not reproduce ENOSPC.
- **`pytest`'s `tmp_path` is named after the test.** A test called `..._queueing_...` handed the gate
  a `--log-dir` containing "queueing", which the gate echoes back — the assertion matched its own
  name and passed against a `gate.sh` with no limiter in it at all. Match a phrase no path can
  contain.

## 🔴 The process lesson, because it repeated at every scale

**Hand-picked mutant lists encode the behaviour you were already thinking about.** Two sweeps on
`main-status-watch.py` self-reported 24/24 and 31/31 while independent samples found survivors both
times, and one audit found two survivors the author's own table did not contain. The fix is to
**enumerate** mutants from the source (every `return RC_*` flipped to each other code, every
state-mutating call deleted, every `if` forced False), not to try harder. A 95-mutant enumerated
sweep immediately found a survivor the hand-written lists would never have included.

Corollary, seen four times: **a fix round's own prose is the likeliest next finding.** A comment
asserting the watcher "ladders rather than going blind in permanent silence" was falsified by the
very arm it described. A ledger entry, a docstring and a header each carried one rule, and only one
copy got fixed — twice, including by the commit whose message argued for one-rule-one-place.

## Next steps (ranked)

🔴 **RANKS ARE IDENTITY** — `claim-work --slug-for <this doc> <rank>` before acting. New items go at
the END. **Ranks 1–4, 7, 9 and 10 are CLOSED tombstones**; renumbering re-points every live claim.

1. **CLOSED** — `homelab-infra#792` merged in dry-run (`dbe47814`). Arming is rank 8.
   forcing: none
2. **CLOSED** — `#1469`'s ladder, `#1502`, shipped and consumer-verified.
   forcing: none
3. **CLOSED — the store-api flake was already fixed by `#1458`.** ⚠ `#1512` is NO LONGER the
   measurement of record: it split on `ce9b55c3`'s TIMESTAMP and its zero was underpowered
   (4/125 → 0/45, P(0) ≈ 0.23). Superseded 2026-09-12 by an ANCESTRY split over 400 PR heads —
   **0 of 99** verdicts on heads carrying the sha against **12 of 298** that do not, P(0) ≈
   **0.017** — recorded in `devrc#1568` (`8114a124`), full table at
   `handoff-gate-flake-store-api.md` rank 1. ⚠ **`#1512`'s table is not WRONG**: re-splitting the
   same 397 verdicts by date reclassified 5 and **0 of 101 failures**. Cite the newer read; keep
   `#1512` for its "it was never the worst flake" finding, which stands.
   forcing: none
4. **CLOSED** — `#1524` merged `58bfb747`, shipped, consumer verified.
   forcing: none
5. **ANSWERED — NO PR, AND BOTH CANDIDATE FIXES WERE REFUTED.** Measured over 23 gate pods (11h
   window): `step-pytests` is **1177s = 94.4%** of a 1247s (20.8 min) gate pod; clone 38s, nix
   realisation 21–65s, nodetests 29s. Inside it, `scripts/tests` is **582–778s** at 14,327 of 22,518
   tests. **Dependency realisation is NOT the cost — test execution is.**
   🔴 **A docs-only commit DOES bust the derivation** (proven by derivation hash; sole diff is the
   `src` path), and **198 of the last 400 `main` commits (49.5%) are docs-only**. But the two obvious
   fixes are dead: (a) **there is no cache left to hit** — the warm node-pinned `/nix` PVC was removed
   in `homelab-talos 3c53d618a` (2026-09-11), now a per-run `emptyDir` with **no substituters**
   configured; min `step-pytests` over 23 runs is **956s** and no 52s outlier exists. (b) **excluding
   `claudedocs/` would blind real gates** — demonstrated by turning the gate red with a one-word edit
   to THIS doc, which `test_retracted_contention_figure.py` pins by digest.
   ⏳ **The one remaining lever is ONE LINE IN `homelab-talos`, not here**: raise `limits.cpu` 4→8 for
   `step-pytests` in `devrc-ci-pipeline.yaml`, leaving `requests.cpu` at 2 (the worker count follows
   the cgroup quota — it is `-n 4` because the LIMIT is 4, not because of `nproc`). ⚠ Distinct from
   the change already reverted (`23887675`/`bb62668f`), which raised the REQUEST and turned execution
   time into queue time. ⚠ **Expected 3–6 min saving is INHERITED, not re-derived** — the per-file
   profiling run died. Risk is documented: bursting to 8 cores is what produced the loopback-starvation
   flakes. **Probe on a scratch pipeline, never on `devrc-ci`.**
   forcing: none
6. **The flake screen in `main-status-watch.py` is probably inert** — decide on/after **2026-10-11**.
   🔴 Decide it TOGETHER with rank 4: `main-status-watch.py`'s flake screen already implements the same
   completeness-proving screen in **15 lines**, and `#1524` rebuilds that gate at ~885. Same question.
   🔴 **EVIDENCE CUTS TOWARD DELETE.** The screen exists to skip re-runs on KNOWN FLAKES, and the
   store-api flake it was written around is now at **0 of 99** verdicts on heads carrying `ce9b55c3`
   (`handoff-gate-flake-store-api.md` rank 1). The same read independently re-derived this file's own
   truncation finding from scratch — **100 of 101 failure descriptions truncated at 138 of the
   140-character cap** — which is what makes the screen unsatisfiable (the measurement sits beside
   `_FAILING_RE` in that file). ⚠ **Both figures are a READ-TIME population that cannot be
   re-derived**; a later disagreement is not a refutation. ⚠ **Not a decision — the 2026-10-11 date
   and "decide them together" both stand.**
   forcing: none
7. **CLOSED — `ZacxDev/homelab-infra#799` MERGED `0b14768a` on 2026-09-12.** Verified by content on
   `trunk`; test-only, reconciled to nothing.
   forcing: none
8. 🔴 **ARM `#792` (`CLOSED_PR_MODE: on`) — SOAK UNTIL ~2026-09-18, THEN ARM.** Operator set this
   date. Criteria (i)–(iii) met non-vacuously on the first two post-deploy sweeps; (iv) needs the
   window. Read `{app="tekton-supersede"} |= "DRY-RUN would cancel"` and `|= "closed-pr pass:"` in
   Loki, hand-check 2–3 named PRs, then flip `supersede-cronjob.yaml` + the pinned literal at
   `test_supersede_logic.py:2251` (one commit, by construction).
   🔴 Zero `DRY-RUN would cancel` lines after a week of normal merging is NOT a clean bill — it is
   the instrument failing to see its bucket. ⚠ Dry-run short-circuits BEFORE the re-read guard, so
   the soak cannot exercise the mid-tick race (devrc #1500 merged 14s after a sweep started).
   ⚠ **`#799` has since merged into `trunk`**, so re-read `test_supersede_logic.py:2251` rather than
   trusting the line number.
   forcing: deadline — the operator set 2026-09-18.
9. **CLOSED by `#1561`** — the kill scanners no longer read `claudedocs/`. **Rate evidence:** the
   kill-mention ledger accounts for **22** `tekton/devrc-pytests` reds across PR heads and **every
   one predates `c0bbd6d9`**; see `handoff-gate-flake-store-api.md` rank 1 for why that population is
   read-time-only.
   🔴 **Both instances of the CLASS are fixed and the class itself is not** — same design fired from
   `scripts/tests/test_runner_bound_ledger.py` (**5** reds, **4** after `c0bbd6d9`), closed by
   `#1567` `6f1867b1`. 🔴 **THIS SESSION ADDED A THIRD INSTANCE AND A NEW CONSEQUENCE**:
   `test_runtime_shebangs.py` reddened `#1603` the same way, and rank 13 shows the class ALSO breaks
   the new triage bot's INHERITED heuristic. The class is now costing more than red builds.
   forcing: none — instances shipped; the class is unaddressed and owned by nobody.
10. **CLOSED — `devrc#1600` MERGED `f99d3c1b`, shipped to both hosts (`b55720e8`), consumer VERIFIED
    RUNNING on the workbench** (timer enabled/active, next fire on a 2h interval, one clean run,
    `Result=success` `ExecMainStatus=10`, deployed `ExecStart` still `--comment-mode dry-run`).
    Arming is **rank 13**, not this one, and rank 13 currently says NO.
    forcing: none
11. **`devrc#1603` — TWO REDS FIXED (`8a88f255`, `323c6b6b`); MERGE ONCE CI IS GREEN.** Both were
    real and both were this PR's own doing; the second was caused by the fix for the first. 38 passed
    on the dev host. ⚠ **CI verdict on `323c6b6b` UNREAD** — read `gh pr checks 1603`, and remember
    the measured ~42–48% not-success rate: read the failing test's NAME and ask whether the diff can
    reach it. **On this PR, twice, it could.**
    The PR's substance stands: the ledger set is genuinely DERIVED (AST closure at call time); its
    two-pass design is load-bearing because `public_ip_scan.repo_files(root)` walks its own PARAMETER.
    ⚠ **166s, ~3 minutes — NOT "seconds"**; ~7× the tier. ⚠ Its files include `scoped-tests.sh` and
    `testlib/**`, BOTH declared shared surfaces, so gate the MERGED tree as rank 10 did.
    forcing: gate — it reddens `main`, and every branch cut from a red `main` inherits it.
12. **`devrc#1613` — the handoff PR.** Merge once CI is green; it carries this doc.
    forcing: none
13. 🔴 **DECIDE WHETHER THE TRIAGE BOT CAN EVER BE ARMED — its first live sweep produced a CONFIRMED
    FALSE `INHERITED`, and rank 10's own unit comment says ONE is disqualifying.** Full diagnosis in
    the open-investigation block above; the mechanism is that the bot's heuristic ("the failing test
    file is unchanged in my branch") is systematically wrong for repo-wide census/scanner guards,
    which is this repo's most common source of red. **Do not arm on the current heuristic.**
    First probe is cheap and is the one that turns 1 into a rate: hand-check `#1450 #1286 #1194
    #1038` from the 17:29 sweep. Then decide between (a) narrowing INHERITED to exclude tests whose
    scan set is not the file they name, (b) demoting those to NOT EXPLAINED, or (c) leaving the bot
    in dry-run permanently as a reading aid. ⚠ The bot is **inert** meanwhile — `--comment-mode
    dry-run` is pinned by value and verified on the deployed unit — so there is no urgency and no
    risk, only a decision.
    forcing: gate — arming is blocked until this is answered.

## Decisions, so they are not re-litigated

- **Branch protection stays OFF, permanently, by operator decision** — solo-contributor repo, they
  require the ability to ship immediately. 🔴 It is **not** conditional on Tekton capacity; an
  earlier declaration said so and had expired by its own terms, which would have led a future
  session to restore it *correctly by the declaration and against what the operator wants*. Only the
  operator reverses this. `bp_declared_off_reason()` in `scripts/drift-check.sh` is the authoritative
  text; everything else points at it. rc 24/25 remain armed for an accidental restore.
- **The ten-reporter change is RETIRED.** Its only justification was "protection would block on
  artefact statuses". With protection permanently off, that evaporates.
- **Splitting `scripts/tests` into sub-targets was REJECTED on measurement**, not taste: reaching the
  same speedup via target granularity needs ~185 targets at 0.95s pytest startup each = **+176s on
  every full run**, landing on CI — and it fragments the `--dist loadfile` pool where the win is.
  File-level `--files` selection gets 94x because one file collects in 0.64s against the monolith's
  60s.
- **The local full-suite pre-merge ritual is DELETED.** It produced dozens of concurrent full-suite
  runs on one 24-core box, running the same ~22k tests twice per change while gating nothing.
## How to verify

```bash
# rank 10 — merged, shipped, RUNNING. Three separate claims; make all three.
git -C ~/workspace/devrc show origin/main:nix/home.nix | grep -c enableStaleBaseTriage   # 2
systemctl --user show stale-base-triage.timer -p UnitFileState -p ActiveState   # enabled / active
systemctl --user show stale-base-triage.service -p ExecStart | tr ' ' '\n' | grep -A1 comment-mode
journalctl --user -u stale-base-triage -n 40 --no-pager     # workbench ONLY — laptop is gated off

# 🔴 rank 13 — the false INHERITED, and why it is false
journalctl --user -u stale-base-triage --no-pager | grep -A14 'PR #1603'
git -C ~/workspace/devrc show cfdb38997ba4 -- scripts/tests/test_runtime_shebangs.py | head -30
#   ^ the ONLY commit main had on that file: an allowlist row for test_nvim_octo.py, unrelated.

# rank 11 — both rounds, on the dev host
nix develop ~/workspace/devrc -c python3 -m pytest \
  ~/workspace/devrc-fix1603/scripts/tests/test_runtime_shebangs.py \
  ~/workspace/devrc-fix1603/scripts/tests/test_census_scan.py -q -p no:cacheprovider   # 38 passed
# and the sandbox shape, which is the tier that was red both times:
S=$(mktemp -d); git -C ~/workspace/devrc-fix1603 archive HEAD | tar -x -C "$S"
test -e "$S/.git" || echo "no .git — sandbox reproduced"
nix develop ~/workspace/devrc -c python3 -m pytest "$S/scripts/tests/test_census_scan.py" \
  -q -p no:cacheprovider --rootdir="$S"
```

## Gotchas / decisions / dead-ends

- 🔴 **An agent worktree is where unsaved work goes to die.** Three agents on this arc were killed by
  session limits mid-task; one had finished its fixes and deliberately not committed them. **Check
  `git -C <agent-worktree> status --short` for every dead agent before concluding its work is lost or
  landed** — `ListAgents` showing no subagents does not mean their trees are empty.
- 🔴 **A resumed agent will wait forever on a background job that died with it.** Every resume message
  on this arc had to say so explicitly. Tell it: whatever you had in flight is gone, re-run it.
- 🔴 **`git worktree add <dir>` against an existing dir fails, but a subsequent `mv` into that dir
  SUCCEEDS.** Measured here: `/home/zach/workspace/devrc-handoff` already existed as ANOTHER session's
  worktree on `docs/handoff-cairn-slice2` with its own unpushed commit, and this session dropped a
  file into it. Caught before committing. **Check `git -C <dir> branch --show-current` before writing
  into any worktree path you did not just create.**
- `claim-work --release <slug>` answering `nothing to release — <ref> does not exist` means **the slug
  is wrong**, not that it was already released. A claim from this arc stayed held for two days on that
  misreading. Copy the slug from `--list`, never retype it.
- **Protection stays OFF permanently — solo-contributor repo, the operator requires the ability to
  ship immediately.** 🔴 It is NOT conditional on Tekton capacity. An earlier declaration said so and
  had **expired by its own terms** (capacity measured fine), which would have led a future session to
  restore protection *correctly by the declaration and against what the operator wants*. Fixed in
  `#1471`/`#1482`; `bp_declared_off_reason()` is the authoritative text and every other site points at
  it rather than restating it. rc 24/25 remain armed for an accidental restore.
- **The ten-reporter change is RETIRED** — its only justification was "protection would block on
  artefact statuses", which the permanent-off decision removes.

### 2026-09-11 — the `#792` review, and the operator's arming decision

- 🔴 **DECIDED by the operator: merge `#792` in dry-run, arm later. Not arm now.** Do not
  re-litigate this, and do not treat the merge as arming. `CLOSED_PR_MODE: on` is a separate,
  later, evidence-gated one-line change (rank 1 carries the four criteria).
- **The dry-run pin is REAL, and was proven rather than read.** Script default `off`, manifest
  ships `dry-run`, pinned at `scripts/tests/test_supersede_logic.py:2251` through a `_container()`
  helper that also asserts `len(containers) == 1` — so arming by appending a second container
  fails too. A 12-mutant battery killed 12/12, each by a named test, **with a negative control**
  (a comment-only edit reported SURVIVED) proving the harness could distinguish at all.
- **`states_read=1 / closed=0` is a genuine positive control, not a silent zero.** `states[ref]`
  is written only after a 2xx JSON body carrying a string `state`; every failure mode (urllib
  error, rate limit, 404, non-JSON, missing field, budget exhausted, failed mint) leaves the ref
  ABSENT, which the selector reads as "do not cancel". So a non-zero `states_read` cannot be
  produced without GitHub answering. Corroborated independently of the PR body from Loki: three
  probe runs, `states_read == resolvable` in all three.
- ⚠ **The manifest cites `resolvable=4` and the PR body `resolvable=1` — that is NOT a
  contradiction**, it is two different probe runs, both real and both still in Loki. Do not file
  it as a finding.
- 🔴 **A PR body claiming "all 5 Tekton checks green" was FALSE and nearly became evidence.**
  Measured on head `60b7b8f81`: `/check-runs` → `total_count: 0`; `/statuses` → exactly ONE
  context, `tekton/gitops-validate success`. The other four are *push*-triggered pipelines on
  **other repos** and structurally cannot post on a homelab-infra PR. The change IS gated
  (gitops-validate ran all 9 legs incl. `scripts-tests` and kubeconform, covering both the script
  and the 10 reporter edits) — but the sentence overstated how that was obtained. **Generalise:
  a PR body's own list of green checks is a claim, and it is the one nobody re-measures.**
- **`#792` does NOT move CI's ~42–48% not-success rate** — a *cancelled* run's `finally` reporter
  posts `error` too. It buys compute and legibility (`superseded` instead of `BROKEN GATE`, which
  today is indistinguishable from a real break). Do not sell it as a red-rate fix.
- **Blast radius when armed, traced rather than assumed:** namespaced `Role` on
  `tekton.dev/pipelineruns` in `tekton-ci` only — `get, list, patch`, **no `delete`**. A cancel
  requires ~9 conjunctive conditions including exact equality on GitHub's `state == "closed"`
  (never `!= "open"`), re-read immediately before the write and re-checked to still name the same
  PR. A run on `main`/a push is unreachable (its key is `branch-…`). Cross-repo mistakes are
  prevented by reading the repo from the `repo-full-name` **param**, not the `ci.zacx.dev/repo`
  label — which matters live, since `vetr-app-e2e-pipeline` currently carries runs for **both**
  `vetrllc/vetr-api` and `vetrllc/vetr-app`.
- **Rollback ≈ 2–7 min** (Flux GitRepository 1m, Kustomization `tekton-triggers` 5m, CronJob tick
  ≤60s), or ~1 min with `flux reconcile kustomization tekton-triggers --with-source`. A wrong
  cancel destroys nothing — no `delete` verb, the object and its `ci.zacx.dev/cancelled-because`
  annotation survive, a push re-triggers. **Reversible, not costly.**
  ⚠ `kubectl -n tekton-ci patch cronjob tekton-supersede -p '{"spec":{"suspend":true}}'` is the
  instant out-of-band stop and kills BOTH passes — but whether Flux SSA reverts it was **not
  tested**. Do not count on it persisting.
- **Accepted trade, stated so it is not rediscovered as a bug:** `devrc` and `homelab-infra` each
  keep an *unkeyed* push leg (`devrc-ci-push-main`, `gitops-validate-push-trunk`) that pass 2 can
  never reach, so post-merge coverage survives. **`vetr-app` has no push leg** — a cancelled
  vetr-app PR run therefore leaves no post-merge verdict.
- **The reopen argument holds, measured:** all 7 `pull_request` triggers in the EventListener
  carry a `body.action` allowlist and all 7 include `reopened`.
- ⚠ **Dry-run short-circuits `cancel_all` before the re-read**, so the soak exercises neither the
  re-read guard nor the `expect` coordinate re-check. Both fail safe and both are mutation-covered
  — but **do not read the soak as evidence about them.**
- ⚠ **No cross-sweep caching:** GitHub is re-asked every 60s per in-flight PR-keyed run. At today's
  volume (4 such runs, 3 owners) that is ~600 req/hr against a 5,000/hr installation limit. ~80
  concurrent PR runs would exhaust it; the failure is fail-closed plus log noise, never a wrong
  cancel.
- **A stale PR base expires a "merged tree" claim silently.** `#792`'s body argued "0 commits
  behind trunk, so the tree these gates ran on *is* the tree the merge creates" — correct when
  written, and by review time the branch was **6 behind**. Zero file overlap and
  `mergeStateStatus: CLEAN`, so the risk was low, but the gated tree was no longer the merged
  tree. **A merged-tree claim carries an expiry that nothing prints.**

### 2026-09-11 — verifying rank 2, and four instrument failures in one session

- 🔴 **`git rev-parse $ref:path` in zsh returns a CONFIDENT WRONG 40-char sha.** `$r` followed by
  `:s` is eaten as a **history substitute modifier**, so the command never asks what you think.
  It printed a plausible blob id that made a file look CHANGED across three refs when
  `git ls-tree` showed one identical blob (`c342720d`). **Brace it (`${r}:path`) or use
  `git ls-tree`.** This is the documented unbraced-var trap, hit while actively reading the rule.
- 🔴 **A mutation that edits a COMMENT reports SURVIVED having never run.** `classify`'s
  fall-through was mutated with `.replace('return "error-other"', …, 1)` — and the FIRST
  occurrence in the file is inside a comment *quoting that literal as prose*. The sweep was green
  and meaningless. **Mutate by LINE NUMBER with an assert on the line's exact content.** Redone
  properly, both fall-throughs are KILLED by named tests
  (`test_an_UNRECOGNISED_state_is_never_green_end_to_end`).
- 🔴 **zsh does not word-split, so `pytest $SEL` passed 50 paths as ONE argument** — pytest errored
  `file or directory not found`, ran **`no tests ran in 0.00s`**, and the pipeline still **exited
  0**. A merged-tree gate that observed nothing and reported success. Use an array, or `${=SEL}`,
  and **assert the collected count moved** before believing a green.
- 🔴 **I destroyed my own control by removing its worktree while it was still running**
  (`FileNotFoundError: …/wt-control`, and it exited **0** anyway). The decision did not change
  because the discriminating run had already finished — but that is luck, not justification.
  **Do not tear down a tree a background job depends on.**
- **A 1-of-4049 merged-tree failure was LOAD, and the mechanism was identified rather than
  re-run-until-green.** `test_no_real_launchers.py::test_autouse_is_what_protects_a_test_that_
  never_asks` died on a 300s `TimeoutExpired` on a *nested* pytest inside a run that took 3090s;
  the same file alone on the same merged tree passes **80/80 in 254s**. ⚠ It was NOT dismissed on
  shape: that file references `main-status-watch` four times, #1469 added its `NOLAUNCH_ACK` row,
  and `ffe4e5d0` is precedent for this exact file breaking on a merge with main. The launcher
  ledger's one real citation into the rewritten test file
  (`test_the_trigger_verb_is_MUTATING_so_the_stub_fails_it_closed`) was checked and RESOLVES.
- 🔴 **A PR green on its own branch is not a merged-tree claim, and #1502 was 12 commits behind.**
  Zero file overlap and a clean textual merge — which is NOT safety — so the at-risk surfaces
  (every test reading `CLAUDE.md`, the disk-accounting ledger, `scoped-tests`) were run on the
  actual merged tree before merging.
- **`#1502`'s own ladder repeated the arc's headline lesson twice more.** Round 4 caught round 3
  **claiming a fix it had not made** — the PR body had been written from the finding list rather
  than the diff. Round 5 then found three of round 3's own measured numbers stated **wider than
  measured** ("21 mutants" → 16; "every mutant of classify's arms" → 35 of 60; "ZERO coverage" →
  10 of 11). A fix round's own prose remains the likeliest next finding.
- **A 100% kill rate is a broken harness, and was reported as such.** One verification sweep
  returned 439/439 KILLED **with a RED negative control**; the whole 457-run sweep was voided
  rather than reported. A span self-check also caught 1 mutant of 440 whose edit landed inside an
  f-string and never made the change it claimed — reported VOID, not SURVIVED.
- 🔴 **CLAUDE.md said `main-status-watch` was "NOT LIVE UNTIL A SWITCH" for a full day after
  `ship.sh` had converged both hosts to `86b1ddec`.** The sentence was written before the deploy
  and nothing re-read it. Corrected in `#1502` to a MEASURED liveness claim that names its own
  re-measurement command. **A ⏳ "not live yet" note is a claim with an expiry that nothing prints.**
- ⚠ **#792's dry-run soak cannot validate the guard its own best evidence needs.** devrc **#1500**
  merged **14 seconds after** the sweep pod started — i.e. mid-tick, exactly the race the re-read
  guard exists for. Dry-run short-circuits `cancel_all` BEFORE the re-read, so the soak shows the
  SELECTOR is right while structurally never exercising the WRITE-path guard. Weigh that when
  deciding rank 8.

### 2026-09-11 — rank 3: a ranked item whose premise was wrong in both directions

- 🔴 **"Needs a diagnosis" was FALSE — the diagnosis was already in the file, and excellent.** The
  suite's own classifier had printed `MECHANISM = SERVER_BLOCKED_IN_FSYNC … accept loop parked=True`
  on run `devrc-ci-86zxj`: `server.py:_replace_bytes` fsyncs the file and then the parent dir
  **inside the request, before the response is written**, and fsync blocks in uninterruptible sleep
  — which is exactly the captured `TimeoutError` inside `socket.recv_into` (connection ESTABLISHED,
  never answered). **Read the target file before believing a handoff's characterisation of it.**
- 🔴 **AND IT WAS ALREADY FIXED, by a PR nobody connected to it.** `#1458` (`ce9b55c3`) sited the 18
  remaining store roots — this class's among them — on **tmpfs** via `sited_root`. That removes the
  mechanism rather than widening a bound: an fsync to RAM cannot stall on a contended disk. Nothing
  recorded that the fix had LANDED, so the item stayed on the queue reading as live.
  **A fix that is not written down where the symptom is described has not finished landing.**
- **Measured, `tekton/devrc-pytests`, newest verdict per PR head, split on `ce9b55c3`:** pre-fix 125
  verdicts / 29 genuine failures / **4** this test; post-fix 45 / 6 / **0**. `failure` and `error`
  counted separately throughout — `error` is a broken gate, not a bad change.
- 🔴 **THE ZERO WAS NEVER THE PROOF, and the comment now says so in the file.** At the pre-fix
  per-verdict rate (3.2%) the expected count in 45 verdicts is ~1.4, so **P(observing 0) ≈ 0.23** —
  a one-in-four coincidence. The mechanism's removal is what carries the claim. **A before/after
  table is the easiest thing in this repo to over-read; state the power beside it or it will be
  upgraded to "proven" by the next reader.**
- 🔴 **IT WAS NEVER THE WORST FLAKE — it was the best-DOCUMENTED one.** Same pre-fix window:
  `test_every_decrypt_family_VERDICT_is_pinned_WHOLE` failed **8** times to this test's **4**, and
  is also at 0 post-fix. It had no long diagnosis attached and was never ranked. **Vividness is not
  frequency: COUNT the failures before choosing which flake to chase.** This is the same error as
  ranking by a memorable incident rather than by a census.
- **`main` is a useless population for this question and that is structural.** Of 43 post-fix
  pytests verdicts on `main`, **40 were artefacts** (~31 `superseded`, 4 `KILLED: the gate pod
  died`, 4 `NO GATE POD`, 1 pending) leaving **3** authoritative successes. Use PR heads.
- ⚠ **UNMEASURED, recorded rather than guessed:** the 760-test verification run emitted **3
  warnings**, and this suite warns on `the spawn lost the port race and retried`. Only the tail was
  captured, so whether those were port-race retries is **unknown**. If they were, the race is live
  on this host — but nothing here claims that.

### 2026-09-12 — rank 4's ladder, and a red `main` nobody had fixed

- 🔴 **A `-k` FILTER THAT MATCHES NOTHING PRINTS `N deselected` AND EXITS 0.** Checking whether `main`
  was red, `-k 'kill_server_call_site' scripts/tests/` printed **`14194 deselected`** — the test lives
  in `scripts/claude-hooks/tests/`, which that path never covered. "Deselected" is not "passed", and
  the exit code cannot tell them apart. **Same silent-zero family as `pytest $VAR` under zsh** (one
  argument, zero tests, exit 0), which I also hit today.
- 🔴 **TWO AGENTS GAVE TWO DIFFERENT, BOTH-WRONG ANSWERS ABOUT THE SAME RED.** One said the test was
  cured on `main` by `7344e76f`; the other named `f3e27aa3` as failing. Current `main` fails on a
  THIRD file neither mentioned. Neither was lying — each measured a different tip of a moving branch.
  **A claim about `main` decays in hours; re-measure at the moment you act.**
- 🔴 **`pgrep -af <pattern>` MATCHED ITS OWN SHELL**, live, while I checked whether another worktree's
  run was still going — the command line containing the pattern appeared in its own output. Harmless
  on a read; it is exactly why a `-f` pattern must never reach `pkill`.
- 🔴 **A GUARD IS TRIPPED BY ITS OWN DOCUMENTATION.** `_KILL_MENTION_LEDGER` pins every file mentioning
  a wide tmux kill, so every handoff written ABOUT it becomes a new unclassified file. Three of its
  four `claudedocs/` rows exist for that reason alone, and `main` went red again **within minutes** of
  the PR that fixed the previous two. Generalise: **a guard whose trip condition is "a file mentions
  X" will be tripped by the documentation of that guard.**
- **A Pyright "Invalid character `\ud83d` in token" diagnostic was a FALSE POSITIVE** — the bytes were
  a valid `f0 9f 94 b4` (U+1F534 🔴) and the file parsed fine. An IDE diagnostic is a claim like any
  other; `ast.parse` is the arbiter.
- **I destroyed one of my own controls by removing its worktree while it ran** (`FileNotFoundError:
  …/wt-control`, exit **0** anyway). The decision did not change because the discriminating run had
  already finished — luck, not justification. **Check for live processes before removing any worktree**
  (`pgrep -af <path>`, reading the result rather than acting on a pattern).
- **Round 0 earned its trial slot: `ran: 1 · changed the outcome: 1`.** Its F1/F2 are unreachable from
  any of the nine correctness axes — a full checklist round would have passed a tool that fired on none
  of its justifying cases. Recorded on `#1524` as a comment.

### 2026-09-12 — closing out: what I got wrong, and what the session actually proved

- 🔴 **I INVESTED TWO PRs IN A PROBLEM ANOTHER SESSION WAS ALREADY SOLVING.** `#1558` and `#1559` both
  targeted the kill-mention treadmill; `#1561` closed it structurally while I was arguing about
  split-token forms, and I closed both unmerged. Three sessions were visibly active in those files —
  `claim-work --list` and `gh pr list` would have shown it. **Sweep for concurrent work BEFORE
  investing in a fix, not only before claiming a ranked item.** My net contribution to that problem
  was becoming an offender myself.
- 🔴 **MY OWN HANDOFF BROKE `main`.** The paragraph documenting "this guard is tripped by its own
  documentation" spelled the token literally, matched `_MENTION_RE`, and became the next offender the
  moment `58c03a77` landed. Three sessions did this concurrently inside one hour (`#1556`, `#1557`,
  mine). **A doc describing a text-matching guard is a file that guard will match.**
- 🔴 **A `-k` FILTER THAT MATCHES NOTHING PRINTS `N deselected` AND EXITS 0.** Checking whether main
  was red, `-k 'kill_server_call_site' scripts/tests/` printed **`14194 deselected`** — the test lives
  under `scripts/claude-hooks/tests/`. Deselected is not passed, and the exit code cannot tell them
  apart. Same family as `pytest $VAR` under zsh (one arg, zero tests, exit 0), also hit today.
- 🔴 **I NEARLY COMMITTED ANOTHER PROCESS'S MUTANT.** Copying a "verified" file out of a worktree
  where a background positive-control run was mid-swap produced a copy containing
  `UNCLASSIFIED-CONTROL`. Caught only by asserting expected marker counts before staging. **Never
  copy from a tree another process is writing; rebuild from the pristine ref.**
- 🔴 **`pgrep -af <pattern>` matched its OWN shell**, live, while checking whether a worktree was busy.
  Harmless on a read; it is exactly why a `-f` pattern must never reach `pkill`.
- **A Pyright "Invalid character `\ud83d`" diagnostic was a FALSE POSITIVE** — valid `f0 9f 94 b4`
  (U+1F534) and the file parsed. An IDE diagnostic is a claim; `ast.parse` is the arbiter.
- **ROUND 0 EARNED ITS TRIAL SLOT: `ran: 1 · changed the outcome: 1`.** On `#1524` it found the tool
  fired on **0 of the 3 cases that justify its existence** — unreachable from any of the nine
  correctness axes. A full checklist round would have passed it.
- **Three agents corrected MY briefs, and each correction was right:** the status-fold premise (first-
  wins over newest-first yields the NEWEST row, not the oldest); `FAILED_COUNT_RE` had the identical
  defect to `TOTALS_RE` and the auditor had not filed it; and a `scoped-tests.sh PASS` claim was
  withdrawn as measured against a pre-rebase base. **Brief an agent with your reasoning and it may
  fix your reasoning — read the corrections rather than the conclusions.**

### 2026-09-12 — the four-rank round, and the errors worth inheriting

- 🔴 **zsh's NO-WORD-SPLITTING bit me FOUR times in one session, in four different shapes**, each
  returning a confident wrong answer rather than an error: `pytest $SEL` (one pathspec ⇒
  `no tests ran in 0.00s`, **exit 0**), `git diff -- $FILES` (one pathspec ⇒ **empty diff**, which I
  briefly read as "round 1 changed nothing"), `set -- $p` (one string ⇒ `$2` empty), and
  `-k <filter>` matching nothing (⇒ `14194 deselected`, which is not "passed"). **Writing the warning
  into three agent briefs did not stop me walking into it. Use an array by default.**
- 🔴 **A REBASE SILENTLY WIDENS A DELTA-AUDIT RANGE, and the tooling's own warning cannot see it.**
  After rebasing `#1524`, `audit-dispatch.py` resolved `ebd6b436..3aec90b4` = **65 commits, only 8 of
  them the PR's**. The script's widened-range warning watches for a missing claims block; mine parsed
  fine. **And a commit range could not express that delta at all** — the fix round AMENDED earlier
  commits, so the naive range showed ONE commit for six fixes. The honest delta was a TREE DIFF
  (`ebd6b436` vs head, 6 files +681/−41).
- 🔴 **I INVESTED TWO PRs IN A PROBLEM ANOTHER SESSION WAS ALREADY SOLVING.** `#1558` and `#1559` both
  targeted the kill-mention treadmill; `#1561` closed it structurally while I argued about split-token
  forms, and I closed both unmerged. Three sessions were visibly active in those files. **Sweep
  `claim-work --list` and `gh pr list` BEFORE investing in an ad-hoc fix, not only before claiming a
  ranked item.**
- 🔴 **MY OWN HANDOFF BROKE `main`** — the paragraph documenting "this guard is tripped by its own
  documentation" spelled the token literally and became the next offender the moment it landed.
- 🔴 **I NEARLY COMMITTED ANOTHER PROCESS'S MUTANT** — copying a "verified" file out of a worktree
  where a background positive-control run was mid-swap produced a copy containing
  `UNCLASSIFIED-CONTROL`. Caught only by asserting expected marker counts before staging.
  **Rebuild from the pristine ref; never copy from a tree another process is writing.**
- 🔴 **CHECK A DEAD AGENT'S WORKTREE BEFORE RESUMING IT.** Four agents died on session limits this
  session. One held its entire deliverable as **UNTRACKED files** (`scripts/ledger-check.sh`,
  `scripts/testlib/census_scan.py`) — one `git clean` from gone, and the flake silently omits a new
  file that is not `git add`ed. Another sat on an unpushed commit through **two** stops.
- **Three agents corrected MY briefs, and every correction was right:** the status-fold premise
  (first-wins over newest-first yields the NEWEST row, not the oldest); "stop at the first
  `/`-prefixed positional" would have made `gh api /repos/o/r/x -X POST` read as CLEAN, a regression
  in the only direction that matters; and rank 11's "seconds" was really ~3 minutes. **Brief an agent
  with your reasoning and it may fix your reasoning — read the corrections, not just the conclusions.**
- **A vacuous expectation survived a mutation sweep, twice, in two different agents' work**: widening
  a module filter so NOTHING was selected produced exactly the `== []` the fixture asserted; and a
  whole-file `replace(…, 1)` hit a COMMENT 3,700 lines before the code, scoring "arm the writer" as
  SURVIVED having never touched it. **Scope a mutation to the narrowest expression and assert
  `count == 1` on what you replaced.**
- **I DID NOT PRUNE THIS DOC, deliberately.** It is 506 lines against a corpus where
  `handoff-tmux-webapp.md` is 3809; `Gotchas` is append-only by design and `handoff_doc.py` has no
  prune flag; and the ~74 replaceable lines are the retraction guards. The durable-drop warning caught
  that two other sessions had just superseded my own `#1512` read with a better-powered one
  (`#1568`, ancestry split, P(0) ≈ 0.017 vs my 0.23). **Pruning would have deleted work that improves
  on mine.**

### 2026-09-12 — rank 11's red: the PR reintroduced the failure it exists to prevent

- 🔴 **A TEST THAT READS `git ls-files` IS A TEST THAT CANNOT PASS IN THE SANDBOX TIER.**
  `tekton/devrc-pytests` was red on `#1603`'s own head `7d19f4c7` with
  `test_the_runner_is_tracked_and_executable` reporting **`not tracked by git: []`** against
  `scripts/ledger-check.sh` — a file tracked at mode **100755**. The message was FALSE. `nix build
  .#checks…` builds from a `cp -r ${./.}` store copy with **no `.git`**, so `git ls-files` exits
  **128** and prints nothing, and the assertion read that empty stdout as "untracked". Merged, it
  would have been a **permanently-red gate on `main`** — which is the exact thing `#1603` was built
  to stop. **A PR whose subject is "stop the guards that redden main" shipped a new one.**
- 🔴 **REPRODUCED, NOT INFERRED — and the reproduction is what discriminated the two assertions.**
  `git archive origin/pr-1603 | tar -x` into a scratch dir gives the sandbox shape exactly (no
  `.git`, modes preserved). Probing it directly: the exec bit is **preserved** (`-rwxr-xr-x`), and
  `git ls-files` returns **rc 128, empty stdout**. So of the test's two assertions only the git one
  is tier-dependent — an empty result that would otherwise have been read as "either could be
  failing". Running the test there reproduced the CI message verbatim.
- **The fix mirrors a shape this repo had ALREADY SOLVED TWICE and nobody reused:**
  `scripts/opencode/tests/test_dispatch.py::file_ship_problems` and
  `scripts/tests/test_load_test_harness.py::deploy_carries`. Both carry long comments explaining
  this precise failure — `test_dispatch.py`'s even records "five failures, all reporting a tracking
  problem that did not exist". **The cost of the third instance was a red CI run and a session's
  investigation; the search that would have prevented it was one grep for `not a git repo`.**
- **The three checks, and which are tier-conditional — only ONE is:** existence proves the flake
  carried the file inside the sandbox; the **exec bit is asserted unconditionally** (measured: both
  `git archive` and the nix store copy preserve 100755, and a lost mode bit is a real regression the
  sandbox CAN see); trackedness is made **conditional rather than skipped**, so it cannot go quietly
  vacuous on the tier that does have a git dir.
- 🔴 **The tier probe is a FUNCTION of the tree, not a module constant — and that is not style.**
  A `GIT_DIR_PRESENT = ...` constant is a fixture that can only ever produce the value an assertion
  about it names, so a mutant hardcoding it to `True` **survives on a dev host**, where the probe
  returns `True` anyway. `test_dispatch.py` records that exact survivor. Also `.git` is a **FILE**
  inside a worktree, so `.exists()` and never `.is_dir()` — this repo is developed in worktrees, and
  `.is_dir()` would disable the tracking half everywhere it matters.
- **Verification matrix, both tiers, identical counts:** dev-host tier (has `.git`) **29 passed**;
  sandbox tier (no `.git`) **29 passed**; pre-fix sandbox tier **1 failed** (the reproduction).
  🔴 **The equal counts are the evidence that nothing skipped itself** — a tier guard that silently
  deselected its own tests would show as a lower count on one side, not as a failure.
- **Mutation check, isolated to the narrowest expression:** deleting `if not git_present: return
  problems` (asserted `count == 1` before writing, under `PYTHONDONTWRITEBYTECODE=1`) killed
  `test_a_present_executable_runner_in_a_GIT_FREE_tree_reports_NOTHING` **with that guard's own
  error string** (`is not git-tracked — \`git add\` it`), plus
  `test_a_NON_EXECUTABLE_runner_is_reported_in_either_tier`. Killed for the right reason, and
  reachable from an ordinary checkout.

### 2026-09-12 — instrument failures in this session

- 🔴 **A BACKGROUNDED WRAPPER REPORTED EXIT 0 OVER A RUN THAT NEVER RAN A TEST.** The merged-tree
  script was launched as `bash run.sh > log 2>&1; echo "EXIT=$?"` — the shell's status is `echo`'s,
  which is always 0. The harness dutifully reported "completed (exit code 0)" for a pytest that had
  died on `unrecognized arguments: --timeout=900` (no `pytest-timeout` in this shell) with
  `PYTEST_RC=4`. **Caught only by reading the log content.** This is the documented
  count-not-exit-code trap arriving through the background-task notification, which reads far more
  like an authority than a pipe does.
- **`pytest` here has NO `--timeout` plugin.** `nix develop ~/workspace/devrc -c python3 -m pytest
  --timeout=N` is a hard usage error, not a slow run. Bound a long run some other way.
- **The merged-tree selector was given a floor for exactly this reason.** `mt1600-run.sh` refuses
  (exit 3, `COULD NOT MEASURE`) below 5 selected files rather than reporting a fast green over a
  collapsed selection — the silent-zero shape. It selected 72.
- **Enumerated with `find … -print0 | xargs -0 grep`, not `grep -r`** — `grep` here is a function
  wrapping ugrep and honours `.gitignore`, so a recursive zero is a claim about grep's view. The
  session hit the hook warning about this on a live call.
- ⚠ **`claim-work` was skipped for rank 7 and the merge happened anyway.** Both sweeps
  (`claim-work --list`, `gh pr list --state open`) were run at session start and showed no claim and
  no duplicate on this arc, so nothing collided — but the lock is supposed to come before the act,
  and "I checked the soft signals" is the reasoning the rule exists to override.

### 2026-09-12 — ship.sh rc 7: the verdict was wrong in BOTH directions

- 🔴 **`ship.sh` EXITED 7 AND THE LAPTOP HAD DEPLOYED FINE; THE HOST THAT FAILED WAS THE ONLY ONE
  THAT MATTERED.** Reading the final line alone gives "the ship failed" (false — the laptop
  fast-forwarded `7e000e6b → f99d3c1b`, `552 checked, 0 dangling`, `✅ VERIFIED … + switched`).
  Reading "one host converged" gives "it shipped" (also false — the sweep is `serverMode`-gated to
  the **workbench**, which is exactly the host that was SKIPPED). **CLAUDE.md's "read every per-host
  line, not the final verdict" is usually quoted against a skip hiding among greens; here the
  greens and the skip pointed at opposite conclusions and only the per-host lines resolved it.**
- 🔴 **A `serverMode`-gated unit deploys to the OTHER host and still runs nowhere — and it looks
  healthy.** MEASURED: the laptop has the SERVICE (`LoadState=loaded`) because the service is always
  emitted, but `UnitFileState=linked`, **0 timers listed**, `journalctl` `-- No entries --`, because
  the `Install` block is gated and the laptop has no `~/.server-mode`. That is the gate working
  correctly. **`systemctl cat <unit>` succeeding is not evidence the unit RUNS** — check
  `list-timers` and `UnitFileState`, and check them on the host the gate actually targets.
- 🔴 **THE BLOCKING FILE WAS LIVE WIP, AND THE REMEDY ship.sh PRINTS WOULD HAVE DESTROYED IT.**
  `claude/skills/clawgate/SKILL.md` was locally changed; `ship.sh` offers
  `git checkout origin/main -- <file>` as "take upstream". Two checks before believing it was stale:
  its working-copy sha1 matched **none of the last 8 commits** of that path (so not the byte-identical
  stale-orphan case CLAUDE.md warns about), and its **mtime was 2 minutes old** — another session was
  writing it *then*. The diff was three security retractions to the clawgate skill. **Handed over
  rather than cleared.** A dirty file that blocks your deploy is not thereby yours to resolve.
- **`ship.sh` is right to refuse, and says so:** `ship never stashes: the stash is repo-GLOBAL and
  would reach into other worktrees`. The skip left the workbench exactly as found, which is the
  designed behaviour — and also the failure mode CLAUDE.md warns silently starves a host of every
  future change. **A skipped host is not a neutral outcome; it has a clock on it.**
- ⚠ **`gh pr merge --delete-branch` FAILED on a branch pinned by a DEAD AGENT'S WORKTREE**
  (`.claude/worktrees/agent-ad021347d6b6db53d`) — the merge itself had succeeded. Checked the
  worktree before dismissing it, per this doc's own standing warning: clean tree, one commit, and
  that commit's content is what landed as `f99d3c1b`. Nothing stranded. **Its ancestry reads
  "unmerged" forever because of the squash** — content is the arbiter, not `--is-ancestor`.

### 2026-09-12 — the triage bot's first sweep, and what it got wrong

- 🔴 **A DRY-RUN SOAK EARNED ITS KEEP ON RUN ONE — by being WRONG in a way only a real sweep could
  show.** `#1600` shipped inert on purpose, and 35 seconds of real output falsified its own arming
  criterion (i). Every mutation test and merged-tree gate in this arc had passed; none of them could
  have found this, because the defect is in the bot's REASONING about real repository history, not
  in its code. **Ship-inert-then-read is not ceremony; it is the only instrument that sees this
  class.**
- 🔴 **THE HEURISTIC IS BACKWARDS FOR CENSUS GUARDS, WHICH IS THIS REPO'S DOMINANT RED.** "The
  failing test file is byte-identical at head and merge-base, and main moved it" means *inherited*
  for a test that exercises code it names, and means *nothing* for a scanner that inspects OTHER
  files — there, an unchanged guard file is the normal state of a genuine, self-inflicted breakage.
  `#1603` was ruled INHERITED on evidence (`cfdb3899`) that turned out to be an allowlist row for an
  unrelated file. **Read the bot's `evidence:` line before believing its `VERDICT:` line** — it
  prints the commit it is reasoning from, which is exactly what made this falsifiable in one command.
- **`SuccessExitStatus=10` works as designed** — `ExecMainStatus=10`, `Result=success`, unit not
  failed, `systemctl --user --failed` clean. rc 10 is the tool's headline finding, not an error.
- **The sweep's own shape, for comparison next time:** 56 read · 24 red · 5 INHERITED · 2 NOT
  EXPLAINED · 17 COULD NOT MEASURE · 4 broken gate, 35.5s wall, 12.6s CPU, 50M peak. ⚠ **17 COULD
  NOT MEASURE is the biggest bucket** and is mostly "no `tekton/devrc-pytests` status on this head"
  — consistent with this doc's own finding that only ~21% of heads get an authoritative verdict.

### 2026-09-12 — a fix round's own CODE was the next finding, not just its prose

- 🔴 **ROUND 1's FIX CAUSED ROUND 2's RED, IN THE SAME TIER, FOR A SIBLING REASON.** Fixing a
  `git ls-files` call that cannot work in the no-`.git` sandbox, I added three controls that wrote
  runtime stubs with an env-resolved shebang — which cannot work in the sandbox either, because that
  resolver is not present and `patchShebangs` cannot reach a file written at test time. This doc
  already recorded "a fix round's own PROSE is the likeliest next finding"; **widen it to CODE.**
- **Both reds shared one root cause I did not generalise fast enough:** *the sandbox tier lacks
  things the dev host has.* Having just fixed the `.git` instance, I did not ask what ELSE that tier
  lacks before writing new fixture code. **The right question after any sandbox-tier fix is "what
  else is absent there?", not "is this instance fixed?"**
- **`testlib.mockbin.write_exec` is the sanctioned answer and it RAISES on a call site that supplies
  its own shebang** — so the fix is not discipline, it is a mechanism. The controls never execute the
  file, so nothing was lost by switching.
- 🔴 **I then nearly re-committed the documented self-documentation trap.** The explanatory comment
  spelled the forbidden token; `test_runtime_shebangs.py`'s own allowlist entries warn that its
  self-match guard exists for precisely this. Reworded so the prose does not spell it — **measured 0
  occurrences after, not assumed.**

### 2026-09-12 — ship.sh rc 7: the verdict was wrong in BOTH directions

- 🔴 **`ship.sh` EXITED 7 WITH THE LAPTOP FULLY DEPLOYED, AND THE SKIPPED HOST WAS THE ONLY ONE THAT
  MATTERED.** The final line alone reads "the ship failed" (false — the laptop fast-forwarded and
  verified). "One host converged" reads "it shipped" (also false — the unit is `serverMode`-gated to
  the **workbench**, the host that was skipped). CLAUDE.md's "read every per-host line, not the final
  verdict" is usually quoted against a skip hiding among greens; **here the greens and the skip
  pointed at opposite conclusions and only the per-host lines resolved it.**
- 🔴 **A `serverMode`-gated unit deploys to the OTHER host and still runs nowhere — and looks
  healthy.** The laptop had the SERVICE (`LoadState=loaded`, `systemctl cat` succeeds) because the
  service is always emitted, but `UnitFileState=linked`, **0 timers listed**, journal
  `-- No entries --`. **`systemctl cat <unit>` succeeding is not evidence the unit RUNS** — read
  `list-timers` and `UnitFileState`, on the host the gate actually targets.
- 🔴 **THE BLOCKING FILE WAS LIVE WIP AND ship.sh's OWN PRINTED REMEDY WOULD HAVE DESTROYED IT.**
  Two checks before believing it was a stale orphan: its working-copy sha1 matched **none of the last
  8 commits** of that path, and its **mtime was 2 minutes old**. The diff was another session's
  in-progress security retractions to the clawgate skill. **Handed over, not cleared — and it
  resolved itself within the hour, after which `ship.sh` exited 0 and converged both hosts.**
  A dirty file blocking your deploy is not thereby yours to resolve.
- ⚠ **`gh pr merge --delete-branch` FAILED on a branch pinned by a DEAD AGENT'S WORKTREE**
  (`.claude/worktrees/agent-ad021347d6b6db53d`); the merge itself had succeeded. Checked the worktree
  per this doc's standing warning: clean, one commit, and that commit's content is what landed.
  **Its ancestry reads "unmerged" forever because of the squash** — content is the arbiter.
## Open investigations — live diagnosis state

### RANK 2: #1469's audit ladder has not reached a clean round
- **Symptom + exact repro:** `scripts/main-status-watch.py` (839 lines) ships and runs every 10
  minutes on both hosts, but its audit ladder never terminated. Round 3 never ran, and a
  95-mutant enumerated sweep died mid-run with its verdict unknown.
- **Observed (with values):** the dead sweep had already surfaced one genuine survivor —
  `print_header`'s sentinel branch — which the two earlier hand-written sweeps (self-reported
  24/24 and 31/31) would never have included. Whether that survivor is fixed is **unknown as of
  this writing**; confirming it is the first step of the in-flight agent's task.
- **Ruled out:** "the hand-written sweeps were adequate, just unlucky" — falsified twice:
  independent samples found survivors after both self-reported perfect scores, and one audit
  found two survivors the author's own table did not contain. via: measurement
- **Ruled out:** "capacity/load explains the sweep dying" — not investigated, and not assumed
  either; the verdict was simply never read. via: assumed
- **Leading hypothesis:** the enumerated sweep will find further survivors in the RC-code and
  state-mutation families, because those are exactly what a hand-picked list omits.
- **Next probe:** read the in-flight agent's survivor table when it reports. If it did not
  finish, re-run the enumerated sweep under `PYTHONDONTWRITEBYTECODE=1` with a known-killed
  positive-control mutant in every batch, and report the table rather than a score.

### RESOLVED — #1469's audit ladder (was rank 2)
- **Closed at round 6.** Rounds 3/4/5 each produced findings; round 6 found none, so it ended
  there — the first clean round ends the ladder, and no confirmation round was run.
- **The defect:** `_guarded_main`'s `except Unmeasured` returned rc 11 **without bumping the blind
  streak**. `SuccessExitStatus=10 11` makes rc 11 a systemd success, so the OUTERMOST net could
  never escalate and never fail the unit — permanently silent, in the net whose own docstring
  claimed the ladder counted it. Third appearance of that shape in this file.
- **Sweep, enumerated from the AST:** 440 mutants → **155 survivors** on the merged code, against
  two earlier hand-written sweeps that self-reported 24/24 and 31/31. After fixes 73, then 58 —
  each remaining one justified (23 are `say()` log-narration deletions, deliberately unpinned).

### `main` goes red every time someone writes a handoff ABOUT the tmux-kill ledger
- **Symptom + exact repro:** `nix develop ~/workspace/devrc -c python3 -m pytest
  scripts/claude-hooks/tests/test_guard_core.py::test_every_kill_server_call_site_in_the_repo_is_
  classified` on a clean `origin/main` worktree. Fails with the unclassified file named.
- **Observed (with values):** `_KILL_MENTION_LEDGER` (`test_guard_core.py:2525`) is a two-way pin over
  every file mentioning a wide tmux kill. Four `claudedocs/` rows now; **three exist purely because
  someone documented the guard.** `#1520` classified one, `#1534` classified two more **plus a real
  scanner bug** (it matched `kill-session` inside `sk`+`ill-session`), and `main` went red again on a
  new doc **within minutes of #1534 merging**.
- **Ruled out:** "carelessness / a one-off" — the recurrence is STRUCTURAL: a write-up of this
  incident is a new unclassified file *by construction*. via: measurement
- **Ruled out:** "#1544 fixed it" — `#1544` is a handoff commit; the ledger still lacks the file, and
  the test still fails on its tip. via: command
- **Leading hypothesis:** any guard whose trip condition is "a file mentions X" will be tripped by the
  documentation of that guard. This is the "permanently-red gate trains everyone to click through"
  shape in slow motion.
- **Next probe:** decide the design fix — scope the scanner off `claudedocs/`, or auto-classify a
  prose-only file that executes no tmux command. 🔴 Deliberately NOT taken unilaterally; classifying
  the doc unblocks `main` but does not close the loop.

### RANK 10: #1600's merged-tree verdict is UNREAD — the run did not finish in session
- **Symptom + exact repro:** `#1600` is green on its own branch at a base 24 commits behind `main`.
  The question is whether the merge it creates is green. Repro:
  `bash /tmp/.../scratchpad/mt1600-run.sh` — or rebuild it: worktree off `origin/main`, merge
  `origin/pr-1600`, run every test file that reads `nix/home.nix` plus the PR's own new file.
- **Observed (with values):** merge is textually clean — `Auto-merging nix/home.nix`, ort strategy,
  `+450/-0` across 2 files, merge commit `01418433` on branch `mt/1600-merged` in worktree
  `/home/zach/workspace/devrc-mt1600`. Selector found **72 test files**. Run reached **~35%** with
  **zero failures so far** before the session ended. `origin/main` did not move during the run
  (still `7e000e6b`), so the merged tree tested is the merged tree that would land.
- **Ruled out:** "the three green checks settle it" — they are a claim about the PR branch at a
  stale base; a clean textual merge is not a clean merge, and the at-risk surface here is a new
  systemd unit against unit ledgers that live in OTHER files. via: code
- **Ruled out:** "`scoped-tests.sh` can answer this" — it exits **4** on `nix/**`, a declared shared
  surface, precisely because its mapper selects only files that NAME what changed and drops every
  target reaching it through an import. via: doc
- **Leading hypothesis:** it passes. The 35% already covered includes much of `scripts/tests/`, and
  the unit is additive with its own master switch. Stated as a hypothesis because **the verdict was
  never read** — this is exactly the "deployed ≠ verified" shape one level up.
- **Next probe:** re-run the script and read `PYTEST_RC=` from
  `scratchpad/mt1600.log`. 🔴 **Read the CONTENT, not the wrapper's exit code** — see the Gotchas
  entry below; the first run of this very script reported wrapper exit 0 over `PYTEST_RC=4`.

### 🔴 RANK 10 SHIPPED TO NEITHER HOST — the workbench is blocked by ANOTHER SESSION'S LIVE WIP
- **Symptom + exact repro:** `bash ~/workspace/devrc/scripts/ship.sh` → **rc 7**. Workbench line:
  `SKIPPED — cannot fast-forward to origin/main: could not switch to the main branch`, blocking file
  `claude/skills/clawgate/SKILL.md`. Re-check with
  `systemctl --user list-timers stale-base-triage.timer --all` (workbench: 0 timers, unit absent).
- **Observed (with values):** the blocking file is **genuinely live WIP, not a stale orphan** — its
  working copy sha1 `978ad806` matches **none** of the last 8 commits of that path, and its mtime was
  **2 minutes old at the moment it was read** (`17:23:38`, read `17:25:49`). The diff is three
  security-relevant RETRACTIONS to the clawgate skill: the LAN UI is *not* open (`/tasks/1` → 303 →
  `/login`), `DELETE /api/tasks/{id}` is `requireHookToken` not unauthenticated (`server.go:699`),
  and a credential-less `POST /agents` on the LAN returns **401**. Someone is writing that file now.
- **Ruled out:** "`ship.sh` failed, so nothing deployed" — the LAPTOP leg SUCCEEDED
  (`fast-forwarded main 7e000e6b -> f99d3c1b`, `552 checked, 0 dangling`, `✅ VERIFIED … + switched`).
  Reading only the final `rc=7` would have gotten this backwards in both directions. via: command
- **Ruled out:** "the laptop got the switch, so the sweep is running there" — its timer is
  `UnitFileState=linked` with **0 timers listed** and `journalctl` `-- No entries --`, because the
  `Install` block is `serverMode`-gated and the laptop has no `~/.server-mode`. That is CORRECT
  behaviour, not a second bug. via: command
- **Ruled out:** "just `git checkout origin/main -- <file>` and re-run ship" — that is the remedy
  `ship.sh` itself prints, and here it would **destroy another session's uncommitted work while it is
  mid-edit**. `claude/RULES.md` names docs-in-a-working-tree as unsaved work and names writing into a
  tree another process is writing as its own hazard. **Deliberately NOT taken.** via: code
- **Leading hypothesis:** nothing is wrong with `#1600`. This is purely the documented
  diverged/dirty-host failure mode, and it will clear on its own once the other session commits or
  parks that file — at which point `ship.sh` fast-forwards the workbench normally.
- **Next probe:** re-check whether the file is still dirty, and only then re-ship:
  `git -C ~/workspace/devrc status --short claude/skills/clawgate/SKILL.md` → if clean,
  `bash ~/workspace/devrc/scripts/ship.sh` and **read every per-host line, not the final verdict**,
  then `journalctl --user -u stale-base-triage -n 40 --no-pager` **on the workbench**.
  🔴 If it is STILL dirty, that is the other session's to resolve — hand it over, do not clear it.

### 🔴 RANK 13: the triage bot's FIRST live sweep produced a FALSE `INHERITED`, on a structural blind spot
- **Symptom + exact repro:** `journalctl --user -u stale-base-triage --no-pager | grep -A14 'PR #1603'`
  on the workbench. It ruled `devrc#1603` **INHERITED — likely cured by rebase**. It was not: the red
  was caused by the PR's own diff, and a rebase would not have touched it.
- **Observed (with values):** the bot's stated evidence was
  `scripts/tests/test_runtime_shebangs.py` *is byte-identical at the PR head and the merge-base*
  (`blobs: merge-base 91f2054bd73b  head 91f2054bd73b  main 4e44053c20c2`) *and main has moved it in
  1 commit absent from the head* — `cfdb38997ba4`. **That commit's entire change to that file is an
  18-line ALLOWLIST ENTRY for `scripts/tests/test_nvim_octo.py`**, an unrelated file. It could not
  have cured `#1603`. The true cause was `#1603`'s own new controls in `test_census_scan.py` writing
  env-resolved shebangs; replacing them with `testlib.mockbin.write_exec` turns the guard green
  (9 passed), which is direct causal evidence rather than correlation.
- **Ruled out:** "the verdict was right and my fix was unnecessary" — the guard fails at `8a88f255`
  and passes at `323c6b6b`, with `main` held constant. The cause is in the PR. via: measurement
- **Ruled out:** "a rebase would have cured it anyway" — the only commit `main` had on that file is
  an allowlist row naming a different file; nothing in it reaches `test_census_scan.py`. via: command
- **Ruled out:** "this is a one-off / bad luck" — the mechanism is structural, see below.
  via: code
- **Leading hypothesis — and it is precise.** The bot's INHERITED test is *"the failing TEST FILE is
  unchanged in my branch, and `main` moved it in commits I lack."* That is sound for a test which
  exercises code it names, and **systematically wrong for a repo-wide CENSUS/SCANNER guard**, where
  the test file scans OTHER files and the offending change lives somewhere else entirely. For that
  whole class, "the guard file is unchanged in my branch" is the NORMAL state of a genuine breakage,
  so the heuristic fires exactly backwards. 🔴 **This repo is dense with that class** —
  `_KILL_MENTION_LEDGER`, `_OWN_BOUND_LEDGER`, `test_runtime_shebangs.py`, and the targets
  `#1603` itself was built to screen. It is the same class rank 9 records as "fixed in both known
  instances, unaddressed as a class". **So the bot's worst false-positive mode coincides with this
  repo's most common way of reddening `main`.**
- **Next probe:** hand-check the other four INHERITED verdicts from this sweep
  (`#1450 #1286 #1194 #1038`) against the same question — *is the named failing test a census/scanner
  guard over files it does not name?* That converts one confirmed false positive into a RATE, which
  is what the arming decision actually needs. ⚠ **Do not assume the other four are also false** —
  `#1450` is 174 commits behind and may well be genuine; only `#1603` has been checked.
