#!/usr/bin/env python3
"""Assemble an adversarial-audit brief for a PR — the INVARIANT half as CODE.

    scripts/audit-dispatch.py <pr-number> [--round N] [--repo owner/name]
    scripts/audit-dispatch.py <pr-number> --round 3 --emit-claims
    scripts/audit-dispatch.py <pr-number> --round 1 --emit-claims --audited <sha>

It PRINTS a brief to stdout for pasting into an `Agent` dispatch. It dispatches
nothing, merges nothing and writes nothing to the repository under audit.

WHY IT EXISTS — the measurement
-------------------------------
`claude/skills/audit-pr/SKILL.md` tells an operator to "dispatch a subagent …
against this checklist" and supplies no procedure, so the brief is reassembled
from prose every time. Measured over one session that ran 14 audit dispatches:
**60,100 characters** of hand-written brief, mean 4,292 each; **42%** mean
similarity between consecutive briefs; **zero** lines longer than 25 chars
identical across all 14.

Hard-won clauses ACCRETED rather than being present from the start, and three
were LOST after being learned:

    instruction                      present in dispatch #
                                     1  2  3  4  5  6  7  8  9 10 11 12 13 14
    do NOT git fetch            5/14 ·  ·  ·  ·  ·  ·  ·  ·  ✓  ✓  ·  ✓  ✓  ✓
    shared checkout warning     9/14 ·  ·  ·  ·  ·  ✓  ✓  ✓  ✓  ✓  ✓  ✓  ✓  ✓
    'ending it is CORRECT'      6/14 ·  ·  ·  ·  ·  ·  ·  ✓  ✓  ✓  ·  ✓  ✓  ✓
    'a nit is not a finding'    9/14 ·  ·  ·  ·  ✓  ✓  ✓  ✓  ✓  ✓  ·  ✓  ✓  ✓
    bare pytest = wrong shell  10/14 ✓  ✓  ✓  ✓  ✓  ✓  ✓  ✓  ✓  ·  ✓  ·  ·  ·
    payload/scaffolding label  10/14 ·  ·  ✓  ✓  ✓  ✓  ✓  ✓  ✓  ✓  ·  ·  ✓  ✓
    sandbox tier named         11/14 ·  ·  ✓  ✓  ✓  ✓  ✓  ✓  ✓  ·  ✓  ✓  ✓  ✓

Consequences that actually happened: the auditor at dispatch 8 ran `git fetch`
in a repo the brief called read-only (the clause first appears at 9); the first
five auditors reported the shared checkout moving as if it might be their fault;
the seven rounds before dispatch 8 ran under a stop rule that never said
stopping was the correct outcome.

So `INVARIANT_CLAUSES` below is the SECTION THAT CANNOT BE FORGOTTEN. It is one
module-level list, rendered verbatim into every brief this script emits, and
pinned two-way by `scripts/tests/test_audit_dispatch.py` against that module's
own independent ledger — so deleting a clause here goes red there, and a bullet
appearing in the rendered section with no clause behind it goes red too.

🔴 THE FRAMING CONSTRAINT — WHY PROSE IS NEVER PARSED
-----------------------------------------------------
A delta round must be framed on WHAT WAS CLAIMED FIXED, never on WHY IT IS
CORRECT. The skill records that three successive FRAMED audits confirmed a claim
purely because the prompt handed them the answer, while one BLIND audit refuted
it in a single pass.

A PR comment contains both — the claims and the reasoning that argues for them.
So this script reads ONLY the fenced block:

    ```audit-claims round=3 audited=997375ec..9f638fd4
    1. run_move collapsed WRONG-KILLER into NOT EXCLUSIVE — now two branches
    2. the "97% / most" quantifier over-stated what the measurement supports
    ```

and reproduces only its numbered lines. Everything else in the comment —
including the paragraph directly under the fence explaining why each fix is
right — is dropped on the floor. `--emit-claims` prints a correctly-formed
skeleton for the operator to paste into the round's PR comment, so the NEXT
round has something to read.

🔴 `audited=<from>..<to>` HAS EXACTLY ONE MEANING, AND TWO READERS
------------------------------------------------------------------
    `<from>` is the tip that round's AUDIT read.
    `<to>`   is the head that round's FIXES produced.

It meant something different to the writer and the reader for two rounds, and
the range that fell out was EMPTY BY CONSTRUCTION: `--emit-claims` stamps the
PR's CURRENT head into `<to>`, while the next round diffed `<to>..HEAD` — and
those coincide, because the block is posted at the fix tip. Reproduced live on
devrc #958 (`audited=abc41024..d9eb36a8`, and `--round 3` rendered
``Diff `d9eb36a8..HEAD` `` with HEAD *being* `d9eb36a8`) and hermetically.

So there are two named readers of the one field, and they return DIFFERENT shas:

  * `range_anchor` = `<from>` or, for a bare round-1 `audited=<sha>`, `<to>`.
    What a delta round DIFFS FROM — everything since the previously-audited tip.
  * `emit_anchor`  = `<to>`. What `--emit-claims` WRITES as the next block's
    `<from>`, because the tip THIS round audited is where the last one stopped.

`anchor_is_head` is the third piece: a `<sha>..HEAD` whose `<sha>` IS HEAD is a
broken question, not a clean round, and the range section, the ledger and
`--emit-claims` each say so LOUDLY rather than rendering an empty diff — an
auditor who diffs nothing finds nothing, and the `stop-rule` clause then
converts that into "the ladder ENDS".

🔴 THREE REFUSALS, AND THEY ARE NOT THE SAME KIND
--------------------------------------------------
1. **`--round N` for N ≥ 2 with no parseable claims block REFUSES to emit**
   (exit 2), naming what it looked for and where. An empty "what was claimed
   fixed" section silently turns a delta re-audit into a blind full audit — a
   different thing, which would then read as covered. Same shape as
   `scripts/ladder-depth-sweep.py`'s refused zero: the failure and the
   legitimate case produce the SAME observable, so neither may be reported.
2. **A brief missing an invariant clause WARNS and never blocks.** That check
   exists for a hand-edited brief, and `claude/RULES.md` is explicit that a
   permanently-red gate trains everyone to click through it. Warn-only is the
   right severity for a channel whose failure is cosmetic and whose false
   positive rate is set by whatever a human typed. It runs over `--check FILE`
   and over the READ-BACK of `--out` — never over the in-memory string, where
   it was unreachable by construction and could not have fired for any input.
3. **`--emit-claims` REFUSES (exit 4) to print a block whose `<from>` this
   script's OWN parser reads back as something else**, and refuses an empty
   `--audited` outright. The flag used to accept any string: `abc 123` emitted
   ``audited=abc 123..<head>``, which parses back as `from=''`, `to='abc'`, so
   the next round diffed `abc..HEAD`; `e06461f7..dd601793` corrupted `<to>`
   instead and cascaded into the round after that. A refusal and NOT a warning,
   because the failure is silent at every downstream station — nothing reports
   the block malformed, and the self-range guard cannot fire on it. Whitespace
   is refused on the INPUT rather than by that round trip, which is
   line-oriented on both sides and therefore blind to a NEWLINE — see the check
   in `main`. 🔴 A well-formed token that is not a commit round-trips and is
   NOT caught. THE REASON IS NOT THAT THIS SCRIPT CANNOT RESOLVE A SHA — it
   runs seven read-only `git -C <that checkout>` commands, two of which
   (`rev-list --count <anchor>..HEAD`, `log --numstat … <anchor>..HEAD`)
   resolve exactly that token in exactly that checkout. The reason is that this
   script deliberately never FETCHES, so the assembly checkout may legitimately
   lack an object that is perfectly fine on the PR, and a `cat-file` refusal
   there would be a FALSE POSITIVE on a correct value. The downstream cost is
   bounded and correctly attributed: `rev-list` exits 128 and the ledger prints
   COULD NOT MEASURE naming the command, one round later.

🔴 EVERY NUMBER HERE IS ABOUT THE PR, NOT ABOUT YOUR CHECKOUT
-------------------------------------------------------------
The delta half of a brief — the ledger, the `<prev>..HEAD` range, the `audited=`
sha `--emit-claims` writes for the NEXT round to anchor on — was resolved
against `HEAD` in the operator's own SHARED checkout, with nothing checking that
HEAD was the PR at all. From a clone standing on an unrelated branch that
produced rc 0, silent stderr, a non-empty range and a confident ledger of the
wrong branch's files; from `main` it produced the banner "🔴 Zero changed lines
over a NON-EMPTY range. That is a real measurement, not a failure."

So `git rev-parse HEAD == headRefOid` is a FOURTH read rule beside rc 0, silent
stderr and a non-empty range, and all three consumers emit COULD NOT MEASURE
naming that cause when it does not hold.

🔴 WHAT THIS SCRIPT DOES NOT DO
--------------------------------
* **It does not classify payload vs scaffolding.** The skill says that is a
  human judgement, and records that a pathspec is wrong in BOTH directions on
  ordinary names (`':!*test*'` swallows `attestation/`, `latest/`,
  `inspector/`; it keeps `FooTest.java` and `login.cy.ts`). So the ledger prints
  the changed-file list and leaves X blank for a human.
* **It does not `git fetch`.** The brief it writes forbids the auditor from
  writing to the shared checkout; doing it here would be the same write. `<base>`
  is therefore only as current as the operator's last fetch, and the brief SAYS
  SO rather than quoting a number it cannot vouch for.
* **It does not dispatch, comment, push or merge.**
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections import namedtuple
from datetime import datetime, timezone
from pathlib import Path

# --------------------------------------------------------------------------- #
# THE INVARIANT CLAUSES — the whole point of the script.
# --------------------------------------------------------------------------- #
# 🔴 ONE list, rendered verbatim into every brief. Each entry is a single line
# (no embedded newlines) so the rendered section is trivially parseable as
# bullets, which is what makes the two-way pin in the test module mechanical
# rather than a prose comparison.
#
# `id` is the stable handle the test module's independent ledger names. Renaming
# an id is a deliberate act that fails that ledger; rewording `text` is not
# caught here (see the test module's "what this does NOT enforce" note).

Clause = namedtuple("Clause", "id text")

INVARIANT_CLAUSES = (
    Clause(
        "read-only",
        "**READ-ONLY — you modify nothing in the repository under audit.** If "
        "you must mutate something to test a theory, do it in a `cp -a` copy "
        "and run `rm -f <copy>/.git` FIRST: a worktree's `.git` is a FILE "
        "pointing at the real git dir, so a commit inside the copy lands on the "
        "branch you are auditing.",
    ),
    # 🔴 UNCONDITIONAL, and round 5 put it back that way. Round 4 reworded this
    # to fire only "to a SHARED checkout — THE CHECKOUT section … says whether
    # the one it names is shared", which armed it on a STATE this script cannot
    # know: `gather_worktree_kind` measures the tree the ASSEMBLER stood in, and
    # in the production configuration it answers `private`, so the prohibition
    # switched itself off over a tree that belongs to the dispatching session.
    # The rule that is true in every state is the one about OWNERSHIP: write
    # only to the copy you made.
    #
    # 🔴 ROUND 8 — "FOR THIS AUDIT" WAS A SECOND, SMALLER NARROWING, AND IT
    # FORBADE THE BRIEF'S OWN RECIPE. Round 5 moved this clause off SHAREDNESS
    # onto ownership and round 7 rewrote the sentence that forward-references it
    # to say "every checkout you did not make" — a DIFFERENT set, and the
    # difference lands exactly on the tree WHERE TO WORK tells a cross-repo
    # auditor to write to: `git -C <your local clone of owner/name> worktree
    # add …`. That clone is one they made, but not one they made FOR THIS
    # AUDIT, so the clause forbade the operation the brief had just prescribed
    # two lines earlier while the forward reference allowed it. Round 6's
    # finding was a forward reference that narrowed the rule it names; round 7's
    # fix replaced one narrowing with another on a different axis.
    #
    # So both sides now spell ONE set — `NO_WRITE_SCOPE` in the test module
    # pins that phrase in the clause AND in every forward reference, so a
    # reword on either side alone fails rather than re-opening the gap.
    Clause(
        "no-fetch",
        "**Do NOT `git fetch`, `pull`, `checkout` or otherwise write to any "
        "checkout you did not make — including the one THE CHECKOUT section "
        "names, which is where this brief was assembled and is not yours.** "
        "Other sessions are in those trees; a fetch there is a write with "
        "cross-session blast radius, and every ref you need is already "
        "resolved for you here.",
    ),
    Clause(
        "stop-rule",
        "**A clean round ENDS the ladder — ending it is the CORRECT outcome, "
        "not a failure.** Rounds continue only while the previous round "
        "produced a finding that needed fixing. Do not manufacture findings to "
        "justify the round, and do not run another round to confirm a clean "
        "one.",
    ),
    Clause(
        "nit-is-not-a-finding",
        "**A nit that changes nothing a reader does is NOT a finding.** If the "
        "fix would be a reword with no behavioural, decision or "
        "correctness consequence, leave it out of the findings and say so in "
        "one line under the verdict instead.",
    ),
    Clause(
        "reverify-self-reported",
        "**Re-verify the fix commit's own self-reported numbers rather than "
        "accepting them.** Counts, byte sizes, mutation-sweep results and "
        "\"watched red at <sha>\" claims in a commit message or PR body are "
        "claims to check against the tree, never evidence.",
    ),
    Clause(
        "finding-format",
        "**Report each finding with `file:line`, a concrete failure scenario "
        "(the input, the path taken, the wrong output) and a `payload` or "
        "`scaffolding` label** — payload is what the PR exists to ship; "
        "scaffolding is the tests, fixtures and notes a round wrote to guard "
        "it.",
    ),
    Clause(
        "do-not-merge",
        "**Do not merge — report only.** No pushes, no PR comments, no "
        "`gh pr merge`. Hand the findings back and let the operator act on "
        "them.",
    ),
)

INVARIANTS_HEADING = "## 🔴 NON-NEGOTIABLE — every audit, every round"

# --------------------------------------------------------------------------- #
# THE SECTION DIRECTIVES — verbatim instruction prose that is NOT a clause.
# --------------------------------------------------------------------------- #
# 🔴 WHOLE-STRING PINNING COVERED THE INVARIANT CLAUSES AND NOTHING ELSE, and
# three inversions walked straight through a fully green 58-test suite. Round 2
# made whole-string pinning the standard for auditor instructions and then
# applied it to `INVARIANT_CLAUSES` alone; these three blocks are verbatim,
# non-generated instruction prose that shipped in every delta brief with
# nothing watching:
#
#   site             mutation applied alone                       result
#   render_claims    keep "never WHY IT IS CORRECT", rewrite the  58 passed
#                    sentence to "the fix round already verified
#                    each of these, so take them as established"
#   render_range     delete the whole "Also hunt for regressions" 58 passed
#   render_checkout  invert "NOT a finding … do not chase it"     58 passed
#                    into "a finding worth reporting. Restore
#                    anything you see move."
#
# The first inverts this module's own headline 🔴 rule into the exact framed
# audit it documents; the third tells the auditor to WRITE to the shared
# checkout, contradicting the `no-fetch` clause two bars later in the same
# brief. Same shape as W1-W5: the pinned fragment survives, the instruction
# around it says the opposite.
#
# They are NOT invariant clauses — an invariant clause renders in every brief,
# and two of these are meaningless in a round-1 one ("this fix round" when
# there has been no fix round). So they stay in the sections that own them and
# get their own ledger, pinned WHOLE and two-way by the test module exactly as
# `INVARIANT_CLAUSES` is.
#
# 🔴 WHAT IS PINNED, AND WHAT IS NOT — stated as a RULE, because the previous
# version of this comment stated a census and the census was both incomplete
# and arithmetically inconsistent with itself ("WHOLE-STRING PINNING COVERED 7
# OF 11 VERBATIM BLOCKS" over an enumeration of sixteen). Worse, it asserted
# that every unpinned block "is guarded by a FRAGMENT assertion aimed at one
# specific hazard" — a coverage claim that was false for three of the six it
# listed, which is the failure mode `claude/RULES.md` calls reading as coverage
# while providing none.
#
# THE RULE: exactly two things in this file are pinned WHOLE and two-way —
# `INVARIANT_CLAUSES` and `SECTION_DIRECTIVES`. **Count them in the ledgers**
# (`CLAUSE_LEDGER` and `DIRECTIVE_LEDGER` in the test module); no number is
# restated here, because the last number here was wrong within one round.
# EVERYTHING ELSE the brief prints is unpinned — some of it fragment-guarded,
# some of it not guarded at all — and that set is NOT enumerable in a comment:
# it is every sentence of every renderer, and any census of it rots on the next
# edit.
#
# 🔴 THE MEASURED INVERSIONS, which is a different and checkable claim. Each
# was applied ALONE and the result recorded. This list is closed only over what
# has been MEASURED; it is not a claim that nothing else is invertible.
#
# 🔴 TWO SCOPES, NAMED, because they are not the same measurement. Y1 and Y2
# were re-measured HERE rather than restated from the audit that found them:
# each inversion applied alone to `e06461f7`'s script, with `e06461f7`'s test
# module, leaves that module's own **72 passed** fully green — a claim about
# THIS module, not about the ~199 audit-related tests the round-4 audit ran,
# which were not re-run for this line. Y3-Y5 are measured continuously instead,
# by their `HOLE` rows in the mutation harness, against the CURRENT tree.
#
#   Y1  the delta-scope sentence "Do not re-audit the whole PR"   -> green
#       inverted to "Re-audit the whole PR as well". It defines the round's
#       SCOPE.                              NOW PINNED as `delta-scope`.
#   Y2  the cross-repo "That worktree is YOURS: fetching … is     -> green
#       fine" inverted to "do not fetch or check out inside it",
#       leaving a cross-repo auditor nowhere to work.
#                                           NOW PINNED as `own-worktree-is-
#                                           writable`.
#   Y3  the OUTPUT per-finding format softened to "a one-paragraph
#       summary is enough".                 -> green. STILL UNPINNED, and
#       NOTHING asserts it. Named here so it does not read as covered.
#   Y4  the toolchain's "Name the tier and the base sha" inverted
#       to "there is no need to".           -> green. STILL UNPINNED, and
#       NOTHING asserts it.
#   Y5  the round-1 "read the whole PR diff" replaced with "the PR
#       description is the fastest way in". -> green. STILL UNPINNED, and
#       NOTHING asserts it.
#
# Y3-Y5 carry rows in `scripts/tests/mutants-audit-dispatch.py` marked as
# DOCUMENTED HOLES rather than controls, so the gap is re-derivable and a
# future guard that closes one turns that row red and forces this comment to be
# updated. They are not pinned because pinning every sentence makes the brief
# unrewordable; that is a judgement, and it is written down as one rather than
# dressed up as coverage.

Directive = namedtuple("Directive", "id text")

SECTION_DIRECTIVES = (
    Directive(
        "claims-framing",
        "🔴 This is WHAT WAS CLAIMED, never WHY IT IS CORRECT — nothing here is "
        "established. Three successive FRAMED audits confirmed a claim purely "
        "because the prompt handed them the answer; one BLIND audit refuted it "
        "in a single pass. Verify each item against the diff and state, per "
        "item: **actually fixed / partially / not / made worse**.",
    ),
    # 🔴 Y1 — THE SENTENCE THAT DEFINES THE DELTA SCOPE, and it shipped
    # unpinned. Inverting "Do not re-audit the whole PR" into "Re-audit the
    # whole PR as well" passed all 199 audit-related tests. It was inside a
    # generated f-string, which is why no whole-string pin could reach it; the
    # generated line now states the RANGE and this states the INSTRUCTION.
    Directive(
        "delta-scope",
        "**Audit ONLY that range — do NOT re-audit the whole PR.** Everything "
        "below the range is work a previous round already dispositioned; "
        "re-reading it re-reports findings that were answered, buries the "
        "delta this round exists to examine, and makes the round's cost "
        "indistinguishable from a first full audit.",
    ),
    Directive(
        "delta-regressions",
        "Also hunt for **regressions this fix round itself introduced** — the "
        "guard that is now too strict, the branch that is now unreachable, the "
        "narrowed check that now rejects a legitimate case, the rule reworded "
        "wider on one axis and narrower on another.",
    ),
    # 🔴 ONE WRITER FOR THE DEGENERATE-RANGE CAUSES, because there are TWO
    # readers (the ledger's COULD NOT MEASURE and THE RANGE's banner) and both
    # shipped the SAME false cause: "the round's fix commits are not in this
    # checkout". `anchor_is_head` returns True only when `head_check.ok`, i.e.
    # only when this checkout has been VERIFIED to be the PR's head commit — so
    # the predicate that reaches the branch refutes the cause the branch offers,
    # and the operator is sent to fetch commits into a checkout that already has
    # them, which the `no-fetch` clause two bars later forbids.
    #
    # Identical shape to the empty-range reason and to `verify_head_is_the_pr`'s
    # `no_sha_reason`, whose commit message said the fix went "to the CLASS
    # rather than to one site" — and which then missed the two sites that same
    # commit was writing. A sweep that does not read the diff it is part of is
    # not a sweep.
    # 🔴 ROUND 5 SCOPED THE FIRST CAUSE TO ROUND 1. It read "`--emit-claims`
    # records [the fix tip] when `--audited` is omitted", flat — but from round
    # 2 on, omitting the flag is CORRECT: `emit_from` falls back to
    # `emit_anchor(newest)`, which is the previous block's `<to>`, i.e. the tip
    # this round read. An operator hitting this banner on a round-3 brief was
    # told their emit was wrong and pushed toward hand-typing a value into
    # `--audited`, which is how a placeholder reached the header.
    Directive(
        "degenerate-range-causes",
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
        "the `no-fetch` clause forbids it anyway.",
    ),
    # 🔴 ROUND 5 added the no-write sentence here. It was the ONLY one of the
    # three checkout states that did not carry it — the `no-fetch` clause was
    # doing the work from another section — which is what let round 4 reword
    # that clause into a conditional one and leave the private state with a
    # write GRANT and no rule anywhere. All three states now state it, and the
    # test module pins that as a RELATIONSHIP over the `checkout-*` set rather
    # than as a phrase in one directive.
    Directive(
        "checkout-moves",
        "🔴 **This checkout is SHARED with other sessions and agents. It MOVES "
        "UNDER YOU** — the branch can change, files can appear and vanish, and "
        "commits can land mid-audit. That is expected and is NOT your fault and "
        "NOT a finding. **Write nothing to it**, and report what you observed "
        "moving and carry on; do not chase it, and do not try to restore it.",
    ),
    # 🔴 THE OTHER TWO THIRDS OF THE CHECKOUT DECISION. `checkout-moves` used to
    # be printed unconditionally under a heading reading THE SHARED CHECKOUT,
    # naming whatever `--show-toplevel` returned — INCLUDING a private
    # per-agent worktree under `.claude/worktrees/agent-…`. Every one of its
    # four claims is false there (shared / moves under you / expected / NOT a
    # finding), and the DIRECTION is what makes it consequential: an auditor in
    # a private worktree who watches files move is told it is expected and not
    # a finding, which suppresses exactly the sibling-agent clobber
    # `claude/RULES.md` ("the SESSION surface") says to report. Git answers it
    # in one read this script never made — see `gather_worktree_kind`.
    # 🔴 ROUND 5 — THE PREMISE, NOT THE SENTENCE. Round 4 fixed the false SHARED
    # claim and replaced it with a WRITE GRANT ("yours alone … writing here is
    # fine") over a tree that is not the auditor's, plus an absolution inverted
    # the unsafe way ("movement is NOT expected here").
    #
    # Both errors have ONE cause: `gather_worktree_kind` measures the cwd of the
    # ASSEMBLING process, and the consumer is a DIFFERENT process. This script
    # prints a brief for pasting into an `Agent` dispatch, and WHERE TO WORK
    # tells that agent to stand somewhere else (`isolation: "worktree"`, or its
    # own `git worktree add`). So "private" here means private to the session
    # that BUILT the brief — which is live in that tree, so movement IS
    # expected — and under the other configuration (no isolation, inherited
    # cwd) the same tree is shared with the dispatcher and with sibling
    # auditors. "Yours alone" is wrong either way.
    #
    # Observed live: the round-5 brief named this session's own agent worktree,
    # classified it PRIVATE and printed "Writing here is fine", and the
    # operator's hand-written dispatch had to CONTRADICT the generated brief in
    # prose. Retyping a correction over generated text is the failure this
    # script exists to remove.
    #
    # So this states only what the script knows: the path is where the brief was
    # ASSEMBLED. It grants nothing.
    Directive(
        "checkout-private",
        "🔴 **This is the checkout this brief was ASSEMBLED in — not "
        "necessarily the one you are standing in.** Git reports it as a "
        "PRIVATE linked worktree: its `.git` is a link into a shared "
        "repository, and the working tree belongs to the session that BUILT "
        "this brief, which is not you. That session is live in it, so files "
        "here can appear, vanish and change. **Write nothing to it** — the "
        "path is a fact about where the brief was built, never a tree you may "
        "work in. If you do see something move here, report it with what moved "
        "and when, and do not try to restore it.",
    ),
    Directive(
        "checkout-unknown",
        "🔴 **COULD NOT DETERMINE whether this checkout is shared** — the "
        "`git rev-parse --git-dir --git-common-dir` read that decides it did "
        "not answer. **Treat it as SHARED and write nothing to it.** But do "
        "NOT treat anything you see move as expected: that absolution belongs "
        "to a checkout KNOWN to be shared, and this one is not known to be "
        "anything. If something moves, report it AND report that its cause "
        "could not be established here.",
    ),
    # 🔴 Y2 — verbatim, operative, and unpinned. Inverting it into "do not
    # fetch or check out inside it" passed all 199 audit-related tests and
    # leaves a cross-repo auditor with nowhere to work: the brief has just told
    # them to build that worktree themselves precisely so they CAN.
    #
    # 🔴 ROUND 6 — THE SECOND SENTENCE FORWARD-REFERENCES A CLAUSE THAT NO
    # LONGER SAYS WHAT IT NAMES. It used to read "the no-write rule below is
    # about the SHARED checkout", which was true while `no-fetch` was scoped to
    # sharedness. Round 5 rewrote that clause off sharedness and onto OWNERSHIP,
    # and this sentence was left behind — so in the configuration production
    # actually produces (a cross-repo PR assembled in a PRIVATE agent worktree)
    # the brief reads, in order: "the no-write rule below is about the SHARED
    # checkout" -> "kind : PRIVATE linked worktree" -> "write to no checkout
    # that is not the copy YOU made, including the one THE CHECKOUT names".
    # That is the same syllogism round 4 wrote into `checkout-private` and round
    # 5 deleted, surviving one directive over: the reader can discharge the
    # no-write rule on the grounds that this checkout is not shared.
    #
    # So the scope is stated by OWNERSHIP, which is true in every checkout
    # state and cannot be discharged by one.
    #
    # 🔴 ROUND 8 — AND IT IS NOW THE CLAUSE'S OWN WORDS, NOT MERELY ITS AXIS.
    # Round 7's comment here read "the scope is stated the way the clause
    # states it", which was a claim ABOUT THE CLAUSE and was false: the clause
    # said "the copy YOU made FOR THIS AUDIT" and this sentence said "every
    # checkout you did not make". Two sets, differing on precisely the clone
    # the recipe three lines above writes into. `no-fetch` dropped the extra
    # narrowing, this sentence is unchanged, and both are pinned to the one
    # phrase `NO_WRITE_SCOPE` — so the guard fails if either side moves alone,
    # instead of only when a CHECKOUT STATE WORD appears.
    #
    # The first sentence names the CLONE as well as the worktree because that
    # is the tree the recipe actually writes to: `git -C <clone> worktree add`
    # is a write to the clone, before the worktree it grants exists.
    #
    # 🔴 ROUND 10 — BUT IT GRANTED FAR MORE THAN THE RECIPE NEEDS, AND IN THE
    # ONE TREE WITH REAL BLAST RADIUS. Round 8 widened "fetching and checking
    # out inside IT" to "inside EITHER", i.e. into the clone — while the recipe
    # three lines above spells that clone `<your local clone of owner/name>`,
    # which on this host resolves in practice to `~/workspace/<repo>`, a
    # long-lived tree other sessions are working in. `worktree add` is the only
    # write the recipe performs there; `fetch` and `checkout` are exactly the
    # cross-session writes the `no-fetch` clause exists to prevent, and this
    # directive was authorising them by name.
    #
    # "The clone YOU made" arguably excludes those trees already — but nothing
    # in the brief disambiguated it and the placeholder invites the
    # substitution, so the grant is stated as the OPERATION it covers rather
    # than as an adjective on the tree. The forward-reference sentence is
    # unchanged and still spells `NO_WRITE_SCOPE`.
    Directive(
        "own-worktree-is-writable",
        "That worktree is YOURS: fetching and checking out inside it is fine. "
        "In the clone you made it from, `git worktree add` is the ONLY write "
        "this brief asks for — do not `fetch`, `pull` or `checkout` there, "
        "whoever made it, because other sessions may be standing in it. The "
        "no-write rule below is about every checkout you did not make.",
    ),
)

DIRECTIVE = {d.id: d.text for d in SECTION_DIRECTIVES}

# 🔴 WHICH DIRECTIVE EACH `gather_worktree_kind` STATE RENDERS — ONE PLACE.
# `render_checkout` used to carry this map inline, which left the RELATIONSHIP
# ("every state the renderer can select carries the no-write rule") with no
# machine-readable statement anywhere: round 5's guard over it had to spell the
# set as ids beginning `checkout-`, and round 6 measured the consequence —
# RENAMING `checkout-private` to `assembly-private`, DROPPING its no-write
# sentence and updating the three ledgers left the whole suite green. The rule
# is the renderer's selection, never a naming convention, so the selection is
# what a guard now reads. `unknown` is both a state and the fallback for a
# state this map does not know: not knowing which state you are in is itself
# the unknown state, and collapsing it into either answer picks the branch
# whose failure is silent.
CHECKOUT_STATE_DIRECTIVE = {
    "shared": "checkout-moves",
    "private": "checkout-private",
    "unknown": "checkout-unknown",
}


def directive(did):
    """The verbatim block `did` — or a LOUD placeholder if it was deleted.

    Never a `KeyError` and never silence. A deleted directive must leave a mark
    a human reading the brief can see AND that the rendered-section pin can
    fail on; raising instead would make every test in the suite error, which
    scores a deletion as "killed" for the wrong reason and tells the operator
    nothing.
    """
    return DIRECTIVE.get(did) or f"⚠ MISSING VERBATIM BLOCK `{did}`"

# The two mutually exclusive worktree directives. Which one a brief carries is
# decided by a FACT (the PR's repo vs the cwd's repo), never by the author's
# memory — that decision is the single highest-value generated field here.
ISOLATION_RECOMMEND = 'Dispatch with `isolation: "worktree"`'
ISOLATION_FORBID = 'Do NOT use `isolation: "worktree"`'

# --------------------------------------------------------------------------- #
# The claims block — the ONLY thing read out of a PR comment.
# --------------------------------------------------------------------------- #
# Tolerant on the header's trailing content and on the fence length, strict on
# the two fields that matter. A block whose header does not parse is reported as
# MALFORMED rather than skipped: skipping it silently would produce the same
# observable as "no block at all", and those need different fixes.
#
# 🔴 THAT COMMENT WAS FALSE FOR FOUR SHAPES, so the parser is line-based rather
# than one regex. The old `^(?P=fence)\s*$` backreference required the CLOSING
# fence to be byte-identical to the opener, and anything it could not match was
# dropped with no report at all:
#   * an UNCLOSED fence — the refusal then said "no `audit-claims` block in any
#     of the 1 comment(s) read", which is false and points at the wrong fix;
#   * a 4-backtick opener closed with 3 — not a close under CommonMark either,
#     so this really is unclosed and is now named as such;
#   * a 3-backtick opener closed with 4 — which IS a valid CommonMark close and
#     renders closed on GitHub, so it is now PARSED rather than dropped;
#   * a nested fence inside the body, which ends the block early and silently
#     drops every claim after it.
# A claim that WRAPS onto a continuation line was silently truncated to its
# first line, changing the claim; continuation lines are now appended.
_FENCE_OPEN = re.compile(r"^(?P<fence>`{3,})audit-claims(?P<header>[^\n]*)$")
_FENCE_BARE = re.compile(r"^(?P<fence>`{3,})\s*$")
_HEADER_ROUND = re.compile(r"\bround=(\d+)\b")
_HEADER_AUDITED = re.compile(r"\baudited=(\S+)")
_CLAIM_ITEM = re.compile(r"^\s*(\d+)[.)]\s+(.+?)\s*$")

ClaimsBlock = namedtuple("ClaimsBlock", "round_no audited_from audited_to items")


def _items_from_body(body_lines):
    """Numbered claim lines, with CONTINUATION lines folded into the item above.

    A claim wrapped over two lines used to lose everything after the first —
    which does not fail, it changes the claim, and the next round is framed on
    the truncated text.
    """
    items = []
    for line in body_lines:
        m = _CLAIM_ITEM.match(line)
        if m:
            items.append(m.group(2))
        elif items and line.strip():
            items[-1] = f"{items[-1]} {line.strip()}"
    return items


def parse_claims_blocks(texts):
    """-> (blocks, malformed_reasons) over an iterable of comment bodies.

    Pure and independently testable: the refusal below is only trustworthy if
    this can be driven with no network and no PR.

    🔴 `malformed` is NOT only populated when the block is unusable. A structural
    problem that still leaves a parseable block (a nested fence that cut the
    claims in half) is reported too, and `main` warns about it rather than
    letting a half-read block pass as a whole one.
    """
    blocks, malformed = [], []
    for text in texts:
        lines = (text or "").splitlines()
        i = 0
        while i < len(lines):
            om = _FENCE_OPEN.match(lines[i])
            if om is None:
                i += 1
                continue
            opener, header = om.group("fence"), om.group("header")

            body, close_at, j = [], None, i + 1
            while j < len(lines):
                bm = _FENCE_BARE.match(lines[j])
                # CommonMark: a closing fence must be AT LEAST as long as the
                # opener. A shorter run of backticks is content, not a close.
                if bm and len(bm.group("fence")) >= len(opener):
                    close_at = j
                    break
                body.append(lines[j])
                j += 1

            if close_at is None:
                malformed.append(
                    "an `audit-claims` fence that is never CLOSED — no line of "
                    f"{len(opener)} or more backticks follows it (header was: "
                    f"`{header.strip() or '<empty>'}`). A shorter closing fence "
                    "does not close it, under CommonMark or here."
                )
                i += 1
                continue

            r = _HEADER_ROUND.search(header)
            a = _HEADER_AUDITED.search(header)
            if not r or not a:
                malformed.append(
                    "an `audit-claims` fence whose header does not carry both "
                    f"`round=<n>` and `audited=<sha>..<sha>` (header was:"
                    f" `{header.strip() or '<empty>'}`)"
                )
                i = close_at + 1
                continue

            spec = a.group(1)
            frm, _, to = spec.partition("..")
            if not to:
                # A bare sha is accepted as the audited TIP; for a round-1 block
                # that tip IS the cumulative anchor (see `round_one_anchor`),
                # and for any later round the anchor is simply unknown, which
                # the ledger reports rather than guesses.
                frm, to = "", frm

            items = _items_from_body(body)
            if not items:
                malformed.append(
                    f"an `audit-claims round={r.group(1)}` block with no "
                    "numbered claim lines in its body"
                )
                i = close_at + 1
                continue

            # 🔴 The nested-fence report. A fence inside the body ends the block
            # early — CommonMark and GitHub agree — so the claims after it are
            # outside it and were dropped with no signal. The tell is claim
            # line(s) AND a stray fence in the region after the close, which is
            # what a cut-in-half block leaves behind. Deliberately a report and
            # not a rejection: the block that DID parse is still used, and a
            # comment whose trailing prose merely happens to contain both would
            # otherwise lose a usable block to a heuristic.
            k = close_at + 1
            tail = []
            while k < len(lines) and _FENCE_OPEN.match(lines[k]) is None:
                tail.append(lines[k])
                k += 1
            if any(_CLAIM_ITEM.match(t) for t in tail) and any(
                _FENCE_BARE.match(t) for t in tail
            ):
                malformed.append(
                    f"an `audit-claims round={r.group(1)}` block that looks CUT "
                    "SHORT by a nested fence: numbered claim line(s) and a "
                    "stray fence follow its closing fence, so claims after the "
                    f"nested fence were not read (read {len(items)}). Indent "
                    "any code inside a claim, or open the block with four "
                    "backticks."
                )

            blocks.append(ClaimsBlock(int(r.group(1)), frm, to, items))
            i = close_at + 1
    return blocks, malformed


def newest_block(blocks):
    """The highest `round=` present. Ties resolve to the last one seen."""
    best = None
    for b in blocks:
        if best is None or b.round_no >= best.round_no:
            best = b
    return best


def range_anchor(block):
    """🔴 What a DELTA round must diff FROM — `audited_from`, not `audited_to`.

    THE FIELD MEANT TWO DIFFERENT THINGS TO THE WRITER AND THE READER, and the
    range that fell out was EMPTY BY CONSTRUCTION. `--emit-claims` stamps the
    PR's CURRENT head into `<to>`; this function used to be spelled
    `newest.audited_to` at the one call site, so the next round diffed
    `<the head at emit time>..HEAD` — and those coincide, because the block is
    posted at the fix tip. Reproduced live on devrc #958: the round-2 comment
    carried `audited=abc41024..d9eb36a8`, and `--round 3` rendered
    ``Diff `d9eb36a8..HEAD` `` with HEAD *being* `d9eb36a8`.

    That is worse than a wrong number. THE RANGE section does not consult the
    ledger, so it printed the empty range under "verified at assembly time to be
    PR #958's head commit" followed by "Do not re-audit the whole PR" — an
    auditor obeying it diffs nothing, finds nothing, and the `stop-rule` clause
    then converts that into "the ladder ENDS".

    THE SEMANTICS, stated once so both sides can be checked against it:

        `audited=<from>..<to>` records that the round's fix took the tree from
        `<from>` — the tip that round's AUDIT read — to `<to>`, the head its
        fixes produced.

    So a delta round reads EVERYTHING SINCE THE PREVIOUSLY-AUDITED TIP:

      * a TWO-SHA `audited=<from>..<to>` -> `<from>`, whatever HEAD is. When
        the block was posted at the fix tip (the common case) HEAD == `<to>`
        and the range is exactly that round's fix commits; when more commits
        landed after it was posted they are included too, which is what a
        delta round wants.
      * a BARE `audited=<sha>` (the round-1 spelling) has no `<from>`
        -> fall back to `<to>`. That single sha is only an anchor if it is the
        tip the round AUDITED. 🔴 THE BARE FORM IS WHAT YOU GET WITH `--audited`
        OMITTED, and the flag is how you AVOID it: passing
        `--emit-claims --audited <sha>` writes a TWO-SHA `audited=<sha>..<head>`
        block, which takes the first bullet's path and never this one. Omitted,
        `--emit-claims` stamps the PR's CURRENT head as that single sha — the
        head the round's FIXES produced, not the tip it read — and this fallback
        then makes `<sha>..HEAD` a self-range. That is why `--emit-claims` warns
        on exactly that spelling instead of emitting it silently: the two
        bullets are otherwise jointly saying "the common case for the round-1
        spelling is a broken range".

    The WRITER's counterpart is `emit_anchor` — a different quantity, and
    conflating them is what this pair exists to prevent.
    """
    if block is None:
        return None
    return block.audited_from or block.audited_to or None


def emit_anchor(block):
    """🔴 What `--emit-claims` must write as `<from>` — `audited_to`.

    The mirror image of `range_anchor`, and NOT the same sha. A round-N block
    records the tip round N's AUDIT read; that tip is the head the previous
    round's block ended at, i.e. the newest block's `audited_to`.

    Spelling this `range_anchor` instead — the obvious "one anchor for
    everything" simplification — writes the tip round N-1 audited into round N's
    block, so round N+1 re-audits round N-1's fix commits as well. A superset,
    not an empty range, which is why it would survive a casual read; the two
    readers are kept separate and named so the next editor has to choose.

    With no prior block (a round-1 `--emit-claims`) there is no `<from>` to
    recover, which is what `--audited <sha>` supplies: `main` prefers the flag
    over this function's answer, because a sha the operator states beats one
    derived from a block someone typed. With NEITHER, the bare
    `audited=<sha>` spelling is emitted and warned about — see
    `emit_claims_skeleton` and the warning in `main`.
    """
    if block is None:
        return None
    return block.audited_to or None


def round_one_anchor(blocks):
    """The sha ROUND 1 audited, or None.

    That is the only sha in the corpus that can anchor the ledger's cumulative
    "since round 1" figure. When no block carries one, the figure is reported
    NOT MEASURED — never derived from the base, which is a different quantity.

    Two spellings carry it, and both are read:
      * `round=2 audited=A..B` — A is the tip ROUND 2's audit read, which is the
        head round 1's fixes produced (the usual case);
      * `round=1 audited=A`    — the bare form `--emit-claims` writes for a
        round-1 comment, which is that same head.
    A bare sha on any LATER round is NOT an anchor: it says what that round
    audited, and the round-1 tip is then genuinely unknown.

    ⚠ NAMED PRECISELY BECAUSE THE OLD WORDING WAS WRONG ("A is the tip round 1
    audited"), AND THE ANSWER NOW DEPENDS ON HOW THE ROUND-1 BLOCK WAS WRITTEN:

      * `round=2 audited=A..B` — A is the head round 1's FIXES produced, so the
        figure is "since round 1's fixes landed" and round 1's own fix commits
        are BELOW it. A real limit of that spelling, not a defect here.
      * `round=1 audited=A` written WITHOUT `--audited` — the same limit, for
        the same reason: A is the head, assumed.
      * `round=1 audited=A..B` written WITH `--audited A` — A really is the tip
        round 1 READ, and the figure then means "since round 1 read the tree",
        which is the stronger quantity the name suggests.

    So the caveat is about the SPELLING, not about this function, and it is
    written out rather than left for someone to re-derive from the name.
    """
    candidates = []
    for b in blocks:
        if b.audited_from:
            candidates.append((b.round_no, b.audited_from))
        elif b.round_no == 1 and b.audited_to:
            candidates.append((b.round_no, b.audited_to))
    # Stable sort on the round number alone, so a tie resolves to the block seen
    # first rather than to whichever sha sorts lower.
    candidates.sort(key=lambda c: c[0])
    return candidates[0][1] if candidates else None


# --------------------------------------------------------------------------- #
# Process boundary — ONE runner, injected, so every test is hermetic.
# --------------------------------------------------------------------------- #

def real_runner(cmd, cwd=None):
    """-> (rc, stdout, stderr). The only place this module spawns anything."""
    p = subprocess.run(
        cmd, cwd=cwd, capture_output=True, text=True, check=False
    )
    return p.returncode, p.stdout, p.stderr


# --------------------------------------------------------------------------- #
# Facts gathered per invocation
# --------------------------------------------------------------------------- #

LedgerReport = namedtuple(
    "LedgerReport",
    "files added deleted commits reason cumulative cumulative_reason",
)
# 🔴 `head_check` is the FOURTH read rule, beside rc 0 / silent stderr /
# non-empty range. See `verify_head_is_the_pr`.
HeadCheck = namedtuple("HeadCheck", "ok reason local_sha pr_sha")
Facts = namedtuple(
    "Facts",
    "pr repo title base_ref url round_no cwd_repo_dir cwd_repo_slug repo_relation "
    "worktree branch dirty prev_sha emit_from claims claims_round checklist "
    "ledger assembled_at claims_source head_check base_assumed "
    "base_assumed_reason",
)
# 🔴 `base_assumed` — ROUND 10's SEVENTH INSTANCE, found by the sweep round 9's
# auditor asked for and not by a finding. `base_ref` is
# `data.get("baseRefName") or "main"`: a DEFAULT, and every site printing it
# presented it as a MEASUREMENT. Measured at `706a6b38` with `baseRefName`
# absent, rc 0 and silent stderr, THE LEDGER's cross-repo hand-over read
# "…where `origin/main` names THAT repository's base branch and not this
# checkout's" — an assertion about a repository nothing was asked about.
# `--claims-file` mode reaches it on EVERY run: it consults no `gh`, so it
# learns no `baseRefName`, exactly as it learns no `headRefOid`.
# 🔴 ROUND 12 — AND FOR TWO ROUNDS IT DID NOT REACH IT. Round 10 stated the
# sentence above and left the mode HARDCODING `baseRefName: "main"`, so
# `base_assumed` answered False in the one mode this comment, the test
# module's `delta-assumed-base` comment and the guard's own docstring all
# call permanently assumed — the banner was suppressed exactly where it is
# always warranted, at rc 0. The hardcode is gone and the mode now carries a
# per-cause `base_assumed_reason`, because "gh reported no baseRefName" is
# itself false in a run that never asked `gh`. Same shape as finding A one
# field over, and the same remedy.
# 🔴 `prev_sha` and `emit_from` are TWO ANCHORS out of ONE `audited=` field, and
# they are deliberately separate fields rather than one: `prev_sha` is
# `range_anchor` (what this round DIFFS FROM) and `emit_from` is `emit_anchor`
# (what `--emit-claims` WRITES as the next block's `<from>`). Collapsing them is
# the defect this round fixed, in one direction or the other.


def _slug_from_remote(url):
    """`owner/name` out of any git remote URL spelling, or None."""
    if not url:
        return None
    url = url.strip().removesuffix(".git")
    m = re.search(r"[:/]([^/:]+)/([^/]+)$", url)
    return f"{m.group(1)}/{m.group(2)}" if m else None


def gather_repo_facts(runner, cwd):
    """(repo_dir, slug) for the checkout the operator is standing in."""
    rc, out, _ = runner(["git", "-C", cwd, "rev-parse", "--show-toplevel"])
    repo_dir = out.strip() if rc == 0 and out.strip() else str(cwd)
    rc, out, _ = runner(["git", "-C", repo_dir, "remote", "get-url", "origin"])
    slug = _slug_from_remote(out) if rc == 0 else None
    return repo_dir, slug


WorktreeKind = namedtuple("WorktreeKind", "kind git_dir common_dir reason")


def gather_worktree_kind(runner, repo_dir):
    """🔴 SHARED checkout, PRIVATE linked worktree, or COULD NOT DETERMINE.

    🔴 IT MEASURES THE TREE THIS PROCESS IS ASSEMBLING IN, AND NOTHING ELSE.
    The consumer is a DIFFERENT process: this script prints a brief for pasting
    into an `Agent` dispatch, and WHERE TO WORK tells that agent to make its own
    copy (`isolation: "worktree"`, or a hand-rolled `git worktree add`). So a
    `private` answer means "private to the session that ran THIS script" — never
    "private to the auditor", and never a licence to write here. Round 4's
    docstring claimed this is "where a dispatched auditor usually stands"; that
    was false, and the `checkout-private` directive was written against it,
    granting write permission over a tree belonging to the dispatching session.
    Under the other configuration (no isolation, an inherited cwd) the same tree
    is shared with the dispatcher and with sibling auditors — so no state this
    function can return makes the tree the auditor's own.

    THREE states, the same shape as `render_worktree_directive`'s repo decision
    and for the same reason: collapsing "cannot tell" into either answer picks
    the branch whose failure is silent.

    THE BRIEF ASSERTED "SHARED" OF WHATEVER `--show-toplevel` RETURNED. Run
    from `…/.claude/worktrees/agent-…` it printed a heading reading THE SHARED
    CHECKOUT over "This checkout is SHARED with other sessions and agents. It
    MOVES UNDER YOU … That is expected and is NOT your fault and NOT a finding."
    Two of those claims are false for a per-agent worktree (it is one session's,
    not many; movement in it is worth naming), and the DIRECTION is what makes
    it consequential: the reader is told to disregard exactly the
    sibling-agent clobber `claude/RULES.md` says to report.

    Git answers it in one read:

        --git-dir        …/devrc/.git/worktrees/agent-af298…
        --git-common-dir …/devrc/.git            <- differ => linked worktree

    🔴 COMPARED AS RESOLVED PATHS, NEVER AS STRINGS. Measured at four points:
    a repo ROOT answers `.git` / `.git`; a repo SUBDIRECTORY answers
    `/abs/path/.git` / `../.git` — textually different, the same directory. A
    string compare would call every subdirectory of an ordinary clone a private
    worktree, i.e. it would be wrong in the direction that drops the no-write
    instruction. `repo_dir` is normally `--show-toplevel`, but
    `gather_repo_facts` falls back to the raw cwd when that fails, so the
    subdirectory case is reachable in production and not a hypothetical.
    """
    rc, out, err = runner(
        ["git", "-C", repo_dir, "rev-parse", "--git-dir", "--git-common-dir"]
    )
    lines = [ln.strip() for ln in (out or "").splitlines() if ln.strip()]
    if rc != 0 or len(lines) != 2:
        return WorktreeKind(
            "unknown", None, None,
            f"`git rev-parse --git-dir --git-common-dir` in {repo_dir} exited "
            f"{rc} and printed {len(lines)} path(s) where 2 were expected: "
            f"{((err or out) or '').strip() or 'no output'}",
        )

    def _resolved(p):
        # `-C repo_dir` is what git resolved these against, so join there.
        return os.path.realpath(os.path.join(repo_dir, p))

    git_dir, common_dir = _resolved(lines[0]), _resolved(lines[1])
    kind = "shared" if git_dir == common_dir else "private"
    return WorktreeKind(kind, git_dir, common_dir, None)


def gather_checkout_state(runner, repo_dir):
    """(branch, dirty-path-count). Both READS; nothing here writes."""
    rc, out, _ = runner(["git", "-C", repo_dir, "rev-parse", "--abbrev-ref", "HEAD"])
    branch = out.strip() if rc == 0 else "UNKNOWN"
    rc, out, _ = runner(["git", "-C", repo_dir, "status", "--porcelain"])
    dirty = len([ln for ln in out.splitlines() if ln.strip()]) if rc == 0 else -1
    return branch, dirty


def gh_pr_facts(runner, pr, repo=None):
    """PR metadata + comment bodies, via `gh`. -> (dict, [comment bodies]).

    🔴 `headRefOid` and `isCrossRepository` are read because the two decisions
    that were WRONG without them are the two most expensive ones this script
    makes: which tree the ledger measured, and which repository the PR lives in.
    🔴 There is NO `baseRepository` field on `gh pr view --json` (checked
    against `gh`'s own field list) — `url` is what carries the base repo, and
    `pr_slug` reads it.
    """
    cmd = ["gh", "pr", "view", str(pr), "--json",
           "title,url,baseRefName,headRefOid,isCrossRepository,"
           "headRepository,headRepositoryOwner,comments"]
    if repo:
        cmd += ["--repo", repo]
    rc, out, err = runner(cmd)
    if rc != 0:
        return {"_error": (err or out).strip() or f"gh exited {rc}"}, []
    try:
        data = json.loads(out)
    except ValueError as e:
        return {"_error": f"gh returned unparseable JSON: {e}"}, []
    comments = [
        c.get("body") or "" for c in (data.get("comments") or [])
        if isinstance(c, dict)
    ]
    return data, comments


# `https://<host>/<owner>/<name>/pull/<n>` — the PR's own URL, which names the
# repository the PR LIVES IN. Host-agnostic on purpose (GHES spells the host
# differently and the path shape is the same).
_PR_URL = re.compile(r"^https?://[^/]+/([^/]+)/([^/]+)/pull/\d+")


def pr_slug(data, override=None):
    """The repo the PR LIVES IN — NOT the repo its head branch lives in.

    🔴 This read `headRepositoryOwner`/`headRepository` and was therefore wrong
    for every FORK PR. Verified against real `gh` output for a fork PR against
    `cli/cli`: `headRepository.nameWithOwner` is `ylfeng250/cli` while `url` is
    `https://github.com/cli/cli/pull/14280` and `isCrossRepository` is `true`.
    A fork PR opened against THIS repo therefore computed a slug that differs
    from the cwd's, took the CROSS-REPO branch, and told the agent to worktree
    "your local clone of <contributor>/<repo>" — a clone that does not exist,
    for a repository the PR is not in.

    `gh pr view --json` has no `baseRepository` field, so `url` is the authority
    and `isCrossRepository` is what says whether the head fields may stand in
    for it: they are the same repo only when it is false.
    """
    if override:
        return override
    m = _PR_URL.match((data.get("url") or "").strip())
    if m:
        return f"{m.group(1)}/{m.group(2)}"
    if data.get("isCrossRepository") is False:
        owner = (data.get("headRepositoryOwner") or {}).get("login")
        name = (data.get("headRepository") or {}).get("name")
        if owner and name:
            return f"{owner}/{name}"
    # 🔴 None, never a guess. `main` renders a third WHERE-TO-WORK branch for
    # this, because collapsing "cannot tell" into "same repo" recommends the
    # flag that is dangerous in exactly the case it cannot rule out.
    return None


def verify_head_is_the_pr(runner, repo_dir, pr_head_sha, no_sha_reason=None):
    """🔴 The FOURTH read rule: is this checkout's HEAD the PR's head commit?

    Everything the delta half of this brief says — the ledger's numbers, the
    `<prev>..HEAD` range it hands the auditor, and the `audited=` sha
    `--emit-claims` stamps for the NEXT round to anchor on — is computed against
    `HEAD` in the operator's own checkout. That checkout is SHARED and moves;
    nothing here ever checked that it was standing on the PR at all.

    Reproduced from a clone standing on an unrelated feature branch: rc 0,
    silent stderr, a non-empty range — all three advertised read rules satisfied
    — and a ledger of that branch's files. Standing on `main` instead produced
    the confident banner "🔴 Zero changed lines over a NON-EMPTY range. That is
    a real measurement, not a failure."

    So a failed check returns a `reason` and NO number, exactly like the other
    three: an unverifiable measurement is not a measurement.

    🔴 `no_sha_reason` IS THE THIRD FALSE-CAUSE SITE, found by sweeping for the
    class rather than fixing the two the audit named. The message here used to
    read "`gh` was not consulted — `--claims-file` mode — or reported no
    `headRefOid`", offering two causes with nothing to choose between them —
    and the CALLER knows which one it is, because it is the caller that decided
    whether to consult `gh` at all. So the caller names it, and this function
    keeps a truthful fallback for a caller that genuinely cannot say.
    """
    if not pr_head_sha:
        why = no_sha_reason or (
            "`gh` was not consulted, or reported no `headRefOid` — this caller "
            "did not say which"
        )
        return HeadCheck(
            False,
            f"the PR's head sha is not known here ({why}), so nothing can "
            "confirm this checkout is standing on the PR",
            None,
            None,
        )
    rc, out, err = runner(["git", "-C", repo_dir, "rev-parse", "HEAD"])
    local = out.strip()
    if rc != 0 or not local:
        return HeadCheck(
            False,
            f"`git rev-parse HEAD` in {repo_dir} exited {rc}: "
            f"{(err or out).strip() or 'no output'}",
            None,
            pr_head_sha,
        )
    if local != pr_head_sha:
        return HeadCheck(
            False,
            f"this checkout's HEAD is `{local}`, but the PR's head is "
            f"`{pr_head_sha}` — the two are DIFFERENT COMMITS, so anything "
            "measured against `HEAD` here is a measurement of another tree",
            local,
            pr_head_sha,
        )
    return HeadCheck(True, None, local, pr_head_sha)


def same_commit(a, b):
    """Do two shas of DIFFERENT LENGTHS name the same commit?

    `audited=` carries an 8-char abbreviation; `git rev-parse HEAD` returns 40.
    A plain `==` between them is False for every real self-range, which is the
    quiet way this whole guard would fail to fire. Empty is never equal to
    anything — `"x".startswith("")` is True, and treating a MISSING anchor as
    "the same commit" would report a self-range for the round-1 case.

    🔴 CASE-FOLDED, and that is not cosmetic either. `git rev-parse` prints
    lowercase hex, but the `audited=` field is TYPED BY A HUMAN into a PR
    comment and `ABC41024` is the same commit as `abc41024`. Compared
    case-sensitively, an upper- or mixed-case anchor answers False here, which
    silently disables `anchor_is_head` at all three call sites and restores the
    confident "verified at assembly time to be PR #<n>'s head commit" banner
    over a range that cannot contain anything — the exact failure the whole
    degenerate-range guard exists to prevent, re-armed by a shift key.
    """
    a, b = (a or "").lower(), (b or "").lower()
    if not a or not b:
        return False
    return a.startswith(b) or b.startswith(a)


def anchor_is_head(anchor, head_check):
    """🔴 Is `<anchor>..HEAD` EMPTY BY CONSTRUCTION rather than merely empty?

    ONE PREDICATE, ONE PLACE — the range renderer, the ledger and
    `--emit-claims` all ask it, and a predicate open-coded at three sites is
    wrong at two of them in the same direction.

    A range whose two ends are the SAME COMMIT can never contain anything, so
    "it is empty" says nothing about the PR: it is a fact about the range. That
    distinction is the whole point. An ordinary empty range is a real
    measurement ("nothing has landed since"); a self-range is a broken question,
    and reporting it as the first is how a round with ZERO commits examined gets
    written up as clean.

    The length mismatch is handled by `same_commit`, not here — and it is not
    cosmetic: `audited=` carries 8 chars, `rev-parse HEAD` returns 40, so a
    plain `==` answers False for every real self-range this exists to catch.

    An UNVERIFIED checkout answers False rather than True: when HEAD is not the
    PR's head, `local_sha` is some other branch's tip and comparing an anchor
    against it says nothing about the range the auditor will run. That case
    already has its own COULD NOT VERIFY banner.
    """
    if head_check is None or not head_check.ok:
        return False
    return same_commit(anchor, head_check.local_sha)


# --------------------------------------------------------------------------- #
# The ledger — the skill's own command, with the skill's own read rules
# --------------------------------------------------------------------------- #

def measure_ledger(runner, repo_dir, prev_sha, base, head_check=None):
    """`git log --numstat --format= --remerge-diff <prev>..HEAD --not <base>`.

    🔴 FOUR read rules are enforced here, not assumed: **the checkout's HEAD is
    the PR's head, rc 0, silent stderr, and a non-empty range**.

    The fourth one came last and is the one that lets the other three pass while
    the answer is entirely wrong: `HEAD` is resolved in the operator's own
    SHARED checkout, which is not necessarily standing on the PR. Reproduced
    from a clone on an unrelated branch — rc 0, silent stderr, a non-empty range
    and a file list belonging to that branch. `head_check` is
    `verify_head_is_the_pr`'s verdict; passing None skips it, which is only for
    a caller that has no PR to compare against.

    A missing ref or a git without `--remerge-diff` exits 128 with empty output;
    an unwritable object store makes `--remerge-diff` UNDER-count, exit 0 and
    print a plausible number, announcing itself only on stderr; and a range
    whose commits are not in this checkout prints nothing at all, silently, with
    rc 0. Each of those returns a `reason` and NO number — a failed command is
    not a zero, and neither is a measurement of the wrong tree.
    """
    def fail(reason):
        return LedgerReport(None, None, None, None, reason, None, None)

    if head_check is not None and not head_check.ok:
        return fail(
            "this checkout is not standing on the PR, so `..HEAD` does not "
            f"mean what this ledger would claim it means: {head_check.reason}"
        )

    rc, out, err = runner(
        ["git", "-C", repo_dir, "rev-list", "--count", f"{prev_sha}..HEAD"]
    )
    if rc != 0:
        return fail(f"`git rev-list {prev_sha}..HEAD` exited {rc}: "
                    f"{(err or out).strip() or 'no output'}")
    if err.strip():
        return fail(f"`git rev-list` wrote to stderr: {err.strip()}")
    try:
        commits = int(out.strip())
    except ValueError:
        return fail(f"`git rev-list --count` printed {out.strip()!r}, not a number")
    if commits == 0:
        # 🔴 THREE causes, and TWO OF THEM ARE REFUTED BY `head_check` — which is
        # a parameter of this very function, already required `ok` twelve lines
        # above. The old text named "the fixes are not committed yet, or this
        # checkout does not have them" unconditionally, so a brief printed
        # "verified at assembly time to be PR #958's head commit" and "this
        # checkout does not have them" in one document. Same false-cause shape
        # as the cumulative reason below, and the reason that fix is now applied
        # to the CLASS rather than to one site.
        if anchor_is_head(prev_sha, head_check):
            return fail(
                f"the range `{prev_sha}..HEAD` is EMPTY BY CONSTRUCTION, not "
                f"merely empty: `{prev_sha}` IS this checkout's HEAD "
                f"({head_check.local_sha}), so both ends of the range are the "
                "same commit and it could not contain anything whatever the "
                "PR looks like. That is a broken question, NOT a clean round — "
                "do not read a finding-free audit over it as evidence of "
                "anything. " + directive("degenerate-range-causes")
            )
        if head_check is not None and head_check.ok:
            return fail(
                f"the range `{prev_sha}..HEAD` is EMPTY — and this checkout's "
                f"HEAD ({head_check.local_sha}) IS the PR's head, so it is not "
                "a case of the commits being elsewhere: nothing has landed on "
                f"the PR itself since `{prev_sha}`. The fixes for this round "
                "are not committed and pushed yet."
            )
        return fail(
            f"the range `{prev_sha}..HEAD` is EMPTY — nothing has landed since "
            "the sha that round audited, so there is no delta to audit. Either "
            "the fixes are not committed yet, or this checkout does not have "
            "them. (No head check was made here, which is why both remain "
            "live.)"
        )

    rc, out, err = runner([
        "git", "-C", repo_dir, "log", "--numstat", "--format=",
        "--remerge-diff", f"{prev_sha}..HEAD", "--not", base,
    ])
    if rc != 0:
        return fail(f"the numstat command exited {rc}: "
                    f"{(err or out).strip() or 'no output'}")
    if err.strip():
        return fail(
            "the numstat command exited 0 but wrote to STDERR, so its number is "
            f"not trustworthy: {err.strip()}"
        )

    files, added, deleted = {}, 0, 0
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        a, d, path = parts[0], parts[1], parts[-1]
        na = 0 if a == "-" else int(a) if a.isdigit() else 0
        nd = 0 if d == "-" else int(d) if d.isdigit() else 0
        cur = files.get(path, (0, 0))
        files[path] = (cur[0] + na, cur[1] + nd)
        added += na
        deleted += nd
    return LedgerReport(files, added, deleted, commits, None, None, None)


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #

def _bar():
    return "-" * 76


def render_invariants():
    out = [INVARIANTS_HEADING, ""]
    out += [f"- {c.text}" for c in INVARIANT_CLAUSES]
    return "\n".join(out)


def render_worktree_directive(facts):
    """🔴 The generated field that closes the skill's 🔴 cross-repo hazard.

    `isolation: "worktree"` worktrees the CWD's repo. For a PR in a DIFFERENT
    repo that is the wrong tree, and the failure is quiet: the agent either
    reports a briefed file missing, or silently audits the wrong repository and
    reports findings about it.

    🔴 THREE states, not two. The decision used to be `bool(cwd_slug and repo
    and cwd_slug != repo)`, so "could not determine either side" evaluated
    FALSE and collapsed into the SAME-REPO branch — the one that recommends the
    flag. With no `origin` remote the output contradicted itself in one
    paragraph: "The PR lives in `<org>/<other-repo>`, which is this session's
    own repository (`/home/…/devrc`)", followed by the recommendation. Not
    knowing is its own answer, and it is the answer that must NOT recommend a
    flag whose failure mode is silent.
    """
    if facts.repo_relation == "unknown":
        unknown_side = (
            "this checkout has no `origin` remote to resolve a slug from"
            if not facts.cwd_repo_slug else
            "`gh` did not report which repository the PR lives in"
        )
        return "\n".join([
            "## WHERE TO WORK — 🔴 COULD NOT DETERMINE — decide by hand",
            "",
            "This script could not establish whether the PR is in THIS "
            f"session's repository or another one: {unknown_side}.",
            "",
            f"    the PR's repo     : {facts.repo}",
            f"    this checkout     : {facts.cwd_repo_dir}"
            + (f" (`{facts.cwd_repo_slug}`)" if facts.cwd_repo_slug else
               " (no `origin` remote)"),
            "",
            f"🔴 {ISOLATION_FORBID} until that is answered. The flag builds its "
            "worktree from the CWD's repo; if that is the wrong repo it fails "
            "quietly — the agent either reports a briefed file missing or "
            "silently audits the wrong tree — and this script cannot currently "
            "rule that out. **Not knowing is not the same as same-repo**, and "
            "the same-repo branch is the one that recommends the flag.",
            "",
            "Answer it, then follow the matching directive:",
            "",
            "```",
            f"gh pr view {facts.pr} --json url          # the repo the PR lives in",
            f"git -C {facts.cwd_repo_dir} remote get-url origin",
            "```",
            "",
            "Or re-run this script with `--repo owner/name` to state the PR's "
            "repository outright.",
        ])
    if facts.repo_relation == "cross":
        return "\n".join([
            "## WHERE TO WORK — 🔴 CROSS-REPO",
            "",
            f"The PR lives in `{facts.repo}`. This session's checkout is "
            f"`{facts.cwd_repo_dir}`"
            + (f" (`{facts.cwd_repo_slug}`)" if facts.cwd_repo_slug else "")
            + " — a DIFFERENT repository.",
            "",
            f"🔴 {ISOLATION_FORBID} for this dispatch: that flag builds its "
            "worktree from the CWD's repo, which is the wrong one here, and it "
            "fails quietly — the agent either reports a briefed file missing or "
            "silently audits the wrong tree.",
            "",
            "Create the worktree yourself, against the PR's own clone:",
            "",
            "```",
            f"git -C <your local clone of {facts.repo}> worktree add "
            f"/tmp/audit-pr{facts.pr}-r{facts.round_no} <the PR's head branch>",
            "```",
            "",
            directive("own-worktree-is-writable"),
        ])
    # 🔴 "this session's own repository (`…/worktrees/agent-…`)" named a
    # PRIVATE per-agent worktree as if it were the clone. The repo relation is
    # unaffected — a linked worktree really is the same repository — but the
    # PATH is not the shared clone, and the brief said it was.
    here = f"`{facts.cwd_repo_dir}`"
    if facts.worktree.kind == "private":
        here += " — a PRIVATE linked worktree of it, not the shared clone"
    return "\n".join([
        "## WHERE TO WORK",
        "",
        f"The PR lives in `{facts.repo}`, which is the repository this session "
        f"is standing in ({here}).",
        "",
        f"{ISOLATION_RECOMMEND} — the flag worktrees the CWD's repo, and here "
        "that is the right one.",
    ])


def render_claims(facts):
    if facts.round_no < 2:
        return ""
    lines = [
        "## WHAT WAS CLAIMED FIXED",
        "",
        directive("claims-framing"),
        "",
    ]
    lines += [f"{i}. {c}" for i, c in enumerate(facts.claims, 1)]
    lines += [
        "",
        f"(Read from the `audit-claims round={facts.claims_round}` block in "
        f"{facts.claims_source}. Nothing else from those comments is reproduced "
        "here — the reasoning beside a claim is exactly what a framed audit "
        "goes on to confirm.)",
    ]
    return "\n".join(lines)


# 🔴 THE ONE SPELLING OF "this script never learned the PR's head commit".
# Written into a rev spec by `range_tip` and into an `audited=` header by
# `--emit-claims`, and it is NOT a sha: every site that PRINTS it has to say so,
# which is what `range_tip_is_a_sha` and `unresolved_tip_note` below are for.
# Open-coded at the two sites it was one reword away from two different
# placeholders, only one of which any guard could recognise.
TIP_PLACEHOLDER = "<the PR's head sha>"

# 🔴 `facts.repo` when NOTHING answered which repository the PR lives in. A
# sentinel and not a slug, so no command may interpolate it: `gh pr view 900
# --repo UNKNOWN (not reported by ...)` is a shell error, not a lookup.
REPO_UNKNOWN = "UNKNOWN (not reported by `gh`; pass --repo owner/name)"


def range_tip(facts):
    """🔴 The commit the delta range ENDS at — ONE PREDICATE, ONE PLACE.

    Read by THE RANGE, which prints it, and by THE LEDGER, which hands it to a
    cross-repo auditor as part of the command only they can run. Open-coded at
    both sites it would be wrong at one of them in the same direction, which is
    `claude/RULES.md` -> consolidation-finds-bugs.

    🔴 IT NEVER RETURNS `HEAD`, and round 8 is where the last caller stopped.
    Round 7 moved the VERIFIED branch off the token and wrote "Only the
    VERIFIED branch hardcoded `HEAD`" — while the DEGENERATE branch, four lines
    above it in the same `if`/`elif`, still handed one out. `HEAD` resolves in
    the AUDITOR's tree, which is cut after this brief is assembled from a
    repository other sessions push to; a sha cannot move under them. That
    argument never depended on which branch produced the range.

    The degenerate case needs no special tip at all: `anchor_is_head` is only
    true when the head check PASSED, so `local_sha` is the verified PR head and
    the range renders as `<anchor>..<the commit the anchor abbreviates>` —
    visibly, and PERMANENTLY, a self-range. That is what makes the banner's
    "it can never contain anything, whatever the PR looks like" true in the
    auditor's tree and not merely in this one.
    """
    hc = facts.head_check
    if hc is not None and hc.ok:
        return hc.local_sha
    return hc.pr_sha if (hc is not None and hc.pr_sha) else TIP_PLACEHOLDER


def range_tip_is_a_sha(facts):
    """Does `range_tip` name a COMMIT, or the literal placeholder?

    🔴 ROUND 10 — THE TIP END OF ROUND 8's REFUSAL 1b, AND IT IS THE SAME
    DEFECT. `range_tip` answers `TIP_PLACEHOLDER` whenever the PR's head sha was
    never learned — a state this script MODELS, with its own reason string — and
    two sites interpolate that answer straight into a git rev spec: THE RANGE's
    ``Diff `<anchor>..<tip>` `` and THE LEDGER's cross-repo hand-over. Measured
    at `706a6b38`, cross-repo + no `headRefOid`, rc 0 and silent stderr:

        Diff **`28492af2..<the PR's head sha>`**
        …
        So the range above names the PR's head SHA outright — read from
        `gh pr view --json headRefOid` …

    two sentences after the brief's own explanation that this run never learned
    that field, and under THE LEDGER's "THE ATTRIBUTION GATE IS YOURS THIS
    ROUND — it is not optional" the same token appears inside a `git log` the
    auditor is told to run. `newest is not None` was read as "we have an
    anchor"; `repo_relation == "cross"` was read as "we have a tip". Both are
    the same mistake — a WEAKER fact read as a STRONGER one.
    """
    return range_tip(facts) != TIP_PLACEHOLDER


def unresolved_tip_note(facts, with_reason=True):
    """The block EVERY site printing `range_tip` must carry when it is no sha.

    🔴 ONE RULE, ONE PLACE, THREE CONSUMERS — THE RANGE's cross-repo branch,
    THE RANGE's could-not-verify branch, and THE LEDGER's cross-repo hand-over.
    Open-coded it would be right at the site the finding was filed against and
    absent at the siblings, which is the shape rounds 3, 5, 7, 8 and 9 each
    found somewhere else in this module.

    Empty string when the tip IS a sha, so a caller can concatenate it
    unconditionally and cannot forget the conditional. `with_reason=False` for
    the callers that have ALREADY printed `hc.reason` a few lines above — both
    RANGE branches do; the same string twice in a row reads as two different
    facts. THE LEDGER's hand-over prints it nowhere else, so it takes the
    default.
    """
    if range_tip_is_a_sha(facts):
        return ""
    hc = facts.head_check
    lookup = f"gh pr view {facts.pr} --json headRefOid"
    if facts.repo != REPO_UNKNOWN:
        lookup += f" --repo {facts.repo}"
    reason = (
        f"\n\n    {hc.reason if hc is not None else 'no check was made'}\n\n"
        if with_reason else " "
    )
    return (
        "🔴 **THE TIP OF THAT RANGE IS A PLACEHOLDER, NOT A SHA — the range as "
        "printed CANNOT BE RUN, and neither can any command below that repeats "
        f"it.** `{TIP_PLACEHOLDER}` is literal text, not a commit: this run "
        "never learned the PR's head."
        + reason
        + f"Resolve it yourself — `{lookup}` — and substitute the sha "
        "everywhere this brief spells that placeholder before diffing or "
        "measuring anything. A command carrying it is not a command that "
        "returned zero; it is one that never ran."
    )


def assumed_base_note(facts):
    """The block every site printing `base_ref` must carry when it was GUESSED.

    Empty when the PR's base branch was actually read, so a caller concatenates
    it unconditionally — the same shape as `unresolved_tip_note`, and for the
    same reason: a conditional a caller has to remember is one a caller forgets
    at the second site.

    🔴 ROUND 12 — THE CAUSE IS THE CALLER'S TO NAME, exactly as `no_sha_reason`
    is one field over. This block used to state "`gh` reported no
    `baseRefName`" unconditionally, which is false in `--claims-file` mode:
    `gh` is never consulted there, so it reported nothing. The fallback below
    is for a caller that genuinely cannot say, and it says so rather than
    picking one of the two causes.
    """
    if not facts.base_assumed:
        return ""
    why = facts.base_assumed_reason or (
        "`gh` was not consulted, or reported no `baseRefName` — this caller "
        "did not say which"
    )
    return (
        f"🔴 **THAT BASE BRANCH WAS ASSUMED, NOT READ** ({why}), so "
        f"`{facts.base_ref}` is this script's DEFAULT and not a fact about "
        "the PR. The base is what the PR is "
        "diffed against and — from round 2 — what `--not <base>` subtracts "
        "when the payload figure is measured, so a wrong one silently changes "
        "what you read, at rc 0. Check the PR's real base branch before "
        "believing any figure derived from it."
    )


def cross_repo_holds_neither_end(facts):
    """This checkout holds NEITHER end of the range — ONE PREDICATE, ONE PLACE.

    🔴 ROUND 10 — TWO CONSUMERS, AND THE SECOND ONE DID NOT ADOPT THE FIRST'S
    ORDERING. `render_range`'s cross-repo branch is ordered after the `hc.ok`
    branch on purpose, and its comment says why: `repo_relation` compares
    SLUGS, and a clone whose `origin` names a different repository can still
    hold the PR's head commit (a renamed remote, a mirror). The sha is the
    ground truth. `render_ledger` asked `repo_relation == "cross"` alone.

    Measured at `706a6b38` in exactly that state (cross-repo PR, the assembly
    checkout standing on the PR's head, round 3, rc 0, silent stderr): the
    ledger MEASURED — 4 commits, 13 added, 2 deleted — and threw the numbers
    away to print "which holds neither end of the range", two bars under THE
    RANGE's "verified at assembly time to be PR #900's head commit". Every
    clause of that sentence was false and the two sections contradicted each
    other inside one document.

    So the question both sites ask is this one, and it asks the head check
    FIRST: claim the structural impossibility only once the sha comparison has
    actually failed.
    """
    hc = facts.head_check
    if hc is not None and hc.ok:
        return False
    return facts.repo_relation == "cross"


def render_range(facts):
    if facts.round_no < 2:
        return "\n".join([
            "## THE RANGE",
            "",
            f"A FIRST, FULL audit: read the whole PR diff (`gh pr diff "
            f"{facts.pr}`) and the code it touches, not just the PR "
            "description.",
            "",
            f"Base branch: `{facts.base_ref}`.",
        ] + (["", assumed_base_note(facts)] if facts.base_assumed else []))
    # 🔴 `..HEAD` is only meaningful once HEAD has been shown to BE the PR's
    # head. Unverified, the range handed to the auditor points at whatever tree
    # the shared checkout happens to be standing on — reproduced against an
    # unrelated feature branch, where it read as an ordinary non-empty range.
    hc = facts.head_check
    # 🔴 AND `..HEAD` is not meaningful when the ANCHOR is HEAD. This section
    # does not consult the ledger, so a self-range used to be rendered here with
    # full confidence — "verified at assembly time to be PR #958's head commit"
    # over ``Diff `d9eb36a8..HEAD` `` with HEAD *being* `d9eb36a8`, followed by
    # "Do not re-audit the whole PR". An auditor obeying that diffs nothing,
    # finds nothing, and the stop-rule clause turns the empty result into "the
    # ladder ENDS". The banner is loud because the failure is silent.
    tip = range_tip(facts)
    if anchor_is_head(facts.prev_sha, hc):
        note = (
            "🔴 **DEGENERATE RANGE — EMPTY BY CONSTRUCTION. DO NOT AUDIT IT AND "
            "DO NOT REPORT IT CLEAN.**\n\n"
            f"    the anchor `{facts.prev_sha}` IS this checkout's HEAD "
            f"({hc.local_sha})\n\n"
            "Both ends of the range name the same commit, so it can never "
            "contain anything, whatever the PR looks like — and BOTH ends are "
            "spelled as shas, so that sentence is true in YOUR tree too. It "
            "was not while this branch handed out `..HEAD`: that token resolves "
            "where you are standing, so a commit landing between assembly and "
            "your audit made the range non-empty and the claim above false. A "
            "finding-free pass over this range is a fact about the RANGE and is "
            "NOT evidence about the code — do not let the stop-rule clause "
            "below convert it into \"the ladder ENDS\".\n\n"
            + directive("degenerate-range-causes")
        )
    # 🔴 ROUND 7 — THE THIRD INSTANCE OF THIS LADDER'S RECURRING CONFUSION, and
    # it is a TIME axis rather than a place one. Round 3 conflated the
    # OPERATOR'S CHECKOUT with the tree under audit; round 5 conflated the
    # ASSEMBLING PROCESS'S CWD with where the auditor stands. This pair: the
    # HEAD VERIFIED AT ASSEMBLY TIME versus the HEAD THE AUDITOR'S COMMAND WILL
    # RESOLVE.
    #
    # `hc.ok` means `git rev-parse HEAD` in THIS tree, at THIS moment, equalled
    # the PR's head. It says nothing about the tree the auditor types `..HEAD`
    # in: this same brief says `Dispatch with isolation: "worktree"` and that
    # the checkout it names is "not necessarily the one you are standing in",
    # and the cross-repo recipe hands them a BRANCH, not this sha. That tree is
    # cut AFTER assembly from a repo the brief itself describes as live under
    # another session — so a commit landing in between makes
    # `git diff <from>..HEAD` cover commits no claims block describes, while
    # the brief's own justification asserts the range was verified.
    #
    # Two tells that this is the same inversion and not a new one: the
    # UNVERIFIED branch below already does the safe thing (`tip = hc.pr_sha`),
    # and `emit_claims_skeleton` already prefers the resolved PR head. Round 7
    # wrote "Only the VERIFIED branch hardcoded `HEAD`" and round 8 measured
    # that the DEGENERATE one still did — both now read `range_tip`, which
    # cannot return the token at all.
    #
    # The ledger below still MEASURES `<anchor>..HEAD`, and correctly: that
    # command runs HERE, now, in the tree just verified. What is handed to
    # ANOTHER process, later, is a sha.
    elif hc is not None and hc.ok:
        note = (
            f"This checkout's HEAD is `{hc.local_sha}`, verified at assembly "
            f"time to be PR #{facts.pr}'s head commit — so the range above "
            "names that SHA and not `HEAD`. `HEAD` would resolve in YOUR "
            "worktree, which is cut after this brief was assembled from a "
            "repository other sessions are pushing to; the sha cannot move "
            "under you."
        )
    # 🔴 ROUND 8 — THE FOURTH INSTANCE OF THE RECURRING CONFUSION, and this one
    # is the REPOSITORY axis: the repo `gh` was asked about versus the repo
    # `git` was run in. Every git command this script issues goes to
    # `repo_dir` — the assembly checkout — while `gh` was asked about
    # `facts.repo`. Cross-repo those are different repositories, so the head
    # check is STRUCTURALLY unable to pass and the unverified branch below fired
    # every time, phrasing a permanent fact as an operator error: "this
    # checkout's HEAD is X, but the PR's head is Y", followed by "Resolve the
    # range in a tree that CONTAINS the PR's head". There is no such tree here
    # and there never will be, so the instruction is unfollowable and the
    # diagnosis sends the reader looking for a checkout that moved.
    #
    # 🔴 IT IS ORDERED AFTER THE `hc.ok` BRANCH ON PURPOSE. `repo_relation`
    # compares SLUGS, and a checkout whose `origin` names a different repository
    # can still hold the PR's head commit (a clone with a renamed remote, a
    # mirror). The sha is the ground truth; this branch only claims the
    # structural impossibility once the sha comparison has actually failed.
    #
    # 🔴 ROUND 10 — AND IT ASKS THAT ORDERING THROUGH A SHARED PREDICATE NOW.
    # `render_ledger` re-derived the same decision from `repo_relation` alone
    # and got it wrong for the renamed-remote clone; see
    # `cross_repo_holds_neither_end`.
    elif cross_repo_holds_neither_end(facts):
        # 🔴 ROUND 10 — THE SECOND SENTENCE IS CONDITIONAL, BECAUSE THE FACT IT
        # ASSERTS IS. "the range above names the PR's head SHA outright" was
        # unconditional, and `range_tip` hands out a PLACEHOLDER whenever the
        # head sha was never learned — so the brief said "read from `gh pr view
        # --json headRefOid`" two sentences after explaining that this run never
        # read that field.
        tail = (
            "So the range above names the PR's head SHA outright — read from "
            "`gh pr view --json headRefOid`, not resolved in any tree. Both of "
            "its ends exist in YOUR worktree, the one WHERE TO WORK told you to "
            "make, and neither exists here."
            if range_tip_is_a_sha(facts)
            else unresolved_tip_note(facts, with_reason=False)
        )
        note = (
            "🔴 **THIS CHECKOUT CANNOT BE STANDING ON THE PR — that is "
            "STRUCTURAL AND PERMANENT, not a checkout that moved.** WHERE TO "
            f"WORK above says why: the PR lives in `{facts.repo}` and this "
            "brief was assembled in "
            f"`{facts.cwd_repo_slug or facts.cwd_repo_dir}`, a DIFFERENT "
            "repository. No `git checkout` here can make the two agree, so do "
            "not go looking for one, and do not report this as a finding "
            "against the PR:\n\n"
            f"    {hc.reason if hc is not None else 'no check was made'}\n\n"
            + tail
        )
    else:
        # 🔴 ROUND 10 — THE PLACEHOLDER REACHES THIS BRANCH TOO. It is the
        # SAME-REPO spelling of the same state (no `headRefOid`), and the
        # widest reading of the finding is about `range_tip`'s answer, not
        # about the cross-repo branch that happened to be filed against.
        note = (
            "🔴 **COULD NOT VERIFY that this checkout is standing on the PR**, "
            "so `..HEAD` is NOT used above — it would name whatever tree the "
            "shared checkout is on:\n\n"
            f"    {hc.reason if hc is not None else 'no check was made'}\n\n"
            "Resolve the range in a tree that CONTAINS the PR's head before "
            "trusting any number derived from it."
        )
        # `with_reason=False` for the same reason the cross branch passes it:
        # `hc.reason` is already printed four lines above, and the same string
        # twice in a row reads as two different facts.
        tip_note = unresolved_tip_note(facts, with_reason=False)
        if tip_note:
            note += "\n\n" + tip_note
    return "\n".join([
        "## THE RANGE",
        "",
        f"A DELTA re-audit, round {facts.round_no}. Diff **`{facts.prev_sha}"
        f"..{tip}`** — the fix commits made since the tip round "
        f"{facts.claims_round} audited.",
        "",
        # 🔴 Y1. The scope instruction used to live INSIDE the generated line
        # above, where no whole-string pin could reach it — and inverting it to
        # "Re-audit the whole PR as well" passed the entire suite. The line
        # above now states the RANGE; this states the INSTRUCTION, and it is
        # pinned two-way like every other directive.
        directive("delta-scope"),
        "",
        note,
        "",
        f"Base branch: `{facts.base_ref}`.",
        "",
    ] + ([assumed_base_note(facts), ""] if facts.base_assumed else []) + [
        directive("delta-regressions"),
    ])


def render_checkout(facts):
    """🔴 THREE states — see `gather_worktree_kind`.

    The heading is deliberately state-INDEPENDENT ("THE CHECKOUT"), because the
    `no-fetch` invariant clause names this section by name and a heading that
    changes with the state would leave that clause pointing at nothing. Which
    KIND of checkout it is goes in the block, where it is a fact, and the
    consequence goes in the directive, where it is pinned.
    """
    dirty = (
        "could not read (`git status` failed)" if facts.dirty < 0
        else f"{facts.dirty} uncommitted path(s)"
    )
    wt = facts.worktree
    # 🔴 "tree is yours" was FALSE and is gone. This is the tree the ASSEMBLER
    # stood in; the auditor is dispatched elsewhere. See `gather_worktree_kind`.
    kind_line = {
        "shared": "SHARED — other sessions and agents are in this tree",
        "private": "PRIVATE linked worktree — belongs to the session that "
                   "assembled this brief, not to you",
    }.get(wt.kind, "🔴 COULD NOT DETERMINE")
    lines = [
        "## THE CHECKOUT — state at assembly time",
        "",
        f"    path   : {facts.cwd_repo_dir}",
        f"    kind   : {kind_line}",
        f"    branch : {facts.branch}",
        f"    dirty  : {dirty}",
        f"    read at: {facts.assembled_at}",
    ]
    if wt.kind == "private":
        lines += [
            f"    git dir: {wt.git_dir}",
            f"    common : {wt.common_dir}   <- differs, so this is a linked "
            "worktree",
        ]
    elif wt.kind == "unknown":
        lines += ["", f"    {wt.reason}"]
    lines += [
        "",
        directive(CHECKOUT_STATE_DIRECTIVE.get(
            wt.kind, CHECKOUT_STATE_DIRECTIVE["unknown"])),
    ]
    return "\n".join(lines)


def render_toolchain(facts):
    """🔴 The operator's checkout resolves the TOOLCHAIN and nothing else.

    Every command here used to interpolate `facts.cwd_repo_dir`, including the
    two that name the tree UNDER TEST. `scripts/gate.sh` resolves its own `ROOT`
    from `BASH_SOURCE`, so `nix develop <shared> -c bash <shared>/scripts/
    gate.sh` runs the suite in the SHARED CHECKOUT, on whatever branch it is
    standing on; `nix build <shared>#checks…` builds that flake ref's tree, not
    the auditor's. Both contradicted WHERE TO WORK three bars above, and the
    auditor then obeyed the very next sentence — "name the tier and the base
    sha" — and named the wrong sha.

    `nix develop {r}` is deliberately left pointing at the operator's checkout:
    that one is only resolving a dev shell (the toolchain), and it is the door
    the repo's own CLAUDE.md tells everyone to use.

    🔴 ROUND 5 — THE RATIONALE IS STATE-INDEPENDENT, AND THAT IS DELIBERATE.
    The round-2 fix moved the COMMANDS off `r` but left the sentence explaining
    WHY saying "runs the suite in the SHARED CHECKOUT" — a claim about a state
    THE CHECKOUT section may have just denied. In the private state that
    sentence is false, the auditor sees the brief refute its own reason two bars
    apart, and the round-2 finding re-opens in a new state: gate the un-mutated
    head, then obey the next sentence ("name the tier and the base sha") and
    name a tree that does not contain what was tested. This section knows
    nothing about where the auditor stands, so it says only what is true in
    every state — that copy is not yours and holds none of your mutations.
    `test_the_toolchain_reason_is_true_in_every_scenario` drives EVERY scenario
    that module knows and requires this section byte-identical across all of
    them — round 5 drove two, both same-repo, and round 6 measured that a
    cross-repo-only reword walked it.
    """
    r = facts.cwd_repo_dir
    return "\n".join([
        "## TOOLCHAIN — the exact commands, and the two ways they lie",
        "",
        "🔴 `<your worktree>` below is **your own copy** — the one WHERE TO WORK "
        f"told you to make. `{r}` appears ONLY as the argument to `nix develop`, "
        "where it resolves the dev shell and nothing else. Never point the gate "
        "or a `nix build` at it: `gate.sh` resolves its root from its own path, "
        "so running that copy runs the suite in a checkout that is NOT yours — "
        "the one this brief was assembled in, on whatever branch it is standing "
        "on, holding none of your mutations — and a `nix build <ref>#…` builds "
        "that ref's tree, not yours.",
        "",
        "Run a SUBSET of the suite:",
        "",
        "```",
        f"nix develop {r} -c python3 -m pytest <paths> -q -p no:cacheprovider",
        "```",
        "",
        "🔴 A bare `python3 -m pytest` failing with **`No module named "
        "pytest`** means you are in the WRONG SHELL, not that the suite is "
        "broken. This repo's `.envrc` is `use opencode`, which does not put "
        "pytest on PATH; a loaded direnv is not the dev shell. Do not report "
        "that as a finding and do not build an ad-hoc `nix-shell` around it.",
        "",
        "Run the whole dev-host gate (its EXIT STATUS is authoritative; also "
        "read each runner's own `RESULT:` line):",
        "",
        "```",
        f"nix develop {r} -c bash <your worktree>/scripts/gate.sh --tier both",
        "```",
        "",
        "🔴 The gate above is the DEV-HOST tier. **The tier the merge actually "
        "gates on is the sandbox one**, which builds from a store copy with no "
        "`.git` and is therefore blind to different things:",
        "",
        "```",
        "nix build <your worktree>#checks.x86_64-linux.pytests",
        "nix build <your worktree>#checks.x86_64-linux.nodetests",
        "```",
        "",
        "Name the tier and the base sha in any claim you make about the gate — "
        "\"the gate passed\" is true of one run, one tier, one base, and reads "
        "as a property of the change.",
        "",
        "`git --version` before you trust a range: `--remerge-diff` needs git "
        "≥ 2.35, and a git without it exits 128 with EMPTY output, which reads "
        "exactly like a clean zero.",
    ])


def payload_summary_line(facts, cumulative):
    """🔴 The line the auditor must carry — ONE WRITER, TWO CALLERS.

    The measured branch asks for it and so does the cross-repo hand-over, and
    round 8 wrote it out twice before the mutation battery objected: the second
    copy sat at a deeper indentation and made a battery target AMBIGUOUS, so a
    `replace(…, 1)` mutated the wrong site. Two copies of one sentence are two
    things to keep in step and, here, a second thing for a pattern to match.
    """
    return (f"round {facts.round_no} · payload lines changed THIS round: X "
            f"(since round 1: {cumulative}) · elapsed: Z")


def render_ledger(facts):
    lines = ["## THE LEDGER — payload attribution for this round", ""]
    # 🔴 ROUND 8 — CROSS-REPO IS "NOT MEASURABLE FROM HERE", NEVER "COULD NOT
    # MEASURE". The generic banner below describes a command that failed and
    # tells the reader to re-run it by hand — advice that, cross-repo, points
    # at the wrong repository. And it fires on EVERY delta round of such a PR,
    # because `measure_ledger` refuses the moment the head check is not ok and
    # the head check is structurally unable to pass. A section that is red
    # every single time is the permanently-red gate `claude/RULES.md` names:
    # the reader learns to skip it, and the ladder's own two-consecutive-zero
    # payload-attribution gate silently stops being computed by ANYONE.
    #
    # So this states the impossibility, hands over the exact command, says
    # WHERE it must run, and says out loud that the gate is the auditor's to
    # compute this round. Ordered before every other branch except round 1,
    # which has no ledger for a reason that has nothing to do with repos.
    #
    # 🔴 ROUND 10 — AND IT NOW ASKS `cross_repo_holds_neither_end`, NOT
    # `repo_relation` ALONE. `render_range`'s comment already said why the
    # cross-repo branch must be ordered after the head check: the relation
    # compares SLUGS, and a renamed remote or a mirror can name a different
    # repository while holding the PR's head commit. This site did not adopt
    # that ordering, so in exactly that state it DISCARDED a successful
    # measurement to print "which holds neither end of the range" — under a
    # RANGE section that had just said "verified at assembly time". One rule,
    # two places, wrong at the second.
    if facts.round_no >= 2 and cross_repo_holds_neither_end(facts):
        # 🔴 ROUND 10 — THE HAND-OVER IS THE LOUDEST COMMAND IN THE BRIEF ("it
        # is not optional and nobody else can compute it") and it repeats
        # `range_tip`, so when that tip is a PLACEHOLDER the auditor is handed a
        # `git log` that cannot run, under a banner telling them it is
        # mandatory. The note goes IMMEDIATELY above the fence, not at the end
        # of the section, because the fence is what gets copied.
        tip_note = unresolved_tip_note(facts)
        base_note = assumed_base_note(facts)
        return "\n".join(lines + [
            "🔴 **NOT MEASURABLE FROM HERE — and that is STRUCTURAL, so it is "
            "true of every round of this PR and not a failure of this run.** "
            f"The PR lives in `{facts.repo}`; this brief was assembled in "
            f"`{facts.cwd_repo_slug or facts.cwd_repo_dir}`, a DIFFERENT "
            "repository, which holds neither end of the range. A number "
            "measured here would be a measurement of another project, so none "
            "is printed.",
            "",
            "🔴 **THE ATTRIBUTION GATE IS YOURS THIS ROUND — it is not "
            "optional and nobody else can compute it.** The ladder ends on two "
            "consecutive rounds of ZERO payload lines; a gate nobody evaluates "
            "never fires, and the ladder then runs forever on scaffolding. Run "
            "this in the worktree WHERE TO WORK told you to make — a worktree "
            f"of YOUR clone of `{facts.repo}`, where `{facts.base_ref}` names "
            "THAT repository's base branch and not this checkout's:",
            "",
        ] + ([tip_note, ""] if tip_note else []) + (
            [base_note, ""] if base_note else []
        ) + [
            "```",
            "git -C <your worktree> log --numstat --format= --remerge-diff "
            f"{facts.prev_sha}..{range_tip(facts)} --not {facts.base_ref}",
            "```",
            "",
            "Require rc 0, silent stderr and a NON-EMPTY range before believing "
            "any figure it prints — a failed command is not a zero. Then "
            "classify the files yourself (payload is what the PR exists to "
            "ship; scaffolding is the tests, fixtures and notes a round wrote "
            "to guard it) and carry this line in your summary:",
            "",
            "```",
            payload_summary_line(facts, "Y"),
            "```",
        ])
    led = facts.ledger
    if led is None:
        lines += [
            "Not measured: a first, full audit has no previous round to "
            "attribute against. Start the ledger at your round 2.",
        ]
        return "\n".join(lines)
    if led.reason:
        lines += [
            "🔴 **COULD NOT MEASURE** — and a failed command is NOT a zero, so "
            "no number is printed here:",
            "",
            f"    {led.reason}",
            "",
            "Re-run the command by hand, and require rc 0, silent stderr and a "
            "non-empty range before believing any figure it prints.",
        ]
        return "\n".join(lines)

    # 🔴 ROUND 8 — PROVENANCE MAY NAME A SHA; IT MAY NOT NAME A TOKEN THE
    # READER RESOLVES DIFFERENTLY. This line is the only copyable command left
    # in the brief whose `HEAD` means something else where the auditor stands —
    # and the paragraph below invites them to re-run it ("if the number looks
    # large, that is the first thing to check"). Re-run in their worktree,
    # `<anchor>..HEAD` covers any commit that landed since assembly, so a longer
    # file list reads as the previous round UNDER-reporting its payload.
    #
    # `local_sha` is not a weaker provenance claim than `HEAD`: at the moment
    # the command ran they were the SAME COMMIT — `measure_ledger` refuses
    # unless the head check passed — so the sha reports exactly what was
    # measured AND survives being copied. Only a run with no head check at all
    # keeps the token, and there `HEAD` is the honest answer, because no sha
    # was ever established.
    hc = facts.head_check
    measured_tip = hc.local_sha if (hc is not None and hc.ok) else "HEAD"
    lines += [
        f"`git log --numstat --format= --remerge-diff {facts.prev_sha}.."
        f"{measured_tip} --not {facts.base_ref}` over {led.commits} commit(s):",
        "",
        "```",
        f"{'added':>7} {'deleted':>8}  path",
    ]
    for path, (a, d) in sorted(led.files.items(), key=lambda kv: -sum(kv[1])):
        lines.append(f"{a:>7} {d:>8}  {path}")
    lines += [
        f"{led.added:>7} {led.deleted:>8}  = {len(led.files)} file(s), "
        f"{led.added + led.deleted} line(s) changed",
        "```",
        "",
    ]
    if not led.files:
        lines += [
            "🔴 Zero changed lines over a NON-EMPTY range. That is a real "
            "measurement, not a failure — but check that `--not "
            f"{facts.base_ref}` is not swallowing this round's own commits "
            "before you act on it.",
            "",
        ]
    lines += [
        "🔴 **Classify these files yourself — this script deliberately does "
        "not.** Payload is what the PR exists to ship; scaffolding is the "
        "tests, fixtures and notes a round wrote to guard it. For a code change "
        "the payload is source and a `.md` is not; **for a docs or skill PR the "
        "payload IS the `.md`**. A pathspec cannot do this — `':!*test*'` "
        "swallows `attestation/`, `latest/` and `inspector/` while keeping "
        "`FooTest.java` and `login.cy.ts`. **Ambiguous is not zero**: the gate "
        "does not fire and the ladder continues.",
        "",
        "Then carry this line in your summary, with X filled in from the "
        "classification above:",
        "",
        "```",
        payload_summary_line(
            facts, led.cumulative if led.cumulative is not None else "Y"),
        "```",
        "",
        "⚠ `<base>` here is `" + facts.base_ref + "` **as it stands in this "
        "checkout**, and it is the ONLY end of that command that can mean "
        "something different where you are standing — both ends of the range "
        "itself are shas. This script does not fetch (that would be a write to "
        "a checkout it does not own), so a stale base re-reports upstream work "
        "as this round's payload. If the number looks large, that is the first "
        "thing to check.",
    ] + ([""] + [assumed_base_note(facts)] if facts.base_assumed else [])
    if led.cumulative is None:
        # 🔴 TWO mechanisms, and they need opposite fixes: there was no anchor
        # sha to measure from, or there WAS one and the measurement failed. The
        # no-anchor sentence used to be printed for both, sending an operator
        # who already had a round-1 anchor off to add one — reproduced with a
        # round-1 block whose `audited=` sha makes `rev-list` exit 128. Same
        # empty-result trap this module refuses everywhere else.
        if led.cumulative_reason:
            lines += [
                "",
                "⚠ The cumulative figure (Y, since round 1) is **NOT "
                "MEASURED** — an anchor sha WAS found, and measuring from it "
                "failed:",
                "",
                f"    {led.cumulative_reason}",
                "",
                "So this is a broken measurement, not a missing anchor: adding "
                "another `audit-claims` block will not fix it.",
            ]
        else:
            lines += [
                "",
                "⚠ The cumulative figure (Y, since round 1) is **NOT "
                "MEASURED**: no `audit-claims` block carried a round-1 anchor "
                "sha. It is not derivable from the base, which is a different "
                "quantity.",
            ]
    return "\n".join(lines)


def render_checklist(facts):
    """The checklist, READ from the skill rather than restated here.

    🔴 One rule, one place. An earlier draft paraphrased the axes inline for
    delta rounds ("risks, regressions, assumptions, …") — a second copy of a
    block that is pinned in `scripts/tests/test_audit_ladder_stop_rule.py` and
    that has already been silently shaved once, with a commit message claiming
    the opposite. The paraphrase would have drifted with nothing watching.
    """
    lines = ["## AUDIT FOR", ""]
    if facts.round_no >= 2:
        lines += [
            "First, per prior claim above: **actually fixed / partially / not "
            "/ made worse**. Then work the same axes as a full audit, scoped "
            "to the range:",
            "",
        ]
    lines.append(facts.checklist or (
        "⚠ COULD NOT INLINE the checklist — "
        "`~/.claude/skills/audit-pr/SKILL.md` was not readable from here. Read "
        "its **Audit for:** section and work through every numbered item."
    ))
    return "\n".join(lines)


def render_output_contract(facts):
    return "\n".join([
        "## OUTPUT",
        "",
        "Findings by severity (🔴 deploy-blocking / 🟡 should-fix / 🟢 nit), "
        "each in the format required above. Then the ledger line. Then a "
        "**verdict** — safe to merge / merge after fixing 🔴 / needs rework — "
        "which is advisory for the human and is **not** the ladder's stop "
        "signal: a round can return \"safe to merge\" and still report real "
        "defects. Flag anything you could not verify, and say so plainly rather "
        "than reporting it as covered.",
    ])


def render_brief(facts):
    kind = (
        "FIRST, FULL adversarial audit" if facts.round_no < 2
        else f"DELTA re-audit — ROUND {facts.round_no}"
    )
    head = "\n".join([
        f"# {kind} — PR #{facts.pr} in `{facts.repo}`",
        "",
        f"**{facts.title}**",
        f"{facts.url}",
        "",
        "Assembled by `scripts/audit-dispatch.py`. The sections below are "
        "generated from live facts; the NON-NEGOTIABLE block is verbatim and "
        "identical in every brief this script emits.",
    ])
    parts = [
        head,
        render_worktree_directive(facts),
        render_range(facts),
        render_claims(facts),
        render_checkout(facts),
        render_toolchain(facts),
        render_invariants(),
        render_checklist(facts),
        render_ledger(facts),
        render_output_contract(facts),
    ]
    return f"\n\n{_bar()}\n\n".join(p for p in parts if p) + "\n"


def emit_claims_skeleton(facts, head_sha):
    """A correctly-formed block for the operator to paste into the PR comment.

    Emitted rather than described, because the next round's assembler reads ONLY
    this shape and a hand-typed near-miss is refused.

    🔴 TWO defects lived in one line here.

    1. With no prior block — a round 1 `--emit-claims`, which is *the remedy the
       refusal advertises* — `prev_sha` was None and the placeholder
       `<the sha round 1 audited>` was interpolated INTO the header. The parser
       then read `audited=<the`, found no `..`, and yielded `audited_from=''`,
       `audited_to='<the'`; the next round's brief said ``Diff `<the..HEAD` ``
       with rc 0 and no refusal at all. The parser already accepts a BARE sha as
       the audited tip, and for round 1 that tip is exactly the quantity the
       header carries, so the bare form is what is emitted.
    2. `head_sha` used to be the operator checkout's `git rev-parse --short
       HEAD` — which is only the PR's head if the shared checkout happens to be
       standing on it, and is otherwise a record of some other branch's tip
       going into the field the NEXT round anchors on. The caller now passes the
       PR's own head sha.

    🔴 A THIRD, from round 3: the `<from>` written here is `facts.emit_from`
    (`emit_anchor` — the newest block's `audited_to`, i.e. the tip THIS round's
    audit read), NOT `facts.prev_sha` (`range_anchor` — what THIS round diffed
    from). They are different shas and the single-anchor spelling is wrong on
    one side whichever one you pick: write `prev_sha` here and the next round
    re-audits the previous round's fix commits as well.

    🔴 A FOURTH, from round 4: `emit_from` is ALSO whatever `--audited` said,
    and for ROUND 1 that flag is the only thing that can supply it. Round 1 has
    no previous block, so with the flag omitted this falls back to the bare
    `audited=<head>` spelling — which records the head this round's FIXES
    produced in the field the next round anchors on, making that round's range
    empty by construction. The fallback is kept (a bare sha is still readable,
    and refusing here would break the remedy the delta refusal advertises) and
    `main` warns LOUDLY on exactly that spelling instead.
    """
    if facts.emit_from:
        audited = f"{facts.emit_from}..{head_sha}"
    else:
        # Round 1 with no `--audited`: nothing here knows the tip the round
        # read, so the header carries ONE sha — the head, assumed. The parser
        # and `round_one_anchor` both read a bare sha as the audited tip, so
        # the ledger stays measurable; the assumption is what `main` warns
        # about, because it is an assumption and not a measurement.
        audited = head_sha
    return "\n".join([
        # 🔴 THE LEGEND, added in round 5. The code and the docstrings above
        # were adjudicated correct and consistent; the failure is at the HUMAN
        # end, because a person reasons from the emitted block and the block
        # carried no key to its own two fields. The operator wrote them the
        # wrong way round in two consecutive briefs and the agent had to
        # override both. It sits OUTSIDE the fence, so the parser drops it.
        "  legend: `<from>` = the tip THIS round's audit READ · `<to>` = the "
        "head THIS round's FIXES produced. Different shas — `<from>` is older.",
        "",
        f"```audit-claims round={facts.round_no} audited={audited}",
        "1. <one line per thing this round's fixes CLAIM to have addressed — "
        "WHAT was claimed, never WHY it is correct>",
        "2. <one line, same rule>",
        "```",
    ])


# 🔴 THE ROUND-TRIP GUARD — round 5. `--audited` accepted ANY string and the
# emitter's own parser then truncated it, in silence, at both ends of the
# pipeline. Measured through the real code at `dd601793`:
#
#     --audited                emitted header            parses back as
#     `abc 123`                audited=abc 123..<head>   from='', to='abc'
#     `<the tip … read>`       audited=<the tip …>..…    from='', to='<the'
#     `e06461f7..dd601793`     audited=e06461f7..dd6…    from='e06461f7',
#                                                        to='dd601793..<head>'
#
# No warning at emit, none at parse, and the self-range guard cannot fire on any
# of them. Worse, `degenerate-range-causes` INSTRUCTS the operator to re-emit
# "with `--audited <the tip that round actually read>`", so pasting the
# placeholder reproduces it — which is how the round-3 bug re-opened.
#
# The check is the pipeline itself: emit the block, feed it back through
# `parse_claims_blocks`, and require what comes back to RECONSTRUCT the header
# that was written. Pinned to what is actually PRINTED rather than to a
# validation regex that could drift from the emitter.
#
# 🔴 IT COMPARES THE HEADER WITH ITSELF, NEVER WITH `emit_from` — and that is
# the whole reason it isolates. The first draft asserted
# `parsed_from == facts.emit_from`, which turned it into a SECOND assertion
# about WHICH field the emitter uses; that claim is owned by `emit_anchor` and
# `test_emit_claims_records_the_tip_this_round_audited_not_the_range_anchor`,
# and duplicating it made mutants N3 and Y7 cascade into five unrelated tests
# and would have answered a wrong-field bug in production with a misleading
# "it does not parse" refusal. Measured by the battery, which reported both as
# EXTRA-KILLER. This asks only whether the printed field survives the FORMAT.
#
# 🔴 NAMED BLIND SPOT ONE: a well-formed token that is not a commit
# (`zzzzzzzz`) round-trips perfectly and is NOT caught.
#
# 🔴 AND THE REASON GIVEN HERE FOR THREE ROUNDS WAS FALSE. It asserted that
# this script performs no sha resolution at all in that checkout, on the
# grounds that it will not write to one. It does resolve it: `gather_ledger`
# runs
# `git -C <that checkout> rev-list --count <anchor>..HEAD` and
# `git -C <that checkout> log --numstat … <anchor>..HEAD`, both of which resolve
# exactly this token in exactly that checkout, and READING is not writing. The
# gap is real; the stated cause was not, and a false cause is what makes the
# next round close a hole that was never there.
#
# THE TRUE REASON, which the code never stated: this script deliberately never
# FETCHES. So the assembly checkout can legitimately be missing an object that
# is perfectly present on the PR — a routine state, since it may be an
# unfetched worktree of a branch someone else pushed — and a `cat-file` refusal
# there would reject a CORRECT value. The downstream cost of leaving it open is
# bounded and loud: `rev-list` exits 128 one round later and the ledger prints
# COULD NOT MEASURE naming the command that failed.
#
# 🔴 NAMED BLIND SPOT TWO, and it is why `main` checks the INPUT separately:
# this function compares the printed header LINE against a parse of that same
# printed header LINE. `_EMITTED_AUDITED` is `re.M` + `$`; `parse_claims_blocks`
# reads line by line. Both sides are line-oriented, so a NEWLINE inside the
# value pushes content onto the next line where NEITHER side can see it and
# they agree — a control built out of the step it doubts. Do not widen this
# function to cover it; the input check does, before this runs.
EMIT_REFUSAL_HEADER = "🔴 REFUSING TO EMIT an `audit-claims` block"

# The RAW text after `audited=`, to end of line — deliberately NOT `\S+`, which
# is what `_HEADER_AUDITED` uses and is exactly the truncation being detected.
_EMITTED_AUDITED = re.compile(
    r"^`{3,}audit-claims[^\n]*?\baudited=([^\n]*)$", re.M
)


def emitted_block_reads_back_as_written(skeleton):
    """-> None when the block this run would print reads back intact, else why.

    Pure, and driven by the tests directly as well as through `main`: a refusal
    is only trustworthy if it can be exercised with no PR and no git.
    """
    m = _EMITTED_AUDITED.search(skeleton)
    if not m:
        return "the block this run would emit carries no `audited=` field at all"
    raw = m.group(1).rstrip()
    blocks, malformed = parse_claims_blocks([skeleton])
    if len(blocks) != 1:
        why = "; ".join(malformed) or "no reason reported"
        return (
            "the block this run would emit does not parse as exactly one "
            f"`audit-claims` block ({len(blocks)} found: {why})"
        )
    b = blocks[0]
    back = f"{b.audited_from}..{b.audited_to}" if b.audited_from else b.audited_to
    if back != raw:
        return (
            f"the header carries `audited={raw}`, but this script's OWN parser "
            f"reads it back as `<from>`={b.audited_from!r}, "
            f"`<to>`={b.audited_to!r} — reconstructing {back!r}. The field is "
            "read as one whitespace-free token, so anything from the first "
            "space onward is silently DISCARDED"
        )
    for label, field in (("`<from>`", b.audited_from), ("`<to>`", b.audited_to)):
        if ".." in field:
            return (
                f"{label} came back as {field!r}, which carries an embedded "
                "`..`. The header splits on the FIRST dot-pair, so a range "
                "written into one half leaves the other half holding a range "
                "— and that corruption is copied forward into the next round's "
                "own block"
            )
        if "<" in field or ">" in field:
            return (
                f"{label} came back as {field!r}, which is a PLACEHOLDER and "
                "not a sha. The next round would anchor its delta range and "
                "its whole ledger on it, and nothing downstream reports it "
                "malformed"
            )
    return None


# --------------------------------------------------------------------------- #
# The warn-only completeness check
# --------------------------------------------------------------------------- #

def _norm(text):
    """Whitespace-normalised, so a re-wrap is not a loss but a reword is.

    A hand-edited brief gets re-wrapped by editors and by paste; a clause that
    survived the edit intact should not be reported missing because a line
    break moved.
    """
    return " ".join((text or "").split())


def missing_clauses(brief):
    """Clause ids whose text is not present in `brief` (whitespace-normalised).

    🔴 WARN-ONLY BY DESIGN. `claude/RULES.md` says a permanently-red gate trains
    everyone to click through it, and a brief a human deliberately edited is not
    a defect. Blocking here would make the tool refuse to run over that edit.

    🔴 IT WAS ALSO UNREACHABLE. The docstring said "it exists for a hand-edited
    `--out` file" and nothing ever read an `--out` file back: the only caller
    passed the string `render_brief` had just built out of `INVARIANT_CLAUSES`,
    so every clause was present BY CONSTRUCTION and the check could not fail for
    any input a user could supply. The suite reached it only by monkeypatching
    `render_brief` to a lossy stub — which is the `unreachable-guards` shape in
    `claude/RULES.md`, and it mattered: the hand-written brief that dispatched
    the audit of this very script HAD been edited, several clauses shortened and
    the checklist paraphrased, and the shipped check could not notice.

    Two real inputs now reach it: `--check FILE` (a brief someone edited) and
    the READ-BACK of `--out` (which also catches a write that lost bytes).
    """
    haystack = _norm(brief)
    return [c.id for c in INVARIANT_CLAUSES if _norm(c.text) not in haystack]


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

REFUSAL_HEADER = "🔴 REFUSING TO EMIT a delta re-audit brief"


def _read_checklist(repo_dir):
    """The nine-item checklist, read from the skill rather than duplicated.

    One rule, one place: restating the checklist here would give it a second
    copy to drift from, and the block has already been silently shaved once.
    """
    for cand in (
        Path(repo_dir) / "claude" / "skills" / "audit-pr" / "SKILL.md",
        Path.home() / ".claude" / "skills" / "audit-pr" / "SKILL.md",
    ):
        try:
            body = cand.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        m = re.search(r"^\*\*Audit for:\*\*\n(.*?)(?=\n## )", body, re.M | re.S)
        if m:
            return "**Audit for:**\n" + m.group(1).rstrip()
    return None


def build_parser():
    ap = argparse.ArgumentParser(
        prog="audit-dispatch.py",
        description="Assemble an adversarial-audit brief for a PR and print it.",
    )
    # `nargs="?"` exists ONLY so `--check FILE` can run with no PR at all — it
    # inspects a file and consults neither `gh` nor `git`. Every other path
    # still requires the number, and `main` says so rather than proceeding with
    # `pr=None`.
    ap.add_argument("pr", type=int, nargs="?", help="the PR number")
    ap.add_argument("--round", dest="round_no", type=int, default=1,
                    help="round number; >=2 assembles a DELTA re-audit brief")
    ap.add_argument("--repo", help="owner/name, when the PR is not in the cwd's repo")
    ap.add_argument("--out", help="write the brief to this file instead of stdout")
    ap.add_argument("--check", metavar="FILE",
                    help="check an EXISTING brief file for missing invariant "
                         "clauses and exit; consults no PR and no git")
    ap.add_argument("--emit-claims", action="store_true",
                    help="also print an audit-claims block skeleton to paste "
                         "into this round's PR comment")
    # 🔴 THE ONE THING `--emit-claims` CANNOT DERIVE. It runs AFTER this round's
    # fixes have landed, so every sha it can see locally is a fix tip; the tip
    # the round's audit READ is only in the operator's head (or in the dispatch
    # that started the round). For round >= 2 it is recoverable from the
    # previous block's `<to>`, which is what `emit_anchor` does — for ROUND 1
    # there is no previous block and nothing to recover it from, which is
    # exactly the hole this flag closes.
    ap.add_argument("--audited", metavar="SHA",
                    help="the tip THIS round's audit read — written as the "
                         "`<from>` of the `--emit-claims` block. Round 1 has "
                         "no previous block to derive it from; without this, "
                         "HEAD is ASSUMED and said so on stderr. ONE sha, and "
                         "exactly one token: any whitespace at all — a "
                         "TRAILING NEWLINE included — is refused on the INPUT, "
                         "because a newline moves content off the header line "
                         "where the round trip below cannot see it. A value "
                         "with `..` or a placeholder is refused too: the "
                         "emitted block is fed back through this script's own "
                         "parser and one that does not survive is REFUSED.")
    ap.add_argument("--claims-file",
                    help="read claims-block text from this file instead of the "
                         "PR's comments (offline/testing seam)")
    return ap


def check_brief_file(path, out_stream, err_stream):
    """`--check FILE` — run the clause check over a brief someone EDITED.

    🔴 This is what makes `missing_clauses` reachable in production. Warn-only,
    like its in-process sibling: it reports and returns 0, because the operator
    who edited the brief may have meant to.
    """
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as e:
        print(f"cannot read --check file: {e}", file=err_stream)
        return 2
    gone = missing_clauses(text)
    if gone:
        print(
            f"⚠ {path} is missing invariant clause(s): " + ", ".join(gone)
            + "\n  This is a WARNING, not a refusal. Each is a clause a "
              "hand-written brief was MEASURED to lose; re-add them, or "
              "re-generate the brief and edit less.",
            file=err_stream,
        )
    else:
        print(
            f"{path}: all {len(INVARIANT_CLAUSES)} invariant clause(s) present.",
            file=out_stream,
        )
    return 0


def main(argv=None, runner=real_runner, cwd=None, stdout=None, stderr=None,
         checklist_reader=None):
    # `checklist_reader` is injected by the test suite so no test depends on
    # whether THIS HOST happens to have the skill deployed under ~/.claude —
    # a suite that reads the ambient home is not hermetic, and the sandbox gate
    # tier runs with a different one.
    checklist_reader = checklist_reader or _read_checklist
    parser = build_parser()
    args = parser.parse_args(argv)
    out_stream = stdout or sys.stdout
    err_stream = stderr or sys.stderr
    cwd = cwd or os.getcwd()

    if args.check:
        return check_brief_file(args.check, out_stream, err_stream)
    if args.pr is None:
        parser.error("the PR number is required (or use --check FILE)")

    # 🔴 An EMPTY `--audited` is the fifth row of round 5's measured table: it
    # is falsy, so it falls straight through to `emit_anchor(newest)` and the
    # flag is IGNORED IN SILENCE — including at round 1, where there is no
    # block to fall back to and the bare `audited=<head>` spelling is written
    # instead. Refused rather than warned: there is no reading of `--audited ""`
    # that the operator meant.
    if args.audited is not None and not args.audited.strip():
        print("\n".join([
            f"{EMIT_REFUSAL_HEADER}: `--audited` was given an EMPTY value "
            f"({args.audited!r}).",
            "",
            "  An empty string is falsy, so it is indistinguishable from the "
            "flag being omitted: the anchor silently falls back to the previous "
            "block's `<to>`, or — at round 1, where there is no previous block "
            "— to the bare `audited=<head>` spelling whose next round is EMPTY "
            "BY CONSTRUCTION.",
            "",
            "  Pass the sha this round's audit actually read, or omit the flag.",
        ]), file=err_stream)
        return 4

    # 🔴 ROUND 6 — THE ROUND TRIP IS STRUCTURALLY BLIND TO A NEWLINE, so the
    # ONE character that moves content off the header LINE gets its own check
    # on the INPUT, before any of that machinery runs.
    #
    # `emitted_block_reads_back_as_written` compares the printed header LINE
    # against a parse of that same printed header LINE. Both sides are
    # line-oriented (`_EMITTED_AUDITED` is `re.M` + `$`; `parse_claims_blocks`
    # reads line by line), so anything pushed onto the NEXT line is invisible
    # to both and they agree — a control built out of the step it doubts. The
    # empty check above misses it too: `.strip()` is truthy for `aaaa1111\n`.
    #
    # Measured through `main()` at `3619fe68`: `--audited $'aaaa1111\n'` exited
    # 0 and emitted ``audited=aaaa1111`` — the BARE round-1 spelling, `<to>`
    # dropped on the floor and a stray `..<head>` line left in the body, with
    # `parse_claims_blocks` reporting `malformed=[]`. Round 3 then reads
    # `emit_anchor` = `aaaa1111`, and round 4 re-audits round 2's fixes on top
    # of round 3's, one round downstream and in silence.
    #
    # 🔴 THE PREDICATE IS `split() != [value]`, NOT `len(split()) != 1`. The
    # obvious spelling does NOT catch this: `'aaaa1111\n'.split()` is
    # `['aaaa1111']` — length ONE — because `str.split()` with no argument
    # discards empty fields. It misses a trailing newline, a trailing space and
    # a trailing `\r` alike. Only comparing against the value ITSELF asks the
    # question that matters: is this exactly one token with nothing around it?
    #
    # Deliberately independent of the round trip: it asks about the INPUT, the
    # round trip asks about the printed BLOCK, and coupling them is the N3/Y7
    # cascade round 5 measured and refused.
    if args.audited is not None and args.audited.split() != [args.audited]:
        print("\n".join([
            f"{EMIT_REFUSAL_HEADER}: `--audited` was given a value carrying "
            f"WHITESPACE ({args.audited!r}).",
            "",
            "  It must be exactly ONE whitespace-free token. A NEWLINE is the "
            "dangerous one: it moves everything after it off the header LINE, "
            "where neither this script's parser nor the emitted block's own "
            "round-trip check can see it — the block degrades to the bare "
            "`audited=<sha>` spelling, `<to>` is lost, and nothing downstream "
            "reports it malformed.",
            "",
            "  Pass a single sha — `--audited abc1234`, not `--audited "
            "\"$(some command)\"`, whose output ends in a newline.",
        ]), file=err_stream)
        return 4

    # A flag that silently does nothing is the shape this module refuses
    # everywhere else: `--audited` is written by `--emit-claims` and by nothing
    # else, so passing it to a plain assembly run is a no-op the operator would
    # otherwise read as "recorded".
    if args.audited and not args.emit_claims:
        print(
            "⚠ --audited names the tip this round's audit read and is only "
            "ever written by --emit-claims, which this run does not pass. The "
            "flag changed NOTHING; the range below is anchored on the previous "
            "round's block as usual.",
            file=err_stream,
        )

    repo_dir, cwd_slug = gather_repo_facts(runner, cwd)
    worktree = gather_worktree_kind(runner, repo_dir)
    branch, dirty = gather_checkout_state(runner, repo_dir)

    if args.claims_file:
        try:
            comment_texts = [Path(args.claims_file).read_text(encoding="utf-8")]
        except OSError as e:
            print(f"cannot read --claims-file: {e}", file=err_stream)
            return 2
        claims_source = f"`{args.claims_file}`"
        # 🔴 ROUND 12 — `baseRefName` WAS HARDCODED `"main"` HERE, AND THAT
        # SWITCHED THE ASSUMED-BASE BANNER OFF IN THE ONE MODE THAT ALWAYS
        # ASSUMES. `base_assumed` is `not data.get("baseRefName")`, so the
        # hardcode answered "the base was READ" for a run that consulted no
        # `gh` at all — the exact state three comments in this module and its
        # test module describe as permanent for this mode. Measured at
        # `88b4105c`: `--round 3 --claims-file <f>` printed "Base branch:
        # `origin/main`." with no banner, at rc 0 and silent stderr, while the
        # same run over a `gh` payload with the field deleted printed it.
        #
        # This mode does not learn `baseRefName` for the same reason it does
        # not learn `headRefOid`, so it now omits BOTH and lets `or "main"`
        # below be the visible default it always was.
        data = {"title": f"PR #{args.pr}", "url": ""}
        # 🔴 THE CALLER KNOWS WHICH CAUSE IT IS. See `verify_head_is_the_pr`:
        # naming both leaves the reader with a list instead of an answer, and
        # this branch is the one that decided not to consult `gh`.
        no_sha_reason = (
            "this run is `--claims-file` mode, which consults no `gh` and so "
            "never learns the PR's `headRefOid`"
        )
        # 🔴 THE SAME PER-CAUSE RULE, ONE FIELD OVER. `assumed_base_note` used
        # to state "`gh` reported no `baseRefName`" unconditionally, which is
        # itself false here — `gh` was never asked. Following `no_sha_reason`
        # rather than inventing a second mechanism.
        base_assumed_reason = (
            "this run is `--claims-file` mode, which consults no `gh` and so "
            "never learns the PR's `baseRefName`"
        )
    else:
        data, comment_texts = gh_pr_facts(runner, args.pr, args.repo)
        claims_source = f"PR #{args.pr}'s comments"
        no_sha_reason = (
            "`gh pr view` was consulted and reported no `headRefOid` for this "
            "PR"
        )
        base_assumed_reason = (
            "`gh pr view` was consulted and reported no `baseRefName` for "
            "this PR"
        )
        if data.get("_error"):
            print(f"🔴 `gh pr view {args.pr}` failed: {data['_error']}",
                  file=err_stream)
            return 3

    # 🔴 THREE states. `pr_repo` is None when neither `url`, `isCrossRepository`
    # nor `--repo` could answer it; falling back to `cwd_slug` there (as this
    # did) makes the comparison compare the cwd with ITSELF and silently yields
    # "same repo" — the branch that recommends `isolation: "worktree"`.
    pr_repo = pr_slug(data, args.repo)
    if pr_repo and cwd_slug:
        repo_relation = "same" if pr_repo == cwd_slug else "cross"
    else:
        repo_relation = "unknown"
    repo = pr_repo or REPO_UNKNOWN

    blocks, malformed = parse_claims_blocks(comment_texts)
    newest = newest_block(blocks)

    # 🔴 ROUND 10 — A REFUSAL THAT PRESCRIBES A COMMAND MUST NOT REFUSE THAT
    # COMMAND. Both refusals below hand the operator a `--emit-claims` re-run as
    # their mechanical remedy, and both then refused it, byte for byte, because
    # the refusal is ordered before the emit half and reads the SAME unreadable
    # block. Measured at `706a6b38`:
    #
    #   REFUSAL 1b, over a `round=2` block whose header is `audited=..`
    #     `… 900 --round 3`                                   -> rc 2
    #     `… 900 --round 2 --emit-claims --audited abc12345`  -> rc 2, the same
    #                                                            refusal, empty
    #                                                            stdout
    #   REFUSAL 1, at round 3 with no block at all
    #     `… 900 --round 3`                                   -> rc 2
    #     `… 900 --round 2 --emit-claims`                     -> rc 2
    #
    # (REFUSAL 1's remedy is sound at round 2 — `--round 1 --emit-claims` needs
    # no block. It is unrunnable from round 3 up, and the operator reads the
    # repeat as their own typo.)
    #
    # The two halves of a run are INDEPENDENT: rendering a DELTA brief needs an
    # anchor to diff from, and emitting a block needs only a head sha and a
    # `<from>` the operator supplies. So the refusal keeps its scope — no brief
    # is emitted and the rc is unchanged — and the run CONTINUES to the emit,
    # printing the very block the remedy promised. Ordered this way rather than
    # by rewriting the remedy into prose, for the reason every other refusal
    # here cites: a mechanical path a machine can run beats a sentence a human
    # has to interpret.
    #
    # `brief_refused` doubles as the return code, so an emit-side refusal
    # (rc 4) still wins — it is a different failure and needs a different fix.
    brief_refused = None

    # ------------------------------------------------------------------ #
    # 🔴 REFUSAL 1 — a delta round with nothing to be framed on.
    # ------------------------------------------------------------------ #
    if args.round_no >= 2 and newest is None:
        why = (
            "  reason: " + "\n          ".join(malformed)
            if malformed else
            f"  reason: no `audit-claims` block in any of the "
            f"{len(comment_texts)} comment(s) read"
        )
        print("\n".join([
            f"{REFUSAL_HEADER} for round {args.round_no} of PR #{args.pr}.",
            "",
            "  looked for: a fenced block of the form",
            "",
            "      ```audit-claims round=<n> audited=<sha>..<sha>",
            "      1. <what was claimed fixed>",
            "      ```",
            "",
            f"  looked in : {claims_source}",
            why,
            "",
            "  ⚠ `gh pr view --json comments` returns ISSUE comments only. A "
            "block posted as a REVIEW comment, as a reply inside a review "
            "thread, or in the PR's own DESCRIPTION is invisible to this "
            "script and is not counted above — check there before concluding "
            "nobody posted one.",
            "",
            "  An empty \"what was claimed fixed\" section silently turns a "
            "DELTA re-audit into a blind full audit — a different thing, which "
            "would then read as covered. So this is refused, not emitted.",
            "",
            f"  Fix: run `audit-dispatch.py {args.pr} --round "
            f"{args.round_no - 1} --emit-claims --audited <the tip that round "
            "read>`, fill the skeleton in, and post it as a comment on the PR. "
            "That run refuses its own brief for this same reason and STILL "
            "prints the block — the two halves are independent. Or run this "
            "round as an explicit first, full audit with no --round.",
        ]), file=err_stream)
        if not args.emit_claims:
            return 2
        brief_refused = 2

    # 🔴 A structural problem that still yielded a usable block must not be
    # silent either. The refusal above only fires when there is NO block, so a
    # comment holding one readable block and one unreadable fence would
    # otherwise pass as complete.
    if malformed and newest is not None:
        print(
            "⚠ the claims text held "
            f"{len(malformed)} thing(s) this script could not read cleanly:\n  "
            + "\n  ".join(malformed)
            + "\n  A block WAS found and is used below — but check that the "
              "claims it carries are all of them.",
            file=err_stream,
        )

    # 🔴 TWO anchors, ONE field. See `range_anchor` / `emit_anchor`: reading
    # `audited_to` here made the delta range EMPTY BY CONSTRUCTION, because
    # `--emit-claims` stamps the head the block is posted at into that same
    # field. Reproduced live on devrc #958 and hermetically.
    prev_sha = range_anchor(newest)

    # ------------------------------------------------------------------ #
    # 🔴 REFUSAL 1b — a block that PARSED but yields no ANCHOR.
    # ------------------------------------------------------------------ #
    # 🔴 FOUND IN ROUND 8 BY SWEEPING FOR THE LADDER'S RECURRING SHAPE rather
    # than from a finding, and it is the FIFTH instance: `newest is not None`
    # was read as "we have something to diff from". Those are two different
    # facts. `audited=..` parses cleanly — the regex wants `\S+` and `..`
    # satisfies it — and yields `audited_from=''`, `audited_to=''`, so
    # `range_anchor` answers None while `newest` is a perfectly good block with
    # readable claims.
    #
    # Measured at `28492af2`, `--round 3` over such a block, rc 0, silent
    # stderr:
    #
    #     THE RANGE : Diff **`None..1111…5555`** — the fix commits made since
    #                 the tip round 2 audited.
    #                 This checkout's HEAD is `1111…5555`, verified at
    #                 assembly time to be PR #900's head commit
    #     THE LEDGER: Not measured: a first, full audit has no previous round
    #                 to attribute against. Start the ledger at your round 2.
    #
    # Both halves are wrong in the way this module keeps refusing. The range
    # hands over a literal Python `None` inside a git rev spec — a command that
    # cannot run — under the brief's most confident banner; and THE LEDGER
    # states the ROUND-1 cause ("no previous round") for a round-3 brief that
    # HAS one, which is the false-cause shape fixed three times already
    # (`no_sha_reason`, the cumulative reason, the empty-range causes) at the
    # one site nobody swept.
    #
    # Refused rather than banner-ed, for REFUSAL 1's own reason: a delta round
    # with no anchor is not a delta round, and rendering one anyway produces a
    # document that reads as covered.
    if args.round_no >= 2 and newest is not None and not prev_sha:
        print("\n".join([
            f"{REFUSAL_HEADER} for round {args.round_no} of PR #{args.pr}.",
            "",
            f"  A block WAS found (round={newest.round_no}, "
            f"{len(newest.items)} claim(s)) and it PARSED — but its `audited=` "
            "field carries no sha this script can read, so there is nothing to "
            "diff FROM.",
            "",
            "  This is NOT the round-1 case. A first, full audit legitimately "
            "has no anchor; here a previous round exists and its anchor is "
            "unreadable, and the two need opposite fixes.",
            "",
            "  Without a refusal the brief renders `Diff `None..<the PR's "
            "head`` — a literal `None` inside a git rev spec, under \"verified "
            "at assembly time\" — and THE LEDGER reports the round-1 reason.",
            "",
            f"  Fix: edit that comment so the header reads `audited="
            "<from>..<to>`, or re-run "
            f"`audit-dispatch.py {args.pr} --round {newest.round_no} "
            "--emit-claims --audited <the tip that round read>` and post the "
            "block it prints. That run refuses its own brief for this same "
            "reason — it reads the same unreadable header — and STILL prints "
            "the block, because emitting one needs the sha you just supplied "
            "and nothing from the comment.",
        ]), file=err_stream)
        if not args.emit_claims:
            return 2
        brief_refused = 2

    # 🔴 `--audited` OVERRIDES THE WRITER'S ANCHOR AND NEVER THE READER'S. It
    # states the tip THIS round's audit read — the same quantity `emit_anchor`
    # recovers from the previous block, supplied instead of derived, and for
    # ROUND 1 the only way to have it at all. `prev_sha` (what this run DIFFS
    # from) is deliberately untouched: assembly runs before the audit and
    # `--emit-claims` after it, and collapsing the two anchors is the defect
    # round 3 fixed.
    emit_from = args.audited or emit_anchor(newest)
    claims = list(newest.items) if newest else []
    claims_round = newest.round_no if newest else None
    if newest is not None and newest.round_no >= args.round_no:
        print(
            f"⚠ the newest claims block says round={newest.round_no}, and you "
            f"asked for round {args.round_no}. Using it anyway — but check you "
            "are not re-auditing a round that already ran.",
            file=err_stream,
        )
    # 🔴 THE MIRROR OF THE WARNING ABOVE, and its absence was the asymmetry: a
    # block from a LATER round warned, a block from a much EARLIER one was
    # silent. `--round 7` over a `round=2` block puts round 2's claims under
    # WHAT WAS CLAIMED FIXED, anchors the range on round 2's sha, and frames
    # the whole audit on them — with nothing anywhere saying that four rounds
    # are missing.
    #
    # A WARNING AND NEVER A REFUSAL, for a reason the refusals above do not
    # share: skipping a round NUMBER is legitimate (a round can be abandoned,
    # or numbered by hand), and `gh pr view --json comments` returns ISSUE
    # comments only — so a block posted as a review comment is invisible here
    # and would make a refusal wrong. The operator can tell those apart and
    # this script cannot.
    elif newest is not None and newest.round_no < args.round_no - 1:
        gap = args.round_no - 1 - newest.round_no
        print(
            f"⚠ the newest claims block says round={newest.round_no}, and you "
            f"asked for round {args.round_no} — {gap} round(s) in between "
            "posted no `audit-claims` block this script can see. The claims "
            "below are that OLDER round's and the range is anchored on its "
            "sha, so this is a delta over everything since, not since the "
            "previous round. Check for a later block posted as a REVIEW "
            "comment, which `gh pr view --json comments` does not return.",
            file=err_stream,
        )

    # 🔴 The FOURTH read rule, computed once and used by all three consumers
    # that were measuring the operator's checkout instead of the PR: the ledger,
    # the range section, and the `audited=` sha `--emit-claims` stamps.
    head_check = verify_head_is_the_pr(
        runner, repo_dir, data.get("headRefOid"), no_sha_reason
    )

    ledger = None
    # 🔴 ROUND 10 — THE DEFAULT IS RECORDED, not silently indistinguishable from
    # a reading. `or "main"` is a guess, and `--not <base>` is what decides which
    # commits count as this round's payload.
    base_ref = data.get("baseRefName") or "main"
    base_assumed = not data.get("baseRefName")
    base_for_range = f"origin/{base_ref}"
    if args.round_no >= 2 and prev_sha:
        ledger = measure_ledger(
            runner, repo_dir, prev_sha, base_for_range, head_check
        )
        anchor = round_one_anchor(blocks)
        if ledger.reason is None and anchor:
            cum = measure_ledger(
                runner, repo_dir, anchor, base_for_range, head_check
            )
            if cum.reason is None:
                ledger = ledger._replace(cumulative=cum.added + cum.deleted)
            else:
                # 🔴 Carry the REASON. Dropping it made the brief print one
                # specific, false cause ("no block carried a round-1 anchor")
                # for a measurement that failed with an anchor in hand.
                ledger = ledger._replace(cumulative_reason=cum.reason)

    facts = Facts(
        pr=args.pr,
        repo=repo,
        title=data.get("title") or "(no title)",
        base_ref=base_ref if args.round_no < 2 else base_for_range,
        url=data.get("url") or "",
        round_no=args.round_no,
        cwd_repo_dir=repo_dir,
        cwd_repo_slug=cwd_slug,
        repo_relation=repo_relation,
        worktree=worktree,
        branch=branch,
        dirty=dirty,
        prev_sha=prev_sha,
        emit_from=emit_from,
        claims=claims,
        claims_round=claims_round,
        checklist=checklist_reader(repo_dir),
        ledger=ledger,
        assembled_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%MZ"),
        claims_source=claims_source,
        head_check=head_check,
        base_assumed=base_assumed,
        base_assumed_reason=base_assumed_reason,
    )

    # 🔴 THE REFUSAL'S SCOPE IS THE BRIEF, AND ONLY THE BRIEF. A refused run
    # renders nothing, writes no `--out` file and runs no clause check over a
    # document that does not exist — it falls straight through to the emit half
    # below, which is the remedy the refusal just prescribed.
    if brief_refused is None:
        brief = render_brief(facts)

        if args.out:
            Path(args.out).write_text(brief, encoding="utf-8")
            print(f"wrote {len(brief):,} chars to {args.out}", file=err_stream)
        else:
            print(brief, file=out_stream)

        # -------------------------------------------------------------- #
        # REFUSAL 2's opposite number: WARN, never block.
        # -------------------------------------------------------------- #
        # 🔴 Checked against what is ON DISK when there is a disk copy, not
        # against the string just built. Checking the in-memory brief could
        # only ever pass: it was rendered FROM `INVARIANT_CLAUSES` a few lines
        # earlier, so every clause was present by construction and this warning
        # was unreachable for any input a user could supply. The read-back also
        # catches a write that lost bytes. `--check FILE` is the other real
        # input.
        checked_what, checked_text = "the assembled brief", brief
        if args.out:
            try:
                checked_text = Path(args.out).read_text(encoding="utf-8")
                checked_what = args.out
            except OSError as e:
                print(f"⚠ could not re-read {args.out} to check it: {e}",
                      file=err_stream)
        gone = missing_clauses(checked_text)
        if gone:
            print(
                f"⚠ {checked_what} is missing invariant clause(s): "
                + ", ".join(gone)
                + "\n  This is a WARNING, not a refusal — the brief is still "
                  "emitted. Re-add them by hand, or re-run without --out "
                  "edits.",
                file=err_stream,
            )

    if args.emit_claims:
        # 🔴 The PR's OWN head, never the shared checkout's. This sha is what
        # the NEXT round anchors its range and its ledger on, so stamping the
        # local HEAD here recorded whatever branch this checkout was standing
        # on — `main`'s tip, in the reproduction — as the sha this round
        # audited.
        head_sha = (data.get("headRefOid") or "")[:8]
        if not head_sha:
            head_sha = TIP_PLACEHOLDER
            print(
                "⚠ --emit-claims could not read the PR's head sha "
                "(`headRefOid`), so the block below carries a PLACEHOLDER. "
                "Replace it with the sha this round actually audited before "
                "posting — the next round reads this field and refuses on a "
                "near-miss.",
                file=err_stream,
            )
        elif not head_check.ok:
            print(
                "⚠ --emit-claims stamped the PR's head sha, which is correct — "
                "but this checkout is NOT standing on it, so re-read the claims "
                f"you are about to write: {head_check.reason}",
                file=err_stream,
            )
        # 🔴 ASKED OF THE ANCHOR THE NEXT ROUND WILL ACTUALLY USE — not of
        # `facts.emit_from` directly. Spelled on `emit_from`, this guard was
        # STRUCTURALLY UNREACHABLE for the one spelling it most needed to
        # cover: a ROUND-1 `--emit-claims` has no previous block, `emit_from`
        # is None, `same_commit` answers False for an empty operand — and the
        # BARE block that then gets written carries the identical hazard,
        # because `range_anchor` falls back to `<to>` and `<to>` IS the head it
        # was just stamped at. Round 1 is the remedy the delta refusal
        # ADVERTISES, so the unreachable case was the advertised one.
        # Reproduced hermetically and live: this PR's own round-1 block is
        # `audited=abc41024` (bare, the round-1 FIX tip), and a round-2 brief
        # assembled from it renders DEGENERATE.
        next_anchor = range_anchor(
            ClaimsBlock(args.round_no, facts.emit_from or "", head_sha, [])
        )
        if same_commit(next_anchor, head_sha):
            if facts.emit_from:
                # `<from>` and `<to>` are both present and name one commit.
                # Emitted silently until round 3: `--round 2 --emit-claims` and
                # `--round 3 --emit-claims` both printed
                # `audited=d9eb36a8..d9eb36a8` with no warning at all.
                print(
                    "🔴 --emit-claims is writing a SELF-RANGE: "
                    f"`audited={head_sha}..{head_sha}`. The two ends are the "
                    "same commit, so the block records a round whose fixes "
                    "changed NOTHING, and the next round's delta range will be "
                    "EMPTY BY CONSTRUCTION — which reads as a clean round. "
                    "Commit and push this round's fixes, then re-run; do not "
                    "post this block as it stands.",
                    file=err_stream,
                )
            else:
                print(
                    "🔴 --emit-claims is writing a BARE "
                    f"`audited={head_sha}`, and that sha is the PR's CURRENT "
                    "head — because --audited was not passed, so HEAD was "
                    "ASSUMED to be the tip this round's audit read. It is "
                    "usually not: --emit-claims runs AFTER the round's fixes "
                    "have landed, so HEAD is the head those fixes PRODUCED. "
                    "The next round reads a bare sha as its anchor and would "
                    f"diff `{head_sha}..HEAD` — both ends the same commit, "
                    "EMPTY BY CONSTRUCTION, which reads as a clean round. "
                    "Re-run with `--audited <the tip this round's audit "
                    "actually read>`; the block then carries `<that tip>.."
                    f"{head_sha}` and the next round sees this round's fix "
                    "commits. (If nothing has landed since the audit read the "
                    "tree, the sha is right and there is no delta to assemble "
                    "anyway.)",
                    file=err_stream,
                )
        # 🔴 THE ROUND TRIP, and it is asked of the BLOCK THAT WOULD BE PRINTED
        # — not of `args.audited` against a validation regex, which is a second
        # spelling of the emitter that can drift from it. It compares the
        # header with ITSELF and never with `emit_from`: see
        # `emitted_block_reads_back_as_written` for why that isolation matters.
        #
        # Gated on `facts.emit_from` because the BARE spelling deliberately has
        # no `<from>`, and its hazard is a different one that already has its
        # own loud warning six lines above — gating any wider would make the
        # placeholder `<to>` of a headRefOid-less run a refusal instead of the
        # warning it already is. Everything the gate does cover comes from a
        # human: `--audited` typed on the command line, or `emit_anchor`
        # recovered from a block someone typed into a PR comment.
        skeleton = emit_claims_skeleton(facts, head_sha)
        if facts.emit_from:
            why = emitted_block_reads_back_as_written(skeleton)
            if why:
                print("\n".join([
                    f"{EMIT_REFUSAL_HEADER} for round {args.round_no} of PR "
                    f"#{args.pr}: it would not survive this script's own "
                    "parser.",
                    "",
                    f"  {why}.",
                    "",
                    "  the block that was NOT emitted:",
                    "",
                    "      " + "\n      ".join(skeleton.splitlines()),
                    "",
                    "  Re-run with `--audited <a single sha, no spaces and no "
                    "`..`>` — the tip THIS round's audit read. A placeholder "
                    "in angle brackets is not a sha; neither is a range.",
                ]), file=err_stream)
                return 4
        print("\n" + _bar(), file=out_stream)
        print("Paste this into the PR comment for this round, so the NEXT "
              "round can read it:\n", file=out_stream)
        print(skeleton, file=out_stream)
        if brief_refused is not None:
            # 🔴 SAID AT THE POINT THE OPERATOR IS LOOKING, not only in the
            # refusal scrolled off above: this run printed a BLOCK and no
            # BRIEF, and it exits non-zero for the brief it withheld.
            print(
                "⚠ this run emitted the block above and NO BRIEF — the "
                "refusal printed earlier still stands and it exits "
                f"{brief_refused}. Post the block, then re-run the round you "
                "actually wanted.",
                file=err_stream,
            )
    return brief_refused if brief_refused is not None else 0


if __name__ == "__main__":
    sys.exit(main())
