"""Suite-wide guarantee: no test in scripts/tests can reach a REAL launcher.

🔴 WHY THIS FILE EXISTS — 15 real launches, measured, on a green suite.
Running `scripts/tests/test_rig_control.py` + `test_monitor_blackout.py` on the
dev host created REAL transient systemd timers (fixed unit name, 2-8h out),
fired REAL desktop toasts and drove the chassis RGB, while 29/29 tests passed
and the authoritative nix-sandbox gate stayed green — the sandbox has none of
those binaries, so it is structurally blind to the whole class. The full
measurement and the reasoning live in `scripts/testlib/nolaunch.py`.

ONE mechanism rather than per-file patches: those files build the child PATH as
`str(tmp_path) + ":" + os.environ["PATH"]`, so prepending a stub directory to
`os.environ["PATH"]` for the session covers every test that inherits the
ambient PATH — including tests written tomorrow, which is the half a per-file
patch cannot do.

It does NOT break tests that legitimately assert on a launcher: a test's own
stub goes in its `tmp_path`, which sits BEFORE this directory in the child
PATH, so it still wins (`test_notify_failure.py` depends on exactly that, and
its graphical case is the positive control that this ordering is right).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # scripts/

from testlib import nolaunch  # noqa: E402

# Read by scripts/tests/test_no_real_launchers.py (and available to any test that
# wants to assert on what a script tried to launch).
STUB_DIR_ENV = "DEVRC_TEST_LAUNCH_STUB_DIR"


@pytest.fixture(scope="session", autouse=True)
def no_real_launchers(tmp_path_factory):
    """Prepend a record-only stub dir for every host launcher to PATH.

    Session-scoped and autouse: the protection must not depend on a test
    remembering to ask for it — a test that forgets is exactly the one that
    fires a toast on someone's desktop.
    """
    stub_dir = Path(tmp_path_factory.mktemp("nolaunch"))
    nolaunch.install(stub_dir)

    prev_path = os.environ.get("PATH", "")
    prev_stub = os.environ.get(STUB_DIR_ENV)
    # FIRST, unconditionally. A later entry cannot shadow a real binary sitting
    # in an earlier one, so "on PATH" is not the property that matters — "before
    # every ambient entry" is.
    os.environ["PATH"] = str(stub_dir) + os.pathsep + prev_path
    os.environ[STUB_DIR_ENV] = str(stub_dir)
    try:
        yield stub_dir
    finally:
        os.environ["PATH"] = prev_path
        if prev_stub is None:
            os.environ.pop(STUB_DIR_ENV, None)
        else:
            os.environ[STUB_DIR_ENV] = prev_stub
