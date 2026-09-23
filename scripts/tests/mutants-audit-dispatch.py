#!/usr/bin/env python3
"""Mutation battery for `scripts/audit-dispatch.py` — the audit-brief assembler.

    nix develop ~/workspace/devrc -c python3 scripts/tests/mutants-audit-dispatch.py

Not run by CI. An author/reviewer instrument, kept IN THE TREE so
"mutation-verified" can be RE-DERIVED instead of believed — the same reason
`scripts/tests/mutants-audit-ladder.sh` exists, and it records the incident:
devrc #900 ran ten rounds and recorded ~30 mutants in a module docstring, every
one of them inside a session scratchpad that no longer exists, so not a single
row could be re-checked by anyone else. This follows its conventions.

🔴 EACH MUTANT NAMES THE EXACT SET OF TESTS THAT MUST KILL IT — not "a test
failed". These pins overlap by design (a clause deletion trips the id ledger,
the fragment ledger and the rendered-section comparison), so a mutant can die to
a DIFFERENT test's error and be scored as covered while its own assertion is
unreachable. Two distinct failures are reported separately because the responses
differ:
  * an expected killer ABSENT   -> 🔴 WRONG-KILLER: that pin is DEAD.
  * an UNEXPECTED extra killer  -> 🔴 EXTRA-KILLER: something else now fires;
                                   the row no longer isolates what it names.
Collapsing them into one comparison routes the operator away from the first,
which is the failure this battery exists to catch.

🔴 EACH MUTATION IS ASSERTED TO APPLY BEFORE IT RUNS. A target string that no
longer matches — a line re-wrapped, a word changed — leaves the file UNMUTATED
and reports "the guard held", the most flattering possible wrong answer.

🔴 IT NEVER TOUCHES YOUR WORKING TREE. Everything happens in a `mktemp` copy
built by naming TWO INDIVIDUAL FILES. That selective copy, not the assertion
below it, is what keeps a `.git` out; the assertion is an INVARIANT GUARD
against the future refactor that replaces the file list with `cp -a`, which
would carry a worktree's `.git` POINTER FILE and let a git command inside the
copy act on the real repository.

🔴 PYTHONDONTWRITEBYTECODE=1, and `-p no:cacheprovider`. A stale `.pyc` keyed on
mtime-in-whole-seconds plus size is how a same-length edit gets scored SURVIVED
without ever executing.

🔴 THE BASELINE IS THE POSITIVE CONTROL. If the unmutated copy is not green,
every row below is meaningless and this exits 2 without running any of them —
and it distinguishes "the tree is red" from "pytest is not on PATH", because the
second one blames the tree for the shell you are in.

🔴 A ROW MAY EXPECT `SURVIVES`. Same convention as
`scripts/tests/mutants-audit-ladder.sh`: the clause ledger pins WHOLE
whitespace-normalised strings, so a pure RE-WRAP must stay green. A re-wrap that
went red would mean the pins are keyed to line breaks and every reflow of the
source becomes a test failure — which is the permanently-red gate
`claude/RULES.md` says trains people to click through. A `SURVIVES` row that
KILLS something is a finding, reported as such.

COVERAGE IS DELIBERATELY PARTIAL. What is here: one deletion per invariant
clause, an addition, one REWORD that leaves the clause present (the reachability
control for the clause ledger), five rewords that INVERT their clause, a
re-wrap control, one renderer bypass, and one inversion per generated decision —
including the four that shipped and were caught in round 2's audit (the head
check, the toolchain's tree-under-test, the fork-PR repo read, and the
three-state repo decision). What is NOT here: the `gh`/`git` boundary itself
(the suite injects a runner and says so), and whether a human classifies the
ledger's files correctly (nothing mechanical can grade that).
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT_REL = "scripts/audit-dispatch.py"
TEST_REL = "scripts/tests/test_audit_dispatch.py"
# 🔴 THIS FILE IS A THIRD INPUT TO THE SUITE, not only its driver. Round 4 made
# the fix matrix's mutants column an evidence claim graded against `ROWS`
# below, so the test module IMPORTS this file — and a scratch tree without it
# fails at collection, which reads as "the mutant killed everything".
HARNESS_REL = "scripts/tests/mutants-audit-dispatch.py"
# Non-stdlib modules `TEST_REL` imports at module scope. See the copy loop in
# `main()` for why they have to be in the sandbox at all.
TESTLIB_RELS = (
    "scripts/testlib/__init__.py",
    "scripts/testlib/hermetic_git.py",
)
# 🔴 A FOURTH INPUT, for the same reason as `HARNESS_REL` above: the suite now
# carries a SEAM test that reads `claude/skills/audit-pr/SKILL.md` — the other
# owner of the attribution gate's mechanism — so a sandbox without it fails the
# baseline and every row below is refused.
#
# 🔴 AND IT MUST BE THE REPO'S COPY, NOT THE DEPLOYED ONE. The test falls back
# to `~/.claude/skills/...` when the repo path misses, which is a nix-store
# copy of whatever was last `home-manager switch`ed — i.e. of `main`. Without
# this copy the sandbox took that fallback and graded the seam against a stale
# deployment: MEASURED, the baseline went red on the very edit that satisfies
# the seam in the repo.
SKILL_RELS = (
    "claude/skills/audit-pr/SKILL.md",
)

# 🔴 A COLLAPSE floor, not a growth floor — same rule as
# `mutants-audit-ladder.sh`. A suite that never ran yields zero FAILED lines,
# i.e. "clean", so a harness wired to nothing would score every mutant SURVIVED.
#
# 🔴 RAISED IN ROUND 8, 50 -> 97, and the drift story is the reason it was 50.
# It was set when the suite held 58 tests and never moved; against round 8's
# 102 it would pass a run that collected barely half the module — a state that
# silently drops whole classes of guard while every remaining row still reports
# a verdict. Derived from the current measurement the way this repo derives
# every other floor, `m - min(50, max(1, m // 20))`, with `m` COUNTED from the
# harness's own positive-control line and NOT hand-picked.
#
# 🔴 ROUND 10 RAISED IT AGAIN, 97 -> 104, at m = 109. And round 8's comment
# here was WRONG IN BOTH ITS NUMBERS — it said "50 -> 95" and "against today's
# 100" three lines above `MIN_TESTS = 97` and beside its own correct `m = 102`.
# The stated derivation from the stated `m` gave 97; neither figure in the
# prose was ever the constant. Exactly the drift this file exists to refuse,
# in the file whose thesis is that unre-run arithmetic rots. Re-derive by
# COUNTING, never by adding this round's new tests to the last sentence.
#
# 🔴 IT IS A COLLAPSE FLOOR, SO IT COMPARES `passed + failed`, NOT `passed`. A
# mutant is SUPPOSED to make tests fail; what this refuses is a run that
# COLLECTED too few. A floor that tracks the suite has to be re-derived when
# the suite grows — the alternative is the one measured above, a number nobody
# updated for four rounds while the thing it protects doubled.
#
# 🔴 ROUND 13 RAISED IT AGAIN, 104 -> 107, at m = 112 — COUNTED from a green
# run of the module (`112 passed`), not from adding this round's three new
# tests to the last sentence, which is the arithmetic the paragraph above
# records going wrong twice.
#
# 🔴 ROUND 15 RAISED IT AGAIN, 107 -> 116, at m = 122 — COUNTED from a green run
# of the module (`122 passed`) and put through the same formula,
# `122 - min(50, max(1, 122 // 20))` = 122 - 6 = 116, not derived by adding that
# round's seven new tests to the last sentence.
#
# 🔴 AND THAT SENTENCE WAS WRONG IN BOTH ITS FIGURES — the THIRD time this
# block has recorded exactly that, which is why it is corrected in place rather
# than appended to. It read "107 -> 115, at m = 121" while quoting its own
# correct `122 - min(50, max(1, 122 // 20))` and sitting above `MIN_TESTS = 116`.
# A reader re-deriving from "m = 121" gets 115 and would "correct" the constant
# DOWNWARD, loosening the collapse floor by one on the strength of a sentence
# nobody re-ran. Read the CONSTANT, re-derive from a COUNT, and fix the prose to
# match — never the other way round.
#
# 🔴 ROUND 16 RAISED IT AGAIN, 116 -> 120, at m = 126 — COUNTED from a green run
# of the module (`126 passed`), `126 - min(50, max(1, 126 // 20))` = 126 - 6 =
# 120, not derived by adding this round's four new tests to 116.
#
# 🔴 ROUND 18 RAISED IT AGAIN, 120 -> 122, at m = 128 — COUNTED from a green run
# of the module (`128 passed`), `128 - min(50, max(1, 128 // 20))` = 128 - 6 =
# 122, not derived by adding this round's one new test to 120.
#
# 🔴 RAISED AGAIN 2026-09-14, 122 -> 131, at m = 137 — COUNTED from a green run of
# the module (`137 passed`), `137 - min(50, max(1, 137 // 20))` = 137 - 6 = 131,
# not derived by adding this round's two new tests to 122. Raised here rather
# than left because a floor sitting 15 below the module is a floor that would
# have vouched for a suite which had silently lost every test added since round
# 16 — the exact shape `mutants-audit-ladder.sh` recorded twice before its own
# floor was pinned to a test. This one still is not; it is maintained by this
# comment and by whoever reads it.
#
# 🔴 ROUND 19 RAISED IT AGAIN, 131 -> 147, at m = 154 — COUNTED from this
# harness's own `POS  unmutated copy .......... 154 passed` line, put through
# the same formula, `154 - min(50, max(1, 154 // 20))` = 154 - 7 = 147. NOT
# derived by adding this round's new tests to 131, and the jump is larger than
# the test count because one of them is parametrized over a twelve-row fixture
# ledger.
#
# 🔴 RAISED AGAIN 2026-09-15, 147 -> 155, at m = 163 — COUNTED from this
# harness's own `POS  unmutated copy .......... 163 passed` line, put through
# the same formula, `163 - min(50, max(1, 163 // 20))` = 163 - 8 = 155. NOT
# derived by adding rounds 20-23's new tests to 147.
#
# 🔴 THIS RAISE IS NOT ANY ONE ROUND'S DRIFT, AND THAT IS WHY IT WAS MISSED.
# The floor was set at m = 154 by round 19 and the module reached 163 over
# rounds 20-23, so no single round's diff ever looked wrong. The gap had
# reached 16 — one more than the 15 the round-18 paragraph above names as the
# point where a floor would vouch for a suite that had silently lost every
# test added since round 16. Round 23 saw it and correctly DECLINED to fix it
# (its brief scoped a MIN_TESTS change to "if the module grew", and it added
# no tests); it named the closing condition instead.
#
# 🔴 A FLOOR THAT IS TOO LOW NEVER COMPLAINS — that is the whole reason this
# sat for four rounds, and it is the direction no green run can report.
# Negative control WATCHED, not assumed: at `MIN_TESTS = 999` the harness
# refuses with `🔴 THE HARNESS could not run: only 163 test(s) ran (floor
# 999) — the harness, not the tree`. So the check is reached and enforcing.
# 🔴 RAISED AGAIN 2026-09-17, 155 -> 162, at m = 170 — COUNTED from a green run
# of the module (`170 passed`) and put through the same formula,
# `170 - min(50, max(1, 170 // 20))` = 170 - 8 = 162. NOT derived by adding the
# attribution gate's seven new tests to 155, which is the arithmetic the
# paragraphs above record going wrong three times.
# 🔴 RAISED AGAIN 2026-09-18, 162 -> 164, at m = 172 — COUNTED the same way,
# from a green run of the module (`172 passed`) put through the same formula,
# `172 - min(50, max(1, 172 // 20))` = 172 - 8 = 164. Two tests were added by
# `fix/audit-dispatch-rc5-overload`; the number is still the formula's output
# on a counted m, NOT 162 + 2.
# 🔴 RAISED AGAIN 2026-09-23, 164 -> 180, at m = 189 — COUNTED the same way,
# from a green run of the module (`189 passed`, and `189 tests collected` on a
# `--collect-only`) put through the same formula,
# `189 - min(50, max(1, 189 // 20))` = 189 - 9 = 180. Seventeen node ids were
# added by `feat/payload-unit-executable-lines` (thirteen functions, one of
# them parametrized five ways); the number is still the formula's output on a
# counted m, NOT 164 + 17. That distinction is the one the three paragraphs
# above record going wrong, and the arithmetic happens to differ here: 164 + 17
# is 181, which would have been one too high and refused every run.
# 🔴 RAISED AGAIN 2026-09-23, 180 -> 187, at m = 196 — COUNTED the same way,
# from a green run of the module (`196 passed`) put through the same formula,
# `196 - min(50, max(1, 196 // 20))` = 196 - 9 = 187. Seven node ids were added
# by `feat/unearned-ledger-detection`; the number is still the formula's output
# on a counted m, NOT 180 + 7 (which is 187 only by coincidence — the floor is
# a function of the CURRENT measurement, and the two agreeing here is not a
# method). 🔴 AND IT WAS CI THAT CAUGHT IT, NOT THE AUTHOR: the gate lives in
# `test_mutation_battery_anchors.py`, which is in NEITHER the five modules that
# change touched NOR `scoped-tests.sh`'s selection for it — the mapper selects
# files that NAME what changed, and that module names the HARNESS rather than
# the target. Assume a change adding tests to `test_audit_dispatch.py` needs
# this literal moved, and run that module by hand.
MIN_TESTS = 187

# A row may name this instead of a killer set: the mutation MUST leave the suite
# green. See the module docstring — the clause ledger pins whole normalised
# strings, and a re-wrap that went red would make every reflow a test failure.
SURVIVES = "SURVIVES"

# 🔴 A THIRD expectation, and it is NOT a synonym for SURVIVES. `SURVIVES` is a
# CONTROL: the pin must not be keyed to layout, and a red there is a defect in
# the pin. `HOLE` is a MEASURED GAP: the mutation survives because nothing
# guards that sentence, which the script's own ledger comment says out loud
# rather than letting three unguarded blocks read as covered. The two print
# differently on purpose — "SURVIVED as required (control)" over a documented
# hole would be the flattering wrong answer this whole file exists to refuse.
#
# A HOLE that starts being KILLED is good news and is reported as an ACTION:
# promote the row to the killer set that now fires, and delete its line from
# the ledger comment in `scripts/audit-dispatch.py`.
HOLE = "HOLE"


# --------------------------------------------------------------------------- #
# Mutations. Each takes the script source and returns it changed, or raises.
# --------------------------------------------------------------------------- #

def _require_unique(n, what):
    """🔴 THE TARGET MUST BE UNIQUE, not merely PRESENT — ONE RULE, ONE PLACE.

    Round 8 measured the difference, and it is the same shape as everything
    else this file guards. `C5` targets a line starting with EIGHT spaces;
    round 8 added a second copy of that sentence inside a nested list at TWELVE
    spaces, and eight spaces is a substring of twelve — so `replace(…, 1)` hit
    the NEW site, which had no `led` in scope, and every scenario-driven test
    died of a `NameError`. The row reported WRONG-KILLER, which is the lucky
    outcome: a wrong site that happens to stay runnable scores SURVIVED, or
    KILLED for a reason belonging to a different mutation, and either reads as
    a measurement of the row's own target.

    `claude/RULES.md`: "A `count=1` text replace on a pattern that occurs more
    than once is a live hazard — which occurrence you hit is not the one you
    pictured." The assert above it already refuses a target that has MOVED;
    this refuses one that has been DUPLICATED, and the two failures need the
    same response — re-target the row.

    🔴 ROUND 10 — LIFTED OUT OF `_swap`, WHICH WAS ONE OF THREE SITES. Round 8
    applied the lesson to the string mutator and left both REGEX mutators
    (`drop_entry`, `reword_entry`) doing `pat.subn(…, count=1)` and testing
    `n != 1` — a predicate that, WITH `count=1`, can only ever be 0 or 1 and so
    can detect an ABSENT target and never a DUPLICATED one. Milder than
    `_swap`'s case (a duplicated entry id would score SURVIVED, which is loud)
    but the same blind spot, at the sites nobody swept. The rule now has one
    statement and three callers.
    """
    if n == 0:
        raise AssertionError(f"target absent: {what}")
    if n > 1:
        raise AssertionError(
            f"target is AMBIGUOUS — {n} occurrences, so a `count=1` "
            f"substitution would edit whichever comes first and not "
            f"necessarily the one this row means: {what}"
        )


def _sub_unique(pat, repl, t, what):
    """`_require_unique` in its REGEX spelling — counts MATCHES, then edits."""
    _require_unique(len(pat.findall(t)), what)
    return pat.sub(repl, t, count=1)


def drop_entry(kind, cid):
    """Delete a whole `Clause(...)` / `Directive(...)` entry from its tuple."""
    def f(t):
        pat = re.compile(
            r"    " + kind + r"\(\n        \"" + re.escape(cid)
            + r"\",\n(?:.*?\n)*?    \),\n"
        )
        return _sub_unique(
            pat, "", t, f"{kind.lower()} {cid!r} in its expected shape"
        )
    return f


def drop_clause(cid):
    return drop_entry("Clause", cid)


def _swap(t, old, new):
    """A UNIQUE literal substring, replaced once. See `_require_unique`."""
    _require_unique(t.count(old), f"{old[:70]!r}…")
    return t.replace(old, new, 1)


# --------------------------------------------------------------------------- #
# 🔴 THE PROSE LADDER'S AUTHORSHIP DETERMINATION (Q-series).
#
# These rows exist because the sweep that produced them was run in a session
# scratchpad, which is the exact failure `mutants-audit-ladder.sh`'s header
# records: "every one of those runs happened in a session scratchpad that no
# longer exists, so not a single row could be re-checked by anyone else". They
# are checked in so the claim "each guard was watched to fail with its OWN
# error" can be re-derived instead of believed.
#
# 🔴 THE SEAM ROWS ARE IN THE OTHER BATTERY. Widening the scope sentences this
# script SHIPS is killed from `test_audit_ladder_stop_rule.py`, which `failing()`
# does not run; `mutants-audit-ladder.sh` runs that module and already copies
# this script, so the two rows live there. Recorded here because "the seam is
# unswept" is the wrong conclusion to draw from their absence.
#
# 🔴 Q1 IS THE RETRACTED RULE ITSELF. `#1678` (`e8fa6fca`) let this hatch stop a
# ladder at ROUND 1 and was withdrawn hours later; the mutation below is that
# rule re-entering through the renderer rather than through the prose. Its
# target carries the docstring terminator on purpose — a bare `if ... < 2:` line
# has near-twins in this script, and `_swap` refuses an ambiguous target rather
# than editing whichever comes first. MEASURED on the previous spelling: without
# the prefix this row reported "target is AMBIGUOUS" and scored nothing.
_DET_FLOOR = '    """\n    if round_no < 2:\n        return ""'
_DET_COLLISION = (
    "    elif anchor:\n"
    "        boundary = (\n"
    "            f\"`{anchor}`, which IS this round's own `<from>` — so every \""
)
_DET_PROSE_ONLY = (
    '        "🔴 **This is for YOU, the ladder runner, not for the auditor.** "\n'
    "        + PROSE_DETERMINATION_POPULATION\n"
)
# 🔴 The FOURTH open-coded site of `same_commit`, restored. See Q13.
_DET_COLLISION_PREDICATE = (
    "    elif anchor and not same_commit(anchor, emit_from):"
)
# 🔴 The boundary line that blamed a block the state proves absent. See Q14.
_DET_NO_BLOCK_BOUNDARY = (
    '            "**NOT RECOVERABLE from this run** — no `audit-claims` block in "\n'
    '            "this PR\'s record carries a `<from>` for this round, so there is "\n'
    '            "no range and no operand"\n'
    '            + (f" (a round-1 anchor `{anchor}` IS on the record, but an anchor "\n'
    '               "with no range measures nothing)" if anchor else\n'
    '               ", and no block carried a round-1 anchor either")\n'
    '            + ". The determination is NOT MEASURED this round: run the next one"'
)
# 🔴 The NOT-MEASURED ledger's reflow row, and the branch that reaches it.
_DET_REFLOW_ROW = '    "reflow": ('
_DET_REFLOW_BRANCH = (
    "    if reflowed:\n"
    "        return _not_measured(\n"
    '            "reflow", ladder, ladder + pr_authored,'
)
_DET_BLAME_FLAGS = "git blame -w -M --porcelain "
_DET_DIFF_FLAGS = "f\"git diff -U0 -w -M {emit_from or '<from>'}..{head_sha}\""
# 🔴 The REFLOW command, whole. A separate literal from `_DET_DIFF_FLAGS` on
# purpose: the defect that made Q17 necessary was exactly that the two
# commands' context widths drifted apart while both looked correct in isolation.
#
# 🔴 AND THE TWO ARE INDISTINGUISHABLE IN THE RENDERED SECTION, which is what
# made the F4 guard vacuous for a round. `_DET_DIFF_FLAGS` and this literal are
# unambiguous HERE — one ends `{head_sha}\"`, the other `..\"` — but both emit a
# line carrying the substring `git diff -U0 -w -M`, so a test asserting that
# substring against the section cannot say WHICH command it found. Q8 and Q22
# mutate the two separately for exactly that reason; a single row over a shared
# spelling would report coverage neither of them has.
_DET_REFLOW_COMMAND = (
    "f\"git diff -U0 -w -M --word-diff=porcelain {emit_from or '<from>'}..\""
)
# The narrowing clause — state (e) covers the PURE rewrap only, and the
# written-down count is the mitigation for the case the detector cannot see.
# The F1 concession's two load-bearing words. Mutated separately because they
# understate the gap in two different directions: one about WHEN the gate
# fires, one about WHICH ladders it can never fire for.
_DET_GATE_CONSECUTIVE = "\"the scaffolding changes ZERO payload lines, so two CONSECUTIVE such \""
_DET_UNCOVERED_SET = (
    '    "CONSECUTIVE rounds are payload-free has neither mechanism — one "\n'
    '    "alternating payload and scaffolding rounds included, which is a shape a "\n'
)
# The one-command check that settles the WHOLE-DIFF condition. Its absence is
# how the narrowing reads as free: the enumerable conjunct gets mistaken for
# the genuinely unanswerable PAYLOAD one.
_DET_ONE_COMMAND_CHECK = (
    '        + " **Whether THIS diff is whole-prose is a ONE-COMMAND check, not a "\n'
    '        "classification anybody has to invent**: `git diff --name-only "\n'
    '        "<from>..<to>`, then read whether every changed path is prose (`.md` "\n'
    '        "here). It is yours to run — this script does not classify, "\n'
    '        "deliberately — and it COULD be automated; it simply is not today. "\n'
)
_DET_REFLOW_NARROWING = (
    '        "by the `-U0 --word-diff` command above. 🔴 **That command finds the "\n'
    '        "PURE case only.** A round that rewraps a paragraph AND edits a word "\n'
    '        "in it — the ordinary shape of a ladder fix — shows `+`/`-` words, is "\n'
    '        "NOT state (e), stays scoreable and carries the SAME bias; for that "\n'
    '        "case the written-down count IS the mitigation, and a `--word-diff` "\n'
    '        "run that finds no reflow is NOT clearance.",\n'
)
_DET_RESTRAINTS = "        + PROSE_DETERMINATION_PRECONDITIONS,"
_DET_THRESHOLD_CMP = (
    "    nameable = Fraction(ladder, attributable) >= PROSE_LADDER_SHARE_THRESHOLD"
)
# 🔴 REAIMED BY THE FIX ROUND. `_not_measured` is now the ONLY constructor for
# a NOT MEASURED verdict — one predicate, one place — so the mutant that turns
# "not scoreable" into "scored a miss" belongs on it rather than on the
# structural-zero branch it used to target. Same hazard, wider reach: it now
# covers all five states at once, and the per-state distinction is what Q13/Q15
# and the driver table cover.
_DET_STRUCTURAL_ZERO = (
    '    return ProseStop(None, ladder, attributable, "NOT MEASURED: " + why, state)'
)


def det_floor_to_round_one(t):
    return _swap(t, _DET_FLOOR,
                 '    """\n    if round_no < 1:\n        return ""')


def det_section_dropped(t):
    return _swap(
        t,
        "        determination = render_prose_determination(\n"
        "            args.round_no, facts.emit_from, head_sha, "
        "round_one_anchor(blocks)\n"
        "        )\n"
        "        if determination:\n"
        '            print("\\n" + determination, file=out_stream)\n',
        "",
    )


def det_collision_reads_as_an_ordinary_boundary(t):
    """The structural-zero case rendered as if it were measurable.

    This is the `empty-result` trap in its live form: the anchor and the
    round's own `<from>` are one sha, every pre-image line then blames at or
    below it, and the share is 0 whatever the ladder did. Printing that as an
    ordinary boundary turns "not scoreable" into "scored zero".
    """
    return _swap(
        t, _DET_COLLISION,
        "    elif anchor:\n"
        "        boundary = (\n"
        '            f"`{anchor}` — the tip ROUND 1 audited. so every "',
    )


def det_prose_only_condition_dropped(t):
    """The section ships with no statement of the POPULATION it governs.

    Reaimed by the fix round: the scope moved into
    `PROSE_DETERMINATION_POPULATION` and narrowed from "this PR's payload is
    PROSE" to "this PR's WHOLE DIFF is prose", which is the population the
    threshold was derived on. Dropping it leaves the section reading as a stop
    rule for every delta round of every PR.
    """
    return _swap(
        t, _DET_PROSE_ONLY,
        '        "🔴 **This is for YOU, the ladder runner.** "\n',
    )


def det_collision_predicate_back_to_equality(t):
    """Q13 — `same_commit` open-coded as `!=` for the FOURTH time.

    `same_commit`'s own docstring calls a plain `==` between an 8-char
    `audited=` and a 40-char `rev-parse` sha "the quiet way this whole guard
    would fail to fire", and `anchor_is_head` carries "a predicate open-coded
    at three sites is wrong at two of them". `#1691` made it four. Under the
    mutant a structural zero renders as an ordinary measurable boundary.
    """
    return _swap(t, _DET_COLLISION_PREDICATE,
                 "    elif anchor and anchor != emit_from:")


def det_no_block_boundary_blames_a_block(t):
    """Q14 — the boundary line names an artefact the state proves absent."""
    return _swap(
        t, _DET_NO_BLOCK_BOUNDARY,
        '            "**NOT RECOVERABLE from this run** — this block carries no "\n'
        '            "`<from>`, so there is no range and no operand. The "\n'
        '            "determination is NOT MEASURED this round: run the next one"',
    )


def det_reflow_state_dropped_from_the_ledger(t):
    """Q15 — a state the code can RETURN is deleted from the shipped ledger.

    This is the `#1691` defect in the opposite direction: there, a returnable
    state had no sentence. `_not_measured` now REFUSES an undocumented key, so
    the mutant turns a silent omission into a loud one — which is the point of
    making the ledger the constructor's gate rather than a parallel list.
    """
    return _swap(t, _DET_REFLOW_ROW, '    "reflow-DELETED": (')


def det_reflow_branch_dropped(t):
    """Q16 — a REWRAPPED round scores a number instead of NOT MEASURED.

    The reflow is the rule's only bias towards STOPPING, so deleting the branch
    restores exactly the unsafe direction the fix round closed: the count is
    computed through a paragraph re-blamed wholesale to the rewrapper.
    """
    return _swap(
        t, _DET_REFLOW_BRANCH,
        "    if False:\n"
        "        return _not_measured(\n"
        '            "reflow", ladder, ladder + pr_authored,',
    )


def det_reflow_command_loses_U0(t):
    """Q17 — the SHIPPED defect, as a mutant. This is the state at `6bf15a8f`.

    The rule counts the hunks of `git diff -U0 -w -M`; state (e) asks whether
    one of THOSE hunks is word-identical. Without `-U0` the reflow command runs
    at three lines of context and MERGES neighbouring changes, so a purely
    rewrapped paragraph beside an edited one lands in a hunk that shows `+`/`-`
    words and (e) can never fire. MEASURED on `ca3b787c..6bf15a8f --
    claude/skills/audit-pr/SKILL.md`: 10 hunks with `-U0`, 4 without.

    ⚠ NOT killable by a one-paragraph fixture — that is why round 20 shipped it
    green. The killer runs the command this module EMITS against a two-paragraph
    repo and compares the two commands' hunk SETS, which is a relationship no
    reword of the flag can satisfy.
    """
    return _swap(
        t, _DET_REFLOW_COMMAND,
        "f\"git diff -w -M --word-diff=porcelain {emit_from or '<from>'}..\"",
    )


def det_reflow_loses_w_and_M(t):
    """Q22 — the REFLOW command loses `-w` and `-M`, the COUNTING one keeps them.

    Q8's twin, and the row that did not exist while the F4 guard was a bare
    `"git diff -U0 -w -M" in section`. That guard could not tell the two
    commands apart, so ONE row over the shared spelling was reporting coverage
    of both while covering neither alone.

    The defect is real and not cosmetic: the rule counts the pre-image lines of
    `git diff -U0 -w -M`'s hunks, and state (e) asks whether one of THOSE hunks
    is word-identical. A reflow command without `-w`/`-M` answers that question
    over a different hunk set — a whitespace-only reindent or a rename it can
    see and the counting command cannot — so a hunk the rule never counted can
    clear, or fail to clear, state (e).
    """
    return _swap(
        t, _DET_REFLOW_COMMAND,
        "f\"git diff -U0 --word-diff=porcelain {emit_from or '<from>'}..\"",
    )


def det_reflow_narrowing_dropped(t):
    """Q18 — state (e)'s sentence goes back to being WIDER than its detector.

    Restoring the pre-fix wording re-demotes the written-down count to a
    backstop over a detector that covers the PURE rewrap only, so a runner whose
    rewrap-plus-edit hunk shows word changes reads a clean detector result as
    clearance. `claude/RULES.md`: a guard's DESCRIPTION claims COVERAGE.
    """
    return _swap(
        t, _DET_REFLOW_NARROWING,
        '        "by the `--word-diff` command above, and the written-down count "\n'
        '        "is the backstop rather than the mitigation.",\n',
    )


def det_gate_concession_drops_CONSECUTIVE(t):
    """Q19 — the attribution gate reads as reachable from any two such rounds.

    It fires on two CONSECUTIVE zero-payload rounds. Without the word the
    concession makes the narrowed scope look cheaper than it is, which is the
    whole reason the concession exists.
    """
    return _swap(
        t, _DET_GATE_CONSECUTIVE,
        '"the scaffolding changes ZERO payload lines, so two such "',
    )


def det_uncovered_set_narrowed_back(t):
    """Q20 — the uncovered set shrinks back to "every round touches payload".

    The real set is any mixed-diff ladder in which no two CONSECUTIVE rounds are
    payload-free. A ladder ALTERNATING payload and scaffolding rounds is in it
    and would, under the narrow wording, read as covered by a gate that can
    never fire for it.
    """
    return _swap(
        t, _DET_UNCOVERED_SET,
        '    "CONSECUTIVE rounds are payload-free has neither mechanism — and "\n'
        '    "a scaffolding-carrying ladder whose every round DOES touch payload "\n',
    )


def det_one_command_check_dropped(t):
    """Q21 — the section states a condition and no way to settle it.

    `is the WHOLE DIFF prose?` is a one-command check; `is the PAYLOAD prose?`
    is the classification no artefact carries. Deleting the first makes the
    paragraph defend an unanswerable-classification claim about a population
    that IS enumerable — which is how the narrowing reads as free.
    """
    return _swap(t, _DET_ONE_COMMAND_CHECK, '        + " "\n')


def det_blame_loses_w_and_M(t):
    """F4 as a mutant. A bare blame is a DIFFERENT measurement, not a laxer one:

    measured over 159 corpus rounds it moves the share on 34 of them, on
    `#1688` r3 from 0.253 to 0.889, and flips the stop verdict on 3.
    """
    return _swap(t, _DET_BLAME_FLAGS, "git blame --porcelain ")


def det_diff_loses_w_and_M(t):
    return _swap(t, _DET_DIFF_FLAGS,
                 "f\"git diff -U0 {emit_from or '<from>'}..{head_sha}\"")


def det_restraints_dropped(t):
    """F2 as a mutant: the permission ships and its four limits do not."""
    return _swap(t, _DET_RESTRAINTS, '        + "",')


def det_not_measured_states_a_and_c_dropped(t):
    """Ship (b) alone — the state that owns a seam constant — and drop the rest.

    This is what the FIRST render of this section actually did, and it is the
    half-delivery shape of round-0 finding F2 reappearing one paragraph down: a
    guard bound to the constant sees nothing, because the constant is still
    there. Reaimed by the fix round, which builds the paragraph from
    `PROSE_NOT_MEASURED_STATES` — so the mutant now has to bypass the ledger,
    which is exactly the edit a guard on the COUNT WORD would still wave
    through.
    """
    return _swap(
        t,
        '        "⚠ **" + _COUNT_WORDS[len(PROSE_NOT_MEASURED_STATES)]\n'
        '        + " states are NOT MEASURED rather than a number, and each "\n'
        '        "means run the next round.** "\n'
        '        + " ".join(PROSE_NOT_MEASURED_STATES.values()),',
        '        "⚠ One state is NOT MEASURED rather than a number. "\n'
        "        + PROSE_DETERMINATION_STRUCTURAL_ZERO,",
    )


def det_conjunct_label_dropped(t):
    """The restraints ship as TEXT but nothing says they are CONJUNCTS.

    The narrower half of F2, and the one a reword reaches first: a paragraph
    that lists four conditions without saying every one of them must hold reads
    as context for the permission above it.
    """
    return _swap(
        t,
        '        "⚠ **NAMEABLE IS NOT SUFFICIENT.** Every one of these still has to hold, "',
        '        "⚠ Some background on the preconditions. "',
    )


def det_threshold_widened_to_a_half(t):
    """The number, moved. Every fixture row is a real corpus round, so this has

    to kill on the rows either side of two-thirds and not on the arithmetic.
    """
    return _swap(t, _DET_THRESHOLD_CMP,
                 "    nameable = Fraction(ladder, attributable) >= Fraction(1, 2)")


def det_threshold_narrowed_to_three_quarters(t):
    """The direction that FORBIDS `#1111` — the defect that withdrew the

    findings-keyed draft, re-entering as an off-by-a-twelfth on the fraction.
    """
    return _swap(t, _DET_THRESHOLD_CMP,
                 "    nameable = Fraction(ladder, attributable) >= Fraction(3, 4)")


def det_structural_zero_reads_as_a_measurement(t):
    """`None` -> `False`. Same ACTION (run the next round), different CLAIM."""
    return _swap(
        t, _DET_STRUCTURAL_ZERO,
        '    return ProseStop(False, ladder, attributable, "NOT MEASURED: " + why, state)')


def add_unledgered_clause(t):
    marker = ")\n\nINVARIANTS_HEADING"
    return _swap(t, marker,
                 '    Clause("unledgered", "**An eighth instruction nobody '
                 'reviewed.**"),\n' + marker)


def reword_stop_rule(t):
    return _swap(
        t,
        '"**A clean round ENDS the ladder — ending it is the CORRECT outcome, "\n'
        '        "not a failure.** ',
        '"**A clean round ends the ladder.** ',
    )


def hardcode_bullets(t):
    return _swap(
        t,
        '    out += [f"- {c.text}" for c in INVARIANT_CLAUSES]',
        '    out += ["- **READ-ONLY.**", "- **Do not merge.**"]',
    )


def remove_the_refusal(t):
    return _swap(t,
                 "    if args.round_no >= 2 and newest is None:",
                 "    if False:")


def claims_from_the_whole_comment(t):
    return _swap(
        t,
        "    claims = list(newest.items) if newest else []",
        '    claims = ([ln for ln in (comment_texts[0] if comment_texts else "")'
        ".splitlines() if ln.strip()] if newest else [])",
    )


def invert_cross_repo(t):
    # Re-targeted when the two-state `cross_repo` flag became the three-state
    # `repo_relation`. Same mutation: the two KNOWN states swap, and the
    # "unknown" state is left alone so this row stays distinct from P2.
    return _swap(t,
                 '        repo_relation = "same" if pr_repo == cwd_slug else "cross"',
                 '        repo_relation = "cross" if pr_repo == cwd_slug else "same"')


def numstat_failure_reads_zero(t):
    return _swap(
        t,
        '    if rc != 0:\n'
        '        return fail(f"the numstat command exited {rc}: "\n'
        '                    f"{(err or out).strip() or \'no output\'}")\n',
        '    if rc != 0:\n        out = ""\n',
    )


def classify_by_pathspec(t):
    # 🔴 RE-TARGETED IN ROUND 8, and it is the FIFTH row this file's
    # assert-before-editing has caught after a refactor nobody thought touched
    # the battery — this time via the new AMBIGUITY check rather than the
    # absence one. Round 8 added a second copy of this sentence in the
    # cross-repo hand-over at a deeper indentation, and the old eight-space
    # target matched THAT line first (eight spaces are a substring of twelve),
    # mutating a branch with no `led` in scope. The script now writes the line
    # from one place, `payload_summary_line`, which is what this targets.
    return _swap(
        t,
        '    return (f"round {facts.round_no} · payload lines changed THIS '
        'round: X "\n',
        '    return (f"round {facts.round_no} · payload lines changed THIS '
        'round: "\n'
        # `files` is None whenever the ledger could not measure (cross-repo,
        # a failed command), and `payload_summary_line` is now reached in
        # those states too — so the mutant guards it. A mutant that dies of an
        # AttributeError is killed by its own crash, not by the guard it is
        # supposed to be testing.
        "            f\"{sum(a + d for p, (a, d) in ((facts.ledger.files if "
        "facts.ledger else None) or dict()).items() if 'test' not in p)} \"\n",
    )


def the_warning_blocks(t):
    # 🔴 RE-TARGETED IN ROUND 10 — the SIXTH catch by assert-before-editing, and
    # this one was a pure RE-INDENT: the whole brief-rendering block moved
    # inside `if brief_refused is None:` so a refused run emits no document to
    # check. Nothing about the warning changed; the row would have scored "the
    # guard held" on a file it never edited.
    old = ('                  "emitted. Re-add them by hand, or re-run without '
           '--out "\n'
           '                  "edits.",\n'
           "                file=err_stream,\n"
           "            )\n")
    return _swap(t, old, old + "            return 4\n")


# --------------------------------------------------------------------------- #
# 🔴 W1-W5 — the five clause REWORDS that INVERT the instruction.
# --------------------------------------------------------------------------- #
# Every one of these passed a fully green 37-test suite while `CLAUSE_LEDGER`
# pinned a FRAGMENT per clause: the fragment stayed present and the instruction
# around it said the opposite. W5 is the sharp one — it makes every future brief
# tell auditors to do what `claude/RULES.md` forbids.
#
# Each replaces the whole `Clause(...)` entry, so the clause is still PRESENT,
# still emitted, and still ledgered by id. Only the whole-string pin can see it.

def reword_entry(kind, cid, new_text):
    """Replace an entry's TEXT, leaving it present, ledgered by id and emitted.

    Only a WHOLE-STRING pin can see this. Every presence check stays green.
    """
    def f(t):
        pat = re.compile(
            r"(    " + kind + r"\(\n        \"" + re.escape(cid) + r"\",\n)"
            r"(?:.*?\n)*?(    \),\n)"
        )
        return _sub_unique(
            pat,
            lambda m: m.group(1) + f'        "{new_text}",\n' + m.group(2),
            t,
            f"{kind.lower()} {cid!r} in its expected shape",
        )
    return f


def reword_clause(cid, new_text):
    return reword_entry("Clause", cid, new_text)


# 🔴 The SURVIVES control. Whitespace inside a clause changes; the instruction
# does not. `_norm` on both sides is what must absorb it — so a RED here means
# the pins are keyed to layout, and every reflow of the source becomes a
# failure.
def rewrap_a_clause(t):
    # 🔴 RE-TARGETED in round 4 — the `no-fetch` clause was reworded when THE
    # CHECKOUT section became three-state, and this row immediately reported
    # MUTATION DID NOT APPLY. Third time an assert-before-editing has caught a
    # row that would otherwise have scored as "the guard held".
    # 🔴 RE-TARGETED AGAIN in round 5, for the fourth such catch: the clause
    # went back to being unconditional ("write to any checkout that is not the
    # copy YOU made"), and this row reported MUTATION DID NOT APPLY on the
    # first run after that edit.
    return _swap(
        t,
        '"**Do NOT `git fetch`, `pull`, `checkout` or otherwise write to any "',
        '"**Do NOT  `git fetch`,  `pull`,  `checkout`  or otherwise write to  any "',
    )


# --------------------------------------------------------------------------- #
# The four decisions round 2's audit found WRONG, one mutant per site.
# --------------------------------------------------------------------------- #

def ledger_ignores_the_head_check(t):
    return _swap(t,
                 "    if head_check is not None and not head_check.ok:",
                 "    if False:")


def range_ignores_the_head_check(t):
    # 🔴 RE-TARGETED in round 3, and the reason is the one C3 records: the
    # branch became `elif` when the degenerate-self-range case was inserted
    # ahead of it, and this row immediately reported MUTATION DID NOT APPLY.
    # Without that assert it would have scored as "the guard held" — the most
    # flattering possible wrong answer.
    return _swap(t, "    elif hc is not None and hc.ok:", "    elif True:")


def emit_claims_uses_the_local_head(t):
    return _swap(
        t,
        '        head_sha = (data.get("headRefOid") or "")[:8]\n',
        '        _rc, _h, _ = runner(["git", "-C", repo_dir, "rev-parse",\n'
        '                             "--short", "HEAD"])\n'
        '        head_sha = _h.strip() if _rc == 0 else ""\n',
    )


def emit_claims_interpolates_the_placeholder(t):
    return _swap(
        t,
        "        audited = head_sha\n",
        '        audited = f"<the sha round 1 audited>..{head_sha}"\n',
    )


# 🔴 T1/T2 RE-TARGETED IN ROUND 14, SAME MUTATION, NEW SITE. The commands moved
# out of one `return [...]` literal into `_toolchain_gate_lines` /
# `_toolchain_checks_lines`, which build them from the PROBED repo. The defect
# they model is unchanged — a command under test pointed at the assembly
# checkout instead of the auditor's worktree — so the rows keep their ids and
# their killer sets. Left un-retargeted these would have failed to APPLY, which
# this harness reports rather than scoring as "the guard held".
def gate_points_at_the_shared_checkout(t):
    return _swap(
        t,
        'cmds.append(f"{shell}bash <your worktree>/scripts/gate.sh{tier}")',
        'cmds.append(f"{shell}bash {tc.root}/scripts/gate.sh{tier}")',
    )


def nix_build_points_at_the_shared_checkout(t):
    return _swap(
        t,
        '        *[f"nix build <your worktree>#checks.{NIX_SYSTEM}.{n}'
        ' --no-link -L"\n'
        "          for n in tc.check_names],\n",
        '        *[f"nix build {tc.root}#checks.{NIX_SYSTEM}.{n} --no-link -L"\n'
        "          for n in tc.check_names],\n",
    )


def pr_slug_reads_the_head_repo(t):
    return _swap(
        t,
        '    m = _PR_URL.match((data.get("url") or "").strip())\n'
        "    if m:\n"
        '        return f"{m.group(1)}/{m.group(2)}"\n'
        '    if data.get("isCrossRepository") is False:\n',
        "    if True:\n",
    )


def unknown_repo_collapses_to_same(t):
    return _swap(
        t,
        "    if pr_repo and cwd_slug:\n"
        '        repo_relation = "same" if pr_repo == cwd_slug else "cross"\n'
        "    else:\n"
        '        repo_relation = "unknown"\n',
        '    repo_relation = "cross" if (\n'
        "        pr_repo and cwd_slug and pr_repo != cwd_slug\n"
        '    ) else "same"\n',
    )


def the_check_command_reports_clean(t):
    return _swap(t, "    gone = missing_clauses(text)\n", "    gone = []\n")


def the_out_file_is_not_read_back(t):
    # 🔴 RE-TARGETED IN ROUND 10, for the same re-indent as `the_warning_blocks`.
    return _swap(
        t,
        '                checked_text = Path(args.out).read_text(encoding="utf-8")\n'
        "                checked_what = args.out\n",
        "                pass\n",
    )


def missing_clauses_is_not_normalised(t):
    return _swap(
        t,
        "    haystack = _norm(brief)\n"
        "    return [c.id for c in INVARIANT_CLAUSES if _norm(c.text) not in haystack]",
        '    return [c.id for c in INVARIANT_CLAUSES if c.text not in (brief or "")]',
    )


def the_cumulative_reason_is_dropped(t):
    return _swap(
        t,
        "                ledger = ledger._replace(cumulative_reason=cum.reason)",
        "                pass",
    )


def an_unclosed_fence_is_skipped_silently(t):
    return _swap(
        t,
        "            if close_at is None:\n                malformed.append(",
        "            if close_at is None:\n"
        "                i += 1\n"
        "                continue\n"
        "            if False:\n"
        "                malformed.append(",
    )


def a_longer_closing_fence_stops_closing(t):
    return _swap(
        t,
        'if bm and len(bm.group("fence")) >= len(opener):',
        'if bm and len(bm.group("fence")) == len(opener):',
    )


def continuation_lines_are_dropped(t):
    return _swap(
        t,
        "        elif items and line.strip():\n"
        '            items[-1] = f"{items[-1]} {line.strip()}"\n',
        "",
    )


def the_nested_fence_report_is_removed(t):
    return _swap(
        t,
        "            if any(_CLAIM_ITEM.match(t) for t in tail) and any(",
        "            if False and any(",
    )


def the_refusal_drops_the_comment_kinds_note(t):
    return _swap(
        t,
        '            "  ⚠ `gh pr view --json comments` returns ISSUE comments only. A "',
        '            "  ⚠ A "',
    )


# --------------------------------------------------------------------------- #
# 🔴 N1-N7 — the round-3 findings, one mutant per site.
# --------------------------------------------------------------------------- #
# N1 restores the shipped defect outright: `audited=` meant `<to>` to the
# WRITER and `<to>` to the READER, so `<to>..HEAD` had HEAD on both ends and the
# delta range was EMPTY BY CONSTRUCTION. N3 is its mirror image — the "one
# anchor for everything" simplification, which is wrong in the other direction
# and would NOT show up as an empty range, only as a superset.

def range_anchor_reads_the_audited_tip(t):
    return _swap(
        t,
        "    return block.audited_from or block.audited_to or None",
        "    return block.audited_to or None",
    )


def range_anchor_loses_the_bare_fallback(t):
    return _swap(
        t,
        "    return block.audited_from or block.audited_to or None",
        "    return block.audited_from or None",
    )


def emit_claims_writes_the_range_anchor(t):
    return _swap(
        t,
        "    if facts.emit_from:\n"
        '        audited = f"{facts.emit_from}..{head_sha}"\n',
        "    if facts.prev_sha:\n"
        '        audited = f"{facts.prev_sha}..{head_sha}"\n',
    )


def the_range_ignores_a_self_range(t):
    return _swap(t, "    if anchor_is_head(facts.prev_sha, hc):", "    if False:")


def the_ledger_ignores_a_self_range(t):
    return _swap(t, "        if anchor_is_head(prev_sha, head_check):",
                 "        if False:")


def emit_claims_drops_the_self_range_warning(t):
    # 🔴 RE-TARGETED in round 4: the predicate became
    # `same_commit(next_anchor, head_sha)` when the guard was moved onto the
    # anchor the NEXT round will use. Disabling it now silences BOTH spellings
    # of the warning, which is why this row gained a second killer.
    return _swap(t, "        if same_commit(next_anchor, head_sha):",
                 "        if False:")


def the_empty_range_names_the_refuted_causes(t):
    return _swap(t, "        if head_check is not None and head_check.ok:\n",
                 "        if False:\n")


def the_unknown_head_reason_names_both_causes(t):
    """The THIRD false-cause site: ignore what the caller knows and list both."""
    return _swap(
        t,
        "        why = no_sha_reason or (\n",
        "        why = None or (\n",
    )


# --------------------------------------------------------------------------- #
# 🔴 X1-X3 — the three verbatim instruction blocks that shipped UNPINNED.
# --------------------------------------------------------------------------- #
# Each of these, applied alone, left the round-2 suite fully green at 58 passed.
# X1 and X3 preserve the fragment an existing test pins and invert the sentence
# around it — the walkable-by-rewording shape from `claude/RULES.md`. Only the
# whole-string DIRECTIVE_LEDGER can see them.

def add_unledgered_directive(t):
    marker = ")\n\nDIRECTIVE = {d.id: d.text for d in SECTION_DIRECTIVES}"
    return _swap(t, marker,
                 '    Directive("unledgered", "**A fourth instruction nobody '
                 'reviewed.**"),\n' + marker)


# --------------------------------------------------------------------------- #
# 🔴 Y1-Y14 — round 4.
# --------------------------------------------------------------------------- #
# Y1 and Y2 are the two OPERATIVE verbatim blocks that were still unpinned after
# round 3's sweep — each inverted alone against all 199 audit-related tests, all
# green. Y3-Y5 are the three that remain unpinned by choice, and they are
# recorded as DOCUMENTED HOLES rather than as controls: a hole that survives is
# not a guard doing its job.

def the_audited_flag_is_ignored(t):
    return _swap(t,
                 "    emit_from = args.audited or emit_anchor(newest)",
                 "    emit_from = emit_anchor(newest)")


def the_ignored_audited_flag_is_silent(t):
    return _swap(t,
                 "    if args.audited and not args.emit_claims:",
                 "    if False:")


def the_round_one_emit_warning_is_unreachable_again(t):
    """The round-3 spelling restored: ask `emit_from`, which is None at round 1."""
    return _swap(
        t,
        "        next_anchor = range_anchor(\n"
        '            ClaimsBlock(args.round_no, facts.emit_from or "", head_sha, [])\n'
        "        )\n",
        "        next_anchor = facts.emit_from\n",
    )


def the_ledger_hand_rolls_its_degenerate_causes(t):
    return _swap(
        t,
        '                "anything. " + directive("degenerate-range-causes")\n',
        '                "anything. The round\'s fix commits are not in this "\n'
        '                "checkout, or the `audited=` field was written with "\n'
        '                "the fix tip in the `<from>` position."\n',
    )


def the_range_hand_rolls_its_degenerate_causes(t):
    return _swap(
        t,
        '            + directive("degenerate-range-causes")\n',
        '            + "Either the round\'s fix commits are not in this "\n'
        '            "checkout, or the `audited=` block was written with the "\n'
        '            "fix tip in its `<from>` position."\n',
    )


def same_commit_is_case_sensitive(t):
    return _swap(t,
                 '    a, b = (a or "").lower(), (b or "").lower()',
                 '    a, b = (a or ""), (b or "")')


def the_git_dirs_are_compared_as_strings(t):
    return _swap(
        t,
        "    git_dir, common_dir = _resolved(lines[0]), _resolved(lines[1])",
        "    git_dir, common_dir = lines[0], lines[1]",
    )


# 🔴 Y6 AND Y14 WERE RE-TARGETED IN ROUND 7 and the reason is a FIX, not a
# refactor: `render_checkout`'s inline state->directive dict became the
# module-level `CHECKOUT_STATE_DIRECTIVE`, so the relationship it encodes can
# be READ by a guard instead of being spelled as an id prefix. Both mutations
# still express exactly what they expressed before — the unknown state
# collapsing into SHARED, and a private worktree being described as shared.
def the_unknown_worktree_state_collapses_to_shared(t):
    return _swap(t,
                 '    "unknown": "checkout-unknown",\n}',
                 '    "unknown": "checkout-moves",\n}')


def a_private_worktree_is_called_shared(t):
    return _swap(t,
                 '    "private": "checkout-private",',
                 '    "private": "checkout-moves",')


def where_to_work_drops_the_private_note(t):
    return _swap(t,
                 '    if facts.worktree.kind == "private":',
                 "    if False:")


# 🔴 Y3-Y5 — the DOCUMENTED HOLES. Each inverts an operative sentence that is
# deliberately NOT pinned, and each must SURVIVE. A row that starts being killed
# is GOOD NEWS and is reported as an action, not as a pass: promote it to a
# killer set and delete its line from the ledger comment in the script.

def the_output_contract_drops_the_per_finding_format(t):
    return _swap(
        t,
        '        "Findings by severity (🔴 deploy-blocking / 🟡 should-fix / 🟢 nit), "\n'
        '        "each in the format required above. ',
        '        "Findings by severity, a one-paragraph summary is enough. ',
    )


def the_toolchain_drops_name_the_tier(t):
    # Round 14: the sentence moved into the `TOOLCHAIN_TAIL` constant, so its
    # indentation dropped from 8 to 4. Same sentence, same mutation.
    return _swap(
        t,
        '    "Name the tier and the base sha in any claim you make about the gate — "',
        '    "There is no need to name the tier or the base sha. "',
    )


def the_round_one_range_points_at_the_pr_description(t):
    return _swap(
        t,
        '            f"A FIRST, FULL audit: read the whole PR diff (`gh pr diff "\n'
        '            f"{facts.pr}`) and the code it touches, not just the PR "\n'
        '            "description.",\n',
        '            "A FIRST, FULL audit: the PR description is the fastest "\n'
        '            "way in.",\n',
    )


# --------------------------------------------------------------------------- #
# 🔴 Z1-Z8 — round 5.
# --------------------------------------------------------------------------- #
# Round 4 removed the false SHARED claim from the private checkout state and
# replaced it with a WRITE GRANT over a tree that is not the auditor's, and
# reworded the `no-fetch` clause from unconditional to conditional on a state
# `gather_worktree_kind` cannot know (it measures the ASSEMBLER's cwd). Z1-Z3
# restore each half. Z4 puts the toolchain's refuted rationale back, Z5-Z7 the
# two `--emit-claims` holes, Z8 the unscoped cause clause.


def the_private_state_grants_writing_again(t):
    """Round 4's text, verbatim — 'yours alone … Writing here is fine'."""
    return reword_entry(
        "Directive", "checkout-private",
        "🔴 **This is a PRIVATE worktree — yours alone.** Its `.git` is a link "
        "into a shared repository, but the WORKING TREE is not shared, so "
        "files appearing, vanishing or changing under you is **NOT expected "
        "here and IS worth reporting** — that is the sibling-agent clobber "
        "`claude/RULES.md` describes, not background noise. **If something "
        "moves, report it with what moved and when.** Writing here is fine: "
        "the no-write clause below is about a SHARED checkout, and this is "
        "not one.")(t)


def the_shared_state_drops_the_no_write_rule(t):
    """Round 4's `checkout-moves`, which stated no rule of its own."""
    return reword_entry(
        "Directive", "checkout-moves",
        "🔴 **This checkout is SHARED with other sessions and agents. It "
        "MOVES UNDER YOU** — the branch can change, files can appear and "
        "vanish, and commits can land mid-audit. That is expected and is NOT "
        "your fault and NOT a finding. **Report what you observed moving and "
        "carry on; do not chase it, and do not try to restore it.**")(t)


def a_fourth_checkout_state_arrives_with_no_rule(t):
    """A new checkout state nobody checked carries the no-write rule.

    🔴 ROUND 7 EXTENDED IT TO REGISTER THE STATE IN THE RENDERER'S MAP. Round 5
    added only the `Directive`, which was enough while the guard derived its set
    from the id PREFIX `checkout-`. The guard now derives it from
    `CHECKOUT_STATE_DIRECTIVE` — what `render_checkout` can actually SELECT —
    so a directive nobody selects reaches no brief and, correctly, is no longer
    that guard's business. Adding the map entry is what keeps this row meaning
    "a fourth STATE with no rule" rather than "an unledgered directive".
    """
    marker = ")\n\nDIRECTIVE = {d.id: d.text for d in SECTION_DIRECTIVES}"
    t = _swap(t, marker,
              '    Directive("checkout-detached", "🔴 **This checkout is on '
              'a DETACHED HEAD.** Nobody wrote a rule for it."),\n' + marker)
    return _swap(t,
                 '    "unknown": "checkout-unknown",\n}',
                 '    "unknown": "checkout-unknown",\n'
                 '    "detached": "checkout-detached",\n}')


# --------------------------------------------------------------------------- #
# 🔴 V1-V7 — round 7 (base `3619fe68`).
# --------------------------------------------------------------------------- #
# V1/V2 restore the two halves of the `checkout-*` PREFIX derivation round 6
# walked; V3 the false blind-spot rationale; V4 the forward reference that
# narrowed the no-write rule; V5 the whitespace input check; V6 the verified
# branch's hardcoded `HEAD`; V7 the cross-repo-only toolchain reword that a
# two-scenario equality could not see.


def the_private_state_is_renamed_and_stripped_of_its_rule(t):
    """Round 6's demonstrated disarm, as far as a SCRIPT-ONLY mutation reaches.

    🔴 AND THE HALF IT CANNOT REACH IS RECORDED RATHER THAN IMPLIED. Round 6's
    measurement also updated the THREE LEDGERS in the test module — which is
    what made the suite stay at 89 green — and this battery mutates the script
    and nothing else. So the ledger pins fire here where they did not fire
    there, and this row is WIDE for that reason. `V2` is the isolating row for
    the derivation change; this one exists so the demonstrated disarm has a
    line in the battery at all.
    """
    t = _swap(t, '        "checkout-private",\n', '        "assembly-private",\n')
    t = _swap(t, '    "private": "checkout-private",',
              '    "private": "assembly-private",')
    return reword_entry(
        "Directive", "assembly-private",
        "🔴 **This is the checkout this brief was ASSEMBLED in — not "
        "necessarily the one you are standing in.** Git reports it as a "
        "PRIVATE linked worktree: its `.git` is a link into a shared "
        "repository, and the working tree belongs to the session that BUILT "
        "this brief, which is not you. That session is live in it, so files "
        "here can appear, vanish and change. If you do see something move "
        "here, report it with what moved and when.")(t)


def a_fourth_state_selects_an_existing_rule_less_directive(t):
    """🔴 THE ISOLATING ROW for round 6's 🟢 F5.

    Adds a fourth SELECTABLE state pointing at a directive that already exists
    and is already ledgered — so `SECTION_DIRECTIVES` is untouched, both
    two-way ledger pins stay green, and the ONLY thing that can see it is a
    guard that reads the renderer's own selection. Under the round-5 spelling
    (`ids beginning "checkout-"`) this mutation was invisible.
    """
    return _swap(t,
                 '    "unknown": "checkout-unknown",\n}',
                 '    "unknown": "checkout-unknown",\n'
                 '    "detached": "own-worktree-is-writable",\n}')


def the_blind_spot_blames_sha_resolution_again(t):
    """The rationale that was false for three rounds, restored."""
    return _swap(
        t,
        "# 🔴 AND THE REASON GIVEN HERE FOR THREE ROUNDS WAS FALSE. It asserted that",
        "# This script never resolves a sha, so whether the token is a real\n"
        "# commit is a question it cannot answer.\n"
        "# 🔴 AND THE REASON GIVEN HERE FOR THREE ROUNDS WAS FALSE. It asserted that",
    )


def the_own_worktree_grant_scopes_the_rule_to_sharedness_again(t):
    """Round 5's second sentence, verbatim."""
    return reword_entry(
        "Directive", "own-worktree-is-writable",
        "That worktree is YOURS: fetching and checking out inside it is fine. "
        "The no-write rule below is about the SHARED checkout.")(t)


def the_whitespace_input_check_is_removed(t):
    return _swap(
        t,
        "    if args.audited is not None and args.audited.split() != [args.audited]:",
        "    if False:",
    )


def the_verified_range_hands_out_head_again(t):
    # 🔴 RE-TARGETED IN ROUND 8. Round 7 wrote the tip inline in
    # `render_range`'s verified branch; round 8 moved it into `range_tip`
    # because the DEGENERATE branch of the same `if`/`elif` still handed out
    # the token. One predicate, one place — so this row now mutates the
    # predicate, and its killer set GREW to include the degenerate range's own
    # test. That growth is the consolidation working, not a pin doing new work.
    return _swap(
        t,
        "    if hc is not None and hc.ok:\n        return hc.local_sha\n",
        '    if hc is not None and hc.ok:\n        return "HEAD"\n',
    )


# --------------------------------------------------------------------------- #
# 🔴 V8-V15 — round 8 (base `28492af2`).
# --------------------------------------------------------------------------- #
# V8 and V10 are V3's plant IN THE TWO SHAPES ROUND 7's NORMALISER COULD NOT
# SEE, and they exist because V3 planted on ONE line: the guard scored 0 for the
# same sentence wrapped across two `#` lines and for either single-quoted
# spelling of an implicit concatenation, while its own prose claimed both. The
# ladder writes every round's narrative into a wrapped comment block, so the
# shape the battery never exercised is the shape the defect would arrive in.

_BLIND_SPOT_ANCHOR = (
    "# 🔴 AND THE REASON GIVEN HERE FOR THREE ROUNDS WAS FALSE. It asserted that"
)


def the_blind_spot_rationale_wraps_across_two_comment_lines(t):
    """V3's false rationale, with the PHRASE ITSELF split by a line break.

    The break falls INSIDE `never resolves a sha` — between "resolves" and "a
    sha" — because a plant that keeps the phrase whole on one line is just V3
    with extra text and proves nothing about the wrap.
    """
    return _swap(
        t,
        _BLIND_SPOT_ANCHOR,
        "# This script never resolves\n"
        "# a sha in that checkout, so whether a well-formed token is a real\n"
        "# commit is a question it cannot answer.\n" + _BLIND_SPOT_ANCHOR,
    )


def the_blind_spot_rationale_hides_in_single_quoted_concat(t):
    """The same rationale, split by a SINGLE-QUOTED implicit concatenation.

    Round 7 joined adjacent literals with `re.sub(r'"\\s*"', "", …)`, which sees
    the double-quoted spelling only — while its docstring said the literals are
    joined "exactly as the compiler joins them". The compiler does not care
    which quote character was used.
    """
    return _swap(
        t,
        _BLIND_SPOT_ANCHOR,
        "_blind_spot_note = ('This script never resolves '\n"
        "                    'a sha in that checkout.')\n" + _BLIND_SPOT_ANCHOR,
    )


def the_head_check_stops_comparing_the_two_shas(t):
    """Round 2's fourth read rule, disarmed — `HEAD` is assumed to be the PR's.

    🔴 THE ISOLATING ROW FOR THE CROSS-REPO SEAM GUARD, from the only side a
    SCRIPT mutation can reach it. What was actually wrong at `28492af2` was the
    test module's FIXTURE (`OTHER_REPO_PR` inherited `DEFAULT_PR`'s
    `headRefOid`, so the assembly checkout was modelled as standing on a commit
    of another repository), and this battery mutates the script and nothing
    else. Removing the sha comparison reaches the same rendered state — a brief
    that says CROSS-REPO and "verified at assembly time" in one document.
    """
    return _swap(t, "    if local != pr_head_sha:", "    if False:")


# 🔴 RE-TARGETED IN ROUND 10, both of them: the two branches stopped asking
# `repo_relation` directly and now ask `cross_repo_holds_neither_end`, the
# shared predicate round 10's finding B introduced. Same mutation — the branch
# never fires — at the branch's own condition, so it stays distinct from V19,
# which mutates the PREDICATE and leaves both branches selectable.
def the_cross_repo_range_diagnoses_a_moved_checkout_again(t):
    return _swap(t, "    elif cross_repo_holds_neither_end(facts):",
                 "    elif False:")


def the_cross_repo_ledger_reports_a_failed_command_again(t):
    return _swap(
        t,
        "    if facts.round_no >= 2 and cross_repo_holds_neither_end(facts):",
        "    if False:",
    )


def the_degenerate_range_hands_out_head_again(t):
    """Round 7's half-applied state, restored: the OTHER branch of the if/elif."""
    return _swap(
        t,
        "    if anchor_is_head(facts.prev_sha, hc):\n        note = (",
        '    if anchor_is_head(facts.prev_sha, hc):\n        tip = "HEAD"\n'
        "        note = (",
    )


def the_no_fetch_clause_narrows_to_this_audit_again(t):
    """Round 7's clause scope: the copy you made FOR THIS AUDIT, and no other."""
    return reword_entry(
        "Clause", "no-fetch",
        "**Do NOT `git fetch`, `pull`, `checkout` or otherwise write to any "
        "checkout that is not the copy YOU made for this audit — including the "
        "one THE CHECKOUT section names, which is where this brief was "
        "assembled and is not yours.** Other sessions are in those trees; a "
        "fetch there is a write with cross-session blast radius, and every ref "
        "you need is already resolved for you here.")(t)


def the_no_anchor_refusal_is_removed(t):
    """The fifth instance restored: a parsed block is read as an anchor."""
    return _swap(
        t,
        "    if args.round_no >= 2 and newest is not None and not prev_sha:",
        "    if False:",
    )


def the_skipped_round_warning_is_removed(t):
    """The asymmetry restored: only a block from a LATER round warns."""
    return _swap(
        t,
        "    elif newest is not None and newest.round_no < args.round_no - 1:",
        "    elif False:",
    )


def the_ledger_provenance_line_hands_out_head_again(t):
    return _swap(
        t,
        '    measured_tip = hc.local_sha if (hc is not None and hc.ok) else "HEAD"',
        '    measured_tip = "HEAD"',
    )


# --------------------------------------------------------------------------- #
# 🔴 V18-V23 — round 10. Base `706a6b38`.
# --------------------------------------------------------------------------- #

def the_tip_is_always_claimed_to_be_a_sha(t):
    """Round 10's finding A: the placeholder is handed over as a commit.

    The NARROWEST expression that can be wrong — the predicate's own return,
    not the branch that consults it. Mutating the enclosing `elif` would take
    the whole cross-repo branch with it and die for someone else's reason.
    """
    return _swap(
        t,
        "    return range_tip(facts) != TIP_PLACEHOLDER",
        "    return True",
    )


def the_ledger_asks_the_repo_relation_before_the_head_check(t):
    """Round 10's finding B, restored at the predicate both sites now share.

    `if False:` on the head-check short-circuit is the base's behaviour
    exactly: the relation alone decides, so a renamed-remote clone that HOLDS
    the PR's head is told it holds neither end of the range.
    """
    return _swap(
        t,
        "    hc = facts.head_check\n"
        "    if hc is not None and hc.ok:\n"
        "        return False\n"
        '    return facts.repo_relation == "cross"',
        "    hc = facts.head_check\n"
        "    if False:\n"
        "        return False\n"
        '    return facts.repo_relation == "cross"',
    )


def the_anchorless_refusal_blocks_its_own_remedy(t):
    """Round 10's finding C at REFUSAL 1b — the site the finding named."""
    return _swap(
        t,
        '            "and nothing from the comment.",\n'
        "        ]), file=err_stream)\n"
        "        if not args.emit_claims:\n"
        "            return 2\n"
        "        brief_refused = 2\n",
        '            "and nothing from the comment.",\n'
        "        ]), file=err_stream)\n"
        "        return 2\n",
    )


def the_no_block_refusal_blocks_its_own_remedy(t):
    """Round 10's finding C at REFUSAL 1 — the SIBLING site.

    Kept as its own row rather than folded into the one above: the two
    refusals are independent `return`s, and a fix applied at the site a finding
    names while the sibling keeps the defect is this ladder's single most
    repeated shape.
    """
    return _swap(
        t,
        '            "round as an explicit first, full audit with no --round.",\n'
        "        ]), file=err_stream)\n"
        "        if not args.emit_claims:\n"
        "            return 2\n"
        "        brief_refused = 2\n",
        '            "round as an explicit first, full audit with no --round.",\n'
        "        ]), file=err_stream)\n"
        "        return 2\n",
    )


def the_two_placeholder_spellings_drift_apart(t):
    """Round 10's finding A', at the site the constant was extracted FROM.

    `range_tip`'s fallback stops naming `TIP_PLACEHOLDER` and spells its own
    near-miss, which is exactly the state the constant was introduced to make
    impossible: the script emits one string and every guard looks for another.
    """
    return _swap(
        t,
        "    return hc.pr_sha if (hc is not None and hc.pr_sha) else TIP_PLACEHOLDER",
        "    return hc.pr_sha if (hc is not None and hc.pr_sha) else "
        "\"<the PR's head sha, unknown>\"",
    )


def the_assumed_base_is_recorded_as_read(t):
    """Round 10's SEVENTH instance: the default stops announcing itself.

    The narrowest expression again — the flag's own assignment, not the sites
    that consult it, so the mutant cannot die of a missing name.
    """
    return _swap(
        t,
        '    base_assumed = not data.get("baseRefName")',
        "    base_assumed = False",
    )


def the_clone_grant_permits_fetching_again(t):
    """Round 10's finding D: round 8's wording, verbatim."""
    return reword_entry(
        "Directive", "own-worktree-is-writable",
        "That worktree is YOURS, and so is the clone you made it from: "
        "fetching and checking out inside either is fine. The no-write rule "
        "below is about every checkout you did not make.")(t)


def the_clone_grant_reverts_to_the_enumeration(t):
    """Round 12's finding: round 10's wording, verbatim.

    The grant is still present, still ledgered, still emitted, and still
    refuses three verbs BY NAME — so every presence pin stays green and only
    the enumeration probe plus the whole-string ledger can see it. That is the
    hole: `remote update`, `switch`, `restore`, `reset`, `branch -f` and `gc`
    are permitted by omission in the one tree whose blast radius is outside
    the brief.
    """
    return reword_entry(
        "Directive", "own-worktree-is-writable",
        "That worktree is YOURS: fetching and checking out inside it is fine. "
        "In the clone you made it from, `git worktree add` is the ONLY write "
        "this brief asks for — do not `fetch`, `pull` or `checkout` there, "
        "whoever made it, because other sessions may be standing in it. The "
        "no-write rule below is about every checkout you did not make.")(t)


def the_unknown_repo_cause_goes_back_to_two_branches(t):
    """Round 13's finding F1: round 12's `unknown_side`, verbatim.

    The PREDICATE half. Two branches for three causes, so a `--claims-file`
    run — which consults no `gh` — is told `gh` did not report, two headings
    from the same brief's own sentence saying no `gh` was consulted.
    """
    return _swap(
        t,
        '        unknown_sides = []\n'
        '        if not facts.cwd_repo_slug:\n'
        '            unknown_sides.append(\n'
        '                "this checkout has no `origin` remote to resolve a slug from"\n'
        '            )\n'
        '        if facts.repo == REPO_UNKNOWN:\n'
        '            unknown_sides.append(\n'
        '                facts.repo_unknown_reason\n'
        '                or "nothing this run consulted reported which repository the "\n'
        '                   "PR lives in"\n'
        '            )\n'
        '        unknown_side = "; and ".join(unknown_sides) or (\n'
        '            "the two sides of the comparison could not be resolved"\n'
        '        )\n',
        '        unknown_side = (\n'
        '            "this checkout has no `origin` remote to resolve a slug from"\n'
        '            if not facts.cwd_repo_slug else\n'
        '            "`gh` did not report which repository the PR lives in"\n'
        '        )\n',
    )


def the_claims_file_names_the_gh_cause(t):
    """Round 13's finding F1: the INPUT half, not the predicate.

    Exactly the V24/V25 split one field over. The three-way branch can be
    correct while the reason the `--claims-file` branch hands it is the `gh`
    sentence — and then the brief says the same false thing at rc 0.
    """
    return _swap(
        t,
        '        repo_unknown_reason = (\n'
        '            "this run is `--claims-file` mode, which consults no `gh` and so "\n'
        '            "never learns which repository the PR lives in — and neither "\n'
        '            "`--repo` nor a PR url supplied it"\n'
        '        )\n',
        '        repo_unknown_reason = (\n'
        '            "`gh pr view` was consulted and reported no `url` this script "\n'
        '            "could read a repository out of, and `--repo` was not passed"\n'
        '        )\n',
    )


def the_repo_lookup_drops_repo_again(t):
    """Round 13's finding F1, sibling: round 12's bare `gh pr view`.

    Run in the auditor's own repository it returns THAT repository's PR of the
    same number, at rc 0 — so the one answer it can produce is the "same repo"
    the section it sits in exists because it cannot rule out.
    """
    return _swap(
        t,
        '                f"gh pr view {facts.pr} --json url --repo <owner/name — this "\n'
        '                "run never learned which repository the PR is in>",\n',
        '                f"gh pr view {facts.pr} --json url",\n',
    )


def the_recipe_goes_back_to_naming_the_head_branch(t):
    """Round 13's finding F2: round 12's recipe, verbatim.

    `worktree add` resolves `<the PR's head branch>` against refs the clone
    already has, and putting it there is a `fetch` the grant refuses.
    Measured on scratch repos, git 2.55.0: rc 128 for an unfetched clone, and
    rc 128 for a FORK PR after any fetch.
    """
    return _swap(
        t,
        '            "```",\n'
        '            f"git -C <your local clone of {facts.repo}> fetch origin "\n'
        '            f"refs/pull/{facts.pr}/head:refs/audit/pr{facts.pr}-r{facts.round_no}",\n'
        '            f"git -C <your local clone of {facts.repo}> worktree add --detach "\n'
        '            f"/tmp/audit-pr{facts.pr}-r{facts.round_no} "\n'
        '            f"refs/audit/pr{facts.pr}-r{facts.round_no}",\n'
        '            "# when you are done, undo both:",\n'
        '            f"git -C <your local clone of {facts.repo}> worktree remove "\n'
        '            f"/tmp/audit-pr{facts.pr}-r{facts.round_no}",\n'
        '            f"git -C <your local clone of {facts.repo}> update-ref -d "\n'
        '            f"refs/audit/pr{facts.pr}-r{facts.round_no}",\n'
        '            "```",\n',
        '            "```",\n'
        '            f"git -C <your local clone of {facts.repo}> worktree add "\n'
        '            f"/tmp/audit-pr{facts.pr}-r{facts.round_no} <the PR\'s head branch>",\n'
        '            "```",\n',
    )


def the_clone_grant_respells_the_enumeration(t):
    """Round 13's finding F4, and it is the mutant the OLD probe could not see.

    Measured at `6349a8b9`: re-spelling round 10's `do not …, … or … there` as
    `never …, … or … in that clone` left the whole 109-test suite GREEN. The
    closed list is back — `remote update`, `switch`, `restore`, `reset`,
    `branch -f`, `gc` permitted by omission — and only the whole-string ledger
    saw the reword at all, which says nothing about the hazard. The verb-SET
    relationship is what kills it now.
    """
    return reword_entry(
        "Directive", "own-worktree-is-writable",
        "That worktree is YOURS: fetching and checking out inside it is fine. "
        "In the clone you made it from, `git worktree add` is the ONLY write "
        "this brief asks for and the ONLY one you may make — never `fetch`, "
        "`pull` or `checkout` in that clone, whoever made it, because other "
        "sessions may be standing in it. The no-write rule below is about "
        "every checkout you did not make.")(t)


def the_claims_file_hardcodes_the_base_ref(t):
    """Round 12's finding: `--claims-file` mode asserting a base it never read.

    The base's behaviour exactly. `base_assumed` is
    `not data.get("baseRefName")`, so hardcoding the field switches the
    assumed-base banner OFF in the one mode that consults no `gh` at all —
    which is the mode three comments in the script call permanently assumed.
    """
    return _swap(
        t,
        '        data = {"title": f"PR #{args.pr}", "url": ""}',
        '        data = {"title": f"PR #{args.pr}", "url": "", '
        '"baseRefName": "main"}',
    )


# 🔴 THE CAVEAT THE LEDGER PIN HAD NO ROW FOR. Round 10 shrank C3's killer set
# and `test_the_ledger_says_the_base_was_not_fetched` fell out of the battery
# entirely — zero occurrences across every row — while sitting in
# `INVARIANT_GUARDS_AND_LEDGERS`, whose declared evidence IS the battery.
# TWO rows, because they answer different questions: V26 asks whether anything
# detects the caveat's ABSENCE, V27 whether anything detects a REWORD that
# leaves the old fragment pins intact.
def the_stale_base_caveat_is_deleted(t):
    """The caveat removed outright; every other ledger sentence untouched."""
    return _swap(
        t,
        '        "⚠ `<base>` here is `" + facts.base_ref + "` **as it stands '
        'in this "\n'
        '        "checkout**, and it is the ONLY end of that command that can '
        'mean "\n'
        '        "something different where you are standing — both ends of '
        'the range "\n'
        '        "itself are shas. This script does not fetch (that would be '
        'a write to "\n'
        '        "a checkout it does not own), so a stale base re-reports '
        'upstream work "\n'
        '        "as this round\'s payload. If the number looks large, that is '
        'the first "\n'
        '        "thing to check.",\n',
        "",
    )


def the_stale_base_caveat_says_it_is_fine(t):
    """🔴 THE REACHABILITY CONTROL for the whole-string pin.

    The qualifier the caveat exists for — "as it stands in THIS CHECKOUT" — is
    replaced by an assurance that it is fine, which is the opposite
    instruction. Both fragments the guard used to pin ("does not fetch",
    "stale base") survive downstream untouched, so a fragment pin CANNOT see
    this. Measured at `88b4105c`: this exact reword left the suite at 109
    passed.
    """
    return _swap(
        t,
        '"` **as it stands in this "\n'
        '        "checkout**, and it is the ONLY end of that command that can '
        'mean "\n',
        '"` and it is fine. It is the ONLY end of that command that can '
        'mean "\n',
    )


def a_third_prescription_site_nothing_drives(t):
    """🔴 ROUND 12 — THE COVERAGE ARM of the refusal-remedy guard.

    An ADDITION rather than an inversion, and that is the point: the guard
    calls itself a class guard over "a third refusal that prescribes a command
    it refuses", so the mutation is a third prescription — planted in the
    `--audited`-without-`--emit-claims` branch, which neither driven case
    reaches. Every other assertion in that guard stays green (the two real
    refusals still prescribe commands that still run); only the site-coverage
    assertion can see it.
    """
    return _swap(
        t,
        "    if args.audited and not args.emit_claims:\n        print(\n",
        "    if args.audited and not args.emit_claims:\n"
        "        print(\n"
        '            f"  Fix: run `audit-dispatch.py {args.pr} --round 1 "\n'
        '            "--emit-claims --audited <sha>`.",\n'
        "            file=err_stream,\n"
        "        )\n"
        "        print(\n",
    )


def a_third_prescription_site_written_through_err_stream(t):
    """🔴 ROUND 13 — V29's MUTATION IN THE SPELLING THE SCANNER COULD NOT SEE.

    Same planted third prescription, same unreached branch — but written
    `err_stream.write(...)` instead of `print(..., file=err_stream)`. Round
    12's `prescription_sites` matched `ast.Call` whose func is the NAME
    `print`, so an `Attribute` func fell straight out of the set and this
    mutant SURVIVED a green 109-test suite, while the guard's docstring went
    on calling itself a class guard. The scanner reads every call now.

    It is deliberately the SAME defect as V29, differing only in the API used
    to print it — which is the whole finding: the hazard is the unrun
    prescription, never the function that emitted it.
    """
    return _swap(
        t,
        "    if args.audited and not args.emit_claims:\n        print(\n",
        "    if args.audited and not args.emit_claims:\n"
        "        err_stream.write(\n"
        '            f"  Fix: run `audit-dispatch.py {args.pr} --round 1 "\n'
        '            "--emit-claims --audited <sha>`.\\n"\n'
        "        )\n"
        "        print(\n",
    )


def the_toolchain_names_the_shared_checkout_cross_repo_only(t):
    """🔴 THE ISOLATING ROW for round 6's 🟢 F4.

    The forbidden phrase comes back, but ONLY in the cross-repo branch. Round
    5's guard drove two scenarios, both SAME-REPO, so this reword left the
    suite at 89 green with no row naming it. Only a guard that drives every
    scenario — and requires the section byte-identical across them — can see it.
    """
    # 🔴 ROUND 14 RE-TARGETED IT, AND THE MUTATION GOT *HARDER* TO WRITE —
    # which is the point of moving the reason into a no-argument constant.
    # There is no longer any `facts` in scope where the reason is built, so a
    # scenario-dependent rationale can only be smuggled back in by APPENDING
    # one at the render site. That is exactly what this does, and it is still
    # cross-repo-only: round 5's guard drove two same-repo scenarios and left
    # this reword at 89 green with no row naming it.
    return _swap(
        t,
        "    tc = detect_repo_toolchain(toolchain_probe_root(facts))\n",
        "    tc = detect_repo_toolchain(toolchain_probe_root(facts))\n"
        '    if facts.repo_relation == "cross":\n'
        "        return \"\\n\".join([\n"
        "            TOOLCHAIN_HEAD,\n"
        '            "so running that copy runs the suite in the SHARED CHECKOUT '
        'on whatever branch it is standing on",\n'
        "            TOOLCHAIN_COMMANDS_HEADING,\n"
        "            TOOLCHAIN_TAIL,\n"
        "        ])\n",
    )


def the_no_fetch_clause_is_conditional_again(t):
    """Round 4's spelling: armed only when the section says SHARED."""
    return reword_entry(
        "Clause", "no-fetch",
        "**Do NOT `git fetch`, `pull`, `checkout` or otherwise write to a "
        "SHARED checkout — THE CHECKOUT section of this brief says whether "
        "the one it names is shared.** Other sessions are in a shared tree; a "
        "fetch there is a write with cross-session blast radius, and every "
        "ref you need is already resolved for you here.")(t)


def the_toolchain_reason_names_the_shared_checkout_again(t):
    # Round 14: the reason is `TOOLCHAIN_HEAD`, so the false claim goes back
    # into the constant. Same words, same finding, one indent level out.
    # Round 15 re-wrapped the constant (the false "at most ONCE" count came out
    # of it), so the target moved with the wrap — the ASSERT-BEFORE-RUN check
    # is what caught that rather than a silently unmutated file.
    return _swap(
        t,
        '    "it: a gate script resolves its root from its own path, so running that "\n'
        '    "copy runs the suite in a checkout that is NOT yours — one holding none of "\n'
        '    "your mutations — and a `nix build <ref>#…` builds that ref\'s tree, not "\n'
        '    "yours.",\n',
        '    "it: a gate script resolves its root from its own path, so running the "\n'
        '    "shared copy runs the suite in the SHARED CHECKOUT on whatever branch it "\n'
        '    "is standing on, and a `nix build <ref>#…` builds that ref\'s tree.",\n',
    )


def the_emitted_block_loses_its_legend(t):
    """🔴 RETARGETED when the legend gained its `payload=` sentence.

    MEASURED before the retarget: `MUTATION DID NOT APPLY — target absent`,
    which is the failure this harness's header calls the most flattering
    possible wrong answer — an unmutated file reporting "the guard held". The
    trailing `"",` also left this list, so the target is the legend STRING
    alone now.
    """
    return _swap(
        t,
        '        "  legend: `<from>` = the tip THIS round\'s audit READ · `<to>` = the "\n'
        '        "head THIS round\'s FIXES produced. Different shas — `<from>` is older. "\n'
        '        "`payload=` = the payload lines THIS round\'s fixes changed, from YOUR "\n'
        '        "classification of the ledger\'s file list.",\n',
        "",
    )


def the_emitted_block_is_not_round_tripped(t):
    return _swap(t,
                 "        if facts.emit_from:\n"
                 "            why = emitted_block_reads_back_as_written(",
                 "        if False:\n"
                 "            why = emitted_block_reads_back_as_written(")


def an_empty_audited_is_ignored_again(t):
    return _swap(t,
                 "    if args.audited is not None and not args.audited.strip():",
                 "    if False:")


def the_degenerate_causes_stop_scoping_the_omission(t):
    """Round 4's clause: the omission is blamed on every round."""
    return _swap(
        t,
        '        "`<from>` position — which is what a bare round-1 `audited=<sha>` "\n'
        '        "always means, and what `--emit-claims` records when `--audited` is "\n'
        '        "omitted AT ROUND 1; from round 2 on, omitting it is correct, because "\n'
        '        "`<from>` is then recovered from the previous block\'s `<to>`. "\n'
        '        "`<from>` is the tip the PREVIOUS round AUDITED, so "\n'
        '        "re-emit that block with `--audited <that round\'s actual tip sha>` "\n'
        '        "and re-assemble — or nothing has been committed and pushed to "\n',
        '        "`<from>` position — which is what `--emit-claims` records when "\n'
        '        "`--audited` is omitted, and what a bare round-1 `audited=<sha>` "\n'
        '        "always means; `<from>` is the tip the PREVIOUS round AUDITED, so "\n'
        '        "re-emit that block with `--audited <the tip that round actually "\n'
        '        "read>` and re-assemble — or nothing has been committed and pushed to "\n',
    )


# --------------------------------------------------------------------------- #
# 🔴 V36-V41 — round 14. Base `9e23c379` (#1104 merged). Target-repo probes.
# --------------------------------------------------------------------------- #
# Each probe is broken in BOTH directions where both are reachable: V36 claims
# an artifact that is absent, V37 denies one that is present. A single
# direction grades only half of a predicate, and the half it omits is the one
# that silently STOPS prescribing a real command.
#
# 🔴 ROUND 15 — THAT SENTENCE WAS A RULE THE ROWS DID NOT OBEY, and three
# probes had NO mutant in either direction. Measured at `5bad0a0c`, each
# inversion survived a fully green 115-test suite while shipping a fabricated
# command. The ledger is now explicit, so "both directions" is checkable rather
# than asserted:
#
#     probe            claims-it-exists   denies-it-exists
#     gate_sh          V36                V37
#     ci_suite         V45                V46
#     gate_tier        V47                V48
#     py_tests         V49                V44 (the hedge collapsing into "none")
#     check_names      V38 (text as code) V42 (a real shape denied)
#     probe_root       V40                — (there is no "probe less" defect
#                                            here; the not-probed branch IS the
#                                            honest answer, and V7 covers its
#                                            body being replaced)
#
# The cause of the gap was FIXTURE REACH, not a missing assertion:
# `_write_repo(py_test=False)` was called by nothing, no fixture had a
# `gate.sh` without `--tier both`, and no test asked whether `run-ci-suite.sh`
# was ABSENT from the devrc-shaped brief. A branch no fixture enters is a
# branch no assertion can grade, so the row would have been vacuous even if
# somebody had written it.


def the_gate_is_prescribed_without_probing_for_it(t):
    """V36 — pretend `scripts/gate.sh` exists everywhere. THE ORIGINAL DEFECT."""
    return _swap(t, "    if tc.gate_sh:\n", "    if True:\n")


def the_gate_is_never_prescribed_even_where_it_exists(t):
    """V37 — the mirror: a real `scripts/gate.sh` goes unmentioned.

    The direction a one-sided mutation misses. A probe wired to always-False
    keeps every "did not fabricate" assertion green while quietly removing the
    repository's real gate from every brief.
    """
    return _swap(t, "    if tc.gate_sh:\n", "    if False:\n")


def the_checks_probe_matches_the_word_not_the_output(t):
    """V38 — the probe reads TEXT rather than CODE. RE-TARGETED IN ROUND 15.

    It used to loosen the regex, because the regex WAS the defence: a loose
    `^\\s*checks\\b[^\\n=]*=` matched `checks = pr.get("statusCheckRollup", [])`
    inside a python heredoc in `homelab-infra`'s real devShell, so a flake
    declaring NO checks output answered yes and the honest fallback became
    unreachable.

    🔴 THAT SPELLING IS NOW INERT, AND SAYING SO IS THE POINT. Round 15's fix
    strips nix strings and comments BEFORE either pattern runs, so the heredoc
    is not text any regex can see — loose or strict, the answer is the same and
    the row would have reported SURVIVED, a hole where a covered hazard used to
    be. The defence moved, so the mutation moves with it: drop the strip, and
    `CHECKS_DECL` matches that same heredoc line in the raw source. Same
    hazard, same killers, at the layer that now carries it.
    """
    return _swap(t, "    code, clean = _nix_scan(text)\n",
                 "    code, clean = text, True\n")


def a_state_dependent_sentence_moves_into_the_commands_bar(t):
    """V52 — round 15's BLOCKING finding, as a row.

    Round 14 moved the reason into a constant and lifted the cross-scenario pin
    off the COMMANDS bar entirely. This is the sentence that walked the gap:
    keyed on the AUDITOR's own worktree kind — which no probe reads — and
    flatly contradicting WHERE TO WORK, exactly as round 5's did. Measured at
    `5bad0a0c`: **115 passed, SURVIVED**. The same sentence inside the old
    whole-section `return [...]` at `9e23c379` was killed by this guard, so the
    finding was reopened by the text sitting three lines lower.
    """
    anchor = (
        '            "standing on another branch, so treat a command that has since "\n'
        '            "moved as a lookup, never as a finding.",\n'
    )
    return _swap(t, anchor, anchor + (
        '            (f"Your checkout is a PRIVATE linked worktree ({facts.worktree}), so "\n'
        '             "the SHARED checkout is the right place to run the gate."\n'
        '             if facts.worktree and facts.worktree.kind == "private" else\n'
        '             f"Worktree kind: {facts.worktree.kind if facts.worktree else None}."),\n'
    ))


def the_checks_probe_denies_a_shape_it_cannot_enumerate(t):
    """V42 — round 15. `[]` collapses back into `None`: the CONFIDENT answer.

    A `checks` binding whose names this parse cannot read is "declared, names
    unreadable"; answering `None` makes the brief state in bold that the
    repository has NO sandbox tier — the tier the same section calls the one
    the merge gates on. Measured at `5bad0a0c` for `genAttrs`, flake-parts'
    `perSystem`, `checks.<system>.default` and `checks.<system> = base // {…}`.
    """
    return _swap(
        t,
        "        return [] if CHECKS_DECL.search(code) else None\n",
        "        return None\n",
    )


def the_indented_string_escapes_are_terminators_again(t):
    """V43 — round 15. `''${…}` and `'''` read as closing an indented string.

    The measured consequence on devrc's OWN flake with one `echo ''${HOME}` in
    the `pytests` build script: `['pytests', 'nodetests']` -> `['pytests',
    'rc', 'rc']`. Two fabricated `nix build` targets named after a shell
    variable, and `nodetests` — a check the merge really gates on — dropped in
    silence. Isolated to the escape branch: the terminator branch and the depth
    arithmetic are untouched, so a kill here is attributable to escapes alone.
    """
    return _swap(
        t,
        '                if nxt in ("$", "\'"):         # \'\'${…}  and  \'\'\'  — escapes\n',
        "                if False:\n",
    )


def the_envrc_sentence_is_hardcoded_again(t):
    """V39 — the remembered string instead of the file on disk."""
    return _swap(
        t,
        '                f"is broken. This repo\'s `.envrc` is {_fmt_uses(tc)}, which "\n',
        '                "is broken. This repo\'s `.envrc` is `use opencode`, which "\n',
    )


def the_probe_reads_the_cwd_repo_cross_repo_too(t):
    """V40 — probe the WRONG repository rather than admit to knowing nothing.

    The fix's own trap. Cross-repo the cwd IS a probeable repository; it is
    simply not the one the PR lives in, so this yields a confident,
    fully-formed, entirely irrelevant command list — the original defect with a
    new source. It also collapses every scenario onto one commands bar, which
    is the second thing the scenario guard now refuses.
    """
    return _swap(
        t,
        '    return facts.cwd_repo_dir if facts.repo_relation == "same" else None\n',
        "    return facts.cwd_repo_dir\n",
    )


def the_checks_scan_stops_tracking_nix_strings(t):
    """V41 — the `''…''` skip is dropped. RE-TARGETED IN ROUNDS 15 AND 16.

    Same defect, one layer over each time: the toggle lived in the name scan,
    then in `_nix_strip`, and now in the CODE frame of `_nix_scan`, so the
    mutation is still "an indented string is not a string". A brace inside a
    build script then moves the depth counter and the block closes early, so a
    flake's SECOND check name is never read. Isolated to the one branch that
    OPENS an indented string from code: `"…"`, `#`, `/* */`, the escape
    handling and the depth arithmetic are untouched.
    """
    return _swap(
        t,
        '            if two == "\'\'":\n                blank(i, i + 2)\n'
        '                stack.append(["ind"])\n',
        '            if False:\n                blank(i, i + 2)\n'
        '                stack.append(["ind"])\n',
    )


def the_walk_cap_answers_no_python_tests(t):
    """V44 — round 15. The cap fails SILENTLY again, as it did at `5bad0a0c`.

    "I stopped looking" collapses into "there are none", which is the confident
    answer, and the brief then omits the subset command with no note at all.
    Measured reachable on a real repository: `~/workspace/civit` has 157,319
    directories within depth ≤ 4 after the skip list and its first `test_*.py`
    at walk visit 1,185.
    """
    return _swap(t,
                 "                return PY_TESTS_UNKNOWN\n",
                 "                return PY_TESTS_NONE\n")


def the_ci_suite_is_prescribed_without_probing_for_it(t):
    """V45 — round 15. `run-ci-suite.sh` for a repo that has none.

    V36's shape at the sibling site the both-directions rule above never
    reached: measured SURVIVING a fully green 115-test suite at `5bad0a0c`,
    because no fixture asserted that runner was ABSENT from the devrc-shaped
    brief.
    """
    return _swap(t, "    if tc.ci_suite:\n", "    if True:\n")


def the_ci_suite_is_never_prescribed_even_where_it_exists(t):
    """V46 — the mirror of V45: a real `run-ci-suite.sh` goes unmentioned."""
    return _swap(t, "    if tc.ci_suite:\n", "    if False:\n")


def the_gate_tier_flag_is_assumed(t):
    """V47 — round 15. `--tier both` appended to any repo's gate.sh.

    The EXACT fabrication that field exists to prevent, and it survived at
    `5bad0a0c`: no fixture had a `gate.sh` lacking `--tier both`, so the branch
    that omits the flag was entered by nothing.
    """
    return _swap(
        t,
        '        gate_tier=bool(gate and "--tier" in gate and "both" in gate),\n',
        "        gate_tier=True,\n",
    )


def the_gate_tier_flag_is_never_prescribed(t):
    """V48 — the mirror: a `gate.sh` that DOES take `--tier both` runs without
    it, so the auditor runs one tier and reports on two."""
    return _swap(
        t,
        '        gate_tier=bool(gate and "--tier" in gate and "both" in gate),\n',
        "        gate_tier=False,\n",
    )


def the_python_tests_are_assumed_present(t):
    """V49 — round 15. `python3 -m pytest` for a repo with no python tests.

    `_write_repo`'s `py_test=` was never passed `False` by any call site, so
    the branch that suppresses the command was executed by no test and the
    inversion SURVIVED at `5bad0a0c`.
    """
    return _swap(t,
                 "    if tc.py_tests == PY_TESTS_NONE:\n",
                 "    if False:\n")


def the_wrong_shell_note_goes_back_inside_the_pytest_branch(t):
    """V50 — round 15. The diagnosis is emitted only where pytest is.

    The measured state at `5bad0a0c`: the bar lived in
    `_toolchain_pytest_lines`, so a repository with a real gate, a flake
    devShell providing `nodejs` and no python tests got a BARE gate command and
    no wrong-shell diagnosis anywhere in the section. `node: command not found`
    then reads exactly like a broken gate.
    """
    return _swap(
        t,
        '        if any(ln.startswith("```") for ln in prescriptions):\n',
        "        if False:\n",
    )


def the_wrong_shell_note_is_pytest_only_again(t):
    """V51 — round 15. The language-agnostic sentence is reworded away.

    Leaves the pytest sentence and the phrase `WRONG SHELL` in place, so a
    guard that asks only for those stays green while an auditor whose gate
    fails on `node`, `go` or `cargo` has been told nothing. The SPELLED-guard
    control for V50: the bar is present, and says less than it claims to.
    """
    return _swap(
        t,
        '            "🔴 A `<tool>: command not found` from ANY command above is the "\n'
        '            "WRONG SHELL and not a broken gate — in EVERY language. The "\n',
        '            "🔴 A note about the wrapper. The "\n',
    )


# --------------------------------------------------------------------------- #
# Round 16 — V53-V61. Base `ba321c06`, this branch's own head one round on.
# --------------------------------------------------------------------------- #

def a_nested_interpolation_is_string_text_again(t):
    """V53 — round 16, the FABRICATION direction of the nested-`''` defect.

    Drops the one branch that makes `${…}` inside an indented string re-enter
    CODE, so the `''` that OPENS a nested string reads as the OUTER one's
    terminator again and the interpolation body is scanned as code. Measured at
    `ba321c06` on `shellHook = '' ${lib.optionalString c '' checks = { unit = 1;
    } ''} '';` — a flake declaring NO checks output answered **`['unit']`**, so
    the brief fenced `nix build <wt>#checks.x86_64-linux.unit` at a repository
    that has no such attribute.

    Isolated to that branch: the escape handling, the terminator and the brace
    arithmetic are untouched, so a kill here is attributable to interpolation
    tracking alone.
    """
    return _swap(
        t,
        '            if two == "${":                   # an interpolation: CODE again\n',
        "            if False:\n",
    )


def an_identifier_ending_in_apostrophes_opens_a_string(t):
    """V54 — round 16, the DENIAL direction of the same class.

    Drops the maximal-munch identifier branch, so `foo'' = 1;` — a legal nix
    name, because `'` is an identifier character — is read as `foo` followed by
    a string opener that never closes. Measured at `ba321c06`: the rest of the
    file is blanked and the answer is `None`, the brief's bolded "**no `checks`
    output**", for a flake whose next binding is the checks block.
    """
    return _swap(
        t,
        '            if ch.isalpha() or ch == "_":\n',
        "            if False:\n",
    )


def the_lexer_never_admits_it_got_lost(t):
    """V55 — round 16. The uncertainty valve is welded shut.

    `_nix_scan` reports whether it ended in a state a nix file can be in. With
    the valve gone, an unterminated string or a `_PROBE_MAX_BYTES` truncation
    answers with whatever the patterns happen to say over the mis-lexed text —
    and both of round 16's measured failures routed to the CONFIDENT answer,
    which is the whole reason the hedge exists.
    """
    return _swap(t, "    if not clean:\n        return []\n",
                 "    if False:\n        return []\n")


def the_scan_stops_stripping_line_comments(t):
    """V56 — round 16. `#` comments are code again.

    The reachability control for the comment branch, and it is NOT the inert
    `CHECKS_SHAPES_NOT_CODE` row: the anchored regex rejects a `#`-commented
    `checks` line whatever the stripper does. What this reaches is the other
    direction — an ordinary sentence in a comment (`# … the ''-string idiom …`)
    opening an indented string that never closes, blanking the REAL checks
    block after it.
    """
    return _swap(
        t,
        '            if ch == "#":                     # line comment\n',
        "            if False:\n",
    )


def _not_probed_sentence_keyed_on(kind):
    """A WHERE-TO-WORK-contradicting sentence in the NOT-PROBED commands bar.

    🔴 THE TWO ROWS BUILT FROM THIS ARE A PAIR, AND THE PAIR IS THE POINT.
    Round 15 pinned the commands bar within each declared target and guarded its
    own non-degeneracy with `any(len(kinds) >= 2)`. Measured at `ba321c06`, the
    partition→checkout-state map was

        probed-devrc-shape    n=6  {private, shared, unknown}
        cross-repo-otherproj  n=5  {private, shared}
        repo-unknown          n=2  {shared}

    so `any` was satisfied by the first row alone and NO not-probed scenario was
    `unknown` kind at all. `kind="unknown"` SURVIVED at **122 passed**;
    `kind="private"` was killed. Same insertion, same file, one word apart —
    which is what makes the survivor a measurement of the guard's blind spot
    rather than a claim about it.
    """
    def mutate(t):
        anchor = (
            '        "`Makefile` target, the repo\'s CI workflow, its `CLAUDE.md` — and "\n'
            '        "**NAME the one you used, by path**, in your report.",\n'
        )
        return _swap(t, anchor, anchor + (
            f'        ("Your checkout is a {kind.upper()} one, so the SHARED "\n'
            '         "checkout is the right place to run the gate."\n'
            f'         if facts.worktree and facts.worktree.kind == "{kind}" else ""),\n'
        ))
    mutate.__name__ = f"a_not_probed_sentence_keyed_on_{kind}"
    return mutate


def the_head_reclaims_prose_it_does_not_always_have(t):
    """V59 — round 16. Round 15's own replacement wording, restored.

    "the prose names it too, to say what was read out of it" is a claim about a
    STATE frozen into the state-INDEPENDENT constant. Counted at `ba321c06`:
    true in the 6 probed scenarios (3 prose lines), false in the 5 cross-repo
    ones (1 line, and it says "is a DIFFERENT repository"), flatly false in the
    2 repo-unknown ones (0 — the path is absent from the section).

    🔴 IT IS KILLED BY A WHOLE-STRING PIN, NOT BY A WORD. `claude/RULES.md`:
    when the artifact under test IS prose, a guard on words is walkable by
    rewording, so the constant is pinned normalised and whole. A cosmetic reword
    goes red here on purpose — that is the price of a machine-readable claim,
    and the docstring on the guard says what to re-measure before changing it.
    """
    return _swap(
        t,
        '    "resolves the dev shell and nothing else; wherever else it appears it is "\n'
        '    "PROSE — a statement ABOUT that checkout, never something to run in. Never "\n',
        '    "resolves the dev shell and nothing else; the prose names it too, to say "\n'
        '    "what was read out of it. Never "\n',
    )


def the_pytest_diagnosis_is_emitted_without_a_pytest_command(t):
    """V60 — round 16. The bar diagnoses a command the section did not give.

    Measured at `ba321c06`: with `py_tests` NONE and a flake that names pytest,
    the section said "no python subset command is prescribed" and then opened
    the wrong-shell bar with `A bare python3 -m pytest failing…` three lines
    later. The reader goes looking for a command that is not there.
    """
    return _swap(t, "        if tc.py_tests == PY_TESTS_FOUND:\n",
                 "        if True:\n")


def the_cached_build_fallback_loses_its_empty_drv_guard(t):
    """V62 — round 17. The guard whose absence is an AFFIRMATIVE false green.

    The fallback resolves `DRV=$(nix path-info --derivation ...)` then runs
    `nix log "$DRV"`. Drop the `[ -n "$DRV" ]` guard and an unresolvable
    attribute leaves `$DRV` EMPTY -- `nix log ""` then resolves as `.` and
    prints the CWD FLAKE'S DEFAULT PACKAGE log. The auditor greps a FOREIGN log
    that may read `RESULT: PASS (exit=0)`.

    Silence would be survivable; this makes the block affirmatively vouch for a
    tier that never ran, which is why the guard is pinned BY NAME and not by a
    count of guards.
    """
    return _swap(t, "        NIX_LOG_DRV_GUARD,\n", "")


def the_drv_guards_stop_becomes_a_trailing_comment(t):
    """V63 — round 18. The stop REMOVED while every word of it survives.

    V62 deletes the guard wholesale, which any presence check sees. This is the
    shape a presence check CANNOT see: `... cat "$ERR"; }   # exit 1`. The
    braces close with no `exit`, so the guard echoes its diagnosis and the block
    walks on into `nix log ""` — the CWD flake's default package log, which may
    read `RESULT: PASS (exit=0)`. Measured SURVIVED at 127 passed against the
    pre-round-18 test module, whose check was `"exit 1" in line`; the line
    filter there drops only lines that START with `#`, so the comment rode
    along inside the asserted line.

    🔴 NARROWEST EXPRESSION THAT CAN BE WRONG. Nothing else on the line moves —
    same guard, same diagnosis, same `$ERR` — so a kill is attributable to the
    stop and to nothing else.
    """
    return _swap(t, 'cat "$ERR"; exit 1; }\'', 'cat "$ERR"; }   # exit 1\'')


def the_verdict_grep_is_deleted_from_the_last_line(t):
    """V64 — round 18. The block stops asking whether a verdict was printed.

    The last line becomes `grep -c "panic: test timed out" "$LOG"; echo done`.
    Walked against a log with NO `RESULT:` line this exits 0 where the shipped
    block exits 1 — the auditor is told nothing is wrong by a block that no
    longer looks. Measured SURVIVED: the pre-round-18 assertion only refused a
    LAST COMMAND matching `^grep -c`, and `echo done` is not one.
    """
    return _swap(t, '"$LOG"; grep -n "RESULT:" "$LOG"\',',
                 '"$LOG"; echo done\',')


def a_true_is_appended_after_the_verdict_grep(t):
    """V65 — round 18. The verdict grep is still there and sets nothing.

    `...; grep -n "RESULT:" "$LOG"; true` — the grep runs, prints, and then
    `true` overwrites `$?`, so the block exits 0 whatever the log says. The
    other direction of V64's gap: V64 removes the right command, this one keeps
    it and takes away its status. Both survived the negative-only pin.
    """
    return _swap(t, '"RESULT:" "$LOG"\',', '"RESULT:" "$LOG"; true\',')


def the_stderr_path_is_fixed_while_the_log_path_is_not(t):
    """V66 — round 18. Cross-agent truncation, in the DIAGNOSTIC path.

    `LOG=` keeps its `mktemp` — so the concurrency pin, which read `LOG=`
    alone, stays green — while `ERR=` becomes a fixed `/tmp` path that every
    sibling agent truncates. The `$DRV` guard then `cat`s a diagnosis that may
    belong to another run's tier, which is worse than no diagnosis: it names a
    cause.

    🔴 THE HALF THE PIN COULD NOT SEE, mutated ALONE on purpose. Moving both
    assignments would die to the `LOG=` check and prove nothing about `ERR=`.
    """
    return _swap(t, 'ERR=$(mktemp -t audit-tier-XXXXXXXX.err)',
                 'ERR=/tmp/audit-tier.err')


def the_drv_guards_stop_hides_in_a_comment_that_ends_in_the_suffix(t):
    """V68 — round 19. V63's hazard, spelled to satisfy an `endswith` pin.

    🔴 THE LADDER'S SIGNATURE FAILURE, IN THE COMMIT THAT FIXED V63. Round 18
    replaced `"exit 1" in line` with `line.endswith("exit 1; }")` — and
    `endswith` anchors at the END of the line, which is exactly where a
    trailing comment lives. `re.match` excludes comments because it anchors at
    the START; `endswith` cannot. So:

        [ -n "$DRV" ] || { echo "…"; cat "$ERR"; }   # exit 1; }

    ends in `exit 1; }`, carries no `exit` in its CODE, and measured **128
    passed, rc 0** against round 18's own fix. The rendered block prints
    `NO DERIVATION` and walks on into `nix log ""` — the CWD flake's
    default-package log. Byte-for-byte V63's hazard, one character apart from
    the mutation that IS killed.

    The fix is not a wider suffix: it is to judge the CODE and not the LINE.
    """
    return _swap(t, 'cat "$ERR"; exit 1; }\'', 'cat "$ERR"; }   # exit 1; }\'')


def a_true_is_reached_by_or_after_the_verdict_grep(t):
    """V69 — round 19. V65's hazard, reached by `||` instead of `;`.

    Round 18 took the last COMMAND as `lines[-1].split(";")[-1]` — a split on
    `;` alone, so a trailing `true` reached by `||` is invisible to it while the
    assertion's own failure message says "A trailing `true` … make[s] the block
    exit 0". Measured **128 passed, rc 0**.

    Walked against a log with NO `RESULT:` line: the shipped block exits 1,
    V65 (`; true`, killed) exits 0, and this (`|| true`, survived) exits 0.
    Identical hazard, different spelling — and the same root cause admits
    `| head` and a wrong file argument, which is why the fix splits on every
    unquoted separator and pins the WHOLE command rather than its prefix.
    """
    return _swap(t, '"RESULT:" "$LOG"\',', '"RESULT:" "$LOG" || true\',')


def the_stop_note_prescribes_wrapping_alone_again(t):
    """V67 — round 18. The prescription that does not stop, restored verbatim.

    This is what shipped: "Wrap the block in a function and call it", with "Do
    NOT simply swap `exit` for `return`" beside it. `exit` inside a function
    still exits the shell, so wrapping alone changes nothing — measured under a
    pty, `run_tier(){ …; exit 1; }; run_tier` kills both `bash -i` and
    `zsh -i`. The note was NOT pinned by anything at the time: `grep` for `Wrap
    the block`, `run_tier` or `INTERACTIVE shell` across the test module and
    this harness returned zero hits.

    The mutation is scoped to the PRESCRIPTION sentence; the surrounding
    measurement prose is untouched, so a kill is attributable to the advice.
    """
    return _swap(
        t,
        '    "🔴 **Two forms stop the block without closing your shell: run it AS A "\n'
        '    "SCRIPT (`bash <file>`), or wrap it in a function AND swap `exit` for "\n'
        '    "`return` — `run_tier() { … return 1; }; run_tier`.** Wrapping ALONE is "\n'
        '    "not one of them: `exit` inside a function still exits the shell, MEASURED "\n'
        '    "under a pty in interactive bash and interactive zsh — `run_tier() { … "\n'
        '    "exit 1; }; run_tier` killed both. 🔴 The swap works only INSIDE the "\n'
        '    "function: `return` AT TOP LEVEL is not a stop, and MEASURED in "\n',
        '    "🔴 **Wrap the block in a function and call it** — `run_tier() { … }; "\n'
        '    "run_tier`. Do NOT simply swap `exit` for `return`: at top level "\n'
        '    "`return` is not a stop, and MEASURED in "\n',
    )


def the_absence_bar_points_at_a_bar_that_is_never_above_it(t):
    """V61 — round 16. The dangling cross-reference, restored.

    The UNKNOWN and NONE branches are mutually exclusive (`if UNKNOWN: return
    …`), so "a different answer from the one above" names a bar that is emitted
    in exactly the runs where this one is not.
    """
    return _swap(
        t,
        '            "from \\"the walk did not finish\\", which this brief says instead "\n',
        '            "from the one above, which this brief says instead "\n',
    )


# 🔴 The SURVIVES control for the directive ledger. Whitespace changes; the
# instruction does not. A RED here means the new pin is keyed to layout.
def rewrap_a_directive(t):
    return _swap(
        t,
        '"Also hunt for **regressions this fix round itself introduced** — the "',
        '"Also  hunt  for **regressions this fix round itself introduced**  — the "',
    )


LEDGER_TRIO = {
    "test_the_invariant_clause_ledger_is_pinned_two_way",
    "test_each_clause_carries_the_instruction_its_ledger_entry_names",
    "test_the_rendered_section_holds_exactly_the_ledgered_clauses",
}
DELETION_CONTROL = "test_control_a_clause_deleted_from_the_constant_is_detected"

# 🔴 ROUND 8's scope guard reads BOTH SIDES of the no-write rule — the
# `no-fetch` clause AND every sentence in the rendered brief that
# forward-references it — because a scope stated in two forms of words is two
# scopes. So every mutation that touches either text trips it, and FIVE
# pre-existing rows (D2, W2, Y2, Z3, V4) gained it in the same run. That is the
# guard's width, measured, not five rows going vague: named once here so the
# coupling stays legible instead of being spelled out five times.
NO_WRITE_SCOPE_GUARD = (
    "test_the_no_write_forward_reference_states_the_clauses_own_scope"
)

# --------------------------------------------------------------------------- #
# 🔴 THE ATTRIBUTION GATE (G-series).
#
# The gate is the skill's own stop condition made into a BRANCH, and it is the
# one guard in this script whose FALSE POSITIVE is worse than its false
# negative: the skill rejects a round cap outright, so a gate that stops a
# converging ladder is the expensive failure. Every row below therefore mutates
# the NARROWEST expression that can be wrong, and half of them mutate it in the
# FAIL-CLOSED direction — a gate that fires when it should not is exactly what
# a suite of "it fired correctly" tests cannot see.
# --------------------------------------------------------------------------- #

def gate_never_fires(t):
    """G1 — the verdict inverted at the one `return` that says it fired."""
    return _swap(
        t,
        "        return AttributionStop(\n            True, newer, older,",
        "        return AttributionStop(\n            False, newer, older,",
    )


def gate_fires_on_one_zero_round(t):
    """G2 — `and` -> `or`: ONE zero round ends the ladder. Two is the floor.

    🔴 RE-AIMED when the gate's unit stopped being the stated count alone. The
    comparison moved from `newer.payload` to `read_newer.value`, and a mutator
    whose target string no longer matches reports "the guard held" — so this
    row was measured as DID NOT APPLY before it was re-pointed, by the
    applicability sweep that runs before any test does.
    """
    return _swap(
        t,
        "    if read_newer.value == 0 and read_older.value == 0:",
        "    if read_newer.value == 0 or read_older.value == 0:",
    )


def the_emitted_block_loses_its_override_record(t):
    """G10 — the override's record ON THE PR, deleted from the EMITTER.

    🔴 MEASURED SURVIVING, which is why this row exists. The only test that
    claims this — `test_the_gate_override_is_refused_without_a_reason_and_
    records_one_given` — asked whether the reason appears "before the fence",
    and the BRIEF is printed to the same stream, carries the same reason and
    sits before every fence. So this deletion left the suite green in BOTH
    spellings that test has had: `split("```audit-claims")[0]` and
    `rsplit("```audit-claims", 1)[0]`. The brief stays in the auditor's
    terminal; the BLOCK is what gets pasted onto the PR, so the half that was
    unguarded is the half the record exists for.
    """
    return _swap(
        t,
        '    if facts.gate_override:\n'
        '        lines.append(\n'
        '            "  🔴 attribution gate OVERRIDDEN for this round — stated '
        'reason: "\n'
        '            f"{facts.gate_override}"\n'
        '        )\n',
        "",
    )


# --------------------------------------------------------------------------- #
# 🔴 THE UNIT THE GATE COUNTS IN (U-series).
#
# The gate shipped in #1765 and did exactly what it said — 0 of 99 ladders
# continued past two consecutive `payload=0` over 4.5 days — while its target
# pathology recurred at least nine times inside that same window, because the
# number it reads counts a COMMENT LINE inside a payload file. These rows break
# the measured reading in each of the directions that would restore that state,
# and in the two that would create the opposite failure: a manufactured zero
# ending a converging ladder.
# --------------------------------------------------------------------------- #
_MEASURED_ZERO_BRANCH = (
    '    if measured_zero:\n'
    '        posted = (\n'
    '            f"`payload={stated}` as posted, but " if stated is not None\n'
    '            else "no `payload=` field, and "\n'
    '        )\n'
)


def the_measured_zero_never_overrules_a_stated_count(t):
    """U1 — the reading is computed and then not acted on.

    The shipped defect itself, restored: every round's count comes from the
    header again, so two rounds that changed zero executable lines while
    stating 41 and 113 assemble a round 5 exactly as #242 and #1590 did.
    """
    return _swap(t, _MEASURED_ZERO_BRANCH,
                 '    if measured_zero and False:\n        posted = ""\n')


def the_measurement_overrules_in_BOTH_directions(t):
    """U2 — the asymmetry inverted: the measured count wins unconditionally.

    This is the plausible simplification, and it DISARMS the gate on the ladder
    it was built for: the measurement counts executable lines over the whole
    range, scaffolding included, so a round that states `payload=0` and rewrote
    1,051 test lines — `civitai/cli` #498's rounds 4-10 — would read non-zero.
    """
    return _swap(
        t,
        '    stated = block.payload\n'
        '    measured_zero = (\n',
        '    stated = block.payload\n'
        '    if churn is not None and churn.reason is None:\n'
        '        return PayloadReading(churn.executable, "measured", "m")\n'
        '    measured_zero = (\n',
    )


def the_measured_zero_only_fills_in_an_absent_field(t):
    """U3 — it may supply a count but never CORRECT one.

    The narrower reading somebody would write to feel safe, and it covers none
    of the nine recurrences: all of them STATED a non-zero count.
    """
    return _swap(t, '    if measured_zero:\n',
                 '    if measured_zero and stated is None:\n')


def the_reading_is_taken_over_the_DELTA_range(t):
    """U4 — the other anchor out of the same field.

    `range_anchor` vs `emit_anchor` is the pair devrc #958 got wrong in the
    other direction. Measuring `<to>..HEAD` asks what the NEXT round changed
    and files the answer under this one.
    """
    return _swap(
        t,
        '            runner, repo_dir, b.audited_from, b.audited_to, base\n',
        '            runner, repo_dir, b.audited_to, "HEAD", base\n',
    )


def a_measured_zero_needs_a_payload_field_to_exist(t):
    """U5 — the legacy corpus goes back out of reach.

    Every block posted before #1765 carries no `payload=` field, so requiring
    one here makes the whole measured reading inert for them.
    """
    return _swap(
        t,
        '    measured_zero = (\n'
        '        churn is not None and churn.reason is None and churn.executable == 0\n'
        '    )\n',
        '    measured_zero = (\n'
        '        churn is not None and churn.reason is None\n'
        '        and churn.executable == 0 and block.payload is not None\n'
        '    )\n',
    )


def a_prose_only_round_is_read_as_a_measured_zero(t):
    """U6 — the guard that keeps this out of gate 3's population, removed.

    `render_prose_determination` ships "on a whole-prose diff every round
    changes payload lines by construction, so the attribution gate cannot
    fire" to every operator. Score a prose round zero and that sentence is
    false, the hatch is double-handled, and a converging docs ladder stops at
    round 3 — the false-stop direction the skill says costs the most.
    """
    return _swap(
        t,
        '    if not files:\n'
        '        return ExecChurn(\n'
        '            None, 0, prose_lines, 0, 0, (),\n',
        '    if False:\n'
        '        return ExecChurn(\n'
        '            None, 0, prose_lines, 0, 0, (),\n',
    )


def an_unrecognised_file_type_is_guessed_to_be_hash_commented(t):
    """U7 — the classifier guesses a comment syntax it was not given.

    `#` is the most common line comment there is, so this is the guess someone
    would actually make — and it manufactures a zero out of a file nobody
    could classify.
    """
    return _swap(t, '    return "unknown"\n', '    return "code"\n')


def a_self_range_in_the_gates_own_pair_is_accepted(t):
    """U8 — the structural zero reaches the gate's arithmetic.

    Measured in the wild on `ZacxDev/naida-ai` #233 r5 and
    `civitai/gpu-fleet-infra` #331 r8, both `audited=X..X payload=0`.
    """
    return _swap(
        t,
        '    if round_no < 2:\n        return []\n',
        '    if round_no < 2 or True:\n        return []\n',
    )


def the_self_range_refusal_covers_the_WHOLE_corpus(t):
    """U13 — the scope widened to every block, which is a permanently-red gate.

    3 of 159 corpus rounds carry a self-range and both measured instances are
    the FINAL block of a closed ladder, so a run whose HISTORY contains one
    would be refused for a record nothing consults.
    """
    return _swap(
        t,
        '    pair = [b for b in (by_round.get(newest.round_no - 1), newest) if b]\n',
        '    pair = list(by_round.values())\n',
    )


def the_file_header_is_matched_before_the_hunk(t):
    """U9 — the ordering trap, and it is silent in both of its effects.

    A REMOVED line reading `-- foo` renders as `--- foo`, byte-identical to a
    file header. Checked first, that line is dropped AND the current path is
    re-pointed at the string after it.
    """
    return _swap(
        t,
        '        if in_hunk and line[:1] in ("+", "-"):\n',
        '        if line.startswith("--- ") and not line.startswith("--- a/x"):\n'
        '            old_path = _strip_diff_prefix(line[4:].strip())\n'
        '            continue\n'
        '        if in_hunk and line[:1] in ("+", "-"):\n',
    )


def the_changed_line_test_becomes_a_substring_membership(t):
    """U10 — `line[:1] in "+-"`, which is True for the EMPTY string.

    Every blank line of the diff then reads as a changed line with empty
    content — a blank, so non-executable — inflating the code-line count for
    whichever file happened to be current.
    """
    return _swap(
        t,
        '        if in_hunk and line[:1] in ("+", "-"):\n',
        '        if in_hunk and line[:1] in "+-":\n',
    )


def a_failed_git_is_read_as_an_empty_diff(t):
    """U11 — "a failed command is not a zero", removed.

    The rule `measure_ledger` keeps for the same commands, because an
    unwritable object store makes `--remerge-diff` UNDER-count at rc 0 while
    printing a plausible diff.
    """
    return _swap(
        t,
        '    if rc != 0:\n'
        '        return _unmeasured(\n'
        '            f"`git log -p --remerge-diff {frm}..{to} --not {base}` exited "\n',
        '    if False:\n'
        '        return _unmeasured(\n'
        '            f"`git log -p --remerge-diff {frm}..{to} --not {base}` exited "\n',
    )


def a_combined_merge_diff_is_counted_anyway(t):
    """U12 — arithmetic on the wrong columns, silently, at rc 0."""
    return _swap(
        t,
        '        if line.startswith("diff --cc ") or line.startswith("diff --combined"):\n',
        '        if False:\n',
    )


def an_asterisk_becomes_a_comment_prefix(t):
    """U14 — the one addition to the prefix table that is a DEFECT.

    A C block-comment continuation line starts with `*` and so does a pointer
    store: `*p = 5;`. Adding it reads executable C as a comment, which is the
    direction that manufactures a zero.
    """
    return _swap(t, '_COMMENT_SLASH = ("//",)\n', '_COMMENT_SLASH = ("//", "*")\n')


def the_empty_reason_refusal_returns_the_gates_verdict(t):
    """G11 — the OVERLOADED EXIT CODE, restored.

    🔴 THE NARROWEST EXPRESSION THAT CAN BE WRONG, deliberately: the return
    VALUE alone, with the guard's condition and its entire message untouched.
    A mutant that deleted the branch would prove nothing about the code —
    `claude/RULES.md`: a mutation that removes a guard together with its
    enclosing condition dies for the wrong reason.

    This is what `80379e83` shipped. The refusal fires in argument validation,
    before any claims block is read and before `attribution_stop` runs, so it
    answered `ATTRIBUTION_STOP_RC` — "the ladder has left the PR, post the
    remaining findings and stop" — for `--override-attribution-gate ""` at
    round 1 with no claims blocks in existence. Its stderr is unaffected by
    this mutation, which is exactly why a message-only assertion cannot see it.

    The anchor carries the comment that FOLLOWS the return: a bare
    `        return 4` has seven near-twins in this script, and `_swap` refuses
    an ambiguous target rather than editing whichever comes first.
    """
    return _swap(
        t,
        "        return 4\n\n    # A flag that silently does nothing",
        "        return ATTRIBUTION_STOP_RC"
        "\n\n    # A flag that silently does nothing",
    )


def absent_payload_field_reads_as_zero(t):
    """G3 — the FAIL-CLOSED mutation: an absent field becomes a measured 0.

    This is the one that would have made the gate permanently red on its first
    day — every block posted before it shipped carries no field at all.
    """
    return _swap(
        t,
        "        return None, present.group(1)\n    return None, None",
        "        return None, present.group(1)\n    return 0, None",
    )


def a_no_count_emit_writes_a_zero(t):
    """G4 — the EMITTER writes 0 instead of the placeholder when no count was
    stated. Fail-closed one station earlier: every `--emit-claims` run without
    `--payload` would put a zero nobody measured into the field the next round's
    gate consumes, so two such rounds would end a ladder on nothing.

    🔴 IT TARGETS THE EMITTER, AND THE PARSER SIDE CANNOT BE MUTATED IN
    ISOLATION AT ALL. MEASURED: `PAYLOAD_PLACEHOLDER = "<count>"` -> `"0"`, and
    equally `payload_from_header`'s placeholder branch returning `0, None`, make
    the emitted header's field parse back as a number — which
    `emitted_payload_reads_back_as_written` correctly refuses, at rc 4, on EVERY
    no-count emit. 22 tests then fail, so the row measures the blast radius of
    the round-trip refusal rather than the placeholder's meaning. That cascade
    is not a defect (it proves that guard's placeholder branch is REACHABLE from
    `main`, which no valid input can demonstrate); it just cannot be a row that
    isolates. This mutation keeps the placeholder constant intact, so the round
    trip passes and only the emitter's choice is under test.
    """
    return _swap(
        t,
        "    payload = PAYLOAD_PLACEHOLDER if facts.payload is None else facts.payload",
        "    payload = 0 if facts.payload is None else facts.payload",
    )


def override_recorded_when_the_gate_did_not_fire(t):
    """G5 — the record becomes a claim about a stop that never happened."""
    return _swap(
        t,
        "        gate_override=args.gate_override if stop.fires else None,",
        "        gate_override=args.gate_override,",
    )


def empty_override_reason_accepted(t):
    """G6 — `.strip()` dropped, so a whitespace-only reason records nothing."""
    return _swap(
        t,
        "    if args.gate_override is not None and not args.gate_override.strip():",
        "    if args.gate_override is not None and not args.gate_override:",
    )


def the_override_stops_overriding(t):
    """G7 — the escape hatch removed from the firing condition.

    The rejected round cap, re-entering through the one condition that makes
    this gate not one.
    """
    return _swap(
        t,
        "    if stop.fires and brief_refused is None and not args.gate_override:",
        "    if stop.fires and brief_refused is None:",
    )


def the_consecutive_requirement_becomes_the_two_newest(t):
    """G8 — two zero rounds with a GAP between them fire the gate.

    A missing intermediate block is an ordinary state this script already warns
    about (`gh pr view --json comments` returns ISSUE comments only), so this
    mutation stops ladders on an unknown round.
    """
    return _swap(
        t,
        "    older = by_round.get(newer.round_no - 1)",
        "    older = by_round[sorted(by_round)[-2]]",
    )


def a_negative_payload_count_is_accepted(t):
    """G9 — the input check widened past the value it exists to reject."""
    return _swap(
        t,
        "    if args.payload is not None and args.payload < 0:",
        "    if args.payload is not None and args.payload < -99:",
    )



# --------------------------------------------------------------------------- #
# 🔴 THE UNEARNED LEDGER (UL-series) — the WHOLE ladder's payload record.
#
# `ZacxDev/homelab-infra` #687 carried four rounds, every one of them recording
# `audited=X..X`, and nothing said so anywhere the operator or the next auditor
# would look. These rows mutate the reading and each of its three surfaces —
# stderr, the brief, and the block that gets pasted onto the PR — so "the report
# is guarded" can be re-derived rather than believed.
#
# 🔴 EACH TARGETS THE NARROWEST EXPRESSION THAT CAN BE WRONG. `claude/RULES.md`
# is explicit that removing a guard TOGETHER WITH ITS ENCLOSING CONDITION proves
# nothing about the guard: it dies for the wrong reason. So UL1 moves one
# predicate inside a comprehension, UL4 moves one comparison, and neither
# deletes the branch it lives in.


def the_denominator_counts_blocks_not_ranges(t):
    """UL1 — a bare `audited=<sha>` is folded into the range denominator.

    The flattering direction: a wholly unearned ladder that also posted one
    rangeless block then reports as PARTIAL, and `every_ranged_block` goes
    False on exactly the corpus the report exists for.
    """
    return _swap(
        t,
        "    ranged = [b for b in blocks if b.audited_from and b.audited_to]",
        "    ranged = list(blocks)",
    )


def the_brief_never_reports_an_unearned_ledger(t):
    """UL2 — the brief section is suppressed, which is the #687 state exactly.

    Not a deletion of the renderer: the function stays, is still called, and
    still returns a string. Only the section's CONTENT goes.
    """
    return _swap(
        t,
        '    if un is None or not un.self_ranges:\n        return ""\n',
        '    if True:\n        return ""\n',
    )


def stderr_never_reports_an_unearned_ledger(t):
    """UL3 — the operator's terminal loses the corpus summary.

    The per-block bystander sentence survives, so this isolates the WHOLE-LADDER
    reading from the pair-scoped one the previous round shipped.
    """
    return _swap(
        t,
        '    if unearned.self_ranges:\n        print("⚠ UNEARNED LEDGER — "',
        '    if False:\n        print("⚠ UNEARNED LEDGER — "',
    )


def a_partial_ladder_reads_as_wholly_unearned(t):
    """UL4 — `== len(ranged)` becomes `>= 1`.

    One comparison, inside the branch that already ran. A ladder with ONE broken
    round then reports as having no valid payload record at all — the direction
    that would make the loudest sentence in the report unreliable.
    """
    return _swap(
        t,
        "        every_ranged_block=bool(ranged) and len(selves) == len(ranged),",
        "        every_ranged_block=bool(ranged) and len(selves) >= 1,",
    )


def the_pasted_block_drops_the_unearned_note(t):
    """UL5 — the note is emitted for the brief and NOT for the PR.

    #687's whole failure is that the only artefact saying so was one terminal.
    """
    return _swap(
        t,
        "    if facts.unearned is not None and facts.unearned.self_ranges:\n"
        '        lines.append("  🔴 UNEARNED LEDGER — " + unearned_ledger_summary(',
        "    if False:\n"
        '        lines.append("  🔴 UNEARNED LEDGER — " + unearned_ledger_summary(',
    )


def the_unearned_heading_is_reworded(t):
    """UL6 — the shipped heading drifts from the literal the suite pins.

    The reachability control for that pin: a reworded heading must be caught by
    the two-way check and not only by the tests that grep for it, or the three
    regression rows would go red with the wrong diagnosis.
    """
    return _swap(
        t,
        '    "## 🔴 PART OF THIS LADDER\'S PAYLOAD RECORD WAS MEASURED OVER '
        'ZERO COMMITS"\n',
        '    "## 🔴 SOME ROUNDS OF THIS LADDER LOOK UNUSUAL"\n',
    )


# (label, expected killer set, mutation)
ROWS = [
    ("D1  delete clause read-only",
     LEDGER_TRIO | {DELETION_CONTROL}, drop_clause("read-only")),
    # 🔴 D2 is the one asymmetric row, and it is recorded rather than tidied
    # away: `test_missing_clause_check_warns_and_never_blocks` looks the
    # `no-fetch` clause up BY ID, so deleting that particular clause makes it
    # error. A real coupling, and the reason the deleted-clause CONTROL is a
    # separate test from the warn test.
    ("D2  delete clause no-fetch",
     LEDGER_TRIO | {"test_missing_clause_check_warns_and_never_blocks",
                    NO_WRITE_SCOPE_GUARD},
     drop_clause("no-fetch")),
    ("D3  delete clause stop-rule",
     LEDGER_TRIO | {DELETION_CONTROL}, drop_clause("stop-rule")),
    ("D4  delete clause nit-is-not-a-finding",
     LEDGER_TRIO | {DELETION_CONTROL}, drop_clause("nit-is-not-a-finding")),
    ("D5  delete clause reverify-self-reported",
     LEDGER_TRIO | {DELETION_CONTROL}, drop_clause("reverify-self-reported")),
    ("D6  delete clause finding-format",
     LEDGER_TRIO | {DELETION_CONTROL}, drop_clause("finding-format")),
    # 🔴 D7 is the SECOND asymmetric row, and the same shape as D2:
    # `test_the_out_file_is_read_back_and_checked` looks the `do-not-merge`
    # clause up BY ID to model a lossy write, so deleting that particular clause
    # makes it error. Recorded rather than tidied away — it is a real coupling,
    # and by-id lookup is still the right thing there (an INDEX would make the
    # test's outcome depend on which OTHER clause a mutation deleted, which
    # shows up in a sweep as dying for the wrong reason).
    ("D7  delete clause do-not-merge",
     LEDGER_TRIO | {DELETION_CONTROL,
                    "test_the_out_file_is_read_back_and_checked"},
     drop_clause("do-not-merge")),
    ("A1  add an unledgered eighth clause",
     {"test_the_invariant_clause_ledger_is_pinned_two_way",
      "test_the_rendered_section_holds_exactly_the_ledgered_clauses",
      DELETION_CONTROL},
     add_unledgered_clause),
    # 🔴 THE REACHABILITY CONTROL. The clause survives and is still emitted, so
    # every presence pin stays GREEN; only the FRAGMENT ledger can see it. A
    # WRONG-KILLER here means that ledger is dead code.
    ("R1  reword stop-rule (clause still present)",
     {"test_each_clause_carries_the_instruction_its_ledger_entry_names"},
     reword_stop_rule),
    # The `--check` round trip fires here too, and correctly: with the bullets
    # hardcoded, a REAL generated brief written to a REAL file genuinely holds
    # none of the clauses, which is precisely what that command reports. It is
    # the only row where the two reach the same conclusion by different routes.
    ("F1  render bullets from a hardcoded copy",
     {"test_the_rendered_section_holds_exactly_the_ledgered_clauses",
      "test_every_clause_is_emitted_verbatim_in_both_kinds_of_brief",
      "test_the_clause_check_runs_over_a_file_that_can_actually_be_lossy",
      DELETION_CONTROL},
     hardcode_bullets),
    ("C1  the delta refusal removed",
     {"test_the_delta_refusal_exits_non_zero_and_emits_no_brief",
      "test_the_refusal_names_what_it_looked_for_and_where",
      "test_the_refusal_fires_for_a_malformed_block_and_says_which_way",
      # Asserts on the refusal MESSAGE, so no refusal means no message.
      "test_the_refusal_says_which_comment_kinds_it_cannot_see",
      # 🔴 ROUND 10. The remedy guard opens by requiring rc 2 from REFUSAL 1;
      # with no refusal there is no rc 2 and no prescription to extract.
      "test_every_command_a_refusal_prescribes_actually_runs"},
     remove_the_refusal),
    ("C2  claims read from the whole comment",
     {"test_the_brief_carries_the_claims_and_not_the_reasoning_around_them",
      "test_the_newest_claims_block_wins",
      "test_emit_claims_prints_a_block_this_scripts_own_parser_accepts"},
     claims_from_the_whole_comment),
    ("C3  cross-repo decision inverted",
     {"test_cross_repo_tells_the_agent_to_worktree_the_prs_repo_itself",
      "test_same_repo_recommends_the_isolation_flag_and_does_not_hand_roll",
      "test_the_cross_repo_decision_comes_from_the_repos_not_from_prose",
      # A fork PR against THIS repo is a same-repo PR, so inverting the two
      # known states flips it to cross as well. Listed because it is the row
      # that PROVES the fork case rides the same decision as the others — the
      # fixture used to encode a model in which it did not.
      "test_a_fork_pr_against_this_repo_is_not_treated_as_cross_repo",
      # 🔴 ROUND 13 — ONE MORE, and the SAME coupling as the two below rather
      # than a new one: the recipe guard reads the `cross-repo` scenario's
      # WHERE TO WORK section, and the inversion sends that scenario down the
      # SAME-repo branch, which renders no recipe at all. It fires on the
      # guard's own "asserting over nothing" assertion, which is the answer
      # that assertion exists to give.
      "test_the_cross_repo_recipe_can_actually_be_run",
      # 🔴 TWO MORE IN ROUND 4, and both are real couplings rather than a row
      # gone vague. `own-worktree-is-writable` is rendered ONLY by the
      # cross-repo branch, so inverting the decision means the cross-repo
      # scenario brief no longer carries it; and the private-worktree scenario
      # is a SAME-repo PR, so the inversion sends it down the cross-repo branch
      # where the WHERE TO WORK section has no private-worktree note at all.
      "test_every_section_directive_is_emitted_verbatim_in_the_brief_that_owns_it",
      "test_the_where_to_work_section_does_not_call_a_private_worktree_the_clone",
      # 🔴 ROUND 14 ADDED FOUR, and they are the same coupling reaching one
      # section further. TOOLCHAIN now derives its COMMANDS from the
      # repository the PR lives in, and `toolchain_probe_root` asks
      # `repo_relation` to decide which checkout may be read — deliberately
      # REUSING the decision WHERE TO WORK already made rather than computing
      # a second one. So inverting that decision swaps the two toolchain
      # branches wholesale: the same-repo briefs stop prescribing anything and
      # the cross-repo brief starts prescribing the wrong repository's layout.
      # That the row's set grew here is the evidence that the reuse is real;
      # a second, independent derivation would have left C3 untouched and left
      # the two sections free to disagree.
      "test_the_toolchain_prescribes_only_commands_it_probed",
      "test_the_toolchain_prescribes_nothing_when_it_cannot_probe",
      "test_the_toolchain_section_names_the_tier_the_merge_gates_on",
      "test_the_toolchain_gates_the_auditors_copy_not_the_shared_checkout",
      # 🔴 ROUND 15 ADDED FIVE MORE, and they are the SAME coupling one layer
      # on: every one of round 15's new toolchain guards drives a probed
      # repository, and the inversion sends all five of them down the
      # not-probed branch, where the section prescribes nothing at all. That
      # the set grew again is the same evidence the round-14 note records —
      # one decision, reused, rather than a second derivation free to disagree.
      "test_a_capped_python_test_walk_is_not_reported_as_no_python_tests",
      "test_the_toolchain_head_claim_is_true_of_the_rendered_brief",
      "test_the_toolchain_probes_are_reachable_in_both_directions",
      "test_the_wrong_shell_diagnosis_covers_every_command_not_only_pytest",
      "test_an_unreadable_flake_is_not_reported_as_an_absent_one",
      # 🔴 ROUND 8 ADDED NINE MORE, and every one is the same coupling seen
      # from a new angle: round 8 made THE RANGE and THE LEDGER consult
      # `repo_relation`, so inverting that decision now moves both sections in
      # every scenario. The delta scenario becomes CROSS and takes the
      # hand-over branch (so its ledger prints no numbers and no cause at all);
      # the cross-repo scenarios become SAME and take the measuring branch. The
      # width is the fix — those two sections used to be blind to the repo
      # decision, which is the finding — and NOT a row gone vague.
      "test_a_cross_repo_ledger_hands_the_attribution_gate_to_the_auditor",
      "test_a_cross_repo_range_states_the_impossibility_not_a_moved_checkout",
      "test_the_ledger_refuses_to_measure_a_checkout_that_is_not_the_pr",
      "test_the_range_does_not_hand_out_a_head_that_is_not_the_prs_head",
      # 🔴 ROUND 10 — NINE OF THE NINETEEN NAMES HERE LEFT THIS SET, and their
      # departure is the FIX, measured. Round 8 made THE LEDGER's branch turn
      # on `repo_relation` ALONE, so inverting that decision sent the
      # same-repo `delta` scenario down the hand-over branch and took every
      # ledger assertion with it. The branch now asks
      # `cross_repo_holds_neither_end`, which consults the HEAD CHECK first —
      # and in `delta` that check passes, so the ledger measures whatever the
      # repo decision says. The nine, in full: the two `..._degenerate_...`
      # guards, the two cumulative ones (`..._a_failed_cumulative_...` and
      # `..._the_cumulative_figure_...`), `..._an_empty_range_...`,
      # `..._refuses_a_failed_command_...`,
      # `..._says_the_base_was_not_fetched`, `..._shows_the_files_...` and
      # `test_no_cross_repo_brief_claims_its_checkout_was_verified` — all of
      # which stopped depending on the repo decision, which is exactly what
      # round 10's finding B says they should never have. Three arrived, so
      # THIRTEEN distinct names remain (fifteen occurrences: the two directive
      # guards are listed twice, once for round 4's reason and once for round
      # 10's).
      #
      # 🔴 ROUND 12 CORRECTED THIS PARAGRAPH; IT WAS WRONG IN BOTH NUMBERS. It
      # read "SEVEN OF ROUND 8's THIRTEEN", and thirteen is what REMAINS at
      # this head, not what round 8 left. Re-derive by AST-diffing this row's
      # name set between the two trees — `ast.walk` over the second tuple
      # element, `set()` the string constants — never by counting the glob
      # tokens in the sentence above, which was how seven was arrived at while
      # two of them cover two guards each. Measured 706a6b38 -> 88b4105c: 19
      # distinct -> 13 distinct, 9 departed, 3 arrived.
      "test_a_cross_repo_ledger_prints_a_measurement_the_head_check_vouched_for",
      "test_a_placeholder_tip_is_never_handed_over_as_though_it_were_a_sha",
      "test_the_clone_grant_covers_only_the_write_the_recipe_makes",
      "test_every_section_directive_is_emitted_verbatim_in_the_brief_that_owns_it",
      "test_the_where_to_work_section_does_not_call_a_private_worktree_the_clone",
      # 🔴 ROUND 16 ADDED ONE, and it is the same coupling again rather than
      # a row gone vague: the absence-bar guard drives three PROBED trees,
      # and the inversion sends all three down the not-probed branch where
      # the section prescribes nothing and carries no wrong-shell bar.
      "test_the_python_absence_bar_does_not_point_at_a_bar_that_is_not_there"},
     invert_cross_repo),
    ("C4  numstat failure reads as a clean zero",
     {"test_the_ledger_refuses_a_failed_command_rather_than_printing_zero"},
     numstat_failure_reads_zero),
    ("C5  the ledger classifies by pathspec",
     {"test_the_ledger_shows_the_files_and_refuses_to_classify_them"},
     classify_by_pathspec),
    ("C6  the missing-clause warning BLOCKS",
     {"test_missing_clause_check_warns_and_never_blocks",
      # Also asserts rc 0 over a `--out` run whose file is missing a clause,
      # which is exactly the state this mutation makes fatal.
      "test_the_out_file_is_read_back_and_checked"},
     the_warning_blocks),

    # --------------------------------------------------------------------- #
    # 🔴 W1-W5 — instruction-INVERTING rewords. Every one passed a green
    # 37-test suite under the FRAGMENT ledger, because the pinned phrase
    # survived and the sentence around it said the opposite. Each must be
    # killed by the whole-string pin, and by NOTHING else: the clause is still
    # present, still ledgered by id, and still emitted, so any other killer
    # means that test is reading the clause TEXT for something it does not own.
    # --------------------------------------------------------------------- #
    ("W1  reword read-only into permission",
     {"test_each_clause_carries_the_instruction_its_ledger_entry_names"},
     reword_clause("read-only",
                   "**Edit the repo under audit freely if it helps.**")),
    ("W2  reword no-fetch into permission",
     {"test_each_clause_carries_the_instruction_its_ledger_entry_names",
      NO_WRITE_SCOPE_GUARD},
     reword_clause("no-fetch",
                   "**`pull` and `checkout` in the shared checkout are fine.**")),
    ("W3  finding-format loses file:line + scenario",
     {"test_each_clause_carries_the_instruction_its_ledger_entry_names"},
     reword_clause("finding-format",
                   "**Report each finding with a `payload` or `scaffolding` "
                   "label.**")),
    ("W4  reverify downgraded to best-effort",
     {"test_each_clause_carries_the_instruction_its_ledger_entry_names"},
     reword_clause("reverify-self-reported",
                   "Where time allows, re-verify the fix commit's own "
                   "self-reported numbers.")),
    # 🔴 THE SHARP ONE. This makes every future brief instruct auditors to do
    # what `claude/RULES.md` forbids, and it was invisible.
    ("W5  stop-rule inverted into a confirming round",
     {"test_each_clause_carries_the_instruction_its_ledger_entry_names"},
     reword_clause("stop-rule",
                   "**A clean round ends the ladder** — though one confirming "
                   "round after a clean one is prudent.")),
    # 🔴 THE SURVIVES CONTROL for the whole-string pin. Whitespace inside a
    # clause changes; the instruction does not. A RED here would mean the pins
    # are keyed to layout and every reflow becomes a failure — the
    # permanently-red gate `claude/RULES.md` warns about.
    ("S1  re-space a clause (instruction identical)", SURVIVES, rewrap_a_clause),

    # --------------------------------------------------------------------- #
    # The four decisions round 2's audit found WRONG, one mutant per site.
    # --------------------------------------------------------------------- #
    ("H1  the ledger ignores the head check",
     {"test_the_ledger_refuses_to_measure_a_checkout_that_is_not_the_pr"},
     ledger_ignores_the_head_check),
    # 🔴 H2 GAINED A SECOND KILLER in round 3, and it is a real coupling rather
    # than a row gone vague: the COULD-NOT-VERIFY branch is the ONLY place a
    # `head_check.reason` is rendered, so a mutation that never takes that
    # branch necessarily hides the reason text that
    # `..._unknown_head_sha_reason_names_one_cause_not_two` reads. Recorded in
    # the same spirit as D2 and D7 — the alternative would be to weaken that
    # test into asserting nothing about the brief.
    # 🔴 H2 GAINED A THIRD KILLER IN ROUND 7, and it is the tightest coupling
    # in the file rather than a row gone vague: this mutation makes THE RANGE
    # hand out the token `HEAD` unconditionally, and round 7's rule is that a
    # VERIFIED head renders as its SHA. The two claims are about the same
    # rendered token from opposite sides — "not `..HEAD` when the head is
    # unverified" and "not `..HEAD` when it IS verified" — so a mutation that
    # hardcodes the token necessarily trips both. Recorded, not narrowed.
    # 🔴 RELABELLED IN ROUND 8, because what this mutation MEANS changed under
    # it and the old label would have gone on describing something it no longer
    # does. The target (`elif hc is not None and hc.ok:` -> `elif True:`) is
    # unchanged and still applies — but round 8 moved the tip out of that
    # branch into `range_tip`, so the branch now selects only the NOTE. The
    # mutant therefore asserts "verified at assembly time" over an UNVERIFIED
    # checkout instead of handing out `..HEAD`, and
    # `..._range_names_a_sha_and_not_head_when_the_head_is_verified` correctly
    # stopped firing: the tip really is still a sha. Handing out the token is
    # now V6's job (`range_tip`) and V13's (the degenerate branch).
    ("H2  the range's verified NOTE ignores the head check",
     {"test_the_range_does_not_hand_out_a_head_that_is_not_the_prs_head",
      "test_the_unknown_head_sha_reason_names_one_cause_not_two",
      "test_a_cross_repo_range_states_the_impossibility_not_a_moved_checkout",
      "test_no_brief_claims_a_verification_its_own_fixture_refutes",
      # 🔴 ROUND 10. With the verified NOTE forced on, the placeholder-tip
      # branches never render — the unresolved-tip note lives in the two
      # branches this mutation makes unreachable — so no scenario spells the
      # placeholder and that guard's own positive control fires.
      "test_a_placeholder_tip_is_never_handed_over_as_though_it_were_a_sha"},
     range_ignores_the_head_check),
    ("H3  --emit-claims stamps the LOCAL head",
     {"test_emit_claims_stamps_the_prs_head_not_the_local_checkouts",
      "test_emit_claims_prints_a_block_this_scripts_own_parser_accepts"},
     emit_claims_uses_the_local_head),
    # 🔴 H4 BRIEFLY GAINED A KILLER IN ROUND 4 AND GAVE IT BACK, recorded
    # because the give-back is the evidence. The round-1 assumption test first
    # asserted `audited_to == <the PR head>`; that made mutant H3 (read the
    # LOCAL head) kill it for someone else's reason, so the assertion was
    # weakened to "the block is the BARE spelling". H4 then stopped killing it
    # — correctly: the placeholder header parses to `audited_from=""`,
    # `audited_to="<the"`, which IS bare-shaped, and the warning still fires
    # because it reads `facts.emit_from`, not the emitted text. The row that
    # owns the round-1 header's parseability is the one below.
    ("H4  round-1 placeholder back in the header",
     {"test_emit_claims_prints_a_block_this_scripts_own_parser_accepts"},
     emit_claims_interpolates_the_placeholder),
    # 🔴 ROUND 15 ADDED ONE KILLER TO EACH, and it is the same coupling in
    # both: `TOOLCHAIN_HEAD` promises the assembly checkout appears in RUNNABLE
    # commands only as the `nix develop` argument, and round 15 added a guard
    # that checks that promise against the RENDERED brief rather than against
    # the constant. Pointing the gate or the `nix build` at that path is
    # exactly the claim it falsifies, so it fires by construction.
    ("T1  gate.sh points at the shared checkout",
     {"test_the_toolchain_gates_the_auditors_copy_not_the_shared_checkout",
      "test_the_toolchain_head_claim_is_true_of_the_rendered_brief"},
     gate_points_at_the_shared_checkout),
    ("T2  nix build points at the shared checkout",
     {"test_the_toolchain_gates_the_auditors_copy_not_the_shared_checkout",
      "test_the_toolchain_section_names_the_tier_the_merge_gates_on",
      "test_the_toolchain_head_claim_is_true_of_the_rendered_brief"},
     nix_build_points_at_the_shared_checkout),
    # 🔴 ROUND 13 WIDENED BOTH KILLER SETS, and both additions are real
    # couplings. Round 13's `unknown-repo-gh-said-nothing` scenario is a `gh`
    # payload with NO `url` and `isCrossRepository` TRUE — precisely the fork
    # shape P1 mutates — so under P1 the head fields answer, the repo becomes
    # KNOWN, and the unknown branch that guard reads is never rendered. Under
    # P2 the same scenario collapses into same-repo for the same effect. Both
    # fire on the "COULD NOT DETERMINE is not in this section" assertion, i.e.
    # the guard reporting that it was pointed at the wrong section — which is
    # what that assertion is for. Listed in full rather than trimmed, the same
    # treatment C3, Y2, V4, V21 and V33 get.
    # 🔴 ROUND 15 — ONE MORE, and it is round 15's own partition pin: with the
    # fork shape resolving to a KNOWN repo, `unknown-repo-gh-said-nothing`
    # leaves the `repo-unknown` partition and its commands bar stops matching
    # `claims-file-assumed-base`'s. The within-target equality is what fires.
    ("P1  pr_slug reads the HEAD repo again",
     {"test_a_fork_pr_against_this_repo_is_not_treated_as_cross_repo",
      "test_the_prs_repo_is_read_from_the_url_not_from_the_head_repo",
      "test_no_brief_blames_gh_for_a_repo_gh_was_never_asked_about",
      "test_the_toolchain_reason_is_true_in_every_scenario",
      # 🔴 ROUND 16: the head-claim guard now asserts, in EVERY scenario, that
      # a fenced command names the assembly checkout IFF that scenario's
      # declared target is the probed one. Any mutation that moves a
      # scenario between the probed and not-probed branches therefore trips
      # it — which is the same one-decision-reused coupling C3 records, seen
      # from the guard that was widened to see it.
      "test_the_toolchain_head_claim_is_true_of_the_rendered_brief"},
     pr_slug_reads_the_head_repo),
    ("P2  'cannot determine' collapses to same-repo",
     {"test_an_undeterminable_repo_gets_its_own_branch_not_the_same_repo_one",
      "test_no_brief_blames_gh_for_a_repo_gh_was_never_asked_about",
      # round 16: the two `repo-unknown` scenarios start PROBING, so the
      # per-target fenced-command assertion in the head-claim guard fires.
      "test_the_toolchain_head_claim_is_true_of_the_rendered_brief"},
     unknown_repo_collapses_to_same),
    ("K1  --check always reports clean",
     {"test_the_clause_check_runs_over_a_file_that_can_actually_be_lossy"},
     the_check_command_reports_clean),
    ("K2  --out is not read back",
     {"test_the_out_file_is_read_back_and_checked"},
     the_out_file_is_not_read_back),
    ("K3  missing_clauses stops normalising whitespace",
     {"test_missing_clauses_is_a_pure_function_over_the_text"},
     missing_clauses_is_not_normalised),
    ("L1  the cumulative failure reason is dropped",
     {"test_a_failed_cumulative_measurement_does_not_print_a_false_cause"},
     the_cumulative_reason_is_dropped),
    ("B1  an unclosed fence is skipped silently",
     {"test_a_fence_the_parser_cannot_read_is_reported_not_skipped",
      "test_a_malformed_fence_beside_a_readable_block_still_warns"},
     an_unclosed_fence_is_skipped_silently),
    ("B2  a longer closing fence stops closing",
     {"test_a_longer_closing_fence_is_a_valid_close_and_is_read"},
     a_longer_closing_fence_stops_closing),
    ("B3  continuation lines are dropped",
     {"test_a_claim_that_wraps_onto_a_continuation_line_keeps_its_tail"},
     continuation_lines_are_dropped),
    ("B4  the nested-fence report is removed",
     {"test_a_block_cut_short_by_a_nested_fence_is_reported"},
     the_nested_fence_report_is_removed),
    ("B5  the refusal drops the comment-kinds note",
     {"test_the_refusal_says_which_comment_kinds_it_cannot_see"},
     the_refusal_drops_the_comment_kinds_note),

    # --------------------------------------------------------------------- #
    # 🔴 N1-N7 — round 3. The two anchors, the self-range guards, and the
    # empty-range reason that named causes `head_check` refutes.
    # --------------------------------------------------------------------- #
    # 🔴 N1 IS THE SHIPPED DEFECT RESTORED, and its killer set is deliberately
    # WIDE: reading `<to>` as the delta anchor moves the range for every
    # consumer at once, and a narrow expectation here would be the wrong
    # claim. It is listed in full rather than trimmed to the "main" one.
    ("N1  the range anchors on `<to>` again",
     {"test_the_range_is_generated_from_the_previous_rounds_audited_sha",
      "test_the_ledger_measures_from_the_tip_the_previous_round_audited",
      "test_the_newest_claims_block_wins",
      "test_the_range_says_head_was_verified_when_it_was",
      "test_a_degenerate_self_range_is_reported_not_rendered_as_a_clean_diff",
      "test_a_degenerate_self_range_is_named_by_the_ledger_too",
      "test_a_bare_round_one_audited_sha_still_anchors_the_next_round",
      # Round 4's four. All four read a range that N1 moves: two assert the
      # degenerate banner (which no longer fires when the anchor changes) and
      # two assert the anchor a `--audited` block produces.
      "test_a_degenerate_range_does_not_blame_a_checkout_it_verified",
      "test_an_uppercase_audited_sha_still_trips_the_degenerate_guard",
      "test_audited_supplies_the_tip_a_round_one_emit_cannot_derive",
      "test_audited_without_emit_claims_says_it_changed_nothing",
      # Round 8's two, both real: moving the anchor changes the range the
      # cross-repo hand-over COMMAND names (asserted whole, so a wrong anchor
      # fails it), and it stops the degenerate scenario being degenerate at
      # all — which is what that test's own positive control requires.
      "test_a_cross_repo_ledger_hands_the_attribution_gate_to_the_auditor",
      "test_the_degenerate_range_names_shas_at_both_ends",
      # 🔴 ROUND 10's one, and it is the same coupling: the renamed-remote
      # ledger's provenance line is asserted WHOLE, so a moved anchor fails it.
      "test_a_cross_repo_ledger_prints_a_measurement_the_head_check_vouched_for"},
     range_anchor_reads_the_audited_tip),
    # The OTHER wrong fix: `audited_from` alone. A round-1 bare `audited=<sha>`
    # then anchors nothing, and the remedy chain the delta refusal advertises
    # dead-ends.
    # 🔴 N2 GAINED A KILLER IN ROUND 4, and it is the one that shows the two
    # fixes are the SAME mechanism: the round-1 assumption warning asks
    # `range_anchor` what the next round will anchor on, so dropping the bare
    # fallback makes that anchor None and the warning silent again.
    # 🔴 N2 GAINED A FOURTH KILLER IN ROUND 19, and it is the same mechanism a
    # third time: the prose determination's COLLISION case — the anchor and the
    # round's own `<from>` being one sha — is reached from a BARE `round=1`
    # block, which is exactly what this fallback makes readable. Drop the
    # fallback and that block anchors nothing, so the collision never arises and
    # the guard over it goes red. Recorded rather than re-scoped: it is a true
    # report about a real coupling, and the alternative is a fixture that stops
    # exercising the state 3 of 159 corpus rounds are actually in.
    ("N2  the bare round-1 fallback is dropped",
     {"test_a_bare_round_one_audited_sha_still_anchors_the_next_round",
      "test_the_cumulative_figure_is_not_measured_without_a_round_one_anchor",
      "test_a_round_one_emit_claims_says_head_is_an_assumption_not_a_measurement",
      "test_the_prose_determination_names_the_boundary_it_can_resolve",
      # The fix round's F2 test is built on `CLAIMS_BLOCK_R1_BARE`, so it
      # depends on this fallback to have an anchor at all.
      "test_a_LONGER_OR_MIXED_CASE_anchor_is_still_the_structural_zero"},
     range_anchor_loses_the_bare_fallback),
    # 🔴 THE MIRROR IMAGE, and the reason the two readers are separate
    # functions: "one anchor everywhere" is wrong on the WRITER's side, and it
    # produces a superset rather than an empty range, so nothing else notices.
    # `--audited` routes through `emit_from`, so swapping the writer's anchor
    # to `prev_sha` breaks the flag as well — round 4's addition.
    # 🔴 KILLER SET GREW IN ROUND 5, for the same STRUCTURAL reason as Y7 and
    # recorded rather than engineered away: this mutation makes the round-1
    # branch fall through to the bare spelling, so a `--audited` value never
    # reaches the header at all — and a guard that reads the PRINTED header
    # correctly stays silent, because there is nothing wrong with what was
    # printed. No end-to-end test of "a bad `--audited` is refused" can survive
    # a mutant that stops the value being emitted. The alternative — asserting
    # the printed `<from>` EQUALS `emit_from` — was measured and rejected: it
    # made this row kill five tests and would answer a wrong-field bug in
    # production with a misleading "it does not parse".
    ("N3  --emit-claims writes the RANGE anchor as `<from>`",
     {"test_emit_claims_records_the_tip_this_round_audited_not_the_range_anchor",
      "test_emit_claims_prints_a_block_this_scripts_own_parser_accepts",
      "test_audited_supplies_the_tip_a_round_one_emit_cannot_derive",
      "test_emit_claims_refuses_an_audited_value_its_own_parser_cannot_read"},
     emit_claims_writes_the_range_anchor),
    ("N4  THE RANGE renders a self-range with no banner",
     {"test_a_degenerate_self_range_is_reported_not_rendered_as_a_clean_diff",
      # Round 4: both of these read THE RANGE's degenerate banner — one for
      # its cause list, one for an uppercase anchor.
      "test_a_degenerate_range_does_not_blame_a_checkout_it_verified",
      "test_an_uppercase_audited_sha_still_trips_the_degenerate_guard",
      # Round 8: that test's POSITIVE CONTROL requires the degenerate banner to
      # be present before it asserts anything about the range, so deleting the
      # banner kills it on the control rather than on the range spelling. That
      # is the control doing its job.
      "test_the_degenerate_range_names_shas_at_both_ends"},
     the_range_ignores_a_self_range),
    ("N5  the ledger calls a self-range merely empty",
     {"test_a_degenerate_self_range_is_named_by_the_ledger_too",
      "test_a_degenerate_range_does_not_blame_a_checkout_it_verified"},
     the_ledger_ignores_a_self_range),
    # 🔴 N6 now silences BOTH spellings of the warning — see the re-targeting
    # note on its mutation. Recorded rather than narrowed: one predicate
    # guards both, and pretending otherwise would mean asserting less.
    ("N6  --emit-claims writes `X..X` in silence",
     {"test_emit_claims_warns_when_the_block_it_writes_is_a_self_range",
      "test_a_round_one_emit_claims_says_head_is_an_assumption_not_a_measurement"},
     emit_claims_drops_the_self_range_warning),
    ("N7  the empty-range reason names the refuted causes",
     {"test_an_empty_range_does_not_name_a_cause_the_head_check_refutes"},
     the_empty_range_names_the_refuted_causes),
    ("N8  the unknown-head reason ignores what the caller knows",
     {"test_the_unknown_head_sha_reason_names_one_cause_not_two"},
     the_unknown_head_reason_names_both_causes),

    # --------------------------------------------------------------------- #
    # 🔴 X1-X3 — the three unpinned verbatim blocks. Each passed a fully green
    # 58-test suite before `SECTION_DIRECTIVES` existed.
    # --------------------------------------------------------------------- #
    # 🔴 X1 KEEPS the fragment `test_the_brief_carries_the_claims_...` pins
    # ("never WHY IT IS CORRECT") and inverts the sentence around it into the
    # framed audit the module's headline rule forbids. Any other killer would
    # mean some test is reading this text for something it does not own.
    ("X1  claims-framing inverted into 'take them as established'",
     {"test_each_section_directive_carries_the_instruction_its_ledger_entry_names"},
     reword_entry(
         "Directive", "claims-framing",
         "🔴 This is WHAT WAS CLAIMED, never WHY IT IS CORRECT. The fix round "
         "already verified each of these, so take them as established unless "
         "something obvious contradicts one.")),
    ("X2  the delta-regressions instruction deleted outright",
     {"test_the_section_directive_ledger_is_pinned_two_way",
      "test_each_section_directive_carries_the_instruction_its_ledger_entry_names",
      # Round 4: the scenario map and the render check both name it too.
      "test_the_directive_render_scenario_ledger_is_pinned_two_way",
      "test_every_section_directive_is_emitted_verbatim_in_the_brief_that_owns_it"},
     drop_entry("Directive", "delta-regressions")),
    # 🔴 X3 KEEPS "MOVES UNDER YOU" and "NOT a finding" — both pinned by
    # `test_the_shared_checkout_state_is_reported_with_the_it_moves_warning` —
    # and inverts the operative sentence into an instruction to WRITE to the
    # shared checkout, contradicting the `no-fetch` clause two bars later.
    # 🔴 KILLER SET GREW IN ROUND 5, and the growth is the news: this
    # replacement text also drops the no-write sentence round 5 added to every
    # checkout state, so the per-state rule ledger fires as well. Leaving the
    # old one-element set would have reported EXTRA-KILLER forever; narrowing
    # the mutation to avoid it would remove the second hazard from the row.
    ("X3  checkout-moves inverted into 'restore anything you see move'",
     {"test_each_section_directive_carries_the_instruction_its_ledger_entry_names",
      "test_every_checkout_state_carries_the_no_write_rule"},
     reword_entry(
         "Directive", "checkout-moves",
         "🔴 **This checkout is SHARED with other sessions and agents. It "
         "MOVES UNDER YOU** — the branch can change, files can appear and "
         "vanish, and commits can land mid-audit. That is NOT a finding in "
         "itself, but it is worth reporting. Restore anything you see move.")),
    # 🔴 RE-TARGETED KILLER SET in round 4, and the swap is the interesting
    # part. The "it reaches no brief" hazard used to be caught by the emitted
    # test, which iterated the SCRIPT's directives; that test is now driven by
    # the scenario map, so an unledgered directive is caught one step earlier —
    # by the scenario pin, which reads `SECTION_DIRECTIVES` and finds an id
    # nobody said where to render. The hazard is still covered; the guard that
    # covers it moved, and pretending otherwise would leave a DEAD pin here.
    ("XA  add an unledgered fourth directive",
     {"test_the_section_directive_ledger_is_pinned_two_way",
      "test_the_directive_render_scenario_ledger_is_pinned_two_way"},
     add_unledgered_directive),
    # 🔴 The SURVIVES control for the directive ledger — the sibling of S1. A
    # RED here means the new pin is keyed to layout, and every reflow of the
    # source becomes a test failure.
    ("XS  re-space a directive (instruction identical)",
     SURVIVES, rewrap_a_directive),

    # --------------------------------------------------------------------- #
    # 🔴 Y1-Y14 — round 4.
    # --------------------------------------------------------------------- #
    # 🔴 Y1 INVERTS THE SENTENCE THAT DEFINES THE DELTA SCOPE. It lived inside
    # a generated f-string, where no whole-string pin could reach it, and the
    # inversion "Re-audit the whole PR as well" passed every audit-related
    # test. Killed by the whole-string ledger ALONE — the directive is still
    # present, still ledgered by id and still emitted, so any other killer
    # would mean some test is reading this text for something it does not own.
    ("Y1  delta-scope inverted into 're-audit the whole PR'",
     {"test_each_section_directive_carries_the_instruction_its_ledger_entry_names"},
     reword_entry(
         "Directive", "delta-scope",
         "**Audit that range, and re-audit the whole PR as well.** A delta "
         "round is cheap and re-reading everything costs little.")),
    # 🔴 Y2 leaves a CROSS-REPO auditor with nowhere to work: the brief has
    # just told them to build that worktree themselves precisely so they can
    # fetch in it.
    # 🔴 ROUND 10 ADDED THE CLONE-GRANT GUARD to this row and to V4. Any reword
    # of this directive necessarily moves it: it asserts the two sentences that
    # scope the grant to `worktree add`, and neither replacement carries them.
    ("Y2  own-worktree-is-writable inverted into 'do not fetch'",
     {"test_each_section_directive_carries_the_instruction_its_ledger_entry_names",
      "test_the_clone_grant_covers_only_the_write_the_recipe_makes",
      NO_WRITE_SCOPE_GUARD},
     reword_entry(
         "Directive", "own-worktree-is-writable",
         "Do not fetch or check out inside it either. The no-write rule "
         "covers every checkout you touch.")),
    # 🔴 Y3-Y5 — DOCUMENTED HOLES, not controls. See `HOLE` above.
    ("Y3  OUTPUT drops the per-finding format (NO GUARD)",
     HOLE, the_output_contract_drops_the_per_finding_format),
    ("Y4  toolchain drops 'name the tier and base sha' (NO GUARD)",
     HOLE, the_toolchain_drops_name_the_tier),
    ("Y5  round-1 range points at the PR description (NO GUARD)",
     HOLE, the_round_one_range_points_at_the_pr_description),
    # 🔴 Y5b is the one measurement that makes the three-state worktree read
    # correct: git answers `/abs/.git` and `../.git` in a repo SUBDIRECTORY —
    # different strings, the same directory — so a string compare calls every
    # subdirectory of an ordinary clone a PRIVATE worktree.
    ("Y5b the two git dirs compared as strings",
     {"test_gather_worktree_kind_resolves_paths_before_comparing_them"},
     the_git_dirs_are_compared_as_strings),
    ("Y6  the UNKNOWN worktree state collapses to SHARED",
     {"test_an_unreadable_worktree_state_is_its_own_answer_and_keeps_the_no_write",
      # The `checkout-unknown` directive then reaches no brief at all.
      "test_every_section_directive_is_emitted_verbatim_in_the_brief_that_owns_it"},
     the_unknown_worktree_state_collapses_to_shared),
    # 🔴 KILLER SET GREW IN ROUND 5, and the growth is a real second
    # consequence rather than a row losing its focus: with `--audited` ignored,
    # `emit_from` is None at round 1, the round-trip refusal is never reached,
    # and a value this script's own parser cannot read is emitted in silence.
    # No test of "a bad `--audited` is refused" can survive the flag being
    # dropped on the floor, so the coupling is in the code, not in the pins.
    ("Y7  --audited is parsed and ignored",
     {"test_audited_supplies_the_tip_a_round_one_emit_cannot_derive",
      "test_emit_claims_refuses_an_audited_value_its_own_parser_cannot_read"},
     the_audited_flag_is_ignored),
    ("Y8  --audited without --emit-claims is silent again",
     {"test_audited_without_emit_claims_says_it_changed_nothing"},
     the_ignored_audited_flag_is_silent),
    # 🔴 Y9 RESTORES THE UNREACHABLE SPELLING: asked of `emit_from`, which is
    # None for the round-1 case the warning exists to cover.
    ("Y9  the round-1 emit warning goes back to `emit_from`",
     {"test_a_round_one_emit_claims_says_head_is_an_assumption_not_a_measurement"},
     the_round_one_emit_warning_is_unreachable_again),
    ("Y10 the LEDGER hand-rolls the refuted cause again",
     {"test_a_degenerate_range_does_not_blame_a_checkout_it_verified",
      "test_the_degenerate_range_causes_have_exactly_one_writer_per_consumer"},
     the_ledger_hand_rolls_its_degenerate_causes),
    ("Y11 THE RANGE hand-rolls the refuted cause again",
     {"test_a_degenerate_range_does_not_blame_a_checkout_it_verified",
      "test_the_degenerate_range_causes_have_exactly_one_writer_per_consumer"},
     the_range_hand_rolls_its_degenerate_causes),
    # 🟢 A SECOND WITNESS, AND IT IS WORTH SAYING WHY. The fix round's F2 test
    # exists for the LENGTH axis — an 8-char `audited=` against a 40-char
    # `<from>` — and it was written with a MIXED-CASE anchor as well, so that a
    # `==` which had merely been case-folded could not pass it. This row is the
    # proof that the second axis is really exercised: re-case-sensitising
    # `same_commit` kills it too, which a length-only fixture would not notice.
    ("Y12 same_commit goes back to case-sensitive",
     {"test_an_uppercase_audited_sha_still_trips_the_degenerate_guard",
      "test_a_LONGER_OR_MIXED_CASE_anchor_is_still_the_structural_zero"},
     same_commit_is_case_sensitive),
    ("Y13 WHERE TO WORK drops the private-worktree note",
     {"test_the_where_to_work_section_does_not_call_a_private_worktree_the_clone"},
     where_to_work_drops_the_private_note),
    ("Y14 a PRIVATE worktree is described as SHARED again",
     {"test_a_private_worktree_is_not_described_as_shared_and_is_not_absolved",
      "test_every_section_directive_is_emitted_verbatim_in_the_brief_that_owns_it"},
     a_private_worktree_is_called_shared),

    # --------------------------------------------------------------------- #
    # 🔴 Z1-Z8 — round 5.
    # --------------------------------------------------------------------- #
    # 🔴 Z1 IS THE INVERSION OF THE CORRECTED WRITE RULE, restored verbatim from
    # round 4. Three pins fire, each a different claim: the whole-string ledger
    # (the text moved), the permission probe (a grant appeared) and the
    # per-state no-write ledger (the rule vanished).
    #
    # 🔴 A FOURTH WAS PREDICTED AND DID NOT FIRE, recorded because the
    # prediction was wrong and the harness is what said so:
    # `..._private_worktree_is_not_described_as_shared_and_is_not_absolved`
    # stays GREEN under Z1, correctly. Round 4's text carries neither of its two
    # negatives, and both of its positives survive — "PRIVATE linked worktree"
    # comes from the `kind :` LINE, which this mutation does not touch, and
    # "report it with what moved and when" is in round 4's text verbatim. That
    # test covers the SHARED/PRIVATE description; the write GRANT is a claim it
    # never made, which is exactly why round 5 needed a new probe.
    ("Z1  the private state grants writing again",
     {"test_each_section_directive_carries_the_instruction_its_ledger_entry_names",
      "test_no_checkout_state_grants_write_permission_over_the_assembly_checkout",
      "test_every_checkout_state_carries_the_no_write_rule"},
     the_private_state_grants_writing_again),
    ("Z2  the SHARED state drops its own no-write rule",
     {"test_each_section_directive_carries_the_instruction_its_ledger_entry_names",
      "test_every_checkout_state_carries_the_no_write_rule"},
     the_shared_state_drops_the_no_write_rule),
    ("Z2b a fourth checkout state arrives with no rule",
     {"test_the_section_directive_ledger_is_pinned_two_way",
      "test_the_directive_render_scenario_ledger_is_pinned_two_way",
      "test_every_checkout_state_carries_the_no_write_rule"},
     a_fourth_checkout_state_arrives_with_no_rule),
    # 🔴 Z3 is the other half of round 4's regression: the clause that forbids
    # the write was made conditional on a state this script cannot know. Killed
    # by the whole-string CLAUSE ledger alone — the clause is still present,
    # still ledgered by id and still emitted, which is exactly how the reword
    # shipped.
    ("Z3  no-fetch goes back to conditional-on-SHARED",
     {"test_each_clause_carries_the_instruction_its_ledger_entry_names",
      NO_WRITE_SCOPE_GUARD},
     the_no_fetch_clause_is_conditional_again),
    # 🔴 ROUND 16 GAVE THIS ROW A SECOND KILLER, and that is the point of the
    # whole-string pin: any edit to `TOOLCHAIN_HEAD` — including one that
    # reads perfectly well — now has to be re-measured against all three
    # targets before it lands.
    ("Z4  TOOLCHAIN's reason names the SHARED CHECKOUT again",
     {"test_the_toolchain_reason_is_true_in_every_scenario", "test_the_toolchain_head_claim_is_true_of_the_rendered_brief"},
     the_toolchain_reason_names_the_shared_checkout_again),
    ("Z5  the emitted block loses its legend",
     {"test_the_emitted_skeleton_carries_a_legend_for_its_two_fields"},
     the_emitted_block_loses_its_legend),
    ("Z6  the emitted block is not round-tripped",
     {"test_emit_claims_refuses_an_audited_value_its_own_parser_cannot_read"},
     the_emitted_block_is_not_round_tripped),
    ("Z7  an EMPTY --audited is ignored again",
     {"test_emit_claims_refuses_an_audited_value_its_own_parser_cannot_read"},
     an_empty_audited_is_ignored_again),
    ("Z8  the degenerate causes stop scoping the omission",
     {"test_each_section_directive_carries_the_instruction_its_ledger_entry_names",
      "test_the_degenerate_cause_list_scopes_the_omitted_flag_to_round_one",
      "test_a_degenerate_range_does_not_blame_a_checkout_it_verified"},
     the_degenerate_causes_stop_scoping_the_omission),

    # --------------------------------------------------------------------- #
    # 🔴 V1-V7 — round 7. Base `3619fe68`.
    # --------------------------------------------------------------------- #
    # 🔴 V1 IS WIDE ON PURPOSE and the width is the finding's own shape: round
    # 6 demonstrated this disarm with the three test-module ledgers UPDATED,
    # which this battery cannot do. Script-only, the ledger pins catch the
    # rename — so the row proves the rename is not free, while V2 is what
    # proves the derivation change bought something.
    ("V1  checkout-private renamed and stripped of its rule",
     {"test_the_section_directive_ledger_is_pinned_two_way",
      "test_the_directive_render_scenario_ledger_is_pinned_two_way",
      "test_each_section_directive_carries_the_instruction_its_ledger_entry_names",
      "test_every_section_directive_is_emitted_verbatim_in_the_brief_that_owns_it",
      "test_every_checkout_state_carries_the_no_write_rule"},
     the_private_state_is_renamed_and_stripped_of_its_rule),
    # 🔴 V2 IS THE ISOLATING ROW. Nothing but the renderer's own selection can
    # see it: `SECTION_DIRECTIVES` is untouched and both ledgers stay green.
    ("V2  a fourth STATE selects a rule-less directive",
     {"test_every_checkout_state_carries_the_no_write_rule"},
     a_fourth_state_selects_an_existing_rule_less_directive),
    ("V3  the blind-spot note blames sha resolution again",
     {"test_the_blind_spot_rationale_matches_what_the_script_actually_does"},
     the_blind_spot_blames_sha_resolution_again),
    ("V4  own-worktree scopes the no-write rule to SHARED again",
     {"test_each_section_directive_carries_the_instruction_its_ledger_entry_names",
      "test_no_forward_reference_scopes_the_no_write_rule_to_a_denied_state",
      # 🔴 ROUND 10 — see Y2: any reword of this directive drops the two
      # sentences that scope the clone grant to `worktree add`.
      "test_the_clone_grant_covers_only_the_write_the_recipe_makes",
      NO_WRITE_SCOPE_GUARD},
     the_own_worktree_grant_scopes_the_rule_to_sharedness_again),
    ("V5  the --audited whitespace input check is removed",
     {"test_emit_claims_refuses_an_audited_value_whose_whitespace_leaves_the_line"},
     the_whitespace_input_check_is_removed),
    # 🔴 V6's killer set is WIDE and every member is a real consumer of the
    # rendered tip: six tests assert a `<anchor>..<sha>` spec, and reverting
    # the tip to `HEAD` moves all of them. Listed in full rather than trimmed
    # to the "main" one — the same treatment N1 gets, for the same reason.
    ("V6  the VERIFIED range hands out `..HEAD` again",
     {"test_the_range_names_a_sha_and_not_head_when_the_head_is_verified",
      "test_the_range_is_generated_from_the_previous_rounds_audited_sha",
      "test_the_range_says_head_was_verified_when_it_was",
      "test_the_newest_claims_block_wins",
      "test_a_bare_round_one_audited_sha_still_anchors_the_next_round",
      "test_audited_supplies_the_tip_a_round_one_emit_cannot_derive",
      "test_audited_without_emit_claims_says_it_changed_nothing",
      # 🔴 ROUND 8 — THE GROWTH IS THE CONSOLIDATION, not a pin doing new work.
      # `range_tip` is now the single writer for BOTH the verified tip and the
      # degenerate one (`anchor_is_head` implies the head check passed), so a
      # mutation of the predicate reaches both. That is the whole point of
      # having one predicate; V13 is the row that isolates the degenerate
      # branch's own override.
      "test_the_degenerate_range_names_shas_at_both_ends"},
     the_verified_range_hands_out_head_again),
    # 🔴 ROUND 14 WIDENED THIS KILLER SET, and the addition is a real
    # coupling rather than a row going vague. This mutant now returns EARLY in
    # the cross-repo branch, so the section it renders carries the commands
    # heading with no commands bar under it and no "NOTHING WAS PROBED HERE" —
    # which is exactly what round 14's cross-repo guard reads. Measured, not
    # predicted: the battery reported it as an EXTRA-KILLER first.
    # 🔴 ROUND 15 — a third killer, same coupling as the second: the mutant
    # replaces the whole cross-repo section body, so the not-probed branch
    # renders neither `NOTHING WAS PROBED HERE` nor its "name the runner by
    # path" hand-over, and round 15's contradiction guard reads exactly those.
    ("V7  TOOLCHAIN names the SHARED CHECKOUT, cross-repo only",
     {"test_the_toolchain_reason_is_true_in_every_scenario",
      "test_the_toolchain_prescribes_nothing_when_it_cannot_probe",
      "test_no_toolchain_note_claims_a_probe_the_brief_itself_denies"},
     the_toolchain_names_the_shared_checkout_cross_repo_only),

    # --------------------------------------------------------------------- #
    # 🔴 V8-V15 — round 8. Base `28492af2`.
    # --------------------------------------------------------------------- #
    # 🔴 V8 AND V10 ARE V3 IN THE SHAPES THE BATTERY NEVER EXERCISED. V3 plants
    # its false rationale on ONE line, and round 7's normaliser could see
    # exactly that: measured at `28492af2`, the same sentence wrapped across two
    # `#` lines scored 0, and so did both single-quoted spellings of an implicit
    # concatenation. A battery that only ever plants the shape the guard handles
    # certifies the guard against itself.
    ("V8  the blind-spot rationale WRAPS over two `#` lines",
     {"test_the_blind_spot_rationale_matches_what_the_script_actually_does"},
     the_blind_spot_rationale_wraps_across_two_comment_lines),
    # 🔴 V9 IS WIDE, AND THE WIDTH IS THE FINDING'S OWN SHAPE — the same
    # treatment V1 gets, for the same reason. What was wrong at `28492af2` was
    # the test module's FIXTURE, which this battery cannot mutate; reaching the
    # same rendered state from the script means disarming the fourth read rule
    # itself, and SIX consumers of that rule move with it. The row proves the
    # rendered state is not free, and it is the only script-side evidence the
    # seam guard executes at all. Every member listed rather than trimmed to
    # the "main" one, as N1 and V6 are.
    ("V9  the head check stops comparing the two shas",
     {"test_no_brief_claims_a_verification_its_own_fixture_refutes",
      "test_a_cross_repo_ledger_hands_the_attribution_gate_to_the_auditor",
      "test_a_cross_repo_range_states_the_impossibility_not_a_moved_checkout",
      "test_emit_claims_stamps_the_prs_head_not_the_local_checkouts",
      "test_the_ledger_refuses_to_measure_a_checkout_that_is_not_the_pr",
      "test_the_range_does_not_hand_out_a_head_that_is_not_the_prs_head",
      "test_the_range_names_a_sha_and_not_head_when_the_head_is_verified",
      # 🔴 ROUND 10's two, and both are the fourth read rule doing its job:
      # `cross_repo_holds_neither_end` consults the head check, so disarming it
      # sends the renamed-remote scenario down the hand-over branch; and
      # `range_tip` falls back to the placeholder in scenarios that had a
      # verified sha, so the tip guard's own claim about which scenarios spell
      # it stops holding.
      "test_a_cross_repo_ledger_prints_a_measurement_the_head_check_vouched_for",
      "test_a_placeholder_tip_is_never_handed_over_as_though_it_were_a_sha"},
     the_head_check_stops_comparing_the_two_shas),
    ("V10 the blind-spot rationale hides in a '…' '…' concat",
     {"test_the_blind_spot_rationale_matches_what_the_script_actually_does"},
     the_blind_spot_rationale_hides_in_single_quoted_concat),
    # 🔴 ROUND 10 GAVE V11 AND V12 ONE KILLER EACH, and in both cases it is the
    # new guard's OTHER-SIDE positive control: round 10's fixes made two
    # branches conditional, so each guard also asserts that the branch still
    # renders its TRUE case. Disable the branch outright and that control
    # fires — which is the control doing exactly what it is for.
    ("V11 the cross-repo RANGE blames a moved checkout again",
     {"test_a_cross_repo_range_states_the_impossibility_not_a_moved_checkout",
      "test_a_placeholder_tip_is_never_handed_over_as_though_it_were_a_sha"},
     the_cross_repo_range_diagnoses_a_moved_checkout_again),
    ("V12 the cross-repo LEDGER reports a failed command again",
     {"test_a_cross_repo_ledger_hands_the_attribution_gate_to_the_auditor",
      "test_a_cross_repo_ledger_prints_a_measurement_the_head_check_vouched_for"},
     the_cross_repo_ledger_reports_a_failed_command_again),
    ("V13 the DEGENERATE range hands out `..HEAD` again",
     {"test_the_degenerate_range_names_shas_at_both_ends"},
     the_degenerate_range_hands_out_head_again),
    # V14 also moves the WHOLE-STRING clause ledger, necessarily: round 8's fix
    # was a reword of that clause, so restoring the old text is a reword the
    # ledger pins. Both are listed; the scope guard is the one that fires for
    # THIS row's reason, and W2/Z3 above show the ledger firing without it.
    ("V14 `no-fetch` narrows to the copy made FOR THIS AUDIT again",
     {"test_the_no_write_forward_reference_states_the_clauses_own_scope",
      "test_each_clause_carries_the_instruction_its_ledger_entry_names"},
     the_no_fetch_clause_narrows_to_this_audit_again),
    # 🔴 ROUND 10 WIDENED THIS ROW BY ONE, and the width is the fix: the
    # renamed-remote scenario now REACHES the measuring branch, so it reads the
    # same provenance line and asserts it whole.
    ("V15 THE LEDGER's provenance line hands out `..HEAD` again",
     {"test_the_ledgers_provenance_line_names_the_sha_it_resolved_not_head",
      "test_a_cross_repo_ledger_prints_a_measurement_the_head_check_vouched_for"},
     the_ledger_provenance_line_hands_out_head_again),
    ("V16 the skipped-round warning is removed",
     {"test_a_claims_block_more_than_one_round_behind_is_warned_about"},
     the_skipped_round_warning_is_removed),
    # 🔴 ROUND 10 WIDENED THIS ROW BY TWO. The remedy guard requires rc 2 from
    # REFUSAL 1b before it can read a prescription out of it, and its negative
    # control is built from the same refusal's text — with the refusal gone
    # there is nothing to extract on either side. Both are listed rather than
    # trimmed: a control that stops being able to fail is exactly the thing the
    # EXTRA-KILLER report exists to surface.
    ("V17 the no-anchor refusal is removed",
     {"test_a_block_that_parses_but_yields_no_anchor_is_refused",
      "test_every_command_a_refusal_prescribes_actually_runs",
      "test_control_the_prescription_extractor_can_see_a_broken_remedy"},
     the_no_anchor_refusal_is_removed),

    # --------------------------------------------------------------------- #
    # 🔴 V18-V23 — round 10. Base `706a6b38`.
    # --------------------------------------------------------------------- #
    # 🔴 TWO killers, and both are the predicate's real consumers: the brief
    # side (the placeholder is handed over with no warning) and the ledger pin,
    # whose second assertion drives `unresolved_tip_note` directly and gets ""
    # back. Listed in full.
    ("V18 the tip is always claimed to be a sha",
     {"test_a_placeholder_tip_is_never_handed_over_as_though_it_were_a_sha",
      "test_the_tip_placeholder_ledger_matches_the_script"},
     the_tip_is_always_claimed_to_be_a_sha),
    ("V19 the cross-repo LEDGER asks the relation before the head check",
     {"test_a_cross_repo_ledger_prints_a_measurement_the_head_check_vouched_for"},
     the_ledger_asks_the_repo_relation_before_the_head_check),
    ("V20 REFUSAL 1b blocks the remedy it prescribes",
     {"test_every_command_a_refusal_prescribes_actually_runs"},
     the_anchorless_refusal_blocks_its_own_remedy),
    # 🔴 V21's killer set is TWO, and the width is the coupling itself: the
    # constant exists so the script's spelling and the guards' literal cannot
    # drift. Break it and the ledger pin fires (its own assertion) AND the
    # placeholder guard's positive control fires — no scenario renders the
    # string it scans for any more. Listed in full rather than trimmed, the
    # same treatment N1, V6 and V9 get.
    ("V21 `range_tip`'s placeholder stops naming TIP_PLACEHOLDER",
     {"test_the_tip_placeholder_ledger_matches_the_script",
      "test_a_placeholder_tip_is_never_handed_over_as_though_it_were_a_sha"},
     the_two_placeholder_spellings_drift_apart),
    # V22 also moves the WHOLE-STRING directive ledger, necessarily: round 10's
    # fix was a reword, so restoring the old text is a reword the ledger pins.
    # Both are listed; the clone-grant guard is the one that fires for THIS
    # row's reason. Same treatment as V14.
    ("V22 the clone grant permits fetching there again",
     {"test_the_clone_grant_covers_only_the_write_the_recipe_makes",
      "test_each_section_directive_carries_the_instruction_its_ledger_entry_names"},
     the_clone_grant_permits_fetching_again),
    # 🟢 A SECOND WITNESS, and it confirms a REACHABILITY claim rather than a
    # behaviour. The fix round's F9 test reaches the blockless boundary only
    # because this refusal falls through to `--emit-claims` instead of
    # returning outright — its docstring says so. Under this mutant nothing is
    # emitted, so the test goes red: the fall-through really is what makes that
    # state reachable, which is the half a reachability claim usually asserts
    # without evidence.
    ("V23 REFUSAL 1 blocks the remedy it prescribes",
     {"test_every_command_a_refusal_prescribes_actually_runs",
      "test_a_run_with_NO_BLOCK_does_not_blame_a_block_that_is_not_there"},
     the_no_block_refusal_blocks_its_own_remedy),
    ("V24 an ASSUMED base branch is recorded as read",
     {"test_no_brief_states_an_assumed_base_branch_as_a_fact"},
     the_assumed_base_is_recorded_as_read),
    # 🔴 ROUND 12. V24 mutates the PREDICATE; V25 mutates the INPUT it reads,
    # and only the second reaches `--claims-file` mode. At `88b4105c` the mode
    # hardcoded `baseRefName: "main"`, so V24's predicate answered correctly
    # over a fixture that lied — the whole point of finding 1.
    ("V25 `--claims-file` hardcodes the base ref again",
     {"test_no_brief_states_an_assumed_base_branch_as_a_fact"},
     the_claims_file_hardcodes_the_base_ref),
    ("V26 THE LEDGER's stale-base caveat deleted",
     {"test_the_ledger_says_the_base_was_not_fetched"},
     the_stale_base_caveat_is_deleted),
    # 🔴 THE REACHABILITY CONTROL for V26's guard, and the row that proves the
    # whole-string pin is worth its cosmetic cost: the caveat is still present
    # and still spells both fragments the guard used to look for.
    ("V27 the stale-base caveat reworded into an assurance",
     {"test_the_ledger_says_the_base_was_not_fetched"},
     the_stale_base_caveat_says_it_is_fine),
    # Same shape as V22: restoring a REWORDED directive necessarily moves the
    # whole-string directive ledger too. The enumeration probe is the one that
    # fires for THIS row's reason.
    ("V28 the clone grant reverts to a closed verb list",
     {"test_the_clone_grant_covers_only_the_write_the_recipe_makes",
      "test_each_section_directive_carries_the_instruction_its_ledger_entry_names"},
     the_clone_grant_reverts_to_the_enumeration),
    ("V29 a THIRD prescription site nothing drives",
     {"test_every_command_a_refusal_prescribes_actually_runs"},
     a_third_prescription_site_nothing_drives),
    # 🔴 ROUND 13. V30 and V31 are the SAME split as V24/V25 one field over:
    # the PREDICATE that picks a cause, and the INPUT it picks from. Only the
    # second reaches a tree whose three-way branch is already correct, which is
    # the state a fix that stopped at `render_worktree_directive` would leave.
    ("V30 the unknown-repo cause reverts to two branches for three causes",
     {"test_no_brief_blames_gh_for_a_repo_gh_was_never_asked_about"},
     the_unknown_repo_cause_goes_back_to_two_branches),
    ("V31 `--claims-file` hands over the `gh` cause it cannot have",
     {"test_no_brief_blames_gh_for_a_repo_gh_was_never_asked_about"},
     the_claims_file_names_the_gh_cause),
    ("V32 the repo lookup drops `--repo` again",
     {"test_no_gh_pr_view_this_script_prescribes_omits_repo"},
     the_repo_lookup_drops_repo_again),
    # 🔴 V33's killer set is TWO, and the width IS the coupling. Reverting the
    # recipe makes it unrunnable (its own guard) AND leaves the grant naming
    # `fetch` and `update-ref` that the recipe no longer runs — which is the
    # grant/recipe relationship, firing from the other side. Listed in full
    # rather than trimmed, the same treatment V21 and V22 get.
    ("V33 the cross-repo recipe names the PR's head BRANCH again",
     {"test_the_cross_repo_recipe_can_actually_be_run",
      "test_the_clone_grant_covers_only_the_write_the_recipe_makes"},
     the_recipe_goes_back_to_naming_the_head_branch),
    # 🔴 V34 IS THE ROW THAT MEASURED FINDING F4. Run against `6349a8b9` it
    # SURVIVED a fully green 109-test suite: the old probe matched the literal
    # `do not …, … or … there` and this re-spelling walks straight past it,
    # while every presence pin stays green. Same shape as V28 — restoring a
    # reworded directive necessarily moves the whole-string ledger too — but
    # the row's own reason is the verb-SET check, which is what replaced the
    # spelled one.
    ("V34 the clone grant re-spells the closed verb list",
     {"test_the_clone_grant_covers_only_the_write_the_recipe_makes",
      "test_each_section_directive_carries_the_instruction_its_ledger_entry_names"},
     the_clone_grant_respells_the_enumeration),
    # 🔴 V35 IS V29 IN A DIFFERENT SPELLING, and that is the entire point: it
    # SURVIVED at `6349a8b9` because the scanner matched `print` by NAME.
    ("V35 a third prescription site written through `err_stream.write`",
     {"test_every_command_a_refusal_prescribes_actually_runs"},
     a_third_prescription_site_written_through_err_stream),

    # --------------------------------------------------------------------- #
    # 🔴 V36-V41 — round 14. Base `9e23c379` — `origin/main` with #1104 in.
    # --------------------------------------------------------------------- #
    ("V36 gate.sh prescribed without probing for it",
     {"test_the_toolchain_prescribes_only_commands_it_probed"},
     the_gate_is_prescribed_without_probing_for_it),
    # Two killers, both measured: one asserts the gate command is PRESENT
    # (`gate.sh --tier both`), the other asserts the auditor's own worktree is
    # what it names (`<your worktree>/scripts/gate.sh`). Suppressing the whole
    # command removes both strings, so both fire — the second for a reason
    # adjacent to what it owns, which is why it is recorded here rather than
    # left to look like an accident.
    # 🔴 ROUND 15 — two more, both from fixtures that did not exist before.
    # Suppressing every `gate.sh` removes the ONLY command from two new
    # single-purpose repos (the no-`--tier` gate and the nodejs-flake gate), and
    # each guard fires on its own "the command under test is absent" assertion.
    ("V37 gate.sh never prescribed, even where it exists",
     {"test_the_toolchain_section_names_the_tier_the_merge_gates_on",
      "test_the_toolchain_gates_the_auditors_copy_not_the_shared_checkout",
      "test_the_toolchain_probes_are_reachable_in_both_directions",
      "test_the_wrong_shell_diagnosis_covers_every_command_not_only_pytest"},
     the_gate_is_never_prescribed_even_where_it_exists),
    ("V38 the checks probe reads TEXT rather than CODE",
     {"test_the_flake_checks_probe_reads_an_output_and_not_the_word",
      "test_the_toolchain_prescribes_only_commands_it_probed",
      "test_the_flake_checks_probe_reads_nix_code_and_not_text_that_looks_like_it",
      "test_the_toolchain_section_names_the_tier_the_merge_gates_on",
      # round 16: unstripped text also fabricates from the nested fixtures,
      # and it is what the clean/dirty pair reads.
      "test_a_nested_indented_string_inside_an_interpolation_is_not_a_terminator",
      "test_the_probe_hedges_when_the_nix_scan_did_not_end_cleanly"},
     the_checks_probe_matches_the_word_not_the_output),
    # 🔴 ROUND 15 — the `_fmt_uses` fallbacks now have fixtures (a repo with no
    # `.envrc` at all, and one declaring no `use` line), and hardcoding the
    # sentence removes both of their answers.
    ("V39 the .envrc sentence is hardcoded again",
     {"test_the_toolchain_prescribes_only_commands_it_probed",
      "test_the_toolchain_probes_are_reachable_in_both_directions"},
     the_envrc_sentence_is_hardcoded_again),
    ("V40 cross-repo, the WRONG repository is probed anyway",
     {"test_the_toolchain_prescribes_nothing_when_it_cannot_probe",
      "test_the_toolchain_reason_is_true_in_every_scenario",
      "test_no_toolchain_note_claims_a_probe_the_brief_itself_denies",
      # round 16: a not-probed target that prescribes fenced commands is
      # exactly what the per-target assertion refuses.
      "test_the_toolchain_head_claim_is_true_of_the_rendered_brief"},
     the_probe_reads_the_cwd_repo_cross_repo_too),
    ("V41 the check-name scan stops tracking nix strings",
     {"test_the_flake_checks_probe_reads_an_output_and_not_the_word",
      "test_the_toolchain_section_names_the_tier_the_merge_gates_on",
      "test_the_flake_checks_probe_reads_nix_code_and_not_text_that_looks_like_it",
      "test_the_toolchain_prescribes_only_commands_it_probed",
      # round 16: a build script scanned as code is what the nested fixtures
      # are made of, and an indented string that never opens never closes
      # either, so the clean/dirty pair sees it too.
      "test_a_nested_indented_string_inside_an_interpolation_is_not_a_terminator",
      "test_the_probe_hedges_when_the_nix_scan_did_not_end_cleanly"},
     the_checks_scan_stops_tracking_nix_strings),

    # --------------------------------------------------------------------- #
    # 🔴 V42-V51 — round 15. Base `5bad0a0c` — THIS BRANCH's own head. Six of
    # these are the both-directions rule applied where round 14 stated it and
    # did not apply it: `ci_suite`, `gate_tier` and `py_tests` each had NO
    # mutant in either direction, and all three inversions were measured
    # SURVIVING a fully green 115-test suite while shipping a fabricated
    # command. The cause was fixture reach, not a missing assertion.
    # --------------------------------------------------------------------- #
    ("V52 a state-dependent sentence in the COMMANDS bar",
     {"test_the_toolchain_reason_is_true_in_every_scenario"},
     a_state_dependent_sentence_moves_into_the_commands_bar),
    ("V42 a checks shape it cannot enumerate is DENIED, not hedged",
     {"test_the_flake_checks_probe_reads_nix_code_and_not_text_that_looks_like_it"},
     the_checks_probe_denies_a_shape_it_cannot_enumerate),
    ("V43 `''${…}` and `'''` close an indented string again",
     {"test_the_flake_checks_probe_reads_nix_code_and_not_text_that_looks_like_it",
      # round 16: the `an ''${…} escape` nested body reaches it too.
      "test_a_nested_indented_string_inside_an_interpolation_is_not_a_terminator"},
     the_indented_string_escapes_are_terminators_again),
    ("V44 the walk cap answers 'no python tests' in silence",
     {"test_a_capped_python_test_walk_is_not_reported_as_no_python_tests",
      # round 16 drives all three py_tests states, so a cap that answers NONE
      # is visible from the absence bar too.
      "test_the_python_absence_bar_does_not_point_at_a_bar_that_is_not_there"},
     the_walk_cap_answers_no_python_tests),
    ("V45 run-ci-suite.sh prescribed without probing for it",
     {"test_the_toolchain_probes_are_reachable_in_both_directions"},
     the_ci_suite_is_prescribed_without_probing_for_it),
    ("V46 run-ci-suite.sh never prescribed, even where it exists",
     {"test_the_toolchain_prescribes_only_commands_it_probed"},
     the_ci_suite_is_never_prescribed_even_where_it_exists),
    ("V47 `--tier both` appended to a gate that never showed it takes one",
     {"test_the_toolchain_probes_are_reachable_in_both_directions"},
     the_gate_tier_flag_is_assumed),
    ("V48 `--tier both` dropped from a gate that does take it",
     {"test_the_toolchain_section_names_the_tier_the_merge_gates_on"},
     the_gate_tier_flag_is_never_prescribed),
    ("V49 a pytest command for a repo with no python tests",
     {"test_a_capped_python_test_walk_is_not_reported_as_no_python_tests",
      "test_the_toolchain_probes_are_reachable_in_both_directions",
      "test_the_python_absence_bar_does_not_point_at_a_bar_that_is_not_there"},
     the_python_tests_are_assumed_present),
    ("V50 the wrong-shell bar is emitted only beside a pytest command",
     {"test_the_wrong_shell_diagnosis_covers_every_command_not_only_pytest",
      "test_the_toolchain_section_names_the_tier_the_merge_gates_on",
      "test_the_toolchain_prescribes_only_commands_it_probed",
      "test_the_toolchain_probes_are_reachable_in_both_directions",
      "test_the_python_absence_bar_does_not_point_at_a_bar_that_is_not_there"},
     the_wrong_shell_note_goes_back_inside_the_pytest_branch),
    ("V51 the wrong-shell bar stops covering every language",
     {"test_the_wrong_shell_diagnosis_covers_every_command_not_only_pytest",
      "test_the_python_absence_bar_does_not_point_at_a_bar_that_is_not_there"},
     the_wrong_shell_note_is_pytest_only_again),

    # --------------------------------------------------------------------- #
    # 🔴 V53-V61 — round 16. Base `ba321c06`. Three of round 15's fixes left
    # their CLASS open one shape further out, so every row here is paired with
    # the row that reaches the OTHER direction of the same defect: V53/V54 for
    # the nested-`''` lex, V57/V58 for the not-probed blind spot (the second is
    # the positive control that the first's survival was the GUARD's fault and
    # not the harness's), V60/V61 for the two dangling references.
    # --------------------------------------------------------------------- #
    ("V53 a nested `''` inside `${…}` terminates the outer string again",
     {"test_a_nested_indented_string_inside_an_interpolation_is_not_a_terminator"},
     a_nested_interpolation_is_string_text_again),
    ("V54 `foo'' = 1;` opens a string instead of naming a binding",
     {"test_a_nested_indented_string_inside_an_interpolation_is_not_a_terminator",
      # a name that opens a string also leaves the scan UNTERMINATED, so the
      # clean/dirty pair sees it — a second, independent detector.
      "test_the_probe_hedges_when_the_nix_scan_did_not_end_cleanly"},
     an_identifier_ending_in_apostrophes_opens_a_string),
    ("V55 the lexer's uncertainty valve is welded shut",
     {"test_the_probe_hedges_when_the_nix_scan_did_not_end_cleanly"},
     the_lexer_never_admits_it_got_lost),
    ("V56 `#` line comments are scanned as code",
     {"test_the_comment_stripper_is_reachable_in_its_own_right",
      # the `a `#` character` nested body carries a comment too.
      "test_a_nested_indented_string_inside_an_interpolation_is_not_a_terminator"},
     the_scan_stops_stripping_line_comments),
    ("V57 a sentence keyed on the UNKNOWN checkout state, not probed",
     {"test_the_toolchain_reason_is_true_in_every_scenario"},
     _not_probed_sentence_keyed_on("unknown")),
    ("V58 the same sentence keyed on PRIVATE — the positive control",
     {"test_the_toolchain_reason_is_true_in_every_scenario"},
     _not_probed_sentence_keyed_on("private")),
    ("V59 TOOLCHAIN_HEAD reclaims prose it does not always have",
     {"test_the_toolchain_head_claim_is_true_of_the_rendered_brief"},
     the_head_reclaims_prose_it_does_not_always_have),
    ("V60 the pytest wrong-shell bar with no pytest command above it",
     {"test_the_python_absence_bar_does_not_point_at_a_bar_that_is_not_there"},
     the_pytest_diagnosis_is_emitted_without_a_pytest_command),
    ("V61 the absence bar points at a bar that is never above it",
     {"test_the_python_absence_bar_does_not_point_at_a_bar_that_is_not_there"},
     the_absence_bar_points_at_a_bar_that_is_never_above_it),
    ("V62 the cached-build fallback loses its empty-$DRV guard",
     {"test_the_cached_build_fallback_is_emitted_with_its_guards"},
     the_cached_build_fallback_loses_its_empty_drv_guard),

    # --------------------------------------------------------------------- #
    # 🔴 V63-V67 — round 18. Base `10d437c9`. Round 17 shipped the recovery
    # block WITH a guard test, and the round-2 delta audit found the test's
    # pins narrower than their own docstring at three separate points. Every
    # row here was measured SURVIVING a green 127-test suite BEFORE the fix —
    # which is the point: round 17 added no mutant for any of them, and V62
    # (delete the guard wholesale) is the only thing that reached this block at
    # all, so nothing could have caught a pin that reads as coverage while
    # providing none.
    # --------------------------------------------------------------------- #
    ("V63 the $DRV guard's stop demoted to a trailing comment",
     {"test_the_cached_build_fallback_is_emitted_with_its_guards"},
     the_drv_guards_stop_becomes_a_trailing_comment),
    ("V64 the verdict grep deleted, the block ending on `echo done`",
     {"test_the_cached_build_fallback_is_emitted_with_its_guards"},
     the_verdict_grep_is_deleted_from_the_last_line),
    ("V65 `true` appended after the verdict grep",
     {"test_the_cached_build_fallback_is_emitted_with_its_guards"},
     a_true_is_appended_after_the_verdict_grep),
    ("V66 a FIXED $ERR path while $LOG keeps its mktemp",
     {"test_the_cached_build_fallback_is_emitted_with_its_guards"},
     the_stderr_path_is_fixed_while_the_log_path_is_not),
    ("V67 the stop-note prescribes wrapping ALONE again",
     {"test_the_stop_note_prescribes_something_that_actually_stops"},
     the_stop_note_prescribes_wrapping_alone_again),

    # --------------------------------------------------------------------- #
    # 🔴 V68-V69 — round 19, from the blind audit OF ROUND 18. Both are round
    # 18's own hazards re-spelled, and both survived round 18's fix at 128
    # passed. The lesson is one line long: round 18 pinned the SYMPTOM SHAPE
    # (a suffix, a `;`) where the hazard is a CLASS (code vs comment, a
    # command vs a line). That is the same per-symptom-not-per-class error
    # round 18 filed against round 17, reproduced inside the fix for it —
    # seven of eight rounds now.
    # --------------------------------------------------------------------- #
    ("V68 the $DRV guard's stop hidden in a comment ENDING in the suffix",
     {"test_the_cached_build_fallback_is_emitted_with_its_guards"},
     the_drv_guards_stop_hides_in_a_comment_that_ends_in_the_suffix),
    ("V69 `true` reached by `||` after the verdict grep",
     {"test_the_cached_build_fallback_is_emitted_with_its_guards"},
     a_true_is_reached_by_or_after_the_verdict_grep),
    # --------------------------------------------------------------------- #
    # --------------------------------------------------------------------- #
    # 🔴 THE PROSE LADDER'S LINE-AUTHORSHIP DETERMINATION — see the Q-series
    # block beside the mutate functions. Q1 is the withdrawn `#1678` rule
    # re-entering through the renderer instead of through the prose; Q9 is the
    # defect that withdrew this PR's OWN first draft, re-entering as a fraction.
    #
    # 🔴 THE SEAM ROWS ARE NOT HERE, AND THAT IS NOT AN OVERSIGHT. Widening the
    # scope SENTENCES this script ships is killed by a test in
    # `test_audit_ladder_stop_rule.py`, and `failing()` above runs `TEST_REL`
    # ALONE — so scored here they would read SURVIVED and be recorded as a
    # coverage gap that does not exist. They live in `mutants-audit-ladder.sh`,
    # whose suite IS that module and which already copies this script into its
    # tree. A row must be scored by a harness that can see its killer.
    # --------------------------------------------------------------------- #
    # Q1 kills two, and the second is worth knowing rather than hiding: with the
    # floor at round 1 the determination — which carries its own fenced command
    # block — prints beside the round-1 `audit-claims` skeleton, and the
    # round-trip guard that reads the emitted block back through this script's
    # own parser then has two fences to choose from. So the withdrawn `#1678`
    # rule does not merely permit a wrong stop, it corrupts the artefact the
    # NEXT round anchors on. Recorded, not re-scoped.
    ("Q1  the determination's round floor moved to ROUND 1",
     {"test_the_prose_determination_ships_on_emit_claims_from_round_2_and_NOT_before",
      "test_emit_claims_prints_a_block_this_scripts_own_parser_accepts"},
     det_floor_to_round_one),
    # Q2 kills five: with the section gone every guard over it fails, four of
    # them on the explicit "carries no determination section at all" assertion
    # that replaced a bare slice index. Recorded rather than re-scoped — a
    # deletion that takes its dependants with it is a true report, and
    # pretending the set is smaller would be the row lying about what it saw.
    ("Q2  the determination dropped from --emit-claims",
     {"test_the_prose_determination_ships_on_emit_claims_from_round_2_and_NOT_before",
      "test_the_prose_determination_names_the_boundary_it_can_resolve",
      "test_the_determination_ships_its_RESTRAINING_half_too",
      "test_the_determination_mandates_the_whitespace_and_move_blame_flags",
      # The fix round's three, all of which slice the output on the section
      # heading. Recorded rather than re-scoped, for the reason this row's
      # comment already gives: a deletion that takes its dependants with it is
      # a TRUE report, and pretending the set is smaller would be the row
      # lying about what it saw.
      "test_a_LONGER_OR_MIXED_CASE_anchor_is_still_the_structural_zero",
      "test_a_run_with_NO_BLOCK_does_not_blame_a_block_that_is_not_there",
      "test_the_NOT_MEASURED_states_the_section_SHIPS_are_the_set_the_code_RETURNS",
      # Round 21's two new guards, recorded the same way and for the same
      # reason. Both slice the output on the section heading, so a wholesale
      # deletion takes them with it. Round 22 OBSERVED them firing and wrote
      # them down rather than re-scoping the tests to look away: this row's
      # claim is "here is everything that went red", and a set trimmed to the
      # ones that feel on-topic is the row lying about what it saw.
      "test_the_F1_gap_concession_is_as_wide_as_the_gap",
      # Round 24's guard, recorded the same way. It asserts the section is
      # BYTE-IDENTICAL across three readings of the round's diff — the claim
      # that the measured payload unit has not reached gate 3 — and it slices
      # on the same heading, so a wholesale deletion takes it too.
      "test_the_prose_determination_is_UNCHANGED_by_the_measured_reading",
      "test_the_section_says_HOW_to_settle_the_WHOLE_PROSE_condition"},
     det_section_dropped),
    # ⚠ Q3 AND Q13 ARE NOT DUPLICATES, and the overlap is the point. Q3
    # rewrites the collision BRANCH so a structural zero prints as an ordinary
    # boundary; Q13 leaves the branch alone and breaks the PREDICATE that
    # reaches it. Either defect produces the same wrong sentence, so the
    # LONGER/MIXED-CASE test fires on both — one of them through a spelling
    # difference the base could not even see.
    ("Q3  the anchor/<from> COLLISION printed as an ordinary boundary",
     {"test_the_prose_determination_names_the_boundary_it_can_resolve",
      "test_a_LONGER_OR_MIXED_CASE_anchor_is_still_the_structural_zero"},
     det_collision_reads_as_an_ordinary_boundary),
    # Q4's second killer is round 21's own doing, and is RECORDED rather than
    # re-scoped. `PROSE_DETERMINATION_POPULATION` is the literal this row
    # replaces, and round 21 moved the F1 gap concession INTO it — so dropping
    # the population statement now also drops the concession, and the guard
    # over the concession fails. That is a true report about what this mutation
    # deletes, not over-coverage to be hidden by narrowing either test.
    # 🔴 ROUND 24 ADDED THE THIRD KILLER, MEASURED. The new guard pins that all
    # five shipped determination constants are still in the section, so a
    # dropped conjunct fails it — which is the guard doing exactly what it
    # says, not a loss of isolation: every killer here is about the section.
    ("Q4  the prose-ONLY condition dropped from the section",
     {"test_the_prose_determination_ships_on_emit_claims_from_round_2_and_NOT_before",
      "test_the_F1_gap_concession_is_as_wide_as_the_gap",
      "test_the_prose_determination_is_UNCHANGED_by_the_measured_reading"},
     det_prose_only_condition_dropped),
    # Q5/Q6 are round-0 finding F2 as mutants. The graft at `4552b745` shows
    # `..._ships_its_RESTRAINING_half_too` raising on an ABSENT surface there
    # rather than failing on the defect, so it is filed as a guard and these
    # rows are its evidence instead.
    ("Q5  the four restraints dropped from the shipped section",
     {"test_the_determination_ships_its_RESTRAINING_half_too",
      "test_the_prose_determination_is_UNCHANGED_by_the_measured_reading"},
     det_restraints_dropped),
    ("Q6  the restraints ship but nothing says they are CONJUNCTS",
     {"test_the_determination_ships_its_RESTRAINING_half_too"},
     det_conjunct_label_dropped),
    # Q7/Q8 are finding F4. `-w`/`-M` are not a refinement: over 159 corpus
    # rounds they move the share on 34, on `#1688` r3 from 0.253 to 0.889, and
    # flip the stop verdict on 3.
    ("Q7  git blame loses -w and -M",
     {"test_the_determination_mandates_the_whitespace_and_move_blame_flags"},
     det_blame_loses_w_and_M),
    # 🔴 Q8 IS THE COUNTING COMMAND ALONE, AND Q22 IS THE REFLOW ONE. They were
    # one row's worth of claimed coverage until round 22. Q17's fix added `-U0`
    # to the reflow command, which gave the section a SECOND line containing
    # `git diff -U0 -w -M`; the F4 guard was a bare membership test on that
    # substring, so Q8 SURVIVED a green 212-test suite — the reflow line kept
    # satisfying it while the counting command shipped with neither flag. The
    # guard now reads each command's own line, and it takes BOTH rows to say so.
    ("Q8  the COUNTING git diff loses -w and -M",
     {"test_the_determination_mandates_the_whitespace_and_move_blame_flags"},
     det_diff_loses_w_and_M),
    ("Q22 the REFLOW git diff loses -w and -M, the counting one keeps them",
     {"test_the_determination_mandates_the_whitespace_and_move_blame_flags"},
     det_reflow_loses_w_and_M),
    # 🔴 Q9/Q10 ARE THE THRESHOLD ITSELF, IN BOTH DIRECTIONS, and they are the
    # only rows here that reach the arithmetic rather than the prose. Q10 is
    # the shape that withdrew the findings-keyed draft: a threshold that
    # FORBIDS `#1111`, the case this whole section rests on.
    ("Q9  threshold widened to one half",
     {"test_the_founding_case_and_its_neighbours_are_a_REGRESSION_fixture"},
     det_threshold_widened_to_a_half),
    ("Q10 threshold narrowed to three quarters — FORBIDS #1111",
     {"test_the_founding_case_and_its_neighbours_are_a_REGRESSION_fixture"},
     det_threshold_narrowed_to_three_quarters),
    # Q12 is the half-delivery shape a second time, one paragraph down from the
    # one F2 named — and the guard that catches it is deliberately NOT bound to
    # the seam constant, because the constant survives this edit intact.
    ("Q12 the (a) and (c) NOT-MEASURED states dropped",
     {"test_the_prose_determination_names_the_boundary_it_can_resolve",
      # The fix round's ledger sees it too, from the other side: the paragraph
      # is now BUILT from `PROSE_NOT_MEASURED_STATES`, so bypassing the ledger
      # is exactly the edit the two-way set comparison exists to catch.
      "test_the_NOT_MEASURED_states_the_section_SHIPS_are_the_set_the_code_RETURNS"},
     det_not_measured_states_a_and_c_dropped),
    ("Q11 a structural zero returned as a measured miss",
     {"test_the_founding_case_and_its_neighbours_are_a_REGRESSION_fixture",
      "test_the_determinations_reason_never_reads_as_a_measurement_when_it_is_not",
      "test_the_NOT_MEASURED_states_the_section_SHIPS_are_the_set_the_code_RETURNS"},
     det_structural_zero_reads_as_a_measurement),
    # ------------------------------------------------------------------- #
    # 🔴 Q13-Q16 — the FIX ROUND against `#1691` as merged. Q13 and Q14 are the
    # two defects that also carry regression tests (`RED_AT_BASE_R20`); Q15 and
    # Q16 are the evidence for the NOT-MEASURED-set ledger, which grafts onto
    # `b9a53101` as an ABSENCE red and is therefore filed as a guard.
    # ------------------------------------------------------------------- #
    ("Q13 the anchor/<from> predicate open-coded as `!=` again",
     {"test_a_LONGER_OR_MIXED_CASE_anchor_is_still_the_structural_zero"},
     det_collision_predicate_back_to_equality),
    ("Q14 the blockless boundary blames a block that is not there",
     {"test_a_run_with_NO_BLOCK_does_not_blame_a_block_that_is_not_there"},
     det_no_block_boundary_blames_a_block),
    ("Q15 a returnable NOT-MEASURED state deleted from the shipped ledger",
     {"test_the_NOT_MEASURED_states_the_section_SHIPS_are_the_set_the_code_RETURNS",
      "test_the_founding_case_and_its_neighbours_are_a_REGRESSION_fixture"},
     det_reflow_state_dropped_from_the_ledger),
    ("Q16 a REWRAPPED round scores a number instead of NOT MEASURED",
     {"test_the_NOT_MEASURED_states_the_section_SHIPS_are_the_set_the_code_RETURNS",
      "test_the_founding_case_and_its_neighbours_are_a_REGRESSION_fixture"},
     det_reflow_branch_dropped),
    # ------------------------------------------------------------------- #
    # 🔴 Q17-Q18 — the FIX ROUND against `#1712` round 1. Both are the state
    # at `6bf15a8f`, i.e. RED_AT_BASE rows rather than guards: the reflow
    # command really did ship without `-U0`, and state (e)'s sentence really
    # was wider than its detector. Q17's killer runs the SHIPPED command
    # against a two-paragraph git fixture; round 20's one-paragraph fixture
    # could not distinguish the two context widths and scored it green.
    # ------------------------------------------------------------------- #
    ("Q17 the reflow command loses `-U0`, so it inspects hunks the rule "
     "never counted and state (e) cannot fire",
     {"test_the_reflow_command_FIRES_on_a_rewrap_beside_an_edit"},
     det_reflow_command_loses_U0),
    ("Q18 state (e)'s sentence goes back to being WIDER than its detector, "
     "re-demoting the written-down count to a backstop",
     {"test_a_rewrap_that_ALSO_edits_is_NOT_state_e_and_the_section_SAYS_so"},
     det_reflow_narrowing_dropped),
    ("Q19 the gap concession drops CONSECUTIVE, so the attribution gate reads "
     "as reachable from any two zero-payload rounds",
     {"test_the_F1_gap_concession_is_as_wide_as_the_gap"},
     det_gate_concession_drops_CONSECUTIVE),
    ("Q20 the uncovered set shrinks back to \"every round touches payload\", "
     "so an ALTERNATING ladder reads as covered",
     {"test_the_F1_gap_concession_is_as_wide_as_the_gap"},
     det_uncovered_set_narrowed_back),
    ("Q21 the section states the whole-prose condition and no way to settle "
     "it, so an enumerable conjunct reads as an unanswerable classification",
     {"test_the_section_says_HOW_to_settle_the_WHOLE_PROSE_condition"},
     det_one_command_check_dropped),

    # ------------------------------------------------------------------- #
    # 🔴 G1-G10 — THE ATTRIBUTION GATE. Killer sets MEASURED by this harness,
    # not predicted: the rows below were run and the reported sets copied in.
    # ------------------------------------------------------------------- #
    # 🔴 G1 GAINED A FOURTH KILLER, MEASURED, not predicted: the exit-code split
    # added a test that asserts the GATE still answers 5 on a firing corpus, so
    # a gate that never fires now fails it too. The row still isolates what it
    # names — every one of the four is about the gate firing.
    # 🔴 AND SIX MORE IN ROUND 24, MEASURED — the set below is what the harness
    # REPORTED, and it is one NARROWER than this row was first written with.
    # The six are the new tests that assert the gate FIRES: four do so directly,
    # and two (`…arm_the_gate`, `…EXECUTABLE_line_still_reads_non_zero`) via a
    # POSITIVE CONTROL, without which their does-not-fire assertions would be
    # indistinguishable from a reading wired to nothing.
    # 🔴 `…UNRECOGNISED_file_type…` IS DELIBERATELY NOT HERE, and it was
    # predicted to be: its assertions are rc 0 and a brief — which a gate that
    # never fires satisfies — and it carries no control of that shape. The row
    # came back WRONG-KILLER and the PREDICTION was corrected, never the set.
    ("G1  the gate never fires — the verdict inverted",
     {"test_the_attribution_gate_refuses_a_round_after_two_zero_payload_rounds",
      "test_the_gate_needs_two_CONSECUTIVE_zero_rounds_and_nothing_less",
      "test_the_gate_override_is_refused_without_a_reason_and_records_one_given",
      "test_the_gates_verdict_and_an_input_refusal_are_DIFFERENT_numbers",
      "test_two_rounds_that_changed_only_COMMENTS_arm_the_gate",
      "test_a_round_that_changed_an_EXECUTABLE_line_still_reads_non_zero",
      "test_a_prose_only_round_is_UNMEASURED_so_gate_3_keeps_its_population",
      "test_a_measured_zero_arms_the_gate_for_a_block_with_NO_payload_field",
      "test_a_measured_NON_zero_never_overrules_a_STATED_zero",
      "test_a_SELF_RANGE_in_a_block_the_gate_reads_is_an_INPUT_refusal"},
     gate_never_fires),
    # 🔴 THE UNEARNED-LEDGER ROWS. See the UL-series banner above the mutation
    # functions for why each targets the narrowest expression rather than the
    # branch it lives in.
    ("UL1 the range denominator counts rangeless blocks",
     {"test_the_unearned_reading_counts_RANGES_and_not_blocks"},
     the_denominator_counts_blocks_not_ranges),
    ("UL2 the BRIEF never reports an unearned ledger",
     {"test_a_ladder_with_SOME_self_ranges_reports_them_IN_THE_BRIEF"},
     the_brief_never_reports_an_unearned_ledger),
    ("UL3 stderr never reports the whole-ladder reading",
     {"test_a_ladder_whose_EVERY_block_is_a_self_range_says_so_on_stderr", "test_the_unearned_ledger_strings_are_the_scripts_own"},
     stderr_never_reports_an_unearned_ledger),
    ("UL4 a PARTIAL ladder reads as wholly unearned",
     {"test_a_ladder_with_SOME_self_ranges_reports_them_IN_THE_BRIEF"},
     a_partial_ladder_reads_as_wholly_unearned),
    ("UL5 the PASTED block drops the unearned note",
     {"test_the_block_the_operator_PASTES_carries_the_unearned_ledger_note"},
     the_pasted_block_drops_the_unearned_note),
    ("UL6 the shipped heading drifts from the pinned literal",
     {"test_a_ladder_with_SOME_self_ranges_reports_them_IN_THE_BRIEF", "test_the_unearned_ledger_strings_are_the_scripts_own"},
     the_unearned_heading_is_reworded),
    ("G2  ONE zero round ends the ladder (`and` -> `or`)",
     {"test_the_gate_needs_two_CONSECUTIVE_zero_rounds_and_nothing_less"},
     gate_fires_on_one_zero_round),
    # 🔴 TWO MORE IN ROUND 24, MEASURED — and they are a genuine WIDENING of
    # this row's coverage rather than noise. With an absent field read as a
    # stated 0, the legacy corpus stops being attributed to the MEASUREMENT
    # (the refusal says `payload=0` where it should say the field is absent
    # and the diff earned the zero), and a legacy pair over an UNCLASSIFIABLE
    # diff fires the gate on two unknowns.
    # 🔴 A THIRD WIDENING IN ROUND 25, AND IT IS A REAL ONE. The
    # unearned-ledger report has a distinct row for a LEGACY block — "with no
    # `payload=` field", never "beside `payload=None`" — and this mutation
    # makes the absent field parse as 0, so that row renders a count the
    # comment never carried. A *report* now detects the fail-closed read, not
    # only the gate: that is coverage this row did not have.
    ("G3  an ABSENT payload field reads as a measured zero",
     {"test_the_gate_FAILS_OPEN_on_a_block_that_carries_no_payload_field",
      "test_the_payload_field_the_emitter_writes_is_the_one_its_parser_reads",
      "test_a_measured_zero_arms_the_gate_for_a_block_with_NO_payload_field",
      "test_an_UNRECOGNISED_file_type_is_UNMEASURED_and_fails_OPEN",
      "test_a_ladder_with_SOME_self_ranges_reports_them_IN_THE_BRIEF"},
     absent_payload_field_reads_as_zero),
    ("G4  a no-count emit writes a ZERO instead of the placeholder",
     {"test_the_payload_field_the_emitter_writes_is_the_one_its_parser_reads"},
     a_no_count_emit_writes_a_zero),
    ("G5  an override is RECORDED for a gate that never fired",
     {"test_the_gate_override_is_refused_without_a_reason_and_records_one_given"},
     override_recorded_when_the_gate_did_not_fire),
    ("G6  a whitespace-only override reason is accepted",
     {"test_the_gate_override_is_refused_without_a_reason_and_records_one_given"},
     empty_override_reason_accepted),
    ("G7  the override stops overriding (the rejected cap, re-entering)",
     {"test_the_gate_override_is_refused_without_a_reason_and_records_one_given"},
     the_override_stops_overriding),
    ("G8  CONSECUTIVE becomes the two NEWEST blocks, gap and all",
     {"test_the_gate_needs_two_CONSECUTIVE_zero_rounds_and_nothing_less"},
     the_consecutive_requirement_becomes_the_two_newest),
    ("G9  a NEGATIVE payload count reaches the header",
     {"test_a_negative_payload_count_is_refused_at_the_input"},
     a_negative_payload_count_is_accepted),
    # 🔴 G10 WAS ADDED BECAUSE IT SURVIVED. The row is the one that was missing
    # when the override's PR-side record went unguarded — see the mutator's
    # docstring for the measurement, and the assertion it now holds in
    # `test_the_gate_override_is_refused_without_a_reason_and_records_one_given`.
    ("G10 the emitted block loses its override record",
     {"test_the_gate_override_is_refused_without_a_reason_and_records_one_given"},
     the_emitted_block_loses_its_override_record),
    # 🔴 G11 IS THE SHIPPED DEFECT ITSELF, not a hypothetical. The killer set
    # below was MEASURED by this harness, not predicted.
    ("G11 the empty-reason INPUT refusal returns the gate's verdict again",
     {"test_an_empty_override_reason_is_refused_as_INPUT_not_as_the_gates_verdict",
      "test_the_gates_verdict_and_an_input_refusal_are_DIFFERENT_numbers",
      "test_the_gate_override_is_refused_without_a_reason_and_records_one_given"},
     the_empty_reason_refusal_returns_the_gates_verdict),
    # 🔴 THE U-SERIES KILLER SETS ARE MEASURED BY THIS HARNESS, NOT PREDICTED.
    # Several of them are deliberately WIDE: breaking the measured reading in
    # one direction moves every test that drives it, and a narrow set written
    # from a guess reports WRONG-KILLER on a correct tree.
    # 🔴 THE FOUR WIDE SETS BELOW ARE WHAT THE HARNESS REPORTED, not what was
    # predicted — every one of these rows was first written with a narrow set
    # and came back EXTRA-KILLER. The extra killers are POSITIVE CONTROLS: five
    # of the new tests assert, as their control, that the comment-only corpus
    # DOES fire, so any mutant that stops the measured reading working fails
    # them too. That is the controls doing their job, and the rows still
    # isolate what they name — each set is about the measured reading.
    ("U1  a measured zero never overrules a stated count",
     {"test_two_rounds_that_changed_only_COMMENTS_arm_the_gate",
      "test_a_measured_zero_arms_the_gate_for_a_block_with_NO_payload_field",
      "test_a_prose_only_round_is_UNMEASURED_so_gate_3_keeps_its_population",
      "test_a_round_that_changed_an_EXECUTABLE_line_still_reads_non_zero"},
     the_measured_zero_never_overrules_a_stated_count),
    ("U2  the measurement overrules in BOTH directions",
     {"test_a_measured_NON_zero_never_overrules_a_STATED_zero",
      "test_a_measured_zero_arms_the_gate_for_a_block_with_NO_payload_field",
      "test_two_rounds_that_changed_only_COMMENTS_arm_the_gate"},
     the_measurement_overrules_in_BOTH_directions),
    ("U3  a measured zero only fills in an ABSENT field",
     {"test_two_rounds_that_changed_only_COMMENTS_arm_the_gate",
      "test_a_prose_only_round_is_UNMEASURED_so_gate_3_keeps_its_population",
      "test_a_round_that_changed_an_EXECUTABLE_line_still_reads_non_zero"},
     the_measured_zero_only_fills_in_an_absent_field),
    ("U4  the reading is taken over the DELTA range",
     {"test_the_measured_range_is_the_blocks_OWN_from_to_and_not_the_delta",
      "test_a_measured_zero_arms_the_gate_for_a_block_with_NO_payload_field",
      "test_a_prose_only_round_is_UNMEASURED_so_gate_3_keeps_its_population",
      "test_a_round_that_changed_an_EXECUTABLE_line_still_reads_non_zero",
      "test_two_rounds_that_changed_only_COMMENTS_arm_the_gate"},
     the_reading_is_taken_over_the_DELTA_range),
    ("U5  a measured zero needs a `payload=` field to exist",
     {"test_a_measured_zero_arms_the_gate_for_a_block_with_NO_payload_field"},
     a_measured_zero_needs_a_payload_field_to_exist),
    ("U6  a prose-only round is read as a measured ZERO",
     {"test_a_prose_only_round_is_UNMEASURED_so_gate_3_keeps_its_population"},
     a_prose_only_round_is_read_as_a_measured_zero),
    ("U7  an unrecognised file type is GUESSED to be `#`-commented",
     {"test_an_UNRECOGNISED_file_type_is_UNMEASURED_and_fails_OPEN"},
     an_unrecognised_file_type_is_guessed_to_be_hash_commented),
    # 🔴 THE SECOND KILLER IS MEASURED AND IS THE CLASS GUARD DOING ITS JOB:
    # `test_every_command_a_refusal_prescribes_actually_runs` now carries a
    # REFUSAL 3b case, so a self-range that is accepted returns 5 where that
    # case expects 4.
    # 🔴 ROUND 25 ADDED THREE KILLERS, AND ONE OF THEM IS THE WHOLE ARGUMENT
    # FOR NOT SHIPPING A SECOND REFUSAL.
    # `…ALL_self_range_ladder_is_ALREADY_refused_by_the_shipped_pair_check`
    # asserts that an all-unearned ladder returns 4 at every round >= 2 BECAUSE
    # this check fires — so disabling the check is exactly the state in which
    # that argument would be false, and it must go red there. The other two are
    # the #687-shape rows, which assert rc 4 for the same reason.
    ("U8  a self-range in the gate's own pair is accepted",
     {"test_a_SELF_RANGE_in_a_block_the_gate_reads_is_an_INPUT_refusal",
      "test_a_ladder_whose_EVERY_block_is_a_self_range_says_so_on_stderr",
      "test_an_ALL_self_range_ladder_is_ALREADY_refused_by_the_shipped_pair_check",
      "test_the_block_the_operator_PASTES_carries_the_unearned_ledger_note",
      "test_every_command_a_refusal_prescribes_actually_runs"},
     a_self_range_in_the_gates_own_pair_is_accepted),
    ("U9  the file header is matched BEFORE the hunk",
     {"test_the_classifier_reads_a_changed_line_the_way_git_wrote_it"},
     the_file_header_is_matched_before_the_hunk),
    ("U10 the changed-line test becomes substring membership",
     {"test_the_classifier_reads_a_changed_line_the_way_git_wrote_it"},
     the_changed_line_test_becomes_a_substring_membership),
    ("U11 a failed `git` is read as an empty diff",
     {"test_a_failed_or_noisy_git_is_UNMEASURED_and_never_a_zero"},
     a_failed_git_is_read_as_an_empty_diff),
    ("U12 a COMBINED merge diff is counted anyway",
     {"test_a_COMBINED_merge_diff_is_UNMEASURED_rather_than_miscounted"},
     a_combined_merge_diff_is_counted_anyway),
    # 🔴 ROUND 25 GAVE THIS ROW A SECOND KILLER, AND THE SCOPE IT GUARDS IS NOW
    # LOAD-BEARING TWICE OVER. Widening the refusal to the whole corpus does not
    # merely refuse a run nothing consults — it DELETES the unearned-ledger
    # report's entire reason to exist, because every ladder the report is for
    # would be refused instead of reported. The mixed-shape row asserts rc 0 and
    # a rendered brief, so it goes red there.
    ("U13 the self-range refusal covers the WHOLE corpus",
     {"test_a_SELF_RANGE_the_gate_does_NOT_read_is_reported_and_not_refused",
      "test_a_ladder_with_SOME_self_ranges_reports_them_IN_THE_BRIEF"},
     the_self_range_refusal_covers_the_WHOLE_corpus),
    ("U14 `*` becomes a comment prefix",
     {"test_the_classifier_reads_a_changed_line_the_way_git_wrote_it"},
     an_asterisk_becomes_a_comment_prefix),
]


# --------------------------------------------------------------------------- #
# TESTLIB_ROWS — the CONTROLS that guard this suite's own parsers.
# --------------------------------------------------------------------------- #
# 🔴 A SECOND TABLE, BECAUSE A KILLER SET CANNOT SEE THESE. V68 and V69 above
# forced `shell_code` and `last_command` into existence, and the fix landed
# EIGHT control assertions beside them pinning that the new parsers do not
# OVERSHOOT — the two one-line repairs that were offered (`"#" not in line`,
# `re.split(r'[;&|]+', …)`) each break correct code, and nothing else in the
# module would notice. All eight live inside the body of ONE test, so `ROWS`'
# name-based expectation would report the same single killer for every one of
# them: eight rows reading as coverage while measuring one. These rows match
# the failing assertion's own MESSAGE instead, and a row fails if ANOTHER
# row's message shows up — that is the in-test spelling of EXTRA-KILLER.
#
# 🔴 THEY MUTATE `TEST_REL`, NOT `SCRIPT_REL`. The thing under test here is
# the suite's own parser, so the payload/scaffolding line falls the other way
# round from every row above. Stated rather than left to be inferred, because
# a reader who assumes one target file will mis-read every verdict below.
#
# 🔴 WHY THESE EXIST AT ALL: #1342 merged with the eight assertions UNVERIFIED
# — nobody had shown they were REACHABLE. They sit after an early `return` in
# `test_the_cached_build_fallback_is_emitted_with_its_guards` (the test bails
# when the brief fences no sandbox tier) and after a `len(blocks) == 1`
# assert, either of which would have made all eight vacuous while the suite
# stayed green. Measured 2026-09-08: the precondition holds, exactly one
# `nix log` block is emitted, and each row below kills its own control.
# The last row is the harness's own negative control — see `T9`.

def _testlib_swap(old: str, new: str, what: str):
    """A UNIQUE literal substring of the TEST module, replaced once."""
    def f(t: str) -> str:
        _require_unique(t.count(old), what)
        return t.replace(old, new, 1)
    return f


_HASH_BRANCH = '        if ch == "#" and (not out or out[-1].isspace()):\n'
_SHELL_QUOTE = ('        if ch in "\'\\"":\n            quote = ch\n'
                '            out.append(ch)\n')
_LASTC_QUOTE = ('        if ch in "\'\\"":\n            quote = ch\n'
                '            i += 1\n')
_SEP_SCAN = '        if ch in ";|" or code[i:i + 2] == "&&":\n'
_SHELL_ESCAPE = ('            if ch == "\\\\" and quote == \'"\' and '
                 'i + 1 < len(line):\n')

TESTLIB_ROWS = [
    # (label, the failing assertion's own message, mutation)
    ("T1  shell_code: any `#` opens a comment",
     "part of a flake ref",
     _testlib_swap(_HASH_BRANCH, '        if ch == "#":\n',
                   "shell_code's start-of-word comment test")),
    ("T2  shell_code: quote-blind",
     "inside a quoted diagnosis",
     _testlib_swap(_SHELL_QUOTE,
                   '        if False:\n            quote = ch\n'
                   '            out.append(ch)\n',
                   "shell_code's quote-open branch")),
    ("T3  last_command: quote-blind",
     "inside a quoted regex",
     _testlib_swap(_LASTC_QUOTE,
                   '        if False:\n            quote = ch\n'
                   '            i += 1\n',
                   "last_command's quote-open branch")),
    ("T4  last_command: a bare `&` separates",
     "of a `2>&1` redirection",
     _testlib_swap(_SEP_SCAN, '        if ch in ";|&":\n',
                   "last_command's separator scan")),
    # The four separators, each made invisible ON ITS OWN. The loop runs them
    # in the order `;`, `||`, `|`, `&&`, so a mutation must leave every EARLIER
    # separator working or the message names that one instead — which is
    # exactly what the message check would then report.
    ("T5  last_command: `;` not a separator",
     "reached by ';'",
     _testlib_swap(_SEP_SCAN,
                   '        if ch in "|" or code[i:i + 2] == "&&":\n',
                   "last_command's separator scan")),
    ("T6  last_command: `||` not a separator",
     "reached by '||'",
     _testlib_swap(_SEP_SCAN,
                   '        if ch == ";" or code[i:i + 2] == "&&" or (\n'
                   '                ch == "|" and code[i-1:i] != "|"\n'
                   '                and code[i+1:i+2] != "|"):\n',
                   "last_command's separator scan")),
    ("T7  last_command: a lone `|` not a separator",
     "reached by '|'",
     _testlib_swap(_SEP_SCAN,
                   '        if ch == ";" or code[i:i + 2] in ("||", "&&"):\n',
                   "last_command's separator scan")),
    ("T8  last_command: `&&` not a separator",
     "reached by '&&'",
     _testlib_swap(_SEP_SCAN, '        if ch in ";|":\n',
                   "last_command's separator scan")),
    # 🔴 T9 IS THE HARNESS'S NEGATIVE CONTROL, and it is the row that makes the
    # eight above readable. Without it, a sub-battery wired to nothing — a
    # mutation that never lands, a pytest that never selects the test — reports
    # nine KILLEDs and is indistinguishable from nine real ones. This mutates a
    # branch of `shell_code` that NO control fixture and no line of the emitted
    # block reaches (a backslash escape inside double quotes), so it MUST
    # survive. A red here means the rows above are reacting to the EDIT rather
    # than to the semantics, and every verdict beside it is uninterpretable.
    ("T9  (control) an unreached shell_code branch",
     SURVIVES,
     _testlib_swap(_SHELL_ESCAPE, '            if False:\n',
                   "shell_code's backslash-in-double-quotes branch")),
]


def failing(root: Path, tb: str = "no"):
    """-> (killer test names, raw output). Reads the CONTENT, never an exit code.

    🔴 `tb` IS NOT COSMETIC — it decides what the caller can DISCRIMINATE.
    `--tb=no` yields test NAMES only, which is all `ROWS` needs and all it can
    use. `TESTLIB_ROWS` below mutates parsers whose eight controls live inside
    ONE test, so a killer SET cannot tell them apart: every row would report
    the same single name and eight rows would read as coverage while measuring
    one. Those rows ask for `--tb=short` and match the failing assertion's own
    MESSAGE instead.
    """
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    p = subprocess.run(
        [sys.executable, "-m", "pytest", str(root / TEST_REL),
         "-q", "--no-header", f"--tb={tb}", "-p", "no:cacheprovider"],
        capture_output=True, text=True, env=env, cwd=str(root), check=False,
    )
    # stderr is CAPTURED, not discarded: the commonest way to get "0 tests ran"
    # on this host is running outside `nix develop` (`.envrc` is `use opencode`,
    # so a loaded direnv has no pytest), and discarding it turns that one-line
    # diagnosis into a headline that blames the TREE.
    out = p.stdout + p.stderr
    if "No module named pytest" in out:
        return None, ("pytest is not on PATH — run this as `nix develop "
                      "~/workspace/devrc -c python3 scripts/tests/"
                      "mutants-audit-dispatch.py`, not in a bare shell")
    passed = sum(int(n) for n in re.findall(r"(\d+) passed", out))
    failed = sum(int(n) for n in re.findall(r"(\d+) failed", out))
    if passed + failed < MIN_TESTS:
        return None, (f"only {passed + failed} test(s) ran (floor {MIN_TESTS}) — "
                      f"the harness, not the tree:\n{out[-800:]}")
    return set(re.findall(r"^FAILED [^:]*::([A-Za-z0-9_]+)", out, re.M)), out


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="audit-dispatch-mut-"))
    try:
        root = tmp / "tree"
        (root / "scripts" / "tests").mkdir(parents=True)
        shutil.copy(REPO / SCRIPT_REL, root / SCRIPT_REL)
        shutil.copy(REPO / TEST_REL, root / TEST_REL)
        shutil.copy(REPO / HARNESS_REL, root / HARNESS_REL)
        # 🔴 THE SANDBOX MUST CARRY WHAT THE TEST MODULE IMPORTS. It does not
        # copy `conftest.py` (deliberately — the point is a minimal tree), so
        # `scripts/` is not on `sys.path` and the module inserts it itself.
        # These are the only non-stdlib modules it needs. Miss one and pytest
        # dies during COLLECTION, which surfaces as `only 0 test(s) ran` — the
        # floor check below catches it, and this comment is where to look.
        (root / "scripts" / "testlib").mkdir(parents=True, exist_ok=True)
        for rel in TESTLIB_RELS:
            shutil.copy(REPO / rel, root / rel)
        for rel in SKILL_RELS:
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(REPO / rel, root / rel)
        if (root / ".git").exists():
            print("🔴 the copy carries a .git — refusing to run")
            return 2
        original = (root / SCRIPT_REL).read_text(encoding="utf-8")

        killers, raw = failing(root)
        if killers is None:
            print(f"🔴 THE HARNESS could not run: {raw}")
            print("   Nothing was measured. This says nothing about the tree.")
            return 2
        if killers:
            print("🔴 the UNMUTATED tree is already failing: "
                  + ", ".join(sorted(killers)))
            print("   every row below would be meaningless. Fix that first.")
            return 2
        n = sum(int(x) for x in re.findall(r"(\d+) passed", raw))
        print(f"POS  unmutated copy .......................... {n} passed")
        print()

        bad = 0
        for label, want, mutate in ROWS:
            try:
                mutated = mutate(original)
            except AssertionError as e:
                print(f"  🔴 {label:44s} MUTATION DID NOT APPLY — {e}")
                bad += 1
                continue
            if mutated == original:
                print(f"  🔴 {label:44s} MUTATION DID NOT APPLY (no change)")
                bad += 1
                continue
            (root / SCRIPT_REL).write_text(mutated, encoding="utf-8")
            got, raw2 = failing(root)
            (root / SCRIPT_REL).write_text(original, encoding="utf-8")

            if got is None:
                print(f"  🔴 {label:44s} HARNESS BROKE — {raw2}")
                bad += 1
                continue
            # 🔴 A row that must SURVIVE. Reported through the same code path as
            # everything else, so a control cannot quietly stop being checked.
            # 🔴 A DOCUMENTED HOLE. Same code path, a different verdict line,
            # because "SURVIVED as required (control)" printed over an
            # unguarded sentence is the flattering wrong answer.
            if want is HOLE:
                if got:
                    print(f"  ⚠ {label:44s} NOW KILLED by {sorted(got)} — a "
                          "guard covers this now. Promote the row to that "
                          "killer set and delete its line from the ledger "
                          "comment in scripts/audit-dispatch.py.")
                    bad += 1
                else:
                    print(f"  ok {label:44s} SURVIVED — DOCUMENTED HOLE "
                          "(nothing guards it; the script's ledger says so)")
                continue
            if want is SURVIVES:
                if got:
                    print(f"  🔴 {label:44s} KILLED by {sorted(got)} — this row "
                          "must SURVIVE; the pins are keyed to layout, not to "
                          "the instruction")
                    bad += 1
                else:
                    print(f"  ok {label:44s} SURVIVED as required (control)")
                continue
            if not got:
                print(f"  🔴 {label:44s} SURVIVED — no test failed")
                bad += 1
                continue
            missing = want - got
            extra = got - want
            if missing:
                print(f"  🔴 {label:44s} WRONG-KILLER: {sorted(missing)} did NOT "
                      f"fire (got {sorted(got)})")
                bad += 1
                continue
            if extra:
                print(f"  🔴 {label:44s} EXTRA-KILLER: {sorted(extra)} also fired "
                      "— the row no longer isolates what it names")
                bad += 1
                continue
            print(f"  ok {label:44s} killed by exactly {len(got)}: "
                  + ", ".join(sorted(got)))

        # ------------------------------------------------------------------ #
        # The suite's own parser controls. Different target file, different
        # discrimination — see the TESTLIB_ROWS banner.
        # ------------------------------------------------------------------ #
        print()
        print("TESTLIB (mutating scripts/tests/test_audit_dispatch.py itself)")
        original_test = (root / TEST_REL).read_text(encoding="utf-8")
        all_msgs = [m for _, m, _ in TESTLIB_ROWS if m is not SURVIVES]
        for label, want, mutate in TESTLIB_ROWS:
            try:
                mutated = mutate(original_test)
            except AssertionError as e:
                print(f"  🔴 {label:44s} MUTATION DID NOT APPLY — {e}")
                bad += 1
                continue
            if mutated == original_test:
                print(f"  🔴 {label:44s} MUTATION DID NOT APPLY (no change)")
                bad += 1
                continue
            (root / TEST_REL).write_text(mutated, encoding="utf-8")
            got, raw2 = failing(root, tb="short")
            (root / TEST_REL).write_text(original_test, encoding="utf-8")

            if got is None:
                print(f"  🔴 {label:44s} HARNESS BROKE — {raw2}")
                bad += 1
                continue
            if want is SURVIVES:
                if got:
                    print(f"  🔴 {label:44s} KILLED by {sorted(got)} — this row "
                          "must SURVIVE. The rows above are then reacting to "
                          "the EDIT, not the semantics, and none of their "
                          "verdicts can be read.")
                    bad += 1
                else:
                    print(f"  ok {label:44s} SURVIVED as required (control)")
                continue
            if not got:
                print(f"  🔴 {label:44s} SURVIVED — the control it targets is "
                      "UNREACHABLE or vacuous")
                bad += 1
                continue
            # 🔴 THE MESSAGE, NOT THE TEST NAME. All eight controls share one
            # test, so a name proves only that SOMETHING in it fired.
            others = [m for m in all_msgs if m != want and m in raw2]
            if want not in raw2:
                print(f"  🔴 {label:44s} KILLED FOR THE WRONG REASON: the "
                      f"failure does not carry {want!r}"
                      + (f"; it carries {others!r}" if others else ""))
                bad += 1
                continue
            if others:
                print(f"  🔴 {label:44s} EXTRA-KILLER: {others!r} also fired — "
                      "the row no longer isolates the control it names")
                bad += 1
                continue
            print(f"  ok {label:44s} killed by its OWN control ({want!r})")

        print()
        total = len(ROWS) + len(TESTLIB_ROWS)
        if bad:
            print(f"🔴 {bad} of {total} row(s) not as expected")
            return 1
        print(f"✅ {total} row(s), all as expected")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
