#!/usr/bin/env bash
# Route registry-1.docker.io lookups to Cloudflare instead of the LAN router.
#
# WHY
# ---
# MEASURED 2026-08-29 on the workbench. `docker build` and `docker pull` began
# failing TLS verification against registry-1.docker.io, reporting certificates
# belonging to unrelated third-party websites — and reporting a DIFFERENT one on
# different attempts, which is what made it look like interception rather than
# what it is.
#
# It is not interception. The LAN router (192.168.50.1) answers
# registry-1.docker.io with a PINNED set of eight A records carrying a TTL of
# 42,048,498 seconds — 487 DAYS. A normal docker.io TTL is 30-60 seconds. Of
# those eight addresses, measured by opening TLS to each with the right SNI:
#
#     4  serve the correct *.docker.com certificate
#     2  serve certificates for unrelated third-party sites (old EC2 elastic IPs,
#        released by Docker and since reassigned to other AWS customers)
#     2  do not complete a handshake at all
#
# dnsmasq caches that 487-day answer and hands it out, so every connection
# round-robins across good and bad addresses. That is the intermittency: roughly
# half of all pulls land on somebody else's web server, present the wrong SNI,
# and fail verification. Cloudflare (1.1.1.1) returns a completely disjoint set
# whose addresses were verified 6/6 correct.
#
# 🔴 THE ROOT CAUSE IS ON THE ROUTER, NOT THIS HOST. This script is a host-side
# bypass for one hostname so builds work; it does not fix the stale entry the
# router is serving, and it does not fix the other machines on the LAN. Clearing
# that entry on 192.168.50.1 is the actual repair.
#
# WHY NOT JUST FLUSH THE CACHE: restarting dnsmasq drops the cached copy and
# then immediately re-asks the router, which returns the same pinned record. The
# fix has to be "do not ask the router for this name".
#
# WHAT THIS CHANGES
# -----------------
# `services.dnsmasq.servers` currently reads:
#     [ "192.168.50.1" "1.1.1.1" ]
# and becomes:
#     [ "/docker.io/1.1.1.1" "192.168.50.1" "1.1.1.1" ]
#
# A dnsmasq `/domain/server` entry is domain-specific and takes precedence, so
# docker.io and everything under it resolve via Cloudflare while every other
# name keeps using the router first exactly as before (which is deliberate — it
# is what bypasses the VPN for LAN names).
#
# Run: sudo bash nix/system/apply-dnsmasq-docker-io-pin.sh
set -euo pipefail

CFG="/etc/nixos/configuration.nix"
[ -r "$CFG" ] || { echo "cannot read $CFG" >&2; exit 1; }

cp -a "$CFG" "$CFG.bak-$(date +%Y%m%d-%H%M%S)"

python3 - "$CFG" <<'PYBLOCK'
import sys
p = sys.argv[1]
s = open(p).read()

old = '    servers = ["192.168.50.1" "1.1.1.1"];  # LAN router first (bypasses VPN), Cloudflare fallback'
new = ('    # 🔴 docker.io is pinned FIRST to Cloudflare on purpose: the LAN router serves\n'
       '    # registry-1.docker.io with a 487-day TTL over a stale address set, two of whose\n'
       '    # IPs now belong to unrelated sites, so ~half of all pulls fail TLS verification.\n'
       '    # Domain-specific entries win, so every OTHER name still asks the router first.\n'
       '    # Remove this line once the router\'s stale entry is cleared.\n'
       '    servers = ["/docker.io/1.1.1.1" "192.168.50.1" "1.1.1.1"];  # LAN router first (bypasses VPN), Cloudflare fallback')

if new in s:
    print("already applied — nothing to do")
    raise SystemExit(0)
if s.count(old) != 1:
    print(f"REFUSING: expected exactly 1 match for the servers line, found {s.count(old)}.",
          file=sys.stderr)
    print("The config has drifted; apply the change by hand rather than guessing.", file=sys.stderr)
    raise SystemExit(2)

open(p, "w").write(s.replace(old, new))
print("configuration.nix updated")
PYBLOCK

echo
echo "rebuilding..."
nixos-rebuild switch

echo
echo "=== verify: docker.io must now resolve via Cloudflare, with a SANE ttl ==="
dig @127.0.0.1 registry-1.docker.io A | grep -E '^registry-1' || true
echo
echo "A TTL in the tens of seconds means it worked. A TTL of ~42,000,000 means the"
echo "router answer is still being used — check the servers line actually changed."
echo
echo "Then confirm every returned address serves the RIGHT certificate:"
echo '  for ip in $(dig +short @127.0.0.1 registry-1.docker.io A); do'
echo '    echo -n "$ip -> "'
echo '    openssl s_client -connect "$ip:443" -servername registry-1.docker.io </dev/null 2>/dev/null \'
echo '      | openssl x509 -noout -subject | sed "s/.*CN *= *//"'
echo '  done'
echo "Every line should read *.docker.com."
