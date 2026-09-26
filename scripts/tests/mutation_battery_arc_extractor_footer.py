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

import os
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
SRC = ROOT / "find-session.py"
TESTS = HERE / "test_find_session_arc.py"
SCRIPT = SRC
SELECT = "ArcNamesTheExtractor"

#: `(id, kind, description, old, new, expected_test)` — the house shape
#: `test_mutation_battery_anchors.py` reads, with `old` as the 4th field.
MUTANTS = [
    # 🔴 POSITIVE CONTROL, first so a wired-to-nothing batch is obvious at once.
    ("C0", "control",
     "the JSON drops `next_command` entirely — the POSITIVE CONTROL; if this "
     "does not go red the battery is wired to nothing. It SURVIVED on the first "
     "run and the test, not the code, was what was wrong.",
     '            "next_command": extractor_next_command(report),\n', "",
     "test_the_JSON_carries_the_command_and_NULL_when_there_is_none"),

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

    ("F7", "reorder",
     "the footer moves ABOVE the coverage line, so an agent that reads the "
     "command and stops has skipped every gap qualifying the chain",
     "    out.append(handoff_arc.coverage_line(report))",
     ("    _h = extractor_next_command(report)\n"
      "    if _h:\n        out.extend([\"\", _h])\n"
      "    out.append(handoff_arc.coverage_line(report))"),
     "test_the_command_sits_BELOW_the_coverage_line"),
]


def _run(node: str) -> tuple[int, str]:
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    p = subprocess.run(
        [sys.executable, "-m", "pytest", str(TESTS), "-q",
         "-k", f"{SELECT} and {node}", "--no-header", "-p", "no:cacheprovider"],
        capture_output=True, text=True, env=env, cwd=str(ROOT))
    return p.returncode, p.stdout + p.stderr


def main() -> int:
    original = SRC.read_text(encoding="utf-8")

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
            n = original.count(old)
            if n != 1:
                print(f"  {mid}: FATAL — anchor occurs {n}x, so the mutation is "
                      "stale or ambiguous and measures nothing.")
                results.append((mid, "ANCHOR-NOT-UNIQUE", node))
                continue
            SRC.write_text(original.replace(old, new), encoding="utf-8")
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
        SRC.write_text(original, encoding="utf-8")

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
