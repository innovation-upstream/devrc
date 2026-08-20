# Handoff: opencode-autocomplete-commands — 2026-08-19

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
Fix opencode's TUI autocomplete so skills (like `clickup`) appear in the `/<name>` dropdown.
Skills had `source: "skill"` and empty `hints`, which excluded them from autocomplete.

## State now
- Branch: `main` at `84c10ae` (clean, both hosts converged)
- Commits: `b9b6c04` (generate-commands.py + nix/home.nix + CLAUDE.md), `84c10ae` (python3 nativeBuildInputs fix)
- PRs: none (direct commits to main via feature branches, merged with `--ff-only`)
- Deploy: both hosts converged via `ship.sh` — verified 0 dangling, 0 absent managed artifacts

**What's DONE:**
- `scripts/opencode/generate-commands.py` — reads `claude/skills/*/SKILL.md`, generates `~/.config/opencode/commands/<name>.md` with `source: "command"` and extracted `$ARGUMENTS` hints
- `nix/home.nix` — `opencodeCommands` derivation (runs generator at build time) + `home.file.".config/opencode/commands"` mapping
- `CLAUDE.md` — documented the auto-generation
- All33 skills in `claude/skills/` now appear as `/<name>` commands in the TUI

**What's NOT covered (edge cases):**
- `customize-opencode` — built-in to opencode, no SKILL.md in `claude/skills/`
- `browser`, `dl-router` — their SKILL.md lives at `scripts/browser-bridge/` and `scripts/dl-router/`, symlinked via `mkOutOfStoreSymlink`, not in `claude/skills/`

## Open investigations — live diagnosis state
(none — the work is complete and deployed)

## Next steps (ranked)
1. Verify autocomplete works in a fresh opencode session — type `/` in the TUI and confirm clickup + other skills appear in the dropdown
2. Consider whether `browser` and `dl-router` should also get command wrappers (they're currently `source: "skill"` with empty hints)

## Gotchas / decisions / dead-ends
- `runCommandLocal` does NOT put `nativeBuildInputs` on PATH automatically — the first build failed with "python3: command not found" until `nativeBuildInputs = [ pkgs.python3 ]` was added
- opencode's `hints` field is auto-extracted from `$` patterns in the template body, not from frontmatter — skills with `$ARGUMENTS` in their body get `hints: ["$ARGUMENTS"]` as commands
- The `GET /command` endpoint is the authority for what the TUI autocompletes; `opencode debug skill` is a different format with `hints: null`

## How to verify
```bash
# Start opencode serve, query the command endpoint
opencode serve --port 19876 &
curl -s http://127.0.0.1:19876/command | jq '.[] | select(.name == "clickup")'
# Should show source: "command" (not "skill")
```
