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

🔴 ROUND 7 — a fifth base, `3619fe68`, fixing round 6's findings
------------------------------------------------------------------
Round 6 returned two 🟡 and four 🟢, plus a PRE-EXISTING 🔴 found under a
targeted probe. The 🔴 is the third instance of this ladder's recurring
confusion and the first on a TIME axis: THE RANGE handed the auditor
`<from>..HEAD` whenever the head was verified, and `HEAD` resolves in the
AUDITOR's worktree — cut after assembly, from a repository other sessions push
to. Round 3 conflated the operator's checkout with the tree under audit; round
5 conflated the assembling process's cwd with where the auditor stands.

The two 🟡: `own-worktree-is-writable` still said the no-write rule "is about
the SHARED checkout", naming a scoping round 5 had removed from the clause it
forward-references — so a cross-repo brief assembled in a PRIVATE worktree
contradicted itself across three consecutive sections; and the exit-4 round
trip is line-oriented on BOTH sides, so a TRAILING NEWLINE in `--audited`
emitted a corrupt block at rc 0. The four 🟢 were all scaffolding: this
module's own prose still asserted the premise round 5 refuted, the TOOLCHAIN
equality drove two scenarios that were both same-repo, the no-write
relationship was spelled as an id PREFIX, and the blind-spot rationale was
false as stated.

Measured at `3619fe68` the same way — `git show 3619fe68:scripts/audit-dispatch
.py` into a scratch tree with THIS module copied in unchanged, under
`PYTHONDONTWRITEBYTECODE=1 -p no:cacheprovider`:

    12 failed, 82 passed

and only FOUR of the twelve are regression coverage. Of the other eight: ONE is
round 7's new state-map guard, which ERRORS there for want of a constant the
fix adds; the rest are pre-existing tests whose expected range spelling moved
with the tip fix, plus the whole-string directive ledger.

🔴 ONE OF ROUND 7's GUARDS CANNOT BE RED UNDER THAT PROCEDURE AT ALL, and the
reason is worth naming rather than papering over: it scans THIS MODULE's prose,
and the procedure copies this module in UNCHANGED. A base SCRIPT says nothing
about a base DOCSTRING. It is filed as a guard, and its red was measured
separately against `3619fe68`'s test module — where both `REFUTED_PREMISES`
entries appear, normalised, and would read twice with the ledger present.

🔴 ROUND 8 — a sixth base, `28492af2`, fixing round 7's findings
------------------------------------------------------------------
Round 7 returned two 🟡 and four 🟢. The larger 🟡 is the FOURTH instance of
this ladder's recurring confusion, and it is the REPOSITORY axis: every `git`
command this script runs goes to the assembly checkout, while every PR fact
comes from `gh` about a repo that, cross-repo, is a DIFFERENT ONE. So
`verify_head_is_the_pr` is structurally unable to pass there — THE RANGE
diagnosed that as a checkout that had moved ("Resolve the range in a tree that
CONTAINS the PR's head", of which there is none here and never will be), and
THE LEDGER printed COULD NOT MEASURE on EVERY delta round of such a PR, leaving
the ladder's own two-consecutive-zero payload gate uncomputed by anybody. No
test built that state: both cross-repo scenarios ran at ROUND 1 and both delta
scenarios were same-repo — a textbook isolation seam, and one the existing
fixture would have hidden, because `OTHER_REPO_PR` inherited `DEFAULT_PR`'s
`headRefOid` and so modelled the assembly checkout standing on a commit of
another repository.

The other 🟡: both prose guards were blind to a phrase wrapped across two `#`
COMMENT lines and to single- or mixed-quote implicit concatenation, while both
described themselves as handling exactly that — and a wrapped comment block is
where this ladder writes every round's narrative.

The four 🟢: round 7's tip fix was HALF-APPLIED (the DEGENERATE branch of the
same `if`/`elif` still handed out `..HEAD`, under a banner claiming the range
"can never contain anything, whatever the PR looks like"); the forward
reference named a WIDER set than the clause it describes, differing on exactly
the clone the brief's own recipe writes to; THE LEDGER's provenance line was
the last copyable command whose `HEAD` resolves differently for the reader the
brief invites to re-run it; and the re-derivability line below printed a
FUNCTION count in a row of NODE-ID counts.

🔴 ROUND 8 ALSO FOUND A FIFTH INSTANCE OF THE SHAPE, by sweeping for it rather
than from a finding, and it is the sharpest of the six: `newest is not None`
(a block PARSED) was read as "there is something to diff FROM". `audited=..`
parses cleanly and yields two EMPTY halves, so a round-3 brief rendered
``Diff `None..<the PR's head>` `` — a literal Python `None` inside a git rev
spec — at rc 0, under "verified at assembly time", with THE LEDGER giving the
ROUND-1 cause ("a first, full audit has no previous round") for a round that
has one. Refused now, with the two causes named apart.

Measured at `28492af2` the same way:

    9 failed, 93 passed

SIX of the nine are regression coverage (`RED_AT_BASE_R7`). Of the other three:
two are the whole-string CLAUSE and DIRECTIVE ledgers, which moved with round
8's reword of `no-fetch` and `own-worktree-is-writable`; one is the
skipped-round warning guard, red there on an ABSENCE the fix adds. All three
are filed as guards, not findings.

🔴 AND THE PER-ROUND COUNTS ABOVE ARE CLAIMS ABOUT THE MODULE OF THEIR OWN
ROUND. Re-running the same procedure with the CURRENT module gives larger
numbers at every historical ref, because each later round adds tests that are
also red at the earlier trees. Measured 2026-08-28 with the round-8 module —
NODE IDS, which is the unit every "N failed" figure in this docstring uses:

    abc41024   64 failed, 38 passed      <- 64 node ids, 61 functions
    d9eb36a8   41 failed, 61 passed
    e06461f7   34 failed, 68 passed
    dd601793   26 failed, 76 passed
    3619fe68   20 failed, 82 passed
    28492af2    9 failed, 93 passed

🔴 THE FIRST NUMBER READ **54** UNTIL ROUND 8, AND 54 WAS THE OTHER UNIT — the
count of distinct FUNCTIONS, where `pytest -q` printed 57, because three of
them are parametrised. (With round 8's module the same pair reads 61 and 64;
the gap is the same three.) Nothing else in this docstring switches units
mid-list —
`:30` and `:81` both say "N node ids, M functions" precisely so the two cannot
be confused — so a lone 54 in a row of node-id counts is a claim that does not
re-derive by the procedure it is filed under. Re-derived, not corrected from
memory: the script above restores each ref and counts `^FAILED ` lines.

What is re-derivable, and was re-derived again in round 8, is the claim the
ledgers actually make: every name in every `RED_AT_BASE_*` set fails at its own
ref. Zero false claims across all six refs.

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

Re-run 2026-08-28 against HEAD of this branch after the round-8 fixes — **93
rows, all as expected**, over a 102-test positive control (83 rows over 94
after round 7; 76 over 89 after round 5) — each mutant applied
to a COPY of
`scripts/audit-dispatch.py` in a scratch tree (never the worktree), under
`PYTHONDONTWRITEBYTECODE=1 -p no:cacheprovider`, with the unmutated copy as the
positive control. Every mutant asserted its target string present before
editing — a mutation that silently fails to apply reports "the guard held",
which is the most flattering possible wrong answer. Two rows have done exactly
that: `C3`, written against the two-state `cross_repo` flag, and `H2`, whose
branch became an `elif` when round 3 inserted the degenerate-self-range case
ahead of it. Both were re-targeted; without that assert each would have scored
as a guard holding. 🔴 TWO MORE IN ROUND 7 — `Y6` and `Y14`, whose target was
`render_checkout`'s inline state->directive dict, now the module-level
`CHECKOUT_STATE_DIRECTIVE`. That makes FOUR rows this assert has caught, every
one of them after a refactor that nobody thought touched the battery.

🔴 ROUND 10 RE-RAN IT AFTER ITS OWN FIXES — **100 rows, all as expected**, over
a 109-test positive control. FOUR MORE rows reported MUTATION DID NOT APPLY and
were re-targeted (`C6` and `K2`, on a pure RE-INDENT — the brief-rendering
block moved inside `if brief_refused is None:`; `V11` and `V12`, whose branch
conditions moved onto the shared `cross_repo_holds_neither_end` predicate).
That makes EIGHT rows this assert has caught. TEN killer sets moved, and the
notable one is `C3`: SEVEN of the thirteen names round 8 added LEFT it, because
THE LEDGER stopped turning on `repo_relation` alone — the departure is round
10's finding B, measured rather than argued.

🔴 ROUND 10's BASE is `706a6b38`, measured the same way: **7 failed, 102
passed**. FIVE are its regression coverage (`RED_AT_BASE_R9`); of the other
two, one is the whole-string DIRECTIVE ledger moving with the reword of
`own-worktree-is-writable`, and one is
`..._tip_placeholder_ledger_matches_the_script`, red there on an
`AttributeError` for a name the fix introduces and therefore filed as a guard.

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
       (that figure is the ROUND-2 control; today's is 102 — the harness
        prints the current one, which is the number to read, and it is the
        only one that cannot go stale. This line said 89 through rounds 6 and
        7 while the paragraph above it said 94, in the same docstring.)

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

   🔴 ROUND 13 PAID FOR IT ONCE. Finding F2 — the cross-repo recipe fails
   `rc 128` in a clone that has not fetched the PR's branch, and ALWAYS for a
   fork PR — is exactly a fact about real `git`, and no test here could have
   seen it. `test_the_cross_repo_recipe_can_actually_be_run` pins the
   RELATIONSHIP that made it possible (a ref `worktree add` resolves must be
   one the recipe itself created), which IS machine-readable; the rc-128
   measurements, and the verification that the replacement recipe runs on a
   real fork-PR clone and leaves it byte-identical bar one `refs/audit/` ref,
   were made by hand and recorded in the fix commit. Read the pin as covering
   the shape, never the tool.
4. **It does not check that the classification a human writes is CORRECT.** The
   script deliberately refuses to classify payload vs scaffolding; nothing
   mechanical can grade the answer.

🔴 ROUND 15's BASE IS `5bad0a0c` — THIS BRANCH'S OWN HEAD, not `origin/main`,
because every finding it carries is a defect this branch shipped. Measured with
the base script and the current module: **6 failed, 116 passed**, and all six
fail on an ASSERTION rather than an import or a missing symbol — checked
deliberately, since an arity red is not regression coverage. `RED_AT_BASE_R15`
carries them.

Its other half is not a base ref at all. Three probes (`ci_suite`, `gate_tier`,
`py_tests`) had NO mutant in either direction, and all three inversions were
measured SURVIVING a fully green 115-test suite while shipping a fabricated
command — `--tier both` appended to any repo's `gate.sh` being the exact
fabrication that field exists to prevent. The cause was FIXTURE REACH:
`_write_repo(py_test=False)` was called by nothing, no fixture had a `gate.sh`
lacking `--tier both`, and no test asked whether `run-ci-suite.sh` was ABSENT
from the devrc-shaped brief. A branch no fixture enters is a branch no assertion
can grade, so a row written for it would have been vacuous too. That is why
`test_the_toolchain_probes_are_reachable_in_both_directions` is filed as a GUARD
and its evidence is V45/V47/V49: a fixture-reach fix cannot be watched red
anywhere, which is precisely why it goes unnoticed.

HERMETIC: no test touches the network, spawns a process, or reads a real PR.
`test_nothing_here_spawns_a_subprocess` proves the first two by making
`subprocess.run` raise for the duration of a full `main()` run.
"""
from __future__ import annotations

import ast
import importlib.util
import io
import json
import re
import sys
import tempfile
import tokenize
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
# 🔴 ROUND 8 — THE PROSE NORMALISER, ONE RULE IN ONE PLACE
# --------------------------------------------------------------------------- #
# Two guards in this module scan Python SOURCE for a phrase a round has already
# established is false: `surviving_refuted_premises` and the prose half of
# `test_the_blind_spot_rationale_matches_what_the_script_actually_does`. Round 7
# gave each its own copy of the same three-line normaliser, and round 8 measured
# what both copies could not see. Planted at `28492af2`, one phrase per shape:
#
#     shape                                    round 7   round 8
#     phrase on ONE `#` line                   CAUGHT    CAUGHT
#     phrase WRAPPED over two `#` lines        MISSED    CAUGHT
#     phrase wrapped inside one docstring      CAUGHT    CAUGHT
#     "…" "…"  double-quoted concatenation     CAUGHT    CAUGHT
#     '…' '…'  single-quoted concatenation     MISSED    CAUGHT
#     "…" '…'  MIXED-quote concatenation       MISSED    CAUGHT
#
# The two MISSED comment rows are the ones that matter, because the wrapped `#`
# comment is where this ladder writes its narrative: 119 multi-line comment
# blocks over 766 comment lines in this module alone, one block per round. A
# round restating a refuted premise in its own `🔴 ROUND N —` header — wrapped,
# as every one of them is — scored ZERO on both guards and read as clean.
#
# 🔴 THE OLD NORMALISER'S DESCRIPTION WAS ALREADY WIDER THAN ITS CODE, twice
# over, which is the failure `claude/RULES.md` -> guards-narrower names: it said
# "these live in wrapped comments" while `" ".join(text.split())` leaves the `#`
# sitting mid-phrase, and "adjacent literals are joined FIRST, exactly as the
# compiler joins them" while `re.sub(r'"\s*"', "", …)` joins only the
# double-quoted spelling. Widening the regex would have kept both claims one
# quoting style ahead of the code, so the reading is handed to the PARSER:
#
#   * `ast` yields every string constant with implicit concatenation ALREADY
#     performed — by the compiler itself, so "exactly as the compiler joins
#     them" is now true by construction rather than by a regex that approximates
#     it, and every quoting style (including an f-string joined to a plain
#     literal) is covered without enumerating any of them.
#   * `tokenize` yields COMMENT tokens, which cannot be confused with a `#`
#     inside a string literal, and a run of them on CONSECUTIVE lines is joined
#     into one chunk — that is what a reader sees when prose is wrapped.
#
# Chunks are kept SEPARATE (joined by newline, and no phrase here contains one)
# so a phrase cannot be manufactured by butting two unrelated blocks together.
def collapse_ws(text):
    """-> `text` with every whitespace run collapsed to one space."""
    return " ".join(text.split())


def prose_chunks(source):
    """-> the prose of Python `source` as chunks, the way a reader reads it.

    A chunk is one contiguous run of comment lines, or one string constant.
    """
    chunks, run, prev_line = [], [], None
    for tok in tokenize.generate_tokens(io.StringIO(source).readline):
        if tok.type != tokenize.COMMENT:
            continue
        line = tok.start[0]
        body = re.sub(r"^#+\s?", "", tok.string)
        if run and line == prev_line + 1:
            run.append(body)
        else:
            if run:
                chunks.append(" ".join(run))
            run = [body]
        prev_line = line
    if run:
        chunks.append(" ".join(run))
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            chunks.append(node.value)
    return [collapse_ws(c) for c in chunks]


def flat_prose(source):
    """-> `prose_chunks` as one string a phrase count can run over."""
    return "\n".join(prose_chunks(source))


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
    # 🔴 ROUND 8 DROPPED "FOR THIS AUDIT", the SECOND narrowing on this clause.
    # It made the rule forbid the brief's own cross-repo recipe: WHERE TO WORK
    # says `git -C <your local clone of owner/name> worktree add …`, and that
    # clone is a checkout the auditor made but did not make FOR THIS AUDIT.
    # The forward reference in `own-worktree-is-writable` named the wider set
    # ("every checkout you did not make"), so the two sides disagreed on
    # precisely the tree the recipe writes to. Both now spell `NO_WRITE_SCOPE`.
    "no-fetch": (
        "**Do NOT `git fetch`, `pull`, `checkout` or otherwise write to any "
        "checkout you did not make — including the one THE CHECKOUT section "
        "names, which is where this brief was assembled and is not yours.** "
        "Other sessions are in those trees; a fetch there is a write with "
        "cross-session blast radius, and every ref you need is already "
        "resolved for you here."
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

# 🔴 ROUND 8 — THE CROSS-REPO FIXTURE'S HEAD OID IS ITS OWN, AND THE ASSEMBLY
# CHECKOUT'S IS A THIRD VALUE. `OTHER_REPO_PR` used to inherit `FAKE_HEAD_OID`
# from `DEFAULT_PR`, and `make_runner`'s `local_head` defaults to the PR's own
# `headRefOid` — so a cross-repo test written with the plain fixture modelled
# the assembly checkout of `example-org/devrc` standing on a commit that exists
# only in `someone-else/otherproj`. That state is PHYSICALLY IMPOSSIBLE, and it
# is the state round 8's first cross-repo delta render was built in: the brief
# read "verified at assembly time to be PR #900's head commit" for a PR in
# another repository. `claude/RULES.md` -> mutation-sweep-blind-spots: pick
# fixture values that are pairwise distinct, and distinct from any constant the
# assertion names, or the fixture cannot see the bug.
FAKE_OTHER_HEAD_OID = "aaaabbbbccccddddeeeeffff00001111a1a1a1a1"
# What `git rev-parse HEAD` answers in the assembly checkout during a cross-repo
# run: a commit of the OTHER project, equal to neither of the two above.
FAKE_ASSEMBLY_HEAD = "9999999988888888777777776666666655555555"

# A genuinely cross-repo PR: it lives in a repository that is NOT the cwd's.
OTHER_REPO_PR = dict(
    DEFAULT_PR,
    url="https://example.invalid/someone-else/otherproj/pull/900",
    headRefOid=FAKE_OTHER_HEAD_OID,
    headRepository={
        "name": "otherproj", "nameWithOwner": "someone-else/otherproj",
    },
    headRepositoryOwner={"login": "someone-else"},
)

# 🔴 A REAL DIRECTORY ON DISK, and it must stay one. `render_toolchain` PROBES
# the checkout the PR lives in — `scripts/gate.sh`, `flake.nix`'s `checks`
# outputs, `.envrc` — and prescribes only what it finds. A `/fake/checkout/…`
# string (what this was) makes every probe answer "absent", so the whole suite
# would exercise the not-detected branch and NOTHING would cover the devrc
# shape the pre-existing guards assert. Same class as the `rev_list` constant
# in `make_runner`: a fixture that cannot enter the branch under test.
#
# Written once at import, beside `CLAIMS_FILE` and for the same reason — the
# `SCENARIO_RUNS` entries are plain callables and cannot take a `tmp_path`.
# `use opencode`, a `--tier both` gate and `checks.${system}` with TWO names
# are devrc's real shapes, measured at `/home/zach/workspace/devrc`.
_FAKE_REPO_TMP = tempfile.TemporaryDirectory(prefix="audit-dispatch-repo-")
FAKE_REPO_DIR = str(Path(_FAKE_REPO_TMP.name) / "devrc")


def _write_repo(root, *, envrc, gate=None, ci_suite=False, flake=None,
                py_test=True):
    """Materialise a repo-shaped tree for `detect_repo_toolchain` to probe."""
    root = Path(root)
    (root / "scripts" / "tests").mkdir(parents=True, exist_ok=True)
    if envrc is not None:
        (root / ".envrc").write_text(envrc, encoding="utf-8")
    if gate is not None:
        (root / "scripts" / "gate.sh").write_text(gate, encoding="utf-8")
    if ci_suite:
        # 🔴 NO SHEBANG, and none is wanted. `detect_repo_toolchain` only ever
        # asks `is_file()` about this path — nothing here is executed, so a
        # shebang would be decoration that trips
        # `test_no_test_writes_a_usr_bin_env_shebang_at_runtime` (the repo-wide
        # guard that says an executable a test WRITES must go through
        # `testlib.mockbin.write_exec`). Caught by the full gate, not by
        # running this module alone.
        (root / "scripts" / "tests" / "run-ci-suite.sh").write_text(
            'python3 -m pytest "$@"\n', encoding="utf-8")
    if flake is not None:
        (root / "flake.nix").write_text(flake, encoding="utf-8")
    if py_test:
        (root / "scripts" / "tests" / "test_x.py").write_text(
            "def test_x():\n    assert True\n", encoding="utf-8")
    return str(root)


# devrc's own flake shape: `checks.${system}` holding exactly `pytests` and
# `nodetests`, and a dev shell that names pytest. The `''…''` block is not
# decoration — it is what an indentation-only name scan trips over, and the
# reason `_flake_check_names` tracks nix strings.
DEVRC_FLAKE = """\
{
  outputs = { self, nixpkgs }:
    let
      forAllSystems = f: f;
    in forAllSystems (system: {
      devShells.default = pkgs.mkShell {
        buildInputs = [ pkgs.python3Packages.pytest ];
        shellHook = ''
echo "devrc: gate toolchain ready"
if [ -n "$X" ]; then
  echo hi
fi
        '';
      };
      checks.${system} = {
        pytests =
        pkgs.runCommandLocal "devrc-pytests"
          {
            nativeBuildInputs = [ pkgs.git pkgs.ripgrep ];
          }
          ''
runHook preBuild
for f in *.py; do echo "{$f}"; done
grep -c '}' out.txt || true
python3 -m pytest .
          '';
        nodetests =
        pkgs.runCommandLocal "devrc-nodetests"
          {
            nativeBuildInputs = [ pkgs.nodejs ];
          }
          ''
node --test
          '';
      };
    });
}
"""
# 🔴 THE FIXTURE MUST REACH THE BRANCHES IT GRADES, and the first draft did
# not. `pytests = pkgs.runCommandLocal "…" { } ''…''` kept brace depth at 1 for
# the whole block, so a mutant that mishandles NESTING could not be observed —
# a fixture landing exactly on its own boundary. The real file spans the
# derivation attrset over several lines (depth 2), and its build scripts start
# at column 0, which is what an indentation-bounded scan trips over. The
# unbalanced `grep -c '}'` above is the third real shape: a brace inside a
# `''…''` string that skews depth for anyone not tracking nix strings.

# `homelab-infra`'s shape, measured 2026-08-29: NO `scripts/gate.sh`, NO
# `checks` output, `.envrc` is `use flake` + `use opencode`, and its real
# runner is `scripts/tests/run-ci-suite.sh`. The `checks = pr.get(...)` line is
# VERBATIM from a python heredoc inside that repo's real devShell — it is the
# string a loose `^\s*checks\b.*=` probe matched, answering "this flake
# declares checks" for a flake that declares none.
HOMELAB_FLAKE = """\
{
  description = "Homelab infrastructure development shell";
  outputs = { self, nixpkgs }:
    {
      devShells = forAllSystems (system: {
        default = pkgs.mkShell {
          buildInputs = with pkgs; [ kubectl fluxcd sops age gh ];
          shellHook = ''
python3 - <<'PY'
def ci_status(checks):
    if not checks:
        return "none"
checks = pr.get("statusCheckRollup", [])
PY
          '';
        };
      });
    };
}
"""

# 🔴 A FLAKE THAT PROVIDES A TOOLCHAIN AND NAMES NO PYTEST. The shape round 15
# found unguarded: `toolchain_shell` asks ONE question — does `flake.nix`
# mention pytest — and wraps the LANGUAGE-AGNOSTIC gate with the answer, so this
# repository's gate is prescribed bare and fails `node: command not found`.
NODE_ONLY_FLAKE = """\
{
  outputs = { self, nixpkgs }:
    {
      devShells.default = pkgs.mkShell {
        buildInputs = [ pkgs.nodejs pkgs.esbuild ];
      };
      checks.x86_64-linux = {
        nodetests = pkgs.runCommandLocal "nodetests" { } "node --test";
      };
    };
}
"""

# Also shebang-free, and for the same reason as `run-ci-suite.sh` above: the
# probe reads this text looking for `--tier` and `both` and never runs it. What
# matters is that both markers are present in the shapes devrc's real
# `scripts/gate.sh` carries them.
DEVRC_GATE = """\
#   scripts/gate.sh [--tier pytest|node|both] [--set hermetic|all]
#     --tier both      (default) run both runners; the gate is red if either is.
case "$1" in
  --tier) TIER="${2:-both}" ;;
esac
"""

# 🔴 A `scripts/gate.sh` THAT DOES NOT TAKE `--tier`, which no fixture had. The
# branch that omits the flag was entered by nothing, so `gate_tier=True` — the
# exact fabrication that field exists to prevent — survived a green suite.
# Shebang-free for the same reason as `DEVRC_GATE` and `run-ci-suite.sh` above:
# nothing here is executed, and a shebang a TEST writes trips the repo-wide
# `test_no_test_writes_a_usr_bin_env_shebang_at_runtime` guard, which only the
# full gate runs.
PLAIN_GATE = """\
# scripts/gate.sh — this repository's gate takes no arguments at all.
set -euo pipefail
python3 -m pytest .
"""

_write_repo(FAKE_REPO_DIR, envrc="use opencode\n", gate=DEVRC_GATE,
            flake=DEVRC_FLAKE)

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


# 🔴 THE FAKE'S OWN, INDEPENDENT READ of which repository each side names —
# deliberately NOT `ad.pr_slug` / `ad._slug_from_remote`. A fake that asks the
# code under test what the world looks like is a second sample of the thing in
# doubt, not a model of it: `pr_slug` was WRONG for every fork PR for three
# rounds, and a fixture derived from it would have modelled the same wrong
# world and hidden the bug. Two lines of regex over the same two inputs is a
# model; a call into the script is not.
_FIXTURE_PR_URL = re.compile(r"^https?://[^/]+/([^/]+)/([^/]+)/pull/\d+")
_FIXTURE_REMOTE = re.compile(r"[:/]([^/:]+)/([^/:]+?)(?:\.git)?$")


def fixture_is_cross_repo(payload, origin):
    """Does this PR fixture live in a DIFFERENT repository from `origin`?"""
    u = _FIXTURE_PR_URL.match((payload.get("url") or "").strip())
    r = _FIXTURE_REMOTE.search((origin or "").strip())
    if not u or not r:
        return False
    return (u.group(1), u.group(2)) != (r.group(1), r.group(2))


def fixture_resolved_head(payload, origin, local_head):
    """What `git rev-parse HEAD` answers in the ASSEMBLY checkout, per fixture.

    🔴 ROUND 10 — LIFTED OUT OF `make_runner` SO A GUARD CAN ASK THE SAME
    QUESTION. `test_no_brief_claims_a_verification_its_own_fixture_refutes`
    needs to know whether a scenario models a checkout standing on the PR's
    head; re-deriving that beside the fake would be a second copy of the rule
    the fake applies, and the two would disagree the first time either moved —
    which is the shape this whole module keeps finding in the script.

    It is still a MODEL and not a call into the code under test: what it
    encodes is the fixture's own three-case default, nothing the script decides.
    """
    if local_head is not None:
        return local_head
    if fixture_is_cross_repo(payload, origin):
        return FAKE_ASSEMBLY_HEAD
    return payload.get("headRefOid")


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
    # 🔴 ROUND 8 — THE DEFAULT IS NOW PHYSICALLY POSSIBLE IN BOTH REPO STATES.
    # "The checkout is standing on the PR" is the right default for a PR in
    # THIS repository and a flat impossibility for one in another: no commit of
    # `someone-else/otherproj` is ever `git rev-parse HEAD` in a clone of
    # `example-org/devrc`. Defaulting to the PR's own oid regardless is how a
    # cross-repo delta brief rendered "verified at assembly time to be PR
    # #900's head commit" — the fixture asserted the state the guard exists to
    # rule out. The freak case (a clone with a renamed remote that really does
    # hold the commit) is still reachable: pass `local_head` explicitly.
    resolved_head = fixture_resolved_head(payload, origin, local_head)
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


# 🔴 A REAL FILE ON DISK, because `--claims-file` is the one input this script
# reads without going through the injected runner: `Path(...).read_text` is a
# real open. A `tmp_path` fixture cannot serve it — `SCENARIO_RUNS` entries are
# built by plain callables that every scenario-driven guard invokes, and a
# pytest fixture is not available there. The `TemporaryDirectory` is bound to a
# module global so it outlives collection and is removed at interpreter exit.
_CLAIMS_FILE_DIR = tempfile.TemporaryDirectory(prefix="audit-dispatch-claims-")
CLAIMS_FILE = Path(_CLAIMS_FILE_DIR.name) / "round-2-claims.md"
CLAIMS_FILE.write_text(CLAIMS_BLOCK_R2, encoding="utf-8")


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
    # rule was about a state the very next section denies. The scope is stated
    # by OWNERSHIP.
    #
    # 🔴 ROUND 8. The first sentence now names the CLONE as well as the
    # worktree, because the recipe two lines above it in WHERE TO WORK writes to
    # the clone — `git -C <your local clone of owner/name> worktree add …` is a
    # write to the clone, made before the worktree it grants exists. The second
    # sentence is UNCHANGED; what moved to meet it was `no-fetch`, which had
    # scoped itself to "the copy YOU made FOR THIS AUDIT" and so forbade that
    # very command. Both sides are pinned to `NO_WRITE_SCOPE` now.
    #
    # 🔴 ROUND 10. Round 8's "and so is the clone you made it from: fetching
    # and checking out inside EITHER is fine" granted the two operations with
    # cross-session blast radius over a tree the recipe names only as `<your
    # local clone of owner/name>` — on this host, in practice,
    # `~/workspace/<repo>`. The recipe performs exactly ONE write there,
    # `worktree add`, so that is what the grant now names; `fetch`/`pull`/
    # `checkout` are refused there explicitly rather than left to the reader's
    # reading of "you made". The forward-reference sentence is untouched and
    # still spells `NO_WRITE_SCOPE`.
    #
    # 🔴 ROUND 12. That refusal was an ENUMERATION of three verbs, over the one
    # tree in this brief whose blast radius is outside it: the `no-fetch`
    # clause is scoped to "every checkout you did not make" and the clone is
    # deliberately outside it, so this sentence is the ONLY rule covering that
    # tree. `git -C <clone> remote update` — which is what an auditor reaches
    # for when `fetch` is refused by name and they still need the PR branch
    # present before `worktree add` — was unenumerated, and so were `switch`,
    # `restore`, `reset`, `branch -f` and `gc`. The permission is now stated
    # positively with a universal refusal after it.
    #
    # 🔴 ROUND 13. The grant and the recipe it covers could not BOTH be obeyed.
    # `worktree add <the PR's head branch>` resolves a ref the clone only has
    # after a FETCH, and this sentence refused every write but `worktree add` —
    # measured rc 128 on scratch repos for an unfetched clone, and rc 128 for a
    # FORK PR after any fetch, because that branch is never in `origin`. The
    # one compliant workaround (`--detach`, then fetch inside the worktree)
    # writes into the clone anyway: a linked worktree shares the clone's ref
    # store and object store. So the recipe changed to a namespaced, additive
    # `refs/pull/<n>/head` fetch plus a DETACHED add, and this grant now names
    # that set. Still positive, still ending in the universal.
    "own-worktree-is-writable": (
        "That worktree is YOURS: checking out inside it is fine — but it is "
        "not a separate repository. A linked worktree SHARES the clone's ref "
        "store and object store, so a fetch run inside it writes to the clone "
        "as well; there is no fetching into the worktree alone, and a rule "
        "that pretended otherwise would be obeyed by a command that breaks "
        "it. So the clone's permission is the set of writes the recipe above "
        "actually makes, stated positively: the `fetch` into `refs/audit/`, "
        "the detached `worktree` add, and the `worktree` remove and "
        "`update-ref` that undo them. Those are the ONLY writes this brief "
        "asks for and the ONLY ones you may make — every other command that "
        "writes there is refused, named or not, whoever made that clone, "
        "because other sessions may be standing in it. The no-write rule "
        "below is about every checkout you did not make."
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
    # 🔴 ROUND 8 — THE MISSING CELL, and it is a textbook isolation seam. Both
    # cross-repo scenarios ran at ROUND 1, where THE RANGE and THE LEDGER do
    # not exist; both delta scenarios were SAME-REPO. So the pair
    # cross-repo × delta — where every git command goes to a repository that
    # holds neither end of the range — was covered by nothing, and the two
    # sections it breaks were each hermetically tested on the other axis.
    # `claude/RULES.md` -> isolation-seam: ask which surface your fixture does
    # NOT load.
    "cross-repo-delta": lambda: (["900", "--round", "3"],
                                 {"pr": OTHER_REPO_PR,
                                  "comments": [CLAIMS_BLOCK_R2]}),
    # 🔴 ROUND 10 — THE MISSING CELL'S OWN MIRROR. Round 8 added the
    # cross-repo × delta pair and left the other axis: a PR whose `headRefOid`
    # is UNKNOWN. `cross-repo-delta` is built from `OTHER_REPO_PR`, which HAS
    # one, and the module's single `headRefOid=None` test ran the SAME-repo
    # fixture — so the state where `range_tip` answers a PLACEHOLDER inside a
    # cross-repo rev spec was reachable by no scenario at all.
    "cross-repo-delta-no-head-sha": lambda: (
        ["900", "--round", "3"],
        {"pr": dict(OTHER_REPO_PR, headRefOid=None),
         "comments": [CLAIMS_BLOCK_R2]},
    ),
    # The SAME-repo spelling of that state: nothing here is cross-repo, the
    # head check simply never learned a sha. `range_tip` returns the identical
    # placeholder, so a fix scoped to the cross-repo branch alone leaves this
    # one printing an unrunnable rev spec.
    "delta-no-head-sha": lambda: (
        ["900", "--round", "3"],
        {"pr": dict(DEFAULT_PR, headRefOid=None),
         "comments": [CLAIMS_BLOCK_R2], "local_head": None},
    ),
    # 🔴 ROUND 10 — THE STATE `render_range`'s OWN COMMENT SAYS IS REAL and no
    # scenario modelled: a clone whose `origin` names a different repository
    # while HOLDING the PR's head commit (a renamed remote, a mirror). The
    # relation is `cross`; the head check PASSES. It is what separates
    # "different slug" from "holds neither end of the range", and THE LEDGER
    # was conflating them.
    "cross-repo-renamed-remote-delta": lambda: (
        ["900", "--round", "3"],
        {"pr": OTHER_REPO_PR, "comments": [CLAIMS_BLOCK_R2],
         "local_head": FAKE_OTHER_HEAD_OID},
    ),
    # 🔴 ROUND 10's SEVENTH INSTANCE, found by sweeping rather than from a
    # finding: `base_ref` is `data.get("baseRefName") or "main"` — a DEFAULT —
    # and every site printing it presented it as a fact about the PR. This is
    # the state `--claims-file` mode is ALWAYS in, because it consults no `gh`
    # and hardcodes the field.
    "delta-assumed-base": lambda: (
        ["900", "--round", "3"],
        {"pr": {k: v for k, v in DEFAULT_PR.items() if k != "baseRefName"},
         "comments": [CLAIMS_BLOCK_R2]},
    ),
    # 🔴 ROUND 12 — THE MODE THE COMMENT ABOVE NAMES AS ALWAYS ASSUMED, AND NO
    # SCENARIO RAN IT. Round 10 modelled the assumed-base state by DELETING
    # `baseRefName` from a `gh` payload, which is the rarer of the two ways to
    # reach it; `--claims-file` reaches it on every single run. The script
    # meanwhile HARDCODED `baseRefName: "main"` in that branch, so the state
    # the guard exists for was unreachable through the door it is always open
    # on. Measured at `88b4105c`: `--round 3 --claims-file <f>` -> rc 0,
    # silent stderr, banner ABSENT.
    #
    # 🔴 Its `pr` payload is DELIBERATELY ABSENT. In this mode `gh_pr_facts`
    # is never called, so a payload here would be a fixture nothing reads —
    # and reading one to decide what to expect is exactly the fixture defect
    # `scenario_base_is_assumed` was written to avoid.
    "claims-file-assumed-base": lambda: (
        ["900", "--round", "3", "--claims-file", str(CLAIMS_FILE)],
        {},
    ),
    # 🔴 ROUND 13 — THE OTHER SIDE OF `repo_unknown_reason`, so its guard is
    # not "delete the `gh` sentence". `gh` IS consulted here and genuinely
    # reports nothing this script can read a repository out of: no `url`, and
    # `isCrossRepository` true, so the head fields are the FORK's and
    # `pr_slug` refuses to answer from them. `repo_relation` is "unknown" for
    # a reason that IS about `gh`, and the brief must say so.
    #
    # `claims-file-assumed-base` above is the OPPOSITE cause and needed no new
    # scenario: round 12 added it, and it was already rendering the WHERE TO
    # WORK contradiction this round found — `gh` blamed by a run that consults
    # no `gh`, at rc 0, with the truth two headings away in THE RANGE.
    "unknown-repo-gh-said-nothing": lambda: (
        ["900"],
        {"pr": dict(DEFAULT_PR, url="", isCrossRepository=True)},
    ),
    # 🔴 ROUND 16 — THE THREE MISSING CELLS OF THE TARGET × CHECKOUT-KIND GRID,
    # and the reason they are added TOGETHER rather than one per finding. Round
    # 15 restored the commands pin within each target and guarded its own
    # non-degeneracy with `any(len(kinds) >= 2)`. Measured at `ba321c06`:
    #
    #     probed-devrc-shape    n=6  {private, shared, unknown}
    #     cross-repo-otherproj  n=5  {private, shared}
    #     repo-unknown          n=2  {shared}
    #
    # `any` is satisfied by the first row alone, so it certified NOTHING about
    # the other two — and no NOT-PROBED scenario was `unknown` kind at all.
    # Measured: inserting into `_toolchain_not_probed` a sentence keyed on
    # `facts.worktree.kind == "unknown"` — one that flatly contradicts WHERE TO
    # WORK, exactly round 15's F1 in its third state — left the module at
    # **122 passed**, while the same insertion keyed on `"private"` was killed.
    #
    # The grid is now COMPLETE and the assertion asks for the whole row, not for
    # two of it: every target spans all three checkout states. That is the
    # mechanical form of the property, so the next un-covered cell is a hole
    # this guard NAMES rather than one it happens to miss.
    "cross-repo-unreadable-worktree": lambda: (
        ["900"], {"pr": OTHER_REPO_PR, "git_dirs": UNREADABLE_GIT_DIRS},
    ),
    "unknown-repo-private-worktree": lambda: (
        ["900"],
        {"pr": dict(DEFAULT_PR, url="", isCrossRepository=True),
         "git_dirs": PRIVATE_GIT_DIRS},
    ),
    "unknown-repo-unreadable-worktree": lambda: (
        ["900"],
        {"pr": dict(DEFAULT_PR, url="", isCrossRepository=True),
         "git_dirs": UNREADABLE_GIT_DIRS},
    ),
}
SCENARIOS = tuple(SCENARIO_RUNS)

# 🔴 WHAT THE COMMANDS BAR IS ALLOWED TO VARY WITH — declared, not inferred.
#
# Round 14 split TOOLCHAIN on one axis (reason vs commands) and pinned only the
# REASON across scenarios. Round 15 measured what that left open: inserting
#
#     (f"Your checkout is a PRIVATE linked worktree ({facts.worktree}), so "
#      "the SHARED checkout is the right place to run the gate."
#      if facts.worktree and facts.worktree.kind == "private" else
#      f"Worktree kind: {facts.worktree.kind if facts.worktree else None}.")
#
# into the PROBED body — a sentence that varies with AUDITOR STATE and flatly
# contradicts WHERE TO WORK — left the module at **115 passed**, while the same
# sentence in the old whole-section `return [...]` at `9e23c379` was killed by
# this very guard. Round 5's finding, reopened by sitting three lines lower.
#
# The relaxation was justified for the COMMANDS, which must differ by TARGET
# REPOSITORY. It was not justified for auditor state, which the probe never
# reads. So the pin is restored WITHIN each target: two scenarios that probe the
# same thing must render byte-identical commands, whatever the round, the head
# sha, the base, or the kind of checkout the brief was assembled in.
#
# 🔴 THE KEY IS NOT "PROBE ROOT". That was the first spelling and it is too
# coarse: `cross-repo` and `unknown-repo-gh-said-nothing` both probe NOTHING,
# and the not-probed branch legitimately names `facts.repo` and says WHY it
# could not probe — two different sentences for two different states. The axes
# the bar may depend on are the target's identity, its content, and whether it
# was probeable; everything else is auditor state.
TOOLCHAIN_TARGET_KEYS = frozenset({
    "probed-devrc-shape",     # a real checkout of the PR's repo, on disk
    "cross-repo-otherproj",   # a DIFFERENT repository is in the cwd
    "repo-unknown",           # which repository the PR is in was never learned
})
TOOLCHAIN_TARGET_OF = {
    "delta": "probed-devrc-shape",
    "degenerate": "probed-devrc-shape",
    "private-worktree": "probed-devrc-shape",
    "unreadable-worktree": "probed-devrc-shape",
    "delta-no-head-sha": "probed-devrc-shape",
    "delta-assumed-base": "probed-devrc-shape",
    "cross-repo": "cross-repo-otherproj",
    "cross-repo-private": "cross-repo-otherproj",
    "cross-repo-delta": "cross-repo-otherproj",
    "cross-repo-delta-no-head-sha": "cross-repo-otherproj",
    "cross-repo-renamed-remote-delta": "cross-repo-otherproj",
    "cross-repo-unreadable-worktree": "cross-repo-otherproj",
    "claims-file-assumed-base": "repo-unknown",
    "unknown-repo-gh-said-nothing": "repo-unknown",
    "unknown-repo-private-worktree": "repo-unknown",
    "unknown-repo-unreadable-worktree": "repo-unknown",
}

# The three checkout states, by the phrase `render_checkout_state` prints for
# each. Spelled here so a reword breaks this loudly rather than silently
# collapsing every scenario into "kind unknown" and making the non-degeneracy
# assertion below vacuous.
CHECKOUT_KIND_MARKERS = {
    "private": "PRIVATE linked worktree",
    "shared": "SHARED — other sessions and agents are in this tree",
    "unknown": "🔴 COULD NOT DETERMINE",
}


def checkout_kind_of(brief):
    """-> 'private' | 'shared' | 'unknown', or an assertion if it is ambiguous."""
    section = checkout_section(brief)
    hit = [k for k, marker in CHECKOUT_KIND_MARKERS.items() if marker in section]
    assert len(hit) == 1, (
        f"THE CHECKOUT names {len(hit)} of the three states ({hit}); this "
        "helper can no longer tell them apart, so any claim built on it is "
        f"vacuous:\n{section}"
    )
    return hit[0]


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


def test_a_block_that_parses_but_yields_no_anchor_is_refused():
    """🔴 REGRESSION. Red at `28492af2`, which rendered ``Diff `None..<sha>` ``.

    🔴 THE FIFTH INSTANCE of this ladder's recurring confusion, found by
    sweeping for the shape rather than from a finding: `newest is not None`
    (a block PARSED) was read as "there is something to diff FROM". Two
    different facts. `audited=..` satisfies `\\baudited=(\\S+)`, splits into two
    EMPTY halves, and `range_anchor` answers None over a block whose claims are
    perfectly readable — so the delta refusal, which asks only whether a block
    exists, let it through.

    Measured at the base, `--round 3` over that block, rc 0 and silent stderr:

        THE RANGE : Diff **`None..1111…5555`** … "This checkout's HEAD is
                    `1111…5555`, verified at assembly time to be PR #900's
                    head commit"
        THE LEDGER: "Not measured: a first, full audit has no previous round
                    to attribute against."

    A literal Python `None` inside a git rev spec — a command that cannot run —
    under the most confident banner the brief has; and the ROUND-1 cause given
    for a round-3 brief that HAS a previous round, which is the false-cause
    shape this module has already fixed at three other sites.
    """
    headless = "```audit-claims round=2 audited=..\n1. a claim\n```"
    # 🔴 POSITIVE CONTROL for the fixture: the block must actually PARSE, with
    # its claims intact. If it were merely malformed this would be REFUSAL 1's
    # case, already covered, and the test would prove nothing new.
    blocks, malformed = ad.parse_claims_blocks([headless])
    assert len(blocks) == 1 and not malformed, (
        f"the fixture no longer parses cleanly ({malformed}), so this test is "
        "exercising the malformed-block path and not the no-anchor one"
    )
    assert blocks[0].items == ["a claim"], (
        "the parsed block carries no claims, so it is not the state this "
        "refusal is about — a block with nothing in it is REFUSAL 1's case"
    )
    assert ad.range_anchor(blocks[0]) is None, (
        "the fixture now yields an anchor, so it cannot reach this refusal"
    )

    rc, out, err = run_main(["900", "--round", "3"], comments=[headless])
    assert rc == 2, (
        f"\n\na delta round with a block that yields NO ANCHOR exited {rc}. At "
        "rc 0 the brief hands the auditor a range containing a literal "
        f"`None`:\n{out[:600]}"
    )
    assert "None.." not in out, (
        f"\n\nthe brief interpolated a literal `None` into the range:\n{out}"
    )
    assert "no sha this script can read" in err, (
        f"\n\nthe refusal does not say WHY it refused:\n{err}"
    )
    # 🔴 IT MUST NOT NAME THE ROUND-1 CAUSE. That is the whole point: an anchor
    # that is missing and an anchor that is unreadable need opposite fixes, and
    # naming the wrong one sends the operator off to add a block they have.
    assert "a first, full audit has no previous round" not in err + out, (
        f"\n\nthe refusal states the ROUND-1 cause over a round-3 brief that "
        f"HAS a previous round:\n{err}"
    )
    assert "NOT the round-1 case" in err, (
        f"\n\nthe refusal does not distinguish itself from the round-1 case, "
        f"which is the reader's first guess:\n{err}"
    )
    # 🔴 NEGATIVE CONTROL: an ordinary block must still be accepted, or the
    # refusal above is satisfied by refusing everything.
    rc2, out2, _ = run_main(["900", "--round", "3"], comments=[CLAIMS_BLOCK_R2])
    assert rc2 == 0 and "## THE RANGE" in out2, (
        "the no-anchor refusal now fires for a well-formed block too"
    )


def test_a_claims_block_more_than_one_round_behind_is_warned_about():
    """🔴 INVARIANT GUARD; its evidence is mutant V16, not a base ref.

    At `28492af2` there is no such warning at all, so a red there would be an
    absence the fix adds rather than a wrong answer observed — which this
    module says repeatedly is not evidence of anything. The same filing as the
    emitted legend (Z5).

    🔴 IT IS THE MIRROR OF A WARNING THAT ALREADY EXISTED, and the asymmetry is
    the finding: a block from a LATER round warned ("check you are not
    re-auditing a round that already ran"), a block four rounds EARLIER was
    silent. `--round 7` over a `round=2` block puts round 2's claims under WHAT
    WAS CLAIMED FIXED, anchors the range on round 2's sha, and frames the audit
    on them — a delta over everything since round 2 presented as a delta since
    round 6.

    🔴 AND IT MUST NEVER BLOCK. Skipping a round number is legitimate, and `gh
    pr view --json comments` returns ISSUE comments only, so a newer block
    posted as a REVIEW comment is invisible here — a refusal would be wrong in
    both cases. Asserted below, because "warns" and "does not block" are two
    claims and the second is the one a later round would break.
    """
    old = ("```audit-claims round=2 audited=aaaa1111..bbbb2222\n"
           "1. a claim from four rounds ago\n```")
    rc, out, err = run_main(["900", "--round", "6"], comments=[old])
    assert rc == 0, f"the gap warning BLOCKED (rc {rc}); it must not: {err}"
    assert "a claim from four rounds ago" in out, (
        "the block was not used at all, so this run is not the state the "
        "warning is about"
    )
    assert "round(s) in between" in err, (
        "\n\nno warning that the newest claims block is FOUR rounds behind the "
        f"round asked for. The brief presents round 2's claims as this "
        f"round's framing and says nothing about the gap.\nstderr: {err!r}"
    )
    assert "3 round(s)" in err, (
        f"\n\nthe warning does not count the gap correctly — rounds 3, 4 and 5 "
        f"posted nothing, so the gap is 3.\nstderr: {err!r}"
    )
    # 🔴 NEGATIVE CONTROL: the ADJACENT case must stay silent, or the warning
    # fires on every ordinary round and is worth nothing.
    adjacent = ("```audit-claims round=5 audited=aaaa1111..bbbb2222\n"
                "1. the previous round's claim\n```")
    rc2, _, err2 = run_main(["900", "--round", "6"], comments=[adjacent])
    assert rc2 == 0
    assert "round(s) in between" not in err2, (
        f"\n\nthe gap warning fires for a block from the IMMEDIATELY previous "
        f"round, which is the normal case:\nstderr: {err2!r}"
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
    # 🔴 ROUND 13 CHANGED WHICH REMEDY THIS ASSERTS, and the old one was the
    # finding. It pinned "`--repo owner/name`" — but this run's unresolved half
    # is the CWD's slug, and `--repo` states the PR's. `repo_relation` needs
    # BOTH, so re-running with `--repo` lands in this exact branch again: a
    # remedy prescribed for a cause it cannot address, the same class as the
    # false `gh` attribution two guards below. What answers THIS run's question
    # is the origin lookup, so that is what the brief now offers and what this
    # pins.
    assert f"git -C {FAKE_REPO_DIR} remote get-url origin" in out, (
        "the branch does not tell the operator how to answer the question it "
        "could not answer — which side is unresolved here is the CWD's slug"
    )
    assert "`--repo owner/name` to state" not in out, (
        "\n\nthe brief offers `--repo owner/name` as the remedy for a run "
        "whose unresolved half is the CWD's slug. `--repo` states the PR's "
        "side; the comparison needs both, so that re-run lands right back in "
        "this branch."
    )


# --------------------------------------------------------------------------- #
# 🔴 ROUND 13 — THE NINTH INSTANCE OF ONE MISTAKE, AND THE THIRD IN ITS FAMILY
# --------------------------------------------------------------------------- #
# A predicate read as a STRONGER fact than it carries. `headRefOid` (round 10),
# `baseRefName` (round 12), and now WHICH REPOSITORY THE PR IS IN: the brief
# said "`gh` did not report which repository the PR lives in" for a run that
# consults no `gh` at all, two headings from its own sentence saying so.
#
# The two probes below are deliberately different shapes. The first reads the
# CAUSE the brief states and drives BOTH causes, so its fix cannot be "delete
# the `gh` sentence". The second is a CLASS guard over every scenario: no
# `gh pr view` this script hands an auditor may omit `--repo`, whatever section
# prints it.

def where_to_work_section(brief):
    """The WHERE TO WORK block alone, sliced off the next `## ` heading."""
    start = brief.index("## WHERE TO WORK")
    rest = brief[start + len("## WHERE TO WORK"):]
    nxt = rest.find("\n## ")
    return brief[start:start + len("## WHERE TO WORK")
                 + (nxt if nxt != -1 else len(rest))]


# 🔴 EVERY WAY THIS SECTION CAN ATTRIBUTE THE UNKNOWN TO `gh`. Not one phrase:
# the defect had TWO spellings in one section — the branch sentence ("`gh` did
# not report which repository the PR lives in") and the `REPO_UNKNOWN` sentinel
# printed three lines under it ("UNKNOWN (not reported by `gh`; …)") — and a
# guard on either alone is satisfied while the other still lies.
_BLAMES_GH = re.compile(
    r"reported by `gh`"
    r"|`gh[^`]*` (?:was consulted|did not report)"
    r"|`gh[^`]*`[^.]*? reported no"
)

# The literal cause the `--claims-file` branch owns. Every OTHER section of the
# same brief already spells this for `headRefOid` and `baseRefName`; the point
# is that WHERE TO WORK now agrees with them.
CLAIMS_FILE_CAUSE = "consults no `gh`"


def test_no_brief_blames_gh_for_a_repo_gh_was_never_asked_about():
    """🔴 REGRESSION. Red at `6349a8b9`, scenario `claims-file-assumed-base`.

    `render_worktree_directive` had TWO branches for the unknown state — no
    `origin` remote, else "`gh` did not report which repository the PR lives
    in" — and no third for "`gh` was never consulted". `--claims-file` mode
    with a resolvable cwd slug therefore took the `gh` branch, and round 12's
    own new scenario rendered a brief that CONTRADICTS ITSELF: THE RANGE says
    "this run is `--claims-file` mode, which consults no `gh`" while WHERE TO
    WORK says `gh` was asked and did not answer. Measured at that base with a
    real checkout: rc 0, silent stderr.

    Both causes are driven. A guard that only checked the claims-file side
    would be satisfied by deleting the `gh` sentence outright, which would
    make the brief silent about a cause that is real and common.
    """
    where = where_to_work_section(brief_for_scenario("claims-file-assumed-base"))
    assert "COULD NOT DETERMINE" in where, (
        f"\n\nthe scenario stopped rendering the unknown branch, so this "
        f"guard is asserting over the wrong section:\n{where}"
    )
    assert not _BLAMES_GH.search(where), (
        "\n\nWHERE TO WORK blames `gh` for not reporting the PR's repository "
        "in a run that consulted no `gh`. The same brief says so itself two "
        "headings away — one document, two contradictory statements, at rc 0 "
        f"and with silent stderr.\n{where}"
    )
    assert CLAIMS_FILE_CAUSE in where, (
        "\n\nWHERE TO WORK does not name the cause this run actually has. "
        "The branch that DECIDED not to consult `gh` is the one that knows "
        "why, exactly as `no_sha_reason` and `base_assumed_reason` are set "
        f"there.\n{where}"
    )

    # 🔴 THE OTHER SIDE, so the fix is not "delete the sentence". Here `gh`
    # really was consulted and really reported nothing readable.
    where = where_to_work_section(
        brief_for_scenario("unknown-repo-gh-said-nothing")
    )
    assert "COULD NOT DETERMINE" in where, (
        f"\n\nthe `gh`-was-asked scenario no longer reaches the unknown "
        f"branch:\n{where}"
    )
    assert _BLAMES_GH.search(where), (
        "\n\nWHERE TO WORK stopped naming `gh` even for a run that DID "
        "consult it and got nothing back. That is the real cause here, and "
        "a brief that will not name it sends the operator looking at the "
        f"wrong thing.\n{where}"
    )
    assert CLAIMS_FILE_CAUSE not in where, (
        "\n\nthe `gh` scenario now claims to be `--claims-file` mode: the "
        f"per-cause reason is stuck on one value.\n{where}"
    )


# 🔴 EVERY `gh pr view` THIS SCRIPT HANDS OVER, wherever it prints one. Round
# 12 fixed exactly one site (`unresolved_tip_note`'s `headRefOid` lookup) and
# left the sibling in WHERE TO WORK — so this reads the class, not the site.
#
# 🔴 THE PR NUMBER IS THE DISCRIMINATOR, and it is a real one rather than a
# convenience. A COMMAND the auditor is meant to run names the PR
# (`gh pr view 900 --json url`); a PROVENANCE sentence names only the API the
# script itself read a field from ("read from `gh pr view --json headRefOid`")
# and is not something anyone types. Requiring `--repo` on the second would be
# a fix to prose that never runs — the "widen the implementation to the
# sentence, but no wider" half of `claude/RULES.md` -> guards-narrower. The
# NEGATIVE control below pins that both readings are live.
_GH_PR_VIEW = re.compile(r"gh pr view \d+[^\n`]*")

# A provenance mention, verbatim from THE LEDGER's cross-repo hand-over. It
# must NOT be flagged: it is not a command.
PROVENANCE_GH_MENTION = (
    "read from `gh pr view --json headRefOid`, not resolved in any tree"
)

# The bare lookup round 12 left in place: run in the auditor's own repository
# it returns THAT repository's PR #900 at rc 0, so the only answer it can ever
# produce is "same repo" — the answer the section it sits in exists because it
# cannot rule out.
ROUND_12_UNQUALIFIED_LOOKUP = (
    "gh pr view 900 --json url          # the repo the PR lives in"
)


def unqualified_gh_lookups_in(text):
    return [c for c in _GH_PR_VIEW.findall(text) if "--repo" not in c]


def test_no_gh_pr_view_this_script_prescribes_omits_repo():
    """🔴 REGRESSION. Red at `6349a8b9`, scenario `claims-file-assumed-base`.

    `gh pr view <n> --json <field>` with no `--repo` resolves against whatever
    repository the auditor's CWD is in. Every brief that prints one has ALSO
    just told them their CWD may be — or definitely is — a different
    repository from the PR's, so the command returns another project's PR
    #<n>, at rc 0, and reads as confirmation.

    Round 12 closed this for the `headRefOid` lookup and wrote a paragraph
    explaining why; the `--json url` lookup one section over kept the bare
    form. So this guard is over the CLASS and over every scenario, rather than
    over the one site a finding was filed against — `claude/RULES.md` ->
    guards-narrower.
    """
    assert unqualified_gh_lookups_in(ROUND_12_UNQUALIFIED_LOOKUP), (
        "POSITIVE CONTROL: the probe cannot see round 12's own bare lookup, "
        "so a clean result below would say nothing at all"
    )
    # 🔴 NEGATIVE CONTROL, and it is not decoration: without it the cheapest
    # way to green this guard is to bolt `--repo` onto a PROVENANCE sentence
    # nobody runs, which fixes nothing and makes the next reader think the
    # class is covered.
    assert not unqualified_gh_lookups_in(PROVENANCE_GH_MENTION), (
        "NEGATIVE CONTROL: the probe flags a sentence describing where the "
        "script read a field, not a command anyone types. Widened that far it "
        "demands `--repo` on prose and stops meaning anything."
    )
    seen = 0
    for scenario in SCENARIOS:
        brief = brief_for_scenario(scenario)
        found = _GH_PR_VIEW.findall(brief)
        seen += len(found)
        bare = unqualified_gh_lookups_in(brief)
        assert not bare, (
            f"\n\nscenario {scenario!r} hands the auditor a `gh pr view` with "
            f"no `--repo`: {bare}\n"
            "  It resolves against their CWD's repository, returns that "
            "project's PR of the same number at rc 0, and the one answer it "
            "can produce is the 'same repo' this brief cannot rule out."
        )
    # 🔴 A ZERO HERE IS NOT A PASS. If no scenario prints a `gh pr view` at
    # all, the loop above proved nothing about a class it never observed.
    assert seen, (
        "no scenario in this module renders a `gh pr view` command, so the "
        "sweep above is a scan wired to nothing"
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
    assert f"nix develop {FAKE_REPO_DIR} -c python3 -m pytest" in out
    assert "-p no:cacheprovider" in out
    assert "No module named pytest" in out and "WRONG SHELL" in out, (
        "the wrong-shell diagnosis is missing — it was present in 9 of the "
        "first 9 measured dispatches and absent from 3 of the last 5"
    )
    assert "nix build <your worktree>#checks.x86_64-linux.pytests" in out
    assert "nix build <your worktree>#checks.x86_64-linux.nodetests" in out
    assert "gate.sh --tier both" in out
    assert "git --version" in out
    _assert_every_nix_build_prints_its_log(out)


def _assert_every_nix_build_prints_its_log(out: str) -> None:
    """EVERY emitted `nix build` line must carry `-L`/`--print-build-logs`.

    🔴 WHY THIS IS STRUCTURAL AND NOT A SPELLING PIN. The two substring asserts
    above were live, and green, for the whole time the brief emitted
    `nix build …#checks…pytests` with NO `-L` — under a sentence telling the
    auditor to "read each runner's own `RESULT:` line". `nix build` prints no
    build log for a build that SUCCEEDS without `-L`, so the brief instructed a
    read that could not succeed, and a substring assert on the COMMAND cannot
    see a missing FLAG. Proven by mutation: stripping ` --no-link -L` from both
    emitted lines left the whole module green (123 passed, mutant SURVIVED),
    while renaming `.pytests` -> `.PYTESTS` in the same emitter turned it red —
    so the harness runs and reaches those lines; it just could not see flags.

    Scanning every `nix build` line rather than pinning two exact strings means
    a THIRD tier added later is covered automatically, and an unrelated reword
    of the surrounding prose cannot redden it.
    """
    offenders = [
        line.strip() for line in out.splitlines()
        if line.strip().startswith("nix build")
        and "-L" not in line.split()
        and "--print-build-logs" not in line
    ]
    assert not offenders, (
        "these emitted `nix build` lines carry no `-L`/`--print-build-logs`, so "
        "a SUCCEEDING build prints no log and the brief's instruction to read a "
        "`RESULT:` line cannot be followed:\n  " + "\n  ".join(offenders))


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
    tip = rendered_range(out).partition("..")[2]
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

    # 🔴 ROUND 7 ASSERTED HERE THAT THE LEDGER'S PROVENANCE LINE KEEPS
    # `..HEAD` — as the control saying this fix was scoped to the
    # auditor-facing instruction. Round 8 measured that the scoping argument
    # never justified the token (`measure_ledger` refuses unless the head check
    # PASSED, so the sha and `HEAD` named the SAME COMMIT when the command ran),
    # and moved that line onto the sha as well. The claim now lives in
    # `test_the_ledgers_provenance_line_names_the_sha_it_resolved_not_head`,
    # alone, rather than as a control inside a test about a different section —
    # a control that asserts the OPPOSITE of a later fix is how a round gets
    # talked out of one.


# --------------------------------------------------------------------------- #
# 🔴 ROUND 8 — EVERY REF THE BRIEF HANDS OVER RESOLVES WHERE THE READER IS
# --------------------------------------------------------------------------- #
def ledger_section(brief):
    """THE LEDGER block alone, sliced off the next `## ` heading."""
    start = brief.index("## THE LEDGER")
    rest = brief[start + len("## THE LEDGER"):]
    nxt = rest.find("\n## ")
    return brief[start:start + len("## THE LEDGER")
                 + (nxt if nxt != -1 else len(rest))]


def test_the_ledgers_provenance_line_names_the_sha_it_resolved_not_head():
    """🔴 REGRESSION. Red at `28492af2`, which printed ``aaaa1111..HEAD --not``.

    Round 7 moved THE RANGE's tip off `HEAD` and kept this line on it, with a
    provenance argument that is sound as far as it goes: that command really
    did run here, in the tree just verified. What the argument does not
    establish is the SPELLING. `measure_ledger` refuses unless the head check
    passed, so when it ran `HEAD` and `local_sha` were the SAME COMMIT — the
    sha reports exactly what was measured and is the only spelling that
    survives being copied.

    And it is copied: this was the last command in the brief whose tip means
    something else in the reader's worktree, and the paragraph under it invites
    a re-run ("if the number looks large, that is the first thing to check").
    Concrete failure: a commit lands after assembly, the auditor re-runs the
    line in their own tree, `..HEAD` now covers it, and the longer file list
    reads as the previous round having under-reported its payload.
    """
    rc, out, err = run_main(["900", "--round", "3"], comments=[CLAIMS_BLOCK_R2])
    assert rc == 0, err
    led = ledger_section(out)
    # 🔴 POSITIVE CONTROL: a brief with no provenance line at all would satisfy
    # every "must not contain" below while reporting nothing.
    assert "--remerge-diff" in led, (
        f"THE LEDGER prints no provenance command at all, so the assertions "
        f"below are about nothing:\n{led}"
    )
    anchor = rendered_range(out).partition("..")[0]
    assert f"{anchor}..{FAKE_HEAD_OID} --not" in led, (
        "\n\nTHE LEDGER's provenance line does not name the sha it resolved. "
        f"It measured `{anchor}..HEAD` in a checkout whose HEAD was verified "
        f"to be {FAKE_HEAD_OID}; spelling that sha reports the same commit and "
        f"re-runs to the same answer anywhere.\n{led}"
    )
    assert "..HEAD" not in led, (
        "\n\nTHE LEDGER still hands the reader a `..HEAD`. That token resolves "
        "where THEY are standing — a tree cut after this brief was assembled "
        f"— and the brief tells them to re-run this command.\n{led}"
    )


def test_the_degenerate_range_names_shas_at_both_ends():
    """🔴 REGRESSION. Red at `28492af2`, which rendered ``aaaa1111..HEAD``.

    Round 7's tip fix was HALF-APPLIED: it moved the VERIFIED branch off the
    token and its own comment recorded "Only the VERIFIED branch hardcoded
    `HEAD`", while the DEGENERATE branch of the same `if`/`elif` — four lines
    above it — still handed one out. The suite stayed fully green, so nothing
    said otherwise.

    🔴 THE BANNER OVER THAT RANGE MAKES AN ABSOLUTE CLAIM, which is what makes
    the spelling load-bearing rather than cosmetic: "Both ends of the range
    name the same commit, so it can never contain anything, whatever the PR
    looks like." That is a property of the ASSEMBLY tree at assembly time. Under
    `aaaa1111..HEAD` it is simply false in the auditor's tree the moment a
    commit lands in between — the range is non-empty there, and the loudest
    banner in the brief is telling them it cannot be.
    """
    brief = brief_for_scenario("degenerate")
    section = range_section(brief)
    # 🔴 POSITIVE CONTROL: assert we are reading the DEGENERATE branch. Without
    # it a fixture change that stops producing a self-range would leave every
    # assertion below true of the ordinary verified branch instead.
    assert "DEGENERATE RANGE" in section, (
        f"this scenario no longer renders the degenerate branch:\n{section}"
    )
    frm, _, tip = rendered_range(brief).partition("..")
    assert tip != "HEAD", (
        "\n\nthe DEGENERATE branch hands the auditor `..HEAD` while the banner "
        "under it says the range 'can never contain anything, whatever the PR "
        f"looks like'. In their tree it can.\n{section}"
    )
    assert tip == SELF_RANGE_HEAD, (
        f"\n\nthe degenerate range ends at {tip!r}, not at the head that was "
        f"verified ({SELF_RANGE_HEAD})."
    )
    assert ad.same_commit(frm, tip), (
        f"\n\n`{frm}..{tip}` is not a self-range, so the DEGENERATE banner "
        "above it is being printed over an ordinary range."
    )


def test_a_cross_repo_range_states_the_impossibility_not_a_moved_checkout():
    """🔴 REGRESSION. Red at `28492af2`.

    THE FOURTH INSTANCE OF THIS LADDER'S RECURRING CONFUSION, on the REPOSITORY
    axis: the repo `gh` was asked about versus the repo `git` was run in. Every
    git command goes to `repo_dir`, the assembly checkout, while the PR's facts
    come from `gh` about `facts.repo`. Cross-repo those are different
    repositories, so `verify_head_is_the_pr` is STRUCTURALLY unable to pass and
    the could-not-verify branch fires on every cross-repo delta round.

    Measured at the base, in this scenario: the brief said "this checkout's
    HEAD is `9999…`, but the PR's head is `aaaa…` — the two are DIFFERENT
    COMMITS", then "Resolve the range in a tree that CONTAINS the PR's head
    before trusting any number derived from it". Both sentences describe a
    checkout that has MOVED and can be moved back. Neither is true here: there
    is no such tree in this repository and there never will be, so the
    instruction is unfollowable and the reader is sent looking for a fix that
    does not exist.
    """
    brief = brief_for_scenario("cross-repo-delta")
    section = range_section(brief)
    assert "CROSS-REPO" in brief, (
        "this scenario no longer renders the cross-repo branch, so the "
        "assertions below are about a different configuration"
    )
    # 🔴 POSITIVE CONTROL for the slice: a section with no verdict at all would
    # satisfy every "must not say" below.
    assert "🔴" in section, f"THE RANGE carries no banner at all:\n{section}"
    assert "STRUCTURAL AND PERMANENT" in section, (
        "\n\nTHE RANGE does not tell a cross-repo auditor that this checkout "
        "CANNOT be standing on the PR. Without that, the two shas it prints "
        f"read as a checkout that drifted.\n{section}"
    )
    assert "Resolve the range in a tree that CONTAINS" not in section, (
        "\n\nTHE RANGE tells a cross-repo auditor to resolve the range in a "
        "tree that contains the PR's head. This repository has no such tree "
        f"and cannot acquire one — the instruction is unfollowable.\n{section}"
    )
    # The tip is still the PR's real head sha: naming the impossibility must
    # not cost the auditor the one ref they CAN use in their own worktree.
    assert rendered_range(brief).partition("..")[2] == FAKE_OTHER_HEAD_OID, (
        "\n\nthe cross-repo range no longer ends at the PR's head sha, which "
        "is the only tip that resolves in the worktree WHERE TO WORK sent "
        "them to."
    )


def test_a_cross_repo_ledger_hands_the_attribution_gate_to_the_auditor():
    """🔴 REGRESSION. Red at `28492af2`, which printed COULD NOT MEASURE.

    `measure_ledger` refuses the moment the head check is not ok, and
    cross-repo that check cannot pass — so THE LEDGER printed its generic
    failure banner on EVERY delta round of such a PR, with advice ("Re-run the
    command by hand") that points at the wrong repository. That is the
    permanently-red gate `claude/RULES.md` names: a section that is red every
    single time trains the reader to skip it.

    🔴 AND WHAT GETS SKIPPED IS A GATE. The ladder ends on two consecutive
    rounds of ZERO payload lines. Nobody but the auditor can compute that
    number for a cross-repo PR, and at the base nothing told them so — the
    brief reported a failure and moved on, leaving the stop condition
    permanently unevaluated while reading as though the script had tried.
    """
    brief = brief_for_scenario("cross-repo-delta")
    led = ledger_section(brief)
    assert "NOT MEASURABLE FROM HERE" in led, (
        f"\n\nTHE LEDGER does not say the measurement is structurally "
        f"impossible from this repository:\n{led}"
    )
    assert "COULD NOT MEASURE" not in led, (
        "\n\nTHE LEDGER reports a cross-repo round as a command that failed. "
        "It did not fail; it was never runnable here, and it never will be — "
        f"phrasing it as a failure invites a re-run in the wrong repo.\n{led}"
    )
    assert "ATTRIBUTION GATE IS YOURS" in led, (
        "\n\nTHE LEDGER does not tell the auditor that the payload-attribution "
        "gate is theirs to compute this round. A gate nobody evaluates never "
        f"fires, and the ladder then runs forever on scaffolding.\n{led}"
    )
    # 🔴 THE HAND-OVER MUST BE RUNNABLE, and in the right tree. Asserted as a
    # whole command line rather than by its parts: three correct fragments in
    # an unrunnable order is exactly what a fragment pin cannot see.
    assert (
        "git -C <your worktree> log --numstat --format= --remerge-diff "
        f"aaaa1111..{FAKE_OTHER_HEAD_OID} --not origin/main"
    ) in led, (
        f"\n\nTHE LEDGER hands over no runnable command, or one whose range "
        f"is not this round's:\n{led}"
    )
    assert "<your worktree>" in led and "your clone of" in led.replace(
        "YOUR clone of", "your clone of"
    ), (
        "\n\nTHE LEDGER does not say WHERE the command must run. `origin/main` "
        "names this checkout's base branch here and the PR's base branch "
        f"there; the command is only correct in one of them.\n{led}"
    )


# 🔴 ROUND 10 — THE SENTENCE THE CROSS-REPO RANGE SAID UNCONDITIONALLY, kept
# here so a guard can forbid it in the one state where it is false.
TIP_IS_A_SHA_CLAIM = "So the range above names the PR's head SHA outright"

# The banner every site printing an unresolved tip must carry.
TIP_PLACEHOLDER_BANNER = "THE TIP OF THAT RANGE IS A PLACEHOLDER, NOT A SHA"

# 🔴 SPELLED HERE AND NOT READ FROM `ad.TIP_PLACEHOLDER`, deliberately. The
# red-at-base procedure copies THIS module into a tree holding the BASE script,
# and `TIP_PLACEHOLDER` does not exist there — reading it would make the guard
# below die of `AttributeError` for want of a name the fix introduced, which
# this module says everywhere is not evidence of a defect. The literal is what
# the base actually printed. `test_the_tip_placeholder_ledger_matches_the_script`
# is what keeps the two in step.
TIP_PLACEHOLDER_TEXT = "<the PR's head sha>"

# The `gh pr view … --json headRefOid` command the placeholder banner hands
# over. Matched up to the closing backtick that ends the inline code span, so
# a missing `--repo` is visible as an absence in the captured text.
_HEAD_OID_LOOKUP = re.compile(r"`(gh pr view [^`]*--json headRefOid[^`]*)`")


def test_a_placeholder_tip_is_never_handed_over_as_though_it_were_a_sha():
    """🔴 REGRESSION. Red at `706a6b38`, scenario `cross-repo-delta-no-head-sha`.

    Measured there, rc 0 and silent stderr:

        Diff **`aaaa1111..<the PR's head sha>`**
        …
            the PR's head sha is not known here (`gh pr view` was consulted and
            reported no `headRefOid` for this PR)
        So the range above names the PR's head SHA outright — read from
        `gh pr view --json headRefOid` …

    and THE LEDGER, under "THE ATTRIBUTION GATE IS YOURS THIS ROUND — it is not
    optional and nobody else can compute it":

        git -C <your worktree> log … aaaa1111..<the PR's head sha> --not
        origin/main

    Two sentences apart, the brief says it never learned `headRefOid` and that
    the range was read from `headRefOid`. `repo_relation == "cross"` was read as
    "the tip is a known sha" — the same weaker-fact-read-as-stronger shape as
    round 8's REFUSAL 1b, which refused a non-sha token in a rev spec at the
    ANCHOR end and left the TIP end alone.

    🔴 DRIVEN OVER EVERY SCENARIO, not over the two that are cross-repo. The
    placeholder comes from `range_tip`, which knows nothing about repos: the
    SAME-repo `delta-no-head-sha` scenario prints it from the could-not-verify
    branch. A fix scoped to the branch the finding was filed against would
    leave that one unguarded, which is this ladder's most-repeated defect.
    """
    seen = lookups = 0
    for scenario in SCENARIOS:
        brief = brief_for_scenario(scenario)
        if TIP_PLACEHOLDER_TEXT not in brief:
            continue
        seen += 1
        assert TIP_PLACEHOLDER_BANNER in brief, (
            f"\n\nscenario {scenario!r}: the brief spells "
            f"{TIP_PLACEHOLDER_TEXT!r} — inside a git rev spec, and inside the "
            "command THE LEDGER calls mandatory — and never says it is not a "
            "sha. A rev spec carrying it cannot run, and rc 0 with silent "
            "stderr is what the reader has to go on."
        )
        assert TIP_IS_A_SHA_CLAIM not in brief, (
            f"\n\nscenario {scenario!r}: the brief claims the range names the "
            "PR's head sha outright, in a run whose own reason line says it "
            "never learned that field. One of the two sentences is false."
        )
        # 🔴 ROUND 12 — AND THE COMMAND IT HANDS OVER MUST NAME A REPOSITORY.
        # `--repo` used to be dropped whenever the PR's repo was UNKNOWN,
        # handing over a bare `gh pr view 900 --json headRefOid` that resolves
        # against the auditor's CWD — in a brief that may have just told them
        # that is a DIFFERENT repository from the PR's. It returns a sha, at
        # rc 0, for another project's PR #900. Driven by
        # `claims-file-assumed-base`, whose payload carries no `url` and so
        # reaches `REPO_UNKNOWN`.
        for lookup in _HEAD_OID_LOOKUP.findall(brief):
            lookups += 1
            assert "--repo " in lookup, (
                f"\n\nscenario {scenario!r}: the brief tells the auditor to "
                f"resolve the tip with `{lookup}` — no `--repo`, so it "
                "resolves against whatever repository their shell is standing "
                "in and answers about a DIFFERENT PR of the same number, at "
                "rc 0. When this run never learned the PR's repository the "
                "honest hand-over is a `<...>` fill-in, which cannot run "
                "until the operator supplies it."
            )
    assert seen, (
        "no scenario renders an unresolved tip, so the loop above asserted "
        "nothing. `cross-repo-delta-no-head-sha` and `delta-no-head-sha` are "
        "what put the placeholder in a brief; if they were dropped or their "
        "`headRefOid` came back, this guard covers nothing."
    )
    # 🔴 POSITIVE CONTROL for the `--repo` arm: a regex that matches nothing
    # reports a clean zero indistinguishable from a brief that never hands the
    # command over. The number has to have moved.
    assert lookups, (
        f"{seen} scenario(s) render an unresolved tip and NONE of them spells "
        "a `gh pr view … --json headRefOid` command the regex can see, so the "
        "`--repo` assertion above ran zero times. Either the hand-over stopped "
        "being rendered or `_HEAD_OID_LOOKUP` no longer matches how it is "
        "spelled."
    )
    # 🔴 POSITIVE CONTROL for the OTHER side: a scenario with a real tip must
    # still get the confident sentence, or the fix above is "delete the claim"
    # rather than "make it conditional".
    assert TIP_IS_A_SHA_CLAIM in brief_for_scenario("cross-repo-delta"), (
        "the cross-repo range no longer tells an auditor with a KNOWN head sha "
        "that both ends of the range resolve in their worktree — the branch "
        "was made conditional and then never rendered its true case"
    )


ASSUMED_BASE_BANNER = "THAT BASE BRANCH WAS ASSUMED, NOT READ"


def scenario_base_is_assumed(name):
    """Does this scenario's fixture leave the base branch to the DEFAULT?

    🔴 ROUND 12 — TWO WAYS TO REACH THAT STATE, AND READING THE PR PAYLOAD SEES
    ONLY ONE. `gh` can report no `baseRefName`, or the run can consult no `gh`
    at all — and in the second case there IS no payload, so
    `kw.get("pr") or DEFAULT_PR` falls to a fixture that HAS the field and the
    guard would assert the banner's ABSENCE in the one mode that always
    assumes. That is the fixture half of the same defect: the script hardcoded
    the field, this module would have hardcoded the expectation, and the two
    wrongs agree.

    So the ARGV is consulted first: `--claims-file` decides it before any
    payload is read.
    """
    argv, kw = SCENARIO_RUNS[name]()
    if "--claims-file" in argv:
        return True
    payload = kw.get("pr") or DEFAULT_PR
    return not payload.get("baseRefName")


def test_no_brief_states_an_assumed_base_branch_as_a_fact():
    """🔴 REGRESSION. Red at `706a6b38`, scenario `delta-assumed-base`.

    🔴 THE SEVENTH INSTANCE, and it came from the sweep round 9's auditor asked
    for rather than from a finding. `base_ref` is `data.get("baseRefName") or
    "main"` — a DEFAULT — and every site printing it presented it as a
    measurement. Measured at the base with `baseRefName` absent, rc 0 and
    silent stderr, THE LEDGER's cross-repo hand-over read:

        Run this in the worktree WHERE TO WORK told you to make … where
        `origin/main` names THAT repository's base branch and not this
        checkout's

    an assertion about a repository nothing was ever asked about. `--claims-file`
    mode reaches it on EVERY run — it consults no `gh` and hardcodes
    `baseRefName: "main"`, exactly as it hardcodes no `headRefOid`, and that
    second omission already carried a reason string while this one did not.

    It matters because `--not <base>` is what decides which commits count as
    this round's payload, and the payload figure is the ladder's own stop
    condition: a wrong base moves the number silently, at rc 0.

    🔴 DRIVEN BOTH WAYS over every scenario, so the fix cannot be "print the
    banner always" — a warning that fires on every run is the permanently-red
    gate `claude/RULES.md` names.
    """
    seen_assumed = seen_read = 0
    for scenario in SCENARIOS:
        brief = brief_for_scenario(scenario)
        if not scenario_base_is_assumed(scenario):
            seen_read += 1
            assert ASSUMED_BASE_BANNER not in brief, (
                f"\n\nscenario {scenario!r}: the brief calls the base branch "
                "assumed, in a run whose fixture reports a real "
                "`baseRefName`. A banner that fires when the field WAS read "
                "is a banner every reader learns to skip."
            )
            continue
        seen_assumed += 1
        assert ASSUMED_BASE_BANNER in brief, (
            f"\n\nscenario {scenario!r}: nothing in this run learned the PR's "
            "`baseRefName` — `gh` reported none, or was never consulted — so "
            "`main` is this script's DEFAULT, and the brief states the result "
            "as a fact about the PR's repository. `--not <base>` decides "
            "which commits count as this round's payload."
        )
    # 🔴 ROUND 12 — THE ASSUMED SIDE IS COUNTED BY MECHANISM, NOT IN TOTAL. A
    # single `seen_assumed` counter is satisfied by either door, and the
    # `--claims-file` door is the one that was covered by nothing while a
    # comment in the script asserted it was reached on EVERY run.
    by_gh = [s for s in SCENARIOS
             if scenario_base_is_assumed(s) and "--claims-file"
             not in SCENARIO_RUNS[s]()[0]]
    by_claims_file = [s for s in SCENARIOS
                      if "--claims-file" in SCENARIO_RUNS[s]()[0]]
    assert seen_assumed and seen_read, (
        f"only one side was driven ({seen_assumed} assumed, {seen_read} read), "
        "so this guard pins a constant rather than a relationship"
    )
    assert by_gh and by_claims_file, (
        f"the assumed side is reached by {len(by_gh)} `gh`-payload "
        f"scenario(s) and {len(by_claims_file)} `--claims-file` scenario(s). "
        "Both doors must be driven: the script hardcoded `baseRefName` in the "
        "claims-file branch for two rounds, and a suite that only deletes the "
        "field from a `gh` payload cannot see that."
    )


def test_the_tip_placeholder_ledger_matches_the_script():
    """🔴 INVARIANT GUARD. Its evidence is mutant V21, not a base ref.

    The guard above spells the placeholder as a LITERAL so it can be red at a
    base tree that has no `TIP_PLACEHOLDER`. That literal is a second copy, and
    a second copy that nobody compares is how a guard quietly stops matching
    what ships. This is the comparison.
    """
    assert ad.TIP_PLACEHOLDER == TIP_PLACEHOLDER_TEXT, (
        f"\n\nthe script writes {ad.TIP_PLACEHOLDER!r} and this module looks "
        f"for {TIP_PLACEHOLDER_TEXT!r}. Every guard keyed on the literal is "
        "scanning for a string no brief contains."
    )
    assert TIP_PLACEHOLDER_BANNER in ad.unresolved_tip_note(
        ad.Facts(
            pr=900, repo="a/b", title="t", base_ref="origin/main", url="",
            round_no=3, cwd_repo_dir="/x", cwd_repo_slug="a/b",
            repo_relation="same", worktree=ad.gather_worktree_kind(
                lambda cmd, cwd=None: UNREADABLE_GIT_DIRS, "/x"),
            branch="b", dirty=0, prev_sha="aaaa1111", emit_from=None,
            claims=[], claims_round=None, checklist="", ledger=None,
            assembled_at="", claims_source="", head_check=None,
            base_assumed=False, base_assumed_reason=None,
            repo_unknown_reason=None,
        )
    ), (
        "the banner every keyed guard looks for is not what the note emits, so "
        "those guards can never fire"
    )


def test_a_cross_repo_ledger_prints_a_measurement_the_head_check_vouched_for():
    """🔴 REGRESSION. Red at `706a6b38`, scenario `cross-repo-renamed-remote-delta`.

    `render_range`'s cross-repo branch is ordered after the `hc.ok` branch on
    purpose, and its comment says why: `repo_relation` compares SLUGS, and a
    clone with a renamed remote or a mirror can name a different repository
    while HOLDING the PR's head commit. `render_ledger` did not adopt that
    ordering — it asked `repo_relation == "cross"` alone.

    Measured at the base in exactly that state (rc 0, silent stderr):

        head_check.ok = True
        facts.ledger  = 4 commits, 13 added, 2 deleted
        THE RANGE : "verified at assembly time to be PR #900's head commit"
        THE LEDGER: "NOT MEASURABLE FROM HERE … which holds neither end of the
                     range. A number measured here would be a measurement of
                     another project, so none is printed."

    The measurement SUCCEEDED and was discarded; every clause of that sentence
    is false; and the two sections contradict each other inside one document.
    Both sites now ask `cross_repo_holds_neither_end`, which consults the head
    check first — one rule, one place.
    """
    brief = brief_for_scenario("cross-repo-renamed-remote-delta")
    led = ledger_section(brief)
    assert "NOT MEASURABLE FROM HERE" not in led, (
        "\n\nTHE LEDGER calls the measurement structurally impossible in a run "
        "whose head check PASSED — the checkout holds the PR's head commit, "
        f"which is what THE RANGE two bars above says.\n{led}"
    )
    assert "13        2" in led, (
        "\n\nTHE LEDGER prints no numbers, so the successful measurement was "
        f"still thrown away:\n{led}"
    )
    assert (
        f"aaaa1111..{FAKE_OTHER_HEAD_OID} --not origin/main"
    ) in led, (
        f"\n\nTHE LEDGER's provenance line does not name the range it "
        f"measured:\n{led}"
    )
    # 🔴 THE OTHER SIDE OF THE PREDICATE, so this is not "delete the branch".
    # A cross-repo run whose head check FAILED must still hand the gate over.
    assert "NOT MEASURABLE FROM HERE" in ledger_section(
        brief_for_scenario("cross-repo-delta")
    ), (
        "the cross-repo hand-over stopped rendering for a checkout that really "
        "does hold neither end of the range — round 8's permanently-red ledger "
        "is back"
    )


# 🔴 ROUND 10 — A REFUSAL'S REMEDY IS A CLAIM, AND IT WAS FALSE AT BOTH SITES.
# `<...>` spans are the operator's fill-in-the-blank; a real sha goes in before
# the command can be run at all.
_PRESCRIBED_COMMAND = re.compile(r"`audit-dispatch\.py ([^`]+)`")
_FILL_IN = re.compile(r"<[^>]*>")
PRESCRIPTION_SHA = "beefcafe"

# The literal every prescription is spelled with. The usage block at the top of
# the script writes the same name WITHOUT a backtick, so this does not match it
# — which is correct: a usage line is not a remedy handed to an operator who
# just hit a refusal.
_PRESCRIPTION_LITERAL = "`audit-dispatch.py "


def prescription_sites(src=None):
    """-> {(first line, last line)} of every CALL that prescribes a re-run.

    🔴 ROUND 12 — READ OUT OF THE SCRIPT, NOT LISTED HERE. The guard below
    calls itself a CLASS guard ("a third refusal that prescribes a command it
    refuses fails here without anyone remembering to add a row") over two
    HARDCODED inputs. That sentence is a coverage claim, and it was true only
    by accident of there being exactly two sites today: an eighth refusal
    reached by a different input would be prescribed, unrun, and unreported,
    while the docstring went on promising otherwise. `claude/RULES.md` ->
    guards-narrower: make the implementation as wide as the sentence.

    🔴 ROUND 13 — AND ROUND 12's OWN IMPLEMENTATION WAS NARROWER THAN ITS OWN
    SENTENCE, in exactly the shape it had just named. It matched `ast.Call`
    whose func is the NAME `print`, so two real spellings SURVIVED a fully
    green 109-test suite when measured: a prescription written through
    `err_stream.write(...)` (the func is an Attribute, not a Name), and
    `print(_CONST, file=...)` where the literal lives in a module constant so
    the CALL's own source segment does not contain it. Coverage was complete
    by accident — both shipping sites are bare `print()` — which is the worst
    version of this defect, because the docstring reads as a guarantee.
    Neither shape is exotic: the script already writes to `err_stream` by name
    all over `main`, and it already hoists shared prose into constants
    (`TIP_PLACEHOLDER`, `REPO_UNKNOWN`).

    So the scan is over EVERY call, by two routes: the literal appearing in
    the call's own source, or the call referencing a name bound to a string
    that carries it. `src` is injectable so the controls below can feed it
    both survivors — a scanner asserted to be wide, and never fed a case it
    must catch, is the "reassuring zero" this module refuses everywhere else.
    """
    src = SCRIPT.read_text(encoding="utf-8") if src is None else src
    tree = ast.parse(src)

    carriers = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if _PRESCRIPTION_LITERAL in (
                ast.get_source_segment(src, node.value) or ""):
            carriers |= {t.id for t in node.targets
                         if isinstance(t, ast.Name)}

    out = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        carried = carriers & {
            n.id for n in ast.walk(node) if isinstance(n, ast.Name)
        }
        if carried or _PRESCRIPTION_LITERAL in (
                ast.get_source_segment(src, node) or ""):
            out.add((node.lineno, node.end_lineno))
    return out


# 🔴 THE TWO SPELLINGS ROUND 12's SCANNER COULD NOT SEE, measured SURVIVING a
# green 109-test suite at `6349a8b9`. They are the controls for the widened
# one: each MUST be found, or the docstring above is a coverage claim over an
# implementation that does not make it.
PRESCRIPTION_SURVIVORS = {
    "written through `err_stream.write`, not `print`": (
        'def f(err_stream):\n'
        '    err_stream.write(\n'
        '        "Fix: run `audit-dispatch.py 900 --round 1 --emit-claims`."\n'
        '    )\n'
    ),
    "the literal hoisted into a module constant": (
        'REMEDY = "Fix: run `audit-dispatch.py 900 --round 1 --emit-claims`."\n'
        'def f(err_stream):\n'
        '    print(REMEDY, file=err_stream)\n'
    ),
}


def run_main_traced(argv, **kw):
    """`run_main`, plus the set of `audit-dispatch.py` LINES the run executed.

    🔴 ATTRIBUTION, not textual matching. The two refusals render prescriptions
    that are BYTE-IDENTICAL under this module's fixtures (`--round 3` over a
    `round=2` block makes both say `--round 2 --emit-claims …`), so a scan of
    stderr cannot tell which site produced one — and a coverage claim built on
    that would report both sites reached when only one ever ran. A line trace
    can, because the two `print()` calls occupy disjoint line spans.

    The previous trace function is saved and restored: a coverage runner or a
    debugger installs its own, and clobbering it would break the tool rather
    than this test.
    """
    out, err = io.StringIO(), io.StringIO()
    runner = kw.pop("runner", None) or make_runner(**kw)
    executed = set()

    def trace_lines(frame, event, _arg):
        if event == "line":
            executed.add(frame.f_lineno)
        return trace_lines

    def trace_calls(frame, event, _arg):
        return trace_lines if frame.f_code.co_filename == str(SCRIPT) else None

    previous = sys.gettrace()
    sys.settrace(trace_calls)
    try:
        rc = ad.main(argv, runner=runner, stdout=out, stderr=err,
                     checklist_reader=fake_checklist)
    finally:
        sys.settrace(previous)
    return rc, out.getvalue(), err.getvalue(), executed


def prescribed_commands(err):
    """Every `audit-dispatch.py …` command a refusal PRINTED, as argv lists."""
    return [
        _FILL_IN.sub(PRESCRIPTION_SHA, m.group(1)).split()
        for m in _PRESCRIBED_COMMAND.finditer(err)
    ]


def test_every_command_a_refusal_prescribes_actually_runs():
    """🔴 REGRESSION. Red at `706a6b38`, for BOTH refusals.

    Measured there, over a `round=2` block whose header is `audited=..`:

        900 --round 3                             -> rc 2, refusal
        900 --round 2 --emit-claims --audited abc12345
                                                  -> rc 2, the SAME refusal,
                                                     stdout EMPTY

    and with no block at all:

        900 --round 3                             -> rc 2, refusal
        900 --round 2 --emit-claims               -> rc 2, stdout EMPTY

    Both refusals hand the operator a `--emit-claims` re-run as their
    mechanical remedy and then refuse it, because the refusal is ordered before
    the emit half and re-reads the same unreadable comment. The operator sees
    the same text twice and reads it as their own typo. (REFUSAL 1's remedy is
    sound at round 2 — `--round 1 --emit-claims` needs no block — and
    unrunnable from round 3 up, which is why "it usually works" was true and
    useless.)

    🔴 THE GUARD IS THE CLASS, NOT THE TWO SITES. It reads the commands out of
    whatever the refusal printed and RUNS them; a third refusal that prescribes
    a command it refuses fails here without anyone remembering to add a row.

    🔴 ROUND 12 MADE THAT SENTENCE TRUE. It was a claim about PRESCRIPTIONS
    made by a loop over two HARDCODED inputs, so it covered a third refusal
    only if that refusal happened to be reached by one of those two — and an
    eighth refusal reached by a different input would have been prescribed,
    never run, and never reported, under a docstring promising otherwise. The
    prescription SITES are now read out of the script (`prescription_sites`)
    and the cases must reach every one of them, attributed by LINE TRACE
    because the two sites' rendered text is byte-identical under these
    fixtures. Coverage is complete today; what changed is that it stays
    complete or this goes red.
    """
    bad_block = (
        "```audit-claims round=2 audited=..\n"
        "1. a claim that parses fine\n"
        "```"
    )
    cases = {
        "REFUSAL 1b (anchorless block)": {"comments": [bad_block]},
        "REFUSAL 1 (no block at all)": {"comments": []},
    }
    sites = prescription_sites()
    # POSITIVE CONTROL for the scanner: an empty site set makes the coverage
    # assertion at the foot vacuously true, which is the reassuring zero
    # `claude/RULES.md` says to feed a case that must move.
    assert sites, (
        "no call in the script prescribes an `audit-dispatch.py` re-run at "
        f"all — {_PRESCRIPTION_LITERAL!r} matched nothing, so the coverage "
        "assertion below would pass over an empty set. Either the remedies "
        "moved or the literal changed."
    )
    # 🔴 ROUND 13 — AND THE SCANNER'S WIDTH IS MEASURED, NOT ASSERTED. Both
    # spellings below SURVIVED this guard at `6349a8b9` while it matched only
    # a bare `print()`. Feeding them here is what makes `prescription_sites`'s
    # docstring a claim the implementation actually keeps: `claude/RULES.md`
    # -> guards-narrower, applied to the guard that names that rule.
    for shape, source in PRESCRIPTION_SURVIVORS.items():
        assert prescription_sites(source), (
            f"POSITIVE CONTROL: the scanner cannot see a prescription {shape}. "
            "It reports a clean sweep of the sites it happens to recognise, "
            "which is not the class its docstring names."
        )
    reached = set()
    for name, kw in cases.items():
        rc, _out, err, executed = run_main_traced(["900", "--round", "3"], **kw)
        reached |= {s for s in sites if any(s[0] <= n <= s[1] for n in executed)}
        assert rc == 2, f"{name}: expected the refusal, got rc {rc}"
        commands = prescribed_commands(err)
        # POSITIVE CONTROL for the extractor: a refusal that prescribes nothing
        # the regex can see would make every assertion below vacuous.
        assert commands, (
            f"\n\n{name}: no `audit-dispatch.py …` command was found in the "
            f"refusal, so this guard checked nothing:\n{err}"
        )
        for argv in commands:
            rc2, out2, err2 = run_main(argv, **kw)
            assert "```audit-claims" in out2, (
                f"\n\n{name}: the remedy it prints does not produce the block "
                f"it promises.\n  ran    : audit-dispatch.py {' '.join(argv)}\n"
                f"  rc     : {rc2}\n  stdout : {out2[:300]!r}\n"
                f"  stderr : {err2[:600]!r}\n"
                "A refusal that prescribes a command it refuses reads to the "
                "operator as their own error."
            )
    unreached = sites - reached
    assert not unreached, (
        f"\n\n{len(unreached)} of the script's {len(sites)} prescription "
        f"site(s) were never executed by any case here — lines {sorted(unreached)}.\n"
        "  Each prints an `audit-dispatch.py …` remedy to an operator who has "
        "just been refused, and nothing in this module has ever RUN what they "
        "print. Add a case whose input reaches that refusal; the two here are "
        "an anchorless `audited=` header and no block at all.\n"
        "  This assertion is what makes this guard's docstring true: without "
        "it the loop above covers whichever refusals these two inputs happen "
        "to reach, and reads as covering all of them."
    )


def test_control_the_prescription_extractor_can_see_a_broken_remedy():
    """NEGATIVE CONTROL for the guard above: it must be able to go red.

    Built from a REAL refusal's text with its remedy rewritten to the command
    that genuinely fails — the delta round itself. If this passes, the guard
    above is a scan wired to nothing.
    """
    bad_block = (
        "```audit-claims round=2 audited=..\n1. a claim\n```"
    )
    _rc, _out, err = run_main(["900", "--round", "3"], comments=[bad_block])
    broken = err.replace(
        "audit-dispatch.py 900 --round 2 --emit-claims "
        "--audited <the tip that round read>",
        "audit-dispatch.py 900 --round 3",
    )
    commands = prescribed_commands(broken)
    assert ["900", "--round", "3"] in commands, (
        f"the extractor cannot see the rewritten remedy: {commands}"
    )
    rc2, out2, _err2 = run_main(["900", "--round", "3"], comments=[bad_block])
    assert rc2 == 2 and "```audit-claims" not in out2, (
        "the control command does not actually fail, so it proves nothing "
        "about the guard's ability to report one that does"
    )


def scenario_checkout_stands_on_the_pr(name):
    """Does this scenario's FIXTURE put the assembly checkout on the PR's head?

    Answered from the scenario's own kwargs through `fixture_resolved_head` —
    the one place that models `git rev-parse HEAD` — so the guard below and the
    fake cannot drift apart.
    """
    _argv, kw = SCENARIO_RUNS[name]()
    payload = dict(kw.get("pr") or DEFAULT_PR)
    resolved = fixture_resolved_head(
        payload, kw.get("origin", FAKE_ORIGIN), kw.get("local_head")
    )
    oid = payload.get("headRefOid")
    return bool(resolved and oid and ad.same_commit(resolved, oid))


def test_no_brief_claims_a_verification_its_own_fixture_refutes():
    """🔴 THE SEAM GUARD over the brief's claim and the fixture's own model.

    🔴 ROUND 12 CORRECTED THIS HEADING. It used to read "and it is also this
    module's fixture guard", which is a coverage claim this test does not
    support: the assertion and the fake BOTH resolve the checkout's HEAD
    through `fixture_resolved_head`, so a defect IN that function moves both
    sides together and this guard cannot see it. Measured — regressing
    `fixture_resolved_head` to round 8's behaviour (default to the PR's own
    `headRefOid` regardless of repo) leaves THIS TEST GREEN — 4 failed, 105
    passed, and this is not one of the four. Coverage is not lost; it lives in
    those four:

        test_a_cross_repo_range_states_the_impossibility_not_a_moved_checkout
        test_a_cross_repo_ledger_hands_the_attribution_gate_to_the_auditor
        test_a_cross_repo_ledger_prints_a_measurement_the_head_check_vouched_for
        test_a_placeholder_tip_is_never_handed_over_as_though_it_were_a_sha

    `claude/RULES.md` -> guards-narrower: do not leave a description wider
    than the code.

    What it DOES pin is the relationship below — the brief's "verified at
    assembly time" sentence against the scenario's modelled `rev-parse HEAD`,
    in any scenario this module knows or later adds.

    🔴 INVARIANT GUARD; its evidence is mutant V9, not a base ref. At
    `28492af2` the SCRIPT would pass this — the sentence it forbids appears
    only when the head check succeeded, and the base's cross-repo scenarios all
    ran at round 1 where THE RANGE does not exist. What was wrong at the base
    was the FIXTURE: `OTHER_REPO_PR` inherited `headRefOid` from `DEFAULT_PR`
    and `make_runner` defaults the checkout's HEAD to it, so the first
    cross-repo delta render printed "verified at assembly time to be PR #900's
    head commit" for a PR in another repository. A test written with that
    fixture would have passed in a physically impossible state.

    🔴 ROUND 10 RESCOPED IT, BECAUSE IT WAS PINNED TO THE WRONG SIDE. Round 8
    spelled the relationship as "no CROSS-REPO brief may claim verification" —
    while `render_range`'s own comment three bars away says that state IS real
    and is why its cross-repo branch is ordered after the head check: a clone
    with a renamed remote, or a mirror, names a different repository and holds
    the PR's head commit anyway. The guard passed only because no scenario
    modelled it; adding `cross-repo-renamed-remote-delta` makes it fail on
    CORRECT output. Measured — that is the first thing the new scenario did.

    The fact the brief's sentence actually claims is `head_check.ok`, so what
    this pins is the sentence against the FIXTURE'S OWN modelled `rev-parse
    HEAD` — in any scenario this module knows or later adds, cross-repo or not.
    A repo relation is not a head check, which is the whole of round 10's
    finding B.
    """
    claimed, cross = [], 0
    for scenario in SCENARIOS:
        brief = brief_for_scenario(scenario)
        cross += "## WHERE TO WORK — 🔴 CROSS-REPO" in brief
        if "verified at assembly time" not in brief:
            continue
        claimed.append(scenario)
        kw = SCENARIO_RUNS[scenario]()[1]
        payload = dict(kw.get("pr") or DEFAULT_PR)
        modelled = fixture_resolved_head(
            payload, kw.get("origin", FAKE_ORIGIN), kw.get("local_head")
        )
        assert scenario_checkout_stands_on_the_pr(scenario), (
            f"\n\nscenario {scenario!r}: the brief says this checkout was "
            "verified to be standing on the PR's head, and this scenario's "
            "own fixture refutes it —\n"
            f"  fixture `git rev-parse HEAD` : {modelled!r}\n"
            f"  fixture PR headRefOid        : {payload.get('headRefOid')!r}\n"
            "One of the two is false, and a test written in that state would "
            "pass in a physically impossible world."
        )
    # 🔴 TWO POSITIVE CONTROLS, because two different zeroes would read as a
    # pass. No scenario claiming verification means the loop asserted nothing;
    # no scenario rendering CROSS-REPO means the configuration this guard was
    # born in has stopped being covered.
    assert claimed, (
        "no scenario renders the 'verified at assembly time' sentence at all, "
        "so the loop above asserted nothing. Either the sentence moved or "
        "every delta scenario stopped passing the head check."
    )
    assert cross, (
        "no scenario renders a CROSS-REPO brief, so this guard no longer "
        "covers the seam it was written for. Either the heading moved or the "
        "cross-repo scenarios were dropped from SCENARIO_RUNS."
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


# 🔴 ROUND 10 — THE ROUND-8 GRANT, verbatim, kept ONLY as this probe's positive
# control. Same role as `ROUND_4_WRITE_GRANT` and `ROUND_5_NO_WRITE_SCOPE`
# above: its job is to be RED AT THE BASE, not to be unwalkable —
# non-walkability comes from the whole-string `DIRECTIVE_LEDGER` pin.
ROUND_8_CLONE_GRANT = (
    "That worktree is YOURS, and so is the clone you made it from: "
    "fetching and checking out inside either is fine. The no-write rule "
    "below is about every checkout you did not make."
)

CLONE_WRITE_GRANT_PHRASES = (
    "the clone you made it from: fetching",
    "inside either is fine",
    "checking out inside either",
)

# 🔴 ROUND 12 — ROUND 10's OWN GRANT, verbatim, kept as the positive control
# for the ENUMERATION probe below. Same role as `ROUND_8_CLONE_GRANT`: it must
# be RED, so a clean result over the shipping grant means something.
ROUND_10_CLONE_GRANT = (
    "That worktree is YOURS: fetching and checking out inside it is fine. "
    "In the clone you made it from, `git worktree add` is the ONLY write "
    "this brief asks for — do not `fetch`, `pull` or `checkout` there, "
    "whoever made it, because other sessions may be standing in it. The "
    "no-write rule below is about every checkout you did not make."
)

# A CLOSED LIST of refused verbs — "do not `a`, `b` or `c` there". The shape
# that leaves `remote update`, `switch`, `restore`, `reset`, `branch -f` and
# `gc` permitted by omission.
#
# 🔴 ROUND 13 — THIS PROBE IS ITSELF A SPELLED GUARD, and it was measured
# walkable. Re-spelling round 10's enumeration as "never `fetch`, `pull` or
# `checkout` in that clone" leaves it — and the whole 109-test suite — green:
# the enumeration is back, permitting `remote update` and `switch` by omission
# again, and nothing sees it. It is KEPT, because a second check that fails
# differently is worth its cost, but it is no longer the thing the guard
# relies on: `git_verbs_named_in` below asserts the STATE (which git verbs the
# grant names) instead of a phrase, and no rewording moves that.
_ENUMERATED_CLONE_REFUSAL = re.compile(
    r"do not (?:`[a-z][a-z-]*`(?:, )?)+ ?or `[a-z][a-z-]*` there"
)

# 🔴 ROUND 13's OWN RE-SPELLING, verbatim, as the second positive control for
# the structural probe. Measured GREEN against the shipping suite at
# `6349a8b9` while re-introducing the enumeration round 12 removed.
ROUND_13_RESPELLED_ENUMERATION = (
    "That worktree is YOURS: fetching and checking out inside it is fine. "
    "In the clone you made it from, `git worktree add` is the ONLY write "
    "this brief asks for and the ONLY one you may make — never `fetch`, "
    "`pull` or `checkout` in that clone, whoever made it, because other "
    "sessions may be standing in it. The no-write rule below is about every "
    "checkout you did not make."
)

# The universal that replaces it: the permitted writes stated positively, and
# everything else refused whether or not anyone thought to name it.
CLONE_GRANT_CATCH_ALL = (
    "the ONLY ones you may make — every other command that writes there is "
    "refused, named or not"
)

# The recipe's own `git -C <clone> …` lines, so what it runs there is READ and
# not remembered. `_CLONE_RECIPE` keeps the verb-only reading the grant/recipe
# relationship is built on; `_CLONE_RECIPE_CMD` keeps the whole command, which
# is what the ref-provenance probe needs.
_CLONE_RECIPE = re.compile(r"git -C <your local clone of [^>]*> ([a-z-]+)")
_CLONE_RECIPE_CMD = re.compile(r"^git -C <your local clone of [^>]*> (.+)$", re.M)

# 🔴 ROUND 13 — ROUND 12's RECIPE LINE, verbatim, as the positive control for
# the ref-provenance probe. It is the line that CANNOT BE RUN: `worktree add`
# resolves `<the PR's head branch>` against refs the clone already has, and
# nothing in the recipe puts it there. Measured on scratch repos with git
# 2.55.0 — `fatal: invalid reference: pr-head`, rc 128, both for a clone that
# has not fetched since the PR opened and (after a full fetch) for a FORK PR,
# whose branch is never in `origin`.
ROUND_12_CLONE_RECIPE = (
    "git -C <your local clone of someone-else/otherproj> worktree add "
    "/tmp/audit-pr900-r1 <the PR's head branch>"
)


def clone_write_grants_in(text):
    return [p for p in CLONE_WRITE_GRANT_PHRASES if p in text]


def git_verbs_named_in(text):
    """Every git SUBCOMMAND a backticked span in `text` names.

    🔴 ROUND 13 — THE STATE, NOT A PHRASE. The hazard the enumeration probe
    means to catch is "this grant names git verbs the recipe does not run",
    and that is a fact about the SET of verbs, not about the words "do not" or
    "never". Reading the set makes every re-spelling of a closed list fail
    identically, which is the `claude/RULES.md` spelled-guards remedy.

    A token counts when it is the first word of a backticked span (after an
    optional leading `git`) and is not path-shaped — so `refs/audit/` and
    `refs/pull/<n>/head` are excluded by the slash and no vocabulary of known
    verbs is needed, which is what would re-introduce a closed list inside the
    guard itself.
    """
    out = set()
    for span in re.findall(r"`([^`]+)`", text):
        toks = span.split()
        if toks and toks[0] == "git":
            toks = toks[1:]
        if not toks:
            continue
        head = toks[0]
        if "/" in head or not re.fullmatch(r"[a-z][a-z-]*", head):
            continue
        out.add(head)
    return out


def clone_recipe_commands(text):
    """The `git -C <clone> …` commands the recipe runs, whole, in order."""
    return [m.group(1) for m in _CLONE_RECIPE_CMD.finditer(text)]


def _worktree_add_committish(cmd):
    """-> what a `worktree add` line asks git to RESOLVE, or None."""
    parts = cmd.split()
    if parts[:2] != ["worktree", "add"]:
        return None
    rest = [p for p in parts[2:] if not p.startswith("--")]
    return " ".join(rest[1:]) or None


def unrunnable_recipe_steps(cmds):
    """-> the reasons this recipe cannot be run as written. Empty is the pass.

    🔴 THE RELATIONSHIP, not a spelling: every ref a `worktree add` resolves in
    the clone must be one an EARLIER line of the same recipe created there.
    Round 12's recipe named the PR's head BRANCH, which is a ref the clone only
    has if it has fetched — and fetching there is the write the grant refuses,
    so the recipe and the rule could not both be obeyed. A fork PR's branch is
    not in that clone's `origin` at any point, so no permitted fetch would help
    either.
    """
    created, problems = set(), []
    for cmd in cmds:
        parts = cmd.split()
        if parts[:1] == ["fetch"]:
            for p in parts[1:]:
                if ":" in p:
                    src, dst = p.split(":", 1)
                    created.add(dst)
                    if not src.startswith("refs/pull/"):
                        problems.append(
                            f"the fetch reads {src!r}, which is not the "
                            "`refs/pull/<n>/head` GitHub publishes on the BASE "
                            "repository — a fork PR's head is reachable by no "
                            "other ref in that clone's `origin`"
                        )
        tip = _worktree_add_committish(cmd)
        if tip is None:
            continue
        if tip not in created:
            problems.append(
                f"`worktree add` resolves {tip!r}, which no earlier line of "
                "this recipe put in the clone. git answers `fatal: invalid "
                "reference`, rc 128, unless the clone happens to have fetched "
                "it already — and putting it there is a write the grant "
                "refuses"
            )
        if "--detach" not in cmd.split():
            problems.append(
                "`worktree add` is not `--detach`ed, so it creates "
                "`refs/heads/<branch>` in the SHARED clone and fails rc 128 "
                "when another worktree already holds that branch"
            )
    return problems


def test_the_clone_grant_covers_only_the_write_the_recipe_makes():
    """🔴 REGRESSION. Red at `706a6b38`, scenario `cross-repo`.

    Round 8 widened `own-worktree-is-writable` from "fetching and checking out
    inside IT" to "inside EITHER" — the worktree AND the clone — to legitimise
    the `worktree add` its own recipe performs. But the recipe writes to that
    clone exactly ONCE, and the grant authorised two more operations with
    cross-session blast radius, over a tree the brief names only as `<your
    local clone of owner/name>`. On this host that placeholder resolves in
    practice to `~/workspace/<repo>`: a long-lived checkout other sessions are
    working in, and a `fetch` or `checkout` there is precisely the write the
    `no-fetch` clause exists to prevent.

    "The clone YOU made" arguably excludes those trees — but nothing in the
    brief disambiguated it and the placeholder invites the substitution, so the
    grant is stated as the OPERATION it covers.

    🔴 THE RELATIONSHIP, not the wording: the verb is read off the recipe LINE,
    so a recipe that starts running something else in the clone fails here
    rather than silently acquiring permission for it.

    🔴 ROUND 12 — AND THE REFUSAL MUST BE A UNIVERSAL, NOT A LIST. Round 10's
    fix refused `fetch`, `pull` and `checkout` BY NAME, which leaves everything
    unnamed permitted by omission: `git -C <clone> remote update` is what an
    auditor reaches for once `fetch` is refused and they still need the PR
    branch present before `worktree add`, and `switch`, `restore`, `reset`,
    `branch -f` and `gc` are the same hole. This directive is the ONLY rule
    covering that tree — the `no-fetch` clause is scoped to every checkout you
    did NOT make, and round 7 chose that scope precisely so the recipe's
    `worktree add` stops being forbidden — and it is the only finding in this
    ladder whose consequence is a cross-session write into `~/workspace/<repo>`
    while other sessions are working there. So the shipping grant must carry no
    closed verb list AND must state the catch-all.
    """
    assert clone_write_grants_in(ROUND_8_CLONE_GRANT), (
        "POSITIVE CONTROL: the probe cannot see round 8's own grant, so a "
        "clean result below would say nothing at all"
    )
    brief = brief_for_scenario("cross-repo")
    verbs = _CLONE_RECIPE.findall(brief)
    assert verbs, (
        "\n\nWHERE TO WORK's recipe no longer runs any `git -C <clone> …` "
        "command, so the grant below is scoped to nothing and every check "
        "under it is vacuous."
    )
    granted = clone_write_grants_in(brief)
    assert not granted, (
        f"\n\nthe brief grants the auditor blanket `fetch`/`checkout` inside "
        f"the clone — {granted}. Its writes there are {sorted(set(verbs))}, "
        "each one narrowly scoped; a blanket grant is a cross-session write "
        "into a tree the brief calls `<your local clone of owner/name>`, "
        "which on this host is a shared, long-lived checkout.\n"
        + ad.DIRECTIVE["own-worktree-is-writable"]
    )
    grant = ad.DIRECTIVE["own-worktree-is-writable"]
    assert "the ONLY writes this brief asks for" in grant, (
        "\n\nthe grant no longer says which operations it covers in the "
        "clone, so the next reader has only 'you made it' to reason from — "
        f"which is what round 8 reasoned from.\n{grant}"
    )
    # 🔴 ROUND 13 — THE RELATIONSHIP, READ BOTH WAYS. The grant must name the
    # verbs the recipe runs and NO OTHERS. Naming fewer leaves a write the
    # brief itself asks for unpermitted (which is finding F2: `worktree add`
    # over a ref only a forbidden `fetch` could supply). Naming more is the
    # closed-verb-list hazard in its structural spelling — round 10's
    # `fetch`/`pull`/`checkout` and round 13's re-wording of it both land here,
    # because both name verbs the recipe does not run.
    #
    # Positive controls FIRST, and there are two, because the regex probe
    # below could see round 10's phrasing and could NOT see round 13's.
    for label, control in (
        ("round 10's enumeration", ROUND_10_CLONE_GRANT),
        ("round 13's re-spelling of it", ROUND_13_RESPELLED_ENUMERATION),
    ):
        assert git_verbs_named_in(control) != set(verbs), (
            f"POSITIVE CONTROL: the verb-set probe reads {label} as agreeing "
            "with the recipe, so a clean result below would say nothing at all"
        )
    assert git_verbs_named_in(grant) == set(verbs), (
        "\n\nthe grant names git verbs the recipe does not run, or omits ones "
        f"it does: grant {sorted(git_verbs_named_in(grant))} vs recipe "
        f"{sorted(set(verbs))}.\n"
        "  MORE than the recipe: a closed list of refused verbs, whatever "
        "words wrap it — everything unnamed is permitted by omission, and "
        "this directive is the ONLY rule covering that tree.\n"
        "  FEWER: the brief asks for a write it does not permit, which is a "
        "recipe the auditor can obey or run but not both.\n"
        f"{grant}"
    )
    # 🔴 ROUND 12 — A CATCH-ALL, NOT A LONGER VERB LIST. Positive control
    # first: the probe must be able to SEE round 10's enumeration, or a clean
    # result over the shipping grant is a scan wired to nothing.
    assert _ENUMERATED_CLONE_REFUSAL.search(ROUND_10_CLONE_GRANT), (
        "POSITIVE CONTROL: the enumeration probe cannot see round 10's own "
        "`do not `fetch`, `pull` or `checkout` there`, so a clean result "
        "below would say nothing at all"
    )
    assert not _ENUMERATED_CLONE_REFUSAL.search(grant), (
        "\n\nthe clone grant refuses a CLOSED LIST of verbs. Everything not "
        "on it is permitted by omission — `remote update` is what an auditor "
        "reaches for when `fetch` is refused by name and they still need the "
        "PR branch present before `worktree add`, and `switch`, `restore`, "
        "`reset`, `branch -f` and `gc` are the same hole. This is the only "
        "rule covering that tree: the `no-fetch` clause is scoped to every "
        f"checkout you did NOT make, and the clone is one you did.\n{grant}"
    )
    assert CLONE_GRANT_CATCH_ALL in grant, (
        "\n\nthe grant no longer refuses everything other than the writes it "
        "permits. A guard on words is walkable by rewording; the refusal "
        f"here has to be a universal.\n{grant}"
    )


def test_the_cross_repo_recipe_can_actually_be_run():
    """🔴 REGRESSION. Red at `6349a8b9`, scenario `cross-repo`.

    Round 12's recipe was one line:

        git -C <clone> worktree add /tmp/audit-pr900-r1 <the PR's head branch>

    and `worktree add` resolves that branch against refs the clone ALREADY
    has. Measured on scratch repos, git 2.55.0:

      * clone has not fetched since the PR opened (the normal case)
            -> `fatal: invalid reference: pr-head`, rc 128
      * FORK PR, after a full `git fetch origin` (the CERTAIN case — that
        branch is never in the base repo's `origin`)
            -> `fatal: invalid reference: fork-pr-head`, rc 128

    Putting the ref there is a `fetch` in the clone, which
    `own-worktree-is-writable` refuses. The one workaround that wording
    permitted — `worktree add --detach`, then fetch INSIDE the worktree — is
    not a workaround at all: a linked worktree shares the clone's ref store
    and object store, so the fetch wrote `refs/remotes/fork/pr-head` into the
    CLONE, added a remote to the CLONE's config, and `git -C <clone> cat-file
    -e <sha>` returned 0. The auditor could either not run the recipe or
    comply with one sentence while violating another — on the ONE tree in this
    brief whose failure lands outside it, a cross-session write into
    `~/workspace/<repo>` while other sessions are working there.

    Same class as `test_every_command_a_refusal_prescribes_actually_runs` one
    section over: a prescription that cannot be run.

    🔴 WHAT THIS DOES NOT DO is spawn git — the module is hermetic and says so
    (blind spot 3 in its docstring). The rc-128 measurements above were made
    by hand on real repos and are recorded in the fix commit; what this pins
    is the RELATIONSHIP that made them possible, which is machine-readable:
    a ref `worktree add` resolves must be one the recipe itself created.
    """
    # 🔴 POSITIVE CONTROL. The probe must be able to SEE round 12's line, or a
    # clean result below is a scan wired to nothing.
    control = unrunnable_recipe_steps(
        clone_recipe_commands(ROUND_12_CLONE_RECIPE)
    )
    assert any("invalid reference" in p for p in control), (
        "POSITIVE CONTROL: the probe does not flag round 12's own recipe, "
        f"which is the line measured at rc 128: {control}"
    )

    brief = brief_for_scenario("cross-repo")
    cmds = clone_recipe_commands(brief)
    assert cmds, (
        "\n\nWHERE TO WORK's cross-repo recipe runs no `git -C <clone> …` "
        "command at all, so this guard is asserting over nothing"
    )
    assert any(c.split()[:2] == ["worktree", "add"] for c in cmds), (
        f"\n\nthe recipe no longer creates a worktree: {cmds}. The section's "
        "whole job is to hand a cross-repo auditor a tree to work in."
    )
    problems = unrunnable_recipe_steps(cmds)
    assert not problems, (
        "\n\nWHERE TO WORK's recipe cannot be run as written:\n  - "
        + "\n  - ".join(problems)
        + "\n\nThat section is the only place a cross-repo auditor is told "
        "where to work, and the tree it names is a long-lived checkout other "
        "sessions are standing in. A recipe that fails rc 128 sends them "
        "looking for a verb that gets the ref there — which is exactly the "
        "cross-session write `own-worktree-is-writable` exists to refuse.\n"
        + "\n".join(cmds)
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

# 🔴 ROUND 8 — THE ONE FORM OF WORDS BOTH SIDES MUST USE FOR THE SAME SET.
# Round 6 found a forward reference scoped to a STATE the clause had stopped
# naming; round 7 fixed it by rewriting the reference to "every checkout you
# did not make" while the clause said "any checkout that is not the copy YOU
# made FOR THIS AUDIT". Still two sets — and the difference is not academic: it
# lands exactly on the clone WHERE TO WORK tells a cross-repo auditor to run
# `git worktree add` in, which they made but did not make for this audit.
# Forbidden under the clause, permitted under the reference, prescribed by the
# brief.
#
# The guard beside it therefore pins a PHRASE both sides must spell, not a
# vocabulary either may avoid: a scope stated in two forms of words is two
# scopes, whatever they were meant to mean. `claude/RULES.md` -> spelled-guards:
# when the artifact under test is prose, pin the string.
NO_WRITE_SCOPE = "checkout you did not make"

# Round 7's clause scope, kept ONLY as the control's payload — the narrower of
# the two sets, and the one that forbade the brief's own recipe.
ROUND_7_CLAUSE_SCOPE = (
    "**Do NOT `git fetch`, `pull`, `checkout` or otherwise write to any "
    "checkout that is not the copy YOU made for this audit.**"
)


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


def no_write_scope_disagreements(brief, clause_text):
    """-> [(which side, sentence)] for each side that does not spell the scope.

    🔴 THE RELATIONSHIP, not either component: the `no-fetch` clause and every
    sentence that forward-references it must name ONE set, in ONE form of
    words. Round 6's guard beside this asks a narrower question — does a
    forward reference name a checkout STATE this brief denies — and a scope
    can disagree without naming any state at all, which is precisely how round
    7's fix passed it while narrowing the clause on a different axis.
    """
    out = []
    if NO_WRITE_SCOPE not in norm(clause_text):
        out.append(("the `no-fetch` clause", norm(clause_text)))
    for s in no_write_forward_references(brief):
        if NO_WRITE_SCOPE not in norm(s):
            out.append(("a forward reference", norm(s)))
    return out


def test_the_no_write_forward_reference_states_the_clauses_own_scope():
    """🔴 REGRESSION. Red at `28492af2`, on the CLAUSE side.

    Measured there: the clause read "any checkout that is not the copy YOU
    made FOR THIS AUDIT" and the sentence forward-referencing it read "every
    checkout you did not make". Two sets, and the difference lands on exactly
    one tree — the clone WHERE TO WORK tells a cross-repo auditor to write
    into, three lines earlier: ``git -C <your local clone of owner/name>
    worktree add …``. That clone is one they made; it is not one they made for
    this audit. Forbidden by the clause, permitted by the reference,
    prescribed by the brief.

    🔴 AND THE COMMENT ABOVE THE REFERENCE ASSERTED THEY AGREED — "the scope is
    stated the way the clause states it" — which is a claim about the clause
    and was false. A comment is a claim too, and this one would have stopped
    the next reader checking.

    Round 6 fixed a forward reference that narrowed the rule it named; round 7
    replaced that narrowing with another on a different axis. So the guard is
    on the SET both sides must spell, not on the vocabulary either may avoid.
    """
    clause = {c.id: c.text for c in ad.INVARIANT_CLAUSES}["no-fetch"]

    # 🔴 TWO POSITIVE CONTROLS, ONE PER SIDE. A predicate that can only see one
    # of the two is half a guard, and which half is invisible is not something
    # a clean result can tell you.
    assert no_write_scope_disagreements(brief_for_scenario("cross-repo"),
                                        ROUND_7_CLAUSE_SCOPE), (
        "POSITIVE CONTROL (clause side): the scan cannot see round 7's own "
        "clause wording, so a clean result below says nothing about the clause"
    )
    ref_control = (
        "## THE CHECKOUT — state at assembly time\n\n"
        "    kind   : SHARED — other sessions and agents are in this tree\n\n"
        f"{ROUND_5_NO_WRITE_SCOPE}\n\n## NEXT\n"
    )
    assert no_write_scope_disagreements(ref_control, clause), (
        "POSITIVE CONTROL (reference side): the scan cannot see round 5's own "
        "sentence, so a clean result below says nothing about the reference"
    )

    seen = 0
    for scenario in SCENARIOS:
        brief = brief_for_scenario(scenario)
        seen += len(no_write_forward_references(brief))
        bad = no_write_scope_disagreements(brief, clause)
        assert not bad, (
            f"\n\nscenario {scenario!r}: the no-write rule's scope is stated "
            f"in more than one form of words, so it names more than one set. "
            f"Every side must spell {NO_WRITE_SCOPE!r}:\n"
            + "".join(f"  {where}: {text!r}\n" for where, text in bad)
            + "  The set that matters is the auditor's OWN checkouts, and the "
            "brief's own cross-repo recipe writes to one of them — a clone "
            "they made, and did not make for this audit."
        )
    assert seen, (
        "no scenario renders a forward reference to the no-write rule at all, "
        "so only the clause half of the loop above asserted anything."
    )


def toolchain_section(brief):
    """The TOOLCHAIN block, sliced off the two headings that bracket it."""
    start = brief.index("## TOOLCHAIN")
    return brief[start:brief.index(EXPECTED_INVARIANTS_HEADING, start)]


def toolchain_commands(section):
    """The non-blank lines inside TOOLCHAIN's fences — what it PRESCRIBES.

    🔴 The distinction every guard over this section needs, and the one a
    substring scan cannot make. The prose must be free to NAME a command in
    order to say it is ABSENT — "`nix build …#checks…` would fail with an
    attribute error", "look for `scripts/gate.sh`" — so a scan of the whole
    section forbids the very sentences that close the fabricated-command
    finding. Only a fenced line is offered as runnable.
    """
    out, inside = [], False
    for line in section.splitlines():
        if line.startswith("```"):
            inside = not inside
            continue
        if inside and line.strip():
            out.append(line)
    return out


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
    including the cross-repo pair, and requires the rendered REASON to be
    BYTE-IDENTICAL across all of them: that is the claim the section's own
    docstring makes ("this section knows nothing about where the auditor
    stands"), stated as wide as the code it describes.

    🔴 ROUND 14 NARROWED THE SLICE FROM THE SECTION TO THE REASON, AND WIDENED
    THE PIN FROM AN EQUALITY TO AN IDENTITY. Deliberate; here is all of it.

    The COMMANDS under the reason are now derived from the repository the PR
    lives in (`detect_repo_toolchain`), because hardcoding devrc's layout
    prescribed three non-existent commands and one false `.envrc` claim against
    `homelab-infra`. Target-repo content and audit scenario are DIFFERENT AXES:
    the `cross-repo` scenarios hold no checkout of the PR's repository to read,
    so their commands bar honestly says nothing was probed, while a same-repo
    one lists real commands. A whole-section equality cannot express that, and
    the only two ways to keep it are to blind the generator (prescribe devrc's
    layout at every target — the defect) or to probe the WRONG repository
    cross-repo (the defect, with a new source). It genuinely could not coexist,
    so the split is stated rather than smuggled.

    🔴 ROUND 14 CLAIMED "THE PROPERTY IT PROTECTED IS NOT WEAKENED — it became
    STRUCTURAL", AND THAT WAS FALSE. It was measured false at `5bad0a0c`: the
    reason bar did become a constant, but the pin was lifted off the COMMANDS
    bar entirely, and a state-dependent sentence three lines lower — one that
    varies with the auditor's own worktree kind and contradicts WHERE TO WORK —
    walked the whole module at 115 passed. See `TOOLCHAIN_TARGET_OF`, which
    carries the mutation and the measurement. A claim that a relaxation is
    lossless is the kind of claim that stops the next reader checking, so it is
    corrected here rather than softened.

    What is true: the reason lives in `TOOLCHAIN_HEAD`, a module-level CONSTANT
    taking no argument, so a state-dependent RATIONALE is unwritable without
    deleting the constant. What is now also true, and is the round-15 fix: the
    COMMANDS bar is pinned byte-identical WITHIN each target
    (`TOOLCHAIN_TARGET_OF`), so the only axis it may vary on is the one the
    probe actually reads. This guard still drives every scenario, still runs
    the three phrase assertions over the FULL section, still requires the
    reason to BE the constant, and still fails if every scenario renders the
    same commands bar (the shape this fix would take if the detection were
    inert).
    """
    sections, reasons, briefs = {}, {}, {}
    for scenario in SCENARIOS:
        briefs[scenario] = brief_for_scenario(scenario)
        tool = toolchain_section(briefs[scenario])
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
        # The reason is everything before the COMMANDS heading. Sliced on that
        # heading, which is itself a constant, so no scenario can move the
        # boundary out from under this guard.
        assert ad.TOOLCHAIN_COMMANDS_HEADING in tool, (
            f"scenario {scenario!r}: the commands heading is gone, so this "
            "guard can no longer tell the reason from the prescription and "
            "would pass over a rationale that had become state-dependent"
        )
        reasons[scenario] = tool.split(ad.TOOLCHAIN_COMMANDS_HEADING)[0]

    ref_name = SCENARIOS[0]
    for scenario, reason in reasons.items():
        assert reason == reasons[ref_name], (
            f"\n\nTOOLCHAIN's REASON differs between {ref_name!r} and "
            f"{scenario!r}. It knows nothing about where the auditor stands or "
            "which repo the PR is in, so a scenario-dependent sentence here is "
            "a claim it cannot support in the branch it is not being read "
            f"in.\n--- {ref_name} ---\n{reasons[ref_name]}\n"
            f"--- {scenario} ---\n{reason}"
        )
        # 🔴 IDENTITY, not merely equality. Equality across scenarios is silent
        # about WHAT they all equal: re-deriving the reason from `facts` would
        # satisfy it under this module's fixtures, where `cwd_repo_dir` holds
        # one value — which is how round 6 was walked one level up.
        assert reason.strip() == ad.TOOLCHAIN_HEAD.strip(), (
            f"\n\nscenario {scenario!r}: the reason bar is no longer "
            "`TOOLCHAIN_HEAD` rendered verbatim. That constant takes no "
            "argument — that is what makes state-independence structural — so "
            "re-deriving or reformatting it here reopens round 5's finding.\n"
            f"--- rendered ---\n{reason}\n--- constant ---\n{ad.TOOLCHAIN_HEAD}"
        )
        assert ad.TOOLCHAIN_TAIL in sections[scenario], (
            f"scenario {scenario!r}: the state-independent TAIL — name the "
            "tier and the base sha, and the `git --version` note — is not "
            "rendered verbatim"
        )
    assert len(sections) == len(SCENARIOS) >= 5, (
        f"only {len(sections)} scenario(s) were driven; an equality over a "
        "handful of them pins independence of the axes they happen to vary "
        "and nothing else — which is exactly how round 6 walked this guard"
    )
    # 🔴 THE PIN THAT KEEPS THE SPLIT HONEST, AND IT ASKS ABOUT PRESCRIPTIONS
    # RATHER THAN TEXT. The first spelling compared the commands bars for
    # inequality — and MEASURED (mutant V40) it did not fire when the WRONG
    # repository was probed cross-repo, because that bar names `facts.repo`,
    # which differs between the scenarios anyway. A text difference is not the
    # property; the property is that a scenario holding no checkout of the PR's
    # repository offers NOTHING to run, and one holding a checkout offers
    # something. Both states must be present, or this whole module is grading
    # one branch.
    prescribing = {s: toolchain_commands(t) for s, t in sections.items()}
    assert any(c for c in prescribing.values()), (
        "no scenario prescribed a single runnable command — the probe answers "
        "'absent' everywhere, so every assertion about what it emits is "
        "vacuous. Check `FAKE_REPO_DIR` is still a real directory on disk."
    )
    silent = [s for s, c in prescribing.items() if not c]
    assert silent, (
        "\n\nEVERY scenario prescribed commands, including the cross-repo ones "
        "that hold no checkout of the repository the PR lives in. Either the "
        "probe is reading the WRONG repository there — a confident, "
        "fully-formed, entirely irrelevant command list, which is the original "
        "defect with a new source — or the detection is inert and one "
        "repository's layout is being prescribed at every target.\n"
        f"  scenarios: {sorted(prescribing)}"
    )

    # 🔴 ROUND 15 — THE COMMANDS BAR, PINNED WITHIN EACH TARGET. Everything
    # above this point grades the REASON; the finding is that the bar BELOW the
    # reason lost its pin altogether when round 14 split the section, and that
    # a sentence keyed on `facts.worktree` walks straight through the gap.
    assert set(TOOLCHAIN_TARGET_OF) == set(SCENARIOS), (
        "\n\nTOOLCHAIN_TARGET_OF and SCENARIOS disagree. A scenario with no "
        "declared target is one this pin silently skips, which is how the "
        f"whole guard gets emptied.\n  only in the table: "
        f"{sorted(set(TOOLCHAIN_TARGET_OF) - set(SCENARIOS))}\n"
        f"  only in SCENARIOS: {sorted(set(SCENARIOS) - set(TOOLCHAIN_TARGET_OF))}"
    )
    assert set(TOOLCHAIN_TARGET_OF.values()) <= TOOLCHAIN_TARGET_KEYS, (
        "a scenario declares a target outside the closed enumeration. Adding a "
        "key is how a state-dependent sentence gets accommodated instead of "
        "fixed — the new key would put the offending scenario in a partition "
        "of its own, where nothing compares it to anything: "
        f"{sorted(set(TOOLCHAIN_TARGET_OF.values()) - TOOLCHAIN_TARGET_KEYS)}"
    )
    by_target = {}
    for scenario in SCENARIOS:
        by_target.setdefault(TOOLCHAIN_TARGET_OF[scenario], []).append(scenario)
    for key, members in sorted(by_target.items()):
        assert len(members) >= 2, (
            f"target {key!r} is declared by ONE scenario ({members}), so "
            "within-target equality asserts nothing there. A partition of one "
            "is the shape this pin degrades into."
        )
        ref = members[0]
        for scenario in members[1:]:
            got = sections[scenario].split(ad.TOOLCHAIN_COMMANDS_HEADING, 1)[1]
            want = sections[ref].split(ad.TOOLCHAIN_COMMANDS_HEADING, 1)[1]
            assert got == want, (
                f"\n\nTOOLCHAIN's COMMANDS bar differs between {ref!r} and "
                f"{scenario!r}, which probe the SAME target ({key}). Nothing "
                "on the auditor's side — round, head sha, base ref, dirty "
                "count, or the kind of checkout this brief was assembled in — "
                "is read by the probe, so a difference here is a claim the "
                "section cannot support in the state it is not being read in. "
                "That is round 5's finding, one bar lower.\n"
                f"--- {ref} ---\n{want}\n--- {scenario} ---\n{got}"
            )
    # 🔴 NON-DEGENERACY, and it is what makes the equality above a MEASUREMENT.
    # A partition whose members all share one checkout state cannot see a
    # sentence keyed on that state — the equality would hold vacuously there.
    #
    # 🔴 ROUND 16 — `any(len(k) >= 2)` CERTIFIED ONE PARTITION AND CALLED IT
    # THE PIN. Measured at `ba321c06`: `probed-devrc-shape` spanned all three
    # states and satisfied it single-handed, while `cross-repo-otherproj` had
    # {private, shared} and `repo-unknown` had {shared} ALONE — so the
    # not-probed branch was blind to `unknown` in both of its partitions.
    # Inserting into `_toolchain_not_probed` a WHERE-TO-WORK-contradicting
    # sentence keyed on `facts.worktree.kind == "unknown"` left the module at
    # **122 passed**; the same insertion keyed on `"private"` was killed. That is
    # round 15's F1 in its third state, one `any` away.
    #
    # So the requirement is the WHOLE GRID: every target spans every checkout
    # state. `any` -> `all` alone would still let a partition sit at two of
    # three; naming the state set makes the missing cell a named hole rather
    # than an unnoticed one. `CHECKOUT_KIND_MARKERS` is the enumeration, so
    # adding a fourth checkout state fails HERE, loudly, instead of quietly
    # halving what this pin can see.
    kinds = {key: {checkout_kind_of(briefs[s]) for s in members}
             for key, members in by_target.items()}
    every_state = set(CHECKOUT_KIND_MARKERS)
    for key, seen in sorted(kinds.items()):
        assert seen == every_state, (
            f"\n\ntarget {key!r} spans only {sorted(seen)} of the "
            f"{sorted(every_state)} checkout states, so the within-target "
            "equality above is BLIND to a sentence keyed on "
            f"{sorted(every_state - seen)} in this branch — which is exactly "
            "the mutation this guard exists for, and it survived a fully green "
            "suite once already.\n  scenarios in this target: "
            f"{sorted(by_target[key])}\n  full grid: {kinds}"
        )


def test_the_toolchain_prescribes_only_commands_it_probed(tmp_path):
    """🔴 REGRESSION. Red at `9e23c379` — #1104 MERGED, and still red there.

    Every TOOLCHAIN command interpolated `facts.cwd_repo_dir` into devrc's OWN
    layout, so a brief for a PR in any other repository prescribed scripts that
    do not exist there. Measured 2026-08-29 against `ZacxDev/homelab-infra`
    (PR #530) from a real checkout of it:

        scripts/gate.sh                 ABSENT
        flake `checks` outputs          NONE, at HEAD and in the working tree
        .envrc                          `use flake` + `use opencode`
        its real runner                 scripts/tests/run-ci-suite.sh
        nix develop <root> -c python3 -m pytest
                                        ModuleNotFoundError: No module named
                                        'pytest'

    Four prescriptions, none runnable — and the last is the worst, because the
    bar under it told the auditor that `No module named pytest` means the WRONG
    SHELL and not a broken suite, so the brief manufactured a failure and told
    the reader to disregard the one true signal it produced. An auditor
    following it hits four missing things and can report the gate broken
    against a PR that is fine.

    The fixture is that repo's SHAPE, not that repo — its `.envrc`, its runner,
    and a `flake.nix` whose devShell embeds a python heredoc containing
    `checks = pr.get("statusCheckRollup", [])`. That line is verbatim from the
    real file and is the reason the probe matches a flake OUTPUT rather than
    the word: the first regex here answered "this flake declares checks" for a
    flake that declares none, which would have prescribed the `nix build` line
    anyway and left the fallback unreachable.
    """
    root = _write_repo(tmp_path / "homelab-talos",
                       envrc="use flake\nuse opencode\n",
                       ci_suite=True, flake=HOMELAB_FLAKE)
    rc, out, err = run_main(["900"], toplevel=root)
    assert rc == 0, err
    tool = toolchain_section(out)
    # 🔴 THE COMMANDS, NOT THE PROSE AROUND THEM — the same distinction
    # `test_the_toolchain_gates_the_auditors_copy_not_the_shared_checkout`
    # draws, and for the same reason. The prose must be free to NAME an absent
    # command in order to say it is absent ("`nix build …#checks…` would fail
    # with an attribute error"); a scan that cannot tell a prescription from
    # its rationale goes red on the sentence that closes the finding.
    cmds = toolchain_commands(tool)
    assert cmds, "no command block at all in a section that probed a real repo"

    for line in cmds:
        assert "gate.sh" not in line, (
            f"\n\nthe brief prescribes `gate.sh` for a repository with no "
            f"`scripts/gate.sh`:\n    {line}\n{tool}"
        )
        assert "nix build" not in line, (
            f"\n\na `nix build …#checks…` is prescribed against a flake that "
            f"declares no `checks` output; it exits on an attribute error, "
            f"which reads exactly like a broken gate:\n    {line}\n{tool}"
        )
        assert "nix develop" not in line, (
            f"\n\na `nix develop` wrapper is prescribed around this "
            f"repository's commands, but its flake names no pytest — that is "
            f"the wrapper measured to exit `No module named 'pytest'`:\n"
            f"    {line}\n{tool}"
        )
    assert "This repo's `.envrc` is `use opencode`" not in tool, (
        f"\n\nthe brief asserts this repo's `.envrc` is `use opencode`. It is "
        f"`use flake` + `use opencode`, and that string was remembered from "
        f"devrc rather than read:\n{tool}"
    )
    assert "`use flake` + `use opencode`" in tool, (
        f"\n\nthe `.envrc` this brief names is not the one on disk:\n{tool}"
    )
    # The runner that DOES exist is named, and the absent tier is called
    # absent rather than passed over in silence — an auditor who reads no
    # sandbox line at all goes looking for one.
    assert "bash <your worktree>/scripts/tests/run-ci-suite.sh" in tool, (
        f"\n\nthe repository's real runner is not prescribed:\n{tool}"
    )
    assert "no `checks` output" in tool, (
        f"\n\nthe brief is silent about the missing sandbox tier; silence "
        f"sends the auditor hunting for one:\n{tool}"
    )
    # 🔴 And the bare subset command, NOT one wrapped in a shell measured to
    # have no pytest in it.
    assert "python3 -m pytest <paths> -q -p no:cacheprovider" in cmds

    # 🔴 THE OTHER `.envrc` BRANCH, AND A FIXTURE THAT CAN SEE THE MUTANT.
    # The wrong-shell diagnosis has two spellings — one for a repo whose flake
    # DOES carry pytest, one for a repo that does not — and only the second is
    # exercised above. MEASURED: with the module's devrc-shaped fixture alone,
    # hardcoding the sentence back to `use opencode` (mutant V39) SURVIVED a
    # fully green suite, because that fixture's `.envrc` IS `use opencode` and
    # a value that can only ever equal the constant cannot see a mutant that
    # returns the constant. So this one says something else, deliberately.
    other = _write_repo(tmp_path / "devrc-shaped", envrc="use flake\n",
                        gate=DEVRC_GATE, flake=DEVRC_FLAKE)
    rc, out, err = run_main(["900"], toplevel=other)
    assert rc == 0, err
    tool = toolchain_section(out)
    assert "This repo's `.envrc` is `use flake`," in tool, (
        f"\n\nthe `.envrc` named in the wrong-shell diagnosis is not the one "
        f"on disk — it says `use flake` and nothing else:\n{tool}"
    )
    assert "`use opencode`" not in tool, (
        f"\n\nthe brief names `use opencode` for a repository whose `.envrc` "
        f"does not contain it. That string is devrc's, remembered rather than "
        f"read, and it is the claim an auditor cannot check:\n{tool}"
    )
    # …and this shape DOES get the shell wrapper, because its flake carries
    # pytest. The negative control for the branch above: same code, opposite
    # answer, so "no `nix develop`" is a measurement and not a constant.
    assert any(f"nix develop {other} -c python3 -m pytest" in line
               for line in toolchain_commands(tool)), (
        f"\n\nthis repository's flake DOES carry pytest, so the subset command "
        f"must be wrapped in its dev shell. Without this the 'no `nix develop`' "
        f"assertion above is a constant rather than a "
        f"measurement:\n{toolchain_commands(tool)}"
    )


def test_the_toolchain_prescribes_nothing_when_it_cannot_probe():
    """🔴 Cross-repo: no checkout of the PR's repo, so NO command is invented.

    The counterpart to the guard above, and the one that pins the design's
    whole point: a fabricated command is strictly worse than an absent one.
    An absent one costs the auditor a lookup; a fabricated one costs a round
    and can be written up as a finding against a PR that is fine.

    The trap this closes is subtle — the cwd IS a probeable repository here
    (devrc's shape, `FAKE_REPO_DIR`), it is simply the WRONG one. Probing it
    would produce a confident, fully-formed, entirely irrelevant command list,
    which is the original defect with a new source rather than a fix.
    """
    tool = toolchain_section(brief_for_scenario("cross-repo"))
    assert "NOTHING WAS PROBED HERE" in tool, (
        f"\n\ncross-repo, and the brief still prescribes commands:\n{tool}"
    )
    # 🔴 NO FENCED BLOCK AT ALL. The prose deliberately still NAMES
    # `scripts/gate.sh` and `scripts/tests/run-ci-suite.sh` — as places to go
    # LOOK — so a substring scan over the whole section would forbid the
    # hand-over this branch exists to give. What must be empty is the set of
    # things presented as runnable.
    assert toolchain_commands(tool) == [], (
        f"\n\ncross-repo, the brief still hands over runnable commands — read "
        f"out of `{FAKE_REPO_DIR}`, a DIFFERENT repository from the one the PR "
        f"lives in:\n{toolchain_commands(tool)}\n{tool}"
    )
    assert "NAME the one you used" in tool, (
        "the hand-over does not ask the auditor to name the runner they "
        "found, so the report cannot be checked by anyone"
    )


def test_the_flake_checks_probe_reads_an_output_and_not_the_word():
    """🔴 The three answers `_flake_check_names` must keep distinct.

    `None` (no `checks` output), `[]` (declared but unreadable) and a name list
    are three different sentences, and collapsing any two either prescribes a
    command for a repo without it or suppresses one for a repo with it.

    The `homelab-infra` row is the negative control that a loose probe fails,
    and the `devrc` row is the positive control that an indentation-only scan
    fails: a build script's lines start at column 0, so bounding the block by
    indentation stopped at the first of them and returned `['pytests']` alone.
    """
    assert ad._flake_check_names(HOMELAB_FLAKE) is None, (
        "`checks = pr.get(...)` inside a devShell heredoc was read as a flake "
        "`checks` OUTPUT — the repo declares none"
    )
    assert ad._flake_check_names(DEVRC_FLAKE) == ["pytests", "nodetests"], (
        "the two real check names were not both read out of a devrc-shaped "
        "flake; a scan that stops at the first low-indent line inside a "
        "`''…''` build script returns ['pytests']"
    )
    assert ad._flake_check_names("") is None
    assert ad._flake_check_names(None) is None
    assert ad._flake_check_names("checks.${system} = {\n") == [], (
        "a truncated/unreadable checks block must answer [] — 'declared but "
        "I could not read the names' — and not None, which claims the "
        "repository HAS no sandbox tier"
    )


# 🔴 ROUND 15 — THE SHAPES THE PROBE ANSWERED WRONG, IN BOTH DIRECTIONS. Every
# one measured at `5bad0a0c` through the real function; the comment beside each
# row is what it answered THERE.
CHECKS_SHAPES_DECLARED = {
    # `None` at `5bad0a0c` — "this repository has no sandbox tier", in bold, for
    # five flakes that all declare one. `None` is the CONFIDENT answer and it
    # routed to it, rather than to the hedged `[]` the function already had.
    "genAttrs": """{
  outputs = { self, nixpkgs }: {
    checks = nixpkgs.lib.genAttrs [ "x86_64-linux" ] (system: {
      unit = 1;
      lint = 2;
    });
  };
}
""",
    "flake-parts perSystem checks.default": """{
  outputs = inputs: flake-parts.lib.mkFlake { } {
    perSystem = { pkgs, ... }: {
      checks.default = pkgs.runCommand "x" { } "true";
    };
  };
}
""",
    "checks.<system>.default": """{
  outputs = { self }: {
    checks.x86_64-linux.default = derivation { };
  };
}
""",
    "checks.<system> = base // {…}": """{
  outputs = { self }: {
    checks.x86_64-linux = base // {
      unit = 1;
    };
  };
}
""",
    "checks = eachSystem (…)": """{
  outputs = { self }: {
    checks = eachSystem (system: {
      unit = 1;
    });
  };
}
""",
}

# The mirror: text that LOOKS like a `checks` declaration and is not code. Both
# answered a fabricated tier at `5bad0a0c` — the heredoc `['unit']`, the comment
# `['unit']` — and the brief fenced `nix build …#checks.x86_64-linux.unit` for a
# flake declaring nothing of the sort.
CHECKS_SHAPES_NOT_CODE = {
    "a devShell heredoc holding `checks = {`": """{
  outputs = { self }: {
    devShells.default = pkgs.mkShell {
      shellHook = ''
python3 - <<'PY'
checks = {
unit = 1;
}
PY
      '';
    };
  };
}
""",
    "a `/* … */`-commented checks block": """{
  outputs = { self }: {
    /*
    checks.x86_64-linux = {
      unit = 1;
    };
    */
    packages.default = 1;
  };
}
""",
    "a `#`-commented checks block": """{
  outputs = { self }: {
#   checks.x86_64-linux = {
#     unit = 1;
#   };
    packages.default = 1;
  };
}
""",
}

# 🔴 ROUND 16 — THE `#` ROW ABOVE IS INERT AGAINST THE STRIPPER, SAID OUT LOUD
# RATHER THAN LEFT TO READ AS COVERAGE. `CHECKS_ATTR`/`CHECKS_DECL` are anchored
# `^[ \t]*checks`, and a `#` line comment puts a `#` before the word on every
# line it comments out — so that row answers `None` at every revision this
# module has ever had, stripper or no stripper. It is kept because it pins the
# ANCHOR (a future `re.search` without `^` would fabricate a tier from it), and
# `test_the_comment_stripper_is_reachable_in_its_own_right` below asserts the
# comment text really is blanked, which is the claim the row's NAME makes and
# the row itself cannot support. The load-bearing `#` case is the FALSE-ABSENT
# one directly below: a comment whose text would otherwise open a string.
#
# `# … the ''-string idiom …` is an ordinary sentence to write in a flake, and
# an unstripped `''` in it opens an indented string that never closes: the real
# `checks` block after it is blanked and the answer is `None`, in bold.
CHECKS_COMMENTED_APOSTROPHES = """{
  outputs = { self }: {
    # we do not use the ''-string idiom here, and "unbalanced quotes happen
    checks.x86_64-linux = {
      unit = 1;
      lint = 2;
    };
  };
}
"""

# 🔴 ROUND 16 — A LEGAL NIX IDENTIFIER THAT ENDS IN APOSTROPHES. `'` is an
# identifier character (this module's own `_NIX_NAME` allows it), so `foo'' = 1;`
# is ONE token. Measured at `ba321c06`: read as a string opener it blanked the
# REST OF THE FILE and the answer was `None` — "**no `checks` output**" for a
# flake whose next binding is the checks block.
CHECKS_IDENT_WITH_APOSTROPHES = """{
  outputs = { self }: rec {
    foo'' = 1;
    checks.x86_64-linux = {
      unit = 1;
      lint = 2;
    };
  };
}
"""


# 🔴 ROUND 16 — THE SHAPE THE FLAT FIXTURES CANNOT REACH, AND IT IS THE SAME
# CLASS ONE LEVEL DOWN. `shellHook = '' ${lib.optionalString c '' … ''} '';` is
# an ordinary nixpkgs idiom, and a scanner that walks to "the next `''`" reads
# the `''` that OPENS the nested string as the OUTER one's terminator. Two
# consequences, both measured at `ba321c06`, both CONFIDENT answers:
#
#   * the interpolation's body is not blanked, it is PROMOTED TO CODE — a flake
#     declaring no `checks` at all answered `['unit']`, i.e. the brief fenced
#     `nix build …#checks.x86_64-linux.unit` for a target that has none;
#   * when that promoted region holds an odd token (an `''${` escape, a lone
#     `"`), string parity inverts for the rest of the file and a flake that DOES
#     declare `checks.x86_64-linux = { unit; lint; }` answered `None`.
#
# So the fixtures are a PRODUCT, not a row: every inner body × both outer
# states. A single nested fixture would have covered whichever direction its own
# body happened to produce and left the other open — which is how the flat F7
# fixture left this whole class open in the first place.
def _nested_interp_flake(body, declares):
    """A flake whose devShell interpolates a NESTED `''…''`, ± a checks output."""
    return (
        "{\n"
        "  outputs = { self }: {\n"
        "    devShells.x86_64-linux.default = pkgs.mkShell {\n"
        "      shellHook = ''\n"
        "        ${lib.optionalString cond ''\n"
        + body
        + "        ''}\n"
        "        echo hi\n"
        "      '';\n"
        "    };\n"
        + ("    checks.x86_64-linux = {\n"
           "      unit = 1;\n"
           "      lint = 2;\n"
           "    };\n" if declares else "")
        + "  };\n"
        "}\n"
    )


NESTED_INNER_BODIES = {
    "plain": "          echo nested\n",
    "an ''${…} escape": "          export P=''${HOME}/bin\n",
    "a lone double quote": "          echo \"unbalanced\n",
    "a `#` character": "          echo '# not a comment'\n",
    "text that LOOKS like a checks binding": (
        "          checks = {\n"
        "          unit = 1;\n"
        "          }\n"
    ),
    "a nested interpolation of its own": (
        "          echo ${toString ${n}}\n"
    ),
}

# devrc's real `checks.${system}` block with nix's two-apostrophe ESCAPES in the
# build script — `''${VAR}` is a literal `${VAR}`, `'''` is a literal `''`.
# Neither closes the string, and a scanner that toggles on any `''` thinks both
# do.
#
# 🔴 ONE ESCAPE PER FIXTURE, AND THE FIRST DRAFT PUT BOTH IN ONE. Measured: with
# `echo ''${HOME}` AND `echo '''` in the same build script, the escapes-are-
# terminators mutant (V43) SURVIVED — two spurious toggles cancel, the string
# parity is restored by the second one, and the answer comes back correct for
# entirely the wrong reason. A fixture whose two defects annihilate is the
# `claude/RULES.md` "fixture landing exactly on its own boundary" shape, and it
# scored a real hazard as covered. Each constant now carries exactly one.
_ESCAPE_TAIL = "rc=$?\npython3 -m pytest .\n"
DEVRC_FLAKE_WITH_DOLLAR_ESCAPE = DEVRC_FLAKE.replace(
    "python3 -m pytest .\n", "echo ''${HOME}\n" + _ESCAPE_TAIL,
)
DEVRC_FLAKE_WITH_QUOTE_ESCAPE = DEVRC_FLAKE.replace(
    "python3 -m pytest .\n", "echo '''\n" + _ESCAPE_TAIL,
)


def test_the_flake_checks_probe_reads_nix_code_and_not_text_that_looks_like_it():
    """🔴 REGRESSION. Red at `5bad0a0c` on eight of its ten rows.

    One root cause, three failure modes, and the fix is one pass: strip nix's
    strings and comments BEFORE any pattern runs, then widen the shapes.

    **The `''` escapes.** The scanner toggled "in string" on any `''`, and nix
    spells three things with two apostrophes. Measured on devrc's OWN
    `flake.nix` with a single `echo ''${HOME}` added inside the `pytests` build
    script: `['pytests', 'nodetests']` became **`['pytests', 'rc', 'rc']`** —
    two fabricated `nix build` targets named after a shell variable, and
    `nodetests`, a check the merge really gates on, dropped in silence. A
    non-empty list is indistinguishable from a complete one, so the `[]` valve
    never fires.

    **False-PRESENT.** The pattern ran over RAW text, so a `checks = {` inside
    a devShell heredoc or inside a comment answered a fabricated tier — the
    same defect the strict-regex fix closed for the `checks = pr.get(...)`
    spelling and left for every other one.

    **False-ABSENT.** The pattern accepted three shapes, one of them naming
    devrc's own `forAllSystems` helper by hardcoding it, and answered `None`
    for `genAttrs`, for flake-parts' `perSystem`, for `checks.<system>.default`
    and for `checks.<system> = base // {…}`. `None` makes the brief state in
    BOLD that the repository has no sandbox tier — the tier the same section
    calls the one the merge gates on. It routed to the strongest wrong answer
    rather than to the hedged middle it already had.
    """
    for name, text in CHECKS_SHAPES_DECLARED.items():
        got = ad._flake_check_names(text)
        assert got is not None, (
            f"\n\nshape {name!r} declares a `checks` output and the probe "
            "answered None — 'this repository has no sandbox tier', which the "
            "brief states in bold. The hedged `[]` already exists for a shape "
            "whose names cannot be read; None is for a flake that declares "
            "nothing."
        )
    # …and where the names ARE readable they are read, so "not None" above is
    # not satisfied by a probe that answers `[]` to everything.
    assert ad._flake_check_names(CHECKS_SHAPES_DECLARED["genAttrs"]) == [
        "unit", "lint"
    ]
    assert ad._flake_check_names(
        CHECKS_SHAPES_DECLARED["checks.<system> = base // {…}"]) == ["unit"]
    assert ad._flake_check_names(
        CHECKS_SHAPES_DECLARED["checks = eachSystem (…)"]) == ["unit"]

    for name, text in CHECKS_SHAPES_NOT_CODE.items():
        assert ad._flake_check_names(text) is None, (
            f"\n\n{name} was read as a flake `checks` OUTPUT. It is not code, "
            "and the brief fences a `nix build …#checks…` for whatever names "
            "come out of it — a command that exits on an attribute error and "
            "reads exactly like a broken gate."
        )

    for name, text in (("''${…}", DEVRC_FLAKE_WITH_DOLLAR_ESCAPE),
                       ("'''", DEVRC_FLAKE_WITH_QUOTE_ESCAPE)):
        assert ad._flake_check_names(text) == ["pytests", "nodetests"], (
            f"\n\n`{name}` is an ESCAPE inside a nix indented string, not a "
            "terminator. Read as a terminator, the rest of the build script "
            "is scanned as code: the measured answer on devrc's own flake was "
            "['pytests', 'rc', 'rc'] — invented targets named after a shell "
            "variable, and `nodetests`, a real merge-gating check, dropped."
        )
    # 🔴 THE POSITIVE CONTROL FOR THE STRIPPER ITSELF. A reassuring "no
    # fabricated name" is indistinguishable from a stripper that blanks the
    # whole file, which would make every row above pass for the wrong reason.
    assert ad._flake_check_names(DEVRC_FLAKE) == ["pytests", "nodetests"]
    stripped = ad._nix_strip(DEVRC_FLAKE)
    assert len(stripped) == len(DEVRC_FLAKE), (
        "`_nix_strip` must preserve LENGTH — the match offset it returns is "
        "used to index the scan, so a stripper that shortens the text scans "
        "from the wrong place"
    )
    assert stripped.count("\n") == DEVRC_FLAKE.count("\n"), (
        "`_nix_strip` must preserve NEWLINES, or every line-anchored pattern "
        "over its output sees a different file"
    )
    assert "checks.${system} = {" in stripped, (
        "the stripper blanked a line of CODE; every 'no fabricated name' row "
        "above would then pass because nothing survives the strip at all"
    )
    assert "runHook preBuild" not in stripped, (
        "the stripper left a `''…''` build script in place, so the rows above "
        "are asserting nothing about stripping"
    )


def test_a_nested_indented_string_inside_an_interpolation_is_not_a_terminator():
    """🔴 REGRESSION. Red at `ba321c06` in BOTH directions, on this branch's fix.

    Round 15 taught the scanner nix's three two-apostrophe spellings and left
    the token that cannot be lexed without a STACK: the `''` that OPENS a nested
    string inside a `${…}`. `shellHook = '' ${lib.optionalString c '' … ''} '';`
    is an ordinary nixpkgs idiom, and a flat "walk to the next `''`" reads that
    opener as the OUTER string's terminator.

    Measured at `ba321c06` through `_flake_check_names`:

        no `checks` output at all           -> ['unit']   (FABRICATED)
        `checks.x86_64-linux = {unit;lint;}`
          with an ''${…} escape in the body -> None       ("**no `checks`
                                                           output**", in bold)

    The first fences `nix build <wt>#checks.x86_64-linux.unit` against a repo
    that has no such attribute — a command that exits on an attribute error and
    reads exactly like a broken gate. The second denies the tier the same
    section calls the one the merge gates on. Both are the CONFIDENT answer; the
    `[]` valve cannot fire for either, because `CHECKS_DECL` runs over the same
    mis-lexed text.

    🔴 THE FIXTURES ARE A PRODUCT, NOT A ROW. Which direction a nested fixture
    exposes depends on the parity of the tokens in its body, so one fixture
    covers one direction by luck. Every inner body is driven against BOTH outer
    states, and `test_the_probe_hedges_when_the_nix_scan_did_not_end_cleanly`
    below pins where the answer goes when the lexer is genuinely lost.
    """
    for name, body in NESTED_INNER_BODIES.items():
        absent = ad._flake_check_names(_nested_interp_flake(body, declares=False))
        assert absent is None, (
            f"\n\ninner body {name!r}: the flake declares NO `checks` output "
            f"and the probe answered {absent!r}. The nested `''` was read as "
            "the outer string's terminator, so the interpolation body was "
            "promoted to CODE — and the brief fences a `nix build "
            "…#checks…<name>` for a repository that has no such attribute."
        )
        present = ad._flake_check_names(_nested_interp_flake(body, declares=True))
        assert present == ["unit", "lint"], (
            f"\n\ninner body {name!r}: the flake DOES declare "
            "`checks.x86_64-linux = {{ unit; lint; }}` and the probe answered "
            f"{present!r}. `None` there is the brief stating in BOLD that the "
            "repository has no sandbox tier; a short list is a tier run "
            "half-way. Both are confident, and the nested string is what "
            "inverted the parity."
        )
    # 🔴 THE OTHER TWO SHAPES OF THE SAME CLASS — `''` that is not a string
    # delimiter at all, and `''` inside a `#` comment.
    assert ad._flake_check_names(CHECKS_IDENT_WITH_APOSTROPHES) == [
        "unit", "lint"
    ], (
        "`foo'' = 1;` is ONE nix identifier — `'` is an identifier character, "
        "which `_NIX_NAME` already knew. Read as a string opener it blanks the "
        "rest of the file and the answer is `None`."
    )
    assert ad._flake_check_names(CHECKS_COMMENTED_APOSTROPHES) == [
        "unit", "lint"
    ], (
        "a `#` comment mentioning the `''`-string idiom opened an indented "
        "string that never closed, blanking the real `checks` block after it"
    )


def test_the_comment_stripper_is_reachable_in_its_own_right():
    """🔴 THE `#` ROW IN `CHECKS_SHAPES_NOT_CODE` IS INERT, AND THIS IS WHY.

    Both patterns are anchored `^[ \\t]*checks`, and a `#` line comment puts a
    `#` in front of the word on every line it comments out. So that row answers
    `None` at every revision this module has ever had — stripper or no stripper
    — and it reads as coverage of the comment handling while providing none.
    `claude/RULES.md`: a guard that reads as coverage while providing none is
    worse than no guard, because it stops anyone looking.

    Two things are pinned here instead, and neither is walkable by the anchor:
    the comment TEXT is really blanked (so the row's own name becomes true of
    something), and a `#` comment whose text would otherwise open a string does
    not blank the code after it — the case the anchor cannot reach and the one
    that flips a real answer.
    """
    text = CHECKS_SHAPES_NOT_CODE["a `#`-commented checks block"]
    stripped = ad._nix_strip(text)
    assert "checks" not in stripped, (
        "the `#`-commented `checks` line survived the strip. The anchored "
        "regex rejects it anyway, which is why the fixture row cannot see "
        "this — an unanchored `re.search` would fabricate a tier from it."
    )
    assert "packages.default = 1;" in stripped, (
        "the stripper blanked the CODE after the comment too, so the assertion "
        "above passes for a stripper that blanks everything"
    )
    # The positive control for the whole comment branch: without it the fixture
    # below answers `None`, because its `''` opens a string that never closes.
    assert ad._flake_check_names(CHECKS_COMMENTED_APOSTROPHES) == ["unit", "lint"]


def test_the_probe_hedges_when_the_nix_scan_did_not_end_cleanly():
    """🔴 WHERE THE LEXER IS LOST, THE ANSWER IS `[]` — never `None`, never names.

    Round 16's two measured failures both routed to the STRONGEST wrong answer:
    a fabricated name list one way, a bolded "no `checks` output" the other.
    `None` and a name list are the two confident sentences; `[]` is the hedge
    that hands over `CHECKS_DISCRIMINATOR`. So `_nix_scan` reports whether it
    ended in a state a nix file can be in, and the caller degrades on that
    BEFORE it reads anything — the valve has to sit above the parse, because
    both patterns run over the same mis-lexed text.

    A `_PROBE_MAX_BYTES` truncation is the same observable and lands the same
    way, which is the point: this is a property of the SCAN, not a list of
    known-bad inputs.
    """
    clean_cases = {
        "devrc's own flake": DEVRC_FLAKE,
        "homelab-infra's flake": HOMELAB_FLAKE,
        "an identifier ending in apostrophes": CHECKS_IDENT_WITH_APOSTROPHES,
        "a nested interpolation": _nested_interp_flake(
            NESTED_INNER_BODIES["plain"], declares=True),
    }
    for name, text in clean_cases.items():
        assert ad._nix_scan(text)[1] is True, (
            f"\n\n{name} does not lex cleanly, so `_flake_check_names` now "
            "hedges `[]` for it — the hedge is only honest while the clean "
            "cases really are clean, or every repository gets it"
        )
    dirty_cases = {
        "an unterminated indented string": "checks.x86_64-linux = ''\n  unit\n",
        "an unterminated double quote": 'checks.x86_64-linux = "unit\n',
        "an unclosed interpolation": "a = ''${\n",
        "an unclosed block comment": "/* checks.x86_64-linux = {\n  unit = 1;\n",
        "braces that never balance (a truncated read)":
            "checks.${system} = {\n",
    }
    for name, text in dirty_cases.items():
        assert ad._nix_scan(text)[1] is False, (
            f"\n\n{name} was reported as a CLEAN lex, so nothing degrades and "
            "whatever the patterns then say is stated with full confidence"
        )
        assert ad._flake_check_names(text) == [], (
            f"\n\n{name}: the probe answered "
            f"{ad._flake_check_names(text)!r} rather than the hedged `[]`. "
            "`None` states in bold that the repository has no sandbox tier and "
            "a name list fences a `nix build` at it; the lexer did not finish, "
            "so it is entitled to neither."
        )
    # 🔴 THE POSITIVE CONTROL FOR THE VALVE ITSELF: a hedge that fired on
    # everything would make every row above pass while destroying the function.
    assert ad._flake_check_names(DEVRC_FLAKE) == ["pytests", "nodetests"]
    assert ad._flake_check_names(HOMELAB_FLAKE) is None


def test_a_capped_python_test_walk_is_not_reported_as_no_python_tests(tmp_path):
    """🔴 REGRESSION. Red at `5bad0a0c`, where the cap failed SILENTLY.

    `_has_python_tests` answered `False` on a cap hit and its docstring called
    that "the honest not-detected wording". There was no such wording:
    `_toolchain_pytest_lines` returned `[]`, so the subset command AND the
    wrong-shell bar vanished with no note, while both sibling probes emit an
    explicit absence bar. Reachable on a real repository here — measured
    2026-08-30 on `~/workspace/civit`: 157,319 directories within depth ≤ 4
    after the skip list, first `test_*.py` at walk visit 1,185, so `py_tests`
    answered False for a repo that HAS a python suite. `os.walk` order is
    arbitrary, so which side of the cap a monorepo lands on is not stable.

    Both fixtures are DETERMINISTIC, which is the point: the capped one has no
    `test_*.py` at all, so no walk order can find one and the only way to leave
    the loop is the cap; the empty one is small enough that the walk always
    completes. Two states, two different sentences.
    """
    wide = Path(tmp_path / "wide")
    _write_repo(wide, envrc="use flake\n", gate=DEVRC_GATE, flake=DEVRC_FLAKE,
                py_test=False)
    for i in range(ad._PROBE_MAX_DIRS + 100):
        (wide / f"d{i:04d}").mkdir()
    # 🔴 FIXTURE REACH, established WITHOUT naming a symbol this fix
    # introduced: the tree exceeds the cap, so the walk cannot leave the loop
    # any other way, and it holds no `test_*.py`, so no walk order finds one.
    assert sum(1 for _ in wide.iterdir()) > ad._PROBE_MAX_DIRS
    assert not list(wide.rglob("test_*.py"))
    rc, out, err = run_main(["900"], toplevel=str(wide))
    assert rc == 0, err
    tool = toolchain_section(out)
    assert "THE PYTHON-TEST PROBE DID NOT FINISH" in tool, (
        f"\n\nthe walk hit its bound and the brief says nothing about it. An "
        f"auditor who reads no python line concludes there is no python "
        f"suite:\n{tool}"
    )
    assert 'not "there are no python tests"' in tool, (
        "the capped bar does not say what it is NOT, which is the whole "
        "distinction the third answer exists to carry"
    )
    for line in toolchain_commands(tool):
        assert "python3 -m pytest" not in line, (
            f"\n\na pytest command is prescribed for a repository whose test "
            f"probe never finished:\n    {line}"
        )

    # 🔴 THE OTHER ANSWER, and it must read differently. A walk that COMPLETED
    # and found nothing is a fact about the repository; a walk that stopped is
    # a lookup the auditor still owes.
    empty = Path(tmp_path / "empty")
    _write_repo(empty, envrc="use flake\n", gate=DEVRC_GATE, flake=DEVRC_FLAKE,
                py_test=False)
    rc, out, err = run_main(["900"], toplevel=str(empty))
    assert rc == 0, err
    tool = toolchain_section(out)
    assert "No `test_*.py` was found" in tool and "the walk COMPLETED" in tool, (
        f"\n\nthe absent-tests answer is not stated, or is not distinguished "
        f"from the capped one:\n{tool}"
    )
    assert "THE PYTHON-TEST PROBE DID NOT FINISH" not in tool, (
        "a completed walk is reported as a cap hit, which collapses the two "
        "answers back into one"
    )
    # 🔴 THE THIRD ANSWER, so "no pytest command" above is a measurement and
    # not a constant, and so the three states are pinned as three.
    assert ad._python_test_probe(wide) == ad.PY_TESTS_UNKNOWN
    assert ad._python_test_probe(empty) == ad.PY_TESTS_NONE
    assert ad._python_test_probe(Path(FAKE_REPO_DIR)) == ad.PY_TESTS_FOUND
    assert len({ad.PY_TESTS_UNKNOWN, ad.PY_TESTS_NONE, ad.PY_TESTS_FOUND}) == 3, (
        "two of the three answers are spelled the same, so the branches that "
        "distinguish them cannot be reached separately"
    )


def test_the_python_absence_bar_does_not_point_at_a_bar_that_is_not_there(tmp_path):
    """🔴 REGRESSION. Red at `ba321c06`, on both halves. Two dangling references.

    **The cross-reference.** The `PY_TESTS_NONE` bar said the completed walk
    "is a different answer from the one above". The branches are mutually
    exclusive — `if UNKNOWN: return …; if NONE: return …` — so the bar it
    contrasted itself with is emitted in exactly the runs where this one is
    not. A reader sent looking for a bar that is never on the page.

    **The diagnosis of an unprescribed command.** With `NONE` and a flake that
    names pytest, `_toolchain_shell_note` opened with a `python3 -m pytest`
    wrong-shell bar three lines after the same section said no python subset
    command is prescribed. Fixed as the general rule and not as a NONE special
    case: the python-specific bar is emitted only where a `python3 -m pytest`
    is actually fenced above it, and the language-agnostic one — which is true
    in every state — is unconditional and self-contained.

    Driven over all THREE `py_tests` states, because "the bar is absent" is
    satisfied by a note that vanished and "the bar is present" by one that never
    varies. Both are the failure this pins.
    """
    trees = {}
    for name, py_test in (("none", False), ("found", True)):
        root = Path(tmp_path / name)
        _write_repo(root, envrc="use flake\n", gate=DEVRC_GATE,
                    flake=DEVRC_FLAKE, py_test=py_test)
        trees[name] = root
    wide = Path(tmp_path / "unknown")
    _write_repo(wide, envrc="use flake\n", gate=DEVRC_GATE, flake=DEVRC_FLAKE,
                py_test=False)
    for i in range(ad._PROBE_MAX_DIRS + 100):
        (wide / f"d{i:04d}").mkdir()
    trees["unknown"] = wide
    # 🔴 FIXTURE REACH, ESTABLISHED RATHER THAN ASSUMED. Without this the three
    # rows below could all be the same state and every assertion would be a
    # claim about one branch wearing three names.
    assert [ad._python_test_probe(trees[k]) for k in ("none", "found", "unknown")
            ] == [ad.PY_TESTS_NONE, ad.PY_TESTS_FOUND, ad.PY_TESTS_UNKNOWN]

    sections = {}
    for name, root in trees.items():
        rc, out, err = run_main(["900"], toplevel=str(root))
        assert rc == 0, err
        sections[name] = toolchain_section(out)

    # 🔴 "the one above", not "from the one above": the narrower spelling is
    # walkable by "different from the one printed above". Nothing else in this
    # section says it — the sibling bars say "The runner above" — so the wider
    # phrase costs no false red and closes the reword.
    assert "the one above" not in sections["none"], (
        f"\n\nthe completed-walk bar points at 'the one above'. The capped bar "
        f"is the only thing it can mean and the two branches are mutually "
        f"exclusive, so it is never above:\n{sections['none']}"
    )
    assert "the walk COMPLETED and found none" in sections["none"], (
        "the contrast itself was dropped instead of being re-pointed; the two "
        "answers are the whole reason there are three states"
    )
    # The python-specific diagnosis: present exactly where a python command is.
    for name, section in sections.items():
        fenced_pytest = any("python3 -m pytest" in c
                            for c in toolchain_commands(section))
        diagnosed = "A bare `python3 -m pytest` failing" in section
        assert diagnosed == fenced_pytest, (
            f"\n\npy_tests={name!r}: a `python3 -m pytest` wrong-shell bar is "
            f"{'present' if diagnosed else 'absent'} while such a command is "
            f"{'fenced' if fenced_pytest else 'NOT fenced'} above it. A "
            f"diagnosis of a command this section did not prescribe sends the "
            f"reader looking for something that is not there:\n{section}"
        )
        # …and the language-agnostic bar is there in EVERY state, so dropping
        # the python one is not the note vanishing again (round 15's F9).
        assert "WRONG SHELL and not a broken gate" in section, (
            f"\n\npy_tests={name!r}: no wrong-shell diagnosis at all. That is "
            f"round 15's F9 re-opened by this round's fix:\n{section}"
        )
        assert "in EVERY language" in section, (
            f"py_tests={name!r}: the diagnosis no longer says it covers every "
            f"language, so it reads as a python note again:\n{section}"
        )


def test_the_wrong_shell_diagnosis_covers_every_command_not_only_pytest(tmp_path):
    """🔴 REGRESSION. Red at `5bad0a0c`. A python probe picked the NODE shell.

    `toolchain_shell` asks ONE question — does `flake.nix` mention pytest — and
    its answer wraps the language-agnostic gate as well as the pytest command.
    So a repository with a real `scripts/gate.sh`, a flake devShell providing
    `nodejs`, and no python tests rendered a BARE `bash <wt>/scripts/gate.sh`
    with NO `nix develop` and, because the wrong-shell bar lived inside
    `_toolchain_pytest_lines`, no wrong-shell diagnosis ANYWHERE in the
    section. That gate fails `node: command not found` and reads exactly like a
    broken gate — the mirror of the defect this section exists to prevent, and
    the one the brief itself calls "a finding against a PR that is fine".
    """
    node = _write_repo(tmp_path / "node-repo", envrc="use flake\n",
                       gate=DEVRC_GATE, flake=NODE_ONLY_FLAKE, py_test=False)
    rc, out, err = run_main(["900"], toplevel=node)
    assert rc == 0, err
    tool = toolchain_section(out)
    cmds = toolchain_commands(tool)
    assert any("gate.sh" in c for c in cmds), (
        f"the repository's real gate is not prescribed at all, so the claim "
        f"below is about nothing:\n{cmds}"
    )
    assert "WRONG SHELL" in tool, (
        f"\n\nthis brief prescribes a BARE gate for a repository whose flake "
        f"provides its toolchain, and carries no wrong-shell diagnosis "
        f"anywhere. `node: command not found` then reads as a broken "
        f"gate:\n{tool}"
    )
    assert "command not found" in tool, (
        f"\n\nthe only wrong-shell wording is the pytest one, so an auditor "
        f"whose gate fails on `node`/`go`/`cargo` has been told nothing:\n{tool}"
    )
    # The bar must be honest about what decided the shell. This repository's
    # flake names no pytest, which is the ONLY shell probe made.
    assert "the only shell probe made here" in tool, (
        f"\n\nthe brief does not say WHICH probe decided there is no dev "
        f"shell, so the auditor cannot check it:\n{tool}"
    )
    # 🔴 AND THE MIRROR, so "the note is present" is not a constant: a repo
    # whose flake DOES name pytest gets the wrapper AND a note saying that a
    # python fact decided the shell for every command above it.
    tool = toolchain_section(brief_for_scenario("delta"))
    assert "in EVERY language" in tool and "mentions `pytest` SOMEWHERE" in tool, (
        f"\n\nthe wrapped branch does not say that ONE python probe decided "
        f"the shell for commands that may run anything:\n{tool}"
    )


def test_an_unreadable_flake_is_not_reported_as_an_absent_one(tmp_path):
    """🔴 REGRESSION. Red at `5bad0a0c`. A failed READ became a fact about the repo.

    `_read_probe` answers `None` for an absent file and for one it could not
    open alike, so a `flake.nix` the probe cannot read rendered ``⚠ `<root>`
    has no `flake.nix` `` — the same class as the walk cap two guards up, and
    the same remedy: say only what the probe established. Fixed in the
    direction that costs no new state, because the wording was the whole
    over-claim.
    """
    root = Path(_write_repo(tmp_path / "locked", envrc="use flake\n",
                            gate=DEVRC_GATE, flake=DEVRC_FLAKE))
    flake = root / "flake.nix"
    flake.chmod(0)
    try:
        # 🔴 FIXTURE REACH, established rather than assumed: mode 000 is not
        # unreadable for root, and a test that silently ran as one would assert
        # the ABSENT branch's wording about a file it read fine.
        if flake.is_file() and ad._read_probe(root, "flake.nix") is not None:
            pytest.skip("this user can read a mode-000 file (running as root?)")
        assert flake.is_file(), "the fixture is not the state under test"
        tool = toolchain_section(run_main(["900"], toplevel=str(root))[1])
    finally:
        flake.chmod(0o644)
    assert "no READABLE `flake.nix`" in tool, (
        f"\n\nthe probe could not OPEN this repository's `flake.nix`, and the "
        f"brief reports that the repository HAS none — a claim about the repo "
        f"derived from a failed read:\n{tool}"
    )


def test_the_toolchain_head_claim_is_true_of_the_rendered_brief():
    """🔴 REGRESSION. Red at `5bad0a0c`: the constant made a FALSE count.

    `TOOLCHAIN_HEAD` said the assembly checkout "appears below at most ONCE, as
    the argument to `nix develop`". #1104's wording was "appears ONLY as the
    argument to `nix develop`" and was true of what that revision rendered;
    this branch strengthened it into a COUNT while adding two prose mentions of
    the same path. Measured at `5bad0a0c` for devrc: FOUR occurrences, two of
    them not `nix develop` arguments.

    Nothing pinned it, and the reason is worth stating: the guard over this
    block asks whether the rendered reason IS this constant, never whether the
    constant is TRUE of what follows it. A claim can be pinned byte-for-byte
    and false in every brief that carries it.

    🔴 ROUND 16 — RED AT `ba321c06` TOO, ON ROUND 15'S OWN REPLACEMENT WORDING,
    AND ON THIS GUARD'S OWN SCOPE. "the prose names it too, to say what was read
    out of it" is a claim about a STATE, put back into the state-INDEPENDENT
    constant. Counted at `ba321c06` over all thirteen scenarios the module had
    there (round 16 adds three more, closing the target x checkout-kind grid):

        probed       (6)  3 prose lines naming the assembly checkout  -> true
        cross-repo   (5)  1, and it says "is a DIFFERENT repository"  -> false
        repo-unknown (2)  0 — the path is absent from the section     -> flatly

    and this guard drove ONLY `delta`. A guard narrower than the sentence it
    certifies is the shape round 15's F2 was filed for, reproduced one round
    later inside F2's own fix — so the fix is BOTH halves: the clause is now the
    widest thing true in every state (wherever the path appears outside a
    `nix develop` argument it is PROSE, never something to run in), and the
    guard drives every scenario the module has.

    The `assert prose` half is scoped to the states that HAVE prose, and its
    non-vacuity is asserted globally instead: if no scenario anywhere named the
    path in prose, the clause should go back to #1104's "ONLY as the argument to
    `nix develop`", which would then be true again.
    """
    root = FAKE_REPO_DIR
    fenced_naming_total, prose_naming_total = 0, 0
    for scenario in SCENARIOS:
        tool = toolchain_section(brief_for_scenario(scenario))
        assert "at most ONCE" not in tool, (
            f"\n\nscenario {scenario!r}: the false COUNT is back. The path "
            "appears in prose too; a count over the whole section cannot be "
            "satisfied while it does"
        )
        fenced = toolchain_commands(tool)
        naming = [c for c in fenced if root in c]
        fenced_naming_total += len(naming)
        for line in naming:
            assert line.startswith(f"nix develop {root} -c "), (
                f"\n\nscenario {scenario!r}: a RUNNABLE command names the "
                f"assembly checkout somewhere other than as the `nix develop` "
                f"argument:\n    {line}\n"
                "  That is the tree under test pointing at somebody else's "
                "checkout, which is what TOOLCHAIN_HEAD promises never happens."
            )
            assert line.replace(
                f"nix develop {root} -c ", "", 1).count(root) == 0, (
                f"\n\nscenario {scenario!r}: the assembly checkout appears a "
                f"SECOND time in one command, past the `nix develop` "
                f"argument:\n    {line}"
            )
        prose_naming_total += len(
            [ln for ln in tool.splitlines() if root in ln and ln not in fenced])
        # 🔴 PER-SCENARIO NON-VACUITY, KEYED ON THE DECLARED TARGET — and it is
        # here because the global counters below LOST a kill without it.
        # Measured: mutant C3 (the cross-repo decision inverted) was killed by
        # this guard while it drove `delta` alone, and survived the first
        # global-counter spelling — the inversion makes the cross-repo
        # scenarios probe, so the totals stay non-zero and only the
        # per-scenario shape can see that the WRONG scenarios produced them.
        # `TOOLCHAIN_TARGET_OF` is the declared intent; a rendering that
        # disagrees with it is the finding.
        probed_target = TOOLCHAIN_TARGET_OF[scenario] == "probed-devrc-shape"
        assert bool(naming) == probed_target, (
            f"\n\nscenario {scenario!r} is declared "
            f"{TOOLCHAIN_TARGET_OF[scenario]!r} and "
            f"{'names' if naming else 'does NOT name'} the assembly checkout "
            f"in a fenced command. A probed target must run something in that "
            f"dev shell; a NOT-probed one must prescribe nothing at all — a "
            f"fenced command there is a confident command list for a "
            f"repository this run never read.\n  fenced: {fenced}"
        )
    # 🔴 BOTH NON-VACUITY CONTROLS, GLOBAL RATHER THAN PER-SCENARIO — because
    # neither half is true in every state and pretending otherwise is the
    # finding. A section that never names the path at all satisfies every loop
    # above while saying nothing.
    assert fenced_naming_total, (
        "no fenced command in ANY scenario names the assembly checkout, so the "
        "`nix develop` half of the claim is vacuous everywhere. Check "
        "FAKE_REPO_DIR is still a real directory that gets probed."
    )
    assert prose_naming_total, (
        "no scenario names the assembly checkout in PROSE, so the reworded "
        "clause describes something that never happens. Put #1104's 'ONLY as "
        "the argument to `nix develop`' back — it would be true again."
    )
    # 🔴 THE WHOLE NORMALISED STRING, and it is the only instrument that works
    # here. `claude/RULES.md`: when the artifact under test IS prose, a guard on
    # WORDS is walkable by REWORDING — and this constant has now been reworded
    # into a false claim TWICE, in the fix for the previous rewording each time
    # ("at most ONCE" -> "the prose names it too"). Neither loop above can see
    # it: both are still satisfied by a head that says something untrue about
    # states it does not render in.
    #
    # A cosmetic reword fails here on purpose. Before changing it, RE-COUNT the
    # prose lines naming the assembly path per target — 3 / 1 / 0 at `ba321c06`
    # for probed / cross-repo / repo-unknown — and write only what holds in all
    # three; then paste the new normalised string in.
    assert " ".join(ad.TOOLCHAIN_HEAD.split()) == (
        "## TOOLCHAIN — the exact commands, and the two ways they lie 🔴 "
        "`<your worktree>` below is **your own copy** — the one WHERE TO WORK "
        "told you to make. The checkout this brief was assembled in appears "
        "below in RUNNABLE commands only as the argument to `nix develop`, "
        "where it resolves the dev shell and nothing else; wherever else it "
        "appears it is PROSE — a statement ABOUT that checkout, never "
        "something to run in. Never point a gate script or a `nix build` at "
        "it: a gate script resolves its root from its own path, so running "
        "that copy runs the suite in a checkout that is NOT yours — one "
        "holding none of your mutations — and a `nix build <ref>#…` builds "
        "that ref's tree, not yours."
    ), (
        "\n\nTOOLCHAIN_HEAD changed. It is a state-INDEPENDENT constant, so "
        "every claim in it must hold in all three targets and all three "
        "checkout states; this pin is whole-string because the last two "
        "rewordings each replaced a false claim with a different false claim "
        "and no word-level guard could tell.\n"
        f"--- now ---\n{' '.join(ad.TOOLCHAIN_HEAD.split())}"
    )


def test_no_toolchain_note_claims_a_probe_the_brief_itself_denies():
    """🔴 REGRESSION. Red at `5bad0a0c`, in the cross-repo and unknown states.

    `TOOLCHAIN_TAIL`'s own comment said "Both notes are true of every
    repository and every checkout state". One was not: "everything above was
    PROBED out of this repository rather than assumed" is a claim about a
    STATE, and the not-probed branch renders it three lines under its own
    `🔴 NOTHING WAS PROBED HERE, SO NOTHING IS PRESCRIBED`. One document
    contradicting itself — and `test_the_toolchain_reason_is_true_in_every_
    scenario` CERTIFIED the contradiction, because it requires the tail
    verbatim in every scenario.
    """
    denied, probed = 0, 0
    for scenario in SCENARIOS:
        tool = toolchain_section(brief_for_scenario(scenario))
        if "NOTHING WAS PROBED HERE" in tool:
            denied += 1
            assert "everything above was PROBED out of this repository" not in tool, (
                f"\n\nscenario {scenario!r}: the section says NOTHING WAS "
                f"PROBED HERE and then says everything above was probed out of "
                f"this repository. A reader believes neither, and the "
                f"instruction each carries goes with it:\n{tool}"
            )
            # …and the instruction the moved clause carried must survive here.
            assert "NAME the one you used, by path" in tool, (
                "the not-probed branch lost the 'name the runner by path' "
                "instruction along with the false clause"
            )
        else:
            probed += 1
            assert "everything above was PROBED out of this repository" in tool, (
                f"\n\nscenario {scenario!r} DID probe, and the clause that "
                f"tells the auditor to name the runner by path is gone:\n{tool}"
            )
    assert denied and probed, (
        f"both states must be present or this guard grades one branch: "
        f"{probed} probed, {denied} not"
    )


def test_the_toolchain_probes_are_reachable_in_both_directions(tmp_path):
    """🔴 INVARIANT GUARD, and its evidence is that it CLOSES three mutants.

    `mutants-audit-dispatch.py` states the rule — "Each probe is broken in BOTH
    directions where both are reachable" — and it was applied to `gate_sh`, the
    checks regex, `flake_pytest` and `probe_root`, and to none of these three.
    Measured at `5bad0a0c`, each SURVIVED a fully green 115-test suite while
    shipping a fabricated command:

        `if tc.ci_suite:` -> `if True:`   `bash <wt>/scripts/tests/
                                          run-ci-suite.sh` for devrc, which has
                                          no such file
        `if not tc.py_tests: return []`   `python3 -m pytest` for a repo with
          -> `if False:`                  no python tests
        `gate_tier=bool(...)` -> `True`   `--tier both` on any repo's gate.sh —
                                          the exact fabrication that field
                                          exists to prevent

    The root cause is FIXTURE REACH, not a missing assertion: `_write_repo`'s
    `py_test=` was never passed `False` by any call site, no fixture had a
    `gate.sh` lacking `--tier both`, and no test asked whether
    `run-ci-suite.sh` was ABSENT from the devrc-shaped brief. A branch no
    fixture enters is a branch no assertion can grade.
    """
    # 1. A repo with a gate that does NOT take `--tier`, and no run-ci-suite.
    plain = _write_repo(tmp_path / "plain", envrc="use flake\n",
                        gate=PLAIN_GATE, flake=DEVRC_FLAKE)
    tool = toolchain_section(run_main(["900"], toplevel=plain)[1])
    cmds = toolchain_commands(tool)
    assert any(c.endswith("/scripts/gate.sh") for c in cmds), (
        f"\n\nthis repo HAS a `scripts/gate.sh` and the brief prescribes it "
        f"with arguments it never showed it takes, or not at all:\n{cmds}"
    )
    for line in cmds:
        assert "--tier" not in line, (
            f"\n\n`--tier both` is prescribed for a `gate.sh` whose text does "
            f"not contain it. A flag is an artifact too:\n    {line}"
        )
        assert "run-ci-suite.sh" not in line, (
            f"\n\n`run-ci-suite.sh` is prescribed for a repository that has "
            f"none:\n    {line}"
        )

    # 2. The devrc-shaped fixture the rest of the module drives: no
    # `run-ci-suite.sh` there either, and nothing asserted it.
    tool = toolchain_section(brief_for_scenario("delta"))
    for line in toolchain_commands(tool):
        assert "run-ci-suite.sh" not in line, (
            f"\n\nthe devrc-shaped target has no `scripts/tests/"
            f"run-ci-suite.sh`, and the brief hands one over:\n    {line}"
        )

    # 3. A repo with NO python tests: no pytest command, and it says why.
    nopy = _write_repo(tmp_path / "nopy", envrc="use flake\n",
                       gate=DEVRC_GATE, flake=DEVRC_FLAKE, py_test=False)
    tool = toolchain_section(run_main(["900"], toplevel=nopy)[1])
    for line in toolchain_commands(tool):
        assert "python3 -m pytest" not in line, (
            f"\n\na pytest command is prescribed for a repository with no "
            f"`test_*.py` anywhere:\n    {line}"
        )

    # 4. `_fmt_uses`' two fallbacks, neither of which any fixture reached.
    noenv = _write_repo(tmp_path / "noenv", envrc=None, gate=DEVRC_GATE,
                        flake=DEVRC_FLAKE)
    tool = toolchain_section(run_main(["900"], toplevel=noenv)[1])
    assert "absent from the tree probed" in tool, (
        f"\n\nthe repo has no `.envrc` and the brief does not say so:\n{tool}"
    )
    blank = _write_repo(tmp_path / "blank", envrc="# nothing to declare\n",
                        gate=DEVRC_GATE, flake=DEVRC_FLAKE)
    tool = toolchain_section(run_main(["900"], toplevel=blank)[1])
    assert "present but declares no `use` line" in tool, (
        f"\n\nthe repo's `.envrc` declares no `use` line and the brief does "
        f"not say so:\n{tool}"
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

    # 🔴 AND THE PROSE MAY NOT SAY OTHERWISE. Read through `flat_prose`, the
    # SHARED normaliser — one rule in one place. Round 7 gave this test its own
    # copy of `surviving_refuted_premises`'s three-liner, and round 8 measured
    # both copies blind to the same three shapes; two copies of a predicate are
    # wrong at both sites in the same direction, which is exactly what happened.
    script_src = Path(ad.__file__).read_text(encoding="utf-8")
    script = flat_prose(script_src)
    module = flat_prose(_THIS_MODULE_SOURCE)

    # 🔴 POSITIVE CONTROL, ONE PER SHAPE, AND PLANTED INTO REAL SOURCE. Round
    # 7's control handed the normaliser the bare string `f"x {phrase} y"` —
    # which is not Python, is not the thing the assertions below scan, and
    # could not have exercised a comment wrap or a concatenation even in
    # principle. A control that shares no step with the measurement is not a
    # control. These plant into the SCRIPT's own source, which is what the
    # `== 0` assertion below reads.
    probe = FALSE_BLIND_SPOT_RATIONALES[0]
    assert not set(probe) & set("\"'\\"), (
        f"the plants below embed {probe!r} in GENERATED Python source, so a "
        "quote or a backslash in it would raise SyntaxError instead of "
        "testing anything. Pick a quote-free ledger entry for this control."
    )
    pw = probe.split()
    ph, pt = " ".join(pw[:2]), " ".join(pw[2:])
    assert ph and pt, f"{probe!r} does not split into two wrappable halves"
    for shape, payload in {
        "one `#` line": f"\n# {probe}\n",
        "two `#` lines": f"\n# {ph}\n# {pt}\n",
        "wrapped docstring": f'\ndef _plant():\n    """{ph}\n    {pt}"""\n',
        'concat "…" "…"': f'\n_plant = ("{ph} "\n          "{pt}")\n',
        "concat '…' '…'": f"\n_plant = ('{ph} '\n          '{pt}')\n",
        "concat \"…\" '…'": f'\n_plant = ("{ph} "\n' + f"          '{pt}')\n",
    }.items():
        assert flat_prose(script_src + payload).count(collapse_ws(probe)) == 1, (
            f"POSITIVE CONTROL ({shape}): the scan cannot see {probe!r} planted "
            "into the script in that shape, so the zero asserted below says "
            "nothing about a rationale written that way — and this ladder "
            "writes its rationales in wrapped comment blocks."
        )
    for phrase in FALSE_BLIND_SPOT_RATIONALES:
        p = collapse_ws(phrase)
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


# 🔴 ROUND 12 — THE WHOLE CAVEAT, NORMALISED, not two fragments out of it.
# Measured at `88b4105c`: replacing "**as it stands in this checkout**, and"
# with "and it is fine. It" — which guts the qualifier the whole caveat exists
# for, and inverts what it tells the reader to do — left the suite at 109
# passed, because both pinned fragments ("does not fetch", "stale base")
# survive the reword untouched. `claude/RULES.md` -> spelled-guards: when the
# artifact under test IS prose, a guard on words is walkable by rewording, so
# pin the whole normalised string and pay the cosmetic-reflow cost.
#
# `origin/main` is interpolated from `facts.base_ref`; this run is round 3, so
# that is the spelling the delta half renders.
STALE_BASE_CAVEAT = norm(
    "⚠ `<base>` here is `origin/main` **as it stands in this checkout**, and "
    "it is the ONLY end of that command that can mean something different "
    "where you are standing — both ends of the range itself are shas. This "
    "script does not fetch (that would be a write to a checkout it does not "
    "own), so a stale base re-reports upstream work as this round's payload. "
    "If the number looks large, that is the first thing to check."
)


def test_the_ledger_says_the_base_was_not_fetched():
    """The script cannot fetch (that would be a write), so it says so.

    A stale `<base>` re-reports upstream work as this round's payload — measured
    at 201 lines where the truth was 1.

    🔴 ROUND 12 — PINNED WHOLE. This test used to assert two fragments and was
    killed by NO row in the mutation battery: zero occurrences of its name
    across the full 100-row log, while it sits in `INVARIANT_GUARDS_AND_LEDGERS`
    whose declared evidence IS the battery. Round 10 removed the evidence for
    it when the C3 killer set shrank. Rows V26 and V27 restore it — one deletes
    the caveat (this test is the sole detector, measured), one rewords it while
    leaving both old fragments intact (the reachability control for the whole
    string).
    """
    rc, out, err = run_main(["900", "--round", "3"], comments=[CLAIMS_BLOCK_R2])
    assert rc == 0, err
    assert STALE_BASE_CAVEAT in norm(out), (
        "\n\nTHE LEDGER's stale-base caveat is gone or reworded. It is the "
        "only thing telling the reader that `--not <base>` resolves LOCALLY "
        "in a checkout this script deliberately never fetched, and a reword "
        "that keeps the words `does not fetch` and `stale base` while "
        "dropping `as it stands in this checkout` inverts what it asks for "
        "and passed a fragment pin.\n\n"
        f"expected: {STALE_BASE_CAVEAT}\n\ngot section: {norm(out)[-1200:]}"
    )


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

# 🔴 ROUND 8's base is the ROUND-7 TREE, `28492af2`. Measured the same way —
# `git show 28492af2:scripts/audit-dispatch.py` into a scratch tree with THIS
# module copied in unchanged, `PYTHONDONTWRITEBYTECODE=1 -p no:cacheprovider`:
# **9 failed, 93 passed**. SIX of the nine are these; of the other three, two
# are the whole-string clause and directive ledgers moving with round 8's
# reword, and one is the skipped-round warning guard, which is red there on an
# ABSENCE the fix adds.
#
# 🔴 THREE OF ROUND 8's NEW TESTS ARE **NOT** HERE, for three different
# reasons, and each is recorded rather than quietly filed:
#   * `..._claims_block_more_than_one_round_behind_is_warned_about` IS red at
#     the base, and is filed as a guard anyway: no such warning exists there,
#     so its red is an absence the fix adds, which this module says everywhere
#     is not evidence of anything. Its evidence is mutant V16.
#   * `..._no_cross_repo_brief_claims_its_checkout_was_verified` is GREEN at the
#     base. What was wrong there was this module's FIXTURE, not the script:
#     `OTHER_REPO_PR` inherited `DEFAULT_PR`'s `headRefOid`, so the checkout was
#     modelled as standing on a commit of another repository. Carrying the FIXED
#     fixture back to the base tree therefore passes. Its evidence is mutant V9.
#   * `..._refuted_premise_survives_in_this_modules_prose` (round 7's) still
#     cannot be red under this procedure at all — it scans THIS MODULE, which
#     the procedure copies in unchanged.
RED_AT_BASE_R7: frozenset[str] = frozenset({
    # 🔴 NOT ONE OF ROUND 7's FINDINGS. Found in round 8 by sweeping for the
    # ladder's recurring shape, which round 7's auditor asked for explicitly:
    # `newest is not None` was read as "there is an anchor". Red at the base on
    # a WRONG ANSWER, not an absence — `Diff `None..<sha>`` at rc 0.
    "test_a_block_that_parses_but_yields_no_anchor_is_refused",
    "test_the_ledgers_provenance_line_names_the_sha_it_resolved_not_head",
    "test_the_degenerate_range_names_shas_at_both_ends",
    "test_a_cross_repo_range_states_the_impossibility_not_a_moved_checkout",
    "test_a_cross_repo_ledger_hands_the_attribution_gate_to_the_auditor",
    "test_the_no_write_forward_reference_states_the_clauses_own_scope",
})

# 🔴 ROUND 10's base is the ROUND-9 TREE, `706a6b38`. Measured the same way —
# `git show 706a6b38:scripts/audit-dispatch.py` into a scratch tree with THIS
# module copied in unchanged, `PYTHONDONTWRITEBYTECODE=1 -p no:cacheprovider`:
# **7 failed, 102 passed**. FIVE of the seven are these. Of the other two, one
# is the whole-string directive ledger moving with round 10's reword of
# `own-worktree-is-writable`, and one is
# `test_the_tip_placeholder_ledger_matches_the_script`, filed as a guard above
# because its red there is an `AttributeError` for a name the fix introduces.
# (An earlier run of the same procedure read 7/101 with the partition guard
# among the failures, because the base module's ledgers did not yet list this
# round's tests. The figure above is the FINAL one, re-measured after they did.)
#
# 🔴 `test_no_brief_claims_a_verification_its_own_fixture_refutes` is NOT here
# and is not new: round 8 filed it as a guard, and round 10 RESCOPED it. It is
# still green at this base for the same reason — a base script says nothing
# about a base fixture — and its evidence is still mutant V9.
RED_AT_BASE_R9: frozenset[str] = frozenset({
    "test_a_placeholder_tip_is_never_handed_over_as_though_it_were_a_sha",
    "test_a_cross_repo_ledger_prints_a_measurement_the_head_check_vouched_for",
    "test_every_command_a_refusal_prescribes_actually_runs",
    "test_the_clone_grant_covers_only_the_write_the_recipe_makes",
    # 🔴 NOT ONE OF ROUND 9's FINDINGS — round 10's own, found by the sweep its
    # auditor asked for. Red at the base on a WRONG ANSWER, not an absence: the
    # brief asserted a DEFAULT base branch as a fact about the PR's repository,
    # at rc 0, inside the hand-over THE LEDGER calls mandatory.
    "test_no_brief_states_an_assumed_base_branch_as_a_fact",
})

# 🔴 ROUND 13. Three tests, watched RED at `6349a8b9` — the tree round 12's
# work MERGED into, which is why round 13 audited it rather than a branch. Each
# is red there on a WRONG ANSWER, not on an absence:
#
#   test_no_brief_blames_gh_for_a_repo_gh_was_never_asked_about
#       WHERE TO WORK: "`gh` did not report which repository the PR lives in"
#       in a `--claims-file` run, whose own RANGE section two headings away
#       says the run consults no `gh`. rc 0, silent stderr.
#   test_no_gh_pr_view_this_script_prescribes_omits_repo
#       the same section prescribes `gh pr view 900 --json url` with no
#       `--repo`, which in the auditor's own repo returns THAT repo's PR #900
#       at rc 0 — so it can only ever confirm "same repo".
#   test_the_cross_repo_recipe_can_actually_be_run
#       `worktree add <path> <the PR's head branch>` names a ref the clone has
#       only if it FETCHED, and the grant beneath it refuses every write but
#       `worktree add`. Measured rc 128 on real scratch repos, git 2.55.0.
#
# `test_an_undeterminable_repo_gets_its_own_branch_not_the_same_repo_one` and
# `test_the_clone_grant_covers_only_the_write_the_recipe_makes` are NOT
# repeated here — both were already filed at earlier refs, and both are red at
# this one too for round 13's reasons. A test belongs to the ref it was FIRST
# watched red at.
RED_AT_BASE_R13: frozenset[str] = frozenset({
    "test_no_brief_blames_gh_for_a_repo_gh_was_never_asked_about",
    "test_no_gh_pr_view_this_script_prescribes_omits_repo",
    "test_the_cross_repo_recipe_can_actually_be_run",
})

# 🔴 ROUND 14's base is `9e23c379` — `origin/main` with #1104 ALREADY MERGED,
# and NOT the tree this work started on. It began at `bd1572f3`; #1104 landed
# mid-run, the branch was rebased onto it, and `bd1572f3` stopped being the
# merge-base. A ledger still naming it would be a claim about a tree this
# branch is not built on, so the matrix was RE-MEASURED at the new anchor
# rather than carried over: `git show 9e23c379:scripts/audit-dispatch.py` into
# a scratch tree with THIS module copied in unchanged,
# `PYTHONDONTWRITEBYTECODE=1 -p no:cacheprovider` -> **4 failed, 111 passed**.
#
# 🔴 `9e23c379` IS KEPT AS THE ANCHOR EVEN THOUGH MAIN MOVED AGAIN (the branch
# was rebased a second time, onto `ebbe5eaa`) — and that is a MEASUREMENT, not
# a convenience. This module's rule is "a test belongs to the ref it was FIRST
# watched red at", and the two refs are the same tree for this script:
#   git rev-parse 9e23c379:scripts/audit-dispatch.py
#   git rev-parse ebbe5eaa:scripts/audit-dispatch.py
#     -> 0298a689d176d517b4997aa48216b3cfd67ada70   (both)
# Byte-identical blob, so the red measured at `9e23c379` describes the current
# merge-base exactly. `9e23c379` is the more informative of the two because it
# is #1104's OWN merge commit — naming it is what makes "the competing fix
# landed and this is still red" a checkable claim rather than an assertion.
#
# 🔴 AND THAT RE-MEASUREMENT IS THE WHOLE POINT: #1104 FIXED THIS SECTION AND
# BOTH ROWS ARE STILL RED AT ITS MERGE COMMIT. It hedged the prose ("if this
# repo has one", "exist in SOME repos and not others") and left all four
# commands inside the FENCES and the `.envrc` sentence hardcoded — so at
# `9e23c379` the first row still fails on `assert "nix develop" not in line`,
# a wrapper measured to exit `No module named 'pytest'` on that very target,
# and the second on the absent hand-over. Both fail on an ASSERTION, not on an
# import or an arity error: the base script renders a TOOLCHAIN section fine,
# it just renders devrc's layout into it whatever the target.
#
# 🔴 `test_the_flake_checks_probe_reads_an_output_and_not_the_word` is NOT
# here, deliberately. `_flake_check_names` does not exist at `9e23c379`, so it
# would fail there with `AttributeError` — a claim about the symbol's absence
# and not about any behaviour. That is the vacuous shape this module refuses to
# count as regression coverage, so it is filed as an invariant guard and its
# evidence is its own two controls (the real `homelab-infra` heredoc line, the
# real devrc two-name block) plus mutants V38 and V41.
RED_AT_BASE_R14: frozenset[str] = frozenset({
    "test_the_toolchain_prescribes_only_commands_it_probed",
    "test_the_toolchain_prescribes_nothing_when_it_cannot_probe",
})

# 🔴 ROUND 15. Base `5bad0a0c` — THIS BRANCH'S OWN HEAD when round 2's audit
# read it, not `origin/main`. That is the honest base for a fix round: every
# finding below is a defect this branch SHIPPED, so the tree that carries them
# is the tree to watch these five go red in.
#
# All five fail on an ASSERTION, not on an import or a missing symbol — checked
# deliberately, because `claude/RULES.md` says an arity/`AttributeError` red is
# not regression coverage. Two of them touch symbols this fix introduces
# (`_nix_strip`, `_python_test_probe`, `PY_TESTS_*`) and both were ORDERED so
# the wrong-answer assertions run FIRST: the checks-shape test fails on
# `genAttrs` answering `None`, and the capped-walk test fails on the brief
# saying nothing about a walk that stopped. The symbol-level rows sit after
# them, where at the base they are unreachable.
RED_AT_BASE_R15: frozenset[str] = frozenset({
    "test_the_flake_checks_probe_reads_nix_code_and_not_text_that_looks_like_it",
    "test_a_capped_python_test_walk_is_not_reported_as_no_python_tests",
    "test_the_wrong_shell_diagnosis_covers_every_command_not_only_pytest",
    "test_the_toolchain_head_claim_is_true_of_the_rendered_brief",
    "test_no_toolchain_note_claims_a_probe_the_brief_itself_denies",
    "test_an_unreadable_flake_is_not_reported_as_an_absent_one",
})

# 🔴 ROUND 16. Base `ba321c06` — round 15's OWN head, for the same reason round
# 15 used `5bad0a0c`: every finding here is a defect this branch shipped one
# round ago, so that is the tree to watch them go red in.
#
# All fail on an ASSERTION, not on an import or a missing symbol — checked,
# because `claude/RULES.md` says an arity/`AttributeError` red is not regression
# coverage. `_nix_scan` is NEW in round 16, so
# `test_the_probe_hedges_when_the_nix_scan_did_not_end_cleanly` would go red at
# `ba321c06` with `AttributeError` and is filed as an invariant guard instead.
#
# 🔴 `test_the_toolchain_head_claim_is_true_of_the_rendered_brief` IS LISTED
# UNDER TWO REFS, and that is a measurement rather than bookkeeping. At
# `5bad0a0c` it fails on round 15's false COUNT ("at most ONCE"); at `ba321c06`
# it fails on round 16's finding — the reworded clause is a claim about a STATE
# and the guard drove only `delta`, the one state it is true in. Same test, two
# different assertions, two different trees. It was NOT renamed: a rename would
# quietly retire the `5bad0a0c` row rather than add to it.
RED_AT_BASE_R16: frozenset[str] = frozenset({
    "test_a_nested_indented_string_inside_an_interpolation_is_not_a_terminator",
    "test_the_toolchain_head_claim_is_true_of_the_rendered_brief",
    "test_the_python_absence_bar_does_not_point_at_a_bar_that_is_not_there",
})

RED_AT_BASE_REFS: dict[str, frozenset[str]] = {
    "abc41024": RED_AT_BASE_R2,
    "d9eb36a8": RED_AT_BASE_R3,
    "e06461f7": RED_AT_BASE_R4,
    "dd601793": RED_AT_BASE_R5,
    "3619fe68": RED_AT_BASE_R6,
    "28492af2": RED_AT_BASE_R7,
    "706a6b38": RED_AT_BASE_R9,
    "6349a8b9": RED_AT_BASE_R13,
    "9e23c379": RED_AT_BASE_R14,
    "5bad0a0c": RED_AT_BASE_R15,
    "ba321c06": RED_AT_BASE_R16,
}
RED_AT_BASE: frozenset[str] = frozenset().union(*RED_AT_BASE_REFS.values())

INVARIANT_GUARDS_AND_LEDGERS = frozenset({
    # 🔴 An invariant guard, NOT regression coverage — `_flake_check_names`
    # does not exist at `9e23c379`, so its red there would be an
    # `AttributeError` about a missing symbol rather than a wrong answer. Its
    # evidence is its own pair of controls, both taken from real files: the
    # `checks = pr.get(...)` line out of `homelab-infra`'s devShell heredoc
    # (which the first regex here matched, answering "declares checks" for a
    # flake that declares none) and devrc's two-name `checks.${system}` block
    # (which an indentation-only scan reads as one name, because a build
    # script's lines start at column 0).
    "test_the_flake_checks_probe_reads_an_output_and_not_the_word",
    # 🔴 ROUND 16. Both would fail at `ba321c06` with `AttributeError` —
    # `_nix_scan` does not exist there — which is a claim about a symbol's
    # absence and not about any behaviour, the vacuous shape this module refuses
    # to count. Their evidence is their own controls (the clean/dirty pair, and
    # the code-survives-the-strip assertion beside the comment one) plus mutants
    # V53-V56.
    "test_the_probe_hedges_when_the_nix_scan_did_not_end_cleanly",
    # 🔴 GREEN at `ba321c06`, MEASURED, and that IS its finding: the `#` row in
    # `CHECKS_SHAPES_NOT_CODE` is rejected by the anchored regex alone, so it
    # answers `None` at every revision and tests nothing about the stripper it
    # is named for. This says so and pins the two `#` claims that are not
    # walkable by the anchor.
    "test_the_comment_stripper_is_reachable_in_its_own_right",
    # 🔴 GREEN at `5bad0a0c`, MEASURED — and that is exactly the finding. The
    # three probes it drives BEHAVE correctly there; what was missing was any
    # fixture that entered their branches, so `if tc.ci_suite: -> if True:`,
    # `if not tc.py_tests: return [] -> if False:` and `gate_tier=… -> True`
    # each SURVIVED a fully green suite while shipping a fabricated command.
    # Its evidence is therefore V42-V46, not a base ref: a fixture-reach fix
    # cannot be watched red anywhere, which is precisely why it goes unnoticed.
    "test_the_toolchain_probes_are_reachable_in_both_directions",
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
    # ------------------------------------------------------------------- #
    # Round 8's one guard, and it is GREEN at `28492af2` for a reason worth
    # keeping: the defect it closes was in this module's FIXTURE, not in the
    # script. `OTHER_REPO_PR` inherited `DEFAULT_PR`'s `headRefOid` and
    # `make_runner` defaults the assembly checkout's HEAD to the PR's own oid,
    # so a cross-repo brief could be rendered with the checkout modelled as
    # standing on a commit that exists only in the OTHER repository. Carrying
    # the FIXED fixture back to the base tree therefore passes — a base script
    # says nothing about a base fixture, the same shape as round 7's guard
    # above. Its evidence is mutant V9, which restores the impossible state
    # from the script side by claiming verification unconditionally.
    "test_no_brief_claims_a_verification_its_own_fixture_refutes",
    # There is no gap warning at `28492af2` at all, so a red there would be an
    # absence the fix adds. Same filing as Z5's legend. Mutant V16 removes it
    # again, and the test's own negative control (an ADJACENT block must stay
    # silent) is what stops the warning being satisfied by firing always.
    "test_a_claims_block_more_than_one_round_behind_is_warned_about",
    # ------------------------------------------------------------------- #
    # Round 10's two guards. Both WERE run against `706a6b38`; they are here
    # because of HOW they behave there.
    # ------------------------------------------------------------------- #
    # RED at `706a6b38` with `AttributeError: module 'audit_dispatch' has no
    # attribute 'TIP_PLACEHOLDER'` — an error for want of a name the fix
    # introduces, not the defect being observed, which this module refuses to
    # count as regression coverage. Mutant V21 (the two spellings of the
    # placeholder drift apart) is its evidence.
    "test_the_tip_placeholder_ledger_matches_the_script",
    # GREEN at `706a6b38`, and it must be: it is the NEGATIVE CONTROL for
    # `test_every_command_a_refusal_prescribes_actually_runs`, showing that
    # guard can see a remedy that genuinely fails. A control that went red at
    # the base would be a second sample of the thing in doubt.
    "test_control_the_prescription_extractor_can_see_a_broken_remedy",
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

# The one evidence token that is legitimately not a mutant: a guard over this
# module's own bookkeeping, which the battery cannot reach because it mutates
# the SCRIPT and never this file. Spelled as a sentinel rather than as prose so
# it cannot be confused with a column somebody forgot to fill in. Defined ABOVE
# the matrix because a row now names it.
IN_MODULE_CONTROL = "IN-MODULE CONTROL"

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

    # ------------------------------------------------------------------ #
    # 🔴 ROUND 7's FINDINGS, FIXED IN ROUND 8. Base `28492af2`.
    # ------------------------------------------------------------------ #
    ("r7/1  both prose guards were blind to a phrase wrapped across two `#` "
     "COMMENT lines, and to single/mixed-quote implicit concatenation, while "
     "both claimed to handle exactly that",
     "test_the_blind_spot_rationale_matches_what_the_script_actually_does",
     "RED@3619fe68", "V8, V10"),
    ("r7/2a cross-repo, the head check CANNOT pass, so THE RANGE diagnosed a "
     "structural impossibility as a checkout that had moved",
     "test_a_cross_repo_range_states_the_impossibility_not_a_moved_checkout",
     "RED@28492af2", "V11"),
    ("r7/2b and THE LEDGER printed COULD NOT MEASURE on EVERY cross-repo delta "
     "round — a permanently-red gate, with the ladder's own payload-attribution "
     "gate left uncomputed by anyone",
     "test_a_cross_repo_ledger_hands_the_attribution_gate_to_the_auditor",
     "RED@28492af2", "V12"),
    ("r7/2c the cross-repo fixture inherited the PR's `headRefOid`, so a "
     "cross-repo test would have passed in a physically impossible state",
     "test_no_brief_claims_a_verification_its_own_fixture_refutes",
     "GUARD", "V9"),
    ("r7/3  the round-7 tip fix was HALF-APPLIED: the DEGENERATE branch of the "
     "same if/elif still handed out `..HEAD`, under a banner claiming the "
     "range can never contain anything",
     "test_the_degenerate_range_names_shas_at_both_ends",
     "RED@28492af2", "V13"),
    ("r7/4  the forward reference named a WIDER set than the clause it "
     "describes, and the difference was the clone the brief's own recipe "
     "writes to",
     "test_the_no_write_forward_reference_states_the_clauses_own_scope",
     "RED@28492af2", "V14"),
    ("r7/5  THE LEDGER's provenance line was the last command in the brief "
     "whose `HEAD` resolves differently for the reader the brief invites to "
     "re-run it",
     "test_the_ledgers_provenance_line_names_the_sha_it_resolved_not_head",
     "RED@28492af2", "V15"),
    # Disclosed by round 7's auditor rather than filed as a finding, and taken
    # because it is the MIRROR of a warning that already shipped: a block from
    # a LATER round warned, a block four rounds EARLIER was silent.
    ("r7/6  a claims block several rounds behind was presented as this round's "
     "framing, silently — the existing warning covered only the other direction",
     "test_a_claims_block_more_than_one_round_behind_is_warned_about",
     "GUARD", "V16"),
    # 🔴 NOT ROUND 7's FINDING — round 8's own, found by sweeping for the
    # recurring shape. Filed under round 7's base because that is the tree it
    # was watched red at.
    ("r8/1  a block that PARSED was read as a block that yields an ANCHOR: "
     "`audited=..` rendered ``Diff `None..<sha>` `` at rc 0, under \"verified "
     "at assembly time\", with THE LEDGER giving the round-1 cause",
     "test_a_block_that_parses_but_yields_no_anchor_is_refused",
     "RED@28492af2", "V17"),
    # --------------------------------------------------------------------- #
    # Round 10. Its base is `706a6b38` — the round-9 tree.
    # --------------------------------------------------------------------- #
    ("r10/A the range and the ledger asserted the TIP was a sha without "
     "checking one was known: `Diff `28492af2..<the PR's head sha>`` at rc 0, "
     "and the same token inside the `git log` THE LEDGER calls mandatory",
     "test_a_placeholder_tip_is_never_handed_over_as_though_it_were_a_sha",
     "RED@706a6b38", "V18"),
    ("r10/A' the two spellings of that placeholder — `range_tip`'s and "
     "`--emit-claims`' — were open-coded literals with nothing comparing them",
     "test_the_tip_placeholder_ledger_matches_the_script",
     "GUARD", "V21"),
    ("r10/B THE LEDGER's cross-repo branch was not ordered after the head "
     "check, unlike THE RANGE's: for a renamed-remote clone it DISCARDED a "
     "successful measurement to print \"holds neither end of the range\"",
     "test_a_cross_repo_ledger_prints_a_measurement_the_head_check_vouched_for",
     "RED@706a6b38", "V19"),
    ("r10/C both refusals prescribed a `--emit-claims` re-run as their "
     "mechanical remedy and then refused it, byte for byte, with empty stdout",
     "test_every_command_a_refusal_prescribes_actually_runs",
     "RED@706a6b38", "V20 V23"),
    ("r10/C' the negative control for that guard: a remedy that genuinely "
     "fails must be reported, or the scan is wired to nothing",
     "test_control_the_prescription_extractor_can_see_a_broken_remedy",
     "GUARD", IN_MODULE_CONTROL),
    ("r10/D `own-worktree-is-writable` granted `fetch` and `checkout` in the "
     "CLONE — a tree the recipe names `<your local clone of owner/name>`, in "
     "practice a long-lived checkout other sessions are working in",
     "test_the_clone_grant_covers_only_the_write_the_recipe_makes",
     "RED@706a6b38", "V22"),
    ("r10/E the SEVENTH instance, found by sweeping: `base_ref` is "
     "`baseRefName or \"main\"` — a DEFAULT — and every site printing it stated "
     "it as a fact about the PR's repository, at rc 0",
     "test_no_brief_states_an_assumed_base_branch_as_a_fact",
     "RED@706a6b38", "V24"),
    # 🔴 ROUND 13, audited against the MERGED tree rather than a branch.
    ("r13/F1 the NINTH instance and the THIRD in one family: "
     "`repo_relation == \"unknown\"` read as \"`gh` was asked and had "
     "nothing\", so a `--claims-file` run — which consults no `gh` — rendered "
     "a brief contradicting itself in two sections, at rc 0",
     "test_no_brief_blames_gh_for_a_repo_gh_was_never_asked_about",
     "RED@6349a8b9", "V30 V31"),
    ("r13/F1' the same section prescribed `gh pr view <n> --json url` with no "
     "`--repo`, which in the auditor's own repo answers about THAT repo's PR "
     "of the same number at rc 0 — the hazard round 12 closed one site over",
     "test_no_gh_pr_view_this_script_prescribes_omits_repo",
     "RED@6349a8b9", "V32"),
    ("r13/F2 the cross-repo recipe and its catch-all could not both be "
     "obeyed: `worktree add <the PR's head branch>` needs a ref only a FETCH "
     "puts in the clone, and the grant refused every write but `worktree "
     "add`. Measured rc 128 on real repos, and certain for a fork PR",
     "test_the_cross_repo_recipe_can_actually_be_run",
     "RED@6349a8b9", "V33"),
    ("r13/F3 the prescription scanner was NARROWER THAN ITS OWN DOCSTRING: it "
     "matched `ast.Call` whose func is the NAME `print`, so a prescription "
     "via `err_stream.write` or via a module constant SURVIVED a green suite. "
     "Coverage was complete only by accident of both real sites being bare "
     "`print()`",
     "test_every_command_a_refusal_prescribes_actually_runs",
     "RED@706a6b38", "V20 V23 V29 V35"),
    ("r13/F4 the closed-verb-list probe was itself SPELLED — re-wording round "
     "10's enumeration left the whole suite green. Replaced by a verb-SET "
     "relationship read off the recipe, which no rewording moves",
     "test_the_clone_grant_covers_only_the_write_the_recipe_makes",
     "RED@706a6b38", "V22 V28 V34"),
    # --------------------------------------------------------------------- #
    # Round 14. Its base is `9e23c379` — `origin/main` with #1104 merged.
    # --------------------------------------------------------------------- #
    ("r14/1 every TOOLCHAIN command hardcoded devrc's own layout, so a brief "
     "for any other target prescribed scripts that do not exist there. "
     "Measured against `ZacxDev/homelab-infra` #530 from a real checkout: no "
     "`scripts/gate.sh`, no flake `checks` output, `.envrc` is `use flake` + "
     "`use opencode` and not the `use opencode` asserted, and `nix develop "
     "<root> -c python3 -m pytest` exits `No module named 'pytest'` — under a "
     "bar telling the auditor that error means the WRONG SHELL, not a broken "
     "suite. Four unrunnable prescriptions, one of them manufacturing the "
     "failure it then told the reader to disregard",
     "test_the_toolchain_prescribes_only_commands_it_probed",
     "RED@9e23c379", "V36 V38 V39"),
    ("r14/2 and the fix's own trap: cross-repo, the cwd IS a probeable "
     "repository, it is merely the WRONG one. Probing it yields a confident, "
     "fully-formed, entirely irrelevant command list — the same defect with a "
     "new source. Nothing detected must prescribe NOTHING, because a "
     "fabricated command costs a round and reads as a broken gate, while an "
     "absent one costs a lookup",
     "test_the_toolchain_prescribes_nothing_when_it_cannot_probe",
     "RED@9e23c379", "V40"),
    ("r14/3 the `checks` probe matched the WORD and not a flake OUTPUT: "
     "`checks = pr.get(\"statusCheckRollup\", [])`, inside a python heredoc in "
     "`homelab-infra`'s real devShell, answered \"this flake declares checks\" "
     "for a flake that declares none — which would have prescribed the `nix "
     "build` anyway and left the honest fallback unreachable. The mirror "
     "error: an indentation-only name scan reads devrc's two-name block as "
     "one, because a build script's lines start at column 0",
     "test_the_flake_checks_probe_reads_an_output_and_not_the_word",
     "GUARD", "V38 V41"),
    # --------------------------------------------------------------------- #
    # Round 15. Its base is `5bad0a0c` — THIS BRANCH's own head, because every
    # finding here is a defect this branch shipped.
    # --------------------------------------------------------------------- #
    ("r15/F1 round 14 lifted the cross-scenario pin off the COMMANDS bar and "
     "asserted in its own docstring that the property was 'not weakened — it "
     "became STRUCTURAL'. Measured false: a sentence keyed on the AUDITOR's "
     "worktree kind, contradicting WHERE TO WORK, survived at 115 passed three "
     "lines below where the same sentence was killed at `9e23c379`. The pin is "
     "restored WITHIN each declared target, which is the axis the probe reads",
     "test_the_toolchain_reason_is_true_in_every_scenario",
     "RED@dd601793", "V52"),
    ("r15/F2 `TOOLCHAIN_HEAD` said the assembly checkout appears 'at most "
     "ONCE, as the argument to `nix develop`'. FOUR occurrences in the devrc "
     "rendering, two of them prose. #1104's 'ONLY as the argument' was true; "
     "this branch strengthened it into a count and broke it, and the guard "
     "over that block asks whether the text IS the constant, never whether the "
     "constant is TRUE",
     "test_the_toolchain_head_claim_is_true_of_the_rendered_brief",
     "RED@5bad0a0c", "-"),
    ("r15/F3 `TOOLCHAIN_TAIL` froze a STATE-dependent claim ('everything above "
     "was PROBED out of this repository') into a state-INDEPENDENT constant, "
     "so the cross-repo brief contradicted itself three lines apart — and the "
     "scenario guard CERTIFIED it by requiring the tail verbatim everywhere",
     "test_no_toolchain_note_claims_a_probe_the_brief_itself_denies",
     "RED@5bad0a0c", "-"),
    ("r15/F4 the battery states 'each probe is broken in BOTH directions where "
     "both are reachable' and three probes had no mutant at all: `ci_suite`, "
     "`py_tests` and `gate_tier`. All three inversions SURVIVED a green "
     "115-test suite while shipping a fabricated command — `--tier both` on "
     "any repo's gate.sh being the exact fabrication that field exists to "
     "prevent. The cause was FIXTURE REACH: `_write_repo(py_test=False)` was "
     "called by nothing, no fixture had a gate without `--tier`, and no test "
     "asked whether `run-ci-suite.sh` was ABSENT",
     "test_the_toolchain_probes_are_reachable_in_both_directions",
     "GUARD", "V45 V47 V49"),
    ("r15/F5+F6+F7 the checks parser, one root cause and three failure modes: "
     "`''${…}`/`'''` read as string TERMINATORS turned devrc's own flake into "
     "['pytests', 'rc', 'rc'] — two invented `nix build` targets and a real "
     "merge-gating check dropped; the pattern ran over RAW text, so a heredoc "
     "or a comment holding `checks = {` fabricated a tier; and it accepted "
     "three shapes only, answering the CONFIDENT `None` ('no sandbox tier', in "
     "bold) for genAttrs, flake-parts `perSystem`, `checks.<system>.default` "
     "and `checks.<system> = base // {…}`",
     "test_the_flake_checks_probe_reads_nix_code_and_not_text_that_looks_like_it",
     "RED@5bad0a0c", "V38 V41 V42 V43"),
    ("r15/F8 the 400-directory walk cap failed SILENTLY, alone among the "
     "probes here: it answered False and the whole python bar vanished with no "
     "note, while both siblings emit an explicit absence bar. Reachable on a "
     "real repo — `~/workspace/civit`, 157,319 dirs at depth ≤ 4, first "
     "`test_*.py` at visit 1,185 — and `os.walk` order decides which side of "
     "the cap a monorepo lands on",
     "test_a_capped_python_test_walk_is_not_reported_as_no_python_tests",
     "RED@5bad0a0c", "V44 V49"),
    ("r15/nit a `flake.nix` the probe could not OPEN rendered `has no "
     "flake.nix` — a fact about the repository derived from a failed read, and "
     "the same class as the walk cap. `_read_probe` answers None for absent "
     "and for unreadable alike; the wording was the whole over-claim",
     "test_an_unreadable_flake_is_not_reported_as_an_absent_one",
     "RED@5bad0a0c", "-"),
    ("r15/F9 one PYTHON-specific probe (`'pytest' in flake.nix`) decided the "
     "shell for the language-agnostic gate, and the wrong-shell bar lived "
     "inside the pytest branch — so a repo with a real gate, a nodejs devShell "
     "and no python tests got a BARE gate and no diagnosis anywhere. `node: "
     "command not found` then reads exactly like a broken gate",
     "test_the_wrong_shell_diagnosis_covers_every_command_not_only_pytest",
     "RED@5bad0a0c", "V50 V51"),
    # --------------------------------------------------------------------- #
    # Round 16. Base `ba321c06`. Every row is one of round 15's own fixes with
    # the CLASS still open one shape further out — which is the pattern worth
    # naming: each previous round closed the instances it was shown.
    # --------------------------------------------------------------------- #
    ("r16/N1 the `''` lex needed a STACK, and round 15 gave it a flag. A `''` "
     "that OPENS a nested string inside `${…}` was read as the OUTER string's "
     "terminator, so `shellHook = '' ${lib.optionalString c '' … ''} '';` — an "
     "ordinary nixpkgs idiom — promoted the interpolation body to CODE. Both "
     "directions measured at `ba321c06`: a flake with NO checks output answered "
     "['unit'] (a fenced `nix build` at a non-existent attribute), and one that "
     "DOES declare checks answered None once the promoted region inverted "
     "string parity. `foo'' = 1;`, a legal nix identifier, did the same",
     "test_a_nested_indented_string_inside_an_interpolation_is_not_a_terminator",
     "RED@ba321c06", "V53 V54"),
    ("r16/N1b where the lexer cannot be sure the answer is now the HEDGE. Both "
     "of N1's failures routed to a CONFIDENT answer — a fabricated name list "
     "one way, a bolded 'no `checks` output' the other — so `_nix_scan` reports "
     "whether it ended in a state a nix file can be in and the caller degrades "
     "to `[]` above the parse, not inside it",
     "test_the_probe_hedges_when_the_nix_scan_did_not_end_cleanly",
     "GUARD", "V55"),
    ("r16/nit the `#`-commented row in CHECKS_SHAPES_NOT_CODE is INERT: both "
     "patterns are anchored `^[ \\t]*checks`, so it answers None at every "
     "revision, stripper or no stripper, and reads as coverage of the comment "
     "branch while providing none",
     "test_the_comment_stripper_is_reachable_in_its_own_right",
     "GUARD", "V56"),
    ("r16/N2 round 15's non-degeneracy pin was `any(len(kinds) >= 2)`, which "
     "one partition satisfied alone. Measured at `ba321c06`: cross-repo spanned "
     "{private, shared} and repo-unknown {shared} — no NOT-PROBED scenario was "
     "`unknown` kind at all — so a WHERE-TO-WORK-contradicting sentence keyed "
     "on `worktree.kind == 'unknown'` in `_toolchain_not_probed` survived at "
     "122 passed while the same sentence keyed on 'private' was killed. The "
     "grid is now complete and the assertion asks for the whole row",
     "test_the_toolchain_reason_is_true_in_every_scenario",
     "RED@dd601793", "V52 V57 V58"),
    ("r16/N3 the F2 reword put a state-dependent claim straight back into the "
     "state-INDEPENDENT constant — 'the prose names it too, to say what was "
     "read out of it', measured true in 6 scenarios, false in 5, flatly false "
     "in 2 — and its guard drove `delta` alone, the one state it holds in. Both "
     "halves fixed: the clause is the widest thing true everywhere, the guard "
     "drives every scenario, and the constant is pinned WHOLE because two "
     "rewordings in a row each replaced a false claim with another one",
     "test_the_toolchain_head_claim_is_true_of_the_rendered_brief",
     "RED@ba321c06", "V59"),
    ("r16/N4 two dangling references in one bar: the completed-walk note "
     "contrasted itself with 'the one above' when the branches are mutually "
     "exclusive, and the wrong-shell bar opened with a `python3 -m pytest` "
     "diagnosis three lines after the section said no python command is "
     "prescribed. Fixed as the general rule — the python bar is emitted only "
     "where a python command is fenced",
     "test_the_python_absence_bar_does_not_point_at_a_bar_that_is_not_there",
     "RED@ba321c06", "V60 V61"),
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
# Round 8 added nine: m = 70, and 70 - max(1, 70 // 20) = 67. Counted the same
# way, by importing the module and printing `len(FIX_MATRIX)`. Round 10 added
# seven: m = 77, and 77 - max(1, 77 // 20) = 74. Counted the same way again —
# `len(FIX_MATRIX)` printed from an import, not seven added to the last sentence.
# Round 15: m = 93 (printed from an import, NOT derived by adding this round's
# rows to 77 — rounds 11-14 also added some), and 93 - max(1, 93 // 20) = 89.
# Round 16: m = 99 (printed from an import again, NOT 93 plus this round's six),
# and 99 - min(50, max(1, 99 // 20)) = 99 - 4 = 95.
MIN_FIX_MATRIX_ROWS = 95

# 🔴 THE MUTANTS COLUMN IS AN EVIDENCE CLAIM, AND IT WAS UNGRADED.
# `fix_matrix_problems` took `_mutants` and threw it away, so rewriting a
# GUARD row's column to `"Z99 — a mutant that does not exist"` left the suite
# fully green — while for a GUARD row that column IS the whole evidence, the
# base-ref cell having been set to `GUARD` precisely because there is no base
# ref to point at. It is now resolved against the harness's OWN `ROWS`.
MUTANT_HARNESS = REPO / "scripts" / "tests" / "mutants-audit-dispatch.py"


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

    🔴 NORMALISED FOUR DRAFTS DEEP, AND THE FIRST THREE WERE NOT ENOUGH — each
    MISSED an occurrence it was written for, and each reported the module CLEAN
    with the premise sitting two lines away. The first two were measured
    against `3619fe68`:

      * a raw `str.count` scored **0** for both phrases. They live in WRAPPED
        prose, so the source reads `where a dispatched auditor\\n    usually
        stands`. Fixed by collapsing whitespace — the same normalisation the
        script uses in `missing_clauses`, for the same reason.
      * whitespace alone still scored **0** for entry 1, because that one
        spans a Python IMPLICIT CONCATENATION — the phrase is cut in half by a
        closing quote, a newline and an opening quote, and collapsing
        whitespace leaves the two quote characters sitting in the middle of
        it. So adjacent literals had to be joined first.

    The third — round 7's, the one this replaces — collapsed whitespace over a
    `re.sub(r'"\\s*"', "", …)`, and scored **0** for a phrase wrapped across two
    `#` COMMENT lines (the `#` is left sitting mid-phrase) and **0** for the
    single- and mixed-quote spellings of the concatenation it claimed to join
    "exactly as the compiler joins them". Measured at `28492af2`; the shape
    table is beside `flat_prose`, which now does the reading.

    That is the "guard whose description is wider than its implementation"
    failure, three times over, in the guard written to catch a claim wider than
    its code.
    """
    flat = flat_prose(text)
    counts = [(p, flat.count(collapse_ws(p))) for p, _ in REFUTED_PREMISES]
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
    #
    # 🔴 ROUND 8 — ONE PLANT PER SHAPE, NOT ONE PLANT. Round 7's control planted
    # a single-line `#` comment and nothing else, which is a shape its
    # normaliser could already see; the two shapes it could NOT see went
    # unexercised, so a control that passed said nothing about them. A control
    # only covers the shapes it plants, and the shapes this ladder actually
    # writes its prose in are the wrapped comment block and the concatenated
    # literal.
    phrase = REFUTED_PREMISES[0][0]
    assert not set(phrase) & set("\"'\\"), (
        f"the plants below embed {phrase!r} in GENERATED Python source, so a "
        "quote or a backslash in it would raise SyntaxError instead of "
        "testing anything. Pick a quote-free ledger entry for this control."
    )
    words = phrase.split()
    head, tail = " ".join(words[:3]), " ".join(words[3:])
    assert head and tail, f"{phrase!r} does not split into two wrappable halves"
    for shape, payload in {
        "one `#` line": f"\n# {phrase}\n",
        "two `#` lines": f"\n# {head}\n# {tail}\n",
        "wrapped docstring": f'\ndef _plant():\n    """{head}\n    {tail}"""\n',
        'concat "…" "…"': f'\n_plant = ("{head} "\n          "{tail}")\n',
        "concat '…' '…'": f"\n_plant = ('{head} '\n          '{tail}')\n",
        "concat \"…\" '…'": f'\n_plant = ("{head} "\n' + f"          '{tail}')\n",
    }.items():
        assert surviving_refuted_premises(_THIS_MODULE_SOURCE + payload), (
            f"POSITIVE CONTROL ({shape}): the scan cannot see a planted second "
            f"copy of {phrase!r} in that shape, so a clean result below says "
            "nothing about prose written that way — and this ladder writes its "
            "narrative in wrapped comment blocks."
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
