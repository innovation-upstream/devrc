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
# 🔴 AN ABSENT `KeyExpiry` IS AMBIGUOUS AND IS NOT RESOLVED TO "DISABLED". The field is
# missing in three different situations -- expiry genuinely disabled, the node never
# authenticated so no node key exists at all, and a build that does not emit it -- and
# only the first is reassuring. Resolving absence to the reassuring branch printed
# `PASS keyexpiry : disabled` on a node that had never logged in, for the single item
# most likely to kill this path silently mid-trip. "Disabled" is now claimed ONLY when
# the node IS authenticated; otherwise the claim is UNKNOWN (see rc 4).
#
# 🔴 AUTHENTICATED IS A FIRST-CLASS STATE, NOT A FAILURE. `apply-tailscale.sh` installs
# the service and switches; it deliberately does NOT run `tailscale up`, which needs a
# browser. So immediately after a successful apply the node is configured, running, and
# has no tailnet identity at all -- backend `NeedsLogin`, no address, nothing
# advertised, no key. Reporting that as a node-side FAIL made the apply script roll back
# the config it had just installed. It is rc 4.
#
# Exit: 0 = every claim holds, including admin-console approval and disabled key expiry
#       3 = everything THIS HOST controls is correct, but an ADMIN-CONSOLE action is
#           still outstanding (route not approved, and/or key expiry still enabled).
#           Split out from 1 on purpose: it is not a defect on the node, it is a human
#           step that cannot be scripted, and apply-tailscale.sh must not roll back a
#           correct switch because a browser tab has not been clicked yet.
#       4 = INCOMPLETE. No defect was found, but one or more claims could not be
#           evaluated. Overwhelmingly the common cause is that the node has not
#           authenticated yet -- the EXPECTED state straight after apply-tailscale.sh --
#           in which case advertisement, route approval and key expiry are all
#           unknowable rather than wrong. Also covers unreadable prefs. Like 3, this is
#           NOT a reason to roll back a switch.
#       1 = a definitive node-side FAIL: the node HAS a tailnet identity and something
#           about it is wrong (backend not Running, not Online, not advertising,
#           forwarding off, no LAN route).
#       2 = cannot determine -- tailscale absent, daemon unreachable, role ambiguous,
#           parser failure, or the parser failed its own controls
set -euo pipefail

SUBNET="${TS_SUBNET:-192.168.50.0/24}"
MESH_SERVER="${TS_EXPECT_MESH_IP_SERVER:-10.42.0.30}"
MESH_CLIENT="${TS_EXPECT_MESH_IP_CLIENT:-10.42.0.100}"
ROLE="${TS_ROLE:-}"
SELFTEST=0

# 🔴 The help text is the comment header, printed to WHEREVER IT ENDS -- deliberately not
# a hardcoded line range. `sed -n '2,56p'` overshot by two lines and printed the literal
# `set -euo pipefail` as if it were documentation, and apply-tailscale.sh's equivalent
# UNDERSHOT and silently dropped its whole rollback/idempotency paragraph. A range is a
# second copy of a fact the file already states; this reads the fact.
_print_help() { awk 'NR==1 {next} /^#/ {sub(/^# ?/, ""); print; next} {exit}' "${BASH_SOURCE[0]}"; }

while [ $# -gt 0 ]; do
  case "$1" in
    --self-test) SELFTEST=1; shift ;;
    # 🔴 `--role` AS THE FINAL ARGUMENT. `shift 2` with one argument left fails, and
    # under `set -e` that killed the script with exit 1 and NOTHING printed -- the
    # operator sees a bare failure and no hint of what is wrong. Checked explicitly.
    --role)      [ $# -ge 2 ] || { echo "CANNOT DETERMINE: --role needs a value ('server' or 'client'); it was given as the last argument with nothing after it." >&2; exit 2; }
                 ROLE="$2"; shift 2 ;;
    --role=*)    ROLE="${1#--role=}"; shift ;;
    -h|--help)   _print_help; exit 0 ;;
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

# --- has this node ever authenticated to a tailnet? -----------------------------------
# 🔴 THE STATE apply-tailscale.sh LANDS IN, and the reason this predicate exists rather
# than being inlined: the apply script installs the service and switches, then asks this
# checker whether to keep the change. Straight after that switch the node has NEVER run
# `tailscale up` (it needs a browser), so backend is `NeedsLogin`, there is no address,
# nothing is advertised and there is no node key. Reporting that as a node-side FAIL is
# what made the apply script roll back a config it had just installed correctly.
#
# TWO signals, and they must AGREE -- disagreement means NOT authenticated, because the
# reassuring answer is the one that has to carry more evidence:
#   * an address in 100.64.0.0/10 is the DURABLE signal. A node that has completed
#     `tailscale up` keeps it even when the backend is `Stopped` (WantRunning=false).
#   * `BackendState` is the daemon saying it in words. `NeedsLogin` / `NoState` mean
#     there is no tailnet identity, whatever else is in the document.
_authenticated() {   # $1 = BackendState, $2 = comma-separated TailscaleIPs
  [ -n "$2" ] || return 1
  case "$1" in NeedsLogin|NoState|"") return 1 ;; esac
  return 0
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

  # --- _authenticated, driven in BOTH directions -------------------------------------
  # 🔴 The whole rc-4 split, and the key-expiry claim, hang off this one predicate. A
  # version that answered "yes" to everything would make rc 4 unreachable and put the
  # post-switch FAIL back; one that answered "no" to everything would make rc 0
  # unreachable and permanently report a working node as not-yet-set-up. Both directions
  # are driven, on the exact states the daemon emits.
  local a_case
  for a_case in \
      "Running:100.100.10.5:yes:a logged-in node" \
      "Stopped:100.100.10.5:yes:logged in but WantRunning=false -- the key still exists" \
      "NeedsLogin::no:the state straight after apply-tailscale.sh" \
      "NoState::no:the daemon has no state at all" \
      "NeedsLogin:100.100.10.5:no:signals DISAGREE -- must not resolve to authenticated" \
      "Running::no:Running with no address is not an identity" \
      ":100.100.10.5:no:an empty BackendState is not evidence of anything"; do
    local a_backend a_ips a_want a_why a_got
    IFS=':' read -r a_backend a_ips a_want a_why <<<"$a_case"
    if _authenticated "$a_backend" "$a_ips"; then a_got=yes; else a_got=no; fi
    if [ "$a_got" = "$a_want" ]; then
      echo "  [self-test] _authenticated '${a_backend:-<empty>}' / '${a_ips:-<none>}' -> $a_got  ($a_why) -> ok"
    else
      echo "  [self-test] _authenticated '${a_backend:-<empty>}' / '${a_ips:-<none>}' FAILED: got $a_got, want $a_want"; rc=1
    fi
  done
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

# 🔴 `--accept-dns=false` ON BOTH ROLES, and the symmetry is the point. The reasoning is
# the same on either machine: MagicDNS makes tailscale take over the host resolver, and
# on this fleet the resolver is already owned by dnsmasq and the `.lan` names. Giving the
# server the flag and not the client would hand the laptop's resolver to tailscale --
# the ONE machine the operator travels with, and the one whose name resolution nobody
# can fix from the far side. The cost is stated out loud in apply-tailscale.sh's next
# steps: MagicDNS names do not resolve, so address the workbench by its tailnet address.
#
# 🔴 These two strings are duplicated in apply-tailscale.sh's next-step text, and
# `scripts/tests/test_tailscale_scripts.py` pins them EQUAL across the two files -- an
# operator who follows the apply script's instructions and then runs this checker must
# not be told to run a different command from the one they were told to run.
if [ "$ROLE" = "server" ]; then
  UP_CMD="sudo tailscale up --advertise-routes=${SUBNET} --accept-dns=false"
else
  UP_CMD="sudo tailscale up --accept-routes --accept-dns=false"
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
  echo "keyexp  : <no KeyExpiry field>  (that means 'disabled' ONLY on an authenticated"
  echo "                                 node -- see the verdict below)"
fi
echo

# --- verdicts ------------------------------------------------------------------------
# THREE buckets, deliberately not two. `fails` is a defect on this node; `actions` is a
# human step in the admin console; `unknowns` is a claim this run COULD NOT EVALUATE.
# Folding the third into the first is what made a freshly-switched, perfectly correct
# host report a node-side FAILURE.
rc=0
fails=()
actions=()
unknowns=()

authed=0
if _authenticated "$backend" "$ips"; then authed=1; fi

if [ "$authed" = "1" ]; then
  echo "PASS  authed    : this node holds a tailnet identity ($ips)"
  # These two are verdicts ONLY once there is an identity to have a verdict about.
  if [ "$backend" != "Running" ]; then
    fails+=("tailscaled backend is '$backend', not 'Running' -- this node carries no
    traffic even though it IS logged in (it holds $ips). Bring it up:  $UP_CMD")
  fi
  if [ "$online" != "true" ]; then
    fails+=("the control plane does not consider this node Online; it cannot be reached.")
  fi
else
  unknowns+=("THIS NODE HAS NEVER AUTHENTICATED to a tailnet (backend '$backend',
    addresses: ${ips:-<none>}). That is the EXPECTED state immediately after
    \`apply-tailscale.sh\`, which installs and starts the service but deliberately does
    NOT log in -- \`tailscale up\` needs a browser. It is NOT a node-side defect, and
    every claim below that depends on a tailnet identity is reported UNKNOWN rather than
    guessed at. Authenticate it, then re-run this check:
      $UP_CMD")
fi

# --- what this node ADVERTISES (its own prefs) ---------------------------------------
if [ "$prefs_src" = "none" ]; then
  unknowns+=("could not read \`tailscale debug prefs\`, so what this node ADVERTISES is
    UNKNOWN. Deliberately not reported as 'advertises nothing' -- that would be a
    definitive claim derived from a file that was never read. It is equally not reported
    as a node-side FAIL: a file this script could not read is not evidence of a defect.")
elif [ "$authed" != "1" ]; then
  unknowns+=("what this node advertises cannot be judged before it authenticates:
    \`AdvertiseRoutes\` is set BY \`tailscale up\`, which has never run here.
    (prefs currently say: ${advertised:-<none>})")
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
  if [ "$authed" != "1" ]; then
    unknowns+=("whether $SUBNET is APPROVED in the admin console cannot be known before
    this node authenticates -- the control plane has no machine here yet, so there is
    nothing to approve and \`PrimaryRoutes\` is empty for a reason that is not refusal.
    Re-run after \`tailscale up\`.")
  elif _in_csv "$SUBNET" "$primary"; then
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
  if [ "$authed" != "1" ]; then
    unknowns+=("whether this client accepts subnet routes cannot be judged before it
    authenticates: \`RouteAll\` is the \`--accept-routes\` pref and is set BY
    \`tailscale up\`. (prefs currently say RouteAll=$route_all)")
  elif [ "$route_all" = "true" ]; then
    echo "PASS  acceptrt  : this client accepts subnet routes (RouteAll / --accept-routes)"
  elif [ "$prefs_src" = "prefs" ]; then
    actions+=("this client does not accept subnet routes (RouteAll=$route_all), so the
    workbench's $SUBNET advertisement will not be installed here. Fix:  $UP_CMD")
  fi
  if ip -4 -o route show 2>/dev/null | awk -v s="$SUBNET" '$1==s' | grep -q 'dev tailscale'; then
    echo "PASS  lanroute  : $SUBNET is installed here via a tailscale interface"
  elif [ "$authed" != "1" ]; then
    unknowns+=("$SUBNET is not installed here via tailscale, but this node has not
    authenticated, so no route COULD have been installed. Not a finding yet.")
  else
    actions+=("$SUBNET is not in this host's routing table via tailscale. Either the
    route is not approved in the admin console, or --accept-routes is off here, or the
    workbench is not advertising. Check the workbench with --role server.")
  fi
fi

# 🔴 KEY EXPIRY -- and the ABSENT case is the one that matters. `Self.KeyExpiry` missing
# is ambiguous between (a) expiry genuinely disabled, (b) the node has no node key at
# all because it never authenticated, and (c) a build that does not emit the field.
# Resolving that absence to (a) is how this printed `PASS keyexpiry : disabled` for a
# node that had never logged in -- the reassuring branch, on the single item most likely
# to kill this path silently mid-trip. "Disabled" is claimed ONLY when there IS a node
# key for expiry to have been disabled on.
if [ -n "$expiry" ]; then
  actions+=("node key expiry is ENABLED: it expires $expiry, in $days days.
    Tailscale's default is 180 days, shorter than a months-long trip, and when it lapses
    this backup path goes dark with no local error. Disabling it is an ADMIN-CONSOLE
    action that cannot be scripted:
      https://login.tailscale.com/admin/machines -> this machine -> ... ->
      Disable key expiry")
elif [ "$authed" != "1" ]; then
  unknowns+=("NODE KEY EXPIRY IS UNKNOWN, not disabled. \`Self.KeyExpiry\` is absent, and
    on a node that has never authenticated that means the control plane has never issued
    a node key -- there is nothing yet for expiry to be on or off for. Absence is
    ambiguous, and this is the one claim where guessing the reassuring answer is most
    expensive: the default is 180 days, shorter than the trip, and the lapse is silent.
    Re-run after \`tailscale up\` -- and expect it to say ENABLED, because that is the
    default a new node gets.")
else
  echo "PASS  keyexpiry : disabled -- this node IS authenticated and the daemon reports no"
  echo "                  KeyExpiry, so this node key will not expire mid-trip"
fi

echo
if [ ${#fails[@]} -gt 0 ]; then
  echo "FAIL: this host is NOT carrying a usable tailscale path."
  for f in "${fails[@]}"; do printf '  - %s\n' "$f"; done
  rc=1
fi
# 🔴 UNKNOWN OUTRANKS ACTION-REQUIRED and is outranked by FAIL. A run that could not
# evaluate half its claims must not be reported as "everything on this host is correct,
# just go click a button" -- rc 3 says the node side is DONE, and rc 4 says it is not
# yet knowable. Both are equally not a reason to roll back a switch.
if [ ${#unknowns[@]} -gt 0 ]; then
  echo "NOT YET DETERMINABLE (no defect found -- these claims cannot be evaluated yet):"
  for u in "${unknowns[@]}"; do printf '  - %s\n' "$u"; done
  if [ "$rc" = "0" ]; then rc=4; fi
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
