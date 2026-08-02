#!/usr/bin/env bash
# Add UDP 443 fallback for prod lighthouse in nebula static host map
# Run: sudo bash nix/system/apply-nebula-443.sh
set -euo pipefail

CFG="/etc/nixos/configuration.nix"

if grep -q '5.161.118.55:443' "$CFG"; then
  echo "Already configured — skipping"
  exit 0
fi

cp "$CFG" "$CFG.bak-nebula443"
sed -i 's|"10.42.0.2" = \[ "5.161.118.55:4242" \];|"10.42.0.2" = [ "5.161.118.55:4242" "5.161.118.55:443" ];|' "$CFG"

if grep -q '5.161.118.55:443' "$CFG"; then
  echo "[1/2] Added 5.161.118.55:443 to staticHostMap"
else
  echo "ERROR: sed failed"
  cp "$CFG.bak-nebula443" "$CFG"
  exit 1
fi

echo "[2/2] Rebuilding..."
nixos-rebuild switch
echo "Done. Nebula will try UDP 443 as fallback if 4242 is blocked."
