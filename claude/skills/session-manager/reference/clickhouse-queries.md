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

The session id goes through `chquery.sql_quote()` — the repo's one quoter. Do not build a
second one, and do not f-string a raw id into SQL.

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

`fuzzyclaw.claude_session` → `activity.events.session` is the only carrier of the session id
from a tmux pane to ClickHouse; `agent-ops` detects *that* Claude runs in a pane but never
learns *which* session. Measured 2026-08-11: `activity.events.session` is 36 chars in 100%
of `source='claude'` rows (1107/1107 over 2 days) and fuzzyclaw's `claude_session` is a
36-char UUID, so the join is structurally sound — **provided** the task file survived the
live-window intersection. See the SKILL body.
