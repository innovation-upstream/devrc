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
# so it can skip the gate for a push that carries no commits at all.
#
# DESIGN PRINCIPLES (fail in the SAFE direction):
#   * GLOBAL-hook safe — no-op for every repo except devrc (self-detected).
#   * Infra flakiness DEGRADES, never blocks — if the test ENV can't be prepared
#     (offline, uncached, substituter hiccup, disk full, no nix-shell) we WARN
#     loudly and allow the push. Only a genuine pytest failure (tests executed,
#     >=1 failed) blocks — and only in enforce mode.
#   * The push filter fails TOWARD running — any ambiguity RUNS the suite, and
#     since 2026-08-21 the ONLY thing it filters out is a ref delete. The
#     changed-files allowlist it replaced is gone; the measurement that removed
#     it is at the filter itself.
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

# --- Does this push carry content to test? (fail TOWARD running) -------------
# 🔴 THERE IS NO CHANGED-FILES ALLOWLIST HERE ANY MORE, AND THE MEASUREMENT
# THAT REMOVED IT. Until 2026-08-21 this ran the suite only when a pushed commit
# touched `^(scripts/|flake\.nix$|flake\.lock$)`. Two independent findings, and
# the second is why adding a prefix was not the fix:
#
#   * `nix/` was ABSENT, so a push touching only `nix/home.nix` — the file that
#     DECLARES every activation entry, and the exact subject of
#     scripts/tests-devhost/test_activation_order.py — printed "no
#     Python/test/flake changes … skipping the test gate" and exited 0. The one
#     guard that needs `--set all` was outside the filter that decides whether
#     `--set all` runs.
#   * An allowlist over THIS repo cannot be completed. Four gates —
#     scripts/tests/test_no_captured_text.py, test_no_captured_markup.py,
#     test_no_public_ips.py and test_no_client_hostnames.py — enumerate
#     `git ls-files` and scan EVERY tracked file, `.md` included; their pinned
#     allowlists name `claudedocs/*.md` entries, so a push touching only a
#     handoff doc can genuinely red the suite. MEASURED 2026-08-21: every
#     top-level tracked prefix in this repo (scripts, claude, claudedocs, nix,
#     .config, .serena, githooks, cmd, docs and the top-level dotfiles) is read
#     by at least one test. A prefix list over that tree is a list of the paths
#     someone happened to think of, and it fails SILENTLY — the skip message
#     reads like a decision rather than a hole.
#
# So the only thing still filtered is the one class that provably carries no
# content: a ref DELETE (`local_sha` all zeros — git pushes nothing, there is no
# tree). Everything else RUNS, and every ambiguity still RUNS.
#
# The cost is real and deliberate: a docs-only push now pays the suite. The
# escape hatch is per-push and already documented — `DEVRC_SKIP_TESTS=1 git push`.
#
#   return 0 = RUN the gate ; return 1 = SKIP (nothing pushed but deletes)
is_all_zeros() { case "$1" in *[!0]*) return 1 ;; *) return 0 ;; esac; }

should_run_by_refs() {
  local stdin_data="$1"
  [ -n "$stdin_data" ] || return 0            # no stdin -> can't tell -> RUN
  local saw_line=0 saw_update=0
  local local_ref local_sha remote_ref remote_sha rest
  while IFS=' ' read -r local_ref local_sha remote_ref remote_sha rest; do
    [ -n "$local_ref" ] || continue           # blank line
    saw_line=1
    # malformed line (missing a field) -> RUN
    if [ -z "$local_sha" ] || [ -z "$remote_sha" ]; then
      return 0
    fi
    # pure delete (local sha all-zeros): no content pushed -> nothing to test
    if is_all_zeros "$local_sha"; then
      continue
    fi
    saw_update=1
  done <<EOF
$stdin_data
EOF
  # 🔴 NOT PARSEABLE IS NOT THE SAME AS NOTHING TO DO. Stdin that reached the
  # loop but yielded no ref line at all (blank lines, a shape git does not use
  # today) must RUN — the SKIP below is only ever for lines we DID read and
  # positively identified as deletes.
  [ "$saw_line" = 1 ] || return 0
  [ "$saw_update" = 1 ] && return 0 || return 1
}

STDIN_DATA="$(cat 2>/dev/null || true)"
if should_run_by_refs "$STDIN_DATA"; then
  DECISION=RUN
else
  DECISION=SKIP
fi

# 🔴 A DRY RUN THAT STOPS BEFORE THE EXPENSIVE PART — this is what makes the
# decision above TESTABLE, and it is the only reason the filter is not once again
# pinned by prose alone. `DEVRC_TESTS_ON_PUSH_DECIDE_ONLY=1` prints the verdict
# and exits 0 without building a nix-shell or running a single test, so
# scripts/tests/test_tests_on_push_filter.py can drive the REAL script (not a
# copy of its regex) for both outcomes. It is also usable by hand:
#   DEVRC_TESTS_ON_PUSH_DECIDE_ONLY=1 githooks/tests-on-push.sh <repo> </dev/null
# It exits 0 in BOTH cases on purpose: the answer is on stdout, and a dry run
# must never be mistaken for a gate verdict.
if [ "${DEVRC_TESTS_ON_PUSH_DECIDE_ONLY:-0}" = "1" ]; then
  echo "DECISION: $DECISION"
  exit 0
fi

if [ "$DECISION" = SKIP ]; then
  echo "pre-push: this push carries no commits (ref deletes only) — skipping the test gate." >&2
  exit 0
fi

# --- Prepare the test env (DEGRADE, don't block, on failure) -----------------
# The env is PINNED (nix-shell); we never trust an ambient pytest — the modules
# under test import requests/psycopg2/minio/yaml at collection time, so a stray
# bare-pytest venv on PATH would ImportError and wrongly block the push.
PY_ENV="python312.withPackages(ps: with ps; [pytest requests psycopg2 minio pyyaml])"

# 🔴 Every OTHER entry in run-tests.sh's REQUIRED_TOOLS is taken from the ambient
# PATH — which works only for tools a dev host actually carries. `logrotate` is
# NOT one of them: nix/home.nix supplies it to the claude-log-rotate *unit* via
# `makeBinPath`, never to `home.packages`, so it is on NO interactive PATH on
# either host. MEASURED 2026-08-11 on the workbench: `command -v logrotate` finds
# nothing, and this gate therefore died at GUARD 1 with
#   run-tests: FATAL — required tool(s) missing from PATH: logrotate
# exit 2, ZERO tests run — a pre-push tier that had stopped being a test gate at
# all. The flake check already declares it (flake.nix checks.pytests
# nativeBuildInputs); this is the same declaration for the other tier.
#
# Add any future REQUIRED_TOOLS entry that is not a normal user-PATH binary here
# AND in flake.nix — the two tiers satisfy the same list by different means, and
# nothing cross-checks them (see test_logrotate_is_supplied_to_the_prepush_tier).
TOOL_ENV=(-p "$PY_ENV" -p logrotate)

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
prep_out="$(nix-shell "${TOOL_ENV[@]}" --run 'python --version' 2>&1)"
prep_rc=$?
if [ "$prep_rc" -ne 0 ]; then
  degrade "nix-shell env build failed (rc=$prep_rc): $(printf '%s' "$prep_out" | tail -1)"
fi

# --- Run the suite (this env is now cached; a failure here is a REAL failure) -
# 🔴 NOTHING may run between this command and `run_rc=$?`. Not an echo, not a
# `| tail`, not a `tee` — a trailing command's status replaces the runner's, and
# that is the exact mechanism that had four agents reporting `exit 0` over
# `RESULT: FAIL` on 2026-08-11. The runner also prints its own status in its
# verdict line now (`RESULT: FAIL (exit=1)`), so a reader who only has the
# output still gets the truth; see scripts/gate.sh.
echo "pre-push: running devrc test suite (mode=$MODE)…" >&2
nix-shell "${TOOL_ENV[@]}" --run "bash '$RUNNER' --set all '$REPO_ROOT'"
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
