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
            hs.run_search(_store(), "zarfwidget", backend="memory",
                          repo="norepo-by-that-name").status,
            hs.run_search(hi.MemorySectionStore([]), "x", backend="memory").status,
        }
        assert emitted == set(hs.STATUSES)

    def test_every_status_has_an_exit_code_and_vice_versa(self):
        """🔴 PINNED TWO-WAY. A status missing from `EXIT_CODES` falls through
        `.get(status, 0)` and exits 0 — the fluent-zero failure one level up, in
        the one channel a scripted caller reads. A code for a status nothing
        emits is a contract that can never fire. `hit`/`no-match` are the two
        ANSWERS and are absent ON PURPOSE, so the ledger is stated as a partition
        rather than as a subset."""
        answers = {"hit", "no-match"}
        assert set(hs.EXIT_CODES) | answers == set(hs.STATUSES)
        assert set(hs.EXIT_CODES) & answers == set()
        # …and no two non-answers share a code, or the caller cannot tell a
        # broken environment from their own bad filter.
        assert len(set(hs.EXIT_CODES.values())) == len(hs.EXIT_CODES)
        assert all(v != 0 for v in hs.EXIT_CODES.values())


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
        self._log.append(("EXEC", " ".join(str(sql).split())))

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

    def kinds(self):
        """The log as a coarse sequence: TRUNCATE / INSERT / DDL / COMMIT."""
        out = []
        for e in self.log:
            if e[0] == "COMMIT":
                out.append("COMMIT")
            elif e[1].startswith("TRUNCATE"):
                out.append("TRUNCATE")
            elif e[1].startswith("INSERT"):
                out.append("INSERT")
            elif e[1].startswith("SELECT pg_advisory"):
                out.append("LOCK")
            else:
                out.append("DDL")
        return out


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

    def test_one_unmeasured_repo_among_several_still_refuses(self, tmp_path):
        """A PARTIAL derivation is the dangerous shape, not the obvious one: it
        produces rows, so a guard keyed only on emptiness would let it truncate
        and replace the corpus with a subset of itself."""
        good = tmp_path / "good"
        _write_repo(good, {"handoff-widget-relay.md": DOC_FULL})
        ds = [hi.derive_repo(good, label="good"),
              hi.derive_repo(tmp_path / "gone", label="gone")]
        rows = [s for d in ds for s in d.sections]
        assert rows  # the run DID produce rows
        refusal = hi.rebuild_refusal(ds, rows)
        assert refusal is not None
        assert "gone (no-such-directory)" in refusal


class TestWriteTransaction:
    def test_the_truncate_and_every_insert_share_ONE_transaction(self, tmp_path):
        """🔴 THE MUTANT-KILLER, AND THE ORDERING IS THE CLAIM.

        Three things are asserted, and each fails for its own mutation:
          * a TRUNCATE is issued at all           (mutant: `TRUNCATE` -> `pass`)
          * it comes BEFORE every INSERT          (mutant: reordered)
          * there is NO commit between the TRUNCATE and the last INSERT, and
            exactly ONE after it                  (mutant: the old two-commit
                                                   `truncate(); upsert()` shape)

        The schema DDL commits separately and BEFORE any of this — that is
        `ensure_schema`'s advisory-lock transaction and is not part of the data
        write — so the sequence is sliced from the TRUNCATE onward."""
        repo = tmp_path / "txrepo"
        _write_repo(repo, {"handoff-widget-relay.md": DOC_FULL})
        conn = RecordingConn()
        rc = hi.main(["--repo", str(repo), "--rebuild", "--write"],
                     open_store=_recording_store(conn))
        assert rc == hi.RC_OK

        kinds = conn.kinds()
        assert "TRUNCATE" in kinds, kinds
        tail = kinds[kinds.index("TRUNCATE"):]
        n_inserts = tail.count("INSERT")
        assert n_inserts == 9, tail  # DOC_FULL's nine retrieval units
        # TRUNCATE, then every INSERT, then exactly ONE commit — and nothing else.
        assert tail == ["TRUNCATE", *(["INSERT"] * n_inserts), "COMMIT"], tail

    def test_a_write_without_rebuild_issues_no_truncate(self, tmp_path):
        """The negative control for the same recorder: it must be capable of
        NOT seeing a TRUNCATE, or the assertion above proves only that the word
        is always present."""
        repo = tmp_path / "notruncate"
        _write_repo(repo, {"handoff-widget-relay.md": DOC_FULL})
        conn = RecordingConn()
        rc = hi.main(["--repo", str(repo), "--write"], open_store=_recording_store(conn))
        assert rc == hi.RC_OK
        kinds = conn.kinds()
        assert "TRUNCATE" not in kinds
        assert kinds.count("INSERT") == 9
        # Still one commit for the data write (plus ensure_schema's own).
        assert kinds[-1] == "COMMIT"

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
        assert len(payload) == 9
        assert {r["section"] for r in payload} == set(hi.SECTIONS)


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

        assert "claudedocs/sub/handoff-orphan.md" in hi.handoff_paths_on_disk(repo)
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

    def test_a_real_scan_with_no_findings_IS_an_all_clear(self, tmp_path):
        """The positive control: the all-clear must still be reachable, or the
        assertion above proves only that it was deleted."""
        repo = tmp_path / "realclean"
        _write_repo(repo, {"handoff-widget-relay.md": DOC_FULL})
        text = hi.render_derivation([hi.derive_repo(repo, label="realclean")])
        assert "handoff doc on disk is also in it" in text
        assert "NOT AN ALL-CLEAR" not in text


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

    def test_the_four_statuses_render_with_no_shared_opening_phrase(self):
        """🔴 WIDENED FROM TWO TO FOUR. Each zero must be machine-distinguishable
        from the others, or a caller reading prose conflates them."""
        broken = hs.render(hs.run_search(hi.MemorySectionStore([]), "zarfwidget",
                                        backend="memory"))
        scope = hs.render(hs.run_search(_store(), "zarfwidget", backend="memory",
                                        repo="norepo"))
        genuine = hs.render(hs.run_search(_store(), "hexapoddery", backend="memory"))
        hit = hs.render(hs.run_search(_store(), "zarfwidget", backend="memory"))

        marks = {
            "ran against NOTHING": broken,
            "the filter, not the corpus, is what is empty": scope,
            "the index WAS searched": genuine,
            "section(s), best first": hit,
        }
        for phrase, owner in marks.items():
            for other in (broken, scope, genuine, hit):
                if other is owner:
                    assert phrase in other, phrase
                else:
                    assert phrase not in other, phrase

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
        assert rc == hs.EXIT_CODES["empty-scope"] == 4
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
