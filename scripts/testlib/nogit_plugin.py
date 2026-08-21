#!/usr/bin/env python3
"""The ONE place that makes "a test cannot WRITE to a repo it does not own"
true.

Read `testlib/nogit.py` first — it carries the measurement, the reflog excerpt
and the reasoning for the read/write split. This module is only the wiring, and
it is deliberately the same wiring as `testlib/nolaunch_plugin.py`: one
implementation, registered from `scripts/tests/conftest.py` so a bare
`pytest scripts/tests` is covered, and exposed through `python -m
testlib.nogit` so the runner can install one shim for a whole run.

🔴 WHY THE SAME SHAPE AND NOT A NEW ONE
----------------------------------------
`nolaunch_plugin` exists in its current form because the previous one lived in
a single `conftest.py` and therefore protected 1 pytest target of 17 — the
declarations-vs-instances trap: one enforcement SITE that read as systemic
while covering a seventeenth of the surface. Copying a fixture into N conftests
is what produced that, twice (#399 and #614). So this module is the rule, once,
and the places that need it register it rather than reimplementing it.

🔴 TWO LAYERS, FOR THE SAME REASON AS THE LAUNCHER POLICY
----------------------------------------------------------
  L1 — PROCESS BOUNDARY (`nogit.install()` + PATH[0]).
       This is the layer that matters, and unlike the launcher case it is not
       merely the broader one — it is the ONLY one that can see the hazard.
       The callers that resolve a repo for themselves are shell scripts the
       tests spawn: `drift-check.sh` pipes its remote leg through `ssh … bash
       -s`, and with a stub `ssh` that leg runs LOCALLY, where `DRIFT_REPO` is
       unset and `${DRIFT_REPO:-$HOME/workspace/devrc}` resolves to the real
       clone. Those `git` calls are made by a grandchild process. A
       `subprocess` patch inside the pytest process cannot observe them at all;
       only something inherited through the environment can.

  L2 — IN-PROCESS, BY BASENAME (`_patch_subprocess()`).
       PATH cannot shadow an ABSOLUTE path, and this tree contains
       absolute-path git invocations. L2 keys on the basename of argv[0] and
       rewrites the launch to the shim, so an absolute reach lands in the same
       log and under the same policy. Its stated limit is identical to the
       launcher policy's: it sees only launches made by THIS python process, so
       a child that execs an absolute git path is invisible to both layers.

🔴 THIS IS AN INVARIANT, NOT A CLEANUP
---------------------------------------
Stated because the obvious alternative was tried and failed in this very
incident: the offending test DID restore the `origin` URL it clobbered, and the
real clone was nevertheless found still poisoned long after every run had
ended — because a restore only happens on clean teardown, and a killed or
failing run skips it. Anything whose safety depends on teardown running is
therefore not a fix. The shim refuses the write at the moment of the call, so
there is nothing to undo and a crash mid-test cannot leave damage behind.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from . import nogit

# Written to the log once per pytest SESSION — the per-target positive control
# for the plugin itself, exactly as `nolaunch_plugin.SESSION_MARKER` is. A
# target with no marker never loaded this plugin, and a guard that never loaded
# is indistinguishable from a guard that found nothing: both produce a clean
# log. The marker is what separates those two.
SESSION_MARKER = "nogit(session)"


def _log_marker(stub_dir: Path, note: str) -> None:
    log = nogit.log_path(stub_dir)
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a", encoding="utf-8") as fh:
        fh.write(f"{SESSION_MARKER} {note}\n")


def _inherited_stub_dir() -> Path | None:
    """The shim dir an outer runner already installed, if there is one.

    Verified, not trusted — an env var naming a directory that does not carry
    the shim would silently downgrade this session to no protection at all,
    which is the failure mode that looks exactly like success.
    """
    raw = os.environ.get(nogit.STUB_DIR_ENV)
    if not raw:
        return None
    d = Path(raw)
    if not (d / "git").exists():
        return None
    return d


# --------------------------------------------------------------------------- #
# L2 — in-process interception of ABSOLUTE-path git launches
# --------------------------------------------------------------------------- #
_ORIGINAL_POPEN = subprocess.Popen


def _redirect_argv(argv, stub_dir: Path):
    """Rewrite an absolute-path `git` invocation to the shim. Else None.

    Deliberately narrow, for the same reason `nolaunch_plugin._redirect_argv`
    is — it must not perturb the thousands of tests that have nothing to do
    with this:
      * only a LIST/TUPLE argv (a `shell=True` string goes through /bin/sh,
        which resolves on PATH, so L1 already owns it);
      * only when argv[0] contains a path separator (a bare `git` is a PATH
        lookup — L1's job, and rewriting it here would HIDE L1 breaking);
      * only when the basename is exactly `git`;
      * never when it already points into the shim dir.
    """
    if not isinstance(argv, (list, tuple)) or not argv:
        return None
    first = argv[0]
    if isinstance(first, bytes):
        first = os.fsdecode(first)
    if not isinstance(first, str) or os.sep not in first:
        return None
    if os.path.basename(first) != "git":
        return None
    try:
        if Path(first).resolve().parent == Path(stub_dir).resolve():
            return None
    except OSError:  # pragma: no cover — a path that cannot be resolved
        pass
    return [str(Path(stub_dir) / "git"), *list(argv)[1:]]


def _patch_subprocess(stub_dir: Path):
    """Route absolute-path git calls through the shim.

    `Popen` is the choke point: `run`, `call`, `check_call` and `check_output`
    all go through it, so patching it once covers the module rather than four
    names that must each be remembered.
    """
    class _NoGitPopen(_ORIGINAL_POPEN):
        def __init__(self, args, *a, **kw):
            redirected = _redirect_argv(args, stub_dir)
            if redirected is not None:
                log = nogit.log_path(stub_dir)
                with log.open("a", encoding="utf-8") as fh:
                    fh.write("git(abs) %s\n"
                             % " ".join(str(x) for x in list(args)[1:]))
                args = redirected
            super().__init__(args, *a, **kw)

    subprocess.Popen = _NoGitPopen
    return _ORIGINAL_POPEN


# --------------------------------------------------------------------------- #
# The fixture
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="session", autouse=True)
def no_real_git_writes(tmp_path_factory):
    """Put a policy-enforcing `git` first on PATH for the whole session.

    Session-scoped and AUTOUSE, for the reason the launcher fixture is:
    protection a test has to remember to ask for is protection the next test
    forgets — and the test that forgets is the one that renames a branch in the
    operator's clone.

    The allowed roots include this session's pytest basetemp, so a fixture that
    builds a clone under `tmp_path` writes freely and every legitimate git
    operation in the suite is untouched. The DENIED root is the tree under
    test, and it wins over the allow-list — which is what keeps the policy live
    in the nix sandbox, where the source is unpacked under `$TMPDIR` and would
    otherwise be inside an allowed root.
    """
    basetemp = Path(tmp_path_factory.getbasetemp()).resolve()
    allowed = nogit.default_roots(basetemp)
    denied = nogit.denied_roots()

    inherited = _inherited_stub_dir()
    if inherited is not None:
        stub_dir = inherited
    else:
        stub_dir = Path(tmp_path_factory.mktemp("nogit"))
        # install() BEFORE the prepend — the shim resolves the real binary via
        # `shutil.which`, so a shim dir already on PATH makes it exec ITSELF.
        #
        # The roots are BAKED IN here, not left to the environment: a test that
        # builds a replacing `env=` dict (keeping only HOME/PATH/GIT_CONFIG_*)
        # still reaches the shim through PATH, and would otherwise run it with
        # no policy at all. Three such tests exist in this suite.
        nogit.install(stub_dir, allowed=allowed, denied=denied)

    prev = {k: os.environ.get(k) for k in (
        "PATH", nogit.STUB_DIR_ENV, nogit.ROOTS_ENV, nogit.DENY_ROOTS_ENV,
        *nogit.GIT_LOCATION_ENV)}

    # 🔴 Scrub the repository-location environment for the whole session.
    #
    # An AMBIENT `GIT_DIR` is the reproduced mechanism of the incident: it
    # redirects a `git -C <fixture>` write into whatever repo it names, while
    # `--show-toplevel` still reports the fixture. A pytest session has no
    # business inheriting any of these, so they are removed rather than
    # validated.
    #
    # 🔴 THIS IS THE WEAKEST LAYER, AND IT IS NOT THE GUARD. It only helps when
    # the variable arrives from OUTSIDE. A test that sets `GIT_DIR` itself —
    # in an `env=` dict, a `monkeypatch.setenv`, or a shell it spawns — hands
    # it straight to the child and this scrub never sees it. The shim's
    # per-call check is what covers that case, and it is the thing to keep
    # working if these two ever disagree.
    for var in nogit.GIT_LOCATION_ENV:
        os.environ.pop(var, None)

    entries = [p for p in prev["PATH"].split(os.pathsep) if p] if prev["PATH"] else []
    # FIRST, unconditionally. A later entry cannot shadow a real binary sitting
    # in an earlier one, so "on PATH" is not the property that matters —
    # "before every ambient entry" is.
    if not entries or entries[0] != str(stub_dir):
        os.environ["PATH"] = str(stub_dir) + os.pathsep + (prev["PATH"] or "")
    os.environ[nogit.STUB_DIR_ENV] = str(stub_dir)
    # Also exported, so a test can READ the policy and a child that inherits
    # the environment sees the same values the shim was built with. The baked
    # copy is what makes the policy hold when this does not survive.
    os.environ[nogit.ROOTS_ENV] = allowed
    os.environ[nogit.DENY_ROOTS_ENV] = denied

    original_popen = _patch_subprocess(stub_dir)
    _log_marker(stub_dir, " ".join(sys.argv[1:]) or "(no args)")
    try:
        yield stub_dir
    finally:
        subprocess.Popen = original_popen
        for k, v in prev.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
