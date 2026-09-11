# Handoff: tmux-scratchpad-bar-statusline — 2026-09-10

## Run this first — the index, one command
```bash
cairn recall --repo /home/zach/workspace/devrc
```

## Goal
Two UX improvements to the scratchpad and tmux statusline:
1. Make the named, colored scratchpad popup hotkeys larger (90%×90% instead of 80%×80%)
2. Remove time/host from tmux status-right, and move the scratchpad status legend (currently in tmux status-left) to the i3status-rust bar — with the full 20-slot list since there's more space there

## State now
- Branch: `main` (clean, no uncommitted changes)
- No PR open for this work yet — nothing has been started beyond planning
- This session produced a **complete implementation plan** but no code changes

### Plan (5 files, 1 new)
1. **`nix/programs/tmux/default.nix`** — `mkBind` function (line 24): change `80%` → `90%` in `-w` and `-h` for all 20 generated scratchpad popup bindings
2. **`.tmux.conf`** — three edits:
   - Line 89: scratch-picker popup `80%` → `90%` (match generated bindings)
   - Line 186: remove `#(~/.config/tmux/scratch-status.sh)` from `status-left`
   - Line 193: remove `#[fg=#ebdbb2]%H:%M #[fg=#282828,bg=#83a598] #H ` from `status-right`
3. **`scripts/i3status-scratchpads`** — NEW bar block script (Python, reads `SCRATCH_SLOTS` from `tmux-scratch-slots.sh`, queries `tmux list-sessions`, renders all 20 slots as `<key><count>` in slot color, hide-at-zero for sessions that don't exist, dim gray for absent ones)
4. **`nix/graphical.nix`** — add `scratchpadsBlock` definition (custom, interval=30, no signal), insert after `diskBlock` in the `blocks` list, add `home.file` entry (unconditional — both hosts)

## Open investigations — live diagnosis state
_(none — this is a planned feature, not a bug investigation)_

## Next steps (ranked)
1. Implement the 5-file plan above, starting with `scripts/i3status-scratchpads` (the new block script) **forcing:** none
2. Run `nix-instantiate --parse nix/graphical.nix >/dev/null` and `python3 scripts/i3status-scratchpads` to verify **forcing:** none
3. `home-manager switch --flake ~/workspace/devrc --impure` on workbench to validate end-to-end **forcing:** none

## Gotchas / decisions / dead-ends
- The scratch-status.sh script (`scripts/tmux-scratch-status.sh`) is NOT deleted — it's just no longer called from the tmux statusline. It may still be useful for debugging or as a fallback.
- The bar block is unconditional (both hosts) because scratchpads exist on both hosts, unlike the poller-backed count blocks which are workbench-only.
- No signal needed — the block reads local tmux state directly, no poller/cache. Same pattern as `i3status-claude-runs`.
- Left-click on the bar legend opens `scratch-picker.sh` in a float terminal.
- The continuum-save interpolation (`set -ag status-right`) in `nix/programs/tmux/default.nix` must remain AFTER the simplified status-right — it appends and survives reloads, so the ordering is still correct.

## How to verify
1. `nix-instantiate --parse nix/graphical.nix >/dev/null` — syntax check
2. `python3 scripts/i3status-scratchpads` — should output valid JSON with slot legend
3. `home-manager switch --flake ~/workspace/devrc --impure` — deploy
4. Verify bar shows full scratchpad legend after disk block
5. Verify tmux status-right no longer shows time/host
6. Verify scratchpad popups are 90%×90%
