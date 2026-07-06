#!/usr/bin/env bash
# Stage the "rig controls" i3blocks button into /etc/nixos.
# Run with: sudo bash nix/system/apply-rig-controls.sh
#
# Idempotent + drift-safe: it APPENDS to the live i3blocks.nix / i3config.nix only
# if the entries are missing, so it never clobbers live-only changes. It does NOT
# run nixos-rebuild — review the diffs first, then rebuild yourself.
set -euo pipefail

DEVRC_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
NIXOS_DIR="/etc/nixos"
SCRIPTS_DIR="$NIXOS_DIR/i3blocks-scripts"
BLOCKS="$NIXOS_DIR/i3blocks.nix"
I3CONF="$NIXOS_DIR/i3config.nix"

echo "=== Staging rig-controls into $NIXOS_DIR ==="

# 1. Install the launcher block script (plain dir, no rebuild needed for this part).
echo "[1/3] Installing i3blocks-scripts/rigcontrol..."
install -m 0755 "$DEVRC_DIR/scripts/i3blocks-rigcontrol" "$SCRIPTS_DIR/rigcontrol"

# 2. Add the [rigcontrol] block to i3blocks.nix (insert before the closing '' line).
echo "[2/3] Ensuring [rigcontrol] block in i3blocks.nix..."
if grep -q '\[rigcontrol\]' "$BLOCKS"; then
  echo "      already present — skipping"
else
  tmp="$(mktemp)"
  head -n -1 "$BLOCKS" > "$tmp"
  cat >> "$tmp" <<'BLOCK'

[rigcontrol]
command=$SCRIPT_DIR/rigcontrol
interval=once
BLOCK
  tail -n 1 "$BLOCKS" >> "$tmp"   # re-append the closing ''
  install -m 0644 "$tmp" "$BLOCKS"
  rm -f "$tmp"
fi

# 3. Add the yad float rule to i3config.nix (insert before the closing '' line).
echo "[3/3] Ensuring yad float rule in i3config.nix..."
if grep -q 'title="Rig Controls"' "$I3CONF"; then
  echo "      already present — skipping"
else
  tmp="$(mktemp)"
  head -n -1 "$I3CONF" > "$tmp"
  cat >> "$tmp" <<'RULE'

# Float the rig-control (yad) popup as a compact centered window instead of tiling it
for_window [class="Yad" title="Rig Controls"] floating enable, move position center
RULE
  tail -n 1 "$I3CONF" >> "$tmp"
  install -m 0644 "$tmp" "$I3CONF"
  rm -f "$tmp"
fi

cat <<'DONE'

=== Staged. Next steps (review, then apply): ===
  1. sudo diff -u <(git -C ~/workspace/devrc show HEAD:nix/system/i3blocks.nix) /etc/nixos/i3blocks.nix   # sanity-check
  2. sudo nixos-rebuild switch
  3. i3-msg restart          # reload the bar + float rule (saves/restores your layout)

A new ⚙ block appears on the bar — left-click it to open the Rig Controls panel.
DONE
