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
# 🔴 SIX STRUCTURAL GUARDS — each exists because a green exit code lies here.
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
#   3. COLLECTED-TEST FLOORS (`TARGET_FLOORS`, one per target). We parse
#      pytest's summary line and count what actually ran rather than reading the
#      exit code — a collection error, an import breakage or an empty glob can
#      produce "0 tests" with a zero exit. There is NO hand-written total any
#      more: it was the most conflict-prone line in the repo (eleven values in
#      one day) because a total is base-dependent. The global floor is the SUM
#      of the per-target floors. Read the TARGET_FLOORS header for the rule and
#      for what the change gave up.
#
#      3a. THE FLOOR TABLE AND THE TARGET LIST PIN EACH OTHER, both ways, so a
#          target cannot run unfloored and a floor cannot describe a suite that
#          is gone. `--check-floors` validates it in milliseconds.
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
#   6. THE VERDICT LINE CARRIES THE EXIT STATUS (`RESULT: FAIL (exit=1)`), from
#      a single writer behind an EXIT trap, because every consumer PIPES this
#      runner's output and a pipeline reports the LAST command's status. Read
#      the GUARD 6 block below for the incident, and `scripts/gate.sh` for the
#      invocation surface that never pipes at all.
#
# Env overrides:
#   MIN_TESTS  overrides the GLOBAL floor for one run. It is DERIVED by default
#              (the sum of the selected targets' TARGET_FLOORS entries), so
#              setting it is a one-off escape hatch, not the place to record a
#              measurement. Raise it, don't lower it. Per-target floors have no
#              env override on purpose — the whole point is that they are
#              reviewed edits.
#
# Usage:
#   scripts/run-tests.sh [--set hermetic|all] [--check-targets] [--check-floors] [ROOT]
#     --set hermetic  (default) — targets safe to run offline in the nix sandbox.
#     --set all                 — hermetic + any targets deferred to the dev host.
#     --check-targets           — run GUARD 5 only (validate the target list and
#                                 exit; no pytest, no tool precondition). Cheap
#                                 enough to be exercised by a unit test.
#     --check-floors            — run GUARD 3a only (validate the TARGET_FLOORS
#                                 two-way pin, print the table and the derived
#                                 global floor, exit). Same reason: cheap.
#   ROOT defaults to the git repo root (or the script's parent-parent).
#
# Exit non-zero if ANY selected suite fails OR any guard above trips. Prints a
# per-dir + total summary (collected / passed / skipped / failed) and, on EVERY
# exit path, exactly one `RESULT: PASS (exit=0)` / `RESULT: FAIL (exit=N)` line
# — the status is in the CONTENT so it survives the pipe every caller writes.

set -uo pipefail

# --- GUARD 6: the verdict line CARRIES the exit status -------------------------
# 🔴 The status of this runner is routinely destroyed before anyone reads it.
# Not by a bug in here — `RESULT: FAIL` has only ever been printed immediately
# before `exit "$fail"` — but by every consumer's need to PIPE the output. The
# gate emits thousands of lines, so agents and humans alike write
# `… 2>&1 | tail -40; echo "rc=$?"`, and a pipeline's status is the LAST
# command's. Four agents hit this independently on 2026-08-11; the same day's
# audit recorded `nix build … | tail` reporting `BUILD_RC=0` over a red build.
#
# So the fix is not "stop piping" — it is to make the status SURVIVE a pipe by
# putting it in the CONTENT, on exactly one line, emitted from exactly one
# place, derived from the status itself:
#
#     RESULT: PASS (exit=0)      RESULT: FAIL (exit=1)
#
# Because `_emit_verdict` is the ONLY writer and it is fed `$?`, the verdict and
# the exit status cannot disagree — there is no code path that prints one and
# returns the other. The EXIT trap is what makes that total: an abort, a kill,
# a `set -u` unbound variable or any of the early `exit 2` preconditions now
# ends with a verdict line too, where before they ended with silence that a
# content-parsing consumer reads as "no failures found".
#
# `scripts/gate.sh` is the invocation surface built on this — it never pipes,
# and it cross-checks this line against the status it actually observed.
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
# Without these, a TERM/INT (a `timeout`, a Ctrl-C, an OOM kill) bypasses the
# EXIT trap in bash and the run ends with no verdict at all — the truncation
# case, which is precisely when a reader most needs to be told the run did not
# finish.
trap 'exit 143' TERM
trap 'exit 130' INT

SET="hermetic"
ROOT=""
CHECK_TARGETS_ONLY=0
CHECK_FLOORS_ONLY=0
while [ $# -gt 0 ]; do
  case "$1" in
    --set) SET="${2:-hermetic}"; shift; [ $# -gt 0 ] && shift ;;
    --set=*) SET="${1#*=}"; shift ;;
    # Run GUARD 5 (target resolution) and nothing else. Milliseconds, no pytest,
    # no tool precondition — so the guard can be exercised by a regression test
    # without paying for the whole suite. Exit 0 = every target resolves.
    --check-targets) CHECK_TARGETS_ONLY=1; shift ;;
    # Same idea for GUARD 3's floor table: validate the two-way pin between
    # TARGET_FLOORS and the target list, print the table, exit. No pytest.
    --check-floors) CHECK_FLOORS_ONLY=1; shift ;;
    *) ROOT="$1"; shift ;;
  esac
done

if [ -z "$ROOT" ]; then
  ROOT="$(git -C "$(dirname "${BASH_SOURCE[0]}")" rev-parse --show-toplevel 2>/dev/null || true)"
  [ -n "$ROOT" ] || ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
fi

# 🔴 `unset CDPATH` BEFORE the first `cd`, and it is not hygiene theatre. With
# CDPATH set in the caller's environment, `cd <dir>` PRINTS the resolved
# directory on stdout, so the extremely common shell idiom
# `HERE=$(cd "$(dirname "$0")" && pwd)` yields a TWO-LINE value. MEASURED on the
# dev host: scripts/tests/test_release_wrapper.sh (a SHELL_TESTS target below)
# then read `.zshrc` through that doubled path and died with
# `FAIL: could not extract _release_run` — a red gate whose message points at
# the wrong thing entirely. The variable is inherited by every child this runner
# starts, so unsetting it here is the one place that fixes it for all of them.
unset CDPATH
cd "$ROOT" || { echo "run-tests: cannot cd to ROOT=$ROOT" >&2; exit 2; }
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
# --check-targets / --check-floors run no tests, so the tool precondition is not
# their business — gating it here keeps both cheap and independently testable
# instead of coupling them to a full sandbox's PATH.
if [ "$CHECK_TARGETS_ONLY" -eq 1 ] || [ "$CHECK_FLOORS_ONLY" -eq 1 ]; then
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
if [ "$CHECK_TARGETS_ONLY" -eq 0 ] && [ "$CHECK_FLOORS_ONLY" -eq 0 ]; then
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

# --- GUARD 3's floor table: PER-TARGET, and NOT an exact total -----------------
# 🔴 THIS REPLACES A SINGLE LITERAL, `MIN_TESTS=<exact total>`, WHICH WAS THE
# MOST CONFLICT-PRONE LINE IN THE REPO. On 2026-08-11 alone it took eleven
# values across eight PRs — 6643 → 6770 → 6897 → 6960 → 6993 → 7122 → 7127 →
# 7138 → 7143 → 7147 → 7168 — because a total is BASE-DEPENDENT: every branch
# measured it against a base the others never saw, so every value was correct
# when written and stale within hours. Three agents reconciled it by hand in one
# day. Worse, `rerere.enabled` is true in this repo and it replayed a resolution
# recorded on a DIFFERENT merge of the same conflict, silently stamping a
# four-way total (7168) onto a two-way tree whose real total was 7143 —
# announcing only `Resolved … using previous resolution`. rerere matches
# conflict-hunk TEXT, not tree membership; on a base-dependent constant that is
# not merely unhelpful, it is reliably wrong while looking like a clean
# auto-resolve.
#
# THE FIX: there is no exact total anywhere any more. Each target carries its
# OWN floor, set BELOW its measured count by a deliberate allowance, and the
# global floor is the SUM of the selected targets' floors — COMPUTED, never
# written. The rule, applied to a target's measured count `m`:
#
#     floor(m) = m - min(50, max(1, m / 20))          [integer division]
#
# That is a function of the CURRENT measurement alone, so a floor carries no
# information about which branches are in the tree — which is exactly what made
# the old total base-dependent. Resolving a conflict here means running the gate
# and applying the rule to the number IT prints; never arithmetic on the two
# sides. And a rerere mis-replay now lands inside a tolerance band instead of
# writing a number that is exactly wrong in the dangerous direction (7168 over a
# 7143 tree was a FALSE RED).
#
# TWO CHECKS PER TARGET, both hard failures (see `run_pytest`):
#   * collected < floor            — the collapse this guard exists for.
#   * collected > floor + drift    — the floor has fallen so far behind the real
#     count that it can no longer detect that collapse; `drift` is
#     `max(60, floor/4)`. This is the failure the single literal kept hitting
#     SILENTLY: it once sat at 5638 against a real 6545 — 907 tests of slack,
#     more than the entire 783-test initiatives suite, which could have vanished
#     with the gate still reporting green. The error message prints the exact
#     replacement number, so bumping it is mechanical rather than a measurement
#     exercise.
#
# 🔴 WHAT THIS CAN NO LONGER CATCH, stated plainly rather than left to be
# discovered: deleting up to `min(50, m/20)` tests from a SINGLE target is now
# silent — up to 50 of scripts/tests' ~1900, 1 of the 13-test i3 suite. The old
# exact total went red on a one-test deletion. That precision is what cost
# eleven reconciliations in a day, and it has never once caught a real deletion;
# the collapses it exists for — a suite emptied, renamed, dropped from
# HERMETIC_TARGETS, or failing to import — move a target's count by far more
# than its allowance, and are now caught on that target's OWN line instead of
# being absorbed into a global total (5 tests vanishing from a 13-test suite was
# invisible under a 7168 global; it is red here).
#
# MEASURED 2026-08-11 at 3acd041 (#388) in BOTH tiers, which agreed
# target-for-target: dev-host `run-tests.sh --set all` and `nix build
# .#checks.x86_64-linux.pytests` each reported TOTAL collected=7194 passed=7193
# skipped=1 failed=0. Every floor below is that tier-agreed count put through
# the rule above. (The floor it replaced, 7168, already had 26 of slack on main
# — three PRs had landed without bumping it.)
#
# ADDING A TARGET: add it to HERMETIC_TARGETS *and* here, or the two-way pin
# below fails naming it. Run the gate once, read that target's `collected=`, and
# write `collected - min(50, max(1, collected/20))`.
TARGET_FLOORS=(
  # 2026-08-11, re-pinned after the session-manager branch merged main: 1873 was
  # measured against a ~1923-test suite and the suite is now 2251, so it carried
  # 378 tests of SLACK — more than this branch's entire suite could vanish with
  # the gate still green, and only ~100 short of the drift error an unrelated
  # author would then have to reconcile. 2251 - min(50, max(1, 2251/20)) = 2201,
  # the gate's own printed count put through the rule above (and through
  # `_suggested_floor` in BOTH its shell and python spellings, which agree).
  #
  # 2026-08-11, the `/handoff` subsystem-index writer: 2251 -> 2455 collected,
  # +134 for scripts/tests/test_subsystem_touch.py and +70 that arrived with
  # main since the line above was written. Re-measured, not computed: the number
  # is `_suggested_floor 2455` — the gate's OWN function, so what it suggests is
  # by construction what it accepts. The suite needed no HERMETIC_TARGETS entry
  # (scripts/tests is already a directory target) and adds ZERO skips, so
  # EXPECTED_SKIPS is untouched; movement on THIS line is the evidence the gate
  # runs the new file at all.
  #
  # 2026-08-12, the idle-bucket split (agents vs bare shells): 2455 -> 2513
  # collected on main + this branch. Re-measured by the authoritative gate on
  # the MERGED tree, not computed from either side of the conflict — this line
  # is a churn magnet (every branch that adds a test re-pins it, so every open
  # PR conflicts here) and `rerere` has previously replayed a stale resolution
  # onto it. `_suggested_floor 2513` = 2513 - min(50, max(1, 125)) = 2463.
  #
  # 2026-08-12, the agent-ops per-window age fix: 2513 -> 2535 collected on
  # main (now carrying the line above) + this branch. Same method — the gate's
  # measurement of the MERGED tree, not arithmetic across the conflict:
  #   _suggested_floor 2535 = 2535 - min(50, max(1, 126)) = 2485.
  #
  # 2026-08-12, the standup fleet-scope fix: 2535 -> 2551 collected, +16 for
  # scripts/tests/test_standup_pr_sweep.py. Same method as every line above —
  # the AUTHORITATIVE gate's own printed count (`nix build
  # .#checks.x86_64-linux.pytests` -> `collected=2551`) put through the gate's
  # own function, NOT arithmetic across a conflict:
  #   _suggested_floor 2551 = 2551 - min(50, max(1, 127)) = 2551 - 50 = 2501.
  # ⚠ The new file must be `git add`ed before that measurement means anything:
  # the first gate run here reported the OLD 2535 because an untracked test is
  # silently absent from the flake source. If this line conflicts with another
  # branch, re-run the gate on the MERGED tree and copy what it prints.
  #
  # 2026-08-12, the waiting-signal branch, doing exactly what the line above
  # says: 2551 -> 2629 collected on main + this branch, measured by the gate on
  # the MERGED tree rather than computed from either side of the conflict.
  #   _suggested_floor 2629 = 2629 - min(50, max(1, 131)) = 2629 - 50 = 2579.
  #
  # 2026-08-12, the subsystem-touch SESSION path source: 2629 -> 2697 collected,
  # +68 in test_subsystem_touch.py (136 -> 204 tests: the session source's
  # positive-control pair, its five named negative controls, the
  # partial-trailing-line edge, and one mutation kill per new guard). The gate's
  # own line on the twice-rebased tree:
  #   PASS  scripts/tests  (collected=2697 passed=2697 skipped=0)
  # put through the gate's own function:
  #   _suggested_floor 2697 = 2697 - min(50, max(1, 134)) = 2697 - 50 = 2647.
  #
  # 🔴 THIS BRANCH MEASURED THIS LINE THREE TIMES AND THE FIRST TWO READINGS ARE
  # VOID — which is the whole argument for re-measuring rather than reconciling.
  # It read `collected=2603` against a base of 2535; the standup fix moved the
  # base to 2551 and it re-read 2619; the waiting-signal branch moved it again to
  # 2629. Only the last reading, taken by the authoritative gate on the tree that
  # actually exists, is worth anything. What that discipline caught: this branch
  # had recorded its OWN delta as +118, and the measured figure is +68 — a wrong
  # number that arithmetic across the conflict would have written straight into
  # the floor, where nothing would ever have caught it (a floor is a minimum, so
  # one that is merely too low fails silently forever).
  #
  # ⚠ Each rebase so far has been purely additive. That is a RESULT of measuring,
  # not a licence to assume it: a merge can change collection through a shared
  # fixture, a colliding parametrize id, or a module that stops importing.
  #
  # See the commit message for the gate line this number was copied from.
  #
  # 2026-08-12, the subsystem-touch PR path source: 2697 -> 2825 collected,
  # +128 in test_subsystem_touch.py (204 -> 332 tests: the PR source's
  # positive-control pair, its ten named negative controls including the
  # measured `gh` file-list truncation, the gh-failure classifier pinned against
  # captured stderr, the repo-slug derivation, the caveat's session-vs-branch
  # wording, the do-not-compose refusals, and one mutation kill per new guard).
  # The AUTHORITATIVE gate's own line, `nix build .#checks.x86_64-linux.pytests`:
  #   PASS  scripts/tests  (collected=2825 passed=2825 skipped=0)
  # put through the gate's OWN function rather than arithmetic:
  #   _suggested_floor 2825 = 2825 - min(50, max(1, 141)) = 2825 - 50 = 2775.
  #
  # ⚠ The recorded delta and the measured one AGREE this time (+128 both ways),
  # which the line above says not to count on: the check is that 2697 + 128 =
  # 2825 exactly, i.e. nothing else moved the base since #421 landed. That is a
  # reading, not an assumption — if this line conflicts with another branch,
  # re-run the gate on the MERGED tree and copy what it prints. Do not reconcile
  # the two sides by hand.
  #
  # ⚠ ZERO new skips, and no HERMETIC_TARGETS entry needed (scripts/tests is
  # already a directory target). The new source shells out to `gh`, which is in
  # NEITHER `REQUIRED_TOOLS` below NOR flake.nix's `nativeBuildInputs` — so its
  # fetch is injectable and every test drives a fixture. `no_live_gh` in
  # test_subsystem_touch.py fails any test that reaches the real binary, which
  # is what keeps this line's `skipped=0` true rather than lucky.
  #
  # 2026-08-12, the subsystem-index READ half (`subsystem_recall.py` + the
  # `/resume` step): 2825 -> 2958 collected, +133 in the new
  # scripts/tests/test_subsystem_recall.py (the scope-wide selection rule, the
  # positive-control pair, the six zeros, four named negative controls, the
  # caveat-on-every-output-path sweep across BOTH renderers, the
  # append-concurrency MEASUREMENT that retracted the "fails loudly" claim, the
  # resume/handoff doc pins, and one mutation kill per guard).
  # The AUTHORITATIVE gate's own line, `nix build .#checks.x86_64-linux.pytests`:
  #   PASS  scripts/tests  (collected=2958 passed=2958 skipped=0 floor=2775)
  # put through the gate's OWN rule rather than arithmetic:
  #   _suggested_floor 2958 = 2958 - min(50, max(1, 147)) = 2958 - 50 = 2908.
  #
  # ⚠ The recorded delta and the measured one AGREE (+133 both ways), which the
  # lines above say not to count on: the check is that 2825 + 133 = 2958
  # exactly, i.e. nothing else moved the base since #424 landed. That is a
  # reading, not an assumption. If this line conflicts with another branch,
  # re-run the gate on the MERGED tree and copy what it prints — do NOT
  # reconcile the two sides by hand. (This line has now conflicted on nine
  # consecutive PRs; the reconcile-by-hand failure it keeps inviting is a floor
  # that is merely too LOW, which is a minimum and therefore fails silently
  # forever.)
  #
  # ⚠ ZERO new skips, and no HERMETIC_TARGETS entry needed (scripts/tests is
  # already a directory target). The reader shells out to nothing — no `gh`, no
  # network, and git only via the writer's `scope_for_repo`, which `git` in
  # REQUIRED_TOOLS already covers.
  #
  # 2026-08-12, the subsystem-index writer showing what is ALREADY THERE before
  # it proposes an append: 2962 -> 3026 collected. The AUTHORITATIVE gate's own
  # line, `nix build .#checks.x86_64-linux.pytests`:
  #   PASS  scripts/tests  (collected=3026 passed=3026 skipped=0 floor=2908)
  # put through the gate's OWN function rather than arithmetic:
  #   _suggested_floor 3026 = 3026 - min(50, max(1, 151)) = 3026 - 50 = 2976.
  #
  # ⚠ THE DELTA WAS ATTRIBUTED, NOT ASSUMED, and doing so moved the number. The
  # naive read — 3026 minus the 2958 the line above recorded — says this branch
  # added 68 tests. It added 64. Measured by splitting the target: 3026 total
  # minus 744 in the three `test_subsystem_*.py` files leaves 2282 elsewhere, and
  # those three files collected 680 before this branch, so the base this branch
  # actually started from was 2962, not 2958. The other +4 arrived with main
  # after #426 landed. The floor is unaffected (it is measured, not computed),
  # but "+68" would have gone into this comment as a fact about a branch that
  # never produced it — which is the failure the note below warns about, one
  # level down. This line has now conflicted on TEN consecutive PRs: if it
  # conflicts again, re-run the gate on the MERGED tree and copy what it prints.
  # Do NOT reconcile the two sides by hand.
  #
  # ⚠ ZERO new skips, and no HERMETIC_TARGETS entry needed (scripts/tests is
  # already a directory target). No new FILE either — every change lands in an
  # already-tracked one, so the "a new file must be git added or the flake
  # silently omits it" trap has no purchase here; movement on THIS line is still
  # the evidence the gate ran the new cases.
  #
  # 2026-08-12, the subsystem-touch COMMIT path source: 3041 -> 3150 collected.
  # The AUTHORITATIVE gate's own line, `nix build .#checks.x86_64-linux.pytests`:
  #   PASS  scripts/tests  (collected=3150 passed=3150 skipped=0 floor=2976)
  # put through the gate's OWN function rather than arithmetic:
  #   _suggested_floor 3150 = 3150 - min(50, max(1, 157)) = 3150 - 50 = 3100.
  #
  # ⚠ THE DELTA IS THE BRANCH'S, NOT THE TARGET'S MOVEMENT — and the difference
  # is 15 tests. The naive read, 3150 minus the 3026 the line above recorded,
  # says +124. This branch added +109. Both halves were measured, not inferred:
  # the gate was run on UNMODIFIED origin/main first and printed `collected=3041`
  # (so +15 had arrived with main since #429 landed), and the branch's own tests
  # all live in ONE file, which went 374 -> 483 = +109 by per-file collection.
  # The two attributions agree exactly, which is the check — if they had not,
  # something else in the target had moved and the +109 would have been a fact
  # about a branch that never produced it. That is now the third consecutive PR
  # where re-measuring changed the recorded delta.
  #
  # ⚠ ZERO new skips (still the one pinned in repo-cos), no HERMETIC_TARGETS
  # entry needed, and NO NEW FILE — every change lands in an already-tracked one,
  # so the "a new file must be git added or the flake silently omits it" trap has
  # no purchase here. Movement on THIS line is still the evidence the gate ran
  # the new cases. If this line conflicts with another branch — it has now on
  # ELEVEN consecutive PRs — re-run the gate on the MERGED tree and copy what it
  # prints. Do NOT reconcile the two sides by hand.
  #
  # ✅ FIXED — the flake formerly recorded here is gone. It was test_dedupe.py's
  # `test_st_blocks_CANNOT_see_a_fallocated_partial` reporting
  # `assert 16896 == 16904`: it pinned st_blocks EQUALITY between a
  # `posix_fallocate`d file and a fully-written one, which is a filesystem
  # allocation property (one 4K extent-tree metadata block, taken or not
  # depending on tmpdir state), not a property of the code under test.
  # Measured on ext4: the two disagreed in 17 of 40 runs, in either direction.
  # It now asserts what the test actually needs — that BOTH files are densely
  # allocated — so it no longer keys on allocator luck.
  #
  # ⚠ INDEPENDENTLY CORROBORATED BEFORE THAT FIX LANDED, and worth keeping as a
  # worked example of how to read one red run. The /handoff branch below was
  # handed a brief asserting main was RED on this test. It measured instead: the
  # failure appeared in 1 of 5 authoritative runs, with the assertion's operands
  # REVERSED between reports (`16904 == 16896` vs `16896 == 16904`) — which is
  # the nondeterminism, visible without knowing the cause. Unmodified main
  # reported `PASS scripts/dl-router/tests (collected=991 passed=991 skipped=0)`.
  # So "main is red" was wrong, "it is a real defect" was right, and the two are
  # separable by re-running. Do not treat a single red run as either one.
  #
  # 2026-08-12, clawgate stuck-dispatch visibility: 3341 collected on
  # main + this branch, measured by the authoritative gate on the MERGED tree
  # (not arithmetic across the conflict — this line is re-pinned by every
  # branch that adds a test, and rerere has replayed a stale resolution onto
  # it before). _suggested_floor 3341 = 3341 - min(50, max(1, 167)) = 3291.
  #
  # 2026-08-12, the /handoff step-4 probe-first restructure: 3341 -> 3348
  # collected, +7 in this target. The gate's own line on the twice-merged tree:
  #   PASS  scripts/tests  (collected=3348 passed=3348 skipped=0)
  # put through the gate's own function: _suggested_floor 3348 = 3348 - min(50,
  # max(1, 167)) = 3298. (The python spelling in test_run_tests_floors.py
  # agrees.)
  #
  # 🔴 RE-MEASURED THREE TIMES AND THE FIRST TWO READINGS ARE VOID, because main
  # moved under this branch twice while it was in flight: 3214 (floor 3164),
  # then 3341 arrived with #431/#435, then 3348. Only the last, taken by the
  # authoritative gate on the tree that actually exists, is worth anything.
  #
  # 🔴 THE DELTA IS DECOMPOSED, because a floor line that records movement it did
  # not cause is how the next author inherits a wrong number:
  #     3200  unmodified origin/main at c7579e4, measured FIRST as the control
  #     +  7  this branch (4 in test_subsystem_recall.py, 3 in
  #           test_subsystem_touch.py — confirmed per-file by --collect-only)
  #     +  7  #433's test_cpu_monitor_ignore.py, landed mid-flight
  #     +134  #431 + #435, landed mid-flight
  #     ----
  #     3348  the merged tree, which is what the gate printed
  # This branch's +7 held across all THREE measurements, against three different
  # bases — which is the check. Two independent attributions also agreed exactly
  # at the 3214 checkpoint: the gate's total and per-file collection. Had they
  # disagreed, something else in the target had moved and neither number would
  # have been a fact about a branch.
  #
  # 🔴 MEASURING MAIN IS WHAT MADE THIS SAFE. The 3100 entry's own arithmetic
  # implies main should have been 3150; it measured 3200, so main was carrying 50
  # tests this table had never seen. Subtracting from the RECORDED number would
  # have claimed +64 for this branch and a floor ~50 too high — a FALSE RED for
  # whoever landed next.
  #
  # ⚠ ZERO new skips — `skipped=0` on this target in every run, and the suite's
  # single skip is still the pinned one in repo-cos. A NEW FILE was added
  # (claude/skills/handoff/reference/index-write.md); it is `git add`ed, and
  # `test_the_sidecar_is_DEPLOYED` fails if it ever is not — in the sandbox by
  # its absence, on the host via `git ls-files`.
  #
  # 2026-08-13, the clawgate stuck-dispatch FIX-FORWARD (the grace window on
  # every disjunct, the bar's stuck rendering + its own toast latch, the
  # None-vs-[] renderer, the shared schema reading, and the de-vacuum'd guards).
  #
  # 🔴 RE-MEASURED ON THE MERGED TREE, and the branch's own earlier reading (3471,
  # floor 3421) is VOID — main moved under this branch while it was in flight
  # (#418/#436/#437 landed, and #436 re-pinned THIS line to 3298). Resolving the
  # conflict by arithmetic across the two sides is exactly what the header
  # forbids; this is the gate's own number on the tree that now exists.
  #
  # 🔴 THE DELTA IS DECOMPOSED, so the next author does not inherit movement this
  # branch did not cause:
  #     3348  origin/main at 5ebc208 as the merged base recorded it (#436's entry)
  #     + 14  arrived with #418/#437 after that entry was written
  #     +116  this branch (the grace-window boundary tables, the bar's stuck
  #           rendering, the second toast latch, the renderer's None cases, the
  #           schema_ok matrix, and the rewritten guards) — NET of one deletion:
  #           `clawgate_tasks.unmeasured()` was dead code with no caller, so its
  #           test went with it and a one-line absence guard replaced it
  #     ----
  #     3478  the merged tree, which is what the gate printed
  #   _suggested_floor 3478 = 3478 - min(50, max(1, 3478/20 = 173)) = 3478 - 50
  #                         = 3428.
  # ⚠ ZERO new skips — `skipped=0` on this target, and the suite's single skip is
  # still the pinned one in repo-cos. No new FILES: every change is to a file the
  # flake already tracks.
  "scripts/tests|3428"
  # 2026-08-11, the session-summary changed-paths work: 230 -> 273 collected,
  # +43 for scripts/collector/tests/test_changed_paths.py (the shared
  # `changed_paths*` module). The gate printed this replacement itself —
  # 273 - min(50, max(1, 273/20)) = 273 - 13 = 260 — so it is that run's own
  # count put through the documented rule, not a number anyone computed.
  # The suite needed no HERMETIC_TARGETS entry: scripts/collector/tests is
  # already a directory target, and movement on THIS line is the evidence the
  # gate runs the new file at all.
  "scripts/collector/tests|260"
  "scripts/collector/keylog/tests|79"
  # 2026-08-11, the malformed-`file_path` abort fix: +28 tests here (the
  # extraction-site guard, run()'s per-session skip-and-report, the exit-status
  # contract that makes `failed=N` reach systemd, and the audit round that moved
  # `build_event` inside the try). The gate printed `collected=115 … floor=105`
  # on the run that set this; the 59 it started from was ALSO already 46 behind
  # — inside the drift band (max(60, 59/4)) and therefore silent, the same slack
  # the line above was re-pinned for. Rule applied to the gate's own count:
  # 115 - min(50, max(1, 115/20)) = 115 - 5 = 110.
  "scripts/collector/claude/tests|110"
  "scripts/collector/i3/tests|12"
  "scripts/collector/browser-ext/tests|12"
  "scripts/collector/opencode/tests|162"
  "scripts/dl-router/tests|942"
  "scripts/browser-bridge/tests|469"
  "scripts/validation/tests|97"
  # 2026-08-12, initiative-scan's `gh_available` honesty flag: 381 -> 386
  # collected, +5 for the flag's probe/report/render cases in
  # test_initiative_scan.py. Gate's own count through the gate's own rule:
  #   _suggested_floor 386 = 386 - min(50, max(1, 19)) = 386 - 19 = 367.
  "scripts/session-analysis/tests|367"
  "scripts/session-analysis/session_insight/tests|55"
  "scripts/mail-actions/tests|129"
  "scripts/initiatives/tests|745"
  "scripts/repo-cos/tests|315"
  "scripts/task-spec-drafter/tests|135"
  "scripts/claude-hooks/tests/test_guard_core.py|1260"
)

# The allowance rule, in one place, used by BOTH the drift message and anyone
# adding a target. Keeping it here rather than restating it means the number the
# gate SUGGESTS is by construction the number the gate would ACCEPT.
_suggested_floor() { # $1 = observed collected count
  local m="$1" allow
  allow=$(( m / 20 ))
  [ "$allow" -lt 1 ]  && allow=1
  [ "$allow" -gt 50 ] && allow=50
  printf '%s' $(( m - allow ))
}

_floor_for() { # $1 = target; echoes its floor, non-zero exit if unpinned
  local t="$1" entry
  for entry in "${TARGET_FLOORS[@]}"; do
    if [ "${entry%%|*}" = "$t" ]; then printf '%s' "${entry##*|}"; return 0; fi
  done
  return 1
}

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

# --- GUARD 3a: the floor table and the target list must pin each other ---------
# BOTH ways, the property run-node-tests.sh's SUITES established: a target with
# no floor would run under no floor at all (the ungated-suite defect, hit four
# times in this repo — #276/#298/#306 and the .mjs discovery gap), and a floor
# for a target nobody runs is an accounting entry describing nothing. Checked
# against every KNOWN target (hermetic + dev-host), not just the selected set,
# so `--set hermetic` does not read a dev-host target's floor as orphaned.
#
# Deliberately AFTER GUARD 5: a bad target must be reported as a bad target, not
# as an unpinned one. Ordering is what keeps each guard reachable for its own
# reason.
ALL_KNOWN_TARGETS=("${HERMETIC_TARGETS[@]}" ${DEVHOST_TARGETS[@]+"${DEVHOST_TARGETS[@]}"})
floor_problems=()
for t in "${ALL_KNOWN_TARGETS[@]}"; do
  if ! _floor_for "$t" >/dev/null; then
    floor_problems+=("$t  — a target with NO entry in TARGET_FLOORS; it would run under no floor at all")
  fi
done
for entry in "${TARGET_FLOORS[@]}"; do
  ft="${entry%%|*}"
  fv="${entry##*|}"
  found=0
  for t in "${ALL_KNOWN_TARGETS[@]}"; do
    [ "$t" = "$ft" ] && { found=1; break; }
  done
  if [ "$found" -eq 0 ]; then
    floor_problems+=("$ft  — pinned in TARGET_FLOORS but in NO target list (deleted, renamed, or a typo?)")
  fi
  case "$fv" in
    ''|*[!0-9]*) floor_problems+=("$ft  — floor '$fv' is not a non-negative integer") ;;
    *) [ "$fv" -lt 1 ] && floor_problems+=("$ft  — floor $fv is not a floor; a target must be pinned above 0") ;;
  esac
done
if [ "${#floor_problems[@]}" -gt 0 ]; then
  echo "run-tests: FATAL — ${#floor_problems[@]} problem(s) in TARGET_FLOORS:" >&2
  for b in "${floor_problems[@]}"; do echo "    $b" >&2; done
  echo "  TARGET_FLOORS must match the target list EXACTLY, both ways." >&2
  echo "  Do NOT delete an entry to make this pass — that is how a suite stops" >&2
  echo "  being floored while the gate stays green." >&2
  exit 2
fi

# The global floor is DERIVED. Nothing hand-writes a total any more; MIN_TESTS
# survives only as an env override for a one-off (raise it, don't lower it).
MIN_TESTS_COMPUTED=0
for t in "${TARGETS[@]}"; do
  MIN_TESTS_COMPUTED=$(( MIN_TESTS_COMPUTED + $(_floor_for "$t") ))
done
MIN_TESTS="${MIN_TESTS:-$MIN_TESTS_COMPUTED}"

if [ "$CHECK_FLOORS_ONLY" -eq 1 ]; then
  echo "run-tests: all ${#TARGET_FLOORS[@]} floor(s) pin a known target, both ways (${#ALL_KNOWN_TARGETS[@]} known: hermetic + dev-host)."
  for entry in "${TARGET_FLOORS[@]}"; do
    echo "  floor ${entry##*|}  ${entry%%|*}"
  done
  echo "  ----"
  echo "  GLOBAL floor (sum over the $SET set) = $MIN_TESTS_COMPUTED"
  exit 0
fi

# A writable, self-consistent HOME so the claude-hooks nudge cache-write path
# works in the sandbox (the hook derives HOME via expanduser; the test derives
# it the same way, so the value only has to be writable — not "real").
export HOME="${HOME:-/tmp}"
if [ ! -w "$HOME" ]; then
  export HOME="$(mktemp -d)"
fi

# --- GUARD 7: NO TEST MAY REACH A REAL LAUNCHER, IN ANY TARGET -----------------
# 🔴 THE ENFORCEMENT POINT. Read this before touching anything below it.
#
# #399 built the mechanism (`scripts/testlib/nolaunch.py`) and installed it from
# `scripts/tests/conftest.py`. This script runs ONE pytest process per target,
# and there are 17 of them plus the hook/shell scripts — so that conftest
# protected exactly ONE. A count of enforcement DECLARATIONS is not a count of
# protected INSTANCES (claude/RULES.md), and the escape that produced 158 real
# desktop toasts in 49 minutes came from a seam test run against a tree where
# the seam did not exist — a shape that can occur in any target.
#
# The fix is attached HERE, at the single place every target is invoked, rather
# than copied into 17 directories: 17 copies drift, and the 18th target gets
# none. Three exports and one pytest flag:
#
#   * a record-only stub dir, FIRST on PATH for this whole script. Covers every
#     PATH-resolved launch made by anything this runner starts — including the
#     NON-pytest targets (HOOK_TESTS, SHELL_TESTS), which have no plugin to load.
#   * `PYTHONPATH` + `-p testlib.nolaunch_plugin` on the pytest line, so every
#     pytest target ALSO gets the in-process layer that intercepts ABSOLUTE-path
#     launches by basename. PATH cannot shadow an absolute path — see
#     `scripts/browser-bridge/server.py`'s `_I3_MSG_FALLBACKS`, which resolves
#     `/run/current-system/sw/bin/i3-msg` directly whenever `which` misses.
#   * `-p` deliberately, NOT `PYTEST_PLUGINS=`: the env var is INHERITED by the
#     nested pytest sessions that `test_no_real_launchers.py` runs as
#     control/mutant pairs, which would protect the mutant half and turn those
#     pins green on their own mutants.
#
# The plugin writes ONE `nolaunch(session)` marker line per pytest session, and
# the accounting below REQUIRES exactly one per pytest target. That is the
# per-target positive control: a target with no marker is a target this guard
# never loaded in, and "no marker" is otherwise indistinguishable from the
# reassuring zero of "nothing tried to launch".
NOLAUNCH_DIR="$(mktemp -d)"
if ! PYTHONPATH="$ROOT/scripts${PYTHONPATH:+:$PYTHONPATH}" \
     python -m testlib.nolaunch "$NOLAUNCH_DIR" >/dev/null 2>&1; then
  echo "run-tests: FATAL — could not install the no-real-launcher stubs into" >&2
  echo "  $NOLAUNCH_DIR. Refusing to run: the suites reach systemd-run, dunstify," >&2
  echo "  openrgb and ddcutil, and without these stubs a green run means real" >&2
  echo "  transient timers and real toasts on the operator's desktop." >&2
  exit 2
fi
NOLAUNCH_LOG="$NOLAUNCH_DIR/launches.log"
export PATH="$NOLAUNCH_DIR:$PATH"
export DEVRC_TEST_LAUNCH_STUB_DIR="$NOLAUNCH_DIR"
export PYTHONPATH="$ROOT/scripts${PYTHONPATH:+:$PYTHONPATH}"

# 🔴 THE ACKNOWLEDGEMENT LEDGER — "<target>|<reason>". A target listed here is
# EXPECTED to drive launchers into the stub, and is REQUIRED to: an entry whose
# target records zero intercepts fails the run. That direction is the point.
# The mutant that matters is not "the guard is deleted" — it is "the guard
# silently covers nothing while the suite stays green", and a ledger that only
# PERMITTED intercepts would be green under exactly that mutant.
NOLAUNCH_ACK=(
  "scripts/tests|drives monitor-blackout's systemd-run scheduling, rig-control's openrgb/notify-send and bar-status-poll's fire_toast INTO the stub on purpose — those are the seam tests, and their whole value is that the launch really happens and really lands here"
)
# Every intercept from any OTHER target is a finding: a test in that target
# tried to touch the operator's machine and only this guard stopped it.
NOLAUNCH_SEEN=()

_nolaunch_lines() { # total lines in the launch log (0 when absent)
  if [ -f "$NOLAUNCH_LOG" ]; then wc -l < "$NOLAUNCH_LOG" | tr -d ' '; else echo 0; fi
}
_nolaunch_slice() { # $1 = first line (1-based), $2 = last line
  [ -f "$NOLAUNCH_LOG" ] || return 0
  [ "$2" -ge "$1" ] || return 0
  sed -n "$1,$2p" "$NOLAUNCH_LOG"
}
_nolaunch_ack_reason() {
  local t="$1" entry
  for entry in "${NOLAUNCH_ACK[@]}"; do
    [ "${entry%%|*}" = "$t" ] && { printf '%s' "${entry#*|}"; return 0; }
  done
  return 1
}

# ⚠ The "every NOLAUNCH_ACK entry names a real target" pin is deliberately NOT
# a runtime check here. It was one, and it PREEMPTED ten existing regression
# tests that build a runner copy with a substituted target list — an earlier
# check that always wins, so their own findings became unobservable (the
# unreachable-guard trap in claude/RULES.md). It lives in
# `scripts/tests/test_no_real_launchers_all_targets.py`, which parses THIS file,
# the same way TARGET_FLOORS' two-way pin is tested.

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
    return 1
  fi
  if [ ! -d "$d" ] && [ ! -f "$d" ]; then
    echo "run-tests: FATAL — test target is neither a file nor a directory: $d" >&2
    RESULTS+=("FAIL  $d (not a file or directory)")
    fail=1
    echo
    return 1
  fi

  local log rc nl_before nl_after
  log="$(mktemp)"
  nl_before="$(_nolaunch_lines)"
  # 🔴 `-p testlib.nolaunch_plugin` is GUARD 7 (see its header). It is on THIS
  # line — the one place every target is invoked — and not in 17 conftests.
  python -m pytest "$d" -q -p no:cacheprovider -p testlib.nolaunch_plugin \
    --no-header -rs >"$log" 2>&1
  rc=$?
  nl_after="$(_nolaunch_lines)"
  NOLAUNCH_SEEN+=("$d|$(( nl_before + 1 ))|$nl_after")
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
    return 1
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

  # GUARD 3 (per-target). Three INDEPENDENT checks, not an elif chain: a suite
  # that both collapsed AND failed used to report only whichever branch matched
  # first, so the second finding was invisible in the summary line.
  local floor ceiling drift bad
  bad=0
  floor="$(_floor_for "$d")" || floor=0
  drift=$(( floor / 4 ))
  [ "$drift" -lt 60 ] && drift=60
  ceiling=$(( floor + drift ))

  if [ "$collected" -lt 1 ]; then
    echo "run-tests: ERROR — $d collected 0 tests (summary: $summary)." >&2
    echo "  A collection error or an import breakage, not a pass." >&2
    RESULTS+=("FAIL  $d (collected 0 tests)")
    bad=1
  elif [ "$floor" -ge 1 ] && [ "$collected" -lt "$floor" ]; then
    # The collapse this floor exists for. Reachable ONLY in 1..floor-1 — the
    # `collected 0` branch above deliberately owns 0, because "the suite did not
    # collect" and "the suite shrank" are different findings.
    echo "run-tests: ERROR — $d collected $collected tests, its floor is $floor." >&2
    echo "  Tests that used to run no longer do. Investigate BEFORE lowering the" >&2
    echo "  floor: a suite emptied, renamed, or failing to import lands here." >&2
    echo "  If the deletion is deliberate, lower it to $(_suggested_floor "$collected") and say why in" >&2
    echo "  the commit — that visible edit IS the accounting." >&2
    RESULTS+=("FAIL  $d  (collected=$collected below floor $floor)")
    bad=1
  elif [ "$floor" -ge 1 ] && [ "$collected" -gt "$ceiling" ]; then
    # The OTHER direction, and the one the old single literal kept failing at
    # silently: a floor so far below the real count that the collapse it exists
    # for would fit underneath it. 5638 once stood against a real 6545.
    echo "run-tests: ERROR — $d collected $collected tests but its floor is only $floor." >&2
    echo "  That is more than $drift of slack: a whole suite could vanish under this" >&2
    echo "  floor with the gate still green. Raise the TARGET_FLOORS entry to" >&2
    echo "  \"$d|$(_suggested_floor "$collected")\" — that number is this run's own count put through" >&2
    echo "  the documented rule, so it needs no measurement of its own." >&2
    RESULTS+=("FAIL  $d  (collected=$collected above drift ceiling $ceiling, floor $floor)")
    bad=1
  fi

  if [ "$rc" -ne 0 ] || [ "$f" -gt 0 ] || [ "$e" -gt 0 ]; then
    RESULTS+=("FAIL  $d  (collected=$collected passed=$p skipped=$s failed=$f errors=$e)")
    bad=1
  elif [ "$bad" -eq 0 ]; then
    RESULTS+=("PASS  $d  (collected=$collected passed=$p skipped=$s floor=$floor)")
  fi

  rm -f "$log"
  echo
  [ "$bad" -eq 0 ] || fail=1
  return "$bad"
}

# 🔴 `|| fail=1` as well as the global the function sets. `run_pytest`'s last
# statement used to be a bare `echo`, so the function's own status was always 0
# and the ONLY thing carrying a failure out was the global — one refactor away
# from a silently green gate. Both mechanisms now agree, and the function ends
# with the `return` rather than with output.
for d in "${TARGETS[@]}"; do
  run_pytest "$d" || fail=1
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
  nl_before="$(_nolaunch_lines)"
  if python "$HOOK_TEST"; then
    RESULTS+=("PASS  $HOOK_TEST (script)")
  else
    RESULTS+=("FAIL  $HOOK_TEST (script)")
    fail=1
  fi
  # These are NOT pytest, so they load no plugin: their only protection is the
  # stub dir this script put first on PATH. They are accounted the same way, and
  # get no session marker — GUARD 7 requires a marker only from pytest targets.
  NOLAUNCH_SEEN+=("$HOOK_TEST|$(( nl_before + 1 ))|$(_nolaunch_lines)")
  echo
done

# --- SHELL targets -------------------------------------------------------------
# Bash test scripts. `test_release_wrapper.sh` had been run by NO gate at all
# since it was written (its own header says `run: bash scripts/tests/
# test_release_wrapper.sh`, by hand), while creating fake `notify-send`/`git`/
# `npm` binaries on PATH and driving the real `_release_run` out of `.zshrc`.
# Nothing forced its stub-prepend to keep inheriting PATH, and nothing ran it.
# It is here so the guard above covers it too — the stub dir is on PATH for this
# whole script, so a stub that stopped shadowing lands in the launch log instead
# of on the operator's desktop.
SHELL_TESTS=(
  "scripts/tests/test_release_wrapper.sh"
)
for SHELL_TEST in "${SHELL_TESTS[@]}"; do
  if [ ! -f "$SHELL_TEST" ]; then
    echo "run-tests: ERROR — shell test '$SHELL_TEST' does not exist (typo, or moved?)." >&2
    RESULTS+=("FAIL  $SHELL_TEST (missing)")
    fail=1
    continue
  fi
  echo "=== script $SHELL_TEST ==="
  nl_before="$(_nolaunch_lines)"
  if bash "$SHELL_TEST"; then
    RESULTS+=("PASS  $SHELL_TEST (script)")
  else
    RESULTS+=("FAIL  $SHELL_TEST (script)")
    fail=1
  fi
  NOLAUNCH_SEEN+=("$SHELL_TEST|$(( nl_before + 1 ))|$(_nolaunch_lines)")
  echo
done

echo "======================== SUMMARY ($SET set) ========================"
for r in "${RESULTS[@]}"; do echo "  $r"; done
echo "  ----"
echo "  TOTAL collected=$TOT_COLLECTED  passed=$TOT_PASSED  skipped=$TOT_SKIPPED  failed=$TOT_FAILED  (floor: $MIN_TESTS = sum of ${#TARGETS[@]} per-target floors)"

# --- GUARD 3 (global): collected-test floor ------------------------------------
# The per-target floors above are the load-bearing check; this total is their
# SUM, so it is mostly implied by them. It is kept because it is not ENTIRELY
# implied — a target that is skipped altogether (not run, so it contributes no
# per-target verdict) still moves this number.
if [ "$TOT_COLLECTED" -lt "$MIN_TESTS" ]; then
  echo "  ERROR: only $TOT_COLLECTED tests were collected, floor is $MIN_TESTS." >&2
  echo "         Far fewer tests ran than exist — a VACUOUS GREEN, not a pass." >&2
  echo "         This total is the SUM of the per-target floors, so the per-target" >&2
  echo "         line(s) above name which suite shrank. Fix there, not here." >&2
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

# --- GUARD 7 (evaluation): per-target launcher accounting ----------------------
# One line per target, ALWAYS printed — including the zeros. A bare "0 real
# launches" from a counter nobody has watched move is indistinguishable from a
# counter wired to nothing, so the acknowledged target's NON-zero count is
# printed beside every other target's zero, as a pair.
echo "  ---- launcher intercepts (GUARD 7) ----"
nolaunch_problems=()
for t in "${TARGETS[@]}"; do
  seen=0
  for entry in "${NOLAUNCH_SEEN[@]}"; do
    [ "${entry%%|*}" = "$t" ] && seen=1 && break
  done
  [ "$seen" -eq 1 ] || nolaunch_problems+=("$t  — never accounted: run_pytest returned before GUARD 7 could measure it")
done

for entry in "${NOLAUNCH_SEEN[@]}"; do
  nt="${entry%%|*}"
  rest="${entry#*|}"
  nfrom="${rest%%|*}"
  nto="${rest#*|}"
  slice="$(_nolaunch_slice "$nfrom" "$nto")"
  markers=$(printf '%s\n' "$slice" | grep -c '^nolaunch(session)' || true)
  reads=$(printf '%s\n' "$slice" | grep -c '^systemctl(read)' || true)
  hits="$(printf '%s\n' "$slice" | grep -vE '^(nolaunch\(session\)|systemctl\(read\)|$)' || true)"
  nhits=0
  [ -n "$hits" ] && nhits=$(printf '%s\n' "$hits" | grep -c . || true)

  is_pytest=0
  for t in "${TARGETS[@]}"; do [ "$t" = "$nt" ] && is_pytest=1 && break; done

  if ack="$(_nolaunch_ack_reason "$nt")"; then
    echo "    $nt  intercepted=$nhits (ACKNOWLEDGED)  systemctl-reads=$reads  plugin=$markers"
    # 🔴 The REQUIRED direction: an acknowledged target that intercepts nothing
    # means the guard is wired to nothing. A ledger that only PERMITTED
    # intercepts would stay green under the one mutant that matters — the guard
    # silently covering zero targets.
    if [ "$nhits" -lt 1 ]; then
      nolaunch_problems+=("$nt  — acknowledged as a target that DRIVES launchers into the stub, but it intercepted NOTHING. Either the guard stopped being installed, or those seam tests stopped running. Reason on file: $ack")
    fi
  else
    echo "    $nt  intercepted=$nhits  systemctl-reads=$reads  plugin=$markers"
    if [ "$nhits" -gt 0 ]; then
      nolaunch_problems+=("$nt  — reached $nhits REAL host launcher(s). They were intercepted and did NOT run; a test in this target tried to touch the operator's machine:"$'\n'"$(printf '%s\n' "$hits" | sed 's/^/           /')")
    fi
  fi

  if [ "$is_pytest" -eq 1 ] && [ "$markers" -ne 1 ]; then
    nolaunch_problems+=("$nt  — the nolaunch plugin emitted $markers session marker(s), expected exactly 1. This target ran WITHOUT the guard, so its zero above means nothing (see GUARD 7's header: -p testlib.nolaunch_plugin on the pytest line).")
  fi
done

if [ "${#nolaunch_problems[@]}" -gt 0 ]; then
  echo "  ERROR: ${#nolaunch_problems[@]} GUARD 7 problem(s):" >&2
  for p in "${nolaunch_problems[@]}"; do echo "         $p" >&2; done
  fail=1
fi
rm -rf "$NOLAUNCH_DIR"

# GUARD 6. One writer, fed the same value `exit` is about to take, so the
# printed verdict and the process status cannot disagree — including through a
# pipe, which is what destroys the status in practice.
_emit_verdict "$fail"
exit "$fail"
