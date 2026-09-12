---
# No clawgate-task field — exit 5, nothing resolved for this session.
---
# Handoff: handoff-resume-skill-trace — 2026-09-12

## Run this first — the index, one command
```bash
cairn recall --repo ~/workspace/devrc
```
Terse pointers this doc does not carry, curated by past sessions and outliving it.
RECALL, NOT LIVE OBSERVATION — every line is a pointer to VERIFY, never a current
reading, and it may describe a gotcha already fixed. Non-blocking: if it exits
non-zero, print the stderr line and carry on.

## Goal
Trace the handoff and resume skills end-to-end: their flows, cross-references,
shared infrastructure, and usage patterns. No code changes — pure analysis.

## State now
- Branch: `main`, behind origin by 1 commit (clean working tree)
- What's DONE this session:
  - Read both `claude/skills/handoff/SKILL.md` (174 lines) and `claude/skills/resume/SKILL.md` (264 lines) in full
  - Produced structured trace of both skills: key flows, critical invariants, cross-references, shared infrastructure
  - Checked recent usage via: filesystem (100+ handoff docs in devrc, most recent 2026-09-12 13:51), skill-usage-audit (handoff: 219 attributed/130 invoked/153 typed; resume: 85 attributed/80 invoked/5 typed, workbench-only), cairn recall (32 devrc entries, 14 with OPEN bullets), claim-work (31 active refs on origin)
  - 🔴 **CORRECTED 2026-09-12** — the "resume skill's laptop contribution is zero" line that
    was here is REFUTED. Re-measured against ClickHouse `activity.events`: laptop `/resume`
    is lower than the workbench (10.6% vs 16.2% of sessions, trailing 30 days) but was never
    zero in any window checked, including this doc's own original 6-day window (8-12
    sessions depending on the boundary). See "Open investigations" below for the full
    refutation, including the fused-scopes error that produced the original "zero" claim.
- What's IN FLIGHT: none — this was a read-only analysis session
- Deploy/verify status: N/A — no code changes

## Open investigations — live diagnosis state

### Resume skill: zero laptop contribution — CLOSED, REFUTED 2026-09-12
🔴 **REFUTED**, re-measured directly against ClickHouse `activity.events` on 2026-09-12. The
claim below is wrong in **two independent ways** — a wrong number, and a fused-scopes error
that produced the wrong number. Kept below as originally written, not silently rewritten.

- **Symptom + exact repro (as originally claimed):** `handoff-search-index.md` reports 0
  `/resume` runs from the laptop during the 6-day measurement window (284 sessions, two
  hosts). The workbench accounts for all 85 attributed sessions.
- **Observed (with values) (as originally claimed):** skill-usage-audit.md line 105:
  `resume | A | 85 | 80 | 5`. The `commands_typed` column (5) suggests minimal manual
  invocation. The laptop gap may be a measurement artifact (short window) or a real
  behavioral pattern (workbench is the primary dev host).
- 🔴 **Error 1 — wrong number at the stated scope.** Measured 2026-09-12: 8-12 laptop resume
  sessions in the skill-usage-audit's own 6-day window, depending on where the boundary
  falls (12 for a 2026-08-29 start, 9 for 08-30, 8 for 08-31, all ending 2026-09-05 00:00
  UTC) — every day from 2026-08-28 through 2026-09-04 has at least one laptop resume
  session. Never zero.
- 🔴 **Error 2 — fused scopes: the two halves of the original claim come from different
  documents measuring different things, glued into an attribution neither doc makes.** The
  `resume | A | 85 | 80 | 5` row is `claudedocs/handoff-skill-usage-audit.md` line 105, in a
  table that **has no host column at all** — it was never split by host, so "the workbench
  accounts for all 85 attributed sessions" is an inference this session invented, not
  something measured. The "0 laptop" figure instead comes from
  `claudedocs/handoff-handoff-search-index.md` (its line 120 and lines ~263-281), which
  measured a **different quantity** (`resume-state.sh` shell invocations found by grepping
  transcripts, not the skill-usage-audit's attributed/invoked/typed ClickHouse fields), over
  a **different window** (since PR #1332 merged, 2026-09-06T17:51:19Z, ~8.6 h — not the
  6-day window this block cites), with a **different instrument** (ssh + transcript grep,
  not ClickHouse). Gluing the 85-row table to the "0 laptop" figure produced a claim neither
  source document makes.
- **Ruled out:** that the instrument is blind to the laptop — 76 laptop sessions carry
  non-empty skill fields in the last 30 days, and laptop `source='claude'` rows are current
  (19,200 rows, latest 2026-09-12 18:48 UTC). via: measurement, ClickHouse `activity.events`,
  2026-09-12
- 🔴 **Leading hypothesis, also refuted.** "The laptop is used for lighter tasks" does not
  survive a behavioural check: `activity-scan.py --days 14`, `host='laptop'` (its i3/attention
  sections are laptop-scoped by default) — 4,013.6 min (~67 h) Alacritty attention, 75
  deep-work blocks >=10 min, 34 >=25 min in the trailing 14 days. The laptop is a primary
  machine, not a light-task machine.
- **Measured for the record (ClickHouse `activity.events`, 2026-09-12, `source='claude'` +
  `kind='session-summary'` rows, distinct sessions = union of `skills_used` /
  `skills_invoked` / `commands_typed`):**
  - Laptop `/resume`, trailing 30 days: **17 sessions** (15 via `skills_invoked`, 2 via
    `commands_typed`). Workbench: **158**. Laptop total Claude sessions 161, workbench 976 —
    resume rates 10.6% (laptop) vs 16.2% (workbench). Lower than the workbench, not zero.
  - Laptop 30-day skill leaderboard (sessions): handoff 51, audit-pr 43, subsystem-index 41,
    resume 17, login 10, signal 9, obs-read 7, clawgate 5.
- **Next probe:** none — closed. The `--days 30` probe this block originally proposed could
  not be run; see "Next steps" rank 1 for why and for the in-flight fix.

## Next steps (ranked)
1. 🔴 **DONE, closed 2026-09-12** — Investigate the resume laptop gap — run the activity scan
   to determine if this is a measurement artifact or a real host-preference pattern.
   Outcome: REFUTED, not confirmed — see "Open investigations" above. The gap was a wrong
   number produced by fusing two documents' different measurements; the real laptop resume
   rate is lower than the workbench's but never zero.
   ⚠ **The probe this rank named, `activity-scan.py --days 30`, could not run**: it fails
   with ClickHouse `Code: 241 ... memory limit exceeded ... While executing
   JoiningTransform` (server cap 2.50 GiB), raised from `q_browser_by_domain`.
   `--days 14` succeeds and is what produced the behavioural figures above. A separate PR is
   in flight fixing the `--days 30` memory failure — reference it as in-flight rather than by
   number until it lands.
   forcing: none
2. No other next steps — this was a self-contained analysis task
   forcing: none

## Gotchas / decisions / dead-ends
- The handoff skill is 174 lines; the resume skill is 264 lines — resume is longer because it carries the cairn recall interface (the store's read surface) and the claim-work lock protocol
- Both skills share `clawgate_handoff.sh` as a parser for the `clawgate-task:` front matter
- The handoff skill's step 5 (`handoff_doc.py`) is the only step that commits — a measured hazard where a session wrote the doc directly and it ended untracked
- The resume skill's step 2 (`resume-state.sh`) runs BEFORE reading the doc to determine which copy is authoritative — a stale clone can serve a handoff 276+ lines behind origin

## How to verify
- The trace above was produced by reading both skill files — verify by reading them: `cat ~/.claude/skills/handoff/SKILL.md` and `cat ~/.claude/skills/resume/SKILL.md`
- Usage numbers come from `handoff-skill-usage-audit.md` (lines 102, 105) — verify by reading that doc
- Cairn index output was captured live from `cairn recall --scope devrc` — 32 entries, 14 with OPEN bullets
- Claim-work ref count was captured live from `git ls-remote --heads origin 'refs/heads/claim/*'` — 31 refs
