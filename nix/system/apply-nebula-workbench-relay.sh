#!/usr/bin/env bash
# Workbench: advertise nebula relays so peers that cannot hole-punch to this
# host (CGNAT, symmetric NAT, off-LAN) can still reach it.
#
# Before: services.nebula.networks.mesh.settings had no `relay` block, so the
# NixOS module rendered `relay: {am_relay: false, relays: [], use_relays: true}`.
# An empty `relays:` means this host advertises NO relay to its peers, so
# laptop -> workbench was direct-hole-punch only.
#
# Relay targets (both verified `am_relay: true` in their LIVE configs, 2026-09-03):
#   10.42.0.2  production lighthouse, public underlay 5.161.118.55:4242  (the off-LAN path)
#   10.42.0.1  homelab lighthouse, LAN-only underlay 192.168.50.94:4242  (on-LAN fallback)
#
# Run: sudo bash nix/system/apply-nebula-workbench-relay.sh
set -euo pipefail

CFG="/etc/nixos/configuration.nix"
# Parse the RENDERED yaml rather than grepping it. A bare grep for the relay IP
# is a false positive: `lighthouse.hosts` lists the same two addresses, so
# `grep -E '^\s+- 10\.42\.0\.2$'` matches even when `relays:` is empty.
show_relays() {  # $1 = rendered config path
  python3 - "$1" <<'PY'
import sys, yaml
d = yaml.safe_load(open(sys.argv[1])) or {}
r = d.get("relay") or {}
print("  am_relay=%r use_relays=%r relays=%r" % (
    r.get("am_relay"), r.get("use_relays"), r.get("relays")))
PY
}
relays_ok() {    # $1 = rendered config path; rc 0 only if 10.42.0.2 is in relay.relays
  python3 - "$1" <<'PY'
import sys, yaml
d = yaml.safe_load(open(sys.argv[1])) or {}
r = (d.get("relay") or {}).get("relays") or []
sys.exit(0 if "10.42.0.2" in [str(x) for x in r] else 1)
PY
}

RENDERED_BEFORE=$(systemctl cat nebula@mesh.service | sed -n 's/.*-config \(\/nix\/store\/[^ ]*\).*/\1/p' | head -1)
echo "[pre] rendered config in use: ${RENDERED_BEFORE}"
show_relays "$RENDERED_BEFORE"

TS=$(date +%Y%m%d-%H%M%S)
cp "$CFG" "$CFG.bak-$TS"
echo "[backup] $CFG.bak-$TS"

python3 - "$CFG" <<'PYBLOCK'
import sys
p = sys.argv[1]
s = open(p).read()

old = """    settings = {
      punchy = {
        punch = true;
        respond = true;
      };
    };"""
new = """    settings = {
      # Advertise relays so peers that cannot hole-punch to this host (CGNAT,
      # symmetric NAT, off-LAN) can still reach it. `relays:` is the list peers
      # may use to relay packets TO this host; empty means no peer can relay
      # here at all. Both targets carry am_relay: true in their live configs.
      #   10.42.0.2 = production lighthouse (public underlay) -- the off-LAN path
      #   10.42.0.1 = homelab lighthouse (LAN-only underlay)  -- on-LAN fallback
      relay = {
        use_relays = true;
        relays = [ "10.42.0.2" "10.42.0.1" ];
      };
      punchy = {
        punch = true;
        respond = true;
      };
    };"""

if "relays = [" in s:
    print("[patch] relay block already present -- skipping")
    sys.exit(0)

n = s.count(old)
if n != 1:
    sys.exit(f"[patch] ABORT: expected exactly 1 match of the nebula settings block, found {n}")

open(p, "w").write(s.replace(old, new))
print("[patch] relay block added to services.nebula.networks.mesh.settings")
PYBLOCK

echo ""
echo "[rebuild] nixos-rebuild switch ..."
nixos-rebuild switch

echo ""
RENDERED_AFTER=$(systemctl cat nebula@mesh.service | sed -n 's/.*-config \(\/nix\/store\/[^ ]*\).*/\1/p' | head -1)
echo "[post] rendered config in use: ${RENDERED_AFTER}"
show_relays "$RENDERED_AFTER"

echo ""
echo "=== verdict ==="
# Negative control: the PRE-change file MUST fail relays_ok, otherwise the check
# is wired to nothing and its PASS below would mean nothing.
if relays_ok "$RENDERED_BEFORE"; then
  echo "FAIL: negative control did not fire -- the pre-change config already passes; check is untrustworthy"
  exit 1
fi
echo "  [control] pre-change config correctly FAILS the check"

if [ "$RENDERED_AFTER" = "$RENDERED_BEFORE" ]; then
  echo "FAIL: the store path did not change -- the running unit still uses the OLD config"
  exit 1
fi
if relays_ok "$RENDERED_AFTER"; then
  echo "PASS: rendered relay.relays is non-empty and contains 10.42.0.2"
else
  echo "FAIL: 10.42.0.2 is not in the rendered relay.relays list"
  exit 1
fi
systemctl is-active nebula@mesh.service
echo ""
echo "Rollback: sudo cp $CFG.bak-$TS $CFG && sudo nixos-rebuild switch"
