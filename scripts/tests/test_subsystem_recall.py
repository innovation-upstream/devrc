"""Tests for scripts/lib/subsystem_recall.py — the READ half of the subsystem index.

WHAT IS BEING PROTECTED
-----------------------
The store had two writers (`/analyze-service`, and `/handoff` via
`subsystem_touch.py`) and no general reader, so the "terse pointer sheet that
outlives this handoff doc" outlived it with nobody opening it. This module is the
reader, and `/resume` step 4 is its caller.

🔴 THE FAILURE MODE IS A CONFIDENT ZERO, and an empty recall block has SIX
distinct causes: the scope has no directory; the scope exists and is empty; a
`--ref` matched nothing; a `--ref` matched two things; the store root is absent;
an entry is unreadable. Four are statuses, two raise. `TestStatusIsTheDiscriminator`
proves every declared status is EMITTED rather than merely declared, and
`TestNegativeControls` proves each failure carries its OWN sentinel — a control
that passes because a NEIGHBOURING guard fired is green for the wrong reason and
stays green with the guard it claims to test deleted.

🔴 THE POSITIVE CONTROL IS A PAIR. A reader wired to nothing surfaces nothing,
and so does a genuinely empty scope. `TestPositiveControl` reports both halves
together — non-empty recall from a populated scope, and nothing from a control
that differs in one respect only.

🔴 THE STORE IS NEVER WRITTEN, TESTED BEHAVIOURALLY. `TestRecallNeverWrites`
hashes a synthetic store tree either side of every mode AND every failure path. A
grep for `open(..., "w")` would be the "spelled rather than structural" guard
`claude/RULES.md` warns about.

🔴 NO TEST HERE READS THE REAL STORE. `~/.claude/analyze-service-index/` is
curated, client-confidential, has no off-machine backup and is rewritten by an
hourly autocommit while other sessions write to it. Every fixture is synthetic,
under `tmp_path`, with names invented for this file. This repo is PUBLIC.

🔴 `TestAppendConcurrency` IS A MEASUREMENT, NOT A BELIEF. The handoff protocol's
rationale for mandating `Edit` over `Write` was asserted from reading tool
semantics; these tests exercise the interleaving directly and pin what was
actually observed — including the half of the original claim that is FALSE.
"""

from __future__ import annotations

import collections
import hashlib
import importlib.util
import json
from testlib import hermetic_git  # noqa: E402
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "scripts" / "lib" / "subsystem_recall.py"
TOUCH_PATH = ROOT / "scripts" / "lib" / "subsystem_touch.py"
RESUME_DOC = ROOT / "claude" / "skills" / "resume" / "SKILL.md"
HANDOFF_DOC = ROOT / "claude" / "skills" / "handoff" / "SKILL.md"
# The body that must ROUTE to the sidecar is the one that carries the protocol,
# which since 2026-08-24 is the `subsystem-index` skill rather than /handoff.
INDEX_DOC = ROOT / "claude" / "skills" / "subsystem-index" / "SKILL.md"
# The on-demand evidence sidecar. Step 4's IMPERATIVES stay in HANDOFF_DOC; the
# measured rationale behind them lives here and costs nothing until it is read.
# 🔴 MOVED 2026-08-24 with the protocol it explains. `/handoff` step 4 became the
# `subsystem-index` skill, and this reference doc is that skill's evidence file —
# it was never about writing handoffs. The name is unchanged so its `§N` pointers
# still resolve.
HANDOFF_REFERENCE = (
    ROOT / "claude" / "skills" / "subsystem-index" / "reference" / "index-write.md"
)
ANALYZE_DOC = ROOT / "claude" / "skills" / "analyze-service" / "SKILL.md"

sys.path.insert(0, str(ROOT / "scripts" / "lib"))
sys.path.insert(0, str(ROOT / "scripts"))

from testlib.skills_mapping import (  # noqa: E402
    assert_skills_mapping_declared,
)

import subsystem_recall as rc  # noqa: E402
import subsystem_resolver as sr  # noqa: E402
import subsystem_touch as st  # noqa: E402


# =============================================================================
# Synthetic store fixtures — realistic SHAPES, invented names.
# =============================================================================
#
# Field distinctness is deliberate (`claude/RULES.md`: "pick fixtures whose
# fields are pairwise distinct"). The scope name is no slug and no alias; the
# pointer text, the nuance text and the `## What it is` text share no substring,
# so a renderer that surfaced the wrong section cannot pass by coincidence.

SCOPE = "workbench-cfg"
OTHER_SCOPE = "hardware-notes"

WHAT_IT_IS = "A durable thing a recall block MUST say the identity of."
POINTER_LINE = "- ops skill `manage-widget` — invoke it for restarts"
NUANCE_LINE = "- 2026-01-02: the readiness probe lies for 40s after a reload."


def _entry(
    service: str,
    scope: str,
    *,
    aliases=(),
    kind=None,
    sensitivity=None,
    what: str | None = WHAT_IT_IS,
    pointers: str | None = POINTER_LINE,
    nuance: str | None = NUANCE_LINE,
    extra_body: str = "",
) -> str:
    """`what=None` omits the `## What it is` HEADING entirely; `what=""` writes
    the heading with an empty body. They are different on-disk states (absent vs
    present-but-empty) and the reader has to degrade cleanly on both."""
    lines = ["---", f"service: {service}", f"scope: {scope}"]
    if aliases:
        lines.append("aliases: [" + ", ".join(aliases) + "]")
    if kind:
        lines.append(f"kind: {kind}")
    if sensitivity is not None:
        lines.append(f"sensitivity: {sensitivity}")
    lines += ["---", ""]
    if what is not None:
        lines += ["## What it is", what, ""]
    if pointers is not None:
        lines += ["## Pointers", pointers, ""]
    if nuance is not None:
        lines += ["## Nuance / work-history", nuance, ""]
    if extra_body:
        lines += [extra_body, ""]
    return "\n".join(lines)


def _make_store(root: Path) -> Path:
    """Two scopes, one deliberate ambiguity pair, one alias, one kind."""
    store = root / "index-store"
    a = store / SCOPE
    a.mkdir(parents=True)
    (a / "README.md").write_text("policy sheet, not an entry\n", encoding="utf-8")
    (a / "collector.md").write_text(
        _entry("collector", SCOPE, aliases=["telemetry-collector", "event_tap"]),
        encoding="utf-8",
    )
    (a / "status-bar.md").write_text(
        _entry("status-bar", SCOPE, sensitivity="public"), encoding="utf-8"
    )
    # The ambiguity pair: one slug naming two KINDS of thing.
    (a / "weekly-digest.md").write_text(_entry("weekly-digest", SCOPE), encoding="utf-8")
    (a / "weekly-digest.process.md").write_text(
        _entry("weekly-digest", SCOPE, kind="process"), encoding="utf-8"
    )
    b = store / OTHER_SCOPE
    b.mkdir(parents=True)
    (b / "fan-curve.md").write_text(_entry("fan-curve", OTHER_SCOPE), encoding="utf-8")
    return store


@pytest.fixture()
def store(tmp_path: Path) -> Path:
    return _make_store(tmp_path)


def _tree_hash(root: Path) -> str:
    h = hashlib.sha256()
    for p in sorted(root.rglob("*")):
        h.update(str(p.relative_to(root)).encode())
        h.update(b"\0")
        if p.is_file():
            h.update(p.read_bytes())
        h.update(b"\0\0")
    return h.hexdigest()


# =============================================================================
# THE POSITIVE CONTROL — a pair, reported together.
# =============================================================================


class TestPositiveControl:
    """`claude/RULES.md` → "Positive control — can it ever observe the thing?"

    A reassuring zero is indistinguishable from a reader wired to nothing. The
    two scopes below are read through the SAME call on the SAME store; only
    whether the scope holds entries differs."""

    def test_the_pair(self, store: Path, tmp_path: Path) -> None:
        (store / "empty-scope").mkdir()

        # `mode="full"` because this control counts BODIES. The digest prints one
        # body by design, so the four-entry assertion would be about the display
        # mode rather than about whether the reader is wired to the store.
        pos = rc.recall(store, SCOPE, mode="full")
        neg = rc.recall(store, "empty-scope", mode="full")

        # THE PAIR: non-zero on the control that MUST produce content, zero on
        # the one that must not — same code path, same store, same call.
        assert len(pos.entries) == 4, "positive control surfaced nothing — wired to nothing"
        assert len(neg.entries) == 0

        assert pos.status == "recalled"
        assert neg.status == "scope-empty"

        # And the content is genuinely THERE, not merely a non-empty list.
        text = rc.render_text(pos)
        assert POINTER_LINE in text, "the positive control surfaced no pointer text"
        assert NUANCE_LINE in text, "the positive control surfaced no nuance text"

    def test_the_negative_zero_is_ACCOUNTED_for(self, store: Path) -> None:
        (store / "empty-scope").mkdir()
        neg = rc.recall(store, "empty-scope")
        assert neg.total_in_scope == 0
        assert neg.omitted == 0
        # It is NOT confused with a scope the store never heard of.
        assert neg.status != "scope-absent"
        assert "empty-scope" in neg.known_scopes

    def test_a_populated_scope_and_an_absent_one_differ_in_STATUS_not_only_in_count(
        self, store: Path
    ) -> None:
        assert rc.recall(store, SCOPE).status == "recalled"
        assert rc.recall(store, "never-indexed").status == "scope-absent"

    def test_the_positive_control_would_fail_if_sections_were_not_read(
        self, tmp_path: Path
    ) -> None:
        """Negative control ON the positive control: an entry whose two surfaced
        sections are absent must NOT produce the pointer/nuance text, or the
        assertion above would pass on any store."""
        store = tmp_path / "s"
        (store / SCOPE).mkdir(parents=True)
        (store / SCOPE / "collector.md").write_text(
            _entry("collector", SCOPE, pointers=None, nuance=None), encoding="utf-8"
        )
        text = rc.render_text(rc.recall(store, SCOPE))
        assert POINTER_LINE not in text
        assert NUANCE_LINE not in text
        assert "has not been filled in" in text


# =============================================================================
# THE SIX ZEROS.
# =============================================================================


class TestStatusIsTheDiscriminator:
    """Every declared status must be EMITTED, and no two spelled the same."""

    def test_scope_absent(self, store: Path) -> None:
        rep = rc.recall(store, "never-indexed")
        assert rep.status == "scope-absent"
        assert rep.entries == ()
        # The scopes that DO exist are named, so a normalization surprise is
        # visible instead of reading as "nothing recorded yet".
        assert set(rep.known_scopes) == {SCOPE, OTHER_SCOPE}

    def test_scope_empty(self, store: Path) -> None:
        (store / "made-never-filled").mkdir()
        assert rc.recall(store, "made-never-filled").status == "scope-empty"

    def test_ref_absent(self, store: Path) -> None:
        rep = rc.recall(store, SCOPE, ref="no-such-subsystem")
        assert rep.status == "ref-absent"
        assert rep.entries == ()
        assert rep.total_in_scope == 4, "the scope size must still be reported"

    def test_ref_ambiguous(self, store: Path) -> None:
        rep = rc.recall(store, SCOPE, ref="weekly-digest")
        assert rep.status == "ref-ambiguous"
        assert rep.entries == ()
        assert set(rep.candidates) == {"weekly-digest.md", "weekly-digest.process.md"}

    def test_recalled(self, store: Path) -> None:
        assert rc.recall(store, SCOPE).status == "recalled"

    def test_every_declared_status_is_reachable(self, store: Path) -> None:
        """Derived from `STATUS_PRECEDENCE`, not hand-listed: a status added
        later cannot quietly go unreached.

        `search` shares this vocabulary rather than minting a second one, so its
        two statuses are exercised here too — through the same tuple, so a search
        status that stopped being emitted fails on the same line."""
        (store / "made-never-filled").mkdir()
        # A scope whose ONLY file cannot be indexed — the `*-unreadable` pair.
        # Its own directory, so it cannot perturb the statuses above it.
        (store / "all-broken").mkdir()
        (store / "all-broken" / "wrapped-aliases.md").write_text(
            "---\nservice: widget\naliases: [one,\n  two]\n---\n", encoding="utf-8"
        )
        seen = {
            rc.recall(store, "never-indexed").status,
            rc.recall(store, "made-never-filled").status,
            rc.recall(store, "all-broken").status,
            rc.recall(store, SCOPE, ref="no-such-subsystem").status,
            rc.recall(store, SCOPE, ref="weekly-digest").status,
            rc.recall(store, SCOPE).status,
            rc.search(store, SCOPE, "readiness").status,
            rc.search(store, SCOPE, "kryptonite").status,
            rc.search(store, "all-broken", "readiness").status,
        }
        assert seen == set(rc.STATUS_PRECEDENCE)

    def test_the_search_statuses_do_not_reuse_a_recall_word(self) -> None:
        """🔴 `search-no-match` and not `search-empty`: `scope-empty` already
        means "the scope holds nothing", and a query that matched nothing in a
        FULL scope is a different fact. Two statuses sharing a word get read as
        one — which is how a wrong zero survives a status field."""
        assert "search-no-match" in rc.STATUS_PRECEDENCE
        assert not any(
            s.endswith("-empty") and s.startswith("search") for s in rc.STATUS_PRECEDENCE
        )

    def test_no_two_statuses_share_a_spelling(self) -> None:
        assert len(set(rc.STATUS_PRECEDENCE)) == len(rc.STATUS_PRECEDENCE)


# =============================================================================
# NEGATIVE CONTROLS — each with its OWN sentinel, each proven reachable.
# =============================================================================


class TestNegativeControls:
    def test_absent_scope_is_a_STATUS_and_not_an_exception(self, store: Path) -> None:
        """🔴 The most load-bearing decision in the module. The store holds 2
        scopes while work spans ~12 repos, so "nothing recorded yet" is the
        ORDINARY outcome. Raising would make the common case the failing case."""
        rep = rc.recall(store, "never-indexed")  # must not raise
        assert rep.status == "scope-absent"
        text = rc.render_text(rep)
        assert "NOTHING RECORDED YET" in text
        assert "NOT an error" in text
        # and it must never read as a clean bill of health
        assert "nothing was checked" in text

    def test_a_missing_store_root_RAISES_with_its_own_sentinel(self, tmp_path: Path) -> None:
        with pytest.raises(rc.StoreMissingError) as exc:
            rc.recall(tmp_path / "absent", SCOPE)
        assert "store root not found" in str(exc.value)
        # NOT confused with the ordinary empty case.
        assert "nothing recorded yet" not in str(exc.value).lower() or "NOT" in str(exc.value)

    def test_a_malformed_entry_DEGRADES_and_is_reported_not_raised(self, store: Path) -> None:
        """🔴 SUPERSEDES `test_a_malformed_entry_RAISES_the_RESOLVERS_sentinel`.

        That test pinned fail-closed, and fail-closed was MEASURED to cost the
        whole scope: 2 good entries listed 2, 2 good + 1 malformed listed 0 and
        exited 3. The reader now serves what it can AND names what it cannot —
        both halves asserted here, because degrading without reporting is the
        worse of the two failures.
        """
        (store / SCOPE / "no-front-matter.md").write_text("just prose\n", encoding="utf-8")
        rep = rc.recall(store, SCOPE)  # must not raise
        assert rep.status == "recalled"
        assert rep.listing_total > 0, "the good entries were spent to report a bad one"
        assert [m.filename for m in rep.malformed] == ["no-front-matter.md"]
        text = rc.render_text(rep)
        assert "malformed index entry" in text, "the sentinel must survive into the output"
        assert "no-front-matter.md" in text, "the bad entry must be NAMED, not counted"
        assert "none omitted" not in text, "a completeness claim over an incomplete index"

    def test_an_unreadable_entry_RAISES_its_own_sentinel(self, store: Path) -> None:
        """Reached with a DIRECTORY sitting where a `.md` is expected — an
        OSError any user hits, rather than a mode change that a root-run sandbox
        would silently bypass and make this control vacuous."""
        (store / SCOPE / "broken.md").mkdir()
        with pytest.raises(rc.EntryUnreadableError) as exc:
            rc.recall(store, SCOPE)
        assert "index entry unreadable" in str(exc.value)
        assert "INCOMPLETE" in str(exc.value)

    def test_read_entry_wraps_its_OWN_read_too(self, store: Path, tmp_path: Path) -> None:
        """The second unreadable site: an entry that loaded fine, then could not
        be read for its body. Reached directly, because the two reads are
        separate code paths and only one of them is covered above."""
        entry = sr.SubsystemEntry.from_mapping(
            {"service": "ghost", "scope": SCOPE, "filename": "ghost.md"}
        )
        (store / SCOPE / "ghost.md").mkdir()
        with pytest.raises(rc.EntryUnreadableError) as exc:
            rc.read_entry(store, entry)
        assert "index entry unreadable" in str(exc.value)

    def test_a_nonsense_limit_RAISES_ValueError(self, store: Path) -> None:
        for bad in (0, -1, True, 2.5, "3"):
            with pytest.raises(ValueError) as exc:
                rc.recall(store, SCOPE, limit=bad)
            assert "limit must be an int >= 1" in str(exc.value)

    def test_each_failure_says_something_DIFFERENT(self, store: Path, tmp_path: Path) -> None:
        """🔴 The whole point of a sentinel: four failures that share a spelling
        are one failure with four causes."""
        msgs = []
        with pytest.raises(rc.StoreMissingError) as e1:
            rc.recall(tmp_path / "absent", SCOPE)
        msgs.append(str(e1.value))
        (store / SCOPE / "broken.md").mkdir()
        with pytest.raises(rc.EntryUnreadableError) as e2:
            rc.recall(store, SCOPE)
        msgs.append(str(e2.value))
        with pytest.raises(ValueError) as e3:
            rc.recall(store, SCOPE, limit=0)
        msgs.append(str(e3.value))
        sentinels = ["store root not found", "index entry unreadable", "limit must be an int"]
        for sentinel, msg in zip(sentinels, msgs):
            assert sentinel in msg
            others = [s for s in sentinels if s != sentinel]
            assert not any(o in msg for o in others), f"{msg!r} carries a neighbour's sentinel"


# =============================================================================
# WHAT IS SURFACED — the three schema sections, and NOTHING else.
# =============================================================================


class TestSurfacesOnlyTheThreeSections:
    def test_what_it_is_IS_surfaced_in_a_body(self, store: Path) -> None:
        """🔴 THE DEFECT THIS CLASS WAS RENAMED FOR. It used to assert the
        opposite — `## What it is` was excluded as "durable boilerplate" — and
        measured on 2026-08-20 the exclusion meant no BRIEFING path printed the
        one section that says what a service IS: not `--ref`, not the digest, not
        `service_recon`'s `index:` block. (`search` did, and still does — see
        `test_search_covers_WHAT_IT_IS_like_every_other_section` — but only for an
        entry a query matched, so it briefs nobody.) An agent briefed only on an
        entry could not name the thing the entry was about."""
        rep = rc.recall(store, SCOPE)
        text = rc.render_text(rep)
        assert rc.WHAT_HEADING in text
        assert WHAT_IT_IS in text
        blob = json.dumps(rc.report_json(rep))
        assert WHAT_IT_IS in blob

    def test_what_it_is_is_rendered_FIRST_in_a_body(self, store: Path) -> None:
        """It is the orienting sentence; pointers and nuance both assume the
        reader has already identified the thing."""
        text = rc.render_text(rc.recall(store, SCOPE))
        assert text.index(rc.WHAT_HEADING) < text.index(rc.POINTERS_HEADING)
        assert text.index(rc.POINTERS_HEADING) < text.index(rc.NUANCE_HEADING)

    def test_all_three_wanted_sections_ARE_surfaced(self, store: Path) -> None:
        text = rc.render_text(rc.recall(store, SCOPE))
        assert rc.WHAT_HEADING in text
        assert rc.POINTERS_HEADING in text
        assert rc.NUANCE_HEADING in text
        assert WHAT_IT_IS in text
        assert POINTER_LINE in text
        assert NUANCE_LINE in text

    def test_an_unrelated_section_is_not_surfaced(self, tmp_path: Path) -> None:
        store = tmp_path / "s"
        (store / SCOPE).mkdir(parents=True)
        (store / SCOPE / "collector.md").write_text(
            _entry("collector", SCOPE, extra_body="## Runbook\nsecret runbook body"),
            encoding="utf-8",
        )
        text = rc.render_text(rc.recall(store, SCOPE))
        assert "secret runbook body" not in text
        assert "## Runbook" not in text

    def test_bodies_are_VERBATIM(self, tmp_path: Path) -> None:
        """The store is markdown so prose survives a read unmangled."""
        store = tmp_path / "s"
        (store / SCOPE).mkdir(parents=True)
        body = "- one — with an em dash\n- two `with backticks`\n  - nested"
        (store / SCOPE / "collector.md").write_text(
            _entry("collector", SCOPE, pointers=body), encoding="utf-8"
        )
        rep = rc.recall(store, SCOPE)
        assert rep.entries[0].sections[rc.POINTERS_HEADING] == body

    def test_a_missing_section_is_NAMED_not_silently_empty(self, tmp_path: Path) -> None:
        store = tmp_path / "s"
        (store / SCOPE).mkdir(parents=True)
        (store / SCOPE / "collector.md").write_text(
            _entry("collector", SCOPE, nuance=None), encoding="utf-8"
        )
        rep = rc.recall(store, SCOPE)
        assert rep.entries[0].missing_sections == (rc.NUANCE_HEADING,)
        assert f"(no `{rc.NUANCE_HEADING}` section)" in rc.render_text(rep)

    def test_a_bare_entry_SAYS_it_is_bare(self, tmp_path: Path) -> None:
        """An entry with neither section is a real state — the writer's own
        template ships a stub. Printing nothing for it is indistinguishable from
        an extractor that failed."""
        store = tmp_path / "s"
        (store / SCOPE).mkdir(parents=True)
        (store / SCOPE / "collector.md").write_text(
            _entry("collector", SCOPE, pointers=None, nuance=None), encoding="utf-8"
        )
        rep = rc.recall(store, SCOPE)
        assert rep.entries[0].is_bare
        assert "has not been filled in" in rc.render_text(rep)

    def test_a_what_it_is_stub_does_NOT_make_an_entry_look_filled_in(
        self, tmp_path: Path
    ) -> None:
        """🔴 THE REGRESSION SURFACING CREATED. `is_bare` used to read every
        value in `sections`, and `sections` now carries `## What it is` — which
        the writer's own `new_entry_template` PRE-FILLS with a placeholder. Read
        naively, every freshly created stub would report as filled-in and lose
        the "has not been filled in" notice in exactly the case it exists for."""
        store = tmp_path / "s"
        (store / SCOPE).mkdir(parents=True)
        (store / SCOPE / "collector.md").write_text(
            _entry("collector", SCOPE, what="<one line: what this thing IS.>",
                   pointers=None, nuance=None),
            encoding="utf-8",
        )
        rep = rc.recall(store, SCOPE)
        assert rep.entries[0].sections[rc.WHAT_HEADING]  # the stub IS there…
        assert rep.entries[0].is_bare  # …and the entry is still bare.
        assert "has not been filled in" in rc.render_text(rep)


class TestWhatItIsDegradesCleanly:
    """An entry whose `## What it is` is absent, or present-but-empty, must not
    crash and must not print a stray heading with nothing under it.

    🔴 ABSENT AND PRESENT-BUT-EMPTY ARE DIFFERENT ON-DISK STATES and the
    extractor tells them apart (`TestSectionExtraction`), so both are exercised:
    a renderer keyed on `in sections` rather than on truthiness would print a
    bare `## What it is` line for the second one.
    """

    def _one(self, tmp_path: Path, **kw) -> str:
        # A store per call — the loop below builds two, and reusing one path
        # would make the second render read the FIRST fixture's file.
        store = tmp_path / ("absent" if kw.get("what") is None else "empty")
        (store / SCOPE).mkdir(parents=True)
        (store / SCOPE / "collector.md").write_text(
            _entry("collector", SCOPE, **kw), encoding="utf-8"
        )
        return rc.render_text(rc.recall(store, SCOPE))

    def test_an_ABSENT_what_it_is_prints_no_stray_heading(self, tmp_path: Path) -> None:
        text = self._one(tmp_path, what=None)
        assert f"    {rc.WHAT_HEADING}\n" not in text
        assert POINTER_LINE in text and NUANCE_LINE in text

    def test_an_EMPTY_what_it_is_prints_no_stray_heading(self, tmp_path: Path) -> None:
        text = self._one(tmp_path, what="")
        assert f"    {rc.WHAT_HEADING}\n" not in text
        assert POINTER_LINE in text and NUANCE_LINE in text

    #: The notice, pinned as ONE normalised sentence rather than by a keyword.
    #: A guard on a word is walkable by rewording, and this sentence is the whole
    #: claim — see `test_the_notice_claims_the_PARSE_not_the_ENTRY`.
    NOTICE = (
        "(no parsable `## What it is` — absent, empty, or not parsed as a heading "
        "[renamed, indented, fenced, among others], so this read cannot say what "
        "the subsystem IS; re-derive it live)"
    )

    def test_both_states_are_NAMED_not_silently_dropped(self, tmp_path: Path) -> None:
        """Said, not left blank — the rule the bare-entry notice already
        follows. Nothing printed is indistinguishable from an extractor that
        failed to find the section."""
        for kw in ({"what": None}, {"what": ""}):
            text = self._one(tmp_path, **kw)
            assert self.NOTICE in text, kw

    @pytest.mark.parametrize(
        "heading",
        ["## What It Is", "## What it is:", "### What it is", "  ## What it is"],
        ids=["case", "colon", "depth", "indent"],
    )
    def test_the_notice_claims_the_PARSE_not_the_ENTRY(
        self, tmp_path: Path, heading: str
    ) -> None:
        """🔴 A THIRD ON-DISK STATE, and the one the old wording lied about.

        `_heading_blocks` matches a heading EXACTLY, at column 0 — so every
        rename here parses to nothing and lands in this same branch while the
        sentence sits on disk. The notice used to read "this entry never says
        what the subsystem IS", which is a claim about the ENTRY that the
        extractor is in no position to make; and `subsystem_touch.SHAPE_HEADINGS`
        deliberately excludes `## What it is`, so `--validate` reports the rename
        nowhere either. The sibling `🔴 NO <heading>` badge already draws this
        line ("0 BY PARSE FAILURE and not by measurement"); this notice now does
        too, and names the rename so the reader knows where to look.
        """
        store = tmp_path / "renamed"
        (store / SCOPE).mkdir(parents=True)
        marooned = "This entry DOES say what it is, under a renamed heading."
        (store / SCOPE / "collector.md").write_text(
            _entry("collector", SCOPE, what=None).replace(
                "## Pointers", f"{heading}\n{marooned}\n\n## Pointers", 1
            ),
            encoding="utf-8",
        )
        text = rc.render_text(rc.recall(store, SCOPE))
        assert marooned not in text, "fixture is inert — the extractor matched the rename"
        assert self.NOTICE in text
        # 🔴 …and it makes no claim about the entry that the parse cannot support.
        assert "never says what the subsystem IS" not in text
        assert "no `## What it is` content" not in text

    def test_a_FENCED_heading_reaches_the_SAME_notice(self, tmp_path: Path) -> None:
        """🔴 THE CAUSE THE OLD ENUMERATION LEFT UNNAMED, and why the list now
        ends "among others".

        `_heading_blocks` skips fenced regions wholesale, so a `## What it is`
        inside a ``` fence is not a heading at all: it reaches this branch while
        being neither absent, nor empty, nor RENAMED — the three words the notice
        used to offer. An indented heading is not literally a rename either. The
        headline (`no parsable`) was always right; only the tail enumeration was
        narrower than its own branch, which reads as a closed list and sends the
        reader looking for the two states it names.
        """
        store = tmp_path / "fenced"
        (store / SCOPE).mkdir(parents=True)
        marooned = "This entry DOES say what it is, inside a code fence."
        fenced = f"```\n{rc.WHAT_HEADING}\n{marooned}\n```"
        (store / SCOPE / "collector.md").write_text(
            _entry("collector", SCOPE, what=None).replace(
                "## Pointers", f"{fenced}\n\n## Pointers", 1
            ),
            encoding="utf-8",
        )
        text = rc.render_text(rc.recall(store, SCOPE))
        assert marooned not in text, "fixture is inert — the extractor read into the fence"
        assert self.NOTICE in text
        # The rest of the entry is untouched: this is a section-level degrade.
        assert POINTER_LINE in text and NUANCE_LINE in text

    def test_the_notice_does_NOT_reach_the_index_row(self, tmp_path: Path) -> None:
        """🔴 The body is where the reader is already looking; the index row is
        printed once PER ENTRY and this decision leaves it at zero bytes. A
        missing `## What it is` is also NOT a `🔴 NO <heading>` badge, because
        that badge means "a count on this row is not a measurement" and this
        section feeds no count."""
        store = tmp_path / "s"
        (store / SCOPE).mkdir(parents=True)
        (store / SCOPE / "collector.md").write_text(
            _entry("collector", SCOPE, what=None), encoding="utf-8"
        )
        rep = rc.recall(store, SCOPE)
        assert rep.entries[0].missing_sections == ()
        row = rc.listing_line(rep.listing[0], 20)
        assert "🔴" not in row and "What it is" not in row
        # …and a control that the badge machinery is not simply inert here: an
        # entry missing a COUNTED heading in the same store DOES badge its row.
        (store / SCOPE / "other.md").write_text(
            _entry("other", SCOPE, nuance=None), encoding="utf-8"
        )
        rows = {e.ref: rc.listing_line(e, 20) for e in rc.recall(store, SCOPE).listing}
        assert "🔴 NO Nuance / work-history" in rows["other"]
        assert "🔴" not in rows["collector"]


class TestWhatItIsIsBodyOnlyNotPerEntry:
    """🔴 THE DECISION, MACHINE-CHECKED. Surfacing `## What it is` is unambiguous
    on the SINGLE-entry paths; the risk was the multi-entry ones, where a scope
    holding N entries would pay N copies of it.

    The choice: **the full section in every printed BODY, and nothing at all on
    the index rows.** A body is printed once per `--ref`, once per digest and
    `--limit N` times in `full` mode — which is already an explicit dump of N
    whole entries. The index row is printed once per entry on EVERY read, which
    is where the multiplier actually lives, so it stays byte-identical.

    The tests below pin that as a COUNT THAT DOES NOT SCALE WITH N: grow the
    scope and the digest's occurrence count must stay at 1. Asserting merely
    that it "appears" would pass just as well for a renderer that printed it N
    times, which is the outcome this decision exists to prevent.
    """

    @staticmethod
    def _scope_of_size(root: Path, n: int) -> Path:
        store = root / "index-store"
        (store / SCOPE).mkdir(parents=True)
        for i in range(n):
            (store / SCOPE / f"svc-{i:02d}.md").write_text(
                _entry(f"svc-{i:02d}", SCOPE), encoding="utf-8"
            )
        return store

    def test_the_digest_prints_it_ONCE_however_many_entries_the_scope_holds(
        self, tmp_path: Path
    ) -> None:
        """🔴 TWO POINTS ON THE DIMENSION, per `claude/RULES.md` — one
        measurement of a size-dependent claim is not a general claim. 3 and 24
        are a boundary and a middle; 24 is deliberately not a multiple of the
        default entry limit."""
        counts = {}
        for n in (3, 24):
            store = self._scope_of_size(tmp_path / f"n{n}", n)
            text = rc.render_text(rc.recall(store, SCOPE))
            assert text.count("### ") == 1, "the digest stopped printing exactly one body"
            counts[n] = text.count(WHAT_IT_IS)
        assert counts == {3: 1, 24: 1}, counts

    def test_the_index_rows_carry_NO_what_it_is_at_all(self, tmp_path: Path) -> None:
        """`--list` prints the whole index and no bodies. It is the pure
        per-entry surface, so its occurrence count is the one that must be 0."""
        store = self._scope_of_size(tmp_path, 24)
        rep = rc.recall(store, SCOPE, mode="list")
        text = rc.render_text(rep)
        assert "NO ENTRY BODIES WERE PRINTED" in text
        assert WHAT_IT_IS not in text
        assert rc.WHAT_HEADING not in "\n".join(
            rc.listing_line(e, 12) for e in rep.listing
        )

    def test_a_ref_lookup_prints_the_FULL_section_not_a_first_line(
        self, tmp_path: Path
    ) -> None:
        """The other half of the decision: `--ref` is the single-entry path
        `service_recon` uses, and it is not truncated. A first-line-only render
        would silently drop the p90 entry's other 7 lines."""
        store = tmp_path / "s"
        (store / SCOPE).mkdir(parents=True)
        body = "Line one of the description.\nLine two.\nLine three, the last."
        (store / SCOPE / "collector.md").write_text(
            _entry("collector", SCOPE, what=body), encoding="utf-8"
        )
        text = rc.render_text(rc.recall(store, SCOPE, ref="collector"))
        for line in body.splitlines():
            assert line in text

    def test_full_mode_prints_it_per_BODY_which_is_the_opt_in_dump(
        self, tmp_path: Path
    ) -> None:
        """Stated rather than left implicit: `--mode full --limit N` DOES pay N
        copies. That mode already prints N whole entries on purpose, and across
        the live store `## What it is` is the smallest of the three sections
        (26 KB vs `## Pointers` 49 KB vs `## Nuance` 235 KB, measured
        2026-08-20)."""
        store = self._scope_of_size(tmp_path, 5)
        text = rc.render_text(rc.recall(store, SCOPE, mode="full", limit=5))
        assert text.count("### ") == 5
        assert text.count(WHAT_IT_IS) == 5


class TestSectionExtraction:
    def test_a_heading_inside_a_fence_does_not_end_the_section(self) -> None:
        """🔴 Otherwise HALF an entry's nuance is surfaced while the output looks
        like a complete read — a silent under-report."""
        text = (
            "## Pointers\n- p\n\n"
            "## Nuance / work-history\n"
            "- 2026-01-01: run this:\n"
            "```\n"
            "## not a heading\n"
            "```\n"
            "- 2026-01-02: the SECOND bullet, after the fence\n"
        )
        got = rc.extract_sections(text)
        assert "the SECOND bullet, after the fence" in got[rc.NUANCE_HEADING]
        assert "## not a heading" in got[rc.NUANCE_HEADING]

    def test_a_present_but_EMPTY_section_is_present_not_absent(self) -> None:
        """Mid-file and at end-of-file must agree — deriving presence from a
        non-empty body made them disagree."""
        mid = rc.extract_sections("## Pointers\n\n## Nuance / work-history\n- x\n")
        assert rc.POINTERS_HEADING in mid and mid[rc.POINTERS_HEADING] == ""
        end = rc.extract_sections("## Nuance / work-history\n- x\n\n## Pointers\n")
        assert rc.POINTERS_HEADING in end and end[rc.POINTERS_HEADING] == ""

    def test_an_absent_section_is_absent(self) -> None:
        got = rc.extract_sections("## Pointers\n- p\n")
        assert rc.NUANCE_HEADING not in got

    def test_a_deeper_heading_also_ends_the_section(self) -> None:
        got = rc.extract_sections("## Pointers\n- p\n### Sub\nnot a pointer\n")
        assert got[rc.POINTERS_HEADING] == "- p"

    def test_matching_is_EXACT_not_normalized(self) -> None:
        """These are schema headings, not user refs. Normalizing them would
        quietly widen what the store is allowed to look like."""
        assert rc.extract_sections("## pointers\n- p\n") == {}
        assert rc.extract_sections("##Pointers\n- p\n") == {}

    def test_there_is_ONE_parser_and_this_module_only_binds_its_DEFAULT(self) -> None:
        """🔴 The anti-duplication pin, structural rather than by eye.

        `subsystem_touch` needed the same extraction to show a `/handoff` what an
        entry already says. The parser moved to `subsystem_resolver` so both
        modules call ONE function; what stayed here is a shim that supplies this
        reader's default heading set. If someone re-inlines a copy, this fails.
        """
        text = "## Pointers\n- p\n## Nuance / work-history\n- 2026-01-01: n\n"
        assert rc.extract_sections(text) == sr.extract_sections(text, rc.SURFACED_HEADINGS)
        assert rc.POINTERS_HEADING is sr.POINTERS_HEADING
        assert rc.NUANCE_HEADING is sr.NUANCE_HEADING
        # The shim's only job: bind the default. Called with one argument the
        # resolver's own function would not even be callable.
        with pytest.raises(TypeError):
            sr.extract_sections(text)

    def test_the_unreadable_entry_error_is_the_SAME_class_the_writer_raises(self) -> None:
        """🔴 One condition, one class. Two spellings is how a caller catches the
        reader's failure and misses the writer's on the very same file."""
        assert rc.EntryUnreadableError is sr.EntryUnreadableError
        assert st.EntryUnreadableError is sr.EntryUnreadableError


class TestSensitivityIsFailSafe:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("public", "public"),
            ("personal", "personal"),
            ("client-confidential", "client-confidential"),
            (None, "client-confidential"),
            ("", "client-confidential"),
            ("PUBLIC ", "public"),
            ("probably-fine", "client-confidential"),
            (["public"], "client-confidential"),
            (True, "client-confidential"),
        ],
    )
    def test_the_fold(self, raw, expected) -> None:
        assert rc.fold_sensitivity(raw) == expected

    def test_an_absent_field_surfaces_as_confidential(self, store: Path) -> None:
        rep = rc.recall(store, SCOPE, ref="collector")
        assert rep.entries[0].sensitivity == "client-confidential"
        assert "sensitivity=client-confidential" in rc.render_text(rep)

    def test_an_explicit_public_claim_is_honoured(self, store: Path) -> None:
        rep = rc.recall(store, SCOPE, ref="status-bar")
        assert rep.entries[0].sensitivity == "public"


# =============================================================================
# HOW ENTRIES ARE SELECTED — scope-wide, and why.
# =============================================================================


class TestAnOverriddenSensitivityIsVISIBLE:
    """🔴 THE FOLD IS NEVER WEAKENED — this makes it AUDIBLE.

    `sensitivity: public` is honoured because it is one of the schema's three
    values; `sensitivity: internal` is not a schema value at all, so the
    fail-safe overrides it. That asymmetry is deliberate and correct. What was
    NOT deliberate is that the override was SILENT: `internal` and an absent
    marker both rendered as a bare `client-confidential`, so the file's own claim
    disappeared and the author who typed a word the schema does not know got no
    signal. Absent stays silent (nothing was claimed); an overridden declaration
    is printed beside the effective value.
    """

    def test_the_fold_itself_is_UNCHANGED(self, tmp_path: Path) -> None:
        """The safety property first: nothing here may make an unknown marker
        read as less sensitive than the fail-safe."""
        for raw in ("internal", "INTERNAL", "probably-fine", "", None, 7, True):
            assert rc.fold_sensitivity(raw) == rc.SENSITIVITY_FAIL_SAFE, raw
        for known in rc.KNOWN_SENSITIVITIES:
            assert rc.fold_sensitivity(known.upper()) == known

    def test_only_an_OVERRIDDEN_declaration_is_reported(self) -> None:
        assert rc.discarded_sensitivity("internal") == "internal"
        assert rc.discarded_sensitivity("  Internal  ") == "Internal"
        # Honoured values and absence report nothing — an annotation on every
        # row would be noise, and noise is what gets skimmed past.
        for quiet in ("public", "personal", "client-confidential", "", "   ", None, 7):
            assert rc.discarded_sensitivity(quiet) is None, quiet

    def test_it_is_DERIVED_from_the_fold_and_cannot_disagree_with_it(self) -> None:
        """Swept over the same inputs both functions see: a value is reported as
        overridden if and only if the fold did not keep it."""
        for raw in ("public", "personal", "internal", "secret", "PUBLIC", "", None):
            kept = isinstance(raw, str) and rc.fold_sensitivity(raw) == raw.strip().lower()
            declared = rc.discarded_sensitivity(raw)
            if isinstance(raw, str) and raw.strip():
                assert (declared is None) == kept, raw
            else:
                assert declared is None, raw

    def test_the_index_row_shows_the_override(self, tmp_path: Path) -> None:
        store = tmp_path / "s"
        (store / SCOPE).mkdir(parents=True)
        (store / SCOPE / "collector.md").write_text(
            _entry("collector", SCOPE, sensitivity="internal"), encoding="utf-8"
        )
        (store / SCOPE / "status-bar.md").write_text(
            _entry("status-bar", SCOPE, sensitivity="public"), encoding="utf-8"
        )
        (store / SCOPE / "weekly-digest.md").write_text(
            _entry("weekly-digest", SCOPE), encoding="utf-8"
        )
        rows = {e.ref: rc.listing_line(e, 16) for e in rc.recall(store, SCOPE, mode="list").listing}
        assert "client-confidential (declared: internal)" in rows["collector"]
        # The honoured value and the absent one are unannotated — three states,
        # two spellings would collapse two of them.
        assert rows["status-bar"].endswith("public")
        assert rows["weekly-digest"].endswith("client-confidential")

    def test_the_override_travels_onto_a_HUNK_too(self, tmp_path: Path) -> None:
        """Hunk output interleaves entries, so an override noted anywhere but on
        the hunk is one the reader never sees beside the lines it governs."""
        store = tmp_path / "s"
        (store / SCOPE).mkdir(parents=True)
        (store / SCOPE / "collector.md").write_text(
            _entry("collector", SCOPE, sensitivity="internal"), encoding="utf-8"
        )
        hunk = rc.search(store, SCOPE, "readiness").hunks[0]
        assert hunk.sensitivity == rc.SENSITIVITY_FAIL_SAFE
        assert hunk.declared_sensitivity == "internal"
        assert "sensitivity=client-confidential (declared: internal)" in rc.render_search(
            rc.search(store, SCOPE, "readiness")
        )

    def test_the_JSON_keeps_the_two_APART(self, tmp_path: Path) -> None:
        """A consumer must be able to act on the EFFECTIVE value without parsing
        prose, and still see what the file claimed."""
        store = tmp_path / "s"
        (store / SCOPE).mkdir(parents=True)
        (store / SCOPE / "collector.md").write_text(
            _entry("collector", SCOPE, sensitivity="internal"), encoding="utf-8"
        )
        blob = rc.report_json(rc.recall(store, SCOPE, mode="list"))
        row = blob["listing"][0]
        assert row["sensitivity"] == "client-confidential"
        assert row["declared_sensitivity"] == "internal"
        hunk = rc.search_json(rc.search(store, SCOPE, "readiness"))["hunks"][0]
        assert hunk["sensitivity"] == "client-confidential"
        assert hunk["declared_sensitivity"] == "internal"


class TestSelectionIsScopeWide:
    """🔴 The reader does NOT reuse the writer's path-derived selection, and the
    asymmetry is the reason. A resuming session has no PRs, a one-turn
    transcript and (ordinarily) a clean tree at the base ref, so a path window
    is empty exactly when recall is worth most."""

    def test_every_entry_in_the_scope_is_surfaced(self, store: Path) -> None:
        rep = rc.recall(store, SCOPE, mode="full")
        assert [e.ref for e in rep.entries] == [
            "collector",
            "status-bar",
            "weekly-digest",
            "weekly-digest.process",
        ]

    def test_other_scopes_are_NOT_surfaced(self, store: Path) -> None:
        refs = [e.ref for e in rc.recall(store, SCOPE, mode="full").entries]
        assert "fan-curve" not in refs

    def test_the_readme_is_not_an_entry(self, store: Path) -> None:
        assert "readme" not in [e.ref for e in rc.recall(store, SCOPE, mode="full").entries]

    def test_order_is_canonical_and_STABLE(self, store: Path) -> None:
        """Two runs over an unchanged store must produce identical bytes, or a
        diff of them shows churn that is not there.

        ⚠ THE TWO ORDERS DIFFER ON PURPOSE since the index was capped. Bodies
        keep the canonical ref order (`full` is a requested dump and its reader is
        scanning for a name); the INDEX is newest-first by mtime, because cutting
        an alphabetical list at `LISTING_PAGE_SIZE` would hide entries by an
        accident of their names. Both are deterministic, which is what this test
        is actually about."""
        for mode in rc.RECALL_MODES:
            a = rc.render_text(rc.recall(store, SCOPE, mode=mode))
            b = rc.render_text(rc.recall(store, SCOPE, mode=mode))
            assert a == b, mode
        refs = [e.ref for e in rc.recall(store, SCOPE, mode="full").entries]
        assert refs == sorted(refs)
        listed = [e.ref for e in rc.recall(store, SCOPE).listing]
        assert sorted(listed) == sorted(refs), "the index stopped covering the scope"
        by_ref = {e.ref: e for e in rc.recall(store, SCOPE).listing}
        mtimes = [by_ref[r].mtime for r in listed]
        assert mtimes == sorted(mtimes, reverse=True), "the index is not newest-first"

    def test_a_ref_narrows_to_ONE_entry(self, store: Path) -> None:
        rep = rc.recall(store, SCOPE, ref="collector")
        assert [e.ref for e in rep.entries] == ["collector"]
        assert rep.total_in_scope == 4

    def test_a_ref_goes_through_the_WRITERS_resolver(self, store: Path) -> None:
        """An alias must reach the entry, and a kind-qualified ref must reach
        only its own file — both are the resolver's rules, not a second matcher
        spelled here."""
        assert [e.ref for e in rc.recall(store, SCOPE, ref="event_tap").entries] == ["collector"]
        assert [e.ref for e in rc.recall(store, SCOPE, ref="Telemetry Collector").entries] == [
            "collector"
        ]
        assert [e.ref for e in rc.recall(store, SCOPE, ref="weekly-digest.process").entries] == [
            "weekly-digest.process"
        ]

    def test_an_ambiguous_ref_is_NEVER_picked(self, store: Path) -> None:
        rep = rc.recall(store, SCOPE, ref="weekly-digest")
        assert rep.entries == ()
        text = rc.render_text(rep)
        assert "AMBIGUOUS REF" in text
        assert "never picks" in text
        for candidate in rep.candidates:
            assert candidate in text


class TestTruncationIsLoud:
    def test_a_truncation_is_PRINTED(self, store: Path) -> None:
        rep = rc.recall(store, SCOPE, mode="full", limit=2)
        assert len(rep.entries) == 2
        assert rep.total_in_scope == 4
        assert rep.omitted == 2
        text = rc.render_text(rep)
        assert "2 more entries" in text
        assert "NOT shown" in text
        assert "display cap" in text

    def test_no_truncation_prints_no_truncation_line(self, store: Path) -> None:
        text = rc.render_text(rc.recall(store, SCOPE, mode="full", limit=99))
        assert "NOT shown" not in text

    def test_a_ref_run_reports_no_omission(self, store: Path) -> None:
        """One entry out of four is a NARROWING, not a truncation — calling it
        an omission would train the reader to ignore the real one."""
        rep = rc.recall(store, SCOPE, ref="collector")
        assert rep.omitted == 0
        assert "NOT shown" not in rc.render_text(rep)

    def test_the_count_is_in_the_json_too(self, store: Path) -> None:
        blob = rc.report_json(rc.recall(store, SCOPE, mode="full", limit=1))
        assert blob["omitted"] == 3
        assert blob["total_in_scope"] == 4


# =============================================================================
# THE DIGEST — an INDEX of everything plus ONE body, and the basis for the pick.
# =============================================================================
#
# 🔴 WHAT THE REGRESSION IS. The old default was `--limit 12` over a scope
# holding 25 entries: 31,485 B (~7,871 tok) that ALSO hid 13 of the 25. Expensive
# AND incomplete. A `/resume` step that displaces the task it was loaded for gets
# dropped, which is how the store came to have two writers and no reader in the
# first place. `BIG_SCOPE` below holds more entries than `DEFAULT_ENTRY_LIMIT`
# precisely so the "incomplete" half is observable in a test rather than only on
# the real, client-confidential store.

BIG_SCOPE = "many-widgets"
BIG_N = 15
FOCUS_SLUG = "widget-07"


def _make_big_store(root: Path) -> Path:
    store = root / "big-store"
    d = store / BIG_SCOPE
    d.mkdir(parents=True)
    for i in range(1, BIG_N + 1):
        slug = f"widget-{i:02d}"
        nuance = "\n".join(f"- 2026-01-{i:02d}: note {j} for {slug}." for j in range(i))
        path = d / f"{slug}.md"
        path.write_text(
            _entry(slug, BIG_SCOPE, pointers=f"- pointer for {slug}", nuance=nuance or None),
            encoding="utf-8",
        )
        # 🔴 MTIME IS PINNED, not left to how fast the loop ran. The index is
        # ordered newest-first by mtime, so an order that depended on write speed
        # would be a test asserting its own timing. Higher NN = newer, so the
        # index order is widget-15 … widget-01 by construction and `widget-15` is
        # the most-recent fallback's pick.
        __import__("os").utime(path, (1_700_000_000 + i, 1_700_000_000 + i))
    return store


@pytest.fixture()
def big_store(tmp_path: Path) -> Path:
    return _make_big_store(tmp_path)


class TestTheDefaultIsCompleteAndCheap:
    """🔴 RED AT origin/main ON THE ASSERTION, not on a missing kwarg: the old
    default printed 12 of 15 bodies and never named the other three at all."""

    def test_the_default_names_EVERY_entry(self, big_store: Path) -> None:
        text = rc.render_text(rc.recall(big_store, BIG_SCOPE))
        for i in range(1, BIG_N + 1):
            assert f"widget-{i:02d}" in text, (
                f"widget-{i:02d} is absent from the default output — the default is "
                f"incomplete again, which is half of what the digest exists to fix."
            )

    def test_the_default_prints_exactly_ONE_body(self, big_store: Path) -> None:
        rep = rc.recall(big_store, BIG_SCOPE)
        assert len(rep.entries) == 1
        text = rc.render_text(rep)
        # The pointer line is per-entry and pairwise distinct, so counting them
        # counts BODIES — not headings, not index lines.
        bodies = sum(text.count(f"- pointer for widget-{i:02d}") for i in range(1, BIG_N + 1))
        assert bodies == 1, f"{bodies} bodies printed; the digest prints one"

    def test_the_default_is_SMALLER_than_the_old_default(self, big_store: Path) -> None:
        digest = len(rc.render_text(rc.recall(big_store, BIG_SCOPE)).encode())
        old = len(
            rc.render_text(
                rc.recall(big_store, BIG_SCOPE, mode="full", limit=rc.DEFAULT_ENTRY_LIMIT)
            ).encode()
        )
        assert digest < old, (digest, old)

    def test_a_generous_size_CEILING(self, big_store: Path) -> None:
        """A ceiling, not a pinned size — an exact byte count would go red on
        every reworded sentence and teach the next person to bump the number.

        The bound: the fixed caveat/header (~1.3 KB measured on the real store)
        + one line per entry (~60 B) + ONE body. `BIG_SCOPE`'s largest body is
        15 bullets, far above the real store's median entry. 8 KB is roughly 4x
        what this fixture actually produces and still ~4x BELOW the old
        `--limit 12` default it replaces, so it can only fire if the digest has
        started printing bodies it should not.
        """
        size = len(rc.render_text(rc.recall(big_store, BIG_SCOPE)).encode())
        assert size < 8192, f"the digest grew to {size} B — is it printing extra bodies?"

    def test_the_UNSHOWN_bodies_are_announced(self, big_store: Path) -> None:
        """The other half of "truncation is never silent": in the digest nothing
        is truncated, but 14 bodies genuinely were not printed and the reader is
        told so, in words that do NOT borrow the `--limit` notice's wording."""
        rep = rc.recall(big_store, BIG_SCOPE)
        assert rep.omitted == BIG_N - 1
        text = rc.render_text(rep)
        assert "LISTED ABOVE but NOT shown in full" in text
        assert "Nothing is hidden" in text
        assert f"--limit {BIG_N}" in text, "the escape hatch must name a usable number"
        assert "display cap" not in text, "the digest borrowed the truncation wording"

    def test_the_json_carries_the_index_without_the_bodies(self, big_store: Path) -> None:
        blob = rc.report_json(rc.recall(big_store, BIG_SCOPE))
        assert blob["mode"] == "digest"
        # NEWEST-FIRST: the fixture pins mtime ascending with NN, so the index
        # runs widget-15 → widget-01. Every entry is still there; only the order
        # changed when the index gained a page cap.
        assert [row["ref"] for row in blob["listing"]] == [
            f"widget-{i:02d}" for i in range(BIG_N, 0, -1)
        ]
        assert blob["listing_total"] == BIG_N
        assert (blob["listing_page"], blob["listing_pages"]) == (1, 1)
        assert len(blob["entries"]) == 1
        # The index rows are rows, not entries: no `sections` key anywhere in them.
        assert all("sections" not in row for row in blob["listing"])


class TestTheIndexIsNeverTruncated:
    def test_every_entry_gets_a_line(self, big_store: Path) -> None:
        for mode in ("digest", "list"):
            rep = rc.recall(big_store, BIG_SCOPE, mode=mode)
            assert len(rep.listing) == BIG_N == rep.total_in_scope, mode

    def test_a_limit_cannot_shrink_the_index(self, big_store: Path) -> None:
        """🔴 `--limit` is a cap on BODIES. If it ever reached the index, the one
        complete thing the digest offers would silently stop being complete."""
        rep = rc.recall(big_store, BIG_SCOPE, limit=2)
        assert len(rep.listing) == BIG_N
        assert "none omitted" in rc.render_text(rep)

    def test_the_line_carries_ref_size_and_sensitivity(self, store: Path) -> None:
        rep = rc.recall(store, SCOPE, mode="list")
        by_ref = {e.ref: e for e in rep.listing}
        line = rc.listing_line(by_ref["status-bar"], 12)
        assert "status-bar" in line
        assert "public" in line, "the per-entry sensitivity left its own line"
        assert "1 nuance" in line, "the size signal is missing"
        # It stays ONE line, or "~60 B per entry" is not a bound at all.
        assert "\n" not in line

    def test_the_count_NAMES_the_section_it_counts(self, store: Path) -> None:
        """⚠ It was `N bullets`, which reads as ENTRY SIZE to anyone scanning the
        index — the count is `## Nuance / work-history` bullets ONLY, so an entry
        with 5 pointers and 7 nuance bullets showed `7 bullets` and has 12. The
        word now names the section; the ambiguous one must not come back."""
        rep = rc.recall(store, SCOPE, mode="list")
        # The ROWS, not the whole block: the caveat legitimately uses the word
        # "bullet" in prose ("a bullet may describe a gotcha already fixed"), and
        # a whole-text grep would be asserting about the wrong lines — the
        # spelled-vs-structural trap in `claude/RULES.md`.
        rows = [rc.listing_line(e, 12) for e in rep.listing]
        assert rows
        for row in rows:
            assert " nuance " in row, row
            assert "bullet" not in row, (
                "the index row says `bullets` again — it counts only the nuance "
                "section, and that word reads as entry size"
            )

    def test_the_count_is_the_NUANCE_section_and_not_the_whole_entry(
        self, tmp_path: Path
    ) -> None:
        """The claim the label now makes, measured: an entry with bullets under
        BOTH surfaced headings reports only the nuance ones."""
        store = tmp_path / "s"
        (store / SCOPE).mkdir(parents=True)
        (store / SCOPE / "collector.md").write_text(
            _entry(
                "collector",
                SCOPE,
                pointers="- p1\n- p2\n- p3\n- p4\n- p5",
                nuance="\n".join(f"- 2026-02-{i:02d}: n{i}." for i in range(1, 8)),
            ),
            encoding="utf-8",
        )
        row = rc.recall(store, SCOPE, mode="list").listing[0]
        assert row.bullet_count == 7, "not the nuance count"
        assert "7 nuance" in rc.listing_line(row, 12)

    def test_the_sensitivity_on_the_line_is_the_FAIL_SAFE_one(self, store: Path) -> None:
        rep = rc.recall(store, SCOPE, mode="list")
        by_ref = {e.ref: e for e in rep.listing}
        text = rc.render_text(rep)
        assert by_ref["collector"].sensitivity == "client-confidential"
        # The RENDERED line, matched by its parts rather than by a width this
        # test would have to re-derive — a second copy of the layout rule is how
        # a display test starts asserting its own arithmetic.
        line = next(l for l in text.splitlines() if l.strip().startswith("collector "))
        assert line.endswith("client-confidential")
        assert "1 nuance" in line

    def test_list_mode_says_it_printed_no_bodies(self, big_store: Path) -> None:
        """A bodyless index must not read like an empty scope."""
        text = rc.render_text(rc.recall(big_store, BIG_SCOPE, mode="list"))
        assert "NO ENTRY BODIES WERE PRINTED" in text
        assert "this is not an empty scope" in text
        assert "NOTHING RECORDED YET" not in text
        assert "- pointer for widget-01" not in text

    def test_the_bullet_count_is_the_RESOLVERS(self, store: Path) -> None:
        """No second bullet parser: the count must equal what the resolver's own
        `parse_journal_bullets` says about the same body."""
        rep = rc.recall(store, SCOPE, mode="list")
        for e in rep.listing:
            expected = len(sr.parse_journal_bullets(e.sections.get(rc.NUANCE_HEADING, "")))
            assert e.bullet_count == expected, e.ref


# =============================================================================
# THE INDEX PAGE CAP — loud, ordered, and reachable.
# =============================================================================
#
# 🔴 WHAT THE REQUIREMENT IS. The index printed EVERY entry, so its cost grew
# with the store forever. It is now capped at `LISTING_PAGE_SIZE` lines with the
# remainder counted and `--page N` reaching it. The two ways to get this wrong
# are (a) dropping the remainder silently and (b) capping an order in which the
# cut is meaningless — so the tests below pin the notice AND the order.

PAGED_SCOPE = "many-gizmos"
MULTI_SCOPE = PAGED_SCOPE
PAGED_N = rc.LISTING_PAGE_SIZE + 5


def _make_paged_store(root: Path, n: int = PAGED_N) -> Path:
    """More entries than one page holds, with mtime PINNED ascending by index.

    Pinned rather than left to the loop's speed: the index is newest-first by
    mtime, so an order derived from how fast the files were written would be a
    test asserting its own timing. `gizmo-001` is therefore always the OLDEST and
    always the last row of the last page, which is what lets a test ask "did this
    page claim more entries follow the oldest one?".

    `n` is a parameter because the page-cap tests need a 2-page store and the
    every-page-describes-itself tests need a 3-page one — a middle page is the
    only shape that can tell "print nothing on the LAST page" apart from the
    wrong fix, "print nothing after page 1"."""
    store = root / f"paged-store-{n}"
    d = store / PAGED_SCOPE
    d.mkdir(parents=True)
    for i in range(1, n + 1):
        slug = f"gizmo-{i:03d}"
        path = d / f"{slug}.md"
        path.write_text(
            _entry(slug, PAGED_SCOPE, pointers=f"- pointer for {slug}", nuance=None),
            encoding="utf-8",
        )
        __import__("os").utime(path, (1_700_000_000 + i, 1_700_000_000 + i))
    return store


@pytest.fixture()
def paged_store(tmp_path: Path) -> Path:
    return _make_paged_store(tmp_path)


class TestTheIndexPageCap:
    """🔴 RED AT origin/main ON THE ASSERTION: the index printed all 105 lines."""

    def test_a_page_holds_at_most_LISTING_PAGE_SIZE_lines(self, paged_store: Path) -> None:
        rep = rc.recall(paged_store, PAGED_SCOPE)
        # 🔴 THE BEHAVIOURAL ASSERTION FIRST, spelled against attributes that
        # existed BEFORE this change. A test whose first failure is
        # `AttributeError: no attribute 'listing_total'` proves the surface is
        # new, not that a behaviour regressed — so the claim "the index used to
        # print every entry" is made where it can actually go red at the base ref.
        assert len(rep.listing) < rep.total_in_scope, (
            "the index printed every one of the scope's entries — it is uncapped"
        )
        assert len(rep.listing) == rc.LISTING_PAGE_SIZE
        assert rep.listing_total == PAGED_N
        assert (rep.listing_page, rep.listing_pages) == (1, 2)

    def test_the_remainder_is_COUNTED_and_the_flag_NAMED(self, paged_store: Path) -> None:
        """The repo's hard rule: a truncation is never silent. The notice has to
        carry the number AND the command that reaches it, or it is an apology
        rather than an escape hatch."""
        text = rc.render_text(rc.recall(paged_store, PAGED_SCOPE))
        # Behavioural first again — no new symbol in this assertion, so it goes
        # red at the base ref on the missing NOTICE rather than on a missing name.
        assert "NOT LISTED on this page" in text
        assert "Nothing is hidden and nothing was filtered" in text
        assert "`--page 2`" in text
        assert f"{PAGED_N - rc.LISTING_PAGE_SIZE} more entries NOT LISTED" in text

    def test_page_two_holds_the_REST_and_the_two_pages_are_disjoint(
        self, paged_store: Path
    ) -> None:
        """Positive control on the pagination itself: page 2 must be non-empty
        and must not repeat page 1, or "the rest is one flag away" is a claim
        with nothing behind it."""
        one = [e.ref for e in rc.recall(paged_store, PAGED_SCOPE, page=1).listing]
        two = [e.ref for e in rc.recall(paged_store, PAGED_SCOPE, page=2).listing]
        assert len(two) == PAGED_N - rc.LISTING_PAGE_SIZE
        assert not set(one) & set(two)
        assert len(set(one) | set(two)) == PAGED_N

    def test_the_order_is_NEWEST_FIRST_and_the_output_SAYS_so(
        self, paged_store: Path
    ) -> None:
        """🔴 The order is what makes the cut mean something. Alphabetically,
        `gizmo-105` would be dropped for being late in the alphabet; by mtime the
        page-2 entries are the STALE ones, which is the only cut a resuming
        session can reason about — so the header states the rule rather than
        leaving the reader to infer it."""
        rep = rc.recall(paged_store, PAGED_SCOPE)
        assert [e.ref for e in rep.listing][0] == f"gizmo-{PAGED_N:03d}"
        assert [e.ref for e in rep.listing][-1] == "gizmo-006"
        assert "newest-first by file mtime" in rc.render_text(rep)
        assert [e.ref for e in rc.recall(paged_store, PAGED_SCOPE, page=2).listing][-1] == (
            "gizmo-001"
        )

    def test_a_page_past_the_END_says_so_and_names_the_RANGE(
        self, paged_store: Path
    ) -> None:
        """🔴 It does NOT clamp to the last page. Clamping answers a question the
        caller did not ask under a heading claiming otherwise; and "nothing on
        this page" must not be readable as "nothing on record"."""
        rep = rc.recall(paged_store, PAGED_SCOPE, page=9)
        assert rep.listing == ()
        assert rep.listing_total == PAGED_N
        text = rc.render_text(rep)
        assert "PAGE 9 IS PAST THE END" in text
        assert "nothing is missing from the store" in text
        assert "`--page 1` … `--page 2`" in text
        assert "NOTHING RECORDED YET" not in text

    def test_a_scope_that_FITS_keeps_the_pre_cap_wording(self, big_store: Path) -> None:
        """A cap that changed the output of every scope that never hits it would
        train the reader to skim past the page notice on the day it matters."""
        text = rc.render_text(rc.recall(big_store, BIG_SCOPE))
        assert f"ALL {BIG_N} entries" in text
        assert "none omitted" in text
        assert "NOT LISTED on this page" not in text

    def test_the_cap_does_not_reach_total_in_scope_or_the_digest_pick(
        self, paged_store: Path
    ) -> None:
        """It is a LISTING cap, not a selection rule: the counts the digest
        reports are still counts of the whole scope."""
        rep = rc.recall(paged_store, PAGED_SCOPE)
        assert rep.total_in_scope == PAGED_N
        assert rep.omitted == PAGED_N - 1
        assert len(rep.entries) == 1

    def test_list_mode_paginates_the_same_way(self, paged_store: Path) -> None:
        rep = rc.recall(paged_store, PAGED_SCOPE, mode="list", page=2)
        assert len(rep.listing) == PAGED_N - rc.LISTING_PAGE_SIZE
        assert "NO ENTRY BODIES WERE PRINTED" in rc.render_text(rep)

    def test_a_nonsense_page_RAISES_with_its_OWN_sentinel(self, store: Path) -> None:
        for bad in (0, -1, True, 2.5, "3"):
            with pytest.raises(ValueError) as exc:
                rc.recall(store, SCOPE, page=bad)
            assert "page must be an int >= 1" in str(exc.value)
            assert "limit must be an int" not in str(exc.value), "a neighbour's sentinel"

    def test_the_pagination_facts_are_in_the_JSON(self, paged_store: Path) -> None:
        blob = rc.report_json(rc.recall(paged_store, PAGED_SCOPE, mode="list", page=2))
        assert blob["listing_total"] == PAGED_N
        assert blob["listing_pages"] == 2
        assert blob["listing_page"] == 2
        assert blob["listing_page_size"] == rc.LISTING_PAGE_SIZE
        assert blob["listing_order"] == "mtime-desc,ref-asc"
        assert len(blob["listing"]) == PAGED_N - rc.LISTING_PAGE_SIZE

    def test_listing_page_is_a_PURE_function_of_its_inputs(self, paged_store: Path) -> None:
        """Called directly, so the page arithmetic is pinned without a store —
        `pages` is a ceiling and an empty index is still "1 of 1", never "of 0"."""
        entries = rc.recall(paged_store, PAGED_SCOPE, mode="list").listing
        assert rc.listing_page((), 1) == ((), 1)
        assert len(rc.listing_page(entries, 1)[0]) == len(entries)
        assert rc.listing_page(entries, 2)[0] == ()


# =============================================================================
# 🔴 EVERY PAGE AFTER THE FIRST — the three defects review found on PR #442.
# =============================================================================
#
# WHAT GOT THROUGH, AND WHY. The page-cap tests above all exercised page 1, or
# checked page 2's CONTENT (`listing`) without ever RENDERING it. The live
# store's largest scope holds 25 entries — one page — so nothing in the repo or
# in a live probe could surface any of this. All three defects are the same
# class: a line that describes the SCOPE while sitting under a header that
# describes the PAGE.
#
#   1. the truncation notice fired on "this page is not the whole index" (TRUE
#      on the last page), announcing page 1's 100 entries as unseen and routing
#      to a `--page 3` that does not exist — a FALSE truncation notice, which
#      this repo treats as worse than no notice, contradicting the correct
#      header on the line directly above it.
#   2. the header computed its range unconditionally: `entries 801–800 of 150
#      … (page 9 of 2)`.
#   3. `--list`'s closing line asserted "the 150 entries above are the complete
#      index" on a page that printed 100, and on one that printed none.
#
# These are rendered-TEXT defects, so every test here asserts on rendered text.
# `TestTheIndexPageCap` above asserts mostly on the report object, which is
# exactly why it stayed green.

MULTI_N = 150
"""Three pages' worth would be better still, but 150 is what review measured
and reproducing the reported numbers exactly is worth more than a rounder
fixture. `test_a_MIDDLE_page_still_announces_what_follows` covers the >2-page
shape, which is the only thing 150 cannot show."""


@pytest.fixture()
def multi_store(tmp_path: Path) -> Path:
    return _make_paged_store(tmp_path, n=MULTI_N)


class TestEveryPageDescribesITSELF:
    """Each test names the wrong string it would print, so a regression cannot
    be satisfied by the absence of some other line."""

    def test_the_LAST_page_prints_NO_truncation_notice(self, multi_store: Path) -> None:
        """🔴 BLOCKER 1. On page 2 of 2 there is nothing after this page, so
        there is nothing to announce. The notice printed anyway."""
        text = rc.render_text(rc.recall(multi_store, MULTI_SCOPE, mode="list", page=2))
        assert "NOT LISTED on this page" not in text, (
            "the last page announced a truncation that is not there"
        )
        assert "Nothing is hidden and nothing was filtered" not in text
        # …and the header it used to contradict is still correct and still there.
        assert f"entries 101–{MULTI_N} of {MULTI_N}" in text
        assert "(page 2 of 2)" in text

    def test_the_last_page_NEVER_routes_to_a_page_that_does_not_exist(
        self, multi_store: Path
    ) -> None:
        """The specific wrong string: `--page 3` on a two-page index."""
        text = rc.render_text(rc.recall(multi_store, MULTI_SCOPE, mode="list", page=2))
        assert "`--page 3`" not in text, "the last page routed past the end"
        rep = rc.recall(multi_store, MULTI_SCOPE, mode="list", page=2)
        assert rep.listing_after_page == 0

    def test_the_count_is_what_comes_AFTER_this_page_not_what_is_off_it(
        self, multi_store: Path
    ) -> None:
        """🔴 The arithmetic itself. Page 1 of 2 has 50 after it, not 100 — and
        the old predicate would have said 100 here too if the numbers had lined
        up differently, so both pages are asserted."""
        one = rc.recall(multi_store, MULTI_SCOPE, mode="list", page=1)
        two = rc.recall(multi_store, MULTI_SCOPE, mode="list", page=2)
        assert (one.listing_before_page, one.listing_after_page) == (0, 50)
        assert (two.listing_before_page, two.listing_after_page) == (100, 0)
        assert "50 more entries NOT LISTED" in rc.render_text(one)
        assert "100 more entries NOT LISTED" not in rc.render_text(two)

    def test_a_MIDDLE_page_still_announces_what_follows(self, tmp_path: Path) -> None:
        """🔴 REACHABILITY, and the control on the fix. "Print nothing on the
        last page" must not become "print nothing after page 1" — so a THREE-page
        index is checked at every page. Without this, gating the notice on
        `page == 1` would pass every other test in this class."""
        store = _make_paged_store(tmp_path, n=2 * rc.LISTING_PAGE_SIZE + 7)
        expected = {1: 107, 2: 7, 3: 0}
        for page, after in expected.items():
            rep = rc.recall(store, MULTI_SCOPE, mode="list", page=page)
            text = rc.render_text(rep)
            assert rep.listing_after_page == after, page
            if after:
                assert f"{after} more entries NOT LISTED" in text, page
                assert f"`--page {page + 1}`" in text, page
            else:
                assert "NOT LISTED on this page" not in text, page

    def test_page_1_of_a_capped_index_is_UNCHANGED(self, multi_store: Path) -> None:
        """🔴 The loud truncation on page 1 was correct and must stay exactly as
        loud — the fix narrows WHEN the notice fires, never what it says."""
        text = rc.render_text(rc.recall(multi_store, MULTI_SCOPE, mode="list", page=1))
        assert f"entries 1–100 of {MULTI_N}" in text
        assert "newest-first by file mtime (page 1 of 2)" in text
        assert "50 more entries NOT LISTED on this page" in text
        assert "capped at 100 lines per page" in text
        assert "Nothing is hidden and nothing was filtered" in text
        assert "`--page 2` lists the rest, oldest last" in text

    def test_a_page_past_the_end_prints_NO_inverted_range(
        self, multi_store: Path
    ) -> None:
        """🔴 BLOCKER 2. The header did unguarded arithmetic and printed
        `entries 801–800 of 150 … (page 9 of 2)`."""
        text = rc.render_text(rc.recall(multi_store, MULTI_SCOPE, mode="list", page=9))
        assert "801–800" not in text, "the header inverted its own range"
        assert "(page 9 of 2)" not in text, "the header claimed page 9 of 2"
        assert "entries 801" not in text
        # It says what it is instead, and the correct guidance line is untouched.
        assert "page 9 is past the end" in text
        assert "PAGE 9 IS PAST THE END" in text
        assert "`--page 1` … `--page 2`" in text

    def test_no_header_ever_prints_a_range_it_did_not_list(
        self, multi_store: Path
    ) -> None:
        """The general form, swept across pages rather than asserted at one — a
        range in the header must count exactly the rows under it."""
        for page in (1, 2, 3, 9, 40):
            rep = rc.recall(multi_store, MULTI_SCOPE, mode="list", page=page)
            head = next(
                l for l in rc.render_text(rep).splitlines() if l.startswith("INDEX")
            )
            rows = [l for l in rc.render_text(rep).splitlines() if l.startswith("  gizmo-")]
            assert len(rows) == len(rep.listing), page
            if "entries " in head and "–" in head:
                lo, hi = head.split("entries ")[1].split(" of ")[0].split("–")
                assert int(hi) - int(lo) + 1 == len(rows), (page, head)
            else:
                assert rows == [], (page, head)

    def test_the_list_completeness_line_describes_THIS_PAGE(
        self, multi_store: Path
    ) -> None:
        """🔴 BLOCKER 3. It claimed "the 150 entries above are the complete
        index" on a page that printed 100."""
        text = rc.render_text(rc.recall(multi_store, MULTI_SCOPE, mode="list", page=1))
        assert f"The {MULTI_N} entries above are the complete index" not in text, (
            "the closing line claimed a completeness the page does not have"
        )
        assert "The 100 entries above are page 1 of 2" in text
        assert f"holds {MULTI_N} in all" in text
        # The half of the sentence that was always true stays true.
        assert "NO ENTRY BODIES WERE PRINTED" in text
        assert "this is not an empty scope" in text

    def test_the_completeness_line_past_the_end_is_not_self_contradictory(
        self, multi_store: Path
    ) -> None:
        """Zero entries were above it, so it may not describe any."""
        text = rc.render_text(rc.recall(multi_store, MULTI_SCOPE, mode="list", page=9))
        assert "entries above" not in text, "it described rows it never printed"
        assert "NO entries were listed" in text
        assert "NO ENTRY BODIES WERE PRINTED" in text
        assert "this is not an empty scope" in text
        assert "NOTHING RECORDED YET" not in text

    def test_a_SINGLE_page_scope_keeps_the_complete_index_claim(
        self, big_store: Path
    ) -> None:
        """🔴 The control on blocker 3: the claim is TRUE when the page is the
        whole index, and weakening it everywhere would train the reader to skim
        past the page wording on the day it matters."""
        text = rc.render_text(rc.recall(big_store, BIG_SCOPE, mode="list"))
        assert f"The {BIG_N} entries above are the complete index" in text
        assert "are page 1 of" not in text

    def test_the_digest_pages_the_same_way(self, multi_store: Path) -> None:
        """The default mode, not only `--list` — the three lines are shared and
        a fix that only reached `--list` would leave the digest wrong."""
        text = rc.render_text(rc.recall(multi_store, MULTI_SCOPE, page=2))
        assert "NOT LISTED on this page" not in text
        assert "`--page 3`" not in text
        assert f"entries 101–{MULTI_N} of {MULTI_N}" in text
        # …and the digest's own not-shown notice (about BODIES) is untouched.
        assert "LISTED ABOVE but NOT shown in full" in text

    def test_no_page_CONTRADICTS_its_own_header(self, multi_store: Path) -> None:
        """🔴 THE INVARIANT UNDERNEATH ALL THREE, swept rather than spot-checked:
        a page that says it holds the last entry may not also say entries remain.
        This is the assertion that would have caught the original defect without
        anyone having thought of the last page specifically."""
        # 🔴 THE BEHAVIOURAL SWEEP RUNS FIRST AND COMPLETES, spelled against
        # nothing this PR introduced. Interleaved with the property check below it
        # died on `AttributeError: listing_after_page` at page 1 and never reached
        # page 2 — where the contradiction actually lives — so the red it produced
        # said "the surface is new" instead of "the output is wrong".
        for page in (1, 2, 3, 9):
            rep = rc.recall(multi_store, MULTI_SCOPE, mode="list", page=page)
            text = rc.render_text(rep)
            says_more = "NOT LISTED on this page" in text
            last_row_is_oldest = bool(rep.listing) and rep.listing[-1].ref == "gizmo-001"
            assert not (says_more and last_row_is_oldest), (
                f"page {page} listed the OLDEST entry and still claimed more follow it"
            )
        for page in (1, 2, 3, 9):
            rep = rc.recall(multi_store, MULTI_SCOPE, mode="list", page=page)
            says_more = "NOT LISTED on this page" in rc.render_text(rep)
            assert says_more == (rep.listing_after_page > 0), page


# =============================================================================
# WHICH ENTRY IS FEATURED, AND WHY — never an implicit pick.
# =============================================================================


class TestFeaturedSelection:
    """🔴 The basis is not decoration. An entry printed first with no stated
    reason reads as "the tool thinks this matters", which is a claim the store
    cannot support. Both selectors name themselves."""

    def test_the_resolver_selector_fires_and_SAYS_it_did(self, big_store: Path) -> None:
        rep = rc.recall(
            big_store,
            BIG_SCOPE,
            focus_paths=[f"apps/{FOCUS_SLUG}/values.yaml", f"apps/{FOCUS_SLUG}/kustomization.yaml"],
            focus_source="claudedocs/handoff-invented.md",
        )
        assert [e.ref for e in rep.entries] == [FOCUS_SLUG]
        assert rep.featured_basis is not None
        assert rep.featured_basis.startswith("resolved via claudedocs/handoff-invented.md")
        assert rep.featured_basis in rc.render_text(rep)

    def test_the_mtime_fallback_fires_and_SAYS_it_did(self, big_store: Path) -> None:
        # Far-future, so the pick cannot be an accident of the order the fixture
        # happened to write the files in (`widget-15` is newest by default).
        newest = big_store / BIG_SCOPE / "widget-03.md"
        __import__("os").utime(newest, (2 * 10**9, 2 * 10**9))
        rep = rc.recall(big_store, BIG_SCOPE)
        assert [e.ref for e in rep.entries] == ["widget-03"]
        assert rep.featured_basis.startswith("most-recent fallback")
        assert rep.featured_basis in rc.render_text(rep)

    def test_a_path_window_that_resolves_to_NOTHING_falls_back_and_says_so(
        self, big_store: Path
    ) -> None:
        """🔴 The two zeros again: "there was no window" and "the window matched
        nothing" are different facts and the basis distinguishes them, so an
        agent can tell a missing handoff from an unrelated one."""
        no_window = rc.recall(big_store, BIG_SCOPE).featured_basis
        dud = rc.recall(
            big_store,
            BIG_SCOPE,
            focus_paths=["some/unrelated/thing.yaml"],
            focus_source="claudedocs/handoff-invented.md",
        ).featured_basis
        assert "no handoff doc" in no_window
        assert "nothing quoted in claudedocs/handoff-invented.md resolved" in dud
        assert no_window != dud

    def test_more_matching_paths_WINS(self, big_store: Path) -> None:
        rep = rc.recall(
            big_store,
            BIG_SCOPE,
            focus_paths=[
                "apps/widget-02/values.yaml",
                "apps/widget-09/a.yaml",
                "apps/widget-09/b.yaml",
                "apps/widget-09/c.yaml",
            ],
            focus_source="doc.md",
        )
        assert [e.ref for e in rep.entries] == ["widget-09"]

    def test_selection_does_not_reimplement_the_matcher(self) -> None:
        """It goes through `associate_paths`, the WRITER's own path→subsystem
        matcher. A second matcher here could drift from the one the store's
        aliases were curated against."""
        src = MODULE_PATH.read_text(encoding="utf-8")
        assert "associate_paths(" in src
        for forbidden in ("def associate_paths", "def path_refs", "def resolve_ref_tiered"):
            assert forbidden not in src

    def test_an_alias_reaches_the_entry(self, store: Path) -> None:
        """The resolver's alias tier, exercised through the featured pick — the
        reader adds no matching of its own, so if aliases work here they work
        because `associate_paths` made them."""
        rep = rc.recall(
            store,
            SCOPE,
            focus_paths=["nix/event_tap/values.yaml"],
            focus_source="doc.md",
        )
        assert [e.ref for e in rep.entries] == ["collector"]

    def test_an_ambiguous_ref_in_the_window_never_features_anything(
        self, store: Path
    ) -> None:
        """`weekly-digest` names two entries. The resolver refuses to pick and so
        does this: the run falls back rather than featuring a coin-flip."""
        rep = rc.recall(
            store, SCOPE, focus_paths=["nix/weekly-digest/values.yaml"], focus_source="doc.md"
        )
        assert rep.featured_basis.startswith("most-recent fallback")

    def test_the_pick_is_DETERMINISTIC_across_runs(self, big_store: Path) -> None:
        a = rc.render_text(rc.recall(big_store, BIG_SCOPE))
        b = rc.render_text(rc.recall(big_store, BIG_SCOPE))
        assert a == b

    def test_the_featured_entry_is_ALSO_in_the_index(self, big_store: Path) -> None:
        """Or the counts stop adding up: "1 of 15" plus a 15-line index has to
        describe 15 entries, not 16."""
        rep = rc.recall(big_store, BIG_SCOPE)
        assert rep.entries[0].ref in {e.ref for e in rep.listing}
        assert len(rep.listing) == rep.total_in_scope


# =============================================================================
# SEARCH — read by MATCH, gated by a SCORED FIXTURE SET rather than by taste.
# =============================================================================
#
# 🔴 WHY A LABELLED CORPUS AND NOT SPOT CHECKS. A fuzzy searcher that ranks noise
# above signal is a confident-wrong instrument: it always returns something, and
# the something looks authoritative because it carries a score. Spot-checking one
# query proves a scorer works for that query — a prototype round produced four
# scorers, EACH of which fixed one query and broke another. So the scorer is
# judged by precision@1 and recall over a labelled set that covers every match
# CLASS, and any future change to the constants is judged the same way.
#
# 🔴 EVERY FIXTURE HERE IS INVENTED. The real store is client-confidential, this
# repo is PUBLIC, and a fixture copied from a real entry would leak on the day
# somebody greps for a hostname.

SEARCH_SCOPE = "gadget-fleet"


def _make_search_store(root: Path) -> Path:
    """A synthetic scope built so each labelled query has ONE right answer.

    The distractors are the point: `mailer` mentions `blob-vault` in passing so
    the "several entries, one clearly most relevant" case has something to be
    more relevant THAN, and `ledger-db`'s pointers say `pooler` so the typo query
    has a near-miss inside the very entry it must land in."""
    store = root / "search-store"
    d = store / SEARCH_SCOPE
    d.mkdir(parents=True)

    def w(name: str, **kw) -> None:
        (d / f"{name}.md").write_text(_entry(name, SEARCH_SCOPE, **kw), encoding="utf-8")

    w(
        "blob-vault",
        aliases=["object-cellar"],
        pointers="- ops skill `manage-blob-vault` — tenant standup and migration.",
        nuance=(
            "- 2026-03-01: the compaction pass stalls once the spill pool is full.\n"
            "- 2026-03-04: the `podman` shim is only used by the offline importer."
        ),
    )
    # The COMPOUND target: the corpus writes `rate-limit`, a caller types `ratelimit`.
    w(
        "edge-proxy",
        pointers="- config lives in `nix/edge-proxy/`, one file per listener.",
        nuance=(
            "- 2026-03-02: the rate-limit cap is GLOBAL rather than per-client, because\n"
            "  the balancer rewrites every source address to one."
        ),
    )
    # The TYPO target, with a near-miss (`pooler`) in its own other section.
    w(
        "ledger-db",
        pointers="- the pooler runs in transaction mode.",
        nuance="- 2026-03-03: the connection pool exhausts at 40, not the documented 200.",
    )
    # The DISTRACTOR: names another entry in passing, at the same score, AND
    # carries a bare `rate` so the compound query has something to out-rank —
    # `ratelimit` reaches `rate` through the prefix rule whether or not the
    # concatenation exists, so a compound test with no distractor would pass on a
    # scorer that never joins anything.
    w(
        "mailer",
        pointers="- templates are rendered ahead of send.",
        nuance=(
            "- 2026-03-06: bounces are archived into blob-vault once a day.\n"
            "- 2026-03-09: the send rate is sampled every minute."
        ),
    )
    # The SHORT-PARTS compound: `k8s` and `api` are both below MIN_INEXACT_LEN, so
    # nothing but the concatenation can reach `k8sapi` from here.
    w(
        "beacon",
        pointers="- the k8s api server is polled every 30s.",
        nuance="- 2026-03-10: the poll interval is not configurable.",
    )
    # The MULTI-LINE BLOCK target: the two query words are on different LINES of
    # one bullet, so a per-line scorer scores each half at 0.50 and returns
    # nothing.
    w(
        "quill",
        pointers="- one worker per queue.",
        nuance=(
            "- 2026-03-05: the quill worker drops a job when the\n"
            "  scheduler restarts mid-batch."
        ),
    )
    # The NAME-ONLY target: nothing in the body ever says `zephyr`.
    w(
        "zephyr-cache",
        pointers="- warm it before a cutover.",
        nuance="- 2026-03-07: eviction is size-based, not time-based.",
    )
    # The FENCE: `-` lines inside a code block are not bullets.
    w(
        "chart-render",
        pointers="- render happens in the sidecar.",
        nuance="- 2026-03-08: the sidecar reads its flags from a file.",
        extra_body="## Recipe\n```sh\nrenderctl run \\\n  --format svg \\\n  --strict\n```",
    )
    return store


@pytest.fixture()
def search_store(tmp_path: Path) -> Path:
    return _make_search_store(tmp_path)


# (query, expected ref at rank 1, a substring the winning hunk MUST contain, class)
# `None` expectations are the negative control: the query must match NOTHING.
SEARCH_FIXTURES: list[tuple[str, str | None, str, str]] = [
    ("compaction", "blob-vault", "compaction pass stalls", "exact"),
    ("conection pool", "ledger-db", "connection pool exhausts", "typo"),
    ("ratelimit", "edge-proxy", "rate-limit cap is GLOBAL", "compound"),
    ("k8sapi", "beacon", "k8s api server", "compound-short-parts"),
    ("quill scheduler", "quill", "scheduler restarts mid-batch", "multi-token-across-lines"),
    ("blob-vault", "blob-vault", "manage-blob-vault", "several-entries-one-relevant"),
    ("zephyr", "zephyr-cache", "", "entry-name-only"),
    ("kryptonite", None, "", "absent-term"),
]


class TestSearchFixtureCorpus:
    """🔴 THE SCORER'S GATE. Precision@1 and recall over the labelled set above.

    Every constant in `subsystem_recall`'s search section — the threshold, the
    fuzzy floor, the minimum inexact length, the prefix/substring strengths — is
    answerable here and nowhere else. A future change to any of them is a change
    to these numbers, which is the point: the alternative is a scorer tuned by
    whoever last looked at one query's output."""

    @pytest.mark.parametrize(
        "query,expected,substring,klass",
        SEARCH_FIXTURES,
        ids=[k for _, _, _, k in SEARCH_FIXTURES],
    )
    def test_precision_at_1(
        self, search_store: Path, query, expected, substring, klass
    ) -> None:
        rep = rc.search(search_store, SEARCH_SCOPE, query)
        if expected is None:
            assert rep.hunks == (), f"{klass}: an absent term returned {len(rep.hunks)} hunks"
            assert rep.status == "search-no-match"
            return
        assert rep.hunks, f"{klass}: {query!r} returned NOTHING — recall failure"
        assert rep.status == "search-hit"
        top = rep.hunks[0]
        assert top.ref == expected, (
            f"{klass}: rank 1 is `{top.ref}` at {top.score:.2f}, expected `{expected}`. "
            f"Ranking noise above signal is the failure this gate exists for."
        )
        if substring:
            assert substring in "\n".join(top.lines), f"{klass}: wrong hunk within the entry"

    def test_the_whole_set_scores_and_the_PAIR_is_reported(
        self, search_store: Path
    ) -> None:
        """🔴 THE CONTROL PAIR, REPORTED TOGETHER. `claude/RULES.md`: a reassuring
        zero is indistinguishable from a harness wired to nothing, so the positive
        count and the negative zero are asserted in ONE place and the message
        carries both."""
        labelled = [f for f in SEARCH_FIXTURES if f[1] is not None]
        hits = {q: rc.search(search_store, SEARCH_SCOPE, q).hunks for q, _, _, _ in labelled}
        negative = rc.search(search_store, SEARCH_SCOPE, "kryptonite").hunks

        recalled = sum(1 for v in hits.values() if v)
        correct = sum(
            1
            for (q, exp, _, _) in labelled
            if hits[q] and hits[q][0].ref == exp
        )
        pair = (
            f"positive control: {recalled}/{len(labelled)} labelled queries returned a "
            f"hunk, {correct}/{len(labelled)} correct at rank 1; "
            f"negative control: {len(negative)} hunks for an absent term"
        )
        assert recalled == len(labelled), pair
        assert correct == len(labelled), pair
        assert len(negative) == 0, pair

    def test_a_per_LINE_scorer_would_MISS_the_multi_line_case(
        self, search_store: Path
    ) -> None:
        """🔴 REACHABILITY FOR THE BLOCK UNIT. `quill scheduler` passing proves
        nothing unless the LINES it spans genuinely fail on their own — otherwise
        the block unit is untested and could be deleted with the suite green."""
        q = rc.tokenize("quill scheduler")
        line_a = "- 2026-03-05: the quill worker drops a job when the"
        line_b = "  scheduler restarts mid-batch."
        assert rc.score_unit(q, line_a) < rc.DEFAULT_SEARCH_THRESHOLD
        assert rc.score_unit(q, line_b) < rc.DEFAULT_SEARCH_THRESHOLD
        assert rc.score_unit(q, line_a + "\n" + line_b) >= rc.DEFAULT_SEARCH_THRESHOLD

    def test_the_name_only_hit_SAYS_it_is_one(self, search_store: Path) -> None:
        """A hunk shown because the ENTRY was named must not read as a hunk whose
        LINES matched — the basis is the only thing that can tell them apart."""
        top = rc.search(search_store, SEARCH_SCOPE, "zephyr").hunks[0]
        assert top.basis == "entry-name"
        assert "zephyr" not in "\n".join(top.lines).lower()
        assert rc.search(search_store, SEARCH_SCOPE, "compaction").hunks[0].basis == "line"

    def test_the_relevant_entry_WINS_a_tie_it_would_lose_alphabetically(
        self, search_store: Path
    ) -> None:
        """🔴 THE TIE-BREAK, PROVEN REACHABLE. `blob-vault` and `mailer` both hit
        `blob-vault` at 1.00, and `blob-vault` < `mailer` alphabetically — so this
        test would pass on a broken tie-break by luck. The kill is asserted on the
        SCORES being equal AND on `name_score` being what separates them."""
        hunks = rc.search(search_store, SEARCH_SCOPE, "blob-vault").hunks
        refs = [h.ref for h in hunks]
        assert refs[0] == "blob-vault"
        assert "mailer" in refs, "the distractor stopped matching — the tie is untested"
        top, other = hunks[0], next(h for h in hunks if h.ref == "mailer")
        assert top.score == other.score, "no tie left to break"
        assert top.name_score > other.name_score
        assert other.name_score == 0.0

    def test_the_name_score_is_NOT_folded_into_the_printed_score(
        self, search_store: Path
    ) -> None:
        """Or the number on the screen would be one nothing else explains."""
        for h in rc.search(search_store, SEARCH_SCOPE, "blob-vault").hunks:
            assert 0.0 <= h.score <= 1.0


class TestSearchScorer:
    """The scorer's rungs, pinned individually — a coverage mean is not readable
    from its output alone, so each rule is asserted where it fires."""

    def test_tokenization_folds_every_punctuation_spelling(self) -> None:
        for spelling in ("rate-limit", "rate_limit", "rate limit", "RATE.LIMIT"):
            assert rc.tokenize(spelling) == ("rate", "limit"), spelling

    def test_the_concatenation_is_what_makes_a_COMPOUND_query_exact(self) -> None:
        """🔴 Deliberate, and not a lowered cutoff. TWO things it buys, both
        measured below:

          1. REACH FOR THE WHOLE COMPOUND. The inexact rungs are DIRECTIONAL, so
             `rate` — a FRAGMENT of the query — scores 0.00, and the
             concatenation is the only thing that can match a corpus writing
             `rate-limit` at all. (It used to be phrased as "the joined form
             out-RANKS the bare half"; the bare half scored 0.92 and had no
             business on the screen. See `TestSearchDirectionality`.)
          2. REACH WHEN THE PARTS ARE SHORT. When a compound's parts are below
             `MIN_INEXACT_LEN` (`k8s`+`api`) every other rung is closed to a
             3-character token, join or no join.
        """
        cands = rc.candidate_tokens("the rate-limit cap")
        assert "ratelimit" in cands
        assert rc.pair_strength("ratelimit", "ratelimit") == 1.0
        # (1) the bare halves are FRAGMENTS of the query and no longer score.
        assert rc.pair_strength("ratelimit", "rate") == 0.0
        assert rc.pair_strength("ratelimit", "limit") == 0.0
        assert rc.score_unit(rc.tokenize("ratelimit"), "the rate-limit cap") == 1.0
        # (2) the reach case: nothing but the join gets there.
        short = "the k8s api server"
        assert all(rc.pair_strength("k8sapi", t) == 0.0 for t in rc.tokenize(short))
        assert "k8sapi" in rc.candidate_tokens(short)
        assert rc.score_unit(rc.tokenize("k8sapi"), short) == 1.0

    def test_the_compound_hit_is_the_ONLY_hit_the_bare_half_is_GONE(
        self, search_store: Path
    ) -> None:
        """End to end, with the distractor still in the store. `mailer` carries a
        bare `rate`; under the old symmetric prefix rule it rode along at 0.92
        for a query it does not contain. It must now be absent — and the fixture
        is still a genuine kill for the join, because with the join removed
        `ratelimit` reaches NOTHING (asserted in the mutation matrix)."""
        hunks = rc.search(search_store, SEARCH_SCOPE, "ratelimit").hunks
        assert hunks[0].ref == "edge-proxy"
        assert hunks[0].score == 1.0
        assert "mailer" not in {h.ref for h in hunks}, (
            "the bare-`rate` distractor is back on the screen — the reverse "
            "direction has reopened in `pair_strength`"
        )

    def test_a_query_the_candidate_EXTENDS_still_matches_both_rungs(self) -> None:
        """The direction that is KEPT, named as the two documented motivating
        cases. If a directional fix breaks either of these the fix is wrong."""
        assert rc.pair_strength("rate", "ratelimit") == rc.PREFIX_STRENGTH
        assert rc.pair_strength("limit", "ratelimit") == rc.SUBSTRING_STRENGTH
        assert rc.pair_strength("postgres", "postgresql") == rc.PREFIX_STRENGTH

    def test_a_short_token_must_match_EXACTLY(self) -> None:
        """🔴 ONE length rule guarding all three inexact rungs. Short tokens are
        where every inexact rule turns into noise."""
        assert len("pod") < rc.MIN_INEXACT_LEN
        assert rc.pair_strength("pod", "pod") == 1.0
        for noise in ("podman", "podinfo", "pgo"):
            assert rc.pair_strength("pod", noise) == 0.0, noise

    def test_a_typo_clears_the_floor_and_a_DIFFERENT_WORD_does_not(self) -> None:
        """The floor is 0.82 and not 0.80 because 0.80 is exactly where `probe`
        and `prone` land — two real words one edit apart, and not a typo of
        anything."""
        assert rc.pair_strength("conection", "connection") >= rc.FUZZY_FLOOR
        assert rc.pair_strength("probe", "prone") == 0.0

    def test_an_absent_TERM_costs_its_share_because_the_score_is_a_MEAN(self) -> None:
        """🔴 The reason a multi-token query cannot be as loose as its loosest
        word. A max would have scored this 1.00."""
        block = "the edge-proxy rate-limit cap is global"
        assert rc.score_unit(rc.tokenize("edge kryptonite"), block) == pytest.approx(0.5)
        assert rc.score_unit(rc.tokenize("edge kryptonite"), block) < (
            rc.DEFAULT_SEARCH_THRESHOLD
        )

    def test_an_empty_query_matches_NOTHING_rather_than_everything(self) -> None:
        assert rc.score_unit((), "anything at all") == 0.0
        assert rc.score_unit(rc.tokenize("anything"), "") == 0.0


# =============================================================================
# DIRECTIONALITY: the inexact rungs are ONE-WAY, and the join stops at a clause.
# =============================================================================
#
# 🔴 EVERY ENTRY BELOW IS INVENTED, like the corpus above it. The real store is
# client-confidential and this repo is public.

DIRECTIONAL_SCOPE = "widget-yard"


def _make_directional_store(root: Path) -> Path:
    """A synthetic scope carrying one BAIT for each way an inexact rung can lie.

    Each bait is a word the corpus really writes, chosen so that a query which
    STRICTLY CONTAINS it has no honest business matching here."""
    store = root / "directional-store"
    d = store / DIRECTIONAL_SCOPE
    d.mkdir(parents=True)

    def w(name: str, **kw) -> None:
        (d / f"{name}.md").write_text(
            _entry(name, DIRECTIONAL_SCOPE, **kw), encoding="utf-8"
        )

    # SUBSTRING BAIT — and THE HEADLINE CASE. The corpus writes `rotate`; nothing
    # anywhere writes `logrotate`. `rotate` is not a PREFIX of `logrotate`, so
    # this bait can only ever be taken by the substring rung.
    w(
        "spin-drive",
        pointers="- the drum will rotate once per cycle.",
        nuance="- 2026-04-01: a stalled drum reports ready for a further 20s.",
    )
    # PREFIX BAIT. `drain` IS a prefix of `drainage`, so a symmetric prefix rung
    # takes this at 0.92 while a symmetric substring rung takes it at 0.85 — two
    # different numbers, which is what lets the two mutants be told apart.
    w(
        "relay-hub",
        pointers="- drain that node, port-forward the admin socket first.",
        nuance="- 2026-04-04: draining twice in a minute wedges the queue.",
    )
    # POSITIVE CONTROL, prefix rung: the query is a PREFIX of what the corpus
    # writes. This direction is KEPT and must not regress.
    w(
        "ledger-store",
        pointers="- the postgresql primary is the only writer.",
        nuance="- 2026-04-02: failover promotes a stale replica when the vip lags.",
    )
    # POSITIVE CONTROL, substring rung: the query is CONTAINED BY what the corpus
    # writes. Also kept.
    w(
        "edge-gate",
        pointers="- one listener per tenant.",
        nuance="- 2026-04-03: the ratelimit cap is global and not per-tenant.",
    )
    # WITHIN-CLAUSE JOIN, so the clause rule cannot be satisfied by deleting the
    # join outright — that mutant has to stay separately killable.
    w(
        "mesh-probe",
        pointers="- the health check runs every 30s.",
        nuance="- 2026-04-05: a failed check is retried three times before it counts.",
    )
    return store


@pytest.fixture()
def directional_store(tmp_path: Path) -> Path:
    return _make_directional_store(tmp_path)


class TestSearchDirectionality:
    """🔴 THE INEXACT RUNGS ASK ONE QUESTION: does the candidate spell MORE than
    the query? Never the reverse.

    A symmetric rung ("either is a prefix/substring of the other") accepts a
    candidate that is a FRAGMENT of the query. `score_unit` is a mean over the
    QUERY's tokens, so a ONE-TOKEN query then took FULL coverage from a single
    incidental short word, and every hit on the page carried a 0.85 or 0.92 that
    nothing in the entry justified — on real single-token queries a MAJORITY of
    the hunks served did not contain the query anywhere, and some first pages
    were fabricated end to end. No fraction is quoted: it is a property of a
    store that changes daily. This class pins the BEHAVIOUR instead."""

    def test_the_fixture_still_carries_its_BAIT(self, directional_store: Path) -> None:
        """🔴 The positive control on the corpus itself. Every assertion below is
        an ABSENCE, and an absence is worthless if the bait quietly left the
        fixture — the store would then be silent for the wrong reason."""
        corpus = "\n".join(
            p.read_text(encoding="utf-8")
            for p in sorted((directional_store / DIRECTIONAL_SCOPE).glob("*.md"))
        ).lower()
        for bait in ("rotate", "drain", "node", "port"):
            assert bait in corpus, f"the `{bait}` bait left the fixture"
        for absent in ("logrotate", "drainage", "nodeport"):
            assert absent not in corpus, f"`{absent}` is IN the corpus — no bait left"

    def test_a_FRAGMENT_of_the_query_is_NOT_a_match(
        self, directional_store: Path
    ) -> None:
        """THE HEADLINE. A corpus that only ever writes `rotate` must be SILENT
        for `logrotate` — the whole word is absent, and a searcher that answers
        anyway has invented its answer.

        The end-to-end claim is asserted FIRST, so the red this test throws on
        pre-change code is the symptom a CALLER sees and not only a number."""
        rep = rc.search(directional_store, DIRECTIONAL_SCOPE, "logrotate")
        assert rep.status == "search-no-match", (
            f"a corpus that never writes `logrotate` served {len(rep.hunks)} hunks "
            f"for it, top score {rep.hunks[0].score if rep.hunks else 0:.2f}"
        )
        assert rep.hunks == ()
        # …and the rung that used to take the bait, at its own number (0.85).
        assert rc.pair_strength("logrotate", "rotate") == 0.0

    def test_the_prefix_FRAGMENT_is_not_a_match_either(
        self, directional_store: Path
    ) -> None:
        """The other rung's bait, end to end: `drain` prefixes `drainage`, so
        this one used to be taken at 0.92 rather than 0.85."""
        rep = rc.search(directional_store, DIRECTIONAL_SCOPE, "drainage")
        assert rep.status == "search-no-match", (
            f"a corpus that never writes `drainage` served {len(rep.hunks)} hunks "
            f"for it, top score {rep.hunks[0].score if rep.hunks else 0:.2f}"
        )
        assert rc.pair_strength("drainage", "drain") == 0.0

    def test_the_POSITIVE_CONTROLS_the_fix_must_not_break(
        self, directional_store: Path
    ) -> None:
        """🔴 The direction that is KEPT, as the two cases `pair_strength` names
        as motivating. If a directional fix breaks either of these the FIX is
        wrong, not the test."""
        pg = rc.search(directional_store, DIRECTIONAL_SCOPE, "postgres")
        assert pg.status == "search-hit"
        assert pg.hunks[0].ref == "ledger-store"
        assert pg.hunks[0].score == rc.PREFIX_STRENGTH
        assert "postgresql" in "\n".join(pg.hunks[0].lines)

        lim = rc.search(directional_store, DIRECTIONAL_SCOPE, "limit")
        assert lim.status == "search-hit"
        assert lim.hunks[0].ref == "edge-gate"
        assert lim.hunks[0].score == rc.SUBSTRING_STRENGTH
        assert "ratelimit" in "\n".join(lim.hunks[0].lines)

    def test_an_EXACT_hit_still_outranks_every_inexact_rung(
        self, directional_store: Path
    ) -> None:
        """The narrowing control at the top of the ladder: a whole-word query
        still lands at 1.00 and still beats the 0.92 the same store can offer."""
        rep = rc.search(directional_store, DIRECTIONAL_SCOPE, "rotate")
        assert rep.status == "search-hit"
        assert rep.hunks[0].ref == "spin-drive"
        assert rep.hunks[0].score == 1.0
        assert rep.hunks[0].score > rc.PREFIX_STRENGTH

    def test_a_MULTI_token_query_still_covers_across_the_block(
        self, directional_store: Path
    ) -> None:
        """The token-set path, so a fix that narrowed matching in general is
        caught here and not only at the rung it touched."""
        rep = rc.search(directional_store, DIRECTIONAL_SCOPE, "stalled drum")
        assert rep.status == "search-hit"
        assert rep.hunks[0].ref == "spin-drive"
        assert rep.hunks[0].score == 1.0
        # …and the mean still charges for a word that is not there.
        half = rc.search(directional_store, DIRECTIONAL_SCOPE, "stalled kryptonite")
        assert half.status == "search-no-match"

    def test_the_NONSENSE_query_control_AND_the_no_match_affordance(
        self, directional_store: Path
    ) -> None:
        """🔴 THE `NO MATCH` BRANCH, PROVEN REACHABLE IN BOTH ITS SHAPES.

        They are reached differently and neither may be assumed. A ONE-TOKEN
        query can never produce a NEAR MISS at the default threshold — its score
        IS its single token's strength, and every non-zero rung (0.85 and up)
        already clears 0.60 — so the near-miss shape is reached by raising the
        threshold, exactly as the flag's own help says. The advice is then
        EXECUTED, because an affordance nobody runs is decoration."""
        gone = rc.search(directional_store, DIRECTIONAL_SCOPE, "kryptonite")
        assert gone.status == "search-no-match"
        assert gone.best_below is None
        n = len(list((directional_store / DIRECTIONAL_SCOPE).glob("*.md")))
        assert gone.entries_searched == n > 1
        absent_text = rc.render_search(gone)
        assert "No candidate scored above zero at all" in absent_text
        assert "closest candidate" not in absent_text

        near = rc.search(
            directional_store, DIRECTIONAL_SCOPE, "postgres", threshold=0.95
        )
        assert near.status == "search-no-match"
        assert near.best_below == ("ledger-store", rc.PREFIX_STRENGTH)
        near_text = rc.render_search(near)
        assert "closest candidate was `ledger-store` at 0.92" in near_text
        assert "`--threshold 0.91`" in near_text
        # The advertised flag, RUN.
        again = rc.search(
            directional_store, DIRECTIONAL_SCOPE, "postgres", threshold=0.91
        )
        assert again.status == "search-hit"
        assert again.hunks[0].ref == "ledger-store"


class TestCandidateJoinStopsAtAClause:
    """🔴 The join may cross the four ways ONE term is spelled, and nothing else.

    Joining `node`+`port` across a comma manufactured a PERFECT 1.00 for
    `nodeport` in an entry that does not contain the word — the worst shape this
    scorer can emit, because 1.00 out-ranks every genuine match on the page."""

    def test_a_COMMA_is_not_part_of_a_compound(self, directional_store: Path) -> None:
        assert "nodeport" not in rc.candidate_tokens("drain that node, port-forward it")
        assert "nodeport" in rc.candidate_tokens("drain that node port-forward it")
        rep = rc.search(directional_store, DIRECTIONAL_SCOPE, "nodeport")
        assert rep.status == "search-no-match"

    def test_every_clause_BREAKER_is_reachable(self) -> None:
        """Each character in the class, exercised — a set nobody probes is a set
        whose members can be wrong one at a time."""
        assert "alphabeta" in rc.candidate_tokens("one alpha beta two"), (
            "the positive control failed: the join is not happening at all, so "
            "every absence below would pass for the wrong reason"
        )
        for ch in ",;:!?()[]":
            assert "alphabeta" not in rc.candidate_tokens(f"one alpha{ch} beta two"), ch

    def test_a_SENTENCE_stop_breaks_it_but_a_DOTTED_identifier_does_not(self) -> None:
        """🔴 The `.` rule is the narrow one, and this is why: a dotted
        identifier is a single term whose halves must keep joining, so the break
        fires only when the `.` is followed by whitespace or end-of-text."""
        assert "alphabeta" not in rc.candidate_tokens("one alpha. Beta follows")
        assert "alphabeta" not in rc.candidate_tokens("it ends in alpha.")
        assert "alphabeta" in rc.candidate_tokens("the alpha.beta key")

    def test_the_join_still_crosses_every_spelling_of_ONE_compound(
        self, directional_store: Path
    ) -> None:
        """🔴 THE COST SIDE, MEASURED SEPARATELY. A hyphen, an underscore, a
        space and a dot inside an identifier are the four ways one term gets
        written; a clause rule that broke on any of them would trade one false
        positive for a lost match."""
        for spelling in ("health-check", "health_check", "health check", "health.check"):
            assert "healthcheck" in rc.candidate_tokens(f"the {spelling} runs"), spelling
        rep = rc.search(directional_store, DIRECTIONAL_SCOPE, "healthcheck")
        assert rep.status == "search-hit"
        assert rep.hunks[0].ref == "mesh-probe"
        assert rep.hunks[0].score == 1.0

    def test_clause_FREE_text_yields_EXACTLY_the_old_candidate_set(self) -> None:
        """🔴 THE NARROWING CONTROL. Where there is no clause punctuation the new
        rule must produce precisely the tokens-plus-every-adjacent-join set the
        old one did — same members, same order — or the fix has quietly shrunk
        real matching everywhere instead of only at the boundary it targets."""
        text = "the readiness probe lies for 40s after a reload"
        toks = rc.tokenize(text)
        old = tuple(toks) + tuple(a + b for a, b in zip(toks, toks[1:]))
        assert rc.candidate_tokens(text) == old

    def test_candidate_tokens_takes_TEXT_so_the_guard_cannot_be_BYPASSED(
        self,
    ) -> None:
        """🔴 One rule, one place. If it accepted a token sequence, a caller that
        tokenized first would silently lose the clause boundary — which is the
        exact shape of the defect. Passing tokens must FAIL, loudly."""
        with pytest.raises(TypeError):
            rc.candidate_tokens(rc.tokenize("drain that node, port-forward it"))


class TestEntryBlocks:
    """The unit of a match. Everything before the first heading is skipped, which
    is the front-matter exclusion expressed as a property of the OUTPUT."""

    def test_front_matter_is_never_a_block(self, search_store: Path) -> None:
        """🔴 A prototype that folded slug tokens into every line ranked the `---`
        fence first. Front matter carries no `##`, so it falls out for free — and
        `parse_front_matter` stays the ONE thing that reads it."""
        text = (search_store / SEARCH_SCOPE / "blob-vault.md").read_text(encoding="utf-8")
        blocks = rc.entry_blocks(text)
        assert blocks, "the positive control: this entry does produce blocks"
        joined = "\n".join(b.text for b in blocks)
        assert "---" not in joined
        assert "service:" not in joined
        assert "scope:" not in joined
        assert "aliases:" not in joined

    def test_a_heading_is_a_LABEL_and_not_a_block(self, search_store: Path) -> None:
        """Or a query for the literal words "nuance work history" would hit every
        entry in the store."""
        text = (search_store / SEARCH_SCOPE / "quill.md").read_text(encoding="utf-8")
        blocks = rc.entry_blocks(text)
        assert all(not b.text.lstrip().startswith("#") for b in blocks)
        assert {rc.NUANCE_HEADING, rc.POINTERS_HEADING} <= {b.section for b in blocks}

    def test_a_wrapped_bullet_is_ONE_block(self, search_store: Path) -> None:
        text = (search_store / SEARCH_SCOPE / "quill.md").read_text(encoding="utf-8")
        block = next(b for b in rc.entry_blocks(text) if "quill worker" in b.text)
        assert len(block.lines) == 2
        assert "scheduler restarts mid-batch" in block.text

    def test_two_bullets_are_TWO_blocks(self, search_store: Path) -> None:
        text = (search_store / SEARCH_SCOPE / "blob-vault.md").read_text(encoding="utf-8")
        nuance = [b for b in rc.entry_blocks(text) if b.section == rc.NUANCE_HEADING]
        assert len(nuance) == 2
        assert all(len(b.lines) == 1 for b in nuance)

    def test_a_fence_stays_WHOLE_even_though_its_lines_start_with_a_dash(
        self, search_store: Path
    ) -> None:
        """Splitting a snippet mid-command emits a fragment that reads like a
        complete instruction — the same hazard `-C N` carries, and the reason the
        bullet is the default context."""
        text = (search_store / SEARCH_SCOPE / "chart-render.md").read_text(encoding="utf-8")
        fence = next(b for b in rc.entry_blocks(text) if "renderctl" in b.text)
        assert "--format svg" in fence.text
        assert "--strict" in fence.text
        assert fence.text.count("```") == 2

    def test_the_line_numbers_are_1_BASED_and_point_at_the_FILE(
        self, search_store: Path
    ) -> None:
        """`-C N` slices the raw file with these, so an off-by-one here shows a
        window around the wrong text with a confident line number beside it."""
        path = search_store / SEARCH_SCOPE / "ledger-db.md"
        raw = path.read_text(encoding="utf-8").splitlines()
        for block in rc.entry_blocks(path.read_text(encoding="utf-8")):
            assert raw[block.start - 1] == block.lines[0], block
            assert raw[block.end - 1] == block.lines[-1], block


class TestSearchOutputContract:
    """🔴 EVERY HUNK CARRIES ITS OWN LABELS. The caveat prints once and
    sensitivity is a per-entry fact, but hunk output INTERLEAVES entries — a
    label printed once gets separated from the content it governs."""

    def test_each_hunk_names_its_scope_ref_section_and_sensitivity(
        self, search_store: Path
    ) -> None:
        rep = rc.search(search_store, SEARCH_SCOPE, "blob-vault")
        assert len(rep.hunks) >= 2, "one hunk cannot show a label going missing"
        text = rc.render_search(rep)
        for h in rep.hunks:
            assert h.sensitivity in rc.KNOWN_SENSITIVITIES
            line = next(
                l for l in text.splitlines() if f"{h.scope}/{h.ref} " in l and "[" in l
            )
            assert f"{h.scope}/{h.ref}" in line
            assert h.section in line
            assert f"sensitivity={h.sensitivity}" in line
            assert f"{h.filename}:{h.start}" in line

    def test_the_labels_repeat_ONCE_PER_HUNK_not_once_per_run(
        self, search_store: Path
    ) -> None:
        """Counted, not eyeballed: a label printed once at the top would satisfy
        an `in` check while describing somebody else's lines further down."""
        rep = rc.search(search_store, SEARCH_SCOPE, "blob-vault")
        text = rc.render_search(rep)
        assert text.count("sensitivity=") == len(rep.hunks)

    def test_an_absent_sensitivity_marker_folds_to_client_confidential(
        self, search_store: Path
    ) -> None:
        """The schema's fail-safe, carried onto the hunk — the fixture writes no
        `sensitivity:` at all."""
        assert all(
            h.sensitivity == rc.SENSITIVITY_FAIL_SAFE
            for h in rc.search(search_store, SEARCH_SCOPE, "compaction").hunks
        )

    def test_the_score_and_the_THRESHOLD_are_both_printed(
        self, search_store: Path
    ) -> None:
        """A score with no threshold beside it cannot be read as strong or weak."""
        text = rc.render_search(rc.search(search_store, SEARCH_SCOPE, "compaction"))
        assert "threshold=0.60" in text
        assert "[1.00 line]" in text

    def test_a_WEAK_match_prints_as_weak(self, search_store: Path) -> None:
        rep = rc.search(search_store, SEARCH_SCOPE, "compaction kryptonite", threshold=0.4)
        assert rep.hunks
        assert rep.hunks[0].score == pytest.approx(0.5)
        assert "[0.50 line]" in rc.render_search(rep)

    def test_the_caveat_is_on_the_search_path_too(self, search_store: Path) -> None:
        for query in ("compaction", "kryptonite", "zephyr"):
            text = rc.render_search(rc.search(search_store, SEARCH_SCOPE, query))
            assert "RECALL, NOT LIVE OBSERVATION" in text, query
            assert "never copy an entry's content into a public repo" in text, query

    def test_the_default_context_is_the_enclosing_BULLET(
        self, search_store: Path
    ) -> None:
        rep = rc.search(search_store, SEARCH_SCOPE, "quill scheduler")
        assert rep.context == rc.CONTEXT_BULLET
        assert rep.hunks[0].lines == (
            "- 2026-03-05: the quill worker drops a job when the",
            "  scheduler restarts mid-batch.",
        )
        assert "context=bullet" in rc.render_search(rep)

    def test_dash_C_overrides_with_RAW_lines(self, search_store: Path) -> None:
        """The agent drives it. `-C 1` must widen the window with the file's own
        neighbouring lines — including the heading above, which is exactly the
        orientation a raw window is for."""
        rep = rc.search(search_store, SEARCH_SCOPE, "quill scheduler", context=1)
        top = rep.hunks[0]
        assert rc.NUANCE_HEADING in "\n".join(top.lines)
        assert len(top.lines) > 2
        assert "context=±1 raw lines" in rc.render_search(rep)

    def test_dash_C_0_is_the_block_alone_and_NOT_the_bullet_sentinel(
        self, search_store: Path
    ) -> None:
        """0 and "the bullet" are different requests that happen to agree here;
        they must stay distinguishable in the report or a JSON consumer cannot
        tell which the caller asked for."""
        rep = rc.search(search_store, SEARCH_SCOPE, "quill scheduler", context=0)
        assert rep.context == 0 != rc.CONTEXT_BULLET
        assert len(rep.hunks[0].lines) == 2

    def test_a_context_window_is_CLAMPED_to_the_file(self, search_store: Path) -> None:
        rep = rc.search(search_store, SEARCH_SCOPE, "compaction", context=500)
        raw = (search_store / SEARCH_SCOPE / "blob-vault.md").read_text(
            encoding="utf-8"
        ).splitlines()
        assert rep.hunks[0].start == 1
        assert len(rep.hunks[0].lines) == len(raw)

    def test_search_covers_WHAT_IT_IS_like_every_other_section(
        self, search_store: Path
    ) -> None:
        """Search has always covered `## What it is` — a targeted question, and
        hiding a section would answer it with a confident zero. The hunk names
        its section, so the reader always knows which one they got.

        ⚠ This test used to end by asserting the DIGEST hid the same section. It
        no longer does: search and the digest now agree, and the divergence this
        docstring used to justify is gone."""
        rep = rc.search(search_store, SEARCH_SCOPE, "durable")
        assert rep.hunks
        assert any("What it is" in h.section for h in rep.hunks)

    def test_the_hunk_truncation_is_LOUD(self, search_store: Path) -> None:
        rep = rc.search(search_store, SEARCH_SCOPE, "the", threshold=0.5, max_hits=2)
        assert rep.total_hits > 2
        assert len(rep.hunks) == 2
        assert rep.omitted == rep.total_hits - 2
        text = rc.render_search(rep)
        assert "NOT shown (--max-hits 2)" in text
        assert "display cap" in text

    def test_no_truncation_prints_no_truncation_line(self, search_store: Path) -> None:
        assert "NOT shown" not in rc.render_search(
            rc.search(search_store, SEARCH_SCOPE, "compaction")
        )

    def test_the_output_is_DETERMINISTIC(self, search_store: Path) -> None:
        a = rc.render_search(rc.search(search_store, SEARCH_SCOPE, "the", threshold=0.5))
        b = rc.render_search(rc.search(search_store, SEARCH_SCOPE, "the", threshold=0.5))
        assert a == b

    def test_the_json_carries_every_label_the_text_does(
        self, search_store: Path
    ) -> None:
        blob = rc.search_json(rc.search(search_store, SEARCH_SCOPE, "blob-vault"))
        assert blob["status"] == "search-hit"
        assert blob["label"] == "from index"
        assert "RECALL, NOT LIVE OBSERVATION" in blob["caveat"]
        for row in blob["hunks"]:
            assert set(row) >= {
                "scope",
                "ref",
                "file",
                "sensitivity",
                "section",
                "start_line",
                "end_line",
                "score",
                "basis",
                "lines",
            }
            assert row["basis"] in rc.HUNK_BASES


class TestSearchScopes:
    def test_the_default_is_THIS_scope_only(self, tmp_path: Path) -> None:
        store = _make_search_store(tmp_path)
        (store / OTHER_SCOPE).mkdir()
        (store / OTHER_SCOPE / "fan-curve.md").write_text(
            _entry("fan-curve", OTHER_SCOPE, nuance="- the compaction pass is elsewhere."),
            encoding="utf-8",
        )
        refs = {h.ref for h in rc.search(store, SEARCH_SCOPE, "compaction").hunks}
        assert "fan-curve" not in refs
        assert "blob-vault" in refs

    def test_all_scopes_reaches_the_OTHER_scope(self, tmp_path: Path) -> None:
        store = _make_search_store(tmp_path)
        (store / OTHER_SCOPE).mkdir()
        (store / OTHER_SCOPE / "fan-curve.md").write_text(
            _entry("fan-curve", OTHER_SCOPE, nuance="- the compaction pass is elsewhere."),
            encoding="utf-8",
        )
        rep = rc.search(store, SEARCH_SCOPE, "compaction", all_scopes=True)
        assert {h.ref for h in rep.hunks} >= {"fan-curve", "blob-vault"}
        assert set(rep.scopes_searched) == {SEARCH_SCOPE, OTHER_SCOPE}
        # Every hunk still says which scope it came from — the one thing that
        # stops a cross-scope result being read as this repo's.
        assert {h.scope for h in rep.hunks} == {SEARCH_SCOPE, OTHER_SCOPE}

    def test_an_absent_scope_is_a_STATUS_and_says_nothing_was_SEARCHED(
        self, search_store: Path
    ) -> None:
        """🔴 The empty-result rule: "the store has no such scope" and "the query
        matched nothing" are different mechanisms with the same shape."""
        rep = rc.search(search_store, "never-indexed", "compaction")
        assert rep.status == "scope-absent"
        text = rc.render_search(rep)
        assert "NOT 'no matches'" in text
        assert "nothing was searched" in text
        assert SEARCH_SCOPE in text

    def test_all_scopes_cannot_produce_scope_absent(self, search_store: Path) -> None:
        assert (
            rc.search(search_store, "never-indexed", "compaction", all_scopes=True).status
            != "scope-absent"
        )


class TestSearchZeroIsAccounted:
    """🔴 `claude/RULES.md` — an EMPTY RESULT cannot distinguish two mechanisms.
    A no-match has to carry what was scanned and how close the best candidate
    came, or "nothing matched" and "just missed at 0.50" print the same blank."""

    def test_a_NEAR_miss_is_named_with_its_score_and_the_flag_that_shows_it(
        self, search_store: Path
    ) -> None:
        rep = rc.search(search_store, SEARCH_SCOPE, "compaction kryptonite")
        assert rep.status == "search-no-match"
        assert rep.best_below == ("blob-vault", 0.5)
        text = rc.render_search(rep)
        assert "closest candidate was `blob-vault` at 0.50" in text
        assert "`--threshold 0.49`" in text

    def test_a_TRULY_absent_term_says_so_INSTEAD(self, search_store: Path) -> None:
        """The discriminator. A zero-scoring block is not a near miss, and
        reporting it as "closest candidate: 0.00" would read as a weak match."""
        rep = rc.search(search_store, SEARCH_SCOPE, "kryptonite")
        assert rep.best_below is None
        text = rc.render_search(rep)
        assert "No candidate scored above zero at all" in text
        assert "absent term rather than a weak one" in text
        assert "closest candidate" not in text

    def test_a_no_match_reports_HOW_MUCH_it_scanned(self, search_store: Path) -> None:
        """A zero from a scan that walked nothing is the failure, not the
        all-clear — so the count of entries searched travels with the zero."""
        rep = rc.search(search_store, SEARCH_SCOPE, "kryptonite")
        # Derived from the store, not hand-counted: a fixture that grew an entry
        # must not be able to make this assertion pass for a new reason.
        n = len(list((search_store / SEARCH_SCOPE).glob("*.md")))
        assert n > 1
        assert rep.entries_searched == n
        assert f"searched {n} entries in `{SEARCH_SCOPE}/`" in rc.render_search(rep)

    def test_the_two_no_match_sentences_share_no_phrase(
        self, search_store: Path
    ) -> None:
        near = rc.render_search(rc.search(search_store, SEARCH_SCOPE, "compaction kryptonite"))
        gone = rc.render_search(rc.search(search_store, SEARCH_SCOPE, "kryptonite"))
        assert near != gone
        assert "closest candidate" in near and "closest candidate" not in gone
        assert "above zero at all" in gone and "above zero at all" not in near

    def test_the_suggested_threshold_ACTUALLY_surfaces_the_near_miss(
        self, search_store: Path
    ) -> None:
        """🔴 The advice is executable or it is decoration. `--threshold 0.49` has
        to produce the hunk the no-match named, at the score it named."""
        rep = rc.search(search_store, SEARCH_SCOPE, "compaction kryptonite", threshold=0.49)
        assert rep.status == "search-hit"
        assert rep.hunks[0].ref == "blob-vault"
        assert rep.hunks[0].score == pytest.approx(0.5)


class TestSearchNegativeControls:
    def test_an_empty_query_RAISES_its_OWN_sentinel(self, search_store: Path) -> None:
        for bad in ("", "   ", "\n"):
            with pytest.raises(ValueError) as exc:
                rc.search(search_store, SEARCH_SCOPE, bad)
            assert "query must be a non-empty string" in str(exc.value)

    def test_a_nonsense_threshold_RAISES_its_OWN_sentinel(
        self, search_store: Path
    ) -> None:
        for bad in (-0.1, 1.5, "high", True):
            with pytest.raises(ValueError) as exc:
                rc.search(search_store, SEARCH_SCOPE, "compaction", threshold=bad)
            assert "threshold must be a number in [0, 1]" in str(exc.value)
            assert "query must be" not in str(exc.value), "a neighbour's sentinel"

    def test_a_nonsense_max_hits_or_context_RAISES_its_OWN_sentinel(
        self, search_store: Path
    ) -> None:
        with pytest.raises(ValueError) as exc:
            rc.search(search_store, SEARCH_SCOPE, "compaction", max_hits=0)
        assert "max-hits must be an int >= 1" in str(exc.value)
        with pytest.raises(ValueError) as exc:
            rc.search(search_store, SEARCH_SCOPE, "compaction", context=-2)
        assert "context must be an int >= 0" in str(exc.value)

    def test_a_missing_store_RAISES_the_SAME_class_recall_does(
        self, tmp_path: Path
    ) -> None:
        """One condition, one class — and the message says what did NOT happen,
        because "nothing was searched" must not read as "no matches"."""
        with pytest.raises(rc.StoreMissingError) as exc:
            rc.search(tmp_path / "absent", SEARCH_SCOPE, "compaction")
        assert "store root not found" in str(exc.value)
        assert "Nothing was searched" in str(exc.value)

    def test_a_malformed_entry_DEGRADES_and_the_SHORT_result_says_so(
        self, search_store: Path
    ) -> None:
        """🔴 SUPERSEDES the fail-closed version. The old objection — "a search
        that silently skipped an unreadable entry would report a zero the store
        cannot support" — is still exactly right, and is now answered by the
        report rather than by the raise: the hits come back AND the skipped file
        is named in the same output, so the result is short and SAYS it is."""
        (search_store / SEARCH_SCOPE / "no-front-matter.md").write_text(
            "just prose\n", encoding="utf-8"
        )
        rep = rc.search(search_store, SEARCH_SCOPE, "compaction")  # must not raise
        assert rep.status == "search-hit"
        assert [m.filename for m in rep.malformed] == ["no-front-matter.md"]
        text = rc.render_search(rep)
        assert "malformed index entry" in text
        assert "no-front-matter.md" in text

    def test_search_leaves_the_store_BYTE_IDENTICAL(self, search_store: Path) -> None:
        before = _tree_hash(search_store)
        for query, kw in (
            ("compaction", {}),
            ("kryptonite", {}),
            ("zephyr", {"context": 3}),
            ("compaction", {"all_scopes": True}),
        ):
            rep = rc.search(search_store, SEARCH_SCOPE, query, **kw)
            rc.render_search(rep)
            json.dumps(rc.search_json(rep))
        with pytest.raises(ValueError):
            rc.search(search_store, SEARCH_SCOPE, "")
        assert _tree_hash(search_store) == before


# =============================================================================
# THE FOCUS WINDOW — one file read, no git, no network, no subprocess.
# =============================================================================


def _make_repo(root: Path, name: str, body: str) -> Path:
    repo = root / "fixture-repo"
    (repo / "claudedocs").mkdir(parents=True, exist_ok=True)
    (repo / "claudedocs" / name).write_text(body, encoding="utf-8")
    return repo


class TestFocusWindow:
    def test_it_reads_the_newest_handoff_and_reports_its_path(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path, "handoff-old.md", "old, mentions `apps/widget-01/x.yaml`\n")
        (repo / "claudedocs" / "handoff-new.md").write_text(
            "new, mentions `apps/widget-09/x.yaml`\n", encoding="utf-8"
        )
        __import__("os").utime(repo / "claudedocs" / "handoff-old.md", (10**9, 10**9))
        __import__("os").utime(repo / "claudedocs" / "handoff-new.md", (2 * 10**9, 2 * 10**9))
        w = rc.focus_window(repo)
        assert w.source == "claudedocs/handoff-new.md"
        assert "apps/widget-09/x.yaml" in w.paths
        assert "apps/widget-01/x.yaml" not in w.paths

    def test_the_uppercase_family_is_only_a_FALLBACK(self, tmp_path: Path) -> None:
        """Same order `scripts/resume-state.sh` resolves in, so step 3 and step 4
        of /resume cannot end up pointed at different initiatives."""
        repo = _make_repo(tmp_path, "SESSION-HANDOFF.md", "caps `apps/widget-02/x.yaml`\n")
        assert rc.focus_window(repo).source == "claudedocs/SESSION-HANDOFF.md"
        (repo / "claudedocs" / "handoff-lower.md").write_text(
            "lower `apps/widget-03/x.yaml`\n", encoding="utf-8"
        )
        assert rc.focus_window(repo).source == "claudedocs/handoff-lower.md"

    def test_no_handoff_is_an_ORDINARY_empty_window(self, tmp_path: Path) -> None:
        """Most repos have no handoff at the moment they are resumed. It must not
        raise: the caller's mtime fallback is a real answer, not a degraded one."""
        (tmp_path / "bare").mkdir()
        w = rc.focus_window(tmp_path / "bare")
        assert w == rc.FocusWindow()
        assert w.paths == () and w.source is None

    @pytest.mark.parametrize(
        "token,keep",
        [
            ("scripts/lib/thing.py", True),
            (".claude/skills/resume/SKILL.md", True),
            ("nix/i3/config", True),
            ("/etc/nixos/configuration.nix", False),  # absolute
            ("~/workspace/devrc/flake.nix", False),  # home-relative
            ("../outside/thing.yaml", False),  # escapes the repo root
            ("$DEVRC/scripts/ship.sh", False),  # a shell variable
            ("https://example.invalid/a/b", False),  # a URL
            ("git@github.invalid:o/r.git", False),  # a host
            ("README.md", False),  # no separator: not a path
            ("--limit 25", False),  # a flag, and it has a space
        ],
        ids=lambda v: str(v),
    )
    def test_which_backticked_tokens_count_as_paths(self, token: str, keep: bool) -> None:
        got = rc.focus_paths_from_text(f"prose `{token}` prose\n")
        assert (token in got) is keep, got

    def test_trailing_punctuation_is_stripped(self, tmp_path: Path) -> None:
        assert rc.focus_paths_from_text("see `scripts/gate.sh`.") == ("scripts/gate.sh",)
        assert rc.focus_paths_from_text("see `scripts/lib/`") == ("scripts/lib",)

    def test_bare_prose_is_NOT_harvested(self, tmp_path: Path) -> None:
        """🔴 `scripts/resume-state.sh` learned this with branch tokens: reaching
        into unquoted prose mints tokens out of ordinary English, and a fabricated
        fact is worse than the silence it replaced."""
        assert rc.focus_paths_from_text("we touched scripts/lib/thing.py today") == ()

    def test_every_emitted_path_is_one_associate_paths_ACCEPTS(self, tmp_path: Path) -> None:
        """🔴 POSITIVE CONTROL ON THE FILTER, and the reason it is stricter than
        the resolver: anything that gets through is handed straight to
        `associate_paths`, which RAISES on a path it considers malformed. A
        /resume step that died because a handoff doc quoted a URL would be worse
        than the cost this whole change is removing."""
        store = _make_store(tmp_path / "s")
        index = sr.load_index(store)
        text = (
            "`/abs/path.yaml` `../up.yaml` `~/home.yaml` `$VAR/x.yaml` "
            "`https://h.invalid/a/b` `ok/one.yaml` `deep/a/b/c/two.yml`"
        )
        paths = rc.focus_paths_from_text(text)
        assert paths  # the control can observe something
        sr.associate_paths(paths, index, SCOPE, min_paths=1)  # must not raise

    def test_the_doc_itself_joins_the_window(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path, "handoff-topic.md", "no paths here\n")
        w = rc.focus_window(repo)
        assert w.paths == ("claudedocs/handoff-topic.md",)

    def test_it_runs_no_subprocess(self) -> None:
        """🔴 The whole point of reading a FILE rather than asking git: the
        writer's path sources shell out to `git` and `gh`, and importing that
        cost into /resume step 4 is what this change exists to avoid."""
        src = MODULE_PATH.read_text(encoding="utf-8")
        code = "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#"))
        for spelling in ("import subprocess", "subprocess.", "os.system", "popen"):
            assert spelling not in code, f"the reader grew a subprocess call: {spelling}"


# =============================================================================
# THE CAVEAT — on EVERY output path, in BOTH renderers.
# =============================================================================


class TestCaveatOnEveryOutputPath:
    """🔴 `claude/RULES.md`: index content is recall, never live observation.
    A caveat printed on the happy path and dropped on the empty ones is worse
    than none — the empty ones are where a reader is most likely to conclude
    something."""

    def _reports(self, store: Path):
        (store / "made-never-filled").mkdir()
        return [
            rc.recall(store, SCOPE),
            rc.recall(store, "never-indexed"),
            rc.recall(store, "made-never-filled"),
            rc.recall(store, SCOPE, ref="no-such-subsystem"),
            rc.recall(store, SCOPE, ref="weekly-digest"),
            rc.recall(store, SCOPE, limit=1),
        ]

    def test_every_status_carries_the_caveat_in_TEXT(self, store: Path) -> None:
        for rep in self._reports(store):
            text = rc.render_text(rep)
            assert "caveat:" in text, rep.status
            assert rc.RECALL_LABEL in text, rep.status
            assert "RECALL, NOT LIVE OBSERVATION" in text, rep.status

    def test_every_status_carries_the_caveat_in_JSON(self, store: Path) -> None:
        for rep in self._reports(store):
            blob = rc.report_json(rep)
            assert blob["label"] == rc.RECALL_LABEL, rep.status
            assert "RECALL, NOT LIVE OBSERVATION" in blob["caveat"], rep.status

    def test_the_caveat_names_what_it_CANNOT_see(self, store: Path) -> None:
        caveat = rc.recall(store, SCOPE).caveat
        assert "CANNOT see" in caveat
        assert "live state" in caveat
        assert "as fresh as the last time someone pruned it" in caveat

    def test_the_label_is_the_one_analyze_service_uses(self) -> None:
        """Two spellings of one provenance claim, in front of the same agent, in
        the same session, about the same store."""
        assert rc.RECALL_LABEL == "from index"
        assert f"`{rc.RECALL_LABEL}`" in ANALYZE_DOC.read_text(encoding="utf-8")

    def test_the_caveat_has_ONE_spelling(self, store: Path) -> None:
        """It is a property, not a sentence per branch. Both renderers must emit
        byte-identical text for the same report."""
        rep = rc.recall(store, SCOPE)
        assert rep.caveat in rc.render_text(rep)
        assert rc.report_json(rep)["caveat"] == rep.caveat

    def test_it_warns_against_copying_into_a_public_repo(self, store: Path) -> None:
        assert "never copy an entry's content into a public repo" in rc.recall(store, SCOPE).caveat


# =============================================================================
# THE ZERO-WRITE-PATH PROPERTY.
# =============================================================================


class TestRecallNeverWrites:
    """Behavioural, not spelled. A grep for a write spelling passes while a
    different spelling writes; hashing the tree does not care how it is spelled."""

    def test_the_hasher_can_observe_a_change(self, store: Path) -> None:
        """Positive control on the INSTRUMENT: a hash that never moves is
        indistinguishable from one wired to a constant."""
        before = _tree_hash(store)
        (store / SCOPE / "collector.md").write_text("mutated\n", encoding="utf-8")
        assert _tree_hash(store) != before

    @pytest.mark.parametrize(
        "scope,ref,limit,mode",
        [
            (SCOPE, None, rc.DEFAULT_ENTRY_LIMIT, "full"),
            (SCOPE, "collector", rc.DEFAULT_ENTRY_LIMIT, "full"),
            (SCOPE, "weekly-digest", rc.DEFAULT_ENTRY_LIMIT, "full"),
            (SCOPE, "no-such-subsystem", rc.DEFAULT_ENTRY_LIMIT, "full"),
            (SCOPE, None, 1, "full"),
            ("never-indexed", None, rc.DEFAULT_ENTRY_LIMIT, "full"),
            (SCOPE, None, rc.DEFAULT_ENTRY_LIMIT, "digest"),
            (SCOPE, None, rc.DEFAULT_ENTRY_LIMIT, "list"),
        ],
        ids=[
            "recalled",
            "ref",
            "ambiguous",
            "ref-absent",
            "truncated",
            "scope-absent",
            "digest",
            "list",
        ],
    )
    def test_every_mode_leaves_the_store_byte_identical(
        self, store: Path, scope, ref, limit, mode
    ) -> None:
        before = _tree_hash(store)
        rep = rc.recall(store, scope, ref=ref, limit=limit, mode=mode)
        rc.render_text(rep)
        json.dumps(rc.report_json(rep))
        assert _tree_hash(store) == before

    def test_the_focus_window_never_writes_to_the_REPO_either(self, tmp_path: Path) -> None:
        """The one thing in the module that reads outside the store. It reads a
        handoff doc; hashing the repo tree either side proves it does no more."""
        repo = tmp_path / "repo"
        (repo / "claudedocs").mkdir(parents=True)
        (repo / "claudedocs" / "handoff-thing.md").write_text(
            "touches `nix/collector/values.yaml`\n", encoding="utf-8"
        )
        before = _tree_hash(repo)
        assert rc.focus_window(repo).paths
        assert _tree_hash(repo) == before

    def test_even_the_FAILURE_paths_leave_it_byte_identical(self, store: Path) -> None:
        """A helper that writes only when it errors still writes."""
        before = _tree_hash(store)
        with pytest.raises(ValueError):
            rc.recall(store, SCOPE, limit=0)
        (store / SCOPE / "no-front-matter.md").write_text("prose\n", encoding="utf-8")
        after_fixture = _tree_hash(store)
        # A malformed entry no longer raises — it DEGRADES — so this arm now
        # exercises the degraded path instead, which is the one that grew new
        # code and therefore the one that could newly have grown a write.
        rep = rc.recall(store, SCOPE)
        rc.render_text(rep)
        json.dumps(rc.report_json(rep))
        with pytest.raises(rc.EntryUnreadableError):
            # …and the raising arm is kept, on the condition that still raises.
            (store / SCOPE / "broken.md").mkdir()
            rc.recall(store, SCOPE)
        (store / SCOPE / "broken.md").rmdir()
        assert _tree_hash(store) == after_fixture
        assert after_fixture != before  # the fixture moved it; the module did not

    def test_the_module_never_opens_anything_for_writing(self) -> None:
        """The structural half, kept alongside the behavioural one: a reader
        that grew a write path would have to spell it somehow, and every
        spelling below is one nobody should be adding to THIS file."""
        src = MODULE_PATH.read_text(encoding="utf-8")
        code = "\n".join(
            line for line in src.splitlines() if not line.lstrip().startswith("#")
        )
        for spelling in ("write_text(", "open(", "mkdir(", "unlink(", "rename(", "touch("):
            assert spelling not in code, f"the reader grew a write path: {spelling}"


# =============================================================================
# NO SECOND MATCHER, NO SECOND NORMALIZER, NO SECOND SCOPE RULE.
# =============================================================================


class TestSharedPrimitivesAreReused:
    def test_scope_derivation_is_the_WRITERS_function(self) -> None:
        """🔴 A reader and a writer that disagree about the scope directory is a
        silent total failure: the writer accrues entries under one name, the
        reader surfaces an empty scope under another, and that renders as
        "nothing recorded yet"."""
        assert rc.scope_for_repo is st.scope_for_repo

    def test_the_worktree_rule_survives_the_extraction(self, tmp_path: Path) -> None:
        """`scope_for_repo` was extracted out of `subsystem_touch.main` so both
        halves could call it. The behaviour it exists for must be unchanged."""
        assert (
            st.derive_scope("/w/some-repo/.claude/worktrees/agent-abc", "/w/some-repo/.git")
            == "some-repo"
        )

    def test_scope_for_repo_agrees_with_the_cli_it_was_extracted_from(
        self, tmp_path: Path
    ) -> None:
        repo = tmp_path / "some-repo"
        repo.mkdir()
        env = {
            "HOME": str(tmp_path),
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_CONFIG_SYSTEM": "/dev/null",
            # 🔴 maintenance OFF: `_tree_hash` hashes `.git`, so a transient
            # maintenance.lock reads as a repo change (testlib/hermetic_git.py).
            **hermetic_git.MAINTENANCE_OFF,
            "PATH": __import__("os").environ.get("PATH", ""),
        }
        subprocess.run(["git", "-C", str(repo), "init", "-q", "-b", "main"], env=env, check=True)
        assert st.scope_for_repo(repo) == "some-repo"

    def test_normalization_is_the_resolvers(self, store: Path) -> None:
        """The reader normalizes nothing itself: a mixed-case, space-bearing ref
        reaches the entry only because `normalize_ref` folded it."""
        assert rc.recall(store, SCOPE, ref="Status Bar").entries[0].ref == "status-bar"
        assert rc.recall(store, "WORKBENCH_CFG").status == "recalled"

    def test_the_reported_scope_is_the_NORMALIZED_one(self, store: Path) -> None:
        assert rc.recall(store, "WORKBENCH_CFG").scope == SCOPE

    def test_the_module_imports_rather_than_respells(self) -> None:
        src = MODULE_PATH.read_text(encoding="utf-8")
        assert "from subsystem_resolver import" in src
        assert "from subsystem_touch import" in src
        # It must not define its own copies of the shared predicates.
        for forbidden in ("def normalize_ref", "def resolve_ref", "def derive_scope"):
            assert forbidden not in src, f"the reader re-spelled a shared predicate: {forbidden}"

    def test_store_missing_is_the_SAME_class_not_a_second_one(self) -> None:
        assert rc.StoreMissingError is st.StoreMissingError


# =============================================================================
# THE CLI — cheap, non-blocking, no network.
# =============================================================================


class TestCli:
    def test_it_prints_recall_and_exits_zero(self, store: Path, capsys) -> None:
        rc_code = rc.main(["--store", str(store), "--scope", SCOPE])
        out = capsys.readouterr().out
        assert rc_code == 0
        assert "status=recalled" in out
        assert POINTER_LINE in out

    def test_scope_absent_exits_ZERO(self, store: Path, capsys) -> None:
        """🔴 It is the ordinary case. A non-zero exit would train `/resume` to
        treat "this repo is not indexed yet" as a failure."""
        assert rc.main(["--store", str(store), "--scope", "never-indexed"]) == 0
        assert "NOTHING RECORDED YET" in capsys.readouterr().out

    def test_ambiguous_and_absent_refs_exit_ZERO(self, store: Path, capsys) -> None:
        assert rc.main(["--store", str(store), "--scope", SCOPE, "--ref", "weekly-digest"]) == 0
        assert rc.main(["--store", str(store), "--scope", SCOPE, "--ref", "nope"]) == 0

    def test_a_missing_store_exits_3_and_says_why(self, tmp_path: Path, capsys) -> None:
        code = rc.main(["--store", str(tmp_path / "absent"), "--scope", SCOPE])
        assert code == 3
        assert "store root not found" in capsys.readouterr().err

    def test_a_bad_limit_exits_2(self, store: Path, capsys) -> None:
        code = rc.main(["--store", str(store), "--scope", SCOPE, "--limit", "0"])
        assert code == 2
        assert "limit must be an int >= 1" in capsys.readouterr().err

    def test_json_mode_is_parseable(self, store: Path, capsys) -> None:
        assert rc.main(
            ["--store", str(store), "--scope", SCOPE, "--limit", "25", "--json"]
        ) == 0
        blob = json.loads(capsys.readouterr().out)
        assert blob["status"] == "recalled"
        assert blob["label"] == "from index"
        assert len(blob["entries"]) == 4

    def test_the_cli_offers_NO_network_or_write_flag(self) -> None:
        """`/resume` must stay cheap: the reader shares no argument surface with
        the writer's `--pr`, which shells out to `gh`."""
        help_text = rc._build_parser().format_help()
        for forbidden in ("--pr", "--session", "--transcript", "--paths-from", "--template"):
            assert forbidden not in help_text, f"the reader grew {forbidden}"

    def test_the_help_states_the_read_only_contract(self) -> None:
        # ⚠ WHITESPACE-NORMALIZED, and that is a FIX not a loosening. argparse
        # re-wraps the description to the terminal width, so whether a phrase
        # survives as a literal substring depends on `COLUMNS` and on how long
        # the sentence before it happens to be — this assertion went red on a
        # description edit that did not touch either phrase. The claim under
        # test is "the help says these words", never "at this column".
        help_text = " ".join(rc._build_parser().format_help().split())
        assert "never writes to the store" in help_text
        assert "never touches the network" in help_text
        assert "## What it is" in help_text

    def test_list_prints_the_index_and_no_bodies(self, store: Path, capsys) -> None:
        assert rc.main(["--store", str(store), "--scope", SCOPE, "--list"]) == 0
        out = capsys.readouterr().out
        assert "NO ENTRY BODIES WERE PRINTED" in out
        assert POINTER_LINE not in out
        for ref in ("collector", "status-bar", "weekly-digest.process"):
            assert ref in out
        # 🔴 THE FOOTER'S POINTER SENTENCE, PINNED WHOLE — a MUTATION SURVIVOR
        # until this line existed. `--list` was measured to cost a flat +18 B when
        # `## What it is` joined the surfaced set, and this sentence IS that +18 B:
        # dropping `{WHAT_HEADING}` from it left the entire suite green, so the
        # only thing `--list` pays for was pinned nowhere. It is asserted as ONE
        # normalised string and not by keyword, because a guard on a word is
        # walkable by rewording — and the three headings are asserted through the
        # module's own constants, so renaming a heading moves the guard with it.
        assert (
            f"Run `--ref <name>` for one entry's `{rc.WHAT_HEADING}` + "
            f"`{rc.POINTERS_HEADING}` + `{rc.NUANCE_HEADING}`."
        ) in out

    def test_the_bare_default_is_the_DIGEST(self, store: Path, capsys) -> None:
        """🔴 RED AT origin/main: the bare default printed every entry's body."""
        assert rc.main(["--store", str(store), "--scope", SCOPE]) == 0
        out = capsys.readouterr().out
        assert "INDEX (from index)" in out
        assert out.count(POINTER_LINE) == 1, "the bare default printed more than one body"
        assert "FEATURED IN FULL" in out

    @pytest.mark.parametrize(
        "extra,expect",
        [
            (["--ref", "collector"], "select different things"),
            (["--limit", "2"], "never truncated"),
        ],
        ids=["list+ref", "list+limit"],
    )
    def test_list_rejects_the_flags_it_contradicts(
        self, store: Path, capsys, extra, expect
    ) -> None:
        """Rejected, not silently reconciled: each combination has an obvious
        reading and they are DIFFERENT readings."""
        code = rc.main(["--store", str(store), "--scope", SCOPE, "--list", *extra])
        assert code == 2
        assert expect in capsys.readouterr().err

    def test_an_explicit_scope_DISABLES_the_repo_derived_window(
        self, store: Path, tmp_path: Path, capsys
    ) -> None:
        """The window is derived from `--repo`, so it is evidence about THAT
        repo's scope and nothing else. Letting it vote on an overridden scope
        would be a relevance claim built on paths that never described the
        entries they ranked."""
        repo = _make_repo(tmp_path, "handoff-x.md", "`nix/collector/values.yaml`\n")
        assert rc.main(
            ["--store", str(store), "--scope", SCOPE, "--repo", str(repo)]
        ) == 0
        assert "most-recent fallback" in capsys.readouterr().out

    def test_the_cli_really_wires_the_focus_window(self, tmp_path: Path, capsys) -> None:
        """🔴 THE SEAM. Every selection test above injects `focus_paths`; none of
        them proves `main()` ever calls `focus_window`. Unwire that one call and
        they all stay green while the feature is inert. This is the only test
        that builds the combined state: a real repo whose derived scope is the
        store scope, with a handoff doc naming a real entry."""
        repo = tmp_path / SCOPE
        (repo / "claudedocs").mkdir(parents=True)
        (repo / "claudedocs" / "handoff-topic.md").write_text(
            "we touched `nix/status-bar/values.yaml` and `nix/status-bar/config.toml`\n",
            encoding="utf-8",
        )
        env = {
            "HOME": str(tmp_path),
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_CONFIG_SYSTEM": "/dev/null",
            # 🔴 maintenance OFF: `_tree_hash` hashes `.git`, so a transient
            # maintenance.lock reads as a repo change (testlib/hermetic_git.py).
            **hermetic_git.MAINTENANCE_OFF,
            "PATH": __import__("os").environ.get("PATH", ""),
        }
        subprocess.run(["git", "-C", str(repo), "init", "-q", "-b", "main"], env=env, check=True)
        store = _make_store(tmp_path / "s")
        assert rc.main(["--store", str(store), "--repo", str(repo)]) == 0
        out = capsys.readouterr().out
        assert "resolved via claudedocs/handoff-topic.md" in out, out
        assert "### status-bar" in out

    def test_the_default_store_is_the_writers_default(self) -> None:
        """Asserted on the parser's DEFAULT, not on its help text: the help
        string does not interpolate it, so a check against the text would pass
        for any default at all."""
        assert rc._build_parser().get_default("store") == str(st.DEFAULT_STORE_ROOT)


class TestSearchAndPageCli:
    """The two new surfaces at the CLI. Every rejection below is REJECTED rather
    than silently reconciled: a flag that quietly does nothing is how a caller
    comes to believe a setting took effect."""

    def test_search_prints_hunks_and_exits_zero(self, tmp_path: Path, capsys) -> None:
        store = _make_search_store(tmp_path)
        assert rc.main(["--store", str(store), "--scope", SEARCH_SCOPE, "-s", "compaction"]) == 0
        out = capsys.readouterr().out
        assert "status=search-hit" in out
        assert "compaction pass stalls" in out
        assert "sensitivity=client-confidential" in out

    def test_a_no_match_exits_ZERO(self, tmp_path: Path, capsys) -> None:
        """🔴 Non-blocking, like every other reading here: a query that matched
        nothing is an ANSWER, and a non-zero exit would train a caller to treat it
        as a broken tool."""
        store = _make_search_store(tmp_path)
        assert rc.main(["--store", str(store), "--scope", SEARCH_SCOPE, "-s", "kryptonite"]) == 0
        assert "NO MATCH" in capsys.readouterr().out

    def test_search_json_is_parseable(self, tmp_path: Path, capsys) -> None:
        store = _make_search_store(tmp_path)
        assert rc.main(
            ["--store", str(store), "--scope", SEARCH_SCOPE, "-s", "compaction", "--json"]
        ) == 0
        blob = json.loads(capsys.readouterr().out)
        assert blob["status"] == "search-hit"
        assert blob["hunks"][0]["ref"] == "blob-vault"

    def test_bad_search_arguments_exit_2_with_their_OWN_sentinel(
        self, tmp_path: Path, capsys
    ) -> None:
        store = _make_search_store(tmp_path)
        base = ["--store", str(store), "--scope", SEARCH_SCOPE]
        for extra, sentinel in (
            (["-s", "   "], "query must be a non-empty string"),
            (["-s", "x", "--threshold", "2"], "threshold must be a number in [0, 1]"),
            (["-s", "x", "--max-hits", "0"], "max-hits must be an int >= 1"),
            (["-s", "x", "-C", "-3"], "context must be an int >= 0"),
            (["--page", "0"], "page must be an int >= 1"),
        ):
            assert rc.main(base + extra) == 2, extra
            assert sentinel in capsys.readouterr().err, extra

    @pytest.mark.parametrize(
        "extra,expect",
        [
            (["-s", "x", "--ref", "collector"], "select different things"),
            (["-s", "x", "--list"], "select different things"),
            (["--page", "2", "--ref", "collector"], "print no index"),
            (["--page", "2", "--limit", "3"], "print no index"),
            (["-C", "2"], "only mean something with --search"),
            (["--all-scopes"], "only mean something with --search"),
            (["--threshold", "0.9"], "only mean something with --search"),
            (["--max-hits", "3"], "only mean something with --search"),
        ],
        ids=[
            "search+ref",
            "search+list",
            "page+ref",
            "page+limit",
            "context-alone",
            "all-scopes-alone",
            "threshold-alone",
            "max-hits-alone",
        ],
    )
    def test_contradictory_flags_are_REJECTED(
        self, store: Path, capsys, extra, expect
    ) -> None:
        code = rc.main(["--store", str(store), "--scope", SCOPE, *extra])
        assert code == 2
        assert expect in capsys.readouterr().err

    def test_page_reaches_the_older_entries_from_the_CLI(
        self, tmp_path: Path, capsys
    ) -> None:
        """🔴 THE SEAM. Every pagination test above calls `recall` directly; none
        of them proves `main()` ever passes `--page` through. Unwire that one
        argument and they all stay green while the flag is inert."""
        store = _make_paged_store(tmp_path)
        assert rc.main(["--store", str(store), "--scope", PAGED_SCOPE, "--list"]) == 0
        page1 = capsys.readouterr().out
        assert rc.main(
            ["--store", str(store), "--scope", PAGED_SCOPE, "--list", "--page", "2"]
        ) == 0
        page2 = capsys.readouterr().out
        assert "gizmo-001" not in page1 and "gizmo-001" in page2
        assert f"gizmo-{PAGED_N:03d}" in page1 and f"gizmo-{PAGED_N:03d}" not in page2

    def test_all_scopes_reaches_the_store_from_the_CLI(
        self, tmp_path: Path, capsys
    ) -> None:
        """The same seam for `--all-scopes`."""
        store = _make_search_store(tmp_path)
        (store / OTHER_SCOPE).mkdir()
        (store / OTHER_SCOPE / "fan-curve.md").write_text(
            _entry("fan-curve", OTHER_SCOPE, nuance="- the compaction pass is elsewhere."),
            encoding="utf-8",
        )
        base = ["--store", str(store), "--scope", SEARCH_SCOPE, "-s", "compaction"]
        assert rc.main(base) == 0
        assert "fan-curve" not in capsys.readouterr().out
        assert rc.main(base + ["--all-scopes"]) == 0
        assert "fan-curve" in capsys.readouterr().out

    @staticmethod
    def _flat_help() -> str:
        """argparse REWRAPS every help string, so a phrase longer than the
        remaining column width arrives split across lines. Matching the raw text
        would make these assertions depend on terminal width — they would pass
        here and fail in CI for a reason having nothing to do with the sentence."""
        return " ".join(rc._build_parser().format_help().split())

    def test_the_help_explains_WHY_the_bullet_is_the_default_context(self) -> None:
        """🔴 Zach asked for the agent to DRIVE `-C`, which it can only do if the
        tradeoff is stated in prose rather than left to be inferred from a
        default."""
        help_text = self._flat_help()
        assert "ENCLOSING BULLET" in help_text
        assert "reads like a complete instruction when it is not" in help_text
        assert "The tradeoff is yours" in help_text

    def test_the_help_states_the_page_ORDER(self) -> None:
        help_text = self._flat_help()
        assert "NEWEST-FIRST by entry-file mtime" in help_text
        assert f"capped at {rc.LISTING_PAGE_SIZE} lines per page" in help_text

    def test_search_adds_NO_network_or_write_flag(self) -> None:
        help_text = rc._build_parser().format_help()
        for forbidden in ("--pr", "--session", "--transcript", "--paths-from", "--template"):
            assert forbidden not in help_text, f"the reader grew {forbidden}"


class TestNoRealStoreIsRead:
    """🔴 No test here may touch `~/.claude/analyze-service-index/`."""

    def test_the_real_store_root_is_outside_this_repo(self) -> None:
        assert ROOT not in st.DEFAULT_STORE_ROOT.parents
        assert not str(st.DEFAULT_STORE_ROOT).startswith(str(ROOT))

    def test_every_call_in_this_file_passes_an_explicit_store(self) -> None:
        """The module's own default is never exercised, so a test cannot reach
        the live store even by accident.

        The forbidden spellings are ASSEMBLED rather than written literally: a
        source file that greps itself matches its own assertion, and this test
        failed exactly that way before the concatenation."""
        src = Path(__file__).read_text(encoding="utf-8")
        forbidden = ["rc.recall(" + "st.DEFAULT_STORE_ROOT", "rc.main(" + "[])"]
        for spelling in forbidden:
            assert spelling not in src, f"a test may reach the LIVE store: {spelling}"
        # and the live path itself never appears as a literal
        assert str(st.DEFAULT_STORE_ROOT) not in src


# =============================================================================
# PART 3 — THE APPEND-CONCURRENCY MEASUREMENT.
# =============================================================================
#
# 🔴 THIS IS A MEASUREMENT OF THE WRITE PROTOCOL, PINNED. `/handoff` mandates
# `Edit` anchored on `## Nuance / work-history` rather than `Write`, and the
# rationale asserted — from reading tool semantics, never measured — that a
# second session's `Edit` "cannot silently clobber, because its `old_string` no
# longer matches once the first inserts a bullet, so it fails loudly".
#
# HALF OF THAT IS FALSE, and these tests are why the skill now says so:
#
#   * "cannot silently clobber"  TRUE — but because `Edit` rewrites only the
#                                MATCHED REGION, not because the anchor breaks.
#   * "fails loudly"             FALSE for the anchor the protocol NAMES. With
#                                the bare heading as `old_string`, the second
#                                Edit SUCCEEDS SILENTLY and both bullets land.
#                                It errors only when the anchor SPANS the
#                                insertion point, or when the first insert makes
#                                the anchor non-unique.
#
# The consequence for the protocol is that "no error" is not evidence you were
# alone, so the re-read-and-re-apply step is the real safeguard.

ENTRY_FIXTURE = _entry("collector", SCOPE)
HEADER = rc.NUANCE_HEADING + "\n"
FIRST_BULLET = NUANCE_LINE + "\n"


class _EditError(RuntimeError):
    pass


def _edit_tool(path: Path, old: str, new: str) -> None:
    """The documented `Edit` semantics: exact match, MUST be unique, else error.

    Modelled rather than driven, because the real tool is not callable from a
    test. The two properties modelled are the two the protocol leans on and both
    are documented behaviour: a non-matching `old_string` errors, and a
    non-unique one errors."""
    text = path.read_text(encoding="utf-8")
    n = text.count(old)
    if n == 0:
        raise _EditError("String to replace not found in file")
    if n > 1:
        raise _EditError(f"Found {n} matches of the string to replace; must be unique")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def _write_tool(path: Path, content: str) -> None:
    """Whole-file overwrite: no preconditions, no failure mode."""
    path.write_text(content, encoding="utf-8")


@pytest.fixture()
def entry_file(tmp_path: Path) -> Path:
    p = tmp_path / "collector.md"
    p.write_text(ENTRY_FIXTURE, encoding="utf-8")
    return p


class TestAppendConcurrency:
    def test_the_edit_model_can_go_red(self, entry_file: Path) -> None:
        """🔴 Negative control on the INSTRUMENT. A model of `Edit` that never
        raises would make every test below vacuously green."""
        with pytest.raises(_EditError, match="not found"):
            _edit_tool(entry_file, "text that is not in the file", "x")
        entry_file.write_text("dup\ndup\n", encoding="utf-8")
        with pytest.raises(_EditError, match="must be unique"):
            _edit_tool(entry_file, "dup\n", "x")

    def test_the_edit_model_can_succeed(self, entry_file: Path) -> None:
        """Positive control on the same instrument."""
        _edit_tool(entry_file, HEADER, HEADER + "- 2026-08-12: S1.\n")
        assert "- 2026-08-12: S1." in entry_file.read_text(encoding="utf-8")

    def test_WRITE_silently_loses_the_first_sessions_bullet(self, entry_file: Path) -> None:
        """🔴 HALF ONE OF THE PAIR. Both sessions read before either wrote."""
        s1_read = entry_file.read_text(encoding="utf-8")
        s2_read = entry_file.read_text(encoding="utf-8")
        _write_tool(entry_file, s1_read.replace(HEADER, HEADER + "- 2026-08-12: S1.\n"))
        _write_tool(entry_file, s2_read.replace(HEADER, HEADER + "- 2026-08-12: S2.\n"))
        final = entry_file.read_text(encoding="utf-8")
        assert "- 2026-08-12: S2." in final
        assert "- 2026-08-12: S1." not in final, "Write did not clobber — the pair is wrong"
        # and NOTHING raised: the loss is silent.

    def test_EDIT_on_the_named_anchor_keeps_BOTH_and_does_NOT_raise(
        self, entry_file: Path
    ) -> None:
        """🔴 HALF TWO OF THE PAIR, and the half that refutes the claim as
        stated. `## Nuance / work-history` survives the first insert, so the
        second Edit matches, succeeds, and says nothing."""
        _edit_tool(entry_file, HEADER, HEADER + "- 2026-08-12: S1.\n")
        _edit_tool(entry_file, HEADER, HEADER + "- 2026-08-12: S2.\n")  # must NOT raise
        final = entry_file.read_text(encoding="utf-8")
        assert "- 2026-08-12: S1." in final
        assert "- 2026-08-12: S2." in final
        assert NUANCE_LINE in final, "the pre-existing bullet must survive too"

    def test_EDIT_fails_loudly_ONLY_when_the_anchor_spans_the_insertion_point(
        self, entry_file: Path
    ) -> None:
        """The case the original claim describes — real, but not the one the
        protocol's wording produces."""
        anchor = HEADER + FIRST_BULLET
        _edit_tool(entry_file, anchor, HEADER + "- 2026-08-12: S1.\n" + FIRST_BULLET)
        with pytest.raises(_EditError, match="not found"):
            _edit_tool(entry_file, anchor, HEADER + "- 2026-08-12: S2.\n" + FIRST_BULLET)
        final = entry_file.read_text(encoding="utf-8")
        assert "- 2026-08-12: S1." in final
        assert "- 2026-08-12: S2." not in final

    def test_EDIT_also_fails_when_the_first_insert_makes_the_anchor_NON_UNIQUE(
        self, entry_file: Path
    ) -> None:
        _edit_tool(entry_file, HEADER, HEADER + FIRST_BULLET)  # a duplicate bullet
        with pytest.raises(_EditError, match="must be unique"):
            _edit_tool(entry_file, FIRST_BULLET, FIRST_BULLET + "- 2026-08-12: S2.\n")

    def test_DIFFERENT_anchors_in_one_file_do_not_collide_at_all(
        self, entry_file: Path
    ) -> None:
        """The case the brief asked about: they do not interact. Both land, and
        neither writer learns the other was there."""
        _edit_tool(entry_file, HEADER + FIRST_BULLET, HEADER + "- 2026-08-12: S1.\n" + FIRST_BULLET)
        _edit_tool(entry_file, POINTER_LINE + "\n", POINTER_LINE + "\n- a second pointer\n")
        final = entry_file.read_text(encoding="utf-8")
        assert "- 2026-08-12: S1." in final
        assert "- a second pointer" in final

    def test_the_measured_property_is_BOUNDEDNESS_not_a_broken_anchor(
        self, entry_file: Path
    ) -> None:
        """🔴 The claim that survives measurement, stated as an invariant: an
        `Edit` rewrites only the matched region, so bytes outside it — including
        another session's concurrent append — are never discarded."""
        _edit_tool(entry_file, HEADER, HEADER + "- 2026-08-12: S1.\n")
        before = entry_file.read_text(encoding="utf-8")
        _edit_tool(entry_file, POINTER_LINE + "\n", POINTER_LINE + "\n- another\n")
        after = entry_file.read_text(encoding="utf-8")
        # every line that was there is still there
        assert set(before.splitlines()) <= set(after.splitlines())

    def test_the_stores_autocommit_cannot_disturb_an_anchor(self, tmp_path: Path) -> None:
        """🔴 Measured rather than reasoned: the hourly autocommit runs `add` and
        `commit` only — no `checkout`, `reset`, `restore` or `stash` — so it
        rewrites no working-tree byte and the anchor is untouched."""
        commit_sh = (ROOT / "scripts" / "analyze-service-index" / "commit.sh").read_text(
            encoding="utf-8"
        )
        code = "\n".join(
            line for line in commit_sh.splitlines() if not line.lstrip().startswith("#")
        )
        for destructive in ("checkout", "reset", "restore", "stash", "clean"):
            assert f"git -C \"$scope\" {destructive}" not in code
            assert f"git {destructive}" not in code

        repo = tmp_path / "scope"
        repo.mkdir()
        env = {
            "HOME": str(tmp_path),
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_CONFIG_SYSTEM": "/dev/null",
            # 🔴 maintenance OFF: `_tree_hash` hashes `.git`, so a transient
            # maintenance.lock reads as a repo change (testlib/hermetic_git.py).
            **hermetic_git.MAINTENANCE_OFF,
            "GIT_AUTHOR_NAME": "T",
            "GIT_AUTHOR_EMAIL": "t@example.invalid",
            "GIT_COMMITTER_NAME": "T",
            "GIT_COMMITTER_EMAIL": "t@example.invalid",
            "PATH": __import__("os").environ.get("PATH", ""),
        }

        def g(*a):
            subprocess.run(["git", "-C", str(repo), *a], env=env, check=True,
                           capture_output=True)

        p = repo / "collector.md"
        p.write_text(ENTRY_FIXTURE, encoding="utf-8")
        g("init", "-q", "-b", "main")
        g("add", "collector.md")
        g("commit", "-qm", "seed")
        _edit_tool(p, HEADER, HEADER + "- 2026-08-12: S1.\n")
        after_edit = p.read_bytes()
        g("add", "collector.md")
        g("commit", "-qm", "autocommit")
        assert p.read_bytes() == after_edit, "the commit rewrote the working tree"
        _edit_tool(p, HEADER, HEADER + "- 2026-08-12: S2.\n")  # anchor still intact


# =============================================================================
# THE SKILLS — the reader is inert unless /resume calls it.
# =============================================================================


class TestSkillDocsArePinned:
    """A tested module nothing invokes is exactly the failure the decision doc
    measured (six commands never invoked once). The protocol lives in prose
    because its executor is an LLM."""

    RESUME_SENTENCES: list[tuple[str, str]] = [
        ("scripts/lib/subsystem_recall.py", "the step actually calls this module"),
        (
            "read half",
            "why the step exists — the store had two writers and no reader",
        ),
        (
            "outlives the handoff doc you just read",
            "the store's stated purpose, which is what makes it worth reading here",
        ),
        (
            "RECALL, NEVER LIVE OBSERVATION",
            "🔴 the crux: index content must never be presented as measured",
        ),
        ("`from index`", "the provenance label, matching /analyze-service"),
        (
            "pointer to verify",
            "what an index bullet is FOR — not an answer",
        ),
        (
            "Never fold it into the live-state findings",
            "recall and the reconciler's measurements stay separate",
        ),
        (
            "NOTHING RECORDED YET",
            "the ordinary case, named as the tool names it",
        ),
        (
            "not an error and not a clean bill of health",
            "🔴 an empty scope must not read as an absence of drift",
        ),
        (
            "Non-blocking, always",
            "/resume's job is to re-enter work, not to block on recall",
        ),
        (
            "continue the resume",
            "what to do when the reader fails — a broken index is not a stop",
        ),
        (
            "Never fall back to recollection",
            "the failure mode an unavailable index invites",
        ),
        (
            "do not go create an entry",
            "writing is /handoff's confirm-gated job, not this step's",
        ),
        ("sensitivity=", "the store is client-confidential and this repo is PUBLIC"),
        # --- the digest, and the claim it replaced -------------------------
        (
            "never truncated",
            "🔴 the index is the one COMPLETE thing the digest offers",
        ),
        (
            "exactly ONE entry in full",
            "what the bare command now prints — the step's whole cost model",
        ),
        (
            "the output names the basis",
            "🔴 a featured entry with no stated basis reads as a relevance claim",
        ),
        (
            "most-recent fallback",
            "the phrase the tool prints when nothing resolved — the agent must recognise it",
        ),
        (
            "a fallback pick says nothing whatsoever about relevance",
            "🔴 the misreading the fallback invites, said out loud",
        ),
        (
            "`--list` prints the index alone",
            "the drill-down path that replaces raising the display cap",
        ),
        (
            "was false for the only scope big enough to matter",
            "🔴 the retracted 'costs a page' claim, retracted rather than quietly reworded",
        ),
        # --- search + pagination -------------------------------------------
        (
            "capped at 100 lines per page",
            "the index cap — the step must know the default output can be a PAGE",
        ),
        (
            "newest-first by entry-file mtime",
            "🔴 the page order, which is what makes the cut mean anything",
        ),
        (
            "reads by MATCH instead of by whole entry",
            "what --search is FOR — reading one fact rather than an orientation",
        ),
        (
            "score beside the threshold",
            "🔴 a fuzzy score with no threshold next to it cannot be read as weak",
        ),
        (
            "A no-match is not an empty screen",
            "🔴 the empty-result rule: an absent term and a near miss are different facts",
        ),
        (
            "basis=entry-name",
            "a name-only hit is not a content hit, and only the basis says so",
        ),
        (
            "Context is the enclosing bullet by default",
            "the -C default, and the agent has to know it is overridable",
        ),
        (
            "reads like a complete instruction",
            "🔴 WHY the bullet is the default — the tradeoff -C hands to the agent",
        ),
        (
            "not* entry size",
            "the index count names its section; `bullets` read as entry size",
        ),
        (
            "the fail-safe overrode it",
            "🔴 an overridden sensitivity marker is shown, never silently rewritten",
        ),
        # --- the three badges added with items 2 and 1 of the entry-shape
        #     proposal. Each pins the CONSEQUENCE, not the glyph: a step that
        #     lists a badge without saying what it implies about the numbers
        #     beside it has documented a decoration.
        (
            "`N OPEN` is **short by up to N**",
            "🔴 what a NEAR-MISS badge implies about the OPEN count on the same row",
        ),
        (
            "the closure cannot be checked",
            "what UNVERIFIABLE means — a real closure, just not a checkable one",
        ),
        (
            "0 by parse failure, not by measurement",
            "🔴 a `NO <heading>` row's counts are not readings, and the step must not "
            "report such an entry as empty",
        ),
    ]

    def test_the_RETRACTED_cost_claim_is_not_reasserted(self) -> None:
        """🔴 NEGATIVE CONTROL ON THE CORRECTION. "it costs a page, not a dump"
        was measured FALSE for `datapacket-talos` (31,485 B, and incomplete). The
        sentence may appear only as the thing being retracted, never as a live
        claim — so the bare phrase must not occur without the retraction beside
        it."""
        doc = RESUME_DOC.read_text(encoding="utf-8")
        if "costs a page, not a dump" in doc:
            assert "was false" in doc, (
                "claude/skills/resume/SKILL.md asserts 'costs a page, not a dump' again "
                "without retracting it. It was measured false on the scope holding 25 of "
                "the store's 29 entries."
            )

    def test_the_documented_flags_EXIST(self) -> None:
        """🔴 A skill is prose whose executor is an LLM, so a flag it names that
        the CLI does not have is an instruction to type a command that fails.
        Derived from the parser, not hand-listed."""
        doc = RESUME_DOC.read_text(encoding="utf-8")
        help_text = rc._build_parser().format_help()
        for flag in ("--list", "--ref", "--limit", "--page", "--search", "-C ", "--all-scopes", "--max-hits"):
            assert flag in doc, f"step 4 stopped documenting {flag}"
            assert flag in help_text, f"step 4 documents {flag}, which the CLI does not have"

    @pytest.mark.parametrize(
        "sentence,why", RESUME_SENTENCES, ids=[w for _, w in RESUME_SENTENCES]
    )
    def test_resume_step_sentence(self, sentence: str, why: str) -> None:
        doc = RESUME_DOC.read_text(encoding="utf-8")
        assert sentence in doc, (
            f"claude/skills/resume/SKILL.md no longer contains the sentence pinning {why}.\n"
            f"  missing: {sentence!r}\n"
            f"  Either restore it or change scripts/lib/subsystem_recall.py in the SAME\n"
            f"  commit. The module cannot enforce a protocol its only caller stopped\n"
            f"  following, and the drift is silent: the step simply stops happening."
        )

    def test_EVERY_emitted_status_the_step_must_handle_is_named(self) -> None:
        """Derived from the module, not hand-listed. The two "nothing recorded"
        statuses are the ones a resuming agent will actually meet."""
        doc = RESUME_DOC.read_text(encoding="utf-8")
        for status in ("scope-absent", "scope-empty", "scope-unreadable"):
            assert f"`{status}`" in doc, (
                f"claude/skills/resume/SKILL.md never mentions `{status}`, which "
                f"subsystem_recall.recall can emit and which the step must not "
                f"report as an error."
            )

    def test_the_recall_step_comes_AFTER_the_handoff_is_read(self) -> None:
        """Structural, not a phrase: recall is context for a doc already read,
        not a substitute for reading it.

        ⚠ RED ON `main` FROM #643 UNTIL THIS COMMIT, and nothing caught it — the
        repo has no automated merge gate. #643 extended step 2's imperative from
        `**Read it fully.**` to `**Read it fully — but treat its "Open
        investigations" section as RECALL, not live state.**`, a legitimate
        reword, and this pin was anchored on the sentence INCLUDING its full
        stop. That is the wrong anchor for this test: what it asserts is an
        ORDERING of four steps, so it should hold the imperative that opens the
        step and let the rest of the sentence evolve. The sentence's WORDING is
        somebody else's pin (`_assert_rationale_pin` above); this one is about
        position. Asserted unique below so the index cannot silently move.
        ⚠ THE RECONCILE STEP MOVED, DELIBERATELY, AND THIS TEST ASSERTED THE OLD
        ORDER. It used to require `read_it < reconcile`. `resume-state.sh` now
        compares the handoff against `origin/<default-branch>` and decides which
        copy is authoritative, so it has to run BEFORE the doc is read —
        otherwise the agent reads the working-tree copy first, which is the
        stale-handoff defect (a clone served one 276 lines behind origin/trunk,
        and the whole session was framed on it).

        So the order is now reconcile < read_it, pinned here so the swap cannot
        be silently undone. The invariant this test is named for is untouched:
        recall still comes after the doc is read.
        """
        doc = RESUME_DOC.read_text(encoding="utf-8")
        # Anchor on the imperative that OPENS the step, never the full sentence:
        # this test asserts an ORDERING, so the rest of the sentence must be free
        # to evolve (a reword inside the bold span turned `main` RED once).
        # Uniqueness is asserted because `.index()` would otherwise return
        # whichever occurrence came first and the ordering claim could invert.
        assert doc.count("**Read the handoff in full") == 1, (
            "the ordering anchor is no longer unique — `.index()` would return "
            "whichever came first and the ordering claim could invert"
        )
        read_it = doc.index("**Read the handoff in full")
        # The INVOCATION, not the bare filename: `resume-state.sh` is also named
        # in step 1's prose, so `.index()` on the bare name finds a point BEFORE
        # the handoff is read and the ordering claim inverts.
        reconcile = doc.index("bash ~/workspace/devrc/scripts/resume-state.sh")
        step = doc.index("subsystem_recall.py")
        report = doc.index("**Report**")
        assert reconcile < read_it < step < report

    def test_the_pin_can_report_absence(self) -> None:
        """Negative control on the pin: a check against a doc that happens to
        contain everything is indistinguishable from one pointed at the wrong
        file."""
        doc = RESUME_DOC.read_text(encoding="utf-8")
        assert "a sentence deliberately absent from the resume skill" not in doc

    def test_the_pinned_doc_is_the_DEPLOYED_one(self) -> None:
        assert RESUME_DOC.exists()
        assert RESUME_DOC.name == "SKILL.md"
        assert RESUME_DOC.parent.parent.name == "skills"
        # Shared with test_subsystem_resolver/_touch. It checks only that the
        # mapping is DECLARED and not switched off (`enable = false`, redirected
        # `target`) — whether the source RESOLVES to this tree is measured
        # against the real filesystem at deploy time by ship.sh/drift-check.sh.
        assert_skills_mapping_declared(ROOT / "nix" / "home.nix")

    # 🔴 THE CONCURRENCY FINDING WAS SPLIT ACROSS TWO FILES, so this pin table is
    # split the same way. Step 4 keeps every IMPERATIVE; the measured evidence
    # moved to the `reference/index-write.md` sidecar, which costs nothing until
    # it is read. A pin has to MOVE WITH ITS SENTENCE: one left pointing at
    # SKILL.md after the sentence went to the sidecar would pass or fail for the
    # wrong file, and a pin that quietly stops covering anything is worse than no
    # pin. `test_a_reworded_pin_is_still_caught_in_its_new_home` is the positive
    # control that these still bite where they now point.
    HANDOFF_CORRECTIONS_SKILL: list[tuple[str, str]] = [
        (
            "do not treat \"no error\" as evidence you were alone",
            "the operational consequence of the retraction — an INSTRUCTION, so it stays in the body",
        ),
    ]

    HANDOFF_CORRECTIONS_REFERENCE: list[tuple[str, str]] = [
        (
            "the protection is that `Edit` is BOUNDED, not that a stale anchor fails",
            "🔴 the corrected mechanism — measured, not reasoned",
        ),
        (
            "It does NOT reliably fail loudly",
            "the retraction, stated rather than quietly softened",
        ),
        (
            "the second `Edit` **succeeds silently**",
            "what actually happens on the anchor the protocol names",
        ),
        (
            "rewrites no working-tree byte",
            "the autocommit cannot disturb an anchor — measured",
        ),
    ]

    @staticmethod
    def _assert_rationale_pin(text: str, sentence: str, why: str, where: str) -> None:
        """The pin predicate, as ONE function so the positive control below can
        exercise the same code the real pins run."""
        assert sentence in text, (
            f"{where} no longer states {why}.\n"
            f"  missing: {sentence!r}\n"
            f"  The earlier rationale asserted the second Edit 'fails loudly'; "
            f"TestAppendConcurrency in this file measures that it does not, for the "
            f"anchor the protocol names. Do not restore the unmeasured wording.\n"
            f"  If you MOVED this sentence, move its pin too — a pin left behind "
            f"stops asserting anything."
        )

    @pytest.mark.parametrize(
        "sentence,why",
        HANDOFF_CORRECTIONS_SKILL,
        ids=[w for _, w in HANDOFF_CORRECTIONS_SKILL],
    )
    def test_handoff_rationale_INSTRUCTION_stays_in_the_body(
        self, sentence: str, why: str
    ) -> None:
        self._assert_rationale_pin(
            INDEX_DOC.read_text(encoding="utf-8"),
            sentence,
            why,
            "claude/skills/subsystem-index/SKILL.md",
        )

    @pytest.mark.parametrize(
        "sentence,why",
        HANDOFF_CORRECTIONS_REFERENCE,
        ids=[w for _, w in HANDOFF_CORRECTIONS_REFERENCE],
    )
    def test_handoff_rationale_is_the_MEASURED_one(self, sentence: str, why: str) -> None:
        self._assert_rationale_pin(
            HANDOFF_REFERENCE.read_text(encoding="utf-8"),
            sentence,
            why,
            "claude/skills/subsystem-index/reference/index-write.md",
        )

    def test_a_reworded_pin_is_still_caught_in_its_new_home(self) -> None:
        """🔴 POSITIVE CONTROL on the move itself. The failure this guards against
        is not "the sentence vanished" but "the pin followed it and went inert".

        Rewording each pinned sentence IN the sidecar's real text must make the
        SAME predicate the real tests use go red — proving the pins bite against
        the file they now point at, not merely that some file somewhere contains
        the words."""
        real = HANDOFF_REFERENCE.read_text(encoding="utf-8")
        for sentence, why in self.HANDOFF_CORRECTIONS_REFERENCE:
            assert sentence in real, "precondition: the pin passes on the real text"
            # A plausible REWORDING, not a deletion — the drift that actually
            # happens when someone "tightens" a paragraph.
            reworded = real.replace(sentence, "the mechanism is described elsewhere")
            assert reworded != real, f"the rewrite was a no-op for {sentence!r}"
            with pytest.raises(AssertionError) as caught:
                self._assert_rationale_pin(
                    reworded, sentence, why, "the reworded sidecar"
                )
            assert sentence in str(caught.value), (
                "the failure must name the missing sentence, or a maintainer "
                "cannot tell which pin broke"
            )

    def test_the_reference_pin_can_report_absence(self) -> None:
        """Negative control on the sidecar pin, matching the one over SKILL.md: a
        check against a doc that happens to contain everything is
        indistinguishable from one pointed at the wrong file."""
        text = HANDOFF_REFERENCE.read_text(encoding="utf-8")
        assert "a sentence deliberately absent from the handoff reference" not in text

    def test_the_body_ROUTES_to_the_sidecar_and_the_target_exists(self) -> None:
        """Splitting rationale out is only safe if the body still points at it AND
        the pointer resolves. Nothing else in the gate checks that any skill's
        `reference/` link resolves, so an evidence file could be deleted or
        renamed and the body would keep advertising it.

        🔴 Reads INDEX_DOC since 2026-08-24: the sidecar explains the INDEX
        protocol, which is no longer /handoff's to route to. /handoff's own seam
        — that it still names the `subsystem-index` skill at all — is asserted by
        `test_handoff_still_routes_to_the_index_skill` in test_subsystem_touch.py."""
        doc = INDEX_DOC.read_text(encoding="utf-8")
        assert "reference/index-write.md" in doc, (
            "claude/skills/subsystem-index/SKILL.md no longer routes to its evidence "
            "sidecar — the rules would be left looking arbitrary with nowhere "
            "to check them."
        )
        assert HANDOFF_REFERENCE.exists(), f"the routed-to sidecar is gone: {HANDOFF_REFERENCE}"
        assert HANDOFF_REFERENCE.parent.name == "reference"
        # The sidecar must live beside the body that ROUTES to it — which is
        # INDEX_DOC since the 2026-08-24 extraction, not HANDOFF_DOC. Asserted
        # structurally rather than as a literal path so a future move of the
        # whole skill directory keeps this honest.
        assert HANDOFF_REFERENCE.parent.parent == INDEX_DOC.parent

    def test_the_sidecar_is_DEPLOYED(self) -> None:
        """`home.file` ships `claude/skills` wholesale, so the sidecar reaches
        `~/.claude/skills/handoff/reference/` only if it is in the FLAKE SOURCE —
        and the flake source is the git-TRACKED tree, so an untracked file is
        silently absent from the deploy while the body keeps pointing at it.

        🔴 TWO TIERS, TWO PROOFS, AND NEITHER IS A SKIP. The first version of
        this test asked git unconditionally and went RED in the nix sandbox,
        which has no `.git` at all — a test that passes on the host and fails
        under the authoritative gate.

          * SANDBOX (no `.git`): the file being here AT ALL is the stronger
            evidence, because the only way it reached this tree is that the
            flake copied it, and the flake copies tracked files.
          * DEV HOST (`.git` present): ask git directly. This is the only tier
            that can catch added-then-untracked before it ever reaches a build.

        An untracking mutation is caught in BOTH: the sandbox loses the file,
        the host loses the `ls-files` hit.
        """
        # Same predicate as the three sites consolidated above. DECLARED-only:
        # see testlib/skills_mapping.py for what it deliberately no longer
        # traces, and which deploy-time check covers that instead.
        assert_skills_mapping_declared(ROOT / "nix" / "home.nix")
        assert HANDOFF_REFERENCE.exists(), (
            f"{HANDOFF_REFERENCE} is absent from the tree under test. In the nix "
            f"sandbox that means it was never git-added, so the deploy omits it."
        )
        if not (ROOT / ".git").exists():
            return  # sandbox tier: the assertion above IS the tracked-ness proof
        tracked = subprocess.run(
            ["git", "ls-files", "--error-unmatch", str(HANDOFF_REFERENCE.relative_to(ROOT))],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        assert tracked.returncode == 0, (
            f"{HANDOFF_REFERENCE.relative_to(ROOT)} is not tracked by git, so the "
            f"flake omits it from the deploy and the body's pointer dangles on "
            f"every host.\n{tracked.stderr}"
        )

    def test_the_retracted_claim_is_NOT_still_asserted(self) -> None:
        """The half that had to GO. A correction that only adds text leaves the
        false sentence in front of the executor — and after the split it must be
        absent from EVERY file that could reassert it, or moving the rationale
        would have reopened it.

        🔴 INDEX_DOC WAS MISSING FROM THIS TUPLE AFTER THE 2026-08-24 EXTRACTION,
        and an audit proved it by mutation: appending the retracted sentence to
        `subsystem-index/SKILL.md` SURVIVED a full green suite. The guard kept
        checking the file the prose had LEFT. That is `claude/RULES.md`'s
        "a guard's DESCRIPTION claims COVERAGE" arriving through a file move
        rather than an edit — the docstring already said BOTH/EVERY; the body
        had silently narrowed to the wrong two."""
        for path in (HANDOFF_DOC, HANDOFF_REFERENCE, INDEX_DOC):
            assert "fails loudly rather than clobbering" not in path.read_text(
                encoding="utf-8"
            ), f"the retracted wording is back in {path}"


# =============================================================================
# THE MUTATION KILL MATRIX.
# =============================================================================


def _load_mutant(tmp_path: Path, name: str, replacements: list[tuple[str, str]]):
    """Import a copy of the module with the named guard(s) neutered.

    The anchor-uniqueness assert is not decoration: `claude/RULES.md` — "a
    count=1 text replace on a pattern that occurs more than once is a live
    hazard"; a mutation applied to the wrong occurrence produces a mutant that is
    green for reasons nobody inspected."""
    src = MODULE_PATH.read_text(encoding="utf-8")
    for old, new in replacements:
        n = src.count(old)
        assert n == 1, f"mutation anchor occurs {n}x, expected exactly 1: {old!r}"
        src = src.replace(old, new)
    path = tmp_path / f"{name}.py"
    path.write_text(src, encoding="utf-8")
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Registered BEFORE exec_module and left registered: `@dataclass` resolves
    # string annotations by looking the defining class's module up in
    # `sys.modules`.
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    return module


class TestMutationKillMatrix:
    """Each test deletes ONE guard and asserts the expectation above it dies —
    with THIS guard's own symptom, not a neighbour's error."""

    def test_the_mutant_loader_itself_works(self, tmp_path: Path) -> None:
        """Positive control on the INSTRUMENT: an unmutated copy must behave
        exactly like the real module, or every kill below is meaningless."""
        mod = _load_mutant(tmp_path, "m_control", [])
        store = _make_store(tmp_path / "s")
        assert mod.recall(store, SCOPE).status == "recalled"
        assert len(mod.recall(store, SCOPE, mode="full").entries) == 4
        # …and the digest half of the same control, or every digest kill below
        # would be measured through a mode this instrument never exercised.
        assert len(mod.recall(store, SCOPE).listing) == 4
        assert len(mod.recall(store, SCOPE).entries) == 1

    def test_kills_the_store_missing_guard(self, tmp_path: Path) -> None:
        mod = _load_mutant(
            tmp_path,
            "m_store",
            [("    if not store.is_dir():", "    if False:")],
        )
        with pytest.raises(Exception) as exc:
            mod.recall(tmp_path / "absent", SCOPE)
        assert not isinstance(exc.value, mod.StoreMissingError)
        assert "store root not found" not in str(exc.value)

    def test_kills_the_scope_absent_catch(self, tmp_path: Path) -> None:
        """Without it, the FIRST run in every unindexed repo is an exception —
        the intended case becomes the failing case, on every resume."""
        mod = _load_mutant(
            tmp_path,
            "m_scope_absent",
            [("    except UnknownScopeError:", "    except ZeroDivisionError:")],
        )
        store = _make_store(tmp_path / "s")
        with pytest.raises(sr.UnknownScopeError):
            mod.recall(store, "never-indexed")

    def test_kills_the_scope_empty_status(self, tmp_path: Path) -> None:
        """Without it an existing-but-empty scope renders as `recalled` with
        nothing in it — a zero with no explanation attached."""
        mod = _load_mutant(tmp_path, "m_empty", [("    if not entries:", "    if False:")])
        store = _make_store(tmp_path / "s")
        (store / "made-never-filled").mkdir()
        rep = mod.recall(store, "made-never-filled", mode="full")
        assert rep.status != "scope-empty"
        assert "NOTHING RECORDED YET" not in mod.render_text(rep)

    def test_kills_the_ambiguity_refusal(self, tmp_path: Path) -> None:
        """Without it an ambiguous ref PICKS one — silently surfacing one of two
        entries as though it were the only candidate."""
        mod = _load_mutant(
            tmp_path,
            "m_amb",
            [("        except AmbiguousRefError as exc:", "        except ZeroDivisionError as exc:")],
        )
        store = _make_store(tmp_path / "s")
        with pytest.raises(sr.AmbiguousRefError):
            mod.recall(store, SCOPE, ref="weekly-digest")

    def test_kills_the_ref_absent_status(self, tmp_path: Path) -> None:
        mod = _load_mutant(tmp_path, "m_ref_absent", [("        if entry is None:", "        if False:")])
        store = _make_store(tmp_path / "s")
        with pytest.raises(Exception) as exc:
            mod.recall(store, SCOPE, ref="no-such-subsystem")
        assert not isinstance(exc.value, ValueError)

    def test_kills_the_unreadable_entry_wrap(self, tmp_path: Path) -> None:
        """Without it an OSError escapes unnamed, and a resuming session cannot
        tell that the SUBSYSTEM STORE was the thing that failed."""
        mod = _load_mutant(
            tmp_path,
            "m_unreadable",
            [
                (
                    "        raise EntryUnreadableError(\n"
                    '            f"index entry unreadable: under {store} ',
                    "        raise RuntimeError(\n"
                    '            f"neutered: under {store} ',
                )
            ],
        )
        store = _make_store(tmp_path / "s")
        (store / SCOPE / "broken.md").mkdir()
        with pytest.raises(Exception) as exc:
            mod.recall(store, SCOPE)
        assert not isinstance(exc.value, mod.EntryUnreadableError)
        assert "index entry unreadable" not in str(exc.value)

    def test_kills_the_read_entry_wrap(self, tmp_path: Path) -> None:
        mod = _load_mutant(
            tmp_path,
            "m_read_entry",
            [
                (
                    "        raise EntryUnreadableError(\n"
                    '            f"index entry unreadable: {path} ',
                    "        raise RuntimeError(\n"
                    '            f"neutered: {path} ',
                )
            ],
        )
        store = _make_store(tmp_path / "s")
        entry = sr.SubsystemEntry.from_mapping(
            {"service": "ghost", "scope": SCOPE, "filename": "ghost.md"}
        )
        (store / SCOPE / "ghost.md").mkdir()
        with pytest.raises(Exception) as exc:
            mod.read_entry(store, entry)
        assert "index entry unreadable" not in str(exc.value)

    def test_kills_the_limit_sanity_guard(self, tmp_path: Path) -> None:
        mod = _load_mutant(
            tmp_path,
            "m_limit",
            [("    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:",
              "    if False:")],
        )
        store = _make_store(tmp_path / "s")
        rep = mod.recall(store, SCOPE, limit=0, mode="full")
        assert rep.entries == (), "limit=0 silently surfaced nothing instead of erroring"

    def test_kills_the_what_it_is_INCLUSION(self, tmp_path: Path) -> None:
        """🔴 THE RED HALF OF THE FIX, PERMANENTLY. Drop `WHAT_HEADING` back out
        of `SURFACED_HEADINGS` — the exact pre-2026-08-20 source — and the recall
        block silently stops answering "what IS this thing", which is the defect
        the change exists to close. Invisible to any test that only checks the
        other two sections are present, which is how it survived for months.

        ⚠ This mutation is the INVERSE of the one that used to live here: the
        anchor asserts the shipped source really carries the wider tuple, so the
        test cannot pass by mutating a line that no longer exists."""
        mod = _load_mutant(
            tmp_path,
            "m_sections",
            [
                (
                    'SURFACED_HEADINGS: tuple[str, ...] = (WHAT_HEADING, POINTERS_HEADING, NUANCE_HEADING)',
                    'SURFACED_HEADINGS: tuple[str, ...] = (POINTERS_HEADING, NUANCE_HEADING)',
                )
            ],
        )
        store = _make_store(tmp_path / "s")
        text = mod.render_text(mod.recall(store, SCOPE))
        # The CONTENT sentinel, not the heading string: the mutant still prints
        # the "(no parsable `## What it is` …)" note, so grepping the heading
        # would score this mutant SURVIVED for a reason that has nothing to do
        # with the section being surfaced.
        assert WHAT_IT_IS not in text
        # …and the kill is distinguishable from a broken harness: the other two
        # sections still render, so this is a section-SELECTION kill and not
        # "the renderer produced nothing".
        assert POINTER_LINE in text and NUANCE_LINE in text

    # ⚠ THE FENCE-SKIP AND PRESENT-BUT-EMPTY KILLS MOVED TO
    # test_subsystem_resolver.py, with the parser itself. `extract_sections` is
    # no longer defined in `subsystem_recall` — it moved down to the resolver so
    # `subsystem_touch` could read an entry's existing bullets without a second
    # copy — and mutating THIS module's source can no longer reach it. They are
    # not deleted: `TestEntryMarkdownShape` over there runs both, and the
    # anchor-uniqueness assert in `_load_mutant` is what made the move loud
    # rather than silently turning two mutation tests into no-ops.

    def test_kills_the_sensitivity_fail_safe(self, tmp_path: Path) -> None:
        """🔴 Without it an unrecognized or absent marker reads as whatever the
        file said — and `public` is a claim a reader may never infer."""
        mod = _load_mutant(
            tmp_path,
            "m_sens",
            [("    return SENSITIVITY_FAIL_SAFE", "    return str(raw)")],
        )
        assert mod.fold_sensitivity(None) != "client-confidential"
        assert mod.fold_sensitivity("probably-fine") != "client-confidential"

    def test_kills_the_truncation_notice(self, tmp_path: Path) -> None:
        """Without it entries vanish silently at the display cap — a filter
        wearing a cap's clothes."""
        mod = _load_mutant(tmp_path, "m_trunc", [("    elif report.omitted:", "    elif False:")])
        store = _make_store(tmp_path / "s")
        text = mod.render_text(mod.recall(store, SCOPE, limit=1, mode="full"))
        assert "NOT shown" not in text
        assert "more entr" not in text
        # 🔴 REACHABILITY, not just breakability: the DIGEST's own not-shown
        # notice sits in the `if` arm one line above and must be untouched by
        # this mutation, or "the notice is gone" would be a claim about the
        # wrong branch. (`claude/RULES.md` — a mutation that a DIFFERENT guard's
        # behaviour explains is green for the wrong reason.)
        assert "LISTED ABOVE but NOT shown in full" in mod.render_text(mod.recall(store, SCOPE))

    def test_kills_the_deterministic_ordering(self, tmp_path: Path) -> None:
        """Two runs must produce identical bytes; without the sort the order is
        whatever the loader's glob produced."""
        mod = _load_mutant(
            tmp_path,
            "m_order",
            [("    ordered = sorted(entries, key=lambda e: e.ref)",
              "    ordered = sorted(entries, key=lambda e: e.ref, reverse=True)")],
        )
        store = _make_store(tmp_path / "s")
        refs = [e.ref for e in mod.recall(store, SCOPE, mode="full").entries]
        assert refs != sorted(refs)

    def test_kills_the_index_ordering(self, tmp_path: Path) -> None:
        """🔴 A SEPARATE KILL because the index has its OWN ordering site now, and
        the body kill above no longer reaches it. Left merged, that test would
        have gone green off the mtime order it never touched — a mutation
        explained by a different rule is green for the wrong reason.

        The direction matters, not just the sort: newest-LAST is what makes a
        capped index hide the entries a resuming session most wants."""
        mod = _load_mutant(
            tmp_path,
            "m_index_order",
            [("    return tuple(sorted(entries, key=lambda e: (-e.mtime, e.ref)))",
              "    return tuple(sorted(entries, key=lambda e: (e.mtime, e.ref)))")],
        )
        store = _make_big_store(tmp_path / "s")
        assert [e.ref for e in mod.recall(store, BIG_SCOPE).listing][0] == "widget-01"
        assert [e.ref for e in rc.recall(store, BIG_SCOPE).listing][0] == f"widget-{BIG_N:02d}"

    # --- the digest's own guards -------------------------------------------
    #
    # 🔴 Each of these breaks ONE thing and asserts THAT thing's symptom. Where a
    # neighbouring branch could produce a similar-looking output (the two
    # not-shown notices), the assertion names the branch's own wording so a kill
    # cannot pass because the other arm fired.

    def test_kills_the_mode_guard(self, tmp_path: Path) -> None:
        """Without it an unknown mode falls through to the digest branch and
        renders a plausible report for a mode nobody implemented."""
        mod = _load_mutant(
            tmp_path, "m_mode", [("    if mode not in RECALL_MODES:", "    if False:")]
        )
        store = _make_store(tmp_path / "s")
        rep = mod.recall(store, SCOPE, mode="not-a-mode")
        assert rep.status == "recalled", "a neighbouring guard fired instead"
        # The real module rejects it, with THIS guard's own sentinel.
        with pytest.raises(ValueError) as exc:
            rc.recall(store, SCOPE, mode="not-a-mode")
        assert "mode must be one of" in str(exc.value)
        assert "limit must be an int" not in str(exc.value)

    def test_kills_the_full_index(self, tmp_path: Path) -> None:
        """🔴 The index is the ONE complete thing the digest offers. Let `limit`
        reach it and the default is silently incomplete again — exactly the half
        of the old default that a size measurement would never have caught."""
        mod = _load_mutant(
            tmp_path,
            "m_index",
            [
                (
                    "    read = tuple(read_entry(store, e) for e in ordered)",
                    "    read = tuple(read_entry(store, e) for e in ordered[:2])",
                )
            ],
        )
        store = _make_big_store(tmp_path / "s")
        assert len(mod.recall(store, BIG_SCOPE).listing) == 2
        assert len(rc.recall(store, BIG_SCOPE).listing) == BIG_N

    def test_kills_the_featured_basis(self, tmp_path: Path) -> None:
        """Without it an entry is printed first with no stated reason, which
        reads as a claim about importance the store cannot support."""
        mod = _load_mutant(
            tmp_path,
            "m_basis",
            [("    if report.featured_basis is not None:", "    if False:")],
        )
        store = _make_big_store(tmp_path / "s")
        text = mod.render_text(mod.recall(store, BIG_SCOPE))
        assert "most-recent fallback" not in text
        assert "resolved via" not in text
        assert "- pointer for widget-15" in text, "the body vanished too — wrong guard"

    def test_kills_the_resolver_selector(self, tmp_path: Path) -> None:
        """🔴 REACHABILITY. The fallback ALWAYS produces an entry, so a broken
        resolver selector still yields a plausible-looking digest. The kill has
        to be a window that DOES resolve, and the symptom is the basis wording."""
        mod = _load_mutant(
            tmp_path, "m_select", [("    if focus_paths:", "    if False:")]
        )
        store = _make_big_store(tmp_path / "s")
        window = dict(
            focus_paths=["apps/widget-04/a.yaml", "apps/widget-04/b.yaml"],
            focus_source="doc.md",
        )
        assert mod.recall(store, BIG_SCOPE, **window).featured_basis.startswith(
            "most-recent fallback"
        )
        real = rc.recall(store, BIG_SCOPE, **window)
        assert real.featured_basis.startswith("resolved via doc.md")
        assert [e.ref for e in real.entries] == ["widget-04"]

    def test_kills_the_mtime_fallback_direction(self, tmp_path: Path) -> None:
        """`max` vs `min`: a fallback that features the OLDEST entry is the
        mutation a "something got featured" assertion cannot see."""
        mod = _load_mutant(
            tmp_path,
            "m_mtime",
            [
                (
                    "    newest = max(entries, key=lambda e: (e.mtime, e.ref))",
                    "    newest = min(entries, key=lambda e: (e.mtime, e.ref))",
                )
            ],
        )
        store = _make_big_store(tmp_path / "s")
        __import__("os").utime(store / BIG_SCOPE / "widget-06.md", (2 * 10**9, 2 * 10**9))
        assert [e.ref for e in mod.recall(store, BIG_SCOPE).entries] != ["widget-06"]
        assert [e.ref for e in rc.recall(store, BIG_SCOPE).entries] == ["widget-06"]

    def test_kills_the_digest_not_shown_notice(self, tmp_path: Path) -> None:
        """The `--limit` notice sits in the very next `elif`, so the assertion
        names THIS branch's wording — otherwise the kill would pass on the
        neighbour's output and stay green with this branch deleted."""
        mod = _load_mutant(
            tmp_path,
            "m_digest_notice",
            [("    if report.omitted and report.listing_total:", "    if False:")],
        )
        store = _make_big_store(tmp_path / "s")
        text = mod.render_text(mod.recall(store, BIG_SCOPE))
        assert "LISTED ABOVE but NOT shown in full" not in text
        assert "Nothing is hidden" not in text
        assert "LISTED ABOVE but NOT shown in full" in rc.render_text(
            rc.recall(store, BIG_SCOPE)
        )

    def test_kills_the_repo_relative_path_filter(self, tmp_path: Path) -> None:
        """🔴 Without it an absolute path reaches `associate_paths`, which RAISES
        — so /resume step 4 would die on a handoff doc that quoted `/etc/...`."""
        mod = _load_mutant(
            tmp_path, "m_pathfilter", [('    if token[0] in "/~-":', "    if False:")]
        )
        text = "we edited `/etc/nixos/configuration.nix` yesterday"
        leaked = mod.focus_paths_from_text(text)
        assert "/etc/nixos/configuration.nix" in leaked
        store = _make_store(tmp_path / "s")
        with pytest.raises(sr.InvalidPathError):
            sr.associate_paths(leaked, sr.load_index(store), SCOPE, min_paths=1)
        assert rc.focus_paths_from_text(text) == ()

    def test_kills_the_cli_flag_conflict_guards(self, tmp_path: Path) -> None:
        """Four guards, killed SEPARATELY: a shared kill would pass while any one
        of the others still fired, which is green for the wrong reason.

        ⚠ The `--list`/`--ref` pair is no longer its own `if`. `--search`, `--ref`
        and `--list` are three selectors over one store and every pair of them is
        the SAME conflict, so they were consolidated into one guard over
        `_SELECTORS` — three pairwise ifs is that predicate open-coded at three
        sites, and `claude/RULES.md` is explicit that such a predicate is wrong at
        N−1 of them. The kill below therefore uses the ONE guard, and proves it
        with two different pairs so a mutation that only broke one pair is
        visible."""
        store = _make_store(tmp_path / "s")
        base = ["--store", str(store), "--scope", SCOPE]
        cases = [
            (
                "m_selectors",
                "    if len(chosen) > 1:",
                [
                    base + ["--list", "--ref", "collector"],
                    base + ["--search", "readiness", "--ref", "collector"],
                    base + ["--search", "readiness", "--list"],
                ],
            ),
            (
                "m_list_limit",
                "    if args.listing and args.limit is not None:",
                [base + ["--list", "--limit", "2"]],
            ),
            (
                "m_page_body",
                "    if args.page is not None and (args.ref is not None or args.limit is not None):",
                [base + ["--page", "2", "--ref", "collector"], base + ["--page", "2", "--limit", "2"]],
            ),
            (
                "m_search_only",
                "        if stray:",
                [base + ["-C", "2"], base + ["--all-scopes"], base + ["--max-hits", "3"]],
            ),
        ]
        for name, anchor, argvs in cases:
            mod = _load_mutant(tmp_path, name, [(anchor, "    if False:" if not anchor.startswith("        ") else "        if False:")])
            for argv in argvs:
                assert mod.main(argv) == 0, (
                    f"{name}: another guard fired for {argv[3:]}, so this kill proves nothing"
                )
                assert rc.main(argv) == 2, f"{name}: the real module accepted {argv[3:]}"

    # --- the page cap's own guards ------------------------------------------

    def test_kills_the_page_slice(self, tmp_path: Path) -> None:
        """🔴 Without it the index is uncapped again — and a size test would not
        see it, because the fixture that shows it is 105 entries and the digest's
        generous size ceiling is measured on 15."""
        mod = _load_mutant(
            tmp_path,
            "m_page_slice",
            [("    return ordered[start : start + LISTING_PAGE_SIZE], pages",
              "    return ordered, pages")],
        )
        store = _make_paged_store(tmp_path / "s")
        assert len(mod.recall(store, PAGED_SCOPE).listing) == PAGED_N
        assert len(rc.recall(store, PAGED_SCOPE).listing) == rc.LISTING_PAGE_SIZE

    def test_kills_the_page_truncation_notice(self, tmp_path: Path) -> None:
        """Without it the cap becomes SILENT — 5 entries vanish off the end of the
        index with the header still saying "entries 1–100 of 105", which reads as
        a complete list to anyone not doing the arithmetic.

        The assertion names THIS branch's wording: the past-the-end notice sits in
        the `if` arm directly above and must be untouched, or the kill would pass
        on the neighbour's silence."""
        mod = _load_mutant(
            tmp_path,
            "m_page_notice",
            [("    elif report.listing_after_page:", "    elif False:")],
        )
        store = _make_paged_store(tmp_path / "s")
        text = mod.render_text(mod.recall(store, PAGED_SCOPE))
        assert "NOT LISTED on this page" not in text
        assert "`--page 2`" not in text
        # REACHABILITY: the other arm still fires in the mutant, so this kill is
        # about the notice and not about the whole block disappearing.
        assert "PAGE 9 IS PAST THE END" in mod.render_text(
            mod.recall(store, PAGED_SCOPE, page=9)
        )
        assert "NOT LISTED on this page" in rc.render_text(rc.recall(store, PAGED_SCOPE))

    def test_kills_the_past_the_end_PREDICATE(self, tmp_path: Path) -> None:
        """🔴 ONE predicate, THREE branches — and this kill proves all three hang
        off it. The first shipped version asked the question separately in each,
        which is exactly how the header printed `entries 801–800 of 150 (page 9
        of 2)` directly above a guidance line that was correct.

        The mutant reproduces that original defect verbatim, which is the
        strongest form this kill can take: the symptom is not "something is
        missing", it is the specific wrong output the property exists to
        prevent."""
        mod = _load_mutant(
            tmp_path,
            "m_past_end",
            [("        return self.listing_page > self.listing_pages",
              "        return False")],
        )
        store = _make_paged_store(tmp_path / "s")
        text = mod.render_text(mod.recall(store, PAGED_SCOPE, mode="list", page=9))
        # (1) the guidance line is gone…
        assert "PAGE 9 IS PAST THE END" not in text
        assert "nothing is missing from the store" not in text
        # (2) …and the header does the unguarded arithmetic again: an inverted
        #     range and an impossible page-of-page.
        first = (9 - 1) * rc.LISTING_PAGE_SIZE + 1
        assert f"entries {first}–{first - 1} of {PAGED_N}" in text
        assert f"(page 9 of {(PAGED_N - 1) // rc.LISTING_PAGE_SIZE + 1})" in text
        # (3) …and `--list`'s completeness line describes rows it never printed.
        assert "The 0 entries above are page 9 of 2" in text

        real = rc.render_text(rc.recall(store, PAGED_SCOPE, mode="list", page=9))
        assert "PAGE 9 IS PAST THE END" in real
        assert f"entries {first}–" not in real
        assert "page 9 is past the end" in real
        assert "NO entries were listed" in real

    def test_kills_the_past_the_end_HEADER_branch(self, tmp_path: Path) -> None:
        """The header's own branch, killed separately from the predicate: a
        shared kill would pass while the header alone was still guarded.

        The symptom is asserted as the INVARIANT (`hi < lo`) rather than as the
        literal `801–800`, because `listing_before_page` still guards past the
        end, so this mutant inverts to `1–0` instead. Pinning the numbers would
        have made this test a claim about which of the two guards is missing."""
        mod = _load_mutant(
            tmp_path,
            "m_past_end_head",
            [("    if report.page_is_past_the_end:\n        # 🔴 NO ARITHMETIC",
              "    if False:\n        # 🔴 NO ARITHMETIC")],
        )
        store = _make_paged_store(tmp_path / "s")
        head = next(
            l
            for l in mod.render_text(
                mod.recall(store, PAGED_SCOPE, mode="list", page=9)
            ).splitlines()
            if l.startswith("INDEX")
        )
        lo, hi = head.split("entries ")[1].split(" of ")[0].split("–")
        assert int(hi) < int(lo), f"the header stopped inverting: {head}"
        assert "(page 9 of 2)" in head
        # REACHABILITY: the guidance line below is untouched, so this kill is
        # about the HEADER and not about the whole past-the-end handling.
        assert "PAGE 9 IS PAST THE END" in mod.render_text(
            mod.recall(store, PAGED_SCOPE, mode="list", page=9)
        )

    def test_kills_the_list_completeness_line_being_PAGE_SCOPED(
        self, tmp_path: Path
    ) -> None:
        """🔴 Reinstating the original defect: the closing line claims "the N
        entries above are the complete index" with N the SCOPE total, on a page
        that printed 100 of 105."""
        mod = _load_mutant(
            tmp_path,
            "m_complete_line",
            [("        elif report.listing_pages > 1:", "        elif False:")],
        )
        store = _make_paged_store(tmp_path / "s")
        text = mod.render_text(mod.recall(store, PAGED_SCOPE, mode="list"))
        assert f"The {PAGED_N} entries above are the complete index" in text
        assert "are page 1 of 2" not in text
        # REACHABILITY: the past-the-end arm sits ABOVE this `elif` and must be
        # untouched, or the kill would be about the wrong branch.
        assert "NO entries were listed" in mod.render_text(
            mod.recall(store, PAGED_SCOPE, mode="list", page=9)
        )
        assert "The 100 entries above are page 1 of 2" in rc.render_text(
            rc.recall(store, PAGED_SCOPE, mode="list")
        )

    def test_kills_the_overridden_sensitivity_ANNOTATION(self, tmp_path: Path) -> None:
        """Without it an unknown marker is silently rewritten: `internal` and an
        absent marker become the same bare string, and the file's own claim
        disappears. The fold is asserted UNCHANGED in the mutant, so this kill is
        about visibility and not about the safety property."""
        mod = _load_mutant(
            tmp_path,
            "m_declared",
            [("    if declared is None:\n        return effective",
              "    if True:\n        return effective")],
        )
        store = tmp_path / "s2"
        (store / SCOPE).mkdir(parents=True)
        (store / SCOPE / "collector.md").write_text(
            _entry("collector", SCOPE, sensitivity="internal"), encoding="utf-8"
        )
        rows = mod.recall(store, SCOPE, mode="list").listing
        assert mod.listing_line(rows[0], 12).endswith("client-confidential")
        assert "declared" not in mod.listing_line(rows[0], 12)
        # the fail-safe itself still fires in the mutant — visibility, not safety
        assert rows[0].sensitivity == mod.SENSITIVITY_FAIL_SAFE
        real = rc.recall(store, SCOPE, mode="list").listing
        assert "client-confidential (declared: internal)" in rc.listing_line(real[0], 12)

    def test_kills_the_page_sanity_guard(self, tmp_path: Path) -> None:
        """Reached with a valid `limit`, so the guard above it cannot be what
        fires — and the symptom is THIS guard's own sentinel."""
        mod = _load_mutant(
            tmp_path,
            "m_page_guard",
            [("    if not isinstance(page, int) or isinstance(page, bool) or page < 1:",
              "    if False:")],
        )
        store = _make_store(tmp_path / "s")
        assert mod.recall(store, SCOPE, page=0).status == "recalled"
        with pytest.raises(ValueError) as exc:
            rc.recall(store, SCOPE, page=0)
        assert "page must be an int >= 1" in str(exc.value)
        assert "limit must be an int" not in str(exc.value)

    # --- search's own guards -------------------------------------------------

    def test_kills_the_coverage_MEAN(self, tmp_path: Path) -> None:
        """🔴 THE SCORER'S LOAD-BEARING DECISION. Turn the mean into a max and a
        multi-token query becomes as loose as its loosest word: `compaction
        kryptonite` scores 1.00 off `compaction` alone, so a query whose second
        term appears NOWHERE returns the first term's hits wearing the second
        term's authority."""
        mod = _load_mutant(
            tmp_path,
            "m_mean",
            [("    return total / len(query_tokens)", "    return min(1.0, total)")],
        )
        store = _make_search_store(tmp_path / "s")
        mutant = mod.search(store, SEARCH_SCOPE, "compaction kryptonite")
        assert mutant.status == "search-hit"
        assert mutant.hunks[0].score == 1.0
        real = rc.search(store, SEARCH_SCOPE, "compaction kryptonite")
        assert real.status == "search-no-match"

    def test_kills_the_concatenation(self, tmp_path: Path) -> None:
        """Without it a compound whose parts are shorter than MIN_INEXACT_LEN is
        unreachable — every other rung is closed to a 3-character token — and,
        now that the rungs are directional, so is a compound whose parts are LONG
        (`ratelimit` against a corpus writing `rate-limit`)."""
        mod = _load_mutant(
            tmp_path,
            "m_concat",
            [("        joined.extend(a + b for a, b in zip(toks, toks[1:]))",
              "        joined.extend(())")],
        )
        store = _make_search_store(tmp_path / "s")
        assert mod.search(store, SEARCH_SCOPE, "k8sapi").status == "search-no-match"
        assert mod.search(store, SEARCH_SCOPE, "ratelimit").status == "search-no-match"
        real = rc.search(store, SEARCH_SCOPE, "k8sapi")
        assert real.hunks[0].ref == "beacon"
        assert real.hunks[0].score == 1.0
        assert rc.search(store, SEARCH_SCOPE, "ratelimit").hunks[0].score == 1.0

    def test_kills_the_PREFIX_rung_DIRECTION(self, tmp_path: Path) -> None:
        """🔴 ONE RUNG, ISOLATED. Only the prefix `if` is reverted to the
        symmetric form — the substring rung below it is untouched, so a green
        here could not be a neighbour's doing.

        The kill is asserted on THIS rung's own number. Both mutants take the
        same bait (`drain` prefixes `drainage`, and therefore is also contained
        by it), but the prefix rung answers 0.92 and the substring rung 0.85, so
        the score IS the discriminator."""
        mod = _load_mutant(
            tmp_path,
            "m_prefix_dir",
            [("    if t.startswith(q):", "    if t.startswith(q) or q.startswith(t):")],
        )
        assert mod.pair_strength("drainage", "drain") == mod.PREFIX_STRENGTH
        assert mod.pair_strength("drainage", "drain") != mod.SUBSTRING_STRENGTH
        assert rc.pair_strength("drainage", "drain") == 0.0
        store = _make_directional_store(tmp_path / "s")
        mutant = mod.search(store, DIRECTIONAL_SCOPE, "drainage")
        assert mutant.status == "search-hit"
        assert mutant.hunks[0].score == mod.PREFIX_STRENGTH
        assert "drainage" not in "\n".join(mutant.hunks[0].lines).lower()
        assert rc.search(store, DIRECTIONAL_SCOPE, "drainage").status == "search-no-match"
        # …and the rung it must NOT have touched still answers as itself.
        assert mod.pair_strength("logrotate", "rotate") == 0.0

    def test_kills_the_SUBSTRING_rung_DIRECTION(self, tmp_path: Path) -> None:
        """🔴 The other rung, isolated the same way — and reached by a bait the
        PREFIX rung structurally cannot take: `rotate` is contained by
        `logrotate` but does not begin it. Without this rung's direction the
        headline defect is back: a corpus that only writes `rotate` answers a
        `logrotate` query at 0.85."""
        mod = _load_mutant(
            tmp_path,
            "m_substr_dir",
            [("    if q in t:", "    if q in t or t in q:")],
        )
        assert mod.pair_strength("logrotate", "rotate") == mod.SUBSTRING_STRENGTH
        assert rc.pair_strength("logrotate", "rotate") == 0.0
        store = _make_directional_store(tmp_path / "s")
        mutant = mod.search(store, DIRECTIONAL_SCOPE, "logrotate")
        assert mutant.status == "search-hit"
        assert mutant.hunks[0].score == mod.SUBSTRING_STRENGTH
        assert "logrotate" not in "\n".join(mutant.hunks[0].lines).lower()
        assert rc.search(store, DIRECTIONAL_SCOPE, "logrotate").status == "search-no-match"
        # …and the rung above it stayed directional, at ITS own number.
        assert mod.pair_strength("drainage", "drain") == mod.SUBSTRING_STRENGTH
        assert mod.pair_strength("drainage", "drain") != mod.PREFIX_STRENGTH

    def test_kills_the_CLAUSE_boundary_on_the_join(self, tmp_path: Path) -> None:
        """🔴 The join's boundary, mutated NARROWLY: the break class is emptied
        to a pattern that matches nothing, leaving the join itself intact — a
        mutant that deleted both would die for the concatenation's reason and
        prove nothing about the boundary.

        Its symptom is specific and is the worst one available: a PERFECT 1.00
        for a query the winning entry does not contain anywhere."""
        mod = _load_mutant(
            tmp_path,
            "m_clause",
            [(r'_CLAUSE_BREAK = re.compile(r"[,;:!?()\[\]]|\.(?=\s|$)")',
              '_CLAUSE_BREAK = re.compile(r"(?!x)x")')],
        )
        assert "nodeport" in mod.candidate_tokens("drain that node, port-forward it")
        assert "nodeport" not in rc.candidate_tokens("drain that node, port-forward it")
        store = _make_directional_store(tmp_path / "s")
        mutant = mod.search(store, DIRECTIONAL_SCOPE, "nodeport")
        assert mutant.status == "search-hit"
        assert mutant.hunks[0].score == 1.0
        assert "nodeport" not in "\n".join(mutant.hunks[0].lines).lower()
        assert rc.search(store, DIRECTIONAL_SCOPE, "nodeport").status == "search-no-match"
        # …and the legitimate join is still there in the mutant, so this kill is
        # about the BOUNDARY and not about the join having vanished.
        assert mod.search(store, DIRECTIONAL_SCOPE, "healthcheck").hunks[0].score == 1.0

    def test_kills_the_short_token_exactness_rule(self, tmp_path: Path) -> None:
        """🔴 Without it short tokens fuzzily reach anything they prefix, which is
        where every inexact rule turns into noise. Killed with a REAL corpus token
        (`podman`) rather than an invented one."""
        mod = _load_mutant(
            tmp_path, "m_shortlen", [("MIN_INEXACT_LEN = 4", "MIN_INEXACT_LEN = 1")]
        )
        assert mod.pair_strength("pod", "podman") == mod.PREFIX_STRENGTH
        assert rc.pair_strength("pod", "podman") == 0.0
        store = _make_search_store(tmp_path / "s")
        assert mod.search(store, SEARCH_SCOPE, "pod").status == "search-hit"
        assert rc.search(store, SEARCH_SCOPE, "pod").status == "search-no-match"

    def test_kills_the_fuzzy_floor(self, tmp_path: Path) -> None:
        """Dropping it to 0.80 admits `probe`/`prone` — two real words one edit
        apart, and not a typo of anything."""
        mod = _load_mutant(tmp_path, "m_floor", [("FUZZY_FLOOR = 0.82", "FUZZY_FLOOR = 0.70")])
        assert mod.pair_strength("probe", "prone") > 0.0
        assert rc.pair_strength("probe", "prone") == 0.0

    def test_kills_the_entry_name_hit(self, tmp_path: Path) -> None:
        """🔴 REACHABILITY. The name selector only ever fires when NO block
        cleared, so a store where every query also matches a body would leave it
        untested; `zephyr-cache`'s body never says `zephyr`. Without the guard the
        entry a caller searched for BY NAME returns nothing at all."""
        mod = _load_mutant(
            tmp_path, "m_namehit", [("    if name_score >= threshold:", "    if False:")]
        )
        store = _make_search_store(tmp_path / "s")
        assert mod.search(store, SEARCH_SCOPE, "zephyr").status == "search-no-match"
        real = rc.search(store, SEARCH_SCOPE, "zephyr")
        assert real.hunks[0].ref == "zephyr-cache"
        assert real.hunks[0].basis == "entry-name"

    def test_kills_the_name_score_TIE_BREAK(self, tmp_path: Path) -> None:
        """Without it a tie at 1.00 falls to the alphabetical key, and the entry
        that merely MENTIONS the subsystem can outrank the subsystem's own entry.
        Kill built so the alphabetical order alone would give the WRONG answer."""
        mod = _load_mutant(
            tmp_path,
            "m_tiebreak",
            [("    cleared.sort(key=lambda h: (-h.score, -h.name_score, h.scope, h.ref, h.start))",
              "    cleared.sort(key=lambda h: (-h.score, h.scope, h.ref, h.start), reverse=True)")],
        )
        store = _make_search_store(tmp_path / "s")
        assert mod.search(store, SEARCH_SCOPE, "blob-vault").hunks[0].ref != "blob-vault"
        assert rc.search(store, SEARCH_SCOPE, "blob-vault").hunks[0].ref == "blob-vault"

    def test_kills_the_front_matter_exclusion(self, tmp_path: Path) -> None:
        """🔴 Without it the `---` fence and the `service:`/`aliases:` lines
        become blocks, and a prototype that let them through ranked the front
        matter first for every query naming an entry."""
        mod = _load_mutant(
            tmp_path,
            "m_frontmatter",
            [('        if cur and section is not None:', "        if cur:")],
        )
        store = _make_search_store(tmp_path / "s")
        text = (store / SEARCH_SCOPE / "blob-vault.md").read_text(encoding="utf-8")
        assert any("service:" in b.text for b in mod.entry_blocks(text))
        assert not any("service:" in b.text for b in rc.entry_blocks(text))

    def test_kills_the_heading_exclusion(self, tmp_path: Path) -> None:
        """Without it a query for the literal words of a schema heading hits every
        entry in the store."""
        mod = _load_mutant(
            tmp_path,
            "m_heading",
            [("        if heading:\n            flush()\n            section = heading.group(0).strip()\n            continue",
              "        if heading:\n            flush()\n            section = heading.group(0).strip()")],
        )
        store = _make_search_store(tmp_path / "s")
        n = len(list((store / SEARCH_SCOPE).glob("*.md")))
        assert len({h.ref for h in mod.search(store, SEARCH_SCOPE, "nuance history").hunks}) == n
        assert rc.search(store, SEARCH_SCOPE, "nuance history").status == "search-no-match"

    def test_kills_the_zero_score_near_miss_filter(self, tmp_path: Path) -> None:
        """🔴 Without it an ABSENT term reports "the closest candidate scored
        0.00", which reads as a weak match. The two zeros need different next
        actions — lower the threshold vs. rephrase — so they must not collapse."""
        mod = _load_mutant(
            tmp_path,
            "m_nearmiss",
            [("    if best is None or (best[0] <= 0.0 and name_score < threshold):",
              "    if best is None:")],
        )
        store = _make_search_store(tmp_path / "s")
        assert mod.search(store, SEARCH_SCOPE, "kryptonite").best_below is not None
        assert "closest candidate" in mod.render_search(mod.search(store, SEARCH_SCOPE, "kryptonite"))
        assert rc.search(store, SEARCH_SCOPE, "kryptonite").best_below is None

    def test_kills_the_best_below_report(self, tmp_path: Path) -> None:
        """The other direction: without the near-miss report a query that JUST
        missed prints the same blank as one that matched nothing."""
        mod = _load_mutant(
            tmp_path,
            "m_bestbelow",
            [("    worst = max(below, default=None, key=lambda h: (h.score, -h.start))",
              "    worst = None")],
        )
        store = _make_search_store(tmp_path / "s")
        text = mod.render_search(mod.search(store, SEARCH_SCOPE, "compaction kryptonite"))
        assert "closest candidate" not in text
        assert "NO MATCH" in text, "the whole branch vanished — wrong guard"
        assert "closest candidate was `blob-vault` at 0.50" in rc.render_search(
            rc.search(store, SEARCH_SCOPE, "compaction kryptonite")
        )

    def test_kills_the_per_hunk_sensitivity_label(self, tmp_path: Path) -> None:
        """🔴 Hunk output INTERLEAVES entries, so a sensitivity printed once would
        end up describing somebody else's lines. Killed by counting, not by an
        `in` check that a single surviving label would satisfy."""
        mod = _load_mutant(
            tmp_path,
            "m_hunklabel",
            [('            f"sensitivity={sensitivity_label(h.sensitivity, h.declared_sensitivity)})"',
              '            f")"')],
        )
        store = _make_search_store(tmp_path / "s")
        rep = mod.search(store, SEARCH_SCOPE, "blob-vault")
        assert len(rep.hunks) >= 2
        assert mod.render_search(rep).count("sensitivity=") == 0
        assert rc.render_search(rc.search(store, SEARCH_SCOPE, "blob-vault")).count(
            "sensitivity="
        ) == len(rep.hunks)

    def test_kills_the_context_override(self, tmp_path: Path) -> None:
        """Without it `-C N` silently returns the bullet — the flag Zach asked for
        would be inert and every hunk would still look plausible."""
        mod = _load_mutant(
            tmp_path,
            "m_context",
            [("        if context == CONTEXT_BULLET:", "        if True:")],
        )
        store = _make_search_store(tmp_path / "s")
        assert mod.search(store, SEARCH_SCOPE, "compaction", context=4).hunks[0].lines == (
            mod.search(store, SEARCH_SCOPE, "compaction").hunks[0].lines
        )
        wide = rc.search(store, SEARCH_SCOPE, "compaction", context=4).hunks[0]
        assert len(wide.lines) > len(rc.search(store, SEARCH_SCOPE, "compaction").hunks[0].lines)

    def test_kills_the_search_scope_absent_status(self, tmp_path: Path) -> None:
        """Without it an unindexed scope reports `search-no-match` — "nothing was
        searched" wearing the words "nothing matched"."""
        mod = _load_mutant(
            tmp_path,
            "m_search_scope",
            [("    elif normalize_ref(scope) not in index.scopes:", "    elif False:")],
        )
        store = _make_search_store(tmp_path / "s")
        with pytest.raises(sr.UnknownScopeError):
            mod.search(store, "never-indexed", "compaction")
        assert rc.search(store, "never-indexed", "compaction").status == "scope-absent"

    def test_kills_the_hunk_truncation_notice(self, tmp_path: Path) -> None:
        mod = _load_mutant(
            tmp_path, "m_hunk_trunc", [("    if report.omitted:", "    if False:")]
        )
        store = _make_search_store(tmp_path / "s")
        rep = mod.search(store, SEARCH_SCOPE, "the", threshold=0.5, max_hits=2)
        assert rep.omitted > 0
        assert "NOT shown" not in mod.render_search(rep)
        assert "NOT shown (--max-hits 2)" in rc.render_search(
            rc.search(store, SEARCH_SCOPE, "the", threshold=0.5, max_hits=2)
        )

    def test_kills_the_search_query_guard(self, tmp_path: Path) -> None:
        """An empty query must not scan the store and return "no match" — that is
        a zero produced by the question, not by the store."""
        mod = _load_mutant(
            tmp_path,
            "m_query",
            [("    if not query or not query.strip():", "    if False:")],
        )
        store = _make_search_store(tmp_path / "s")
        assert mod.search(store, SEARCH_SCOPE, "").status == "search-no-match"
        with pytest.raises(ValueError) as exc:
            rc.search(store, SEARCH_SCOPE, "")
        assert "query must be a non-empty string" in str(exc.value)
        assert "threshold must be" not in str(exc.value)

    def test_kills_the_threshold_range_guard(self, tmp_path: Path) -> None:
        """Reached with a valid query, so the guard above cannot be what fires."""
        mod = _load_mutant(
            tmp_path,
            "m_threshold",
            [("    if not 0.0 <= float(threshold) <= 1.0:", "    if False:")],
        )
        store = _make_search_store(tmp_path / "s")
        assert mod.search(store, SEARCH_SCOPE, "compaction", threshold=-1).status == "search-hit"
        with pytest.raises(ValueError) as exc:
            rc.search(store, SEARCH_SCOPE, "compaction", threshold=-1)
        assert "threshold must be a number in [0, 1]" in str(exc.value)
        assert "query must be" not in str(exc.value)

    def test_kills_the_caveat(self, tmp_path: Path) -> None:
        """🔴 Without it index recall is presented as a plain finding, which is
        the one thing `/analyze-service` says never to do."""
        mod = _load_mutant(
            tmp_path,
            "m_caveat",
            [('        f"{RECALL_LABEL} — RECALL, NOT LIVE OBSERVATION. These are notes curated by "',
              '        f"(caveat neutered) "')],
        )
        store = _make_store(tmp_path / "s")
        text = mod.render_text(mod.recall(store, SCOPE))
        assert "RECALL, NOT LIVE OBSERVATION" not in text
        # 🔴 ONE kill, BOTH renderers — which is the point of `caveat_text` being
        # a module function rather than a property on one report. If search ever
        # grew its own copy of the sentence this assertion would go green while
        # the search block kept presenting recall as a plain finding.
        assert "RECALL, NOT LIVE OBSERVATION" not in mod.render_search(
            mod.search(store, SCOPE, "readiness")
        )
        assert "RECALL, NOT LIVE OBSERVATION" in rc.render_search(
            rc.search(store, SCOPE, "readiness")
        )


# =============================================================================
# ONE BAD ENTRY MUST NOT COST THE WHOLE SCOPE.
# =============================================================================
#
# 🔴 THE MEASUREMENT, on a synthetic store, BEFORE this change:
#
#     2 good entries          -> rc=0, both listed
#     2 good + 1 malformed    -> rc=3, good entries still listed: 0
#
# The defect that produced it was an `aliases:` flow list wrapped across two
# physical lines. The front-matter parser is LINE-BASED, so line 1 is an
# unterminated `[` and reads as a bare string — and `/handoff`'s own template
# prints `aliases:` on ONE line, so the writer had no signal and the confirm-gate
# diff shown to the human CONTAINED the defect while being structurally
# incapable of revealing it.
#
# Every fixture here is SYNTHETIC. `devrc` is PUBLIC and real entries are
# client-confidential: no real name, host, path or scope appears below.

WRAPPED_ALIASES = (
    "---\n"
    "service: widget-index\n"
    f"scope: {SCOPE}\n"
    "aliases: [widget_touch, widget_recall, widget_resolver,\n"
    "          test_widget_touch, test_widget_recall]\n"
    "---\n"
    "\n"
    "## What it is\n"
    "The entry whose front matter the writer wrapped.\n"
    "\n"
    "## Pointers\n"
    "- lib/widget.py — the wrapped one\n"
)


def _break_one(store: Path, scope: str = SCOPE, name: str = "widget-index.md") -> Path:
    """Drop THE reported defect into a scope. Returns the file."""
    p = store / scope / name
    p.write_text(WRAPPED_ALIASES.replace(f"scope: {SCOPE}", f"scope: {scope}"), encoding="utf-8")
    return p


def _all_broken_scope(store: Path, scope: str = "every-entry-broken") -> Path:
    """A scope holding files, NONE of which can be indexed."""
    d = store / scope
    d.mkdir()
    _break_one(store, scope, "widget-index.md")
    (d / "cog-unit.md").write_text(
        f"---\nservice: cog-unit\nscope: {scope}\naliases: not-a-list\n---\n",
        encoding="utf-8",
    )
    return d


class TestBlastRadius:
    """The reported numbers, reproduced as assertions on THIS code."""

    def test_two_good_plus_one_bad_still_lists_the_good_ones(self, tmp_path: Path) -> None:
        """🔴 THE REGRESSION. At base this listed 0 and raised."""
        store = tmp_path / "s"
        store.mkdir()
        (store / SCOPE).mkdir()
        (store / SCOPE / "alpha-unit.md").write_text(_entry("alpha-unit", SCOPE), encoding="utf-8")
        (store / SCOPE / "beta-unit.md").write_text(_entry("beta-unit", SCOPE), encoding="utf-8")
        _break_one(store)

        rep = rc.recall(store, SCOPE)
        assert rep.status == "recalled"
        assert sorted(e.ref for e in rep.listing) == ["alpha-unit", "beta-unit"]
        assert rep.listing_total == 2, "readable entries only — a reject is not an entry"
        assert [m.filename for m in rep.malformed] == ["widget-index.md"]

    def test_the_bad_one_is_NAMED_and_never_silently_dropped(self, store: Path) -> None:
        """🔴 The obligation that comes with degrading. A silent skip is WORSE
        than the collapse: a dropped entry is indistinguishable from one nobody
        ever wrote."""
        _break_one(store)
        text = rc.render_text(rc.recall(store, SCOPE))
        assert "MALFORMED" in text
        assert "widget-index.md" in text, "the file must be named, not merely counted"
        assert "malformed index entry" in text, "the sentinel must reach the output"
        assert "must be a list, not a bare string" in text, "…and so must the REASON"

    def test_every_selector_survives_one_bad_entry(self, store: Path) -> None:
        """`--list`, `--ref`, the digest and `--search` died TOGETHER at base."""
        _break_one(store)
        assert rc.recall(store, SCOPE, mode="list").status == "recalled"
        assert rc.recall(store, SCOPE, mode="digest").status == "recalled"
        assert rc.recall(store, SCOPE, mode="full").status == "recalled"
        assert rc.recall(store, SCOPE, ref="collector").status == "recalled"
        assert rc.search(store, SCOPE, "readiness").status == "search-hit"

    def test_the_index_stops_claiming_it_is_COMPLETE(self, store: Path) -> None:
        """🔴 A completeness claim is a comment like any other, and it is FALSE
        about a scope whose third file could not be indexed."""
        clean = rc.render_text(rc.recall(store, SCOPE, mode="list"))
        assert "none omitted" in clean
        assert "complete index" in clean

        _break_one(store)
        dirty = rc.render_text(rc.recall(store, SCOPE, mode="list"))
        assert "none omitted" not in dirty
        assert "NOT the complete index" in dirty
        assert "READABLE" in dirty

    def test_a_ref_that_misses_does_not_claim_the_name_is_UNRECORDED(
        self, store: Path
    ) -> None:
        """The `--ref` a reader would type after seeing the reject. "Nothing
        recorded under that name yet" is a claim about the STORE and it is false
        when the entry exists in a file the loader refused."""
        _break_one(store)
        text = rc.render_text(rc.recall(store, SCOPE, ref="widget-index"))
        assert "Nothing recorded under that name yet" not in text
        assert "may name one of them" in text

    def test_the_store_is_still_BYTE_IDENTICAL_after_the_degraded_paths(
        self, store: Path
    ) -> None:
        """The read-only invariant, re-asserted over the code that is NEW."""
        _break_one(store)
        before = _tree_hash(store)
        for kw in ({"mode": "list"}, {"mode": "digest"}, {"mode": "full"}, {"ref": "collector"}):
            rep = rc.recall(store, SCOPE, **kw)
            rc.render_text(rep)
            json.dumps(rc.report_json(rep))
        srep = rc.search(store, SCOPE, "readiness")
        rc.render_search(srep)
        json.dumps(rc.search_json(srep))
        assert _tree_hash(store) == before


class TestUnreadableIsNotEmpty:
    """🔴 `scope-empty` is reported by `/resume` as an ordinary non-finding. A
    scope nothing could be read from must NEVER reach that branch."""

    def test_the_pair(self, store: Path) -> None:
        """POSITIVE AND NEGATIVE CONTROL, one call apart. Both surfaces are
        empty; only the mechanism differs, and the mechanism is the whole point."""
        (store / "made-never-filled").mkdir()
        _all_broken_scope(store)

        empty = rc.recall(store, "made-never-filled")
        unreadable = rc.recall(store, "every-entry-broken")

        assert empty.status == "scope-empty"
        assert unreadable.status == "scope-unreadable"
        assert empty.malformed == ()
        assert len(unreadable.malformed) == 2, "the positive control collected nothing"

    def test_the_two_share_NO_wording(self, store: Path) -> None:
        """A shared opening phrase is all it takes for a broken store to be read
        as an empty one."""
        (store / "made-never-filled").mkdir()
        _all_broken_scope(store)
        empty = rc.render_text(rc.recall(store, "made-never-filled"))
        unreadable = rc.render_text(rc.recall(store, "every-entry-broken"))
        assert "NOTHING RECORDED YET" in empty
        assert "NOTHING RECORDED YET" not in unreadable
        assert "NOTHING COULD BE READ" in unreadable
        assert "NOTHING COULD BE READ" not in empty
        assert "NOT an empty scope" in unreadable

    def test_an_ABSENT_scope_is_a_third_thing_still(self, store: Path) -> None:
        _all_broken_scope(store)
        assert rc.recall(store, "never-indexed").status == "scope-absent"

    def test_a_ref_into_an_unreadable_scope_is_NOT_ref_absent(self, store: Path) -> None:
        """🔴 Order matters: the unreadable check runs BEFORE `--ref`. Otherwise
        the answer would be "nothing recorded under that name", about a directory
        the tool never managed to read."""
        _all_broken_scope(store)
        rep = rc.recall(store, "every-entry-broken", ref="widget-index")
        assert rep.status == "scope-unreadable"

    def test_search_has_the_same_pair(self, store: Path) -> None:
        (store / "made-never-filled").mkdir()
        _all_broken_scope(store)
        assert rc.search(store, "made-never-filled", "readiness").status == "search-no-match"
        broken = rc.search(store, "every-entry-broken", "readiness")
        assert broken.status == "search-unreadable"
        text = rc.render_search(broken)
        assert "NOTHING COULD BE READ" in text
        assert "never run against anything" in text
        assert "NO MATCH" not in text


class TestRejectsElsewhereAreVisible:
    def test_a_reject_in_ANOTHER_scope_does_not_contaminate_this_one(
        self, store: Path
    ) -> None:
        _break_one(store, OTHER_SCOPE, "widget-index.md")
        rep = rc.recall(store, SCOPE)
        assert rep.malformed == (), "another scope's defect was attributed here"
        assert rep.listing_total == 4
        assert "none omitted" in rc.render_text(rep), (
            "this scope IS complete; the claim must survive a defect elsewhere"
        )

    def test_but_it_is_still_COUNTED_and_its_scope_NAMED(self, store: Path) -> None:
        """A reader is scope-scoped, so without this a defect in a scope nobody
        recalls today is invisible until somebody does."""
        _break_one(store, OTHER_SCOPE, "widget-index.md")
        text = rc.render_text(rc.recall(store, SCOPE))
        assert "+1 further malformed" in text
        assert OTHER_SCOPE in text
        assert "widget-index.md" not in text, (
            "another scope's FILENAMES are client-identifying; the count is named, not the rows"
        )

    def test_it_shows_up_even_on_scope_absent(self, store: Path) -> None:
        """🔴 The most common status in most repos. A block that skipped it would
        never mention a store-wide defect at all."""
        _break_one(store, OTHER_SCOPE, "widget-index.md")
        text = rc.render_text(rc.recall(store, "never-indexed"))
        assert "+1 further malformed" in text
        assert "NOTHING RECORDED YET" in text  # …and the ordinary case still reads as one

    def test_all_scopes_search_owns_every_reject(self, store: Path) -> None:
        """Under `--all-scopes` nothing is "elsewhere"."""
        _break_one(store, OTHER_SCOPE, "widget-index.md")
        rep = rc.search(store, SCOPE, "readiness", all_scopes=True)
        assert [m.scope for m in rep.malformed] == [OTHER_SCOPE]
        assert rep.malformed_elsewhere == ()


class TestDegradedExitCodes:
    """🔴 `/resume` step 4 branches on zero/non-zero: "print the stderr line
    verbatim, note recall was unavailable, and continue". So a non-zero throws
    away everything the run DID surface — it is only honest when nothing was."""

    def test_content_served_exits_ZERO_with_the_block_in_band(
        self, store: Path, capsys
    ) -> None:
        _break_one(store)
        code = rc.main(["--store", str(store), "--scope", SCOPE, "--list"])
        cap = capsys.readouterr()
        assert code == 0, "a partial read exited non-zero and threw away 4 good entries"
        assert "MALFORMED" in cap.out
        assert "widget-index.md" in cap.out
        assert cap.err == "", "nothing was unavailable; stderr must stay clean"

    def test_nothing_readable_exits_3_with_ONE_stderr_line(
        self, store: Path, capsys
    ) -> None:
        _all_broken_scope(store)
        code = rc.main(["--store", str(store), "--scope", "every-entry-broken", "--list"])
        cap = capsys.readouterr()
        assert code == 3
        # The DETAIL is on stdout — per-entry rows are the whole point…
        assert "widget-index.md" in cap.out
        assert "cog-unit.md" in cap.out
        # …and stderr carries exactly one quotable summary, because the skill
        # says to print it verbatim.
        assert len(cap.err.strip().splitlines()) == 1
        assert "scope-unreadable" in cap.err
        assert "NOT an empty scope" in cap.err

    def test_an_EMPTY_scope_still_exits_zero(self, store: Path, capsys) -> None:
        """The discriminator, at the exit-code level."""
        (store / "made-never-filled").mkdir()
        assert rc.main(["--store", str(store), "--scope", "made-never-filled"]) == 0

    def test_search_follows_the_same_rule(self, store: Path, capsys) -> None:
        _all_broken_scope(store)
        assert rc.main(["--store", str(store), "--scope", SCOPE, "-s", "readiness"]) == 0
        capsys.readouterr()
        code = rc.main(
            ["--store", str(store), "--scope", "every-entry-broken", "-s", "readiness"]
        )
        assert code == 3
        assert "search-unreadable" in capsys.readouterr().err

    def test_the_json_carries_the_rows_not_only_a_count(self, store: Path, capsys) -> None:
        _break_one(store)
        _break_one(store, OTHER_SCOPE, "widget-index.md")
        assert rc.main(["--store", str(store), "--scope", SCOPE, "--json"]) == 0
        blob = json.loads(capsys.readouterr().out)
        assert [m["file"] for m in blob["malformed"]] == ["widget-index.md"]
        assert blob["malformed"][0]["reason"]
        assert blob["malformed_elsewhere"] == [OTHER_SCOPE]
        assert blob["malformed_elsewhere_count"] == 1

    def test_the_search_json_carries_them_too(self, store: Path, capsys) -> None:
        _break_one(store)
        assert rc.main(["--store", str(store), "--scope", SCOPE, "-s", "readiness", "--json"]) == 0
        blob = json.loads(capsys.readouterr().out)
        assert [m["file"] for m in blob["malformed"]] == ["widget-index.md"]

    def test_UNREADABLE_STATUSES_is_the_only_gate(self) -> None:
        """Derived, not hand-listed: a third "nothing could be read" status added
        later cannot quietly exit 0."""
        assert set(rc.UNREADABLE_STATUSES) <= set(rc.STATUS_PRECEDENCE)
        for status in rc.STATUS_PRECEDENCE:
            expected = 3 if status in rc.UNREADABLE_STATUSES else 0
            assert rc._exit_for(status, "x/", ()) == expected


class TestDegradationMutationKills:
    """Break each new guard on purpose; assert THIS guard's own symptom dies and
    the failure is reachable (no earlier check short-circuits it)."""

    def test_kills_the_COLLECT_policy(self, tmp_path: Path) -> None:
        """The reader must not silently go back to fail-closed."""
        mod = _load_mutant(
            tmp_path,
            "m_collect",
            [
                # The anchor moved when `load_store` grew its `visible_scopes`
                # narrowing: the load is now bound to a name so the index can be
                # filtered before it is returned. The MUTATION is unchanged —
                # drop `on_malformed=ON_MALFORMED_COLLECT` and nothing else — and
                # it is still the narrowest expression that can be wrong.
                (
                    "        index = load_index(store, on_malformed=ON_MALFORMED_COLLECT)",
                    "        index = load_index(store)",
                )
            ],
        )
        store = _make_store(tmp_path / "s")
        _break_one(store)
        with pytest.raises(sr.MalformedEntryError):
            mod.recall(store, SCOPE)
        # …and the real module does not.
        assert rc.recall(store, SCOPE).status == "recalled"

    def test_kills_the_MALFORMED_block(self, tmp_path: Path) -> None:
        """🔴 The one that matters most: degrading WITHOUT reporting is worse
        than the collapse it replaced. The mutant still serves the good entries,
        so it is green on every other assertion in this file."""
        mod = _load_mutant(
            tmp_path,
            "m_block",
            [("    out: list[str] = []\n    if malformed:", "    out: list[str] = []\n    if False:")],
        )
        store = _make_store(tmp_path / "s")
        _break_one(store)
        text = mod.render_text(mod.recall(store, SCOPE, mode="list"))
        assert "widget-index.md" not in text
        assert "malformed index entry" not in text
        assert "collector" in text, (
            "the mutant must still SERVE — otherwise this kill is measuring the "
            "wrong thing (a collapse, not a silent drop)"
        )
        # ONE mutation, BOTH surfaces — the point of a single `render_malformed`.
        assert "widget-index.md" not in mod.render_search(mod.search(store, SCOPE, "readiness"))
        assert "widget-index.md" in rc.render_search(rc.search(store, SCOPE, "readiness"))

    def test_kills_the_scope_unreadable_discriminator(self, tmp_path: Path) -> None:
        """Reachable: the scope EXISTS (past `scope-absent`) and holds files
        (which is what makes `scope-empty` the wrong answer). With the guard
        gone, a broken scope reports as an ordinary empty one AND exits 0 —
        exactly the conflation `/resume` acts on."""
        mod = _load_mutant(
            tmp_path,
            "m_unreadable",
            [("    if not entries and bad:", "    if False:")],
        )
        store = _make_store(tmp_path / "s")
        _all_broken_scope(store)
        rep = mod.recall(store, "every-entry-broken")
        assert rep.status == "scope-empty"
        assert mod._exit_for(rep.status, "x/", rep.malformed) == 0
        assert rc.recall(store, "every-entry-broken").status == "scope-unreadable"

    def test_kills_the_search_unreadable_discriminator(self, tmp_path: Path) -> None:
        mod = _load_mutant(
            tmp_path,
            "m_search_unreadable",
            [('            if searched == 0 and bad', "            if False")],
        )
        store = _make_store(tmp_path / "s")
        _all_broken_scope(store)
        assert mod.search(store, "every-entry-broken", "readiness").status == "search-no-match"
        assert rc.search(store, "every-entry-broken", "readiness").status == "search-unreadable"

    def test_kills_the_withdrawn_completeness_claim(self, tmp_path: Path) -> None:
        """With the branch gone, an index three files short says `none omitted`."""
        mod = _load_mutant(
            tmp_path,
            "m_complete",
            [("    elif report.listing_pages <= 1 and n_bad:", "    elif False:")],
        )
        store = _make_store(tmp_path / "s")
        _break_one(store)
        text = mod.render_text(mod.recall(store, SCOPE, mode="list"))
        assert "none omitted" in text, "the mutant must reassert the false claim"
        assert "none omitted" not in rc.render_text(rc.recall(store, SCOPE, mode="list"))

    def test_kills_the_exit_code_gate(self, tmp_path: Path, capsys) -> None:
        """Reachable past every argument guard: a valid `--scope`/`--list` run
        over a scope whose files are all rejects."""
        mod = _load_mutant(
            tmp_path,
            "m_exit",
            [("    if status not in UNREADABLE_STATUSES:", "    if True:")],
        )
        store = _make_store(tmp_path / "s")
        _all_broken_scope(store)
        argv = ["--store", str(store), "--scope", "every-entry-broken", "--list"]
        assert mod.main(argv) == 0
        capsys.readouterr()
        assert rc.main(argv) == 3
        assert "scope-unreadable" in capsys.readouterr().err

    def test_kills_the_scope_attribution(self, tmp_path: Path) -> None:
        """With the reject not attributed to its own scope, a defect anywhere in
        the store makes EVERY scope look broken."""
        mod = _load_mutant(
            tmp_path,
            "m_attribution",
            # The anchor carries the following `try:` so it hits `recall`'s
            # derivation and not `search`'s identical-looking one — see the
            # uniqueness assert in `_load_mutant`.
            [
                (
                    "    bad = index.malformed_in(scope)\n"
                    "    bad_elsewhere = index.malformed_outside((scope,))\n"
                    "\n"
                    "    try:",
                    "    bad = index.malformed\n"
                    "    bad_elsewhere = index.malformed_outside((scope,))\n"
                    "\n"
                    "    try:",
                )
            ],
        )
        store = _make_store(tmp_path / "s")
        _break_one(store, OTHER_SCOPE, "widget-index.md")
        assert len(mod.recall(store, SCOPE).malformed) == 1
        assert rc.recall(store, SCOPE).malformed == ()

    def test_the_control_for_this_section(self, tmp_path: Path) -> None:
        """POSITIVE CONTROL on the harness for these anchors: an unmutated copy
        must degrade exactly as the real module does, or every kill above is a
        claim about the loader rather than about a guard."""
        mod = _load_mutant(tmp_path, "m_degrade_noop", [])
        store = _make_store(tmp_path / "s")
        _break_one(store)
        rep = mod.recall(store, SCOPE, mode="list")
        assert rep.status == "recalled"
        assert len(rep.malformed) == 1
        assert "widget-index.md" in mod.render_text(rep)


# =============================================================================
# The index line's OPEN annotation. See test_subsystem_touch.py's open-actions
# section for the measured incident behind the marker.
# =============================================================================


def _recalled(
    nuance: list[str], ref: str = "svc", nuance_heading: str = sr.NUANCE_HEADING
) -> rc.RecalledEntry:
    """A `RecalledEntry` built the way `read_entry` builds one — EVERY field the
    index row can render, not just the ones a given test is about.

    🔴 The omission mattered. This helper used to populate `open_count` and
    nothing else, so the byte-identical pin below was silently asserting about
    an entry whose near-miss/unverifiable/missing-section fields were all at
    their dataclass defaults — it could not have caught a badge that renders
    unconditionally. `nuance_heading` is a parameter so a test can rename the
    heading and watch the section stop being found, which is the §2.2 bug.
    """
    text = "\n".join(
        ["---", f"service: {ref}", "sensitivity: public", "---", "",
         "## Pointers", "- a", "", nuance_heading, *nuance, ""]
    )
    sections = sr.extract_sections(text, (sr.POINTERS_HEADING, sr.NUANCE_HEADING))
    bullets = sr.parse_journal_bullets(sections.get(sr.NUANCE_HEADING, ""))
    pops = collections.Counter(b.openness_population for b in bullets)
    return rc.RecalledEntry(
        ref=ref, filename=f"{ref}.md", sensitivity="public", sections=sections,
        bullet_count=len(bullets),
        open_count=pops["open"],
        near_miss_count=pops["near-miss"],
        unverifiable_count=pops["unverifiable"],
        missing_sections=tuple(
            h for h in (sr.POINTERS_HEADING, sr.NUANCE_HEADING) if h not in sections
        ),
    )


class TestListingLineOpenAnnotation:
    def test_an_entry_with_nothing_open_renders_BYTE_IDENTICAL_to_before(self):
        """🔴 The index prints for EVERY entry on EVERY read, so a field that
        rendered unconditionally would tax the common case forever to describe the
        rare one. The annotation is conditional for that reason, and this pins it:
        the no-open line ends at the sensitivity, exactly as it always has."""
        # The pre-change format, SPELLED OUT here rather than copied from the
        # renderer — a literal lifted from the implementation would agree with it
        # by construction and could not detect the format moving.
        expected = "  " + "svc".ljust(12) + "  " + f"{1:>3}" + " nuance   " + "public"
        line = rc.listing_line(_recalled(["- 2026-08-15: an ordinary lesson."]), 12)
        assert line == expected

    def test_an_open_entry_is_annotated_with_its_COUNT(self):
        line = rc.listing_line(
            _recalled([
                "- 2026-08-01: OPEN: one.",
                "- 2026-08-02: OPEN: two.",
                "- 2026-08-03: an ordinary lesson.",
            ]), 12,
        )
        assert line.endswith("🔴 2 OPEN")
        assert "3 nuance" in line

    def test_a_RESOLVED_bullet_does_not_count_as_open(self):
        """The whole point of the marker: closing one has to be visible on the
        index, or nobody is rewarded for closing it."""
        line = rc.listing_line(
            _recalled(["- 2026-08-02: RESOLVED b83bfb584: closed."]), 12
        )
        assert "OPEN" not in line

    def test_an_unmarked_action_does_NOT_reach_the_index_line(self):
        """Deliberate: the index is the highest-traffic surface in the tool, and a
        two-phrasing guess with unmeasured recall does not belong on a line that
        cannot carry its own caveat. It is reported by `--validate`, which can."""
        line = rc.listing_line(
            _recalled(["- 2026-07-24: FIX (1 line): widen the widget timeout."]), 12
        )
        assert "OPEN" not in line

    def test_read_entry_populates_open_count_from_disk(self, tmp_path):
        store = tmp_path / "s"
        (store / "sc").mkdir(parents=True)
        (store / "sc" / "svc.md").write_text(
            "\n".join(["---", "service: svc", "scope: sc", "---", "",
                       "## Nuance / work-history",
                       "- 2026-08-01: OPEN: outstanding.",
                       "- 2026-08-02: an ordinary lesson.", ""]),
            encoding="utf-8",
        )
        index = sr.load_index(store)
        entry = index.entries("sc")[0]
        assert rc.read_entry(store, entry).open_count == 1


class TestReadEntryOpenCountIsNotFixtureCollapsed:
    """An audit found `read_entry`'s open count mutation-blind: replacing
    `if b.is_open` with `if b.openness is not None` SURVIVED the whole suite,
    because the only disk-level fixture had no `RESOLVED` bullet — so "open" and
    "declared anything" produced identical output.

    `claude/RULES.md`: a fixture of default or absent sibling values collapses
    distinct implementations into the same result. The fix is a fixture whose
    values are pairwise DISTINCT — here, all three openness states present at
    once, so any predicate that confuses two of them moves the number.
    """

    def _store(self, tmp_path, bullets):
        store = tmp_path / "s"
        (store / "sc").mkdir(parents=True)
        (store / "sc" / "svc.md").write_text(
            "\n".join(["---", "service: svc", "scope: sc", "---", "",
                       "## Nuance / work-history", *bullets, ""]),
            encoding="utf-8",
        )
        return store

    def test_all_three_openness_states_at_once_and_only_OPEN_is_counted(self, tmp_path):
        store = self._store(tmp_path, [
            "- 2026-08-01: OPEN: still outstanding.",
            "- 2026-08-02: RESOLVED b83bfb584: closed by that commit.",
            "- 2026-08-03: an ordinary durable lesson, no marker.",
        ])
        index = sr.load_index(store)
        got = rc.read_entry(store, index.entries("sc")[0])
        assert got.bullet_count == 3, "the fixture must exercise all three states"
        assert got.open_count == 1, (
            "counted something other than the OPEN bullet — a predicate of "
            "'declared anything' gives 2 here, and 'any bullet' gives 3"
        )


# =============================================================================
# The index row's three ADDITIONAL badges: near-miss, unverifiable, and the
# missing-section note. Items 2 and 1 of
# `claudedocs/proposal-entry-shape-explicit.md` §4.2.
#
# 🔴 WHAT THESE CLOSE, MEASURED, NOT READ OFF THE SOURCE. Two silent-failure
# paths shared one observable — a row that reads `0 nuance` with no badge:
#
#   §2.4  2 bullets in the live store attempted an openness marker and missed
#         the grammar, against 8 that declare `OPEN:` and parse (re-measured
#         2026-08-19 over 53 entries / 323 nuance bullets; the proposal's
#         "2 of 10 textual `OPEN:`" denominator did NOT reproduce — a raw grep
#         returns 11 — but its near-miss count of 2 did).
#         A near-miss was byte-identical to no marker on the read surface, so
#         the population MOST likely to hold a stale open action was the one
#         no routine surface reported. `--validate` reported it; `/resume`
#         does not run `--validate`.
#   §2.2  A renamed `## Nuance / work-history` heading zeroes the bullet count
#         AND deletes the `🔴 N OPEN` badge, and `--validate` returns `OK` at
#         exit 0. `missing_sections` already existed and was rendered ONLY
#         under a printed body — and the digest prints one body out of N.
#
# The store was NOT touched to produce any of this: every fixture below is
# synthetic, under `tmp_path`, with invented names. This repo is PUBLIC.
# =============================================================================


class TestNearMissAndUnverifiableReachTheIndexRow:
    """§2.4 — a marker that was ATTEMPTED and missed is not "no marker"."""

    def test_a_near_miss_bullet_is_annotated_with_its_COUNT(self):
        """The regression. Before this badge the line below rendered exactly as
        an entry with two ordinary lessons: the emphasis-wrapped `**OPEN:**` and
        the parenthetical `RESOLVED <sha> (repo):` both declare nothing."""
        line = rc.listing_line(
            _recalled([
                "- 2026-08-02: **OPEN:** an emphasis-wrapped marker.",
                "- 2026-08-03: RESOLVED abc1234 (repo): a parenthetical first.",
            ]), 12,
        )
        assert "🔴 2 NEAR-MISS" in line, line
        assert "OPEN" not in line.replace("NEAR-MISS", ""), (
            "a near-miss was counted as a declared OPEN — it declares nothing"
        )

    def test_a_near_miss_does_not_render_as_a_CLEAN_row(self):
        """The differential the badge exists for: identical bullets, one of them
        spelled so the marker parses. The two rows must not be the same string."""
        parses = rc.listing_line(_recalled(["- 2026-08-02: OPEN: the retry budget."]), 12)
        misses = rc.listing_line(_recalled(["- 2026-08-02: **OPEN:** the retry budget."]), 12)
        clean = rc.listing_line(_recalled(["- 2026-08-02: an ordinary lesson."]), 12)
        assert parses != misses, "the two spellings still render identically"
        assert misses != clean, (
            "a bullet that TRIED to declare an action is still byte-identical to "
            "one that never tried — which is the §2.4 bug, unfixed"
        )

    def test_an_unverifiable_closure_is_annotated_with_its_COUNT(self):
        line = rc.listing_line(
            _recalled(["- 2026-08-04: RESOLVED: closed, no sha named."]), 12
        )
        assert "⚠ 1 UNVERIFIABLE" in line, line

    def test_a_RESOLVED_WITH_a_sha_carries_NO_unverifiable_badge(self):
        """The sha is the whole point of the marker: naming one has to be
        visible, or nobody is rewarded for making the claim checkable."""
        line = rc.listing_line(
            _recalled(["- 2026-08-05: RESOLVED b83bfb584: closed and checkable."]), 12
        )
        assert "UNVERIFIABLE" not in line, line
        assert "NEAR-MISS" not in line, line

    def test_an_unmarked_action_still_does_NOT_reach_the_index_row(self):
        """Unchanged, and deliberately so. The unmarked-action detector is a
        FLOOR with unknown recall over two measured phrasings; a guess that
        cannot state its own recall does not belong on a line with no room for a
        caveat. `--validate` reports it, and can say so."""
        line = rc.listing_line(
            _recalled(["- 2026-07-24: FIX (1 line): widen the widget timeout."]), 12
        )
        for badge in ("OPEN", "NEAR-MISS", "UNVERIFIABLE", "NO "):
            assert badge not in line, (badge, line)

    def test_the_badges_are_in_VALIDATE_order_and_count_DISTINCT_populations(self):
        """🔴 PAIRWISE-DISTINCT COUNTS, so no two badges can be swapped and stay
        green. 1 open, 2 near-miss, 3 unverifiable: any assignment that reads the
        wrong population moves a printed number.

        The order is `--validate`'s (declared → near-miss → unverifiable) and is
        asserted as one whole string, not three `in` checks — a substring set is
        satisfied by any permutation.
        """
        line = rc.listing_line(
            _recalled([
                "- 2026-08-01: OPEN: still outstanding.",
                "- 2026-08-02: **OPEN:** emphasis-wrapped, declares nothing.",
                "- 2026-08-03: RESOLVED abc1234 (repo): parenthetical, declares nothing.",
                "- 2026-08-04: RESOLVED: no sha (a).",
                "- 2026-08-05: RESOLVED: no sha (b).",
                "- 2026-08-06: RESOLVED: no sha (c).",
                "- 2026-08-07: RESOLVED b83bfb584: closed and checkable.",
                "- 2026-08-08: an ordinary durable lesson.",
            ]), 12,
        )
        assert line == (
            "  " + "svc".ljust(12) + "  " + f"{8:>3}" + " nuance   public"
            + "   🔴 1 OPEN   🔴 2 NEAR-MISS   ⚠ 3 UNVERIFIABLE"
        ), line

    def test_read_entry_populates_the_counts_FROM_DISK(self, tmp_path):
        """The renderer tests above build a `RecalledEntry` by hand. This one
        goes through the real disk path, because a field the renderer prints and
        `read_entry` never sets is a badge that can only ever be zero."""
        store = tmp_path / "s"
        (store / "sc").mkdir(parents=True)
        (store / "sc" / "svc.md").write_text(
            "\n".join(["---", "service: svc", "scope: sc", "---", "",
                       "## Nuance / work-history",
                       "- 2026-08-01: OPEN: still outstanding.",
                       "- 2026-08-02: **OPEN:** emphasis-wrapped.",
                       "- 2026-08-03: RESOLVED abc1234 (repo): parenthetical.",
                       "- 2026-08-04: RESOLVED: no sha (a).",
                       "- 2026-08-05: RESOLVED: no sha (b).",
                       "- 2026-08-06: RESOLVED: no sha (c).",
                       "- 2026-08-07: RESOLVED b83bfb584: checkable.",
                       "- 2026-08-08: an ordinary durable lesson.", ""]),
            encoding="utf-8",
        )
        index = sr.load_index(store)
        got = rc.read_entry(store, index.entries("sc")[0])
        assert (got.bullet_count, got.open_count) == (8, 1)
        assert got.near_miss_count == 2, (
            "read_entry did not count the near-miss population — 'any bullet with "
            "no marker' gives 4 here and counting every bullet gives 8"
        )
        assert got.unverifiable_count == 3, (
            "read_entry did not count sha-less RESOLVED bullets — counting all "
            "RESOLVED gives 4 and counting none gives 0"
        )

    def test_the_counts_reach_the_RENDERED_index_block(self, tmp_path):
        """End to end: disk → `read_entry` → `render_text`. A badge that only
        appears when a test calls `listing_line` directly is a badge `/resume`
        never sees."""
        store = tmp_path / "s"
        (store / "sc").mkdir(parents=True)
        (store / "sc" / "svc.md").write_text(
            "\n".join(["---", "service: svc", "scope: sc", "sensitivity: public", "---", "",
                       "## Pointers", "- a", "",
                       "## Nuance / work-history",
                       "- 2026-08-02: **OPEN:** emphasis-wrapped.", ""]),
            encoding="utf-8",
        )
        text = rc.render_text(rc.recall(store, "sc", mode="list"))
        row = next(l for l in text.splitlines() if l.strip().startswith("svc "))
        assert "🔴 1 NEAR-MISS" in row, row


class TestAMissingSurfacedSectionReachesTheIndexRow:
    """§2.2 — the differential control, as a test.

    Two entries differing in EXACTLY ONE variable (the nuance heading) and
    carrying the same `OPEN:` marker. Before this badge the renamed one rendered
    `0 nuance` with no badge at all, which is what a well-formed entry with an
    empty work-history renders — so `/resume`, which consumes exactly this row,
    could not tell a curated entry with an open action from an empty one.
    """

    NUANCE = [
        "- 2026-08-01: OPEN: the retry budget is still unset.",
        "- 2026-08-02: an ordinary lesson.",
    ]

    def _store(self, tmp_path, heading: str, ref: str = "payments-api"):
        store = tmp_path / "s"
        (store / "example-scope").mkdir(parents=True, exist_ok=True)
        (store / "example-scope" / f"{ref}.md").write_text(
            "\n".join(["---", f"service: {ref}", "scope: example-scope",
                       "sensitivity: public", "---", "",
                       "## What it is", "", "A synthetic fixture.", "",
                       "## Pointers", "- `some/path` — a pointer.", "",
                       heading, *self.NUANCE, ""]),
            encoding="utf-8",
        )
        return store

    def _row(self, store, ref):
        rep = rc.recall(store, "example-scope", mode="list")
        return rc.listing_line(next(e for e in rep.listing if e.ref == ref), 14)

    def test_the_renamed_heading_is_ANNOUNCED_on_the_row(self, tmp_path):
        store = self._store(tmp_path, "## Nuance and work history")
        row = self._row(store, "payments-api")
        assert "🔴 NO Nuance / work-history" in row, row

    def test_the_renamed_row_no_longer_matches_a_WELL_FORMED_EMPTY_one(self, tmp_path):
        """🔴 THE ACTUAL BUG, stated as a difference rather than as a substring.
        A word-level check would pass on a row that merely happened to spell
        something; this compares the two renders that used to be equal."""
        renamed = self._row(self._store(tmp_path / "a", "## Nuance and work history"),
                            "payments-api")
        store_empty = tmp_path / "b" / "s"
        (store_empty / "example-scope").mkdir(parents=True)
        (store_empty / "example-scope" / "payments-api.md").write_text(
            "\n".join(["---", "service: payments-api", "scope: example-scope",
                       "sensitivity: public", "---", "",
                       "## Pointers", "- `some/path` — a pointer.", "",
                       "## Nuance / work-history", ""]),
            encoding="utf-8",
        )
        empty = self._row(store_empty, "payments-api")
        assert renamed != empty, (
            "an entry whose OPEN marker the parser never reached still renders "
            "byte-identically to a genuinely empty one — §2.2 unfixed"
        )

    def test_the_CORRECT_heading_renders_the_row_UNCHANGED(self, tmp_path):
        """The other half of the differential: the conforming entry keeps its
        badge and grows nothing."""
        row = self._row(self._store(tmp_path, sr.NUANCE_HEADING), "payments-api")
        assert row == (
            "  " + "payments-api".ljust(14) + "  " + f"{2:>3}" + " nuance   public"
            + "   🔴 1 OPEN"
        ), row

    def test_a_missing_POINTERS_section_names_POINTERS(self, tmp_path):
        """The badge names WHICH heading. `NO Pointers` and `NO Nuance /
        work-history` are different facts with different next actions, and a
        badge that said only `NO SECTION` would collapse them."""
        store = tmp_path / "s"
        (store / "sc").mkdir(parents=True)
        (store / "sc" / "svc.md").write_text(
            "\n".join(["---", "service: svc", "scope: sc", "sensitivity: public", "---", "",
                       "## Nuance / work-history",
                       "- 2026-08-01: OPEN: outstanding.", ""]),
            encoding="utf-8",
        )
        row = rc.listing_line(rc.recall(store, "sc", mode="list").listing[0], 12)
        assert row.endswith("🔴 1 OPEN   🔴 NO Pointers"), row

    def test_BOTH_missing_sections_are_named_in_the_stored_order(self, tmp_path):
        store = tmp_path / "s"
        (store / "sc").mkdir(parents=True)
        (store / "sc" / "svc.md").write_text(
            "\n".join(["---", "service: svc", "scope: sc", "sensitivity: public", "---", "",
                       "## What it is", "", "prose only, no spine.", ""]),
            encoding="utf-8",
        )
        row = rc.listing_line(rc.recall(store, "sc", mode="list").listing[0], 12)
        assert row.endswith("🔴 NO Pointers, Nuance / work-history"), row

    def test_short_heading_drops_the_ATX_marker_and_nothing_else(self):
        assert rc.short_heading(sr.NUANCE_HEADING) == "Nuance / work-history"
        assert rc.short_heading(sr.POINTERS_HEADING) == "Pointers"


class TestTheCommonCaseRowIsUnchanged:
    """🔴 THE INDEX PRINTS FOR EVERY ENTRY ON EVERY READ. Measured over the live
    store on 2026-08-19: of 53 entries, 1 would carry a near-miss badge, 0 an
    unverifiable badge and 0 a missing-section badge. So 52 rows must render
    byte-identically to what they rendered before this change, or three rare
    signals have taxed the common case forever to describe them.
    """

    def test_a_conforming_entry_with_nothing_flagged_renders_the_BARE_row(self):
        """The pre-change format, SPELLED OUT rather than lifted from the
        renderer — a literal copied from the implementation agrees with it by
        construction and cannot detect the format moving."""
        expected = "  " + "svc".ljust(12) + "  " + f"{2:>3}" + " nuance   public"
        line = rc.listing_line(
            _recalled([
                "- 2026-08-15: an ordinary lesson.",
                "- 2026-08-16: RESOLVED b83bfb584: closed and checkable.",
            ]), 12,
        )
        assert line == expected, line

    def test_the_whole_index_BLOCK_is_unchanged_for_a_conforming_scope(
        self, store: Path
    ) -> None:
        """Not one row — every row of the shared fixture store, which no test in
        this file wrote a near-miss or a renamed heading into."""
        rows = [
            rc.listing_line(e, 16)
            for e in rc.recall(store, SCOPE, mode="list").listing
        ]
        assert rows
        for row in rows:
            for badge in ("NEAR-MISS", "UNVERIFIABLE", "NO Pointers", "NO Nuance"):
                assert badge not in row, (
                    f"a conforming entry grew a `{badge}` badge — the new signals "
                    f"are supposed to be conditional"
                )


def _store_with(tmp_path: Path, name: str, nuance: list[str],
                heading: str = sr.NUANCE_HEADING) -> Path:
    """A one-entry store whose single row carries whatever badge `nuance` earns.

    🔴 The badge explanations are CONDITIONAL, so a test that wants to see one
    must PUT one on a row. Asserting against the shared `store` fixture is what
    these tests used to do, and it worked only while the caveat named every badge
    unconditionally — i.e. it was asserting about prose, not about a badge.
    """
    root = tmp_path / "condstore"
    (root / SCOPE).mkdir(parents=True, exist_ok=True)
    (root / SCOPE / f"{name}.md").write_text(
        "\n".join(["---", f"service: {name}", "sensitivity: public", "---", "",
                    "## Pointers", "- a", "", heading, *nuance, ""]),
        encoding="utf-8",
    )
    return root


class TestTheCaveatExplainsEveryBadgeItPrints:
    """🔴 A BADGE THE CAVEAT DOES NOT NAME IS A GLYPH THE READER GUESSES AT, and
    the caveat is the one place this tool states what it can and cannot see. The
    `🔴 N OPEN` clause has been there since the badge shipped; these three
    arrived with the same obligation.

    🔴 STRENGTHENED when the explanations became conditional. These used to assert
    against a fixture store that contains NO near-miss and NO renamed heading — so
    they passed on prose alone and could not have told you whether the caveat
    matched the ROWS. Each now puts the badge on a row first, which is what the
    class name claims to test, and the complementary "no badge ⇒ no clause" half
    lives in `TestCaveatBadgeClausesAreConditional`.
    """

    def test_the_caveat_names_the_NEAR_MISS_badge_and_what_it_costs(
        self, tmp_path: Path
    ):
        root = _store_with(tmp_path, "svc", ["- 2026-08-19: OPEN gaps found here."])
        rep = rc.recall(root, SCOPE)
        assert any("NEAR-MISS" in rc.listing_line(e, 16) for e in rep.listing), (
            "fixture must actually earn the badge, or this asserts about prose"
        )
        caveat = rep.caveat
        assert "NEAR-MISS" in caveat
        assert "missed the grammar" in caveat
        assert "short by up to N" in caveat, (
            "the caveat names the badge without saying what it implies about the "
            "OPEN count beside it — which is the whole reason it is on the row"
        )

    def test_the_caveat_names_the_UNVERIFIABLE_badge(self, tmp_path: Path):
        root = _store_with(tmp_path, "svc", ["- 2026-08-19: RESOLVED: closed, no sha."])
        rep = rc.recall(root, SCOPE)
        assert any("UNVERIFIABLE" in rc.listing_line(e, 16) for e in rep.listing)
        caveat = rep.caveat
        assert "UNVERIFIABLE" in caveat
        assert "name no sha" in caveat

    def test_the_caveat_says_a_missing_heading_makes_the_counts_PARSE_FAILURES(
        self, tmp_path: Path
    ):
        """The claim that matters: `0 nuance` on such a row is not a reading."""
        root = _store_with(tmp_path, "svc", ["- 2026-08-19: a note."],
                           heading="## Nuance and work history")
        rep = rc.recall(root, SCOPE)
        assert any("NO " in rc.listing_line(e, 16) for e in rep.listing)
        caveat = rep.caveat
        assert "NO <heading>" in caveat
        assert "0 BY PARSE FAILURE and not by measurement" in caveat

    def test_the_caveat_is_still_ONE_spelling_on_every_surface(self, store: Path):
        rep = rc.recall(store, SCOPE)
        assert rep.caveat in rc.render_text(rep)
        assert rc.report_json(rep)["caveat"] == rep.caveat


# --- the caveat's badge explanations are CONDITIONAL ----------------------------
#
# Measured on the first real session to use the resume→recall flow: the caveat is
# ~1.5 KB and is paid PER CALL, so a targeted `--ref` lookup — the cheap operation
# the design encourages — spent 27% of its output explaining NEAR-MISS,
# UNVERIFIABLE and NO <heading> while showing NONE of them. The badges already
# render conditionally; the prose explaining them did not.


class TestBadgesPresent:
    """`badges_present` reports what the OUTPUT will render, nothing wider."""

    def test_no_entries_yields_an_empty_set(self) -> None:
        assert rc.badges_present(()) == frozenset()

    def test_a_clean_entry_yields_an_empty_set(self) -> None:
        """POSITIVE CONTROL's partner: an entry with every count at zero must not
        claim a badge, or the conditional degenerates to unconditional."""
        e = _recalled(["- 2026-08-19: a plain dated bullet."])
        assert e.near_miss_count == 0 and e.unverifiable_count == 0
        assert rc.badges_present((e,)) == frozenset()

    def test_a_near_miss_bullet_yields_only_NEAR_MISS(self) -> None:
        e = _recalled(["- 2026-08-19: OPEN gaps found while fixing the above."])
        assert e.near_miss_count > 0, "fixture must actually produce a near-miss"
        assert rc.badges_present((e,)) == frozenset({rc.BADGE_NEAR_MISS})

    def test_an_unverifiable_bullet_yields_only_UNVERIFIABLE(self) -> None:
        e = _recalled(["- 2026-08-19: RESOLVED: closed it, no sha named."])
        assert e.unverifiable_count > 0, "fixture must actually produce one"
        assert rc.badges_present((e,)) == frozenset({rc.BADGE_UNVERIFIABLE})

    def test_a_renamed_heading_yields_only_MISSING_HEADING(self) -> None:
        e = _recalled(["- 2026-08-19: a note."], nuance_heading="## Nuance and work history")
        assert e.missing_sections, "fixture must actually lose the section"
        assert rc.badges_present((e,)) == frozenset({rc.BADGE_MISSING_HEADING})

    def test_the_set_is_a_UNION_across_entries_not_the_first_hit(self) -> None:
        """The index renders many rows; one row's badge must not mask another's."""
        near = _recalled(["- 2026-08-19: OPEN gaps found."], ref="a")
        unver = _recalled(["- 2026-08-19: RESOLVED: no sha."], ref="b")
        gone = _recalled(["- 2026-08-19: x."], ref="c", nuance_heading="## Renamed")
        got = rc.badges_present((near, unver, gone))
        assert got == frozenset(
            {rc.BADGE_NEAR_MISS, rc.BADGE_UNVERIFIABLE, rc.BADGE_MISSING_HEADING}
        )


class TestCaveatBadgeClausesAreConditional:
    SCOPE = "example-scope/"

    # --- what must NEVER be gated -------------------------------------------------

    def test_the_UNCONDITIONAL_contract_survives_an_empty_badge_set(self) -> None:
        """🔴 The half that makes this safe rather than merely cheaper. These
        sentences are the anti-confabulation contract and apply to every read."""
        text = rc.caveat_text(self.SCOPE, frozenset())
        assert "RECALL, NOT LIVE OBSERVATION" in text
        assert "This window CANNOT see" in text
        assert "POINTER to verify" in text
        assert rc.SENSITIVITY_FAIL_SAFE in text
        assert "never copy an" in text

    def test_the_OPEN_ABSENCE_warning_is_NOT_gated(self) -> None:
        """🔴 The asymmetry, pinned. The OPEN clause ends "the absence of that
        marker means nothing was declared, NOT that nothing is open" — a warning
        about a MISSING badge. Gating it on a badge being PRESENT would delete it
        in exactly the case it was written for."""
        for badges in (frozenset(), frozenset({rc.BADGE_NEAR_MISS}), None):
            text = rc.caveat_text(self.SCOPE, badges)
            assert "NOT that nothing is open" in text, badges

    # --- what must be gated -------------------------------------------------------

    def test_an_empty_badge_set_drops_ALL_THREE_explanations(self) -> None:
        text = rc.caveat_text(self.SCOPE, frozenset())
        for absent in ("N NEAR-MISS", "N UNVERIFIABLE", "NO <heading>"):
            assert absent not in text, absent

    def test_each_badge_brings_ONLY_its_own_clause(self) -> None:
        """Killed separately, so no clause can hide behind another."""
        cases = {
            rc.BADGE_NEAR_MISS: ("N NEAR-MISS", ("N UNVERIFIABLE", "NO <heading>")),
            rc.BADGE_UNVERIFIABLE: ("N UNVERIFIABLE", ("N NEAR-MISS", "NO <heading>")),
            rc.BADGE_MISSING_HEADING: ("NO <heading>", ("N NEAR-MISS", "N UNVERIFIABLE")),
        }
        for badge, (present, absent) in cases.items():
            text = rc.caveat_text(self.SCOPE, frozenset({badge}))
            assert present in text, f"{badge} should explain {present}"
            for a in absent:
                assert a not in text, f"{badge} should NOT explain {a}"

    def test_all_three_badges_reproduce_the_FULL_prose(self) -> None:
        """The unconditional text is the ceiling, not a different text: with every
        badge present the caveat must equal the `badges=None` rendering."""
        every = frozenset(
            {rc.BADGE_NEAR_MISS, rc.BADGE_UNVERIFIABLE, rc.BADGE_MISSING_HEADING}
        )
        assert rc.caveat_text(self.SCOPE, every) == rc.caveat_text(self.SCOPE, None)

    def test_the_lead_phrase_AGREES_with_the_clause_count(self) -> None:
        """Prose that says "Three further badges" above one clause is the kind of
        near-miss this module exists to complain about."""
        one = rc.caveat_text(self.SCOPE, frozenset({rc.BADGE_NEAR_MISS}))
        assert "One further badge says" in one and "Three further" not in one
        two = rc.caveat_text(
            self.SCOPE, frozenset({rc.BADGE_NEAR_MISS, rc.BADGE_UNVERIFIABLE})
        )
        assert "Two further badges say" in two
        three = rc.caveat_text(
            self.SCOPE,
            frozenset({rc.BADGE_NEAR_MISS, rc.BADGE_UNVERIFIABLE, rc.BADGE_MISSING_HEADING}),
        )
        assert "Three further badges say" in three

    def test_badges_None_is_FAIL_SAFE_toward_saying_more(self) -> None:
        """A caller that computed nothing gets the full text, never a silent trim."""
        full = rc.caveat_text(self.SCOPE, None)
        for clause in ("N NEAR-MISS", "N UNVERIFIABLE", "NO <heading>"):
            assert clause in full, clause

    def test_it_is_SHORTER_when_nothing_is_badged(self) -> None:
        """The point of the change, asserted as a relation rather than a constant
        so a reword cannot make it vacuous and cannot pin a number that drifts."""
        assert len(rc.caveat_text(self.SCOPE, frozenset())) < len(
            rc.caveat_text(self.SCOPE, None)
        )


class TestTheCaveatDescribesTHISReportsRows:
    """🔴 The regression test for the bug this change shipped with once.

    `badges_present` was first wired to `RecallReport.entries` — which holds only
    the FEATURED bodies — while badges are rendered by `listing_line` over
    `report.listing`. On a real index whose visible `🔴 2 NEAR-MISS` row was not
    among the featured entries, the explanation silently vanished. It read
    correctly and was wrong; only running it caught it.
    """

    def test_a_badge_in_LISTING_but_not_in_ENTRIES_is_still_explained(self) -> None:
        badged = _recalled(["- 2026-08-19: OPEN gaps found."], ref="badged")
        featured = _recalled(["- 2026-08-19: a clean bullet."], ref="featured")
        assert badged.near_miss_count > 0
        assert featured.near_miss_count == 0
        report = rc.RecallReport(
            status="recalled", scope="example-scope", store_root="/nowhere",
            entries=(featured,), listing=(badged, featured), total_in_scope=2,
        )
        assert "N NEAR-MISS" in report.caveat, (
            "the caveat must describe the rows the INDEX renders, not the featured subset"
        )

    def test_a_clean_listing_gets_no_badge_prose(self) -> None:
        clean = _recalled(["- 2026-08-19: a clean bullet."], ref="clean")
        report = rc.RecallReport(
            status="recalled", scope="example-scope", store_root="/nowhere",
            entries=(clean,), listing=(clean,), total_in_scope=1,
        )
        for absent in ("N NEAR-MISS", "N UNVERIFIABLE", "NO <heading>"):
            assert absent not in report.caveat, absent
        assert "NOT that nothing is open" in report.caveat


class TestTheSearchReportCaveat:
    def test_search_explains_no_badges_because_it_renders_none(self) -> None:
        """Search prints matched Hunks, never an index row, so no badge can appear
        in its output. It also has no `entries` field at all — asking for one was
        an AttributeError waiting to happen."""
        report = rc.SearchReport(
            status="searched", scope="example-scope", store_root="/nowhere",
            query="anything", scopes_searched=("example-scope",),
        )
        assert not hasattr(report, "entries")
        text = report.caveat
        for absent in ("N NEAR-MISS", "N UNVERIFIABLE", "NO <heading>"):
            assert absent not in text, absent
        # …but the contract still ships.
        assert "RECALL, NOT LIVE OBSERVATION" in text
        assert "NOT that nothing is open" in text
