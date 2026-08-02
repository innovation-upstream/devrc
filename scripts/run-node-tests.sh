#!/usr/bin/env bash
#
# devrc NODE test-suite runner — the single source of truth for "run the .mjs tests".
#
# Used by BOTH:
#   1. the flake check  (nix flake check / nix build .#checks.x86_64-linux.nodetests)
#      — runs in the nix sandbox (no network, nodejs pinned by flake.nix).
#   2. a dev-host invocation: `bash scripts/run-node-tests.sh` from the repo root.
#
# The caller is responsible for putting `node` on PATH (the flake check does this
# via the derivation's nativeBuildInputs). This script only ORCHESTRATES.
#
# The suite under test is scripts/browser-bridge/tests/*.test.mjs. It is HERMETIC:
# audited 2026-08-02 — no `createServer`/`.listen()`, no `spawn`/`execFile`, every
# `fetch` is a stub assigned onto `globalThis.fetch`, and the only I/O is reads of
# repo-relative files (extension/protocol.js, browser-agent, server.py) plus writes
# under `tmpdir()`. Nothing reaches the network or a subprocess.
#
# 🔴 TWO STRUCTURAL GUARDS — both exist because a green exit code lies here:
#
#   1. NEVER PASS A DIRECTORY. `node --test scripts/browser-bridge/tests/` silently
#      collapses to `# tests 1 / # fail 1` (MODULE_NOT_FOUND) — a FALSE RED that a
#      future edit could just as easily turn into a false green. This script globs
#      the files itself into an array and refuses to run if any element is not a
#      regular file ending in `.test.mjs`, so it CANNOT degrade to the dir form.
#
#   2. COUNT THE TESTS, don't read the exit code. A collection failure, a bad glob,
#      or a toolchain breakage can produce "0 tests, exit 0". We parse node's TAP
#      summary and fail unless the run reports at least MIN_TESTS. Without this the
#      gate can go green while testing nothing — which is exactly how the pytest
#      gate silently skipped 41 test_server.py tests for want of `curl` on PATH.
#
# Env overrides (defaults are the point — raise them, don't lower them casually):
#   MIN_TESTS  minimum tests the run must REPORT   (default 450; 460 pass today)
#   MIN_FILES  minimum .test.mjs files to collect  (default 14; 14 exist today)
#
# Exit non-zero if the suite fails OR either floor is not met.

set -uo pipefail

ROOT=""
[ $# -gt 0 ] && ROOT="$1"
if [ -z "$ROOT" ]; then
  ROOT="$(git -C "$(dirname "${BASH_SOURCE[0]}")" rev-parse --show-toplevel 2>/dev/null || true)"
  [ -n "$ROOT" ] || ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
fi
cd "$ROOT" || { echo "run-node-tests: cannot cd to ROOT=$ROOT" >&2; exit 2; }

MIN_TESTS="${MIN_TESTS:-450}"
MIN_FILES="${MIN_FILES:-14}"

command -v node >/dev/null 2>&1 || {
  echo "run-node-tests: FATAL — no \`node\` on PATH (the caller must provide one)" >&2
  exit 2
}
echo "run-node-tests: node $(node --version)"

# --- collect (guard 1) ---------------------------------------------------------
shopt -s nullglob
FILES=(scripts/browser-bridge/tests/*.test.mjs)
shopt -u nullglob

if [ "${#FILES[@]}" -lt "$MIN_FILES" ]; then
  echo "run-node-tests: FATAL — collected ${#FILES[@]} test file(s), expected >= $MIN_FILES." >&2
  echo "  The glob matched nothing or almost nothing. Do NOT 'fix' this by passing the" >&2
  echo "  tests DIRECTORY to \`node --test\` — that yields a bogus 1-test failure." >&2
  exit 2
fi
for f in "${FILES[@]}"; do
  case "$f" in
    *.test.mjs) ;;
    *) echo "run-node-tests: FATAL — refusing non-test argument: $f" >&2; exit 2 ;;
  esac
  [ -f "$f" ] || { echo "run-node-tests: FATAL — not a regular file: $f" >&2; exit 2; }
done
echo "run-node-tests: collected ${#FILES[@]} test files"

# --- run -----------------------------------------------------------------------
LOG="$(mktemp)"
trap 'rm -f "$LOG"' EXIT
node --test --test-reporter=tap "${FILES[@]}" >"$LOG" 2>&1
rc=$?
cat "$LOG"

# --- count (guard 2) -----------------------------------------------------------
# node's TAP summary lines: "# tests N", "# pass N", "# fail N". Take the LAST of
# each (one summary per run) and default to a sentinel that fails the check if the
# line is absent entirely.
sum() { grep -E "^# $1 [0-9]+$" "$LOG" | tail -1 | awk '{print $3}'; }
T="$(sum tests)"; P="$(sum pass)"; F="$(sum fail)"; S="$(sum skipped)"; TD="$(sum todo)"
: "${T:=-1}"; : "${P:=-1}"; : "${F:=-1}"; : "${S:=0}"; : "${TD:=0}"

echo "======================== NODE SUMMARY ========================"
echo "  files=${#FILES[@]}  tests=$T  pass=$P  fail=$F  skipped=$S  todo=$TD  (floor: $MIN_TESTS)"

status=0
if [ "$T" -lt 0 ] || [ "$P" -lt 0 ] || [ "$F" -lt 0 ]; then
  echo "  ERROR: could not parse node's TAP summary — the runner cannot vouch for this run." >&2
  status=1
fi
if [ "$T" -lt "$MIN_TESTS" ]; then
  echo "  ERROR: only $T tests ran, floor is $MIN_TESTS. Something collected far fewer" >&2
  echo "         tests than exist — a VACUOUS GREEN, not a pass. Investigate before" >&2
  echo "         lowering MIN_TESTS." >&2
  status=1
fi
if [ "$F" -gt 0 ]; then
  echo "  ERROR: $F test(s) failed." >&2
  status=1
fi
if [ "$rc" -ne 0 ] && [ "$status" -eq 0 ]; then
  echo "  ERROR: node exited $rc with no failing test — a crash or unhandled rejection." >&2
  status=1
fi

if [ "$status" -ne 0 ]; then
  echo "RESULT: FAIL"
else
  echo "RESULT: PASS"
fi
exit "$status"
