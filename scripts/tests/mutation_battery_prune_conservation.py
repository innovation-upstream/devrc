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
        "16 collapsed onto rule (q)'s 15, so the two remedies become indistinguishable to a caller branching on the number",
        'TestADurablePruneWithNoArchiveIsRefused::test_it_is_refused_and_writes_NOTHING',
        '            return EXIT_PRUNE_UNCONSERVED\n',
        '            return EXIT_PRUNE_REFUSED\n',
    ),
    (
        'R11-inert-archive-flag-accepted',
        'the usage refusal for an `--archive` with no `--prune` switched off, so an inert flag reads on the run as a conservation claim',
        'TestTheArchiveFlagUsageContract::test_an_archive_without_a_prune_is_a_USAGE_refusal',
        '    if args.archive is not None and args.prune is None:\n',
        '    if False and args.prune is None:\n',
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
