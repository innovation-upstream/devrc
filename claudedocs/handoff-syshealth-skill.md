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
- Branch `feat/syshealth` in worktree `/home/zach/workspace/devrc-syshealth`, cut from
  `origin/main` at `56c68cc7`. **Nothing committed yet** — all 9 files are STAGED only.
- 🔴 The branch is **behind origin/main by 2** already. The base MOVED under the gate run,
  so the merged-tree claim must be re-made against current `origin/main` before merging.
- No PR yet. Nothing deployed; `home-manager switch` NOT run, so `~/.claude/skills/syshealth`
  does not exist on either host.
- Work claim held: `claim-work syshealth-skill-1` (release it when this lands or is abandoned:
  `claim-work --release syshealth-skill-1`).
- **No `clawgate-task:` field**: `clawgate_handoff.sh resolve` exited **5** — 0 tasks for this
  session, with its positive control confirming the board was genuinely read. Per the skill
  that is NOT a clean bill of health (a wrong session id also answers 200 + empty array), so
  no field was written and no task was created.

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
1. **Finish/re-run `scripts/gate.sh --tier both` inside `nix develop`** and read the
   per-tier `RESULT:` lines, not the exit code.
2. **Run the nix check tier, one derivation at a time**:
   `nix build .#checks.x86_64-linux.pytests` then `.#checks.x86_64-linux.nodetests`.
3. **Rebase onto current `origin/main`** (already 2 behind) and re-run both tiers on the
   MERGED tree — the base moved during the run above.
4. Commit the 9 staged files and open the PR.
5. After merge: `scripts/ship.sh`, then confirm `~/.claude/skills/syshealth/SKILL.md`
   resolves on both hosts (`readlink -f`) — merged ≠ deployed.
6. `claim-work --release syshealth-skill-1`.

## Gotchas / constraints
- `cpu-monitor.sh` already handles always-on monitoring — don't duplicate that layer
- The `testlib/mockbin.py` stubs use `#!/bin/sh` (NOT `#!/usr/bin/env bash`) — the sandbox has no `/usr/bin/env`
- Existing test pattern: extract script prelude (everything before `while :;`) and wrap in test harness for function-level testing
- The skill must be added to `claude/skill-tiers.json` (tier B initially, since it's explicit-invocation not auto-fire)
- The skill description must fit the 1,536-char per-entry cap and the total listing budget

## How to verify
- `nix develop /home/zach/workspace/devrc-syshealth --command bash scripts/gate.sh --tier both`
- `nix build .#checks.x86_64-linux.pytests` / `.#checks.x86_64-linux.nodetests` — SEPARATELY
- `python3 scripts/syshealth` → exit 1 on this box today (swap ~57%, 21 zombies, 2 mem hogs)
- `python3 scripts/syshealth --json | python3 -c "import json,sys; json.load(sys.stdin)"`
- `python3 scripts/syshealth --systemd --fds` → the FD section must print `unreadable` beside
  `examined`; a bare "0 dangling"-style zero there is the failure, not the all-clear.
## What was built (design-only handoff is now superseded)
Four design questions the previous session left open were answered by the operator:
**Python, not bash** · **no `--kill` in v1** · **swap/OOM always-on** · **`--systemd` + `--fds`
opt-in, `--disk` dropped**.

- `scripts/syshealth` — Python, stdlib only, report-only. Sections: overview (load, mem, swap,
  OOM headroom), zombies, CPU hogs, mem hogs, runaways; `--systemd` / `--fds` opt-in.
  Exit `0` clean · `1` warnings · `2` critical · `3` could-not-measure.
- `claude/skills/syshealth/SKILL.md` — tier **B** in `claude/skill-tiers.json`.
- `scripts/tests/test_syshealth.py` — **98 tests, all green**.

### 🔴 The two design facts worth not re-deriving
1. **`ps` %CPU is cpu-time ÷ elapsed over the process's WHOLE LIFE, not an instant sample.**
   That is why `--min-age` (default 10s) exists and why this is not four `ps` one-liners.
   The 2026-09-03 manual sweep in this doc's own history reported a *"runaway nix store scan
   at 240%"* that was a **20-millisecond `pgrep`**, and a 1100% row that **was the `ps`
   producing the report**. The script excludes itself, its `ps` child and its ancestors.
2. **A persistent zombie always indicts a LIVE parent.** A dead parent's zombies get
   reparented to init and reaped, so a zombie that survives for days proves its parent is
   alive and not calling `wait()`. The previous session concluded the opposite ("children of
   already-dead services") — that is RETRACTED.
   Measured on the workbench 2026-09-04: **21 zombies under 2 parents**, 18 of them under
   **pid 2273825 `sleep infinity`**, which is a Kubernetes container's PID 1
   (`/proc/2273825/cgroup` → `kubepods/besteffort/pod…`). Container PID 1 is not a subreaper
   and never reaps. The other 3 sit under pid 3716907 `stash`. **This is a real, still-open
   finding about the box — it is not fixed, and syshealth only reports it.**

## Verification state — READ THIS BEFORE CLAIMING ANYTHING
- ✅ 98/98 unit + end-to-end tests green (`nix develop … -c python3 -m pytest
  scripts/tests/test_syshealth.py`).
- ✅ **Mutation sweep 22/22**: positive control green (unmutated → 98 passed) and 21 mutants
  each killed by their OWN named test, run under `PYTHONDONTWRITEBYTECODE=1` in a fresh temp
  copy per mutant. Harness: `/tmp/claude-1000/-home-zach-workspace-devrc/
  07d75e8f-3dc4-41a7-8a14-5602fce01709/scratchpad/mutate.py` (scratch — **re-create it if you
  need it again; it is not in the repo**).
  The sweep found 3 REAL test gaps that a fully green suite had hidden, all now closed:
  a substring-vs-prefix `is_zombie` mutant; a `group_by_parent` boundary with no fixture
  landing exactly ON `GROUP_MIN_CHILDREN`; and a zombie-group "oldest age" assertion that was
  vacuous because `find_zombies` already returns oldest-first (fixed by grouping a REVERSED
  list, with an assert that the fixture still exercises the ordering).
- ⏳ **`scripts/gate.sh --tier both` was STILL RUNNING when this was written.** Not verified.
  First attempt exited 3 — `logrotate` missing from PATH because it ran outside the dev shell;
  that is the guard working (it refuses to run a suite that would silently skip tests), NOT a
  code failure. Re-run: `nix develop <worktree> --command bash scripts/gate.sh --tier both`.
- ❌ **The `nix` check tier has NOT been run at all.** It is the tier Tekton gates on and it
  is structurally different (builds from a `cp -r` store copy with no `.git`).
  🔴 Run the two derivations **ONE AT A TIME** — a combined invocation produces false failures.

## The listing-budget work (unplanned, and it is the bigger change)
Adding any skill reddened `test_skill_descriptions.py`: the listing ratchet was at
12,861/12,929, i.e. **68 chars of headroom**, and its own comment says the next addition of
any size reds it on purpose.

**Measurement** (the operator asked for it explicitly): one walk of the Claude Code transcript
corpus on BOTH hosts, using the three attribution channels `find-session --skill` reads
(`attributionSkill` auto-fire, `Skill` tool calls, typed `/name`) — **not** the ClickHouse
`skills_used` map, which the `activity` skill measures ~40% low and whose
`JSONExtractString(...) IS NOT NULL` predicate matches the whole table.
Corpus bound: workbench 900 sessions from **2026-08-04**, laptop 170 from **2026-07-12** —
so "never" means "no recorded use since those dates", NOT all-time.

- 17 of 41 skills zero-use in 10d; **13 with zero across the whole corpus** (4,093 chars).
- 🔴 **The counter is blind to service/timer-driven use and is EVIDENCE, NOT A VERDICT.**
  It read 0 for `adoption-scan` (20,494 tool-invocation events), 1 for `dl-router` (a live
  systemd service) and 0 for `repo-cos` (a weekly timer read as email). The ledger predicted
  exactly this. Do not delete on a zero.
- **Paid for syshealth by SHRINKING three never-fired descriptions**, keeping every
  capability and every `/name`: `window-triage` 507→126, `initiatives` 389→128,
  `standup` 367→116.
- Net **−602 chars** vs the pre-change 12,861. Both ratchets re-pinned DOWNWARD:
  `LISTING_TOTAL_CEILING_CHARS` 12,929→**12,259** (headroom 0 by choice) and
  `TIER_A_CEILING_CHARS` 8,821→**7,928**, plus the four `MEASURED_*` constants.
- 🔴 **Correction now recorded in the source: demoting a skill to tier B does NOT relieve the
  listing ceiling.** `listing_total_chars` sums EVERY entry and never reads
  `claude/skill-tiers.json`; a comment there implied it did. Only shortening or removing
  description TEXT moves that number.

## Open questions
1. The two un-reaping parents are a live finding nobody has acted on — is the
   `sleep infinity` container (pod `f1a4b8bc-610c-422f-b1ed-c1c0f0dbe395`) meant to be running
   at all, and does it want `shareProcessNamespace`/an init shim?
2. `syshealth` is tier B, so it will NOT auto-fire from "check zombie procs" — the exact
   phrasing that produced it. Promoting it to A needs an eviction in the same commit.
3. Should `syshealth` become a `bar` pill or a timer? Deliberately out of scope for v1.
