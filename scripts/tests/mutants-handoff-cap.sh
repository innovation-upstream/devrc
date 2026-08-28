#!/usr/bin/env bash
# Mutation battery for rules (i) and (j) of `scripts/lib/handoff_doc.py` —
# one-doc-per-effort, and a forcing function per ranked next-step.
#
# Not run by CI. An author/reviewer instrument, kept IN THE TREE so
# "mutation-verified" can be RE-DERIVED instead of believed.
#
#   bash scripts/tests/mutants-handoff-cap.sh        # exit 0 only if ALL ok
#
# Follows the convention `mutants-claim-work.sh` / `mutants-dead-guard.sh`
# established, and for the reasons documented there:
#
# 🔴 IT NEVER TOUCHES YOUR WORKING TREE. Everything is mutated inside a
# `mktemp -d` copy.
#
# 🔴 EACH MUTANT NAMES THE TEST THAT MUST KILL IT. "A test failed" is not enough:
# these guards sit in one `main()` in a fixed order, which is exactly the shape
# where a mutant dies to the NEXT guard's error and is scored covered while its
# own assertion is unreachable. A mutant killed only by some other test reports
# 🔴 WRONG-KILLER, not ok.
#
# 🔴 EACH MUTATION IS DIFFED AGAINST THE ORIGINAL BEFORE IT RUNS — a `sed` that
# silently fails to match reports the UNMUTATED file's behaviour, i.e. "the guard
# held", the most flattering possible wrong answer.
#
# 🔴 MUTATIONS ARE ISOLATED TO THE NARROWEST EXPRESSION THAT CAN BE WRONG. A
# mutant that removes a guard TOGETHER WITH ITS ENCLOSING CONDITION proves
# nothing about the guard and dies for the wrong reason — so, for example, the
# `--new-effort` row mutates ONLY `not args.new_effort` and leaves
# `not doc.exists()` alone, and vice versa. Both halves get their own row.
#
# 🔴 THREE CONTROLS, ALL MANDATORY:
#   * the unmutated BASELINE must be green (else every row is meaningless);
#   * a `SURVIVES` row — a behaviour-free edit that must NOT kill anything, which
#     proves the harness keys on BEHAVIOUR and not on the file's text;
#   * `already-caught-positive-control`, a mutant to a PRE-EXISTING guard known
#     to be covered, so a harness wired to nothing cannot report a clean sweep.
#
# 🔴 PYTHONDONTWRITEBYTECODE=1. CPython validates a cached module on source
# mtime-in-whole-SECONDS + size, so a same-LENGTH edit landing in the same second
# as the last import is invisible: the test imports the ORIGINAL bytecode and the
# mutant is scored SURVIVED without ever executing. Several rows below ARE
# same-length edits. This is not optional.
set -uo pipefail
CDPATH=
D="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
SRC="$(cd "$D/../.." && pwd)"

T="$(mktemp -d /tmp/handoff-cap-mut-XXXXXX)"
trap 'rm -rf "$T"' EXIT
ROOT="$T/tree"

mkdir -p "$ROOT/scripts/tests" "$ROOT/claude/skills/handoff/reference"
cp -a "$SRC/scripts/lib" "$ROOT/scripts/"
cp -a "$SRC/scripts/testlib" "$ROOT/scripts/"
cp -a "$SRC/scripts/tests/test_handoff_doc.py" "$ROOT/scripts/tests/"
cp -a "$SRC/scripts/tests/conftest.py" "$ROOT/scripts/tests/" 2>/dev/null
# `test_the_two_call_sites_agree_over_the_COMMITTED_matrix` reads the near-miss
# fixture. Found by the BASELINE control, which went red rather than scoring a
# sweep against a suite that was already failing for an unrelated reason.
mkdir -p "$ROOT/scripts/tests/fixtures"
cp -a "$SRC/scripts/tests/fixtures/." "$ROOT/scripts/tests/fixtures/"
# The suite reads the skill body (TestSkillAndModuleAgree) and its reference dir.
cp -a "$SRC/claude/skills/handoff/SKILL.md" "$ROOT/claude/skills/handoff/"
cp -a "$SRC/claude/skills/handoff/reference/." \
      "$ROOT/claude/skills/handoff/reference/"

# 🔴 A `cp -a` of a WORKTREE would carry its `.git` POINTER FILE, and a git
# command inside the copy would then act on the REAL repository. Nothing above
# copies `.git`; this asserts it rather than assuming it.
if [ -e "$ROOT/.git" ]; then
  echo "🔴 the copy carries a .git — refusing to run"; exit 2
fi
find "$ROOT" -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null

MOD="$ROOT/scripts/lib/handoff_doc.py"
SUITE="$ROOT/scripts/tests/test_handoff_doc.py"
cp -a "$MOD" "$T/mod.orig"
restore() { cp -a "$T/mod.orig" "$MOD"; }

FAILURES=0
ROWS=0

# failed test names, one per line. Read the CONTENT — never an exit code.
# 🔴 A SUITE THAT NEVER RAN YIELDS ZERO `FAILED` LINES, i.e. "clean", so a
# harness wired to nothing would score every mutant SURVIVED and every SURVIVES
# control ok. Count the tests that ran and refuse below a floor. The floor
# catches COLLAPSE, not growth — deliberately far under the real count (222).
MIN_TESTS=180
failing() {
  local out n f total
  out="$(PYTHONDONTWRITEBYTECODE=1 python3 -m pytest "$SUITE" -q --no-header --tb=no \
    -p no:cacheprovider 2>/dev/null)"
  n="$(sed -n 's/^\([0-9]*\) passed.*/\1/p;s/^[0-9]* failed, \([0-9]*\) passed.*/\1/p' <<<"$out" | tail -1)"
  f="$(sed -n 's/^\([0-9]*\) failed.*/\1/p' <<<"$out" | tail -1)"
  total=$(( ${n:-0} + ${f:-0} ))
  if [ "$total" -lt "$MIN_TESTS" ]; then
    echo "__HARNESS_BROKE__ only $total test(s) ran (floor $MIN_TESTS)"
    return
  fi
  # Parametrised ids are `name[param]`; the class stops at `[`, which collapses
  # every case of one test onto its base name — what `want` is spelled as.
  sed -n 's/^FAILED [^:]*::\([A-Za-z0-9_]*\)::\([A-Za-z0-9_]*\).*/\2/p;s/^FAILED [^:]*::\([A-Za-z0-9_]*\)$/\1/p' <<<"$out" | sort -u
}

run() { # run <name> <expect: a test name | SURVIVES> <sed-expr>
  local name="$1" want="$2" expr="$3"
  ROWS=$((ROWS+1))
  sed "$expr" "$MOD" > "$T/m" 2>/dev/null
  if cmp -s "$MOD" "$T/m"; then
    printf '  🔴 %-44s MUTATION DID NOT APPLY — result meaningless\n' "$name"
    FAILURES=$((FAILURES+1)); return
  fi
  cat "$T/m" > "$MOD"
  local killers; killers="$(failing)"
  restore
  if grep -q __HARNESS_BROKE__ <<<"$killers"; then
    printf '  🔴 %-44s HARNESS BROKE — %s\n' "$name" "$killers"
    FAILURES=$((FAILURES+1)); return
  fi
  if [ "$want" = SURVIVES ]; then
    if [ -z "$killers" ]; then
      printf '  ok %-44s SURVIVED as required (control)\n' "$name"; return
    fi
    printf '  🔴 %-44s CONTROL KILLED by %s — not measuring behaviour\n' \
      "$name" "$(tr '\n' ',' <<<"$killers")"; FAILURES=$((FAILURES+1)); return
  fi
  if [ -z "$killers" ]; then
    printf '  🔴 %-44s SURVIVED — no test failed\n' "$name"
    FAILURES=$((FAILURES+1)); return
  fi
  if grep -qx "$want" <<<"$killers"; then
    # Extras are REPORTED, not swallowed: a mutant that also kills half the suite
    # is usually a mutation wider than the guard it claims to isolate.
    local extra; extra="$(grep -vx "$want" <<<"$killers" | tr '\n' ',' | sed 's/,$//')"
    if [ -n "$extra" ]; then
      printf '  ok %-44s killed by %s  (also: %s)\n' "$name" "$want" "$extra"
    else
      printf '  ok %-44s killed by %s\n' "$name" "$want"
    fi
    return
  fi
  printf '  🔴 %-44s WRONG-KILLER — died to: %s (wanted %s)\n' \
    "$name" "$(tr '\n' ',' <<<"$killers")" "$want"; FAILURES=$((FAILURES+1))
}

printf 'mutating a COPY at %s (your worktree is untouched)\n' "$ROOT"
printf 'baseline (must be empty): '
b="$(failing)"; [ -z "$b" ] && echo "clean" || { echo "🔴 ALREADY RED: $b"; exit 1; }

printf '\n== rule (i-a): a dated topic is a PER-SESSION doc (must be KILLED) ==\n'
# The guard deleted outright: the predicate can no longer say yes to anything.
run 'date-predicate-always-none' test_every_dated_spelling_in_the_corpus_is_refused \
  's|return m.group(0) if m else None|return None|'
# ONE ARM of the pattern, not the whole thing — `q3-2026-cleanup` is a real
# corpus spelling and the ISO arms alone are blind to it.
run 'date-bare-year-arm-removed' test_every_dated_spelling_in_the_corpus_is_refused \
  's/|(?<!\\d)(?:19|20)\\d{2}(?!\\d)//'
# ⚠ A `date-year-month-arm-removed` row sat here and SURVIVED — correctly. The
# arm was redundant with the bare-year one (both refuse `remix-2026-07-session`,
# differing only in the token quoted), so no test could tell it apart. The arm
# was DELETED rather than the row being marked SURVIVES_BY_DESIGN: a branch no
# test can reach is not a documented redundancy, it is dead code.
# The OVER-BROAD direction. Without a row here, a pattern that ate `h2-planning`
# and `s3-403-triage` would sweep clean — the narrowness assertion would never
# have been shown reachable.
run 'date-pattern-eats-any-digits' test_ordinary_numbers_in_a_slug_are_not_dates \
  's|_TOPIC_DATE = re.compile(r"[^"]*")|_TOPIC_DATE = re.compile(r"\\d")|'

printf '\n== rule (i-b): a second doc for an existing effort (must be KILLED) ==\n'
# 🔴 THE TWO HALVES OF THE CONDITION GET SEPARATE ROWS. Mutating both together
# would delete the guard with its enclosing condition and prove nothing about
# either half.
run 'new-doc-guard-never-fires' test_a_new_topic_is_refused_and_lists_what_exists \
  's|if not doc.exists() and not args.new_effort:|if False and not args.new_effort:|'
run 'new-effort-assertion-ignored' test_new_effort_lands_it \
  's|if not doc.exists() and not args.new_effort:|if not doc.exists() and True:|'
# The LIST is the half that makes the refusal compliable. A refusal naming no
# existing doc is a block with no way past it but the flag.
run 'existing-docs-list-suppressed' test_a_new_topic_is_refused_and_lists_what_exists \
  's|^    docs = (repo / "claudedocs").glob("handoff-\*.md")|    docs = []|'

printf '\n== rule (j): a ranked item names a forcing function (must be KILLED) ==\n'
run 'unforced-refusal-never-fires' test_an_untagged_item_is_refused_and_writes_nothing \
  's|^    if unforced:|    if False:|'
# 🔴 THE ALLOWLIST ROW. Adding the exact label a self-generated item reaches for
# is the mutation this rule exists to survive.
run 'vocabulary-admits-followup' test_a_kind_outside_the_vocabulary_is_refused \
  's|"incident", "user", "gate"|"followup", "incident", "user", "gate"|'
# Isolated from the row above: the membership TEST rather than the SET. An
# untagged item (kind None) is still refused under this mutant, so only the
# unknown-kind assertion can kill it.
run 'declared-means-merely-present' test_a_kind_outside_the_vocabulary_is_refused \
  's|return self.kind in FORCING_KINDS|return self.kind is not None|'
run 'external-set-includes-none' test_none_is_in_the_vocabulary_but_not_in_the_external_set \
  's|FORCING_KINDS - {"none"}|FORCING_KINDS \| frozenset()|'
# ⚠ RE-AIMED. This row used to mutate `ranked_items`' own `_unfenced` loop; the
# fence check moved into `_item_blocks` when the search widened to the whole
# item, and the `MUTATION DID NOT APPLY` control caught it on the first run
# rather than scoring the stale row `ok` against an unmutated file.
run 'ranked-items-not-fence-aware' test_a_numbered_line_inside_a_FENCE_is_not_a_ranked_item \
  's|visible = {idx for idx, _ln in _unfenced(section_body)}|visible = set(range(len(all_lines)))|'
run 'ranked-items-ignore-the-heading' test_items_outside_a_next_steps_heading_are_not_asked \
  's|if not heading_text(heading).lower().startswith(NEXT_STEPS_PREFIX):|if False:|'
run 'nested-sub-items-counted-as-ranks' test_a_nested_numbered_line_is_not_a_rank \
  's|r"\^ {0,3}(\\d+)\[.)\]|r"^ *(\\d+)[.)]|'
run 'self-generated-block-suppressed' test_forcing_none_is_ACCEPTED_and_reported \
  's|none_items = \[i for i in items if i.kind == "none"\]|none_items = []|'

printf '\n== rule (j): the field is found on the WHOLE ITEM (must be KILLED) ==\n'
# 🔴 THE REVERT ROW. Collapses the item block back to its numbered line — the
# defect this change fixed. Narrowest expression that can be wrong: the walk's
# upper bound, leaving the boundary logic below it untouched so the next row
# still isolates that half.
run 'block-collapsed-to-the-numbered-line' \
  test_the_observed_refusal_of_two_correctly_tagged_items_is_gone \
  's|for i in range(start + 1, limit):|for i in range(start + 1, start + 1):|'
# 🔴 THE BOUNDARY, ISOLATED FROM THE WALK. Removing only the break gives the
# NAIVE block — numbered line to the next numbered line — which silently tags
# the last item from a copied boilerplate paragraph. Without this row, "why not
# just run to the next item" is an unanswered question in the diff.
run 'naive-block-boundary' test_trailing_boilerplate_does_not_tag_the_last_item \
  's|if blanked and not line.startswith((" ", "\\t")):|if False:|'
# The other side of that boundary, and ISOLATED FROM THE ROW ABOVE: keep the
# blank-line test, drop only the INDENT test, so a blank line ends the item
# whatever follows it. A two-paragraph item is a shape the corpus has.
run 'a-blank-line-ends-the-item' \
  test_an_indented_paragraph_after_a_blank_line_is_still_the_item \
  's|if blanked and not line.startswith((" ", "\\t")):|if blanked:|'
# The fenced lines must stay OUT of the item's own text. Feeding them back in
# would make a pasted sample a declaration.
run 'fenced-lines-counted-as-the-item' \
  test_a_field_inside_a_FENCE_still_does_not_count_and_says_why \
  's|                hidden.append(line)|                own.append(line)|'
# …and the diagnosis half of the same case: the field IS in the file, so the
# refusal must not claim absence.
run 'fenced-field-not-diagnosed' \
  test_a_field_inside_a_FENCE_still_does_not_count_and_says_why \
  's|                    any(_FORCING.search(ln) for ln in hidden),|                    False,|'
# 🔴 THE MESSAGE HALF. `_FORCING_ATTEMPT` never fires ⇒ a near-miss is reported
# as `[no forcing: field]` under a remedy the author already satisfied, which is
# the unrecoverable refusal this whole change exists to end.
run 'near-miss-never-detected' \
  test_a_near_miss_is_REFUSED_and_NAMED_never_called_absent \
  's|next((ln.strip() for ln in own if _FORCING_ATTEMPT.search(ln)), None),|None,|'
# The admitted spelling. `**forcing:** gate` is accepted BECAUSE the vocabulary
# is closed; deleting the markup class is the decision reverted.
run 'markup-between-key-and-colon-rejected' \
  test_an_accepted_spelling_on_a_continuation_line_counts \
  's|^_MARKUP = r"\[\*_`~\]{0,3}"|_MARKUP = r""|'

printf '\n== controls ==\n'
# 🔴 POSITIVE CONTROL — a mutant to a PRE-EXISTING guard (rule d) that the suite
# is already known to catch. If this row ever reports SURVIVED, the harness is
# wired to nothing and every `ok` above is worthless.
run 'already-caught-positive-control' test_no_advance_is_still_4_on_an_undated_existing_doc \
  's|return advanced.strip().lower() not in NO_ADVANCE_SENTINELS|return True|'
# 🔴 BEHAVIOUR-FREE CONTROL — a comment reword. Must kill NOTHING, which is what
# proves the rows above key on behaviour rather than on the file's bytes.
run 'comment-reword-control' SURVIVES \
  's|# --- rule (j): a ranked item names an external forcing function|# --- rule j: ranked item forcing function (reworded comment)|'

printf '\n%d row(s), %d failure(s)\n' "$ROWS" "$FAILURES"
[ "$FAILURES" -eq 0 ] || exit 1
