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
#     (offline, uncached, substituter hiccup, no nix) we WARN loudly and allow
#     the push. 🔴 TWO DISJOINT mechanisms, deliberately not conflated: THIS
#     file's `degrade()` handles the env-can't-be-built case BEFORE the runner
#     is invoked at all; the runner's own exit 3 covers its environment
#     preconditions (GUARDs 1b/1c, a failed `cd $ROOT`, the spool `mkdir`, and
#     GUARD 1 ONLY when run outside a sanctioned gate env).
#     Saying "the runner signals this with exit 3" sent people looking for a
#     runner message that was never printed.
#     🔴 GUARD 1 is no longer purely environmental: its input REQUIRED_TOOLS is
#     REPO CONTENT, so since devrc#705 it exits 2 (BLOCK) when DEVRC_GATE_ENV=1
#     — i.e. the env already supplies everything `gateTools` declares and the
#     tool is still missing, which means the repo asked for something nothing
#     supplies. It is the one guard whose code depends on the CAUSE.
#   * 🔴 REPO-CONTENT guards BLOCK even though zero tests ran. run-tests.sh exits
#     2 when its target list, floor table, launcher stubs or spool wiring are
#     wrong — defects in the REPO, whose own messages warn that silencing them is
#     "how a suite stops running while the gate goes green". This header used to
#     say "only a genuine pytest failure blocks"; that was rewritten on
#     2026-08-22 rather than left to be cited as authority for degrading them.
#     So: exit 3 degrades, exit 2 blocks, a real test failure (exit 1) blocks.
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
# The env is PINNED to the repo's own devShell; we never trust an ambient pytest
# — the modules under test import requests/psycopg2/minio/yaml at COLLECTION
# time, so a stray bare-pytest venv on PATH would ImportError and wrongly block
# the push. `flake.nix devShells.default` carries all five plus logrotate, and
# is the same `gatePyEnv` the flake's `checks.pytests` uses, so both tiers run
# the identical interpreter.
#
# 🔴 KEPT FROM THE PREVIOUS `nix-shell -p …` FORM, because the lesson outlives
# the mechanism: every OTHER entry in run-tests.sh's REQUIRED_TOOLS is taken from
# the ambient PATH, which works only for tools a dev host actually carries.
# `logrotate` is NOT one of them — nix/home.nix supplies it to the
# claude-log-rotate *unit* via `makeBinPath`, never to `home.packages`, so it is
# on NO interactive PATH on either host. MEASURED 2026-08-11 on the workbench:
# `command -v logrotate` found nothing and this gate died at GUARD 1 with
#   run-tests: FATAL — required tool(s) missing from PATH: logrotate
# exit 2, ZERO tests run — a pre-push tier that had stopped being a test gate at
# all. The devShell now satisfies it, and `test_logrotate_is_on_path`
# (scripts/tests/test_claude_log_rotate.py) fails the suite if it ever stops
# being on PATH — deliberately NOT a skipif.
#
# So: a new REQUIRED_TOOLS entry that is not a normal user-PATH binary must be
# added to the devShell AND to flake.nix's checks — the two tiers satisfy the
# same list by different means, and nothing cross-checks them.

degrade() {
  echo "" >&2
  echo "pre-push: ⚠ skipping test gate — could not prepare the test env: $1" >&2
  echo "pre-push: push ALLOWED to proceed; the hermetic gate is 'nix flake check' / CI." >&2
  exit 0
}

# 🔴 `--no-write-lock-file`: `nix develop <repo>` MUTATES a tracked file — it
# writes flake.lock when an input is unlocked. A pre-push hook that dirties the
# working tree is a side effect nobody expects, and the changed-files filter
# fires this gate PRECISELY when flake.nix changed. With the flag, nix errors on
# a stale lock instead, which degrades — the honest outcome for this tier.
#
# 🔴 `env -u PYTHONPATH -u PYTHONHOME`: `nix develop` PRESERVES them, and
# python honours PYTHONPATH ahead of the env's own site-packages. The nix-sandbox
# tier has no such variable, so without this the two tiers are not running the
# identical interpreter environment even though both use the same derivation.
# 🔴 `nix develop`, NOT `nix-shell -p …`. MEASURED 2026-08-22, and the TRIGGER is
# the CWD, not an inherited variable: `nix-shell --run` executes the user's shell
# hooks, which activate a venv belonging to whatever directory you are standing
# in; `nix develop --command` does not. Setting VIRTUAL_ENV by hand from a
# neutral cwd reproduces NOTHING — both forms then give the store python — which
# is why this is worth stating precisely. From a cwd that owns a venv:
#     nix-shell -p "python312.withPackages(…)" --run python -> …/other-repo/.venv/bin/python
#     nix develop <repo> --command python                   -> /nix/store/…-env/bin/python
# That is the whole mechanism behind #698, where an unrelated repo's venv
# produced 13 failures that read as a broken branch. devShells.default exists so
# contributors stop hand-rolling the dep list, carries logrotate and all five
# suite deps, and its own greeting points at this runner. Using it REMOVES the
# defect rather than detecting it; run-tests.sh's GUARD 1c is the backstop for
# every other caller.
if ! command -v nix >/dev/null 2>&1; then
  degrade "nix not found on PATH"
fi

echo "pre-push: preparing test env (nix develop)…" >&2
prep_out="$(env -u PYTHONPATH -u PYTHONHOME nix develop --no-write-lock-file "$REPO_ROOT" --command python --version 2>&1)"
prep_rc=$?
if [ "$prep_rc" -ne 0 ]; then
  degrade "nix develop env build failed (rc=$prep_rc): $(printf '%s' "$prep_out" | tail -1)"
fi

# --- Run the suite (this env is now cached; a failure here is a REAL failure) -
# 🔴 NOTHING may run between this command and `run_rc=$?`. Not an echo, not a
# `| tail`, not a `tee` — a trailing command's status replaces the runner's, and
# that is the exact mechanism that had four agents reporting `exit 0` over
# `RESULT: FAIL` on 2026-08-11. The runner also prints its own status in its
# verdict line now (`RESULT: FAIL (exit=1)`), so a reader who only has the
# output still gets the truth; see scripts/gate.sh.
echo "pre-push: running devrc test suite (mode=$MODE)…" >&2
env -u PYTHONPATH -u PYTHONHOME nix develop --no-write-lock-file "$REPO_ROOT" --command bash "$RUNNER" --set all "$REPO_ROOT"
run_rc=$?

if [ "$run_rc" -eq 0 ]; then
  echo "pre-push: ✅ devrc test suite passed." >&2
  exit 0
fi

# 🔴 rc 3 is an ENVIRONMENT precondition abort (run-tests.sh GUARDs 1b/1c, a
# failed `cd $ROOT`, the spool `mkdir`, and GUARD 1 when run outside a gate env):
# by construction ZERO tests ran, and the fault is in the CALLER, not the repo.
# This file's header promises "Infra flakiness DEGRADES, never blocks", so it
# degrades.
#
# 🔴 rc 2 STILL BLOCKS, and the distinction is the whole point. A first version of
# this degraded on rc 2 — but run-tests.sh has ELEVEN abort sites, six `exit 3`
# and five `exit 2`; the exit-2 ones are REPO-CONTENT guards (target list, floor
# table, launcher stubs, spool wiring) whose own messages warn "do NOT delete the
# entry to make this pass — that is how a suite stops running while the gate goes
# green". Degrading on 2 produced exactly that, on the only tier that BLOCKS a
# push. The runner exits 3 for the environment cases so the two can be told apart.
#
# 🔴 GUARD 1 appears in BOTH lists — since devrc#705 its code depends on the CAUSE
# (DEVRC_GATE_ENV=1 -> repo defect -> 2; unset -> caller defect -> 3), because its
# input REQUIRED_TOOLS is repo content while its usual failure is environmental.
# So "which guard fired" no longer determines the code; do not re-derive the
# mapping from the guard number alone.
#
# 🔴 A Tekton PR gate (`tekton/devrc-pytests`, `tekton/devrc-nodetests`) DOES now
# run on PRs — the older "no CI" claim here was true when written and is not now.
# It runs `nix build .#checks.x86_64-linux.<leg>` and does NOT enter the
# devShell, so it is armed by checks.pytests's OWN DEVRC_GATE_ENV export — not
# by the shellHook this hook relies on. The two exports are not redundant.
# (The nodetests leg carries no marker and needs none: no DEVRC_GATE_ENV, no
# exit 3 in its runner.)
# Its red is advisory — `main`'s protection requires a review but NOT status
# checks (`required_status_checks` -> 404, measured 2026-08-22) — which is why
# this hook is still the only tier that blocks A PUSH. (Precisely: `main` also
# requires 1 approving review, which blocks a MERGE — so "the only tier that
# blocks" unqualified is wrong. The push is what this hook governs.)
#
# Verified in the other direction too: `fail` is only ever assigned 0 or 1 and the
# script ends `exit "$fail"`, so a genuine pytest failure can never surface as 2
# or 3 and be degraded away.
if [ "$run_rc" -eq 3 ]; then
  degrade "the ENVIRONMENT could not satisfy a precondition (rc=3) — see the message above"
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
