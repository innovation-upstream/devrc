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

- **closing-condition:** `check` — every live rank carries an external forcing function and
  resolves: rank 8 armed or explicitly deferred past 2026-09-18, rank 6 decided on/after
  2026-10-11, rank 16's two guards green on `origin/main`, and `#1681` merged. When those four
  are true this doc is DONE and anything further opens a new arc.
  ⚠ **Added 2026-09-14, RETROSPECTIVELY** — this doc predates rule (m) and was grandfathered. It
  is therefore a reconstruction of the finish line, not one frozen at round 1, and it deliberately
  names only what is ALREADY ranked rather than absorbing later asks.

## State now

- ✅ **THE ARC IS CLOSED, `main` IS GREEN, AND IT IS DEPLOYED TO BOTH HOSTS.** Rank 14's
  four-round ladder finished this session (`#1681`), both unowned reds are fixed and merged
  (`#1692`, `#1690`), and `ship.sh` converged workbench + laptop. Ranks 1–16 are all tombstones;
  **rank 8's 2026-09-18 date is the only thing left with a clock on it.**
- **Commit ledger** (`State now` is REPLACED every update — re-carry it or it is lost):
  `#1429` a0839ec4 · `#1445` cace96d9 · `#1469` 86b1ddec · `#1471` 4ab87a64 · `#1482` 972fbcbd ·
  `#1488` d835fe51 · `#1489` b315cdd3 · `#1502` ffef57bc · `#1512` 189689c1 · `#1567` 6f1867b1 ·
  `#1524` 58bfb747 · `homelab-infra#799` 0b14768a · `#1600` f99d3c1b · `#1613` 264de70d ·
  `#1603` 14daa42a · `#1629` f839e720 · `#1625` b9f40f82 · `#1631` ced40bdb · `#1640` f45bb86e ·
  `#1645` 3a3bede7 · `#1647` 3ff2bef9 · `#1648` a2c84a1c · `#1654` cfe4eb54 · `#1658` 00afff8c ·
  `#1671` 2b27bdae · `#1672` 42efad37 · `#1681` 053aa1f4 · `#1683` 8881029e ·
  `#1692` **ff5585ef** · `#1690` **f88e5235** · `#1693` **6a629a45**.
- ✅ **`main` IS GREEN, MEASURED ON THE REAL TIP — not on a constructed tree.** A `git archive` of
  `origin/main` **`f88e5235`**, zero PR content: **644 passed, 0 failed** across
  `test_runtime_shebangs.py`, `test_clawgate_writeback_guard.py` and
  `test_clawgate_task_interview_guard.py` (which owns the SKILL.md ceiling). The same trio was
  **2 failed** on every tip from `6c8c94d2` through `8881029e`.
  ⚠ **The pre-merge gate was NOT re-run after its base moved, by operator decision.** The
  combined merged-tree run (`053aa1f4` + both branches, merge `555baa62`) was 644 passed, then
  `#1683` moved `main` to `8881029e` and that claim expired unrefreshed. What redeems it is the
  post-merge reading above, which is a STRONGER claim than the one skipped — but it arrived
  AFTER the merge, so for the duration of the merge itself the gate was stale. Recorded because
  the honest version of "it was fine" is "it was fine, and we found out afterwards".
- ⚠ **NEITHER FIX WAS AUDITED.** The pre-merge audit was offered and declined for `#1690`
  (a 69 B restore, every claim a measurement) and not run for `#1692`. `#1692` carries a stated,
  UNCLOSED gap: *"nothing execs these fixtures" is measured, not enforced by a test* — the count
  ledger bounds it by failing on a new site, which is a forcing function, not a proof.
- ✅ **DEPLOYED AND VERIFIED, stated separately from "merged" because they are different claims.**
  `ship.sh` rc **0**: both hosts at **`f88e5235`**, cross-host agreement CONFIRMED
  (`local=workbench remote=laptop`), 598 / 557 managed artifacts resolve, **0 dangling, 0 stale**
  on each. That matters here because `#1690` changes `claude/skills/clawgate/SKILL.md`, a
  nix-managed path a `git pull` cannot move.
  ⚠ **The hosts sit one commit behind `origin/main` (`6a629a45`) and that is FINE** — the only
  commit since is this handoff doc, under `claudedocs/`, which nix does not deploy. Do not
  re-ship for it.
- 🔴 **ONE HOST CARRIES CONTENT THAT IS ON NO BRANCH — the workbench, and it is NOT this arc's
  work.** See the dated gotcha below. Nothing to fix here; it is another session's live WIP.
- **CI IMPACT, MEASURED — the arc's two halves have OPPOSITE answers.** Local delivering
  (`gate.sh` 1.92→0.76 runs per merged PR, `nix build .#checks` 2.83→0.73, `scoped-tests.sh`
  0→0.67); **CI wall time never moved and could NOT have** — see rank 5. Unchanged this session.
- **No `clawgate-task:` recorded.** `resolve` exited **5** (0 tasks) twice this session, each
  time with its positive control showing the board reachable — which proves a correct id WOULD
  have resolved, **not** that this session touched no task. Not a clean bill of health.
- **Claim `main-red-shebang-and-writeback-guards` is RELEASED.** Base clone fast-forwarded to
  `6a629a45`; every worktree this session made is removed with nothing stranded.

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
the END. **Ranks 1–16 are CLOSED tombstones.** Live: **8** (DUE 2026-09-18) and **6**
(DUE 2026-10-11), **5** (ANSWERED, NOT DELIVERED — measured). 🔴 **With rank 16 closed, `main` is
green and NOTHING here is blocking** — rank 8's date is the only thing with a clock on it.

1. **CLOSED** — `homelab-infra#792` merged dry-run (`dbe47814`). Arming is rank 8.  forcing: none
2. **CLOSED** — `#1469`'s ladder, `#1502`, shipped and consumer-verified.  forcing: none
3. **CLOSED — the store-api flake was fixed by `#1458`.** ⚠ `#1512` is no longer the measurement of
   record: superseded by an ANCESTRY split over 400 PR heads — **0 of 99** verdicts on heads carrying
   `ce9b55c3` vs **12 of 298** that do not, P(0) ≈ **0.017** (`devrc#1568` `8114a124`; table at
   `handoff-gate-flake-store-api.md` rank 1). Keep `#1512` for "it was never the worst flake".
   forcing: none
4. **CLOSED** — `#1524` merged `58bfb747`, shipped, consumer verified.  forcing: none
5. **ANSWERED — NO PR, BOTH CANDIDATE FIXES REFUTED.** `step-pytests` is **1177s = 94.4%** of a
   1247s gate pod (23 pods, 11h); `scripts/tests` is **582–778s** at 14,327 of 22,518 tests. **Test
   execution is the cost, not dependency realisation.** (a) **No cache left to hit** — the warm
   node-pinned `/nix` PVC was removed in `homelab-talos 3c53d618a`; per-run `emptyDir`, no
   substituters, min `step-pytests` **956s**. (b) **Excluding `claudedocs/` would blind real gates**
   — proven by turning the gate red with a one-word edit to this doc.
   ⏳ One lever remains, in `homelab-talos`: `limits.cpu` 4→8 for `step-pytests`, `requests.cpu`
   stays 2 (worker count follows the cgroup quota — `-n 4` because the LIMIT is 4, not `nproc`).
   ⚠ Distinct from the reverted `23887675`/`bb62668f`, which raised the REQUEST. ⚠ 3–6 min saving is
   INHERITED, not re-derived. Bursting to 8 produced the loopback-starvation flakes. **Scratch
   pipeline, never `devrc-ci`.**
   🔴 **2026-09-14 — MEASURED: CI HAS NOT GOT FASTER, AND NOTHING SHIPPED COULD HAVE MADE IT.**
   123 real `tekton/devrc-main-pytests` runs over 852 `main` commits (durations from
   `/commits/{sha}/statuses`, `error` rows and 28 sub-300s nix-cache replays excluded and named):
   median **1082s before `#1429` → 1260s after**, a monotone climb with **no step at the
   boundary**. Normalised it is FLAT — **48–57 s per 1,000 collected tests** throughout — so the
   growth is SUITE GROWTH (19,440 → 23,089 collected in 14 days), not regression.
   🔴 **`#1429` could not have moved CI by construction**: it changed `min(nproc, 4)` →
   `min(nproc, quota, 8)`, and in the `devrc-ci` pod both yield **4** — `run-tests.sh`'s own
   comment says so. Verified live on `homelab-talos` `trunk`: `requests.cpu: "2"`,
   `limits.cpu: "4"`, unchanged. **This rank is the arc's one UNDELIVERED objective.**
   forcing: none — but the operator's original question was "can we make it faster", and for CI
   the answer is still no.
6. **The flake screen in `main-status-watch.py` is probably inert** — decide on/after **2026-10-11**,
   together with rank 4 (15 lines there vs ~885 in `#1524`). 🔴 Evidence cuts toward DELETE: the
   store-api flake is at **0 of 99** post-fix, and **100 of 101** failure descriptions truncate at
   138 of the 140-char cap, making the screen unsatisfiable. ⚠ Both are READ-TIME populations that
   cannot be re-derived. ⚠ Not a decision — the date stands.
   forcing: none
7. **CLOSED — `homelab-infra#799` `0b14768a`.** Test-only; reconciled to nothing.  forcing: none
8. 🔴 **ARM `#792` (`CLOSED_PR_MODE: on`) — SOAK TO ~2026-09-18, THEN ARM.** Operator set the date.
   Read `{app="tekton-supersede"} |= "DRY-RUN would cancel"` / `|= "closed-pr pass:"` in Loki,
   hand-check 2–3 named PRs, then flip `supersede-cronjob.yaml` + the pinned literal at
   `test_supersede_logic.py:2251` (re-read the line number; `#799` has landed since).
   🔴 Zero `DRY-RUN would cancel` after a week is NOT a clean bill. ⚠ Dry-run short-circuits before
   the re-read guard. 🔴 **Rank 13 is the WORKED EXAMPLE for this call** — a dry-run soak exposed a
   **2-of-4 false rate** in a sibling tool that every mutation sweep and merged-tree gate had passed,
   because the defect was in its REASONING about real repo history. **Ask what `#792`'s soak
   structurally cannot see, and probe its selector against ground truth.**
   ✅ **2026-09-14 — the soak is LIVE and NOT SILENT** (the check this item demands): live env
   `CLOSED_PR_MODE = dry-run`, cronjob running `* * * * *` for 16 days, and a real hit in the
   current logs — `DRY-RUN would cancel devrc-ci-bhrld (innovation-upstream/devrc#1663 was
   merged before this run finished)`, `closed-pr pass: mode=dry-run resolvable=6 closed=1
   skipped=277`. Nothing to do until the date; the evidence precondition is satisfied.
   forcing: deadline — 2026-09-18.
9. **CLOSED by `#1561` + `#1567`.** 🔴 The CLASS is mitigated, not closed: `#1603`'s
   `ledger-check.sh` DETECTS it in 229s and `#1629` stops the triage bot being fooled by it, but
   nothing PREVENTS a new instance. ⚠ **The count is TWO known instances, not three** — see the
   correction in `State now`; the needle guard was a documented false-positive range, not this class.
   forcing: none — owned by nobody.
10. **CLOSED — `#1600` `f99d3c1b`**, shipped, consumer VERIFIED RUNNING (2h timer, dry-run pinned by
    value, one clean run `Result=success` `ExecMainStatus=10`).  forcing: none
11. **CLOSED — `#1603` `14daa42a`**, shipped, consumer VERIFIED (244 nodeids, 392 passed, **229s** —
    not the 166s this doc once claimed).  forcing: none
12. **CLOSED — `#1613` `264de70d`**, merged through a red proven unreachable from its diff.
    forcing: none
13. **CLOSED — `#1629` `f839e720`**, shipped, CONSUMER-VERIFIED on the real verdicts: `#1450`/`#1286`
    stay INHERITED, `#1194` demotes, 244 guards in 18.7s. ⚠ Fixes the bot's REASONING; does NOT arm
    it — arming is still a separate, evidence-gated call.
    forcing: none
14. **CLOSED — `#1671` `2b27bdae`**, verified by CONTENT. A render race: the wait gated the PANE
    GRID while the test asserted on the COPY-MODE snapshot, a different object. Fixed in the
    fixture; the redundant sibling guard DELETED (it survived every mutation of the shipped
    `.tmux.conf`, and the silent `copy_cursor_hyperlink`-removal class is caught only by
    `spans_the_wrap`). ✅ **ROUND 3 LANDED TOO — `#1681` `053aa1f4`**, verified by CONTENT
    (`origin/main`'s copy of the file is byte-identical to the PR head's, md5 `5c1c8753`), which
    retires the tautological reachability guard (`#{copy_cursor_hyperlink}` reads at the CURSOR,
    which on entry sits past the link, so it read `''` regardless). Gated on the MERGED tree
    before merging, not on the PR branch: touched file **6 passed × 3 runs**, and the
    inherited-red control unchanged at **exactly 2** — which is what made the red inherited
    rather than introduced. **The ladder is four rounds and is OVER: round 3 was the last, and
    it produced no round 4** — `claude/RULES.md` says a clean round ENDS the ladder.
    forcing: none
15. **CLOSED — `#1648` `a2c84a1c`, shipped and consumer-verified on the deployed tool.**
    `handoff_doc.py` now warns an author, in the proposal run above the diff, when an update would
    put the doc over its budget. 🔴 **It WARNS and never REFUSES, and that is forced**: a blocking
    check deadlocks against `handoff-write-guard.py`, which blocks Stop until a handoff is written,
    so a session on a doc already grandfathered OVER the ceiling could neither record its work nor
    end its turn. Pinned by `test_the_over_budget_warning_REFUSES_NOTHING`. Ceiling/step/ledger moved
    to `scripts/lib/handoff_budget.py`; `test_handoff_doc_size.py` still owns the policy.
    ⚠ Closes the FEEDBACK gap, not the budget problem.
    forcing: none
16. **CLOSED — `#1692` `ff5585ef` (shebang scan) + `#1690` `f88e5235` (writeback seam), and
    `main` VERIFIED GREEN on the real tip `f88e5235`: 644 passed, 0 failed, zero PR content.**
    The operator answered: RED 2 → restore the literal; RED 1 → *"do we need this? can we
    simplify?"*, answered by measurement rather than by picking an option. 🔴 **The framing this
    item inherited — "guard-vs-guard conflicts" needing a gate to yield — is REFUTED for BOTH.**
    RED 2 was not a three-way pull (the #1621 guard does not cover `~/.claude/…`, and the ceiling
    had 125 B of headroom); RED 1's literals turned out to be TEST DATA for a production parser,
    so no guard had to yield there either. Detail + every measurement is in the Open
    investigations block below. Claim `main-red-shebang-and-writeback-guards` still HELD —
    release it when both merge.
    forcing: gate — until both land, every PR still inherits both reds, and a permanently-red
    gate trains click-through.

\n\n## Open investigations — live diagnosis state

### RESOLVED — rank 16's two reds: BOTH "guard-vs-guard conflict" readings were wrong
- as-of: 2026-09-14
- 🔴 **SUPERSEDES the block above it** (`main` is RED on two UNOWNED guards …). That block's
  framing — two gates in genuine conflict, needing an operator to decide which one yields — is
  **refuted for both reds**, and its "Next probe: none needed, this is an operator decision" is
  no longer the instruction to follow. Its *observations* (42 hits, all in `test_guard_core.py`,
  all from `9b8969a8`; `FLOW_DEPLOYED` absent from SKILL.md) all reproduced exactly.
- **RED 2 — measured, not argued.** The claimed three-way pull had only one live leg. #1621's
  handle guard does **not** cover this path: `test_absolute_handle_paths.py` says so in its own
  source (*"NO handle names `.claude`, so arming `~` for every handle reports ZERO
  `~/.claude/…` findings"*) and uses that very shape as an example in its guidance text. And the
  ceiling had room — SKILL.md was **15540 B against 15665**, so restoring the sentence #1640
  evicted cost **69 B** and landed at **15609** (56 B headroom). `#1690`, 742 passed.
- **RED 1 — the literals are TEST DATA for a production parser, which inverts the question.**
  `guard_core._runs_as_shell` carries a branch existing solely to resolve an env-based
  interpreter line (`<env> bash`, and the `-S VAR=1 bash` form), because a DIRECTLY-executed
  file is handed to whatever that line names. Mutating `interp == "env"` → False (narrowest
  expression, `count == 1`, `PYTHONDONTWRITEBYTECODE=1`) **KILLS**
  `test_every_invocation_shape_that_runs_the_file_is_DENIED[{p}]` and
  `[timeout 60 {p} /some/arg]` — and kills them for the branch's own reason: the kill is ALLOWED
  because the file stopped being read as shell. Only the two DIRECT-execution shapes die, which
  is the only arm that consults an interpreter line at all.
- 🔴 **Ruled out — "just respell them `/bin/sh`" (the fix that looks obvious and is wrong).**
  With `_CRASH1_BODY` respelled AND the same mutant applied, all **7** invocation shapes PASS:
  the mutant **SURVIVES**. The respell buys green by making the branch UNTESTED, not by making
  it unnecessary. via: measurement
- **Ruled out — `testlib.mockbin.write_exec`.** It owns a `/bin/sh` line and RAISES on a
  supplied one, so it is the respell above wearing a helper's clothes — unavailable here for
  exactly the reason the control shows. via: code
- **Ruled out — the sandbox hazard is present at these sites.** It needs the file EXECUTED. The
  file has **18** subprocess/exec sites and **0** of them lie in the fixture span (4676–6244);
  the bodies go to `gc.evaluate()` as TEXT. Positive control on that search: the same query
  finds all 18 elsewhere, so the zero is a reading, not a wiring fault. via: measurement
- **Ruled out — a plain allowlist entry is enough.** An entry keyed on `(path, substring)`
  covering 40 lines pre-approves the 41st for free, in a 6,000-line file whose SUBJECT is
  interpreter lines. So `#1692` gives the two entries a **COUNT** (`PINNED_COUNTS`), making it a
  ledger over 42 known lines. Negative controls, each applied to a `cp -a` copy and reverted to
  byte-identical (`cmp`): extra site → RED · site removed → count RED *and* scan RED (a
  `/bin/sh` line is still an offender under shape 1) · orphan key → both new tests RED.
  via: measurement
- ⚠ **STATED, NOT ENFORCED:** "nothing execs these fixtures" is measured, not pinned by a test —
  a static "does this span exec anything" check would go quietly vacuous. What bounds it is the
  count ledger: a new site fails the suite and forces someone to re-verify the premise.
- **Next probe:** none for the diagnosis. What remains is a decision: audit `#1690`/`#1692`
  (offered, unanswered) and merge, then `claim-work --release
  main-red-shebang-and-writeback-guards`.

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
# rank 14 / #1681 landed — by CONTENT, never ancestry (squash merge)
git -C ~/workspace/devrc show origin/main:scripts/tests/test_tmux_hyperlink_open.py | md5sum
#   5c1c87536fd65523a8c4a12ffe2c227a  — byte-identical to the PR head's copy

# rank 16 — main GREEN, on the real tip, with zero PR content. Re-measure; do not trust this age.
S=$(mktemp -d); git -C ~/workspace/devrc archive origin/main | tar -x -C "$S"
(cd "$S" && nix develop ~/workspace/devrc -c python3 -m pytest \
  "$S/scripts/tests/test_runtime_shebangs.py" \
  "$S/scripts/claude-hooks/tests/test_clawgate_writeback_guard.py" \
  "$S/scripts/claude-hooks/tests/test_clawgate_task_interview_guard.py" \
  -q -p no:cacheprovider --rootdir="$S")        # 644 passed at f88e5235

# the counted pin can go RED in BOTH directions — the control that makes it a ledger
python3 - <<'EOF'
import sys; sys.path.insert(0, '/home/zach/workspace/devrc/scripts')
from pathlib import Path
from testlib import shebang_scan as S
hits = S.scan_file(Path('/home/zach/workspace/devrc/scripts/claude-hooks/tests/test_guard_core.py'))
print(sum('env bash' in l for _, l in hits), sum('env python3' in l for _, l in hits))  # 40 2
EOF
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
  rather than cleared — and it resolved itself within the hour, after which `ship.sh` exited 0
  and converged both hosts.** A dirty file that blocks your deploy is not thereby yours to resolve.
  ⚠ This incident was written up TWICE under an identical `###` heading; `gotchas` is an APPEND
  bucket, so the merge kept both and neither author could see the other. De-duplicated 2026-09-14.
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

### 2026-09-12 — closing the arc: what the merged-tree discipline actually cost and bought

- 🔴 **A MERGED-TREE CLAIM EXPIRED MID-SESSION AND THE RE-RUN WAS PAID, NOT REASONED PAST.**
  `#1603`'s first merged-tree run came back clean (3665 passed / 0 failed, 24 files, 27 min) and
  `main` moved during it — gaining a commit touching `scripts/lib/transcript_search.py`, a declared
  shared surface. `claude/RULES.md` is explicit that the trigger is **the base MOVED**, not "we
  touched the same thing", and that a file-overlap check is *a cheaper, different claim*. Re-run on
  the new base: clean again, and that time the base held. **The second run is the one that justified
  the merge; the first justified nothing by the time it finished.**
- **Ordering matters when several PRs are queued.** `#1613` was merged FIRST and `#1603`'s tree
  rebuilt on top of it, because landing `#1613` after the re-run would have expired the re-run
  immediately. **With a moving `main`, sequence the merges so each gate is the last thing that
  happens before its own merge.**
- 🔴 **MERGING THROUGH A RED WAS A DECISION, AND IT IS SAID OUT LOUD.** `#1613` merged with
  `tekton/devrc-pytests` RED. The red was shown unreachable from the diff rather than waved off:
  the diff is ONE markdown file; the failing test (`test_mention_open.py`) has **zero** references to
  `claudedocs` or `handoff` (grep `rc=1`, checked without a `| head` that would have swallowed the
  status); the same test passed on `#1610` whose status posted **12 minutes later**; and the test
  drives REAL interactive fzf through a PTY. **Structural unreachability, not a probability
  argument — that is the standard for merging through a red.**
- ⚠ **The measured runtime of `ledger-check.sh` is 229s, not the 166s this doc carried.**
  derive 27.8s + run 201.4s, 392 passed, on the workbench at 275% CPU. Not a retraction of the
  earlier number — different tree, different load — but **229s is the figure to quote**, because a
  loaded box is the condition the tool is actually used under. This doc has already been burned once
  by a performance figure that was true when written (the RETRACTED contention table).
- **`grep -c` returning 0 makes a shell read exit 1 on a perfectly clean run.** Twice this session a
  status line reported failure over a green result for that reason alone. The documented
  count-not-exit-code rule, in miniature: **the content was the verdict every time.**
- ⚠ **`gh pr merge --delete-branch` failed on BOTH `#1600` and `#1613`** — each branch was pinned by
  a worktree (a dead agent's, and my own). The merge itself succeeded both times; only the local
  branch delete failed. **Check the worktree before assuming either that work is stranded or that
  the merge did not happen** — on `#1600` the pinning worktree was clean and its single commit's
  content was exactly what had landed.

### 2026-09-12 — rank 13's probe: testing the claim instead of arguing about it

- 🔴 **"INHERITED — LIKELY CURED BY REBASE" IS A FALSIFIABLE CLAIM, AND FALSIFYING IT COST MINUTES.**
  Build the merged tree, run ONLY the named failing test: passes ⇒ right, fails ⇒ false. Four PRs,
  four answers, each test under 4 seconds. **The temptation was to reason from the test's name about
  whether it "looked like" a census guard; the merged tree answers the actual question and the
  reasoning would have been a guess dressed as analysis.**
- **The one-data-point predicate SURVIVED contact with more data, which is not the usual outcome.**
  With only `#1603` in hand the proposed discriminator was *is the named failing test a census guard
  over files it does not name?* On four cases it separates 4/4 — and it did NOT merely confirm a
  prior: it also predicted the two TRUE positives correctly, which is the half that could have
  falsified it.
- 🔴 **A 50% FALSE RATE IS A DIFFERENT OBJECT FROM ONE FALSE POSITIVE.** One is an anecdote that
  invites "it was unlucky"; a rate with a named mechanism and a clean split is a specification for
  the fix. **Rank 13 went from "decide whether this can ever be armed" to "implement this demotion
  and re-run the sweep" purely by spending ten minutes measuring.**
- ⚠ **A MERGE CONFLICT IS NOT A PASS AND NOT A FAILURE.** `#1038` (601 commits behind, 1 conflicting
  path) has no merged tree, so its verdict is UNTESTABLE and is excluded from the rate rather than
  quietly counted. The probe script reports it as its own outcome — the same discipline as
  `COULD NOT MEASURE` elsewhere in this repo. **A denominator of 4, stated, beats a denominator of 5
  that hides one.**
- **The probe script refuses to read silence as success**: it greps for a countable
  `N passed`/`N failed` line and reports `COULD NOT MEASURE — no countable verdict` otherwise,
  because a pytest selection that matches nothing prints `no tests ran` and **exits 0** — the
  silent-zero family this doc has now been bitten by three separate ways.

### 2026-09-13 — implementing rank 13: the fix, and what implementing it taught

- 🔴 **MY OWN TEST CAUGHT A FAIL-OPEN BUG IN MY OWN DESIGN, AND IT WAS THE BUG I WAS FIXING.**
  `census_scan.analyze()` on a mis-rooted path **RETURNS AN EMPTY RESULT rather than raising**. The
  first `CensusIndex` trusted that, so a wrong root would have answered "not a census guard" for
  every test in the repo — failing OPEN into precisely the false-INHERITED bug under repair. Caught
  only because the fail-safe was written as a test driven at a real empty directory rather than
  asserted about a stub. **Write the unhappy path as a test against reality, not as a comment.**
- 🔴 **MY FIRST FLOOR WAS THE WRONG SHAPE AND THE SUITE SAID SO IMMEDIATELY.** Mirroring
  `ledger-check.sh`'s `MIN_NODEIDS` (a count floor) turned **every end-to-end fixture repo** into
  COULD NOT MEASURE: a small repo with genuinely no census guards is a TRUE answer, not a broken
  scan. The trip is now `parsed == 0` — "did the scan read anything at all" — and the
  production-strength claim lives in the suite as a positive control. **A guard that cannot
  distinguish "small" from "broken" fails the wrong way.**
- 🔴 **I DESTROYED MY OWN UNCOMMITTED IMPLEMENTATION WITH THE MUTATION BATTERY.** The battery
  restored between mutants with `git checkout -- <file>` against a tree whose changes were **never
  committed**, reverting the entire implementation to `origin/main`. Recovered by re-applying all
  six blocks with `count == 1` assertions, re-verifying at 106 passed, and committing BEFORE
  re-running. **A mutation battery needs a COMMITTED baseline, not merely a green one** — this is
  `claude/RULES.md`'s "restore from `cp -a`, not `git checkout --`" with the emphasis moved to
  *when* you are allowed to start.
- **The battery itself then worked: 4/4 killed**, control green both ends, tree restored clean —
  drop the index at the call site → wiring guard red; screen computes but never acts → demotion
  red; fail open on unbuildable → fail-safe red; drop the `parsed == 0` trip → mis-rooted red.
- 🔴 **"RED AT BASE" WAS STRUCTURAL, NOT BEHAVIOURAL, AND SAYING SO MATTERS.** The 9 base failures
  are all `AttributeError: no attribute 'CensusIndex'` — they prove the tests need the new code,
  NOT that behaviour changed. The behavioural delta is pinned separately on both sides with the
  IDENTICAL fixture: base asserts ALPHA is INHERITED (still green at HEAD), the new test asserts the
  same fixture demotes, and the two differ ONLY in the oracle's answer. **A regression matrix that
  is really an import error should be labelled as one.**
- **The screen defaults to OFF (`census=None`), so its WIRING is what can rot** — dropping the
  argument at the one production call site would make it silently inert while all 106 tests still
  pass. Pinned STRUCTURALLY over the AST at both call sites, with a positive control proving the
  scan can see the spelling it forbids.
- ⚠ **`mapfile` DOES NOT EXIST IN zsh, and the Bash tool runs zsh.** An inline selector using it
  found **0 files**; the run refused on its own floor instead of reporting a green over nothing.
  Put any `mapfile`/array selector in a `#!/usr/bin/env bash` script file.
- ⚠ **`grep … | head` returns HEAD's status, so `|| echo "none"` never fires.** Hit twice in one
  session while checking reachability, and once it truncated a `find` so a tracked file looked
  absent. **Capture to a variable and branch on `$?`.**

### 2026-09-13 — two PRs, two unreachable reds, two different causes

- 🔴 **BOTH OPEN PRs WERE RED ON TESTS THEIR DIFFS COULD NOT REACH, AND THE TWO REDS HAD DIFFERENT
  CAUSES.** `#1625` (one markdown file) red on `test_mjs_parses[attachments.mjs]`; `#1629`
  (`stale-base-triage.py` only) red on `test_tmux_stores_the_hyperlink_and_can_report_it`. Same
  `failed=2`, same shape, and it would have been easy to call both "the same flake". They are not:
  **`test_mjs_parses` passes 34/34 on clean `main`**, while **the tmux one fails 2 of 3 runs there**.
  **Measure each red against a clean checkout of `main` before grouping them.**
- 🔴 **`main`'s OWN STATUSES COULD NOT ANSWER WHETHER `main` WAS RED** — the three newest commits
  read `pending`, `superseded by a newer run`, and `KILLED: the gate pod died`. That is this doc's
  own "only ~21% of `main` commits get an authoritative verdict", met in practice. **A clean-checkout
  probe answered in seconds what the status API could not answer at all.**
- **`head -2` truncated a `find` and made a TRACKED file look absent.** `attachments.mjs` appeared
  to exist only inside nested agent worktrees; it is tracked and present on `main`, and the two
  worktree copies simply sorted first. **A truncated listing is not an inventory.**
- ⚠ **`ship.sh` rc 19 — HOSTS DISAGREE — on a run where every per-host line was green.**
  `origin/main` moved between the two legs' fetches (another PR merged mid-run), so the workbench
  landed on `b9f40f82` and the laptop on `f5942a24`; each host really was at `origin/main` as IT saw
  it. The second pass converged both. **The verdict is a claim about the FLEET agreeing on one sha,
  and it is not reducible to the per-host lines.**

### 2026-09-13 — rate evidence for rank 9's close, moved somewhere it cannot be dropped

- **`#1561`'s close is backed by a RATE, not just a merge, and the figure kept getting dropped.**
  The `_KILL_MENTION_LEDGER` census guard accounts for **22** `tekton/devrc-pytests` reds across PR
  heads, and **every one of them predates `c0bbd6d9`** — the commit after which the kill scanners no
  longer read `claudedocs/`. The second instance of the same class,
  `scripts/tests/test_runner_bound_ledger.py`, accounts for **5** reds (**4** after `c0bbd6d9`) and
  was closed by `#1567` `6f1867b1`, verified **5 passed** at `origin/main` `337114e0`.
  ⚠ Both populations are READ-TIME ONLY and cannot be re-derived — GitHub keeps one status per
  context and supersedes overwrite it (see `handoff-gate-flake-store-api.md` rank 1). A later
  disagreement is not a refutation.
- 🔴 **WHY THIS IS HERE RATHER THAN IN RANK 9:** it lived under `Next steps`, which is REPLACED on
  every update, and `handoff_doc.py`'s durable-drop warning caught it being deleted — after the
  write, because that update was run with `--confirm --push` in one pass instead of proposing
  first. `Gotchas` is APPEND-only, so the sha survives here permanently. **A measured figure that
  matters belongs under an APPEND heading; a REPLACE section is for status, and status is exactly
  what gets overwritten.**

### 2026-09-13 — chasing one red found another, and one of them was mine

- 🔴 **A RED I WAS GOING TO CALL "INHERITED" WAS PARTLY MINE.** `#1640`'s CI failed on
  `test_no_handoff_doc_exceeds_its_budget`, and my diff touched no handoff doc — the textbook
  unreachable-red shape. Probing clean `main` showed THREE violators and **one was the doc I had
  grown in three merges that same session**. ⚠ **"My diff cannot reach it" and "I did not cause it"
  are different claims**, and the gap between them is everything a session did EARLIER. Check the
  second one before reaching for the word inherited.
- 🔴 **TWO SECTIONS SHARING A HEADING ARE NOT NECESSARILY DUPLICATES.** `### 2026-09-12 — ship.sh rc
  7` appears twice in this doc and I was about to delete one to save 2,032 B. **0 of the shorter
  copy's 10 sentences appear in the longer** — same title, different content, written by two of my
  own updates. Compare bodies, never titles.
- 🔴 **THREE OF MY OWN SURVIVAL-CHECK NEEDLES WERE THE DEFECT, NOT THE CONTENT**, in one session:
  `does not trust Authelia` (spans a line break), `pre-start note` (the flow file says
  "pre-start **comment**"), and `mtime was **2 minutes old**` (asterisks placed differently). Every
  one reported LOST for content that was present. **Normalise whitespace, and confirm a miss against
  the defining file before believing your own grep.**
- **The prune ladders are worth following in order.** `ledger-check.sh`'s ceiling wanted an EVICTION
  (remedy 1, "usually the whole answer") and that is exactly what both fixes turned out to be —
  demote verbatim to a sidecar, keep the imperative, leave a pointer. Neither needed a raised
  number, which both playbooks rank LAST.
- ⚠ **`skill-audit.py` quotes the GENERIC 12,288 B skill budget even for a file governed by its own
  ceiling.** clawgate's gate is 15665, in
  `test_clawgate_task_interview_guard.py::test_the_skill_did_not_grow`. The prune-skill doc says to
  believe the governing gate over the generic number — read it before chasing 3 KB you do not owe.
- 🔴 **READING THE DEMOTED FILE END TO END CAUGHT TWO DEFECTS EVERY STRUCTURAL CHECK PASSED**: a
  sidecar section that began mid-sentence (the slice cut after the subject, which stayed in the
  core), and a fact stated twice. Ceiling green, paths resolved, no orphans, survival check clean,
  170-line gap audit clean. **Slicing guarantees content survives; it does not guarantee the result
  is readable.**
- ⚠ **My own CI waiter read "no checks reported" as settled.** After a push resets the checks, the
  absence of a `pending` row is not a verdict. Fixed to require all three checks PRESENT and
  settled. Four stale waiters also fired with empty output and **exit 0** — the shape that reads as
  success.

### 2026-09-13 — closing out: what a "closed" arc still had outstanding

- 🔴 **THE RANKED LIST SAID RANK 15 WAS OPEN AFTER I HAD ALREADY SHIPPED IT.** `#1647` filed rank 15
  as 🔴 actionable; `#1648` closed it twenty minutes later; nothing updated the doc in between, so
  `main` carried an open item pointing at work that was merged, shipped and verified. A next session
  would have `claim-work`ed it and rebuilt it — the exact duplicate-work failure that cost this arc
  two PRs (`#1558`/`#1559`) earlier. **Found only by being asked "is anything outstanding?" and
  CHECKING the doc on `main` rather than reciting what I remembered doing.**
  Generalise: **when one PR files a ranked item and a later PR closes it, the doc is stale between
  them, and the window is invisible** — the ranked list is the queue every `/resume` draws from, so
  a stale OPEN is worse than a stale CLOSED.
- **Worktree hygiene is part of finishing.** Eleven worktrees and thirteen branches were left behind
  by this arc's merged-tree gates and fix branches. All were clean, and every one's work was
  verified ON `main` BY CONTENT before removal — **their `commits-not-on-main` counts were 1–8 and
  that is the SQUASH TRAP, not evidence of stranded work**; ancestry says unmerged forever after a
  squash. ⚠ Eight similarly-named worktrees in that directory belong to OTHER sessions and were left
  untouched; a name pattern is not ownership.
- ⚠ **The rank-9 class produced a THIRD instance today, and it is still red on `main`.**
  `test_NO_TRACKED_FILE_ASSERTS_the_RETRACTED_two_entry_boundary` fires on a handoff doc whose prose
  DOCUMENTS a grep that was wrongly quoted as a clean sweep — spelling the needle to explain the
  trap is what trips the guard. Same shape as `_KILL_MENTION_LEDGER` and `test_runtime_shebangs.py`.
  **Three in one day is a rate, not a coincidence.**

### 2026-09-13 — the close-out, and two of my own claims that did not survive it

- 🔴 **I RECORDED A THIRD INSTANCE OF THE RANK-9 CLASS THAT WAS NOT ONE.**
  `test_NO_TRACKED_FILE_ASSERTS_the_RETRACTED_two_entry_boundary` fired on a handoff doc, and I
  wrote it up as "a guard tripped by prose documenting that guard — three in one day is a rate".
  **`#1655` (another session) shows it is a documented FALSE-POSITIVE RANGE of a deliberately-short
  needle**: the doc quotes the phrase as a grep PATTERN and never asserts it, and the needle's own
  comment predicts exactly this and prescribes reword-or-marker. **The pattern I "found" was me
  fitting a new observation to the class I had spent the day thinking about.** A rate claimed from
  three points, one of which was a different phenomenon, is not a rate.
- 🔴 **AND I LEFT IT ON `main` AS "STILL RED" AFTER SOMEBODY FIXED IT.** `#1654` merged with that
  claim; `#1655` had already landed. **Re-measure before a close-out asserts a red** — a handoff's
  last update is the one nobody re-checks.
- ⚠ **ONE GREEN IS NOT A VERDICT ON A FLAKY TEST.** Rank 14's tmux test PASSED in the same run that
  produced these corrections. Measured earlier at 1 passed / 2 failed in 3 runs, so that pass is a
  sample, not a fix — recorded here because the next reader will see one green and be tempted.
- **The close-out found a real gap by CHECKING rather than reciting.** Asked whether anything was
  outstanding, reading the doc on `main` showed **rank 15 still marked 🔴 OPEN twenty minutes after
  `#1648` shipped it** — `#1647` filed it, `#1648` closed it, nothing updated the doc between. A
  next session would have claimed and rebuilt it. **When one PR files a ranked item and a later PR
  closes it, the doc is stale in the window between, and that window is invisible.**
- **Worktree hygiene is part of finishing.** Eleven worktrees and thirteen branches were removed;
  all clean, each verified on `main` BY CONTENT first, because their `commits-not-on-main` counts of
  1–8 are the SQUASH TRAP, not stranded work. ⚠ Eight similarly-named worktrees in that directory
  belong to OTHER sessions and were left alone — **a name pattern is not ownership.**
- **The budget warning shipped in `#1648` fired on its own author's close-out write** (`873 B left`),
  and the right response was to take its advice: two more CLOSED dated sections went to
  `claudedocs/refs/` in the same change, ending at 8,287 B of headroom. ⚠ The `#792` review section
  was deliberately NOT evicted — rank 8 is live and dated, and that section is its arming evidence.

### 2026-09-14 — is the gate work DELIVERING? Local yes, CI no, and one reading retracted mid-analysis

- 🔴 **THE TWO HALVES OF THIS ARC HAVE OPPOSITE ANSWERS, AND THE SPLIT IS BY CONSTRUCTION.** The
  local iteration loop improved and is being used; **CI wall time never moved and could not have**.
  `#1429` changed `min(nproc, 4)` → `min(nproc, cgroup quota, 8)`; in the `devrc-ci` pod
  (`limits.cpu: "4"`, verified still 4 on `homelab-talos` `trunk`) both formulas yield **4**. The
  only lever that would move CI — rank 5's `limits.cpu` 4→8 — has no PR. **Read a perf change's
  MECHANISM before measuring its effect**: the before/after alone would have been read as a
  regression caused by the change, and it is neither caused by it nor a regression.
- **CI, measured:** 123 real runs over 852 `main` commits. Median **1082s → 1260s** across `#1429`,
  but per-test cost is FLAT (**48–57 s / 1,000 collected tests**) and collected grew
  **19,440 → 23,089 in 14 days**. The gate is getting slower because the suite is growing ~1.3%/day.
- **Local, measured two ways, both agreeing:** per merged PR `gate.sh` **1.92 → 0.76**,
  `nix build .#checks` **2.83 → 0.73**, `scoped-tests.sh` **0 → 0.67**; per 1,000 Bash calls
  **3.39 → 1.80**, **5.01 → 1.72**, **0 → 1.57**. The positive control (`git`) rose 186 → 195, so
  this is SUBSTITUTION, not a quiet week.
- 🔴 **RETRACTED MID-ANALYSIS: "merge throughput doubled (21.6 → 43.2/day)".** The before-window
  (Sep 1–8) was a local TROUGH; late August already ran ~40/day, so the after is a RETURN to
  baseline, not a step. **A before/after is a claim about the window you chose** — extend the
  baseline before attributing a step to your own change. Caught only by re-fetching a longer
  history; `gh pr list --limit 400` had silently truncated the earlier count.
- ⚠ **Two instrument traps, both of which would have produced a confident wrong number.**
  (a) `/commits/{sha}/statuses` durations are **bimodal**: 28 of 151 authoritative runs finished in
  **47–63 s** with a full `collected=21426` line — nix **cache replays**, because the check derivation
  is keyed on the TREE and a squash-merge of an already-built PR head rebuilds nothing. Folding them
  in dragged the "before" median down and manufactured an improvement that was never there.
  (b) Raw `grep`-shaped counting of tool names counts `grep gate.sh` as an invocation; requiring an
  invocation SHAPE moved `gate.sh` from 3,793 matches to 1,184.
- **The cluster could not answer this at all** — Tekton retains ~3.5 h of PipelineRuns (45 devrc-ci,
  all from today). GitHub's per-status timestamps are the only durable record of CI duration here.

### 2026-09-14 — a four-round ladder in which every round's own PROSE was the next defect

- 🔴 **THREE CONSECUTIVE ROUNDS FOUND A FALSE CLAIM WRITTEN BY THE PREVIOUS ROUND'S FIX**, and in
  each case the CODE was fine. The shape is not "the fix was wrong", it is "the sentence explaining
  the fix asserted a measurement that did not hold". Round 3's own tally: three of its four findings
  were prose. **Budget for this — it is the ladder's steady state, not an anomaly.**
- 🔴 **A REACHABILITY GUARD CAN BE A TAUTOLOGY, AND IT LOOKS EXACTLY LIKE A REAL ONE.** Round 2
  added a test to prove a retry was reached, guarded by `assert empty != URI`. But
  `#{copy_cursor_hyperlink}` reads at the COPY CURSOR, which on entering copy mode sits one cell
  PAST a link ending at row 1 col 19 — so it read `''` whether or not the link had landed and the
  assert could never fail. **The control that exposes it is a PAIR, not a mutation**: break the
  thing AND perturb the timing. Merged guard → 6 passed (mutant SURVIVES); fixed guard → 1 failed.
- 🔴 **"BREAKABLE" IS NOT "REACHED".** Mutating `cancel` to a no-op left the whole file GREEN,
  because a module-scoped fixture had already rendered the grid before any test ran, so the retry's
  body past iteration 1 never executed. The fix was a test that drives its OWN server with the link
  emitted LATE — which needed the helper parameterised by socket. **Ask what state no existing test
  can produce, then build it.**
- 🔴 **AN AUDIT'S RECOMMENDED FIX IS A HYPOTHESIS.** Round 2 said "assert `cancel`'s rc, or branch
  on `#{pane_in_mode}`". I implemented the second and it HUNG. Measured: it is a COUNT of stacked
  modes (`1` on a clean pane, not a boolean), and here the pane starts in `view-mode` because the
  fixture's fake `HOME` makes the conf's `session-created` hooks fail — so one `cancel` pops 2→1,
  never 0. Control: shipped conf → view-mode 8/8; `-f /dev/null` → none 8/8.
- ⚠ **I REFUTED ONE AUDIT FINDING WITH A CONTROL, AND WAS RIGHT TO.** Round 1 called a claim
  unreproducible at 0-of-40; re-measured on the same tree, **17 failed of 24 at load 36**. Two of
  three measurements reproduce it. **Re-measure before accepting a refutation, exactly as before
  accepting a finding.**
- **A repro pinned to a test NAME dies silently when the test is deleted** — rank 14's documented
  repro would have become `5 deselected`, exit 5, ~0.1s: indistinguishable from a pass.

### 2026-09-14 — `ship.sh` bakes another session's UNCOMMITTED work into the deployed generation

- 🔴 **MEASURED, on this run: the workbench's deployed generation is `origin/main` PLUS two
  staged-but-uncommitted paths**, and `ship.sh` said so itself — `🔴 DIRTY AND IN THE ARTIFACT —
  nix reads 2 path(s) at eval/build time`, naming `claude/skills/the-algorithm/SKILL.md` and
  `claude/skill-tiers.json`. The laptop line on the same run reads `(clean tree)`. **The run
  still exits 0 and still says `✅ VERIFIED`** — this is a warning among greens, on one host's
  lines, which is exactly the shape CLAUDE.md says to read per-host rather than by verdict.
- **The proof it is real, not a formality:** `git cat-file -e origin/main:claude/skills/the-algorithm/SKILL.md`
  → `exists on disk, but not in 'origin/main'`, while `readlink -f
  ~/.claude/skills/the-algorithm/SKILL.md` terminates in `/nix/store/…-devrc-claude-skills/`.
  **A skill that is on no branch is live on the workbench** — it appears in this session's own
  skill listing. `readlink -f` is the arbiter, per RULES.md, and it says DEPLOYED.
- **Whose:** another session, actively — the files were **74 seconds old** and **staged** when
  ship ran, and the dirty set had grown from 2 paths to **13** twenty minutes later (a
  skill-description/tier pass: 9 `SKILL.md` files, `claude/skill-tiers.json`,
  `scripts/tests/test_skill_descriptions.py`). **Left untouched** — a dirty file that lands in
  your deploy is not thereby yours to resolve, the same call this doc records for the
  `ship.sh` rc 7 incident.
- 🔴 **The generalisation, which is the durable half:** `ship.sh` deploys the **working tree**,
  not the **committed tree**, wherever nix reads a path at eval/build time. So *"I shipped
  `origin/main`"* is false on any host with a dirty nix-read path, and the divergence is
  invisible to `git log`, to `drift-check`'s git-parity arm (both hosts ARE at the same sha) and
  to the managed-artifact scan (0 dangling, 0 stale — the links resolve; they just resolve to
  bytes no branch has). **The warning line is the only detector.**
- ⚠ **It self-heals only if that session commits.** If the work is abandoned, the workbench keeps
  running a skill nobody can find in git until the next switch rebuilds without it — and nothing
  announces that either. Worth a `git status --short` on both hosts before quoting any host as
  "at `origin/main`".
## Open investigations — live diagnosis state

### CLOSED investigation blocks — evicted 2026-09-13

Eight blocks that reached an answer (rank 2's ladder, rank 10's unread verdict, rank 10's
ship-to-neither-host, rank 13's first false-INHERITED reading, and — 2026-09-14 — rank 13's
PROBE RUN, and — also 2026-09-14 — three CLOSED dated gotcha sections from
rank 4, rank 11 and the four-rank round) were moved verbatim to
`claudedocs/refs/gate-speed-and-ci-signal.md` when this doc went over its budget and reddened
`main`. They are CLOSED, so the eviction costs a reader nothing live — but that file is **not**
indexed by `handoff_search`, so go to it by path.


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

### ✅ RESOLVED 2026-09-14 — RANK 14's flaky tmux test: a render race, fixed in `#1671`
- **Resolution:** the Next probe's FIRST option (poll with a bounded deadline) shipped as a
  `rendered` fixture, and its second (delete the guard) shipped too — for the test that was
  measured to detect nothing the sibling does not. 🔴 **The block below is the ORIGINAL diagnosis,
  kept verbatim; its leading hypothesis was RIGHT.** Two corrections to what was built on it: the
  ranked item's "strongest argument for deleting it" was read as a recommendation and is not one
  (this block's own Next probe names polling first and says the fix is the author's call), and the
  1-passed/2-failed figure is one sample of a LOAD-DEPENDENT rate, not a property of the test.
- 🔴 **THE "EXACT REPRO" BELOW IS NOW A SILENT ZERO — DO NOT RUN IT AND READ THE RESULT.** It
  selects `-k stores_the_hyperlink`, and `#1671` DELETES that test, so after it merges the command
  matches nothing and exits **5** with `5 deselected` in ~0.1s. That reads exactly like "fixed".
  The live equivalent is `-k spans_the_wrap`. **A repro pinned to a test NAME dies silently when
  the test is renamed or removed** — the zero it returns is indistinguishable from a pass, which is
  the same shape as this doc's own `#{copy_cursor_hyperlink}` finding: an absent thing answering
  empty at rc 0.

### 🔴 RANK 14 (ORIGINAL, superseded by the block above): a flaky tmux test on `main` is reddening unrelated PRs
- as-of: 2026-09-13
- **Symptom + exact repro:** on a clean checkout of `origin/main`,
  `nix develop ~/workspace/devrc -c python3 -m pytest
  scripts/tests/test_tmux_hyperlink_open.py -k stores_the_hyperlink -q` — run it **three times**.
- **Observed (with values):** **1 passed / 2 failed in 3 consecutive runs** on `origin/main`
  `80266b76`, same tree, same command, nothing else changed. The failure is
  `assert URI in out.stdout` where **`out.stdout` is EMPTY** — `capture-pane -p -H -t t` returned
  nothing, `returncode == 0`. Local `tmux 3.7c`. It also reddened `#1629`'s CI
  (`FAILING: test_tmux_stores_the_hyperlink_and_can_report_it | … failed=2`), whose diff touches
  only `scripts/stale-base-triage.py` and its test and cannot reach tmux.
- **Ruled out:** "`#1629` caused it" — the diff cannot reach tmux, and the test fails on a clean
  `origin/main` worktree with no PR content at all. via: measurement
- **Ruled out:** "`main` is deterministically RED" — run 1 PASSED. This is a flake, not a break,
  and the distinction changes who must act and how urgently. via: measurement
- **Ruled out:** "`capture-pane -H` is unsupported here" — the flag parses; the failure is an
  EMPTY capture, not an error, and it succeeds on some runs. via: command
- **Leading hypothesis:** a race between the tmux server rendering the OSC 8 sequence into the grid
  and `capture-pane` reading it. The test's own docstring calls it an **INVARIANT GUARD, not
  regression coverage**, and notes it passed before the fix too — so it is a guard whose failure
  costs everyone a red PR while proving nothing about the change that introduced it.
- **Next probe:** `git log --diff-filter=A -- scripts/tests/test_tmux_hyperlink_open.py` to confirm
  it arrived with `c794c9a7` (`#1622`, OSC 8 hyperlinks), then either make the capture wait for the
  URI to appear (poll with a bounded deadline) or delete the guard. 🔴 **Deliberately NOT taken
  unilaterally — it is another session's test, days old, and `claude/RULES.md` says a flaky test is
  FIXABLE rather than re-runnable, but the fix is the author's call.**

### `main` is RED on two UNOWNED guards, and BOTH are guard-vs-guard conflicts rather than bugs
- as-of: 2026-09-14
- **Symptom + exact repro:** on a clean `origin/main` `6c8c94d2` worktree with zero PR content,
  `pytest scripts/tests/test_runtime_shebangs.py scripts/claude-hooks/tests/test_clawgate_writeback_guard.py`
  → **2 failed**. CI reports the pair as `failed=2` on every PR head.
- **Observed (with values) — RED 1, `test_no_test_writes_a_usr_bin_env_shebang_at_runtime`:**
  **42 unpinned hits, ALL in `scripts/claude-hooks/tests/test_guard_core.py`**, all introduced by
  ONE commit — `9b8969a8` (#1551), which taught the wide-kill guard to see kills *inside executed
  scripts* and therefore had to write scripts. `git blame` on 4676 / 5161 / 6244 all → `9b8969a8`.
- **Observed — RED 2, `test_the_skill_names_the_same_DEPLOYED_path_the_hook_prints`:** the hook's
  `FLOW_DEPLOYED = "~/.claude/skills/clawgate/flows/task-pickup.md"`
  (`clawgate-writeback-guard.py:496`); `claude/skills/clawgate/SKILL.md` contains that literal
  **0** times (it says `flows/task-pickup.md`). The task-authoring path is **0** too.
- **Ruled out:** "someone is already on them" — no `claim-work` entry, and no open PR touches
  either file. via: command
- **Ruled out:** "RED 1 is fixed by swapping the interpreter" — replaced all 40
  `#!/usr/bin/env bash` with `#!/bin/sh`: **1639 passed** in `test_guard_core.py` (so the env
  shebang is NOT load-bearing) but the scan **still fails** — it flags EVERY shebang a test
  writes, not just `/usr/bin/env`. via: measurement
- **Ruled out:** "just allowlist it" — the guard's own header says *"Do not add an ALLOWLIST entry
  to get green"*, and one entry keyed on `#!` would exempt a 6,000-line file from a repo-wide
  safety guard forever. via: doc
- **Leading hypothesis:** RED 1 is rank 9's class generalised — *a guard banning pattern X is
  tripped by the suite whose SUBJECT is X* (one fixture deliberately tests a **lying** shebang, so
  its shebang is genuinely under test). RED 2 is a three-way pull: this seam guard requires the
  literal path, `df09a6c2` (#1621) gates *against* a skill spelling out a path a handle names, and
  `f45bb86e` (#1640) evicted content to satisfy the size ceiling.
- **Next probe:** none needed — this is an operator decision, already put to them. RED 1:
  allowlist-with-evidence (verified: these scripts are never dispatched via their shebang; they are
  scanned, or invoked as `bash <path>`, which ignores it) **vs** routing all 42 through
  `mockbin.write_exec`. RED 2: decide which of the three gates yields.
