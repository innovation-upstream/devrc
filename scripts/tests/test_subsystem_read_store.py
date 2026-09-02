#!/usr/bin/env python3
"""The host-local READ store: which directory, and can it date itself?

🔴 WHAT THIS FILE GUARDS, IN ONE SENTENCE. The Cairn cutover made a hosted pod
the canonical datastore, FROZE the per-host mirror at
`~/.claude/analyze-service-index` (entry files `0444`, nothing refreshes it) and
introduced a synced cache at `~/.cache/subsystem-store`. Nobody repointed the
READ path, so both prescribed read surfaces — `subsystem_recall.py`'s CLI (what
`/resume` step 4 runs) and `service_recon.py`'s recon (what `/analyze-service`
runs) — went on reading the frozen copy. MEASURED 2026-09-02 on the workbench:
the frozen mirror served **26** `devrc/` entries and the cache **29**, and the
frozen one printed "ALL 26 entries in `devrc/`, none omitted" with no staleness
stamp anywhere in the output. A completeness claim, about a store that had
stopped moving, with nothing in the render able to say so.

🔴 THE DISCRIMINATOR IS THE STAMP, NOT THE PATH. `cairn sync` writes
`.sync-stamp` into the cache; the frozen mirror has none. So the guard is
"REFUSE a store that cannot date itself", which is strictly wider than "do not
read that one path" — it also catches a cache that was never synced on a fresh
host, and it cannot be walked by moving the frozen tree somewhere else.

🔴 THE LIBRARY IS DELIBERATELY EXEMPT, AND `TestThePodContractIsUnchanged`
BELOW IS THE PIN. `subsystem-store-api/server.py` imports `subsystem_recall` as
a library (`rc.recall`, `rc.search`, `rc.load_store`) and serves `/data`, which
has no `.sync-stamp` and never will; `scripts/cairn` imports it the same way. A
refusal inside `recall()` would take down the pod AND the very client whose sync
produces the stamp — the whole store, offline, in one commit. Verified by grep
before this file was written: neither shells the CLI, neither calls `rc.main`.
"""

from __future__ import annotations

import ast
import dataclasses
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "lib"))

import service_recon as srec  # noqa: E402
import subsystem_read_store as rs  # noqa: E402
import subsystem_recall as rc  # noqa: E402
import subsystem_touch as st  # noqa: E402

SCOPE = "workbench-cfg"

# The stamp `cairn sync` actually writes, field-for-field. Values are synthetic
# but the SHAPE is the writer's — a stamp missing `coverage` or `entries` would
# be a different artifact and would not exercise the multi-line header render.
STAMP_LINES = (
    "synced=1788363567",
    "revision=r-fixture-9",
    "snapshot=seeded=2026-09-01T20:38:36Z staged_entries=49 newest=2026-09-02T15:38:28Z",
    "entries=201",
    "coverage=ALL",
)


def _entry(service: str, scope: str) -> str:
    return "\n".join(
        [
            "---",
            f"service: {service}",
            f"scope: {scope}",
            "---",
            "",
            "## What it is",
            "A durable thing a recall block MUST name.",
            "",
            "## Pointers",
            "- ops skill `manage-widget` — invoke it for restarts",
            "",
            "## Nuance / work-history",
            "- 2026-01-02: the readiness probe lies for 40s after a reload.",
            "",
        ]
    )


def _store(root: Path, *, stamped: bool) -> Path:
    """A real store, with or without the stamp. Nothing else differs."""
    store = root / "store"
    scope = store / SCOPE
    scope.mkdir(parents=True)
    (scope / "collector.md").write_text(_entry("collector", SCOPE), encoding="utf-8")
    if stamped:
        (store / rs.SYNC_STAMP).write_text("\n".join(STAMP_LINES) + "\n", encoding="utf-8")
    return store


@pytest.fixture()
def repointed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Repoint the DEFAULT read-store resolution at a tmp directory.

    🔴 EVERY TEST BELOW THAT EXERCISES THE DEFAULT USES THIS. Without it the
    default resolves to the operator's real `~/.cache/subsystem-store` and the
    suite would read — and report on — a live, client-confidential store.
    `read_store_root()` reads the module global at call time precisely so this
    one assignment reaches every consumer.
    """

    def _point_at(store: Path) -> Path:
        monkeypatch.setattr(rs, "DEFAULT_CACHE_ROOT", store)
        return store

    return _point_at


# =============================================================================
# THE WIRE CONSTANTS. One ledger, because this defect kept coming back.
# =============================================================================
#
# 🔴 THIS CLASS EXISTS BECAUSE THE SAME FINDING RECURRED FIVE TIMES.
# `SYNC_STAMP`, then `REMEDY` and `DEFAULT_CACHE_ROOT`, then `NOT_READ_PREFIX`
# and `INDEX_UNSTAMPED` — every one asserted only as `<constant> in <output>`,
# i.e. the constant agreeing with itself, and every one SURVIVING a rename
# against a fully green suite. Fixing them one at a time is what let the fourth
# happen: the F1 fix introduced `NOT_READ_PREFIX` unpinned in the same session
# that pinned the other three.
#
# So the remedy is a LEDGER rather than another one-off pin. Each entry maps a
# constant to the literal it must equal, and `test_the_ledger_is_two_way` fails
# when the new module grows a constant nobody put here — which is the only part
# that can stop the sixth.
#
# 🔴 THE FIFTH HID INSIDE THE LEDGER ITSELF. The sweep was `isupper() and
# isinstance(value, str)` while its docstring claimed the module's "whole
# surface" was in scope. MEASURED: `FROZEN_MIRROR = Path.home() / ".claude" /
# "analyze-service-index"` (a `Path`), `STAMP_FIELDS = ("synced", …)` (a tuple)
# and `Wire_Fact_v2 = "another-status"` (mixed case) each SURVIVED it — and
# `DEFAULT_CACHE_ROOT`, the most load-bearing wire fact in the module, is a
# `Path` and was therefore invisible to the sweep, surviving only on a
# hand-written pin below: exactly the one-off pattern the ledger replaces. The
# sweep now enumerates module-scope ASSIGNMENTS from the SOURCE, so the shape of
# the value cannot excuse one.
#
#: `(module, attribute, the value it MUST equal)`. The expected value is written
#: out HERE — never read back off the module — so no entry can be satisfied by a
#: constant agreeing with itself.
WIRE_CONSTANTS: tuple[tuple[object, str, object], ...] = (
    # A FILENAME on disk, written by a deployed `cairn sync`.
    (rs, "SYNC_STAMP", ".sync-stamp"),
    # A COMMAND a human is told to type, by the refusal and by two SKILL.mds.
    (rs, "REMEDY", "cairn sync"),
    # A DIRECTORY every reader resolves and `scripts/cairn` writes. Not a `str`,
    # which is precisely why the old sweep could not see it.
    (rs, "DEFAULT_CACHE_ROOT", Path.home() / ".cache" / "subsystem-store"),
    # A RENDERED TOKEN two renderers emit and `analyze-service/SKILL.md` tells
    # the reader to relay ("the `stamp:` lines").
    (rs, "STAMP_PREFIX", "  stamp: "),
    # A STATUS other code and `analyze-service/SKILL.md` match on by name.
    (srec, "INDEX_UNSTAMPED", "store-unstamped"),
    # A PREFIX a JSON consumer parses to tell "read" from "refused".
    (srec, "NOT_READ_PREFIX", "NOT READ"),
)

#: Module-scope names of `subsystem_read_store` that are NOT wire facts.
#: Enumerated, not pattern-matched: an unlisted one is a wire fact by default,
#: which is the direction that fails safe.
NOT_WIRE_FACTS: frozenset[str] = frozenset()


def _module_scope_assignments(path: Path) -> set[str]:
    """Every non-underscore name ASSIGNED at a module's top level, from source.

    🔴 READ OFF THE AST, NOT `vars()`. `vars()` cannot tell a constant this
    module declares from a name it imported, so a sweep over it has to filter by
    NAME SHAPE (`isupper()`) and VALUE TYPE (`isinstance(..., str)`) — and both
    filters are holes: a `Path`, a tuple, a frozenset or a mixed-case name walks
    straight through. An assignment is an assignment whatever it holds.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in tree.body:
        targets: list[ast.expr] = []
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        for target in targets:
            if isinstance(target, ast.Name) and not target.id.startswith("_"):
                found.add(target.id)
    return found


def _assigned_anywhere(path: Path) -> set[str]:
    """Every non-underscore name assigned at ANY depth in a file.

    Wider than `_module_scope_assignments` on purpose: a re-declaration inside a
    `try:` at module scope, or a local shadowing an imported constant, is the
    same "second copy of somebody else's fact" hazard.
    """
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        targets: list[ast.expr] = []
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        for target in targets:
            if isinstance(target, ast.Name) and not target.id.startswith("_"):
                found.add(target.id)
    return found


class TestTheWireConstants:
    """Every string that crosses a boundary, pinned to its literal."""

    @pytest.mark.parametrize(
        "module, name, literal",
        [(m, n, lit) for m, n, lit in WIRE_CONSTANTS],
        ids=[f"{n}" for _m, n, _lit in WIRE_CONSTANTS],
    )
    def test_the_constant_equals_its_literal(self, module, name, literal) -> None:
        """The literal is written out HERE, so the assertion cannot be satisfied
        by the constant being consistent with itself."""
        assert getattr(module, name) == literal

    def test_the_ledger_is_two_way_over_the_new_module(self) -> None:
        """🔴 THE HALF THAT STOPS THE NEXT ONE.

        Pinning five constants does nothing about the sixth. This enumerates
        every module-scope ASSIGNMENT in `subsystem_read_store` — the module this
        change introduced, so its whole surface really is in scope — and requires
        each to be either in `WIRE_CONSTANTS` or explicitly excused. Adding one
        without a pin fails here.

        🔴 SHAPE-BLIND ON PURPOSE. The previous version filtered `isupper() and
        isinstance(value, str)` under this same docstring, so a wire fact
        arriving as a `Path`, a tuple, a frozenset or a mixed-case name passed
        silently — and `DEFAULT_CACHE_ROOT` was already such a fact, covered only
        by a hand-written pin. Reading assignments out of the SOURCE removes both
        filters: what a constant HOLDS stops being an escape hatch.

        `service_recon` is deliberately NOT swept: it predates this change and
        carries many string constants that are not wire facts, so a sweep there
        would either be noise or need an exclusion list long enough to hide a
        real omission. Its two are pinned by name above.
        """
        source = ROOT / "scripts" / "lib" / "subsystem_read_store.py"
        declared = _module_scope_assignments(source)
        # POSITIVE CONTROL. A walk that returned nothing — wrong path, a parse
        # that found no `Assign` — would report a clean sweep over zero names,
        # which is the silent zero this whole change exists to kill.
        assert "SYNC_STAMP" in declared, declared
        # Every swept name must really be on the module: an AST name the module
        # does not expose would mean the sweep and the import disagree.
        for name in declared:
            assert hasattr(rs, name), f"{name} is assigned but not importable"

        pinned = {n for m, n, _lit in WIRE_CONSTANTS if m is rs}
        assert declared - pinned - NOT_WIRE_FACTS == set(), (
            f"`subsystem_read_store` grew module-scope constant(s) "
            f"{sorted(declared - pinned - NOT_WIRE_FACTS)} with no pin. "
            f"Add them to WIRE_CONSTANTS, or to NOT_WIRE_FACTS with a reason."
        )
        assert pinned <= declared, (
            f"WIRE_CONSTANTS names {sorted(pinned - declared)}, which "
            f"`subsystem_read_store` no longer declares."
        )

    def test_the_analyze_service_step_does_NOT_chain_the_sync_with_double_ampersand(
        self,
    ) -> None:
        """🔴 `&&` HERE DELETES THE WHOLE BRIEF, AND THE PROSE PROMISES OTHERWISE.

        `cairn sync` exits 4 whenever the pod is unreachable but a usable cache
        survives — that is `cmd_sync`'s stated contract, not an edge case. Under
        `&&` that non-zero short-circuits and `service_recon.py` never runs, so
        `/analyze-service` emits NOTHING during an outage, exactly when the
        stamped cache would have served the index at full fidelity. Eight lines
        below, the same file promises "a failed sync costs you the index block,
        never the brief".

        Pinned on the COMMAND, because nothing else could: the REMEDY test only
        requires the substring `cairn sync`, which `&&` satisfies.
        """
        doc = (ROOT / "claude/skills/analyze-service/SKILL.md").read_text(encoding="utf-8")
        step = [
            ln for ln in doc.splitlines()
            if rs.REMEDY in ln and "service_recon.py" in ln
        ]
        assert len(step) == 1, f"expected one step-1 command line, found {step}"
        assert "&&" not in step[0], (
            f"step 1 chains the sync with `&&`: {step[0]!r}. A failed sync then "
            f"suppresses the entire recon. Use `;`."
        )
        assert f"{rs.REMEDY};" in step[0], (
            f"step 1 must separate the two commands with `;`: {step[0]!r}"
        )
        # …and the promise the separator has to keep is still there to keep.
        assert "costs you the index block, never the brief" in doc

    def test_cairns_cache_default_FOLLOWS_a_repointed_resolver(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """🔴 THE WRITER MUST MOVE WITH THE READER.

        `scripts/cairn` used to do `from subsystem_read_store import
        DEFAULT_CACHE_ROOT as DEFAULT_CACHE`, which binds at IMPORT — the exact
        spelling `read_store_root`'s docstring warns defeats repointing. Nothing
        depended on it yet, so the hazard was invisible: a future cairn test
        repointing the resolver would have moved the reader and left cairn
        writing to the operator's real cache. Asserted behaviourally, through
        cairn's own parser, rather than by grepping for the import shape.
        """
        path = ROOT / "scripts" / "cairn"
        assert "DEFAULT_CACHE_ROOT as DEFAULT_CACHE" not in path.read_text(encoding="utf-8")
        # `scripts/cairn` has no `.py` suffix, so `spec_from_file_location`
        # returns None. Same loader-less exec the cairn suites use.
        spec = importlib.util.spec_from_loader(
            "cairn_cache_default_probe", loader=None, origin=str(path)
        )
        module = importlib.util.module_from_spec(spec)
        module.__file__ = str(path)
        exec(compile(path.read_text(encoding="utf-8"), str(path), "exec"), module.__dict__)
        monkeypatch.setattr(module._read_store, "DEFAULT_CACHE_ROOT", tmp_path / "moved")
        assert module.build_parser().parse_args(["sync"]).cache == tmp_path / "moved"

    def test_the_status_literal_matches_the_skill_prose_that_quotes_it(self) -> None:
        """`analyze-service/SKILL.md` hardcodes `store-unstamped` in prose. That
        is the same code/prose drift closed for `REMEDY`, so it gets the same
        cross-check rather than a second copy of the fact."""
        doc = (ROOT / "claude/skills/analyze-service/SKILL.md").read_text(encoding="utf-8")
        assert f"index: {srec.INDEX_UNSTAMPED}" in doc


# =============================================================================
# The resolver itself.
# =============================================================================


class TestTheResolver:
    def test_a_stamped_store_reports_its_stamp_UNPARSED(self, tmp_path: Path) -> None:
        store = _store(tmp_path, stamped=True)
        got = rs.resolve_read_store(store)
        assert got.stamped is True
        assert got.reason is None
        assert got.stamp == STAMP_LINES  # unparsed, in file order

    def test_an_unstamped_store_is_not_stamped_and_says_why(self, tmp_path: Path) -> None:
        store = _store(tmp_path, stamped=False)
        got = rs.resolve_read_store(store)
        assert got.stamped is False
        assert got.stamp is None
        assert rs.SYNC_STAMP in (got.reason or "")

    def test_an_EMPTY_stamp_is_ABSENT_not_a_stamp_with_no_fields(self, tmp_path: Path) -> None:
        """🔴 "The store is stamped" must not be satisfiable by a zero-byte file.

        A truncated write is a real failure mode for a file `cairn sync` creates
        during an interrupted sync, and an empty stamp would otherwise pass the
        guard while carrying no freshness at all — the exact silent-zero shape
        the refusal exists to prevent.
        """
        store = _store(tmp_path, stamped=False)
        (store / rs.SYNC_STAMP).write_text("\n  \n\n", encoding="utf-8")
        got = rs.resolve_read_store(store)
        assert got.stamped is False
        assert "empty" in (got.reason or "")

    def test_the_default_resolution_is_the_cache_and_not_the_frozen_mirror(self) -> None:
        """Pinned two-way: WHERE it points, and where it must not."""
        assert rs.read_store_root() == rs.DEFAULT_CACHE_ROOT
        assert rs.DEFAULT_CACHE_ROOT != st.DEFAULT_STORE_ROOT
        assert rs.DEFAULT_CACHE_ROOT.name == "subsystem-store"
        assert rs.DEFAULT_CACHE_ROOT.parent.name == ".cache"

    def test_the_cache_root_is_ANCHORED_AT_HOME(self) -> None:
        """🔴 THE `$HOME` ANCHOR WAS THE UNPINNED HALF, AND IT IS THE DANGEROUS ONE.

        MEASURED: `Path("/var/tmp") / ".cache" / "subsystem-store"` SURVIVED all
        668 tests. The two assertions above check the tail; `read_store_root() ==
        DEFAULT_CACHE_ROOT` is the constant agreeing with itself. Nothing checked
        the anchor — so a mutant put a CLIENT-CONFIDENTIAL store in a
        world-writable directory, and `scripts/cairn` imports this same constant
        for its `--cache` default, so the WRITER would have followed it there.

        🔴 THE LITERAL ITSELF NOW LIVES IN `WIRE_CONSTANTS`, NOT HERE. This test
        held the only pin on it, which is exactly the one-off shape the ledger
        exists to replace — and because the constant is a `Path`, the ledger's
        old `isinstance(..., str)` sweep could not see that it was unpinned. The
        two-way sweep fails if the entry is ever removed, so deleting the ledger
        row cannot quietly re-open this. What stays here is the PROPERTY the
        `/var/tmp` mutant violated and a literal cannot state on its own.
        """
        assert rs.DEFAULT_CACHE_ROOT.is_relative_to(Path.home())
        assert rs.DEFAULT_CACHE_ROOT != Path.home()

    def test_the_REMEDY_names_a_subcommand_cairn_ACTUALLY_HAS(self) -> None:
        """🔴 THE REMEDY IS A WIRE FACT: it is the one thing the refusal tells a
        human to TYPE.

        MEASURED: `REMEDY = "cairn resync-the-store"` SURVIVED all 668 tests,
        because every assertion was `rs.REMEDY in msg` — the message quoting the
        constant that produced it. Two SKILL.md files hardcode `cairn sync` in
        prose with nothing cross-checking, so the prose and the code could drift
        apart silently in either direction.

        Pinned three ways: the literal; that `scripts/cairn` really declares that
        subparser (so a renamed subcommand fails HERE rather than at a user's
        terminal); and that both prescribing skills say the same words.
        """
        assert rs.REMEDY == "cairn sync"
        prog, _, verb = rs.REMEDY.partition(" ")
        assert prog == "cairn"
        cairn = (ROOT / "scripts" / "cairn").read_text(encoding="utf-8")
        assert f'sub.add_parser("{verb}"' in cairn, (
            f"the refusal tells a human to run `{rs.REMEDY}`, but `scripts/cairn` "
            f"declares no `{verb}` subcommand."
        )
        for rel in (
            "claude/skills/resume/SKILL.md",
            "claude/skills/analyze-service/SKILL.md",
        ):
            doc = (ROOT / rel).read_text(encoding="utf-8")
            assert rs.REMEDY in doc, f"{rel} no longer prescribes `{rs.REMEDY}`"

    def test_the_stamp_FILENAME_is_pinned_to_the_literal_cairn_writes(
        self, tmp_path: Path
    ) -> None:
        """🔴 FOUND BY THE MUTATION SWEEP, AND IT IS A REAL GAP.

        Every fixture in this file writes the stamp through `rs.SYNC_STAMP`, so
        a mutant that RENAMES the constant renames the fixture with it and the
        suite stays green: MEASURED, `SYNC_STAMP = ".sync-stampX"` SURVIVED all
        615 tests in the three files that cover this change. That is the
        expectation-derived-from-the-implementation shape — the suite could not
        tell the two names apart because it never named either.

        The filename is a WIRE fact, not an internal choice. Caches already on
        disk were written by a deployed `cairn sync`; a reader looking for a
        different name reports every one of them as unstamped and refuses the
        lot. So it is pinned to the literal, and the behavioural half writes the
        literal by hand — never through the constant — so the pin cannot be
        satisfied by the constant agreeing with itself.
        """
        assert rs.SYNC_STAMP == ".sync-stamp"
        store = _store(tmp_path, stamped=False)
        (store / ".sync-stamp").write_text("synced=1\n", encoding="utf-8")
        got = rs.resolve_read_store(store)
        assert got.stamped is True
        assert got.stamp == ("synced=1",)

    def test_the_ONLY_normalisation_is_trailing_whitespace_and_blank_lines(
        self, tmp_path: Path
    ) -> None:
        """🔴 THE DOCSTRING SAID "VERBATIM" WHILE THE BODY `rstrip()`s.

        No test could see the difference — every fixture writes clean lines — so
        the word survived review. Rather than pick one and hope, this pins the
        normalisation itself: trailing whitespace goes (a `\\r` from a CRLF write
        must not reach the rendered header), blank lines go, and NOTHING else
        moves — order, LEADING whitespace, `=` signs, duplicate keys and unknown
        fields all survive untouched, because this module does not own the
        stamp's schema.
        """
        store = _store(tmp_path, stamped=False)
        (store / rs.SYNC_STAMP).write_text(
            "synced=1   \r\n"          # trailing spaces + CR
            "\n"                        # blank line
            "  indented=kept\n"         # LEADING whitespace survives
            "coverage=ALL\n"
            "coverage=DUPLICATE\n"      # duplicate key survives, in order
            "unknown-field=passed through\n",
            encoding="utf-8",
        )
        got = rs.resolve_read_store(store)
        assert got.stamp == (
            "synced=1",
            "  indented=kept",
            "coverage=ALL",
            "coverage=DUPLICATE",
            "unknown-field=passed through",
        )

    def test_the_refusal_names_the_store_the_reason_and_the_remedy(self, tmp_path: Path) -> None:
        """A refusal that does not say what to do next is a dead end.

        All three are asserted because a message with any one missing still
        reads like a complete sentence — which is how an unactionable error
        survives review.
        """
        store = _store(tmp_path, stamped=False)
        msg = rs.refusal_message("prog", rs.resolve_read_store(store))
        assert str(store) in msg
        assert rs.SYNC_STAMP in msg
        assert rs.REMEDY in msg
        assert msg.startswith("prog: ")

    def test_the_constants_have_exactly_one_definition(self) -> None:
        """🔴 `scripts/cairn` IMPORTS these; it must not re-declare them.

        The cache path and the stamp name lived in `cairn` alone, which is why
        the readers could not see them — a second copy in a reader would have
        been a second thing to keep in step, and the whole defect is the two
        sides disagreeing.

        🔴 THE ARM THIS REPLACES GUARDED A SPELLING THAT CAN NO LONGER EXIST.
        It matched the TEXT `'DEFAULT_CACHE = Path.home()'`, and the round that
        deleted the `DEFAULT_CACHE` name from `cairn` left the check behind
        matching nothing: MEASURED, re-declaring `DEFAULT_CACHE_ROOT =
        Path.home() / ".cache" / "subsystem-store"` at `cairn`'s module scope
        SURVIVED it. Reading as text also made the arm sensitive to whitespace
        and blind to prose containing the same characters, so it is now an AST
        walk, and the forbidden set is DERIVED from `subsystem_read_store`'s own
        `__all__` — a constant added there is covered here the same day, with no
        second list to keep in step. Parsed rather than imported because `cairn`
        has no `.py` suffix and importing it would run its argparse module scope.
        """
        path = ROOT / "scripts" / "cairn"
        src = path.read_text(encoding="utf-8")
        assert "from subsystem_read_store import" in src
        assigned = _assigned_anywhere(path)
        # POSITIVE CONTROL: the walk really saw `cairn`'s assignments, so an
        # empty intersection below means "no re-declaration", not "no parse".
        assert "EXIT_REFRESH_FAILED" in assigned, sorted(assigned)[:20]
        clash = assigned & set(rs.__all__)
        assert clash == set(), (
            f"`scripts/cairn` assigns {sorted(clash)}, which it must IMPORT from "
            f"`subsystem_read_store` — a second copy is a second thing to keep in "
            f"step, and the readers cannot see it."
        )
        # The pre-cutover name is gone and must not come back under its old
        # spelling either; that one is dead, so it is asserted as text.
        assert "DEFAULT_CACHE = " not in src


# =============================================================================
# The CLI: refuse the default when it cannot date itself.
# =============================================================================


class TestTheCliRefusesAnUnstampedDefaultStore:
    def test_an_unstamped_DEFAULT_store_is_refused_and_renders_no_index(
        self, tmp_path: Path, repointed, capsys
    ) -> None:
        """🔴 (a) THE HEADLINE CASE. Non-zero exit, and — the half that actually
        matters — NOTHING that could be mistaken for an index."""
        repointed(_store(tmp_path, stamped=False))
        rc_code = rc.main(["--scope", SCOPE])
        cap = capsys.readouterr()
        assert rc_code == rc.EXIT_UNSTAMPED_READ_STORE
        assert rc_code != 0
        # The refusal is unmistakable: it is on stderr and it names the remedy.
        assert "REFUSING" in cap.err
        assert rs.REMEDY in cap.err
        # 🔴 AND NOTHING RENDERED. A refusal that still printed the digest would
        # be an advisory, and an advisory is exactly what the frozen mirror
        # already had (none) — the caller must not be able to read an index here.
        assert cap.out.strip() == ""
        assert "subsystem-recall: status=" not in cap.out
        assert "collector" not in cap.out

    def test_an_unstamped_default_is_refused_for_search_too(
        self, tmp_path: Path, repointed, capsys
    ) -> None:
        """The guard sits before the search/recall fork, so BOTH read surfaces
        of this CLI are covered by one check rather than one each."""
        repointed(_store(tmp_path, stamped=False))
        assert rc.main(["--scope", SCOPE, "--search", "readiness"]) == (
            rc.EXIT_UNSTAMPED_READ_STORE
        )
        cap = capsys.readouterr()
        assert "REFUSING" in cap.err
        assert cap.out.strip() == ""

    def test_the_refusal_exit_code_is_NOT_the_store_broken_code(
        self, tmp_path: Path, repointed, capsys
    ) -> None:
        """🔴 THE MUTATION-TEST IDENTITY OF THIS GUARD.

        3 already means "nothing readable is there, recall was unavailable".
        This is a different fact with a different one-command remedy, and a
        caller that cannot tell them apart cannot act on it. Asserted so a
        mutant that returns 3 (or 2, or 0) here dies on THIS guard's own code
        rather than passing for somebody else's reason.
        """
        repointed(_store(tmp_path, stamped=False))
        assert rc.main(["--scope", SCOPE]) == 4
        capsys.readouterr()
        assert rc.EXIT_UNSTAMPED_READ_STORE not in (0, 2, 3)

    def test_the_guard_is_REACHABLE_no_earlier_check_wins(
        self, tmp_path: Path, repointed, capsys
    ) -> None:
        """🔴 REACHABILITY, not just breakability.

        Every check ahead of this one rejects a malformed COMMAND (two selectors,
        `--limit` with `--list`, `--page` with `--ref`, a search-only flag with
        no `--search`). This input trips none of them: it is a well-formed
        command whose ONLY problem is the store it would read. If an earlier
        check ever grew wide enough to swallow it, the exit code here would stop
        being 4 and this test says so.
        """
        repointed(_store(tmp_path, stamped=False))
        # A flag combination the parser accepts outright.
        assert rc.main(["--scope", SCOPE, "--list"]) == rc.EXIT_UNSTAMPED_READ_STORE
        cap = capsys.readouterr()
        assert "REFUSING" in cap.err
        # The proof that this argv is otherwise-VALID — and not merely rejected
        # by something else — is the next test: same argv, stamped store, exit 0.

    def test_the_same_command_succeeds_once_the_store_is_stamped(
        self, tmp_path: Path, repointed, capsys
    ) -> None:
        """The positive half of the reachability pair above: same argv, same
        store contents, stamp present ⇒ exit 0 and a real index."""
        repointed(_store(tmp_path, stamped=True))
        assert rc.main(["--scope", SCOPE, "--list"]) == 0
        out = capsys.readouterr().out
        assert "collector" in out

    def test_a_flag_error_still_beats_the_store_guard(
        self, tmp_path: Path, repointed, capsys
    ) -> None:
        """Guard ORDER, pinned. A caller who passed two selectors gets told
        about the selectors — being additionally told to run `cairn sync` would
        describe a store the command was never going to read."""
        repointed(_store(tmp_path, stamped=False))
        assert rc.main(["--scope", SCOPE, "--list", "--ref", "collector"]) == 2
        cap = capsys.readouterr()
        assert "select different things" in cap.err
        assert "REFUSING" not in cap.err


class TestAStampedDefaultStoreCarriesItsFreshness:
    def test_the_header_carries_every_stamp_line_UNPARSED(
        self, tmp_path: Path, repointed, capsys
    ) -> None:
        """🔴 (b) UNPARSED, and in the HEADER — beside `store:` and `store host:`.
        (This said "VERBATIM" while the reader `rstrip()`s each line; the word
        was retired in the module and left standing here.)

        Every line is asserted, not a sampled one: a render that dropped
        `coverage=ALL` would let a scope-filtered cache read as complete, and a
        render that dropped `synced=` would take the freshness back out of a
        read that exists to carry it.
        """
        repointed(_store(tmp_path, stamped=True))
        assert rc.main(["--scope", SCOPE]) == 0
        out = capsys.readouterr().out
        for line in STAMP_LINES:
            assert f"  stamp: {line}" in out, line
        head = out.splitlines()
        store_at = next(i for i, ln in enumerate(head) if ln.startswith("  store: "))
        caveat_at = next(i for i, ln in enumerate(head) if ln.startswith("  caveat: "))
        stamp_at = [i for i, ln in enumerate(head) if ln.startswith("  stamp: ")]
        assert stamp_at, out
        assert store_at < min(stamp_at) and max(stamp_at) < caveat_at, out

    def test_NO_AGE_IS_COMPUTED(self, tmp_path: Path, repointed, capsys) -> None:
        """🔴 `subsystem_recall` documents itself "no clock", and `cairn` owns
        `cache_age`. A second age here would be a second answer to one question.

        Asserted STRUCTURALLY — the reader's module imports no clock — rather
        than by grepping the output for a duration, because a duration string
        is a word another feature could spell.
        """
        src = (ROOT / "scripts" / "lib" / "subsystem_recall.py").read_text(encoding="utf-8")
        assert "\nimport time" not in src and "\nfrom time import" not in src
        assert "\nimport datetime" not in src and "\nfrom datetime import" not in src
        rd = (ROOT / "scripts" / "lib" / "subsystem_read_store.py").read_text(encoding="utf-8")
        assert "\nimport time" not in rd and "\nfrom time import" not in rd
        # And behaviourally: the stamp's own epoch is echoed, never converted.
        repointed(_store(tmp_path, stamped=True))
        assert rc.main(["--scope", SCOPE]) == 0
        out = capsys.readouterr().out
        assert "  stamp: synced=1788363567" in out

    def test_the_search_header_carries_the_stamp_too(
        self, tmp_path: Path, repointed, capsys
    ) -> None:
        repointed(_store(tmp_path, stamped=True))
        assert rc.main(["--scope", SCOPE, "--search", "readiness"]) == 0
        out = capsys.readouterr().out
        for line in STAMP_LINES:
            assert f"  stamp: {line}" in out, line

    def test_the_json_payload_carries_the_stamp_UNPARSED(
        self, tmp_path: Path, repointed, capsys
    ) -> None:
        """A `--json` consumer has no header block, so without this it is the
        one reader that gets no freshness — in the format least likely to be
        eyeballed."""
        repointed(_store(tmp_path, stamped=True))
        assert rc.main(["--scope", SCOPE, "--json"]) == 0
        blob = json.loads(capsys.readouterr().out)
        assert blob["read_store_stamp"] == list(STAMP_LINES)


class TestAnExplicitStoreStaysPermissive:
    def test_an_explicit_store_at_an_unstamped_path_still_serves(
        self, tmp_path: Path, repointed, capsys
    ) -> None:
        """🔴 (c) THE OPERATOR NAMED A PATH. Every fixture in this repo does
        this, `prune-index` prescribes it, and the store-api's `/data` is
        exactly it — the refusal is about the DEFAULT resolution only."""
        # The default is repointed at an unstamped store as well, so a pass here
        # cannot come from the default happening to be fine.
        repointed(_store(tmp_path / "default", stamped=False))
        elsewhere = _store(tmp_path / "named", stamped=False)
        assert rc.main(["--store", str(elsewhere), "--scope", SCOPE]) == 0
        cap = capsys.readouterr()
        assert "collector" in cap.out
        assert "REFUSING" not in cap.err

    def test_an_explicit_store_prints_no_stamp_lines_when_it_has_none(
        self, tmp_path: Path, repointed, capsys
    ) -> None:
        """Permissive is not the same as silent-about-freshness: with no stamp
        there is simply nothing to print, and nothing is invented."""
        repointed(_store(tmp_path / "default", stamped=False))
        elsewhere = _store(tmp_path / "named", stamped=False)
        assert rc.main(["--store", str(elsewhere), "--scope", SCOPE]) == 0
        assert "  stamp: " not in capsys.readouterr().out

    def test_an_explicit_STAMPED_store_still_prints_its_stamp(
        self, tmp_path: Path, repointed, capsys
    ) -> None:
        repointed(_store(tmp_path / "default", stamped=False))
        elsewhere = _store(tmp_path / "named", stamped=True)
        assert rc.main(["--store", str(elsewhere), "--scope", SCOPE]) == 0
        assert "  stamp: coverage=ALL" in capsys.readouterr().out


# =============================================================================
# The pod's contract. Breaking this is the catastrophic failure mode.
# =============================================================================


class TestThePodContractIsUnchanged:
    """🔴 THE LIBRARY SERVES AN UNSTAMPED STORE, FOREVER.

    `subsystem-store-api/server.py` calls `rc.recall` / `rc.search` /
    `rc.load_store` against `/data` — a directory with no `.sync-stamp`, because
    the pod is what the stamp is ABOUT. `scripts/cairn` calls the same functions
    against a cache that is unstamped on a host that has never synced. A refusal
    in any of them takes the whole store offline; these pin that it cannot
    happen by accident.
    """

    def test_recall_serves_an_unstamped_store(self, tmp_path: Path) -> None:
        store = _store(tmp_path, stamped=False)
        rep = rc.recall(str(store), SCOPE)
        assert rep.status == "recalled"
        assert [e.ref for e in rep.entries] == ["collector"]

    def test_search_serves_an_unstamped_store(self, tmp_path: Path) -> None:
        store = _store(tmp_path, stamped=False)
        rep = rc.search(str(store), SCOPE, "readiness")
        assert rep.status not in ("search-unreadable",)

    def test_load_store_serves_an_unstamped_store(self, tmp_path: Path) -> None:
        store = _store(tmp_path, stamped=False)
        _root, index = rc.load_store(str(store), verb="recalled")
        assert index is not None

    def test_render_text_with_no_extra_header_is_byte_identical(
        self, tmp_path: Path
    ) -> None:
        """The pod calls `render_text(report)` positionally with no keyword.
        The stamp hook must be inert for it — not merely harmless."""
        store = _store(tmp_path, stamped=False)
        rep = rc.recall(str(store), SCOPE)
        assert rc.render_text(rep) == rc.render_text(rep, extra_header=())
        assert "  stamp: " not in rc.render_text(rep)

    #: 🔴 THE THREE CLI-ONLY DEFINITIONS, ENUMERATED. Everything else in
    #: `subsystem_recall.py` is library surface some caller may reach without an
    #: argv, so none of it may consult the read-store resolver.
    CLI_ONLY = frozenset({"_build_parser", "_with_stamp", "main"})

    def test_only_the_enumerated_CLI_functions_consult_the_resolver(self) -> None:
        """🔴 A POSITIONAL GUARD MISSED A FUNCTION THE POD CALLS EVERY REQUEST.

        This test used to split the source at `def _build_parser(` and scan only
        ABOVE it, while its docstring claimed that proved "the whole library half
        cannot grow a refusal". It did not: `_exit_for` is defined ~160 lines
        BELOW that marker, and `server.py` calls `rc._exit_for(...)` inside
        `_serve_report` on every `/recall` and `/search`, as does `scripts/cairn`
        on every read. MEASURED: a refusal grown inside `_exit_for` SURVIVED the
        old guard in both shapes (`return 4` and `raise`).

        Moving the marker down would not fix it either — `_build_parser`,
        `_StoreAction` and the exit-code constant all sit between the two and
        legitimately name the resolver.

        So the guard is no longer POSITIONAL. It enumerates, by AST, every
        top-level definition whose source mentions the resolver and asserts that
        set EQUALS `CLI_ONLY`. Pinned both ways: a library function that grows a
        reference fails, and so does removing a name from the allowlist while the
        reference remains. Source text rather than attribute nodes, so a string
        annotation (`_with_stamp`'s) counts too; comment lines are excluded so
        prose about the resolver stays free.
        """
        src = (ROOT / "scripts" / "lib" / "subsystem_recall.py").read_text(encoding="utf-8")
        referencing = set()
        for node in ast.parse(src).body:
            segment = ast.get_source_segment(src, node) or ""
            body = "\n".join(
                ln for ln in segment.splitlines() if not ln.lstrip().startswith("#")
            )
            if "_read_store." in body:
                referencing.add(getattr(node, "name", None) or type(node).__name__)
        assert referencing == set(self.CLI_ONLY), (
            f"definitions consulting the read-store resolver changed.\n"
            f"  expected (CLI only): {sorted(self.CLI_ONLY)}\n"
            f"  found:               {sorted(referencing)}\n"
            f"Anything not on that list is library surface the POD and `cairn` "
            f"reach without an argv — `_exit_for` is called on every pod request."
        )

    def test_exit_for_is_named_by_the_pod_and_is_NOT_in_the_cli_allowlist(self) -> None:
        """The premise of the test above, pinned rather than recalled.

        If `server.py` stops calling `_exit_for`, or `_exit_for` is added to
        `CLI_ONLY`, the guard above silently stops covering the function that
        motivated it — and nothing else would say so.
        """
        server = (ROOT / "scripts" / "subsystem-store-api" / "server.py").read_text(
            encoding="utf-8"
        )
        cairn = (ROOT / "scripts" / "cairn").read_text(encoding="utf-8")
        assert "rc._exit_for(" in server
        assert "rc._exit_for(" in cairn
        assert "_exit_for" not in self.CLI_ONLY

    def test_cairns_exit_4_is_SYNC_ONLY_and_is_not_the_readers_refusal(self) -> None:
        """🔴 TWO TOOLS, ONE NUMBER, OPPOSITE REMEDIES — pinned from the code.

        The reader's 4 means "this host has not fetched the store; run
        `cairn sync`". `cairn`'s 4 is `EXIT_REFRESH_FAILED` and means "the store
        was NOT reached but a usable cache survived" — where re-running
        `cairn sync` is precisely the command that just failed. `/resume` step 4
        told the reader to do that, because the two numbers collide and prose
        cannot see a collision.

        Asserted structurally: the numbers really are equal (so the hazard is
        real and not imagined), and `EXIT_REFRESH_FAILED` is returned from
        `cmd_sync` and nowhere else — which is what makes "`cairn recall` never
        returns 4" true rather than remembered.
        """
        src = (ROOT / "scripts" / "cairn").read_text(encoding="utf-8")
        assert "EXIT_REFRESH_FAILED = 4" in src
        assert rc.EXIT_UNSTAMPED_READ_STORE == 4, "the collision is the premise"
        returning = set()
        for node in ast.parse(src).body:
            # The constant's own `EXIT_REFRESH_FAILED = 4` is a module-level
            # Assign, not a USE — only definitions can return it.
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            segment = ast.get_source_segment(src, node) or ""
            body = "\n".join(
                ln for ln in segment.splitlines() if not ln.lstrip().startswith("#")
            )
            if "EXIT_REFRESH_FAILED" in body:
                returning.add(node.name)
        assert returning == {"cmd_sync"}, (
            f"`EXIT_REFRESH_FAILED` (4) escaped `cmd_sync`: {sorted(returning)}. "
            f"`/resume` step 4 tells the reader that a 4 from `cairn` is NOT the "
            f"reader's unstamped-store refusal — that sentence is only true while "
            f"this set is exactly {{'cmd_sync'}}."
        )

    def test_exit_for_serves_an_unstamped_default_store(self, tmp_path: Path, repointed) -> None:
        """🔴 THE BEHAVIOURAL BACKSTOP, MADE HOST-INDEPENDENT.

        `_exit_for` is what the pod calls per request. Without repointing, this
        host's real cache IS stamped, so a refusal grown here would never execute
        and every store-api test would pass — the mutant would be scored SURVIVED
        for want of a reachable input, not for want of a defect. So the default
        resolution is forced somewhere with no stamp first, and BOTH verdicts are
        exercised: a served status (0) and an unreadable one (3).
        """
        repointed(tmp_path / "never-synced")  # does not exist: unstamped by any reading
        assert rc._exit_for("recalled", "devrc/", []) == 0
        assert rc._exit_for("scope-unreadable", "devrc/", []) == 3

    def test_the_pod_and_cairn_use_the_library_not_the_cli(self) -> None:
        """The claim this whole exemption rests on, pinned rather than recalled.

        Both import `subsystem_recall` and call its FUNCTIONS; neither shells the
        CLI nor calls `main`. If one ever switches to the CLI it would inherit
        the refusal, and this test is where that gets noticed.
        """
        for rel in ("scripts/subsystem-store-api/server.py", "scripts/cairn"):
            src = (ROOT / rel).read_text(encoding="utf-8")
            assert "import subsystem_recall as rc" in src, rel
            assert "rc.main(" not in src, rel
            assert "_build_parser(" not in src, rel
        server = (ROOT / "scripts" / "subsystem-store-api" / "server.py").read_text(
            encoding="utf-8"
        )
        assert "rc.recall(" in server and "rc.load_store(" in server
        cairn = (ROOT / "scripts" / "cairn").read_text(encoding="utf-8")
        assert "rc.recall(" in cairn and "rc.search(" in cairn


# =============================================================================
# service_recon: degrade ONE section, never the brief.
# =============================================================================


class TestServiceReconDegradesGracefully:
    def test_an_unstamped_default_degrades_only_the_index_section(
        self, tmp_path: Path, repointed
    ) -> None:
        """🔴 (e) RECON HAS FOUR OTHER SECTIONS. Aborting the whole brief over
        an unreadable index would trade a working recon for a defect the brief
        can simply state."""
        unstamped = _store(tmp_path / "a", stamped=False)
        repointed(unstamped)
        brief = srec.recon("collector", repos=[str(tmp_path)], cwd=tmp_path)
        assert brief.index.status == srec.INDEX_UNSTAMPED
        text = srec.render_brief(brief)
        assert f"index: {srec.INDEX_UNSTAMPED}" in text
        assert "the index could not be read:" in text
        assert rs.REMEDY in text

        # 🔴 "The rest of the brief still ran" is asserted by COMPARISON, not by
        # spotting a section header: every non-index section is byte-identical to
        # the same recon against an explicitly-named store. A header check would
        # pass over a section that ran and produced nothing.
        named = srec.render_brief(
            srec.recon(
                "collector", repos=[str(tmp_path)], store_root=unstamped, cwd=tmp_path
            )
        )

        def _sections(t: str) -> list[list[str]]:
            blocks, cur = [], []
            for line in t.splitlines():
                if line == "":
                    blocks.append(cur)
                    cur = []
                else:
                    cur.append(line)
            blocks.append(cur)
            return blocks

        degraded_blocks = _sections(text)
        named_blocks = _sections(named)
        assert len(degraded_blocks) == len(named_blocks), text
        differing = [
            i for i, (x, y) in enumerate(zip(degraded_blocks, named_blocks)) if x != y
        ]
        assert len(differing) == 1, (
            f"exactly ONE block may differ (the index); blocks {differing} did"
        )
        assert degraded_blocks[differing[0]][0].startswith("index: "), text

    def test_the_degraded_section_is_NOT_an_empty_index(
        self, tmp_path: Path, repointed
    ) -> None:
        """🔴 "Nothing recorded" and "nothing was read" must not render alike —
        that equivalence is the silent zero this whole change exists to kill."""
        repointed(_store(tmp_path, stamped=False))
        text = srec.render_brief(
            srec.recon("collector", repos=[str(tmp_path)], cwd=tmp_path)
        )
        assert "nothing recorded under that ref yet" not in text
        assert "Nothing was checked" in text

    def test_an_explicit_store_root_still_reads_an_unstamped_store(
        self, tmp_path: Path, repointed
    ) -> None:
        """recon's `--store`/`store_root=` is the operator naming a path, same
        as the reader's. Every existing test in `test_service_recon.py` is this
        case, and all of them must keep working."""
        repointed(_store(tmp_path / "default", stamped=False))
        named = _store(tmp_path / "named", stamped=False)
        brief = srec.recon(
            "collector", repos=[str(tmp_path)], store_root=named, cwd=tmp_path
        )
        assert brief.index.status != srec.INDEX_UNSTAMPED

    def test_a_stamped_default_is_read_normally(self, tmp_path: Path, repointed) -> None:
        repointed(_store(tmp_path, stamped=True))
        brief = srec.recon("collector", repos=[str(tmp_path)], cwd=tmp_path)
        assert brief.index.status != srec.INDEX_UNSTAMPED

    def test_the_brief_reports_the_RESOLVED_store_not_the_argument(
        self, tmp_path: Path, repointed
    ) -> None:
        """🔴 THE FIELD THAT ANSWERS "WHICH STORE DID THIS READ?".

        It read the literal string `"None"` for one commit: `recon` stringified
        its own `store_root=None` sentinel straight into the `Brief`. No test
        could see it — all ~40 `sr.recon(...)` calls in `test_service_recon.py`
        pass `store_root=` explicitly, so the default path never reached this
        field, and nothing anywhere asserted `brief_json()["store_root"]`. A
        suite blind to a dimension passes every defect on it.

        All three resolutions are asserted here, because the stamped case alone
        would have gone green over the `"None"` bug for the explicit path while
        the default stayed broken.
        """
        stamped = _store(tmp_path / "cache", stamped=True)
        repointed(stamped)
        brief = srec.recon("collector", repos=[str(tmp_path)], cwd=tmp_path)
        assert srec.brief_json(brief)["store_root"] == str(stamped)
        assert "None" not in srec.brief_json(brief)["store_root"]

        named = _store(tmp_path / "named", stamped=False)
        brief = srec.recon(
            "collector", repos=[str(tmp_path)], store_root=named, cwd=tmp_path
        )
        assert srec.brief_json(brief)["store_root"] == str(named)

    def test_a_refused_default_says_NOT_READ_rather_than_naming_a_path(
        self, tmp_path: Path, repointed
    ) -> None:
        """🔴 A BARE PATH IN THIS FIELD IS A CLAIM THAT THE BRIEF READ IT.

        On a refusal the store was resolved and then deliberately not opened, so
        emitting the directory alone would tell a consumer the index came from
        it. The prefix makes the difference machine-visible; the path stays so a
        human can see WHICH store needs syncing.
        """
        unstamped = _store(tmp_path / "cache", stamped=False)
        repointed(unstamped)
        blob = srec.brief_json(srec.recon("collector", repos=[str(tmp_path)], cwd=tmp_path))
        assert blob["store_root"].startswith(srec.NOT_READ_PREFIX)
        assert srec.INDEX_UNSTAMPED in blob["store_root"]
        assert str(unstamped) in blob["store_root"]
        assert blob["store_root"] != str(unstamped)
        assert blob["index"]["status"] == srec.INDEX_UNSTAMPED

    def test_read_index_called_DIRECTLY_refuses_an_unstamped_default(
        self, tmp_path: Path, repointed
    ) -> None:
        """🔴 A GUARD MY OWN FIX UNCOVERED, CAUGHT BY RE-RUNNING THE SWEEP.

        `read_index`'s refusal branch was covered while `recon` delegated the
        resolution to it. Fixing F1 moved that resolution into `recon` — correct,
        because only `recon` can report which store the brief read — and the
        branch instantly became reachable only by a DIRECT caller, which no test
        was. MEASURED: deleting both lines then SURVIVED all 624 tests, having
        been KILLED by four of them one commit earlier.

        `read_index` is exported, so this is live surface. Both accepted shapes
        of `loc` are exercised, because the refusal sits before the branch that
        tells them apart and a test of one would not prove the other.
        """
        repointed(_store(tmp_path / "cache", stamped=False))
        for loc in (str(tmp_path), srec.locate("collector", ((str(tmp_path), "test"),))):
            got = srec.read_index(loc, "collector")
            assert got.status == srec.INDEX_UNSTAMPED, loc
            assert rs.REMEDY in (got.detail or "")

    def test_read_index_one_called_DIRECTLY_refuses_an_unstamped_default(
        self, tmp_path: Path, repointed
    ) -> None:
        """The same uncovering, one level down: `read_index`'s loop always passes
        a resolved root, so `_read_index_one`'s own guard has no in-tree caller
        that can reach it either."""
        repointed(_store(tmp_path / "cache", stamped=False))
        got = srec._read_index_one(str(tmp_path), "collector")
        assert got.status == srec.INDEX_UNSTAMPED
        assert rs.REMEDY in (got.detail or "")

    def test_read_index_called_directly_with_an_explicit_store_still_reads(
        self, tmp_path: Path, repointed
    ) -> None:
        """The permissive half of the pair above — otherwise the two tests would
        pass equally well over a `read_index` that refused everything."""
        repointed(_store(tmp_path / "cache", stamped=False))
        named = _store(tmp_path / "named", stamped=False)
        got = srec.read_index(str(tmp_path), "collector", store_root=named)
        assert got.status != srec.INDEX_UNSTAMPED

    def test_the_cli_store_default_is_None_so_it_resolves(self) -> None:
        """`None` IS the "resolve, and refuse an undateable store" case. A
        literal here would be a second copy of a path the resolver owns — and
        the literal it used to hold was the FROZEN mirror."""
        assert srec._build_parser().get_default("store") is None


# =============================================================================
# The recon DATES the store it serves. The refusal never covered staleness.
# =============================================================================


class TestTheBriefSaysHowFreshItsIndexIs:
    """🔴 THE REFUSAL FIRES ON UNDATEABLE, NOT ON STALE — AND `;` EXPOSED THAT.

    Changing step 1 from `cairn sync && …` to `cairn sync; …` was right: under
    `&&` a sync failing with exit 4 (pod down, usable cache) deleted the whole
    brief. But it turned a loud nothing into a quiet something: pod down three
    days, cache stamped three days ago, `cairn sync` printing its age warning on
    STDERR, `;` letting the recon run — and a full brief whose `index:` block was
    three days old with NO freshness field anywhere in the text output. That is
    a store that cannot say how fresh it is serving as if current, which is this
    change's own thesis one layer up.

    So the brief renders the stamp. These pin that it does, that it invents one
    when there is none, that the two renderers spell it the same way, and that
    the skill prose says what the code does — the prose asserted the opposite for
    one round ("REFUSES an undateable store rather than serving a stale one",
    "served the index block at full fidelity").
    """

    def _brief_text(self, tmp_path: Path, *, stamped: bool) -> str:
        return srec.render_brief(
            srec.recon("collector", repos=[str(tmp_path)], cwd=tmp_path)
        )

    def test_a_stamped_default_prints_EVERY_stamp_line_INSIDE_the_index_block(
        self, tmp_path: Path, repointed
    ) -> None:
        """Every line, and POSITIONED — a freshness fact printed under `config:`
        answers a question nobody asked there.

        The block is delimited the way `render_brief` delimits every section: a
        blank line. So the assertion is "between the `index:` line and the next
        blank line", which a stamp appended to the end of the brief would fail.
        """
        repointed(_store(tmp_path, stamped=True))
        text = self._brief_text(tmp_path, stamped=True)
        lines = text.splitlines()
        index_at = next(i for i, ln in enumerate(lines) if ln.startswith("index: "))
        end = next(
            (i for i in range(index_at + 1, len(lines)) if lines[i] == ""), len(lines)
        )
        block = lines[index_at:end]
        for i, line in enumerate(STAMP_LINES):
            assert block[1 + i] == f"  stamp: {line}", block
        # …and the store really was SERVED, so this is a date beside a read and
        # not beside a refusal that happens to print one.
        assert srec.INDEX_UNSTAMPED not in block[0], block

    def test_the_stamp_precedes_the_ENTRY_BODY_on_a_hit(
        self, tmp_path: Path, repointed
    ) -> None:
        """🔴 THE BRANCH WITH SUB-LINES, WHICH IS THE ONE A READER ACTUALLY SEES.

        On a `hit` the `index:` header is followed by `## What it is`, pointers
        and nuance. A stamp appended after those would date a paragraph the
        reader has already believed. Rendered from a `hit` `IndexResult` swapped
        into a real brief, because reaching a hit end-to-end needs a git repo
        whose name is the scope — a heavier fixture that would test the locate
        step, not this one.
        """
        repointed(_store(tmp_path, stamped=True))
        brief = srec.recon("collector", repos=[str(tmp_path)], cwd=tmp_path)
        hit = dataclasses.replace(
            brief,
            index=srec.IndexResult(
                "hit", scope=SCOPE, ref="collector",
                what="A durable thing a recall block MUST name.",
                pointers="- ops skill `manage-widget`",
                nuance="- the readiness probe lies for 40s.",
                basis=srec.OWNER_BASIS,
            ),
        )
        lines = srec.render_brief(hit).splitlines()
        index_at = next(i for i, ln in enumerate(lines) if ln.startswith("index: "))
        assert "HIT (from index)" in lines[index_at]
        for i, line in enumerate(STAMP_LINES):
            assert lines[index_at + 1 + i] == f"{rs.STAMP_PREFIX}{line}", lines
        what_at = next(i for i, ln in enumerate(lines) if "What it is" in ln)
        assert index_at + len(STAMP_LINES) < what_at, lines

    def test_the_stamp_prefix_is_the_ONE_spelling_both_renderers_use(
        self, tmp_path: Path, repointed, capsys
    ) -> None:
        """🔴 TWO RENDERERS, ONE TOKEN. `subsystem_recall`'s CLI header and the
        recon's `index:` block both print the stamp, and the skill tells a reader
        to relay "the `stamp:` lines" — one token, or the instruction is true of
        one output and false of the other. Asserted by comparing the two RENDERED
        outputs, not by grepping both sources for the same f-string.
        """
        store = _store(tmp_path, stamped=True)
        repointed(store)
        assert rc.main(["--scope", SCOPE]) == 0
        cli_lines = {
            ln for ln in capsys.readouterr().out.splitlines() if ln.startswith("  stamp:")
        }
        brief_lines = {
            ln for ln in self._brief_text(tmp_path, stamped=True).splitlines()
            if ln.startswith("  stamp:")
        }
        assert cli_lines == brief_lines != set()
        assert cli_lines == {f"{rs.STAMP_PREFIX}{ln}" for ln in STAMP_LINES}

    def test_the_brief_json_carries_the_stamp_UNPARSED(
        self, tmp_path: Path, repointed
    ) -> None:
        """A `--json` consumer reads no header. Same key as `subsystem_recall
        --json` (`read_store_stamp`), because two names for one fact is how a
        consumer ends up reading freshness from one tool and not the other."""
        repointed(_store(tmp_path, stamped=True))
        blob = srec.brief_json(
            srec.recon("collector", repos=[str(tmp_path)], cwd=tmp_path)
        )
        assert blob["read_store_stamp"] == list(STAMP_LINES)

    def test_a_REFUSED_default_invents_no_stamp(
        self, tmp_path: Path, repointed
    ) -> None:
        """Nothing is invented, and the absence is not silence: the refusal text
        is what carries the reason."""
        repointed(_store(tmp_path, stamped=False))
        brief = srec.recon("collector", repos=[str(tmp_path)], cwd=tmp_path)
        assert brief.store_stamp == ()
        text = srec.render_brief(brief)
        assert rs.STAMP_PREFIX not in text
        assert srec.INDEX_UNSTAMPED in text

    def test_an_explicitly_named_store_is_dated_when_it_can_be_and_not_when_it_cannot(
        self, tmp_path: Path, repointed
    ) -> None:
        """🔴 BOTH HALVES, because either alone passes over a renderer that
        always prints or never does.

        `--store <path>` is exempt from the REFUSAL, not from the date: a
        snapshot an operator names is as capable of being three days old as the
        default cache is.
        """
        repointed(_store(tmp_path / "default", stamped=False))
        named_stamped = _store(tmp_path / "fresh", stamped=True)
        named_bare = _store(tmp_path / "bare", stamped=False)

        dated = srec.render_brief(
            srec.recon("collector", repos=[str(tmp_path)],
                       store_root=named_stamped, cwd=tmp_path)
        )
        for line in STAMP_LINES:
            assert f"{rs.STAMP_PREFIX}{line}" in dated, line

        undated = srec.render_brief(
            srec.recon("collector", repos=[str(tmp_path)],
                       store_root=named_bare, cwd=tmp_path)
        )
        assert rs.STAMP_PREFIX not in undated
        # The undated one still SERVED — the missing stamp is a missing date,
        # never a refusal.
        assert srec.INDEX_UNSTAMPED not in undated

    #: 🔴 THE WHOLE PARAGRAPH, NORMALISED — not a keyword.
    #:
    #: The artifact under test is PROSE, and a guard on words is walkable by
    #: rewording: this file's prose has now made a false freshness claim twice
    #: (`&&` "costs you nothing", then "REFUSES … rather than serving a stale
    #: one"), each time surviving because the tests only matched keywords that
    #: stayed true. Pinning the normalised paragraph makes a reword go red, which
    #: is the price of a machine-readable claim — re-read the code, then paste
    #: the new text here.
    FRESHNESS_PARAGRAPH = (
        "🔴 **So READ THE `stamp:` LINES under `index:` — they are the only thing in "
        "the brief that says how old it is.** A cache stamped three days ago is served "
        "in full, and `HIT (from index)` reads exactly the same whether the sync ran a "
        "second ago or failed all week: the age `cairn sync` computes goes to "
        "**stderr**, which the brief does not carry. The stamp lines (`synced=`, "
        "`revision=`, `snapshot=`, `entries=`, `coverage=`) sit directly under the "
        "`index:` line, unparsed, with no age computed here. **Relay them, or state "
        "the sync's outcome, whenever you present index content.** Absent stamp lines "
        "mean the store carried no stamp, never that it is fresh."
    )

    #: Claims the prose made that the code does not keep. Asserted ABSENT as
    #: well as the paragraph above being present, because a reword could
    #: reintroduce one somewhere else in the file.
    RETIRED_CLAIMS = (
        "REFUSES an undateable store rather than serving a stale one",
        "served the index block at full fidelity",
        "would have served the index block at full",
    )

    def test_the_skill_prose_says_what_the_recon_ACTUALLY_does(self) -> None:
        doc = (ROOT / "claude/skills/analyze-service/SKILL.md").read_text(encoding="utf-8")
        paragraphs = [
            " ".join(p.split()) for p in doc.split("\n\n")
        ]
        assert " ".join(self.FRESHNESS_PARAGRAPH.split()) in paragraphs, (
            "the freshness paragraph in `analyze-service/SKILL.md` no longer "
            "matches the one pinned here. Re-read `render_brief`'s index block "
            "and `_resolve_store`, then paste the corrected paragraph into "
            "`FRESHNESS_PARAGRAPH` — do not delete this pin."
        )
        for claim in self.RETIRED_CLAIMS:
            assert claim not in doc, f"retired claim is back: {claim!r}"

    def test_the_prose_token_is_the_one_the_code_emits(self) -> None:
        """The paragraph tells the reader to look for `stamp:` lines under
        `index:`. Derived from the constant, so renaming the token fails HERE
        rather than at a reader wondering where the date went."""
        doc = (ROOT / "claude/skills/analyze-service/SKILL.md").read_text(encoding="utf-8")
        assert f"`{rs.STAMP_PREFIX.strip()}` LINES under `index:`" in doc
