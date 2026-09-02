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
# be a different artifact and would not exercise the verbatim render.
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
# The resolver itself.
# =============================================================================


class TestTheResolver:
    def test_a_stamped_store_reports_its_stamp_verbatim(self, tmp_path: Path) -> None:
        store = _store(tmp_path, stamped=True)
        got = rs.resolve_read_store(store)
        assert got.stamped is True
        assert got.reason is None
        assert got.stamp == STAMP_LINES  # verbatim, unparsed, in file order

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
        sides disagreeing. Read as TEXT because `cairn` has no `.py` suffix and
        an import of it here would run its argparse module scope.
        """
        src = (ROOT / "scripts" / "cairn").read_text(encoding="utf-8")
        assert "from subsystem_read_store import" in src
        for spelling in ('DEFAULT_CACHE = Path.home()', 'SYNC_STAMP = "'):
            assert spelling not in src, f"cairn re-declares {spelling!r}"


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
    def test_the_header_carries_every_stamp_line_verbatim(
        self, tmp_path: Path, repointed, capsys
    ) -> None:
        """🔴 (b) VERBATIM, and in the HEADER — beside `store:` and `store host:`.

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

    def test_the_json_payload_carries_the_stamp_verbatim(
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

    def test_the_library_functions_never_consult_the_resolver(self) -> None:
        """🔴 SPELLED-GUARD DEFENCE: asserted on the SOURCE REGION, not on one
        call's behaviour. A behavioural test proves the current inputs are fine;
        this proves the whole library half of the file cannot grow a refusal —
        the only reference to the resolver lives at or after `_build_parser`.
        """
        src = (ROOT / "scripts" / "lib" / "subsystem_recall.py").read_text(encoding="utf-8")
        marker = "def _build_parser("
        assert marker in src
        library_half = src[: src.index(marker)]
        # The module-scope import is the sole permitted mention above the CLI.
        mentions = [
            ln for ln in library_half.splitlines()
            if "_read_store." in ln and not ln.lstrip().startswith("#")
        ]
        assert mentions == [], f"the LIBRARY half consults the read-store resolver: {mentions}"

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

    def test_the_cli_store_default_is_None_so_it_resolves(self) -> None:
        """`None` IS the "resolve, and refuse an undateable store" case. A
        literal here would be a second copy of a path the resolver owns — and
        the literal it used to hold was the FROZEN mirror."""
        assert srec._build_parser().get_default("store") is None
