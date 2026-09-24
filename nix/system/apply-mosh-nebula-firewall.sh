#!/usr/bin/env bash
# Open mosh's UDP port range to the NEBULA INTERFACE ONLY, then rebuild.
#
#   sudo bash nix/system/apply-mosh-nebula-firewall.sh
#
# Run it on whichever host you want to mosh *into*. For the measured case
# (laptop off-LAN, working on the workbench) that is the WORKBENCH; run it on
# both if you want the connection to work in either direction.
#
# WHY THIS EXISTS
#   mosh-server binds an ephemeral UDP port in 60000-61000 on the machine you
#   connect TO. Neither host's `networking.firewall` opens that range, so
#   `mosh <host>` sits at "Connecting..." until it times out, with no
#   diagnostic naming the firewall. The mosh package alone (nix/pkgs) is not
#   enough — this is the other half.
#
# WHY INTERFACE-SCOPED AND NOT `allowedUDPPorts`
#   Adding 60000-61000 to `networking.firewall.allowedUDPPorts` opens 1001 UDP
#   ports on EVERY interface, including the WAN-facing one. This instead uses
#   `interfaces."nebula.mesh"`, so the range is reachable only from inside the
#   mesh — where nebula's own firewall already gates who may speak at all
#   (verified 2026-09-23: this laptop's cert is in group `admin`, and the
#   workbench's nebula inbound rules allow `any`/`any` from `admin`).
#
# 🔴 THIS IS A MITIGATION FOR A FAULT THAT IS NOT ON EITHER HOST. The path to
#   home blacks out for ~6.5 s every ~1-2.5 min; mosh rides that gap instead of
#   freezing. It fixes nothing upstream. See
#   claudedocs/handoff-laptop-airvpn-tunnel.md → the mesh-flap investigation.
set -euo pipefail

CFG="/etc/nixos/configuration.nix"
IFACE="nebula.mesh"
MARK="apply-mosh-nebula-firewall"

[ -r "$CFG" ] || { echo "ERROR: cannot read $CFG — run me under sudo"; exit 1; }

if grep -q "$MARK" "$CFG"; then
  echo "Already configured (marker '$MARK' present) — skipping the edit."
  echo "Rebuilding anyway so a half-applied run converges..."
else
  # Anchor on the firewall block's own opening line. Verified present on both
  # hosts 2026-09-23 as `  networking.firewall = {`.
  ANCHOR='^\s*networking\.firewall = {'
  if [ "$(grep -cE "$ANCHOR" "$CFG")" -ne 1 ]; then
    echo "ERROR: expected exactly 1 'networking.firewall = {' in $CFG, found" \
         "$(grep -cE "$ANCHOR" "$CFG"). Refusing to guess — edit by hand."
    exit 1
  fi

  cp -a "$CFG" "$CFG.bak-mosh"
  echo "Backed up $CFG -> $CFG.bak-mosh"

  # Insert our keys immediately after the opening brace, leaving every existing
  # key untouched.
  awk -v mark="$MARK" -v iface="$IFACE" '
    /^[[:space:]]*networking\.firewall = \{/ && !done {
      print
      print "    # " mark ": mosh-server binds an ephemeral UDP port in this"
      print "    # range. Scoped to the mesh interface so it is NOT exposed on"
      print "    # the WAN — nebula'\''s own firewall gates who may reach it."
      print "    interfaces.\"" iface "\".allowedUDPPortRanges = ["
      print "      { from = 60000; to = 61000; }"
      print "    ];"
      done = 1
      next
    }
    { print }
  ' "$CFG" > "$CFG.new"

  if ! grep -q "$MARK" "$CFG.new"; then
    echo "ERROR: awk produced no marker — leaving $CFG untouched."
    rm -f "$CFG.new"
    exit 1
  fi

  # Parse-validate BEFORE moving it into place. A broken configuration.nix is
  # how a remote host becomes unrebuildable.
  if ! nix-instantiate --parse "$CFG.new" >/dev/null 2>&1; then
    echo "ERROR: the edited file does not parse as Nix. Original is untouched;"
    echo "the rejected candidate is at $CFG.new for inspection."
    exit 1
  fi

  mv "$CFG.new" "$CFG"
  echo "[1/2] Added UDP 60000-61000 on $IFACE"
fi

echo "[2/2] Rebuilding..."
nixos-rebuild switch

cat <<'VERIFY'

Applied. Verify from the OTHER host (this proves the path, not just the config):

    mosh --ssh="ssh -o ConnectTimeout=10" zach@10.42.0.30 -- true && echo MOSH-OK

🔴 A successful `mosh` is the only verification. `nixos-rebuild switch`
reporting success is a claim about the REBUILD, not about reachability.

If it hangs at "Connecting...", check in this order:
    ss -lunp 'sport >= :60000 and sport <= :61000'   # on THIS host, while connecting
    command -v mosh-server                           # must be on PATH for the ssh session
VERIFY
