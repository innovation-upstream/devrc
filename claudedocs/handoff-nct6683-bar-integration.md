# Handoff: nct6683 cooling sensors → bar integration

## Goal
Make the AIO pump and case fan RPMs visible in the i3status-rust bar, and persist the `nct6683` kernel module across reboots via NixOS configuration.

## State now
- Branch: `feat/nct6683-fans-bar`, pushed. Commit `357aa140`. **No PR yet** — waiting on the gate (below).
- **Rank 2 (the bar pill) is DONE and LIVE.** Rank 1 is **STAGED, not applied** — it needs sudo, which Claude cannot do.

**DONE this session**
- `scripts/i3status-fans` (new) — reads the NCT6687D tachos out of `/sys/class/hwmon`, no poller/cache/network.
- `fansBlock` in `nix/graphical.nix` + its `home.file`, both gated `!isLaptop`; block sits between `temperatureBlock` and `gpuBlock`.
- `scripts/tests/test_i3status_fans.py` (new, 50 tests) and `refresh` added to `_KNOWN_GOOD_ICONS` in `test_bar_status.py`.
- `nix/system/apply-nct6683-module.sh` (new, executable) — the staged sudo script for rank 1.
- Deployed: `home-manager switch --flake ~/workspace/devrc --impure` + `i3-msg restart`. **The pill is rendering live**, screenshotted as `⟳ 2810·1749` on the real bar.

**IN FLIGHT**
- 🔴 **The dev-host pytest tier has NOT reported yet.** `nix develop ~/workspace/devrc -c bash scripts/gate.sh --tier pytest` was still running when this doc was written; its verdict is UNKNOWN, not green. The **node tier passed** (1449 tests, 5 suites, 0 fail) and the **nix check derivations were never run at all**. Do not read "tests pass" into this doc — see "How to verify".

**Verified so far (name the scope, not just the verdict)**
- `scripts/tests/test_i3status_fans.py` — 50 passed, run under `nix develop`.
- `scripts/tests/test_bar_status.py` — 529 passed, same shell. Includes the gate-pairing guard and the icon guard, and the icon guard's STRONG half ran (i3status-rust 0.36.1 is in this host's `/nix/store`, so the allowlist was checked against the real key set, not just against itself).
- Three mutants, in an isolated copy under `PYTHONDONTWRITEBYTECODE=1`, control green either side. Each died to its OWN named guard with its OWN assertion:
  - state-ordering swap (`Warning if unknown` before `Critical if alarm`) → **only** `test_render_an_ALARM_OUTRANKS_an_unreadable_sibling`, `assert 'Warning' == 'Critical'`; the other 49 passed, so the guard is isolated.
  - `find_chip` matching any device instead of the `name` → `test_find_chip_locates_BY_NAME_not_by_number`.
  - hide-at-zero (`return {}` when Idle) → `test_this_pill_is_ALWAYS_VISIBLE[readings0]`, `assert None` on the text.
- All four render states driven end-to-end against real AND fake sysfs (see "How to verify").

## What was discovered
- Motherboard: MSI X670E GAMING PLUS WIFI (MS-7E16)
- CPU: AMD Ryzen 9 9900X (12c/24t)
- Super I/O: Nuvoton NCT6687D eSIO — probed at 0x4e/0x4f, driver `nct6683`
- `nct6775` does NOT work for this chip (returns "no such device")
- `sensors-detect --auto` is the reliable probe — it found the chip at 0x4e/0x4f
- AIO pump: fan1 @ 2823 RPM (pwm1=114%, driven past max — typical for pump headers)
- Case fan: fan3 @ 1646 RPM (pwm3=76%)
- fan2, fan4–10: 0 RPM (not connected)

### sensors output (nct6687-isa-0a20 section)
```
fan1:  2823 RPM  (AIO pump)
fan3:  1646 RPM  (case fan)
fan2,4–10: 0 RPM
pwm1: 114%  pwm2: 128%  pwm3–8: 76%
AMD TSI Addr 98h: +82.0°C  (CPU socket)
Diode 0: +36.0°C
Thermistors: 37–46°C (VRM/chipset)
```

## Next steps (ranked)
🔴 **Renumbering note:** rank 2 was "add the bar block" and is now DONE, so rank 2 below is a
different item. Both `nct6683-bar-integration-1` and `-2` claims from this session are RELEASED,
so no live claim is re-pointed by that shift. Ranks 1 and 3 keep their original meaning.

1. **Run the staged sudo script** — `sudo bash ~/workspace/devrc/nix/system/apply-nct6683-module.sh`.
   Adds `"nct6683"` to `boot.kernelModules` in `/etc/nixos/configuration.nix`, rebuilds, and verifies.
   `NCT_DRY_RUN=1` prints the edit and rebuilds nothing. Until this runs, the driver is loaded only
   by August's hand `modprobe` and the pill will read `?` after the next reboot.
   forcing: user — only the operator can sudo; Claude is forbidden `sudo nixos-rebuild` by CLAUDE.md.
2. **Finish the gate, then open + merge the PR for `feat/nct6683-fans-bar`.** Needs BOTH tiers on the
   MERGED tree (the branch already contains `origin/main`, so merged-tree == branch as of `3d4ac941`):
   `nix develop ~/workspace/devrc -c bash scripts/gate.sh --tier both`, AND
   `nix build .#checks.x86_64-linux.pytests` then `.nodetests` **one at a time**.
   forcing: gate — nothing else blocks this merge (`main` is protected in name only, deliberately),
   so the two-tier run is the only real gate and it is not finished.
3. **Optional: a dunst toast when the pump stalls.** The pill already goes Critical below 500 RPM,
   but a fullscreen game or a covered bar hides it. Would need a poller source + rising-edge latch
   (the `bar-status-poll` shape), which is a real cost for a rare event.
   forcing: none — nothing external is asking for it.
4. **Optional: verify persistence survives an actual reboot.** The script's own check reads
   `/etc/modules-load.d/nixos.conf`, which is what systemd replays at boot, so this adds little.
   forcing: none.

## Gotchas / decisions
- The chip is NCT6687D, not NCT6798D — the MSI X670E GAMING PLUS WIFI spec page lists NCT6798D but the actual probe found NCT6687D
- `nct6775` module is the wrong driver for this chip — must use `nct6683`
- `sensors-detect --auto` is the definitive probe; manual `modprobe` guesses won't work
- pwm1 >100% is normal for pump headers — the board overdrives them by design

- 🔴 **`lsmod` CANNOT verify rank 1, and that is why the script does not use it.** The module is
  already loaded by hand, so `lsmod | grep nct6683` says yes whether or not the config change landed —
  it cannot distinguish "persisted" from "a human modprobe'd it in August". The discriminating
  artifact is `/etc/modules-load.d/nixos.conf`; measured BEFORE the change, `nct6683` was absent from
  it while the module was live in `lsmod`. That file is what the apply script greps.
- 🔴 **hwmon numbering is not stable, so the chip is found by its `name` file.** It sat on `hwmon11`
  behind ten nvme/spd5118/k10temp/amdgpu devices. Two of the test fixtures' decoy devices carry a
  `fan1_input` of their own precisely so a by-number lookup fails the suite instead of passing it.
- 🔴 **material-nf has NO `fan` icon key.** Measured against i3status-rust 0.36.1's `material-nf.toml`
  (76 keys, listed in full). `refresh` is the closest real one. An unknown key does not degrade to a
  missing glyph — it renders the whole pill as a red `Failed to render full text` in every visible
  state, which is why `_KNOWN_GOOD_ICONS` had to be extended in the same change. Its comment also said
  "these 4" over a set of 5; now 6 and correct.
- **The pill is ALWAYS VISIBLE, deliberately against the bar's hide-at-zero house style.** A count's
  quiet state means "nothing to do"; a pump RPM is the number itself, and a cooler pill invisible while
  healthy is invisible in exactly the state it exists to certify. Pinned by
  `test_this_pill_is_ALWAYS_VISIBLE`, which is parameterized over every state so a future port of the
  hide-at-zero idiom reddens the suite.
- **The RPM floor is PER-FAN and OPTIONAL; only the pump has one (500).** `fan2` and `fan4`–`fan10`
  read a permanent 0 because nothing is plugged into them, and a case fan on a zero-RPM PWM curve can
  legitimately stop when idle. A blanket floor would make the pill cry wolf, which is how a real alarm
  gets ignored. A fan passed without `:MINRPM` is display-only and can never raise Critical.
- **An alarm outranks an unreadable sibling** (`!0·?` stays Critical). Same house rule the count pills
  follow: an outage may make a reading less trusted, never a recorded alarm quieter.
- **Adding a block needs `i3-msg restart`, not just a switch.** The bar holds the block LIST it parsed
  at startup. Measured here: the running bar had started `Sep 7 18:11` against a config written
  `Sep 8 13:51` — ~20 h stale, and the new block simply absent, on a host reporting a fully successful
  deploy. `pgrep -x i3status-rs` finds nothing (comm is `.i3status-rs-wr`); match `/bin/i3status-rs`
  and take the process whose parent is `i3bar`.
- **`scripts/gate.sh` run from a bare shell exits 3 = MISSING ENVIRONMENT, not a code failure.** It
  prints the fix itself: re-run under `nix develop`. `.envrc` here is `use opencode`, so a loaded
  direnv does NOT put pytest on PATH.
- **No clawgate task was recorded for this session** — `clawgate_handoff.sh resolve` exited 5
  (0 tasks). Its positive control showed the board is reachable and the token accepted, but a wrong
  session id also answers 200 with an empty array, so this is NOT a clean bill of health; no
  `clawgate-task:` field was written.
- Five untracked files in the tree (`nix/system/apply-journald-settings-migration.sh`,
  `apply-nebula-relay.sh`, `check-nebula-relays.sh`, `output.txt`, `scripts/diagnose-nix-disk.sh`)
  belong to OTHER sessions. They were left exactly as found and nothing was blind-staged.

## How to verify
🔴 **The gate is NOT part of this list's green — it had not reported when this doc was written.**

1. **The block, offline (no hardware needed):**
   `nix develop ~/workspace/devrc -c python3 -m pytest scripts/tests/test_i3status_fans.py scripts/tests/test_bar_status.py -q`
   → expect 50 + 529 passed.
2. **The block against REAL hardware:**
   `~/workspace/devrc/scripts/i3status-fans --fan pump=1:500 --fan case=3`
   → `{"icon": "refresh", "text": "2836·1648", "short_text": "2836", "state": "Idle"}` (numbers vary).
3. **The three states you cannot get from real hardware** — build a fake root and point at it:
   ```bash
   F=$(mktemp -d); mkdir -p $F/hwmon0 $F/hwmon3
   echo nvme > $F/hwmon0/name; echo nct6687 > $F/hwmon3/name
   echo 0 > $F/hwmon3/fan1_input; echo 1736 > $F/hwmon3/fan3_input
   S=~/workspace/devrc/scripts/i3status-fans
   $S --hwmon-root $F --fan pump=1:500 --fan case=3   # -> "!0·1736"  Critical
   rm $F/hwmon3/fan3_input
   $S --hwmon-root $F --fan pump=1:500 --fan case=3   # -> "!0·?"     Critical (alarm beats unknown)
   rm $F/hwmon3/name
   $S --hwmon-root $F --fan pump=1:500 --fan case=3   # -> "?"        Warning (driver not loaded)
   ```
4. **The pill is actually on the bar** (a switch alone does NOT do this):
   ```bash
   stat -c '%y' ~/.config/i3status-rust/config-top.toml
   ps -eo pid,ppid,lstart,args | grep '/bin/i3status-rs' | grep -v grep   # must have STARTED AFTER that mtime
   ```
   If it started before, `i3-msg restart`. Then look at the bar between the temperature and GPU pills.
5. **Rank 1 landed** (after the sudo script):
   `grep -x nct6683 /etc/modules-load.d/nixos.conf` → exit 0. 🔴 **NOT `lsmod`** — see Gotchas.
6. **Before merging:** `nix develop ~/workspace/devrc -c bash scripts/gate.sh --tier both`, then
   `nix build .#checks.x86_64-linux.pytests` and `.nodetests` **one at a time** (a combined
   invocation produces FALSE failures via store contention). Name the tier and base sha in the claim.
## Run this first — the index, one command
```bash
cairn recall --repo /home/zach/workspace/devrc
```
Terse pointers this doc does not carry, curated by past sessions and outliving it.
🔴 RECALL, NOT LIVE OBSERVATION — every line is a pointer to VERIFY, never a current
reading, and it may describe a gotcha already fixed. `scope-absent`/`scope-empty` means
nothing is recorded yet: ordinary, not an error, and not a clean bill of health.
Non-blocking: if it exits non-zero, print the stderr line and carry on.
