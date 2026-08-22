#!/usr/bin/env python3
"""Run all tests for check-clickup-addressed skill."""
import shutil, sys
from pathlib import Path

TEST_DIR = Path(__file__).parent


def run_test_file(test_file):
    """Run a test file and return (passed, failed).

    🔴 An IMPORT failure is counted as a FAILURE, not allowed to crash the run.

    Until 2026-08-21 a module that failed to import raised straight out of `main()`: no
    further test files ran, and — the part that actually did damage — **no `Total:` line was
    printed at all**. Every reader of this suite, including this skill's own docs, is told to
    read that line rather than an exit code. An absent line reads as nothing rather than as
    failure, and through a `grep -E '✗|Total:'` filter it is literally empty output.

    That is how a `_selfrun.py` with a SyntaxError was committed and pushed as "122 passed":
    a stale `__pycache__/_selfrun.cpython-312.pyc` served the pre-edit bytecode for one run
    (`PYTHONDONTWRITEBYTECODE=1` stops bytecode being WRITTEN, never READ), and when the
    cache was gone the suite crashed instead of reporting. Both halves now fail loudly:
    the count below, and the cache purge in `main()`.
    """
    import importlib.util
    spec = importlib.util.spec_from_file_location(test_file.stem, test_file)
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except BaseException as e:                      # SyntaxError, ImportError, SystemExit…
        print(f"  ✗ {test_file.name}: FAILED TO IMPORT — {type(e).__name__}: {e}")
        return 0, 1

    tests = [v for k, v in vars(module).items() if k.startswith("test_")]
    if not tests:
        # A module that collects zero tests is indistinguishable from a passing one in the
        # totals. Say so; a suite silently covering nothing is this skill's own headline bug.
        print(f"  ✗ {test_file.name}: collected 0 tests")
        return 0, 1
    passed = 0
    failed = 0
    
    for test in tests:
        try:
            test()
            print(f"  ✓ {test.__name__}")
            passed += 1
        except SystemExit as e:
            # SystemExit is a BaseException, so a bare `except Exception` does NOT catch it
            # and it propagates out of main(), killing the whole run: no further test files,
            # no "Total: N passed, M failed" line, and an exit code a reader is told
            # elsewhere in this skill not to trust. Found by the round-4 mutation sweep,
            # where a mutant made parse_args reject a flag a test passes it; the mutant
            # scored HARNESS ERROR rather than KILLED. Any code under test that calls
            # sys.exit() has this shape.
            print(f"  ✗ {test.__name__}: SystemExit({e.code}) escaped the test")
            failed += 1
        except Exception as e:
            print(f"  ✗ {test.__name__}: {e}")
            failed += 1

    return passed, failed


def _purge_bytecode():
    """Delete every `__pycache__` under the skill before importing anything.

    🔴 `PYTHONDONTWRITEBYTECODE=1` prevents bytecode being WRITTEN. It does nothing about
    bytecode being READ, and CPython validates a cached module on source mtime-in-whole-
    SECONDS plus size — so an edit that lands in the same second as the last import and
    happens not to change the size is invisible, and the ORIGINAL module is imported.

    Measured 2026-08-21: a `_selfrun.py` edited into a SyntaxError imported cleanly from a
    stale `.pyc` and the suite reported "122 passed, 0 failed". It was committed and pushed
    on the strength of that line. The scripts here are edited constantly by mutation
    batteries and by hand, which is exactly the workload the mtime heuristic mis-serves.
    """
    for cache in (TEST_DIR.parent).rglob("__pycache__"):
        shutil.rmtree(cache, ignore_errors=True)


def main():
    _purge_bytecode()
    test_files = sorted(TEST_DIR.glob("test_*.py"))

    total_passed = 0
    total_failed = 0

    for test_file in test_files:
        print(f"\n--- {test_file.name} ---")
        passed, failed = run_test_file(test_file)
        total_passed += passed
        total_failed += failed
    
    print(f"\n{'='*40}")
    print(f"Total: {total_passed} passed, {total_failed} failed")
    
    return 1 if total_failed else 0


if __name__ == "__main__":
    sys.exit(main())
