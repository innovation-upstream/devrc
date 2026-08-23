"""The IMAGE must learn about a new import — requirements.txt and the Dockerfile, DERIVED.

🔴 WHY THIS FILE EXISTS. A missing runtime dependency in this service is not a
crash, it is a ZOMBIE. `consumer.run()` treats any exception out of the stream
factory as a dropped stream and reconnects with backoff, so an image built
without `websocket-client` comes up healthy, logs "reconnecting" forever,
ingests zero rows, and reports nothing anywhere that says why. Hours were lost
to exactly that before the image existed. The failure has to happen at BUILD
time.

Two guards live in the build itself (`build-push.sh`: it imports every derived
dependency inside the built image, and asserts /app's file set exactly). Those
run when somebody builds. THIS file runs on every gate, and pins the two
declarations a build would silently obey:

  * `requirements.txt` — what pip installs, versus what the modules IMPORT;
  * the Dockerfile's `COPY` list — which modules are in the image at all.

🔴 EVERY LEDGER HERE IS DERIVED BY WALKING THE AST, never restated. A
hand-written list of dependencies could not catch the thing this file exists to
catch — an `import` added to `consumer.py` with nothing else touched — because
the list and the omission would be written by the same person in the same
moment. `sys.stdlib_module_names` decides what is stdlib (3.10+, so the version
that runs the gate answers it, not a table here) and the module basenames in
`scripts/signal/` decide what is local.

The one thing that CANNOT be derived is that a PyPI distribution name is not
always the module you import (`psycopg2-binary` -> `psycopg2`,
`websocket-client` -> `websocket`). That mapping is written down once, below,
and `test_every_requirement_has_a_known_import_name` asserts it is TOTAL over
requirements.txt — so a new dependency with no mapping fails loudly here rather
than quietly dropping out of the parity check it was added to satisfy.
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

import pytest

import consumer

SIGNAL_DIR = Path(consumer.__file__).resolve().parent
REQUIREMENTS = SIGNAL_DIR / "requirements.txt"
DOCKERFILE = SIGNAL_DIR / "Dockerfile"
DOCKERIGNORE = SIGNAL_DIR / "Dockerfile.dockerignore"

# PyPI distribution name -> the name you `import`. Asserted TOTAL over
# requirements.txt by a test below; anything not listed here is treated as
# importing under its own name.
DIST_TO_IMPORT = {
    "psycopg2-binary": "psycopg2",
    "websocket-client": "websocket",
}

# Plausible floors. NOT the contract (the derived sets are) — they exist so a
# broken parser fails as "the harness is broken" rather than passing vacuously.
MIN_RUNTIME_MODULES = 4
MIN_THIRD_PARTY_IMPORTS = 4


# --------------------------------------------------------------------------- #
# Derivations
# --------------------------------------------------------------------------- #
def runtime_modules() -> list[Path]:
    """Every .py in scripts/signal/ — i.e. the modules the image must carry.

    `tests/` is a subdirectory, so a plain glob already excludes it; that is
    checked below rather than assumed.
    """
    return sorted(SIGNAL_DIR.glob("*.py"))


def imported_names(source: str, *, local: set[str]) -> set[str]:
    """Third-party top-level import names in `source`, by AST.

    Walks the WHOLE tree, so a deferred import inside a function counts — which
    matters here more than anywhere: `websocket` is imported inside
    `ws_stream_factory()` and `minio` inside `_open_minio()`, precisely the two
    that a module-level-only scan would miss and precisely the two whose absence
    produces the silent reconnect loop.
    """
    found: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            # level > 0 is an explicit relative import: local by construction.
            names = [node.module] if (node.level == 0 and node.module) else []
        else:
            continue
        for name in names:
            root = name.split(".")[0]
            if root not in sys.stdlib_module_names and root not in local:
                found.add(root)
    return found


def third_party_imports() -> set[str]:
    modules = runtime_modules()
    local = {p.stem for p in modules}
    found: set[str] = set()
    for path in modules:
        found |= imported_names(path.read_text(encoding="utf-8"), local=local)
    return found


def requirement_dists() -> list[str]:
    """The distribution names in requirements.txt, comments and blanks stripped."""
    dists = []
    for raw in REQUIREMENTS.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        # No version specifiers are used today; tolerate them so adding one is
        # not silently read as part of the name.
        dists.append(re.split(r"[<>=!~\[]", line, maxsplit=1)[0].strip())
    return dists


def dockerfile_copied_files() -> set[str]:
    """Basenames of the scripts/signal/ files the Dockerfile COPYs."""
    copied = set()
    for m in re.finditer(r"^COPY\s+(\S+)\s+\S+\s*$", DOCKERFILE.read_text(encoding="utf-8"),
                         re.MULTILINE):
        src = m.group(1)
        if src.startswith("scripts/signal/"):
            copied.add(Path(src).name)
    return copied


# --------------------------------------------------------------------------- #
# Harness controls — run these before believing anything below them
# --------------------------------------------------------------------------- #
def test_the_derivations_observe_something():
    """POSITIVE CONTROL. Every parity claim in this file is `derived == derived`,
    and two empty sets are equal. A glob that matched nothing, a regex that lost
    its anchor, a requirements parser that ate every line — each of those makes
    the whole file pass while measuring nothing. So each derivation is asserted
    non-empty and plausibly-sized FIRST.
    """
    modules = runtime_modules()
    assert len(modules) >= MIN_RUNTIME_MODULES, (
        f"HARNESS BROKEN: found {len(modules)} module(s) in {SIGNAL_DIR}; the "
        f"import derivation would have almost nothing to walk")
    assert len(third_party_imports()) >= MIN_THIRD_PARTY_IMPORTS, (
        "HARNESS BROKEN: the AST walk derived fewer than "
        f"{MIN_THIRD_PARTY_IMPORTS} third-party imports")
    assert requirement_dists(), "HARNESS BROKEN: requirements.txt parsed to zero entries"
    assert dockerfile_copied_files(), (
        "HARNESS BROKEN: the Dockerfile COPY regex matched nothing — every "
        "COPY-list assertion below would pass vacuously")


def test_the_ast_walker_can_see_a_new_import():
    """POSITIVE CONTROL, the one that matters. The guard's entire job is to
    NOTICE a newly added import. A walker that reported the four names it
    already knows — hardcoded, or crashing into a cached result — would look
    identical on today's tree and be blind to tomorrow's change.

    So feed it a source that imports something no signal module imports, in each
    of the three syntactic shapes, and watch the set MOVE. `zoneinfo` is the
    stdlib control on the same input: it must NOT appear, or the walker is
    reporting everything and its parity check can never fail either.
    """
    src = (
        "import brandnewdep\n"
        "import zoneinfo\n"
        "from otherdep.sub import thing\n"
        "def f():\n"
        "    import deferreddep\n"
        "    from consumer import KIND_MESSAGE\n"
    )
    found = imported_names(src, local={"consumer"})
    assert found == {"brandnewdep", "otherdep", "deferreddep"}, (
        f"the AST walker returned {sorted(found)}. It must see plain, from-, and "
        "function-local imports; it must not report stdlib (`zoneinfo`) or local "
        "(`consumer`) names")


def test_dist_to_import_map_is_only_needed_where_names_differ():
    """A mapping entry that maps a name to ITSELF is dead weight that reads as
    coverage. Every entry here must actually rename something.
    """
    for dist, mod in DIST_TO_IMPORT.items():
        assert dist != mod, (
            f"DIST_TO_IMPORT['{dist}'] maps to itself — the fallback already "
            "handles that case; delete the entry")


# --------------------------------------------------------------------------- #
# The contract
# --------------------------------------------------------------------------- #
def test_every_requirement_has_a_known_import_name():
    """A new dependency whose import name differs from its distribution name must
    be declared in DIST_TO_IMPORT, or the parity test below would compare it
    against an import that never appears and fail confusingly — or worse, a
    future edit "fixes" that by dropping it from the comparison entirely.

    Checked by IMPORTING it: the gate installs these (flake.nix `checks.pytests`
    carries psycopg2, minio, requests), so a wrong mapping is observable rather
    than assumed. `websocket-client` is NOT in the gate's closure — the hermetic
    suite injects every transport — so that one is checked against the map only,
    and named here so the exemption is visible instead of silent.
    """
    not_installed_in_the_gate = {"websocket-client"}
    for dist in requirement_dists():
        mod = DIST_TO_IMPORT.get(dist, dist)
        if dist in not_installed_in_the_gate:
            assert dist in DIST_TO_IMPORT, (
                f"{dist} is not importable in the gate's environment, so its "
                "import name cannot be verified here — it must be declared in "
                "DIST_TO_IMPORT explicitly")
            continue
        pytest.importorskip(
            mod,
            reason=f"requirements.txt lists {dist}, which should import as {mod}")


def test_requirements_matches_what_the_modules_actually_import():
    """🔴 THE GUARD. Both directions, and neither is cosmetic.

    A third-party import with no requirement -> the image builds fine and the
    pod reconnects forever (the original defect).

    A requirement no module imports -> a dependency nobody can point at, kept
    alive because deleting it feels risky. Removing it is a decision; drifting
    into it is not.
    """
    imported = third_party_imports()
    declared = {DIST_TO_IMPORT.get(d, d) for d in requirement_dists()}

    missing = imported - declared
    assert not missing, (
        f"scripts/signal/ imports {sorted(missing)} but requirements.txt does not "
        "declare it. The image would build, start, and ingest NOTHING — "
        "`run()` swallows the ImportError as a dropped stream and reconnects "
        "forever. Add it to scripts/signal/requirements.txt (and to "
        "DIST_TO_IMPORT if the distribution name differs from the import name).")

    unused = declared - imported
    assert not unused, (
        f"requirements.txt declares {sorted(unused)} but no module under "
        f"{SIGNAL_DIR.name}/ imports it. Either it is dead and should be "
        "removed, or the code that needed it was deleted and this is the "
        "leftover.")


def test_dockerfile_copies_every_runtime_module():
    """🔴 THE SEAM. requirements.txt being right says nothing about whether the
    module that imports them is even in the image.

    The Dockerfile COPYs by name on purpose (devrc is public; `COPY . .` bakes a
    working tree into a pushed layer). The cost of that choice is exactly this:
    a new module added to scripts/signal/ is silently absent from the image, and
    its ImportError lands inside the same reconnect loop as a missing
    dependency. So the COPY list is pinned to the directory listing, both ways —
    a module added without a COPY fails, and a COPY left behind by a deleted
    module fails too.
    """
    on_disk = {p.name for p in runtime_modules()} | {REQUIREMENTS.name}
    copied = dockerfile_copied_files()
    assert copied == on_disk, (
        f"Dockerfile COPY list {sorted(copied)} != scripts/signal/ contents "
        f"{sorted(on_disk)}.\n"
        "  in on_disk but not copied: the image will NOT contain it — an "
        "ImportError inside run()'s reconnect loop, which logs nothing.\n"
        "  in copied but not on_disk: the build will FAIL on a missing source.")


def test_dockerignore_allowlists_exactly_the_copied_files():
    """The per-Dockerfile ignore file is what makes `COPY`-by-name safe: it denies
    `**` and re-admits only the named paths, so the build context cannot carry a
    stray working-tree file to the daemon at all. If it drifts from the COPY
    list, the build fails on a missing source (loud) or admits more context than
    intended (quiet) — this pins the quiet direction too.
    """
    text = DOCKERIGNORE.read_text(encoding="utf-8")
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    assert lines and lines[0] == "**", (
        "Dockerfile.dockerignore must DENY everything on its first line and "
        f"re-admit by name; it starts with {lines[0]!r}")
    admitted = {Path(ln[1:]).name for ln in lines[1:] if ln.startswith("!")}
    copied = dockerfile_copied_files()
    assert admitted == copied, (
        f"Dockerfile.dockerignore admits {sorted(admitted)} but the Dockerfile "
        f"COPYs {sorted(copied)}")


def test_the_dockerfile_does_not_hand_write_a_second_dependency_list():
    """requirements.txt is the single source of truth, and the Dockerfile's header
    says so. A `pip install <names>` alongside it is the drift this whole file
    exists to prevent, arriving from the other side.
    """
    for line in DOCKERFILE.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped.startswith("RUN") or "pip install" not in stripped:
            continue
        assert "-r " in stripped, (
            f"Dockerfile installs packages without -r requirements.txt: {stripped!r}")


def test_tests_are_not_part_of_the_runtime_module_set():
    """`runtime_modules()` globs one level, so tests/ is excluded by construction.
    Pinned because the assertion above is set EQUALITY against the Dockerfile: if
    this ever started matching tests/, the fix would look like "add tests to the
    image", which is the wrong direction.
    """
    assert not any("tests" in p.parts for p in runtime_modules())
    assert (SIGNAL_DIR / "tests").is_dir(), (
        "HARNESS BROKEN: there is no tests/ directory, so the exclusion above "
        "proves nothing")


# --------------------------------------------------------------------------- #
# 🔴 The build's subcommand control and the CLI must agree — pinned BOTH ways.
#
# `build-push.sh` control 3 compares the image's argparse choices against a
# hardcoded `want_choices` set, and REFUSES TO PUSH on any difference, including
# an addition. That is the right design — a new operator subcommand is a
# decision about what the pod can be told to do — but nothing kept the list in
# sync with the parser, so the two could drift apart silently and the mismatch
# only surfaced at BUILD time, after the code had merged. Adding `health` in
# #540 hit exactly that: a green gate, then a refused push.
#
# This closes it at the gate instead. It fails when the CLI grows or loses a
# subcommand without the control being updated, AND when the control lists one
# the CLI does not have.

def _build_push_want_choices() -> set[str]:
    import re
    text = (Path(__file__).resolve().parents[1] / "build-push.sh").read_text(
        encoding="utf-8")
    m = re.search(r'^want_choices="([^"]*)"', text, re.MULTILINE)
    assert m, "build-push.sh has no want_choices line — the control was renamed"
    return set(m.group(1).split())


def _cli_subcommands() -> set[str]:
    import consumer
    parser = consumer.build_parser()
    for action in parser._actions:
        if getattr(action, "choices", None) and hasattr(action, "_name_parser_map"):
            return set(action.choices)
    raise AssertionError("no subparser action found on the CLI parser")


def test_the_build_control_lists_EXACTLY_the_CLI_subcommands():
    want = _build_push_want_choices()
    have = _cli_subcommands()
    assert want == have, (
        f"build-push.sh control 3 and consumer.py disagree.\n"
        f"  only in build-push.sh: {sorted(want - have)}\n"
        f"  only in the CLI:       {sorted(have - want)}\n"
        f"Update want_choices in build-push.sh — deliberately: that control "
        f"exists so a new subcommand is an explicit decision about what the "
        f"pod can be told to do.")


def test_the_two_way_pin_is_reading_REAL_data_not_empty_sets():
    """Positive control. Two empty sets compare equal, so the test above would
    pass with both readers wired to nothing — the failure mode its own subject
    (control 3) documents as 'Empty means the parse found no {…} token at all'."""
    want = _build_push_want_choices()
    have = _cli_subcommands()
    assert len(want) >= 8, want
    assert len(have) >= 8, have
    assert "run" in have and "health" in have
