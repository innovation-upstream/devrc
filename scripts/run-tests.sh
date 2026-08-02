#!/usr/bin/env bash
#
# devrc test-suite runner — the single source of truth for "run the Python tests".
#
# Used by BOTH:
#   1. the flake check  (nix flake check / nix build .#checks.x86_64-linux.pytests)
#      — runs the HERMETIC set in the nix sandbox (no network, pinned python).
#   2. githooks/pre-push — runs the fuller set on the dev host before a push.
#
# SCOPE: this script runs the PYTHON suites ONLY. The browser-bridge `.mjs` suite
# (scripts/browser-bridge/tests/*.test.mjs) is NOT run here — it has its own
# runner, scripts/run-node-tests.sh, gated by its own flake check
# (`nix build .#checks.x86_64-linux.nodetests`). `nix flake check` runs both.
# Note that `HERMETIC_DIRS` below includes scripts/browser-bridge/tests, but that
# entry collects only the `test_*.py` files in that directory — pytest does not
# see the `.mjs` files at all.
#
# The caller is responsible for putting a pytest-capable `python` on PATH (the
# flake check does this via the derivation's buildInputs; the pre-push hook wraps
# this in a nix-shell with the deps). This script only ORCHESTRATES: each test
# dir gets its own `python -m pytest` invocation because the suites rely on a
# per-directory sys.path (bare `import collector` / `import extract` etc. would
# collide if collected together under one rootdir).
#
# 🔴 FOUR STRUCTURAL GUARDS — each exists because a green exit code lies here.
#    They mirror scripts/run-node-tests.sh (which asserts a test-count floor and
#    parses node's TAP summary instead of reading an exit code). Read that file
#    too before changing this one.
#
#   1. TOOL PRECONDITION (`REQUIRED_TOOLS`). The suites carry ~55 `skipif`s keyed
#      on external binaries. If one goes missing from the environment the tests
#      that need it SILENTLY SKIP and the gate stays green while testing less.
#      This exact failure shipped: `curl` was absent from the flake check's
#      inputs, so 41 test_server.py tests skipped (fixed in #251 — but nothing
#      asserted the fix kept working, which is what this guard is for). We now
#      check each binary UP FRONT and abort naming it, so "41 tests silently
#      skipped" becomes "the gate says curl is missing".
#      ⚠ This binds the pre-push tier too, which supplies only python via
#      nix-shell and takes the rest from the ambient PATH (all 10 verified
#      present on the workbench 2026-08-02). A host missing one now BLOCKS the
#      push with the named tool instead of pushing a silently-thinner run;
#      `DEVRC_SKIP_TESTS=1 git push …` is the documented override.
#
#   2. PINNED EXPECTED-SKIP SET (`EXPECTED_SKIPS`). Not a numeric ceiling: every
#      skip must match a pinned (directory, reason-regex) entry, and the observed
#      skip TOTAL must equal the number of pinned entries. So a new skip fails,
#      a skip that moves to a different suite fails, one skip swapped for another
#      fails, and a pinned skip that stops firing ALSO fails (remove it from the
#      pin — that is the accounting). A bare ceiling would let a curl regression
#      hide behind an unrelated skip disappearing; the set is small enough (2)
#      that pinning costs nothing. Suites run with `-rs` so every skip's reason
#      is printed, not just counted.
#
#   3. COLLECTED-TEST FLOORS (`MIN_TESTS`, and >=1 per directory). We parse
#      pytest's summary line and count what actually ran rather than reading the
#      exit code — a collection error, an import breakage or an empty glob can
#      produce "0 tests" with a zero exit. A per-directory floor is checked too,
#      so one suite collapsing to nothing cannot be absorbed by the global total.
#
#   4. PARSE GUARD. If a suite's summary line cannot be parsed at all, the run
#      FAILS — an unparseable summary means this runner cannot vouch for the run,
#      which is not the same as a pass.
#
# Env overrides (defaults are the point — raise them, don't lower them casually):
#   MIN_TESTS  minimum tests the whole run must REPORT  (default 2850; 2920 today)
#
# Usage:
#   scripts/run-tests.sh [--set hermetic|all] [ROOT]
#     --set hermetic  (default) — dirs safe to run offline in the nix sandbox.
#     --set all                 — hermetic + any dirs deferred to the dev host.
#   ROOT defaults to the git repo root (or the script's parent-parent).
#
# Exit non-zero if ANY selected suite fails OR any guard above trips. Prints a
# per-dir + total summary (collected / passed / skipped / failed).

set -uo pipefail

SET="hermetic"
ROOT=""
while [ $# -gt 0 ]; do
  case "$1" in
    --set) SET="${2:-hermetic}"; shift; [ $# -gt 0 ] && shift ;;
    --set=*) SET="${1#*=}"; shift ;;
    *) ROOT="$1"; shift ;;
  esac
done

if [ -z "$ROOT" ]; then
  ROOT="$(git -C "$(dirname "${BASH_SOURCE[0]}")" rev-parse --show-toplevel 2>/dev/null || true)"
  [ -n "$ROOT" ] || ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
fi

cd "$ROOT" || { echo "run-tests: cannot cd to ROOT=$ROOT" >&2; exit 2; }

MIN_TESTS="${MIN_TESTS:-2850}"

# --- GUARD 1: tool precondition ------------------------------------------------
# Every binary the suites `skipif` on. Absence must be an ERROR, never a skip.
# Sources (grep `shutil.which` under scripts/):
#   curl    scripts/browser-bridge/tests/{test_server,test_browser_cli_args}.py (41+ tests)
#   bash    the `browser` CLI + drafter + ship-converge suites
#   node    scripts/initiatives/tests/{test_viewer,test_streaming}.py  (123 tests)
#   rg      scripts/repo-cos/tests/test_prescan.py
#   git     scripts/repo-cos/tests/test_prescan.py, scripts/tests/test_ship_converge.py
#   awk     scripts/browser-bridge/tests/test_browser_session_id.py
#   jq,grep scripts/task-spec-drafter/tests/test_severity_and_gate_skip.py
#   setsid  scripts/browser-bridge/tests/test_browser_agent.py (process-group kill)
#   python3 scripts/browser-bridge/tests/test_browser_agent.py
REQUIRED_TOOLS=(bash curl node rg git awk jq grep setsid python3)
missing_tools=()
for t in "${REQUIRED_TOOLS[@]}"; do
  command -v "$t" >/dev/null 2>&1 || missing_tools+=("$t")
done
if [ "${#missing_tools[@]}" -gt 0 ]; then
  echo "run-tests: FATAL — required tool(s) missing from PATH: ${missing_tools[*]}" >&2
  echo "  The suites SKIP the tests that need these, so the run would go green while" >&2
  echo "  testing less. Add them to the caller's inputs (flake.nix checks.pytests" >&2
  echo "  nativeBuildInputs / the pre-push nix-shell) — do NOT drop them from" >&2
  echo "  REQUIRED_TOOLS to make this pass." >&2
  exit 2
fi

# --- HERMETIC set --------------------------------------------------------------
# Verified to pass in the offline nix sandbox: every third-party call
# (psycopg2 / requests / minio HTTP) is mocked, so no live DB or network is
# reached. See flake.nix `checks.pytests` and the PR body for the audit.
HERMETIC_DIRS=(
  scripts/tests
  scripts/collector/tests
  scripts/collector/keylog/tests
  scripts/collector/claude/tests
  scripts/collector/i3/tests
  scripts/collector/browser-ext/tests
  scripts/browser-bridge/tests
  scripts/validation/tests
  scripts/session-analysis/tests
  scripts/session-analysis/session_insight/tests
  scripts/mail-actions/tests
  scripts/initiatives/tests
  scripts/repo-cos/tests
  scripts/task-spec-drafter/tests
)

# --- DEV-HOST-ONLY set ---------------------------------------------------------
# Dirs deferred to the pre-push tier (empty today — nothing here currently needs
# a live DB/network at runtime; kept so a future DB-bound suite has a home that
# does NOT block the hermetic flake gate).
DEVHOST_DIRS=()

DIRS=("${HERMETIC_DIRS[@]}")
if [ "$SET" = "all" ]; then
  DIRS+=("${DEVHOST_DIRS[@]}")
fi

# A writable, self-consistent HOME so the claude-hooks nudge cache-write path
# works in the sandbox (the hook derives HOME via expanduser; the test derives
# it the same way, so the value only has to be writable — not "real").
export HOME="${HOME:-/tmp}"
if [ ! -w "$HOME" ]; then
  export HOME="$(mktemp -d)"
fi

# --- GUARD 2: the pinned expected-skip set -------------------------------------
# One "<dir>|<reason-regex>" per LEGITIMATELY skipped test. Both halves must match
# a pytest `SKIPPED [n] <path>:<line>: <reason>` line, and the skip TOTAL must
# equal the number of entries. Adding an entry is a deliberate act of accounting
# — do it only when the skip is genuinely unavoidable, and say why.
#
# Built AFTER $HOME is settled: an entry may be conditional, but its condition
# must be the SAME predicate the test itself uses — never a blanket allowance,
# or the pin degrades into the numeric ceiling it exists to beat.
EXPECTED_SKIPS=(
  # Opt-in drift check against the LIVE homelab store — needs a kubeconfig and
  # network, neither of which a hermetic gate may have. Skips everywhere unless
  # REPO_COS_LIVE_DRIFT_CHECK=1.
  "scripts/repo-cos/tests|live-store drift check is opt-in"
)
# test_scrub.py::test_patterns_cover_bash_guard compares scrub.py's
# SECRET_PATTERNS against the DEPLOYED hook, and skips iff that file is absent —
# `_BASH_GUARD = Path.home() / ".claude" / "hooks" / "bash-guard.py"`, which is
# exactly the test below. It is ABSENT in the nix sandbox (synthetic $HOME) and
# PRESENT on a switched dev host, so the test genuinely runs in one and not the
# other. Pinning it unconditionally would fail the pre-push tier; pinning it
# conditionally still fails if it skips on a host where the hook exists.
if [ ! -e "$HOME/.claude/hooks/bash-guard.py" ]; then
  EXPECTED_SKIPS+=("scripts/session-analysis/session_insight/tests|bash-guard\.py absent")
fi

fail=0
declare -a RESULTS
declare -a SKIP_LINES
TOT_COLLECTED=0
TOT_PASSED=0
TOT_SKIPPED=0
TOT_FAILED=0

# Pull "<N> <word>" out of a pytest summary line; 0 when the word is absent.
_count_of() { # $1 = alternation regex, $2 = summary line
  local n
  n="$(printf '%s\n' "$2" | grep -oE "[0-9]+ ($1)([^a-z]|$)" | tail -1 | grep -oE '^[0-9]+')"
  printf '%s' "${n:-0}"
}

run_pytest() {
  local d="$1"
  echo "=== pytest $d ==="

  if [ ! -d "$d" ]; then
    echo "run-tests: FATAL — test directory does not exist: $d" >&2
    RESULTS+=("FAIL  $d (missing directory)")
    fail=1
    echo
    return
  fi

  local log rc
  log="$(mktemp)"
  python -m pytest "$d" -q -p no:cacheprovider --no-header -rs >"$log" 2>&1
  rc=$?
  cat "$log"

  # GUARD 4: parse pytest's summary line. `-q` emits it undecorated, e.g.
  #   "660 passed, 123 skipped in 15.10s"   /   "1 failed, 2 passed in 0.1s"
  #   "no tests ran in 0.01s"               /   "1 error in 0.05s"
  local summary p s f e x xp collected
  summary="$(grep -aE '^(=+ )?([0-9]+ (passed|failed|errors?|skipped|xfailed|xpassed|deselected)|no tests ran)' "$log" | tail -1)"
  if [ -z "$summary" ]; then
    echo "run-tests: ERROR — could not parse pytest's summary for $d." >&2
    echo "  This runner cannot vouch for that run; treating it as a FAILURE." >&2
    RESULTS+=("FAIL  $d (unparseable summary)")
    fail=1
    rm -f "$log"
    echo
    return
  fi

  p="$(_count_of 'passed' "$summary")"
  s="$(_count_of 'skipped' "$summary")"
  f="$(_count_of 'failed' "$summary")"
  e="$(_count_of 'errors?' "$summary")"
  x="$(_count_of 'xfailed' "$summary")"
  xp="$(_count_of 'xpassed' "$summary")"
  collected=$(( p + s + f + e + x + xp ))

  # Collect the `-rs` short-summary skip lines for GUARD 2.
  while IFS= read -r line; do
    [ -n "$line" ] && SKIP_LINES+=("$line")
  done < <(grep -aE '^SKIPPED \[[0-9]+\]' "$log")

  TOT_COLLECTED=$(( TOT_COLLECTED + collected ))
  TOT_PASSED=$(( TOT_PASSED + p ))
  TOT_SKIPPED=$(( TOT_SKIPPED + s ))
  TOT_FAILED=$(( TOT_FAILED + f + e ))

  # GUARD 3 (per-directory): a suite that collects nothing is a vacuous green.
  if [ "$collected" -lt 1 ]; then
    echo "run-tests: ERROR — $d collected 0 tests (summary: $summary)." >&2
    echo "  A collection error or an import breakage, not a pass." >&2
    RESULTS+=("FAIL  $d (collected 0 tests)")
    fail=1
  elif [ "$rc" -ne 0 ] || [ "$f" -gt 0 ] || [ "$e" -gt 0 ]; then
    RESULTS+=("FAIL  $d  (collected=$collected passed=$p skipped=$s failed=$f errors=$e)")
    fail=1
  else
    RESULTS+=("PASS  $d  (collected=$collected passed=$p skipped=$s)")
  fi

  rm -f "$log"
  echo
}

for d in "${DIRS[@]}"; do
  run_pytest "$d"
done

# The claude-hooks tests are hand-rolled scripts (asserts + sys.exit, not
# pytest-collectable) — run them directly. Hermetic (pure string logic + a
# subprocess of the hook itself). Always part of the hermetic set. They report
# no counts, so they are pass/fail only and are NOT part of the totals.
HOOK_TESTS=(
  "scripts/claude-hooks/tests/test_shell_env_nudge.py"
  "scripts/claude-hooks/tests/test_claude_notify.py"
  "scripts/claude-hooks/tests/test_register_nudge_hook.py"
  "scripts/claude-hooks/tests/test_bash_guard.py"
)
for HOOK_TEST in "${HOOK_TESTS[@]}"; do
  [ -f "$HOOK_TEST" ] || continue
  echo "=== script $HOOK_TEST ==="
  if python "$HOOK_TEST"; then
    RESULTS+=("PASS  $HOOK_TEST (script)")
  else
    RESULTS+=("FAIL  $HOOK_TEST (script)")
    fail=1
  fi
  echo
done

echo "======================== SUMMARY ($SET set) ========================"
for r in "${RESULTS[@]}"; do echo "  $r"; done
echo "  ----"
echo "  TOTAL collected=$TOT_COLLECTED  passed=$TOT_PASSED  skipped=$TOT_SKIPPED  failed=$TOT_FAILED  (floor: $MIN_TESTS)"

# --- GUARD 3 (global): collected-test floor ------------------------------------
if [ "$TOT_COLLECTED" -lt "$MIN_TESTS" ]; then
  echo "  ERROR: only $TOT_COLLECTED tests were collected, floor is $MIN_TESTS." >&2
  echo "         Far fewer tests ran than exist — a VACUOUS GREEN, not a pass." >&2
  echo "         Investigate before lowering MIN_TESTS." >&2
  fail=1
fi

# --- GUARD 2 (evaluation): pinned expected-skip set ----------------------------
echo "  ---- skips (pinned: ${#EXPECTED_SKIPS[@]}) ----"
if [ "${#SKIP_LINES[@]}" -eq 0 ]; then
  echo "    (none reported)"
else
  for line in "${SKIP_LINES[@]}"; do echo "    $line"; done
fi

unexpected=()
for line in "${SKIP_LINES[@]}"; do
  matched=0
  for entry in "${EXPECTED_SKIPS[@]}"; do
    edir="${entry%%|*}"
    ere="${entry#*|}"
    # "SKIPPED [n] <path>:<line>: <reason>" — require BOTH the owning directory
    # and the reason, so a skip that migrates to another suite is not absorbed.
    if printf '%s\n' "$line" | grep -qE "\] $edir/" && printf '%s\n' "$line" | grep -qE "$ere"; then
      matched=1
      break
    fi
  done
  [ "$matched" -eq 0 ] && unexpected+=("$line")
done

if [ "${#unexpected[@]}" -gt 0 ]; then
  echo "  ERROR: ${#unexpected[@]} UNPINNED skip group(s) — coverage silently collapsed:" >&2
  for line in "${unexpected[@]}"; do echo "         $line" >&2; done
  echo "         A skip is a test that did not run. Fix the environment (see" >&2
  echo "         REQUIRED_TOOLS) or, if the skip is genuinely unavoidable here," >&2
  echo "         add it to EXPECTED_SKIPS with a comment saying why." >&2
  fail=1
fi

if [ "$TOT_SKIPPED" -ne "${#EXPECTED_SKIPS[@]}" ]; then
  echo "  ERROR: $TOT_SKIPPED test(s) skipped, but ${#EXPECTED_SKIPS[@]} are pinned in EXPECTED_SKIPS." >&2
  if [ "$TOT_SKIPPED" -lt "${#EXPECTED_SKIPS[@]}" ]; then
    echo "         FEWER than pinned: a pinned skip now RUNS (good) — delete its" >&2
    echo "         EXPECTED_SKIPS entry so the pin keeps meaning something." >&2
  else
    echo "         MORE than pinned: tests stopped running. See the skip list above." >&2
  fi
  fail=1
fi

if [ "$fail" -ne 0 ]; then
  echo "RESULT: FAIL"
else
  echo "RESULT: PASS"
fi
exit "$fail"
