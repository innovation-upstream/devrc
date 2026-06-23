#!/usr/bin/env bash
# ship — converge both hosts (workbench + laptop) to origin/main and verify.
#
# Agent-callable deterministic deploy primitive. Replaces the manual,
# error-prone per-host ritual (stash -> pull --ff-only -> home-manager
# switch -> stash pop -> verify) with one idempotent command, so a config
# change lands identically on both machines in a single tool call.
#
# Scope: home-manager (user-level) — the bulk of this repo's changes.
# It does NOT run `sudo nixos-rebuild` (needs an interactive password);
# system/i3 changes are surfaced as a remaining manual step, not attempted.
#
# Verifier (cheap + automatic): each host ends at HEAD == origin/main AND
# `home-manager switch` exits 0. Diverged local history (un-pushed commits)
# is reported and that host's switch is skipped — never auto-rebased.
#
# Usage:
#   scripts/ship.sh              # converge local (workbench) + laptop
#   scripts/ship.sh --no-laptop  # local only
#   scripts/ship.sh --no-local   # laptop only
#
# Env overrides: LAPTOP_SSH (default zach@10.42.0.100), REPO_PATH
set -uo pipefail

LAPTOP_SSH="${LAPTOP_SSH:-zach@10.42.0.100}"
REPO_PATH="${REPO_PATH:-$HOME/workspace/devrc}"
DO_LOCAL=1
DO_LAPTOP=1
for a in "$@"; do
  case "$a" in
    --no-laptop) DO_LAPTOP=0 ;;
    --no-local)  DO_LOCAL=0 ;;
    -h|--help)   sed -n '2,28p' "$0"; exit 0 ;;
    *) echo "unknown arg: $a" >&2; exit 2 ;;
  esac
done

# Self-contained converge routine, run identically on each host (local via
# bash -c, remote via ssh). Single source of truth for the sequence.
CONVERGE='
set -uo pipefail
repo="$HOME/workspace/devrc"
cd "$repo" || { echo "[$(hostname)] no repo at $repo"; exit 3; }
host=$(hostname)
git fetch origin -q || { echo "[$host] git fetch failed"; exit 4; }
head=$(git rev-parse HEAD)
target=$(git rev-parse origin/main)
if [ "$head" != "$target" ]; then
  if git merge-base --is-ancestor "$head" "$target"; then
    dirty=0
    if ! git diff --quiet || ! git diff --cached --quiet; then
      dirty=1; git stash push -q -u -m ship-auto || { echo "[$host] stash failed"; exit 5; }
    fi
    if ! git pull --ff-only origin main -q; then
      echo "[$host] fast-forward pull failed"
      [ "$dirty" = 1 ] && git stash pop -q
      exit 6
    fi
    if [ "$dirty" = 1 ] && ! git stash pop -q; then
      echo "[$host] STASH POP CONFLICT — local changes kept in stash, resolve manually"
      exit 7
    fi
    echo "[$host] fast-forwarded $head -> $target"
  else
    echo "[$host] not a fast-forward to origin/main (feature branch or un-pushed commits) — skipping switch"
    exit 8
  fi
else
  echo "[$host] already at origin/main ($target)"
fi
log=$(mktemp /tmp/ship-hm.XXXXXX.log)
if ! home-manager switch --flake "$repo" --impure >"$log" 2>&1; then
  echo "[$host] home-manager switch FAILED:"; tail -4 "$log"; exit 9
fi
now=$(git rev-parse HEAD)
if [ "$now" = "$target" ]; then
  echo "[$host] ✅ VERIFIED — at origin/main + switched"
else
  echo "[$host] ❌ VERIFY FAILED — HEAD=$now != origin/main=$target"; exit 11
fi
'

rc=0

if [ "$DO_LOCAL" = 1 ]; then
  echo "=== local (workbench) ==="
  HOME="$HOME" bash -c "$CONVERGE" || rc=$?
  echo
fi

if [ "$DO_LAPTOP" = 1 ]; then
  echo "=== laptop ($LAPTOP_SSH) ==="
  if ssh -o ConnectTimeout=10 "$LAPTOP_SSH" "$CONVERGE"; then :; else
    laprc=$?
    rc=$laprc
    echo "[laptop] converge exited $laprc"
  fi
  echo
fi

if [ "$rc" = 0 ]; then
  echo "ship: both hosts converged + verified at origin/main."
else
  echo "ship: incomplete (rc=$rc) — see per-host lines above."
  echo "  rc8=diverged(needs rebase)  rc7=stash-pop-conflict  rc9=switch-failed"
fi
exit "$rc"
