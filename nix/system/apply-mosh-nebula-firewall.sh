#!/usr/bin/env bash
# Stage mosh on this host: the NixOS module plus a UDP range opened on the
# NEBULA INTERFACE ONLY. By default it EDITS AND STOPS -- it does not switch.
#
#   sudo bash nix/system/apply-mosh-nebula-firewall.sh              # edit + dry-build, NO switch
#   sudo MOSH_FW_SWITCH=1 bash nix/system/apply-mosh-nebula-firewall.sh   # ... and switch
#
# Run it on whichever host you want to mosh *into*. For the measured case
# (laptop off-LAN, working on the workbench) that is the WORKBENCH; run it on
# both if you want the connection to work in either direction.
#
# 🔴 WHY THE DEFAULT DOES NOT SWITCH -- READ THIS BEFORE SETTING MOSH_FW_SWITCH.
#   /etc/nixos has NO flake, so it resolves nixpkgs from the root channel
#   (nixpkgs-unstable), which moves independently of anything in this repo.
#   MEASURED 2026-09-23 with `nixos-rebuild dry-build`, and the two hosts did
#   NOT agree -- which is the whole argument for printing rather than quoting:
#     * on the host this script was audited against, the UNEDITED config
#       already wanted 444 derivations built and 1,420 paths fetched, because
#       the channel had moved five days since that system was built;
#     * on the laptop the same day the UNEDITED config wanted 0 and 0, and this
#       change ALONE wanted 20 built / 16 fetched (measured end-to-end against
#       a copy of its real configuration.nix).
#   So a bare `nixos-rebuild switch` can activate a FULL SYSTEM UPDATE rather
#   than this firewall delta -- on a host that may be unreachable, over a link
#   that blacks out. If nebula or tailscale come back differently after that
#   update, you are locked out with no console. Whether that risk is live TODAY
#   on THIS host is exactly what the two columns below tell you.
#
#   So this script dry-builds the CURRENT config and the CANDIDATE config,
#   prints both counts and the delta between them, writes the edit, and stops.
#   The numbers it prints are measured on YOUR host at that moment, not the
#   example above. A large number there is pre-existing channel drift, NOT this
#   change; the delta between the two columns is this change.
#
#   MOSH_FW_SWITCH=1 is for when you are ON THE LAN with physical access to the
#   machine, so that a bad activation costs a walk to the keyboard rather than
#   the host.
#
# WHY THIS EXISTS
#   mosh-server binds one ephemeral UDP port in 60000-61000 on the machine you
#   connect TO. Neither host's `networking.firewall` opens that range, so
#   `mosh <host>` sits at "Connecting..." until it times out, with no
#   diagnostic naming the firewall. The mosh CLIENT package in nix/pkgs is not
#   enough -- this is the other half.
#
# WHAT IT INSERTS, AND WHY BOTH HALVES
#   programs.mosh = { enable = true; openFirewall = false; };
#   networking.firewall.interfaces."nebula.mesh".allowedUDPPortRanges =
#     [ { from = 60000; to = 61000; } ];
#
#   The MODULE earns its place for two things the hand-rolled range cannot
#   give: `security.wrappers.utempter` (its `withUtempter` option, default
#   true) -- without it mosh cannot write utmp, so `who` does not show the
#   session -- and mosh-server on the SYSTEM PATH, which is what the incoming
#   ssh exec needs. Its `openFirewall` defaults to TRUE and is explicitly
#   turned OFF here: it would add 60000-61000 to
#   `networking.firewall.allowedUDPPortRanges`, i.e. 1001 UDP ports on EVERY
#   interface including the WAN-facing one. The interface-scoped range is the
#   narrow replacement, and is the reason this script exists at all.
#
#   🔴 WHICH IS WHY "ALREADY APPLIED" REQUIRES `openFirewall = false`, NOT
#   merely the presence of `programs.mosh`. A hand-written
#   `programs.mosh.enable = true;` with no openFirewall line IS the WAN-wide
#   opening, not this change applied; the run refuses it, names the exposure
#   and spells the safe form rather than converging onto it.
#
#   The mosh entry in nix/pkgs/default.nix stays: that is the user-level CLIENT
#   on both hosts, installed by home-manager with no sudo. This module is the
#   SERVER side and only on the host you connect into.
#
# 🔴 WHO CAN REACH THE RANGE -- IT IS NOT ONLY THE ADMIN LAPTOP.
#   Scoping to `interfaces."nebula.mesh"` means the range is unreachable from
#   the WAN, but inside the mesh the gate is nebula's OWN firewall, and that is
#   wider than one machine. Its `inbound` rules allow any/any from every listed
#   GROUP, so every peer holding a cert in any of those groups can reach
#   60000-61000. MEASURED 2026-09-23 on the laptop: three such groups
#   (`lighthouse`, `admin`, `workbench`) plus icmp from any. The set is
#   per-host: read the target host's own
#   `services.nebula.networks.mesh.settings.firewall.inbound` before assuming
#   it is smaller. ("This laptop's cert is in group `admin`, and the workbench
#   allows any/any from `admin`" is true and is what makes mosh work -- it is
#   just not the whole exposure.)
#
# 🔴 THIS IS A MITIGATION FOR A FAULT THAT IS NOT ON EITHER HOST.
#   MEASURED off-LAN 2026-09-23, restated here rather than cited: the path to
#   home goes fully dark for ~6.5 s every ~1-2.5 min. nebula and tailscale
#   black out in the SAME second for the same duration, while a far endpoint of
#   ours that is not at home stays 0/140 over the same wifi, router, ISP and
#   distance -- so the fault is upstream of both hosts and nothing in this repo
#   causes or cures it. ssh experiences the gap as a frozen terminal and often a
#   banner-exchange timeout; mosh carries session state over UDP and rides it.
#   The write-up lives in `claudedocs/handoff-laptop-airvpn-tunnel.md`, and
#   `main`'s copy now AGREES with the paragraph above: PR #1866
#   (`fix/flap-two-mechanisms-round3`) MERGED as `b7a30bc3`, so that doc carries
#   the two-mechanism correction and its old single-cause line is marked
#   "SUPERSEDED, kept for the record". Verified against `origin/main`
#   2026-09-24. So the doc may be read as the justification for this change --
#   it is the longer version of what is restated above.
#
#   ⚠ THE MEASUREMENT IS STILL RESTATED HERE RATHER THAN CITED, DELIBERATELY.
#   The restatement is what makes this file's justification legible without a
#   second read, and it is what survived two WRONG citations of this very
#   paragraph: one naming #1861 (merged as `5834b4c5`, which did not carry the
#   correction), then one describing #1866's review status as pending -- a
#   claim that expired within the hour of being written. A citation that
#   asserts a PR's STATE has a shelf life; one that names a merge commit does
#   not. Do not reintroduce a state claim here; a test pins this.
#
# SAFETY. Nothing is written until every precondition passes AND both
# dry-builds have run: the file is patched in a temp sibling, parse-checked,
# evaluated with `nixos-rebuild dry-build -I nixos-config=<temp>` (which is a
# real eval gate -- `nix-instantiate --parse` is only a syntax gate and will
# happily accept keys written at the wrong OPTION SCOPE), backed up, and only
# then moved into place. From that moment a trap restores the backup on ANY
# failure and says what the run had reached when it failed.
#
# ⚠ BE PRECISE ABOUT WHAT THAT LAST CLAIM IS WORTH. The trap carries three
# post-write branches, and only ONE of them -- "STARTED, OUTCOME UNKNOWN" -- is
# reachable from a realistic failure (a `nixos-rebuild switch` that does not
# return 0). "ALREADY SWITCHED" needs PERSISTED=1 with OK=0, and those are set
# on adjacent lines with nothing between them; "NEVER STARTED" needs a failure
# between the `mv` and the switch, where the only commands are `echo`s. They are
# kept as DEFENCE -- if a future edit puts work between those statements the
# branch starts telling the truth instead of having to be invented then -- but
# an earlier version of this header said the trap "reports which of three states
# the run actually reached", which claimed more coverage than is delivered.
#
# Overrides (all optional; the first three are test seams):
#   MOSH_FW_CFG=/etc/nixos/configuration.nix
#   MOSH_FW_IFACE=nebula.mesh
#   MOSH_FW_SWITCH=0                 1 = also run `nixos-rebuild switch`
set -euo pipefail

CFG="${MOSH_FW_CFG:-/etc/nixos/configuration.nix}"
IFACE="${MOSH_FW_IFACE:-nebula.mesh}"
DO_SWITCH="${MOSH_FW_SWITCH:-0}"
PORT_FROM=60000
PORT_TO=61000

die() { echo "ABORT: $*" >&2; exit 1; }

# Trap state. Each flag is set immediately AFTER the step it names succeeds, so
# the message the trap prints is what was actually REACHED, never what was
# intended. The three post-write states are mutually exclusive by construction:
# `set -e` plus `PERSISTED=1` on the line after a successful switch means
# SWITCH_ATTEMPTED-without-PERSISTED can only be a switch that did not return 0.
PATCHED=0            # $CFG has been replaced by the patched file
SWITCH_ATTEMPTED=0   # `nixos-rebuild switch` was started -- the profile MAY have moved
PERSISTED=0          # ... and returned 0: profile + bootloader carry the change
OK=0

# 🔴 NOT `/tmp/<fixed-name>.$$`. /tmp is 1777, `>` follows symlinks, and this
# runs as root: a predictable path there is an arbitrary-file-overwrite
# primitive for any local user who pre-creates the symlink.
SCRATCH="$(mktemp -d -t mosh-nebula-fw.XXXXXXXX)" || die "mktemp -d failed"
TMP=""   # set in the patch section; a SIBLING of $CFG so the `mv` is atomic
cleanup_scratch() { rm -rf "$SCRATCH"; [ -n "$TMP" ] && rm -f "$TMP"; return 0; }
trap cleanup_scratch EXIT

# ------------------------------------------------------------- what gets inserted
# One definition, used by the patch pass, by the idempotence check and by the
# manual-fix text the trap prints. Three copies of this would disagree.
RANGE_KEY="interfaces.\"${IFACE}\".allowedUDPPortRanges"
MOSH_KEY="programs.mosh"

# `#` comments stripped before any structural grep.
#
# 🔴 F3: THE IDEMPOTENCE MARKER MUST BE THE STRUCTURE, NOT A NAME. The previous
# version grepped for its own filename anywhere in the file. $CFG ALREADY
# carries `# See nix/system/apply-tmp-churn-retention.sh.` and `# Added by
# nix/system/apply-tailscale.sh.` -- the colliding convention is live in the
# very file being edited, and this script's own comment lines use it too. A
# comment mentioning this script would have made the run report "already
# configured" over a firewall that was never opened. So the check is for the
# attribute paths themselves, in COMMENT-STRIPPED text, which no comment can
# spell.
# (The stripper is deliberately crude -- it cannot see a `#` inside a Nix
# string. Neither key can contain one, and erring toward "not yet applied"
# fails safe: a duplicate attribute is rejected by `nix-instantiate --parse`.)
code_only() { sed 's/#.*$//' "$CFG"; }

# 🔴 R3-1: "THE ATTRIBUTE PATH IS PRESENT" IS NOT "THE CHANGE IS APPLIED".
# `programs.mosh.openFirewall` DEFAULTS TO TRUE, and writing it `false` is this
# script's entire reason to write the module explicitly (see WHAT IT INSERTS
# above): left on, the module adds 60000-61000 to
# `networking.firewall.allowedUDPPortRanges` -- 1001 UDP ports on EVERY
# interface, the WAN-facing one included.
#
# So the idempotence check needed a THIRD fact, not a second. Before the fix a
# config carrying `programs.mosh.enable = true;` plus the range -- with no
# `openFirewall` line anywhere, which is the single most likely hand-written
# spelling -- reported `ALREADY APPLIED`, exit 0, and under MOSH_FW_SWITCH=1
# went down the converge path and SWITCHED. Measured at ee6b0ce9.
#
# 🔴 AND IT MUST BE SCOPED TO MOSH, NOT A WHOLE-FILE GREP. `openFirewall` is a
# common NixOS option name; some other service's `openFirewall = false;` says
# nothing about mosh's, and accepting it would re-open this hole through a
# different door -- wider on one axis while narrowing another.
#
# Answers `false` / `true` / `absent` for `programs.mosh`'s own openFirewall,
# reading COMMENT-STRIPPED text. `true` wins over `false` when both are seen:
# an ambiguous config is refused, never accepted.
#
# ⚠ The brace tracker is deliberately crude, in the same way `code_only` is: it
# counts `{`/`}` without understanding Nix strings or antiquotation. Neither
# key can contain a brace, and every direction it can be wrong in errs toward
# REFUSING, which is the safe verdict for a gate whose failure mode is opening
# the WAN.
mosh_openfirewall_state() {
  code_only | awk '
    function brace_delta(s,   i, c, d) {
      d = 0
      for (i = 1; i <= length(s); i++) {
        c = substr(s, i, 1)
        if (c == "{") d++
        else if (c == "}") d--
      }
      return d
    }
    {
      line = $0

      # --- the dotted spelling: programs.mosh.openFirewall = <bool>;
      if (line ~ /(^|[^.[:alnum:]_])programs\.mosh\.openFirewall[[:space:]]*=[[:space:]]*true[[:space:]]*;/)
        saw_true = 1
      else if (line ~ /(^|[^.[:alnum:]_])programs\.mosh\.openFirewall[[:space:]]*=[[:space:]]*false[[:space:]]*;/)
        saw_false = 1

      # --- the braced spelling: programs.mosh = { ... openFirewall = X; ... }
      scan = ""
      if (!inblock && match(line, /(^|[^.[:alnum:]_])programs\.mosh[[:space:]]*=[[:space:]]*\{/)) {
        inblock = 1; depth = 0
        scan = substr(line, RSTART)
      } else if (inblock) {
        scan = line
      }
      if (inblock) {
        if (scan ~ /openFirewall[[:space:]]*=[[:space:]]*true[[:space:]]*;/) saw_true = 1
        else if (scan ~ /openFirewall[[:space:]]*=[[:space:]]*false[[:space:]]*;/) saw_false = 1
        depth += brace_delta(scan)
        if (depth <= 0) inblock = 0
      }
    }
    END {
      if (saw_true) print "true"
      else if (saw_false) print "false"
      else print "absent"
    }'
}

# ------------------------------------------------------------------- helpers
# Defined up here rather than beside their first use because the ALREADY
# APPLIED + MOSH_FW_SWITCH=1 converge path (below) needs all three before the
# patch section exists, and bash resolves a function at CALL time -- a
# definition further down the file would simply not be there yet.

dry_build() {   # $1 = config to evaluate, $2 = label
  local rc=0
  nixos-rebuild dry-build -I "nixos-config=$1" >"$SCRATCH/$2.out" 2>&1 || rc=$?
  return $rc
}

# `nix` prints these to stderr, which dry_build folds into the same file:
#   these 444 derivations will be built:      / this derivation will be built:
#   these 1420 paths will be fetched (...)    / this path will be fetched (...)
# Absent means zero. `tail -1` because a run can print the pair more than once.
count_of() {    # $1 = label, $2 = built|fetched
  local f="$SCRATCH/$1.out" n
  if [ "$2" = "built" ]; then
    n="$(sed -n 's/^ *these \([0-9]*\) derivations will be built.*/\1/p' "$f" | tail -1)"
    [ -n "$n" ] || { grep -q '^ *this derivation will be built' "$f" && n=1 || n=0; }
  else
    n="$(sed -n 's/^ *these \([0-9]*\) paths will be fetched.*/\1/p' "$f" | tail -1)"
    [ -n "$n" ] || { grep -q '^ *this path will be fetched' "$f" && n=1 || n=0; }
  fi
  printf '%s' "$n"
}

# 🔴 F7-adjacent: `wc -l` COUNTS NEWLINES, NOT LINES. A config whose last line
# has no terminating newline is one short by that count, while awk's output
# always ends with one -- so the +17 check saw +18 and the run died claiming
# "the patch added 18 lines", blaming the patch for the input's shape. awk's NR
# counts the final unterminated record, so both sides are measured the same way.
# (Both hosts' real files do end with a newline, so this was latent.)
lines_of() { awk 'END { print NR + 0 }' "$1"; }

verify_text() {
  # No apostrophe in the fallback: bash treats a `'` inside `${x:-…}` as an
  # opening quote even within double quotes, and the whole file then fails
  # `bash -n` with "unexpected EOF while looking for matching `''".
  local target="${MESH_IP:-<THIS HOST-S $IFACE ADDRESS: nebula was down, look it up>}"
  echo "VERIFY FROM THE OTHER HOST (this proves the path, not just the config):"
  echo
  echo "    mosh --ssh=\"ssh -o ConnectTimeout=10\" zach@${target} -- true && echo MOSH-OK"
  echo
  echo "🔴 A successful \`mosh\` is the only verification. \`nixos-rebuild switch\`"
  echo "reporting success is a claim about the REBUILD, not about reachability."
  echo
  echo "If it hangs at \"Connecting...\", check in this order:"
  echo "    ss -lunp 'sport >= :$PORT_FROM and sport <= :$PORT_TO'  # here, while connecting"
  echo "    command -v mosh-server                                  # must be on the ssh session's PATH"
}

# ---------------------------------------------------------------- preflight (no writes)
echo "== preflight =="

# 🔴 F4: THIS IS THE ROOT CHECK. The previous version used `[ -r "$CFG" ]`,
# which passes for an ordinary user -- /etc/nixos/configuration.nix is 0644 on
# both hosts (measured), so the "run me under sudo" guard never fired and the
# run died later, half-way, on the first write.
[ "$(id -u)" = "0" ] || die "must run as root: sudo bash ${BASH_SOURCE[0]}"

# Everything this script execs. A missing tool must abort BEFORE the first
# write, not somewhere in the middle.
for t in awk sed grep cp mv rm mktemp date head cut wc diff \
         id ip nixos-rebuild nix-instantiate; do
  command -v "$t" >/dev/null 2>&1 || die "\`$t\` is not on PATH.
  sudo inherits the CALLER's PATH here (there is no secure_path). Run this from
  a shell that has it:
    sudo env \"PATH=\$PATH\" bash ${BASH_SOURCE[0]}"
done

# 🔴 SYMLINKS ARE REFUSED, NOT FOLLOWED. `[ -f ]` is true for a symlink to a
# regular file and the patch ends in `mv "$TMP" "$CFG"`, which REPLACES the
# link with a regular file and silently orphans the real one. Resolving it with
# `readlink -f` and writing through it is the other option, and is rejected
# because this runs as ROOT. The comparison is against `readlink -f`, not
# `[ -L ]`, so a symlinked DIRECTORY component is caught too.
cfg_real="$(readlink -f -- "$CFG" 2>/dev/null || true)"
[ -n "$cfg_real" ] || die "$CFG does not resolve to any path (dangling symlink?)"
if [ "$cfg_real" != "$CFG" ]; then
  die "$CFG is a symlink (or sits under one); it resolves to
    $cfg_real
  This script will not write through a symlink as root. Re-run against the real
  file:
    sudo MOSH_FW_CFG=$cfg_real bash ${BASH_SOURCE[0]}"
fi
[ -f "$CFG" ] && [ -w "$CFG" ] || die "$CFG is not a writable regular file"
echo "  config    : $CFG"

# F5: the address to VERIFY against is this host's, derived here. The previous
# version printed a quoted heredoc with `zach@10.42.0.30` baked in, so a run on
# the laptop told the operator to mosh into the WORKBENCH -- testing the wrong
# host's firewall rule and yielding a false green.
MESH_IP="$(ip -4 -o addr show "$IFACE" 2>/dev/null | awk '{print $4}' | cut -d/ -f1 | head -1 || true)"
if [ -n "$MESH_IP" ]; then
  echo "  mesh addr : $IFACE = $MESH_IP  (this is the address to mosh INTO)"
else
  echo "  mesh addr : 🔴 $IFACE has NO IPv4 address on this host."
  echo "              The firewall rule is still written correctly -- NixOS does not"
  echo "              require the interface to exist -- but nothing will reach it"
  echo "              until nebula is up, and the verify command below cannot name"
  echo "              an address. Check \`systemctl status nebula@mesh\`."
fi

# ------------------------------------------------------------------ idempotence
have_range=0; have_mosh=0
code_only | grep -qF "$RANGE_KEY"  && have_range=1
# 🔴 THE RIGHT BOUNDARY MUST ALLOW `.`, OR THE MOST IDIOMATIC SPELLING NEVER
# MATCHES. This read `([^.[:alnum:]_]|$)`, which excludes a following dot -- so
# `programs.mosh.enable = true;`, the dotted form this script's OWN die message
# tells the operator to consider, did not count as present. Measured: a fully
# and correctly hand-applied dotted config was refused as "HALF configured",
# with remediation advice whose result is `already defined`. Only alnum and `_`
# may follow, so `programs.moshfoo` still does not match while
# `programs.mosh.enable` and `programs.mosh = {` both do. The LEFT boundary
# still excludes `.` on purpose: `services.programs.mosh` is a different key.
code_only | grep -qE "(^|[^.[:alnum:]_])programs\.mosh([^[:alnum:]_]|$)" && have_mosh=1

if [ "$have_range" = "1" ] && [ "$have_mosh" = "1" ]; then
  # 🔴 R3-1. Both paths present is NOT enough -- see mosh_openfirewall_state
  # above. Refuse rather than "converge" onto a config that opens the WAN.
  openfw="$(mosh_openfirewall_state)"
  if [ "$openfw" != "false" ]; then
    if [ "$openfw" = "true" ]; then
      why="explicitly set to \`true\`"
    else
      why="ABSENT, and the option DEFAULTS TO TRUE"
    fi
    die "$CFG carries \`$MOSH_KEY\` and \`$RANGE_KEY\`, but
  \`$MOSH_KEY.openFirewall\` is $why.
  That is NOT this change applied -- it is the WAN exposure this script exists
  to avoid. With openFirewall on, the module adds $PORT_FROM-$PORT_TO to
  \`networking.firewall.allowedUDPPortRanges\`: $(( PORT_TO - PORT_FROM + 1 )) UDP ports on EVERY
  interface, the WAN-facing one included, which the interface-scoped range in
  this file does NOT undo. Nothing was written and nothing was switched.
  Fix it by hand -- write the flag explicitly, in whichever spelling the file
  already uses:
    $MOSH_KEY = { enable = true; openFirewall = false; };
  or, beside a dotted \`$MOSH_KEY.enable = true;\`:
    $MOSH_KEY.openFirewall = false;
  then re-run this script."
  fi
  echo "  state     : ALREADY APPLIED -- both \`$MOSH_KEY\` and"
  echo "              \`$RANGE_KEY\` are already in $CFG,"
  echo "              and \`$MOSH_KEY.openFirewall\` is set false."
  if [ "$DO_SWITCH" != "1" ]; then
    echo
    echo "Nothing to write. If you have not switched since the edit landed, run:"
    echo "    sudo nixos-rebuild switch"
    exit 0
  fi

  # 🔴 THIS BRANCH USED TO FALL THROUGH INTO THE PATCH PASS, AND THAT KILLED
  # THE SECOND HALF OF THE ADVERTISED TWO-STEP WORKFLOW. It printed "converging
  # with a switch anyway" and then ran the anchor check and the awk insert
  # again, producing a SECOND `programs.mosh` block and a SECOND
  # `allowedUDPPortRanges` -- measured: two of each, two backups. The real
  # parser then rejects the result with `attribute 'programs.mosh.enable'
  # already defined`, so it failed closed, but "edit now, converge later" could
  # never succeed. Nothing needs writing here, so the converge path SKIPS the
  # whole patch section: no anchor check, no awk, no temp file, no backup.
  echo "              MOSH_FW_SWITCH=1: CONVERGING. Nothing is written -- the"
  echo "              file already carries the change; this run only switches."
  echo

  echo "== dry-build (nothing is activated) =="
  if ! dry_build "$CFG" before; then
    sed 's/^/    | /' "$SCRATCH/before.out" >&2 || true
    die "your config does not dry-build (see above). $CFG is UNTOUCHED and
  nothing was switched. Fix that first -- switching on top of it would be
  strictly worse."
  fi
  echo "  current   : $(count_of before built) to build, $(count_of before fetched) to fetch"
  echo "  🔴 There is no delta column: the change is ALREADY in the file, so that"
  echo "     count is the whole switch, this change included. /etc/nixos has no"
  echo "     flake, so most of it is root-channel drift, not mosh."
  echo

  echo "== nixos-rebuild switch =="
  # No rollback trap is armed on this path and none is wanted: this run never
  # touched $CFG, so there is nothing of ours to restore -- and the PATCHED /
  # SWITCH_ATTEMPTED / PERSISTED flags are deliberately NOT set here, because
  # nothing reads them yet (`finish` is installed further down) and setting them
  # would read as "the trap will report this" when it will not. The failure
  # branch below does the reporting instead. What CAN have moved is the system,
  # and that is a separate claim -- say it rather than implying the failure was
  # harmless.
  if ! nixos-rebuild switch; then
    echo >&2
    echo "🔴 \`nixos-rebuild switch\` FAILED." >&2
    echo "   $CFG is UNTOUCHED -- this run wrote nothing, so there is no backup" >&2
    echo "   and nothing to roll back. The SYSTEM is the open question: switch" >&2
    echo "   registers the profile generation and installs the bootloader BEFORE" >&2
    echo "   it activates, so a failure part-way can still have moved both." >&2
    echo "   Check:  readlink /nix/var/nix/profiles/system" >&2
    echo "           nixos-rebuild list-generations | tail -5" >&2
    exit 1
  fi
  echo
  echo "=== SWITCHED (config was already applied; nothing was edited) ==="
  echo
  verify_text
  exit 0
elif [ "$have_range" != "$have_mosh" ]; then
  # Refuse rather than guess: half of the change is present, and which half it
  # is changes what the fix should be.
  present="$MOSH_KEY"; missing="$RANGE_KEY"
  [ "$have_range" = "1" ] && { present="$RANGE_KEY"; missing="$MOSH_KEY"; }
  die "$CFG is HALF configured: \`$present\` is present but \`$missing\` is not.
  This script only knows how to insert both at once, and will not merge into a
  partial edit. Add the missing half by hand:
    $MOSH_KEY = { enable = true; openFirewall = false; };
    networking.firewall.$RANGE_KEY = [ { from = $PORT_FROM; to = $PORT_TO; } ];"
fi

# -------------------------------------------------------------------- the anchor
# 🔴 F2: THE ANCHOR MUST BE A MULTI-LINE BLOCK OPENER AT TOP LEVEL, AND THAT IS
# CHECKED STRUCTURALLY, NOT BY A LOOSE REGEX.
#
# The previous version matched `^[[:space:]]*networking\.firewall = \{` and
# printed the inserted keys after the WHOLE matched line. Measured walkthrough:
# if the block is written on ONE line --
#
#     networking.firewall = { allowedUDPPorts = [ 51820 ]; };
#
# -- that regex still matches exactly once, so the guard passes, and the keys
# land at MODULE TOP LEVEL instead of inside the firewall block. The result
# PARSES (rc 0 from `nix-instantiate --parse`) and then fails NixOS evaluation
# with "The option `interfaces' does not exist". Neither host is written that
# way today, so it was latent, not live.
#
# Two independent closures, because a latent shape is exactly the one nobody
# re-checks: this refusal, and the candidate dry-build further down, which is a
# real eval and catches an option-scope error whatever produced it.
#
# The accepted form is the line, comment- and trailing-space-stripped, being
# exactly `  networking.firewall = {` -- two spaces of indent. The indent is
# load-bearing and not cosmetic: `programs.mosh` is inserted at the SAME indent
# immediately above, and it is a TOP-LEVEL attribute. A firewall block nested
# inside something else would put it in the wrong scope.
anchor_lines="$(awk '
  { line = $0
    sub(/#.*$/, "", line)
    sub(/[[:space:]]+$/, "", line)
    if (line == "  networking.firewall = {") print NR
  }' "$CFG")"
anchor_count="$(printf '%s' "$anchor_lines" | grep -c . || true)"

if [ "$anchor_count" != "1" ]; then
  echo "  anchor    : $anchor_count match(es) for a top-level, multi-line \`networking.firewall = {\`" >&2
  echo "  lines that mention networking.firewall (for orientation):" >&2
  grep -nE 'networking\.firewall' "$CFG" | sed 's/^/    /' >&2 || true
  die "cannot locate exactly one top-level \`  networking.firewall = {\` block opener in $CFG.
  This script inserts \`$MOSH_KEY\` at that line's indentation and the UDP range
  INSIDE the block, so it needs the two-space, brace-opens-a-multi-line-block
  form. A single-line \`networking.firewall = { ... };\`, a differently indented
  one, or the dotted \`networking.firewall.allowedUDPPorts = [ ... ];\` form are
  all REFUSED rather than guessed at -- inserting after any of them puts the
  keys at the wrong option scope, which still parses and then fails evaluation.
  Add both by hand instead:
    $MOSH_KEY = { enable = true; openFirewall = false; };
    networking.firewall.$RANGE_KEY = [ { from = $PORT_FROM; to = $PORT_TO; } ];"
fi
ANCHOR_LINE="$anchor_lines"
echo "  anchor    : line $ANCHOR_LINE  (top-level \`networking.firewall = {\`)"
echo

# ------------------------------------------------------------------------ patch (temp)
echo "== patch =="
CAND="$SCRATCH/candidate.nix"
awk -v n="$ANCHOR_LINE" -v iface="$IFACE" -v pf="$PORT_FROM" -v pt="$PORT_TO" '
NR == n {
  print "  # Added by nix/system/apply-mosh-nebula-firewall.sh."
  print "  # The NixOS module gives the utempter setgid wrapper (without it mosh"
  print "  # cannot write utmp, so `who` misses the session) and mosh-server on the"
  print "  # system PATH. openFirewall is OFF on purpose: it defaults to TRUE and"
  print "  # would open 60000-61000 on EVERY interface, WAN included. The"
  print "  # interface-scoped range below is the narrow replacement."
  print "  programs.mosh = {"
  print "    enable = true;"
  print "    openFirewall = false;"
  print "  };"
  print ""
  print $0
  print "    # mosh-server binds one ephemeral UDP port in this range on the host"
  print "    # you connect TO. Scoped to the mesh interface so it is not exposed"
  print "    # on the WAN; inside the mesh, nebula'\''s own firewall gates it."
  print "    interfaces.\"" iface "\".allowedUDPPortRanges = ["
  print "      { from = " pf "; to = " pt "; }"
  print "    ];"
  inserted++
  next
}
{ print }
END { if (inserted + 0 != 1) exit 3 }
' "$CFG" > "$CAND" || die "the patch pass did not make exactly one insertion"

added=$(( $(lines_of "$CAND") - $(lines_of "$CFG") ))
[ "$added" = "17" ] || die "expected the patch to add exactly 17 lines, it added $added"
echo "  candidate : +17 lines"

# The temp lives NEXT TO $CFG so the final `mv` is atomic (it is only atomic
# within one filesystem, and /tmp is routinely a different one).
#
# 🔴 F7: MODE AND OWNER ARE PRESERVED. The previous version wrote `awk … >
# "$CFG.new"` and moved that into place, so the result took the SHELL'S UMASK
# and the original's mode was silently dropped -- a 0600 config would come back
# 0644. Here `mktemp` gives an unpredictable 0600 name, `cp -p` gives it $CFG's
# mode and owner, and the `>` redirect that follows TRUNCATES rather than
# re-creating, so both survive into the moved file.
TMP="$(mktemp "${CFG}.new.XXXXXXXX")" || die "cannot create a temp file next to $CFG"
cp -p "$CFG" "$TMP"
cat "$CAND" > "$TMP"

# 🔴 F8: THE PARSE ERROR IS SHOWN, NOT SWALLOWED. `>/dev/null 2>&1` swallowed
# the parser's own diagnosis, so every rejection printed the same opaque "the
# edited file does not parse as Nix". Capturing stderr and echoing it is what
# makes the message name the actual syntax error.
#
# ⚠ THE rc-127 CASE IS NOT CLOSED HERE, AND AN EARLIER VERSION OF THIS COMMENT
# SAID IT WAS. A MISSING `nix-instantiate` cannot reach this line at all: the
# preflight `command -v` loop near the top of the file lists it and aborts with
# the "sudo inherits the CALLER's PATH" message long before any patching. That
# loop is the guard delivering the claim; this block only ever sees a real
# parser verdict.
#
# ⚠ AND IT IS ONLY A SYNTAX GATE. It rejects a duplicated attribute (that much
# was checked, and the duplicate-attribute worry is refuted), but it knows
# nothing about OPTION SCOPE: keys written outside the block they belong to
# parse fine. The dry-build below is the gate that catches that.
if ! nix-instantiate --parse "$TMP" >/dev/null 2>"$SCRATCH/parse.err"; then
  sed 's/^/    | /' "$SCRATCH/parse.err" >&2 || true
  die "the candidate does not parse as Nix (see the parser's own output above).
  $CFG is UNTOUCHED. A missing \`nix-instantiate\` cannot produce this message --
  the preflight tool check aborts on that before anything is patched -- so the
  output above is a real parser verdict on the candidate file."
fi
echo "  nix parse : OK"

echo "  diff:"
diff -u "$CFG" "$TMP" | sed 's/^/    /' || true
echo

# ------------------------------------------------------- dry-build BOTH configs
# 🔴 THE BLOCKER THIS SCRIPT WAS REWRITTEN FOR. Both runs happen BEFORE $CFG is
# touched -- `-I nixos-config=<path>` evaluates an arbitrary file, so the
# candidate is evaluated without being installed. That makes this both the
# eval gate (option scope, F2) and the honest answer to "what will a switch
# actually do to this machine".
echo "== dry-build (nothing is activated) =="

if ! dry_build "$CFG" before; then
  sed 's/^/    | /' "$SCRATCH/before.out" >&2 || true
  die "your CURRENT config does not even dry-build (see above). That is a
  pre-existing fault, not this change -- $CFG is UNTOUCHED. Fix it first;
  switching on top of it would be strictly worse."
fi
before_built="$(count_of before built)"; before_fetch="$(count_of before fetched)"
echo "  current   : $before_built to build, $before_fetch to fetch"

if ! dry_build "$TMP" after; then
  sed 's/^/    | /' "$SCRATCH/after.out" >&2 || true
  die "the CANDIDATE config does not evaluate (see above) while the current one
  does -- so this is THIS change, and $CFG is UNTOUCHED. An error naming an
  option that 'does not exist' means the inserted keys landed at the wrong
  scope; add them by hand instead."
fi
after_built="$(count_of after built)"; after_fetch="$(count_of after fetched)"
echo "  candidate : $after_built to build, $after_fetch to fetch"
echo "  delta     : $(( after_built - before_built )) to build, $(( after_fetch - before_fetch )) to fetch  <- THIS change"
echo
echo "  🔴 The 'current' column is what a switch would do to this host EVEN WITH"
echo "     NO EDIT AT ALL: /etc/nixos has no flake, so it follows the root"
echo "     channel, which has moved since this system was built. Those are a"
echo "     FULL SYSTEM UPDATE. The 'delta' line is this change."
echo

# ------------------------------------------------------------------- land the edit
BAK="${CFG}.bak-mosh-fw-$(date +%Y%m%d-%H%M%S)-$$"

finish() {
  local rc=$?
  cleanup_scratch
  if [ "$OK" = "1" ]; then return; fi
  if [ "$PATCHED" != "1" ]; then exit $rc; fi

  echo >&2
  if [ -f "$BAK" ] && cp -p "$BAK" "$CFG"; then
    echo "ROLLED BACK: $CFG restored from $BAK" >&2
  else
    # 🔴 NEVER SILENT. A missing or unreadable backup must not skip the restore
    # and print nothing, leaving the config patched while the header promises a
    # restore on any failure.
    echo "🔴 ROLLBACK FAILED — your config is still patched at $CFG" >&2
    if [ -f "$BAK" ]; then
      echo "   The backup exists at $BAK but restoring it failed (permissions? disk full?)." >&2
      echo "   Fix by hand:  sudo cp -p $BAK $CFG" >&2
    else
      echo "   The backup $BAK is GONE, so there is nothing to restore from." >&2
      echo "   Fix by hand: delete the inserted \`$MOSH_KEY\` block and the" >&2
      echo "   \`$RANGE_KEY\` list from $CFG." >&2
    fi
  fi

  # What is RUNNING / PERSISTED is a separate claim from what is in the FILE.
  # Say only what this run actually reached.
  if [ "$PERSISTED" = "1" ]; then
    echo "🔴 ALREADY SWITCHED. \`nixos-rebuild switch\` succeeded before this failure," >&2
    echo "   so the change is running AND registered in /nix/var/nix/profiles/system" >&2
    echo "   and the bootloader. Restoring the file above is NOT enough -- run" >&2
    echo "   \`sudo nixos-rebuild switch\` to return the running system and the profile." >&2
  elif [ "$SWITCH_ATTEMPTED" = "1" ]; then
    echo "🔴 STARTED, OUTCOME UNKNOWN. \`nixos-rebuild switch\` was started and did not" >&2
    echo "   report success. switch registers the profile generation and installs the" >&2
    echo "   bootloader BEFORE it activates, so a failure part-way can still have moved" >&2
    echo "   both. This script does NOT know which side of that line it failed on." >&2
    echo "   Check:  readlink /nix/var/nix/profiles/system" >&2
    echo "           nixos-rebuild list-generations | tail -5" >&2
    echo "   If a new generation is there, \`sudo nixos-rebuild switch\` on the restored" >&2
    echo "   config puts the profile and the boot entry back." >&2
  else
    echo "   NEVER STARTED. No rebuild was started, so nothing is running the change" >&2
    echo "   and no generation was registered. Only the file had been touched." >&2
  fi
  exit $rc
}
trap finish EXIT

[ -e "$BAK" ] && die "backup path $BAK already exists; refusing to overwrite it"
cp -p "$CFG" "$BAK"
echo "  backup    : $BAK"

mv "$TMP" "$CFG"
TMP=""            # moved, not leaked -- keep the cleanup honest
PATCHED=1
echo "  applied   : $CFG"
echo

if [ "$DO_SWITCH" != "1" ]; then
  OK=1
  echo "=== EDITED, NOT SWITCHED ==="
  echo "The change is in $CFG and NOTHING has been activated. That is the default"
  echo "and it is deliberate -- see the header: a switch here pulls the whole"
  echo "channel delta printed above ($before_built to build, $before_fetch to"
  echo "fetch BEFORE this change), which is not something to do to a host you"
  echo "cannot physically reach."
  echo
  echo "To complete it, ON THE LAN, with console access to this machine:"
  echo "    sudo nixos-rebuild switch"
  echo
  echo "To undo the edit instead:"
  echo "    sudo cp -p $BAK $CFG"
  echo
  verify_text
  exit 0
fi

echo "== nixos-rebuild switch =="
echo "  🔴 MOSH_FW_SWITCH=1: activating. This carries the whole channel delta"
echo "     above, not just this change."
SWITCH_ATTEMPTED=1
nixos-rebuild switch
PERSISTED=1
OK=1
echo
echo "=== SWITCHED ==="
echo "Backup of the previous config: $BAK"
echo "To revert:  sudo cp -p $BAK $CFG && sudo nixos-rebuild switch"
echo
verify_text
