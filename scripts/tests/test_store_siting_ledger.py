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

import pytest
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
    `test_subsystem_store_api.py` still builds store roots inline from `tmp_path` at
    the sites `_DISK_ROOTED_SITES` counts below — 20 of them spelled `tmp_path /
    "store"` and 13 not — each in one or two tests rather than a shared fixture. That
    count is asserted so it can only go DOWN — see the next test.
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
# 🔴 33, UP FROM 20, AND NOT ONE SITE WAS ADDED. The predicate below stopped being
# spelled, and 13 inline disk-backed store roots that had always been there became
# visible. Every one of them was a REAL store served by `running(...)`. The
# breakdown, RE-MEASURED 2026-09-02 and summing to 13 — an earlier revision of this
# comment said "five" `served` and summed to 15, which is the sort of arithmetic
# nobody re-does:
#
#     served              3    a `cp -a` of the store fixture, written into, served
#     served-elsewhere    3
#     stage               2    the output of `run_seed`, served back
#     absent              2    `running(tmp_path / "absent")`
#     big                 1
#     at-the-cap          1
#     unambiguous         1
#                        --
#                        13   + the 20 `store`/`name`/`kind` sites = 33
#
# The `absent` pair is the only debatable entry: an absent store writes nothing and
# fsyncs nothing, so counting it errs WIDE — the safe direction for a ratchet, and not
# worth a second spelled exception to avoid.
#
# 🔴 SO THIS NUMBER MOVING UP IS NOT ALWAYS DEBT ARRIVING. The assertion below cannot
# tell "13 new store sites were written" from "the predicate got 13 sites wider", and
# they demand opposite actions. Establish WHICH before touching the constant, the same
# way the DOWN direction already demands.
_DISK_ROOTED_SITES = 33

# Directory names that make a `tmp_path / "<name>"` a store root ON SIGHT, with no
# need for it to flow anywhere. MEASURED 2026-09-02: of the 33 counted sites, 26 flow
# into a store consumer and would be caught without this set; 7 are counted ONLY by
# this set, so deleting it would lose those seven.
#
# 🔴 BUT DO NOT READ THOSE 7 AS "SITES THAT GENUINELY DO NOT FLOW ANYWHERE" — an
# earlier revision of this comment said exactly that and it is false of all seven. They
# are every `tmp_path / "store"` at :11310, :11765, :13099, :14086, :14201, :14227 and
# :14803 of `test_subsystem_store_api.py`, and they split two ways, each one a MASKED
# GAP in the flow arm rather than a shortcut:
#   * :11310 and :13099 are `root = tmp_path / "store"` in a `_phases` helper whose
#     NESTED `present()`/`absent()` closures call `_build_store(root, …)` — a consumer
#     `_ROOT_CONSUMERS` names. They are invisible only because `_walk_scope` stops at a
#     nested function boundary, so no single scope sees both the binding and the call.
#   * the other five reach `api.append_bullet` / `api.rc.load_index` — real store
#     consumers that `_ROOT_CONSUMERS` simply does not name (see its own comment: the
#     set closes renames, not GROWTH into a new consumer name).
# So `_ROOT_NAMES` is currently propping up the flow arm on this file. Deleting it
# would not "lose seven non-flowing sites", it would expose two structural gaps.
_ROOT_NAMES = {"store", "src"}


def _is_disk_rooted_store_expr(node: ast.AST) -> bool:
    """`tmp_path / "store"` and its spellings — a store root that skips the siting.

    🔴 A BARE `tmp_path / <Name>` IS NOT ENOUGH, and this was measured on the merged
    tree rather than argued. Round 3's audit called the unrestricted Name arm "a false
    accusation"; `main` then added
    `(tmp_path / name).write_text(body)` — a scratch file called "wrapped.md" — and the
    ratchet counted 21, demanding an author use `store_root()` for something that is
    not a store. So a Name only counts when the expression is USED as a store root:
    handed to `_build_store` / `running` / `build_server`, directly or through the
    variable it was bound to. See `_index_store_root_uses`.

    🔴 AND NEITHER IS A CONSTANT IN A SET OF TWO WORDS. Round 7's audit found the
    binding-NAME half of this predicate spelled and walked through it with a rename;
    the DIRECTORY-name half was the same defect, unreported and larger. `served =
    tmp_path / "served"` is a `cp -a` copy of the store fixture, written into and then
    handed to `running(...)` — a disk-backed store server by every property that
    matters, invisible because the directory is not called "store". Measured: 13 such
    sites, on the same file, at the same moment the ratchet read a confident 20. So the
    Constant arm now ALSO counts a path that flows into a store consumer, and
    `_ROOT_NAMES` is the residual on-sight shortcut rather than the whole test.

    Quoting and spacing still do not matter: they do not survive parsing.
    """
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div) and (
        isinstance(node.left, ast.Name) and node.left.id == "tmp_path"
    ):
        right = node.right
        if isinstance(right, ast.Constant) and right.value in _ROOT_NAMES:
            return True
        return isinstance(right, (ast.Name, ast.Constant)) and _used_as_a_store_root(
            node
        )
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
        return isinstance(first, (ast.Name, ast.Constant)) and _used_as_a_store_root(
            node
        )
    return False


# The calls that CONSUME a store root.
#
# 🔴 THIS SET CLOSES RENAMES AND NOT GROWTH, AND ONLY THE FIRST HALF USED TO BE SAID.
# The rename argument holds: a callee name is a real referent — the function being
# invoked — so renaming `_build_store` renames its definition too and the set follows.
# (The set it replaced, `_ROOT_BINDINGS`, keyed on the name of the VARIABLE a root was
# bound to, which no rename follows. That set is deleted; `test_the_site_index_keys_on_
# USE_not_on_the_variables_NAME` is the control that killed it.)
#
# But a ratchet's job is to notice GROWTH, and growth arrives as a NEW consumer name,
# which no rename argument covers. Measured with this file's own control probe:
# `running(served)` counts 1 and `serve_store(served)` — same expression, same copy,
# same writes — counts 0. So only half the spelled-guard class is closed here, and
# `_ROOT_NAMES` above records what that already costs on the real file today.
_ROOT_CONSUMERS = {"_build_store", "running", "build_server"}
# Wrappers a path is handed through on its way into a consumer. `str(root)` is the
# live shape: `build_server(..., store_root=str(store))`. Bare names only: `_unwrap`
# matches an `ast.Attribute` on its `.attr`, so `os.fspath(x)` is matched by the
# `"fspath"` entry and a dotted `"os.fspath"` entry could never match anything.
_PATH_WRAPPERS = {"str", "fspath", "Path"}
_STORE_ROOT_PARENTS: dict[int, bool] = {}


def _unwrap(expr: ast.AST) -> ast.AST:
    """Peel `str(...)` / `os.fspath(...)` / `Path(...)` off a path expression."""
    while isinstance(expr, ast.Call) and expr.args:
        name = None
        if isinstance(expr.func, ast.Name):
            name = expr.func.id
        elif isinstance(expr.func, ast.Attribute):
            name = expr.func.attr
        if name not in _PATH_WRAPPERS:
            break
        expr = expr.args[0]
    return expr


def _dotted(expr: ast.AST) -> str | None:
    """`a`, `self.root`, `cls.x.y` -> the dotted name. Anything else -> None."""
    parts: list[str] = []
    cur = expr
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if not isinstance(cur, ast.Name):
        return None
    parts.append(cur.id)
    return ".".join(reversed(parts))


def _path_base(expr: ast.AST) -> str | None:
    """The dotted name a path expression is ROOTED at.

    `root / "store" / f"{x}.md"` -> `root`; `str(self.src / "a")` -> `self.src`.
    This is what makes the index structural: it asks what a value IS BUILT FROM,
    never what the variable holding it is called.
    """
    cur = _unwrap(expr)
    while True:
        if isinstance(cur, ast.BinOp) and isinstance(cur.op, ast.Div):
            cur = _unwrap(cur.left)
        elif isinstance(cur, ast.Call) and isinstance(cur.func, ast.Attribute):
            cur = _unwrap(cur.func.value)
        else:
            break
    return _dotted(cur)


_FUNCTION_NODES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)


def _scopes(tree: ast.AST) -> list[ast.AST]:
    """Every function body, plus the module itself, as separate name scopes.

    Per-scope rather than file-wide: a `root` that flows into `_build_store` in one
    test must not silently vouch for an unrelated `root` in another.
    """
    out: list[ast.AST] = [tree]
    for node in ast.walk(tree):
        if isinstance(node, _FUNCTION_NODES):
            out.append(node)
    return out


def _walk_scope(scope: ast.AST):
    """`ast.walk`, but STOPPING at a nested function boundary.

    🔴 A plain `ast.walk(module)` descends into every function body, so treating the
    module as "a scope" would silently make the whole index file-wide and the
    per-scope claim above a lie. The names inside a function are that function's.
    """
    stack = list(ast.iter_child_nodes(scope))
    while stack:
        node = stack.pop()
        yield node
        if not isinstance(node, _FUNCTION_NODES):
            stack.extend(ast.iter_child_nodes(node))


def _assignments(scope: ast.AST):
    """`(dotted-target, value)` for every binding form that can hold a store root.

    The shapes past a plain `x = …` that THIS function handles are: an annotated
    assignment (`ast.AnnAssign`), a walrus (`ast.NamedExpr`), a `with … as` target
    (`ast.withitem`), and a tuple/list target on any of them.

    🔴 SAY WHICH ONES ARE HERE. A previous revision of this docstring listed four
    shapes as "here" and two of them were not: a helper that RETURNS the root is
    resolved in `_index_store_root_uses` via `flowing_callees`, not by any binding
    form; and it omitted `ast.withitem`, which the body below does handle. The walrus
    IS here, but it is an INVARIANT guard rather than regression coverage — it already
    counted before this function existed, because a consumer's argument is walked
    recursively, so the nested `tmp_path / name` was reached without the binding ever
    being resolved. `test_the_site_index_sees_the_BINDING_FORMS_a_plain_assignment_is_
    not`'s docstring says the same thing, and the two used to disagree.
    """
    for node in _walk_scope(scope):
        pairs: list[tuple[ast.AST, ast.AST]] = []
        if isinstance(node, ast.Assign):
            pairs = [(t, node.value) for t in node.targets]
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            pairs = [(node.target, node.value)]
        elif isinstance(node, ast.NamedExpr):
            pairs = [(node.target, node.value)]
        elif isinstance(node, ast.withitem) and node.optional_vars is not None:
            pairs = [(node.optional_vars, node.context_expr)]
        for target, value in pairs:
            if isinstance(target, (ast.Tuple, ast.List)):
                # `a, b = tmp_path / "store", other` binds elementwise; anything
                # else (a starred unpack, a call) is credited to every element,
                # which errs WIDE — the safe direction for a ratchet.
                elts = value.elts if isinstance(value, (ast.Tuple, ast.List)) else None
                for i, sub in enumerate(target.elts):
                    name = _dotted(sub)
                    if name:
                        yield name, (elts[i] if elts and i < len(elts) else value)
                continue
            name = _dotted(target)
            if name:
                yield name, value


def _index_store_root_uses(tree: ast.AST) -> None:
    """Record which `tmp_path / X` nodes are USED as a store root.

    🔴 THIS USED TO BE A SPELLED GUARD AND IT WAS WALKED THROUGH WITH A PAIRED
    CONTROL. The old index counted a `tmp_path / <Name>` only when the variable it
    was bound to was in `_ROOT_BINDINGS = {"root", "store", "store_root", "src",
    "source_store", "scoped"}`. An audit copied a COUNTED site verbatim and renamed
    the variable to `base_dir`: identical runtime behaviour, identical fsync
    exposure, and the ratchet stayed at 20 with the suite green. Rename it back to
    `root` and nothing else, and the same code went red. A guard on a WORD is
    walkable by anything that can spell a different word.

    So the index now asks what the value FLOWS INTO, never what it is called:

      * an expression handed to a store consumer — positionally OR BY KEYWORD.
        🔴 The keyword half was missing entirely; `node.keywords` appeared nowhere
        in this file, and `build_server(..., store_root=str(store))` is the live
        spelling at nine call sites. Same expression, same behaviour, invisible.
      * a name that some store-flowing expression is ROOTED at, so
        `root = tmp_path / name` counts because `_build_store(root / "store", …)`
        exists in the same scope — with no opinion about the word "root".
      * a helper whose RETURN value is that expression, when the helper itself is
        called in a store-flowing position.

    Scoped per function, so one test's `root` cannot vouch for another's.
    """
    _STORE_ROOT_PARENTS.clear()

    # Which locally-defined functions are called in a store-flowing position?
    flowing_callees: set[str] = set()

    def mark(expr: ast.AST) -> None:
        _STORE_ROOT_PARENTS[id(expr)] = True
        for sub in ast.walk(expr):
            # `_build_store(root / "store", …)` — the tmp_path expr may be nested.
            _STORE_ROOT_PARENTS[id(sub)] = True

    for scope in _scopes(tree):
        flowing_roots: set[str] = set()
        for node in _walk_scope(scope):
            if not isinstance(node, ast.Call):
                continue
            name = None
            if isinstance(node.func, ast.Name):
                name = node.func.id
            elif isinstance(node.func, ast.Attribute):
                name = node.func.attr
            if name not in _ROOT_CONSUMERS:
                continue
            for arg in list(node.args) + [kw.value for kw in node.keywords]:
                mark(arg)
                base = _path_base(arg)
                if base:
                    flowing_roots.add(base)
                inner = _unwrap(arg)
                callee = None
                if isinstance(inner, ast.Call):
                    if isinstance(inner.func, ast.Name):
                        callee = inner.func.id
                    elif isinstance(inner.func, ast.Attribute):
                        callee = inner.func.attr
                if callee:
                    flowing_callees.add(callee)
        # To a FIXPOINT, because a store root reaches a consumer through a chain:
        # `holder = tmp_path / name` -> `root = holder / "store"` -> `running(root)`.
        # One pass sees only the last hop, so the `tmp_path` expression that started
        # the chain stays uncounted — a silent under-report in a ratchet whose whole
        # job is to notice growth. The set only grows, so this terminates.
        bindings = list(_assignments(scope))
        while True:
            grew = False
            for target, value in bindings:
                if target not in flowing_roots:
                    continue
                mark(value)
                base = _path_base(value)
                if base and base not in flowing_roots:
                    flowing_roots.add(base)
                    grew = True
            if not grew:
                break

    if flowing_callees:
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if node.name not in flowing_callees:
                continue
            for sub in ast.walk(node):
                if isinstance(sub, ast.Return) and sub.value is not None:
                    mark(sub.value)
                elif isinstance(sub, (ast.Yield, ast.YieldFrom)) and sub.value:
                    mark(sub.value)


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
        "testlib.store_siting.store_root() instead. 🔴 BUT FIRST check WHICH happened, "
        "because this direction is ambiguous too: did someone WRITE new inline store "
        "sites, or did _is_disk_rooted_store_expr get WIDER and start seeing sites "
        "that were always there? Round 7 was the second — 20 -> 33 with no site added, "
        "because the predicate stopped keying on the directory being spelled 'store'. "
        "Raising the constant is right for a widening and wrong for new debt."
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


def _count_disk_rooted(source: str) -> int:
    """The ratchet's own counter, run over a probe module."""
    tree = ast.parse(source)
    _index_store_root_uses(tree)
    return sum(1 for n in ast.walk(tree) if _is_disk_rooted_store_expr(n))


# A counted site, verbatim in shape, with the binding NAME as the only variable.
# `tmp_path / kind` is the live shape at two sites in `test_subsystem_store_api.py`.
_RENAME_PROBE = """
def test_probe(tmp_path, kind):
    {var} = tmp_path / kind
    store = _build_store({var} / "store", {{}})
    running(store)
"""


def test_the_site_index_keys_on_USE_not_on_the_variables_NAME():
    """🔴 THE PAIRED CONTROL THAT KILLED THE PREVIOUS INDEX.

    Two modules that differ in exactly one character sequence — the name of a local
    variable — and in nothing else. Same expression, same call, same fsync exposure,
    same behaviour at runtime. The old index consulted `_ROOT_BINDINGS = {"root",
    "store", "store_root", "src", "source_store", "scoped"}`, so `root` counted and
    `base_dir` did not: a new disk-backed store site could be added invisibly by
    choosing a word the set had not thought of.

    Reading a rename as a coverage change is the definition of a spelled guard.
    """
    named_root = _count_disk_rooted(_RENAME_PROBE.format(var="root"))
    renamed = _count_disk_rooted(_RENAME_PROBE.format(var="base_dir"))
    assert named_root == 1, (
        f"the probe's own shape is not counted at all ({named_root}) — this control "
        "cannot detect a rename if it detects nothing"
    )
    assert renamed == named_root, (
        f"renaming the variable moved the count {named_root} -> {renamed}. The index "
        "is keying on the WORD again, not on what the value flows into."
    )


def test_the_site_index_does_not_key_on_the_DIRECTORY_being_spelled_store():
    """The Constant-arm mirror of the rename control above, and it found more.

    `served = tmp_path / "served"` is a `cp -a` of the store fixture, written into and
    then handed to `running(...)`. It is a disk-backed store server by every property
    that matters and differs from a counted site in one thing: the word in the path.
    Thirteen such sites existed on `test_subsystem_store_api.py` while the ratchet
    read a confident 20.

    The `served` half of this pair is REGRESSION coverage — measured 0 before, 1 now.
    """
    template = (
        "def test_probe(tmp_path):\n"
        "    served = tmp_path / '{name}'\n"
        "    (served / 'scope' / 'e.md').write_text('x')\n"
        "    running(served)\n"
    )
    spelled_store = _count_disk_rooted(template.format(name="store"))
    spelled_served = _count_disk_rooted(template.format(name="served"))
    assert spelled_store == 1, spelled_store
    assert spelled_served == spelled_store, (
        f"a store directory called 'store' counts {spelled_store}, the identical one "
        f"called 'served' counts {spelled_served}. Same copy, same writes, same "
        "`running()`, same fsyncs — only the word differs."
    )


def test_a_path_that_is_never_used_as_a_store_still_does_NOT_count():
    """The other side of that widening, and it is the one that keeps it honest.

    Widening the Constant arm to "any word" would re-create the false accusation round
    3 measured: `(tmp_path / name).write_text(body)` for a scratch file, counted as a
    store an author must convert. Flowing into a store consumer is the whole
    discriminator, so a cache directory and a scratch file must still be invisible.
    """
    probe = (
        "def test_probe(tmp_path):\n"
        "    cache = tmp_path / 'cache'\n"
        "    (tmp_path / 'wrapped.md').write_text('body')\n"
        "    _extract(body, tmp_path / 'copy')\n"
        "    cache.mkdir()\n"
    )
    assert _count_disk_rooted(probe) == 0, (
        f"counted {_count_disk_rooted(probe)} store roots in a module with none. A "
        "cache dir, a scratch file and a non-consumer call are not stores, and "
        "demanding store_root() for them is the false accusation round 3 measured."
    )


def test_a_store_root_passed_BY_KEYWORD_counts_like_a_positional_one():
    """🔴 `node.keywords` APPEARED NOWHERE IN THIS FILE.

    `_index_store_root_uses` walked `node.args` only, so `build_server(store_root=X)`
    — the spelling used at nine live call sites in `test_subsystem_store_api.py` —
    was invisible while the identical expression passed positionally was counted.
    Python does not distinguish them; neither may this.
    """
    positional = _count_disk_rooted(
        "def test_probe(tmp_path, name):\n    build_server(tmp_path / name)\n"
    )
    keyword = _count_disk_rooted(
        "def test_probe(tmp_path, name):\n"
        "    build_server(host='127.0.0.1', store_root=str(tmp_path / name))\n"
    )
    assert positional == 1, positional
    assert keyword == positional, (
        f"positional counted {positional}, keyword counted {keyword}. Same value, "
        "same consumer, same disk-backed store — only the calling convention differs."
    )


def test_the_site_index_sees_the_BINDING_FORMS_a_plain_assignment_is_not():
    """Tuple targets, attribute targets, the walrus, and a helper that RETURNS the
    root. Each was reproduced as a way to bind a store root that the old index did
    not look at — so each is a way to add a site the ratchet would not count.

    Named individually rather than asserted as a total, because a single number here
    would let three of the four regress unnoticed behind one that still works.

    ⚠ THREE OF THESE FOUR ARE REGRESSION COVERAGE; THE WALRUS IS AN INVARIANT GUARD.
    Measured against the previous index: tuple target 0, attribute target 0, returned
    from a helper 0 — all three uncounted, all three now 1. The walrus case already
    counted, for an unrelated reason (the consumer's argument is walked recursively,
    so the nested `tmp_path / name` was reached without the binding ever being
    resolved). It is kept because it pins that behaviour, not because it caught
    anything, and it must not be tallied as a fourth fix.
    """
    shapes = {
        "tuple target": (
            "def test_probe(tmp_path, name):\n"
            "    root, other = tmp_path / name, 1\n"
            "    running(root / 'store')\n"
        ),
        "attribute target": (
            "class T:\n"
            "    def test_probe(self, tmp_path, name):\n"
            "        self.holder = tmp_path / name\n"
            "        running(self.holder / 'store')\n"
        ),
        "walrus": (
            "def test_probe(tmp_path, name):\n"
            "    running((root := tmp_path / name) / 'store')\n"
        ),
        "two-hop chain": (
            "def test_probe(tmp_path, name):\n"
            "    holder = tmp_path / name\n"
            "    root = holder / 'store'\n"
            "    running(root)\n"
        ),
        "returned from a helper": (
            "def _make(tmp_path, name):\n"
            "    return tmp_path / name\n"
            "def test_probe(tmp_path, name):\n"
            "    running(_make(tmp_path, name))\n"
        ),
    }
    missed = {k: _count_disk_rooted(v) for k, v in shapes.items()}
    missed = {k: n for k, n in missed.items() if n != 1}
    assert not missed, (
        f"these binding forms hold a store root the ratchet cannot see: {missed}. "
        "Each is a way to add an inline disk-backed store while the count stays flat."
    )


def test_one_tests_store_root_does_not_vouch_for_ANOTHERS_scratch_directory():
    """The index is scoped per function, and this is what makes that a fact.

    Both functions below bind a name to `tmp_path / name`. In the first it becomes a
    store; in the second it is a scratch directory that merely happens to reuse the
    word. A file-wide index counts BOTH — which is how a ratchet starts accusing
    authors of not using `store_root()` for things that are not stores. Round 3's
    audit called exactly that "a false accusation", and `main` then hit it.

    ⚠ THE SCOPING IS NOT FREE, AND THIS DOCSTRING USED TO SAY IT WAS. It read "the
    count over the real file is 20 either way today" — wrong in both halves. MEASURED
    2026-09-02 on `test_subsystem_store_api.py`: **per-scope 33, file-wide 39**. The
    scoping suppresses SIX sites, and they are not one kind:

      * four `tmp_path / "stage"` (:2658, :3619, :3647, :3750) are stage directories
        handed to `seed.sh` as a `--stage` SUBPROCESS argument and never served
        in-process. File-wide indexing would count them only by colliding with the
        genuinely-served `stage` at :5710/:5726 — precisely the false accusation this
        scoping exists to prevent. Here the scoping is RIGHT.
      * two — `ordered-served` (:3945) and `ambig-served` (:4294) — ARE real
        disk-backed store roots, `cp -a`'d and served in-process. File-wide would
        count them for the right reason by accident. They are missed for a different
        reason entirely, recorded and NOT closed: see
        `test_a_store_root_bound_in_a_pytest_FIXTURE_is_NOT_counted` below.

    So file-wide indexing is not "the same answer, more cheaply" — it is four false
    accusations bought with two accidental catches, which is why it stays rejected.
    """
    probe = (
        "def test_a(tmp_path, name):\n"
        "    root = tmp_path / name\n"
        "    running(root / 'store')\n"
        "\n"
        "def test_b(tmp_path, name):\n"
        "    root = tmp_path / name\n"
        "    root.mkdir()\n"
    )
    assert _count_disk_rooted(probe) == 1, (
        f"counted {_count_disk_rooted(probe)} store roots in a module with one store "
        "and one scratch directory. The index is resolving names across function "
        "boundaries, so an unrelated local called `root` is being counted as a store."
    )


def test_a_store_root_bound_in_a_pytest_FIXTURE_is_NOT_counted():
    """🔴 A KNOWN, MEASURED RESIDUAL — THIS IS A GAP GUARD, NOT COVERAGE.

    Read the name literally: it asserts the ratchet CANNOT see a store root bound
    inside a `@pytest.fixture` and served in a test that requests it. That is a hole,
    it is recorded here so it cannot be rediscovered as news, and it is pinned so that
    CLOSING it fails this test and forces whoever closes it to delete this guard and
    move `_DISK_ROOTED_SITES` in the same commit.

    Fixture-binding is this file's dominant idiom, so the hole is not exotic. TWO LIVE
    INSTANCES on `test_subsystem_store_api.py`, both real `cp -a` copies of a store,
    written into, and served in-process by the real store server:

      * `served = tmp_path / "ordered-served"` in the `shuffled_pair` fixture (:3945),
        served at :4392 and four more `running(served)` sites;
      * `served = tmp_path / "ambig-served"` in the `ambiguous_pair` fixture (:4294),
        served at :4758 and one more.

    🔴 WHY IT IS NOT CLOSED, stated rather than implied. The flow arm is scoped per
    function (`_walk_scope` stops at a function boundary), and a pytest fixture crosses
    exactly that boundary through pytest's own name-injection, which is not a Python
    binding the AST can see. Following it would mean a NEW cross-scope resolver:
    fixture def -> fixture name -> parameter of a consuming scope -> and, for both live
    instances, the ELEMENT POSITION in a returned tuple that the consumer destructures
    (`local, served = shuffled_pair`). File-wide indexing is not the shortcut: it was
    measured (see the scoping test above) to buy these two catches at the cost of four
    false accusations, and round 3 rejected it for exactly that.

    So the honest statement, which replaces the one this PR shipped: the predicate does
    NOT cover the flow case generally. It covers the flow case WITHIN ONE FUNCTION
    SCOPE.
    """
    inline = (
        "def test_probe(tmp_path):\n"
        "    served = tmp_path / 'served'\n"
        "    (served / 'scope' / 'e.md').write_text('x')\n"
        "    running(served)\n"
    )
    via_fixture = (
        "import pytest\n"
        "@pytest.fixture\n"
        "def pair(tmp_path):\n"
        "    served = tmp_path / 'served'\n"
        "    (served / 'scope' / 'e.md').write_text('x')\n"
        "    return served\n"
        "def test_probe(pair):\n"
        "    running(pair)\n"
    )
    # The live shape: the fixture returns a TUPLE and the test destructures it.
    via_fixture_tuple = (
        "import pytest\n"
        "@pytest.fixture\n"
        "def pair(tmp_path):\n"
        "    local = tmp_path / 'local'\n"
        "    served = tmp_path / 'served'\n"
        "    (served / 'scope' / 'e.md').write_text('x')\n"
        "    return local, served\n"
        "def test_probe(pair):\n"
        "    local, served = pair\n"
        "    running(served)\n"
    )
    # The positive control. Without it a probe that counts nothing for an unrelated
    # reason (a typo in the template, a consumer name that is not in the set) would
    # make the two zeros below look like the documented residual when they are not.
    assert _count_disk_rooted(inline) == 1, (
        f"the inline form of this probe counts {_count_disk_rooted(inline)}, not 1 — "
        "this control cannot demonstrate a residual if the shape is uncountable for "
        "some other reason entirely"
    )
    assert _count_disk_rooted(via_fixture) == 0, (
        "the ratchet now SEES a store root bound in a fixture. That is good news and "
        "it makes this guard wrong: delete it, re-measure _DISK_ROOTED_SITES (the two "
        "live instances at test_subsystem_store_api.py:3945 and :4294 will start "
        "counting), and rewrite the residual in "
        "test_one_tests_store_root_does_not_vouch_for_ANOTHERS_scratch_directory."
    )
    assert _count_disk_rooted(via_fixture_tuple) == 0, (
        "the tuple-returning fixture form — the shape both live instances actually "
        "use — is now counted. Same action as above."
    )


# 🔴 THE SECOND GUARD — AND IT NO LONGER READS THE SOURCE AT ALL.
#
# `store_siting._LARGEST_STORE_BYTES` is the budget a store must stay under, and
# `_MIN_FREE_BYTES` is sized against it. Rounds 4-6 tried to keep that budget honest by
# DERIVING it from a syntactic sweep of the ledgered test files — counting `write_text`
# calls and `range(N)` loops — because a constant checked only against another constant
# is not a guard. The reasoning was right; the instrument was not. Every revision of the
# sweep was walked through by a shape its author had not imagined, and each one
# under-reported SILENTLY with the whole suite green:
#
#   * nested loops SUMMED instead of multiplied: `for a in range(4): for b in
#     range(300)` writes 1,200 files and reported 4 + 300 = 304, a 2.7x under-report of
#     exactly the growth scenario this exists to catch;
#   * `range(303)` respelled `range(0, 303)` — the scan required exactly one argument,
#     so the file's whole contribution silently became zero;
#   * the same loop rewritten as a list comprehension, likewise zero;
#   * the loop body extracted into a helper, likewise zero;
#   * `ast.AsyncFor` never in the isinstance tuple at all;
#   * and the census that fed it counted EVERY `write_text` in the file, so appending
#     one five-byte scratch write to a test with no store, no server and no tmpfs moved
#     the derived requirement. It moved six times in nineteen commits.
#
# The positive control could not see any of it either: it asserted the total was >= 100,
# and summing three files floated it over 100 without the ~300-entry fixture being
# visible at all.
#
# 🔴 SO THE ENFORCEMENT MOVED OUT OF THE SCAN AND INTO THE THING ITSELF.
# `store_siting._check_store_budget` WALKS THE REAL DIRECTORY at teardown of every
# `store_root(...)` and raises when it exceeds the budget. There is no spelling of a
# write loop that produces files a directory walk cannot see, so the entire class of
# defect above is gone rather than patched. What is left to test here is the
# INSTRUMENT: that the check runs, that it measures pages rather than apparent bytes,
# that it can go red, that it does so on both siting branches, and that it does not
# eat the failure of the test it is attached to.
#
# ⚠ WHAT IS AND IS NOT AUTOMATED HERE — an earlier revision of this comment blurred
# the two. A ONE-OFF, HAND-INSTRUMENTED run over the three ledgered files on 2026-09-02
# saw the check called on 448 stores, the largest 1,253,376 B / 306 entries
# (`test_cairn_cli.py`'s concurrency fixture) and the next 176,128 B / 43 entries. That
# was a manual measurement; the instrumentation it used has been removed, NOTHING
# re-runs it, and it must not be quoted as a standing positive control. The standing
# controls are the two tests below, both of which run every time and were
# mutation-verified: `test_store_root_INVOKES_the_budget_check_on_the_root_it_yielded`
# (the check is reached at all) and `test_a_store_over_the_budget_RAISES_with_the_
# budget_checks_own_message` (it can go red, on its own wording).
#
# The residual, stated rather than hidden: this is a claim about stores that are
# actually BUILT by a run. A fixture nobody executes is not measured. The gate runs the
# whole suite, and `test_every_ledgered_file_IMPORTS_AND_CALLS_the_shared_siting_at_
# least_once` above is what keeps the ledgered files routed through the seam, so the
# two together cover the population. A deselected subset run is not that claim. Nor is
# it a claim about the 33 inline sites the FIRST ratchet counts (20 of them spelled
# `tmp_path / "store"`, 13 not): those never reach `store_root`, live on disk rather
# than tmpfs, and are that ratchet's business, not this budget's.

# How much bigger than the measured peak the budget must be. 🔴 THE PREVIOUS BUDGET HAD
# ZERO SLACK — 1,875,968 was exactly (442 + 16) * 4096 where 442 was the sweep's own
# output, so the required value and the constant were literally the same number and any
# nudge in either direction turned a required merge check red.
_MIN_BUDGET_HEADROOM = 1.25

# How much bigger than the budget the free-space floor must be. This is the claim
# `_MIN_FREE_BYTES`'s comment makes in prose; pinning it here is what stops the prose
# rotting again. 🔴 It read "better than 3x margin" while the true ratio was 2.24x —
# falsified by round 6's own change to the other constant, in the same commit, unnoticed.
_MIN_FLOOR_MARGIN = 2.0

# Entries needed to break the budget, DERIVED from the budget rather than transcribed:
# one page per file, one file past the limit. Deriving it is the point — a literal here
# would go stale the moment the budget moved, and would then be testing nothing.
_OVER_BUDGET_ENTRIES = store_siting._LARGEST_STORE_BYTES // store_siting._PAGE_BYTES + 1


def _fill(root: Path, entries: int) -> None:
    """`entries` one-page files under `root`, in the `scope/name.md` shape."""
    scope = root / "a-scope"
    scope.mkdir(parents=True, exist_ok=True)
    for i in range(entries):
        (scope / f"e-{i:04d}.md").write_text(f"entry {i}\n")


def test_store_root_INVOKES_the_budget_check_on_the_root_it_yielded(tmp_path: Path):
    """Reachability, and it is a different claim from "the check is correct".

    A budget checker that is never called reports nothing for every store in the
    suite, which is indistinguishable from a suite that builds no stores. This spies
    on the call rather than the arithmetic, so it stays green under any correct
    implementation and red the moment `store_root` stops consulting it.
    """
    seen: list[Path] = []
    real = store_siting._check_store_budget
    store_siting._check_store_budget = lambda root: seen.append(root)
    try:
        with store_siting.store_root(tmp_path) as root:
            _fill(root, 2)
            yielded = root
    finally:
        store_siting._check_store_budget = real
    assert seen == [yielded], (
        f"store_root yielded {yielded} but handed the budget check {seen}. Every "
        "store the ledgered files build reaches tmpfs through this one call, so a "
        "check it does not invoke is a check on nothing."
    )


def test_page_allocated_bytes_counts_PAGES_not_apparent_size(tmp_path: Path):
    """🔴 APPARENT BYTES UNDERSTATE TMPFS COST ~17x, and that is the whole reason
    this measurement exists rather than a `sum(st_size)`.

    The fixture is built so the four plausible wrong answers are all DISTINCT from the
    right one, because a fixture that cannot tell them apart survives a mutation that
    hardcodes any of them. Each line names the mutant it separates, and each of those
    mutants was RUN and killed here:

      * 7 files of ~9 bytes    -> 7 pages   summing st_size instead reads 5,063 bytes
      * 1 empty file           -> 1 page    dropping `max(1, …)` reads 9 pages
      * 1 file of 5,000 bytes  -> 2 pages   `entries * page` reads 9 pages, not 10
      * 2 directories          -> 0 pages   counting them reads 11 entries and 12 pages

    So the right answer is 9 entries / 10 pages / 40,960 bytes, and 9, 10, 11, 12,
    4,096, 5,063 and 40,960 are pairwise distinct — no fixture value can coincide with
    a constant the implementation names.
    """
    root = tmp_path / "measured"
    (root / "one").mkdir(parents=True)
    (root / "two").mkdir(parents=True)
    for i in range(7):
        (root / "one" / f"s-{i}.md").write_text("12345678\n")
    (root / "one" / "empty.md").write_text("")
    (root / "two" / "big.md").write_text("z" * 5000)

    entries, allocated = store_siting.page_allocated_bytes(root)
    apparent = sum(p.stat().st_size for p in root.rglob("*") if p.is_file())

    assert entries == 9, entries
    assert allocated == 10 * store_siting._PAGE_BYTES, (
        f"{allocated:,} bytes for 9 files spanning 10 pages — expected "
        f"{10 * store_siting._PAGE_BYTES:,}"
    )
    # The control that makes the claim mean something: the two numbers must DIFFER,
    # by a lot. If they ever agree, the fixture stopped being able to see the bug.
    # Measured ratio here is 8.09x (40,960 / 5,063); the bound is 7x deliberately, so
    # this control is not itself sitting on its own boundary.
    assert apparent * 7 < allocated, (
        f"apparent {apparent:,} vs page-allocated {allocated:,} — this fixture no "
        "longer distinguishes a page count from a byte sum, so it cannot catch the "
        "mistake it exists for"
    )


def test_a_store_over_the_budget_RAISES_with_the_budget_checks_own_message(
    tmp_path: Path,
):
    """The negative control: the instrument must be able to go red.

    🔴 NOTHING IS MONKEYPATCHED HERE. The predecessor of this guard asserted a floor
    while monkeypatching the very variable it claimed to bound, so no value of that
    variable could fail it. This builds a store that genuinely exceeds the REAL
    `_LARGEST_STORE_BYTES`, and asserts on THIS check's own wording — not merely that
    something failed, which a different guard's error would also satisfy.
    """
    with pytest.raises(store_siting.StoreBudgetExceeded) as excinfo:
        with store_siting.store_root(tmp_path) as root:
            _fill(root, _OVER_BUDGET_ENTRIES)
    message = str(excinfo.value)
    assert "_LARGEST_STORE_BYTES budget" in message, message
    assert f"{_OVER_BUDGET_ENTRIES:,} entries" in message, message
    assert "_MIN_FREE_BYTES" in message, (
        "the message must name the constant that was sized against this budget — the "
        "reader's next action is to re-check the free-space floor, not to shrug"
    )


def test_a_store_just_UNDER_the_budget_is_accepted(tmp_path: Path):
    """The other half of the pair, and it is what stops the guard being a tripwire.

    One page under the limit must pass. Without this, a mutant turning `>` into `>=`
    — or a budget quietly set to the peak with no slack, which is exactly what F1
    found — would be invisible: the over-budget test above passes either way.
    """
    with store_siting.store_root(tmp_path) as root:
        _fill(root, _OVER_BUDGET_ENTRIES - 1)


def test_the_budget_check_runs_on_the_DISK_FALLBACK_branch_too(
    tmp_path: Path, monkeypatch
):
    """🔴 A GUARD THAT ONLY RUNS WHERE A TMPFS EXISTS IS BLIND ON THE MACHINES THAT
    HAVE NONE — and those are the machines where a store quietly grows unchecked
    until it reaches one where it matters.

    The assertion on `root` is the reachability half: without it this test passes on
    a tmpfs host by taking the branch it claims to be avoiding, and proves nothing.
    """
    monkeypatch.setattr(store_siting, "tmpfs_dir", lambda: None)
    with pytest.raises(store_siting.StoreBudgetExceeded):
        with store_siting.store_root(tmp_path) as root:
            assert root == tmp_path / "store", (
                f"expected the tmp_path fallback, got {root} — this test took the "
                "tmpfs branch and is not testing the fallback at all"
            )
            _fill(root, _OVER_BUDGET_ENTRIES)


def test_the_budget_check_does_NOT_mask_the_bodys_own_failure(tmp_path: Path):
    """A teardown check that overwrites the real error is worse than no check.

    The store here is deliberately over budget AND the body raises. The author needs
    to read their own failure; a `StoreBudgetExceeded` about a constant would send
    them to the wrong file entirely.
    """
    class Boom(Exception):
        pass

    with pytest.raises(Boom):
        with store_siting.store_root(tmp_path) as root:
            _fill(root, _OVER_BUDGET_ENTRIES)
            raise Boom("the test's own failure")


def test_the_budget_keeps_real_HEADROOM_over_the_measured_peak():
    """🔴 ZERO SLACK IS A TRIPWIRE, NOT A BUDGET.

    This does not try to prove the peak is right — `_check_store_budget` does that,
    at runtime, against the real tree. It pins the weaker, true thing: the budget is
    not sitting exactly on its own requirement, so an ordinary fixture edit cannot
    turn a required merge check red on arithmetic alone.
    """
    peak = store_siting._MEASURED_PEAK_STORE_BYTES
    budget = store_siting._LARGEST_STORE_BYTES
    assert store_siting._BUDGET_HEADROOM >= _MIN_BUDGET_HEADROOM, (
        f"_BUDGET_HEADROOM is {store_siting._BUDGET_HEADROOM}, under the "
        f"{_MIN_BUDGET_HEADROOM} this pins. Shrinking the slack to buy room under "
        "the free-space floor is the move that recreates the zero-slack tripwire — "
        "raise _MIN_FREE_BYTES or shrink the fixture instead."
    )
    assert budget >= peak * _MIN_BUDGET_HEADROOM, (
        f"_LARGEST_STORE_BYTES ({budget:,}) is only {budget / peak:.2f}x the measured "
        f"peak ({peak:,}); it must be at least {_MIN_BUDGET_HEADROOM}x. A budget set "
        "to its own requirement goes red on an unrelated edit."
    )
    # And the budget must BE what `_BUDGET_HEADROOM` says it is. Without this the
    # headroom constant is prose: it could read 1.5 beside a budget carrying 1.26x,
    # which is the same defect as the free-space margin comment one constant below —
    # a number describing another number, with nothing reading either.
    page = store_siting._PAGE_BYTES
    expected = -(-int(peak * store_siting._BUDGET_HEADROOM) // page) * page
    assert budget == expected, (
        f"_LARGEST_STORE_BYTES is {budget:,}, but {peak:,} x "
        f"{store_siting._BUDGET_HEADROOM} rounded up to a page is {expected:,}. One of "
        "the three constants was edited without the others; they are not independent."
    )


def test_the_free_space_floor_keeps_the_MARGIN_its_comment_claims():
    """🔴 A COMMENT IS A CLAIM, AND THIS ONE WAS FALSIFIED BY ITS OWN NEIGHBOUR.

    `_MIN_FREE_BYTES`'s comment read "better than 3x margin". It was true (3.18x) when
    written and false (2.24x) one commit later, because round 6 changed the OTHER
    constant and left the sentence alone. Nothing could notice: no test read the ratio.
    Now one does, so the prose and the arithmetic fail together.
    """
    margin = store_siting._MIN_FREE_BYTES / store_siting._LARGEST_STORE_BYTES
    assert margin >= _MIN_FLOOR_MARGIN, (
        f"_MIN_FREE_BYTES ({store_siting._MIN_FREE_BYTES:,}) is only {margin:.2f}x "
        f"_LARGEST_STORE_BYTES ({store_siting._LARGEST_STORE_BYTES:,}); its comment "
        f"claims better than {_MIN_FLOOR_MARGIN}x. Raising the floor collides with "
        "the measured reason it was lowered from 8 MiB (8 MiB rejects a usable 4 MiB "
        "tmpfs), so the answer is usually a smaller fixture — and whichever you "
        "choose, REWRITE THE COMMENT: it states the ratio, and a stale one there has "
        "already cost a round."
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
