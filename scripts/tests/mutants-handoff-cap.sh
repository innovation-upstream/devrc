#!/usr/bin/env bash
# Mutation battery for rules (i) and (j) of `scripts/lib/handoff_doc.py` —
# one-doc-per-effort, and a forcing function per ranked next-step — plus the
# EXIT-CODE CONTRACT those rules return through, and the mainline-copy predicate
# rule (i) consults. THREE sections were added closing #1093/#1115 — the codes,
# the predicate, and the round-4 message row with its negative control.
#
# 🔴 THEY ARE NOT ALL THE SAME KIND OF ROW. This change adds NINE rows, and
# reading them as one kind gets FIVE of them wrong. MEASURED by running each
# mutant against the merge-base `093a63db`, a 295-test tree:
#
#   REGRESSION COVERAGE (4) — the mutant SURVIVED the whole suite at the base,
#   so a NEW assertion is what catches it:
#     stale-base-code-renumbered · doc-per-effort-code-renumbered ·
#     unforced-code-renumbered · rule-i-broadened-to-bare-stale   (295 passed)
#
#   INVARIANT GUARDS (3) — the mutant was ALREADY killed at the base by
#   pre-existing tests, so the row does not show the suite would otherwise
#   miss it:
#     one-exit-constant-silently-vanishes      (8 failed at `093a63db`)
#     mainline-measured-from-the-wrong-text    (2 failed at `093a63db`)
#     stale-clone-treated-as-new-effort        (1 failed at `093a63db`)
#
#   DIAGNOSTIC-QUALITY (2) — killed at the base by the WRONG MESSAGE, which is
#   the whole of #1093.1, so `ok` means the failure NAMES the real defect:
#     round-4-regression-message  +  round-4-negative-control
#
# 🔴 "INVARIANT GUARD" IS A CLAIM ABOUT THE SUITE, NOT ABOUT THIS HARNESS — and
# an earlier wording conflated the two, which is the error this note exists to
# stop repeating. `_run` demands the row's OWN NAMED killer, so deleting the new
# assertion does NOT leave the row reporting `ok`: it reports 🔴 WRONG-KILLER.
# MEASURED on the head tree with `assert len(codes) == 9` deleted and the
# vanishing-constant mutant applied — 8 tests fail and
# `test_no_two_exit_constants_share_a_value` is NOT among them, so `grep -qx`
# misses and the row goes red. So these three rows DO bind their new assertions;
# what they do not do is prove the suite would have missed the mutant without
# them. `claude/RULES.md` asks for that label, not for the row to be deleted.
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
# 🔴 A THIRD PRISTINE COPY. Every row before #1093.1 mutated the module or the
# skill only, so the SUITE never needed one. The round-4 negative control
# restores the PRE-FIX ASSERTION ORDER by editing the suite, and a row that
# edits a file it cannot put back would silently poison every row after it.
cp -a "$SUITE" "$T/suite.orig"
# 🔴 THE SKILL BODY IS A SECOND MUTABLE ARTEFACT, AND IT HAS TO BE. Rule (j)'s
# refusal is only actionable through SKILL.md's step-5 legend — the module's own
# comment calls that legend "the executor's only map from a marker to what to do
# about it" — so a guard on the legend is a guard on behaviour the executor gets,
# and `run` alone could never show one reachable: every row before this mutated
# the module only. MEASURED: at `e34ed6ef` the legend told the executor to do the
# exact thing `FENCED_FIELD_REMEDY`'s comment says is harmful, and the whole
# suite was green.
SKILL="$ROOT/claude/skills/handoff/SKILL.md"
cp -a "$SKILL" "$T/skill.orig"

FAILURES=0
ROWS=0

# failed test names, one per line. Read the CONTENT — never an exit code.
# 🔴 A SUITE THAT NEVER RAN YIELDS ZERO `FAILED` LINES, i.e. "clean", so a
# harness wired to nothing would score every mutant SURVIVED and every SURVIVES
# control ok. Count the tests that ran and refuse below a floor. The floor
# catches COLLAPSE, not growth — deliberately far under the real count (271 as
# of 2026-08-28; 222 → 237 when the fence/underscore round added 15, → 261 when
# the round-3 fixes added the legend seam and the anchor/admission pins, → 268
# when round 4 replaced the anchor params with the full 5x2 position grid, → 271
# when round 5 pinned the two round-4 prose corrections that were still
# revertible with the suite green, plus a locator control for the new one).
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
  # 🔴 THREE PATTERNS, AND THE THIRD IS A FIX. The first two are class::method
  # and a BARE module-level test — the second END-ANCHORED, so a module-level
  # PARAMETRISED test (`name[param]`) matched NEITHER: no second `::` for the
  # first, and `[param]` after the name for the second. Such a failure was
  # therefore invisible, and its mutant scored SURVIVED. MEASURED 2026-08-31 on
  # `leading-decoration-rejected`: the harness said SURVIVED while running the
  # killing test by hand failed 3 of its 4 cases.
  sed -n 's/^FAILED [^:]*::\([A-Za-z0-9_]*\)::\([A-Za-z0-9_]*\).*/\2/p;s/^FAILED [^:]*::\([A-Za-z0-9_]*\)$/\1/p;s/^FAILED [^:]*::\([A-Za-z0-9_]*\)\[.*/\1/p' <<<"$out" | sort -u
}

# 🔴 ONE IMPLEMENTATION, TWO TARGETS. `run` mutates the module, `run_skill` the
# skill body; everything else — the DID-NOT-APPLY diff, the harness floor, the
# WRONG-KILLER check, the restore — is shared, because a second copy of this
# logic is the shape `claude/RULES.md` says regenerates the same bug at N sites.
_run() { # _run <file> <pristine-copy> <name> <want> <sed-expr>
  local file="$1" orig="$2" name="$3" want="$4" expr="$5"
  ROWS=$((ROWS+1))
  sed "$expr" "$file" > "$T/m" 2>/dev/null
  if cmp -s "$file" "$T/m"; then
    printf '  🔴 %-44s MUTATION DID NOT APPLY — result meaningless\n' "$name"
    FAILURES=$((FAILURES+1)); return
  fi
  cat "$T/m" > "$file"
  local killers; killers="$(failing)"
  cp -a "$orig" "$file"
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

run() {       # run <name> <expect: a test name | SURVIVES> <sed-expr>
  _run "$MOD" "$T/mod.orig" "$@"
}
run_skill() { # run_skill <name> <expect: a test name | SURVIVES> <sed-expr>
  _run "$SKILL" "$T/skill.orig" "$@"
}

printf 'mutating a COPY at %s (your worktree is untouched)\n' "$ROOT"
printf 'baseline (must be empty): '
b="$(failing)"; [ -z "$b" ] && echo "clean" || { echo "🔴 ALREADY RED: $b"; exit 1; }

printf '\n== rule (i-a): a dated topic is a PER-SESSION doc (must be KILLED) ==\n'
# The guard deleted outright: the predicate can no longer say yes to anything.
run 'date-predicate-always-none' test_every_dated_spelling_in_the_corpus_is_refused \
  's|return m.group(0) if m else None|return None|'
# ⚠ RE-ANCHORED, AND ITS SUBJECT CHANGED. A `date-bare-year-arm-removed` row sat
# here anchored on `|(?<!\d)(?:19|20)\d{2}(?!\d)`. That arm was DELETED
# 2026-08-31 (#964 item 1), so the sed now matches nothing and the row reported
# `MUTATION DID NOT APPLY` — the harness's own `cmp -s` control is what made that
# loud rather than a flattering SURVIVED. It is replaced by the row below, which
# names the arm that took its place.
#
# ⚠ A `date-year-month-arm-removed` row also sat here and SURVIVED — correctly.
# The arm was redundant with the bare-year one, so no test could tell it apart,
# and it was DELETED rather than marked SURVIVES_BY_DESIGN: a branch no test can
# reach is not a documented redundancy, it is dead code.
#
# ONE ARM of the pattern, not the whole thing — the COMPACT `20260801` spelling
# is what `date +%Y%m%d` produces, and the hyphenated arm alone is blind to it.
run 'date-compact-arm-removed' test_every_dated_spelling_in_the_corpus_is_refused \
  's/|(?<!\\d)(?:19|20)\\d{6}(?!\\d)//'
# 🔴 THE ARM'S OWN WIDTH, ISOLATED FROM ITS EXISTENCE. `\d{6}` -> `\d{2}` puts
# the DELETED bare-year arm back under a new spelling, so the ten
# `test_ordinary_numbers_in_a_slug_are_not_dates` hazards start refusing again.
# Without this row the deletion is pinned only by the absence of a line, which a
# rewrite can undo without any test noticing.
run 'compact-arm-widened-back-to-a-bare-year' test_ordinary_numbers_in_a_slug_are_not_dates \
  's@(?:19|20)\\d{6}(?!\\d)@(?:19|20)\\d{2}(?!\\d)@'
# The OVER-BROAD direction. Without a row here, a pattern that ate `h2-planning`
# and `s3-403-triage` would sweep clean — the narrowness assertion would never
# have been shown reachable.
run 'date-pattern-eats-any-digits' test_ordinary_numbers_in_a_slug_are_not_dates \
  's|_TOPIC_DATE = re.compile(r"[^"]*")|_TOPIC_DATE = re.compile(r"\\d")|'
# 🔴 THE COST SIDE, PINNED. The deletion is an operator DECISION, so the tests
# that record what it costs must themselves be reachable: put the bare-year arm
# back and `test_a_year_only_slug_is_a_KNOWN_gap` must go red. A "known gap"
# nothing can falsify is a comment, not a guard.
run 'bare-year-arm-restored' test_a_year_only_slug_is_a_KNOWN_gap \
  's@_TOPIC_DATE = re.compile(r"@_TOPIC_DATE = re.compile(r"(?<!\\d)(?:19|20)\\d{2}(?!\\d)|@'

printf '\n== rule (i-b): a second doc for an existing effort (must be KILLED) ==\n'
# 🔴 THE TWO HALVES OF THE CONDITION GET SEPARATE ROWS. Mutating both together
# would delete the guard with its enclosing condition and prove nothing about
# either half.
# 🔴 RE-ANCHORED. devrc#1046 rewrote this line into a parenthesised THREE-term
# condition, so the old single-line anchor matched 0 times and both rows reported
# `MUTATION DID NOT APPLY — result meaningless`. The harness's own `cmp -s` guard
# is what made that loud instead of a false SURVIVED; without it these two rows
# would have read as coverage while testing nothing.
run 'new-doc-guard-never-fires' test_a_new_topic_is_refused_and_lists_what_exists \
  's|if (not doc.exists() and not args.new_effort|if (False and not args.new_effort|'
run 'new-effort-assertion-ignored' test_new_effort_lands_it \
  's|if (not doc.exists() and not args.new_effort|if (not doc.exists() and True|'
# The THIRD term, added by #1046: a doc on the MAINLINE means this is a stale
# clone, not a new effort. Dropping it re-shadows the stale-base refusal — the
# defect that reopened #1046's audit ladder after it had closed.
run 'stale-clone-treated-as-new-effort' test_a_STALE_BASE_in_a_REALISTIC_repo_is_not_reported_as_a_NEW_effort \
  's|and not currency.replaces_mainline_doc("")):|and True):|'
# The LIST is the half that makes the refusal compliable. A refusal naming no
# existing doc is a block with no way past it but the flag.
run 'existing-docs-list-suppressed' test_a_new_topic_is_refused_and_lists_what_exists \
  's|^    docs = (repo / "claudedocs").glob("handoff-\*.md")|    docs = []|'
# 🔴 THE MAINLINE HALF OF THE LIST — #964 item 4. The working-tree glob above and
# this are DIFFERENT sources, so they get different rows: a clone that is behind
# is offered docs it does not have, and suppressing only one of the two still
# leaves the refusal looking complete.
run 'mainline-docs-never-listed' test_a_doc_only_the_MAINLINE_has_is_listed_and_labelled \
  's|name for name in mainline_handoff_docs(repo, currency.base_ref)|name for name in []|'
# The LABEL, isolated from the list. An upstream-only doc offered WITHOUT saying
# so reads as "you have this" — and the session then re-runs with a topic whose
# file is not in its tree.
run 'mainline-only-rows-not-labelled' test_a_doc_only_the_MAINLINE_has_is_listed_and_labelled \
  's|f"    {name}" + (f"    (on {ref} only)" if name in upstream_set else "")|f"    {name}"|'
# 🔴 THE SENTENCE, which is the half that made the list read as complete. A count
# of one CHECKOUT'S glob described as "this repo" is a claim wider than the code.
# ⚠ THE ANCHOR CARRIES THE MESSAGE'S OWN LEADING SPACES. A first version dropped
# the two spaces after `f"` and reported `MUTATION DID NOT APPLY` — caught by the
# harness's `cmp -s` control, which is the only reason it was not scored `ok`.
run 'count-claims-the-whole-repo' test_the_count_says_WORKING_TREE_not_repo \
  's|f"  {relpath} does not exist, and this WORKING TREE already has "|f"  {relpath} does not exist, and this repo already has "|'
# The elision's escape hatch, ONE ROW PER SOURCE — the two lists come from
# different places and no single command enumerates the union, so a message that
# names only one of them is wrong for half the callers. Showing 12 of 92 with no
# way to see the rest is the amplifier that turns the cap into a dead end.
run 'elision-omits-the-LOCAL-enumeration' \
  test_an_ELIDING_list_says_how_to_see_the_rest \
  's@`ls {repo}/claudedocs/@`stat {repo}/claudedocs/@'
# The MAINLINE half, isolated: keep the local command, drop the ref one. A stale
# clone is then told to `ls` a tree that does not have the doc it is being sent
# to find.
run 'elision-omits-the-MAINLINE-source' \
  test_an_ELIDING_list_says_how_to_see_the_rest \
  's@f" lists {ref}'"'"'s" if upstream else ""@f" lists {ref}'"'"'s" if False else ""@'

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
# ⚠ RE-ANCHORED. #964 item 3 replaced this call site's open-coded
# `heading_text(heading).lower().startswith(NEXT_STEPS_PREFIX)` with the one
# owner, so the old anchor matched nothing.
run 'ranked-items-ignore-the-heading' test_items_outside_a_next_steps_heading_are_not_asked \
  's|if not is_next_steps_heading(heading_text(heading)):|if False:|'
# 🔴 THE CONSOLIDATION, REVERTED — #964 item 3. Puts the raw `.startswith` back
# at this call site, byte-for-byte the predicate that disagreed with
# `canonical_prefix` over `## Next  steps`. The seam guard is what must catch it;
# every OTHER heading test agrees under both spellings, which is exactly why the
# duplication survived unnoticed.
run 'heading-test-open-coded-again' test_the_TWO_call_sites_cannot_disagree \
  's|if not is_next_steps_heading(heading_text(heading)):|if not heading_text(heading).lower().startswith(NEXT_STEPS_PREFIX):|'
# The decoration half, ISOLATED from the consolidation above: keep one owner,
# make it stop stripping ornament. `▶ Next steps (ranked)` is a real corpus
# heading whose five items were exempt from rule (j).
run 'heading-decoration-not-stripped' test_the_selector_answers_the_whole_matrix \
  's|^_HEADING_DECORATION = re.compile(r"\^\[\^0-9A-Za-z\]+")|_HEADING_DECORATION = re.compile(r"^$")|'
# 🔴 THE OTHER DIRECTION, and the bound that makes the widening safe: let the
# stripper eat WORD characters and a SYNONYM gets promoted into the canonical
# set. Without this row, `_HEADING_DECORATION` could be widened and every
# behavioural row above would stay green.
run 'decoration-stripper-eats-words' test_decoration_stripping_cannot_promote_a_SYNONYM \
  's|^_HEADING_DECORATION = re.compile(r"\^\[\^0-9A-Za-z\]+")|_HEADING_DECORATION = re.compile(r"^[A-Za-z ]*?(?=next)")|'
# 🔴 RE-AIMED, AND ITS SUBJECT MOVED — #964 item 5. This row used to widen
# `_RANKED_ITEM` to `^ *` and be killed by the 4-space nesting test. The regex
# bound is no longer what excludes a child (the contextual walk is), so under the
# fix that mutation SURVIVES the nesting tests: the row was measuring a guard
# that had moved. Two rows now, one per guard.
#
# The CONTEXTUAL exclusion, deleted. `1. parent` + `   1. child` (three spaces —
# CommonMark's content indent for `1. `) returns TWO items both ranked `1`, which
# re-points live `claim-work --slug-for <doc> <rank>` claims.
run 'nested-sub-items-counted-as-ranks' test_a_nested_numbered_line_is_not_a_rank \
  's|if top_indent is not None and indent > top_indent:|if False:|'
# The same exclusion made ABSOLUTE rather than relative — the fail-OPEN failure
# mode. A queue indented as a whole then has every item after the first dropped
# from rule (j), silently accepting untagged work.
run 'nesting-column-pinned-at-zero' test_a_queue_INDENTED_AS_A_WHOLE_still_has_every_item_counted \
  's|if top_indent is not None and indent > top_indent:|if indent > 0:|'
# `_RANKED_ITEM`'s own `^ {0,3}` bound, which now has exactly one killer: the
# FIRST numbered line in a section, indented past CommonMark's top-level bound.
run 'ranked-item-pattern-accepts-any-indent' \
  test_a_numbered_line_indented_PAST_the_commonmark_bound_is_never_a_rank \
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

printf '\n== the two holes the widening opened (must be KILLED) ==\n'
# 🔴 THE F1 REVERT ROW. Puts back the one statement that erased the "a blank line
# has intervened" memory on the fence path. Narrowest expression that can be
# wrong: the assignment alone, appended to the line it used to follow, so the
# `hidden.append` half and the boundary test below both stay untouched and the
# rows that isolate THEM are unaffected.
run 'fence-resets-the-blank-line-memory' \
  test_a_fence_does_not_erase_the_blank_line_boundary \
  's@                hidden.append(line)@                hidden.append(line); blanked = False@'
# 🔴 THE F2 REVERT ROWS, ONE PER PATTERN — they are spelled out at each use site
# precisely so these two can be isolated. A single shared constant would make one
# sed mutate both and neither row would prove anything about the pattern it names.
#
# `_FORCING` back on `\b`: `_` is a word character, so `_forcing: gate_` stops
# parsing and an italic-underscore tag gets `[no forcing: field]` — a remedy its
# author has already carried out.
run 'forcing-key-anchored-on-word-boundary' \
  test_UNDERSCORE_emphasis_parses_like_asterisk_emphasis \
  's@rf"(?<!\[A-Za-z0-9\]){FORCING_KEY}{_MARKUP}@rf"\\b{FORCING_KEY}{_MARKUP}@'
# …and the SAFETY NET's own anchor, isolated from it. Under this mutant the tag
# still parses, so only the near-miss arm can see the difference: an emphasised
# near-miss falls through to `[no forcing: field]`, which is the unrecoverable
# refusal `_FORCING_ATTEMPT` exists to prevent.
run 'near-miss-key-anchored-on-word-boundary' \
  test_an_UNDERSCORE_emphasised_near_miss_is_NAMED_not_called_absent \
  's@rf"(?<!\[A-Za-z0-9\]){FORCING_KEY}(?!\[A-Za-z0-9\])"@rf"\\b{FORCING_KEY}\\b"@'
# The KIND's own anchors in the same pattern, isolated from the key's. `\b` fails
# on the trailing `_` of `_forcing = gate_` too, so the net has TWO holes and
# closing only the key's would leave it half-open.
#
# 🔴 SPLIT INTO THREE ROWS, AND THE SPLIT IS THE FINDING. One row used to revert
# BOTH ends in a single sed, and its killing fixture — `_forcing = gate_`, whose
# `gate` is preceded by a SPACE — can only exercise the TRAILING anchor. So the
# comment on `_FORCING_ATTEMPT` claiming "BOTH ends of BOTH tokens are anchored"
# had three of four anchors shown reachable, not four.
#
# 🔴 AND THE LEADING KIND ANCHOR FAILS IN TWO DIRECTIONS, WHICH ONE ROW CANNOT
# COVER — the first draft of this pair had exactly one row and the battery
# reported it SURVIVED. MEASURED at `e34ed6ef`, per fixture:
#
#                            HEAD  leading→\b  leading DELETED  trailing→\b
#   _forcing = gate_          M        M              M              .
#   forcing = _gate           M        .              M              M
#   forcing = mygate          .        .              M              .
#
# `\b` excludes `mygate` exactly as the lookbehind does, so the over-match
# fixture cannot kill the `\b` mutant; and `forcing = _gate` survives the
# TRAILING mutant, so it isolates the leading end. One row per direction, each
# with the only fixture that separates it.
run 'near-miss-kind-TRAILING-anchor-on-word-boundary' \
  test_an_UNDERSCORE_emphasised_near_miss_is_NAMED_not_called_absent \
  "s@rf\"(?<!\[A-Za-z0-9\])(?:{'|'.join(sorted(FORCING_KINDS))})(?!\[A-Za-z0-9\])\"@rf\"(?<!\[A-Za-z0-9\])(?:{'|'.join(sorted(FORCING_KINDS))})\\\\b\"@"
# UNDER-MATCH on the leading end: an EMPHASISED KIND (`forcing = _gate`) stops
# being a near-miss and falls through to `[no forcing: field]` — the refusal a
# re-run cannot clear, for the very spelling class the underscore round existed
# to admit.
run 'near-miss-kind-LEADING-anchor-on-word-boundary' \
  test_an_EMPHASISED_KIND_is_still_seen_by_the_near_miss_net \
  "s@rf\"(?<!\[A-Za-z0-9\])(?:{'|'.join(sorted(FORCING_KINDS))})(?!\[A-Za-z0-9\])\"@rf\"\\\\b(?:{'|'.join(sorted(FORCING_KINDS))})(?!\[A-Za-z0-9\])\"@"
# OVER-MATCH on the same end: with the anchor gone entirely, `forcing = mygate`
# becomes a near-miss and the tool quotes ordinary prose back at an author as
# "an unparsed forcing field".
run 'near-miss-kind-LEADING-anchor-DELETED' \
  test_a_longer_word_around_the_KIND_is_not_a_near_miss_either \
  "s@rf\"(?<!\[A-Za-z0-9\])(?:{'|'.join(sorted(FORCING_KINDS))})(?!\[A-Za-z0-9\])\"@rf\"(?:{'|'.join(sorted(FORCING_KINDS))})(?!\[A-Za-z0-9\])\"@"

# 🔴 THE COMMENT'S OWN GUARD, ONE ROW PER ASSERTION. Round 4 rewrote the comment
# above `_FORCING` and `test_the_comment_still_states_the_ASCII_scope` gained
# three assertions over it, none of which any row showed reachable — the vacuous-
# guard shape this battery exists to refuse. The rows above pin the BEHAVIOUR of
# the widening; these pin the RECORD of it, and `claude/RULES.md` is explicit
# that a comment is a claim too. ⚠ They are also the reason `comment-reword-
# control` below is scoped to an UNPINNED comment: some comment text here IS
# behaviour the suite asserts, and that control must stay a control.
#
# 🔴 ONE CLAUSE EACH, DELIBERATELY. A mutant that gutted the paragraph would die
# to whichever of the three assertions runs first and prove nothing about the
# other two — the same isolation argument as the anchor rows above.
#
# (a) The ASCII caveat re-narrowed off the other four lookaround positions —
# verbatim the wording the comment carried at `6a862d8c`, when it claimed for the
# leading key alone a hole that all five positions have.
run 'ascii-scope-narrowed-off-every-position' \
  test_the_comment_still_states_the_ASCII_scope \
  's|OF THAT CLAIM, AT EVERY POSITION, AND THE|OF THAT CLAIM AND THE|'
# (b) The SHAPE claim demoted back to a list. An enumeration of examples is what
# undercounted the admissions twice, so "stated by POSITION, not by examples" is
# the property the assertion owns; this reverts it and leaves (a)'s clause and
# (c)'s refused literal untouched.
run 'admissions-restated-as-a-list' \
  test_the_comment_still_states_the_ASCII_scope \
  's|THE ADMISSIONS ARE A GRID, NOT A LIST|THE ADMISSIONS ARE ENUMERATED BELOW|'
# (c) The RETIRED COUNT put back. The only `not in` assertion of the three, so
# the only mutant here that ADDS text rather than removing it. The count went
# stale twice (four named, ten measured); a comment that re-states one machine-
# enforces a number already known wrong.
run 'retired-count-back-in-the-comment' \
  test_the_comment_still_states_the_ASCII_scope \
  's|# undercounted here twice\.|# undercounted here twice — FOUR ADMISSIONS, NOT ONE.|'

printf '\n== the refusals stay CLEARABLE: remedy + legend (must be KILLED) ==\n'
# 🔴 THE ROW THAT WAS MISSING, AND ITS ABSENCE WAS MEASURED. Reverting
# `FENCED_FIELD_REMEDY` to the bare "move it out of the fence" left the WHOLE
# suite green at `e34ed6ef` — the only fenced test asserted `[fenced]` and "code
# fence", both of which the bare text keeps. So the corrected remedy was prose,
# not a guarantee. Obeying the bare instruction on a fence quoting this tool's
# own vocabulary line promotes a quoted example into a declaration: a FALSE
# `forcing: none`.
run 'fenced-remedy-reverted-to-bare-move-it-out' \
  test_the_FENCED_remedy_is_not_a_bare_move_it_out \
  '/^    "field is YOUR declaration/d;/^    "own lines, INDENTED/d;/^    "output, a copied example/d;/^    "genuinely untagged and needs/d;/^    "where it does not count/c\    "where it does not count. Move it out of the fence onto one of its own lines."'
# The other unclearable arm: an author who wrote the tag at COLUMN 0 under the
# item is told "a continuation line counts" and has already done it. Naming the
# INDENT is what ends that loop; this reverts only that clause.
run 'missing-field-remedy-omits-the-indent' \
  test_the_missing_field_remedy_tells_a_FLUSH_LEFT_author_to_INDENT \
  '/^    "be INDENTED: a flush-left/d;/^    "so a tag at column 0 below one/d;/^    "counts — the field does not have to sit on the numbered line, but it MUST "$/c\    "counts — the field does not have to sit on the numbered line."'
# 🔴 THE SCOPE OF THAT SAME REMEDY, ISOLATED FROM THE INDENT IMPERATIVE ABOVE.
# The row above deletes the whole clause; this one keeps "MUST be INDENTED" and
# reverts ONLY the explanation to the unqualified "a flush-left line ENDS the
# item" it carried at `6a862d8c` — the wording round 4 measured FALSE (a col-0
# tag with NO blank before it parses: kind='gate'). Re-measured 2026-08-28.
#
# 🔴 IT IS THE ROW THAT SHOWS THE OLD ASSERTION WAS VACUOUS. Under this mutant
# `test_the_missing_field_remedy_tells_a_FLUSH_LEFT_author_to_INDENT` stays
# GREEN — its first assertion compares the constant against the output that
# printed that same constant, and its second ("MUST be INDENTED") is untouched.
# Only the whole-string pin can see it, which is why that pin was added.
run 'missing-field-remedy-scope-unqualified' \
  test_the_missing_field_remedy_states_the_walks_ACTUAL_boundary \
  's|ENDS the item once a blank has intervened, "|ENDS the item, so a tag at column 0 under "|;s|"so a tag at column 0 below one is outside the item|"it is outside the item|'
# 🔴 THE SKILL-SIDE ROW — the first mutation in this battery that is NOT to the
# module. It puts SKILL.md's step-5 legend back to the bare "move it out of the
# fence" it carried at `e34ed6ef`, i.e. the executor's only map telling them to
# do the thing the module's own comment calls harmful. Nothing could show that
# guard reachable while every row mutated the module only.
run_skill 'skill-legend-says-only-move-it-out' \
  test_the_skill_legend_does_not_tell_the_executor_to_promote_a_quote \
  's@\*\*yours[^*]*\*\*[^.]*\.@move it out of the fence.@'
# 🔴 THE SECOND SKILL-SIDE ROW, and a DIFFERENT sentence — step 3's near-miss
# clause, not the step-5 legend. It reverts the parenthetical to where it sat at
# `6a862d8c`, against its nearest antecedent "a fenced field", for which it is
# FALSE: `fenced` is `any(_FORCING.search(ln) …)` and never consults
# `FORCING_KINDS`, so a fenced `forcing: followup` is reported `[fenced]`, not
# absent. Re-measured 2026-08-28.
#
# 🔴 AND IT SHOWS WHY A `SKILL_PINS` ENTRY WAS NOT ENOUGH. The pin over this
# sentence is the substring "`forcing = gate` **with a listed kind**", which the
# reverted text still contains verbatim — so `test_skill_pins` stays GREEN under
# this mutant and only the whole-string clause pin can see it.
run_skill 'skill-near-miss-clause-rebound-to-the-fence' \
  test_the_skill_binds_UNLISTED_reads_ABSENT_to_the_right_antecedent \
  's|\*\*with a listed kind\*\* (unlisted reads ABSENT), and a fenced field regardless of kind, are \*\*near-misses, NAMED\*\* not absent\.|**with a listed kind**, and a fenced field, are **near-misses, NAMED** not absent (an unlisted kind there reads ABSENT).|'
# 🔴 THE SEAM ROW. Renames a marker in the module. SKILL.md's step-5 legend is
# the executor's only map from a marker to what to do about it, and a rename that
# left the legend behind would keep every SKILL_PIN green.
# ⚠ It also kills the module-side fenced test and the three legend tests —
# unavoidable, because the SAME constant feeds the printed row, the skill check
# and `_fenced_legend_clause`'s anchor (which is derived from `MARK_FENCED` for
# exactly that reason: a rename must not leave a locator hunting for a marker
# nobody prints). The row is here to prove the DERIVED test can see a rename at
# all; `want` names that test.
run 'refusal-marker-renamed' \
  test_every_refusal_MARKER_the_module_prints_reaches_the_skill \
  's@^MARK_FENCED = "\[fenced\]"@MARK_FENCED = "[in-a-fence]"@'

printf '\n== the EXIT-CODE CONTRACT the ladder left unpinned (must be KILLED) ==\n'
# 🔴 THE FIRST THREE — the renumbers — SURVIVED ALL 295 TESTS WHEN #1115 FILED
# THEM, so those three rows are regression coverage. The enumeration in
# `test_the_exit_code_constants_did_not_move` stopped at 6, so the ladder's OWN
# codes — the ones rules (i)/(j) and the stale-base refusal return — had no pin
# on their VALUES at all. A caller branches on the number.
#
# 🔴 THE FOURTH IS NOT ONE OF THEM, and an earlier wording said it was.
# MEASURED at the merge-base `093a63db`: `one-exit-constant-silently-vanishes`
# already killed **8** tests there, so it is an INVARIANT GUARD on the new
# `== 9` — it does not show the suite would have missed the mutant without it.
# 🔴 THAT IS NOT A REASON TO DELETE `== 9`, and an earlier version of this
# comment said deleting it would leave this row reporting `ok`. FALSE, and
# measured: with the assertion deleted, the 8 killers do NOT include this row's
# named `test_no_two_exit_constants_share_a_value`, so `_run`'s `grep -qx` misses
# and the row reports 🔴 WRONG-KILLER. The pin is load-bearing HERE even though
# the mutant was already covered THERE.
run 'stale-base-code-renumbered' test_the_exit_code_constants_did_not_move \
  's|^EXIT_STALE_BASE = 9$|EXIT_STALE_BASE = 10|'
run 'doc-per-effort-code-renumbered' test_the_exit_code_constants_did_not_move \
  's|^EXIT_DOC_PER_EFFORT = 7$|EXIT_DOC_PER_EFFORT = 11|'
run 'unforced-code-renumbered' test_the_exit_code_constants_did_not_move \
  's|^EXIT_UNFORCED = 8$|EXIT_UNFORCED = 12|'
# 🔴 THE FLOOR-VS-EXACT ROW. `len(codes) >= 8` against exactly 9 constants
# tolerates ONE silently vanishing — a rename, a deletion, or a value that stops
# being an `int`. This mutant makes one stop being an int, which is the arm a
# deletion-shaped mutant would miss.
run 'one-exit-constant-silently-vanishes' test_no_two_exit_constants_share_a_value \
  's|^EXIT_BEHIND = 6$|EXIT_BEHIND = "6"|'

printf '\n== rule (i) consults WHICH predicate, and the mainline is really MEASURED ==\n'
# 🔴 THE FAIL-OPEN ROW. Broadening the third term to `not currency.stale` reads
# as a simplification and changes behaviour: a whitespace-only mainline copy is
# `stale` but is NOT something to lose, so rule (i) stops firing and #962's
# one-doc-per-effort cap is silently bypassed. It survived all 295 tests.
run 'rule-i-broadened-to-bare-stale' \
  test_rule_i_asks_WHICH_predicate_not_merely_whether_stale \
  's|and not currency.replaces_mainline_doc("")):|and not currency.stale):|'
# 🔴 THE DEGENERATE-VALUE ROW. `0 sections` is what `doc_shape("")` and a
# hardcoded `DocShape(0,0)` also produce, so pinning it alone cannot see a
# mutant that reads the mainline blob and then measures the WRONG TEXT. The
# per-fixture LINE COUNT is the half that proves real bytes were measured.
run 'mainline-measured-from-the-wrong-text' \
  test_a_WHITESPACE_mainline_doc_is_ALSO_not_something_to_lose \
  's|            mainline = doc_shape(shown.out)|            mainline = doc_shape("")|'

printf '\n== the round-4 regression must fail with the RIGHT message (#1093.1) ==\n'
# 🔴 A DIAGNOSTIC-QUALITY ROW, so `_run` cannot score it: every other row asks
# WHICH TEST died, this one asks WHICH MESSAGE it died with. Reverting
# `replaces_mainline_doc` to the round-4 bug takes the LOUD branch, so the shape
# line never prints — and if the shape assertion runs FIRST it fires with "the
# mainline copy was never read", which is false and points a maintainer two
# functions from the defect.
#
# 🔴 READ ONLY THE `^E ` LINES. pytest prints the failing test's SOURCE as
# traceback context and this test's COMMENTS quote the old message verbatim, so
# a grep over the whole output matches the comment and reports a misdirect that
# did not happen. Measured — that is exactly what the first run of this row did.
R4_MUT='s|            and self.mainline_blank is False|            and bool(self.mainline.lines)|'
R4_TEST=test_a_WHITESPACE_mainline_doc_is_ALSO_not_something_to_lose
r4_err() { # r4_err  ⇒ the AssertionError lines from the mutated run
  PYTHONDONTWRITEBYTECODE=1 python3 -m pytest "$SUITE" -k "$R4_TEST" \
    -q --no-header -p no:cacheprovider 2>&1 | grep '^E '
}
ROWS=$((ROWS+1))
sed "$R4_MUT" "$MOD" > "$T/m" 2>/dev/null
if cmp -s "$MOD" "$T/m"; then
  printf '  🔴 %-44s MUTATION DID NOT APPLY — result meaningless\n' 'round-4-regression-message'
  FAILURES=$((FAILURES+1))
else
  cat "$T/m" > "$MOD"
  r4="$(r4_err)"
  cp -a "$T/mod.orig" "$MOD"
  if [ -z "$r4" ]; then
    printf '  🔴 %-44s SURVIVED — no assertion fired\n' 'round-4-regression-message'
    FAILURES=$((FAILURES+1))
  elif grep -q 'the guard MIS-FIRED on a whitespace mainline copy' <<<"$r4"; then
    printf '  ok %-44s KILLED naming the real defect (guard MIS-FIRED)\n' 'round-4-regression-message'
  else
    printf '  🔴 %-44s MISDIRECTS — died to: %s\n' 'round-4-regression-message' \
      "$(head -1 <<<"$r4")"
    FAILURES=$((FAILURES+1))
  fi
fi
# 🔴 NEGATIVE CONTROL FOR THE ROW ABOVE — without it, its `ok` is unfalsifiable.
# Restores the PRE-FIX ORDER by deleting the loud-line assertion, so the shape
# assertion runs first exactly as it did before #1093.1, then re-runs the SAME
# mutant. This MUST misdirect; if it reports the correct message too, the row
# above is keying on something other than the fix and its verdict is void.
# Deliberately NOT `git show <sha>` — a squash merge makes the pre-fix sha
# unreachable from the mainline, which would rot this control the day it lands.
ROWS=$((ROWS+1))
python3 - "$SUITE" <<'PYEOF'
import re, sys
p = sys.argv[1]
s = open(p, encoding="utf-8").read()
# 🔴 The message is an implicit-concat string tuple, so the LAST line before
# `prop.stdout[-400:]` carries a trailing comma — `"[^"]*"\n` without the
# optional `,` matched ZERO times and the control reported COULD NOT STAGE.
pat = re.compile(
    r'\n *assert "will be replaced by this delta" not in prop\.stdout, \(\n'
    r'(?: *"[^"]*",?\n)+ *prop\.stdout\[-400:\]\)\n')
hits = pat.findall(s)
# 🔴 EXACTLY ONE. A count=1 replace on a pattern that occurs twice deletes
# whichever comes first, which is not the one anybody pictured; and zero means
# the assertion was reworded and this control is measuring nothing.
if len(hits) != 1:
    sys.stderr.write(f"expected exactly 1 loud-line assertion, found {len(hits)}\n")
    sys.exit(1)
m = pat.search(s)
open(p, "w", encoding="utf-8").write(s[:m.start()] + "\n" + s[m.end():])
PYEOF
if [ $? -ne 0 ]; then
  printf '  🔴 %-44s COULD NOT STAGE — the row above is UNVALIDATED\n' 'round-4-negative-control'
  FAILURES=$((FAILURES+1))
else
  sed -i "$R4_MUT" "$MOD"
  nc="$(r4_err)"
  cp -a "$T/mod.orig" "$MOD"; cp -a "$T/suite.orig" "$SUITE"
  if grep -q 'the guard MIS-FIRED' <<<"$nc"; then
    printf '  🔴 %-44s PRE-FIX ORDER gave the CORRECT message — row above is void\n' \
      'round-4-negative-control'
    FAILURES=$((FAILURES+1))
  elif grep -q 'the mainline copy was never read' <<<"$nc"; then
    printf '  ok %-44s PRE-FIX ORDER misdirects — the row above can go red\n' \
      'round-4-negative-control'
  else
    printf '  🔴 %-44s INCONCLUSIVE — died to: %s\n' 'round-4-negative-control' \
      "$(head -1 <<<"$nc")"
    FAILURES=$((FAILURES+1))
  fi
fi
printf '\n== rule (k): an elimination names HOW it was eliminated (must be KILLED) ==\n'
# The refusal deleted outright: no bullet is ever unevidenced.
run 'elimination-refusal-never-fires' test_the_MEASURED_bullet_that_this_rule_exists_for_is_refused \
  's|bad = \[b for b in bullets if not b.is_declared\]|bad = []|'
# The CLOSED VOCABULARY opened up. Anything typed after `via:` would count,
# which is the whole difference between an allowlist and a keyword sniff.
run 'elimination-kind-allowlist-opened' test_an_unknown_kind_is_refused_and_NAMED \
  's|return self.kind in ELIMINATION_KINDS|return self.kind is not None|'
# 🔴 THE WALKABILITY ROW. Reverts the marker to the narrow form that demanded a
# separator straight after `Ruled out` — the shape that MISSED nine real corpus
# bullets and is escaped by typing `Ruled out (obviously):`. The anchor carries
# the `\b` because `ruled[ -]out` alone also matches the comment above it, and a
# sed that hits the comment mutates nothing while reporting the guard held.
run 'elimination-marker-requires-a-separator' test_a_qualifier_between_the_marker_and_the_claim_cannot_escape \
  's|ruled\[ -\]out\\b|ruled[ -]out\\s*:|'
# The near-miss net removed: a bullet that DOES carry a tag gets told to add one.
run 'elimination-near-miss-net-removed' test_a_near_miss_is_NAMED_not_reported_as_absent \
  's|if _VIA_ATTEMPT.search(ln)|if False|'
# The fenced diagnosis removed: a field the author can SEE reads as absent.
run 'elimination-fenced-diagnosis-removed' test_a_field_inside_a_fence_is_NAMED_as_fenced \
  's|        fenced = near is None and any(|        fenced = False and any(|'
# 🔴 THE COLLISION THIS REPO ALREADY SHIPPED ONCE — two constants both taking 7.
# The killing guard is PRE-EXISTING; this row proves rule (k)'s new code is
# inside its reach rather than beside it.
run 'exit-code-collides-with-unforced' test_no_two_exit_constants_share_a_value \
  's|^EXIT_UNEVIDENCED = 10|EXIT_UNEVIDENCED = 8|'
# The legend half: a code can be unique and still undocumented. MEASURED — the
# legend ended at 8 while EXIT_STALE_BASE = 9 was live and printed at callers.
run 'exit-legend-omits-the-new-code' test_the_module_legend_documents_every_nonzero_code \
  's|^ 10  unevidenced|# 10 unevidenced|'
# The ADVISORY half — `via: assumed` must land AND be counted where a reader
# sees it. Rule (j) has the same row for `forcing: none`.
run 'assumed-advisory-suppressed' test_via_assumed_is_ACCEPTED_and_reported \
  's|    assumed = \[b for b in bullets if b.kind == "assumed"\]|    assumed = []|'
# 🔴 THE CLEARABILITY ROWS. An audit found rule (k) shipped with ONE trailer for
# all four causes, reproducing all three unrecoverable-refusal shapes rule (j)
# had already been fixed for twice. Collapsing the per-cause selection back is
# what these rows forbid.
run 'remedies-collapse-to-all-causes' test_only_the_causes_PRESENT_get_a_remedy \
  's|) if c in causes|) if True|'
run 'nothing-written-line-dropped' test_the_refusal_says_NOTHING_WAS_WRITTEN \
  's|f"NOTHING WRITTEN — not the doc|f"nothing written — not the doc|'
run 'elided-count-suppressed' test_more_bullets_than_are_shown_says_so \
  's|    if elided:|    if False:|'
# The leading-decoration widening, reverted — the mirror of the qualifier row.
run 'leading-decoration-rejected' test_leading_decoration_before_the_marker_cannot_escape \
  's|rf"(?:\[\^A-Za-z\\s\]+\\s\*)\*"|rf""|'
# The col-0-fence false verdict, reintroduced by dropping the indent filter.
run 'fenced-filter-ignores-indent' test_an_unrelated_column_0_fence_does_not_forge_a_fenced_verdict \
  's|_VIA.search(ln) for ln in hidden if ln\[:1\] in (" ", "\\t")|_VIA.search(ln) for ln in hidden|'
# BEHAVIOUR-FREE CONTROL for this block specifically — the rows above must key on
# behaviour, not on the presence of rule (k)'s own comment text.
run 'rule-k-comment-reword-control' SURVIVES \
  's|# --- rule (k): an elimination names HOW it was eliminated|# --- rule k: elimination evidence (reworded comment)|'

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
