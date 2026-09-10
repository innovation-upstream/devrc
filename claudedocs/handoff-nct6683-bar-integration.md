# Handoff: nct6683 cooling sensors → bar integration

## Goal
Make the AIO pump and case fan RPMs visible in the i3status-rust bar, and persist the `nct6683` kernel module across reboots via NixOS configuration.

## State now
- **Both ranks DONE and MERGED. Nothing is in flight.** No open PR, no branch of mine outstanding.
- `nct6683` persists (operator ran the script 2026-09-08 14:18:33).
- **PR #1411** merged (squash `161206e6`) — the pill.
- **PR #1430** merged (squash `a2b74e4b`) — the cooling view + its audit round.
- **Both hosts converged and VERIFIED at `a2b74e4b`** (`ship.sh` with `LAPTOP_SSH=zach@10.42.0.100`; the LAN address times out, nebula works). `2 hosts compared` — not `NOT COMPARED`.
- Live: the pill renders `⟳ 2708·1652`; left-click opens the cooling float; `q`/Esc/Ctrl-C all close it; no `__pycache__` beside the deployed scripts.

**What shipped**
- `scripts/i3status-fans` + `fansBlock` (pill), `nix/system/apply-nct6683-module.sh` (the sudo script), `scripts/fans-detail` (cooling view), `scripts/tests/test_{i3status_fans,nct6683_apply,fans_detail}.py`.
- `claude/skills/bar/SKILL.md` carries the pill + the click target.

**Gate coverage — stated as it actually is**
- #1411: dev-host both tiers PASS (21214 passed) + nix `pytests`/`nodetests` PASS, base `01956bf0`.
- #1430: the pytest tier **timed out twice at 3600s with ZERO failing tests** (box at load ~70–96 on 24 cores from other sessions). Coverage was assembled as **22 of 29 targets in one gate run + the remaining 7 run directly** (1014 passed, floor sum 940), plus node 1449/1449. 🔴 **The nix sandbox tier was never run on the #1430 tree.**
- Post-merge, `origin/main` content re-run: **99 passed** (`test_fans_detail` + `test_i3status_fans`).

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
1. **Optional: give the case fan an RPM floor in the pill.** Currently `--fan 'Case fan=3'` (display-only, can never alarm), so a DEAD case fan is invisible. Measured this session: it is pinned at 60% and never varies, so it should never read 0 — a floor of ~600 would be safe TODAY. One-line change to `fanArgs` in `nix/graphical.nix` + a test.
   🔴 **Conditional, and the condition is rank 2:** if a BIOS curve with a zero-RPM idle region is ever set, this floor becomes a false alarm. Do NOT do this before deciding rank 2.
   forcing: none — nothing external is asking; it closes a real blind spot but the current config is honest.
2. **Optional (operator-only): set a real SYS_FAN3 curve in the MSI BIOS** for idle noise. Nothing in this repo can do it, and nothing depends on it.
   forcing: none.
3. **Optional: delta re-audit of #1430 against `823ab95f`.** The ladder's stop rule says a round that produced findings gets another, and round 1 produced 4 should-fix + 5 nits. The fix round rewrote one claim in three places and touched key handling twice — the shape most likely to carry the next finding. ⚠ #1430 is already MERGED, so this would be a post-merge audit of `main`.
   forcing: none — merged and verified live; this is diligence, not a blocker.
4. **Optional: `rescue/untracked-workbench-2026-09-08`** still exists on origin. It holds two untracked drafts removed to unblock `ship.sh`. `git diff origin/main..rescue/untracked-workbench-2026-09-08`, then delete the branch if superseded.
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

- **Rank 1 was completed by the OPERATOR, not by an agent: `sudo bash nix/system/apply-nct6683-module.sh` ran at 2026-09-08 14:18:33.** Carried here from `State now` because it is a durable fact under a REPLACE heading. Evidence: `/etc/nixos/configuration.nix:421` reads `boot.kernelModules = [ "i2c-dev" "vfio_pci" "vfio" "vfio_iommu_type1" "nct6683" ];`, `/etc/modules-load.d/nixos.conf` carries `nct6683`, and `/etc/nixos/configuration.nix.bak-nct6683-20260908-141833` is that script's own backup format. Re-running is idempotent ("Already present in boot.kernelModules").
- 🔴 **`home.file` gives EACH file its own /nix/store path**, so a deployed script is a symlink to a *file* directly in `/nix/store`. `Path(__file__).resolve().parent` is therefore `/nix/store` itself, NOT a directory holding the sibling. Use `os.path.dirname(os.path.abspath(__file__))` — the repo idiom (`i3status-clawgate` → `bar_freshness.py`). This shipped once and rendered "sibling did not load" on a correctly-deployed host while all 24 tests passed, because tests run from the repo where the files genuinely ARE siblings.
- 🔴 **A `.pyc` written into `~/.config/i3status-rust/scripts` outlives every same-size redeploy.** Every store path has `mtime=1` and CPython validates bytecode on source **mtime-seconds + size**. Verified three ways, including reproducing it: same-size edit + `touch -d @1` → the import returned the OLD value. Two such files from July are still being honoured today (they happen to compile identically, so nothing wrong is executing). `sys.dont_write_bytecode` is why fans-detail is on the right side of this; it is now pinned by a test with a positive control.
- 🔴 **`sensors`' pwm percentage is NOT sysfs's.** sysfs `pwmN` is raw 0–255; unscaled it renders a pump at "201%". The earlier handoff note "pwm1: 114%" came from `sensors` and is not what sysfs reports.
- 🔴 **The pill and the cooling view share ONE `fanArgs` binding** in `nix/graphical.nix`, passed verbatim to both. An audit measured the earlier duplicated config drifting with **84 of 84 tests green**, producing a pill reading `2448·1650 Idle` beside a view reading `AIO pump ? unreadable`, and (worse) a view saying "Super I/O NOT FOUND, run the sudo script" — an actively wrong diagnosis — beside a healthy pill.
- 🔴 **cbreak alone leaves ISIG on**, so Ctrl-C becomes a SIGINT that only arrives if the process is in the tty's FOREGROUND PROCESS GROUP — true under alacritty, not under a bare pty. `fans-detail` clears ISIG so the byte is readable and the footer's promise holds everywhere.
- 🔴 **A NEAR-MISS worth remembering: `git fetch` does not move your local branch head.** A worktree still at `9ae4fdb4` was merged with `origin/main`; the result silently DROPPED a subagent's 170 lines pushed in between. Only the non-fast-forward push rejection stopped it landing. **Before merging main into a feature branch, check the branch head against `origin/<branch>`, not just that you fetched.**
- **No clawgate task was recorded** — `clawgate_handoff.sh resolve` exited 5 (0 tasks). Its positive control answered 11 links for a different session, so the board is reachable; but a wrong session id also answers 200 with an empty array, so this is NOT a clean bill of health.
- The shared checkout `~/workspace/devrc` is routinely moved onto other sessions' branches mid-work (observed twice: `main`, then `feat/audit-pr-round-0-algorithm`). Do all writing in your own worktree.

## How to verify
1. **The pill** — `~/.config/i3status-rust/scripts/i3status-fans --fan 'AIO pump=1:500' --fan 'Case fan=3'` → `{"icon":"refresh","text":"2708·1652",…,"state":"Idle"}`.
2. **The cooling view** — `~/.config/i3status-rust/scripts/fans-detail --dump --fan 'AIO pump=1:500' --fan 'Case fan=3'` → pump/case rows with PWM gauges, then CPU/GPU/board/disk temps.
3. **The keys** (the footer is a claim) — run it without `--dump` in a terminal; `q`, `Esc` and `Ctrl-C` must each close it, and an unrelated key must NOT.
4. **The pill is actually on the bar** — `stat -c '%y' ~/.config/i3status-rust/config-top.toml` and `ps -eo pid,lstart,args | grep '/bin/i3status-rs' | grep -v grep`; the bar must have STARTED AFTER the config mtime, else `i3-msg restart`.
5. **Rank 1 (driver) landed** — `grep -x nct6683 /etc/modules-load.d/nixos.conf` → exit 0. 🔴 **NOT `lsmod`**: the module is loaded by hand too, so lsmod cannot tell persistence from a live modprobe.
6. **No bytecode leak** — `ls ~/.config/i3status-rust/scripts/__pycache__/ | grep -c i3status-fans` → `0`.
7. **Suites** — `nix develop ~/workspace/devrc -c python3 -m pytest scripts/tests/test_fans_detail.py scripts/tests/test_i3status_fans.py scripts/tests/test_nct6683_apply.py -q` → 129 passed.
## Run this first — the index, one command
```bash
cairn recall --repo /home/zach/workspace/devrc
```
Terse pointers this doc does not carry, curated by past sessions and outliving it.
🔴 RECALL, NOT LIVE OBSERVATION — every line is a pointer to VERIFY, never a current
reading, and it may describe a gotcha already fixed. `scope-absent`/`scope-empty` means
nothing is recorded yet: ordinary, not an error, and not a clean bill of health.
Non-blocking: if it exits non-zero, print the stderr line and carry on.
## Open investigations — live diagnosis state

### Is any fan / AIO tuning warranted? — ANSWERED, no action possible from Linux
- **Symptom + exact repro:** operator asked whether fan or AIO tuning makes sense. Reproduce the readings with `bash -c 'H=/sys/class/hwmon/hwmon11; for i in $(seq 6); do echo "$(cat /sys/class/hwmon/hwmon6/temp1_input) $(cat $H/fan1_input) $(cat $H/pwm1) $(cat $H/fan3_input) $(cat $H/pwm3)"; sleep 5; done'` (chip is found by NAME — `hwmon11` is not stable).
- **Observed (with values):** under load **96.49 on 24 cores** (4x oversubscribed, sustained): `Tctl` 81–83 °C, `Tccd1/2` 70–82 °C, pump 2678–2843 rpm at `pwm1` 226–228 (~89%), case fan 1641–1751 rpm at `pwm3` **153 (60%) in every single sample**. VRM 36–48 °C, GPU edge 49 °C, NVMe ≤56 °C. Tjmax ≈ 95 °C.
- **Ruled out: a cooling deficiency.** At 4x oversubscription Tctl sits at 81 °C, ~14 °C below the 95 °C limit — the CPU is hitting its POWER ceiling, not its thermal one, so a better cooler buys no performance. via: measurement
- **Ruled out: any Linux-side fan control.** `nct6683` exposes PWM READ-ONLY — no `pwm*_enable` files exist, `pwm1` is not writable, and `modinfo nct6683` lists exactly one parameter, `force` ("Set to one to enable support for unknown vendors"), which is a vendor-detection override and NOT a write-enable. So a fan-curve daemon is impossible; tuning is a BIOS-only act. via: command
- **Ruled out: the pump needs attention.** `pwm1` moved 201 → 223 → 228 across the session as load rose, i.e. it tracks temperature. MSI overdrives pump headers by design. via: measurement
- **Leading hypothesis:** the case fan is on a **flat 60% curve**, not a temperature curve — `pwm3` never left 153 across loads 60→96 and temps 71→83 °C. The available win is NOISE at idle, not cooling.
- **Next probe:** none from Linux — it is a BIOS change (SYS_FAN3 curve, e.g. ~30% under 60 °C ramping to 100% by 85 °C). If the operator sets one, re-measure `pwm3` at idle before considering rank 2 below.
