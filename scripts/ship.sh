#!/usr/bin/env bash
# ship — converge BOTH NixOS hosts (workbench + laptop) to origin/main + verify.
#
# Agent-callable deterministic deploy primitive. Replaces the manual,
# error-prone per-host ritual (stash -> pull --ff-only -> home-manager
# switch -> stash pop -> verify) with one idempotent command, so a config
# change lands identically on both machines in a single tool call.
#
# Host identity: BOTH machines have hostname `nixos`, so we CANNOT tell them
# apart by hostname. Instead we detect the physical host from its local IPv4
# addresses (see detect_role) and derive the REMOTE (the other host) from that.
# This is direction-agnostic: run it from EITHER host and it converges local +
# the other one. Historically it hardcoded "local == workbench" and an SSH
# target that was actually the laptop's own address — so running it on the
# laptop mislabelled the laptop as workbench and SSH'd to itself while the real
# workbench was never converged. Detection fixes that.
#
# Scope: home-manager (user-level) — the bulk of this repo's changes.
# It does NOT run `sudo nixos-rebuild` (needs an interactive password);
# system/i3 changes are surfaced as a remaining manual step, not attempted.
# It ALSO rsyncs the per-host Claude skills (~/.claude/skills/, not in git/nix)
# from the WORKBENCH to the laptop so the skill set does not drift. The workbench
# is the source of truth: the rsync ONLY runs when the LOCAL host is the
# workbench (workbench -> laptop). Run from the laptop it is SKIPPED, never
# pushing laptop skills back onto the workbench (which would clobber the source).
#
# Verifier (cheap + automatic): each host ends ON the `main` BRANCH at
# HEAD == origin/main AND `home-manager switch` exits 0. It is not enough for
# HEAD to merely equal main's commit — a feature branch whose tip is an
# ancestor of origin/main could be fast-forwarded to that commit and pass a
# commit-only check while leaving the host stranded on the feature branch with
# a stale local `main`. So we explicitly `git checkout main` and land there.
# Diverged local `main` (un-pushed commits) is reported and that host's switch
# is skipped — never auto-rebased.
#
# Usage:
#   scripts/ship.sh              # converge local host + the other (remote) host
#   scripts/ship.sh --no-remote  # local host only  (alias: --no-laptop)
#   scripts/ship.sh --no-local   # remote host only
#   scripts/ship.sh --no-switch  # land on main + verify git state, SKIP home-manager (test/dry-run)
#   scripts/ship.sh --detect-role # print detected local role (workbench|laptop|unknown) and exit
#
# Env overrides:
#   SHIP_ROLE     force the local role (workbench|laptop) when detection fails/differs
#   REMOTE_SSH    ssh target for the OTHER host (default derived from role)
#   LAPTOP_SSH    back-compat: ssh target used ONLY when the remote host is the laptop
#   SHIP_REPO     repo path the CONVERGE routine operates on (default $HOME/workspace/devrc)
#   SHIP_NO_SWITCH=1  same as --no-switch: run full git-landing logic, skip home-manager switch
set -uo pipefail

# --- Canonical per-host identity (both hosts share hostname `nixos`) -----------
# Primary signal: the stable LAN address (192.168.50.x) — reliable across boots.
# Secondary signal: the 10.42.x address (less stable) — fallback only.
WORKBENCH_IP_PRIMARY="192.168.50.250"
WORKBENCH_IP_SECONDARY="10.42.0.30"
LAPTOP_IP_PRIMARY="192.168.50.155"
LAPTOP_IP_SECONDARY="10.42.0.100"
WORKBENCH_SSH_DEFAULT="zach@192.168.50.250"
LAPTOP_SSH_DEFAULT="zach@192.168.50.155"

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

# Hidden mode: print the role detected from an injected IP list (for tests) or,
# with no list, from this machine's own addresses. Prints role, exits 0.
if [ "${1:-}" = "--detect-role" ]; then
  # With an explicit (even empty) IP-list arg, detect from it (testable);
  # with no second arg, detect from THIS machine's own addresses.
  if [ "$#" -ge 2 ]; then detect_role "$2"; else detect_role "$(local_ipv4s | tr '\n' ' ')"; fi
  exit 0
fi

SHIP_REPO="${SHIP_REPO:-$HOME/workspace/devrc}"
SHIP_NO_SWITCH="${SHIP_NO_SWITCH:-0}"
DO_LOCAL=1
DO_REMOTE=1
for a in "$@"; do
  case "$a" in
    --no-remote|--no-laptop) DO_REMOTE=0 ;;   # skip the OTHER (remote) host
    --no-local)  DO_LOCAL=0 ;;                # skip THIS (local) host
    --no-switch) SHIP_NO_SWITCH=1 ;;
    --detect-role) : ;;                        # handled above
    -h|--help)   sed -n '2,48p' "$0"; exit 0 ;;
    *) echo "unknown arg: $a" >&2; exit 2 ;;
  esac
done

# --- Resolve local role (detection, override-able) + derive the remote target --
SHIP_ROLE="${SHIP_ROLE:-}"
if [ -z "$SHIP_ROLE" ]; then
  SHIP_ROLE="$(detect_role "$(local_ipv4s | tr '\n' ' ')")"
fi
if [ "$SHIP_ROLE" != workbench ] && [ "$SHIP_ROLE" != laptop ]; then
  echo "ship: could not identify this host (role='$SHIP_ROLE')." >&2
  echo "  local IPv4s: $(local_ipv4s | tr '\n' ' ')" >&2
  echo "  expected a workbench ($WORKBENCH_IP_PRIMARY) or laptop ($LAPTOP_IP_PRIMARY) address." >&2
  echo "  override with SHIP_ROLE=workbench|laptop to force." >&2
  exit 6
fi

if [ "$SHIP_ROLE" = workbench ]; then
  REMOTE_ROLE=laptop
  # remote is the laptop -> honor the back-compat LAPTOP_SSH override here.
  REMOTE_SSH="${REMOTE_SSH:-${LAPTOP_SSH:-$LAPTOP_SSH_DEFAULT}}"
else
  REMOTE_ROLE=workbench
  # remote is the workbench -> LAPTOP_SSH must NOT apply (it is the laptop itself).
  REMOTE_SSH="${REMOTE_SSH:-$WORKBENCH_SSH_DEFAULT}"
fi

# Self-contained converge routine, run identically on each host (local via
# bash -c, remote via ssh). Single source of truth for the sequence.
CONVERGE='
set -uo pipefail
repo="${SHIP_REPO:-$HOME/workspace/devrc}"
no_switch="${SHIP_NO_SWITCH:-0}"
cd "$repo" || { echo "[$(hostname)] no repo at $repo"; exit 3; }
host=$(hostname)
git fetch origin -q || { echo "[$host] git fetch failed"; exit 4; }
target=$(git rev-parse origin/main)

# 1) Stash any WIP (incl. untracked, which an upcoming checkout could clobber)
#    so we can safely land on the `main` branch.
dirty=0
if ! git diff --quiet \
   || ! git diff --cached --quiet \
   || [ -n "$(git ls-files --others --exclude-standard)" ]; then
  dirty=1
  git stash push -q -u -m ship-auto || { echo "[$host] stash failed"; exit 5; }
fi

# 2) Land on the `main` branch (not merely main'"'"'s commit). Create a local
#    main tracking origin/main if it does not exist yet.
branch=$(git symbolic-ref --quiet --short HEAD || echo "")
if [ "$branch" != "main" ]; then
  if git checkout main -q 2>/dev/null || git checkout -B main origin/main -q; then :; else
    echo "[$host] could not checkout main"
    [ "$dirty" = 1 ] && git stash pop -q
    exit 9
  fi
fi

# 3) Fast-forward main to origin/main. A non-ff (diverged / un-pushed commits
#    on main) is reported and skipped — never auto-rebased, never switched.
head=$(git rev-parse HEAD)
if [ "$head" != "$target" ]; then
  if git merge --ff-only origin/main -q; then
    echo "[$host] fast-forwarded main $head -> $target"
  else
    echo "[$host] main not a fast-forward to origin/main (un-pushed/diverged commits) — skipping switch"
    [ "$dirty" = 1 ] && git stash pop -q
    exit 8
  fi
else
  echo "[$host] main already at origin/main ($target)"
fi

# 4) Restore WIP.
if [ "$dirty" = 1 ] && ! git stash pop -q; then
  echo "[$host] STASH POP CONFLICT — local changes kept in stash, resolve manually"
  exit 7
fi

# 5) home-manager switch (skippable for tests/dry-run).
if [ "$no_switch" = 1 ]; then
  echo "[$host] (SHIP_NO_SWITCH) skipping home-manager switch"
else
  log=$(mktemp /tmp/ship-hm.XXXXXX.log)
  if ! home-manager switch --flake "$repo" --impure >"$log" 2>&1; then
    echo "[$host] home-manager switch FAILED:"; tail -4 "$log"; exit 9
  fi
fi

# 6) Verify: must be ON branch main AND HEAD == origin/main.
now=$(git rev-parse HEAD)
nowbranch=$(git symbolic-ref --quiet --short HEAD || echo "DETACHED")
if [ "$now" = "$target" ] && [ "$nowbranch" = "main" ]; then
  echo "[$host] ✅ VERIFIED — on branch main at origin/main + switched"
else
  echo "[$host] ❌ VERIFY FAILED — branch=$nowbranch HEAD=$now origin/main=$target"; exit 11
fi
'

rc=0

if [ "$DO_LOCAL" = 1 ]; then
  echo "=== local ($SHIP_ROLE) ==="
  SHIP_REPO="$SHIP_REPO" SHIP_NO_SWITCH="$SHIP_NO_SWITCH" bash -c "$CONVERGE" || rc=$?
  echo
fi

if [ "$DO_REMOTE" = 1 ]; then
  echo "=== remote ($REMOTE_ROLE — $REMOTE_SSH) ==="
  # Pass the switch toggle remotely; SHIP_REPO stays host-default ($HOME/workspace/devrc).
  if ssh -o ConnectTimeout=10 "$REMOTE_SSH" "SHIP_NO_SWITCH=$SHIP_NO_SWITCH; $CONVERGE"; then :; else
    remrc=$?
    rc=$remrc
    echo "[$REMOTE_ROLE] converge exited $remrc"
  fi

  # Sync per-host Claude skills (~/.claude/skills/ — NOT in git/nix; the workbench
  # is the source of truth where they're edited). Keeps the laptop's skill set from
  # drifting. Additive (NO --delete) so a laptop-only skill is never clobbered.
  # DIRECTION-SAFE: only ever push workbench -> laptop. So the rsync runs ONLY when
  # THIS host is the workbench (remote == laptop). Run FROM the laptop we SKIP it —
  # never pushing the laptop's skills onto the workbench (would clobber the source).
  # Auxiliary + best-effort: a failure warns but never fails the ship. Skipped on a
  # --no-switch dry-run (it is a real file change, like the home-manager switch).
  if [ "$SHIP_NO_SWITCH" = 1 ]; then
    : # dry-run: no file changes
  elif [ "$SHIP_ROLE" != workbench ]; then
    echo "[$REMOTE_ROLE] skills sync skipped (workbench is source of truth; not pushing laptop -> workbench)"
  elif [ -d "$HOME/.claude/skills" ]; then
    if rsync -az -e "ssh -o ConnectTimeout=10" "$HOME/.claude/skills/" "$REMOTE_SSH:.claude/skills/" 2>/dev/null; then
      echo "[$REMOTE_ROLE] skills synced (~/.claude/skills/)"
    else
      echo "[$REMOTE_ROLE] ⚠ skill sync failed (non-fatal — check rsync on both hosts)"
    fi
  fi
  echo
fi

if [ "$rc" = 0 ]; then
  echo "ship: converged + verified at origin/main (local=$SHIP_ROLE remote=$REMOTE_ROLE)."
else
  echo "ship: incomplete (rc=$rc) — see per-host lines above."
  echo "  rc6=host-unidentified  rc8=diverged(needs rebase)  rc7=stash-pop-conflict  rc9=switch-failed"
fi
exit "$rc"
