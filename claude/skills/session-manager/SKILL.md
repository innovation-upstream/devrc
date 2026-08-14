---
name: session-manager
description: "Live cross-host view of every tmux window on workbench + laptop — which have Claude Code running, what each is doing, which ones are WAITING ON A HUMAN (asked a question / blocked on a modal / out of context), how stale it is, plus the clawgate approval queue and recent agent sessions from ClickHouse. JSON-first, read-only. Use for: is anything waiting on me, active sessions, what's running where, tmux state across both hosts, cross-host session status, tail a tmux window, which windows are stale/idle/busy/blocked."
---

# session-manager — cross-host tmux + agent activity

`scripts/session-manager`. One-shot, **read-only**, `--json`-first, both hosts.

```bash
python3 $DEVRC/scripts/session-manager --json                      # everything
python3 $DEVRC/scripts/session-manager --host workbench --no-ch    # fast + offline
python3 $DEVRC/scripts/session-manager detail scratch7:3           # one window + prompts
python3 $DEVRC/scripts/session-manager tail scratch7:3 --plain     # scrollback, no ANSI
```

🔴 **Rows are at `report["hosts"][<"workbench"|"laptop">]["windows"]`** — not at the top
level. Roll-ups: `summary.waiting`, `summary.status[bucket]` (`{claude, shell, total}`),
`blocked_on_me`.

| flag | effect |
|---|---|
| `--json` | JSON (default is a table). Compact, not indented — 34% of the payload was whitespace for a reader that does not exist |
| `--lean` | 🔴 **with `--json`, prefer this.** The agent-shaped view: rows trimmed to the fields that answer this tool's question, **untruncated**, with every measurement discriminator and the caveats kept |
| `--host workbench\|laptop\|all` | default `all`; `tail` resolves `all` to LOCAL |
| `--claude-only` | drop non-Claude rows; every count then describes the FILTERED set, and `summary.excluded_non_claude` says how many went |
| `--no-ch` | skip ClickHouse — the client is never constructed |
| `--no-capture` | skip the pane scrape; **every** `waiting_probable` becomes `null` |
| `--fuzzyclaw` / `--no-fuzzyclaw` | the task-file join is **OFF by default** (see below) |
| `--no-ledger` | skip the per-host agent-ledger read. Rows then have **no age and no session id** — the #419 view, reproducible on demand |
| `--plain` | `tail` only: strip ANSI at the source instead of `sed`-ing it out |
| `--stale-threshold <secs>` | default 3600; `age >= threshold` is stale |
| `--lines N` | `tail` scrollback depth (default 100) |

## 🔴 Which output to ask for — you are the only consumer

Measured: **0 interactive shell invocations in 30 days against 55 agent references**, confirmed
by the operator 2026-08-14. An agent reads this, and pays by the token.

| | cost on a 75-row scan | faithful? |
|---|---|---|
| table (default) | ~3,280 tok | ❌ **lossy** — 73 truncated cells, 45 rows whose task exceeds the 25-char column |
| `--json` | ~14,013 tok | ✅ |
| `--json --lean` | ~9,085 tok | ✅ on what it keeps |

**Ask for `--json --lean` unless you need a dropped field.** It is cheaper than the full payload
AND more faithful than the cheap one. `lean_row_fields` and `lean_host_fields` travel in the payload
naming exactly what this view CARRIES — so a key absent from a row was omitted by the view, never
measured as null. `caveats`, `summary.waiting`'s tri-state, `blocked_on_me` and every per-host
measurement status are kept in full, because a cheap payload that can lie is worse than an
expensive one.

Dropped from **rows** (8): `window_id`, `window_name`, `codename`, `pane_id`, `command`, `panes`,
and the `ledger`/`fuzzyclaw` sub-objects — duplication, their useful contents are already flat on
the row. `label_source` is deliberately KEPT: like `age_source` it is provenance, and it is the
only thing separating a row labelled from a real directory from one labelled because the cwd
yielded nothing.

Dropped from **hosts** (2): `ssh_target` (fixed config the caller already knows) and
`live_window_ids` (a ~346 B array no consumer reads).

## 🔴 `waiting_probable` — is anything waiting on a HUMAN

`status: idle` merges four states that need four different actions. Each Claude pane is
scraped (one batched `capture-pane` per host) for three signals, and every row carries the
**matched line** so you can disagree with it:

| signal | means | do |
|---|---|---|
| `trailing_question` | the agent's last sentence ends in `?` | answer it |
| `selection_menu` | `❯ 1./2./3.` modal is up | press a key |
| `context_exhausted` | `ctx: 0%` | `/clear` it |

🔴 **`waiting_probable: false` means "these three were looked for and none matched" — NOT
"this window needs nothing."** Recall is partial by construction: measured on 40 live panes
2026-08-12, a window parked on `Press Enter to continue…` matches none of them, and text
typed at the `❯` prompt is deliberately **excluded** (a window one Enter away reads `no` —
see `reference/waiting-signal.md` for the evidence and what would justify turning it on).

🔴 **`waiting_probable: null` is not `false`.** Read `waiting_status`: `ok` (scraped),
`not_claude` (never scraped — the signals are Claude-TUI shapes and a shell's last line
ending in `?` would be a false positive), `uncaptured` (the batch ran, this pane was not in
it), `skipped` / `error`. `summary.waiting.probable` is likewise **`null`, never `0`**, when
nothing was scraped — the one sentence this tool must never emit is "nothing is waiting on
you" off a look that never happened.

## 🔴 `blocked_on_me` — the clawgate approval queue

Read from the bar poller's cache. It is here because an accurate cross-reference once cost
real signal: a dogfooding agent read that `agent-ops` has no JSON API, correctly preferred
this script, never opened agent-ops, and **missed 11 pending approvals — four of them
credential-exposure or cross-user-data-leak.**

- **`count` = pending + STUCK.** `pending_count` is the `{open, ready_for_review}` half;
  `stuck_count` is `in_progress` tasks whose agent looks dead. The two always travel with
  the total. 🔴 Excluding `in_progress` ("an agent is working = not on the human") is exactly
  what hid a dispatch whose agent had been dead **four hours** — invisible on every surface,
  because this script reads the poller's cache rather than the API. The predicate is
  `scripts/lib/clawgate_tasks.py`, shared with the poller and agent-ops.
- **`open` / `ready_for_review` / `stuck` ENUMERATE the queue** — a count that moves without
  naming what moved is how three finished tasks appeared in no list anywhere. Each `stuck`
  row carries `reasons` (`no_agent` / `agent_error` / `not_kicked_off` / `agent_idle` /
  `activity_unknown`) and `agent_idle_secs`: **`idle 16m` and `idle 4h` are the same
  boolean** and only one is worth acting on. Idle time cannot see progress *within* an
  in-flight turn, so it errs toward a false alarm — the intended direction.
- **`count` is the measurement; `detail` is not.** Nothing here parses it. Schema-2 detail
  states its own cap (`(+N more)`); a schema-1 string truncated silently to ~6 ids and has
  dropped `ready_for_review` items.
- 🔴 **`schema_ok: false` means STUCK WAS NOT MEASURED, not zero stuck** — the cache came
  from a poller predating the predicate (i.e. `home-manager switch` + restart
  `bar-status-poll` has not happened yet). `stuck_count` is `null` in that case.
- Four states: `ok` / `stale` (cache older than 300s — the poller writes every 45s) /
  `absent` / `unparseable`. The last three publish **`count: null`, never `0`**.
- **`agent-ops`** (`$mod+i`, tmux `prefix+A`, ▦ bar button) shows the same queue LIVE off the
  API rather than off the cache, plus open PRs and a `/proc` walk that finds a `claude`
  buried under a wrapper shell. Complements, not substitutes.

## `label` + `hotkey` — a name for the sessions the slot table never named

`codename` is null for anything outside `tmux-scratch-slots.sh`; on the workbench that was
9 of ~30 windows. Every row now also carries `label` (+ `label_source`, the tier that
answered, + `hotkey`): **`codename`** if the session is a scratch slot → else the **leaf of
the pane's cwd** (`path`) → else **`main`** (`fallback`, i.e. cwd was `$HOME`/`/`/empty).
The table's old CODENAME column is now LABEL and renders `Grove (S)`.

🔴 **`hotkey` is the actionable half** — it is the `$mod+Shift+<k>` that gets you to the
window, read from the slot table's own `key` field. It is **`null` (never `""`) on tiers 2
and 3**: no binding exists for a session outside the table, so quoting a key there would
send the operator to press something and land nowhere.

🔴 **`label` is NOT an address — `session:window` still is.** Two sessions sitting in one
repo resolve to the SAME label, so quote both. New non-scratch sessions get a real tmux name
at creation from `scripts/tmux-autoname-session.sh`; `label` is what covers the ones that
predate it.

## The caveats are in the OUTPUT, not just in this file

`report["caveats"]` (structured) + three footer lines in the table, printed
unconditionally — an agent that runs the script cold never reads this file:

- `claude_detection` — `pane_current_command =~ /claude/`; a claude under a wrapper shell
  reads as `shell` (shallower than agent-ops' `/proc` walk, which is not reachable over SSH).
- `fuzzyclaw_scope` — `local_host_only`; a REMOTE row carries null `fuzzyclaw`. It says
  **nothing** about age/session-id/stale (it used to, and that was wrong — see below).
- `ledger_scope` — `per_host`; `age_secs` / `claude_session_id` come from the agent ledger,
  read on EVERY scanned host, so a REMOTE row has both and **can** be `stale`.
- `waiting_signal` — the enumerated signal set, the claude-rows-only scope, and the
  prompt-text exclusion with its reason.

## 🔴 Read the exit code — the two zeroes are different facts

| code | meaning |
|---|---|
| `0` | ran, found windows (**including** a partial scan where one host was unreachable) |
| `2` | usage / bad `<session>:<window>` / **`tail`: the host answered, no such window** |
| `3` | every requested host answered and the answer is a **real zero** |
| `4` | **no** host could be reached — the zero is unmeasured, not measured |
| `5` | **`tail` only**: the host answered and there is **no tmux server** on it |

Rationale, and why 5 had to be split out of 3: `reference/exit-codes.md`.

Same discipline inside the payload: `hosts.<n>.reachable`/`.error` describe the
**`list-panes`** call, `.windows_measured`/`.windows_error` the **`list-windows`** call, and
`.captures_measured`/`.captures_status` the **capture batch** — three independent
measurements, and one succeeding says nothing about the others. `clickhouse.status` must be
`ok` before `rows: []` is believable. **Never read a bare count without its status.**

## fuzzyclaw is OFF by default

Measured 2026-08-12: **29 live of 401 files, 363 stale, 9 slot-mismatched — and every one of
the 29 live rows read `paused`**, including a window demonstrably running an agent. 29 table
rows, zero contribution, from a source `CLAUDE.md` marks UNTRUSTED. Opt in with
`--fuzzyclaw`; `--no-fuzzyclaw` still works and now names the default. Off, every count is
`null` rather than `0`.

The intersection guard is unchanged and still runs when you opt in — a task file survives
only when its `window_id` is live **and** that live window's real `(session, index)` equals
the one the file recorded. Why that relationship (not mere existence) is the guard, what the
slot-conflict drop does and does not still catch, and the field ledger:
`reference/fuzzyclaw-guard.md`.

## The agent activity ledger — where age / `stale` / `claude_session_id` come from

#419 switched fuzzyclaw off, which also switched off the only supplier of `age_secs`, the
`stale` bucket derived from it, and the `claude_session_id` the ClickHouse join needs.
Measured 2026-08-12 on the shipped default view: **0 rows with an age, 0 with a session id,
no `stale` bucket at all** — and nothing in the output said so.

A devrc-owned Claude hook now writes one record per tmux pane into `~/.cache/agent-ledger/`,
and the script reads each host's ledger with one `sh -c` (locally and over SSH). Read
`report["ledger"]`, never just the row:

- `status` — `ok` / `partial` (some host did not answer) / `error` / `skipped`. Only `ok`
  and `partial` publish integers; the rest are `null`, never `0`.
- `hosts.<host>` — per host: `live of seen`, its `tmux_pid`, and the rejections
  (`not_live`, `generation_mismatch`, `unparseable`, `no_window`) plus
  `generation_unchecked`, which counts records that were **kept while unverified**.
- `summary.rows_with_age` / `rows_with_session_id` / `age_sources` — the meter. A `stale=0`
  bucket means *either* nothing is stale *or* nothing has an age; only this tells them apart.

Row fields: `age_secs`, `age_source` (`ledger` / `fuzzyclaw` / `null` — which writer
answered; the ledger wins), `ledger` (the joined record), `claude_session_id`.

🔴 **A null age is not age 0** — it means no writer has recorded that window. A session that
has not taken a turn since the hook was registered has none yet.

🔴 **Records carry the tmux SERVER pid and the reader checks it.** Window ids restart at `@0`
when the server does, so after a reboot a stale `@41` record and a fresh `@41` window would
otherwise collide and hand the new window a dead session's id. Spec:
`claudedocs/spec-agent-activity-ledger.md`.

## Where everything lives

| path | what |
|---|---|
| `scripts/session-manager` | the script |
| `scripts/tests/test_session_manager.py` | the hermetic suite (mocks tmux, SSH, CH, FS) |
| `scripts/tmux-scratch-slots.sh` | codename table |
| `scripts/validation/chquery.py` | shared CH client — a LIBRARY, `sys.path`-inserted |
| `~/.config/activity-collector/env` | CH endpoint + creds (never hardcoded) |
| `~/.cache/bar-status/clawgate.json` | the blocked-on-me cache (`scripts/bar-status-poll`) |
| `~/.tmux/tasks/*.json` | fuzzyclaw task files (UNTRUSTED) |
| `~/.cache/agent-ledger/*.json` | the agent activity ledger, one record per pane |
| `scripts/lib/agent_ledger.py` | its record shape, read protocol and join filter |
| `scripts/claude-hooks/agent-ledger-hook.py` | writer 1 (Claude Code) |

Reference: `waiting-signal.md`, `exit-codes.md`, `fuzzyclaw-guard.md`,
`clickhouse-queries.md`, `cross-host.md`.

## Gotchas

- Both hosts report `hostname` as `nixos`. The local label comes from `ACTIVITY_HOST` (env,
  then the collector env file), defaulting to `workbench`.
- `tmux` saying *"no server running"* is a **reachable** host with zero windows, not an
  unreachable one. Keep the two separate.
- `signal` / `kill` are **not implemented, deliberately.** This tool never writes to,
  signals or kills a window — which is the only reason it is safe to point at a live machine
  holding 40+ windows of real work. A `waiting` flag is a read; acting on it is not. A
  destructive verb needs its own PR and its own guards.
- Merged ≠ deployed: this file only reaches `~/.claude/skills/` on a `home-manager switch` /
  `ship.sh`. The script itself runs straight from the repo checkout.
