---
---
# Handoff: cross-subsystem-analysis — 2026-08-24

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
Comprehensive identification, tracing, and analysis of five subsystems: subsystem-index, session-manager, check-clickup-addressed, clawgate, and object-leak. Produced a reflection, evaluation, and recommendations across the stack.

## State now
- Branch: `main` (behind origin/main by 1 commit)
- No commits, no PRs, no code changes this session — pure analysis
- No deploy/verify state

## Subsystem analysis delivered

### 1. Subsystem Index (`analyze-service-index`)
- Core library: `scripts/lib/subsystem_resolver.py` (1,753L), `subsystem_touch.py` (6,032L), `subsystem_recall.py` (3,063L)
- Store: `~/.claude/analyze-service-index/<scope>/<slug>.md` — each scope is independent git repo
- Lifecycle: write via handoff → autocommit hourly (sandboxed) → backup daily to MinIO (age-encrypted) → prune via audit tool
- 38,490 lines of tests across 11 files
- Cross-scope query gap identified: API serves one scope at a time

### 2. Session-Manager
- Main: `scripts/session-manager` (4,395L) — read-only cross-host tmux + agent-activity view
- 6 data sources: tmux panes/windows, batched capture, agent ledger, clawgate cache, ClickHouse, fuzzyclaw (opt-in, off by default)
- `waiting_probable`: 3 regex signals, 11/11 precision. `unsent_prompt`: separate signal, never summed
- 18,718 lines of tests across 6 files
- Dependency fan-out: 3 independently deployed writers can drift

### 3. Check-ClickUp-Addressed
- 4 scripts (~2,611L): orchestrator, clickup fetcher, session searcher, completion extractor
- Default mode (~21s, ClickUp only) nearly always correct; transcript mode (~90s) OFF since 2026-08-22
- Self-run guard drops 81% of matching transcripts (prior runs of the checker itself)
- Keep-open veto: STRONG (absolute) vs WEAK (downgrades when closure claim exists)
- 226 collected tests, 82 mutants, 8 rounds of debugging

### 4. Clawgate
- Go + htmx PWA: approval hook → Task API (121 routes, Postgres) → agent dispatch (kubeclaw pods) → browser extension → Flux deploy
- LAN NodePort fully unauthenticated since 0.7.37; `DELETE /api/tasks/{id}` tears down agent pods; `POST /api/auto-approve-all` is global kill switch
- 2 devrc-side enforcement hooks: interview guard (blocks criteria-less create), writeback guard (escalates: 2 blocks → 1 systemMessage → silent)
- `clawgatectl` built from local homelab-talos working tree; version read from Go source
- GitOps from `trunk` deploys manifest, not code — `git log` is NOT evidence code is live

### 5. Object-Leak
- Not a file — anchor section in `claude/RULES-ARCHIVE.md` (lines 1416–1505)
- Measurement: 30-day issue survival ~47% vs PR ~5% (9× gap)
- 96% of 90-day net growth landed in last 30 days (growth, not backlog)
- Duplication NOT the channel (~2% near-dupes, 0 exact)
- 42% of open issues reference already-merged PRs (provenance, not "done but never closed")
- Tier system: T1 deterministic (auto-closable), T2 evidential (human judgement), T3 none (forbidden)
- Stamping (`agent/<producer>` label) still IN FLIGHT

## Findings and recommendations

### Strengths across the stack
- Consistent "never writes" invariant on library layer (subsystem-index, session-manager, check-clickup-addressed)
- Silent-zero discipline everywhere (classified empties, not_measured populations)
- Mutation-tested suites, not just green (82 mutants on clickup, mutation sweep on session-manager)
- Honest admissions of failure (fuzzyclaw off by default, transcript scan off by default, object-leak stamping not shipped)

### Recommendations
1. **Close clawgate auth gap**: enforce `requireHookToken` on `POST /api/auto-approve-all` — it's already "enforce-when-set" on other machine endpoints
2. **Retire transcript mode from check-clickup-addressed**: off since 2026-08-22, produced all false verdicts, ClickUp-side default nearly correct; archive the code, keep self-run guard as library
3. **Add cross-scope query to subsystem-store-api**: `GET /api/search?q=<term>` across all scopes — unlocks the queries the index was built for
4. **Ship object-leak stamping or retract proposal**: `agent/<producer>` label has been "IN FLIGHT" since the measurement; either ship it or remove the deferred promise
5. **Add reconciliation signal between clawgate ClickUp mirror and check-clickup-addressed**: daily diff report comparing task states
6. **Consider making writeback guard escalation resettable**: tombstone is absolute, escalation ladder finite (2→1→silent) — a stale read followed by long work session permanently disables the check

## Gotchas / decisions / dead-ends
- Subsystem index cross-scope query gap: most useful queries require enumerating scopes first
- Check-clickup-addressed transcript mode is effectively dead code (off, produced all false verdicts)
- Clawgate writeback guard's asymmetric subagent rule: dispatching session owes writeback even when subagents do the work
- Object-leak structural fingerprint attempt discarded (31/74 false positive against human control)
- The five subsystems form a coherent agent-ops stack but the approval seam (clawgate LAN) is the widest attack surface

## How to verify
- Subsystem index probe: `python3 ~/workspace/devrc/scripts/lib/subsystem_touch.py --repo ~/workspace/devrc`
- Session manager: `python3 ~/workspace/devrc/scripts/session-manager --json`
- Clawgate health: `clawgatectl health`
- Object-leak: read `claude/RULES-ARCHIVE.md` lines 1416–1505
