#!/usr/bin/env bash
# Persist the `nct6683` hwmon driver across reboots.
#
#   What:    adds "nct6683" to boot.kernelModules in /etc/nixos/configuration.nix
#            (today line ~420, but this script MATCHES the assignment, never a
#            line number — the file moves).
#   Why:     the workbench's Super I/O is a Nuvoton NCT6687D (MSI X670E GAMING
#            PLUS WIFI, MS-7E16), probed by `sensors-detect --auto` at ISA
#            0x0a20 via 0x4e/0x4f. It is the ONLY source of the AIO pump and
#            case-fan RPMs, and nothing loads it automatically — it has been
#            live only because a session ran `modprobe nct6683` by hand, which
#            does not survive a reboot.
#   NOT nct6775: that driver returns "no such device" for this chip. The board's
#            spec page says NCT6798D; the probe says NCT6687D. Believe the probe.
#
# 🔴 The consumer is the status bar. `nix/graphical.nix`'s `fansBlock` renders
#    /sys/class/hwmon/*/fan{1,3}_input from this chip; without the module the
#    pill renders `?` — deliberately visible, so a reboot that loses the driver
#    announces itself rather than showing a silently blank block.
#
# Run with:  sudo bash nix/system/apply-nct6683-module.sh
# Dry run:   sudo NCT_DRY_RUN=1 bash nix/system/apply-nct6683-module.sh
#            (edits nothing, rebuilds nothing — prints the line it WOULD write)
set -euo pipefail

CFG="/etc/nixos/configuration.nix"
MOD="nct6683"
LOADCONF="/etc/modules-load.d/nixos.conf"
DRY="${NCT_DRY_RUN:-}"

[ -r "$CFG" ] || { echo "ERROR: cannot read $CFG (run under sudo)" >&2; exit 2; }

# Match the ASSIGNMENT, uncommented, at the start of a line. `[^]]*` keeps this
# anchored to a single-line list — the file has exactly one today, and a
# multi-line reflow should stop this script rather than have it guess.
ASSIGN_RE='^[[:space:]]*boot\.kernelModules[[:space:]]*=[[:space:]]*\[[^]]*\][[:space:]]*;'

n=$(grep -cE "$ASSIGN_RE" "$CFG" || true)
if [ "$n" -ne 1 ]; then
  echo "ERROR: expected exactly 1 single-line boot.kernelModules assignment in $CFG, found $n." >&2
  echo "       (a multi-line list, or a second assignment, needs a human — this script will not guess)" >&2
  exit 1
fi

current=$(grep -E "$ASSIGN_RE" "$CFG")
echo "current: ${current#"${current%%[![:space:]]*}"}"

# Idempotence is decided on the LIST, not on the whole file: the module name
# also appears in comments and in a `boot.blacklistedKernelModules` would mean
# the opposite thing.
if printf '%s\n' "$current" | grep -q "\"$MOD\""; then
  echo "Already present in boot.kernelModules — no config change needed."
else
  if [ -n "$DRY" ]; then
    echo "DRY RUN: would insert \"$MOD\" before the closing ] and run nixos-rebuild switch"
    exit 0
  fi
  BAK="$CFG.bak-nct6683-$(date +%Y%m%d-%H%M%S)"
  cp "$CFG" "$BAK"
  # Insert before the closing bracket of THAT line only.
  sed -i -E "s|^([[:space:]]*boot\.kernelModules[[:space:]]*=[[:space:]]*\[[^]]*)\][[:space:]]*;|\1\"$MOD\" ];|" "$CFG"

  now=$(grep -E "$ASSIGN_RE" "$CFG" || true)
  if ! printf '%s\n' "$now" | grep -q "\"$MOD\""; then
    echo "ERROR: sed did not take — restoring $BAK" >&2
    cp "$BAK" "$CFG"
    exit 1
  fi
  echo "[1/3] boot.kernelModules += \"$MOD\"   (backup: $BAK)"
  echo "new:     ${now#"${now%%[![:space:]]*}"}"
fi

if [ -n "$DRY" ]; then
  echo "DRY RUN: would run nixos-rebuild switch"
  exit 0
fi

echo "[2/3] Rebuilding..."
nixos-rebuild switch

# 🔴 THE VERIFICATION IS THE RENDERED FILE, NOT `lsmod`. The module is very
# likely ALREADY loaded by hand, so `lsmod | grep nct6683` says yes whether or
# not this change landed — it cannot distinguish "persisted" from "a human
# modprobe'd it in August". /etc/modules-load.d/nixos.conf is what systemd
# replays at every boot, so its content IS the persistence claim.
echo "[3/3] Verifying persistence..."
if grep -qx "$MOD" "$LOADCONF"; then
  echo "OK: $MOD is in $LOADCONF — it will load on every boot."
else
  echo "ERROR: $MOD is NOT in $LOADCONF after the rebuild." >&2
  echo "       The config edit landed but the rebuild did not activate it," >&2
  echo "       or boot.kernelModules is being overridden elsewhere. Do NOT" >&2
  echo "       treat this as applied." >&2
  exit 1
fi

echo
echo "Done. No reboot needed for the module (systemd-modules-load runs at activation),"
echo "but the persistence claim above is what a reboot will honour."
echo "Read the fans:  sensors | grep -A6 nct6687"
echo "Bar pill:       ~/workspace/devrc/scripts/i3status-fans --fan pump=1:500 --fan case=3"
