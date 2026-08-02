#!/usr/bin/env bash
# Install the system-level backend needed for a working default file manager.
#
# Approach (robust, avoids fragile sed-address edits):
#   1. cp a complete standalone module to /etc/nixos/file-manager-backend.nix
#   2. add one import line to configuration.nix via awk (line-based, idempotent)
#
# The module enables services.gvfs + programs.dconf and installs
# shared-mime-info / file / desktop-file-utils so Brave's "Show in folder"
# path classification has a working backend. The home-manager layer
# (nix/home.nix xdg.mimeApps) already pins inode/directory -> thunar.desktop.
#
# Run with: sudo bash nix/system/apply-file-manager-backend.sh
set -euo pipefail

CFG=/etc/nixos/configuration.nix
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MODULE_SRC="${SRC_DIR}/file-manager-backend.nix"
MODULE_DST=/etc/nixos/file-manager-backend.nix
IMPORT_LINE='      ./file-manager-backend.nix'
ANCHOR='      ./hardware-configuration.nix'

[ -f "$CFG" ] || { echo "ERROR: $CFG not found"; exit 1; }
[ -f "$MODULE_SRC" ] || { echo "ERROR: module source $MODULE_SRC not found"; exit 1; }

ts=$(date +%Y%m%d-%H%M%S)
cp -v "$CFG" "${CFG}.bak-${ts}"

# 1. Drop the complete module in place (full-file cp, never a surgical edit).
cp -v "$MODULE_SRC" "$MODULE_DST"

# 2. Idempotently add the import line right after the hardware-config import.
if grep -qF "$IMPORT_LINE" "$CFG"; then
  echo "SKIP: import already present in $CFG"
else
  grep -qF "$ANCHOR" "$CFG" || { echo "ERROR: import anchor not found: $ANCHOR"; exit 1; }
  tmp=$(mktemp)
  awk -v anchor="$ANCHOR" -v ins="$IMPORT_LINE" '
    { print }
    $0 == anchor { print ins }
  ' "$CFG" > "$tmp"
  # Sanity: exactly one import line added, file grew by exactly one line.
  before=$(wc -l < "$CFG"); after=$(wc -l < "$tmp")
  if [ "$after" -ne "$((before + 1))" ]; then
    echo "ERROR: unexpected line delta ($before -> $after); not applying"; rm -f "$tmp"; exit 1
  fi
  mv "$tmp" "$CFG"
  echo "ADDED import: $IMPORT_LINE"
fi

echo
echo "Validating with a dry build (no activation)..."
nixos-rebuild dry-build 2>&1 | tail -5

echo
echo "Backup saved at ${CFG}.bak-${ts}"
echo "If the dry build looks good, apply with:  sudo nixos-rebuild switch"
echo "Then verify in Brave: download something -> 'Show in folder' should open Thunar."
