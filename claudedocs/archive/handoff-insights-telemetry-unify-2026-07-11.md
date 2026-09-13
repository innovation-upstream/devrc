# Insights ↔ telemetry unification — SHIPPED (status as of 2026-07-28)

Unify the Claude Code "insights" system with the personal activity-telemetry
pipeline, so session insight lives durably in the authed homelab ClickHouse
`activity.events` (versioned, cross-host, queryable) instead of the built-in
`/insights` ephemeral, per-host, non-versioned `~/.claude/usage-data/` cache whose
LLM layer even CONFABULATED friction (invented a false "500 output-token maximum"
story — verified against transcripts: the sessions it blamed were full 1,300–2,750
line transcripts with zero token-limit markers; the analyzer hit ITS OWN limit and
invented the reason).

**STATUS: COMPLETE + LIVE.** Both PRs merged, shipped to both hosts, verified live.
This doc is now the durable record + the "what's left" list for a future session.

## The 3-layer architecture (`activity.events`, `source=claude`)
- **Message stream** — `kind=prompt|command`. One event per genuine user turn /
  slash-command. Emitter: `scripts/collector/claude/tailer.py`. (Pre-existed.)
- **Layer A — deterministic session rollups** — `kind=session-summary`. One event
  per session; `payload` = whole-transcript rollup (tool counts, input/output +
  cache-read/cache-creation tokens, langs, git commits/pushes, churn, durations,
  interruptions, tool errors + categories, task/mcp/web flags, models, start/end
  ts). Emitter: `scripts/collector/claude/session-tailer.py`. **NO LLM.** LIVE on
  the 5-min `claude-activity-source` timer, both hosts.
- **Layer B — LLM qualitative facets** — `kind=session-insight`. underlying_goal /
  outcome / session_type / claude_helpfulness / friction + the purpose-aligned
  `automation_opportunity` / `recurring_toil` / `workflow_gap`. Package
  `scripts/session-analysis/session_insight/`. **Session-driven** (the live Claude
  session running the `activity` skill does the extraction — NO `claude -p`, no
  external API), **manual/on-demand only**. Anti-confabulation contract: Layer A
  counts are injected as ground-truth FACTS the model may not restate/invent;
  `unreadable` flagged honestly.

Report: `scripts/session-analysis/insights.py [--days 14] [--insight-days 30]
[--json] [--host H] [--html PATH]` fuses A + B + the message stream. It is the
telemetry-native **successor to the built-in `/insights`** — the built-in can't be
overridden but is no longer the source of truth (it confabulates). Don't reconcile
against its numbers.

## What shipped
- **PR #93** (Layer A rollups + `insights.py` + `_shared.py` refactor of tailer.py +
  2 validation invariants + home-manager wiring). Merged, shipped. Live-verified:
  **373 `session-summary` rows** landed on first backfill; report TOOLS/LANGUAGES/
  tokens sections populate. The honest-tokens fix mattered — input reads ~20.2B
  (12.8M fresh + 19.7B cache-read + 560M cache-write); the old `input_tokens`-only
  metric was ~1,580× low.
- **PR #96** (Layer B session-insight extractor + report OUTCOMES + leverage-ranked
  automation/toil/gap sections). Merged, shipped. `select.py`→`selection.py` (stdlib
  shadow), payload bounded < PIPE_BUF, staging 0700/0600 + per-session purge on emit,
  vendored-secret-pattern drift test. Live-verified: extract→write→report round-trip.
- **Separate this session:** `~/.claude/hooks/bash-guard.py` gained a secret/IP
  **publish-sink** scanner (private-key block unconditional; API/token patterns +
  public IPs blocked only in `git commit`/`gh pr|issue|release|gist` sinks; internal
  IPs + `$VAR` creds untouched). Per-host hook, 17/17 tests. Noted in the
  `harness-audit-tooling` memory.

## Read contract (IMPORTANT)
`activity.events` is append-only; a session grows until it ends, so its summary/
insight re-emits (Layer A: when the transcript signature mtime-ns+size changes).
**Consumers take the latest per session with `argMax(<field>, ingested_at)` grouped
by `session`.** State files: `~/.local/state/activity/session-summary-state.json`
(Layer A). Layer B staging/results under `~/.local/state/activity/insights/`
(0700; scrubbed inputs purged per-session on successful `write`).

## First extraction run — what the data produced (8 sessions, 2026-07-11)
Layer B over 8 sessions (mostly B2/civitai incidents) surfaced, leverage-ranked:
- **Dominant: config-as-code gap for the storage/CDN layer** (config_gap ×5) — CORS
  rules, Cloudflare cache/firewall rules, ingestion egress-IP allowlisting, the OTLP
  `service.instance.id` gap — all managed OUT-OF-BAND, not in git, recurring across
  incidents. **This is the highest-leverage follow-up the data points at.**
- **B2-incident tooling = repeated toil** — ~18 throwaway probe scripts, hand-launched
  k8s diagnostic Jobs, re-derived Class-B breakdowns across ≥3 incident sessions →
  a reusable CF+Prometheus incident toolkit + a b2-throttle runbook. (NOTE: a later
  session shipped the `obs-read` skill, which addresses the "port-forward → PromQL →
  python parse → teardown" part of this.)
- deploy-then-verify soak loop + pod-triage sequence — both hand-assembled per
  incident, both scriptable.

## Operating it / how to extract more (next session)
Via the `activity` skill (its Layer B section has the full flow + a new "Operating
the backlog" subsection with these lessons). Quick version:
```bash
# reader creds — NOTE the --input-type yaml gotcha on the process-substitution:
export CLICKHOUSE_URL=http://192.168.50.94:30123 CLICKHOUSE_USER=activity_reader
export CLICKHOUSE_PASSWORD=$(SOPS_AGE_KEY_FILE=~/workspace/homelab-talos/.secrets/age.key \
  sops -d --input-type yaml --extract '["stringData"]["reader-password"]' \
  <(git -C ~/workspace/homelab-talos show origin/trunk:clusters/homelab/apps/activity/secrets.enc.yaml))
python3 scripts/session-analysis/session_insight/cli.py status --json     # ~90 pending
python3 scripts/session-analysis/session_insight/cli.py prepare --days 30 --limit 6 --json
# extract: monster sessions (250–280 chunks) → ONE subagent each, SAMPLE (first/last/spread),
#          never read all chunks (counts come from ground_truth); small ones batch several/subagent
python3 scripts/session-analysis/session_insight/cli.py write --run-id <id> --json
python3 scripts/session-analysis/insights.py --days 30
```

## What's left (next session, priority order)
1. **Backlog extraction** — ~90 sessions still lack Layer B. Extract in bounded
   batches (`--limit ~6`, real cost = the operating session's tokens). Each batch
   makes the OUTCOMES + automation/toil/gap report more meaningful.
2. **Act on the dominant finding** — config-as-code for the storage/CDN layer
   (Cloudflare/B2 rules → git/IaC). This is the concrete, evidence-backed leverage
   the whole exercise produced; could become its own scoped effort.
3. **Deferred Layer A hardening — re-emit storm:** a long-lived session re-emits its
   FULL summary every 5-min tick (signature changes each turn) → many near-duplicate
   rows. `argMax`-latest keeps reads correct but it's wasteful. Address via
   **emit-on-settle** (emit once idle N min) and/or a **ClickHouse TTL** on
   `session-summary` rows so superseded rollups age out.
4. **Scrubber scope:** Layer B's scrubber catches only PREFIXED secret shapes; a bare
   token (no recognizable prefix) survives into on-disk staging (0600, purged after
   emit; reaches ClickHouse only if the model quotes it into a summary). Widen the
   patterns if this matters. (Same regexes as the bash-guard hook — keep in sync; a
   drift test exists.)
5. **`first_prompt` reintroduction** — dropped from Layer A for the leak surface; only
   reintroduce once a robust free-text scrubber exists.

## Where things live
- Layer A: `scripts/collector/claude/session-tailer.py` + `_shared.py`; wiring `nix/home.nix`.
- Layer B: `scripts/session-analysis/session_insight/` (schema/scrub/selection/prepare/
  consolidate/write/cli + tests); spec `claudedocs/spec-insights-telemetry-pr2-2026-07-11.md`.
- Report: `scripts/session-analysis/insights.py`. Validation: `scripts/validation/invariants.py`.
- Ops: the `activity` skill (per-host `~/.claude/skills/activity/SKILL.md`). CLAUDE.md
  Layout bullet + `scripts/session-analysis/README.md` document the 3 layers.
