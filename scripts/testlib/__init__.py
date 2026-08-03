"""Test helpers shared ACROSS suites.

Not a test directory — nothing here is collected by pytest (it is not in
`scripts/run-tests.sh`'s target list, and no module here is named `test_*`).
It exists so a rule that applies to more than one suite lives in ONE place;
`claude/RULES.md` → "One rule, one place".

Import it by putting `scripts/` on sys.path, e.g. from
`scripts/<area>/tests/test_x.py`:

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # scripts/
    from testlib import mockbin
"""
