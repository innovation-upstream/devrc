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
- Branch: `main` (devrc). This session wrote no product code — read-only ClickHouse/transcript queries plus two doc changes.
- Clawgate: `clawgate_handoff.sh resolve` ⇒ **rc=5, 0 tasks** (positive control passed). No `clawgate-task:` field written. The doc's existing `clawgate-task: none` is UNREADABLE (`field` ⇒ rc=2) and makes every `/resume` emit a GAP line.
- What's DONE:
  - **Refuted the prior measurement** — the session predicate was vacuous, so every figure was population-wide (detail below).
  - Re-derived the real token/cost decomposition (30 d, 1,027 sessions) and the real lever.
  - **Corrected my OWN first figure**: the authoritative signal-usage count is **10**, not 6 — see the correction block below.
  - Landed `5faf248d` (this doc) on `main`; opened **devrc#1271** (`b96737c7` + `c8879ade`) correcting `claude/skills/activity/SKILL.md`.
- What's IN FLIGHT: **devrc#1271** — two Tekton gates (`tekton/devrc-nodetests`, `tekton/devrc-pytests`) were still `pending` when this was written. **NOT verified green.**
- Deploy/verify status: docs only. Local `test_doc_path_rot.py` 77 passed; the two remote gates are unsettled.

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

### CORRECTION to the block above — the "6 sessions, all laptop" figure was itself wrong
- **Symptom:** the CLOSED block above records the real signal population as **6 sessions, all 6 on laptop**, from ClickHouse `skills_used != ''`. That is the count in a **derived** surface, reported as if it were usage.
- **Observed (with values):** `find-session --skill signal` (transcript-derived, every reachable host; stderr carried only the opencode caveat and named **no** unreachable peer) returns **10** sessions — **9 laptop / 1 workbench**. ClickHouse `skills_used` returns **6**, a strict SUBSET. The 4 missing: `50e9157d…` (workbench), `6fb90d0d…`, `9f8092ed…`, `ef3bf4ba…`.
- **Ruled out:** "the 4 missing sessions were never ingested" — each HAS `session-summary` rows in ClickHouse (9, 13, 13 and 19 respectively); their `skills_used` map is simply **empty**. All 4 started 2026-08-19 → 2026-08-25. via: measurement
- **Ruled out:** "`skills_used` is reliable from its 2026-08-04 first appearance" — that date is when the field first appears, NOT when it became reliable; coverage is partial at least three weeks later. via: measurement
- **Leading hypothesis:** `skills_used` undercounts signal by **40%**. Treat transcripts (`find-session --skill`) as the defining surface and `skills_used` as derived. The token/cost findings in this doc are UNAFFECTED — they aggregate over all sessions and never filter on `skills_used`.
- **Next probe:** measure the undercount on a second skill to see whether 40% is signal-specific or general: compare `find-session --skill <other>` against the same `!= ''` test.

## Next steps (ranked)
1. Get **devrc#1271** to a terminal gate state and merge it. Repo: `devrc`. IN FLIGHT: `devrc#1271`.
   forcing: gate — two Tekton commit statuses are attached to the PR and unsettled; the change cannot land until they report.
2. Quantify the `skills_used` undercount beyond one skill, then decide whether the field needs a backfill or only a documented caveat (the caveat is already in #1271). Repo: `devrc`. Touches `scripts/collector/`.
   forcing: none
3. Sample ~5 of the heaviest sessions' transcripts and classify text-only assistant turns as removable vs necessary, to size the real saving before changing guidance. Repo: `devrc`. Touches `scripts/session-analysis/`.
   forcing: none
4. Repair this doc's `clawgate-task: none` front-matter field so `/resume` stops emitting a GAP line. Repo: `devrc`.
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

- 🔴 **The vacuous predicate exists NOWHERE in devrc's committed tooling** — scanned `scripts/` + `claude/` for `JSONExtract*(...) IS [NOT] NULL`; **0 hits**, with a positive control proving the regex matches the two known-bad forms and ignores the safe `!= ''`. So the earlier "audit the codebase for this shape" item was **retired**: the defect was introduced ad-hoc in a session and only ever lived in this handoff. The durable fix is the skill correction (devrc#1271), not a code change.
- 🔴 **`find-session --skill` writes its report to STDOUT and its caveat to STDERR — do not split those streams with `cmd 2>&1 >/dev/null` in zsh.** MULTIOS copies stdout into the redirect too, so that idiom returns stdout while looking like it returns stderr, and a "no unreachable peer" conclusion drawn from it is unfounded. Redirect each to its OWN file and read both.
- 🔴 **A poll loop that emits only on TRANSITIONS is indistinguishable from a dead poller.** A 20-minute Monitor over two `pending` gates exited 0 having printed nothing, because its filter emitted only newly-settled checks — the docs' "silence is not success" trap. Emit a per-poll heartbeat so *still pending* and *dead* are different observations.
- ClickHouse aggregate aliasing: `SELECT any(host) AS host … GROUP BY host` is `ILLEGAL_AGGREGATION`. Alias to a different name (`AS h`) or nest the aggregate in a subquery.

## How to verify
```bash
# 1. The refutation — four-arm control. Expect 1946 / 1946 / 0 / 6.
#   ... AND JSONExtractString(payload,'skills_used','signal') IS NOT NULL   -> 1946
#   ... (no predicate)                                                      -> 1946
#   ... AND JSONExtractString(payload,'skills_used','signal') IS NULL       -> 0
#   ... AND JSONExtractString(payload,'skills_used','signal') != ''         -> 6

# 2. The undercount — the authoritative surface disagrees with the derived one.
#    Expect 10 sessions (9 laptop / 1 workbench) against the 6 above.
python3 ~/workspace/devrc/scripts/find-session.py --skill signal >/tmp/fs.out 2>/tmp/fs.err
grep -c 'claude --resume' /tmp/fs.out      # 10
cat /tmp/fs.err                            # must name NO unreachable peer

# 3. The PR's gates (never trust an empty rollup on a fresh PR — require >=2 terminal states)
gh pr checks 1271 --repo innovation-upstream/devrc
```
