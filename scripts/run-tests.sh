#!/usr/bin/env bash
#
# devrc test-suite runner — the single source of truth for "run the Python tests".
#
# Used by BOTH:
#   1. the flake check  (nix flake check / nix build .#checks.x86_64-linux.pytests)
#      — runs the HERMETIC set in the nix sandbox (no network, pinned python).
#   2. githooks/pre-push — runs the fuller set on the dev host before a push.
#
# The caller is responsible for putting a pytest-capable `python` on PATH (the
# flake check does this via the derivation's buildInputs; the pre-push hook wraps
# this in a nix-shell with the deps). This script only ORCHESTRATES: each test
# dir gets its own `python -m pytest` invocation because the suites rely on a
# per-directory sys.path (bare `import collector` / `import extract` etc. would
# collide if collected together under one rootdir).
#
# Usage:
#   scripts/run-tests.sh [--set hermetic|all] [ROOT]
#     --set hermetic  (default) — dirs safe to run offline in the nix sandbox.
#     --set all                 — hermetic + any dirs deferred to the dev host.
#   ROOT defaults to the git repo root (or the script's parent-parent).
#
# Exit non-zero if ANY selected suite fails. Prints a per-dir + total summary.

set -uo pipefail

SET="hermetic"
ROOT=""
while [ $# -gt 0 ]; do
  case "$1" in
    --set) SET="${2:-}"; shift 2 ;;
    --set=*) SET="${1#*=}"; shift ;;
    *) ROOT="$1"; shift ;;
  esac
done

if [ -z "$ROOT" ]; then
  ROOT="$(git -C "$(dirname "${BASH_SOURCE[0]}")" rev-parse --show-toplevel 2>/dev/null || true)"
  [ -n "$ROOT" ] || ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
fi

cd "$ROOT" || { echo "run-tests: cannot cd to ROOT=$ROOT" >&2; exit 2; }

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
  scripts/validation/tests
  scripts/session-analysis/tests
  scripts/session-analysis/session_insight/tests
  scripts/mail-actions/tests
  scripts/repo-cos/tests
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

fail=0
declare -a RESULTS
run_pytest() {
  local d="$1"
  echo "=== pytest $d ==="
  if python -m pytest "$d" -q -p no:cacheprovider --no-header; then
    RESULTS+=("PASS  $d")
  else
    RESULTS+=("FAIL  $d")
    fail=1
  fi
  echo
}

for d in "${DIRS[@]}"; do
  run_pytest "$d"
done

# The claude-hooks nudge test is a hand-rolled script (asserts + sys.exit, not
# pytest-collectable) — run it directly. Hermetic (pure string logic + a
# subprocess of the hook itself). Always part of the hermetic set.
HOOK_TEST="scripts/claude-hooks/tests/test_shell_env_nudge.py"
if [ -f "$HOOK_TEST" ]; then
  echo "=== script $HOOK_TEST ==="
  if python "$HOOK_TEST"; then
    RESULTS+=("PASS  $HOOK_TEST (script)")
  else
    RESULTS+=("FAIL  $HOOK_TEST (script)")
    fail=1
  fi
  echo
fi

echo "======================== SUMMARY ($SET set) ========================"
for r in "${RESULTS[@]}"; do echo "  $r"; done
if [ "$fail" -ne 0 ]; then
  echo "RESULT: FAIL"
else
  echo "RESULT: PASS"
fi
exit "$fail"
