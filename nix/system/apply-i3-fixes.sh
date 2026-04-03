#!/usr/bin/env bash
# Apply i3 DX improvements to /etc/nixos/
# Run with: sudo bash nix/system/apply-i3-fixes.sh
set -euo pipefail

DEVRC_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
NIXOS_DIR="/etc/nixos"

echo "=== Applying i3 DX improvements to $NIXOS_DIR ==="

# 1. Replace i3config.nix
echo "[1/8] Replacing i3config.nix (vim bindings, rofi, lock, screenshots, 5% volume)..."
cp "$DEVRC_DIR/nix/system/i3config.nix" "$NIXOS_DIR/i3config.nix"

# 2. Add rofi, remove polybar from i3 extraPackages
echo "[2/8] Updating i3 extraPackages (add rofi, remove polybar)..."
sed -i '/dmenu #application launcher/a\        rofi' "$NIXOS_DIR/configuration.nix"
sed -i '/polybar/d' "$NIXOS_DIR/configuration.nix"
sed -i '/#xss-lock/d' "$NIXOS_DIR/configuration.nix"
sed -i '/#betterlockscreen/d' "$NIXOS_DIR/configuration.nix"
sed -i '/#i3status/d' "$NIXOS_DIR/configuration.nix"
# Clean up old comments in extraPackages
sed -i 's/dmenu #application launcher most people use/dmenu/' "$NIXOS_DIR/configuration.nix"
sed -i 's/i3lock #default i3 screen locker/i3lock/' "$NIXOS_DIR/configuration.nix"
sed -i 's/i3blocks #if you are planning on using i3blocks over i3status/i3blocks/' "$NIXOS_DIR/configuration.nix"

# 3. Remove polybar stub config
echo "[3/8] Removing polybar stub config..."
sed -i '/environment.etc."config\/polybar"/,/^  };$/d' "$NIXOS_DIR/configuration.nix"

# 4. Enable xss-lock (replace commented block)
echo "[4/8] Enabling xss-lock for suspend protection..."
sed -i '/#programs.xss-lock = {/,/#};/c\  programs.xss-lock = {\n    enable = true;\n    lockerCommand = "i3lock -c 282828";\n  };' "$NIXOS_DIR/configuration.nix"

# 5. Remove dunst from systemPackages (home-manager handles it now)
echo "[5/8] Removing dunst from systemPackages (managed by home-manager)..."
sed -i '/^    dunst$/d' "$NIXOS_DIR/configuration.nix"

# 6. Remove I3CONFIG_DMENU_INCLUDE (rofi doesn't need whitelist)
echo "[6/8] Removing I3CONFIG_DMENU_INCLUDE..."
sed -i '/I3CONFIG_DMENU_INCLUDE/d' "$NIXOS_DIR/configuration.nix"

# 7. Replace i3blocks.nix (adds VPN block)
echo "[7/8] Replacing i3blocks.nix..."
cp "$DEVRC_DIR/nix/system/i3blocks.nix" "$NIXOS_DIR/i3blocks.nix"

# 8. Deploy i3blocks scripts from devrc
echo "[8/8] Deploying i3blocks scripts..."
SCRIPTS_DIR="$NIXOS_DIR/i3blocks-scripts"
cp "$DEVRC_DIR/scripts/i3blocks-vpn" "$SCRIPTS_DIR/vpn"
cp "$DEVRC_DIR/scripts/i3blocks-vpn-detail" "$SCRIPTS_DIR/vpn-detail"
cp "$DEVRC_DIR/scripts/i3blocks-vpn-sudo" "$SCRIPTS_DIR/vpn-sudo"
chmod +x "$SCRIPTS_DIR/vpn" "$SCRIPTS_DIR/vpn-detail" "$SCRIPTS_DIR/vpn-sudo"

echo ""
echo "=== Done! Next steps: ==="
echo "  1. Review: sudo diff /etc/nixos/configuration.nix"
echo "  2. sudo nixos-rebuild switch"
echo "  3. Mod+Shift+R   (reload i3)"
