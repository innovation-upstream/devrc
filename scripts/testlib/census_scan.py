"""Derive — never enumerate by hand — the tests whose verdict depends on the
REPO'S FILE SET, so a file can no longer land on `main` breaking one of them.

WHAT THIS IS FOR
----------------
`main` went red twice in one session, hours each, from one shape: a **two-way
ledger pin** broken by a new file arriving without its row.

  * `_KILL_MENTION_LEDGER` (`scripts/claude-hooks/tests/test_guard_core.py`) —
    six docs reddened `main` inside two hours, and every fix was itself a doc.
  * `_OWN_BOUND_LEDGER` (`scripts/tests/test_runner_bound_ledger.py`) — a new
    test file arrived spawning a runner with no `timeout=`, scored `[ABSENT]`,
    and was red the moment it landed.

Neither author could have known. `scripts/scoped-tests.sh` maps a diff to the
tests that NAME what you changed, and a brand-new file names nothing — which is
exactly why that mapping cannot see this class. The full gate would have caught
both and nobody runs a 20-minute gate before merging.

🔴 THE SET IS DERIVED, AND THAT IS THE WHOLE POINT
--------------------------------------------------
A hand-written list of "the ledger tests" is itself a ledger, and it goes stale
the same way — the failure being fixed, one level up. So the population is
computed from the AST at call time, from a mechanical property:

    a test is IN if it (transitively) ENUMERATES THE REPO TREE.

Three enumeration shapes, and nothing else counts:

  1. ``<repo-root-expr>.glob/.rglob/.iterdir(...)`` — a `Path` walk whose
     receiver mentions a module-level name bound from `Path(__file__)`.
  2. ``os.walk(<repo-root-expr>)``.
  3. a ``git ls-files`` string inside a function body (not a docstring).

…then a transitive closure over calls, resolved through each module's own
imports. The closure is what makes `repo_files` work: this repo already owns
ONE two-tier file lister (`testlib.public_ip_scan.repo_files`), most census
guards go through it, and a scan that only looked for literal `rglob` calls
would miss every one of them.

🔴 WHAT IT DELIBERATELY GETS WRONG, AND IN WHICH DIRECTION
----------------------------------------------------------
**It over-classifies, on purpose.** A `git ls-files` inside a *fixture string*
— a shell snippet fed to the script under test — reads identically to a real
scan; `testlib/readset_plugin.py`'s docstring records that exact
over-classification biting an earlier regex. Here the cost of a false positive
is one extra fast test in a seconds-long run, and the cost of a false negative
is a red `main` for hours. So the tie is broken toward including.

**What it still cannot see**, stated rather than hidden:

  * a test whose verdict depends on a file it never reads — no static or
    dynamic tracer can see that (`readset_plugin` names the same gap);
  * an enumeration reached through a callable passed as an ARGUMENT, or through
    `getattr`/a dict of handlers — the closure resolves names and attributes on
    imported modules, not values;
  * a root path that is not derived from `Path(__file__)` (an env var, a
    `pytest` fixture, a hardcoded absolute path);
  * a census performed by a SUBPROCESS the test spawns, unless its argv carries
    `ls-files` literally.

Because of those, `census_nodeids()` is paired with an ANCHOR CHECK — see
`scripts/tests/test_census_scan.py`. The anchors are a positive control (a lower
bound proving the derivation still sees the two incidents it was built for),
never the source of the set.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path

#: `Path` methods that enumerate a directory rather than read one file.
WALK_ATTRS = frozenset({"glob", "rglob", "iterdir"})

#: The literal a `git ls-files` spawn must carry. Matched against string
#: constants in a function body, which is why a `.sh` runner invoked by name is
#: invisible here and a fixture's shell snippet is a false positive.
LS_FILES_TOKEN = "ls-files"

#: Directories whose `.py` files are never parsed.
SKIP_DIR_NAMES = frozenset({"__pycache__", ".git", "node_modules", ".pytest_cache"})


@dataclass
class CensusResult:
    """The derivation's output, with the numbers a caller must be able to read.

    `nodeids` is the answer; everything else exists so a consumer can prove the
    instrument observed something rather than quoting a reassuring zero.
    """

    nodeids: list[str] = field(default_factory=list)
    #: (repo-relative module, function) pairs that enumerate SOME directory.
    enumerator_functions: set[tuple[str, str]] = field(default_factory=set)
    #: …and the subset aimed at THIS repo's tree.
    census_functions: set[tuple[str, str]] = field(default_factory=set)
    #: Modules selected WHOLE because the census runs at import time.
    whole_modules: list[str] = field(default_factory=list)
    #: Python files parsed, and files that failed to parse.
    parsed: int = 0
    unparseable: list[str] = field(default_factory=list)


def _module_root_names(tree: ast.Module) -> set[str]:
    """Module-level names bound from a `Path(__file__)`-derived expression.

    `REPO_ROOT = Path(__file__).resolve().parents[2]` and its spellings. A name
    bound any other way is not treated as the repo root, which is one of the
    named blind spots.
    """
    out: set[str] = set()
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        names = [t.id for t in targets if isinstance(t, ast.Name)]
        if node.value is None or not names:
            continue
        src = ast.dump(node.value)
        if "__file__" in src and "parent" in src:
            out.update(names)
    return out


def _mentions_root(expr: ast.AST, roots: set[str]) -> bool:
    """True when `expr` names a repo-root constant anywhere inside it.

    Covers `REPO_ROOT.rglob(...)`, `(REPO_ROOT / "scripts").glob(...)` and
    `REPO_ROOT.joinpath(x).iterdir()` without dumping the whole subtree — the
    dump is O(subtree) and this runs once per attribute call in the repo.
    """
    for sub in ast.walk(expr):
        if isinstance(sub, ast.Name) and sub.id in roots:
            return True
    return False


def _docstring_node(fn: ast.AST) -> ast.AST | None:
    body = getattr(fn, "body", None)
    if not body:
        return None
    first = body[0]
    if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
        if isinstance(first.value.value, str):
            return first.value
    return None


def _index_module(tree: ast.Module) -> tuple[
    dict[str, ast.AST], dict[str, list[ast.AST]], list[ast.AST], list[ast.AST]
]:
    """ONE traversal per module: nodes attributed to their nearest function.

    🔴 The obvious spelling — `ast.walk(fn)` per function — re-walks every
    nested subtree once per enclosing function and cost 30 s of the first
    version's 46 s. Decorators are children of the `FunctionDef`, so a
    `@pytest.mark.parametrize(...)` argument that calls a census helper is
    attributed to the test it decorates, which is what we want: that call runs
    at import time and its result is the test's parameter set.

    Bare NAME is the key, so a method and a module-level function sharing a name
    share a bucket. Over-inclusion in one direction, and the closure is already
    name-granular.
    """
    fn_defs: dict[str, ast.AST] = {}
    fn_nodes: dict[str, list[ast.AST]] = {}
    module_nodes: list[ast.AST] = []
    import_nodes: list[ast.AST] = []
    stack: list[tuple[ast.AST, str | None]] = [(tree, None)]
    while stack:
        node, owner = stack.pop()
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            # Collected wherever they appear — a function-local import is how
            # several suites reach `testlib`, and dropping those would sever
            # the closure exactly at the shared listers.
            import_nodes.append(node)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            fn_defs[node.name] = node
            fn_nodes.setdefault(node.name, [])
            owner = node.name
        elif owner is None:
            module_nodes.append(node)
        else:
            fn_nodes.setdefault(owner, []).append(node)
        for child in ast.iter_child_nodes(node):
            stack.append((child, owner))
    return fn_defs, fn_nodes, module_nodes, import_nodes


def _enumerates(nodes: list[ast.AST], doc: ast.AST | None) -> bool:
    """Does this function body enumerate a directory — ANY directory?

    Root-blind on purpose. `public_ip_scan.repo_files(root)` walks its own
    PARAMETER, so a root-aware seed would never fire on the one shared lister
    most of this repo's census guards go through. Whether the thing walked is
    THIS repo is decided at the call site instead, by `_passes_root`.

    🔴 The function's own DOCSTRING is excluded before the `ls-files` test. A
    docstring scans nothing, and several helpers here document the shared lister
    by name — counting those would classify half the testlib as an enumerator.
    """
    for node in nodes:
        if node is doc:
            continue
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if LS_FILES_TOKEN in node.value:
                return True
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute):
            if func.attr in WALK_ATTRS:
                return True
            if (
                func.attr == "walk"
                and isinstance(func.value, ast.Name)
                and func.value.id == "os"
            ):
                return True
    return False


def _names_a_root(node: ast.AST, roots: set[str]) -> bool:
    return _mentions_root(node, roots) if roots else False


def _root_enumeration(nodes: list[ast.AST], doc: ast.AST | None, roots: set[str]) -> bool:
    """Does this function enumerate THIS REPO, in its own body?

    The narrower half of the pair. It is what separates
    `repo_files(REPO_ROOT)` — a census of the tree, reddened by any file that
    lands — from `repo_files(tmp_path)`, which is a behavioural test of the
    lister and is reddened by nothing.

    🔴 That distinction is the difference between a **5m41s** run and a **~30s**
    one, measured on this repo: the root-blind version selected 281 nodeids
    including every `tmp_path` exercise of every scanner.
    """
    if not roots:
        return False
    for node in nodes:
        if node is doc:
            continue
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            # A literal `git … ls-files` argv in a function that also names the
            # repo root. Both halves are needed: the token alone is every
            # scanner's tmp-repo fixture.
            if LS_FILES_TOKEN in node.value and any(
                isinstance(n, ast.Name) and n.id in roots for n in nodes
            ):
                return True
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute):
            if func.attr in WALK_ATTRS and _names_a_root(func.value, roots):
                return True
            if (
                func.attr == "walk"
                and isinstance(func.value, ast.Name)
                and func.value.id == "os"
                and node.args
                and _names_a_root(node.args[0], roots)
            ):
                return True
    return False


class _ModuleTable:
    """Every parsed module under `scripts/`, plus how each one resolves a name."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.scripts = root / "scripts"
        self.trees: dict[Path, ast.Module] = {}
        self.unparseable: list[str] = []
        for path in sorted(self.scripts.rglob("*.py")):
            if any(part in SKIP_DIR_NAMES for part in path.parts):
                continue
            try:
                self.trees[path] = ast.parse(path.read_text(encoding="utf-8"))
            except (OSError, SyntaxError, UnicodeDecodeError, ValueError):
                self.unparseable.append(str(path.relative_to(root)))
        self.by_dotted: dict[str, Path] = {}
        for path in self.trees:
            dotted = ".".join(path.relative_to(self.scripts).with_suffix("").parts)
            self.by_dotted.setdefault(dotted, path)
        self.roots = {p: _module_root_names(t) for p, t in self.trees.items()}
        self.index = {p: _index_module(t) for p, t in self.trees.items()}
        self.imports = {
            p: self._imports(p, self.index[p][3]) for p in self.trees
        }

    def _resolve_module(self, name: str, here: Path) -> Path | None:
        """`testlib.public_ip_scan` -> its path; also a same-directory sibling.

        Both spellings are live: suites `sys.path.insert(REPO_ROOT/"scripts")`
        and import `testlib.x`, while scripts beside each other import bare.
        """
        hit = self.by_dotted.get(name)
        if hit is not None:
            return hit
        sibling = here.parent / (name.replace(".", "/") + ".py")
        return sibling if sibling in self.trees else None

    def _imports(
        self, path: Path, import_nodes: list[ast.AST]
    ) -> dict[str, tuple[str, object]]:
        out: dict[str, tuple[str, object]] = {}
        for node in import_nodes:
            if isinstance(node, ast.Import):
                for alias in node.names:
                    target = self._resolve_module(alias.name, path)
                    if target is not None:
                        out[alias.asname or alias.name.split(".")[0]] = ("mod", target)
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                target = self._resolve_module(node.module, path)
                for alias in node.names:
                    if target is not None:
                        out[alias.asname or alias.name] = ("fn", (target, alias.name))
                    else:
                        sub = self._resolve_module(f"{node.module}.{alias.name}", path)
                        if sub is not None:
                            out[alias.asname or alias.name] = ("mod", sub)
        return out

    def call_targets(
        self, path: Path, node: ast.Call, local: set[str]
    ) -> list[tuple[Path, str]]:
        """Resolve a call's callee to (module path, function name) pairs.

        Names resolve against the module's own definitions first, then its
        imports; `mod.fn(...)` resolves when `mod` is an imported module in this
        repo. A callee that is a value (a parameter, a dict lookup, `getattr`)
        resolves to nothing — a named blind spot, not a silent one.
        """
        func = node.func
        imports = self.imports[path]
        if isinstance(func, ast.Name):
            if func.id in local:
                return [(path, func.id)]
            hit = imports.get(func.id)
            if hit is not None and hit[0] == "fn":
                return [hit[1]]  # type: ignore[list-item]
            return []
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            hit = imports.get(func.value.id)
            if hit is not None and hit[0] == "mod":
                return [(hit[1], func.attr)]  # type: ignore[list-item]
        return []


def _is_fixture(fn: ast.AST) -> bool:
    for dec in getattr(fn, "decorator_list", []):
        target = dec.func if isinstance(dec, ast.Call) else dec
        if isinstance(target, ast.Attribute) and target.attr == "fixture":
            return True
        if isinstance(target, ast.Name) and target.id == "fixture":
            return True
    return False


def analyze(root: Path) -> CensusResult:
    """Derive every pytest nodeid whose verdict depends on the repo's file set."""
    table = _ModuleTable(root)
    result = CensusResult(parsed=len(table.trees), unparseable=table.unparseable)

    index = table.index

    # --- pass 1: ENUMERATORS — functions that walk a directory, any directory -
    enumerators: set[tuple[Path, str]] = set()
    for path, (fn_defs, fn_nodes, _mod_nodes, _imp) in index.items():
        for name, fn in fn_defs.items():
            if _enumerates(fn_nodes[name], _docstring_node(fn)):
                enumerators.add((path, name))

    # Call graph, resolved once. Each edge also records whether the call site
    # handed the callee a repo root, which is what turns an enumerator into a
    # census of THIS tree.
    edges: dict[tuple[Path, str], set[tuple[Path, str]]] = {}
    root_edges: dict[tuple[Path, str], set[tuple[Path, str]]] = {}
    for path, (fn_defs, fn_nodes, _mod_nodes, _imp) in index.items():
        local = set(fn_defs)
        roots = table.roots[path]
        for name in fn_defs:
            out: set[tuple[Path, str]] = set()
            rooted: set[tuple[Path, str]] = set()
            for node in fn_nodes[name]:
                if not isinstance(node, ast.Call):
                    continue
                targets = table.call_targets(path, node, local)
                if not targets:
                    continue
                out.update(targets)
                passes_root = any(
                    _names_a_root(a, roots)
                    for a in list(node.args) + [k.value for k in node.keywords]
                )
                # A zero-argument call cannot NAME the root — it closes over a
                # module-level one. `_scan_kill_sites()` is exactly that shape,
                # and excluding it would drop the `_KILL_MENTION_LEDGER`
                # incident this whole module exists for.
                if passes_root or not (node.args or node.keywords):
                    rooted.update(targets)
            edges[(path, name)] = out
            root_edges[(path, name)] = rooted

    changed = True
    while changed:
        changed = False
        for key, out in edges.items():
            if key in enumerators:
                continue
            if out & enumerators:
                enumerators.add(key)
                changed = True

    # --- pass 2: CENSUS — an enumeration aimed at this repo -------------------
    census: set[tuple[Path, str]] = set()
    for path, (fn_defs, fn_nodes, _mod_nodes, _imp) in index.items():
        roots = table.roots[path]
        for name, fn in fn_defs.items():
            if _root_enumeration(fn_nodes[name], _docstring_node(fn), roots):
                census.add((path, name))
                continue
            if root_edges[(path, name)] & enumerators:
                census.add((path, name))

    changed = True
    while changed:
        changed = False
        for key, out in edges.items():
            if key in census:
                continue
            if out & census:
                census.add(key)
                changed = True

    result.enumerator_functions = {
        (str(p.relative_to(root)), n) for p, n in enumerators
    }
    result.census_functions = {
        (str(p.relative_to(root)), n) for p, n in census
    }

    # --- selection ------------------------------------------------------------
    for path, tree in sorted(table.trees.items()):
        if not path.name.startswith("test_"):
            continue
        rel = str(path.relative_to(root))
        roots = table.roots[path]
        fn_defs, _fn_nodes, mod_nodes, _imp = index[path]
        local = set(fn_defs)

        # A census that runs at IMPORT time makes the whole module depend on
        # the tree — collection itself can fail, so no per-test selection is
        # meaningful.
        module_level = False
        for node in mod_nodes:
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr in WALK_ATTRS:
                if _names_a_root(func.value, roots):
                    module_level = True
                    break
            targets = table.call_targets(path, node, local)
            if not targets:
                continue
            if any(t in census for t in targets):
                module_level = True
                break
            passes_root = any(
                _names_a_root(a, roots)
                for a in list(node.args) + [k.value for k in node.keywords]
            )
            if (passes_root or not (node.args or node.keywords)) and any(
                t in enumerators for t in targets
            ):
                module_level = True
                break
        if module_level:
            result.whole_modules.append(rel)
            result.nodeids.append(rel)
            continue

        census_fixtures = {
            n for n, fn in fn_defs.items()
            if _is_fixture(fn) and (path, n) in census
        }

        def _wanted(fn: ast.AST, name: str) -> bool:
            if (path, name) in census:
                return True
            args = getattr(fn, "args", None)
            if args is not None:
                params = [a.arg for a in list(args.args) + list(args.kwonlyargs)]
                if census_fixtures.intersection(params):
                    return True
            return False

        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name.startswith("test_") and _wanted(node, node.name):
                    result.nodeids.append(f"{rel}::{node.name}")
            elif isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
                for sub in node.body:
                    if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        if sub.name.startswith("test_") and _wanted(sub, sub.name):
                            result.nodeids.append(
                                f"{rel}::{node.name}::{sub.name}"
                            )

    result.nodeids.sort()
    return result


def census_nodeids(root: Path) -> list[str]:
    """The derived selection, as pytest nodeids."""
    return analyze(root).nodeids


if __name__ == "__main__":  # pragma: no cover - the CLI `ledger-check.sh` drives
    import sys

    where = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path.cwd()
    out = analyze(where)
    if "--stats" in sys.argv:
        print(
            f"parsed={out.parsed} unparseable={len(out.unparseable)} "
            f"enumerator-functions={len(out.enumerator_functions)} "
            f"census-functions={len(out.census_functions)} "
            f"nodeids={len(out.nodeids)} whole-modules={len(out.whole_modules)}"
        )
    else:
        for nodeid in out.nodeids:
            print(nodeid)
