#!/usr/bin/env python3
"""GUARD 9's IN-PROCESS control: proof the shim is armed for THIS target.

🔴 WHY A RUNNER-SIDE PROBE IS NOT ENOUGH
-----------------------------------------
`run-tests.sh` fires two deliberate refusals before each target and counts them.
That proves the shim is armed **in the runner's environment**. It does not prove
it is armed inside the target's own process — and if it isn't, every
`refused=0` in the per-target table is a statement about the runner, so the
32/32 table reads as rigorous while measuring one thing thirty-two times.

This plugin fires the same two probes from INSIDE the pytest process, tagged
`via=plugin`. The runner requires BOTH counts, and prints them separately, so a
target protected only by inheritance cannot be mistaken for one whose own
process was checked.

🔴 AND THE TWO MECHANISMS ARE GENUINELY DIFFERENT, WHICH IS WHY BOTH ARE COUNTED
--------------------------------------------------------------------------------
The five `HOOK_TESTS` and the `SHELL_TEST` are not pytest. They can never load a
plugin, so their protection IS inheritance — the exported environment plus the
PATH shim — and their `plugin=0` is correct rather than a failure. Collapsing
the two into one number would make a dead plugin on a pytest target look exactly
like a healthy non-pytest target, which is precisely where a future regression
would hide.

🔴 NESTING, and it is load-bearing accounting rather than tidiness
-------------------------------------------------------------------
Some tests START ANOTHER PYTEST (`test_no_real_launchers.py` builds control /
mutant pairs that way), so this plugin loads in those child sessions too and
they inherit the guard-dir handle. A nested session must still be PROTECTED —
that is the half that matters — but it must not write into the runner's
per-target ledger, where an extra control looks like a miscount. The flag below
is set for children and checked on entry, the same shape as
`spool_plugin.NESTED_ENV`.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from . import norepo

#: Set for CHILD sessions. Its presence means "you are not a target".
NESTED_ENV = "DEVRC_TEST_GITGUARD_IN_SESSION"


def _probe(env: dict, argv: list[str]) -> None:
    try:
        subprocess.run(argv, env=env, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        # A probe that cannot run writes no control line, which the runner
        # turns into a loud per-target failure. Swallowing here keeps the
        # failure in the ONE place that reports it.
        pass


def pytest_configure(config) -> None:  # noqa: D401 - pytest hook
    guard_dir = os.environ.get(norepo.GUARD_DIR_ENV)
    if not guard_dir:
        # Not under `run-tests.sh`. There is no ledger to write into, and
        # inventing one here would report a control nobody asked for.
        return
    if os.environ.get(NESTED_ENV):
        return
    os.environ[NESTED_ENV] = "1"

    env = dict(os.environ)
    env[norepo.PROBE_ENV] = "1"
    env[norepo.PROBE_VIA_ENV] = "plugin"

    # 1. The NETWORK probe. Works in every environment, including the nix build
    #    sandbox where no real repository exists to protect.
    _probe(env, ["git", "ls-remote", "https://guard9-probe.invalid/probe.git"])

    # 2. The PROTECTED-REPO probe, only where there is one. The target path is
    #    read from the guard dir rather than re-derived, so the runner and this
    #    plugin cannot disagree about what is protected.
    target = norepo.probe_target(Path(guard_dir))
    if target:
        _probe(env, ["git", "-C", target, "config", "--local",
                     "devrc.guard9.probe", "x"])
