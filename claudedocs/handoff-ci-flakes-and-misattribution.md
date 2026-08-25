# Handoff: ci-flakes-and-misattribution — 2026-08-25

## Run this first — the index, one read-only command
```bash
python3 ~/workspace/devrc/scripts/lib/subsystem_recall.py --repo ~/workspace/devrc
```
Terse pointers this doc does not carry, curated by past sessions and outliving it.
🔴 RECALL, NOT LIVE OBSERVATION — every line is a pointer to VERIFY, never a current
reading, and it may describe a gotcha already fixed. `scope-absent`/`scope-empty` means
nothing is recorded yet: ordinary, not an error, and not a clean bill of health.
Non-blocking: if it exits non-zero, print the stderr line and carry on.

⚠ **No `clawgate-task:` field on purpose.** `clawgate_handoff.sh resolve` returned **exit 5,
`NOTHING RESOLVED — 0 tasks for this session`**. Per the tool's own contract that is not a
clean bill of health: an unknown session id answers `200` with an empty array, so the result
cannot distinguish "touched no task" from "wrong id". No field written, none invented.

## Goal
Started as one line of the ccua handoff — *"fix GUARD 10's attribution message"*. It became a
thread about **guards that blame the wrong thing**: GUARD 10 blaming a test for another
session's git write, and browser-bridge tests blaming their own subject for a neighbour's
spool row. Both are now fixed; the flake family underneath is only half closed.

## State now
- Branch `main`. **Nothing in flight.** Base clone `behind 1` — benign; the untracked
  `scripts/discord-embed-ext/` WIP that was blocking `merge --ff-only` landed as **#838**
  (all four previously local-only files verified on `origin/main`), so a plain
  `git merge --ff-only origin/trunk`/`origin/main` clears it now.
- **Seven PRs merged 2026-08-25, each verified by CONTENT (a squash is never an ancestor):**

| PR | squash | what |
|---|---|---|
| devrc #810 | `01121dab` | two ledger tests asserted under a PRODUCTION deadline they did not control |
| devrc #811 | `6c900e9b` | tekton skill: trigger count, the `255` split, prior-art credit, `<homelab-infra>/` path convention |
| devrc #833 | `21166720` | pin the tmux budget's MAGNITUDE — `2.0 → 30.0` had survived the whole suite |
| devrc #840 | `266edd8d` | make the budget reachable from all SIX exposures, not two |
| homelab-infra #395 | `ba904916` | the kills are simultaneous + node-level |
| homelab-infra #397 | `19eadbf1` | superseded-by-#396 record + follow-up measurement |
| homelab-infra #403 | `7a49ee2f` | read-only burst watcher + 8 unittest cases, `RUN` in `ci-manifest.txt` |

- 🔴 **THE HEADLINE: the devrc-ci gate's ~59% pass rate had TWO causes and both are now
  closed — but only one of them by this session.**
  - **~27 of ~65 failures were SCHEDULER PREEMPTION**, and that was **already diagnosed
    2026-08-24** in the manifests' own comments. Fixed by **homelab-infra #396**
    (`c35c78cd`, "give gitops-validate its own nix cache and node"), which merged
    2026-08-25 05:32Z — **46 minutes AFTER** the burst capture that "confirmed" it. Not our work.
  - **~27 were genuine per-test flakes**, ~1 test in ~15,500, varying run to run. #810/#833/#840
    address the tmux-deadline class; the rest are listed under Next steps.
- ✅ **Post-fix measurement — RE-RUN 2026-08-25 ~23:50Z at n=65. THE PREEMPTION FIX IS SETTLED.**
  Before #396 `30/121 killed (24.8%)`; after (created ≥06:00Z 2026-08-25) **`0/65` killed,
  57 passed / 8 genuine `verdict exit=1` / 3 still running.**
  P(0 kills in 65 | rate unchanged at 24.8%) = **9e-9**; 95% upper bound on any residual
  kill rate **4.5%**, down from 24.8%. The earlier `0/9` was ~1-in-13 by luck; this is not.
  - 🔴 **Instrument validated by EXACT RECONSTRUCTION, not by plausibility.** The same
    classifier over the same window boundary reproduces the prior session's figure to the
    digit — `30/121 = 24.8%` — numerator *and* denominator. (`121` = all gate TaskRuns
    created before `06:00Z`, incompletes included; a `complete`-only denominator gives
    `30/116 = 25.9%`. Both are stated so the number carries its own definition.)
  - **TWO independent discriminators agree.** Kills counted as any step exiting `255`/`137`;
    separately, `verdict exit=2` is the killed-step signature. Pre: 30 and 30. Post: **0 and 0.**
    Zero `255`/`137` on ANY step (`clone`/`pytests`/`nodetests`/…), not just the test legs.
  - 🔴 **The rival mechanism — "the post window was just quiet" — is REFUTED by measurement,
    not assumed away.** Post is *busier*: median concurrent TaskRuns 14 vs 12, max **74 vs 44**;
    max overlapping `gitops-validate` TaskRuns **38 vs 30**; max overlapping `devrc-ci` gates
    **14 vs 9**; runs overlapping ≥10 `gitops-validate` TaskRuns **15 vs 12**. Equal-or-higher
    contention, zero kills.
  - **No pruning confound:** 68 `devrc-ci` PipelineRuns and 68 gate TaskRuns in the post
    window — nothing GC'd out of the denominator.
  - **The mechanism is live:** 212 pods checked, `devrc-ci` **entirely** on `talos-xr6-r7p`,
    `gitops-validate` **entirely** on `talos-uvh-gtj`, zero cross-over.
  - **Preemption did not MOVE — it stopped:** `gitops-validate` shows `0` killed in *both*
    windows (0/238 pre, 0/107 post); it was the preemptor, never the victim.
  - **The genuine-flake class is UNCHANGED, exactly as expected:** `verdict exit=1` at
    14/116 (12.1%) pre vs 8/65 (12.3%) post. #396 closed preemption and touched nothing
    else — the per-test flakes under Next steps 2–4 are still open and still real.
- **Deploy: NOT done this session.** Nothing was shipped to either host; all seven PRs are
  merged only. `DEFAULT_TMUX_TIMEOUT_S` is untouched at `2.0`, so no production behaviour changed.

## Investigations — live diagnosis state (first entry is DECIDED, the rest are open)

### ✅ DECIDED 2026-08-25 — the rescued `initiative-scan.py` WIP: one half salvaged, one half rejected on measurement
**Operator decision: salvage `--exclude-slugs` only; drop `parse_resolved`. SHIPPED — #824
merged as `d3b6eeae` 2026-08-25.**
✅ **`rescue/initiative-scan-resolved-filter` is now DELETED — deliberately, not as cleanup.**
The delete was gated on four conditions re-checked in the same command that performed it:
`parse_exclude_slugs` present on `origin/main` (**by content**, since a squash merge is never
an ancestor), the invariant guard present, `gh pr view 824 --json mergedAt` non-null, and the
branch still existing. All four held.
🔴 **`parse_resolved` now exists NOWHERE.** That was the decision, not an accident — it is
rejected, and the reasoning below is the record. Its commit was `1327372d`; GitHub keeps an
unreferenced commit reachable by sha for a limited window only, so if it is ever wanted back,
recover it soon or rebuild it from the description here. **Do not rebuild it as specified** —
read the root cause first; the design is what failed, not the code.

- **What made it urgent (re-verified live, not carried forward):** branch present on origin;
  `merge-base --is-ancestor 1327372d origin/main` → **no**; `git branch -a --contains` → only
  the rescue branch itself; grep on `origin/main` → **0**. And `initiative-scan.py` had taken
  **zero commits** since the rescue's parent, so the WIP still applied cleanly.
- **The two halves are independent**, which is what made a split decision possible at all:
  `--exclude-slugs` (+12 lines, explicit operator list) vs `parse_resolved` (+55 lines,
  inferred verdict, filter **on by default**).
- 🔴 **#778's own diagnosis was wrong in three ways** — found by re-measuring, not by reading
  it. Corrected on the PR across THREE comments, each left standing rather than edited
  because people read each before the next was known to be needed. **Only the third is
  authoritative** — the table below matches it:
  `…#issuecomment-5413412588` (original; the `8 / 1 / 2` split is WRONG) →
  `…#issuecomment-5414463043` (corrects the split to `7 / 1 / 3` — numbers right, but its
  explanation of how `8 / 1 / 2` arose is wrong) →
  `…#issuecomment-5414562898` (**authoritative**: same numbers, correct mechanism).
  All three at <https://github.com/innovation-upstream/devrc/pull/778>.
  - Method: loaded `1327372d`'s module directly, ran `parse_resolved` over handoffs
    materialized from git, attributed every hit to the arm that fired. Positive control
    (`## Status: RESOLVED`) → `True`; negative control (plain prose) → `False`.

    | corpus | scanned | flagged | `heading-marker` | `inline-status` | `PROSE-SUMMARY` |
    |---|---|---|---|---|---|
    | `199774f8` (main at #824's base) | 62 | 11 (18%) | **7** | 1 | 3 |
    | `982778ee` (the sha it cites) | 53 | 10 (19%) | **7** | 1 | 2 |

    ⚠ Both corpora are PINNED on purpose. An earlier draft of this table wrote the first row
    as "`origin/main` today" and as **8 / 1 / 2** — wrong on both counts, caught by the #826
    audit. `main` moves (it is already past `199774f8`), so an unpinned row cannot be told
    apart from doc rot.
    **How the `8 / 1 / 2` arose** — the actual mechanism, itself re-derived after a first
    explanation turned out to be wrong: the linked #778 comment hedges the split as "7–8"
    heading and "2–3" prose, and the table was then built by taking the TOP of one range and
    the BOTTOM of the other. Not a miscount of any particular document — a hedge hardened in
    two directions at once. **A previous draft of this note claimed `8 / 1 / 2` was
    UNREACHABLE. That was also false**, and is recorded rather than deleted because it is the
    same error a third time: at `199774f8` FOUR flagged docs sit outside the heading column
    (3 prose + 1 inline), so miscounting any one of the three prose docs yields exactly
    `8 / 1 / 2`. Two of them look like heading hits at a glance — `### ✅ CLOSED 2026-08-22`
    and `## State now — DONE, verified end to end` — and classify as prose only because the
    arm's regex needs the marker to START the heading.
    The row summed to 11 in every wrong version, which is exactly why an arithmetic
    self-check never caught any of them.
    🔴 The lesson, earned three times in one section: this block exists to correct #778 for
    publishing numbers it had not re-derived, and its first draft, and then its own
    correction, each did the same thing. **Re-derive; do not reason about what the number
    must have been.**

  - (a) It blames the prose scan and states *"the heading and inline `Status:` arms did **not**
    fire"*. The heading arm is the DOMINANT one — `### DONE this session`,
    `### RESOLVED: the clawgate stuck detector…`, `## CLOSED: the commit-to-main guard fail-open`.
  - (b) Its counts came from the **working tree** while citing `982778ee`: its headline example
    `handoff-ccua-waiting-flag-and-fork-close.md` does **not exist** at that sha
    (`git cat-file -e` → absent). That is the 11/55-vs-10/53 gap.
  - (c) Its suggested fix — *"a heading that **starts** with the marker"* — **preserves all 7**:
    that arm's regex already requires the marker to start the heading, so the fix is a no-op
    against every one of them.
- **Root cause (the reason the half was rejected, not just deferred):** a handoff is a
  MULTI-SECTION document. Those headings describe one investigation *inside* a doc that still
  carries live next-steps. The predicate conflates "this document mentions something finished"
  with "this initiative is finished", and that signal **does not exist in the corpus at the
  granularity it needs** — so no tightening of the marker regex reaches it. A report whose
  entire job is answering *"what am I working on"* must not hide a row on a guess.
- **Also fixed in the salvage:** the WIP's `set(raw.split(","))` never stripped, so `"a, b"`
  yielded `" b"` — it suppressed one of two while reading as though it had done both.
- **Not a dead end, recorded so nobody re-derives it:** if an inferred filter is ever wanted,
  it needs an explicit top-level status FIELD as a handoff convention. That convention does
  not exist today, and inventing it is a docs-format change, not a parser change.

### #783 — the spool defect's ROOT CAUSE is untouched
- **Symptom + exact repro:** a test reads `_wait_events(spool_dir, 1)[0]` and gets a
  neighbour's row. Seen in CI as `assert 'getHtml' == 'frames'` (#773, a change to
  `scripts/run-tests.sh`) and `assert 'getHtml' == 'type'` (#770, a change to one `.md`).
- **Observed (with values):** `conftest._isolate_activity_spool` does
  `monkeypatch.setenv("ACTIVITY_SPOOL_DIR", str(tmp_path / "activity-spool"))` — a
  **process-global** env var. `spool_emit.default_spool_dir()` reads it **at emit time**
  (`scripts/collector/keylog/spool_emit.py:32-38`), and `srv.daemon_threads = True`
  (`test_server.py:352`) means `server_close()` joins nothing. The #807 audit reproduced it
  deterministically with a 2-test probe: a thread sleeping 1s then calling
  `S.emit_cmd_event(op="getHtml", outcome="timeout")` wrote into the NEXT test's spool.
- **Ruled out:** `_wait_connected` polls `/instances`, not `/health`, so no in-test diag
  emit. `tmp_path` is unique per test. Each test binds its own port 0 server. The
  `cmd_timeout` lines are the server's structured **stderr log**, not spool rows — do not
  grep the spool for that string.
- **Leading hypothesis:** confirmed, not hypothesis — emitter threads outlive their test
  while the env var is re-pointed under them.
- **Next probe:** close it at the source by joining emitter threads at teardown (or scoping
  the spool dir per-server rather than per-process). `#807` only made the ASSERTIONS robust.
  **39 `_wait_events` call sites still take a count and index by position** (AST-derived, in
  `_wait_events`' docstring: `53 total = 39 n=1 + 5 until= + 9 n>=2`, + 7 op-selected).

### The SECOND flake family — `test_browser_agent.py:558`, unfiled
- **Symptom + exact repro:** full-suite runs of `scripts/browser-bridge/tests` fail 1–2 tests
  in `test_browser_agent.py`, always at the shared subprocess hang-net at `:558`, a
  **different test each run**. All pass in isolation.
- **Observed (with values):**
  - base `5bd00189`, full suite: 1 failed — `test_tab_closed_on_opencode_error` (283s)
  - head, full suite (audit's run): 1 failed — `test_a_goal_beginning_with_a_dash_is_reachable_via_the_separator` (306s)
  - head, full suite (mine): 2 failed — `test_no_shell_string_path_remains`, `test_partial_status_is_success_exit` (376s)
  - a later full dev-host gate on the merged tree: **0 failed** (15827 collected)
- **Ruled out:** not this thread's diffs — **it fails at base too**, and a deterministic
  regression fails the SAME test, not a different one each run.
- **Leading hypothesis:** load-dependent exhaustion of the rig's stall budget, not a hang.
- **Next probe:** capture a FULL failure message, not the tail. The rig itself prints one of
  two verdicts — *"so the MACHINE is not the explanation and the wrapper genuinely hung"* vs
  *"so the MACHINE is stalled — but the stall budget for this run is exhausted"*. **File the
  issue only with that line quoted**; filing it as "flaky" without it is the same
  under-diagnosis this thread spent the evening correcting.

## Next steps (ranked)
1. ✅ **DONE 2026-08-25 — the post-#396 kill-rate measurement re-ran at n=65 and came back
   `0/65`.** Full result, controls and refuted rival mechanism under `State now`. **The
   preemption arm of this thread is CLOSED**; do not re-derive it. What remains below is the
   genuine per-test flake class only, which the same measurement shows is untouched (12.1% →
   12.3%, i.e. unchanged). Method, if it is ever needed again: dump
   `kubectl -n tekton-ci get taskruns -o json`, take names matching `devrc-ci-*-gate`,
   count a run as KILLED if ANY step's `terminated.exitCode` is `255` or `137`, GENUINE if
   `verdict` exits non-zero without one. **Reproduce `30/121` on the pre-`06:00Z 2026-08-25`
   window first** — that is the instrument's positive control, and it matches to the digit.
2. **Two flaky tests still UNDIAGNOSED**, both `devrc`, `scripts/tests/`:
   `test_no_unallowlisted_public_ip_literal_is_committed` and
   `test_the_module_root_is_load_bearing`. Neither has a wall-clock dependency in the sandbox
   tier; either could have been a *correct* red on some branch. Logs were unrecoverable (pods
   GC'd; commit statuses cap at 140 chars) — **but `broken-gate-tail` now exists**, so a
   recurrence is readable.
3. **Re-check `test_an_absent_origin_header_is_not_the_same_as_an_empty_one`** —
   `scripts/browser-bridge/tests/`. It shares #802's dropped-emit mechanism and **#802 has
   merged (`d09038d8`)**; the prior agent said verify rather than assume it is covered.
4. **A ninth flake, UNCONFIRMED:** `test_live_cotenants_does_not_count_this_process`
   (`scripts/tests/test_git_repo_isolation.py:1479`). Leading suspect `git commit`'s detached
   `run_auto_maintenance`. 0/25 reproductions across three load conditions, **and the /proc
   watcher used failed its own positive control — so those zeros are not evidence.**
5. **#810's two remaining cosmetic nits:** `scripts/lib/agent_ledger.py:261` still says "a 2s
   timeout under load" (a second spelling of the budget), and the narrow arm's failure message
   in `test_agent_ledger.py` names the wrong cause. Neither merits its own PR.
6. **Housekeeping, none urgent:** `homelab-talos` has **53 stash entries** and a repo-local
   `core.hooksPath` nobody set deliberately; `devrc` has **46 worktrees**, many on merged
   branches. And `ecc4332e` references the capacity question as `#1205`, which is **not** a
   homelab-infra number (issues stop at #316, PRs at #394) — tracker unknown.

## Gotchas / decisions / dead-ends
- 🔴 **`scripts/gate.sh` runs the DEV-HOST tier only** — `run-tests.sh` + `run-node-tests.sh`.
  It never invokes `nix build .#checks…`, which is the sandbox tier Tekton gates on and which
  builds from a store copy with **no `.git`**. Four consecutive `GATE: RESULT=PASS` runs were
  reported on #773 as covering the merge; the sandbox tier had never been run. Now corrected
  in `CLAUDE.md` (#788). **Name the tier AND the base sha in any green claim.**
- 🔴 **A parallel worker landed mid-thread with a DIFFERENT diagnosis of the same symptom.**
  #802 (`d09038d8`) found the #1 CI failure was a **dropped** row (a data race in
  `server.py:_load_spool_emit` publishing its flag before its module). #807 found a **wrong**
  row. Both real, neither a duplicate. Their AST-derived call-site census was better than my
  grep, so the conflict was resolved by taking **their** text and adding only the half they
  did not cover. Check for a parallel worker before assuming a diagnosis is yours alone.
- 🔴 **Three of the five merges fixed FALSE CLAIMS, not logic bugs**: a CLAUDE.md line saying
  two tiers were interchangeable; a conftest docstring promising isolation it did not
  provide; a helper documented "deliberately total" that raised `AttributeError` on `null`,
  `123`, `"str"`, `[1,2]`. **Every one was found by an audit, none by a green gate.**
- 🔴 **Budget several audit rounds when the artifact is PROSE.** #773's first cut passed a
  green gate AND a mutation sweep and still shipped two 🔴s; the round that fixed them
  re-instated the exact prose-contradicts-code defect the branch existed to close. Prefer
  **deleting a duplicated sentence** (read the value from one place) over correcting it.
- **CI congestion is real and it costs verdicts.** `exited with code 255` on `step-pytests` is
  the congestion signature, not a code failure. A rerun forced into a busy window turned a
  green `nodetests` into `ERROR`. Drain first: no other pipelineruns Running, 0 Pending pods,
  **memory** < 45% on `talos-xr6-r7p` (memory, not CPU — that mistake cost one run).
- **A hand-written rerun PipelineRun needs `workspaces: [source]` and the `taskRunTemplate`** —
  copy them from an existing run or it dies `InvalidWorkspaceBindings`.
- **Mutation sweeps: verify each mutant APPLIED and PARSES.** One round produced three void
  mutants (one didn't parse and killed everything for the wrong reason, one anchor never
  matched, one applied half). Driving the sweep from Python rather than shell removed the
  quoting class entirely.
- **A green-at-base test is not regression coverage.** A large-delta fixture used
  `core.hooksPath`, which `sort -k2` places AFTER 15000 filler lines, so `grep -q` never
  exited early and no SIGPIPE occurred — it passed at base. Fixed by using `alias.*`, which
  sorts first.

- 🔴 **THE MANIFESTS HOLD LOAD-BEARING ANALYSIS — READ THE COMMENTS BEFORE RE-DERIVING.**
  This session diagnosed CI preemption from scratch over several rounds. All of it already
  existed in `homelab-infra` comments dated 2026-08-24: `triggers/ci-priority-classes.yaml`
  (~100), `triggers/gitops-validate-triggertemplate.yaml` (~98),
  `triggers/devrc-ci-pipeline.yaml` (~1130) — including direct `Preempted` events, the rate
  (14/108 = 41% of non-passing), and the exit-code explanation. **Three fixes are rejected
  there WITH MEASUREMENTS** and will keep being re-proposed: concurrency capping (worse at
  every helpful cap — a queued TaskRun's clock starts at CREATION and burns its own
  deadline), `ResourceQuota` (cannot be scoped safely; it rejects the no-requests
  `notify`/`report`/affinity pods, and losing `report` is the worst failure here), and
  `retries` (reverted as a trap — Tekton retries any non-cancelled failure, so it re-runs
  genuine verdicts).
- 🔴 **`exit 255` is a claim about the STEP, not about the tests — and the LOG discriminates,
  not the exit code.** Measured over all completed `devrc-ci` gate TaskRuns: **~27 killed
  steps** (25× `pytests` 255, 1× `nodetests` 255, 1× `137` OOMKilled) reporting **no test
  result at all**, against **~27 genuine failures** surfacing as `verdict exit=1`. A step that
  printed `RESULT:` / `<leg> verdict=` failed a test; one that printed neither was killed.
  ⚠ `nodetests exit=2` while the leg actually PASSED (`verdict=pass nix_rc=0`) — the step exit
  codes mislead in both directions.
- 🔴 **`broken-gate-tail` exists and WORKS — use it before guessing.** `ecc4332e` added it;
  positive-controlled 2026-08-25 at **14 populated / 35 empty**, so a zero from it is real.
  ```bash
  kubectl -n tekton-ci get taskrun <run>-report -o \
    jsonpath='{.status.results[?(@.name=="broken-gate-tail")].value}'
  ```
  Two ways to misread it, both of which cost time here: a `nodetests` section reading
  `killed before it wrote any` is **not** an early kill (the legs are sequential, so
  `nodetests` never starts once `pytests` dies); and the tail is the **last 1200B**, so it
  always ends mid-suite — that is truncation, not the failure point.
- 🔴 **The kube-scheduler logs NOTHING about preemption at default verbosity** — measured
  **6 lines in 24h**. Its silence is not evidence. The discriminator is which pods ARRIVED on
  the node at the kill second; preemption deletes victims first, so the preemptor appears a
  second or two AFTER them.
- 🔴 **`git checkout -- <file>` inside a mutation loop DESTROYS uncommitted work.** Done in
  this session, mid-verification: the loop restored a mutated constant with
  `git checkout -- scripts/lib/agent_ledger.py`, which reverted the entire in-progress
  refactor in that file. The "HEAD" arm then ran without the change under test and produced a
  confident, wrong result ("fixed 2 of 4") that was reported before being caught. **Restore a
  mutated line with `sed`, never `checkout`.**
- 🔴 **AN ARGPARSE DEFAULT SHADOWS AN ENV VAR, INVISIBLY.** `--tmux-timeout` carried
  `default=DEFAULT_TMUX_TIMEOUT_S`, so `timeout` was **never `None`** at that call site — and
  an explicit argument beats the env var by design. `$AGENT_LEDGER_TMUX_TIMEOUT_S` was
  therefore structurally unreachable on the CLI path, i.e. on the opencode plugin, one of the
  two exposures it exists to serve. The code reads correctly; only the mutation matrix
  disagreed. Fix: `default=None` ("nobody said"), with the help text spelling the constant
  instead of `%(default)s` so the existing help-pin test still holds.
- 🔴 **A GUARD'S MUTANT MUST BE BOUNDARY-ADJACENT, not just extreme.** #833's range guard
  `[1.0, 5.0]` was proved by killing `5.5` and `0.9` — not only `30.0`. Killing the extreme
  alone would not have shown the bounds were load-bearing. Run mutations under
  `PYTHONDONTWRITEBYTECODE=1` so a stale `.pyc` cannot score one as SURVIVED.
- ⚠ **A big local failure count is meaningless without the base run.** A broad
  `scripts/tests + claude-hooks + opencode` sweep showed **67 failed / 10362 passed** — and
  the identical command at `origin/main` gave the **identical failure set**, zero difference
  in either direction. They are dependency gaps in an ad-hoc `nix-shell` (`yaml` missing,
  among others), not a regression.
- **CARRIED FORWARD from the previous `State now`/`Next steps` (they sit under REPLACE headings
  and this update would otherwise drop them — they are decisions, not stale status):**
  **#778 CLOSED unmerged, decided 2026-08-25.** The `--exclude-slugs` salvage landed instead as
  **#824 (`d3b6eeae`)**, and `rescue/initiative-scan-resolved-filter` was then **deleted
  deliberately**, gated on two content greps returning 1 — so that thread is fully closed,
  residual included, and nothing there is single-copy any more. 🔴 A later reader finding that
  branch absent should read this line before treating it as lost work.
  ⚠ The previous doc's "Deploy VERIFIED on both hosts / `ship.sh` rc=0 at `324693fd`" is NOT
  carried forward on purpose: `main` has moved many times since, and **this session deployed
  nothing**. Re-run `drift-check.sh` rather than trusting any deploy line in this doc.
- ⚠ **`devrc` is PUBLIC and cites other repos' paths.** `test_no_new_dead_paths` flags a
  cross-repo path whose first segment collides with a real devrc directory — `claudedocs/…`
  did exactly that. The convention is `<homelab-infra>/claudedocs/…`; rule 3 skips any token
  containing `<`. Sibling `triggers/…` citations passed only because devrc has no `triggers/`
  top-level.

## How to verify
```bash
# the three devrc changes are on main, and production behaviour did NOT change
git -C ~/workspace/devrc show origin/main:scripts/lib/agent_ledger.py \
  | grep -E '^DEFAULT_TMUX_TIMEOUT_S = 2.0|def resolve_tmux_budget|TMUX_TIMEOUT_ENV = '
# the budget is reachable from all six exposures — the load-bearing direction is the SECOND:
#   constant 0.001 + env 60.0  -> 0 failed (env protects)
#   constant 2.0   + env 0.001 -> 4 failed (env CONTROLS)
# the watcher and its manifest registration landed
git -C $HOMELAB show origin/trunk:scripts/tests/ci-manifest.txt | grep catch_ci_preemption
# preemption is gone: the two pipelines are on DIFFERENT nodes
KUBECONFIG=$KC_HOMELAB kubectl -n tekton-ci get pods -o json | python3 -c "
import json,sys,collections; d=json.load(sys.stdin); a=collections.Counter()
[a.__setitem__((n.split('-')[0], p['spec'].get('nodeName')), a[(n.split('-')[0], p['spec'].get('nodeName'))]+1)
 for p in d['items'] for n in [p['metadata']['name']] if p['status'].get('phase')=='Running']
print(a)"
```
