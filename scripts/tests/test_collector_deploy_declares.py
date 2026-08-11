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
