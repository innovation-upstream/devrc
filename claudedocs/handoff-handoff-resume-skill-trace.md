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
  - Verified resume skill's laptop contribution is zero during measurement window
- What's IN FLIGHT: none — this was a read-only analysis session
- Deploy/verify status: N/A — no code changes

## Open investigations — live diagnosis state

### Resume skill: zero laptop contribution
- **Symptom + exact repro:** `handoff-search-index.md` reports 0 `/resume` runs from the laptop during the 6-day measurement window (284 sessions, two hosts). The workbench accounts for all 85 attributed sessions.
- **Observed (with values):** skill-usage-audit.md line 105: `resume | A | 85 | 80 | 5`. The `commands_typed` column (5) suggests minimal manual invocation. The laptop gap may be a measurement artifact (short window) or a real behavioral pattern (workbench is the primary dev host).
- **Ruled out:** Nothing measured yet — this is a fresh observation.
  via: measurement (the gap is real in the data)
- **Leading hypothesis:** Zach works primarily on the workbench; the laptop is used for lighter tasks or the measurement window didn't capture a laptop-heavy period.
- **Next probe:** `python3 ~/workspace/devrc/scripts/session-analysis/activity-scan.py --days 30 --json` to check if zsh/tmux/i3 patterns show laptop usage that doesn't translate to `/resume` invocations.

## Next steps (ranked)
1. Investigate the resume laptop gap — run the activity scan to determine if this is a measurement artifact or a real host-preference pattern
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
