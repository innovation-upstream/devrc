# Handoff: rig-control brightness restore — 2026-08-06

## Goal
Fix unreliable brightness restore when waking from sleep mode via the rig-control yad panel. The monitor (LG ULTRAGEAR on DDC-CI bus 5) sometimes stays dark after clicking "Wake Mode".

## State now
- Branch: `main`, behind origin by 4 commits, uncommitted changes in 2 files
- Files changed: `scripts/monitor-blackout.sh` (+23/-7), `scripts/rig-control.sh` (+1/-1)
- No open PR for this work yet
- All 27 tests pass (`test_monitor_blackout.py` + `test_rig_control.py`)
- Brightness currently at 60/100, rig state "awake", no stale timers

## What's DONE (uncommitted)
Three bugs fixed in `scripts/monitor-blackout.sh` and `scripts/rig-control.sh`:

1. **Race condition between fade_blackout and fade_restore** — sleep's background `fade_blackout` (~9s) kept running and overwriting brightness after wake's restore. Fixed by:
   - `rig-control.sh:do_wake()` now calls blocking `restore()` instead of `restore_bg()` (`rig-control.sh:71`)
   - `fade_blackout()` writes its PID to `$FADE_PID_FILE` (`monitor-blackout.sh:136`)
   - `restore()` kills that process before restoring (`monitor-blackout.sh:88-96`)

2. **Saved brightness of 0** — if blackout triggered while monitor already dark (from prior failed restore), state file contained `bus:0`, so wake restored to 0. Fixed by:
   - `restore()` and `fade_restore()` treat saved brightness of 0 as invalid, default to 60 (`monitor-blackout.sh:101`, `monitor-blackout.sh:151`)

3. **DDC-CI flakiness** — this LG panel intermittently fails `setvcp`. Fixed by:
   - `set_brightness()` retries 3x with 0.5s backoff (`monitor-blackout.sh:48-55`)
   - `fade_to_zero`/`fade_from_zero` use `wait || true` so individual failures don't abort the fade (`monitor-blackout.sh:112`, `monitor-blackout.sh:122`)

## Open investigations — live diagnosis state

### DDC-CI intermittent `DDC communication failed`
- **Symptom:** `ddcutil --bus 5 setvcp 10 <val>` sometimes fails with `DDC communication failed for monitor on bus /dev/i2c-5`
- **Observed:** Journal entries:
  ```
  Aug 05 08:16:19 nixos ddcutil[2653893]: DDC communication failed for monitor on bus /dev/i2c-5
  Aug 06 09:24:21 nixos ddcutil[3228046]: DDC communication failed for monitor on bus /dev/i2c-5
  ```
  Manual `ddcutil` calls succeed reliably when run directly (~5/5 rapid calls). Failures appear only in systemd-run units or after monitor state transitions.
- **Ruled out:** permissions (user has ACL on `/dev/i2c-5`), ddcutil version (2.2.7), bus detection (bus 5 responds reliably to `getvcp`)
- **Leading hypothesis:** DDC-CI timing — the panel's DDC interface needs a settling period after state changes (blackout/restore). The 0.5s retry backoff works around this.
- **Next probe:** If the issue resurfaces, try `ddcutil --sleep-multiplier 2.0` to increase inter-command delay.

### Auto-restore timer bypasses retry-capable set_brightness
- **The systemd-run timer** created by `schedule_restore()` runs raw `ddcutil --bus N setvcp 10 $val` without retries. If DDC-CI fails when the timer fires (8h later), brightness stays at whatever it was.
- **Mitigated by** the fact that wake now kills any in-progress fade and restores blocking, so the timer rarely fires in practice. But not fully fixed.
- **Next step if needed:** Replace the raw ddcutil call in `schedule_restore()` with a wrapper script that retries.

## Next steps (ranked)
1. Commit these changes and `home-manager switch` to deploy to workbench (laptop doesn't have DDC-CI)
2. Test the yad panel sleep→wake flow live for a day to confirm reliability
3. (Optional) Harden `schedule_restore()` to use retry-capable wrapper instead of raw ddcutil

## Gotchas / decisions / dead-ends
- **Fade disabled on wake** — wake is now blocking (no visual fade). The ~1s restore is fast enough; the fade caused all the race conditions. Sleep still fades in background.
- **PID kill only kills parent** — `fade_blackout`'s child `set_brightness &` processes become orphans when killed. In testing, they complete quickly and don't interfere (each is a single ddcutil call). If this proves wrong, switch to process-group kill (`kill -- -$PID`).
- **`pkill -f` avoided** — RULES.md warns about it; PID file approach used instead.
- **`restore_bg` still exists** in rig-control.sh (for backward compat / CLI use) but `do_wake()` no longer calls it.

## How to verify
```bash
# From brightness 0 (bad state), sleep then immediate wake:
ddcutil --bus 5 setvcp 10 0 --noverify
echo "awake" > ~/.cache/rig-control/state
rig-control.sh sleep && sleep 1 && rig-control.sh wake
ddcutil --bus 5 getvcp 10 --brief  # should show60

# Full test suite:
python3 -m pytest scripts/tests/test_monitor_blackout.py scripts/tests/test_rig_control.py -v

# Deploy:
home-manager switch --flake ~/workspace/devrc --impure
```
