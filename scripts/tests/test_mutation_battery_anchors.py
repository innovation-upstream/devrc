"""Every mutation-battery anchor must occur EXACTLY ONCE in the script it targets.

🔴 WHY THIS IS A COLLECTED TEST AND THE BATTERIES ARE NOT. The batteries rewrite
`scripts/resume-state.sh` in place, so they are author instruments nobody can run
concurrently and the gate does not collect them (`scripts/run-tests.sh` takes
`test_*.py`). This module reads files and mutates nothing, so it runs on every
push — which is the whole point: the failure it catches is committed by someone
who is not running the battery.

THE FAILURE, MEASURED TWICE IN THIS REPO. A battery row is `(id, …, old, new)`
and `old` is matched VERBATIM against the script. Reformat the anchored line —
wrap it, re-indent it, insert a comment between `*)` and its command — and the
pattern matches 0x. The battery then prints `!! PATTERN OCCURS 0x — NOT APPLIED`
and counts the row as a survivor, so the row guarding that behaviour silently
stops testing anything, and only someone re-running the battery ever sees it.

  * `60c893b7` (this branch, retracted by `6f4d748b`) split the `*) if …; then`
    line of the `$mine` gate across two lines to insert a comment. X1 — the row
    guarding the exact hole the branch closes — went to 0x.
  * `#1115.1` fixed the identical class in `scripts/tests/mutants-handoff-cap.sh`.

The remedy shipped for the first was a DO-NOT-REFORMAT comment above the line.
`claude/RULES.md` prefers a deterministic/structural fix to prose, and prose
covers one anchor: this module covers all of them, and covers a row added
tomorrow without anyone remembering to.

⚠ SCOPE — the `.sh` batteries are NOT covered. `scripts/tests/mutants-*.sh`
carry their anchors as shell heredocs/`sed` expressions rather than a Python
table, so nothing here can read them; they keep their own in-script checks.
`git grep -l 'PATTERN OCCURS' scripts/tests/` enumerates both families.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent

# Every battery whose anchors this module pins. Kept in sync with the directory
# by `test_the_battery_ledger_names_every_python_battery` below, so a third
# battery is covered by adding it here and cannot be added by forgetting to.
BATTERIES = (
    "mutation_battery_resume_state.py",
    "mutation_battery_resume_state_skill.py",
)


def _load(filename: str):
    """Import a battery for its `MUTANTS` table and its `SCRIPT` path.

    Both batteries keep every side effect under `if __name__ == "__main__"`, so
    importing one runs no subprocess and rewrites nothing.
    """
    path = HERE / filename
    spec = importlib.util.spec_from_file_location(f"_anchors_{path.stem}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _anchors(mod):
    """`(mutant_id, pattern)` for every anchor in a battery's table.

    `old` is the 4th field in BOTH tables (the skill battery adds a 6th
    `expected` field after it, and nothing before it). It may be a TUPLE of
    patterns applied in order — the skill battery's multi-site mutants — and
    each element is separately required to occur exactly once, which is the
    battery's own rule.
    """
    for row in mod.MUTANTS:
        assert len(row) >= 5, f"unexpected row shape in {mod.__name__}: {row!r}"
        mid, old = row[0], row[3]
        for pat in (old if isinstance(old, tuple) else (old,)):
            yield mid, pat


def _offenders(text: str, mod) -> list[tuple[str, str, int]]:
    """Anchors that do NOT occur exactly once in `text`."""
    return [(mid, pat, text.count(pat))
            for mid, pat in _anchors(mod)
            if text.count(pat) != 1]


@pytest.mark.parametrize("battery", BATTERIES)
def test_every_mutation_anchor_occurs_exactly_once_in_its_target(battery):
    """🔴 THE GUARD. A 0x anchor is a row that tests nothing; a 2x anchor is a
    mutation applied at a site the row does not describe."""
    mod = _load(battery)
    text = mod.SCRIPT.read_text(encoding="utf-8")
    bad = _offenders(text, mod)
    assert not bad, (
        f"{battery}: {len(bad)} anchor(s) do not occur EXACTLY ONCE in "
        f"{mod.SCRIPT.relative_to(HERE.parents[1])}.\n"
        "An anchor at 0x makes its row report `!! PATTERN OCCURS 0x — NOT "
        "APPLIED` and score as a SURVIVOR without testing anything; an anchor "
        "at 2x mutates a second site the row's description does not name.\n"
        "FIX THE BATTERY ROW, not this test — re-anchor `old` on the line as it "
        "now reads, and re-run the battery to confirm the row still kills.\n"
        + "\n".join(f"  {mid}: occurs {n}x — {pat!r}" for mid, pat, n in bad)
    )


@pytest.mark.parametrize("battery", BATTERIES)
def test_the_anchor_check_can_go_RED_in_BOTH_directions(battery):
    """🔴 NEGATIVE CONTROL — a checker nobody has watched fail proves nothing.

    Both failure shapes are exercised against a synthetic copy of the real
    script, so this is hermetic (no git, no `.git` — the sandbox tier has
    neither) while still using the live anchors:

      0x  the `60c893b7` shape — the anchored text is reformatted away.
      2x  a second site grows the same text, so the row mutates two places.

    Membership, not equality: removing or duplicating one pattern can move the
    count of another anchor that shares text with it, and that is not what this
    control is asserting.
    """
    mod = _load(battery)
    text = mod.SCRIPT.read_text(encoding="utf-8")
    mid, pat = next(_anchors(mod))

    gone = _offenders(text.replace(pat, "", 1), mod)
    assert (mid, pat, 0) in gone, (
        f"deleting {mid}'s anchor did not report it at 0x — the checker cannot "
        f"see the failure it exists for. offenders={gone!r}"
    )

    twice = _offenders(text + pat, mod)
    assert (mid, pat, 2) in twice, (
        f"duplicating {mid}'s anchor did not report it at 2x. "
        f"offenders={twice!r}"
    )


def test_the_anchor_check_is_not_vacuous_because_the_tables_are_NON_EMPTY():
    """POSITIVE CONTROL. `not bad` is also true of a battery whose table this
    module failed to read — an import that yielded no rows, a field that moved.
    Make the number move before believing the zero."""
    for battery in BATTERIES:
        mod = _load(battery)
        pats = list(_anchors(mod))
        assert len(pats) >= 20, (
            f"{battery} yielded {len(pats)} anchors; the tables have dozens. "
            "The reader is broken, not the batteries."
        )
        assert mod.SCRIPT.is_file(), f"{battery}: SCRIPT does not exist"


def test_the_battery_ledger_names_every_python_battery():
    """TWO-WAY PIN. A battery added to the directory but not to `BATTERIES`
    would be uncovered while this module reads as covering them all."""
    on_disk = sorted(p.name for p in HERE.glob("mutation_battery_*.py"))
    assert on_disk == sorted(BATTERIES), (
        "the battery ledger disagrees with scripts/tests/.\n"
        f"  on disk: {on_disk}\n"
        f"  ledger:  {sorted(BATTERIES)}\n"
        "Add the new battery to BATTERIES (and check its table keeps `old` as "
        "the 4th field), or remove the stale name."
    )
