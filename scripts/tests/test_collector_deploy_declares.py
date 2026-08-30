"""`nix/home.nix` must DECLARE every collector file the tailers import.

🔴 CLAUDE.md, verbatim: "A NEW file must be `git add`ed or the flake silently
omits it from the deploy … The switch succeeds and the file simply is not
there." `scripts/collector/changed_paths.py` is the first file BOTH session
summarisers import from the collector ROOT — `claude/` and `opencode/` are
`recursive = true` directory sources, so a file added inside either of them
ships automatically, and a file added BESIDE them does not.

The failure mode this pins is deployment-shaped and invisible to every other
test in the repo: `home-manager switch` reports success, the unit definition
does not change, and the next 5-minute timer tick dies on `ImportError` —
i.e. BOTH session summarisers stop emitting, which is the exact shape of the
opencode outage that cost 11 hours of telemetry on both hosts.

It is a STRUCTURAL check on the import graph, not a spelling check on one
filename: it derives what to look for from the tailers' own imports, so a second
shared module added tomorrow is covered without editing this file.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
COLLECTOR = ROOT / "scripts" / "collector"
HOME_NIX = ROOT / "nix" / "home.nix"

# The tailers that put the collector ROOT on sys.path and import from it.
IMPORTERS = (
    COLLECTOR / "claude" / "session-tailer.py",
    COLLECTOR / "opencode" / "session_tailer.py",
)

# Modules that live at the collector ROOT (not inside a `recursive = true`
# subdirectory), so each needs its own `home.file` entry.
_IMPORT_RE = re.compile(r"^import\s+([A-Za-z_][A-Za-z0-9_]*)(?:\s+as\s+\w+)?\s*(?:#.*)?$",
                        re.M)


def _root_modules() -> set[str]:
    """Module names the tailers import that resolve to a file at the collector
    ROOT. Derived from the source, so the check cannot go stale."""
    found: set[str] = set()
    for src in IMPORTERS:
        for name in _IMPORT_RE.findall(src.read_text(encoding="utf-8")):
            if (COLLECTOR / f"{name}.py").is_file():
                found.add(name)
    return found


def test_positive_control_the_scan_finds_at_least_one_root_module():
    """A reassuring empty set is indistinguishable from a regex that matches
    nothing, and would make every assertion below vacuously true."""
    mods = _root_modules()
    assert mods, (
        "no collector-root module was detected in "
        f"{[str(p) for p in IMPORTERS]} — the import scan is broken, so the "
        "deploy check below would pass without checking anything"
    )
    assert "changed_paths" in mods


@pytest.mark.parametrize("module", sorted(_root_modules()))
def test_home_nix_declares_the_root_module(module):
    text = HOME_NIX.read_text(encoding="utf-8")
    target = f'home.file.".config/activity-collector/{module}.py"'
    assert target in text, (
        f"{module}.py is imported by a session tailer from the collector ROOT "
        f"but nix/home.nix has no `{target}` entry.\n"
        "The switch will SUCCEED and the file will simply not be deployed; the "
        "next claude-activity-source / opencode timer tick then dies on "
        "ImportError and BOTH summarisers stop emitting.\n"
        f"Fix: add the entry beside collector.py, pointing at "
        f"../scripts/collector/{module}.py."
    )


@pytest.mark.parametrize("module", sorted(_root_modules()))
def test_the_declared_source_path_exists(module):
    """A declared entry pointing at a missing file breaks the whole switch, so
    the two halves are checked separately — an entry that names the wrong path
    is a different failure from an entry that is absent."""
    text = HOME_NIX.read_text(encoding="utf-8")
    assert f"../scripts/collector/{module}.py" in text
    assert (COLLECTOR / f"{module}.py").is_file()


def test_the_import_works_in_the_DEPLOYED_symlink_layout(tmp_path):
    """🔴 THE SEAM. Every other test in this repo imports the tailers from the
    REPO layout, where `scripts/collector/` is a plain directory. What actually
    runs on a host is `~/.config/activity-collector/`: a real directory holding
    per-file SYMLINKS into /nix/store, with `claude/` and `opencode/` as real
    subdirectories of more symlinks. Nothing exercised that arrangement, and it
    is the one where the sibling import can fail.

    Reproduces it: a fake "store" holding the real files, and a fake deployed
    tree of symlinks pointing at them. The module must import THROUGH the
    symlink.

    ⚠ SCOPE, stated rather than implied: this fixture does NOT distinguish
    `.resolve()` from plain parent traversal, because the store mirror also
    holds `changed_paths.py`, so both land somewhere it exists. What it DOES
    catch is the sibling-path setup being wrong or absent — verified by
    mutation: deleting the `sys.path.append` in either tailer turns this red
    and leaves every other test in the repo green.
    """
    store = tmp_path / "store"
    dep = tmp_path / "deployed"
    (store / "claude").mkdir(parents=True)
    (store / "opencode").mkdir(parents=True)
    (dep / "claude").mkdir(parents=True)
    (dep / "opencode").mkdir(parents=True)

    def place(rel: str):
        src = COLLECTOR / rel
        dst_store = store / rel
        dst_store.write_bytes(src.read_bytes())
        (dep / rel).symlink_to(dst_store)

    # 🔴 DERIVED, not hardcoded. This file's header promises that "a second
    # shared module added tomorrow is covered without editing this file", and
    # every assertion above honours that — but this fixture used to carry a
    # literal list, so the promise stopped exactly here. Adding
    # `mention_scan.py` to the collector root made this test fail with
    # `ModuleNotFoundError` in the deployed layout, which is the RIGHT alarm
    # raised for the WRONG reason: the module was declared in nix/home.nix and
    # would have deployed fine; it was the fixture that had not been told.
    # The list now comes from the same `_root_modules()` scan as the rest.
    root_modules = tuple(f"{m}.py" for m in sorted(_root_modules()))
    for rel in root_modules + (
            "claude/session-tailer.py", "claude/_shared.py", "claude/tailer.py",
            "opencode/session_tailer.py", "opencode/_shared.py"):
        place(rel)

    import subprocess
    import sys
    import textwrap

    # 🔴 ONE SUBPROCESS PER SOURCE, and this is not tidiness. Loading both in a
    # single interpreter made the opencode half VACUOUS: the claude tailer runs
    # first and appends the collector root to `sys.path`, so opencode's own
    # sibling-path setup could be deleted entirely and the import still
    # succeeded. Verified by mutation — with both in one process, removing
    # opencode's `sys.path.append` left this test green.
    # A subprocess is also what keeps the repo-layout copies already imported by
    # this pytest run from satisfying the import.
    programs = {
        "claude": """
            import importlib.util, sys
            dep = sys.argv[1]
            sys.path.insert(0, dep + "/claude")
            spec = importlib.util.spec_from_file_location(
                "st", dep + "/claude/session-tailer.py")
            m = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(m)
            assert m.CP.CHANGED_PATHS_CAP > 0
            assert m.build_rollup([])["changed_paths"] is None
            print("OK")
        """,
        "opencode": """
            import sys
            dep = sys.argv[1]
            sys.path.insert(0, dep + "/opencode")
            import session_tailer as o
            assert o.CP.CHANGED_PATHS_CAP > 0
            assert o.build_rollup({}, [], [], directory="")["changed_paths"] == []
            print("OK")
        """,
    }
    for source, prog in programs.items():
        r = subprocess.run([sys.executable, "-c", textwrap.dedent(prog), str(dep)],
                           capture_output=True, text=True)
        assert r.returncode == 0 and "OK" in r.stdout, (
            f"the {source} session summariser cannot import changed_paths.py "
            "through the deployed symlink layout — this is the failure that "
            "takes that source down at the next timer tick.\n"
            f"stdout: {r.stdout}\nstderr: {r.stderr}"
        )


def test_the_tailers_do_not_resolve_symlinks_when_locating_the_root():
    """🔴 `Path(__file__).resolve()` walks INTO /nix/store and loses the
    ~/.config/activity-collector/ prefix, so the sibling import would resolve
    against the store path instead of the deployed tree. Documented in
    opencode/_shared.py's spool bridge; pinned here because the same mistake in
    a tailer is silent until a timer tick fails on a host, not in CI."""
    for src in IMPORTERS:
        text = src.read_text(encoding="utf-8")
        for line in text.splitlines():
            if "parent.parent" in line and "sys.path" not in line:
                assert ".resolve()" not in line, (
                    f"{src.name}: {line.strip()!r} resolves the collector root "
                    "through the nix-store symlink"
                )
