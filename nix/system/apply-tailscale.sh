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
#   TS_MAX_PENDING_MIB=64            download volume, gated separately from build count
#   TS_MAX_TOTAL_MIB=128
#   TS_MAX_PENDING_FETCH=50          number of paths to fetch, gated separately again --
#   TS_MAX_TOTAL_FETCH=100           it is the axis that still has a number when the
#                                    download SIZE cannot be parsed
#   TS_MAX_CHANGED_PATHS=250
#   TS_LIST_MAX=200                  how many pending derivation names a refusal prints
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
# **+7 derivations, +1 fetched path and +17.6 MiB** -- `tailscale-1.102.3` plus six
# regenerated unit/etc/activation derivations. That is the scale a "add tailscale" change
# should be, and it is why TS_MAX_PENDING_BUILDS defaults to 10, TS_MAX_TOTAL_BUILDS to
# 25, TS_MAX_PENDING_MIB to 64 and TS_MAX_TOTAL_MIB to 128. On this host today the
# pending gates therefore REFUSE, which is the correct answer: 40 queued derivations and
# 159.4 MiB of queued downloads are not what you asked for.
#
# So, BEFORE switching, this script measures the change in FOUR ways and REFUSES by
# default when the answer is "you are about to rebuild the world". Each is listed with
# what it CANNOT see, because a gate's blind spot is the part worth knowing:
#
#   1. BUILD COUNTS, BASELINE vs CANDIDATE -- not just the total. `nixos-rebuild
#      dry-build` is run TWICE, once against the CURRENT config and once against the
#      patched one. The difference is what tailscale actually costs; the baseline is what
#      `switch` would drag in whether or not you ran this script. Reporting only the
#      total would blame tailscale for a channel bump, and reporting only the delta would
#      hide it.
#      BLIND TO: a change that is entirely SUBSTITUTABLE. Zero derivations to build and
#      thousands of paths to fetch is a world-sized change with a build count of 0 --
#      which is why (2) exists as a separate gate rather than a printed column.
#
#   2. DOWNLOAD VOLUME **AND FETCH COUNT**, baseline and total. The same two dry-builds,
#      the other two numbers they report. Both gated in their OWN right: build count,
#      download volume and path count are independent axes and any one of them can be
#      world-sized while the others are tiny. Both were once decorative columns in the
#      summary table that no gate read.
#      🔴 AND THE SIZE IS FAIL-CLOSED. If dry-build announces paths to fetch but prints a
#      size this script cannot parse -- an unknown unit, a comma decimal separator, or no
#      parenthetical at all -- the volume is reported as UNKNOWN and REFUSED, never as
#      0.0. Reporting it as zero is exactly how a 2400-path substitutable world rebuild
#      passed all four gates in silence; measured, on four separate malformed shapes.
#      The FETCH COUNT is the backstop for that same blind spot: it still has a number
#      when the size does not.
#
#   3. THE NIXPKGS RELEASE STRING. Extracted with ONE implementation from the store-path
#      basename of both the running system and the candidate derivation, so the two can
#      never drift apart, and cross-checked against /run/current-system/nixos-version so
#      a broken extractor cannot silently agree with itself. A release change is refused
#      unconditionally -- it is an OS upgrade wearing a feature's clothes.
#      🔴 BLIND TO A REVISION BUMP WITHIN ONE RELEASE, BY CONSTRUCTION. The release is
#      the leading major.minor, so `26.11pre1066106` -> `26.11pre1066425` -- which is
#      exactly the pending state of this host today -- reads as NO release change and
#      this gate does not fire. That is deliberate (every channel tick would otherwise
#      refuse), and it is why gates 1 and 2 are the ones that actually stop a same-release
#      world rebuild. When the releases match but the revisions differ, the preflight
#      says so out loud instead of printing a bare, reassuring "26.11 -> 26.11".
#
#   4. THE CLOSURE SET. After a build that the gates above have already approved, the
#      running and candidate closures are compared as SETS (`nix-store -qR | comm -3`),
#      which cannot silently return a reassuring zero the way a parsed diff can, plus
#      `nix store diff-closures` for a human-readable package-level report.
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
# Re-running once tailscale is DECLARED (an actual setting, not a mention in a comment)
# is a no-op that exits 0.
#
# 🔴 WHAT SUCCESS LOOKS LIKE, AND WHY IT IS NOT rc 0 FROM THE VERIFIER. This script does
# not run `tailscale up` -- that needs a browser. So the state it lands in is: service
# installed, daemon running, node NOT authenticated. `check-tailscale.sh` calls that
# rc 4 (INCOMPLETE, no defect), and rc 4 is a SUCCESSFUL apply. An earlier version
# treated anything other than rc 0/2/3 as a node-side failure and rolled back the config
# it had just installed -- on the very first run, every time, guaranteed.
#
# 🔴 KILLSWITCH INTERACTION -- READ THIS BEFORE TRUSTING THE REDUNDANCY STORY.
# `scripts/airvpn-updown`'s degraded/fallback rulesets allow egress on a LITERAL
# interface list -- `lo`, the airvpn tun, `nebula.mesh`, `cni0`, `flannel.1`, `docker0`
# -- and then `drop`. `tailscale0` is NOT on that list and there is no DERP/control-plane
# carve-out, while nebula has three. So IF the AirVPN killswitch ever arms fail-closed,
# NEBULA SURVIVES AND TAILSCALE DIES -- the exact inverse of the independence this whole
# change is for. AirVPN is default-OFF on these hosts, so this is a latent interaction
# and not a live defect, and `airvpn-updown` is deliberately NOT touched here (changing
# a killswitch to widen egress is its own change, with its own review). Recorded so the
# next person does not discover it from the far side of an outage.
set -euo pipefail

SUBNET="${TS_SUBNET:-192.168.50.0/24}"
CFG="${TS_CFG:-/etc/nixos/configuration.nix}"
MESH_SERVER="${TS_EXPECT_MESH_IP_SERVER:-10.42.0.30}"
MESH_CLIENT="${TS_EXPECT_MESH_IP_CLIENT:-10.42.0.100}"
MAX_PENDING_BUILDS="${TS_MAX_PENDING_BUILDS:-10}"
MAX_TOTAL_BUILDS="${TS_MAX_TOTAL_BUILDS:-25}"
MAX_PENDING_MIB="${TS_MAX_PENDING_MIB:-64}"
MAX_TOTAL_MIB="${TS_MAX_TOTAL_MIB:-128}"
# Measured 2026-09-07 on this host: 24 paths pending, 25 with tailscale added. These are
# ~2x and ~4x that, so they refuse a world-sized queue without becoming a permanent red
# light on a normal one.
MAX_PENDING_FETCH="${TS_MAX_PENDING_FETCH:-50}"
MAX_TOTAL_FETCH="${TS_MAX_TOTAL_FETCH:-100}"
MAX_CHANGED_PATHS="${TS_MAX_CHANGED_PATHS:-250}"
LIST_MAX="${TS_LIST_MAX:-200}"

DRYRUN=0
ALLOW_WORLD=0
ROLE="${TS_ROLE:-}"
SELFTEST=0

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CHECK="${HERE}/check-tailscale.sh"

# 🔴 The help text is the comment header, printed to WHEREVER IT ENDS -- deliberately not
# a hardcoded line range. `sed -n '2,95p'` cut this file's SAFETY paragraph off in the
# middle of its second sentence, silently dropping the rollback and idempotency
# guarantees from `--help`; check-tailscale.sh's equivalent OVERSHOT and printed the
# literal `set -euo pipefail` as documentation. A line range is a second copy of a fact
# the file already states, and it goes stale on the first edit. This reads the fact.
_print_help() { awk 'NR==1 {next} /^#/ {sub(/^# ?/, ""); print; next} {exit}' "${BASH_SOURCE[0]}"; }

while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run)             DRYRUN=1; shift ;;
    --allow-world-rebuild) ALLOW_WORLD=1; shift ;;
    # 🔴 `--role` AS THE FINAL ARGUMENT. `shift 2` with one argument left fails, and
    # under `set -e` that killed the script with exit 1 and NOTHING printed at all --
    # a bare failure with no hint of what was wrong, on a script that is run under sudo
    # by an operator with three days left. Checked explicitly.
    --role)                [ $# -ge 2 ] || { echo "ABORT: --role needs a value ('server' or 'client'); it was given as the last argument with nothing after it." >&2; exit 1; }
                           ROLE="$2"; shift 2 ;;
    --role=*)              ROLE="${1#--role=}"; shift ;;
    --self-test)           SELFTEST=1; shift ;;
    -h|--help)             _print_help; exit 0 ;;
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
#
# 🔴 THE SIZE FIELD IS `UNKNOWN`, NOT 0.0, WHENEVER IT CANNOT BE READ -- AND THAT IS THE
# WHOLE POINT OF THIS FUNCTION'S SECOND HALF. The previous implementation searched for
#   will be fetched \(([0-9.]+) ([KMG]i?B) download
# and left `mib = 0.0` on no match, which is indistinguishable from a genuine zero.
# MEASURED, all four against the real gate: `these 2400 paths will be fetched:` (no
# parenthetical at all), `2.5 TiB`, `900000000 B`, and `4096,0 MiB` (a comma decimal
# separator, which a non-C locale produces) EACH returned `0|2400|0.0` -- so a
# 2400-path, entirely substitutable world rebuild passed all four gates in silence.
# That is precisely the change the download gate was added to stop.
#
# So the distinction that has to survive is "there was nothing to fetch" vs "there was
# something to fetch and I could not size it":
#   * NO fetch line at all  -> genuinely nothing to fetch -> 0.0.
#   * a fetch line WITH a size this function understands -> that size, scaled to MiB.
#   * a fetch line whose size it cannot read -> the literal string `UNKNOWN`, which
#     `_gate_reasons` REFUSES on. Fail closed: an unmeasured download is not a small one.
# The unit table is exhaustive over what nix emits and `.get()` returns None -- there is
# deliberately no defaulting-to-MiB fallback, because a unit this script has never heard
# of is exactly the case where guessing 1.0 turns a TiB into a rounding error.
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

UNITS = {"B": 1 / 1048576.0,
         "KB": 1 / 1024.0, "KiB": 1 / 1024.0,
         "MB": 1.0,        "MiB": 1.0,
         "GB": 1024.0,     "GiB": 1024.0,
         "TB": 1048576.0,  "TiB": 1048576.0}

fetch_line = re.search(r"^(?:these [0-9]+|this) paths? will be fetched\b(.*)$", text, re.M)
if fetch_line is None:
    mib = "0.0"                       # no fetch line -> nothing to fetch -> a real zero
else:
    m = re.match(r"\s*\(\s*([0-9]+(?:\.[0-9]+)?)\s+([A-Za-z]+)\s+download\b",
                 fetch_line.group(1))
    if m is None:
        mib = "UNKNOWN"               # a fetch line we cannot size -- NOT a zero
    else:
        factor = UNITS.get(m.group(2))
        mib = "UNKNOWN" if factor is None else "%.1f" % (float(m.group(1)) * factor)
print("%d|%d|%s" % (count("built"), count("fetched"), mib))
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

# Is tailscale actually DECLARED in this config, or merely mentioned?
#
# 🔴 `grep -q 'services\.tailscale'` WAS THE WHOLE IDEMPOTENCY TEST, and a file whose
# only occurrence was `# TODO: consider services.tailscale one day` made this script
# print "Nothing to do. Exiting 0" on a host where nothing had been applied -- the most
# expensive possible lie, because it tells the operator the backup path is DONE.
# Measured; that exact one-line fixture reproduced it.
#
# So: blank out everything that is NOT executable Nix -- comments AND string literals --
# then require an actual SETTING: the attribute followed by `=`, `{` or a further `.`,
# which is every way Nix can spell one and no way it can spell a mention. Prints
# `lineno:text` per declaration, nothing at all when there is none, so the caller
# branches on emptiness.
#
# 🔴 STRINGS, NOT JUST COMMENTS -- AND THE `[.={]` ANCHOR IS NOT ENOUGH ON ITS OWN. An
# earlier version blanked comments only, on the reasoning that the anchor already rejects
# a bare mention. It does reject `"services.tailscale is not enabled here"`. It does NOT
# reject a string that happens to contain the punctuation, and MEASURED, both of these
# reported the host as ALREADY CONFIGURED and printed "Nothing to do. Exiting 0":
#     warnings = [ "you should run services.tailscale.enable = true; here" ];
#     text = ''<newline>  services.tailscale.enable = true;<newline>'';
# That is the most expensive lie this script can tell -- it says the backup path is DONE
# on a host where nothing was applied, two days before the operator leaves for months.
#
# The blanking is a LEFT-TO-RIGHT SCANNER, not three independent regexes, because the
# constructs nest: a `#` inside a string is not a comment and a `"` inside a comment is
# not a string, and only a scanner that consumes them in order gets both right. It
# handles `#` line comments, `/* */` blocks, `"..."` with backslash escapes, and Nix's
# `''...''` indented strings with their `''$`, `'''` and `''\` escapes. Newlines are
# preserved so the line numbers reported below still index the ORIGINAL file.
#
# Direction of error is deliberate, and unchanged: a declaration this misses means the
# script proceeds and adds a SECOND block, which `nix-instantiate '<nixpkgs/nixos>' -A
# system` then refuses as a duplicate definition BEFORE anything is written. A mention it
# wrongly accepted would exit 0 and leave the host unprotected with no further check at
# all. So when the scanner is confused -- by an identifier ending in `''`, say -- it
# fails toward the loud, harmless side.
_cfg_tailscale_decls() {   # $1 = config path; prints "lineno:line" per declaration
  python3 - "$1" <<'PY'
import re, sys
try:
    src = open(sys.argv[1], encoding="utf-8", errors="replace").read()
except Exception as e:
    sys.stderr.write("read: %s\n" % e); sys.exit(2)


def blank_non_code(text):
    """Replace comments and string literals with spaces, keeping every newline."""
    out = list(text)
    n = len(text)

    def wipe(a, b):
        for k in range(a, b):
            if out[k] != "\n":
                out[k] = " "

    i = 0
    while i < n:
        if text.startswith("#", i):
            j = text.find("\n", i)
            j = n if j < 0 else j
            wipe(i, j)
            i = j
        elif text.startswith("/*", i):
            j = text.find("*/", i + 2)
            j = n if j < 0 else j + 2
            wipe(i, j)
            i = j
        elif text.startswith("''", i):
            # Nix indented string. It ends at the next `''` that is not itself the start
            # of an escape: `''$`, `'''` and `''\` all continue the string.
            j = i + 2
            while j < n:
                if text.startswith("''", j):
                    if text[j + 2:j + 3] in ("$", "'", "\\"):
                        j += 3
                        continue
                    j += 2
                    break
                j += 1
            else:
                j = n
            wipe(i, j)
            i = j
        elif text.startswith('"', i):
            j = i + 1
            while j < n:
                if text[j] == "\\":
                    j += 2
                    continue
                if text[j] == '"':
                    j += 1
                    break
                j += 1
            else:
                j = n
            wipe(i, j)
            i = j
        else:
            i += 1
    return "".join(out)


stripped = blank_non_code(src)

orig = src.splitlines()
for i, line in enumerate(stripped.splitlines()):
    if re.search(r"services\s*\.\s*tailscale\s*[.={]", line):
        print("%d:%s" % (i + 1, orig[i].strip()))
PY
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
  got=$(_gate_reasons 26.11 26.11 40 47 0.0 17.6 3 4)
  if printf '%s' "$got" | grep -q 'PENDING WORK UNRELATED'; then
    echo "  [self-test] gate REFUSES the real measured state (40 pending / 47 total) -> ok"
  else
    echo "  [self-test] gate FAILED: allowed 40 pending derivations. got: '$got'"; rc=1
  fi
  # The allowing branch: nothing pending, tailscale's own 7 derivations. If this
  # refused, the script could never succeed and the gate would be a permanent red
  # light that everyone learns to override.
  got=$(_gate_reasons 26.11 26.11 0 7 0.0 17.6 2 5)
  if [ -z "$got" ]; then
    echo "  [self-test] gate ALLOWS a clean tree with tailscale's own 7 builds -> ok"
  else
    echo "  [self-test] gate FAILED: refused a clean tailscale-only delta: '$got'"; rc=1
  fi
  # 🔴 THE RELEASE JUMP, which must refuse on its own even when the counts are tiny --
  # a release change is an OS upgrade however small the queue looks at eval time.
  got=$(_gate_reasons 26.05 26.11 0 3 0.0 9.5 1 3)
  if printf '%s' "$got" | grep -q 'NIXPKGS RELEASE CHANGE'; then
    echo "  [self-test] gate REFUSES a 26.05 -> 26.11 jump even with 3 builds pending -> ok"
  else
    echo "  [self-test] gate FAILED: a release jump passed on low counts. got: '$got'"; rc=1
  fi
  # And the total-size gate on its own, with a clean baseline.
  got=$(_gate_reasons 26.11 26.11 0 900 0.0 17.6 3 6)
  if printf '%s' "$got" | grep -q 'TOTAL BUILD SIZE'; then
    echo "  [self-test] gate REFUSES 900 total builds from a clean baseline -> ok"
  else
    echo "  [self-test] gate FAILED: 900 builds allowed. got: '$got'"; rc=1
  fi

  # --- _over_mib, the FRACTIONAL comparison ------------------------------------------
  # `-gt` cannot compare "159.4" at all. MEASURED in the shape the gate actually uses
  # (`if _over ...; then`): bash prints `[: 159.4: integer expected` to stderr, `[`
  # returns 2, errexit does not apply to an `if` condition, and EXECUTION CONTINUES with
  # the gate SILENT -- it fails OPEN, it does not abort. See `_over_mib` below; this
  # comment used to say the opposite. Driven in both directions, and across the boundary
  # with a fraction on BOTH sides so a mutant that truncates to an integer (159.4 -> 159,
  # 64.5 -> 64) is visible.
  if _over_mib "159.4" "64";   then echo "  [self-test] _over_mib 159.4 > 64 -> ok"; else echo "  [self-test] _over_mib FAILED: 159.4 not > 64"; rc=1; fi
  if _over_mib "17.6" "64";    then echo "  [self-test] _over_mib FAILED: 17.6 > 64"; rc=1; else echo "  [self-test] _over_mib 17.6 < 64 -> not over -> ok"; fi
  if _over_mib "64.5" "64.4";  then echo "  [self-test] _over_mib 64.5 > 64.4 (fraction decides) -> ok"; else echo "  [self-test] _over_mib FAILED: 64.5 not > 64.4 -- truncating to int?"; rc=1; fi
  if _over_mib "64.0" "64.0";  then echo "  [self-test] _over_mib FAILED: equal read as over"; rc=1; else echo "  [self-test] _over_mib 64.0 == 64.0 -> not over -> ok"; fi
  if _over_mib "0.0" "0";      then echo "  [self-test] _over_mib FAILED: 0 > 0"; rc=1; else echo "  [self-test] _over_mib 0.0 == 0 -> not over -> ok"; fi

  # 🔴 THE SUBSTITUTABLE WORLD REBUILD -- the case the download gate exists for, and the
  # one that walked straight through a gate that read only build counts. 0 to build,
  # 4 GiB to fetch. Both build gates are silent here BY CONSTRUCTION, so if this passes
  # the download gate is genuinely the only thing that saw it.
  got=$(_gate_reasons 26.11 26.11 0 0 4096.0 4096.0 3 3)
  if printf '%s' "$got" | grep -q 'PENDING DOWNLOAD VOLUME'; then
    echo "  [self-test] gate REFUSES 4096 MiB of pending downloads at ZERO builds -> ok"
  else
    echo "  [self-test] gate FAILED: a 4 GiB substitutable change passed. got: '$got'"; rc=1
  fi
  if printf '%s' "$got" | grep -qE 'BUILD SIZE|PENDING WORK'; then
    echo "  [self-test] the build gates FAILED: they claim to have seen a 0-build change"; rc=1
  else
    echo "  [self-test] ...and neither BUILD gate fired on it -- so it was the download gate -> ok"
  fi
  # The download gates must ALLOW tailscale's own measured cost, or the script can never
  # succeed and the gate becomes a permanent red light everyone learns to override.
  got=$(_gate_reasons 26.11 26.11 0 7 0.0 17.6 0 1)
  if [ -z "$got" ]; then
    echo "  [self-test] gate ALLOWS tailscale's own measured 7 builds / 17.6 MiB -> ok"
  else
    echo "  [self-test] gate FAILED: refused the measured tailscale-only delta: '$got'"; rc=1
  fi
  # And the TOTAL download gate on its own, from a clean baseline.
  got=$(_gate_reasons 26.11 26.11 0 0 0.0 900.0 0 4)
  if printf '%s' "$got" | grep -q 'TOTAL DOWNLOAD VOLUME'; then
    echo "  [self-test] gate REFUSES 900 MiB total from a clean baseline -> ok"
  else
    echo "  [self-test] gate FAILED: 900 MiB total allowed. got: '$got'"; rc=1
  fi
  # 🔴 THE NEGATIVE DELTA. The two dry-builds are separate evaluations and CAN disagree;
  # the old phrasing rendered "Only -3 of the 37 are tailscale's" -- nonsense stated as
  # a measurement, inside the one message the operator reads to make a judgement call.
  got=$(_gate_reasons 26.11 26.11 40 37 0.0 17.6 3 2)
  if printf '%s' "$got" | grep -q 'Only -'; then
    echo "  [self-test] negative-delta phrasing FAILED: still says 'Only -N of the M'"; rc=1
  elif printf '%s' "$got" | grep -q 'FEWER builds'; then
    echo "  [self-test] a negative delta is reported as a DISAGREEMENT, not 'Only -3' -> ok"
  else
    echo "  [self-test] negative-delta phrasing FAILED: got '$got'"; rc=1
  fi
  got=$(_gate_reasons 26.11 26.11 40 47 0.0 17.6 3 4)
  if printf '%s' "$got" | grep -q "Only 7 of the 47 are tailscale's"; then
    echo "  [self-test] a POSITIVE delta still reads 'Only 7 of the 47' -> ok"
  else
    echo "  [self-test] positive-delta phrasing FAILED: got '$got'"; rc=1
  fi

  # --- _is_num, the thing that keeps a non-number away from the comparison ------------
  local numcase
  for numcase in "0:yes" "0.0:yes" "17.6:yes" "4096.0:yes" "159.4:yes" \
                 "UNKNOWN:no" ":no" ".:no" "1.2.3:no" "4096,0:no" "-1:no" "1e3:no"; do
    if _is_num "${numcase%%:*}"; then got=yes; else got=no; fi
    if [ "$got" = "${numcase##*:}" ]; then
      echo "  [self-test] _is_num '${numcase%%:*}' -> $got -> ok"
    else
      echo "  [self-test] _is_num '${numcase%%:*}' FAILED: got $got, want ${numcase##*:}"; rc=1
    fi
  done
  # The control that makes the case above matter: awk WOULD have said "not over".
  if _over_mib "UNKNOWN" "64"; then
    echo "  [self-test] the _over_mib fixture is not what this file claims"; rc=1
  else
    echo "  [self-test] _over_mib('UNKNOWN', 64) is silently FALSE -- so _is_num is load-bearing -> ok"
  fi

  # --- 🔴 AN UNMEASURABLE DOWNLOAD MUST REFUSE, NOT READ AS ZERO -----------------------
  # The build counts and the fetch counts are held DELIBERATELY BELOW every other limit
  # here, so nothing but the unparseable-size branch can produce a refusal. If this
  # passes, that branch is genuinely the only thing that saw it.
  got=$(_gate_reasons 26.11 26.11 0 3 UNKNOWN 17.6 3 4)
  if printf '%s' "$got" | grep -q 'PENDING DOWNLOAD VOLUME COULD NOT BE MEASURED'; then
    echo "  [self-test] gate REFUSES an UNPARSEABLE pending download size -> ok"
  else
    echo "  [self-test] gate FAILED OPEN on an unparseable pending size. got: '$got'"; rc=1
  fi
  got=$(_gate_reasons 26.11 26.11 0 3 0.0 UNKNOWN 3 4)
  if printf '%s' "$got" | grep -q 'TOTAL DOWNLOAD VOLUME COULD NOT BE MEASURED'; then
    echo "  [self-test] gate REFUSES an UNPARSEABLE total download size -> ok"
  else
    echo "  [self-test] gate FAILED OPEN on an unparseable total size. got: '$got'"; rc=1
  fi

  # --- the FETCH COUNT gates, both directions -----------------------------------------
  # The download SIZES here are 0.0 and the build counts are 3, both far under their
  # limits, so only the fetch gates can speak.
  got=$(_gate_reasons 26.11 26.11 0 3 0.0 17.6 2400 2401)
  if printf '%s' "$got" | grep -q 'PENDING FETCH COUNT'; then
    echo "  [self-test] gate REFUSES 2400 pending paths to fetch -> ok"
  else
    echo "  [self-test] gate FAILED: 2400 pending fetches allowed. got: '$got'"; rc=1
  fi
  got=$(_gate_reasons 26.11 26.11 0 3 0.0 17.6 7 913)
  if printf '%s' "$got" | grep -q 'TOTAL FETCH COUNT'; then
    echo "  [self-test] gate REFUSES 913 total paths to fetch from a small baseline -> ok"
  else
    echo "  [self-test] gate FAILED: 913 total fetches allowed. got: '$got'"; rc=1
  fi
  # 🔴 AND IT MUST ALLOW THE MEASURED REALITY, or the fetch gate is a permanent red light
  # that everyone learns to override. 24 pending / 25 total is what this host reported on
  # 2026-09-07 -- neither number is a round multiple of its limit.
  got=$(_gate_reasons 26.11 26.11 0 7 0.0 17.6 24 25)
  if [ -z "$got" ]; then
    echo "  [self-test] gate ALLOWS the measured 24 pending / 25 total fetched paths -> ok"
  else
    echo "  [self-test] gate FAILED: refused the measured fetch counts: '$got'"; rc=1
  fi

  # --- the ARITY guard ------------------------------------------------------------------
  # A call site that omits an axis must be LOUD. The old signature defaulted the missing
  # ones to 0, which is a gate that cannot fire dressed as a gate that passed.
  if got=$(_gate_reasons 26.11 26.11 0 3 0.0 17.6 2>&1); then
    echo "  [self-test] _gate_reasons FAILED: returned 0 for a 6-argument call"; rc=1
  elif printf '%s' "$got" | grep -q 'needs 8 arguments'; then
    echo "  [self-test] _gate_reasons refuses a 6-argument call by name -> ok"
  else
    echo "  [self-test] _gate_reasons FAILED: wrong error for a short call: '$got'"; rc=1
  fi

  # --- 🔴 THE SEAM: _drybuild_counts -> _gate_reasons, JOINED ---------------------------
  # Everything above drives the parser on well-formed text and the gate on numbers typed
  # in by hand. The defect this replaces lived in NEITHER -- it lived in the join, where
  # the parser's "I could not read this" was spelled `0.0` and the gate read it as "there
  # is nothing to download". So these cases go through BOTH, exactly as the real run
  # does, and the fixtures are the four malformed shapes measured against the old gate.
  local seam_case seam_label seam_text seam_counts seam_gate
  # 🔴 THE POSITIVE CONTROL FIRST. A well-formed world-sized fetch line must REFUSE --
  # otherwise a seam harness that refused everything (or that was wired to nothing and
  # happened to print a refusal) would look identical to a working one.
  printf 'these 2400 paths will be fetched (4096.0 MiB download, 9000.0 MiB unpacked):\n' \
    >"$dir/seam-ok.txt"
  seam_counts=$(_drybuild_counts "$dir/seam-ok.txt")
  IFS='|' read -r _sb _sf _sm <<<"$seam_counts"
  seam_gate=$(_gate_reasons 26.11 26.11 "$_sb" "$_sb" "$_sm" "$_sm" "$_sf" "$_sf")
  if [ -n "$seam_gate" ] && [ "$_sm" = "4096.0" ]; then
    echo "  [self-test] seam control: a well-formed 4096.0 MiB / 2400-path line parses AND refuses -> ok"
  else
    echo "  [self-test] seam control FAILED: counts '$seam_counts', gate '$seam_gate'"; rc=1
  fi
  # And a genuinely EMPTY dry-build must still pass the seam, or the gate is a red light.
  printf 'building the system configuration...\n' >"$dir/seam-zero.txt"
  seam_counts=$(_drybuild_counts "$dir/seam-zero.txt")
  IFS='|' read -r _sb _sf _sm <<<"$seam_counts"
  seam_gate=$(_gate_reasons 26.11 26.11 "$_sb" "$_sb" "$_sm" "$_sm" "$_sf" "$_sf")
  if [ -z "$seam_gate" ] && [ "$seam_counts" = "0|0|0.0" ]; then
    echo "  [self-test] seam control: an EMPTY dry-build is a real 0.0 and is ALLOWED -> ok"
  else
    echo "  [self-test] seam control FAILED on the empty case: counts '$seam_counts', gate '$seam_gate'"; rc=1
  fi
  # Now the four measured malformed shapes. Every one of them returned `0|2400|0.0` and
  # passed all four gates in silence.
  #
  # 🔴 THE EXPECTED SIZE IS PINNED EXACTLY, not merely asserted "not 0.0". A weaker
  # version of this loop was MUTATION-TESTED and a mutant that defaulted an unrecognised
  # unit to MiB -- reading 4 EiB as 4.0 -- SURVIVED it: the value was not 0.0, and the
  # fetch-count gate refused the change for an unrelated reason. The fetch gate catching
  # it is defence in depth, not a reason to leave this guard unable to see the bug it
  # exists for.
  local seam_want
  for seam_case in \
      "no size parenthetical at all|UNKNOWN|these 2400 paths will be fetched:" \
      "a TiB download (understood -- must SCALE, not refuse)|2621440.0|these 2400 paths will be fetched (2.5 TiB download, 9.0 TiB unpacked):" \
      "a comma decimal separator|UNKNOWN|these 2400 paths will be fetched (4096,0 MiB download, 9000,0 MiB unpacked):" \
      "a unit this script has never seen|UNKNOWN|these 2400 paths will be fetched (4.0 EiB download, 9.0 EiB unpacked):"; do
    seam_label="${seam_case%%|*}"
    seam_case="${seam_case#*|}"
    seam_want="${seam_case%%|*}"
    seam_text="${seam_case#*|}"
    printf '%s\n' "$seam_text" >"$dir/seam.txt"
    seam_counts=$(_drybuild_counts "$dir/seam.txt")
    IFS='|' read -r _sb _sf _sm <<<"$seam_counts"
    seam_gate=$(_gate_reasons 26.11 26.11 "$_sb" "$_sb" "$_sm" "$_sm" "$_sf" "$_sf")
    if [ "$_sm" != "$seam_want" ]; then
      echo "  [self-test] seam FAILED: $seam_label sized as '$_sm', want '$seam_want'"; rc=1
    elif [ -z "$seam_gate" ]; then
      echo "  [self-test] seam FAILED: $seam_label passed the gate ($seam_counts)"; rc=1
    else
      echo "  [self-test] seam: $seam_label -> $seam_counts -> REFUSED -> ok"
    fi
  done
  # 🔴 `2.5 TiB` IS PARSEABLE NOW, and it must be scaled, not merely refused: a TiB read
  # as 2.5 MiB is six orders of magnitude of "this is fine".
  printf 'these 3 paths will be fetched (2.5 TiB download, 9.0 TiB unpacked):\n' >"$dir/tib.txt"
  got=$(_drybuild_counts "$dir/tib.txt")
  if [ "$got" = "0|3|2621440.0" ]; then
    echo "  [self-test] 2.5 TiB is scaled to MiB (2621440.0), not read as 2.5 -> ok"
  else
    echo "  [self-test] TiB scaling FAILED: got '$got', want '0|3|2621440.0'"; rc=1
  fi
  # A bare-byte size is likewise a real number, not an UNKNOWN.
  printf 'these 2 paths will be fetched (900000000 B download, 1000000000 B unpacked):\n' >"$dir/bytes.txt"
  got=$(_drybuild_counts "$dir/bytes.txt")
  if [ "$got" = "0|2|858.3" ]; then
    echo "  [self-test] a bare-byte size is scaled to MiB (900000000 B -> 858.3) -> ok"
  else
    echo "  [self-test] byte scaling FAILED: got '$got', want '0|2|858.3'"; rc=1
  fi

  # --- _cfg_tailscale_decls: a MENTION is not a DECLARATION ---------------------------
  # 🔴 The idempotency test used to be `grep -q 'services\.tailscale'`, so the first
  # fixture below made this script print "Nothing to do. Exiting 0" on a host where
  # nothing had been applied. Both directions are driven, and the comment cases are the
  # point -- a checker that only ever sees real declarations cannot fail this way.
  local decl_case name body want n_got
  # Fixture bodies carry a leading marker so the loop can hold them on one line.
  printf '%s\n' '{' '  # TODO: consider services.tailscale one day' '}' >"$dir/c-mention.nix"
  printf '%s\n' '{' '  /* services.tailscale = { enable = true; }; */' '}' >"$dir/c-block.nix"
  printf '%s\n' '{' '  services.tailscale.enable = true;' '}' >"$dir/c-dotted.nix"
  printf '%s\n' '{' '  services.tailscale = {' '    enable = true;' '  };' '}' >"$dir/c-attrset.nix"
  printf '%s\n' '{' '  environment.systemPackages = [ pkgs.tailscale ];' '}' >"$dir/c-pkgonly.nix"
  # 🔴 THE FIXTURE THAT DISCRIMINATES THE `[.={]` ANCHOR. `services.tailscale` in live
  # code that is NOT a setting. Without this case a match on the bare attribute path
  # passes every other fixture here, so the anchor is untested and a config that merely
  # NAMES the option in a warning string reports the host as already done.
  printf '%s\n' '{' '  warnings = [ "services.tailscale is not enabled here" ];' '}' >"$dir/c-string.nix"
  # 🔴 THE FIXTURE THAT DISCRIMINATES STRING-BLANKING FROM THE `[.={]` ANCHOR ALONE. The
  # case above carries no `[.={]` after the attribute path, so it is rejected by the
  # anchor and stays 0 even with every line of string handling deleted -- it cannot see
  # that mutation. These two CAN: each is a string whose CONTENTS are a syntactically
  # perfect declaration, and each reported "Nothing to do. Exiting 0" before the scanner
  # existed.
  printf '%s\n' '{' '  warnings = [ "you should run services.tailscale.enable = true; here" ];' '}' \
    >"$dir/c-instring.nix"
  printf '%s\n' '{' "  text = ''" '    services.tailscale.enable = true;' "  '';" '}' \
    >"$dir/c-indented.nix"
  # ...and the control that keeps the two above from being satisfied by a scanner that
  # blanks the whole file: a real declaration SITTING AFTER a closed string on the line
  # before must still be found.
  printf '%s\n' '{' '  warnings = [ "not enabled" ];' '  services.tailscale.enable = true;' '}' \
    >"$dir/c-afterstring.nix"
  printf '%s\n' '{' '  # services.tailscale = { enable = true; };' '  services.tailscale.enable = true;' '}' >"$dir/c-both.nix"
  for decl_case in "c-mention:0:a bare mention inside a # comment" \
                   "c-block:0:a declaration inside a /* */ block comment" \
                   "c-pkgonly:0:pkgs.tailscale in systemPackages is not a service" \
                   "c-string:0:the option NAMED in a warning string is not a setting" \
                   "c-instring:0:a whole declaration INSIDE a double-quoted string" \
                   "c-indented:0:a whole declaration inside a '' indented string" \
                   "c-afterstring:1:a real declaration on the line after a closed string" \
                   "c-dotted:1:services.tailscale.enable = true" \
                   "c-attrset:1:services.tailscale = { ... }" \
                   "c-both:1:a commented-out copy PLUS a real one"; do
    name="${decl_case%%:*}"; body="${decl_case#*:}"; want="${body%%:*}"; body="${body#*:}"
    n_got=$(_cfg_tailscale_decls "$dir/${name}.nix" | wc -l)
    if [ "$n_got" = "$want" ]; then
      echo "  [self-test] declared? $body -> $n_got -> ok"
    else
      echo "  [self-test] declared? $body FAILED: got $n_got, want $want"; rc=1
    fi
  done
  # The line number must survive the comment blanking, or the "already configured"
  # report points the operator at the wrong line of their own config.
  got=$(_cfg_tailscale_decls "$dir/c-both.nix")
  if [ "$got" = "3:services.tailscale.enable = true;" ]; then
    echo "  [self-test] a declaration keeps its ORIGINAL line number (3) -> ok"
  else
    echo "  [self-test] declaration line number FAILED: got '$got'"; rc=1
  fi
  return $rc
}

_over() { [ "$1" -gt "$2" ]; }

# 🔴 THE MiB FIGURES ARE FRACTIONAL ("159.4") AND `-gt` CANNOT COMPARE THEM -- BUT NOT IN
# THE WAY THIS COMMENT USED TO CLAIM. It said `-gt` "aborts the shell ... under `set -e`".
# MEASURED in the shape actually used here, `if _over "159.4" "64"; then`:
#     $ set -euo pipefail; _over() { [ "$1" -gt "$2" ]; }
#     $ if _over "159.4" "64"; then echo FIRED; else echo SILENT; fi; echo "continued"
#     bash: line 2: [: 159.4: integer expected
#     SILENT
#     continued                          <- and the script exits 0
# `[` prints to stderr and returns 2; because the call is the CONDITION of an `if`,
# errexit does not apply to it, so nothing aborts -- the gate simply does not fire and
# execution carries on. The conclusion (use awk) was right; the stated mechanism was
# backwards, and it matters: a comment describing a LOUD abort where the truth is a
# SILENT fail-open is how the next maintainer decides the guard is redundant and deletes
# it. awk does the comparison in floating point, and `_is_num` below keeps a
# non-numeric string from reaching it at all.
_over_mib() { awk -v a="$1" -v b="$2" 'BEGIN { exit !(a + 0 > b + 0) }'; }

# 🔴 A BARE NON-NEGATIVE DECIMAL, AND NOTHING ELSE. `_over_mib UNKNOWN 64` would evaluate
# `"UNKNOWN" + 0` as 0 in awk and report "not over" -- the fail-open this whole round
# exists to close. Every MiB figure is put through here BEFORE any comparison, and a
# value that fails is REFUSED rather than compared.
_is_num() {
  case "$1" in
    ''|.|*[!0-9.]*) return 1 ;;   # empty, a bare dot, or any character that is not a digit/dot
    *.*.*)          return 1 ;;   # more than one decimal point
  esac
  return 0
}

# THE GATE, as a pure function of the six numbers, so every branch can be driven from
# fixtures. A refusal that has only ever been reasoned about is not a guard; the
# self-test below watches this one both refuse and allow, using the numbers MEASURED on
# this host rather than invented ones.
#
# 🔴 THE DOWNLOAD ARGUMENTS ARE NOT DECORATION. An earlier version took only the two
# release strings and the two BUILD counts, while the summary table printed fetch counts
# and MiB that no gate ever read -- so a pending change that was entirely SUBSTITUTABLE
# (0 derivations to build, thousands of paths, several GiB to download) passed the gate
# untouched. Build count and download volume are independent axes and either can be
# world-sized alone.
#
# 🔴 THE FETCH COUNTS ARE NOT DECORATION EITHER -- THE SAME DEFECT, ONE COLUMN OVER.
# `_drybuild_counts` has always parsed the fetch count correctly and the summary table
# has always printed it, and until now NO GATE READ IT. That is the identical
# "decorative column" shape the download-volume gate was added to fix: a change of 2400
# fetched paths whose size could not be read passed every gate, and the count -- correct,
# parsed, printed -- was sitting right there. Defaults are set from the SAME measurement
# as the rest: 24 pending / 25 total on this host on 2026-09-07, tailscale's own cost
# being +1 fetched path.
#
# 🔴 ALL EIGHT ARGUMENTS ARE REQUIRED. They used to have `${5:-0}`-style defaults, which
# is the shape that lets a call site quietly omit an axis and get a gate that cannot
# fire. A missing argument is now loud.
#
# Prints one reason per paragraph; empty output means "allow".
_gate_reasons() {   # $1 run_rel      $2 cand_rel
                    # $3 pending_builds  $4 total_builds
                    # $5 pending_mib     $6 total_mib      (may be the string UNKNOWN)
                    # $7 pending_fetch   $8 total_fetch
  if [ "$#" -ne 8 ]; then
    printf '%s\n' "INTERNAL ERROR: _gate_reasons needs 8 arguments, got $#. Refusing to
    return a verdict from a gate that was not given every axis it measures." >&2
    return 2
  fi
  local run_rel="$1" cand_rel="$2" b_built="$3" c_built="$4"
  local b_mib="$5" c_mib="$6" b_fetch="$7" c_fetch="$8"
  local d_built=$(( $4 - $3 )) delta_note
  # 🔴 A NEGATIVE DELTA IS A REAL OBSERVED STATE, not an impossible one: the two
  # dry-builds are separate evaluations and the store can gain paths between them, so the
  # candidate can legitimately report FEWER builds than the baseline. Phrased as
  # "Only -3 of the 37 are tailscale's", which is nonsense presented as a measurement.
  if [ "$d_built" -ge 0 ]; then
    delta_note="Only $d_built of the $c_built are tailscale's."
  else
    delta_note="The candidate reports FEWER builds ($c_built) than the current config
    ($b_built), so the two dry-builds disagree and NO delta can be attributed to
    tailscale -- most likely the store gained paths between the two evaluations."
  fi
  if [ "$run_rel" != "$cand_rel" ]; then
    printf '%s\n' "NIXPKGS RELEASE CHANGE: $run_rel -> $cand_rel.
    This is an OS UPGRADE, not a feature. The last time this happened here a 4-line
    nebula edit turned into a 2h16m world rebuild. Refused regardless of size."
  fi
  if _over "$b_built" "$MAX_PENDING_BUILDS"; then
    printf '%s\n' "PENDING WORK UNRELATED TO TAILSCALE: $b_built derivations are already
    queued by the CURRENT config (limit $MAX_PENDING_BUILDS), and \`switch\` applies
    them too. $delta_note
    THE CLEAN FIX IS TO SEPARATE THE TWO OPERATIONS: run \`sudo nixos-rebuild switch\`
    on its own, at a time you choose, watch it land, then re-run this script -- the
    delta will then be just tailscale. Or accept it with --allow-world-rebuild."
  fi
  if _over "$c_built" "$MAX_TOTAL_BUILDS"; then
    printf '%s\n' "TOTAL BUILD SIZE: $c_built derivations would be built (limit
    $MAX_TOTAL_BUILDS). Adding tailscale should be a handful of substituted paths."
  fi
  # 🔴 UNPARSEABLE FIRST, AND IT REFUSES. An unmeasured download is not a small one, and
  # the numeric gates below would silently read it as zero.
  if ! _is_num "$b_mib"; then
    printf '%s\n' "PENDING DOWNLOAD VOLUME COULD NOT BE MEASURED ('$b_mib'). \`nixos-rebuild
    dry-build\` announced paths to fetch for the CURRENT config but printed a size this
    script cannot read -- an unknown unit, a locale that uses a comma decimal separator,
    or no size at all. REFUSED, deliberately: an unmeasured download is not a small one,
    and reporting it as 0 MiB is how a 2400-path substitutable world rebuild walked
    through all four gates in silence. Read the numbers yourself:
      nixos-rebuild dry-build   (its summary goes to STDERR)
    then re-run with --allow-world-rebuild if you are satisfied."
  elif _over_mib "$b_mib" "$MAX_PENDING_MIB"; then
    printf '%s\n' "PENDING DOWNLOAD VOLUME: the CURRENT config already wants to fetch
    $b_mib MiB (limit $MAX_PENDING_MIB MiB), and \`switch\` fetches it too. A change with
    NOTHING to build can still be world-sized -- a fully substitutable channel bump is
    thousands of paths and gigabytes of download at a build count of zero. Same clean
    fix: land the pending change on its own first, or --allow-world-rebuild."
  fi
  if ! _is_num "$c_mib"; then
    printf '%s\n' "TOTAL DOWNLOAD VOLUME COULD NOT BE MEASURED ('$c_mib') for the
    CANDIDATE config. Same reason and same refusal as above: this gate does not treat an
    unreadable size as zero."
  elif _over_mib "$c_mib" "$MAX_TOTAL_MIB"; then
    printf '%s\n' "TOTAL DOWNLOAD VOLUME: $c_mib MiB would be fetched (limit
    $MAX_TOTAL_MIB MiB). Tailscale's own measured cost on this host is ~17.6 MiB."
  fi
  # 🔴 THE FETCH COUNT, WHICH IS THE AXIS THAT STILL HAS A NUMBER WHEN THE SIZE DOES NOT.
  # It is the only gate that sees a fetch line whose size is unreadable AND whose volume
  # therefore cannot be compared -- so it is not redundant with the two above, it is the
  # backstop for exactly their blind spot.
  if _over "$b_fetch" "$MAX_PENDING_FETCH"; then
    printf '%s\n' "PENDING FETCH COUNT: the CURRENT config already wants to fetch
    $b_fetch paths (limit $MAX_PENDING_FETCH), and \`switch\` fetches them too. Measured
    on this host, a normal pending queue is ~24 paths and tailscale's own cost is +1.
    Thousands of paths is a channel bump wearing a feature's clothes, whatever the
    download size says -- or fails to say. Land the pending change on its own first, or
    --allow-world-rebuild."
  fi
  if _over "$c_fetch" "$MAX_TOTAL_FETCH"; then
    printf '%s\n' "TOTAL FETCH COUNT: $c_fetch paths would be fetched (limit
    $MAX_TOTAL_FETCH). Adding tailscale measured 25 fetched paths in total on this host."
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
ts_decls=$(_cfg_tailscale_decls "$CFG") \
  || die "could not read $CFG to decide whether tailscale is already declared"
if [ -n "$ts_decls" ]; then
  echo "  state     : $CFG already DECLARES services.tailscale:"
  printf '%s\n' "$ts_decls" | sed 's/^/    | /'
  echo
  echo "Nothing to do. Exiting 0 without touching $CFG."
  echo "Whether it is actually WORKING is a different question -- ask the verifier:"
  echo "    bash ${CHECK} --role ${ROLE}"
  exit 0
fi
# A MENTION is not a declaration, and saying so out loud matters: the operator who wrote
# that comment is exactly the one who might read a bare "proceeding" as "it ignored my
# config". This branch is the reason `_cfg_tailscale_decls` strips comments at all.
if grep -q 'services\.tailscale' "$CFG"; then
  echo "  state     : $CFG MENTIONS services.tailscale but does not DECLARE it --"
  grep -n 'services\.tailscale' "$CFG" | sed 's/^/    | /'
  echo "              every occurrence is inside a comment, so nothing is applied."
  echo "              Proceeding to add the real block."
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
SWITCH_ATTEMPTED=0
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
    # 🔴 THREE STATES, NOT TWO, AND ONLY ONE OF THEM IS ESTABLISHED. This used to branch
    # on SWITCHED and assert, when it was 0, "The system was never switched, so nothing
    # is running the change." That is a claim about the RUNNING system derived from a
    # flag that only records whether `nixos-rebuild switch` RETURNED ZERO. A switch can
    # exit non-zero AFTER activating -- a unit that fails to start is the ordinary case
    # -- and it writes the bootloader entry before that, so the assertion is false in
    # exactly the situation it gets printed in. State what is known.
    if [ "$SWITCHED" = "1" ]; then
      echo "🔴 The system had ALREADY been switched. The FILE is restored but the RUNNING" >&2
      echo "   system is not -- run \`sudo nixos-rebuild switch\` to return it." >&2
    elif [ "$SWITCH_ATTEMPTED" = "1" ]; then
      echo "🔴 \`nixos-rebuild switch\` WAS STARTED and did not complete. Whether the" >&2
      echo "   RUNNING system changed is NOT established by this script: a switch can" >&2
      echo "   activate and still exit non-zero (e.g. a unit fails to start), and it" >&2
      echo "   writes the bootloader entry before that. The FILE is restored; the" >&2
      echo "   running system may or may not carry the change. Check, then decide:" >&2
      echo "       readlink -f /run/current-system" >&2
      echo "       sudo nixos-rebuild list-generations | tail -3" >&2
      echo "   \`sudo nixos-rebuild switch\` returns the running system to $CFG." >&2
    else
      echo "   \`nixos-rebuild switch\` was never started, so nothing activated." >&2
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
  # 🔴 ONE SHARED COMPONENT DOES EXIST, AND IT IS NOT SYMMETRIC: the AirVPN killswitch.
  # \`scripts/airvpn-updown\`'s degraded and fallback rulesets allow egress on a LITERAL
  # interface list -- lo, the airvpn tun, nebula.mesh, cni0, flannel.1, docker0 -- and
  # then \`drop\`. \`tailscale0\` is not on it, and unlike nebula (three carve-outs)
  # tailscale has no DERP/control-plane bypass. So if that killswitch ever arms
  # fail-closed, NEBULA SURVIVES AND TAILSCALE DIES -- the inverse of the redundancy this
  # block is for. AirVPN is default-OFF on these hosts, so it is a latent interaction,
  # not a live defect; it is NOT fixed here because widening a killswitch is its own
  # change with its own review.
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
  #
  # 🔴 ONE SHARED COMPONENT DOES EXIST, AND IT IS NOT SYMMETRIC: the AirVPN killswitch.
  # \`scripts/airvpn-updown\`'s degraded and fallback rulesets allow egress on a LITERAL
  # interface list -- lo, the airvpn tun, nebula.mesh, cni0, flannel.1, docker0 -- and
  # then \`drop\`. \`tailscale0\` is not on it, and unlike nebula (three carve-outs)
  # tailscale has no DERP/control-plane bypass. So if that killswitch ever arms
  # fail-closed, NEBULA SURVIVES AND TAILSCALE DIES -- the inverse of the redundancy this
  # block is for. AirVPN is default-OFF on these hosts, so it is a latent interaction,
  # not a live defect; it is NOT fixed here because widening a killswitch is its own
  # change with its own review.
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

# 🔴 `awk END{print NR}`, NOT `wc -l`. `wc -l` counts NEWLINES, so a config whose last
# line has no trailing newline is undercounted by one -- while awk's output always ends
# with one. The difference then came out as "expected the patch to add exactly 32 lines,
# it added 33", a message naming the wrong problem entirely and sending the operator to
# look for a bug in the block. awk counts RECORDS, and a final line without a newline is
# still a record, so both sides are measured the same way.
added=$(( $(awk 'END{print NR}' "$TMP") - $(awk 'END{print NR}' "$CFG") ))
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
# 🔴 SAY WHAT THE RELEASE GATE CANNOT SEE, RIGHT WHERE IT REPORTS. `_release_of` reduces
# to major.minor, so this host's pending `26.11pre1066106` -> `26.11pre1066425` prints
# `26.11 -> 26.11` and the release gate does NOT fire on it. A bare matching pair reads
# as "nothing is moving"; it means "nothing is moving THAT THIS GATE MEASURES". The
# build-count and download-volume gates are what cover a same-release channel tick.
if [ "$run_rel" = "$cand_rel" ] && [ "$running_version" != "$cand_version" ]; then
  echo "              same RELEASE, but the revisions DIFFER:"
  echo "                $running_version"
  echo "                $cand_version"
  echo "              The release gate compares major.minor only, so it does NOT fire on"
  echo "              this. The build-count and download gates are what cover it."
fi
echo

# --- the gate ---------------------------------------------------------------------------
# One implementation, exercised in both directions by --self-test above.
refuse=$(_gate_reasons "$run_rel" "$cand_rel" "$b_built" "$c_built" \
                       "$b_mib" "$c_mib" "$b_fetch" "$c_fetch")

if [ -n "$refuse" ] && [ "$ALLOW_WORLD" != "1" ]; then
  echo "REFUSING to switch:" >&2
  printf '  - %s\n' "$refuse" >&2
  echo >&2
  echo "  $CFG was NOT modified. Nothing to roll back." >&2
  # 🔴 `|| true` is load-bearing, twice over: `grep` exits 1 when it matches nothing,
  # and `head` closing the pipe early SIGPIPEs everything upstream. With `set -e` plus
  # `pipefail` either would abort HERE -- inside the refusal path, one line before
  # `exit 4` -- so the script would exit 1 and never print the override the operator
  # needs. A cosmetic listing must not be able to change the exit code.
  #
  # 🔴 THIS LISTING IS THE ONLY DIAGNOSTIC FOR THE --allow-world-rebuild DECISION, AND IT
  # USED TO HIDE THE ANSWER. It was headed "Largest pending items" and cut at `head -15`
  # of an ALPHABETICAL list -- so on the real 38-derivation queue on this host everything
  # from `s` onward was invisible, including every `steam-*` derivation, the only heavy
  # builds present; `wine-wow-11.0`, the culprit in the 2h16m incident this whole script
  # exists because of, sorts last of all. "Largest" was also a false claim: nothing here
  # is sorted by size, and a .drv name carries no size. So: it is named for what it is,
  # it shows EVERY item up to a generous cap, and when it does cap it says how many were
  # omitted rather than trailing off.
  drvnames=$( { grep -oE '/nix/store/[a-z0-9]+-[^ ]+\.drv' "$WORK/base.err" \
      | sed -e 's#.*/[a-z0-9]*-##' -e 's/\.drv$//' | sort -u; } || true )
  n_drv=0
  [ -z "$drvnames" ] || n_drv=$(printf '%s\n' "$drvnames" | wc -l)
  echo "  All $n_drv pending derivations, so you can judge the cost yourself" >&2
  echo "  (alphabetical -- this is a NAME list, not a size ranking):" >&2
  if [ "$n_drv" -gt "$LIST_MAX" ]; then
    printf '%s\n' "$drvnames" | head -n "$LIST_MAX" | sed 's/^/      /' >&2 || true
    echo "      ... and $(( n_drv - LIST_MAX )) more NOT shown (alphabetically after the" >&2
    echo "      last line above -- the heavy ones may well be among them). Raise the cap" >&2
    echo "      with TS_LIST_MAX=$n_drv to see every one." >&2
  elif [ "$n_drv" -gt 0 ]; then
    printf '%s\n' "$drvnames" | sed 's/^/      /' >&2
  else
    echo "      (none -- the refusal above is about downloads or the release string," >&2
    echo "       not about derivations to build)" >&2
  fi
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
# 🔴 PATCHED IS SET **BEFORE** THE COPY, AND THE ORDER IS THE WHOLE POINT. `cp` is not
# atomic -- it truncates the destination and then writes -- so a failure PART WAY
# THROUGH (ENOSPC, an I/O error; `/` on this host sits at 77%) leaves $CFG half
# overwritten. With the flag set after the copy, `set -e` would abort with PATCHED=0,
# the trap's rollback branch would be skipped, and the operator would be left with a
# corrupt configuration.nix, an unused good backup sitting beside it, and NOTHING
# PRINTED. Setting it first costs nothing in the other direction: a `cp` that fails
# before writing a single byte simply restores a byte-identical file.
# The flag therefore means "$CFG MAY have been modified", not "it was".
PATCHED=1
cp -p "$TMP" "$CFG"
echo "  applied   : $CFG"
echo

echo "== nixos-rebuild switch =="
# 🔴 THE STATUS IS CAPTURED, NOT ASSUMED. This rebuild can run for hours, which is
# exactly when it gets interrupted or killed, and the line after it used to be a bare
# `SWITCHED=1` -- a variable that says "the switch succeeded" set without anyone having
# asked whether it did.
#
# MEASURED on bash 5.3.15 here, with the signal confirmed to have landed (the killed
# child's own wall time is the control -- a `kill` that returns 0 against a process with
# SIGINT ignored looks identical to "the shell continued"):
#   * SIGINT, to the child alone AND to the whole process group (the tty Ctrl-C shape):
#     bash propagates the child's SIGINT death and TERMINATES the script at this line,
#     exit 130. The EXIT trap fires and rolls back. So the Ctrl-C case never reaches the
#     check below -- it is already handled, and the claim that a SIGINT-killed child
#     silently continues did NOT reproduce.
#   * SIGTERM and SIGHUP: no such propagation. The child exits 143 / 129 and execution
#     CONTINUES here. Under the old `nixos-rebuild switch` + `SWITCHED=1` those exits
#     did trip errexit, but they aborted the script with no statement of what happened;
#     with the capture the operator is told which signal killed the rebuild.
# So the check below is reachable and it is the SIGTERM/SIGHUP/SIGKILL path -- driven
# with `kill -TERM` and `kill -HUP` against the real script, both watched to print the
# message and roll back.
#
# `rc=0` FIRST, then `cmd || rc=$?`: `cmd; rc=$?` is dead code under `set -e`, and
# `cmd || rc=$?` without the seed dies under `set -u` on the SUCCESS path.
switch_rc=0
SWITCH_ATTEMPTED=1
nixos-rebuild switch || switch_rc=$?
if [ "$switch_rc" -ge 128 ]; then
  die "\`nixos-rebuild switch\` was KILLED BY SIGNAL $(( switch_rc - 128 )) (exit
  $switch_rc). It did NOT complete. Signal 15 is a \`kill\`/timeout, 9 an out-of-memory
  or hard kill, 1 a lost terminal; a Ctrl-C (signal 2) normally kills this script
  outright instead of arriving here. Whether the switch ACTIVATED before it died is not
  established -- see the rollback note below."
elif [ "$switch_rc" != "0" ]; then
  die "\`nixos-rebuild switch\` exited $switch_rc. It did not complete successfully.
  Note that a non-zero exit does NOT mean nothing was activated -- see the rollback note
  below. Its own output is above."
fi
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

# =====================================================================================
# 🔴 WHAT COUNTS AS SUCCESS -- and the assumption that used to make the FIRST RUN ROLL
# BACK, EVERY TIME, GUARANTEED.
#
# This block used to accept rc 0/2/3 and treat everything else as a node-side failure,
# on the stated premise that "rc 3 is the EXPECTED state straight after a switch". That
# premise was false, and this script is the reason: it deliberately does NOT run
# `tailscale up` (see the next steps below), so straight after a switch the node has
# never authenticated -- BackendState `NeedsLogin`, `Online` false, no TailscaleIPs,
# nothing advertised. Under the old verifier that was four separate FAIL entries and rc
# **1**, not 3, so `die` fired, the EXIT trap restored the backup, and the run ended
# "ABORT ... ROLLED BACK" having deleted the very thing it had just installed.
#
# So the states are now enumerated with what each one MEANS, and the verifier reports
# "configured but not yet authenticated" as its own code rather than as a defect.
# Rollback is for a GENUINE failure only.
#
# `bash "$CHECK"`, not `"$CHECK"`: the file is guaranteed READABLE by the preflight
# (`[ -r "$CHECK" ]`), not executable -- a checkout copied without its mode bits, or
# read off a noexec mount, would fail with "permission denied" here, one line after a
# successful switch, and be rolled back for it. Running it under an explicit interpreter
# makes `-r` the right precondition and makes it sufficient.
chk_rc=0
bash "$CHECK" --role "$ROLE" || chk_rc=$?
case "$chk_rc" in
  0)
    echo "  verifier  : rc 0 -- PASS. Authenticated, approved, key expiry disabled."
    echo "              Nothing outstanding; the steps below are already done."
    ;;
  4)
    # 🔴 NAME WHAT WAS OBSERVED, NOT ONE CAUSE OUT OF SEVERAL. This used to read "the
    # switch succeeded and the node is NOT YET AUTHENTICATED" -- an assertion about the
    # node's identity, made by a script that never looked at it, for an exit code with
    # more than one cause. It is reachable two lines below the verifier printing
    # `PASS authed`, on an authenticated node whose prefs could not be read. On a first
    # run the not-yet-authenticated reading is almost always right, which is exactly why
    # it is worth not asserting: the run where it is wrong is the run that matters.
    echo "  verifier  : rc 4 -- INCOMPLETE, and NO DEFECT WAS FOUND. One or more of the"
    echo "              verifier's claims could not be evaluated; the reasons are listed"
    echo "              under 'NOT YET DETERMINABLE' in its own output immediately above,"
    echo "              and that output is the authority on which ones. On a first run"
    echo "              that is normally 'this node has never authenticated', because"
    echo "              this script does not run \`tailscale up\` (it needs a browser) --"
    echo "              but rc 4 also covers unreadable prefs on a node that IS"
    echo "              authenticated. KEEPING the config either way: an unevaluated"
    echo "              claim is not a defect. Note that a node which HAD an identity and"
    echo "              LOST it -- an expired or revoked key -- is rc 1, not this."
    ;;
  3)
    echo "  verifier  : rc 3 -- the node side is correct; an ADMIN-CONSOLE action is"
    echo "              outstanding (route approval and/or key expiry). KEEPING the"
    echo "              config -- a browser tab nobody has clicked is not a defect."
    ;;
  2)
    echo "  verifier  : 🔴 rc 2 -- CANNOT DETERMINE. The switch succeeded and tailscaled" >&2
    echo "              is active, but the verifier could not read the daemon's state," >&2
    echo "              so this run has NOT established that the path works. The config" >&2
    echo "              is KEPT (nothing was shown to be wrong with it). Re-run the" >&2
    echo "              verifier by hand and read its reason:" >&2
    echo "                  bash ${CHECK} --role ${ROLE}" >&2
    ;;
  1)
    die "the verifier reports a definitive node-side FAILURE (rc 1) after the switch.
  That is NOT the not-yet-authenticated state -- rc 4 is, and this is not it. Something
  the node itself controls is wrong: forwarding off, no LAN route, a node that HAS a
  tailnet identity and is nevertheless not Running/Online/advertising, or a node that HAD
  one and has LOST it (an expired or revoked node key). Its output is immediately above
  and names which. Rolling back."
    ;;
  *)
    die "the verifier exited $chk_rc, which is not one of its documented codes
  (0 pass, 1 node-side fail, 2 cannot determine, 3 admin action, 4 not yet
  authenticated). An unrecognised code is not evidence of success. Rolling back."
    ;;
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
else
  echo "       sudo tailscale up --accept-routes --accept-dns=false"
fi
# 🔴 `--accept-dns=false` ON BOTH ROLES. The reasoning is identical on the two machines
# -- MagicDNS makes tailscale take over the host resolver, and on this fleet dnsmasq and
# the `.lan` names already own it -- so giving the server the flag and not the client
# would hand the resolver of the ONE machine the operator travels with to tailscale,
# months from anyone who could fix it. The cost is real and is stated rather than hidden.
echo "   (--accept-dns=false keeps tailscale out of this host's resolver, which dnsmasq"
echo "    and the .lan names already own. It applies to BOTH hosts for the same reason."
echo "    THE COST: MagicDNS names will not resolve here, so address the other machine by"
echo "    its tailnet address -- \`tailscale status\` prints it. Drop the flag on a host"
echo "    where you would rather have MagicDNS than keep its current resolver.)"
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
echo "       bash ${CHECK} --role ${ROLE}"
echo "         0 = done   3 = a step above is outstanding"
echo "         4 = a claim could not be evaluated and no defect was found"
echo "             (normally: step 1 has not been done yet)"
echo "         1 = a node-side defect -- INCLUDING an EXPIRED or REVOKED node key,"
echo "             which is what step 3 exists to prevent   2 = it could not read the daemon"
