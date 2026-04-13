#!/usr/bin/env bash
# Fix DNS for travel: use nebula IPs instead of forwarding to cluster DNS
# (Cluster DNS returns LAN IPs which are unreachable off-LAN)
# Run: sudo bash nix/system/apply-dns-travel.sh
set -euo pipefail

CFG="/etc/nixos/configuration.nix"

python3 - "$CFG" << 'PYBLOCK'
import sys
p = sys.argv[1]
content = open(p).read()

old = """      # Forward .homelab.lan and .workbench.lan queries
      # Use Nebula DNS when remote:
      server = [ "/homelab.lan/10.42.0.10" "/workbench.lan/10.42.0.10" "1.1.1.1" "8.8.8.8" ];
      # Use LAN DNS when on local network (comment above, uncomment below):
      #server = [ "/homelab.lan/192.168.50.95" "/workbench.lan/192.168.50.250" "1.1.1.1" "8.8.8.8" ];
     #address = [
       #"/workbench.lan/192.168.50.250"  # Specific: workbench \xe2\x86\x92 workbench
       #"/homelab.lan/192.168.50.95"     # Specific: homelab \xe2\x86\x92 homelab
       #"/lan/192.168.50.95"
       #"1.1.1.1"
     #];"""

new = """      # .lan DNS: toggle between Travel and LAN mode
      # TRAVEL MODE (nebula IPs - use when off 192.168.50.x LAN):
      address = [
        "/workbench.lan/10.42.0.30"    # workbench nebula IP
        "/homelab.lan/10.42.0.10"      # homelab gateway nebula IP
      ];
      # LAN MODE (comment above block, uncomment below when on local network):
      #address = [
      #  "/workbench.lan/192.168.50.250"
      #  "/homelab.lan/192.168.50.95"
      #];"""

if old in content:
    content = content.replace(old, new)
    open(p, 'w').write(content)
    print("[1/2] Switched dnsmasq to travel mode (nebula IPs)")
else:
    print("[1/2] WARNING: expected block not found - checking alternatives")
    # Try without the unicode arrows (in case encoding differs)
    if "server = [" in content and "homelab.lan/10.42.0.10" in content:
        print("  Found server= line but block structure differs. Manual edit needed.")
        sys.exit(1)
    elif "address = [" in content and "10.42.0.30" in content:
        print("  Already in travel mode")
    else:
        print("  Could not match. Manual edit needed.")
        sys.exit(1)
PYBLOCK

echo "[2/2] Rebuilding NixOS..."
nixos-rebuild switch

echo ""
echo "=== Done ==="
echo "Test: dig traefik.workbench.lan +short  (should return 10.42.0.30)"
echo "      dig grafana.homelab.lan +short    (should return 10.42.0.10)"
echo ""
echo "To switch back to LAN mode when home:"
echo "  Edit /etc/nixos/configuration.nix dnsmasq section"
echo "  Comment TRAVEL block, uncomment LAN block, nixos-rebuild switch"
