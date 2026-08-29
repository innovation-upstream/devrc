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
- **Branch / PR:** `zach/skill-chain-usage-audit` off `origin/main` (devrc). No PR at time of writing.
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

### Why 19 sessions ended without invoking `/handoff` at all
- **Symptom + exact repro:** 19 of 22 losses show NO `handoff_doc.py` invocation anywhere
  in the transcript — not a refused write, an absent one. Reproduce with
  `scratchpad/split.py` (classifier below), bucket `7. step 5 never run`.
- **Observed (with values):** losses by cause — 19 never-run, 2 tool-failed, 1
  proposed-never-confirmed. Named instances: `handoff-skill-prune-campaign.md` (08-20,
  appears TWICE — once unconfirmed, once never-run), `handoff-ci-flakes-and-misattribution.md`
  (08-27), `handoff-app-store-ui-feedback.md` (08-23), `handoff-agent-attention-tooling.md`
  (08-22), `handoff-clickup-clawgate-mirror.md` (08-22).
- **Ruled out:** *"much of the gap is the skill correctly declining"* — killed, zero
  `no-change`/`no-advance` in the loss set. *"docs are written but never committed"* —
  killed, 0 cases of an on-disk untracked handoff doc across 154 repos.
- **Leading hypothesis:** `/handoff` is invoked by INTENT, and a session that runs out of
  context, is interrupted, or simply stops never forms it. The clawgate half of the chain
  has a `Stop` hook enforcing write-back and scores 86%; the handoff half has no
  equivalent and its failure mode is exactly "session ended". The asymmetry is visible in
  the two numbers.
- **Next probe:** decide whether a `Stop`-hook analogue is warranted, and whether it can be
  made non-annoying. Before building anything, measure how many of the 19 were
  context-exhausted vs cleanly ended — the transcripts carry that, and the two want
  different remedies (an auto-draft vs a nudge).

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
1. **Split the 19 never-run losses into context-exhausted vs cleanly-ended** (devrc;
   reads `~/.claude/projects/**/*.jsonl`, no repo edit). Decides whether the remedy is a
   hook, a nudge, or nothing. Cheapest item and it gates item 2.
2. **Decide on a handoff-write `Stop` hook** (devrc `claude/hooks/`, `~/.claude/settings.json`).
   Only after 1. The clawgate writeback guard (`~/.claude/hooks/clawgate-writeback-guard.py`)
   is the working precedent — arm-on-read, block-on-Stop, 86% compliance.
3. **Audit the 25 drift cases for stale abandoned docs** (devrc + datapacket-talos +
   homelab-talos `claudedocs/`). Cheap, and it feeds `/resume` quality directly.
4. **Deploy the clawgate SKILL.md fix** — `home-manager switch`, then confirm
   `~/.claude/skills/clawgate/SKILL.md` no longer contains "preserve both".

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
Classifier scripts used for the split live in this session's scratchpad
(`split.py`, `verifyE.py`, `reconcile2.py`); they are throwaway and are NOT committed —
re-derive from the commands above rather than hunting for them.
