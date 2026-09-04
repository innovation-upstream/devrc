---
clawgate-task:
---
# Handoff: syshealth-skill — 2026-09-04

## Run this first — the index, one command
```bash
cairn recall --repo /home/zach/workspace/devrc
```
Terse pointers this doc does not carry, curated by past sessions and outliving it.
Non-blocking: if it exits non-zero, print the stderr line and carry on.

## Goal
Create a `syshealth` skill — an on-demand deep system inspection that automates the checks Zach just ran manually (zombie procs, mem/CPU hogs, runaways, load). Complements `cpu-monitor.sh` (always-on shallow alerts) with a one-shot deep sweep.

## State now
- Branch: `main` (on `main`, not a feature branch — no commits for this work yet)
- No PR, no commits — this is pre-implementation, design-only
- Nothing deployed, nothing verified

## Design (solidified this session)

### Script: `scripts/syshealth.sh` (~200-300 lines, bash)

**Flags:**
- `--json` — JSON output instead of tables
- `--cpu-threshold N` — CPU% for "hog" (default: 50)
- `--mem-threshold N` — RSS in GB for "hog" (default: 1)
- `--load-threshold N` — load avg threshold (default: nproc)
- `--kill PID` — SIGTERM a confirmed runaway (prompt first)
- `--disk` — include disk usage section
- `--systemd` — include failed systemd units
- `--no-zombies` / `--no-hogs` — skip sections

**Always-on sections:**
1. System overview — uptime, load averages, core count, RAM summary
2. Zombie processes — PID, age, command, parent PID + command, parent alive/dead
3. CPU hogs — processes above threshold, with age, command, process tree
4. Memory hogs — processes above threshold, with RSS, age, command, process tree
5. Runaway candidates — processes >10min at >80% CPU

**Optional sections:**
6. Disk — `df -h` filtered, alert on >80%
7. Systemd — failed user units

**Exit codes:** 0=clean, 1=warnings, 2=critical (runaway >200% CPU for >30min)

### Skill: `claude/skills/syshealth/SKILL.md`

YAML frontmatter with `name: syshealth`, description routing on "check procs", "zombie", "cpu hog", "mem hog", "runaway", "system health". Body: goal, steps (parse flags, run script, interpret output), reference to script.

### Tests: `scripts/tests/test_syshealth.py` (~300-400 lines)

Following established patterns (subprocess + stubs, control pairs):

| Test | Pattern |
|---|---|
| `test_exit_0_on_clean_system` | Stub ps/top/loadavg → exit 0, no WARNING |
| `test_exit_1_on_zombies` | Stub ps with zombie → exit 1, zombie section non-empty, parent tracked |
| `test_exit_1_on_cpu_hog` | Stub ps >threshold → exit 1 |
| `test_exit_1_on_mem_hog` | Stub ps high RSS → exit 1 |
| `test_exit_2_on_runaway` | Stub ps >200% CPU + old etime → exit 2 |
| `test_zombie_parent_tracking` | Zombie parent resolved to command, alive/dead shown |
| `test_process_tree_grouping` | Workers grouped under parent |
| `test_json_output_valid_schema` | `--json` valid JSON with expected keys |
| `test_json_exit_codes_match` | JSON exit_code matches process exit |
| `test_custom_thresholds` | Parametrized threshold tests |
| `test_ignore_list` | Expected heavy hitters excluded |
| `test_disk_section_disabled_by_default` | Negative control |
| `test_disk_section_shows_usage` | Stub df |
| `test_systemd_section` | Stub systemctl --failed |
| `test_control_clean_vs_dirty_must_differ` | Standup-style control pair |

Fixtures: `testlib/mockbin.write_exec()` for stubs, `tmp_path` for temp artifacts. No real process inspection.

## Key decisions
- **Complement, not duplicate** — `cpu-monitor.sh` stays the always-on daemon; syshealth is the on-demand deep sweep
- **Report-only by default** — `--kill` for explicit user action, never auto-kill
- **No bar pill** — scope control; separate project if needed later
- **No dunst integration** — cpu-monitor owns notifications
- **No nix store detection** — drift-check owns that
- **Zombie parent tracking** — key differentiator from raw `ps aux | awk '/Z/'`
- **Process tree grouping** — reduces noise, shows real culprit (e.g., pytest workers under parent)
- **Thresholds configurable via flags** — adapts to different machines

## Open questions (for next session)
1. Should `--kill` prompt for confirmation (y/n) or just send SIGTERM?
2. Should the ignore list be configurable via flag, or hardcoded like cpu-monitor's?
3. Any missing sections? (open FDs, network connections, swap usage)
4. Should the skill auto-fire from description ("check procs") or only on explicit `/syshealth`?

## What needs to happen next (ranked)
1. Create `scripts/syshealth.sh` — the main script implementing the design above
2. Create `claude/skills/syshealth/SKILL.md` — skill definition with YAML frontmatter
3. Create `scripts/tests/test_syshealth.py` — full test suite per the table above
4. Run `scripts/gate.sh` to verify tests pass and no regressions
5. `git add` the new files (skill won't deploy without it)

## Gotchas / constraints
- `cpu-monitor.sh` already handles always-on monitoring — don't duplicate that layer
- The `testlib/mockbin.py` stubs use `#!/bin/sh` (NOT `#!/usr/bin/env bash`) — the sandbox has no `/usr/bin/env`
- Existing test pattern: extract script prelude (everything before `while :;`) and wrap in test harness for function-level testing
- The skill must be added to `claude/skill-tiers.json` (tier B initially, since it's explicit-invocation not auto-fire)
- The skill description must fit the 1,536-char per-entry cap and the total listing budget

## How to verify
- `nix develop ~/workspace/devrc -c python3 -m pytest scripts/tests/test_syshealth.py -q` — all tests pass
- `scripts/gate.sh` — full gate green on both tiers
- Manual: `bash scripts/syshealth.sh` produces readable output on a live system
- Manual: `bash scripts/syshealth.sh --json` produces valid JSON
