#!/usr/bin/env bash
# Task C: Deploy modernized i3 config on the laptop.
#
# Replaces /etc/nixos/i3config.nix with the staged nix/system/i3config.nix
# (rofi launcher, vim hjkl, flameshot, i3lock, Gruvbox bar, brightness keys).
# Adds rofi to environment.systemPackages.
#
# Prerequisite: Task B (apply-brightness.sh) should be applied first to add
# brightnessctl. This script will still run without it, but the brightness
# keybindings in the new i3 config will be no-ops until brightnessctl is installed.
#
# Run: sudo bash nix/system/apply-i3-modern.sh
set -euo pipefail

DEVRC_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
NIXOS_DIR="/etc/nixos"
CFG="$NIXOS_DIR/configuration.nix"
I3C="$NIXOS_DIR/i3config.nix"
STAGED_I3C="$DEVRC_DIR/nix/system/i3config.nix"

echo "=== Task C: i3 modernization ==="

# Sanity checks
if [ ! -f "$STAGED_I3C" ]; then
  echo "ERROR: staged i3config not found: $STAGED_I3C" >&2
  exit 1
fi
if ! grep -q "brightnessctl" "$CFG"; then
  echo "WARNING: brightnessctl not in systemPackages — brightness keybindings will not work until Task B is applied."
fi

# Back up originals
TS=$(date +%Y%m%d-%H%M%S)
cp "$CFG" "$CFG.bak-$TS"
cp "$I3C" "$I3C.bak-$TS"
echo "[backup] Saved $CFG.bak-$TS and $I3C.bak-$TS"

# 1. Deploy modernized i3config.nix
echo "[1/3] Replacing i3config.nix with staged version..."
cp "$STAGED_I3C" "$I3C"

# 2. Add rofi to environment.systemPackages (idempotent, single-line insert)
if grep -q "^    rofi$" "$CFG"; then
  echo "[2/3] rofi already in systemPackages — skipping"
else
  sed -i "/^  environment\.systemPackages = with pkgs; \[$/,/^  \];$/ { /^    acpi$/a\\
    rofi
  }" "$CFG"
  echo "[2/3] Added rofi to environment.systemPackages"
fi

# 3. Rebuild system
echo "[3/3] Running nixos-rebuild switch..."
nixos-rebuild switch

echo ""
echo "=== Done ==="
echo "Reload i3 from your own shell (not sudo): i3-msg reload"
echo ""
echo "Test new bindings:"
echo "  Mod+d           rofi launcher"
echo "  Mod+h/j/k/l     vim-style focus"
echo "  Mod+Shift+x     i3lock"
echo "  Print           flameshot gui"
echo "  Mod+b           brave"
echo "  Mod+s           dictate"
echo ""
echo "Rollback if needed: sudo cp $CFG.bak-$TS $CFG && sudo cp $I3C.bak-$TS $I3C && sudo nixos-rebuild switch"
