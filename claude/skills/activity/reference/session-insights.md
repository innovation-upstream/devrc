# activity — Layer B session insights: extraction contract + backlog operations

Read this **before doing an extraction run** (step 3 of the Layer B flow in `SKILL.md`).
It carries the anti-confabulation contract you must follow while extracting, and the
lessons from running a real backlog.

## Extraction rules (the anti-confabulation contract — NON-NEGOTIABLE)

- The `ground_truth` block = DETERMINISTIC counts (tools, tokens, commits, files, lines,
  errors, interruptions, models, durations). They are FACTS. Do NOT contradict,
  restate-as-if-counted, or invent ANY count/limit/metric. There is **no "output-token
  maximum"** — that was a confabulation by the old built-in; do not reproduce it.
- Your job is ONLY the qualitative facets: `underlying_goal`, `goal_categories`, `outcome`,
  `session_type`, `claude_helpfulness` (1–5: 5 = Claude materially drove the win … 1 =
  mostly got in the way), `friction_counts` + `friction_detail` (INTERACTION friction —
  wrong approaches, repeated corrections — distinct from mechanical `tool_errors`),
  `primary_success`, `brief_summary`, and the `automation_opportunity` /
  `recurring_toil` / `workflow_gap` observations (these three are WHY this data exists —
  be concrete and evidence-backed).
- Use only the controlled enum values given in the input's `schema` block.
- If a (chunked) transcript is too degraded/truncated/ambiguous to judge honestly, set
  `unreadable=true` + a one-line `unreadable_reason` and leave qualitative fields empty.
  **Flag it — never fabricate.**
- `<REDACTED:…>` tokens are scrubbed secrets; treat as opaque, never guess the original.
- Per session it is MAP-REDUCE: note qualitative observations per `chunk`, then reduce to
  ONE `result.json`. Counts come from `ground_truth` — never recount.

## Operating the backlog (lessons from a real 8-session run)

- **Scale**: ~90+ sessions are typically pending (`status --json` → `candidates`). Extract
  in BOUNDED batches (`prepare --limit ~6`) — the real cost is THIS operating session's own
  tokens, so never fan the whole backlog at once.
- **Fan-out**: >3 sessions → use the Agent tool (`general-purpose`), one disjoint slice per
  subagent, each writing its own `result.json`. **No worktree isolation needed** — agents
  only READ staging inputs and WRITE results under `~/.local/state/…`, never the repo.
- **Session sizes vary wildly** — some transcripts are 250–280 `chunks`. Give each such
  MONSTER session its OWN subagent, and tell subagents to SAMPLE strategically: first ~3
  chunks (goal), last ~3 (outcome), ~every 25th middle chunk (friction / automation / toil /
  gap signals). NEVER read all chunks — it blows context, and the counts come from
  `ground_truth` anyway. Batch several SMALL sessions (<~40 chunks) per subagent.
- **`write --run-id <id>`** validates + emits + PURGES each session's staging/result on
  success (per-session); a session that is missing/conflict/rejected is RETAINED for
  re-run. Re-runs are append-only (argMax-latest wins); `--force` re-extracts.
- **What it produces** (so the value is legible): the first 8-session batch surfaced a
  dominant **config-as-code gap for the storage/CDN layer** (CORS / CF-rules / egress-IPs
  managed out-of-band, not in git — recurred ×5 across incidents) and **B2-incident tooling
  as repeated toil** (~18 throwaway probe scripts / hand-launched diagnostic Jobs across ≥3
  sessions) — the leverage-ranked automation/toil/gap outputs.

## Layer A emit-on-settle (why there are several rows per session)

`kind=session-summary` (`claude/session-tailer.py`, 5-min timer) emits once on first sight,
at most once per `CLAUDE_SUMMARY_INTERIM_HOURS` (default 4) while the session is live, and
again whenever the transcript has been idle `CLAUDE_SUMMARY_SETTLE_MINUTES` (default 20) —
so a `--resume`d session still gets a correct final rollup. Both knobs are read from the
environment at run time; `0` disables that gate.

State: `~/.local/state/activity/session-summary-state.json`
(v2: `{"sessions": {path: {sig, emitted_at}}}`; a corrupt/missing file degrades to "never
emitted", never crashes the timer).

Expect ~1–5 rows per session; the `session_summary_rows_bounded` invariant flags >24
rows/session ingested in 24h. Historical duplicates (pre-2026-07-30: 27,061 rows over 702
sessions, avg 38.5, worst 486, 97.4% immediately superseded) are NOT rewritten — they age
out under the 180d TTL.
