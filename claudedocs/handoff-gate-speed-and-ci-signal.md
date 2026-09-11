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

- **Branch / PR:** nothing in flight in `devrc`. Six PRs merged this arc — `#1429` a0839ec4,
  `#1445` cace96d9, `#1469` 86b1ddec, `#1471` 4ab87a64, `#1482` 972fbcbd, `#1488` d835fe51 (this doc).
- **DEPLOYED AND VERIFIED LIVE**, which the first version of this doc could only pose as a question.
  `scripts/ship.sh` rc 0, both hosts at `86b1ddec`:
  - workbench — `✅ VERIFIED — on branch main at origin/main (clean tree) + switched`; 584 managed
    artifacts checked, 0 dangling; 409 repo-sourced examined, 0 stale.
  - laptop (over nebula `10.42.0.100`; LAN `192.168.50.155` did not answer, which is normal) —
    `fast-forwarded main 0150d71f -> 86b1ddec`, same VERIFIED line; 530 checked, 0 dangling.
  - ⚠ Both legs read individually AND the shas compared — `ship.sh` returns rc 19 when the two hosts
    land on different shas, and every per-host line can be green while that happens.
- 🔴 **The CONSUMER is live, not just the deploy.** A deploy reporting success is a claim about the
  deploy; this is the unit:
  ```
  main-status-watch.timer   ActiveState=active   next 2026-09-11 00:05 CDT
  main-status-watch: newest main verdict: GREEN at ce9b55c3 (walked 20) — nothing to do.
  ```
  It walked 20 commits, found green, did nothing, exited 0. `SuccessExitStatus=10 11`, so the rc 12
  the round-2 fix introduced will correctly FAIL the unit.
- **`#1469` shipped WITH its round-2 fix, narrowly.** The agent that wrote those fixes finished them,
  reported 61 tests passing, deliberately left them UNCOMMITTED so a claims block would record a real
  range — and then died on a session limit. Two modified files sat unstaged in a dead agent worktree,
  one `git checkout` from silent deletion. Rescued and pushed as `3e8d4315`, which became the PR head,
  so the 🔴 fix is on `main` (verified by content: `ladder_exit` ×7, `EPISODE_MAX_AGE_S` ×6).
- **`clawgate-task:` deliberately NOT recorded.** `clawgate_handoff.sh resolve` exited **5** —
  nothing resolved. An unknown session id answers `200` with an empty array, so that result cannot
  distinguish "this session touched no task" from "the id is wrong". It is not a clean bill of health.

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
go at the END; inserting mid-list silently re-points every live claim.

1. **Review `ZacxDev/homelab-infra#792`** and decide whether to arm it (`CLOSED_PR_MODE: on`).
   Ships `dry-run`, with the literal pinned by a test so arming costs a visible line. Its dry run over
   254 live objects cancelled nothing, `states_read=1` beside `closed=0` proving GitHub was asked.
   GitOps — merging deploys.
   forcing: user — the operator asked for it as a PR to review, not to merge.
2. **Close `#1469`'s audit ladder.** Round 3 never ran, and its **95-mutant enumerated sweep died
   mid-run with its verdict unknown** after already surfacing one genuine survivor
   (`print_header`'s sentinel branch) that its hand-written sweeps would never have included.
   🔴 This is now running unattended on both hosts every 10 min, so the ladder is open on LIVE code.
   Files: `scripts/main-status-watch.py`, `scripts/tests/test_main_status_watch.py`.
   forcing: gate — an audit fix resets the verification gate; the ladder is not closed.
3. **The one genuine flake:** `TestARefusedWriteIsIndistinguishableFromAnAbsentOne::test_POSITIVE_
   CONTROL…` in `scripts/tests/test_subsystem_store_api.py` — 5 of 26 failure heads. Its own
   docstring (`:7360`) states **#1432 is NOT a fix** and that whether the port race moves its rate is
   UNKNOWN. Needs a diagnosis, not another ported retry. Load-sensitive.
   forcing: gate — it reddens the only automated signal at random.
4. **Stale PR bases re-report already-fixed reds** — 8 of 8 failing open PRs were 5–42 commits behind;
   a rebase cured 4 outright (proven: the fix commit is on `main` and not an ancestor of their heads).
   A bot comment naming the fix would stop humans triaging cured reds. `strict: true` is deliberately
   off and correctly so.
   forcing: none
5. **The 19-min CI median.** `pytests` is 90–95% of it. The local loop is solved; CI still pays full
   freight per push. ⚠ `devrc-ci-5m64b` ran `pytests` in **52s** on a nix cache hit, so an unchanged
   derivation is already near-free — the cost is entirely rebuild-on-change.
   forcing: none
6. **The flake screen in `main-status-watch.py` is probably inert** — at current status-description
   lengths (140-char cap) it will likely never fire; its author proposed deleting it if still dead in
   a month. Decide on/after **2026-10-11**. The deadman's double-run is the real defence.
   forcing: none

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
# the consumer, not the deploy — this is what "#1469 is live" means
systemctl --user list-timers main-status-watch.timer --all
journalctl --user -u main-status-watch.service -n 5 --no-pager

# both hosts on one sha (rc 19 if they disagree; read EVERY per-host line, not the verdict)
bash ~/workspace/devrc/scripts/ship.sh

# the 18-second claim, on main
nix develop ~/workspace/devrc --command bash -c \
  "cd ~/workspace/devrc && bash scripts/run-tests.sh --files 'scripts/dl-router/tests/test_store.py' ."
#  expect: SCOPE: SCOPED (1 file(s) across 1 of 28 …) + RESULT: PASS, ~18s

# protection is off BY DECISION, and the declaration is unconditional
bash ~/workspace/devrc/scripts/drift-check.sh 2>&1 | grep '^\[protect\]'
#  expect: DECLARED OFF, and live OFF … why: … STANDING preference
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
