# Proposal: session-manager

**Status:** draft — 3 factual errors corrected, 2 decisions still open (see §6)
**Date:** 2026-08-11
**Author:** Claude Code (opencode session). Corrections + §6 by Claude Code (review session).

> **Provenance note.** This was marked `approved` with all of §5 "resolved" at
> 2026-08-11 00:59 by the authoring session. No operator decision is on record for
> those seven answers, and three of them were wrong against the live system (§6.1).
> Reset to `draft` until §6.3's two open decisions are made.

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

1. **tmux sessions** — local: `tmux list-panes -a -F <format>`. Laptop: `ssh zach@10.42.0.100 "tmux list-panes -a -F <format>"`. Same pipe-delimited format as agent-ops: `pane_id|pane_pid|session_name|window_index|window_name|pane_current_path|pane_current_command|pane_title`.

2. **Scratch codenames** — parse `tmux-scratch-slots.sh` (same regex as agent-ops: `_SLOT_RE`). Map session name → codename, color, hotkey.

3. **Claude Code detection** — same process-tree walk as agent-ops: `/proc/<pid>/stat` → BFS descendants → match `comm` against `/claude/`. Extract task from pane_title (strip status glyph).

4. **fuzzyclaw tasks** — read `~/.tmux/tasks/*.json` (the format from `tmux-task-hook.sh`: `{task, window_id, tmux_session, window_index, status, cwd, claude_session, started, last_activity, summary}`). Merge with tmux state by window_id.

   🔴 **`CLAUDE.md` marks fuzzyclaw UNTRUSTED as a data source.** This proposal adopted it
   without acknowledging that, and it is not a cosmetic problem: `claude_session` is the
   *only* key linking a tmux pane to a ClickHouse session, so the untrusted source is
   load-bearing for the headline feature. See §6.2 — decide this before building.

5. **ClickHouse activity** — use `chquery.py` (shared client) with the collector's env file (`~/.config/activity-collector/env`). **Workbench endpoint only** (`http://192.168.50.94:30123`) — both hosts send to the same homelab CH pod, so one endpoint has the full dataset. Two queries:
   - **Recent Claude/OpenCode sessions** — verified against the live table 2026-08-11 (returns 20 rows, non-empty `first_msg`):

     ```sql
     SELECT session,
            argMax(project, ingested_at)          AS project,
            argMinIf(text, ts, kind = 'prompt')   AS first_msg,
            max(ts)                               AS last_seen
     FROM activity.events
     WHERE source IN ('claude', 'opencode')
       AND ts > now() - INTERVAL 1 DAY
     GROUP BY session
     ORDER BY last_seen DESC
     LIMIT 20
     ```

     There is **no `first_message` column** — the original draft's query fails outright with
     `Code: 47 … UNKNOWN_IDENTIFIER`. The real columns are `ts, host, source, kind, project,
     cwd, session, app, text, duration_ms, exit_code, payload, ingested_at`; the first prompt
     is reconstructed with `argMinIf(text, ts, kind = 'prompt')` (`kind='prompt'` is present
     for both `claude` and `opencode`). The stray `DISTINCT` was redundant with `GROUP BY`.
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
5. **Cross-host SSH** — how it reaches the laptop (nebula `zach@10.42.0.100`; the LAN address `192.168.50.155` works only when both hosts are on the same network)
6. **ClickHouse queries** — the two SQL patterns, how to modify them
7. **Gotchas** — both hosts are `nixos`, use `ACTIVITY_HOST` to disambiguate; zsh reserves `status`; never `cd`

**reference/ subdirectory:**
- `reference/clickhouse-queries.md` — the SQL patterns, how to add new ones
- `reference/cross-host.md` — SSH setup, LAN + nebula paths

### 2.3 Slash command: `claude/commands/sessions.md`

> 🔴 **Superseded — do NOT create this file.** PR #377 (open, 56 files) migrates all 17
> slash-commands to skills and cuts the listing budget 21%. A new `claude/commands/*.md`
> lands on the wrong side of that migration the day it merges. The `/sessions` entry point
> must be the **skill** in §2.2 instead, whose front-matter `description` is what the model
> matches on. Keep the description tight — it is charged to the per-session listing budget
> that #377 exists to reduce. The block below is retained only to show what was intended.

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

`claude/skills/standup/standup.sh` (not `scripts/standup.sh` — it lives under the skill) already does cross-host SSH for repo state. session-manager could provide a `--standup` mode that returns just the session summary line for inclusion in the standup STATUS line (e.g., `Sessions: 8 active (3 busy, 2 stale, 2 waiting)`).

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

- Add laptop SSH fanout: **nebula `zach@10.42.0.100`**, matching `claude/skills/standup/standup.sh:26` (`LAP="zach@10.42.0.100"`), the repo's only existing precedent. The LAN address `192.168.50.155` is same-network-only, so LAN-first buys a timeout on every scan of a laptop that usually is not there.
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

## 5. Open questions

These were marked "(resolved)" by the authoring session with no operator decision on record.
Status after review:

| # | Question | Answer as written | Review |
|---|---|---|---|
| 1 | agent-ops scope | Option A — replaces inline tmux scan | ⚠️ **Contested** — see §6.3 |
| 2 | Laptop SSH | LAN first, fallback `10.42.0.10` | ❌ **Was wrong** — `10.42.0.10` is the homelab gateway. Corrected to nebula `10.42.0.100` |
| 3 | ClickHouse | Workbench endpoint only | ✅ Correct — both hosts ship to the same homelab pod |
| 4 | tail mode | one-shot + `--stream` | ✅ Fine |
| 5 | kill safety | require `--yes` | ✅ Fine |
| 6 | Bar pill | session count via bar-status-poll | ⚠️ **Deferred** — see §6.3 |
| 7 | Naming | `session-manager` | ✅ Fine |

---

## 6. Review (2026-08-11)

Every claim below was checked against the working tree and the live ClickHouse table, not
against recollection.

### 6.1 Factual errors found and corrected

| # | Claim as written | Reality | Evidence |
|---|---|---|---|
| 1 | Laptop nebula fallback is `10.42.0.10` (4 places) | That is the **homelab nebula gateway**. The laptop is `10.42.0.100` | `claude/skills/standup/standup.sh:26`; `claude/skills/mailbox/SKILL.md:19`; `claude/skills/clawgate/reference/hooks.md:6` |
| 2 | `activity.events` has a `first_message` column | It does not — 13 columns, none named that | `system.columns` query returned 0; the draft's query fails `Code: 47 UNKNOWN_IDENTIFIER` |
| 3 | `scripts/standup.sh` | Lives at `claude/skills/standup/standup.sh` | `ls scripts/` — absent |

Error 1 is the dangerous one: it would not fail loudly. SSH would succeed against a real host
and report the gateway's tmux state as the laptop's.

### 6.2 The design gap — the join key comes from the untrusted source

`CLAUDE.md` states: *"⚠ fuzzyclaw (`~/.tmux/tasks/*.json`) is UNTRUSTED as a data source."*

This matters beyond hygiene. `agent-ops` detects **that** Claude runs in a pane (a `/proc`
BFS in `classify_claude_sessions()`, `scripts/agent-ops:521`) but never learns **which**
session it is — there is no `claude_session`/`session_id` anywhere in its 1409 lines. The only
carrier of that key is the fuzzyclaw task file. So the `claude_session_id` field in §2.1's
sample JSON, and the entire tmux-pane ↔ ClickHouse correlation the proposal is built to
deliver, rest on the disqualified source.

Without fuzzyclaw this degrades to "cross-host `tmux list-panes` beside an unjoined list of
recent CH sessions" — still useful, but not the stated goal.

#### RESOLVED (2026-08-11) — use it, but only intersected with live windows

Measured rather than assumed. Three findings close this:

1. **The failure mode is staleness, not corruption.** Of the 400 files in `~/.tmux/tasks/`,
   **43 point at a currently-live tmux window and 357 do not** — 89% stale. Zero were
   unparseable and zero lacked a `window_id`. So the data is well-formed; it is simply
   *old*. That is a filterable defect, not an unusable source.
2. **The join is structurally sound.** `activity.events.session` for `source='claude'` is
   36 chars in 100% of rows (1107/1107 over 2 days), and fuzzyclaw's `claude_session` is a
   36-char UUID. `fuzzyclaw.claude_session → activity.events.session` is a valid join.
3. **The repo already established the mitigation.** `scripts/tmux-scratch-status.sh:28-34`
   reads the same task files and intersects them against `tmux list-windows -a -F
   '#{window_id}'`, with a header comment saying exactly why: *"filtered against currently
   existing tmux windows so stale entries don't trigger."*

**Therefore:** consume fuzzyclaw, but *only* after intersecting `window_id` with live tmux
windows, following the `tmux-scratch-status.sh` precedent. No new first-class column in
`activity.events` is needed, and the correlation feature survives intact.

🔴 **The intersection is a load-bearing guard, not a filter** — without it 89% of rows are
lies. It gets a mutation test (§7 test 8): delete the intersection and a test must go red
with *that* guard's specific failure.

Note also that the field list in §2.1 source 4 is incomplete — the task files also carry
`transcript_path`.

### 6.3 Scope

Phases 1–2 and 4 are the value: a cross-host, JSON-emitting session view that does not exist
today. Dependencies are real — `agent-ops`, `tmux-scratch-slots.sh`, `validation/chquery.py`,
`bar-status-poll` and `session-analysis/initiative-scan.py` all exist, every function named in
§3.1 exists at the implied line, and "no new dependencies" is true.

Phases 5–6 should wait:

- **Phase 5** rewrites a working 1409-line dashboard covered by 2 test files, to remove an
  `O(N-procs)` scan from a 4-second refresh loop **that nobody has measured**. Measure first;
  if the refresh is not actually hurting, this is risk with no payoff.
- **Phase 6** adds a bar pill for a number the popup already shows.

**Recommendation: build Phases 1–2 + 4. Defer 5 and 6 pending a measurement.**
