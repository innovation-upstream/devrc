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
  per session; `payload` = whole-transcript rollup (tool counts, input/output +
  cache-read/cache-creation tokens, langs, git commits/pushes, churn, durations,
  interruptions, tool errors + categories, task/mcp/web flags, models, start/end
  ts). Emitter: `scripts/collector/claude/session-tailer.py`. **NO LLM.** ← shipped
  in PR-1 (post-review hardening dropped `first_prompt`/`message_hours` — see below).
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

## PR-2 plan (next) — see full spec: claudedocs/spec-insights-telemetry-pr2-2026-07-11.md
- **Owned qualitative extractor driven by the LIVE Claude Code session** (NOT
  `claude -p`, NOT an external API — decision locked with Zach). Deterministic
  Python does the plumbing (select settled+un-extracted sessions → secret-scrub →
  attach Layer A rollup as GROUND TRUTH → write staging inputs; then validate +
  `emit` the results); the session running the `activity` skill performs the
  extraction step (inline, or Agent-tool fan-out for a backlog). Emits
  `kind=session-insight` with the ENRICHED schema — goal, outcome, friction PLUS
  `automation_opportunity`, `recurring_toil`, `workflow_gap`. **MANUAL** only
  (operated via the `activity` skill; no timer). Anti-confabulation contract:
  the model may NOT invent/restate counts (kills the built-in's "500-token max"
  failure) and must flag `unreadable` honestly.
- Fold outcomes into `insights.py` OUTCOMES (already reads `session-insight` if
  present) + document in the `activity` skill.
- Consider back-emitting Layer A over the existing transcript history once on
  first deploy (the tailer already handles all sessions; a first timer fire will
  summarize every transcript on disk).

## PR-2 / follow-up considerations
- **Post-review hardening (on the PR-1 branch, additive commits):** `--host` query
  alias-shadow fixed (`argMax(host,…) AS sess_host`, mirroring the earlier `ts`
  fix); first-run backfill hardened — `save_state()` now checkpoints incrementally
  (every 25 emits) so a SIGTERM mid-backfill resumes instead of re-storming, and
  the oneshot got `TimeoutStartSec=600`; `input_tokens` no longer masquerades as
  total input (cache-read + cache-creation now summed and rendered honestly);
  `first_prompt` (unscrubbed raw-prompt leak surface) and `message_hours`
  (unbounded, unread) DROPPED from the rollup; sidechain skip moved ahead of the
  duration min/max.
- **Re-emit storm for long-lived sessions (deferred):** a session re-emits its FULL
  summary on every 5-min tick while it keeps growing (signature changes on each new
  turn). For a long-lived session that is a lot of near-duplicate `session-summary`
  rows. `argMax`-latest read contract makes this correct but wasteful. Address later
  via **emit-on-settle** (only emit once a session has been idle N minutes) and/or a
  **ClickHouse TTL** on `session-summary` rows so superseded rollups age out.
- **`first_prompt` reintroduction:** deferred to PR-2, and only once the secret
  scrubber exists (PR-2 owns free-text scrubbing before any raw prompt hits CH).

## Deploy (when merged)
`~/workspace/devrc/scripts/ship.sh` converges both hosts; the 5-min timer then
starts emitting `session-summary`. Sanity: `insights.py --days 14` should start
showing non-empty TOOLS/LANGUAGES/tokens within ~5 min.
