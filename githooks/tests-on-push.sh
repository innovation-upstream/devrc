#!/usr/bin/env bash
#
# tests-on-push.sh — SYNCHRONOUS test gate for the global pre-push hook.
#
# Unlike audit-on-push.sh (backgrounded, never blocks), THIS worker runs the
# devrc Python test suite BEFORE the push completes and — in enforce mode —
# BLOCKS the push if a test genuinely fails. It is the dev-host tier of the test
# gate; the hermetic subset is ALSO enforced by `nix flake check` (flake.nix
# `checks.pytests`) / CI. Here we run the FULLER set (`--set all`).
#
# It reads the pushed ref updates on STDIN (git's pre-push protocol:
#   <local_ref> <local_sha> <remote_ref> <remote_sha>  per line)
# so it can skip the gate for pushes that don't touch Python/tests/flake.
#
# DESIGN PRINCIPLES (fail in the SAFE direction):
#   * GLOBAL-hook safe — no-op for every repo except devrc (self-detected).
#   * Infra flakiness DEGRADES, never blocks — if the test ENV can't be prepared
#     (offline, uncached, substituter hiccup, disk full, no nix-shell) we WARN
#     loudly and allow the push. Only a genuine pytest failure (tests executed,
#     >=1 failed) blocks — and only in enforce mode.
#   * Changed-files filter fails TOWARD running — any ambiguity (new branch we
#     can't resolve, unparseable stdin, diff error) RUNS the suite.
#
# MODE (parallels the audit's flag) — TESTS_ON_PUSH, from env or the shared
# ~/.claude/audit-on-push.env (override the path with TESTS_ON_PUSH_CONF_FILE):
#   off             — skip the gate entirely.
#   shadow          — run the tests, REPORT the result, NEVER block (warn-only).
#   on / enforce    — run the tests, BLOCK the push on a genuine failure. DEFAULT.
#
# Escape hatch: `DEVRC_SKIP_TESTS=1 git push …` skips the gate regardless of mode.
#
# Exit: 0 = passed, skipped, degraded, or not-applicable (push proceeds);
#       non-zero = tests genuinely failed in enforce mode (push BLOCKED).

set -uo pipefail

REPO_ROOT="${1:-$(git rev-parse --show-toplevel 2>/dev/null || true)}"
[ -n "$REPO_ROOT" ] || exit 0

RUNNER="$REPO_ROOT/scripts/run-tests.sh"
FLAKE="$REPO_ROOT/flake.nix"

# --- Applicability gate: devrc only -----------------------------------------
[ -f "$RUNNER" ] || exit 0
{ [ -f "$FLAKE" ] && grep -q 'DEVRC' "$FLAKE" 2>/dev/null; } || exit 0

# --- Per-push escape hatch ---------------------------------------------------
if [ "${DEVRC_SKIP_TESTS:-0}" = "1" ]; then
  echo "pre-push: DEVRC_SKIP_TESTS=1 — skipping the test gate (flake check / CI still enforce it)." >&2
  exit 0
fi

# --- Mode (env overrides file; default enforce) ------------------------------
CONF="${TESTS_ON_PUSH_CONF_FILE:-$HOME/.claude/audit-on-push.env}"
if [ -z "${TESTS_ON_PUSH:-}" ] && [ -f "$CONF" ]; then
  # shellcheck disable=SC1090
  . "$CONF" 2>/dev/null || true
fi
MODE="${TESTS_ON_PUSH:-on}"
case "$MODE" in
  off)
    echo "pre-push: TESTS_ON_PUSH=off — test gate disabled." >&2
    exit 0 ;;
  shadow|on|enforce) : ;;
  *)
    echo "pre-push: unknown TESTS_ON_PUSH='$MODE' — treating as 'on'." >&2
    MODE=on ;;
esac

# --- Changed-files filter (fail TOWARD running) ------------------------------
# Read git's ref-update lines from stdin. RUN the suite iff any pushed commit
# touches Python/tests/flake. Any ambiguity -> RUN (return 0).
#   return 0 = RUN the gate ; return 1 = SKIP (no code touched)
CODE_RE='^(scripts/|flake\.nix$|flake\.lock$)'
is_all_zeros() { case "$1" in *[!0]*) return 1 ;; *) return 0 ;; esac; }

should_run_by_files() {
  local stdin_data="$1"
  [ -n "$stdin_data" ] || return 0            # no stdin -> can't tell -> RUN
  local saw_diff=0 matched=0
  local local_ref local_sha remote_ref remote_sha rest
  while IFS=' ' read -r local_ref local_sha remote_ref remote_sha rest; do
    [ -n "$local_ref" ] || continue           # blank line
    # malformed line (missing a field) -> RUN
    if [ -z "$local_sha" ] || [ -z "$remote_sha" ]; then
      return 0
    fi
    # pure delete (local sha all-zeros): no content pushed -> nothing to test
    if is_all_zeros "$local_sha"; then
      continue
    fi
    local range
    if is_all_zeros "$remote_sha"; then
      # new branch on the remote: diff against merge-base with origin/main.
      local base
      base="$(git -C "$REPO_ROOT" merge-base origin/main "$local_sha" 2>/dev/null || true)"
      if [ -n "$base" ]; then
        range="$base..$local_sha"
      else
        return 0                              # can't resolve a base -> RUN
      fi
    else
      range="$remote_sha..$local_sha"
    fi
    local files
    if ! files="$(git -C "$REPO_ROOT" diff --name-only "$range" 2>/dev/null)"; then
      return 0                                # diff failed -> RUN
    fi
    saw_diff=1
    if printf '%s\n' "$files" | grep -qE "$CODE_RE"; then
      matched=1
    fi
  done <<EOF
$stdin_data
EOF
  [ "$saw_diff" = 1 ] || return 0             # never computed a diff -> RUN
  [ "$matched" = 1 ] && return 0 || return 1
}

STDIN_DATA="$(cat 2>/dev/null || true)"
if ! should_run_by_files "$STDIN_DATA"; then
  echo "pre-push: no Python/test/flake changes in this push — skipping the test gate." >&2
  exit 0
fi

# --- Prepare the test env (DEGRADE, don't block, on failure) -----------------
# The env is PINNED (nix-shell); we never trust an ambient pytest — the modules
# under test import requests/psycopg2/minio/yaml at collection time, so a stray
# bare-pytest venv on PATH would ImportError and wrongly block the push.
PY_ENV="python312.withPackages(ps: with ps; [pytest requests psycopg2 minio pyyaml])"

degrade() {
  echo "" >&2
  echo "pre-push: ⚠ skipping test gate — could not prepare the test env: $1" >&2
  echo "pre-push: push ALLOWED to proceed; the hermetic gate is 'nix flake check' / CI." >&2
  exit 0
}

if ! command -v nix-shell >/dev/null 2>&1; then
  degrade "nix-shell not found on PATH"
fi

echo "pre-push: preparing test env (nix-shell)…" >&2
prep_out="$(nix-shell -p "$PY_ENV" --run 'python --version' 2>&1)"
prep_rc=$?
if [ "$prep_rc" -ne 0 ]; then
  degrade "nix-shell env build failed (rc=$prep_rc): $(printf '%s' "$prep_out" | tail -1)"
fi

# --- Run the suite (this env is now cached; a failure here is a REAL failure) -
echo "pre-push: running devrc test suite (mode=$MODE)…" >&2
nix-shell -p "$PY_ENV" --run "bash '$RUNNER' --set all '$REPO_ROOT'"
run_rc=$?

if [ "$run_rc" -eq 0 ]; then
  echo "pre-push: ✅ devrc test suite passed." >&2
  exit 0
fi

# Tests EXECUTED and at least one failed.
if [ "$MODE" = "shadow" ]; then
  echo "" >&2
  echo "pre-push: ⚠ devrc test suite FAILED (rc=$run_rc) — SHADOW mode, push NOT blocked." >&2
  echo "pre-push: flip TESTS_ON_PUSH=on in ~/.claude/audit-on-push.env to enforce." >&2
  exit 0
fi

echo "" >&2
echo "pre-push: ❌ devrc test suite FAILED (rc=$run_rc) — push BLOCKED." >&2
echo "pre-push: fix the failing tests, or 'DEVRC_SKIP_TESTS=1 git push …' to override." >&2
exit "$run_rc"
