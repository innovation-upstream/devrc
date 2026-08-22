#!/usr/bin/env python3
"""The wiring that makes "a test cannot WRITE to a repo it does not own" true.

Read `testlib/nogit.py` first — it carries the measurement, the reflog excerpt
and the reasoning for the read/write split. This module is only the wiring.

🔴 WHAT THIS MODULE DOES **NOT** CLAIM — REGISTRATION SCOPE
------------------------------------------------------------
`scripts/run-tests.sh` runs ONE pytest process per target and there are 25 of
them. This module is registered from `scripts/tests/conftest.py` ONLY, so a
bare `pytest scripts/tests` is covered and **the other 24 targets are not**.
MEASURED, running the same write through each target's own pytest process:
`scripts/tests` -> rc 99 (refused); `scripts/repo-cos/tests` — which contains
the culprit `test_prescan.py::_init_clone` — -> **rc 0, the write landed**;
`scripts/dl-router/tests` -> rc 0.

That gap is NOT closed here, deliberately. `scripts/run-tests.sh` belongs to
another change which registers ITS guard with `-p` on the single pytest command
line, and `-p` reaches every target regardless of conftest inheritance. Adding
a second `-p`, or a second auto-loading `scripts/conftest.py`, would put two
plugins on the same class — the exact collision measured below with `Popen`.
So the runner registration is that change's to own, and the residual gap is
stated rather than papered over.

(A single `scripts/conftest.py` WOULD close it: measured, pytest auto-loads it
for all 13 sampled targets, directory and file targets alike, with no runner
edit. It is not added here only because it would duplicate enforcement.)

🔴 TWO LAYERS
--------------
  L1 — PROCESS BOUNDARY (`nogit.install()` + PATH + `GIT_EXEC_PATH`).
       The layer that matters. The callers that resolve a repo for themselves
       are shell scripts the tests spawn, and their `git` calls are made by a
       grandchild process; a `subprocess` patch inside the pytest process
       cannot observe them at all. `GIT_EXEC_PATH` is what extends this to
       git's OWN children — see `nogit.exec_path_farm()` for the measurement
       that made it necessary.

  L2 — IN-PROCESS, BY BASENAME (`_patch_subprocess()`).
       PATH cannot shadow an ABSOLUTE path, and this tree contains
       absolute-path git invocations. L2 keys on the basename of argv[0] and
       rewrites the launch to the shim.

🔴 L2 WAS COMPLETELY INERT, AND THE MECHANISM IS WORTH STATING
---------------------------------------------------------------
MEASURED: `subprocess.Popen.__mro__` during a session was
`['_NoLaunchPopen', 'Popen', 'object']` — this module's class was simply GONE,
and an absolute-path `git config` against a denied repo returned 0 with no
banner. Both plugins captured the PRISTINE `subprocess.Popen` at IMPORT time
and each assigned its own subclass *of that pristine class*, so whichever
fixture ran last discarded the other's patch entirely; and each teardown
restored the pristine class, discarding the survivor too.

The fix is to capture whatever `subprocess.Popen` IS at PATCH time, subclass
that, and restore that — then the two compose in either order.
`test_both_launch_policies_survive_in_the_popen_chain` pins it, because a
silently-inert layer is indistinguishable from a working one.

🔴 THIS IS AN INVARIANT, NOT A CLEANUP
---------------------------------------
Stated because the obvious alternative was tried and failed in this very
incident: the offending test DID restore the `origin` URL it clobbered, and the
real clone was nevertheless found still poisoned long after every run had
ended — because a restore only happens on clean teardown, and a killed or
failing run skips it. The shim refuses the write at the moment of the call, so
there is nothing to undo.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from . import nogit

# Written to the log once per pytest SESSION — the per-target positive control
# for the plugin itself. A target with no marker never loaded this plugin, and
# a guard that never loaded is indistinguishable from a guard that found
# nothing: both produce a clean log.
SESSION_MARKER = "nogit(session)"

# The names the marker's `via=` field can carry.
VIA_SELF = "self"
VIA_INHERITED = "inherited"


class ForeignGitShim(RuntimeError):
    """`DEVRC_TEST_GIT_STUB_DIR` names something that is not this policy's shim."""


def _log_marker(stub_dir: Path, note: str, via: str) -> None:
    """Record the session marker, INCLUDING how the shim was obtained.

    🔴 `via=` exists because the marker used to be written identically whether
    this session installed the shim or adopted an inherited one — so a run that
    adopted a FOREIGN `git` and installed nothing at all still reported a
    healthy marker. The field makes those two states distinguishable in the log
    a runner reads.
    """
    log = nogit.log_path(stub_dir)
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a", encoding="utf-8") as fh:
        fh.write(f"{SESSION_MARKER} via={via} {note}\n")


def _inherited_stub_dir() -> Path | None:
    """The shim dir an outer runner already installed — VERIFIED BY CONTENT.

    🔴 THE PREVIOUS VERSION OF THIS FUNCTION WAS A SPELLED GUARD, AND IT WAS
    WALKED. It adopted any directory whose `DEVRC_TEST_GIT_STUB_DIR` contained
    a FILE NAMED `git`, then skipped `install()` entirely — while its own
    docstring claimed it verified the shim. MEASURED with the variable pointed
    at a plain passthrough script: a write to the repo-under-test landed
    **rc 0, no banner**, the session marker was written anyway, and pytest
    reported "1 passed". A filename is walkable by anything called `git`.

    It now asks the shim to prove itself (`nogit.handshake`), which no foreign
    binary can answer and no OLDER revision of this policy can answer either —
    the token is a hash of the policy's own generated body.

    🔴 A FAILED HANDSHAKE RAISES RATHER THAN FALLING BACK. Falling back to
    self-installation would be safe for this session but would silently hide a
    misconfigured outer runner, which is the state that produced the measured
    "1 passed" above. The session fails and names the directory.

    🔴 A SHIM THAT IS OURS BUT SCOPED TO ANOTHER TREE RETURNS None INSTEAD —
    self-install, do not raise. See the deny-floor branch for the measurement
    that forced the distinction.
    """
    raw = os.environ.get(nogit.STUB_DIR_ENV)
    if not raw:
        return None
    d = Path(raw)
    answer = nogit.handshake(d)
    if answer is None:
        raise ForeignGitShim(
            f"{nogit.STUB_DIR_ENV}={raw!r} does not hold THIS policy's git "
            f"shim: it did not answer `{nogit.HANDSHAKE_FLAG}` with "
            f"`{nogit.HANDSHAKE_MAGIC} {nogit.policy_fingerprint()}`. That is "
            "either a foreign `git`, or a shim generated by a different "
            "revision of testlib/nogit.py. Adopting it would run this session "
            "with NO git policy while still writing a healthy session marker — "
            "measured, that reports '1 passed' with the guard absent. Unset "
            f"{nogit.STUB_DIR_ENV} to let this session install its own.")
    # 🔴 GENUINELY OURS, BUT SCOPED TO A DIFFERENT TREE -> SELF-INSTALL, DO NOT
    # RAISE. The deny floor is what keeps the policy live in the nix sandbox
    # (where the source sits inside an allowed root), so an inherited shim that
    # does not carry THIS tree is not usable as-is.
    #
    # Raising here was measured to be WRONG, and the case is ordinary: several
    # tests in this suite COPY the repo to a tmp dir and run a nested pytest in
    # it. The nested session inherits `DEVRC_TEST_GIT_STUB_DIR` from the outer
    # one, its `repo_root()` is the copy, and the outer shim's baked deny roots
    # name the original — a perfectly healthy arrangement that a raise turned
    # into nine red tests. Installing our own correctly-scoped shim is both
    # safe and what the caller wants; `via=` records that it happened.
    #
    # The FOREIGN case above still raises, because there the question is not
    # "which tree" but "is this a guard at all".
    denied = [p for p in answer.get("denied", "").split(":") if p]
    if str(nogit.repo_root()) not in denied:
        return None
    # An inherited shim in AUDIT mode records violations and lets every one of
    # them through. Same treatment: replace it rather than adopt it.
    if answer.get("audit") == "1":
        return None
    return d


# --------------------------------------------------------------------------- #
# L2 — in-process interception of ABSOLUTE-path git launches
# --------------------------------------------------------------------------- #
def _redirect_argv(argv, stub_dir: Path, tmp_root: Path | None = None):
    """Rewrite an absolute-path `git` invocation to the shim. Else None.

    Deliberately narrow, so it cannot perturb the thousands of tests that have
    nothing to do with this:
      * only a LIST/TUPLE argv (a `shell=True` string goes through /bin/sh,
        which resolves on PATH, so L1 already owns it);
      * only when argv[0] contains a path separator (a bare `git` is a PATH
        lookup — L1's job, and rewriting it here would HIDE L1 breaking);
      * only when the basename is exactly `git`;
      * never when it already points into the shim dir or its exec-path farm;
      * never when it lives inside THIS SESSION'S tmp tree.

    🔴 THE LAST EXCLUSION IS NOT TIDINESS — WITHOUT IT, L2 REPLACES A TEST'S
    OWN INSTRUMENT WITH THE GUARD. MEASURED as two red tests the moment L2
    started working: `scripts/tests/test_ship_converge.py::_git_shim` builds a
    `git` under `tmp_path` that is real for every subcommand except one
    deliberately sabotaged verb, then calls it by absolute path to prove
    `ship.sh`'s currency guard reacts. L2 rewrote that call to the shim, the
    sabotage never happened, `ls-files` returned its real output, and the test
    failed on an assertion about the guard it was validating.

    A `git` a test built inside its own tmp tree is a DOUBLE, not a reach
    around PATH to the real binary — which is the only thing L2 exists to
    catch. It is also not an escape: such a double resolves whatever it
    delegates to through `PATH` (or a `shutil.which` taken inside the session),
    both of which lead back to the shim.
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
        resolved = Path(first).resolve()
        if resolved.parent in (Path(stub_dir).resolve(),
                               nogit.exec_path_dir(stub_dir).resolve()):
            return None
        if tmp_root is not None and resolved.is_relative_to(
                Path(tmp_root).resolve()):
            return None
    except OSError:  # pragma: no cover — a path that cannot be resolved
        pass
    return [str(Path(stub_dir) / "git"), *list(argv)[1:]]


def _patch_subprocess(stub_dir: Path, tmp_root: Path | None = None):
    """Route absolute-path git calls through the shim; return what to restore.

    🔴 SUBCLASSES WHATEVER `subprocess.Popen` IS RIGHT NOW, not a pristine copy
    captured at import. See this module's docstring for the measurement — with
    an import-time capture the two launch policies silently deleted each
    other's patch and L2 was inert.
    """
    current = subprocess.Popen

    class _NoGitPopen(current):  # type: ignore[misc,valid-type]
        def __init__(self, args, *a, **kw):
            redirected = _redirect_argv(args, stub_dir, tmp_root)
            if redirected is not None:
                log = nogit.log_path(stub_dir)
                try:
                    with log.open("a", encoding="utf-8") as fh:
                        fh.write("git(abs) %s\n"
                                 % " ".join(str(x) for x in list(args)[1:]))
                except OSError:  # pragma: no cover — log dir gone
                    pass
                args = redirected
            super().__init__(args, *a, **kw)

    subprocess.Popen = _NoGitPopen
    return current


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
    builds a clone under `tmp_path` writes freely. The DENIED root is the tree
    under test, and it wins over the allow-list.
    """
    basetemp = Path(tmp_path_factory.getbasetemp()).resolve()
    allowed = nogit.default_roots(basetemp)
    denied = nogit.denied_roots()

    inherited = _inherited_stub_dir()
    if inherited is not None:
        stub_dir = inherited
        via = VIA_INHERITED
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
        via = VIA_SELF

    scrubbed = (*nogit.GIT_LOCATION_ENV, *nogit.GIT_EXEC_ENV,
                nogit.MODE_ENV, "GIT_CONFIG_COUNT", "GIT_EXEC_PATH",
                "GIT_ALTERNATE_OBJECT_DIRECTORIES")
    prev = {k: os.environ.get(k) for k in (
        "PATH", nogit.STUB_DIR_ENV, nogit.ROOTS_ENV, nogit.DENY_ROOTS_ENV,
        *scrubbed)}
    # `GIT_CONFIG_KEY_<n>` / `GIT_CONFIG_VALUE_<n>` are indexed, so the names
    # are not knowable in advance — they are collected from the live env.
    indexed = [k for k in os.environ
               if k.startswith(("GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_"))]
    prev.update({k: os.environ.get(k) for k in indexed})

    # 🔴 Scrub the repository-location environment for the whole session.
    #
    # An AMBIENT `GIT_DIR` is the reproduced mechanism of the incident.
    # `MODE_ENV` is scrubbed too: it no longer does anything (the mode is baked
    # in), but leaving a stale value in the environment invites the next reader
    # to wire it back up.
    #
    # 🔴 THIS IS THE WEAKEST LAYER, AND IT IS NOT THE GUARD. It only helps when
    # the variable arrives from OUTSIDE. A test that sets `GIT_DIR` itself
    # hands it straight to the child and this scrub never sees it. The shim's
    # per-call check is what covers that case.
    for var in (*scrubbed, *indexed):
        os.environ.pop(var, None)

    entries = [p for p in prev["PATH"].split(os.pathsep) if p] if prev["PATH"] else []
    # FIRST, unconditionally. A later entry cannot shadow a real binary sitting
    # in an earlier one, so "on PATH" is not the property that matters —
    # "before every ambient entry" is.
    if not entries or entries[0] != str(stub_dir):
        os.environ["PATH"] = str(stub_dir) + os.pathsep + (prev["PATH"] or "")
    os.environ[nogit.STUB_DIR_ENV] = str(stub_dir)
    # 🔴 The exec-path farm, so git's OWN children resolve `git` to the shim.
    # Without it an alias, hook or filter reaches the real binary at PATH[0] —
    # measured, and it let `-c 'alias.x=!git -C <victim> commit …' x` write the
    # victim a commit with the shim installed.
    farm = nogit.exec_path_dir(stub_dir)
    if (farm / "git").exists():
        os.environ["GIT_EXEC_PATH"] = str(farm)
    # Also exported, so a test can READ the policy and a child that inherits
    # the environment sees the same values the shim was built with. The baked
    # copy is what makes the policy hold when this does not survive.
    os.environ[nogit.ROOTS_ENV] = allowed
    os.environ[nogit.DENY_ROOTS_ENV] = denied

    original_popen = _patch_subprocess(stub_dir, basetemp)
    _log_marker(stub_dir, " ".join(sys.argv[1:]) or "(no args)", via)
    try:
        yield stub_dir
    finally:
        subprocess.Popen = original_popen
        for k, v in prev.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
