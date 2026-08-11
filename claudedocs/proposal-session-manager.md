# Proposal: session-manager

**Status:** approved
**Date:** 2026-08-11
**Author:** Claude Code (opencode session)

---

## 1. Problem

Right now, "what's happening across my two hosts" requires stitching together:

- `agent-ops` (tmux popup, local-only, live tmux scan, no JSON API)
- `tmux list-panes -a` (manual, per-host)
- ClickHouse queries (manual `curl` with reader creds)
- `fuzzyclaw` task files (per-window JSON, no cross-host aggregation)
- `standup.sh` (fleet status but focuses on PRs/deployments/alerts, not session detail)

There's no single script that answers: "show me every active tmux session on both hosts, which ones have Claude Code running, what they're doing, and what ClickHouse says about recent agent activity."

## 2. Design

### 2.1 Script: `scripts/session-manager`

A Python 3 script following the agent-ops pattern (same palette, same cache/TTL architecture, same fail-safe degradation).

**Location:** `scripts/session-manager` (executable, like `agent-ops`)

#### Subcommands

| Command | Purpose | Output |
|---|---|---|
| `scan` (default) | Full cross-host tmux + activity scan | JSON or table |
| `list` | List tmux sessions (all hosts, no ClickHouse) | JSON or table |
| `detail <session>:<window>` | Deep view of one window (proc tree, task file, CH activity) | JSON or table |
| `tail <session>:<window>` | One-shot `capture-pane` dump (default) or `--stream` for continuous feed | Text |
| `signal <session>:<window> <signal>` | Send signal to a window's pane process | Confirmation |
| `kill <session>:<window> <signal>` | Kill a stale window (requires `--yes`; SIGTERM → 5s → SIGKILL) | Confirmation |

#### Flags

| Flag | Effect |
|---|---|
| `--json` | JSON output (default is human-readable table) |
| `--host <name>` | Filter to one host (`workbench`, `laptop`, or `all` — default `all`) |
| `--include-attached` | Include attached sessions (default: detached only, since attached = you're in it) |
| `--stale-threshold <secs>` | Window age above which it's flagged "stale" (default: 3600 = 1h) |
| `--no-ch` | Skip ClickHouse queries (faster, offline) |
| `--no-fuzzyclaw` | Skip fuzzyclaw task files |
| `--refresh <secs>` | Auto-refresh interval for `scan` (default: 0 = one-shot) |
| `--once` | Single frame, then exit (for scripting / agent-ops integration) |
| `--stream` | Continuous `tail` mode (requires active tmux connection) |
| `--yes` | Confirm destructive actions (`kill`) |

#### Data sources (per host)

1. **tmux sessions** — local: `tmux list-panes -a -F <format>`. Laptop: `ssh zach@192.168.50.155 "tmux list-panes -a -F <format>"` (fallback: `ssh zach@10.42.0.10`). Same pipe-delimited format as agent-ops: `pane_id|pane_pid|session_name|window_index|window_name|pane_current_path|pane_current_command|pane_title`.

2. **Scratch codenames** — parse `tmux-scratch-slots.sh` (same regex as agent-ops: `_SLOT_RE`). Map session name → codename, color, hotkey.

3. **Claude Code detection** — same process-tree walk as agent-ops: `/proc/<pid>/stat` → BFS descendants → match `comm` against `/claude/`. Extract task from pane_title (strip status glyph).

4. **fuzzyclaw tasks** — read `~/.tmux/tasks/*.json` (the format from `tmux-task-hook.sh`: `{task, window_id, tmux_session, window_index, status, cwd, claude_session, started, last_activity, summary}`). Merge with tmux state by window_id.

5. **ClickHouse activity** — use `chquery.py` (shared client) with the collector's env file (`~/.config/activity-collector/env`). **Workbench endpoint only** (`http://192.168.50.94:30123`) — both hosts send to the same homelab CH pod, so one endpoint has the full dataset. Two queries:
   - **Recent Claude/OpenCode sessions:** `SELECT DISTINCT session, argMax(project, ingested_at) AS project, argMax(first_message, ingested_at) AS first_msg, max(ts) AS last_seen FROM activity.events WHERE source IN ('claude', 'opencode') AND ts > now() - INTERVAL 1 DAY GROUP BY session ORDER BY last_seen DESC LIMIT 20`
   - **Per-session prompt history:** `SELECT ts, kind, left(text, 200) AS snippet FROM activity.events WHERE session = '{session_id}' AND ts > now() - INTERVAL 1 DAY ORDER BY ts DESC LIMIT 10`

6. **Telemetry freshness** — same `dateDiff` aggregate as agent-ops (per-source seconds-since-last).

#### Output format (table mode)

```
CROSS-HOST SESSION MANAGER                    workbench + laptop                 2026-08-11 14:32 UTC

▸ ACTIVE TMUX SESSIONS (8)

  HOST       SESSION       WINDOW  CODENAME   TASK                          STATUS    AGE
  ─────────  ────────────  ──────  ─────────  ────────────────────────────  ────────  ────
  workbench  scratch       1       grove      Investigate 500s              ▶ busy    23m
  workbench  scratch       2       Gold       —                             ● idle    1h
  workbench  scratch4      1       Vapor      Refactor dl-router            ▶ busy    2h
  workbench  homelab       3       —          Flux reconcile                · stale   4h
  laptop     scratch       1       grove      Fix mail-forward hook         ● idle    12m
  laptop     naida-dev     1       —          Build failing                 ▶ busy    45m

▸ RECENT AGENT ACTIVITY (ClickHouse, last 24h)

  SESSION          PROJECT        LAST SEEN  PROMPT SNIPPET
  ───────────────  ─────────────  ─────────  ──────────────────────────
  ses_abc123       devrc          12m ago    "Add session-manager skill"
  ses_def456       homelab-talos  1h ago     "Bump cilium version"

▸ FUZZYCLAW TASKS (2 waiting)

  WINDOW     SESSION       TASK                      STATUS   LAST ACTIVITY
  ─────────  ────────────  ────────────────────────  ───────  ─────────────
  @5         scratch4      Fix ship.sh laptop skip   paused   2h ago
  @12        scratch       Write proposal             paused   30m ago

▸ TELEMETRY FRESHNESS

  zsh 1m  tmux 1m  keys 3m  i3 —  browser 5m  claude 12m
```

#### Output format (JSON mode)

```json
{
  "ts": "2026-08-11T14:32:00Z",
  "hosts": {
    "workbench": {
      "sessions": [
        {
          "session": "scratch",
          "window_index": 1,
          "window_name": "grove",
          "codename": "grove",
          "color": "#b8bb26",
          "hotkey": "g",
          "path": "/home/zach/workspace/devrc",
          "task": "Investigate 500s",
          "busy": true,
          "age_secs": 1380,
          "claude_session_id": "ses_abc123",
          "fuzzyclaw": null,
          "ch_activity": {
            "project": "devrc",
            "last_seen": "2026-08-11T14:20:00Z",
            "recent_prompts": ["Add session-manager skill"]
          }
        }
      ],
      "telemetry_freshness": {"zsh": 60, "tmux": 60, "claude": 720}
    },
    "laptop": {
      "sessions": [...],
      "telemetry_freshness": null
    }
  },
  "summary": {
    "total_sessions": 8,
    "busy": 3,
    "idle": 3,
    "stale": 2,
    "fuzzyclaw_waiting": 2
  }
}
```

### 2.2 Skill: `claude/skills/session-manager/SKILL.md`

```yaml
---
name: session-manager
description: Live dashboard of all active tmux sessions, Claude Code activity, and OpenCode work across both hosts (workbench + laptop). Query sessions, tail windows, kill stale agents, check ClickHouse activity. Use when asked about "active sessions", "what's running", "tmux state", "cross-host status", "session manager", or to tail/inspect a specific tmux window.
---
```

**SKILL.md body structure** (following the bar/activity skill pattern):

1. **Architecture** — why it exists (cross-host tmux + CH unified view), relationship to agent-ops (this is the queryable/programmatic counterpart; agent-ops is the always-on popup)
2. **Where everything lives** — table:
   - `scripts/session-manager` — the script
   - `scripts/tmux-scratch-slots.sh` — slot table
   - `scripts/validation/chquery.py` — ClickHouse client
   - `~/.config/activity-collector/env` — ClickHouse creds
   - `~/.tmux/tasks/*.json` — fuzzyclaw state
3. **Subcommands** — each with example invocations
4. **Integration with agent-ops** — session-manager writes a cache file (1-2s TTL); agent-ops reads it (zero-cost on 4s refresh, replaces the O(N-procs) inline scan)
5. **Cross-host SSH** — how it reaches the laptop (LAN first `192.168.50.155`, fallback nebula `10.42.0.10`)
6. **ClickHouse queries** — the two SQL patterns, how to modify them
7. **Gotchas** — both hosts are `nixos`, use `ACTIVITY_HOST` to disambiguate; zsh reserves `status`; never `cd`

**reference/ subdirectory:**
- `reference/clickhouse-queries.md` — the SQL patterns, how to add new ones
- `reference/cross-host.md` — SSH setup, LAN + nebula paths

### 2.3 Slash command: `claude/commands/sessions.md`

```yaml
---
name: sessions
description: "Show live cross-host tmux sessions + Claude Code activity. Arg: [scan|list|detail <s>:<w>|tail <s>:<w>|kill <s>:<w>] — default: scan."
argument-hint: "[scan|list|detail <session>:<window>|tail <session>:<window>|kill <session>:<window>]"
allowed-tools: Bash, Read
---
```

Body: delegates to `python3 /home/zach/workspace/devrc/scripts/session-manager $ARGUMENTS`.

### 2.4 opencode skill

The same `claude/skills/session-manager/SKILL.md` is automatically symlinked to `~/.config/opencode/skills/session-manager/` via the existing `nix/home.nix` pattern. No separate opencode skill definition needed.

## 3. Integration points

### 3.1 agent-ops bar (Option A — refactor)

session-manager replaces agent-ops' inline tmux scan. The current pipeline in agent-ops:

1. `list_tmux_panes_raw()` — subprocess: `tmux list-panes -a`
2. `build_proc_index()` — O(N-procs) `/proc` scan (most expensive)
3. `own_pid_chain()` — self-exclusion
4. `classify_claude_sessions()` — BFS + regex + git root walk
5. `load_scratch_codenames()` — file read (cheap)
6. `render_active_runs()` — formatting

After refactor:
- session-manager runs its own polling loop (1-2s TTL), writes cache file
- agent-ops reads the cache file (zero-cost on 4s refresh, like PRs/initiatives)
- session-manager handles self-exclusion (its own PID chain)
- agent-ops keeps `render_active_runs()` and `load_scratch_codenames()` (or moves codename resolution to session-manager)
- **Net improvement:** removes the only O(N-procs) operation from the 4s refresh path

### 3.2 /sessions slash command

Already described above. Delegates to the script.

### 3.3 initiative-scan integration

`initiative-scan.py` already parses scratch-slots and tracks sessions. session-manager could feed its session data into initiative-scan's "active agent runs" section, or initiative-scan could call `session-manager --json --host workbench --no-ch` to get the local session list instead of re-implementing the tmux scan.

### 3.4 standup.sh

`standup.sh` already does cross-host SSH for repo state. session-manager could provide a `--standup` mode that returns just the session summary line for inclusion in the standup STATUS line (e.g., `Sessions: 8 active (3 busy, 2 stale, 2 waiting)`).

### 3.5 bar-status-poll (session count pill)

`bar-status-poll` queries `session-manager --json --once --no-ch --host workbench` to get a session count for a bar pill (like the existing `clawgate`/`mail` pills).

## 4. Implementation plan

### Phase 1: Core script (MVP)

**Files to create:**
| File | Purpose |
|---|---|
| `scripts/session-manager` | Main script (~400 lines, Python 3) |
| `scripts/tests/test_session_manager.py` | Unit tests (mock tmux, mock CH) |

**Dependencies (all already in the codebase):**
- `scripts/validation/chquery.py` — ClickHouse client
- `scripts/tmux-scratch-slots.sh` — slot table (parsed by Python regex, same as agent-ops)
- `~/.config/activity-collector/env` — ClickHouse creds
- Standard library only: `json`, `subprocess`, `os`, `re`, `sys`, `argparse`, `time`

**No new dependencies.**

### Phase 2: Cross-host SSH

- Add laptop SSH fanout (LAN first `192.168.50.155`, fallback nebula `10.42.0.10`)
- Handle both paths with timeout and fail-safe: if SSH fails, show laptop as "unreachable" (don't crash)

### Phase 3: Interactive features

- `tail` subcommand: one-shot `tmux capture-pane -t <session>:<window> -p -e -S -100` (default) or `--stream` for continuous `tmux pipe-pane`
- `signal` subcommand: `tmux send-keys -t <session>:<window> C-c` or `kill -TERM <pane_pid>`
- `kill` subcommand: requires `--yes` confirmation flag; SIGTERM → 5s → SIGKILL

### Phase 4: Skill + slash command

- `claude/skills/session-manager/SKILL.md` with reference docs
- `claude/commands/sessions.md` slash command
- Both auto-symlinked to opencode via nix/home.nix

### Phase 5: agent-ops refactor (Option A)

- session-manager writes cache file (1-2s TTL)
- agent-ops replaces `list_tmux_panes_raw()`, `build_proc_index()`, `own_pid_chain()`, `classify_claude_sessions()` with cache read
- agent-ops keeps `render_active_runs()` and `load_scratch_codenames()`
- Self-exclusion contract: session-manager excludes its own PID; agent-ops excludes session-manager's PID from its cache

### Phase 6: Bar pill integration

- `bar-status-poll` adds a session-count source
- Queries `session-manager --json --once --no-ch --host workbench`
- Renders as a bar pill (e.g., `⚡ 8` or `🖥 3busy/2stale`)

## 5. Open questions (resolved)

| # | Question | Resolution |
|---|---|---|
| 1 | agent-ops scope | Option A — session-manager replaces inline tmux scan |
| 2 | Laptop SSH | LAN first (`192.168.50.155`), fallback nebula (`10.42.0.10`) |
| 3 | ClickHouse | Workbench endpoint only — both hosts send to same homelab CH pod |
| 4 | tail mode | Both — one-shot (default) + `--stream` for continuous |
| 5 | kill safety | Require `--yes` confirmation flag |
| 6 | Bar pill | Session count in i3 bar via bar-status-poll |
| 7 | Naming | `session-manager` |
