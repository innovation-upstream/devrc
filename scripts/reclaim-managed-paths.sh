#!/usr/bin/env bash
# reclaim-managed-paths — take back a home-manager path owned by the WRONG WRITER.
#
# ── THE HOLE THIS CLOSES ──────────────────────────────────────────────────────
# home-manager deploys by symlinking $HOME/<path> at a /nix/store file. When
# something else writes a REGULAR FILE at one of those paths, two upstream
# behaviours combine into a silent, permanent handover:
#
#   1. `force = true` does NOT clobber. It only suppresses the COLLISION CHECK
#      (`checkLinkTargets` skips any target under a forced prefix). Nothing is
#      removed by it — read `check-link-targets.sh` in any generation.
#   2. The link step's slow path, for a target that exists and is not a symlink:
#          if [ -e "$targetPath" && ! -L "$targetPath" ] && cmp -s "$src" "$tgt"
#          then  "Skipping '$targetPath' as it is identical to '$sourcePath'"
#      A regular file whose content matches the store copy is DELIBERATELY left
#      in place. Every subsequent switch takes the same branch, forever.
#
# So the population home-manager leaks is exactly "regular file, content
# identical". A file whose content DIFFERS is relinked by the very next switch
# (`ln -Tsf` on the else branch) and needs nobody's help.
#
# 🔴 THAT IS WHY REMOVING THESE IS LOSSLESS BY CONSTRUCTION, and why this script
# refuses to touch anything else. `cmp -s` identical means the bytes on disk are
# the bytes the store already holds: there is nothing to lose. A file that
# differs is a file somebody may have meant, and it is also the case that fixes
# itself — both reasons to leave it alone and REPORT it.
#
# ── MEASURED, TWICE, IN TWO UNRELATED SUBSYSTEMS ──────────────────────────────
#   2026-08-04  ~/.config/i3status-rust/scripts/i3blocks-rigcontrol — a bar
#               script, regular file, byte-identical, still not a link 16 days
#               later.
#   2026-08-19  34 files under ~/.config/opencode/commands/ — an agent ran
#               scripts/opencode/generate-commands.py with its OUTPUT DIRECTORY
#               pointed at the live deploy path instead of /tmp/test-commands
#               (opencode log, run=146c5448, 21:39:22.454Z; the files' mtimes are
#               21:39:22.484–.487). 16 of them were later relinked — precisely
#               the 16 whose skill body had since changed, so the content no
#               longer matched. The other 18 were permanent.
# Prevention for the second one lives in generate-commands.py, which now refuses
# a managed output directory. This script is the REPAIR, and it is generic
# because the class is: nothing here knows what opencode or i3status-rust are.
#
# ── USAGE ─────────────────────────────────────────────────────────────────────
#   scripts/reclaim-managed-paths.sh              # DRY RUN — report only
#   scripts/reclaim-managed-paths.sh --apply      # remove the reclaimable ones
#   RECLAIM_HOME=<dir> RECLAIM_HOME_FILES=<dir> …  # for the test suite
#
# It is called with --apply from `home.activation.reclaimManagedPaths` in
# nix/home.nix, BEFORE checkLinkTargets, so an ordinary `home-manager switch`
# repairs the whole class. That is what keeps drift-check's rc 19 from being a
# permanently-red gate: the finding clears on the next switch, not on a human
# remembering a one-off `rm`.
#
# EXIT CODES
#   0  ran, and reported the examined/reclaimable pair (whether or not --apply)
#   1  usage error
#   2  COULD NOT MEASURE — the home-files manifest is absent or unreadable. Never
#      reported as "0 found": a walk of nothing is not a clean walk.

set -uo pipefail

APPLY=0
HOME_DIR="${RECLAIM_HOME:-$HOME}"
HOME_FILES="${RECLAIM_HOME_FILES:-}"

usage() {
  echo "usage: reclaim-managed-paths.sh [--apply] [--home <dir>] [<home-files-dir>]" >&2
  exit 1
}

while [ $# -gt 0 ]; do
  case "$1" in
    --apply) APPLY=1; shift ;;
    --home) [ $# -ge 2 ] || usage; HOME_DIR="$2"; shift 2 ;;
    -h|--help) usage ;;
    -*) echo "reclaim-managed-paths: unknown flag $1" >&2; usage ;;
    *) HOME_FILES="$1"; shift ;;
  esac
done

if [ -z "$HOME_FILES" ]; then
  state="${XDG_STATE_HOME:-$HOME_DIR/.local/state}"
  HOME_FILES="$state/nix/profiles/home-manager/home-files"
fi

if [ ! -d "$HOME_FILES" ]; then
  echo "managed paths: COULD NOT MEASURE — no home-files manifest at $HOME_FILES"
  echo "  a walk of nothing is not a clean walk; this is not '0 found'."
  exit 2
fi

# The walk is bash builtins plus readlink/cmp — no `find`, for the same reason
# drift-check.sh refuses it: the laptop resolves `find` to BUSYBOX, whose
# unsupported predicates print usage to stderr and EXIT 0, which is how a
# checker wired to nothing reports a confident clean.
EXAMINED=0
RECLAIMABLE=0
KEPT=0
ABSENT=0
r_list=""
k_list=""

r_walk() { # r_walk <dir-in-manifest> <relative-prefix>
  local E BASE REL TGT
  for E in "$1"/* "$1"/.*; do
    BASE="${E##*/}"
    [ "$BASE" = "." ] && continue
    [ "$BASE" = ".." ] && continue
    [ -e "$E" ] || [ -L "$E" ] || continue
    REL="$2$BASE"
    # A LEAF is anything that is not a real directory. In a home-files tree the
    # leaves are symlinks into the store; directories are real. Testing -L first
    # matters: a symlink TO a directory is a leaf home-manager links whole, and
    # `[ -d ]` would follow it and recurse into the store.
    if [ -L "$E" ] || [ ! -d "$E" ]; then
      EXAMINED=$(( EXAMINED + 1 ))
      TGT="$HOME_DIR/$REL"
      if [ ! -e "$TGT" ] && [ ! -L "$TGT" ]; then
        ABSENT=$(( ABSENT + 1 ))
      elif [ -L "$TGT" ]; then
        :                                   # already a link — home-manager's
      elif cmp -s "$E" "$TGT"; then
        RECLAIMABLE=$(( RECLAIMABLE + 1 ))
        r_list="$r_list$REL
"
      else
        # Differs from the store copy. The next switch relinks it on its own —
        # reported so the pair is never mistaken for "everything was fine".
        KEPT=$(( KEPT + 1 ))
        k_list="$k_list$REL
"
      fi
      continue
    fi
    r_walk "$E" "$REL/"
  done
}

r_walk "$HOME_FILES" ""

# 🔴 THE EXAMINED COUNT IS PRINTED BESIDE EVERY OTHER COUNT, ALWAYS. "0
# reclaimable" out of 0 examined is indistinguishable from a healthy host and is
# exactly the reassuring zero a scanner wired to nothing produces. The pair is
# the claim; neither number alone is.
echo "managed paths: examined=$EXAMINED reclaimable=$RECLAIMABLE differing=$KEPT absent=$ABSENT (manifest: $HOME_FILES)"

if [ "$KEPT" -gt 0 ]; then
  echo "  $KEPT managed path(s) hold a regular file whose content DIFFERS from the store copy."
  echo "  Not touched: the next home-manager switch relinks those on its own, and the"
  echo "  bytes might be someone's. Listed so the count is never silently folded in:"
  printf "%s" "$k_list" | sed 's|^|      ? |'
fi

if [ "$RECLAIMABLE" = 0 ]; then
  exit 0
fi

echo "  $RECLAIMABLE managed path(s) are a REGULAR FILE byte-identical to the store copy."
echo "  home-manager will never take these back on its own — it skips an identical target."
printf "%s" "$r_list" | sed 's|^|      x |'

if [ "$APPLY" = 0 ]; then
  echo "  DRY RUN — nothing removed. Re-run with --apply, or just run a home-manager"
  echo "  switch: home.activation.reclaimManagedPaths calls this with --apply."
  exit 0
fi

removed=0
while IFS= read -r REL; do
  [ -n "$REL" ] || continue
  TGT="$HOME_DIR/$REL"
  # 🔴 RE-CHECK IMMEDIATELY BEFORE THE DESTRUCTIVE STEP, not on the survey that
  # motivated it. The walk above is a hypothesis about a moment that has passed;
  # a concurrent switch or editor could have changed this path since. Every
  # condition that made the removal lossless is re-asserted here, and the
  # removal is conditional on what comes back.
  [ -e "$TGT" ] || continue
  [ -L "$TGT" ] && continue
  [ -f "$TGT" ] || continue
  cmp -s "$HOME_FILES/$REL" "$TGT" || continue
  rm -f "$TGT" && removed=$(( removed + 1 ))
done <<EOF
$r_list
EOF

echo "  reclaimed $removed of $RECLAIMABLE (the rest changed under us and were left alone)"
exit 0
