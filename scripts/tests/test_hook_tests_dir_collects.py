"""`pytest scripts/claude-hooks/tests/` must run, or fail LOUDLY — never zero.

THE DEFECT (measured on origin/main, a29b97b):

    $ python3 -m pytest scripts/claude-hooks/tests/ -q
    INTERNALERROR> ... File ".../tests/test_bash_guard.py", line 436, in <module>
    INTERNALERROR>     sys.exit(1 if fail else 0)
    INTERNALERROR> SystemExit: 0

    no tests ran in 4.99s

That directory mixes ordinary pytest modules with hand-rolled SCRIPT suites
that do their work at import and end in `sys.exit()`. Collecting one imports
it, so it ran and exited during collection; SystemExit is a BaseException, so
it escaped as INTERNALERROR and the session reported **"no tests ran"** —
indistinguishable from a clean zero.

`scripts/run-tests.sh` never saw it: it names the collectable files
individually and runs the scripts itself. So the gate was green while the
obvious directory invocation was a crash wearing a zero's clothes.

WHAT EACH TEST HERE IS (labelled, because "it passes" is not a category):

  * test_the_hook_tests_directory_collects_without_an_internal_error
        REGRESSION. Red at a29b97b, green at HEAD.
  * test_a_module_that_exits_at_import_is_a_named_error_not_no_tests_ran
        REGRESSION for the CLASS, via a synthetic module planted in a copy of
        the directory. Red at a29b97b, green at HEAD.
  * test_the_classifier_matches_run_tests_shs_own_two_lists (+ its parser
    positive controls)
        SEAM / LEDGER guard, not regression coverage. conftest.py decides
        script-vs-module structurally; run-tests.sh decides it with two
        hand-maintained lists. Nothing made those agree. This fails when the
        sets GROW or SHRINK on either side.
"""
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
HOOK_TESTS_DIR = REPO_ROOT / "scripts" / "claude-hooks" / "tests"
RUN_TESTS = REPO_ROOT / "scripts" / "run-tests.sh"
CONFTEST = HOOK_TESTS_DIR / "conftest.py"


def _pytest(*args, cwd=None):
    # 🔴 `scripts/` ON PYTHONPATH, EXPLICITLY. Several tests below `shutil.copy`
    # CONFTEST into a tmp dir, and that conftest is a second entry point for
    # GUARD 9 — it imports `testlib.gitenv_plugin`. In the real tree it finds
    # `scripts/` by walking up from its own location; a COPY has nothing above
    # it, so the copying harness has to supply what the location supplied.
    # Without this the copy tests are GREEN under `run-tests.sh` (which exports
    # PYTHONPATH) and RED under a bare `pytest scripts/tests` — a suite green in
    # one tier and broken in the other, which claude/RULES.md rates as moving
    # the bug rather than removing it. Measured, both tiers, before fixing it.
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT / "scripts") + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", *args],
        cwd=str(cwd or REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=600,
        env=env,
    )


# ---------------------------------------------------------------------------
# REGRESSION
# ---------------------------------------------------------------------------

def test_the_hook_tests_directory_collects_without_an_internal_error():
    """RED at a29b97b, GREEN at HEAD — but only ONE of its three assertions is
    regression coverage, and the split is measured, not assumed.

    Asserted on --collect-only so this stays cheap: the bug was in COLLECTION,
    and running the ~1900 real tests here would just be the gate again. That
    choice has a consequence worth naming rather than glossing:

        MEASURED at a29b97b under --collect-only:
            "INTERNALERROR>"  105 occurrences
            "no tests ran"      0 occurrences

    So the INTERNALERROR assertion is what fails on pre-change code -- it is the
    REGRESSION. The "no tests ran" assertion is an INVARIANT GUARD riding along
    inside this test: the phrase belongs to a full run (which is how the defect
    was originally reported), and --collect-only never prints it, so that
    assertion has never been red and pins the reassuring-zero wording rather
    than proving anything about the bug. The collected-count check below is a
    positive control. Three assertions, three different jobs.
    """
    proc = _pytest(str(HOOK_TESTS_DIR), "-q", "--collect-only")
    out = proc.stdout + proc.stderr

    # The literal symptom, both halves. Matched on `INTERNALERROR>` WITH the
    # angle bracket -- that is pytest's own line prefix, and the bare word
    # appears in conftest.py's explanatory error text, so asserting on it made
    # this guard match its own fix.
    assert "INTERNALERROR>" not in out, (
        "collecting the hook tests directory raised INTERNALERROR again.\n"
        "Something in it calls sys.exit() at import and conftest.py did not "
        f"classify it as a script suite.\n\n{out[-4000:]}"
    )
    assert "no tests ran" not in out, (
        "collecting the hook tests directory reported 'no tests ran' — the "
        "reassuring zero this whole file exists to stop.\n\n" + out[-4000:]
    )
    assert proc.returncode == 0, f"exit={proc.returncode}\n\n{out[-4000:]}"

    # Positive control: prove collection actually SAW the suites, so a future
    # regression cannot satisfy the two assertions above by collecting nothing
    # in some quieter way.
    m = re.search(r"(\d+) tests collected", out)
    assert m, f"could not find a 'N tests collected' line:\n\n{out[-4000:]}"
    collected = int(m.group(1))
    assert collected > 1500, (
        f"only {collected} tests collected from {HOOK_TESTS_DIR.name}/; the "
        "directory holds ~1900. A collapse this large means most of it stopped "
        "being collected."
    )


def test_a_module_that_exits_at_import_is_a_named_error_not_no_tests_ran(tmp_path):
    """RED at a29b97b, GREEN at HEAD — the CLASS, not just today's file.

    A file with real top-level `def test_*` functions that ALSO calls sys.exit()
    at import is not classified as a script suite, so layer 1 does not catch it.
    Layer 2 must turn it into a named CollectError rather than an INTERNALERROR.
    """
    sandbox = tmp_path / "tests"
    sandbox.mkdir()
    shutil.copy(CONFTEST, sandbox / "conftest.py")
    (sandbox / "test_exits_at_import.py").write_text(
        "import sys\n"
        "def test_never_reached():\n"
        "    assert True\n"
        "sys.exit(0)\n"
    )

    proc = _pytest(str(sandbox), "-q", "--collect-only", cwd=tmp_path)
    out = proc.stdout + proc.stderr

    # `INTERNALERROR>` with the bracket -- see the note in the test above.
    assert "INTERNALERROR>" not in out, f"still an INTERNALERROR:\n\n{out[-4000:]}"
    assert proc.returncode != 0, (
        "a module that exits at import was reported as SUCCESS — that is the "
        f"reassuring zero.\n\n{out[-4000:]}"
    )
    # Named, and it says what to do. Pin the distinctive phrase, not a word any
    # other error could spell.
    assert "called sys.exit(0) while pytest was IMPORTING it" in out, (
        "the failure is not the named, actionable one conftest.py raises:\n\n"
        + out[-4000:]
    )


@pytest.mark.parametrize("exit_code, want_outcome", [(0, "passed"), (1, "failed")])
def test_a_script_suites_exit_code_decides_its_verdict(tmp_path, exit_code, want_outcome):
    """The verdict mapping, pinned in BOTH directions.

    Without this nothing ever RAN a script suite through conftest.py -- the
    directory test uses --collect-only, which builds the item but never executes
    it. A mutant that made `runtest` ignore the subprocess's return code (so
    every script suite passes, forever, including a genuinely broken one) would
    therefore have SURVIVED the whole battery. Measured, and it did: that is why
    this test exists.

    Parametrized rather than asserted one-way on purpose. A test that only pins
    "exit 1 fails" is satisfied by a runtest that fails EVERYTHING; a test that
    only pins "exit 0 passes" is satisfied by one that passes everything. The
    pair is what makes the mapping, not either half.

    Runs in a tmp sandbox, which also exercises `_find_repo_root`'s copy path:
    the positional `parents[2]` this replaced produced a garbage root here.
    """
    sandbox = tmp_path / "tests"
    sandbox.mkdir()
    shutil.copy(CONFTEST, sandbox / "conftest.py")
    # Script-shaped: no top-level `def test_*`, so the classifier must route it
    # to the subprocess path rather than importing it.
    (sandbox / "test_script_suite.py").write_text(
        "import sys\n"
        "print('the suite ran and is reporting its own verdict')\n"
        f"sys.exit({exit_code})\n"
    )

    proc = _pytest(str(sandbox), "-q", cwd=tmp_path)
    out = proc.stdout + proc.stderr

    assert f"1 {want_outcome}" in out, (
        f"a script suite exiting {exit_code} should be reported {want_outcome!r}, "
        f"and was not.\n\n{out[-4000:]}"
    )
    if want_outcome == "failed":
        # The script's own output must reach the report, or a failing suite is
        # undiagnosable from the gate log.
        assert "the suite ran and is reporting its own verdict" in out, (
            "the failing script suite's stdout was swallowed:\n\n" + out[-4000:]
        )


# ---------------------------------------------------------------------------
# SEAM / LEDGER — conftest.py's classifier vs run-tests.sh's two lists
# ---------------------------------------------------------------------------

def _bash_array(name: str) -> list[str]:
    """Parse a `NAME=( ... )` array out of run-tests.sh, dropping comments."""
    src = RUN_TESTS.read_text()
    m = re.search(rf"^{name}=\((.*?)^\)", src, re.S | re.M)
    assert m, (
        f"could not find a {name}=( ... ) block in run-tests.sh. If the array "
        "was renamed, update this parser -- do NOT delete the test, or the "
        "classifier goes back to being unguarded."
    )
    out = []
    for raw in m.group(1).splitlines():
        line = raw.strip().strip('"')
        if not line or line.startswith("#"):
            continue
        out.append(line)
    return out


def _classifier():
    """Import conftest.py's classifier by path, without importing the package."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("_hook_tests_conftest", CONFTEST)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_parsers_find_something_positive_control():
    """Both parsers must be shown capable of a NON-EMPTY answer.

    Without this, every set comparison below could compare empty to empty and
    pass while measuring nothing.
    """
    hook_tests = [t for t in _bash_array("HOOK_TESTS") if t.startswith("scripts/")]
    hermetic = [t for t in _bash_array("HERMETIC_TARGETS") if t.startswith("scripts/")]
    assert len(hook_tests) >= 5, hook_tests
    assert len(hermetic) >= 10, len(hermetic)

    mod = _classifier()
    scripts = [p for p in sorted(HOOK_TESTS_DIR.glob("test_*.py")) if mod.is_script_suite(p)]
    modules = [p for p in sorted(HOOK_TESTS_DIR.glob("test_*.py")) if not mod.is_script_suite(p)]
    assert scripts, "the classifier found NO script suites -- it is broken, not the directory"
    assert modules, "the classifier found NO pytest modules -- it is broken, not the directory"


def test_the_classifier_matches_run_tests_shs_own_two_lists():
    """INVARIANT / SEAM guard — NOT regression coverage.

    conftest.py classifies structurally; run-tests.sh carries two hand-written
    lists that mean the same thing. This asserts the ledger both ways, so the
    set cannot grow or shrink on one side only. It fails when a suite is added
    to the directory and to one list but not the other -- the state in which
    the directory invocation crashes again, or the gate silently stops running
    a suite.
    """
    mod = _classifier()
    on_disk = sorted(HOOK_TESTS_DIR.glob("test_*.py"))

    classified_scripts = {p.name for p in on_disk if mod.is_script_suite(p)}
    classified_modules = {p.name for p in on_disk if not mod.is_script_suite(p)}

    prefix = "scripts/claude-hooks/tests/"
    gate_scripts = {t[len(prefix):] for t in _bash_array("HOOK_TESTS") if t.startswith(prefix)}
    gate_modules = {t[len(prefix):] for t in _bash_array("HERMETIC_TARGETS") if t.startswith(prefix)}

    assert classified_scripts == gate_scripts, (
        "conftest.py's script-suite classification disagrees with run-tests.sh's "
        f"HOOK_TESTS.\n  conftest says: {sorted(classified_scripts)}\n"
        f"  HOOK_TESTS says: {sorted(gate_scripts)}\n"
        "Add the new suite to BOTH, or give it top-level `def test_` functions."
    )
    assert classified_modules == gate_modules, (
        "conftest.py's pytest-module classification disagrees with run-tests.sh's "
        f"HERMETIC_TARGETS.\n  conftest says: {sorted(classified_modules)}\n"
        f"  HERMETIC_TARGETS says: {sorted(gate_modules)}\n"
        "A collectable suite here that the gate does not name is a suite NOTHING runs."
    )


@pytest.mark.parametrize(
    "source, expected_script",
    [
        ("import sys\nprint('work')\nsys.exit(0)\n", True),
        ("def test_a():\n    assert True\n", False),
        ("class TestThing:\n    def test_a(self):\n        assert True\n", False),
        ("async def test_a():\n    assert True\n", False),
        ("def helper():\n    return 1\n", True),
    ],
)
def test_the_classifier_reads_structure_not_names(tmp_path, source, expected_script):
    """INVARIANT guard on `has_toplevel_test_functions` itself.

    The set comparison above would still pass if the classifier were replaced by
    a hard-coded filename list, which is the version that rots. This pins that
    the decision is made from the file's STRUCTURE.

    Exercised in tmp_path, deliberately: `is_script_suite` additionally requires
    the file to live in the hook tests directory, and planting probe files there
    would be visible to a concurrently-running gate.
    """
    mod = _classifier()
    probe = tmp_path / "test_probe.py"
    probe.write_text(source)
    # `is_script_suite` == "in that directory" AND "no top-level tests"; the
    # second half is the part that can be wrong, so it is the part pinned here.
    assert mod.has_toplevel_test_functions(probe) is (not expected_script)
    assert mod.is_script_suite(probe) is False, (
        "is_script_suite must stay scoped to the hook tests directory -- every "
        "other tests/ tree in this repo is pure pytest and must not be reclassified"
    )
