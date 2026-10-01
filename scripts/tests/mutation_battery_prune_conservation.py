#!/usr/bin/env python3
"""Mutation battery for rule (r) — `--prune` conservation in `handoff_doc.py`.

    python3 scripts/tests/mutation_battery_prune_conservation.py

🔴 NOT COLLECTED BY THE GATE, ON PURPOSE — and the filename is the mechanism:
`scripts/run-tests.sh` collects `test_*.py` only. This is a MANUAL instrument,
run when rule (r) changes. It rewrites tracked source in place once per mutant
and runs the whole of `test_handoff_doc.py` each time, so it is not something
two sessions can run concurrently.

WHY IT IS COMMITTED AT ALL — the reason `mutation_battery_resume_state.py` and
`mutation_battery_handoff_archive_and_cap.py` both give. Results produced from a
scratchpad script are a CLAIM; a committed battery is evidence, and the next
auditor can re-run it instead of rebuilding one and imagining a different set.

READ BEFORE TRUSTING A VERDICT
------------------------------
  * THE BASELINE RUNS FIRST and aborts on a red tree, or on a run that printed
    no summary line. A zero is indistinguishable from a probe wired to nothing.
  * EVERY ROW NAMES THE TEST WHOSE OWN ASSERTION MUST GO RED. A mutant killed
    only by a neighbouring guard is `KILLED-WRONG-REASON` and is counted with
    the survivors — green for the wrong reason is not a kill, and all four
    rule (r) markers return the SAME exit code, so "it refused" proves nothing
    about WHICH guard refused.
  * 🔴 THE WHOLE FILE IS RUN EVERY TIME, never a `-k` slice. `claude/RULES.md`
    records a battery whose `-k` filter excluded the only test that could kill a
    mutant and reported it SURVIVED — a harness defect dressed as a finding.
    The cost is ~160 s per mutant; the alternative is a result you cannot read.
  * A mutant whose pattern is not found EXACTLY ONCE is `NOT-APPLIED` and counts
    as a survivor. Silent non-application is how a battery reports a clean sweep
    of mutations it never made.
  * `PYTHONDONTWRITEBYTECODE=1` IS FORCED ON THE CHILD, and it is not hygiene.
    CPython validates a cached module on whole-second mtime + size, so a
    same-length edit landing in the same second as the last import is invisible:
    the test would import the ORIGINAL bytecode and the mutant would score
    SURVIVED without ever executing. Several rows below are same-length edits.
  * Sources are restored in a `finally`. Check `git status` anyway if it dies
    hard.

WHAT THE FIRST RUN FOUND, kept because it is the reusable part
--------------------------------------------------------------
Run 1 scored `killed=10 killed-wrong-reason=1 survived=1 of 12`, and NEITHER
non-kill was a defect in the battery:

  * 🔴 **R2 SURVIVED, AND THE LINE IT MUTATED WAS DEAD.**
    `conservation_problems` opened with `if not durable: return ()` as the rule's
    apparent arming decision. Inverting it left the whole suite green — because
    the branch below is a comprehension over `durable`, so an empty `durable`
    already yields `()`. That fast path decided nothing it had not already
    decided. It is deleted, with the measurement recorded beside its absence, and
    R1/R2 now mutate `for target in durable`, which is where the arming actually
    lives: mirrors on ONE expression (empty it / widen it to every removal).
    THE REUSABLE PART: a row filed as "the guard" that SURVIVES is evidence about
    the CODE first. Re-anchoring it on a line that does decide something is the
    fix; re-labelling the row is not.
  * 🔴 **R12 WAS FILED AGAINST THE WRONG TEST, AND ITS PREMISE WAS REFUTED.** It
    was written believing a marker RENAME can only be seen by a LITERAL pin — the
    survival `TestThePruneRuleReachesTheSkill`'s header records — so a literal pin
    was written here too. Measured: renaming `ARCHIVE_MARKER_NONE` reds the
    DERIVED loop (the new value is not in the doc) and leaves the literal pin
    GREEN. The only case the literal uniquely caught was a rename PROPAGATED to
    the doc, which is a coordinated edit rather than a defect, so that test was
    deleted and the row re-aimed at the derived loop.
    THE REUSABLE PART: `KILLED-WRONG-REASON` is a finding about the LEDGER, and
    "a lesson recorded for a sibling guard applies here too" is a hypothesis, not
    a measurement.

WHAT THE `--archive-write` ROWS FOUND, kept for the same reason
---------------------------------------------------------------
The writer half's first subset run scored `killed=8 survived=1 not-applied=1 of 10`,
and NEITHER non-kill was noise:

  * 🔴 **R14 SURVIVED, AND THE CONJUNCT IT MUTATED WAS DEAD — R2 ALL OVER AGAIN.**
    The write branch opened `if args.archive_write and not archive_is_the_doc and
    (...)`, reading as its "never append into the doc itself" decision. Mutating
    that conjunct to `True` left the whole suite green, because
    `conservation_problems`' FIRST branch refuses `archive_is_the_doc`
    unconditionally and the call site passes the flag straight through — so the
    conjunct could only make `archive_append` run one pointless time before a
    refusal that was already certain, and nothing is written on that path. The
    conjunct is deleted with the measurement recorded beside its absence, and R14
    is deleted as a DUPLICATE of R7, which mutates the guard that does the
    deciding.
    THE REUSABLE PART: this is the second time in one rule that a row filed as
    "the guard" SURVIVED and the finding was about the CODE. When a new branch's
    condition repeats a test the callee already makes unconditionally, the repeat
    is not defence in depth — it is a second copy of one predicate.
  * 🔴 **R20 WAS `NOT-APPLIED (no summary line)`, AND THAT IS THE HARNESS CONTROL
    EARNING ITS PLACE.** Its mutant spliced `*(() and (` into the path-limited
    commit call, which leaves the opening `git(` unclosed — a SyntaxError, so
    pytest printed no summary at all. It scored as a NON-KILL rather than a kill,
    which is the whole point of reading the CONTENT instead of the exit code: a
    malformed mutant that stops the suite COLLECTING would otherwise look exactly
    like a mutant every test caught, and vouch for nothing. Re-written as a
    well-formed one-site edit of a single complete expression.
    THE REUSABLE PART: a mutant spliced into a multi-line call's argument list can
    balance its own parentheses while unbalancing the CALL's. Mutate a complete
    expression on one line.

WHAT THE ROWS COVER, as a ledger rather than a count
----------------------------------------------------
    R1/R2   the ARMING expression, both directions (never arm / arm on
            everything). R2 is what the negative control test exists for: a
            guard that refuses every prune passes every refusal test in the
            suite.
    R3      the SHARED predicate — `durable_removals` is what arms the rule and
            what the disclosure counts; breaking it must not be silent.
    R4      the COMPARISON's width: narrowed to the durable half, which is the
            design error the asymmetry exists to prevent.
    R5/R6   the normalisation, both directions (too strict / satisfied by
            anything).
    R7/R8   the self-archive walk: switched off, and made a NAME check rather
            than an IDENTITY check.
    R9      the unreadable arm's code — 16 (a verdict) vs 3 (operational).
    R10     the refusal's own exit code, 16 vs rule (q)'s 15.
    R11     the usage refusal for an inert `--archive`.
    R12     the doc seam: a renamed marker must red it.
"""
from __future__ import annotations

import os
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]

#: 🔴 NAMED `SCRIPT`, SINGULAR, BECAUSE `test_mutation_battery_anchors.py`
#: READS IT. That module statically pins every `old` anchor at exactly-once
#: in `SCRIPT`, so a reformatted line is caught at the moment it lands rather
#: than by whoever next runs the sweep and reads a NOT-APPLIED row. Every row
#: here targets this one file, so there is no `TARGETS` map.
SCRIPT = ROOT / "scripts/lib/handoff_doc.py"
SUITE = "scripts/tests/test_handoff_doc.py"

#: 🔴 ROW SHAPE IS `(id, why, killer, old, new)` AND THE ORDER IS NOT FREE —
#: `test_mutation_battery_anchors.py::_anchors` reads `row[0]` and `row[3]`,
#: so a table with `old` anywhere but the 4th field is one that module cannot
#: parse, and its exactly-once check then passes over a battery it never
#: read. `mutants-round0-attribution-rate.py` had to be reshaped for exactly
#: this reason; this one is born in the convention.
MUTANTS: list[tuple[str, str, str, str, str]] = [
    (
        'R1-never-arm',
        'the ARMING predicate switched OFF: a durable removal no longer needs an '
        'archive, i.e. `origin/main`\'s behaviour restored. 🔴 THE MIRROR OF R2 '
        'ON THE SAME EXPRESSION, which is what makes the pair a measurement of '
        'the arming rather than of a fast path: one empties the set rule (r) '
        'refuses on, the other widens it to every removal.',
        'TestADurablePruneWithNoArchiveIsRefused::test_it_is_refused_and_writes_NOTHING',
        '            for target in durable\n',
        '            for target in ()\n',
    ),
    (
        'R2-always-arm',
        'the ARMING predicate switched ON for EVERYTHING: every removal needs an '
        'archive, which is the permanently-red gate the negative control exists '
        'for. 🔴 ITS FIRST SPELLING WAS `if not durable: return ()` -> `if '
        'False:` AND IT SURVIVED A GREEN SUITE — the comprehension iterates '
        '`durable`, so an empty one already yields `()` and that fast path '
        'decided nothing. The dead line is deleted and the arming is now '
        'mutated where it actually lives.',
        'TestADurablePruneWithNoArchiveIsRefused::test_a_NON_durable_prune_with_no_archive_is_still_ACCEPTED',
        '            for target in durable\n',
        '            for target in plan.removed\n',
    ),
    (
        'R3-shared-predicate-empty',
        "`durable_removals` — the ONE list rule (f)'s disclosure and rule (r)'s trigger both read — made empty",
        'TestADurablePruneWithNoArchiveIsRefused::test_it_is_refused_and_writes_NOTHING',
        '    return tuple(t for t in plan.removed if t.reason)\n',
        '    return tuple(t for t in plan.removed if False)\n',
    ),
    (
        'R4-compare-only-the-durable-half',
        "the COMPARISON narrowed to the durable half — the design error the asymmetry exists to prevent, inheriting the predicate's floor",
        'TestTheArchiveIsCheckedOverEVERYRemovedLine::test_a_NON_durable_removal_missing_from_the_archive_refuses',
        '        for target in plan.removed\n',
        '        for target in durable\n',
    ),
    (
        'R5-archive-compared-verbatim',
        'the archive read WITHOUT `_norm_line`, so a re-indented copy stops counting — too strict, and disagrees with the prune matcher one function away',
        'TestAnArchiveThatHoldsTheContentLetsThePruneLand::test_whitespace_is_collapsed_and_NOTHING_ELSE_IS',
        '    return {_norm_line(ln) for ln in archive_text.splitlines() if ln.strip()}\n',
        '    return {ln for ln in archive_text.splitlines() if ln.strip()}\n',
    ),
    (
        'R6-containment-always-satisfied',
        'the containment test made unconditionally true — any file, empty included, conserves everything',
        'TestTheArchiveIsNeverWrittenAndCannotBeTheDoc::test_an_EMPTY_archive_is_not_a_bypass',
        '        if _norm_line(target.line) not in held\n',
        '        if False\n',
    ),
    (
        'R7-self-archive-guard-off',
        'the self-archive walk reopened: `--archive <the doc>` satisfies containment trivially, because the check runs before the prune is written',
        'TestTheArchiveIsNeverWrittenAndCannotBeTheDoc::test_pointing_the_archive_AT_THE_DOC_is_refused',
        '    if archive_is_the_doc:\n',
        '    if False:\n',
    ),
    (
        'R8-self-archive-by-NAME-not-identity',
        'the self-archive guard made a NAME check instead of an IDENTITY one — the spelled-guard shape, walkable by a symlink',
        'TestTheArchiveIsNeverWrittenAndCannotBeTheDoc::test_the_self_archive_refusal_is_on_IDENTITY_not_on_a_spelling',
        '            archive_is_the_doc = archive.resolve() == doc.resolve()\n',
        '            archive_is_the_doc = archive == doc\n',
    ),
    (
        'R9-unreadable-archive-is-operational',
        "an unreadable archive reported as an OPERATIONAL failure (3) rather than as rule (r)'s verdict (16) — a false conservation claim is about the prune",
        'TestTheArchiveIsNeverWrittenAndCannotBeTheDoc::test_an_unreadable_archive_is_RULE_R_s_verdict_not_an_OPERATIONAL_one',
        '                archive_error = str(exc)\n',
        '                return EXIT_FAIL\n',
    ),
    (
        'R10-exit-code-collapses-onto-rule-q',
        "16 collapsed onto rule (q)'s 15, so the two remedies become "
        "indistinguishable to a caller branching on the number. 🔴 ITS ANCHOR WAS "
        "WIDENED BY ONE LINE WHEN `--archive-write` LANDED, AND THE GUARD IS WHAT "
        "CAUGHT IT. That change added a SECOND `return EXIT_PRUNE_UNCONSERVED` (the "
        "read-back arm), indented four deeper — and `str.count` matches SUBSTRINGS, "
        "so the 16-space line CONTAINS the 12-space anchor and the row went to 2x. "
        "`test_mutation_battery_anchors.py` failed on it in the same commit. The "
        "lesson is the reusable part: a bare `return <CONST>` line is never a "
        "stable anchor, because any deeper-indented copy of it matches.",
        'TestADurablePruneWithNoArchiveIsRefused::test_it_is_refused_and_writes_NOTHING',
        '            )\n            return EXIT_PRUNE_UNCONSERVED\n',
        '            )\n            return EXIT_PRUNE_REFUSED\n',
    ),
    (
        'R11-inert-archive-flag-accepted',
        "the usage refusal for an `--archive` with no removal switched off, so "
        "an inert flag reads on the run as a conservation claim. 🔴 ITS ANCHOR "
        "WAS WIDENED BY A THIRD CONJUNCT WHEN RULE (s) LANDED, AND THE GUARD IS "
        "WHAT CAUGHT IT — `--autoevict` also removes lines, so an archive beside "
        "it is not inert and the predicate had to grow `and not args.autoevict`. "
        "The anchor went to 0x, i.e. the row would have reported "
        "`!! PATTERN OCCURS 0x — NOT APPLIED` and scored a SURVIVOR while "
        "testing nothing; `test_mutation_battery_anchors.py` failed on it in CI "
        "the same hour. Same lesson R10 records one row up, in its other shape: "
        "an anchor on a CONDITION is only as stable as the predicate, so a rule "
        "that widens one owes this file a re-anchor. The mutation now kills the "
        "FIRST conjunct alone — the narrowest expression that can be wrong — "
        "rather than the whole line.",
        'TestTheArchiveFlagUsageContract::test_an_archive_without_a_prune_is_a_USAGE_refusal',
        '    if args.archive is not None and args.prune is None and not args.autoevict:\n',
        '    if False and args.prune is None and not args.autoevict:\n',
    ),
    # ---- rule (r)'s WRITER half: `--archive-write` --------------------------
    # 🔴 ROWS ADDED TO *THIS* BATTERY RATHER THAN A NEW ONE, AND THAT IS A GATE
    # DECISION AS MUCH AS A TIDINESS ONE. A new single-site battery is a new
    # SKIP GROUP in `test_mutation_battery_anchors.py` (its multi-site negative
    # control has nothing to truncate), and an unpinned skip group is a GUARD 2
    # failure reported as `failed=0` — the trap the SIXTH `EXPECTED_SKIPS` entry
    # in `scripts/run-tests.sh` records this battery itself paying a CI round for.
    # Extending the existing file creates no new skip group, so that ledger is
    # untouched. It is also the honest shape: these rows mutate the same file, for
    # the same rule, and the reader half's rows are the ones that have to stay
    # green beside them.
    (
        'R13-write-flag-not-an-opt-in',
        'the writer armed on EVERY `--archive` run, so a caller who never asked '
        'for it gets a SECOND written and committed file. 🔴 THE HAZARD #1960 '
        'NAMED BY NAME, inverted: that change argued the tool must not become a '
        'second writer of a file nobody reviewed, and the answer here is an '
        'explicit flag rather than a default. This row is what makes "opt-in" a '
        'measured property instead of a sentence in a help string.',
        'TestTheWriteFlagCreatesTheArchive::test_without_the_write_flag_the_SAME_RUN_is_still_REFUSED',
        '        if args.archive_write and (archive_text is not None or archive_missing):\n',
        '        if True and (archive_text is not None or archive_missing):\n',
    ),
    (
        'R15-write-flag-forgives-an-UNREADABLE-archive',
        'the flag made to forgive UNREADABLE as well as ABSENT, so this run would '
        'append past a file it could not read — losing whatever is in it and '
        'unable to preserve the header, which is half of why the note exists.',
        'TestTheWriteFlagRoutesAroundNothing::test_an_EXISTING_but_unreadable_archive_is_still_refused',
        '        if args.archive_write and (archive_text is not None or archive_missing):\n',
        '        if args.archive_write and (archive_text is not None or True):\n',
    ),
    (
        'R16-the-READ-BACK-check-deleted',
        'the read-back conservation check removed, so rule (r) no longer validates '
        'the bytes the writer actually put on disk — constraint (3) inverted into '
        '"the writer gets to skip the guard it feeds". 🔴 ITS KILLER IS THE '
        '`write_text` SABOTAGE AND NOT THE `archive_append` ONE, measured: '
        'breaking the writer is caught by the PROJECTION check one block earlier '
        'and never reaches this arm at all, so a row aimed at the obvious '
        'sabotage would have scored KILLED by a different guard and vouched for '
        'this one not at all.',
        'TestTheConservationCheckRunsAFTERTheWriteNotInsteadOfIt::test_a_lossy_WRITE_is_caught_by_the_READ_BACK_and_rolls_BOTH_back',
        '                plan, args.archive, readback, "", False\n',
        '                plan, args.archive, "", "", True\n',
    ),
    (
        'R17-the-READ-BACK-reads-the-PROJECTION-not-the-disk',
        'the read-back fed the projected text instead of the file, which is the '
        'SUBTLE version of R16: the check still runs, still refuses on a broken '
        'writer, and is blind to the one thing it exists for — the bytes on disk '
        'not being what the writer produced. A guard that reads its own input '
        'back from memory is the "verified in isolation" shape.',
        'TestTheConservationCheckRunsAFTERTheWriteNotInsteadOfIt::test_a_lossy_WRITE_is_caught_by_the_READ_BACK_and_rolls_BOTH_back',
        '            readback = archive_write.path.read_text(encoding="utf-8")\n',
        '            readback = appended.text\n',
    ),
    (
        'R18-the-PROJECTION-check-deleted',
        'the projection check fed an archive that conserves everything, so the '
        'PROPOSAL run can no longer refuse a broken writer and the two-run shape '
        'stops covering the writer at all. The mirror of R16 — one arm per input, '
        'and each has a killer the other cannot have.',
        'TestTheConservationCheckRunsAFTERTheWriteNotInsteadOfIt::test_a_WRITER_that_drops_a_line_is_caught_BEFORE_anything_is_written',
        '                plan, args.archive, appended.text, "", archive_is_the_doc\n',
        '                plan, args.archive, "", "", True\n',
    ),
    (
        'R19-the-archive-is-written-AFTER-the-leak-gate',
        'the archive write moved BELOW rule (o), so an evicted block is committed '
        'without ever being scanned — constraint (2). 🔴 MUTATED AS A DELETION OF '
        'THE WRITE AT ITS CURRENT POSITION rather than as a move, because a move '
        'is not expressible as a one-site substitution and a two-site mutant here '
        'would mutate the guard together with its enclosing condition.',
        'TestTheWriteFlagRoutesAroundNothing::test_the_leak_scanner_SEES_the_archive_block',
        '            archive_write.path.write_text(appended.text, encoding="utf-8")\n',
        '            pass  # the write moved below the leak gate\n',
    ),
    (
        'R20-the-commit-drops-the-archive-path',
        'the archive written but NOT committed — the exact half of the old hand '
        'workflow people forgot: the doc lands, the archive sits in one working '
        'tree, and the evicted content is gone for everyone else.',
        'TestTheWriteFlagCommitsExactlyTwoPaths::test_the_WRITE_flag_commits_the_doc_AND_the_archive',
        '            [archive_write.relpath] if archive_write is not None else []\n        ))\n        committed = True\n',
        '            [] if archive_write is not None else []\n        ))\n        committed = True\n',
    ),
    (
        'R21-the-rollback-forgets-the-archive',
        'the leak-refusal rollback handed only the doc, so a run printing NOTHING '
        'WRITTEN leaves behind an archive it created — and in the leak case, one '
        'holding the content the scanner just refused.',
        'TestTheWriteFlagRoutesAroundNothing::test_the_leak_scanner_SEES_the_archive_block',
        'staged, archive_write if archive_written else None)}",\n',
        'staged, None)}",\n',
    ),
    (
        'R22-idempotence-off-every-line-re-appended',
        'the "already held" set difference switched OFF, so every re-run appends '
        'the whole block again and an archive hand-authored earlier gains a '
        'duplicate of every line it already had.',
        'TestTheArchiveWriteIsIdempotent::test_an_archive_that_ALREADY_HOLDS_every_line_is_not_opened',
        '    fresh = [t for t in plan.removed if _norm_line(t.line) not in held]\n',
        '    fresh = [t for t in plan.removed]\n',
    ),
    (
        'R23-idempotence-normalisation-dropped',
        'the "already held" test made VERBATIM, so a re-indented copy in the '
        'archive stops counting as held and the line is appended a second time — '
        'while rule (r), which DOES normalise, was already satisfied by the '
        'first. Two spellings of one predicate, one function apart.',
        'TestTheArchiveWriteIsIdempotent::test_whitespace_is_collapsed_when_deciding_ALREADY_HELD',
        '    fresh = [t for t in plan.removed if _norm_line(t.line) not in held]\n',
        '    fresh = [t for t in plan.removed if t.line not in held]\n',
    ),
    (
        'R24-the-note-becomes-optional',
        'the required-note refusal switched off, so a run with no judgement writes '
        'a block carrying only the generated provenance line — the header that '
        'reads as a complete description of the content\'s status and so stops the '
        'next reader looking for the caveat.',
        'TestTheWriteFlagUsageContract::test_the_write_flag_without_a_NOTE_is_a_USAGE_refusal',
        '    if args.archive_write and not (args.archive_note or "").strip():\n',
        '    if False and not (args.archive_note or "").strip():\n',
    ),
    (
        'R25-an-EMPTY-note-accepted',
        'the emptiness half of the note refusal dropped while presence is still '
        'required — the `--override-size-ratchet` shape: a flag that reads on the '
        'run as a recorded judgement while recording nothing.',
        'TestTheWriteFlagUsageContract::test_an_EMPTY_note_is_a_USAGE_refusal',
        '    if args.archive_write and not (args.archive_note or "").strip():\n',
        '    if args.archive_write and args.archive_note is None:\n',
    ),
    (
        'R26-the-repo-containment-refusal-off',
        'the INSIDE--repo refusal switched off, so an archive written outside the '
        'tree is neither scanned by rule (o) nor carryable by a path-limited '
        'commit — and the run still reports `status=written`.',
        'TestTheWriteFlagUsageContract::test_an_archive_OUTSIDE_the_repo_is_a_USAGE_refusal',
        '    if args.archive_write and not _within(repo, Path(args.archive)):\n',
        '    if False and not _within(repo, Path(args.archive)):\n',
    ),
    (
        'R27-containment-by-SPELLING-not-identity',
        'the containment check made a prefix test on the spelling instead of an '
        'identity test on the resolved path — walkable by a symlink inside the '
        'tree pointing out of it, the same spelled-guard shape R8 covers for the '
        'self-archive walk.',
        'TestTheWriteFlagUsageContract::test_the_containment_check_is_on_IDENTITY_not_on_a_spelling',
        '    return path.resolve().is_relative_to(repo.resolve())\n',
        '    return str(path).startswith(str(repo))\n',
    ),
    (
        'R28-the-write-disclosure-claims-the-file-was-only-READ',
        'the disclosure\'s read-only branch widened to cover the write path, so a '
        'run that CREATED and COMMITTED the archive tells the operator the file is '
        '"READ, never written" and that "committing it is yours". 🔴 A COMMENT IS A '
        'CLAIM AND SO IS stdout: this is the one line an operator reads to decide '
        'whether there is still hand work to do.',
        'TestTheWriteFlagCreatesTheArchive::test_the_disclosure_says_the_archive_was_WRITTEN_not_merely_read',
        '    if archive_path is not None and appended is None:\n',
        '    if archive_path is not None:\n',
    ),
    (
        'R29-the-refusal-claims-nothing-was-written',
        'the read-back refusal\'s own disclosure flipped, so a run that appended to '
        'the archive and rolled the append back prints "this tool does NOT write '
        '--archive\'s file". The operator believes a file holding the only copy of '
        'evicted text is untouched, on the one path where it was not.',
        'TestTheConservationCheckRunsAFTERTheWriteNotInsteadOfIt::test_a_lossy_WRITE_is_caught_by_the_READ_BACK_and_rolls_BOTH_back',
        '                        regression, plan, relpath, archive_was_written=True\n',
        '                        regression, plan, relpath, archive_was_written=False\n',
    ),
    (
        'R30-the-note-is-reflowed-rather-than-written-verbatim',
        'the caller\'s sentence truncated on the way into the block. It is the one '
        'part of the archive no machine can regenerate, so a writer that reflowed '
        'or clipped it destroys the only thing the flag exists to carry — and a '
        'keyword assertion would not notice.',
        'TestTheWriteFlagCreatesTheArchive::test_the_note_lands_VERBATIM_and_is_not_paraphrased',
        '    rows = [f"{ARCHIVE_BLOCK_PREFIX} `{relpath}` — {today}", "", note.strip(), ""]\n',
        '    rows = [f"{ARCHIVE_BLOCK_PREFIX} `{relpath}` — {today}", "", note.strip()[:20], ""]\n',
    ),
    (
        'R31-the-existing-header-is-CLOBBERED-rather-than-appended-to',
        'the append turned into a whole-file rewrite, so a hand-authored editorial '
        'header is destroyed by the run that adds a block under it — the half of '
        'the header decision that "preserve an existing header" is supposed to '
        'cover.',
        'TestAnExistingEditorialHeaderIsPreserved::test_the_existing_header_bytes_survive_the_append',
        '        f"{existing.rstrip(chr(10))}\\n\\n{block}",\n',
        '        block,\n',
    ),
    (
        'R32-marker-rename-must-red-the-skill-seam-for-the-writer-flags',
        'the WRITER flag renamed in the module only. The reference topic is the '
        'executor\'s single map from a flag to what it does, and `--archive-write` '
        'is the flag that makes this tool write a second file — a rename that does '
        'not reach the doc leaves the only documentation pointing at a flag that '
        'no longer exists.',
        'TestTheWriteFlagReachesTheSkill::test_the_reference_topic_names_both_writer_flags',
        'ARCHIVE_WRITE_FLAG = "--archive-write"\n',
        'ARCHIVE_WRITE_FLAG = "--archive-append"\n',
    ),
    (
        'R12-marker-rename-must-red-the-skill-seam',
        'a marker RENAME must red the doc seam, because the reference topic is '
        'the executor\'s only map from a printed marker to what to do about it. '
        '🔴 THIS ROW WAS FILED AS "the literal pin is the only thing that can '
        'see a rename" AND THE MEASUREMENT REFUTED IT: the DERIVED loop reds '
        '(the new value is not in the doc) and the literal pin stays GREEN. The '
        'only case the literal uniquely caught was a rename PROPAGATED to the '
        'doc — a coordinated edit, not a defect — so that test was deleted and '
        'this row keeps the seam honest on its own.',
        'TestRuleRReachesTheSkill::test_the_reference_topic_documents_the_markers_and_the_flag',
        'ARCHIVE_MARKER_NONE = "[no archive]"\n',
        'ARCHIVE_MARKER_NONE = "[gone]"\n',
    ),
]

_SUMMARY = re.compile(r"^(?:=+ )?(?:\x1b\[[0-9;]*m)?\d+ (?:passed|failed)", re.M)


def run_suite() -> tuple[str, int]:
    """The whole suite. Returns (stdout+stderr, returncode).

    🔴 THE CONTENT IS READ, NOT THE EXIT CODE — `claude/RULES.md`'s
    count-not-exit-code. The caller greps the per-test FAILED lines and refuses
    to score a run whose summary line is missing at all.
    """
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", SUITE, "-q", "-p", "no:randomly",
         "--no-header", "-rf"],
        cwd=ROOT, capture_output=True, text=True, env=env,
    )
    return proc.stdout + proc.stderr, proc.returncode


def failed_tests(out: str) -> set[str]:
    return set(re.findall(r"^FAILED (\S+)", out, re.M))


def main() -> int:
    # 🔴 LINE-BUFFERED, because a battery you cannot WATCH is one you cannot
    # tell from a hang. Redirect stdout to a file and Python block-buffers it,
    # so a half-hour sweep shows nothing at all until it exits — and the first
    # thing a reader does with silence is kill it.
    sys.stdout.reconfigure(line_buffering=True)
    originals = {SCRIPT: SCRIPT.read_text(encoding="utf-8")}
    try:
        print("baseline (no mutation) …")
        out, _rc = run_suite()
        if not _SUMMARY.search(out):
            print("ABORT: the baseline run printed no summary line. A battery "
                  "scored against a harness that did not run is a fabrication.")
            print(out[-3000:])
            return 2
        base_failures = failed_tests(out)
        if base_failures:
            print(f"ABORT: the baseline is RED ({len(base_failures)} failures). "
                  f"Every mutant would score KILLED off a pre-existing break.")
            for name in sorted(base_failures):
                print(f"  {name}")
            return 2
        print(f"  baseline GREEN — {out.strip().splitlines()[-1]}")

        killed, wrong, survived, not_applied = [], [], [], []
        for mid, _why, named, old, new in MUTANTS:
            path = SCRIPT
            src = originals[path]
            hits = src.count(old)
            if hits != 1:
                print(f"{mid}: NOT-APPLIED (pattern found {hits}x, need 1)")
                not_applied.append(mid)
                continue
            path.write_text(src.replace(old, new), encoding="utf-8")
            try:
                out, _rc = run_suite()
            finally:
                path.write_text(src, encoding="utf-8")
            if not _SUMMARY.search(out):
                print(f"{mid}: NOT-APPLIED (the run printed no summary line)")
                not_applied.append(mid)
                continue
            fails = failed_tests(out)
            hit = {f for f in fails if f.endswith(named)}
            if hit:
                print(f"{mid}: KILLED by {named} "
                      f"({len(fails)} test(s) red in total)")
                killed.append(mid)
            elif fails:
                print(f"{mid}: KILLED-WRONG-REASON — {named} stayed GREEN while "
                      f"{len(fails)} other test(s) went red:")
                for name in sorted(fails)[:5]:
                    print(f"      {name}")
                wrong.append(mid)
            else:
                print(f"{mid}: SURVIVED — the whole suite is green with the "
                      f"guard broken")
                survived.append(mid)

        print()
        print(f"RESULT: killed={len(killed)} killed-wrong-reason={len(wrong)} "
              f"survived={len(survived)} not-applied={len(not_applied)} "
              f"of {len(MUTANTS)}")
        if wrong:
            print(f"  wrong reason: {', '.join(wrong)}")
        if survived:
            print(f"  survived:     {', '.join(survived)}")
        if not_applied:
            print(f"  not applied:  {', '.join(not_applied)}")
        return 0 if not (wrong or survived or not_applied) else 1
    finally:
        for path, text in originals.items():
            path.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
