#!/usr/bin/env bash
# Travel prep: lid suspend, DNS via nebula, S3 deep sleep, docker disabled, nebula relay
# Run: sudo bash nix/system/apply-travel-prep.sh
set -euo pipefail

CFG="/etc/nixos/configuration.nix"
TS=$(date +%Y%m%d-%H%M%S)
cp "$CFG" "$CFG.bak-$TS"
echo "[backup] $CFG.bak-$TS"

# 1. HandleLidSwitch: lock -> suspend
if grep -q 'HandleLidSwitch = "suspend"' "$CFG"; then
  echo "[1/5] HandleLidSwitch already suspend -- skipping"
else
  sed -i 's/HandleLidSwitch = "lock"/HandleLidSwitch = "suspend"/' "$CFG"
  echo "[1/5] HandleLidSwitch -> suspend"
fi

# 2. DNS: switch from hardcoded LAN addresses to nebula DNS forwarding
# Uses python for safe multi-line string replacement
python3 - "$CFG" << 'PYBLOCK'
import sys
p = sys.argv[1]
with open(p) as f:
    content = f.read()

changed = False

# Uncomment the nebula server line
old = '      #server = [ "/homelab.lan/10.42.0.10" "/workbench.lan/10.42.0.10" "1.1.1.1" "8.8.8.8" ];'
new = '      server = [ "/homelab.lan/10.42.0.10" "/workbench.lan/10.42.0.10" "1.1.1.1" "8.8.8.8" ];'
if old in content:
    content = content.replace(old, new)
    changed = True

# Comment out address block entries
for pat, rep in [
    ('     address = [', '     #address = ['),
    ('       "/workbench.lan/192.168.50.250"', '       #"/workbench.lan/192.168.50.250"'),
    ('       "/homelab.lan/192.168.50.95"', '       #"/homelab.lan/192.168.50.95"'),
]:
    if pat in content and rep not in content:
        content = content.replace(pat, rep)
        changed = True

# Comment out the closing ]; of the address block
# It's the first ]; after #address = [
lines = content.split('\n')
found_addr = False
for i, line in enumerate(lines):
    if '#address = [' in line:
        found_addr = True
    if found_addr and line.strip() == '];':
        lines[i] = line.replace('];', '#];')
        found_addr = False
        changed = True
        break

if changed:
    with open(p, 'w') as f:
        f.write('\n'.join(lines))
    print("[2/5] DNS switched to nebula forwarding for .lan domains")
else:
    print("[2/5] DNS already configured for nebula -- skipping")
PYBLOCK

# 3. S3 deep sleep
if grep -q "mem_sleep_default=deep" "$CFG"; then
  echo "[3/5] mem_sleep_default=deep already set -- skipping"
else
  sed -i '/^  boot\.kernelPackages/a\  boot.kernelParams = [ "mem_sleep_default=deep" ];' "$CFG"
  echo "[3/5] Added boot.kernelParams mem_sleep_default=deep"
fi

# 4. Docker: disable on boot
if grep -q "enableOnBoot = false" "$CFG"; then
  echo "[4/5] Docker enableOnBoot already false -- skipping"
else
  sed -i '/docker = {/{n;s/enable = true;/enable = true;\n      enableOnBoot = false;/}' "$CFG"
  echo "[4/5] Docker: enableOnBoot = false"
fi

# 5. Nebula relay
python3 - "$CFG" << 'PYBLOCK2'
import sys
p = sys.argv[1]
with open(p) as f:
    content = f.read()

# Check if relay is already in the nebula section
nebula_section = content.split("services.nebula")[1].split("security.pki")[0] if "services.nebula" in content else ""
if "relay" in nebula_section:
    print("[5/5] Nebula relay already configured -- skipping")
else:
    old = """      punchy = {
        punch = true;
        respond = true;
      };"""
    new = """      relay = {
        use_relays = true;
        relays = [ "10.42.0.2" ];
      };
      punchy = {
        punch = true;
        respond = true;
      };"""
    content = content.replace(old, new)
    with open(p, 'w') as f:
        f.write(content)
    print("[5/5] Added nebula relay (10.42.0.2 prod lighthouse)")
PYBLOCK2

echo ""
echo "[rebuild] Running nixos-rebuild switch..."
nixos-rebuild switch

echo ""
echo "=== Done ==="
echo "Test:"
echo "  - Close lid -> should suspend (not just lock)"
echo "  - cat /sys/power/mem_sleep -> should show [deep]"
echo "  - dig workbench.lan -> should resolve via 10.42.0.10"
echo "  - systemctl is-enabled docker -> should show disabled"
echo ""
echo "Rollback: sudo cp $CFG.bak-$TS $CFG && sudo nixos-rebuild switch"
