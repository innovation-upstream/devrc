# i3 — environment and tool inventory

## Environment
- **Display**: single 3440x1440 ultrawide on DP-0
- **Mod key**: Alt (Mod1)
- **Bar**: i3status-rust at top, home-manager-managed (`nix/graphical.nix` — NOT the old
  i3blocks in `/etc/nixos`, migrated PR #74). System blocks (mem/disk/net/cpu/temp/GPU/
  sound/vpn/time) + hide-at-zero count blocks (clawgate/mail/homelab-alerts/civitai-alerts,
  fed by `scripts/bar-status-poll`) + the live Claude-runs count ▦ (indicator only —
  its click-to-launch went with the retired agent-ops TUI) + rig-control ⚙.
  **Bar work belongs to the `bar` skill, not this one.**
- **Theme**: gruvbox dark (#282828 bg, pixel 2 borders)
- **Launcher**: `rofi -show drun -show-icons -theme gruvbox-dark-hard`
- **Lock**: `i3lock -c 282828` (xss-lock for suspend)
- **Notifications**: dunst (gruvbox-themed)
- **Terminal**: alacritty (via `$I3CONFIG_DEFAULT_TERMINAL`)
- **Browser**: brave
- **Screenshots**: flameshot

## Tool inventory
| Tool | Path | Purpose |
|------|------|---------|
| `i3-msg` | `/run/current-system/sw/bin/i3-msg` | IPC: query tree, focus, move, resize, exec |
| `xdotool` | `~/.nix-profile/bin/xdotool` | Keyboard/mouse simulation, window geometry |
| `xprop` | `/run/current-system/sw/bin/xprop` | X11 window properties and atoms |
| `xclip` | `/run/current-system/sw/bin/xclip` | Clipboard read/write |
| `flameshot` | `/run/current-system/sw/bin/flameshot` | Screenshot capture |
| `notify-send` | `~/.nix-profile/bin/notify-send` | Desktop notifications (dunst) |
| `jq` | system PATH | JSON parsing for i3 IPC responses |

## Reliability tiers
Always use the highest tier available for a task.

**Tier 1 — deterministic (prefer):**
- i3 IPC via `i3-msg`: focus, move, layout, workspace, marks, criteria selectors
- `xdotool type`: text input (deterministic character sequence)
- `xdotool key`: key combos (Ctrl+S, Return, Escape)
- clipboard via `xclip`: data transfer in/out of GUI apps
- `notify-send`: desktop notifications

**Tier 2 — visual verification recommended:**
- `xdotool mousemove + click`: coordinate-based clicking (fragile if the window moves/resizes)
- `xdotool mousedown/mouseup`: drag operations
- scroll operations: may need adjustment for scroll speed/amount
- `arrange` recipes: compound operations that depend on window state

**Tier 3 — last resort:**
- multi-step GUI automation (click → type → click → verify) — each step compounds error risk
- coordinate-based operations on dynamic content (scrolling pages, animations)
- browser GUI interaction — prefer the `browser` skill (real logged-in Brave) or Playwright

If Tier 2/3 is needed, bracket the action with screenshots (before + after) to verify.
