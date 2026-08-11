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
python3 $DEVRC/scripts/session-manager detail scratch7:3     # one window
python3 $DEVRC/scripts/session-manager tail scratch7:3 --lines 200
```

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
| `2` | usage / malformed `<session>:<window>` target |
| `3` | every requested host answered and the answer is a **real zero** |
| `4` | **no** host could be reached — the zero is unmeasured, not measured |

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

## 🔴 fuzzyclaw is only usable intersected with LIVE windows

`CLAUDE.md` marks `~/.tmux/tasks/*.json` UNTRUSTED. Measured on the workbench 2026-08-11:
**400 files, 44 live windows, 43 intersect, 357 stale (89%), 0 unparseable.** The failure
mode is staleness, not corruption — so it is filterable, and `filter_live_tasks()` does the
filtering against `tmux list-windows -a -F '#{window_id}'`.

That intersection is a **load-bearing guard, not a cosmetic filter**: without it 89% of the
rows would describe windows that no longer exist. Deleting it turns
`test_task_file_pointing_at_a_DEAD_window_is_excluded` red with `['@41','@997'] == ['@41']`
— watched, not assumed — and
`test_the_intersection_is_REACHABLE_and_is_the_only_thing_excluding_it` pins in-suite that
no earlier check would have rejected the fixture anyway. Same precedent and rationale as
`scripts/tmux-scratch-status.sh:28-34`. If you add a second consumer of these files,
intersect there too — do not copy the fields out raw.

The task-file key set consumed is pinned as a **field ledger** (`FUZZYCLAW_FIELDS`, 11 keys
including `transcript_path`, which the original spec omitted). It fails the suite when the
set grows *or* shrinks.

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
