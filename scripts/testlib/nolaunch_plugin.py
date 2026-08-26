#!/usr/bin/env python3
"""The ONE place that makes "a test cannot reach a real launcher" true — for
EVERY pytest target, not for one directory.

🔴 WHAT WAS WRONG WITH THE PREVIOUS SHAPE
------------------------------------------
#399 built the mechanism (`testlib/nolaunch.py`) and installed it from
`scripts/tests/conftest.py`. `scripts/run-tests.sh` runs **one pytest process
per target directory** and there are 17 of them, so a session-scoped conftest in
one directory protected **1 of 17**. A guard that reads as systemic covering a
seventeenth of the surface is the declarations-vs-instances trap from
claude/RULES.md: the count of enforcement SITES was 1 and looked like enough,
while the count of protected INSTANCES was 1 of 17.

It was not theoretical. #423's escape (158 real `dunstify` toasts in 49 minutes)
came from a seam test run against a base-ref sandbox, and the thing that closed
it was #399's PATH fixture — which existed in exactly one directory.

🔴 WHY A PLUGIN AND NOT 17 CONFTESTS
-------------------------------------
Copying a conftest into each target directory would satisfy the coverage count
and regenerate the bug at 17 sites: 17 copies drift, and the 18th target added
next month gets none. "One rule, one place" — this module is the rule, and it is
attached at the single point where every target is invoked
(`scripts/run-tests.sh`'s `python -m pytest … -p testlib.nolaunch_plugin`), so a
target added to `HERMETIC_TARGETS` is protected by construction rather than by
someone remembering.

`scripts/tests/conftest.py` still exists and now loads THIS module, so a bare
`pytest scripts/tests` outside the runner goes through the same code — one
implementation, two entry points, no second copy of the logic.

🔴 TWO LAYERS, BECAUSE PATH IS NECESSARY AND DEMONSTRABLY NOT SUFFICIENT
------------------------------------------------------------------------
  L1 — PROCESS BOUNDARY (`nolaunch.install()` + PATH[0]).
       Covers every launch that goes through a PATH lookup, including launches
       made by CHILD processes (a shell script a test runs, a `subprocess` in
       production code). This is what run-tests.sh also exports for the
       NON-pytest targets, which have no plugin to load.

  L2 — IN-PROCESS, BY BASENAME (`_patch_subprocess()`).
       PATH cannot shadow an ABSOLUTE path. `scripts/browser-bridge/server.py`
       resolves `i3-msg` through `_I3_MSG_FALLBACKS`
       (`/run/current-system/sw/bin/i3-msg`, `~/.nix-profile/bin/i3-msg`) when
       `shutil.which` returns None — which is exactly what happens when a test
       CLOBBERS PATH. No stub directory can ever shadow that, so L1 is
       structurally blind to it. L2 keys on the BASENAME of argv[0] instead of
       on PATH, and rewrites the launch to the stub, so an absolute-path reach
       lands in the same log as a PATH one.

       Its own limit, stated rather than implied: L2 only sees launches made by
       THIS python process. A child process that execs an absolute launcher path
       is invisible to both layers — `testlib.launcher_scan.absolute_launchers()`
       scans for that shape and pins it instead.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from . import nolaunch

# Published so a test can find the stub dir WITHOUT requesting the fixture —
# the only way to observe that the protection reached a test that never asked.
STUB_DIR_ENV = "DEVRC_TEST_LAUNCH_STUB_DIR"

# Written to the launch log once per pytest SESSION. `run-tests.sh` requires
# exactly one of these per pytest target: it is the per-target positive control
# for the plugin itself. A guard nobody has watched fire in a given target is
# indistinguishable from one that does not cover it — and a marker-less target
# means this plugin never loaded there, which is the failure that would
# otherwise be a silent zero.
SESSION_MARKER = "nolaunch(session)"


def _log_marker(stub_dir: Path, note: str) -> None:
    # 🔴 DELIBERATELY NOT `[<worker>]`-TAGGED, unlike every launch line. This
    # marker is `run-tests.sh`'s per-TARGET positive control — it counts one per
    # xdist worker that ran a test and compares against `-n N` — so the runner
    # already owns its attribution and a tag would buy nothing. The consequence,
    # stated rather than implied: `nolaunch.recorded()` filtered to a worker does
    # NOT return these; `worker=ALL_WORKERS` does.
    log = nolaunch.log_path(stub_dir)
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a", encoding="utf-8") as fh:
        fh.write(f"{SESSION_MARKER} {note}\n")


def _inherited_stub_dir() -> Path | None:
    """The stub dir `run-tests.sh` already installed, if this session is under it.

    Reusing it rather than making a second one is what lets the runner attribute
    every intercept to the target that produced it: one log, one dir, one
    ledger. A session that is NOT under the runner (a bare `pytest …`) gets its
    own — the protection must not be conditional on the runner.
    """
    raw = os.environ.get(STUB_DIR_ENV)
    if not raw:
        return None
    d = Path(raw)
    # Verified, not trusted: an env var naming a directory that does not carry
    # the stubs would silently downgrade this session to no protection at all.
    if not (d / "systemd-run").exists():
        return None
    return d


# --------------------------------------------------------------------------- #
# L2 — in-process interception of ABSOLUTE-path launches
# --------------------------------------------------------------------------- #
_ORIGINAL_POPEN = subprocess.Popen


def _redirect_argv(argv, stub_dir: Path):
    """Rewrite an absolute-path launcher invocation to the stub. Else None.

    Deliberately narrow — it must not perturb the 7,000 tests that have nothing
    to do with this:
      * only a LIST/TUPLE argv (a `shell=True` string goes through /bin/sh,
        which resolves on PATH, so L1 already owns it);
      * only when argv[0] contains a path separator (a bare name is a PATH
        lookup — L1's job, and rewriting it would hide L1 breaking);
      * only when the BASENAME is a ledgered launcher;
      * never when it already points into the stub dir.
    """
    if not isinstance(argv, (list, tuple)) or not argv:
        return None
    first = argv[0]
    if isinstance(first, bytes):
        first = os.fsdecode(first)
    if not isinstance(first, str) or os.sep not in first:
        return None
    name = os.path.basename(first)
    if name not in nolaunch.HOST_LAUNCHERS:
        return None
    try:
        if Path(first).resolve().parent == Path(stub_dir).resolve():
            return None
    except OSError:  # pragma: no cover — a path that cannot be resolved
        pass
    return [str(Path(stub_dir) / name), *list(argv)[1:]]


def _patch_subprocess(stub_dir: Path):
    """Make `subprocess.Popen` route absolute-path launcher calls to the stub.

    Popen is the choke point: `run`, `call`, `check_call` and `check_output` all
    go through it, so patching it once covers the whole module rather than four
    names that must each be remembered.
    """
    class _NoLaunchPopen(_ORIGINAL_POPEN):
        def __init__(self, args, *a, **kw):
            redirected = _redirect_argv(args, stub_dir)
            if redirected is not None:
                log = nolaunch.log_path(stub_dir)
                # 🔴 SAME `[<worker>]` SECOND FIELD as the L1 stubs write. Both
                # write paths must be tagged or `recorded()`'s per-worker filter
                # would silently drop the L2 line — an absolute-path reach that
                # WAS intercepted reading as one that was never seen, which is
                # the reassuring-zero failure this whole guard exists to avoid.
                with log.open("a", encoding="utf-8") as fh:
                    fh.write("%s(abs) [%s] %s\n" % (
                        os.path.basename(str(args[0])),
                        nolaunch.worker_tag(),
                        " ".join(str(x) for x in args[1:])))
                args = redirected
            super().__init__(args, *a, **kw)

    subprocess.Popen = _NoLaunchPopen
    return _ORIGINAL_POPEN


# --------------------------------------------------------------------------- #
# The fixture
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="session", autouse=True)
def no_real_launchers(tmp_path_factory):
    """Prepend a record-only stub dir for every host launcher to PATH.

    Session-scoped and AUTOUSE: protection a test has to remember to ask for is
    protection the next test forgets — and the test that forgets is the one that
    fires a toast on someone's desktop. `test_no_real_launchers.py` pins the
    autouse flag with a control/mutant pair in a nested session, because every
    test in that file requesting the fixture by name once made deleting the flag
    completely invisible.

    It composes with per-test stubs rather than fighting them: a test's own stub
    lives in its `tmp_path`, which still precedes this directory in the child
    PATH (`test_notify_failure.py` depends on exactly that ordering).
    """
    inherited = _inherited_stub_dir()
    if inherited is not None:
        stub_dir = inherited
    else:
        stub_dir = Path(tmp_path_factory.mktemp("nolaunch"))
        # install() BEFORE the prepend: the systemctl stub resolves the real
        # binary through `shutil.which`, so a stub dir already on PATH would
        # make it exec ITSELF forever. nolaunch.install() asserts on that.
        nolaunch.install(stub_dir)

    prev_path = os.environ.get("PATH", "")
    prev_stub = os.environ.get(STUB_DIR_ENV)
    entries = [p for p in prev_path.split(os.pathsep) if p]
    # FIRST, unconditionally. A later entry cannot shadow a real binary sitting
    # in an earlier one, so "on PATH" is not the property that matters —
    # "before every ambient entry" is.
    if not entries or entries[0] != str(stub_dir):
        os.environ["PATH"] = str(stub_dir) + os.pathsep + prev_path
    os.environ[STUB_DIR_ENV] = str(stub_dir)

    original_popen = _patch_subprocess(stub_dir)
    _log_marker(stub_dir, " ".join(sys.argv[1:]) or "(no args)")
    try:
        yield stub_dir
    finally:
        subprocess.Popen = original_popen
        os.environ["PATH"] = prev_path
        if prev_stub is None:
            os.environ.pop(STUB_DIR_ENV, None)
        else:
            os.environ[STUB_DIR_ENV] = prev_stub
