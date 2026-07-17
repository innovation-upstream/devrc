#!/usr/bin/env bash
#
# tests-on-push.sh — SYNCHRONOUS, BLOCKING test gate for the global pre-push hook.
#
# Unlike audit-on-push.sh (backgrounded, never blocks), THIS worker runs the
# devrc Python test suite BEFORE the push completes and BLOCKS the push if any
# test fails. It is the dev-host tier of the test gate; the hermetic subset is
# ALSO enforced by `nix flake check` (flake.nix `checks.pytests`) / CI. Here we
# run the FULLER set (`--set all`) so any dev-host-only suites get exercised too.
#
# GLOBAL-hook safety: this worker is a no-op for every repo EXCEPT devrc. It
# self-detects devrc (scripts/run-tests.sh present AND flake.nix is the DEVRC
# flake) and exits 0 immediately otherwise, so installing the global hook never
# starts running pytest on unrelated repos.
#
# Escape hatch: `DEVRC_SKIP_TESTS=1 git push …` skips the gate (logged to stderr).
#
# Exit: 0 = passed or not-applicable (push proceeds); non-zero = tests failed
# (push is BLOCKED).

set -uo pipefail

REPO_ROOT="${1:-$(git rev-parse --show-toplevel 2>/dev/null || true)}"
[ -n "$REPO_ROOT" ] || exit 0

RUNNER="$REPO_ROOT/scripts/run-tests.sh"
FLAKE="$REPO_ROOT/flake.nix"

# --- Applicability gate: devrc only -----------------------------------------
[ -f "$RUNNER" ] || exit 0
[ -f "$FLAKE" ] && grep -q 'DEVRC' "$FLAKE" 2>/dev/null || exit 0

if [ "${DEVRC_SKIP_TESTS:-0}" = "1" ]; then
  echo "pre-push: DEVRC_SKIP_TESTS=1 set — skipping the test gate (flake check / CI still enforce it)." >&2
  exit 0
fi

echo "pre-push: running devrc test suite (blocking gate)…" >&2

run_suite() {
  bash "$RUNNER" --set all "$REPO_ROOT"
}

# Prefer an already-usable pytest; else provide deps via nix-shell (matches the
# per-README invocation). If neither is available, DEGRADE to a warning rather
# than wedge every push — the hermetic flake check / CI is the hard gate.
if python -c 'import pytest' >/dev/null 2>&1; then
  run_suite
  rc=$?
elif command -v nix-shell >/dev/null 2>&1; then
  nix-shell -p "python312.withPackages(ps: with ps; [pytest requests psycopg2 minio pyyaml])" \
    --run "bash '$RUNNER' --set all '$REPO_ROOT'"
  rc=$?
else
  echo "pre-push: WARNING — no pytest and no nix-shell available; cannot run the test gate locally." >&2
  echo "pre-push: push allowed to proceed; rely on 'nix flake check' / CI for the hermetic gate." >&2
  exit 0
fi

if [ "$rc" -ne 0 ]; then
  echo "" >&2
  echo "pre-push: ❌ devrc test suite FAILED (rc=$rc) — push BLOCKED." >&2
  echo "pre-push: fix the failing tests, or 'DEVRC_SKIP_TESTS=1 git push …' to override." >&2
  exit "$rc"
fi

echo "pre-push: ✅ devrc test suite passed." >&2
exit 0
