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
# It ALSO rsyncs the per-host Claude skills (~/.claude/skills/, not in git/nix)
# from the workbench to the laptop so the skill set does not drift between hosts.
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
#   scripts/ship.sh              # converge local (workbench) + laptop
#   scripts/ship.sh --no-laptop  # local only
#   scripts/ship.sh --no-local   # laptop only
#   scripts/ship.sh --no-switch  # land on main + verify git state, SKIP home-manager (test/dry-run)
#
# Env overrides:
#   LAPTOP_SSH    laptop ssh target (default zach@10.42.0.100)
#   SHIP_REPO     repo path the CONVERGE routine operates on (default $HOME/workspace/devrc)
#   SHIP_NO_SWITCH=1  same as --no-switch: run full git-landing logic, skip home-manager switch
set -uo pipefail

LAPTOP_SSH="${LAPTOP_SSH:-zach@10.42.0.100}"
SHIP_REPO="${SHIP_REPO:-$HOME/workspace/devrc}"
SHIP_NO_SWITCH="${SHIP_NO_SWITCH:-0}"
DO_LOCAL=1
DO_LAPTOP=1
for a in "$@"; do
  case "$a" in
    --no-laptop) DO_LAPTOP=0 ;;
    --no-local)  DO_LOCAL=0 ;;
    --no-switch) SHIP_NO_SWITCH=1 ;;
    -h|--help)   sed -n '2,33p' "$0"; exit 0 ;;
    *) echo "unknown arg: $a" >&2; exit 2 ;;
  esac
done

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
  echo "=== local (workbench) ==="
  SHIP_REPO="$SHIP_REPO" SHIP_NO_SWITCH="$SHIP_NO_SWITCH" bash -c "$CONVERGE" || rc=$?
  echo
fi

if [ "$DO_LAPTOP" = 1 ]; then
  echo "=== laptop ($LAPTOP_SSH) ==="
  # Pass the switch toggle remotely; SHIP_REPO stays host-default ($HOME/workspace/devrc).
  if ssh -o ConnectTimeout=10 "$LAPTOP_SSH" "SHIP_NO_SWITCH=$SHIP_NO_SWITCH; $CONVERGE"; then :; else
    laprc=$?
    rc=$laprc
    echo "[laptop] converge exited $laprc"
  fi

  # Sync per-host Claude skills (~/.claude/skills/ — NOT in git/nix; the workbench
  # is the source of truth where they're edited). Keeps the laptop's skill set from
  # drifting. Additive (NO --delete) so a laptop-only skill is never clobbered.
  # Auxiliary + best-effort: a failure warns but never fails the ship. Skipped on a
  # --no-switch dry-run (it is a real file change, like the home-manager switch).
  if [ "$SHIP_NO_SWITCH" != 1 ] && [ -d "$HOME/.claude/skills" ]; then
    if rsync -az -e "ssh -o ConnectTimeout=10" "$HOME/.claude/skills/" "$LAPTOP_SSH:.claude/skills/" 2>/dev/null; then
      echo "[laptop] skills synced (~/.claude/skills/)"
    else
      echo "[laptop] ⚠ skill sync failed (non-fatal — check rsync on both hosts)"
    fi
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
