# Spec — Insights/Telemetry Unification, PR-2: Layer B (LLM qualitative facets)

**Date:** 2026-07-11
**Status:** Implementation-ready specification (no code in this doc)
**Repo:** `/home/zach/workspace/devrc` (NixOS / home-manager dotfiles)
**Depends on:** PR-1 (Layer A deterministic `session-summary` rollups + `insights.py` report skeleton) — treated here as a landed contract.
**Deliverable of PR-2:** a `source=claude, kind=session-insight` event per settled session, produced by the live Claude Code session (no `claude -p`, no external API), written to homelab ClickHouse `activity.events` via the existing `emit` path, and surfaced by `insights.py`.

---

## 0. Context & motivation (why this exists)

Zach drives all his work through agents and mines his own behaviour from a durable, authed telemetry store (homelab ClickHouse `activity.events`). The built-in Claude Code `/insights` harness is being superseded because:

- Its state is **ephemeral** (`~/.claude/usage-data/`) and unqueryable alongside the rest of the pipeline.
- Its LLM layer **confabulated friction**: it invented a "500 output-token maximum" to rationalise its own failure to parse large transcripts (verified false). Owning the extraction lets us inject **ground truth** (the Layer A deterministic rollup) and enforce an **anti-confabulation contract** so the model reports qualitative facets only and is forbidden from inventing counts.

Three layers land in `activity.events` under `source=claude`:

| Layer | `kind` | Producer | Status |
|---|---|---|---|
| Message stream | `prompt` / `command` | `scripts/collector/claude/tailer.py` | EXISTS |
| **A** — deterministic session rollups | `session-summary` | `scripts/collector/claude/session-tailer.py` | PR-1 (in flight) |
| **B** — LLM qualitative facets | `session-insight` | **PR-2 (this spec)** | to build |

`activity.events` columns (the fixed contract): `ts DateTime64(3) UTC, host, source, kind, project, cwd, session, app, text, duration_ms, exit_code, payload JSON, ingested_at`.

### Layer A `session-summary` payload (GIVEN — PR-2 consumes this as ground truth, does not modify it)

Keys: `tool_counts, input_tokens, output_tokens, user_message_count, assistant_message_count, duration_minutes, languages, git_commits, git_pushes, files_modified, lines_added, lines_removed, user_interruptions, tool_errors, tool_error_categories, uses_task_agent, uses_mcp, uses_web_search, uses_web_fetch, models, first_prompt, start_ts, end_ts, message_hours, unreadable`.

Re-emitted when a session grows; readers dedupe via `argMax(payload, ingested_at)` per `session`.

---

## 1. Architecture & data flow

The division of labour is fixed by decision: **deterministic Python does all plumbing; the live Claude session does the extraction step.**

```
                          ┌──────────────────────────────────────────────────────────┐
                          │  DETERMINISTIC PYTHON  (scripts/session-analysis/           │
                          │                         session_insight/)                   │
                          └──────────────────────────────────────────────────────────┘
                                              │
  activity.events (CH) ── select.py ─────────┤  select settled + un-extracted sessions
  (session-summary rows,                     │    · has a session-summary row (ground truth exists)
   session-insight rows,                     │    · settled: no new activity for N hours
   message stream)                           │    · no session-insight row (unless --force)
                                             │
  ~/.claude/projects/**.jsonl ── prepare.py ─┤  per session:
                                             │    · read transcript (reuse _shared.iter_transcripts)
                                             │    · scrub.py → redact secrets BEFORE anything sees it
                                             │    · chunk scrubbed transcript (map-reduce budget)
                                             │    · attach Layer A rollup as GROUND TRUTH
                                             │    · write  staging/<run-id>/<session>.input.json
                                             │    · write  staging/<run-id>/manifest.json
                                             ▼
                          ┌──────────────────────────────────────────────────────────┐
                          │  THE LIVE CLAUDE SESSION  (operating the `activity` skill) │
                          │  reads inputs → extracts qualitative facets → writes        │
                          │  results/<run-id>/<session>.result.json                     │
                          │  (inline for 1 session; Agent-tool fan-out for a backlog,   │
                          │   then a MANDATORY consolidation check)                     │
                          └──────────────────────────────────────────────────────────┘
                                             │
                          ┌──────────────────┴───────────────────────────────────────┐
                          │  DETERMINISTIC PYTHON                                       │
                          └──────────────────────────────────────────────────────────┘
                                             │
                             write.py ───────┤  read every result.json
                                             │    · validate against schema.py
                                             │    · emit via `emit` as kind=session-insight
                                             ▼
  activity.events (CH) ◄──────────────── collector daemon ships spool → ClickHouse
                                             │
  insights.py report ◄──────────────────────┘  OUTCOMES section + leverage-ranked
                                                automation / toil / gap section
                                                (reads session-insight via argMax)
```

**Why the session (not `claude -p`)?** Decision (fixed): Zach always runs this from inside an interactive Claude Code session via the `activity` skill. There is no subprocess, no OpenRouter/sglang, no new API key. The model doing the work IS the session (or its subagents). Python never calls an LLM; it only prepares inputs and consumes structured outputs.

---

## 2. Component breakdown & file layout

New package (underscore name so it is importable and `-m`-runnable; mirrors the sibling-import idiom of `scripts/mail-actions/` and `scripts/collector/claude/`):

```
scripts/session-analysis/session_insight/
  __init__.py
  cli.py          entrypoint: subcommands `status` | `prepare` | `write`; flags below
  select.py       settled + un-extracted session selection (queries CH + transcript mtime)
  prepare.py      build + write per-session input.json (scrub, chunk, attach ground truth)
  scrub.py        secret / private-key / (opt) public-IP redaction — patterns vendored from bash-guard
  schema.py       session-insight payload schema, controlled vocabularies, validation, constants
                  (SHARED by prepare.py, write.py, and insights.py — single source of truth)
  write.py        consume result.json files → validate → emit via `emit`
  consolidate.py  merge/verify result files from a subagent fan-out (union by session, conflict/missing checks)
  tests/
    __init__.py
    test_select.py        settled/un-extracted logic over fixture rows + mtimes
    test_scrub.py         redaction of each secret pattern; internal IP left intact
    test_prepare.py       input.json shape, ground-truth embedding, chunk boundaries
    test_schema.py        validation: enums, required fields, unreadable path, bad payloads rejected
    test_write.py         emit arg shape (subprocess mocked), idempotency skip, --force re-emit
    test_consolidate.py   union / duplicate-conflict / missing-session cases
    fixtures/             tiny transcript .jsonl, a session-summary payload, fake result.json files
```

Report code lives in the PR-1 file `scripts/session-analysis/insights.py`; PR-2 **extends** it (new sections + a `session-insight` reader). It imports `schema.py` for field names / vocab so the report stays a drop-in over the payload.

**Invocation** (from the skill):
- `python3 scripts/session-analysis/session_insight/cli.py status [--days N] [--settle-hours H] [--json]`
- `python3 scripts/session-analysis/session_insight/cli.py prepare [--days N] [--limit K] [--settle-hours H] [--force] [--json]`
- `python3 scripts/session-analysis/session_insight/cli.py write --run-id <id> [--json]`

CH creds come from the same env as the rest of the pipeline (`CLICKHOUSE_URL/USER/PASSWORD`), reusing `scripts/validation/chquery.py` (`Q.CHConn.from_env()` / `Q.CHClient`) exactly like `activity-scan.py`.

---

## 3. `session-insight` payload schema

All qualitative fields are produced by the model; deterministic counts are NEVER produced by the model (they live in Layer A). `schema.py` owns this definition, the controlled vocabularies, and a `validate(payload) -> list[str]` returning error strings (empty = valid).

Top-level payload (JSON object stored in the `payload` column):

| Field | Type | Notes / enum |
|---|---|---|
| `schema_version` | int | `1` for PR-2. Bump on breaking change. |
| `session` | str | echoes the event `session` (transcript stem) for self-containment. |
| `underlying_goal` | str | one sentence: what the user was *actually* trying to accomplish (not the literal first prompt). |
| `goal_categories` | list[str] | controlled vocab (see below); 1–3 tags. |
| `outcome` | enum str | `fully_achieved` \| `mostly_achieved` \| `partially_achieved` \| `not_achieved` \| `unclear`. |
| `session_type` | enum str | controlled vocab (see below). |
| `claude_helpfulness` | int | 1–5 (5 = Claude materially drove the win; 1 = mostly got in the way). |
| `friction_counts` | dict[str,int] | friction-category → count; categories controlled (see below). Empty dict = no notable friction. |
| `friction_detail` | list[str] | ≤5 short concrete descriptions of the notable friction moments (e.g. "re-ran nix build 3× before reading the actual error"). |
| `primary_success` | str | the single most valuable thing accomplished; `""` if `not_achieved`. |
| `brief_summary` | str | 1–3 neutral sentences. Also copied to the event `text` column for glanceability. |
| `automation_opportunity` | object \| null | see below — a repeatable manual action Claude/Zach did by hand that a script/command/hook could do. |
| `recurring_toil` | object \| null | see below — low-value repetitive grind observed in this session. |
| `workflow_gap` | object \| null | see below — a missing tool/doc/automation/config/knowledge that caused avoidable work. |
| `unreadable` | bool | honesty flag — transcript could not be meaningfully read/extracted. |
| `unreadable_reason` | str | required non-empty iff `unreadable=true`; else `""`. |

### Enriched-field object shapes (the WHY of this dataset)

**`automation_opportunity`** (or `null` if none observed):
```jsonc
{
  "present": true,
  "description": "string — what could be automated",
  "trigger": "string — the manual/repeated action that signals it (e.g. 'hand-typed the 4-step nix switch + verify dance')",
  "leverage": "high | medium | low",   // expected time/toil saved × frequency
  "evidence": "string — concrete moment(s) in this session that show it"
}
```

**`recurring_toil`** (or `null`):
```jsonc
{
  "present": true,
  "description": "string — the repetitive low-value grind",
  "category": "string — controlled vocab: env-setup | deploy | debugging | context-gathering | boilerplate | manual-verification | data-wrangling | other",
  "frequency_hint": "string — how often this seems to recur (this session / seen-before / likely-weekly …)"
}
```

**`workflow_gap`** (or `null`):
```jsonc
{
  "present": true,
  "description": "string — what was missing",
  "kind": "missing_tool | missing_doc | missing_automation | config_gap | knowledge_gap"
}
```

When a facet is genuinely absent, the model emits `null` (not a `present:false` husk) — keeps the report aggregation simple (`if obj:`).

### Controlled vocabularies (defined in `schema.py`, extensible)

- `GOAL_CATEGORIES`: `infra, deploy, feature, bugfix, refactor, config, docs, research, ops, review, chore, data`.
- `SESSION_TYPES`: `feature_build, bugfix, deployment, investigation, refactor, config_change, research, review, chore, exploration`.
- `FRICTION_CATEGORIES`: `wrong_approach, repeated_correction, tool_error, permission_block, context_loss, hallucination, missing_info, env_breakage, slow_feedback`. (Aligned with Layer A `tool_error_categories` + interaction-level friction so the two layers can be cross-referenced.)

`validate()` rejects out-of-vocab enum values for `outcome`/`session_type`/`leverage`/`workflow_gap.kind`; tolerates (but records a soft-warning list for) out-of-vocab `goal_categories`/`friction_counts` keys/`recurring_toil.category` so the vocab can grow without hard failures.

### Mapping to the built-in `/insights` facets (drop-in for the report)

| Built-in facet | This schema |
|---|---|
| underlying goal | `underlying_goal` |
| goal categories | `goal_categories` |
| outcome | `outcome` (enum) |
| session type | `session_type` |
| Claude helpfulness | `claude_helpfulness` |
| friction | `friction_counts` + `friction_detail` |
| primary success | `primary_success` |
| summary | `brief_summary` |

The three enriched fields (`automation_opportunity`, `recurring_toil`, `workflow_gap`) are the net-new, purpose-aligned additions that feed the new report section.

---

## 4. "Settled session" definition & detection

A session is **settled** when it has had **no new activity for `N` hours** (default `N=6`, configurable via `--settle-hours` / `INSIGHT_SETTLE_HOURS`).

Detection is belt-and-suspenders, all deterministic:

1. **Ground truth exists** — the session has a `session-summary` row (Layer A). Without it there is no rollup to inject, so it is not yet a candidate.
2. **Last activity age** — `now() - last_activity_ts > N hours`, where `last_activity_ts = max(ts)` for that `session` across `source='claude'` rows (message stream + session-summary). Prefer the Layer A rollup's `end_ts` when present; fall back to `max(ts)`.
3. **Transcript mtime** — the transcript file's mtime is also older than `N` hours (guards against a rollup emitted mid-session; the message tailer runs on a 5-min timer so `ts` can lag the live file). `select.py` resolves the transcript path via `_shared.iter_transcripts` and `os.stat().st_mtime`.

A session is a **candidate** iff (1) ∧ (2) ∧ (3) ∧ not-already-extracted (§5). `--settle-hours 0` disables the settle gate (for testing a specific just-finished session).

---

## 5. Idempotency & `--force`

- **Already-extracted set** (query, argMax so we see the *current* state incl. a prior `unreadable`):
  ```sql
  SELECT session, argMax(simpleJSONExtractBool(toString(payload),'unreadable'), ingested_at) AS unreadable
  FROM activity.events
  WHERE source='claude' AND kind='session-insight'
  GROUP BY session
  ```
  A session in this set with `unreadable=false` is skipped by `prepare` unless `--force`. A session with `unreadable=true` is a **soft candidate**: re-attempted automatically on the next run if the transcript has since grown (mtime newer than the insight row's ts) — a previously-unreadable session may now have a clean rollup — otherwise skipped unless `--force`.
- **`--force`** re-prepares (and re-writes) candidates regardless of an existing row.
- **Re-extraction dedupe is append-only**: `write.py` emits a NEW `session-insight` row with a fresh `ingested_at` (stamped by the collector on ingest). Readers everywhere use `argMax(payload, ingested_at)` per `session`, so the newest extraction wins with **no deletes** and full history retained (180d TTL). This mirrors the Layer A dedupe contract exactly.

`prepare` and `write` both print a **skip log**: `session, reason` (`already-extracted`, `not-settled`, `no-rollup`, `over-limit`) so a run is auditable.

---

## 6. Secret scrubbing (applied in `prepare.py` BEFORE anything reads the transcript)

**Why:** transcripts contain pasted API keys and other secrets. Scrubbed text is what lands in the staging file on disk, what the session/subagents read, and what could otherwise be echoed back into a `brief_summary` and shipped to ClickHouse. Scrub at the source.

`scrub.py` **vendors the patterns from `~/.claude/hooks/bash-guard.py`** (that file is the source of truth; copy the regexes with a comment citing it, because the hook lives outside this repo and cannot be imported reliably). Patterns:

- **`SECRET_PATTERNS`** (unconditional redaction — unlike bash-guard we do NOT gate behind a publish sink, because the whole transcript is leaving to the model): AWS `AKIA…`/`ASIA…`, GitHub `gh[pousr]_…` + `github_pat_…`, GitLab `glpat-…`, Anthropic `sk-ant-…`, OpenRouter `sk-or-v1-…`, OpenAI `sk-proj-…`, Slack `xox[baprs]-…`, Google `AIza…`. Each match → `<REDACTED:aws-key>` etc. (label taken from the pattern's description).
- **Private-key block** (`-----BEGIN … PRIVATE KEY-----` … `-----END … PRIVATE KEY-----`) → `<REDACTED:private-key>`.
- **Public IPs** — reuse bash-guard's `ipaddress.is_global` detector, but **default OFF** (`--redact-public-ips` / `INSIGHT_REDACT_IPS=1` to enable). Rationale: Zach's infra work is full of internal RFC1918/nebula/NodePort IPs that are NOT sensitive and whose redaction would degrade summary usefulness; the staging file never leaves the authed host; and bash-guard itself only redacts public IPs at a *publish* sink. Available for the paranoid, off by default.

`scrub()` returns `(scrubbed_text, redaction_counts: dict[label,int])`. The counts go into the input.json (transparency: the model is told "N secrets were redacted here" so a `<REDACTED:…>` token is never mistaken for content).

Scrubbing is unit-tested against a synthetic transcript containing one of each pattern + an internal IP that must survive.

---

## 7. Ground-truth injection & the anti-confabulation contract

`prepare.py` embeds the Layer A rollup verbatim into each input.json under `ground_truth`, fetched with:
```sql
SELECT session, argMax(payload, ingested_at) AS payload
FROM activity.events
WHERE source='claude' AND kind='session-summary' AND session IN (<candidate sessions>)
GROUP BY session
```

The input.json instructs the session (and the SKILL.md reinforces it) with an explicit contract:

> **ANTI-CONFABULATION CONTRACT.** The `ground_truth` block holds DETERMINISTIC counts computed from the transcript (tool_counts, tokens, git_commits, files_modified, lines_added/removed, tool_errors, interruptions, models, durations). These are FACTS. You MUST NOT contradict or restate them as if you counted them, and you MUST NOT invent any count, limit, or metric of your own (there is no "output-token maximum" — that earlier story was a confabulation; do not reproduce that failure mode). Your job is ONLY the qualitative facets in the schema: goal, outcome, helpfulness, friction *descriptions*, successes, and the automation/toil/gap observations. If the (chunked) transcript is too degraded, truncated, or ambiguous to judge a facet honestly, set `unreadable=true` with a one-line `unreadable_reason` and leave the qualitative fields empty — **flag it honestly rather than fabricate.** `friction_counts` are your qualitative tallies of *interaction* friction (wrong approaches, repeated corrections); they are distinct from Layer A's mechanical `tool_errors`.

Because the counts are handed over as ground truth, the model never needs to read the whole transcript to *count* anything — it reads for meaning only. This is the structural fix for the built-in's failure.

---

## 8. Large-transcript handling (map-reduce / chunking)

The built-in choked on 2000+-line sessions and confabulated a limit. PR-2 chunks deterministically in `prepare.py`:

- Split the scrubbed transcript into ordered **chunks** bounded by a char budget (`--chunk-chars`, default ~24k chars ≈ ~6k tokens) that never split a single message; carry a small overlap of the last message for continuity.
- `input.json` carries `chunks: [ {idx, text}, … ]` plus `chunk_count`. Small sessions produce a single chunk.
- The **extraction is map-reduce** (instructed by the skill):
  - **map:** for each chunk, the model notes qualitative observations (goal signals, friction moments, successes, automation/toil/gap candidates). For a multi-chunk session this MAY be one subagent per chunk (see §9) or sequential note-taking.
  - **reduce:** the model consolidates the per-chunk notes + the `ground_truth` block into ONE `result.json` conforming to the schema. Counts come from `ground_truth`, not from re-reading.
- Because qualitative facets are additive and the counts are external, chunking cannot corrupt the deterministic numbers and cannot force a "give up / invent a limit" path — a chunk that is too degraded contributes nothing rather than failing the whole session.

---

## 9. Subagent fan-out vs inline; consolidation contract

**Single-session (or ≤ `--limit` small backlog): inline path.** The main session reads the one/few input.json files and writes result.json directly. No fan-out.

**Backlog fan-out (skill decision, when candidates > a small threshold, e.g. > 3):** the main session dispatches N subagents via the **Agent tool** (`general-purpose`), each handed a disjoint slice of session ids + their input.json paths, each writing `results/<run-id>/<session>.result.json`. Per RULES, if the agents could touch the repo they'd need `isolation: "worktree"` — but here they only READ staging inputs and WRITE result JSONs under `~/.local/state/…` (outside the repo), so worktree isolation is unnecessary; the note in SKILL.md must say so explicitly.

**Mandatory consolidation step** (`consolidate.py`, invoked by `write.py` before emitting, and echoed as a checklist in the skill):
- **Union by `session`** — exactly one result per expected candidate session.
- **Missing** — any candidate with no result.json → reported as an error; that session is NOT emitted (re-run picks it up).
- **Conflict** — two result files for the same session → keep NEITHER, flag; forces a clean re-run for that session.
- **Schema** — each result validated via `schema.validate`; a failing result is quarantined (moved to `results/<run-id>/rejected/`) and reported, not emitted.
- Output: `emitted_ok`, `missing`, `conflicts`, `rejected` lists.

This makes a partial or duplicated fan-out safe: only clean, unique, schema-valid results are written.

---

## 10. ClickHouse write path — `emit` (chosen) vs direct INSERT

**Decision: use `emit`** (`scripts/collector/emit`), NOT a direct authed INSERT. Rationale:
- Consistency with every other source; the collector daemon (always running on the workbench) batches, offline-buffers, retries, and stamps `host` from `ACTIVITY_HOST` — no writer creds in this script, no bespoke HTTP.
- The one downside (write is async — lands within a collector flush interval, not instantly) is irrelevant for a manual on-demand tool.
- A direct INSERT would require handling `activity_writer` creds in the script for zero benefit. Rejected.

`write.py` resolves the emit binary exactly like the tailers (`_shared.emit_path()`; `CLAUDE_SOURCE_EMIT` override for tests) and, per validated result, runs:

```
emit source=claude kind=session-insight \
     b64:session=<session> \
     b64:project=<project> \
     b64:cwd=<cwd> \
     b64:text=<brief_summary>            # human-glanceable summary in the text column \
     b64:payload=<json.dumps(payload, ensure_ascii=False, separators=(",",":"))> \
     ts=<ch-ts of the session end>
```

- `project`/`cwd` are carried through from the input.json (originally from the transcript/rollup) so the row joins to the rest of the pipeline.
- `ts` = the session's `end_ts` from the Layer A rollup, converted via `_shared.to_ch_ts`; fallback to the `session-summary` row's own `ts`. This places the insight at the session's end instant (UTC), aligned with the pipeline's ts-is-UTC contract — NOT emit time.
- Unknown keys go to `payload` by the collector's own mapping; here we pass the whole structured object explicitly as `b64:payload` and let known keys (`session/project/cwd/text/ts`) map to columns.
- `emit` base64-encodes `b64:`-prefixed values, so arbitrary transcript-derived text (quotes, newlines, unicode) is safe.

**Unreadable sessions ARE emitted** (with `unreadable=true`, empty qualitative fields, populated `unreadable_reason`) so the skip is durable and idempotency (§5) can reason about them — an honest "we looked and couldn't judge" is a first-class record, not a silent drop.

---

## 11. Report integration (`insights.py`)

PR-1 delivers `insights.py` with Layer A (`session-summary`) sections and the `chquery`-based scaffolding (mirrors `activity-scan.py`: pure `gather()`/`render()`, `--days`/`--json`, graceful degrade). PR-2 ADDS a reader + two sections.

**Reader** (one query, argMax dedupe, windowed):
```sql
SELECT session, argMax(payload, ingested_at) AS payload
FROM activity.events
WHERE source='claude' AND kind='session-insight' AND ts > now() - <win>
GROUP BY session
```
Parse each `payload` (tolerate string-or-object as CH JSON returns), skip `unreadable=true` rows from the qualitative aggregates but count them in a "N sessions unreadable" footnote.

**Window:** `--days` default **30** for the insight sections (qualitative facets accrue slower than raw activity); Layer A sections keep their PR-1 default. If no `session-insight` rows in the window: print `"(no qualitative insights yet — run session_insight via the activity skill)"` and render nothing else for these sections (graceful degrade, no crash).

**Section 1 — OUTCOMES:**
- `outcome` distribution (counts + %) across the window.
- Mean `claude_helpfulness` (+ simple 1–5 histogram).
- `session_type` breakdown, and `goal_categories` frequency.
- Top `friction_counts` categories summed across sessions (with the honest caveat that these are qualitative tallies).

**Section 2 — AUTOMATION CANDIDATES / RECURRING TOIL / WORKFLOW GAPS (leverage-ranked):**
- Collect every non-null `automation_opportunity` / `recurring_toil` / `workflow_gap` across the window.
- **Rank automation candidates** by `leverage` (high>medium>low) then by **frequency** — group near-duplicate opportunities by a normalized key (lowercased `trigger`/`description`, whitespace-collapsed; exact-match grouping in PR-2, no fuzzy clustering — YAGNI) and count how many sessions surfaced each. Show `description`, `trigger`, leverage, session-count, and one `evidence` example.
- **Recurring toil** grouped by `category` then description, with `frequency_hint`s.
- **Workflow gaps** grouped by `kind`, then description.
- Honest caveat line (mirroring `activity-scan.py`): these are model-surfaced qualitative candidates, not measured savings — they earn their keep only if reading them changes what Zach automates.

Render style matches `activity-scan.py` (`##` section headers, ranked rows, ASCII `bar()`); reuse its `num()`/`fmt_*`/`bar()` helpers.

---

## 12. `activity` skill SKILL.md additions

> **IMPORTANT for the implementer:** the skill lives **per-host** at `~/.claude/skills/activity/SKILL.md` (NOT in this repo). Edit it there on the workbench (and laptop if used). The repo-tracked docs that MUST also be updated in the PR: `scripts/session-analysis/session_insight/` module docstrings, a `scripts/session-analysis/README.md` note (create/extend), and the `devrc/CLAUDE.md` layout bullet for `scripts/session-analysis/` (add Layer B). The skill text below is the DRAFT to paste into SKILL.md.

### Draft SKILL.md section to add

````markdown
## session insights (Layer B — LLM qualitative facets)

Turns settled Claude sessions into `source=claude, kind=session-insight` rows in
`activity.events`. Deterministic Python does the plumbing; THIS live session does the
extraction (no `claude -p`, no external API). Manual/on-demand only.

Prereqs: `CLICKHOUSE_URL/USER/PASSWORD` in env (reader creds via SOPS — see top of this skill).

**1. See what's pending (no writes):**
```bash
python3 ~/workspace/devrc/scripts/session-analysis/session_insight/cli.py status --json
```

**2. Prepare a batch (deterministic: select settled + un-extracted, scrub, attach ground truth):**
```bash
python3 ~/workspace/devrc/scripts/session-analysis/session_insight/cli.py \
    prepare --days 14 --limit 20 --json
# prints: run_id, staging dir, and the per-session input.json paths.
```

**3. Extract (THIS session does the work):** read each `input.json` and, per session,
write `results/<run-id>/<session>.result.json` conforming to the schema in the input.
- 1 session → do it inline.
- A backlog (>3) → fan out with the Agent tool (`general-purpose`), one disjoint slice of
  sessions per subagent, each writing its result.json. NO worktree isolation needed — the
  agents only READ staging inputs and WRITE result files under `~/.local/state/…`, never the repo.
- Per session it is MAP-REDUCE: note qualitative observations per `chunk`, then reduce to ONE
  result.json. Counts come from `ground_truth` — never recount.

**4. Write to ClickHouse (deterministic: validate + emit):**
```bash
python3 ~/workspace/devrc/scripts/session-analysis/session_insight/cli.py write --run-id <id> --json
```

**5. Read the report:**
```bash
CLICKHOUSE_URL=… CLICKHOUSE_USER=activity_reader CLICKHOUSE_PASSWORD=… \
    python3 ~/workspace/devrc/scripts/session-analysis/insights.py --days 30
```

### Extraction rules (the anti-confabulation contract — NON-NEGOTIABLE)
- The `ground_truth` block = DETERMINISTIC counts (tools, tokens, commits, files, lines, errors,
  interruptions, models, durations). They are FACTS. Do NOT contradict, restate-as-if-counted,
  or invent ANY count/limit/metric. There is **no "output-token maximum"** — that was a
  confabulation by the old built-in; do not reproduce it.
- Your job is ONLY the qualitative facets: underlying_goal, goal_categories, outcome,
  session_type, claude_helpfulness (1–5), friction_counts + friction_detail (INTERACTION
  friction — wrong approaches, repeated corrections — distinct from mechanical tool_errors),
  primary_success, brief_summary, and the automation_opportunity / recurring_toil / workflow_gap
  observations (these three are WHY this data exists — be concrete and evidence-backed).
- Use only the controlled enum values given in the input's `schema` block.
- If a (chunked) transcript is too degraded/truncated/ambiguous to judge honestly, set
  `unreadable=true` + a one-line `unreadable_reason` and leave qualitative fields empty.
  **Flag it — never fabricate.**
- `<REDACTED:…>` tokens are scrubbed secrets; treat as opaque, never guess the original.
````

---

## 13. Cost / scope controls

- **Manual only** — no timer, no daemon, no systemd unit. Operated via the `activity` skill on demand.
- **Batch cap** — `--limit K` (default **20**) caps candidates per `prepare` run (context-budget guard for the session doing extraction; the real cost here is the session's own tokens, not $).
- **Window scoping** — `--days N` bounds selection to sessions whose activity is within the trailing window.
- **Skip log** — every skipped session prints `session, reason` (§5).
- **Settle gate** — `--settle-hours` prevents burning effort on live sessions.
- **No external spend** — extraction runs in the interactive session; there is no API bill line.

---

## 14. Coexistence with the built-in `/insights`

- The built-in `/insights` **cannot be overridden or disabled** (harness-owned). It keeps writing its own ephemeral `~/.claude/usage-data/` cache; ignore it.
- **This pipeline is now the canonical view.** `insights.py` + the `activity` skill are the source of truth; the built-in is explicitly no longer trusted (it confabulated friction). Document this in the skill and `CLAUDE.md` so a future agent doesn't "reconcile" against the built-in's numbers.
- No shared state, no migration — Layer B is a clean, queryable superset.

---

## 15. Test plan (FULL coverage required; LLM step MOCKED)

All Python is unit-testable with **no live ClickHouse and no live LLM**. Follow the repo idiom: pure logic separated from I/O; `chquery`/`emit`/CH access injected. The LLM step is mocked by dropping fixture `result.json` files into a fake `results/<run-id>/` dir.

**Unit-testable (must all be covered):**
1. **Selection / settled logic** (`test_select.py`): fixture `session-summary` rows + synthetic transcript mtimes → assert candidate set respects (has-rollup ∧ settled ∧ not-extracted); `--settle-hours 0` bypass; `--days` window; `--limit` truncation + over-limit skip-log entries.
2. **Scrubber** (`test_scrub.py`): each `SECRET_PATTERNS` entry redacted with correct label + count; private-key block redacted; internal RFC1918/nebula IP SURVIVES with `--redact-public-ips` off; a public IP redacted when on.
3. **Ground-truth prep + input.json contract** (`test_prepare.py`): `ground_truth` embedded verbatim from the (mocked) argMax query; chunk boundaries never split a message; `chunk_count`/`redaction_counts` present; the schema/anti-confab block present; file written under `staging/<run-id>/`.
4. **Schema validation** (`test_schema.py`): valid payload passes; bad `outcome`/`session_type`/`leverage`/`workflow_gap.kind` rejected; `unreadable=true` requires non-empty `unreadable_reason`; null vs husk facets both handled; out-of-vocab `goal_categories` → soft warning not hard fail.
5. **Writer / emit arg shape** (`test_write.py`): `subprocess.run` mocked → assert argv is exactly `emit source=claude kind=session-insight b64:session=… b64:project=… b64:cwd=… b64:text=… b64:payload=… ts=…`, payload round-trips through JSON, `ts` = rollup `end_ts` in CH format.
6. **Idempotency + `--force`** (`test_write.py`/`test_select.py`): a session already having a `session-insight` row is skipped by prepare; `--force` re-prepares; re-write emits a second row (argMax-newer) — assert no delete path exists; a prior `unreadable` row is re-attempted only when transcript mtime is newer.
7. **Unreadable path** (`test_write.py`): a fixture result with `unreadable=true` IS emitted (not dropped) with empty qualitative fields + reason.
8. **Report aggregation** (`test_insights.py`, extend PR-1's): over fixture `session-insight` payloads, assert OUTCOMES distribution, mean helpfulness, and the leverage-ranked automation/toil/gap grouping (high>med>low then frequency); `unreadable` rows excluded from aggregates but counted in the footnote; empty-window graceful-degrade message.
9. **Consolidation merge** (`test_consolidate.py`): clean union; a missing session → reported + not emitted; duplicate results for one session → neither emitted + conflict flagged; a schema-invalid result → quarantined + reported.

**NOT auto-testable — the live extraction QUALITY.** Whether the model's `underlying_goal`/`outcome`/automation calls are *true* cannot be asserted mechanically. Zach validates manually:
- Pick a session he remembers well; run `prepare` for just it (`--settle-hours 0 --limit 1`), extract, `write`, and read the row — confirm the goal/outcome/friction match his memory and that NO count contradicts the Layer A rollup.
- Spot-check that a session containing a pasted key shows `<REDACTED:…>` in the staging input and that no secret appears in the emitted `brief_summary`/payload.
- Confirm the report's automation/toil/gap items are plausible and actionable, not confabulated.

CI/pre-commit: the new tests run under the repo's existing `python3 -m pytest scripts/session-analysis/session_insight/tests` (and the extended `insights` test). No new heavy deps — stdlib only, matching `chquery`/`_shared`.

---

## 16. Open questions / risks / reversibility

**Open questions (resolve before/at implementation):**
- **O1 — Does PR-1's `insights.py` already exist and what sections does it render?** PR-2 EXTENDS it. If PR-1 lands after PR-2 starts, agree the file's `gather()`/`render()` shape first (or PR-2 creates the file and PR-1 rebases). *Recommend: land PR-1 first; PR-2 branches from it.*
- **O2 — Controlled-vocab ownership.** `goal_categories`/`session_type`/`friction_categories` live in `schema.py`. Confirm the starter vocab (§3) fits Zach's work; it is intentionally extensible (soft-fail on unknown category tags).
- **O3 — `claude_helpfulness` scale semantics** (1–5). Confirm the anchor descriptions so the number is comparable across sessions.
- **O4 — Automation-candidate grouping.** PR-2 uses exact normalized-string grouping (YAGNI). If duplicates fragment badly in practice, a later PR can add fuzzy clustering — explicitly out of scope now.
- **O5 — Fan-out threshold + subagent count.** Left to the skill's judgement (suggested: fan out when candidates > 3, ~5 sessions/subagent). Confirm this matches Zach's context-budget comfort.

**Risks:**
- **Context bloat** — extraction happens in the operating session; a large backlog could balloon its context. Mitigated by `--limit`, chunking, and subagent fan-out. The skill should default to modest batches.
- **Staging files hold scrubbed full transcripts on disk** — under `~/.local/state/activity/insights/` (create `0700`). `write.py` (or a `--clean` flag) removes a run's staging + results after a successful emit; document a retention note. Scrubbing already removes secrets, but keep the dir private.
- **Model drift / vocab creep** — soft-fail vocab + `schema_version` bump path contain this.
- **Ground-truth staleness** — if Layer A re-emits after PR-2 extracted, the insight's counts (which we don't store — we reference) stay consistent because the report reads Layer A separately; the insight only holds qualitative fields. No divergence risk.

**Reversibility:** fully reversible. Writes are append-only into a 180d-TTL table; a bad extraction is superseded by re-running (argMax). No schema migration, no deletes, no changes to Layer A or the message stream, no infra changes. Removing the feature = deleting the module + the report section; existing rows age out via TTL. Classified **reversible**.

---

## 17. Task breakdown (implementation-ready, ending in a PR against `main`)

Conventions baked in (from RULES / CLAUDE.md): feature branch; **never `git add -A`** (stage paths individually); **`git -C <path>`** never `cd`; `#!/usr/bin/env bash` for any shell; NixOS → `nix-shell -p <pkg>` for ad-hoc deps; KISS/YAGNI; commit trailer `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`; PR body ends `🤖 Generated with [Claude Code](https://claude.com/claude-code)`.

1. **Branch.** From up-to-date `main` (ideally after PR-1 merges; see O1): `git -C $DEVRC checkout -b feat/insights-telemetry-pr2 origin/main`.
2. **`schema.py`** — payload schema, controlled vocabularies, `validate()`, shared field-name constants. Tests: `test_schema.py`.
3. **`scrub.py`** — vendor bash-guard `SECRET_PATTERNS` + private-key + optional public-IP; `scrub(text) -> (clean, counts)`. Tests: `test_scrub.py`.
4. **`select.py`** — CH queries (has-rollup, settled, already-extracted) via `chquery`; transcript-mtime gate via `_shared.iter_transcripts`; candidate selection honoring `--days/--limit/--settle-hours/--force`; skip-log. Tests: `test_select.py` (inject a fake `CHClient` + fixture mtimes).
5. **`prepare.py`** — fetch ground-truth argMax, scrub, chunk, write `staging/<run-id>/<session>.input.json` + `manifest.json`; embed the schema + anti-confab block. Tests: `test_prepare.py`.
6. **`consolidate.py`** — union/missing/conflict/schema-quarantine over `results/<run-id>/`. Tests: `test_consolidate.py`.
7. **`write.py`** — consolidate → per valid result build emit argv (`_shared.emit_path`) → `subprocess.run`; unreadable rows emitted; `--clean` to purge the run dir on success. Tests: `test_write.py` (subprocess mocked).
8. **`cli.py`** — argparse: `status`/`prepare`/`write`; flags `--days --limit --settle-hours --force --chunk-chars --redact-public-ips --run-id --json --clean`; `--json` machine output mirroring `mail-actions`/`activity-scan`.
9. **`insights.py` extension** — add the `session-insight` argMax reader + the OUTCOMES section + the leverage-ranked automation/toil/gap section; reuse `num/fmt_*/bar`; graceful degrade + `--days` default 30 for these sections. Tests: extend `test_insights.py`.
10. **Docs** — module docstrings; `scripts/session-analysis/README.md` (create/extend) documenting the 4-step operator flow; add a Layer B bullet to `devrc/CLAUDE.md`'s `scripts/session-analysis/` line. Stage each file individually.
11. **Skill (per-host, NOT in the PR)** — paste §12 into `~/.claude/skills/activity/SKILL.md` on the workbench; flag in the PR description that this manual step is required post-merge.
12. **Validate end-to-end (honest verification):** run `prepare` for one remembered, settled session → extract inline → `write` → query the emitted row and confirm (a) payload validates, (b) NO count contradicts the Layer A rollup, (c) a pasted-secret session shows `<REDACTED>` and no secret leaks into the summary, (d) `insights.py --days 30` lights up both new sections. State plainly what was reproduced vs merely built.
13. **Run tests:** `python3 -m pytest scripts/session-analysis/session_insight/tests scripts/session-analysis/tests -q` (use `nix-shell -p 'python3.withPackages(...)'` only if a dep is missing — the design is stdlib-only, so plain `python3` should suffice).
14. **PR against `main`** — descriptive title (`feat(insights): Layer B session-insight LLM qualitative facets`), body covering the design + the required manual SKILL.md step + what was verified vs unverified, ending with the required trailer/footer.
