---
clawgate-task: none — no session ID available
---
# Handoff: opencode-rig-control-skill — 2026-08-30

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
Create an opencode skill + command for the rig-control RGB gradient system, with full test coverage and a blind dogfood validation dispatch.

## State now
- Branch: `main` (behind origin by 2 commits, uncommitted changes in `nix/graphical.nix`, `nix/pkgs/default.nix`, plus two new untracked test files)
- **DONE this session:**
  - Created `~/.config/opencode/skills/rig-control/SKILL.md` — full subsystem knowledge (prereq guard, state, toggle, gradient, color editing, timers, gotchas, files)
  - Created `~/.config/opencode/commands/rig-control.md` — `/rig-control` command with `$ARGUMENTS` for status/sleep/wake/colors/edit/restart
  - Created `scripts/tests/test_opencode_rig_control.py` — 34 tests (frontmatter, body sections, script existence, color schedule parsing, command template, consistency)
  - All 34 tests pass (`nix develop -c python3 -m pytest scripts/tests/test_opencode_rig_control.py -v`)
  - Blind dogfood dispatch v1: skill loaded but state file hit `external_directory` permission, `rig-control-fade` not on PATH
  - Fixed: `~` → `$HOME` for state path, full path for `rig-control-fade`
  - Blind dogfood dispatch v2: all 4 commands succeeded (openrgb check, state read, timers, gradient color)
- **NOT committed:** the skill, command, and test file are all untracked/new. `git add` them before shipping.
- **NOT deployed to laptop:** skill is global (`~/.config/opencode/`) — will deploy on next `ship.sh` or manual copy

## Open investigations — live diagnosis state
(none — this was a build task, not an investigation)

## Next steps (ranked)
1. `git add ~/.config/opencode/skills/rig-control/SKILL.md ~/.config/opencode/commands/rig-control.md scripts/tests/test_opencode_rig_control.py` and commit + push as a PR
   forcing: none
2. Run `ship.sh` to deploy the skill to both hosts
   forcing: none
3. Tier the skill in `claude/skill-tiers.json` — rig-control should be tier A (must auto-fire from RGB/lighting symptoms)
   forcing: none

## Gotchas / decisions / dead-ends
- **opencode dispatch `--dir` restricts file access**: commands like `cat ~/.cache/rig-control/state` get auto-rejected as `external_directory`. Fix: use `$HOME/.cache/...` instead of `~/.cache/...` in templates.
- **Scripts not on PATH**: `rig-control-fade` is in `scripts/` dir, not on PATH. Skill/command templates must use full path `~/workspace/devrc/scripts/rig-control-fade`.
- **Dogfood v1 found both issues**, v2 confirmed fixes. The blind dispatch pattern works well for validating skill content.
- **`generate-commands.py` auto-generates commands from Claude skills**: the opencode command at `~/.config/opencode/commands/rig-control.md` is a hand-written override (like `browser` and `dl-router`). If the skill changes, the command may need updating too — or the skill could be added to `generate-commands.py`'s source dir if no custom command template is needed.

## How to verify
```bash
# Tests
nix develop -c python3 -m pytest scripts/tests/test_opencode_rig_control.py -v

# Skill exists and has valid frontmatter
head -5 ~/.config/opencode/skills/rig-control/SKILL.md

# Command exists
head -5 ~/.config/opencode/commands/rig-control.md

# Dogfood: dispatch a read-only validation
opencode-dispatch run --dir ~/workspace/devrc --title "verify rig-control skill" -m flash <<'EOF'
Load the rig-control skill, run its prereq guard, check state, list timers, get gradient color.
Report raw output. Read-only, no edits.
EOF
```
