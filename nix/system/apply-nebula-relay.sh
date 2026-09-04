#!/usr/bin/env bash
# Add a nebula relay advertisement to this host and switch to it.
#
#   sudo bash nix/system/apply-nebula-relay.sh
#
# `relay.relays` is the list of mesh IPs that PEERS MAY USE TO RELAY PACKETS TO THIS
# HOST. Empty means no peer can relay here, so a peer that cannot hole-punch (CGNAT,
# symmetric NAT, off-LAN) never connects. This inserts
#
#       relay = {
#         use_relays = true;
#         relays = [ "<relay>" ];
#       };
#
# into services.nebula.networks.<net>.settings in /etc/nixos/configuration.nix, then
# runs `nixos-rebuild switch`.
#
# Overrides (all optional):
#   NEBULA_NET=mesh                 the network under services.nebula.networks
#   NEBULA_RELAY=10.42.0.2          the relay to advertise (mesh IP)
#   NEBULA_EXPECT_MESH_IP=10.42.0.30  the host guard -- see "WHICH HOST" below
#   NEBULA_CFG=/etc/nixos/configuration.nix
#
# WHICH HOST: `hostname` is NOT a discriminator on this fleet -- more than one machine
# answers `nixos`, and more than one runs nebula@<net>. So the guard is this host's own
# mesh address, which is unique by construction. Running this on the wrong machine
# aborts before any write. Pass NEBULA_EXPECT_MESH_IP to target a different host
# deliberately.
#
# SAFETY. Nothing is written until every precondition passes. Then, in order: the file
# is patched in a temp copy, the temp is syntax-checked with `nix-instantiate --parse`,
# a uniquely-named backup is taken (an existing one is never overwritten), and only
# then is the temp moved into place. From that moment a trap restores the backup on ANY
# failure. Re-running once the relay is live is a no-op that exits 0.
set -euo pipefail

NET="${NEBULA_NET:-mesh}"
RELAY="${NEBULA_RELAY:-10.42.0.2}"
EXPECT_MESH_IP="${NEBULA_EXPECT_MESH_IP:-10.42.0.30}"
CFG="${NEBULA_CFG:-/etc/nixos/configuration.nix}"
UNIT="nebula@${NET}.service"
IFACE="nebula.${NET}"

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CHECK="${HERE}/check-nebula-relays.sh"

die() { echo "ABORT: $*" >&2; exit 1; }

# ---------------------------------------------------------------- preflight (no writes)
echo "== preflight =="

[ "$(id -u)" = "0" ] || die "must run as root: sudo bash ${BASH_SOURCE[0]}"

for t in awk sed systemctl ip nixos-rebuild nix-instantiate python3; do
  command -v "$t" >/dev/null 2>&1 || die "\`$t\` is not on PATH.
  sudo inherits the CALLER's PATH here (there is no secure_path), and python3 in
  particular is NOT in /run/current-system/sw/bin. Run this from a shell that has it:
    sudo env \"PATH=\$PATH\" bash ${BASH_SOURCE[0]}"
done

[ -r "$CHECK" ] || die "cannot read the verifier at $CHECK (run this from the repo checkout)"

# Host guard. Fail closed: no interface, or the wrong address, is an abort.
have_ip=$(ip -4 -o addr show "$IFACE" 2>/dev/null | awk '{print $4}' | cut -d/ -f1 | head -1)
[ -n "$have_ip" ] || die "no address on $IFACE -- is nebula running on this host?"
[ "$have_ip" = "$EXPECT_MESH_IP" ] || die "WRONG HOST: $IFACE is $have_ip, expected $EXPECT_MESH_IP.
  \`hostname\` does not discriminate between the machines on this fleet, so this guard
  uses the mesh address. If you really mean this host, re-run with
  NEBULA_EXPECT_MESH_IP=$have_ip"
echo "  host      : $IFACE = $have_ip  (matches the expected target)"

systemctl cat "$UNIT" >/dev/null 2>&1 || die "$UNIT is not loaded on this host"
echo "  unit      : $UNIT loaded"

# Already done? Ask the verifier, not the file: it reads the config the RUNNING unit
# loaded, so a stale edit that was never switched does not read as satisfied.
set +e
"$CHECK" "$RELAY" >/tmp/nebula-relay-pre.$$ 2>&1
pre_rc=$?
set -e
case "$pre_rc" in
  0) echo "  state     : ALREADY SATISFIED -- $RELAY is advertised and the unit is running it."
     sed 's/^/    | /' /tmp/nebula-relay-pre.$$; rm -f /tmp/nebula-relay-pre.$$
     echo; echo "Nothing to do. Exiting 0 without touching $CFG."
     exit 0 ;;
  1) echo "  state     : relay NOT advertised yet -- proceeding" ;;
  *) sed 's/^/    | /' /tmp/nebula-relay-pre.$$; rm -f /tmp/nebula-relay-pre.$$
     die "the verifier could not read the current config (rc=$pre_rc); fix that first" ;;
esac
rm -f /tmp/nebula-relay-pre.$$

[ -f "$CFG" ] && [ -w "$CFG" ] || die "$CFG is not a writable regular file"

# The anchor: `    settings = {` IMMEDIATELY followed by `      punchy = {`.
# The bare `settings = {` line is NOT unique in this file; the pair is. If that stops
# being true this aborts rather than guessing which one to patch.
anchors=$(awk 'NR==1{p=$0;next} {if(p=="    settings = {" && $0=="      punchy = {") n++; p=$0} END{print n+0}' "$CFG")
if [ "$anchors" != "1" ]; then
  echo "  anchor    : $anchors match(es) -- expected exactly 1" >&2
  if grep -qn 'relays = \[' "$CFG"; then
    echo "  NOTE: $CFG already contains a \`relays = [\` line:" >&2
    grep -n 'relays = \[' "$CFG" | sed 's/^/    /' >&2
    echo "  A relay list is already configured (apply-travel-prep.sh writes one too)." >&2
    echo "  Edit it by hand to include \"$RELAY\" -- this script will not merge lists." >&2
  fi
  die "cannot locate exactly one nebula settings block to patch; edit $CFG by hand"
fi
echo "  anchor    : exactly 1 nebula settings block found"
echo

# ------------------------------------------------------------------------ patch (temp)
echo "== patch =="
TMP="${CFG}.new.$$"
BAK="${CFG}.bak-nebula-relay-$(date +%Y%m%d-%H%M%S)-$$"
PATCHED=0
SWITCHED=0
OK=0

finish() {
  local rc=$?
  rm -f "$TMP"
  if [ "$OK" = "1" ]; then return; fi
  if [ "$PATCHED" = "1" ] && [ -f "$BAK" ]; then
    cp -p "$BAK" "$CFG"
    echo >&2
    echo "ROLLED BACK: $CFG restored from $BAK" >&2
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

awk -v relay="$RELAY" '
NR==1 { prev=$0; next }
{
  if (prev == "    settings = {" && $0 == "      punchy = {") {
    print prev
    print "      relay = {"
    print "        use_relays = true;"
    print "        relays = [ \"" relay "\" ];"
    print "      };"
    n++
  } else {
    print prev
  }
  prev = $0
}
END { print prev; if (n+0 != 1) exit 3 }
' "$CFG" > "$TMP" || die "the patch pass did not make exactly one insertion"

added=$(( $(wc -l < "$TMP") - $(wc -l < "$CFG") ))
[ "$added" = "4" ] || die "expected the patch to add exactly 4 lines, it added $added"
echo "  temp file : +4 lines"

nix-instantiate --parse "$TMP" >/dev/null 2>&1 || die "the patched file is not valid Nix -- $CFG untouched"
echo "  nix parse : OK"

echo "  diff:"
diff -u "$CFG" "$TMP" | sed 's/^/    /' || true

[ -e "$BAK" ] && die "backup path $BAK already exists; refusing to overwrite it"
cp -p "$CFG" "$BAK"
echo "  backup    : $BAK"

mv "$TMP" "$CFG"
PATCHED=1
echo "  applied   : $CFG"
echo

# ---------------------------------------------------------------------------- rebuild
echo "== nixos-rebuild switch =="
nixos-rebuild switch
SWITCHED=1
echo

# ----------------------------------------------------------------------------- verify
echo "== verify =="
active=$(systemctl is-active "$UNIT" || true)
[ "$active" = "active" ] || die "$UNIT is '$active' after the switch, not 'active'"
echo "  unit      : active"

# The verifier reads the RUNNING process, not the unit file, so it returns 2 ("cannot
# determine") if the switch landed a new config that nebula has not restarted onto.
# `nixos-rebuild switch` normally restarts a changed unit; when it has not, restarting
# it here is the expected completion of the change we just made, not an escalation.
# One retry only -- a second failure is a real failure and rolls back.
set +e; "$CHECK" "$RELAY"; check_rc=$?; set -e
if [ "$check_rc" = "2" ]; then
  echo "  retry     : the running process has not picked up the new config; restarting $UNIT once"
  systemctl restart "$UNIT"
  set +e; "$CHECK" "$RELAY"; check_rc=$?; set -e
fi
[ "$check_rc" = "0" ] || die "the verifier does not see $RELAY advertised by the running unit (rc=$check_rc)"

OK=1
echo
echo "=== DONE ==="
echo "$RELAY is now advertised as a relay by the running $UNIT."
echo "Backup of the previous config: $BAK"
echo "To revert:  sudo cp $BAK $CFG && sudo nixos-rebuild switch"
