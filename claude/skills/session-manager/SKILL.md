---
name: session-manager
description: "Live cross-host view of every tmux window on workbench + laptop — which have Claude Code running, what each is doing, how stale it is, plus recent agent sessions from ClickHouse. JSON-first, read-only. Use for: active sessions, what's running where, tmux state across both hosts, cross-host session status, tail a tmux window, which windows are stale/idle/busy."
---

# session-manager — cross-host tmux + agent activity

`scripts/session-manager`. One-shot, read-only, `--json`-first. It is the **queryable
counterpart to `agent-ops`**, which is the always-on local tmux popup with no JSON API and
no cross-host reach.

## Run it

```bash
python3 $DEVRC/scripts/session-manager                       # table, both hosts
python3 $DEVRC/scripts/session-manager --json                # agent-readable
python3 $DEVRC/scripts/session-manager --host workbench --no-ch   # fast + offline
python3 $DEVRC/scripts/session-manager list                  # tmux only, no ClickHouse
python3 $DEVRC/scripts/session-manager detail scratch7:3     # one window + its prompt history
python3 $DEVRC/scripts/session-manager tail scratch7:3 --lines 200   # --host defaults to LOCAL
```

`detail` additionally runs the per-session prompt-history query for that window's
`claude_session_id` and attaches it as `session_history` (skipped, with a stated reason,
under `--no-ch` or when the window carries no session id). See
`reference/clickhouse-queries.md`.

| flag | effect |
|---|---|
| `--json` | JSON (default is a table) |
| `--host workbench\|laptop\|all` | default `all` |
| `--stale-threshold <secs>` | default 3600; `age >= threshold` is stale |
| `--no-ch` | skip ClickHouse — the client is never even constructed |
| `--no-fuzzyclaw` | skip the task files |
| `--lines N` | `tail` scrollback depth (default 100) |

## 🔴 Read the exit code — the two zeroes are different facts

| code | meaning |
|---|---|
| `0` | ran, found windows (**including** a partial scan where one host was unreachable) |
| `2` | usage / malformed `<session>:<window>` target / **`tail`: the host answered and there is no such window** |
| `3` | every requested host answered and the answer is a **real zero** (for `tail`: the window exists and its scrollback is empty) |
| `4` | **no** host could be reached — the zero is unmeasured, not measured |

🔴 For `tail`, "no such window" is **exit 2**, not 4. The host answered; calling it
unreachable states a false fact and sends you to debug SSH over a typo. `tail`'s JSON
carries `reachable` *and* `found` — `found: null` means the host never answered, so it has
said nothing about whether the target exists.

`--host` defaults to `all`, which is meaningless for a command targeting one window, so
`tail` resolves it to the **local** host. That default is recorded, not silent:
`host_defaulted: true` in the JSON, and the not-found message names the host searched and
how to search the other one.

Same discipline inside the payload. `hosts.<name>.reachable` + `.error` are always present;
`clickhouse.status` is one of `ok` / `unreachable` / `query_error` / `unavailable` /
`skipped`, so `rows: []` is only believable when it is `ok`; `fuzzyclaw` reports
`files_seen` / `files_live` / `files_unparseable`, so "no live tasks" is distinguishable
from "no task files". Never read a bare count without its status.

## Where everything lives

| path | what |
|---|---|
| `scripts/session-manager` | the script |
| `scripts/tests/test_session_manager.py` | the hermetic suite (mocks tmux, SSH, CH, FS) |
| `scripts/tmux-scratch-slots.sh` | codename table (`~/.config/tmux/scratch-slots.sh` deployed) |
| `scripts/validation/chquery.py` | shared CH client — a LIBRARY, imported by `sys.path` insert |
| `~/.config/activity-collector/env` | CH endpoint + creds (never hardcoded) |
| `~/.tmux/tasks/*.json` | fuzzyclaw task files |

Details: `reference/clickhouse-queries.md`, `reference/cross-host.md`.

## 🔴 fuzzyclaw is only usable intersected with LIVE windows — and the guard pins a RELATIONSHIP

`CLAUDE.md` marks `~/.tmux/tasks/*.json` UNTRUSTED. Measured on the workbench 2026-08-11:
**400 files, 44 live windows, 357 stale (89%), 11 slot-mismatched, 32 live, 0 unparseable.**
The failure mode is staleness, not corruption — so it is filterable, and
`filter_live_tasks()` does the filtering against
`tmux list-windows -a -F '#{window_id}|#{window_index}|#{session_name}'`.

A task file survives only when **both** hold:

```
window_id is live   AND   that live window's real (session, index) == the file's
```

🔴 **Existence alone is not enough, and checking only existence was a real defect here.** An
earlier revision keyed the guard on `window_id` but joined the task onto a pane row by
`(tmux_session, window_index)` — two independent facts, never checked against each other.
`renumber-windows` is `on`, so indexes shift under live windows: of the 43 files that passed
the id-only guard, only **32** still sat in the slot they recorded, **7** named a slot now
held by a *different* live window, and **5** slots were claimed by more than one survivor
(silently last-wins). Two rendered rows carried another window's `claude_session_id` — the
one carrier of the session id into ClickHouse — so a `detail` would have pulled a stranger's
prompt history.

Consequences, all pinned by tests:

- rejections are counted **separately**: `files_stale` (the window is gone) vs
  `files_mismatched` (alive, but somewhere else now). Collapsing them hides a renumber storm.
- a slot two files both claim resolves to **nothing**. `index_tasks_by_window()` drops it and
  reports it under `fuzzyclaw.slot_conflicts`; attaching an arbitrary one of two
  contradictory records is worse than attaching none, because it reads as measured data.
- every row carries `window_id`, so the join is auditable in the output:
  `row.window_id == row.fuzzyclaw.window_id` is an invariant for every joined row.
- `filter_live_tasks()` **rejects a bare set of ids with a `TypeError`** rather than
  degrading to the old existence-only check.

Same precedent and rationale as `scripts/tmux-scratch-status.sh:28-34`. If you add a second
consumer of these files, intersect there too — do not copy the fields out raw.

The task-file key set consumed is pinned as a **field ledger** (`FUZZYCLAW_FIELDS`, 11 keys
including `transcript_path`, which the original spec omitted). It fails the suite when the
set grows *or* shrinks.

## 🔴 The third zero: `fuzzyclaw.status`

The live-window set is **measured or `None`**, never a fabricated empty set. When it was not
measured, `fuzzyclaw.status` is `"unmeasured"` and `files_live` is `null` — *not* `0`, and
never `"ok"`. Two ways to get there, both of which used to report
`files_seen: 400, files_live: 0, status: "ok"`:

| cause | what you see |
|---|---|
| `--host laptop` — the local host is never scanned, so its windows are never listed | `fuzzyclaw.error` names the unscanned local host |
| `list-panes` succeeded but `list-windows` failed | `hosts.<n>.windows_measured: false` + `windows_error`; `live_window_ids: null` |

`hosts.<n>.reachable`/`.error` describe the **`list-panes`** call; `.windows_measured`/
`.windows_error` describe the **`list-windows`** call. They are independent measurements —
one succeeding says nothing about the other.

## Honest limits — do not describe these as working

- Claude detection is `pane_current_command =~ /claude/`, **shallower** than agent-ops'
  `/proc` descendant walk. `/proc` is not reachable over SSH, so a deeper local check would
  make the two hosts report by different rules.
- fuzzyclaw is read on the **local host only** (the files are local state). A remote row
  therefore has `fuzzyclaw: null`, `claude_session_id: null` and `age_secs: null`, and is
  classified busy/idle from the title glyph alone — it is never labelled `stale`, because
  no age was measured.
- `tail --stream` is **not implemented** — one-shot `capture-pane` only.
- `signal` / `kill` are **not implemented**, deliberately. This tool never writes to,
  signals, or kills a window. Adding a destructive verb needs its own PR and its own guards.

## Gotchas

- Both hosts report `hostname` as `nixos`. The local host label comes from `ACTIVITY_HOST`
  (env, then the collector env file), defaulting to `workbench`.
- `tmux` exiting non-zero with *"no server running"* is a **reachable** host with zero
  windows, not an unreachable one. The script already separates them; keep it that way.
- zsh reserves `status` — use `rc=`/`out=`. Use `git -C <path>`, never `cd <repo> &&`.
- Merged ≠ deployed: this file only reaches `~/.claude/skills/` on a `home-manager switch`
  / `ship.sh`. The script itself runs straight from the repo checkout.
