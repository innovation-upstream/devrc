# shellcheck shell=bash
# nix_read_paths.sh — "is this repo path READ BY NIX?", derived, never hardcoded.
#
# SOURCE THIS, do not execute it. It defines functions and sets nothing at
# source time (test_nix_read_paths.py pins that), exactly like lib/host-role.sh.
#
# ── WHY IT EXISTS ─────────────────────────────────────────────────────────────
# Two consumers need the SAME question answered and they are the two safety
# instruments of this repo:
#
#   * scripts/ship.sh   — a converge on a DIRTY tree used to end with a flat
#     "what was built/deployed is origin/main + local WIP". Honest, and
#     unactionable: nobody could tell whether the dirt was IN the artifact. On
#     2026-08-25 the workbench's two dirty paths were
#     nix/system/apply-nebula-443.sh.LOCAL-preserved-2026-08-02 (nix reads
#     nothing under nix/system/ — those are staged sudo scripts) and
#     scripts/dl-router/tests/load_test_store.sh (which IS read: home.nix says
#     `${../scripts/dl-router}` and copies the WHOLE directory into the store).
#     A hand-written list would have got the second one wrong. This derives it.
#
#   * scripts/drift-check.sh — an untracked file in a nix-read path is code
#     being SERVED by a host with no commit and no backup behind it.
#
# A predicate open-coded at two sites is typically wrong at one of them in the
# same direction (claude/RULES.md, "One rule, one place"), so there is one copy
# and both call it. test_nix_read_paths.py::test_the_predicate_has_exactly_one
# _definition is the ledger that keeps it that way.
#
# ── THE TWO CLASSES, and why they are not one ─────────────────────────────────
#   LIVE   `config.lib.file.mkOutOfStoreSymlink "${workspace}/devrc/<p>"`.
#          The deployed path is a symlink BACK INTO the working tree, so an edit
#          is deployed CONTINUOUSLY — no `home-manager switch`, no signal, no
#          generation boundary. Dirty here means "this host is running your WIP
#          right now".
#   STORE  a nix path literal (`source = ../claude/RULES.md`, `${../scripts/
#          dl-router}`, `builtins.readFile ../../../.zshrc`, …). Nix reads it at
#          eval/build time, so it lands in the artifact the NEXT switch builds.
#          Dirty here means "your WIP is in the generation ship.sh just built".
#
# The consequences differ (one needs no switch and has no boundary; the other is
# pinned to a generation), so the classes are reported separately rather than
# folded into one "nix-read" bit.
#
# 🔴 A STORE CLASSIFICATION IS NOT YET A CLAIM THAT THE FILE REACHED THE
# ARTIFACT — and the first version of this file said it was. See
# nix_read_artifact_reach below for the measurement and the rule.
#
# ── DERIVED, NEVER HARDCODED ──────────────────────────────────────────────────
# Same discipline as drift-check.sh's built-source set, which is "derived from
# nix/pkgs/ at scan time and pinned two-way by a test, so a third such package is
# covered automatically". Here the scan input is the FLAKE'S OWN ENTRY POINTS —
# NIXREAD_ROOT_FILES and NIXREAD_ROOT_DIRS below — and everything else is read
# out of the .nix files found under them. Add a 13th mkOutOfStoreSymlink and it
# is covered with no edit here.
#
# 🔴 THE SCAN ROOTS ARE THE ONLY LITERALS, and they are the scanner's INPUT, not
# a list of answers. They are pinned by test_nix_read_paths.py (they must exist,
# and the .nix files under them must be found), because a root that stops
# existing turns this whole file into a scanner wired to nothing — which returns
# an empty set, which reads to both consumers as a reassuring "nothing is
# nix-read". Every entry point therefore reports NIXREAD_FILES beside its answer
# and every consumer is required to print it.
#
# ── WHAT IT CANNOT SEE (stated so nobody reads more into a NONE than is there) ─
#   * The path alphabet is [A-Za-z0-9._/-] — the same one drift-check.sh's
#     source scan uses. A repo path with a space, a quote or a non-ASCII byte is
#     NOT classified; nix_read_class_of returns UNREPRESENTABLE for it, which
#     callers must report rather than treat as NONE.
#   * Comment stripping is `${LN%%#*}`, so a path mentioned after a `#` on a code
#     line is dropped. That can only ever SHRINK the derived set; the two-way pin
#     in the test is what makes a shrink loud.
#   * A path reached only through a nix STRING built at eval time
#     ("${toString ./.}/x") is invisible. None exist today; the two-way pin
#     covers the `source =` and mkOutOfStoreSymlink spellings that do.
#   * It answers "does nix READ it", never "does the deployed artifact still
#     match" — that is ship.sh's verify_managed_currency, and neither replaces
#     the other.

# The scan roots. Repo-relative. Files first, then directories walked for *.nix.
NIXREAD_ROOT_FILES="flake.nix flake.lock"
NIXREAD_ROOT_DIRS="nix"

# Every character that cannot appear in a repo path we are willing to classify.
# Held in a variable because `${v//[!…/…]/ }` would otherwise end its pattern at
# the literal `/` inside the bracket expression: the delimiters are parsed off
# the EXPRESSION TEXT, so a `/` arriving by expansion is data, not a delimiter.
NIXREAD_NONPATH='[!A-Za-z0-9._/-]'

# Populated by nix_read_scan. Declared here so `set -u` consumers can read them
# before a scan and get "" rather than an unbound-variable death.
NIXREAD_LIVE=""       # space-separated repo-relative paths, mkOutOfStoreSymlink
NIXREAD_STORE=""      # space-separated repo-relative paths, nix path literals
NIXREAD_FILES=0       # .nix files actually READ — the population, always report it
NIXREAD_COUNT=0       # |LIVE| + |STORE|
NIXREAD_MISSING=""    # derived paths that do NOT exist in the repo (a fault)
NIXREAD_REASON=""     # OK | NOREPO | NOROOTS | NOSCAN

_nixread_add() { # _nixread_add <LIVE|STORE> <repo-relative-path>
  case "$1" in
    LIVE)
      case " $NIXREAD_LIVE " in *" $2 "*) return 0 ;; esac
      NIXREAD_LIVE="$NIXREAD_LIVE $2" ;;
    *)
      case " $NIXREAD_STORE " in *" $2 "*) return 0 ;; esac
      NIXREAD_STORE="$NIXREAD_STORE $2" ;;
  esac
}

# _nixread_take_live <line> — every ${workspace}/devrc/<path> the line names.
# Only called for lines that also contain mkOutOfStoreSymlink: that interpolation
# is what makes the deployed link point at the WORKING TREE, and it is the whole
# difference between the two classes.
_nixread_take_live() {
  local L="$1" REST PP
  while : ; do
    case "$L" in *"\${workspace}/devrc/"*) ;; *) break ;; esac
    REST="${L#*"\${workspace}/devrc/"}"
    PP="${REST%%$NIXREAD_NONPATH*}"
    PP="${PP%/}"
    [ -n "$PP" ] && _nixread_add LIVE "$PP"
    L="$REST"
  done
}

# _nixread_resolve <token> <dir-of-the-nix-file, repo-relative> -> repo-relative
# path on stdout, or nothing when the token escapes the repo root.
#
# `../` is resolved against the FILE'S OWN directory, which is why the walk is
# per-file and not per-repo: nix/home.nix's `../scripts/x` and
# nix/programs/zsh/default.nix's `../../../.zshrc` both mean a repo path, and
# they mean different depths.
_nixread_resolve() {
  local T="$1" D="$2"
  case "$T" in ./*) T="${T#./}" ;; esac
  while : ; do
    case "$T" in ../*) ;; *) break ;; esac
    # No directory left to climb: the literal points OUTSIDE the repo. Nix would
    # still read it, but it is not a path any consumer of ours can see as dirty.
    [ -n "$D" ] || return 0
    T="${T#../}"
    case "$D" in */*) D="${D%/*}" ;; *) D="" ;; esac
  done
  # A `..` surviving in the middle is a shape this resolver does not model;
  # dropping it is safe (it can only shrink the set) and the two-way pin is what
  # would make a real one loud.
  #
  # 🔴 `.` — THE REPO ROOT ITSELF — IS DROPPED, DELIBERATELY, and it is the one
  # exclusion that changes the answer for every path in the repo. flake.nix's
  # `cp -r ${./.} src` is real and it does read the whole tree, but only to build
  # the `checks.*` derivations (`nix build .#checks.…pytests`), which are not the
  # home-manager artifact either consumer is asking about. Keeping it would make
  # nix_read_class_of answer STORE for literally everything, and a predicate that
  # is always true tells nobody anything.
  case "$T" in ""|"."|*..*|/*) return 0 ;; esac
  if [ -n "$D" ]; then printf '%s\n' "$D/$T"; else printf '%s\n' "$T"; fi
}

# nix_read_scan <repo> — populate the module variables. Returns 0 when the scan
# is USABLE (it read at least one .nix file and derived at least one path), 1
# otherwise, with NIXREAD_REASON naming which. 🔴 A caller that ignores the
# return value gets an empty set, which is exactly the reassuring zero this file
# is built to refuse.
nix_read_scan() {
  local REPO="$1" F D T P
  NIXREAD_LIVE=""; NIXREAD_STORE=""; NIXREAD_MISSING=""
  NIXREAD_FILES=0; NIXREAD_COUNT=0; NIXREAD_REASON=""

  if [ -z "$REPO" ] || [ ! -d "$REPO" ]; then
    NIXREAD_REASON=NOREPO
    return 1
  fi

  # globstar for the recursive walk, restored to whatever the caller had. This
  # library is SOURCED into two long-lived scripts; leaving a shell option
  # flipped under them would be a side effect, and side effects here are pinned
  # against by test_the_lib_is_side_effect_free_when_sourced.
  local WANT_GS=0 WANT_NG=0
  shopt -q globstar || WANT_GS=1
  shopt -q nullglob || WANT_NG=1
  shopt -s globstar nullglob

  local ROOTS_SEEN=0
  for T in $NIXREAD_ROOT_FILES; do
    [ -f "$REPO/$T" ] || continue
    ROOTS_SEEN=$(( ROOTS_SEEN + 1 ))
    _nixread_add STORE "$T"
    case "$T" in *.nix) _nixread_scan_file "$REPO/$T" "" ;; esac
  done
  for D in $NIXREAD_ROOT_DIRS; do
    [ -d "$REPO/$D" ] || continue
    ROOTS_SEEN=$(( ROOTS_SEEN + 1 ))
    # 🔴 WALKED IS NOT THE SAME AS READ. Every .nix under here is SCANNED for
    # path literals, but a file only joins the STORE set when something REACHES
    # it — flake.nix names ./nix/home.nix, which names ./graphical.nix and
    # ./pkgs, and so on down. Adding each walked file (or the directory) instead
    # would classify nix/system/*.nix and every staged sudo script beside them as
    # nix-read, and nix opens none of those: they are hand-run under sudo against
    # /etc/nixos, outside the flake entirely. Measured 2026-08-25 — the
    # workbench's dirty
    # nix/system/apply-nebula-443.sh.LOCAL-preserved-2026-08-02 comes out STORE
    # under the walked-is-read spelling and NONE under this one, and NONE is the
    # true answer.
    for F in "$REPO/$D"/**/*.nix; do
      [ -f "$F" ] || continue
      P="${F#"$REPO/"}"
      case "$P" in */*) _nixread_scan_file "$F" "${P%/*}" ;; *) _nixread_scan_file "$F" "" ;; esac
    done
  done

  [ "$WANT_GS" = 1 ] && shopt -u globstar
  [ "$WANT_NG" = 1 ] && shopt -u nullglob

  NIXREAD_LIVE="${NIXREAD_LIVE# }"
  NIXREAD_STORE="${NIXREAD_STORE# }"
  for T in $NIXREAD_LIVE $NIXREAD_STORE; do
    NIXREAD_COUNT=$(( NIXREAD_COUNT + 1 ))
    [ -e "$REPO/$T" ] || NIXREAD_MISSING="$NIXREAD_MISSING $T"
  done
  NIXREAD_MISSING="${NIXREAD_MISSING# }"

  if [ "$ROOTS_SEEN" = 0 ]; then NIXREAD_REASON=NOROOTS; return 1; fi
  if [ "$NIXREAD_FILES" = 0 ] || [ "$NIXREAD_COUNT" = 0 ]; then
    NIXREAD_REASON=NOSCAN; return 1
  fi
  NIXREAD_REASON=OK
  return 0
}

# _nixread_scan_file <abs-path> <repo-relative-dir-of-that-file>
_nixread_scan_file() {
  local LN WORDS W R
  NIXREAD_FILES=$(( NIXREAD_FILES + 1 ))
  while IFS= read -r LN || [ -n "$LN" ]; do
    LN="${LN%%#*}"
    [ -n "$LN" ] || continue
    case "$LN" in *mkOutOfStoreSymlink*) _nixread_take_live "$LN" ;; esac
    case "$LN" in *./*) ;; *) continue ;; esac
    WORDS="${LN//$NIXREAD_NONPATH/ }"
    for W in $WORDS; do
      case "$W" in ../*|./*) ;; *) continue ;; esac
      R="$(_nixread_resolve "$W" "$2")"
      [ -n "$R" ] && _nixread_add STORE "$R"
    done
  done < "$1"
}

# nix_read_class_of <repo-relative-path> -> LIVE | STORE | NONE | UNREPRESENTABLE
#
# MOST SPECIFIC WINS, and that is the point: `scripts/dl-router` is STORE (the
# whole directory is copied into the store) while `scripts/dl-router/dl-route`
# is LIVE (a mkOutOfStoreSymlink onto that one file). A path is matched against
# itself and then each ancestor, nearest first; at one level LIVE outranks STORE
# because "already deployed, no switch needed" is the stronger claim.
#
# 🔴 Requires a prior nix_read_scan. With an empty set every answer is NONE,
# which is why nix_read_scan's return value is not optional.
nix_read_class_of() {
  local P="$1"
  case "$P" in ""|*$NIXREAD_NONPATH*) printf 'UNREPRESENTABLE\n'; return 0 ;; esac
  while : ; do
    case " $NIXREAD_LIVE " in *" $P "*) printf 'LIVE\n'; return 0 ;; esac
    case " $NIXREAD_STORE " in *" $P "*) printf 'STORE\n'; return 0 ;; esac
    case "$P" in */*) P="${P%/*}" ;; *) break ;; esac
  done
  printf 'NONE\n'
}

# nix_read_artifact_reach <class> <1 if git KNOWS the path, else 0>
#   -> LIVE | ARTIFACT | DROPPED | NONE | UNREPRESENTABLE
#
# 🔴 THE CLASS ALONE OVERSTATES, AND THIS IS WHERE THAT IS FIXED. A STORE
# classification says "nix reads this path". It does NOT say the file reached the
# built artifact, because nix's flake source for a git checkout is FILTERED to
# the files git knows about — CLAUDE.md already says so from the other side ("A
# NEW file must be `git add`ed or the flake silently omits it from the deploy"),
# and the first version of this library ignored it.
#
# MEASURED 2026-08-25, one directory, one build, four states, positive control
# included (scripts/tests/test_nix_read_paths.py pins the same table):
#   committed, unmodified ....... PRESENT (committed content)
#   committed, MODIFIED ......... PRESENT — with the WORKING-TREE content
#   `git add`ed, never committed  PRESENT — with the WORKING-TREE content
#   staged then modified again .. PRESENT — with the LATEST working-tree content
#   UNTRACKED ................... ABSENT, at every depth
# and corroborated on the live host: all six `-dl-router` store generations carry
# `tests/` (37 files) and NONE carries the untracked `tests/load_test_store.sh`.
#
# So the discriminator is INDEX MEMBERSHIP, not commitment: `git ls-files
# --error-unmatch <p>`, or equivalently "this path came from `git diff` /
# `git diff --cached` rather than from `git ls-files --others`". A file staged
# ten seconds ago is in the artifact; a file committed nowhere but staged is in
# the artifact; a file never `git add`ed is not, however long it has sat there.
#
# 🔴 LIVE IS UNAFFECTED, and that is not an assumption. `mkOutOfStoreSymlink`
# bakes a runtime PATH STRING into the store, so the deployed link resolves into
# the working tree at USE time and never through the flake source. Verified on
# the live host: ~/.claude/skills/browser/browser -> …-home-manager-files/… ->
# /home/zach/workspace/devrc/scripts/browser-bridge/browser. An untracked file at
# a LIVE path is therefore genuinely being served.
#
# DROPPED is NOT "harmless". The file is unsaved work in no commit and no backup,
# it sits in a tree nix copies, and a single `git add` moves it into the artifact
# with no other action — so it is worth reporting. It is simply not worth
# reporting as something that is already deployed.
nix_read_artifact_reach() {
  case "$1" in
    LIVE)  printf 'LIVE\n' ;;
    STORE) if [ "${2:-0}" = 1 ]; then printf 'ARTIFACT\n'; else printf 'DROPPED\n'; fi ;;
    *)     printf '%s\n' "$1" ;;
  esac
}
