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
# Env:
#   DEVRC_GATE_SLOTS      how many gate runs may execute at once on this box
#                         (default 2; 0 disables the limiter). See the SLOT
#                         LIMITER block for the measurement that set it.
#   DEVRC_GATE_SLOT_WAIT  seconds to queue for a slot before giving up and
#                         running unslotted anyway (default 3600). Fail-open.
#   DEVRC_GATE_NO_REEXEC  =1 to run against the ambient PATH instead of
#                         re-entering `nix develop`. See the RE-EXEC block.
#   DEVRC_GATE_SLOT_DIR   which slot pool to join (default: a fixed per-uid path
#                         under /tmp). Naming one also opts a pytest-nested run
#                         back INTO the limiter — see the SLOT LIMITER block.
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
    # Print the header comment up to the first line of code, rather than a
    # hardcoded range: `2,70p` silently truncated --help the moment the header
    # grew, which is a help text that lies about the flags it documents.
    -h|--help) awk 'NR>1 { if ($0 !~ /^#/) exit; print }' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
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
# 🔴 AND GUARD 9's OWN SEAMS. `DEVRC_GITENV_PROTECT` redirects the DETECTOR at a
# different repository and `DEVRC_GITENV_MODE` decides whether it fails or
# merely reports; #683's audit measured `DEVRC_GITENV_PROTECT=":"` producing
# `protected-git-dirs=0` and a GREEN run over a real escape, and
# `=/nonexistent/x` producing a marker line that asserted coverage it did not
# have. One inherited variable defeating every layer is the bug this guard
# exists for. Owned by `testlib/gitenv.py::CONTROL_VARS`, pinned two-way.
DEVRC_GITENV_CONTROL_VARS=(
  DEVRC_GITENV_PROTECT               # which git dirs the detector watches
  DEVRC_GITENV_MODE                  # enforce | report | auto
)
unset "${DEVRC_GIT_REPO_POINTERS[@]}"
unset "${DEVRC_GITENV_CONTROL_VARS[@]}"

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

# --- IS THIS AN AUTOMATED / NESTED INVOCATION? ---------------------------------
# The two features below must be INERT for the suite's own meta-tests, which
# drive this script dozens of times with fake runners that finish in
# milliseconds. 🔴 But they are inert under DIFFERENT conditions, and collapsing
# them into one flag is a bug in both directions: it would make the limiter
# untestable (every test that drives this script sets the seams) AND leave the
# re-exec firing under a bare PYTEST_CURRENT_TEST. So they are computed apart.
#
# GATE_NESTED — "we are inside a pytest process". Same nesting signal
# `run-tests.sh` uses to force nested runs serial, and inherited by child
# processes (measured there, serial and under xdist), so it catches a test
# spawning this script through a wrapper no seam variable touches.
GATE_NESTED=0
[ -n "${PYTEST_CURRENT_TEST:-}" ] && GATE_NESTED=1
# GATE_SEAMED — "a test is driving this script against a fake runner".
GATE_SEAMED=0
[ -n "${DEVRC_GATE_PYTEST_RUNNER:-}${DEVRC_GATE_NODE_RUNNER:-}" ] && GATE_SEAMED=1

# --- RE-EXEC INTO THE REPO'S OWN DEV SHELL -------------------------------------
# 🔴 WHY. `run-tests.sh` refuses to run outside an environment carrying its
# REQUIRED_TOOLS, and prints the exact `nix develop …` line that fixes it.
# MEASURED 2026-09-08 over the 100 gate log dirs still in /tmp that used the
# default log location: 100 of 100 died on that FATAL — `logrotate dash` missing
# — with the pytest tier never starting. Every one of those was a human or an
# agent typing `scripts/gate.sh`, reading the instruction, and typing it again
# with the prefix. The node tier ran anyway in all 100, so each wasted attempt
# also paid the node suite twice.
#
# The message was never wrong; making a correct instruction be re-typed by hand
# is the defect. So: if we are not already in a sanctioned gate environment and
# the repo has a flake, re-enter it and run the SAME arguments there.
#
# 🔴 THE RE-EXEC IS FULLY RESOLVED, NOT `"$@"`. ROOT may have been derived from
# the script's own location rather than typed, and LOG_DIR may be a mktemp dir
# this process just created — replaying the original argv would re-derive both
# INSIDE the new shell, where `mktemp` honours nix's per-shell TMPDIR and would
# silently choose a DIFFERENT log dir from the one just announced. Passing the
# resolved values through makes the inner run land exactly where the outer one
# said it would.
#
# 🔴 LOOP GUARD. `DEVRC_GATE_ENV=1` comes from the flake's shellHook, so the
# normal exit condition is that the inner run sees it. If that hook ever stops
# setting it, this would re-exec forever; `DEVRC_GATE_REEXEC` is set by US and
# breaks the cycle on the second pass regardless. Belt and braces, because the
# failure mode of getting it wrong is an unkillable fork bomb rather than a
# wrong answer. Opt out entirely with DEVRC_GATE_NO_REEXEC=1.
if [ "$GATE_SEAMED" -eq 0 ] && [ "$GATE_NESTED" -eq 0 ] \
   && [ "${DEVRC_GATE_NO_REEXEC:-0}" != "1" ] \
   && [ "${DEVRC_GATE_ENV:-0}" != "1" ] \
   && [ "${DEVRC_GATE_REEXEC:-0}" != "1" ] \
   && [ -f "$ROOT/flake.nix" ] \
   && command -v nix >/dev/null 2>&1; then
  echo "gate: not in a gate environment (DEVRC_GATE_ENV unset) — re-entering \`nix develop $ROOT\`."
  echo "gate: (set DEVRC_GATE_NO_REEXEC=1 to run against the ambient PATH instead)"
  export DEVRC_GATE_REEXEC=1
  # `exec`, so there is no wrapper process to swallow the inner status — the
  # whole point of this script is that its own exit code is readable.
  exec nix develop "$ROOT" --command bash "${BASH_SOURCE[0]}" \
      --tier "$TIER" --set "$SET" --timeout "$TIMEOUT" --log-dir "$LOG_DIR" "$ROOT"
fi

# --- SLOT LIMITER: BOUND HOW MANY GATES RUN AT ONCE ----------------------------
# 🔴 WHY. Nothing serialised gate runs, and on a box shared by several agent
# sessions they all start their own. MEASURED 2026-09-08 across 237 real runs
# (the runner's own `run=NNNNs`), bucketed by how many other runs overlapped:
#
#     overlapping others   runs   median gate
#              0             73      14.5 min
#              1             39      18.7 min
#              2             35      19.9 min
#              3             23      23.2 min
#              4             28      23.3 min
#              5             10      31.4 min
#              6+            28      48.9 min
#
# 3.4x, and SUPERLINEAR — 7 runs x 4 workers is 28 workers on 24 cores, which
# fair-shares to ~1.2x, so the rest is not CPU division. It is subprocess-spawn
# storms, timing-sensitive tests drifting toward their deadlines, and the
# suite's own meta-tests spawning nested runners inside that. 35 of the 117 red
# runs in the same window died on SIGTERM at the 3600s cap: an hour of wall
# clock spent to produce no verdict at all.
#
# 🔴 THE SLOT IS TAKEN BEFORE `run_tier`, SO QUEUE TIME IS NOT TEST TIME. The
# --timeout cap starts when the tier starts. A run that waited 20 minutes for a
# slot still gets its full budget; queueing can never manufacture the timeout
# it exists to prevent.
#
# 🔴 FAIL-OPEN, ALWAYS. A limiter that can block the gate is worse than a slow
# gate. No `flock`, an unwritable slot dir, or a wait past DEVRC_GATE_SLOT_WAIT
# all print what happened and RUN ANYWAY. Nothing here can change the verdict.
#
# The slot dir is a FIXED path, deliberately not under $TMPDIR: every real run
# is inside `nix develop`, which gives each shell its own TMPDIR, so a
# TMPDIR-relative lock would give every run its own private set of slots and
# limit nothing. Per-uid so it cannot collide across users.
#
# 🔴 NESTING vs TESTABILITY, and why DEVRC_GATE_SLOT_DIR settles both. Inside a
# pytest process the limiter is inert by default: N xdist workers each spawning
# gate.sh would otherwise queue behind slots held by their own parent — a
# deadlock built out of a performance feature. But the limiter's OWN tests run
# inside pytest too, so a blanket "inert when nested" makes the one feature
# whose failure mode is a hang the one feature nothing exercises. Naming a slot
# dir is the opt-in: it is private to the caller, so it cannot contend with a
# real gate run on this box NOR with another test, and a caller that named one
# has demonstrably thought about which pool it is joining.
GATE_SLOTS="${DEVRC_GATE_SLOTS:-2}"
GATE_SLOT_WAIT="${DEVRC_GATE_SLOT_WAIT:-3600}"
case "$GATE_SLOTS" in ''|*[!0-9]*) echo "gate: FATAL — DEVRC_GATE_SLOTS must be a whole number, got '$GATE_SLOTS'" >&2; exit 2 ;; esac
case "$GATE_SLOT_WAIT" in ''|*[!0-9]*) echo "gate: FATAL — DEVRC_GATE_SLOT_WAIT must be a whole number, got '$GATE_SLOT_WAIT'" >&2; exit 2 ;; esac

GATE_SLOT_HELD=""      # which slot we hold, for the report line
GATE_SLOT_FD=""        # the fd carrying the lock; closed in the tier's children
GATE_SLOT_DIR="${DEVRC_GATE_SLOT_DIR:-/tmp/devrc-gate-slots-$(id -u 2>/dev/null || echo 0)}"

acquire_slot() {
  if [ "$GATE_NESTED" -eq 1 ] && [ -z "${DEVRC_GATE_SLOT_DIR:-}" ]; then
    echo "gate: slot limiter INERT (nested inside pytest, and no DEVRC_GATE_SLOT_DIR named)."
    return 0
  fi
  [ "$GATE_SLOTS" -gt 0 ] || { echo "gate: slot limiter DISABLED (DEVRC_GATE_SLOTS=0)."; return 0; }
  if ! command -v flock >/dev/null 2>&1; then
    echo "gate: WARNING — no \`flock\` on PATH; running WITHOUT a concurrency slot." >&2
    return 0
  fi
  mkdir -p "$GATE_SLOT_DIR" 2>/dev/null || {
    echo "gate: WARNING — cannot create $GATE_SLOT_DIR; running WITHOUT a concurrency slot." >&2
    return 0
  }
  local waited=0 i fd announced=0
  while :; do
    for i in $(seq 1 "$GATE_SLOTS"); do
      # fd 200+i is reserved for the held slot: it must stay open for the life
      # of this process, which is exactly what makes the lock mean "a gate is
      # running" rather than "a gate started once".
      fd=$((200 + i))
      eval "exec ${fd}>>'$GATE_SLOT_DIR/slot-$i.lock'" 2>/dev/null || continue
      if flock -n "$fd"; then
        GATE_SLOT_HELD="$i"
        GATE_SLOT_FD="$fd"
        if [ "$waited" -gt 0 ]; then
          echo "gate: acquired slot $i/$GATE_SLOTS after waiting ${waited}s."
        else
          echo "gate: acquired slot $i/$GATE_SLOTS immediately."
        fi
        return 0
      fi
      eval "exec ${fd}>&-" 2>/dev/null || true
    done
    if [ "$waited" -ge "$GATE_SLOT_WAIT" ]; then
      echo "gate: WARNING — all $GATE_SLOTS slot(s) still busy after ${waited}s" >&2
      echo "  (DEVRC_GATE_SLOT_WAIT=$GATE_SLOT_WAIT). Running WITHOUT a slot rather than" >&2
      echo "  blocking further — this run will contend, and may be slower than the" >&2
      echo "  medians in this script's header." >&2
      return 0
    fi
    if [ "$announced" -eq 0 ]; then
      echo "gate: all $GATE_SLOTS slot(s) busy — queueing (another gate is running on this box)."
      echo "gate: waiting up to ${GATE_SLOT_WAIT}s; the --timeout budget starts AFTER the slot is taken."
      announced=1
    fi
    sleep 5
    waited=$((waited + 5))
  done
}

acquire_slot

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
  # 🔴 THE SLOT FD IS CLOSED IN THE CHILD, and this is not tidiness. A flock
  # lives on the open file DESCRIPTION, which fork/exec inherits — so a single
  # process that outlives this gate (a daemonised helper, an orphaned server, a
  # runner subprocess that escapes its group) keeps holding the slot after we
  # exit. Nobody would ever see it: the pool would just be permanently one slot
  # smaller, every later run would queue for DEVRC_GATE_SLOT_WAIT and then
  # fail-open, and the limiter would be silently off while still printing that
  # it was on. This repo has been bitten by orphans holding a resource before.
  #
  # The subshell exists ONLY to scope that close, and it ends in `exec` so it is
  # REPLACED by the command — the subshell contributes no exit status of its
  # own. 🔴 Nothing may come between this and `rc=$?`; that is the whole bug
  # this script was written for.
  if [ -n "$TIMEOUT_BIN" ]; then
    # --kill-after: a runner that ignores TERM must not outlive the cap either.
    ( [ -n "$GATE_SLOT_FD" ] && eval "exec ${GATE_SLOT_FD}>&-"
      exec "$TIMEOUT_BIN" --kill-after=30s "$TIMEOUT" "$@" ) >"$log" 2>&1
  else
    ( [ -n "$GATE_SLOT_FD" ] && eval "exec ${GATE_SLOT_FD}>&-"
      exec "$@" ) >"$log" 2>&1
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
# State the concurrency conditions the run actually got, because every duration
# in this log is a claim about them. "Slow" and "contended" are different
# findings and the log has to be able to tell them apart AFTER the fact.
if [ -n "$GATE_SLOT_HELD" ]; then
  echo "  slot: $GATE_SLOT_HELD of $GATE_SLOTS (concurrency was bounded)"
else
  echo "  slot: NONE HELD — this run was not bounded by the limiter; its timing is not comparable to a slotted run"
fi

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
