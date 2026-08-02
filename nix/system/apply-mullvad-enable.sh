#!/usr/bin/env bash
# Enable Mullvad WireGuard VPN, server: ca-mtr-wg-301 (Montreal, 20Gbps)
# Run: sudo bash nix/system/apply-mullvad-enable.sh
set -euo pipefail

CFG="/etc/nixos/configuration.nix"
TS=$(date +%Y%m%d-%H%M%S)

cp "$CFG" "${CFG}.bak-${TS}"
echo "[1/3] Backed up configuration.nix to ${CFG}.bak-${TS}"

python3 - "$CFG" << 'PYBLOCK'
import sys
p = sys.argv[1]
content = open(p).read()

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
#      ip route add 154.47.16.34 via $GW dev $IFACE || true
#      # Bypass VPN for LAN
#      ip route add 192.168.50.0/24 via $GW dev $IFACE || true
#      # Bypass VPN for Nebula endpoints (Hetzner + homelab public)
#      ip route add 5.161.118.55 via $GW dev $IFACE || true
#      ip route add 24.79.61.66 via $GW dev $IFACE || true
#    '';

#    preDown = ''
#      ip route del 154.47.16.34 || true
#      ip route del 192.168.50.0/24 || true
#      ip route del 5.161.118.55 || true
#      ip route del 24.79.61.66 || true
#    '';

#    peers = [
#      {
#        publicKey = "iaMa84nCHK+v4TnQH4h2rxkqwwxemORXM12VbJDRZSU=";
#        endpoint = "154.47.16.34:51820";
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
      ip route add 23.234.120.2 via $GW dev $IFACE || true
      # Bypass VPN for LAN
      ip route add 192.168.50.0/24 via $GW dev $IFACE || true
      # Bypass VPN for Nebula endpoints (Hetzner + homelab public)
      ip route add 5.161.118.55 via $GW dev $IFACE || true
      ip route add 24.79.61.66 via $GW dev $IFACE || true
    '';

    preDown = ''
      ip route del 23.234.120.2 || true
      ip route del 192.168.50.0/24 || true
      ip route del 5.161.118.55 || true
      ip route del 24.79.61.66 || true
    '';

    peers = [
      {
        publicKey = "iV7uZuw8vbqrW/p4YhsxkIxXaUuI4Uj2hTl8TaJZfAA=";
        endpoint = "23.234.120.2:51820";
        persistentKeepalive = 25;
        allowedIPs = [ "0.0.0.0/1" "128.0.0.0/1" ];
      }
    ];
  };

  systemd.services."wg-quick-mullvad".after = [ "network-online.target" ];
  systemd.services."wg-quick-mullvad".wants = [ "network-online.target" ];'''

if old not in content:
    print("ERROR: expected commented mullvad block not found in configuration.nix")
    print("       (the file may have already been edited or has drifted from expected layout)")
    sys.exit(1)

content = content.replace(old, new)
open(p, 'w').write(content)
print("[2/3] Enabled Mullvad block (server: ca-mtr-wg-301, 23.234.120.2)")
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
