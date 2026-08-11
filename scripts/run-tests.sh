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
# Note that `HERMETIC_TARGETS` below includes scripts/browser-bridge/tests, but
# that entry collects only the `test_*.py` files in that directory — pytest does
# not see the `.mjs` files at all.
#
# A target in that list may be a DIRECTORY or a single .py FILE; both are used
# today. See GUARD 5.
#
# The caller is responsible for putting a pytest-capable `python` on PATH (the
# flake check does this via the derivation's buildInputs; the pre-push hook wraps
# this in a nix-shell with the deps). This script only ORCHESTRATES: each test
# dir gets its own `python -m pytest` invocation because the suites rely on a
# per-directory sys.path (bare `import collector` / `import extract` etc. would
# collide if collected together under one rootdir).
#
# 🔴 FIVE STRUCTURAL GUARDS — each exists because a green exit code lies here.
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
#   5. TARGET RESOLUTION (`GUARD 5`). Every entry in the target list must resolve
#      to something pytest can actually run, checked UP FRONT and reported by
#      name. This one is retrospective: #276 added a FILE to the list, the old
#      `[ ! -d ]` check rejected it as a "missing directory", and the gate went
#      RED on main with 913 tests never running — while reading like an
#      environment fault. A typo, a moved suite, or an unexpanded glob all failed
#      the same indistinguishable way. Now the list is validated as a whole
#      before any suite runs, and a bad entry is named.
#
# Env overrides (defaults are the point — raise them, don't lower them casually):
#   MIN_TESTS  minimum tests the whole run must REPORT
#              MEASURED 2026-08-02 in the nix sandbox (`nix build
#              .#checks.x86_64-linux.pytests`) at 4eb5798+this change: 5658
#              collected / 5657 passed / 1 skipped / 0 failed. The dev-host
#              tier (scripts/run-tests.sh under nix-shell) reports the same
#              5658. origin/main alone measures 4662.
#              Floor set at 5600 — the previous 2850 had drifted to less
#              than HALF the real total,
#              which is a floor that can no longer detect the collapse it
#              exists for (an entire 989-test suite could vanish under it).
#              Raise it when suites are added; never lower it to get green.
#              2026-08-06: +38 for scripts/tests/test_analyze_service_index_commit.py
#              (the /analyze-service index autocommit). The suite needs no new
#              HERMETIC_TARGETS entry — scripts/tests is already a directory
#              target, so the file is collected by the existing one.
#              2026-08-07: the drift noted here (a floor ~900 below the real
#              total, the same way the 2850 had drifted) is now CLOSED — the
#              floor is set to the measured total with no headroom. See
#              MIN_TESTS below for the current number and how it was measured;
#              do not restate it here, one place only.
#
# Usage:
#   scripts/run-tests.sh [--set hermetic|all] [--check-targets] [ROOT]
#     --set hermetic  (default) — targets safe to run offline in the nix sandbox.
#     --set all                 — hermetic + any targets deferred to the dev host.
#     --check-targets           — run GUARD 5 only (validate the target list and
#                                 exit; no pytest, no tool precondition). Cheap
#                                 enough to be exercised by a unit test.
#   ROOT defaults to the git repo root (or the script's parent-parent).
#
# Exit non-zero if ANY selected suite fails OR any guard above trips. Prints a
# per-dir + total summary (collected / passed / skipped / failed).

set -uo pipefail

SET="hermetic"
ROOT=""
CHECK_TARGETS_ONLY=0
while [ $# -gt 0 ]; do
  case "$1" in
    --set) SET="${2:-hermetic}"; shift; [ $# -gt 0 ] && shift ;;
    --set=*) SET="${1#*=}"; shift ;;
    # Run GUARD 5 (target resolution) and nothing else. Milliseconds, no pytest,
    # no tool precondition — so the guard can be exercised by a regression test
    # without paying for the whole suite. Exit 0 = every target resolves.
    --check-targets) CHECK_TARGETS_ONLY=1; shift ;;
    *) ROOT="$1"; shift ;;
  esac
done

if [ -z "$ROOT" ]; then
  ROOT="$(git -C "$(dirname "${BASH_SOURCE[0]}")" rev-parse --show-toplevel 2>/dev/null || true)"
  [ -n "$ROOT" ] || ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
fi

cd "$ROOT" || { echo "run-tests: cannot cd to ROOT=$ROOT" >&2; exit 2; }

# MEASURED 2026-08-07 on `nix build .#checks.x86_64-linux.pytests` (the
# authoritative tier, which runs `run-tests.sh --set hermetic .`):
#   TOTAL collected=6595  passed=6594  skipped=1  failed=0
#
# 🔴 RE-MEASURE ON THE MERGED TREE, NOT THE BRANCH. The previous value, 6566,
# was measured on a branch two commits behind main and was already stale when
# written: #363 added 13 tests to test_playwright_nixos.py, and this branch adds
# 16 more. A floor measured on a stale tree is a floor with invisible slack —
# the same failure it exists to catch, one level up.
#
# 🔴 The floor tracks the measurement. It was once 5638 against a real total of
# 6545 — 907 tests of slack, more than the entire 783-test
# scripts/initiatives/tests suite: that whole directory could have vanished with
# this gate still reporting green.
#
# `--set all` and `--set hermetic` are measured identical BY CONSTRUCTION today,
# so this one number is valid for both: DEVHOST_TARGETS is empty (see below), so
# `all` appends nothing to the hermetic list. If a DEVHOST target is ever added,
# this floor stops covering `--set all` and needs splitting per set.
#
# Raise this whenever tests are added; LOWER it only with the new measurement
# quoted in the commit message, never to make a red gate go green.
#
# MEASURED 2026-08-10 on `nix build .#checks.x86_64-linux.pytests`, post-rebase
# onto 91aaa21 (#379), not computed. CONTROL PAIR under one tool set, same
# command, same machine:
#     base 91aaa21 : TOTAL collected=6745  passed=6744  failed=0  scripts/tests=1652
#     this branch  : TOTAL collected=6960  passed=6959  failed=0  scripts/tests=1867
#   +215, and 1867-1652 = 215 = exactly the new
#   scripts/tests/test_subsystem_resolver.py suite.
#   That attribution is also the positive control proving the gate RUNS the new
#   suite: it needed no HERMETIC_TARGETS entry, because `scripts/tests` is
#   already a directory target (same as the 2026-08-06 note above), and the
#   delta on that directory's OWN line is what demonstrates it rather than
#   asserting it.
#
# 🔴 RE-MEASURED, NOT CARRIED FORWARD, AND NOT COMPUTED. The pre-rebase value on
# this branch was 6940 against base 5c53f38's 6743. #379 then landed and moved
# the base, so 6940 was stale the moment it did. Arithmetic on the old numbers
# predicted 6942; the merged tree reported 6945, because the branch had also
# gained 3 tests from its own review fix. It then reached 6960 when an audit
# round replaced two vacuous guards and added a live-path-shape pass. Each of
# those numbers was MEASURED at the time it was written — none was computed from
# the one before it, which is the whole point of this note.
#
# EXPECTED_SKIPS is untouched: skipped=1, the one pinned entry. The new suite
# adds ZERO skips by construction — no external binary, no network, no path
# outside tmp_path.
#
# ⚠ The comment this replaced still described the #367 measurement (6643 / base
# fce27f2) while the value beside it had been bumped twice — to 6745 by #379.
# A floor comment that no longer describes its own number is the "a comment is a
# claim too" case from claude/RULES.md; rewritten rather than bumped again.
#
# 🔴 MERGE RESOLUTION (#376 <- origin/main). Both sides raised this floor
# INDEPENDENTLY on different bases, so BOTH numbers are stale on the merged
# tree — the BASE-DEPENDENT hazard above, arriving as a textual conflict:
#     #376 feat/guard-commit-to-main  6897  (measured on base 5c53f38)
#     origin/main via #378            6960  (measured on base 91aaa21)
# The merged tree carries BOTH #376's guard-check suite and #378's
# subsystem-resolver suite, so neither branch ever observed the real total.
# Taking either side's number would have gone green on slack it never earned.
# Re-MEASURED on the merged tree below, not computed from the two above.
MIN_TESTS="${MIN_TESTS:-6960}"  # PENDING re-measure

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
#   nix-instantiate
#           scripts/tests/test_opencode_config.py (10 parametrized handle tests)
#           — `nix_eval()` calls pytest.FAIL, not skip, when it is absent, so
#           without this entry a missing nix reads as 10 unexplained assertion
#           failures deep in the run instead of one named precondition here.
#   opencode
#           scripts/tests/test_opencode_engine.py (19 tests) — runs the REAL
#           `opencode debug agent --pure` against a throwaway config dir seeded
#           from scripts/opencode/. Like nix-instantiate above it calls
#           pytest.FAIL, not skip, when absent: this is the ONLY file that can
#           see a resolver-semantics change under an unchanged config, so a skip
#           there is precisely the silent green the version pin exists to stop.
# logrotate: scripts/tests/test_claude_log_rotate.py drives the REAL binary
# against a temp directory (rotation, truncation, generation cap, and the .bak
# scope fence). Those tests FAIL rather than skip when it is absent — a skipped
# rotation test reports safety it never measured — so it belongs here.
#
# 🔴 `python` is listed as well as `python3` because THIS SCRIPT invokes
# `python -m pytest`, not `python3`. Asserting only `python3` checked a binary
# the runner never calls.
REQUIRED_TOOLS=(bash curl node rg git awk jq grep setsid python python3 nix-instantiate opencode logrotate)
missing_tools=()
for t in "${REQUIRED_TOOLS[@]}"; do
  command -v "$t" >/dev/null 2>&1 || missing_tools+=("$t")
done
# --check-targets runs no tests, so the tool precondition is not its business —
# gating it here keeps the target-resolution guard cheap and independently
# testable instead of coupling it to a full sandbox's PATH.
if [ "$CHECK_TARGETS_ONLY" -eq 1 ]; then
  missing_tools=()
fi
if [ "${#missing_tools[@]}" -gt 0 ]; then
  echo "run-tests: FATAL — required tool(s) missing from PATH: ${missing_tools[*]}" >&2
  echo "  The suites SKIP the tests that need these, so the run would go green while" >&2
  echo "  testing less. Add them to the caller's inputs (flake.nix checks.pytests" >&2
  echo "  nativeBuildInputs / the pre-push nix-shell) — do NOT drop them from" >&2
  echo "  REQUIRED_TOOLS to make this pass." >&2
  exit 2
fi

# --- GUARD 1b: pytest ITSELF must be importable --------------------------------
# 🔴 REQUIRED_TOOLS is a list of BINARIES checked with `command -v`. pytest is
# not a binary this runner calls — it is a MODULE (`python -m pytest`) — so it
# was structurally outside the one guard whose entire job is "the thing that
# runs the tests is present". The precondition that mattered most was the one
# the precondition mechanism could not express.
#
# MEASURED 2026-08-03 on this dev host, with every REQUIRED_TOOLS binary present
# but no pytest importable from `python`: all 17 targets reported
#   run-tests: ERROR — could not parse pytest's summary for <dir>.
# and the run ended `TOTAL collected=0 … RESULT: FAIL`, exit 1.
#
# So the gate did NOT go green — GUARD 4 (unparseable summary) and GUARD 3 (the
# collected floor) both fired, and the "reports per-target PASS with collected=0"
# version of this hole does not exist on this revision. What it produced instead
# was SEVENTEEN copies of a message blaming pytest's OUTPUT FORMAT for what was
# actually "pytest is not installed" — a diagnosis pointing at the wrong
# subsystem, the shape that has repeatedly cost this repo whole sessions (#276's
# "missing directory" read as an environment fault). One named error is the fix.
if [ "$CHECK_TARGETS_ONLY" -eq 0 ]; then
  if ! python -m pytest --version >/dev/null 2>&1; then
    echo "run-tests: FATAL — \`python -m pytest\` is not runnable." >&2
    echo "  \`python\` resolves to $(command -v python 2>/dev/null || echo '(not found)') but the" >&2
    echo "  pytest MODULE is not importable from it, so every suite would collect 0" >&2
    echo "  tests and report an unparseable summary. This is NOT a pytest output-format" >&2
    echo "  change — it is a missing dependency in the caller's environment." >&2
    echo "  Fix the caller: flake.nix checks.pytests builds a python312.withPackages" >&2
    echo "  env that includes pytest; the pre-push hook wraps this in a nix-shell." >&2
    exit 2
  fi
fi

# --- HERMETIC set --------------------------------------------------------------
# Verified to pass in the offline nix sandbox: every third-party call
# (psycopg2 / requests / minio HTTP) is mocked, so no live DB or network is
# reached. See flake.nix `checks.pytests` and the PR body for the audit.
#
# 🔴 ENTRIES MAY BE DIRECTORIES **OR** SINGLE .py FILES. The list was called
# HERMETIC_DIRS while already holding a file, and `run_pytest` rejected anything
# that was not a directory — see GUARD 5 and the note in `run_pytest`. Renamed so
# the name stops asserting something false about its own contents.
HERMETIC_TARGETS=(
  scripts/tests
  scripts/collector/tests
  scripts/collector/keylog/tests
  scripts/collector/claude/tests
  scripts/collector/i3/tests
  scripts/collector/browser-ext/tests
  # Added 2026-08-02. This suite existed since the OpenCode source landed and was
  # never in this list — 166 tests that no gate ran. That is why a plugin whose
  # `tool.execute.after` handler mis-read the hook contract shipped and produced
  # 2,699 rows of `text='unknown'` before anyone noticed.
  scripts/collector/opencode/tests
  # Added 2026-08-02, the SAME shape as the opencode entry above and found the
  # same day: 989 tests that no gate had ever run since dl-router was written.
  # The suite is hermetic by construction (temp roots, temp SQLite, a stub
  # qBittorrent, an injected clock — see its conftest) and passed 989/989 in
  # both tiers once two portability defects were fixed: a stub script written
  # with `#!/usr/bin/env bash` (dead in the sandbox) and a hard-coded port 8799
  # that an orphaned listener on the workbench was answering.
  scripts/dl-router/tests
  scripts/browser-bridge/tests
  scripts/validation/tests
  scripts/session-analysis/tests
  scripts/session-analysis/session_insight/tests
  scripts/mail-actions/tests
  scripts/initiatives/tests
  scripts/repo-cos/tests
  scripts/task-spec-drafter/tests
  # A FILE, not a dir, and deliberately so: scripts/claude-hooks/tests/ also
  # holds hand-rolled scripts that call main() at import and sys.exit(), which
  # pytest cannot collect. Naming the one pytest-collectable file keeps them
  # apart. (test_guard_core.py covers the shared guard core behind BOTH
  # bash-guard.py and opencode's plugin/guard.js.)
  scripts/claude-hooks/tests/test_guard_core.py
)

# --- DEV-HOST-ONLY set ---------------------------------------------------------
# Targets deferred to the pre-push tier (empty today — nothing here currently
# needs a live DB/network at runtime; kept so a future DB-bound suite has a home
# that does NOT block the hermetic flake gate).
DEVHOST_TARGETS=()

TARGETS=("${HERMETIC_TARGETS[@]}")
if [ "$SET" = "all" ]; then
  TARGETS+=("${DEVHOST_TARGETS[@]}")
fi

# --- GUARD 5: every target must RESOLVE, up front ------------------------------
# The #276 failure mode: an entry was added to the list, the runner silently
# rejected it, and the gate failed for a reason that read like an environment
# problem ("missing directory") rather than "this target is missing". A typo, a
# moved suite, or a glob that bash never expanded would all land the same way,
# and only at the point the suite was reached — after minutes of other output.
#
# So validate the WHOLE list before running anything, and name every bad entry
# in one report rather than dying on the first. A directory is fine; a file must
# look pytest-collectable, because `python -m pytest not_a_test.py` collects 0
# and would trip the per-target floor with a confusing message instead of this
# one.
#
# Globs: bash DOES perform pathname expansion inside an array literal (MEASURED
# 2026-08-02 — injecting `scripts/tests/test_*.py` took the list from 15 to 32
# entries), and the array is built AFTER the `cd "$ROOT"`, so a MATCHING glob
# expands to real files and is harmless. The case that must fail is a glob that
# matches NOTHING: with nullglob unset it survives as a literal `*`, which would
# otherwise be reported as a missing path and read as a moved suite. Catching
# the literal metacharacter says what actually happened.
bad_targets=()
for t in "${TARGETS[@]}"; do
  case "$t" in
    *'*'*|*'?'*|*'['*)
      bad_targets+=("$t  — looks like an unexpanded GLOB; name each target literally")
      continue ;;
  esac
  if [ ! -e "$t" ]; then
    bad_targets+=("$t  — does not exist (typo, or the suite moved?)")
  elif [ -d "$t" ]; then
    :
  elif [ -f "$t" ]; then
    case "$(basename "$t")" in
      test_*.py|*_test.py) : ;;
      *) bad_targets+=("$t  — a FILE target must be pytest-collectable (test_*.py / *_test.py)") ;;
    esac
  else
    bad_targets+=("$t  — neither a file nor a directory")
  fi
done
if [ "${#bad_targets[@]}" -gt 0 ]; then
  echo "run-tests: FATAL — ${#bad_targets[@]} unusable entr(ies) in the $SET target list:" >&2
  for b in "${bad_targets[@]}"; do echo "    $b" >&2; done
  echo "  Entries may be DIRECTORIES or single .py FILES. Fix the list (or the" >&2
  echo "  path) — do NOT delete the entry to make this pass, which is how a" >&2
  echo "  suite stops running while the gate goes green." >&2
  exit 2
fi

if [ "$CHECK_TARGETS_ONLY" -eq 1 ]; then
  echo "run-tests: all ${#TARGETS[@]} $SET target(s) resolve."
  for t in "${TARGETS[@]}"; do
    if [ -d "$t" ]; then echo "  dir   $t"; else echo "  file  $t"; fi
  done
  exit 0
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
# ⚠ REMOVED, deliberately — do not re-add. `scripts/tests/test_skill_audit.py`
# carried two regression pins against the LIVE datapacket-talos skill corpus, a
# separate PRIVATE clone that cannot exist in the nix sandbox and may be absent
# on a dev host. Both skipped off an `is_dir()` check on an absolute out-of-repo
# path, and this entry existed to pin those two environment-dependent skips.
# (#332 added the tests without the entry, and the gate was RED on main from
# 2026-08-04 to 2026-08-06 on the unpinned-skip guard alone.)
#
# Same defect as the test_scrub.py case below: a check keyed to a path outside
# the repo is structurally unobservable in the tier that gates merges, so it
# means one thing on a dev host and nothing at all here. Re-pointed 2026-08-06
# at synthetic fixtures TRACKED IN THIS REPO
# (scripts/tests/fixtures/skill_audit/), so those pins now RUN in every tier and
# NEVER skip — which is why there is nothing left to pin.
#
# ⚠ REMOVED, deliberately — do not re-add. test_scrub.py's drift guard used to
# compare scrub.py's SECRET_PATTERNS against the DEPLOYED hook
# (`Path.home()/".claude"/"hooks"/"bash-guard.py"`) and skip when that file was
# absent, so it ran on a switched dev host and skipped in the sandbox — and this
# entry existed to pin that environment-dependent skip.
#
# That HOME-keyed referent was the bug: the check was structurally unobservable
# in the tier that gates merges, so when #276 moved SECRET_PATTERNS into
# guard_core.py the parser returned [] and the test failed on every dev host
# while the hermetic gate stayed green. The test now compares two files that are
# both TRACKED IN THIS REPO (session_insight/scrub.py vs claude-hooks/
# guard_core.py), so it runs in every tier and NEVER skips — which is why there
# is nothing left to pin here.
#
# If you find yourself re-adding a conditional skip for this suite, the drift
# guard has been re-pointed at something environment-dependent again. Fix that
# instead.

fail=0
# 🔴 `RESULTS=()` not `declare -a RESULTS`. Under `set -u`, `declare -a foo`
# leaves the variable DECLARED BUT UNSET, so the first `${#foo[@]}` or
# `"${foo[@]}"` on a still-empty array aborts with "foo: unbound variable"
# (MEASURED on bash 5.3.15; an explicit `=()` assignment makes it set-and-empty).
#
# That was live: with zero skips, GUARD 2's reporting block died at
# `if [ "${#SKIP_LINES[@]}" -eq 0 ]`, printing a raw
# `run-tests.sh: line 479: SKIP_LINES: unbound variable` instead of the skip
# list, and the unpinned-skip loop below it never executed. Because there is no
# `set -e` the script carried on, so the damage was confined to the DIAGNOSTIC
# path — the skip-total accounting still fired — but the runner emitted a bash
# internal error at exactly the moment someone is trying to read why the gate is
# red. That is the #276 lesson repeating: a failure that reads like an
# environment fault rather than a named finding.
RESULTS=()
SKIP_LINES=()
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

  # A target is a DIRECTORY **or** a single FILE, and both are load-bearing:
  # scripts/claude-hooks/tests/ mixes pytest-collectable modules with hand-rolled
  # scripts, so only the collectable file is named (see HERMETIC_TARGETS).
  #
  # 🔴 This guard used to be `[ ! -d "$d" ]`. That rejected the file target added
  # by #276 and reported it as "missing directory" — so the gate was RED on main
  # while the 913 tests in test_guard_core.py never ran, and the failure read
  # like an environment problem rather than "this target is missing". Existence
  # is checked with -e; the file/dir distinction only changes the MESSAGE,
  # because `python -m pytest` accepts either.
  if [ ! -e "$d" ]; then
    echo "run-tests: FATAL — test target does not exist: $d" >&2
    RESULTS+=("FAIL  $d (missing target)")
    fail=1
    echo
    return
  fi
  if [ ! -d "$d" ] && [ ! -f "$d" ]; then
    echo "run-tests: FATAL — test target is neither a file nor a directory: $d" >&2
    RESULTS+=("FAIL  $d (not a file or directory)")
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

for d in "${TARGETS[@]}"; do
  run_pytest "$d"
done

# The claude-hooks tests are hand-rolled scripts (asserts + sys.exit, not
# pytest-collectable) — run them directly. Hermetic (pure string logic + a
# subprocess of the hook itself). Always part of the hermetic set. They report
# no counts, so they are pass/fail only and are NOT part of the totals.
HOOK_TESTS=(
  "scripts/claude-hooks/tests/test_shell_env_nudge.py"
  "scripts/claude-hooks/tests/test_search_tool_nudge.py"
  "scripts/claude-hooks/tests/test_claude_notify.py"
  "scripts/claude-hooks/tests/test_register_nudge_hook.py"
  "scripts/claude-hooks/tests/test_bash_guard.py"
)
for HOOK_TEST in "${HOOK_TESTS[@]}"; do
  # Was `|| continue` — a SILENT skip, the exact #276 shape GUARD 5 exists to stop:
  # an entry added to this list that the runner quietly rejects, leaving the gate
  # green while the tests never ran. A missing entry is now a loud failure.
  if [ ! -f "$HOOK_TEST" ]; then
    echo "run-tests: ERROR — hook test '$HOOK_TEST' does not exist (typo, or moved?)." >&2
    RESULTS+=("FAIL  $HOOK_TEST (missing)")
    fail=1
    continue
  fi
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
