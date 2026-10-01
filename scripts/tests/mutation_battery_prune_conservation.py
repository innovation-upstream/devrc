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
  * `positive-control-marker-rename` IS A MUTANT THIS BATTERY MUST CATCH, and it
    is here as the battery's own control. Renaming `ARCHIVE_MARKER_NONE` moves
    the module AND every test that reads the constant, so only the LITERAL pin
    in `TestRuleRReachesTheSkill` can see it. If that row ever reports SURVIVED,
    the literal pin has been deleted and the marker ledger is self-referential —
    exactly the measured survival the rule (q) block's header records.
  * Sources are restored in a `finally`. Check `git status` anyway if it dies
    hard.

WHAT THE ROWS COVER, as a ledger rather than a count
----------------------------------------------------
    R1/R2   the ARMING predicate, both directions (never arm / always arm).
            R2 is what the negative control test exists for: a guard that
            refuses every prune passes every refusal test in the suite.
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
    R12     the positive control described above.
"""
from __future__ import annotations

import os
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
MOD = ROOT / "scripts/lib/handoff_doc.py"
SUITE = "scripts/tests/test_handoff_doc.py"

#: (id, file, old, new, the test whose OWN assertion must go red)
MUTANTS: list[tuple[str, pathlib.Path, str, str, str]] = [
    (
        "R1-never-arm", MOD,
        "        if not durable:\n",
        "        if True:\n",
        "TestADurablePruneWithNoArchiveIsRefused::test_it_is_refused_and_writes_NOTHING",
    ),
    (
        "R2-always-arm", MOD,
        "        if not durable:\n",
        "        if False:\n",
        "TestADurablePruneWithNoArchiveIsRefused::"
        "test_a_NON_durable_prune_with_no_archive_is_still_ACCEPTED",
    ),
    (
        "R3-shared-predicate-empty", MOD,
        "    return tuple(t for t in plan.removed if t.reason)\n",
        "    return tuple(t for t in plan.removed if False)\n",
        "TestADurablePruneWithNoArchiveIsRefused::test_it_is_refused_and_writes_NOTHING",
    ),
    (
        "R4-compare-only-the-durable-half", MOD,
        "        for target in plan.removed\n",
        "        for target in durable\n",
        "TestTheArchiveIsCheckedOverEVERYRemovedLine::"
        "test_a_NON_durable_removal_missing_from_the_archive_refuses",
    ),
    (
        "R5-archive-compared-verbatim", MOD,
        "    return {_norm_line(ln) for ln in archive_text.splitlines() if ln.strip()}\n",
        "    return {ln for ln in archive_text.splitlines() if ln.strip()}\n",
        "TestAnArchiveThatHoldsTheContentLetsThePruneLand::"
        "test_whitespace_is_collapsed_and_NOTHING_ELSE_IS",
    ),
    (
        "R6-containment-always-satisfied", MOD,
        "        if _norm_line(target.line) not in held\n",
        "        if False\n",
        "TestTheArchiveIsNeverWrittenAndCannotBeTheDoc::"
        "test_an_EMPTY_archive_is_not_a_bypass",
    ),
    (
        "R7-self-archive-guard-off", MOD,
        "    if archive_is_the_doc:\n",
        "    if False:\n",
        "TestTheArchiveIsNeverWrittenAndCannotBeTheDoc::"
        "test_pointing_the_archive_AT_THE_DOC_is_refused",
    ),
    (
        "R8-self-archive-by-NAME-not-identity", MOD,
        "            archive_is_the_doc = archive.resolve() == doc.resolve()\n",
        "            archive_is_the_doc = archive == doc\n",
        "TestTheArchiveIsNeverWrittenAndCannotBeTheDoc::"
        "test_the_self_archive_refusal_is_on_IDENTITY_not_on_a_spelling",
    ),
    (
        "R9-unreadable-archive-is-operational", MOD,
        "                archive_error = str(exc)\n",
        "                return EXIT_FAIL\n",
        "TestTheArchiveIsNeverWrittenAndCannotBeTheDoc::"
        "test_an_unreadable_archive_is_RULE_R_s_verdict_not_an_OPERATIONAL_one",
    ),
    (
        "R10-exit-code-collapses-onto-rule-q", MOD,
        "            return EXIT_PRUNE_UNCONSERVED\n",
        "            return EXIT_PRUNE_REFUSED\n",
        "TestADurablePruneWithNoArchiveIsRefused::test_it_is_refused_and_writes_NOTHING",
    ),
    (
        "R11-inert-archive-flag-accepted", MOD,
        "    if args.archive is not None and args.prune is None:\n",
        "    if False and args.prune is None:\n",
        "TestTheArchiveFlagUsageContract::"
        "test_an_archive_without_a_prune_is_a_USAGE_refusal",
    ),
    (
        "R12-positive-control-marker-rename", MOD,
        'ARCHIVE_MARKER_NONE = "[no archive]"\n',
        'ARCHIVE_MARKER_NONE = "[gone]"\n',
        "TestRuleRReachesTheSkill::"
        "test_the_literal_marker_spellings_are_in_the_reference_topic",
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
    originals = {MOD: MOD.read_text(encoding="utf-8")}
    try:
        print("baseline (no mutation) …", flush=True)
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
        for mid, path, old, new, named in MUTANTS:
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
