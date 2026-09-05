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
table, so nothing here can read them. Each does carry its own in-script
did-not-apply control — checked file by file rather than assumed, 2026-09-01 —
but they do not agree on how to SPELL it: most print `MUTATION DID NOT APPLY`,
`mutants-dead-guard-exclude.sh` prints `mutation did not apply uniquely`, and
`mutants-install-sh.sh` prints `🔴 NOT-APPLIED`.

🔴 SO ENUMERATE BY FILENAME, NOT BY MARKER — no single marker grep sees both
families, and none is authoritative even within the `.sh` one:

    ls scripts/tests/mutants-* scripts/tests/mutation_battery_*

That is the whole population: `mutants-*` (mostly `.sh`, plus the
function-style `mutants-audit-dispatch.py`) and `mutation_battery_*.py`. The
PYTHON half of it is pinned two-way by
`test_the_battery_ledger_names_every_python_instrument` below, so treat that
test — not this paragraph — as the ledger; the `.sh` half is described here and
enforced nowhere, so re-run the `ls`.

This paragraph used to hand over `git grep -l 'PATTERN OCCURS' scripts/tests/`
as the enumerator of "both families". It returns the two Python batteries and
this file and **no `.sh` at all** — `PATTERN OCCURS` is the Python family's
spelling alone — so a reader who ran it concluded there are no `.sh` batteries,
contradicting the sentence three lines above it. (audit of #1197, round 3, F3.)
"""

from __future__ import annotations

import importlib.util
import re
import shutil
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent

# Every battery whose anchors this module pins. Kept in sync with the directory
# by `test_the_battery_ledger_names_every_python_instrument` below, so a third
# battery is covered by adding it here and cannot be added by forgetting to.
BATTERIES = (
    "mutation_battery_resume_state.py",
    "mutation_battery_resume_state_skill.py",
    "mutation_battery_mentions.py",
)

# 🔴 PYTHON MUTATION INSTRUMENTS THIS MODULE CANNOT PIN, each with its reason.
# The ledger below globs BOTH naming conventions — `mutation_battery_*.py` is
# the MINORITY one (two files against ten `mutants-*`), so a future Python
# battery named `mutants-<x>.py` would have sat outside a "two-way pin" that
# read as directory-wide. It is a named exemption rather than a pattern, and
# `test_the_EXEMPTION_list_is_not_a_hiding_place` re-checks the reason.
# (audit of #1197, round 3, F5.)
NOT_TABLE_DRIVEN = {
    # Builds each mutant with a Python FUNCTION that rewrites the source; there
    # is no `MUTANTS` table of `(id, …, old, new)` rows to read anchors out of.
    # It does its own uniqueness checking in `_require_unique`/`_sub_unique`.
    "mutants-audit-dispatch.py",
}


def _load(filename: str | Path):
    """Import a battery for its `MUTANTS` table and its `SCRIPT` path.

    Both batteries keep every side effect under `if __name__ == "__main__"`, so
    importing one runs no subprocess and rewrites nothing. A bare name resolves
    against this directory; a Path is used as given, which is what lets the
    negative controls load a deliberately-broken COPY from a tmpdir instead of
    editing a real battery in the tree.
    """
    path = Path(filename) if Path(filename).is_absolute() else HERE / filename
    spec = importlib.util.spec_from_file_location(f"_anchors_{path.stem}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _anchors(mod):
    """`(mutant_id, pattern)` for every anchor in a battery's table.

    `old` is the 4th field in ALL tables (the skill battery adds a 6th
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


def _target_of(mod, mid) -> Path:
    """The file a row's anchor must occur exactly once in.

    🔴 A BATTERY MAY SPAN SEVERAL FILES. `mutation_battery_mentions.py` does,
    because the defects it exists for live at the SEAMS between the scanner, the
    tailer and the click handler rather than inside any one of them — a battery
    restricted to one `SCRIPT` structurally cannot express those. Such a battery
    declares `TARGETS = {mutant_id: Path}`; a single-file battery declares only
    `SCRIPT` and every row resolves to it, unchanged.
    """
    return getattr(mod, "TARGETS", {}).get(mid, mod.SCRIPT)


def _offenders(mod, override: dict | None = None) -> list[tuple[str, str, int]]:
    """Anchors that do NOT occur exactly once in their own target file.

    `override` maps a Path to text to use INSTEAD of reading that file — the
    negative control's mechanism, so it can exercise both failure shapes without
    writing to a battery's target in the tree.
    """
    override = override or {}
    cache: dict[Path, str] = {}
    bad = []
    for mid, pat in _anchors(mod):
        path = _target_of(mod, mid)
        if path in override:
            text = override[path]
        else:
            text = cache.setdefault(path, path.read_text(encoding="utf-8"))
        n = text.count(pat)
        if n != 1:
            bad.append((mid, pat, n))
    return bad


@pytest.mark.parametrize("battery", BATTERIES)
def test_every_mutation_anchor_occurs_exactly_once_in_its_target(battery):
    """🔴 THE GUARD. A 0x anchor is a row that tests nothing; a 2x anchor is a
    mutation applied at a site the row does not describe."""
    mod = _load(battery)
    bad = _offenders(mod)
    assert not bad, (
        f"{battery}: {len(bad)} anchor(s) do not occur EXACTLY ONCE in their "
        "target file.\n"
        "An anchor at 0x makes its row report `!! PATTERN OCCURS 0x — NOT "
        "APPLIED` and score as a SURVIVOR without testing anything; an anchor "
        "at 2x mutates a second site the row's description does not name.\n"
        "FIX THE BATTERY ROW, not this test — re-anchor `old` on the line as it "
        "now reads, and re-run the battery to confirm the row still kills.\n"
        + "\n".join(
            f"  {mid}: occurs {n}x in "
            f"{_target_of(mod, mid).relative_to(HERE.parents[1])} — {pat!r}"
            for mid, pat, n in bad)
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

    The copy is fed in through `override` rather than written to disk, and it is
    keyed on the FIRST ROW'S OWN target — which for a multi-file battery is not
    necessarily `SCRIPT`.
    """
    mod = _load(battery)
    mid, pat = next(_anchors(mod))
    target = _target_of(mod, mid)
    text = target.read_text(encoding="utf-8")

    gone = _offenders(mod, {target: text.replace(pat, "", 1)})
    assert (mid, pat, 0) in gone, (
        f"deleting {mid}'s anchor did not report it at 0x — the checker cannot "
        f"see the failure it exists for. offenders={gone!r}"
    )

    twice = _offenders(mod, {target: text + pat})
    assert (mid, pat, 2) in twice, (
        f"duplicating {mid}'s anchor did not report it at 2x. "
        f"offenders={twice!r}"
    )


@pytest.mark.parametrize("battery", BATTERIES)
def test_every_declared_TARGET_exists_and_is_named_by_a_row(battery):
    """🔴 A `TARGETS` MAP IS A LEDGER, AND AN UNPINNED ROW IS THE FAILURE IT
    EXISTS TO PREVENT. Two ways it rots, both silent:

      * a row with no entry falls back to `SCRIPT`, so its anchor is checked
        against the WRONG FILE — where it occurs 0x, which this module would
        then report as a battery bug rather than as the mapping bug it is;
      * an entry naming a mutant id no row carries is a stale pointer that
        reads as coverage.

    A battery with no `TARGETS` is single-file and passes trivially — asserted
    rather than skipped, so the two families run the same check.
    """
    mod = _load(battery)
    targets = getattr(mod, "TARGETS", {})
    ids = [row[0] for row in mod.MUTANTS]
    assert len(ids) == len(set(ids)), f"{battery}: duplicate mutant ids"
    if targets:
        missing = [i for i in ids if i not in targets]
        assert not missing, f"{battery}: rows with no TARGETS entry: {missing}"
        stale = [k for k in targets if k not in ids]
        assert not stale, f"{battery}: TARGETS names no such row: {stale}"
    for path in ({*targets.values()} or {mod.SCRIPT}):
        assert path.is_file(), f"{battery}: target does not exist: {path}"


def test_the_anchor_check_is_not_vacuous_because_the_tables_are_NON_EMPTY():
    """POSITIVE CONTROL. `not bad` is also true of a battery whose table this
    module failed to read — an import that yielded no rows, a field that moved.
    Make the number move before believing the zero."""
    for battery in BATTERIES:
        mod = _load(battery)
        pats = list(_anchors(mod))
        # 🔴 DERIVED FROM THE TABLE, NOT A LITERAL. This used to be `>= 20`,
        # which is a claim about how big the two original batteries happened to
        # be — a 16-row battery added later failed it while its reader worked
        # perfectly. Every row contributes at least one anchor and a multi-site
        # row contributes more, so `len(pats) >= len(MUTANTS)` is the real
        # invariant; the small absolute floor only rejects an import that
        # yielded a stub. What catches a reader pointed at the WRONG FIELD is
        # the exactly-once check above, where a `new` pattern occurs 0x.
        assert len(mod.MUTANTS) >= 5, (
            f"{battery} has {len(mod.MUTANTS)} rows; that is a stub, not a "
            "battery — or the import yielded the wrong object."
        )
        assert len(pats) >= len(mod.MUTANTS), (
            f"{battery} yielded {len(pats)} anchors for {len(mod.MUTANTS)} "
            "rows; the reader is broken, not the battery."
        )
        assert mod.SCRIPT.is_file(), f"{battery}: SCRIPT does not exist"


def _python_instruments() -> list[str]:
    """Every Python mutation instrument in `scripts/tests/`, under EITHER
    naming convention — `mutation_battery_*.py` and `mutants-*.py`."""
    return sorted({p.name
                   for pat in ("mutation_battery_*.py", "mutants-*.py")
                   for p in HERE.glob(pat)})


def test_the_battery_ledger_names_every_python_instrument():
    """TWO-WAY PIN. A battery added to the directory but named in neither
    `BATTERIES` nor `NOT_TABLE_DRIVEN` would be uncovered while this module
    reads as covering them all — and, before F5, a battery named `mutants-*.py`
    would have been uncovered *and* undetected, because the glob only saw the
    minority convention."""
    on_disk = _python_instruments()
    accounted = {*BATTERIES, *NOT_TABLE_DRIVEN}
    unaccounted = [n for n in on_disk if n not in accounted]
    assert not unaccounted, (
        "python mutation instrument(s) in scripts/tests/ that this module "
        f"neither pins nor exempts: {unaccounted}\n"
        "Add it to BATTERIES (and check its table keeps `old` as the 4th "
        "field), or to NOT_TABLE_DRIVEN with the reason it has no readable "
        "`MUTANTS` table."
    )
    stale = sorted(n for n in accounted if n not in on_disk)
    assert not stale, (
        f"the ledger names file(s) that are not on disk: {stale}\n"
        f"  on disk: {on_disk}"
    )


def test_the_EXEMPTION_list_is_not_a_hiding_place():
    """🔴 An exemption written when a file had no `MUTANTS` table must not
    survive the file GROWING one — that is how a battery ends up unpinned
    behind a reason that stopped being true. Re-checked from the source."""
    for name in sorted(NOT_TABLE_DRIVEN):
        src = (HERE / name).read_text(encoding="utf-8")
        assert not re.search(r"(?m)^MUTANTS\b", src), (
            f"{name} is exempted from the anchor check as having no `MUTANTS` "
            "table, and it now has one. Move it from NOT_TABLE_DRIVEN to "
            "BATTERIES."
        )


# --------------------------------------------------------------------------- #
# 🔴 A MULTI-SITE ROW MUST PAIR EVERY `old` WITH A `new` (audit of #1197, F4)
# --------------------------------------------------------------------------- #
def _pair_offenders(rows) -> list[str]:
    """Rows whose `old`/`new` cannot be zipped into complete pairs.

    Both batteries build their sites as `list(zip(old, new))` and then derive
    the occurrence check FROM those pairs. `zip` truncates to the shorter
    operand, so `old=(A, B), new=(N,)` drops site B out of the count *and* out
    of the application, and the half-mutant is scored under the row's name —
    verbatim the failure the comment above each `zip` says it prevents. A `new`
    handed over as a 2-character STRING is worse: it zips character-wise and
    applies two single-character substitutions the row does not describe.
    """
    bad = []
    for row in rows:
        old, new = row[3], row[4]
        if isinstance(old, tuple) and not isinstance(new, tuple):
            bad.append(f"{row[0]}: `old` is a {len(old)}-tuple but `new` is a "
                       f"{type(new).__name__} — zip() would pair CHARACTERS")
        elif not isinstance(old, tuple) and isinstance(new, tuple):
            bad.append(f"{row[0]}: `new` is a tuple but `old` is not — "
                       "str.replace() would be handed a tuple")
        elif isinstance(old, tuple) and len(old) != len(new):
            bad.append(f"{row[0]}: {len(old)} old pattern(s) against "
                       f"{len(new)} new — zip() would silently drop "
                       f"{abs(len(old) - len(new))} site(s)")
    return bad


@pytest.mark.parametrize("battery", BATTERIES)
def test_a_MULTI_SITE_row_pairs_every_old_with_a_new(battery):
    """🔴 THE GUARD. Latent, not live: no row is malformed today, which is
    exactly when the pin is cheap to add and impossible to argue with."""
    bad = _pair_offenders(_load(battery).MUTANTS)
    assert not bad, (
        f"{battery}: {len(bad)} row(s) cannot be zipped into complete pairs. "
        "A truncated row applies FEWER edits than it names, passes the "
        "occurrence check on the sites that survived the zip, and is scored "
        "under the row's id.\n" + "\n".join(f"  {b}" for b in bad)
    )


@pytest.mark.parametrize("battery", BATTERIES)
def test_the_PAIR_check_goes_RED_on_a_real_battery_COPY(battery, tmp_path):
    """🔴 NEGATIVE CONTROL, end to end, on a COPY — never on a battery in the
    tree, which the collected suite and any concurrent author both read.

    A stub object would only prove `_pair_offenders` can count. This copies the
    real file out, truncates its FIRST multi-site row's `new` tuple by textual
    surgery, imports the copy through the same `_load`, and requires the guard
    to name that row. If no row in the battery is multi-site there is nothing
    to truncate and the control skips itself LOUDLY rather than passing.
    """
    mod = _load(battery)
    multi = [r[0] for r in mod.MUTANTS if isinstance(r[3], tuple)]
    if not multi:
        pytest.skip(f"{battery} has no multi-site row to truncate")

    copy = tmp_path / battery
    shutil.copy(HERE / battery, copy)
    src = copy.read_text(encoding="utf-8")
    # Append a row that is malformed in the way F4 describes, built from the
    # first real multi-site row so the shape is the battery's own.
    row = next(r for r in mod.MUTANTS if isinstance(r[3], tuple))
    injected = (row[0] + "-TRUNCATED",) + tuple(row[1:3]) + (row[3], row[4][:-1]) \
        + tuple(row[5:])
    src += f"\nMUTANTS.append({injected!r})\n"
    copy.write_text(src, encoding="utf-8")

    bad = _pair_offenders(_load(copy).MUTANTS)
    assert any(b.startswith(row[0] + "-TRUNCATED:") for b in bad), (
        "the pair check did not report the truncated row — it cannot see the "
        f"failure it exists for. offenders={bad!r}"
    )
