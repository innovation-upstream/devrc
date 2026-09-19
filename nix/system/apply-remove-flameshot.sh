#!/usr/bin/env bash
# Remove flameshot from the system profile (replaced by ksnip, home-manager).
#
# ksnip lives in devrc's nix/graphical.nix home.packages and carries the i3
# keybindings; flameshot is system-level and v14's supported capture path
# (xdg-desktop-portal) wedges on i3, so the binary only works through its
# deprecated legacy toggle. This drops it from defaultPackages and from the
# dmenu include list. Idempotent: exits 0 if already removed.
#
# Run as root (or sudo bash):
#   sudo bash nix/system/apply-remove-flameshot.sh
set -euo pipefail

CFG="/etc/nixos/configuration.nix"

if ! grep -q '^[[:space:]]*flameshot$' "$CFG"; then
  echo "Already removed — skipping"
  exit 0
fi

BAK="$CFG.bak-remove-flameshot-$(date +%Y%m%d-%H%M%S)"
cp "$CFG" "$BAK"
echo "Backed up to $BAK"

# [1/3] drop the standalone package line (only lines that are exactly the name)
sed -i '/^[[:space:]]*flameshot$/d' "$CFG"

# [2/3] drop it from the dmenu include list, if listed
if grep -q 'I3CONFIG_DMENU_INCLUDE=.*flameshot' "$CFG"; then
  sed -i '/I3CONFIG_DMENU_INCLUDE=/s/ flameshot//' "$CFG"
fi

# [3/3] verify: no flameshot reference may remain in the live config
if grep -n 'flameshot' "$CFG"; then
  echo "ERROR: flameshot still referenced — restoring backup"
  cp "$BAK" "$CFG"
  exit 1
fi
echo "Removed flameshot from defaultPackages and dmenu include list"

echo "Rebuilding..."
nixos-rebuild switch

if [ -e /run/current-system/sw/bin/flameshot ]; then
  echo "NOTE: flameshot still in the new system profile (unexpected) — check defaultPackages"
else
  echo "Done. flameshot gone from the system profile; ksnip (home-manager) is the screenshot tool."
fi
