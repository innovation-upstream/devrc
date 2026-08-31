# Handoff: skill-chain-usage-audit — 2026-08-29

## Run this first — the index, one read-only command
```bash
python3 ~/workspace/devrc/scripts/lib/subsystem_recall.py --repo ~/workspace/devrc
```
Terse pointers this doc does not carry, curated by past sessions and outliving it.
🔴 RECALL, NOT LIVE OBSERVATION — every line is a pointer to VERIFY, never a current
reading, and it may describe a gotcha already fixed. `scope-absent`/`scope-empty` means
nothing is recorded yet: ordinary, not an error, and not a clean bill of health.
Non-blocking: if it exits non-zero, print the stderr line and carry on.

## Goal
Measure how the `/handoff` → `/resume` → clawgate skill chain is ACTUALLY used, and
whether sessions that resume a handoff end up recording their work. Ship a number that
survives adversarial re-derivation, and fix whatever it exposes.

## State now
- 🔴 **THE INITIATIVE'S HEADLINE MEASUREMENT, carried forward because it is durable and this
  section is REPLACED on every update.** Window 2026-08-15 → 08-29, both hosts, both runtimes
  (`find-session` stderr silent on every run ⇒ full coverage per its own contract):
  391 sessions match `"Canonical handoff (read first)"`; **256** carry a `/resume` kickoff as
  their genesis line; **253 graded** (98.8%). **231 / 253 = 91.3% RECORDED** — 206 updated the
  doc they resumed, 25 landed under a different topic (drift, not loss). **22 / 253 = 8.7%
  real loss** — 19 never invoked `handoff_doc.py`, 2 tool failures, 1 stopped at the confirm
  gate. Legitimate declines (`no-change`/`no-advance`): **ZERO**. Rank 1's end-state split of
  the 16 readable never-run losses: **8 cleanly-ended · 4 interrupted-at-end · 2
  context-exhausted · 2 never-started**, with a `Stop` hook already firing in **8 of 8** of the
  dominant bucket. Clawgate leg: of the 85 tasks that advanced past `open`, **73 (86%)** carry
  a write-back comment; **0** of 186 were agent-dispatched.
- ✅ **MERGED AND LIVE ON BOTH HOSTS — ranks 2 + 7: `scripts/claude-hooks/handoff-write-guard.py`.**
  devrc**#1092** merged as **`ad891a5c`**; `ship.sh` converged both hosts to `bd1572f3` (verified
  by ANCESTRY of the shipped sha, plus a byte check: deployed blob **`6d25558e`**). Registered on
  both hosts — 1 `PostToolUse`, 1 `Stop`, **0** `SessionStart` — and probed live against the
  DEPLOYED copy: block · self-suppress · read-with-no-work silent · no state dir for an untouched
  session. **Merged and registered are two claims; both were made separately.**
- ✅ **MERGED — rank 6: devrc#1108 as `57b010fb`** (squash, 2026-08-31T05:15:15Z; **verified by
  CONTENT on `origin/main`, never by ancestry** — ancestry is false forever after a squash).
  9 commits: the correction, six audit rounds, two gate re-triggers. **Every round found something
  real and FIVE of six found the PREVIOUS round's correction wrong** — see the ladder block below;
  that ratio is the durable finding, not the PR.
- ✅ **CLOSED — rank 3**, committed on #1055: the drift question does not discriminate (95.5% of
  abandoned docs declare open work vs a **90.3% control**). Real finding: **13 / 26** abandoned and
  uncommitted-since, **5** with no link from X to Y. Instrument
  `claudedocs/skill-chain-drift-audit.py`, 4 controls, all green.
- ✅ **CLOSED — the CI investigation, and it LEAVES this arc.** Capacity, already measured in-tree
  at `scripts/tests/test_subsystem_store_api.py:99-108`. The symptom fix (`8e33bf1d`, #1023,
  `HANG_TIMEOUT` 15→60 s) is live and **insufficient**. 🔴 See the WALK-BACK block: the
  queue-depth attribution is an **uncontrolled covariate**, not a demonstrated cause.
- ✅ **Subsystem store recorded** (2026-08-31, `--pr 1108,1055` window — the `--session` window
  REFUSED with `transcript cwd does not match`, correctly, because this session ran in
  `datapacket-talos`). Two bullets: `devrc/skills.md` (the derivation-command lesson) and
  `devrc/analyze-service-index.md` (recall *lists* vs *features*). Also **closed an `OPEN:` bullet
  that was not mine** — the clawgate "browser layer is UNGATED" item, whose fix merged as
  `6bf866fe`; verified by content before rewriting it `RESOLVED`. Scope open-actions 1 → 0.
- **Branch / PRs:** `zach/skill-chain-usage-audit` → **devrc#1055, OPEN** (carries ranks 3's
  instrument, rank 4's clawgate fix, and this doc). **devrc#1064, OPEN** (`feat/handoff-audit`).
  🔴 **Both are still RED on the capacity flake — neither caused it**, and `main` is
  `enforce_admins: true` with both legs required, so both are blocked on a re-run against a quiet
  queue. #1108 got through on its **third** attempt.
- 🔴 **The LOCAL branch `zach/skill-chain-usage-audit` in the primary clone is DIVERGED and is the
  STALE side** — do not check it out, do not move it. **Work detached off
  `origin/zach/skill-chain-usage-audit` and push `HEAD:zach/skill-chain-usage-audit`.** The primary
  clone sits on `main` (behind 2) and holds other sessions' uncommitted files.
- **Not deployed:** rank 4's clawgate `SKILL.md` fix is committed on #1055 and NOT live;
  `handoff-audit.py` is on #1064 and wired into no gate, skill or script.

## Open investigations — live diagnosis state

### ✅ CLOSED — why the never-run losses ended without invoking `/handoff`
**Answer: they ended CLEANLY. Context exhaustion is a minority cause, 4:1.** The remedy
this selects is a **nudge**, not an auto-draft.

Re-derived 2026-08-29 22:5xZ on a fresh population (the prior run's classifier was
throwaway and is gone; nothing cached was reused). 305 `/resume`-genesis sessions, 276
graded, 51 with no in-window commit on the resumed doc, of which **32 DID run
`handoff_doc.py`** (mostly the topic-drift cases below) and **16 never ran it**:

| bucket | n | share |
|---|---|---|
| **D cleanly-ended** | **8** | 50.0% |
| B interrupted-at-end | 4 | 25.0% |
| **A context-exhausted** | **2** | 12.5% |
| 0 never-started (zero assistant turns) | 2 | 12.5% |

Only the two A sessions come near a ceiling — peak input **944,856 (0.94)** and
**965,819 (0.97)** against 1M. Every other session peaked at 0.29–0.60. Three sessions
have an unresolvable ceiling, so at most one D could flip: **cleanly-ended ≥ 7** is the
floor.

🔴 **The decisive number for item 2 — a `Stop` hook ALREADY FIRES in these sessions.**
`stop_hook_summary` rows are present in **8 of 8** cleanly-ended, 2/2 exhausted, 3/4
interrupted, 0/2 never-started (nothing ran at all) = **13/16**. So a handoff-write
`Stop` hook is not merely warranted, it is **mechanically reachable in 100% of the
dominant bucket** — the hook infrastructure demonstrably executes exactly where the
handoff is being lost. An auto-draft-before-compaction would address 2 of 16.

**Controls** (a detector never watched fire proves nothing): the `handoff_doc.py` probe
fired on **32 of 48** readable loser transcripts, so the 16 zeros are absences, not a
dead probe. The compaction-marker detector is real but weak — **20 of 5,961** corpus
transcripts carry a genuine marker — which is why the token ratio does the work here.

**Reconciliation with the 19 above, which is NOT refuted.** Population grew 256 → 305
between the two runs; this run's loser set is wider because it also holds the 25
topic-drift sessions the earlier pass scored as *recorded*. **3 loser transcripts live on
the laptop and are unreadable from the workbench** (43 of the 305 sessions are remote) and
29 sessions were dropped because their resumed doc would not resolve. 16 + 3 unreadable
is consistent with 19; treat the bucket SHARES as the finding, not the denominator.

### ✅ CLOSED — the topic-drift sessions, and why the question as posed does not discriminate
**Answer: yes, the abandoned doc still declares open work — and that fact is WORTHLESS on
its own, because 90.3% of the MAINTAINED docs do too.** Measured 2026-08-30 by
`claudedocs/skill-chain-drift-audit.py` (committed; window 2026-08-15 → 08-30, both hosts,
both runtimes, `find-session` stderr empty ⇒ full coverage). **330** `/resume`-genesis
sessions, 298 with a resolved doc, **230 aligned · 29 DRIFT · 24 no-record**; the 29 drift
sessions abandon **26 distinct docs**.

| predicate | drift-abandoned | maintained (control) |
|---|---|---|
| declares open work | **21 / 22 = 95.5%** | **84 / 93 = 90.3%** |

🔴 **The next-probe as written was a corpus measurement wearing a finding's clothes.** A
handoff doc declaring open work is the NORM, not a symptom of being abandoned — the two
arms are 5 points apart. Had this been run without the control it would have shipped
"95.5% of abandoned docs still claim in-flight work" as if drift caused it.

**Two predicates that DO discriminate, and they are the finding:**
- **13 / 26** abandoned docs declare open work **AND** have received no commit on or after
  the drift session's date. That is the staleness generator: an unmaintained doc still
  advertising a ranked backlog.
- **5 of those 13** have a successor whose text names **neither the abandoned doc nor its
  topic** — a reader landing on X has no route to Y at all:
  `claudedocs-audit-arc-2026-08-22` → `opencode-rm-glob-narrowing`;
  `gate-hardening-and-review-hide-2026-08-23` → `devrc-ci-failing-test-names`;
  `skill-prune-campaign` → `bulkhead-metric-registry`;
  `submit-guards-and-app-drift` → `pkgzip-exclusion-rules`;
  `subsystem-index-per-host` → `b2-orphan-sweep-arming`.

**Controls** (all four green; a run whose control block is absent is void —
`DRIFT_CONTROLS=1`): positive `handoff-5-new-tasks-2026-08-14.md` → True with 5 booked
items; negative `handoff-bridge-unbounded-waits.md` → False; **sensitivity** — inject one
un-done ranked item into that same negative doc and it flips False ⇒ True, so the
predicate varies with the THING and not merely with the doc; write-detector fired on
**97 of 120** sampled transcripts, so the zeros are absences.

**Next probe (the part still open):** the remedy is not obvious and should not be guessed.
The two candidate shapes are a `/handoff` step that writes a `superseded-by:` pointer into
X when the topic moves, or a `/resume` warning when the doc it is opening has had no commit
since the last session that read it. Both are cheap; which one is right depends on whether
drift is usually a RENAME (X and Y are the same work) or a genuine SCOPE MOVE — that split
has not been measured, and the 5 unlinked cases above are the set to read.

### 🔴 WALK-BACK — "queue depth is what moved" was UNDER-CONTROLLED, and the store already knew
**Corrected 2026-08-31, by reading the subsystem store instead of re-deriving.** This doc briefly
claimed that #1108's three gate attempts showed *"the variable that moved was queue depth, not the
diff"*. The three attempts are real and are recorded above:

| attempt | queue at fire | outcome |
|---|---|---|
| 1 `9cb3e56f` | 8 running / 3 pending | `FAILED: pytests` naming a test |
| 2 `a3f15123` | 7 running / 2 pending | `COULD NOT RUN` both legs, gate task's 60m budget expired |
| 3 `eb03676c` | **5 running / 0 pending** | **both legs green, 22m** |

🔴 **But `devrc/tests.md` in the subsystem store has carried the controlled version since
2026-08-27, and nobody in this arc consulted it** — *"Three runs of `tekton/devrc-*` on ONE
UNCHANGED commit (`4ea2ee71`, PR #937, retriggered by close/reopen): (1) both legs ERROR 'COULD NOT
RUN'; (2) nodetests pass, pytests FAILED with `failed=0`; (3) both pass, summary byte-identical to
(2)."* That is **the exact probe this doc kept listing as its open next step** — one unchanged
commit, three different verdicts — already run, days earlier, and it moves queue depth from
"demonstrated cause" to "uncontrolled covariate". Three attempts at three depths with three
outcomes is equally consistent with a gate that is simply nondeterministic.

**What survives and what does not.** The capacity MECHANISM stands — it is measured and written
down in-tree at `scripts/tests/test_subsystem_store_api.py:99-108`, and attempt 2's failure is
unambiguous (60m13s against a 60m task budget). What does NOT survive is the causal claim about
attempt 3 specifically: a single green at one depth is not an experiment. **Do not cite the
8→7→5 / 3→2→0 table as evidence that draining the queue fixes the gate.**

🔴 **The reusable lesson is about where to look, not about CI.** This arc spent days naming
"re-run on an unchanged sha" as its next probe while the answer sat in a store that
`subsystem_recall.py` prints in one read-only command — and this session DID run that command at
kickoff, but the recall featured `skills` in full and only listed `tests`, so the relevant bullet
was one `--ref` away and never opened. **When a doc names a probe as open, grep the store for it
before running it.** Related: `devrc#943` is open on the sibling defect (a failing status that
does not name which of ~28 targets failed), which is why attempt 1's named test and PR #937's
`failed=0` are different shapes and must not be merged into one story.

### ✅ CLOSED — the audit ladder on #1108, and what SIX rounds on a 21-line docs PR actually taught
**Every round found something real, and five of the six found the PREVIOUS round's correction
wrong.** That ratio is the durable finding here, not the PR.

| round | what it caught |
|---|---|
| 1 | the PR's recorded cause was falsified by the tree it described |
| 2 | the R1 fix **deleted a safety instruction** ("proven on a scratch pipeline") and leaned on a control invalid on the exact variable it refuted |
| 3 | the R2 fix asserted a "measured difference" (root dir 0777 vs 0755) that is **measurably absent** — `cp -a` overwrites the destination root's mode |
| 4 | the R3 fix rested on a premise the tree contradicts — "the gate runs as `nixbld`"; it runs as **root** (`uid=0`) |
| 5 | the R4 fix **censused the wrong population** (live pods, not TriggerTemplates), losing `remix-ux-audit`, and left a self-contradiction 50 lines away |
| 6 | a dead external path in the one class gate 0 cannot see, and the same stale claim left in the **auto-loaded body** |

🔴 **THE GENERATOR, NAMED: a literal fact restated in prose, derived from a population that is
not the defining one, going stale.** Rounds 2–5 were all the same shape wearing different
clothes, and each round's fix supplied the next round's finding. **Restating the number a third
time is what does NOT work.** What closed it was structural — delete the list, ship the
DERIVATION COMMAND over the defining population, and *run the command before shipping it*.
Round 6 ran it byte-for-byte out of the markdown (rc 0, reproduces 4/1/8, robust to a missing
`podTemplate`) and called it *"the first correction in this ladder that I could not falsify
against the tree."*

🔴 **THE STOP GATE COULD NOT FIRE, AND NOBODY WOULD HAVE NOTICED.** `/audit-pr`'s mechanical
stop is *two consecutive rounds whose fixes changed zero PAYLOAD lines* — the check that catches
a ladder auditing its own scaffolding. **On a docs-only PR the payload IS the `.md`, so every
fix round is 100% payload by construction and the gate can never trip.** Six rounds ran with the
gate structurally silent. Stopping was a judgement call: round 7 would have re-read two edits
whose correctness is settled by `ls <path>` and one `grep`. **When you open a ladder on a docs
PR, say up front that the gate is inert and that you will stop on judgement.**

🔴 **A DIAGNOSTIC A SKILL PRESCRIBES CAN BE UNABLE TO OBSERVE ITS OWN CONDITION.** Gotcha 6(b)
is titled *"`sandbox = false` in the CI pod"* and instructed the reader, under a 🔴, to run
`nix config show | grep "^sandbox "`. Measured live: `sandbox = true`, `sandbox-fallback = true`,
**`/build` absent**, build in `/tmp/nix-build-*` — the pod builds unsandboxed and the
*configured* value reads `true`. The prescribed command returns a reassuring `true` over exactly
the condition it exists to catch, so an operator following the 🔴 concludes "not a broken gate,
debug your diff" — the precise inversion it was written to prevent. **Ask of any detector in a
doc: does it read the CONFIGURED value or the EFFECTIVE one?**

**Method notes worth reusing:** dispatch each round BLIND to the previous round's reasoning (the
brief carries only what the fixes CLAIM, never why they are correct); re-derive every auditor
finding first-hand before acting on it — three of the six rounds' headline numbers moved when I
checked them; and post the `audit-claims` block per round so the next brief cannot be assembled
against a range that is empty by construction.

### ✅ CLOSED — the red `devrc-ci` checks are a CAPACITY problem, and the repo had already diagnosed it
**Answer: a localhost round-trip losing the scheduler while ~12 pipelineruns share one node.
The test logic is never reached, so the gate reports a code failure for a capacity problem.**
Found 2026-08-30 not by a new probe but by *reading what another module had already measured* —
the same cross-check shape that found the write guard's unarmed `Read` path.

`scripts/tests/test_subsystem_store_api.py:99-108` on `origin/main`, verbatim:

> Why 60 and not 15: measured 2026-08-29, the devrc Tekton gate was failing **~60% of runs
> REPO-WIDE (6 of 10 in one window, on unrelated branches)** with `TimeoutError` out of
> `socket.py` — a localhost round-trip that lost the scheduler for >15 s while 12
> pipelineruns shared the node and this suite ran 637 s under xdist. The test logic was never
> reached, so the gate reported a code failure for a capacity problem.
> 🔴 This is the SYMPTOM fix. The cause is a 10-minute parallel suite competing with a
> saturated cluster, which belongs to Tekton capacity, not to this file.

- **This CORRECTS this doc's own leading hypothesis.** It said *"the affected population is
  exactly tests that read host state"*. That is not the population. `nix eval`, the deployed
  `~/.claude/skills/` copies and live espanso config are host-state reads; a **loopback socket
  starved by the scheduler** is not — it is any test that makes a localhost call while the node
  is oversubscribed. Two different populations, and only the second one is measured.
- **The symptom fix is LANDED AND INSUFFICIENT — say both.** `8e33bf1d` (#1023, 2026-08-29)
  raised `HANG_TIMEOUT` 15 → 60 s and it is live on `main` (`:119`). Today's runs build from
  merge previews of that `main`, and **4 of 11 still failed**. So 60 s absorbed some of the
  delay and not all of it; the remaining fix is Tekton capacity, exactly as the comment says.
- **Population read, 2026-08-30 19:27→20:51Z.** Swept every resident `devrc-ci-*-gate-pod` and
  read `step-pytests`' own `verdict=` line: **11 runs, 7 pass / 4 fail**, and the four failures
  name **four DIFFERENT tests** —
  `TestTheActorComesFromTheTOKEN.test_a_FORGED_actor_in_the_body_is_DISCARDED[record1-…]`
  (`wvnl4`, 20:00), `TestOnlyAWriteROUTERetainsItsBody.test_POSITIVE_CONTROL_a_real_WRITE_route_DOES_keep_its_body`
  (`9stqk`, 20:03), `test_a_hanging_fetch_is_BOUNDED_and_the_memo_spares_a_second_wait`
  (`qnq4v`, 20:50), plus **#1108**'s `[record0-…]`. With **#1055**'s
  `test_live_existing_resolutions_not_made_ambiguous` (having failed
  `test_handles_resolve_to_the_exact_expected_paths[repos-HOMELAB-…]` on its previous run),
  that is **five distinct failing tests across five diffs that touch none of them.**
- 🔴 **The single decisive datapoint, and it is an attribution not a correlation:**
  `devrc-ci-wvnl4` is **PR #1118**, not #1108 (`ci.zacx.dev/supersede-key: pr-1118`), and it
  failed the **sibling parametrization** — `[record1-…]` against #1108's `[record0-…]` — of the
  *same* test, with `TimeoutError: timed out` at `python3.12/socket.py:720`. A deterministic
  defect fails the same case every run. Different generated cases failing on different
  branches is the signature the in-tree comment describes.
- **Ruled out:** *"it is this arc's diffs"* — #1108 is markdown-only, one file, one list item,
  and the module passes **8/8 in 5.16 s** at the PR head on an unloaded host; the diff touches
  no `.py`, so base and head are byte-identical there. *"the gate is unconditionally red for
  this arc"* — 7 of 11 runs passed today and #1092 went green on both legs.
- 🔴 **RETRACTED before it was written up: the "same collected count, different verdict"
  control.** Two count groups each held both verdicts (10707: pass/**fail**/pass; 10731:
  pass/pass/**fail**), which reads as one tree disagreeing with itself. It is not — every run
  carries a distinct `refs/pull/N/merge` preview sha (`7cdb05a8da15`/`6f302194e897`/`182c32809f88`;
  `e3df913307a2`/`911af220fb62`/`f5b6bddf24ad`). **An equal test COUNT is not an equal TREE.**
  The attribution above does the work instead, and needs no such control.
🔴 **THE RE-RUN DID NOT CLEAR IT, AND IT FAILED A DIFFERENT WAY — 2026-08-30/31.** #1108 was
re-triggered with empty commit `a3f15123` on the strength of the non-attribution above. The
gate came back **`COULD NOT RUN` on BOTH legs**, which is a different observable from the first
attempt's `FAILED: pytests — FAILING: <test>`. Diagnosed rather than re-run again:

| attempt | sha | observable | mechanism |
|---|---|---|---|
| 1 | `9cb3e56f` | `FAILED: pytests — FAILING: TestTheActorComesFromTheTOKEN…` | a `TimeoutError` out of `socket.py` INSIDE the suite |
| 2 | `a3f15123` | `COULD NOT RUN` on **both** legs | the **gate task's own 60m budget** expired before any verdict |

Attempt 2's status went `pending 23:12:50Z` → `error 00:13:03Z` = **60m13s**, against
`devrc-ci-pipeline.yaml:1554` `timeout: "60m"` (the pipeline budget is 1h25m and tasks 70m, so
neither of those is what fired). Queue at diagnosis: **8 concurrent PipelineRuns, 3 Pending
pods.** `29ccfd69`'s own commit message predicted exactly this shape — *"halving the pods a node
admits moved the cost into PENDING, and the timeout clock runs there."*

🔴 **So "re-run until green" is REFUTED here, not merely unattractive.** Two attempts produced
two distinct symptoms of one saturated node, and a third would be the antipattern rather than a
measurement. The `tekton` skill's own instruction is the applicable one — *"Push, wait for the
queue to drain, push."* **The next attempt should be made against a quiet queue, once, and the
queue depth recorded beside the result**; if it fails a third time the blocker is capacity and
belongs to the `tekton` owners, not to another retry.

- **Consequence, stated because it blocks the arc:** `main` protection is `enforce_admins: true`
  with **both** `tekton/devrc-pytests` and `tekton/devrc-nodetests` required (`strict: false`),
  so a red pytests leg blocks the merge outright — **#1055, #1064 and #1108 are all held by a
  failure none of them caused.** A re-run is defensible *given the attribution above*; it is not
  a fix, and the capacity problem is the real defect. It now belongs to Tekton capacity work,
  not to this arc.

### Both PRs are red on tests neither one touches — environment-sensitive, not code
- **Symptom + exact repro:** `gh pr checks 1055` → `tekton/devrc-pytests FAILURE`; same for #1064.
- **Observed (with values):** the named failure DIFFERS per run and per PR —
  #1055 `test_handles_resolve_to_the_exact_expected_paths[repos-HOMELAB-…]`;
  #1064 first `test_espanso_detect.py::test_live_existing_resolutions_not_made_ambiguous`,
  later `test_a_hanging_fetch_is_BOUNDED_and_the_memo_spares_a_second_wait`.
  Three other open PRs (#1046, #1057, #1059) all failed one *shared* test,
  `TestSkillDocsArePinned.test_the_pinned_docs_are_the_DEPLOYED_ones`.
  Every one of these reads host state: `nix eval`, the deployed `~/.claude/skills/` copies,
  live espanso config, or wall-clock timing.
- **Ruled out:** *"my change broke it"* — for #1055 the test is about `nix/agent-handles.nix`
  resolving `$HOMELAB` against a synthetic `/home/testuser`, and the diff is markdown plus two
  `claudedocs/` scripts; **10 passed at `origin/main` AND 10 passed at head on the dev-host
  tier**, and `origin/main`'s sandbox tier is green with a real build log
  (`collected=18703 passed=18701 failed=0 · RESULT: PASS`).
  *"main is red on the sandbox tier"* — killed by that same log.
  *"the shared hostPath nix cache causes concurrent-eval interference"* — killed: if it did,
  #1046/#1057/#1059 would fail the nix-eval test, and they fail a filesystem one instead.
- ~~**Leading hypothesis:** the affected population is exactly "tests that read host state".~~
  🔴 **SUPERSEDED AND PARTLY WRONG — see the CLOSED block below.** The mechanism is a **loopback
  socket starved by the scheduler**, which is not a host-state read at all; it is any test that
  makes a localhost call while the node is oversubscribed. The two blocks after this one are
  also superseded and are kept only for the reasoning they rule out.

### Was the devrc-ci outage fully closed?
- **Symptom:** 2026-08-29 ~22:23–22:48Z every devrc-ci gate leg reported
  `COULD NOT RUN: the gate stopped before this leg reported`; 8 PRs blocked.
- **Observed:** `pods "devrc-ci-…-gate-pod" is forbidden: violates PodSecurity "baseline:latest":
  hostPath volumes (volume "nix-cache")` — `6bec075e` gave the gate a hostPath cache and the ns
  had no PSA exemption, so the pod was rejected at ADMISSION and never created (hence notify+report
  pods but no gate pod).
- **Ruled out:** congestion/preemption (the `tekton` skill's documented 255 signature) — this is
  `PodAdmissionFailed`, a different reason string entirely.
- **Resolved by someone else:** `686d6ff0` at 22:48Z added
  `pod-security.kubernetes.io/enforce: privileged` to ns `tekton-ci`. Verified by the boundary:
  **5 `PodAdmissionFailed` before, 0 after**, across 14 devrc-ci TaskRuns.
- **Next probe (the part still open):** the residue is ordinary node congestion —
  `ExceededNodeResources`, `talos-xr6-r7p` at 89% CPU requests. The gate pod requests **4250m**
  while three sampled running pods used **1m / 4m / 544m**. That is a lead, not a licence:
  measure PEAK over a full run before proposing a right-size, and note the `tekton` skill records
  four *other* fixes as already-rejected-with-measurements.

### Both PRs of this arc are red on tests neither one touches — one datapoint AGAINST, not a resolution
- **New evidence (2026-08-30):** devrc**#1092** — a diff that touches `scripts/run-tests.sh`,
  `nix/home.nix`, `register-nudge-hook.py` and four hook test files — came back
  `tekton/devrc-pytests SUCCESS` **and** `tekton/devrc-nodetests SUCCESS` on the first run,
  with no retry. So the gate is not unconditionally red for this arc's PRs.
- **What that does and does not license.** It is consistent with the leading hypothesis
  (environment/timing-sensitive tests that read host state, made worse by node congestion) and
  it refutes nothing about #1055/#1064 specifically — their failing tests are still
  unattributed and were never re-run on an unchanged SHA. 🔴 **Do NOT record this as "the CI
  problem is fixed".** One green run at one moment cannot distinguish "the runner is fine now"
  from "the queue happened to be empty", which is the same empty-result trap the rest of this
  doc is about.
- **Next probe, UNCHANGED and now cheaper to interpret:** re-run #1055's gate on an unchanged
  SHA and see whether the SAME test fails. A different test ⇒ environment/timing; the same test
  ⇒ a real runner-vs-local divergence worth fixing in the test. #1092's green makes the
  "different test" reading more likely, not proven.

### The local suite's 76 failures are NOT this arc's, and the control says so
- **Symptom + exact repro:** `python3 -m pytest scripts/tests -q` in a worktree of
  `zach/handoff-write-guard` → **76 failed, 10368 passed, 1 skipped** in 15m28s.
- **Observed (with values):** the SAME command in a clean worktree of `origin/main`
  (`4ca8d662`) → **76 failed, 10368 passed, 1 skipped** in 16m19s, and
  `diff <(grep ^FAILED base) <(grep ^FAILED branch)` is **EMPTY — the two failure sets are
  byte-identical**. A positive control was run on the comparison itself (delete one line from
  one side; `diff` moves), so the empty diff is a measurement and not a comparator wired to
  nothing. The four suites that read the files this diff touches
  (`test_run_tests_floors.py`, `test_run_tests_preconditions.py`, `test_run_tests_timing.py`,
  `test_nogit_isolation.py`) give **49 failed / 38 passed on BOTH** sides.
- **Ruled out:** *"the new target's floor entry broke the two-way pin"* — the floors suite fails
  identically without this diff. *"the new HERMETIC_TARGETS entry broke collection"* — same.
- **Leading hypothesis:** these suites need the nix dev shell (`nix develop`), and a bare
  `python3 -m pytest` is missing tools they probe for; `assert None is not None` is the shape.
  The in-cluster gate, which DOES build that shell, is green on the same tree.
- **Next probe:** run one of them under `nix develop ~/workspace/devrc -c python3 -m pytest …`
  and see whether it goes green — that would close it. It is NOT this arc's problem either
  way; it is recorded so the next session does not re-derive the attribution.

### Rank 6 RE-MEASURED 2026-08-30: the tekton skill was edited TODAY and is still wrong
- **Why this is worse than "still open".** Another session landed devrc**#1096** (`abdd44b7`,
  *"docs(tekton): two ADMISSION-class causes of a red devrc-ci check that are NOT your diff"*)
  on `main` today. It is **+21 lines, 0 deletions** to `claude/skills/tekton/SKILL.md` — rank 6's
  exact target — and it **did not touch any of the stale claims rank 6 exists for.** So the file
  now carries a today's-date edit alongside content that is wrong, which is the strongest
  possible false signal of freshness for the next reader.
- **Observed (with values), read off `origin/main` at `55fca8ff`:** the claims are still there,
  with line numbers — `nodeSelector` node-pinning at **39**, the `nix-store-cache` PVC at **41**,
  the "cold cache ≈ 3 min" tradeoff at **45**, burst-stacking at **48**, and a second
  `talos-xr6-r7p` + `nix-store-cache` description at **430**. Measured 2026-08-29 against the
  live gate: **no `nodeSelector`**, and a per-node **hostPath** at
  `/var/lib/mnt/disk-1/devrc-ci-nix-cache`.
- **Ruled out:** *"#1096 already did rank 6"* — killed by `git show --stat abdd44b7`: one file,
  21 insertions, **0 deletions**. An additive edit cannot have corrected a claim.
- ✅ **CLOSED 2026-08-30 — and the re-measure REFUTED the item, not the skill.** The live gate
  is `nodeSelector kubernetes.io/hostname=talos-xr6-r7p` with `nix-cache` →
  `persistentVolumeClaim: nix-store-cache` (30Gi Bound on that node; `gitops-validate` has its
  own `nix-store-cache-2` on `talos-uvh-gtj`), read off `devrc-ci-vchxk-gate-pod`. **Not one of
  lines 39/41/45/48/430 needed changing.** homelab-infra `6bec075e` (08-29T22:09Z) introduced
  the hostPath and `7839ef54` (08-30T00:29Z) reverted it — a **2h20m** window, and the 08-29
  measurement landed inside it.
- 🔴 **THE TRANSFERABLE LESSON, and it inverts the one this block was written to teach.** The
  block above warned that an ADDITIVE edit is a false FRESHNESS signal. The symmetric error is
  the one that actually happened: **a correct live measurement is a false STALENESS signal once
  the thing it measured moves back.** A doc-correction item carries a measurement whose subject
  is mutable, so the item decays exactly like the ranked backlog it sits in — and here it decayed
  in under 24 hours, into an edit that would have INTRODUCED the error it was filed to remove.
  "Re-measure before editing" was in the item and is what saved it; **write that clause into
  every doc-correction item whose evidence is a live reading, not a git fact.**
- **What the re-measure DID find, shipped as devrc#1108:** gotcha 6(a) closes on *"the narrow fix
  is a baseline-compatible cache"* and never records that the hostPath was reverted, nor why it
  cannot simply return — `DirectoryOrCreate` is created by kubelet as **root**, so `seed-nix`
  fills it and nothing in the sandbox can take the nix DB lock (`big-lock: Permission denied`,
  **75 occurrences over 42 tests, on every devrc PR**, byte-identical on two branches, against a
  pre-change run of `failed=0` over 18,557 passed). That is a SECOND blocker, independent of
  PodSecurity, and merging the two into one story is the empty-result trap this doc keeps naming.

### The CI investigation now has evidence from ANOTHER session — read it before re-deriving
- **New (2026-08-30):** devrc#1096 documents **two ADMISSION-class causes of a red `devrc-ci`
  check that are NOT your diff.** That is the same class this doc's CI block has been circling,
  measured independently. **Read `claude/skills/tekton/SKILL.md` around line 253** (`baseline`
  PSA forbidding hostPath, `6bec075e`'s cache swap, `PodAdmissionFailed`) before running any new
  probe on #1055/#1064's failures.
- **What it does NOT settle:** #1055's and #1064's specific failing tests are *not* admission
  failures — they are tests that read host state and that this doc already attributes to
  environment/timing. The admission class explains a gate leg that never ran; it does not explain
  a test that ran and failed. 🔴 **Do not merge the two into one story** — that is the
  empty-result trap (an absence has many causes, and confirming one proves nothing about
  another). #1092 going green on both legs is consistent with both readings and settles neither.
- **Next probe (unchanged):** re-run #1055's gate on an unchanged SHA. A *different* test ⇒
  environment/timing; the *same* test ⇒ a real runner-vs-local divergence worth fixing.

## Next steps (ranked)

🔴 **The numbering below is STABLE and load-bearing** — `claim-work --slug-for <this doc> <rank>`
makes the rank half of every claim's identity, so completed items are marked DONE in place
rather than removed and renumbered. New work is appended at the end.

1. ✅ **DONE (2026-08-29)** — the end-state split: 8 cleanly-ended vs 2 context-exhausted of 16.
   forcing: none
2. ✅ **DONE (2026-08-30)** — the handoff-write `Stop` hook. **MERGED as `ad891a5c`.**
   forcing: none
3. ✅ **DONE (2026-08-30)** — the question as posed does NOT discriminate: 95.5% of abandoned
   docs declare open work against **90.3% of maintained ones**. Real finding: **13 / 26**
   abandoned-and-uncommitted-since, of which **5** have a successor that names neither the doc
   nor its topic. Instrument `claudedocs/skill-chain-drift-audit.py` (committed, 4 controls).
   The successor question (rename vs scope-move) is the one live thread it leaves — the 5
   unlinked cases are the set to read.
   forcing: none
4. **Deploy the clawgate `SKILL.md` fix** — `home-manager switch`, then confirm
   `~/.claude/skills/clawgate/SKILL.md` no longer contains "preserve both".
   🔴 IN FLIGHT / blocked: needs devrc#1055 merged, which is blocked on the capacity flake.
   forcing: none
5. **Act on the handoff-doc bloat proposal** — `claudedocs/proposal-handoff-doc-bloat.md`.
   The auditor shipped (IN FLIGHT: devrc#1064); the `/handoff` SKILL.md change is deliberately
   NOT written — operator chose propose-only. Next move is the eviction contract
   (budget + step 4.5 + EVICT_HISTORY/RELOCATE_DURABLE/KEEP_HOT), not a gate: the caps file for
   gate 11 records that the general ratchet rule was replayed over 365 days / 901 commits and
   **REFUTED**, so re-run that replay for handoff docs before proposing one.
   forcing: none
6. ✅ **DONE (2026-08-31) — the item's own PREMISE was REFUTED by the re-measure it demanded, and
   then six audit rounds refuted five of their own predecessors.** The skill's lines
   39/41/45/48/430 were CORRECT and none was edited; the 08-29 read caught a state that lived
   **2h19m** (`6bec075e` → `7839ef54`). What shipped instead is why the hostPath cannot come
   back, the sandbox detector that cannot observe its own condition, and a DERIVATION COMMAND in
   place of a pin list. **MERGED as `57b010fb`.**
   forcing: none
7. ✅ **DONE (2026-08-30)** — merged, shipped to both hosts, registered on both, and probed live
   against the deployed copy (block · self-suppress · read-with-no-work silent · no state for an
   untouched session). See `## State now`.
   forcing: none
8. 🔴 **Grade the hook against the number it was built to move — THE CLOSING CONDITION OF THIS
   WHOLE ARC. STILL NOT CUT, and deliberately so.** Re-run rank 1's measurement on a
   post-2026-08-30T17:35Z window and compare the **8.7%** loss rate and the **8/16 cleanly-ended**
   bucket. As of 2026-08-31 the post-window holds **hours, not days**; the pre-period needed
   **14 days for 253** `/resume`-genesis sessions. Cutting it now would grade on a handful of
   tail samples — the exact defect that killed talos-infra #901.
   🔴 **Design it against the two ways this shape has ALREADY died here.** (a) *The treatment must
   outlive the wait*: the treatment is a home-manager generation and ANY unrelated `ship.sh`
   replaces it — check the deployed blob is still `6d25558e` **across the whole window**, not just
   at the end. (b) *The grading population must exist across the whole pre-window*: the pre-period
   is the 2026-08-15 → 08-29 corpus, already measured and on disk, so this half is satisfied — say
   so rather than re-deriving it.
   🔴 **And name the confound before cutting:** the guard changes the behaviour it measures, so a
   session that writes a handoff BECAUSE it was blocked is a success, not a contaminated sample —
   count blocks (`~/.cache/claude-handoff-write/s/*/fires-*`) and report them beside the rate.
   forcing: none
9. **Re-trigger #1055 and #1064 against a QUIET queue** — the only mechanical thing left, and it
   needs a window rather than a decision. Measure first
   (`kubectl -n tekton-ci get pipelineruns` running count + Pending pod count); **0 pending** is
   the signal that matters. Then one empty commit per branch, recording the depth in the message.
   🔴 Read the WALK-BACK block first: two attempts on #1108 failed and the third passed, but that
   is **not** a controlled demonstration that draining the queue fixes it.
   forcing: gate — both PRs are blocked by a required check that neither one caused
10. **Right-size the devrc-ci gate pod, or establish it cannot be** — it requests **4250m** while
   three sampled running pods used **1m / 4m / 544m**, and `talos-xr6-r7p` sits at ~89% CPU
   requests. 🔴 A lead, not a licence: measure PEAK over a full run before proposing anything, and
   read the `tekton` skill first — it records four *other* fixes as already-rejected-with-measurements.
   forcing: none

## Gotchas / decisions / dead-ends

🔴 **This measurement took FOUR instrument corrections, and every uncorrected version
produced a confident, publishable, WRONG number. The corrections are the durable value
of this doc — re-read them before trusting any similar analysis.**

1. **Keyword search over skills is poisoned by the skill BODIES.** `task-pickup` matched
   183 sessions and `status=proposed` matched 325 — those count sessions where the SKILL
   was LOADED, not where the flow ran (the body contains its own keywords, and it is
   injected into the transcript on every trigger). **Search tool INPUTS and tool RESULTS,
   never prose**, when asking "did this tool run".
2. **`find-session` truncates `opened:` at ~120 chars** and the handoff doc path sits at
   the END. Keying the join on that path parsed **7 of 256** rows and returned a confident
   **100%**. Key on the TOPIC (early in the line) and resolve the full path from the
   transcript's untruncated first user message.
3. 🔴 **`git log` from HEAD missed 376 commits — 33% of the total.** These clones sit on
   unpredictable branches and run behind (`datapacket-talos` was 131 commits behind during
   this session). **Use `git log --all`** for any repo-wide historical count here. The same
   staleness also made 6 handoff docs look ABSENT from disk when they exist on
   `origin/trunk` — `git cat-file -e <upstream>:<path>` before believing a missing file.
4. **Topic drift breaks a filename join.** 25 sessions wrote a differently-named doc;
   demanding the same basename scored them as losses.
5. 🔴 **The end-state classifier's interrupt guard was SPELLED, not STRUCTURAL** (found
   2026-08-29 while doing item 1). Matching `Request interrupted` *anywhere* in a
   transcript scores a session interrupted at turn 40 that then ran 500 more turns and
   finished cleanly as "interrupted". **An interrupt anywhere is not an interrupted END** —
   record the row index and only count interrupts inside the final N conversational rows.
   Negative control: 2 sessions carry an early interrupt and must NOT bucket as interrupted.
6. 🔴 **An exhaustion ratio computed against an ASSUMED context ceiling is an artifact
   generator — and it announces itself with impossible values.** The transcript's model
   string is `claude-opus-5` and carries **no context tier**, so assuming 200k produced
   ratios up to **4.83** and scored **11 of 16** sessions as context-exhausted. The true
   figure is 2. A ratio above 1.0 is the tell; **infer the ceiling from evidence (a peak
   above 200k refutes the 200k tier) and mark it AMBIGUOUS where evidence is absent**,
   rather than defaulting a constant. Same class as gotcha 1: the number was wrong because
   the instrument's own parameter was never validated.

   Net effect of 2–4: the rate read **77%** before correction and **91.3%** after. Every
   error was in the instrument, none in the chain.

7. 🔴 **A DAY-GRANULARITY DATE COMPARED WITH `>` MAKES SAME-DAY WORK INVISIBLE — and in a
   corpus where most follow-up lands the same day, that is most of it** (found 2026-08-30
   doing rank 3). `docs.idx` is built with `--date=short`, so "did this doc get a commit
   after the session that abandoned it" was asked as `d > session_date` and every commit
   landing hours later on the same calendar day scored as **never**. The headline read
   **20 / 27**; with `>=` it is **13 / 26** — a **1.5×** overstatement, and the uncorrected
   version was internally consistent, plausible, and about to be written up. The tell was
   the `after` column reading **0 for literally every row**, including this arc's own doc,
   which had been committed four times that day. **A column that is constant across every
   row is a claim about your comparison, not about the world** — and prefer `>=` when the
   two directions are not symmetric: here it can only REMOVE docs from the finding.
8. 🔴 **THE OBVIOUS POSITIVE CONTROL WAS VOID, AND IT ANNOUNCED ITSELF AS "UNREADABLE"
   RATHER THAN AS FAILING.** Rank 3's predicate was to be positive-controlled on *this doc*
   — which declares open work by construction — and the control returned `UNREADABLE`,
   because the doc lives only on an unmerged branch and the resolver reads
   `origin/main|trunk|master`. A control that cannot see its subject is not a lenient
   control, it is **no control**, and a run that prints it beside three green ones reads as
   4/4. **Resolve controls FROM THE CORPUS** (scan for a doc matching the property) rather
   than pinning one by name: a pin rots the moment its items close, and it cannot notice
   that it was never read at all.
9. 🔴 **A PREDICATE SHOWN True ON ONE DOC AND False ON ANOTHER HAS ONLY BEEN SHOWN TO VARY
   WITH THE DOC.** The fix is one more control and it is cheap: take the doc that returned
   False, inject exactly the thing being detected, and watch it flip. Rank 3's
   `declares_open_work` was only trustworthy once `handoff-bridge-unbounded-waits.md` went
   `False ⇒ True` on a single injected un-done ranked item.
10. 🔴 **A DOC'S OWN STATUS PROSE GOES STALE INDEPENDENTLY OF ITS ITEMS — READ THE ITEMS.**
   Found *in the negative control*, not the population: `handoff-bridge-unbounded-waits.md`
   opens its ranked list with **"Only 5 and 6 are open"** while all seven items carry
   ✅ DONE. Items 5 and 6 were finished and the summary sentence was never touched. This is
   the staleness mechanism rank 3 went looking for, sitting in the instrument's own fixture
   — and it means any audit of these docs that keys on the header sentence measures a
   different, older document than the one keyed on the markers.

- **Dead end (do not re-derive): "handoff docs are written but never committed."** Checked
  across 154 repos — **zero** on-disk untracked handoff docs. The hypothesis is refuted.
- **Decision: no `clawgate-task:` field.** `clawgate_handoff.sh resolve` returned **rc 5,
  NOTHING RESOLVED**, with its positive control confirming the board was reachable and the
  token accepted. Per `/handoff` step 1 that is not a clean bill of health, it is an
  unresolved session — write no field, create no task.
- **Decision: worktree + PR, not an in-place push.** devrc's `CLAUDE.md` forbids committing
  to `main` in either host checkout (`ship.sh` converges with `merge --ff-only`, so a
  diverged host is skipped and silently stops receiving changes). `handoff_doc.py --push`
  would have pushed wherever the checkout sits, which was `main`.

- 🔴 **A LIST ITEM'S EXTENT MUST STOP AT THE NEXT HEADING — this class bit the same file THREE
  TIMES.** An H1 whose extent was the whole file; a 160-character "first line" bound that reached
  two lines into the body; and bullet/ranked-item walkers that ran to the end of the section,
  swallowing every H3 after them. The third booked **16,380 B for a bullet containing no
  retraction** and made one bucket **1.43× high**. Each time the *headline* survived and the
  *per-bucket* line did not. When you write a block walker, clip at the next heading AND exclude
  fenced lines from the predicate — the bytes belong to the block, the claim does not.
- 🔴 **DO NOT PIN ABSOLUTE CORPUS TOTALS IN A COMMENT — the corpus is live shared trees.** Three
  consecutive audit rounds each corrected the figure justifying one guard, and each correction was
  itself unreproducible by the next round. Measured across four rounds with nobody touching the
  code: **414→413→414 docs, advisory count 568→573→577.** The fix is structural — pin the quantity
  that is STABLE (the guard's own effect: 10 sections / 14,466 B / 0 + 0, identical every round)
  and tell the reader to re-derive corpus figures by running the tool.
- 🔴 **A borrowed matcher is only validated on the corpus it was validated on.** Reusing
  `skill-audit`'s `WORK_STATUS_HEADING` here keyed on a bare `\bsessions?\b`; a handoff doc's H1 is
  `# Handoff: <topic>` and topics like `session-makework-audit` contain the word, so entire
  documents scored as evictable history — **1,026,330 B / 343 blocks against a true 236,746 B / 119.**
- 🔴 **THIS CORPUS SHOUTS A TERMINAL STATUS, so case IS the discriminator.** Adding `re.I` to the
  resolved-heading matcher looked like fixing an inconsistency and was a pure regression: **10 of 10**
  new hits were inversions — `bounded, not closed`, `unresolved, and deliberately not resolved`,
  `was closed — the reusable lesson`, and `fail-closed` as a *term of art*.
- 🔴 **A COUNT assertion can survive its own inversion.** `assert len(x) == 1` passed with the guard
  inverted to scan only the wrong sections, because one item is booked either way; it died only to
  neighbours. Assert the booked item's TEXT.
- **Decision — stop an audit ladder on CONVERGENCE, not only on a clean round.** Four rounds; every
  one found something real, so the findings-keyed stop rule never fired. Executable payload per
  round ran **109 → 62 → 17 → ~15**, and all three of round 4's findings were about text the
  *previous* round wrote (a comment, a legend from that same commit, a commit message). Stopping was
  made safe by fixing the recurrence *class* (removing the volatile numbers) rather than its fourth
  instance.
- **Dead end (do not re-derive):** the shared-hostPath-nix-cache theory for the CI failures. Killed
  by the cross-PR table — the PRs that would have to fail the nix-eval test fail a filesystem one.
- **Decision — commit throwaway analysis scripts.** The previous pass left its classifiers in a
  session scratchpad as "throwaway", which cost rank 1 a full re-derivation (fresh population, fresh
  154-repo commit index, a rewritten classifier, two defects rediscovered). If a number will be
  quoted, its script belongs in the repo.

- **Decision — the guard's satisfaction anchor is each doc's FIRST READ, not the last work
  event, and that is the ONE place it diverges from its precedent.** `clawgate-writeback-guard.py`
  must anchor on work because its pickup ritual posts a "Starting" comment BEFORE the work, so a
  read anchor would be satisfied at pickup and a missing COMPLETION write-back would be
  unobservable. `/handoff` runs at the END, so there is no such degenerate case here — and a work
  anchor would import a false-positive class this record does not have: `write the doc → git
  commit → git push` leaves the doc's mtime BEFORE the last work event, and the guard would block
  a session that had just recorded everything. The cost is named rather than hidden: a session
  that writes its handoff early and then works for hours without updating it scores as recorded.
  That is exactly what the 91.3% measurement scored too, so the guard and the number agree.
- 🔴 **A FIXTURE WHOSE THREE VALUES NORMALISE TO ONE STRING CANNOT SEE A MUTANT, and this arc
  produced a clean example.** The test "one doc read three ways books one ledger slot" used three
  spellings that `os.path.normpath` collapses to the same string — so it passed with the key
  mutated from the BASENAME to the FULL PATH, the single survivor of a 27-mutant sweep. The fix is
  mechanical and general: assert the fixture CAN move (`len(set(spellings)) == 3`) before asserting
  the code does not. The replacement spellings are the ones a real session produces — the base
  clone's copy, the throwaway worktree's copy that `/handoff` actually writes, and a `..`-bearing
  relative resolution.
- 🔴 **A SELF-AUDIT ROUND ON THE DELTA FOUND A GUARD THAT ARMED NOTHING, and no test could have.**
  The hook's `Read` arm resolved its path against no base at all, so a RELATIVE `file_path` armed
  nothing — a silent blind spot, not a wrong answer. It was found by asking what `lib/subsystem_touch.py`
  (which reads the same payloads out of transcripts) records about that field: *"ABSOLUTE whenever
  the caller passed an absolute"*, i.e. a relative one reaches the payload verbatim. **The
  cross-check that found it was reading what ANOTHER module had already measured about the same
  field**, not re-deriving it.
- **Decision — the mutation sweep is COMMITTED**, as `claudedocs/handoff-write-guard-mutation-sweep.py`,
  parameterised on `HWG_TREE` and re-run from the committed copy before its number was quoted.
  This is the arc's own recorded lesson applied to itself: the previous pass left its classifiers in
  a session scratchpad as throwaway and it cost rank 1 a full re-derivation.
- **Dead end (do not re-derive): "the 76 local `scripts/tests` failures are the new floor entry."**
  Killed by an identical failure SET at `origin/main`. See the investigation block above.
- 🔴 **`clawgate_handoff.sh resolve` returned rc 5 again this session** (0 tasks, with its positive
  control confirming the board reachable and the token accepted). Per `/handoff` step 1 that is an
  UNRESOLVED session, not a clean bill of health: **no `clawgate-task:` field was written and no
  task was created.** Same outcome as the previous session — two consecutive sessions of this arc
  have failed to resolve, which is itself worth a look before a third assumes it is normal.

- 🔴 **`ship.sh` converged both hosts to a sha that is NOT the merge commit, and that is
  correct — verify by ANCESTRY of the SHIPPED sha, not by equality with your own.** The merge
  landed `ad891a5c`; `ship.sh` reported both hosts at `bd1572f3`, because other PRs landed in
  between. Reading "shipped != my merge commit" as a failed deploy is the available mistake;
  `git merge-base --is-ancestor ad891a5c bd1572f3` is the check. Pair it with the byte check on
  the deployed artifact (`git hash-object ~/.claude/hooks/<x>` vs
  `git rev-parse origin/main:<path>`) — the sha says WHAT SHIPPED, the hash says WHAT IS ON
  DISK, and only the second one is a statement about the running system.
- 🔴 **"Merged" and "registered" are two claims and only the second one makes a hook do
  anything.** `nix/home.nix` deploys the FILE; `register-nudge-hook.py` writes the ENTRY into
  each host's `~/.claude/settings.json`, per host, on switch. This repo's #452 is a hook that
  shipped to both hosts, reported a successful switch, and sat inert with nothing on screen to
  say so. The check that separates them is reading `settings.json` for the event names — here,
  1 on `PostToolUse`, 1 on `Stop`, **0 on `SessionStart`**, on BOTH hosts.

- 🔴 **AN ADDITIVE EDIT TO A DOC IS THE STRONGEST FALSE FRESHNESS SIGNAL THERE IS, and the
  ranked-backlog decay rule does not cover it.** The known rule is "a ranked backlog decays —
  re-measure each item before working it", and the expected decay is *the item got done*. This is
  the inverse: rank 6's target file was edited **today**, by a competent session, in a way that
  makes it *look* maintained while leaving every wrong line intact — because the edit was `+21/-0`
  in a different section. **`git log -1 <file>` is not evidence about a claim inside that file.**
  The discriminator is one command: `git show --stat <sha>` — an edit with **0 deletions** cannot
  have corrected anything. Check that before you downgrade a doc-correction item.
- **Decision — this session's third `/handoff` wrote only an APPEND delta.** The ranked list was
  deliberately left untouched: rank 6 already says "re-measure before editing", the new fact is a
  *warning attached to* that item rather than a change to it, and re-emitting all 8 items under a
  REPLACE heading to add one clause risks dropping a line for no gain. The finding is keyed to
  rank 6 by name in the investigation block above, which is where a reader working that item
  arrives anyway.

- 🔴 **THE WRITE GUARD'S DESIGN, MOVED HERE FROM `State now` SO A FUTURE REPLACE CANNOT EAT IT.**
  Whoever works rank 8 needs it to grade the thing. `scripts/claude-hooks/handoff-write-guard.py`
  is armed on a `/resume` **READ** — a `Read` of `claudedocs/handoff-*.md` / `*HANDOFF*.md`, or the
  `git show <ref>:claudedocs/…` form — and **never on `SessionStart`**. Three conditions: the read,
  REAL WORK after it, and no observable handoff write since. Three satisfaction routes are UNIONED
  (a `handoff_doc.py` run, a Write/Edit of ANY handoff doc, the resumed doc's own mtime); **two are
  session-level ON PURPOSE**, because the 25 drift sessions were scored RECORDED and a path-keyed
  guard would block every one of them. Ladder is `block, block, systemMessage, silent`; `MAX_DOCS 3`;
  dismissal leaves a tombstone; it **fails open** and has one exit, always 0. Registered as 1
  `PostToolUse` + 1 `Stop`, **0 `SessionStart`** — that is the design, not an omission.
  ⚠ Its pre-merge gates were green (`#1092`), which was the first PR of this arc ever seen green —
  a data point for the CI question, never a claim it was closed.
- 🔴 **`clawgate_handoff.sh resolve` returned rc 5 for the THIRD consecutive session of this arc**
  (2026-08-31), with its positive control confirming the board reachable and the token accepted.
  Per `/handoff` step 1 that is an UNRESOLVED session, not a clean bill of health: **no
  `clawgate-task:` field was written and no task was created.** The previous update said two in a
  row was "worth a look before a third assumes it is normal" — this IS the third. **Stop treating
  it as noise.** The rc-5 message is explicit that a wrong session id and a session that touched
  nothing are indistinguishable (both answer `200` with an empty array), so the next session should
  check whether `CLAUDE_CODE_SESSION_ID` is actually being set in this runtime before concluding
  anything about the board.
- 🔴 **THE SUBSYSTEM STORE HELD THE ANSWER TO THIS ARC'S OWN OPEN PROBE, AND `--session` COULD NOT
  SEE THIS SESSION AT ALL.** Two separate lessons from step 4, both measured:
  (a) `subsystem_touch.py --session` **refused** with `transcript cwd does not match` — this session
  ran in `datapacket-talos` while `--repo` was `devrc`, and it correctly told me **not** to fall
  back to the git window (empty for the same reason) but to use `--pr`. A hub session that lands
  PRs elsewhere is the normal shape here, and the session window is structurally blind to it.
  (b) The store's `devrc/tests.md` has carried *"three runs of `tekton/devrc-*` on ONE unchanged
  commit, three different verdicts"* since **2026-08-27** — the exact probe this doc kept listing
  as open. Recall was RUN at kickoff and *listed* that entry while *featuring* another, so it was
  one `--ref` away and never opened. **When a doc names a probe as OPEN, `--ref` the plausible
  entries before running it.**
- **Decision — the audit ladder STOPS on judgement when the PR is docs-only.** `/audit-pr`'s
  mechanical stop is two consecutive rounds of ZERO payload lines. On a docs PR the payload IS the
  `.md`, so every fix round is 100% payload by construction and **the gate can never fire**. Six
  rounds ran with it structurally silent. Say this out loud when opening a ladder on a docs PR.
- **Decision — re-derive every auditor finding before acting on it.** Three of the six rounds'
  headline numbers MOVED when checked first-hand (a byte size, a pipeline count, a "measured
  difference" that was measurably absent). Taking the reports at face value would itself have
  shipped errors.

## How to verify
```bash
# 1. rank 1's split, end to end (the classifier IS committed on #1055)
W=/tmp/chain-work; mkdir -p "$W"
python3 ~/workspace/devrc/scripts/find-session.py "Canonical handoff (read first)" \
  --since 2026-08-15 --limit 500 > "$W/chain2.out" 2>"$W/chain2.err"
test ! -s "$W/chain2.err" || echo "PARTIAL COVERAGE — read chain2.err first"
CHAIN_WORKDIR="$W" bash ~/workspace/devrc/claudedocs/skill-chain-loss-index.sh
CHAIN_WORKDIR="$W" python3 ~/workspace/devrc/claudedocs/skill-chain-loss-classifier.py
#   expect 8 cleanly-ended / 4 interrupted-at-end / 2 context-exhausted / 2 never-started,
#   and a non-empty INSTRUMENT CONTROLS block. A run whose controls are absent is void.

# 2. rank 2's hook — its own suite, and the mutation sweep that certifies its guards.
#    🔴 The sweep EDITS the hook in place and restores it in a `finally`, so give it a CLEAN
#    worktree, never the primary clone.
WT=/tmp/hwg
git -C ~/workspace/devrc worktree add --detach "$WT" origin/zach/handoff-write-guard
(cd "$WT" && PYTHONDONTWRITEBYTECODE=1 python3 -m pytest scripts/claude-hooks/tests/ -q)  # expect 2922 passed
HWG_TREE="$WT" python3 "$WT/claudedocs/handoff-write-guard-mutation-sweep.py"             # expect 29/29 killed
git -C ~/workspace/devrc worktree remove --force "$WT"

# 3. rank 2's hook, END TO END as a real process, in the shape the 8.7% loss actually takes.
#    🔴 This is the verification that matters: the suite proves the parts, this proves the whole.
H=/tmp/hwg-e2e; rm -rf $H; mkdir -p $H/repo/claudedocs $H/home/.claude
G=~/.claude/hooks/handoff-write-guard.py   # the DEPLOYED copy — see rank 7
run(){ printf '%s' "$1" | HOME=$H/home python3 $G; }
run '{"hook_event_name":"PostToolUse","session_id":"e2e","cwd":"/elsewhere","tool_name":"Bash","tool_input":{"command":"git -C '$H'/repo show origin/x:claudedocs/handoff-t.md"}}'
run '{"hook_event_name":"PostToolUse","session_id":"e2e","tool_name":"Edit","tool_input":{"file_path":"/x/y.py"}}'
run '{"hook_event_name":"Stop","session_id":"e2e"}'          # expect {"decision":"block", …}
run '{"hook_event_name":"PostToolUse","session_id":"e2e","tool_name":"Bash","tool_input":{"command":"python3 /r/scripts/lib/handoff_doc.py --repo /r --topic t"}}'
run '{"hook_event_name":"Stop","session_id":"e2e"}'          # expect EMPTY — it self-suppressed

# 3b. rank 3's drift audit. Shares chain2.out/docs.idx/allnames.txt with the classifier
#     above, so run it in the SAME $W. The controls run is NOT optional — a run without
#     them is void, and one of them was silently UNREADABLE until gotcha 8.
CHAIN_WORKDIR="$W" DRIFT_CONTROLS=1 python3 ~/workspace/devrc/claudedocs/skill-chain-drift-audit.py
#   expect 4 lines, ALL beginning OK (positive / negative / sensitivity / write-detector)
CHAIN_WORKDIR="$W" python3 ~/workspace/devrc/claudedocs/skill-chain-drift-audit.py
#   expect 29 DRIFT over 26 distinct docs, 13 abandoned-and-still-open, 5 with no link back,
#   and a CONTROL line near 90% on the maintained arm. If the control arm is far BELOW the
#   drift arm the predicate has changed meaning — re-read it before quoting either number.
#   First run takes ~12m (transcript scan); it caches to $W/written.cache.json, then ~20s.

# 4. the bloat corpus (#1064). Absolute totals DRIFT — re-derive, never quote a comment.
python3 ~/workspace/devrc/scripts/handoff-audit.py \
  ~/workspace/devrc ~/workspace/homelab-talos ~/workspace/civit/datapacket-talos

# 5. the auditor's own suite
nix develop ~/workspace/devrc -c python3 -m pytest \
  ~/workspace/devrc/scripts/tests/test_handoff_audit.py -q -p no:cacheprovider   # expect 23

# 6. rank 4 is DEPLOYED (not merely committed) — expect 0 only AFTER a home-manager switch
grep -c 'preserve both' ~/.claude/skills/clawgate/SKILL.md
```
