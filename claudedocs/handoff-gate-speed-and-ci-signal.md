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

- **RANK 7 IS MERGED. RANK 11's RED WAS REAL AND IS FIXED (CI re-running). RANK 10 IS HELD BEHIND A
  MERGED-TREE RUN THAT IS STILL IN FLIGHT.** Ranks 1, 2, 3, 4, 9 remain closed tombstones; rank 5
  ANSWERED with no PR; ranks 6 and 8 are DATED, not ready.
- **Commit ledger** (`State now` is REPLACED every update — re-carry it or it is lost):
  `#1429` a0839ec4 · `#1445` cace96d9 · `#1469` 86b1ddec · `#1471` 4ab87a64 · `#1482` 972fbcbd ·
  `#1488` d835fe51 · `#1489` b315cdd3 · `#1502` ffef57bc · `#1512` 189689c1 · `#1567` 6f1867b1 ·
  `#1524` 58bfb747 · **`homelab-infra#799` 0b14768a (NEW)**. Closed unmerged on purpose: `#1558`,
  `#1559` (superseded by `#1561`).
- **`ZacxDev/homelab-infra#799` — MERGED `0b14768a`, rank 7 CLOSED.** Verified by CONTENT on `trunk`
  (a squash merge never makes the head an ancestor, so `--is-ancestor` is false forever and is not
  the check). The test-only claim was **re-verified independently of the handoff** before merging:
  2 files, both `scripts/tests/*`, `+190/-0`, **zero** files under `clusters/`/`triggers/`/`apps/`,
  so it reconciles to nothing. `tekton/gitops-validate` green on all 9 legs. It was 4 behind
  `trunk`; those 4 commits are comic-flex UI, two handoffs and a SOPS rotation-ledger test —
  no overlap with supersede wiring in either direction.
- **`devrc#1603` — the red was REAL, was the PR reintroducing the exact failure it exists to
  prevent, and is FIXED at `8a88f255` (pushed; CI re-running as of this writing).** See the
  Findings block below. Branch `feat/ledger-fast-check`, now `CLEAN`/`MERGEABLE`, 16 behind `main`.
- **`devrc#1600` — NOT MERGED, deliberately.** All three checks green and `CLEAN`, but **24 commits
  behind** `main` (tip `7e000e6b`). Its only shared surface is `nix/home.nix`, which **72 test files
  read**, and it adds a **new systemd unit** — the two-way-unit-ledger class that has reddened `main`
  twice on this arc. `scoped-tests.sh` exits 4 on that surface by design, so a merged-tree run over
  those 72 files is the gate. **In flight at ~35% when this was written; its verdict is UNREAD.**
  🔴 **Do not merge #1600 on the strength of its three green checks** — those are a claim about its
  own branch at a base 24 commits stale.
- **Claims held:** `gate-speed-and-ci-signal-10`, `gate-speed-and-ci-signal-11`. Rank 7 was merged
  **without** claiming it first — `claim-work --list` and `gh pr list --state open` were both swept
  and showed no claim and no duplicate on this arc, so nothing collided, but the claim should have
  come first and did not.
- **No `clawgate-task:` field recorded.** `clawgate_handoff.sh resolve` exited **5** — 0 tasks for
  this session, with its positive control showing the board reachable. Per its own instruction that
  is not a clean bill of health (a wrong id also answers 200/`[]`), so no field was written.

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
the END. **Ranks 1–4, 7 and 9 are CLOSED tombstones**; renumbering re-points every live claim.

1. **CLOSED** — `homelab-infra#792` merged in dry-run (`dbe47814`). Arming is rank 8.
   forcing: none
2. **CLOSED** — `#1469`'s ladder, `#1502`, shipped and consumer-verified.
   forcing: none
3. **CLOSED — the store-api flake was already fixed by `#1458`.** ⚠ `#1512` is NO LONGER the
   measurement of record; superseded 2026-09-12 by an ANCESTRY split over 400 PR heads — **0 of 99**
   verdicts on heads carrying `ce9b55c3` against **12 of 298** that do not, P(0) ≈ **0.017** —
   recorded in `devrc#1568` (`8114a124`), full table at `handoff-gate-flake-store-api.md` rank 1.
   ⚠ `#1512`'s table is not WRONG: re-splitting the same 397 verdicts by date reclassified 5 and
   **0 of 101 failures**. Cite the newer read; keep `#1512` for its "it was never the worst flake"
   finding, which stands.
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
   `_FAILING_RE` in that file). So the screen now guards a flake that has stopped occurring, using a
   completeness proof a 140-byte field cannot supply. ⚠ **Both figures are a READ-TIME population that
   cannot be re-derived** (GitHub keeps one status per context and supersedes overwrite it); a later
   disagreement is not a refutation. ⚠ **Not a decision — the 2026-10-11 date and "decide them
   together" both stand**; this is the datum to decide ON, and it did not exist when the date was set.
   forcing: none
7. **CLOSED — `ZacxDev/homelab-infra#799` MERGED `0b14768a` on 2026-09-12.** Verified by content on
   `trunk`; test-only, reconciled to nothing. See `State now`.
   forcing: none
8. 🔴 **ARM `#792` (`CLOSED_PR_MODE: on`) — SOAK UNTIL ~2026-09-18, THEN ARM.** Operator set this
   date. Criteria (i)–(iii) met non-vacuously on the first two post-deploy sweeps; (iv) needs the
   window. Read `{app="tekton-supersede"} |= "DRY-RUN would cancel"` and `|= "closed-pr pass:"` in
   Loki, hand-check 2–3 named PRs, then flip `supersede-cronjob.yaml` + the pinned literal at
   `test_supersede_logic.py:2251` (one commit, by construction).
   🔴 Zero `DRY-RUN would cancel` lines after a week of normal merging is NOT a clean bill — it is
   the instrument failing to see its bucket. ⚠ Dry-run short-circuits BEFORE the re-read guard, so
   the soak cannot exercise the mid-tick race (devrc #1500 merged 14s after a sweep started).
   ⚠ **`#799` has since merged into `trunk`**, so the tree you arm against is not the one the soak
   started on. Zero overlap with the cronjob manifest, but re-read `test_supersede_logic.py:2251`
   rather than trusting the line number.
   forcing: deadline — the operator set 2026-09-18.
9. **CLOSED by `#1561`** — the kill scanners no longer read `claudedocs/`, which ends the treadmill
   rather than paying another round of it. **Rate evidence that the close is real and not merely
   merged:** the kill-mention ledger accounts for **22** `tekton/devrc-pytests` reds across PR heads
   and **every one predates `c0bbd6d9`** — read 2026-09-12 ~04:00Z in `devrc#1568`; see rank 1 of
   `handoff-gate-flake-store-api.md` for why that population is read-time-only and cannot be
   re-derived.
   🔴 **Both instances of the CLASS are now fixed and the class itself is not.** The same design — a
   census over tracked text reddening `main` for everyone — fired next from
   `scripts/tests/test_runner_bound_ledger.py` (**5** reds, **4** after `c0bbd6d9`), closed by
   `#1567` `6f1867b1` (**5 passed** at `origin/main` `337114e0`). **At least two instances in two
   days, both measured here, and nothing prevents the next one.** Tracked as
   `handoff-gate-flake-store-api.md` rank 8, closed as an instance and retained for the class.
   forcing: none — both instances shipped; the class is unaddressed and owned by nobody.
10. 🔴 **MERGE `devrc#1600` — BUT ONLY AFTER READING THE MERGED-TREE VERDICT, WHICH IS UNREAD.**
    The run was ~35% done when the session ended; see the Open investigation above for the exact
    repro. Its three green checks are a claim about a base **24 commits** stale and do not settle it.
    Design reviewed against the diff this session and sound. Timer for `stale-base-triage.py`, **2h**
    (derived: a verdict only changes when a new `devrc-pytests` status lands ~19.6 min after a push,
    or `main` moves a file the red names; measured cost **60 API reads / 43s / 59 PRs ⇒ 0.6% of the
    rate limit**). Ships `--comment-mode dry-run`, **pinned by value**; 17/17 mutants killed.
    **No `OnFailure=notify-failure@`** (every red it sees is somebody's PR, not an incident) and
    **rc 11 FAILS the unit** — the opposite of its siblings, because it has no blind ladder.
    `SuccessExitStatus=10`, `TimeoutStartSec=600` (above the script's own 300s budget).
    `serverMode`-gated to the workbench only — both hosts build the same flake, and once armed two
    hosts would race to post the same comment.
    ⏳ **After merge: `ship.sh`, THEN `journalctl --user -u stale-base-triage -n 40 --no-pager`** —
    two separate claims. Whether the unit runs cleanly under systemd is STILL unverified; no switch
    has been done.
    🔴 **ITS SOAK EVIDENCE IS JOURNAL-ONLY, NOT LOKI** — a systemd-user unit's stdout carries
    `_TRANSPORT=stdout` and alloy's journal source is a default-deny allowlist of
    `kernel|journal|syslog`. Unlike `#792`, you cannot read its dry-run evidence from Loki.
    forcing: none
11. **`devrc#1603` — FIXED AND PUSHED (`8a88f255`); MERGE ONCE CI IS GREEN.** The sandbox-tier red
    was real and was this PR reintroducing the permanently-red-gate failure it exists to prevent —
    full diagnosis in the Gotchas block. Fix verified in **both** tiers, 29 passed each, with the
    tier guard mutation-killed by its own error string.
    The PR's own substance is unchanged and stands: the ledger set is **genuinely DERIVED** (AST
    closure at call time, no list); only a positive control is hardcoded, and a mutant proves it
    reachable. 🔴 Its two-pass design is load-bearing: `public_ip_scan.repo_files(root)` walks its
    own PARAMETER, so a root-aware seed never fires on the shared lister most guards go through —
    **without pass 1, neither incident is visible.** Surface is `scoped-tests.sh` on a file-SET
    change, which closes that script's OWN blind spot (its mapper selects tests that NAME what you
    changed; a new file names nothing — exactly how both reds happened). Both historical reds
    reproduced and caught, clean either side. ⚠ **166s, i.e. ~3 minutes — NOT the "seconds" this
    rank originally claimed**; ~7× the tier. A 2× narrower filter was REJECTED because its false
    negatives re-open the hole.
    ⚠ **CI was still `pending` on the new head when this was written — the verdict is UNREAD.**
    Read `gh pr checks 1603` before merging, and remember the measured ~42–48% not-success rate:
    read the failing test's name and ask whether the diff can reach it rather than acting on colour.
    ⚠ 16 behind `main`; its files are `scripts/{ledger-check.sh,scoped-tests.sh,testlib/census_scan.py}`
    plus two test files. `scoped-tests.sh` and `testlib/**` are BOTH declared shared surfaces, so the
    same merged-tree caveat as rank 10 applies — do not lean on the branch's own green.
    forcing: gate — it reddens `main`, and every branch cut from a red `main` inherits it.

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
# rank 7 — squash-merged, so verify by CONTENT, never by ancestry
gh pr view 799 --repo ZacxDev/homelab-infra --json state,mergedAt,mergeCommit
gh api /repos/ZacxDev/homelab-infra/contents/scripts/tests/test_supersede_wiring.py?ref=trunk --jq .size

# rank 11 — the fix, in BOTH tiers. The sandbox tier is the one that was red.
git -C ~/workspace/devrc fetch origin -q
nix develop ~/workspace/devrc -c python3 -m pytest \
  ~/workspace/devrc-fix1603/scripts/tests/test_census_scan.py -q -p no:cacheprovider   # 29 passed

S=$(mktemp -d); git -C ~/workspace/devrc-fix1603 archive HEAD | tar -x -C "$S"
test -e "$S/.git" && echo "NOT the sandbox shape" || echo "no .git — sandbox reproduced"
nix develop ~/workspace/devrc -c python3 -m pytest \
  "$S/scripts/tests/test_census_scan.py" -q -p no:cacheprovider --rootdir="$S"          # 29 passed

# rank 10 — the gate that is UNREAD. Read PYTEST_RC, never the wrapper's exit code.
bash /tmp/claude-1000/-home-zach-workspace-devrc/*/scratchpad/mt1600-run.sh
grep -E '^(SELECTED|PYTEST_RC)=' <that run's log>

# after merging #1600 only: ship, THEN read the consumer (separate claims)
bash ~/workspace/devrc/scripts/ship.sh
journalctl --user -u stale-base-triage -n 40 --no-pager
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
