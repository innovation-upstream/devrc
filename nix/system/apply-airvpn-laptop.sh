#!/usr/bin/env bash
# =============================================================================
# Phase-2 apply for the LAPTOP's HOST AirVPN WireGuard tunnel — ROAMING mode.
# =============================================================================
# Mirrors apply-airvpn-host.sh for the laptop. The difference is the tunnel
# MODE: the workbench's killswitch hardcodes the home LAN (192.168.50.0/24);
# the laptop's conf is generated with the ROAMING PostUp/PreDown (a third
# argument to airvpn-updown), which DERIVES the local LAN from the uplink's
# connected route at PostUp — so the killswitch is correct on any network.
#
# Prerequisite (Zach's secret, never in git / the nix store):
#   1. Generate a NEW-device AirVPN WireGuard config (airvpn.org -> Config
#      Generator -> Linux -> WireGuard -> NEW device — a SEPARATE device from
#      the workbench's) and save it to /etc/wireguard/airvpn.conf.
#
# Then run:
#   sudo bash nix/system/apply-airvpn-laptop.sh
#
# What it does (idempotent, with backups):
#   - installs airvpn-sudo + airvpn-updown to /etc/nixos/i3blocks-scripts/
#   - appends the ROAMING PostUp/PreDown to the conf's [Interface] (if absent)
#   - copies nix/system/airvpn-host.nix to /etc/nixos/ + wires configuration.nix
#     (wireguard-tools + nftables + the NOPASSWD sudoers rule — identical on
#     both hosts), then nixos-rebuild switch
#
# After that: `systemctl --user start airvpn-status-poll.service` (or let the
# timer fire), left-click the vpn pill -> Connect, and run the LAN-first
# re-test in claude/skills/bar/reference/airvpn.md (laptop section).
# =============================================================================
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
    echo "This must run as root:  sudo bash nix/system/apply-airvpn-laptop.sh" >&2
    exit 1
fi

REPO="$(cd "$(dirname "$0")/../.." && pwd)"
NIXOS_DIR="/etc/nixos"
HELPER_DIR="${NIXOS_DIR}/i3blocks-scripts"
MODULE="${NIXOS_DIR}/airvpn-host.nix"
CONF="/etc/wireguard/airvpn.conf"
CONFIG="${NIXOS_DIR}/configuration.nix"

if [[ ! -f "$CONF" ]]; then
    echo "ERROR: $CONF missing." >&2
    echo "       Generate it at airvpn.org (Config Generator -> Linux -> WireGuard -> NEW device)," >&2
    echo "       a SEPARATE device from the workbench's, and save it here first." >&2
    exit 3
fi

echo "[1/5] Locking $CONF to 0600 root:root..."
chown root:root "$CONF"; chmod 0600 "$CONF"

echo "[2/5] Appending ROAMING PostUp/PreDown hooks to $CONF..."
if grep -q 'airvpn-updown' "$CONF"; then
    if grep -q 'airvpn-updown up %i roaming' "$CONF"; then
        echo "      already present (roaming) — leaving untouched"
    else
        echo "      FOUND non-roaming hooks — rewriting them to roaming mode" >&2
        ( umask 077; cp "${CONF}" "${CONF}.bak.airvpn-apply" )
        sed -i 's|/etc/nixos/i3blocks-scripts/airvpn-updown up %i$|/etc/nixos/i3blocks-scripts/airvpn-updown up %i roaming|; s|/etc/nixos/i3blocks-scripts/airvpn-updown down %i$|/etc/nixos/i3blocks-scripts/airvpn-updown down %i roaming|' "${CONF}"
        grep -q 'airvpn-updown up %i roaming' "${CONF}" \
            || { echo "      rewrite FAILED — restoring backup" >&2; cp "${CONF}.bak.airvpn-apply" "${CONF}"; exit 4; }
    fi
else
    ( umask 077; cp "${CONF}" "${CONF}.bak.airvpn-apply" )
    sed -i '/^\[Interface\]/a PostUp = /etc/nixos/i3blocks-scripts/airvpn-updown up %i roaming\nPreDown = /etc/nixos/i3blocks-scripts/airvpn-updown down %i roaming' "${CONF}"
    if ! grep -q 'airvpn-updown' "${CONF}"; then
        echo "      insert FAILED — restoring backup. Add manually:" >&2
        echo "       PostUp = /etc/nixos/i3blocks-scripts/airvpn-updown up %i roaming" >&2
        echo "       PreDown = /etc/nixos/i3blocks-scripts/airvpn-updown down %i roaming" >&2
        cp "${CONF}.bak.airvpn-apply" "${CONF}"
        exit 4
    fi
fi

echo "[3/5] Installing airvpn-sudo + airvpn-updown to ${HELPER_DIR}..."
mkdir -p "${HELPER_DIR}"
install -m 0755 -o root -g root "${REPO}/scripts/airvpn-sudo"   "${HELPER_DIR}/airvpn-sudo"
install -m 0755 -o root -g root "${REPO}/scripts/airvpn-updown" "${HELPER_DIR}/airvpn-updown"

echo "[4/5] Installing the system module + sudoers rule (airvpn-host.nix)..."
install -m 0644 -o root -g root "${REPO}/nix/system/airvpn-host.nix" "${MODULE}"
if grep -q 'airvpn-host.nix' "${NIXOS_DIR}/configuration.nix"; then
    echo "      already imported"
else
    cp "${CONFIG:-$NIXOS_DIR/configuration.nix}" "${NIXOS_DIR}/configuration.nix.bak.airvpn-host" 2>/dev/null || true
    awk '
        /^\s*imports\s*=\s*\[/ {
            in_imports=1; print; next
        }
        in_imports && !done && /\]/ {
            sub(/\]/, "      ./airvpn-host.nix\n    ]"); done=1; print; next
        }
        { print }
    ' "${NIXOS_DIR}/configuration.nix" > "${NIXOS_DIR}/configuration.nix.tmp.airvpn"
    cat "${NIXOS_DIR}/configuration.nix.tmp.airvpn" > "${NIXOS_DIR}/configuration.nix"
    rm -f "${NIXOS_DIR}/configuration.nix.tmp.airvpn"
    if ! grep -q 'airvpn-host.nix' "${NIXOS_DIR}/configuration.nix"; then
        echo "      could not insert the import. Add   ./airvpn-host.nix   to imports in ${NIXOS_DIR}/configuration.nix manually, then re-run." >&2
        exit 5
    fi
fi

echo "[5/5] nixos-rebuild switch..."
nixos-rebuild switch

cat <<'EOF'

Done. Next steps (as zach, no sudo):
  1. systemctl --user status airvpn-status-poll.timer   # the pill's writer
  2. Left-click the vpn pill -> Connect
  3. Re-test, LAN FIRST (this LAN is the recovery path):
       curl -s https://ipinfo.io/json        -> CA + an AirVPN org
       ssh zach@10.42.0.30 'true'            -> nebula path survived
     (full protocol: claude/skills/bar/reference/airvpn.md — laptop section)
EOF
