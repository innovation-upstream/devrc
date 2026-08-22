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
#      ⚠ CORRECTED 2026-08-22 — both halves of what this said are now false.
#      The pre-push tier used to supply only python via `nix-shell -p …` and take
#      the rest from the ambient PATH; it now runs `nix develop`, so the devShell
#      supplies EVERY entry from the same flake.nix `gateTools` list the flake
#      check uses. And a host missing one no longer BLOCKS: a missing tool is an
#      ENVIRONMENT precondition, which exits 3, which githooks/tests-on-push.sh
#      DEGRADES on — blocking a push over a broken caller is what teaches people
#      to reach for `DEVRC_SKIP_TESTS=1`. Repo-content guards still exit 2 and
#      still block.
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
# a `set -u` unbound variable or any of the early `exit 3` environment preconditions now
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
cd "$ROOT" || { echo "run-tests: cannot cd to ROOT=$ROOT" >&2; exit 3; }
# 🔴 EXIT CODES: 3 = the ENVIRONMENT could not satisfy a precondition (this guard,
# 1b, 1c). 2 = a REPO-CONTENT guard refused (target list, floor table, launcher
# stubs, spool wiring). The distinction is load-bearing: `githooks/tests-on-push.sh`
# DEGRADES on 3 (an env fault must not block a push over a broken caller) and
# BLOCKS on 2. Collapsing them was a measured mistake — degrading on 2 turned
# GUARD 5's and GUARD 3a's own warning ("do NOT delete the entry to make this
# pass — that is how a suite stops running while the gate goes green") into
# exactly that outcome, on the only tier that runs automatically: this repo has
# no CI and no branch protection (pinned by test_ci_claim_matches_reality.py).
# A new `exit` here must pick a side deliberately.
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
#   rsync   scripts/tests/test_subsystem_store_api.py, which drives the real
#           scripts/subsystem-store-api/seed.sh — its copy step is
#           `rsync -a --delete "$STORE"/ "$STAGE"/`. Without the binary those
#           tests fail with rc 127 rather than skipping, deliberately: the
#           property they pin is that seeding never writes to the local store,
#           and that store is the only copy of client-confidential content.
# logrotate: scripts/tests/test_claude_log_rotate.py drives the REAL binary
# against a temp directory (rotation, truncation, generation cap, and the .bak
# scope fence). Those tests FAIL rather than skip when it is absent — a skipped
# rotation test reports safety it never measured — so it belongs here.
#
# zsh:      scripts/tests/test_run3.py (2 tests). This is the sharpest case in
#           the list, because the RULE under test is a zsh-vs-bash difference:
#           zsh's MULTIOS makes `cmd 2>&1 >/dev/null | c` hand the consumer
#           STDOUT where bash hands it nothing. A tier with no zsh is
#           STRUCTURALLY blind to that whole class — the tests would skip and
#           the gate would go green having measured the one shell the defect
#           cannot occur in. Both hosts run zsh as the login shell, and
#           flake.nix's `gateTools` carries it for the sandbox.
#
# 🔴 `python` is listed as well as `python3` because THIS SCRIPT invokes
# `python -m pytest`, not `python3`. Asserting only `python3` checked a binary
# the runner never calls.
REQUIRED_TOOLS=(bash curl node rg git awk jq grep setsid python python3 nix-instantiate opencode logrotate rsync zsh)
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
  echo "  testing less. This is a MISSING ENVIRONMENT, not a code failure — nothing" >&2
  echo "  in the repo is broken and no test has run yet." >&2
  echo >&2
  echo "  FIX — enter the repo's own dev shell, which carries exactly this list," >&2
  echo "  and re-run from there:" >&2
  echo >&2
  echo "      nix develop \"$ROOT\" --command bash \"$ROOT/scripts/run-tests.sh\" \"$ROOT\"" >&2
  echo >&2
  echo "  (or \`nix develop\` once, then \`bash scripts/run-tests.sh .\` as often as" >&2
  echo "  you like). That shell is built from the SAME flake.nix \`gateTools\` list" >&2
  echo "  as the \`nix flake check\` gate, so it cannot drift out of satisfying this" >&2
  echo "  precondition." >&2
  echo >&2
  echo "  Do NOT drop entries from REQUIRED_TOOLS to make this pass — each one is" >&2
  echo "  justified in the comment block directly above it." >&2
  exit 3
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
    echo "  Fix the caller: use the repo's own devShell, which carries pytest and" >&2
    echo "  every other suite dep from the same flake.nix \`gateTools\` list the" >&2
    echo "  \`nix flake check\` gate uses:" >&2
    echo "      nix develop \"$ROOT\" --command bash \"$ROOT/scripts/run-tests.sh\" \"$ROOT\"" >&2
    exit 3
  fi

  # --- GUARD 1c: the interpreter must carry EVERY dep the suites import --------
  # 🔴 GUARD 1b asks "is pytest importable". Necessary, NOT sufficient: an
  # interpreter can have pytest and still lack the deps the suites import at
  # COLLECTION time, and the run then fails in a shape that reads as a BROKEN
  # BRANCH rather than a broken caller.
  #
  # MEASURED 2026-08-21, running this suite with a cwd inside an UNRELATED repo:
  # `python` resolved to that repo's `.venv`, pytest imported fine, and the run
  # produced
  #     FAIL scripts/mail-actions/tests   (collected 0 tests)
  #     FAIL scripts/signal/tests         (collected=2 below floor 553, errors=2)
  #     FAIL scripts/initiatives/tests    (collected=784 ... failed=9)
  # — 13 failures, every one an artifact of missing deps. The only tell was a
  # traceback path naming the other repo's site-packages; it took four attempts to
  # find, and the intermediate readings were confidently wrong.
  #
  # 🔴 CHECK THE PROPERTY, NOT A PROXY FOR IT. The first version of this guard
  # refused a VIRTUALENV (`sys.prefix != sys.base_prefix`), which is wrong in BOTH
  # directions and was measured so: a nix `withPackages` env missing psycopg2 and
  # minio — the exact shape above — PASSED it silently, while a
  # `venv --system-site-packages` with every dep importable was REFUSED. Importing
  # the deps costs the same, catches every wrong-interpreter shape, and needs no
  # escape hatch: if they import, the run is sound by definition.
  #
  # 🔴 ACTUALLY IMPORT THEM. `find_spec` only resolves the module — it returns a
  # spec for a package whose C extension is broken (psycopg2 against a mismatched
  # libpq) or for an empty directory on the path, both of which then ImportError
  # at COLLECTION time, which is the failure this guard exists to pre-empt. The
  # first version used find_spec and its own text claimed "if they import"; the
  # two were not the same check.
  #
  # 🔴 FAIL CLOSED on an unreadable probe. Anything the interpreter prints ahead
  # of this output (a `sitecustomize.py` on PYTHONPATH will do it) shifts the
  # parse, and a guard that then passes silently is worse than none — GUARD 4
  # already fails the run when pytest's summary is unparseable; this matches it.
  _dep_probe="$(python -c '
import sys
need = ("pytest", "requests", "psycopg2", "minio", "yaml")
missing = []
for m in need:
    try:
        __import__(m)
    except Exception:
        missing.append(m)
print("DEPS:" + (",".join(missing) if missing else "OK"))
print("EXE:" + sys.executable)
' 2>/dev/null)"
  _dep_line="$(printf '%s\n' "$_dep_probe" | grep -a '^DEPS:' | tail -1)"
  _exe_line="$(printf '%s\n' "$_dep_probe" | grep -a '^EXE:' | tail -1)"
  if [ -z "$_dep_line" ] || [ -z "$_exe_line" ]; then
    echo "run-tests: FATAL — could not read the interpreter dependency probe." >&2
    echo "  Expected a DEPS: and an EXE: line; got:" >&2
    printf '%s\n' "$_dep_probe" | sed 's/^/    /' >&2
    echo "  Refusing rather than guessing — an unreadable precondition is not a" >&2
    echo "  satisfied one." >&2
    exit 3
  fi
  # 🔴 ALWAYS print the resolved interpreter, including on the happy path. A green
  # whose environment you cannot see is what made this expensive: the one fact
  # that would have ended it in a single step was never in the log.
  echo "run-tests: interpreter ${_exe_line#EXE:}" >&2
  if [ "$_dep_line" != "DEPS:OK" ]; then
    echo "run-tests: FATAL — the interpreter is missing suite dependencies." >&2
    echo "  missing : ${_dep_line#DEPS:}" >&2
    echo "  python  : ${_exe_line#EXE:}" >&2
    echo "  pytest imports, so GUARD 1b passed — but the suites import these at" >&2
    echo "  COLLECTION time, so the run would report collection errors and floor" >&2
    echo "  misses that read as a broken BRANCH rather than a broken CALLER." >&2
    echo "  A cwd inside another repo supplies ITS interpreter, and \`env -u" >&2
    echo "  VIRTUAL_ENV\` is not enough — \`nix-shell --run\` executes the shell" >&2
    echo "  hooks that re-activate it. Use the repo's own devShell, which is" >&2
    echo "  immune (measured) and needs no hand-copied dependency list:" >&2
    echo "    nix develop $ROOT --command bash $ROOT/scripts/run-tests.sh --set all $ROOT" >&2
    exit 3
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
  # Added 2026-08-16 with the Signal chat pipeline. Hermetic by construction:
  # the DB layer runs against an in-memory sqlite substrate translated from the
  # module's OWN `SCHEMA_STATEMENTS` (tests/fakepg.py), MinIO and the Signal API
  # are injected fakes, and conftest.py fails any test that reaches for the real
  # `requests` — so no Postgres, no MinIO, no network.
  scripts/signal/tests
  scripts/initiatives/tests
  scripts/repo-cos/tests
  scripts/task-spec-drafter/tests
  # A FILE, not a dir, and deliberately so: scripts/claude-hooks/tests/ also
  # holds hand-rolled scripts that call main() at import and sys.exit(), which
  # pytest cannot collect. Naming the one pytest-collectable file keeps them
  # apart. (test_guard_core.py covers the shared guard core behind BOTH
  # bash-guard.py and opencode's plugin/guard.js.)
  scripts/claude-hooks/tests/test_guard_core.py
  # Same reason as the line above: a FILE, because its directory neighbours are
  # hand-rolled scripts pytest cannot collect. next-step-nudge is the Stop hook that
  # asks for a next step; it BLOCKS the operator's turn when it fires, so its
  # suppression predicate is gated here rather than left to the ungated hand-rolled
  # scripts beside it.
  scripts/claude-hooks/tests/test_next_step_nudge.py
  # Same reason again — a FILE, not the directory. This one gates the DELIVERY seam
  # rather than a hook's own logic: that register-nudge-hook.py is deployed, that a
  # switch actually RUNS it, and that the run leaves the right end state in a
  # throwaway settings.json without ever clobbering a foreign Stop hook. It exists
  # because #452's hook shipped to both hosts and sat inert — every component was
  # tested, the seam between them was owned by nobody.
  scripts/claude-hooks/tests/test_registrar_activation.py
  # Same reason again — a FILE, not the directory. Writer 1 of the agent activity
  # ledger. It fires on PostToolUse, i.e. after EVERY tool call the operator's
  # session makes, so its fail-open contract (exit 0, nothing on stdout or
  # stderr, on every malformed input) is felt on every turn and is gated here
  # rather than left to the ungated hand-rolled scripts beside it.
  scripts/claude-hooks/tests/test_agent_ledger_hook.py
  # Same reason again — a FILE, not the directory. The clawgate write-back guard is
  # the only hook in this repo that can BLOCK a turn, and it fires on BOTH the
  # per-tool-call hot path and Stop, so its trigger's non-matches, its
  # no-work-after-read false-positive killer, its never-block-when-unmeasurable
  # contract and its exit-0-on-anything backstop are all gated here rather than left
  # to the ungated hand-rolled scripts beside it.
  scripts/claude-hooks/tests/test_clawgate_writeback_guard.py
  # Same reason again — a FILE, not the directory. The write-back guard's
  # counterpart at the OTHER end of a task's life: this one denies a `task create`
  # whose body carries no `## Acceptance criteria`, and — deliberately — one whose
  # body it cannot read at all. It is the second hook in this repo that can BLOCK,
  # it fires PreToolUse on EVERY Bash call, and its non-matches (`task ls`,
  # `task get`, `task comment`, a curl to `/api/tags`, a producer launcher) are the
  # load-bearing half, so they are gated here rather than left to the ungated
  # hand-rolled scripts beside it.
  scripts/claude-hooks/tests/test_clawgate_task_interview_guard.py
  # Same reason again — a FILE, not the directory. This one gates a CROSS-MODULE
  # class rather than any single hook: the on-disk names every hook's cache is made
  # of. Fifteen of them could be renamed with zero test movement, because writer and
  # reader in each module share one function, so a rename is self-consistent and
  # nothing behavioural can see it — while every in-flight session's state is
  # orphaned by the switch that deploys it. Gated here because it is the only test
  # that asserts a property ACROSS the hook modules, so no per-hook target owns it.
  scripts/claude-hooks/tests/test_on_disk_artifact_names.py
  # Same reason again — a FILE, not the directory. The backgrounded-command
  # capture log (ClickUp 868ktvqf9). It fires PreToolUse AND PostToolUse on every
  # Bash call, so its fail-open contract is felt on every command the operator
  # runs: 21 hostile inputs each asserting exit 0 with an empty stdout AND an
  # empty stderr, plus the negative control proving that battery can go red. The
  # marker scanner's NON-matches are the load-bearing half (14 of 28 rows expect
  # no marker), because a scanner that marks everything would satisfy a one-way
  # test while turning the 16 MiB bound into hours of history instead of months.
  scripts/claude-hooks/tests/test_bg_command_capture.py
  # Writer 2 of the agent activity ledger — the OPENCODE half. A Python suite
  # driving real `node`, mirroring scripts/collector/opencode/tests/test_plugin.py
  # (this repo's established way to test an opencode plugin; there is no node
  # suite for scripts/opencode/plugin/ and this does not add one). It gates the
  # SEAM that matters: the record shape is Python and the writer is JavaScript,
  # so "the plugin ran" and "the record is one session-manager can read" are
  # different claims.
  scripts/opencode/tests
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
  #
  # 2026-08-13, the session LABEL/HOTKEY resolver + the tmux session-created
  # autoname hook: 3704 -> 3774 collected on the merged tree.
  #
  # 🔴 THE DELTA IS DECOMPOSED, and the CONTROL was measured first — the entry
  # above implies main should have been 3478, and it was 3704, i.e. main was
  # carrying 226 tests this table had never seen. Subtracting from the RECORDED
  # number would have claimed +296 for this branch and a floor ~226 too high, a
  # FALSE RED for whoever lands next.
  #     3704  origin/main at a8f8426, measured FIRST by the authoritative gate
  #           as the control
  #     + 27  scripts/tests/test_session_manager.py, 391 -> 418 (the three label
  #           tiers, the hotkey seam against the slot table, render_label, the
  #           column trade, and the two-sessions-one-label tie)
  #     + 44  scripts/tests/test_tmux_autoname_session.py (NEW FILE)
  #     ----
  #     3775  the merged tree, which is what the gate printed
  #   _suggested_floor 3775 = 3775 - min(50, max(1, 3775/20 = 188)) = 3775 - 50
  #                         = 3725.
  # Two independent attributions agree: the same delta was measured against the
  # branch's ORIGINAL base (6eaeb61) too, where the gate printed 3774 at 43
  # cases in the new file — #446/#448 landed in between and added no tests,
  # which is what makes the two readings agree rather than a coincidence to
  # explain away. The 44th case (a MISSING slot table, `set -u` + unset array)
  # was added after that reading and re-measured here.
  #
  # ⚠ ZERO new skips (`skipped=0` on this target; the suite's single skip is
  # still the pinned one in repo-cos). A NEW FILE was added in BOTH senses —
  # `scripts/tmux-autoname-session.sh` and its test — and both are `git add`ed;
  # `test_the_script_is_tracked_by_git` fails if the script ever is not, in the
  # sandbox by its ABSENCE and on the host via `git ls-files`.
  #
  # 2026-08-13, the /handoff doc write+push gate (`scripts/lib/handoff_doc.py`):
  # 3867 -> 3915 collected.
  #
  # 🔴 THE CONTROL WAS MEASURED FIRST, and it is what makes the delta a fact
  # about this branch. The entry above implies main should have been 3775; the
  # authoritative gate run on UNMODIFIED origin/main at bab2012 — rev-pinned
  # (`nix build 'git+file://<repo>?rev=bab2012…#checks.x86_64-linux.pytests'
  # --rebuild`, because a cache hit prints an EMPTY log and `nix log` then has
  # nothing to read) — printed `collected=3867`. So main was carrying 92 tests
  # this table had never seen, and subtracting from the RECORDED number would
  # have claimed +140 for this branch and a floor ~92 too high: a FALSE RED for
  # whoever lands next.
  #     3867  origin/main at bab2012, measured FIRST by the authoritative gate
  #           as the control
  #     + 48  scripts/tests/test_handoff_doc.py (NEW FILE) — the both-directions
  #           gate (decline hashes the whole repo tree; accept compares the diff
  #           SHOWN against the one `git show` reports for the commit), the
  #           append-vs-replace fixture with two prior findings, the no-advance
  #           and no-change refusals, four instrument controls, and the
  #           skill/module pins
  #     ----
  #     3915  which is what the gate printed
  #   _suggested_floor 3915 = 3915 - min(50, max(1, 3915/20 = 195)) = 3915 - 50
  #                         = 3865.
  # Two independent attributions agree exactly: the gate's own total (3915) and
  # per-file collection on the new file (`--collect-only` = 48) against the
  # measured control. Had they disagreed, something else in the target had moved
  # and the +48 would have been a fact about a branch that never produced it.
  #
  # ⚠ ZERO new skips (`skipped=0` on this target; the suite's single skip is
  # still the pinned one in repo-cos). NEW FILES were added — the module, its
  # test, and two `claude/skills/handoff/reference/*.md` — and all are `git
  # add`ed; `test_the_tool_is_tracked_by_git` fails if the module ever is not,
  # in the sandbox by its ABSENCE and on the host via `git ls-files`.
  #
  # ⚠ origin/main moved to ed35850 (#462) while this was in flight, and that PR
  # CUT a guard — so the merged tree's count will differ in BOTH directions.
  # Re-run the gate on the merged tree and copy what it prints; do NOT reconcile
  # the two sides by hand. (rerere has replayed a stale resolution onto this
  # line before.)
  #
  # 2026-08-13, done exactly as the note above says: main moved on to d01cf23
  # (#462 cut a guard, #465 added a whole new target), so the merged tree was
  # measured rather than reconciled by hand — the gate printed
  # `scripts/tests collected=3932`, not the 3915 this branch alone measured.
  #   _suggested_floor 3932 = 3932 - min(50, max(1, 196)) = 3882.
  #
  # 2026-08-13, the agent activity ledger (spec #428): +79 on this target —
  # 40 in the NEW `scripts/tests/test_agent_ledger.py` and 39 added to
  # `test_session_manager.py` (the §9 ledger section, 418 -> 439). Includes the
  # audit round: the co-tenancy guard, the two clock-skew clamps, and the
  # caveat guard re-run with the ledger ON (it had gone structurally blind).
  # Plus the delta re-audit round: the failed-tmux-lookup regression guard, the
  # conflict->report->render seam (three tests, mirroring the ones fuzzyclaw's
  # identically-shaped path already had), and the two branches of the
  # "host answered neither tmux call" skip.
  # Gate's own count on this branch: `scripts/tests collected=4002`
  # (confirmed by `PASS … collected=4002 … floor=3970`).
  #   _suggested_floor 4002 = 4002 - min(50, max(1, 200)) = 3952.
  # Pinned at 3970 rather than 3952 — a floor the gate ACCEPTS and which sits
  # closer to the measurement, so a collapse is caught sooner. Do not reconcile
  # this by arithmetic against another branch: re-run the gate on the merged
  # tree and copy what it prints.
  # ⚠ ZERO new skips on this target. TWO NEW FILES land under it (the module
  # `scripts/lib/agent_ledger.py` and its test) plus the hook and its own
  # target — all `git add`ed, which this repo's flake requires or the deploy
  # silently omits them.
  # ⚠ If main moves before this merges, re-run the gate on the MERGED tree and
  # copy what it prints. Do not reconcile the two sides by arithmetic.
  #
  # 2026-08-17, the captured-text gate's coverage round (#521): 3970 was still
  # sitting under a 5026-test target — 1056 of SLACK against a drift ceiling of
  # 4962, so the gate went RED on the ceiling check, which is what this line is
  # for. Not computed and not reconciled against another branch: the gate was
  # run on the MERGED tree (this branch + origin/main at 5565c33) and printed
  #
  #   run-tests: ERROR — scripts/tests collected 5026 tests but its floor is
  #   only 3970. … Raise the TARGET_FLOORS entry to "scripts/tests|4976"
  #
  # and 4976 is copied verbatim from that line. The same run: TOTAL
  # collected=11493 passed=11492 skipped=1 failed=0, every other target PASS —
  # this floor was the ONLY red.
  # ⚠ ZERO new skips on this target. NO new file lands under it (the gate's two
  # files already existed on this branch); the +80 tests are all inside
  # scripts/tests/test_no_captured_text.py.
  #
  # 2026-08-19, the waiting-windows report (`scripts/waiting-windows`): 5026 ->
  # 5394 collected, +91 of which are the new
  # scripts/tests/test_waiting_windows.py and the rest arrived with main since
  # the line above was written. `scripts/tests` is already a DIRECTORY target,
  # so the new file needed no HERMETIC_TARGETS entry — only this floor moves.
  #
  # 🔴 MEASURED ON THE MERGED TREE, not on the branch alone. The branch-only run
  # said collected=5358; merging origin/main (72d3ea8, which grows
  # test_session_manager.py, test_prune_skill_size.py and test_obs_read.py)
  # moved it to 5394. Pinning 5358 would have been a number from a tree nobody
  # will ever run. The gate's own function on the merged measurement:
  #
  #   scripts/tests  (collected=5394 …)
  #   _suggested_floor 5394 = 5394 - min(50, max(1, 269)) = 5394 - 50 = 5344
  #
  # Re-measured again after the harness-session-registry clock landed on this
  # same branch (+39 tests in test_waiting_windows.py, 91 -> 130):
  #
  #   scripts/tests  (collected=5433 passed=5432 …)
  #   _suggested_floor 5433 = 5433 - min(50, max(1, 271)) = 5433 - 50 = 5383
  #
  # And once more after the three-state harness-presence split (+14 tests,
  # 130 -> 144). Measured on this tree, not computed from the line above:
  #
  #   scripts/tests  (collected=5447)
  #   _suggested_floor 5447 = 5447 - min(50, max(1, 272)) = 5447 - 50 = 5397
  #
  # 🔴 AND ONCE MORE, ON THE MERGED TREE. Merging origin/main (ecddbae, which
  # adds scripts/tests/test_service_recon.py and grows two more suites under
  # this target) merged CLEANLY in git and left 5397 sitting there — a number
  # measured against a tree that no longer exists. That is the semantic
  # conflict a clean merge does not raise: the line did not conflict, the
  # MEANING of the line did. Re-measured on the merged tree:
  #
  #   scripts/tests  (collected=5532)
  #   _suggested_floor 5532 = 5532 - min(50, max(1, 276)) = 5532 - 50 = 5482
  #
  # ⚠ A CONCURRENT BRANCH TOUCHES THIS SAME LINE (`scripts/session-resolve`,
  # adding scripts/tests/test_session_resolve.py under this same directory
  # target), so it WILL conflict. Resolve it the way the header says: re-run
  # the gate on the MERGED tree and copy the number it prints. Do NOT add the
  # two branches' deltas together — that is exactly the arithmetic-across-a-
  # conflict that made the old single literal take eleven values in one day.
  #
  # ⚠ ZERO new skips on this target — the new suite is fully hermetic (every
  # impure source injected; the only disk it touches is a pytest `tmp_path`
  # `$HOME` for the on-disk artifact-name pin), so EXPECTED_SKIPS is untouched.
  # Movement on THIS line is the evidence the gate runs the new file at all.
  # 2026-08-19, the session identity resolver (scripts/session-resolve): 5303 ->
  # 5405 collected, +102 for scripts/tests/test_session_resolve.py. The target
  # needs no HERMETIC_TARGETS entry — scripts/tests is already a directory
  # target — so this ONE number is the whole registration. `_suggested_floor
  # 5405` = 5405 - min(50, max(1, 270)) = 5355, the gate's own function applied
  # to its own count, never arithmetic on two sides of a conflict.
  # ⚠ ZERO new skips. ⚠ CONFLICT EXPECTED: a concurrent branch adds
  # scripts/tests/test_waiting_windows.py under this same target, so this line
  # will need a test-merge — re-run the gate on the MERGED tree and copy the
  # number IT prints rather than adding the two branches' deltas together.
  #
  # 🔴 AND ONCE MORE, ON THE TREE THIS MERGE CREATES. The two branches above
  # both edited THIS line from the same 4976 base — 5482 (waiting-windows)
  # and 5355 (session-resolve) — so NEITHER number describes the tree that
  # now exists, and adding their deltas is precisely the arithmetic-across-a-
  # conflict this header forbids. Re-measured by the gate on the MERGED tree:
  #
  #   scripts/tests  (collected=5722)
  #   _suggested_floor 5722 = 5722 - min(50, max(1, 286)) = 5672
  #
  # 2026-08-19, the waiting-windows kind-band ordering + --kind filter folded
  # into this same branch (see the PR body: it is a fix to a script this branch
  # does not own, landed here to avoid a THIRD conflict on this very line):
  # 5722 -> 5741 collected, +19 for the ordering/filter section of
  # scripts/tests/test_waiting_windows.py.
  #
  #   _suggested_floor 5741 = 5741 - min(50, max(1, 287)) = 5741 - 50 = 5691
  #
  #
  # 2026-08-19, the #558 audit fix round (the --host default that hid a whole
  # host, the hermeticity hole, and main() coverage for both scripts):
  # 5741 -> 5780 collected, +39 across test_session_resolve.py and
  # test_waiting_windows.py.
  #
  #   _suggested_floor 5780 = 5780 - min(50, max(1, 289)) = 5780 - 50 = 5730
  #
  #
  # 2026-08-19, the #558 delta-audit fix round (the per-host status that
  # replaced a positive false claim, the state-file test that could not fail,
  # the welded tmux separator, and the consolidated hosts_not_covered):
  # 5780 -> 5796 collected, +16.
  #
  #   _suggested_floor 5796 = 5796 - min(50, max(1, 289)) = 5796 - 50 = 5746
  #
  #
  # 2026-08-19, the #558 round-3 fix (the host-disclosure MODEL and its
  # 144-cell matrix, replacing a hand-written status): 5796 -> 5810 collected,
  # +14 net -- the matrix replaced several targeted tests rather than adding to
  # them.
  #
  #   _suggested_floor 5810 = 5810 - min(50, max(1, 290)) = 5810 - 50 = 5760
  #
  #
  # 2026-08-19, the #558 round-4 fix (the partial-view caveat keyed on
  # MEASURABLE rather than ANSWERED, plus the vacuous single-host test):
  # 5810 -> 5814 collected, +4.
  #
  #   _suggested_floor 5814 = 5814 - min(50, max(1, 290)) = 5814 - 50 = 5764
  #
  #
  # 2026-08-20, the gate-harness DX fix (the hook-tests directory that reported
  # "no tests ran" over an INTERNALERROR, and the missing-tool FATAL that read
  # as a broken gate), re-measured after a rebase onto fc1f581:
  # 6201 -> 6219 collected, +18 across test_hook_tests_dir_collects.py and
  # test_devshell_satisfies_required_tools.py.
  #
  # BOTH numbers MEASURED with `--collect-only` on scripts/tests, one in a clean
  # detached worktree of origin/main and one on the rebased branch — not
  # inferred, and not carried over from the earlier round of this same entry
  # (which read 5947 -> 5962 against a29b97b and is now superseded: main moved
  # four times while this branch was in review).
  #
  # 🔴 The pin this REPLACES was 5912, against a real count of 6201 on main
  # alone — 289 of drift, none of it from here. The same thing happened to the
  # entry before it (5764 pinned against 5947). Tests keep landing without the
  # floor being re-pinned, so this table's sensitivity decays continuously
  # between the contributions that happen to notice. Re-measure before trusting
  # a number here; do not assume the last entry describes the current tree.
  #
  #   _suggested_floor 6219 = 6219 - min(50, max(1, 310)) = 6219 - 50 = 6169
  #
  # ⚠ ZERO new skips from any contribution.
  #
  # 2026-08-21, `scripts/run3` + scripts/tests/test_run3.py (the stream-capture
  # helper): 6483 -> 6509 collected, +26. BOTH numbers MEASURED with
  # `--collect-only` — 6483 in a clean detached worktree of origin/main, 6509 on
  # this branch, re-measured AFTER a rebase onto 6070161e — not inferred from
  # the pin, and not from the pre-rebase base (main moved by 3 commits mid-work
  # and #653 alone added 3 tests, so the pre-rebase pair would have been wrong
  # by exactly that).
  #
  # 🔴 The pin this REPLACES was 6169 against a real 6483 on main alone: 314 of
  # drift, none of it from here, exactly as the paragraph above predicted it
  # would keep happening. Re-measure; do not trust this number either.
  #
  #   _suggested_floor 6509 = 6509 - min(50, max(1, 325)) = 6509 - 50 = 6459
  #
  # ⚠ ZERO new skips. No HERMETIC_TARGETS entry needed — scripts/tests is
  # already a directory target, so movement on THIS line is the evidence the
  # gate runs the new file at all. test_run3.py's two zsh tests carry a
  # `skipif` for a bare `pytest scripts/tests`, but under THIS runner they can
  # never fire: `zsh` is in REQUIRED_TOOLS, so a host without it aborts on
  # GUARD 1 naming the binary rather than running two tests thinner. That is
  # why they are not pinned in EXPECTED_SKIPS.
  "scripts/tests|6459"
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
  # 2026-08-19, the DRIFT CEILING fired and #570 re-pinned this line to 622 as
  # part of the opencode-pin fix, landing the VALUE with no accounting. This is
  # that accounting. The gate had reported: 654 collected against a floor of
  # only 469 — 185 above the FLOOR and 68 above the ceiling of
  # 469 + max(60, 469/4) = 469 + 117 = 586. That is the ceiling doing its job,
  # not a suite defect: every one of the 654 passes.
  #
  #   _suggested_floor 654 = 654 - min(50, max(1, 654/20)) = 654 - 32 = 622
  #
  # WHY IT DRIFTED: 469 was set at #397 (e1219d4), when the suite collected
  # exactly 493. It grew 161 tests across the emulation, context/annotated-read,
  # surface-parity, session-id and SKILL.md-size work — most recently #562's
  # per-site `site_notes` routing and its ledger test — and was never re-pinned
  # on the way, so the gap widened one PR at a time. At 469 a collapse of a
  # QUARTER of this suite would have fitted under the floor and reported green.
  # A floor that has fallen far behind is not a weak guard; it is an absent one
  # that still prints a number.
  #
  # ⚠ ZERO new skips on this target (collected=654 passed=654 skipped=0 at the
  #   time of this measurement).
  #
  # 2026-08-20 FOLLOW-UP: #551 has since MERGED and did NOT re-pin this line —
  # its own 469->580 edit was dropped on rebase, which is exactly what the
  # merged-tree rule produces once 622 is already in main. It did add tests:
  # this target now collects 680, still inside the 622/777 band, so the gate is
  # green and no re-pin is owed. Left at 622 deliberately — the convention here
  # is to re-pin when the gate FIRES and to copy the number it prints, not to
  # tighten a band that is holding. (For reference only, not applied:
  # _suggested_floor 680 = 646.)
  "scripts/browser-bridge/tests|716"
  "scripts/validation/tests|97"
  # 2026-08-12, initiative-scan's `gh_available` honesty flag: 381 -> 386
  # collected, +5 for the flag's probe/report/render cases in
  # test_initiative_scan.py. Gate's own count through the gate's own rule:
  #   _suggested_floor 386 = 386 - min(50, max(1, 19)) = 386 - 19 = 367.
  # 2026-08-19, the /analyze-service recon-cost harness: 386 -> 463 collected,
  # +77 in the NEW scripts/session-analysis/tests/test_recon_cost.py (window
  # boundary, classification, the never-a-clean-zero contract, the no-captured-
  # text canaries, aggregation and --compare). That crossed the DRIFT CEILING,
  # not the floor — the gate failed with
  #   run-tests: ERROR — scripts/session-analysis/tests collected 463 tests but
  #   its floor is only 367. … Raise the TARGET_FLOORS entry to
  #   "scripts/session-analysis/tests|440"
  # 440 is copied verbatim from that message, which is this run's own count put
  # through the documented rule — never arithmetic done by hand here.
  "scripts/session-analysis/tests|440"
  "scripts/session-analysis/session_insight/tests|55"
  "scripts/mail-actions/tests|129"
  # 2026-08-16, the Signal chat pipeline arrives as a NEW target: 387 collected
  # (10 suites). MEASURED, never computed — the entry was pinned at 1 so the
  # AUTHORITATIVE gate would print its own replacement, and `nix build
  # .#checks.x86_64-linux.pytests` did:
  #   Raise the TARGET_FLOORS entry to "scripts/signal/tests|368"
  # then the re-run with 368 in place read
  #   PASS  scripts/signal/tests  (collected=387 passed=387 skipped=0 floor=368)
  # Five measurements across four audit rounds, each RE-READ from the gate rather
  # than adjusted by hand: 262/249 (first pass), 266/253 (pyright triage),
  # 340/323 (the bbernhard route table, transaction recovery, the `sending`
  # claim, contact identity, MinIO keys, remote delete, conversation grouping),
  # 376/358 (the commit-retry drop, the websocket import site, the atomic claim,
  # the reconcile path, the own-device retraction), and 387/368 (guarded state
  # transitions, the preserved approval record, the clean CLI refusal).
  # 2026-08-18, two more, both RE-READ from the gate: 400/368 stayed under the
  # ceiling (the outbound-reaction fix, #537), then 467 tripped the DRIFT
  # CEILING at 460 with every test PASSING — the floor had fallen 92 behind, so
  # a whole suite could have vanished while the gate stayed green. The gate
  # printed "scripts/signal/tests|444" and that is what is written below,
  # copied not computed. The 67 new tests are the liveness heartbeat and the
  # three deploy-blocking defects its audit found: a TIMESTAMPTZ column fed a
  # Python float (the DB layer had no executing test at all — a FakeDB appended
  # to a list), a STALLED Postgres freezing the liveness file because both
  # sinks shared a thread, and a probe that read the database it was supposed
  # to be independent of.
  # ZERO new skips, so EXPECTED_SKIPS is untouched, and GUARD 7 reported
  # `intercepted=0 systemctl-reads=0` for this target — the evidence the suite is
  # hermetic rather than merely asserted to be. If this line conflicts with a
  # sibling branch, re-run the gate on the MERGED tree and copy what it prints;
  # do not reconcile the two sides by hand.
  # 2026-08-20: 582 tripped the ceiling at 555 with every test PASSING. The gate
  # printed "scripts/signal/tests|553" and that is what is below, copied not
  # computed. The 66 new tests guard the MUTATION BATTERY, now kept in-repo at
  # scripts/signal/tests/mutation_battery.py after eight successive batteries
  # were written into scratchpads and thrown away. They mutate nothing — they
  # pin the battery's ANCHORS and killer-test NAMES, because the way a battery
  # rots is silent: an anchor stops matching, the mutant never lands, and the
  # run prints ANCHOR-MISS, which reads like a hiccup rather than "this mutant
  # tested nothing". That has already happened here once.
  "scripts/signal/tests|553"
  "scripts/initiatives/tests|745"
  "scripts/repo-cos/tests|315"
  "scripts/task-spec-drafter/tests|135"
  "scripts/claude-hooks/tests/test_guard_core.py|1260"
  # 2026-08-13, next-step-nudge.py's suite arrives as a NEW target: 78 collected on the
  # branch. Gate's own count through the gate's own rule:
  #   _suggested_floor 78 = 78 - min(50, max(1, 78/20 = 3)) = 78 - 3 = 75.
  #
  # 2026-08-13, the audit-fix round: 78 -> 112 collected. +34 for the switch to
  # additionalContext (IO-contract tests), the ASKS anchor fix, per-ARM fixtures for the
  # alternation mutants that were surviving, the isMeta guard, the headless opt-out seam,
  # and main()'s fail-open backstop. Re-measured by the AUTHORITATIVE gate — `nix build
  # .#checks.x86_64-linux.pytests` -> `collected=112`, read out of `nix log`, not from a
  # local pytest run — then put through the gate's own function:
  #   _suggested_floor 112 = 112 - min(50, max(1, 112/20 = 5)) = 112 - 5 = 107.
  #
  # 2026-08-13, the BLOCKED next-step shape: 112 -> 114 collected. +2 — the shape's own
  # structural guard and the guard that feeds NUDGE's quoted exemplar back through the
  # predicate (the two new helpers are not tests and collect nothing). Movement on this
  # line is the evidence the gate ran the new assertions at all. Measured by the
  # AUTHORITATIVE gate on THIS branch's tree — `nix build
  # .#checks.x86_64-linux.pytests` -> `collected=114`, read out of `nix log`, not from a
  # local pytest run — then put through the gate's own function, not arithmetic:
  #   _suggested_floor 114 = 114 - min(50, max(1, 114/20 = 5)) = 114 - 5 = 109.
  #
  # 2026-08-13, same branch, the OFFERS `waiting on`/`blocked on` widening: 114 -> 122.
  # +8 = four new PARKED-arm fixtures x the two parametrized arm tests. Two of the four
  # cover arms that already existed and had NO fixture (`awaiting`, `standing by`) — a
  # full mutant battery over the whole suppressor set found both surviving. Measured by
  # the AUTHORITATIVE gate on this branch's tree, `nix build
  # .#checks.x86_64-linux.pytests` -> `collected=122`, read out of `nix log`:
  #   _suggested_floor 122 = 122 - min(50, max(1, 122/20 = 6)) = 122 - 6 = 116.
  # ⚠ Two sibling branches also add tests today and will conflict HERE. Do not reconcile
  # the two sides by hand: re-run the gate on the MERGED tree and copy what it prints
  # (`rerere` has replayed a stale resolution onto this line before).
  #
  # 2026-08-13, MERGED with main (#465's registrar target below, #459, #463). Both sides
  # of this conflict are KEPT — they pin different targets — and the number was re-read
  # from the gate run on the MERGED tree, not carried over from the branch measurement.
  #
  # 2026-08-13, the OFFERS `waiting for`/`blocked by` widening — the last two arms of the
  # blocked/parked family: 122 -> 126 collected. +4 = two new PARKED-arm fixtures x the
  # two parametrized arm tests. The branch is off fd88780 and `git merge-base --is-ancestor
  # origin/main HEAD` is TRUE, so the MERGED tree and the branch tree are the same tree —
  # this number is not a branch-only reading that a merge could invalidate. Measured by
  # the AUTHORITATIVE gate, `nix build .#checks.x86_64-linux.pytests`, read out of `nix
  # log` (the build was uncached here, but a cached one prints an EMPTY log and would
  # otherwise read as a green with no counts):
  #   PASS  scripts/claude-hooks/tests/test_next_step_nudge.py  (collected=126 passed=126 skipped=0 floor=116)
  # put through the gate's OWN function rather than arithmetic:
  #   _suggested_floor 126 = 126 - min(50, max(1, 126/20 = 6)) = 126 - 6 = 120.
  # ZERO new skips; no HERMETIC_TARGETS entry needed (the file is already a target).
  # If this line conflicts with a sibling branch, re-run the gate on the MERGED tree and
  # copy what it prints — do not reconcile the two sides by hand.
  #
  # 2026-08-13, same branch, closing the two arms a differently-built mutation sweep
  # found SURVIVING (COMMITS' bare `'ll`, DONE's `that's it`): 126 -> 130 collected.
  # +4 = two more arm fixtures x the two parametrized arm tests.
  #
  # ⚠ RE-MEASURED, and the earlier reading on this line is VOID — which is the whole
  # argument for re-measuring rather than reconciling. The 126 above was taken when
  # `origin/main` was fd88780; main moved to 71c0aa2 (#467) mid-round, so that number was
  # a claim about a tree that no longer exists. This one was taken AFTER `git merge
  # origin/main`, on the tree the merge actually produced. (#467 touched only
  # claudedocs/handoff-subsystem-store.md, so the merge was textually AND semantically
  # clean here — checked, not assumed: a clean `ort` merge only means no textual
  # conflict.) The AUTHORITATIVE gate's own line, `nix build
  # .#checks.x86_64-linux.pytests`, read out of `nix log`:
  #   PASS  scripts/claude-hooks/tests/test_next_step_nudge.py  (collected=130 passed=130 skipped=0 floor=120)
  #   TOTAL collected=9666  passed=9665  skipped=1  failed=0
  # put through the gate's OWN function rather than arithmetic:
  #   _suggested_floor 130 = 130 - min(50, max(1, 130/20 = 6)) = 130 - 6 = 124.
  "scripts/claude-hooks/tests/test_next_step_nudge.py|124"
  # 2026-08-13, the registrar's DELIVERY seam arrives as a NEW target: 17 collected.
  # Gate's own count through the gate's own rule:
  #   _suggested_floor 17 = 17 - min(50, max(1, 17/20 = 0 -> 1)) = 17 - 1 = 16.
  "scripts/claude-hooks/tests/test_registrar_activation.py|16"
  # 2026-08-13, the agent activity ledger's WRITER arrives as a NEW target.
  # 39 collected after TWO audit rounds. Round 1 (+3): the throttle's real
  # observable, its positive control, and that prune is actually CALLED — the
  # first two replaced a vacuous "two calls leave one record", which
  # one-file-per-key satisfied on its own. Round 2 (+3): the no-pane path, where
  # the call-site throttle ARGUMENT is the only thing suppressing the write (the
  # round-1 test set TMUX_PANE, so it only ever exercised the early check and a
  # delta re-audit found that mutant still alive), its positive control, and the
  # failed-tmux-lookup regression. Gate's own count through the gate's own rule:
  #   _suggested_floor 39 = 39 - min(50, max(1, 39/20 = 1)) = 39 - 1 = 38.
  "scripts/claude-hooks/tests/test_agent_ledger_hook.py|38"
  # 2026-08-16, the clawgate write-back guard arrives as a NEW target: 130 collected.
  # (118 in the first round; +6 for the wall-clock budget and its positive control, a
  # naive-timestamp case, and the two gaps a DIFFERENTLY-BUILT mutation sweep found —
  # the work gate exercised through `post_tool_use` rather than a seeded state dir,
  # and a corrupt state file; +4 for the state prune; +2 for the deferred-import
  # measurement and its positive control.) Read from the AUTHORITATIVE gate's own
  # per-target line, then put through the gate's OWN function rather than arithmetic:
  #   _suggested_floor 130 = 130 - min(50, max(1, 130/20 = 6)) = 130 - 6 = 124.
  # ZERO new skips, so EXPECTED_SKIPS is untouched. If this line conflicts with a
  # sibling branch, re-run the gate on the MERGED tree and copy what it prints.
  #
  # 2026-08-16, THE AUDIT ROUND: 130 -> 208. The additions are not padding — each one
  # answers a measured defect: the relenting rung asserted on the EMITTED JSON rather
  # than on an internal kind string (the CLI feeds `additionalContext` into the same
  # `blockingErrors` array as a block, so the old rung forced a third continuation),
  # the four false-positive probes (subagent `agent_id`, unrelated-repo work, a
  # partially-written-back survey, a command that merely MENTIONS a commit), the
  # `--dismiss` escape driven end to end from the block text it advertises, the work
  # anchor that stops the skill's own pre-start comment from disarming the guard, and
  # the two hang bounds + `_sanitize`/`_state_dir`/`_scrub`, all of which had ZERO
  # coverage and survived a sweep. Read from the gate's per-target line, then through
  # the gate's OWN function:
  #   _suggested_floor 208 = 208 - min(50, max(1, 208/20 = 10)) = 208 - 10 = 198.
  #
  # 2026-08-16, THE SECOND AUDIT ROUND: 208 -> 244, and the 198 above had already
  # accumulated 46 of slack — nearly the whole 50-test allowance, i.e. one target away
  # from being unable to see a collapse. Again not padding: the quoted `git -C "<path>"
  # commit` shapes (all five measured NOT work, and `git -C` is the form this repo's own
  # CLAUDE.md mandates, so the guard silently never armed for it), the ASYMMETRIC
  # subagent rule (a subagent's READ must not arm the parent — a measured false positive
  # — while its WORK must count, because in BOTH incidents #193/#194 the work ran in
  # dispatched local subagents and refusing it made the hook silent on its own
  # motivating case), the per-task read anchor that closes "read N, work, write N back,
  # then merely read M -> blocks on M", the notice sentence that has to be true both
  # alone and spliced into a block reason, and the `--dismiss` audit ledger. Read from
  # the gate's per-target line, then through the gate's OWN function:
  #   _suggested_floor 244 = 244 - min(50, max(1, 244/20 = 12)) = 244 - 12 = 232.
  #
  # 2026-08-16, THE `--dismiss` TOMBSTONE: 244 -> 273. `--dismiss` cleared the ledger
  # entries and wrote nothing, so the NEXT read of the card re-armed the guard and it
  # blocked again — measured in production twice, 90 ms apart in the SAME tool call,
  # because the natural way to confirm a dismissal is to look at the card. Three audit
  # rounds missed it: every existing test drove `--dismiss` and then asserted silence,
  # and NONE of them read the card again afterwards. So +29 here are written the other
  # way round — the read comes after the dismissal. Two are regression tests measured
  # RED at the base sha (dismiss -> re-read -> work -> Stop, by two different routes),
  # four are labelled invariant guards/controls that were already green at base, and
  # one (the tombstone's file NAME) exists only because a mutation sweep found
  # `int(task_id) + 1` SURVIVING everything else — writer and reader share one
  # function, so an off-by-one is self-consistent. Read from the AUTHORITATIVE gate's
  # own per-target line out of `nix log`, not from a local pytest run:
  #   PASS  scripts/claude-hooks/tests/test_clawgate_writeback_guard.py  (collected=273 passed=273 skipped=0 floor=232)
  # then through the gate's OWN function rather than arithmetic:
  #   _suggested_floor 273 = 273 - min(50, max(1, 273/20 = 13)) = 273 - 13 = 260.
  # ZERO new skips, so EXPECTED_SKIPS is untouched. If this line conflicts with a
  # sibling branch, re-run the gate on the MERGED tree and copy what it prints.
  #
  # 2026-08-16, THE AUDIT-FIX ROUND ON THAT SAME PR: 273 -> 280. The audit found the
  # write order inside `dismiss` was NOT the "equivalent order" the PR called it — the
  # tombstone was written AFTER the removals, leaving a window in which a `record_read`
  # re-creates `read-<id>` and the tombstone then lands on top of it, so `is_dismissed`
  # is True while `stop_decision` still returns `block`. No test distinguished the two
  # orders, which is how the label survived; +2 here do, one behavioural (a read driven
  # into the window by a patched `os.remove`) and one structural (the call sequence
  # pinned whole). +3 more cover `dismiss_report`'s head, which derived "nothing to
  # dismiss" from `removed` rather than from disk and so said it over a `read-<id>` a
  # failed `os.remove` had left behind, plus the state-dir side effect of a bare
  # `--dismiss`. +2 are new rows on the whole-string message parametrize. ALL SEVEN are
  # RED at the base sha. Read from the AUTHORITATIVE gate's own per-target line:
  #   PASS  scripts/claude-hooks/tests/test_clawgate_writeback_guard.py  (collected=280 passed=280 skipped=0 floor=260)
  #   _suggested_floor 280 = 280 - min(50, max(1, 280/20 = 14)) = 280 - 14 = 266.
  #
  # 2026-08-20, the `delegated` state: 280 -> 296. A card whose DISPATCHED AGENT is
  # still alive owes its write-back to that AGENT, so nagging the dispatching session
  # asked it to pre-empt a close-out that had not happened — measured on tasks 241/294
  # while `bright-fox`/`brave-finch` were running, where following the guard's own
  # remedy would have manufactured the signal that run was measuring.
  # +6 are RED at the base sha (3 live statuses, the two-way status ledger, and the two
  # end-to-end cases). The other +10 are INVARIANT GUARDS and are NOT regression
  # coverage — they pin behaviour that was already correct and that the fix must not
  # eat: dead agents still `missing`, `"agent": null` unchanged, four junk shapes
  # (incl. `agent` as the status STRING), the comment-scan-wins ordering, and closed
  # beating delegated. Each was mutation-checked to die for its OWN reason; the
  # safety-critical inversion (dead treated as live) is killed three independent ways.
  # ZERO new skips, so EXPECTED_SKIPS is untouched. Read from the AUTHORITATIVE gate:
  #   PASS  scripts/claude-hooks/tests/test_clawgate_writeback_guard.py  (collected=296 passed=296 skipped=0 floor=266)
  #   _suggested_floor 296 = 296 - min(50, max(1, 296/20 = 14)) = 296 - 14 = 282.
  "scripts/claude-hooks/tests/test_clawgate_writeback_guard.py|282"
  # 2026-08-20, the clawgate task INTERVIEW gate arrives as a NEW target: 300
  # collected. Large because the non-matches are the load-bearing half of a hook
  # that DENIES — 30 commands that must not trigger, 5 producer launchers, 15
  # detector accepts against 18 rejects, 8 fenced-heading rejects — plus every body
  # source (`--body`, `--body-file`, heredoc, curl payload) driven in BOTH
  # directions and the piped case that must BLOCK. A 34-mutant sweep run under
  # PYTHONDONTWRITEBYTECODE=1 killed 34/34; the two that survived the FIRST sweep
  # (the first-unseeable-reason ordering, and a flow-path rename that survived
  # because the assertion read the constant out of the module under test) are the
  # reason three of these tests exist. The positive control — `deny()` emitting
  # "allow" — is killed by 86 of them.
  #   _suggested_floor 300 = 300 - min(50, max(1, 300/20 = 15)) = 300 - 15 = 285.
  # ZERO new skips, so EXPECTED_SKIPS is untouched. If this line conflicts with a
  # sibling branch, re-run the gate on the MERGED tree and copy what it prints.
  "scripts/claude-hooks/tests/test_clawgate_task_interview_guard.py|285"
  # 2026-08-18, the on-disk artifact-name registry arrives as a NEW target: 13
  # collected. Deliberately small — one test per module whose names it pins, plus the
  # two-sided classification guard that fails when a hook module appears or vanishes.
  #   _suggested_floor 13 = 13 - min(50, max(1, 13/20 = 0 -> 1)) = 12.
  "scripts/claude-hooks/tests/test_on_disk_artifact_names.py|12"
  # 2026-08-21, the backgrounded-command capture log (868ktvqf9) arrives as a NEW
  # target: 80 collected, 0 skipped, measured by this gate on this branch.
  #   _suggested_floor 80 = 80 - min(50, max(1, 80/20 = 4)) = 76.
  # The number is the gate's own function applied to the gate's own printed
  # count, not arithmetic — if this line conflicts, re-run the gate on the MERGED
  # tree and copy what it prints rather than reconciling the two sides.
  "scripts/claude-hooks/tests/test_bg_command_capture.py|76"
  # 2026-08-14, writer 2 (opencode) arrives as a NEW target: 15 collected.
  #   _suggested_floor 15 = 15 - min(50, max(1, 15/20 = 0 -> 1)) = 14.
  #
  # 2026-08-20, the opencode-dispatch skill (#583) lands in this target:
  # 15 -> 90 collected. +75 for scripts/opencode/tests/test_dispatch.py, which is
  # a NEW FILE in an existing directory target — so HERMETIC_TARGETS needs no
  # entry and this line is the only evidence the gate runs it at all. ZERO new
  # skips, so EXPECTED_SKIPS is untouched.
  #
  # 🔴 COPIED FROM THE AUTHORITATIVE GATE'S OWN DRIFT MESSAGE on the MERGED
  # integration tree (main + #578 + #580 + #583 + #584), not computed here and
  # not derived from this branch alone — this line is a churn magnet, `rerere`
  # has replayed a stale resolution onto its neighbours before, and arithmetic
  # across two sides of a conflict is what gave the old global literal eleven
  # values in one day:
  #   run-tests: ERROR — scripts/opencode/tests collected 90 tests but its floor
  #   is only 14. Raise the TARGET_FLOORS entry to "scripts/opencode/tests|86"
  # (which is `_suggested_floor 90` = 90 - min(50, max(1, 90/20 = 4)) = 86.)
  "scripts/opencode/tests|86"
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

# --- GUARD 8: NO TEST MAY WRITE TO THE REAL ACTIVITY SPOOL, IN ANY TARGET ------
# 🔴 THE SECOND ENFORCEMENT POINT, attached to the same line for the same reason.
#
# `scripts/collector/invocation.py` emits one `source=tool kind=invocation` row
# per tool run into `<ACTIVITY_SPOOL_DIR>/current.log`, and the activity
# collector daemon ships that spool to the PRODUCTION ClickHouse
# `activity.events`. Several suites drive tools that emit. Measured before this
# guard existed, a full `scripts/gate.sh --tier pytest` run wrote real rows into
# the operator's own dataset, where they are indistinguishable from real
# activity.
#
# #614 fixed ONE directory with a conftest. Thirteen test directories under
# `scripts/` have `.py` tests and no conftest, and the non-pytest targets
# (HOOK_TESTS, SHELL_TESTS) can never have one — so the fix is attached HERE,
# beside GUARD 7, and it is two exports:
#
#   * ACTIVITY_SPOOL_DIR -> a temp dir. The direct lever.
#   * XDG_STATE_HOME     -> a temp dir. The FALLBACK lever, and the reason this
#     guard can MEASURE itself: `spool_emit.default_spool_dir()` reads
#     ACTIVITY_SPOOL_DIR at call time and otherwise resolves
#     `${XDG_STATE_HOME:-~/.local/state}/activity/spool`. A test that takes the
#     variable away (monkeypatch.delenv, a hand-built subprocess env, a fixture
#     teardown restoring an unset ambient) drops through to the fallback — which
#     now lands in a TRAP under this run's dir instead of in production, where
#     the accounting at the bottom of this file counts it PER TARGET.
#
# 🔴 BOTH OVERRIDES ARE UNCONDITIONAL, INCLUDING OVER AN AMBIENT VALUE. A test
# runner must never write to a real spool, and an inherited ACTIVITY_SPOOL_DIR
# is far likelier to be a shell with the collector's env sourced than a
# deliberate intent. The consequence is stated rather than hidden: exporting
# ACTIVITY_SPOOL_DIR before calling this script no longer measures leakage into
# YOUR directory — it is overridden. That workflow still works, because the
# chosen paths are PRINTED below and the GUARD 8 table at the end reports the
# per-target row counts and the per-target fallback leaks directly.
SPOOL_DIR="$(mktemp -d)"
SPOOL_ISOLATED="$SPOOL_DIR/isolated"
SPOOL_TRAP="$SPOOL_DIR/xdg"
if ! mkdir -p "$SPOOL_ISOLATED" "$SPOOL_TRAP"; then
  echo "run-tests: FATAL — could not create the activity-spool isolation dirs" >&2
  echo "  under $SPOOL_DIR. Refusing to run: without them the suites append" >&2
  echo "  real rows to ~/.local/state/activity/spool, which the collector ships" >&2
  echo "  to the production ClickHouse activity.events." >&2
  exit 3
fi
export ACTIVITY_SPOOL_DIR="$SPOOL_ISOLATED"
export XDG_STATE_HOME="$SPOOL_TRAP"
export DEVRC_TEST_SPOOL_GUARD_DIR="$SPOOL_DIR"
# A fresh RUN is the ROOT of the session-nesting chain (see spool_plugin's
# NESTED_ENV). Without this, a runner copy driven FROM a pytest test — which is
# how ten of this file's own regression tests work — would inherit the flag, and
# every target inside it would be scored as a nested session with no marker.
unset DEVRC_TEST_SPOOL_IN_SESSION
SPOOL_SESSIONS_LOG="$SPOOL_DIR/sessions.log"
SPOOL_ISOLATED_LOG="$SPOOL_ISOLATED/current.log"

# The three tokens shared with `scripts/testlib/spool_plugin.py`. They are
# spelled on both sides of a process boundary, so they are PINNED both ways by
# `scripts/tests/test_activity_spool_isolation.py` — a rename on one side alone
# would leave this accounting matching nothing and reporting a clean run.
SPOOL_SESSION_MARKER="spool(session)"
SPOOL_CONTROL_SOURCE="devrc-spool-guard"
SPOOL_CONTROL_OK="emitted"

# Where the fallback lands, asked of `spool_emit` ITSELF with the variable
# removed — never restated here. A second copy of the "…/activity/spool" layout
# would agree with the first until the day the rule changed, and the trap would
# then watch a directory nothing writes to while still printing a reassuring
# zero. This also VERIFIES the trap up front: a fallback that did not land
# inside this run's dir means the two exports above do not actually govern it,
# and the run must stop rather than proceed with an unarmed detector.
SPOOL_TRAP_LOG="$(env -u ACTIVITY_SPOOL_DIR XDG_STATE_HOME="$SPOOL_TRAP" \
  python -c 'import importlib.util,sys
p=sys.argv[1]
s=importlib.util.spec_from_file_location("_se",p)
m=importlib.util.module_from_spec(s); s.loader.exec_module(m)
print(m.default_spool_dir()/m.CURRENT_NAME)' \
  "$ROOT/scripts/collector/keylog/spool_emit.py" 2>/dev/null)"
case "$SPOOL_TRAP_LOG" in
  "$SPOOL_DIR"/*) : ;;
  *)
    echo "run-tests: FATAL — the activity-spool FALLBACK does not resolve inside" >&2
    echo "  this run's guard dir. Resolved: '${SPOOL_TRAP_LOG:-<could not resolve>}'" >&2
    echo "  Expected something under $SPOOL_DIR. XDG_STATE_HOME no longer governs" >&2
    echo "  spool_emit.default_spool_dir(), so a test that drops ACTIVITY_SPOOL_DIR" >&2
    echo "  would write to the operator's REAL spool and this guard would not see it." >&2
    exit 2
    ;;
esac

echo "run-tests: activity telemetry isolated for this run (GUARD 8)"
echo "  ACTIVITY_SPOOL_DIR=$ACTIVITY_SPOOL_DIR"
echo "  fallback trap      =$SPOOL_TRAP_LOG"

# "<target>|<sess-from>|<sess-to>|<trap-from>|<trap-to>|<iso-from>|<iso-to>" —
# three line ranges per target, so every count below is attributed to the target
# that produced it rather than to the run as a whole.
SPOOL_SEEN=()
SPOOL_B_S=0
SPOOL_B_T=0
SPOOL_B_I=0

_spool_lines() { # total lines in $1 (0 when absent)
  if [ -f "$1" ]; then wc -l < "$1" | tr -d ' '; else echo 0; fi
}
_spool_slice() { # $1 = file, $2 = first line (1-based), $3 = last line
  [ -f "$1" ] || return 0
  [ "$3" -ge "$2" ] || return 0
  sed -n "$2,$3p" "$1"
}
_spool_mark_before() {
  SPOOL_B_S="$(_spool_lines "$SPOOL_SESSIONS_LOG")"
  SPOOL_B_T="$(_spool_lines "$SPOOL_TRAP_LOG")"
  SPOOL_B_I="$(_spool_lines "$SPOOL_ISOLATED_LOG")"
}
# $1 = target name. Every call MUST be preceded by the before-mark above, or the
# range it records belongs to whatever ran previously.
_spool_account() {
  SPOOL_SEEN+=("$1|$(( SPOOL_B_S + 1 ))|$(_spool_lines "$SPOOL_SESSIONS_LOG")|$(( SPOOL_B_T + 1 ))|$(_spool_lines "$SPOOL_TRAP_LOG")|$(( SPOOL_B_I + 1 ))|$(_spool_lines "$SPOOL_ISOLATED_LOG")")
}

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
# An entry is "<dir>|<reason-regex>" or "<dir>|<reason-regex>|unset:VAR" per
# LEGITIMATELY skipped test. `dir` and `reason` must both match a pytest
# `SKIPPED [n] <path>:<line>: <reason>` line, and the skip TOTAL must equal the
# number of entries THAT APPLY HERE — not the raw entry count. Adding an entry is
# a deliberate act of accounting — do it only when the skip is genuinely
# unavoidable, and say why.
#
# Built AFTER $HOME is settled: an entry may be conditional, and its condition
# must be the SAME predicate the test itself uses — never a blanket allowance,
# or the pin degrades into the numeric ceiling it exists to beat.
#
# 🔴 The third field is what makes that promise real. Without it the accounting
# compared the skip total against a FLAT entry count, so on a host where a pinned
# skip legitimately RUNS the gate went red — and the advice it printed ("delete
# its EXPECTED_SKIPS entry") is wrong for a host-conditional pin, because
# deleting it reds every OTHER host. `unset:VAR` means "expected only where VAR
# is unset or empty". One predicate — `_skip_entry_applies` — decides both the
# count and the forgiveness, so the two cannot drift apart.
EXPECTED_SKIPS=(
  # Opt-in drift check against the LIVE homelab store — needs a kubeconfig and
  # network, neither of which a hermetic gate may have. Skips everywhere unless
  # REPO_COS_LIVE_DRIFT_CHECK=1.
  "scripts/repo-cos/tests|live-store drift check is opt-in"
  # Needs a REAL Postgres (SIGNAL_PG_DSN). Unlike the skill_audit case below —
  # which was correctly fixed by re-pointing at tracked fixtures so it RUNS —
  # this one cannot be made hermetic: the test exists because SQLite does not
  # reproduce Postgres static type checking, which is the exact defect #657
  # fixed (a COALESCE over uuid/text that had never worked on real Postgres).
  # gateTools carries psycopg2 (the client) but no Postgres SERVER, so nothing
  # in the hermetic tier can satisfy it.
  # 🔴 CONSEQUENCE, stated so it is not a surprise: this test is opt-in only and
  # never runs in the gate. The regression it guards is caught only when someone
  # runs with SIGNAL_PG_DSN set. Pinning unbreaks main; it does not restore the
  # coverage.
  # CONDITIONAL: expected only where the DSN is absent. Without the third field
  # this pin made the gate permanently RED for any developer who exports
  # SIGNAL_PG_DSN — the test then RUNS, the skip total drops to 1, and the flat
  # entry count still said 2.
  "scripts/signal/tests|needs a real Postgres|unset:SIGNAL_PG_DSN"
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
  _spool_mark_before
  # 🔴 `-p testlib.nolaunch_plugin` is GUARD 7 and `-p testlib.spool_plugin` is
  # GUARD 8 (see each header). Both are on THIS line — the one place every
  # target is invoked — and not in two dozen conftests.
  python -m pytest "$d" -q -p no:cacheprovider -p testlib.nolaunch_plugin -p testlib.spool_plugin \
    --no-header -rs >"$log" 2>&1
  rc=$?
  nl_after="$(_nolaunch_lines)"
  NOLAUNCH_SEEN+=("$d|$(( nl_before + 1 ))|$nl_after")
  _spool_account "$d"
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
  _spool_mark_before
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
  # GUARD 8 accounts them too. They load no plugin, so they get no session
  # marker and no fallback control — their protection is the two env exports,
  # and what is checked is that they leaked nothing into the trap.
  _spool_account "$HOOK_TEST"
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
#
# `test_resume_state.sh` is here for the SAME reason, found the same way: it too
# said `run: bash scripts/tests/test_resume_state.sh` and nothing ever did, so
# it went RED on main unnoticed when `handoff_says_inflight` changed from taking
# a PATH to taking TEXT. Worse than merely red — a helper that returns false for
# every input satisfies every negative assertion in the file, so two of its four
# cases passed FOR THE WRONG REASON. It asserts the pure extraction helpers that
# the DRIFT block's whole PR-reconciliation rests on; an unrun suite is a guard
# that reports no failures.
SHELL_TESTS=(
  "scripts/tests/test_release_wrapper.sh"
  "scripts/tests/test_resume_state.sh"
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
  _spool_mark_before
  if bash "$SHELL_TEST"; then
    RESULTS+=("PASS  $SHELL_TEST (script)")
  else
    RESULTS+=("FAIL  $SHELL_TEST (script)")
    fail=1
  fi
  NOLAUNCH_SEEN+=("$SHELL_TEST|$(( nl_before + 1 ))|$(_nolaunch_lines)")
  _spool_account "$SHELL_TEST"
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
# --- CONDITIONAL LEDGER ENTRIES ------------------------------------------------
# An entry is `dir|reason` or `dir|reason|unset:VAR`. The third field makes the
# pin CONDITIONAL: it is expected only where VAR is unset.
#
# 🔴 WHY. The header above already promises this ("an entry may be conditional"),
# and the implementation compared the skip TOTAL against a flat entry COUNT, so a
# host where a pinned skip legitimately RUNS went red — and the runner's own
# advice there is "delete its EXPECTED_SKIPS entry", which is WRONG for a
# host-conditional pin: deleting it reds every other host. Measured consequence:
# any developer with SIGNAL_PG_DSN exported had a permanently-red gate and no
# correct way to unbreak it, i.e. the state that teaches people to pass
# DEVRC_SKIP_TESTS=1. A permanently-red gate is worse than no gate.
#
# 🔴 PARSING, and why it is not `${entry#*|}`. That takes everything after the
# FIRST `|`, so on a 3-field entry the reason would become `reason|unset:VAR` —
# fed straight to `grep -qE`, where `|` is ALTERNATION. The matcher would then
# accept any skip whose reason contained EITHER side, silently widening the pin
# instead of narrowing it. Split explicitly.
_split_skip_entry() {
  local _e="$1" _rest
  edir="${_e%%|*}"
  _rest="${_e#*|}"
  ere="${_rest%%|*}"
  if [ "$_rest" = "$ere" ]; then econd=""; else econd="${_rest#*|}"; fi
}

# 🔴 ONE predicate, used by BOTH the counting loop and the matching loop below.
# They previously evaluated applicability separately — counting honoured the
# condition, matching ignored it — so a skip could be forgiven by a pin the
# ledger said did not apply, and the totals still balanced. A rule open-coded at
# two sites is wrong at one of them; this is the consolidation.
#
# Reads the `edir`/`ere`/`econd` that `_split_skip_entry` just set. Returns 0 if
# the entry applies HERE, 1 if not. An invalid condition sets `fail` and returns
# 1 — fail CLOSED, so a typo cannot silently widen a pin.
_skip_entry_applies() {
  case "$econd" in
    "") return 0 ;;
    unset:*)
      local _v="${econd#unset:}"
      # 🔴 VALIDATE BEFORE EXPANDING. `${!_v-}` on a non-identifier (a bare
      # `unset:`, or a 4-field entry leaving `unset:FOO|typo`) raises bash's
      # `invalid variable name`, and that error ABORTS THE ENCLOSING LOOP — every
      # later entry goes silently unevaluated and `fail` stays 0. A typo in the
      # pipe-delimited grammar is the LIKELIER shape than an unknown prefix, so
      # it must not be the one that fails open.
      if ! printf '%s' "$_v" | grep -qE '^[A-Za-z_][A-Za-z0-9_]*$'; then
        echo "  ERROR: EXPECTED_SKIPS condition names an invalid variable: '$_v'" >&2
        echo "         Expected 'unset:VAR' with VAR matching [A-Za-z_][A-Za-z0-9_]*." >&2
        fail=1
        return 1
      fi
      # `-z` is unset-OR-EMPTY. That is correct here — it mirrors the pinned
      # test's own `not os.environ.get(...)` — but a future test using
      # `"VAR" in os.environ` would disagree, so match the predicate you pin.
      if [ -z "${!_v-}" ]; then return 0; fi
      return 1
      ;;
    *)
      echo "  ERROR: EXPECTED_SKIPS entry has an unknown condition: '$econd'" >&2
      echo "         Supported: 'unset:VAR'." >&2
      fail=1
      return 1
      ;;
  esac
}

# Count only the entries whose condition HOLDS here.
# NOTE (pre-existing, deliberately not changed here): this compares a count of
# skipped TESTS against a count of ENTRIES, which assumes one test per entry.
# Both current entries are `SKIPPED [1]`. Widening that is a separate change.
pin_expected=0
for entry in "${EXPECTED_SKIPS[@]}"; do
  _split_skip_entry "$entry"
  if _skip_entry_applies; then pin_expected=$(( pin_expected + 1 )); fi
done

for line in "${SKIP_LINES[@]}"; do
  matched=0
  for entry in "${EXPECTED_SKIPS[@]}"; do
    _split_skip_entry "$entry"
    # 🔴 A pin that does NOT apply here must not forgive anything. Without this,
    # the two halves of the accounting disagree: `pin_expected` excludes the
    # entry while this loop still matches on it, so a skip the ledger says
    # cannot happen here is silently absorbed and the totals still balance.
    # Measured before the fix: with SIGNAL_PG_DSN set, the signal skip was
    # forgiven by a pin whose own condition said it could not apply — green.
    if ! _skip_entry_applies; then continue; fi
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

if [ "$TOT_SKIPPED" -ne "$pin_expected" ]; then
  echo "  ERROR: $TOT_SKIPPED test(s) skipped, but $pin_expected of ${#EXPECTED_SKIPS[@]} pinned entries apply here." >&2
  if [ "$TOT_SKIPPED" -lt "$pin_expected" ]; then
    echo "         FEWER than pinned: a pinned skip now RUNS (good) — delete its" >&2
    echo "         EXPECTED_SKIPS entry, or make it conditional with '|unset:VAR'" >&2
    echo "         if it only skips where that variable is absent." >&2
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

# --- GUARD 8 (evaluation): per-target activity-spool accounting ----------------
# One line per target, ALWAYS printed — including the zeros, and never the zero
# ALONE. `leaked=0` on its own is exactly what a guard wired to nothing prints,
# so every line carries `control=` beside it: the row this run DELIBERATELY sent
# down the real fallback path and watched land in the trap. The pair is the
# claim — "1 on the positive control, 0 under test" — and `control=0` on a
# pytest target fails the run.
echo "  ---- activity-spool isolation (GUARD 8) ----"
echo "    isolated=$SPOOL_ISOLATED"
echo "    fallback-trap=$SPOOL_TRAP_LOG"
spool_problems=()
for t in "${TARGETS[@]}"; do
  seen=0
  for entry in "${SPOOL_SEEN[@]}"; do
    [ "${entry%%|*}" = "$t" ] && seen=1 && break
  done
  [ "$seen" -eq 1 ] || spool_problems+=("$t  — never accounted: run_pytest returned before GUARD 8 could measure it")
done

for entry in "${SPOOL_SEEN[@]}"; do
  IFS='|' read -r st sfrom sto tfrom tto ifrom ito <<<"$entry"

  sess_slice="$(_spool_slice "$SPOOL_SESSIONS_LOG" "$sfrom" "$sto")"
  markers=$(printf '%s\n' "$sess_slice" | grep -c "^$SPOOL_SESSION_MARKER" || true)
  marker_iso="$(printf '%s\n' "$sess_slice" | grep "^$SPOOL_SESSION_MARKER" | head -1 \
    | awk -F'\t' '{for(i=1;i<=NF;i++) if($i ~ /^isolated=/) print substr($i,10)}')"
  marker_ctl="$(printf '%s\n' "$sess_slice" | grep "^$SPOOL_SESSION_MARKER" | head -1 \
    | awk -F'\t' '{for(i=1;i<=NF;i++) if($i ~ /^control=/) print substr($i,9)}')"
  marker_fb="$(printf '%s\n' "$sess_slice" | grep "^$SPOOL_SESSION_MARKER" | head -1 \
    | awk -F'\t' '{for(i=1;i<=NF;i++) if($i ~ /^fallback=/) print substr($i,10)}')"

  trap_slice="$(_spool_slice "$SPOOL_TRAP_LOG" "$tfrom" "$tto")"
  controls=$(printf '%s\n' "$trap_slice" | grep -cF "source=$SPOOL_CONTROL_SOURCE" || true)
  leak_rows="$(printf '%s\n' "$trap_slice" | grep -vF "source=$SPOOL_CONTROL_SOURCE" | grep -c . || true)"
  rows=$(_spool_slice "$SPOOL_ISOLATED_LOG" "$ifrom" "$ito" | grep -c . || true)

  is_pytest=0
  for t in "${TARGETS[@]}"; do [ "$t" = "$st" ] && is_pytest=1 && break; done

  echo "    $st  spool-rows=$rows  fallback-leaked=$leak_rows  control=$controls  plugin=$markers"

  # 🔴 THE LEAK ITSELF. Any row in the trap that is not this guard's own control
  # reached the FALLBACK — i.e. it would have gone to ~/.local/state/activity/
  # spool and been shipped to the production activity.events.
  if [ "$leak_rows" -gt 0 ]; then
    # Only the plain scalar fields are echoed. The rest of a v1 line is
    # base64 free text, and this log is read by humans and pasted into PRs.
    kinds="$(printf '%s\n' "$trap_slice" | grep -vF "source=$SPOOL_CONTROL_SOURCE" \
      | tr '\t' '\n' | grep -E '^(source|kind)=' | sort | uniq -c \
      | sed 's/^/             /' || true)"
    spool_problems+=("$st  — wrote $leak_rows row(s) down the REAL spool fallback. They were trapped and did NOT reach the operator's dataset; a test in this target emits activity telemetry without isolating it:"$'\n'"$kinds")
  fi

  if [ "$is_pytest" -eq 1 ]; then
    if [ "$markers" -ne 1 ]; then
      spool_problems+=("$st  — the spool plugin emitted $markers session marker(s), expected exactly 1. This target ran WITHOUT the guard, so its fallback-leaked=$leak_rows means nothing (see GUARD 8's header: -p testlib.spool_plugin on the pytest line).")
    elif [ "$marker_iso" != "$SPOOL_ISOLATED" ]; then
      spool_problems+=("$st  — ran with ACTIVITY_SPOOL_DIR='$marker_iso', not this run's '$SPOOL_ISOLATED'. Something between this script and pytest is re-pointing the spool.")
    elif [ "$marker_ctl" != "$SPOOL_CONTROL_OK" ]; then
      spool_problems+=("$st  — the plugin WITHHELD its fallback control (control=$marker_ctl): it resolved the fallback to '$marker_fb', which is not inside $SPOOL_DIR. The leak detector for this target is UNARMED, so its zero is not evidence.")
    elif [ "$controls" -ne 1 ]; then
      # The control was emitted and did not arrive. The trap is not where the
      # fallback goes, so `fallback-leaked=0` is the reassuring zero of a
      # counter wired to nothing.
      spool_problems+=("$st  — the fallback trap recorded $controls control row(s), expected exactly 1. The detector is wired to nothing and its fallback-leaked=$leak_rows is unreadable.")
    fi
  else
    # Non-pytest targets load no plugin, so a marker or a control from one means
    # the accounting slices are misaligned, not that they are better protected.
    if [ "$markers" -ne 0 ] || [ "$controls" -ne 0 ]; then
      spool_problems+=("$st  — a NON-pytest target recorded $markers session marker(s) and $controls control row(s); it can have neither, so the per-target slices are misattributed.")
    fi
  fi
done

if [ "${#spool_problems[@]}" -gt 0 ]; then
  echo "  ERROR: ${#spool_problems[@]} GUARD 8 problem(s):" >&2
  for p in "${spool_problems[@]}"; do echo "         $p" >&2; done
  fail=1
fi
rm -rf "$SPOOL_DIR"

# GUARD 6. One writer, fed the same value `exit` is about to take, so the
# printed verdict and the process status cannot disagree — including through a
# pipe, which is what destroys the status in practice.
_emit_verdict "$fail"
exit "$fail"
