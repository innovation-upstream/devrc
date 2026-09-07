#!/usr/bin/env bash
# 🔴 SUPERSEDED 2026-09-07 — DO NOT RUN. What this adds CANNOT WORK, and
# `nix/system/apply-nebula-drop-443.sh` exists to undo it.
#
# Measured (clawgate #497): the prod nebula lighthouse listens on 4242 ONLY
# (clusters/production/apps/nebula/lighthouse/lighthouse-config.yaml -> listen.port),
# 443/udp on that host is WIREGUARD (k0s/host-firewall/relay-firewall.sh), and no
# 443->4242 redirect exists anywhere in homelab-infra. So nebula handshakes a socket
# that does not speak nebula and gets nothing back.
#
# The harm is not the wasted handshakes, it is that the entry READS AS A FALLBACK.
# In the one situation it was written for -- a network blocking UDP 4242 -- there is
# no fallback at all, while the config asserts there is one. Tailscale is the real
# answer to blocked-UDP; see clawgate #497.
#
# Kept rather than deleted so the reasoning survives with the artefact.
#
# ---- original header below ----
# Add UDP 443 fallback for prod lighthouse in nebula static host map
#
# This repo is PUBLIC, so the lighthouse's public IP is NOT committed. Supply it:
#   sudo NEBULA_LIGHTHOUSE=<lighthouse public IP> bash nix/system/apply-nebula-443.sh
# (read it out of the existing staticHostMap in /etc/nixos/configuration.nix, or from
#  the `server:` URL in $KC_PROD).
set -euo pipefail

# 🔴 HARD STOP, not just the banner above. 14 `claudedocs/handoff-*.md` files still name
# this script, so an operator or an agent resuming from one can reach it and re-add the
# exact entry apply-nebula-drop-443.sh exists to remove. A comment does not stop an
# execution; this does. The override exists so the file stays runnable for anyone who
# genuinely needs the old behaviour and has read why it does not work.
if [ "${NEBULA_443_I_KNOW_THIS_IS_DEAD:-}" != "1" ]; then
  echo "REFUSING: this script adds a nebula fallback that CANNOT WORK." >&2
  echo "  The prod lighthouse listens on 4242 only; 443/udp on that host is WireGuard," >&2
  echo "  and no 443->4242 redirect exists. See the header, and clawgate #497." >&2
  echo "  To REMOVE what this added:  sudo env \"PATH=\$PATH\" bash nix/system/apply-nebula-drop-443.sh" >&2
  echo "  To run it anyway:           NEBULA_443_I_KNOW_THIS_IS_DEAD=1 ..." >&2
  exit 64
fi

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
