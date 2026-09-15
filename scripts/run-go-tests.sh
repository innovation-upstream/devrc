#!/usr/bin/env bash
#
# devrc GO test-suite runner — the single source of truth for "run the Go tests".
#
# 🔴 A THIRD TIER, AND IT IS A REAL MIGRATION COST RATHER THAN A FILE.
# This repo's gate had TWO tiers (`pytests`, `nodetests`) and Tekton builds them
# as `LEG ∈ {pytests, nodetests}`. Go is a third: it touches `flake.nix` checks,
# `gate.sh`, and anything that enumerates legs. The proposal that asked for this
# flagged it as easy to under-budget; this file exists so it is not.
#
# Used by BOTH:
#   1. the flake check  (`nix build .#checks.x86_64-linux.gotests`) — the nix
#      sandbox, no network, `go` pinned by flake.nix.
#   2. a dev-host invocation: `bash scripts/run-go-tests.sh` from the repo root,
#      or through `scripts/gate.sh --tier go`.
#
# 🔴 WHY THE SAME GUARD SHAPE AS THE OTHER TWO RUNNERS, RATHER THAN A BARE
# `go test ./...`. Every one of these exists because a green exit code lied:
#
#   1. COUNT THE TESTS, DON'T READ THE EXIT CODE. `go test` over a package with
#      no test files prints `[no test files]` and exits 0. A build tag typo, a
#      `_test.go` file that stopped compiling into the right package, or a
#      module path change can therefore take this tier fully green while running
#      NOTHING. The run parses `go test -json` and fails below a floor.
#
#   2. A TWO-WAY PACKAGE PIN. Discovery alone makes a whole package going silent
#      invisible: delete `internal/udiff/udiff_test.go` and a bare `./...` just
#      collects fewer tests and — above a global floor — still says PASS. So
#      every discovered package with tests must appear in `PACKAGES`, and every
#      `PACKAGES` entry must be discovered. It fails BOTH ways.
#
#   3. PER-PACKAGE FLOORS, not just a global one. One package collapsing to
#      near-zero must not be absorbed by the others' totals.
#
#   4. SKIPS ARE REPORTED AND CAPPED. A test that skips itself is worse than no
#      test. `internal/ui`'s palette guard skips when there is no repo checkout
#      above the module — legitimate inside the Go-module-only `buildGoModule`
#      sandbox, and NOT legitimate here, where this runner always runs from a
#      checkout. A skip budget of zero is what makes that difference visible.
#
#   5. THE VERDICT LINE CARRIES THE EXIT STATUS (`RESULT: FAIL (exit=1)`), from
#      one writer behind an EXIT trap. Every consumer pipes this output and a
#      pipeline reports the LAST command's status, so the truth has to be in the
#      content.
#
#   6. IT STATES ITS SCOPE. `gate.sh` requires to SEE `SCOPE: FULL` from every
#      tier before it may print a gate PASS — a positive control, so a runner
#      that says nothing is "cannot vouch" rather than "ran everything".
#
# Env overrides (defaults are the point — raise them, don't lower them casually):
#   MIN_GO_TESTS   one-off override of the GLOBAL floor, otherwise DERIVED as
#                  the sum of the per-package floors.
#   MAX_GO_SKIPS   one-off override of the skip budget (default 0).
#
# Usage:
#   scripts/run-go-tests.sh [--check-packages] [ROOT]
#     --check-packages  run GUARD 2 only (discovery + the two-way pin) and exit.
#                       No `go`, no tests — cheap enough for a unit test, and it
#                       reports `SCOPE: NONE`, never FULL.
#
# Exit: 0 all packages passed and every guard held
#       1 a test failed
#       2 a usage or environment error
#       3 `go` is not on PATH (a MISSING TOOLCHAIN, never a pass)
#       4 a guard tripped (floor, package pin, or skip budget)

set -uo pipefail

# --- GUARD 5: the verdict line CARRIES the exit status -------------------------
VERDICT_EMITTED=0
_emit_verdict() {
  local rc="$1"
  [ "$VERDICT_EMITTED" -eq 0 ] || return 0
  VERDICT_EMITTED=1
  _emit_scope
  if [ "$rc" -eq 0 ]; then
    echo "RESULT: PASS (exit=0)"
  else
    echo "RESULT: FAIL (exit=$rc)"
  fi
}

# --- GUARD 6: this runner states its scope too ---------------------------------
# 🔴 THE PRE-RESOLUTION DEFAULT IS UNKNOWN, NOT FULL. Before the scope is
# resolved the honest answer is UNKNOWN; an early exit that inherited a `FULL`
# would emit a whole-suite coverage claim from a run that collected nothing.
SCOPE_STATE="UNKNOWN"
SCOPE_DETAIL="the scope has not been resolved yet"
SCOPE_EMITTED=0
_emit_scope() {
  [ "$SCOPE_EMITTED" -eq 0 ] || return 0
  SCOPE_EMITTED=1
  echo "SCOPE: ${SCOPE_STATE} (${SCOPE_DETAIL})"
}
_on_exit() { _emit_verdict "$?"; }
trap '_on_exit' EXIT
trap 'exit 143' TERM
trap 'exit 130' INT

CHECK_PACKAGES_ONLY=0
ROOT=""
while [ $# -gt 0 ]; do
  case "$1" in
    --check-packages) CHECK_PACKAGES_ONLY=1; shift ;;
    -h|--help) sed -n '1,70p' "${BASH_SOURCE[0]}"; SCOPE_STATE="NONE"
               SCOPE_DETAIL="--help printed, no tests run"; exit 0 ;;
    -*) echo "run-go-tests: unknown flag $1" >&2
        SCOPE_STATE="NONE"; SCOPE_DETAIL="an unrecognised flag, no tests run"
        exit 2 ;;
    *) ROOT="$1"; shift ;;
  esac
done

# --- GUARD: NO TEST MAY OPERATE ON THE REPO THE SUITE RUNS FROM ----------------
# 🔴 BEFORE the ROOT block below, not after it: with GIT_DIR set and no
# GIT_WORK_TREE, git takes the CWD as the work tree and `rev-parse
# --show-toplevel` returns the wrong directory. The set is owned by
# `scripts/testlib/gitenv.py::REPO_POINTER_VARS` and the reason it is spelled
# per-runner rather than sourced is in `scripts/run-tests.sh`'s copy of this
# header.
DEVRC_GIT_REPO_POINTERS=(
  GIT_DIR GIT_WORK_TREE GIT_COMMON_DIR GIT_INDEX_FILE
  GIT_OBJECT_DIRECTORY GIT_ALTERNATE_OBJECT_DIRECTORIES
  GIT_NAMESPACE GIT_PREFIX GIT_GRAFT_FILE GIT_SHALLOW_FILE GIT_CONFIG
)
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
cd "$ROOT" || { echo "run-go-tests: cannot cd to ROOT=$ROOT" >&2; exit 2; }

# --- the pinned module + package table -----------------------------------------
# "<module dir>|<package import suffix>|<min_tests>".
#
# MEASURED 2026-09-14 on the dev host by this runner itself, counting `pass` +
# `fail` + `skip` records from `go test -json` (so SUBTESTS count, which is why
# `internal/argv` reports 28 rather than its 8 top-level funcs):
#
#   cmd/mention-review       5   the argv contract against the BINARY
#   internal/argv           28   the ported argv table, parametrised
#   internal/ghapi          19   auth classification + GraphQL/REST decoding
#   internal/udiff          12   diff parsing + hunk navigation
#   internal/ui             53   Step, the ledgers, the words, one end-to-end
#
# RE-MEASURED 2026-09-15 by this runner, after Phase 2 added the write actions
# (`internal/cfg` is new, and it is the merge-method resolver):
#
#   cmd/mention-review       5   unchanged
#   internal/argv           29
#   internal/cfg            12   the ABSENT-vs-UNREADABLE split, both directions
#   internal/ghapi          38   + the three REST writes and the loopback guard
#   internal/udiff          12   unchanged
#   internal/ui            122   + the confirmation ledger, the prompts, the modes
#
# ⚠ THE FIRST VERSION OF THIS TABLE CARRIED GUESSED FLOORS — 9/7/10/11/22, typed
# before anything had been run. `cmd/mention-review` has FIVE tests, so its
# floor of 9 was unsatisfiable and the tier was red on a green suite. A floor is
# a function of a MEASUREMENT; these are derived from the numbers above by the
# same rule the other two runners use: `m - min(50, max(1, m/20))`.
# Raise one when a package grows. NEVER lower one to get green.
GO_MODULE="nix/pkgs/tools/mention-review/src"
PACKAGES=(
  "cmd/mention-review|4"
  "internal/argv|27"
  "internal/cfg|11"
  "internal/ghapi|37"
  "internal/udiff|11"
  "internal/ui|116"
)

# --- GUARD 2: discovery + the two-way pin --------------------------------------
# 🔴 FILESYSTEM DISCOVERY, NOT `git ls-files`. The flake check builds from a
# `cp -r ${./.}` store copy with NO `.git`, so a git-based discovery would find
# nothing in the exact tier that CI runs.
#
# 🔴 AND BASH GLOBSTAR, NOT `find`. Three different `find`s are reachable from
# this repo — busybox under bash, `bfs` under the interactive zsh, GNU findutils
# in the nix sandbox — and they do not agree on `-printf`. Globstar is a bash
# builtin, so it depends on no external binary and behaves identically in every
# tier.
shopt -s globstar nullglob

discovered=()
for f in "$GO_MODULE"/**/*_test.go; do
  d="$(dirname "$f")"
  rel="${d#"$GO_MODULE"/}"
  case " ${discovered[*]-} " in *" $rel "*) ;; *) discovered+=("$rel") ;; esac
done

pinned=()
for entry in "${PACKAGES[@]}"; do pinned+=("${entry%%|*}"); done

guard_failed=0
for d in "${discovered[@]-}"; do
  case " ${pinned[*]} " in
    *" $d "*) ;;
    *) echo "run-go-tests: FATAL — package '$d' has tests but is NOT in PACKAGES." >&2
       echo "  A new package swept in under the global total is a package nobody gave a floor." >&2
       guard_failed=1 ;;
  esac
done
for d in "${pinned[@]}"; do
  case " ${discovered[*]-} " in
    *" $d "*) ;;
    *) echo "run-go-tests: FATAL — pinned package '$d' has NO test files." >&2
       echo "  The suite vanished, was renamed, or the module moved." >&2
       guard_failed=1 ;;
  esac
done
# 🔴 POSITIVE CONTROL ON DISCOVERY ITSELF. An empty discovery satisfies the
# first loop vacuously, and the second loop's failures would then read as "the
# packages were deleted" rather than "the glob is wrong".
if [ "${#discovered[@]}" -eq 0 ]; then
  echo "run-go-tests: FATAL — discovery found ZERO packages with tests under $GO_MODULE." >&2
  echo "  That is a broken glob or a moved module, not an empty suite." >&2
  guard_failed=1
fi
echo "go packages: discovered=${#discovered[@]} pinned=${#pinned[@]}"

if [ "$CHECK_PACKAGES_ONLY" -eq 1 ]; then
  # 🔴 `SCOPE: NONE`, NEVER FULL. This path validates the pin and exits in
  # milliseconds having run zero tests; a `FULL` + `PASS` pair here is the
  # full-gate-shaped claim off a run that tested nothing.
  SCOPE_STATE="NONE"
  SCOPE_DETAIL="--check-packages validated the pin only, no tests run"
  [ "$guard_failed" -eq 0 ] || exit 4
  exit 0
fi
[ "$guard_failed" -eq 0 ] || { SCOPE_STATE="PARTIAL"
  SCOPE_DETAIL="the package pin tripped before any test ran"; exit 4; }

# --- the toolchain precondition ------------------------------------------------
# 🔴 A MISSING TOOLCHAIN IS EXIT 3, NEVER A PASS. `go test` absent means this
# tier measured nothing; reporting that as success is how a gate goes green over
# an untested language.
if ! command -v go >/dev/null 2>&1; then
  echo "run-go-tests: FATAL — \`go\` is not on PATH." >&2
  echo "  Enter the gate toolchain: nix develop $ROOT" >&2
  SCOPE_STATE="NONE"; SCOPE_DETAIL="go is not on PATH, no tests run"
  exit 3
fi
echo "go: $(go version)"

# 🔴 THE MODULE CACHE MUST BE WRITABLE. In the nix sandbox `$HOME` is not, and
# `go test` then fails with a message about the cache rather than about the
# code — a red that reads like a broken change. The flake check sets these; this
# is the dev-host fallback.
export GOFLAGS="${GOFLAGS:--mod=mod}"
export GOCACHE="${GOCACHE:-$(mktemp -d)/go-build}"

# --- run, and COUNT ------------------------------------------------------------
total_pass=0
total_fail=0
total_skip=0
failed_pkgs=()

for entry in "${PACKAGES[@]}"; do
  pkg="${entry%%|*}"
  floor="${entry##*|}"
  json="$(mktemp)"

  # 🔴 `-json`, AND THE EXIT CODE IS NOT WHAT IS READ. `go test` exits 0 over a
  # package with no test files; the per-test Action records are what say
  # whether anything ran.
  ( cd "$GO_MODULE" && go test -json -count=1 "./$pkg" ) > "$json" 2>&1
  rc=$?

  # 🔴 ORDER-INDEPENDENT, AND THIS TOOK TWO MEASURED FAILURES TO GET RIGHT.
  # Parsing a tool's output makes its FORMAT a dependency nobody pinned, and
  # "no matches" means "possibly the wrong pattern", never "nothing there".
  #
  #   1. The first version grepped `'"Action":"pass","Test":'`. Go's encoder
  #      emits Time, Action, PACKAGE, Test — so it matched nothing and every
  #      package counted 0 tests, on the dev host.
  #   2. The second allowed for that with `'"Action":"pass".*"Test":"'`. It
  #      worked on the dev host's go 1.25.14 and matched NOTHING in the nix
  #      sandbox, whose go is 1.26.7 — a DIFFERENT field order, in the tier
  #      whose blind spots are supposed to differ from the dev host's.
  #
  # Both times the per-package FLOOR is what caught it. Without a floor this
  # tier would have printed `RESULT: PASS` having counted nothing — precisely
  # the failure a floor exists for, and a second reason not to trust a zero.
  #
  # Two greps, so neither field's POSITION matters — only that both tokens are
  # on the line. `-c` on the second counts what the first let through.
  p=$(grep '"Action":"pass"' "$json" 2>/dev/null | grep -c '"Test":"' || true)
  f=$(grep '"Action":"fail"' "$json" 2>/dev/null | grep -c '"Test":"' || true)
  s=$(grep '"Action":"skip"' "$json" 2>/dev/null | grep -c '"Test":"' || true)
  p=${p:-0}; f=${f:-0}; s=${s:-0}
  ran=$((p + f + s))

  total_pass=$((total_pass + p))
  total_fail=$((total_fail + f))
  total_skip=$((total_skip + s))

  status="ok"
  if [ "$f" -gt 0 ] || [ "$rc" -ne 0 ]; then
    status="FAIL"
    failed_pkgs+=("$pkg")
    # Print the failing detail — a summary with no failure text is unactionable.
    #
    # 🔴 BOTH STREAMS, AND THE NON-JSON ONE IS THE IMPORTANT HALF. `go test`
    # writes per-test results as JSON records but writes COMPILE errors to its
    # own stderr as PLAIN TEXT. The first version of this block grepped the JSON
    # records only, so a package that failed to BUILD reported the single line
    # `FAIL <pkg> [build failed]` and the actual error — file, line, symbol —
    # was discarded. Measured in the nix sandbox, where four of five packages
    # failed to build and the log said nothing whatsoever about why.
    grep -v '^{' "$json" | head -40 >&2
    # 🔴 `build-output` TOO, AND THAT IS THE ARM THAT WAS MISSING. A package
    # that fails to COMPILE emits its compiler error as a `build-output` record
    # and nothing else — no `--- FAIL`, no `panic:`, and nothing outside the
    # JSON for `grep -v '^{'` to catch. MEASURED in the nix sandbox: four of
    # five packages failed with `cgo: C compiler "gcc" not found` and this
    # runner printed not one word of it, so three rebuild cycles went into
    # guessing. A failure report that omits the failure is not a report.
    grep -E '"Action":"(build-output|build-fail)"' "$json" | head -20 >&2
    grep '"Action":"output"' "$json" | grep -E '(--- FAIL|panic:)' | head -40 >&2
  fi
  if [ "$ran" -lt "$floor" ]; then
    echo "run-go-tests: FATAL — $pkg ran $ran tests, floor is $floor." >&2
    echo "  A package that collects fewer tests than its floor is a package that" >&2
    echo "  stopped testing something, not a package that got smaller." >&2
    status="FLOOR"
    failed_pkgs+=("$pkg")
  fi
  printf '  %-6s %-24s pass=%-4d fail=%-3d skip=%-3d (floor %d)\n' \
    "$status" "$pkg" "$p" "$f" "$s" "$floor"
  rm -f "$json"
done

global_floor=0
for entry in "${PACKAGES[@]}"; do global_floor=$((global_floor + ${entry##*|})); done
global_floor="${MIN_GO_TESTS:-$global_floor}"
ran_total=$((total_pass + total_fail + total_skip))

echo "TOTAL: pass=$total_pass fail=$total_fail skip=$total_skip ran=$ran_total (global floor $global_floor)"

SCOPE_STATE="FULL"
SCOPE_DETAIL="every pinned Go package, always — this runner has no selection flag"

# 🔴 SKIPS ARE A FINDING, NOT A DETAIL. `internal/ui`'s palette guard skips when
# it cannot find a repo checkout above the module — correct inside the
# Go-module-only `buildGoModule` sandbox, and WRONG here, where this runner
# always runs from a checkout. A budget of zero is what makes a silent skip
# visible instead of reading exactly like a pass.
max_skips="${MAX_GO_SKIPS:-0}"
if [ "$total_skip" -gt "$max_skips" ]; then
  echo "run-go-tests: FATAL — $total_skip test(s) SKIPPED, budget is $max_skips." >&2
  echo "  A test that skips itself reads exactly like a passing one. Either the" >&2
  echo "  skip is legitimate (raise MAX_GO_SKIPS and say why, in the commit) or" >&2
  echo "  something it needs is missing from this environment." >&2
  exit 4
fi

if [ "$ran_total" -lt "$global_floor" ]; then
  echo "run-go-tests: FATAL — ran $ran_total tests, global floor is $global_floor." >&2
  exit 4
fi
if [ "${#failed_pkgs[@]}" -gt 0 ]; then
  echo "run-go-tests: FAILED packages: ${failed_pkgs[*]}" >&2
  exit 1
fi
exit 0
