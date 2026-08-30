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
  gate. Legitimate declines (`no-change`/`no-advance`): **ZERO** — the hypothesis that the gap
  was the skill correctly refusing to write is REFUTED. Clawgate leg: of the 85 tasks that
  advanced past `open`, **73 (86%) carry a write-back comment**; **0** of 186 were
  agent-dispatched.
- **Branch / PR:** `zach/skill-chain-usage-audit` → **devrc#1055, OPEN**, `a5982acc`. Second
  PR from this arc: **devrc#1064, OPEN**, branch `feat/handoff-audit`, head `466a0938`
  (5 commits: the tool + four audit-ladder fix rounds).
- 🔴 **Neither PR has ever been seen GREEN in CI, and neither failure is attributable to its
  diff.** See the open investigation below before re-diagnosing either.
- **DONE — rank 1 (the gating item).** 16 never-run losses split by session end-state:
  **8 cleanly-ended · 4 interrupted-at-end · 2 context-exhausted · 2 never-started.**
  Classifier committed this time: `claudedocs/skill-chain-loss-classifier.py` +
  `claudedocs/skill-chain-loss-index.sh`, both parameterised on `CHAIN_WORKDIR` and verified
  from the committed copy to reproduce the numbers.
- **DONE — rank 5's first move.** `scripts/handoff-audit.py` + `scripts/tests/test_handoff_audit.py`
  (23 tests) on #1064, plus `claudedocs/proposal-handoff-doc-bloat.md`. Corpus, 414 docs:
  **7.74 MB ≈ 1.94M tokens · 203 over the 12,288 B reference target · 44 over the 40,960 B cap ·
  ~14% structurally-terminal.** 🔴 That 14% is a FLOOR — the tool sees only content that is
  *structurally marked* terminal; the read-based estimate over 5 docs was 35–44%.
- **Deploy/verify status:** nothing is deployed. The clawgate `SKILL.md` fix (rank 4) is
  committed on #1055 and NOT live — it needs `home-manager switch` after that PR merges.
  `handoff-audit.py` is committed on #1064 and wired into no gate, skill or script.

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

### Whether the 25 "topic drift" sessions should count as recorded
- **Symptom:** 25 sessions resumed doc X and wrote doc Y (`clawgate-usage-audit` →
  `clawgatectl-agent-delivery`; `app-store-copy-and-platform` → `appblock-tool-calling`).
- **Observed:** counted as RECORDED here, because the work IS on disk and committed.
- **Leading hypothesis:** this is healthy — scope legitimately moves — but it means the
  resumed doc goes stale while looking maintained, and nothing links X to Y.
- **Next probe:** check whether the ABANDONED doc (X) was left with a status header that
  still claims in-flight work. If so, that is a silent staleness generator feeding
  `/resume`'s own known "open-investigation blocks read as current forever" trap.

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
- **Leading hypothesis:** the Tekton runner's environment differs from both local tiers, and the
  affected population is exactly "tests that read host state". Under node congestion the
  timing-shaped ones join in.
- **Next probe:** re-run one PR's gate on an unchanged SHA when the queue is empty and see
  whether the SAME test fails. A different test ⇒ environment/timing; the same test ⇒ a real
  runner-vs-local divergence worth fixing in the test.

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

## Next steps (ranked)
1. ✅ **DONE (2026-08-29)** — the split: 8 cleanly-ended vs 2 context-exhausted of 16. Selects a
   **nudge**, and shows a `Stop` hook already fires in **8/8** of the dominant bucket.
2. 🔴 **Build the handoff-write `Stop` hook — the largest outstanding item, and the whole point
   of rank 1** (devrc `claude/hooks/`, `~/.claude/settings.json`). Precedent:
   `~/.claude/hooks/clawgate-writeback-guard.py` — arm-on-read, block-on-Stop, 86% compliance.
   Arm it on a `/resume` READ, not on session start, or it fires on every session that never
   touched a handoff. The 2 never-started sessions are the reminder that a hook cannot reach a
   session that produced no turns.
3. **Audit the 25 drift cases for stale abandoned docs** (devrc + datapacket-talos +
   homelab-talos `claudedocs/`). Untouched. Feeds `/resume` quality directly.
4. **Deploy the clawgate SKILL.md fix** — `home-manager switch`, then confirm
   `~/.claude/skills/clawgate/SKILL.md` no longer contains "preserve both".
   🔴 IN FLIGHT / blocked: needs devrc#1055 merged.
5. **Act on the handoff-doc bloat proposal** — `claudedocs/proposal-handoff-doc-bloat.md`.
   The auditor shipped (IN FLIGHT: devrc#1064); the **`/handoff` SKILL.md change is deliberately
   NOT written** — operator chose propose-only. Next move is the eviction contract
   (budget + step 4.5 + EVICT_HISTORY/RELOCATE_DURABLE/KEEP_HOT), not a gate: the caps file for
   gate 11 records that the general ratchet rule was replayed over 365 days / 901 commits and
   **REFUTED**, so re-run that replay for handoff docs before proposing one.
6. **Correct the `tekton` skill's stale devrc-ci claims** (devrc `claude/skills/tekton/SKILL.md`).
   Measured 2026-08-29: the gate pod has **no `nodeSelector`** and uses a per-node **hostPath** at
   `/var/lib/mnt/disk-1/devrc-ci-nix-cache`; the skill still describes node-pinning to
   `talos-xr6-r7p` and a `nix-store-cache` PVC. That skill loads as authoritative for anyone
   debugging this gate. Re-measure before editing — it is a live label/volume, not a git fact.

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

# 2. the bloat corpus (#1064). Absolute totals DRIFT — re-derive, never quote a comment.
python3 ~/workspace/devrc/scripts/handoff-audit.py \
  ~/workspace/devrc ~/workspace/homelab-talos ~/workspace/civit/datapacket-talos

# 3. the tool's own suite
nix develop ~/workspace/devrc -c python3 -m pytest \
  ~/workspace/devrc/scripts/tests/test_handoff_audit.py -q -p no:cacheprovider   # expect 23

# 4. rank 4 is deployed (NOT merely committed) — expect 0 only AFTER a home-manager switch
grep -c 'preserve both' ~/.claude/skills/clawgate/SKILL.md
```
