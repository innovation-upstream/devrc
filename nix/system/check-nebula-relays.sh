#!/usr/bin/env bash
# Read-only check: does this host ADVERTISE a nebula relay?
#
# `relay.relays` in a nebula config is the list of mesh IPs that PEERS MAY USE TO
# RELAY PACKETS TO THIS HOST. It is NOT the list of relays this host uses to reach
# others -- that distinction is why `use_relays: true` on its own changes nothing.
# An empty `relays: []` means no peer can relay here at all, so a peer that cannot
# hole-punch (CGNAT, symmetric NAT, off-LAN) simply never connects.
#
# On NixOS the list comes from `services.nebula.networks.<net>.settings.relay.relays`
# in /etc/nixos/configuration.nix. This script does NOT edit that file and does NOT
# rebuild -- it needs no sudo and can be run before and after a change.
#
#   bash nix/system/check-nebula-relays.sh                  # expect the default relay
#   bash nix/system/check-nebula-relays.sh 10.42.0.7        # expect a specific one
#   NEBULA_NET=other bash nix/system/check-nebula-relays.sh # a network other than `mesh`
#   bash nix/system/check-nebula-relays.sh --self-test      # validate the parser only
#
# 🔴 IT READS THE RUNNING PROCESS, NOT THE UNIT FILE. The config is taken from
# /proc/<MainPID>/cmdline -- what the live nebula actually loaded. `systemctl cat`
# alone is NOT sufficient and the earlier version of this script was wrong to use it:
# it exits 0 for a unit that is dead or was never restarted, so a config edit that
# built but never took effect reads as applied. The unit's own ExecStart is read too,
# and a disagreement between the two is reported as its own outcome.
#
# Exit: 0 = the expected relay is advertised BY THE RUNNING PROCESS
#       1 = it is not (a definitive answer about a running, readable config)
#       2 = cannot determine -- unit down, no config argument, unit/process disagree,
#           parser failure, or the parser failed its own controls
#
# Every rc-2 path also prints ONE machine-readable line to stderr:
#
#       REASON: <token>
#
# with token one of: unit-not-loaded · unit-inactive · no-mainpid · no-config-arg ·
# unit-process-disagree · self-test-failed · parse-failed. It exists because rc 2
# alone lumps together outcomes that call for DIFFERENT responses, and a caller
# that wants to distinguish them must not have to pattern-match English prose.
# apply-nebula-relay.sh branches on `unit-process-disagree` and only that one.
#
# ⚠ Nothing here restarts anything -- but note for the caller: making a relay change
# take effect requires restarting nebula@<net>, which drops every mesh session for a
# few seconds. If you are connected to this host OVER the mesh, you will be
# disconnected mid-command.
#
# 🔴 Do NOT verify this by grepping the rendered YAML for the relay's address. The
# lighthouse list (`lighthouse.hosts`) contains the SAME mesh addresses, so a pattern
# like `^\s+- 10\.42\.0\.2$` matches a config whose `relays:` is EMPTY -- measured; it
# reported a match on the pre-change file and would have certified a change that had
# not happened. Read the `relay.relays` key, which is what this script does.
set -euo pipefail

NET="${NEBULA_NET:-mesh}"
EXPECT="${1:-10.42.0.2}"          # prod lighthouse mesh IP; its public underlay is
                                  # deliberately NOT in this file -- see the header of
                                  # apply-nebula-443.sh, this repo is PUBLIC.
[ "${EXPECT}" = "--self-test" ] && EXPECT="10.42.0.2"

# --- extracting the -config argument ----------------------------------------------
# One implementation, fed from two sources (the process's NUL-separated cmdline and
# the unit's space-separated ExecStart line), so the two can never drift apart.
# Go's flag package accepts `-config X`, `--config X`, `-config=X` and `--config=X`;
# a previous version matched only the first and silently fell back to a DIFFERENT
# ExecStart line when a drop-in used another spelling.
_config_from_lines() {            # newline-separated tokens on stdin -> the path, or nothing
  awk '
    take == 1 { print; exit }
    /^--?config=/ { sub(/^--?config=/, ""); print; exit }
    /^--?config$/ { take = 1 }
  '
}

# --- the parser -------------------------------------------------------------------
# Stdlib only, on purpose: PyYAML is NOT importable from the python3 on the default
# PATH here, so an `import yaml` version of this cannot run as documented.
#
# 🔴 BOTH styles occur and both branches are load-bearing. `pkgs.formats.yaml` is
# yaml_1_1 via remarshal, which renders an EMPTY list as the FLOW scalar `relays: []`
# and a populated one as a block sequence -- so `relays: []`, the exact state this
# script exists to detect, arrives in flow style. A one-line flow MAPPING
# (`relay: {am_relay: false, relays: [...], use_relays: true}`) is handled too.
# Do not "simplify" either branch away.
#
# Exits 2 when there is no top-level `relay:` key at all, so "the parser found
# nothing" can never be reported as "the relay list is empty".
_parse_relay() {  # $1 = rendered config path; prints "am_relay|use_relays|csv-of-relays"
  python3 - "$1" <<'PY'
import re, sys

try:
    lines = open(sys.argv[1], encoding="utf-8", errors="strict").read().splitlines()
except Exception as e:                      # unreadable, or not UTF-8
    sys.stderr.write("parse: %s\n" % e)
    sys.exit(2)

def split_flow(s):
    return [x.strip().strip("'\"") for x in s.split(",") if x.strip()]

am = use = ""
relays = []
found = False

# (a) one-line flow mapping:  relay: {am_relay: false, relays: [a, b], use_relays: true}
for line in lines:
    m = re.match(r"^relay:\s*\{(.*)\}\s*$", line)
    if m:
        found = True
        body = m.group(1)
        lm = re.search(r"relays:\s*\[([^\]]*)\]", body)
        if lm:
            relays = split_flow(lm.group(1))
        body_wo = re.sub(r"relays:\s*\[[^\]]*\]", "", body)
        for part in body_wo.split(","):
            k, _, v = part.partition(":")
            if k.strip() == "am_relay":
                am = v.strip()
            elif k.strip() == "use_relays":
                use = v.strip()
        break

# (b) block mapping:  relay:\n  am_relay: false\n  relays: ...\n
if not found:
    block, in_block = [], False
    for line in lines:
        if re.match(r"^relay:\s*$", line):
            in_block = True
            found = True
            continue
        if in_block:
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            if not line.startswith((" ", "\t")):   # next top-level key ends the block
                break
            block.append(line)

    in_relays = False
    for line in block:
        s = line.strip()
        if in_relays:
            if s.startswith("- "):
                relays.append(s[2:].strip().strip("'\""))
                continue
            in_relays = False
        if s.startswith("am_relay:"):
            am = s.split(":", 1)[1].strip()
        elif s.startswith("use_relays:"):
            use = s.split(":", 1)[1].strip()
        elif s.startswith("relays:"):
            rest = s.split(":", 1)[1].strip()
            if rest in ("", "~", "null"):
                in_relays = True                      # block sequence follows
            elif rest.startswith("["):                # flow list, incl. the empty []
                relays += split_flow(rest[1:-1])

if not found:
    sys.stderr.write("parse: no top-level `relay:` key in %s\n" % sys.argv[1])
    sys.exit(2)

print("%s|%s|%s" % (am or "?", use or "?", ",".join(relays)))
PY
}

# --- self-test: prove the parser can say BOTH answers before trusting either -------
# A checker that cannot go red is not evidence, and one that has never gone green may
# simply be wired to nothing. The fixtures are real rendered-config shapes; the
# lighthouse list deliberately carries the same addresses as the relay list, which is
# the trap the header warns about.
_self_test() {
  local dir rc=0 got
  dir=$(mktemp -d)
  trap 'rm -rf "$dir"' RETURN

  _t() {  # $1 = name, $2 = expected "am|use|csv", $3 = file
    local g
    if ! g=$(_parse_relay "$3" 2>/dev/null); then
      echo "  [self-test] $1: parser exited non-zero"; rc=1; return
    fi
    if [ "$g" = "$2" ]; then
      echo "  [self-test] $1 -> ok"
    else
      echo "  [self-test] $1 FAILED: got '$g', want '$2'"; rc=1
    fi
  }

  local head='lighthouse:
  am_lighthouse: false
  hosts:
  - 10.42.0.1
  - 10.42.0.2
'
  # 1. the live shape: an EMPTY list, which remarshal renders in FLOW style
  printf '%srelay:\n  am_relay: false\n  relays: []\n  use_relays: true\n' "$head" >"$dir/1.yml"
  _t "empty flow list (the state we exist to detect)" "false|true|" "$dir/1.yml"

  # 2. a populated BLOCK sequence
  printf '%srelay:\n  am_relay: false\n  relays:\n  - 10.42.0.2\n  use_relays: true\n' "$head" >"$dir/2.yml"
  _t "block sequence, one entry                    " "false|true|10.42.0.2" "$dir/2.yml"

  # 3. a populated FLOW list -- kills a mutant that ignores flow-list CONTENTS while
  #    still handling `[]`, which fixture 1 alone cannot see
  printf '%srelay:\n  am_relay: false\n  relays: [10.42.0.2, 10.42.0.3]\n  use_relays: true\n' "$head" >"$dir/3.yml"
  _t "flow list with two entries                   " "false|true|10.42.0.2,10.42.0.3" "$dir/3.yml"

  # 4. a one-line flow MAPPING
  printf '%srelay: {am_relay: false, relays: [10.42.0.2], use_relays: true}\n' "$head" >"$dir/4.yml"
  _t "one-line flow mapping                        " "false|true|10.42.0.2" "$dir/4.yml"

  # 5. A REAL following block: `static_host_map` after `relay`, carrying its own
  #    `- ...` sequence. 🔴 THIS FIXTURE DOES NOT KILL THE TERMINATOR MUTANT and its
  #    comment used to say it did. Measured: replace the terminator
  #    `if not line.startswith((" ", "\t")):` with `if False:` and this fixture still
  #    passes -- the swallowed lines are a `10.42.0.2:` key and a `- ` item, and by
  #    then `in_relays` is False (`relays: []` is a flow list), so neither is read.
  #    It is kept because it documents the real rendered shape, not as coverage.
  printf '%srelay:\n  am_relay: false\n  relays: []\n  use_relays: true\nstatic_host_map:\n  10.42.0.2:\n  - 198.51.100.7:4242\n' "$head" >"$dir/5.yml"
  _t "following block with its own list (shape only)" "false|true|" "$dir/5.yml"

  # 5b. TERMINATOR, for real. A later TOP-LEVEL key the relay parser also reads
  #     (`am_relay`). With the terminator present the block stops at
  #     `static_host_map:` and am stays `false`; without it the scan runs on and the
  #     trailing `am_relay: true` overwrites the real value, so the mutant above dies
  #     here with THIS fixture's own message (got 'true|true|', want 'false|true|').
  #
  # 🔴 BE CLEAR WHAT THIS IS. `pkgs.formats.yaml` cannot emit a duplicate top-level
  #     `am_relay:` -- it renders one mapping. So this fixture pins the PARSER's
  #     structural correctness (the relay block must end where YAML says it ends), NOT
  #     a config shape production can produce. It is a unit test of the terminator,
  #     and claiming more than that is what the previous comment did wrong.
  printf '%srelay:\n  am_relay: false\n  relays: []\n  use_relays: true\nstatic_host_map:\n  10.42.0.2:\n  - 198.51.100.7:4242\nam_relay: true\n' "$head" >"$dir/5b.yml"
  _t "terminator: later top-level am_relay is OUTSIDE" "false|true|" "$dir/5b.yml"

  # 6. no relay key at all -> must be rc 2, NOT an empty list
  printf '%stun:\n  dev: nebula.mesh\n' "$head" >"$dir/6.yml"
  if _parse_relay "$dir/6.yml" >/dev/null 2>&1; then
    echo "  [self-test] absent relay: key FAILED: parser answered instead of exiting 2"; rc=1
  else
    echo "  [self-test] absent relay: key -> rc 2, not a false 'empty' -> ok"
  fi

  # 7. the -config extractor, all four spellings Go's flag package accepts
  got=$(printf '%s\n' "-config" "/a.yml" | _config_from_lines)
  [ "$got" = "/a.yml" ] || { echo "  [self-test] -config X FAILED: '$got'"; rc=1; }
  got=$(printf '%s\n' "--config" "/b.yml" | _config_from_lines)
  [ "$got" = "/b.yml" ] || { echo "  [self-test] --config X FAILED: '$got'"; rc=1; }
  got=$(printf '%s\n' "-config=/c.yml" | _config_from_lines)
  [ "$got" = "/c.yml" ] || { echo "  [self-test] -config=X FAILED: '$got'"; rc=1; }
  got=$(printf '%s\n' "--config=/d.yml" | _config_from_lines)
  [ "$got" = "/d.yml" ] || { echo "  [self-test] --config=X FAILED: '$got'"; rc=1; }
  got=$(printf '%s\n' "-verbose" "/e.yml" | _config_from_lines)
  [ -z "$got" ] || { echo "  [self-test] no-config-arg FAILED: '$got'"; rc=1; }
  [ "$rc" = "0" ] && echo "  [self-test] -config extractor: all 4 spellings + absent -> ok"

  # 8. the trap itself: the naive grep matches the EMPTY fixture, via lighthouse.hosts
  if grep -qE '^[[:space:]]+- 10\.42\.0\.2$' "$dir/1.yml"; then
    echo "  [self-test] the naive grep matches a config with relays: [] -> trap confirmed"
  else
    echo "  [self-test] the naive grep did NOT match the empty fixture; this file's header no longer describes reality"; rc=1
  fi
  return $rc
}

if [ "${1:-}" = "--self-test" ]; then
  echo "parser self-test:"
  _self_test || exit 2          # 2, not 1: 1 means "relay absent", a different claim
  exit 0
fi

# --- what the RUNNING process loaded ----------------------------------------------
unit="nebula@${NET}.service"

# Every rc-2 exit goes through this, so the REASON line can never be forgotten on a
# new path: one writer, not seven copies of an echo.
cannot_determine() {   # $1 = token, rest = human lines
  local token="$1"; shift
  local l
  for l in "$@"; do echo "$l" >&2; done
  echo "REASON: $token" >&2
  exit 2
}

if ! systemctl cat "$unit" >/dev/null 2>&1; then
  cannot_determine unit-not-loaded "CANNOT DETERMINE: $unit is not loaded on this host"
fi
if ! systemctl is-active -q "$unit"; then
  cannot_determine unit-inactive \
    "CANNOT DETERMINE: $unit is '$(systemctl is-active "$unit" || true)', not active." \
    "  A relay is only advertised while nebula is running. Start it, then re-run."
fi
pid=$(systemctl show "$unit" -p MainPID --value)
if [ -z "$pid" ] || [ "$pid" = "0" ] || [ ! -r "/proc/$pid/cmdline" ]; then
  cannot_determine no-mainpid "CANNOT DETERMINE: no readable MainPID for $unit (got '${pid:-<empty>}')"
fi

running=$(tr '\0' '\n' < "/proc/$pid/cmdline" | _config_from_lines)
if [ -z "$running" ] || [ ! -r "$running" ]; then
  cannot_determine no-config-arg "CANNOT DETERMINE: no readable -config argument on pid $pid (got '${running:-<empty>}')"
fi

# The unit's own ExecStart, for the deferred-restart case below. Last ExecStart= line
# wins: systemctl cat prints the base unit first and drop-ins after.
onunit=$(systemctl cat "$unit" | grep '^ExecStart=' | tail -1 | tr ' ' '\n' | _config_from_lines || true)

echo "unit    : $unit (active, pid $pid)"
echo "running : $running"

if [ -n "$onunit" ] && [ "$onunit" != "$running" ]; then
  cannot_determine unit-process-disagree \
    "on-unit : $onunit" \
    "CANNOT DETERMINE: the unit file and the running process disagree." \
    "  A rebuild has landed a new config but nebula has not restarted onto it." \
    "  \`sudo systemctl restart $unit\`, then re-run. 🔴 That drops every mesh session" \
    "  for a few seconds -- if you are connected OVER the mesh you will be cut off."
fi

echo "parser self-test:"
if ! _self_test; then
  cannot_determine self-test-failed \
    "CANNOT DETERMINE: the parser failed its own controls -- its verdict would mean nothing"
fi

if ! parsed=$(_parse_relay "$running"); then
  cannot_determine parse-failed "CANNOT DETERMINE: could not parse a relay block out of $running"
fi
IFS='|' read -r am use relays <<<"$parsed"
echo "relay   : am_relay=$am use_relays=$use relays=[${relays}]"

case ",${relays}," in
  *,"${EXPECT}",*)
    echo "PASS: the running $unit advertises ${EXPECT} as a relay -- peers that cannot hole-punch can reach it"
    exit 0
    ;;
esac

if [ -z "$relays" ]; then
  echo "FAIL: relays is EMPTY -- NO peer can relay to this host."
else
  echo "FAIL: relays does not include ${EXPECT} (advertised: ${relays})."
fi
cat <<EOF

To fix, add this to services.nebula.networks.${NET}.settings in /etc/nixos/configuration.nix
(alongside the existing \`punchy = { ... };\`), then \`sudo nixos-rebuild switch\`:

      relay = {
        use_relays = true;
        relays = [ "${EXPECT}" ];
      };

or run \`sudo bash nix/system/apply-nebula-relay.sh\`, which does exactly that.

Anything listed must carry \`am_relay: true\` in ITS own config, and this host must be
able to reach it. 🔴 Relayed traffic EGRESSES THE RELAY in both directions, so choosing
a billed host (a cloud lighthouse) puts every relayed byte on that bill. For peers that
only ever fall back on-LAN, a lighthouse you already pay flat-rate for is cheaper.

🔴 APPLYING THIS RESTARTS THE MESH. nebula@${NET} has to reload to pick the change up,
which drops every session over nebula.${NET} for a few seconds. If your shell reaches
this host over the mesh, run it from a console/LAN session instead.
EOF
exit 1
