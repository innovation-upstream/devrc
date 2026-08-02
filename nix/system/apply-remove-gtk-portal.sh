#!/usr/bin/env bash
# Remove GTK_USE_PORTAL=1 from /etc/nixos/configuration.nix.
#
# Why: GTK_USE_PORTAL=1 tells Brave/Chromium and GTK apps to render
# Save/Open/Upload dialogs via xdg-desktop-portal's FileChooser. No portal is
# installed on this i3/X11 host, so the flag drops apps to a bare built-in
# picker. Removing it lets Chromium/GTK use their in-process GTK3 chooser
# directly (places sidebar, recent, bookmarks) with no portal daemon needed.
# XDG_CURRENT_DESKTOP is intentionally kept.
#
# Idempotent: re-running after removal is a no-op (SKIP).
# Robust: line-based awk filter + sanity check on exactly one line removed.
#
# Run with: sudo bash nix/system/apply-remove-gtk-portal.sh
set -euo pipefail

CFG=/etc/nixos/configuration.nix
[ -f "$CFG" ] || { echo "ERROR: $CFG not found"; exit 1; }

if ! grep -q 'GTK_USE_PORTAL' "$CFG"; then
  echo "SKIP: GTK_USE_PORTAL already absent from $CFG"
  exit 0
fi

n=$(grep -c 'GTK_USE_PORTAL' "$CFG")
if [ "$n" -ne 1 ]; then
  echo "ERROR: expected exactly 1 GTK_USE_PORTAL line, found $n; not editing"; exit 1
fi

ts=$(date +%Y%m%d-%H%M%S)
cp -v "$CFG" "${CFG}.bak-${ts}"

tmp=$(mktemp)
awk '!/GTK_USE_PORTAL/ { print }' "$CFG" > "$tmp"

before=$(wc -l < "$CFG"); after=$(wc -l < "$tmp")
if [ "$after" -ne "$((before - 1))" ]; then
  echo "ERROR: unexpected line delta ($before -> $after); not applying"; rm -f "$tmp"; exit 1
fi
mv "$tmp" "$CFG"
echo "REMOVED: GTK_USE_PORTAL line"

echo
echo "Validating with a dry build (no activation)..."
nixos-rebuild dry-build 2>&1 | tail -5

echo
echo "Backup saved at ${CFG}.bak-${ts}"
echo "If the dry build looks good, apply with:  sudo nixos-rebuild switch"
echo "Then LOG OUT and back into i3 so the removed env var clears from the session,"
echo "quit Brave fully, reopen, and test: right-click image -> Save image as."
