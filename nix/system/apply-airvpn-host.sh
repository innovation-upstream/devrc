#!/usr/bin/env bash
# =============================================================================
# Phase-2 apply for the HOST AirVPN WireGuard tunnel (see nix/system/airvpn-host.nix)
# =============================================================================
# Brings the bar's `airvpn` block live: installs the NOPASSWD sudo helper + the
# system module, wires the killswitch/split-tunnel hooks into your secret conf, and
# rebuilds. Idempotent + backs up every file it edits. Run from the repo root:
#
#     sudo bash nix/system/apply-airvpn-host.sh
#
# 🔴 DEFAULT-OFF: this does NOT bring the tunnel up. The host stays on its direct
#    route until you left-click the AirVPN bar pill -> Connect.
#
# PREREQUISITE (yours, never in git / the nix store): generate a NEW-device AirVPN
# WireGuard config (airvpn.org -> Config Generator -> Linux -> WireGuard -> NEW
# device) and save it to /etc/wireguard/airvpn.conf. The script refuses to proceed
# without it, appends the PostUp/PreDown killswitch hooks for you, and locks it 0600.
# =============================================================================
set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
  echo "This must run as root:  sudo bash nix/system/apply-airvpn-host.sh" >&2
  exit 1
fi

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
NIXOS_DIR="/etc/nixos"
CONFIG="${NIXOS_DIR}/configuration.nix"
HELPER_DIR="${NIXOS_DIR}/i3blocks-scripts"
MODULE="${NIXOS_DIR}/airvpn-host.nix"
CONF="/etc/wireguard/airvpn.conf"

echo "=== Phase-2 apply: HOST AirVPN tunnel (default-OFF) ==="

# ---------------------------------------------------------------------------- #
# 1. Your secret WireGuard config must exist.
# ---------------------------------------------------------------------------- #
if [[ ! -f "${CONF}" ]]; then
  cat >&2 <<MSG
[!] ${CONF} not found — this is YOUR secret AirVPN WireGuard config.
    Generate it at airvpn.org (Config Generator -> Linux -> WireGuard -> NEW device),
    save it to ${CONF}, then re-run this script. It is never committed / stored.
MSG
  exit 1
fi
echo "[1/5] Securing ${CONF} (root:root 0600)..."
chown root:root "${CONF}"
chmod 0600 "${CONF}"

# ---------------------------------------------------------------------------- #
# 2. Killswitch / split-tunnel hooks in the conf's [Interface] (idempotent).
# ---------------------------------------------------------------------------- #
if grep -q 'airvpn-updown' "${CONF}"; then
  echo "[2/5] PostUp/PreDown hooks already present — skipping."
else
  echo "[2/5] Appending PostUp/PreDown killswitch hooks to [Interface]..."
  cp "${CONF}" "${CONF}.bak.airvpn-apply"
  sed -i '/^\[Interface\]/a PostUp = /etc/nixos/i3blocks-scripts/airvpn-updown up %i\nPreDown = /etc/nixos/i3blocks-scripts/airvpn-updown down %i' "${CONF}"
  if ! grep -q 'airvpn-updown' "${CONF}"; then
    echo "  -> ERROR: no [Interface] line to anchor to. Add these two lines under" >&2
    echo "     [Interface] in ${CONF} manually, then re-run:" >&2
    echo "       PostUp = /etc/nixos/i3blocks-scripts/airvpn-updown up %i" >&2
    echo "       PreDown = /etc/nixos/i3blocks-scripts/airvpn-updown down %i" >&2
    cp "${CONF}.bak.airvpn-apply" "${CONF}"
    exit 1
  fi
fi

# ---------------------------------------------------------------------------- #
# 3. Privileged helpers at the STABLE sudoers-trusted path (never a nix-store path).
# ---------------------------------------------------------------------------- #
echo "[3/5] Installing airvpn-sudo + airvpn-updown to ${HELPER_DIR}..."
install -d -m 0755 "${HELPER_DIR}"
install -m 0755 -o root -g root "${REPO}/scripts/airvpn-sudo"   "${HELPER_DIR}/airvpn-sudo"
install -m 0755 -o root -g root "${REPO}/scripts/airvpn-updown" "${HELPER_DIR}/airvpn-updown"

# ---------------------------------------------------------------------------- #
# 4. System module + ensure configuration.nix imports it.
# ---------------------------------------------------------------------------- #
echo "[4/5] Installing ${MODULE} and wiring the import..."
install -m 0644 -o root -g root "${REPO}/nix/system/airvpn-host.nix" "${MODULE}"
if grep -q 'airvpn-host.nix' "${CONFIG}"; then
  echo "  -> import already present — skipping."
else
  cp "${CONFIG}" "${CONFIG}.bak.airvpn-host"
  # Insert ./airvpn-host.nix as the first entry of the existing imports = [ ... ] list,
  # preserving the leading indentation (empty-regex reuse keeps the address's \1 group).
  sed -i '0,/^\(\s*\)imports\s*=\s*\[/s//&\n\1  .\/airvpn-host.nix/' "${CONFIG}"
  if ! grep -q 'airvpn-host.nix' "${CONFIG}"; then
    echo "  -> ERROR: could not find an 'imports = [' list to extend." >&2
    echo "     Add   ./airvpn-host.nix   to imports in ${CONFIG} manually, then re-run." >&2
    cp "${CONFIG}.bak.airvpn-host" "${CONFIG}"
    exit 1
  fi
fi

# ---------------------------------------------------------------------------- #
# 5. Rebuild (tunnel stays down — no auto-start unit is declared).
# ---------------------------------------------------------------------------- #
echo "[5/5] nixos-rebuild switch..."
nixos-rebuild switch

cat <<MSG

=== Done. ===
The tunnel is DEFAULT-OFF: the host is still on its direct route.
Bring it up:  left-click the AirVPN bar pill -> Connect  (or: sudo /etc/nixos/i3blocks-scripts/airvpn-sudo up)
Then verify:  left-click -> "Verify exit IP / leak"  (should read verified, exit IP != your home IP).
Backups: ${CONFIG}.bak.airvpn-host, ${CONF}.bak.airvpn-apply (if edited).
MSG
