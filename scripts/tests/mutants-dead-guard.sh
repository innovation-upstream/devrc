#!/usr/bin/env bash
# Mutation battery for the dead-guard detector.
#
# Not run by CI -- an author/reviewer instrument, kept in-tree so
# "mutation-verified" can be RE-DERIVED instead of believed.
#
#   bash scripts/tests/mutants-dead-guard.sh
#
# 🔴 EACH MUTANT NAMES THE TEST THAT MUST KILL IT. "A test failed" is not
# enough: with several overlapping assertions a mutant can die to a DIFFERENT
# test's error and be scored as covered while its own assertion is unreachable.
# So the expectation here is a test NODE ID, and a mutant killed only by some
# other test is reported 🔴 WRONG-KILLER, not ok.
#
# 🔴 EACH MUTANT IS DIFFED AGAINST THE ORIGINAL BEFORE IT RUNS. A `sed` that
# silently fails to match reports the UNMUTATED file's behaviour, i.e. "the
# guard held" -- the most flattering possible wrong answer.
#
# 🔴 PYTHONDONTWRITEBYTECODE=1 IS LOAD-BEARING, NOT HYGIENE. CPython validates a
# cached module on mtime-in-whole-SECONDS + size, so a same-length edit landing
# in the same second as the last import is invisible: the run imports the
# ORIGINAL bytecode and the mutant is scored SURVIVED without ever executing.
# Several mutants below are deliberately same-length substitutions.
set -uo pipefail
CDPATH=
D="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
ROOT="$(cd "$D/../.." && pwd)"
LIB="$ROOT/scripts/lib/dead_guard.py"
CLI="$ROOT/scripts/dead-guard-scan.py"
REG="$ROOT/scripts/data/dead-guard-registry.tsv"
SUITE="$ROOT/scripts/tests/test_dead_guard_scan.py"
T="$(mktemp -d /tmp/dgs-mut-XXXXXX)"

cp "$LIB" "$T/lib.orig"; cp "$CLI" "$T/cli.orig"; cp "$REG" "$T/reg.orig"
restore() { cp "$T/lib.orig" "$LIB"; cp "$T/cli.orig" "$CLI"; cp "$T/reg.orig" "$REG"; }
trap 'restore; rm -rf "$T"' EXIT

# failed test names, one per line. Read the CONTENT -- never an exit code.
failing() {
  PYTHONDONTWRITEBYTECODE=1 python3 -m pytest "$SUITE" -q --no-header --tb=no \
    -p no:cacheprovider 2>/dev/null | sed -n 's/^FAILED [^:]*::\([A-Za-z0-9_]*\).*/\1/p'
}

run() { # run <name> <target-file> <expect: a test node name | SURVIVES> <sed-expr>
  local name="$1" file="$2" want="$3" expr="$4"
  sed "$expr" "$file" > "$T/m" 2>/dev/null
  if cmp -s "$file" "$T/m"; then
    printf '  🔴 %-38s MUTATION DID NOT APPLY — result meaningless\n' "$name"; return 1
  fi
  cp "$T/m" "$file"
  local killers; killers="$(failing)"
  restore
  if [ "$want" = SURVIVES ]; then
    # The control. A behaviour-free edit MUST survive; if it kills something,
    # the harness is keying on the file's text rather than on the code, and
    # every `ok` above is worthless.
    if [ -z "$killers" ]; then
      printf '  ok %-38s SURVIVED as required (control)\n' "$name"; return 0
    fi
    printf '  🔴 %-38s CONTROL KILLED by %s — harness is not measuring behaviour\n' \
      "$name" "$(tr '\n' ',' <<<"$killers")"; return 1
  fi
  if [ -z "$killers" ]; then
    printf '  🔴 %-38s SURVIVED — no test failed\n' "$name"; return 1
  fi
  if grep -qx "$want" <<<"$killers"; then
    printf '  ok %-38s killed by %s\n' "$name" "$want"; return 0
  fi
  printf '  🔴 %-38s WRONG-KILLER — died to: %s (wanted %s)\n' \
    "$name" "$(tr '\n' ',' <<<"$killers")" "$want"; return 1
}

printf 'baseline (must be empty): '
b="$(failing)"; [ -z "$b" ] && echo "clean" || { echo "🔴 ALREADY RED: $b"; exit 1; }

printf '\n== the verdict must track EXECUTION (must be KILLED) ==\n'
# Never flag: the detector reports every guard clean. The comforting zero.
run 'never-flag-anything' "$LIB" \
  test_a_branch_with_zero_corpus_instances_is_flagged_at_its_own_line \
  's|        if any(ln in executed for ln in b.lines()):|        if True:|'
# Always flag: ignores the trace entirely, so a live branch is condemned.
run 'ignore-the-trace' "$LIB" \
  test_a_branch_with_a_real_corpus_instance_is_not_flagged \
  's|        if any(ln in executed for ln in b.lines()):|        if False:|'
# First line only, not the body SPAN -- the multi-line-statement attribution bug.
run 'span-narrowed-to-first-line' "$LIB" \
  test_a_reporting_branch_covered_only_by_its_battery_is_not_flagged \
  's|any(ln in executed for ln in b.lines())|b.first_line in executed and False|'

printf '\n== branch enumeration (must be KILLED) ==\n'
run 'elif-collapsed-into-else' "$LIB" \
  test_elif_is_reported_once_against_its_own_condition_line \
  's|            if node.orelse and not (len(node.orelse) == 1|            if node.orelse and not (len(node.orelse) == 9|'
run 'main-guard-no-longer-excluded' "$LIB" \
  test_main_guard_is_not_a_guard_branch \
  's|            if _is_main_guard(node):|            if False:|'
run 'main-exclusion-widened-to-any-Name' "$LIB" \
  test_main_guard_is_not_a_guard_branch \
  's|and t.left.id == _MAIN_GUARD|and t.left.id != _MAIN_GUARD|'
run 'except-handlers-dropped' "$LIB" \
  test_except_and_match_and_loop_else_are_enumerated \
  's|        elif isinstance(node, ast.ExceptHandler):|        elif isinstance(node, ast.Delete):|'

printf '\n== the justification hatch (must be KILLED) ==\n'
run 'bare-marker-now-resolves' "$LIB" \
  test_a_justification_needs_a_reason \
  's|reason = just.get(b.cond_line) or just.get(b.first_line)|reason = just.get(b.cond_line, "x") or just.get(b.first_line, "x")|'
run 'reason-no-longer-required' "$LIB" \
  test_a_justification_needs_a_reason \
  's|^    return \[f for f in flags if not f.justified_reason\]|    return []|'
run 'hatch-read-by-regex-not-tokenize' "$LIB" \
  test_a_pragma_inside_a_STRING_is_not_a_justification \
  's|            if tok.type != tokenize.COMMENT:|            if tok.type == tokenize.COMMENT and False:|'
run 'body-line-placement-ignored' "$LIB" \
  test_justification_is_read_from_the_condition_line_or_the_body_line \
  's|reason = just.get(b.cond_line) or just.get(b.first_line)|reason = just.get(b.cond_line)|'

printf '\n== the CLI'"'"'s zeros (must be KILLED) ==\n'
run 'missing-trace-scored-clean' "$CLI" \
  test_a_missing_trace_file_is_undecidable_not_a_clean_run \
  's|        return EXIT_UNDECIDABLE$|        return EXIT_CLEAN|'
run 'unknown-repo-scored-clean' "$CLI" \
  test_scan_of_an_unregistered_repo_is_undecidable_not_clean \
  's|              f"rather than reading this silence as coverage.", file=sys.stderr)\n||;s|^        return EXIT_UNDECIDABLE\(.*unknown\)\?$|        return EXIT_CLEAN|'
run 'slug-keeps-the-dot-git-suffix' "$CLI" \
  test_slug_parsing_handles_ssh_https_and_a_missing_dot_git \
  's|r"\[:/\](\[^/:\]+/\[^/\]+?)(?:\\.git)?\$"|r"[:/]([^/:]+/[^/]+?)(?:XXgit)?$"|'

printf '\n== the EXIT-CODE contract, end to end (must be KILLED) ==\n'
# The whole tool is worthless if the analysis is right and the exit status is
# not: a CI caller reads the status, not the flag list.
run 'unresolved-flags-still-exit-0' "$CLI" \
  test_e2e_a_planted_dead_branch_makes_the_command_exit_NONZERO \
  's|^    return EXIT_FLAGS if unres else EXIT_CLEAN|    return EXIT_CLEAN|'
run 'always-exit-nonzero' "$CLI" \
  test_e2e_a_clean_tree_exits_ZERO \
  's|^    return EXIT_FLAGS if unres else EXIT_CLEAN|    return EXIT_FLAGS|'
run 'census-path-goes-absolute' "$CLI" \
  test_e2e_the_census_path_is_repo_relative_not_absolute \
  's|^        rel = str(t.relative_to(repo))|        rel = str(t)|'
run 'census-drops-the-out-of-instrument-rows' "$CLI" \
  test_e2e_the_census_names_the_file_LINE_and_the_case \
  's|^        for r in oo:|        for r in []:|'

printf '\n== the registry contract (must be KILLED) ==\n'
run 'drop-an-out-of-instrument-row' "$REG" \
  test_registry_parses_and_every_repo_declares_what_is_NOT_measured \
  '/^civitai\/civitai\tts\tout-of-instrument/d;/^civitai\/civitai\tmjs\tout-of-instrument/d'
run 'out-of-instrument-row-stops-saying-why' "$REG" \
  test_registry_parses_and_every_repo_declares_what_is_NOT_measured \
  's|^\(ZacxDev/homelab-infra\tgo\tout-of-instrument\t\).*|\1no|'

printf '\n== POSITIVE CONTROL — a mutant that MUST survive ==\n'
# A pure comment edit changes no behaviour. If this reports KILLED the harness
# is keying on something other than the code, and every ok above is suspect.
run 'comment-only-edit-must-survive' "$LIB" \
  SURVIVES \
  's|^"""Branch-liveness analysis|"""BRANCH-liveness analysis|'
