# Handoff: nct6683 cooling sensors → bar integration

## Goal
Make the AIO pump and case fan RPMs visible in the i3status-rust bar, and persist the `nct6683` kernel module across reboots via NixOS configuration.

## State now
- Branch: `main`
- No PR, no uncommitted changes to tracked files
- `nct6683` module loaded manually (`sudo modprobe nct6683`) — working, shows pump at 2823 RPM and case fan at 1646 RPM
- `/etc/nixos/configuration.nix` line 420 needs `"nct6683"` added to `boot.kernelModules` — user has not run the `sed` + `nixos-rebuild switch` yet
- `sensors-detect` identified the chip as **Nuvoton NCT6687D** at ISA 0xa20 (NOT NCT6798D as initially assumed)

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
1. **Persist nct6683 in NixOS** — run `sudo sed` to add `"nct6683"` to `boot.kernelModules` in `/etc/nixos/configuration.nix` line 420, then `sudo nixos-rebuild switch`. Reboot to verify.
   forcing: user
2. **Add pump/fan RPM to the i3status-rust bar** — create a custom block in the bar config that reads from `sensors` or `/sys/class/hwmon/hwmon*/fan*_input`. Likely use a `command` block that parses `sensors nct6687-isa-0a20` for fan1/fan3 RPMs. Check existing bar config at `nix/i3/` or `nix/graphical.nix` for the i3status-rust block format.
   forcing: user
3. **Consider adding a dunst toast** if pump RPM drops below a threshold (e.g., <500 RPM = pump failure). Low priority.
   forcing: none

## Gotchas / decisions
- The chip is NCT6687D, not NCT6798D — the MSI X670E GAMING PLUS WIFI spec page lists NCT6798D but the actual probe found NCT6687D
- `nct6775` module is the wrong driver for this chip — must use `nct6683`
- `sensors-detect --auto` is the definitive probe; manual `modprobe` guesses won't work
- pwm1 >100% is normal for pump headers — the board overdrives them by design

## How to verify
1. After `nixos-rebuild switch` + reboot: `sensors | grep -A20 nct6687` shows fan1/fan3 RPMs
2. After bar integration: the i3status-rust bar displays pump RPM as a block
