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
# 🔴 WHY REMOVING THESE IS LOSSLESS — AND THE EXACT SCOPE OF THAT WORD, because
# it is narrower than it reads and the narrow part is load-bearing:
#
#   * BYTES ONLY. `cmp` compares content. It does NOT compare mode, ownership,
#     ACLs or xattrs, and all of those are discarded when the file is removed
#     and replaced by a symlink. That is the intended outcome — a managed path
#     is supposed to be a link with the store's permissions — but it is a real
#     difference, so "lossless" here means "no BYTES are lost", not "nothing
#     changes".
#   * "THE BYTES ARE ALREADY IN THE STORE" IS FALSE FOR A mkOutOfStoreSymlink
#     LEAF. Such a leaf's manifest entry is a store symlink whose target is a
#     path in the MUTABLE working tree (verified: home-files/.claude/skills/
#     browser/SKILL.md -> /nix/store/…-hm_SKILL.md -> ~/workspace/devrc/scripts/
#     browser-bridge/SKILL.md). This repo has 16 such leaves. For those, `cmp`
#     compares the target against the working tree, and the bytes survive
#     deletion only as long as the working tree still holds them. It is still
#     the right call — an identical copy at the managed path is a copy nobody
#     asked for, and the working tree is the file's home — but the guarantee is
#     "identical to the source the manifest names", not "recoverable from the
#     store forever".
#
# A file that DIFFERS is a file somebody may have meant, and it is also the case
# that fixes itself — both reasons to leave it alone and REPORT it.
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
# nix/home.nix, IMMEDIATELY BEFORE linkGeneration, so an ordinary `home-manager
# switch` repairs the whole class. That is what keeps drift-check's rc 19 from
# being a permanently-red gate: the finding clears on the next switch, not on a
# human remembering a one-off `rm`.
#
# 🔴 THE PLACEMENT IS A SAFETY PROPERTY, NOT A DETAIL. Between this script's
# `rm` and linkGeneration's `ln`, the reclaimed files exist NOWHERE on disk, so
# every activation step in that window is a chance to strand them deleted and
# unlinked. It used to run before `checkLinkTargets`, justified by "the collision
# check would otherwise abort on these" — which is false: `checkCollision()`
# handles identical content with `warnEcho`, never `collisionErrors+=`, so this
# population can never fail that check. nix/home.nix carries the full refutation.
#
# EXIT CODES
#   0  ran, and reported the examined/reclaimable pair (whether or not --apply)
#   1  usage error
#   2  COULD NOT MEASURE — the home-files manifest is absent, unreadable, or has
#      no leaves. Never reported as "0 found": a walk of nothing is not a clean
#      walk, and that is as true of an EMPTY directory as of a missing one.
#   3  COULD NOT MEASURE — `cmp` is not on PATH. See the preflight below.

set -uo pipefail

APPLY=0
HOME_DIR="${RECLAIM_HOME:-$HOME}"
HOME_FILES="${RECLAIM_HOME_FILES:-}"
HOME_FILES_SET=0
# The printed listing is capped so a pathological generation cannot dump
# hundreds of lines into the activation log. It caps the LISTING ONLY — the
# delete loop below walks the full list, because a repair that silently stopped
# at the cap would be a worse bug than the log noise.
LIST_MAX="${RECLAIM_LIST_MAX:-20}"

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
    # 🔴 A SECOND POSITIONAL IS AN ERROR, NOT AN OVERRIDE. The unknown-flag guard
    # above catches a typo'd `--aply`; it cannot see `reclaim … dirA dirB`, where
    # the old code silently kept the LAST and walked a manifest the caller never
    # meant — the same "reports nothing to do instead of erroring" shape the flag
    # guard exists to refuse.
    *)
      if [ "$HOME_FILES_SET" = 1 ]; then
        echo "reclaim-managed-paths: two manifest directories given ('$HOME_FILES' and '$1')" >&2
        echo "  exactly one is meant; refusing to guess which." >&2
        usage
      fi
      HOME_FILES="$1"; HOME_FILES_SET=1; shift ;;
  esac
done

# ── WHERE THE MANIFEST LIVES — ONE RULE, THREE READERS ────────────────────────
# 🔴 ship.sh's `ma_manifest`, this script and drift-check.sh's rc-19 payload all
# have to answer "where is this host's home-files tree". They MUST agree: the
# detector reports a finding, this script repairs it, and ship.sh verifies the
# result — three verdicts about one tree. They cannot share a function (the
# drift payload is piped to `bash -s` over ssh and can source nothing), so the
# probe is duplicated deliberately and pinned by
# test_reclaim_managed_paths.py::test_all_three_manifest_probes_agree, which
# fails if any one of them drifts.
#
# Two candidates because home-manager has used both locations; the first that
# exists wins. `${XDG_STATE_HOME:-…}` is honoured — an earlier version of the
# drift payload hardcoded $HOME/.local/state and disagreed with the other two on
# any host that sets it.
if [ -z "$HOME_FILES" ]; then
  state="${XDG_STATE_HOME:-$HOME_DIR/.local/state}"
  for c in "$state/home-manager/gcroots/current-home/home-files" \
           "$state/nix/profiles/home-manager/home-files"; do
    # The trailing slash is BELT-AND-BRACES, not load-bearing, and
    # saying which is the point: MEASURED 2026-08-21 under bash 5.3, `[ -d X ]`
    # and `[ -d X/ ]` agree on all three shapes that can appear here — a real
    # directory (both true), a symlink to one (both true, `-d` follows), and a
    # DANGLING symlink (both false). ship.sh's copy of this probe called the
    # slash load-bearing "because home-files is itself a SYMLINK into the store";
    # that reason is wrong, because `-d` already resolves the symlink. It is kept
    # because strict POSIX pathname resolution does require a directory for a
    # trailing slash and not every /bin/sh is bash — but a mutation sweep will
    # report removing it as an EQUIVALENT MUTANT under bash, and that is correct,
    # not a coverage gap.
    if [ -d "$c/" ]; then HOME_FILES="$c"; break; fi
  done
  [ -n "$HOME_FILES" ] || HOME_FILES="$state/nix/profiles/home-manager/home-files"
fi

# 🔴 PREFLIGHT: `cmp` IS THE INSTRUMENT, SO ITS ABSENCE IS AN UNMEASURED RUN,
# NOT A CLEAN ONE. Without it every `cmp -s` returns 127 and every candidate
# falls into the "differs" branch: the run prints `reclaimable=0`, exits 0, and
# reads as a healthy host while every permanent wrong-writer is relabelled
# self-healing. Measured 2026-08-21 under a PATH with no diffutils:
# `examined=2 reclaimable=0 differing=2`, rc 0, on a fixture holding one genuine
# permanent case. `cmp` is in diffutils, NOT coreutils, so a PATH assembled from
# a package list can plausibly lack it — the drift-check unit's did.
if ! command -v cmp >/dev/null 2>&1; then
  echo "managed paths: COULD NOT MEASURE — reason=no-cmp (\`cmp\` is not on PATH)"
  echo "  \`cmp\` is what separates a path this can losslessly reclaim from one it must"
  echo "  never touch. Without it nothing is classified, so no count is claimed and"
  echo "  nothing is removed. cmp ships in diffutils, not coreutils."
  exit 3
fi

if [ ! -d "$HOME_FILES/" ]; then
  echo "managed paths: COULD NOT MEASURE — reason=no-manifest (nothing at $HOME_FILES)"
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
BLOCKING=0
r_list=""
k_list=""
b_list=""

r_walk() { # r_walk <dir-in-manifest> <relative-prefix>
  local E BASE REL TGT KIND
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
      # 🔴 `[ ! -e ] && [ ! -L ]` — BOTH HALVES. A DANGLING SYMLINK at a managed
      # target is `-L` true and `-e` false, and it is NOT absent: the link step
      # sees a symlink, resolves it, finds it does not point at the new source
      # and relinks it. Dropping the `-L` half would file it under `absent` and
      # under-report nothing — but it would also mean the classifier no longer
      # matches what home-manager does, which is the only thing making these
      # labels true.
      if [ ! -e "$TGT" ] && [ ! -L "$TGT" ]; then
        ABSENT=$(( ABSENT + 1 ))
      elif [ -L "$TGT" ]; then
        :                                   # already a link — home-manager's
      elif [ ! -f "$TGT" ]; then
        # 🔴 NOT A REGULAR FILE, AND THEREFORE NEITHER RECLAIMABLE NOR
        # SELF-HEALING. This branch exists because `cmp` cannot answer for these
        # and answering anyway was wrong in both directions:
        #   * a DIRECTORY made `cmp` exit non-zero, so it was reported as "holds
        #     a regular file whose content DIFFERS … the next switch relinks it".
        #     Both halves false. Upstream's slow path runs `ln -Tsf … || exit 1`
        #     on a differing target, and `ln` cannot overwrite a directory, so
        #     the next switch ABORTS. The most severe finding wore the label of
        #     the benign one.
        #   * a FIFO made `cmp` BLOCK on open(2), hanging the whole walk with no
        #     timeout anywhere in the activation call path — and hanging the
        #     drift-check timer, which runs this classification 4x/day and over
        #     ssh. Measured 2026-08-21: `timeout 8` -> rc 124, on the DRY RUN,
        #     because the block is in the walk and not in the delete loop (that
        #     loop already tested `-f` before its `cmp`).
        # The `-f` test is what makes `cmp` reachable only for operands it can
        # answer for. Nothing here is ever removed.
        KIND="unknown"
        [ -d "$TGT" ] && KIND="directory"
        [ -p "$TGT" ] && KIND="fifo"
        [ -S "$TGT" ] && KIND="socket"
        { [ -b "$TGT" ] || [ -c "$TGT" ]; } && KIND="device"
        BLOCKING=$(( BLOCKING + 1 ))
        b_list="$b_list$REL ($KIND)
"
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

# 🔴 AN EMPTY MANIFEST IS AN UNMEASURED RUN TOO. `[ ! -d ]` above catches a
# manifest that is MISSING; it says nothing about one that exists and holds
# nothing, and `examined=0 reclaimable=0 … rc 0` out of such a directory is
# byte-for-byte the reassuring all-clear a healthy host prints. A real
# generation has hundreds of leaves (488 on the workbench), so zero means the
# path is wrong, the tree is half-built, or it is not a manifest at all.
if [ "$EXAMINED" = 0 ]; then
  echo "managed paths: COULD NOT MEASURE — reason=empty-manifest (examined=0 at $HOME_FILES)"
  echo "  the directory exists but has no leaves. A walk of nothing is not a clean walk,"
  echo "  and this is not '0 found' — a real generation has hundreds."
  exit 2
fi

# 🔴 THE EXAMINED COUNT IS PRINTED BESIDE EVERY OTHER COUNT, ALWAYS. "0
# reclaimable" out of 0 examined is indistinguishable from a healthy host and is
# exactly the reassuring zero a scanner wired to nothing produces. The pair is
# the claim; neither number alone is.
echo "managed paths: examined=$EXAMINED reclaimable=$RECLAIMABLE differing=$KEPT absent=$ABSENT blocking=$BLOCKING (manifest: $HOME_FILES)"

# Print a capped list, then say how many were withheld. Never silently truncate.
cap_list() { # cap_list <list> <marker>
  printf "%s" "$1" | head -n "$LIST_MAX" | sed "s|^|      $2 |"
  local n
  n=$(printf "%s" "$1" | grep -c '' 2>/dev/null || echo 0)
  if [ "$n" -gt "$LIST_MAX" ]; then
    echo "      ... and $(( n - LIST_MAX )) more (RECLAIM_LIST_MAX=$LIST_MAX caps the LISTING only)"
  fi
}

if [ "$BLOCKING" -gt 0 ]; then
  echo "  🔴 $BLOCKING managed path(s) hold something that is NOT a regular file."
  echo "  These are the SEVERE ones, not the benign ones: home-manager's link step runs"
  echo "  'ln -Tsf … || exit 1' on a target it cannot skip, and ln will not overwrite a"
  echo "  directory — so the next switch ABORTS here rather than repairing it. Nothing is"
  echo "  removed automatically; a human has to look at each one."
  cap_list "$b_list" "!"
fi

if [ "$KEPT" -gt 0 ]; then
  echo "  $KEPT managed path(s) hold a regular file whose content DIFFERS from the store copy."
  echo "  Not touched: the next home-manager switch relinks those on its own, and the"
  echo "  bytes might be someone's. Listed so the count is never silently folded in:"
  cap_list "$k_list" "?"
fi

if [ "$RECLAIMABLE" = 0 ]; then
  exit 0
fi

echo "  $RECLAIMABLE managed path(s) are a REGULAR FILE byte-identical to the store copy."
echo "  home-manager will never take these back on its own — it skips an identical target."
cap_list "$r_list" "x"

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
  #
  # 🔴 THEIR REACHABILITY IS NOT UNIFORM, AND SAYING SO IS THE POINT — an
  # independent mutation sweep found three of the four survived, because the
  # walk's own classification means only one of them can normally decide:
  #   [ -e ]  UNREACHABLE BY DESIGN, and kept as belt-and-braces. `-f` implies
  #           `-e` for everything except a dangling symlink, and a dangling
  #           symlink is caught by the `-L` test on the next line — so no operand
  #           can reach this test's `continue` without a later one also firing.
  #           Deleting it would change no behaviour; it is here so the invariant
  #           reads in full at the site that depends on it.
  #   [ -L ]  reachable, and pinned: the target can BECOME a symlink between the
  #           walk and this loop, and without this line `-f` follows it and `rm`
  #           destroys the link. test_a_target_that_became_a_symlink_after_the_
  #           walk_is_not_removed drives exactly that.
  #   [ -f ]  reachable, and pinned: the target can become a FIFO/directory,
  #           where `cmp` would block forever. test_a_target_that_became_a_fifo_
  #           after_the_walk_is_not_removed drives exactly that.
  #   cmp     the ordinary case, pinned by the stubbed-cmp test below.
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
