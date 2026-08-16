# session-manager — ClickHouse queries

## Endpoint and credentials

**Workbench endpoint only.** Both hosts ship telemetry to the same homelab ClickHouse pod,
so one endpoint holds the full dataset — there is no per-host CH to fan out to.

The endpoint, user and password are read from `~/.config/activity-collector/env` (the
collector's own file, `chmod 600`, not in the nix store and not committed). `make_ch_client()`
merges it under `os.environ`, so a one-off override works without editing the credentials
file:

```bash
CLICKHOUSE_URL=http://<host>:<port> python3 $DEVRC/scripts/session-manager --json
```

The HTTP client itself is `scripts/validation/chquery.py` — a **library** (`CHClient` /
`CHConn`, no `__main__`), reached by a `sys.path` insert resolved relative to
`scripts/session-manager`, not to `$DEVRC` (which is wrong inside a worktree and absent in
the nix build sandbox).

## Query 1 — recent Claude/OpenCode sessions (`SQL_RECENT_SESSIONS`)

```sql
SELECT session,
       argMax(project, ingested_at)        AS project,
       argMinIf(text, ts, kind = 'prompt') AS first_msg,
       max(ts)                             AS last_seen
FROM activity.events
WHERE source IN ('claude','opencode') AND ts > now() - INTERVAL 1 DAY
GROUP BY session ORDER BY last_seen DESC LIMIT 20
```

🔴 **There is no `first_message` column.** `activity.events` has 13 columns — `ts, host,
source, kind, project, cwd, session, app, text, duration_ms, exit_code, payload,
ingested_at` — and an earlier draft of this query named `first_message`, which fails
outright with `Code: 47 … UNKNOWN_IDENTIFIER`. The first prompt is *reconstructed* with
`argMinIf(text, ts, kind = 'prompt')`; `kind='prompt'` is populated for both `claude` and
`opencode`.

This string is pinned by a contract test that types its own independent copy. If you change
the SQL, change the test's copy deliberately — never regenerate the expectation from the
code.

## Query 2 — per-session prompt history (`sql_session_history`)

```sql
SELECT ts, kind, left(text, 200) AS snippet
FROM activity.events
WHERE session = '<quoted>' AND ts > now() - INTERVAL 1 DAY
ORDER BY ts DESC LIMIT 10
```

**Consumer: `detail_history()`, called by `main()` for the `detail` subcommand.** It runs
for the `claude_session_id` of the window `detail` narrowed to, and attaches the result as
`session_history`. (In the first revision this function was defined and called from
*nowhere*, while this section documented it as implemented — a doc describing behaviour that
did not exist. `test_sql_session_history_IS_reachable_from_main` now names the caller.)

`session_history` is status-discriminated like `ch_query`, and its `skipped` carries a
`reason`, because facts as different as these would otherwise all render as "no history":

| status | reason | means |
|---|---|---|
| `skipped` | `--no-ch` | the query was never run |
| `skipped` | `fuzzyclaw was skipped (--no-fuzzyclaw) …` | the task files were never read — **not** a measured absence |
| `skipped` | `fuzzyclaw is read on the LOCAL host only …` | **any** matched window is remote — fuzzyclaw is local-only, so no task file was searched for it. Fires on a mixed local+remote row set too: with the default `--host all`, one `session:index` living on both machines yields a row from each, and the shared half must not be reported as a measured absence — **not** a measured absence |
| `skipped` | `the fuzzyclaw intersection never ran …` | the live-window set was never measured — **not** a measured absence |
| `skipped` | `this window's slot was claimed by N task files and ALL were dropped …` | contested slot; no id is trusted — **not** a measured absence |
| `skipped` | `no window in this report matched the requested target …` | nothing to carry an id — **not** a measured absence |
| `skipped` | `fuzzyclaw reported status '<x>' …` | a status this reader does not recognise — the measured absence below is GATED on `ok` rather than reached by fallthrough, so a status added later cannot silently become one — **not** a measured absence |
| `skipped` | `this window carries no claude_session_id (no live fuzzyclaw task file joined to it)` | **the one genuine measured absence**: fuzzyclaw ran, this local window simply has no task |
| `ok`, `rows: []` | — | the query ran and this session has no prompts in 24h |
| `unreachable` / `query_error` / `unavailable` | — | the query did not answer |

🔴 **Only the last `skipped` row is a measured negative.** A single hardcoded reason used to
answer *all* of them, so one `detail --json` could print `LIVE COUNT UNMEASURED` and then
assert a measured absence over that same unmeasured set a few lines later. Every
non-measured reason now ends with **"this is NOT a measured absence"**, and
`no_session_reason()` — pure, unit-tested, branching only on facts the report already
carries — chooses between them.

The session id goes through `chquery.sql_quote()` — the repo's one quoter. Do not build a
second one, and do not f-string a raw id into SQL. The id comes from a source `CLAUDE.md`
marks UNTRUSTED and it reaches SQL, so a hostile-id test pins the escaping.

## 🔴 Reading the result

`ch_query()` never returns a bare list. It returns:

```json
{"status": "ok|unreachable|query_error|error|skipped", "rows": [], "error": null, "code": null}
```

`rows: []` is a real, measured zero **only** when `status == "ok"`. Everything else means
the query did not answer, and both output modes say so explicitly — the table prints
`QUERY FAILED [<status>] … (this is NOT zero sessions; the query did not answer)`.

`CHUnreachable` (nothing can be said about any query — abort the gather) and `CHQueryError`
(the server answered and rejected *this* query — carry on degraded) are distinct on purpose;
`chquery.py`'s own header explains why collapsing them once made a healthy pipeline report
as "telemetry unavailable" with exit 0.

## The join to tmux

Two joins, in sequence. Only the second one touches ClickHouse.

**1. pane row ← fuzzyclaw task.** `filter_live_tasks()` keeps a task file only when its
`window_id` is live *and* that live window's real `(session, index)` equals the one the file
records; `index_tasks_by_window()` then looks the task up by that already-verified slot and
**drops any slot two files both claim**. So the task attached to a row is one whose window
identity has been checked, not assumed. `row.window_id == row.fuzzyclaw.window_id` holds for
every joined row and is asserted end-to-end.

🔴 This is the join that was wrong. The guard checked `window_id` while the lookup keyed on
`(session, index)`, so a file could pass the guard and then attach to whatever window had
since taken its slot — measured: 7 of 43 survivors, plus 5 contested slots resolved by
silent last-wins.

**2. task → ClickHouse.** `fuzzyclaw.claude_session` → `activity.events.session` is the only
carrier of the session id from a tmux pane to ClickHouse; the `/proc` detector
(`scripts/lib/claude_sessions.py`) sees *that* Claude runs in a pane but never learns
*which* session. Measured 2026-08-11:
`activity.events.session` is 36 chars in 100% of `source='claude'` rows (1107/1107 over 2
days) and fuzzyclaw's `claude_session` is a 36-char UUID, so the join is structurally sound
— **provided** the task file survived join 1. That proviso is load-bearing: a wrong
`claude_session_id` here pulls a *different session's* prompt history and renders it as this
window's. See the SKILL body.
