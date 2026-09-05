---
clawgate-task: 
---
# Handoff: activity-audit-context-dead-weight — 2026-09-04

## Run this first — the index, one command
```bash
cairn recall --repo ~/workspace/devrc
```
Terse pointers this doc does not carry, curated by past sessions and outliving it.
RECALL, NOT LIVE OBSERVATION — every line is a pointer to VERIFY, never a current
reading, and it may describe a gotcha already fixed. `scope-absent`/`scope-empty` means
nothing is recorded yet: ordinary, not an error, and not a clean bill of health.
Non-blocking: if it exits non-zero, print the stderr line and carry on.

## Goal
Cross-reference the activity telemetry pipeline (14-day window) with the opencode context (skills, agents, tools) to identify dead weight: unused sources, stale subsystems, and zero-use skills that bloat the always-on context cost.

## State now
- Branch: main (clean, no uncommitted changes beyond 4 untracked files)
- No open PR
- No clawgate task (session ID was unset — board not reached)

### Completed this session
- **Deadman check**: all 19 sources alive, 0 dead, 0 presence-stalled
- **14-day activity query**: 16,224 rows across 10 sources on 2 hosts
- **Session-insight (Layer B) stall identified**: last row 2026-07-30 (36 days stale), 164 total rows ever, manual CLI only (`scripts/session-analysis/session_insight/cli.py`), no systemd timer
- **Skill usage cross-reference**: 43 skills in opencode context; 20 with zero uses in 14d (confirmed via both Claude `skills_used` AND opencode `skill` tool calls)
- **Overlap analysis**: `check-clickup-addressed` ⊂ `clickup`, `window-triage` ⊂ `session-manager`, `verify-agent` ⊂ `audit-pr`, `initiatives` overlaps `initiative-scan`
- **Opencode context mismatch**: `arr-stack` and `check-cluster` callable in opencode but not in `available_skills`
- **AGENTS.md size**: 47,761 bytes; skill listing adds ~6.5KB context per session

### Recommendations produced (7 items, ranked)
1. Automate Layer B (session-insight) — add systemd timer for `prepare` step
2. Tier 20 zero-use skills to B in `skill-tiers.json` (~3KB context savings)
3. Merge `check-clickup-addressed` into `clickup`
4. Merge `window-triage` into `session-manager`
5. Decide `initiatives` vs `initiative-scan` boundary
6. Investigate `arr-stack` / `check-cluster` dead references in opencode
7. Move `close-the-loop` to `reference/` (process tool, not task tool)

## Open investigations — live diagnosis state

### Layer B (session-insight) non-generation
- **Symptom:** `session-insight` kind has 0 rows since 2026-07-30. Layer A (`session-summary`) continues normally (3,884 rows in 14d).
- **Observed:** `scripts/session-analysis/session_insight/cli.py` exists with subcommands `status`/`prepare`/`write`. No systemd timer, no cron, no hook triggers it. Last data: 164 rows from 2026-07-09 to 2026-07-30. Reference doc (`activity/reference/session-insights.md`) describes it as a manual process with `prepare --limit ~6` batching.
- **Ruled out:** collector not running (all sources healthy) — via: measurement (deadman check). Code removed (CLI exists and imports resolve) — via: code (read cli.py and its imports).
- **Leading hypothesis:** Operator simply stopped running it manually after the initial 3-week exploration. No automation was ever added.
- **Next probe:** Check `~/.local/state/activity/` for any staging/input.json remnants from the last run. Run `python3 scripts/session-analysis/session_insight/cli.py status` with CH creds to see pending count.

### Opencode skill catalogue mismatch
- **Symptom:** `arr-stack` and `check-cluster` appear in opencode `skill` tool calls but are not in `available_skills`.
- **Observed:** 112 skill tool calls in 14d, 21 distinct skill names. `arr-stack` (7 calls), `check-cluster` (1 call) not in the 43 listed skills.
- **Ruled out:** Nothing — not investigated further. via: assumed
- **Leading hypothesis:** These are aliases or removed skills still callable by name.
- **Next probe:** `rg -r "arr-stack\|check-cluster" ~/workspace/devrc/claude/skills/` to find definitions or references.

## Next steps (ranked)
1. Automate Layer B: add a systemd timer for `session_insight prepare` (weekly), keep `write` manual. Touches: `nix/home.nix` (systemd user timer), `scripts/session-analysis/session_insight/cli.py` (may need env wrapper). forcing: none
2. Tier 20 zero-use skills to B: run `scripts/sync-skill-tiers.py --dry-run` to preview, then `--apply`. Touches: `claude/skill-tiers.json`. forcing: none
3. Investigate `arr-stack` / `check-cluster` references: grep for definitions, decide register-vs-retire. forcing: none
4. Merge overlapping skills (`check-clickup-addressed` → `clickup`, `window-triage` → `session-manager`). forcing: none

## Gotchas / decisions / dead-ends
- `skills_used` in ClickHouse undercounts by ~40% vs `find-session` transcript search — always use `find-session` for authoritative skill usage, never `skills_used` alone
- `JSONExtractString(payload,'skills_used','<name>') IS NOT NULL` is always true (returns `''` for missing) — must use `!= ''` predicate
- `session-summary` rows are append-only — dedupe with `argMax(<field>, ingested_at)` grouped by session
- SOPS secrets require temp file (process substitution fails with sops unmarshal error)

## How to verify
- Re-run deadman: `python3 ~/workspace/devrc/scripts/collector/deadman.py`
- Re-run 14d query: `curl -s --user "activity_reader:$RPW" --data-binary "SELECT source, count() FROM activity.events WHERE ts >= now() - INTERVAL 14 DAY GROUP BY source ORDER BY count() DESC FORMAT TSV" "$CH/"`
- Check Layer B status: `python3 ~/workspace/devrc/scripts/session-analysis/session_insight/cli.py status`
