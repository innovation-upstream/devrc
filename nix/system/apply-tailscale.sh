#!/usr/bin/env bash
# Add Tailscale to this host as an INDEPENDENT SECOND PATH, and switch to it.
#
#   sudo env "PATH=$PATH" bash nix/system/apply-tailscale.sh --dry-run   # see the cost
#   sudo env "PATH=$PATH" bash nix/system/apply-tailscale.sh             # do it
#
# Nebula is currently the ONLY remote path to the workbench. If its lighthouse, its
# relay, or the host firewall in front of it breaks while the operator is off-LAN, there
# is no second door. Tailscale is that second door, and it shares no component with
# nebula: different control plane, different relays, different code.
#
# This inserts a `services.tailscale` block into /etc/nixos/configuration.nix and runs
# `nixos-rebuild switch`. It does NOT log the node in -- that needs a browser and is
# printed as the next step. It cannot approve the subnet route or disable node-key
# expiry either; both live only in the admin console. Those are REPORTED, never faked.
#
# Overrides (all optional):
#   TS_SUBNET=192.168.50.0/24        the LAN the server role advertises
#   TS_CFG=/etc/nixos/configuration.nix
#   TS_EXPECT_MESH_IP_SERVER=10.42.0.30
#   TS_EXPECT_MESH_IP_CLIENT=10.42.0.100
#   TS_MAX_PENDING_BUILDS=10         see THE CLOSURE PREFLIGHT
#   TS_MAX_TOTAL_BUILDS=25
#   TS_MAX_CHANGED_PATHS=250
#
# Flags:
#   --dry-run                stop after the closure preflight; never writes the config
#   --allow-world-rebuild    proceed even when the preflight says the pending change is
#                            far larger than this delta, or the release string moves
#   --role server|client     override the host role (normally auto-detected)
#   --self-test              validate this script's own parsers, then exit
#
# WHICH HOST: `hostname` is NOT a discriminator on this fleet -- BOTH machines answer
# `nixos`. The guard is this host's nebula mesh address, which is unique by
# construction:
#   10.42.0.30  -> workbench -> SUBNET ROUTER (useRoutingFeatures = "server",
#                  IP forwarding on, advertises the LAN)
#   10.42.0.100 -> laptop    -> plain CLIENT  (useRoutingFeatures = "client")
# Anything else aborts before any write rather than guessing.
#
# ============================================================================
# 🔴 THE CLOSURE PREFLIGHT -- WHY THIS SCRIPT IS NOT FOUR LINES OF `sed`
# ============================================================================
# `nixos-rebuild switch` applies EVERYTHING PENDING in the configuration, not just the
# delta you added. MEASURED on this exact machine: a 4-line nebula relay edit triggered a
# 26.05 -> 26.11 release jump that took 2h16m and rebuilt the world, largely
# `wine-wow-11.0`. A script advertised as "add a few lines" attempted a full OS upgrade.
# That was recorded as an OPEN defect on devrc PR #1272 and never fixed; this is the fix.
#
# MEASURED AGAIN 2026-09-07, on an UNCHANGED config, before this script existed:
# `nixos-rebuild dry-build` reported **40 derivations to build and 24 paths to fetch
# (159.4 MiB download, 651.1 MiB unpacked)**, and the system it would produce was
# `26.11pre1066425.9387b3fcc0c2` against a running `26.11pre1066106.3ed67ec0a4d3`. So
# the hazard is not hypothetical or historical -- it is the state of this host TODAY,
# and none of those 40 derivations has anything to do with tailscale.
#
# THE DEFAULT THRESHOLDS ARE CALIBRATED FROM MEASUREMENT, not guessed. On the same host,
# the same day, the SAME dry-build run against a config carrying the server block was
# **47 derivations to build, 25 to fetch (177.0 MiB)**. So tailscale's own true cost is
# **+7 derivations and +1 fetched path** -- `tailscale-1.102.3` plus six regenerated
# unit/etc/activation derivations. That is the scale a "add tailscale" change should be,
# and it is why TS_MAX_PENDING_BUILDS defaults to 10 and TS_MAX_TOTAL_BUILDS to 25.
# On this host today the pending gate therefore REFUSES, which is the correct answer:
# 40 queued derivations are not what you asked for.
#
# So, BEFORE switching, this script measures the change in three independent ways and
# REFUSES by default when the answer is "you are about to rebuild the world":
#
#   1. BASELINE vs CANDIDATE, not just the total. `nixos-rebuild dry-build` is run TWICE
#      -- once against the CURRENT config and once against the patched one. The
#      difference is what tailscale actually costs; the baseline is what `switch` would
#      drag in whether or not you ran this script. Reporting only the total would blame
#      tailscale for a channel bump, and reporting only the delta would hide it.
#
#   2. THE NIXPKGS RELEASE STRING. Extracted with ONE implementation from the store-path
#      basename of both the running system and the candidate derivation, so the two can
#      never drift apart, and cross-checked against /run/current-system/nixos-version so
#      a broken extractor cannot silently agree with itself. A release change is refused
#      unconditionally -- it is an OS upgrade wearing a feature's clothes.
#
#   3. THE CLOSURE SET. After a build that the two gates above have already approved,
#      the running and candidate closures are compared as SETS
#      (`nix-store -qR | comm -3`), which cannot silently return a reassuring zero the
#      way a parsed diff can, plus `nix store diff-closures` for a human-readable
#      package-level report.
#
# 🔴 `readlink -f` IS USED ON BOTH SIDES, ALWAYS. `/nix/var/nix/profiles/system` is a
# symlink TO ANOTHER SYMLINK (measured: it reads `system-389-link`, a bare NAME), while
# `/run/current-system` points straight at the store. A single-level `readlink` returns
# a link name on one side and a store path on the other, so they can NEVER compare equal
# and the mismatch is reported forever. A previous session shipped exactly that bug.
# `_store_path` below resolves fully and REFUSES anything that is not under /nix/store,
# so a silent empty string cannot masquerade as agreement.
#
# SAFETY. Nothing is written to the config until every precondition AND the closure
# preflight pass -- the candidate is built from a temp file via `--include
# nixos-config=`, so a refusal leaves /etc/nixos untouched and there is nothing to roll
# back. Once the file IS moved into place a trap restores the backup on ANY failure.
# Re-running once tailscale is configured is a no-op that exits 0.
set -euo pipefail

SUBNET="${TS_SUBNET:-192.168.50.0/24}"
CFG="${TS_CFG:-/etc/nixos/configuration.nix}"
MESH_SERVER="${TS_EXPECT_MESH_IP_SERVER:-10.42.0.30}"
MESH_CLIENT="${TS_EXPECT_MESH_IP_CLIENT:-10.42.0.100}"
MAX_PENDING_BUILDS="${TS_MAX_PENDING_BUILDS:-10}"
MAX_TOTAL_BUILDS="${TS_MAX_TOTAL_BUILDS:-25}"
MAX_CHANGED_PATHS="${TS_MAX_CHANGED_PATHS:-250}"

DRYRUN=0
ALLOW_WORLD=0
ROLE="${TS_ROLE:-}"
SELFTEST=0

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CHECK="${HERE}/check-tailscale.sh"

while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run)             DRYRUN=1; shift ;;
    --allow-world-rebuild) ALLOW_WORLD=1; shift ;;
    --role)                ROLE="${2:-}"; shift 2 ;;
    --role=*)              ROLE="${1#--role=}"; shift ;;
    --self-test)           SELFTEST=1; shift ;;
    -h|--help)             sed -n '2,95p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

case "${ROLE:-}" in
  ""|server|client) ;;
  *) echo "ABORT: role must be 'server' or 'client', got '$ROLE'" >&2; exit 1 ;;
esac

die() { echo "ABORT: $*" >&2; exit 1; }

# =====================================================================================
# parsers -- each one validated by --self-test before any verdict is read off it
# =====================================================================================

# `nixos-rebuild dry-build` writes its summary to STDERR, and the shape is
#   these 40 derivations will be built:      /  this derivation will be built:
#   these 24 paths will be fetched (159.4 MiB download, 651.1 MiB unpacked):
# Absent lines mean ZERO, which is why the self-test drives a zero case AND a non-zero
# one: a parser wired to nothing also returns zero, and zero is the reassuring answer.
_drybuild_counts() {   # $1 = file holding dry-build's stderr; prints "built|fetched|mib"
  python3 - "$1" <<'PY'
import re, sys
try:
    text = open(sys.argv[1], encoding="utf-8", errors="replace").read()
except Exception as e:
    sys.stderr.write("parse: %s\n" % e); sys.exit(2)

def count(kind):
    m = re.search(r"^(?:these ([0-9]+)|this) (?:derivations?|paths?) will be %s" % kind,
                  text, re.M)
    if not m:
        return 0
    return int(m.group(1)) if m.group(1) else 1

mib = 0.0
m = re.search(r"will be fetched \(([0-9.]+) ([KMG]i?B) download", text)
if m:
    v, unit = float(m.group(1)), m.group(2)
    mib = v * {"KiB": 1 / 1024.0, "MiB": 1.0, "GiB": 1024.0,
               "KB": 1 / 1024.0, "MB": 1.0, "GB": 1024.0}.get(unit, 1.0)
print("%d|%d|%.1f" % (count("built"), count("fetched"), mib))
PY
}

# ONE extractor, fed from BOTH the running system's store path and the candidate
# derivation's, so the two sides can never drift apart -- the same discipline that
# check-nebula-relays.sh applies to its `-config` argument. Input is a store-path
# basename; output is the nixos version string.
# 🔴 `.drv` is stripped in its OWN pass. A trailing `\(\.drv\)\?` in the extraction
# regex does NOT work: BRE `[^-]*` is greedy and swallows `.drv` itself, so the optional
# group matches empty and the version comes back as `26.11pre…c0c2.drv`. Measured -- the
# self-test caught it on the first run.
_version_from_store_basename() {   # stdin -> version, or nothing
  sed 's/\.drv$//' | sed -n 's/^[a-z0-9]\{32\}-nixos-system-.*-\([0-9][^-]*\)$/\1/p'
}

# The RELEASE is the leading major.minor. `26.11pre1066425.9387b3fcc0c2` -> `26.11`.
# A change here is an OS upgrade, not a feature, however small the diff looks.
_release_of() { printf '%s' "${1%%pre*}" | cut -d. -f1,2; }

# Resolve a symlink CHAIN to a store path, and refuse anything else.
# 🔴 `readlink -f`, never plain `readlink`: /nix/var/nix/profiles/system is a symlink to
# a symlink and single-level readlink returns the bare name `system-NNN-link`, which can
# never equal a store path. Returning "" on failure rather than a partial answer means a
# comparison can never silently succeed against nothing.
_store_path() {
  local p
  p=$(readlink -f "$1" 2>/dev/null || true)
  case "$p" in
    /nix/store/*) printf '%s' "$p"; return 0 ;;
    *) return 1 ;;
  esac
}

_self_test() {
  local dir rc=0 got
  dir=$(mktemp -d)
  trap 'rm -rf "$dir"' RETURN

  # --- _drybuild_counts, both directions ------------------------------------------
  # 🔴 THE POSITIVE CONTROL. Every gate below reads a COUNT, and a zero is
  # indistinguishable from a parser wired to nothing. The plural fixture is the REAL
  # output measured on this host on 2026-09-07, verbatim.
  cat >"$dir/plural.txt" <<'T'
building the system configuration...
evaluation warning: something harmless
these 40 derivations will be built:
  /nix/store/aaaa-nixos-manual-html.drv
these 24 paths will be fetched (159.4 MiB download, 651.1 MiB unpacked):
  /nix/store/bbbb-mesa-26.2.2
T
  got=$(_drybuild_counts "$dir/plural.txt")
  if [ "$got" = "40|24|159.4" ]; then
    echo "  [self-test] dry-build counts, plural (the measured shape) -> ok"
  else
    echo "  [self-test] dry-build counts plural FAILED: got '$got', want '40|24|159.4'"; rc=1
  fi

  # SINGULAR. Nix drops the count word entirely for 1, so a regex that requires digits
  # reads "this derivation will be built" as ZERO -- the reassuring direction.
  cat >"$dir/singular.txt" <<'T'
this derivation will be built:
  /nix/store/cccc-thing.drv
this path will be fetched (0.1 MiB download, 0.3 MiB unpacked):
  /nix/store/dddd-other
T
  got=$(_drybuild_counts "$dir/singular.txt")
  if [ "$got" = "1|1|0.1" ]; then
    echo "  [self-test] dry-build counts, singular ('this ... will be built') -> ok"
  else
    echo "  [self-test] dry-build counts singular FAILED: got '$got', want '1|1|0.1'"; rc=1
  fi

  # THE ZERO CASE -- a config with nothing pending prints neither line.
  printf 'building the system configuration...\n' >"$dir/zero.txt"
  got=$(_drybuild_counts "$dir/zero.txt")
  if [ "$got" = "0|0|0.0" ]; then
    echo "  [self-test] dry-build counts, nothing pending -> 0|0|0.0 -> ok"
  else
    echo "  [self-test] dry-build counts zero FAILED: got '$got'"; rc=1
  fi

  # A GiB download must not be read as 2.5 MiB -- that is three orders of magnitude of
  # "this is fine" on the gate that exists to stop a world rebuild.
  cat >"$dir/gib.txt" <<'T'
these 3 paths will be fetched (2.5 GiB download, 9.0 GiB unpacked):
T
  got=$(_drybuild_counts "$dir/gib.txt")
  if [ "$got" = "0|3|2560.0" ]; then
    echo "  [self-test] a GiB download is scaled to MiB (2.5 GiB -> 2560.0) -> ok"
  else
    echo "  [self-test] GiB scaling FAILED: got '$got', want '0|3|2560.0'"; rc=1
  fi

  # --- _version_from_store_basename, one implementation, several real inputs --------
  got=$(printf '%s\n' "ihli9cw6p4b86rjhz75rymiwrgckzdfv-nixos-system-nixos-26.11pre1066106.3ed67ec0a4d3" | _version_from_store_basename)
  if [ "$got" = "26.11pre1066106.3ed67ec0a4d3" ]; then
    echo "  [self-test] version from a running-system basename -> ok"
  else
    echo "  [self-test] version from running-system basename FAILED: '$got'"; rc=1
  fi
  got=$(printf '%s\n' "6klnvgksn7b5qrm0ynz8q1nikamdkil2-nixos-system-nixos-26.11pre1066425.9387b3fcc0c2.drv" | _version_from_store_basename)
  if [ "$got" = "26.11pre1066425.9387b3fcc0c2" ]; then
    echo "  [self-test] version from a candidate .drv basename -> ok"
  else
    echo "  [self-test] version from .drv basename FAILED: '$got'"; rc=1
  fi
  # A HOSTNAME CONTAINING DASHES must not eat the version. `nixos` has none, so the
  # obvious greedy implementation passes every real input on this fleet and breaks the
  # day the host is renamed.
  got=$(printf '%s\n' "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-nixos-system-my-work-box-25.05.20250101.abcdef1" | _version_from_store_basename)
  if [ "$got" = "25.05.20250101.abcdef1" ]; then
    echo "  [self-test] version survives a dashed hostname (my-work-box) -> ok"
  else
    echo "  [self-test] dashed hostname FAILED: got '$got'"; rc=1
  fi
  # And a path that is NOT a system closure must yield NOTHING, not a guess. Returning
  # a wrong version on both sides would make them compare EQUAL and wave a release
  # jump straight through.
  got=$(printf '%s\n' "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-hello-2.12.1" | _version_from_store_basename)
  if [ -z "$got" ]; then
    echo "  [self-test] a non-system store path yields no version -> ok"
  else
    echo "  [self-test] non-system path FAILED: invented '$got'"; rc=1
  fi

  # --- _release_of ------------------------------------------------------------------
  for pair in "26.11pre1066106.3ed67ec0a4d3:26.11" "26.05.20260101.abc:26.05" "25.05:25.05"; do
    got=$(_release_of "${pair%%:*}")
    if [ "$got" = "${pair##*:}" ]; then
      echo "  [self-test] release of ${pair%%:*} -> ${pair##*:} -> ok"
    else
      echo "  [self-test] release of ${pair%%:*} FAILED: got '$got'"; rc=1
    fi
  done
  # 🔴 THE CASE THE GATE EXISTS FOR: two versions in the SAME release must compare
  # equal, and two in DIFFERENT releases must not. Asserted as a relationship, because
  # a _release_of that returned a constant would satisfy every case above.
  if [ "$(_release_of 26.11pre1)" = "$(_release_of 26.11pre2)" ]; then
    echo "  [self-test] same release, different revision -> equal -> ok"
  else
    echo "  [self-test] same-release comparison FAILED"; rc=1
  fi
  if [ "$(_release_of 26.05.1)" != "$(_release_of 26.11pre2)" ]; then
    echo "  [self-test] 26.05 vs 26.11 -> different -> ok  (this is the 2h16m jump)"
  else
    echo "  [self-test] release-jump comparison FAILED: 26.05 read as 26.11"; rc=1
  fi

  # --- _store_path ------------------------------------------------------------------
  # 🔴 THE TWO-LEVEL SYMLINK. Built as a real chain, because that is the shape that
  # made a previous session's comparison fail forever: plain `readlink` on the outer
  # link returns the NAME of the inner one.
  mkdir -p "$dir/store"
  ln -s "/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-thing" "$dir/inner-link"
  ln -s "inner-link" "$dir/outer-link"
  got=$(_store_path "$dir/outer-link" || true)
  if [ "$got" = "/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-thing" ]; then
    echo "  [self-test] _store_path resolves a symlink-to-a-symlink -> ok"
  else
    echo "  [self-test] _store_path two-level FAILED: got '$got'"; rc=1
  fi
  # The control that proves the case above is not vacuous: single-level readlink on the
  # SAME input returns a bare name, which is the bug.
  if [ "$(readlink "$dir/outer-link")" = "inner-link" ]; then
    echo "  [self-test] single-level readlink returns 'inner-link' -> the trap is real -> ok"
  else
    echo "  [self-test] the two-level fixture is not what this file claims"; rc=1
  fi
  # Non-store paths must REFUSE, so an empty result can never read as agreement.
  # 🔴 The target is a plain file in the temp dir, NOT something under /etc: on NixOS
  # most of /etc is itself a symlink INTO /nix/store, so `ln -s /etc/hostname` builds a
  # fixture that legitimately resolves to a store path and the case passes vacuously.
  # Measured -- this was the fixture's first version and it reported a false FAILURE.
  : > "$dir/plain-file"
  ln -s "$dir/plain-file" "$dir/not-store"
  if _store_path "$dir/not-store" >/dev/null 2>&1; then
    echo "  [self-test] _store_path FAILED: accepted a non-store path"; rc=1
  else
    echo "  [self-test] _store_path refuses a non-store path -> ok"
  fi
  if _store_path "$dir/does-not-exist" >/dev/null 2>&1; then
    echo "  [self-test] _store_path FAILED: accepted a dangling path"; rc=1
  else
    echo "  [self-test] _store_path refuses a dangling path -> ok"
  fi

  # --- the gate predicate, driven ----------------------------------------------------
  # The parsers being right is not the same as the GATE being right.
  if _over "11" "10"; then echo "  [self-test] _over 11 > 10 -> ok"; else echo "  [self-test] _over FAILED"; rc=1; fi
  if _over "10" "10"; then echo "  [self-test] _over FAILED: 10 > 10"; rc=1; else echo "  [self-test] _over 10 == 10 -> not over -> ok"; fi
  if _over "0" "10";  then echo "  [self-test] _over FAILED: 0 > 10"; rc=1;  else echo "  [self-test] _over 0 < 10 -> not over -> ok"; fi

  # --- THE GATE ITSELF, both directions, on MEASURED numbers ------------------------
  # A guard whose refusing branch has never been watched execute is not a guard, and a
  # guard that refuses everything is not one either. Both are driven here.
  #
  # 🔴 The refusing fixture is not invented: 40 pending / 47 total / +7 delta is what
  # `nixos-rebuild dry-build` reported on this host on 2026-09-07, against an UNCHANGED
  # config and with the tailscale block added. The gate must refuse THAT.
  got=$(_gate_reasons 26.11 26.11 40 47)
  if printf '%s' "$got" | grep -q 'PENDING WORK UNRELATED'; then
    echo "  [self-test] gate REFUSES the real measured state (40 pending / 47 total) -> ok"
  else
    echo "  [self-test] gate FAILED: allowed 40 pending derivations. got: '$got'"; rc=1
  fi
  # The allowing branch: nothing pending, tailscale's own 7 derivations. If this
  # refused, the script could never succeed and the gate would be a permanent red
  # light that everyone learns to override.
  got=$(_gate_reasons 26.11 26.11 0 7)
  if [ -z "$got" ]; then
    echo "  [self-test] gate ALLOWS a clean tree with tailscale's own 7 builds -> ok"
  else
    echo "  [self-test] gate FAILED: refused a clean tailscale-only delta: '$got'"; rc=1
  fi
  # 🔴 THE RELEASE JUMP, which must refuse on its own even when the counts are tiny --
  # a release change is an OS upgrade however small the queue looks at eval time.
  got=$(_gate_reasons 26.05 26.11 0 3)
  if printf '%s' "$got" | grep -q 'NIXPKGS RELEASE CHANGE'; then
    echo "  [self-test] gate REFUSES a 26.05 -> 26.11 jump even with 3 builds pending -> ok"
  else
    echo "  [self-test] gate FAILED: a release jump passed on low counts. got: '$got'"; rc=1
  fi
  # And the total-size gate on its own, with a clean baseline.
  got=$(_gate_reasons 26.11 26.11 0 900)
  if printf '%s' "$got" | grep -q 'TOTAL BUILD SIZE'; then
    echo "  [self-test] gate REFUSES 900 total builds from a clean baseline -> ok"
  else
    echo "  [self-test] gate FAILED: 900 builds allowed. got: '$got'"; rc=1
  fi
  return $rc
}

_over() { [ "$1" -gt "$2" ]; }

# THE GATE, as a pure function of the four numbers, so both of its branches can be
# driven from fixtures. A refusal that has only ever been reasoned about is not a
# guard; the self-test below watches this one both refuse and allow, using the numbers
# MEASURED on this host rather than invented ones.
# Prints one reason per paragraph; empty output means "allow".
_gate_reasons() {   # $1 run_rel  $2 cand_rel  $3 pending_builds  $4 total_builds
  local run_rel="$1" cand_rel="$2" b_built="$3" c_built="$4" d_built=$(( $4 - $3 ))
  if [ "$run_rel" != "$cand_rel" ]; then
    printf '%s\n' "NIXPKGS RELEASE CHANGE: $run_rel -> $cand_rel.
    This is an OS UPGRADE, not a feature. The last time this happened here a 4-line
    nebula edit turned into a 2h16m world rebuild. Refused regardless of size."
  fi
  if _over "$b_built" "$MAX_PENDING_BUILDS"; then
    printf '%s\n' "PENDING WORK UNRELATED TO TAILSCALE: $b_built derivations are already
    queued by the CURRENT config (limit $MAX_PENDING_BUILDS), and \`switch\` applies
    them too. Only $d_built of the $c_built are tailscale's.
    THE CLEAN FIX IS TO SEPARATE THE TWO OPERATIONS: run \`sudo nixos-rebuild switch\`
    on its own, at a time you choose, watch it land, then re-run this script -- the
    delta will then be just tailscale. Or accept it with --allow-world-rebuild."
  fi
  if _over "$c_built" "$MAX_TOTAL_BUILDS"; then
    printf '%s\n' "TOTAL BUILD SIZE: $c_built derivations would be built (limit
    $MAX_TOTAL_BUILDS). Adding tailscale should be a handful of substituted paths."
  fi
}

if [ "$SELFTEST" = "1" ]; then
  command -v python3 >/dev/null 2>&1 || die "python3 is not on PATH"
  echo "self-test:"
  st_rc=0
  _self_test || st_rc=$?     # 🔴 seed FIRST. `f; rc=$?` is dead code under `set -e`,
                             # and `f || rc=$?` without the seed dies under `set -u`
                             # on the SUCCESS path. Both shapes have shipped here.
  if [ "$st_rc" != "0" ]; then echo "self-test: FAILED" >&2; exit 1; fi
  echo "self-test: all controls passed"
  exit 0
fi

# =====================================================================================
# preflight -- no writes
# =====================================================================================
echo "== preflight =="

[ "$(id -u)" = "0" ] || die "must run as root:
  sudo env \"PATH=\$PATH\" bash ${BASH_SOURCE[0]}"

# 🔴 python3 is NOT in /run/current-system/sw/bin on these hosts, and sudo here has no
# secure_path -- root gets a python3 only by inheriting the caller's PATH. Preflighted
# rather than discovered halfway through a patch.
for t in awk sed diff systemctl ip date nixos-rebuild nix-instantiate nix-store nix python3; do
  command -v "$t" >/dev/null 2>&1 || die "\`$t\` is not on PATH.
  sudo inherits the CALLER's PATH here (there is no secure_path), and python3 in
  particular is NOT in /run/current-system/sw/bin. Re-run from a shell that has it:
    sudo env \"PATH=\$PATH\" bash ${BASH_SOURCE[0]}"
done

[ -r "$CHECK" ] || die "cannot read the verifier at $CHECK (run this from the repo checkout)"

echo "  self-test : validating this script's own parsers before trusting their numbers"
# 🔴 CAPTURED, THEN INDENTED -- deliberately NOT `_self_test | sed 's/^/  /' || rc=$?`.
# In a pipeline `$?` is the pipeline's status, which is `sed`'s (always 0) UNLESS
# `pipefail` happens to be set. MEASURED both ways: with pipefail rc=1, without it rc=0
# and the gate is INERT while looking correct. That would make this whole
# parsers-earn-their-verdict check a no-op keyed on an exit code reporting something
# else. Not left depending on a `set` line 400 lines away.
st_rc=0
st_out=$(_self_test) || st_rc=$?
printf '%s\n' "$st_out" | sed 's/^/  /'
[ "$st_rc" = "0" ] || die "this script's parsers failed their own controls -- every
  number the closure preflight prints would be meaningless"

# --- which host is this? --------------------------------------------------------------
if [ -z "$ROLE" ]; then
  have_ip=$(ip -4 -o addr show nebula.mesh 2>/dev/null | awk '{print $4}' | cut -d/ -f1 | head -1)
  [ -n "$have_ip" ] || die "no address on nebula.mesh, so the host role cannot be
  auto-detected, and \`hostname\` does not discriminate these machines (both answer
  \`nixos\`). Say which host this is:
    --role server   # the workbench, $MESH_SERVER, becomes the subnet router
    --role client   # the laptop,    $MESH_CLIENT, becomes a plain client"
  case "$have_ip" in
    "$MESH_SERVER") ROLE=server ;;
    "$MESH_CLIENT") ROLE=client ;;
    *) die "WRONG HOST: nebula.mesh is $have_ip, which is neither the workbench
  ($MESH_SERVER) nor the laptop ($MESH_CLIENT). Refusing to guess. If you really mean
  this host, pass --role server or --role client." ;;
  esac
  echo "  host      : nebula.mesh = $have_ip -> role '$ROLE'"
else
  echo "  host      : role '$ROLE' (forced)"
fi

[ -f "$CFG" ] && [ -w "$CFG" ] || die "$CFG is not a writable regular file"

# --- already done? ---------------------------------------------------------------------
# Asked of the FILE here because this is a question about what the file will contain.
# Whether it is RUNNING is a different question, and check-tailscale.sh is the only
# thing that answers it -- it reads the live daemon, never the config.
if grep -q 'services\.tailscale' "$CFG"; then
  echo "  state     : $CFG already mentions services.tailscale:"
  grep -n 'services\.tailscale' "$CFG" | sed 's/^/    | /'
  echo
  echo "Nothing to do. Exiting 0 without touching $CFG."
  echo "Whether it is actually WORKING is a different question -- ask the verifier:"
  echo "    bash ${CHECK} --role ${ROLE}"
  exit 0
fi

# --- a sysctl key defined twice is a Nix evaluation error --------------------------------
# `boot.kernel.sysctl."x"` and `boot.kernel.sysctl = { ... }` merge happily (measured),
# but two definitions of the SAME key at the same priority do not. Checked before
# writing rather than discovered by the parse afterwards, so the message names the
# actual conflict.
# Scoped to the SERVER role, because the client block adds no sysctls at all -- an
# unscoped loop would abort on a laptop whose config already tunes forwarding for some
# unrelated reason, refusing a change that could not have collided with it.
sysctl_keys=()
if [ "$ROLE" = "server" ]; then
  sysctl_keys=("net.ipv4.ip_forward" "net.ipv6.conf.all.forwarding")
fi
for k in ${sysctl_keys[@]+"${sysctl_keys[@]}"}; do
  if grep -q "\"${k}\"" "$CFG"; then
    die "$CFG already defines the sysctl \"$k\":
$(grep -n "\"${k}\"" "$CFG" | sed 's/^/    /')
  Two definitions of one key at the same priority is a Nix evaluation error. Merge it
  by hand instead of letting this script add a second one."
  fi
done

# --- the anchor -------------------------------------------------------------------------
# The block is appended just before the module's final closing brace. Two independent
# conditions must BOTH hold, so a file shaped differently aborts rather than being
# guessed at: exactly one line in the file is a bare `}` at column 0, AND it is the last
# non-blank line.
closers=$(grep -c '^}[[:space:]]*$' "$CFG" || true)
last_sig=$(awk 'NF{l=$0} END{print l}' "$CFG")
if [ "$closers" != "1" ] || [ "$last_sig" != "}" ]; then
  die "cannot locate the module's final closing brace in $CFG
  (bare-\`}\` lines: $closers, expected 1; last non-blank line: '$last_sig', expected '}')
  Add the tailscale block by hand instead -- see --help for exactly what it should say."
fi
anchor_line=$(grep -n '^}[[:space:]]*$' "$CFG" | cut -d: -f1)
echo "  anchor    : the module's final \`}\` is line $anchor_line, and it is unique"
echo

# =====================================================================================
# build the patched config in a TEMP FILE -- $CFG is not touched until the preflight
# passes. The temp lives NEXT TO $CFG on purpose: the config's own `imports` are
# RELATIVE (./airvpn-host.nix, ./hardware-configuration.nix), so a temp file anywhere
# else evaluates to a different -- and broken -- system.
# =====================================================================================
echo "== patch (temp file only) =="
TMP="${CFG%.nix}.tailscale-candidate.$$.nix"
BAK="${CFG}.bak-tailscale-$(date +%Y%m%d-%H%M%S)-$$"
WORK=$(mktemp -d)
PATCHED=0
SWITCHED=0
OK=0

finish() {
  local rc=$?
  rm -f "$TMP"
  rm -rf "$WORK"
  if [ "$OK" = "1" ]; then exit $rc; fi
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

if [ "$ROLE" = "server" ]; then
  BLOCK=$(cat <<EOF

  # --- Tailscale: the INDEPENDENT SECOND PATH to this host ---------------------------
  # Added by nix/system/apply-tailscale.sh. Nebula is the primary remote path; this
  # exists so that a nebula outage while off-LAN is not a total loss of access. It
  # shares no component with nebula: different control plane, different relays.
  #
  # THIS HOST IS THE SUBNET ROUTER. Advertising ${SUBNET} makes the whole LAN
  # reachable over the tailnet, not just this machine.
  #
  # 🔴 WHAT THIS BLOCK DOES **NOT** DO. It starts tailscaled and permits routing. It
  # does NOT log the node in and it does NOT advertise anything -- both are runtime
  # state owned by \`tailscale up\`, which needs a browser. And the route still has to
  # be APPROVED in the admin console before it carries a single packet; until then the
  # node reports it is advertising and nothing flows. \`check-tailscale.sh\` reports
  # those as separate claims because they are separate claims.
  services.tailscale = {
    enable = true;
    useRoutingFeatures = "server";
    openFirewall = true;              # UDP 41641 for direct (non-DERP) connections
  };

  # A subnet router that cannot forward advertises a route into a black hole.
  #
  # 🔴 REDUNDANT TODAY, ON PURPOSE. \`useRoutingFeatures = "server"\` already sets
  # net.ipv4.conf.all.forwarding and net.ipv6.conf.all.forwarding at \`mkOverride 97\`,
  # which OUTRANKS a plain definition here (97 < 100), so the ipv6 line below loses and
  # the module's value is what lands. Both are kept anyway: flipping useRoutingFeatures
  # would otherwise take forwarding with it silently. net.ipv4.ip_forward is the same
  # kernel knob as net.ipv4.conf.all.forwarding, spelled the way check-tailscale.sh
  # reads it back out of /proc.
  boot.kernel.sysctl."net.ipv4.ip_forward" = 1;
  boot.kernel.sysctl."net.ipv6.conf.all.forwarding" = 1;
EOF
)
else
  BLOCK=$(cat <<EOF

  # --- Tailscale: the INDEPENDENT SECOND PATH off this host --------------------------
  # Added by nix/system/apply-tailscale.sh. This host is a PLAIN CLIENT: it advertises
  # nothing and consumes the workbench's ${SUBNET} advertisement.
  #
  # \`useRoutingFeatures = "client"\` is what makes an accepted subnet route usable: it
  # sets networking.firewall.checkReversePath = "loose", without which return traffic
  # over a subnet route is dropped by strict reverse-path filtering.
  #
  # 🔴 It does NOT log the node in, and it does not set --accept-routes. Both are
  # runtime state owned by \`tailscale up\`. See check-tailscale.sh.
  services.tailscale = {
    enable = true;
    useRoutingFeatures = "client";
    openFirewall = true;              # UDP 41641 for direct (non-DERP) connections
  };
EOF
)
fi

awk -v blk="$BLOCK" -v anchor="$anchor_line" '
  NR == anchor { print blk }
  { print }
' "$CFG" > "$TMP"

added=$(( $(wc -l < "$TMP") - $(wc -l < "$CFG") ))
expected=$(printf '%s\n' "$BLOCK" | wc -l)
[ "$added" = "$expected" ] || die "expected the patch to add exactly $expected lines, it added $added"
echo "  temp file : $TMP  (+$added lines)"

nix-instantiate --parse "$TMP" >/dev/null 2>&1 || die "the patched file is not valid Nix -- $CFG untouched"
echo "  nix parse : OK"
echo "  diff:"
diff -u "$CFG" "$TMP" | sed 's/^/    /' || true
echo

# =====================================================================================
# 🔴 THE CLOSURE PREFLIGHT
# =====================================================================================
echo "== closure preflight =="
echo "  \`nixos-rebuild switch\` applies EVERYTHING PENDING, not just this delta."
echo "  Measuring what it would actually do, from the TEMP file. $CFG is still untouched."
echo

running=$(_store_path /run/current-system) \
  || die "/run/current-system does not resolve into /nix/store -- refusing to compare"
profile=$(_store_path /nix/var/nix/profiles/system || true)
echo "  running   : $running"
if [ -n "$profile" ] && [ "$profile" != "$running" ]; then
  echo "  NOTE: /nix/var/nix/profiles/system resolves to a DIFFERENT closure:"
  echo "        $profile"
  echo "        (both were resolved with \`readlink -f\`; the profile is a symlink TO A"
  echo "         SYMLINK, so this is a real difference and not the classic comparison bug)"
fi

running_version=$(basename "$running" | _version_from_store_basename)
[ -n "$running_version" ] || die "could not extract a version from $running"
# CONTROL: the extractor's answer must agree with the file the system ships. Without
# this, an extractor that is wrong on BOTH sides agrees with itself and waves a release
# jump straight through.
if [ -r /run/current-system/nixos-version ]; then
  file_version=$(tr -d '[:space:]' < /run/current-system/nixos-version)
  [ "$running_version" = "$file_version" ] || die "the version extractor disagrees with
  /run/current-system/nixos-version (extracted '$running_version', file says
  '$file_version'). Refusing to gate on a number this script cannot read correctly."
  echo "  version   : $running_version  (extractor agrees with /run/current-system/nixos-version)"
else
  echo "  version   : $running_version  (no nixos-version file to cross-check against)"
fi

echo "  evaluating the candidate system derivation..."
cand_drv=$(nix-instantiate '<nixpkgs/nixos>' -A system --include "nixos-config=$TMP" 2>"$WORK/inst.err") \
  || { sed 's/^/    | /' "$WORK/inst.err" >&2; die "could not evaluate the patched configuration"; }
cand_version=$(basename "$cand_drv" | _version_from_store_basename)
[ -n "$cand_version" ] || die "could not extract a version from the candidate derivation
  $cand_drv"
echo "  candidate : $cand_version"

run_rel=$(_release_of "$running_version")
cand_rel=$(_release_of "$cand_version")

echo
echo "  measuring what is PENDING (the current config, unchanged)..."
nixos-rebuild dry-build --include "nixos-config=$CFG" >"$WORK/base.out" 2>"$WORK/base.err" \
  || die "dry-build of the CURRENT config failed -- fix that before adding anything"
base=$(_drybuild_counts "$WORK/base.err")
IFS='|' read -r b_built b_fetch b_mib <<<"$base"

echo "  measuring the CANDIDATE (with tailscale)..."
nixos-rebuild dry-build --include "nixos-config=$TMP" >"$WORK/cand.out" 2>"$WORK/cand.err" \
  || die "dry-build of the PATCHED config failed"
cand=$(_drybuild_counts "$WORK/cand.err")
IFS='|' read -r c_built c_fetch c_mib <<<"$cand"

d_built=$(( c_built - b_built ))
d_fetch=$(( c_fetch - b_fetch ))

echo
printf '  %-28s %8s %8s %10s\n' "" "build" "fetch" "download"
printf '  %-28s %8s %8s %9s M\n' "PENDING (not yours)"    "$b_built" "$b_fetch" "$b_mib"
printf '  %-28s %8s %8s %9s M\n' "TOTAL if you switch"    "$c_built" "$c_fetch" "$c_mib"
printf '  %-28s %8s %8s\n'       "DELTA (tailscale only)" "$d_built" "$d_fetch"
echo
echo "  release   : running $run_rel  ->  candidate $cand_rel"
echo

# --- the gate ---------------------------------------------------------------------------
# One implementation, exercised in both directions by --self-test above.
refuse=$(_gate_reasons "$run_rel" "$cand_rel" "$b_built" "$c_built")

if [ -n "$refuse" ] && [ "$ALLOW_WORLD" != "1" ]; then
  echo "REFUSING to switch:" >&2
  printf '  - %s\n' "$refuse" >&2
  echo >&2
  echo "  $CFG was NOT modified. Nothing to roll back." >&2
  echo "  Largest pending items, so you can judge the cost yourself:" >&2
  # 🔴 `|| true` is load-bearing, twice over: `grep` exits 1 when it matches nothing,
  # and `head` closing the pipe early SIGPIPEs everything upstream. With `set -e` plus
  # `pipefail` either would abort HERE -- inside the refusal path, one line before
  # `exit 4` -- so the script would exit 1 and never print the override the operator
  # needs. A cosmetic listing must not be able to change the exit code.
  { grep -oE '/nix/store/[a-z0-9]+-[^ ]+\.drv' "$WORK/base.err" | sed 's#.*/[a-z0-9]*-##' \
      | sort -u | head -15 | sed 's/^/      /'; } >&2 || true
  echo >&2
  echo "  To proceed anyway, exactly as written:" >&2
  echo "      sudo env \"PATH=\$PATH\" bash ${BASH_SOURCE[0]} --allow-world-rebuild" >&2
  exit 4
fi
if [ -n "$refuse" ]; then
  echo "  🔴 --allow-world-rebuild given; proceeding DESPITE:"
  printf '     - %s\n' "$refuse"
  echo
fi

if [ "$DRYRUN" = "1" ]; then
  echo "== --dry-run =="
  echo "Stopping here. $CFG was NOT modified and nothing was switched."
  echo "The block that WOULD be added is in the diff above."
  OK=1
  exit 0
fi

# --- build (still not activating) ---------------------------------------------------------
echo "  building the candidate system (no activation)..."
( cd "$WORK" && nixos-rebuild build --include "nixos-config=$TMP" ) \
  || die "the candidate system failed to BUILD -- $CFG untouched"
built=$(_store_path "$WORK/result") \
  || die "nixos-rebuild build left no usable ./result symlink"
echo "  built     : $built"

# --- the closure, compared as SETS ---------------------------------------------------------
# 🔴 Deliberately not a parse of `nix store diff-closures`. That command prints NOTHING
# when two closures match, so an empty result is indistinguishable from a command that
# did not run -- the reassuring-zero shape. A set difference of the two closures cannot
# do that: it is derived from two enumerations that are separately non-empty, and the
# closure sizes are printed beside the answer so a zero can be checked against them.
nix-store -qR "$running" | sort > "$WORK/run.txt"
nix-store -qR "$built"   | sort > "$WORK/new.txt"
n_run=$(wc -l < "$WORK/run.txt"); n_new=$(wc -l < "$WORK/new.txt")
[ "$n_run" -gt 100 ] && [ "$n_new" -gt 100 ] \
  || die "a system closure of $n_run / $n_new paths is not credible -- \`nix-store -qR\`
  did not do what this script assumes, so the comparison below would be meaningless"
changed=$(comm -3 "$WORK/run.txt" "$WORK/new.txt" | wc -l)
echo "  closure   : running $n_run paths, candidate $n_new paths, $changed differ"
echo
echo "  package-level diff (nix store diff-closures):"
nix store diff-closures "$running" "$built" 2>/dev/null | head -40 | sed 's/^/    /' || true
echo

if _over "$changed" "$MAX_CHANGED_PATHS" && [ "$ALLOW_WORLD" != "1" ]; then
  die "$changed store paths differ between the running and candidate closures (limit
  $MAX_CHANGED_PATHS). That is a world-sized change, not a tailscale-sized one.
  $CFG was NOT modified. To proceed anyway:
      sudo env \"PATH=\$PATH\" bash ${BASH_SOURCE[0]} --allow-world-rebuild"
fi

# =====================================================================================
# only now is the real config touched
# =====================================================================================
echo "== apply =="
[ -e "$BAK" ] && die "backup path $BAK already exists; refusing to overwrite it"
cp -p "$CFG" "$BAK"
echo "  backup    : $BAK"
cp -p "$TMP" "$CFG"
PATCHED=1
echo "  applied   : $CFG"
echo

echo "== nixos-rebuild switch =="
nixos-rebuild switch
SWITCHED=1
echo

# =====================================================================================
# verify -- the RUNTIME symptom, not the rollout
# =====================================================================================
echo "== verify =="
active=$(systemctl is-active tailscaled.service 2>/dev/null || true)
[ "$active" = "active" ] || die "tailscaled is '$active' after the switch, not 'active'"
echo "  unit      : tailscaled active"
command -v tailscale >/dev/null 2>&1 || die "the \`tailscale\` CLI is still not on PATH
  after the switch. Open a new shell and re-check before assuming this failed."
echo "  cli       : $(tailscale version 2>&1 | head -1)"

# The verifier is the authority on whether this is a working path. rc 3 means the node
# is correct and an ADMIN-CONSOLE action is outstanding -- that is the EXPECTED state
# straight after a switch, and rolling back a correct config because a human has not
# opened a browser yet would be absurd.
chk_rc=0
"$CHECK" --role "$ROLE" || chk_rc=$?
case "$chk_rc" in
  0) echo "  verifier  : PASS -- nothing outstanding" ;;
  2|3) echo "  verifier  : rc $chk_rc -- the node still needs the manual steps below" ;;
  *) die "the verifier reports a node-side FAILURE (rc=$chk_rc) after the switch" ;;
esac

OK=1
echo
echo "=== SWITCHED ==="
echo "Backup of the previous config: $BAK"
echo "To revert:  sudo cp $BAK $CFG && sudo nixos-rebuild switch"
echo
echo "🔴 NOT DONE YET. Three steps remain and NONE of them can be scripted."
echo
echo "1. AUTHENTICATE THIS NODE (needs a browser; prints a login URL):"
if [ "$ROLE" = "server" ]; then
  echo "       sudo tailscale up --advertise-routes=${SUBNET} --accept-dns=false"
  echo "   (--accept-dns=false keeps tailscale out of this host's resolver, which"
  echo "    dnsmasq and the .lan names already own.)"
else
  echo "       sudo tailscale up --accept-routes"
fi
echo
if [ "$ROLE" = "server" ]; then
  echo "2. APPROVE THE SUBNET ROUTE -- admin console only. Until this is done the route"
  echo "   carries NO traffic and the node cannot tell the difference:"
  echo "       https://login.tailscale.com/admin/machines"
  echo "       -> this machine -> ... -> Edit route settings -> tick ${SUBNET} -> Save"
  echo
fi
echo "3. DISABLE NODE KEY EXPIRY -- admin console only. The default is 180 days, which"
echo "   is SHORTER than the trip; when it lapses this backup path goes dark with no"
echo "   local error:"
echo "       https://login.tailscale.com/admin/machines"
echo "       -> this machine -> ... -> Disable key expiry"
echo
echo "Then confirm all of it, from live runtime state:"
echo "       bash ${CHECK} --role ${ROLE}      # 0 = done, 3 = a step above is outstanding"
