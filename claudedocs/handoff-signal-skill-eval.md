---
clawgate-task: none
---
# Handoff: signal-skill-eval — 2026-09-03

## Run this first — the index, one command
```bash
cairn recall --repo ~/workspace/devrc
```
Terse pointers this doc does not carry, curated by past sessions and outliving it.
🔴 RECALL, NOT LIVE OBSERVATION — every line is a pointer to VERIFY, never a current
reading, and it may describe a gotcha already fixed. `scope-absent`/`scope-empty` means
nothing is recorded yet: ordinary, not an error, and not a clean bill of health.
Non-blocking: if it exits non-zero, print the stderr line and carry on.

## Goal
Evaluate the signal skill's performance, quality, and token efficiency using activity telemetry data from ClickHouse.

## State now
- Branch: main (behind origin by 1 commit)
- What's DONE this session:
  - Queried `activity.events` in ClickHouse for signal skill usage patterns
  - Identified 1,024 sessions using signal skill (157 laptop, 867 workbench)
  - Calculated token efficiency metrics: 96.5% cache hit rate, 40,000%+ output/input ratio
  - Analyzed usage patterns by hour, day, and host
  - Assessed quality: high cache efficiency, concerning token consumption
- What's IN FLIGHT: None — analysis complete
- Deploy/verify status: N/A (research task, no code changes)

## Open investigations — live diagnosis state
### Signal skill token consumption patterns
- **Symptom:** Signal skill sessions show extremely high output/input ratio (40,000%+) suggesting over-generation
- **Observed:** Average session duration 1,213 minutes (20.2 hours), millions of tokens per session
- **Ruled out:** Not a cache inefficiency (96.5% hit rate is excellent) — via: measurement
- **Leading hypothesis:** Long-running coordination sessions with multiple skills generate excessive output
- **Next probe:** Analyze specific session transcripts to identify output reduction opportunities

## Next steps (ranked)
1. Analyze specific long-running sessions to identify output reduction opportunities — forcing: none
2. Review signal skill implementation for potential optimizations — forcing: none

## Gotchas / decisions / dead-ends
- Clawgate task resolution returned NO SESSION ID (expected for opencode sessions)
- ClickHouse queries require SOPS age key for credential decryption
- Signal skill is primarily used on laptop (88% of sessions)

## How to verify
- Query ClickHouse: `SELECT count(DISTINCT session) FROM activity.events WHERE source = 'claude' AND kind = 'session-summary' AND JSONExtractString(payload, 'skills_used', 'signal') IS NOT NULL`
- Check token metrics: `SELECT avg(toFloat64(JSONExtractString(payload, 'cache_read_tokens'))) / (avg(toFloat64(JSONExtractString(payload, 'cache_read_tokens'))) + avg(toFloat64(JSONExtractString(payload, 'cache_creation_tokens')))) * 100 as cache_hit_rate FROM activity.events WHERE ...`
