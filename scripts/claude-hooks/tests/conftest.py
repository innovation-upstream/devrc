"""Make `pytest scripts/claude-hooks/tests/` work on the WHOLE directory.

THE DEFECT THIS FIXES (measured on origin/main, a29b97b):

    $ python3 -m pytest scripts/claude-hooks/tests/ -q
    INTERNALERROR> ... File ".../tests/test_bash_guard.py", line 436, in <module>
    INTERNALERROR>     sys.exit(1 if fail else 0)
    INTERNALERROR> SystemExit: 0

    no tests ran in 4.99s

Root cause: this directory mixes TWO kinds of file behind ONE `test_*.py`
name. Six are ordinary pytest modules. Five are hand-rolled SCRIPT suites --
they do all their work at import, print their own PASS/FAIL lines, and end in
`sys.exit()`. pytest collects a script suite by IMPORTING it, so the script
runs during collection and its `sys.exit()` raises SystemExit out of the
collector. SystemExit derives from BaseException, so pytest does not treat it
as a collection error; it escapes as INTERNALERROR and the session ends
"no tests ran".

Why that is worse than a crash: **"no tests ran" is indistinguishable from a
clean zero.** `scripts/run-tests.sh` is unaffected -- it names the collectable
files INDIVIDUALLY and runs the five scripts itself (see HERMETIC_TARGETS'
"A FILE, not a dir, and deliberately so" comment, and the HOOK_TESTS array) --
so the gate never saw this. Every human or agent who reached for the obvious
directory invocation did, and got a reassuring zero over a crash.

THE FIX, in two layers:

  1. CLASSIFY STRUCTURALLY, then never import a script suite. `pytest_collect_
     file` claims each script suite BEFORE pytest's python collector can import
     it, and runs it the way run-tests.sh does -- as a subprocess, one pytest
     item, exit 0 is pass. So the directory now runs ALL eleven suites instead
     of crashing on the first script.

     The classifier is STRUCTURAL, not a hard-coded list: a file is a script
     suite iff it has no top-level `def test_*` / `class Test*`, read with
     `ast.parse` (no import, so a script suite is never executed to find out
     what it is). Measured over this directory today, that splits it exactly:

         top-level test funcs == 0   ->  the 5 scripts in HOOK_TESTS
         top-level test funcs >  0   ->  the 6 files named in HERMETIC_TARGETS

     A hard-coded list would rot the first time a suite is added -- and the
     rotted state is precisely the crash above. `scripts/tests/
     test_hook_tests_dir_collects.py` pins the classifier against run-tests.sh's
     two lists as a ledger, so the sets cannot drift apart silently.

  2. TURN THE WHOLE CLASS LOUD. Layer 1 removes today's SystemExit. Layer 2
     makes any FUTURE one land as a named collection error instead of
     INTERNALERROR: `pytest_pycollect_makemodule` returns a Module subclass
     that catches SystemExit around collection and re-raises it as a
     `CollectError`. pytest reports that as a red, named error against the
     offending file -- never again as "no tests ran".

Both layers are inert for the gate: it targets the six collectable files by
path, and for those `pytest_collect_file` returns None (normal collection) and
the Module subclass behaves exactly like `pytest.Module`. Per-target collected
counts are unchanged, so no TARGET_FLOORS entry moves.
"""
import ast
import subprocess
import sys
from pathlib import Path

import pytest

_THIS_DIR = Path(__file__).resolve().parent
# scripts/claude-hooks/tests -> scripts/claude-hooks -> scripts -> <repo root>.
# The script suites are invoked with cwd=<repo root> because that is what
# run-tests.sh does (it cd's to the git toplevel first), and at least one of
# them resolves repo-relative paths.
_REPO_ROOT = _THIS_DIR.parents[2]

# A script suite that hangs must fail loudly rather than wedge the session.
# Generous: the slowest of these forks a dozen subprocesses of its own.
_SCRIPT_TIMEOUT_SECONDS = 900


def has_toplevel_test_functions(path: Path) -> bool:
    """True if `path` looks like an ordinary pytest module.

    Read with `ast.parse`, deliberately: deciding this by importing is the very
    thing that detonates a script suite. A syntax error returns True so pytest
    -- not this classifier -- reports it, with its own error message.
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (SyntaxError, UnicodeDecodeError, OSError):
        return True
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test"):
            return True
        if isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
            return True
    return False


def is_script_suite(path: Path) -> bool:
    """True for the hand-rolled `asserts + sys.exit` suites in THIS directory.

    Scoped to this directory on purpose. Every other tests/ tree in this repo is
    pure pytest, and a classifier that reached into them could silently demote a
    real module whose tests are all inside classes or generated at import.
    """
    path = Path(path)
    return (
        path.suffix == ".py"
        and path.name.startswith("test_")
        and path.resolve().parent == _THIS_DIR
        and not has_toplevel_test_functions(path)
    )


class ScriptSuiteFailure(Exception):
    """Carries a script suite's own stdout/stderr into pytest's failure report."""


class ScriptSuiteItem(pytest.Item):
    """One hand-rolled suite, run as a subprocess exactly as the gate runs it."""

    def runtest(self):
        proc = subprocess.run(
            [sys.executable, str(self.path)],
            cwd=str(_REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=_SCRIPT_TIMEOUT_SECONDS,
        )
        if proc.returncode != 0:
            raise ScriptSuiteFailure(
                f"{self.path.relative_to(_REPO_ROOT)} exited {proc.returncode} "
                f"(this is a hand-rolled script suite; it prints its own PASS/FAIL "
                f"lines).\n\n--- stdout ---\n{proc.stdout}\n--- stderr ---\n{proc.stderr}"
            )

    def repr_failure(self, excinfo, style=None):
        if isinstance(excinfo.value, ScriptSuiteFailure):
            return str(excinfo.value)
        if isinstance(excinfo.value, subprocess.TimeoutExpired):
            return (
                f"{self.path.name} did not finish within {_SCRIPT_TIMEOUT_SECONDS}s. "
                "A hung script suite is a failure, not a skip."
            )
        return super().repr_failure(excinfo, style=style)

    def reportinfo(self):
        return self.path, 0, f"script suite: {self.path.name}"


class ScriptSuiteFile(pytest.File):
    def collect(self):
        yield ScriptSuiteItem.from_parent(self, name=self.path.name)


def pytest_pycollect_makemodule(module_path, parent):
    """The single interception point for BOTH layers.

    🔴 This is `pytest_pycollect_makemodule` and NOT `pytest_collect_file`, and
    the difference is load-bearing. `pytest_collect_file` is not a firstresult
    hook: pytest UNIONS every plugin's return, so claiming the file there left
    the builtin python plugin free to ALSO collect it as a Module -- measured,
    the suite was collected twice and the import still detonated (1943 items
    *and* an error on test_bash_guard.py). `pytest_pycollect_makemodule` IS
    firstresult, so returning a node here REPLACES the Module pytest would have
    built, and the import never happens.

    Layer 1: a script suite becomes a subprocess-backed node.
    Layer 2: everything else becomes a Module that reports a SystemExit at
             import as a named CollectError instead of an INTERNALERROR.
    """
    if is_script_suite(module_path):
        return ScriptSuiteFile.from_parent(parent, path=module_path)
    return _SystemExitIsACollectError.from_parent(parent, path=module_path)


class _SystemExitIsACollectError(pytest.Module):
    def collect(self):
        try:
            return list(super().collect())
        except SystemExit as exc:
            raise self.CollectError(
                f"{self.path.name} called sys.exit({exc.code!r}) while pytest was "
                "IMPORTING it, so it never yielded any tests.\n"
                "\n"
                "This file is a hand-rolled script suite living under a `test_*.py` "
                "name. Give it top-level `def test_*` functions to be collected "
                "normally, or leave it script-shaped -- conftest.py in this "
                "directory classifies a file with no top-level test functions as a "
                "script suite and runs it as a subprocess instead of importing it.\n"
                "\n"
                "You are seeing this message rather than an INTERNALERROR / "
                "'no tests ran' because that pair was indistinguishable from a "
                "clean zero."
            ) from exc
