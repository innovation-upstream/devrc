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

- **Branch / PR:** nothing in flight in `devrc` from this session yet. Two items are IN FLIGHT
  in background agents — see Next steps, ranks 1 and 2. Local `devrc` clone is level with
  `origin/main` at `af78acb6` (fetched 2026-09-11).
- **The arc's commit ledger, carried forward** (do not lose these — `State now` is REPLACED on
  every update): `#1429` a0839ec4, `#1445` cace96d9, `#1469` 86b1ddec, `#1471` 4ab87a64,
  `#1482` 972fbcbd, `#1488` d835fe51, `#1489` b315cdd3.
- ⚠ **A SHIP IS PROBABLY DUE, and this is a claim about git, not about the hosts.** Both hosts
  were converged and VERIFIED at `86b1ddec` (`ship.sh` rc 0; workbench 584 managed artifacts /
  0 dangling, laptop 530 / 0 dangling, over nebula `10.42.0.100` — LAN `192.168.50.155` not
  answering is normal). `origin/main` has since moved to `af78acb6` via other sessions' merges,
  so that verification **no longer describes the current tip**. Re-run `ship.sh` and read every
  per-host line; rc 19 means the two hosts landed on different shas while each leg read green.
- **RANK 1 — the review is DONE and the operator has DECIDED.** `ZacxDev/homelab-infra#792`
  reviewed at head `60b7b8f81`. **Operator's call: merge in dry-run, arm later — NOT arm now.**
  Execution is IN FLIGHT: correct the PR body, rebase onto trunk, wait for `gitops-validate`
  on the new head, merge with `CLOSED_PR_MODE: dry-run`, then verify the Flux reconcile in the
  cluster. Arming is explicitly out of scope for that change.
- **RANK 2 — IN FLIGHT, not closed.** An agent is running the enumerated mutation sweep and
  round 3 on `scripts/main-status-watch.py` in an isolated worktree. **No result yet; do not
  record one.** If this doc is read before that agent's PR appears, the ladder is still open.
- **The consumer is still live and behaving.** Re-verified 2026-09-11 00:25 CDT, not taken from
  the previous doc: `main-status-watch.timer` ActiveState=active, 10-min cadence (last 00:25:22,
  next 00:35:22). Newest run:
  ```
  main-status-watch: no authoritative tekton/devrc-main-* verdict in the newest 20 commits …
    Nothing is claimed about main. The 4-hourly deadman still covers it.
    An open red episode (if any) is left open: absence is not a fix.
  ```
  That is the absence-is-not-green arm working as designed — a *different* path from the GREEN
  reading the previous doc recorded, so both arms are now observed in production.
- **`clawgate-task:` still deliberately NOT recorded.** `clawgate_handoff.sh resolve` exited **5**
  again this session. Its positive control fired (the same endpoint answered 9 links for another
  session, so the board is reachable and the token accepted) — but that is the narrower claim: a
  wrong session id also answers `200` with an empty array. Not a clean bill of health.

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

1. **`ZacxDev/homelab-infra#792` — merge in dry-run, then decide arming on the soak.**
   IN FLIGHT: the review is done, the operator has chosen (b) merge-dry-run-arm-later, and an
   agent is executing the merge. **Do not re-review it and do not arm it.** What remains for a
   future session is the ARMING decision, on evidence, after 3–7 days of normal devrc activity.
   Arm only when ALL FOUR hold, read from Loki (`{app="tekton-supersede"}`, which survives the
   pods' `ttlSecondsAfterFinished: 600`):
   (i) ≥1 line `DRY-RUN would cancel <run> (<owner/repo>#<n> was merged before this run finished)`
   naming a run you would have wanted cancelled; (ii) you hand-check 2–3 of those PRs and every
   one was genuinely closed *before* that tick — zero lines naming a PR that was open;
   (iii) `states_read == resolvable` **with `resolvable >= 1`** (`0 == 0` satisfies the terse
   discriminator vacuously); (iv) no sustained `budget of …s spent` or `no installation token`
   lines. 🔴 **Zero `DRY-RUN would cancel` lines after 7 days of normal merging is NOT a clean
   bill — it is the instrument failing to see the bucket it was built for. Do not arm on it.**
   Arming is one line in `supersede-cronjob.yaml` plus the matching literal at
   `test_supersede_logic.py:2251`; the test forces both into the same commit.
   forcing: user — the operator made the dry-run/arm-later call and owns the arming decision.
2. **Close `#1469`'s audit ladder.** IN FLIGHT in a background agent (enumerated sweep + round 3).
   🔴 Running unattended on both hosts every 10 min, so the ladder is open on LIVE code.
   Files: `scripts/main-status-watch.py`, `scripts/tests/test_main_status_watch.py`.
   **If no PR from that agent exists when you read this, the work did not land — redo it.**
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
7. **Pin the `repo-full-name` invariant in `homelab-infra`'s supersede tests** (SHOULD-FIX from the
   #792 review, deliberately deferred out of that PR). Once #792 is armed, pass 2's correctness rests
   on `repo-full-name` naming the repo the PR number belongs to, and `test_supersede_wiring.py`
   contains **zero** occurrences of the string. The tree is correct today — 15/15 supersede-capable
   resourcetemplates wire `repo-full-name: $(tt.params.git-repo-full-name)`, none defaults it — and
   `SUPERSEDE_TEMPLATES` is a two-way ledger, so an unregistered 16th template fails the suite. The
   gap is a *ledger-registered* template that hardcodes the repo. 🔴 That failure is not a clean 404:
   devrc has 1400+ PRs and homelab-infra 790+, mostly closed, so a wrong-repo query is more likely to
   return a false `"closed"` than a miss. Fix: extend `test_vetr_crossrepo_e2e_wiring.py:184-189`'s
   assertion over all of `SUPERSEDE_TEMPLATES`. Repo: `ZacxDev/homelab-infra` (clone:
   `~/workspace/homelab-talos`).
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
journalctl --user -u main-status-watch.service -n 8 --no-pager
#  expect: 10-min cadence, and a line that is EITHER a GREEN/RED verdict OR
#  "no authoritative … verdict in the newest 20 commits" — both are healthy arms.

# rank 1: did the dry-run merge land AND reconcile? two separate claims.
gh pr view 792 --repo ZacxDev/homelab-infra --json state,mergeCommit
kubectl -n tekton-ci get cronjob tekton-supersede -o yaml | grep -A1 CLOSED_PR_MODE
#  expect: state MERGED, and the LIVE cronjob env reading dry-run (not just the git file)

# rank 1 arming evidence — read these before even considering CLOSED_PR_MODE: on
#   {app="tekton-supersede"} |= "DRY-RUN would cancel"
#   {app="tekton-supersede"} |= "closed-pr pass:"

# rank 2: did the ladder actually close? a PR must exist.
gh pr list --repo innovation-upstream/devrc --state all --search "main-status-watch" --limit 5

# both hosts on one sha (rc 19 if they disagree; read EVERY per-host line, not the verdict)
bash ~/workspace/devrc/scripts/ship.sh

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
