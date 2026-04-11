#!/usr/bin/env bash
# Task B: add brightnessctl + XF86MonBrightness keybindings to the LAPTOP.
#
# Edits the currently-deployed /etc/nixos/{configuration,i3config}.nix in place.
# Idempotent: re-running is safe (checks for existing changes before inserting).
# Run: sudo bash nix/system/apply-brightness.sh
set -euo pipefail

NIXOS_DIR="/etc/nixos"
CFG="$NIXOS_DIR/configuration.nix"
I3C="$NIXOS_DIR/i3config.nix"

echo "=== Task B: Brightness fix ==="

# Back up originals (timestamped)
TS=$(date +%Y%m%d-%H%M%S)
cp "$CFG" "$CFG.bak-$TS"
cp "$I3C" "$I3C.bak-$TS"
echo "[backup] Saved *.bak-$TS"

# 1. Add brightnessctl to environment.systemPackages
if grep -q "^    brightnessctl$" "$CFG"; then
  echo "[1/3] brightnessctl already in systemPackages — skipping"
else
  # Insert after the "acpi" line in systemPackages
  sed -i "/^  environment\.systemPackages = with pkgs; \[$/,/^  \];$/ { /^    acpi$/a\\
    brightnessctl
  }" "$CFG"
  echo "[1/3] Added brightnessctl to environment.systemPackages"
fi

# 2. Add XF86MonBrightness bindings to i3config.nix after XF86AudioMicMute line
if grep -q "XF86MonBrightnessUp" "$I3C"; then
  echo "[2/3] Brightness keybindings already present — skipping"
else
  sed -i "/^bindsym XF86AudioMicMute/a\\
\\
# Brightness (laptop backlight, 5% steps; Shift for 1% fine control)\\
bindsym XF86MonBrightnessUp exec --no-startup-id brightnessctl set +5%\\
bindsym XF86MonBrightnessDown exec --no-startup-id brightnessctl set 5%-\\
bindsym Shift+XF86MonBrightnessUp exec --no-startup-id brightnessctl set +1%\\
bindsym Shift+XF86MonBrightnessDown exec --no-startup-id brightnessctl set 1%-" "$I3C"
  echo "[2/3] Added brightness keybindings to i3config.nix"
fi

# 3. Rebuild system
echo "[3/3] Running nixos-rebuild switch..."
nixos-rebuild switch

echo ""
echo "=== Done ==="
echo "Test with: brightnessctl set +5%   (or press Fn + brightness key)"
echo "Reload i3 so new bindings take effect: i3-msg reload"
