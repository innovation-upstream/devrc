#!/usr/bin/env python3
"""The overlap-bucketing contention figure is RETRACTED — it may be quoted, never asserted.

🔴 WHY THIS EXISTS. `CLAUDE.md` retracted "14.5 min alone -> 48.9 min at 6+
overlapping" and says in terms that **re-deriving it is the trap**: bucketing
runs by how many others overlapped them is length-biased (a long run overlaps
more runs BY CONSTRUCTION), and a null Monte Carlo with zero interaction
reproduces the shape, the 14.5-min baseline and ~2.06x of the 3.4x.

It was retracted in `CLAUDE.md` and in `claudedocs/handoff-gate-speed-and-ci-signal.md`
— and `scripts/scoped-tests.sh` went on stating it as the measured reason for its
own existence, in its `WHY THIS EXISTS` header. One tree, two files, opposite
claims, with the live one justifying a script. Nothing noticed, because a
retraction in prose cannot reach a comment in a shell script.

🔴 THE RULE THIS ENFORCES IS A RELATIONSHIP, NOT A WORD. A plain "the string
must not appear" guard is wrong in both directions: it would fail the three
legitimate sites that quote the figure IN ORDER to retract it, and it would pass
a file that re-derived the same claim in different digits. So: wherever the
figure appears, a retraction must appear near it.

An INVARIANT GUARD, not regression coverage — it pins a property no shipped bug
violated once `scoped-tests.sh` was fixed in the same commit. Its evidence is
the positive control below, which fails if the scanner is wired to nothing.
"""
import re
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

# The figure, in the spellings it has actually been written in. Digits, not
# prose: a reword is exactly what this must still catch.
FIGURE = re.compile(r"\b48\.9\b|\b14\.5\s*min\b")
RETRACTION = re.compile(r"RETRACTED|length-biased", re.I)

# How far from the figure a retraction may sit. The figure and its retraction
# belong in one paragraph; 20 lines is generous for a wrapped shell comment and
# still far too tight for an unrelated mention elsewhere in a long file.
NEAR_LINES = 20

# 🔴 THE COUNT LEDGER, AND WHY PROXIMITY ALONE WAS NOT ENOUGH.
#
# The first version of this guard asserted only "a retraction appears within
# NEAR_LINES of the figure". It was MUTATION-TESTED and SURVIVED the one mutant
# that matters: re-inserting the bare claim
#
#     "runs bucketed by overlap go 14.5 min (0 others) -> 48.9 min (6+)."
#
# into `scripts/scoped-tests.sh`'s header — which is EXACTLY where it was
# re-derived before, and exactly where it would be re-derived again — passed,
# because the retraction note now sits ten lines below it and satisfied the
# proximity check. The guard was excusing the one site it exists to watch.
#
# So the binding assertion is a COUNT, pinned two-way: each file may carry
# exactly the occurrences its retraction needs, and no more. A new assertion
# anywhere moves a number, whatever words surround it. Update these counts only
# when you have read the diff and the occurrence is part of a RETRACTION.
EXPECTED_OCCURRENCES = {
    # The canonical retraction. Quotes the figure once, in one prose line.
    # (Counts are LINES carrying the figure, not regex matches: CLAUDE.md's
    # retraction quotes both halves on one wrapped line.)
    "CLAUDE.md": 1,
    # The handoff that first retracted it.
    "claudedocs/handoff-gate-speed-and-ci-signal.md": 1,
    # The header that used to ASSERT it, now retracting it in place.
    "scripts/scoped-tests.sh": 1,
}


def _tracked_text_files():
    out = subprocess.run(
        ["git", "-C", str(REPO), "ls-files", "-z"],
        capture_output=True, text=True, check=True,
    ).stdout
    for rel in out.split("\0"):
        if not rel or rel.endswith((".png", ".jpg", ".gif", ".ico", ".pdf")):
            continue
        p = REPO / rel
        try:
            yield rel, p.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue


def _hits():
    """-> [(relpath, lineno, line, retracted_nearby)]"""
    found = []
    for rel, lines in _tracked_text_files():
        if rel == "scripts/tests/test_retracted_contention_figure.py":
            continue  # this file quotes the figure to define it
        for i, line in enumerate(lines):
            if not FIGURE.search(line):
                continue
            lo, hi = max(0, i - NEAR_LINES), min(len(lines), i + NEAR_LINES + 1)
            near = any(RETRACTION.search(l) for l in lines[lo:hi])
            found.append((rel, i + 1, line.strip(), near))
    return found


def test_the_scanner_can_see_the_figure_at_all():
    """🔴 POSITIVE CONTROL. A reassuring zero below is worthless without it.

    The figure IS in this tree — quoted by every site that retracts it — so a
    scanner that finds nothing is a scanner wired to nothing, not a clean tree.
    `claude/RULES.md`: report the pair, never the zero alone.
    """
    hits = _hits()
    assert len(hits) >= 2, (
        f"the scanner found {len(hits)} occurrence(s) of the retracted figure. "
        "At least the retracting sites should match, so this is a broken "
        "scanner rather than a clean tree — check FIGURE against how the "
        "figure is actually spelled now."
    )


def test_the_figure_appears_exactly_where_and_as_often_as_the_ledger_says():
    """🔴 THE BINDING ASSERTION — a COUNT, because proximity was walkable.

    Pinned two-way: a file carrying the figure with no ledger entry fails, and
    a ledger entry naming a file that no longer carries it fails. A
    re-assertion inside an already-retracting file moves that file's count,
    which is the mutant the proximity check slept through.
    """
    counts = {}
    for rel, _ln, _text, _near in _hits():
        counts[rel] = counts.get(rel, 0) + 1

    unledgered = {r: n for r, n in counts.items() if r not in EXPECTED_OCCURRENCES}
    assert not unledgered, (
        f"\n\nthe RETRACTED contention figure appears in file(s) with no "
        f"ledger entry: {unledgered}.\n"
        "  If this is a new RETRACTION, add it to EXPECTED_OCCURRENCES with "
        "its count. If it is a new ASSERTION, delete it — bucketing runs by "
        "overlap is length-biased and that dataset cannot size contention."
    )
    stale = {r: n for r, n in EXPECTED_OCCURRENCES.items() if r not in counts}
    assert not stale, (
        f"\n\nledger entries naming a file that no longer carries the figure: "
        f"{sorted(stale)}.\n  A ledger that names nothing reads as coverage "
        "that no longer runs — drop the entry."
    )
    moved = {
        r: (EXPECTED_OCCURRENCES[r], n)
        for r, n in counts.items()
        if r in EXPECTED_OCCURRENCES and n != EXPECTED_OCCURRENCES[r]
    }
    assert not moved, (
        "\n\nthe number of times the RETRACTED contention figure appears has "
        "changed (file: expected -> found):\n  "
        + "\n  ".join(f"{r}: {exp} -> {got}" for r, (exp, got) in moved.items())
        + "\n\n  A COUNT GOING UP IS THE REGRESSION THIS GUARD EXISTS FOR: the "
          "figure was re-asserted, and a retraction sitting nearby does NOT "
          "make it true. `scripts/scoped-tests.sh` once stated it as the "
          "measured reason for its own existence while CLAUDE.md retracted "
          "it.\n"
          "  Say 'dozens of concurrent full suites' with no magnitude. The "
          "20.1-min median and the 60.0s collection cost are NOT retracted "
          "and are what justify scoping.\n"
          "  A count going DOWN is fine if you deleted a retraction "
          "deliberately — update the ledger in the same commit."
    )


def test_the_retracted_figure_is_never_asserted_without_its_retraction():
    """Quote it to retract it; never state it as a measurement.

    ⚠ WEAKER THAN IT LOOKS, AND KEPT ONLY AS A SECOND ANGLE. Mutation showed
    this passes when the figure is re-asserted in a file whose retraction note
    is within NEAR_LINES — which is every file in the ledger. The count test
    above is what actually binds; this one catches the figure appearing in a
    file with no retraction anywhere near it.
    """
    bare = [(rel, ln, text) for rel, ln, text, near in _hits() if not near]
    assert not bare, (
        "\n\nthe RETRACTED overlap-bucketing contention figure is stated "
        f"without a retraction within {NEAR_LINES} lines:\n  "
        + "\n  ".join(f"{rel}:{ln}: {text[:100]}" for rel, ln, text in bare)
        + "\n\n  'runs bucketed by overlap go 14.5 min (0 others) -> 48.9 min "
          "(6+)' is RETRACTED. Bucketing by overlap is length-biased — a long "
          "run overlaps more runs BY CONSTRUCTION — and a null Monte Carlo "
          "with zero interaction reproduces the shape, the 14.5-min baseline "
          "and ~2.06x of the 3.4x. Contention is real; that dataset cannot "
          "size it.\n"
          "  `scripts/scoped-tests.sh` asserted it as the measured reason for "
          "its own existence while CLAUDE.md retracted it, in the same tree.\n"
          "  Say 'dozens of concurrent full suites' with no magnitude, or "
          "quote the figure WITH its retraction. The 20.1-min median and the "
          "60.0s collection cost are NOT retracted and are what justify "
          "scoping."
    )
