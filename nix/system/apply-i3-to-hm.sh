#!/usr/bin/env bash
# Stage the i3 → home-manager cutover into /etc/nixos/configuration.nix.
# Run with:  sudo bash nix/system/apply-i3-to-hm.sh
#
# This is the ONE sudo moment of the i3status-rust / i3-in-home-manager migration.
# It edits /etc/nixos/configuration.nix so the system STOPS owning the i3 config,
# handing it to home-manager's ~/.config/i3/config (already written by
# `home-manager switch`). Specifically it:
#   1. removes  configFile = "/etc/i3.conf";     (so i3 reads ~/.config/i3/config)
#   2. removes  i3blocks                          from windowManager.i3.extraPackages
#   3. removes  environment.etc."i3.conf".text        assignment
#   4. removes  environment.etc."i3blocks.conf".text  assignment
# It keeps  windowManager.i3.enable = true  and  services.displayManager.defaultSession.
#
# It is IDEMPOTENT, backs up configuration.nix, PRINTS the diff, and STOPS before
# nixos-rebuild — review the diff, then rebuild + restart display-manager yourself (see footer).
#
# --- airvpn-sudo NOPASSWD sudoers rule (deliberately NOT touched) -----------
# The host Mullvad block (and its vpn-sudo helper) is DECOMMISSIONED; the host VPN
# is now AirVPN, driven by scripts/airvpn-{menu,detail} + the poller's `airvpn`
# source. Those invoke a privileged helper at the STABLE, sudoers-trusted path
# /etc/nixos/i3blocks-scripts/airvpn-sudo ($AIRVPN_SUDO_HELPER default). Add this
# NOPASSWD rule to configuration.nix (see nix/system/airvpn-host.nix for the full
# block Zach applies in Phase 2):
#     security.sudo.extraRules = [{
#       users = ["zach"];
#       commands = [{ command = "/etc/nixos/i3blocks-scripts/airvpn-sudo"; options = ["NOPASSWD"]; }];
#     }];
# We do NOT move airvpn-sudo into the nix store: a store path (a) would not match
# this NOPASSWD rule and (b) changes on every rebuild. Copy scripts/airvpn-sudo to
# /etc/nixos/i3blocks-scripts/airvpn-sudo (root-owned, 0755) and keep it there.
# When you clean up /etc/nixos/i3blocks-scripts, PRESERVE airvpn-sudo (or relocate
# it to another stable path and repoint both the sudoers `command` and
# $AIRVPN_SUDO_HELPER together). The retired vpn-sudo may be removed with Mullvad.
set -euo pipefail

NIXOS_DIR="/etc/nixos"
CFG="$NIXOS_DIR/configuration.nix"
STAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP="$CFG.bak.pre-i3hm-$STAMP"

[[ -w "$CFG" ]] || { echo "Need write access to $CFG — run with sudo." >&2; exit 1; }

echo "=== Staging i3 → home-manager cutover in $CFG ==="
echo "[0/4] Backing up -> $BACKUP"
cp -a "$CFG" "$BACKUP"

# 1. Remove the forced i3 configFile (so i3 falls back to ~/.config/i3/config).
if grep -q 'configFile = "/etc/i3.conf";' "$CFG"; then
  echo "[1/4] Removing  configFile = \"/etc/i3.conf\";"
  sed -i '/configFile = "\/etc\/i3.conf";/d' "$CFG"
else
  echo "[1/4] configFile already absent — skipping"
fi

# 2. Remove i3blocks from windowManager.i3.extraPackages (bar is i3status-rust now).
if grep -qE '^\s*i3blocks\s*$' "$CFG"; then
  echo "[2/4] Removing  i3blocks  from extraPackages"
  sed -i '/^\s*i3blocks\s*$/d' "$CFG"
else
  echo "[2/4] i3blocks already absent from extraPackages — skipping"
fi

# 3. Remove the environment.etc."i3.conf" assignment.
if grep -q 'environment.etc."i3.conf".text' "$CFG"; then
  echo "[3/4] Removing  environment.etc.\"i3.conf\".text"
  sed -i '/environment\.etc\."i3\.conf"\.text/d' "$CFG"
else
  echo "[3/4] environment.etc.\"i3.conf\" already absent — skipping"
fi

# 4. Remove the environment.etc."i3blocks.conf" assignment.
if grep -q 'environment.etc."i3blocks.conf".text' "$CFG"; then
  echo "[4/4] Removing  environment.etc.\"i3blocks.conf\".text"
  sed -i '/environment\.etc\."i3blocks\.conf"\.text/d' "$CFG"
else
  echo "[4/4] environment.etc.\"i3blocks.conf\" already absent — skipping"
fi

echo ""
echo "=== Diff (backup -> staged) ==="
if diff -u "$BACKUP" "$CFG"; then
  echo "(no changes — already fully staged)"
fi

cat <<DONE

=== Staged. NOT rebuilt. Review the diff above, then apply the cutover: ===
  1. Confirm ~/.config/i3/config is the home-manager symlink:
       ls -l ~/.config/i3/config     # -> /nix/store/...  (run 'home-manager switch' first if not)
  2. sudo nixos-rebuild switch
  3. sudo systemctl restart display-manager

  ⚠ DO NOT use 'i3-msg restart' here, and do NOT just log out/in at the greeter.
    This rebuild deletes /etc/i3/config and switches the session to launch a
    PLAIN 'i3' (which then reads ~/.config/i3/config). But:
      - your RUNNING i3 was launched as 'i3 -c /etc/i3/config', and 'i3 restart'
        re-execs with that SAME argv -> it can't find the now-deleted file -> the
        session dies.
      - the already-running lightdm keeps launching the OLD session command until
        the display-manager SERVICE itself is restarted, so a greeter log-out/in
        just loops on the deleted path.
    Restarting display-manager re-execs lightdm from the new generation (plain i3).
    If you're stuck at a black screen / login loop: Ctrl+Alt+F2 for a TTY, log in,
    run 'sudo systemctl restart display-manager', then Ctrl+Alt+F1 (or F7).

Verify after re-login:
  - the bar renders (memory/disk/net/cpu/temp/vpn/dictation/time [+ ⚙ on workbench])
  - VPN left-click menu + right-click detail work (NOPASSWD sudo still trusted)
  - workbench shows NO battery block

Rollback: sudo cp $BACKUP $CFG && sudo nixos-rebuild switch && sudo systemctl restart display-manager
The old /etc/nixos/i3config.nix, i3blocks.nix and i3blocks-scripts/ are left in
place as a fallback until you verify the new bar over a day.
DONE
