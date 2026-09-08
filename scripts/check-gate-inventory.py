#!/usr/bin/env python3
"""Assert the gate-inventory document satisfies criteria 3 and 4 of clawgate #525.

Criterion 3: every row carries EITHER named evidence of a real catch OR the
             explicit string `no evidence found`. A row may not be blank.
Criterion 4: every row carries a verdict of KEEP, DROP or TIER.

Deliberately NOT wired into `scripts/gate.sh`, `run-tests.sh` or the flake checks,
and deliberately NOT placed under `scripts/tests/` -- adding a file there would
change the collected count for the `scripts/tests` target and force a re-pin of
its TARGET_FLOORS entry, which is churn this document does not need. Run it by
hand:

    python3 scripts/check-gate-inventory.py claudedocs/gate-inventory-2026-09-07.md

🔴 It carries its own controls, for the reason the document it checks spends a
section on: a checker that cannot go red is not evidence. `--self-test` feeds it
one row of each malformed shape and asserts each is rejected, then feeds a
well-formed row and asserts it is accepted. A run that checks zero rows is an
error, not a pass -- a vacuous green here would certify an empty table.

Exit codes:  0 = all rows well-formed   1 = at least one row bad   2 = usage/no rows
"""
from __future__ import annotations

import re
import sys

VERDICT_RX = re.compile(r"\*\*(KEEP|DROP|TIER)\b")
NO_EVIDENCE = "no evidence found"
# A data row in one of the inventory tables: starts with | then an id like A1/B5/E13/F2/G1/D10.
ROW_RX = re.compile(r"^\|\s*([ABCDEFG]\d{1,2})\s*\|")


def split_cells(line: str) -> list[str]:
    """Markdown cells, minus the empty edges an outer-piped row produces."""
    parts = [c.strip() for c in line.strip().strip("|").split("|")]
    return parts


# 🔴 Only the INVENTORY tables (sections 3-9) are rows. Section 10's proposed
# selection rules reuse the same row ids in their first column ("| A10 | validation
# | scripts/validation/** |") and are not rows with evidence and verdict cells.
# Scanning the whole file reported 11 of those as malformed -- a false positive in
# the checker, not a defect in the document. Scope, do not loosen: widening the
# cell-count threshold instead would silently skip a genuinely truncated row.
SECTION_START = re.compile(r"^## 3\. ")
SECTION_END = re.compile(r"^## 10\. ")


def check_rows(text: str, scoped: bool = True) -> tuple[list[str], int]:
    """Return (problems, rows_checked)."""
    problems: list[str] = []
    checked = 0
    in_scope = not scoped
    for lineno, line in enumerate(text.splitlines(), 1):
        if scoped:
            if SECTION_START.match(line):
                in_scope = True
            elif SECTION_END.match(line):
                in_scope = False
        if not in_scope:
            continue
        m = ROW_RX.match(line)
        if not m:
            continue
        rid = m.group(1)
        checked += 1
        cells = split_cells(line)
        if len(cells) < 4:
            problems.append(f"{rid} (line {lineno}): only {len(cells)} cells; not a full row")
            continue
        # Verdict: any cell may carry it, but exactly one should.
        verdicts = [c for c in cells if VERDICT_RX.search(c)]
        if not verdicts:
            problems.append(f"{rid} (line {lineno}): no KEEP/DROP/TIER verdict")
        elif len(verdicts) > 1:
            problems.append(f"{rid} (line {lineno}): {len(verdicts)} verdict cells, expected 1")
        # Evidence: the cell before the verdict cell.
        try:
            vi = cells.index(verdicts[0]) if verdicts else -1
        except ValueError:                                  # pragma: no cover
            vi = -1
        if vi > 0:
            ev = cells[vi - 1]
            if not ev or ev in {"-", "--", "—"}:
                problems.append(f"{rid} (line {lineno}): evidence cell is blank")
            elif NO_EVIDENCE not in ev and not re.search(
                r"[0-9a-f]{8}-[0-9a-f]{4}|agent-[0-9a-f]{8}|`[0-9a-f]{8}`|#\d{2,}|CONFIRMED|×\d+",
                ev,
            ):
                problems.append(
                    f"{rid} (line {lineno}): evidence names no session id, sha, PR number "
                    f"or count, and does not say {NO_EVIDENCE!r}"
                )
    return problems, checked


SELF_TEST_BAD = [
    ("no verdict", "| A9 | x | 1 | 2 | `bfb2ae02-9576-4cb4-bdc3-5bc01b9504bc` ×3 | plain | reason |"),
    ("blank evidence", "| A9 | x | 1 | 2 |  | **KEEP** | reason |"),
    ("vague evidence", "| A9 | x | 1 | 2 | it seems fine | **KEEP** | reason |"),
    ("two verdicts", "| A9 | **TIER** | 1 | 2 | ×3 | **KEEP** | reason |"),
]
SELF_TEST_GOOD = "| A9 | x | 1 | 2 | `bfb2ae02-9576-4cb4-bdc3-5bc01b9504bc` ×3 | **KEEP** | reason |"


def self_test() -> int:
    ok = True
    for label, row in SELF_TEST_BAD:
        problems, checked = check_rows(row, scoped=False)
        if checked != 1:
            print(f"SELF-TEST BROKEN: {label!r} matched {checked} rows, expected 1")
            ok = False
        elif not problems:
            print(f"SELF-TEST FAIL (negative control): {label!r} was accepted; checker is inert")
            ok = False
        else:
            print(f"  ok  rejects {label}")
    problems, checked = check_rows(SELF_TEST_GOOD, scoped=False)
    if checked != 1 or problems:
        print(f"SELF-TEST FAIL (positive control): well-formed row rejected: {problems}")
        ok = False
    else:
        print("  ok  accepts a well-formed row")
    # Scoping control: a section-10-shaped row must be OUT of scope when scoped,
    # and IN scope (and rejected) when not -- otherwise "scoped" could be doing
    # nothing and the pass would be vacuous.
    s10 = "## 10. x\n| A10 | validation | scripts/validation/** |\n"
    _, n_scoped = check_rows(s10, scoped=True)
    p_unscoped, n_unscoped = check_rows(s10, scoped=False)
    if n_scoped != 0:
        print(f"SELF-TEST FAIL (scoping): section-10 row still checked ({n_scoped})")
        ok = False
    elif n_unscoped != 1 or not p_unscoped:
        print("SELF-TEST FAIL (scoping): unscoped run did not see/reject the row; "
              "the scoping control proves nothing")
        ok = False
    else:
        print("  ok  scoping excludes the section-10 rule table (and only it)")
    print("SELF-TEST: PASS" if ok else "SELF-TEST: FAIL")
    return 0 if ok else 1


def main(argv: list[str]) -> int:
    if "--self-test" in argv:
        return self_test()
    if len(argv) != 2:
        print(__doc__)
        return 2
    with open(argv[1], "r", encoding="utf-8") as fh:
        text = fh.read()
    problems, checked = check_rows(text)
    if checked == 0:
        print(f"ERROR: matched 0 inventory rows in {argv[1]} — refusing to report a pass "
              f"for a run that checked nothing.")
        return 2
    for p in problems:
        print(f"BAD  {p}")
    print(f"\nrows checked: {checked}   problems: {len(problems)}")
    print("RESULT: PASS" if not problems else "RESULT: FAIL")
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
