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

# 🔴 EVIDENCE MUST BE AN IDENTIFIER, NOT A WORD.
#
# The first version of this regex also accepted the bare literal `CONFIRMED` and a
# bare `×\d+` count. Both are vocabulary, not evidence: an audit demonstrated that
# replacing ALL 81 evidence cells with the single word `CONFIRMED` still produced
# `problems: 0 / RESULT: PASS`, so the instrument could not distinguish this
# document from one carrying no evidence at all. That is the exact failure this
# script exists to prevent, committed inside the script itself.
#
# So the alternation now requires something a reader can LOOK UP:
#   * a UUID-shaped session id                (8-4-4-4-12)
#   * an `agent-<hex>` subagent session id    (>= 12 hex, so `agent-abc` fails)
#   * a git sha of >= 7 hex in backticks
#   * a PR/issue reference `#<digits>`
# A count alone no longer satisfies it, and neither does any English word.
EVIDENCE_RX = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
    r"|agent-[0-9a-f]{12,}"
    r"|`[0-9a-f]{7,}`"
    r"|#\d{2,}"
)
# A data row in one of the inventory tables: starts with | then an id like A1/B5/E13/F2/G1/D10.
ROW_RX = re.compile(r"^\|\s*([ABCDEFG]\d{1,2})\s*\|")

# The document's own declaration of how many rows it should contain.
ROWS_MARKER_HINT = "<!-- inventory-rows: N -->"
ROWS_MARKER_RX = re.compile(r"<!--\s*inventory-rows:\s*(\d+)\s*-->")

# 🔴 And its declaration of the verdict tally.
#
# This exists because the hand-written tally was WRONG IN BOTH ROUNDS -- 51/30 in
# round 1, 47/32 in round 2 -- while the row LISTS printed beside it were correct
# each time. A total kept next to what it counts is the classic drifting claim, so
# it is checked rather than proofread. Absent marker => cannot vouch => exit 2.
TALLY_MARKER_HINT = "<!-- inventory-tally: KEEP=n TIER=n DROP=n -->"
TALLY_MARKER_RX = re.compile(
    r"<!--\s*inventory-tally:\s*KEEP=(\d+)\s+TIER=(\d+)\s+DROP=(\d+)\s*-->")


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
            elif NO_EVIDENCE not in ev and not EVIDENCE_RX.search(ev):
                problems.append(
                    f"{rid} (line {lineno}): evidence names no session id, sha or PR number, "
                    f"and does not say {NO_EVIDENCE!r}"
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
    # 🔴 SCOPING CONTROL -- and the FIRST version of it was VACUOUS.
    #
    # It used the fixture "## 10. x\n| A10 | … |", which contains no `## 3. ` line,
    # so `in_scope` was False from the first line REGARDLESS of whether SECTION_END
    # existed. An audit demonstrated the miss mechanically: deleting the
    # `elif SECTION_END…: in_scope = False` branch -- the exact half this control
    # claims to cover -- left the self-test fully green, still printing "ok".
    #
    # The fixture must therefore OPEN the scope, put a good row inside it, and only
    # THEN cross into section 10, so that the SECTION_END branch is the only thing
    # that can keep the rule row out. Mutation-checked: with SECTION_END deleted,
    # scoped now sees 2 rows and 1 problem, and this control goes red.
    fixture = (
        "## 3. Pytest targets\n"
        "| A1 | x | 1 | 2 | `bfb2ae02-9576-4cb4-bdc3-5bc01b9504bc` ×3 | **KEEP** | reason |\n"
        "## 10. Proposed selection rules\n"
        "| A10 | validation | scripts/validation/** |\n"
    )
    p_scoped, n_scoped = check_rows(fixture, scoped=True)
    p_unscoped, n_unscoped = check_rows(fixture, scoped=False)
    if n_scoped != 1 or p_scoped:
        print(f"SELF-TEST FAIL (scoping): expected the 1 in-scope row to pass, "
              f"got rows={n_scoped} problems={p_scoped}")
        ok = False
    elif n_unscoped != 2 or not p_unscoped:
        print(f"SELF-TEST FAIL (scoping): unscoped run must SEE both rows and reject "
              f"the section-10 one, got rows={n_unscoped} problems={p_unscoped}; "
              f"the scoping control proves nothing")
        ok = False
    else:
        print("  ok  scoping keeps the in-scope row and excludes the section-10 rule table")
    # Evidence-strictness control: the word CONFIRMED, or a bare count, must NOT
    # satisfy the evidence cell. An audit replaced all 81 cells with `CONFIRMED`
    # and the checker still reported PASS.
    for label, cell in (("bare CONFIRMED", "**CONFIRMED CATCH**"),
                        ("bare count", "×12"),
                        ("short agent id", "agent-abc")):
        row = f"| A9 | x | 1 | 2 | {cell} | **KEEP** | reason |"
        p, n = check_rows(row, scoped=False)
        if n != 1 or not p:
            print(f"SELF-TEST FAIL (evidence strictness): {label!r} was accepted")
            ok = False
        else:
            print(f"  ok  rejects evidence that is only {label}")
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

    # 🔴 A ZERO-ROW GUARD IS NARROWER THAN THE SENTENCE THAT DESCRIBES IT.
    # The docstring promises a vacuous green cannot certify an empty table -- but
    # `checked == 0` only catches the EMPTY case. A document truncated to a single
    # row passed with `rows checked: 1 / RESULT: PASS`. So the document must state
    # its own row count in a machine-readable marker, and this asserts against it.
    # No marker => cannot vouch => exit 2, never a pass.
    m = ROWS_MARKER_RX.search(text)
    if not m:
        print(f"ERROR: {argv[1]} carries no `{ROWS_MARKER_HINT}` marker, so the row count "
              f"cannot be cross-checked. Refusing to vouch for a table whose expected "
              f"size is unstated.")
        return 2
    expected = int(m.group(1))
    if checked != expected:
        print(f"ERROR: the document declares {expected} rows and {checked} were matched. "
              f"Either the table was truncated/extended or the marker is stale — both are "
              f"defects, and neither is a pass.")
        return 2
    if checked == 0:
        print(f"ERROR: matched 0 inventory rows in {argv[1]} — refusing to report a pass "
              f"for a run that checked nothing.")
        return 2

    # Ids must be unique: a duplicated row id means one row is silently shadowing
    # another in every count derived from this table.
    ids = ROW_RX.findall("\n".join(
        l for l in text.splitlines() if ROW_RX.match(l)))
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    if dupes:
        problems.append(f"duplicate row id(s): {', '.join(dupes)}")

    # Verdict tally, cross-checked against the document's own declaration.
    tm = TALLY_MARKER_RX.search(text)
    if not tm:
        print(f"ERROR: {argv[1]} carries no `{TALLY_MARKER_HINT}` marker, so its verdict "
              f"tally cannot be cross-checked. Refusing to vouch.")
        return 2
    counted = {"KEEP": 0, "TIER": 0, "DROP": 0}
    in_scope = False
    for line in text.splitlines():
        if SECTION_START.match(line):
            in_scope = True
        elif SECTION_END.match(line):
            in_scope = False
        if not in_scope or not ROW_RX.match(line):
            continue
        for cell in split_cells(line):
            mv = VERDICT_RX.search(cell)
            if mv:
                counted[mv.group(1)] += 1
                break
    declared = {"KEEP": int(tm.group(1)), "TIER": int(tm.group(2)), "DROP": int(tm.group(3))}
    if counted != declared:
        problems.append(
            f"verdict tally disagrees with the document's own marker: "
            f"counted {counted}, declared {declared}")
    if sum(declared.values()) != expected:
        problems.append(
            f"declared tally sums to {sum(declared.values())} but the row marker "
            f"declares {expected} rows")

    for p in problems:
        print(f"BAD  {p}")
    print(f"\nrows checked: {checked} (declared: {expected})   problems: {len(problems)}")
    print("RESULT: PASS" if not problems else "RESULT: FAIL")
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
