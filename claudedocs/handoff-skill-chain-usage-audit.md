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
- **Branch / PR:** `zach/skill-chain-usage-audit` off `origin/main` (devrc) → **devrc#1055,
  OPEN and BLOCKED**. Both Tekton legs return `COULD NOT RUN: the gate stopped before this
  leg reported` on both SHAs. 🔴 **It is not this branch** — every PR touched after
  ~22:23Z reads `ERROR,ERROR` (#1048–#1050, #1055–#1059); the last clean run was #1054 at
  22:06Z. Pointer for whoever fixes it: in ns `tekton-ci` the recent `devrc-ci-*` runs have
  `notify-pod` and `report-pod` but **no `gate-pod`**, while sibling `vetr-app-unit-*` runs
  do; all `tekton-pipelines` controllers are `Running` and the node is `Ready`, so it is
  neither a controller nor a node outage.
- **DONE — the measurement.** Window 2026-08-15 → 08-29, both hosts, both runtimes
  (`find-session` stderr silent on every run ⇒ full coverage per its own contract).
  - 391 sessions match `"Canonical handoff (read first)"`; **256** carry a `/resume`
    kickoff as their genesis line; **253 graded** (98.8%).
  - **231 / 253 = 91.3% RECORDED** — 206 updated the doc they resumed, 25 landed under a
    different topic (genuine topic drift, not loss).
  - **22 / 253 = 8.7% real loss** — 19 never invoked `handoff_doc.py` at all, 2 tool
    failures (`behind`/`failed`), **1** reached `status=proposed` and never confirmed.
  - **Legitimate declines (`no-change`/`no-advance`): ZERO.** The hypothesis that a chunk
    of the gap was the skill correctly refusing to write is REFUTED.
- **DONE — clawgate leg.** 186 tasks created in-window; of the **85** that advanced past
  `open`, **73 (86%) carry a write-back comment**. 12 advanced with zero comments
  (6 `complete`, 4 `in_progress`). **0** of the 186 were agent-dispatched.
- **DONE — a doc fix**, in this branch: `claude/skills/clawgate/SKILL.md` line 61 said the
  `Stop` array carries "**two**" unrelated hooks and to "preserve both". Live it carries
  **four** (`task-hook.sh`, `claude-notify.py`, `next-step-nudge.py`, `agent-ledger-hook.py`)
  beside clawgate's own two. Rewritten to be count-independent + a re-derive command.
- **Deploy/verify status:** the SKILL.md edit is committed only. It is NOT live — the
  deployed copy is a nix `home.file` at `~/.claude/skills/`, so it needs a
  `home-manager switch` (or `scripts/ship.sh`), never a `git pull`.

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

## Next steps (ranked)
1. ✅ **DONE (2026-08-29)** — split measured: **8 cleanly-ended vs 2 context-exhausted**
   of 16, plus 4 interrupted-at-end and 2 never-started. See the closed investigation
   above. It selects a **nudge**, and shows a `Stop` hook already fires in 8/8 of the
   dominant bucket.
2. **Build the handoff-write `Stop` hook** (devrc `claude/hooks/`, `~/.claude/settings.json`).
   No longer "decide" — item 1 decided it. The clawgate writeback guard
   (`~/.claude/hooks/clawgate-writeback-guard.py`) is the working precedent — arm-on-read,
   block-on-Stop, 86% compliance. 🔴 Arm it on a `/resume` READ, not on session start, or
   it fires on every session that never touched a handoff. The 2 never-started sessions
   are the reminder that a hook cannot reach a session that produced no turns.
3. **Audit the 25 drift cases for stale abandoned docs** (devrc + datapacket-talos +
   homelab-talos `claudedocs/`). Cheap, and it feeds `/resume` quality directly.
4. **Deploy the clawgate SKILL.md fix** — `home-manager switch`, then confirm
   `~/.claude/skills/clawgate/SKILL.md` no longer contains "preserve both".
   🔴 Gated on #1055 merging, which is gated on the CI outage in *State now*.
5. **Act on the handoff-doc bloat audit** — `claudedocs/proposal-handoff-doc-bloat.md`
   (landed in this branch). Measured: handoff docs grow **monotonically** (121 of 123
   revisions grew or held; 16 of 18 docs never shrank once; ×2.55 first→latest), and
   **~35–44%** of what a `/resume` pays for is dead-but-dated or belongs in a skill.
   `/handoff` has no budget, no archive step and no eviction verdict; archive adoption
   across 413 docs is **0**. Proposal adapts `prune-skill`'s method. **Propose-only — the
   SKILL.md change is not written.** Cheapest first move is `handoff-audit.py`, no gate.

🔴 **This list is a WORK QUEUE, and `claim-work` is its LOCK** — every
`/resume` session draws from it, so a *better* ranked list produces *more*
duplicate work, not less. **NUMBER the items and keep the numbering stable:
the rank is half a claim's identity** (`claim-work --slug-for <this doc>
<rank>`), and re-ranking silently re-points every live claim. Make each item
cheap to check — name the repo and the files it will touch, and mark anything
in flight `IN FLIGHT: <repo>#<pr>`; that marker is the SOFT half, the lock is
the command `/resume` step 6 runs before touching an item. Worktrees do NOT
prevent this. 📖 `~/.claude/skills/handoff/reference/shared-queue.md`.

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

## How to verify
```bash
# 1. the population (~30 s; stderr MUST be silent or coverage is partial)
python3 ~/workspace/devrc/scripts/find-session.py "Canonical handoff (read first)" \
  --since 2026-08-15 --limit 500 > /tmp/chain.out
grep -c "opened: '/resume" /tmp/chain.out          # expect 256 for this window

# 2. the doc index — --all is load-bearing, HEAD-only loses ~33%
for g in $(find ~/workspace -maxdepth 4 -type d -name .git); do r=$(dirname "$g"); \
  git -C "$r" log --all --since=2026-08-15 --pretty=format:'C%x09%h%x09%ad' --date=short \
  --diff-filter=AM --name-only -- 'claudedocs/*handoff*' ; done | grep -c '^C'

# 3. the clawgate leg
clawgatectl task ls --summary --limit 400 | jq '[.[]|select(.createdAt>="2026-08-15" and .status!="open")] | {advanced: length, no_comment: [.[]|select((.commentCount//0)==0)]|length}'

# 4. the SKILL.md fix is DEPLOYED (not merely committed)
grep -c 'preserve both' ~/.claude/skills/clawgate/SKILL.md   # expect 0 AFTER a home-manager switch
```
```bash
# 5. item 1's split, end to end. The classifier IS committed this time (see below).
W=/tmp/chain-work; mkdir -p "$W"
python3 ~/workspace/devrc/scripts/find-session.py "Canonical handoff (read first)" \
  --since 2026-08-15 --limit 500 > "$W/chain2.out" 2>"$W/chain2.err"
test ! -s "$W/chain2.err" || echo "PARTIAL COVERAGE — read chain2.err before believing anything"
CHAIN_WORKDIR="$W" bash ~/workspace/devrc/claudedocs/skill-chain-loss-index.sh
CHAIN_WORKDIR="$W" python3 ~/workspace/devrc/claudedocs/skill-chain-loss-classifier.py
#   expect: 8 cleanly-ended / 4 interrupted-at-end / 2 context-exhausted / 2 never-started
#   and the INSTRUMENT CONTROLS block non-empty. A run whose controls are absent is void.
```

🔴 **The prior pass's classifiers (`split.py`, `verifyE.py`) were left in a session
scratchpad as "throwaway", and item 1 therefore cost a FULL re-derivation** — fresh
population, fresh 154-repo commit index, a rewritten classifier, and two defects
re-discovered from scratch (gotchas 5 and 6). **That is why these are committed:**
`claudedocs/skill-chain-loss-classifier.py` and `claudedocs/skill-chain-loss-index.sh`,
both parameterised on `CHAIN_WORKDIR` and both verified from the committed copy to
reproduce the numbers above. `reconcile2.py`/`final.py` (the bucket-E verifier) remain
uncommitted in the earlier session's scratchpad.
