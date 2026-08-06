#!/usr/bin/env bash
# Add UDP 443 fallback for prod lighthouse in nebula static host map
#
# This repo is PUBLIC, so the lighthouse's public IP is NOT committed. Supply it:
#   sudo NEBULA_LIGHTHOUSE=<lighthouse public IP> bash nix/system/apply-nebula-443.sh
# (read it out of the existing staticHostMap in /etc/nixos/configuration.nix, or from
#  the `server:` URL in $KC_PROD).
set -euo pipefail

CFG="/etc/nixos/configuration.nix"
LH="${NEBULA_LIGHTHOUSE:?set NEBULA_LIGHTHOUSE to the nebula lighthouse public IP}"

if grep -q "${LH}:443" "$CFG"; then
  echo "Already configured — skipping"
  exit 0
fi

cp "$CFG" "$CFG.bak-nebula443"
sed -i "s|\"10.42.0.2\" = \[ \"${LH}:4242\" \];|\"10.42.0.2\" = [ \"${LH}:4242\" \"${LH}:443\" ];|" "$CFG"

if grep -q "${LH}:443" "$CFG"; then
  echo "[1/2] Added ${LH}:443 to staticHostMap"
else
  echo "ERROR: sed failed"
  cp "$CFG.bak-nebula443" "$CFG"
  exit 1
fi

echo "[2/2] Rebuilding..."
nixos-rebuild switch
echo "Done. Nebula will try UDP 443 as fallback if 4242 is blocked."
