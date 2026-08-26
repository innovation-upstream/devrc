#!/usr/bin/env bash
# Mutation battery for the dead-guard detector.
#
# Not run by CI -- an author/reviewer instrument, kept in-tree so
# "mutation-verified" can be RE-DERIVED instead of believed.
#
#   bash scripts/tests/mutants-dead-guard.sh          # exit 0 only if ALL ok
#
# 🔴 IT NEVER TOUCHES YOUR WORKING TREE. An earlier revision `cp`d each mutant
# over the real `scripts/lib/dead_guard.py`, `scripts/dead-guard-scan.py` and
# the committed `scripts/data/dead-guard-registry.tsv`, relying on an EXIT trap
# to put them back. In a repo whose rules are built around shared checkouts and
# parallel agents, one SIGKILL or one concurrent `git add` leaves a MUTATED
# TRACKED FILE staged. The repo's only other battery
# (`scripts/tests/mutants-base-clone.sh`) got this right from the start by
# writing mutants into `mktemp -d`; this one now copies the whole tree to /tmp
# and mutates only the copy.
#
# 🔴 EACH MUTANT NAMES THE TEST THAT MUST KILL IT. "A test failed" is not
# enough: with several overlapping assertions a mutant can die to a DIFFERENT
# test's error and be scored as covered while its own assertion is unreachable.
# A mutant killed only by some other test reports 🔴 WRONG-KILLER, not ok.
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
SRC="$(cd "$D/../.." && pwd)"

T="$(mktemp -d /tmp/dgs-mut-XXXXXX)"
trap 'rm -rf "$T"' EXIT
ROOT="$T/tree"
mkdir -p "$ROOT/scripts/tests"
cp -a "$SRC/scripts/lib" "$SRC/scripts/testlib" "$SRC/scripts/data" "$ROOT/scripts/"
cp -a "$SRC/scripts/dead-guard-scan.py" "$ROOT/scripts/"
cp -a "$SRC/scripts/tests/test_dead_guard_scan.py" "$ROOT/scripts/tests/"
# 🔴 A `cp -a` of a worktree would carry its `.git` POINTER FILE, and a git
# command inside the copy would then act on the REAL repository. Nothing here
# copies `.git`, and this asserts that rather than assuming it.
if [ -e "$ROOT/.git" ]; then
  echo "🔴 the copy carries a .git -- refusing to run"; exit 2
fi
find "$ROOT" -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null

# The copy carries only the files under mutation, but
# `test_registry_names_every_test_directory_in_devrc` enumerates the repo's
# TEST DIRECTORIES -- so without a skeleton it sees one directory, trips that
# test's own anti-vacuity guard, and the whole battery reports ALREADY RED.
# (That guard doing its job is why this block exists.) Recreate the directory
# NAMES from the source with empty placeholders: the ledger test is about the
# registry's coverage of those names, not about their contents, and the battery
# only ever collects "$SUITE" so the placeholders are never run.
git -C "$SRC" ls-files \
  | sed -n 's|^\(.*\)/test_[^/]*\.py$|\1|p' | sort -u \
  | while IFS= read -r d; do
      mkdir -p "$ROOT/$d" && : > "$ROOT/$d/test_ledger_placeholder.py"
    done

LIB="$ROOT/scripts/lib/dead_guard.py"
CLI="$ROOT/scripts/dead-guard-scan.py"
REG="$ROOT/scripts/data/dead-guard-registry.tsv"
PLUG="$ROOT/scripts/testlib/dead_guard_plugin.py"
SUITE="$ROOT/scripts/tests/test_dead_guard_scan.py"
cp "$LIB" "$T/lib.orig"; cp "$CLI" "$T/cli.orig"; cp "$REG" "$T/reg.orig"
cp "$PLUG" "$T/plug.orig"
restore() { cp "$T/lib.orig" "$LIB"; cp "$T/cli.orig" "$CLI"; cp "$T/reg.orig" "$REG"; cp "$T/plug.orig" "$PLUG"; }

FAILURES=0

# failed test names, one per line. Read the CONTENT -- never an exit code.
# 🔴 A SUITE THAT NEVER RAN YIELDS ZERO `FAILED` LINES, i.e. "clean". Reading
# only FAILED lines could not tell "no test failed" from "pytest died at
# collection" -- and the two SURVIVES controls would then report `ok` over a
# harness that ran nothing. So COUNT the collected tests and refuse below a
# floor. The floor is deliberately far under the real count: it catches
# collapse, not growth.
MIN_TESTS=30
failing() {
  local out
  out="$(PYTHONDONTWRITEBYTECODE=1 python3 -m pytest "$SUITE" -q --no-header --tb=no \
    -p no:cacheprovider 2>/dev/null)"
  local n
  n="$(sed -n 's/^\([0-9]*\) passed.*/\1/p;s/^[0-9]* failed, \([0-9]*\) passed.*/\1/p' <<<"$out" | tail -1)"
  local f
  f="$(sed -n 's/^\([0-9]*\) failed.*/\1/p' <<<"$out" | tail -1)"
  local total=$(( ${n:-0} + ${f:-0} ))
  if [ "$total" -lt "$MIN_TESTS" ]; then
    echo "__HARNESS_BROKE__ only $total test(s) ran (floor $MIN_TESTS)"
    return
  fi
  sed -n 's/^FAILED [^:]*::\([A-Za-z0-9_]*\).*/\1/p' <<<"$out"
}

run() { # run <name> <target-file> <expect: a test node name | SURVIVES> <sed-expr>
  local name="$1" file="$2" want="$3" expr="$4"
  sed "$expr" "$file" > "$T/m" 2>/dev/null
  if cmp -s "$file" "$T/m"; then
    printf '  🔴 %-40s MUTATION DID NOT APPLY — result meaningless\n' "$name"
    FAILURES=$((FAILURES+1)); return
  fi
  cp "$T/m" "$file"
  local killers; killers="$(failing)"
  restore
  if grep -q __HARNESS_BROKE__ <<<"$killers"; then
    printf '  🔴 %-40s HARNESS BROKE — %s\n' "$name" "$killers"
    FAILURES=$((FAILURES+1)); return
  fi
  if [ "$want" = SURVIVES ]; then
    # The control. A behaviour-free edit MUST survive; if it kills something,
    # the harness is keying on the file's text rather than on the code, and
    # every `ok` above is worthless.
    if [ -z "$killers" ]; then
      printf '  ok %-40s SURVIVED as required (control)\n' "$name"; return
    fi
    printf '  🔴 %-40s CONTROL KILLED by %s — not measuring behaviour\n' \
      "$name" "$(tr '\n' ',' <<<"$killers")"; FAILURES=$((FAILURES+1)); return
  fi
  if [ -z "$killers" ]; then
    printf '  🔴 %-40s SURVIVED — no test failed\n' "$name"
    FAILURES=$((FAILURES+1)); return
  fi
  if grep -qx "$want" <<<"$killers"; then
    printf '  ok %-40s killed by %s\n' "$name" "$want"; return
  fi
  printf '  🔴 %-40s WRONG-KILLER — died to: %s (wanted %s)\n' \
    "$name" "$(tr '\n' ',' <<<"$killers")" "$want"; FAILURES=$((FAILURES+1))
}

printf 'mutating a COPY at %s (your worktree is untouched)\n' "$ROOT"
printf 'baseline (must be empty): '
b="$(failing)"; [ -z "$b" ] && echo "clean" || { echo "🔴 ALREADY RED: $b"; exit 1; }

printf '\n== the verdict must track EXECUTION (must be KILLED) ==\n'
run 'never-flag-anything' "$LIB" \
  test_a_branch_with_zero_corpus_instances_is_flagged_at_its_own_line \
  's|        if any(ln in executed for ln in b.lines()):|        if True:|'
run 'ignore-the-trace' "$LIB" \
  test_a_branch_with_a_real_corpus_instance_is_not_flagged \
  's|        if any(ln in executed for ln in b.lines()):|        if False:|'
# 🔴 `any` -> `all` SURVIVED the whole suite before the multi-line fixtures were
# added: every branch body was ONE line, which makes the two identical. The
# fixture-collapse trap, found by an audit, not by this battery.
run 'any-to-all-over-the-span' "$LIB" \
  test_a_multiline_body_is_TAKEN_when_only_SOME_of_its_lines_ran \
  's|        if any(ln in executed for ln in b.lines()):|        if all(ln in executed for ln in b.lines()):|'
# `span-truncated-to-first-line` lives in the expected-survivor section below.

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
  's|^            if not (f.justified_reason and re.search(r"\\w", f.justified_reason))\]|            if f.justified_reason is None]|'
run 'reason-no-longer-required' "$LIB" \
  test_a_justification_needs_a_reason \
  's|^    return \[f for f in flags|    return [] or [f for f in flags[:0]|'
run 'hatch-read-by-regex-not-tokenize' "$LIB" \
  test_a_pragma_inside_a_STRING_is_not_a_justification \
  's|^        if tok.type != tokenize.COMMENT:|        if tok.type == tokenize.COMMENT and False:|'
run 'body-line-placement-ignored' "$LIB" \
  test_justification_is_read_from_the_condition_line_or_the_body_line \
  's|^            reason = just.get(b.first_line)|            reason = None|'

printf '\n== the zeros that must be UNDECIDABLE, not clean (must be KILLED) ==\n'
run 'missing-trace-scored-clean' "$CLI" \
  test_a_missing_trace_file_is_undecidable_not_a_clean_run \
  's|^        return EXIT_UNDECIDABLE$|        return EXIT_CLEAN|'
run 'clobbered-tracer-published-anyway' "$CLI" \
  test_a_test_that_CLEARS_the_tracer_makes_the_run_undecidable \
  's|^    if trace.get("clobbered"):|    if False:|'
run 'untraced-file-reported-as-all-dead' "$CLI" \
  test_a_LIBRARY_guard_driven_only_by_a_SUBPROCESS_is_undecidable \
  's|^        if not executed.get(str(t)):|        if False:|'
run 'empty-instrument-selector-scored-clean' "$CLI" \
  test_an_instrument_selector_matching_NOTHING_is_undecidable \
  's|^    if empty:|    if False:|'
# 🔴 The FIRST attempt here mutated `tokenize.TokenError` out of the catch list
# and SURVIVED -- because that catch was unreachable (`ast.parse` raises
# SyntaxError first for every TokenError input). It has since been deleted, and
# `test_tokenize_TokenError_is_deliberately_NOT_in_the_catch_list` pins its
# absence. `UnicodeDecodeError` is the reachable member of that class.
# 🔴 SECOND attempt: mutating `UnicodeDecodeError` out ALSO survived, because
# it is a ValueError SUBCLASS and ValueError was still listed. Three of the
# five names in the original catch list were redundant spellings that read as
# coverage while adding nothing. The list is now three (OSError joined for the
# mode-000 case), and each of these mutants removes real behaviour.
run 'valueerror-not-caught-so-latin1-escapes' "$CLI" \
  test_a_guard_that_cannot_be_DECODED_is_undecidable_not_a_findings_exit \
  's|^        except (SyntaxError, ValueError, OSError) as e:$|        except (SyntaxError, OSError) as e:|'
run 'syntaxerror-not-caught' "$CLI" \
  test_an_unparseable_guard_is_undecidable_not_a_findings_exit \
  's|^        except (SyntaxError, ValueError, OSError) as e:$|        except (ValueError, OSError) as e:|'
run 'undecidable-arm-removed' "$CLI" \
  test_an_unparseable_guard_is_undecidable_not_a_findings_exit \
  's|^    if undecidable:$|    if False:|'
run 'slug-keeps-the-dot-git-suffix' "$CLI" \
  test_slug_parsing_handles_ssh_https_and_a_missing_dot_git \
  's|r"\[:/\](\[^/:\]+/\[^/\]+?)(?:\\.git)?\$"|r"[:/]([^/:]+/[^/]+?)(?:XXgit)?$"|'

printf '\n== the EXIT-CODE contract, end to end (must be KILLED) ==\n'
run 'unresolved-flags-still-exit-0' "$CLI" \
  test_e2e_a_planted_dead_branch_makes_the_command_exit_NONZERO \
  's|^    return EXIT_FLAGS if unres else EXIT_CLEAN|    return EXIT_CLEAN|'
run 'always-exit-nonzero' "$CLI" \
  test_e2e_a_clean_tree_exits_ZERO \
  's|^    return EXIT_FLAGS if unres else EXIT_CLEAN|    return EXIT_FLAGS|'

printf '\n== the census is an artifact people re-derive (must be KILLED) ==\n'
run 'census-path-goes-absolute' "$CLI" \
  test_e2e_the_census_path_is_repo_relative_not_absolute \
  's|^        rel = str(t.relative_to(repo))|        rel = str(t)|'
run 'census-drops-the-out-of-instrument-rows' "$CLI" \
  test_e2e_the_census_names_the_file_LINE_and_the_case \
  's|^    for r in oo:|    for r in []:|'
run 'census-goes-back-to-append-only' "$CLI" \
  test_the_census_is_IDEMPOTENT \
  's|^    blocks\[slug\] = mine|    blocks.setdefault(slug, []).extend(mine)|'
run 'census-writes-the-absolute-interpreter-path' "$CLI" \
  test_the_census_never_carries_an_absolute_path \
  's|^                     interpreter_id(python, redact=True), rc, excluded=excl)|                     interpreter_id(python, redact=False), rc, excluded=excl)|'
run 'census-stops-recording-the-red-run' "$CLI" \
  test_the_census_records_that_the_run_was_RED \
  's|^    if nfail:$|    if False:|'
run 'tsv-escaping-removed' "$CLI" \
  test_a_TAB_in_a_snippet_cannot_shift_the_census_columns \
  's|^    return str(s).replace("\\t", "\\\\t").replace("\\n", " ").replace("\\r", " ")|    return str(s)|'

printf '\n== the registry contract (must be KILLED) ==\n'
run 'drop-an-out-of-instrument-row' "$REG" \
  test_registry_parses_and_every_repo_declares_what_is_NOT_measured \
  '/^civitai\/civitai\tts\tout-of-instrument/d;/^civitai\/civitai\tmjs\tout-of-instrument/d'
run 'out-of-instrument-row-stops-saying-why' "$REG" \
  test_registry_parses_and_every_repo_declares_what_is_NOT_measured \
  's|^\(ZacxDev/homelab-infra\tgo\tout-of-instrument\t\).*|\1no|'
# 🔴 Pick a row whose directories are named NOWHERE ELSE. Deleting the
# `scripts/claude-hooks/tests` row does NOT uncover that directory, because the
# two `instrument` rows above it contain the same path as a prefix -- so that
# mutant SURVIVED, correctly, and testing it would have been testing nothing.
run 'drop-a-ledger-row-for-unlisted-dirs' "$REG" \
  test_registry_names_every_test_directory_in_devrc \
  '/^innovation-upstream\/devrc\tpython\tout-of-instrument\tscripts\/dl-router\/tests/d'

# 🔴 RETRACTION. This mutant was recorded here as an EXPECTED SURVIVOR, on the
# claim that no reachable input distinguishes `last = max(...)` from
# `last = first`. THE CLAIM WAS FALSE. `global`/`nonlocal` are compile-time
# declarations that emit no bytecode, so a body starting with one has an
# untraceable first line while the rest of it runs -- and the narrowed span
# then condemns a live branch. A committed "expected survivor" is a claim like
# any other, and this one went unchecked for exactly one round.
run 'span-truncated-to-first-line' "$LIB" \
  test_a_body_whose_FIRST_STATEMENT_EMITS_NO_BYTECODE_is_still_TAKEN \
  's|^    last = max((getattr(s, "end_lineno", None) or s.lineno) for s in body)|    last = first|'

run 'census-drops-other-repos-provenance' "$CLI" \
  test_scanning_one_repo_KEEPS_another_repos_provenance_line \
  's|^            if line.startswith("#") and not _PROVENANCE.match(line):|            if line.startswith("#"):|'
run 'pragma-on-if-also-silences-the-else' "$LIB" \
  test_one_pragma_on_an_IF_line_does_not_silence_its_ELSE_too \
  's|^        reason = just.get(b.cond_line) if b.kind != "else-body" else None|        reason = just.get(b.cond_line)|'
run 'except-header-pragma-stops-resolving' "$LIB" \
  test_a_pragma_on_an_EXCEPT_line_resolves_that_handler \
  's|^        reason = just.get(b.cond_line) if b.kind != "else-body" else None|        reason = just.get(b.cond_line) if b.kind == "if-body" else None|'
run 'body-read-widened-to-the-whole-span' "$LIB" \
  test_a_NESTED_branchs_pragma_does_not_resolve_its_PARENT \
  's|^            reason = just.get(b.first_line)|            reason = next((just[l] for l in b.lines() if just.get(l)), None)|'
run 'census-blocks-no-longer-sorted' "$CLI" \
  test_the_census_is_ORDER_STABLE_across_repos \
  's|^    for key in sorted(blocks):|    for key in blocks:|'
run 'ledger-membership-back-to-substring' "$CLI" \
  test_ledger_membership_is_path_segments_not_substrings \
  's|^    return \[d for d in test_dirs(repo) if d not in named\]|    return [d for d in test_dirs(repo) if d not in " ".join(named)]|'

run 'em-dash-counts-as-a-reason' "$LIB" \
  test_a_marker_with_only_PUNCTUATION_after_it_is_still_bare \
  's|^            if not (f.justified_reason and re.search(r"\\w", f.justified_reason))\]|            if not f.justified_reason]|'
run 'no-test-file-blamed-on-subprocesses' "$CLI" \
  test_a_registry_with_NO_test_file_says_so_instead_of_blaming_subprocesses \
  's|^            why = ("no registered test file drives it -- the registry lists "|            why = ("driven through a subprocess XX "|'
run 'bare-pytest-runs-the-target-repos-whole-suite' "$CLI" \
  test_a_registry_with_NO_test_file_says_so_instead_of_blaming_subprocesses \
  's|^        return ({"executed": {}, "clobbered": False}, (0, 0),|        groups = {None: []}\n    if False:\n        return ({"executed": {}, "clobbered": False}, (0, 0),|'

printf '\n== the tracer plugin — THREE detection sites, each pinned ALONE ==\n'
# 🔴 The plugin was not a mutation target at all until now, and all three
# clobber checks were exercised by ONE in-body last-test case that every site
# catches -- so each was individually deletable with the suite still green.
# The redundant-guard trap: "each died for its own reason" is a much stronger
# claim than "each died".
run 'drop-sessionfinish-hook' "$PLUG" \
  test_a_clobber_in_a_FIXTURE_FINALIZER_of_the_last_test_is_caught \
  's|^def pytest_sessionfinish(session, exitstatus):|def _disabled_sessionfinish(session, exitstatus):|'
run 'library-file-selector-registers-its-parent-dir' "$CLI" \
  test_ledger_membership_is_path_segments_not_substrings \
  's|^            if "/" in tok and last.startswith("test_"):|            if "/" in tok and ("." in last or "*" in last):|'
run 'clobber-check-back-in-atexit' "$PLUG" \
  test_an_ordinary_atexit_cleanup_is_NOT_reported_as_a_clobber \
  's|^    if not _OUT:|    if _TARGETS and sys.gettrace() is not _tracer:\n        _state["clobbered"] = True\n    if not _OUT:|'

printf '\n== POSITIVE CONTROL — a mutant that MUST survive ==\n'
run 'comment-only-edit-must-survive' "$LIB" \
  SURVIVES \
  's|^"""Branch-liveness analysis|"""BRANCH-liveness analysis|'

printf '\n'
if [ "$FAILURES" -eq 0 ]; then
  echo "ALL MUTANTS ACCOUNTED FOR"
else
  # 🔴 An aggregate, because the script's own status used to be the LAST run's
  # -- which is the must-survive control -- so a battery with failing mutants
  # exited 0. In a file whose header says "read the CONTENT, never an exit
  # code", that was the exact defect it warns about.
  echo "🔴 $FAILURES MUTANT(S) UNACCOUNTED FOR"
fi
exit $(( FAILURES > 0 ))
