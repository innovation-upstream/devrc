---
clawgate-task: none — no session ID available
---
# Handoff: rig-control RGB overhaul — 2026-08-29

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
Overhaul rig-control from a static RGB on/off toggle into a smooth time-of-day gradient system with automated scheduling.

## State now
- Branch: `main` at `07890ebc` (both hosts converged)
- All PRs merged and deployed:
  - `#918` — instant bar toggle + daily sleep/wake timers (3am→2:30am, 10:15am)
  - `#938` — time-of-day RGB color schedule (hourly discrete)
  - `#967` — fix `date +%H` zero-padded hour (`%-H`)
  - `#969` — wake resilience: don't let DDC/CI restore failure block wake
  - `#1006` — move sleep timer to 2:30am
  - `#1013` — smooth gradient RGB with per-minute updates (`rig-control-fade`)
  - `#1016` — fix bar click toggle (`[[block.click]]` handler)
  - `#1019` — fix `(( )) &&` set -e leak killing `do_wake()`
- **Verified live**: both hosts, all timers active, gradient running

## Files changed
| File | What it does |
|---|---|
| `scripts/rig-control.sh` | Main script: sleep/wake/blackout/restore + `fade_in()` on wake |
| `scripts/rig-control-fade` | One-shot gradient updater: reads config, interpolates RGB, calls openrgb. `--print` flag for fade-in |
| `scripts/rig-control-colors.conf` | Color waypoints (edit to change schedule) |
| `scripts/rig-control-toggle` | Bar click handler: reads state, calls wake or sleep |
| `scripts/i3blocks-rigcontrol` | Icon-only render (☀/🌙). Click handling is in nix block definition |
| `nix/graphical.nix` | 6 systemd units: sleep timer, wake timer, fade timer (60s) + services |

## Architecture
```
┌─ systemd timers (workbench only) ─────────────────────────┐
│  rig-control-sleep.timer  → 02:30 daily → rig-control.sh sleep
│  rig-control-wake.timer   → 10:15 daily → rig-control.sh wake
│  rig-control-fade.timer   → every 60s   → rig-control-fade (gradient) │
│
│  bar click (i3status-rust [[block.click]])
│  → setsid -f rig-control-toggle
│  → reads state → rig-control.sh wake|sleep
└──────────────────────────────────────────────────────────┘

rig-control.sh:
  do_sleep(): state=sleeping, rgb_off (000000), blackout_bg
  do_wake():  state=awake, fade_in (30s ramp), restore || toast
  fade_in(): calls rig-control-fade --print, loops N steps openrgb
```

## Color schedule (rig-control-colors.conf)
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
Linear RGB interpolation, updated every 60s by `rig-control-fade`.

## Gotchas / decisions / dead-ends
- **i3status-rust does NOT pass `$BLOCK_BUTTON`**: the old i3blocks `$BLOCK_BUTTON` convention doesn't exist in i3status-rust custom blocks. Click handling MUST go in `[[block.click]]` nix config, not in the script. Every other custom block was migrated; this one was missed.
- **`(( expr )) && cmd` leaks exit code 1 under `set -e`**: when `(( ))` is on the LHS of `&&` inside a function body, `set -e` catches the false exit code before the `&&` short-circuit. Use `if (( )); then ... fi` instead. This killed `do_wake()` after `fade_in()` completed but before `restore()` ran — the monitor stayed blacked out.
- **`date +%H` is zero-padded**: `%H` returns `"01"` not `"1"`. Case patterns like `0|1|2` never match. Use `%-H`.
- **DDC/CI can fail at timer time**: the monitor bus may be unresponsive at 10:15am. `do_wake()` uses `restore || notify ... || true` so RGB stays on even if monitor restore fails.
- **`openrgb --device 2`**: device 2 is the MSI motherboard chassis headers. Keyboard/mouse are never touched.
- **Test stubs**: `openrgb` is in the `HOST_LAUNCHERS` nolaunch ledger. Tests must never reach the real binary. Fade-in is short-circuited in tests via `RIG_FADE_STEPS=1 RIG_FADE_DELAY=0`.

## How to verify
```bash
# Check timers
systemctl --user list-timers rig-control-* --no-pager

# Check state
cat ~/.cache/rig-control/state

# Test toggle
rig-control.sh sleep && cat ~/.cache/rig-control/state  # → sleeping
rig-control.sh wake && cat ~/.cache/rig-control/state   # → awake

# Test gradient
rig-control-fade --print  # → current interpolated hex color

# Edit schedule
vim scripts/rig-control-colors.conf && ship.sh
```
