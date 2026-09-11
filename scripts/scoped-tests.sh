#!/usr/bin/env bash
#
# scoped-tests.sh — map a git diff to the test FILES it can affect, and run only
# those. The fast iteration loop; explicitly NOT the merge gate.
#
# 🔴 WHY THIS EXISTS — the measured problem, not a hunch. The pytest tier's
# median wall time is 20.1 min over 237 real runs (p90 38 min). It is not slow
# because any one thing is slow: it is slow because dozens of concurrent agent
# sessions each run the FULL ~21k-test suite on one 24-core box. The lever is
# not a faster suite. It is FEWER FULL SUITES.
#
# ⚠ DO NOT PUT A SIZE ON THE CONTENTION EFFECT HERE. This header used to read
# "runs bucketed by overlap go 14.5 min (0 others) -> 48.9 min (6+)", and that
# figure is RETRACTED — `CLAUDE.md` retracted it and says in terms that
# re-deriving it is the trap, while this file went on asserting it as the
# measured reason for its own existence. Bucketing runs by how many others
# overlapped them is LENGTH-BIASED: a long run overlaps more runs BY
# CONSTRUCTION, and a null Monte Carlo with zero interaction reproduces the
# shape, the 14.5-min baseline and ~2.06x of the 3.4x. Contention is real and
# its mechanism is uncontroversial; that dataset cannot size it.
#
# The two numbers above and below this note are NOT retracted and are what
# justify the script: the 20.1-min median, and the collection cost measured
# next. Neither depends on the overlap bucketing.
#
# `run-tests.sh --targets` has existed for a while and buys little on its own,
# because the target that matters is one monolith. MEASURED 2026-09-08 in this
# repo's devShell:
#
#     pytest scripts/tests --collect-only        13,367 tests   60.0s wall / 30.5s CPU
#     pytest scripts/tests/test_drift_check.py      461 tests    0.64s wall
#
# i.e. a MINUTE of collection before a single test runs, and `--targets
# scripts/tests` pays all of it. Only a file selection avoids it — which is what
# this script produces and `run-tests.sh --files` consumes.
#
# 🔴 WHAT THIS IS NOT. Read this before quoting a green run from it.
#   * NOT a gate. It runs a fraction of a fraction. `run-tests.sh` labels the
#     run `SCOPE: SCOPED`, and `gate.sh` REFUSES to print a gate PASS off any
#     run that is not `SCOPE: FULL` (it exits 91 instead).
#   * NOT complete. The mapping below is a heuristic over a git diff — it finds
#     tests that NAME what you changed. A test that covers your change through
#     an import chain without ever mentioning it is invisible here, and so is
#     every test that would have caught a change you did not make.
#   * NOT a substitute for running the full gate before merging. It is for the
#     loop between edits.
#
# The refusal that keeps that honest: if the mapping selects NOTHING, this
# script FAILS (exit 4). It never reports "nothing to do" and exits 0 — a
# wrapper that says that is precisely how a false green gets believed here.
#
# Usage:
#   scripts/scoped-tests.sh [--base REF] [--set hermetic|all] [--dry-run] [ROOT]
#     --base REF   compare against REF as well as the working tree.
#                  Default: origin/<main-branch> if it resolves, else HEAD.
#     --set        passed through to run-tests.sh.
#     --dry-run    print the mapping and the exact run-tests.sh command; run
#                  nothing. Exit 0 only if the selection is non-empty.
#   ROOT defaults to the git repo root.
#
# Exit: whatever run-tests.sh exits (its verdict is the verdict), or
#       2 = a usage/precondition problem here
#       4 = the mapping selected no test files. NOT a pass — run the full gate.

set -uo pipefail

BASE=""
SET="hermetic"
DRY=0
ROOT=""

while [ $# -gt 0 ]; do
  case "$1" in
    --base) BASE="${2:-}"; shift; [ $# -gt 0 ] && shift ;;
    --base=*) BASE="${1#*=}"; shift ;;
    --set) SET="${2:-hermetic}"; shift; [ $# -gt 0 ] && shift ;;
    --set=*) SET="${1#*=}"; shift ;;
    --dry-run) DRY=1; shift ;;
    -h|--help) sed -n '2,60p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) ROOT="$1"; shift ;;
  esac
done

if [ -z "$ROOT" ]; then
  ROOT="$(git -C "$(dirname "${BASH_SOURCE[0]}")" rev-parse --show-toplevel 2>/dev/null || true)"
  [ -n "$ROOT" ] || ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
fi
unset CDPATH
cd "$ROOT" || { echo "scoped-tests: FATAL — cannot cd to ROOT=$ROOT" >&2; exit 2; }

# 🔴 AN AMBIENT `DEVRC_TARGETS` SILENTLY SHRINKS THIS MAPPER'S UNIVERSE. The
# declared target list is read from `run-tests.sh --check-targets`, which HONOURS
# that variable — so an exported value makes the search universe a subset, and
# changed files whose covering tests live in an unselected target map to nothing.
# The end state is safe (the run refuses at exit 4 rather than passing), but the
# DIAGNOSIS it prints is wrong: "no test names what you changed" when the truth
# is "this mapper was not allowed to look there". A wrong diagnosis on a refusal
# is how someone concludes their change is untested and moves on.
# Refused rather than unset, for the reason gate.sh gives: silently discarding an
# operator's exported selection is the mirror defect.
if [ -n "${DEVRC_TARGETS+x}" ]; then
  echo "scoped-tests: FATAL — DEVRC_TARGETS is set ('${DEVRC_TARGETS}')." >&2
  echo "  The declared target list is read through run-tests.sh, which honours" >&2
  echo "  it, so this mapper's search universe would be a SUBSET — and a changed" >&2
  echo "  file whose tests live outside it would be reported as UNMAPPED, which" >&2
  echo "  is a wrong diagnosis rather than a wrong verdict." >&2
  echo "  \`unset DEVRC_TARGETS\` (or \`env -u DEVRC_TARGETS scripts/scoped-tests.sh …\`)." >&2
  exit 2
fi

RUNNER="${DEVRC_SCOPED_RUNNER:-$ROOT/scripts/run-tests.sh}"
[ -x "$RUNNER" ] || [ -f "$RUNNER" ] || {
  echo "scoped-tests: FATAL — no runner at $RUNNER" >&2; exit 2; }

# --- the declared target list, read from the runner itself ---------------------
# 🔴 NOT a second copy of the list. `run-tests.sh --check-targets` prints
# `dir <path>` / `file <path>` in milliseconds and is the same list the run will
# use, so a target added there is covered here with no edit. A hardcoded list
# would be wrong the first time someone adds a suite — and wrong in the silent
# direction, mapping a changed file to nothing.
TARGET_LINES="$(bash "$RUNNER" --set "$SET" --check-targets "$ROOT" 2>/dev/null | sed -n 's/^  \(dir\|file\)   *//p')"
if [ -z "$TARGET_LINES" ]; then
  echo "scoped-tests: FATAL — could not read the declared target list from the runner." >&2
  echo "  Ran: bash $RUNNER --set $SET --check-targets $ROOT" >&2
  echo "  An empty list would map every change to nothing, so this refuses" >&2
  echo "  rather than reporting an empty selection as if the diff were clean." >&2
  exit 2
fi
TARGETS=()
while IFS= read -r _l; do
  [ -n "$_l" ] || continue
  # Normalise to repo-relative. The declared list is relative today, but `git
  # diff --name-only` is ALWAYS relative, so an absolute entry would match no
  # changed path and every change would map to nothing — reported as an empty
  # selection, i.e. a fault in the target list wearing the costume of a clean
  # diff. Normalising here makes the two sides comparable by construction.
  case "$_l" in "$ROOT"/*) _l="${_l#"$ROOT"/}" ;; ./*) _l="${_l#./}" ;; esac
  TARGETS+=("$_l")
done <<EOF
$TARGET_LINES
EOF

# --- what changed --------------------------------------------------------------
# Both halves matter and they answer different questions: the diff against BASE
# is "what this branch changed", the working-tree reads are "what I have not
# committed yet". An iteration loop is mostly the second; a pre-push check is
# mostly the first. Taking only one of them is how a scoped run misses the very
# edit it was invoked for.
if [ -z "$BASE" ]; then
  _mainref="$(git symbolic-ref --quiet --short refs/remotes/origin/HEAD 2>/dev/null || true)"
  [ -n "$_mainref" ] || _mainref="origin/main"
  if git rev-parse --verify --quiet "$_mainref" >/dev/null 2>&1; then
    BASE="$_mainref"
  else
    BASE="HEAD"
  fi
fi
if ! git rev-parse --verify --quiet "$BASE" >/dev/null 2>&1; then
  echo "scoped-tests: FATAL — --base '$BASE' does not resolve to a commit." >&2
  exit 2
fi

CHANGED_RAW="$(
  {
    # `...` = changes on this branch since it forked from BASE, which is what a
    # reviewer will see. A plain `..` would also list everything BASE gained
    # meanwhile, and in this repo origin/main moves every ~20 minutes.
    git diff --name-only --diff-filter=ACMR "$BASE...HEAD" 2>/dev/null || true
    git diff --name-only --diff-filter=ACMR HEAD 2>/dev/null || true
    git diff --name-only --diff-filter=ACMR --cached 2>/dev/null || true
    git ls-files --others --exclude-standard 2>/dev/null || true
  } | sort -u
)"
CHANGED=()
while IFS= read -r _l; do [ -n "$_l" ] && CHANGED+=("$_l"); done <<EOF
$CHANGED_RAW
EOF

echo "scoped-tests: base=$BASE  set=$SET  changed=${#CHANGED[@]} file(s)"
if [ "${#CHANGED[@]}" -eq 0 ]; then
  echo "scoped-tests: FATAL — the diff is EMPTY: nothing changed against $BASE" >&2
  echo "  and the working tree is clean. There is nothing to scope a run to." >&2
  echo "  This is not a pass. If you want a verdict, run scripts/gate.sh." >&2
  exit 4
fi

# --- is a path a test file inside a declared target? ---------------------------
_owning_target() { # $1 = repo-relative path; echoes the longest matching target
  local f="$1" t own=""
  for t in "${TARGETS[@]}"; do
    case "$f" in
      "$t"|"$t"/*) [ "${#t}" -gt "${#own}" ] && own="$t" ;;
    esac
  done
  printf '%s' "$own"
}
# The declared target that BELONGS to the changed file's subsystem: a target
# living under one of its ancestor directories (`scripts/dl-router/server.py` ->
# `scripts/dl-router/tests`), deepest first. Empty when there is none.
#
# 🔴 THIS IS WHAT MAKES THE BASENAME FALLBACK USABLE. MEASURED on this repo:
# `server.py` matched 27 test files across the whole universe — almost all of
# them unrelated suites that merely mention a file of that name — and 3 inside
# `scripts/dl-router/tests`, which is the subsystem that actually owns it. An
# over-selecting mapper is not merely slow: it hands back a run whose green says
# far less about the change than its size implies.
_subsystem_target() { # $1 = repo-relative path
  local f="$1" d t best=""
  d="$(dirname "$f")"
  while [ -n "$d" ] && [ "$d" != "." ] && [ "$d" != "/" ]; do
    for t in "${TARGETS[@]}"; do
      case "$t" in
        "$d"/tests|"$d"/tests/*)
          [ "${#d}" -gt "${#best}" ] && best="$t"
          ;;
      esac
    done
    [ -n "$best" ] && break
    d="$(dirname "$d")"
  done
  printf '%s' "$best"
}
_is_selectable_test_file() { # $1 = path
  local f="$1"
  [ -f "$f" ] || return 1
  case "$(basename "$f")" in
    test_*.py|*_test.py) : ;;
    *) return 1 ;;
  esac
  [ -n "$(_owning_target "$f")" ] || return 1
  return 0
}

# Every pytest-collectable file under every declared target — the universe the
# grep below searches. Built once.
UNIVERSE="$(
  for t in "${TARGETS[@]}"; do
    if [ -d "$t" ]; then
      find "$t" -type f \( -name 'test_*.py' -o -name '*_test.py' \) 2>/dev/null || true
    elif [ -f "$t" ]; then
      printf '%s\n' "$t"
    fi
  done | sed 's#^\./##' | sort -u
)"
if [ -z "$UNIVERSE" ]; then
  echo "scoped-tests: FATAL — found no test files under any declared target." >&2
  echo "  An empty search universe maps every change to nothing, which would" >&2
  echo "  then be reported as an empty selection rather than as this fault." >&2
  exit 2
fi
UNIVERSE_N="$(printf '%s\n' "$UNIVERSE" | wc -l | tr -d ' ')"

# --- the mapping ---------------------------------------------------------------
# 🔴 A HEURISTIC, AND IT SAYS SO. Two rules, in order, per changed file:
#   1. the file IS a selectable test file        -> select it
#   2. some test file NAMES it                   -> select those
# Rule 2 matches the full repo-relative path first (specific), and falls back to
# the bare basename only when the path matched nothing (a test that says
# `drift-check.sh` rather than `scripts/drift-check.sh`). The fallback is
# reported separately because it is the looser of the two and can over-select.
#
# There is deliberately NO third rule of the form "otherwise run the owning
# subsystem's whole target". It would silently turn a 1-file scope into a
# 13k-test one for anything under scripts/, i.e. quietly undo the entire point,
# and the operator would have no way to see it happened. Unmapped is REPORTED
# instead, and an unmapped file is a fact about the mapping, not a pass.
SELECTED=""
UNMAPPED=()
BY_BASENAME=()
_select() { # $1 = test file path
  case " $SELECTED " in
    *" $1 "*) return 0 ;;
    *) SELECTED="${SELECTED}${SELECTED:+ }$1" ;;
  esac
}
for c in "${CHANGED[@]}"; do
  if _is_selectable_test_file "$c"; then
    _select "$c"
    continue
  fi
  # `grep -F -f -` over the enumerated universe rather than `git grep`: the
  # universe already excludes everything that is not a collectable test file, and
  # CLAUDE.md records that a recursive grep here honours .gitignore and returns a
  # confident 0 for generated paths. Feed it an explicit file list instead.
  hits="$(printf '%s\n' "$UNIVERSE" | tr '\n' '\0' \
          | xargs -0 grep -l -F -e "$c" 2>/dev/null | sed 's#^\./##' | sort -u || true)"
  used_basename=0
  if [ -z "$hits" ]; then
    b="$(basename "$c")"
    # A one- or two-character basename would match everything; a bare `x.py`
    # would too. Require some specificity before falling back.
    if [ "${#b}" -ge 6 ]; then
      # Search the changed file's OWN subsystem first — see `_subsystem_target`
      # for the 27-vs-3 measurement that makes this the default rather than an
      # optimisation. The universe-wide search survives only for a file with no
      # subsystem target at all (a repo-root doc, a nix module).
      sub="$(_subsystem_target "$c")"
      if [ -n "$sub" ]; then
        # 🔴 A `case` glob, NOT `grep -E "^$sub"`. A target path interpolated
        # into a regex is a pattern, not a literal: any `.` in it becomes
        # "any character" and the filter silently widens — the exact
        # over-selection this narrowing exists to remove, wearing its costume.
        scope_list=""
        while IFS= read -r _u; do
          case "$_u" in
            "$sub"|"$sub"/*) scope_list="${scope_list}${_u}"$'\n' ;;
          esac
        done <<EOF
$UNIVERSE
EOF
      else
        scope_list="$UNIVERSE"
      fi
      if [ -n "$scope_list" ]; then
        hits="$(printf '%s\n' "$scope_list" | tr '\n' '\0' \
                | xargs -0 grep -l -F -e "$b" 2>/dev/null | sed 's#^\./##' | sort -u || true)"
        [ -n "$hits" ] && used_basename=1
      fi
    fi
  fi
  if [ -z "$hits" ]; then
    UNMAPPED+=("$c")
    continue
  fi
  [ "$used_basename" -eq 1 ] && BY_BASENAME+=("$c")
  while IFS= read -r h; do
    [ -n "$h" ] || continue
    _is_selectable_test_file "$h" && _select "$h"
  done <<EOF
$hits
EOF
done

SEL_ARR=()
# `set -f` for the same reason run-tests.sh's `--targets`/`--files` blocks give:
# word splitting is wanted here, PATHNAME EXPANSION is not. Without it a path
# carrying a glob character would silently select whatever happens to exist on
# disk instead — a narrowing nobody asked for, and green.
set -f
# shellcheck disable=SC2206  # word splitting intended; globbing is not, hence set -f
SEL_ARR=($SELECTED)
set +f

echo "scoped-tests: universe=${UNIVERSE_N} collectable test file(s) under ${#TARGETS[@]} '$SET' target(s)"
for c in "${CHANGED[@]}"; do echo "  changed: $c"; done
if [ "${#BY_BASENAME[@]}" -gt 0 ]; then
  echo "  ⚠ matched by BASENAME only (looser rule, can over-select): ${BY_BASENAME[*]}"
fi
if [ "${#UNMAPPED[@]}" -gt 0 ]; then
  # 🔴 PRINTED WHETHER OR NOT ANYTHING ELSE MAPPED. This is the honest half of a
  # heuristic: these changed files have NO covering test in the selection, so
  # whatever the run says, it says nothing about them.
  echo "  🔴 UNMAPPED — no test file names these, so this run says NOTHING about them:"
  for u in "${UNMAPPED[@]}"; do echo "       $u"; done
fi
echo "scoped-tests: selected ${#SEL_ARR[@]} of ${UNIVERSE_N} test file(s):"
# "N of M", never a bare N — the denominator is what turns a count into a
# coverage statement, the same reason run-tests.sh's subset note carries one.
# A selection past half the universe is not wrong, it is just no longer cheap,
# and the operator is better off with the run that actually produces a verdict.
if [ "$(( ${#SEL_ARR[@]} * 2 ))" -gt "$UNIVERSE_N" ]; then
  echo "  ⚠ that is more than HALF the collectable test files. A scoped run this"
  echo "    wide costs about what the gate costs and still is NOT a gate — you"
  echo "    probably want \`scripts/gate.sh --tier both\` instead."
fi
for s in "${SEL_ARR[@]}"; do echo "  select : $s"; done

if [ "${#SEL_ARR[@]}" -eq 0 ]; then
  # 🔴 THE REFUSAL. Every other outcome of this script hands a verdict to
  # run-tests.sh; this one cannot, so it must not exit 0. "Nothing to run" and
  # "everything passed" are the same observable to anyone reading an exit code,
  # and this is the branch where they would be confused.
  echo "scoped-tests: FATAL — mapped ${#CHANGED[@]} changed file(s) to ZERO test files." >&2
  echo "  This is NOT a pass. Nothing ran, and nothing here can tell you whether" >&2
  echo "  the change is safe." >&2
  echo "  Either no test names what you changed, or the change is outside the" >&2
  echo "  tested surface entirely. Run the real gate:" >&2
  echo "      scripts/gate.sh --tier both" >&2
  exit 4
fi

CMD=(bash "$RUNNER" --set "$SET" --files "${SEL_ARR[*]}" "$ROOT")
if [ "$DRY" -eq 1 ]; then
  echo "scoped-tests: DRY RUN — would execute:"
  printf '    %q' "${CMD[@]}"; echo
  echo "scoped-tests: nothing ran. A dry run is not a verdict."
  exit 0
fi

# `exec` so the runner's exit status IS this script's, with nothing running
# afterwards that could replace it — the same discipline gate.sh's `rc=$?`
# placement enforces. The runner's own SCOPE/RESULT lines are therefore the last
# thing printed.
exec "${CMD[@]}"
