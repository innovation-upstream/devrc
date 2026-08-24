---
# Handoff: i3-laptop-espanso-float — 2026-08-24

## Goal
Fix espanso windows tiling instead of floating on the laptop. The espanso floating rule (`for_window [class="(?i)espanso"] floating enable` in `nix/i3/config.nix:70`) is inert on the laptop because the NixOS system config forces i3 to read from `/etc/i3.conf` instead of the home-manager managed `~/.config/i3/config`.

## State now
- Branch: `fix/i3-laptop-espanso-float` — pushed, PR #805 (open, mergeable)
- Commit: `d03702b8` — adds `nix/system/apply-i3-to-hm.sh`, updates stale comments in `nix/graphical.nix`
- No uncommitted changes
- **NOT deployed yet** — the apply script must be run manually with sudo after merge

## Root cause (observed)
- `i3-msg -t get_version` → `loaded_config_file_name: /etc/i3/config`
- `/etc/i3/config` is a symlink → `/etc/static/i3/config`, generated from `/etc/nixos/i3config.nix`
- `/etc/nixos/i3config.nix` is a 166-line basic i3 config with NO espanso rule, NO rig-control float, NO i3status-rust
- `/etc/nixos/configuration.nix` (line ~95, from backup) has:
  ```nix
  windowManager.i3.configFile = "/etc/i3.conf";
  environment.etc."i3.conf".text = import ./i3config.nix;
  environment.etc."i3blocks.conf".text = import ./i3blocks.nix;
  ```
- This forces `i3 -c /etc/i3.conf`, making `~/.config/i3/config` (home-manager) completely inert
- `nix/graphical.nix:14-16` documents this exact problem but references a retired apply script

## The fix
`nix/system/apply-i3-to-hm.sh` removes the three offending lines from `/etc/nixos/configuration.nix` and runs `nixos-rebuild switch`. After rebuild, i3 reads from `~/.config/i3/config` (home-manager managed) where the espanso rule is already live.

## Next steps (ranked)
1. Merge PR #805 — it's open and mergeable
2. Run `sudo bash nix/system/apply-i3-to-hm.sh` on the laptop
3. Reload i3 (`Mod+Shift+r`) or log out/in
4. Verify: `i3-msg -t get_version | jq .loaded_config_file_name` → expect `~/.config/i3/config`
5. Open espanso search — confirm it floats

## How to verify
```bash
# After apply script + rebuild:
i3-msg -t get_version | jq .loaded_config_file_name
# expect: ~/.config/i3/config

# Open espanso (Ctrl+Space or whatever the trigger is) — window should float
```
