#!/usr/bin/env bash
# Remove the DEAD `<lighthouse>:443` staticHostMap entry that apply-nebula-443.sh added.
#
#   bash nix/system/apply-nebula-drop-443.sh --check   # read-only, no root
#   sudo env "PATH=$PATH" bash nix/system/apply-nebula-drop-443.sh
#
# WHY (measured 2026-09-03, clawgate #497). apply-nebula-443.sh appended a second
# address for the prod lighthouse, `<ip>:443`, as a fallback for networks that block
# UDP 4242. It cannot ever work:
#
#   * the prod nebula lighthouse listens on 4242 ONLY
#     (clusters/production/apps/nebula/lighthouse/lighthouse-config.yaml -> listen.port: 4242)
#   * 443/udp on that host is WIREGUARD
#     (k0s/host-firewall/relay-firewall.sh -> "443/udp (wireguard)")
#   * no 443->4242 redirect exists anywhere in homelab-infra (grepped)
#
# So nebula sends handshakes to a WireGuard socket and gets nothing back. The entry
# costs handshake attempts and, far worse, READS AS A FALLBACK THAT EXISTS. In the one
# scenario it was created for -- a hotel or cafe blocking UDP 4242 -- there is no
# fallback at all. Tailscale is the real answer to that failure mode (clawgate #497);
# this just stops the config asserting a safety net it does not have.
#
# 🔴 THIS REPO IS PUBLIC, so the lighthouse IP is NOT written here. The script DERIVES
#    it from the `:4242` entry sitting beside the `:443` one, and refuses to act unless
#    the two are the same host. That is also a correctness check, not only a secrecy
#    one: a `:443` beside a DIFFERENT host is not the entry this script is about.
#
# WHICH HOST: `hostname` does not discriminate on this fleet -- the workbench and the
# portable both answer `nixos`. The guard is the mesh address, unique by construction.
# Default targets the PORTABLE (10.42.0.100), which is where apply-nebula-443.sh ran.
#
# Overrides: NEBULA_NET=mesh  NEBULA_EXPECT_MESH_IP=10.42.0.100  NEBULA_CFG=/etc/nixos/configuration.nix
#
# SAFETY: nothing is written until every precondition passes. Then, in order: patch a
# temp copy, `nix-instantiate --parse` it, take a uniquely-named backup (never
# overwriting one), move it into place, and only then rebuild -- with a trap that
# restores the backup on ANY later failure. Re-running once it is gone is a no-op.
set -euo pipefail

NET="${NEBULA_NET:-mesh}"
EXPECT_MESH_IP="${NEBULA_EXPECT_MESH_IP:-10.42.0.100}"
CFG="${NEBULA_CFG:-/etc/nixos/configuration.nix}"
UNIT="nebula@${NET}.service"
IFACE="nebula.${NET}"
MODE="${1:-apply}"

die() { echo "ABORT: $*" >&2; exit 1; }

# ---------------------------------------------------------------------------- --check
# Reads the config the RUNNING unit actually loaded, so a stale edit that was never
# switched does not read as satisfied. Needs no root: /nix/store is world-readable.
#   rc 0 = the dead entry is GONE      rc 1 = still present      rc 2 = cannot tell
if [ "$MODE" = "--check" ]; then
  systemctl cat "$UNIT" >/dev/null 2>&1 || { echo "cannot tell: $UNIT is not loaded here"; exit 2; }
  running=$(systemctl cat "$UNIT" | grep -oP '(?<=-config )\S+' | head -1)
  [ -n "$running" ] && [ -r "$running" ] || { echo "cannot tell: cannot read the running config"; exit 2; }
  echo "unit    : $UNIT ($(systemctl is-active "$UNIT"), pid $(systemctl show -p MainPID --value "$UNIT"))"
  echo "running : $running"
  block=$(sed -n '/^static_host_map:/,/^[a-z]/p' "$running")
  echo "$block" | sed 's/^/  | /'
  if printf '%s' "$block" | grep -qE ':443$'; then
    echo "FAIL: a ':443' address is still advertised -- nebula will keep handshaking a socket"
    echo "      that does not speak nebula (443/udp there is WireGuard)."
    exit 1
  fi
  echo "PASS: no ':443' entry in the running static_host_map."
  exit 0
fi

# ------------------------------------------------------------------ preflight (no writes)
echo "== preflight =="
[ "$(id -u)" = "0" ] || die "must run as root: sudo env \"PATH=\$PATH\" bash ${BASH_SOURCE[0]}"

for t in awk sed grep systemctl ip nixos-rebuild nix-instantiate; do
  command -v "$t" >/dev/null 2>&1 || die "\`$t\` is not on PATH.
  sudo inherits the CALLER's PATH here (no secure_path), so run it as:
    sudo env \"PATH=\$PATH\" bash ${BASH_SOURCE[0]}"
done

have_ip=$(ip -4 -o addr show "$IFACE" 2>/dev/null | awk '{print $4}' | cut -d/ -f1 | head -1)
[ -n "$have_ip" ] || die "no address on $IFACE -- is nebula running on this host?"
[ "$have_ip" = "$EXPECT_MESH_IP" ] || die "WRONG HOST: $IFACE is $have_ip, expected $EXPECT_MESH_IP.
  \`hostname\` does not discriminate between the machines on this fleet, so this guard
  uses the mesh address. If you really mean this host, re-run with
  NEBULA_EXPECT_MESH_IP=$have_ip"
echo "  host      : $IFACE = $have_ip  (matches the expected target)"

[ -f "$CFG" ] && [ -w "$CFG" ] || die "$CFG is not a writable regular file"

# The line, in the shape apply-nebula-443.sh leaves behind:
#   "10.42.0.2" = [ "<ip>:4242" "<ip>:443" ];
# Require EXACTLY ONE. Zero means it is already gone (or hand-edited into another shape,
# which this must not guess at); more than one means several hosts and no single answer.
matches=$(grep -cE '"[0-9.]+" = \[ "[0-9.]+:4242" "[0-9.]+:443" \];' "$CFG" || true)
if [ "$matches" = "0" ]; then
  if grep -qE ':443"' "$CFG"; then
    echo "  NOTE: a ':443' string is present but not in the expected one-line shape:" >&2
    grep -nE ':443"' "$CFG" | sed 's/^/    /' >&2
    die "refusing to guess at a hand-edited staticHostMap -- remove the :443 address by hand"
  fi
  echo "  state     : ALREADY GONE -- no :4242/:443 pair in $CFG. Nothing to do."
  exit 0
fi
[ "$matches" = "1" ] || die "expected exactly 1 ':4242'+':443' pair, found $matches -- edit by hand"

line=$(grep -nE '"[0-9.]+" = \[ "[0-9.]+:4242" "[0-9.]+:443" \];' "$CFG")
ip4242=$(printf '%s' "$line" | grep -oE '"[0-9.]+:4242"' | tr -d '"' | cut -d: -f1)
ip443=$(printf  '%s' "$line" | grep -oE '"[0-9.]+:443"'  | tr -d '"' | cut -d: -f1)
[ "$ip4242" = "$ip443" ] || die "the :443 address ($ip443) is a DIFFERENT host from the :4242 one ($ip4242).
  That is not the entry this script removes; inspect it by hand."
echo "  target    : dropping the :443 address beside the :4242 one for the same host"
echo "  anchor    : exactly 1 match, line $(printf '%s' "$line" | cut -d: -f1)"
echo

# -------------------------------------------------------------------------- patch (temp)
echo "== patch =="
TMP="${CFG}.new.$$"
BAK="${CFG}.bak-nebula-drop443-$(date +%Y%m%d-%H%M%S)-$$"
PATCHED=0; SWITCHED=0; OK=0

finish() {
  local rc=$?
  rm -f "$TMP"
  [ "$OK" = "1" ] && return
  if [ "$PATCHED" = "1" ] && [ -f "$BAK" ]; then
    cp -p "$BAK" "$CFG"
    echo >&2; echo "ROLLED BACK: $CFG restored from $BAK" >&2
    if [ "$SWITCHED" = "1" ]; then
      echo "🔴 The system had ALREADY been switched. The FILE is restored but the RUNNING" >&2
      echo "   system is not -- run \`sudo nixos-rebuild switch\` to return it." >&2
    else
      echo "   The system was never switched, so nothing is running the change." >&2
    fi
  fi
  exit $rc
}
trap finish EXIT

# sed, not awk: 3-arg `match()` is a gawk extension and this must not depend on which
# awk the host ships. The substitution drops only the `"<ip>:443"` token and the single
# space before it, leaving the rest of the line byte-identical.
sed -E 's/^([^"]*"[0-9.]+" = \[ "[0-9.]+:4242") "[0-9.]+:443"( \];.*)$/\1\2/' "$CFG" > "$TMP"

# Verify the edit did exactly what was intended -- the substitution count is not
# reported by sed, so it is reconstructed from the result rather than assumed.
[ "$(wc -l < "$TMP")" = "$(wc -l < "$CFG")" ] || die "line count changed; this edit removes a token, not a line"
changed=$(diff "$CFG" "$TMP" | grep -c '^<' || true)
[ "$changed" = "1" ] || die "expected exactly 1 changed line, got $changed -- $CFG untouched"
grep -qE ':443"' "$TMP" && die "a ':443' string survived the patch -- refusing to continue"
echo "  temp file : :443 address removed, 1 line changed, line count unchanged"

nix-instantiate --parse "$TMP" >/dev/null 2>&1 || die "the patched file is not valid Nix -- $CFG untouched"
echo "  nix parse : OK"
echo "  diff:"; diff -u "$CFG" "$TMP" | sed 's/^/    /' || true

[ -e "$BAK" ] && die "backup path $BAK already exists; refusing to overwrite it"
cp -p "$CFG" "$BAK"; echo "  backup    : $BAK"
mv "$TMP" "$CFG"; PATCHED=1; echo "  applied   : $CFG"
echo

echo "== nixos-rebuild switch =="
nixos-rebuild switch
SWITCHED=1
echo

echo "== verify =="
active=$(systemctl is-active "$UNIT" || true)
[ "$active" = "active" ] || die "$UNIT is '$active' after the switch, not 'active'"
echo "  unit      : active"
if bash "${BASH_SOURCE[0]}" --check; then
  OK=1
else
  die "the running config STILL advertises a :443 address after the switch"
fi

echo
echo "=== Done. What this did and did NOT buy ==="
echo " * REMOVED a fallback that could never work. It did not ADD one."
echo " * If UDP 4242 is blocked, there is still NO nebula fallback. Tailscale is the"
echo "   answer to that (clawgate #497), and it is not done yet."
echo " * Rollback: sudo cp $BAK $CFG && sudo nixos-rebuild switch"
