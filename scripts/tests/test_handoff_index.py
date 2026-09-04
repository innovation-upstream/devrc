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
import inspect
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


def _norm(text: str) -> str:
    """Collapse whitespace. The ONLY normalisation the prose pins apply, so
    re-wrapping an f-string is free and every WORD stays load-bearing."""
    return " ".join(text.split())


#: 🔴 THE WHOLE ORPHAN SENTENCE, PINNED. Three substring assertions (headline,
#: label list, remedy command) let a mutant rewrite "only ever" to "ALWAYS" and
#: survive — `claude/RULES.md`: when the artifact under test IS prose, a guard on
#: WORDS is walkable by REWORDING, so pin the whole normalised string. Written
#: here rather than imported so the test states the expectation independently of
#: the implementation it checks.
#:
#: 🔴 THE TAIL CHANGED IN ROUND 5: it used to end at "remove the rows
#: deliberately with `--rebuild --prune --write` (which refuses unless every
#: handle is SET …)", which is a command the state it describes — a repo
#: GENUINELY GONE — makes impossible on every host, because the handles are
#: existence-guarded. The retired-repo route (drop the handle from the nix file
#: AND from `REPO_ENV_HANDLES`, ship, then prune) is now named, and
#: `TestARetiredRepoIsToldTheOneRemedyThatWorks` proves it is the one that
#: actually unblocks the prune rather than merely asserting the words.
_EXPECTED_ORPHAN_WARNING = (
    "⚠ ORPHANED LABELS — the table holds 2 repo label(s) THIS config does not "
    "name: plimforth, wibbleton-retired. A query can still answer FROM them, and "
    "nothing on THIS host will refresh them — the index is shared, so another "
    "host whose config DOES name them still can. They are NOT deleted: a rebuild "
    "only ever deletes repos it was told about. If the repo is meant to be "
    "indexed, RE-ADD ITS HANDLE — that is the fix whenever the checkout still "
    "exists somewhere, and an unset handle is the ordinary reason a label reads "
    "as unnamed here. Only when the repo is genuinely gone do you remove the "
    "rows, and HOW depends on whether any host still has the checkout. If one "
    "does, run `--rebuild --prune --write` THERE: it refuses unless every handle "
    "is SET and every repo MEASURES, because an absent or renamed checkout looks "
    "exactly like a removed one. If the checkout is gone EVERYWHERE, no host can "
    "ever satisfy that — the handles are existence-guarded, so an absent "
    "checkout is an unset handle on all of them — and the fix is a CONFIG "
    "change, not a command: drop the handle from nix/agent-handles.nix and from "
    "handoff_index.REPO_ENV_HANDLES, ship it, and then --prune."
)

#: 🔴 THE OTHER HALF OF THE SAME DEFECT, PINNED THE SAME WAY. `--prune`'s
#: config-width refusal offered "Set every handle and re-run --prune from a host
#: that has all the checkouts — or drop --prune", and for a RETIRED repo neither
#: exists: the checkout is gone everywhere, so no host has the handle set, and
#: dropping `--prune` leaves the rows. Two handles, in `REPO_ENV_HANDLES` order.
_EXPECTED_PRUNE_CONFIG_REFUSAL = (
    "REFUSING --rebuild --prune: 2 of 4 repo handle(s) are UNSET — $DATAPACKET, "
    "$CIVITAI. --prune deletes every stored label THIS config does not name, "
    "which is only sound if this config is as wide as the corpus; an unset "
    "handle narrows it SILENTLY (the repo produces no derivation at all, so it "
    "is not even UNMEASURED), and its rows would be collected as 'removed from "
    "config'. If those checkouts still exist somewhere, re-run --prune from a "
    "host that has ALL of them. If the repo is RETIRED and its checkout is gone "
    "everywhere, NO host can satisfy this guard — the handle is "
    "existence-guarded, so it is unset on all of them: drop it from BOTH "
    "nix/agent-handles.nix and handoff_index.REPO_ENV_HANDLES, ship that, and "
    "then --prune, which stops asking for a handle the config no longer has. "
    "Dropping --prune is not a third route to the same place: it un-blocks the "
    "REBUILD but deletes only what this run MEASURED, so the rows you came to "
    "remove stay exactly where they are."
)


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


def _apply_handles(monkeypatch, mapping: dict[str, str]) -> None:
    """Put `REPO_ENV_HANDLES` in exactly the state `mapping` describes.

    🔴 IT CLEARS EVERY HANDLE FIRST. These tests run on a real workbench where
    `$DEVRC`/`$HOMELAB`/… are exported by `.zshenv`, so a test that only SETS the
    handles it cares about inherits the rest and measures a config it did not
    construct — and `--prune`'s config-width guard is a claim about the WHOLE
    handle set, so an inherited handle is the difference between refusing and
    proceeding. `monkeypatch` restores the real environment afterwards."""
    for handle in hi.REPO_ENV_HANDLES:
        monkeypatch.delenv(handle, raising=False)
    for handle, value in mapping.items():
        monkeypatch.setenv(handle, value)


def _every_handle_set(tmp_path, *, docs=True) -> dict[str, str]:
    """A COMPLETE config: one real, readable repo per `REPO_ENV_HANDLES` entry.

    🔴 THE COMPLETE CONFIG IS NOW A PRECONDITION OF `--prune`, so a fixture that
    sets two of four handles no longer reaches the guard under test — it trips
    `prune_config_refusal` first. Each repo is named after its handle so a
    delete-scope assertion reads as a list of labels, and every label is a
    nonsense word distinct from every other fixture's."""
    out: dict[str, str] = {}
    for handle in hi.REPO_ENV_HANDLES:
        root = tmp_path / f"{handle.lower()}repo"
        _write_repo(root, {"handoff-widget-relay.md": DOC_FULL} if docs else {},
                    commit=docs)
        out[handle] = str(root)
    return out


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
        assert _norm(warning[0]) == _norm(_EXPECTED_ORPHAN_WARNING)
        # The negative control: no orphans, no sentence. A per-run "0 orphaned"
        # buries the real ones.
        assert hi.orphan_label_warning(()) == ()

    def test_a_label_this_run_DELETED_is_no_longer_an_orphan(self, tmp_path):
        """🔴 F2, AT THE PURE LEVEL. `orphan_labels` read only the PRE-write
        `stored` list, so a successful `--prune` reported the rows it had just
        deleted as "NOT deleted". It now subtracts the run's BOUND DELETE SCOPE —
        the thing that actually happened — rather than an `args.prune` boolean,
        which would be a second spelling of that fact, free to drift from it.

        Asserted as a differential on `deleted` alone: same derivations, same
        stored set, one argument apart."""
        ds, stored = self._renamed_checkout_fixture(tmp_path)
        assert hi.orphan_labels(ds, stored, scoped=False) == (
            "plimforth", "wibbleton-retired")
        assert hi.orphan_labels(ds, stored, scoped=False,
                                deleted=("wibbleton-retired",)) == ("plimforth",)
        assert hi.orphan_labels(
            ds, stored, scoped=False,
            deleted=("plimforth", "wibbleton-retired")) == ()
        # …and a non-prune scope (the MEASURED labels) subtracts nothing, because
        # a measured label is configured by construction and never an orphan.
        measured = tuple(d.label for d in ds if d.unmeasured is None)
        assert measured
        assert hi.orphan_labels(ds, stored, scoped=False, deleted=measured) == (
            "plimforth", "wibbleton-retired")

    def test_the_orphan_sentence_is_pinned_as_a_WHOLE_normalised_string(self):
        """🔴 F5's SECOND SURVIVING MUTANT. Three substring assertions — the
        headline, the label list and the remedy command — let a mutant rewrite
        "a rebuild **only ever** deletes repos it was told about" to "**ALWAYS**"
        and survive. `claude/RULES.md`: when the artifact under test IS prose, a
        guard on WORDS is walkable by REWORDING, so pin the WHOLE normalised
        string. A cosmetic reword now fails this test; that is the intended cost
        of a machine-readable claim.

        Normalised on whitespace only, so re-wrapping the f-string is free and
        every WORD is load-bearing."""
        got = hi.orphan_label_warning(("plimforth", "wibbleton-retired"))
        assert len(got) == 1
        assert _norm(got[0]) == _norm(_EXPECTED_ORPHAN_WARNING)
        # 🔴 THE CLAIMS THAT WERE FALSE, named individually so a future reword
        # cannot quietly restore them while the whole-string pin is updated to
        # match. "Nothing will ever refresh them" is a claim about EVERY host
        # over a SHARED index; and offering the destructive command before
        # "re-add the handle" pointed at deletion in the state where an unset
        # handle is the likeliest cause.
        assert "Nothing will ever refresh" not in got[0]
        assert got[0].index("RE-ADD ITS HANDLE") < got[0].index("--prune --write")

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

    def test_a_SUCCESSFUL_prune_does_not_report_what_it_just_deleted(
            self, tmp_path, capsys, monkeypatch):
        """🔴 F2 — THE SUCCESSFUL PRUNE PATH HAD NO END-TO-END TEST AT ALL, and
        it was the one path where the report contradicted the transaction above
        it. MEASURED: `--rebuild --prune --write` DELETEd `wibbleton-retired` and
        then printed "the table holds 1 repo label(s) this config does not name:
        wibbleton-retired … They are NOT deleted … Remove them deliberately with
        `--rebuild --prune --write`" — the remedy for a state the run had just
        left, naming rows that no longer existed.

        The positive control only ever drove the pure functions, which is why
        `stored` being the PRE-write read went unnoticed: nothing joined
        `orphan_labels` to the delete scope except `main`.

        The DELETE scope is asserted too, so this cannot pass by the prune having
        quietly stopped working — a silent no-op would also print no orphan."""
        _apply_handles(monkeypatch, _every_handle_set(tmp_path))
        labels = [f"{h.lower()}repo" for h in hi.REPO_ENV_HANDLES]
        conn = StoredReposConn((*labels, "wibbleton-retired"))
        rc = hi.main(["--rebuild", "--prune", "--write"],
                     open_store=_recording_store(conn))
        cap = capsys.readouterr()
        assert rc == hi.RC_OK
        # It really did prune — the orphan is in the bound DELETE scope…
        assert conn.params_for("DELETE") == [[sorted([*labels, "wibbleton-retired"])]]
        assert "wibbleton-retired" in cap.out  # named in the success line's scope
        # …and it is NOT then reported as a label that was left behind.
        assert "ORPHANED LABELS" not in cap.err
        assert "They are NOT deleted" not in cap.err

    def test_the_SAME_fixture_WITHOUT_prune_still_reports_the_orphan(
            self, tmp_path, capsys, monkeypatch):
        """The differential that makes the assertion above mean something: one
        flag apart, over the same repos and the same stored set. Silence after a
        prune is correct; silence without one would be the stale-corpus bug the
        report exists to prevent."""
        _apply_handles(monkeypatch, _every_handle_set(tmp_path))
        labels = [f"{h.lower()}repo" for h in hi.REPO_ENV_HANDLES]
        conn = StoredReposConn((*labels, "wibbleton-retired"))
        rc = hi.main(["--rebuild", "--write"], open_store=_recording_store(conn))
        cap = capsys.readouterr()
        assert rc == hi.RC_OK
        assert conn.params_for("DELETE") == [[sorted(labels)]]
        assert "⚠ ORPHANED LABELS" in cap.err
        assert "wibbleton-retired" in cap.err
        assert "They are NOT deleted" in cap.err

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
        the next is how it becomes decoration. Rejected, never reinterpreted.

        🔴 NO `--repo` HERE, AND ITS ABSENCE IS THE POINT. This used to pass
        `--repo` — which trips the OTHER usage error — so it asserted the
        no-rebuild message while driving an argv that is wrong for two reasons,
        and the two checks' ORDER was the only thing making it green."""
        rc = hi.main(["--prune", "--write"], open_store=_refusing_store())
        assert rc == hi.RC_USAGE
        assert "--prune only widens a --rebuild" in capsys.readouterr().err

    def test_prune_with_repo_and_no_rebuild_names_the_repo_conflict(
            self, tmp_path, capsys):
        """🔴 WHEN BOTH USAGE ERRORS APPLY, THE ONE THAT SURVIVES THE OBVIOUS FIX
        IS THE ONE TO SAY. `--prune --repo X` (no `--rebuild`) used to answer
        "pass --rebuild" — an instruction that leaves the argv still wrong, and
        reads as a nudge toward the dangerous combination. Both exit RC_USAGE, so
        only the sentence differs, and only the check order decides it."""
        repo = tmp_path / "zarfrepo"
        _write_repo(repo, {"handoff-widget-relay.md": DOC_FULL})
        rc = hi.main(["--repo", str(repo), "--prune", "--write"],
                     open_store=_refusing_store())
        err = capsys.readouterr().err
        assert rc == hi.RC_USAGE
        assert "--prune and --repo contradict each other" in err
        assert "--prune only widens a --rebuild" not in err

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
            self, tmp_path, capsys, monkeypatch):
        """The refusal reaches the exit code, and the store is never opened.

        🔴 EVERY HANDLE IS SET, AND ONE POINTS AT AN ABSENT CHECKOUT. This used
        to set two of four, which now trips `prune_config_refusal` FIRST — so the
        test would have gone on passing while never reaching the guard it names.
        The assertion is on the UNMEASURED wording, not on the shared "REFUSING
        --rebuild --prune" prefix, for the same reason: two guards whose messages
        share an opening are two guards one assertion cannot tell apart."""
        handles = _every_handle_set(tmp_path)
        handles[hi.REPO_ENV_HANDLES[1]] = str(tmp_path / "plimforth-renamed")
        _apply_handles(monkeypatch, handles)
        rc = hi.main(["--rebuild", "--prune", "--write"], open_store=_refusing_store())
        err = capsys.readouterr().err
        assert rc == hi.RC_REFUSED
        assert "came back UNMEASURED" in err
        assert "plimforth-renamed (no-such-directory)" in err
        assert "handle(s) are UNSET" not in err


class TestPruneRefusesAConfigNarrowerThanTheCorpus:
    """🔴 THE OTHER HALF OF `--prune`'s PRECONDITION, AND THE ONE THE FIRST ROUND
    MISSED. `rebuild_refusal` asks "did a configured repo fail to READ";
    `prune_config_refusal` asks "is this config as WIDE as the corpus". They are
    disjoint by construction — an UNSET handle produces no derivation, so it can
    never appear in the unmeasured set — which means **the narrowest configs
    produce ZERO unmeasured repos and the old guard was weakest exactly where the
    risk was highest.**"""

    def test_the_refusal_names_the_unset_handles(self):
        """PURE, and the negative control is the whole point: a guard that fired
        on a complete config would make `--prune` unusable rather than safe."""
        refusal = hi.prune_config_refusal(("DATAPACKET", "CIVITAI"))
        assert refusal is not None
        assert "handle(s) are UNSET" in refusal
        assert "$DATAPACKET, $CIVITAI" in refusal
        assert f"2 of {len(hi.REPO_ENV_HANDLES)}" in refusal
        assert hi.prune_config_refusal(()) is None

    def test_unset_handles_are_the_exact_complement_of_the_derived_config(self):
        """The relationship the guard rests on, pinned rather than assumed:
        `len(default_repos()) == len(REPO_ENV_HANDLES) - len(unset_repo_handles())`
        for every partial config, so "the config is narrower than the corpus" and
        "a handle is unset" are one fact and cannot drift apart.

        Driven over EVERY subset size, not just the empty and full ones — a
        boundary-only check cannot see a guard that is off by one in the middle."""
        handles = hi.REPO_ENV_HANDLES
        for n in range(len(handles) + 1):
            env = {h: f"/nonexistent/{h.lower()}" for h in handles[:n]}
            unset = hi.unset_repo_handles(env)
            assert set(unset) == set(handles[n:])
            assert len(unset) == len(handles) - n
            assert (hi.prune_config_refusal(unset) is None) == (n == len(handles))

    def test_an_UNSET_handle_makes_prune_refuse_END_TO_END(self, tmp_path, capsys,
                                                           monkeypatch):
        """🔴 THE REPRODUCTION, MEASURED BEFORE THE FIX. Two handles set to real,
        READABLE repos and two unset — the ordinary state on a host without those
        checkouts, since `nix/agent-handles.nix` existence-guards every handle —
        with a table holding all four labels. `--rebuild --prune --write` derived
        2 repos, BOTH measured, `bad == []`, no refusal fired, and bound the
        DELETE to all four labels at rc 0. Same ~62%-of-the-corpus blast radius as
        the unit's two-handle `Environment`, relocated to the operator's shell.

        The store is `_refusing_store()`, so this cannot pass on an exit code
        alone: a run that opened the table would raise."""
        handles = _every_handle_set(tmp_path)
        for dropped in hi.REPO_ENV_HANDLES[2:]:
            handles.pop(dropped)
        _apply_handles(monkeypatch, handles)

        rc = hi.main(["--rebuild", "--prune", "--write"], open_store=_refusing_store())
        err = capsys.readouterr().err
        assert rc == hi.RC_REFUSED
        assert "handle(s) are UNSET" in err
        for dropped in hi.REPO_ENV_HANDLES[2:]:
            assert f"${dropped}" in err
        # It is NOT the read-failure guard: both configured repos measured fine.
        assert "came back UNMEASURED" not in err

    def test_a_COMPLETE_config_still_prunes(self, tmp_path, capsys, monkeypatch):
        """🔴 THE DIFFERENTIAL THAT KEEPS THIS FROM BEING A PERMANENTLY-RED GATE.
        Same fixture, same argv, every handle SET: the run proceeds and the DELETE
        is bound to the four measured labels plus the stored orphan. Without this
        the guard above is indistinguishable from one that refuses everything —
        which is the mistake `rebuild_refusal`'s all-unmeasured arm had to unwind
        once already."""
        _apply_handles(monkeypatch, _every_handle_set(tmp_path))
        labels = [f"{h.lower()}repo" for h in hi.REPO_ENV_HANDLES]
        conn = StoredReposConn((*labels, "wibbleton-retired"))
        rc = hi.main(["--rebuild", "--prune", "--write"],
                     open_store=_recording_store(conn))
        assert rc == hi.RC_OK
        assert "REFUSING" not in capsys.readouterr().err
        assert conn.params_for("DELETE") == [[sorted([*labels, "wibbleton-retired"])]]

    def test_the_two_prune_guards_report_in_words_that_cannot_be_confused(
            self, tmp_path, monkeypatch, capsys):
        """🔴 A SEAM ASSERTION, NOT A COMPONENT ONE. Both refusals open
        "REFUSING --rebuild --prune", so an assertion on that prefix passes for
        either — which is how the UNMEASURED test above went on passing while the
        new guard was the one actually firing. The discriminating tokens are
        asserted as a MATRIX over the two states, each present in one and absent
        in the other."""
        complete = _every_handle_set(tmp_path)

        narrow = dict(complete)
        narrow.pop(hi.REPO_ENV_HANDLES[-1])
        _apply_handles(monkeypatch, narrow)
        hi.main(["--rebuild", "--prune"], open_store=_refusing_store())
        narrow_err = capsys.readouterr().err

        unreadable = dict(complete)
        unreadable[hi.REPO_ENV_HANDLES[-1]] = str(tmp_path / "plimforth-renamed")
        _apply_handles(monkeypatch, unreadable)
        hi.main(["--rebuild", "--prune"], open_store=_refusing_store())
        unmeasured_err = capsys.readouterr().err

        assert "handle(s) are UNSET" in narrow_err
        assert "came back UNMEASURED" not in narrow_err
        assert "came back UNMEASURED" in unmeasured_err
        assert "handle(s) are UNSET" not in unmeasured_err

    def test_the_gate_fires_in_DRY_RUN_too(self, tmp_path, monkeypatch, capsys):
        """A pre-flight that passes for a config the real run refuses is not a
        pre-flight — the same claim every other gate in this CLI already makes.
        Same argv one `--write` apart, and the SAME exit code."""
        handles = _every_handle_set(tmp_path)
        handles.pop(hi.REPO_ENV_HANDLES[-1])
        _apply_handles(monkeypatch, handles)
        dry = hi.main(["--rebuild", "--prune"], open_store=_refusing_store())
        dry_err = capsys.readouterr().err
        wet = hi.main(["--rebuild", "--prune", "--write"], open_store=_refusing_store())
        wet_err = capsys.readouterr().err
        assert dry == wet == hi.RC_REFUSED
        assert "handle(s) are UNSET" in dry_err and "handle(s) are UNSET" in wet_err
        assert "this was a DRY RUN" in dry_err
        assert "this was a DRY RUN" not in wet_err

    def test_a_NON_prune_rebuild_over_a_narrow_config_is_UNAFFECTED(
            self, tmp_path, monkeypatch, capsys):
        """🔴 THE BLAST-RADIUS CONTROL. The timer runs `--rebuild --write` with no
        `--prune`, on hosts that legitimately lack a checkout. If this guard
        reached that path it would fire a failure toast 4×/day forever, which is
        the permanently-red gate `claude/RULES.md` says is worse than no gate."""
        handles = _every_handle_set(tmp_path)
        for dropped in hi.REPO_ENV_HANDLES[2:]:
            handles.pop(dropped)
        _apply_handles(monkeypatch, handles)
        labels = [f"{h.lower()}repo" for h in hi.REPO_ENV_HANDLES[:2]]
        conn = StoredReposConn((*labels, "wibbleton-retired"))
        rc = hi.main(["--rebuild", "--write"], open_store=_recording_store(conn))
        assert rc == hi.RC_OK
        assert "REFUSING" not in capsys.readouterr().err
        assert conn.params_for("DELETE") == [[sorted(labels)]]

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

        # 🔴 …AND THE OTHER DIRECTION IS CHECKED TOO, AGAINST AN ENUMERATED
        # LEDGER. This assertion used to run one way only while `nix/home.nix`
        # described it as failing "if the two sets ever diverge" — and the two
        # sets DID diverge (nix declared five repo handles, the module read
        # four) with the suite green. A description wider than its check reads as
        # coverage and provides none, which is the failure that stops the next
        # person looking.
        #
        # It is an ENUMERATION and not a pattern, for `drift-check.sh`'s
        # `IGNORED` allowlist reason: a new handle is a candidate for the corpus
        # BY DEFAULT, and adding one to `agent-handles.nix` must force a decision
        # here rather than being silently excluded. Each entry carries the reason
        # it is not indexed, in the source.
        not_indexed = {
            # A CLI client checkout, not a project with a handoff corpus of its
            # own — the docs about it live in `civitai`. Indexing it would add a
            # repo whose mainline holds no `claudedocs/handoff-*.md`, i.e. a
            # standing PARTIAL/zero-doc contribution for no retrieval value.
            "CIVITAI_CLI",
        }
        extra = declared - set(hi.REPO_ENV_HANDLES)
        assert extra == not_indexed, (
            f"nix/agent-handles.nix declares {sorted(extra)} which "
            f"handoff_index.REPO_ENV_HANDLES does not read, and the deliberate "
            f"exclusions are {sorted(not_indexed)}. A new repo handle is a candidate "
            f"for the handoff corpus by DEFAULT: either add it to REPO_ENV_HANDLES, "
            f"or add it here with the reason it is excluded. 🔴 It also decides "
            f"whether `--prune` can run — `prune_config_refusal` requires every "
            f"REPO_ENV_HANDLES entry to be SET, so widening that tuple narrows the "
            f"hosts an operator can prune from."
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
        assert "DELETE (read in FULL, will be re-derived): zarfrepo" in out
        assert "KEPT (configured but UNMEASURED): plimforth-renamed" in out
        assert "This list is the COMPLETE delete scope." in out

    def test_a_dry_run_WITH_prune_names_its_own_blind_spot(self, tmp_path, capsys,
                                                           monkeypatch):
        """⚠ THE HALF THAT IS STATED, NOT CLOSED. `--prune`'s extra set lives in
        the TABLE, and a dry-run opens none. Printing a scope that silently omits
        those rows would be a pre-flight that under-reports what the run deletes,
        which is worse than one that names its blind spot.

        Every handle is SET, because a `--prune` run over a narrower config now
        refuses before it can print a plan at all."""
        _apply_handles(monkeypatch, _every_handle_set(tmp_path))
        rc = hi.main(["--rebuild", "--prune"], open_store=_refusing_store())
        out = capsys.readouterr().out
        assert rc == hi.RC_OK
        assert "a --dry-run CANNOT show it" in out
        assert "This list is the COMPLETE delete scope." not in out

    def test_the_WRITE_runs_plan_does_not_claim_a_dry_run_blind_spot(
            self, tmp_path, capsys, monkeypatch):
        """The nit under F4: the `--prune` plan line said "a --dry-run CANNOT
        show it" on `--write` runs too, where the bound scope IS printed a few
        lines later. Same argv one flag apart, so only `--write` explains it."""
        _apply_handles(monkeypatch, _every_handle_set(tmp_path))
        conn = StoredReposConn(tuple(f"{h.lower()}repo" for h in hi.REPO_ENV_HANDLES))
        rc = hi.main(["--rebuild", "--prune", "--write"],
                     open_store=_recording_store(conn))
        out = capsys.readouterr().out
        assert rc == hi.RC_OK
        assert "a --dry-run CANNOT show it" not in out
        assert "the full bound scope is printed with the row count below" in out

    def test_a_NON_prune_DRY_RUN_does_not_promise_a_report_it_cannot_make(
            self, tmp_path, capsys, monkeypatch):
        """🔴 F4 — THE PLAN LINE ASSERTED A REPORT AND THEN SHOWED NONE. It said
        "a stored label this config does not name is REPORTED and kept"; the
        report needs `store.repos()`, and a dry-run opens no store. The branch two
        lines above it names precisely this blind spot, which is what makes the
        claim read as considered rather than as an oversight.

        Asserted as the PAIR — same argv, one `--write` apart — because a test on
        the dry-run alone cannot tell "the promise is now conditional" from "the
        promise was deleted"."""
        _apply_handles(monkeypatch, _every_handle_set(tmp_path))
        rc_dry = hi.main(["--rebuild"], open_store=_refusing_store())
        dry = capsys.readouterr().out
        assert rc_dry == hi.RC_OK
        assert "is REPORTED" not in dry
        assert "cannot list them" in dry
        assert "This list is the COMPLETE delete scope." in dry

        conn = StoredReposConn(("marganserrepo",))
        rc_wet = hi.main(["--rebuild", "--write"], open_store=_recording_store(conn))
        cap = capsys.readouterr()
        assert rc_wet == hi.RC_OK
        # The write run DOES make the report, and says so where the dry-run says
        # it cannot — and the report it points at is actually printed.
        assert "is REPORTED (see ORPHANED LABELS below)" in cap.out
        assert "cannot list them" not in cap.out
        assert "⚠ ORPHANED LABELS" in cap.err

    def test_a_SCOPED_rebuild_plan_says_it_reports_nothing_either(
            self, tmp_path, capsys):
        """🔴 F5's SURVIVING MUTANT. `rebuild_plan_lines`' `if scoped:` mutated to
        `if False:` left the whole suite green: a scoped run fell through to the
        no-prune branch and printed "a stored label this config does not name is
        REPORTED and kept" — false twice over, because `orphan_labels` returns ()
        for a scoped run, so nothing is reported at all.

        Killed by asserting the scoped sentence is PRESENT and the no-prune one is
        ABSENT. Either assertion alone is walkable: an unconditional scoped
        sentence passes the first, and a deleted branch passes the second."""
        repo = tmp_path / "quixotryrepo"
        _write_repo(repo, {"handoff-widget-relay.md": DOC_FULL})
        rc = hi.main(["--repo", str(repo), "--rebuild"], open_store=_refusing_store())
        out = capsys.readouterr().out
        assert rc == hi.RC_OK
        assert "This run is SCOPED by --repo" in out
        assert "and none is REPORTED either" in out
        assert "No --prune, so a stored label" not in out
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


# --------------------------------------------------------------------------- #
# The README row is a CLAIM about this module, and it drifted
# --------------------------------------------------------------------------- #


def _readme_row(name: str) -> str:
    """The `scripts/README.md` table row whose first cell names `name`.

    🔴 BOUNDED TO ONE ROW. A `split()` on the module name runs to the end of a
    2,000-line file, so an assertion about "the row" would happily be satisfied
    by any later row that happens to mention the same status word."""
    text = (REPO_ROOT / "scripts" / "README.md").read_text()
    rows = [ln for ln in text.splitlines() if ln.startswith(f"| `{name}` |")]
    assert len(rows) == 1, f"expected exactly one README row for {name}, got {len(rows)}"
    return rows[0]


class TestTheReadmeRowMatchesTheExitCodeLedger:
    """🔴 F3 — THE ROW SAID `handoff_search` HAS **FOUR** ZEROS AND FOUR EXIT
    CODES. It had five: `derived-zero-docs`/rc 7 was added and the same commit
    updated the SIBLING `lib/handoff_index.py` row and not this one. Nothing
    pinned it, so the prose was free to describe a CLI that no longer existed —
    and a reader trusting "four causes" would read rc 7 as an undocumented code.

    Pinned against the module's own ledgers rather than against a literal, so a
    sixth zero fails HERE instead of being discovered by whoever hits it."""

    _WORDS = {0: "zero", 1: "one", 2: "two", 3: "three", 4: "four", 5: "five",
              6: "six", 7: "seven", 8: "eight", 9: "nine"}

    def test_every_status_keyed_exit_code_is_named_in_the_row(self):
        """The direction that catches an ADDED code. `EXIT_CODES` holds every
        status whose rc depends on the status alone; each must appear in the row
        as `(N —`, the shape the prose uses to introduce one."""
        row = _readme_row("lib/handoff_search.py")
        for status, code in hs.EXIT_CODES.items():
            assert f"({code} —" in row, (
                f"scripts/README.md's lib/handoff_search.py row does not document "
                f"exit code {code} ({status}). Every entry in "
                f"handoff_search.EXIT_CODES must be named there."
            )

    def test_the_row_counts_the_zeros_the_module_actually_has(self):
        """The direction that catches a STALE COUNT — the actual defect. The
        zeros are every status that is not `hit`: `no-match` is a zero that is an
        ANSWER, and the row counts it (it is the `(0, an answer)` entry)."""
        row = _readme_row("lib/handoff_search.py")
        zeros = [s for s in hs.STATUSES if s != "hit"]
        word = self._WORDS[len(zeros)]
        assert f"A zero has **{word}** causes" in row, (
            f"scripts/README.md claims a different number of zero-causes than "
            f"handoff_search.STATUSES has ({len(zeros)}: {zeros}). Update the row."
        )
        assert f"carrying {word} exit codes" in row

    def test_the_count_word_map_is_not_silently_short(self):
        """The instrument's own control: a `KeyError` here would fail the test
        above for the wrong reason, and a `.get(…, 'four')` would make it
        permanently green. Asserted rather than assumed."""
        assert len(hs.STATUSES) - 1 in self._WORDS


# --------------------------------------------------------------------------- #
# rc 7 — "the repos hold no handoff docs" is TWO mechanisms behind one zero
# --------------------------------------------------------------------------- #


def _blob_of(repo: Path, ref: str, path: str) -> str:
    out = subprocess.run(["git", "-C", str(repo), "rev-parse", f"{ref}:{path}"],
                         check=True, capture_output=True, text=True)
    return out.stdout.strip()


def _break_every_doc_blob(repo: Path) -> tuple[str, ...]:
    """Delete the loose object behind every handoff doc in the mainline ref.

    🔴 THE REAL MECHANISM, NOT A MONKEYPATCH. `git ls-tree` reads the TREE
    object, `git show <ref>:<path>` reads the BLOB, and they are separate objects
    — so removing the blob leaves the path listed and its content unreadable,
    which is what an incomplete or partially-fetched object store looks like.
    Stubbing `doc_text_at_ref` would test the stub and could not show that the
    two git commands really do disagree."""
    ref, _ = hi.resolve_mainline(repo)
    assert ref is not None
    tracked = hi.handoff_paths_in_ref(repo, ref)
    assert tracked, "the fixture must commit at least one handoff doc"
    for path in tracked:
        sha = _blob_of(repo, ref, path)
        (repo / ".git" / "objects" / sha[:2] / sha[2:]).unlink()
    return tracked


class TestAnUnreadableDocIsNotAnAbsentOne:
    """🔴 F7 — `derive_repo` reported "this ref holds no handoff docs" and "git
    could not produce any of the handoff docs this ref holds" as ONE value:
    `docs == 0`, `unmeasured is None`. `handoff_search`'s rc 7 then asserted the
    first for both, telling a reader to WRITE a doc that was already committed.

    Same conflation as `handoff_paths_in_ref`'s `None`-vs-`()`, one function
    later, and the same `claude/RULES.md` rule: an EMPTY RESULT cannot
    distinguish two mechanisms — go find the step that differs. Here the two
    disagree about `git ls-tree`, which lists the path either way."""

    def test_the_derivation_records_the_paths_it_could_not_read(self, tmp_path):
        """REPRODUCED, not inferred. The pre-fix state is asserted alongside the
        new field so the record shows exactly what was and was not observable:
        `docs` and `unmeasured` are unchanged — this repo really is MEASURED —
        and `unreadable` is the only thing that separates it from a repo whose
        mainline is genuinely doc-free."""
        repo = tmp_path / "zarfrepo"
        _write_repo(repo, {"handoff-widget-relay.md": DOC_FULL})
        tracked = _break_every_doc_blob(repo)

        d = hi.derive_repo(repo, label="zarfrepo")
        assert d.unmeasured is None          # the repo resolved and enumerated
        assert d.docs == 0                   # …and derived nothing
        assert d.unreadable == tracked       # …because THIS is why
        assert any("UNREADABLE" in w for w in d.warnings)

    def test_a_genuinely_doc_free_ref_records_NO_unreadable_paths(self, tmp_path):
        """The negative control. Without it, `unreadable` could be unconditional
        and every assertion above would still pass."""
        repo = tmp_path / "emptyrepo"
        _write_repo(repo, {}, commit=False)
        subprocess.run(["git", "-C", str(repo), "commit", "-qm", "e", "--allow-empty"],
                       check=True)
        d = hi.derive_repo(repo, label="emptyrepo")
        assert d.unmeasured is None
        assert d.docs == 0
        assert d.unreadable == ()

    def test_rc7_does_not_claim_the_repo_holds_no_docs(self, tmp_path, capsys):
        """🔴 THE FALSE SENTENCE, THROUGH THE CLI. Both states exit 7 — the status
        is correct — but the two remedies are opposite (write a doc / repair the
        object store), so the prose is the whole of the diagnosis."""
        repo = tmp_path / "zarfrepo"
        _write_repo(repo, {"handoff-widget-relay.md": DOC_FULL})
        _break_every_doc_blob(repo)

        rc = hs.main(["--query", "zarfwidget", "--offline", "--offline-repo", str(repo)])
        out = capsys.readouterr().out
        assert rc == 7
        assert "ZERO HANDOFF DOCS DERIVED — and NOT because there are none" in out
        assert "resolved a mainline ref and hold no" not in out
        # It names the document it could not read, and points at the object store
        # rather than at writing a doc that already exists.
        assert "claudedocs/handoff-widget-relay.md" in out
        assert "git fsck" in out

    def test_the_genuinely_doc_free_rc7_sentence_is_unchanged(self, tmp_path, capsys):
        """The differential: same status, same exit code, and the doc-free repo
        must still get the sentence that IS true of it. A fix that rendered the
        unreadable prose for both would trade one false sentence for another."""
        repo = tmp_path / "emptyrepo"
        _write_repo(repo, {}, commit=False)
        subprocess.run(["git", "-C", str(repo), "commit", "-qm", "e", "--allow-empty"],
                       check=True)
        rc = hs.main(["--query", "zarfwidget", "--offline", "--offline-repo", str(repo)])
        out = capsys.readouterr().out
        assert rc == 7
        assert "resolved a mainline ref and hold no" in out
        assert "NOT because there are none" not in out
        assert "git fsck" not in out

    def test_the_machine_surface_carries_the_same_discriminator(self, tmp_path,
                                                                capsys):
        """🔴 A CAVEAT SPELLED IN ONE RENDERER IS A CAVEAT THE OTHER DOES NOT
        HAVE — and `--json` is the surface consumed without a human reading it.
        `indexed_docs: 0` with an empty `unmeasured` is exactly the shape that
        reads as 'these repos hold no handoff docs'."""
        repo = tmp_path / "zarfrepo"
        _write_repo(repo, {"handoff-widget-relay.md": DOC_FULL})
        _break_every_doc_blob(repo)
        rc = hs.main(["--query", "zarfwidget", "--offline", "--json",
                      "--offline-repo", str(repo)])
        doc = json.loads(capsys.readouterr().out)
        assert rc == 7
        assert doc["indexed_docs"] == 0
        assert doc["unmeasured"] == []
        assert doc["unreadable"] == [
            {"repo": "zarfrepo", "doc_path": "claudedocs/handoff-widget-relay.md"}
        ]

    def test_the_indexers_own_json_carries_it_too(self, tmp_path, capsys):
        """The same fact on the OTHER CLI's machine surface, because a consumer
        deciding whether the corpus is really empty may be reading either."""
        repo = tmp_path / "zarfrepo"
        _write_repo(repo, {"handoff-widget-relay.md": DOC_FULL})
        _break_every_doc_blob(repo)
        rc = hi.main(["--repo", str(repo), "--json"], open_store=_refusing_store())
        doc = json.loads(capsys.readouterr().out)
        assert rc == hi.RC_OK
        assert doc["totals"]["docs"] == 0
        assert doc["repos"][0]["unreadable"] == [
            "claudedocs/handoff-widget-relay.md"
        ]


# --------------------------------------------------------------------------- #
# Round 5 — operator guidance is a claim about the state it is printed in
# --------------------------------------------------------------------------- #


class TestARetiredRepoIsToldTheOneRemedyThatWorks:
    """🔴 EVERY REMEDY THIS CLI PRINTS NAMES A COMMAND; NOT ALL OF THEM CHECKED
    THAT THE STATE ALLOWS IT. Two shipped sentences recommended `--rebuild
    --prune --write` (or "set every handle and re-run it") for a repo that is
    GENUINELY RETIRED — and `nix/agent-handles.nix` existence-guards every handle
    on its directory, so a deleted checkout leaves that handle unset on EVERY
    host. `prune_config_refusal` then refuses everywhere, forever: the two
    remedies the tool offered were "do it on a host that cannot exist" and "drop
    --prune", which un-blocks the rebuild while leaving the exact rows the
    operator came to remove.

    The prose pins below are WHOLE NORMALISED STRINGS, for
    `_EXPECTED_ORPHAN_WARNING`'s reason. The behavioural test after them is what
    makes those words more than an assertion about themselves: it drives the
    retired-repo state end to end and shows that the newly-named route — drop the
    handle from `REPO_ENV_HANDLES` — is the one that actually lets the prune run,
    while the state the old sentences described stays refused."""

    def test_the_orphan_warning_names_the_config_route(self):
        got = hi.orphan_label_warning(("plimforth", "wibbleton-retired"))
        assert _norm(got[0]) == _norm(_EXPECTED_ORPHAN_WARNING)

    def test_the_prune_config_refusal_names_the_config_route(self):
        """Pinned whole, so a reword that drops the retired-repo route fails.

        The literal counts in `_EXPECTED_PRUNE_CONFIG_REFUSAL` are asserted
        against the tuple first: adding a handle legitimately changes "2 of 4",
        and a bare string mismatch would send the next reader hunting a prose
        change that never happened."""
        assert len(hi.REPO_ENV_HANDLES) == 4, (
            "REPO_ENV_HANDLES changed size — update the '2 of N' literal in "
            "_EXPECTED_PRUNE_CONFIG_REFUSAL, and re-read it: the message names "
            "the handles by value too."
        )
        got = hi.prune_config_refusal(("DATAPACKET", "CIVITAI"))
        assert got is not None
        assert _norm(got) == _norm(_EXPECTED_PRUNE_CONFIG_REFUSAL)

    def test_dropping_the_handle_is_what_actually_unblocks_the_prune(
            self, tmp_path, monkeypatch, capsys):
        """🔴 THE BEHAVIOURAL HALF — a guard on WORDS is walkable by REWORDING,
        and a remedy is a claim that a command WORKS, which only running it can
        settle. Three runs over ONE fixture, differing in exactly one thing:

          1. the retired repo's handle is unset (the state on every host once its
             checkout is deleted) -> rc 4, the store is never opened. This is the
             state the old prose told the operator to "re-run from a host that
             has all the checkouts" out of, and no such host exists.
          2. same argv, `--prune` dropped -> rc 0, and the retired label is NOT
             in the bound DELETE. The rebuild proceeds; the rows stay. That is
             the second old remedy, measured, and it does not remove them.
          3. same argv as (1), with the retired handle dropped from
             `REPO_ENV_HANDLES` — the config change the message now names -> rc 0
             and the retired label IS in the bound DELETE.

        Only (3) reaches the outcome the operator wanted, which is the whole
        content of the fixed sentence."""
        retired = hi.REPO_ENV_HANDLES[-1]
        retired_label = f"{retired.lower()}repo"
        live = _every_handle_set(tmp_path)
        del live[retired]                      # its checkout no longer exists
        _apply_handles(monkeypatch, live)
        stored = tuple(f"{h.lower()}repo" for h in hi.REPO_ENV_HANDLES)

        # 1. the remedy the message used to lead with: impossible here.
        rc = hi.main(["--rebuild", "--prune", "--write"], open_store=_refusing_store())
        assert rc == hi.RC_REFUSED
        assert "handle(s) are UNSET" in capsys.readouterr().err

        # 2. the remedy it offered second: succeeds, and removes nothing.
        conn = StoredReposConn(stored)
        rc = hi.main(["--rebuild", "--write"], open_store=_recording_store(conn))
        capsys.readouterr()
        assert rc == hi.RC_OK
        deleted = conn.params_for("DELETE")[0][0]
        assert retired_label not in deleted, (
            "a --prune-less rebuild must not delete the retired label — if it "
            "does, the message's claim that dropping --prune leaves the rows is "
            "false and this whole remedy needs rewriting"
        )

        # 3. the remedy it now names: drop the handle from the config.
        monkeypatch.setattr(
            hi, "REPO_ENV_HANDLES",
            tuple(h for h in hi.REPO_ENV_HANDLES if h != retired))
        conn = StoredReposConn(stored)
        rc = hi.main(["--rebuild", "--prune", "--write"],
                     open_store=_recording_store(conn))
        err = capsys.readouterr().err
        assert rc == hi.RC_OK
        assert "REFUSING" not in err
        assert retired_label in conn.params_for("DELETE")[0][0]

    def test_the_all_unmeasured_refusal_does_not_offer_a_run_that_writes_nothing(
            self, tmp_path, monkeypatch, capsys):
        """🔴 THE THIRD SITE OF THE SAME SHAPE, and its old remedy was measured
        false rather than merely unhelpful. The arm said "re-run without
        --rebuild to refresh what CAN be measured" in the one state where NOTHING
        can be measured. MEASURED, same argv minus `--rebuild`: rc 0, `wrote 0
        section row(s)`, store opened — a silent success, which is verbatim the
        failure the sentence directly above it gives as the reason to refuse.

        The second half of this test is that measurement, run here so the prose
        assertion cannot outlive the behaviour it describes."""
        bad = {h: str(tmp_path / f"nope-{h.lower()}") for h in hi.REPO_ENV_HANDLES}
        _apply_handles(monkeypatch, bad)

        rc = hi.main(["--rebuild", "--write"], open_store=_refusing_store())
        err = capsys.readouterr().err
        assert rc == hi.RC_REFUSED
        assert "ALL 4 repo(s) came back UNMEASURED" in err
        assert "Fix the repo handles — in THIS state that is the only remedy" in err
        assert "writes 0 row(s) and exits 0" in err
        # The retracted remedy must not survive anywhere in the message.
        assert "refresh what CAN be measured" not in err

        # …and the claim it now makes about that run is true.
        conn = StoredReposConn(())
        rc = hi.main(["--write"], open_store=_recording_store(conn))
        out = capsys.readouterr().out
        assert rc == hi.RC_OK
        assert "wrote 0 section row(s)" in out
        assert "INSERT" not in conn.kinds()


class TestTheNoMatchRemedyMatchesTheFilterTheRunActuallyHAD:
    """🔴 THE FOURTH SITE, IN THE OTHER MODULE. `NO MATCH` told every reader to
    "widen --repo / --section", including the common case where the run passed
    neither: there is no filter to widen, and the advice reads as "your scope
    caused this zero" when the scope was the entire index. `outcome.filtered`
    is the discriminator and was already being read two lines above for the
    parenthetical, which is what makes this a miss rather than a missing signal.

    A differential over the ONE variable, so nothing else can explain the two
    messages: same corpus, same query that matches nothing, `--section` present
    or absent."""

    def _repo(self, tmp_path):
        repo = tmp_path / "zarfrepo"
        _write_repo(repo, {"handoff-widget-relay.md": DOC_FULL})
        return repo

    def test_an_unfiltered_no_match_does_not_send_you_to_widen_a_filter(
            self, tmp_path, capsys):
        repo = self._repo(tmp_path)
        rc = hs.main(["--query", "brimsculp", "--offline", "--offline-repo", str(repo)])
        out = capsys.readouterr().out
        # rc 0: `no-match` is the one zero that IS an answer about the corpus —
        # the index was searched and holds nothing on this. Asserted so the test
        # cannot pass off some other status' message as the one under test.
        assert rc == 0
        assert "NO MATCH" in out
        assert "widen --repo / --section" not in out
        assert "There is no filter to widen" in out
        assert "every section in the index was already in scope" in out

    def test_a_FILTERED_no_match_still_says_widen(self, tmp_path, capsys):
        """The differential. Without it the fix is indistinguishable from one
        that deleted the widen advice outright, which would be a worse message
        for the case it IS true of."""
        repo = self._repo(tmp_path)
        rc = hs.main(["--query", "brimsculp", "--offline", "--offline-repo", str(repo),
                      "--section", "goal"])
        out = capsys.readouterr().out
        assert rc == 0
        assert "NO MATCH" in out
        assert "widen --repo / --section" in out
        assert "There is no filter to widen" not in out


class TestThePlanDescribesTheRunThatActuallyHappens:
    """🔴 THE REBUILD PLAN WAS PRINTED ABOVE THE GATES, SO ON A REFUSING RUN
    EVERY SENTENCE IN IT WAS ABOUT A RUN THAT NEVER HAPPENED. `rebuild_plan_lines`
    is handed `args.write` — the INTENT — and its branches describe an OUTCOME.

    MEASURED before the fix, both at rc 4 with the store never opened:

      * `--rebuild --prune --write`, three handles set and one unset: stdout
        carried "this run opens the table, and the full bound scope is printed
        with the row count below". The recorded SQL log was `[]` — no table was
        opened, no bound scope and no row count were ever printed.
      * `--rebuild --write` with every repo unmeasured: "…is REPORTED (see
        ORPHANED LABELS below) and kept", above a run that emitted no ORPHANED
        LABELS section at all.

    The fix is ORDERING — `main` prints the block after every gate has passed —
    chosen over threading a "this run will actually write" boolean, because that
    boolean IS "no gate fired" and would be a second spelling of the gate's own
    verdict, free to drift from it. Ordering also makes the two sentences that
    speak about a DIFFERENT run sound, since the gates are mode-identical.

    🔴 THE POSITIVE CONTROL IS NOT OPTIONAL HERE. Every assertion below is an
    ABSENCE, and a plan block deleted outright would satisfy all of them; the
    passing runs at the end are what separate "printed only when true" from
    "never printed"."""

    def _plan_absent(self, out, err):
        both = out + err
        assert "## rebuild delete scope" not in both
        assert "DELETE (read in FULL, will be re-derived)" not in both
        assert "KEPT (configured but UNMEASURED)" not in both

    def test_a_refusing_prune_write_prints_no_plan_promising_a_bound_scope(
            self, tmp_path, monkeypatch, capsys):
        handles = _every_handle_set(tmp_path)
        handles.pop(hi.REPO_ENV_HANDLES[-1])
        _apply_handles(monkeypatch, handles)
        rc = hi.main(["--rebuild", "--prune", "--write"], open_store=_refusing_store())
        out, err = capsys.readouterr()
        assert rc == hi.RC_REFUSED
        assert "handle(s) are UNSET" in err
        self._plan_absent(out, err)
        assert "the full bound scope is printed with the row count below" not in out + err

    def test_a_refusing_rebuild_write_does_not_promise_an_orphan_report(
            self, tmp_path, monkeypatch, capsys):
        bad = {h: str(tmp_path / f"nope-{h.lower()}") for h in hi.REPO_ENV_HANDLES}
        _apply_handles(monkeypatch, bad)
        rc = hi.main(["--rebuild", "--write"], open_store=_refusing_store())
        out, err = capsys.readouterr()
        assert rc == hi.RC_REFUSED
        self._plan_absent(out, err)
        assert "see ORPHANED LABELS below" not in out + err

    def test_a_refusing_prune_DRY_RUN_does_not_promise_what_the_write_run_prints(
            self, tmp_path, monkeypatch, capsys):
        """The third branch, and the one a `write` boolean would not have fixed:
        its claim is about a DIFFERENT run ("The --write run prints the full
        bound scope"), which is false in exactly the states where that run also
        refuses."""
        handles = _every_handle_set(tmp_path)
        handles.pop(hi.REPO_ENV_HANDLES[-1])
        _apply_handles(monkeypatch, handles)
        rc = hi.main(["--rebuild", "--prune"], open_store=_refusing_store())
        out, err = capsys.readouterr()
        assert rc == hi.RC_REFUSED
        self._plan_absent(out, err)
        assert "The --write run prints the full bound scope" not in out + err

    def test_a_refusing_SCOPED_rebuild_prints_no_plan_either(self, tmp_path, capsys):
        """A scoped run has its own plan branch, and its own way to refuse: a
        repo that resolves and yields zero rows. Included so the class covers a
        gate other than the two prune ones — the ordering fix is not per-gate,
        and a test suite that only exercised the prune gates could not say so."""
        repo = tmp_path / "norows"
        _write_repo(repo, {}, commit=False)
        subprocess.run(["git", "-C", str(repo), "commit", "-qm", "e", "--allow-empty"],
                       check=True)
        rc = hi.main(["--repo", str(repo), "--rebuild", "--write"],
                     open_store=_refusing_store())
        out, err = capsys.readouterr()
        assert rc == hi.RC_REFUSED
        assert "ZERO rows" in err
        self._plan_absent(out, err)

    def test_a_PASSING_write_still_prints_the_plan_ABOVE_the_row_count(
            self, tmp_path, monkeypatch, capsys):
        """🔴 THE POSITIVE CONTROL, AND IT PINS THE PLAN'S OWN CLAIM. The
        `--prune --write` branch says the bound scope is "printed with the row
        count BELOW", so the assertion is on ORDER, not merely on presence: the
        plan line must precede the success line in the same stream."""
        _apply_handles(monkeypatch, _every_handle_set(tmp_path))
        conn = StoredReposConn((*(f"{h.lower()}repo" for h in hi.REPO_ENV_HANDLES),
                                "wibbleton-retired"))
        rc = hi.main(["--rebuild", "--prune", "--write"],
                     open_store=_recording_store(conn))
        out = capsys.readouterr().out
        assert rc == hi.RC_OK
        assert "## rebuild delete scope" in out
        plan = out.index("the full bound scope is printed with the row count below")
        count = out.index("wrote ")
        assert plan < count, (
            "the plan promises the row count BELOW it; printing it after would "
            "make the sentence false in the run it is true of"
        )
        assert "(after DELETE of 5 repo label(s)" in out

    def test_a_PASSING_dry_run_still_prints_the_plan(self, tmp_path, capsys):
        """The other half of the control: the pre-flight `nix/home.nix` tells an
        operator to watch must not have been moved out of dry-run mode."""
        good = tmp_path / "zarfrepo"
        _write_repo(good, {"handoff-widget-relay.md": DOC_FULL})
        rc = hi.main(["--repo", str(good), "--rebuild"], open_store=_refusing_store())
        out = capsys.readouterr().out
        assert rc == hi.RC_OK
        assert "## rebuild delete scope" in out
        assert "DELETE (read in FULL, will be re-derived): zarfrepo" in out


class TestTheTwoPruneGuardsHaveAFIXEDOrder:
    """🟢 THE ORDER `main` CLAIMS WAS UNPINNED. `prune_config_refusal` runs
    before `rebuild_refusal` and the comment says so, but swapping the two
    survived the whole suite: `test_the_two_prune_guards_report_in_words_that
    _cannot_be_confused` builds each state SEPARATELY, and in a state where only
    one guard can fire the order is unobservable.

    The COMBINED state is the only one that can see it — a handle UNSET *and* a
    set handle pointing at a checkout that is not there — so it is built here.
    There is no safety consequence either way (both refuse at rc 4); what is at
    stake is which DIAGNOSIS the operator reads first, and the config-width one
    is the precondition for `--prune`'s whole premise."""

    def test_the_config_width_guard_wins_when_BOTH_could_fire(
            self, tmp_path, monkeypatch, capsys):
        handles = _every_handle_set(tmp_path)
        handles.pop(hi.REPO_ENV_HANDLES[-1])                       # UNSET
        handles[hi.REPO_ENV_HANDLES[1]] = str(tmp_path / "plimforth-renamed")  # UNMEASURED
        _apply_handles(monkeypatch, handles)

        rc = hi.main(["--rebuild", "--prune", "--write"], open_store=_refusing_store())
        err = capsys.readouterr().err
        assert rc == hi.RC_REFUSED
        # Both guards are ARMED in this state…
        assert hi.prune_config_refusal(hi.unset_repo_handles()) is not None
        derivations = [hi.derive_repo(p, label=Path(p).name)
                       for p, _ in hi.default_repos()]
        assert any(d.unmeasured for d in derivations), (
            "the fixture must also arm the read-failure guard, or this test "
            "measures a state in which the order is still unobservable"
        )
        # …and the config-width one is the message the operator gets.
        assert "handle(s) are UNSET" in err
        assert "came back UNMEASURED" not in err


class TestTheUnreadableReportIsMeasuredForMoreThanOneDoc:
    """🟢 EVERY W7 FIXTURE HAD EXACTLY ONE UNREADABLE DOC, so `for path in
    d.unreadable[:1]` SURVIVED the suite: with N == 1 the slice is the identity.
    `claude/RULES.md`'s "a fixture that can only ever produce the constant's own
    value cannot see a mutant that hardcodes the literal" — here the constant is
    the implicit 1.

    So: two repos, with 3 and 4 unreadable docs. Every number an assertion names
    is pairwise distinct and distinct from 1 and from the repo count — 7 total,
    3 and 4 per repo, 2 repos — so no mutant can survive by landing on a value
    the fixture could only ever have produced. The shipped code is correct; what
    was missing was any measurement that could tell."""

    #: 3 and 4, deliberately not equal to each other, to 1, or to the repo count.
    A_DOCS = ("handoff-quixotry-latch.md", "handoff-drumble-cache.md",
              "handoff-snarfle-probe.md")
    B_DOCS = ("handoff-marganser-log.md", "handoff-blimflark-path.md",
              "handoff-trundlebore-count.md", "handoff-flanterbly-sweep.md")

    def _broken_repo(self, root, names):
        """A repo whose mainline LISTS every doc and whose blobs are all gone.

        Each doc body is unique: identical content would share ONE blob, and
        `_break_every_doc_blob` would fail unlinking the second — the fixture
        would then be measuring fewer docs than it thinks it is."""
        docs = {
            name: DOC_FULL.replace("quixotry relay", f"{name[8:-3]} relay")
            for name in names
        }
        _write_repo(root, docs)
        return _break_every_doc_blob(root)

    def test_every_unreadable_doc_is_carried_across_the_offline_seam(self, tmp_path):
        a = tmp_path / "zarfrepo"
        b = tmp_path / "plimforthrepo"
        tracked_a = self._broken_repo(a, self.A_DOCS)
        tracked_b = self._broken_repo(b, self.B_DOCS)
        assert len(tracked_a) == 3 and len(tracked_b) == 4

        _, _, unmeasured, unreadable = hs._offline_store(
            [(str(a), "zarfrepo"), (str(b), "plimforthrepo")])
        assert unmeasured == ()          # both repos MEASURED — that is the trap
        assert unreadable == (
            *(("zarfrepo", p) for p in tracked_a),
            *(("plimforthrepo", p) for p in tracked_b),
        )
        assert len(unreadable) == 7

    def test_the_rc7_message_counts_and_lists_every_one_of_them(self, tmp_path,
                                                                 capsys):
        """🔴 THE COUNT AND THE LIST ARE SEPARATE CLAIMS AND BOTH ARE ASSERTED. A
        mutant that truncated the pairs would leave a correct-looking count if
        the count were derived from the same truncated tuple — it is not, so the
        two move together and a per-repo slice moves both. Naming 7 explicitly is
        what makes the truncation visible at all."""
        a = tmp_path / "zarfrepo"
        b = tmp_path / "plimforthrepo"
        tracked_a = self._broken_repo(a, self.A_DOCS)
        tracked_b = self._broken_repo(b, self.B_DOCS)

        rc = hs.main(["--query", "zarfwidget", "--offline",
                      "--offline-repo", str(a), "--offline-repo", str(b)])
        out = capsys.readouterr().out
        assert rc == 7
        assert "ZERO HANDOFF DOCS DERIVED — and NOT because there are none" in out
        assert "list 7 `claudedocs/handoff-*.md`" in out
        assert "The 2 repo(s) this run read" in out
        for path in tracked_a:
            assert f"zarfrepo:{path}" in out
        for path in tracked_b:
            assert f"plimforthrepo:{path}" in out

    def test_the_machine_surface_carries_all_seven_pairs(self, tmp_path, capsys):
        """The `--json` half, for `test_the_machine_surface_carries_the_same
        _discriminator`'s reason: a consumer deciding whether the corpus is
        really empty may be reading either surface, and a truncated list there is
        the same false answer without the prose to hint at it."""
        a = tmp_path / "zarfrepo"
        b = tmp_path / "plimforthrepo"
        tracked_a = self._broken_repo(a, self.A_DOCS)
        tracked_b = self._broken_repo(b, self.B_DOCS)

        rc = hs.main(["--query", "zarfwidget", "--offline", "--json",
                      "--offline-repo", str(a), "--offline-repo", str(b)])
        doc = json.loads(capsys.readouterr().out)
        assert rc == 7
        assert doc["unreadable"] == [
            *({"repo": "zarfrepo", "doc_path": p} for p in tracked_a),
            *({"repo": "plimforthrepo", "doc_path": p} for p in tracked_b),
        ]
        assert len(doc["unreadable"]) == 7

    def test_a_repo_with_ONE_unreadable_doc_is_still_reported_whole(self, tmp_path):
        """The lower-boundary control. `claude/RULES.md` asks for a boundary AND
        a middle; the N>1 cases above are the middle, and this is the boundary
        the old fixtures pinned — kept so a fix aimed at N>1 cannot break it."""
        repo = tmp_path / "zarfrepo"
        tracked = self._broken_repo(repo, self.A_DOCS[:1])
        _, _, _, unreadable = hs._offline_store([(str(repo), "zarfrepo")])
        assert unreadable == (("zarfrepo", tracked[0]),)


# --------------------------------------------------------------------------- #
# F8 — a repo this run could not read IN FULL is not a repo it may DELETE
# --------------------------------------------------------------------------- #


# 🔴 THREE DOCS, AND THE COUNT IS LOAD-BEARING. Breaking ONE of three makes
# `docs`, `len(unreadable)` and the total pairwise distinct (2, 1, 3), so a
# mutant that hardcodes any of those literals — or that keys the guard on
# `docs == 0` — cannot survive by accident of a fixture where they coincide.
#
# 🔴 AND THE THREE BODIES ARE DISTINCT, WHICH IS NOT COSMETIC. Git addresses a
# blob by the hash of its CONTENT, so two docs with identical text are ONE
# object: the first draft of this fixture reused `DOC_FULL` twice, and deleting
# "one" blob took out two documents — `docs` came back 1 instead of 2 and the
# partial case silently became the total one. A fixture that cannot express the
# distinction it is built to test is the same vacuous green one level down.
_TRIAD = {
    "handoff-glimmerwick-bus.md": DOC_FULL,
    "handoff-quorlbane-cache.md": DOC_SPARSE,
    "handoff-fandrelly-probe.md": DOC_FULL.replace(
        "widget-relay", "fandrelly-probe").replace("quixotry", "snorrelquim"),
}


def _break_doc_blob(repo: Path, path: str) -> None:
    """Delete the loose object behind ONE doc, leaving the tree entry listed.

    `_break_every_doc_blob`'s mechanism, applied to a single path — the same
    reason it is git and not a monkeypatch: `git ls-tree` reads the TREE and
    `git show` reads the BLOB, and only a real object store can show that the
    two commands disagree."""
    ref, _ = hi.resolve_mainline(repo)
    assert ref is not None
    (repo / ".git" / "objects" / _blob_of(repo, ref, path)[:2]
     / _blob_of(repo, ref, path)[2:]).unlink()


class TestARepoReadIncompletelyIsNotAuthoritativeOverItsRows:
    """🔴 F8 — REPRODUCED, then fixed. `--rebuild --write` over one healthy repo
    plus one whose committed doc blobs were removed from `.git/objects` bound
    BOTH labels to the DELETE, re-inserted only the healthy repo's rows, printed
    no PARTIAL notice and exited 0. The broken repo resolved its mainline ref, so
    `unmeasured is None`, and `unmeasured` was the only field the delete scope
    read.

    MEASURED before the fix, on the exact fixtures below's shape:
    `partial_scope_warnings() == ()`, `rebuild_refusal() is None`,
    `DELETE params == [['badrepo', 'goodrepo']]`, rc 0. The only signal anywhere
    in the run was one `⚠ UNREADABLE` line on stderr.

    🔴 AND THE FILING UNDERSTATED IT. Breaking ONE blob of several produces the
    same silent deletion from a run that looks HEALTHIER — it writes rows — so
    the guard is on ANY unreadable doc, not on "every doc unreadable". The
    ONE-of-three cases below are what separate the two fixes."""

    def test_the_predicate_withholds_authority_and_names_which_kind(self, tmp_path):
        """PURE, over all three states of one predicate, with the healthy repo as
        the negative control. Without that control an unconditional
        `incomplete_reason` would satisfy every other assertion here."""
        healthy = tmp_path / "glimmerrepo"
        _write_repo(healthy, _TRIAD)
        whole = hi.derive_repo(healthy, label="glimmerrepo")
        assert hi.incomplete_reason(whole) is None
        assert hi.may_replace_stored_rows(whole) is True

        partial = tmp_path / "quorlrepo"
        _write_repo(partial, _TRIAD)
        _break_doc_blob(partial, "claudedocs/handoff-quorlbane-cache.md")
        d = hi.derive_repo(partial, label="quorlrepo")
        # The pre-fix state, asserted alongside the new one so the record shows
        # exactly what was and was not observable: the ref DID resolve.
        assert d.unmeasured is None
        assert d.docs == 2
        assert d.unreadable == ("claudedocs/handoff-quorlbane-cache.md",)
        assert hi.may_replace_stored_rows(d) is False
        # The reason names the KIND and the counts — "1 of 40" and "40 of 40" are
        # different operator situations and the label alone cannot say which.
        assert hi.incomplete_reason(d) == "docs-unreadable (1 of 3)"

        gone = hi.derive_repo(tmp_path / "fandrellyrepo", label="fandrellyrepo")
        assert hi.incomplete_reason(gone) == "no-such-directory"
        assert hi.may_replace_stored_rows(gone) is False

    def test_the_delete_scope_excludes_a_repo_whose_docs_would_not_READ(self, tmp_path):
        """The scope itself, PURE, as a differential over the ONE variable: two
        repos built from the SAME fixture, one with a blob removed."""
        good = tmp_path / "glimmerrepo"
        bad = tmp_path / "quorlrepo"
        _write_repo(good, _TRIAD)
        _write_repo(bad, _TRIAD)
        _break_every_doc_blob(bad)
        ds = [hi.derive_repo(good, label="glimmerrepo"),
              hi.derive_repo(bad, label="quorlrepo")]
        # 🔴 NOT ("glimmerrepo", "quorlrepo") — which is what this returned
        # before, with no warning and exit 0.
        assert hi.rebuild_delete_labels(ds, (), scoped=True) == ("glimmerrepo",)

    def test_ONE_unreadable_doc_of_THREE_also_withholds_authority(self, tmp_path):
        """🔴 THE WIDEST READING, AND THE MUTANT THIS EXISTS TO KILL. A guard
        written for the filed symptom — every doc unreadable — would read
        `d.unreadable and not d.docs` and pass every other test in this class.
        Here the repo contributed 2 of 3 docs, so a total-only guard puts it back
        in the scope and its third doc's stored rows are deleted with nothing to
        replace them."""
        good = tmp_path / "glimmerrepo"
        bad = tmp_path / "quorlrepo"
        _write_repo(good, _TRIAD)
        _write_repo(bad, _TRIAD)
        _break_doc_blob(bad, "claudedocs/handoff-fandrelly-probe.md")
        ds = [hi.derive_repo(good, label="glimmerrepo"),
              hi.derive_repo(bad, label="quorlrepo")]
        assert ds[1].docs == 2, "the fixture must be PARTIALLY readable, or this pins nothing"
        assert hi.rebuild_delete_labels(ds, (), scoped=True) == ("glimmerrepo",)

    def test_what_it_DID_read_is_still_indexed(self, tmp_path):
        """The boundary the fix must NOT have moved. Withholding the DELETE is
        not a reason to throw away the docs that read: an ON CONFLICT insert
        REFRESHES without destroying, so those rows are still contributed. A fix
        that reclassified the repo as UNMEASURED would drop them, and this is
        what says so."""
        bad = tmp_path / "quorlrepo"
        _write_repo(bad, _TRIAD)
        _break_doc_blob(bad, "claudedocs/handoff-quorlbane-cache.md")
        d = hi.derive_repo(bad, label="quorlrepo")
        assert d.sections, "the readable docs must still produce rows"
        assert {s.slug for s in d.sections} == {"glimmerwick-bus", "fandrelly-probe"}

    def test_the_bound_DELETE_through_main_names_only_what_was_read_in_FULL(
            self, tmp_path, capsys):
        """🔴 THE REGRESSION TEST, END TO END, AND THE ASSERTION IS ON THE BOUND
        SCOPE. The statement text, the exit code and the row count are IDENTICAL
        between the correct run and the destructive one — the bound parameters
        are the only place the difference shows, which is why `RecordingConn`
        keeps them.

        The table is pre-loaded with both labels so the delete has something real
        to be wrong about."""
        good = tmp_path / "glimmerrepo"
        bad = tmp_path / "quorlrepo"
        _write_repo(good, _TRIAD)
        _write_repo(bad, _TRIAD)
        _break_every_doc_blob(bad)

        conn = StoredReposConn(("glimmerrepo", "quorlrepo"))
        rc = hi.main(["--repo", str(good), "--repo", str(bad), "--rebuild", "--write"],
                     open_store=_recording_store(conn))
        cap = capsys.readouterr()
        assert rc == hi.RC_OK
        # 🔴 NOT [[['glimmerrepo', 'quorlrepo']]].
        assert conn.params_for("DELETE") == [[["glimmerrepo"]]]
        # …and the run SAYS it is partial, on both the warning path and the one
        # line a scripted caller is most likely to read alone.
        assert "🔴 PARTIAL INDEX" in cap.err
        assert "quorlrepo (docs-unreadable (3 of 3))" in cap.err
        assert "🔴 THIS INDEX IS PARTIAL" in cap.out
        # The pre-flight names it in its own bucket rather than omitting it.
        assert "KEPT (resolved, but a doc would not READ" in cap.out

    def test_a_run_over_only_HEALTHY_repos_deletes_both_and_says_nothing(
            self, tmp_path, capsys):
        """The negative control for the test above, over the same shape one blob
        apart. Without it, the assertions there are satisfied by a scope that
        withholds authority from everything — which would be a permanently-red
        rebuild, not a fix."""
        good = tmp_path / "glimmerrepo"
        also = tmp_path / "quorlrepo"
        _write_repo(good, _TRIAD)
        _write_repo(also, _TRIAD)

        conn = StoredReposConn(("glimmerrepo", "quorlrepo"))
        rc = hi.main(["--repo", str(good), "--repo", str(also), "--rebuild", "--write"],
                     open_store=_recording_store(conn))
        cap = capsys.readouterr()
        assert rc == hi.RC_OK
        assert conn.params_for("DELETE") == [[["glimmerrepo", "quorlrepo"]]]
        assert "PARTIAL" not in cap.out + cap.err

    def test_the_machine_surface_publishes_the_same_decision(self, tmp_path, capsys):
        """🔴 A CAVEAT SPELLED IN ONE RENDERER IS A CAVEAT THE OTHER DOES NOT
        HAVE. `--json` is the surface consumed without a human reading it, and a
        consumer deciding whether a repo's rows are current has to be able to
        read the SAME value the DELETE read — not re-derive it from `unmeasured`
        and `unreadable`, which is exactly how this predicate came to be spelled
        four different ways inside the module."""
        bad = tmp_path / "quorlrepo"
        _write_repo(bad, _TRIAD)
        _break_doc_blob(bad, "claudedocs/handoff-glimmerwick-bus.md")
        rc = hi.main(["--repo", str(bad), "--json"], open_store=_refusing_store())
        doc = json.loads(capsys.readouterr().out)
        assert rc == hi.RC_OK
        repo = doc["repos"][0]
        assert repo["unmeasured"] is None
        assert repo["rebuildable"] is False
        assert repo["incomplete_reason"] == "docs-unreadable (1 of 3)"


class TestThePartialPromiseCoversTheUnreadableRepoToo:
    """🔴 THE SEAM, RE-PINNED OVER THE STATE THAT BROKE IT. `TestThePartialPromiseIsKept`
    asserts the PARTIAL sentence and the delete scope cannot contradict each
    other — and it drove only the UNMEASURED state, because that was the only
    state either function could see. A repo whose docs would not read was in
    NEITHER: silently deleted, and unmentioned by the sentence that claims to
    name every repo this run does not speak for.

    `claude/RULES.md`: a seam guard must pin a RELATIONSHIP, not a component."""

    def _mixed(self, tmp_path):
        good = tmp_path / "glimmerrepo"
        partial = tmp_path / "quorlrepo"
        _write_repo(good, _TRIAD)
        _write_repo(partial, _TRIAD)
        _break_doc_blob(partial, "claudedocs/handoff-quorlbane-cache.md")
        return [hi.derive_repo(good, label="glimmerrepo"),
                hi.derive_repo(partial, label="quorlrepo")]

    def test_no_repo_the_warning_speaks_for_can_be_in_the_delete_scope(self, tmp_path):
        """The relationship itself, over every (scoped, prune) combination the
        run can reach a write in — the delete scope is a SUBSET of the labels
        this run read in FULL, so no repo the sentence names is in it."""
        ds = self._mixed(tmp_path)
        rows = [s for d in ds for s in d.sections]
        full = {d.label for d in ds if hi.may_replace_stored_rows(d)}
        spoken_for = {d.label for d in ds if not hi.may_replace_stored_rows(d)}

        partial = hi.partial_scope_warnings(ds)
        assert partial, "the fixture must actually be partial, or this pins nothing"
        assert "left untouched" in partial[0]
        assert "quorlrepo" in partial[0]

        for scoped in (True, False):
            for prune in (True, False):
                if hi.rebuild_refusal(ds, rows, prune=prune) is not None:
                    continue  # this run never reaches a write; the promise holds
                scope = set(hi.rebuild_delete_labels(ds, ("glimmerrepo", "quorlrepo"),
                                                     scoped=scoped, prune=prune))
                assert scope <= full, (scoped, prune, scope)
                assert not (scope & spoken_for), (scoped, prune, scope)

    def test_the_warning_no_longer_claims_the_repo_contributed_NOTHING(self, tmp_path):
        """🔴 A SENTENCE THE WIDENING WOULD HAVE MADE FALSE. The warning used to
        say the repos it names "came back UNMEASURED and contributed NOTHING" —
        true of an absent checkout, false of a repo that read 2 of its 3 docs and
        whose rows are in the very transaction that prints it."""
        ds = self._mixed(tmp_path)
        assert ds[1].sections, "the fixture must contribute rows, or this pins nothing"
        text = hi.partial_scope_warnings(ds)[0]
        assert "contributed NOTHING" not in text
        assert "were NOT read completely" in text


class TestNoRepoReadInFullIsRefusedNotCrashed:
    """🔴 THE STATE THE WIDER SCOPE MADE REACHABLE, AND THE ANSWER IT GETS —
    WHICH IS A DOWNGRADE, NOT A REFUSAL. `PostgresSectionStore.write` RAISES on an
    empty rebuild scope, deliberately, so a caller that forgets the scope cannot
    get a whole-table wipe as the default; and until the widening
    `rebuild_refusal` guaranteed the scope was non-empty, because "not every repo
    is unmeasured" implied "at least one repo is in the scope". That implication
    is gone.

    🔴 THE FIRST ANSWER WAS A FOURTH REFUSAL ARM AT rc 4, AND IT SHIPPED FOR ONE
    COMMIT. MEASURED on the TIMER's own argv — `--rebuild --write`, unscoped, one
    handle pointing at a real checkout with ONE corrupt blob and three pointing at
    absent ones:

        pre-widening : rc 0   DELETE + 18 INSERTs   index refreshed, PARTIAL
        that arm     : rc 4   SQL kinds: []         nothing written at all
        here         : rc 0   no DELETE, 18 INSERTs index refreshed, PARTIAL

    (`test_the_TIMERS_OWN_ARGV_in_the_state_home_nix_calls_SUPPORTED` below is
    that exact fixture, so the third row is asserted rather than quoted.)

    Three absent checkouts is not a broken machine — `nix/home.nix` states in as
    many words that it is SUPPORTED and that the price is a standing warning. So
    the refusal turned a supported state into `OnFailure=notify-failure@%n
    .service` every 6h until a human repaired an object store, with the index
    frozen meanwhile: `claude/RULES.md`'s permanently-red gate, which is the
    mistake this same function's all-unmeasured arm had to unwind once already.

    The fallback is not an invention either — it is the sentence that refusal
    printed as its own remedy ("Re-running WITHOUT --rebuild is a real remedy
    here"). A remedy only a human can type is not a remedy for a unit."""

    def _all_partial(self, tmp_path):
        a = tmp_path / "glimmerrepo"
        b = tmp_path / "quorlrepo"
        for root in (a, b):
            _write_repo(root, _TRIAD)
            _break_doc_blob(root, "claudedocs/handoff-fandrelly-probe.md")
        return a, b

    def test_a_rebuild_with_nothing_read_in_FULL_writes_and_deletes_NOTHING(
            self, tmp_path, capsys):
        """🔴 THE REGRESSION, END TO END. At the parent commit this argv exited 4
        with the store never opened; the recording connection is what proves the
        write now HAPPENS rather than merely that the exit code moved, and
        `"DELETE" not in kinds` is what proves the downgrade is a downgrade and
        not a rebuild that got its scope from somewhere else."""
        a, b = self._all_partial(tmp_path)
        conn = StoredReposConn(("glimmerrepo", "quorlrepo"))
        rc = hi.main(["--repo", str(a), "--repo", str(b), "--rebuild", "--write"],
                     open_store=_recording_store(conn))
        cap = capsys.readouterr()
        assert rc == hi.RC_OK
        assert conn.kinds().count("INSERT") > 0
        assert "DELETE" not in conn.kinds()
        assert "REBUILD DOWNGRADED TO AN UPSERT" in cap.out
        assert "the delete scope would be EMPTY" in cap.out
        # It must NOT reach for the all-unmeasured arm's language: both repos
        # resolved, so "fix the repo handles" would be a confident wrong step.
        assert "came back UNMEASURED" not in cap.out + cap.err
        assert "REFUSING" not in cap.err
        # …and the success line says the rebuild did not happen, because
        # `wrote N section row(s)` otherwise reads as a completed rebuild.
        assert "--rebuild DOWNGRADED to an upsert — nothing was deleted" in cap.out

    def test_the_TIMERS_OWN_ARGV_in_the_state_home_nix_calls_SUPPORTED(
            self, tmp_path, monkeypatch, capsys):
        """🔴 THE MEASURED SHAPE, REPRODUCED WITH THE UNIT'S ARGV RATHER THAN
        `--repo`. Scoped runs and unscoped runs take different branches of
        `rebuild_delete_labels`, and it is the UNSCOPED one the timer takes — so
        a regression test driven only by `--repo` would not have covered the run
        that regressed.

        One handle present (a real checkout, one corrupt blob), the rest absent:
        exactly `nix/home.nix`'s SUPPORTED state plus a repairable object
        store."""
        handles = {}
        present = tmp_path / "brindlemossrepo"
        _write_repo(present, _TRIAD)
        _break_doc_blob(present, "claudedocs/handoff-quorlbane-cache.md")
        handles[hi.REPO_ENV_HANDLES[0]] = str(present)
        for handle in hi.REPO_ENV_HANDLES[1:]:
            handles[handle] = str(tmp_path / f"absent-{handle.lower()}")
        _apply_handles(monkeypatch, handles)

        conn = StoredReposConn(("brindlemossrepo",))
        rc = hi.main(["--rebuild", "--write"], open_store=_recording_store(conn))
        cap = capsys.readouterr()
        assert rc == hi.RC_OK, "a SUPPORTED state must not fire OnFailure 4x/day"
        # 🔴 THE EXACT ROW COUNT, because "> 0" cannot tell "the two readable docs
        # were indexed" from "one was". MEASURED at 18 here and at the commit
        # BEFORE the widening; the refusing commit wrote 0.
        assert conn.kinds().count("INSERT") == 18
        assert "DELETE" not in conn.kinds()
        assert "REBUILD DOWNGRADED TO AN UPSERT" in cap.out
        # Both kinds of incompleteness are named, with their own reasons.
        assert "brindlemossrepo (docs-unreadable (1 of 3))" in cap.out
        assert f"absent-{hi.REPO_ENV_HANDLES[1].lower()} (no-such-directory)" in cap.out

    def test_ONE_repo_read_in_FULL_anywhere_keeps_the_DELETE(self, tmp_path, capsys):
        """🔴 THE NEGATIVE CONTROL, AND WITHOUT IT EVERY ASSERTION ABOVE IS
        SATISFIED BY A RUN THAT NEVER DELETES ANYTHING — which would be a
        permanently-DEAD rebuild rather than a permanently-red one. The same two
        repos, one blob apart: the second is left intact."""
        a, b = self._all_partial(tmp_path)
        healthy = tmp_path / "brindlemossrepo"
        _write_repo(healthy, _TRIAD)
        conn = StoredReposConn(("glimmerrepo", "quorlrepo", "brindlemossrepo"))
        rc = hi.main(["--repo", str(a), "--repo", str(b), "--repo", str(healthy),
                      "--rebuild", "--write"], open_store=_recording_store(conn))
        cap = capsys.readouterr()
        assert rc == hi.RC_OK
        assert conn.params_for("DELETE") == [[["brindlemossrepo"]]]
        assert "REBUILD DOWNGRADED" not in cap.out

    def test_the_downgrade_and_the_bound_DELETE_SCOPE_cannot_disagree(self, tmp_path):
        """🔴 THE SEAM. `main` computes the delete scope ONLY when the downgrade
        does not fire, so an empty scope can never reach `write()` — and that is a
        RELATIONSHIP between two functions, not a property of either. Pinned over
        every (scoped, prune) combination a run can reach a write in, and over
        BOTH fixtures, so a change that made one of them always-empty or
        always-none fails here.

        `write()`'s own `ValueError` is the backstop, and it is asserted as the
        positive control: the thing the seam prevents must actually be fatal, or
        preventing it proves nothing."""
        a, b = self._all_partial(tmp_path)
        healthy = tmp_path / "brindlemossrepo"
        _write_repo(healthy, _TRIAD)
        nothing = [hi.derive_repo(a, label="glimmerrepo"),
                   hi.derive_repo(b, label="quorlrepo")]
        something = [*nothing, hi.derive_repo(healthy, label="brindlemossrepo")]

        assert hi.rebuild_downgrade_reason(nothing) is not None
        assert hi.rebuild_downgrade_reason(something) is None
        for ds in (nothing, something):
            rows = [s for d in ds for s in d.sections]
            for scoped in (True, False):
                for prune in (True, False):
                    if hi.rebuild_refusal(ds, rows, prune=prune) is not None:
                        continue  # never reaches a write
                    scope = hi.rebuild_delete_labels(
                        ds, ("glimmerrepo", "quorlrepo", "brindlemossrepo"),
                        scoped=scoped, prune=prune)
                    downgraded = hi.rebuild_downgrade_reason(ds) is not None
                    assert downgraded == (scope == ()), (scoped, prune, scope)

        # The positive control for what the seam is FOR.
        with pytest.raises(ValueError, match="EMPTY delete scope"):
            hi.PostgresSectionStore(RecordingConn()).write(
                [s for d in something for s in d.sections],
                rebuild=True, rebuild_labels=())

    def test_the_all_UNMEASURED_arm_still_wins_where_it_applies(self, tmp_path, capsys):
        """The ladder's ordering, pinned. Both arms fire on "every repo is
        unusable"; only the narrower one can honestly say `came back UNMEASURED`
        and send the reader to the handles, so it must be reached first."""
        rc = hi.main(["--repo", str(tmp_path / "glimmerrepo"),
                      "--repo", str(tmp_path / "quorlrepo"), "--rebuild", "--write"],
                     open_store=_refusing_store())
        err = capsys.readouterr().err
        assert rc == hi.RC_REFUSED
        assert "ALL 2 repo(s) came back UNMEASURED" in err
        assert "was read COMPLETELY" not in err

    def test_a_healthy_rebuild_is_NOT_refused(self, tmp_path, capsys):
        """The negative control that keeps this from being a permanently-red
        gate — the mistake `rebuild_refusal`'s all-unmeasured arm had to unwind
        once already."""
        good = tmp_path / "glimmerrepo"
        _write_repo(good, _TRIAD)
        conn = StoredReposConn(("glimmerrepo",))
        rc = hi.main(["--repo", str(good), "--rebuild", "--write"],
                     open_store=_recording_store(conn))
        assert rc == hi.RC_OK
        assert "REFUSING" not in capsys.readouterr().err
        assert conn.params_for("DELETE") == [[["glimmerrepo"]]]


class TestPruneRefusesARepoItCannotReplace:
    """`--prune` widens the delete to every stored label the config does not
    name. That is only sound if the run can replace what it keeps, so the same
    predicate gates it — and the message has to stay distinguishable from the
    config-width guard, which opens with the same six words."""

    def test_prune_refuses_while_a_doc_would_not_read(self, tmp_path, monkeypatch,
                                                      capsys):
        """Every handle SET — so `prune_config_refusal` is not what fires — with
        one checkout carrying a broken blob."""
        handles = _every_handle_set(tmp_path)
        broken = Path(handles[hi.REPO_ENV_HANDLES[2]])
        _break_every_doc_blob(broken)
        _apply_handles(monkeypatch, handles)

        rc = hi.main(["--rebuild", "--prune", "--write"], open_store=_refusing_store())
        err = capsys.readouterr().err
        assert rc == hi.RC_REFUSED
        assert "came back UNMEASURED or INCOMPLETE" in err
        assert f"{broken.name} (docs-unreadable (1 of 1))" in err
        assert "handle(s) are UNSET" not in err

    def test_the_same_config_with_every_doc_readable_still_prunes(
            self, tmp_path, monkeypatch, capsys):
        """The differential over the ONE variable — one blob — so nothing else
        can explain the two outcomes."""
        _apply_handles(monkeypatch, _every_handle_set(tmp_path))
        labels = [f"{h.lower()}repo" for h in hi.REPO_ENV_HANDLES]
        conn = StoredReposConn((*labels, "brindlewick-retired"))
        rc = hi.main(["--rebuild", "--prune", "--write"],
                     open_store=_recording_store(conn))
        assert rc == hi.RC_OK
        assert "REFUSING" not in capsys.readouterr().err
        assert conn.params_for("DELETE") == [
            [sorted([*labels, "brindlewick-retired"])]]


class TestTheAuthorityPredicateIsREACHEDByTheDeleteScope:
    """🔴 PROVING THE GUARD REACHABLE, NOT MERELY BREAKABLE. `claude/RULES.md`: a
    mutation test still passes when an EARLIER check always wins, so the guard
    never executes. Here the earlier check is `d.unmeasured`, and every fixture
    above reaches the `unreadable` clause only because `derive_repo` happens to
    leave `unmeasured` as `None` for a resolvable repo.

    These drive `RepoDerivation` DIRECTLY — the functions are PURE and public, so
    this is a supported call, not a contrivance — and construct the one state
    that can only be answered by the second clause: `unmeasured is None` with
    `unreadable` non-empty. If a future change made the first clause subsume the
    second, these fail while everything above stays green."""

    def _d(self, label, *, unmeasured=None, unreadable=()):
        return hi.RepoDerivation(repo=f"/nowhere/{label}", label=label,
                                 ref="refs/heads/main", docs=len(unreadable) and 1,
                                 unmeasured=unmeasured, unreadable=unreadable)

    def test_the_second_clause_alone_decides_a_label(self):
        only_unreadable = self._d("thackrepo",
                                  unreadable=("claudedocs/handoff-thack-relay.md",))
        assert only_unreadable.unmeasured is None      # the earlier check does NOT fire
        assert hi.incomplete_reason(only_unreadable) is not None
        assert hi.may_replace_stored_rows(only_unreadable) is False

    def test_the_delete_scope_reads_it_rather_than_re_deriving_it(self):
        """The scope is the consumer that matters, and it is asserted over a
        derivation the git layer cannot produce — so the answer comes from the
        predicate and from nothing else in `derive_repo`."""
        ds = [self._d("venlorepo"),
              self._d("thackrepo", unreadable=("claudedocs/handoff-thack-relay.md",))]
        assert hi.rebuild_delete_labels(ds, (), scoped=True) == ("venlorepo",)
        # …and with the SAME two derivations minus the unreadable path, both.
        ds[1] = self._d("thackrepo")
        assert hi.rebuild_delete_labels(ds, (), scoped=True) == ("thackrepo", "venlorepo")


# --------------------------------------------------------------------------- #
# Round 2 of the incomplete-read authority work — the audit's findings
# --------------------------------------------------------------------------- #


def _plan_bucket(lines, prefix: str) -> list[str]:
    """The labels listed on the plan line starting with `prefix`.

    🔴 IT PARSES THE OPERATOR'S LINE, NOT A FUNCTION'S RETURN. The whole point of
    the seam tests below is that the pre-flight and the bound DELETE are two
    different renderings of one decision, so a helper that re-called
    `rebuild_delete_labels` would be asserting a value against itself."""
    hit = [ln for ln in lines if ln.strip().startswith(prefix)]
    assert len(hit) == 1, f"expected exactly one {prefix!r} line, got {hit}"
    body = hit[0].split(":", 1)[1].strip()
    if body == "(none)":
        return []
    # Each entry is `label` or `label (reason)` — the label is the first token.
    return [e.strip().split(" ")[0] for e in body.split(", ")]


class TestThePlanBucketsArePartitionOnTheKindNotOnTheRawFields:
    """🔴 THE ONE CONSOLIDATED SITE THAT STILL RE-DERIVED. `incomplete_reason`
    exists because the same predicate had been spelled four ways at four sites;
    `rebuild_plan_lines` was made to read it for ONE of its three buckets and
    left reading the raw fields for the other two — `d.unmeasured is not None`
    and `d.unmeasured is None and d.unreadable`.

    That is not a style nit. Those two spellings enumerate the kinds that existed
    when they were written, and `incomplete_reason`'s own docstring says the
    design exists so a THIRD kind can be added ("any incompleteness — of any
    kind, including kinds not yet invented — withholds it"). On the day one is,
    the repo falls out of all three buckets while `partial_scope_warnings` still
    names it: the plan and the warning then disagree about the same run, and the
    plan is the operator's only view of what the run destroys."""

    def _mixed(self, tmp_path):
        healthy = tmp_path / "brindlemossrepo"
        broken = tmp_path / "varkelthornrepo"
        _write_repo(healthy, _TRIAD)
        _write_repo(broken, _TRIAD)
        _break_doc_blob(broken, "claudedocs/handoff-quorlbane-cache.md")
        return [hi.derive_repo(healthy, label="brindlemossrepo"),
                hi.derive_repo(broken, label="varkelthornrepo"),
                hi.derive_repo(tmp_path / "sundrellipexrepo",
                               label="sundrellipexrepo")]

    def test_every_configured_repo_lands_in_exactly_one_bucket(self, tmp_path):
        """The partition itself, over all three kinds at once. Three labels, three
        buckets, one each — and the union is the whole config, which is the claim
        a reader makes when they count the plan's labels."""
        ds = self._mixed(tmp_path)
        lines = hi.rebuild_plan_lines(ds, scoped=True, prune=False, write=True)
        delete = _plan_bucket(lines, "DELETE (read in FULL")
        unmeasured = _plan_bucket(lines, "KEPT (configured but UNMEASURED)")
        unreadable = _plan_bucket(lines, "KEPT (resolved, but a doc would not READ")
        assert delete == ["brindlemossrepo"]
        assert unmeasured == ["sundrellipexrepo"]
        assert unreadable == ["varkelthornrepo"]
        assert sorted(delete + unmeasured + unreadable) == sorted(
            d.label for d in ds)

    def test_a_THIRD_kind_of_incompleteness_is_REPORTED_not_dropped(
            self, tmp_path, monkeypatch):
        """🔴 THE MUTANT-SHAPED FUTURE, DRIVEN DIRECTLY. A new
        `incomplete_kind` value is exactly the change the raw-field spellings
        cannot survive, and no fixture built out of `derive_repo` can produce one
        — so the kind is monkeypatched, which is a supported call on a PURE public
        function rather than a contrivance.

        🔴 ONLY THE **KIND** IS PATCHED, AND PATCHING BOTH IS WHY THIS GUARD WAS
        STRUCTURALLY BLIND. It used to patch `incomplete_reason` alongside it,
        justified as "patching only the kind would leave the repo in the DELETE
        bucket" — MEASURED FALSE: `incomplete_reason` calls `incomplete_kind`, so
        a patched kind already flows through and `may_replace_stored_rows` is
        `False` with the kind alone. The pair-patch therefore supplied the very
        output under test, and it concealed a real defect: `incomplete_reason`'s
        tail was an unguarded `return` of the `docs-unreadable` literal, so the
        REAL function answered a novel kind with `docs-unreadable (0 of 3)` for a
        repo with ZERO unreadable docs. The mechanism built to make an
        unenumerated kind LOUD explained it with a confident wrong reason, and
        `--json` published the two in adjacent fields.

        RED at the parent commit on the reason assertion; at the commit before
        that, the label appeared NOWHERE in the plan."""
        ds = self._mixed(tmp_path)
        real_kind = hi.incomplete_kind
        novel = "blob-checksum-mismatch"
        assert novel not in hi.INCOMPLETE_KINDS
        monkeypatch.setattr(hi, "incomplete_kind", lambda d: (
            novel if d.label == "brindlemossrepo" else real_kind(d)))

        # The fixture's own numbers, so the assertion below cannot be satisfied
        # by a coincidence: this repo is HEALTHY — nothing unreadable at all —
        # which is what makes `docs-unreadable (0 of 3)` a visibly false answer.
        target = next(d for d in ds if d.label == "brindlemossrepo")
        assert target.unreadable == () and target.docs == 3

        lines = hi.rebuild_plan_lines(ds, scoped=True, prune=False, write=True)
        assert "brindlemossrepo" not in _plan_bucket(lines, "DELETE (read in FULL")
        blob = "\n".join(lines)
        assert "brindlemossrepo" in blob, (
            "a kind the plan does not enumerate must still be REPORTED")
        unclassified = [ln for ln in lines if "UNCLASSIFIED" in ln]
        assert len(unclassified) == 1
        assert "brindlemossrepo" in unclassified[0]
        # …and the plan says its own buckets are not a complete view, rather than
        # letting a reader count three lines and believe them.
        assert "NOT a complete view" in unclassified[0]
        # 🔴 THE HALF THE PAIR-PATCH HID. The reason must not borrow another
        # kind's explanation — it must name the kind it could not classify.
        reason = hi.incomplete_reason(target)
        assert "docs-unreadable" not in reason, (
            f"an unenumerated kind rendered another kind's reason: {reason!r}")
        assert novel in reason and "UNENUMERATED" in reason
        # Authority is still WITHHELD — the conservative direction is what the
        # "kinds not yet invented" promise is for.
        assert hi.may_replace_stored_rows(target) is False

    def test_no_UNCLASSIFIED_line_when_every_kind_is_enumerated(self, tmp_path):
        """The negative control. Without it the assertion above is satisfied by a
        plan that prints the unclassified line unconditionally, which would make
        every healthy run look like it had hit an unknown failure mode."""
        ds = self._mixed(tmp_path)
        lines = hi.rebuild_plan_lines(ds, scoped=True, prune=False, write=True)
        assert not [ln for ln in lines if "UNCLASSIFIED" in ln]

    def test_the_reason_is_derived_FROM_the_kind_not_matched_out_of_it(self):
        """🔴 THE REASON FOR AN UNMEASURED REPO IS A FREE-FORM TOKEN, so recovering
        the kind by matching on the reason string would be a guard on WORDS. Pinned
        as a differential: two derivations whose reasons share no substring, both
        classified `unmeasured`."""
        a = hi.RepoDerivation(repo="/x/pellinorerepo", label="pellinorerepo",
                              unmeasured="no-such-directory")
        b = hi.RepoDerivation(repo="/x/thistlegrimrepo", label="thistlegrimrepo",
                              unmeasured="ls-tree-failed")
        assert hi.incomplete_kind(a) == hi.incomplete_kind(b) == hi.INCOMPLETE_UNMEASURED
        assert hi.incomplete_reason(a) == "no-such-directory"
        assert hi.incomplete_reason(b) == "ls-tree-failed"
        # …and the unreadable kind is the OTHER one, over a derivation whose
        # `unmeasured` is clear — proving the first clause does not always win.
        c = hi.RepoDerivation(repo="/x/mockwaitherepo", label="mockwaitherepo",
                              ref="refs/heads/main", docs=4,
                              unreadable=("claudedocs/handoff-mockwaithe-lane.md",))
        assert hi.incomplete_kind(c) == hi.INCOMPLETE_UNREADABLE
        assert hi.incomplete_reason(c) == "docs-unreadable (1 of 5)"


class TestTheReportsAndTheBOUNDDeleteCannotDisagree:
    """🔴 THREE MUTANTS THAT SURVIVED A FULL GREEN SUITE, AND THE SEAM THAT HELD
    NONE OF THEM. Each is a revert of one changed expression back to
    `d.unmeasured is None` — the spelling the widening replaced — and each
    produces an operator report that CONTRADICTS ITSELF in the same sentence,
    with 234 tests still passing:

      * `partial_scope_warnings`' `ok` list: "…were NOT read completely…:
        varkelthornrepo" beside "Read in FULL, and therefore rebuilt, covers only:
        brindlemossrepo, varkelthornrepo".
      * `rebuild_plan_lines`' `measured`: "DELETE …: brindlemossrepo,
        varkelthornrepo" while the bound DELETE is `[['brindlemossrepo']]`, and
        `varkelthornrepo` also under the mutually-exclusive KEPT bucket.
      * `rebuild_refusal`'s zero-rows denominator: "ZERO rows from the 0 repo(s)
        that resolved a mainline ref" when one did.

    The only assertion the suite had on any of them drove the UNMEASURED state,
    where both spellings agree — `claude/RULES.md`'s "verified in isolation" seam,
    one function down: every existing test was scoped to a state in which the
    difference cannot appear."""

    def _mixed(self, tmp_path):
        healthy = tmp_path / "brindlemossrepo"
        broken = tmp_path / "varkelthornrepo"
        _write_repo(healthy, _TRIAD)
        _write_repo(broken, _TRIAD)
        _break_doc_blob(broken, "claudedocs/handoff-quorlbane-cache.md")
        return [hi.derive_repo(healthy, label="brindlemossrepo"),
                hi.derive_repo(broken, label="varkelthornrepo")]

    def test_the_PARTIAL_warnings_ok_list_names_only_repos_read_in_FULL(
            self, tmp_path):
        """MUTANT 1. The `ok` list is the half of the sentence a reader uses to
        decide which repos the index now covers, and it read `d.unmeasured is
        None` — so it named the repo the same sentence had just said the run does
        not speak for."""
        ds = self._mixed(tmp_path)
        text = hi.partial_scope_warnings(ds)[0]
        assert "were NOT read completely" in text and "varkelthornrepo" in text
        covers = text.split("covers only: ")[1].split(".")[0]
        assert covers == "brindlemossrepo", (
            "the two halves of one sentence must not name the same repo")

    def test_the_plan_DELETE_line_names_only_repos_read_in_FULL(self, tmp_path):
        """MUTANT 2, at the pre-flight. A label in BOTH the DELETE bucket and a
        mutually-exclusive KEPT bucket is a self-contradicting plan."""
        ds = self._mixed(tmp_path)
        lines = hi.rebuild_plan_lines(ds, scoped=True, prune=False, write=True)
        delete = _plan_bucket(lines, "DELETE (read in FULL")
        unreadable = _plan_bucket(lines, "KEPT (resolved, but a doc would not READ")
        assert delete == ["brindlemossrepo"]
        assert unreadable == ["varkelthornrepo"]
        assert not set(delete) & set(unreadable)

    def test_the_planned_DELETE_IS_the_scope_that_gets_BOUND(self, tmp_path, capsys):
        """🔴 THE SEAM THE AUDIT ASKED FOR, AND IT BINDS TWO RENDERINGS RATHER
        THAN RESTATING ONE. `rebuild_plan_lines`' own trailing sentence calls its
        list "the COMPLETE delete scope"; nothing held it to the parameters
        `DELETE_SQL` is actually executed with, which is why mutant 2 survived
        while an end-to-end test asserting the bound scope was green.

        Driven through `main` so the two values are produced by the run rather
        than by the test, over a fixture where they CAN differ: one healthy repo,
        one partially readable, one absent."""
        healthy = tmp_path / "brindlemossrepo"
        broken = tmp_path / "varkelthornrepo"
        _write_repo(healthy, _TRIAD)
        _write_repo(broken, _TRIAD)
        _break_doc_blob(broken, "claudedocs/handoff-quorlbane-cache.md")
        absent = tmp_path / "sundrellipexrepo"

        conn = StoredReposConn(("brindlemossrepo", "varkelthornrepo",
                                "sundrellipexrepo"))
        rc = hi.main(["--repo", str(healthy), "--repo", str(broken),
                      "--repo", str(absent), "--rebuild", "--write"],
                     open_store=_recording_store(conn))
        out = capsys.readouterr().out
        assert rc == hi.RC_OK
        planned = _plan_bucket(out.splitlines(), "DELETE (read in FULL")
        bound = conn.params_for("DELETE")
        assert bound == [[["brindlemossrepo"]]], bound
        assert planned == sorted(bound[0][0]), (planned, bound)
        # …and the run's own success line quotes the same scope back.
        assert "after DELETE of 1 repo label(s): brindlemossrepo" in out

    def test_the_zero_rows_arm_counts_repos_that_RESOLVED_a_ref(self, tmp_path):
        """MUTANT 3. `measured` here is a count of repos that RESOLVED A MAINLINE
        REF — that is what the sentence says — so it is `len(derivations) -
        len(unmeasured)`, not `len(bad)`. Reverting it made the message read "ZERO
        rows from the 0 repo(s) that resolved a mainline ref" for a run in which
        one did.

        The fixture is chosen so the three candidate numbers are pairwise
        distinct: 2 derivations, 1 unmeasured, 2 incomplete — correct answer 1,
        mutant's answer 0, `len(derivations)` 2."""
        empty = tmp_path / "brindlemossrepo"
        _write_repo(empty, {"handoff-varkelthorn-lane.md": DOC_FULL})
        _break_every_doc_blob(empty)
        ds = [hi.derive_repo(empty, label="brindlemossrepo"),
              hi.derive_repo(tmp_path / "sundrellipexrepo",
                             label="sundrellipexrepo")]
        assert [d.label for d in ds if d.unmeasured] == ["sundrellipexrepo"]
        assert [d.label for d in ds if hi.incomplete_reason(d)] == [
            "brindlemossrepo", "sundrellipexrepo"]
        refusal = hi.rebuild_refusal(ds, [])
        assert refusal is not None
        assert "ZERO rows from the 1 repo(s) that resolved a mainline ref" in refusal


class TestThePruneRefusalStatesTheReasonThatActuallyApplies:
    """🔴 A REMEDY OR A RATIONALE IS A CLAIM ABOUT THE STATE IT IS PRINTED IN.
    When the `--prune` arm widened to INCOMPLETE repos it explained the new half
    with the OLD half's reason: "refused for the same reason it is kept out of the
    delete scope: this run has nothing to put back". That is false twice —
    `--prune` never puts back the disappeared labels' rows (deleting them is what
    it is for), and the incomplete repo's own rows are already outside `measured`,
    so they are safe with or without this arm. The renamed-checkout ambiguity the
    arm exists for cannot arise from an unreadable doc either: the ref resolved,
    the directory is present, the label is unchanged.

    The behaviour is kept — conservative on a destructive operator flag is cheap,
    and `--prune` is never in the unit's argv — so what this pins is the SENTENCE.

    ⚠ IT IS A WORD-LEVEL GUARD, DELIBERATELY AND WITH ITS LIMIT STATED: it pins
    that one RETRACTED sentence cannot come back and that the true one is present.
    It cannot tell you a third, differently-worded false rationale is false. What
    makes that acceptable here is that the retracted sentence is the specific
    thing an audit found and a reword would otherwise silently restore."""

    def _prune_refusal(self, tmp_path):
        healthy = tmp_path / "brindlemossrepo"
        broken = tmp_path / "varkelthornrepo"
        _write_repo(healthy, _TRIAD)
        _write_repo(broken, _TRIAD)
        _break_doc_blob(broken, "claudedocs/handoff-quorlbane-cache.md")
        ds = [hi.derive_repo(healthy, label="brindlemossrepo"),
              hi.derive_repo(broken, label="varkelthornrepo")]
        rows = [s for d in ds for s in d.sections]
        return hi.rebuild_refusal(ds, rows, prune=True), ds

    def test_it_does_not_claim_the_incomplete_repo_has_nothing_to_put_back(
            self, tmp_path):
        refusal, ds = self._prune_refusal(tmp_path)
        assert refusal is not None
        text = _norm(refusal)
        assert "for the same reason it is kept out of the delete scope" not in text
        assert "this run has nothing to put back" not in text

    def test_it_states_the_reason_that_DOES_apply(self, tmp_path):
        refusal, _ = self._prune_refusal(tmp_path)
        text = _norm(refusal)
        assert "cannot vouch for the corpus at all" in text
        assert "decides what to DESTROY from what this run did NOT find" in text
        # …and it says the two halves are refused for DIFFERENT reasons, so the
        # unmeasured half's spelling-ambiguity rationale is not read as covering
        # both.
        assert "refused for a DIFFERENT reason" in text

    def test_the_claim_the_message_makes_about_the_repos_rows_is_TRUE(
            self, tmp_path):
        """The behavioural half, so this class is not purely a prose pin: the
        message says the incomplete repo's own rows are already safe. They are —
        the same derivations with `--prune` dropped bind a DELETE that excludes
        it."""
        _, ds = self._prune_refusal(tmp_path)
        assert hi.rebuild_delete_labels(
            ds, ("brindlemossrepo", "varkelthornrepo"),
            scoped=False, prune=False) == ("brindlemossrepo",)

    def test_a_config_with_every_doc_readable_still_prunes(self, tmp_path,
                                                          monkeypatch, capsys):
        """The negative control, one blob apart — without it the assertions above
        are satisfied by an arm that refuses every `--prune` run."""
        _apply_handles(monkeypatch, _every_handle_set(tmp_path))
        labels = [f"{h.lower()}repo" for h in hi.REPO_ENV_HANDLES]
        conn = StoredReposConn(tuple(labels))
        rc = hi.main(["--rebuild", "--prune", "--write"],
                     open_store=_recording_store(conn))
        assert rc == hi.RC_OK
        assert "REFUSING" not in capsys.readouterr().err


class TestThePartialNoticeReachesEveryOutputPath:
    """🔴 A CLAIM THAT WAS FALSE IN A REACHABLE STATE, AND IT IS THE SENTENCE THAT
    STOPS THE NEXT PERSON LOOKING. `rebuild_delete_labels`' cost note says
    `partial_scope_warnings` "says so on every output path". It did not:
    `partial_scope_warnings` short-circuited to `()` when EVERY repo was
    incomplete and deferred to `rebuild_refusal`, which `main` consults only under
    `--rebuild`.

    MEASURED at the parent commit, two repos each with one unreadable doc,
    `--write` WITHOUT `--rebuild`: `partial_scope_warnings() == ()`, `wrote 24
    section row(s)`, rc 0, and the word PARTIAL nowhere on stdout — the only
    signal was two `⚠ UNREADABLE` lines. The hole pre-dated the widening; the
    CLAIM did not."""

    def _all_incomplete(self, tmp_path):
        a = tmp_path / "brindlemossrepo"
        b = tmp_path / "varkelthornrepo"
        for root in (a, b):
            _write_repo(root, _TRIAD)
            _break_doc_blob(root, "claudedocs/handoff-fandrelly-probe.md")
        return a, b

    def test_a_plain_WRITE_over_an_all_incomplete_corpus_says_so(
            self, tmp_path, capsys):
        """The reproduced hole, through `main`, on the argv that has no
        `--rebuild` — the one path `rebuild_refusal` never sees."""
        a, b = self._all_incomplete(tmp_path)
        conn = StoredReposConn(("brindlemossrepo", "varkelthornrepo"))
        rc = hi.main(["--repo", str(a), "--repo", str(b), "--write"],
                     open_store=_recording_store(conn))
        cap = capsys.readouterr()
        assert rc == hi.RC_OK
        assert conn.kinds().count("INSERT") > 0
        assert "🔴 THIS INDEX IS PARTIAL" in cap.out
        assert "🔴 NOTHING WAS READ COMPLETELY" in cap.err
        assert "brindlemossrepo (docs-unreadable (1 of 3))" in cap.err

    def test_the_pure_function_itself_no_longer_returns_empty(self, tmp_path):
        """Directly, because `main` has three renderers and the defect lives one
        function below all of them."""
        a, b = self._all_incomplete(tmp_path)
        ds = [hi.derive_repo(a, label="brindlemossrepo"),
              hi.derive_repo(b, label="varkelthornrepo")]
        assert all(hi.incomplete_reason(d) for d in ds)
        warnings = hi.partial_scope_warnings(ds)
        assert len(warnings) == 1
        # It must not claim an `ok` list it does not have — the mixed sentence's
        # "covers only: <labels>" would render as "covers only: " here.
        assert "covers only" not in warnings[0]
        assert "all 2 configured repo(s) were incomplete" in warnings[0]

    def test_the_JSON_surface_carries_it_too(self, tmp_path, capsys):
        """`--json` REPLACES the prose renderer, and it is the surface consumed
        without a human reading it."""
        a, b = self._all_incomplete(tmp_path)
        rc = hi.main(["--repo", str(a), "--repo", str(b), "--json"],
                     open_store=_refusing_store())
        doc = json.loads(capsys.readouterr().out)
        assert rc == hi.RC_OK
        assert any("NOTHING WAS READ COMPLETELY" in w for w in doc["warnings"])

    def test_an_ALL_HEALTHY_run_still_says_nothing(self, tmp_path, capsys):
        """🔴 THE ONE SUPPRESSION THAT SURVIVES, AND THE CONTROL FOR THE WHOLE
        CLASS. A per-run "0 repos missing" buries the real ones; removing the
        all-bad short-circuit must not have removed the empty one."""
        a = tmp_path / "brindlemossrepo"
        b = tmp_path / "varkelthornrepo"
        _write_repo(a, _TRIAD)
        _write_repo(b, _TRIAD)
        ds = [hi.derive_repo(a, label="brindlemossrepo"),
              hi.derive_repo(b, label="varkelthornrepo")]
        assert hi.partial_scope_warnings(ds) == ()
        conn = StoredReposConn(("brindlemossrepo", "varkelthornrepo"))
        rc = hi.main(["--repo", str(a), "--repo", str(b), "--write"],
                     open_store=_recording_store(conn))
        cap = capsys.readouterr()
        assert rc == hi.RC_OK
        assert "PARTIAL" not in cap.out + cap.err
        assert "NOTHING WAS READ COMPLETELY" not in cap.out + cap.err


class TestASharedLABELIsNotReportedAsPreserved:
    """🟢 THE DEFECT IS PRE-EXISTING AND STAYS; THE FALSE REPORT ABOUT IT DOES NOT.
    Authority is demonstrated per DERIVATION and exercised per LABEL, and two
    configured checkouts can derive one label (`main` uses `Path(raw).name`).

    MEASURED, identical at the parent commit and at the commit before the whole
    widening: `--repo <left>/protorepo --repo <right>/protorepo --rebuild --write`
    with the right twin's doc blob removed gives `DELETE params
    [[['protorepo']]]`, INSERTs only from the healthy twin, rc 0 — the broken
    twin's rows deleted with nothing to replace them. `identity_collisions` cannot
    see it: unreadable docs produce no sections, so nothing collides at the
    section level.

    🔴 WHAT THIS ROUND CHANGED IS THAT THE RUN STOPPED LYING ABOUT IT. The
    widening newly printed, in that exact state, "Their existing rows are left
    untouched … this run destroyed nothing it could not replace" and "kept their
    old rows" — about rows it had just deleted — and listed the label under both
    DELETE and KEPT with no comment. A silent wrong is recoverable by looking; a
    stated wrong is what stops anyone looking."""

    def _twins(self, tmp_path):
        left = tmp_path / "left" / "protorepo"
        right = tmp_path / "right" / "protorepo"
        _write_repo(left, _TRIAD)
        _write_repo(right, _TRIAD)
        _break_every_doc_blob(right)
        return left, right

    def test_the_collision_is_detected_per_LABEL(self, tmp_path):
        left, right = self._twins(tmp_path)
        ds = [hi.derive_repo(left, label="protorepo"),
              hi.derive_repo(right, label="protorepo")]
        assert hi.authority_label_collisions(ds) == ("protorepo",)
        # The negative control: the SAME two checkouts under distinct labels
        # collide with nothing, so this is about the label and not about the
        # broken blob.
        distinct = [hi.derive_repo(left, label="brindlemossrepo"),
                    hi.derive_repo(right, label="varkelthornrepo")]
        assert hi.authority_label_collisions(distinct) == ()

    def test_the_PARTIAL_warning_retracts_the_left_untouched_promise(self, tmp_path):
        """The false sentence, pinned absent, and the true one pinned present."""
        left, right = self._twins(tmp_path)
        ds = [hi.derive_repo(left, label="protorepo"),
              hi.derive_repo(right, label="protorepo")]
        text = _norm(hi.partial_scope_warnings(ds)[0])
        assert "destroyed nothing it could not replace" not in text
        assert "EXCEPT WHERE A LABEL IS SHARED: protorepo" in text
        assert "those rows ARE deleted" in text

    def test_the_promise_is_still_made_where_it_is_TRUE(self, tmp_path):
        """🔴 THE CONTROL THAT KEEPS THE QUALIFICATION FROM SWALLOWING THE CLAIM.
        With distinct labels the delete really does spare the incomplete repo, and
        the warning must still say so — otherwise the fix is "delete the promise",
        which loses the fact an operator needs."""
        left, right = self._twins(tmp_path)
        ds = [hi.derive_repo(left, label="brindlemossrepo"),
              hi.derive_repo(right, label="varkelthornrepo")]
        text = _norm(hi.partial_scope_warnings(ds)[0])
        assert "destroyed nothing it could not replace" in text
        assert "EXCEPT WHERE A LABEL IS SHARED" not in text

    def test_the_preflight_names_the_collision_rather_than_listing_it_twice(
            self, tmp_path):
        left, right = self._twins(tmp_path)
        ds = [hi.derive_repo(left, label="protorepo"),
              hi.derive_repo(right, label="protorepo")]
        lines = hi.rebuild_plan_lines(ds, scoped=True, prune=False, write=True)
        assert _plan_bucket(lines, "DELETE (read in FULL") == ["protorepo"]
        assert _plan_bucket(
            lines, "KEPT (resolved, but a doc would not READ") == ["protorepo"]
        shared = [ln for ln in lines if "SHARED LABEL" in ln]
        assert len(shared) == 1
        assert "protorepo" in shared[0]
        assert "the rows of the checkout(s) that were NOT read are deleted too" \
            in shared[0]

    def test_through_main_the_residual_is_recorded_and_the_report_is_true(
            self, tmp_path, capsys):
        """🔴 THE RESIDUAL IS ASSERTED, NOT WISHED AWAY. The DELETE really does
        still bind `protorepo`; this test says so, so the day the structural fix
        lands it fails and forces this class to be re-read rather than leaving a
        stale 'known issue' comment behind."""
        left, right = self._twins(tmp_path)
        conn = StoredReposConn(("protorepo",))
        rc = hi.main(["--repo", str(left), "--repo", str(right),
                      "--rebuild", "--write"], open_store=_recording_store(conn))
        cap = capsys.readouterr()
        assert rc == hi.RC_OK
        # THE UNFIXED DEFECT, pinned as-is.
        assert conn.params_for("DELETE") == [[["protorepo"]]]
        # …and every sentence the run prints about it is now true.
        assert "kept their old rows" not in cap.out
        assert "destroyed nothing it could not replace" not in cap.err
        assert "🔴 SHARED LABEL" in cap.out
        assert "EXCEPT WHERE A LABEL IS SHARED" in cap.err


class TestTheCommentsSitWithTheCodeTheyDescribe:
    """🔴 A COMMENT IS A CLAIM TOO, AND A MISPLACED ONE IS READ AS LICENCE. Two
    strays the audit found, both pinned structurally rather than by eye — a
    comment moves when someone edits around it, and nothing else in this suite
    would notice."""

    def test_the_disjunction_note_sits_above_the_arm_whose_opener_IS_one(self):
        """`"THE OPENER IS A DISJUNCTION ('UNMEASURED or INCOMPLETE')"` describes
        the `--prune` arm's message. It was left sitting immediately above the
        ALL-UNMEASURED arm, whose opener is not a disjunction at all — so a
        maintainer attaching it to the arm below it reads it as licence to widen
        that arm from `unmeasured` to `bad`, which is precisely the change that
        makes the timer refuse in a supported state."""
        src = inspect.getsource(hi.rebuild_refusal)
        note = src.index("THE OPENER IS A DISJUNCTION")
        prune_arm = src.index("if prune and bad:")
        unmeasured_arm = src.index("if derivations and len(unmeasured)")
        assert note < prune_arm < unmeasured_arm, (
            "the note must precede the arm it describes, not the next one")

    def test_the_prune_bullet_names_the_condition_the_code_actually_checks(self):
        """`rebuild_delete_labels`' `prune` bullet still said the refusal fires on
        a repo that "came back unmeasured" after the arm had widened to
        INCOMPLETE. The bullet directly above it was updated in the same diff;
        this one was not.

        Pinned as a behavioural cross-check as well as a word one: the state the
        stale bullet describes as NOT refusing is driven, and it refuses."""
        doc = _norm(hi.rebuild_delete_labels.__doc__)
        assert "refuses a `--prune` run in which ANY repo came back unmeasured" \
            not in doc
        assert "refuses a `--prune` run in which ANY repo came back INCOMPLETE" in doc
        # The behaviour the corrected bullet asserts.
        d = hi.RepoDerivation(repo="/x/mockwaitherepo", label="mockwaitherepo",
                              ref="refs/heads/main", docs=2,
                              unreadable=("claudedocs/handoff-mockwaithe-lane.md",))
        row = hi.Section("mockwaitherepo", "s", "p", None, None, "goal", 0, "h", "b")
        assert d.unmeasured is None
        assert hi.rebuild_refusal([d], [row], prune=True) is not None


class TestHomeNixDescribesTheGuardTheModuleACTUALLYHAS:
    """🔴 `nix/home.nix` IS WHERE AN OPERATOR LEARNS WHAT THE TIMER DOES, and it
    enumerated the refusal conditions in prose that the module then outgrew. It
    said the guard "refuses only when ALL repos are unmeasured, when the measured
    subset yields zero rows, or when `--prune` … meets an unmeasured repo" — the
    third condition had widened to INCOMPLETE, and a fourth state (nothing read in
    full) had been added as a REFUSAL, in the same diff.

    `claude/RULES.md`: when the artifact under test IS prose, a guard on WORDS is
    walkable by rewording — so the enumeration sentence is pinned as a WHOLE
    normalised string. A cosmetic reword fails this test. That is the price, and
    it is worth paying for a sentence an operator uses to decide whether a toast
    means their machine is broken."""

    ENUMERATION = _norm(
        "It now refuses only when ALL repos are unmeasured, when the measured "
        "subset yields zero rows, or when `--prune` (never passed here) meets an "
        "INCOMPLETE repo."
    )

    def _block(self) -> str:
        return _norm(_handoff_unit_block().replace("#", " "))

    def test_the_enumeration_sentence_is_exactly_this(self):
        assert self.ENUMERATION in self._block()

    def test_the_state_it_calls_SUPPORTED_is_documented_as_a_DOWNGRADE(self):
        """The fourth state, named where the operator reads about the third. A
        host with absent checkouts plus one corrupt blob does NOT get a refusal,
        and the comment has to say so or the next reader re-adds one."""
        block = self._block()
        assert "REBUILD DOWNGRADED TO AN UPSERT" in block
        assert "A FOURTH STATE EXISTS THAT IS *NOT* A REFUSAL" in block

    def test_the_home_nix_cost_paragraph_IS_the_sentence_the_run_prints(self):
        """🔴 THE COST IS ONE CLAIM ON THREE SURFACES, SO IT IS ONE STRING.

        This comment and the module's two printers each carried their own copy,
        and all three were WRONG in the same direction: "a section REMOVED from a
        doc keeps its stale row" reads as trailing-ordinal drift, while the DELETE
        is per-repo-LABEL and the upsert key is `(repo, slug, section, ordinal)`
        — so what persists under a downgrade is every row the run did not
        re-derive, whole DELETED documents included, and both slugs of a RENAMED
        one side by side.

        Pinned by IDENTITY rather than by keywords: `home.nix` must contain the
        constant VERBATIM (modulo comment markers and wrapping, which `_block`
        normalises away). Correcting one surface and not the others is then a
        failing test rather than an audit finding — which is how this defect was
        found, three copies deep."""
        assert _norm(hi.DOWNGRADE_COST) in self._block()

    def test_the_retracted_cost_wording_cannot_come_back(self):
        """The old sentence, banned in the same breath. Without this the pin
        above is satisfied by a comment that says BOTH — and a paragraph holding
        a claim and its correction leaves the reader to pick."""
        assert "a section REMOVED from a doc keeps its stale row" not in self._block()
        assert "a section REMOVED from a doc keeps its stale row" \
            not in _norm(hi.rebuild_downgrade_reason.__doc__)

    def test_the_retracted_wording_cannot_come_back(self):
        assert "meets an unmeasured repo" not in self._block()


# --------------------------------------------------------------------------- #
# Round 2 of the delta audit — the variant chosen off a RENDERED STRING, the
# unenumerated-kind fall-through, the unguarded plumbing, and the named guards
# that had rotted.
# --------------------------------------------------------------------------- #


#: 🔴 THE DOWNGRADE'S COST, WRITTEN OUT HERE RATHER THAN IMPORTED. `_ORPHAN_
#: SENTENCE`'s reason: a pin that reads the implementation's own constant asserts
#: it against itself and cannot see a reword. The module, `nix/home.nix` and this
#: literal are three independent statements of one claim, and the suite fails
#: unless all three agree.
_DOWNGRADE_COST = _norm(
    "EVERY row this run did not re-derive persists until some repo reads in "
    "FULL again — not just a section removed from a doc, but ALL rows of a doc "
    "DELETED from the repo, and for a RENAMED doc both the old and the new "
    "slug's rows, which a search returns side by side as two documents."
)


class TestTheAllBadArmIsChosenByCOUNTNotByARenderedString:
    """🔴 A VARIANT PICKED OFF A RENDERED STRING, SO A DELETE THAT HAPPENED WAS
    REPORTED AS NOT HAVING HAPPENED. `partial_scope_warnings` built
    `ok = ", ".join(d.label for d in derivations if may_replace_stored_rows(d))`
    and then branched on `if not ok:`. A label is `Path(r).name` (`main`), so
    `--repo .` derives `""` — and a run whose ONLY replaceable repo is that one
    joins to `""`, which is falsy, and took the ALL-BAD arm.

    MEASURED at the parent commit through `main` with a recording store,
    `--repo . --repo <broken> --rebuild --write` — ONE run, THREE contradicting
    statements about it:

        STORE CALLS: [('write', True, ('',))]   # rebuild=True: a DELETE WAS bound
        stderr : 🔴 NOTHING WAS READ COMPLETELY — all 2 configured repo(s) were
                 incomplete … the delete scope is EMPTY … NOTHING was deleted
        stdout :   DELETE (read in FULL, will be re-derived): (none)
        stdout : wrote 3 section row(s) … (after DELETE of 1 repo label(s):  …)
        rc 0

    The middle statement denies a DELETE the first and third record, and the
    sentence contradicts itself in isolation — "all 2 … were incomplete" and then
    naming one. The pre-flight's `', '.join(measured) or '(none)'` is the SAME
    root cause one function over, which is why both are fixed and both are
    pinned here: one bug printed in two places is how the second copy outlives
    the fix.

    ⚠ THE FIXTURES USE AN EMPTY LABEL DELIBERATELY. Every pre-existing fixture in
    this file uses non-empty labels, which is exactly why a full green suite,
    a mutation sweep and a round-1 audit all missed this: the difference between
    a COUNT and a rendered string cannot appear while every label renders."""

    def _empty_label_plus_broken(self, tmp_path):
        """One repo read in FULL whose label renders EMPTY, one partially read.

        🔴 THE FIXTURE ASSERTS ITSELF, because it could not build the state it
        names. `derive_repo` read `name = label or root.name`, so an EXPLICIT
        empty label was discarded and replaced by the directory name — the same
        falsy-string shape as the two defects this class is about, a third site.
        Without the assertion below every test here would run against a repo
        labelled `brindlemossrepo` and pass VACUOUSLY, which is precisely how the
        original defect survived a green suite."""
        good = tmp_path / "brindlemossrepo"
        broken = tmp_path / "varkelthornrepo"
        _write_repo(good, _TRIAD)
        _write_repo(broken, _TRIAD)
        _break_doc_blob(broken, "claudedocs/handoff-quorlbane-cache.md")
        ds = [hi.derive_repo(good, label=""),
              hi.derive_repo(broken, label="varkelthornrepo")]
        assert ds[0].label == "", (
            "derive_repo discarded an explicitly empty label — this fixture "
            "cannot exercise the defect it was written for")
        assert hi.may_replace_stored_rows(ds[0]) is True
        assert hi.may_replace_stored_rows(ds[1]) is False
        return ds

    def test_an_EMPTY_label_is_what_main_derives_from_repo_dot(self, tmp_path,
                                                               monkeypatch,
                                                               capsys):
        """🔴 REACHABILITY, PROVED THROUGH `main` RATHER THAN ASSERTED. The whole
        finding rests on a label that renders as nothing being a state a real
        argv produces, so that is measured here and not reasoned about:
        `Path(".").name == ""`, and `main` labels a `--repo` with exactly that."""
        assert Path(".").name == ""
        repo = tmp_path / "brindlemossrepo"
        _write_repo(repo, _TRIAD)
        monkeypatch.chdir(repo)
        # Driven through `main --json` so the LABEL under test is the one the CLI
        # derived, not one the test chose.
        rc = hi.main(["--repo", ".", "--json"])
        assert rc == hi.RC_OK
        payload = json.loads(capsys.readouterr().out)
        assert [r["label"] for r in payload["repos"]] == [""]

    def test_a_replaceable_repo_whose_label_renders_EMPTY_is_not_an_all_bad_run(
            self, tmp_path):
        """THE REGRESSION. One repo WAS read in full, so the run is PARTIAL —
        never "nothing was read completely"."""
        ds = self._empty_label_plus_broken(tmp_path)
        assert hi.nothing_was_read_completely(ds) is False
        assert hi.rebuild_downgrade_reason(ds) is None
        text = "\n".join(hi.partial_scope_warnings(ds))
        assert "NOTHING WAS READ COMPLETELY" not in text, (
            "a run with a replaceable repo took the all-bad arm because that "
            "repo's label renders as the empty string")
        assert "PARTIAL INDEX" in text
        # …and the delete scope really is non-empty, which is the fact the
        # retracted sentence denied.
        assert hi.rebuild_delete_labels(
            ds, (), scoped=True, prune=False) == ("",)

    def test_the_empty_derivation_list_is_not_an_all_bad_run(self):
        """🔴 THE `bool(derivations)` CONJUNCT, WHICH NOTHING GUARDED. A mutant
        dropping it — `return not any(may_replace_stored_rows(d) for d in
        derivations)` — SURVIVED a full 281-test run, because every fixture in
        this file passes at least one derivation, so no test ever reached the
        empty input.

        `any()` over an empty sequence is False, so `not any(…)` is the vacuous
        TRUE the conjunct exists to refuse. Without it
        `nothing_was_read_completely([])` flips False→True and drags two
        published surfaces with it, which is why all three are asserted here
        rather than the predicate alone: the predicate is the cause, and the two
        derived values are what a caller actually reads. All three are `__all__`
        exports and `handoff_search` imports this module — `main` is insulated
        only because it returns RC_USAGE on an empty config before it asks.

        The docstring that let this through is retracted in the module: it cited
        an `if not derivations: return None` in `rebuild_downgrade_reason` that
        the same commit had deleted, so a reader checking the empty-input case
        found a citation and stopped looking."""
        assert hi.nothing_was_read_completely([]) is False
        assert hi.rebuild_downgrade_reason([]) is None
        assert hi.derivation_json([])["rebuild_would_be_downgraded"] is False

    def test_the_plan_distinguishes_an_EMPTY_label_from_an_EMPTY_bucket(
            self, tmp_path):
        """The pre-flight half. `', '.join(measured) or '(none)'` printed
        `(none)` — "this run deletes nothing" — for a bucket holding one repo
        whose label renders empty. `(none)` and a blank are now different
        renderings, because they are different facts."""
        ds = self._empty_label_plus_broken(tmp_path)
        lines = hi.rebuild_plan_lines(ds, scoped=True, prune=False, write=True)
        delete_line = [ln for ln in lines
                       if ln.strip().startswith("DELETE (read in FULL")][0]
        assert "(none)" not in delete_line, (
            "the DELETE bucket is NOT empty — it holds one repo whose label "
            "renders as the empty string")
        assert _plan_bucket(lines, "DELETE (read in FULL") == [""]
        # The negative control: a genuinely empty bucket still says (none), or
        # the assertion above is satisfied by a plan that never says it.
        only_broken = [d for d in ds if d.label == "varkelthornrepo"]
        empty_lines = hi.rebuild_plan_lines(only_broken, scoped=True,
                                            prune=False, write=True)
        assert _plan_bucket(empty_lines, "DELETE (read in FULL") == []

    def test_the_two_owners_of_the_downgrade_decision_cannot_disagree(
            self, tmp_path):
        """🔴 THE SEAM, AND THE POINT OF THE CONSOLIDATION. `rebuild_downgrade_
        reason` and `partial_scope_warnings` are two renderings of ONE decision.
        They were two SPELLINGS of it, and the empty label is an input on which
        the spellings disagreed. Asserted as a relationship over a fixture matrix
        that includes the disagreeing input — a structural check over non-empty
        labels alone is exactly the coverage that already existed."""
        good = tmp_path / "brindlemossrepo"
        broken = tmp_path / "varkelthornrepo"
        other = tmp_path / "glimmerrepo"
        _write_repo(good, _TRIAD)
        _write_repo(broken, _TRIAD)
        _write_repo(other, _TRIAD)
        _break_doc_blob(broken, "claudedocs/handoff-quorlbane-cache.md")
        _break_doc_blob(other, "claudedocs/handoff-fandrelly-probe.md")

        empty = hi.derive_repo(good, label="")
        # 🔴 THE FIXTURE ASSERTS ITSELF, for the reason `_empty_label_plus_broken`
        # carries the same line: this is the ONLY case in the matrix below that
        # is not built from a directory name, and it is the "disagreeing input"
        # the docstring above says the matrix exists to include. Under a
        # `derive_repo` revert (`label or root.name`) `empty.label` becomes
        # `brindlemossrepo`, so `cases[0]` collapses into `cases[2]` and
        # `cases[1]` into `cases[6]`: a 7-case matrix silently becomes 5, the
        # disagreeing input is GONE, and the positive control below
        # (`verdicts == {True, False}`) still passes — so nothing signals it.
        # MEASURED: under that revert this test passes ALONE.
        assert empty.label == "", (
            "derive_repo discarded an explicitly empty label — the matrix below "
            "has collapsed to duplicate cases and no longer contains the input "
            "this seam test exists for")
        named = hi.derive_repo(good, label="brindlemossrepo")
        bad1 = hi.derive_repo(broken, label="varkelthornrepo")
        bad2 = hi.derive_repo(other, label="glimmerrepo")
        absent = hi.derive_repo(tmp_path / "sundrellipexrepo",
                                label="sundrellipexrepo")

        cases = [
            [empty, bad1],          # the disagreeing input
            [empty],                # replaceable, and the ONLY label is empty
            [named, bad1],
            [bad1, bad2],           # genuinely all-bad
            [bad1, absent],         # all-bad, both kinds
            [absent],
            [named],
        ]
        for ds in cases:
            downgraded = hi.rebuild_downgrade_reason(ds) is not None
            warned = any("NOTHING WAS READ COMPLETELY" in w
                         for w in hi.partial_scope_warnings(ds))
            assert downgraded == warned, [d.label for d in ds]
            # …and both are the ONE predicate, not a third spelling.
            assert downgraded == hi.nothing_was_read_completely(ds), \
                [d.label for d in ds]
        # The positive control: the matrix must actually contain both verdicts,
        # or `downgraded == warned` is satisfied by everything being False.
        verdicts = {hi.nothing_was_read_completely(ds) for ds in cases}
        assert verdicts == {True, False}

    def test_through_main_the_DELETE_and_every_sentence_about_it_agree(
            self, tmp_path, monkeypatch, capsys):
        """🔴 THE MEASURED SHAPE, END TO END, WITH THE RECORDING STORE. The three
        contradicting statements in this class's docstring came from ONE run, so
        the regression is pinned as one run: what got BOUND, what the pre-flight
        planned, what the warning said, and what the success line claimed."""
        good = tmp_path / "brindlemossrepo"
        broken = tmp_path / "varkelthornrepo"
        _write_repo(good, _TRIAD)
        _write_repo(broken, _TRIAD)
        _break_doc_blob(broken, "claudedocs/handoff-quorlbane-cache.md")
        monkeypatch.chdir(good)

        conn = StoredReposConn(("", "varkelthornrepo"))
        rc = hi.main(["--repo", ".", "--repo", str(broken), "--rebuild",
                      "--write"], open_store=_recording_store(conn))
        cap = capsys.readouterr()
        assert rc == hi.RC_OK
        bound = conn.params_for("DELETE")
        assert bound == [[[""]]], bound
        # 1. The warning must not deny the DELETE that was just bound.
        assert "NOTHING WAS READ COMPLETELY" not in cap.err + cap.out
        assert "PARTIAL INDEX" in cap.err
        # 2. The pre-flight must not report the bound scope as empty.
        planned = _plan_bucket(cap.out.splitlines(), "DELETE (read in FULL")
        assert planned == sorted(bound[0][0]) == [""], (planned, bound)
        # 3. The run took a real rebuild, so it must not claim a downgrade.
        assert "REBUILD DOWNGRADED" not in cap.out
        assert "DOWNGRADED to an upsert" not in cap.out
        assert "after DELETE of 1 repo label(s)" in cap.out


class TestThePlanPlumbingThisRoundAddedIsGuarded:
    """🔴 THREE MUTANTS THAT SURVIVED A FULL GREEN SUITE, TWO OF THEM ON CODE
    ADDED IN THE ROUND THAT SWEPT FOR THEM. From an independent 13-mutant sweep
    (fresh tree per mutant, `PYTHONDONTWRITEBYTECODE=1`, a no-op negative control
    that SURVIVED and a known-caught positive control that DIED):

      * M8 — `rebuild_plan_lines`' header ignores `downgraded`. Nothing in the
        suite referenced `downgraded=` or the SKIPPED header at all, so the
        parameter, the header string AND `main`'s call-site argument could each
        be deleted with the suite green — while the commit message calls the
        pre-flight "the operator's only view of what the run destroys". The
        nearest test asserts text from `rebuild_downgrade_reason`, not the plan.
      * M10 — the `kept` bucket reverted from the KIND to `d.unmeasured is not
        None`. Reverting the sibling `measured` bucket is caught by five tests;
        reverting `kept` was caught by none.
        ⚠ M10's guard is an INVARIANT GUARD, not regression coverage, and is
        labelled one here because `claude/RULES.md` requires the distinction to
        be written down rather than inferred: no shipped bug ever violated it —
        the two spellings agree on every state a real fixture can build, and the
        third kind that parts them is monkeypatched into existence. It is
        therefore correctly ABSENT from this PR's red-at-base matrix (it cannot
        go red at the base; there is nothing at the base for it to catch), and
        counting it as a regression test would overstate what this round
        measured.
      * M11 — the downgrade message drops its stated cost. The `nix/home.nix`
        half was pinned; the sentence the RUN prints was not."""

    def _mixed(self, tmp_path):
        healthy = tmp_path / "brindlemossrepo"
        broken = tmp_path / "varkelthornrepo"
        _write_repo(healthy, _TRIAD)
        _write_repo(broken, _TRIAD)
        _break_doc_blob(broken, "claudedocs/handoff-quorlbane-cache.md")
        return [hi.derive_repo(healthy, label="brindlemossrepo"),
                hi.derive_repo(broken, label="varkelthornrepo"),
                hi.derive_repo(tmp_path / "sundrellipexrepo",
                               label="sundrellipexrepo")]

    # ---- M8 ---------------------------------------------------------------- #

    def test_the_plan_HEADER_says_the_DELETE_is_SKIPPED_when_downgraded(
            self, tmp_path):
        """M8. A plan headed "rebuild delete scope" above a run that takes no
        DELETE is the defect the gate-ordering fix already closed once, one line
        further in: the block is printed, its heading names a DELETE, and the
        DELETE does not happen."""
        ds = self._mixed(tmp_path)
        header = hi.rebuild_plan_lines(ds, scoped=True, prune=False, write=True,
                                       downgraded=True)[0]
        assert header == (
            "## rebuild delete scope — SKIPPED (the --rebuild is DOWNGRADED to "
            "an upsert; NOTHING is deleted)")

    def test_the_plan_HEADER_is_plain_when_the_DELETE_really_runs(self, tmp_path):
        """M8's negative control. Without it the assertion above is satisfied by
        a header that says SKIPPED unconditionally — which would tell an operator
        that every rebuild deletes nothing."""
        ds = self._mixed(tmp_path)
        header = hi.rebuild_plan_lines(ds, scoped=True, prune=False, write=True,
                                       downgraded=False)[0]
        assert header == "## rebuild delete scope"
        # …and the default is the plain one, so a caller that forgets the
        # argument does not silently claim a downgrade.
        assert hi.rebuild_plan_lines(
            ds, scoped=True, prune=False, write=True)[0] == header

    def test_MAIN_passes_the_downgrade_through_to_the_plan_header(
            self, tmp_path, capsys):
        """🔴 M8's THIRD SITE. The parameter and the header string are dead
        unless `main` actually passes its decision in — and `main`'s argument was
        deletable with the suite green too. Driven end to end so the header an
        operator sees is the one asserted, over BOTH verdicts."""
        a = tmp_path / "glimmerrepo"
        b = tmp_path / "quorlrepo"
        for root in (a, b):
            _write_repo(root, _TRIAD)
            _break_doc_blob(root, "claudedocs/handoff-fandrelly-probe.md")
        conn = StoredReposConn(("glimmerrepo", "quorlrepo"))
        rc = hi.main(["--repo", str(a), "--repo", str(b), "--rebuild", "--write"],
                     open_store=_recording_store(conn))
        out = capsys.readouterr().out
        assert rc == hi.RC_OK and "DELETE" not in conn.kinds()
        assert "## rebuild delete scope — SKIPPED (the --rebuild is DOWNGRADED" \
            in out

        healthy = tmp_path / "brindlemossrepo"
        _write_repo(healthy, _TRIAD)
        conn2 = StoredReposConn(("brindlemossrepo",))
        rc = hi.main(["--repo", str(healthy), "--rebuild", "--write"],
                     open_store=_recording_store(conn2))
        out2 = capsys.readouterr().out
        assert rc == hi.RC_OK and conn2.params_for("DELETE") == [[["brindlemossrepo"]]]
        assert "## rebuild delete scope" in out2
        assert "SKIPPED" not in out2

    # ---- M10 --------------------------------------------------------------- #

    def test_the_UNMEASURED_bucket_is_keyed_on_the_KIND_not_the_raw_field(
            self, tmp_path, monkeypatch):
        """M10. `kept` reverted to `d.unmeasured is not None` survives every
        existing test, because that spelling and `incomplete_kind(d) ==
        INCOMPLETE_UNMEASURED` agree on every state a plain fixture can build.
        They part company the moment a THIRD kind claims a repo whose
        `unmeasured` field is set — which is precisely the future the bucketing
        was consolidated for, and the raw-field spelling then files that repo
        under UNMEASURED *and* under UNCLASSIFIED: two mutually exclusive
        buckets, one repo.

        Only the KIND is patched (see `test_a_THIRD_kind_of_incompleteness_is_
        REPORTED_not_dropped`), and the target is the ABSENT repo, whose
        `unmeasured` is a real non-`None` token — so the mutant's predicate is
        TRUE for it and the correct one is FALSE."""
        ds = self._mixed(tmp_path)
        target = next(d for d in ds if d.label == "sundrellipexrepo")
        assert target.unmeasured is not None, "the mutant's predicate must be TRUE here"
        real_kind = hi.incomplete_kind
        novel = "blob-checksum-mismatch"
        assert novel not in hi.INCOMPLETE_KINDS
        monkeypatch.setattr(hi, "incomplete_kind", lambda d: (
            novel if d.label == "sundrellipexrepo" else real_kind(d)))

        lines = hi.rebuild_plan_lines(ds, scoped=True, prune=False, write=True)
        unmeasured = _plan_bucket(lines, "KEPT (configured but UNMEASURED)")
        assert unmeasured == [], (
            "a repo whose KIND is not `unmeasured` must not appear in the "
            "UNMEASURED bucket, whatever its raw field says")
        # …and it is reported exactly once, under the bucket it belongs to.
        assert sum("sundrellipexrepo" in ln for ln in lines) == 1
        unclassified = [ln for ln in lines if "UNCLASSIFIED" in ln]
        assert len(unclassified) == 1 and "sundrellipexrepo" in unclassified[0]

    def test_the_UNMEASURED_bucket_still_holds_a_genuinely_unmeasured_repo(
            self, tmp_path):
        """M10's negative control — otherwise the assertion above is satisfied by
        a bucket that is always empty, i.e. a plan that never reports an absent
        checkout at all."""
        ds = self._mixed(tmp_path)
        lines = hi.rebuild_plan_lines(ds, scoped=True, prune=False, write=True)
        assert _plan_bucket(lines, "KEPT (configured but UNMEASURED)") == [
            "sundrellipexrepo"]

    # ---- M11 --------------------------------------------------------------- #

    def test_the_downgrade_states_its_FULL_cost_not_just_a_section(self, tmp_path):
        """M11. The cost sentence is what an operator uses to decide whether a
        downgraded run is survivable, and it was unpinned at the one surface that
        prints it. Pinned as a WHOLE NORMALISED STRING, `_ORPHAN_SENTENCE`'s
        reason: a guard on WORDS is walkable by rewording.

        It is also the sentence that was WRONG — "a section REMOVED from a doc"
        describes trailing-ordinal drift, and the real residue is whole
        documents. See `TestHomeNixDescribesTheGuardTheModuleACTUALLYHAS`, which
        pins the same string in `nix/home.nix`."""
        a = tmp_path / "glimmerrepo"
        b = tmp_path / "quorlrepo"
        for root in (a, b):
            _write_repo(root, _TRIAD)
            _break_doc_blob(root, "claudedocs/handoff-fandrelly-probe.md")
        ds = [hi.derive_repo(a, label="glimmerrepo"),
              hi.derive_repo(b, label="quorlrepo")]
        assert _DOWNGRADE_COST in _norm(hi.rebuild_downgrade_reason(ds))
        # The SAME sentence on the other surface that prints it — one claim, and
        # the all-bad warning used to carry its own drifting copy.
        assert _DOWNGRADE_COST in _norm("\n".join(hi.partial_scope_warnings(ds)))
        # And the module's constant IS that sentence, so `home.nix`'s pin and
        # this one cannot be satisfied by two different strings.
        assert _norm(hi.DOWNGRADE_COST) == _DOWNGRADE_COST


class TestTheRunLevelDowngradeIsPublishedNotWordMatched:
    """🔴 A DOWNGRADED RUN AND A CLEAN REBUILD ARE BOTH rc 0, SO A MACHINE
    CONSUMER HAD ONLY THE PROSE. `derivation_json` published per-repo
    `rebuildable` and `incomplete_kind` but no RUN-level field, so a `--json`
    caller had to word-match `"NOTHING WAS READ COMPLETELY"` out of `warnings` —
    the guard-on-WORDS this module refuses everywhere else, and the exact reason
    `incomplete_kind` was split out of `incomplete_reason`. `all_clear` is
    reliably `False` under a downgrade but is `False` under a dozen other things
    too, so it cannot be branched on."""

    def _all_bad(self, tmp_path):
        a = tmp_path / "glimmerrepo"
        b = tmp_path / "quorlrepo"
        for root in (a, b):
            _write_repo(root, _TRIAD)
            _break_doc_blob(root, "claudedocs/handoff-fandrelly-probe.md")
        return [hi.derive_repo(a, label="glimmerrepo"),
                hi.derive_repo(b, label="quorlrepo")]

    def test_the_json_carries_the_downgrade_as_a_BOOLEAN(self, tmp_path):
        ds = self._all_bad(tmp_path)
        payload = hi.derivation_json(ds)
        assert payload["rebuild_would_be_downgraded"] is True
        # It is the SAME decision `main` branches on, not a second spelling.
        assert payload["rebuild_would_be_downgraded"] == (
            hi.rebuild_downgrade_reason(ds) is not None)

    def test_a_healthy_run_publishes_FALSE(self, tmp_path):
        """The negative control. A field that is always True is not a signal —
        and `all_clear` is False for this run too (it carries warnings), which is
        exactly why `all_clear` could not serve as the downgrade flag."""
        healthy = tmp_path / "brindlemossrepo"
        _write_repo(healthy, _TRIAD)
        ds = [*self._all_bad(tmp_path),
              hi.derive_repo(healthy, label="brindlemossrepo")]
        payload = hi.derivation_json(ds)
        assert payload["rebuild_would_be_downgraded"] is False
        assert payload["all_clear"] is False, (
            "the discriminating claim: `all_clear` cannot tell a downgraded run "
            "from a merely partial one, which is why this field exists")

    def test_the_field_survives_the_CLI_json_surface(self, tmp_path, capsys):
        """Through `main --json`, because the field is for the surface an agent
        reads and a key present only in the pure function is not published."""
        a = tmp_path / "glimmerrepo"
        _write_repo(a, _TRIAD)
        _break_doc_blob(a, "claudedocs/handoff-fandrelly-probe.md")
        rc = hi.main(["--repo", str(a), "--json"])
        assert rc == hi.RC_OK
        payload = json.loads(capsys.readouterr().out)
        assert payload["rebuild_would_be_downgraded"] is True


class TestEveryGuardThisModuleNamesByNameActuallyExists:
    """🔴 A COMMENT THAT NAMES A GUARD IS A CLAIM OF COVERAGE, AND TWO OF THEM
    NAMED NOTHING. `INCOMPLETE_KINDS` cited `incomplete_kinds_are_partitioned`
    and `rebuild_downgrade_reason` cited
    `TestARebuildWithNothingReadInFullIsDowngradedNotRefused`; neither has ever
    existed. Both guards DO exist under other names, which is the bad case: a
    reader greps the cited name, finds nothing, and concludes the invariant is
    unguarded — so the comment reads as coverage while providing none, which
    `claude/RULES.md` calls worse than no comment because it stops anyone
    looking.

    Prose cannot be trusted to stay true, so this is machine-checked."""

    #: `Test[A-Z]…` avoids matching the ordinary word "Tests"; `test_…` catches
    #: the function form. Both are the shapes this repo names guards in.
    #:
    #: 🔴 THE `test_` ARM'S CHARACTER CLASS IS `[A-Za-z0-9_]`, NOT `[a-z0-9_]`,
    #: AND THAT ONE CHARACTER WAS THE WHOLE INSTRUMENT. A lowercase-only class
    #: cannot cross an uppercase letter, and the trailing `\b` cannot fire
    #: mid-identifier, so a cited name like
    #: `test_the_gate_fires_in_DRY_RUN_too` matched NOTHING AT ALL — not a
    #: truncated prefix this file could notice, no match. Three real citations
    #: in `handoff_index.py` were invisible that way, TWO of them written by the
    #: round that added this checker's blind-spot note; none dangled, so the
    #: whole class stayed green and would have stayed green through a rename.
    #: This repo names guards in SCREAMING_CASE fragments routinely — the
    #: uppercase half is not an edge case here, it is the house style.
    NAME_RE = re.compile(r"\b(?:Test[A-Z][A-Za-z0-9_]*|test_[A-Za-z0-9_]+)\b")

    def _dangling(self, source: str) -> list[str]:
        """Names `source` cites that no test file defines.

        🔴 IT DE-WRAPS FIRST. A long guard name does not fit an 79-column
        comment, and the module wraps one mid-identifier across a `#` line
        break; scanning the raw text would report that half as dangling. The
        rule is narrow and mechanical — a line ending in `_` continues into the
        next line's first token — and a wrap done any OTHER way fails this test,
        which is the correct outcome: fix the wrap or fix the name."""
        dewrapped = re.sub(r"_\n[ \t]*#?[ \t]*", "_", source)
        defined = _defined_test_names()
        return sorted({n for n in self.NAME_RE.findall(dewrapped)
                       if n not in defined})

    def test_no_test_name_cited_in_handoff_index_is_dangling(self):
        source = (REPO_ROOT / "scripts" / "lib" / "handoff_index.py").read_text()
        dangling = self._dangling(source)
        assert dangling == [], (
            f"scripts/lib/handoff_index.py cites {len(dangling)} guard name(s) "
            f"that nothing under scripts/tests/ defines: {dangling}\n"
            f"This is a repo-WIDE coupling, so ANY rename or deletion of a "
            f"guard turns it red from a diff that never touched this file — "
            f"which is the point: the citation is a claim of coverage, and a "
            f"reader who greps it and finds nothing concludes the invariant is "
            f"unguarded. Fix by updating the citation in handoff_index.py (or "
            f"restoring the name). Do NOT delete the sentence around it — the "
            f"sentence is the claim; deleting it hides the gap instead of "
            f"closing it. If the guard was renamed, cite the new name.")

    def test_the_checker_actually_finds_the_names_it_is_scanning(self):
        """🔴 THE POSITIVE CONTROL. A checker wired to nothing returns the same
        clean `[]` as a checker over a clean file, so the zero above is worthless
        until the scan is shown to SEE something.

        The retracted `Test…` name is asserted absent too, so the clean run
        cannot be reached by it quietly coming back. The other dead citation was
        bare snake_case, which `NAME_RE` does not recognise at all — that is the
        checker's stated blind spot (see `INCOMPLETE_KINDS`), and asserting on it
        here would claim a coverage this instrument does not have."""
        source = (REPO_ROOT / "scripts" / "lib" / "handoff_index.py").read_text()
        dewrapped = re.sub(r"_\n[ \t]*#?[ \t]*", "_", source)
        found = set(self.NAME_RE.findall(dewrapped))
        assert len(found) >= 5, sorted(found)
        assert "TestThePlanDescribesTheRunThatActuallyHappens" in found
        assert "test_every_configured_repo_lands_in_exactly_one_bucket" in found
        # 🔴 THE WRAPPED CITATION, which is the case the de-wrap exists for: it
        # is scanned as one name, so the de-wrap is exercised by the real file
        # and not only by the synthetic case below.
        assert "test_next_steps_split_per_ranked_item_carrying_the_whole_block" \
            in found
        assert "TestARebuildWithNothingReadInFullIsDowngradedNotRefused" not in found

    def test_a_cited_test_name_carrying_a_CAPITAL_is_scanned(self):
        """🔴 THE REGRESSION. `NAME_RE`'s `test_` arm was `test_[a-z0-9_]+`,
        which cannot cross an uppercase letter, and `\\b` cannot fire
        mid-identifier — so a cited name containing a CAPITAL matched nothing at
        all. Three such citations were live in `handoff_index.py` and this
        checker could not see one of them.

        None of them dangled, which is exactly why it was invisible: the class
        was GREEN, and would have stayed green after any rename of the three.
        The checker read as coverage over every citation in the module while
        covering 10 of 13.

        Asserted on the REAL citations rather than a synthetic string, so the
        test fails if they are renamed to a shape the checker cannot see
        again."""
        source = (REPO_ROOT / "scripts" / "lib" / "handoff_index.py").read_text()
        dewrapped = re.sub(r"_\n[ \t]*#?[ \t]*", "_", source)
        found = set(self.NAME_RE.findall(dewrapped))
        for name in (
                "test_the_downgrade_and_the_bound_DELETE_SCOPE_cannot_disagree",
                "test_the_downgrade_states_its_FULL_cost_not_just_a_section",
                "test_the_gate_fires_in_DRY_RUN_too"):
            assert name in found, (
                f"{name} is cited in handoff_index.py but NAME_RE did not "
                f"match it — the capital-carrying blind spot is back")
        # 🔴 THE DISCRIMINATING HALF. The three above could pass on a regex that
        # matched only their lowercase PREFIX, which would still leave the
        # checker unable to report a capital-carrying name as dangling. So feed
        # it one that does not exist and require the WHOLE name back.
        bogus = "test_a_GUARD_that_never_EXISTED"
        assert self._dangling(f"# pinned by `{bogus}` above") == [bogus]

    def test_the_checker_can_go_RED(self):
        """🔴 THE NEGATIVE CONTROL. Fed a citation of a guard that does not
        exist, it must report it — otherwise the clean run above is a fact about
        the instrument, not about the module. Built from the REAL retracted name,
        not a nonsense token, so it is the shape that actually shipped."""
        bogus = "TestARebuildWithNothingReadInFullIsDowngradedNotRefused"
        assert self._dangling(f"# see `{bogus}` for the pin") == [bogus]
        # …and a wrapped citation of a name that DOES exist is not reported,
        # which is what the de-wrap is for.
        assert self._dangling(
            "# pinned by test_every_configured_repo_lands_in_exactly_\n"
            "    # one_bucket, which asserts the partition") == []


def _defined_test_names() -> set[str]:
    """Every test module, class and function name `scripts/tests/` defines.

    Module STEMS count too: the module docstring cites `test_handoff_index`,
    which is a file rather than a callable."""
    names: set[str] = set()
    for path in sorted((REPO_ROOT / "scripts" / "tests").rglob("*.py")):
        names.add(path.stem)
        for match in re.finditer(r"^\s*(?:def|class)\s+([A-Za-z_][A-Za-z0-9_]*)",
                                 path.read_text(), re.M):
            names.add(match.group(1))
    return names
