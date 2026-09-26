#!/usr/bin/env python3
"""Mutation battery for the arc→extractor footer (`find-session.extractor_next_command`).

🔴 WHY THIS EXISTS. `TestTheArcNamesTheExtractor` went green on the first run. A
green suite is a claim about the mutations someone IMAGINED, so each guard here is
broken on purpose and the battery asserts a test goes red WITH THAT GUARD'S OWN
name in the output — not merely that "something failed", which a different guard's
error satisfies while the guard under test is deleted.

🔴 IT ALREADY PAID FOR ITSELF. The first run scored C0 — the positive control,
dropping `"next_command"` from `run_arc`'s JSON dict — as **SURVIVED**. That was
not a harness fault: the test named
`test_the_JSON_carries_the_command_and_NULL_when_there_is_none` asserted against
`extractor_next_command(...)` directly and never touched the JSON path, so its
NAME claimed a relationship its BODY did not check. The test now goes through
`run_arc` and parses the output. This is the `guards-narrower` shape from
`claude/RULES.md`, caught by a control rather than by review.

🔴 RUN UNDER `PYTHONDONTWRITEBYTECODE=1`. CPython validates a cached module on
mtime-in-whole-SECONDS + size, so a same-length mutation landing in the same
second as the last import is invisible: the test imports the ORIGINAL bytecode and
the mutant is scored SURVIVED without ever executing. C0 is the other half of that
defence — a mutant known to be caught, in every batch.

Usage: PYTHONDONTWRITEBYTECODE=1 python3 mutation_battery_arc_extractor_footer.py
"""
from __future__ import annotations

import hashlib
import os
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
SRC = ROOT / "find-session.py"
EUM = ROOT / "session-analysis" / "extract_user_msgs.py"
TESTS = HERE / "test_find_session_arc.py"
SCRIPT = SRC
SELECT = "ArcNamesTheExtractor"

#: `(id, kind, description, old, new, expected_test)` — the house shape
#: `test_mutation_battery_anchors.py` reads, with `old` as the 4th field.
MUTANTS = [
    # 🔴 POSITIVE CONTROL, first so a wired-to-nothing batch is obvious at once.
    #
    # ⚠ C0 USED TO MUTATE `run_arc`'s `"next_command"` JSON FIELD, and that field
    # was DELETED (measured reach 0 of 6, no caller). Its anchor would now occur 0x,
    # which this battery scores ANCHOR-NOT-UNIQUE and the anchor ledger fails on —
    # so the control was re-pointed rather than dropped. Its first incarnation
    # SURVIVED, which is how the guards-narrower defect in the JSON test was found;
    # that history is worth keeping even though both the field and the test are gone.
    ("C0", "control",
     "`extractor_next_command` returns None unconditionally — the POSITIVE "
     "CONTROL. Every footer assertion must die; if this does not go red the "
     "battery is wired to nothing and every SURVIVED below is uninterpretable.",
     # 🔴 THE ANCHOR SPANS TO `n = …` ON PURPOSE. A first draft replaced only the
     # opening line of the multi-line `return (…)`, which orphaned its continuation
     # lines into a SyntaxError — the module then failed to import and the batch
     # scored KILLED-WRONG-TEST, dying for a reason that says nothing about the
     # guard. Mutate to something that still PARSES, or the control is theatre.
     # (`claude/RULES.md`: "ISOLATE THE MUTATION … dies for the wrong reason".)
     "    if not report.members:\n        return None\n"
     "    n = len(report.members)\n",
     "    return None  # C0 — control\n    n = len(report.members)\n",
     "test_a_resolved_arc_NAMES_the_extractor_command"),

    ("F1", "replacement",
     "the footer is computed and never appended — the whole feature inert while "
     "every other arc assertion stays green",
     "    hint = extractor_next_command(report)\n    if hint:\n",
     "    hint = extractor_next_command(report)\n    if False and hint:\n",
     "test_a_resolved_arc_NAMES_the_extractor_command"),

    ("F2", "guard-removal",
     "the measured-empty suppression is removed, so a resolved-but-empty arc is "
     "handed a command that exits non-zero — the reassuring-command shape",
     "    if not report.members:\n        return None\n",
     "    if False:\n        return None\n",
     "test_a_MEASURED_EMPTY_arc_names_NO_command"),

    ("F3", "replacement",
     "the plural is hardcoded, so a one-session arc reads `1 sessions`",
     "f\"{n} session{'' if n == 1 else 's'} of this arc:\\n\"",
     'f"{n} sessions of this arc:\\n"',
     "test_the_member_COUNT_is_the_real_one_and_singular_reads_right"),

    ("F4", "replacement",
     "the count is a constant rather than `len(members)`. 🔴 The fixture bounds "
     "are 1 and 3, never 2 alone — a 2-member fixture cannot see this mutant, "
     "because the constant equals the fixture's own value and it SURVIVES green.",
     "    n = len(report.members)\n", "    n = 2\n",
     "test_the_member_COUNT_is_the_real_one_and_singular_reads_right"),

    ("F5", "replacement",
     "the seed becomes a literal, so every arc's footer names ONE doc — the "
     "command runs and answers about the wrong arc, which is worse than failing",
     "--arc {shlex.quote(report.doc)}", "--arc handoff-arc-fixture.md",
     "test_the_command_carries_the_RESOLVED_doc_not_the_users_seed"),

    ("F6", "replacement",
     "the extractor path is renamed, so the footer prints a confident path to a "
     "file that does not exist — the seam a rendering test cannot see",
     'EXTRACTOR_REL = "scripts/session-analysis/extract_user_msgs.py"',
     'EXTRACTOR_REL = "scripts/session-analysis/extract_user_messages.py"',
     "test_the_NAMED_PATH_EXISTS_in_this_repo"),

    # 🔴 F8 EXISTS BECAUSE ROUND 0 FOUND THE SEED GUARD WAS THE ONE GUARD WITH NO
    # MUTANT — so its ability to go red was never verified, while the PR called it
    # one of "two seam guards, the load-bearing ones". Breaking the resolver the
    # EXTRACTOR shares is the failure mode the old, extractor-blind version of that
    # guard could not see; this mutant is what proves the widened one can.
    ("F8", "replacement",
     "`arc_seed_to_doc` stops accepting a bare basename, so every footer names a "
     "seed the extractor's own resolver rejects — the seam breaking with both "
     "components individually fine",
     "    return handoff_arc.doc_basename(seed)",
     '    return "" if seed and not seed.startswith("/") else (\n'
     "        handoff_arc.doc_basename(seed))",
     "test_the_printed_SEED_is_one_the_EXTRACTOR_ACCEPTS"),

    # 🔴 X1 IS THE ONLY MUTANT THAT ISOLATES THE SEAM, AND IT LIVES IN THE OTHER
    # FILE. Round 1 measured that F8 kills the seam guard's OLD, extractor-blind
    # form and its widened form identically — so `9/9 KILLED` vouched for the
    # widening not at all, and the battery structurally could not, because it
    # mutated one file while every discriminating mutant lives in the extractor.
    # Hence `TARGETS` below. X1 keeps the resolver's NAME in a comment on purpose:
    # that is exactly what the v2 spelled guard `in body` check accepted.
    ("X1", "replacement",
     "the extractor stops routing its seed through `arc_seed_to_doc` while still "
     "NAMING it in a comment — the seam broken in the one shape a spelled guard "
     "cannot see. After it, `--arc <session-uuid>` resolves in find-session and "
     "exits 3 in the extractor, because the UUID branch lives only in the resolver.",
     "    basename = fs.arc_seed_to_doc(seed, root=root)",
     "    # X1: was fs.arc_seed_to_doc(seed, root=root)\n"
     "    basename = fs.handoff_arc.doc_basename(seed)",
     "test_the_printed_SEED_is_one_the_EXTRACTOR_ACCEPTS"),

    ("F7", "reorder",
     "the footer moves ABOVE the coverage line, so an agent that reads the "
     "command and stops has skipped every gap qualifying the chain",
     "    out.append(handoff_arc.coverage_line(report))",
     ("    _h = extractor_next_command(report)\n"
      "    if _h:\n        out.extend([\"\", _h])\n"
      "    out.append(handoff_arc.coverage_line(report))"),
     "test_the_command_sits_BELOW_the_coverage_line"),
]

#: 🔴 A MULTI-FILE BATTERY, and it has to be. Every mutant that isolates the SEAM
#: lives in the extractor, not in `find-session.py` — round 1 measured that a
#: single-file battery scored `9/9 KILLED` while vouching for the seam guard's
#: widening not at all. `test_mutation_battery_anchors.py` reads this map to check
#: each anchor against the file it actually belongs to; without it every row falls
#: back to `SCRIPT` and X1's anchor would be checked against the wrong file, where
#: it occurs 0x — reported as a battery bug rather than the mapping bug it is.
TARGETS = {
    **{mid: SRC for mid, *_ in MUTANTS},
    "X1": EUM,
}


def _run(node: str) -> tuple[int, str]:
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    p = subprocess.run(
        [sys.executable, "-m", "pytest", str(TESTS), "-q",
         "-k", f"{SELECT} and {node}", "--no-header", "-p", "no:cacheprovider"],
        capture_output=True, text=True, env=env, cwd=str(ROOT))
    return p.returncode, p.stdout + p.stderr


def main() -> int:
    # 🔴 EVERY TARGET'S PRISTINE TEXT, KEYED BY PATH. A single `original` would
    # restore the wrong file's contents onto a mutated sibling, and the next mutant
    # would then land on leftover damage — a test dying to that is recorded as
    # killing a mutant it never saw ("a sweep whose restore step can fail silently
    # scores borrowed kills").
    pristine = {p: p.read_text(encoding="utf-8") for p in set(TARGETS.values())}

    rc, out = _run(SELECT)
    if rc != 0:
        print("FATAL: baseline is RED — a sweep over a red suite measures "
              f"nothing.\n{out[-2000:]}")
        return 2
    # 🔴 POSITIVE CONTROL ON THE HARNESS ITSELF: a `-k` filter that selects zero
    # tests exits 0 in some pytest versions, which would read as a green baseline.
    if not re.search(r"(\d+) passed", out) or " 0 passed" in out:
        print(f"FATAL: the selector matched no tests — `-k {SELECT}` is wired to "
              f"nothing.\n{out[-800:]}")
        return 2
    print(f"baseline: GREEN ({out.strip().splitlines()[-1]})")

    results, control = [], None
    try:
        for mid, _kind, _desc, old, new, node in MUTANTS:
            # 🔴 RESTORE *EVERY* TARGET AT THE TOP OF EACH ITERATION, NOT JUST THE
            # ONE ABOUT TO BE MUTATED. Writing only `target` self-restores that file
            # and leaves a SIBLING carrying the previous mutant's damage, so the next
            # mutant runs against a tree with two mutations and its kill may belong
            # entirely to the earlier one — a BORROWED KILL.
            #
            # MEASURED, and this battery shipped with the bug: `F8` (find-session.py)
            # runs immediately before `X1` (extract_user_msgs.py) and both name the
            # SAME expected killer, so X1 was scored KILLED on a tree still carrying
            # F8. Control that proved it — neuter X1's replacement to a semantic
            # no-op, seam fully intact, and the run STILL printed
            # `X1: KILLED … 10/10 KILLED`. The digest check below could not see it:
            # it runs ONCE after the loop, so it is structurally blind to mid-loop
            # cross-file contamination. Both sibling multi-file batteries in this
            # directory already restore per iteration — copy them, not this file's
            # history.
            for path, text in pristine.items():
                path.write_text(text, encoding="utf-8")
            target = TARGETS[mid]
            base = pristine[target]
            n = base.count(old)
            if n != 1:
                print(f"  {mid}: FATAL — anchor occurs {n}x in {target.name}, so "
                      "the mutation is stale or ambiguous and measures nothing.")
                results.append((mid, "ANCHOR-NOT-UNIQUE", node))
                continue
            target.write_text(base.replace(old, new), encoding="utf-8")
            rc, out = _run(node)
            # 🔴 THE VERDICT IS NOT `rc != 0`. A different guard's failure would
            # satisfy that with the guard under test deleted, so the NAMED test
            # must be the one that appears as failing.
            named = bool(re.search(rf"{re.escape(node)}\b", out))
            verdict = ("KILLED" if rc != 0 and named else
                       "KILLED-WRONG-TEST" if rc != 0 else "SURVIVED")
            if mid == "C0":
                control = verdict
            results.append((mid, verdict, node))
            print(f"  {mid}: {verdict}  (expected killer: {node})")
    finally:
        for path, text in pristine.items():
            path.write_text(text, encoding="utf-8")

    # 🔴 ASSERT THE RESTORE BY DIGEST, not by a green re-run. A silently failed
    # restore leaves the next mutant landing on leftover damage, and the test that
    # dies to it is scored as killing a mutant it never saw — a BORROWED kill. The
    # green re-check below is the tripwire; this is the actual proof.
    dirty = [p.name for p, text in pristine.items()
             if hashlib.sha256(p.read_bytes()).hexdigest()
             != hashlib.sha256(text.encode()).hexdigest()]
    if dirty:
        print(f"🔴 RESTORE FAILED — not byte-identical: {dirty}. Every verdict "
              "above is suspect: later mutants may have landed on leftover damage.")
        return 2

    rc, _ = _run(SELECT)
    print(f"restored: {'GREEN' if rc == 0 else 'RED — RESTORE FAILED'}")

    bad = [r for r in results if r[1] != "KILLED"]
    print(f"\n{len(results) - len(bad)}/{len(results)} KILLED; "
          f"positive control C0 = {control}")
    if control != "KILLED":
        print("🔴 C0 DID NOT FIRE — every SURVIVED above is uninterpretable, "
              "because the sweep may have executed nothing.")
        return 2
    for mid, verdict, node in bad:
        print(f"  🔴 {mid}: {verdict} (wanted {node} to fail)")
    return 1 if bad or rc != 0 else 0


if __name__ == "__main__":
    raise SystemExit(main())
