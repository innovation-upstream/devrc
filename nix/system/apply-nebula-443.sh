#!/usr/bin/env bash
# Add UDP 443 fallback for prod lighthouse in nebula static host map
# Run with: sudo bash nix/system/apply-nebula-443.sh
set -euo pipefail

NIXOS_DIR="/etc/nixos"
CONFIG="$NIXOS_DIR/configuration.nix"
BACKUP="$CONFIG.bak.nebula-443"

echo "=== Adding nebula UDP 443 fallback for prod lighthouse ==="

# 1. Backup configuration.nix
echo "[1/3] Backing up $CONFIG to $BACKUP..."
cp "$CONFIG" "$BACKUP"

# 2. Add 5.161.118.55:443 as fallback endpoint for prod lighthouse (10.42.0.2)
echo "[2/3] Adding 5.161.118.55:443 to staticHostMap for 10.42.0.2..."
if grep -q '5.161.118.55:443' "$CONFIG"; then
    echo "  -> Already present, skipping."
else
    sed -i 's|"10.42.0.2" = \[ "5.161.118.55:4242" \];|"10.42.0.2" = [ "5.161.118.55:4242" "5.161.118.55:443" ];|' "$CONFIG"
    if grep -q '5.161.118.55:443' "$CONFIG"; then
        echo "  -> Successfully added."
    else
        echo "  -> ERROR: sed replacement did not match. Check configuration.nix manually."
        echo "  -> Restoring backup..."
        cp "$BACKUP" "$CONFIG"
        exit 1
    fi
fi

# 3. Rebuild NixOS
echo "[3/3] Running nixos-rebuild switch..."
nixos-rebuild switch

echo ""
echo "=== Done! ==="
echo "Nebula will now try 5.161.118.55:4242 first, then fall back to :443."
echo "Backup saved at: $BACKUP"
