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
# 🔴 THIS FILE USED TO HARD-CODE ONE DIRECTORY.
#    `FILES=(scripts/browser-bridge/tests/*.test.mjs)` was the entire collection
#    step, so `scripts/dl-router/tests/*.test.mjs` (508 tests) and
#    `scripts/collector/browser-ext/tests/*.test.mjs` (21 tests) had NEVER been
#    run by any gate — 529 tests, ungated since each suite was written. Exactly
#    the shape of #306 (989 ungated pytest tests) and #298 (166) and #276 (913),
#    one language over: a target list that only grows when somebody remembers.
#
#    The fix is not "add two more lines to the list" — that leaves the next suite
#    ungated the same way. Collection is now DISCOVERY (every `*.test.mjs` under
#    each entry of `DISCOVERY_ROOTS`), so a new suite is gated the moment it exists.
#
# 🔴 DISCOVERY IS ROOTED, AND A ROOT IS THE SAME DEFECT ONE LEVEL UP.
#    The roots were `scripts/` alone, which is exactly as blind as the old
#    single-directory glob for anything living elsewhere: `claude/skills/clickup/`
#    ships executable .mjs to both hosts (via nix/home.nix) and carries two gates,
#    and a `scripts/**` glob cannot see them no matter how many suites it finds.
#    So the root list is EXPLICIT and lives in one place (`DISCOVERY_ROOTS`), and
#    scripts/tests/test_run_node_tests_suites.py parses it out of this file rather
#    than restating it — a second copy of the list is how a list and its guard
#    drift apart. Adding a new top-level home for .mjs tests means adding it HERE.
#
# 🔴 FOUR STRUCTURAL GUARDS — all four exist because a green exit code lies here:
#
#   1. NEVER PASS A DIRECTORY. `node --test scripts/browser-bridge/tests/` silently
#      collapses to `# tests 1 / # fail 1` (MODULE_NOT_FOUND) — a FALSE RED that a
#      future edit could just as easily turn into a false green. This script globs
#      the files itself and refuses to run if any element is not a regular file
#      ending in `.test.mjs`, so it CANNOT degrade to the dir form.
#
#   2. COUNT THE TESTS, don't read the exit code. A collection failure, a bad glob,
#      or a toolchain breakage can produce "0 tests, exit 0". We parse node's TAP
#      summary and fail unless the run reports at least the floor. Without this the
#      gate can go green while testing nothing — which is exactly how the pytest
#      gate silently skipped 41 test_server.py tests for want of `curl` on PATH.
#
#   3. DISCOVERY + A TWO-WAY PIN (`SUITES`). Discovery alone would make a whole
#      suite going silent invisible again: if `scripts/dl-router/tests` were
#      emptied, deleted or renamed, a bare repo-wide glob would just collect fewer
#      files and — above the global floor — still say PASS. So every discovered
#      suite directory must appear in `SUITES`, and every `SUITES` entry must be
#      discovered. It fails BOTH ways, which is the property #306 established for
#      its shebang allowlist:
#        * a NEW suite directory not in SUITES     -> FATAL (forces an accounting
#          entry with a floor, instead of being silently swept in under the total)
#        * a PINNED suite that discovery misses    -> FATAL (the suite vanished)
#      This is the guard that answers "if the dl-router tests silently stopped
#      executing, would anything go red?" — before this change the honest answer
#      was no, because they were never executing in the first place.
#
#   4. PER-SUITE FLOORS, not just a global one. Each suite runs in its OWN
#      `node --test` invocation with its own TAP summary and its own minimum, so
#      one suite collapsing to near-zero cannot be absorbed by the others' totals.
#      A single global floor of 970 would be fully satisfied by browser-bridge
#      (468) + dl-router (508) with browser-ext's 21 tests silently gone.
#
#   5. THE VERDICT LINE CARRIES THE EXIT STATUS (`RESULT: FAIL (exit=1)`), from
#      a single writer behind an EXIT trap. Every consumer pipes this output and
#      a pipeline reports the LAST command's status, so the truth has to be in
#      the content. See run-tests.sh's GUARD 6 and `scripts/gate.sh`.
#
# Env overrides (defaults are the point — raise them, don't lower them casually):
#   MIN_TESTS  one-off override of the GLOBAL floor, which is otherwise DERIVED
#              as the sum of the per-suite floors in SUITES.
#
# Usage:
#   scripts/run-node-tests.sh [--check-suites] [ROOT]
#     --check-suites  run GUARD 3 only (discovery + the two-way pin) and exit.
#                     No node, no tests — cheap enough to be exercised by a unit
#                     test, mirroring run-tests.sh's --check-targets.
#
# Exit non-zero if any suite fails OR any guard above trips.

set -uo pipefail

# --- GUARD 5: the verdict line CARRIES the exit status -------------------------
# Identical mechanism, and identical reason, to run-tests.sh's GUARD 6 — read
# that block for the incident. Short version: this runner's status is routinely
# destroyed by the pipe every consumer writes around its output, so the status
# goes in the CONTENT, on one line, from one writer that is fed `$?`. An EXIT
# trap makes it total, so an abort or a `timeout` kill ends with a verdict
# instead of the silence a content-parsing consumer reads as "no failures".
VERDICT_EMITTED=0
_emit_verdict() {
  local rc="$1"
  [ "$VERDICT_EMITTED" -eq 0 ] || return 0
  VERDICT_EMITTED=1
  if [ "$rc" -eq 0 ]; then
    echo "RESULT: PASS (exit=0)"
  else
    echo "RESULT: FAIL (exit=$rc)"
  fi
}
_on_exit() { _emit_verdict "$?"; }
trap '_on_exit' EXIT
trap 'exit 143' TERM
trap 'exit 130' INT

CHECK_SUITES_ONLY=0
ROOT=""
while [ $# -gt 0 ]; do
  case "$1" in
    --check-suites) CHECK_SUITES_ONLY=1; shift ;;
    *) ROOT="$1"; shift ;;
  esac
done

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
cd "$ROOT" || { echo "run-node-tests: cannot cd to ROOT=$ROOT" >&2; exit 2; }

# --- the pinned suite table ----------------------------------------------------
# "<dir>|<min_files>|<min_tests>". MEASURED 2026-08-03 in the nix sandbox
# (`nix build .#checks.x86_64-linux.nodetests`); floors sit just under the real
# totals so ordinary test-writing does not trip them but a collapse does.
#
#   scripts/browser-bridge/tests        14 files   468 tests
#   scripts/dl-router/tests             13 files   508 tests
#   scripts/collector/browser-ext/tests  2 files    21 tests
#
# MEASURED 2026-08-13, same way:
#   claude/skills/clickup/test           3 files    71 tests
#   -> 92 tests on 2026-08-14: js-source.mjs split by CODE POINT and indexed by
#      CODE UNIT, so every emoji desynced the offsets both structural guards are
#      built on. Its controls asserted the right property and had simply never
#      been handed a non-ASCII character; 21 of the 21 new tests are those
#      fixtures plus the substitution brace accounting they exposed.
#   (it peaked at 6 files / 154 tests earlier the same day. Most of that was
#   coverage OF the webhook listener, and the listener was then deleted — it
#   had never run on either host: no token configured, no watcher ever
#   registered, no state files, and the forwarder it spawned was not installed.
#   Deleting the feature deleted its three suites with it. A count going DOWN
#   is normally the thing this file exists to catch, so the accounting entry
#   below records why this one is a deletion and not a suite going silent.)
#
# Raise a floor when a suite grows. NEVER lower one to get green — that is the
# move that turned the pytest global floor into less than half the real total.
# A floor is a function of the measurement, not an opinion: `m - min(50, max(1, m/20))`.
SUITES=(
  "scripts/browser-bridge/tests|15|490"
  # Ungated until 2026-08-03: `FILES=` above named browser-bridge alone, so these
  # 508 tests had never run under any gate since dl-router was written.
  "scripts/dl-router/tests|13|500"
  # Ungated for the same reason. Small, but 21 tests reporting safety they never
  # measured is the same defect as 508 of them.
  "scripts/collector/browser-ext/tests|2|20"
  # The clickup skill's hermetic gates (help-coverage: showUsage() is complete;
  # state-paths: no state path resolves inside the read-only skill dir,
  # including a structural seam walk over every module in the tree; js-source:
  # direct controls for the scanner both structural guards are built on). They
  # lived in a standalone, UNCOMMITTED ~/.claude/skills/clickup/ and ran only
  # when a human remembered — ungated by anything, on either host.
  #
  # Was 6 files / 154 tests until the webhook listener was deleted (it had never
  # run on either host): that removed webhook-server, listen-integration and
  # catchup along with the feature. 71 tests measured 2026-08-13,
  # floor 68 = 71 - min(50, max(1, 71/20)).
  # 92 tests measured 2026-08-14 (the astral-character controls js-source.mjs
  # never had), floor 88 = 92 - min(50, max(1, 92/20)) = 92 - 4.
  # 122 tests / 4 files measured 2026-08-21: test/awaiting.test.mjs, the gate for
  # the `awaiting` command (predicate, fan-out cap, pacing) and for the inbox
  # cursor loop. Floor 116 = 122 - min(50, max(1, 122/20)) = 122 - 6.
  #
  # 152 tests / 5 files measured 2026-08-23: test/agent-marker.test.mjs, the gate
  # for the `claw:obj` agent-object marker (Phase 0 of agent-object hygiene) —
  # the grammar, the enumerated `cond` allowlist, the CROSS-LANGUAGE pin against
  # talos-infra's Python implementation (no byte-mirror can bridge the two, so
  # shared vectors are what keeps them from drifting), and the create-path seam.
  # Floor 145 = 152 - min(50, max(1, 152/20)) = 152 - 7.
  #
  # 160 tests / 5 files measured 2026-08-24: the `cond` guard grew a checker
  # requirement (`manual:<who>`, bare `manual` rejected) and an honest
  # missing-condition fallback (`cond=unstated`, warned on stderr at the create
  # site) — plus the enumerated JS/Python divergence those two introduce.
  # Floor 152 = 160 - min(50, max(1, 160/20)) = 160 - 8.
  #
  # 171 tests / 5 files measured 2026-08-24 (fix round on the same PR). The
  # `unstated` sentinel was passable by any CALLER, not just the fallback that
  # observes an absence — so the create seam now validates it, `create-subtask`
  # and batch-create gained a way to name a condition at all, the JS/Python
  # divergence block was rewritten to carry its own provenance, and
  # flows/task-hygiene.md's no-enforcement disclaimer is pinned as prose (it was
  # guarded by nothing; flipping it to a false gate claim went fully green).
  # Floor 163 = 171 - min(50, max(1, 171/20)) = 171 - 8.
  #
  # 188 tests / 6 files measured 2026-08-24: test/awaiting-contract.test.mjs, the JS
  # half of the CROSS-LANGUAGE contract for "the newest comment is not the token
  # owner's". That predicate is implemented twice — here as `isAwaiting()`, and in
  # scripts/check-clickup-addressed/ derived across a seam — and nothing made the two
  # agree. Both suites now read ONE shared table
  # (test/awaiting-contract.fixtures.json) and each MEASURES its own column, so a
  # divergence appearing or disappearing is red on both sides. It also pins the
  # structural blocker that makes a consolidation impossible: `awaiting` emits no row
  # for a task the owner answered last. FILE COUNT MOVES 5 -> 6.
  # Floor 179 = 188 - min(50, max(1, 188/20)) = 188 - 9.
  "claude/skills/clickup/test|6|179"
  # 2026-08-29: the save-button suite is gone with save_button.js (dead on
  # arrival — dl-router requires a bearer token on every endpoint, /healthz
  # included, so the probe 401s and the button never mounts). FILE COUNT MOVES
  # 3 -> 2 while the test count RISES 68 -> 134: v0.2.3 had gutted the suite
  # along with the implementation.
  # Floor 128 = 134 - min(50, max(1, 134/20)) = 134 - 6.
  "scripts/discord-embed-ext/tests|2|128"
)

# --- discovery roots -----------------------------------------------------------
# Every top-level directory that may hold `*.test.mjs`. Read the 🔴 block in the
# header before adding one: this list is the reason a suite outside scripts/ is
# gated at all, and scripts/tests/test_run_node_tests_suites.py parses it FROM
# HERE so the two cannot drift.
DISCOVERY_ROOTS=(
  "scripts"
  "claude"
)

# --- GUARD 3: discovery + the two-way pin --------------------------------------
# Discovery is filesystem-based, NOT `git ls-files`: the flake check builds from
# `cp -r ${./.} src`, which carries no .git, so a git-based discovery would find
# nothing in the exact tier that gates merges.
#
# 🔴 And it is BASH GLOBSTAR, not `find`. The first draft of this used
# `find scripts -name '*.test.mjs' -printf '%h\n'` and discovery came back EMPTY
# on the dev host — `-printf` is a GNU extension and the `find` on bash's PATH
# here is BUSYBOX (~/.nix-profile/bin/find), which rejects it. Three different
# `find`s are reachable from this repo: busybox under bash, `bfs` 4.1.1 under the
# interactive zsh, GNU findutils in the nix sandbox — and only two support
# `-printf`. The original also wrote `2>/dev/null`, which turned the error into
# a silent empty list: the exact "an EMPTY RESULT cannot distinguish two
# mechanisms" trap. Globstar is a bash builtin, so it depends on no external
# binary at all and behaves identically in every tier.
#
# 🔴 node_modules is EXCLUDED. `claude/skills/clickup` has a dependency tree
# (nix/pkgs/clickup-node-modules.nix), and its own UPDATING instructions tell a
# developer to run `npm ci` in the checkout — which materialises 51 packages,
# several of which ship `*.test.mjs` fixtures. Discovery would then find a suite
# directory nobody pinned and the runner would FATAL with "would run UNGATED",
# blaming the developer for following the documented procedure. Third-party test
# files are also not this gate's to run: they are not ours, their floors are not
# ours, and one of them failing says nothing about this repo.
shopt -s globstar nullglob
DISCOVERED_DIRS=()
_seen=""
for _root in "${DISCOVERY_ROOTS[@]}"; do
  for f in "$_root"/**/*.test.mjs; do
    [ -f "$f" ] || continue
    case "$f" in
      */node_modules/*) continue ;;
    esac
    d="${f%/*}"
    case "$_seen" in
      *"|$d|"*) continue ;;
    esac
    _seen="$_seen|$d|"
    DISCOVERED_DIRS+=("$d")
  done
done
shopt -u globstar nullglob

# A root that matches NOTHING is the empty-result trap: it cannot be told apart
# from a root that is simply typo'd or has moved, and both read as "no suites
# here". Every root must contribute at least one suite directory.
root_problems=()
for _root in "${DISCOVERY_ROOTS[@]}"; do
  found=0
  for d in "${DISCOVERED_DIRS[@]}"; do
    case "$d" in
      "$_root"/*) found=1; break ;;
    esac
  done
  [ "$found" -eq 1 ] || root_problems+=("$_root  — a DISCOVERY_ROOTS entry that matched no *.test.mjs at all (typo, moved, or the suites were deleted?)")
done
if [ "${#root_problems[@]}" -gt 0 ]; then
  echo "run-node-tests: FATAL — ${#root_problems[@]} discovery-root problem(s):" >&2
  for b in "${root_problems[@]}"; do echo "    $b" >&2; done
  echo "  A root globbing to nothing silently shrinks the gate to the OTHER roots" >&2
  echo "  while still reporting PASS. Fix the root or remove it deliberately." >&2
  exit 2
fi

PINNED_DIRS=()
for entry in "${SUITES[@]}"; do
  PINNED_DIRS+=("${entry%%|*}")
done

# The global floor is the SUM of the per-suite floors — DERIVED, never written.
# It used to be a hand-maintained `MIN_TESTS:-970` sitting beside per-suite
# floors that already summed to 1010, i.e. a second number saying something
# weaker than the numbers next to it and drifting on its own schedule. That
# duplication is the exact shape that made run-tests.sh's single total take
# eleven values in one day; there is no reason to keep a second copy of it here.
# MIN_TESTS survives only as a one-off env override.
MIN_TESTS_COMPUTED=0
for entry in "${SUITES[@]}"; do
  MIN_TESTS_COMPUTED=$(( MIN_TESTS_COMPUTED + ${entry##*|} ))
done
MIN_TESTS="${MIN_TESTS:-$MIN_TESTS_COMPUTED}"

pin_problems=()

# (a) a discovered suite that nobody pinned — the ungated-suite defect itself.
for d in "${DISCOVERED_DIRS[@]}"; do
  found=0
  for p in "${PINNED_DIRS[@]}"; do
    [ "$d" = "$p" ] && { found=1; break; }
  done
  if [ "$found" -eq 0 ]; then
    shopt -s nullglob; _n=("$d"/*.test.mjs); shopt -u nullglob
    pin_problems+=("$d  — holds ${#_n[@]} .test.mjs file(s) but is NOT in SUITES, so it would run UNGATED. Add it with a measured floor.")
  fi
done

# (b) a pinned suite discovery did not find — the suite vanished.
for p in "${PINNED_DIRS[@]}"; do
  found=0
  for d in "${DISCOVERED_DIRS[@]}"; do
    [ "$d" = "$p" ] && { found=1; break; }
  done
  if [ "$found" -eq 0 ]; then
    pin_problems+=("$p  — pinned in SUITES but discovery found no .test.mjs there (deleted, emptied, or renamed?)")
  fi
done

if [ "${#pin_problems[@]}" -gt 0 ]; then
  echo "run-node-tests: FATAL — ${#pin_problems[@]} suite-pin problem(s):" >&2
  for b in "${pin_problems[@]}"; do echo "    $b" >&2; done
  echo "  SUITES must match the .test.mjs directories on disk EXACTLY, both ways." >&2
  echo "  Do NOT delete an entry to make this pass — that is how a suite stops" >&2
  echo "  running while the gate stays green (508 dl-router tests, ungated for" >&2
  echo "  the whole life of the suite, is what this guard exists to prevent)." >&2
  exit 2
fi

if [ "$CHECK_SUITES_ONLY" -eq 1 ]; then
  echo "run-node-tests: all ${#PINNED_DIRS[@]} pinned suite(s) match discovery."
  for entry in "${SUITES[@]}"; do
    d="${entry%%|*}"; rest="${entry#*|}"
    echo "  suite $d  (min_files=${rest%%|*} min_tests=${rest##*|})"
  done
  exit 0
fi

command -v node >/dev/null 2>&1 || {
  echo "run-node-tests: FATAL — no \`node\` on PATH (the caller must provide one)" >&2
  exit 2
}
echo "run-node-tests: node $(node --version)"

# --- run each suite in its own invocation (GUARD 1, 2, 4) ----------------------
sum() { grep -E "^# $2 [0-9]+$" "$1" | tail -1 | awk '{print $3}'; }

fail=0
declare -a RESULTS
TOT_TESTS=0
TOT_PASS=0
TOT_FAIL=0
TOT_SKIP=0
TOT_FILES=0

for entry in "${SUITES[@]}"; do
  dir="${entry%%|*}"
  rest="${entry#*|}"
  min_files="${rest%%|*}"
  min_tests="${rest##*|}"

  echo "=== node --test $dir ==="

  # GUARD 1: glob the files ourselves; never hand `node --test` a directory.
  shopt -s nullglob
  FILES=("$dir"/*.test.mjs)
  shopt -u nullglob

  if [ "${#FILES[@]}" -lt "$min_files" ]; then
    echo "run-node-tests: FATAL — $dir collected ${#FILES[@]} test file(s), expected >= $min_files." >&2
    echo "  The glob matched nothing or almost nothing. Do NOT 'fix' this by passing" >&2
    echo "  the tests DIRECTORY to \`node --test\` — that yields a bogus 1-test failure." >&2
    RESULTS+=("FAIL  $dir (collected ${#FILES[@]} files, floor $min_files)")
    fail=1
    echo
    continue
  fi
  for f in "${FILES[@]}"; do
    case "$f" in
      *.test.mjs) ;;
      *) echo "run-node-tests: FATAL — refusing non-test argument: $f" >&2; exit 2 ;;
    esac
    [ -f "$f" ] || { echo "run-node-tests: FATAL — not a regular file: $f" >&2; exit 2; }
  done

  LOG="$(mktemp)"
  node --test --test-reporter=tap "${FILES[@]}" >"$LOG" 2>&1
  rc=$?
  cat "$LOG"

  # GUARD 2: parse node's TAP summary; a missing line becomes a sentinel that fails.
  T="$(sum "$LOG" tests)"; P="$(sum "$LOG" pass)"; F="$(sum "$LOG" fail)"
  S="$(sum "$LOG" skipped)"; TD="$(sum "$LOG" todo)"
  : "${T:=-1}"; : "${P:=-1}"; : "${F:=-1}"; : "${S:=0}"; : "${TD:=0}"
  rm -f "$LOG"

  suite_bad=0
  if [ "$T" -lt 0 ] || [ "$P" -lt 0 ] || [ "$F" -lt 0 ]; then
    echo "run-node-tests: ERROR — could not parse node's TAP summary for $dir;" >&2
    echo "  this runner cannot vouch for that run, which is not the same as a pass." >&2
    suite_bad=1
  fi
  # GUARD 4: per-suite floor.
  if [ "$T" -ge 0 ] && [ "$T" -lt "$min_tests" ]; then
    echo "run-node-tests: ERROR — $dir ran only $T tests, floor is $min_tests." >&2
    echo "  Far fewer tests than exist — a VACUOUS GREEN, not a pass. Investigate" >&2
    echo "  before lowering the floor in SUITES." >&2
    suite_bad=1
  fi
  if [ "$F" -gt 0 ]; then
    echo "run-node-tests: ERROR — $F test(s) failed in $dir." >&2
    suite_bad=1
  fi
  if [ "$rc" -ne 0 ] && [ "$suite_bad" -eq 0 ]; then
    echo "run-node-tests: ERROR — node exited $rc for $dir with no failing test —" >&2
    echo "  a crash or an unhandled rejection." >&2
    suite_bad=1
  fi

  [ "$T" -gt 0 ] && TOT_TESTS=$(( TOT_TESTS + T ))
  [ "$P" -gt 0 ] && TOT_PASS=$(( TOT_PASS + P ))
  [ "$F" -gt 0 ] && TOT_FAIL=$(( TOT_FAIL + F ))
  TOT_SKIP=$(( TOT_SKIP + S ))
  TOT_FILES=$(( TOT_FILES + ${#FILES[@]} ))

  if [ "$suite_bad" -ne 0 ]; then
    RESULTS+=("FAIL  $dir  (files=${#FILES[@]} tests=$T pass=$P fail=$F skipped=$S)")
    fail=1
  else
    RESULTS+=("PASS  $dir  (files=${#FILES[@]} tests=$T pass=$P skipped=$S floor=$min_tests)")
  fi
  echo
done

echo "======================== NODE SUMMARY ========================"
for r in "${RESULTS[@]}"; do echo "  $r"; done
echo "  ----"
echo "  TOTAL suites=${#SUITES[@]} files=$TOT_FILES tests=$TOT_TESTS pass=$TOT_PASS fail=$TOT_FAIL skipped=$TOT_SKIP  (floor: $MIN_TESTS)"

if [ "$TOT_TESTS" -lt "$MIN_TESTS" ]; then
  echo "  ERROR: only $TOT_TESTS tests ran in total, floor is $MIN_TESTS." >&2
  echo "         A VACUOUS GREEN, not a pass. Investigate before lowering MIN_TESTS." >&2
  fail=1
fi

# GUARD 5. One writer, fed the same value `exit` is about to take.
_emit_verdict "$fail"
exit "$fail"
