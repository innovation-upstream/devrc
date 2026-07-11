# session-analysis

On-demand reports + extractors over the personal activity telemetry
(`activity.events` in the homelab ClickHouse). All reader-only unless noted;
credentials come from the env (`CLICKHOUSE_URL/USER/PASSWORD`, reader creds via
SOPS — see the `activity` skill). Everything here is stdlib-only and degrades
gracefully when telemetry is unconfigured/unreachable.

| Tool | What it does |
|---|---|
| `activity-scan.py` | "where workflow time goes + what to automate" (repeated commands, bottlenecks, attention). |
| `initiative-scan.py` | cross-repo initiative + progress ledger (handoff docs + git + telemetry). `/initiatives`. |
| `insights.py` | telemetry-native Claude Code insights report (successor to the built-in `/insights`). |
| `session_insight/` | **Layer B** qualitative-facet extractor (this dir). |

## Claude-session telemetry layers (`source=claude`)

| Layer | `kind` | Producer | Report reader |
|---|---|---|---|
| Message stream | `prompt` / `command` | `collector/claude/tailer.py` | `insights.py` |
| **A** deterministic rollups | `session-summary` | `collector/claude/session-tailer.py` | `insights.py` |
| **B** LLM qualitative facets | `session-insight` | `session_insight/` (this dir) | `insights.py` |

Append-only table → every reader dedupes with `argMax(<field>, ingested_at)` per
`session`.

## Layer B — `session_insight/` (LLM qualitative facets)

Turns SETTLED Claude sessions into `session-insight` rows: underlying goal,
outcome, Claude-helpfulness (1–5), friction, and — the reason this exists —
**automation opportunities / recurring toil / workflow gaps**.

**Division of labour is fixed:** deterministic Python does ALL the plumbing; the
**live Claude session** (operating the `activity` skill) does the extraction step.
There is NO `claude -p`, NO external API, NO API key — Python never calls an LLM.

Three phases:

1. **`prepare`** (Python) — select settled + un-extracted sessions → secret-scrub
   the transcript → chunk it (map-reduce budget, never splitting a message) →
   attach the Layer A rollup as `ground_truth` → write
   `staging/<run-id>/<session>.input.json` (+ `manifest.json`) under
   `~/.local/state/activity/insights/` (mode 0700).
2. **Extraction** (the live session) — read each `input.json`, write
   `results/<run-id>/<session>.result.json` conforming to `schema.py`. Inline for
   ≤3 sessions; else fan out via the Agent tool (~5 sessions/subagent) then a
   mandatory `consolidate` check.
3. **`write`** (Python) — consolidate → validate each result against `schema.py`
   → `emit` as `source=claude kind=session-insight`, `ts` = the session's
   `end_ts`, `text` = `brief_summary`, `payload` = the JSON. Unreadable sessions
   ARE emitted (an honest "we looked and couldn't judge" record).

### Operator flow

```bash
SI=scripts/session-analysis/session_insight/cli.py
python3 $SI status --json                       # what's pending (no writes)
python3 $SI prepare --days 14 --limit 20 --json # select + scrub + chunk → staging
#   … the live session extracts each input.json → its result_path …
python3 $SI write --run-id <id> --clean         # consolidate → validate → emit
python3 scripts/session-analysis/insights.py --days 30   # read the report
```

Flags: `--settle-hours H` (default 6; 0 disables the idle gate), `--force`
(re-prepare/re-emit regardless of an existing row — append-only, argMax-newer
wins), `--chunk-chars C` (default 24000), `--redact-public-ips` (default OFF —
internal RFC1918/nebula/NodePort IPs are not sensitive), `--clean` (purge a run's
staging+results after a fully-clean emit).

### Anti-confabulation contract

The `ground_truth` block holds the DETERMINISTIC Layer A counts (tools, tokens,
commits, files, lines, errors, interruptions, models, durations). They are FACTS
the model must not restate-as-if-counted or contradict, and it must invent NO
count/limit of its own (there is no "output-token maximum" — that was the
built-in's confabulation). The model produces ONLY the qualitative facets; if a
transcript is too degraded to judge, it sets `unreadable=true` + a reason rather
than fabricating.

### Secret scrubbing (caveat)

`scrub.py` redacts the high-confidence secret SHAPES vendored from
`~/.claude/hooks/bash-guard.py` (AWS/GitHub/GitLab/Anthropic/OpenRouter/OpenAI/
Slack/Google keys + private-key blocks) unconditionally, and public IPs only when
`--redact-public-ips` is on. It does NOT catch a bare, prefix-less service token
(a generic alphanumeric string is indistinguishable from content) — so the model
is instructed to treat `<REDACTED:…>` as opaque, and `brief_summary` must never
echo transcript credentials. Staging holds the scrubbed transcript on disk under
a 0700 dir; `--clean` removes it after emit.

### Coexistence with the built-in `/insights`

The built-in cannot be disabled (harness-owned) and keeps its ephemeral
`~/.claude/usage-data/` cache; ignore it. **This pipeline is the canonical view** —
the built-in is no longer trusted (it confabulated friction). Do not reconcile
against its numbers.

Design record: `claudedocs/spec-insights-telemetry-pr2-2026-07-11.md`.
Tests: `python3 -m pytest scripts/session-analysis/session_insight/tests scripts/session-analysis/tests -q` (stdlib-only; the LLM step is mocked with fixture result.json files).
