#!/usr/bin/env bash
# Enable Mullvad WireGuard VPN, server: ca-mtr-wg-301 (Montreal, 20Gbps)
#
# LEGACY — Mullvad was retired in favour of AirVPN (see scripts/airvpn-updown and the
# `bar` skill's airvpn.md). Kept only as the reverse-cutover recipe.
#
# This repo is PUBLIC, so no real IP is committed here. Every address is supplied at
# run time; the script refuses to run without them:
#   sudo OLD_MULLVAD_EP=<prev endpoint> MULLVAD_EP=<new endpoint> \
#        NEBULA_LH=<nebula lighthouse public IP> HOME_IP=<home uplink public IP> \
#        bash nix/system/apply-mullvad-enable.sh
# Read the current values out of /etc/nixos/configuration.nix before running.
set -euo pipefail

CFG="/etc/nixos/configuration.nix"
TS=$(date +%Y%m%d-%H%M%S)

: "${OLD_MULLVAD_EP:?set OLD_MULLVAD_EP to the endpoint IP in the commented-out block}"
: "${MULLVAD_EP:?set MULLVAD_EP to the ca-mtr-wg-301 endpoint IP}"
: "${NEBULA_LH:?set NEBULA_LH to the nebula lighthouse public IP}"
: "${HOME_IP:?set HOME_IP to the home uplink public IP}"

cp "$CFG" "${CFG}.bak-${TS}"
echo "[1/3] Backed up configuration.nix to ${CFG}.bak-${TS}"

python3 - "$CFG" << 'PYBLOCK'
import os, sys
p = sys.argv[1]
content = open(p).read()

# Tokens (not f-strings): the nix blocks below contain `${pkgs.gawk}` and `{ }`,
# which any brace-based formatting would mangle.
SUBS = {
    "@OLD_MULLVAD_EP@": os.environ["OLD_MULLVAD_EP"],
    "@MULLVAD_EP@":     os.environ["MULLVAD_EP"],
    "@NEBULA_LH@":      os.environ["NEBULA_LH"],
    "@HOME_IP@":        os.environ["HOME_IP"],
}


def fill(s):
    for k, v in SUBS.items():
        s = s.replace(k, v)
    return s

old = '''# ==========================================
  # Mullvad WireGuard VPN (Bogota)
  # ==========================================
  #networking.wg-quick.interfaces.mullvad = {
   # address = [ "10.67.129.86/32" ];
   # privateKeyFile = "/etc/nixos/mullvad-wg.key";
#
#    preUp = ''
#      # Save default gateway before VPN changes routing
#      ip route | grep '^default' | ${pkgs.gawk}/bin/awk '{print $3}' > /run/mullvad-gateway
#      ip route | grep '^default' | ${pkgs.gawk}/bin/awk '{print $5}' > /run/mullvad-iface
#    '';

#    postUp = ''
#      GW=$(cat /run/mullvad-gateway)
#      IFACE=$(cat /run/mullvad-iface)
#      # Bypass VPN for Mullvad endpoint
#      ip route add @OLD_MULLVAD_EP@ via $GW dev $IFACE || true
#      # Bypass VPN for LAN
#      ip route add 192.168.50.0/24 via $GW dev $IFACE || true
#      # Bypass VPN for Nebula endpoints (Hetzner + homelab public)
#      ip route add @NEBULA_LH@ via $GW dev $IFACE || true
#      ip route add @HOME_IP@ via $GW dev $IFACE || true
#    '';

#    preDown = ''
#      ip route del @OLD_MULLVAD_EP@ || true
#      ip route del 192.168.50.0/24 || true
#      ip route del @NEBULA_LH@ || true
#      ip route del @HOME_IP@ || true
#    '';

#    peers = [
#      {
#        publicKey = "iaMa84nCHK+v4TnQH4h2rxkqwwxemORXM12VbJDRZSU=";
#        endpoint = "@OLD_MULLVAD_EP@:51820";
#        persistentKeepalive = 25;
#        allowedIPs = [ "0.0.0.0/1" "128.0.0.0/1" ];
#      }
#    ];
#  };
#
#  systemd.services."wg-quick-mullvad".after = [ "network-online.target" ];
#  systemd.services."wg-quick-mullvad".wants = [ "network-online.target" ];'''

new = '''# ==========================================
  # Mullvad WireGuard VPN (ca-mtr-wg-301 - Montreal)
  # ==========================================
  networking.wg-quick.interfaces.mullvad = {
    address = [ "10.67.129.86/32" ];
    privateKeyFile = "/etc/nixos/mullvad-wg.key";

    preUp = ''
      # Save default gateway before VPN changes routing
      ip route | grep '^default' | ${pkgs.gawk}/bin/awk '{print $3}' > /run/mullvad-gateway
      ip route | grep '^default' | ${pkgs.gawk}/bin/awk '{print $5}' > /run/mullvad-iface
    '';

    postUp = ''
      GW=$(cat /run/mullvad-gateway)
      IFACE=$(cat /run/mullvad-iface)
      # Bypass VPN for Mullvad endpoint
      ip route add @MULLVAD_EP@ via $GW dev $IFACE || true
      # Bypass VPN for LAN
      ip route add 192.168.50.0/24 via $GW dev $IFACE || true
      # Bypass VPN for Nebula endpoints (Hetzner + homelab public)
      ip route add @NEBULA_LH@ via $GW dev $IFACE || true
      ip route add @HOME_IP@ via $GW dev $IFACE || true
    '';

    preDown = ''
      ip route del @MULLVAD_EP@ || true
      ip route del 192.168.50.0/24 || true
      ip route del @NEBULA_LH@ || true
      ip route del @HOME_IP@ || true
    '';

    peers = [
      {
        publicKey = "iV7uZuw8vbqrW/p4YhsxkIxXaUuI4Uj2hTl8TaJZfAA=";
        endpoint = "@MULLVAD_EP@:51820";
        persistentKeepalive = 25;
        allowedIPs = [ "0.0.0.0/1" "128.0.0.0/1" ];
      }
    ];
  };

  systemd.services."wg-quick-mullvad".after = [ "network-online.target" ];
  systemd.services."wg-quick-mullvad".wants = [ "network-online.target" ];'''

old = fill(old)
new = fill(new)

if old not in content:
    print("ERROR: expected commented mullvad block not found in configuration.nix")
    print("       (the file may have already been edited or has drifted from expected layout)")
    sys.exit(1)

content = content.replace(old, new)
open(p, 'w').write(content)
print("[2/3] Enabled Mullvad block (server: ca-mtr-wg-301)")
PYBLOCK

echo "[3/3] Rebuilding NixOS..."
nixos-rebuild switch

echo ""
echo "=== Done ==="
echo "Verify exit IP:"
echo "  curl -s --max-time 10 https://am.i.mullvad.net/json | python3 -m json.tool"
echo ""
echo "Expect: mullvad_exit_ip: true, hostname: ca-mtr-wg-301, city: Montreal"
echo ""
echo "If something is wrong, restore with:"
echo "  sudo cp /etc/nixos/configuration.nix.bak-* /etc/nixos/configuration.nix && sudo nixos-rebuild switch"
