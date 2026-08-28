#!/usr/bin/env bash
# Mutation battery for the audit-ladder PROSE guards —
# `claude/skills/audit-pr/SKILL.md`, its `reference/round-ladder-evidence.md`,
# and the pins in `scripts/tests/test_audit_ladder_stop_rule.py`.
#
# Not run by CI. An author/reviewer instrument, kept IN THE TREE so
# "mutation-verified" can be RE-DERIVED instead of believed.
#
#   nix develop ~/workspace/devrc -c bash scripts/tests/mutants-audit-ladder.sh
#
#   (a bare `bash …` works only where pytest is already on PATH; this host's
#   `.envrc` is `use opencode`, which does not put it there.) Exit 0 only if
#   every row is as expected.
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
# `mktemp -d` copy built by naming FIVE INDIVIDUAL FILES — that selective copy,
# not the assertion below it, is what keeps a `.git` out. The assertion is an
# INVARIANT GUARD and is labelled as one rather than counted as coverage: it
# cannot fire today and has never been watched to. It earns its two lines only
# against the future refactor that replaces the file list with `cp -a "$SRC"`,
# which would carry a worktree's `.git` POINTER FILE and let a git command
# inside the copy act on the real repository — a hazard the skill under test
# now warns about, and the reason to leave a tripwire on the road not taken.
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
# COVERAGE IS DELIBERATELY PARTIAL, AND HERE IS WHAT IS NOT COVERED. Two kinds
# of row are here: the ones whose SURVIVAL was measured during #900 — each green
# until a pin was added for it — and the two RELOCATIONS at the end, which never
# survived anything and are here for the opposite reason: they are the only rows
# that can show the RELATIONSHIP assertions execute at all, since a relocation
# leaves every whole-string pin byte-identical. (An earlier version of this
# paragraph excluded "the M-series against `claude/RULES.md`" by name while the
# harness ran M4 two hundred lines below. Fixed; the rows are the authority.)
#
# NOT here: rows that only ever CONFIRMED an existing pin, and the evidence-file
# truncation ladder. Run those from the module's docstring matrix if you are
# changing those guards. This list is the executable one — count it here rather
# than trusting a total written anywhere else, including in that docstring.
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
RULES="$ROOT/claude/RULES.md"
cp -a "$SKILL" "$T/skill.orig"
cp -a "$EVID"  "$T/evid.orig"
cp -a "$RULES" "$T/rules.orig"
restore() {
  cp -a "$T/skill.orig" "$SKILL"
  cp -a "$T/evid.orig"  "$EVID"
  cp -a "$T/rules.orig" "$RULES"
}

FAILURES=0
ROWS=0

# 🔴 Read the CONTENT, never an exit code. A suite that never ran yields zero
# FAILED lines — i.e. "clean" — so a harness wired to nothing would score every
# mutant SURVIVED and every control ok. The floor catches COLLAPSE, not growth.
# `run-tests.sh`'s own floor formula is `m - min(50, max(1, m/20))`; at m=11
# that is 10. It catches COLLAPSE, not growth — losing one test still clears it,
# losing two reports HARNESS BROKE.
MIN_TESTS=10
failing() {
  local out n f total
  # stderr is CAPTURED, not discarded: the commonest way to get "0 tests ran" on
  # this host is running outside `nix develop` (`.envrc` is `use opencode`, so a
  # loaded direnv has no pytest), and discarding stderr turns that one-line
  # diagnosis into a headline that blames the TREE. Checked before the count.
  out="$(cd "$ROOT" && PYTHONDONTWRITEBYTECODE=1 python3 -m pytest "$SUITE" \
    -q --no-header --tb=no -p no:cacheprovider 2>&1)"
  if grep -q "No module named pytest" <<<"$out"; then
    echo "__HARNESS_BROKE__ pytest is not on PATH — run it as" \
         "\`nix develop ~/workspace/devrc -c bash" \
         "scripts/tests/mutants-audit-ladder.sh\`, not in a bare shell"
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

# move <file> <python-expr-on-t> — for mutants that RELOCATE a block with its
# text byte-identical. Those are the reachability controls: every whole-string
# pin stays green, so only an assertion that actually EXECUTES can see them.
move() {
  python3 - "$1" "$2" <<'PYEOF'
import pathlib, sys
p = pathlib.Path(sys.argv[1]); t = p.read_text()
ns = {"t": t}
exec("out = " + sys.argv[2], ns)          # noqa: S102 — harness, not product
if ns["out"] == t:
    sys.exit(3)
p.write_text(ns["out"])
PYEOF
}

run_move() { # run_move <name> <expect> <file> <python-expr>
  local name="$1" want="$2" file="$3" expr="$4"
  ROWS=$((ROWS+1))
  if ! move "$file" "$expr"; then
    printf '  🔴 %-46s MUTATION DID NOT APPLY — result meaningless\n' "$name"
    FAILURES=$((FAILURES+1)); restore; return
  fi
  local killers; killers="$(failing)"
  restore
  if grep -q __HARNESS_BROKE__ <<<"$killers"; then
    printf '  🔴 %-46s HARNESS BROKE — %s\n' "$name" "$killers"
    FAILURES=$((FAILURES+1)); return
  fi
  if [ -z "$killers" ]; then
    printf '  🔴 %-46s SURVIVED — no test failed\n' "$name"
    FAILURES=$((FAILURES+1)); return
  fi
  # 🔴 EXCLUSIVE, not "among" -- but in TWO steps, because one comparison
  # collapses two states that need opposite responses. These rows exist to show
  # that ONE assertion (the relationship one) executes; the docstring quotes
  # them as "relationship pin ONLY".
  #   * named test ABSENT      -> WRONG-KILLER: your relationship pin is DEAD,
  #                               something else killed the mutant.
  #   * named test PLUS others -> NOT EXCLUSIVE: the pin fired, but the probe no
  #                               longer isolates it; re-scope the mutant.
  # A single equality test reports the first as the second, which routes the
  # operator away from the exact failure these rows exist to catch. Measured:
  # `grep -qx` alone printed ok for named-plus-neighbour (the round-2 bug);
  # equality alone printed NOT EXCLUSIVE for named-absent (the round-3 bug).
  if ! grep -qx "$want" <<<"$killers"; then
    printf '  🔴 %-46s WRONG-KILLER: %s (wanted %s)\n' \
      "$name" "$(tr '\n' ',' <<<"$killers" | sed 's/,$//')" "$want"
    FAILURES=$((FAILURES+1)); return
  fi
  if [ "$killers" != "$want" ]; then
    printf '  🔴 %-46s NOT EXCLUSIVE: %s (wanted %s alone)\n' \
      "$name" "$(tr '\n' ',' <<<"$killers" | sed 's/,$//')" "$want"
    FAILURES=$((FAILURES+1)); return
  fi
  printf '  ok %-46s killed by %s ALONE\n' "$name" "$want"
}

run() { # run <name> <expect: test node name | SURVIVES> <file> <old> <new>
        # scores on "the named test is AMONG the killers" and PRINTS any extras,
        # because these rows do not claim exclusivity -- only `run_move` does.
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
    local extra; extra="$(grep -vx "$want" <<<"$killers" | tr '\n' ',' | sed 's/,$//')"
    if [ -n "$extra" ]; then
      printf '  ok %-46s killed by %s (also: %s)\n' "$name" "$want" "$extra"
    else
      printf '  ok %-46s killed by %s\n' "$name" "$want"
    fi
    return
  fi
  printf '  🔴 %-46s WRONG-KILLER: %s (wanted %s)\n' \
    "$name" "$(tr '\n' ',' <<<"$killers" | sed 's/,$//')" "$want"
  FAILURES=$((FAILURES+1))
}

echo "== baseline =="
base="$(failing)"
if grep -q __HARNESS_BROKE__ <<<"$base"; then
  # Not the tree's fault — say which, because the previous version printed
  # "the UNMUTATED tree is already failing" at somebody whose only mistake was
  # the shell they ran in.
  echo "  🔴 THE HARNESS could not run: ${base#__HARNESS_BROKE__ }"
  echo "     Nothing was measured. This says nothing about the tree."
  exit 2
fi
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
# The assembler router. The DELETION is the obvious mutant and the weak one;
# this inverts the REFUSAL instead, leaving the router itself byte-identical.
# That is the reading a reader would accept ("it warns, fine") and it describes a
# different tool: a delta re-audit with no claims block silently becomes a blind
# full audit, which then reads as covered. The whole-string pin is what catches
# it — a keyword guard on "audit-dispatch.py" would stay green.
run "assembler router's REFUSAL downgraded to a warning" \
    test_the_operator_instructions_the_gate_depends_on_are_pinned "$SKILL" \
    '**A delta round with no parseable block is REFUSED.**' \
    'A delta round with no parseable block warns and proceeds.'
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
echo "== REACHABILITY: relocations that leave every string pin byte-identical =="
# 🔴 These two are the rows the module's docstring marks as the reachability
# controls, and they were the gap this harness shipped with: without them the
# two RELATIONSHIP assertions -- the ones that check WHERE a rule sits, not that
# its words exist -- had no re-derivable evidence that they execute at all.
run_move "stop clause moved to its OWN bullet in RULES.md" \
    test_the_stop_rule_shares_a_bullet_with_the_rule_it_bounds "$RULES" \
    't.replace("🔴 **A CLEAN round ENDS the ladder", "\n- 🔴 **A CLEAN round ENDS the ladder", 1)'
run_move "ATTRIBUTION section moved ABOVE the stop rule" \
    test_the_attribution_gate_comes_after_the_rule_it_bounds "$SKILL" \
    't[:t.index("### 🔴 A clean round ENDS the ladder.")] + t[t.index("### 🔴 ATTRIBUTION: a round that changes no PAYLOAD"):t.index("## Mutation testing:")] + t[t.index("### 🔴 A clean round ENDS the ladder."):t.index("### 🔴 ATTRIBUTION: a round that changes no PAYLOAD")] + t[t.index("## Mutation testing:"):]'

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
