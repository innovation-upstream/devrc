#!/usr/bin/env python3
"""Guards on `scripts/lib/cairn_pin.py` — the seam devrc reaches the pinned
`cairn` client's modules through.

🔴 WHAT THIS FILE IS FOR, AND WHY IT IS NOT A UNIT TEST FILE. devrc used to carry
its own forked copies of five reader modules beside the OSS originals; the
operator decided on 2026-09-08 to delete them and consume the pinned package
instead. The modules themselves are cairn's to test — its own suite owns
`test_subsystem_{recall,resolver,read_store}.py` and `test_cairn_doctor.py`, and
devrc's copies of those four were deleted with the modules. What is left over is
a SEAM: two directories of Python that must not shadow each other, one env var,
one PATH derivation, and a writer that has to raise classes the pinned reader
catches. Every guard here pins a RELATIONSHIP between the two sides, because
that is the thing no test on either side can see on its own.
"""
from __future__ import annotations

import ast
import importlib.util
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
LIB = ROOT / "scripts" / "lib"

sys.path.insert(0, str(LIB))
import cairn_pin  # noqa: E402


def _pin() -> Path:
    """The pinned lib dir, or skip with the refusal's own words.

    ⚠ A SKIP, and it is the only one in this file: without the pinned client
    deployed there is no relationship to measure, and asserting one anyway would
    be a guard that passes in an environment it cannot observe. The `nix` checks
    set `CAIRN_LIB`, so the gating tier does NOT take this branch — see
    `flake.nix`'s `checks.pytests`.
    """
    try:
        return cairn_pin.pinned_lib_dir()
    except cairn_pin.CairnPinUnresolved as exc:  # pragma: no cover - env-dependent
        pytest.skip(f"the pinned cairn client is not available here: {exc}")


# =============================================================================
# The overlap ledger — the guard the whole consolidation rests on
# =============================================================================

#: 🔴 THE ASSERTED LEDGER OF MODULE NAMES PRESENT ON BOTH SIDES. Exactly one, and
#: it is deliberate: devrc's `scripts/lib/timeouts.py` is imported by
#: `scripts/cairn`, `scripts/cairn-who` and `scripts/claim-work`, none of which
#: are cairn's to govern, while the pinned copy exists because the OSS client
#: needs the same predicate. The set is pinned BOTH WAYS — a name that appears
#: on both sides and is not listed here fails, and a name listed here that stops
#: overlapping fails too — because an unnoticed overlap resolves to whichever
#: copy happened to be at the front of `sys.path` when it was first imported, and
#: that order is NOT stable (see `test_the_pinned_reader_prepends_its_own_dir`).
EXPECTED_OVERLAP = {"timeouts"}


def _module_names(d: Path) -> set[str]:
    return {p.stem for p in d.glob("*.py") if not p.name.startswith("__")}


def test_exactly_one_module_name_is_present_on_BOTH_sides():
    """The overlap ledger, asserted as a set rather than a count.

    A count would pass while one name left and another arrived. The set is the
    claim: these names, and no others, are resolvable from two files.
    """
    pin = _pin()
    overlap = _module_names(LIB) & _module_names(pin)
    assert overlap == EXPECTED_OVERLAP, (
        f"the devrc/pin module-name overlap is {sorted(overlap)}, expected "
        f"{sorted(EXPECTED_OVERLAP)}. A name in BOTH directories resolves to "
        f"whichever copy is nearer the front of sys.path, and that order changes "
        f"depending on whether the pinned reader has been imported yet — so an "
        f"unledgered overlap is a module that silently swaps identity. Delete "
        f"one copy, or add it here WITH the compatibility guard below extended "
        f"to cover it."
    )


def test_the_one_overlapping_module_is_SAFE_to_resolve_either_way():
    """🔴 THE BEHAVIOURAL HALF — the set guard above is structural and not enough.

    For the one ledgered name devrc genuinely cannot control which copy a given
    process gets, so "there is only one overlap" is not a safety claim on its
    own. This asserts the pinned copy is a SUPERSET of devrc's public surface: a
    consumer that imports either gets every name it asked for. It cannot prove
    the bodies agree — that is what makes it a guard on the seam rather than on
    the module — and it would go red the moment the pinned copy DROPPED something
    devrc imports, which is the failure that would otherwise reach a user as an
    ImportError from a file they did not edit.
    """
    pin = _pin()
    for name in sorted(EXPECTED_OVERLAP):
        ours = _public_names(LIB / f"{name}.py")
        theirs = _public_names(pin / f"{name}.py")
        missing = ours - theirs
        assert not missing, (
            f"the pinned `{name}` does not define {sorted(missing)}, which devrc's "
            f"copy does. Because the pinned reader prepends its own directory to "
            f"sys.path, a devrc consumer importing `{name}` after the reader gets "
            f"the PINNED copy — so a dropped name is an ImportError in devrc code "
            f"caused by a pin bump."
        )


def _public_names(path: Path) -> set[str]:
    out: set[str] = set()
    for node in ast.parse(path.read_text()).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if not node.name.startswith("_"):
                out.add(node.name)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and not t.id.startswith("_"):
                    out.add(t.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if not node.target.id.startswith("_"):
                out.add(node.target.id)
    return out


def test_the_pinned_reader_prepends_its_own_dir_so_ordering_is_not_an_invariant():
    """🔴 MEASURED, AND IT IS WHY THE LEDGER ABOVE EXISTS AT ALL.

    `cairn_pin.ensure()` APPENDS, so before the reader loads devrc's own modules
    win. The pinned `subsystem_recall` then runs
    `sys.path.insert(0, <its own dir>)` at import time and the pinned lib becomes
    `sys.path[0]`. This pins that fact rather than asserting the ordering devrc
    wanted, because asserting the wish would be a guard that passes only while
    nobody imports the reader — i.e. in exactly the tests that do not matter.

    If a future pin STOPS prepending, this goes red and the ledger's
    compatibility half can be relaxed. That is a real finding, not a chore.
    """
    pin = _pin()
    src = (pin / "subsystem_recall.py").read_text()
    assert re.search(r"sys\.path\.insert\(\s*0\s*,", src), (
        "the pinned `subsystem_recall` no longer prepends its own directory to "
        "sys.path. That is a BEHAVIOUR CHANGE in the pin, and it makes the "
        "overlap ledger's compatibility guard stricter than it needs to be — "
        "re-read cairn_pin's docstring before changing anything."
    )


# =============================================================================
# Resolution: two routes, and a refusal that is not a fallback
# =============================================================================

def test_route_1_is_the_env_var(tmp_path, monkeypatch):
    lib = tmp_path / "fake-lib"
    lib.mkdir()
    for m in cairn_pin.MARKER_MODULES:
        (lib / m).write_text("")
    monkeypatch.setenv(cairn_pin.CAIRN_LIB_ENV, str(lib))
    assert cairn_pin.pinned_lib_dir() == lib


def test_route_1_BEATS_route_2(tmp_path, monkeypatch):
    """The override has to actually override, or the sandbox would silently test
    whatever client happens to be on the builder's PATH."""
    lib = tmp_path / "fake-lib"
    lib.mkdir()
    for m in cairn_pin.MARKER_MODULES:
        (lib / m).write_text("")
    monkeypatch.setenv(cairn_pin.CAIRN_LIB_ENV, str(lib))
    if shutil.which("cairn") is None:
        pytest.skip("no `cairn` on PATH, so there is no route 2 to beat")
    assert cairn_pin.pinned_lib_dir() == lib


def test_a_candidate_is_REJECTED_on_CONTENT_not_on_being_a_directory(
    tmp_path, monkeypatch
):
    """🔴 THE POSITIVE-CONTENT CHECK, AND ITS CONTROL IS THE TEST ABOVE.

    An empty `libexec/cairn/lib` is what a half-fetched or half-built store path
    looks like. Accepting it on `is_dir()` would turn a deploy fault into an
    `ImportError` several frames from the cause. Each marker is removed in turn,
    so a guard that only ever checked the FIRST one fails here.

    ⚠ `PATH` is NOT emptied. That is the point: a SET-but-unusable `CAIRN_LIB`
    must refuse even when a perfectly good `cairn` is on PATH, or the sandbox
    could green against the wrong client. The control for the other direction —
    that an UNSET variable does fall through — is `test_route_1_BEATS_route_2`
    plus `_pin()` resolving at all in this file.

    🔴 THE MARKER SET IS SPELLED LITERALLY HERE, NOT READ OFF THE MODULE, and
    that is not style. Iterating `cairn_pin.MARKER_MODULES` derives the test's
    expectation from the implementation it tests: MEASURED — shrinking the tuple
    to `("entry_shape.py",)` SURVIVED a fully green run of this file, because the
    loop shrank with it and never built the case the deletion exposes. The
    literal below is the claim; the equality assert is what makes a deliberate
    change to the set fail HERE rather than silently narrow the guard.
    """
    expected = {"entry_shape.py", "subsystem_resolver.py"}
    assert set(cairn_pin.MARKER_MODULES) == expected, (
        f"the pin's marker set is {sorted(cairn_pin.MARKER_MODULES)}, not "
        f"{sorted(expected)}. TWO markers are deliberate: one cannot tell a real "
        f"lib dir from a directory that happens to hold a single file with that "
        f"name. If this is an intended change, change the literal here in the "
        f"same commit and say which half-built directory the new set still "
        f"rejects."
    )
    for held_back in sorted(expected):
        lib = tmp_path / f"lib-without-{held_back}"
        lib.mkdir()
        for m in sorted(expected):
            if m != held_back:
                (lib / m).write_text("")
        monkeypatch.setenv(cairn_pin.CAIRN_LIB_ENV, str(lib))
        with pytest.raises(cairn_pin.CairnPinUnresolved) as exc:
            cairn_pin.pinned_lib_dir()
        assert held_back in str(exc.value), (
            f"the refusal does not name the missing `{held_back}` — a deploy "
            f"fault reported without naming what is missing is a fault report "
            f"nobody can act on."
        )


def test_it_REFUSES_rather_than_falling_back(tmp_path, monkeypatch):
    """🔴 NO SILENT FALLBACK, AND THE MESSAGE CARRIES BOTH ROUTES.

    devrc's own copies are DELETED, so there is nothing to degrade to. The
    refusal must say which two routes were tried and what to do, or it reads as
    "that module does not exist" and sends the reader hunting for a file that
    was removed on purpose.
    """
    monkeypatch.setenv(cairn_pin.CAIRN_LIB_ENV, "")
    monkeypatch.setenv("PATH", str(tmp_path))  # nothing named `cairn` in here
    with pytest.raises(cairn_pin.CairnPinUnresolved) as exc:
        cairn_pin.pinned_lib_dir()
    msg = str(exc.value)
    assert "pinned cairn lib not found" in msg, "the sentinel moved"
    assert cairn_pin.CAIRN_LIB_ENV in msg, "route 1 is not named"
    assert "on PATH" in msg, "route 2 is not named"
    assert "home-manager switch" in msg, "the remedy is not named"
    assert "no local fallback" in msg, (
        "the message does not say the absence is deliberate — without that a "
        "reader looks for the deleted copies"
    )


def test_ensure_APPENDS_and_is_idempotent(monkeypatch):
    pin = _pin()
    monkeypatch.setattr(sys, "path", ["/first-entry", "/second-entry"])
    cairn_pin.ensure()
    assert sys.path[-1] == str(pin), (
        "ensure() did not APPEND. Prepending would shadow devrc's own modules "
        "with the store copies — see the overlap ledger."
    )
    before = list(sys.path)
    cairn_pin.ensure()
    assert sys.path == before, "ensure() is not idempotent"


def test_running_the_module_prints_the_path(monkeypatch):
    """The shell surface `build-push.sh` and `verify-byte-identity.sh` use."""
    pin = _pin()
    out = subprocess.run(
        [sys.executable, str(LIB / "cairn_pin.py")],
        capture_output=True, text=True, check=True,
    )
    assert out.stdout.strip() == str(pin)


def test_running_the_module_EXITS_NONZERO_when_it_cannot_resolve(tmp_path):
    """🔴 THE NEGATIVE CONTROL FOR THE SHELL SURFACE. Both shell consumers run
    under `set -e` and use the output as a path; a refusal that exited 0 with an
    empty line would build `"/subsystem_recall.py"` and fail somewhere useless."""
    env = dict(os.environ, PATH=str(tmp_path))
    env.pop("CAIRN_LIB", None)
    out = subprocess.run(
        [sys.executable, str(LIB / "cairn_pin.py")],
        capture_output=True, text=True, env=env,
    )
    assert out.returncode != 0, "the refusal exited 0"
    assert "pinned cairn lib not found" in out.stderr


# =============================================================================
# The writer/reader class seam
# =============================================================================

def test_the_writer_raises_the_classes_the_PINNED_reader_catches():
    """🔴 CLASS IDENTITY, NOT NAME EQUALITY — the defect this whole slice risks.

    The pinned reader catches `entry_shape.StoreMissingError`. If devrc's writer
    kept a same-named class of its own, an `except` over there would not match a
    raise over here and the failure would present as an unhandled traceback in a
    tool neither side changed. `is` is the assertion; `==` on names would pass
    against exactly the bug.
    """
    _pin()
    cairn_pin.ensure()
    import entry_shape  # noqa: PLC0415
    import subsystem_touch as st  # noqa: PLC0415

    for name in (
        "CairnError", "TouchError", "GitError",
        "StoreMissingError", "RepoPathMissingError",
    ):
        # 🔴 `getattr(..., None)` RATHER THAN A BARE LOOKUP, so a MISSING name
        # fails on THIS guard's sentence instead of on an `AttributeError` raised
        # while reaching the assertion. Measured at the pre-consolidation base:
        # the bare form died with `module 'subsystem_touch' has no attribute
        # 'CairnError'`, which is red for the right reason and says nothing about
        # the property — and a red that does not name its own claim is how a
        # regression matrix stops being evidence.
        theirs = getattr(entry_shape, name, None)
        ours = getattr(st, name, None)
        assert ours is not None and ours is theirs, (
            f"subsystem_touch.{name} is {ours!r}, not entry_shape.{name} "
            f"({theirs!r}) — the writer either defines a look-alike the pinned "
            f"reader's `except` cannot match, or does not expose the name at all."
        )
    assert entry_shape.TouchError is entry_shape.CairnError, (
        "`TouchError` stopped aliasing `CairnError` in the pin, so devrc's ~25 "
        "writer errors no longer share a base with the shared vocabulary and "
        "`except TouchError` has silently narrowed."
    )


def test_every_devrc_writer_error_still_answers_to_except_TouchError():
    """The compatibility claim the re-basing rests on, exercised rather than read."""
    _pin()
    cairn_pin.ensure()
    import subsystem_touch as st  # noqa: PLC0415

    errs = [
        v for v in vars(st).values()
        if isinstance(v, type) and issubclass(v, Exception)
        and v.__module__ == "subsystem_touch"
    ]
    assert len(errs) >= 20, (
        f"only {len(errs)} writer-defined error classes found — this guard was "
        f"written against ~25 and a collapse to a handful means the discovery "
        f"is broken, not that the errors were tidied"
    )
    for e in errs:
        assert issubclass(e, st.TouchError), f"{e.__name__} is not a TouchError"


def test_the_writer_takes_its_shared_vocabulary_from_the_pin_not_from_itself():
    """🔴 A LEDGER OF NAMES, FAILING IF IT GROWS *OR* SHRINKS.

    The point of the slice is that these live in ONE module. A redefinition in
    `subsystem_touch` would type-check, pass every writer test, and quietly
    reintroduce the fork — so the check is on the module the name RESOLVES to,
    not on whether it exists.
    """
    _pin()
    cairn_pin.ensure()
    import entry_shape  # noqa: PLC0415
    import subsystem_touch as st  # noqa: PLC0415

    shared = {
        "SHAPE_HEADINGS", "STORE_IS_PER_HOST", "CairnError", "GitError",
        "RepoPathMissingError", "StoreMissingError", "TouchError",
        "derive_scope", "store_host", "store_host_line",
    }
    for name in sorted(shared):
        # Same `None` default, for the same reason as the class-identity guard
        # above: a name that is simply absent must fail on this sentence.
        theirs = getattr(entry_shape, name, None)
        ours = getattr(st, name, None)
        assert ours is not None and ours is theirs, (
            f"`subsystem_touch.{name}` is {ours!r}, not the pinned "
            f"`entry_shape.{name}` ({theirs!r}) — the shared vocabulary has "
            f"forked again at that name, or the writer no longer exposes it."
        )

    # …and the names devrc deliberately KEEPS, because the pinned spellings are
    # the sanitised ones and drop devrc-specific facts. Listed so that quietly
    # adopting the pinned version — which would be a user-visible regression in
    # the refusal message — fails here instead of shipping.
    for name in ("REPO_PATH_HANDLES", "repo_path_missing_message", "_scope_hint"):
        assert hasattr(st, name), f"devrc's own `{name}` is gone"
    assert st.repo_path_missing_message is not entry_shape.repo_path_missing_message, (
        "`subsystem_touch.repo_path_missing_message` is now the PINNED one. That "
        "drops the sentence naming the pre-exported repo handles and narrows the "
        "scope hint from four readings to one — a deliberate devrc-side "
        "enrichment, not redundancy."
    )


def test_devrc_refusal_message_keeps_the_handle_sentence_the_pin_dropped():
    """🔴 THE REGRESSION GUARD, and the reason `scope_for_repo` is a wrapper.

    The pinned `repo_path_missing_message` is the OSS-sanitised one: it names no
    repo handles, and its scope hint appears only when the directory exists. Both
    of those are devrc-specific facts the packaged client cannot state. devrc
    answers the non-directory case before delegating, so its own CLIs keep the
    fuller message — and this pins that, against the exact input that produces it.
    """
    _pin()
    cairn_pin.ensure()
    import subsystem_touch as st  # noqa: PLC0415

    with pytest.raises(st.RepoPathMissingError) as exc:
        st.scope_for_repo("no-such-repo-here", store_root="/nonexistent-store")
    msg = str(exc.value)
    for handle in st.REPO_PATH_HANDLES:
        assert handle in msg, (
            f"the refusal no longer names {handle}. The pinned message drops the "
            f"handle sentence; devrc adds it back at its own boundary, and this "
            f"is the guard on that."
        )
    assert "NOT CHECKED" in msg, (
        "the four-reading scope hint is gone — an unreadable/absent store root "
        "now reads as 'no such scope', which is the confident zero the hint "
        "exists to prevent."
    )


def test_scope_derivation_still_comes_from_the_pin(tmp_path):
    """The other half: enriching the message must not have re-forked the RULE.

    ⚠ THE SUBJECT IS A THROWAWAY `git init`, NOT THIS REPO. The authoritative
    tier runs against a `cp -r` of the source tree with NO `.git` in it, so
    `scope_for_repo(ROOT)` would raise `GitError` there and this guard would be
    red in the tier that gates rather than in the one that does not. It also
    keeps the assertion about the RULE instead of about whatever this checkout
    happens to be called.
    """
    _pin()
    cairn_pin.ensure()
    import entry_shape  # noqa: PLC0415
    import subsystem_touch as st  # noqa: PLC0415

    assert st.derive_scope is entry_shape.derive_scope, (
        f"`subsystem_touch.derive_scope` is {st.derive_scope!r}, not the pinned "
        f"`entry_shape.derive_scope` ({entry_shape.derive_scope!r}). The scope "
        f"RULE is the one thing the reader and the writer absolutely must not "
        f"have two copies of — disagree and the writer accrues entries under one "
        f"name while the reader surfaces an empty scope under another, which "
        f"renders as 'nothing recorded yet'."
    )

    repo = tmp_path / "some-repo"
    repo.mkdir()
    env = dict(
        os.environ,
        HOME=str(tmp_path),
        GIT_CONFIG_GLOBAL="/dev/null",
        GIT_CONFIG_SYSTEM="/dev/null",
    )
    subprocess.run(
        ["git", "-C", str(repo), "init", "-q", "-b", "main"], env=env, check=True
    )
    assert st.scope_for_repo(repo) == entry_shape.scope_for_repo(repo) == "some-repo", (
        "devrc's wrapper and the pinned function disagree about a repo's scope — "
        "the silent, total failure the shared module exists to prevent."
    )


# =============================================================================
# The deleted copies stay deleted
# =============================================================================

#: The five modules devrc deleted when it consolidated onto the pin. A
#: re-appearance is not a merge accident to shrug at: a `scripts/lib/` copy sorts
#: BEFORE the appended pin, so it would silently take over from the packaged one
#: everywhere except after the reader has been imported — the worst of both.
CONSOLIDATED_AWAY = (
    "host_identity", "subsystem_resolver", "subsystem_recall",
    "cairn_doctor", "subsystem_read_store",
)


def test_the_five_consolidated_modules_are_not_back_in_scripts_lib():
    back = [m for m in CONSOLIDATED_AWAY if (LIB / f"{m}.py").exists()]
    assert not back, (
        f"{back} reappeared in scripts/lib/. devrc consumes these from the "
        f"pinned `cairn` flake input (operator decision, 2026-09-08); a local "
        f"copy shadows the pin because `cairn_pin` APPENDS, and the two would "
        f"drift exactly as they did before."
    )


def test_every_consolidated_module_resolves_to_the_pin():
    """The positive half — absence proves deletion, not that the pin answers."""
    pin = _pin()
    cairn_pin.ensure()
    for m in CONSOLIDATED_AWAY:
        spec = importlib.util.find_spec(m)
        assert spec is not None and spec.origin is not None, f"{m} does not resolve"
        assert Path(spec.origin).parent == pin, (
            f"`{m}` resolves to {spec.origin}, not to the pinned lib {pin}"
        )
