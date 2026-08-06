# Plan: migrate i3 status bar → i3status-rust, move i3 into home-manager (2026-07-06)

Grounded against the live workbench system + repo. "Option 1" from the i3-bar research.

## Verified current wiring (ground truth)
`/etc/nixos/configuration.nix`:
```nix
services.displayManager.defaultSession = "none+i3";
services.xserver.windowManager.i3 = {
  enable = true;
  configFile = "/etc/i3.conf";          # forces `i3 -c /etc/i3.conf`
  extraPackages = [ dmenu rofi xorg.xrandr i3lock i3blocks ];
};
environment.etc."i3.conf".text       = import ~/workspace/devrc/nix/system/i3config.nix;
environment.etc."i3blocks.conf".text = import ~/workspace/devrc/nix/system/i3blocks.nix;
```
- Because `configFile` is set, **`~/.config/i3/config` is ignored until the system stops setting it** → a home-manager switch alone can't take over; a matching `sudo nixos-rebuild` is mandatory for cutover.
- The `environment.etc` strings import from the **repo working tree** — switching the repo git branch changes what the next `nixos-rebuild` builds (footgun; freeze branch during cutover).
- The bar (status_command, gruvbox workspace colors) is declared inside the i3 config's `bar {}`. i3status-rust replaces only the statusline content, not the i3bar colors.
- **Drift confirmed** repo↔live in both directions (brightness bindings, tabbed keybind, nm-applet, `[dictation]` present in repo/absent live, `[rigcontrol]` top vs bottom, ssid/wifi pinned to the *laptop's* `wlp170s0` so they render nothing on workbench).
- **This host = workbench**: no battery/backlight, wifi `wlp15s0`, `~/.server-mode` PRESENT (so `serverMode=true` on a graphical desktop — see decision b).

## Key decisions
- **(a) i3 config = raw string via `xdg.configFile."i3/config".text`**, NOT the HM i3 DSL (Zach hand-maintains ~160 lines; DSL port is high-churn/risky). Re-add the DSL's build-time `i3 -c -C` validation + `i3-msg reload` manually.
- **(b) Do NOT gate the bar on `serverMode`** — it's true on graphical workbench (would disable the bar). Introduce `isLaptop = builtins.pathExists "/sys/class/backlight/intel_backlight"` (HM evals per-host under `--impure`) for battery/backlight/wifi (laptop) vs rig-control/DDC (workbench).
- **(c)** `theme = "gruvbox-dark"`, `icons = "none"` (keep current text labels; no nerd-font installed → glyphs would tofu). Font + glyph icons = separate later change.
- **(d)** custom `[[block]]` scripts emit JSON; clicks move from in-script `$BLOCK_BUTTON` to `[[block.click]]`; refresh from `pkill -RTMIN+n i3blocks` → `signal=N` + `pkill -RTMIN+N i3status-rs`.

## Per-host block mapping
| Old i3blocks | New i3status-rust | Type | Hosts |
|---|---|---|---|
| memory | `memory` | built-in | both |
| disk | `disk_space` path=/ | built-in | both |
| cpu_usage | `cpu` | built-in | both |
| temperature | `temperature` (per-host chip) | built-in | both |
| battery | `battery` | built-in | **laptop only** |
| bandwidth+ssid+wifi+iface | `net` (device per host) | built-in | both — **collapses 4→1** |
| calendar | `time` + `[[block.click]]`→`yad --calendar` | built-in+click | both |
| vpn (rofi menu + detail) | `custom` (split: status/menu) signal=10 | custom | both |
| dictation | `custom` render-only, signal=11 | custom | both |
| rigcontrol (⚙ → yad) | `custom`, click→`rig-control.sh gui` | custom | **workbench only** |

Retires ~8 custom scripts (cpu/mem/disk/temp/battery/ssid/wifi/iface).

## Phased execution (workbench first; laptop only after verified)
- **P0 Freeze & branch** off `feat/monitor-blackout` → `feat/i3-bar-hm`; don't switch repo branch between staging the system change and rebuild.
- **P1 Author HM config** (inert): `isLaptop` in home.nix; `programs.i3status-rust` + `xdg.configFile."i3/config"` (guarded `mkIf isNixOS`); new `nix/i3/config.nix` (raw string, fn of `{isLaptop}`, reconciled superset of drift); split VPN scripts; dictation render script; `home.file` symlinks. **Gate A:** `home-manager build` + inspect generated TOML/config in the store (zero desktop impact).
- **P2 Prove bar renders** (inert): run `i3status-rs <generated toml>` in a terminal on `:0`; every block resolves (temp chip, net device, ⚙); VPN/dictation JSON valid; screenshot. **Gate B.**
- **P3 `home-manager switch`** — writes `~/.config/i3/config` + TOML but running i3 is still `-c /etc/i3.conf` → **desktop unchanged (inert)**. Check for pre-existing unmanaged `~/.config/i3/config` (use `force=true`). **Gate C:** `i3 -c ~/.config/i3/config -C` passes.
- **P4 System cutover** (one sudo moment, staged as `nix/system/apply-i3-to-hm.sh` for Zach): remove `configFile="/etc/i3.conf"`, remove `i3blocks` from extraPackages, remove both `environment.etc."i3*.conf"`; keep `i3.enable`+`defaultSession`. Script prints diff + stops before rebuild. Then `sudo nixos-rebuild switch` → **`i3-msg restart`** (preserves layout). Keep old `/etc/nixos` i3 files as rollback. **Gate D:** exercise every clickable block; workbench shows no battery.
- **P5 Cleanup** (after Gate D holds ~a day): rm `/etc/nixos/i3config.nix,i3blocks.nix,i3blocks-scripts/`; retire repo `nix/system/i3*.nix` + `apply-i3-*.sh` + `apply-rig-controls.sh`. **Gate E:** `grep -rn 'i3blocks|/etc/i3' /etc/nixos ~/.config` empty.
- **P6 Laptop**: repeat P3–P4 (inspect laptop's own `/etc/nixos/configuration.nix` first; `isLaptop` adds battery/backlight/wifi, drops rig).

## Top risks
- **R1/R2 broken bar on live desktop** → P1–P3 inert; real switch is one revertible `i3-msg restart`; old config kept until Gate E; workbench first, keep an ssh/TTY.
- **R3 repo-working-tree import** → freeze branch during P4; apply script removes the `import` entirely.
- **R4 serverMode mis-gate** → never gate bar on serverMode.
- **R6 vpn-sudo NOPASSWD sudoers** targets `/etc/nixos/i3blocks-scripts/vpn-sudo`; moving to a store path breaks `sudo` → **find the sudoers rule first** (not in main configuration.nix; in an import) and repoint, or keep vpn-sudo at a stable path.
- **R5 host specifics**: temperature chip, net device (`wlp15s0`/`wlp170s0`), disk path — validate at Gate B per host.

## Sequencing vs PR #73 (recommended)
**Rebase-into-migration**, don't merge-then-migrate. #73's keepers (`rig-control.sh`, `monitor-blackout.sh`, `i3blocks-rigcontrol`) fold into the HM i3 config; its `apply-rig-controls.sh` + `nix/system` rig edits get deleted (this migration retires that path). Branch `feat/i3-bar-hm` off `feat/monitor-blackout`, land rig-control *and* the bar migration in one PR. (If Zach wants the ⚙ button on workbench sooner, merge #73 first — but then the migration PR must explicitly delete the `nix/system` rig additions.)

## Unverified — resolve during implementation
- Location of the `vpn-sudo` NOPASSWD sudoers rule (must find before moving vpn-sudo).
- Laptop's `/etc/nixos/configuration.nix` (i3 wiring, brightness, `wlp170s0`).
- Whether `class="float"` actually floats today (no matching `for_window` rule found).
- Correct `temperature` chip on workbench.
