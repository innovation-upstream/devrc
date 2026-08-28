#!/usr/bin/env python3
"""Tests for `scripts/audit-dispatch.py` — the audit-brief assembler.

WHAT THE SCRIPT IS FOR, so these tests are read at the right scope
------------------------------------------------------------------
The `/audit-pr` skill tells an operator to dispatch a subagent and supplies no
procedure, so the brief was reassembled from prose every time. Measured over one
session's 14 dispatches: 60,100 chars written by hand, 42% mean similarity
between consecutive briefs, ZERO lines over 25 chars identical across all 14 —
and three hard-won clauses present in early dispatches and LOST by later ones.
The script exists to make the invariant sections impossible to forget; this
module exists to make them impossible to delete quietly.

🔴 WHICH TESTS ARE REGRESSION COVERAGE AND WHICH ARE NOT
---------------------------------------------------------
`claude/RULES.md` requires the distinction, and requires naming the base a test
was watched red at.

  RED_AT_BASE WAS EMPTY AND IS NOT ANY MORE, and the reason is the point of this
  section. It was empty because `scripts/audit-dispatch.py` did not exist at
  `3b79a35a` (this branch's base): every test would have ERRORED there for want
  of the module, which is not evidence of anything.

  Round 2's adversarial audit then found NINE defects in the shipped script —
  three 🔴, six 🟡, every one reproduced live — so there is now a base with a
  real bug in it: **`abc41024`**, the branch head that carried them. Measured
  by restoring that script into a scratch tree with this module copied in
  unchanged, under `PYTHONDONTWRITEBYTECODE=1 -p no:cacheprovider`:

      24 failed, 34 passed          <- 24 node ids, 21 functions

  Those 21 are `RED_AT_BASE`, and three of them are labelled INSIDE that
  constant as weaker evidence than the rest: two assert text the fix added
  rather than observing a wrong answer, and one pins a sentence about a gap
  this PR documents but does not close.

  Everything else is an INVARIANT GUARD or a STRUCTURAL LEDGER. What makes those
  non-vacuous is the MUTATION MATRIX below: each was watched to fail against a
  deliberately mutated copy of the script, and the mutant is named beside the
  test that caught it. A guard nobody watched go red is a guard nobody has
  evidence for, whatever its base ref.

  Both ledgers are asserted mechanically at the bottom of this module
  (`test_the_two_ledgers_partition_this_modules_tests`), which now also refuses
  an entry appearing in BOTH — a test is regression coverage or it is a guard,
  and listing it twice makes the count of what was watched red unreadable.

🔴 THE FIX MATRIX IS `FIX_MATRIX`, NOT THIS DOCSTRING
------------------------------------------------------
One row per finding — the defect, the test that detects it, the EVIDENCE for
that test (a base ref it was watched red at, or `GUARD` when its evidence is the
mutation battery instead), and the mutants. It is a module-level constant near
the bottom, graded against the two ledgers by
`test_the_fix_matrix_evidence_matches_the_two_ledgers`, and that test carries an
in-module negative control.

🔴 IT USED TO BE A PROSE TABLE HERE, UNDER THE HEADER "detector (red at
abc41024)" and the sentence "Every 'red at' cell is `abc41024`" — AND THAT WAS
FALSE FOR ONE ROW. Finding 5's detector,
`test_each_clause_carries_the_instruction_its_ledger_entry_names`, is GREEN at
`abc41024` and is not among the 24 that failed there; the constant fifty lines
below said exactly that, and the table said the opposite. Its real evidence is
mutants W1-W5, which is arguably stronger than a base ref — only the LABEL was
wrong, and a reader checking whether that finding had been watched red would
have read the table and stopped. A table nobody can check acquires this kind of
error, so the record moved into data and the prose stops here.

🔴 ROUND 3 — a second base, and it is `d9eb36a8`, not `abc41024`
-----------------------------------------------------------------
Round 3's audit found defects in the ROUND-2 tree: the delta range was EMPTY BY
CONSTRUCTION (`audited=` meant `<to>` to the reader and `<to>` to the writer, so
`<to>..HEAD` had HEAD on both ends), the empty-range reason named two causes
`head_check` refutes, and three verbatim instruction blocks shipped unpinned
while three inversions passed a fully green 58-test suite.

`RED_AT_BASE_REFS` maps each base ref to the tests watched red there. Round 3's
set is measured against `git show d9eb36a8:scripts/audit-dispatch.py` restored
into a scratch tree with THIS module copied in unchanged, under
`PYTHONDONTWRITEBYTECODE=1 -p no:cacheprovider`:

    14 failed, 58 passed         <- 14 node ids, 14 functions

🔴 AND ONLY **NINE** OF THOSE FOURTEEN ARE IN `RED_AT_BASE_R3`. The other five
fail with `AttributeError: module 'audit_dispatch' has no attribute
'SECTION_DIRECTIVES'` (four) or `... 'range_anchor'` (one) — they ERROR for
want of a name the fix introduced, which this module's opening section already
says is not evidence of anything. Counting them would inflate the regression
tally with five tests that never reached an assertion, so they are filed as
guards with the mutation battery as their evidence, and the gap between 14 and
9 is written down here rather than left for a reader to reconcile.

One of the nine is labelled INSIDE the constant as weaker evidence than the
rest (it is the does-not-fire-spuriously control for the new banner, so its red
restates the diff). Round 3's guards are listed in
`INVARIANT_GUARDS_AND_LEDGERS` with the reason each is a guard and the mutant
that proves it executes.

🔴 ROUND 4 — a third base, `e06461f7`
--------------------------------------
Round 4's audit found four 🟡 in the ROUND-3 tree, **two of them in code that
round wrote**: the round-1 -> round-2 hop was empty by construction and
`--emit-claims` was silent about it (the self-range warning was spelled on a
field that is None at round 1, so it was structurally unreachable for the one
spelling it was for); both new degenerate-range messages blamed "the round's
fix commits are not in this checkout" over a checkout `anchor_is_head` had
just verified to BE the PR's head; the "which blocks are NOT pinned" comment
claimed a coverage that did not exist over an enumeration that was incomplete;
and THE SHARED CHECKOUT asserted "shared" of whatever cwd was, including a
private per-agent worktree — telling that auditor the movement it should
report is expected and not a finding.

Measured at `e06461f7` the same way: **15 failed, 68 passed**, of which FIVE
are regression coverage. `RED_AT_BASE_R4` carries them and the constant says
why the other ten are guards.

🔴 ROUND 5 — a fourth base, `dd601793`
---------------------------------------
Round 5's audit found that round 4's own fix INVERTED the write rule while
fixing the sharedness one: the private checkout state was rewritten to "**a
PRIVATE worktree — yours alone** … Writing here is fine", and `no-fetch` was
reworded in the same range from unconditional to conditional on a state
`gather_worktree_kind` cannot know — it measures the ASSEMBLING process's cwd,
and the consumer is a different process that WHERE TO WORK dispatches
elsewhere. So the brief granted write permission over the dispatching session's
tree and overrode its own `read-only` clause, in exactly the configuration
production produces. TOOLCHAIN separately still called that path "the SHARED
CHECKOUT" two bars after THE CHECKOUT had denied it, `--audited` accepted any
string and the emitter's own parser truncated it in silence, and the
degenerate-cause banner blamed an omission that is CORRECT from round 2 on.

Measured at `dd601793` the same way — `git show dd601793:scripts/audit-dispatch
.py` into a scratch tree with THIS module copied in unchanged, under
`PYTHONDONTWRITEBYTECODE=1 -p no:cacheprovider`:

    10 failed, 79 passed

and only FOUR of the ten are regression coverage. Of the other six: TWO are
round 5's new guards, red there on an ABSENCE the fix adds (two of the three
checkout states carry no no-write rule at that ref; there is no legend at all),
and FOUR are pre-existing tests whose ledgers moved with the reword — the two
whole-string ledger tests, the degenerate-cause test that asserts the ledgered
text, and the private-worktree test whose POSITIVES moved. That last one stays
filed under `e06461f7`: its two NEGATIVES are untouched and run first, and it
was RE-MEASURED at that ref after the change, failing on the false SHARED claim
exactly as before.

🔴 THE FIX MATRIX GAINED TWO CHECKS IT DID NOT HAVE, and both were holes the
round-4 audit found in this module's own bookkeeping: the `mutants` column was
DISCARDED by `fix_matrix_problems` (rewriting a GUARD row to name a mutant that
does not exist left the suite green, while for a GUARD row that column IS the
evidence), and nothing required a `RED_AT_BASE` entry to appear in the matrix
at all — ten rows over nine distinct detectors were in that state, so five
findings could have dropped out with nothing going red. 🔴 THAT SENTENCE USED
TO SAY "nine round-2 detectors and one round-3 detector", which counts TEN over
a set of NINE: one test is red at two bases and carries a row for each. The
count now comes out of the ledgers in the assertion itself.

🔴 FINDING 3's PRESCRIPTION WAS WRONG ON ONE POINT, and it is recorded because
the next person will reach for the same field: the audit said to use
`isCrossRepository` + **`baseRepository`**. `gh pr view --json` HAS NO
`baseRepository` FIELD (checked against `gh`'s own field list, which is what it
prints on an unknown key). `url` is what carries the base repo, and it is what
`pr_slug` reads; `isCrossRepository` decides whether the head fields may stand
in when there is no url.

🔴 THE NEGATIVE-CONTROL MATRIX — measured, and RE-DERIVABLE
-------------------------------------------------------------
🔴 The rows below are transcribed from a harness that is IN THE TREE, and that
harness is the authority on which rows exist — count them there, not here:

    nix develop ~/workspace/devrc -c python3 scripts/tests/mutants-audit-dispatch.py

It exists rather than a scratchpad script for the reason
`scripts/tests/mutants-audit-ladder.sh` records: devrc #900 wrote ~30 mutants
into a docstring and every run of them happened in a session directory that no
longer exists, so not one row could be re-checked by anyone else — including the
rows that justified adding a pin. Each row there names the EXACT killer set and
reports WRONG-KILLER (an expected pin did not fire) separately from EXTRA-KILLER
(the row no longer isolates what it names).

Re-run 2026-08-28 against HEAD of this branch after the round-5 fixes — **76
rows, all as expected**, over an 89-test positive control — each mutant applied
to a COPY of
`scripts/audit-dispatch.py` in a scratch tree (never the worktree), under
`PYTHONDONTWRITEBYTECODE=1 -p no:cacheprovider`, with the unmutated copy as the
positive control. Every mutant asserted its target string present before
editing — a mutation that silently fails to apply reports "the guard held",
which is the most flattering possible wrong answer. Two rows have done exactly
that: `C3`, written against the two-state `cross_repo` flag, and `H2`, whose
branch became an `elif` when round 3 inserted the degenerate-self-range case
ahead of it. Both were re-targeted; without that assert each would have scored
as a guard holding.

🔴 THE ROUND-3 BATTERY CAUGHT TWO DEFECTS IN THE ROUND-3 TESTS THEMSELVES, and
they are recorded because both are the shape this file warns about elsewhere:
  * `N5` (delete the LEDGER's self-range branch) SURVIVED a fully green suite,
    because the test asserting "EMPTY BY CONSTRUCTION" was reading the WHOLE
    brief and matching THE RANGE section's banner instead. Two sections saying
    the same phrase is not two guards. Fixed by slicing the ledger section.
  * `H3` and `C2` came back EXTRA-KILLER against new rows that had duplicated a
    neighbouring test's assertion (the `<to>` sha) and had parsed the FIRST
    `audit-claims` fence in the output rather than the emitted one.

The rows below are the pre-existing sixteen. Everything added since —
W1-W5 + S1, H1-H4, T1-T2, P1-P2, K1-K3, L1, B1-B5 in round 2; N1-N8, X1-X3, XA
and the SURVIVES control XS in round 3; Y1-Y14 in round 4; Z1-Z8 in round 5 —
is listed in the harness with its killer set and is NOT transcribed here; the
harness is the authority on which rows exist.

🔴 ROUND 4 RE-TARGETED FIVE PRE-EXISTING ROWS AND SHIFTED SEVEN KILLER SETS,
and both kinds are worth reading before trusting any row here. S1 and N6
reported MUTATION DID NOT APPLY (the `no-fetch` clause was reworded; the emit
warning's predicate moved onto the next round's anchor) — without the
assert-before-editing rule each would have scored as "the guard held". XA's
expected set had to CHANGE rather than grow: the hazard it names is now caught
one guard earlier, and leaving the old name there would have left a DEAD pin
reading as coverage.

🔴 ROUND 5 RE-TARGETED S1 AGAIN — the FOURTH time assert-before-editing has
caught a row that would otherwise have scored as "the guard held", and again on
the `no-fetch` clause, which round 5 made unconditional. Three killer sets
GREW, and the growth is recorded on each row because two of the three are a
measured COUPLING rather than a pin doing new work: X3 also drops the no-write
sentence round 5 added to every checkout state (real second hazard), while N3
and Y7 each stop a `--audited` value from reaching the emitted header, so no
end-to-end test of "a bad value is refused" can survive them. 🔴 One PREDICTED
killer did NOT fire — Z1 leaves `..._private_worktree_is_not_described_as_shared
_and_is_not_absolved` green, correctly, because that test covers the
SHARED/PRIVATE description and the write GRANT is a claim it never made. The
prediction was written down and the harness refuted it, which is the point of
running it rather than reporting it.

🔴 ROUND 4 ALSO INTRODUCED A THIRD EXPECTATION, `HOLE`, and it is not a synonym
for `SURVIVES`. `SURVIVES` is a control (a pin must not be keyed to layout);
`HOLE` is a measured GAP (Y3-Y5: three operative sentences nothing guards).
Printing "SURVIVED as required (control)" over an unguarded sentence is exactly
the flattering wrong answer this battery exists to refuse.

  POS  unmutated copy .............................. 72 passed  <- control
       (that figure is the ROUND-2 control; today's is 89 — the harness
        prints the current one, which is the number to read)

  D1   delete the `read-only` clause ............... 4 failed
  D3   delete the `stop-rule` clause ............... 4 failed
  D4   delete the `nit-is-not-a-finding` clause .... 4 failed
  D5   delete the `reverify-self-reported` clause .. 4 failed
  D6   delete the `finding-format` clause .......... 4 failed
         all five kill the same four: ledger_is_pinned_two_way /
         carries_the_instruction_its_ledger_entry_names /
         rendered_section_holds_exactly /
         control_a_clause_deleted_from_the_constant_is_detected
  D2   delete the `no-fetch` clause ................ 4 failed
         the three ledger tests, plus
         missing_clause_check_warns_and_never_blocks — that one looks up
         `no-fetch` BY ID, so deleting that particular clause makes it error.
         Recorded rather than tidied away: it is a real coupling, and it is why
         the deleted-clause CONTROL is a different test from the warn test.
  D7   delete the `do-not-merge` clause ............ 5 failed
         the same four, plus out_file_is_read_back_and_checked — the SECOND
         instance of D2's shape, and for the same reason: that test looks
         `do-not-merge` up BY ID to model a lossy write. An INDEX would be
         worse, not better; it would make the test's outcome depend on which
         OTHER clause a mutation deleted.
  A1   ADD an eighth clause with no ledger entry ... 3 failed
         ledger_is_pinned_two_way / rendered_section_holds_exactly /
         control_a_clause_deleted_from_the_constant_is_detected
  R1   reword `stop-rule` to drop "ending it is the
       CORRECT outcome, not a failure" — the clause
       is still present and still emitted .......... 1 failed
         carries_the_instruction_its_ledger_entry_names ALONE
         🔴 THE REACHABILITY CONTROL. Every presence pin stays GREEN, so a red
         here proves the CLAUSE ledger executes and is not a second spelling
         of the id ledger beside it. W1-W5 are the same shape, one per
         instruction that a fragment pin could not see.
  F1   render the invariant section from a hardcoded
       copy of the bullets, ignoring the constant ... 4 failed
         ledger_is_pinned_two_way stays GREEN (the ids are untouched);
         rendered_section_holds_exactly, emitted_verbatim_in_both_kinds, the
         deleted-clause control and the `--check` round trip go red. This is
         why the RENDERED section is compared and not only the constant — and
         the `--check` row is the one that reaches it through a real FILE.
  C1   the delta refusal removed (a round-N brief is
       emitted with no claims block) ............... 4 failed
         delta_refusal_exits_non_zero / refusal_names_what_it_looked_for /
         refusal_fires_for_a_malformed_block / refusal_says_which_comment_kinds
  C2   claims read from the WHOLE comment body
       instead of the fenced block ................. 3 failed
         brief_carries_the_claims_and_not_the_reasoning (the framing
         guarantee) / newest_claims_block_wins / emit_claims round-trip
  C3   the two KNOWN repo states swapped .......... 4 failed
         cross_repo_tells_the_agent_to_worktree_the_prs_repo /
         same_repo_recommends_the_isolation_flag / decision_comes_from_the_repos
         / fork_pr_against_this_repo_is_not_treated_as_cross_repo — the last
         one is what proves the FORK case rides this same decision. The fixture
         used to encode a model in which it did not, which is how three
         cross-repo tests and this mutant all passed over a live bug.
  C4   the numstat command's non-zero exit reads as
       a clean zero instead of COULD NOT MEASURE ... 1 failed
         ledger_refuses_a_failed_command_rather_than_printing_zero
  C5   the ledger classifies by a `test`-substring
       pathspec and prints a number for X .......... 1 failed
         ledger_shows_the_files_and_refuses_to_classify_them
  C6   the missing-clause warning BLOCKS
       (returns non-zero) .......................... 2 failed
         missing_clause_check_warns_and_never_blocks /
         out_file_is_read_back_and_checked (it too asserts rc 0 over a `--out`
         run whose file is missing a clause)

🔴 ONE MEASURED NON-RESULT, RECORDED BECAUSE IT LOOKS LIKE COVERAGE:
`test_every_clause_is_emitted_verbatim_in_both_kinds_of_brief` does **NOT** go
red for D1-D7. It iterates `INVARIANT_CLAUSES` and asserts each entry reaches
the brief, so a clause deleted from the constant is deleted from both sides of
its comparison. It is the POSITIVE control (a zero-length or clause-free brief
fails loudly) and F1's second killer — it is not a deletion detector, and
reading it as one is exactly the "guard whose description is wider than its
implementation" failure. The deletion detectors are the ledger tests.

🔴 WHAT THIS MODULE DOES **NOT** ENFORCE
-----------------------------------------
1. **It cannot see a clause reworded into a SEMANTICALLY IDENTICAL sentence.**
   🔴 This item used to read "it cannot see a clause REWORDED into something
   weaker", and that was a live hole, not a documented limit: `CLAUSE_LEDGER`
   pinned one phrase per clause, and five rewords that INVERTED their clause
   passed a fully green 37-test suite — including one telling every future
   auditor that "one confirming round after a clean one is prudent", which is
   what `claude/RULES.md` forbids.

   The ledger now pins the WHOLE whitespace-normalised clause, the way the
   sibling module `scripts/tests/test_audit_ladder_stop_rule.py` pins the prose
   it guards. What remains out of reach is what remains out of reach there too:
   a restatement in different words that means the same thing. There is no known
   mechanical fix, and the cost of the pin is that a cosmetic REWORD now fails
   here — which is the price, deliberately paid, for a machine-readable claim.
   A pure re-wrap is free: whitespace is normalised, and mutant S1 is the
   SURVIVES control proving it.
2. **It proves nothing about BEHAVIOUR.** Nothing here shows any auditor read
   the brief, obeyed the no-fetch clause, or stopped at a clean round. The
   measurement that motivated the script was over transcripts; the verifier for
   whether it CHANGED anything is a re-measurement over future dispatches, and
   that claim is NOT made here.
3. **It does not test the real `gh` or `git`.** Every test injects a runner. So
   a wrong `gh --json` field name, or a `git` flag this host's version does not
   have, is out of scope — the seam is tested, not the tools behind it. That is
   deliberate (hermetic), and it is a real blind spot, not a covered one.
4. **It does not check that the classification a human writes is CORRECT.** The
   script deliberately refuses to classify payload vs scaffolding; nothing
   mechanical can grade the answer.

HERMETIC: no test touches the network, spawns a process, or reads a real PR.
`test_nothing_here_spawns_a_subprocess` proves the first two by making
`subprocess.run` raise for the duration of a full `main()` run.
"""
from __future__ import annotations

import importlib.util
import io
import json
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "audit-dispatch.py"


def _load():
    spec = importlib.util.spec_from_file_location("audit_dispatch", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ad = _load()

# --------------------------------------------------------------------------- #
# 🔴 THE INDEPENDENT LEDGER — this is what makes the pin two-way.
# --------------------------------------------------------------------------- #
# If this ledger were derived from `ad.INVARIANT_CLAUSES` the whole test would
# be vacuous: deleting a clause would delete it from both sides and the
# comparison would stay green. So the ids and the WHOLE TEXT of each clause are
# RESTATED here by hand, and the duplication is the point.
#
# 🔴 THESE USED TO BE FRAGMENTS, AND FIVE INSTRUCTION-INVERTING REWORDS PASSED A
# FULLY GREEN 37-TEST SUITE:
#
#   read-only      -> "Edit the repo under audit freely if it helps."
#   no-fetch       -> "`pull` and `checkout` in the shared checkout are fine."
#   finding-format -> `file:line` and the scenario requirement deleted
#   reverify       -> "Where time allows, re-verify…"
#   stop-rule      -> "one confirming round after a clean one is prudent"
#
# The last one makes every future brief instruct auditors to do the thing
# `claude/RULES.md` forbids. A fragment pin certifies that a PHRASE is present,
# never that the instruction around it still says what it said.
#
# So these are WHOLE, whitespace-normalised strings, the way the sibling module
# `scripts/tests/test_audit_ladder_stop_rule.py` pins the prose it guards — and
# for the reason it cites, `claude/RULES.md` -> spelled-guards: "when the
# artifact under test IS prose, a guard on WORDS is walkable by REWORDING — pin
# the WHOLE normalised string. A cosmetic reword then fails the test — pay it,
# for a machine-readable claim."
#
# Normalising whitespace is what keeps the price to a REWORD and not a re-wrap:
# the mutation battery carries a SURVIVES control ("re-wrap a clause across
# different line breaks") proving a pure re-flow stays green here.
CLAUSE_LEDGER = {
    "read-only": (
        "**READ-ONLY — you modify nothing in the repository under audit.** If "
        "you must mutate something to test a theory, do it in a `cp -a` copy "
        "and run `rm -f <copy>/.git` FIRST: a worktree's `.git` is a FILE "
        "pointing at the real git dir, so a commit inside the copy lands on "
        "the branch you are auditing."
    ),
    # 🔴 REWORDED TWICE, AND ROUND 5 REVERSED ROUND 4's DIRECTION. Round 4 made
    # the prohibition CONDITIONAL — "write to a SHARED checkout — THE CHECKOUT
    # section … says whether the one it names is shared" — which armed it on a
    # state the script cannot know: `gather_worktree_kind` measures the tree the
    # ASSEMBLER stands in, and in production it answers `private`, so the rule
    # switched itself off over a tree belonging to the dispatching session.
    # The axis that is true in every state is OWNERSHIP, not sharedness, and it
    # keeps `own-worktree-is-writable` consistent: the copy YOU made is the one
    # you may write to.
    "no-fetch": (
        "**Do NOT `git fetch`, `pull`, `checkout` or otherwise write to any "
        "checkout that is not the copy YOU made for this audit — including the "
        "one THE CHECKOUT section names, which is where this brief was "
        "assembled and is not yours.** Other sessions are in those trees; a "
        "fetch there is a write with cross-session blast radius, and every ref "
        "you need is already resolved for you here."
    ),
    "stop-rule": (
        "**A clean round ENDS the ladder — ending it is the CORRECT outcome, "
        "not a failure.** Rounds continue only while the previous round "
        "produced a finding that needed fixing. Do not manufacture findings to "
        "justify the round, and do not run another round to confirm a clean "
        "one."
    ),
    "nit-is-not-a-finding": (
        "**A nit that changes nothing a reader does is NOT a finding.** If the "
        "fix would be a reword with no behavioural, decision or correctness "
        "consequence, leave it out of the findings and say so in one line "
        "under the verdict instead."
    ),
    "reverify-self-reported": (
        "**Re-verify the fix commit's own self-reported numbers rather than "
        "accepting them.** Counts, byte sizes, mutation-sweep results and "
        "\"watched red at <sha>\" claims in a commit message or PR body are "
        "claims to check against the tree, never evidence."
    ),
    "finding-format": (
        "**Report each finding with `file:line`, a concrete failure scenario "
        "(the input, the path taken, the wrong output) and a `payload` or "
        "`scaffolding` label** — payload is what the PR exists to ship; "
        "scaffolding is the tests, fixtures and notes a round wrote to guard "
        "it."
    ),
    "do-not-merge": (
        "**Do not merge — report only.** No pushes, no PR comments, no "
        "`gh pr merge`. Hand the findings back and let the operator act on "
        "them."
    ),
}


def norm(text: str) -> str:
    """Whitespace-normalised. A re-wrap must not fail; a reword must."""
    return " ".join(text.split())

# Restated, not imported, for the same reason: the bullet extractor below must
# not be steerable by the module it audits.
EXPECTED_INVARIANTS_HEADING = "## 🔴 NON-NEGOTIABLE — every audit, every round"


def bullets_in_invariant_section(brief: str) -> list[str]:
    """Every `- ` bullet between the invariants heading and the next `## `.

    A second, independent implementation on purpose — the script renders that
    section, so a shared parser could agree with a broken renderer.
    """
    lines = brief.splitlines()
    assert EXPECTED_INVARIANTS_HEADING in lines, (
        "the rendered brief has no invariants heading spelled exactly "
        f"{EXPECTED_INVARIANTS_HEADING!r} — every pin below is looking in a "
        "section that does not exist, so they would all pass or all fail for "
        "the wrong reason."
    )
    start = lines.index(EXPECTED_INVARIANTS_HEADING)
    out = []
    for line in lines[start + 1:]:
        if line.startswith("## "):
            break
        if line.startswith("- "):
            out.append(line[2:])
    return out


# --------------------------------------------------------------------------- #
# Fakes for the ONE process boundary
# --------------------------------------------------------------------------- #

# 🔴 THIS FIXTURE SHARED THE DEFECT IT WAS SUPPOSED TO CATCH. It carried no
# `isCrossRepository`, no `headRefOid`, and a `url` of the wrong SHAPE
# (`.../pulls/900`), so every cross-repo test — and mutant C3 — ran against a
# model of `gh` in which the head repo IS the PR's repo. That model is false for
# every fork PR, and it is why three tests and a mutation row all passed through
# the bug.
#
# Field shapes are copied from real `gh pr view --json` output (values are
# synthetic; the host is `example.invalid` on purpose — this repo is public).
# The load-bearing shape is `url` = `<scheme>://<host>/<owner>/<name>/pull/<n>`,
# which names the repo the PR LIVES in.
FAKE_HEAD_OID = "1111111122222222333333334444444455555555"

DEFAULT_PR = {
    "title": "a synthetic PR title",
    "url": "https://example.invalid/example-org/devrc/pull/900",
    "baseRefName": "main",
    "headRefOid": FAKE_HEAD_OID,
    "isCrossRepository": False,
    "headRepository": {"name": "devrc", "nameWithOwner": "example-org/devrc"},
    "headRepositoryOwner": {"login": "example-org"},
}

# A FORK PR: opened from a contributor's fork AGAINST `example-org/devrc`, which
# is the cwd's repo. Verified against real `gh` output for a fork PR (the head
# repo is the contributor's, `url` is the base repo's, `isCrossRepository` is
# true). The correct brief for this is the SAME-REPO one.
FORK_PR = {
    "title": "a synthetic fork PR title",
    "url": "https://example.invalid/example-org/devrc/pull/900",
    "baseRefName": "main",
    "headRefOid": FAKE_HEAD_OID,
    "isCrossRepository": True,
    "headRepository": {
        "name": "devrc", "nameWithOwner": "some-contributor/devrc",
    },
    "headRepositoryOwner": {"login": "some-contributor"},
}

# A genuinely cross-repo PR: it lives in a repository that is NOT the cwd's.
OTHER_REPO_PR = dict(
    DEFAULT_PR,
    url="https://example.invalid/someone-else/otherproj/pull/900",
    headRepository={
        "name": "otherproj", "nameWithOwner": "someone-else/otherproj",
    },
    headRepositoryOwner={"login": "someone-else"},
)

FAKE_REPO_DIR = "/fake/checkout/devrc"
FAKE_ORIGIN = "git@github.com:example-org/devrc.git"

# 🔴 The two paths `gather_worktree_kind` reads, in the shapes REAL git prints
# them — measured at four points (repo root, repo subdirectory, linked
# worktree, and this session's own agent worktree):
#
#     repo root        `.git`                          `.git`
#     repo subdirectory `/abs/path/.git`               `../.git`
#     linked worktree  `/abs/.git/worktrees/<name>`    `/abs/.git`
#
# The SUBDIRECTORY row is why the script resolves both paths before comparing
# them: textually different, the same directory. A string compare there calls
# every subdirectory of an ordinary clone a private worktree — wrong in the
# direction that DROPS the no-write instruction.
SHARED_GIT_DIRS = (0, ".git\n.git\n", "")
PRIVATE_COMMON_DIR = f"{FAKE_REPO_DIR}/.git"
PRIVATE_GIT_DIRS = (
    0, f"{PRIVATE_COMMON_DIR}/worktrees/agent-7f3a\n{PRIVATE_COMMON_DIR}\n", ""
)
UNREADABLE_GIT_DIRS = (128, "", "fatal: not a git repository")

CHECKLIST = (
    "**Audit for:**\n1. **Risks** — what breaks in production.\n"
    "9. **Second-order consequences** — ripple effects."
)


def fake_checklist(_repo_dir):
    return CHECKLIST


def make_runner(
    *,
    comments=(),
    pr=None,
    gh_rc=0,
    gh_stdout=None,
    origin=FAKE_ORIGIN,
    toplevel=FAKE_REPO_DIR,
    branch="feat/some-branch",
    status=" M scripts/a.py\n?? scripts/b.py\n",
    rev_list=None,
    numstat=(0, "10\t2\tscripts/foo.py\n3\t0\tscripts/tests/test_foo.py\n", ""),
    head_sha="deadbee",
    local_head=None,
    git_dirs=SHARED_GIT_DIRS,
):
    """A closed-world stand-in for `gh` and `git`. No process is spawned.

    `local_head` is what `git rev-parse HEAD` returns in the operator's
    checkout. It DEFAULTS to the PR's own `headRefOid`, i.e. the checkout is
    standing on the PR — the only state in which the delta half of a brief means
    what it says. Pass a different sha to model the shared checkout having moved
    (which it does, constantly), and `None` explicitly for a PR fixture with no
    `headRefOid`.

    🔴 `rev_list` USED TO BE A CONSTANT `(0, "4\\n", "")`, and that made the
    DEGENERATE self-range STRUCTURALLY INVISIBLE to this whole module: a fake
    `git rev-list` that answers "4 commits" whatever range it is asked about
    cannot model `<sha>..HEAD` where `<sha>` IS HEAD, so no test here could
    observe the state that shipped in every round-3 brief. The default is now a
    FUNCTION of the range spec — a self-range counts 0, anything else counts 4 —
    and every request is recorded on `runner.ranges` so a test can assert WHICH
    range was measured, not merely that something was.

    Pass a tuple to pin one canned answer (the old behaviour, still used by the
    read-rule rows), or a callable taking the range spec.
    """
    payload = dict(pr or DEFAULT_PR)
    payload["comments"] = [{"body": c} for c in comments]
    resolved_head = local_head if local_head is not None else payload.get("headRefOid")
    ranges = []

    def default_rev_list(spec):
        """0 commits for a self-range, 4 otherwise — what real `git` would say."""
        left, _, right = spec.partition("..")
        if right == "HEAD":
            right = resolved_head or ""
        if left and right and (right.startswith(left) or left.startswith(right)):
            return 0, "0\n", ""
        return 0, "4\n", ""

    def runner(cmd, cwd=None):
        if cmd[0] == "gh":
            if gh_rc:
                return gh_rc, "", "gh: synthetic failure"
            return 0, gh_stdout if gh_stdout is not None else json.dumps(payload), ""
        if cmd[:2] == ["git", "-C"]:
            verb = cmd[3]
            # 🔴 BEFORE the other `rev-parse` rows: this one is identified by
            # its FLAGS, and `cmd[-1] == "HEAD"` below would not match it but
            # a future re-ordering could. `git_dirs` defaults to the SHARED
            # answer, which is what every pre-existing test in this module
            # assumes and asserts.
            if verb == "rev-parse" and "--git-common-dir" in cmd:
                return git_dirs
            if verb == "rev-parse" and "--show-toplevel" in cmd:
                return 0, toplevel + "\n", ""
            if verb == "rev-parse" and "--abbrev-ref" in cmd:
                return 0, branch + "\n", ""
            if verb == "rev-parse" and "--short" in cmd:
                return 0, head_sha + "\n", ""
            if verb == "rev-parse" and cmd[-1] == "HEAD":
                return (0, resolved_head + "\n", "") if resolved_head else (
                    128, "", "fatal: ambiguous argument 'HEAD'"
                )
            if verb == "remote":
                return (0, origin + "\n", "") if origin else (1, "", "no origin")
            if verb == "status":
                return 0, status, ""
            if verb == "rev-list":
                spec = cmd[-1]
                ranges.append(spec)
                if callable(rev_list):
                    return rev_list(spec)
                if rev_list is not None:
                    return rev_list
                return default_rev_list(spec)
            if verb == "log":
                return numstat
        raise AssertionError(f"unexpected command in a hermetic test: {cmd}")

    runner.ranges = ranges
    return runner


def run_main(argv, **kw):
    """-> (rc, stdout, stderr). Always with an injected runner.

    `runner` is popped BEFORE the default is built: `kw.pop(k, default)`
    evaluates its default eagerly, so building `make_runner(**kw)` inline passed
    `runner=` straight into `make_runner` and raised.
    """
    out, err = io.StringIO(), io.StringIO()
    runner = kw.pop("runner", None) or make_runner(**kw)
    rc = ad.main(
        argv,
        runner=runner,
        stdout=out,
        stderr=err,
        checklist_reader=fake_checklist,
    )
    return rc, out.getvalue(), err.getvalue()


CLAIMS_BLOCK_R2 = (
    "```audit-claims round=2 audited=aaaa1111..bbbb2222\n"
    "1. the collapsed branch was split so each reports its own state\n"
    "2. the over-stated quantifier was replaced with the measured floor\n"
    "```"
)

# Prose that surrounds the block in a real PR comment. 🔴 This is the material a
# framed audit would go on to CONFIRM, and it must never reach the brief.
SURROUNDING_PROSE = (
    "Round 2 fixes are pushed. WHY THIS IS CORRECT: the earlier guard already "
    "rejects that input, so the second branch is unreachable and the split is "
    "safe. I re-ran the sweep and every row is green.\n\n"
    "{block}\n\n"
    "Happy to walk through the reasoning if useful."
)


def comment_with_prose():
    return SURROUNDING_PROSE.format(block=CLAIMS_BLOCK_R2)


# --------------------------------------------------------------------------- #
# THE TWO-WAY PIN
# --------------------------------------------------------------------------- #

def test_the_invariant_clause_ledger_is_pinned_two_way():
    """A clause with no ledger entry, or a ledger entry naming no clause, fails.

    🔴 Both directions, and the messages differ, because the fixes differ. A
    clause deleted from the script is the LOSS this whole script exists to
    prevent; a clause added without a ledger entry is an unreviewed instruction
    riding into every future brief.
    """
    in_script = {c.id for c in ad.INVARIANT_CLAUSES}
    in_ledger = set(CLAUSE_LEDGER)

    dropped = in_ledger - in_script
    assert not dropped, (
        "\n\nscripts/audit-dispatch.py: invariant clause(s) "
        f"{sorted(dropped)} are named in this module's ledger but no longer "
        "exist in INVARIANT_CLAUSES.\n"
        "  These are the clauses that were MEASURED to go missing when a brief "
        "was written from memory — one of them (`no-fetch`) first appeared at "
        "dispatch 9 of 14, and an auditor at dispatch 8 fetched in a repo the "
        "brief had called read-only.\n"
        "  If you removed one deliberately, delete its CLAUSE_LEDGER entry in "
        "the same commit and say in the message which dispatch failure that "
        "clause was closing."
    )
    added = in_script - in_ledger
    assert not added, (
        "\n\nscripts/audit-dispatch.py: invariant clause(s) "
        f"{sorted(added)} are emitted into every brief but are not in this "
        "module's ledger.\n"
        "  Add a CLAUSE_LEDGER entry naming the load-bearing phrase, so a "
        "later reword that guts the clause goes red here."
    )


def test_each_clause_carries_the_instruction_its_ledger_entry_names():
    """🔴 The reachability control for the ledger above — and the reword guard.

    The id pin passes for a clause whose text has been reworded into something
    weaker. This is the assertion that sees that: mutant R1 (reword `stop-rule`)
    and mutants W1-W5 (five rewords that INVERT the instruction) each kill THIS
    test alone, with every presence pin still green.

    🔴 WHOLE STRING, whitespace-normalised, not a fragment. With fragments,
    all five inversions passed a green 37-test suite — including one that told
    every future auditor "one confirming round after a clean one is prudent",
    the exact thing `claude/RULES.md` forbids.
    """
    by_id = {c.id: c.text for c in ad.INVARIANT_CLAUSES}
    for cid, pinned in CLAUSE_LEDGER.items():
        assert cid in by_id, f"clause {cid!r} is gone; see the two-way pin above"
        assert norm(pinned) == norm(by_id[cid]), (
            f"\n\nclause {cid!r} is not, word for word, what this module "
            f"pins.\n  pinned here :\n    {norm(pinned)!r}\n"
            f"  in the script:\n    {norm(by_id[cid])!r}\n"
            "  🔴 This is a WHOLE-STRING pin because the artifact is prose and "
            "a keyword guard is walkable by rewording (claude/RULES.md, "
            "spelled-guards). Whitespace is normalised, so a RE-WRAP is free "
            "and only a REWORD fails.\n"
            "  If the reword is deliberate, update CLAUSE_LEDGER in the SAME "
            "commit — which is the moment to notice you are rewriting an "
            "instruction rather than reformatting one. Five rewords that "
            "inverted their clause outright passed the fragment version of "
            "this pin, one of them telling auditors to run a confirming round "
            "after a clean one."
        )


@pytest.mark.parametrize("argv,kw", [
    (["900"], {}),
    (["900", "--round", "3"], {"comments": [CLAIMS_BLOCK_R2]}),
])
def test_every_clause_is_emitted_verbatim_in_both_kinds_of_brief(argv, kw):
    """POSITIVE CONTROL: a real assembled brief carries every clause.

    A zero-length or clause-free brief must fail loudly. Both round shapes are
    covered because they take different render paths, and a clause section
    wired into only one of them would otherwise look complete.
    """
    rc, out, err = run_main(argv, **kw)
    assert rc == 0, f"assembly failed: {err}"
    assert len(out) > 3_000, (
        f"the assembled brief is only {len(out)} chars — a brief that short is "
        "not a brief, and every 'clause is present' assertion below would be a "
        "claim about nothing."
    )
    for clause in ad.INVARIANT_CLAUSES:
        assert clause.text in out, (
            f"clause {clause.id!r} is in INVARIANT_CLAUSES but does NOT reach "
            f"the emitted brief for argv={argv}. The constant is not the "
            "artifact; the brief is."
        )


def test_the_rendered_section_holds_exactly_the_ledgered_clauses():
    """Two-way, against the RENDERED section rather than the constant.

    This is what mutant F1 catches: a renderer that stops reading
    INVARIANT_CLAUSES and emits a hardcoded copy of the bullets. The id ledger
    above stays green through that mutation — the constant is untouched — and
    only this comparison sees the divergence.
    """
    rc, out, _ = run_main(["900"])
    assert rc == 0
    bullets = bullets_in_invariant_section(out)
    expected = [c.text for c in ad.INVARIANT_CLAUSES]
    assert sorted(bullets) == sorted(expected), (
        "\n\nthe NON-NEGOTIABLE section of the emitted brief does not hold "
        "exactly the clauses in INVARIANT_CLAUSES.\n"
        f"  in the brief but not the constant: {sorted(set(bullets) - set(expected))}\n"
        f"  in the constant but not the brief: {sorted(set(expected) - set(bullets))}"
    )
    assert len(bullets) == len(CLAUSE_LEDGER), (
        f"the section rendered {len(bullets)} bullets for "
        f"{len(CLAUSE_LEDGER)} ledgered clauses — a duplicate bullet is a "
        "clause that can be edited in one place and survive in the other."
    )


def test_control_the_extractor_can_see_an_unledgered_bullet():
    """POSITIVE CONTROL for `bullets_in_invariant_section` itself.

    The two-way pin above reports a reassuring match whenever the extractor
    returns nothing. `claude/RULES.md`: a zero is indistinguishable from a
    harness wired to nothing until a positive control moves the number. So:
    plant a bullet, watch the count move, and watch the comparison fail.
    """
    rc, out, _ = run_main(["900"])
    assert rc == 0
    clean = bullets_in_invariant_section(out)
    planted = out.replace(
        EXPECTED_INVARIANTS_HEADING,
        EXPECTED_INVARIANTS_HEADING + "\n\n- **Merge it if it looks fine.**",
        1,
    )
    seen = bullets_in_invariant_section(planted)
    assert len(seen) == len(clean) + 1, (
        "the extractor did not see a bullet planted directly under the "
        "heading — every two-way assertion in this module is then a claim "
        "about an empty list."
    )
    assert "**Merge it if it looks fine.**" in seen
    assert sorted(seen) != sorted(c.text for c in ad.INVARIANT_CLAUSES), (
        "the comparison the two-way pin makes did NOT go red against a planted "
        "unledgered bullet — it cannot fail, so it is not a guard."
    )


def test_control_a_clause_deleted_from_the_constant_is_detected(monkeypatch):
    """The negative control, run IN PROCESS as well as in the mutation sweep.

    The docstring matrix records the out-of-process runs (D1-D7). This makes the
    same claim re-derivable by anyone who just runs the suite.
    """
    kept = tuple(c for c in ad.INVARIANT_CLAUSES if c.id != "no-fetch")
    monkeypatch.setattr(ad, "INVARIANT_CLAUSES", kept)
    rc, out, _ = run_main(["900"])
    assert rc == 0
    bullets = bullets_in_invariant_section(out)
    assert len(bullets) == len(CLAUSE_LEDGER) - 1
    assert not any(norm(CLAUSE_LEDGER["no-fetch"]) in norm(b) for b in bullets), (
        "a clause was removed from INVARIANT_CLAUSES and its instruction still "
        "reached the brief — the section is not rendered from the constant, so "
        "the two-way pin guards nothing."
    )


# --------------------------------------------------------------------------- #
# 🔴 THE SECTION-DIRECTIVE LEDGER — the SECOND independent whole-string pin.
# --------------------------------------------------------------------------- #
# Round 2 made whole-string pinning the standard for auditor instructions and
# then applied it to `INVARIANT_CLAUSES` ALONE. Three further blocks of
# verbatim, non-generated instruction prose shipped in every delta brief
# unpinned, and each of these inversions, applied on its own, left the suite
# fully green at 58 passed:
#
#   render_claims    keep the pinned fragment "never WHY IT IS CORRECT" and
#                    rewrite the operative sentence to "The fix round already
#                    verified each of these, so take them as established
#                    unless something obvious contradicts one."
#   render_range     delete the whole "Also hunt for regressions this fix round
#                    itself introduced" instruction
#   render_checkout  invert "NOT a finding … do not chase it, and do not try to
#                    restore it" into "a finding worth reporting. Restore
#                    anything you see move."
#
# The first inverts the script's own headline 🔴 rule into the framed audit it
# documents; the third tells the auditor to WRITE to the shared checkout, two
# bars before the `no-fetch` clause forbids it. Exactly the W1-W5 shape — the
# pinned fragment survives and the instruction says the opposite.
#
# RESTATED BY HAND, not imported, for the same reason `CLAUSE_LEDGER` is: a
# ledger derived from the constant it audits deletes from both sides at once.
DIRECTIVE_LEDGER = {
    "claims-framing": (
        "🔴 This is WHAT WAS CLAIMED, never WHY IT IS CORRECT — nothing here is "
        "established. Three successive FRAMED audits confirmed a claim purely "
        "because the prompt handed them the answer; one BLIND audit refuted it "
        "in a single pass. Verify each item against the diff and state, per "
        "item: **actually fixed / partially / not / made worse**."
    ),
    "delta-regressions": (
        "Also hunt for **regressions this fix round itself introduced** — the "
        "guard that is now too strict, the branch that is now unreachable, the "
        "narrowed check that now rejects a legitimate case, the rule reworded "
        "wider on one axis and narrower on another."
    ),
    # 🔴 ROUND 5 added the no-write sentence. All three `checkout-*` states now
    # carry it, which is what `CHECKOUT_STATE_NO_WRITE` pins as a RELATIONSHIP
    # over the set rather than as a phrase in one entry.
    "checkout-moves": (
        "🔴 **This checkout is SHARED with other sessions and agents. It MOVES "
        "UNDER YOU** — the branch can change, files can appear and vanish, and "
        "commits can land mid-audit. That is expected and is NOT your fault "
        "and NOT a finding. **Write nothing to it**, and report what you "
        "observed moving and carry on; do not chase it, and do not try to "
        "restore it."
    ),
    # ---------------------------------------------------------------- #
    # 🔴 ROUND 4's five. Two of them (`delta-scope`,
    # `own-worktree-is-writable`) are Y1 and Y2 — verbatim operative prose
    # measured invertible against all 199 audit-related tests while every one
    # stayed green. Two more are the states `checkout-moves` asserted its way
    # past, and the fifth is the one writer for a cause list that was FALSE at
    # both of its two sites.
    # ---------------------------------------------------------------- #
    "delta-scope": (
        "**Audit ONLY that range — do NOT re-audit the whole PR.** Everything "
        "below the range is work a previous round already dispositioned; "
        "re-reading it re-reports findings that were answered, buries the "
        "delta this round exists to examine, and makes the round's cost "
        "indistinguishable from a first full audit."
    ),
    # 🔴 ROUND 5 SCOPED THE FIRST CAUSE TO ROUND 1. From round 2 on, omitting
    # `--audited` is CORRECT — the anchor is recovered from the previous
    # block's `<to>` — so blaming the omission on a round-3 brief sent the
    # operator to hand-type a value, which is how a placeholder reached the
    # header. The re-emit instruction also stops quoting a phrase that is not a
    # sha, for the same reason.
    "degenerate-range-causes": (
        "Either the `audited=` block was written with the fix tip in its "
        "`<from>` position — which is what a bare round-1 `audited=<sha>` "
        "always means, and what `--emit-claims` records when `--audited` is "
        "omitted AT ROUND 1; from round 2 on, omitting it is correct, because "
        "`<from>` is then recovered from the previous block's `<to>`. "
        "`<from>` is the tip the PREVIOUS round AUDITED, so "
        "re-emit that block with `--audited <that round's actual tip sha>` "
        "and re-assemble — or nothing has been committed and pushed to "
        "the PR since that sha was recorded, in which case there is no delta "
        "to audit yet. 🔴 What is NOT a cause: the round's fix commits being "
        "absent from this checkout. This checkout was VERIFIED at assembly "
        "time to be the PR's head commit, and that verification is the very "
        "thing that makes this range DEGENERATE rather than merely "
        "unmeasurable — so fetching or checking out here could not help, and "
        "the `no-fetch` clause forbids it anyway."
    ),
    # 🔴 ROUND 5 REPLACED THE PREMISE, NOT THE SENTENCE. Round 4's version
    # ("a PRIVATE worktree — yours alone … Writing here is fine") granted write
    # permission over a tree that is NOT the auditor's, and inverted the
    # movement claim in the unsafe direction ("NOT expected here"). Both follow
    # from one false premise: `gather_worktree_kind` measures the ASSEMBLING
    # process's cwd, while the consumer is a different process that WHERE TO
    # WORK sends elsewhere. So this states only what the script knows — the
    # path is where the brief was built — and grants nothing.
    "checkout-private": (
        "🔴 **This is the checkout this brief was ASSEMBLED in — not "
        "necessarily the one you are standing in.** Git reports it as a "
        "PRIVATE linked worktree: its `.git` is a link into a shared "
        "repository, and the working tree belongs to the session that BUILT "
        "this brief, which is not you. That session is live in it, so files "
        "here can appear, vanish and change. **Write nothing to it** — the "
        "path is a fact about where the brief was built, never a tree you may "
        "work in. If you do see something move here, report it with what "
        "moved and when, and do not try to restore it."
    ),
    "checkout-unknown": (
        "🔴 **COULD NOT DETERMINE whether this checkout is shared** — the "
        "`git rev-parse --git-dir --git-common-dir` read that decides it did "
        "not answer. **Treat it as SHARED and write nothing to it.** But do "
        "NOT treat anything you see move as expected: that absolution belongs "
        "to a checkout KNOWN to be shared, and this one is not known to be "
        "anything. If something moves, report it AND report that its cause "
        "could not be established here."
    ),
    # 🔴 ROUND 6. The second sentence used to read "about the SHARED checkout",
    # which named a scoping the `no-fetch` clause stopped having in round 5 —
    # so a cross-repo brief assembled in a PRIVATE worktree told the reader the
    # rule was about a state the very next section denies. The scope is now
    # stated by OWNERSHIP, the way the clause itself states it.
    "own-worktree-is-writable": (
        "That worktree is YOURS: fetching and checking out inside it is fine. "
        "The no-write rule below is about every checkout you did not make."
    ),
}

# 🔴 WHICH BRIEF EACH DIRECTIVE SHIPS IN — a second, independent ledger, and it
# exists because the emitted-verbatim test used to assert that EVERY directive
# reaches a DELTA brief. That was true while all three shipped unconditionally;
# it is false the moment a directive is owned by a STATE (a private worktree, a
# cross-repo PR, a degenerate range), and the cheap fix — dropping the
# conditional ones from the check — would have retired the only test that
# proves a directive reaches an artifact at all.
#
# Each value names a scenario built by `brief_for_scenario` below. Pinned
# two-way against `DIRECTIVE_LEDGER`, so a directive with no scenario (an
# instruction nobody can be shown to receive) fails, and a scenario naming no
# directive fails.
DIRECTIVE_RENDERS_IN = {
    "claims-framing": "delta",
    "delta-scope": "delta",
    "delta-regressions": "delta",
    "degenerate-range-causes": "degenerate",
    "checkout-moves": "delta",
    "checkout-private": "private-worktree",
    "checkout-unknown": "unreadable-worktree",
    "own-worktree-is-writable": "cross-repo",
}


_CHECKOUT_HEADING = re.compile(r"^## THE (?:SHARED )?CHECKOUT\b.*$", re.M)


def checkout_section(brief):
    """The checkout block, sliced WITHOUT depending on the round-4 heading.

    🔴 Deliberately matches the OLD spelling (`## THE SHARED CHECKOUT`) as well
    as the new one. Slicing on the new heading alone made two round-4 tests
    fail at `e06461f7` with `ValueError: substring not found` — an error for
    want of a name the fix introduced, which `claude/RULES.md` says is not
    evidence of anything. With both spellings matched, the same tests fail
    there on the WRONG ANSWER instead, which is the claim they are making.
    """
    m = _CHECKOUT_HEADING.search(brief)
    assert m, f"no checkout section in the brief at all:\n{brief[:400]}"
    rest = brief[m.end():]
    nxt = rest.find("\n## ")
    return brief[m.start():m.end() + (nxt if nxt != -1 else len(rest))]


def range_section(brief):
    """THE RANGE block alone, sliced off the next `## ` heading.

    🔴 Exists because `"<sha>..HEAD" in out` is not a claim about THE RANGE:
    THE LEDGER prints its own provenance line (`git log --numstat …
    <anchor>..HEAD --not <base>`) carrying the same substring, and that one
    legitimately keeps `..HEAD` because the command ran in the tree that was
    just verified. An unscoped assertion is satisfied by the wrong section.
    """
    start = brief.index("## THE RANGE")
    rest = brief[start + len("## THE RANGE"):]
    nxt = rest.find("\n## ")
    return brief[start:start + len("## THE RANGE") + (nxt if nxt != -1 else len(rest))]


# 🔴 THE SCENARIOS AS DATA, so "every scenario this module knows" is something
# a test can ITERATE rather than something an author has to remember to list.
#
# Round 6's finding: two guards that meant "every state / every brief" were
# spelled as a hand-written pair and as an id PREFIX, and both were walkable —
# re-introducing a forbidden phrase in the CROSS-REPO branch, and renaming a
# `checkout-*` directive, each left the suite fully green. A scenario added
# here is now automatically driven by every guard that iterates `SCENARIOS`.
#
# 🔴 `cross-repo-private` is not a hypothetical: it is the configuration that
# produced round 6's 🟡 F1. `own-worktree-is-writable` renders ONLY in the
# cross-repo branch, and the tree the brief is assembled in is routinely a
# private per-agent worktree — so this is the pair whose two sections were
# read together and found to contradict each other. Neither single-axis
# scenario can produce it.
# 🔴 THUNKS, not literals: `SELF_RANGE_PR` is defined with round 3's section
# eight hundred lines below, where its 40-char shape is explained. A literal
# dict here would resolve it at import and fail collection — and moving the
# fixture up to satisfy this table would strand its reasoning.
SCENARIO_RUNS = {
    "delta": lambda: (["900", "--round", "3"], {"comments": [CLAIMS_BLOCK_R2]}),
    "degenerate": lambda: (["900", "--round", "3"],
                           {"comments": [CLAIMS_BLOCK_R2], "pr": SELF_RANGE_PR}),
    "private-worktree": lambda: (["900"], {"git_dirs": PRIVATE_GIT_DIRS}),
    "unreadable-worktree": lambda: (["900"], {"git_dirs": UNREADABLE_GIT_DIRS}),
    "cross-repo": lambda: (["900"], {"pr": OTHER_REPO_PR}),
    "cross-repo-private": lambda: (["900"], {"pr": OTHER_REPO_PR,
                                             "git_dirs": PRIVATE_GIT_DIRS}),
}
SCENARIOS = tuple(SCENARIO_RUNS)


def brief_for_scenario(name):
    """-> the stdout of a run that MUST carry the directive(s) mapped to it."""
    try:
        build = SCENARIO_RUNS[name]
    except KeyError:
        raise AssertionError(f"no such scenario: {name!r}") from None
    argv, kw = build()
    rc, out, err = run_main(argv, **kw)
    assert rc == 0, f"scenario {name!r} exited {rc}: {err}"
    assert len(out) > 3_000, (
        f"scenario {name!r} produced only {len(out)} chars — a presence "
        "assertion over it would be a claim about nothing"
    )
    return out


def test_the_section_directive_ledger_is_pinned_two_way():
    """A directive with no ledger entry, or an entry naming none, fails.

    🔴 INVARIANT GUARD. Its evidence is mutants X1-X3 and XA, not a base ref:
    `SECTION_DIRECTIVES` does not exist at `d9eb36a8`, so "red at the base"
    here would only restate that the fix added a constant.
    """
    in_script = {d.id for d in ad.SECTION_DIRECTIVES}
    in_ledger = set(DIRECTIVE_LEDGER)
    dropped = in_ledger - in_script
    assert not dropped, (
        f"\n\nsection directive(s) {sorted(dropped)} are ledgered here but no "
        "longer exist in SECTION_DIRECTIVES. Each is verbatim auditor "
        "instruction prose that was MEASURED to be invertible while the suite "
        "stayed green; deleting one needs its ledger entry deleted in the same "
        "commit, with the message saying which instruction the brief now lacks."
    )
    added = in_script - in_ledger
    assert not added, (
        f"\n\nsection directive(s) {sorted(added)} ship in the brief with no "
        "ledger entry here — an unreviewed instruction riding into every "
        "future dispatch. Add the WHOLE text to DIRECTIVE_LEDGER."
    )


def test_each_section_directive_carries_the_instruction_its_ledger_entry_names():
    """WHOLE string, whitespace-normalised — the guard the three inversions beat.

    🔴 INVARIANT GUARD; mutants X1 (claims-framing inverted) and X3
    (checkout-moves inverted) each kill this test ALONE, with every presence
    pin still green. XS (a pure re-space) must SURVIVE it.
    """
    by_id = {d.id: d.text for d in ad.SECTION_DIRECTIVES}
    for did, pinned in DIRECTIVE_LEDGER.items():
        assert did in by_id, f"directive {did!r} is gone; see the two-way pin"
        assert norm(pinned) == norm(by_id[did]), (
            f"\n\ndirective {did!r} is not, word for word, what this module "
            f"pins.\n  pinned here :\n    {norm(pinned)!r}\n"
            f"  in the script:\n    {norm(by_id[did])!r}\n"
            "  🔴 A fragment guard on prose is walkable by rewording "
            "(claude/RULES.md, spelled-guards), and these three were walked: "
            "each inversion above passed a fully green 58-test suite. "
            "Whitespace is normalised, so a RE-WRAP is free and only a REWORD "
            "fails. If the reword is deliberate, update DIRECTIVE_LEDGER in "
            "the SAME commit — that is the moment to notice you are rewriting "
            "an instruction rather than reformatting one."
        )


def test_the_directive_render_scenario_ledger_is_pinned_two_way():
    """Every directive names a SCENARIO, and every scenario names a directive.

    🔴 THE SENTENCE ABOVE IS EXACTLY AS WIDE AS THE CODE BELOW, and its first
    draft was not: it read "every directive names a brief THAT CARRIES IT",
    which claims a rendering check this test does not make. Whether the
    scenario's brief actually carries the directive is
    `..._emitted_verbatim_in_the_brief_that_owns_it`, next door. Splitting them
    is deliberate — one owns the LEDGER, one owns the RENDER — and describing
    this one as if it did both is the "guard whose description is wider than
    its implementation" failure `claude/RULES.md` names, which is also 🟡3 of
    the round this test was written in.

    🔴 INVARIANT GUARD; its evidence is mutant XA (a directive added with no
    scenario) and X2 (one deleted). Without it a directive could be added to
    `SECTION_DIRECTIVES`, ledgered whole, and be scoped to nothing at all —
    after which the render check has no scenario to look in and passes
    vacuously.
    """
    # 🔴 Against the SCRIPT's ids, not only against the sibling ledger. Two
    # test-module constants compared with each other are unreachable by the
    # mutation battery — a guard with no possible evidence — and the claim that
    # matters is about the directives that SHIP, not about this file's
    # bookkeeping. Mutant XA (add an unledgered directive) is what proves it
    # executes.
    in_script = {d.id for d in ad.SECTION_DIRECTIVES}
    scoped = set(DIRECTIVE_RENDERS_IN)
    assert in_script == scoped, (
        "\n\n`SECTION_DIRECTIVES` and `DIRECTIVE_RENDERS_IN` disagree.\n"
        f"  ships but never shown to render: {sorted(in_script - scoped)}\n"
        f"  scoped but not in the script   : {sorted(scoped - in_script)}\n"
        "  A directive with no scenario is an instruction nobody can be shown "
        "to receive; a scenario with no directive asserts nothing."
    )
    assert set(DIRECTIVE_LEDGER) == scoped, (
        "\n\n`DIRECTIVE_LEDGER` and `DIRECTIVE_RENDERS_IN` disagree: "
        f"{sorted(set(DIRECTIVE_LEDGER) ^ scoped)}"
    )


def test_every_section_directive_is_emitted_verbatim_in_the_brief_that_owns_it():
    """POSITIVE CONTROL: the constant is not the artifact, the brief is.

    🔴 THIS USED TO ASSERT THAT EVERY DIRECTIVE REACHES A DELTA BRIEF, which
    was true only while all three shipped unconditionally. Five of the eight
    are now owned by a STATE — a degenerate range, a private worktree, an
    unreadable one, a cross-repo PR — so the delta brief carries three of them
    and the old assertion would have to be weakened to nothing. It is driven by
    `DIRECTIVE_RENDERS_IN` instead: each directive is looked for in a brief
    built for ITS state, and the two-way pin above stops a directive from
    quietly having no state at all.
    """
    # 🔴 Compared against the SCRIPT's own text, not against `DIRECTIVE_LEDGER`.
    # This test owns RENDERING; text drift is owned by
    # `..._carries_the_instruction_its_ledger_entry_names`. Reading the ledger
    # here would make every reword kill both, and a row that dies to two guards
    # cannot show which one is alive — mutants X1 and X3 exist precisely to
    # prove the whole-string ledger fires ALONE.
    by_scenario = {}
    for did, scenario in DIRECTIVE_RENDERS_IN.items():
        by_scenario.setdefault(scenario, []).append(did)
    for scenario, dids in sorted(by_scenario.items()):
        out = brief_for_scenario(scenario)
        for did in sorted(dids):
            assert did in ad.DIRECTIVE, f"directive {did!r} is gone"
            assert ad.DIRECTIVE[did] in out, (
                f"\n\ndirective {did!r} is scoped to the {scenario!r} brief "
                "but does NOT reach it — the section that owns it stopped "
                "rendering it, so the instruction ships to nobody."
            )
    # The shared-checkout warning is not delta-specific: a first audit stands
    # in the same tree and it moves exactly as much.
    assert ad.DIRECTIVE["checkout-moves"] in run_main(["900"])[1], (
        "the shared-checkout warning is missing from a ROUND-1 brief"
    )


def test_control_a_directive_deleted_from_the_constant_is_detected(monkeypatch):
    """NEGATIVE CONTROL, in process: a deletion must be visible in the BRIEF.

    Deleting a directive renders a loud placeholder rather than raising, so
    that a deletion cannot take every test in the module down with it and score
    as "killed" for the wrong reason. This proves the placeholder path is real
    and that the instruction genuinely leaves the brief.
    """
    kept = tuple(d for d in ad.SECTION_DIRECTIVES if d.id != "delta-regressions")
    monkeypatch.setattr(ad, "SECTION_DIRECTIVES", kept)
    monkeypatch.setattr(ad, "DIRECTIVE", {d.id: d.text for d in kept})
    rc, out, err = run_main(["900", "--round", "3"], comments=[CLAIMS_BLOCK_R2])
    assert rc == 0, err
    assert norm(DIRECTIVE_LEDGER["delta-regressions"]) not in norm(out), (
        "a directive was removed from the constant and its instruction still "
        "reached the brief — the section is not rendered from the constant, so "
        "the two-way pin guards nothing"
    )
    assert "MISSING VERBATIM BLOCK `delta-regressions`" in out, (
        "the deletion is SILENT in the brief. A missing instruction that "
        "leaves no mark is exactly the loss this ledger exists to prevent."
    )


# --------------------------------------------------------------------------- #
# 🔴 REFUSAL 1 — a delta round with nothing to be framed on
# --------------------------------------------------------------------------- #

def test_the_delta_refusal_exits_non_zero_and_emits_no_brief():
    rc, out, err = run_main(["900", "--round", "3"], comments=[])
    assert rc != 0, (
        "a round-3 brief was emitted with NO claims block. An empty 'what was "
        "claimed fixed' section silently turns a delta re-audit into a blind "
        "full audit — a different thing, which then reads as covered."
    )
    assert out.strip() == "", (
        "the refusal still printed something on stdout; an operator pasting "
        "stdout would dispatch a brief the script refused to vouch for."
    )


def test_the_refusal_names_what_it_looked_for_and_where():
    """Assert on the MESSAGE, not just the exit code.

    A refusal an operator cannot act on gets worked around, and the workaround
    is 'run it without --round', which is exactly the silent downgrade the
    refusal exists to prevent.
    """
    _, _, err = run_main(["900", "--round", "3"], comments=[])
    assert ad.REFUSAL_HEADER in err
    assert "audit-claims round=<n> audited=<sha>..<sha>" in err, (
        "the refusal does not show the block shape it looked for"
    )
    assert "PR #900's comments" in err, "the refusal does not say where it looked"
    assert "no `audit-claims` block" in err, "the refusal does not say what it found"
    assert "--emit-claims" in err, (
        "the refusal does not tell the operator how to produce the block it "
        "wants — a refusal with no exit is a refusal people route around"
    )


@pytest.mark.parametrize("body,expected", [
    ("```audit-claims round=3\n1. a thing\n```", "does not carry both"),
    ("```audit-claims audited=aa..bb\n1. a thing\n```", "does not carry both"),
    ("```audit-claims round=3 audited=aa..bb\nfixed some stuff\n```",
     "no numbered claim lines"),
])
def test_the_refusal_fires_for_a_malformed_block_and_says_which_way(body, expected):
    """A near-miss must be reported as a near-miss.

    Silently skipping an unparseable fence produces the SAME observable as 'no
    block at all' — and those need opposite fixes (repair the header vs write
    the block). `claude/RULES.md`: an empty result cannot distinguish two
    mechanisms.
    """
    rc, out, err = run_main(["900", "--round", "3"], comments=[body])
    assert rc != 0 and out.strip() == ""
    assert expected in err, (
        f"the refusal message does not name the malformation. Got:\n{err}"
    )


def test_a_first_round_needs_no_claims_block():
    """The refusal must not fire where a blind full audit is the CORRECT thing."""
    rc, out, err = run_main(["900"], comments=[])
    assert rc == 0, err
    assert "FIRST, FULL adversarial audit" in out
    assert "WHAT WAS CLAIMED FIXED" not in out, (
        "a first audit must not carry a claims section at all — an empty one "
        "reads as 'nothing was claimed', which is a different statement"
    )


# --------------------------------------------------------------------------- #
# 🔴 THE FRAMING GUARANTEE
# --------------------------------------------------------------------------- #

def test_the_brief_carries_the_claims_and_not_the_reasoning_around_them():
    """🔴 The rule that keeps a delta round from verifying its own frame.

    The skill records three successive FRAMED audits confirming a claim purely
    because the prompt handed them the answer, against one BLIND audit that
    refuted it in a single pass. A PR comment holds BOTH the claims and the
    argument for them, so the assembler reads only the fenced block.

    Mutant C2 (read the whole comment body instead of the fence) kills this test
    alone.
    """
    rc, out, err = run_main(
        ["900", "--round", "3"], comments=[comment_with_prose()]
    )
    assert rc == 0, err

    assert "the collapsed branch was split so each reports its own state" in out
    assert "the over-stated quantifier was replaced with the measured floor" in out

    for leaked in (
        "WHY THIS IS CORRECT",
        "so the second branch is unreachable and the split is safe",
        "I re-ran the sweep and every row is green",
        "Happy to walk through the reasoning if useful",
    ):
        assert leaked not in out, (
            f"the brief reproduced reasoning from the PR comment: {leaked!r}.\n"
            "  That is the material a framed audit goes on to CONFIRM. Only "
            "the fenced block's numbered lines may reach the brief."
        )
    assert "never WHY IT IS CORRECT" in out, (
        "the claims section does not tell the auditor that these are claims"
    )


def test_the_newest_claims_block_wins():
    older = (
        "```audit-claims round=2 audited=aaaa1111..bbbb2222\n"
        "1. an older claim\n```"
    )
    newer = (
        "```audit-claims round=4 audited=bbbb2222..cccc3333\n"
        "1. a newer claim\n```"
    )
    rc, out, err = run_main(["900", "--round", "5"], comments=[older, newer])
    assert rc == 0, err
    assert "a newer claim" in out
    assert "an older claim" not in out
    # 🔴 The NEWEST BLOCK's `<from>`, not the oldest block's and not that
    # block's `<to>`. `<to>` (`cccc3333`) is the head round 4's fixes produced
    # and is where the block was POSTED, so anchoring there is the self-range
    # defect round 3 fixed — see `range_anchor`.
    #
    # 🔴 SCOPED TO THE RANGE SECTION IN ROUND 7, and the old scope was
    # VACUOUS-ADJACENT: `"bbbb2222..HEAD" in out` also matched THE LEDGER's
    # provenance line (`git log --numstat … bbbb2222..HEAD --not main`), which
    # legitimately keeps `..HEAD` because that command ran HERE. So both
    # assertions could be satisfied by a section this test is not about, and
    # when round 7 changed the rendered tip they did not move.
    the_range = range_section(out)
    assert f"bbbb2222..{FAKE_HEAD_OID}" in the_range, (
        "the range must be anchored at the newest BLOCK's `<from>` — the tip "
        "that round's audit read — not at the oldest block, and not at the "
        f"newest block's `<to>`, which is the head it was posted at\n"
        f"{the_range}"
    )
    assert f"cccc3333..{FAKE_HEAD_OID}" not in the_range, (
        "the range is anchored at the newest block's `<to>`, which is the head "
        "the block was posted at: that range is EMPTY BY CONSTRUCTION whenever "
        "the block sits at the fix tip, which is the normal case"
    )


@pytest.mark.parametrize("argv,kw,want_from,want_round", [
    # The shape the round-trip test used to cover ALONE.
    (["900", "--round", "3", "--emit-claims"], {"comments": [CLAIMS_BLOCK_R2]},
     "bbbb2222", 3),
    # 🔴 REGRESSION, red at `abc41024`. `--round 1 --emit-claims` is the exact
    # command the delta refusal names as the fix, and with no prior block
    # `prev_sha` was None, so the PLACEHOLDER `<the sha round 1 audited>` was
    # interpolated into the header. `_HEADER_AUDITED` then captured `<the`,
    # found no `..`, and produced `audited_from=''`, `audited_to='<the'` — and
    # the next round's brief said ``Diff `<the..HEAD` `` with rc 0 and no
    # refusal at all. The parser already accepts a bare sha; round 1 emits it.
    (["900", "--emit-claims"], {"comments": []}, "", 1),
    (["900", "--round", "1", "--emit-claims"], {"comments": []}, "", 1),
])
def test_emit_claims_prints_a_block_this_scripts_own_parser_accepts(
    argv, kw, want_from, want_round
):
    """A round trip, so the skeleton cannot drift away from the reader.

    The next round REFUSES on an unparseable block, so a skeleton the parser
    rejects would turn `--emit-claims` into a trap — and it was one for the
    round-1 shape, which is the one the refusal tells people to run.
    """
    rc, out, err = run_main(argv, **kw)
    assert rc == 0, err
    tail = out[out.index("```audit-claims"):]
    blocks, malformed = ad.parse_claims_blocks([tail])
    assert not malformed, (
        f"the block this script EMITS is one its own parser rejects: "
        f"{malformed}\n{tail}"
    )
    assert len(blocks) == 1
    assert blocks[0].round_no == want_round
    assert blocks[0].audited_from == want_from
    assert blocks[0].audited_to == FAKE_HEAD_OID[:8], (
        f"the audited TIP parsed as {blocks[0].audited_to!r}. A placeholder "
        "that survives into the header is worse than a refusal: it parses, so "
        "the next round anchors a range on it and reports rc 0."
    )
    assert "<the" not in tail, (
        "a placeholder reached the emitted header; the next round reads this "
        "field literally"
    )
    assert len(blocks[0].items) >= 1


def test_a_round_one_block_anchors_the_next_rounds_cumulative_figure():
    """The other half of the round-1 emission: the bare sha IS the anchor.

    `--emit-claims` at round 1 writes `audited=<sha>` with no `..`, and that sha
    is exactly the quantity `round_one_anchor` needs. Reading it keeps the
    remedy chain the refusal advertises from dead-ending in a permanently
    NOT MEASURED cumulative.
    """
    r1 = ad.ClaimsBlock(1, "", "aaaa1111", ["a claim"])
    later = ad.ClaimsBlock(3, "bbbb2222", "cccc3333", ["another"])
    assert ad.round_one_anchor([r1, later]) == "aaaa1111"
    # A bare sha on a LATER round is NOT an anchor — it says what that round
    # audited, and round 1's tip is then genuinely unknown.
    assert ad.round_one_anchor([ad.ClaimsBlock(2, "", "bbbb2222", ["x"])]) is None


# --------------------------------------------------------------------------- #
# 🔴 THE CROSS-REPO BRANCH — both directions
# --------------------------------------------------------------------------- #

def _isolation_lines(brief):
    return [ln for ln in brief.splitlines() if 'isolation: "worktree"' in ln]


def test_cross_repo_tells_the_agent_to_worktree_the_prs_repo_itself():
    """🔴 The highest-value generated field.

    `isolation: "worktree"` builds from the CWD's repo. For a PR in a different
    repo that is the WRONG tree and the failure is quiet — the agent either
    reports a briefed file missing, or silently audits the wrong repository.
    """
    rc, out, err = run_main(["900"], pr=OTHER_REPO_PR)
    assert rc == 0, err

    assert "git -C" in out and "worktree add" in out, (
        "the cross-repo brief does not tell the agent to create the worktree "
        "itself"
    )
    assert "someone-else/otherproj" in out, (
        "the cross-repo brief does not name the repo the worktree must come from"
    )
    lines = _isolation_lines(out)
    assert lines, "the brief says nothing about the isolation flag at all"
    for line in lines:
        assert "Do NOT" in line, (
            "\n\nthe cross-repo brief mentions `isolation: \"worktree\"` on a "
            f"line that does not forbid it:\n    {line}\n"
            "  That flag worktrees the CWD's repo, which is the wrong one here."
        )
    assert ad.ISOLATION_RECOMMEND not in out


def test_same_repo_recommends_the_isolation_flag_and_does_not_hand_roll():
    """The reverse direction — measured, not assumed to follow from the first."""
    rc, out, err = run_main(["900"])  # DEFAULT_PR is example-org/devrc, == origin
    assert rc == 0, err
    assert ad.ISOLATION_RECOMMEND in out
    lines = _isolation_lines(out)
    assert lines and not any("Do NOT" in ln for ln in lines), (
        "the same-repo brief forbids the flag that is correct here"
    )
    where = out[out.index("## WHERE TO WORK"):out.index("## THE RANGE")]
    assert "worktree add" not in where, (
        "the same-repo brief tells the agent to hand-roll a worktree; that is "
        "the cross-repo remedy and it is noise here"
    )


def test_the_cross_repo_decision_comes_from_the_repos_not_from_prose():
    """The same run, differing only in the PR's repo, must flip the section."""
    same = run_main(["900"])[1]
    other = run_main(["900"], pr=OTHER_REPO_PR)[1]
    assert (ad.ISOLATION_FORBID in other) and (ad.ISOLATION_FORBID not in same)
    assert (ad.ISOLATION_RECOMMEND in same) and (ad.ISOLATION_RECOMMEND not in other)


def test_a_fork_pr_against_this_repo_is_not_treated_as_cross_repo():
    """🔴 REGRESSION. Red at `abc41024`, where it emitted the CROSS-REPO brief.

    `pr_slug` read `headRepositoryOwner`/`headRepository` — the repo the head
    BRANCH lives in, which for a fork PR is the contributor's fork. Verified
    against real `gh` output for a fork PR against `cli/cli`: head reports
    `ylfeng250/cli` while the PR lives in `cli/cli` and `isCrossRepository` is
    true. So a fork PR opened against THIS repo computed
    `repo=some-contributor/devrc != cwd_slug` and emitted the cross-repo branch:
    "the PR lives in `some-contributor/devrc` … Do NOT use `isolation:
    \"worktree\"` … `git -C <your local clone of some-contributor/devrc>
    worktree add`". Every part of that is wrong, `gh pr diff` in the fork fails,
    and no such clone exists.

    🔴 All three cross-repo tests and mutant C3 passed through this bug because
    `DEFAULT_PR` encoded the same wrong model. The fixture is corrected; this is
    the case that could not have passed under it.
    """
    rc, out, err = run_main(["900"], pr=FORK_PR)
    assert rc == 0, err

    assert ad.ISOLATION_RECOMMEND in out, (
        "\n\na fork PR opened AGAINST this repo got the cross-repo brief. The "
        "PR lives in the repo this session is standing in; the worktree flag "
        "is correct here and hand-rolling one against a clone of the fork is "
        "not."
    )
    assert ad.ISOLATION_FORBID not in out
    assert "some-contributor/devrc" not in out, (
        "\n\nthe brief names the CONTRIBUTOR'S FORK as the repo to work in. "
        "That is the head repo, not the PR's repo — `gh pr diff` there fails "
        "and the local clone the brief tells the agent to use does not exist."
    )
    assert "example-org/devrc" in out


def test_the_prs_repo_is_read_from_the_url_not_from_the_head_repo():
    """The unit-level statement of the same fact, driven through `pr_slug`.

    Kept beside the brief-level test because the two fail differently: this one
    says WHICH field was misread, the one above says what the operator sees.
    """
    assert ad.pr_slug(FORK_PR) == "example-org/devrc"
    assert ad.pr_slug(DEFAULT_PR) == "example-org/devrc"
    assert ad.pr_slug(OTHER_REPO_PR) == "someone-else/otherproj"
    # `--repo` still wins over everything: it is the operator stating it.
    assert ad.pr_slug(FORK_PR, "stated/outright") == "stated/outright"
    # No url, but `isCrossRepository` false ⇒ the head fields ARE the PR's repo.
    assert ad.pr_slug({
        "url": "", "isCrossRepository": False,
        "headRepository": {"name": "devrc"},
        "headRepositoryOwner": {"login": "example-org"},
    }) == "example-org/devrc"
    # No url and cross-repository ⇒ the head fields are the FORK. Answering
    # from them would be the bug above, so this returns None and the brief
    # renders its COULD NOT DETERMINE branch.
    assert ad.pr_slug({
        "url": "", "isCrossRepository": True,
        "headRepository": {"name": "devrc"},
        "headRepositoryOwner": {"login": "some-contributor"},
    }) is None


def test_an_undeterminable_repo_gets_its_own_branch_not_the_same_repo_one():
    """🔴 REGRESSION. Red at `abc41024`, where it emitted the SAME-REPO brief.

    `cross_repo` was `bool(cwd_slug and repo and cwd_slug != repo)`, so a falsy
    `cwd_slug` — no `origin` remote — made the three-state decision evaluate
    FALSE and silently become "same repo", which recommends the flag whose
    failure is silent. The measured output contradicted itself in one
    paragraph: "The PR lives in `<org>/<other-repo>`, which is this session's
    own repository (`/home/…/devrc`)" followed by the isolation directive.
    """
    rc, out, err = run_main(["900"], pr=OTHER_REPO_PR, origin=None)
    assert rc == 0, err

    assert "COULD NOT DETERMINE" in out, (
        "\n\nwith no `origin` remote the script still claimed to know which "
        "repository the PR is in. Three states, not two: not knowing is its "
        "own answer."
    )
    assert ad.ISOLATION_RECOMMEND not in out, (
        "\n\nthe brief RECOMMENDED `isolation: \"worktree\"` for a PR whose "
        "repository it could not determine. That flag worktrees the cwd's "
        "repo; recommending it is only safe once the repos are known to match."
    )
    assert ad.ISOLATION_FORBID in out, (
        "the undetermined branch must default to NOT using the flag"
    )
    assert "which is this session's own repository" not in out, (
        "\n\nthe brief asserted the PR is in this session's repository — the "
        "self-contradicting claim this branch exists to stop"
    )
    assert "--repo owner/name" in out, (
        "the branch does not tell the operator how to answer the question"
    )


# --------------------------------------------------------------------------- #
# The generated facts
# --------------------------------------------------------------------------- #

def test_the_shared_checkout_state_is_reported_with_the_it_moves_warning():
    rc, out, err = run_main(["900"], branch="feat/thing", status=" M a\n M b\n M c\n")
    assert rc == 0, err
    assert "feat/thing" in out
    assert "3 uncommitted path(s)" in out
    assert "MOVES UNDER YOU" in out
    assert "NOT a finding" in out


def test_the_toolchain_section_names_the_tier_the_merge_gates_on():
    """Two of the three clauses the measurement showed decaying live here.

    They are GENERATED (they interpolate the repo path), so they are not in
    INVARIANT_CLAUSES — which means nothing else in this module would notice
    them going missing.
    """
    rc, out, err = run_main(["900"])
    assert rc == 0, err
    assert "nix develop /fake/checkout/devrc -c python3 -m pytest" in out
    assert "-p no:cacheprovider" in out
    assert "No module named pytest" in out and "WRONG SHELL" in out, (
        "the wrong-shell diagnosis is missing — it was present in 9 of the "
        "first 9 measured dispatches and absent from 3 of the last 5"
    )
    assert "nix build <your worktree>#checks.x86_64-linux.pytests" in out
    assert "nix build <your worktree>#checks.x86_64-linux.nodetests" in out
    assert "gate.sh --tier both" in out
    assert "git --version" in out


def test_the_toolchain_gates_the_auditors_copy_not_the_shared_checkout():
    """🔴 REGRESSION. Red at `abc41024`, where every command named the shared
    checkout.

    `r = facts.cwd_repo_dir` was interpolated into all four commands, so the
    brief said `nix develop <shared> -c bash <shared>/scripts/gate.sh` and
    `nix build <shared>#checks…`. `gate.sh` resolves its `ROOT` from its own
    `BASH_SOURCE`, so that runs the suite IN the shared checkout on whatever
    branch it is standing on, and the `nix build` builds that ref's tree —
    while WHERE TO WORK three bars earlier told the agent to work elsewhere.
    The auditor then obeys the next sentence, "name the tier and the base sha",
    and names the wrong sha.

    `nix develop {r}` is the ONE allowed use: it resolves a dev shell, not a
    tree under test.
    """
    rc, out, err = run_main(["900"])
    assert rc == 0, err

    toolchain = out[out.index("## TOOLCHAIN"):out.index("## 🔴 NON-NEGOTIABLE")]
    # 🔴 The COMMANDS, not the prose around them. The prose necessarily names
    # both the shared path and the two commands in order to explain the rule,
    # and a check that cannot tell an instruction from its rationale would go
    # red on the fix that closes the finding.
    commands, inside = [], False
    for line in toolchain.splitlines():
        if line.startswith("```"):
            inside = not inside
            continue
        if inside and line.strip():
            commands.append(line)
    assert commands, "no command block found in the toolchain section at all"

    for line in commands:
        if "gate.sh" in line or "nix build" in line:
            assert FAKE_REPO_DIR not in line.replace(
                f"nix develop {FAKE_REPO_DIR}", ""
            ), (
                "\n\nthe toolchain section tells the auditor to run the GATE "
                f"or a `nix build` against the shared checkout:\n    {line}\n"
                "  gate.sh resolves its root from its own path, so that runs "
                "the suite in the shared checkout on whatever branch it is "
                "standing on — not on the tree under audit. Only `nix develop "
                "<repo>` may name it, and only to resolve the dev shell."
            )
    assert "<your worktree>/scripts/gate.sh" in out
    assert "resolves its root from its own path" in out, (
        "the section does not say WHY the shared path is wrong there, so the "
        "next editor puts it back"
    )


def test_the_range_is_generated_from_the_previous_rounds_audited_sha():
    """🔴 REGRESSION. Red at `d9eb36a8`, which anchored on `<to>`.

    `audited=<from>..<to>` records that the round's fix took the tree from
    `<from>` — the tip that round's AUDIT read — to `<to>`, the head its fixes
    produced. A delta round therefore reads everything since the
    previously-audited tip, which is `<from>`.

    Anchoring on `<to>` made the range EMPTY BY CONSTRUCTION, because
    `--emit-claims` stamps the PR's CURRENT head into that field: the block is
    posted at the fix tip, so `<to>` IS HEAD. Reproduced live on devrc #958 —
    the round-2 comment carried `audited=abc41024..d9eb36a8` and `--round 3`
    rendered ``Diff `d9eb36a8..HEAD` `` with HEAD being `d9eb36a8`.
    """
    rc, out, err = run_main(["900", "--round", "3"], comments=[CLAIMS_BLOCK_R2])
    assert rc == 0, err
    # 🔴 The TIP moved in round 7 (a verified head renders as its SHA, never as
    # `HEAD` — see `render_range`), so the range reads `aaaa1111..<pr head>`.
    # The claim here is unchanged and is about the ANCHOR half.
    the_range = range_section(out)
    assert f"`aaaa1111..{FAKE_HEAD_OID}`" in the_range, (
        "\n\nthe delta range is not anchored at the tip the previous round "
        "AUDITED (`<from>`). The fixture's block is "
        f"`audited=aaaa1111..bbbb2222`.\n{the_range}"
    )
    assert f"`bbbb2222..{FAKE_HEAD_OID}`" not in the_range, (
        "\n\nthe range is anchored at `<to>` — the head the block was posted "
        "at. Whenever the block sits at the fix tip (the normal case) that "
        "range is empty, and THE RANGE section prints it with no warning."
    )


# --------------------------------------------------------------------------- #
# 🔴 THE FOURTH READ RULE — is this checkout even standing on the PR?
# --------------------------------------------------------------------------- #

WRONG_HEAD = "9999999988888888777777776666666655555555"


def test_the_ledger_refuses_to_measure_a_checkout_that_is_not_the_pr():
    """🔴 REGRESSION. Red at `abc41024`, which printed a confident ledger.

    `measure_ledger` was handed the operator's checkout and hard-coded `HEAD`,
    and nothing checked HEAD was the PR's head. Reproduced from a clone standing
    on an unrelated feature branch against real PR #958: rc 0, silent stderr, a
    non-empty range — all three advertised read rules satisfied — and a file
    list belonging to that branch. Standing on `main` produced the banner "🔴
    Zero changed lines over a NON-EMPTY range. That is a real measurement, not a
    failure."

    A measurement of the wrong tree is not a measurement, so it earns the same
    COULD NOT MEASURE the other three failures earn.
    """
    rc, out, err = run_main(
        ["900", "--round", "3"], comments=[CLAIMS_BLOCK_R2], local_head=WRONG_HEAD
    )
    assert rc == 0, err
    assert "COULD NOT MEASURE" in out, (
        "\n\nthe ledger printed a number measured against a checkout that is "
        "NOT standing on the PR. rc 0, silent stderr and a non-empty range are "
        "all satisfied in that state and the answer is still about another "
        "tree."
    )
    assert "not standing on the PR" in out and WRONG_HEAD in out, (
        "the COULD NOT MEASURE does not NAME this cause, so the operator "
        "re-runs the command by hand in the same wrong checkout"
    )
    assert "payload lines changed THIS round" not in out, (
        "a ledger line was printed under a COULD NOT MEASURE"
    )


def test_the_range_does_not_hand_out_a_head_that_is_not_the_prs_head():
    """🔴 REGRESSION. Red at `abc41024`: `<prev>..HEAD` was emitted regardless.

    The range section is the instruction the auditor actually runs. Handing it
    `..HEAD` while the shared checkout is on another branch points the whole
    delta audit at the wrong diff — and reads as an ordinary range.
    """
    rc, out, err = run_main(
        ["900", "--round", "3"], comments=[CLAIMS_BLOCK_R2], local_head=WRONG_HEAD
    )
    assert rc == 0, err
    the_range = out[out.index("## THE RANGE"):out.index("## WHAT WAS CLAIMED")]
    # The INSTRUCTION, not the explanation of why `..HEAD` is unsafe — that
    # sentence necessarily contains the token it is warning about.
    assert "Diff **`aaaa1111..HEAD`**" not in the_range, (
        f"\n\nthe range still tells the auditor to diff `..HEAD` while this "
        f"checkout is on {WRONG_HEAD} and the PR's head is {FAKE_HEAD_OID}:\n"
        f"{the_range}"
    )
    assert FAKE_HEAD_OID in the_range, (
        "the range does not name the PR's actual head sha, which is the only "
        "thing the auditor can resolve the range against"
    )
    assert "COULD NOT VERIFY" in the_range


def test_the_range_says_head_was_verified_when_it_was():
    """The other direction: a verified checkout gets the SHA, and says so.

    Measured separately from the failure case because "the guard fires" and "the
    guard does not fire spuriously" are different claims, and a check wired to
    always fail would satisfy the test above.

    🔴 ROUND 7 changed what the verified branch renders — the tip is
    `hc.local_sha`, not the token `HEAD`. The claim this test makes is
    unchanged (the head check did not fire, and the brief says the head was
    verified); only the spelling of the range moved.
    `..._names_a_sha_and_not_head_when_the_head_is_verified` owns the new
    claim.
    """
    rc, out, err = run_main(["900", "--round", "3"], comments=[CLAIMS_BLOCK_R2])
    assert rc == 0, err
    the_range = range_section(out)
    assert f"`aaaa1111..{FAKE_HEAD_OID}`" in the_range
    assert "COULD NOT VERIFY" not in the_range
    assert "DEGENERATE RANGE" not in the_range, (
        "the self-range banner fired on an ordinary, non-degenerate range — "
        "the anchor `aaaa1111` is not this checkout's HEAD"
    )
    assert FAKE_HEAD_OID in the_range and "verified at assembly time" in the_range


_DIFF_LINE = re.compile(r"Diff \*\*`([^`]+)`\*\*")


def rendered_range(brief):
    """The `<from>..<tip>` spec THE RANGE actually tells the auditor to diff."""
    m = _DIFF_LINE.search(range_section(brief))
    assert m, f"THE RANGE names no diff spec at all:\n{range_section(brief)}"
    return m.group(1)


def test_the_range_names_a_sha_and_not_head_when_the_head_is_verified():
    """🔴 REGRESSION. Red at `3619fe68`, which rendered ``aaaa1111..HEAD``.

    THE THIRD INSTANCE OF THIS LADDER'S RECURRING CONFUSION, and the first on
    the TIME axis. Round 3 conflated the operator's checkout with the tree
    under audit; round 5 conflated the assembling process's cwd with where the
    auditor stands. This one: the HEAD VERIFIED AT ASSEMBLY TIME versus the
    HEAD THE AUDITOR'S COMMAND WILL RESOLVE.

    `head_check.ok` is a fact about THIS tree at THIS moment. The auditor is
    told two sections earlier to stand somewhere else — `isolation:
    "worktree"`, or a `git worktree add` against a BRANCH in the cross-repo
    recipe — in a tree cut after assembly from a repository the brief itself
    describes as live under another session. A commit landing in between makes
    `git diff <from>..HEAD` cover commits no claims block describes, and the
    brief's own note asserts the range was verified while it does.

    🔴 TWO TELLS THAT IT IS THE SAME INVERSION, asserted below rather than
    argued: the UNVERIFIED branch already named a sha, and `emit_claims_skeleton`
    already prefers the resolved PR head. Only the VERIFIED branch hardcoded the
    token — the branch where being sure was mistaken for the range being stable.
    """
    rc, out, err = run_main(["900", "--round", "3"], comments=[CLAIMS_BLOCK_R2])
    assert rc == 0, err
    frm, _, tip = rendered_range(out).partition("..")
    assert tip != "HEAD", (
        "\n\nTHE RANGE tells the auditor to diff `..HEAD`. That token resolves "
        "in THEIR worktree, which this brief has already told them to create "
        f"themselves:\n{range_section(out)}"
    )
    assert tip == FAKE_HEAD_OID, (
        f"\n\nthe verified range ends at {tip!r}, which is not the head that "
        f"was verified ({FAKE_HEAD_OID}). Whatever it names, the auditor "
        "resolves it in a tree this script never saw."
    )
    # 🔴 DELIBERATELY NO ASSERTION ON `<from>`. Which sha anchors the range is
    # `..._range_is_generated_from_the_previous_rounds_audited_sha`'s claim, and
    # asserting it here made mutant N1 (anchor reads `<to>`) kill this row for
    # that test's reason — measured as EXTRA-KILLER. What this row claims is
    # narrower and is the whole claim: the TIP is a sha.
    # 🔴 THE CO-OCCURRENCE IS THE POINT, so it is asserted and not assumed: the
    # same brief sends the reader to a different tree. Without this the tip
    # rule would read as a style preference.
    assert 'isolation: "worktree"' in out, (
        "this brief does not send the auditor to a tree of their own, so the "
        "claim above is about a configuration it is not testing"
    )
    assert "verified at assembly time" in range_section(out)

    # 🔴 TELL ONE — the COULD-NOT-VERIFY branch already did the safe thing, and
    # still does. If this ever regressed to `HEAD` the fix would be half-applied.
    unverified = run_main(
        ["900", "--round", "3"], comments=[CLAIMS_BLOCK_R2], local_head=WRONG_HEAD
    )[1]
    assert rendered_range(unverified).partition("..")[2] == FAKE_HEAD_OID

    # 🔴 SCOPED TO THE AUDITOR-FACING INSTRUCTION, and this is the control that
    # says so: THE LEDGER's provenance line keeps `..HEAD`, correctly. That
    # command ran HERE, in the tree just verified, at assembly time. A fix that
    # rewrote it would be claiming the assembler measured something it did not.
    # Asked of the anchor THIS brief rendered, not of a literal, so a mutation
    # that moves the anchor is not answered by this row.
    assert f"{frm}..HEAD --not" in out, (
        "THE LEDGER no longer reports the range it actually measured. It ran "
        "`git rev-list`/`git log` in the assembly checkout, where `HEAD` is "
        "the verified head — rewriting that line would misreport provenance."
    )


# --------------------------------------------------------------------------- #
# 🔴 ROUND 3 — `audited=` HAS ONE MEANING AND TWO READERS
# --------------------------------------------------------------------------- #
# `<from>` is the tip that round's AUDIT read; `<to>` is the head its FIXES
# produced. Reading `<to>` as the delta anchor made the range EMPTY BY
# CONSTRUCTION, because `--emit-claims` stamps the PR's current head into that
# same field and the block is posted at the fix tip.

# A checkout standing on the sha the round-2 fixture names as its `<from>`, so
# `<from>..HEAD` is a SELF-RANGE. 40 chars, like real `rev-parse` output —
# `same_commit` has to bridge the length difference, and a `==` would not.
SELF_RANGE_HEAD = "aaaa1111000000000000000000000000000000ff"
SELF_RANGE_PR = dict(DEFAULT_PR, headRefOid=SELF_RANGE_HEAD)

# A PR whose head IS the round-2 fixture's `<to>` — the state the live PR was
# in when `--round 3 --emit-claims` printed `audited=d9eb36a8..d9eb36a8`.
AT_FIX_TIP_HEAD = "bbbb2222000000000000000000000000000000ff"
AT_FIX_TIP_PR = dict(DEFAULT_PR, headRefOid=AT_FIX_TIP_HEAD)


def test_the_ledger_measures_from_the_tip_the_previous_round_audited():
    """🔴 REGRESSION. Red at `d9eb36a8`, which ran `rev-list bbbb2222..HEAD`.

    The strongest form of the anchor claim: not what the brief SAYS, but which
    range `git` was actually asked about. The ledger and THE RANGE section are
    separate consumers of the same anchor and were wrong together.
    """
    runner = make_runner(comments=[CLAIMS_BLOCK_R2])
    rc, out, err = run_main(["900", "--round", "3"], runner=runner)
    assert rc == 0, err
    assert runner.ranges, (
        "no `git rev-list` ran at all, so this test proves nothing about which "
        "range the ledger measures"
    )
    assert runner.ranges[0] == "aaaa1111..HEAD", (
        f"\n\nthe ledger measured {runner.ranges[0]!r}. The fixture's block is "
        "`audited=aaaa1111..bbbb2222`, so the tip the previous round AUDITED "
        "is `aaaa1111`; `bbbb2222` is the head that round's fixes produced and "
        "is where the block was posted."
    )


def test_a_degenerate_self_range_is_reported_not_rendered_as_a_clean_diff():
    """🔴 REGRESSION. Red at `d9eb36a8`, which rendered it with full confidence.

    THE RANGE section does not consult the ledger, so a `<sha>..HEAD` whose
    `<sha>` IS HEAD was printed under "verified at assembly time to be PR
    #958's head commit" followed by "Do not re-audit the whole PR". An auditor
    obeying that diffs nothing, finds nothing, reports a clean round — and the
    `stop-rule` clause in the same brief converts that into "the ladder ENDS".
    Zero claims checked, and the brief reads as covered.
    """
    rc, out, err = run_main(
        ["900", "--round", "3"], comments=[CLAIMS_BLOCK_R2], pr=SELF_RANGE_PR
    )
    assert rc == 0, err
    the_range = out[out.index("## THE RANGE"):out.index("## WHAT WAS CLAIMED")]
    assert "DEGENERATE RANGE" in the_range, (
        f"\n\nthe brief handed out a range whose two ends are the same commit "
        f"with no warning:\n{the_range}"
    )
    assert "EMPTY BY CONSTRUCTION" in the_range
    assert "verified at assembly time" not in the_range, (
        "the confident head-verified sentence is still printed over a range "
        "that cannot contain anything — both were in the live round-3 brief"
    )
    # 🔴 The stop-rule interaction is the reason this is 🔴 and not 🟡: the
    # banner has to say that a finding-free pass here is NOT a clean round.
    assert "NOT evidence" in the_range and "ladder ENDS" in the_range, (
        "the banner does not tell the auditor that an empty result here is a "
        "fact about the RANGE, so the stop-rule clause converts it into a "
        "clean round anyway"
    )


def test_a_degenerate_self_range_is_named_by_the_ledger_too():
    """🔴 REGRESSION. Red at `d9eb36a8`: the ledger called it merely EMPTY.

    Two consumers, one hazard. "The range is empty" is a real measurement about
    the PR; "the range cannot contain anything" is a fact about the range, and
    the responses differ — the first says wait for the fixes, the second says
    the `audited=` field or this checkout is wrong.
    """
    rc, out, err = run_main(
        ["900", "--round", "3"], comments=[CLAIMS_BLOCK_R2], pr=SELF_RANGE_PR
    )
    assert rc == 0, err
    # 🔴 THE LEDGER SECTION ALONE. Asserting over the whole brief made this
    # test pass on THE RANGE section's banner instead — mutant N5 (delete the
    # ledger's self-range branch) SURVIVED a fully green suite until this slice
    # was added. Two sections saying the same phrase is not two guards.
    ledger = out[out.index("## THE LEDGER"):]
    assert "COULD NOT MEASURE" in ledger
    assert "EMPTY BY CONSTRUCTION" in ledger, (
        "\n\nthe ledger reported a self-range as an ordinary empty one, which "
        "sends the operator to wait for commits that would not help"
    )
    assert SELF_RANGE_HEAD in ledger, (
        "the reason does not name the HEAD it collided with, so the operator "
        "cannot see that the two ends are the same commit"
    )
    assert "payload lines changed THIS round" not in ledger


def test_an_empty_range_does_not_name_a_cause_the_head_check_refutes():
    """🔴 REGRESSION. Red at `d9eb36a8`, which named two refuted causes.

    The COULD NOT MEASURE for an empty range said "Either the fixes are not
    committed yet, or this checkout does not have them" — while `head_check`, a
    parameter of that same function already required `ok` twelve lines above,
    proves this checkout IS the PR's head and refutes the second outright. The
    live round-3 brief printed "verified at assembly time to be PR #958's head
    commit" and "this checkout does not have them" in one document.

    Same false-cause shape as the cumulative reason fixed in round 2 — that fix
    was scoped to one site rather than to the class.
    """
    rc, out, err = run_main(
        ["900", "--round", "3"],
        comments=[CLAIMS_BLOCK_R2],
        rev_list=(0, "0\n", ""),
    )
    assert rc == 0, err
    assert "COULD NOT MEASURE" in out and "is EMPTY" in out
    assert "this checkout does not have them" not in out, (
        "\n\nthe brief offered 'this checkout does not have them' as a cause "
        "for an empty range in a checkout it had just VERIFIED to be the PR's "
        "head. The reader cannot tell which half to act on."
    )
    assert "IS the PR's head" in out, (
        "the surviving cause is not stated positively, so the reader is left "
        "with a list rather than an answer"
    )


def test_the_unknown_head_sha_reason_names_one_cause_not_two(tmp_path):
    """🔴 REGRESSION. Red at `d9eb36a8` — the THIRD site of the same shape.

    Round 2 fixed one false-cause message and round 3's audit found a second;
    this is the third, found by sweeping the class rather than the two named.
    `verify_head_is_the_pr` reported "the PR's head sha is not known here
    (`gh` was not consulted — `--claims-file` mode — or reported no
    `headRefOid`)" — two causes with nothing to choose between them, in a
    function whose CALLER is the thing that decided whether to consult `gh` at
    all.
    """
    claims = tmp_path / "claims.md"
    claims.write_text(CLAIMS_BLOCK_R2, encoding="utf-8")
    rc, out, err = run_main(
        ["900", "--round", "3", "--claims-file", str(claims)],
        pr=dict(DEFAULT_PR, headRefOid=None),
        local_head=WRONG_HEAD,
    )
    assert rc == 0, err
    the_range = out[out.index("## THE RANGE"):out.index("## WHAT WAS CLAIMED")]
    assert "COULD NOT VERIFY" in the_range
    assert "`--claims-file` mode" in the_range, (
        "the reason does not name the cause that actually applies"
    )
    assert "or reported no `headRefOid`" not in the_range, (
        "\n\nthe reason still offers a second cause the caller had already "
        "ruled out. A reader cannot tell which half to act on, and one of them "
        "sends them to check `gh` output that was never fetched."
    )


def test_emit_claims_records_the_tip_this_round_audited_not_the_range_anchor():
    """The WRITER's anchor is `<to>`, and it is NOT the reader's.

    🔴 INVARIANT GUARD, green at `d9eb36a8` — recorded as such rather than
    filed with the regressions it sits beside. The base got this right by
    accident: it used ONE anchor for both sides, and `<to>` happens to be the
    correct one HERE. It exists because the obvious simplification of the
    round-3 fix — "one anchor, use `range_anchor` everywhere" — is wrong in the
    other direction and would ship a block whose `<from>` makes the NEXT round
    re-audit this round's predecessor as well. Mutant N3 kills it.
    """
    rc, out, err = run_main(
        ["900", "--round", "3", "--emit-claims"], comments=[CLAIMS_BLOCK_R2]
    )
    assert rc == 0, err
    # 🔴 `rindex`, not `index`: the EMITTED skeleton is the last fence in the
    # output, and a brief that reproduces a comment verbatim would otherwise
    # hand this test the INPUT block to parse. Mutant C2 fired here on `index`
    # — a killer that says nothing about the anchor this test owns.
    tail = out[out.rindex("```audit-claims"):]
    blocks, malformed = ad.parse_claims_blocks([tail])
    assert not malformed and len(blocks) == 1, f"{malformed}\n{tail}"
    assert blocks[0].audited_from == "bbbb2222", (
        f"\n\nthe emitted `<from>` is {blocks[0].audited_from!r}. It must be "
        "the tip THIS round's audit read — the newest block's `<to>` — not the "
        "sha this round diffed FROM (`aaaa1111`), which would make the next "
        "round re-audit the previous round's fix commits as well."
    )
    # The `<to>` half is owned by `..._stamps_the_prs_head_not_the_local_...`
    # and is deliberately NOT re-asserted here: duplicating it made mutant H3
    # kill this row too, which reads as coverage of the anchor and is not.


def test_emit_claims_warns_when_the_block_it_writes_is_a_self_range():
    """🔴 REGRESSION. Red at `d9eb36a8`, which emitted it in silence.

    Measured on the live PR: `--round 2 --emit-claims` and `--round 3
    --emit-claims` BOTH printed `audited=d9eb36a8..d9eb36a8` with no warning of
    any kind. A block like that records a round whose fixes changed nothing,
    and the next round then anchors a range that is empty by construction.
    """
    rc, out, err = run_main(
        ["900", "--round", "3", "--emit-claims"],
        comments=[CLAIMS_BLOCK_R2],
        pr=AT_FIX_TIP_PR,
        # The LOCAL `rev-parse --short HEAD` is made to agree with the PR's
        # head here on purpose: the warning must fire because the two ends of
        # the range are equal, not because of WHICH sha `--emit-claims` reads.
        # With the two disagreeing, mutant H3 (read the local head) killed this
        # row as well, and a row that dies for someone else's reason is not
        # evidence for this one.
        head_sha="bbbb2222",
    )
    assert rc == 0, err
    assert "SELF-RANGE" in err, (
        "\n\n`--emit-claims` wrote `audited=bbbb2222..bbbb2222` and said "
        f"nothing. stderr was:\n{err}"
    )
    assert "EMPTY BY CONSTRUCTION" in err and "do not post this block" in err


def test_a_bare_round_one_audited_sha_still_anchors_the_next_round():
    """The round-1 spelling has no `<from>`, and must fall back to `<to>`.

    🔴 INVARIANT GUARD, green at `d9eb36a8` — the bare form was already read
    correctly there, by the same accident. `--emit-claims` at round 1 writes
    `audited=<sha>` with no `..`, so a fix that reached for `audited_from`
    ALONE would leave the round-2 range with no anchor at all, breaking the
    exact remedy chain the delta refusal advertises. Mutant N2 kills it.
    """
    bare_r1 = "```audit-claims round=1 audited=aaaa1111\n1. a round-1 claim\n```"
    rc, out, err = run_main(["900", "--round", "2"], comments=[bare_r1])
    assert rc == 0, err
    assert f"`aaaa1111..{FAKE_HEAD_OID}`" in range_section(out), (
        "a bare round-1 `audited=<sha>` no longer anchors the round-2 range; "
        f"stderr was:\n{err}"
    )
    assert ad.range_anchor(ad.ClaimsBlock(1, "", "aaaa1111", ["x"])) == "aaaa1111"
    assert ad.range_anchor(ad.ClaimsBlock(2, "cc", "dd", ["x"])) == "cc"
    assert ad.emit_anchor(ad.ClaimsBlock(2, "cc", "dd", ["x"])) == "dd"


def test_emit_claims_stamps_the_prs_head_not_the_local_checkouts():
    """🔴 REGRESSION. Red at `abc41024`, which stamped `rev-parse --short HEAD`.

    The `audited=` sha is the field the NEXT round anchors its range and its
    ledger on. Stamping the local HEAD recorded whatever branch the shared
    checkout was standing on — `main`'s tip, in the reproduction — as the sha
    this round audited, and the next round then measured from there.
    """
    rc, out, err = run_main(
        ["900", "--round", "3", "--emit-claims"],
        comments=[CLAIMS_BLOCK_R2],
        local_head=WRONG_HEAD,
        head_sha="deadbee",
    )
    assert rc == 0, err
    block = out[out.index("```audit-claims"):]
    assert "deadbee" not in block, (
        "\n\nthe emitted block carries the LOCAL checkout's HEAD as the sha "
        f"this round audited:\n{block}\n"
        "  The next round anchors on this field."
    )
    assert FAKE_HEAD_OID[:8] in block, (
        "the emitted block does not carry the PR's own head sha"
    )
    assert "NOT standing on it" in err, (
        "the mismatch was stamped over silently; the operator writing the "
        "claims needs to know the checkout they measured in is not the PR"
    )


# --------------------------------------------------------------------------- #
# 🔴 ROUND 4 — THE ROUND-1 -> ROUND-2 HOP, WHICH WAS EMPTY BY CONSTRUCTION
# --------------------------------------------------------------------------- #
# Round 3 spelled the self-range warning on `same_commit(facts.emit_from, head)`
# — and for a ROUND-1 `--emit-claims` there is no prior block, so `emit_from` is
# None and the guard is STRUCTURALLY UNREACHABLE. The bare block it writes
# instead carries the identical hazard: `range_anchor` falls back to `<to>`, and
# `<to>` IS the head it was just stamped at. Round 1 is the remedy the delta
# refusal ADVERTISES, so the unreachable case was the advertised one — and this
# PR's own live round-1 block (`audited=abc41024`, bare, the round-1 FIX tip) is
# the instance.

def test_a_round_one_emit_claims_says_head_is_an_assumption_not_a_measurement():
    """🔴 REGRESSION. Red at `e06461f7`, which emitted it in total silence.

    The observable at the base is not a wrong sha — it is NO STDERR AT ALL over
    a block whose single sha makes the next round's range empty by
    construction. The next round then renders DEGENERATE and the tip round 1
    actually read is recorded nowhere.
    """
    rc, out, err = run_main(["900", "--round", "1", "--emit-claims"])
    assert rc == 0, err
    tail = out[out.rindex("```audit-claims"):]
    blocks, malformed = ad.parse_claims_blocks([tail])
    assert not malformed and len(blocks) == 1, f"{malformed}\n{tail}"
    # 🔴 The BARE spelling, and deliberately NOT `audited_to == <the PR head>`:
    # that claim is owned by `..._stamps_the_prs_head_not_the_local_checkouts`,
    # and re-asserting it here made mutant H3 (read the LOCAL head) kill this
    # row too — a row that dies for someone else's reason is not evidence for
    # the one it names.
    assert blocks[0].audited_from == "" and blocks[0].audited_to, (
        "the round-1 block is not the bare `audited=<sha>` spelling this "
        f"warning is about: {blocks[0]!r}"
    )
    assert "ASSUMED" in err, (
        "\n\n`--emit-claims` at round 1 stamped the PR's CURRENT head into the "
        "field the next round anchors on and said nothing. It runs AFTER the "
        "round's fixes land, so that head is the one the FIXES produced — not "
        f"the tip the round read. stderr was:\n{err}"
    )
    assert "EMPTY BY CONSTRUCTION" in err and "--audited" in err, (
        "the warning does not say what goes wrong next, or how to supply the "
        f"sha it could not derive. stderr was:\n{err}"
    )


def test_audited_supplies_the_tip_a_round_one_emit_cannot_derive():
    """🔴 INVARIANT GUARD — `--audited` does not exist at `e06461f7`.

    A test that fails there fails with `unrecognized arguments`, which is an
    error for want of a name the fix introduced and is not evidence of
    anything. Its evidence is mutant Y7 (the flag is parsed and ignored).

    The assertion is END TO END on purpose: the emitted block is fed back
    through this script's OWN parser as the next round's comment, because the
    only thing that matters about the field is what the next round anchors on.
    """
    rc, out, err = run_main(
        ["900", "--round", "1", "--emit-claims", "--audited", "abcd1234"]
    )
    assert rc == 0, err
    tail = out[out.rindex("```audit-claims"):]
    blocks, malformed = ad.parse_claims_blocks([tail])
    assert not malformed and len(blocks) == 1, f"{malformed}\n{tail}"
    assert blocks[0].audited_from == "abcd1234", (
        f"\n\n`--audited abcd1234` did not reach the block's `<from>`: "
        f"{blocks[0]!r}. That field is the ONLY way a round-1 comment can "
        "record the tip its audit read; --emit-claims runs after the fixes and "
        "cannot derive it."
    )
    # The `<to>` half is owned by `..._stamps_the_prs_head_not_the_local_...`
    # and is deliberately not re-asserted here — see the sibling test.
    assert "ASSUMED" not in err, (
        f"the assumption warning still fired with --audited passed: {err}"
    )

    emitted = "\n".join(tail.splitlines()[:4])
    rc2, out2, err2 = run_main(["900", "--round", "2"], comments=[emitted])
    assert rc2 == 0, err2
    assert f"`abcd1234..{FAKE_HEAD_OID}`" in range_section(out2), (
        "\n\nthe next round does not anchor on the tip round 1 audited, so the "
        "round-1 -> round-2 hop still loses round 1's own fix commits."
    )
    assert "DEGENERATE RANGE" not in out2


def test_audited_without_emit_claims_says_it_changed_nothing():
    """🔴 INVARIANT GUARD (the flag does not exist at `e06461f7`); mutant Y8.

    A flag that silently does nothing is the shape this module refuses
    everywhere else. `--audited` is written by `--emit-claims` and by nothing
    else, so an operator who passes it to a plain assembly run must not read
    the silence as "recorded".
    """
    rc, out, err = run_main(
        ["900", "--round", "3", "--audited", "abcd1234"],
        comments=[CLAIMS_BLOCK_R2],
    )
    assert rc == 0, err
    assert "--audited" in err and "changed NOTHING" in err, (
        f"the ignored flag was accepted in silence; stderr was:\n{err}"
    )
    assert f"`aaaa1111..{FAKE_HEAD_OID}`" in range_section(out), (
        "worse than silence: `--audited` moved the range this round DIFFS "
        "from. It is the WRITER's anchor and must not touch the reader's."
    )


# --------------------------------------------------------------------------- #
# 🔴 ROUND 4 — THE DEGENERATE-RANGE CAUSES, WHICH `anchor_is_head` REFUTES
# --------------------------------------------------------------------------- #
# `anchor_is_head` returns True only when `head_check.ok` — this checkout is
# VERIFIED to be the PR's head. Both degenerate-range messages nevertheless
# offered "the round's fix commits are not in this checkout", sending the
# operator to fetch commits into a checkout that already has them, which the
# brief's own `no-fetch` clause forbids there.

# The spellings a message could use to claim the commits are absent. 🔴 This
# probe is NOT what makes the guard non-walkable — a reword could step around
# it. Its job is to be RED AT THE BASE, i.e. to observe the wrong answer that
# shipped. Non-walkability comes from the whole-string `DIRECTIVE_LEDGER` pin
# on `degenerate-range-causes` and from the writer ledger below it.
ABSENT_COMMIT_CLAIMS = (
    "not in this checkout",
    "does not have them",
    "checkout does not have",
)


def test_a_degenerate_range_does_not_blame_a_checkout_it_verified():
    """🔴 REGRESSION. Red at `e06461f7`, at BOTH of its two sites.

    The round-3 fix for this class said in its own commit message that it went
    "to the CLASS rather than to one site" — and then wrote two new sites with
    the same false cause nine lines below the one it fixed. So this asserts
    over both consumers in one test: they are one hazard with two renderers,
    and a test scoped to either would have passed at the base.
    """
    rc, out, err = run_main(
        ["900", "--round", "3"], comments=[CLAIMS_BLOCK_R2], pr=SELF_RANGE_PR
    )
    assert rc == 0, err
    sections = {
        "THE RANGE": out[out.index("## THE RANGE"):out.index("## WHAT WAS CLAIMED")],
        "THE LEDGER": out[out.index("## THE LEDGER"):],
    }
    for name, text in sections.items():
        assert "EMPTY BY CONSTRUCTION" in text, (
            f"{name} does not report the degenerate range at all, so this "
            "test is asserting over the wrong slice"
        )
        for phrase in ABSENT_COMMIT_CLAIMS:
            assert phrase not in text, (
                f"\n\n{name} offers {phrase!r} as a cause for a range it "
                "reached only because `anchor_is_head` confirmed this checkout "
                "IS the PR's head commit. The operator is told to get commits "
                "into a checkout that already has them — and the `no-fetch` "
                "clause forbids that action there."
            )
        assert norm(DIRECTIVE_LEDGER["degenerate-range-causes"]) in norm(text), (
            f"\n\n{name} does not carry the ledgered cause list, so it is "
            "hand-rolling its own again — which is how the two sites drifted "
            "apart from the one nine lines above them."
        )


def test_the_degenerate_range_causes_have_exactly_one_writer_per_consumer():
    """🔴 INVARIANT GUARD, and the STRUCTURAL half of the pair above.

    A seam guard in the `claude/RULES.md` sense: an asserted ledger of every
    site that renders this cause list, failing when the set GROWS (a third
    consumer joined with nobody reading its prose) or SHRINKS (a consumer went
    back to hand-rolling). The phrase probe above cannot see either.

    🔴 THE RESIDUAL BLIND SPOT, said rather than left to read as covered: a
    BRAND-NEW degenerate surface that never calls `directive()` at all is
    invisible here. Nothing mechanical in this module can see prose that has
    not been written yet.
    """
    src = SCRIPT.read_text(encoding="utf-8")
    sites, fn = set(), None
    for line in src.splitlines():
        m = re.match(r"def (\w+)\(", line)
        if m:
            fn = m.group(1)
        if 'directive("degenerate-range-causes")' in line and fn:
            sites.add(fn)
    assert sites == {"measure_ledger", "render_range"}, (
        f"\n\nthe degenerate-range cause list is rendered by {sorted(sites)}, "
        "not by exactly the two consumers this ledger names.\n"
        "  GREW: a new site renders auditor-facing prose that nobody reviewed "
        "against `head_check`'s guarantee.\n"
        "  SHRANK: a consumer is hand-rolling its own causes again — which is "
        "the defect, not a refactor."
    )


# --------------------------------------------------------------------------- #
# 🔴 ROUND 4 — "THE SHARED CHECKOUT" NAMED WHATEVER CWD WAS
# --------------------------------------------------------------------------- #

def test_a_private_worktree_is_not_described_as_shared_and_is_not_absolved():
    """🔴 REGRESSION. Red at `e06461f7`, which asserted SHARED of every tree.

    Run from `…/.claude/worktrees/agent-…` — the tree this script is ROUTINELY
    ASSEMBLED IN — the brief printed a heading reading THE SHARED CHECKOUT
    over "This checkout is SHARED with other sessions and agents. It MOVES
    UNDER YOU … That is expected and is NOT your fault and NOT a finding."

    🔴 ROUND 6 CORRECTED THIS PARAGRAPH. It used to place a dispatched auditor
    in that worktree — the premise `REFUTED_PREMISES[0]` records, and the one
    round 5 overturned: the tree is measured by the ASSEMBLING process, and
    WHERE TO WORK sends the auditor somewhere else. The script's own docstring
    was corrected in round 5 and this one was not, leaving the ladder's record
    of the finding asserting the premise the finding overturned — twelve lines
    above a docstring the same round edited.
    `test_no_refuted_premise_survives_in_this_modules_prose` is what makes that
    mechanical instead of noticed.

    🔴 The DIRECTION is the consequential half, and it is what this asserts:
    a reader in a private worktree who watches files move is told it is
    expected and not a finding, which suppresses exactly the sibling-agent
    clobber `claude/RULES.md` says to report.
    """
    checkout = checkout_section(brief_for_scenario("private-worktree"))
    # 🔴 THE NEGATIVES FIRST, deliberately. Asserting "PRIVATE worktree" is
    # present first would make this test fail at the base for want of a WORD
    # the fix introduced; failing on the two false claims below is failing on
    # the wrong ANSWER, which is what makes this regression coverage rather
    # than a restatement of the diff.
    assert "SHARED with other sessions" not in checkout, (
        f"\n\nthe brief calls a PRIVATE per-agent worktree shared:\n{checkout}"
    )
    assert "NOT a finding" not in checkout, (
        "\n\nthe brief absolves movement in a PRIVATE linked worktree, which "
        "belongs to one session and to no other. That absolution is what "
        "suppresses a sibling-agent clobber report — the one signal a private "
        "worktree can produce."
    )
    # 🔴 The POSITIVES moved in round 5 and the reason is the finding: round 4
    # replaced the false SHARED claim with "a PRIVATE worktree — yours alone …
    # Writing here is fine", which is a WRITE GRANT over the dispatching
    # session's tree. The state is still reported; what it licenses is not.
    # Both negatives above are unchanged, so this is still red at `e06461f7`
    # on the wrong ANSWER (that tree renders `checkout-moves`).
    assert "PRIVATE linked worktree" in checkout
    assert "report it with what moved and when" in checkout


def test_an_unreadable_worktree_state_is_its_own_answer_and_keeps_the_no_write():
    """🔴 INVARIANT GUARD — the third state; mutant Y6 collapses it.

    Same three-state rule as `render_worktree_directive`: not knowing is its
    own answer, and it must keep the conservative instruction (write nothing)
    WITHOUT the absolution (movement is expected). Collapsing it either way
    picks a branch whose failure is silent.
    """
    checkout = checkout_section(brief_for_scenario("unreadable-worktree"))
    assert "COULD NOT DETERMINE" in checkout
    assert "write nothing to it" in checkout, (
        "the unknown state dropped the conservative no-write instruction"
    )
    assert "NOT a finding" not in checkout, (
        "the unknown state kept the absolution. The conservative half of "
        "'treat it as shared' is the no-write rule; the absolution is the "
        "half that costs a report."
    )
    # And the SHARED state still says what it always said.
    shared = brief_for_scenario("delta")
    assert "SHARED with other sessions" in shared and "NOT a finding" in shared


def test_gather_worktree_kind_resolves_paths_before_comparing_them():
    """🔴 INVARIANT GUARD — `gather_worktree_kind` does not exist at the base.

    🔴 MEASURED AT THREE SHAPES, because one measurement here is not a claim:
    real `git rev-parse --git-dir --git-common-dir` answers `.git`/`.git` at a
    repo ROOT, `/abs/path/.git`/`../.git` in a repo SUBDIRECTORY, and
    `/abs/.git/worktrees/<n>`/`/abs/.git` in a linked worktree. The middle row
    is the trap: textually different, the same directory. Comparing the strings
    calls every subdirectory of an ordinary clone a PRIVATE worktree — wrong in
    the direction that drops the no-write instruction. `gather_repo_facts`
    falls back to the raw cwd when `--show-toplevel` fails, so that row is
    reachable in production. Mutant Y5b.
    """
    def stub(answer):
        def runner(cmd, cwd=None):
            assert "--git-common-dir" in cmd, cmd
            return answer
        return runner

    root = ad.gather_worktree_kind(stub(SHARED_GIT_DIRS), FAKE_REPO_DIR)
    assert root.kind == "shared", root

    subdir = ad.gather_worktree_kind(
        stub((0, f"{FAKE_REPO_DIR}/.git\n../.git\n", "")),
        f"{FAKE_REPO_DIR}/sub",
    )
    assert subdir.kind == "shared", (
        f"\n\na SUBDIRECTORY of an ordinary clone was classified {subdir!r}. "
        "git answers `/abs/.git` and `../.git` there — different strings, the "
        "same directory."
    )

    linked = ad.gather_worktree_kind(stub(PRIVATE_GIT_DIRS), FAKE_REPO_DIR)
    assert linked.kind == "private", linked

    for broken in (UNREADABLE_GIT_DIRS, (0, ".git\n", "")):
        got = ad.gather_worktree_kind(stub(broken), FAKE_REPO_DIR)
        assert got.kind == "unknown" and got.reason, (
            f"a read that answered {broken!r} was not reported as unknown: "
            f"{got!r}"
        )


def test_the_where_to_work_section_does_not_call_a_private_worktree_the_clone():
    """🔴 REGRESSION. Red at `e06461f7`.

    The repo RELATION is unaffected — a linked worktree really is the same
    repository, and the isolation recommendation stays correct. The PATH is
    not the shared clone, and the brief said it was: "the PR lives in
    `<org>/devrc`, which is this session's own repository
    (`…/worktrees/agent-…`)".
    """
    out = brief_for_scenario("private-worktree")
    where = out[out.index("## WHERE TO WORK"):out.index("## THE RANGE")]
    assert ad.ISOLATION_RECOMMEND in where, (
        "the private-worktree case is still a SAME-REPO PR and the flag is "
        "still right; only the description of the path was wrong"
    )
    assert "this session's own repository" not in where, (
        f"\n\nthe brief names a private per-agent worktree as the session's "
        f"repository, full stop:\n{where}"
    )
    assert "PRIVATE linked worktree" in where


# --------------------------------------------------------------------------- #
# 🔴 ROUND 4 — `same_commit` WAS CASE-SENSITIVE
# --------------------------------------------------------------------------- #

def test_an_uppercase_audited_sha_still_trips_the_degenerate_guard():
    """🔴 REGRESSION. Red at `e06461f7`, where one shift key disarmed the guard.

    `git rev-parse` prints lowercase, but `audited=` is TYPED BY A HUMAN into a
    PR comment. Compared case-sensitively, `ABC41024` is not the same commit as
    `abc41024ff…`, `anchor_is_head` answers False at all three call sites, and
    the confident "verified at assembly time to be PR #<n>'s head commit"
    banner comes back over a range that cannot contain anything.
    """
    assert ad.same_commit("ABC41024", "abc41024ff00112233445566778899aabbccddee"), (
        "`same_commit` is case-sensitive, so an upper- or mixed-case "
        "`audited=` sha disables every degenerate-range guard in this script"
    )
    upper = (
        "```audit-claims round=2 audited=AAAA1111..BBBB2222\n"
        "1. a claim whose anchor was typed in upper case\n"
        "```"
    )
    rc, out, err = run_main(
        ["900", "--round", "3"], comments=[upper], pr=SELF_RANGE_PR
    )
    assert rc == 0, err
    the_range = out[out.index("## THE RANGE"):out.index("## WHAT WAS CLAIMED")]
    assert "DEGENERATE RANGE" in the_range, (
        f"\n\nan UPPERCASE anchor equal to HEAD rendered as an ordinary "
        f"range:\n{the_range}"
    )
    assert "verified at assembly time" not in the_range


# --------------------------------------------------------------------------- #
# 🔴 ROUND 5 — THE BRIEF GRANTED WRITE PERMISSION OVER A TREE THAT IS NOT THE
#              AUDITOR'S, AND OVERRODE ITS OWN READ-ONLY CLAUSE
# --------------------------------------------------------------------------- #
# Round 4 removed the false SHARED claim from the private state and replaced it
# with "**a PRIVATE worktree — yours alone** … Writing here is fine: the
# no-write clause below is about a SHARED checkout, and this is not one", and
# reworded `no-fetch` in the same range from unconditional to conditional. So
# the prohibition was ARMED ONLY when `gather_worktree_kind` returns `shared` —
# and in the production configuration it returns `private`.
#
# 🔴 THE ROOT ERROR IS THE PREMISE. `gather_worktree_kind` measures the cwd of
# the ASSEMBLING process; the consumer is a DIFFERENT process, dispatched by
# WHERE TO WORK to make its own copy. "Private" therefore means "private to the
# session that built the brief" — a session that is LIVE in that tree — and
# under the other configuration (no isolation, inherited cwd) the same tree is
# shared with the dispatcher and with sibling auditors. No state this script can
# measure makes the tree the auditor's own.
#
# Observed live: the round-5 brief named this session's own agent worktree,
# classified it PRIVATE and printed "Writing here is fine". The operator's
# hand-written dispatch had to contradict the generated brief in prose —
# retyping a correction over generated text being the failure this script exists
# to remove.

# 🔴 THE PROBE, and what it is and is not. Like `ABSENT_COMMIT_CLAIMS` above,
# its job is to be RED AT THE BASE — to observe the wrong answer that shipped —
# not to be unwalkable; a reword could step around it. Non-walkability comes
# from the whole-string `DIRECTIVE_LEDGER` pin on every `checkout-*` entry and
# from the relationship ledger below.
#
# SCOPED TO THE CHECKOUT SECTION on purpose: `own-worktree-is-writable` grants
# writing to the worktree the AUDITOR built, in WHERE TO WORK, and that grant is
# correct. What may never appear is a grant over the path THE CHECKOUT names.
WRITE_PERMISSION_PHRASES = (
    "Writing here is fine",
    "yours alone",
    "tree is yours",
    "you may write",
    "writing here is fine",
    "free to write",
)

# The round-4 text, verbatim, kept ONLY as the positive control for the probe: a
# scan that reports zero is indistinguishable from a scan wired to nothing.
ROUND_4_WRITE_GRANT = (
    "🔴 **This is a PRIVATE worktree — yours alone.** Its `.git` is a link "
    "into a shared repository, but the WORKING TREE is not shared, so files "
    "appearing, vanishing or changing under you is **NOT expected here and IS "
    "worth reporting**. **If something moves, report it with what moved and "
    "when.** Writing here is fine: the no-write clause below is about a SHARED "
    "checkout, and this is not one."
)

# 🔴 THE STATES `render_checkout` CAN SELECT, keyed by the `gather_worktree_kind`
# answer — pinned two-way against the SCRIPT's own map, never derived from an id
# prefix. Round 6 measured why: renaming `checkout-private` to
# `assembly-private`, dropping its no-write sentence and updating the three
# ledgers left the whole 89-test suite GREEN, because the guard's set was
# spelled "ids beginning `checkout-`" and the renamed directive fell out of it.
# The relationship that matters is "every directive the renderer can select
# carries the rule", and the renderer's selection is now readable.
CHECKOUT_KIND_SCENARIO = {
    "shared": "delta",
    "private": "private-worktree",
    "unknown": "unreadable-worktree",
}
# Matched case-insensitively at the first letter so "**Write nothing to it**"
# and "and write nothing to it" both count — the sentence, not its capital.
NO_WRITE_RULE = "rite nothing to it"


def write_permissions_in(text):
    return [p for p in WRITE_PERMISSION_PHRASES if p in text]


def test_no_checkout_state_grants_write_permission_over_the_assembly_checkout():
    """🔴 REGRESSION. Red at `dd601793` in the PRIVATE state.

    The wrong ANSWER, not a missing word: the brief names a path, says the
    working tree there is the reader's alone, and tells them writing to it is
    fine — over a worktree belonging to the dispatching session, in the state
    the production configuration actually produces.

    The three states are driven together because the hazard is one claim with
    three renderers, and a test scoped to any single state would have passed at
    some base or other.
    """
    assert write_permissions_in(ROUND_4_WRITE_GRANT), (
        "POSITIVE CONTROL: the probe cannot see the round-4 grant it was "
        "written to catch, so a clean result below would say nothing at all"
    )
    for scenario in ("private-worktree", "delta", "unreadable-worktree"):
        checkout = checkout_section(brief_for_scenario(scenario))
        assert FAKE_REPO_DIR in checkout, (
            f"scenario {scenario!r}: THE CHECKOUT does not name a path, so "
            "this test is asserting over the wrong slice"
        )
        granted = write_permissions_in(checkout)
        assert not granted, (
            f"\n\nscenario {scenario!r}: THE CHECKOUT grants the auditor "
            f"permission to write to `{FAKE_REPO_DIR}` — {granted}.\n"
            "  That path is the tree the ASSEMBLER stood in. The auditor is a "
            "different process, dispatched elsewhere by WHERE TO WORK, and the "
            "session that owns this tree is live in it. The `read-only` clause "
            "is non-negotiable and no state may override it.\n"
            f"{checkout}"
        )


def test_every_checkout_state_carries_the_no_write_rule():
    """🔴 INVARIANT GUARD — a RELATIONSHIP over the `checkout-*` set.

    Its evidence is the mutation battery (Z1, Z2), not a base ref: at
    `dd601793` two of the three states simply lack the sentence, which is an
    absence the fix adds rather than a wrong answer observed.

    A seam guard in the `claude/RULES.md` sense. The per-state directive is the
    ONLY thing that survives a reword of the `no-fetch` clause — round 4
    reworded that clause into a conditional one and the private state was left
    with a write grant and no rule anywhere — so every state must carry the rule
    itself.

    🔴 ROUND 6 REPLACED HOW THE SET IS DERIVED, and the old spelling was the
    finding. It read `{d.id for d in SECTION_DIRECTIVES if
    d.id.startswith("checkout-")}` — an id PREFIX, i.e. a naming convention —
    while the relationship it means is "every directive `render_checkout` can
    SELECT". Measured at `3619fe68`: renaming `checkout-private` to
    `assembly-private`, deleting its "Write nothing to it" and updating the
    three ledgers left the suite at **89/89 green**. The renamed directive
    simply fell out of the set, and a guard whose set can be emptied by a
    rename is spelled, not structural. It now reads `render_checkout`'s own
    `CHECKOUT_STATE_DIRECTIVE` map, and drives one SCENARIO per state rather
    than looking each id up in a ledger that a rename also updates.
    """
    states = ad.CHECKOUT_STATE_DIRECTIVE
    assert set(states) == set(CHECKOUT_KIND_SCENARIO), (
        f"\n\nthe checkout STATES are {sorted(states)}, not "
        f"{sorted(CHECKOUT_KIND_SCENARIO)}.\n"
        "  GREW: a new state renders auditor-facing prose that nobody checked "
        "carries the no-write rule, and no scenario drives it.\n"
        "  SHRANK: a state was deleted and its scenario left behind."
    )
    for kind, cid in sorted(states.items()):
        assert cid in ad.DIRECTIVE, (
            f"\n\nstate {kind!r} selects directive `{cid}`, which does not "
            "exist. `render_checkout` would print the MISSING-BLOCK "
            "placeholder for every brief assembled in that state."
        )
        assert NO_WRITE_RULE in ad.DIRECTIVE[cid], (
            f"\n\nstate {kind!r} selects `{cid}`, which does not tell the "
            "auditor to write nothing to the path THE CHECKOUT names. Only "
            "the per-state directive survives a reword of the `no-fetch` "
            "clause, and round 4 reworded that clause into one that switches "
            "itself off."
        )
    # 🔴 BEHAVIOURAL, because a structural check type-checks past prose that
    # never reaches an artifact: each state's rule must be IN the section a run
    # in that state actually prints.
    for kind, scenario in sorted(CHECKOUT_KIND_SCENARIO.items()):
        section = checkout_section(brief_for_scenario(scenario))
        assert NO_WRITE_RULE in section, (
            f"state {kind!r} (scenario {scenario!r}) renders a checkout "
            f"section with no no-write rule in it:\n{section}"
        )


# --------------------------------------------------------------------------- #
# 🔴 ROUND 6 — A FORWARD REFERENCE THAT NARROWED THE RULE IT NAMED
# --------------------------------------------------------------------------- #
# Round 5 rewrote `no-fetch` off SHAREDNESS and onto OWNERSHIP. The sentence in
# `own-worktree-is-writable` that forward-references that clause was left
# saying "the no-write rule below is about the SHARED checkout" — true at
# `dd601793`, false at `3619fe68`, and the falsity is load-bearing: rendered in
# the configuration production actually produces (a cross-repo PR assembled in
# a private per-agent worktree) the brief reads, in order,
#
#   WHERE TO WORK : "The no-write rule below is about the SHARED checkout."
#   THE CHECKOUT  : "kind : PRIVATE linked worktree — belongs to the session…"
#   NON-NEGOTIABLE: "…write to any checkout that is not the copy YOU made…"
#
# which is the syllogism round 4 wrote into `checkout-private` and round 5
# deleted, surviving one directive over. The reader can discharge the
# non-negotiable clause on the grounds that this checkout is not shared.

# The vocabulary the brief uses to REPORT a checkout state, as the `kind :`
# line spells it. Deliberately the rendered words and not the internal kind
# keys: what a forward reference can contradict is what the reader was told.
CHECKOUT_STATE_WORDS = ("SHARED", "PRIVATE", "COULD NOT DETERMINE")

_KIND_LINE = re.compile(r"^\s*kind\s*:\s*(.+)$", re.M)

# 🔴 Round 5's sentence, verbatim, kept ONLY as the positive control's payload.
ROUND_5_NO_WRITE_SCOPE = "The no-write rule below is about the SHARED checkout."


def no_write_forward_references(text):
    """Sentences that tell the reader what the no-write rule COVERS.

    Sliced on sentence boundaries rather than on the directive, because the
    hazard is a CLAIM ABOUT ANOTHER SECTION and any section may make one.
    """
    return [s for s in re.split(r"(?<=[.!])\s+", text) if "no-write rule" in s]


def checkout_kind_word(brief):
    """The state word THE CHECKOUT's `kind :` line reports for this brief.

    Read off that LINE and not off the section, because two of the three state
    directives legitimately mention a state they are not: `checkout-unknown`
    says "Treat it as SHARED", and `checkout-private` mentions a "shared
    repository". The `kind :` line is the brief's own answer.
    """
    m = _KIND_LINE.search(checkout_section(brief))
    assert m, f"no `kind :` line in THE CHECKOUT:\n{checkout_section(brief)}"
    named = [w for w in CHECKOUT_STATE_WORDS if w in m.group(1)]
    assert len(named) == 1, (
        f"the `kind :` line names {named}, so this brief has no single "
        f"reported state to compare a forward reference against: {m.group(1)!r}"
    )
    return named[0]


def no_write_scope_conflicts(brief):
    """-> [(sentence, reported state, states it names instead)].

    The RELATIONSHIP: a sentence that scopes the no-write rule to a checkout
    state may not name a state THIS brief's own THE CHECKOUT section denies.
    Deliberately says nothing about the `no-fetch` clause's wording — round 5
    owns that, and re-asserting it here would make mutant Z3 kill this row for
    someone else's reason.
    """
    kind = checkout_kind_word(brief)
    out = []
    for sentence in no_write_forward_references(brief):
        wrong = [w for w in CHECKOUT_STATE_WORDS if w in sentence and w != kind]
        if wrong:
            out.append((sentence, kind, wrong))
    return out


def test_no_forward_reference_scopes_the_no_write_rule_to_a_denied_state():
    """🔴 REGRESSION. Red at `3619fe68`, in the `cross-repo-private` scenario.

    The wrong ANSWER, not a missing word: the brief tells the auditor the
    no-write rule is about the SHARED checkout, two bars above its own
    statement that the checkout in question is PRIVATE — and the clause it is
    describing has had no sharedness scoping since round 5.

    🔴 Both cross-repo scenarios are driven, and only ONE is red at the base.
    In `cross-repo` the reported state really is SHARED, so the sentence is
    merely stale there; the contradiction needs the PRIVATE assembly tree,
    which is the ordinary case for an agent-dispatched round and is exactly
    how round 6 observed it. A test scoped to `cross-repo` alone would have
    passed at the base and reported the hazard absent.
    """
    # 🔴 POSITIVE CONTROL, through the same two helpers a real brief goes
    # through. A conflict list that comes back empty is indistinguishable from
    # a scan wired to nothing.
    control = (
        "## THE CHECKOUT — state at assembly time\n\n"
        "    kind   : PRIVATE linked worktree — belongs to the session that "
        "assembled this brief, not to you\n\n"
        f"{ROUND_5_NO_WRITE_SCOPE}\n\n"
        "## NEXT\n"
    )
    assert no_write_scope_conflicts(control), (
        "POSITIVE CONTROL: the scan cannot see round 5's own sentence over a "
        "PRIVATE `kind :` line, so a clean result below would say nothing"
    )

    seen = 0
    for scenario in SCENARIOS:
        brief = brief_for_scenario(scenario)
        seen += len(no_write_forward_references(brief))
        conflicts = no_write_scope_conflicts(brief)
        assert not conflicts, (
            f"\n\nscenario {scenario!r}: the brief scopes the no-write rule to "
            "a checkout state its own THE CHECKOUT section denies.\n"
            + "".join(
                f"  says   : {s!r}\n  reports: kind = {k}\n  names   : {w}\n"
                for s, k, w in conflicts
            )
            + "  The rule the sentence describes is scoped by OWNERSHIP — "
            "every checkout you did not make — and has been since round 5. "
            "Naming a STATE lets the reader discharge it on the grounds that "
            "this checkout is not that state, which is round 4's syllogism "
            "one directive over."
        )
    assert seen, (
        "no scenario renders a forward reference to the no-write rule at all, "
        "so the loop above asserted nothing. Either the sentence was deleted "
        "or `no_write_forward_references` no longer matches how it is spelled."
    )


def toolchain_section(brief):
    """The TOOLCHAIN block, sliced off the two headings that bracket it."""
    start = brief.index("## TOOLCHAIN")
    return brief[start:brief.index(EXPECTED_INVARIANTS_HEADING, start)]


def test_the_toolchain_reason_is_true_in_every_scenario():
    """🔴 REGRESSION. Red at `dd601793` in the PRIVATE state.

    `render_toolchain` interpolated `r = facts.cwd_repo_dir` into prose reading
    "running the shared copy runs the suite in the SHARED CHECKOUT on whatever
    branch it is standing on". In the private state `r` is a private worktree
    and THE CHECKOUT has already said so two bars above, so the stated REASON is
    refuted by the same document — and the rationale is what persuades. An
    auditor who discounts it gates the un-mutated head, then obeys the next
    sentence ("name the tier and the base sha") and names a tree that does not
    contain what they tested. That is round 2's finding re-opened in a new
    state.

    🔴 The PRIVATE state runs FIRST so the red at the base is the FALSE claim.
    In the shared state the sentence was true; making it state-independent is
    the same fix, and the equality assertion at the foot is what pins it.

    🔴 ROUND 6 WIDENED IT FROM TWO SCENARIOS TO ALL OF THEM, and the two it
    drove were both SAME-REPO. Measured at `3619fe68`: re-introducing the
    forbidden phrase in the CROSS-REPO branch left the suite at **89 passed**,
    and no mutant row named it. An equality over two same-repo scenarios does
    not pin state-independence — it pins independence of the one axis it
    happened to vary. It now drives every scenario `brief_for_scenario` knows,
    including the cross-repo pair, and requires the rendered section to be
    BYTE-IDENTICAL across all of them: that is the claim the section's own
    docstring makes ("this section knows nothing about where the auditor
    stands"), stated as wide as the code it describes.
    """
    sections = {}
    for scenario in SCENARIOS:
        tool = toolchain_section(brief_for_scenario(scenario))
        sections[scenario] = tool
        assert "SHARED CHECKOUT" not in tool, (
            f"\n\nscenario {scenario!r}: TOOLCHAIN calls `{FAKE_REPO_DIR}` the "
            "SHARED CHECKOUT. This section knows nothing about where the "
            "auditor stands and THE CHECKOUT may have just denied that state, "
            "so the reason must hold in every state — a refuted rationale is "
            f"discounted along with the instruction it carries.\n{tool}"
        )
        assert "resolves its root from its own path" in tool, (
            "the section no longer says WHY the assembly path is wrong there, "
            "so the next editor puts it back"
        )
        assert "is NOT yours" in tool, (
            "the reason no longer names the property that is true in every "
            "state — that copy is not the auditor's"
        )
    # 🔴 EQUALITY ACROSS EVERY SCENARIO, not across a chosen pair. Compared
    # against ONE reference so the failure names which scenario diverged.
    ref_name = SCENARIOS[0]
    for scenario, tool in sections.items():
        assert tool == sections[ref_name], (
            f"\n\nTOOLCHAIN differs between {ref_name!r} and {scenario!r}. It "
            "is rendered from `facts.cwd_repo_dir` alone and knows nothing "
            "about where the auditor stands or which repo the PR is in, so a "
            "scenario-dependent sentence here is a claim it cannot support in "
            "the branch it is not being read in.\n"
            f"--- {ref_name} ---\n{sections[ref_name]}\n"
            f"--- {scenario} ---\n{tool}"
        )
    assert len(sections) == len(SCENARIOS) >= 5, (
        f"only {len(sections)} scenario(s) were driven; an equality over a "
        "handful of them pins independence of the axes they happen to vary "
        "and nothing else — which is exactly how round 6 walked this guard"
    )


def test_emit_claims_refuses_an_audited_value_its_own_parser_cannot_read():
    """🔴 REGRESSION. Red at `dd601793`, which truncated it in silence.

    Measured there through the real code: `--audited "abc 123"` emitted
    ``audited=abc 123..<head>``, which this script's OWN parser reads back as
    `from=''`, `to='abc'` — so the next round diffs `abc..HEAD`. No warning at
    emit, none reported malformed at parse, and the self-range guard cannot
    fire. `--audited e06461f7..dd601793` corrupted `<to>` instead, which
    cascades into the round after that.

    🔴 This is the round-3 bug re-opened, and the brief ITSELF reproduced it:
    `degenerate-range-causes` told the operator to re-emit "with `--audited
    <the tip that round actually read>`", so pasting the placeholder yields
    `from=''`, `to='<the'`.
    """
    bad = (
        ("abc 123", "whitespace: cut at the first space"),
        ("<the tip that round actually read>", "the brief's own placeholder"),
        ("e06461f7..dd601793", "a RANGE where a single sha belongs"),
    )
    for value, why in bad:
        rc, out, err = run_main(
            ["900", "--round", "1", "--emit-claims", "--audited", value]
        )
        assert rc != 0, (
            f"\n\n--audited {value!r} ({why}) was accepted: rc {rc}. The block "
            f"it would emit does not round-trip through this script's own "
            f"parser, and the NEXT round anchors its whole delta on that "
            f"field.\nstdout:\n{out[-600:]}"
        )
        assert "```audit-claims" not in out, (
            f"--audited {value!r} was refused but the malformed block was "
            "printed anyway, which is what an operator pastes"
        )
        assert ad.EMIT_REFUSAL_HEADER in err and value in err, (
            f"the refusal does not name the value it rejected:\n{err}"
        )

    # 🔴 THE OTHER SOURCE, so the guard is not keyed to the flag: `emit_from`
    # is `emit_anchor(newest)` when `--audited` is omitted, recovered from a
    # block a human typed into a PR comment. `audited=a..b..c` splits on the
    # FIRST dot-pair, so `<to>` comes back holding a range — and this round
    # would copy that corruption forward into its own block.
    corrupt = (
        "```audit-claims round=2 audited=aaaa1111..bbbb2222..cccc3333\n"
        "1. a claim whose previous block carried two dot-pairs\n"
        "```"
    )
    rc, out, err = run_main(
        ["900", "--round", "3", "--emit-claims"], comments=[corrupt]
    )
    assert rc != 0 and ad.EMIT_REFUSAL_HEADER in err, (
        "\n\na corrupt anchor INHERITED from the previous block is copied "
        f"forward in silence; the flag is not the only way in. rc {rc}\n{err}"
    )

    # 🔴 An EMPTY value is the fifth measured row: falsy, so it fell through to
    # the fallback and the flag was ignored in silence.
    rc, out, err = run_main(["900", "--round", "1", "--emit-claims", "--audited", ""])
    assert rc != 0 and ad.EMIT_REFUSAL_HEADER in err, (
        f"`--audited ''` was ignored rather than refused: rc {rc}\n{err}"
    )
    # 🔴 AND IT MUST GET ITS OWN DIAGNOSIS, not merely a refusal. Round 7's
    # whitespace check subsumes the OUTCOME — `"".split() != [""]` — so with
    # the empty branch deleted the run still exits 4, and mutant Z7 SURVIVED a
    # test that asked only for a non-zero rc. The two failures need different
    # explanations: an empty value would be silently IGNORED (falling back to
    # the previous block's `<to>`, or to the bare round-1 spelling), which is
    # nothing to do with whitespace leaving the header line. Telling an
    # operator about newlines when they passed `""` sends them to the wrong fix.
    assert "EMPTY value" in err and "indistinguishable from the flag being" in err, (
        "\n\n`--audited ''` was refused, but not as an EMPTY value — the "
        "explanation it got belongs to a different failure:\n" + err
    )

    # 🔴 NEGATIVE CONTROL — a checker that rejects everything is as useless as
    # one that accepts everything. A well-formed sha still emits.
    rc, out, err = run_main(
        ["900", "--round", "1", "--emit-claims", "--audited", "abcd1234"]
    )
    assert rc == 0, err
    blocks, malformed = ad.parse_claims_blocks([out[out.rindex("```audit-claims"):]])
    # 🔴 DELIBERATELY NOT `audited_from == "abcd1234"`. That is the claim
    # `..._audited_supplies_the_tip_a_round_one_emit_cannot_derive` owns, and
    # re-asserting it here made mutants N3 and Y7 kill this row for someone
    # else's reason — measured as EXTRA-KILLER. What this control claims is
    # narrower and is the whole claim: a legitimate value is not rejected.
    assert not malformed and len(blocks) == 1, (
        f"the guard rejected a legitimate anchor: {blocks}, {malformed}"
    )

    # 🔴 NAMED BLIND SPOT, asserted so it cannot read as covered: a well-formed
    # token that is no commit round-trips and is NOT caught.
    #
    # 🔴 THE REASON PRINTED HERE FOR THREE ROUNDS WAS FALSE — see
    # `FALSE_BLIND_SPOT_RATIONALES`, which now holds it and is the only place
    # it may appear. It runs
    # seven read-only `git -C <that checkout>` commands, two of which resolve
    # this very token there (`rev-list --count <anchor>..HEAD`, `log --numstat
    # … <anchor>..HEAD`). Reading is not writing, and the gap has a different
    # cause: this script never FETCHES, so the assembly checkout may be missing
    # an object that is fine on the PR, and a `cat-file` refusal there would
    # reject a CORRECT value. The cost of leaving it open is bounded and loud —
    # `rev-list` exits 128 and the ledger says COULD NOT MEASURE, naming the
    # command, one round later.
    rc, out, err = run_main(
        ["900", "--round", "1", "--emit-claims", "--audited", "zzzzzzzz"]
    )
    assert rc == 0, (
        "a non-sha token that round-trips cleanly is outside this guard's "
        "reach, and the docstring says so — if it is now caught, widen the "
        "claim rather than leaving this assertion inverted"
    )


# 🔴 Values whose ONLY defect is SURROUNDING whitespace. What the operator
# meant is unambiguous for every one of them — the stripped token — so an
# emitted block can be held to carrying exactly that, which is a stronger claim
# than "it exited non-zero". A value with an INTERIOR space (`abc 123`) is not
# here: there is no single sha it unambiguously means, and its refusal is owned
# by the test above.
SURROUNDING_WHITESPACE_AUDITED = (
    ("aaaa1111\n", "a TRAILING NEWLINE — the one that moves content off the "
                   "header line"),
    ("aaaa1111\r\n", "a CRLF line ending, as a Windows editor or a pasted "
                     "shell capture produces"),
    ("aaaa1111\n\n", "two newlines: the second lands a blank line in the body"),
    ("aaaa1111 ", "a trailing space"),
    (" aaaa1111", "a leading space"),
    ("\taaaa1111", "a leading tab"),
)


def test_emit_claims_refuses_an_audited_value_whose_whitespace_leaves_the_line():
    """🔴 REGRESSION. Red at `3619fe68` for a TRAILING NEWLINE.

    Measured there through `main()`: `--audited $'aaaa1111\\n'` exited **0** and
    printed a block reading ``audited=aaaa1111`` — the BARE round-1 spelling.
    `<to>` was lost onto the next line as a stray `..<head>`, and
    `parse_claims_blocks` reported `malformed=[]`. Round 3 then reads
    `emit_anchor` = `aaaa1111`, emits `audited=aaaa1111..<head>`, and round 4
    re-audits round 2's fixes on top of round 3's — silently, one round
    downstream.

    🔴 WHY THE EXISTING ROUND TRIP CANNOT SEE IT, which is why the fix is an
    INPUT check and not a wider round trip: `emitted_block_reads_back_as_written`
    compares the printed header LINE against a parse of that same printed header
    LINE. `_EMITTED_AUDITED` is `re.M` + `$`; `parse_claims_blocks` reads line by
    line. Both sides are line-oriented, so anything pushed onto the next line is
    invisible to both and they agree — a control built out of the step it
    doubts. The empty check misses it too: `.strip()` is truthy here.

    🔴 AND THE OBVIOUS PREDICATE DOES NOT WORK. `len(value.split()) != 1` is
    **1** for `'aaaa1111\\n'`, for `'aaaa1111 '` and for `'aaaa1111\\r'` alike,
    because `str.split()` with no argument discards empty fields. Asserted below
    against the real values, so a future simplification to that spelling fails
    here rather than silently re-opening the hole.
    """
    # 🔴 THE STRUCTURAL CLAIM, asserted rather than described: fed the exact
    # block a trailing newline produces, the round trip reports NO problem. If
    # this ever starts returning a reason, the input check is no longer the
    # only thing standing between that value and the emitted block — say so
    # rather than leaving this reading as coverage it does not provide.
    blind = (
        "```audit-claims round=1 audited=aaaa1111\n"
        f"..{FAKE_HEAD_OID[:8]}\n"
        "1. a claim\n"
        "```"
    )
    assert ad.emitted_block_reads_back_as_written(blind) is None, (
        "the round trip now sees content pushed off the header line. Widen "
        "this test's claim rather than leaving it asserting the opposite"
    )
    # 🔴 THE PREDICATE CONTROL. `len(split()) != 1` cannot distinguish these
    # from a clean sha, so it is not what the script may use.
    assert all(len(v.split()) == 1 for v, _ in SURROUNDING_WHITESPACE_AUDITED), (
        "a value here no longer has the property that makes this test's point "
        "— `len(split())` must be 1 for every row, or the hole it guards is a "
        "different one"
    )

    for value, why in SURROUNDING_WHITESPACE_AUDITED:
        rc, out, err = run_main(
            ["900", "--round", "1", "--emit-claims", "--audited", value]
        )
        # 🔴 THE CONTENT CLAIM FIRST, and it is the one that is red at the
        # base: whatever is emitted must read back as the value that was
        # given. rc alone would be satisfied by a refusal for any reason.
        if "```audit-claims" in out:
            tail = out[out.rindex("```audit-claims"):]
            blocks, malformed = ad.parse_claims_blocks([tail])
            got = (blocks[0].audited_from, blocks[0].audited_to) if blocks else None
            assert not malformed and got == (value.strip(), FAKE_HEAD_OID[:8]), (
                f"\n\n--audited {value!r} ({why}) was ACCEPTED, and the block "
                f"it printed does not carry it.\n"
                f"  wanted `<from>`/`<to>`: {(value.strip(), FAKE_HEAD_OID[:8])}\n"
                f"  reads back as        : {got}\n"
                f"  malformed            : {malformed}\n"
                "  The next round anchors its whole delta on `<from>` and "
                "copies `<to>` forward, and nothing downstream reports this "
                f"malformed.\n{tail}"
            )
        assert rc != 0 and ad.EMIT_REFUSAL_HEADER in err, (
            f"\n\n--audited {value!r} ({why}) was accepted: rc {rc}. It is not "
            "one whitespace-free token, and the emitted header is a single "
            f"LINE.\nstderr:\n{err}"
        )
        assert repr(value) in err or value.strip() in err, (
            f"the refusal does not name the value it rejected:\n{err}"
        )

    # 🔴 NEGATIVE CONTROL — the check must not reject a clean sha. Without it a
    # guard that refuses everything reads as green here.
    rc, out, err = run_main(
        ["900", "--round", "1", "--emit-claims", "--audited", "aaaa1111"]
    )
    assert rc == 0, f"the whitespace check rejected a clean sha:\n{err}"
    tail = out[out.rindex("```audit-claims"):]
    blocks, malformed = ad.parse_claims_blocks([tail])
    assert not malformed and len(blocks) == 1


# 🔴 THE RATIONALES THE BLIND-SPOT NOTE GAVE, AND WHY EACH IS FALSE. Same
# exactly-once contract as `REFUTED_PREMISES`: this ledger is the one place
# either may be written. The SCRIPT must not carry them at all.
FALSE_BLIND_SPOT_RATIONALES = (
    "never resolves a sha",
    "never resolves the sha",
    "refuses to touch the checkout at all",
)


def recording_runner(**kw):
    """A `make_runner` that also records every command it is asked to run."""
    inner = make_runner(**kw)
    seen = []

    def runner(cmd, cwd=None):
        seen.append(list(cmd))
        return inner(cmd, cwd=cwd)

    runner.seen = seen
    return runner


def test_the_blind_spot_rationale_matches_what_the_script_actually_does():
    """🔴 REGRESSION. Red at `3619fe68`, in the SCRIPT and in this module.

    The `--emit-claims` round trip has a named blind spot — a well-formed token
    that is no commit is not caught — and for three rounds both files gave the
    same reason for leaving it open: that this script does no sha resolution in
    the assembly checkout, because it will not touch it. Measured below: it
    issues read-only `git -C <that checkout>` commands, and two of them resolve
    exactly that token there. Reading is not writing.

    🔴 KEEP THE GAP, REPLACE THE REASON — and this test pins only the half it
    can measure. The true reason (no `git fetch` is ever run, so the checkout
    may legitimately lack an object that is fine on the PR, and refusing there
    would be a false positive on a correct value) is prose and is not
    machine-checked. What IS checked is that a rationale contradicted by the
    script's own behaviour cannot be restated: a false cause is how a later
    round closes a hole that was never there, or leaves one it thinks is
    unreachable.
    """
    runner = recording_runner(comments=[CLAIMS_BLOCK_R2])
    rc, out, err = run_main(["900", "--round", "3"], runner=runner)
    assert rc == 0, err

    in_checkout = [c for c in runner.seen if c[:3] == ["git", "-C", FAKE_REPO_DIR]]
    assert in_checkout, (
        "POSITIVE CONTROL: this run issued no `git -C <the assembly checkout>` "
        "command at all, so the measurement below is about nothing"
    )
    resolving = [c for c in in_checkout if any(".." in a for a in c)]
    assert resolving, (
        "\n\nno command resolved a revision RANGE in the assembly checkout, "
        "which is the observation that refutes the old rationale. Commands "
        f"run there:\n" + "\n".join("  " + " ".join(c) for c in in_checkout)
    )
    anchor = CLAIMS_BLOCK_R2.split("audited=")[1].split("..")[0]
    assert any(anchor in a for c in resolving for a in c), (
        f"\n\nno command resolved the `audited=` token {anchor!r} itself. The "
        "blind spot is about THAT token; a range over some other pair would "
        "not make the old rationale false.\n"
        + "\n".join("  " + " ".join(c) for c in resolving)
    )

    # 🔴 AND THE PROSE MAY NOT SAY OTHERWISE. Normalised the same way
    # `surviving_refuted_premises` normalises, and for the same two reasons:
    # these live in wrapped comments and in implicitly-concatenated literals.
    def flat(text):
        return " ".join(re.sub(r'"\s*"', "", text).split())

    script = flat(Path(ad.__file__).read_text(encoding="utf-8"))
    module = flat(_THIS_MODULE_SOURCE)
    # POSITIVE CONTROL for the scan itself.
    assert flat(f"x {FALSE_BLIND_SPOT_RATIONALES[0]} y").count(
        flat(FALSE_BLIND_SPOT_RATIONALES[0])) == 1, (
        "POSITIVE CONTROL: the normalising counter cannot find a phrase it was "
        "just handed, so every zero below would be meaningless"
    )
    for phrase in FALSE_BLIND_SPOT_RATIONALES:
        p = flat(phrase)
        assert script.count(p) == 0, (
            f"\n\n`scripts/audit-dispatch.py` states {phrase!r} of itself, "
            f"{script.count(p)} time(s). It is false — the run above resolved "
            f"{anchor!r} in that checkout with "
            f"`{' '.join(resolving[0])}` — and a comment is a claim too."
        )
        assert module.count(p) == 1, (
            f"\n\nthis module states {phrase!r}, {module.count(p)} time(s). "
            "It may appear exactly once: in FALSE_BLIND_SPOT_RATIONALES, as a "
            "record of a rationale that was measured false."
        )


def test_the_emitted_skeleton_carries_a_legend_for_its_two_fields():
    """🔴 INVARIANT GUARD; its evidence is mutant Z5, not a base ref.

    At `dd601793` there is no legend at all, so a red there is an absence the
    fix adds rather than a wrong answer observed — which this module says
    repeatedly is not evidence of anything.

    The code and the docstrings were already correct and consistent about
    `<from>` and `<to>`; the failure is at the HUMAN end, because a person
    reasons from the emitted block and it carried no key to its own fields. The
    operator wrote them the wrong way round in two consecutive briefs.

    The legend sits OUTSIDE the fence, so the block it annotates must still
    parse — asserted here, because a helpful line that breaks the parser would
    trade one silent failure for another.
    """
    rc, out, err = run_main(
        ["900", "--round", "3", "--emit-claims"], comments=[CLAIMS_BLOCK_R2]
    )
    assert rc == 0, err
    tail = out[out.index("Paste this into the PR comment"):]
    assert "legend:" in tail, f"the emitted block carries no legend:\n{tail}"
    assert "the tip THIS round's audit READ" in tail
    assert "the head THIS round's FIXES produced" in tail
    blocks, malformed = ad.parse_claims_blocks([tail])
    assert not malformed and len(blocks) == 1, (
        f"the legend broke the parser it annotates: {malformed}\n{tail}"
    )
    # 🔴 The two fields are asserted PRESENT, never by value: which sha goes in
    # `<from>` is `emit_anchor`'s claim and its own test's, and naming it here
    # made mutant N3 kill this row for that test's reason.
    assert blocks[0].audited_from and blocks[0].audited_to, (
        f"the legend changed what the block parses to: {blocks[0]!r}"
    )


def test_the_degenerate_cause_list_scopes_the_omitted_flag_to_round_one():
    """🟢 REGRESSION. Red at `dd601793`, where the clause was unscoped.

    The banner offered "the `audited=` block was written with the fix tip in
    its `<from>` position — which is what `--emit-claims` records when
    `--audited` is omitted" flat, for every round. For round >= 2 omitting the
    flag is CORRECT: `emit_from` falls back to `emit_anchor(newest)`, the
    previous block's `<to>`, which IS the tip this round read. An operator
    hitting the banner on a round-3 brief was told their emit was wrong and
    pushed toward hand-typing a value — which is the trigger for the
    `--audited` truncation above.

    The behavioural half (omitting the flag at round >= 2 really is correct) is
    owned by `..._records_the_tip_this_round_audited_not_the_range_anchor`; this
    asserts the PROSE agrees with it, as a relationship between two clauses
    rather than as a phrase.
    """
    text = norm(ad.directive("degenerate-range-causes"))
    i = text.index("`--audited` is omitted")
    tail = text[i:i + 120]
    assert "AT ROUND 1" in tail, (
        f"\n\nthe cause list blames an omitted `--audited` without scoping it "
        f"to round 1:\n    …{tail}…\n"
        "  From round 2 on the omission is correct, so this sends an operator "
        "to hand-type a value into a field whose only safe content is a sha."
    )
    assert "from round 2 on, omitting it is correct" in text, (
        "the clause names the round-1 case but never says what the other case "
        "is, so a reader on round 3 still cannot tell whether it applies"
    )


# --------------------------------------------------------------------------- #
# THE LEDGER
# --------------------------------------------------------------------------- #

def test_the_ledger_shows_the_files_and_refuses_to_classify_them():
    """🔴 The classification is a HUMAN judgement and the script says so.

    A pathspec is wrong in both directions on ordinary names — `':!*test*'`
    swallows `attestation/`, `latest/` and `inspector/` and keeps
    `FooTest.java`. Mutant C5 (classify by pathspec and print an X) kills this.
    """
    rc, out, err = run_main(["900", "--round", "3"], comments=[CLAIMS_BLOCK_R2])
    assert rc == 0, err
    assert "scripts/foo.py" in out and "scripts/tests/test_foo.py" in out
    assert "Classify these files yourself" in out
    assert "payload lines changed THIS round: X" in out, (
        "the ledger line must leave X for the human — a number here would be a "
        "classification the script is not entitled to make"
    )
    assert "attestation" in out and "login.cy.ts" in out, (
        "the counter-evidence for why a pathspec cannot do this is missing"
    )


@pytest.mark.parametrize("kw,expect", [
    ({"rev_list": (128, "", "fatal: bad revision")}, "exited 128"),
    ({"rev_list": (0, "0\n", "")}, "is EMPTY"),
    ({"numstat": (128, "", "unknown option --remerge-diff")}, "exited 128"),
    ({"numstat": (0, "1\t1\tx\n", "warning: unable to write object")},
     "wrote to STDERR"),
])
def test_the_ledger_refuses_a_failed_command_rather_than_printing_zero(kw, expect):
    """🔴 rc 0, silent stderr, non-empty range — all three, or no number.

    Each of these produces a PLAUSIBLE zero: a git without `--remerge-diff`
    exits 128 with empty output; an unwritable object store makes it UNDER-count
    while exiting 0 and announcing itself only on stderr; a range whose commits
    are not in this checkout prints nothing at all with rc 0.
    """
    rc, out, err = run_main(
        ["900", "--round", "3"], comments=[CLAIMS_BLOCK_R2], **kw
    )
    assert rc == 0, err
    assert "COULD NOT MEASURE" in out, (
        "the ledger reported a number from a command that did not earn it"
    )
    assert expect in out, f"the reason does not name the failure. Got:\n{out}"
    assert "payload lines changed THIS round" not in out, (
        "a ledger line was printed under a COULD NOT MEASURE — the two must "
        "not appear together, or the operator copies the line anyway"
    )


def test_the_cumulative_figure_is_not_measured_without_a_round_one_anchor():
    """An unmeasurable quantity is reported as unmeasured, never substituted."""
    bare = (
        "```audit-claims round=2 audited=bbbb2222\n1. something\n```"
    )
    rc, out, err = run_main(["900", "--round", "3"], comments=[bare])
    assert rc == 0, err
    assert "(since round 1: Y)" in out
    assert "NOT MEASURED" in out
    assert "no `audit-claims` block carried a round-1 anchor sha" in out, (
        "with genuinely no anchor, the no-anchor reason is the TRUE one and "
        "must still be the one printed"
    )


def test_a_failed_cumulative_measurement_does_not_print_a_false_cause():
    """🔴 REGRESSION. Red at `abc41024`, which printed a specific, false reason.

    When the cumulative measurement failed, `cumulative` stayed None and the
    brief stated "NOT MEASURED: no `audit-claims` block carried a round-1 anchor
    sha" — printed even when one DID. The operator is then sent to add an anchor
    that already exists, while the real fault (a sha `rev-list` cannot resolve)
    goes unnamed.

    Two mechanisms, one observable — the exact rule this module cites everywhere
    else to justify its own COULD-NOT-MEASURE design.

    Driven by failing the SECOND `rev-list` only: the per-round measurement
    succeeds (so a ledger is rendered) and the cumulative one, from the anchor,
    does not.
    """
    calls = {"n": 0}
    base = make_runner(comments=[CLAIMS_BLOCK_R2])

    def runner(cmd, cwd=None):
        if cmd[:2] == ["git", "-C"] and cmd[3] == "rev-list":
            calls["n"] += 1
            if calls["n"] > 1:
                return 128, "", "fatal: bad revision 'aaaa1111..HEAD'"
        return base(cmd, cwd)

    rc, out, err = run_main(["900", "--round", "3"], runner=runner)
    assert rc == 0, err
    assert calls["n"] >= 2, (
        "the cumulative measurement never ran, so this test proves nothing "
        "about what happens when it fails"
    )
    assert "(since round 1: Y)" in out and "NOT MEASURED" in out
    assert "no `audit-claims` block carried a round-1 anchor sha" not in out, (
        "\n\nthe brief blamed a MISSING round-1 anchor for a measurement that "
        "failed WITH one in hand. That sends the operator to add a block that "
        "is already there, and leaves the real fault unnamed."
    )
    assert "fatal: bad revision" in out, (
        "the real reason the cumulative figure is missing is not reported"
    )
    assert "not a missing anchor" in out


def test_the_ledger_says_the_base_was_not_fetched():
    """The script cannot fetch (that would be a write), so it says so.

    A stale `<base>` re-reports upstream work as this round's payload — measured
    at 201 lines where the truth was 1.
    """
    rc, out, err = run_main(["900", "--round", "3"], comments=[CLAIMS_BLOCK_R2])
    assert rc == 0, err
    assert "does not fetch" in out and "stale base" in out


# --------------------------------------------------------------------------- #
# REFUSAL 2's opposite number: WARN, NEVER BLOCK
# --------------------------------------------------------------------------- #

def test_missing_clause_check_warns_and_never_blocks(monkeypatch, tmp_path):
    """🔴 Warn-only, deliberately.

    This check exists for a hand-edited `--out` file. `claude/RULES.md`: a
    permanently-red gate is worse than no gate — it trains everyone to click
    through. Blocking here would refuse to run over an edit that was deliberate.
    Mutant C6 (raise instead of return) kills this test.
    """
    real_render = ad.render_brief
    # By ID, not by index: an index makes this test's outcome depend on which
    # OTHER clause a mutation deleted, which shows up in a sweep as this test
    # dying for the wrong reason.
    target = next(c for c in ad.INVARIANT_CLAUSES if c.id == "no-fetch")

    def lossy(facts):
        text = real_render(facts)
        return text.replace(target.text, "(hand-edited away)")

    monkeypatch.setattr(ad, "render_brief", lossy)
    out_file = tmp_path / "brief.md"
    rc, _, err = run_main(["900", "--out", str(out_file)])

    assert rc == 0, "the missing-clause check BLOCKED; it must only warn"
    assert "missing invariant clause(s)" in err and "no-fetch" in err
    assert "WARNING, not a refusal" in err
    assert out_file.read_text(encoding="utf-8"), (
        "the brief was not written — a warning must not suppress the output"
    )


def test_missing_clauses_is_a_pure_function_over_the_text():
    full = "\n".join(c.text for c in ad.INVARIANT_CLAUSES)
    assert ad.missing_clauses(full) == []
    assert ad.missing_clauses("") == [c.id for c in ad.INVARIANT_CLAUSES]
    assert ad.missing_clauses(
        full.replace(ad.INVARIANT_CLAUSES[0].text, "")
    ) == [ad.INVARIANT_CLAUSES[0].id]
    # A RE-WRAP is not a loss. A brief that has been through an editor or a
    # paste has different line breaks and the same instructions.
    rewrapped = "\n".join(
        "\n".join(c.text.split(" ")) for c in ad.INVARIANT_CLAUSES
    )
    assert ad.missing_clauses(rewrapped) == [], (
        "a pure re-wrap was reported as missing clauses — the check would then "
        "be red on every hand-edited brief, which is the permanently-red gate "
        "claude/RULES.md says trains people to click through"
    )


def test_the_clause_check_runs_over_a_file_that_can_actually_be_lossy(tmp_path):
    """🔴 REGRESSION. Red at `abc41024`, where `--check` did not exist.

    The guard's own docstring said "it exists for a hand-edited `--out` file" —
    but nothing read `--out` back. Its only caller passed the string
    `render_brief` had just built out of `INVARIANT_CLAUSES`, so every clause
    was present BY CONSTRUCTION and no user input could make it fire. The suite
    reached it only by monkeypatching `render_brief` to a lossy stub: the
    `unreachable-guards` shape in `claude/RULES.md`.

    It mattered. The hand-written brief that dispatched the audit of this script
    HAD been edited — several clauses shortened, the checklist paraphrased — and
    the shipped check could not notice.

    So: round-trip a real brief through a real file, degrade it the way a human
    editing it would, and check that from the file.
    """
    generated = tmp_path / "brief.md"
    rc, _, err = run_main(["900", "--out", str(generated)])
    assert rc == 0, err
    text = generated.read_text(encoding="utf-8")

    # Control: the generated file as written is complete, and says so.
    # 🔴 The count is READ, not literal. A literal `7` here made every
    # clause-deletion mutant (D1-D7) and the clause-addition mutant (A1)
    # extra-kill this test, which is a coupling to the SIZE of a constant this
    # test does not own — `test_the_rendered_section_holds_exactly_the_
    # ledgered_clauses` owns that, against the independent ledger.
    rc, out, err = run_main(["--check", str(generated)])
    assert rc == 0
    assert f"all {len(ad.INVARIANT_CLAUSES)} invariant clause(s) present" in out, out
    assert "missing invariant clause(s)" not in err

    # Now degrade it the way the measured hand-edit did: shorten two clauses.
    dropped = ad.INVARIANT_CLAUSES[0]
    shortened = ad.INVARIANT_CLAUSES[2]
    edited = tmp_path / "edited.md"
    edited.write_text(
        text.replace(dropped.text, "").replace(
            shortened.text, "**A clean round ends the ladder.**"
        ),
        encoding="utf-8",
    )
    rc, out, err = run_main(["--check", str(edited)])
    assert rc == 0, "the clause check BLOCKED; it must only warn"
    assert "missing invariant clause(s)" in err, (
        f"\n\n`--check` did not notice two clauses removed from a real brief "
        f"file. stderr was:\n{err}"
    )
    assert dropped.id in err and shortened.id in err
    assert "WARNING, not a refusal" in err


def test_the_out_file_is_read_back_and_checked(monkeypatch, tmp_path):
    """The second real input: what landed on DISK, not the string in memory.

    A write that lost bytes, or a `--out` path something else rewrites, is
    exactly what this check is described as guarding and could not see.
    """
    out_file = tmp_path / "brief.md"
    real_write = Path.write_text
    target = next(c for c in ad.INVARIANT_CLAUSES if c.id == "do-not-merge")

    def lossy_write(self, data, *a, **kw):
        # Model a write that lands short — the file, not the brief, is wrong.
        if str(self) == str(out_file):
            data = data.replace(target.text, "(lost in the write)")
        return real_write(self, data, *a, **kw)

    monkeypatch.setattr(Path, "write_text", lossy_write)
    rc, _, err = run_main(["900", "--out", str(out_file)])

    assert rc == 0
    assert "missing invariant clause(s)" in err and target.id in err, (
        "\n\nthe brief on disk was missing a clause and the check passed — it "
        "was still reading the in-memory string, where every clause is present "
        "by construction"
    )
    assert str(out_file) in err, (
        "the warning does not say WHICH artifact is missing the clause"
    )


# --------------------------------------------------------------------------- #
# The parser, driven directly
# --------------------------------------------------------------------------- #

def test_parse_claims_blocks_reads_only_the_fence():
    blocks, malformed = ad.parse_claims_blocks([comment_with_prose()])
    assert not malformed
    assert len(blocks) == 1
    assert blocks[0].items == [
        "the collapsed branch was split so each reports its own state",
        "the over-stated quantifier was replaced with the measured floor",
    ]
    assert blocks[0].audited_from == "aaaa1111"
    assert blocks[0].audited_to == "bbbb2222"


def test_parse_claims_blocks_reports_a_bad_header_rather_than_skipping_it():
    blocks, malformed = ad.parse_claims_blocks(
        ["```audit-claims round=oops audited=aa..bb\n1. x\n```"]
    )
    assert blocks == []
    assert malformed and "does not carry both" in malformed[0]


def test_a_comment_with_no_fence_yields_nothing_and_no_false_malformation():
    blocks, malformed = ad.parse_claims_blocks(
        ["just prose, mentioning audit-claims and round=3 in passing"]
    )
    assert blocks == [] and malformed == []


@pytest.mark.parametrize("label,text,expect", [
    (
        "an unclosed fence",
        "```audit-claims round=2 audited=aa..bb\n1. a claim\n",
        "never CLOSED",
    ),
    (
        "a 4-backtick opener closed with 3",
        "````audit-claims round=2 audited=aa..bb\n1. a claim\n```\n",
        "never CLOSED",
    ),
])
def test_a_fence_the_parser_cannot_read_is_reported_not_skipped(label, text, expect):
    """🔴 REGRESSION. Red at `abc41024`, where all of these vanished silently.

    The module's own comment promised "A block whose header does not parse is
    reported as MALFORMED rather than skipped: skipping it silently would
    produce the same observable as 'no block at all', and those need different
    fixes." That was false for four shapes, because the old regex required the
    closing fence to be byte-identical to the opener and dropped anything else
    with no report. The refusal then said "no `audit-claims` block in any of the
    1 comment(s) read" — false, and it points at the wrong fix (write a block,
    rather than close the one you wrote).

    A closing fence SHORTER than the opener does not close it under CommonMark
    either, so the 4-then-3 case really is unclosed and is now named as such.
    """
    blocks, malformed = ad.parse_claims_blocks([text])
    assert blocks == [], f"{label}: unexpectedly parsed"
    assert malformed, (
        f"\n\n{label} was skipped SILENTLY. That produces the same observable "
        "as 'nobody posted a block', and the two need opposite fixes."
    )
    assert expect in malformed[0], malformed


def test_a_longer_closing_fence_is_a_valid_close_and_is_read():
    """The direction that is valid CommonMark and renders CLOSED on GitHub.

    A 3-backtick opener closed with 4 was ALSO dropped silently. The block is
    well-formed, so the right answer is to read it — not to report it.
    """
    blocks, malformed = ad.parse_claims_blocks(
        ["```audit-claims round=2 audited=aa..bb\n1. a claim\n````\n"]
    )
    assert not malformed, malformed
    assert len(blocks) == 1 and blocks[0].items == ["a claim"]


def test_a_claim_that_wraps_onto_a_continuation_line_keeps_its_tail():
    """🔴 REGRESSION. Red at `abc41024`, where the continuation was DROPPED.

    This one does not fail, it CHANGES THE CLAIM — and the next round is framed
    on the truncated text, which is the one thing the framing rule above says
    must be exact.
    """
    blocks, malformed = ad.parse_claims_blocks([
        "```audit-claims round=2 audited=aa..bb\n"
        "1. the collapsed branch was split so each\n"
        "   reports its own state\n"
        "2. a second claim\n"
        "```"
    ])
    assert not malformed, malformed
    assert blocks[0].items == [
        "the collapsed branch was split so each reports its own state",
        "a second claim",
    ], (
        "\n\na wrapped claim lost everything after its first line. The next "
        "round is framed on exactly this text."
    )


def test_a_block_cut_short_by_a_nested_fence_is_reported():
    """🔴 REGRESSION. Red at `abc41024`: everything after the nested fence went.

    A fence inside the body ends the block — CommonMark and GitHub agree — so
    the claims after it are outside it. The block still parses, with FEWER
    claims, and nothing said so.
    """
    blocks, malformed = ad.parse_claims_blocks([
        "```audit-claims round=2 audited=aa..bb\n"
        "1. tightened the guard, was:\n"
        "```py\n"
        "if x: pass\n"
        "```\n"
        "2. the claim nobody ever read\n"
        "```\n"
    ])
    assert len(blocks) == 1, "the readable half of the block was lost too"
    assert any("CUT SHORT by a nested fence" in m for m in malformed), (
        f"\n\nclaims after a nested fence were dropped with no report. "
        f"malformed was {malformed}"
    )


def test_a_malformed_fence_beside_a_readable_block_still_warns(tmp_path):
    """The report has to REACH the operator when a usable block exists.

    The refusal only fires when there is NO block, so a comment holding one
    readable block and one unreadable fence would otherwise pass as complete.
    """
    rc, out, err = run_main(
        ["900", "--round", "3"],
        comments=[
            CLAIMS_BLOCK_R2,
            "```audit-claims round=2 audited=cc..dd\n1. never closed\n",
        ],
    )
    assert rc == 0, err
    assert "could not read cleanly" in err and "never CLOSED" in err, (
        f"\n\nan unreadable fence beside a readable block was silent. "
        f"stderr:\n{err}"
    )
    assert "the collapsed branch was split" in out, (
        "the warning suppressed the block that DID parse"
    )


def test_the_refusal_says_which_comment_kinds_it_cannot_see():
    """`gh pr view --json comments` returns ISSUE comments only.

    A block posted as a review comment, inside a review thread, or in the PR
    body is invisible here — so "no block in any of the N comment(s) read" is a
    claim about a narrower corpus than the operator has in mind.
    """
    _, _, err = run_main(["900", "--round", "3"], comments=[])
    assert "ISSUE comments only" in err
    assert "REVIEW comment" in err and "DESCRIPTION" in err


# --------------------------------------------------------------------------- #
# HERMETICITY + the ledger-of-ledgers
# --------------------------------------------------------------------------- #

def test_nothing_here_spawns_a_subprocess(monkeypatch):
    """Proves the injected seam is the ONLY process boundary.

    If `main` ever reaches around the injected runner — a bare `subprocess.run`
    for one convenient extra fact — this suite would silently start depending on
    the host, the network and a real PR.
    """
    def explode(*a, **kw):
        raise AssertionError(
            "audit-dispatch.py spawned a process during a test that injected a "
            f"runner: {a!r}"
        )

    monkeypatch.setattr(ad.subprocess, "run", explode)
    rc, out, err = run_main(
        ["900", "--round", "3", "--emit-claims"], comments=[CLAIMS_BLOCK_R2]
    )
    assert rc == 0, err
    assert out


def test_a_gh_failure_is_reported_and_not_papered_over():
    rc, out, err = run_main(["900"], gh_rc=1)
    assert rc != 0 and out.strip() == ""
    assert "gh pr view 900" in err and "failed" in err


# The two ledgers the module docstring commits to.
#
# 🔴 RED_AT_BASE WAS EMPTY, AND IS NOT ANY MORE. It was empty because the script
# did not exist at this branch's base, so nothing here could be regression
# coverage for a shipped bug. Round 2's adversarial audit found nine defects in
# the shipped script, every one reproduced live, and each test below was watched
# to FAIL against `abc41024` — the branch head that carried them. That is a real
# base with a real bug in it, so these belong in this list and not the other.
# The list is the MEASURED one — `git show abc41024:scripts/audit-dispatch.py`
# into a scratch tree with this module copied in unchanged, run under
# `PYTHONDONTWRITEBYTECODE=1 -p no:cacheprovider`: **24 failed, 34 passed**
# (24 node ids, 21 functions; three of them are parametrised rows of one).
#
# 🔴 THERE ARE NOW TWO BASES, AND ONE SHARED REF WOULD BE A LIE ABOUT BOTH.
# Round 3's audit found defects in the ROUND-2 tree, so its regression tests
# were watched red at `d9eb36a8`, not at `abc41024` — where several of them
# would fail for a different reason entirely (the code they exercise did not
# exist). "Watched red" is a claim about a SPECIFIC tree, so each set carries
# its own, and the partition test refuses a set with no ref or a ref with no
# set.
RED_AT_BASE_R2: frozenset[str] = frozenset({
    "test_a_fork_pr_against_this_repo_is_not_treated_as_cross_repo",
    "test_the_prs_repo_is_read_from_the_url_not_from_the_head_repo",
    "test_an_undeterminable_repo_gets_its_own_branch_not_the_same_repo_one",
    "test_the_toolchain_gates_the_auditors_copy_not_the_shared_checkout",
    "test_the_ledger_refuses_to_measure_a_checkout_that_is_not_the_pr",
    "test_the_range_does_not_hand_out_a_head_that_is_not_the_prs_head",
    "test_emit_claims_stamps_the_prs_head_not_the_local_checkouts",
    "test_a_failed_cumulative_measurement_does_not_print_a_false_cause",
    "test_missing_clauses_is_a_pure_function_over_the_text",
    "test_the_clause_check_runs_over_a_file_that_can_actually_be_lossy",
    "test_the_out_file_is_read_back_and_checked",
    "test_a_fence_the_parser_cannot_read_is_reported_not_skipped",
    "test_a_longer_closing_fence_is_a_valid_close_and_is_read",
    "test_a_claim_that_wraps_onto_a_continuation_line_keeps_its_tail",
    "test_a_block_cut_short_by_a_nested_fence_is_reported",
    "test_a_malformed_fence_beside_a_readable_block_still_warns",
    "test_the_refusal_says_which_comment_kinds_it_cannot_see",
    "test_emit_claims_prints_a_block_this_scripts_own_parser_accepts",
    "test_a_round_one_block_anchors_the_next_rounds_cumulative_figure",
    # 🔴 THREE ENTRIES WHOSE REDNESS IS WEAKER EVIDENCE THAN THE REST, said
    # here rather than left for a reader to assume otherwise. Every other entry
    # above fails at the base by exercising the defect and observing the wrong
    # ANSWER. These three fail at the base because they assert text or a branch
    # the fix ADDED, so their red is a restatement of the diff:
    #   * `..._names_the_tier_the_merge_gates_on` is a PRE-EXISTING guard whose
    #     expected strings changed with the fix (`nix build <your worktree>#…`),
    #     not new coverage;
    #   * `..._range_says_head_was_verified_when_it_was` is the
    #     does-not-fire-spuriously control for the head check — necessary (a
    #     check wired to always fail would satisfy its sibling), and not itself
    #     a detector;
    #   * `..._refusal_says_which_comment_kinds_it_cannot_see` pins a sentence.
    #     The gap it documents — `--json comments` returns issue comments only —
    #     is NOT closed by this PR, and no test here can close it.
    "test_the_toolchain_section_names_the_tier_the_merge_gates_on",
    "test_the_range_says_head_was_verified_when_it_was",
})

# 🔴 ROUND 3's base is the ROUND-2 TREE. Every entry here was watched to FAIL
# against `d9eb36a8` by restoring `git show d9eb36a8:scripts/audit-dispatch.py`
# into a scratch tree with THIS module copied in unchanged, under
# `PYTHONDONTWRITEBYTECODE=1 -p no:cacheprovider`. The measured counts are in
# the round-3 section of the module docstring.
#
# They are NOT listed under `abc41024`: several exercise code that round 2
# ADDED (the head check, the read-back), so a red there would be an import-time
# accident rather than the defect being observed.
RED_AT_BASE_R3: frozenset[str] = frozenset({
    "test_the_range_is_generated_from_the_previous_rounds_audited_sha",
    # Pre-existing, and it MOVED LEDGERS this round: its anchor assertion was
    # `cccc3333..HEAD` (the newest block's `<to>`), which is the defect. It now
    # asserts `bbbb2222..HEAD` and observes the wrong answer at `d9eb36a8`.
    "test_the_newest_claims_block_wins",
    "test_the_ledger_measures_from_the_tip_the_previous_round_audited",
    "test_a_degenerate_self_range_is_reported_not_rendered_as_a_clean_diff",
    "test_a_degenerate_self_range_is_named_by_the_ledger_too",
    "test_emit_claims_warns_when_the_block_it_writes_is_a_self_range",
    "test_an_empty_range_does_not_name_a_cause_the_head_check_refutes",
    "test_the_unknown_head_sha_reason_names_one_cause_not_two",
    # 🔴 ONE WEAKER ENTRY, said rather than left to assume: this is the
    # does-not-fire-spuriously control for the degenerate-range banner, and it
    # is red at `d9eb36a8` because the anchor it asserts (`aaaa1111..HEAD`)
    # is the FIXED one. Its red is a restatement of the diff, not a defect
    # observed — the same class as the three flagged under `abc41024`.
    "test_the_range_says_head_was_verified_when_it_was",
})

# 🔴 ONE TEST, TWO BASES. `..._range_says_head_was_verified_when_it_was` is red
# at both refs for two DIFFERENT reasons, so it is listed under both — which is
# why the per-ref sets may overlap each other even though neither may overlap
# the guard ledger.
# 🔴 ROUND 4's base is the ROUND-3 TREE, `e06461f7`. Measured the same way —
# `git show e06461f7:scripts/audit-dispatch.py` into a scratch tree with THIS
# module copied in unchanged, `PYTHONDONTWRITEBYTECODE=1 -p no:cacheprovider`:
#
#     15 failed, 68 passed
#
# and only these FIVE are regression coverage. The other TEN fail there for
# reasons this module already says are not evidence: `SystemExit: 2` from an
# argparse flag the fix introduced (2), `AttributeError` for a function it
# introduced (1), and an assertion over a directive, a worktree state or a
# ledger that does not exist in that tree (7). They are filed as guards, with
# the mutation battery as their evidence.
#
# 🔴 TWO OF THE FIVE ONLY BECAME REGRESSION COVERAGE AFTER BEING REWRITTEN, and
# it is recorded because the first draft of each was the vacuous shape:
#   * the private-worktree test sliced on the round-4 heading `## THE CHECKOUT`
#     and died at the base with `ValueError: substring not found` — for want of
#     a heading the fix introduced. `checkout_section` now matches the OLD
#     spelling too, and it fails there on the false SHARED claim instead;
#   * its assertions were ordered positive-first, so it failed on the absence
#     of the word "PRIVATE". The negatives run first now, so the red is the
#     wrong ANSWER.
RED_AT_BASE_R4: frozenset[str] = frozenset({
    "test_a_round_one_emit_claims_says_head_is_an_assumption_not_a_measurement",
    "test_a_degenerate_range_does_not_blame_a_checkout_it_verified",
    "test_a_private_worktree_is_not_described_as_shared_and_is_not_absolved",
    "test_the_where_to_work_section_does_not_call_a_private_worktree_the_clone",
    "test_an_uppercase_audited_sha_still_trips_the_degenerate_guard",
})

# 🔴 ROUND 5's base is the ROUND-4 TREE, `dd601793`. Measured the same way —
# `git show dd601793:scripts/audit-dispatch.py` into a scratch tree with THIS
# module copied in unchanged, `PYTHONDONTWRITEBYTECODE=1 -p no:cacheprovider`.
# The counts are in the round-5 section of the module docstring.
#
# 🔴 TWO OF ROUND 5's SIX NEW TESTS ARE **NOT** HERE, and it is the same
# distinction every earlier round had to make: their red at `dd601793` is an
# ABSENCE the fix adds (no no-write sentence in two of the three states; no
# legend at all), not a wrong answer observed. They are filed as guards with
# mutants Z1/Z2 and Z5 as their evidence.
RED_AT_BASE_R5: frozenset[str] = frozenset({
    "test_no_checkout_state_grants_write_permission_over_the_assembly_checkout",
    "test_the_toolchain_reason_is_true_in_every_scenario",
    "test_emit_claims_refuses_an_audited_value_its_own_parser_cannot_read",
    "test_the_degenerate_cause_list_scopes_the_omitted_flag_to_round_one",
    # Pre-existing, and it MOVED with the fix: its positives asserted the
    # round-4 wording ("PRIVATE worktree" / "IS worth reporting"). Its two
    # NEGATIVES are untouched and run first, so it is still red at `e06461f7`
    # on the false SHARED claim — it stays filed under that ref, not this one.
})

# 🔴 ROUND 7's base is the ROUND-6 TREE, `3619fe68`. Measured the same way —
# `git show 3619fe68:scripts/audit-dispatch.py` into a scratch tree with THIS
# module copied in unchanged, `PYTHONDONTWRITEBYTECODE=1 -p no:cacheprovider`.
#
# 🔴 ONE OF ROUND 7's FIVE NEW TESTS IS **NOT** HERE, and the distinction is a
# new one: `..._refuted_premise_survives_in_this_modules_prose` scans THIS
# MODULE's prose, and the red-at-base procedure copies this module in
# UNCHANGED. It structurally cannot be red under it — a base script says
# nothing about a base docstring. It is filed as a guard whose evidence is its
# own in-module positive control, and its red WAS measured, separately: the
# base module's prose carries both ledger entries once each (normalised), so
# with the ledger's own copy present each reads 2.
RED_AT_BASE_R6: frozenset[str] = frozenset({
    "test_no_forward_reference_scopes_the_no_write_rule_to_a_denied_state",
    "test_emit_claims_refuses_an_audited_value_whose_whitespace_leaves_the_line",
    "test_the_blind_spot_rationale_matches_what_the_script_actually_does",
    "test_the_range_names_a_sha_and_not_head_when_the_head_is_verified",
})

RED_AT_BASE_REFS: dict[str, frozenset[str]] = {
    "abc41024": RED_AT_BASE_R2,
    "d9eb36a8": RED_AT_BASE_R3,
    "e06461f7": RED_AT_BASE_R4,
    "dd601793": RED_AT_BASE_R5,
    "3619fe68": RED_AT_BASE_R6,
}
RED_AT_BASE: frozenset[str] = frozenset().union(*RED_AT_BASE_REFS.values())

INVARIANT_GUARDS_AND_LEDGERS = frozenset({
    "test_the_invariant_clause_ledger_is_pinned_two_way",
    # 🔴 GREEN at `abc41024`, MEASURED — and it is the guard for finding 5, so
    # the temptation to file it as regression coverage is real and is refused
    # here. The clause TEXTS did not change in this round; what changed is that
    # this module now pins them WHOLE instead of by fragment. Carrying the new
    # ledger back to the base tree therefore passes. Its evidence is not a base
    # ref at all — it is mutants W1-W5, five rewords that INVERT their clause
    # and each kill this test alone.
    "test_each_clause_carries_the_instruction_its_ledger_entry_names",
    "test_every_clause_is_emitted_verbatim_in_both_kinds_of_brief",
    "test_the_rendered_section_holds_exactly_the_ledgered_clauses",
    "test_control_the_extractor_can_see_an_unledgered_bullet",
    "test_control_a_clause_deleted_from_the_constant_is_detected",
    "test_the_delta_refusal_exits_non_zero_and_emits_no_brief",
    "test_the_refusal_names_what_it_looked_for_and_where",
    "test_the_refusal_fires_for_a_malformed_block_and_says_which_way",
    "test_a_first_round_needs_no_claims_block",
    "test_the_brief_carries_the_claims_and_not_the_reasoning_around_them",
    "test_cross_repo_tells_the_agent_to_worktree_the_prs_repo_itself",
    "test_same_repo_recommends_the_isolation_flag_and_does_not_hand_roll",
    "test_the_cross_repo_decision_comes_from_the_repos_not_from_prose",
    "test_the_shared_checkout_state_is_reported_with_the_it_moves_warning",
    "test_the_ledger_shows_the_files_and_refuses_to_classify_them",
    "test_the_ledger_refuses_a_failed_command_rather_than_printing_zero",
    "test_the_cumulative_figure_is_not_measured_without_a_round_one_anchor",
    "test_the_ledger_says_the_base_was_not_fetched",
    "test_missing_clause_check_warns_and_never_blocks",
    "test_parse_claims_blocks_reads_only_the_fence",
    "test_parse_claims_blocks_reports_a_bad_header_rather_than_skipping_it",
    "test_a_comment_with_no_fence_yields_nothing_and_no_false_malformation",
    "test_nothing_here_spawns_a_subprocess",
    "test_a_gh_failure_is_reported_and_not_papered_over",
    "test_the_two_ledgers_partition_this_modules_tests",
    # ------------------------------------------------------------------- #
    # Round 3's guards. None is red at `d9eb36a8` and each says why in its
    # own docstring; their evidence is the mutation battery.
    # ------------------------------------------------------------------- #
    # GREEN at `d9eb36a8` by ACCIDENT: the base used one anchor for both the
    # reader and the writer, and `<to>` happens to be right on the writer's
    # side. This pins that, so the obvious "one anchor everywhere"
    # simplification of the round-3 fix goes red. Mutant N3.
    "test_emit_claims_records_the_tip_this_round_audited_not_the_range_anchor",
    # The BEHAVIOURAL half is green at `d9eb36a8` — the bare round-1 spelling
    # was already read correctly there. The test does fail at that ref, but
    # with `AttributeError: ... no attribute 'range_anchor'` from the unit
    # assertions at its foot, which is not the defect being observed. Mutant
    # N2 (drop the bare fallback) is what proves it executes.
    "test_a_bare_round_one_audited_sha_still_anchors_the_next_round",
    # 🔴 These four DO fail at `d9eb36a8` — with `AttributeError: module
    # 'audit_dispatch' has no attribute 'SECTION_DIRECTIVES'`. That is an
    # error for want of a name the fix introduced, not the defect being
    # observed, so they are NOT counted as regression coverage. Mutants X1-X3,
    # XA, and the XS survives-control are their evidence.
    "test_the_section_directive_ledger_is_pinned_two_way",
    "test_each_section_directive_carries_the_instruction_its_ledger_entry_names",
    "test_every_section_directive_is_emitted_verbatim_in_the_brief_that_owns_it",
    "test_control_a_directive_deleted_from_the_constant_is_detected",
    # ------------------------------------------------------------------- #
    # Round 4's guards. Each says in its own docstring why it is one, and
    # every one of them WAS run against `e06461f7` — they are here because of
    # HOW they failed there, not because nobody looked.
    # ------------------------------------------------------------------- #
    # `--audited` does not exist at `e06461f7`, so both die with `SystemExit:
    # 2` from argparse. Mutants Y7 (the flag is parsed and ignored) and Y8
    # (the ignored-flag warning removed) are their evidence.
    "test_audited_supplies_the_tip_a_round_one_emit_cannot_derive",
    "test_audited_without_emit_claims_says_it_changed_nothing",
    # The STRUCTURAL half of the false-cause pair: a ledger of every site that
    # renders the shared cause list, red when the set grows or shrinks. At the
    # base the directive does not exist, so its red there is an absence.
    # Mutants Y10 and Y11 (each site hand-rolls its own causes again).
    "test_the_degenerate_range_causes_have_exactly_one_writer_per_consumer",
    # The THIRD worktree state. `e06461f7` has no unknown branch at all, so
    # "the unknown state kept the absolution" is not a defect it has — it is a
    # state it lacks. Mutant Y6 collapses it back into SHARED.
    "test_an_unreadable_worktree_state_is_its_own_answer_and_keeps_the_no_write",
    # `AttributeError: ... no attribute 'gather_worktree_kind'`. Mutant Y5b
    # (compare the two paths as strings) is what proves the resolution step
    # executes — and the SUBDIRECTORY row is what proves it is needed.
    "test_gather_worktree_kind_resolves_paths_before_comparing_them",
    # Reads `SECTION_DIRECTIVES` against the scenario map; mutant XA (a
    # directive added with no scenario) is its evidence.
    "test_the_directive_render_scenario_ledger_is_pinned_two_way",
    # Guards over THIS module's own bookkeeping. The mutation battery mutates
    # the SCRIPT, never this module, so it cannot reach them — their evidence
    # is the in-module negative control beside each.
    "test_the_fix_matrix_evidence_matches_the_two_ledgers",
    "test_control_the_fix_matrix_checker_catches_a_wrong_evidence_label",
    # ------------------------------------------------------------------- #
    # Round 5's guards. Both WERE run against `dd601793`; they are here
    # because of HOW they fail there.
    # ------------------------------------------------------------------- #
    # The RELATIONSHIP half of the write-permission pair. At `dd601793` two of
    # the three checkout states simply do not carry the no-write sentence —
    # an absence the fix adds, not a wrong answer — so its evidence is mutants
    # Z1 (a state drops the rule) and Z2 (a fourth state with no rule).
    "test_every_checkout_state_carries_the_no_write_rule",
    # There is no legend at `dd601793` at all. Mutant Z5 deletes it again.
    "test_the_emitted_skeleton_carries_a_legend_for_its_two_fields",
    # ------------------------------------------------------------------- #
    # Round 7's one guard. It scans THIS MODULE's prose, and the red-at-base
    # procedure copies this module in UNCHANGED — so no base SCRIPT can make
    # it red, and filing it as regression coverage would be a claim the
    # procedure cannot support. Its red was measured separately (the base
    # module carries both ledger entries once each, normalised) and its
    # in-test positive control is what keeps it honest per run. The battery
    # mutates the script and cannot reach it.
    "test_no_refuted_premise_survives_in_this_modules_prose",
})

# --------------------------------------------------------------------------- #
# 🔴 THE FIX MATRIX AS DATA — because the prose version carried a FALSE LABEL.
# --------------------------------------------------------------------------- #
# Round 2's matrix lived in the module docstring under the column header
# "detector (red at abc41024)" and the sentence "Every 'red at' cell is
# `abc41024`". Row 5's detector —
# `test_each_clause_carries_the_instruction_its_ledger_entry_names` — is GREEN
# at that ref and is not among the 24 that failed there; the constant fifty
# lines below said so, and the table said the opposite. Its real evidence is
# mutants W1-W5, which is arguably stronger; only the LABEL was wrong.
#
# A table nobody can check acquires exactly this kind of error, so the matrix
# is now DATA and the two ledgers are what grade it. Prose may still describe
# it; prose may no longer be the record.
#
#   (finding, detector test name, evidence, mutants)
#
# `evidence` is `RED@<ref>` — the test was watched to FAIL at that tree — or
# `GUARD`, meaning its evidence is the mutation battery and NOT a base ref.
FIX_MATRIX = (
    ("r2/1a ledger measured the OPERATOR'S checkout, not the PR",
     "test_the_ledger_refuses_to_measure_a_checkout_that_is_not_the_pr",
     "RED@abc41024", "H1"),
    ("r2/1b range handed out `..HEAD` from the wrong tree",
     "test_the_range_does_not_hand_out_a_head_that_is_not_the_prs_head",
     "RED@abc41024", "H2"),
    ("r2/1c --emit-claims stamped the LOCAL head as the audited sha",
     "test_emit_claims_stamps_the_prs_head_not_the_local_checkouts",
     "RED@abc41024", "H3"),
    ("r2/2  toolchain gated the SHARED checkout",
     "test_the_toolchain_gates_the_auditors_copy_not_the_shared_checkout",
     "RED@abc41024", "T1, T2"),
    ("r2/3  fork PRs inverted the cross-repo directive",
     "test_a_fork_pr_against_this_repo_is_not_treated_as_cross_repo",
     "RED@abc41024", "C3, P1"),
    ("r2/3b the PR's repo was read from the HEAD repo",
     "test_the_prs_repo_is_read_from_the_url_not_from_the_head_repo",
     "RED@abc41024", "P1"),
    ("r2/4  missing_clauses() was unreachable",
     "test_the_clause_check_runs_over_a_file_that_can_actually_be_lossy",
     "RED@abc41024", "K1"),
    ("r2/4b the --out file was never read back",
     "test_the_out_file_is_read_back_and_checked",
     "RED@abc41024", "K2"),
    # 🔴 THE ROW THE PROSE TABLE MISLABELLED. Green at `abc41024`, measured.
    ("r2/5  five clause rewords INVERTED the instruction and stayed green",
     "test_each_clause_carries_the_instruction_its_ledger_entry_names",
     "GUARD", "W1-W5, R1"),
    ("r2/6  a FALSE cause printed for a failed cumulative measurement",
     "test_a_failed_cumulative_measurement_does_not_print_a_false_cause",
     "RED@abc41024", "L1"),
    ("r2/7  --round 1 --emit-claims emitted a block parsing to garbage",
     "test_emit_claims_prints_a_block_this_scripts_own_parser_accepts",
     "RED@abc41024", "H4"),
    ("r2/8  the parser dropped four fence shapes silently",
     "test_a_fence_the_parser_cannot_read_is_reported_not_skipped",
     "RED@abc41024", "B1"),
    ("r2/9  'repo cannot be determined' collapsed into SAME-REPO",
     "test_an_undeterminable_repo_gets_its_own_branch_not_the_same_repo_one",
     "RED@abc41024", "P2"),

    ("r3/1a the delta range anchored on `<to>`, so it was empty by construction",
     "test_the_range_is_generated_from_the_previous_rounds_audited_sha",
     "RED@d9eb36a8", "N1"),
    ("r3/1a' the same, over the NEWEST of several blocks",
     "test_the_newest_claims_block_wins",
     "RED@d9eb36a8", "N1"),
    ("r3/1b the LEDGER measured that same wrong range",
     "test_the_ledger_measures_from_the_tip_the_previous_round_audited",
     "RED@d9eb36a8", "N1"),
    ("r3/1c THE RANGE rendered a self-range with full confidence",
     "test_a_degenerate_self_range_is_reported_not_rendered_as_a_clean_diff",
     "RED@d9eb36a8", "N4"),
    ("r3/1d the ledger called a self-range an ordinary empty one",
     "test_a_degenerate_self_range_is_named_by_the_ledger_too",
     "RED@d9eb36a8", "N5"),
    ("r3/1e --emit-claims wrote `X..X` in silence",
     "test_emit_claims_warns_when_the_block_it_writes_is_a_self_range",
     "RED@d9eb36a8", "N6"),
    ("r3/1f the WRITER's anchor must stay `<to>` under the fix",
     "test_emit_claims_records_the_tip_this_round_audited_not_the_range_anchor",
     "GUARD", "N3"),
    ("r3/1g a bare round-1 `audited=<sha>` must still anchor round 2",
     "test_a_bare_round_one_audited_sha_still_anchors_the_next_round",
     "GUARD", "N2"),
    ("r3/2  the empty-range reason named two causes head_check refutes",
     "test_an_empty_range_does_not_name_a_cause_the_head_check_refutes",
     "RED@d9eb36a8", "N7"),
    ("r3/2' the same shape at a THIRD site, found by sweeping the class",
     "test_the_unknown_head_sha_reason_names_one_cause_not_two",
     "RED@d9eb36a8", "N8"),
    ("r3/3  three verbatim instruction blocks shipped unpinned",
     "test_each_section_directive_carries_the_instruction_its_ledger_entry_names",
     "GUARD", "X1, X3 (XA, XS controls)"),
    ("r3/4  a 'watched RED at abc41024' label was wrong",
     "test_the_fix_matrix_evidence_matches_the_two_ledgers",
     "GUARD", "IN-MODULE CONTROL"),

    # ------------------------------------------------------------------ #
    # 🔴 NINE ROUND-2 DETECTORS AND ONE ROUND-3 DETECTOR THAT WERE WATCHED
    # RED AND HAD NO ROW. Nothing required a `RED_AT_BASE` entry to appear
    # here, so a finding could be dropped from the matrix while its test
    # stayed green and its ledger entry stayed put — the matrix would grade
    # clean and assert nothing about the finding it had lost.
    # `..._evidence_matches_the_two_ledgers` now requires the containment.
    # ------------------------------------------------------------------ #
    ("r2/2' the toolchain named the wrong tier in its gate commands",
     "test_the_toolchain_section_names_the_tier_the_merge_gates_on",
     "RED@abc41024", "T2"),
    ("r2/4c missing_clauses() compared un-normalised text, so a re-wrap read "
     "as a loss",
     "test_missing_clauses_is_a_pure_function_over_the_text",
     "RED@abc41024", "K3"),
    ("r2/7b a bare round-1 block must anchor the NEXT round's cumulative figure",
     "test_a_round_one_block_anchors_the_next_rounds_cumulative_figure",
     "RED@abc41024", "-"),
    ("r2/8a a 4-backtick opener closed with 3 was dropped in silence",
     "test_a_malformed_fence_beside_a_readable_block_still_warns",
     "RED@abc41024", "B1"),
    ("r2/8b a longer closing fence closes under CommonMark and was dropped",
     "test_a_longer_closing_fence_is_a_valid_close_and_is_read",
     "RED@abc41024", "B2"),
    ("r2/8c a claim wrapping onto a second line was silently truncated",
     "test_a_claim_that_wraps_onto_a_continuation_line_keeps_its_tail",
     "RED@abc41024", "B3"),
    ("r2/8d a block cut short by a nested fence lost every claim after it",
     "test_a_block_cut_short_by_a_nested_fence_is_reported",
     "RED@abc41024", "B4"),
    ("r2/8e the refusal did not say which comment kinds it cannot see",
     "test_the_refusal_says_which_comment_kinds_it_cannot_see",
     "RED@abc41024", "B5"),
    ("r2/9' the head-verified note is the does-not-fire-spuriously control",
     "test_the_range_says_head_was_verified_when_it_was",
     "RED@d9eb36a8", "N1"),

    # ------------------------------------------------------------------ #
    # 🔴 ROUND 4. Base `e06461f7`.
    # ------------------------------------------------------------------ #
    ("r4/1a the round-1 -> round-2 hop was EMPTY BY CONSTRUCTION, and silent: "
     "the self-range warning was spelled on a field that is None at round 1",
     "test_a_round_one_emit_claims_says_head_is_an_assumption_not_a_measurement",
     "RED@e06461f7", "Y9"),
    ("r4/1b round 1 had no way to record the tip it AUDITED, only the tip its "
     "fixes produced",
     "test_audited_supplies_the_tip_a_round_one_emit_cannot_derive",
     "GUARD", "Y7"),
    ("r4/1c --audited without --emit-claims was a silent no-op",
     "test_audited_without_emit_claims_says_it_changed_nothing",
     "GUARD", "Y8"),
    ("r4/2  BOTH degenerate-range messages blamed a checkout `head_check` had "
     "just verified — written by the commit whose message claimed a class fix",
     "test_a_degenerate_range_does_not_blame_a_checkout_it_verified",
     "RED@e06461f7", "Y10, Y11"),
    ("r4/2' the cause list must have exactly one writer per consumer",
     "test_the_degenerate_range_causes_have_exactly_one_writer_per_consumer",
     "GUARD", "Y10, Y11"),
    ("r4/3  Y1/Y2: two OPERATIVE verbatim blocks shipped unpinned — the "
     "delta-scope sentence and the cross-repo 'that worktree is YOURS'",
     "test_each_section_directive_carries_the_instruction_its_ledger_entry_names",
     "GUARD", "Y1, Y2"),
    ("r4/3' a directive that reaches NO brief is an instruction nobody receives",
     "test_the_directive_render_scenario_ledger_is_pinned_two_way",
     "GUARD", "XA"),
    ("r4/4a THE SHARED CHECKOUT named whatever cwd was, including a PRIVATE "
     "per-agent worktree — and absolved the movement it should have reported",
     "test_a_private_worktree_is_not_described_as_shared_and_is_not_absolved",
     "RED@e06461f7", "Y14"),
    ("r4/4b WHERE TO WORK called that worktree the session's own repository",
     "test_the_where_to_work_section_does_not_call_a_private_worktree_the_clone",
     "RED@e06461f7", "Y13"),
    ("r4/4c not knowing is its own state: keep the no-write, drop the "
     "absolution",
     "test_an_unreadable_worktree_state_is_its_own_answer_and_keeps_the_no_write",
     "GUARD", "Y6"),
    ("r4/4d the two git dirs are compared as RESOLVED paths, never as strings",
     "test_gather_worktree_kind_resolves_paths_before_comparing_them",
     "GUARD", "Y5b"),
    ("r4/5  same_commit was case-sensitive, so an uppercase `audited=` sha "
     "disarmed every degenerate-range guard",
     "test_an_uppercase_audited_sha_still_trips_the_degenerate_guard",
     "RED@e06461f7", "Y12"),
    ("r4/6  this matrix's own mutants column was UNGRADED, and every "
     "RED_AT_BASE entry could drop out of it silently",
     "test_the_fix_matrix_evidence_matches_the_two_ledgers",
     "GUARD", "IN-MODULE CONTROL"),

    # ------------------------------------------------------------------ #
    # 🔴 ROUND 5. Base `dd601793`.
    # ------------------------------------------------------------------ #
    ("r5/1a THE CHECKOUT granted WRITE permission over the assembling "
     "session's tree — 'yours alone … Writing here is fine'",
     "test_no_checkout_state_grants_write_permission_over_the_assembly_checkout",
     "RED@dd601793", "Z1"),
    ("r5/1b the no-write rule must live in EVERY checkout state, not only in "
     "a `no-fetch` clause a later round can make conditional",
     "test_every_checkout_state_carries_the_no_write_rule",
     "GUARD", "Z1, Z2"),
    ("r5/1c `no-fetch` was reworded from unconditional to conditional on a "
     "state the script cannot know, disarming it in production",
     "test_each_clause_carries_the_instruction_its_ledger_entry_names",
     "GUARD", "Z3"),
    ("r5/2  TOOLCHAIN's rationale called the assembly checkout the SHARED "
     "CHECKOUT two bars after THE CHECKOUT denied it",
     "test_the_toolchain_reason_is_true_in_every_scenario",
     "RED@dd601793", "Z4"),
    ("r5/3  --audited accepted any string; a value with a space, an embedded "
     "`..` or a placeholder was truncated by the emitter's own parser",
     "test_emit_claims_refuses_an_audited_value_its_own_parser_cannot_read",
     "RED@dd601793", "Z6, Z7"),
    ("r5/4  the degenerate-cause list blamed an omitted --audited on rounds "
     "where omitting it is correct",
     "test_the_degenerate_cause_list_scopes_the_omitted_flag_to_round_one",
     "RED@dd601793", "Z8"),
    ("r5/5  the emitted block carried no key to its own two fields, and the "
     "operator wrote them the wrong way round twice running",
     "test_the_emitted_skeleton_carries_a_legend_for_its_two_fields",
     "GUARD", "Z5"),

    # ------------------------------------------------------------------ #
    # 🔴 ROUND 6's FINDINGS, FIXED IN ROUND 7. Base `3619fe68`.
    # ------------------------------------------------------------------ #
    ("r6/1  `own-worktree-is-writable` scoped the no-write rule to the SHARED "
     "checkout, naming a scoping round 5 removed from the clause it describes",
     "test_no_forward_reference_scopes_the_no_write_rule_to_a_denied_state",
     "RED@3619fe68", "V4"),
    ("r6/2  the exit-4 round trip is line-oriented on BOTH sides, so a "
     "TRAILING NEWLINE in `--audited` emitted a corrupt block at rc 0",
     "test_emit_claims_refuses_an_audited_value_whose_whitespace_leaves_the_line",
     "RED@3619fe68", "V5"),
    ("r6/3  this module's own prose still asserted the premise round 5 refuted",
     "test_no_refuted_premise_survives_in_this_modules_prose",
     "GUARD", "IN-MODULE CONTROL"),
    # 🔴 THE SAME DETECTOR AS r5/2, DELIBERATELY, and its evidence label is
    # r5/2's — the test really was watched red at `dd601793` and that has not
    # changed. What round 6 found was that it was too NARROW (two scenarios,
    # both same-repo), and a width claim has no base ref: its evidence is the
    # mutant that walks the narrow version, which is why V7 is named here and
    # not folded into r5/2's column.
    ("r6/4  the TOOLCHAIN equality drove two scenarios, both same-repo, so a "
     "cross-repo-only reword left it green",
     "test_the_toolchain_reason_is_true_in_every_scenario",
     "RED@dd601793", "V7"),
    ("r6/5  the no-write relationship was spelled as an id PREFIX, so a rename "
     "emptied the set it guards",
     "test_every_checkout_state_carries_the_no_write_rule",
     "GUARD", "V1, V2"),
    ("r6/6  the blind-spot rationale said the script resolves no sha in that "
     "checkout; it resolves that very token there, twice",
     "test_the_blind_spot_rationale_matches_what_the_script_actually_does",
     "RED@3619fe68", "V3"),
    ("r6/7  the VERIFIED range handed out `..HEAD`, which the auditor resolves "
     "in a worktree cut AFTER assembly (pre-existing; the third instance)",
     "test_the_range_names_a_sha_and_not_head_when_the_head_is_verified",
     "RED@3619fe68", "V6"),
)

# A COLLAPSE floor, not a growth floor: a matrix emptied by a bad refactor
# grades every row correctly and asserts nothing. Derived from the current
# measurement the way this repo derives every other floor —
# `m - min(50, max(1, m // 20))` — rather than hand-picked, because a
# hand-picked floor drifts from what it is protecting.
#
# 🔴 COUNTED, not estimated. This comment first said "at m = 48 that is 46" and
# BOTH numbers were wrong: the matrix held 47 rows, and the formula gives
# 47 - max(1, 2) = 45. Caught by counting the rows instead of trusting the
# sentence — which is the same move as 🟢 "count them in the ledger" elsewhere,
# and it is recorded because an arithmetic claim nobody re-runs is precisely
# what that finding was about. Round 5 added seven rows: m = 54, and
# 54 - max(1, 54 // 20) = 52. Counted again, not extrapolated. Round 7 added
# seven more: m = 61, and 61 - max(1, 61 // 20) = 58. Counted by asking the
# constant itself (`len(FIX_MATRIX)`), not by adding 7 to the last sentence.
MIN_FIX_MATRIX_ROWS = 58

# 🔴 THE MUTANTS COLUMN IS AN EVIDENCE CLAIM, AND IT WAS UNGRADED.
# `fix_matrix_problems` took `_mutants` and threw it away, so rewriting a
# GUARD row's column to `"Z99 — a mutant that does not exist"` left the suite
# fully green — while for a GUARD row that column IS the whole evidence, the
# base-ref cell having been set to `GUARD` precisely because there is no base
# ref to point at. It is now resolved against the harness's OWN `ROWS`.
MUTANT_HARNESS = REPO / "scripts" / "tests" / "mutants-audit-dispatch.py"

# The one evidence token that is legitimately not a mutant: a guard over this
# module's own bookkeeping, which the battery cannot reach because it mutates
# the SCRIPT and never this file. Spelled as a sentinel rather than as prose so
# it cannot be confused with a column somebody forgot to fill in.
IN_MODULE_CONTROL = "IN-MODULE CONTROL"


def _known_mutant_ids():
    """Every mutant id the harness actually carries, read from the harness.

    Imported, not restated: a ledger of mutant ids kept here by hand would go
    stale in exactly the direction that makes a false claim pass.
    """
    spec = importlib.util.spec_from_file_location(
        "mutants_audit_dispatch", MUTANT_HARNESS
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return {label.split()[0] for label, _want, _mutate in mod.ROWS}


KNOWN_MUTANT_IDS = _known_mutant_ids()

_RANGE = re.compile(r"\b([A-Za-z]{1,2})(\d+)\s*-\s*(?:([A-Za-z]{1,2}))?(\d+)\b")
_ID = re.compile(r"\b([A-Za-z]{1,2}\d+[a-z]?)\b")


def mutant_ids(column, known=()):
    """The mutant ids a matrix cell names, with `W1-W5` ranges expanded.

    🔴 DIGIT-FORM IDS ARE FOUND BY SHAPE; digitless ones (`XA`, `XS`) only by
    membership in `known`. Said plainly because it is the blind spot: a
    digitless id somebody TYPO'd (`XB`) is invisible here, while a digit-form
    invention (`Z99`) is caught. Widening the shape to digitless tokens makes
    `IN`, `A` and every capitalised word in a prose cell into a mutant id, and
    a checker that fires on prose is one nobody keeps.
    """
    rest, out = column, set()
    for m in _RANGE.finditer(column):
        pre, lo, pre2, hi = m.group(1), int(m.group(2)), m.group(3), int(m.group(4))
        if pre2 and pre2 != pre:
            continue
        out.update(f"{pre}{n}" for n in range(lo, hi + 1))
        rest = rest.replace(m.group(0), " ")
    out.update(_ID.findall(rest))
    for k in known:
        if not any(c.isdigit() for c in k) and re.search(
            rf"\b{re.escape(k)}\b", column
        ):
            out.add(k)
    return out


def fix_matrix_problems(rows, red_by_ref, guards, known_tests, known_mutants):
    """-> a list of complaints. Shared by the guard and its negative control.

    Kept as a function precisely so the control can drive the SAME code with a
    deliberately wrong row: a checker whose only caller is the happy path is a
    checker nobody has watched go red.
    """
    problems = []
    for finding, detector, evidence, mutants in rows:
        named = mutant_ids(mutants, known_mutants)
        unknown = named - known_mutants
        if unknown:
            problems.append(
                f"{finding}: names mutant(s) {sorted(unknown)}, which the "
                "harness's own ROWS do not carry"
            )
        if evidence == "GUARD" and not named and mutants.strip() != IN_MODULE_CONTROL:
            problems.append(
                f"{finding}: labelled GUARD, but its mutants column "
                f"{mutants!r} names no mutant. For a GUARD row that column IS "
                f"the evidence — there is no base ref to point at. Use "
                f"{IN_MODULE_CONTROL!r} only for a guard the battery cannot "
                "reach at all."
            )
        if detector not in known_tests:
            problems.append(f"{finding}: names no test called {detector!r}")
            continue
        if evidence.startswith("RED@"):
            ref = evidence[len("RED@"):]
            if ref not in red_by_ref:
                problems.append(f"{finding}: unknown base ref {ref!r}")
            elif detector not in red_by_ref[ref]:
                problems.append(
                    f"{finding}: claims {detector} was watched RED at {ref}, "
                    f"but it is not in that ref's RED_AT_BASE set"
                )
        elif evidence == "GUARD":
            if detector not in guards:
                problems.append(
                    f"{finding}: labelled GUARD but {detector} is filed as "
                    "regression coverage"
                )
        else:
            problems.append(f"{finding}: evidence {evidence!r} is neither "
                            "RED@<ref> nor GUARD")
    return problems


def test_the_fix_matrix_evidence_matches_the_two_ledgers():
    """🔴 Every detector's EVIDENCE LABEL is graded by the ledgers, not by prose.

    🔴 INVARIANT GUARD. The mutation battery mutates `scripts/audit-dispatch.py`
    and never this module, so it cannot reach this test; its evidence is
    `test_control_the_fix_matrix_checker_catches_a_wrong_evidence_label`, which
    drives the same function with a row that must be rejected.

    This exists because the prose table said `RED@abc41024` for a detector that
    is GREEN there, fifty lines above a constant that said so. A reader
    checking whether finding 5 had been watched red would have read the table
    and stopped.
    """
    assert len(FIX_MATRIX) >= MIN_FIX_MATRIX_ROWS, (
        f"the fix matrix has {len(FIX_MATRIX)} rows (floor "
        f"{MIN_FIX_MATRIX_ROWS}). A matrix that shrank grades every remaining "
        "row correctly and asserts nothing about the findings it dropped."
    )
    assert KNOWN_MUTANT_IDS, (
        "no mutant ids were read out of the harness at all, so the mutants "
        "column below would be graded against an empty set and every row "
        "would pass. Positive control: the harness carries dozens."
    )
    here = {n for n in globals() if n.startswith("test_")}
    problems = fix_matrix_problems(
        FIX_MATRIX, RED_AT_BASE_REFS, INVARIANT_GUARDS_AND_LEDGERS, here,
        KNOWN_MUTANT_IDS,
    )
    assert not problems, (
        "\n\nthe fix matrix claims evidence the ledgers do not support:\n  "
        + "\n  ".join(problems)
        + "\n  A 'watched RED at <sha>' label is a measurement claim. Move the "
          "row to GUARD, or add the detector to that ref's RED_AT_BASE set "
          "AFTER watching it fail there."
    )
    # 🔴 CONTAINMENT, and it is the half that was missing entirely: every test
    # filed as regression coverage names a FINDING, and a finding with no row
    # here is a finding this matrix silently stopped asserting anything about.
    #
    # 🔴 THE COUNT IS COMPUTED, and round 5 made it so. It read "Nine round-2
    # detectors and one round-3 detector were in that state" — TEN over a set
    # with NINE distinct members, because one test is red at two bases and
    # carries a row for each. A hand-written count beside data that grows is a
    # claim nobody re-reads, and this module has now carried three of them; the
    # numbers below come out of the ledgers themselves.
    detectors = {row[1] for row in FIX_MATRIX}
    orphaned = RED_AT_BASE - detectors
    assert not orphaned, (
        f"\n\n{len(orphaned)} of {len(RED_AT_BASE)} test(s) filed as "
        "regression coverage for a real defect appear in NO matrix row: "
        f"{sorted(orphaned)}\n"
        f"  ({len(RED_AT_BASE & detectors)} of them do have one.)\n"
        "  The ledger says each was watched RED at a named base, so each is a "
        "finding. Give it a row, or explain in the ledger why it is not one."
    )


def test_control_the_fix_matrix_checker_catches_a_wrong_evidence_label():
    """NEGATIVE CONTROL: the checker above must be able to go red.

    ONE deliberately wrong row per rejection path — **count them in `bad`**,
    which is what the assertion does. The docstring used to say "Four" over
    five rows and five paths; a hand-written count beside data that grows is a
    claim nobody re-reads, and this module has now carried two of them.

    🔴 The last two paths are round 4's: the mutants column was DISCARDED, so
    a GUARD row could name `"Z99 — a mutant that does not exist"`, or name
    nothing at all, and grade clean — while for a GUARD row that column is the
    entire evidence.
    """
    guards = frozenset({"test_a_guard"})
    red = {"aaaa1111": frozenset({"test_a_regression"})}
    known = {"test_a_guard", "test_a_regression"}
    mutants = frozenset({"M1", "M2"})
    bad = (
        ("mislabelled guard", "test_a_guard", "RED@aaaa1111", "-"),
        ("mislabelled regression", "test_a_regression", "GUARD", "M1"),
        ("unknown ref", "test_a_regression", "RED@ffff9999", "-"),
        ("nonexistent detector", "test_vanished", "GUARD", "M1"),
        ("nonsense evidence", "test_a_guard", "probably fine", "M1"),
        ("mutant that does not exist", "test_a_guard", "GUARD",
         "Z99 — a mutant that does not exist"),
        ("guard whose evidence column is empty", "test_a_guard", "GUARD", "-"),
    )
    problems = fix_matrix_problems(bad, red, guards, known, mutants)
    assert len(problems) == len(bad), (
        f"the checker reported {len(problems)} problem(s) for {len(bad)} "
        f"deliberately wrong rows: {problems}"
    )
    # And it must NOT fire on correct rows — a checker that rejects everything
    # is as useless as one that accepts everything.
    good = (
        ("ok guard", "test_a_guard", "GUARD", "M1"),
        ("ok guard, range spelling", "test_a_guard", "GUARD", "M1-M2"),
        ("ok guard the battery cannot reach", "test_a_guard", "GUARD",
         IN_MODULE_CONTROL),
        ("ok regression with no mutant", "test_a_regression", "RED@aaaa1111",
         "-"),
    )
    assert fix_matrix_problems(good, red, guards, known, mutants) == []
    # 🔴 POSITIVE CONTROL for the range expander itself: a reassuring empty set
    # is indistinguishable from a parser wired to nothing.
    assert mutant_ids("W1-W5, R1") == {"W1", "W2", "W3", "W4", "W5", "R1"}
    assert mutant_ids("X1, X3 (XA, XS controls)", {"XA", "XS"}) == {
        "X1", "X3", "XA", "XS"
    }
    assert mutant_ids("Y5b") == {"Y5b"}
    assert mutant_ids(IN_MODULE_CONTROL, {"XA"}) == set()


# --------------------------------------------------------------------------- #
# 🔴 ROUND 6 — THE LADDER'S OWN RECORD CONTRADICTED THE SCRIPT
# --------------------------------------------------------------------------- #
# Round 5 refuted the premise round 4 built its write grant from. It corrected
# the SCRIPT's docstring and left this module's, which went on stating the
# refuted premise as fact — in the docstring of the very test that records the
# finding, twelve lines above a docstring the same round edited.
#
# A premise nobody re-reads is exactly what acquires this error, so the ledger
# is machine-checked rather than trusted. Each entry is a phrase that may now
# appear EXACTLY ONCE in this module: here, as a record of what was refuted.
#
# 🔴 SCOPED TO THIS MODULE, and the scope is not an accident. The SCRIPT also
# quotes the first phrase once, inside `gather_worktree_kind`'s docstring,
# where it is explicitly marked false — a legitimate second copy this check
# deliberately does not reach, because a count over two files could be
# satisfied by the wrong file holding both.
REFUTED_PREMISES = (
    ("where a dispatched auditor usually stands",
     "round 5: `gather_worktree_kind` measures the cwd of the ASSEMBLING "
     "process, and WHERE TO WORK dispatches the auditor to a tree it makes "
     "itself. No state this script can measure puts the auditor here."),
    ("the auditor's alone",
     "round 5: the assembly tree belongs to the session that BUILT the brief, "
     "and under an inherited cwd it is shared with the dispatcher and with "
     "sibling auditors. It is the auditor's in neither configuration."),
)

_THIS_MODULE_SOURCE = Path(__file__).read_text(encoding="utf-8")


def surviving_refuted_premises(text):
    """-> [(phrase, count)] for every refuted premise not stated exactly once.

    ONE occurrence is the ledger entry itself. A SECOND is this module
    asserting the premise somewhere else; ZERO means the ledger entry was
    deleted, which is the same failure read from the other end.

    🔴 NORMALISED TWICE OVER, AND THE FIRST TWO DRAFTS WERE NOT — each MISSED
    an occurrence it was written for, and each reported the module CLEAN with
    the premise sitting two lines away. Both were measured against `3619fe68`:

      * a raw `str.count` scored **0** for both phrases. They live in WRAPPED
        prose, so the source reads `where a dispatched auditor\\n    usually
        stands`. Fixed by collapsing whitespace — the same normalisation the
        script uses in `missing_clauses`, for the same reason.
      * whitespace alone still scored **0** for entry 1, because that one
        spans a Python IMPLICIT CONCATENATION — the phrase is cut in half by a
        closing quote, a newline and an opening quote, and collapsing
        whitespace leaves the two quote characters sitting in the middle of
        it. So adjacent literals are joined FIRST, exactly as the compiler
        joins them, and only then is whitespace collapsed.

    That is the "guard whose description is wider than its implementation"
    failure, twice, in the guard written to catch a claim wider than its code.
    """
    joined = re.sub(r'"\s*"', "", text)      # implicit string concatenation
    flat = " ".join(joined.split())           # wrapped prose
    counts = [(p, flat.count(" ".join(p.split()))) for p, _ in REFUTED_PREMISES]
    return [(p, n) for p, n in counts if n != 1]


def test_no_refuted_premise_survives_in_this_modules_prose():
    """🔴 REGRESSION. Red at `3619fe68`, on both entries.

    Measured there: entry 0 of the ledger above appeared in
    `..._private_worktree_is_not_described_as_shared_and_is_not_absolved`'s
    docstring and entry 1 in one of its assertion messages — both stating, as
    the reason a test exists, the premise round 5 established is false. The
    commit message for `3619fe68` claims both docstrings were corrected; the
    script's was, this module's was not.

    🔴 THIS DOCSTRING DELIBERATELY DOES NOT QUOTE EITHER PHRASE. Quoting one
    here would be a second occurrence and would fail this very test — which is
    the ledger working, not a limitation: the record of what was refuted lives
    in ONE place, and everything else refers to it by index.

    🔴 THIS IS PROSE, SO THE GUARD IS ON THE WHOLE PHRASE. `claude/RULES.md`:
    when the artifact under test IS prose, a guard on a WORD is walkable by
    rewording. A reword that keeps the claim keeps the phrase; a reword that
    drops the phrase has also dropped the claim, which is the outcome wanted.
    """
    # 🔴 POSITIVE CONTROL, through the same function: a clean result over a
    # scan that matched nothing is indistinguishable from a scan wired to
    # nothing. Plant a second copy and require it to be seen.
    planted = f"{_THIS_MODULE_SOURCE}\n# {REFUTED_PREMISES[0][0]}\n"
    assert surviving_refuted_premises(planted), (
        "POSITIVE CONTROL: the scan cannot see a planted second copy of "
        f"{REFUTED_PREMISES[0][0]!r}, so a clean result below says nothing"
    )
    survivors = surviving_refuted_premises(_THIS_MODULE_SOURCE)
    assert not survivors, (
        "\n\nthis module states a premise the ladder has REFUTED:\n"
        + "".join(
            f"  {p!r}: {n} occurrence(s), want exactly 1 (the ledger entry)\n"
            f"    refuted by {dict(REFUTED_PREMISES)[p]}\n"
            for p, n in survivors
        )
        + "  A test module is the ladder's record of what each round found. "
        "Reading as coverage while restating the thing that was overturned is "
        "worse than saying nothing, because it stops the next round looking."
    )


def test_the_two_ledgers_partition_this_modules_tests():
    """A test cannot quietly join or leave either ledger.

    Same shape as `scripts/tests/test_transcript_search.py`'s two lists, and for
    the same reason: "watched red" is a claim about a specific base, and a
    module whose docstring says "these are invariant guards" while quietly
    growing an unlisted test is asserting coverage nobody checked.
    """
    here = {n for n in globals() if n.startswith("test_")}
    assert RED_AT_BASE_REFS, (
        "RED_AT_BASE is non-empty, so it must name the base ref every one of "
        "its tests was watched to FAIL at. 'Watched red' is a claim about a "
        "specific tree; without the ref it is not a claim at all."
    )
    for ref, names in RED_AT_BASE_REFS.items():
        assert ref and names, (
            f"base ref {ref!r} maps to {sorted(names)} — a ref with no tests, "
            "or a set with no ref, is half a claim. Delete it or fill it in."
        )
    overlap = RED_AT_BASE & INVARIANT_GUARDS_AND_LEDGERS
    assert not overlap, (
        f"tests in BOTH ledgers: {sorted(overlap)}. A test is regression "
        "coverage or it is an invariant guard; listing it twice makes the "
        "count of what was watched red unreadable."
    )
    unlisted = here - INVARIANT_GUARDS_AND_LEDGERS - RED_AT_BASE
    assert not unlisted, (
        f"tests not in either ledger: {sorted(unlisted)}. Add each to "
        "INVARIANT_GUARDS_AND_LEDGERS (or to RED_AT_BASE with its base ref)."
    )
    stale = (INVARIANT_GUARDS_AND_LEDGERS | RED_AT_BASE) - here
    assert not stale, (
        f"ledger entries naming no test: {sorted(stale)}. A ledger that names "
        "a deleted test reads as coverage that no longer runs."
    )
