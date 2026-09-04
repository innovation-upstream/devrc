#!/usr/bin/env bash
# break-glass-merge — open `main`'s required-status-check window, optionally
# merge a PR through it, then CLOSE it and PROVE it closed.
#
# WHY THIS EXISTS
# ---------------
# `innovation-upstream/devrc` requires `tekton/devrc-pytests` +
# `tekton/devrc-nodetests` on `main` with `enforce_admins: true`. When Tekton is
# down or wedged, NOTHING merges and there is no admin override. The escape
# hatch is to delete the `required_status_checks` sub-resource, merge, and put
# protection back.
#
# That hatch is documented in CLAUDE.md as prose. Prose is what went wrong:
# MEASURED over three uses on 2026-08-29/30, `DELETE` opened the window and
# `PATCH` could NOT close it — two restores failed, one of them inside an EXIT
# trap that fired exactly as designed and still left `main` open, because the
# untested command was *inside* the safety net.
#
# THE THREE FACTS THIS SCRIPT IS BUILT AROUND
# -------------------------------------------
#  1. `PATCH .../protection/required_status_checks` **404s** once the
#     sub-resource is deleted (`Required status checks not enabled`). It updates
#     checks that EXIST; it cannot recreate a deleted sub-resource. So this
#     script never issues a PATCH against protection. Closing the window needs a
#     full `PUT` of the WHOLE protection object.
#  2. A PARTIAL `PUT` returns **200** and silently drops every key it does not
#     carry — `enforce_admins`, force-push and deletion settings included. So
#     "the PUT returned 200" is a claim about the REQUEST, never about the
#     protection. All 11 keys are load-bearing, and the `app_id` pinning inside
#     `checks` is what binds a restored context to Tekton rather than to any app
#     that can post the same name.
#  3. Therefore the READ-BACK IS NOT OPTIONAL, and neither is capturing first.
#     Without the step-1 capture you cannot restore at all.
#
# 🔴 THE SAFETY-NET LESSON, ENCODED STRUCTURALLY
# ----------------------------------------------
# `restore_and_verify` is called on the NORMAL path and from the EXIT trap. It
# is ONE function, so the trap can never contain a command that has not just
# been exercised by an ordinary run. The historical failure was a trap holding
# an untested `PATCH`; that shape is now unrepresentable here.
#
# 🔴 NOTHING IS REDIRECTED TO /dev/null. The 2026-08-30 restore looked "silent"
# only because the idiom around it was `>/dev/null 2>&1`, which discarded the
# very message naming the cause. Every API error is printed.
#
# SELF-TEST MODE
# --------------
# Called with no `--pr`, this opens the window, merges NOTHING, and closes it.
# That is deliberately the SAME code path as a real break-glass run — only the
# payload differs. The dangerous half (DELETE / PUT / read-back) is exercised
# identically, which is what makes a real run trustworthy: it is not a mode that
# has never run.
#
# EXIT CODES
#   0  window opened and closed; read-back FAITHFUL key-by-key (+ merge ok)
#   2  usage / environment error                        — nothing touched
#   3  capture missing, malformed or incomplete         — nothing touched
#   4  opening the window failed                        — nothing touched
#   5  the merge failed (window was still closed + verified afterwards)
#   6  🔴 RESTORE FAILED — `main` MAY BE UNPROTECTED. Manual action required.
#   7  🔴 read-back does NOT match the capture — protection differs.
set -euo pipefail

REPO_DEFAULT="innovation-upstream/devrc"
BRANCH_DEFAULT="main"

# --- BEGIN CAPTURE_JQ ---
# The projection of a protection object into the exact 11-key body the PUT
# endpoint accepts. ONE definition, two consumers: the capture and the
# read-back both use it, so a drift between them is unrepresentable. Its
# fidelity is pinned by scripts/tests/test_break_glass_merge.py, which runs
# THIS string (extracted from THIS file) through the real jq.
CAPTURE_JQ='{
  required_status_checks:{strict:.required_status_checks.strict,
    checks:[.required_status_checks.checks[]|{context,app_id}]},
  enforce_admins:.enforce_admins.enabled,
  required_pull_request_reviews, restrictions,
  required_linear_history:.required_linear_history.enabled,
  allow_force_pushes:.allow_force_pushes.enabled,
  allow_deletions:.allow_deletions.enabled,
  block_creations:.block_creations.enabled,
  required_conversation_resolution:.required_conversation_resolution.enabled,
  lock_branch:.lock_branch.enabled,
  allow_fork_syncing:.allow_fork_syncing.enabled}'
# --- END CAPTURE_JQ ---

# Every key the PUT body must carry.
#
# 🔴 SPLIT BY WHAT A VALID VALUE IS, not merely by presence. `has()` alone is
# VACUOUS here and a mutation sweep proved it: the projection above names all
# eleven keys unconditionally, so jq emits `allow_force_pushes: null` for a
# source object that lacks it, and `has("allow_force_pushes")` is still TRUE.
# A guard that can never fire reads as coverage while providing none — worse
# than no guard, because it stops anyone looking. What actually distinguishes a
# usable capture is the VALUE: the eight booleans must be booleans, or the
# restore PUTs a null over a setting and GitHub takes its own default.
BOOL_KEYS=(
  enforce_admins required_linear_history allow_force_pushes allow_deletions
  block_creations required_conversation_resolution lock_branch
  allow_fork_syncing
)
# Legitimately null on this repo, but REQUIRED by the endpoint — so presence,
# not truthiness, is the right check for exactly these two.
NULLABLE_KEYS=(required_pull_request_reviews restrictions)

REQUIRED_KEYS=(required_status_checks "${BOOL_KEYS[@]}" "${NULLABLE_KEYS[@]}")

REPO="$REPO_DEFAULT"
BRANCH="$BRANCH_DEFAULT"
PR=""
ASSUME_YES=0
WORKDIR=""

die() { printf '%s\n' "break-glass: $*" >&2; exit "${2:-2}"; }
say() { printf '%s\n' "break-glass: $*" >&2; }

usage() {
  cat >&2 <<'EOF'
usage: break-glass-merge.sh [--pr N] [--repo OWNER/NAME] [--branch NAME]
                            [--workdir DIR] --yes

  --pr N       merge PR N while the window is open. Omit to run the
               self-test: open the window, merge nothing, close it.
  --yes        required. This writes to branch protection.

Exit codes: 0 ok · 2 usage · 3 bad capture · 4 open failed · 5 merge failed
            6 RESTORE FAILED (main may be unprotected) · 7 read-back mismatch
EOF
  exit 2
}

while [ $# -gt 0 ]; do
  case "$1" in
    --pr)      PR="${2:-}";      shift 2 || usage ;;
    --repo)    REPO="${2:-}";    shift 2 || usage ;;
    --branch)  BRANCH="${2:-}";  shift 2 || usage ;;
    --workdir) WORKDIR="${2:-}"; shift 2 || usage ;;
    --yes)     ASSUME_YES=1;     shift ;;
    -h|--help) usage ;;
    *)         say "unknown argument: $1"; usage ;;
  esac
done

[ "$ASSUME_YES" = 1 ] || { say "refusing to touch branch protection without --yes"; usage; }
[ -n "$REPO" ] && [ -n "$BRANCH" ] || usage
if [ -n "$PR" ]; then
  case "$PR" in (*[!0-9]*|"") die "--pr must be a number, got: $PR" ;; esac
fi

command -v gh   >/dev/null 2>&1 || die "gh not on PATH"
command -v jq   >/dev/null 2>&1 || die "jq not on PATH"
command -v diff >/dev/null 2>&1 || die "diff not on PATH"

if [ -z "$WORKDIR" ]; then
  WORKDIR="$(mktemp -d "${TMPDIR:-/tmp}/break-glass.XXXXXX")"
fi
mkdir -p "$WORKDIR"
CAPTURE="$WORKDIR/restore.json"
AFTER="$WORKDIR/after.json"

PROT="/repos/$REPO/branches/$BRANCH/protection"
WINDOW_OPEN=0
RESTORE_DONE=0
VERDICT=0

# ---------------------------------------------------------------- capture ---
# Step 1. Without this the window cannot be closed at all, so it runs BEFORE
# anything is deleted and its result is validated before anything is deleted.
capture() {
  say "capturing $BRANCH protection -> $CAPTURE"
  if ! gh api "$PROT" --jq "$CAPTURE_JQ" > "$CAPTURE"; then
    die "could not read $PROT — refusing to open a window I could not close" 3
  fi
  [ -s "$CAPTURE" ] || die "capture is EMPTY — refusing to proceed" 3
  jq -e . "$CAPTURE" >/dev/null 2>&1 || die "capture is not valid JSON — refusing" 3

  # NOTE: there is deliberately NO `has()` check for NULLABLE_KEYS here. The
  # projection names every key unconditionally, so jq emits them even when the
  # source object lacks them and `has()` is ALWAYS true — a check that cannot
  # fail. A sweep caught it (`nullable-presence-loop-removed` SURVIVED with the
  # loop deleted, i.e. the loop was doing nothing). That invariant belongs to
  # the FILTER and is pinned there, by
  # test_break_glass_merge.py::test_the_capture_projection_runs_under_real_jq.
  local k
  for k in "${BOOL_KEYS[@]}"; do
    jq -e ".[\"$k\"] | type == \"boolean\"" "$CAPTURE" >/dev/null \
      || die "capture's '$k' is $(jq -c ".[\"$k\"]" "$CAPTURE"), not a boolean — restoring it would PUT a null and let GitHub pick the default" 3
  done
  jq -e '.required_status_checks | type == "object"' "$CAPTURE" >/dev/null \
    || die "capture has no required_status_checks object — nothing to restore" 3
  local n
  n="$(jq -r '.required_status_checks.checks | length' "$CAPTURE")"
  [ "$n" -gt 0 ] 2>/dev/null \
    || die "capture has ZERO required checks — that is already an open window, not a captured one" 3
  jq -e '[.required_status_checks.checks[] | select(.app_id == null)] | length == 0' "$CAPTURE" >/dev/null \
    || die "a captured check has a null app_id — restoring it would bind the context to ANY app" 3
  say "capture OK: ${#REQUIRED_KEYS[@]} keys, $n required check(s), all app_id-pinned"
}

# ------------------------------------------------------- restore + verify ---
# 🔴 Called on the normal path AND from the EXIT trap. One function, so the
# trap cannot hold an untested command. Idempotent via RESTORE_DONE.
restore_and_verify() {
  [ "$RESTORE_DONE" = 1 ] && return 0
  RESTORE_DONE=1

  # A full PUT of the WHOLE object. Never PATCH: PATCH cannot recreate a
  # deleted sub-resource and 404s with "Required status checks not enabled".
  say "closing the window: PUT $PROT (full object, ${#REQUIRED_KEYS[@]} keys)"
  if ! gh api -X PUT "$PROT" --input "$CAPTURE"; then
    say "🔴 RESTORE FAILED — '$BRANCH' MAY BE UNPROTECTED RIGHT NOW."
    say "🔴 restore by hand:  gh api -X PUT $PROT --input $CAPTURE"
    VERDICT=6
    return 1
  fi
  WINDOW_OPEN=0

  # The read-back is the only thing that can distinguish "the PUT returned 200"
  # from "the protection is what it was". Same projection as the capture.
  say "reading protection back"
  if ! gh api "$PROT" --jq "$CAPTURE_JQ" > "$AFTER"; then
    say "🔴 could not read protection back — cannot confirm the restore landed"
    VERDICT=7
    return 1
  fi

  local before_n after_n
  before_n="$(jq -S -c . "$CAPTURE")"
  after_n="$(jq -S -c . "$AFTER")"
  if [ "$before_n" = "$after_n" ]; then
    say "read-back FAITHFUL — every one of the ${#REQUIRED_KEYS[@]} keys matches the capture"
    local k
    for k in "${REQUIRED_KEYS[@]}"; do
      say "  ok  $k"
    done
    return 0
  fi

  say "🔴 read-back MISMATCH — protection differs from the capture:"
  diff <(jq -S . "$CAPTURE") <(jq -S . "$AFTER") >&2 || true
  local k bv av
  for k in "${REQUIRED_KEYS[@]}"; do
    bv="$(jq -S -c ".[\"$k\"]" "$CAPTURE")"
    av="$(jq -S -c ".[\"$k\"]" "$AFTER")"
    if [ "$bv" = "$av" ]; then say "  ok    $k"; else say "  DIFF  $k: $bv -> $av"; fi
  done
  VERDICT=7
  return 1
}

on_exit() {
  local rc=$?
  if [ "$WINDOW_OPEN" = 1 ] && [ "$RESTORE_DONE" = 0 ]; then
    say "trap: the window is still open — restoring"
    restore_and_verify || true
  fi
  if [ "$VERDICT" != 0 ]; then exit "$VERDICT"; fi
  exit "$rc"
}
trap on_exit EXIT

# ------------------------------------------------------------------- run ---
say "repo=$REPO branch=$BRANCH pr=${PR:-<none, self-test>}"
capture

say "opening the window: DELETE $PROT/required_status_checks"
if ! gh api -X DELETE "$PROT/required_status_checks"; then
  die "could not delete required_status_checks — window NOT opened, nothing to restore" 4
fi
WINDOW_OPEN=1
say "window is OPEN — $BRANCH has no required status checks right now"

MERGE_RC=0
if [ -n "$PR" ]; then
  say "merging PR #$PR"
  if ! gh pr merge "$PR" --repo "$REPO" --squash; then
    say "merge of #$PR FAILED — closing the window anyway"
    MERGE_RC=5
  else
    say "merged #$PR"
  fi
else
  say "self-test: merging nothing; the window closes immediately"
fi

restore_and_verify || true

if [ "$VERDICT" != 0 ]; then exit "$VERDICT"; fi
if [ "$MERGE_RC" != 0 ]; then exit "$MERGE_RC"; fi
say "DONE — window opened and closed, protection verified identical to capture"
say "capture kept at: $CAPTURE"
exit 0
