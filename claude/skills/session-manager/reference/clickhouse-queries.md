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

🔴 **NEVER PASTE CAPTURED OPERATOR TEXT INTO A COMMITTED FILE.** `first_msg` is **text the
operator typed** — the opening prompt of every recent session, up to 20 of them, ~17 KB in a
default scan — and devrc is a **PUBLIC** repo, as is every `claudedocs/` note, commit message,
PR body, comment or test fixture an agent writes into it. Report it as a **count, a length or
a shape**, never verbatim; `--no-ch` drops the block entirely. Identical rule, identical
reason, to `unsent_prompt` in `waiting-signal.md` — and that rule named only the draft until
2026-08-21, which is why this paragraph exists here rather than being assumed.

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
| `skipped` | `the agent ledger was skipped (--no-ledger) …` | no record was read — **not** a measured absence |
| `skipped` | `the agent ledger did not answer for every host this target matches …` | **any** matched row sits on a host whose ledger read is not `ok` (`error` / `no_sentinel` / `unmeasured`, or no block at all), so nothing was searched for it. Fires on a mixed row set too: with the default `--host all`, one `session:index` living on both machines yields a row from each, and the unanswered half must not be reported as a measured absence. Names the host and its status — **not** a measured absence |
| `skipped` | `no window in this report matched the requested target …` | nothing to carry an id — **not** a measured absence |
| `skipped` | `the agent ledger reported status '<x>' …` | a roll-up status this reader does not recognise — the measured absence below is GATED on `ok`/`partial` rather than reached by fallthrough, so a status added later cannot silently become one — **not** a measured absence |
| `skipped` | `this window carries no claude_session_id (the agent ledger was read on its host and holds no live record for it)` | **the one genuine measured absence**: the ledger answered for this row's host and simply has no live record |
| `ok`, `rows: []` | — | the query ran and this session has no prompts in 24h |
| `unreachable` / `query_error` / `unavailable` | — | the query did not answer |

🔴 **Only the last `skipped` row is a measured negative.** A single hardcoded reason used to
answer *all* of them, so one `detail --json` could print an UNMEASURED banner and then
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

**1. pane row ← agent-ledger record.** `_host_ledger()` keeps a record only when its
`window_id` is live **on that host** *and* the record's tmux server pid matches the live
server's, so a stale `@41` from before a reboot cannot hand a fresh `@41` a dead session's
id. The row's own `window_id` is the key the lookup used, so the join is auditable in the
output rather than only inside `fold_windows`.

⚠ Where two records claim one `window_id`, `agent_ledger.index_records_by_window()` resolves
by **last activity** and reports the conflict — it does not drop the window. That is
deliberately different from the fuzzyclaw join this replaced, which dropped a contested slot
outright; so there is no "contested" reason in the table above, because no such row exists.

🔴 The fuzzyclaw join this replaced was WRONG, and it is why the guard above is keyed the way
it is: its liveness check used `window_id` while its lookup keyed on `(session, index)`, so a
task file could pass the guard and then attach to whatever window had since taken its slot —
measured 2026-08-11: 7 of 43 survivors, plus 5 contested slots resolved by silent last-wins.
Those readers are now deleted.

**2. record → ClickHouse.** The ledger's `session_id` → `activity.events.session` is the only
carrier of the session id from a tmux pane to ClickHouse; the `/proc` detector
(`scripts/lib/claude_sessions.py`) sees *that* Claude runs in a pane but never learns
*which* session. Measured 2026-08-11:
`activity.events.session` is 36 chars in 100% of `source='claude'` rows (1107/1107 over 2
days) and the recorded `session_id` is a 36-char UUID, so the join is structurally sound
— **provided** the record survived join 1. That proviso is load-bearing: a wrong
`claude_session_id` here pulls a *different session's* prompt history and renders it as this
window's. See the SKILL body.
