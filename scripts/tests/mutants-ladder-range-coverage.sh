#!/usr/bin/env bash
# Mutation battery for `scripts/ladder-range-coverage.py` and the
# `measure_range_churn` extraction it shares with `scripts/audit-dispatch.py`.
#
# Not run by CI. An author/reviewer instrument, kept IN THE TREE so
# "mutation-verified" can be RE-DERIVED instead of believed.
#
#   nix develop ~/workspace/devrc -c bash \
#     scripts/tests/mutants-ladder-range-coverage.sh
#
#   (a bare `bash …` works only where pytest is already on PATH; this host's
#   `.envrc` is `use opencode`, which does not put it there.) Exit 0 only if
#   every row is as expected.
#
# 🔴 CONVENTIONS COME FROM `mutants-audit-ladder.sh` — the assert-it-applied
# driver, the named-killer rule, the harness floor, `PYTHONDONTWRITEBYTECODE=1`.
# Read that file's header for WHY each exists; this one does not restate it.
#
# 🔴 THE ARTIFACT HERE IS CODE, NOT PROSE, so these are operator and branch
# mutants rather than rewordings. The one thing they must prove is specific to
# this script: its headline output is a ZERO for a healthy ladder, so "the GAP
# detector works" and "the GAP detector is dead" are indistinguishable from the
# report alone. Every row below breaks the detector on purpose and names the
# test that must notice.
#
# 🔴 IT NEVER TOUCHES YOUR WORKING TREE. Everything is mutated inside a
# `mktemp -d` copy built by naming FOUR INDIVIDUAL FILES. That selective copy,
# not the assertion below it, is what keeps a `.git` out of the tree — this repo
# is worked in WORKTREES, whose `.git` is a FILE pointing at the real git dir, so
# a `cp -a "$SRC"` here would let a git command inside the copy act on the real
# repository.
#
# COVERAGE IS DELIBERATELY PARTIAL. Not covered: the `gh` facts path (no network
# in this battery — `facts_from_gh` is exercised only by `--facts-file`'s absence
# in `main`), the renderer's exact wording beyond the tokens the tests assert,
# and `fetch_pr_ref`. Those are the surfaces where a defect is LOUD; the rows
# here are the ones where it would be silent and flattering.
set -uo pipefail

D="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
SRC="$(cd "$D/../.." && pwd)"

T="$(mktemp -d /tmp/ladder-range-mut-XXXXXX)"
trap 'rm -rf "$T"' EXIT
ROOT="$T/tree"

mkdir -p "$ROOT/scripts/tests" "$ROOT/scripts/testlib"
cp -a "$SRC/scripts/ladder-range-coverage.py"                "$ROOT/scripts/"
cp -a "$SRC/scripts/audit-dispatch.py"                       "$ROOT/scripts/"
cp -a "$SRC/scripts/tests/test_ladder_range_coverage.py"     "$ROOT/scripts/tests/"
# The suite imports `testlib.hermetic_git` for its git fixtures; without it the
# UNMUTATED baseline aborts at import and the battery runs ZERO rows while
# reporting nothing the author touched.
cp -a "$SRC/scripts/testlib/hermetic_git.py"                 "$ROOT/scripts/testlib/"
# 🔴 THIS SCRIPT IS ITSELF A DEPENDENCY OF THE SUITE, and forgetting it is the
# trap `mutants-audit-ladder.sh` records twice:
# `test_the_batterys_floor_is_re_derived_from_this_modules_size` reads the
# `MIN_TESTS` literal below out of this file, so without this line the baseline
# aborts and every row goes unmeasured. Measured here on the first run — the
# battery reported the unmutated suite red and ran ZERO rows. Assume the next
# guard added to that module needs a line here too.
cp -a "$SRC/scripts/tests/mutants-ladder-range-coverage.sh"  "$ROOT/scripts/tests/"
[ -f "$SRC/scripts/testlib/__init__.py" ] && \
  cp -a "$SRC/scripts/testlib/__init__.py"                   "$ROOT/scripts/testlib/"

if [ -e "$ROOT/.git" ]; then
  echo "🔴 the copy carries a .git — refusing to run"; exit 2
fi
find "$ROOT" -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null

SUITE="$ROOT/scripts/tests/test_ladder_range_coverage.py"
LRC="$ROOT/scripts/ladder-range-coverage.py"
DISP="$ROOT/scripts/audit-dispatch.py"
cp -a "$LRC"  "$T/lrc.orig"
cp -a "$DISP" "$T/disp.orig"
restore() { cp -a "$T/lrc.orig" "$LRC"; cp -a "$T/disp.orig" "$DISP"; }

FAILURES=0
ROWS=0

# 🔴 Read the CONTENT, never an exit code. A suite that never ran yields zero
# FAILED lines — i.e. "clean" — so a harness wired to nothing would score every
# mutant SURVIVED. The floor catches COLLAPSE, not growth; `run-tests.sh`'s own
# formula is `m - min(50, max(1, m/20))`, which at m=20 is 19.
# 🔴 IT FIRED TWICE BEFORE THIS FILE WAS EVEN MERGED, which is the whole argument:
# the module grew 18 → 19 (pin said 17, failed with `should be 18, not 17`), then
# 19 → 20 one commit later (pin said 18, failed with `should be 19, not 18`).
# Neither growth was a refactor — each was a single test added while fixing
# something — and a hand-maintained floor would have silently tolerated both.
# 🔴 DO NOT maintain this by memory — `test_ladder_range_coverage.py::
# test_the_batterys_floor_is_re_derived_from_this_modules_size` reads the literal
# below, counts the module, and fails with the replacement value. Two instances
# of a too-low floor silently widening have already been recorded in
# `mutants-audit-ladder.sh`; this is pinned from the first commit instead.
MIN_TESTS=20
failing() {
  local out n f total
  out="$(cd "$ROOT" && PYTHONDONTWRITEBYTECODE=1 python3 -m pytest "$SUITE" \
    -q --no-header --tb=no -p no:cacheprovider 2>&1)"
  if grep -q "No module named pytest" <<<"$out"; then
    echo "__HARNESS_BROKE__ pytest is not on PATH — run it as" \
         "\`nix develop ~/workspace/devrc -c bash" \
         "scripts/tests/mutants-ladder-range-coverage.sh\`, not in a bare shell"
    return
  fi
  n="$(sed -n 's/^\([0-9]*\) passed.*/\1/p;s/^[0-9]* failed, \([0-9]*\) passed.*/\1/p' <<<"$out" | tail -1)"
  f="$(sed -n 's/^\([0-9]*\) failed.*/\1/p' <<<"$out" | tail -1)"
  total=$(( ${n:-0} + ${f:-0} ))
  if [ "$total" -lt "$MIN_TESTS" ]; then
    echo "__HARNESS_BROKE__ only $total test(s) ran (floor $MIN_TESTS)"
    return
  fi
  sed -n 's/^FAILED [^:]*::\([A-Za-z0-9_]*\).*/\1/p' <<<"$out" | sort -u
}

apply() {
  python3 - "$1" "$2" "$3" <<'PY'
import pathlib, sys
p = pathlib.Path(sys.argv[1]); t = p.read_text()
old, new = sys.argv[2], sys.argv[3]
if old not in t:
    sys.exit(3)
p.write_text(t.replace(old, new, 1))
PY
}

run() { # run <name> <expect: test name | SURVIVES> <file> <old> <new>
  local name="$1" want="$2" file="$3" old="$4" new="$5"
  ROWS=$((ROWS+1))
  if ! apply "$file" "$old" "$new"; then
    printf '  🔴 %-52s MUTATION DID NOT APPLY — result meaningless\n' "$name"
    FAILURES=$((FAILURES+1)); restore; return
  fi
  local killers; killers="$(failing)"
  restore
  if grep -q __HARNESS_BROKE__ <<<"$killers"; then
    printf '  🔴 %-52s HARNESS BROKE — %s\n' "$name" "$killers"
    FAILURES=$((FAILURES+1)); return
  fi
  if [ "$want" = SURVIVES ]; then
    if [ -n "$killers" ]; then
      printf '  🔴 %-52s KILLED but should SURVIVE: %s\n' \
        "$name" "$(tr '\n' ',' <<<"$killers" | sed 's/,$//')"
      FAILURES=$((FAILURES+1)); return
    fi
    printf '  ok %-52s SURVIVES, as expected\n' "$name"; return
  fi
  if [ -z "$killers" ]; then
    printf '  🔴 %-52s SURVIVED — no test failed\n' "$name"
    FAILURES=$((FAILURES+1)); return
  fi
  # 🔴 The named test must be AMONG the killers, reported in two steps so
  # "your guard is dead, something else caught it" is never rendered as
  # "the probe is no longer isolated". These rows are about whether THIS
  # assertion executes.
  if ! grep -qx "$want" <<<"$killers"; then
    printf '  🔴 %-52s WRONG-KILLER: %s (wanted %s)\n' \
      "$name" "$(tr '\n' ',' <<<"$killers" | sed 's/,$//')" "$want"
    FAILURES=$((FAILURES+1)); return
  fi
  printf '  ok %-52s killed by %s\n' "$name" \
    "$(tr '\n' ',' <<<"$killers" | sed 's/,$//')"
}

echo "== baseline (must be GREEN, or every row below is meaningless) =="
BASE="$(failing)"
if [ -n "$BASE" ]; then
  echo "🔴 the UNMUTATED suite is not green: $(tr '\n' ',' <<<"$BASE")"
  exit 2
fi
echo "  ok unmutated suite is green"

echo
echo "== the GAP detector itself =="
# The single most flattering failure this script can have: report every ladder
# as a tight chain. The uncovered total is then 0 everywhere, which is exactly
# what a healthy corpus looks like.
run "GAP can never be returned (collapse it to TIGHT)" \
    test_the_1233_shape_reports_the_skipped_rounds_churn "$LRC" \
    '    return GAP, churn, None' \
    '    return TIGHT, churn, None'

run "the tail adjacency is dropped from the chain" \
    test_the_tail_after_the_last_block_is_its_own_gap "$LRC" \
    '    pairs.append((usable[-1].audited_to, head, usable[-1].round_no, None))' \
    '    pass  # tail dropped'

run "uncovered churn is never accumulated" \
    test_the_1233_shape_reports_the_skipped_rounds_churn "$LRC" \
    '        if label == GAP:
            uncovered_a += added or 0
            uncovered_d += deleted or 0' \
    '        if False:
            uncovered_a += added or 0
            uncovered_d += deleted or 0'

# 🔴 The interior/tail split is the one number a reader will quote, and a mutant
# that credits a tail gap to the interior bucket makes the unambiguous half look
# 6x bigger than it is (3,727 tail vs 655 interior over the review's 20 ladders).
run "a TAIL gap is credited to the INTERIOR bucket" \
    test_interior_and_tail_gaps_are_reported_as_SEPARATE_totals "$LRC" \
    '            if r_to is None:' \
    '            if False:'

run "the interior bucket never accumulates" \
    test_interior_and_tail_gaps_are_reported_as_SEPARATE_totals "$LRC" \
    '                interior_a += added or 0' \
    '                interior_a += 0'

# 🔴 The regression row. This caveat shipped as a LITERAL ("Three of the 20
# ladders") and printed that devrc figure under a 5-ladder run of another repo.
# The mutant restores the literal; the guard must notice.
run "the zero-line-gap caveat goes back to a literal" \
    test_the_zero_line_gap_caveat_is_DERIVED_from_this_run "$LRC" \
    '                   f"{zero_line_gaps} gap(s) in THIS run look like that.")' \
    '                   "Three of the 20 ladders look like that.")'

# 🔴 The regression that made a real finding read as routine drift: pairing a raw
# range count with a `--not <base>` line count. devrc #1046's tail printed
# `55 commit(s), 1105 line(s)` when the churn population was TWO.
run "the commit count reverts to the RAW range population" \
    test_the_commit_count_beside_the_lines_is_the_CHURN_population "$LRC" \
    '        commits = churn.churn_commits if churn else None' \
    '        commits = churn.commits if churn else None'

run "the churn-population count is never computed" \
    test_the_commit_count_beside_the_lines_is_the_CHURN_population "$DISP" \
    '        churn_commits = int(out2.strip())' \
    '        churn_commits = int(out.strip())'

echo
echo "== the labels that must NOT become a sized GAP =="
run "an OVERLAP is reported as a GAP instead" \
    test_an_overlap_is_labelled_OVERLAP_and_given_no_size "$LRC" \
    '        if backward is True:' \
    '        if backward is None:'

run "a git ERROR is read as a plain False (not UNMEASURABLE)" \
    test_a_sha_git_cannot_resolve_is_UNMEASURABLE_not_a_gap "$LRC" \
    '    # Any other rc is an ERROR, not a False. `_classify` turns None into
    # UNMEASURABLE; collapsing it to False there would label the adjacency a GAP
    # and hand it a size.
    return None' \
    '    return False'

echo
echo "== the refusal: a zero that cannot be told from a broken run =="
run "the positive control is never consulted (render)" \
    test_absent_commits_are_REFUSED_not_reported_as_zero "$LRC" \
    '        if not L.control_churn:' \
    '        if False:'

run "the refusal never reaches the EXIT STATUS" \
    test_exit_code_4_when_a_ladder_is_refused "$LRC" \
    '    if any(L.reason is None and not L.control_churn for L in ladders):' \
    '    if False:'

echo
echo "== holes with no size =="
run "a bare audited=<sha> is not recorded as a hole" \
    test_a_bare_audited_sha_is_reported_as_a_hole_with_no_size "$LRC" \
    '    bare = [b.round_no for b in blocks if not (b.audited_from and b.audited_to)]' \
    '    bare = []'

run "malformed blocks are dropped instead of reported" \
    test_an_unparsed_block_is_reported_rather_than_dropped "$LRC" \
    '        for r in L.malformed:' \
    '        for r in []:'

run "a round-2-first ledger is not told round 1 is out of window" \
    test_a_ledger_starting_at_round_2_says_round_1_is_out_of_window "$LRC" \
    '        if L.first_round and L.first_round > 1:' \
    '        if False:'

echo
echo "== the shared core, and the rule that must differ per caller =="
# 🔴 THE POINT OF THE EXTRACTION. If the core starts refusing an empty range,
# every TIGHT chain becomes unmeasurable; if `measure_ledger` STOPS refusing one,
# a delta round's empty-by-construction range reads as a clean round. Both
# directions have their own row, because one mutant cannot show both.
run "the shared core starts refusing an empty range" \
    test_measure_range_churn_does_NOT_refuse_an_empty_range "$DISP" \
    '        return RangeChurn({}, 0, 0, 0, 0, None)' \
    '        return fail("the range is EMPTY")'

run "measure_ledger stops refusing an empty range" \
    test_measure_ledger_still_refuses_an_empty_range "$DISP" \
    '    if churn.commits == 0:
        # 🔴 THREE causes' \
    '    if False:
        # 🔴 THREE causes'

run "the churn numbers are dropped on the way back out" \
    test_measure_ledger_still_measures_a_real_range "$DISP" \
    '    return LedgerReport(
        churn.files, churn.added, churn.deleted, churn.commits,
        churn.churn_commits, None, None, None
    )' \
    '    return LedgerReport(
        churn.files, 0, 0, churn.commits,
        churn.churn_commits, None, None, None
    )'

run "a failed git call becomes a zero instead of a reason" \
    test_a_failed_git_call_is_a_reason_not_a_zero "$DISP" \
    '    if rc != 0:
        return fail(f"`git rev-list {frm}..{to}` exited {rc}: "
                    f"{(err or out).strip() or '"'"'no output'"'"'}")' \
    '    if rc != 0:
        return RangeChurn({}, 0, 0, 0, None)'

echo
echo "== the anti-duplication guard (its own positive control) =="
# The structural guard asserts this script does NOT carry a second copy of the
# churn command. A guard that cannot fire reads as coverage and provides none,
# so the mutant is the duplication it forbids.
run "a second copy of the churn command is re-typed here" \
    test_it_imports_the_churn_command_rather_than_carrying_a_copy "$LRC" \
    'def real_runner(cmd, cwd=None):' \
    'def _unused(runner, repo_dir, a, b, base):
    return runner(["git", "-C", repo_dir, "log", "--numstat", "--format=",
                   "--remerge-diff", f"{a}..{b}", "--not", base])


def real_runner(cmd, cwd=None):'

echo
echo "== controls (must kill NOTHING) =="
# A comment edit and a rename of a purely internal local must both survive. If
# either goes red the suite is keyed to incidental text rather than behaviour.
run "edit a comment nobody asserts on" SURVIVES "$LRC" \
    '# --------------------------------------------------------------------------- #
# Rendering' \
    '# --------------------------------------------------------------------------- #
# Rendering (the operator-facing half)'

run "rename an internal local in the numstat loop" SURVIVES "$DISP" \
    '    files, added, deleted = {}, 0, 0
    for line in out.splitlines():
        parts = line.split("\t")' \
    '    files, added, deleted = {}, 0, 0
    for raw_line in out.splitlines():
        parts = raw_line.split("\t")'

echo
if [ "$FAILURES" -eq 0 ]; then
  echo "✅ $ROWS row(s), all as expected"
  exit 0
fi
echo "🔴 $FAILURES of $ROWS row(s) not as expected"
exit 1
