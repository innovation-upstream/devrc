#!/usr/bin/env bash
# Mutation battery for `scripts/worktree-prune` — a tool that DELETES git
# worktrees, so "the guards are covered" has to be re-derivable, not believed.
#
#   nix develop -c bash scripts/tests/mutants-worktree-prune.sh
#
# 🔴 WHY THIS FILE EXISTS AT ALL. Rounds 1-3 of PR #895 each reported a mutation
# sweep ("13 mutants, 13 killed", "29 mutants") and each sweep died with the
# session that ran it. A mutation result that cannot be re-derived from the tree
# is a CLAIM about a run nobody can repeat — the same standing this repo already
# rejected for test results. `mutants-dead-guard.sh` and `mutants-base-clone.sh`
# are the in-tree precedent; this follows their shape deliberately, including
# the run() contract, so there is one thing to learn and not three.
#
# 🔴 IT NEVER TOUCHES YOUR WORKING TREE. Every mutant is written into a COPY of
# the tree under `mktemp -d`. The tool under mutation is a DELETE tool, and this
# repo is a shared checkout with parallel agents in it: one SIGKILL between
# `cp mutant` and `cp orig` in a design that mutated the real file would leave a
# tracked, executable, worktree-DELETING script mutated on disk, one blind
# `git add` from being committed.
#
# 🔴 EACH MUTANT NAMES THE TEST THAT MUST KILL IT. "A test failed" is not enough:
# with several overlapping assertions a mutant can die to a DIFFERENT test's
# error and be scored covered while its own assertion is unreachable. A mutant
# killed only by some other test reports 🔴 WRONG-KILLER, not ok.
#
# 🔴 EACH MUTANT IS DIFFED AGAINST THE ORIGINAL BEFORE IT RUNS. A `sed` that
# silently fails to match reports the UNMUTATED file's behaviour — i.e. "the
# guard held", the most flattering possible wrong answer. That is not
# hypothetical here: the tool's docstrings are long and several of these
# patterns anchor on lines that a reword would move.
#
# 🔴 PYTHONDONTWRITEBYTECODE=1 IS LOAD-BEARING, NOT HYGIENE. CPython validates a
# cached module on mtime-in-whole-SECONDS + size, so a same-length edit landing
# in the same second as the last import is invisible: the run imports the
# ORIGINAL bytecode and the mutant is scored SURVIVED without ever executing.
# Several mutants below are deliberately same-length substitutions.
#
# 🔴 THE SUMMARY LINE IS PARSED, NEVER "THE LAST LINE OF THE OUTPUT". A sibling
# harness for this same PR scored EVERY mutant SURVIVED — including its positive
# control — because it read the last line of a merged stdout+stderr stream, and
# that line was the direnv banner. stderr is dropped here and the parse anchors
# on pytest's own `N passed` / `N failed` summary.
set -uo pipefail
CDPATH=
D="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
SRC="$(cd "$D/../.." && pwd)"

T="$(mktemp -d /tmp/wtp-mut-XXXXXX)"
trap 'rm -rf "$T"' EXIT
ROOT="$T/tree"
mkdir -p "$ROOT/scripts/tests"
cp -a "$SRC/scripts/testlib" "$ROOT/scripts/"
cp -a "$SRC/scripts/worktree-prune" "$ROOT/scripts/"
cp -a "$SRC/scripts/tests/test_worktree_prune.py" "$ROOT/scripts/tests/"
cp -a "$SRC/scripts/tests/conftest.py" "$ROOT/scripts/tests/"
# 🔴 A `cp -a` of a worktree would carry its `.git` POINTER FILE, and a git
# command inside the copy would then act on the REAL repository. Nothing here
# copies `.git`, and this asserts that rather than assuming it.
if [ -e "$ROOT/.git" ]; then
  echo "🔴 the copy carries a .git — refusing to run"; exit 2
fi
find "$ROOT" -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null

TOOL="$ROOT/scripts/worktree-prune"
SUITE="$ROOT/scripts/tests/test_worktree_prune.py"
cp "$TOOL" "$T/tool.orig"
ORIG_SHA="$(sha256sum "$T/tool.orig" | cut -d' ' -f1)"
restore() { cp "$T/tool.orig" "$TOOL"; }

FAILURES=0

# 🔴 A SUITE THAT NEVER RAN YIELDS ZERO `FAILED` LINES, i.e. "clean". Reading
# only FAILED lines could not tell "no test failed" from "pytest died at
# collection" — and the SURVIVES controls would then report `ok` over a harness
# wired to nothing. So COUNT the collected tests and refuse below a floor. The
# floor catches COLLAPSE, not growth, so it sits far under the real count.
MIN_TESTS=140
failing() {
  local out
  out="$(PYTHONDONTWRITEBYTECODE=1 python3 -m pytest "$SUITE" -q --no-header --tb=no \
    -p no:cacheprovider -p no:randomly 2>/dev/null)"
  local n f
  n="$(sed -n 's/^\([0-9]*\) passed.*/\1/p;s/^[0-9]* failed, \([0-9]*\) passed.*/\1/p' <<<"$out" | tail -1)"
  f="$(sed -n 's/^\([0-9]*\) failed.*/\1/p' <<<"$out" | tail -1)"
  local total=$(( ${n:-0} + ${f:-0} ))
  if [ "$total" -lt "$MIN_TESTS" ]; then
    echo "__HARNESS_BROKE__ only $total test(s) ran (floor $MIN_TESTS)"
    return
  fi
  sed -n 's/^FAILED [^:]*::\([A-Za-z0-9_]*\).*/\1/p' <<<"$out"
}

run() { # run <name> <expect: a test node name | SURVIVES> <sed-expr>
  local name="$1" want="$2" expr="$3"
  sed "$expr" "$TOOL" > "$T/m" 2>/dev/null
  if cmp -s "$TOOL" "$T/m"; then
    printf '  🔴 %-46s MUTATION DID NOT APPLY — result meaningless\n' "$name"
    FAILURES=$((FAILURES+1)); return
  fi
  cp "$T/m" "$TOOL"
  local killers; killers="$(failing)"
  restore
  if grep -q __HARNESS_BROKE__ <<<"$killers"; then
    printf '  🔴 %-46s HARNESS BROKE — %s\n' "$name" "$killers"
    FAILURES=$((FAILURES+1)); return
  fi
  if [ "$want" = SURVIVES ]; then
    # The control. A behaviour-free edit MUST survive; if it kills something,
    # the harness is keying on the file's TEXT rather than on its CODE, and
    # every `ok` above is worthless.
    if [ -z "$killers" ]; then
      printf '  ok %-46s SURVIVED as required (control)\n' "$name"; return
    fi
    printf '  🔴 %-46s CONTROL KILLED by %s — not measuring behaviour\n' \
      "$name" "$(tr '\n' ',' <<<"$killers")"; FAILURES=$((FAILURES+1)); return
  fi
  if [ -z "$killers" ]; then
    printf '  🔴 %-46s SURVIVED — no test failed\n' "$name"
    FAILURES=$((FAILURES+1)); return
  fi
  if grep -qx "$want" <<<"$killers"; then
    printf '  ok %-46s killed by %s\n' "$name" "$want"; return
  fi
  printf '  🔴 %-46s WRONG-KILLER — died to: %s (wanted %s)\n' \
    "$name" "$(tr '\n' ',' <<<"$killers")" "$want"; FAILURES=$((FAILURES+1))
}

printf 'mutating a COPY at %s (your worktree is untouched)\n' "$ROOT"
printf 'baseline (must be empty): '
b="$(failing)"; [ -z "$b" ] && echo "clean" || { echo "🔴 ALREADY RED: $b"; exit 1; }

printf '\n== the exclusion predicate — the thing that spares a live session ==\n'
run 'path-excluded-always-none' \
  test_the_default_spares_a_non_agent_entry_end_to_end \
  's|^def path_excluded(path: str, globs: "list\[str\] \| None") -> "str \| None":|&\n    return None|'
run 'default-glob-not-applied' \
  test_the_default_and_the_explicit_glob_produce_identical_rows \
  's|^    if not include_agent_worktrees and AGENT_WORKTREE_GLOB not in globs:|    if False:|'
run 'exclusion-not-a-blocker-in-classify' \
  test_an_excluded_row_is_unremovable_whatever_its_verdict \
  's|^    if excluded_by:$|    if False:|'
run 'executor-trusts-the-removable-flag' \
  test_execute_refuses_an_excluded_row_even_if_removable_was_forced \
  's|^        if r.get("excluded_by"):|        if False:|'
run 'ancestors-not-tested-so-a-directory-glob-misses-its-contents' \
  test_excluding_a_directory_covers_its_contents \
  's|^    forms = \[path\]|    return [path]\n    forms = [path]|'
run 'trailing-slash-not-stripped' \
  test_a_trailing_slash_does_not_defeat_the_filter \
  's|^    return raw.strip().rstrip("/")|    return raw.strip()|'

printf '\n== --confirm and the removal floor ==\n'
run 'confirm-compares-against-the-dead-count-not-the-removable-one' \
  test_confirm_with_the_total_dead_count_is_refused_when_one_is_excluded \
  's|^    targets = \[r for r in rows if r.get("removable")\]|    targets = [r for r in rows if r.get("verdict") == DEAD]|'
run 'confirm-mismatch-does-not-refuse' \
  test_execute_with_a_wrong_confirm_refuses_and_removes_nothing \
  's|^    if confirm != len(targets):|    if False:|'
run 'orphan-is-removable' \
  test_only_dead_rows_are_ever_removable \
  's|^        removable = verdict == DEAD and not blockers|        removable = verdict in (DEAD, ORPHAN) and not blockers|'

printf '\n== ROUND 4 FIX 1 — the interpreter-split guard in _resolve_or_none ==\n'
# 🔴 The `except` is what stops one looped symlink taking down an 860-row scan on
# CPython 3.12. On 3.13 `resolve()` does not raise, so this mutant is EXPECTED to
# survive there — which is exactly why the test asserting `is None` is guarded,
# and why this line says so instead of the battery reporting a mystery.
if python3 -c 'import sys; sys.exit(0 if sys.version_info < (3,13) else 1)'; then
  run 'resolve-or-none-does-not-swallow-the-loop' \
    test_a_looped_repo_path_on_the_command_line_does_not_crash_the_run \
    's|^    except (OSError, RuntimeError, ValueError):|    except (OSError, ValueError):|'
else
  echo "  -- skipped on CPython >= 3.13: resolve() does not raise, so this"
  echo "     mutant is unreachable HERE. Run the battery on 3.12 to score it."
fi
run 'resolve-or-none-always-returns-none' \
  test_a_glob_on_the_resolved_path_matches_a_symlinked_repo \
  's|^        return p.resolve()|        return None|'
# 🔴 Anchored on the RETURN, not on the `except` — `sed` is line-oriented and an
# `\n` in the PATTERN never matches, which the first draft of this battery
# reported as "MUTATION DID NOT APPLY" (the run() diff guard doing its job).
run 'is-dir-raises-instead-of-answering-false' \
  test_discovery_survives_a_directory_it_cannot_read \
  '/^def is_dir/,/^def discover_repos/ s|^        return False$|        raise|'

printf '\n== ROUND 4 FIX 2 — the shout must track the GLOB, not the FLAG ==\n'
# 🔴 Two mutants, ISOLATED. Mutating the whole `if` would also delete the guard's
# enclosing condition and prove nothing about the new half; each of these leaves
# one half intact so the other has to be doing the work.
run 'shout-gated-on-the-flag-again' \
  test_the_shout_is_gated_on_the_GLOB_not_on_the_flag \
  's|^    if summary.get("agent_worktrees_included") and not summary.get("agent_worktree_glob_in_force"):|    if summary.get("agent_worktrees_included"):|'
run 'glob-in-force-key-hardcoded-false' \
  test_summarize_reports_whether_the_agent_glob_is_actually_in_force \
  's|^        "agent_worktree_glob_in_force": AGENT_WORKTREE_GLOB in globs,|        "agent_worktree_glob_in_force": False,|'
run 'glob-in-force-key-hardcoded-true' \
  test_summarize_reports_whether_the_agent_glob_is_actually_in_force \
  's|^        "agent_worktree_glob_in_force": AGENT_WORKTREE_GLOB in globs,|        "agent_worktree_glob_in_force": True,|'
run 'retyped-glob-stops-sparing-the-agent-tree' \
  test_the_retyped_glob_really_does_spare_the_agent_worktree \
  's|^    globs = normalize_globs(patterns)|    globs = [g for g in normalize_globs(patterns) if g != AGENT_WORKTREE_GLOB]|'

printf '\n== ROUND 4 FIX 4 — the --allow-unmatched-globs echo ==\n'
run 'override-echo-back-inside-the-glob-block' \
  test_the_override_is_echoed_even_with_no_exclude_path_at_all \
  's|^    if summary.get("allow_unmatched_globs"):$|    if summary.get("allow_unmatched_globs") and summary.get("exclude_globs"):|'
run 'override-echo-deleted-entirely' \
  test_the_override_is_echoed_prominently_in_the_report \
  's|^    if summary.get("allow_unmatched_globs"):$|    if False:|'

printf '\n== ROUND 4 FIX 5 — the refusal names the free remedy ==\n'
run 'refusal-drops-the-default-is-free-note' \
  test_the_refusal_says_a_retyped_default_glob_is_free_to_drop \
  's|^        redundant = \[g for g in dud if normalize_glob(g) == AGENT_WORKTREE_GLOB\]|        redundant = []|'
run 'refusal-note-fires-for-every-glob' \
  test_the_refusal_says_a_retyped_default_glob_is_free_to_drop \
  's|^        redundant = \[g for g in dud if normalize_glob(g) == AGENT_WORKTREE_GLOB\]|        redundant = list(dud)|'

printf '\n== ROUND 4 FIX 8 — prefix abbreviation ==\n'
run 'allow-abbrev-back-on' \
  test_dangerous_flags_cannot_be_reached_by_abbreviation \
  's|^        allow_abbrev=False,|        allow_abbrev=True,|'

printf '\n== ROUND 4 FIX 9 — the production job count ==\n'
run 'jobs-default-back-to-serial' \
  test_the_default_job_count_is_the_one_production_uses \
  's|^    p.add_argument("--jobs", type=int, default=8|    p.add_argument("--jobs", type=int, default=1|'

printf '\n== the zero-match refusal, and its round-2 scoping ==\n'
run 'refusal-scoped-to-every-glob-again' \
  test_the_default_exclusion_matching_zero_rows_does_not_refuse \
  's|^            d\["glob"\] for d in per_glob if d\["matched"\] == 0 and d\["typed"\]\]|            d["glob"] for d in per_glob if d["matched"] == 0]|'
run 'typed-globs-matching-zero-do-not-refuse' \
  test_execute_refuses_while_a_TYPED_glob_matches_zero_rows \
  's|^            d\["glob"\] for d in per_glob if d\["matched"\] == 0 and d\["typed"\]\]|            ]|'
run 'per-glob-counts-become-a-total' \
  test_a_working_glob_alongside_a_dud_still_refuses \
  's|^        hits = \[r for r in rows if path_excluded(r\["path"\], \[g\])\]|        hits = [r for r in rows if path_excluded(r["path"], globs)]|'
run 'empty-scan-refuses-again' \
  test_an_empty_scan_with_confirm_zero_is_a_no_op_not_a_refusal \
  's|^    if dud and not rows:|    if False:|'
run 'allow-unmatched-globs-does-not-downgrade' \
  test_allow_unmatched_globs_warns_and_proceeds \
  's|^    elif dud and args.allow_unmatched_globs:|    elif False:|'
run 'an-empty-typed-glob-is-silently-dropped' \
  test_an_empty_exclude_path_is_a_usage_error \
  's|^        if not normalize_glob(raw):|        if False:|'

printf '\n== the report — a number that reads as full coverage ==\n'
run 'spared-dead-rows-not-announced' \
  test_the_default_report_says_dead_rows_were_spared \
  's|^    if summary.get("excluded_dead"):|    if False:|'
# 🔴 THIS BATTERY'S FIRST REAL FIND: it SURVIVED a fully green suite. Every
# excluded row in every fixture was also `dead`, so the two numbers were always
# equal and nothing could tell them apart. The killer below is the test written
# to answer it — an excluded row that CANNOT be dead.
run 'excluded-dead-counts-every-excluded-row' \
  test_excluded_dead_counts_only_the_DEAD_excluded_rows \
  's|^        "excluded_dead": sum(1 for r in excluded if r\["verdict"\] == DEAD),|        "excluded_dead": len(excluded),|'
run 'excluded-rows-hidden-from-the-report' \
  test_excluded_rows_still_appear_in_the_text_report \
  's|^    return f"{row\[.verdict.\]} (excluded)" if row.get("excluded_by") else row\["verdict"\]|    return row["verdict"]|'
# 🔴 The killer is the ZERO-MATCH test, and MEASURED so — not the mistyped-glob
# one this originally named. On `two_dead` the default glob matches, so
# `excluded` is 1 under the mutant too and the table still prints; only a scan
# where NOTHING matched separates "a glob is in force" from "a glob matched".
run 'filter-table-gated-on-the-match-count' \
  test_the_default_exclusion_matching_zero_is_still_reported \
  's|^    if summary.get("exclude_globs"):|    if summary.get("excluded"):|'

printf '\n== --force must never reach `git worktree remove` ==\n'
run 'force-passed-to-worktree-remove' \
  test_the_tool_never_passes_force_to_worktree_remove \
  's|"worktree", "remove", str(path)|"worktree", "remove", "--force", str(path)|'

printf '\n== POSITIVE CONTROLS — mutants whose fate is KNOWN ==\n'
# 🔴 A comment-only edit MUST survive. If it kills something, the harness is
# keying on the file's text and every `ok` above is worthless.
run 'comment-only-edit-must-survive' SURVIVES \
  's|^# ── reporting ─|# ── REPORTING ─|'
# 🔴 …and a mutant that CANNOT survive, so a harness wired to nothing cannot
# report a clean sweep. `main()` returning early removes everything the suite
# checks; if THIS is scored SURVIVED, no test executed.
run 'main-returns-immediately-must-die' \
  test_dry_run_is_the_default_and_removes_nothing \
  's|^    args = build_parser().parse_args(argv)|    args = build_parser().parse_args(argv)\n    return RC_OK|'

printf '\n'
# 🔴 The tool is restored by CONTENT, not by trusting the trap: this battery
# mutates a DELETE tool, so "it was put back" is a claim worth checking.
NOW_SHA="$(sha256sum "$TOOL" | cut -d' ' -f1)"
if [ "$NOW_SHA" != "$ORIG_SHA" ]; then
  echo "🔴 THE MUTATED COPY WAS NOT RESTORED ($NOW_SHA != $ORIG_SHA)"
  FAILURES=$((FAILURES+1))
else
  echo "tool restored by content: sha256 $NOW_SHA"
fi
# 🔴 …and the SOURCE tree's copy is byte-identical to what we started from. The
# battery is supposed to be incapable of touching it; this asserts it.
SRC_SHA="$(sha256sum "$SRC/scripts/worktree-prune" | cut -d' ' -f1)"
if [ "$SRC_SHA" != "$ORIG_SHA" ]; then
  echo "🔴 THE SOURCE TREE'S worktree-prune CHANGED ($SRC_SHA != $ORIG_SHA)"
  FAILURES=$((FAILURES+1))
else
  echo "source tree untouched:    sha256 $SRC_SHA"
fi

printf '\n'
if [ "$FAILURES" -eq 0 ]; then
  echo "ALL MUTANTS ACCOUNTED FOR"
else
  # 🔴 An aggregate, because a script whose status is the LAST run's exits 0 when
  # the last run is the must-SURVIVE control — the exact defect this file's
  # header warns about.
  echo "🔴 $FAILURES MUTANT(S) UNACCOUNTED FOR"
fi
exit $(( FAILURES > 0 ))
