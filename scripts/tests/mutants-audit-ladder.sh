#!/usr/bin/env bash
# Mutation battery for the audit-ladder PROSE guards —
# `claude/skills/audit-pr/SKILL.md`, its `reference/round-ladder-evidence.md`,
# and the pins in `scripts/tests/test_audit_ladder_stop_rule.py`.
#
# Not run by CI. An author/reviewer instrument, kept IN THE TREE so
# "mutation-verified" can be RE-DERIVED instead of believed.
#
#   bash scripts/tests/mutants-audit-ladder.sh          # exit 0 only if ALL ok
#
# 🔴 IT EXISTS BECAUSE THE SWEEPS IT REPLACES WERE NOT COMMITTED. devrc #900 ran
# ten audit rounds and recorded ~30 mutants in the module's docstring; every one
# of those runs happened in a session scratchpad that no longer exists, so not a
# single row could be re-checked by anyone else — including the rows that
# justified adding a pin. `mutants-claim-work.sh` had already recorded exactly
# that lesson for a different file. This follows its conventions.
#
# 🔴 THE ARTIFACT UNDER MUTATION IS PROSE, so the mutants are rewordings and
# deletions rather than operator swaps. That is the point: `claude/RULES.md`
# says a guard on WORDS is walkable by REWORDING, and each row below is a
# rewording that a reader would accept and that must nevertheless go red.
#
# 🔴 IT NEVER TOUCHES YOUR WORKING TREE. Everything is mutated inside a
# `mktemp -d` copy, and the copy is asserted to carry no `.git` — a `cp -a` of a
# worktree would carry its `.git` POINTER FILE and a git command inside the copy
# would then act on the real repository. (That hazard is one of the things the
# skill under test now warns about.)
#
# 🔴 EACH MUTANT NAMES THE TEST THAT MUST KILL IT. "A test failed" is not
# enough: these pins overlap by design — the same sentence can be covered by a
# whole-block constant and by a narrower one — so a mutant can die to a
# DIFFERENT test's error and be scored as covered while its own assertion is
# unreachable. A mutant killed only by some other test reports 🔴 WRONG-KILLER.
#
# 🔴 EACH MUTATION IS COMPARED AGAINST THE ORIGINAL BEFORE IT RUNS. A target
# string that no longer matches — line re-wrapped, a word changed — would leave
# the file UNMUTATED and report "the guard held", the most flattering possible
# wrong answer. That is not hypothetical here: during #900 five mutants silently
# failed to apply for exactly that reason across three rounds, and were caught
# only because the driver asserted first.
#
# 🔴 TWO CONTROLS, BOTH MANDATORY:
#   * the unmutated BASELINE must be green (else every row is meaningless);
#   * two SURVIVES rows — a re-wrap of a pinned sentence and an edit to prose
#     nobody pinned — which must kill NOTHING. `_norm` collapses whitespace on
#     purpose, so a re-wrap that goes red would mean the pins are keyed to line
#     breaks rather than to words.
#
# 🔴 PYTHONDONTWRITEBYTECODE=1: a stale `.pyc` keyed on mtime-in-whole-seconds
# plus size is how a same-length edit gets scored SURVIVED without executing.
#
# COVERAGE IS DELIBERATELY PARTIAL, AND HERE IS WHAT IS NOT COVERED. The rows
# below are the ones whose SURVIVAL was measured during #900 — i.e. each was
# green until a pin was added for it. Rows that only ever confirmed an existing
# pin (the M-series against `claude/RULES.md`, the evidence-file truncation
# ladder) are not repeated here; run them from the module's docstring matrix if
# you are changing those guards.
set -uo pipefail
CDPATH=
D="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
SRC="$(cd "$D/../.." && pwd)"

T="$(mktemp -d /tmp/audit-ladder-mut-XXXXXX)"
trap 'rm -rf "$T"' EXIT
ROOT="$T/tree"

mkdir -p "$ROOT/scripts/tests" \
         "$ROOT/claude/skills/audit-pr/reference"
cp -a "$SRC/scripts/tests/test_audit_ladder_stop_rule.py" "$ROOT/scripts/tests/"
cp -a "$SRC/claude/RULES.md"          "$ROOT/claude/"
cp -a "$SRC/claude/RULES-ARCHIVE.md"  "$ROOT/claude/"
cp -a "$SRC/claude/skills/audit-pr/SKILL.md" "$ROOT/claude/skills/audit-pr/"
cp -a "$SRC/claude/skills/audit-pr/reference/round-ladder-evidence.md" \
      "$ROOT/claude/skills/audit-pr/reference/"

if [ -e "$ROOT/.git" ]; then
  echo "🔴 the copy carries a .git — refusing to run"; exit 2
fi
find "$ROOT" -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null

SUITE="$ROOT/scripts/tests/test_audit_ladder_stop_rule.py"
SKILL="$ROOT/claude/skills/audit-pr/SKILL.md"
EVID="$ROOT/claude/skills/audit-pr/reference/round-ladder-evidence.md"
cp -a "$SKILL" "$T/skill.orig"
cp -a "$EVID"  "$T/evid.orig"
restore() { cp -a "$T/skill.orig" "$SKILL"; cp -a "$T/evid.orig" "$EVID"; }

FAILURES=0
ROWS=0

# 🔴 Read the CONTENT, never an exit code. A suite that never ran yields zero
# FAILED lines — i.e. "clean" — so a harness wired to nothing would score every
# mutant SURVIVED and every control ok. The floor catches COLLAPSE, not growth.
MIN_TESTS=8
failing() {
  local out n f total
  out="$(cd "$ROOT" && PYTHONDONTWRITEBYTECODE=1 python3 -m pytest "$SUITE" \
    -q --no-header --tb=no -p no:cacheprovider 2>/dev/null)"
  n="$(sed -n 's/^\([0-9]*\) passed.*/\1/p;s/^[0-9]* failed, \([0-9]*\) passed.*/\1/p' <<<"$out" | tail -1)"
  f="$(sed -n 's/^\([0-9]*\) failed.*/\1/p' <<<"$out" | tail -1)"
  total=$(( ${n:-0} + ${f:-0} ))
  if [ "$total" -lt "$MIN_TESTS" ]; then
    echo "__HARNESS_BROKE__ only $total test(s) ran (floor $MIN_TESTS)"
    return
  fi
  sed -n 's/^FAILED [^:]*::\([A-Za-z0-9_]*\).*/\1/p' <<<"$out" | sort -u
}

# apply <file> <old-literal> <new-literal> — literal, multi-line safe, and it
# refuses rather than no-ops when the target is absent.
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

run() { # run <name> <expect: test node name | SURVIVES> <file> <old> <new>
  local name="$1" want="$2" file="$3" old="$4" new="$5"
  ROWS=$((ROWS+1))
  if ! apply "$file" "$old" "$new"; then
    printf '  🔴 %-46s MUTATION DID NOT APPLY — result meaningless\n' "$name"
    FAILURES=$((FAILURES+1)); restore; return
  fi
  local killers; killers="$(failing)"
  restore
  if grep -q __HARNESS_BROKE__ <<<"$killers"; then
    printf '  🔴 %-46s HARNESS BROKE — %s\n' "$name" "$killers"
    FAILURES=$((FAILURES+1)); return
  fi
  if [ "$want" = SURVIVES ]; then
    if [ -z "$killers" ]; then
      printf '  ok %-46s SURVIVED as required (control)\n' "$name"; return
    fi
    printf '  🔴 %-46s CONTROL KILLED by %s — keyed on text, not words\n' \
      "$name" "$(tr '\n' ',' <<<"$killers")"; FAILURES=$((FAILURES+1)); return
  fi
  if [ -z "$killers" ]; then
    printf '  🔴 %-46s SURVIVED — no test failed\n' "$name"
    FAILURES=$((FAILURES+1)); return
  fi
  if grep -qx "$want" <<<"$killers"; then
    printf '  ok %-46s killed by %s\n' "$name" "$want"; return
  fi
  printf '  🔴 %-46s WRONG-KILLER: %s (wanted %s)\n' \
    "$name" "$(tr '\n' ',' <<<"$killers")" "$want"
  FAILURES=$((FAILURES+1))
}

echo "== baseline =="
base="$(failing)"
if [ -n "$base" ]; then
  echo "  🔴 the UNMUTATED tree is already failing: $(tr '\n' ',' <<<"$base")"
  echo "     every row below would be meaningless. Fix that first."
  exit 2
fi
echo "  ok unmutated tree is green"

echo
echo "== the gate's MECHANISM =="
run "gate command -> echo 0" \
    test_the_gate_pins_its_MECHANISM_not_only_its_decision "$SKILL" \
    'git log --numstat --format= --remerge-diff <the sha you audited THAT round>..HEAD --not <base>' \
    'echo 0'
run "range reverted to the cumulative two-dot" \
    test_the_gate_pins_its_MECHANISM_not_only_its_decision "$SKILL" \
    'git log --numstat --format= --remerge-diff <the sha you audited THAT round>..HEAD --not <base>' \
    'git diff --numstat <first-audited-sha>..HEAD'
run "classifier method -> just use the pathspec" \
    test_the_gate_pins_its_MECHANISM_not_only_its_decision "$SKILL" \
    'read the list and name each one
payload or scaffolding' \
    'just run the pathspec and trust it'
run "fail-safe inverted (ambiguous counts as zero)" \
    test_the_gate_pins_its_MECHANISM_not_only_its_decision "$SKILL" \
    '**Ambiguous is not zero**: the gate does not fire, and the ladder continues.' \
    '**Ambiguous counts as zero**: the gate fires, and the ladder stops.'
run "decision reworded TWO -> THREE" \
    test_the_attribution_gate_is_stated_with_its_not_a_cap_qualifier "$SKILL" \
    '**Two consecutive rounds whose fixes' \
    '**Three consecutive rounds whose fixes'

echo
echo "== what an operator ACTS on =="
run "'rollback' shaved out of the Gaps item" \
    test_the_operator_instructions_the_gate_depends_on_are_pinned "$SKILL" \
    'migrations, rollback.' 'migrations.'
run "whole 9-item checklist gutted" \
    test_the_operator_instructions_the_gate_depends_on_are_pinned "$SKILL" \
    '**Audit for:**' '**Audit for:** whatever seems off. ORIGINAL FOLLOWS:'
run "cp -a .git hazard deleted" \
    test_the_operator_instructions_the_gate_depends_on_are_pinned "$SKILL" \
    ' — **`rm -f <copy>/.git` first**, since a worktree'"'"'s is a FILE pointing at the
real git dir, so a commit in the copy lands on your branch —' ','
run "cross-repo worktree caveat inverted" \
    test_the_operator_instructions_the_gate_depends_on_are_pinned "$SKILL" \
    '🔴 `isolation:
  "worktree"` worktrees the **cwd'"'"'s** repo, not the PR'"'"'s' \
    'Use `isolation: "worktree"` for any repo'
run "stderr-capture rule inverted" \
    test_the_operator_instructions_the_gate_depends_on_are_pinned "$SKILL" \
    'Keep stderr on the terminal — folding it into the sum with
`2>&1` makes the one loud failure invisible.' \
    'Fold stderr into the sum with `2>&1` so no failure escapes the count.'

echo
echo "== the EVIDENCE the rules rest on =="
run "shape-A row inverted (argues for the banned flag)" \
    test_the_attribution_measurement_survives_where_the_skill_routes_to_it "$EVID" \
    '| 1 line, and it is a TEST ⇒ 0 payload | **201** | 1 | 1 |' \
    '| 1 line, and it is a TEST ⇒ 0 payload | 1 | 1 | **201** |'
run "shape-B/C lesson tail inverted" \
    test_the_attribution_measurement_survives_where_the_skill_routes_to_it "$EVID" \
    '`git show --numstat <merge>` is not the remedy either' \
    '`git show --numstat <merge>` is the remedy'
run "churn row reverted to the stale 1,002" \
    test_the_attribution_measurement_survives_where_the_skill_routes_to_it "$EVID" \
    '**1,051**' '**1,002**'

echo
echo "== controls (must kill NOTHING) =="
run "re-wrap a pinned sentence across new line breaks" SURVIVES "$SKILL" \
    'Rounds continue **only** while the previous round produced a finding that required a fix. The first' \
    'Rounds continue **only** while the previous round
produced a finding that required a fix. The first'
run "edit prose nobody pinned" SURVIVES "$SKILL" \
    '## What to do' '## What to do (read this first)'

echo
if [ "$FAILURES" -eq 0 ]; then
  echo "✅ $ROWS row(s), all as expected"
  exit 0
fi
echo "🔴 $FAILURES of $ROWS row(s) not as expected"
exit 1
