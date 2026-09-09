#!/usr/bin/env bash
# shellcheck shell=bash
# host-role.sh — canonical per-host identity for the devrc two-host fleet.
#
# The shebang is load-bearing despite this file normally being SOURCED: the file
# is mode 755, so `./scripts/lib/host-role.sh --detect-role` is a thing an
# operator will do, and without it the kernel hands the file to /bin/sh. Under a
# dash-ish /bin/sh, ${BASH_SOURCE[0]} expands to the empty string, the
# executed-not-sourced guard at the bottom never matches, and --detect-role exits
# 0 having printed NOTHING — a silent no-op from a probe you are trusting.
#
# 🔴 SINGLE SOURCE OF TRUTH. Both NixOS hosts report hostname `nixos`, so nothing
# can tell them apart by name. This file owns the ONE predicate that can: derive
# the physical host from its local IPv4 addresses. Every consumer SOURCES this —
# `scripts/ship.sh` (the converger) and `scripts/drift-check.sh` (the passive
# deadman) — because a second copy of this predicate WILL drift and be wrong.
#
# It already has been wrong once, expensively: ship.sh originally hardcoded
# "local == workbench" plus an SSH target that was actually the laptop's own
# address, so running it on the laptop mislabelled the laptop as workbench and
# SSH'd to itself while the real workbench was never converged.
#
# SOURCE-ONLY, no side effects. Sourcing this defines variables + functions and
# does nothing else — it must stay safe to pull into any script at any point.
# For a standalone probe it is ALSO directly executable:
#     bash scripts/lib/host-role.sh --detect-role ["<ip ip ...>"]
# printing workbench|laptop|unknown (with no IP list: this machine's own).

# --- Canonical per-host identity ----------------------------------------------
# Primary signal: the stable LAN address (192.168.50.x) — reliable across boots.
# Secondary signal: the 10.42.x address (less stable) — fallback only.
WORKBENCH_IP_PRIMARY="192.168.50.250"
WORKBENCH_IP_SECONDARY="10.42.0.30"
LAPTOP_IP_PRIMARY="192.168.50.155"
LAPTOP_IP_SECONDARY="10.42.0.100"
SSH_USER_DEFAULT="zach"
WORKBENCH_SSH_DEFAULT="${SSH_USER_DEFAULT}@${WORKBENCH_IP_PRIMARY}"
LAPTOP_SSH_DEFAULT="${SSH_USER_DEFAULT}@${LAPTOP_IP_PRIMARY}"
# 🔴 The SECONDARY (nebula) targets exist because the LAN address is only
# reachable from the same network, and the laptop is routinely NOT on it.
# MEASURED 2026-09-09: `ship.sh` reached for 192.168.50.155, ssh timed out, the
# laptop leg exited 255 and the run reported `converge exited 255` — while the
# host was up and answering on 10.42.0.100 the whole time. A timed-out leg reads
# as a DEAD HOST, so the laptop silently stopped receiving deploys while looking
# merely unreachable. Derived from the IP constants above rather than spelled
# again: one address, one place.
WORKBENCH_SSH_SECONDARY="${SSH_USER_DEFAULT}@${WORKBENCH_IP_SECONDARY}"
LAPTOP_SSH_SECONDARY="${SSH_USER_DEFAULT}@${LAPTOP_IP_SECONDARY}"

# detect_role <space-or-comma-separated ipv4 list> -> workbench|laptop|unknown
# Pure + testable: takes an IP list as input (no live-machine calls), so it can
# be unit-tested with injected addresses. Precedence is deterministic:
#   1. primary LAN addresses (192.168.50.x) are matched before secondary ones;
#   2. within a pass, WORKBENCH is matched before LAPTOP — so an (unexpected)
#      list carrying BOTH hosts' primary addresses resolves to "workbench".
detect_role() {
  local ips="${1:-}"
  ips="${ips//,/ }"
  local ip
  for ip in $ips; do [ "$ip" = "$WORKBENCH_IP_PRIMARY" ] && { echo workbench; return 0; }; done
  for ip in $ips; do [ "$ip" = "$LAPTOP_IP_PRIMARY" ]    && { echo laptop;    return 0; }; done
  for ip in $ips; do [ "$ip" = "$WORKBENCH_IP_SECONDARY" ] && { echo workbench; return 0; }; done
  for ip in $ips; do [ "$ip" = "$LAPTOP_IP_SECONDARY" ]    && { echo laptop;    return 0; }; done
  echo unknown
  return 0
}

# local_ipv4s — global-scope IPv4 addresses of THIS machine, one per line
# (scope global drops 127.0.0.1). Used as detect_role's input at runtime.
local_ipv4s() {
  ip -4 -o addr show scope global 2>/dev/null | awk '{print $4}' | cut -d/ -f1
}

# resolve_local_role — the role of THIS machine, honouring an explicit override.
# $SHIP_ROLE (shared with ship.sh, deliberately: one override for one fleet)
# wins when set; otherwise detection runs. Prints workbench|laptop|unknown.
resolve_local_role() {
  if [ -n "${SHIP_ROLE:-}" ]; then echo "$SHIP_ROLE"; return 0; fi
  detect_role "$(local_ipv4s | tr '\n' ' ')"
}

# remote_role_of <local-role> -> the OTHER host's role (empty if unknown).
remote_role_of() {
  case "${1:-}" in
    workbench) echo laptop ;;
    laptop)    echo workbench ;;
    *)         echo "" ;;
  esac
}

# remote_ssh_of <local-role> -> ssh target of the OTHER host.
# $REMOTE_SSH overrides unconditionally. $LAPTOP_SSH is a back-compat override
# that applies ONLY when the remote host actually IS the laptop — applying it
# from the laptop would point at the laptop itself.
remote_ssh_of() {
  if [ -n "${REMOTE_SSH:-}" ]; then echo "$REMOTE_SSH"; return 0; fi
  case "${1:-}" in
    workbench) echo "${LAPTOP_SSH:-$LAPTOP_SSH_DEFAULT}" ;;
    laptop)    echo "$WORKBENCH_SSH_DEFAULT" ;;
    *)         echo "" ;;
  esac
}

# remote_ssh_candidates_of <local-role> -> ssh targets to TRY, one per line,
# most-preferred first: the LAN address, then the nebula one.
#
# 🔴 AN EXPLICIT TARGET IS NEVER SECOND-GUESSED. When $REMOTE_SSH or (for the
# laptop) $LAPTOP_SSH is set, that ONE target is the whole list: an operator who
# names a host means that host, and silently trying a different machine after it
# fails to answer is how a converge lands somewhere nobody asked for. Only the
# DERIVED defaults get a fallback.
#
# Pure: prints candidates, contacts nothing. The probing lives in
# first_reachable_ssh so this stays unit-testable with no network.
remote_ssh_candidates_of() {
  if [ -n "${REMOTE_SSH:-}" ]; then echo "$REMOTE_SSH"; return 0; fi
  case "${1:-}" in
    workbench)
      if [ -n "${LAPTOP_SSH:-}" ]; then echo "$LAPTOP_SSH"; return 0; fi
      echo "$LAPTOP_SSH_DEFAULT"; echo "$LAPTOP_SSH_SECONDARY" ;;
    laptop)
      echo "$WORKBENCH_SSH_DEFAULT"; echo "$WORKBENCH_SSH_SECONDARY" ;;
    *) echo "" ;;
  esac
}

# first_reachable_ssh <target>... -> the first target that answers, or empty.
#
# Probes with BatchMode (never prompts) and a bounded ConnectTimeout, running
# `true` rather than anything that could change state. Prints the winner on
# stdout; every diagnostic goes to stderr so callers can capture the target
# cleanly. Returns 1 when NOTHING answered — the caller must treat that as a
# genuinely unreachable host, not as "use the first one anyway".
#
# 🔴 $SSH_PROBE_CMD replaces the probe wholesale (it receives the target as $1
# and signals reachability by exit status). It exists so the selection logic can
# be tested without a network — a probe that always fails, or one that answers
# only for a named address, is how the fallback is driven red.
first_reachable_ssh() {
  local timeout="${SSH_PROBE_TIMEOUT:-5}" target
  for target in "$@"; do
    [ -n "$target" ] || continue
    if [ -n "${SSH_PROBE_CMD:-}" ]; then
      if "$SSH_PROBE_CMD" "$target"; then echo "$target"; return 0; fi
    # 🔴 ONE LINE, not a continuation. `test_drift_check.py`'s command extractor
    # reads the first token of each line as a command, so a wrapped invocation
    # makes it see the continuation's leading word (`target`) as a program and
    # report it as unaccounted-for on the unit PATH. Keeping it on one line lets
    # it see `ssh`, which the table already carries as pkgs.openssh.
    elif ssh -o BatchMode=yes -o ConnectTimeout="$timeout" -o StrictHostKeyChecking=accept-new "$target" true >/dev/null 2>&1; then
      echo "$target"; return 0
    fi
    echo "ship: $target did not answer" >&2
  done
  return 1
}

# Standalone probe mode — only when EXECUTED, never when sourced.
# ${BASH_SOURCE[0]} == $0 exactly when this file is the script being run.
if [ "${BASH_SOURCE[0]}" = "${0}" ]; then
  case "${1:-}" in
    --detect-role)
      # With an explicit (even empty) IP-list arg, detect from it (testable);
      # with no second arg, detect from THIS machine's own addresses.
      if [ "$#" -ge 2 ]; then detect_role "$2"; else detect_role "$(local_ipv4s | tr '\n' ' ')"; fi
      exit 0 ;;
    *)
      echo "host-role.sh: source me, or run: bash $0 --detect-role [\"<ip ip ...>\"]" >&2
      exit 2 ;;
  esac
fi
