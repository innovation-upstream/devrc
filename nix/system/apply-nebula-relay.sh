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
# activates it.
#
# Overrides (all optional):
#   NEBULA_NET=mesh                 the network under services.nebula.networks
#   NEBULA_RELAY=10.42.0.2          the relay to advertise (mesh IP)
#   NEBULA_EXPECT_MESH_IP=10.42.0.30  the host guard -- see "WHICH HOST" below
#   NEBULA_CFG=/etc/nixos/configuration.nix
#
# 🔴 THIS RESTARTS THE MESH. Activating the change reloads nebula@<net>, which drops
# every session over nebula.<net> for a few seconds -- and the verify step may restart
# the unit once more. If the shell you are running this from reaches this host OVER
# the mesh, you will be disconnected mid-run and the trap will not get to finish. Run
# it from a console or a LAN/loopback session.
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
#
# 🔴 WHY `test` BEFORE `switch`, AND WHY THAT IS THE WHOLE POINT.
# `nixos-rebuild switch` registers the built system into /nix/var/nix/profiles/system
# and installs the bootloader BEFORE it runs the activation script. So a switch whose
# activation FAILS has still moved the profile and the boot entry: restoring the config
# file leaves the machine booting into the change on the next reboot. An earlier version
# of this script called `switch` directly and its trap printed "the system was never
# switched, so nothing is running the change" -- which was measured false on this host
# (generation 387 was registered, with `relays: - 10.42.0.2` in its nebula config,
# after the script reported a clean rollback).
#
# `nixos-rebuild test` activates WITHOUT registering a profile generation and WITHOUT
# touching the bootloader. So the order here is:
#
#   1. nixos-rebuild test    -- activate only; a failure persists nothing
#   2. verify                -- the running unit really advertises the relay
#   3. nixos-rebuild switch  -- only now persist it (cached after step 1, so cheap)
#   4. verify again
#
# and the trap tracks WHICH of those was reached, because "never activated",
# "activated but not persisted" and "persisted" are three different things for the
# operator to undo. It never claims more than it knows.
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

# Scratch dir for everything this script writes outside $CFG.
#
# 🔴 NOT `/tmp/<fixed-name>.$$`. /tmp is 1777, `>` follows symlinks, and this runs as
# root: a predictable path there is an arbitrary-file-overwrite primitive for any local
# user who pre-creates the symlink. `mktemp -d` gives a 0700 directory with an
# unpredictable name, and the trap removes it.
SCRATCH="$(mktemp -d -t nebula-relay.XXXXXXXX)" || die "mktemp -d failed"
TMP=""   # set in the patch section; a sibling of $CFG so the `mv` is atomic
cleanup_scratch() { rm -rf "$SCRATCH"; [ -n "$TMP" ] && rm -f "$TMP"; return 0; }
trap cleanup_scratch EXIT

# ---------------------------------------------------------------- preflight (no writes)
echo "== preflight =="

[ "$(id -u)" = "0" ] || die "must run as root: sudo bash ${BASH_SOURCE[0]}"

# Everything this script execs. Kept complete on purpose: the point of a preflight is
# that a missing tool aborts BEFORE the first write, and a list that omits half of them
# fails somewhere in the middle instead.
for t in awk sed grep diff tr cut wc cp mv date mktemp readlink \
         systemctl ip nixos-rebuild nix-instantiate python3; do
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
PRE="$SCRATCH/pre.out"
set +e
"$CHECK" "$RELAY" >"$PRE" 2>&1
pre_rc=$?
set -e
case "$pre_rc" in
  0) echo "  state     : ALREADY SATISFIED -- $RELAY is advertised and the unit is running it."
     sed 's/^/    | /' "$PRE"
     echo; echo "Nothing to do. Exiting 0 without touching $CFG."
     exit 0 ;;
  1) echo "  state     : relay NOT advertised yet -- proceeding"
     # 🔴 SHOW THE VERIFIER'S FAIL OUTPUT. It carries the 🔴 paragraph about relayed
     # traffic egressing the relay -- i.e. that pointing this at a billed cloud host
     # puts every relayed byte on that bill. It was written to be read AT THE MOMENT
     # OF CHOOSING, and an earlier version of this script captured it and deleted it
     # unread, which made the warning inert.
     echo
     echo "  the verifier's finding, in full -- read the cost note before continuing:"
     sed 's/^/    | /' "$PRE"
     echo ;;
  *) sed 's/^/    | /' "$PRE"
     die "the verifier could not read the current config (rc=$pre_rc); fix that first" ;;
esac

# 🔴 SYMLINKS ARE REFUSED, NOT FOLLOWED -- and that is a deliberate choice between the
# two options, not an oversight.
#
# `[ -f "$CFG" ]` is true for a symlink to a regular file, and the patch below ends in
# `mv "$TMP" "$CFG"`, which REPLACES the symlink with a regular file. Measured: with
# /etc/nixos/configuration.nix symlinked into a git repo, the script exited 0 saying
# `=== DONE ===`, the symlink was gone, the repo's real file was never patched, and the
# printed revert command (`cp $BAK $CFG`) would have cemented the loss.
#
# Following it with `readlink -f` was the alternative. It is rejected because this runs
# as ROOT: resolving a path chosen by someone else and writing through it is the same
# hazard as the /tmp one above, one directory up. Refusing is the fail-closed half, and
# the operator's fix is one flag.
#
# The comparison is against `readlink -f`, not `[ -L ]`, so a symlinked DIRECTORY
# component (/etc/nixos -> /home/x/repo) is caught too; `[ -L ]` only inspects the last
# component and would wave that through.
cfg_real="$(readlink -f -- "$CFG" 2>/dev/null || true)"
[ -n "$cfg_real" ] || die "$CFG does not resolve to any path (dangling symlink?)"
if [ "$cfg_real" != "$CFG" ]; then
  die "$CFG is a symlink (or sits under one); it resolves to
    $cfg_real
  This script will not write through a symlink as root -- \`mv\` would replace the link
  with a regular file and silently orphan the real one. Re-run against the real file:
    sudo NEBULA_CFG=$cfg_real bash ${BASH_SOURCE[0]}
  (and if that file is in a git repo, commit the result there.)"
fi

[ -f "$CFG" ] && [ -w "$CFG" ] || die "$CFG is not a writable regular file"

# ---------------------------------------------------------- locate the network's block
# 🔴 THE ANCHOR MUST BE INSIDE services.nebula.networks.<NET>, NOT ANYWHERE IN THE FILE.
# The anchor is the line pair `    settings = {` + `      punchy = {`. That pair is tied
# to nothing on its own: measured, `NEBULA_NET=travel` reported
# "DONE ... advertised by nebula@travel.service" while the four lines had landed in the
# MESH network's settings block. So the block is located first and the anchor is only
# accepted inside it.
#
# Locating is by brace depth from the `services.nebula.networks.<NET> = {` line, with
# `#` comments and string bodies skipped (`${...}` interpolation inside a string counts
# as code so it balances). That scanner is deliberately simple; every way it can be
# wrong ends in an abort rather than a guess -- no unique declaration, depth that never
# returns to zero, or an anchor count inside the range that is not exactly 1.
locate_out="$SCRATCH/locate.out"
set +e
python3 - "$CFG" "$NET" >"$locate_out" 2>&1 <<'PY'
import re, sys

path, net = sys.argv[1], sys.argv[2]
try:
    lines = open(path, encoding="utf-8", errors="strict").read().splitlines()
except Exception as e:
    sys.stderr.write("locate: %s\n" % e)
    sys.exit(2)

decl = re.compile(r"^\s*services\.nebula\.networks\." + re.escape(net) + r"\s*=\s*\{\s*$")
starts = [i for i, l in enumerate(lines) if decl.match(l)]
if len(starts) != 1:
    sys.stderr.write(
        "locate: found %d line(s) matching `services.nebula.networks.%s = {` in %s; "
        "expected exactly 1\n" % (len(starts), net, path))
    for i in starts:
        sys.stderr.write("  %d: %s\n" % (i + 1, lines[i]))
    if not starts:
        sys.stderr.write(
            "  (a `services.nebula.networks = { %s = { ... }; }` style declaration is\n"
            "   NOT recognised -- this script only patches the dotted form)\n" % net)
    sys.exit(3)

def code_only(line, state):
    """Return (braces-bearing text, new state). state: 0 code, 1 \"..\", 2 ''..''.

    Comments and string BODIES contribute no braces, but `${` inside a string opens
    an interpolation that a `}` closes, so those two are emitted to keep the count
    balanced. Deliberately simple: every way it can be wrong ends in an abort here
    or in the anchor count, never in a silent mis-patch."""
    out, i, n = [], 0, len(line)
    while i < n:
        if state == 0:
            if line[i] == "#":
                break
            if line.startswith("''", i):
                state = 2; i += 2; continue
            if line[i] == '"':
                state = 1; i += 1; continue
            out.append(line[i]); i += 1
        elif state == 1:
            if line[i] == "\\":
                i += 2; continue
            if line.startswith("${", i):
                out.append("{"); i += 2; continue
            if line[i] == "}":
                out.append("}"); i += 1; continue
            if line[i] == '"':
                state = 0; i += 1; continue
            i += 1
        else:
            if line.startswith("''", i):
                state = 0; i += 2; continue
            if line.startswith("${", i):
                out.append("{"); i += 2; continue
            if line[i] == "}":
                out.append("}"); i += 1; continue
            i += 1
    return "".join(out), state

start = starts[0]
depth, state, end = 0, 0, None
for i in range(start, len(lines)):
    code, state = code_only(lines[i], state)
    depth += code.count("{") - code.count("}")
    if depth <= 0:
        end = i
        break
if end is None:
    sys.stderr.write(
        "locate: the `services.nebula.networks.%s = {` block starting at line %d never\n"
        "  closes (brace depth never returns to 0). Refusing to guess where it ends.\n"
        % (net, start + 1))
    sys.exit(4)

print("%d %d" % (start + 1, end + 1))
PY
loc_rc=$?
set -e
if [ "$loc_rc" != "0" ]; then
  sed 's/^/    | /' "$locate_out" >&2
  die "cannot identify the services.nebula.networks.${NET} block in $CFG unambiguously"
fi
read -r NET_START NET_END < "$locate_out"
echo "  net block : services.nebula.networks.${NET} = lines ${NET_START}-${NET_END}"

# The anchor, counted ONLY inside that range.
anchors=$(awk -v s="$NET_START" -v e="$NET_END" '
  NR < s || NR > e { prev = $0; next }
  { if (prev == "    settings = {" && $0 == "      punchy = {") n++; prev = $0 }
  END { print n+0 }' "$CFG")
if [ "$anchors" != "1" ]; then
  echo "  anchor    : $anchors match(es) inside services.nebula.networks.${NET} -- expected exactly 1" >&2
  if grep -qn 'relays = \[' "$CFG"; then
    echo "  NOTE: $CFG already contains a \`relays = [\` line:" >&2
    grep -n 'relays = \[' "$CFG" | sed 's/^/    /' >&2
    echo "  A relay list is already configured (apply-travel-prep.sh writes one too)." >&2
    echo "  Edit it by hand to include \"$RELAY\" -- this script will not merge lists." >&2
  fi
  die "cannot locate exactly one nebula settings block to patch inside
  services.nebula.networks.${NET} (lines ${NET_START}-${NET_END}); edit $CFG by hand"
fi
echo "  anchor    : exactly 1 nebula settings block found inside services.nebula.networks.${NET}"
echo

# ------------------------------------------------------------------------ patch (temp)
echo "== patch =="
# A SIBLING of $CFG, not a file in $SCRATCH: the final step is `mv "$TMP" "$CFG"`, which
# is only atomic within one filesystem, and /tmp is routinely a different one. mktemp
# gives it an unpredictable name and 0600; the `cp -p` then gives it $CFG's mode and
# owner, which the `>` redirect below preserves (it truncates, it does not re-create).
TMP="$(mktemp "${CFG}.new.XXXXXXXX")" || die "cannot create a temp file next to $CFG"
cp -p "$CFG" "$TMP"
BAK="${CFG}.bak-nebula-relay-$(date +%Y%m%d-%H%M%S)-$$"

# Trap state. Each flag is set immediately AFTER the step it names succeeds, so the
# message the trap prints is what was actually reached, never what was intended.
PATCHED=0           # $CFG has been replaced by the patched file
TEST_ATTEMPTED=0    # `nixos-rebuild test` was started
ACTIVATED=0         # ... and returned 0: the change is RUNNING, nothing persisted
SWITCH_ATTEMPTED=0  # `nixos-rebuild switch` was started -- the profile MAY have moved
PERSISTED=0         # ... and returned 0: profile + bootloader now carry the change
OK=0

finish() {
  local rc=$?
  cleanup_scratch
  if [ "$OK" = "1" ]; then return; fi
  if [ "$PATCHED" != "1" ]; then exit $rc; fi

  echo >&2
  if [ -f "$BAK" ] && cp -p "$BAK" "$CFG"; then
    echo "ROLLED BACK: $CFG restored from $BAK" >&2
  else
    # 🔴 NEVER SILENT. Without this branch a missing/unreadable backup skipped the
    # restore and printed NOTHING, leaving the config patched while the header promised
    # a restore on any failure. Measured.
    echo "🔴 ROLLBACK FAILED — your config is still patched at $CFG" >&2
    if [ -f "$BAK" ]; then
      echo "   The backup exists at $BAK but restoring it failed (permissions? disk full?)." >&2
      echo "   Fix by hand:  sudo cp -p $BAK $CFG" >&2
    else
      echo "   The backup $BAK is GONE, so there is nothing to restore from." >&2
      echo "   Fix by hand: delete the four inserted lines from services.nebula.networks.${NET}" >&2
      echo "     relay = {" >&2
      echo "       use_relays = true;" >&2
      echo "       relays = [ \"$RELAY\" ];" >&2
      echo "     };" >&2
      echo "   (or \`sudo git -C \$(dirname $CFG) checkout -- $CFG\` if it is in a repo)," >&2
      echo "   then \`sudo nixos-rebuild switch\`." >&2
    fi
  fi

  # What is RUNNING / PERSISTED is a separate claim from what is in the FILE. Say only
  # what this run actually reached.
  if [ "$PERSISTED" = "1" ]; then
    echo "🔴 PERSISTED. \`nixos-rebuild switch\` succeeded before this failure, so the" >&2
    echo "   change is running AND registered in /nix/var/nix/profiles/system and the" >&2
    echo "   bootloader. Restoring the file above is NOT enough -- run" >&2
    echo "   \`sudo nixos-rebuild switch\` to return the running system and the profile." >&2
  elif [ "$SWITCH_ATTEMPTED" = "1" ]; then
    echo "🔴 THE PROFILE MAY HAVE MOVED. \`nixos-rebuild switch\` was started and did not" >&2
    echo "   report success. switch registers the profile generation and installs the" >&2
    echo "   bootloader BEFORE it activates, so a failure part-way can still have moved" >&2
    echo "   both. This script does NOT know which side of that line it failed on." >&2
    echo "   Check:  readlink /nix/var/nix/profiles/system" >&2
    echo "           nixos-rebuild list-generations | tail -5" >&2
    echo "   If a new generation is there, \`sudo nixos-rebuild switch\` on the restored" >&2
    echo "   config puts the profile and the boot entry back." >&2
  elif [ "$ACTIVATED" = "1" ]; then
    echo "   ACTIVATED, NOT PERSISTED. \`nixos-rebuild test\` activated the change in the" >&2
    echo "   RUNNING system, but test registers no profile generation and does not touch" >&2
    echo "   the bootloader -- so nothing was persisted and a reboot returns to the" >&2
    echo "   current system. To return it NOW without rebooting, run" >&2
    echo "   \`sudo nixos-rebuild test\` on the restored config." >&2
  elif [ "$TEST_ATTEMPTED" = "1" ]; then
    echo "   NOT PERSISTED. \`nixos-rebuild test\` was started and did not succeed; it" >&2
    echo "   registers no profile generation and does not touch the bootloader, so" >&2
    echo "   nothing is persisted and a reboot returns to the current system." >&2
    echo "   Activation may have run part-way, so the RUNNING system can be in a mixed" >&2
    echo "   state -- \`sudo nixos-rebuild test\` on the restored config settles it." >&2
  else
    echo "   NEVER ACTIVATED. No rebuild was started, so nothing is running the change" >&2
    echo "   and no generation was registered. Only the file had been touched." >&2
  fi
  exit $rc
}
trap finish EXIT

awk -v relay="$RELAY" -v s="$NET_START" -v e="$NET_END" '
NR==1 { prev=$0; next }
{
  if (NR-1 >= s && NR <= e && prev == "    settings = {" && $0 == "      punchy = {") {
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
TMP=""            # moved, not leaked -- keep the cleanup honest
PATCHED=1
echo "  applied   : $CFG"
echo

# ------------------------------------------------------- activate WITHOUT persisting
# See the header. `test` does not register a profile generation and does not touch the
# bootloader, which is the only reason the rollback below can honestly claim that a
# failure here persisted nothing.
echo "== nixos-rebuild test (activate, persist nothing) =="
echo "  🔴 this reloads $UNIT -- mesh sessions over $IFACE will drop for a few seconds"
TEST_ATTEMPTED=1
nixos-rebuild test
ACTIVATED=1
echo

# ------------------------------------------------------------------ verify (activated)
echo "== verify (activated) =="
verify_now() {   # dies on failure; retries exactly once, on ONE narrow condition
  local active out rc
  active=$(systemctl is-active "$UNIT" || true)
  [ "$active" = "active" ] || die "$UNIT is '$active' after the rebuild, not 'active'"
  echo "  unit      : active"

  out="$SCRATCH/check.out"
  set +e; "$CHECK" "$RELAY" >"$out" 2>&1; rc=$?; set -e
  sed 's/^/    | /' "$out"

  # 🔴 THE RETRY IS NARROWER THAN rc 2. The verifier returns 2 for seven different
  # situations (unit not loaded, unit inactive, no MainPID, unreadable -config, a
  # parser failure, its own self-test failing, and the unit/process disagreeing).
  # Only the LAST of those is "the rebuild landed a new config nebula has not restarted
  # onto", and only that one is completed by a restart; restarting for a parser failure
  # drops every mesh session for no reason at all. The verifier prints a machine-
  # readable `REASON: <token>` on every rc-2 path exactly so this branch can be a test
  # on the CONDITION rather than on the exit code.
  if [ "$rc" = "2" ] && grep -qx 'REASON: unit-process-disagree' "$out"; then
    echo "  retry     : the running process has not picked up the new config; restarting $UNIT once"
    systemctl restart "$UNIT"
    set +e; "$CHECK" "$RELAY" >"$out" 2>&1; rc=$?; set -e
    sed 's/^/    | /' "$out"
  fi
  [ "$rc" = "0" ] || die "the verifier does not see $RELAY advertised by the running unit (rc=$rc)"
}
verify_now
echo "  verified  : running, but NOT yet persisted"
echo

# -------------------------------------------------------------------------- persist
# The store paths are already built and the activation already proven, so this is the
# cheap half. It is also the half that moves /nix/var/nix/profiles/system and the boot
# entry, which is why SWITCH_ATTEMPTED is set before it and not after.
echo "== nixos-rebuild switch (persist) =="
SWITCH_ATTEMPTED=1
nixos-rebuild switch
PERSISTED=1
echo

# ------------------------------------------------------------------ verify (persisted)
echo "== verify (persisted) =="
verify_now

OK=1
echo
echo "=== DONE ==="
echo "$RELAY is now advertised as a relay by the running $UNIT,"
echo "and the change is persisted in /nix/var/nix/profiles/system."
echo "Backup of the previous config: $BAK"
echo "To revert:  sudo cp -p $BAK $CFG && sudo nixos-rebuild switch"
