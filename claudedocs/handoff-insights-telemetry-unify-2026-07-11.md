# Insights ↔ telemetry unification — 2026-07-11

Unify the Claude Code "insights" system with the personal activity-telemetry
pipeline, so session insight lives durably in the authed homelab ClickHouse
`activity.events` (versioned, cross-host, queryable) instead of the built-in
`/insights` ephemeral, per-host, non-versioned `~/.claude/usage-data/` cache whose
LLM layer even CONFABULATED friction (invented a false "500 output-token maximum"
story). This is a **two-PR initiative**; PR-1 is done.

## The 3-layer architecture (`activity.events`, `source=claude`)
- **Message stream** — `kind=prompt|command`. One event per genuine user turn /
  slash-command. Emitter: `scripts/collector/claude/tailer.py`. (Pre-existed.)
- **Layer A — deterministic session rollups** — `kind=session-summary`. One event
  per session; `payload` = whole-transcript rollup (tool counts, tokens, langs,
  git commits/pushes, churn, durations, interruptions, tool errors + categories,
  task/mcp/web flags, models, first_prompt, start/end ts). Emitter:
  `scripts/collector/claude/session-tailer.py`. **NO LLM.** ← shipped in PR-1.
- **Layer B — qualitative facets** — `kind=session-insight`. goal/outcome/friction
  + automation_opportunity/recurring_toil/workflow_gap. ← PR-2 (not built).

## What PR-1 shipped (branch `feat/insights-telemetry-unify-pr1`)
- **`scripts/collector/claude/session-tailer.py`** — the Layer A emitter (sibling
  of tailer.py). Parses each transcript fully, emits one `session-summary`.
- **`scripts/collector/claude/_shared.py`** — shared ts/project/emit/root/
  iter-transcript helpers; tailer.py refactored to import them (behaviour
  identical — its 18 tests still pass).
- **home-manager wiring** — `claude-activity-source` oneshot now runs BOTH tailers
  (two `ExecStart` lines) on the same 5-min timer, both hosts. `nix/home.nix`.
- **`scripts/session-analysis/insights.py`** — telemetry-native report
  (`--days 14`/`--json`/`--host`/`--html`). Reads Layer A rollups (argMax-latest)
  + the message stream; degrades gracefully when telemetry is off. Successor to
  the built-in `/insights`. Honest: shows `unreadable` sessions, never fabricates;
  OUTCOMES section renders Layer B if present else "qualitative layer pending (PR-2)".
- **validation** — two invariants added (`invariants.py`): `session_summary_wellformed`
  (payload has required keys) + `session_summary_no_orphans` (settled Layer-A-era
  prompt sessions all have a summary; vacuous pre-deploy).
- **tests** — `tests/test_session_tailer.py` (17), `tests/test_insights.py` (13),
  invariant tests (+4). Full repo suite: **323 passed**.
- **docs** — collector/validation READMEs + CLAUDE.md Layout updated.

## Read contract (IMPORTANT)
`activity.events` is append-only and a session grows until it ends, so its summary
CHANGES and re-emits (only when its transcript signature — mtime-ns + size —
changes). A session therefore accumulates several `session-summary` rows over its
life. **Consumers take the latest per session with `argMax(<field>, ingested_at)`
grouped by `session`.** State file (per-transcript signature):
`~/.local/state/activity/session-summary-state.json` (env: `CLAUDE_SUMMARY_STATE`).

## Verified / NOT verified
- Emitter unit-tested + **dry-run verified end-to-end**: `session-tailer.py` →
  real `emit` → `collector.parse_line` round-trips a well-formed `session-summary`
  (all payload keys correct).
- `insights.py` **run against LIVE ClickHouse** (reader creds): message-stream
  sections populate from real data (4816 prompts / 143 commands / 14d); Layer A
  sections empty (0 `session-summary` rows — none exist until deploy). Query fixed
  during verification: an `argMax(ts,…) AS ts` alias shadowed the `ts` column in
  WHERE (ILLEGAL_AGGREGATION) → renamed to `session_ts`.
- **NOT deployed / not switched.** No live `session-summary` rows exist until
  `ship.sh` converges both hosts. Do NOT `home-manager switch` from the agent.

## PR-2 plan (next)
- **Owned LLM extractor** via headless `claude -p` (no built-in `/insights` LLM,
  no confabulation): read a transcript, emit `kind=session-insight` with an
  ENRICHED schema — goal, outcome, friction PLUS `automation_opportunity`,
  `recurring_toil`, `workflow_gap`. **MANUAL trigger** (the only $/LLM piece),
  like `mail-actions/extract.py run` — NOT on the 5-min timer.
- Fold outcomes into `insights.py` OUTCOMES (already reads `session-insight` if
  present) + document in the `activity` skill.
- Consider back-emitting Layer A over the existing transcript history once on
  first deploy (the tailer already handles all sessions; a first timer fire will
  summarize every transcript on disk).

## Deploy (when merged)
`~/workspace/devrc/scripts/ship.sh` converges both hosts; the 5-min timer then
starts emitting `session-summary`. Sanity: `insights.py --days 14` should start
showing non-empty TOOLS/LANGUAGES/tokens within ~5 min.
