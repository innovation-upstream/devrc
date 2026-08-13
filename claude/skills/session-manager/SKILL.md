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
| `--json` | JSON (default is a table) |
| `--host workbench\|laptop\|all` | default `all`; `tail` resolves `all` to LOCAL |
| `--claude-only` | drop non-Claude rows; every count then describes the FILTERED set, and `summary.excluded_non_claude` says how many went |
| `--no-ch` | skip ClickHouse — the client is never constructed |
| `--no-capture` | skip the pane scrape; **every** `waiting_probable` becomes `null` |
| `--fuzzyclaw` / `--no-fuzzyclaw` | the task-file join is **OFF by default** (see below) |
| `--plain` | `tail` only: strip ANSI at the source instead of `sed`-ing it out |
| `--stale-threshold <secs>` | default 3600; `age >= threshold` is stale |
| `--lines N` | `tail` scrollback depth (default 100) |

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

## The caveats are in the OUTPUT, not just in this file

`report["caveats"]` (structured) + three footer lines in the table, printed
unconditionally — an agent that runs the script cold never reads this file:

- `claude_detection` — `pane_current_command =~ /claude/`; a claude under a wrapper shell
  reads as `shell` (shallower than agent-ops' `/proc` walk, which is not reachable over SSH).
- `fuzzyclaw_scope` — `local_host_only`; a REMOTE row carries null `fuzzyclaw` /
  `claude_session_id` / `age_secs` and is never labelled `stale`.
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
