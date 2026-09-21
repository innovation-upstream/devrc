#!/usr/bin/env bash
# Mutation battery for the two Python task guards after the `muster` widening —
# `scripts/claude-hooks/clawgate-writeback-guard.py` (PostToolUse + Stop) and
# `scripts/claude-hooks/clawgate-task-interview-guard.py` (PreToolUse/Bash).
#
# Not run by CI. An author/reviewer instrument, kept IN THE TREE so
# "mutation-verified" can be RE-DERIVED instead of believed:
#
#   nix develop ~/workspace/devrc -c bash scripts/tests/mutants-muster-hook-guards.sh
#
# (a bare `bash …` works only where pytest is already on PATH.) Exit 0 only if
# every row is as expected. Follows `mutants-audit-ladder.sh`'s conventions.
#
# 🔴 WHY THESE GUARDS NEED A BATTERY AT ALL. Both fail SILENTLY and in the
# FAIL-OPEN direction when their name matching stops working: the interview gate
# allows every criteria-less create with no output, and the writeback gate
# reaches no verdict at all — which is also exactly what a correctly-written-back
# session looks like. So "a test passed" is worth nothing here unless the test
# has been watched to go red for the right reason.
#
# 🔴 EACH MUTANT NAMES THE TEST THAT MUST KILL IT. "A test failed" is not enough:
# these suites overlap by design (a ledger pin and a behavioural case can both
# cover one constant), so a mutant can die to a DIFFERENT test's error and be
# scored covered while its own assertion never executes. A mutant killed only by
# some other test reports 🔴 WRONG-KILLER.
#
# 🔴 THE OVER-MATCH ROWS ARE HALF THE POINT. Widening a guard can go wrong in two
# directions, and only one of them is loud. W3 and I5 widen the predicates TOO
# far — arming/denying on the bare word "muster", which is ordinary English and
# frequent during this migration — and they exist to prove the negative controls
# are REACHABLE rather than decorative.
#
# 🔴 EACH MUTATION IS COMPARED AGAINST THE ORIGINAL BEFORE IT RUNS. A target
# string that no longer matches would leave the file UNMUTATED and report "the
# guard held" — the most flattering possible wrong answer.
#
# 🔴 THREE CONTROLS, ALL MANDATORY:
#   * the unmutated BASELINE must be green (else every row is meaningless);
#   * W0/I0 are POSITIVE CONTROLS — mutants that MUST be caught, so a harness
#     wired to nothing cannot report a clean sweep;
#   * the SURVIVES rows edit comments only and must kill NOTHING, so a harness
#     that is red for everything cannot report a clean sweep either.
#
# 🔴 PYTHONDONTWRITEBYTECODE=1 AND `__pycache__` DELETED PER ROW: CPython
# validates cached bytecode on source mtime-in-whole-SECONDS plus size, so a
# same-length edit landing in the same second as the last import is invisible —
# the test imports the ORIGINAL bytecode and the mutant is scored SURVIVED
# without ever executing. Several rows below are same-length by construction.
#
# 🔴 IT NEVER TOUCHES YOUR WORKING TREE. Everything is mutated inside a
# `mktemp -d` copy built by naming individual paths — that selective copy, not
# the assertion below it, is what keeps a `.git` out of the copy (a `cp -a` of a
# worktree carries the `.git` POINTER FILE, and a git command inside such a copy
# acts on the real repository).
set -uo pipefail
CDPATH=
D="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
SRC="$(cd "$D/../.." && pwd)"

T="$(mktemp -d /tmp/muster-hook-mut-XXXXXX)"
trap 'rm -rf "$T"' EXIT
ROOT="$T/tree"

mkdir -p "$ROOT/scripts/claude-hooks/tests" "$ROOT/scripts/testlib" \
         "$ROOT/nix" "$ROOT/claude/skills/clawgate"
# The hooks themselves plus every module they or their suites import
# (`guard_core`, `hook_telemetry`, the registrar the suites read as a ledger).
cp -a "$SRC"/scripts/claude-hooks/*.py "$ROOT/scripts/claude-hooks/"
cp -a "$SRC/scripts/claude-hooks/tests/conftest.py" \
      "$SRC/scripts/claude-hooks/tests/test_clawgate_writeback_guard.py" \
      "$SRC/scripts/claude-hooks/tests/test_clawgate_task_interview_guard.py" \
      "$ROOT/scripts/claude-hooks/tests/"
# `testlib` is not optional: the suites import `testlib.mockbin` (the ONE
# definition of "write an executable stub") and this directory's conftest
# imports `testlib.spool_plugin`, so without it the baseline aborts at
# collection and every row goes unmeasured.
cp -a "$SRC"/scripts/testlib/. "$ROOT/scripts/testlib/"
# Both suites read these as LEDGERS (`HOME_NIX`, `SKILL`, `FLOW`): the hook's
# registration in home.nix and the skill/flow text it routes to.
cp -a "$SRC/nix/home.nix" "$ROOT/nix/"
cp -a "$SRC"/claude/skills/clawgate/. "$ROOT/claude/skills/clawgate/"

if [ -e "$ROOT/.git" ]; then
  echo "🔴 the copy carries a .git — refusing to run"; exit 2
fi

WB="$ROOT/scripts/claude-hooks/clawgate-writeback-guard.py"
IV="$ROOT/scripts/claude-hooks/clawgate-task-interview-guard.py"
WB_SUITE="$ROOT/scripts/claude-hooks/tests/test_clawgate_writeback_guard.py"
IV_SUITE="$ROOT/scripts/claude-hooks/tests/test_clawgate_task_interview_guard.py"
cp -a "$WB" "$T/wb.orig"
cp -a "$IV" "$T/iv.orig"
restore() { cp -a "$T/wb.orig" "$WB"; cp -a "$T/iv.orig" "$IV"; }

FAILURES=0
ROWS=0

# 🔴 FLOORS, READ FROM THE CONTENT AND NOT FROM AN EXIT CODE. A suite that never
# ran yields zero FAILED lines — i.e. "clean" — so a harness wired to nothing
# would score every mutant SURVIVED. `run-tests.sh`'s own formula is
# `m - min(50, max(1, m/20))`; re-derive with `--collect-only`, never by memory.
# Measured 2026-09-21: writeback 371, interview 361.
WB_FLOOR=352
IV_FLOOR=343

failing() { # failing <suite> <floor>
  local suite="$1" floor="$2" out n f total
  # stderr is CAPTURED, not discarded: the commonest way to get "0 tests ran" is
  # running outside `nix develop`, and discarding stderr turns that one-line
  # diagnosis into a headline that blames the TREE.
  find "$ROOT" -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null
  out="$(cd "$ROOT" && PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$ROOT/scripts" \
    python3 -m pytest "$suite" -q --no-header --tb=no -p no:cacheprovider 2>&1)"
  if grep -q "No module named pytest" <<<"$out"; then
    echo "__HARNESS_BROKE__ pytest is not on PATH — run under nix develop"
    return
  fi
  n="$(sed -n 's/^\([0-9]*\) passed.*/\1/p;s/^[0-9]* failed, \([0-9]*\) passed.*/\1/p' <<<"$out" | tail -1)"
  f="$(sed -n 's/^\([0-9]*\) failed.*/\1/p' <<<"$out" | tail -1)"
  total=$(( ${n:-0} + ${f:-0} ))
  if [ "$total" -lt "$floor" ]; then
    echo "__HARNESS_BROKE__ only $total test(s) ran (floor $floor)"
    return
  fi
  sed -n 's/^FAILED [^:]*::\([A-Za-z0-9_]*\).*/\1/p' <<<"$out" | sort -u
}

apply() { # apply <file> <old-literal> <new-literal>; refuses rather than no-ops
  python3 - "$1" "$2" "$3" <<'PY'
import pathlib, sys
p = pathlib.Path(sys.argv[1]); t = p.read_text()
old, new = sys.argv[2], sys.argv[3]
if old not in t:
    sys.exit(3)
p.write_text(t.replace(old, new, 1))
PY
}

run() { # run <name> <expect: test node name | SURVIVES> <file> <suite> <floor> <old> <new>
  local name="$1" want="$2" file="$3" suite="$4" floor="$5" old="$6" new="$7"
  ROWS=$((ROWS+1))
  if ! apply "$file" "$old" "$new"; then
    printf '  🔴 %-52s MUTATION DID NOT APPLY — result meaningless\n' "$name"
    FAILURES=$((FAILURES+1)); restore; return
  fi
  local killers; killers="$(failing "$suite" "$floor")"
  restore
  if grep -q __HARNESS_BROKE__ <<<"$killers"; then
    printf '  🔴 %-52s HARNESS BROKE — %s\n' "$name" "$killers"
    FAILURES=$((FAILURES+1)); return
  fi
  if [ "$want" = SURVIVES ]; then
    if [ -z "$killers" ]; then
      printf '  ok %-52s SURVIVED as required (control)\n' "$name"; return
    fi
    printf '  🔴 %-52s killed %s — control should survive\n' \
      "$name" "$(tr '\n' ',' <<<"$killers" | sed 's/,$//')"
    FAILURES=$((FAILURES+1)); return
  fi
  if [ -z "$killers" ]; then
    printf '  🔴 %-52s SURVIVED — no test failed\n' "$name"
    FAILURES=$((FAILURES+1)); return
  fi
  if ! grep -qx "$want" <<<"$killers"; then
    printf '  🔴 %-52s WRONG-KILLER: %s (wanted %s)\n' \
      "$name" "$(tr '\n' ',' <<<"$killers" | sed 's/,$//')" "$want"
    FAILURES=$((FAILURES+1)); return
  fi
  local extra; extra="$(grep -vx "$want" <<<"$killers" | tr '\n' ',' | sed 's/,$//')"
  printf '  ok %-52s killed by %s%s\n' "$name" "$want" \
    "${extra:+ (also: $extra)}"
}

echo "== baseline (must be green in BOTH suites) =="
for pair in "$WB_SUITE:$WB_FLOOR" "$IV_SUITE:$IV_FLOOR"; do
  base="$(failing "${pair%:*}" "${pair##*:}")"
  if [ -n "$base" ]; then
    echo "🔴 unmutated baseline is not clean for ${pair%:*}: $base"; exit 2
  fi
done
echo "  ok both suites green unmutated"

echo
echo "== clawgate-writeback-guard.py =="
# W0 POSITIVE CONTROL: a mutant that MUST be caught, so a clean sweep cannot be
# reported by a harness that never ran the code.
run "W0 the arming regex matches no CLI at all" \
    test_a_read_of_a_specific_task_arms_the_guard "$WB" "$WB_SUITE" "$WB_FLOOR" \
    'TASK_GET_RX = re.compile(r"\b(?:" + _TASK_CLI_ALT + r")\s+task\s+get\s+(\d+)\b")' \
    'TASK_GET_RX = re.compile(r"\bZZZZZZZZZZZ\s+task\s+get\s+(\d+)\b")'
# W1 THE WIDENING ITSELF, at the tuple. This is the rename, simulated in reverse.
run "W1 TASK_CLI_NAMES loses 'muster'" \
    test_THE_SILENT_NO_VERDICT_CASE_a_muster_read_plus_work_still_reaches_a_verdict \
    "$WB" "$WB_SUITE" "$WB_FLOOR" \
    'TASK_CLI_NAMES = ("clawgatectl", "muster")' \
    'TASK_CLI_NAMES = ("clawgatectl",)'
# W2 the versioned path tolerance, mutated on its own so the CLI half stays intact.
run "W2 the task API path drops the version group" \
    test_the_same_path_through_the_VERSIONED_API_also_reaches_a_verdict \
    "$WB" "$WB_SUITE" "$WB_FLOOR" \
    'TASK_API_PATH_RX = r"/api(?:/v\d+)?/tasks"' \
    'TASK_API_PATH_RX = r"/api/tasks"'
# W3 🔴 THE OVER-MATCH DIRECTION. Widening is not free: arming on the bare word
# would put a BLOCK in front of any turn that merely mentions muster.
run "W3 the arming regex fires on the bare word" \
    test_the_word_muster_alone_does_NOT_arm_the_guard "$WB" "$WB_SUITE" "$WB_FLOOR" \
    'TASK_GET_RX = re.compile(r"\b(?:" + _TASK_CLI_ALT + r")\s+task\s+get\s+(\d+)\b")' \
    'TASK_GET_RX = re.compile(r"\b(?:" + _TASK_CLI_ALT + r")\D*(\d+)\b")'
# W4 the curl fallback's URL preference, mutated at the USE SITE so the ledger pin
# stays byte-identical and only the behavioural case can see it.
run "W4 curl reads the GENERIC url instead of the tasks one" \
    test_the_curl_fallback_prefers_the_TASKS_url_and_the_TASKS_token \
    "$WB" "$WB_SUITE" "$WB_FLOOR" \
    '    url = _first_set(conf, TASK_API_URL_VARS)[0]' \
    '    url = conf.get(TASK_API_URL_VARS[1])'
run "W5 curl reads the GENERIC token instead of the tasks one" \
    test_the_curl_fallback_prefers_the_TASKS_url_and_the_TASKS_token \
    "$WB" "$WB_SUITE" "$WB_FLOOR" \
    '    token = _first_set(conf, TASK_TOKEN_VARS)[0]' \
    '    token = conf.get(TASK_TOKEN_VARS[1])'
# W6 the flag that makes the new variable non-inert.
run "W6 --api-url is never passed to the CLI" \
    test_the_TASKS_url_is_passed_to_the_cli_as_api_url "$WB" "$WB_SUITE" "$WB_FLOOR" \
    '    if api_url:
        argv += ["--api-url", api_url]' \
    '    if False:
        argv += ["--api-url", api_url]'
# W7 🔴 THE OTHER DIRECTION of the same flag: inventing it from the GENERIC key
# would be a second spelling of a default the CLIs already resolve. Proves the
# negative control is reachable.
run "W7 --api-url is invented from the generic url" \
    test_WITHOUT_the_tasks_url_no_api_url_flag_is_invented "$WB" "$WB_SUITE" "$WB_FLOOR" \
    '    api_url = _env_file(env_path).get(TASK_API_URL_VARS[0])' \
    '    api_url = _first_set(_env_file(env_path), TASK_API_URL_VARS)[0]'
# W8 the second client is never reached.
run "W8 only the FIRST task CLI is ever tried" \
    test_live_task_falls_through_to_muster_when_clawgatectl_is_ABSENT \
    "$WB" "$WB_SUITE" "$WB_FLOOR" \
    '    for binary in TASK_CLI_NAMES:' \
    '    for binary in TASK_CLI_NAMES[:1]:'
# W9 the endpoint is not bound onto the error.
run "W9 LiveReadError carries no endpoint" \
    test_every_LiveReadError_carries_the_endpoint_it_could_not_reach \
    "$WB" "$WB_SUITE" "$WB_FLOOR" \
    '        if getattr(e, "endpoint", None) is None:
            e.endpoint = endpoint' \
    '        if getattr(e, "endpoint", None) is None:
            pass'
# W10 the endpoint is bound but never printed — the DIAGNOSABILITY half of the
# fail-open trade, which is the only thing making that rung defensible.
run "W10 the UNVERIFIED notice drops the endpoint line" \
    test_an_unreachable_board_is_a_systemMessage_that_NAMES_the_endpoint \
    "$WB" "$WB_SUITE" "$WB_FLOOR" \
    '        "Endpoint it could not read: %(endpoint)s\n"' \
    '        ""'
# W11 the fail-open DECISION itself: a cannot-measure that blocks.
run "W11 the cannot-measure rung becomes a BLOCK" \
    test_an_unreachable_board_is_a_systemMessage_that_NAMES_the_endpoint \
    "$WB" "$WB_SUITE" "$WB_FLOOR" \
    '                notices.append(unknown_text(tid, first_read_ts, err, session_id,' \
    '                blocks.append(unknown_text(tid, first_read_ts, err, session_id,'
# W12 SURVIVES: a comment-only edit must kill nothing, or every row above is a
# harness that is simply red for any change.
run "W12 comment-only edit (control)" SURVIVES "$WB" "$WB_SUITE" "$WB_FLOOR" \
    '# The task API path, as a PATTERN.' \
    '# The task API path, as a PATTERN (see the extraction plan).'

echo
echo "== clawgate-task-interview-guard.py =="
# I0 POSITIVE CONTROL. 🔴 ITS NAMED KILLER IS THE *ALLOW* CASE, AND THAT IS THE
# lesson, not a detail: breaking the detector makes the gate deny EVERYTHING, so
# `…_is_DENIED_…` still passes — for the wrong reason. Only a test that asserts a
# well-specified create passes SILENTLY can see it. Naming the deny case here
# reported WRONG-KILLER on the first run, which is exactly what that check is for.
run "I0 the acceptance-criteria detector never matches" \
    test_a_create_WITH_criteria_passes_silently_for_every_cli_spelling "$IV" \
    "$IV_SUITE" "$IV_FLOOR" \
    '    r"^ {0,3}##[ \t]+acceptance[ \t]+criteria(?![^\W\d_])[^\n]*$",' \
    '    r"^ {0,3}##[ \t]+ZZZZZZZZZZ[ \t]+criteria(?![^\W\d_])[^\n]*$",'
run "I1 TASK_CLI_NAMES loses 'muster'" \
    test_a_criteria_less_create_is_DENIED_for_every_cli_spelling "$IV" "$IV_SUITE" \
    "$IV_FLOOR" \
    'TASK_CLI_NAMES = ("clawgatectl", "muster")' \
    'TASK_CLI_NAMES = ("clawgatectl",)'
# I2 the PREFILTER alone — TASK_CLI_NAMES intact, so only the seam can see it.
# 🔴 Its killer must be the SEAM guard, not the deny cases: this is the row that
# proves the relationship assertion executes rather than restating the others.
run "I2 the PREFILTER drops the muster spelling" \
    test_the_PREFILTER_admits_everything_the_CLASSIFIERS_recognise "$IV" "$IV_SUITE" \
    "$IV_FLOOR" \
    'PREFILTER = re.compile(_TASK_CLI_ALT + r"|" + _CREATE_PATH_ALT)' \
    'PREFILTER = re.compile("clawgatectl" + r"|" + _CREATE_PATH_ALT)'
run "I3 CREATE_PATHS loses the versioned mount" \
    test_the_versioned_create_path_is_in_scope_for_curl "$IV" "$IV_SUITE" "$IV_FLOOR" \
    'CREATE_PATHS = ("/api/tasks", "/api/v1/tasks")' \
    'CREATE_PATHS = ("/api/tasks",)'
# I4 the crash backstop, narrowed to the pre-widening spelling. It denies exactly
# when the gate is already broken, so a spelling it misses is the worst moment.
run "I4 the crash classifier loses the versioned path" \
    test_the_CRASH_classifier_recognises_both_spellings_and_both_paths "$IV" \
    "$IV_SUITE" "$IV_FLOOR" \
    '    r"\btask\s+create\b|(?:" + _CREATE_PATH_ALT + r")(?![/\w])")' \
    '    r"\btask\s+create\b|/api/tasks(?![/\w])")'
# I5 🔴 THE OVER-MATCH DIRECTION for this gate: keying on the word appearing
# ANYWHERE in the argv rather than on argv[0]'s basename. Proves the negative
# controls are reachable, not decorative.
run "I5 the create predicate keys on the word anywhere" \
    test_is_task_cli_create "$IV" "$IV_SUITE" "$IV_FLOOR" \
    '    if not argv or os.path.basename(argv[0]) not in TASK_CLI_NAMES:
        return False
    ops = _operands(argv, TASK_CLI_VALUE_FLAGS)' \
    '    if not argv or not any(n in " ".join(argv) for n in TASK_CLI_NAMES):
        return False
    ops = _operands(argv, TASK_CLI_VALUE_FLAGS)'
run "I6 the help exemption is scoped to one binary" \
    test_the_help_exemption_covers_both_cobra_binaries "$IV" "$IV_SUITE" "$IV_FLOOR" \
    '    if not argv or os.path.basename(argv[0]) not in TASK_CLI_NAMES:
        return False
    skip = False' \
    '    if not argv or os.path.basename(argv[0]) != "clawgatectl":
        return False
    skip = False'
run "I7 comment-only edit (control)" SURVIVES "$IV" "$IV_SUITE" "$IV_FLOOR" \
    '# The task CLI'"'"'s persistent flags that take a separate value token.' \
    '# The task CLI'"'"'s persistent flags (cobra) that take a separate value token.'

echo
if [ "$FAILURES" -eq 0 ]; then
  echo "✅ $ROWS row(s), all as expected"
  exit 0
fi
echo "🔴 $FAILURES of $ROWS row(s) not as expected"
exit 1
