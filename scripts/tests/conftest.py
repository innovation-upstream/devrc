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

# 🔴 The SAME shape again, for GIT. `testlib/nogit_plugin.py` is the
# implementation and this import is one entry point; the runner loads the same
# module for every target, so this is not a per-directory copy.
#
# It exists because the suite drove `git commit`, `git branch -m`, `git config
# core.bare true`, `remote set-url` and `git push` against the operator's REAL
# clone — the local reflog puts the `main → trunk` rename at 19:21:35Z and the
# ~40-push storm onto `refs/heads/main` at 19:28:14Z, seven minutes later. The
# offending code RESTORED the URL it clobbered, so every after-the-fact check
# reported a clean repo; the only way to see it was `git remote -v` in the real
# clone WHILE the suite ran.
#
# The policy is therefore an invariant at the moment of the call, not a
# cleanup: a shim first on PATH that lets READS through anywhere and refuses
# WRITES to any repo outside this session's tmp roots. Read
# `testlib/nogit.py` before changing it — in particular the read-verb ledger,
# which is an allowlist so an unknown verb fails CLOSED.
from testlib.nogit_plugin import no_real_git_writes  # noqa: E402,F401
