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
# SAFETY: nothing is written until every precondition passes -- including a dry-build of
# the CURRENT, unpatched config, so that any later failure is attributable to this change.
# Then, in order: patch a temp copy, parse it (and the pre-patch file, as a control), take
# a uniquely-named backup (never overwriting one), move it into place, and only then
# rebuild -- with a trap that restores the backup on ANY later failure. Re-running once
# the address is gone is a no-op.
#
# 🔴 WHAT THIS SCRIPT CANNOT UNDO: `nixos-rebuild switch` applies EVERY pending edit in
# $CFG, not only the token removed here, and the rollback restores only that token. If
# you have unrelated half-finished edits in configuration.nix, this switches them live.
# The preflight dry-build tells you the file builds; it does not tell you that you meant
# everything in it.
set -euo pipefail

NET="${NEBULA_NET:-mesh}"
EXPECT_MESH_IP="${NEBULA_EXPECT_MESH_IP:-10.42.0.100}"
CFG="${NEBULA_CFG:-/etc/nixos/configuration.nix}"
UNIT="nebula@${NET}.service"
IFACE="nebula.${NET}"
die() { echo "ABORT: $*" >&2; exit 1; }

# 🔴 EXPLICIT, because the fallthrough default here is DESTRUCTIVE. An earlier draft did
# `MODE="${1:-apply}"` and compared it only against `--check`, so `--dry-run`, `--help`,
# `-c` and `check` all ran the full patch + `nixos-rebuild switch`. For a root script that
# rebuilds the OS, the natural exploratory invocations were the destructive ones.
case "${1-}" in
  ""|apply) MODE=apply ;;
  --check)  MODE=check ;;
  *) echo "unknown argument: $1" >&2
     echo "usage: bash ${BASH_SOURCE[0]} --check     # read-only, no root" >&2
     echo "       sudo env \"PATH=\$PATH\" bash ${BASH_SOURCE[0]}   # apply" >&2
     exit 64 ;;
esac

# ---------------------------------------------------------------------------- --check
# Reads the config the RUNNING unit actually loaded, so a stale edit that was never
# switched does not read as satisfied. Needs no root: /nix/store is world-readable.
#   rc 0 = the dead entry is GONE      rc 1 = still present      rc 2 = cannot tell
if [ "$MODE" = "check" ]; then
  pid=$(systemctl show -p MainPID --value "$UNIT" 2>/dev/null || true)
  [ -n "$pid" ] && [ "$pid" != "0" ] || { echo "cannot tell: $UNIT has no running MainPID"; exit 2; }

  # 🔴 READ THE RUNNING PROCESS, NOT `systemctl cat`. The unit file reflects what is on
  # disk AFTER a switch; the process was started with whatever was there BEFORE it. If a
  # switch installs a new unit but the restart is deferred, `is-active` is still `active`
  # and the unit file already shows the new config -- so a check reading it reports PASS
  # while the live process still advertises the dead address. /proc/<pid>/cmdline is what
  # the process was actually given.
  running=$(tr '\0' '\n' < "/proc/$pid/cmdline" 2>/dev/null \
            | awk 'p=="-config"{print; exit} {p=$0}')
  [ -n "$running" ] && [ -r "$running" ] || {
    echo "cannot tell: could not read the -config path from /proc/$pid/cmdline"; exit 2; }

  echo "unit    : $UNIT ($(systemctl is-active "$UNIT"), pid $pid)"
  echo "running : $running   (read from the PROCESS, not the unit file)"
  block=$(sed -n '/^static_host_map:/,/^[a-z]/p' "$running")
  echo "$block" | sed 's/^/  | /'

  # 🔴 POSITIVE CONTROL. Without this the check degrades silently to "always PASS": if the
  # emitter ever writes quoted scalars, flow style, a quoted `static_host_map` key, or an
  # inline comment, the extraction returns nothing (or nothing this pattern matches) and a
  # live :443 reads as clean. A static_host_map that carries no `:4242` at all is not a
  # config this check can speak about -- say so instead of passing.
  printf '%s' "$block" | grep -qE ':4242' || {
    echo "cannot tell: no ':4242' address found in the extracted static_host_map block."
    echo "             The block is empty or in a shape this check cannot read, so a"
    echo "             ':443' could be present and invisible here. NOT a pass."
    exit 2; }

  if printf '%s' "$block" | grep -qE ':443([^0-9]|$)'; then
    echo "FAIL: a ':443' address is still advertised -- nebula will keep handshaking a socket"
    echo "      that does not speak nebula (443/udp there is WireGuard)."
    exit 1
  fi
  echo "PASS: no ':443' entry in the running static_host_map (and the block was readable)."
  exit 0
fi

# ------------------------------------------------------------------ preflight (no writes)
echo "== preflight =="
[ "$(id -u)" = "0" ] || die "must run as root: sudo env \"PATH=\$PATH\" bash ${BASH_SOURCE[0]}"

# `diff` and `wc` are load-bearing in the verify block below, not conveniences: without
# `diff` the changed-line count resolves to 0 and the script dies with a WRONG diagnosis
# ("expected exactly 1 changed line, got 0") from the very check meant to give the right
# one; without `wc` the line-count comparison compares "" to "" and passes vacuously.
for t in awk sed grep diff wc systemctl ip nixos-rebuild nix-instantiate; do
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

# 🔴 THE PRE-EXISTING TREE MUST ALREADY BE SWITCHABLE — this is the real stranding vector,
# and it is not the :443 removal. `nixos-rebuild switch` applies EVERY pending edit in
# $CFG, not just this one token, so a half-finished unrelated edit sitting in that file
# gets switched into the running system by this script. The rollback below restores only
# this script's token, leaving those live. Establishing that the CURRENT file already
# builds means any later failure is attributable to this change.
echo "  control   : dry-building the CURRENT (unpatched) config..."
if ! nixos-rebuild dry-build >/dev/null 2>&1; then
  die "$CFG does NOT dry-build as it stands, BEFORE this script changes anything.
  Something else in that file is already broken. Fix that first: if this script ran now,
  \`nixos-rebuild switch\` would try to apply those edits too, and the rollback only
  restores the one token this script removes."
fi
echo "  control   : the unpatched config dry-builds, so a later failure is THIS change"

# ⚠ Also true and worth knowing before you run it: a switch activates whatever else is
# pending in $CFG. This script cannot un-apply those.

# The line, in the shape apply-nebula-443.sh leaves behind:
#   "10.42.0.2" = [ "<ip>:4242" "<ip>:443" ];
# Require EXACTLY ONE. Zero means it is already gone (or hand-edited into another shape,
# which this must not guess at); more than one means several hosts and no single answer.
# 🔴 EVERY `:443` SEARCH BELOW IS SCOPED TO staticHostMap-SHAPED LINES, NEVER THE WHOLE
# FILE. An earlier draft grepped $CFG entire, which meant ONE unrelated quoted `:443`
# anywhere in configuration.nix -- an nginx `proxyPass = "https://upstream:443"` is the
# obvious one -- aborted the run with "a ':443' string survived the patch", blaming a
# substitution that had in fact worked. On such a file the script could NEVER succeed,
# and its "re-running once it is gone is a no-op" was false for the same reason. It
# failed closed, so it was never dangerous; it was unusable, which on the one machine
# this was written for is nearly as bad.
hostmap_443() { grep -nE '^[^"]*"[0-9.]+" = \[[^]]*:443"' "$1" || true; }

matches=$(grep -cE '"[0-9.]+" = \[ "[0-9.]+:4242" "[0-9.]+:443" \];' "$CFG" || true)
if [ "$matches" = "0" ]; then
  stray=$(hostmap_443 "$CFG")
  if [ -n "$stray" ]; then
    echo "  NOTE: a ':443' address is present in a staticHostMap entry, but not in the" >&2
    echo "        expected one-line shape this script knows how to edit:" >&2
    printf '%s\n' "$stray" | sed 's/^/    /' >&2
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
# The `\2` BACKREFERENCE makes the substitution itself require the two addresses to name
# the SAME host. The shell guard below already refuses a mismatched pair, so this is
# defence in depth -- but it means the expression cannot strip a `:443` belonging to some
# other host even if it is ever reused or reached out of order. (Found by writing the
# test: the previous expression matched regardless of IP, and only the guard's ORDERING
# kept that safe.)
sed -E 's/^([^"]*"[0-9.]+" = \[ "([0-9.]+):4242") "\2:443"( \];.*)$/\1\3/' "$CFG" > "$TMP"

# Verify the edit did exactly what was intended -- the substitution count is not
# reported by sed, so it is reconstructed from the result rather than assumed.
[ "$(wc -l < "$TMP")" = "$(wc -l < "$CFG")" ] || die "line count changed; this edit removes a token, not a line"
changed=$(diff "$CFG" "$TMP" | grep -c '^<' || true)
[ "$changed" = "1" ] || die "expected exactly 1 changed line, got $changed -- $CFG untouched"
[ -z "$(hostmap_443 "$TMP")" ] || die "a ':443' address survived the patch in a staticHostMap
  entry -- refusing to continue:
$(hostmap_443 "$TMP" | sed 's/^/    /')"
echo "  temp file : :443 address removed, 1 line changed, line count unchanged"

# Control first: if $CFG was ALREADY unparseable, the die below would misattribute that to
# the patch. (`nixos-rebuild dry-build` in preflight covers the stronger property; this is
# the cheap local one that keeps THIS message honest.)
nix-instantiate --parse "$CFG" >/dev/null 2>&1 \
  || die "$CFG does not parse as Nix BEFORE this patch -- not caused by this script"
nix-instantiate --parse "$TMP" >/dev/null 2>&1 || die "the patched file is not valid Nix -- $CFG untouched"
echo "  nix parse : OK (and the pre-patch file parsed too, so this is attributable)"
echo "  diff:"; diff -u "$CFG" "$TMP" | sed 's/^/    /' || true

[ -e "$BAK" ] && die "backup path $BAK already exists; refusing to overwrite it"
cp -p "$CFG" "$BAK"; echo "  backup    : $BAK"
mv "$TMP" "$CFG"; PATCHED=1; echo "  applied   : $CFG"
echo

echo "== nixos-rebuild switch =="
# 🔴 SET BEFORE, NOT AFTER. `switch-to-configuration` restarts units and THEN runs
# activation, so it can exit non-zero with services already cycled. Setting this only on
# success made the trap print "The system was never switched, so nothing is running the
# change." for exactly the case where something IS -- and omit the instruction to switch
# back. A false reassurance in the rollback path is worse than no message.
SWITCHED=1
nixos-rebuild switch
echo

echo "== verify =="
active=$(systemctl is-active "$UNIT" || true)
[ "$active" = "active" ] || die "$UNIT is '$active' after the switch, not 'active'"
echo "  unit      : active"

# 🔴 THREE OUTCOMES, NOT TWO. `--check` returns 2 for "cannot tell", and collapsing that
# into the failure branch rolled the change back while asserting ":443 is still
# advertised" -- a claim the check never made -- and then offered a rollback command that
# RE-ADDS the dead entry. Retry once (the unit may still be settling), then be honest.
check_one() { set +e; bash "${BASH_SOURCE[0]}" --check; local r=$?; set -e; return $r; }
check_one; check_rc=$?
if [ "$check_rc" = "2" ]; then
  echo "  (verifier could not read the running config; waiting 5s and retrying once)"
  sleep 5
  check_one; check_rc=$?
fi
case "$check_rc" in
  0) OK=1 ;;
  1) die "the running config STILL advertises a :443 address after the switch" ;;
  *) die "COULD NOT VERIFY after the switch (rc=$check_rc). This is NOT evidence the
  change failed, and NOT evidence it worked -- the verifier could not read the running
  config. Rolling back, because an unverified state on this machine is the thing we are
  trying not to leave behind. Re-run \`--check\` by hand before deciding." ;;
esac

# Reachability, not just "the unit came back". Advisory ON PURPOSE: ICMP to a lighthouse's
# mesh address is measured-flaky on this fleet (10.42.0.2 answered from the workbench
# after one restart and not after another, while discovery worked throughout), so a failed
# ping here is NOT evidence the mesh is down and must not roll back a good change.
probe="${NEBULA_PROBE:-10.42.0.2}"
if ping -c1 -W3 "$probe" >/dev/null 2>&1; then
  echo "  mesh      : $probe answers ICMP"
else
  echo "  ⚠ mesh    : $probe did NOT answer ICMP. On this fleet that is flaky and is not"
  echo "              by itself a failure -- but VERIFY REACHABILITY BY HAND before you"
  echo "              rely on this machine remotely (ssh over the mesh, or nebula logs)."
fi

echo
echo "=== Done. What this did and did NOT buy ==="
echo " * REMOVED a fallback that could never work. It did not ADD one."
echo " * If UDP 4242 is blocked, there is still NO nebula fallback. Tailscale is the"
echo "   answer to that (clawgate #497), and it is not done yet."
echo " * Rollback: sudo cp $BAK $CFG && sudo nixos-rebuild switch"
