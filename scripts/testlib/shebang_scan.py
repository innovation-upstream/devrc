"""THE structural scan for runtime-written shebangs — one implementation.

A test that writes an executable stub while running must not give it
`#!/usr/bin/env <interp>`: the nix build sandbox (the AUTHORITATIVE gate,
`nix build .#checks.x86_64-linux.pytests`) has no `/usr/bin/env`, while every
NixOS dev host does. See `testlib.mockbin` for the full account. This module is
the deterministic half: it finds the call sites, so the rule is enforced rather
than remembered.

🔴 SELF-MATCH. The needles are assembled from CHARACTER CODES, never written as
literals, because a scan whose pattern appears in its own source reports ITSELF
— the same failure mode as `pgrep -f <pattern>` matching its own shell. The
first version of this guard (#298) failed on its own two source lines. Keep it
that way: no literal quoted `#!` anywhere in this file.

Scope note (2026-08-02): #298's version scanned ONE directory —
`scripts/collector/opencode/tests` — so it could not see
`scripts/dl-router/tests/test_setup_script.py`, which carried the identical
defect the whole time. The scan is repo-wide now, with a PINNED allowlist for
the sites that handle it a different (still-correct) way.
"""
from __future__ import annotations

from pathlib import Path

# `#!` and the two quoted forms a source line can carry it in, built at runtime.
_HB = chr(35) + chr(33)
NEEDLES = (chr(34) + _HB, chr(39) + _HB)


def line_is_offender(lineno: int, line: str) -> bool:
    """True when `line` embeds a quoted shebang.

    Line 1 is always exempt: that is the module's OWN shebang, and pytest
    imports test modules rather than exec'ing them, so it never runs.
    """
    if lineno == 1:
        return False
    return any(n in line for n in NEEDLES)


def scan_file(path: Path) -> list[tuple[int, str]]:
    """Return `(lineno, stripped_line)` for every offending line in `path`."""
    out = []
    text = path.read_text(encoding="utf-8", errors="replace")
    for i, line in enumerate(text.splitlines(), 1):
        if line_is_offender(i, line):
            out.append((i, line.strip()))
    return out


def scan_tree(root: Path, pattern: str = "test_*.py") -> list[tuple[Path, int, str]]:
    """Scan every `pattern` file under `root`, recursively, sorted by path."""
    hits = []
    for py in sorted(root.rglob(pattern)):
        for lineno, line in scan_file(py):
            hits.append((py, lineno, line))
    return hits


def offending_source_line(interp: str = "/usr/bin/env bash") -> str:
    """Build a line that MUST be flagged — for positive controls.

    Assembled the same way as the needles, for the same self-match reason.
    """
    return "p.write_text(" + chr(34) + _HB + interp + chr(34) + ")"
