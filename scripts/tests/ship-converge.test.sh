#!/usr/bin/env bash
# ship-converge.test.sh — exercises the CONVERGE landing logic of scripts/ship.sh
# against THROWAWAY temp git repos. Does NOT touch ~/workspace/devrc and never
# runs `home-manager switch` (SHIP_NO_SWITCH=1).
#
# Each scenario builds: a bare `origin` whose `main` is ahead by >=1 commit, and
# a working clone placed in some pre-state, then runs ship.sh --no-local
# --no-laptop-equivalent by invoking only the local converge with the temp repo
# via SHIP_REPO + SHIP_NO_SWITCH=1, and asserts post-state branch==main &&
# HEAD==origin/main (or the diverged exit code for scenario d).
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SHIP="$SCRIPT_DIR/../ship.sh"

FAILS=0
PASS() { echo "PASS: $1"; }
FAIL() { echo "FAIL: $1"; FAILS=$((FAILS + 1)); }

# Run ship.sh against a temp repo with home-manager skipped, local only.
# Captures output + exit code into globals OUT / RC.
run_ship() {
  local repo="$1"
  OUT=$(SHIP_REPO="$repo" SHIP_NO_SWITCH=1 bash "$SHIP" --no-laptop 2>&1)
  RC=$?
}

# Build a throwaway origin (bare) + working clone. The clone starts checked out
# on `main` at a commit that is one BEHIND origin/main (origin advanced after
# clone), so origin/main is always >=1 ahead. Echoes the working repo path.
make_repo() {
  local root origin work
  root=$(mktemp -d /tmp/ship-test.XXXXXX)
  origin="$root/origin.git"
  work="$root/work"

  git init -q --bare "$origin"
  # seed origin via a builder clone
  local builder="$root/builder"
  git clone -q "$origin" "$builder"
  git -C "$builder" config user.email t@t && git -C "$builder" config user.name t
  git -C "$builder" checkout -q -b main
  echo base > "$builder/f"
  echo stable > "$builder/stable.txt"   # tracked file the ahead-commit never touches
  git -C "$builder" add f stable.txt && git -C "$builder" commit -q -m base
  git -C "$builder" push -q -u origin main

  # working clone at the base commit (so far == origin/main)
  git clone -q "$origin" "$work"
  git -C "$work" config user.email t@t && git -C "$work" config user.name t
  git -C "$work" checkout -q main

  # advance origin/main by one commit (origin is now ahead of work)
  echo more >> "$builder/f"
  git -C "$builder" commit -qam ahead
  git -C "$builder" push -q origin main

  echo "$work"
}

git_branch() { git -C "$1" symbolic-ref --quiet --short HEAD || echo DETACHED; }
git_head()   { git -C "$1" rev-parse HEAD; }
origin_main() { git -C "$1" rev-parse origin/main; }

assert_landed() {
  local repo="$1" name="$2"
  git -C "$repo" fetch origin -q
  local b h t
  b=$(git_branch "$repo"); h=$(git_head "$repo"); t=$(origin_main "$repo")
  if [ "$b" = main ] && [ "$h" = "$t" ]; then
    PASS "$name (landed on main @ origin/main)"
  else
    FAIL "$name — branch=$b HEAD=$h origin/main=$t"
    echo "  --- ship output ---"; echo "$OUT" | sed 's/^/  /'
  fi
}

# (a) host on a feature branch that is an ancestor of origin/main -> must land on main
scenario_a() {
  local repo; repo=$(make_repo)
  git -C "$repo" checkout -q -b feat/ancestor   # tip == base, ancestor of origin/main
  run_ship "$repo"
  assert_landed "$repo" "a: feature branch (ancestor of origin/main)"
}

# (b) already on main but behind -> fast-forwards, stays on main
scenario_b() {
  local repo; repo=$(make_repo)   # already on main, behind
  run_ship "$repo"
  assert_landed "$repo" "b: on main but behind"
}

# (c) feature branch with dirty tracked + untracked WIP -> lands on main AND WIP restored
scenario_c() {
  local repo; repo=$(make_repo)
  git -C "$repo" checkout -q -b feat/wip
  # Dirty a tracked file (stable.txt) that upstream's ahead-commit never touches,
  # so the post-landing pop is clean — plus an untracked file.
  echo "tracked change" >> "$repo/stable.txt"  # dirty tracked
  echo "untracked content" > "$repo/newfile"   # untracked
  run_ship "$repo"
  # landing assertion
  git -C "$repo" fetch origin -q
  local b h t ok=1
  b=$(git_branch "$repo"); h=$(git_head "$repo"); t=$(origin_main "$repo")
  [ "$b" = main ] && [ "$h" = "$t" ] || ok=0
  # WIP restored: tracked change still present, untracked file present
  grep -q "tracked change" "$repo/stable.txt" || ok=0
  { [ -f "$repo/newfile" ] && grep -q "untracked content" "$repo/newfile"; } || ok=0
  # no stash left behind
  [ -z "$(git -C "$repo" stash list)" ] || ok=0
  if [ "$ok" = 1 ]; then
    PASS "c: dirty tracked+untracked WIP -> landed on main, WIP popped"
  else
    FAIL "c: branch=$b HEAD=$h origin/main=$t stash='$(git -C "$repo" stash list)'"
    echo "  --- ship output ---"; echo "$OUT" | sed 's/^/  /'
  fi
}

# (d) main with a diverged un-pushed commit -> skips with diverged exit 8, WIP restored, not mid-stash
scenario_d() {
  local repo; repo=$(make_repo)
  # commit on local main that diverges from origin/main (origin advanced separately)
  echo "local-only" > "$repo/local.txt"
  git -C "$repo" add local.txt && git -C "$repo" commit -q -m "local divergent commit"
  # add WIP to confirm it is restored, not stranded in stash
  echo "wip tracked" >> "$repo/f"
  echo "wip untracked" > "$repo/wip-untracked"
  run_ship "$repo"
  local ok=1
  [ "$RC" = 8 ] || ok=0
  # should still be on main (never left it), with the local commit intact
  [ "$(git_branch "$repo")" = main ] || ok=0
  git -C "$repo" log -1 --format=%s | grep -q "local divergent commit" || ok=0
  # WIP restored (popped), not left in stash
  grep -q "wip tracked" "$repo/f" || ok=0
  [ -f "$repo/wip-untracked" ] || ok=0
  [ -z "$(git -C "$repo" stash list)" ] || ok=0
  if [ "$ok" = 1 ]; then
    PASS "d: diverged un-pushed main -> exit 8, WIP restored, not mid-stash"
  else
    FAIL "d: rc=$RC branch=$(git_branch "$repo") stash='$(git -C "$repo" stash list)'"
    echo "  --- ship output ---"; echo "$OUT" | sed 's/^/  /'
  fi
}

echo "=== ship.sh CONVERGE landing-logic tests ==="
scenario_a
scenario_b
scenario_c
scenario_d
echo "==="
if [ "$FAILS" = 0 ]; then
  echo "ALL PASS"
  exit 0
else
  echo "$FAILS scenario(s) FAILED"
  exit 1
fi
