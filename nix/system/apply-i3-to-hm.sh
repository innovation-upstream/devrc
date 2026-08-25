#!/usr/bin/env bash
# Cutover i3 from NixOS-managed /etc/i3.conf to home-manager ~/.config/i3/config
#
# The NixOS config forces `i3 -c /etc/i3.conf` via:
#   windowManager.i3.configFile = "/etc/i3.conf";
#   environment.etc."i3.conf".text = import ./i3config.nix;
#
# Home-manager writes ~/.config/i3/config (nix/graphical.nix) with the full
# config including espanso floating, rig-control floats, i3status-rust, etc.
# But i3 never reads it because the NixOS module passes -c explicitly.
#
# This script removes the NixOS-level i3 config and lets i3 fall back to its
# default search path (~/.config/i3/config). Requires sudo nixos-rebuild.
#
#   sudo bash nix/system/apply-i3-to-hm.sh
set -euo pipefail

CFG=/etc/nixos/configuration.nix
[[ $EUID -eq 0 ]] || { echo "must run as root (sudo bash $0)"; exit 1; }
[[ -f $CFG ]] || { echo "missing $CFG"; exit 1; }

BAK="$CFG.bak-i3-hm-$(date +%Y%m%d-%H%M%S)"
cp -a "$CFG" "$BAK"
echo "[0/4] backed up $CFG -> $BAK"

restore_on_err() {
  local rc=$?
  echo "ERROR (rc=$rc) — restoring $CFG from $BAK" >&2
  cp -a "$BAK" "$CFG"
  exit "$rc"
}
trap restore_on_err ERR

# ---- 1. Remove configFile = "/etc/i3.conf" from windowManager.i3 ----------
if grep -q 'configFile.*=.*"/etc/i3.conf"' "$CFG"; then
  sed -i '/configFile.*=.*"\/etc\/i3\.conf"/d' "$CFG"
  grep -q 'configFile.*=.*"/etc/i3.conf"' "$CFG" && { echo "ERROR: configFile edit did not apply"; false; }
  echo "[1/4] removed windowManager.i3.configFile"
else
  echo "[1/4] configFile already absent — skipping"
fi

# ---- 2. Remove environment.etc."i3.conf" (the system-level config text) ---
if grep -q 'environment\.etc\."i3\.conf"' "$CFG"; then
  # This is a multi-line attr: text = import ./i3config.nix; — remove the whole block.
  # Match from the etc line through the closing semicolon.
  sed -i '/environment\.etc\."i3\.conf"/,/;$/d' "$CFG"
  grep -q 'environment\.etc\."i3\.conf"' "$CFG" && { echo "ERROR: i3.conf etc edit did not apply"; false; }
  echo "[2/4] removed environment.etc.\"i3.conf\""
else
  echo "[2/4] environment.etc.\"i3.conf\" already absent — skipping"
fi

# ---- 3. Remove environment.etc."i3blocks.conf" (companion) ----------------
if grep -q 'environment\.etc\."i3blocks\.conf"' "$CFG"; then
  sed -i '/environment\.etc\."i3blocks\.conf"/,/;$/d' "$CFG"
  echo "[3/4] removed environment.etc.\"i3blocks.conf\""
else
  echo "[3/4] environment.etc.\"i3blocks.conf\" already absent — skipping"
fi

# ---- 4. Validate ----------------------------------------------------------
echo "[4/4] parsing $CFG ..."
nix-instantiate --parse "$CFG" >/dev/null
echo "[4/4] parse OK"

trap - ERR

echo
echo "Review the diff before applying:"
echo "    diff -u $BAK $CFG"
echo

if [[ -t 0 ]]; then
  read -r -p "Run 'nixos-rebuild switch' now? [y/N] " ans || ans=n
else
  ans=n
  echo "(non-interactive stdin — skipping the rebuild prompt)"
fi

if [[ ${ans:-n} =~ ^[Yy]$ ]]; then
  if ! nixos-rebuild switch; then
    echo
    echo "nixos-rebuild FAILED. The config edits are still in place." >&2
    echo "Rollback: cp -a $BAK $CFG && nixos-rebuild switch" >&2
    exit 1
  fi
else
  echo "Config edited and validated; nixos-rebuild NOT run. Run it when ready:"
  echo "    sudo nixos-rebuild switch"
fi

echo
echo "After rebuild, reload i3 (Mod+Shift+r) or log out/in."
echo "Verify: i3-msg -t get_version | jq .loaded_config_file_name"
echo "  expect: ~/.config/i3/config  (was /etc/i3/config)"
echo
echo "Rollback: cp -a $BAK $CFG && sudo nixos-rebuild switch"
