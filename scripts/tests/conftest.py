"""Suite-wide guarantee: no test in scripts/tests can reach a REAL launcher.

🔴 WHY THIS FILE EXISTS — 15 real launches, measured, on a green suite.
Running `scripts/tests/test_rig_control.py` + `test_monitor_blackout.py` on the
dev host created REAL transient systemd timers (fixed unit name, 2-8h out),
fired REAL desktop toasts and drove the chassis RGB, while 29/29 tests passed
and the authoritative nix-sandbox gate stayed green — the sandbox has none of
those binaries, so it is structurally blind to the whole class. The full
measurement and the reasoning live in `scripts/testlib/nolaunch.py`.

🔴 THE IMPLEMENTATION IS NOT HERE ANY MORE — it is `testlib/nolaunch_plugin.py`,
and this file only imports it. `scripts/run-tests.sh` runs ONE pytest process
per target directory and there are 17 of them, so a fixture defined in this
conftest protected exactly ONE of them; the runner now loads the same module
with `-p testlib.nolaunch_plugin` for EVERY target. This file remains so that a
bare `pytest scripts/tests` outside the runner is protected too — by the same
code, not by a second copy of it. Read that module before changing anything
here; per-directory copies are precisely what it exists to prevent.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # scripts/

# Importing a fixture into a conftest REGISTERS it for this directory. That is
# the whole file: one implementation, two entry points (this import and the
# runner's `-p` flag), no duplicated logic to drift.
from testlib.nolaunch_plugin import STUB_DIR_ENV, no_real_launchers  # noqa: E402,F401
