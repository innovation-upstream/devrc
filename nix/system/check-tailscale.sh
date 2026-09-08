#!/usr/bin/env bash
# Read-only check: is Tailscale actually carrying a usable second path INTO the LAN?
#
# Tailscale is the INDEPENDENT BACKUP for nebula. Nebula is currently the only remote
# path to the workbench; if its lighthouse, its relay or the host firewall in front of
# it breaks while the operator is off-LAN for months, there is no second door. This
# script answers whether the second door is actually open -- not whether it was
# configured.
#
#   bash nix/system/check-tailscale.sh                 # auto-detect the host role
#   bash nix/system/check-tailscale.sh --role server   # force the subnet-router role
#   bash nix/system/check-tailscale.sh --role client   # force the plain-client role
#   bash nix/system/check-tailscale.sh --self-test     # validate the parser only
#
# Overrides (all optional):
#   TS_ROLE=server|client            same as --role
#   TS_SUBNET=192.168.50.0/24        the subnet the server role must advertise
#   TS_EXPECT_MESH_IP_SERVER=10.42.0.30
#   TS_EXPECT_MESH_IP_CLIENT=10.42.0.100
#
# 🔴 IT READS LIVE RUNTIME STATE, NOT THE NIX CONFIG AND NOT THE UNIT FILE. Everything
# below comes from `tailscale status --json` and `tailscale debug prefs` (the running
# daemon's own answer), from /proc/sys (the kernel's own answer), and from `ip route`.
# `systemctl cat tailscaled` exits 0 for a unit that is dead or was never started, so a
# config that BUILT but never ACTIVATED reads as applied -- that exact bug is why
# check-nebula-relays.sh was rewritten, and this script does not repeat it. The unit's
# state is printed as CONTEXT only; no verdict is keyed on it.
#
# 🔴 ADVERTISED AND APPROVED ARE TWO DIFFERENT CLAIMS AND BOTH ARE PRINTED.
#   * ADVERTISED lives on this node -- `AdvertiseRoutes` in the daemon's prefs. It is
#     entirely under this script's control and says nothing about whether traffic flows.
#   * APPROVED lives in the Tailscale admin console. Until a human ticks the route there
#     it carries NO traffic, and from the node's side that is INDISTINGUISHABLE from
#     success: the node happily reports it is advertising. The approval signal is
#     `Self.PrimaryRoutes` in `tailscale status --json`, which the control plane only
#     populates once the route is approved.
#   Reporting only the first would certify a subnet router that routes nothing.
#
# 🔴 NODE KEY EXPIRY. Tailscale node keys expire after 180 days BY DEFAULT. A trip of
# several months crosses that, and the failure mode is the backup path going dark while
# it is being relied on -- silently, with no local error. The expiry date and the days
# remaining are reported, and expiry being ENABLED AT ALL is treated as an outstanding
# action, not as a pass.
#
# Exit: 0 = every claim holds, including admin-console approval and disabled key expiry
#       3 = everything THIS HOST controls is correct, but an ADMIN-CONSOLE action is
#           still outstanding (route not approved, and/or key expiry still enabled).
#           Split out from 1 on purpose: it is not a defect on the node, it is a human
#           step that cannot be scripted, and apply-tailscale.sh must not roll back a
#           correct switch because a browser tab has not been clicked yet.
#       1 = a definitive node-side FAIL (not running, not authenticated, not
#           advertising, forwarding off, no LAN route)
#       2 = cannot determine -- tailscale absent, daemon unreachable, role ambiguous,
#           parser failure, or the parser failed its own controls
set -euo pipefail

SUBNET="${TS_SUBNET:-192.168.50.0/24}"
MESH_SERVER="${TS_EXPECT_MESH_IP_SERVER:-10.42.0.30}"
MESH_CLIENT="${TS_EXPECT_MESH_IP_CLIENT:-10.42.0.100}"
ROLE="${TS_ROLE:-}"
SELFTEST=0

while [ $# -gt 0 ]; do
  case "$1" in
    --self-test) SELFTEST=1; shift ;;
    --role)      ROLE="${2:-}"; shift 2 ;;
    --role=*)    ROLE="${1#--role=}"; shift ;;
    -h|--help)   sed -n '2,56p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *)           echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

# 🔴 Validated HERE, not next to the host probe further down. Measured: with the check
# sitting after the `tailscale` binary precondition, `--role bogus` was rejected with
# "no tailscale binary on PATH" -- the right exit code for the wrong reason, and an
# error message that sends the operator to fix something that is not broken.
case "${ROLE:-}" in
  ""|server|client) ;;
  *) echo "CANNOT DETERMINE: role must be 'server' or 'client', got '$ROLE'" >&2; exit 2 ;;
esac

# --- exact membership of a comma-separated list -------------------------------------
# NOT a substring test. `192.168.50.0/24` is a substring of `192.168.50.0/241`, and a
# prefix match also accepts `192.168.50.0/2` -- both are pinned by controls in
# _self_test, in the failing direction.
_in_csv() {  # $1 = needle, $2 = csv
  case ",${2}," in *,"${1}",*) return 0 ;; esac
  return 1
}

# --- the parser -------------------------------------------------------------------
# Stdlib json only, on purpose: `jq` is not guaranteed on either host, and PyYAML is
# already known not to be importable from the python3 on the default PATH here.
#
# Emits ONE `|`-separated record so a caller cannot half-read it:
#   backend|online|ips|advertised|primary|expiry_iso|expiry_days|route_all|prefs_src
#
# Exits 2 rather than printing a record when the document is not what it claims to be,
# so "the parser found nothing" can never be reported as "this node advertises nothing"
# -- which is a definitive FAIL derived from a file that was never understood.
_parse_ts() {   # $1 = status json path, $2 = prefs json path or "-" when unavailable
  python3 - "$1" "$2" <<'PY'
import datetime, json, sys

def load(p):
    with open(p, encoding="utf-8") as f:
        return json.load(f)

try:
    st = load(sys.argv[1])
except Exception as e:
    sys.stderr.write("parse: status: %s\n" % e)
    sys.exit(2)
if not isinstance(st, dict) or "BackendState" not in st:
    sys.stderr.write("parse: no BackendState key -- not a `tailscale status --json` document\n")
    sys.exit(2)

me = st.get("Self") or {}
backend = st.get("BackendState") or "?"
online = "true" if me.get("Online") is True else "false"

ips = [i for i in (st.get("TailscaleIPs") or me.get("TailscaleIPs") or []) if isinstance(i, str)]
primary = [r for r in (me.get("PrimaryRoutes") or []) if isinstance(r, str)]

# Key expiry. `KeyExpiry` is omitempty AND some builds emit the Go zero time instead,
# so BOTH spellings of "expiry is disabled" have to be handled; treating only one of
# them as disabled reports a node with expiry OFF as expiring on 0001-01-01.
raw = me.get("KeyExpiry") or ""
expiry, days = "", ""
if raw and not raw.startswith("0001-01-01"):
    try:
        t = datetime.datetime.fromisoformat(raw.replace("Z", "+00:00"))
        now = datetime.datetime.now(datetime.timezone.utc)
        expiry = t.isoformat()
        days = str((t - now).days)
    except Exception as e:                # a value we cannot read is NOT "disabled"
        sys.stderr.write("parse: KeyExpiry %r: %s\n" % (raw, e))
        sys.exit(2)

advertised, route_all, src = [], "?", "none"
if sys.argv[2] != "-":
    try:
        pf = load(sys.argv[2])
    except Exception as e:
        sys.stderr.write("parse: prefs: %s\n" % e)
        sys.exit(2)
    if not isinstance(pf, dict) or "AdvertiseRoutes" not in pf:
        sys.stderr.write("parse: no AdvertiseRoutes key -- not a `tailscale debug prefs` document\n")
        sys.exit(2)
    advertised = [r for r in (pf.get("AdvertiseRoutes") or []) if isinstance(r, str)]
    route_all = "true" if pf.get("RouteAll") is True else "false"
    src = "prefs"

print("|".join((backend, online, ",".join(ips), ",".join(advertised),
                ",".join(primary), expiry, days, route_all, src)))
PY
}

# --- self-test: prove the parser can say BOTH answers before trusting either -------
# A checker that cannot go red is not evidence, and one that has never gone green may
# be wired to nothing. Every fixture below is a shape the real daemon emits.
#
# 🔴 PUBLIC-REPO SAFE BY CONSTRUCTION. 100.64.0.0/10 is the CGNAT range Tailscale
# itself hands out (not routable to anything of ours), 192.168.50.0/24 is RFC1918, and
# every hostname is under the reserved `.example.test` TLD. No real public IP and no
# client hostname is spelled anywhere in this file.
#
# The DAYS field is asserted separately, from fixtures generated at run time at
# `+N days +12 hours`, so the twelve-hour cushion makes the floor deterministic instead
# of racing the second boundary. Two different N kill a mutant that hardcodes or
# mis-scales the value.
_self_test() {
  local dir rc=0
  dir=$(mktemp -d)
  trap 'rm -rf "$dir"' RETURN

  # Compares everything EXCEPT the days field (7th), which has its own cases below.
  _t() {  # $1 = name, $2 = expected record with `D` in the days slot, $3 = status, $4 = prefs
    local g norm
    if ! g=$(_parse_ts "$3" "$4" 2>/dev/null); then
      echo "  [self-test] $1: parser exited non-zero"; rc=1; return 0
    fi
    norm=$(printf '%s' "$g" | awk -F'|' -v OFS='|' '{ if ($7 != "") $7 = "D"; print }')
    if [ "$norm" = "$2" ]; then
      echo "  [self-test] $1 -> ok"
    else
      echo "  [self-test] $1 FAILED: got '$norm', want '$2'"; rc=1
    fi
    return 0
  }

  cat >"$dir/adv-prefs.json" <<'J'
{"AdvertiseRoutes":["192.168.50.0/24"],"RouteAll":false,"WantRunning":true}
J

  # A subnet router that IS advertising and HAS been approved. PrimaryRoutes and
  # AdvertiseRoutes carry the SAME string -- that is the trap: reading either one alone
  # answers both questions identically and certifies an unapproved router.
  cat >"$dir/approved.json" <<'J'
{"BackendState":"Running","TailscaleIPs":["100.100.10.5"],
 "Self":{"HostName":"workbench","DNSName":"workbench.tailnet.example.test.","Online":true,
         "KeyExpiry":"2099-01-02T03:04:05Z","PrimaryRoutes":["192.168.50.0/24"]}}
J
  _t "advertised + approved                       " \
     "Running|true|100.100.10.5|192.168.50.0/24|192.168.50.0/24|2099-01-02T03:04:05+00:00|D|false|prefs" \
     "$dir/approved.json" "$dir/adv-prefs.json"

  # 🔴 THE CASE THIS SCRIPT EXISTS FOR: advertised, NOT approved. Identical on the node
  # side; PrimaryRoutes is the only thing that differs, and it is EMPTY.
  cat >"$dir/unapproved.json" <<'J'
{"BackendState":"Running","TailscaleIPs":["100.100.10.5"],
 "Self":{"HostName":"workbench","Online":true,
         "KeyExpiry":"2099-01-02T03:04:05Z","PrimaryRoutes":[]}}
J
  _t "advertised but NOT approved (empty Primary) " \
     "Running|true|100.100.10.5|192.168.50.0/24||2099-01-02T03:04:05+00:00|D|false|prefs" \
     "$dir/unapproved.json" "$dir/adv-prefs.json"

  # PrimaryRoutes absent entirely (the field is omitempty) must read the SAME as empty,
  # not as a parser failure and not as approval.
  cat >"$dir/noprimary.json" <<'J'
{"BackendState":"Running","TailscaleIPs":["100.100.10.5"],
 "Self":{"HostName":"workbench","Online":true,"KeyExpiry":"2099-01-02T03:04:05Z"}}
J
  _t "PrimaryRoutes absent == not approved        " \
     "Running|true|100.100.10.5|192.168.50.0/24||2099-01-02T03:04:05+00:00|D|false|prefs" \
     "$dir/noprimary.json" "$dir/adv-prefs.json"

  # Key expiry DISABLED, both spellings. Omitted...
  cat >"$dir/noexpiry.json" <<'J'
{"BackendState":"Running","TailscaleIPs":["100.100.10.5"],
 "Self":{"HostName":"laptop","Online":true,"PrimaryRoutes":[]}}
J
  _t "key expiry disabled (field omitted)         " \
     "Running|true|100.100.10.5|||||?|none" "$dir/noexpiry.json" "-"
  # ...and spelled as the Go zero time, which is NOT an expiry in the year 1.
  cat >"$dir/zeroexpiry.json" <<'J'
{"BackendState":"Running","TailscaleIPs":["100.100.10.5"],
 "Self":{"HostName":"laptop","Online":true,"KeyExpiry":"0001-01-01T00:00:00Z","PrimaryRoutes":[]}}
J
  _t "key expiry disabled (Go zero time)          " \
     "Running|true|100.100.10.5|||||?|none" "$dir/zeroexpiry.json" "-"

  # A logged-out node: BackendState carries it, Online is false, no addresses.
  cat >"$dir/needslogin.json" <<'J'
{"BackendState":"NeedsLogin","AuthURL":"https://login.example.test/a/abc","TailscaleIPs":[],
 "Self":{"HostName":"laptop","Online":false}}
J
  _t "NeedsLogin (not authenticated)              " \
     "NeedsLogin|false||||||?|none" "$dir/needslogin.json" "-"

  # A plain client that ACCEPTS routes. RouteAll is the `--accept-routes` pref, and
  # `AdvertiseRoutes: null` is how the daemon spells "none" -- distinct from `[]`.
  cat >"$dir/client-prefs.json" <<'J'
{"AdvertiseRoutes":null,"RouteAll":true,"WantRunning":true}
J
  _t "client prefs: null routes, RouteAll on      " \
     "Running|true|100.100.10.5|||||true|prefs" "$dir/noexpiry.json" "$dir/client-prefs.json"

  # --- the DAYS arithmetic, at two points -------------------------------------------
  local n exp got_days
  for n in 10 400; do
    exp=$(date -u -d "+${n} days +12 hours" +%Y-%m-%dT%H:%M:%SZ)
    printf '{"BackendState":"Running","TailscaleIPs":["100.100.10.5"],"Self":{"Online":true,"KeyExpiry":"%s"}}\n' \
      "$exp" >"$dir/exp-$n.json"
    got_days=$(_parse_ts "$dir/exp-$n.json" "-" | cut -d'|' -f7)
    if [ "$got_days" = "$n" ]; then
      echo "  [self-test] expiry in $n days -> reports $n -> ok"
    else
      echo "  [self-test] expiry in $n days FAILED: reports '$got_days'"; rc=1
    fi
  done
  # An ALREADY-EXPIRED key must report a negative number, not be mistaken for disabled.
  exp=$(date -u -d "-5 days -12 hours" +%Y-%m-%dT%H:%M:%SZ)
  printf '{"BackendState":"Running","TailscaleIPs":["100.100.10.5"],"Self":{"Online":true,"KeyExpiry":"%s"}}\n' \
    "$exp" >"$dir/exp-past.json"
  got_days=$(_parse_ts "$dir/exp-past.json" "-" | cut -d'|' -f7)
  case "$got_days" in
    -*) echo "  [self-test] an already-expired key reports $got_days (negative) -> ok" ;;
    *)  echo "  [self-test] an already-expired key FAILED: reports '$got_days'"; rc=1 ;;
  esac

  # --- the parser must REFUSE rather than answer -------------------------------------
  local f
  for f in notstatus broken notprefs; do
    case "$f" in
      notstatus) printf '{"hello":"world"}\n' >"$dir/$f.json"
                 set -- "$dir/$f.json" "-"
                 ;;
      broken)    printf 'not json at all\n' >"$dir/$f.json"
                 set -- "$dir/$f.json" "-"
                 ;;
      notprefs)  printf '{"RouteAll":true}\n' >"$dir/$f.json"
                 set -- "$dir/approved.json" "$dir/$f.json"
                 ;;
    esac
    if _parse_ts "$1" "$2" >/dev/null 2>&1; then
      echo "  [self-test] $f: FAILED -- parser answered instead of exiting 2"; rc=1
    else
      echo "  [self-test] $f -> rc 2, not a false 'nothing advertised' -> ok"
    fi
  done
  # An UNREADABLE KeyExpiry must also refuse. Reporting it as "" would print
  # "DISABLED -- this node key does not expire" for a key that does expire.
  printf '{"BackendState":"Running","Self":{"Online":true,"KeyExpiry":"whenever"}}\n' >"$dir/badexp.json"
  if _parse_ts "$dir/badexp.json" "-" >/dev/null 2>&1; then
    echo "  [self-test] unreadable KeyExpiry FAILED -- answered instead of exiting 2"; rc=1
  else
    echo "  [self-test] unreadable KeyExpiry -> rc 2, not a false 'DISABLED' -> ok"
  fi

  # --- the verdict predicate, driven -------------------------------------------------
  # The parser being right is not the same as the VERDICT being right. Drive _in_csv
  # directly, in BOTH directions, so it cannot be wired to nothing.
  if _in_csv "192.168.50.0/24" "192.168.50.0/24"; then
    echo "  [self-test] _in_csv exact match -> ok"
  else
    echo "  [self-test] _in_csv exact match FAILED"; rc=1
  fi
  if _in_csv "192.168.50.0/24" "10.0.0.0/8,192.168.50.0/24,::/0"; then
    echo "  [self-test] _in_csv middle of a list -> ok"
  else
    echo "  [self-test] _in_csv middle of a list FAILED"; rc=1
  fi
  if _in_csv "192.168.50.0/24" ""; then
    echo "  [self-test] _in_csv on an EMPTY list FAILED: said yes"; rc=1
  else
    echo "  [self-test] _in_csv on an EMPTY list -> no -> ok"
  fi
  # 🔴 A SUBSTRING must not count: /24 is a substring of /241, and a prefix match would
  # also accept the wrong mask /2. Both would certify a router advertising the wrong
  # thing.
  if _in_csv "192.168.50.0/24" "192.168.50.0/241"; then
    echo "  [self-test] _in_csv substring FAILED: /241 matched /24"; rc=1
  else
    echo "  [self-test] _in_csv rejects a substring (/241 != /24) -> ok"
  fi
  if _in_csv "192.168.50.0/24" "192.168.50.0/16"; then
    echo "  [self-test] _in_csv FAILED: a different mask matched"; rc=1
  else
    echo "  [self-test] _in_csv rejects a different mask (/16 != /24) -> ok"
  fi
  return $rc
}

if [ "$SELFTEST" = "1" ]; then
  command -v python3 >/dev/null 2>&1 || {
    echo "CANNOT DETERMINE: python3 is not on PATH" >&2; exit 2; }
  echo "parser self-test:"
  st_rc=0
  _self_test || st_rc=$?     # 🔴 `rc=0` FIRST, then `f || rc=$?`. A bare `f; rc=$?`
                             # is dead code under `set -e`, and `f || rc=$?` without
                             # the seed dies under `set -u` on the SUCCESS path.
  if [ "$st_rc" != "0" ]; then
    echo "self-test: FAILED -- the parser's verdict would mean nothing" >&2
    exit 2                   # 2, not 1: 1 means "tailscale is wrong", another claim
  fi
  echo "self-test: all controls passed"
  exit 0
fi

# --- preconditions -----------------------------------------------------------------
command -v python3 >/dev/null 2>&1 || {
  echo "CANNOT DETERMINE: python3 is not on PATH." >&2
  echo "  It is NOT in /run/current-system/sw/bin on these hosts. Run this from a" >&2
  echo "  shell that has one (the devrc nix develop shell does)." >&2
  exit 2; }

if ! command -v tailscale >/dev/null 2>&1; then
  echo "CANNOT DETERMINE: no \`tailscale\` binary on PATH." >&2
  echo "  Tailscale has not been applied to this host yet:" >&2
  echo "    sudo env \"PATH=\$PATH\" bash nix/system/apply-tailscale.sh" >&2
  exit 2
fi

# --- which host is this? ------------------------------------------------------------
# 🔴 `hostname` CANNOT discriminate these machines -- BOTH answer `nixos`. The nebula
# mesh address is unique by construction, so that is the discriminator.
#
# 🔴 BUT NEBULA MAY BE THE THING THAT IS BROKEN. Tailscale exists precisely as the
# backup for a dead nebula, so the interface being absent is the EXPECTED case when
# this script matters most. It is not guessed at: the role is reported as ambiguous
# and the exact `--role` remedy is printed. Pass `--role` and the probe never runs.
if [ -z "$ROLE" ]; then
  mesh=$(ip -4 -o addr show nebula.mesh 2>/dev/null | awk '{print $4}' | cut -d/ -f1 | head -1)
  case "$mesh" in
    "$MESH_SERVER") ROLE=server ;;
    "$MESH_CLIENT") ROLE=client ;;
    "")  echo "CANNOT DETERMINE: no address on nebula.mesh, so the host role cannot be" >&2
         echo "  auto-detected. This is EXPECTED when nebula is down -- which is exactly" >&2
         echo "  when tailscale matters. Say which host this is:" >&2
         echo "    bash ${BASH_SOURCE[0]} --role server   # the workbench (subnet router)" >&2
         echo "    bash ${BASH_SOURCE[0]} --role client   # the laptop" >&2
         exit 2 ;;
    *)   echo "CANNOT DETERMINE: nebula.mesh is $mesh, which is neither the workbench" >&2
         echo "  ($MESH_SERVER) nor the laptop ($MESH_CLIENT). Refusing to guess a role." >&2
         echo "  Re-run with --role server or --role client if you mean it." >&2
         exit 2 ;;
  esac
  echo "role    : $ROLE  (from nebula.mesh = $mesh)"
else
  echo "role    : $ROLE  (forced -- validated at argument-parse time, above)"
fi

# --- the parser earns its verdict before the verdict is read ------------------------
echo "parser self-test:"
st_rc=0
_self_test || st_rc=$?
if [ "$st_rc" != "0" ]; then
  echo "CANNOT DETERMINE: the parser failed its own controls -- its verdict would mean nothing" >&2
  exit 2
fi

# --- live runtime state --------------------------------------------------------------
TMPD=$(mktemp -d)
trap 'rm -rf "$TMPD"' EXIT

# 🔴 Key the verdict on the CONTENT, never on an exit code that reports something else.
# `tailscale status` exits NON-ZERO for perfectly readable states (a stopped or
# logged-out backend), so treating rc as the signal reports "cannot read tailscale" for
# a daemon that answered fully and truthfully -- the exact inverse of the truth. Take
# the JSON if it parses; the rc is only worth looking at when it does not.
ts_rc=0
tailscale status --json >"$TMPD/status.json" 2>"$TMPD/status.err" || ts_rc=$?
if ! python3 -c 'import json,sys; json.load(open(sys.argv[1]))' "$TMPD/status.json" 2>/dev/null; then
  echo "CANNOT DETERMINE: \`tailscale status --json\` produced no readable JSON (rc=$ts_rc)." >&2
  sed 's/^/    | /' "$TMPD/status.err" >&2 || true
  echo "  The daemon is not reachable. Check it is actually RUNNING, not merely" >&2
  echo "  configured:  systemctl status tailscaled" >&2
  exit 2
fi

# Prefs are how the node's OWN advertisement is read. Optional: without them the
# ADVERTISED claim is reported as UNKNOWN rather than silently as "nothing".
prefs_arg="-"
if tailscale debug prefs >"$TMPD/prefs.json" 2>"$TMPD/prefs.err"; then
  if python3 -c 'import json,sys; json.load(open(sys.argv[1]))' "$TMPD/prefs.json" 2>/dev/null; then
    prefs_arg="$TMPD/prefs.json"
  fi
fi

if ! rec=$(_parse_ts "$TMPD/status.json" "$prefs_arg"); then
  echo "CANNOT DETERMINE: could not parse the daemon's own state" >&2
  exit 2
fi
IFS='|' read -r backend online ips advertised primary expiry days route_all prefs_src <<<"$rec"

# CONTEXT ONLY -- deliberately not a verdict. `systemctl cat` exits 0 for a dead unit
# and `is-active` says nothing about whether the node is authenticated or routing.
unit_state=$(systemctl is-active tailscaled.service 2>/dev/null || true)

if [ "$ROLE" = "server" ]; then
  UP_CMD="sudo tailscale up --advertise-routes=${SUBNET} --accept-dns=false"
else
  UP_CMD="sudo tailscale up --accept-routes"
fi

echo
echo "daemon  : tailscaled unit is '${unit_state:-unknown}'  (context only -- no verdict is keyed on it)"
echo "backend : $backend"
echo "online  : $online"
echo "addrs   : ${ips:-<none>}"
echo "adverts : ${advertised:-<none>}   (source: $prefs_src -- what THIS NODE claims)"
echo "primary : ${primary:-<none>}   (source: control plane -- what is APPROVED)"
if [ -n "$expiry" ]; then
  echo "keyexp  : $expiry  (${days} days from now)"
else
  echo "keyexp  : DISABLED -- this node key does not expire"
fi
echo

# --- verdicts ------------------------------------------------------------------------
rc=0
fails=()
actions=()

if [ "$backend" != "Running" ]; then
  fails+=("tailscaled backend is '$backend', not 'Running' -- this node carries no traffic.
    If it is NeedsLogin or Stopped, bring it up:  $UP_CMD")
fi
if [ "$online" != "true" ]; then
  fails+=("the control plane does not consider this node Online; it cannot be reached.")
fi
if [ -z "$ips" ]; then
  fails+=("this node has no Tailscale address -- it has never authenticated to a tailnet.
    Authenticate it:  $UP_CMD")
fi

if [ "$prefs_src" = "none" ]; then
  fails+=("could not read \`tailscale debug prefs\`, so what this node ADVERTISES is
    UNKNOWN. Deliberately not reported as 'advertises nothing' -- that would be a
    definitive claim derived from a file that was never read.")
elif [ "$ROLE" = "server" ]; then
  if _in_csv "$SUBNET" "$advertised"; then
    echo "PASS  advertise : this node advertises $SUBNET"
  else
    fails+=("this node does NOT advertise $SUBNET (advertised: ${advertised:-<none>}).
    Fix:  $UP_CMD")
  fi
elif [ -n "$advertised" ]; then
  echo "NOTE  advertise : a CLIENT is advertising routes ($advertised) -- not wrong, but"
  echo "                  the laptop is meant to be a plain client."
else
  echo "PASS  advertise : plain client, advertises nothing (as intended)"
fi

if [ "$ROLE" = "server" ]; then
  # 🔴 THE SECOND, DIFFERENT CLAIM. An advertised-but-unapproved route looks IDENTICAL
  # to success from this node and carries no traffic at all.
  if _in_csv "$SUBNET" "$primary"; then
    echo "PASS  approved  : $SUBNET is APPROVED in the admin console; this node is its primary router"
  else
    actions+=("$SUBNET is advertised but NOT APPROVED. It carries NO traffic until a
    human approves it, and the node cannot tell the difference. This CANNOT be
    scripted -- the admin console is the only place it exists:
      https://login.tailscale.com/admin/machines -> this machine -> ... ->
      Edit route settings -> tick $SUBNET -> Save")
  fi

  # Forwarding, read from the KERNEL. A sysctl that is in the config but was never
  # activated reads as ON if you read the config instead of /proc.
  #
  # TS_PROC_ROOT exists so BOTH directions of this check can be driven from fixtures.
  # A guard whose failing branch has never been watched execute is not evidence, and
  # neither host currently has ipv6 forwarding on, so the passing branch alone would
  # be all anyone ever saw.
  v4=$(cat "${TS_PROC_ROOT:-/proc}/sys/net/ipv4/ip_forward" 2>/dev/null || echo "?")
  v6=$(cat "${TS_PROC_ROOT:-/proc}/sys/net/ipv6/conf/all/forwarding" 2>/dev/null || echo "?")
  if [ "$v4" = "1" ]; then
    echo "PASS  forward4  : net.ipv4.ip_forward = 1 (live, from /proc)"
  else
    fails+=("net.ipv4.ip_forward is '$v4', not 1. A subnet router that cannot forward
    advertises a route into a black hole. Read from /proc, so this is the KERNEL's
    answer and not the config's.")
  fi
  if [ "$v6" = "1" ]; then
    echo "PASS  forward6  : net.ipv6.conf.all.forwarding = 1 (live, from /proc)"
  else
    actions+=("net.ipv6.conf.all.forwarding is '$v6', not 1. IPv4 forwarding is what
    $SUBNET needs, so this is reported rather than failed.")
  fi

  # The subnet has to actually be reachable FROM here, over something that is not
  # tailscale itself. Advertising a subnet you are not on routes nothing.
  lanroute=$(ip -4 -o route show 2>/dev/null | awk -v s="$SUBNET" '$1==s' | grep -v 'dev tailscale' || true)
  if [ -n "$lanroute" ]; then
    echo "PASS  lanroute  : $SUBNET is directly reachable from this host --"
    printf '                  %s\n' "$lanroute"
  else
    fails+=("no non-tailscale route to $SUBNET in \`ip route\`. This host is advertising
    a subnet it is not on; traffic arriving over tailscale would have nowhere to go.")
  fi
else
  if [ "$route_all" = "true" ]; then
    echo "PASS  acceptrt  : this client accepts subnet routes (RouteAll / --accept-routes)"
  elif [ "$prefs_src" = "prefs" ]; then
    actions+=("this client does not accept subnet routes (RouteAll=$route_all), so the
    workbench's $SUBNET advertisement will not be installed here. Fix:  $UP_CMD")
  fi
  if ip -4 -o route show 2>/dev/null | awk -v s="$SUBNET" '$1==s' | grep -q 'dev tailscale'; then
    echo "PASS  lanroute  : $SUBNET is installed here via a tailscale interface"
  else
    actions+=("$SUBNET is not in this host's routing table via tailscale. Either the
    route is not approved in the admin console, or --accept-routes is off here, or the
    workbench is not advertising. Check the workbench with --role server.")
  fi
fi

# 🔴 KEY EXPIRY. Enabled at all is an outstanding action, because the 180-day default is
# SHORTER than the trip and the lapse is silent.
if [ -z "$expiry" ]; then
  echo "PASS  keyexpiry : disabled -- this node key will not expire mid-trip"
else
  actions+=("node key expiry is ENABLED: it expires $expiry, in $days days.
    Tailscale's default is 180 days, shorter than a months-long trip, and when it lapses
    this backup path goes dark with no local error. Disabling it is an ADMIN-CONSOLE
    action that cannot be scripted:
      https://login.tailscale.com/admin/machines -> this machine -> ... ->
      Disable key expiry")
fi

echo
if [ ${#fails[@]} -gt 0 ]; then
  echo "FAIL: this host is NOT carrying a usable tailscale path."
  for f in "${fails[@]}"; do printf '  - %s\n' "$f"; done
  rc=1
fi
if [ ${#actions[@]} -gt 0 ]; then
  echo "ACTION REQUIRED (admin console / human -- cannot be scripted):"
  for a in "${actions[@]}"; do printf '  - %s\n' "$a"; done
  # 🔴 NOT `[ "$rc" = 0 ] && rc=3`: under `set -e` an `a && b` whose test is FALSE
  # fails as a whole and kills the script right before it prints its verdict.
  if [ "$rc" = "0" ]; then rc=3; fi
fi
if [ "$rc" = "0" ]; then
  echo "PASS: tailscale is a working second path on this host, and nothing is outstanding."
fi
exit "$rc"
