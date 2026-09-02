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
import json
import os
import re
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
            # The common case is UNCHANGED: `claudedocs/` is stripped, so every
            # doc directly under it keeps the bare slug it has always had.
            ("claudedocs/handoff-widget-relay.md", "widget-relay"),
            ("claudedocs/handoff-a.md", "a"),
            # 🔴 A NESTED doc keeps its directory, because the basename alone is
            # not a unique identity — see the collision test below.
            ("claudedocs/sub/handoff-a.md", "sub/a"),
            ("claudedocs/deep/deeper/handoff-a.md", "deep/deeper/a"),
            ("other/dir/handoff-nested-thing.md", "other/dir/nested-thing"),
        ],
    )
    def test_slug_comes_from_the_path_not_the_basename(self, path, expected):
        assert hi.slug_for(path) == expected

    def test_two_docs_sharing_a_basename_do_not_share_an_identity(self):
        """🔴 THE COLLISION, AT THE SOURCE. `claudedocs/handoff-a.md` and
        `claudedocs/sub/handoff-a.md` are two documents. Under a basename-only
        slug they were ONE `(repo, slug, section, ordinal)` identity, so the
        table's `ON CONFLICT DO UPDATE` overwrote the first with the second —
        no error, no warning, `docs=2` beside `indexed_docs=1`.

        Asserted as a RELATIONSHIP (the two differ) rather than as two literals,
        because the value that matters is that they cannot coincide."""
        flat = hi.slug_for("claudedocs/handoff-a.md")
        nested = hi.slug_for("claudedocs/sub/handoff-a.md")
        assert flat != nested

    def test_the_identity_of_two_same_named_docs_survives_a_real_derivation(self):
        doc = "# Handoff: a — 2026-03-04\n\n## Goal\nthe grinnelwort target\n"
        rows = [
            *hi.sections_for_doc("r", "claudedocs/handoff-a.md", doc),
            *hi.sections_for_doc("r", "claudedocs/sub/handoff-a.md", doc),
        ]
        identities = {(r.repo, r.slug, r.section, r.ordinal) for r in rows}
        assert len(identities) == len(rows) == 2
        assert hi.identity_collisions(rows) == ()
        # …and the store counts them as TWO documents, agreeing with `docs=2`.
        assert hi.MemorySectionStore(rows).stats().indexed_docs == 2

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
        """🔴 THREE LABELS, NOT TWO, AND THE THIRD IS THE POINT. This test used to
        pass only labels that ARE indexed, so its name ("narrows without emptying")
        read wider than its assertion: it never exercised a filter that empties the
        scope, which is the case that rendered the corpus-is-silent prose.

        Now: a filter that narrows to a populated scope stays `no-match` with the
        scoped counts non-zero, a filter that narrows to a HIT returns it, and a
        filter naming a repo the index does not hold is `empty-scope` — a
        different status, with `in_scope` at zero while the whole index is not."""
        # (a) narrows to a populated scope; the term is simply not in it.
        out = hs.run_search(_store(), "trundlebore", backend="memory", repo="relayrepo")
        assert out.status == "no-match"
        assert out.stats.indexed_docs == 2
        assert out.in_scope.indexed_docs == 1
        assert out.in_scope.indexed_sections > 0
        assert out.in_scope.indexed_sections < out.stats.indexed_sections

        # (b) narrows to the doc that has it.
        out2 = hs.run_search(_store(), "trundlebore", backend="memory", repo="cablerepo")
        assert [h.slug for h in out2.hits] == ["cable-audit"]
        assert out2.in_scope.indexed_docs == 1

        # (c) 🔴 names a repo the index does not hold — NOT a no-match.
        out3 = hs.run_search(_store(), "trundlebore", backend="memory", repo="norepo")
        assert out3.status == "empty-scope"
        assert out3.in_scope.indexed_sections == 0
        assert out3.stats.indexed_sections > 0

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

    def test_a_derived_empty_corpus_exits_non_zero_through_main(self, tmp_path, capsys):
        """A caller that only reads the exit code must not read an empty corpus as
        'no results'. Driven through `main` with an --offline repo holding no docs.

        ⚠ THIS TEST USED TO ASSERT rc 3 / `🔴 BROKEN INDEX` FOR THIS EXACT ARGV,
        and that was the Y3 defect surviving in its sibling case: `--offline`
        opens no database, so a remedy naming a table to rebuild and the
        handoff-index-sync unit is a confident wrong next step. Only the
        unresolvable half had been fixed. `broken-index` is now reachable ONLY
        from a store this run READ, which the memory backend never is."""
        repo = tmp_path / "emptyrepo"
        _write_repo(repo, {}, commit=False)
        subprocess.run(["git", "-C", str(repo), "commit", "-qm", "empty",
                        "--allow-empty"], check=True)
        rc = hs.main(["--query", "zarfwidget", "--offline", "--offline-repo", str(repo)])
        out = capsys.readouterr().out
        assert rc == hs.EXIT_CODES["derived-zero-docs"] == 7
        assert "🔴 ZERO HANDOFF DOCS DERIVED" in out
        assert "BROKEN INDEX" not in out
        assert "handoff-index-sync" not in out
        assert "--rebuild --write" not in out

    def test_the_broken_index_status_is_still_reachable_from_a_read_store(self):
        """The negative control for the reclassification above: `broken-index` and
        its rc must still EXIST and still fire for the case it was written for —
        a store that was READ and holds nothing, with no derivation behind it (the
        Postgres shape, which passes no `targets`). Otherwise the assertion above
        proves only that the branch was deleted."""
        out = hs.run_search(hi.MemorySectionStore([]), "zarfwidget", backend="postgres")
        assert out.status == "broken-index"
        assert hs.exit_code_for(out) == 3
        assert "🔴 BROKEN INDEX" in hs.render(out)

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
            hs.run_search(_store(), "zarfwidget", backend="memory",
                          repo="norepo-by-that-name").status,
            hs.run_search(hi.MemorySectionStore([]), "x", backend="memory").status,
            # ALL targets unmeasured -> unmeasured-corpus. The denominator is what
            # makes "all" true, and passing `unmeasured` without `targets` is the
            # shape that used to say "all 1 repo(s)" for a run pointed at two.
            hs.run_search(hi.MemorySectionStore([]), "x", backend="memory",
                          unmeasured=(("gone", "no-such-directory"),),
                          targets=("gone",)).status,
            # SOME target resolved and the built corpus is empty -> the fifth zero.
            hs.run_search(hi.MemorySectionStore([]), "x", backend="memory",
                          unmeasured=(("gone", "no-such-directory"),),
                          targets=("gone", "hollow")).status,
        }
        assert emitted == set(hs.STATUSES)

    def test_every_status_has_an_exit_code_and_vice_versa(self):
        """🔴 PINNED AS A PARTITION. A status in none of the three ledgers falls
        through `.get(status, 0)` and exits 0 — the fluent-zero failure one level
        up, in the one channel a scripted caller reads. A ledger entry for a
        status nothing emits is a contract that can never fire.

        Three ledgers, not one, because `empty-scope`'s code is decided by its
        REASON and the other statuses' by the status alone. They must PARTITION
        `STATUSES`: overlap would mean two rules claim one status and the winner
        is whichever branch `exit_code_for` happens to test first."""
        ledgers = [set(hs.EXIT_CODES), set(hs.ANSWER_STATUSES),
                   set(hs.REASON_KEYED_STATUSES)]
        assert set().union(*ledgers) == set(hs.STATUSES)
        for i, a in enumerate(ledgers):
            for b in ledgers[i + 1:]:
                assert a & b == set(), (a, b)
        # …and no two status-keyed non-answers share a code, or the caller cannot
        # tell a broken environment from a missing checkout.
        assert len(set(hs.EXIT_CODES.values())) == len(hs.EXIT_CODES)
        assert all(v != 0 for v in hs.EXIT_CODES.values())

    def test_every_scope_reason_has_an_exit_code_and_vice_versa(self):
        """🔴 THE SECOND HALF OF THE PARTITION, PINNED TWO-WAY. `empty-scope` is
        REASON-keyed, so a new `SCOPE_REASONS` member with no entry here would
        take `exit_code_for`'s fallback silently, and an entry naming no reason is
        a code nothing can produce."""
        assert set(hs.SCOPE_REASON_EXIT_CODES) == set(hs.SCOPE_REASONS)

    def test_the_documented_no_rows_decision_is_the_one_that_ships(self):
        """🔴 THE ARGUABLE CHOICE, PINNED SO IT CANNOT DRIFT SILENTLY. The module
        docstring argues at length that a VALID filter over an empty scope
        (`no-rows`) exits non-zero rather than 0, because the exit code is the one
        channel read without the prose and `no-rows` (searched ZERO sections) and
        `no-match` (searched N>0, found nothing) are the two zeros this module
        exists to keep apart. Asserted as a differential against `no-match`, which
        IS an answer and IS 0 — a test on `no-rows` alone could not tell the
        decision from a blanket 'everything is non-zero'."""
        store = _store()
        no_rows = hs.run_search(store, "zarfwidget", backend="memory",
                                repo="cablerepo", sections=["gotcha"])
        no_match = hs.run_search(store, "hexapoddery", backend="memory")
        assert no_rows.scope_reason == "no-rows"
        assert hs.exit_code_for(no_rows) == 4
        assert no_match.status == "no-match"
        assert hs.exit_code_for(no_match) == 0


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
        """🔴 THE MATCH IS AGAINST A COLUMN DECLARATION, NOT ANY OCCURRENCE.

        The `f"{col},"` half of the old assertion matched a column name ANYWHERE
        in the DDL text — including inside `(repo, slug, section, ordinal)`, the
        UNIQUE INDEX's column list. So a column dropped from the CREATE TABLE but
        still named in an index satisfied the test, which is exactly the case the
        test exists to catch. Now the DDL's declarations are PARSED out of the
        CREATE TABLE body and compared as a SET, in both directions: a row key
        with no column fails, and a column with no row key fails."""
        body = hi.TABLES_DDL.split(f"CREATE TABLE IF NOT EXISTS {hi.TABLE} (", 1)[1]
        body = body.split("\n);", 1)[0]
        declared = set()
        for line in body.splitlines():
            stripped = line.strip()
            m = re.match(r"^([a-z_]+)\s+[a-z]", stripped)
            if m and not stripped.startswith(("CREATE", "PRIMARY", "UNIQUE")):
                declared.add(m.group(1))
        # The generated column and the surrogate key are not part of a row's
        # payload; everything else must correspond exactly.
        payload = declared - {"tsv", "id"}
        row = hi.sections_for_doc("r", "claudedocs/handoff-a.md", DOC_FULL)[0].as_row()
        assert payload == set(row), (payload, set(row))
        assert payload == set(hi.PostgresSectionStore.WRITE_COLUMNS)
        # The parser must actually have found something — a regex that matched
        # nothing would make both set comparisons vacuously about empty sets.
        assert len(payload) == 11

    def test_no_test_in_this_file_can_reach_a_database(self):
        """The seam is the point: `MemorySectionStore` satisfies the same protocol
        the Postgres one does, so nothing above needed a connection."""
        for attr in ("search", "stats", "repos"):
            assert hasattr(hi.MemorySectionStore, attr), attr
            assert hasattr(hi.PostgresSectionStore, attr), attr

    def test_the_stats_query_carries_the_same_filters_the_search_query_does(self):
        """🔴 THE ONE-PREDICATE PIN, AS A RELATIONSHIP. An unscoped count printed
        beside a scoped query is a number about a different corpus — the F2
        defect. Asserted by comparing the two builders' output rather than by
        re-typing either WHERE clause, so a change to one that misses the other
        is red here."""
        plain = hi.PostgresSectionStore.stats_sql(repo=False, sections=False)
        assert "WHERE" not in plain
        for kwargs in ({"repo": True, "sections": False},
                       {"repo": False, "sections": True},
                       {"repo": True, "sections": True}):
            stats = hi.PostgresSectionStore.stats_sql(**kwargs)
            search = hi.PostgresSectionStore.search_sql(**kwargs)
            for pred in hi._filter_predicates(**kwargs):
                assert pred in stats, (pred, stats)
                assert pred in search, (pred, search)
            assert "WHERE" in stats
        # A positive control on the helper itself: it must be capable of
        # returning something, or the loop above asserts over an empty list.
        assert hi._filter_predicates(repo=True, sections=True) != []

    def test_the_repos_query_asks_for_distinct_labels(self):
        assert "SELECT DISTINCT repo" in hi.PostgresSectionStore.REPOS_SQL
        assert hi.TABLE in hi.PostgresSectionStore.REPOS_SQL


# --------------------------------------------------------------------------- #
# F1 — the write path: refusal, exit codes, ONE transaction
# --------------------------------------------------------------------------- #


class RecordingCursor:
    """Records every statement, in order, as a normalised first token + table."""

    def __init__(self, log):
        self._log = log

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def execute(self, sql, params=None):
        # 🔴 THE PARAMS ARE RECORDED TOO, and that is not decoration: the F2
        # defect is a DELETE whose WHERE clause is right and whose BOUND SCOPE is
        # wrong. A recorder that keeps only the SQL text cannot see the difference
        # between deleting one repo's rows and deleting every repo's.
        self._log.append(("EXEC", " ".join(str(sql).split()), params))

    def fetchone(self):
        return (0, 0)

    def fetchall(self):
        return []


class RecordingConn:
    """A fake psycopg2 connection that records `(statement | commit)` IN ORDER.

    🔴 THE ORDER IS THE ASSERTION, and nothing else here can produce it. The F1
    defect was not "TRUNCATE ran" — it is supposed to run — but that it ran in
    its OWN transaction and committed before a single row was inserted, so any
    exception in the row loop left the table empty AND durable. Only a recorder
    that keeps commits interleaved with statements can show that. It is not a
    server and proves nothing about whether Postgres makes TRUNCATE
    transactional; it proves what THIS module asks the server to do."""

    def __init__(self):
        self.log: list[tuple] = []
        self.closed = False

    def cursor(self):
        return RecordingCursor(self.log)

    def commit(self):
        self.log.append(("COMMIT",))

    def close(self):
        self.closed = True

    # -- readers over the recording ----------------------------------------- #

    def statements(self):
        return [e[1] for e in self.log if e[0] == "EXEC"]

    def params_for(self, prefix):
        """The bound parameters of every statement starting with `prefix`."""
        return [e[2] for e in self.log if e[0] == "EXEC" and e[1].startswith(prefix)]

    def kinds(self):
        """The log as a coarse sequence: TRUNCATE / DELETE / INSERT / DDL / COMMIT.

        🔴 `TRUNCATE` IS STILL IN THE VOCABULARY THOUGH NOTHING EMITS IT ANY MORE.
        That is deliberate: the assertions below say `"TRUNCATE" not in kinds`,
        and a classifier that cannot spell the word would satisfy that assertion
        for a run that issued one. A negative assertion is only worth what the
        instrument's positive vocabulary is."""
        out = []
        for e in self.log:
            if e[0] == "COMMIT":
                out.append("COMMIT")
            elif e[1].startswith("TRUNCATE"):
                out.append("TRUNCATE")
            elif e[1].startswith("DELETE"):
                out.append("DELETE")
            elif e[1].startswith("INSERT"):
                out.append("INSERT")
            elif e[1].startswith("SELECT pg_advisory"):
                out.append("LOCK")
            else:
                out.append("DDL")
        return out


def _handoff_unit_block() -> str:
    """The `systemd.user.services.handoff-index-sync` attrset, and NOTHING after it.

    🔴 BOUNDED AT BOTH ENDS ON PURPOSE. A bare `split(marker)[1]` runs to the end
    of `home.nix` — 14 other units' `ExecStart` lines included — so an assertion
    about "the unit's ExecStart" would be reading somebody else's. Ends at the
    next `systemd.user.` declaration, which is what closes this one."""
    text = (REPO_ROOT / "nix" / "home.nix").read_text()
    after = text.split("systemd.user.services.handoff-index-sync")[1]
    return after.split("\n  systemd.user.")[0]


class StoredReposCursor(RecordingCursor):
    """A `RecordingCursor` whose `fetchall` answers `PostgresSectionStore.repos()`.

    🔴 THE PLAIN RECORDER RETURNS `[]` THERE, WHICH IS THE ONE VALUE THAT MAKES
    THE BUG UNREACHABLE. `rebuild_delete_labels`' collect-what-the-config-no-
    longer-names arm is a set difference against the STORED labels; with an empty
    stored set the difference is always empty, so every existing end-to-end test
    exercised the delete scope with the input that cannot show it. This cursor is
    what lets a test drive `main` against a table that already holds rows."""

    def __init__(self, log, stored):
        super().__init__(log)
        self._stored = stored

    def fetchall(self):
        return [(r,) for r in self._stored]


class StoredReposConn(RecordingConn):
    """`RecordingConn` over a table that already holds `stored` repo labels."""

    def __init__(self, stored):
        super().__init__()
        self._stored = tuple(stored)

    def cursor(self):
        return StoredReposCursor(self.log, self._stored)


def _recording_store(conn):
    """An `open_store` seam yielding a real `PostgresSectionStore` over `conn`.

    The store is the PRODUCTION class — only the connection is fake — so the SQL
    under test is the SQL that ships."""
    import contextlib

    @contextlib.contextmanager
    def _open():
        yield hi.PostgresSectionStore(conn)

    return _open


def _refusing_store():
    """An `open_store` that FAILS if anything opens it. The positive control for
    'the write was refused': a test asserting only on the exit code cannot tell a
    refusal from a write that happened and then returned non-zero."""
    import contextlib

    @contextlib.contextmanager
    def _open():  # pragma: no cover - the assertion is that this never runs
        raise AssertionError("the store was opened for a run that must not write")
        yield None

    return _open


class TestRebuildRefusal:
    def test_an_all_unmeasured_rebuild_writes_nothing_and_exits_non_zero(self, capsys):
        """🔴 THE REPRODUCED INCIDENT. Driving this exact argv against a fake
        connection emptied the table and returned 0, so the unit's
        `OnFailure=notify-failure@%n.service` never fired and the only symptom was
        `🔴 BROKEN INDEX` from the search CLI days later."""
        rc = hi.main(["--repo", "/nonexistent/alpha", "--repo", "/nonexistent/bravo",
                      "--rebuild", "--write"], open_store=_refusing_store())
        assert rc == hi.RC_REFUSED
        assert rc != 0
        err = capsys.readouterr().err
        assert "REFUSING --rebuild" in err
        assert "UNMEASURED" in err

    def test_a_rebuild_over_a_repo_with_no_docs_is_refused_too(self, tmp_path, capsys):
        """A repo that RESOLVED a ref and produced zero rows. Not the same
        mechanism as UNMEASURED, and both must refuse: 'I could not read the
        corpus' is never 'the corpus is empty'."""
        repo = tmp_path / "norows"
        _write_repo(repo, {}, commit=False)
        subprocess.run(["git", "-C", str(repo), "commit", "-qm", "e", "--allow-empty"],
                       check=True)
        rc = hi.main(["--repo", str(repo), "--rebuild", "--write"],
                     open_store=_refusing_store())
        assert rc == hi.RC_REFUSED
        assert "ZERO rows" in capsys.readouterr().err

    def test_the_refusal_reads_the_structural_flag_not_the_warning_prose(self):
        """🔴 A GUARD ON WORDS IS WALKABLE BY REWORDING. `rebuild_refusal` must
        key off `RepoDerivation.unmeasured`, never off the string `UNMEASURED`
        appearing in `warnings` — otherwise editing the warning's wording (a
        cosmetic change nobody would re-test) silently disarms the guard.

        Built as a differential over the two fields with the SAME rows, so only
        the field being read can explain the two answers."""
        row = hi.Section("r", "s", "p", None, None, "goal", 0, "h", "b")

        # Structural flag set, warning prose says nothing at all -> REFUSED.
        silent = hi.RepoDerivation(repo="/x", label="x", unmeasured="no-mainline-ref")
        assert hi.rebuild_refusal([silent], [row]) is not None

        # Warning prose SHOUTS the word, structural flag clear, rows present
        # -> allowed. A prose-reading guard would refuse here.
        noisy = hi.RepoDerivation(repo="/y", label="y", ref="main",
                                  warnings=["⚠ UNMEASURED — decorative text"])
        assert hi.rebuild_refusal([noisy], [row]) is None

    def test_a_healthy_rebuild_is_NOT_refused(self, tmp_path):
        """The negative control. Without it the two assertions above prove only
        that the guard always refuses."""
        repo = tmp_path / "healthy"
        _write_repo(repo, {"handoff-widget-relay.md": DOC_FULL})
        d = hi.derive_repo(repo, label="healthy")
        assert d.unmeasured is None
        assert hi.rebuild_refusal([d], d.sections) is None

    def test_one_unmeasured_repo_among_several_is_NOT_refused(self, tmp_path):
        """🔴 A DELIBERATE REVERSAL, AND THE REASON IT REVERSED IS A MEASUREMENT.
        This guard first shipped refusing when ANY repo came back UNMEASURED.
        Measured on exactly this shape — one present repo with real rows plus one
        absent repo — it returned `REFUSING --rebuild: 1 of 2 repo(s) came back
        UNMEASURED`, rc 4, nothing written. On a unit carrying
        `OnFailure=notify-failure@%n.service` behind a 6h timer that is a failure
        toast 4×/day forever with the index frozen, on a host whose only sin is
        not having `$CIVITAI` checked out — `claude/RULES.md`'s permanently-red
        gate, which trains everyone to click through.

        A partial run is now SAFE rather than refused, and the thing that makes it
        safe is `rebuild_delete_labels` scoping the delete away from the repo that
        could not be read. That is asserted here too: refusing would be pointless
        if the proceed-path still destroyed the unmeasured repo's rows."""
        good = tmp_path / "good"
        _write_repo(good, {"handoff-widget-relay.md": DOC_FULL})
        ds = [hi.derive_repo(good, label="good"),
              hi.derive_repo(tmp_path / "gone", label="gone")]
        rows = [s for d in ds for s in d.sections]
        assert rows and ds[1].unmeasured == "no-such-directory"
        assert hi.rebuild_refusal(ds, rows) is None
        # …and the unmeasured repo's rows are NOT in the delete scope, so they
        # survive the rebuild rather than being silently replaced by nothing.
        labels = hi.rebuild_delete_labels(ds, ("good", "gone"), scoped=False)
        assert labels == ("good",)

    def test_a_partial_derivation_says_PARTIAL_INDEX_loudly(self, tmp_path):
        """🔴 PROCEEDING WITHOUT SAYING SO IS THE WORSE BUG. With the refusal
        relaxed, this warning is the only thing standing between "indexed what
        resolved" and a reader believing the index covers every configured repo.
        Pinned as a differential over the SAME renderer, so only the partiality
        can explain the two outputs."""
        good = tmp_path / "partialgood"
        _write_repo(good, {"handoff-widget-relay.md": DOC_FULL})
        ds = [hi.derive_repo(good, label="partialgood"),
              hi.derive_repo(tmp_path / "vanished", label="vanished")]

        partial = hi.render_derivation(ds)
        assert "🔴 PARTIAL INDEX" in partial
        assert "vanished (no-such-directory)" in partial
        assert "covers only: partialgood" in partial

        # The negative control: an all-measured run must NOT carry it, or the
        # assertion above proves only that the sentence is always printed.
        whole = hi.render_derivation([ds[0]])
        assert "PARTIAL INDEX" not in whole

    def test_an_ALL_unmeasured_derivation_is_still_refused(self, tmp_path):
        """The boundary the reversal did NOT move. Refusing on ANY unmeasured repo
        and refusing on ALL of them differ only when the set is mixed, so this
        pins the end of the range the relaxation must not have swallowed."""
        ds = [hi.derive_repo(tmp_path / "gone-a", label="gone-a"),
              hi.derive_repo(tmp_path / "gone-b", label="gone-b")]
        refusal = hi.rebuild_refusal(ds, [])
        assert refusal is not None
        assert "ALL 2 repo(s) came back UNMEASURED" in refusal
        assert "gone-a (no-such-directory)" in refusal

    def test_a_partial_derivation_with_no_rows_is_refused(self, tmp_path):
        """The other end: SOME repos measured, and what they measured is nothing.
        The relaxation keys on the unmeasured COUNT, so a partial run whose
        measured half is empty must still be caught by the zero-rows arm — with a
        message that no longer claims every repo resolved a ref."""
        empty = tmp_path / "partialempty"
        _write_repo(empty, {}, commit=False)
        subprocess.run(["git", "-C", str(empty), "commit", "-qm", "e", "--allow-empty"],
                       check=True)
        ds = [hi.derive_repo(empty, label="partialempty"),
              hi.derive_repo(tmp_path / "absent", label="absent")]
        refusal = hi.rebuild_refusal(ds, [])
        assert refusal is not None
        assert "ZERO rows from the 1 repo(s) that resolved" in refusal

    def test_a_partial_rebuild_WRITES_and_says_partial_through_main(self, tmp_path, capsys):
        """🔴 THE WHOLE REVERSAL, END TO END, THROUGH `main`. Before this change
        the identical argv exited 4 and opened no store at all; the recording
        connection is what proves the write now HAPPENS rather than merely that
        the exit code moved."""
        good = tmp_path / "e2egood"
        _write_repo(good, {"handoff-widget-relay.md": DOC_FULL})
        conn = RecordingConn()
        rc = hi.main(["--repo", str(good), "--repo", str(tmp_path / "e2egone"),
                      "--rebuild", "--write"], open_store=_recording_store(conn))
        assert rc == hi.RC_OK
        assert conn.kinds().count("INSERT") == 9
        out = capsys.readouterr()
        assert "🔴 THIS INDEX IS PARTIAL" in out.out
        assert "🔴 PARTIAL INDEX" in out.err
        # …and the delete named only the repo that measured.
        assert conn.params_for("DELETE") == [[["e2egood"]]]


class TestWriteTransaction:
    def test_the_delete_and_every_insert_share_ONE_transaction(self, tmp_path):
        """🔴 THE MUTANT-KILLER, AND THE ORDERING IS THE CLAIM.

        Three things are asserted, and each fails for its own mutation:
          * a DELETE is issued at all             (mutant: the delete -> `pass`)
          * it comes BEFORE every INSERT          (mutant: reordered)
          * there is NO commit between the DELETE and the last INSERT, and
            exactly ONE after it                  (mutant: the old two-commit
                                                   `truncate(); upsert()` shape)

        The schema DDL commits separately and BEFORE any of this — that is
        `ensure_schema`'s advisory-lock transaction and is not part of the data
        write — so the sequence is sliced from the DELETE onward."""
        repo = tmp_path / "txrepo"
        _write_repo(repo, {"handoff-widget-relay.md": DOC_FULL})
        conn = RecordingConn()
        rc = hi.main(["--repo", str(repo), "--rebuild", "--write"],
                     open_store=_recording_store(conn))
        assert rc == hi.RC_OK

        kinds = conn.kinds()
        assert "DELETE" in kinds, kinds
        tail = kinds[kinds.index("DELETE"):]
        n_inserts = tail.count("INSERT")
        assert n_inserts == 9, tail  # DOC_FULL's nine retrieval units
        # DELETE, then every INSERT, then exactly ONE commit — and nothing else.
        assert tail == ["DELETE", *(["INSERT"] * n_inserts), "COMMIT"], tail

    def test_a_write_without_rebuild_issues_no_delete(self, tmp_path):
        """The negative control for the same recorder: it must be capable of
        NOT seeing a delete, or the assertion above proves only that the word
        is always present."""
        repo = tmp_path / "notruncate"
        _write_repo(repo, {"handoff-widget-relay.md": DOC_FULL})
        conn = RecordingConn()
        rc = hi.main(["--repo", str(repo), "--write"], open_store=_recording_store(conn))
        assert rc == hi.RC_OK
        kinds = conn.kinds()
        assert "DELETE" not in kinds
        assert "TRUNCATE" not in kinds
        assert kinds.count("INSERT") == 9
        # Still one commit for the data write (plus ensure_schema's own).
        assert kinds[-1] == "COMMIT"


class TestScopedRebuildDelete:
    """F2 — a `--repo`-scoped `--rebuild --write` used to empty the WHOLE table."""

    def test_a_scoped_rebuild_deletes_ONLY_the_repo_it_was_pointed_at(self, tmp_path):
        """🔴 THE REPRODUCED DEFECT, AND IT REPORTED SUCCESS. Measured against
        this exact class over a recording connection:
        `handoff_index.py --repo ~/workspace/devrc --rebuild --write` issued
        `TRUNCATE initiatives.handoff_section` — no predicate, every repo —
        re-inserted only devrc, printed `wrote 968 section row(s) … (after
        TRUNCATE, one transaction)` and exited 0. homelab-talos's ~515 sections
        were gone.

        The assertion is on the BOUND SCOPE, not on the verb. A DELETE with a
        WHERE clause bound to every stored label is the same data loss spelled
        differently, and a test that only checked for the word `DELETE` would pass
        for it."""
        repo = tmp_path / "onlyme"
        _write_repo(repo, {"handoff-widget-relay.md": DOC_FULL})

        conn = RecordingConn()
        rc = hi.main(["--repo", str(repo), "--rebuild", "--write"],
                     open_store=_recording_store(conn))
        assert rc == hi.RC_OK
        stmts = [s for s in conn.statements() if s.startswith(("DELETE", "TRUNCATE"))]
        assert len(stmts) == 1, stmts
        assert stmts[0] == "DELETE FROM initiatives.handoff_section WHERE repo = ANY(%s)"
        # 🔴 THE SCOPE ITSELF. `onlyme` and nothing else.
        assert conn.params_for("DELETE") == [[["onlyme"]]]
        # …and the success line no longer advertises a TRUNCATE that is not run.
        assert "TRUNCATE" not in "\n".join(conn.statements())

    def test_the_scope_is_the_MEASURED_labels_not_every_derived_one(self, tmp_path):
        """A repo that could not be READ is not a repo whose rows this run may
        destroy — that is what makes the relaxed refusal safe. Differential over
        one derivation list, so only the `unmeasured` flag can explain it."""
        good = tmp_path / "measured"
        _write_repo(good, {"handoff-widget-relay.md": DOC_FULL})
        ds = [hi.derive_repo(good, label="measured"),
              hi.derive_repo(tmp_path / "unmeasured", label="unmeasured")]
        assert hi.rebuild_delete_labels(ds, (), scoped=True) == ("measured",)
        assert hi.rebuild_delete_labels(ds, (), scoped=False) == ("measured",)

    def _renamed_checkout_fixture(self, tmp_path):
        """🔴 THE FIXTURE IS THE POINT, AND THE OLD ONE STRUCTURALLY COULD NOT FAIL.

        This shape used `cantread` and `dropped-from-config` as DISJOINT
        identities, so no configured label could ever be a second spelling of a
        stored one and the collision below was unreachable from it. The values
        here are pairwise distinct AND two of them are two spellings of ONE repo:

          zarfrepo            measured, real, has docs
          plimforth-renamed   CONFIGURED (a renamed checkout), UNMEASURED
          plimforth           STORED under the repo's OLD label — the SAME repo
          wibbleton-retired   STORED, genuinely gone from the config

        `plimforth` is therefore "not configured at all" (nothing named
        `plimforth` is in the config) while ALSO being "configured but
        unmeasured" (as `plimforth-renamed`), which is the contradiction the
        delete scope used to resolve in the destructive direction. Returned as a
        helper so several tests read the SAME state rather than each inventing a
        near-miss of it."""
        good = tmp_path / "zarfrepo"
        _write_repo(good, {"handoff-widget-relay.md": DOC_FULL})
        ds = [hi.derive_repo(good, label="zarfrepo"),
              hi.derive_repo(tmp_path / "plimforth-renamed", label="plimforth-renamed")]
        stored = ("zarfrepo", "plimforth", "wibbleton-retired")
        return ds, stored

    def test_a_FULL_run_never_deletes_a_repo_it_was_not_told_about(self, tmp_path):
        """🔴 A DELIBERATE REVERSAL, AND THE OPERATOR DECIDED IT. The previous
        round's asymmetry — configured-but-UNMEASURED preserved, NOT-CONFIGURED-
        at-all collected — was defensible on its own terms (a repo dropped from
        the env handles would otherwise stay indexed forever) and wrong in
        practice, twice over:

          * `nix/home.nix`'s unit sets only $DEVRC and $HOMELAB while a human
            dry-run on this host measures FOUR repos, so an armed timer's
            unscoped rebuild would delete ~62% of the corpus every 6h at exit 0;
          * and CONFIGURED and STORED are two spellings that can disagree — see
            `_renamed_checkout_fixture`.

        So the collection is behind an explicit `--prune`. Three-way differential
        over ONE stored set, and the stored set contains a genuine orphan
        (`wibbleton-retired`) so this cannot pass by the fixture having nothing
        to collect."""
        ds, stored = self._renamed_checkout_fixture(tmp_path)

        full = hi.rebuild_delete_labels(ds, stored, scoped=False)
        assert full == ("zarfrepo",)
        assert "wibbleton-retired" not in full     # the genuine orphan: kept
        assert "plimforth" not in full             # the renamed repo: kept
        assert "plimforth-renamed" not in full     # unmeasured: kept, as before

        scoped = hi.rebuild_delete_labels(ds, stored, scoped=True)
        assert scoped == ("zarfrepo",)

        # …and `--prune` is what an operator uses instead. It is refused on THIS
        # derivation (an unmeasured repo is present), which is the collision
        # guard; the collection itself is asserted on a fully-measured one below.
        assert hi.rebuild_refusal(ds, [s for d in ds for s in d.sections],
                                  prune=True) is not None

    def test_PRUNE_is_what_collects_an_orphan_and_it_needs_every_repo_MEASURED(
            self, tmp_path):
        """The positive control for `--prune`: with every configured repo
        measured, it DOES collect the label the config no longer names. Without
        this, the test above proves only that the collection was deleted rather
        than moved behind a flag.

        The differential is one argument on one call, so nothing but `prune` can
        explain the two answers."""
        good = tmp_path / "zarfrepo"
        _write_repo(good, {"handoff-widget-relay.md": DOC_FULL})
        other = tmp_path / "trundlerepo"
        _write_repo(other, {"handoff-cable-audit.md": DOC_SPARSE})
        ds = [hi.derive_repo(good, label="zarfrepo"),
              hi.derive_repo(other, label="trundlerepo")]
        stored = ("zarfrepo", "trundlerepo", "wibbleton-retired")
        rows = [s for d in ds for s in d.sections]

        assert hi.rebuild_refusal(ds, rows, prune=True) is None
        assert hi.rebuild_delete_labels(ds, stored, scoped=False, prune=True) == (
            "trundlerepo", "wibbleton-retired", "zarfrepo")
        assert hi.rebuild_delete_labels(ds, stored, scoped=False, prune=False) == (
            "trundlerepo", "zarfrepo")

    def test_a_PRUNE_over_an_UNMEASURED_repo_is_REFUSED_by_name(self, tmp_path):
        """🔴 THE COLLISION GUARD, AND ITS MESSAGE HAS TO SAY WHY. `--prune`
        asserts that the config's spelling of every repo is the table's spelling.
        An unmeasured repo is exactly where those can differ, so the run refuses
        rather than guessing which of "renamed" and "removed" it is looking at —
        the two have opposite correct actions and one of them destroys rows.

        Differential against the SAME derivations without `--prune`, which must
        proceed: a refusal that fired either way would be the permanently-red
        gate `rebuild_refusal` already had to unwind once."""
        ds, _ = self._renamed_checkout_fixture(tmp_path)
        rows = [s for d in ds for s in d.sections]

        refusal = hi.rebuild_refusal(ds, rows, prune=True)
        assert refusal is not None
        assert "REFUSING --rebuild --prune" in refusal
        assert "plimforth-renamed (no-such-directory)" in refusal
        assert "1 of 2 repo(s)" in refusal

        assert hi.rebuild_refusal(ds, rows, prune=False) is None

    def test_an_orphaned_label_is_REPORTED_rather_than_silently_left(self, tmp_path):
        """🔴 THE OTHER HALF OF REMOVING THE IMPLICIT PRUNE. Not deleting an
        orphan silently would only trade a data-loss bug for a stale-corpus bug —
        rows nothing refreshes that every query still answers from. So the set is
        reported, with the command that removes it.

        A SCOPED run reports none: it was told what to look at, never what the
        config is, so it cannot answer 'not configured'."""
        ds, stored = self._renamed_checkout_fixture(tmp_path)

        orphans = hi.orphan_labels(ds, stored, scoped=False)
        assert orphans == ("plimforth", "wibbleton-retired")
        assert hi.orphan_labels(ds, stored, scoped=True) == ()

        warning = hi.orphan_label_warning(orphans)
        assert len(warning) == 1
        assert "⚠ ORPHANED LABELS" in warning[0]
        assert "plimforth, wibbleton-retired" in warning[0]
        assert "--rebuild --prune --write" in warning[0]
        assert "They are NOT deleted" in warning[0]
        # The negative control: no orphans, no sentence. A per-run "0 orphaned"
        # buries the real ones.
        assert hi.orphan_label_warning(()) == ()

    def test_a_rebuild_with_an_EMPTY_scope_raises_rather_than_wiping(self):
        """🔴 THE LIBRARY-LEVEL BELT. `main` cannot reach this — the refusal guard
        guarantees at least one measured repo before a write — but `write()` is
        public and a future caller that forgets the scope must not get the
        whole-table wipe as the default. Reachable by construction, and it is the
        DELETE that must not have run: the assertion is on the recorder, not only
        on the raise."""
        conn = RecordingConn()
        store = hi.PostgresSectionStore(conn)
        row = hi.Section("r", "s", "p", None, None, "goal", 0, "h", "b")
        with pytest.raises(ValueError, match="EMPTY delete scope"):
            store.write([row], rebuild=True)
        assert conn.statements() == []
        assert conn.log == []

    def test_the_insert_is_an_upsert_on_the_tables_unique_identity(self, tmp_path):
        repo = tmp_path / "upsertrepo"
        _write_repo(repo, {"handoff-widget-relay.md": DOC_FULL})
        conn = RecordingConn()
        hi.main(["--repo", str(repo), "--write"], open_store=_recording_store(conn))
        inserts = [s for s in conn.statements() if s.startswith("INSERT")]
        assert inserts
        assert all("ON CONFLICT (repo, slug, section, ordinal) DO UPDATE" in s
                   for s in inserts)
        # The identity columns are never in the SET list — updating them would
        # move a row's identity rather than refresh its payload.
        for col in ("repo", "slug", "section", "ordinal"):
            assert f"{col} = EXCLUDED.{col}" not in inserts[0]
        for col in ("doc_path", "body", "heading", "doc_date"):
            assert f"{col} = EXCLUDED.{col}" in inserts[0]


class TestTheTimersShapeDeletesOnlyWhatItWasToldAbout:
    """🔴 F2 — the unit's own ENVIRONMENT made an armed timer delete 62% of the
    corpus every 6h at exit 0, and every existing test was blind to it because the
    fake table was empty."""

    def test_an_unscoped_rebuild_through_main_deletes_no_stored_orphan(self, tmp_path):
        """🔴 THE REPRODUCTION, END TO END, AGAINST A TABLE THAT HOLDS ROWS.

        The unit runs `--rebuild --write` UNSCOPED with only two handles in its
        environment, on a host whose corpus spans four repos. So from inside the
        unit the other two repos are "labels the config no longer names", and the
        old scope collected them: measured on this host's real numbers, ~2,476 of
        ~4,008 sections deleted per tick, printing `## warnings: none` above it
        because from that environment nothing is UNMEASURED — no PARTIAL INDEX
        fires, and there is nothing in the output to read as a warning.

        The assertion is on the BOUND SCOPE of the DELETE. That is the only place
        the difference shows: the statement text, the exit code, the row count and
        the warning block are all identical between the correct run and the
        destructive one."""
        indexed = tmp_path / "zarfrepo"
        _write_repo(indexed, {"handoff-widget-relay.md": DOC_FULL})
        # The table already holds two repos this run's config does not name —
        # the shape the unit's two-handle environment creates.
        conn = StoredReposConn(("zarfrepo", "trundlerepo", "marganserrepo"))
        rc = hi.main(["--repo", str(indexed)], open_store=_recording_store(conn))
        assert rc == hi.RC_OK  # a non-rebuild write: no delete at all

        conn2 = StoredReposConn(("zarfrepo", "trundlerepo", "marganserrepo"))
        rc2 = hi.main(["--repo", str(indexed), "--rebuild", "--write"],
                      open_store=_recording_store(conn2))
        assert rc2 == hi.RC_OK
        assert conn2.params_for("DELETE") == [[["zarfrepo"]]]

    def test_the_unscoped_default_path_is_the_one_that_regressed(self, tmp_path,
                                                                 monkeypatch):
        """The same claim on the UNSCOPED path — the timer's actual argv shape.
        `--repo` makes a run scoped, and a scoped run was never the bug; driving
        only the scoped path would leave the regressing branch untested.

        `default_repos()` is steered through the env handles, which is exactly the
        surface the incident was about."""
        indexed = tmp_path / "zarfrepo"
        _write_repo(indexed, {"handoff-widget-relay.md": DOC_FULL})
        for handle in hi.REPO_ENV_HANDLES:
            monkeypatch.delenv(handle, raising=False)
        monkeypatch.setenv("DEVRC", str(indexed))

        conn = StoredReposConn(("zarfrepo", "trundlerepo", "marganserrepo"))
        rc = hi.main(["--rebuild", "--write"], open_store=_recording_store(conn))
        assert rc == hi.RC_OK
        # 🔴 NOT ("marganserrepo", "trundlerepo", "zarfrepo") — which is what this
        # returned before, with no warning and exit 0.
        assert conn.params_for("DELETE") == [[["zarfrepo"]]]

    def test_the_orphans_it_did_not_delete_are_named_on_stderr(self, tmp_path, capsys):
        """Leaving them silently would trade data loss for a stale corpus. The
        write's own output has to say the table holds labels nothing will refresh,
        and name the one command that removes them."""
        indexed = tmp_path / "zarfrepo"
        _write_repo(indexed, {"handoff-widget-relay.md": DOC_FULL})
        for handle in hi.REPO_ENV_HANDLES:
            os.environ.pop(handle, None)
        os.environ["DEVRC"] = str(indexed)
        try:
            conn = StoredReposConn(("zarfrepo", "trundlerepo"))
            rc = hi.main(["--rebuild", "--write"], open_store=_recording_store(conn))
        finally:
            os.environ.pop("DEVRC", None)
        err = capsys.readouterr().err
        assert rc == hi.RC_OK
        assert "⚠ ORPHANED LABELS" in err
        assert "trundlerepo" in err
        assert "--rebuild --prune --write" in err

    def test_a_SCOPED_run_reports_no_orphans_because_it_cannot_know(self, tmp_path,
                                                                    capsys):
        """The negative control for the report: a `--repo` run was told what to
        LOOK at, never what the config is, so every label outside its argv would
        read as an orphan. Silence there is correct, and it is what proves the
        sentence above is conditional rather than unconditional."""
        indexed = tmp_path / "zarfrepo"
        _write_repo(indexed, {"handoff-widget-relay.md": DOC_FULL})
        conn = StoredReposConn(("zarfrepo", "trundlerepo"))
        rc = hi.main(["--repo", str(indexed), "--rebuild", "--write"],
                     open_store=_recording_store(conn))
        assert rc == hi.RC_OK
        assert "ORPHANED LABELS" not in capsys.readouterr().err


class TestThePartialPromiseIsKept:
    """🔴 F1 — a SEAM guard, not a component one. `partial_scope_warnings` printed
    'Their existing rows are left untouched (the rebuild delete is scoped to what
    MEASURED)' while `rebuild_delete_labels`, in the same transaction, put those
    rows in the DELETE. Both functions were individually tested and individually
    correct by their own docstrings; the defect lived only in the RELATIONSHIP,
    because one reasons over CONFIGURED labels and the other over STORED ones."""

    def test_the_warning_and_the_delete_scope_cannot_contradict_each_other(
            self, tmp_path):
        """The relationship, pinned directly: whenever the PARTIAL sentence is
        printed and a write is allowed, the delete scope is a subset of the
        MEASURED labels — so no repo the sentence speaks for can be in it.

        Driven over the renamed-checkout state, which is the one that broke it."""
        good = tmp_path / "zarfrepo"
        _write_repo(good, {"handoff-widget-relay.md": DOC_FULL})
        ds = [hi.derive_repo(good, label="zarfrepo"),
              hi.derive_repo(tmp_path / "plimforth-renamed", label="plimforth-renamed")]
        stored = ("zarfrepo", "plimforth", "wibbleton-retired")
        rows = [s for d in ds for s in d.sections]
        measured = {d.label for d in ds if d.unmeasured is None}

        partial = hi.partial_scope_warnings(ds)
        assert partial, "the fixture must actually be partial, or this pins nothing"
        assert "left untouched" in partial[0]

        for scoped in (True, False):
            for prune in (True, False):
                if hi.rebuild_refusal(ds, rows, prune=prune) is not None:
                    continue  # this run never reaches a write; the promise holds
                scope = set(hi.rebuild_delete_labels(
                    ds, stored, scoped=scoped, prune=prune))
                assert scope <= measured, (scoped, prune, scope)

    def test_the_two_spellings_of_one_repo_no_longer_decide_a_delete(self, tmp_path):
        """The concrete case, named: the table's `plimforth` and the config's
        `plimforth-renamed` are the same repo. `plimforth` is 'not configured at
        all' AND `plimforth-renamed` is 'configured but unmeasured', and the old
        scope resolved that contradiction by deleting.

        Asserted as a differential on `prune` alone — the same derivations, the
        same stored set, one argument apart — so nothing else can explain it."""
        good = tmp_path / "zarfrepo"
        _write_repo(good, {"handoff-widget-relay.md": DOC_FULL})
        ds = [hi.derive_repo(good, label="zarfrepo"),
              hi.derive_repo(tmp_path / "plimforth-renamed", label="plimforth-renamed")]
        stored = ("zarfrepo", "plimforth")

        assert "plimforth" not in hi.rebuild_delete_labels(ds, stored, scoped=False)
        # …and the path that WOULD collect it is refused before it can.
        rows = [s for d in ds for s in d.sections]
        assert hi.rebuild_refusal(ds, rows, prune=True) is not None

    def test_the_collision_state_through_main_deletes_only_the_measured_repo(
            self, tmp_path, capsys):
        """End to end, because the two functions meet in `main` and nowhere else.
        The recording connection is what shows the BOUND scope — the statement
        text, the exit code and the row count are identical either way."""
        good = tmp_path / "zarfrepo"
        _write_repo(good, {"handoff-widget-relay.md": DOC_FULL})
        for handle in hi.REPO_ENV_HANDLES:
            os.environ.pop(handle, None)
        os.environ["DEVRC"] = str(good)
        os.environ["HOMELAB"] = str(tmp_path / "plimforth-renamed")
        try:
            conn = StoredReposConn(("zarfrepo", "plimforth"))
            rc = hi.main(["--rebuild", "--write"], open_store=_recording_store(conn))
        finally:
            for handle in ("DEVRC", "HOMELAB"):
                os.environ.pop(handle, None)
        cap = capsys.readouterr()
        assert rc == hi.RC_OK
        assert conn.params_for("DELETE") == [[["zarfrepo"]]]
        # The sentence that was false is printed, and now it is true.
        assert "🔴 PARTIAL INDEX" in cap.err
        assert "left untouched" in cap.err


class TestPruneIsAnOperatorActNotAFlagYouCanDriftInto:
    def test_prune_without_rebuild_is_a_usage_error(self, tmp_path, capsys):
        """A destructive flag that silently does nothing in one argv and fires in
        the next is how it becomes decoration. Rejected, never reinterpreted."""
        repo = tmp_path / "zarfrepo"
        _write_repo(repo, {"handoff-widget-relay.md": DOC_FULL})
        rc = hi.main(["--repo", str(repo), "--prune", "--write"],
                     open_store=_refusing_store())
        assert rc == hi.RC_USAGE
        assert "--prune only widens a --rebuild" in capsys.readouterr().err

    def test_prune_with_an_explicit_repo_is_a_usage_error(self, tmp_path, capsys):
        """🔴 THE DANGEROUS COMBINATION, AND IT IS REFUSED RATHER THAN NARROWED.
        `--prune` deletes every stored label THE CONFIG does not name; a `--repo`
        run was never told what the config is. Treating its argv as the config
        would delete every repo the caller did not happen to list — a wider
        version of the very bug this round is fixing."""
        repo = tmp_path / "zarfrepo"
        _write_repo(repo, {"handoff-widget-relay.md": DOC_FULL})
        rc = hi.main(["--repo", str(repo), "--rebuild", "--prune", "--write"],
                     open_store=_refusing_store())
        assert rc == hi.RC_USAGE
        assert "--prune and --repo contradict each other" in capsys.readouterr().err

    def test_a_prune_run_over_an_unmeasured_repo_is_refused_through_main(
            self, tmp_path, capsys):
        """The refusal reaches the exit code, and the store is never opened."""
        good = tmp_path / "zarfrepo"
        _write_repo(good, {"handoff-widget-relay.md": DOC_FULL})
        for handle in hi.REPO_ENV_HANDLES:
            os.environ.pop(handle, None)
        os.environ["DEVRC"] = str(good)
        os.environ["HOMELAB"] = str(tmp_path / "plimforth-renamed")
        try:
            rc = hi.main(["--rebuild", "--prune", "--write"],
                         open_store=_refusing_store())
        finally:
            for handle in ("DEVRC", "HOMELAB"):
                os.environ.pop(handle, None)
        assert rc == hi.RC_REFUSED
        assert "REFUSING --rebuild --prune" in capsys.readouterr().err

    def test_the_timers_argv_does_not_carry_prune(self):
        """The whole point of the flag is that the SCHEDULED run does not pass it.
        Read out of `nix/home.nix`, so a future edit to the unit's ExecStart that
        adds it fails here rather than 6h later."""
        block = _handoff_unit_block()
        execstart = [ln for ln in block.splitlines() if "ExecStart" in ln]
        assert len(execstart) == 1, execstart
        assert "handoff_index.py --rebuild --write" in execstart[0]
        assert "--prune" not in execstart[0]


class TestTheUnitEnvironmentMatchesTheHandlesTheIndexerReads:
    """🔴 F2's DEFENCE IN DEPTH. The Python fix stops a rebuild deleting a repo it
    was not told about; this stops the unit and the module DISAGREEING about which
    repos exist in the first place, which is what made the delete destructive."""

    def test_every_handle_the_indexer_reads_is_exported_by_the_unit(self):
        """The unit hardcoded two handles (`DEVRC`, `HOMELAB`) while
        `REPO_ENV_HANDLES` names four, so `default_repos()` inside the unit saw a
        different config from the one a human sees — silently, since a handle that
        is simply UNSET contributes nothing and produces no warning at all.

        It now derives its Environment from `nix/agent-handles.nix`, the same file
        `programs/zsh` and the opencode plugin read. Pinned as a relationship
        between the two files rather than as a literal list, so adding a handle to
        either side fails here instead of silently narrowing the unit's view."""
        handles = (REPO_ROOT / "nix" / "agent-handles.nix").read_text()
        repos_block = handles.split("repos = {")[1].split("};")[0]
        declared = set(re.findall(r"^\s*([A-Z][A-Z0-9_]*)\s*=", repos_block, re.M))
        assert declared, "the repos block must parse, or this test measures nothing"

        # Every handle the module reads must be one nix exports…
        missing = set(hi.REPO_ENV_HANDLES) - declared
        assert not missing, (
            f"{sorted(missing)} is read by handoff_index.REPO_ENV_HANDLES but is not "
            f"declared in nix/agent-handles.nix, so the systemd unit cannot export it "
            f"and the unit's view of the config differs from a human's."
        )

        # …and the unit must export the whole block rather than a hand-picked
        # subset, which is the state that caused the incident.
        env = _handoff_unit_block().split("Environment = [")[1].split("ExecStart")[0]
        assert "agent-handles.nix" in env, (
            "the handoff-index-sync unit must DERIVE its repo handles from "
            "nix/agent-handles.nix, not list them — a hand-picked subset is what "
            "made an unscoped rebuild delete the repos it could not see."
        )
        for handle in hi.REPO_ENV_HANDLES:
            assert f'"{handle}=' not in env, (
                f"{handle} is hardcoded in the unit's Environment beside the derived "
                f"block; two sources for one fact is how they drifted before."
            )


class TestTheDryRunShowsWhatTheRebuildDeletes:
    """🔴 F2's aggravator: the delete scope was computed only inside the `--write`
    branch, so the pre-flight `nix/home.nix` tells an operator to watch was
    structurally incapable of showing the one thing the run destroys."""

    def test_a_dry_run_prints_the_complete_delete_scope(self, tmp_path, capsys):
        good = tmp_path / "zarfrepo"
        _write_repo(good, {"handoff-widget-relay.md": DOC_FULL})
        for handle in hi.REPO_ENV_HANDLES:
            os.environ.pop(handle, None)
        os.environ["DEVRC"] = str(good)
        os.environ["HOMELAB"] = str(tmp_path / "plimforth-renamed")
        try:
            rc = hi.main(["--rebuild"], open_store=_refusing_store())
        finally:
            for handle in ("DEVRC", "HOMELAB"):
                os.environ.pop(handle, None)
        out = capsys.readouterr().out
        assert rc == hi.RC_OK
        assert "## rebuild delete scope" in out
        assert "DELETE (measured, will be re-derived): zarfrepo" in out
        assert "KEPT (configured but UNMEASURED): plimforth-renamed" in out
        assert "This list is the COMPLETE delete scope." in out

    def test_a_dry_run_WITH_prune_names_its_own_blind_spot(self, tmp_path, capsys):
        """⚠ THE HALF THAT IS STATED, NOT CLOSED. `--prune`'s extra set lives in
        the TABLE, and a dry-run opens none. Printing a scope that silently omits
        those rows would be a pre-flight that under-reports what the run deletes,
        which is worse than one that names its blind spot."""
        good = tmp_path / "zarfrepo"
        _write_repo(good, {"handoff-widget-relay.md": DOC_FULL})
        for handle in hi.REPO_ENV_HANDLES:
            os.environ.pop(handle, None)
        os.environ["DEVRC"] = str(good)
        try:
            rc = hi.main(["--rebuild", "--prune"], open_store=_refusing_store())
        finally:
            os.environ.pop("DEVRC", None)
        out = capsys.readouterr().out
        assert rc == hi.RC_OK
        assert "a --dry-run CANNOT show it" in out
        assert "This list is the COMPLETE delete scope." not in out

    def test_a_non_rebuild_run_prints_no_delete_scope_at_all(self, tmp_path, capsys):
        """The negative control: a run that deletes nothing must not print a
        section about a delete, or the assertions above prove only that the block
        is unconditional."""
        good = tmp_path / "zarfrepo"
        _write_repo(good, {"handoff-widget-relay.md": DOC_FULL})
        rc = hi.main(["--repo", str(good)], open_store=_refusing_store())
        assert rc == hi.RC_OK
        assert "rebuild delete scope" not in capsys.readouterr().out


class TestAFailedLsTreeIsNotAnEmptyRef:
    """🔴 F5 — `_git` returns None on a non-zero rc and `handoff_paths_in_ref`
    folded that into `()`, so a git failure and a genuinely empty mainline were
    ONE value. Survivable while it only affected a count; not survivable once that
    same MEASURED set decides what a `--rebuild` may DELETE."""

    def test_the_two_mechanisms_return_different_values(self, tmp_path):
        """The distinction at its source, as a natural differential — a real repo
        whose mainline holds no handoff docs, versus a ref that does not exist.
        No monkeypatching: `git ls-tree <missing-ref>` genuinely exits non-zero."""
        repo = tmp_path / "bareish"
        _write_repo(repo, {}, commit=False)
        subprocess.run(["git", "-C", str(repo), "commit", "-qm", "e", "--allow-empty"],
                       check=True)
        assert hi.handoff_paths_in_ref(repo, "main") == ()
        assert hi.handoff_paths_in_ref(repo, "refs/heads/no-such-branch") is None

    def test_a_failed_ls_tree_makes_the_repo_UNMEASURED(self, tmp_path, monkeypatch):
        """`derive_repo` must turn the `None` into a STRUCTURAL unmeasured reason.
        The mainline resolves, so nothing earlier can catch this; the failure is
        injected at `_git` and ONLY for `ls-tree`, so the repo is otherwise a
        perfectly ordinary measured one — which is the differential."""
        repo = tmp_path / "zarfrepo"
        _write_repo(repo, {"handoff-widget-relay.md": DOC_FULL})
        assert hi.derive_repo(repo, label="zarfrepo").unmeasured is None  # control

        real_git = hi._git

        def failing_ls_tree(r, args):
            return None if args and args[0] == "ls-tree" else real_git(r, args)

        monkeypatch.setattr(hi, "_git", failing_ls_tree)
        d = hi.derive_repo(repo, label="zarfrepo")
        assert d.unmeasured == "ls-tree-failed"
        assert d.docs == 0 and d.sections == []
        assert any("ls-tree" in w and "UNMEASURED" in w for w in d.warnings), d.warnings
        assert any("NOT 'the repo has no handoff docs'" in w for w in d.warnings)

    def test_a_repo_whose_ls_tree_failed_is_not_in_the_delete_scope(
            self, tmp_path, monkeypatch):
        """🔴 THE CONSEQUENCE THAT MAKES IT URGENT. Before this, the repo landed
        in the MEASURED set with `docs=0` and no warning, so `--rebuild` deleted
        its rows, re-inserted none, and exited 0 — an index silently emptied for
        one repo by a transient git failure."""
        good = tmp_path / "zarfrepo"
        _write_repo(good, {"handoff-widget-relay.md": DOC_FULL})
        blind = tmp_path / "trundlerepo"
        _write_repo(blind, {"handoff-cable-audit.md": DOC_SPARSE})

        real_git = hi._git

        def failing_for_blind(r, args):
            if args and args[0] == "ls-tree" and Path(r).name == "trundlerepo":
                return None
            return real_git(r, args)

        monkeypatch.setattr(hi, "_git", failing_for_blind)
        ds = [hi.derive_repo(good, label="zarfrepo"),
              hi.derive_repo(blind, label="trundlerepo")]
        assert ds[0].unmeasured is None and ds[1].unmeasured == "ls-tree-failed"
        labels = hi.rebuild_delete_labels(ds, ("zarfrepo", "trundlerepo"), scoped=False)
        assert labels == ("zarfrepo",)
        assert "trundlerepo" not in labels


class TestTheJsonSurfaceIsParseable:
    def test_json_and_write_leave_stdout_pure(self, tmp_path, capsys):
        """🔴 `--json --write` EMITTED JSON AND THEN PROSE ON ONE STREAM, so
        `json.loads(stdout)` raised on a run that had worked perfectly — the
        machine surface, the one consumed without a human reading it, was the one
        that could not be parsed. Asserted by PARSING, not by grepping for the
        absence of a word: a second document appended after the first would pass a
        substring check and still break every consumer."""
        repo = tmp_path / "zarfrepo"
        _write_repo(repo, {"handoff-widget-relay.md": DOC_FULL})
        conn = RecordingConn()
        rc = hi.main(["--repo", str(repo), "--rebuild", "--write", "--json"],
                     open_store=_recording_store(conn))
        cap = capsys.readouterr()
        assert rc == hi.RC_OK
        payload = json.loads(cap.out)
        assert payload["totals"]["sections"] == 9
        # …and the prose is not lost, it is on stderr where a human still sees it.
        assert "wrote 9 section row(s)" in cap.err
        assert "## rebuild delete scope" in cap.err

    def test_the_text_mode_still_prints_the_prose_on_stdout(self, tmp_path, capsys):
        """The negative control: without `--json` nothing moved, or the assertion
        above would be satisfied by a change that simply stopped printing."""
        repo = tmp_path / "zarfrepo"
        _write_repo(repo, {"handoff-widget-relay.md": DOC_FULL})
        conn = RecordingConn()
        rc = hi.main(["--repo", str(repo), "--rebuild", "--write"],
                     open_store=_recording_store(conn))
        cap = capsys.readouterr()
        assert rc == hi.RC_OK
        assert "wrote 9 section row(s)" in cap.out
        assert "## rebuild delete scope" in cap.out


class TestDryRunIsTheDefault:
    def test_a_bare_invocation_writes_nothing(self, tmp_path, capsys):
        """🔴 `main([...])` WITH NO FLAGS USED TO UPSERT EVERY ROW TO PRODUCTION.
        The store seam raises if opened, so this asserts the write did not
        happen — not merely that the exit code was 0."""
        repo = tmp_path / "defaultrepo"
        _write_repo(repo, {"handoff-widget-relay.md": DOC_FULL})
        rc = hi.main(["--repo", str(repo)], open_store=_refusing_store())
        assert rc == hi.RC_OK
        out = capsys.readouterr().out
        assert "no --write: nothing was written" in out
        # …and it still DID the work it is useful for.
        assert "docs=1" in out

    def test_the_explicit_dry_run_flag_agrees_with_the_default(self, tmp_path, capsys):
        repo = tmp_path / "dryrepo"
        _write_repo(repo, {"handoff-widget-relay.md": DOC_FULL})
        assert hi.main(["--repo", str(repo), "--dry-run"],
                       open_store=_refusing_store()) == hi.RC_OK
        assert "nothing was written" in capsys.readouterr().out

    def test_write_and_dry_run_together_is_a_usage_error(self, tmp_path, capsys):
        """Contradictory, not a preference. Guessing which one was meant is how
        a 'dry run' writes."""
        rc = hi.main(["--repo", "/nonexistent/x", "--write", "--dry-run"],
                     open_store=_refusing_store())
        assert rc == hi.RC_USAGE
        assert "contradict" in capsys.readouterr().err

    def test_no_repos_at_all_is_a_usage_error_naming_the_handles(self, monkeypatch, capsys):
        for handle in hi.REPO_ENV_HANDLES:
            monkeypatch.delenv(handle, raising=False)
        rc = hi.main([], open_store=_refusing_store())
        assert rc == hi.RC_USAGE
        err = capsys.readouterr().err
        for handle in hi.REPO_ENV_HANDLES:
            assert f"${handle}" in err

    def test_default_repos_reads_the_handles_that_are_set(self, monkeypatch, tmp_path):
        """`default_repos` was reached by no test. Exercised as a differential:
        one handle set, the rest cleared, so only the set one may appear."""
        for handle in hi.REPO_ENV_HANDLES:
            monkeypatch.delenv(handle, raising=False)
        assert hi.default_repos() == []
        target = tmp_path / "grumblesnitch"
        target.mkdir()
        monkeypatch.setenv(hi.REPO_ENV_HANDLES[0], str(target))
        assert hi.default_repos() == [(str(target), "grumblesnitch")]

    def test_the_json_surface_emits_the_derived_rows(self, tmp_path, capsys):
        repo = tmp_path / "jsonrepo"
        _write_repo(repo, {"handoff-widget-relay.md": DOC_FULL})
        assert hi.main(["--repo", str(repo), "--json"],
                       open_store=_refusing_store()) == hi.RC_OK
        payload = json.loads(capsys.readouterr().out.split("\n(no --write")[0])
        assert len(payload["rows"]) == 9
        assert {r["section"] for r in payload["rows"]} == set(hi.SECTIONS)

    def test_the_json_surface_carries_the_WARNINGS_the_text_one_prints(
            self, tmp_path, capsys):
        """🔴 THE WARNING BLOCK LIVED ONLY IN `render_derivation`, WHICH `--json`
        REPLACES. So the surface built FOR AN AGENT — the consumer least able to
        notice the omission — got rows and no durability-hole report at all.
        Measured as a differential over ONE repo carrying a real hole, through
        both renderers, so only the surface can explain the difference."""
        repo = tmp_path / "jsonwarn"
        _write_repo(repo, {"handoff-widget-relay.md": DOC_FULL})
        (repo / hi.HANDOFF_DIR / "handoff-cable-audit.md").write_text(DOC_SPARSE)

        text = hi.render_derivation([hi.derive_repo(repo, label="jsonwarn")])
        assert "DURABILITY HOLE" in text

        assert hi.main(["--repo", str(repo), "--json"],
                       open_store=_refusing_store()) == hi.RC_OK
        payload = json.loads(capsys.readouterr().out.split("\n(no --write")[0])
        assert any("DURABILITY HOLE" in w for w in payload["warnings"])
        assert payload["all_clear"] is False
        assert payload["repos"][0]["untracked"] == ["claudedocs/handoff-cable-audit.md"]

    def test_the_json_all_clear_is_a_BOOLEAN_a_caller_can_branch_on(
            self, tmp_path, capsys):
        """The positive control for the field: it must be reachable as True, or
        the assertion above pins a constant. And when it is False without any
        warning — nothing examined — the blockers say WHICH measurement is
        missing, which is the distinction the text renderer draws in prose."""
        clean = tmp_path / "jsonclean"
        _write_repo(clean, {"handoff-widget-relay.md": DOC_FULL})
        assert hi.main(["--repo", str(clean), "--json"],
                       open_store=_refusing_store()) == hi.RC_OK
        payload = json.loads(capsys.readouterr().out.split("\n(no --write")[0])
        assert payload["warnings"] == []
        assert payload["all_clear"] is True
        assert payload["all_clear_blockers"] == []
        assert payload["repos"][0]["disk_scan_complete"] is True
        assert payload["repos"][0]["disk_paths_seen"] == 1

        empty = tmp_path / "jsonempty"
        _write_repo(empty, {}, commit=False)
        subprocess.run(["git", "-C", str(empty), "commit", "-qm", "e", "--allow-empty"],
                       check=True)
        assert hi.main(["--repo", str(empty), "--json"],
                       open_store=_refusing_store()) == hi.RC_OK
        payload = json.loads(capsys.readouterr().out.split("\n(no --write")[0])
        assert payload["warnings"] == []
        assert payload["all_clear"] is False
        assert payload["all_clear_blockers"]

    def test_a_dry_run_evaluates_the_SAME_refusal_the_write_run_does(
            self, tmp_path, capsys):
        """🔴 THE DOCUMENTED PRE-FLIGHT PASSED FOR A CONFIG THE REAL RUN REFUSES.
        `nix/home.nix` tells the operator to watch a `--dry-run` before arming the
        timer. MEASURED: `--repo /nope --rebuild` (dry-run) exited **0** with no
        "REFUS" text anywhere, while the identical argv plus `--write` exited 4. A
        pre-flight that cannot go red is not an instrument.

        Asserted as the PAIR — same argv, one flag apart — because a test on the
        dry-run alone could not tell "the gate now fires in dry-run" from "the gate
        fires for everything"."""
        argv = ["--repo", str(tmp_path / "nope-a"), "--repo", str(tmp_path / "nope-b"),
                "--rebuild"]
        dry = hi.main(argv, open_store=_refusing_store())
        dry_err = capsys.readouterr().err
        wet = hi.main([*argv, "--write"], open_store=_refusing_store())
        wet_err = capsys.readouterr().err

        assert dry == wet == hi.RC_REFUSED
        assert "REFUSING --rebuild" in dry_err and "REFUSING --rebuild" in wet_err
        # …and the dry run says which run it is speaking for.
        assert "this was a DRY RUN" in dry_err
        assert "this was a DRY RUN" not in wet_err

    def test_a_dry_run_over_a_HEALTHY_config_still_exits_zero(self, tmp_path, capsys):
        """The negative control for the pre-flight: making dry-run evaluate the
        gates must not have made it refuse everything, or a green dry-run stops
        being obtainable and the instruction in `nix/home.nix` becomes unusable."""
        repo = tmp_path / "preflightok"
        _write_repo(repo, {"handoff-widget-relay.md": DOC_FULL})
        rc = hi.main(["--repo", str(repo), "--rebuild"], open_store=_refusing_store())
        assert rc == hi.RC_OK
        out = capsys.readouterr()
        assert "no --write: nothing was written" in out.out
        assert "REFUS" not in out.err and "REFUS" not in out.out

    def test_a_dry_run_evaluates_the_collision_gate_too(self, tmp_path, capsys):
        """The same class as the refusal, and the same fix: a pre-flight that skips
        a gate the write run applies reports a config as safe that is not."""
        for parent in ("dryone", "drytwo"):
            (tmp_path / parent).mkdir()
            _write_repo(tmp_path / parent / "samename",
                        {"handoff-widget-relay.md": DOC_FULL})
        argv = ["--repo", str(tmp_path / "dryone" / "samename"),
                "--repo", str(tmp_path / "drytwo" / "samename")]
        assert hi.main(argv, open_store=_refusing_store()) == hi.RC_COLLISION
        assert "IDENTITY COLLISION" in capsys.readouterr().err
        assert hi.main([*argv, "--write"], open_store=_refusing_store()) == hi.RC_COLLISION


class TestIdentityCollisionGuard:
    def test_a_collision_names_both_documents(self):
        """🔴 `ON CONFLICT DO UPDATE` CANNOT RAISE ON A DUPLICATE IDENTITY —
        that is what the clause is for — so this function is the only thing
        between a silently lost document and a run that reports success."""
        rows = [
            hi.Section("r", "a", "claudedocs/handoff-a.md", None, None, "goal", 0, "h", "x"),
            hi.Section("r", "a", "claudedocs/sub/handoff-a.md", None, None, "goal", 0, "h", "y"),
        ]
        out = hi.identity_collisions(rows)
        assert len(out) == 1
        assert "IDENTITY COLLISION" in out[0]
        assert "claudedocs/handoff-a.md" in out[0]
        assert "claudedocs/sub/handoff-a.md" in out[0]

    def test_a_clean_derivation_reports_nothing(self):
        """The negative control: a per-run '0 collisions' would bury the real
        ones, and an always-firing detector is not a detector. `_corpus()` is a
        real two-doc derivation with 12 rows and every identity distinct."""
        assert hi.identity_collisions(_corpus()) == ()
        assert len(_corpus()) > 1  # …over something, not over an empty list

    def test_two_repos_sharing_a_LABEL_collide_even_with_identical_paths(self):
        """🔴 THE CASE A UNIQUE SLUG CANNOT COVER, and the one a
        path-de-duplicating check was blind to. `default_repos` labels a repo by
        `Path(raw).name`, so `~/a/proj` and `~/b/proj` are both `proj`; their
        identically-named docs then carry the SAME `doc_path` as well as the same
        identity, and counting DISTINCT paths reads that as one document."""
        doc = "claudedocs/handoff-a.md"
        rows = [
            hi.Section("proj", "a", doc, None, None, "goal", 0, "h", "from-repo-one"),
            hi.Section("proj", "a", doc, None, None, "goal", 0, "h", "from-repo-two"),
        ]
        out = hi.identity_collisions(rows)
        assert len(out) == 1
        assert "claimed by 2 rows" in out[0]
        assert "proj/a [goal#0]" in out[0]

    def test_main_refuses_to_write_a_colliding_derivation(self, tmp_path, capsys):
        """Two repo PATHS whose basenames collide resolve to one label, so their
        docs share `(repo, slug, …)`. Slug uniqueness alone cannot cover this —
        which is why the detector exists beside it."""
        for parent in ("one", "two"):
            (tmp_path / parent).mkdir()
            _write_repo(tmp_path / parent / "samename",
                        {"handoff-widget-relay.md": DOC_FULL})
        rc = hi.main(["--repo", str(tmp_path / "one" / "samename"),
                      "--repo", str(tmp_path / "two" / "samename"), "--write"],
                     open_store=_refusing_store())
        assert rc == hi.RC_COLLISION
        assert "IDENTITY COLLISION" in capsys.readouterr().err

    def test_the_derivation_report_surfaces_a_collision_as_a_warning(self, tmp_path):
        for parent in ("three", "four"):
            (tmp_path / parent).mkdir()
            _write_repo(tmp_path / parent / "dup", {"handoff-widget-relay.md": DOC_FULL})
        ds = [hi.derive_repo(tmp_path / p / "dup", label="dup") for p in ("three", "four")]
        text = hi.render_derivation(ds)
        assert "IDENTITY COLLISION" in text
        # …and the false all-clear is NOT printed beside it.
        assert "handoff doc on disk is also in it" not in text


# --------------------------------------------------------------------------- #
# F3 — the durability report must walk the same tree `ls-tree -r` does
# --------------------------------------------------------------------------- #


class TestNestedDurabilityHole:
    def test_a_NESTED_untracked_doc_is_found(self, tmp_path):
        """🔴 THE MUTANT-KILLER FOR `.rglob` -> `.glob`. `handoff_paths_in_ref`
        runs `git ls-tree -r` (RECURSIVE); the disk side ran a non-recursive
        `glob`, so the two halves of one set difference walked different shapes.
        A doc at `claudedocs/sub/handoff-*.md` was invisible to the disk half,
        `untracked` came back `()`, and the report printed the literal all-clear.

        The doc here lives ONLY in a subdirectory, so a non-recursive scan sees
        nothing at all — the assertion cannot be satisfied by accident."""
        repo = tmp_path / "nestedrepo"
        _write_repo(repo, {"handoff-widget-relay.md": DOC_FULL})
        sub = repo / hi.HANDOFF_DIR / "sub"
        sub.mkdir()
        (sub / "handoff-orphan.md").write_text(DOC_SPARSE)

        assert "claudedocs/sub/handoff-orphan.md" in hi.handoff_paths_on_disk(repo).paths
        d = hi.derive_repo(repo, label="nestedrepo")
        assert d.untracked == ("claudedocs/sub/handoff-orphan.md",)
        assert any("DURABILITY HOLE" in w for w in d.warnings)
        text = hi.render_derivation([d])
        assert "DURABILITY HOLE" in text
        # 🔴 PIN THE WHOLE SENTENCE. The false all-clear is the actual damage:
        # it reads as coverage and stops anyone looking again.
        assert "handoff doc on disk is also in it" not in text

    def test_a_top_level_untracked_doc_is_still_found(self, tmp_path):
        """The non-nested case must keep working — a fix that only ever looks in
        subdirectories would trade one blind spot for another."""
        repo = tmp_path / "flatrepo"
        _write_repo(repo, {"handoff-widget-relay.md": DOC_FULL})
        (repo / hi.HANDOFF_DIR / "handoff-cable-audit.md").write_text(DOC_SPARSE)
        d = hi.derive_repo(repo, label="flatrepo")
        assert d.untracked == ("claudedocs/handoff-cable-audit.md",)

    def test_a_committed_nested_doc_is_indexed_and_not_reported_as_a_hole(self, tmp_path):
        """The negative control for the recursion: once the nested doc IS in the
        ref, both halves see it and it is a normal indexed document, not a hole."""
        repo = tmp_path / "nestedcommitted"
        _write_repo(repo, {"handoff-widget-relay.md": DOC_FULL})
        sub = repo / hi.HANDOFF_DIR / "sub"
        sub.mkdir()
        (sub / "handoff-orphan.md").write_text(DOC_SPARSE)
        subprocess.run(["git", "-C", str(repo), "add",
                        f"{hi.HANDOFF_DIR}/sub/handoff-orphan.md"], check=True)
        subprocess.run(["git", "-C", str(repo), "commit", "-qm", "nested"], check=True)

        d = hi.derive_repo(repo, label="nestedcommitted")
        assert d.untracked == ()
        assert d.warnings == []
        assert d.docs == 2
        assert any("trundlebore" in r.body for r in d.sections)
        # …and its identity carries the subdirectory, so it cannot collide with a
        # same-named doc at the top level.
        assert "sub/orphan" in {r.slug for r in d.sections}


class TestTheAllClearIsEarned:
    """🔴 WIDENED FROM THE REF SIDE TO THE SIDE THE SENTENCE ACTUALLY DESCRIBES.

    The original version of this class exercised only the ref-side count, and the
    implementation was gated only on the ref-side count (`elif total_docs:`) — so
    the suite was green over a disk-side blind spot that the all-clear sentence is
    entirely about. That is the shape `claude/RULES.md` calls a guard whose
    DESCRIPTION claims coverage its body does not provide."""

    def test_zero_documents_scanned_is_NOT_an_all_clear(self, tmp_path):
        """🔴 A GUARD WHOSE DESCRIPTION CLAIMS COVERAGE ITS BODY DOES NOT PROVIDE
        IS WORSE THAN NONE. With no documents examined the on-disk-vs-ref
        comparison had nothing to compare, and 'every handoff doc on disk is also
        in it' is vacuously true and read as a finding."""
        repo = tmp_path / "emptyclean"
        _write_repo(repo, {}, commit=False)
        subprocess.run(["git", "-C", str(repo), "commit", "-qm", "e", "--allow-empty"],
                       check=True)
        d = hi.derive_repo(repo, label="emptyclean")
        assert d.warnings == [] and d.docs == 0
        text = hi.render_derivation([d])
        assert "NOT AN ALL-CLEAR" in text
        assert "ZERO documents were scanned" in text
        assert "handoff doc on disk is also in it" not in text

    def test_an_UNREADABLE_claudedocs_is_NOT_an_all_clear(self, tmp_path):
        """🔴 THE MEASURED DEFECT THIS CLASS COULD NOT SEE, AND IT IS A DIFFERENT
        SIDE OF THE COMPARISON. `handoff_paths_on_disk` returned `()` with no
        warning and no flag when `claudedocs/` was unreadable, `untracked_docs`
        found nothing to report, and the all-clear was gated on the REF-side
        document count — which is high and healthy, because git can read the ref
        perfectly well.

        MEASURED on one repo with a REAL durability hole present:
          readable      -> `🔴 DURABILITY HOLE — 1 handoff doc …`
          chmod 0o000   -> `## warnings: none — … every handoff doc on disk is
                            also in it.`
        The uncommitted doc had not moved. This is that pair, run as a
        differential over ONE repo, so only the permission bit can explain it.

        ⚠ AND THE OLD `except OSError: return ()` WAS NOT THE MECHANISM. `rglob`
        walks via `os.scandir` and swallows the `PermissionError` internally —
        measured on CPython 3.12.14, it returns `[]` and raises nothing — so that
        clause could never fire. The fix is `os.walk(onerror=…)`, which is why the
        error text reaches the report at all."""
        repo = tmp_path / "unreadable"
        _write_repo(repo, {"handoff-widget-relay.md": DOC_FULL})
        docs = repo / hi.HANDOFF_DIR
        (docs / "handoff-cable-audit.md").write_text(DOC_SPARSE)

        readable = hi.render_derivation([hi.derive_repo(repo, label="unreadable")])
        assert "DURABILITY HOLE" in readable

        os.chmod(docs, 0o000)
        try:
            scan = hi.handoff_paths_on_disk(repo)
            assert scan.paths == ()          # the disk side still comes back empty…
            assert scan.errors               # …but it now SAYS it could not look
            assert scan.complete is False
            d = hi.derive_repo(repo, label="unreadable")
            assert d.untracked == ()         # the hole is genuinely invisible…
            text = hi.render_derivation([d])
        finally:
            os.chmod(docs, 0o755)

        # …and the report must not read as a finding of nothing.
        assert "handoff doc on disk is also in it" not in text
        assert "UNSCANNABLE DISK" in text
        assert "NOT reported" in text
        # The ref side is healthy throughout — which is exactly why gating the
        # all-clear on it could not see this.
        assert "docs=1" in text

    def test_a_real_scan_with_no_findings_IS_an_all_clear(self, tmp_path):
        """The positive control: the all-clear must still be reachable, or the
        assertions above prove only that it was deleted."""
        repo = tmp_path / "realclean"
        _write_repo(repo, {"handoff-widget-relay.md": DOC_FULL})
        d = hi.derive_repo(repo, label="realclean")
        assert d.disk.complete is True and len(d.disk.paths) == 1
        text = hi.render_derivation([d])
        assert "handoff doc on disk is also in it" in text
        assert "NOT AN ALL-CLEAR" not in text

    def test_a_disk_side_that_saw_ZERO_paths_is_NOT_an_all_clear(self, tmp_path):
        """🔴 THE THIRD BLOCKER, AND IT IS NOT THE SAME AS THE FIRST TWO. The walk
        RAN and hid nothing — it simply had nothing to walk, because `claudedocs/`
        is absent from the working tree while the ref carries documents (an
        ordinary worktree-on-another-branch state). 'Every handoff doc on disk is
        also in it' over zero disk docs is vacuously true and measures nothing."""
        repo = tmp_path / "nodiskdir"
        _write_repo(repo, {"handoff-widget-relay.md": DOC_FULL})
        import shutil
        shutil.rmtree(repo / hi.HANDOFF_DIR)

        scan = hi.handoff_paths_on_disk(repo)
        assert scan.absent is True and scan.complete is True and scan.paths == ()
        d = hi.derive_repo(repo, label="nodiskdir")
        assert d.docs == 1                   # the REF side is populated…
        assert d.warnings == []              # …and there is nothing to warn about
        text = hi.render_derivation([d])
        assert "handoff doc on disk is also in it" not in text
        assert "NOT AN ALL-CLEAR" in text
        assert "DISK side of the comparison saw ZERO" in text

    def test_the_blockers_NAME_which_measurement_is_missing(self, tmp_path):
        """A bare NOT-AN-ALL-CLEAR tells a reader nothing about what to fix, and
        the three blockers have three different fixes. Pinned as a differential:
        the empty-repo case must name the REF-side blocker and NOT the
        unreadable-disk one, or the reasons are decoration."""
        empty = tmp_path / "blockempty"
        _write_repo(empty, {}, commit=False)
        subprocess.run(["git", "-C", str(empty), "commit", "-qm", "e", "--allow-empty"],
                       check=True)
        blockers = hi.all_clear_blockers([hi.derive_repo(empty, label="blockempty")])
        assert any("ZERO documents were scanned" in b for b in blockers)
        assert not any("did not complete" in b for b in blockers)

        clean = tmp_path / "blockclean"
        _write_repo(clean, {"handoff-widget-relay.md": DOC_FULL})
        assert hi.all_clear_blockers([hi.derive_repo(clean, label="blockclean")]) == ()

    def test_an_incomplete_walk_is_named_in_the_blockers_LIST_itself(self):
        """🔴 REACHED DIRECTLY, BECAUSE THE RENDERER STRUCTURALLY CANNOT REACH IT.
        A mutation sweep deleted `all_clear_blockers`' incomplete-walk arm and the
        whole suite stayed green: an incomplete walk always also emits an
        `⚠ UNSCANNABLE DISK` warning (both read `DiskScan.complete`), and ANY
        warning sends `render_derivation` down its `if warnings:` branch, which
        never consults the blockers. Through the text report the arm is a second
        copy of one predicate and cannot be killed.

        It is not decoration, though, because `derivation_json` publishes
        `all_clear_blockers` as its own field — a consumer asking "what stopped
        the all-clear" has to get the complete answer from that list rather than
        parse prose out of `warnings`. So the coverage is a contract of this PURE
        function and is pinned as one, with the derivation built by hand to
        isolate the arm from the other two blockers.

        ⚠ Labelled honestly: this is a CONTRACT pin on a pure function, not a
        regression test for a defect anyone observed in the rendered report."""
        d = hi.RepoDerivation(
            repo="/x", label="halfread", ref="main", docs=3,
            disk=hi.DiskScan(paths=("claudedocs/handoff-a.md",), scanned=True,
                             errors=("claudedocs/locked: Permission denied",)),
        )
        blockers = hi.all_clear_blockers([d])
        assert any("did not complete" in b and "halfread" in b for b in blockers)
        # …and neither of the other two arms fires here, so the assertion above
        # cannot be satisfied by one of them.
        assert not any("ZERO documents were scanned" in b for b in blockers)
        assert not any("saw ZERO handoff docs" in b for b in blockers)
        assert len(blockers) == 1

        # The negative control on the SAME shape: a complete walk, everything else
        # identical, yields no blocker at all.
        ok = hi.RepoDerivation(
            repo="/x", label="halfread", ref="main", docs=3,
            disk=hi.DiskScan(paths=("claudedocs/handoff-a.md",), scanned=True),
        )
        assert hi.all_clear_blockers([ok]) == ()


# --------------------------------------------------------------------------- #
# F5 — one malformed byte must not take the whole run down
# --------------------------------------------------------------------------- #


class TestUndecodableDoc:
    def test_a_non_utf8_committed_doc_does_not_kill_the_run(self, tmp_path):
        """🔴 ONE DOC, EVERY REPO. `_git` decoded with the process locale and
        `text=True`, so a single committed `\\xff` raised `UnicodeDecodeError`
        out of `subprocess.run` — from inside a per-document loop, with only
        `OSError` caught. The unit sets no `LANG`/`LC_ALL`.

        The assertion is that the OTHER document in the same repo still reaches a
        row: 'it did not raise' alone would also be satisfied by a run that
        silently produced nothing."""
        repo = tmp_path / "badbytes"
        _write_repo(repo, {"handoff-widget-relay.md": DOC_FULL})
        bad = repo / hi.HANDOFF_DIR / "handoff-mojibake.md"
        bad.write_bytes(b"# Handoff: mojibake \xff\xfe - 2026-02-02\n\n"
                        b"## Goal\nThe \xff snarklebutt target.\n")
        subprocess.run(["git", "-C", str(repo), "add",
                        f"{hi.HANDOFF_DIR}/handoff-mojibake.md"], check=True)
        subprocess.run(["git", "-C", str(repo), "commit", "-qm", "bad"], check=True)

        d = hi.derive_repo(repo, label="badbytes")
        assert d.docs == 2
        assert any("quixotry" in r.body for r in d.sections)
        # The malformed doc is still indexed, with its undecodable bytes replaced
        # rather than dropped — a U+FFFD in a result tells a reader the source is
        # malformed, where `errors="ignore"` would hand back plausible fiction.
        moji = [r for r in d.sections if r.slug == "mojibake"]
        assert moji and "snarklebutt" in moji[0].body

    def test_a_search_over_the_corpus_still_answers_with_the_bad_doc_present(self, tmp_path):
        """End to end through `main`: the failure mode was a dead PROCESS, so the
        control that matters is that the CLI still returns an answer."""
        repo = tmp_path / "badbytes2"
        _write_repo(repo, {"handoff-widget-relay.md": DOC_FULL})
        bad = repo / hi.HANDOFF_DIR / "handoff-mojibake.md"
        bad.write_bytes(b"# Handoff: m \xff - 2026-02-02\n\n## Goal\nx\n")
        subprocess.run(["git", "-C", str(repo), "add",
                        f"{hi.HANDOFF_DIR}/handoff-mojibake.md"], check=True)
        subprocess.run(["git", "-C", str(repo), "commit", "-qm", "bad"], check=True)
        assert hs.main(["--query", "zarfwidget", "--offline",
                        "--offline-repo", str(repo)]) == 0


# --------------------------------------------------------------------------- #
# F6 — a shape-valid but impossible date must not reach a `date` column
# --------------------------------------------------------------------------- #


class TestImpossibleDates:
    @pytest.mark.parametrize("bad", ["2026-99-99", "1234-56-78", "2026-02-30",
                                     "2026-00-01", "2026-13-01"])
    def test_an_impossible_date_is_absent_not_stored(self, bad):
        """🔴 THE SHAPE IS NOT THE VALIDATION. `\\d{4}-\\d{2}-\\d{2}` admits every
        one of these. Postgres rejects them — INSIDE the write transaction, after
        `--rebuild` truncated — and deterministically, so the same doc emptied the
        index every 6h until a human read the journal. A malformed display field
        must never be able to take the corpus down with it."""
        doc = f"# Handoff: bad-date — {bad}\n\n## Goal\nx\n"
        assert hi.doc_date_for("claudedocs/handoff-bad-date.md", doc) is None

    def test_a_valid_date_still_parses(self):
        """The positive control: a validator that rejected everything would pass
        every assertion above."""
        doc = "# Handoff: ok — 2026-02-29\n\n## Goal\nx\n"   # 2026 is not a leap year
        assert hi.doc_date_for("claudedocs/handoff-ok.md", doc) is None
        doc = "# Handoff: ok — 2024-02-29\n\n## Goal\nx\n"   # 2024 is
        assert hi.doc_date_for("claudedocs/handoff-ok.md", doc) == "2024-02-29"

    def test_the_scan_continues_past_an_impossible_date_to_a_real_one(self):
        """Treating a bad date as fatal-to-the-field would drop a date the doc
        genuinely carries. It is skipped, not terminal."""
        doc = "# Handoff: mixed — 2026-99-99 (typo), really 2026-05-06\n\n## Goal\nx\n"
        assert hi.doc_date_for("claudedocs/handoff-mixed.md", doc) == "2026-05-06"

    def test_an_impossible_preamble_date_falls_through_to_the_filename(self):
        doc = "# Handoff: fallback — 2026-77-77\n\n## Goal\nx\n"
        assert hi.doc_date_for("claudedocs/handoff-fallback-2026-05-06.md", doc) \
            == "2026-05-06"

    def test_an_impossible_date_reaches_no_row(self):
        """The end-to-end control: the defect was a value reaching the INSERT."""
        doc = "# Handoff: bad — 2026-99-99\n\n## Goal\nthe wamblecrest target\n"
        rows = hi.sections_for_doc("r", "claudedocs/handoff-bad.md", doc)
        assert rows and all(r.doc_date is None for r in rows)


# --------------------------------------------------------------------------- #
# F2 — the scoped silent-zero guard, end to end
# --------------------------------------------------------------------------- #


class TestScopedSilentZeroGuard:
    def test_an_unknown_repo_label_is_empty_scope_not_the_corpus_is_silent_prose(self):
        """🔴 THE REPRODUCED DEFECT. `--repo <never-indexed>` rendered
        `NO MATCH — the index WAS searched … That is an answer about the corpus,
        not a broken tool` beside a reassuring non-zero `indexed_docs`. Both
        halves were false: the index was NOT searched, and the count described a
        corpus the query could not reach."""
        out = hs.run_search(_store(), "zarfwidget", backend="memory",
                            repo="totally-bogus-repo")
        assert out.status == "empty-scope"
        assert out.scope_reason == "unknown-repo"
        text = hs.render(out)
        assert "🔴 EMPTY SCOPE" in text
        # 🔴 PIN THE WHOLE NORMALISED SENTENCES, not a word.
        assert "the index WAS searched" not in text
        assert "That is an answer about the corpus" not in text
        assert "the filter, not the corpus, is what is empty" in text

    def test_the_two_empty_scope_reasons_are_told_apart_in_the_prose(self):
        """🔴 ONE STATUS, TWO REASONS, TWO DIFFERENT FIXES. A mutation sweep
        caught this: collapsing the unknown-label check into the bare
        `scoped == 0` count left all tests green, because both paths reach
        `empty-scope` — but it made the list of indexed labels, the ONE thing
        that tells a caller they typed a path where a label goes, unreachable
        prose. Pinned as a differential over the SAME store, so only the reason
        can explain the two messages."""
        store = _store()
        unknown = hs.render(hs.run_search(store, "zarfwidget", backend="memory",
                                          repo="totally-bogus-repo"))
        valid = hs.run_search(store, "zarfwidget", backend="memory",
                              repo="cablerepo", sections=["gotcha"])
        assert valid.scope_reason == "no-rows"

        assert "NO REPO IS INDEXED UNDER THE LABEL" in unknown
        assert "relayrepo" in unknown and "cablerepo" in unknown
        valid_text = hs.render(valid)
        assert "NO REPO IS INDEXED UNDER THE LABEL" not in valid_text
        assert "The filter is VALID" in valid_text
        assert "The filter is VALID" not in unknown

    def test_the_scope_reason_vocabulary_is_exactly_what_is_emitted(self):
        """Pinned two-way, like `STATUSES`: a declared reason nothing emits is
        dead, and an emitted reason that is not declared cannot be switched on."""
        store = _store()
        emitted = {
            hs.run_search(store, "x", backend="memory", repo="nope").scope_reason,
            hs.run_search(store, "x", backend="memory", repo="cablerepo",
                          sections=["gotcha"]).scope_reason,
        }
        assert emitted == set(hs.SCOPE_REASONS)
        # …and a status that is NOT empty-scope carries no reason at all.
        assert hs.run_search(store, "zarfwidget", backend="memory").scope_reason is None

    def test_an_absolute_path_as_repo_is_rejected_naming_the_real_labels(self):
        """The natural mistake on this box: `$DEVRC` is pre-exported, so
        `--repo $DEVRC` is what a caller types — and `handoff_index.py --repo`
        genuinely DOES take a path. The message has to teach the distinction."""
        out = hs.run_search(_store(), "zarfwidget", backend="memory",
                            repo="/home/somebody/workspace/relayrepo")
        assert out.status == "empty-scope"
        assert out.scope_reason == "unknown-repo"
        text = hs.render(out)
        assert "--repo takes a repo LABEL, not a path" in text
        for label in ("relayrepo", "cablerepo"):
            assert label in text
        assert out.known_repos == ("cablerepo", "relayrepo")

    def test_a_section_filter_that_selects_nothing_is_empty_scope_too(self):
        """DOC_SPARSE has no gotchas. Scoped to `cablerepo` + `gotcha`, the index
        is healthy and the scope is empty — the same fact as an unknown label,
        arrived at by a filter that is individually valid."""
        store = hi.MemorySectionStore(
            hi.sections_for_doc("cablerepo", "claudedocs/handoff-cable-audit.md",
                                DOC_SPARSE))
        out = hs.run_search(store, "trundlebore", backend="memory", sections=["gotcha"])
        assert out.status == "empty-scope"
        assert out.stats.indexed_sections == 3
        assert out.in_scope.indexed_sections == 0
        # …and the SAME store answers on a section it does have. The pair is what
        # makes either readable.
        ok = hs.run_search(store, "trundlebore", backend="memory", sections=["goal"])
        assert ok.status == "hit"

    def test_the_scoped_counts_are_printed_beside_the_totals(self):
        out = hs.run_search(_store(), "quixotry", backend="memory", repo="relayrepo")
        text = hs.render(out)
        assert f"indexed_sections={out.stats.indexed_sections}" in text
        assert f"in_scope_sections={out.in_scope.indexed_sections}" in text
        assert out.in_scope.indexed_sections < out.stats.indexed_sections

    def test_an_unfiltered_query_prints_no_in_scope_pair(self):
        """The negative control for the line: an unscoped query has one corpus,
        and printing two identical numbers would train a reader to skip both."""
        text = hs.render(hs.run_search(_store(), "quixotry", backend="memory"))
        assert "in_scope_sections=" not in text

    def test_the_six_statuses_render_with_no_shared_opening_phrase(self):
        """🔴 WIDENED FROM TWO TO FOUR TO FIVE TO SIX. Each zero must be
        machine-distinguishable from the others, or a caller reading prose
        conflates them. `unmeasured-corpus` joined the ledger because it used to
        render as `broken-index` — same words, wrong diagnosis, confident next
        step. `derived-zero-docs` joins for the SAME reason one round later: the
        fix carved out the repos that did not RESOLVE and left the repos that
        resolved and hold no docs still rendering the table-and-unit remedy."""
        broken = hs.render(hs.run_search(hi.MemorySectionStore([]), "zarfwidget",
                                        backend="postgres"))
        unmeasured = hs.render(hs.run_search(
            hi.MemorySectionStore([]), "zarfwidget", backend="memory",
            unmeasured=(("gone", "no-such-directory"),), targets=("gone",)))
        derived_empty = hs.render(hs.run_search(
            hi.MemorySectionStore([]), "zarfwidget", backend="memory",
            targets=("hollow",)))
        scope = hs.render(hs.run_search(_store(), "zarfwidget", backend="memory",
                                        repo="norepo"))
        genuine = hs.render(hs.run_search(_store(), "hexapoddery", backend="memory"))
        hit = hs.render(hs.run_search(_store(), "zarfwidget", backend="memory"))

        rendered = (broken, unmeasured, derived_empty, scope, genuine, hit)
        marks = {
            "ran against NOTHING": broken,
            "failed to resolve, so no corpus was ever built": unmeasured,
            "resolved a mainline ref and hold no": derived_empty,
            "the filter, not the corpus, is what is empty": scope,
            "the index WAS searched": genuine,
            "section(s), best first": hit,
        }
        for phrase, owner in marks.items():
            for other in rendered:
                if other is owner:
                    assert phrase in other, phrase
                else:
                    assert phrase not in other, phrase

        # 🔴 AND THE TWO WRONG REMEDIES APPEAR ONLY ON THE ONE PATH THAT HAS THEM.
        # This is the actual Y3 claim — "shares no opening phrase" would still
        # pass for a derived-empty block that ALSO told you to rebuild a table.
        for other in (unmeasured, derived_empty, scope, genuine, hit):
            assert "handoff-index-sync" not in other
            assert "--rebuild --write" not in other
        assert "handoff-index-sync" in broken

    def test_the_no_match_prose_reports_the_SCOPE_size_not_the_index_size(self):
        out = hs.run_search(_store(), "hexapoddery", backend="memory", repo="cablerepo")
        assert out.status == "no-match"
        text = hs.render(out)
        assert f"{out.in_scope.indexed_sections} section(s) in scope" in text
        assert f"the whole index holds {out.stats.indexed_sections}" in text

    def test_the_json_surface_carries_the_scoped_pair_and_the_labels(self):
        payload = hs.outcome_json(hs.run_search(_store(), "zarfwidget",
                                                backend="memory", repo="norepo"))
        assert payload["status"] == "empty-scope"
        assert payload["in_scope_sections"] == 0
        assert payload["indexed_sections"] > 0
        assert payload["known_repos"] == ["cablerepo", "relayrepo"]

    def test_the_empty_scope_status_exits_four_through_main(self, tmp_path, capsys):
        """🔴 A CALLER READING ONLY THE EXIT CODE MUST NOT SEE 0. And the code
        differs from `broken-index`'s 3, because a broken environment and a bad
        filter have different fixes."""
        repo = tmp_path / "scoperepo"
        _write_repo(repo, {"handoff-widget-relay.md": DOC_FULL})
        rc = hs.main(["--query", "zarfwidget", "--offline", "--offline-repo", str(repo),
                      "--repo", str(repo)])   # a PATH, not the label
        assert rc == hs.SCOPE_REASON_EXIT_CODES["unknown-repo"] == 4
        assert "🔴 EMPTY SCOPE" in capsys.readouterr().out
        # …and the correct label, through the same main, exits 0 with a hit.
        assert hs.main(["--query", "zarfwidget", "--offline",
                        "--offline-repo", str(repo), "--repo", "scoperepo"]) == 0

    @pytest.mark.parametrize("limit", [0, -1, -10])
    def test_a_limit_below_one_is_a_usage_error(self, tmp_path, capsys, limit):
        """`--limit 0` returned zero hits from a healthy index and rendered the
        corpus-is-silent prose — the same false claim by another door. `--limit -1`
        is worse: the memory backend's `hits[:-1]` drops the LAST hit and returns a
        plausible list, while Postgres rejects `LIMIT -1` outright."""
        repo = tmp_path / "limitrepo"
        _write_repo(repo, {"handoff-widget-relay.md": DOC_FULL})
        rc = hs.main(["--query", "zarfwidget", "--offline", "--offline-repo", str(repo),
                      "--limit", str(limit)])
        assert rc == 2
        assert f"--limit must be >= {hs.MIN_LIMIT}" in capsys.readouterr().err

    def test_an_UNRESOLVABLE_offline_repo_is_not_a_broken_index(self, tmp_path, capsys):
        """🔴 THE REPRODUCED MISDIAGNOSIS, AND IT CAME WITH A CONFIDENT WRONG FIX.
        MEASURED: `--offline --offline-repo /does/not/exist` printed `🔴 BROKEN
        INDEX — this table holds ZERO documents … Rebuild it (--rebuild --write)
        or check the handoff-index-sync unit`, rc 3 — on a code path that opens no
        database, has no table to rebuild and no unit to check. `_offline_store`
        called `derive_repo`, which records a STRUCTURAL `unmeasured` reason per
        repo, and threw the flag away; `run_search` then saw an empty store and
        had nothing to distinguish the two mechanisms with. `claude/RULES.md` →
        'an EMPTY RESULT cannot distinguish two mechanisms'.

        Pinned as a differential against a genuinely EMPTY-but-measurable corpus,
        which must still say BROKEN INDEX — otherwise this asserts only that the
        broken-index branch was deleted."""
        rc = hs.main(["--query", "zarfwidget", "--offline",
                      "--offline-repo", str(tmp_path / "does-not-exist")])
        out = capsys.readouterr().out
        assert rc == hs.EXIT_CODES["unmeasured-corpus"] == 6
        assert "🔴 UNMEASURABLE CORPUS" in out
        assert "no-such-directory" in out
        assert "all 1 repo(s) this run was pointed at" in out
        # 🔴 THE WRONG REMEDIES MUST BE GONE, not merely joined by a right one.
        assert "BROKEN INDEX" not in out
        assert "--rebuild --write" not in out
        assert "handoff-index-sync" not in out

        # The differential: a real repo that resolves and holds no handoff docs is
        # a MEASURED empty corpus — a different status, a different code, and
        # still not a broken index, because this path opens no database either.
        empty = tmp_path / "measurably-empty"
        _write_repo(empty, {}, commit=False)
        subprocess.run(["git", "-C", str(empty), "commit", "-qm", "e", "--allow-empty"],
                       check=True)
        rc2 = hs.main(["--query", "zarfwidget", "--offline", "--offline-repo", str(empty)])
        out2 = capsys.readouterr().out
        assert rc2 == hs.EXIT_CODES["derived-zero-docs"] == 7
        assert "🔴 ZERO HANDOFF DOCS DERIVED" in out2
        assert "UNMEASURABLE CORPUS" not in out2
        assert "BROKEN INDEX" not in out2

    def test_the_UNMEASURABLE_count_is_the_repos_POINTED_AT_not_the_failures(
            self, tmp_path, capsys):
        """🔴 THE MISCOUNT, AND THE REMEDY IT MISDIRECTED. MEASURED: pointing
        `--offline` at one repo that RESOLVES (and holds no handoff docs) plus one
        that does not exist printed

            🔴 UNMEASURABLE CORPUS — all **1** repo(s) this run was pointed at
            failed to resolve, so no corpus was ever built

        It was pointed at TWO, one resolved, and a corpus WAS built — it is empty.
        `len(outcome.unmeasured)` is a count of FAILURES being read as a count of
        ATTEMPTS. The remedy then told the reader to fix the checkout paths, one
        of which is fine.

        Both halves are asserted, and the second is the one a count fix alone
        would not give: the status itself must move, because "no corpus was ever
        built" is false here however the number is spelled. The resolvable repo
        is NAMED as fine, and only the absent one is offered as a path to fix."""
        resolvable = tmp_path / "resolves-but-bare"
        _write_repo(resolvable, {}, commit=False)
        subprocess.run(["git", "-C", str(resolvable), "commit", "-qm", "e",
                        "--allow-empty"], check=True)
        rc = hs.main(["--query", "zarfwidget", "--offline",
                      "--offline-repo", str(resolvable),
                      "--offline-repo", str(tmp_path / "utterly-absent")])
        out = capsys.readouterr().out

        assert rc == hs.EXIT_CODES["derived-zero-docs"] == 7
        # The false sentence, in the exact shape it was measured in.
        assert "all 1 repo(s) this run was pointed at" not in out
        assert "no corpus was ever built" not in out
        # The true one: one repo read, one of two did not resolve, and the reader
        # is told WHICH path to fix rather than "the checkout path(s)".
        assert "the 1 repo(s) this run read (resolves-but-bare)" in out
        assert "1 of the 2 repo(s) named did not resolve AT ALL" in out
        assert "utterly-absent (no-such-directory)" in out
        assert "the ones listed above are fine" in out

    def test_the_UNMEASURABLE_sentence_reads_the_DENOMINATOR_not_the_failures(self):
        """🔴 THE MUTANT THIS EXISTS TO KILL, AND WHY IT NEEDED ITS OWN TEST.
        Swapping the renderer's `len(outcome.targets)` back to
        `len(outcome.unmeasured)` SURVIVED a full green sweep driven through
        `main`: `run_search` only emits `unmeasured-corpus` when the two are
        EQUAL, so every fixture that reaches this branch through the classifier
        makes the two constants coincide — exactly the blind spot
        `claude/RULES.md` describes, where a fixture can only ever produce the
        value the assertion names.

        The renderer is its own function with its own contract, so it is reached
        DIRECTLY with an outcome the classifier would never build: 3 repos
        pointed at, 2 unmeasured. The right sentence names 3. Reading `unmeasured`
        prints 2 — the F3 defect, in the one place it is still spellable."""
        outcome = hs.SearchOutcome(
            query="zarfwidget",
            stats=hi.IndexStats(indexed_docs=0, indexed_sections=0),
            hits=(), status="unmeasured-corpus", backend="memory",
            unmeasured=(("plimforth", "no-such-directory"),
                        ("marganser", "no-mainline-ref")),
            targets=("plimforth", "marganser", "trundlebore"),
        )
        text = hs.render(outcome)
        assert "all 3 repo(s) this run was pointed at" in text
        assert "all 2 repo(s)" not in text

    def test_the_json_surface_carries_the_denominator(self, tmp_path, capsys):
        """The count fix has to reach the MACHINE surface too, or a consumer
        recomputes the same wrong ratio from `unmeasured` alone — which is exactly
        what the text renderer did."""
        resolvable = tmp_path / "jsondenominator"
        _write_repo(resolvable, {}, commit=False)
        subprocess.run(["git", "-C", str(resolvable), "commit", "-qm", "e",
                        "--allow-empty"], check=True)
        hs.main(["--query", "zarfwidget", "--offline", "--json",
                 "--offline-repo", str(resolvable),
                 "--offline-repo", str(tmp_path / "jsonabsent")])
        payload = json.loads(capsys.readouterr().out)
        assert payload["status"] == "derived-zero-docs"
        assert payload["exit_code"] == 7
        assert payload["targets"] == ["jsondenominator", "jsonabsent"]
        assert len(payload["unmeasured"]) == 1 < len(payload["targets"])

    def test_no_repos_at_all_is_a_usage_error_in_the_siblings_wording(
            self, monkeypatch, capsys):
        """🔴 THE SECOND VARIANT, AND IT PRINTED **ZERO** WARNINGS. With every
        handle unset and no `--offline-repo`, `default_repos()` returns `[]`, the
        corpus is empty for the most ordinary reason there is, and this rendered
        the same false `🔴 BROKEN INDEX`, rc 3.

        The sibling CLI over the SAME corpus already got it right — `no repos to
        index. Pass --repo, or set one of: $DEVRC, …`, rc 2 — so two front ends
        disagreed about one fact. Asserted as that cross-CLI differential, not
        against a hand-copied string: pinning the literal would let the two drift
        apart again the next time either is reworded."""
        for handle in hi.REPO_ENV_HANDLES:
            monkeypatch.delenv(handle, raising=False)

        rc_index = hi.main(["--rebuild"], open_store=_refusing_store())
        index_err = capsys.readouterr().err
        rc_search = hs.main(["--query", "zarfwidget", "--offline"])
        search_err = capsys.readouterr().err

        assert rc_index == rc_search == 2
        assert "no repos to index" in search_err
        assert "BROKEN INDEX" not in search_err
        # Same sentence, same handle list, only the flag name differs.
        assert index_err.replace("--repo,", "--offline-repo,") == search_err.replace(
            "handoff-search:", "handoff-index:")

    def test_a_PARTIALLY_unmeasured_offline_corpus_still_answers(self, tmp_path, capsys):
        """The boundary the fix must NOT have moved. `unmeasured-corpus` fires only
        when the corpus came back EMPTY; a run over one good repo and one absent
        one has rows and must answer normally, with the per-repo warning beside
        it. Widening it to 'any unmeasured repo' would make a host missing one
        checkout permanently unable to get an answer — the same permanently-red
        mistake `rebuild_refusal` had to unwind."""
        good = tmp_path / "partialsearch"
        _write_repo(good, {"handoff-widget-relay.md": DOC_FULL})
        rc = hs.main(["--query", "zarfwidget", "--offline",
                      "--offline-repo", str(good),
                      "--offline-repo", str(tmp_path / "absent-one")])
        cap = capsys.readouterr()
        assert rc == 0
        assert "Why the zarfwidget latch never fires" in cap.out
        assert "UNMEASURABLE CORPUS" not in cap.out
        assert "UNMEASURED — absent-one" in cap.err

    def test_the_json_surface_carries_the_unmeasured_repos(self, tmp_path, capsys):
        """A consumer reading `--json` must be able to see which repos contributed
        nothing to the hits it is about to act on, without parsing stderr."""
        good = tmp_path / "jsonpartial"
        _write_repo(good, {"handoff-widget-relay.md": DOC_FULL})
        rc = hs.main(["--query", "zarfwidget", "--offline", "--json",
                      "--offline-repo", str(good),
                      "--offline-repo", str(tmp_path / "jsongone")])
        payload = json.loads(capsys.readouterr().out)
        assert rc == 0 and payload["status"] == "hit"
        assert payload["unmeasured"] == [
            {"repo": "jsongone", "reason": "no-such-directory"}
        ]
        assert payload["exit_code"] == 0

    def test_the_smallest_legal_limit_still_answers(self, tmp_path, capsys):
        """The positive control for the bound: it must not have moved the cliff."""
        repo = tmp_path / "limitok"
        _write_repo(repo, {"handoff-widget-relay.md": DOC_FULL})
        assert hs.main(["--query", "zarfwidget", "--offline",
                        "--offline-repo", str(repo), "--limit", "1"]) == 0
        assert "Why the zarfwidget latch never fires" in capsys.readouterr().out


# --------------------------------------------------------------------------- #
# Packaging
# --------------------------------------------------------------------------- #


class TestPackaging:
    def test_the_production_store_seam_is_the_DEFAULT_not_an_opt_in(self):
        """🔴 THE SEAM MUST NOT SILENTLY REPLACE THE PRODUCTION PATH. Every write
        test above injects a fake `open_store`, so nothing exercises the real one
        — and a default of `None` (or a test double) would mean the unit's
        `--rebuild --write` wrote nowhere while every test stayed green.

        This is a WIRING pin, not a behavioural one, and says so: it asserts the
        default is `_maildb_store` and that the `_db.py` it loads exists on disk.
        It does NOT connect to anything. `import_maildb` imports psycopg2, which
        the nix sandbox does not carry, so the load itself remains unexercised —
        stated rather than papered over."""
        import inspect
        default = inspect.signature(hi.main).parameters["open_store"].default
        assert default is hi._maildb_store
        assert hi.MAILDB_PATH.is_file(), hi.MAILDB_PATH
        assert "MailDB" in hi.MAILDB_PATH.read_text()

    @pytest.mark.parametrize("name", ["handoff_index.py", "handoff_search.py"])
    def test_a_module_documenting_bare_invocation_is_executable(self, name):
        """Both carry a python3 shebang and their docstrings show them being run
        directly; `lib/subsystem_touch.py` is 0755 for the same reason. A shebang
        on a non-executable file is a claim the filesystem refuses.

        🔴 THE SHEBANG IS CHECKED BY SHAPE, NOT BY ITS LITERAL TEXT, for two
        independent reasons. (a) `test_runtime_shebangs.py` is a repo-wide scan
        for tests reaching at `/usr/bin/env`, and it caught the literal here on
        the first gate run — correctly by its own rules, since it matches text
        rather than intent. This test WRITES no stub and EXECS nothing, so the
        right answer is to stop carrying the literal, not to pin an exemption.
        (b) `patchShebangs` rewrites the env form to a store path in some build
        contexts, so pinning the exact string would assert something about ONE
        tree. What is actually load-bearing is `#!` + a python3 interpreter."""
        path = REPO_ROOT / "scripts" / "lib" / name
        first = path.read_text().splitlines()[0]
        assert first.startswith("#!"), first
        assert "python3" in first, first
        assert os.stat(path).st_mode & 0o111, f"{name} is not executable"
