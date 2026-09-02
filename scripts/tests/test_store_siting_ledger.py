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
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
TESTS = REPO / "scripts" / "tests"
sys.path.insert(0, str(REPO / "scripts"))
from testlib import store_siting  # noqa: E402

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

def _calls_build_server_ast(tree: ast.AST) -> bool:
    """Does this module CALL `build_server`, however the name was bound?

    🔴 PARSED, NOT GREPPED — and the regex this replaces was the same spelled-guard
    defect the sibling check had already been fixed for. `re.search(r"\\bbuild_server
    \\s*\\(")` decides who is IN the ledger at all, and it was walked through by
    `from srv import build_server as make_server; make_server(...)`: a new
    disk-backed store-server test, invisible to the guard whose entire purpose is to
    catch exactly that. Measured — the aliased spelling SURVIVED, 3 passed.

    It has a mirror false-positive too: a file that only MENTIONS `build_server(` in
    a comment was reported as an unledgered offender. One such comment already exists
    in `test_subsystem_store_api.py`, masked only because that file is ledgered.

    Both die on an AST, because neither a comment nor a rebinding survives parsing:
    resolve every local name bound to `build_server` (import-as, plain assignment),
    then look for a call to any of them, or to any attribute named `build_server`,
    or a `getattr(x, "build_server")`.
    """
    aliases: set[str] = {"build_server"}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for a in node.names:
                if a.name == "build_server":
                    aliases.add(a.asname or a.name)
        elif isinstance(node, ast.Assign):
            # `mk = api.build_server` / `mk = build_server`
            value = node.value
            bound = (
                isinstance(value, ast.Attribute) and value.attr == "build_server"
            ) or (isinstance(value, ast.Name) and value.id in aliases)
            if bound:
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        aliases.add(target.id)

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr == "build_server":
            return True
        if isinstance(func, ast.Name) and func.id in aliases:
            return True
        # getattr(api, "build_server")(...) — the call's func is itself that getattr
        if (
            isinstance(func, ast.Call)
            and isinstance(func.func, ast.Name)
            and func.func.id == "getattr"
            and len(func.args) >= 2
            and isinstance(func.args[1], ast.Constant)
            and func.args[1].value == "build_server"
        ):
            return True
        if (
            isinstance(func, ast.Name)
            and func.id == "getattr"
            and len(node.args) >= 2
            and isinstance(node.args[1], ast.Constant)
            and node.args[1].value == "build_server"
        ):
            return True
    return False


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
    """AST call OR a `build_server(` inside a STRING LITERAL.

    🔴 THE STRING HALF IS NOT BELT-AND-BRACES — without it this is NARROWER than the
    regex it replaced, and in the worse direction. Seven test files here drive
    `sys.executable -c <script>`; a store server stood up inside such a script is a
    call the AST cannot see, and dropping a file from the ledger silently is worse
    than the comment-only false positive the AST fixed. Measured: a mutant running
    `build_server(...)` from a `textwrap.dedent` block was caught by the old regex
    and SURVIVED the AST-only version.

    Scanning `ast.Constant` strings rather than raw text keeps the `#`-comment
    false positive fixed — a comment is not a Constant.
    ⚠ But a DOCSTRING is both prose AND a Constant, so a file whose docstring
    discusses `build_server(` IS flagged. This file is the live example, handled
    by the `discard` in the ledger test; any other such file needs adding to the
    ledger or rewording. That is the price of not silently dropping a
    subprocess-driven server test, which is the worse error.
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return False
    if _calls_build_server_ast(tree):
        return True
    return any(
        isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and "build_server(" in node.value
        for node in ast.walk(tree)
    )


def test_the_ledger_names_exactly_the_files_that_stand_up_the_store_server():
    found = {p.name for p in _test_files() if _calls_build_server(p)}
    # 🔴 LOAD-BEARING, and it stopped being dead in the commit that called it dead.
    # Widening the scan to `ast.Constant` strings made THIS file match its own
    # docstrings, which mention `build_server(` while explaining the scanner.
    # Measured: delete this line and the suite goes red accusing the ledger file
    # of standing up a store server it does not have.
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


def test_every_ledgered_file_IMPORTS_AND_CALLS_the_shared_siting_at_least_once():
    """🔴 READ THE NAME: at least ONCE, not for every store root in the file.

    An earlier name — `..._takes_its_store_root_from_the_shared_siting` — claimed
    coverage this body does not provide, and that mattered: an audit found
    `scoped_store` still disk-backed while this test passed, because `imported` and
    `called` are scanned independently over the whole module. Reading a guard's name
    as coverage is what stops anyone looking, so the name now states the weaker,
    true thing.

    `scoped_store` is fixed. The residual gap is recorded and NOT closed here:
    `test_subsystem_store_api.py` still builds store roots inline from
    `tmp_path / "store"` at the sites `_DISK_ROOTED_SITES` counts below, each in one
    or two tests rather than a shared fixture. That count is asserted so it can only
    go DOWN — see the next test.
    """
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


# 🔴 A RATCHET, NOT A TARGET — and an AST one, because the first version was SPELLED.
# `test_subsystem_store_api.py` still builds store roots inline from `tmp_path` inside
# individual tests. Those reach `api.append_bullet` -> `_replace_bytes` in-process and
# so fsync inside the request; they are a long tail (one or two tests each) rather than
# the shared fixtures, which are all sited now.
#
# 🔴 The first ratchet was `.count('tmp_path / "store"')` and an audit walked FOUR of
# five spellings through it: `tmp_path/"store"` (no spaces), `tmp_path / 'store'`
# (single quotes), `tmp_path / VAR`, and `tmp_path.joinpath("store")`. There is no
# formatter gate in this repo, so the first two are not normalised away. Counting the
# AST collapses all four — quoting and spacing do not survive parsing at all.
#
# A scripted mass conversion of these sites was attempted and REVERTED: it silently
# skipped 9 of 19 signature edits while its own assertion still passed. Hence a ratchet
# rather than a rushed refactor.
_DISK_ROOTED_SITES = 20

_ROOT_NAMES = {"store", "src"}


def _is_disk_rooted_store_expr(node: ast.AST) -> bool:
    """`tmp_path / "store"` and its spellings — a store root that skips the siting.

    🔴 A BARE `tmp_path / <Name>` IS NOT ENOUGH, and this was measured on the merged
    tree rather than argued. Round 3's audit called the unrestricted Name arm "a false
    accusation"; `main` then added
    `(tmp_path / name).write_text(body)` — a scratch file called "wrapped.md" — and the
    ratchet counted 21, demanding an author use `store_root()` for something that is
    not a store. So a Name only counts when the expression is USED as a store root:
    assigned to a root-ish variable, or passed to `_build_store`/`running`.

    Quoting and spacing still do not matter: they do not survive parsing.
    """
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div) and (
        isinstance(node.left, ast.Name) and node.left.id == "tmp_path"
    ):
        right = node.right
        if isinstance(right, ast.Constant) and right.value in _ROOT_NAMES:
            return True
        return isinstance(right, ast.Name) and _used_as_a_store_root(node)
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "joinpath"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "tmp_path"
        and node.args
    ):
        first = node.args[0]
        if isinstance(first, ast.Constant) and first.value in _ROOT_NAMES:
            return True
        return isinstance(first, ast.Name) and _used_as_a_store_root(node)
    return False


# Variables a store root is bound to in this suite, and the calls that consume one.
_ROOT_BINDINGS = {"root", "store", "store_root", "src", "source_store", "scoped"}
_ROOT_CONSUMERS = {"_build_store", "running", "build_server"}
_STORE_ROOT_PARENTS: dict[int, bool] = {}


def _index_store_root_uses(tree: ast.AST) -> None:
    """Record which `tmp_path / X` nodes are USED as a store root.

    Two shapes, both structural: bound to a root-ish name, or handed to a call that
    takes a store root. Anything else — a scratch file, a token path, a cache dir —
    is not this ratchet's business.
    """
    _STORE_ROOT_PARENTS.clear()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in _ROOT_BINDINGS:
                    _STORE_ROOT_PARENTS[id(node.value)] = True
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name) and node.target.id in _ROOT_BINDINGS:
                if node.value is not None:
                    _STORE_ROOT_PARENTS[id(node.value)] = True
        elif isinstance(node, ast.Call):
            name = None
            if isinstance(node.func, ast.Name):
                name = node.func.id
            elif isinstance(node.func, ast.Attribute):
                name = node.func.attr
            if name in _ROOT_CONSUMERS:
                for arg in node.args:
                    _STORE_ROOT_PARENTS[id(arg)] = True
                    # `_build_store(root / "store", …)` — the tmp_path expr is nested
                    for sub in ast.walk(arg):
                        _STORE_ROOT_PARENTS[id(sub)] = True


def _used_as_a_store_root(node: ast.AST) -> bool:
    return _STORE_ROOT_PARENTS.get(id(node), False)


def test_the_inline_disk_rooted_store_sites_do_not_GROW():
    path = TESTS / "test_subsystem_store_api.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    _index_store_root_uses(tree)
    actual = sum(1 for n in ast.walk(tree) if _is_disk_rooted_store_expr(n))
    assert actual <= _DISK_ROOTED_SITES, (
        f"{actual} inline disk-backed store roots, up from {_DISK_ROOTED_SITES}. "
        "Each writes through server.py:_replace_bytes and fsyncs inside the request, "
        "so a new one rejoins the contention-flake population. Use "
        "testlib.store_siting.store_root() instead."
    )
    # 🔴 NO SLACK, and no `or actual == 0` escape. The previous version tolerated a
    # drop of up to three and passed unconditionally at zero — so the count could
    # regrow 0 -> 18 with the constant still reading 18 and the ratchet never biting.
    assert actual == _DISK_ROOTED_SITES, (
        f"only {actual} inline sites left, was {_DISK_ROOTED_SITES}. 🔴 FIRST check "
        "WHICH happened: sites genuinely converted to store_root(), or "
        "_is_disk_rooted_store_expr narrowed so it counts fewer? The counter and "
        "the constant live in this file and this assertion cannot tell them apart. "
        "If sites were fixed, lower _DISK_ROOTED_SITES in the SAME commit. If the "
        "predicate narrowed, widen it back — lowering the constant would bank a "
        "coverage loss as if it were progress."
    )


# 🔴 THE SECOND RATCHET, AND IT CLOSES THE ONE HOLE THE FIRST ONE'S OWN LESSON NAMES.
# `store_siting._LARGEST_STORE_BYTES` is the peak a store reaches on tmpfs, and
# `_MIN_FREE_BYTES` must clear it. Round 4 asserted one constant against the other —
# two literals sixteen lines apart in one file, checked by a test whose NAME claims to
# know "the largest store this suite builds" while its body knows nothing about the
# suite. An audit measured the consequence: grow the concurrency fixture from
# `range(303)` to `range(1200)`, touch neither constant, and every guard stays green
# while a /dev/shm of 4500k dies `OSError: [Errno 28]` — the ENOSPC hazard this whole
# module exists to close, re-armed through the guard added to prevent it.
#
# So the requirement is DERIVED from the fixture that produces it.
_STORE_PAGE_BYTES = 4096
# Directories, and slack for a fixture that grows by a few files rather than a loop.
_FIXTURE_SLACK_PAGES = 16


def _seeded_entry_count(tree: ast.AST) -> int:
    """Entries a module writes OUTSIDE any loop — the fixture seed.

    🔴 DERIVED, because `_SEEDED_ENTRIES = 3` was a bare literal about another file's
    function, pinned by nothing — the exact defect this ratchet exists to prevent,
    reintroduced inside its own fix. Add 200 seeded entries and a hardcoded 3 is blind
    to all of them.
    """
    loop_writes: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.For, ast.While, ast.comprehension)):
            for sub in ast.walk(node) if not isinstance(node, ast.comprehension) else []:
                loop_writes.add(id(sub))
    seeded = 0
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in ("write_text", "write_bytes")
            and id(node) not in loop_writes
        ):
            seeded += 1
    return seeded


def _biggest_fixture_entry_count() -> int | None:
    """Entries in the largest store fixture across EVERY ledgered file, or None.

    🔴 SUM, NOT MAX, and every ledgered file, not one. Round 5 took the max of one
    loop in `test_cairn_cli.py`; an audit measured three shapes that under-report
    SILENTLY — two write loops in one test (reports 303, truth 603), nested loops
    (203 vs 1003), and a bigger non-`write_text` loop beside the existing one, which
    passed while the true need was 21 MB against a 4 MiB floor. Under-reporting is the
    dangerous direction: it re-arms ENOSPC through the guard added to prevent it.

    Returns None rather than 0 when nothing is found: a zero would make the
    requirement trivially satisfiable, which is the failure mode this exists to stop.
    """
    total = 0
    found_any = False
    for name in sorted(EXPECTED_SERVER_TESTS):
        path = TESTS / name
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            continue
        n = _entries_written_by_loops(tree)
        if n is not None:
            found_any = True
            total += n + _seeded_entry_count(tree)
    return total if found_any else None


def _entries_written_by_loops(tree: ast.AST) -> int | None:
    """Total entries written by `for … in range(<int>)` loops that write files."""
    total = 0
    found = False
    # 🔴 ONLY loops that WRITE ENTRIES. A bare max over every `range(N)` is wrong and
    # was measured wrong: it picked up an unrelated `range(60_000)` building a string,
    # demanding a 245 MB floor. The discriminator is a write call in the loop body.
    # `write_bytes` counts too — round 5 checked only `write_text`, and an audit showed
    # a `write_bytes` loop beside the existing one passes while needing 21 MB.
    for node in ast.walk(tree):
        if not isinstance(node, ast.For):
            continue
        it = node.iter
        if not (
            isinstance(it, ast.Call)
            and isinstance(it.func, ast.Name)
            and it.func.id == "range"
            and len(it.args) == 1
            and isinstance(it.args[0], ast.Constant)
            and isinstance(it.args[0].value, int)
        ):
            continue
        writes = any(
            isinstance(c, ast.Call)
            and isinstance(c.func, ast.Attribute)
            and c.func.attr in ("write_text", "write_bytes")
            for c in ast.walk(node)
        )
        if not writes:
            continue
        found = True
        total += it.args[0].value
    return total if found else None


def test_the_largest_store_constant_still_covers_the_biggest_fixture():
    entries = _biggest_fixture_entry_count()
    assert entries is not None, (
        "could not find the bulk `range(N)` loop in test_cairn_cli.py. That is a "
        "BROKEN SCAN, not a satisfied requirement — fix the scan rather than the "
        "constant, or the floor silently stops being checked against anything."
    )
    required = entries * _STORE_PAGE_BYTES + _FIXTURE_SLACK_PAGES * _STORE_PAGE_BYTES
    assert required <= store_siting._LARGEST_STORE_BYTES, (
        f"the biggest store fixture is now {entries} entries, needing "
        f"{required:,} page-allocated bytes, but _LARGEST_STORE_BYTES is "
        f"{store_siting._LARGEST_STORE_BYTES:,}. Raise it AND re-check "
        f"_MIN_FREE_BYTES ({store_siting._MIN_FREE_BYTES:,}), which must stay above "
        "it. tmpfs charges whole 4 KiB pages, so entry COUNT drives this, not text "
        "size — apparent bytes understate the cost ~17x."
    )


def test_the_fixture_scan_can_actually_SEE_a_range_loop():
    # Positive control: without it, a scan that matched nothing would return None...
    # which the test above does catch — but a scan that matched only a TINY range
    # elsewhere would silently under-report and pass. Pin that it finds a big one.
    entries = _biggest_fixture_entry_count()
    assert entries is not None and entries >= 100, (
        f"the fixture scan found {entries} entries — expected the ~300-entry "
        "concurrency fixture. The scan, not the constant, is what to fix."
    )


def test_the_string_literal_half_of_the_membership_scan_is_LOAD_BEARING(tmp_path: Path):
    """A store server stood up inside a `sys.executable -c` script.

    The AST scan cannot see a call inside a string, and seven test files here
    already drive that shape. Before this test existed, disabling the Constant
    half left this file's suite fully green — the widening reverted unnoticed.
    (No passed-count here on purpose: the previous one was falsified by the very
    commit that moved this test, which is how it earned its own finding.)
    """
    probe = tmp_path / "test_probe_subprocess_server.py"
    probe.write_text(
        "import textwrap\n"
        'SCRIPT = textwrap.dedent("""\n'
        "    build_server(store_root=arg)\n"
        '""")\n'
    )
    assert _calls_build_server(probe), (
        "a build_server( call inside a string literal must count as standing up "
        "the server — otherwise a subprocess-driven test is silently dropped from "
        "the ledger, which is worse than the comment false positive this replaced"
    )

def test_a_hash_COMMENT_mentioning_build_server_is_still_not_a_call(tmp_path: Path):
    """The mirror. Widening for strings must not undo the false-positive fix."""
    probe = tmp_path / "test_probe_comment_only.py"
    probe.write_text(
        "# this file deliberately never calls build_server(...) itself\n"
        "def test_x():\n    assert True\n"
    )
    assert not _calls_build_server(probe)
