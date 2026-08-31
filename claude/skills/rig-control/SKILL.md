---
name: rig-control
description: "Operate the rig-control RGB gradient system — sleep/wake toggle, smooth time-of-day gradient, color schedule editing, timer management. Use for: rig-control, RGB lighting, chassis RGB, openrgb, the gradient, sleep/wake mode, color schedule, rig-control-fade, the bar toggle, monitor blackout/restore."
---

# rig-control — RGB gradient + monitor blackout

Smooth time-of-day RGB gradient on the MSI motherboard chassis headers (device 2),
with automated sleep/wake scheduling and monitor blackout/restore via DDC-CI.

## Prereq guard — run this first

```bash
which openrgb >/dev/null 2>&1 || { echo "openrgb not found — rig-control is workbench-only"; exit 1; }
```

Laptop has no openrgb. All commands below are workbench-only.

## Quick reference

| Script | What it does |
|---|---|
| `rig-control.sh` | Main script: sleep/wake/blackout/restore + fade-in on wake |
| `rig-control-fade` | One-shot gradient updater: reads config, interpolates RGB, calls openrgb. `--print` for current color |
| `rig-control-colors.conf` | Color waypoints (edit to change schedule) |
| `rig-control-toggle` | Bar click handler: reads state, calls wake or sleep |
| `i3blocks-rigcontrol` | Icon-only render (☀/🌙). Click handling is in nix block definition |

## State & timers

```bash
# Current state
cat "$HOME/.cache/rig-control/state"                # → "awake" or "sleeping"

# Active timers
systemctl --user list-timers rig-control-* --no-pager
# → rig-control-fade.timer   (every 60s — gradient)
# → rig-control-sleep.timer  (02:30 daily)
# → rig-control-wake.timer   (10:15 daily)
```

## Toggle sleep/wake

```bash
rig-control.sh sleep && cat ~/.cache/rig-control/state   # → sleeping
rig-control.sh wake  && cat ~/.cache/rig-control/state   # → awake
```

`sleep` = RGB off (000000) + monitor blackout. `wake` = fade-in from black (~30s ramp) + monitor restore.

## Test gradient

```bash
~/workspace/devrc/scripts/rig-control-fade --print   # → current interpolated hex color (e.g. "FF6347")
```

## Edit color schedule

File: `scripts/rig-control-colors.conf`

Format: `HH:MM RRGGBB` (comments and blank lines ignored). Linear interpolation between waypoints. Wraps at midnight.

Current schedule:
```
10:00 FFA500  # amber
11:00 FFBF00  # golden
12:00 00CED1  # turquoise
14:00 1E90FF  # blue
16:00 9400D3  # violet
17:00 FF69B4  # pink
18:00 FF6347  # tomato (sunset)
20:00 FF4500  # orange-red
22:00 FF0000  # red
00:00 8B0000  # dark red (carries through sleep)
```

Example — add a 13:00 green waypoint:
```
12:00 00CED1  # turquoise
13:00 00FF00  # green
14:00 1E90FF  # blue
```

After editing, deploy: `~/workspace/devrc/scripts/ship.sh`

## Timer management

```bash
# Restart the gradient timer (e.g. after config change)
systemctl --user restart rig-control-fade.timer

# Check last fade run
journalctl --user -u rig-control-fade -n 5 --no-pager

# Manually trigger one gradient update
systemctl --user start rig-control-fade.service
```

## Gotchas

- **i3status-rust does NOT pass `$BLOCK_BUTTON`**: click handling MUST go in `[[block.click]]` nix config, not in the script.
- **`(( )) && cmd` leaks exit code 1 under `set -e`**: use `if (( )); then ... fi` instead. This killed `do_wake()` after `fade_in()`.
- **`date +%H` is zero-padded**: use `%-H` for case patterns like `0|1|2`.
- **DDC/CI can fail at timer time**: `do_wake()` uses `restore || notify ... || true` so RGB stays on even if monitor restore fails.
- **`openrgb --device 2`**: device 2 is the MSI motherboard chassis headers. Keyboard/mouse are never touched.

## Files

| File | Purpose |
|---|---|
| `scripts/rig-control.sh` | Main script |
| `scripts/rig-control-fade` | Gradient updater |
| `scripts/rig-control-colors.conf` | Color waypoints |
| `scripts/rig-control-toggle` | Bar click handler |
| `scripts/i3blocks-rigcontrol` | Icon renderer |
| `nix/graphical.nix` | 6 systemd units: sleep/wake/fade timers + services |
