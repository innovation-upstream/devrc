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

**Merged and verified by content on `origin/main`:**

| PR | what it shipped |
|---|---|
| `#1429` a0839ec4 | xdist workers sized from the **cgroup quota** (not `nproc`, which lies in a container), cap 8; `gate.sh` re-execs into `nix develop` itself; merge-gate policy rewritten |
| `#1445` cace96d9 | `run-tests.sh --files` change-scoped runs + `scripts/scoped-tests.sh`; `SCOPE: FULL\|PARTIAL\|SCOPED\|NONE\|UNKNOWN` machine contract that `gate.sh` refuses to emit a PASS off |
| `#1469` 86b1ddec | `scripts/main-status-watch.py` + user timer: watches `main`'s own CI status, starts the `main-green-check` deadman early on an authoritative red |
| `#1471` 4ab87a64 | the declared reason protection is off — **was conditional and had expired**; now unconditional |
| `#1482` 972fbcbd | two more stale copies of that reason, one in the file #1471 edited |

**Open:** `ZacxDev/homelab-infra#792` — cancels CI runs whose PR already merged. Ships `DRY_RUN`/
`dry-run`, **not armed**; the literal is pinned by a test so arming costs a visible line. GitOps —
merging deploys. **Needs operator review.**

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
the END; inserting mid-list silently re-points every live claim.

1. **Review `ZacxDev/homelab-infra#792`** and decide whether to arm it (`CLOSED_PR_MODE: on`). GitOps
   — merging deploys. Its dry run over 254 live objects cancelled nothing, with `states_read=1`
   beside `closed=0` proving GitHub was actually asked.
   forcing: operator
2. **Close #1469's audit ladder.** Round 3 never ran, and its 95-mutant enumerated sweep **died
   mid-run with its verdict unknown** — it had already surfaced one genuine survivor. The 🔴 is
   fixed and verified (138 passed, fail-loud rc 12), but the ladder is open.
   forcing: none
3. **The one genuine flake: `TestARefusedWriteIsIndistinguishableFromAnAbsentOne::test_POSITIVE_
   CONTROL…`** in `scripts/tests/test_subsystem_store_api.py` — 5 of 26 failure heads. Its own
   docstring (`:7360`) says **#1432 is NOT a fix** and that whether the port race affects its rate is
   UNKNOWN. Needs a real diagnosis, not another ported retry. Load-sensitive.
   forcing: none
4. **Stale PR bases re-report already-fixed reds** — 8 of 8 failing open PRs were 5–42 commits
   behind. A rebase cured 4 of them outright (proven: the fix commit is on `main` and not an ancestor
   of their heads). A bot comment naming the fix would stop humans triaging cured reds. `strict:true`
   is deliberately off and correctly so.
   forcing: none
5. **The 19-min CI median.** `pytests` is 90–95% of it. The local loop is solved; CI still pays full
   freight on every push. Note `devrc-ci-5m64b` ran `pytests` in **52s** on a nix cache hit — an
   unchanged derivation is already near-free, so the cost is entirely rebuild-on-change.
   forcing: none
6. **The flake screen in `main-status-watch.py` is probably inert** — at current status-description
   lengths (140-char cap) it will likely never fire. Its author proposed deleting it if still dead in
   a month. Decide on/after **2026-10-11**; the deadman's double-run is the real defence.
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
