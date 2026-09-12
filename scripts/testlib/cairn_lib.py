#!/usr/bin/env python3
"""Where the PINNED cairn reader modules live on disk, for tests that read source.

🔴 WHY A TESTLIB MODULE AND NOT `ROOT / "scripts" / "lib"`. devrc deleted its five
forked reader modules (`subsystem_recall`, `subsystem_resolver`,
`subsystem_read_store`, `cairn_doctor`, `host_identity`) when it consolidated onto
the `cairn` flake pin. A large number of guards in `scripts/tests/` do not merely
IMPORT those modules — they read their SOURCE: mutation harnesses that copy a file
and re-exec it, "exactly one definition" sweeps, docstring/comment pins. Every one
of those had a hardcoded `scripts/lib/<m>.py`, which now names nothing.

Pointing them at `cairn_pin.pinned_lib_dir()` keeps them working AND makes them
guards on the PIN — which is worth having, because `flake.nix` records that every
other cairn guard in this repo reads text and stays green while the pinned client
is broken. This is the one import seam for that, so a pin bump moves every such
test together instead of leaving a stale path in whichever file nobody re-ran.

⚠ IT IS NOT A SUBSTITUTE FOR cairn's OWN SUITE. cairn tests these modules
upstream; devrc's copies of those assertions are a second, older reading of the
same code. Where the two disagree the PIN is right — devrc's expectation is the
thing to update.
"""
from __future__ import annotations

import sys
from pathlib import Path

_LIB = Path(__file__).resolve().parents[1] / "lib"
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

import cairn_pin  # noqa: E402

__all__ = ["PINNED_LIB", "pinned"]

#: The pinned client's `lib/` directory. Resolved at IMPORT — a test that reads
#: module source cannot be written against "maybe there is a pin", and
#: `cairn_pin` raises with both resolution routes and the remedy named.
PINNED_LIB = cairn_pin.ensure()


def pinned(module: str) -> Path:
    """`<pinned lib>/<module>.py`, asserting it exists.

    The bare path join would hand a caller a `Path` that `read_text()`s into a
    `FileNotFoundError` several frames later; this names the module and the
    directory at the point the mistake is made.
    """
    p = PINNED_LIB / f"{module}.py"
    assert p.is_file(), (
        f"the pinned cairn lib at {PINNED_LIB} has no `{module}.py`. Either the "
        f"module was renamed upstream, or this test is naming a devrc-local "
        f"module that belongs under `scripts/lib/` instead."
    )
    return p
