#!/usr/bin/env bash
#
# gate.sh — the ONE invocation surface for devrc's test gate, whose exit status
# is trustworthy.
#
# 🔴 WHY THIS EXISTS. `scripts/run-tests.sh` and `scripts/run-node-tests.sh` have
# always exited truthfully; what kept failing was READING that status. Both
# emit thousands of lines, so every consumer — CI notes, the pre-push tier,
# agents, humans — wraps them in a pipe:
#
#     bash scripts/run-tests.sh 2>&1 | tail -40 ; echo "rc=$?"
#     nix build .#checks.x86_64-linux.pytests 2>&1 | tail -60 ; echo "BUILD_RC=$?"
#
# and a pipeline's status is the LAST command's. On 2026-08-11 four agents hit
# this independently, reporting `exit code 0` over output containing
# `RESULT: FAIL` and `failed=1`; the same day's rules audit recorded
# `BUILD_RC=0` over a genuinely red `nix build`. The repo's own CLAUDE.md had to
# tell everyone to count `PASSED`/`FAILED` lines instead of reading the status
# — a workaround for a bug, promoted to house style.
#
# The fix is two-sided and BOTH sides are needed:
#
#   1. The runners now put the status IN THE CONTENT, on exactly one line
#      (`RESULT: FAIL (exit=1)`), from a single writer behind an EXIT trap. So
#      the truth survives a pipe even when someone bypasses this script.
#   2. This script removes the REASON to pipe. It sends the full output to a
#      LOG FILE (a redirect, not a pipe), prints a bounded summary — the SUMMARY
#      block and the verdict, ~40 lines — and exits with the status it actually
#      observed. Nothing here runs after the status is captured except things
#      whose own status is discarded.
#
# And then it checks the two against each other, because "the exit code is
# truthful now" is a claim like any other:
#
#   * status non-zero + content says PASS  -> FATAL, exit 90
#   * status zero     + content says FAIL  -> FATAL, exit 90
#   * status zero     + no verdict at all  -> FATAL, exit 90 (truncated run)
#   * `panic: test timed out` anywhere     -> FATAL, exit 90
#
# A disagreement is never resolved in favour of the reassuring side. Exit 90 is
# deliberately its own code: it means "this gate could not vouch for its own
# answer", which is a different finding from "the tests failed".
#
# Usage:
#   scripts/gate.sh [--tier pytest|node|both] [--set hermetic|all]
#                   [--timeout SECS] [--log-dir DIR] [ROOT]
#
#     --tier both      (default) run both runners; the gate is red if either is.
#     --set            passed through to run-tests.sh (pytest tier only).
#     --timeout SECS   wall-clock cap per tier (default 3600, 0 disables). A
#                      tier that hits it is FAIL with reason=timeout, never a
#                      hang someone reads as "still going".
#     --log-dir DIR    where full logs land (default: a mktemp -d).
#
# Exit: 0 = every selected tier passed and agreed with its own content.
#       1 = a tier genuinely failed.
#       2 = a usage/precondition problem in this script.
#      90 = status/content disagreement, or a truncated run: NOT a verdict.
#
# TEST SEAM: DEVRC_GATE_PYTEST_RUNNER / DEVRC_GATE_NODE_RUNNER override the
# runner paths. They exist so the negative controls in
# scripts/tests/test_gate_exit_truthfulness.py can drive this script against a
# runner that is forced red, forced to hang, or forced to LIE (exit 0 while
# printing FAIL) in seconds instead of running the real 7k-test suite. They are
# a seam for tests, not a supported way to run the gate.

set -uo pipefail

TIER="both"
SET="hermetic"
TIMEOUT="${DEVRC_GATE_TIMEOUT:-3600}"
LOG_DIR=""
ROOT=""

while [ $# -gt 0 ]; do
  case "$1" in
    --tier) TIER="${2:-both}"; shift; [ $# -gt 0 ] && shift ;;
    --tier=*) TIER="${1#*=}"; shift ;;
    --set) SET="${2:-hermetic}"; shift; [ $# -gt 0 ] && shift ;;
    --set=*) SET="${1#*=}"; shift ;;
    --timeout) TIMEOUT="${2:-3600}"; shift; [ $# -gt 0 ] && shift ;;
    --timeout=*) TIMEOUT="${1#*=}"; shift ;;
    --log-dir) LOG_DIR="${2:-}"; shift; [ $# -gt 0 ] && shift ;;
    --log-dir=*) LOG_DIR="${1#*=}"; shift ;;
    -h|--help) sed -n '2,70p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) ROOT="$1"; shift ;;
  esac
done

case "$TIER" in
  pytest|node|both) : ;;
  *) echo "gate: FATAL — unknown --tier '$TIER' (want pytest|node|both)" >&2; exit 2 ;;
esac
case "$TIMEOUT" in
  ''|*[!0-9]*) echo "gate: FATAL — --timeout must be a whole number of seconds, got '$TIMEOUT'" >&2; exit 2 ;;
esac

# --- GUARD 9: NO TEST MAY OPERATE ON THE REPO THE SUITE RUNS FROM -------------
# 🔴 BEFORE the ROOT block below, not after it: with GIT_DIR set and no
# GIT_WORK_TREE, git takes the CWD as the work tree, so
# `rev-parse --show-toplevel` returns `<repo>/scripts` and this script dies
# `exit 127` with no verdict. The 2026-08-21 incident, the reproduction, and the
# reason this block is spelled in three files rather than sourced from one are
# in `scripts/run-tests.sh`'s copy of this header. The SET is owned by
# `scripts/testlib/gitenv.py::REPO_POINTER_VARS`; every spelling is pinned
# two-way by `scripts/tests/test_git_repo_isolation.py`.
DEVRC_GIT_REPO_POINTERS=(
  GIT_DIR                            # the repository itself; beats -C
  GIT_WORK_TREE                      # the working tree
  GIT_COMMON_DIR                     # where refs/config actually live
  GIT_INDEX_FILE                     # the index a `git add` writes
  GIT_OBJECT_DIRECTORY               # where new objects are written
  GIT_ALTERNATE_OBJECT_DIRECTORIES   # extra object stores
  GIT_NAMESPACE                      # the ref namespace refs land in
  GIT_PREFIX                         # hook-injected pathspec prefix
  GIT_GRAFT_FILE                     # repo-scoped grafts
  GIT_SHALLOW_FILE                   # repo-scoped shallow list
  GIT_CONFIG                         # legacy: the file `git config` WRITES
)
unset "${DEVRC_GIT_REPO_POINTERS[@]}"

if [ -z "$ROOT" ]; then
  ROOT="$(git -C "$(dirname "${BASH_SOURCE[0]}")" rev-parse --show-toplevel 2>/dev/null || true)"
  [ -n "$ROOT" ] || ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
fi
cd "$ROOT" || { echo "gate: FATAL — cannot cd to ROOT=$ROOT" >&2; exit 2; }

PYTEST_RUNNER="${DEVRC_GATE_PYTEST_RUNNER:-$ROOT/scripts/run-tests.sh}"
NODE_RUNNER="${DEVRC_GATE_NODE_RUNNER:-$ROOT/scripts/run-node-tests.sh}"

if [ -z "$LOG_DIR" ]; then
  LOG_DIR="$(mktemp -d -t devrc-gate-XXXXXX)"
fi
mkdir -p "$LOG_DIR" || { echo "gate: FATAL — cannot create log dir $LOG_DIR" >&2; exit 2; }

# `timeout` is not universally present (busybox coreutils on this box provide
# it, the nix sandbox provides GNU's). Degrade LOUDLY rather than silently
# dropping the cap: a gate that quietly stopped enforcing its own timeout is the
# reassuring-zero shape all over again.
TIMEOUT_BIN=""
if [ "$TIMEOUT" -gt 0 ]; then
  if command -v timeout >/dev/null 2>&1; then
    TIMEOUT_BIN="timeout"
  else
    echo "gate: WARNING — no \`timeout\` on PATH; running WITHOUT a wall-clock cap." >&2
  fi
fi

GATE_FAIL=0
GATE_UNVOUCHED=0
TIER_LINES=()

# Run one runner. Everything that matters is decided from ($rc, log content).
run_tier() { # $1 = label, $2.. = command
  local label="$1"; shift
  local log="$LOG_DIR/$label.log"
  local rc reason verdict panic

  echo "gate: === $label === (full log: $log)"
  if [ -n "$TIMEOUT_BIN" ]; then
    # --kill-after: a runner that ignores TERM must not outlive the cap either.
    "$TIMEOUT_BIN" --kill-after=30s "$TIMEOUT" "$@" >"$log" 2>&1
  else
    "$@" >"$log" 2>&1
  fi
  # 🔴 NOTHING may run between the command and this line. This is the whole bug
  # in one statement: an `echo`, a `tee`, a `| tail` here and the status below
  # is that command's, not the runner's.
  rc=$?

  # --- read the CONTENT, independently of the status -------------------------
  # Anchored at column 0 so a test fixture's own output (`RESULT: all good`
  # appears in the real suite) cannot be mistaken for the runner's verdict.
  verdict="$(grep -aE '^RESULT: (PASS|FAIL)' "$log" | tail -1 || true)"
  panic="$(grep -ac 'panic: test timed out' "$log" || true)"
  : "${panic:=0}"

  reason=""
  if [ "$rc" -eq 124 ] || [ "$rc" -eq 137 ]; then
    reason="timeout after ${TIMEOUT}s"
  fi

  # --- cross-check: status vs content ----------------------------------------
  local disagree=""
  if [ "$panic" -gt 0 ]; then
    disagree="found $panic 'panic: test timed out' line(s) — the run was TRUNCATED, so every count below it is missing"
  elif [ -z "$verdict" ]; then
    if [ "$rc" -eq 0 ]; then
      disagree="exited 0 but printed NO 'RESULT:' verdict line — the runner did not reach its own summary"
    fi
  elif [ "$rc" -eq 0 ] && [ "${verdict#RESULT: FAIL}" != "$verdict" ]; then
    disagree="exited 0 while printing '$verdict'"
  elif [ "$rc" -ne 0 ] && [ "${verdict#RESULT: PASS}" != "$verdict" ]; then
    disagree="exited $rc while printing '$verdict'"
  fi

  # --- bounded summary to stdout; the full log stays on disk -----------------
  local start
  start="$(grep -an '^=\{8,\} .*SUMMARY' "$log" | tail -1 | cut -d: -f1 || true)"
  if [ -n "$start" ]; then
    sed -n "${start},\$p" "$log"
  else
    echo "gate: (no SUMMARY block in $label's log — showing its last 25 lines)"
    tail -25 "$log"
  fi

  if [ -n "$disagree" ]; then
    echo "gate: FATAL — $label $disagree." >&2
    echo "  The exit status and the printed verdict do not agree, so this gate" >&2
    echo "  cannot vouch for either. That is NOT the same finding as 'the tests" >&2
    echo "  failed' — read $log before believing any number in it." >&2
    GATE_UNVOUCHED=1
    TIER_LINES+=("UNVOUCHED  $label  exit=$rc  verdict='${verdict:-<none>}'")
  elif [ "$rc" -ne 0 ]; then
    GATE_FAIL=1
    TIER_LINES+=("FAIL  $label  exit=$rc${reason:+  ($reason)}  verdict='${verdict:-<none>}'")
  else
    TIER_LINES+=("PASS  $label  exit=0  verdict='$verdict'")
  fi
  echo
}

if [ "$TIER" = "pytest" ] || [ "$TIER" = "both" ]; then
  run_tier pytest bash "$PYTEST_RUNNER" --set "$SET" "$ROOT"
fi
if [ "$TIER" = "node" ] || [ "$TIER" = "both" ]; then
  run_tier node bash "$NODE_RUNNER" "$ROOT"
fi

echo "======================== GATE ========================"
for l in "${TIER_LINES[@]}"; do echo "  $l"; done
echo "  logs: $LOG_DIR"

# Precedence: "could not vouch" outranks "failed", which outranks "passed". A
# gate that cannot trust its own instrument must never report a plain FAIL, let
# alone a PASS.
if [ "$GATE_UNVOUCHED" -ne 0 ]; then
  echo "GATE: RESULT=UNVOUCHED exit=90"
  exit 90
fi
if [ "$GATE_FAIL" -ne 0 ]; then
  echo "GATE: RESULT=FAIL exit=1"
  exit 1
fi
echo "GATE: RESULT=PASS exit=0"
exit 0
