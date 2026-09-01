"""Every test file that stands up the store server must site its store off disk.

🔴 THIS IS A SEAM GUARD, AND IT EXISTS BECAUSE THE FIRST FIX MISSED TWO OF THREE
SITES. devrc#1211 moved the store onto tmpfs in `test_subsystem_store_api.py` and
nowhere else; the very next PR gated after it merged went red on `TestAppendLands` in
`test_cairn_write.py`, which open-codes its own disk-backed fixture against the same
`api.build_server`. Every one of those files was individually fine. What was broken
was the RELATIONSHIP between them, which no per-file test could see.

So this asserts a LEDGER, and it fails when the set GROWS *or* SHRINKS:

  * a NEW file that calls `build_server` and does not use the shared siting is the
    regression this exists to catch — it starts disk-backed and silently rejoins the
    flake population.
  * a file that stops calling `build_server` should be removed from the ledger, and
    leaving a stale name here would quietly weaken the guard.

🔴 It deliberately checks the SOURCE, not behaviour at runtime. A behavioural check
("the store ended up on tmpfs") cannot run where no tmpfs exists — CI may well be
such a place — so it would pass vacuously in exactly the environment that matters.
The structural check holds everywhere. `test_subsystem_store_api.py` carries the
behavioural half (`TestTheStoreIsSitedOffTheContendedDisk`); the two are different
claims and both are needed.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
TESTS = REPO / "scripts" / "tests"

# The ledger. Every file here calls `api.build_server`, therefore writes through
# `server.py:_replace_bytes`, therefore fsyncs inside the request — and must take its
# store root from `testlib.store_siting`.
EXPECTED_SERVER_TESTS: frozenset[str] = frozenset(
    {
        "test_subsystem_store_api.py",
        "test_cairn_write.py",
        "test_cairn_cli.py",
    }
)

_BUILD_SERVER = re.compile(r"\bbuild_server\s*\(")


def _uses_siting(path: Path) -> bool:
    """Does this file actually IMPORT and CALL the shared siting?

    🔴 PARSED, NOT GREPPED, AND THAT IS THE WHOLE POINT. The first version of this
    check was `re.search(r"\\bstore_siting\\b|\\bstore_root\\s*\\(")`, and it SURVIVED
    the mutation that matters: strip the import and the call, leave the explanatory
    COMMENT that says "the root comes from testlib.store_siting", and the regex still
    matched — a file reading as compliant while its store sat back on the contended
    disk. A guard on a WORD is walkable by anything that can spell the word, comments
    and docstrings included; this asserts the STRUCTURE, which prose cannot fake.

    Both halves are required, because either alone is satisfiable without the other:
    an unused import, or a `store_root(...)` call on some unrelated object.
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return False

    imported = False
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and any(
            a.name == "store_siting" or a.asname == "store_siting" for a in node.names
        ):
            imported = True
        elif isinstance(node, ast.Import) and any(
            a.name.endswith("store_siting") or a.asname == "store_siting"
            for a in node.names
        ):
            imported = True

    called = any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "store_root"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "store_siting"
        for node in ast.walk(tree)
    )
    return imported and called


def _test_files() -> list[Path]:
    return sorted(p for p in TESTS.glob("test_*.py") if p.is_file())


def _calls_build_server(path: Path) -> bool:
    try:
        return bool(_BUILD_SERVER.search(path.read_text(encoding="utf-8")))
    except OSError:
        return False


def test_the_ledger_names_exactly_the_files_that_stand_up_the_store_server():
    found = {p.name for p in _test_files() if _calls_build_server(p)}
    # This file names those filenames in prose, which would make it match itself.
    found.discard(Path(__file__).name)

    missing = sorted(EXPECTED_SERVER_TESTS - found)
    extra = sorted(found - EXPECTED_SERVER_TESTS)
    assert not extra, (
        f"these files call build_server but are NOT in the ledger: {extra}. "
        "A new store-server test starts DISK-BACKED and silently rejoins the "
        "fsync-contention flake population. Add it to the ledger AND make it take "
        "its root from testlib.store_siting.store_root()."
    )
    assert not missing, (
        f"the ledger names files that no longer call build_server: {missing}. "
        "Remove them — a stale name weakens this guard without anyone noticing."
    )


def test_every_ledgered_file_takes_its_store_root_from_the_shared_siting():
    offenders = []
    for name in sorted(EXPECTED_SERVER_TESTS):
        path = TESTS / name
        if not path.is_file():
            offenders.append(f"{name} (missing)")
            continue
        if not _uses_siting(path):
            offenders.append(name)
    assert not offenders, (
        f"these ledgered files do not use testlib.store_siting: {offenders}. "
        "They stand up the store server, so their writes fsync inside the request; "
        "on a contended disk that is what fails the gate on unrelated PRs."
    )


def test_the_scan_can_actually_SEE_a_build_server_call():
    # Positive control. Without it, a regex that matched nothing would make both
    # tests above pass vacuously — `found` would be empty, `extra` empty, and only
    # `missing` would fire, which reads as a ledger problem rather than a broken
    # scanner.
    hits = [p.name for p in _test_files() if _calls_build_server(p)]
    assert len(hits) >= 3, (
        f"the build_server scan found only {hits} — expected at least the three "
        "ledgered files. The scan, not the ledger, is what to fix."
    )
