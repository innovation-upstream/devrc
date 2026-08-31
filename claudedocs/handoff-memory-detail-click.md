---
clawgate-task: 193
---
# Handoff: memory-detail-click — 2026-08-30

## Run this first — the index, one read-only command
```bash
python3 ~/workspace/devrc/scripts/lib/subsystem_recall.py --repo /home/zach/workspace/devrc
```
Terse pointers this doc does not carry, curated by past sessions and outliving it.
🔴 RECALL, NOT LIVE OBSERVATION — every line is a pointer to VERIFY, never a current
reading, and it may describe a gotcha already fixed. `scope-absent`/`scope-empty` means
nothing is recorded yet: ordinary, not an error, and not a clean bill of health.
Non-blocking: if it exits non-zero, print the stderr line and carry on.

## Goal
Replace the memory block's left-click (currently opens btop — a general system monitor) with a focused "top RAM consumers" view: summary header (RAM/swap + buffers/cached breakdown) + top 15 processes grouped by command, sorted by RSS.

## State now
- Branch: `main`, behind origin/main by 2 commits
- Uncommitted changes on main (not on a feature branch):
  - `scripts/memory-detail` — NEW, staged (`git add`ed), executable
  - `scripts/tests/test_memory_detail.py` — NEW, untracked
  - `nix/graphical.nix` — modified (memoryBlock.click retargeted + home.file entry)
  - `nix/pkgs/default.nix` — modified (added inxi + cpu-x, unrelated to this work)
- **Deployed:** `home-manager switch --impure` succeeded. `i3-msg restart` done.
- **Verified:** `--dump` mode produces correct output. `less` exec path works (user tested after fix).
- **Tests:** 20/20 pass (`nix develop . -c python3 -m pytest scripts/tests/test_memory_detail.py`)
- **NOT committed or pushed yet.** No PR exists for this work.

## What was done this session
1. Created `scripts/memory-detail` — Python script, stdlib only, no external deps
   - `parse_meminfo(text)` → dict (from /proc/meminfo)
   - `parse_ps(text)` → list of dicts (from ps aux)
   - `group_procs(procs)` → grouped by command base name, summed RSS, counted instances
   - `format_summary(info)` → RAM/swap + buffers/cached/reclaimable breakdown
   - `format_table(groups)` → top N grouped commands with total RSS, count, avg %MEM
   - `--dump` prints to stdout; without it, execs into `less` via temp file
2. Created `scripts/tests/test_memory_detail.py` — 20 tests, all offline
3. Modified `nix/graphical.nix:65-66` — memoryBlock.click retargeted from `btopCmd` to `alacritty ... -e ${scriptsDir}/memory-detail`
4. Modified `nix/graphical.nix:450-454` — added `home.file` entry deploying the script (unconditional, both hosts)
5. Bug fixed during session: `os.mktemp` doesn't exist in Python — changed to `tempfile.mkstemp`

## Open investigations — live diagnosis state
None. The feature works as designed.

## Next steps (ranked)
1. Commit and PR the memory-detail work (the uncommitted changes on main). Forcing: user
2. The `nix/pkgs/default.nix` change (inxi + cpu-x) is already PR #1135 on a separate branch — ignore it here. Forcing: none

## Gotchas / decisions / dead-ends
- `os.mktemp` does not exist — must use `tempfile.mkstemp`. This was the bug that caused the float terminal to flash and close instantly.
- `os.execvp("less", ...)` replaces the process, keeping the alacritty window alive until the user presses `q`. This is the correct pattern for float-terminal launchers in i3status-rust.
- The script is on `main` directly, not on a feature branch. Commit needs care (don't commit the unrelated pkgs change with it).
- `clawgate resolve` returned rc=3 (no session id) — no clawgate task was linked to this session.

## How to verify
```bash
# Unit tests
nix develop . -c python3 -m pytest scripts/tests/test_memory_detail.py -v

# Smoke test (stdout, no terminal)
python3 scripts/memory-detail --dump | head -20

# Live test (float terminal)
# Click the memory pill on the bar — should open alacritty float with RAM summary + top processes in less
```
