#!/usr/bin/env bash
#
# load_test_store.sh — a FLAKE REPRODUCER. Runs one or more pytest targets in a
# loop under synthetic CPU pressure (and optionally under pytest-xdist) and
# reports how often they go red.
#
# 🔴 THIS IS A SCRIPT, NOT A TEST — never run it as part of the gate.
#    It spawns (nproc/2) busy-loop CPU burners and takes minutes. It is safe
#    from `scripts/run-tests.sh` by construction: that runner hands each target
#    DIRECTORY to `python -m pytest`, and pytest's default `python_files` is
#    `test_*.py` / `*_test.py`, so a `.sh` file in a tests dir is never
#    collected. `scripts/tests/test_load_test_harness.py` pins that (the repo
#    must keep having no pytest config that widens `python_files`), and the
#    same convention already covers `scripts/tests/mutants-*.sh` and
#    `scripts/tests/test_*.sh` — shell scripts that live beside tests and are
#    invoked by hand, never collected.
#
# 🔴 WHY THIS FILE HAS A GUARD IN IT. Until 2026-08-25 the pytest nodeid was
#    split across a line continuation:
#
#        python -m pytest "$FLAKE_DIR/.../test_store.py" \
#          ::test_concurrent_writers_do_not_lose_rows \
#
#    so `::test_concurrent_writers_do_not_lose_rows` arrived as its OWN argv
#    element instead of a suffix on the file path. pytest answers that with
#    `ERROR: directory argument cannot contain :: selection parts` and
#    `no tests ran in 0.01s`, exit 4 — so EVERY run "failed" and the script
#    exited `failures == RUNS` no matter what the code under test did. It was
#    an instrument reporting a verdict about ITSELF.
#
#    Two things stop that coming back:
#      1. the targets live in an ARRAY and are expanded `"${RESOLVED[@]}"`, so
#         one target is one argv element and there is no continuation to split;
#      2. a PREFLIGHT `--collect-only` runs BEFORE any burner is spawned. If the
#         targets collect zero tests, this script prints `COULD NOT RUN` and
#         exits 91 — it never reports a failure COUNT it cannot vouch for.
#         Exit 91 can never collide with a failure count because RUNS > 90 is
#         rejected up front.
#
# Usage:
#   load_test_store.sh [RUNS] [FLAKE_DIR] [TARGET ...]
#
#     RUNS       how many times to run the targets (default 6, max 90).
#     FLAKE_DIR  the flake/repo to run inside (default: this script's own repo
#                root — the old default was a `/tmp` path that did not exist, so
#                the script could not be run without arg 2).
#     TARGET...  one or more pytest targets. A relative target is resolved
#                against FLAKE_DIR; an absolute one is used as-is. Default is
#                the dl-router concurrent-writers test, so existing muscle
#                memory (`load_test_store.sh 10`) still does what it did.
#
#   Env:
#     PER_RUN_TIMEOUT  seconds per run (default 120).
#     BURNERS          number of CPU burners (default nproc/2; 0 disables them,
#                      which is how you test xdist contention WITHOUT CPU load).
#     XDIST            pytest-xdist width, e.g. 4 (default 0 = no xdist).
#     DIST             xdist distribution mode when XDIST > 0 (default loadfile,
#                      matching scripts/run-tests.sh).
#     PYTEST_EXTRA     extra pytest args, space-separated.
#     LOG_DIR          where per-run logs go (default /tmp).
#
# Exit status:
#     0..RUNS   the number of runs that FAILED (0 = the target never went red).
#     91        COULD NOT RUN — the targets collect nothing, or a run collected
#               nothing. This is NOT "the tests failed"; read the log.
#     2         bad usage.
#
# Examples:
#   ./load_test_store.sh 10
#   ./load_test_store.sh 10 "$PWD" scripts/tests/test_git_repo_isolation.py::test_live_cotenants_sees_another_process_in_the_repo
#   XDIST=4 BURNERS=0 ./load_test_store.sh 10 "$PWD" scripts/tests/test_subsystem_store_api.py
#
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
DEFAULT_FLAKE_DIR=$(cd -- "$SCRIPT_DIR/../../.." && pwd)
DEFAULT_TARGET='scripts/dl-router/tests/test_store.py::test_concurrent_writers_do_not_lose_rows'

# 91 is the COULD-NOT-RUN status. It is only unambiguous while a failure count
# cannot reach it, so the run count is capped here rather than clamped later.
COULD_NOT_RUN=91
MAX_RUNS=90

RUNS=${1:-6}
FLAKE_DIR=${2:-$DEFAULT_FLAKE_DIR}
if [ "$#" -gt 2 ]; then
  shift 2
  TARGETS=("$@")
else
  TARGETS=("$DEFAULT_TARGET")
fi

case "$RUNS" in
  ''|*[!0-9]*) echo "usage: $(basename "$0") [RUNS] [FLAKE_DIR] [TARGET ...]" >&2
               echo "  RUNS must be a positive integer, got: $RUNS" >&2; exit 2 ;;
esac
if [ "$RUNS" -lt 1 ] || [ "$RUNS" -gt "$MAX_RUNS" ]; then
  echo "RUNS must be between 1 and $MAX_RUNS (got $RUNS) — $COULD_NOT_RUN is reserved" >&2
  exit 2
fi
if [ ! -e "$FLAKE_DIR/flake.nix" ]; then
  echo "COULD NOT RUN: no flake.nix in FLAKE_DIR=$FLAKE_DIR" >&2
  exit "$COULD_NOT_RUN"
fi

# 🔴 ONE TARGET = ONE ARGV ELEMENT. Never build a nodeid across a line
# continuation, and never interpolate this array into a single string — that is
# exactly the defect this file exists to have fixed.
RESOLVED=()
for t in "${TARGETS[@]}"; do
  case "$t" in
    /*) RESOLVED+=("$t") ;;
    *)  RESOLVED+=("$FLAKE_DIR/$t") ;;
  esac
done

PER_RUN_TIMEOUT=${PER_RUN_TIMEOUT:-120}
CORES=$(nproc 2>/dev/null || echo 4)
NUM_BURNERS=${BURNERS:-$(( CORES / 2 ))}
XDIST=${XDIST:-0}
DIST=${DIST:-loadfile}
LOG_DIR=${LOG_DIR:-/tmp}
mkdir -p "$LOG_DIR"

PYTEST_OPTS=(-q --tb=short)
if [ "$XDIST" -gt 0 ]; then
  PYTEST_OPTS+=(-n "$XDIST" --dist "$DIST")
fi
# Word-splitting is WANTED here: PYTEST_EXTRA is a space-separated arg list.
# shellcheck disable=SC2206
EXTRA=(${PYTEST_EXTRA:-})

Burners=()
cleanup() {
  # Reap by RESOLVED PID only — never a `pkill -f` pattern, which would match
  # this script's own command line and sibling agents' processes.
  for pid in ${Burners[@]+"${Burners[@]}"}; do
    kill "$pid" 2>/dev/null || true
  done
  wait 2>/dev/null || true
}
trap cleanup EXIT

echo "flake-dir=$FLAKE_DIR"
echo "targets=${#RESOLVED[@]}"
for t in "${RESOLVED[@]}"; do echo "  target: $t"; done
echo "cores=$CORES burners=$NUM_BURNERS runs=$RUNS xdist=$XDIST timeout=${PER_RUN_TIMEOUT}s"

# --- PREFLIGHT: prove the targets COLLECT before burning a single cycle -------
# A harness that cannot reach its subject must say so, not report a count.
preflight="$LOG_DIR/load-test-$$.preflight.log"
set +e
timeout "$PER_RUN_TIMEOUT" nix develop "$FLAKE_DIR" --command \
  python -m pytest "${RESOLVED[@]}" --collect-only -q >"$preflight" 2>&1
preflight_rc=$?
set -e
collected=$(grep -Eo '^[0-9]+(/[0-9]+)? tests? collected' "$preflight" 2>/dev/null \
            | tail -1 | grep -Eo '^[0-9]+' || true)
if [ "$preflight_rc" -ne 0 ] || [ -z "$collected" ] || [ "$collected" -eq 0 ]; then
  echo "---"
  echo "COULD NOT RUN: the targets collect no tests (pytest --collect-only rc=$preflight_rc, collected=${collected:-none})"
  echo "  this is NOT a test failure — the harness could not reach its subject."
  echo "  see $preflight"
  # 🔴 The DIAGNOSTIC must never be able to change the VERDICT. Measured while
  # building this: a missing LOG_DIR made `sed` exit 2, `set -e` killed the
  # script mid-report, and the run exited 2 while printing COULD NOT RUN — the
  # same class of self-referential lie this file was written to remove.
  sed -n '1,20p' "$preflight" 2>/dev/null | sed 's/^/  | /' || true
  exit "$COULD_NOT_RUN"
fi
echo "preflight: $collected test(s) collected — the harness can reach its subject"

for (( i=0; i<NUM_BURNERS; i++ )); do
  ( while :; do :; done ) &>/dev/null &
  Burners+=($!)
done
echo "spawned ${#Burners[@]} burners, load: $(uptime | sed 's/.*load average: //')"

failures=0
for (( run=1; run<=RUNS; run++ )); do
  log="$LOG_DIR/load-test-$$.run$run.log"
  set +e
  timeout "$PER_RUN_TIMEOUT" nix develop "$FLAKE_DIR" --command \
    python -m pytest "${RESOLVED[@]}" "${PYTEST_OPTS[@]}" ${EXTRA[@]+"${EXTRA[@]}"} \
    >"$log" 2>&1
  run_rc=$?
  set -e
  # `|| true`: `set -o pipefail` is on, and grep exits 1 on an EMPTY log — which
  # would kill the script here, mid-measurement, over a cosmetic line.
  summary=$(grep -Ev '^[[:space:]]*$' "$log" | tail -1 || true)
  # A run that collected nothing is the ORIGINAL defect wearing a different
  # hat: it must not be scored as a failure of the code under test.
  if grep -qE '^no tests (ran|collected) in ' "$log"; then
    echo "  run $run: COULD NOT RUN — collected nothing (rc=$run_rc) — $log"
    echo "---"
    echo "COULD NOT RUN after $((run-1)) clean run(s); see $log"
    exit "$COULD_NOT_RUN"
  fi
  if [ "$run_rc" -eq 0 ]; then
    echo "  run $run: PASS  | $summary"
  else
    echo "  run $run: FAIL (rc=$run_rc, see $log) | $summary"
    failures=$((failures+1))
  fi
done

echo "---"
echo "failures: $failures / $RUNS"
echo "final load: $(uptime | sed 's/.*load average: //')"
exit "$failures"
