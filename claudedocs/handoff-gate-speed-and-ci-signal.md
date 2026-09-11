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

- **RANKS 1 AND 2 ARE BOTH CLOSED, MERGED, SHIPPED AND VERIFIED LIVE.** Both claims released.
- **The arc's commit ledger, carried forward** (`State now` is REPLACED every update, so this
  list must be re-carried or it is lost): `#1429` a0839ec4, `#1445` cace96d9, `#1469` 86b1ddec,
  `#1471` 4ab87a64, `#1482` 972fbcbd, `#1488` d835fe51, `#1489` b315cdd3, `#1502` ffef57bc.
- **RANK 1 — `ZacxDev/homelab-infra#792` MERGED IN DRY-RUN** (operator's decision; NOT armed).
  Merge `dbe47814`, 06:19:03Z, off the rebased head `cc362946` — all 5 Tekton contexts green,
  read per-status. Four separate claims, each measured:
  - on `trunk` **by content** (`CLOSED_PR_MODE: "dry-run"`) — ancestry is useless, the repo squashes;
  - **reconciled** — Kustomization `tekton-triggers` `f364c499` → `dbe47814` at 06:21:06Z;
  - **consumer running it** — sweep logs `mode=dry-run`;
  - **wrote nothing** — 0 of 329 PipelineRuns carry `ci.zacx.dev/cancelled-because`, and all three
    runs named in DRY-RUN lines have empty `spec.status`. Pass 1 stays armed (`dry_run=False`).
- 🔴 **RANK 1's ARMING EVIDENCE ARRIVED ON THE FIRST TICK, not in 3–7 days.** Two sweeps so far:
  `resolvable=10 states_read=10 closed=3` and `resolvable=16 states_read=16 closed=3`, each naming
  devrc #1499 / #1497 / #1500, all three hand-verified as genuinely merged, zero naming an open PR.
  So criteria (i), (ii), (iii) are met NON-VACUOUSLY. ⚠ **Still do not arm on this**: (iv) needs
  sustained observation, it is two samples, and — see Gotchas — the one case it exercised is
  precisely the one dry-run structurally CANNOT validate.
- **RANK 2 — `#1502` MERGED as `ffef57bc`**, and the defect it fixes was LIVE on `main` until then.
  `ship.sh` rc 0, **both hosts at `ffef57bc` and the two shas actually COMPARED** (not
  `NOT COMPARED`): workbench ff `f76f3c11`→`ffef57bc`, 586 artifacts / 0 dangling, 411 / 0 stale;
  laptop ff `cc278b7a`→`ffef57bc`, 532 / 0 dangling, 393 / 0 stale; both `✅ VERIFIED … + switched`.
- 🔴 **The CONSUMER is verified, and this unit has an unusual deploy path worth knowing.**
  `ExecStart=…%h/workspace/devrc/scripts/main-status-watch.py` — it runs **directly from the
  working tree**, so a host's checkout advancing IS the deploy; there is no `/nix/store` copy to
  go stale. Verified in the executed file: bare `return RC_UNMEASURED` count **3 → 2**, the fix's
  own comment present, timer active on its 10-min cadence, last run `ExecMainStatus=0`,
  `NRestarts=0`. (`ActiveState=inactive/dead` between firings is correct for a oneshot.)

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

🔴 **RANKS ARE IDENTITY** — `claim-work --slug-for <this doc> <rank>` before acting on one. New items
go at the END; inserting mid-list silently re-points every live claim. **Ranks 1 and 2 are CLOSED
and deliberately left in place as tombstones** — renumbering would re-point every live claim on
this doc.

1. **CLOSED — `homelab-infra#792` merged in dry-run.** ⚠ What remains is the ARMING decision, which
   is a NEW item at rank 8, not this one. Do not re-open this rank.
   forcing: none
2. **CLOSED — `#1469`'s audit ladder, merged as `#1502` `ffef57bc`, shipped and verified live.**
   forcing: none
3. **The one genuine flake:** `TestARefusedWriteIsIndistinguishableFromAnAbsentOne::test_POSITIVE_
   CONTROL…` in `scripts/tests/test_subsystem_store_api.py` — 5 of 26 failure heads. Its own
   docstring (`:7360`) states **#1432 is NOT a fix** and that whether the port race moves its rate is
   UNKNOWN. Needs a diagnosis, not another ported retry. Load-sensitive.
   forcing: gate — it reddens the only automated signal at random.
4. **Stale PR bases re-report already-fixed reds** — 8 of 8 failing open PRs were 5–42 commits behind;
   a rebase cured 4 outright. A bot comment naming the fix would stop humans triaging cured reds.
   `strict: true` is deliberately off and correctly so.
   forcing: none
5. **The 19-min CI median.** `pytests` is 90–95% of it. ⚠ `devrc-ci-5m64b` ran `pytests` in **52s**
   on a nix cache hit, so an unchanged derivation is already near-free — the cost is entirely
   rebuild-on-change.
   forcing: none
6. **The flake screen in `main-status-watch.py` is probably inert** — decide on/after **2026-10-11**.
   ⚠ `#1502` did NOT delete it; round 6 left it in place. The deadman's double-run is the real defence.
   forcing: none
7. **Pin the `repo-full-name` invariant in `homelab-infra`'s supersede tests.** Now that #792 is
   merged, pass 2's correctness rests on `repo-full-name` naming the repo the PR number belongs to,
   and `test_supersede_wiring.py` contains **zero** occurrences of the string. 15/15 supersede-capable
   resourcetemplates wire it correctly today and `SUPERSEDE_TEMPLATES` is a two-way ledger, so the gap
   is a *ledger-registered* template that hardcodes the repo. 🔴 That fails as a FALSE `"closed"`, not
   a clean 404 — devrc has 1400+ and homelab-infra 790+ mostly-closed PRs. Fix: extend
   `test_vetr_crossrepo_e2e_wiring.py:184-189` over all of `SUPERSEDE_TEMPLATES`. Repo:
   `ZacxDev/homelab-infra` (clone `~/workspace/homelab-talos`).
   forcing: none
8. **Decide whether to ARM `#792` (`CLOSED_PR_MODE: on`).** Criteria (i)–(iii) are already met
   non-vacuously on the first two post-deploy sweeps; (iv) needs sustained observation. Read
   `{app="tekton-supersede"} |= "DRY-RUN would cancel"` and `|= "closed-pr pass:"` in Loki.
   🔴 Zero `DRY-RUN would cancel` lines after 7 days of normal merging is NOT a clean bill — it is
   the instrument failing to see the bucket it was built for. Arming is one line in
   `supersede-cronjob.yaml` plus the pinned literal at `test_supersede_logic.py:2251`, which forces
   both into one commit. **Operator's call.**
   forcing: user — the operator chose merge-in-dry-run-arm-later and owns the arming decision.

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
# rank 2's consumer — this unit runs from the WORKING TREE, so the checkout IS the deploy
grep -c 'return RC_UNMEASURED' ~/workspace/devrc/scripts/main-status-watch.py   # 2 = fixed, 3 = pre-fix
systemctl --user list-timers main-status-watch.timer --all
journalctl --user -u main-status-watch.service -n 6 --no-pager
#  a "no authoritative … verdict in the newest 20 commits" line is a HEALTHY arm, not a fault

# rank 1 — merged AND reconciled AND running are three claims; make them separately
gh pr view 792 --repo ZacxDev/homelab-infra --json state,mergeCommit
KUBECONFIG=$KC_HOMELAB kubectl -n tekton-ci get cronjob tekton-supersede -o yaml | grep -A1 CLOSED_PR_MODE
KUBECONFIG=$KC_HOMELAB kubectl -n tekton-ci logs -l app=tekton-supersede --tail=20 | grep 'closed-pr pass'
#  and the write-nothing control: 0 PipelineRuns should carry ci.zacx.dev/cancelled-because

# both hosts on one sha — read EVERY per-host line, and that it says hosts were COMPARED
bash ~/workspace/devrc/scripts/ship.sh
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
