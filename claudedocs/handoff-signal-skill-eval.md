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
- Branch: `main` (devrc), clean w.r.t. this work — this session wrote no code, only read-only ClickHouse queries.
- Clawgate: `clawgate_handoff.sh resolve` returned **rc=5, 0 tasks** for this session (positive control passed: the same endpoint answered 3 links for another session, so the board was reachable). Per protocol NO `clawgate-task:` field was written. The doc's existing `clawgate-task: none` is an **unreadable** field (`field` subcommand ⇒ rc=2) and makes every `/resume` emit a GAP line — see Gotchas.
- What's DONE this session:
  - **Refuted the entire prior measurement.** The previous session's session-selection predicate was vacuous; all of its reported figures were computed over the whole session population, not over signal-skill sessions.
  - Re-derived the real token/cost decomposition over 30 days / 1,027 sessions from `activity.events`.
  - Identified the actual cost driver and the real reduction lever (below).
- What's IN FLIGHT: nothing.
- Deploy/verify status: N/A — analysis only, no code changes.

## Open investigations — live diagnosis state
### Signal skill token consumption patterns
- **Symptom:** Signal skill sessions show extremely high output/input ratio (40,000%+) suggesting over-generation
- **Observed:** Average session duration 1,213 minutes (20.2 hours), millions of tokens per session
- **Ruled out:** Not a cache inefficiency (96.5% hit rate is excellent) — via: measurement
- **Leading hypothesis:** Long-running coordination sessions with multiple skills generate excessive output
- **Next probe:** Analyze specific session transcripts to identify output reduction opportunities

### CLOSED — "signal skill over-generates output" was an instrument artifact, not a finding
- **Symptom + exact repro:** the prior doc reported 1,024 signal-skill sessions, 96.5% cache hit rate, a 40,000%+ output/input ratio and a 1,213-minute mean duration, and concluded the signal skill over-generates. Reproduce the defect with the doc's own recorded predicate:
  `JSONExtractString(payload,'skills_used','signal') IS NOT NULL`
- **Observed (with values):** four-arm control over `source='claude' AND kind='session-summary'`:
  - with the predicate ⇒ **1,946** distinct sessions
  - predicate REMOVED ⇒ **1,946** (identical — the filter selects nothing)
  - `IS NULL` negative control ⇒ **0** (the predicate can never be false)
  - real test `!= ''` ⇒ **6** sessions, all 6 on `host=laptop`, spanning 2026-08-28 → 2026-09-02
  ClickHouse `JSONExtractString` returns `''` on a missing key, never `NULL`, so `IS NOT NULL` is a tautology.
- **Ruled out:** "the signal skill has a token-efficiency problem" — the population it was measured on was never signal sessions, and the real population (6) is far too small to rank a skill on. via: measurement
- **Ruled out:** "output volume is a meaningful cost lever" — over 30 days output is **445,946,626 tokens = 0.293%** of all tokens (cache read 147.22 B = 96.9%, cache creation 4.73 B = 3.1%, uncached input 0.93 M). Eliminating 100% of output would cut ~0.3% of tokens; by Opus list rates it is ~9.7% of cost against ~90% for cache read+creation. via: measurement
- **Ruled out:** "the 40,000% output/input ratio indicates over-generation" — it reproduces exactly (**47,838%**) and is an artifact of the denominator: `input_tokens` counts only UNCACHED input, 932,199 tokens, i.e. 0.0006% of real input. The ratio divides output by a rounding error. via: measurement
- **Ruled out:** "long-running sessions are the right selector for high consumption" — `corr(duration_minutes, cache_read)` = **0.296**. `duration_minutes` is wall-clock span, so a resumed session reads as ~20 h while idle; the median 888 min is an artifact, not work. via: measurement
- **Ruled out:** "the very large per-repo CLAUDE.md is the differentiator" — context/turn is nearly uniform across repos (241K–323K). The repo with the 113 KB CLAUDE.md sits at **301K/turn**, BELOW devrc's 319K and homelab-talos's 323K. The ~42K-token fixed preamble is ~15% of a turn; the rest is accumulated tool output. via: measurement

### OPEN — the real lever: text-only assistant turns each cost a full context re-read
- **Observed (with values):** 30 days, 1,027 sessions, deduped `argMax(field, ingested_at)` per session:
  - `corr(assistant_message_count, cache_read)` = **0.967** — turns is THE driver
  - `corr(bash_calls, cache_read)` = **0.935**; Bash is **151,372 of 197,568** tool calls (**76.6%**), mean 167/session
  - mean assistant turns/session **563.3**; mean tool calls/session **259.1** ⇒ **0.46 tool calls per assistant turn**
  - mean assistant turns per USER message = **19.8**
  - cache-read cost per assistant turn ≈ **301 K tokens**; median context/turn 280 K, p95 461 K; heavy sessions 422–528 K
- **Leading hypothesis:** ≥54% of assistant turns carry no tool call at all (a FLOOR — parallel tool calls pack several into one message, so tool-carrying turns are fewer and text-only turns more). Each such turn triggers a full ~301K context re-read to emit ~1K of text: a ~300:1 ratio. Conservatively over half of all cache-read volume is spent on turns that invoke no tool. The lever is turn COUNT, not output verbosity.
- **Next probe:** classify text-only turns into removable (narration/preamble/status) vs necessary (final answer, question to user) on a sample of the heaviest sessions:
  `python3 ~/workspace/devrc/scripts/session-analysis/insights.py --days 14 --json`
  and cross-check against raw transcripts under `~/.claude/projects/*/`.

## Next steps (ranked)
1. Sample ~5 of the heaviest sessions' raw transcripts and classify text-only assistant turns as removable vs necessary, to size the real saving before changing any guidance. Repo: `devrc`. Touches `scripts/session-analysis/`.
   forcing: none
2. Fix the vacuous-predicate class at its source: `skills_used` is a JSON map and every consumer must test `!= ''`, never `IS NOT NULL`. Audit `scripts/session-analysis/` + `scripts/collector/` for the same shape and add a regression test. Repo: `devrc`.
   forcing: regression — a shipped analysis in this repo's own claudedocs reported figures that were off by the entire population; the same predicate shape will silently do it again.
3. Repair this doc's `clawgate-task: none` front-matter field so `/resume` stops emitting a GAP line on every read. Repo: `devrc`.
   forcing: none

## Gotchas / decisions / dead-ends
- Clawgate task resolution returned NO SESSION ID (expected for opencode sessions)
- ClickHouse queries require SOPS age key for credential decryption
- Signal skill is primarily used on laptop (88% of sessions)

- 🔴 **`JSONExtractString(payload,'k','sub') IS NOT NULL` is ALWAYS TRUE in ClickHouse** — it returns `''` on a missing key. Any session-selection predicate built this way silently selects the whole table and every derived statistic becomes a population statistic. The negative control that catches it in one query: run the same aggregate with the predicate REMOVED and with `IS NULL`; if arm 1 equals arm 2 and arm 3 is 0, the filter is inert. Test membership with `!= ''`.
- The `activity` skill already documents the correct entry point — **`find-session --skill NAME`**, not hand-written SQL — and records the same measured 6 signal uses. Reading the skill first would have prevented this entirely.
- `skills_used` attribution is **forward-only, first rows 2026-08-04**, and only **227 of 1,027** sessions in the 30-day window carry a non-empty map. No skill-level attribution is possible before that date; report "no recorded use since <date>", never "never used".
- `duration_minutes` is wall-clock transcript span, NOT active time — a `claude --resume` session spans days of idle. Never use it to rank sessions by work done (`corr` with cache_read is 0.296).
- `session-summary` rows are append-only — always dedupe with `argMax(<field>, ingested_at)` grouped by `session`, or every aggregate double-counts.
- Cost shares assume the measured model mix (`claude-opus-5` in 802 of the 30-day sessions, haiku-4-5 in 100, `<synthetic>` in 347).
- SOPS flag ordering matters: `sops -d --extract '…' --input-type yaml <(git show …)` — putting `--input-type yaml` AFTER the process substitution yields an empty password and a confusing `AUTHENTICATION_FAILED` from ClickHouse rather than a decrypt error.
- devrc is a **PUBLIC** repo — client repo names are deliberately kept out of this doc.

## How to verify
```bash
# 1. Reproduce the refutation (four-arm control). Expect: 1946 / 1946 / 0 / 6.
SP=~/workspace/devrc/scripts   # reader creds via SOPS, see the activity skill
# arm A: handoff predicate | arm B: no predicate | arm C: IS NULL | arm D: real test
#   ... AND JSONExtractString(payload,'skills_used','signal') IS NOT NULL   -> 1946
#   ... (no predicate)                                                      -> 1946
#   ... AND JSONExtractString(payload,'skills_used','signal') IS NULL       -> 0
#   ... AND JSONExtractString(payload,'skills_used','signal') != ''         -> 6

# 2. The authoritative way to ask "was skill X used?" — never hand-written SQL:
find-session --skill signal
```
