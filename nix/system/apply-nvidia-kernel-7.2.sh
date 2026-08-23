#!/usr/bin/env bash
# Unbreak nixos-rebuild after the channel jumped to kernel 7.2.
#
#   Symptom: nvidia-open-595.45.04-7.2 fails with
#            nv-linux.h:1730:10: fatal error: linux/of_gpio.h: No such file or directory
#   Cause:   /etc/nixos/configuration.nix pins hardware.nvidia.package to
#            nvidiaPackages.beta, which in the current channel is 595.45.04 —
#            OLDER than stable/production (595.91.07). 595.45.04 includes
#            <linux/of_gpio.h> unconditionally; kernel 7.2 no longer ships it.
#   Fix:     move off the beta branch. 595.91.07 (production/stable) and
#            610.57.04 (latest) both build against 7.2 and are prebuilt on
#            cache.nixos.org — verified 2026-08-19 by building
#            linuxPackages_latest.nvidiaPackages.{production,latest}.open
#            against root's channel (kernel 7.2); both substituted, no compile.
#
# Run with:  sudo bash nix/system/apply-nvidia-kernel-7.2.sh
# Or pick a different branch:
#            sudo NVIDIA_BRANCH=latest bash nix/system/apply-nvidia-kernel-7.2.sh
set -euo pipefail

CFG="/etc/nixos/configuration.nix"
BRANCH="${NVIDIA_BRANCH:-production}"

case "$BRANCH" in
  production|stable|latest|beta) ;;
  *) echo "ERROR: NVIDIA_BRANCH must be one of production|stable|latest|beta (got '$BRANCH')" >&2; exit 2 ;;
esac

ACTIVE_RE='^[[:space:]]*package[[:space:]]*=[[:space:]]*config\.boot\.kernelPackages\.nvidiaPackages\.[a-z_0-9]+;'

n=$(grep -cE "$ACTIVE_RE" "$CFG" || true)
if [ "$n" -ne 1 ]; then
  echo "ERROR: expected exactly 1 uncommented nvidiaPackages line in $CFG, found $n." >&2
  echo "       Edit it by hand — this script will not guess which one to change." >&2
  exit 1
fi

current=$(grep -oE "$ACTIVE_RE" "$CFG" | grep -oE '[a-z_0-9]+;$' | tr -d ';')
if [ "$current" = "$BRANCH" ]; then
  echo "Already on nvidiaPackages.$BRANCH — no config change needed."
else
  BAK="$CFG.bak-nvidia-$(date +%Y%m%d-%H%M%S)"
  cp "$CFG" "$BAK"
  sed -i -E "s|^([[:space:]]*package[[:space:]]*=[[:space:]]*config\.boot\.kernelPackages\.nvidiaPackages\.)[a-z_0-9]+;|\1${BRANCH};|" "$CFG"

  now=$(grep -oE "$ACTIVE_RE" "$CFG" | grep -oE '[a-z_0-9]+;$' | tr -d ';')
  if [ "$now" != "$BRANCH" ]; then
    echo "ERROR: sed did not take (still '$now') — restoring $BAK" >&2
    cp "$BAK" "$CFG"
    exit 1
  fi
  echo "[1/2] hardware.nvidia.package: $current -> $BRANCH   (backup: $BAK)"
fi

echo "[2/2] Rebuilding..."
nixos-rebuild switch

echo
echo "Done. The new driver only takes effect after a REBOOT (kernel module)."
echo "Check after reboot:  nvidia-smi --query-gpu=driver_version --format=csv,noheader"
echo "Roll back without rebooting into it:  nixos-rebuild switch --rollback"
