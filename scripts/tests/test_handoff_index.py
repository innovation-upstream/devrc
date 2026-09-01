"""Tests for scripts/lib/handoff_index.py + handoff_search.py — the P1 index.

🔴 EVERY TEST HERE IS HERMETIC. The authoritative gate is
`nix build .#checks.x86_64-linux.pytests`, a sandbox with NO cluster, NO
kubeconfig and NO Postgres. So the derivation is pure functions over text, the
git half builds real repositories in `tmp_path`, and the database sits behind
`handoff_index.SectionStore` — a protocol with two real implementations, of which
the memory one is production code (`handoff_search.py --offline`), not a double.

⚠ WHAT THIS FILE THEREFORE CANNOT SEE, stated rather than implied: nothing here
executes `PostgresSectionStore` against a database. Its DDL is never accepted by a
server, its generated `tsv` column is never computed, and `ts_rank` never orders
anything. What IS pinned is the SQL text it builds, that the boost table it shares
with the memory backend is the ONE place those numbers live, and every caller path
above it. A green run here is not evidence that the indexed path works.

🔴 REAL GIT, NOT A MOCK, for the ref-sourcing half. The whole design decision is
"read the corpus from a git ref, not the working tree", and a mocked `subprocess`
would test the mock. `git` is a pinned `REQUIRED_TOOLS` entry in `run-tests.sh`
and a `nativeBuildInputs` entry in flake.nix's `checks.pytests`, so it is present
in BOTH tiers. Nothing here skips.

🔴 FIXTURE VALUES ARE PAIRWISE DISTINCT AND DISTINCT FROM EVERY CONSTANT AN
ASSERTION NAMES. The section tokens, boost weights and limits all carry small
numbers; the fixtures use invented nonsense words (`quixotry`, `zarfwidget`,
`plimforth`) that occur in exactly one place each, so a mutant that hardcodes a
literal cannot survive by accident of a fixture that could only ever produce it.
This repo is PUBLIC — every fixture is synthetic and no real doc content, path or
hostname appears.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))
sys.path.insert(0, str(REPO_ROOT / "scripts" / "lib"))


def _load(name: str):
    path = REPO_ROOT / "scripts" / "lib" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


hi = _load("handoff_index")
hs = _load("handoff_search")
handoff_doc = _load("handoff_doc")


# --------------------------------------------------------------------------- #
# Fixtures — synthetic docs. Every distinctive term appears in EXACTLY one doc.
# --------------------------------------------------------------------------- #

DOC_FULL = """---
clawgate-task: cg-8814
owner: nobody
---
# Handoff: widget-relay — 2026-04-17

Preamble prose that is not a section.

## Goal
Make the quixotry relay answer within one hop.

## State now
The relay is deployed and the plimforth counter reads 12.

## Open investigations — live diagnosis state

### Why the zarfwidget latch never fires
Ruled out: the latch is not clock-gated. Measured twice.

### Whether the drumble cache is stale
Still open. Nothing measured yet.

## Next steps (ranked)
1. Rebuild the frobnitz table.
   forcing: gate
   It blocks the merge.
2. Chase the wibbleton report.
   forcing: sprocket
3. Tidy the marganser logs.

## Gotchas / decisions / dead-ends
- The blimflark path was a dead end; do not re-derive it.

## How to verify
Run the snarfle probe and read its exit code.
"""

# Deliberately MISSING: Open investigations, Next steps, Gotchas. Carries a term
# that appears nowhere else, so it is the negative-control's neighbour.
DOC_SPARSE = """# Handoff: cable-audit — 2026-05-03

## Goal
Count the trundlebore connectors.

## State now
Nothing counted.

## How to verify
Ask the flanterbly script.
"""

# 🔴 UNTERMINATED front matter: `---` on line 1 and NO closing `---`. This is
# preamble prose, not front matter, so `clawgate_task` must be None.
DOC_UNTERMINATED_FM = """---
clawgate-task: cg-9999

# Handoff: broken-fm — 2026-06-21

## Goal
Prove the vorpling case parses as preamble.
"""


def _write_repo(root: Path, docs: dict[str, str], *, commit: bool = True) -> None:
    """A real git repo with `claudedocs/handoff-*.md` committed on `main`."""
    root.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(root)], check=True)
    for k, v in (("user.email", "t@example.invalid"), ("user.name", "T")):
        subprocess.run(["git", "-C", str(root), "config", k, v], check=True)
    docs_dir = root / hi.HANDOFF_DIR
    docs_dir.mkdir(exist_ok=True)
    for name, text in docs.items():
        (docs_dir / name).write_text(text)
    if commit:
        for name in docs:
            subprocess.run(["git", "-C", str(root), "add", f"{hi.HANDOFF_DIR}/{name}"], check=True)
        subprocess.run(["git", "-C", str(root), "commit", "-qm", "docs"], check=True)


# --------------------------------------------------------------------------- #
# Section splitting
# --------------------------------------------------------------------------- #


class TestSectionSplitting:
    def test_a_full_doc_yields_one_row_per_retrieval_unit(self):
        rows = hi.sections_for_doc("relayrepo", "claudedocs/handoff-widget-relay.md", DOC_FULL)
        got = [(r.section, r.ordinal) for r in rows]
        assert got == [
            ("goal", 0),
            ("state", 0),
            ("investigation", 0),
            ("investigation", 1),
            ("next_step", 0),
            ("next_step", 1),
            ("next_step", 2),
            ("gotcha", 0),
            ("verify", 0),
        ]

    def test_investigations_split_per_h3_subblock_with_their_own_headings(self):
        rows = hi.sections_for_doc("relayrepo", "claudedocs/handoff-widget-relay.md", DOC_FULL)
        inv = [r for r in rows if r.section == "investigation"]
        assert [r.heading for r in inv] == [
            "Why the zarfwidget latch never fires",
            "Whether the drumble cache is stale",
        ]
        # The RULED-OUT text is the whole reason for sub-block granularity: it
        # must live in ONE row's body and not be diluted across the section.
        assert "Ruled out" in inv[0].body
        assert "Ruled out" not in inv[1].body

    def test_next_steps_split_per_ranked_item_carrying_the_whole_block(self):
        rows = hi.sections_for_doc("relayrepo", "claudedocs/handoff-widget-relay.md", DOC_FULL)
        items = [r for r in rows if r.section == "next_step"]
        assert [r.heading for r in items] == [
            "1. Rebuild the frobnitz table.",
            "2. Chase the wibbleton report.",
            "3. Tidy the marganser logs.",
        ]
        # A continuation line travels WITH its item — first-line-only indexing
        # would drop the majority of real items' content.
        assert "It blocks the merge." in items[0].body
        assert "It blocks the merge." not in items[1].body

    def test_a_doc_with_missing_sections_yields_only_what_it_has(self):
        rows = hi.sections_for_doc("cablerepo", "claudedocs/handoff-cable-audit.md", DOC_SPARSE)
        assert [r.section for r in rows] == ["goal", "state", "verify"]
        # 🔴 An absent section produces NO placeholder row. A row with an empty
        # body would match every query weakly and be indistinguishable, on the
        # result screen, from a section somebody actually wrote.
        assert all(r.body.strip() for r in rows)

    def test_ordinals_are_unique_within_a_section_token_across_the_whole_doc(self):
        """Two headings mapping to one token must not collide on the table's
        UNIQUE (repo, slug, section, ordinal) index."""
        doc = "# Handoff: dup — 2026-07-02\n\n## State now\nalpha\n\n## What shipped\nbravo\n"
        rows = hi.sections_for_doc("duprepo", "claudedocs/handoff-dup.md", doc)
        assert [(r.section, r.ordinal, r.body) for r in rows] == [
            ("state", 0, "alpha"),
            ("state", 1, "bravo"),
        ]

    def test_a_non_canonical_heading_produces_no_row(self):
        doc = "# Handoff: odd — 2026-07-03\n\n## Weather report\nrain\n\n## Goal\nsun\n"
        rows = hi.sections_for_doc("oddrepo", "claudedocs/handoff-odd.md", doc)
        assert [(r.section, r.body) for r in rows] == [("goal", "sun")]

    def test_a_fenced_h3_does_not_split_an_investigation_block(self):
        doc = (
            "# Handoff: fenced — 2026-07-04\n\n"
            "## Open investigations\n"
            "Body before.\n"
            "```\n### not a heading\n```\n"
            "Body after.\n"
        )
        rows = hi.sections_for_doc("fencerepo", "claudedocs/handoff-fenced.md", doc)
        assert len(rows) == 1
        assert rows[0].section == "investigation"
        assert "not a heading" in rows[0].body

    def test_every_canonical_prefix_has_a_section_and_vice_versa(self):
        """🔴 PINNED TWO-WAY against handoff_doc, which OWNS the heading skeleton.

        A prefix added there with no mapping here would be silently unindexed; a
        mapping here naming no prefix would be dead. Neither is visible without
        this assertion."""
        assert set(hi.PREFIX_SECTION) == set(handoff_doc.CANONICAL_HEADING_PREFIXES)
        assert set(hi.PREFIX_SECTION.values()) == set(hi.SECTIONS)


# --------------------------------------------------------------------------- #
# forcing_kind
# --------------------------------------------------------------------------- #


class TestForcingKind:
    def test_a_recognised_kind_is_stored_lowercased(self):
        rows = hi.sections_for_doc("relayrepo", "claudedocs/handoff-widget-relay.md", DOC_FULL)
        items = [r for r in rows if r.section == "next_step"]
        assert items[0].forcing_kind == "gate"

    def test_an_unrecognised_kind_folds_to_none_not_to_itself(self):
        """`forcing: sprocket` is not in the closed vocabulary. Storing it raw
        would put a value in the column that no query asks for and that makes
        `WHERE forcing_kind IS NULL` — 'declared nothing' — quietly wrong."""
        rows = hi.sections_for_doc("relayrepo", "claudedocs/handoff-widget-relay.md", DOC_FULL)
        items = [r for r in rows if r.section == "next_step"]
        assert items[1].forcing_kind is None
        # …and the near-miss stays fully SEARCHABLE via the item's own text.
        assert "sprocket" in items[1].body

    def test_an_item_with_no_field_at_all_is_none(self):
        rows = hi.sections_for_doc("relayrepo", "claudedocs/handoff-widget-relay.md", DOC_FULL)
        items = [r for r in rows if r.section == "next_step"]
        assert items[2].forcing_kind is None

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("gate", "gate"),
            ("INCIDENT", "incident"),
            ("  security  ", "security"),
            ("none", "none"),
            ("cleanup", None),
            ("followup", None),
            ("", None),
            (None, None),
            (17, None),
        ],
    )
    def test_fold_forcing_kind_is_a_closed_vocabulary(self, raw, expected):
        assert hi.fold_forcing_kind(raw) == expected

    def test_only_next_step_rows_ever_carry_a_kind(self):
        rows = hi.sections_for_doc("relayrepo", "claudedocs/handoff-widget-relay.md", DOC_FULL)
        assert all(r.forcing_kind is None for r in rows if r.section != "next_step")

    def test_ranked_item_units_agree_with_handoff_doc(self):
        """🔴 THE SEAM GUARD. `_next_step_units` walks the doc itself so it can
        keep each item's BLOCK; `handoff_doc.ranked_items` is what `/handoff`
        GATES on. If the two ever disagree about one document, search would
        return a forcing kind the writer rejects (or miss one it accepts) and
        nothing else in either suite would notice.

        Pins the RELATIONSHIP (the full paired sequence), not one side."""
        rows = hi.sections_for_doc("relayrepo", "claudedocs/handoff-widget-relay.md", DOC_FULL)
        mine = [(r.heading.split(".", 1)[0], r.forcing_kind)
                for r in rows if r.section == "next_step"]
        theirs = [(i.rank, hi.fold_forcing_kind(i.kind)) for i in handoff_doc.ranked_items(DOC_FULL)]
        assert mine == theirs == [("1", "gate"), ("2", None), ("3", None)]


# --------------------------------------------------------------------------- #
# Front matter / clawgate_task / doc_date / slug
# --------------------------------------------------------------------------- #


class TestFrontMatter:
    def test_a_closed_block_at_line_one_is_front_matter(self):
        assert hi.clawgate_task_for(DOC_FULL) == "cg-8814"
        assert hi.front_matter_fields(DOC_FULL)["owner"] == "nobody"

    def test_an_unterminated_block_is_not_front_matter(self):
        """🔴 THE GUARD. An opening `---` with no closer is preamble PROSE. A
        `clawgate-task:` harvested from it would attach a durable task id to a
        document that never declared one — and a caller reconciles against that
        field."""
        assert hi.clawgate_task_for(DOC_UNTERMINATED_FM) is None
        assert hi.front_matter_fields(DOC_UNTERMINATED_FM) == {}
        rows = hi.sections_for_doc("fmrepo", "claudedocs/handoff-broken-fm.md",
                                   DOC_UNTERMINATED_FM)
        assert rows and all(r.clawgate_task is None for r in rows)

    def test_a_horizontal_rule_later_in_the_doc_is_not_front_matter(self):
        doc = "# Handoff: rule — 2026-07-05\n\n---\nclawgate-task: cg-0001\n---\n\n## Goal\nx\n"
        assert hi.clawgate_task_for(doc) is None

    def test_a_doc_with_no_front_matter_has_no_task(self):
        assert hi.clawgate_task_for(DOC_SPARSE) is None

    def test_the_task_reaches_every_row_of_its_doc(self):
        rows = hi.sections_for_doc("relayrepo", "claudedocs/handoff-widget-relay.md", DOC_FULL)
        assert {r.clawgate_task for r in rows} == {"cg-8814"}


class TestDocIdentity:
    @pytest.mark.parametrize(
        "path,expected",
        [
            ("claudedocs/handoff-widget-relay.md", "widget-relay"),
            ("claudedocs/handoff-a.md", "a"),
            ("other/dir/handoff-nested-thing.md", "nested-thing"),
        ],
    )
    def test_slug_comes_from_the_filename(self, path, expected):
        assert hi.slug_for(path) == expected

    def test_the_date_comes_from_the_preamble_not_the_body(self):
        """The body of DOC_FULL contains no other ISO date; the preamble's is
        2026-04-17 and the filename carries none. A whole-document search would
        be non-deterministic on a real doc, which quotes dozens."""
        assert hi.doc_date_for("claudedocs/handoff-widget-relay.md", DOC_FULL) == "2026-04-17"

    def test_the_filename_is_the_fallback_when_the_preamble_has_no_date(self):
        doc = "# Handoff: nodate\n\n## Goal\nx said 2026-01-02 in the body\n"
        assert hi.doc_date_for("claudedocs/handoff-nodate-2025-11-30.md", doc) == "2025-11-30"

    def test_no_date_anywhere_is_none_not_a_guess(self):
        doc = "# Handoff: nodate\n\n## Goal\nx\n"
        assert hi.doc_date_for("claudedocs/handoff-nodate.md", doc) is None


# --------------------------------------------------------------------------- #
# The untracked-doc report (the durability hole)
# --------------------------------------------------------------------------- #


class TestUntrackedReport:
    def test_untracked_docs_is_a_one_way_difference(self):
        on_disk = ("claudedocs/handoff-a.md", "claudedocs/handoff-b.md")
        tracked = ("claudedocs/handoff-b.md", "claudedocs/handoff-c.md")
        # `handoff-c.md` is in the ref and not on disk — ordinary (any branch),
        # never a finding. Only the disk-only doc is a durability hole.
        assert hi.untracked_docs(on_disk, tracked) == ("claudedocs/handoff-a.md",)

    def test_a_clean_repo_reports_nothing_at_all(self):
        assert hi.untracked_docs(("x",), ("x",)) == ()
        assert hi.untracked_warnings("somerepo", ()) == ()

    def test_the_warning_names_the_repo_the_count_and_every_path(self):
        out = hi.untracked_warnings("somerepo", ["claudedocs/handoff-a.md",
                                                 "claudedocs/handoff-b.md"])
        assert "DURABILITY HOLE" in out[0]
        assert "2 handoff docs" in out[0]
        assert "somerepo" in out[0]
        assert "NOT INDEXED" in out[0]
        assert out[1:] == ("    claudedocs/handoff-a.md", "    claudedocs/handoff-b.md")

    def test_the_report_fires_end_to_end_on_a_real_repo(self, tmp_path):
        """🔴 THE POSITIVE CONTROL FOR THE REPORT. One doc committed, one only on
        disk. The uncommitted one must be WARNED ABOUT and must NOT appear in a
        single derived row."""
        repo = tmp_path / "holerepo"
        _write_repo(repo, {"handoff-widget-relay.md": DOC_FULL})
        (repo / hi.HANDOFF_DIR / "handoff-cable-audit.md").write_text(DOC_SPARSE)

        d = hi.derive_repo(repo, label="holerepo")
        assert d.untracked == ("claudedocs/handoff-cable-audit.md",)
        assert any("DURABILITY HOLE" in w for w in d.warnings)
        assert d.docs == 1
        # The uncommitted doc's unique term reaches NO row.
        assert not any("trundlebore" in r.body for r in d.sections)
        # …and the committed one's does.
        assert any("quixotry" in r.body for r in d.sections)

    def test_a_fully_committed_repo_warns_about_nothing(self, tmp_path):
        """The negative control for the same report: it must be capable of
        staying silent, or the assertion above proves only that it always fires."""
        repo = tmp_path / "cleanrepo"
        _write_repo(repo, {"handoff-widget-relay.md": DOC_FULL})
        d = hi.derive_repo(repo, label="cleanrepo")
        assert d.untracked == ()
        assert d.warnings == []


# --------------------------------------------------------------------------- #
# Git sourcing
# --------------------------------------------------------------------------- #


class TestGitSourcing:
    def test_the_corpus_comes_from_the_ref_not_the_working_tree(self, tmp_path):
        """🔴 THE DESIGN DECISION, TESTED. The committed doc says `quixotry`; the
        working copy is then overwritten with a term that exists nowhere else. The
        index must carry the COMMITTED text — a working-tree read would carry the
        edit."""
        repo = tmp_path / "refrepo"
        _write_repo(repo, {"handoff-widget-relay.md": DOC_FULL})
        (repo / hi.HANDOFF_DIR / "handoff-widget-relay.md").write_text(
            "# Handoff: widget-relay — 2026-04-17\n\n## Goal\nThe grumbleflitch edit.\n"
        )
        d = hi.derive_repo(repo, label="refrepo")
        bodies = " ".join(r.body for r in d.sections)
        assert "quixotry" in bodies
        assert "grumbleflitch" not in bodies

    def test_the_mainline_ref_is_derived_and_recorded_with_its_ladder(self, tmp_path):
        repo = tmp_path / "ladderrepo"
        _write_repo(repo, {"handoff-cable-audit.md": DOC_SPARSE})
        d = hi.derive_repo(repo, label="ladderrepo")
        assert d.ref == "main"
        assert d.ladder  # never an unexplained empty
        assert d.sections and all(len(r.commit_sha or "") == 40 for r in d.sections)

    def test_only_handoff_named_docs_are_collected(self, tmp_path):
        repo = tmp_path / "mixedrepo"
        _write_repo(repo, {"handoff-cable-audit.md": DOC_SPARSE,
                           "decision-something.md": "# Handoff: nope\n\n## Goal\nno\n"})
        ref, _ladder = hi.resolve_mainline(repo)
        assert hi.handoff_paths_in_ref(repo, ref) == ("claudedocs/handoff-cable-audit.md",)

    def test_a_directory_that_is_not_a_repo_is_UNMEASURED_not_a_silent_zero(self, tmp_path):
        plain = tmp_path / "notarepo"
        (plain / hi.HANDOFF_DIR).mkdir(parents=True)
        (plain / hi.HANDOFF_DIR / "handoff-cable-audit.md").write_text(DOC_SPARSE)
        d = hi.derive_repo(plain, label="notarepo")
        assert d.sections == []
        assert any("UNMEASURED" in w for w in d.warnings)
        assert any("no mainline ref" in w for w in d.warnings)

    def test_a_missing_directory_is_UNMEASURED_too(self, tmp_path):
        d = hi.derive_repo(tmp_path / "absent", label="absentrepo")
        assert d.sections == []
        assert any("UNMEASURED" in w and "no such directory" in w for w in d.warnings)


# --------------------------------------------------------------------------- #
# Search — the controls
# --------------------------------------------------------------------------- #


def _corpus() -> list:
    return [
        *hi.sections_for_doc("relayrepo", "claudedocs/handoff-widget-relay.md", DOC_FULL),
        *hi.sections_for_doc("cablerepo", "claudedocs/handoff-cable-audit.md", DOC_SPARSE),
    ]


def _store():
    return hi.MemorySectionStore(_corpus())


class TestSearchControls:
    def test_positive_control_a_term_in_exactly_one_doc_returns_that_doc(self):
        """🔴 POSITIVE CONTROL. `zarfwidget` occurs in exactly one section of one
        fixture doc. The count must MOVE OFF ZERO and land on that doc alone —
        a reassuring zero is indistinguishable from a harness wired to nothing."""
        out = hs.run_search(_store(), "zarfwidget", backend="memory")
        assert out.status == "hit"
        assert len(out.hits) == 1
        h = out.hits[0]
        assert (h.repo, h.slug, h.section) == ("relayrepo", "widget-relay", "investigation")
        assert h.heading == "Why the zarfwidget latch never fires"
        assert out.stats.indexed_docs == 2

    def test_negative_control_a_term_in_no_doc_returns_zero_with_a_full_index(self):
        """🔴 NEGATIVE CONTROL, and the pair is what makes either readable: the
        SAME store that returned 1 above returns 0 here, with indexed_docs
        unchanged and NON-ZERO. That is an answer about the corpus."""
        out = hs.run_search(_store(), "hexapoddery", backend="memory")
        assert out.status == "no-match"
        assert out.hits == ()
        assert out.stats.indexed_docs == 2
        assert out.stats.indexed_sections == len(_corpus())

    def test_the_repo_filter_narrows_without_emptying_the_index(self):
        out = hs.run_search(_store(), "trundlebore", backend="memory", repo="relayrepo")
        assert out.status == "no-match"
        assert out.stats.indexed_docs == 2
        out2 = hs.run_search(_store(), "trundlebore", backend="memory", repo="cablerepo")
        assert [h.slug for h in out2.hits] == ["cable-audit"]

    def test_the_section_filter_narrows_to_the_named_kinds(self):
        out = hs.run_search(_store(), "relay", backend="memory", sections=["goal"])
        assert {h.section for h in out.hits} == {"goal"}

    def test_investigation_and_gotcha_outrank_a_plain_section_on_an_equal_match(self):
        """The boost is the whole retrieval thesis: re-discovery-prevention
        content ranks first. Built as a differential — one term, three sections,
        identical token coverage — so only the multiplier can order them."""
        term = "flurb"
        rows = [
            hi.Section("r", "s", "p", "2026-01-01", None, "goal", 0, "h", term),
            hi.Section("r", "s", "p", "2026-01-01", None, "gotcha", 0, "h", term),
            hi.Section("r", "s", "p", "2026-01-01", None, "investigation", 0, "h", term),
        ]
        out = hs.run_search(hi.MemorySectionStore(rows), term, backend="memory")
        assert [h.section for h in out.hits] == ["investigation", "gotcha", "goal"]
        assert out.hits[0].rank > out.hits[1].rank > out.hits[2].rank

    def test_recency_breaks_a_tie_and_a_dateless_row_sorts_last(self):
        rows = [
            hi.Section("r", "old", "p", "2020-02-02", None, "goal", 0, "h", "flurb"),
            hi.Section("r", "none", "p", None, None, "goal", 0, "h", "flurb"),
            hi.Section("r", "new", "p", "2026-08-08", None, "goal", 0, "h", "flurb"),
        ]
        out = hs.run_search(hi.MemorySectionStore(rows), "flurb", backend="memory")
        assert [h.slug for h in out.hits] == ["new", "old", "none"]

    def test_the_limit_is_applied_and_announced(self):
        rows = [
            hi.Section("r", f"s{i}", "p", "2026-01-01", None, "goal", 0, "h", "flurb")
            for i in range(5)
        ]
        out = hs.run_search(hi.MemorySectionStore(rows), "flurb", backend="memory", limit=2)
        assert len(out.hits) == 2
        assert out.truncated
        assert "--limit 2 reached" in hs.render(out)

    def test_an_empty_query_matches_nothing_rather_than_everything(self):
        out = hs.run_search(_store(), "   ", backend="memory")
        assert out.status == "no-match"


class TestSilentZeroGuard:
    def test_an_empty_index_is_broken_not_a_no_match(self):
        """🔴 THE GUARD. Zero rows means the query ran against NOTHING, so the
        status must not be the one that says 'the corpus does not mention that'."""
        out = hs.run_search(hi.MemorySectionStore([]), "zarfwidget", backend="memory")
        assert out.status == "broken-index"
        assert out.stats.indexed_docs == 0

    def test_the_two_zeros_render_with_no_shared_opening_phrase(self):
        broken = hs.render(hs.run_search(hi.MemorySectionStore([]), "zarfwidget",
                                        backend="memory"))
        genuine = hs.render(hs.run_search(_store(), "hexapoddery", backend="memory"))

        assert "🔴 BROKEN INDEX" in broken
        assert "NO MATCH" not in broken
        assert "NO MATCH" in genuine
        assert "BROKEN INDEX" not in genuine
        # 🔴 PIN THE WHOLE NORMALISED SENTENCE, not a word — a guard on WORDS is
        # walkable by REWORDING (claude/RULES.md). These two claims must stay
        # distinguishable by a machine.
        assert ("ran against NOTHING" in broken) and ("ran against NOTHING" not in genuine)
        assert ("the index WAS searched" in genuine) and ("the index WAS searched" not in broken)

    def test_every_status_carries_the_counts_in_a_greppable_shape(self):
        for store, query, expect_docs in (
            (_store(), "zarfwidget", 2),
            (_store(), "hexapoddery", 2),
            (hi.MemorySectionStore([]), "zarfwidget", 0),
        ):
            out = hs.run_search(store, query, backend="memory")
            text = hs.render(out)
            assert f"indexed_docs={expect_docs} indexed_sections=" in text
            assert "backend=memory" in text

    def test_the_broken_index_status_exits_non_zero_through_main(self, tmp_path, capsys):
        """A caller that only reads the exit code must not read a broken index as
        'no results'. Driven through `main` with an --offline repo holding no docs."""
        repo = tmp_path / "emptyrepo"
        _write_repo(repo, {}, commit=False)
        subprocess.run(["git", "-C", str(repo), "commit", "-qm", "empty",
                        "--allow-empty"], check=True)
        rc = hs.main(["--query", "zarfwidget", "--offline", "--offline-repo", str(repo)])
        assert rc == 3
        assert "🔴 BROKEN INDEX" in capsys.readouterr().out

    def test_a_real_hit_exits_zero_through_main(self, tmp_path, capsys):
        """The positive control for the exit code — otherwise rc 3 above could be
        the only value main ever returns."""
        repo = tmp_path / "hitrepo"
        _write_repo(repo, {"handoff-widget-relay.md": DOC_FULL})
        rc = hs.main(["--query", "zarfwidget", "--offline", "--offline-repo", str(repo)])
        assert rc == 0
        assert "Why the zarfwidget latch never fires" in capsys.readouterr().out


class TestRecallBanner:
    def test_the_banner_is_on_every_status(self):
        for store, query in (
            (_store(), "zarfwidget"),
            (_store(), "hexapoddery"),
            (hi.MemorySectionStore([]), "zarfwidget"),
        ):
            text = hs.render(hs.run_search(store, query, backend="memory"))
            assert text.startswith(hs.recall_banner())

    def test_the_banner_makes_the_three_claims_that_matter(self):
        b = hs.recall_banner()
        assert "RECALL, NOT LIVE OBSERVATION" in b
        assert "POINTER TO VERIFY" in b
        assert "HAS SINCE BEEN FIXED" in b
        assert "CANNOT see" in b

    def test_the_json_surface_carries_the_banner_too(self):
        payload = hs.outcome_json(hs.run_search(_store(), "zarfwidget", backend="memory"))
        assert payload["caveat"] == hs.recall_banner()
        assert payload["status"] == "hit"
        assert payload["indexed_docs"] == 2
        assert payload["hits"][0]["section"] == "investigation"

    def test_the_declared_status_vocabulary_is_exactly_what_is_emitted(self):
        """A status constant no code path emits would be a declaration with
        nothing behind it; a status emitted but undeclared cannot be switched on."""
        emitted = {
            hs.run_search(_store(), "zarfwidget", backend="memory").status,
            hs.run_search(_store(), "hexapoddery", backend="memory").status,
            hs.run_search(hi.MemorySectionStore([]), "x", backend="memory").status,
        }
        assert emitted == set(hs.STATUSES)


# --------------------------------------------------------------------------- #
# The Postgres backend — what CAN be checked without a database
# --------------------------------------------------------------------------- #


class TestPostgresBackendShape:
    def test_the_boost_numbers_live_in_exactly_one_place(self):
        """🔴 The SQL CASE is BUILT from SECTION_BOOST. If the two rankers could
        drift, a change would land in the offline one and silently miss the
        indexed one — invisible until somebody compared two runs."""
        case = hi._boost_case()
        for token, boost in hi.SECTION_BOOST.items():
            assert f"WHEN '{token}' THEN {boost}" in case
        assert f"ELSE {hi.DEFAULT_BOOST} END" in case
        assert case in hi.PostgresSectionStore.search_sql(repo=False, sections=False)

    def test_the_sql_ranks_boosts_and_tiebreaks_in_that_order(self):
        sql = hi.PostgresSectionStore.search_sql(repo=False, sections=False)
        assert "ts_rank(tsv, q)" in sql
        assert "tsv @@ q" in sql
        assert "ORDER BY rank DESC, doc_date DESC NULLS LAST" in sql
        assert "LIMIT %s" in sql

    def test_the_filters_are_parameterised_not_interpolated(self):
        plain = hi.PostgresSectionStore.search_sql(repo=False, sections=False)
        both = hi.PostgresSectionStore.search_sql(repo=True, sections=True)
        assert "repo = %s" not in plain and "repo = %s" in both
        assert "section = ANY(%s)" not in plain and "section = ANY(%s)" in both

    def test_the_ddl_declares_the_generated_tsv_the_gin_index_and_the_identity(self):
        ddl = hi.TABLES_DDL
        assert "GENERATED ALWAYS AS" in ddl and "STORED" in ddl
        assert "to_tsvector('english'" in ddl
        assert "USING GIN (tsv)" in ddl
        assert "CREATE UNIQUE INDEX IF NOT EXISTS handoff_section_identity_idx" in ddl
        assert "(repo, slug, section, ordinal)" in ddl
        # Idempotent like sync.py's: every object is IF NOT EXISTS.
        for stmt in ("CREATE SCHEMA", "CREATE TABLE", "CREATE INDEX"):
            assert stmt in ddl
        assert "CREATE TABLE IF NOT EXISTS" in ddl

    def test_the_row_shape_matches_the_columns_the_ddl_declares(self):
        row = hi.sections_for_doc("r", "claudedocs/handoff-a.md", DOC_FULL)[0].as_row()
        for col in ("repo", "slug", "doc_path", "doc_date", "commit_sha", "section",
                    "ordinal", "heading", "body", "forcing_kind", "clawgate_task"):
            assert col in row, col
            assert f"    {col} " in hi.TABLES_DDL or f"{col}," in hi.TABLES_DDL

    def test_no_test_in_this_file_can_reach_a_database(self):
        """The seam is the point: `MemorySectionStore` satisfies the same protocol
        the Postgres one does, so nothing above needed a connection."""
        assert hasattr(hi.MemorySectionStore, "search")
        assert hasattr(hi.MemorySectionStore, "stats")
        assert hasattr(hi.PostgresSectionStore, "search")
        assert hasattr(hi.PostgresSectionStore, "stats")
