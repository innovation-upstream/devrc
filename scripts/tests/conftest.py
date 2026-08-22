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

# 🔴 The SAME shape for the activity spool (GUARD 8). `testlib/spool_plugin.py`
# is the implementation and `scripts/run-tests.sh` loads it for EVERY target;
# this import is the second entry point, so a bare `pytest scripts/tests`
# outside the runner cannot append rows to `~/.local/state/activity/spool` —
# which the collector ships to the production ClickHouse `activity.events`.
#
# It is NOT a per-directory copy, and the difference matters: adding the fixture
# to N conftests is exactly what #399 and #614 both did and what left 1 target
# of 17 (and 1 directory of 13) protected. The rule lives in one module; this
# line only registers it. The per-test `monkeypatch.setenv(ACTIVITY_SPOOL_DIR…)`
# calls scattered through this directory stay as defence in depth — they narrow
# the spool per test, which this session-wide floor deliberately does not.
from testlib.spool_plugin import no_real_activity_spool  # noqa: E402,F401

# 🔴 The SAME shape a third time, for the repository the suite RUNS FROM
# (GUARD 9). On 2026-08-21 a gate run rewrote this checkout's `main` with
# fixture commits, deleted the branch, rewrote `core.hooksPath` and
# `remote.origin.url`, and pushed fixture refs to the production remote —
# because `GIT_DIR` overrides the `-C <tmp_path>` every git fixture here
# already passes. `scripts/run-tests.sh` clears the pointers for EVERY target
# (pytest and non-pytest alike); this import is the second entry point, so a
# bare `pytest scripts/tests` gets the same strip plus the per-test detector
# that names whichever test moved a ref.
#
# 🔴 The names are imported INTO THIS CONFTEST'S NAMESPACE, which is what
# registers them — pytest reads hooks and fixtures off the conftest module
# object. `pytest_configure` is deliberately NOT among them: it is an
# initialisation hook and pytest does not call it for a conftest loaded during
# collection, so importing it would look like coverage while providing none.
# The plugin arms its baseline at IMPORT for exactly that reason, so this entry
# point loses nothing but the marker line.
from testlib.gitenv_plugin import (  # noqa: E402,F401
    _devrc_git_repo_isolation,
    pytest_collection_finish,
    pytest_sessionfinish,
)

# 🔴 THE SAME SHAPE A FOURTH TIME, for git WRITES (GUARD 10). GUARD 9 above
# strips the repo POINTERS so a fixture cannot reach this checkout by accident;
# this one refuses a WRITE to any repo outside the session's tmp roots, which is
# the case a pointer strip cannot answer. Different failure, different guard —
# they compose, and the numbering is the only thing that had to change when the
# two landed a day apart. `testlib/nogit_plugin.py` is
# the implementation and `scripts/run-tests.sh` loads it for EVERY target; this
# import is the second entry point, so a bare `pytest scripts/tests` outside the
# runner cannot rewrite the operator's `~/.gitconfig` or push to a real remote.
#
# Measured 2026-08-21: a test ran `githooks/install.sh` for real — that script
# sets `core.hooksPath` --global — and ~63 fixture commits reached the REAL
# origin/main. The fix is one module registered twice, NOT a fixture added to N
# conftests: that is what #399 and #614 each did, leaving 1 target of 17 and 1
# directory of 13 protected while reading as systemic.
from testlib.nogit_plugin import no_real_git  # noqa: E402,F401
