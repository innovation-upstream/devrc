"""Test helpers shared ACROSS suites.

Not a test directory — nothing here is collected by pytest (it is not in
`scripts/run-tests.sh`'s target list, and no module here is named `test_*`).
It exists so a rule that applies to more than one suite lives in ONE place;
`claude/RULES.md` → "One rule, one place".

Import it by putting `scripts/` on sys.path, e.g. from
`scripts/<area>/tests/test_x.py`:

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # scripts/
    from testlib import mockbin

🔴 ONE MODULE HERE IS NOT TEST-ONLY: `gitenv.py`. `scripts/analyze-service-index/
backup.py` — a systemd-timer program that encrypts and UPLOADS OFF-BOX, and
`restore-verify.py` through it — imports `REPO_POINTER_VARS` /
`strip_repo_pointers` at RUNTIME, and refuses to start if the import fails. So
this package is on the deployed path (the unit mounts the whole `scripts/` tree
read-only), and moving, renaming or making `gitenv.py` import anything heavier
than the stdlib is a PRODUCTION change, not an internal test refactor. See that
module's own header.
"""
