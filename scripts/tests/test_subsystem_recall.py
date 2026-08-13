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

import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "scripts" / "lib" / "subsystem_recall.py"
TOUCH_PATH = ROOT / "scripts" / "lib" / "subsystem_touch.py"
RESUME_DOC = ROOT / "claude" / "skills" / "resume" / "SKILL.md"
HANDOFF_DOC = ROOT / "claude" / "skills" / "handoff" / "SKILL.md"
# The on-demand evidence sidecar. Step 4's IMPERATIVES stay in HANDOFF_DOC; the
# measured rationale behind them lives here and costs nothing until it is read.
HANDOFF_REFERENCE = (
    ROOT / "claude" / "skills" / "handoff" / "reference" / "index-write.md"
)
ANALYZE_DOC = ROOT / "claude" / "skills" / "analyze-service" / "SKILL.md"

sys.path.insert(0, str(ROOT / "scripts" / "lib"))
sys.path.insert(0, str(ROOT / "scripts"))

from testlib.skills_mapping import (  # noqa: E402
    assert_skills_mapping_deploys_repo_skills,
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

WHAT_IT_IS = "A durable thing that must NEVER appear in a recall block."
POINTER_LINE = "- ops skill `manage-widget` — invoke it for restarts"
NUANCE_LINE = "- 2026-01-02: the readiness probe lies for 40s after a reload."


def _entry(
    service: str,
    scope: str,
    *,
    aliases=(),
    kind=None,
    sensitivity=None,
    pointers: str | None = POINTER_LINE,
    nuance: str | None = NUANCE_LINE,
    extra_body: str = "",
) -> str:
    lines = ["---", f"service: {service}", f"scope: {scope}"]
    if aliases:
        lines.append("aliases: [" + ", ".join(aliases) + "]")
    if kind:
        lines.append(f"kind: {kind}")
    if sensitivity is not None:
        lines.append(f"sensitivity: {sensitivity}")
    lines += ["---", "", "## What it is", WHAT_IT_IS, ""]
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
        later cannot quietly go unreached."""
        (store / "made-never-filled").mkdir()
        seen = {
            rc.recall(store, "never-indexed").status,
            rc.recall(store, "made-never-filled").status,
            rc.recall(store, SCOPE, ref="no-such-subsystem").status,
            rc.recall(store, SCOPE, ref="weekly-digest").status,
            rc.recall(store, SCOPE).status,
        }
        assert seen == set(rc.STATUS_PRECEDENCE)

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

    def test_a_malformed_entry_RAISES_the_RESOLVERS_sentinel(self, store: Path) -> None:
        """Fail-closed, and deliberately NOT caught here: an interactive caller
        must be told the store is broken rather than handed a short index."""
        (store / SCOPE / "no-front-matter.md").write_text("just prose\n", encoding="utf-8")
        with pytest.raises(sr.MalformedEntryError) as exc:
            rc.recall(store, SCOPE)
        assert "malformed index entry" in str(exc.value)

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
# WHAT IS SURFACED — the two sections, and NOTHING else.
# =============================================================================


class TestSurfacesOnlyTheTwoSections:
    def test_what_it_is_is_NOT_surfaced(self, store: Path) -> None:
        """🔴 The brief's hard line: recall, not a dump. `## What it is` is
        durable boilerplate a resuming session either knows or can open."""
        rep = rc.recall(store, SCOPE)
        text = rc.render_text(rep)
        assert WHAT_IT_IS not in text
        assert "## What it is" not in text
        blob = json.dumps(rc.report_json(rep))
        assert WHAT_IT_IS not in blob

    def test_both_wanted_sections_ARE_surfaced(self, store: Path) -> None:
        text = rc.render_text(rc.recall(store, SCOPE))
        assert rc.POINTERS_HEADING in text
        assert rc.NUANCE_HEADING in text
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
        diff of them shows churn that is not there."""
        for mode in rc.RECALL_MODES:
            a = rc.render_text(rc.recall(store, SCOPE, mode=mode))
            b = rc.render_text(rc.recall(store, SCOPE, mode=mode))
            assert a == b, mode
        refs = [e.ref for e in rc.recall(store, SCOPE, mode="full").entries]
        assert refs == sorted(refs)
        listed = [e.ref for e in rc.recall(store, SCOPE).listing]
        assert listed == sorted(listed)

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
        (d / f"{slug}.md").write_text(
            _entry(slug, BIG_SCOPE, pointers=f"- pointer for {slug}", nuance=nuance or None),
            encoding="utf-8",
        )
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
        assert [row["ref"] for row in blob["listing"]] == [
            f"widget-{i:02d}" for i in range(1, BIG_N + 1)
        ]
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
        assert "1 bullet " in line, "the size signal is missing"
        # It stays ONE line, or "~60 B per entry" is not a bound at all.
        assert "\n" not in line

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
        assert "1 bullet " in line

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
        with pytest.raises(sr.MalformedEntryError):
            rc.recall(store, SCOPE)
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
        help_text = rc._build_parser().format_help()
        assert "never writes to the store" in help_text
        assert "never touches the network" in help_text

    def test_list_prints_the_index_and_no_bodies(self, store: Path, capsys) -> None:
        assert rc.main(["--store", str(store), "--scope", SCOPE, "--list"]) == 0
        out = capsys.readouterr().out
        assert "NO ENTRY BODIES WERE PRINTED" in out
        assert POINTER_LINE not in out
        for ref in ("collector", "status-bar", "weekly-digest.process"):
            assert ref in out

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
        for flag in ("--list", "--ref", "--limit"):
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
        for status in ("scope-absent", "scope-empty"):
            assert f"`{status}`" in doc, (
                f"claude/skills/resume/SKILL.md never mentions `{status}`, which "
                f"subsystem_recall.recall can emit and which the step must not "
                f"report as an error."
            )

    def test_the_recall_step_comes_AFTER_the_handoff_is_read(self) -> None:
        """Structural, not a phrase: recall is context for a doc already read,
        not a substitute for reading it."""
        doc = RESUME_DOC.read_text(encoding="utf-8")
        read_it = doc.index("**Read it fully.**")
        # The INVOCATION, not the bare filename: `resume-state.sh` is also named
        # in step 1's prose, so `.index()` on the bare name finds a point BEFORE
        # the handoff is read and the ordering claim inverts.
        reconcile = doc.index("bash ~/workspace/devrc/scripts/resume-state.sh")
        step = doc.index("subsystem_recall.py")
        report = doc.index("**Report**")
        assert read_it < reconcile < step < report

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
        home_nix = (ROOT / "nix" / "home.nix").read_text(encoding="utf-8")
        # Structural, and shared with test_subsystem_resolver/_touch — the three
        # copies of this predicate used to match the LITERAL
        # `source = ../claude/skills;`, and all three went red together when the
        # mapping's source became a derivation built from that path.
        assert_skills_mapping_deploys_repo_skills(home_nix)

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
            HANDOFF_DOC.read_text(encoding="utf-8"),
            sentence,
            why,
            "claude/skills/handoff/SKILL.md",
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
            "claude/skills/handoff/reference/index-write.md",
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
        renamed and the body would keep advertising it."""
        doc = HANDOFF_DOC.read_text(encoding="utf-8")
        assert "reference/index-write.md" in doc, (
            "claude/skills/handoff/SKILL.md no longer routes to its evidence "
            "sidecar — the rules would be left looking arbitrary with nowhere "
            "to check them."
        )
        assert HANDOFF_REFERENCE.exists(), f"the routed-to sidecar is gone: {HANDOFF_REFERENCE}"
        assert HANDOFF_REFERENCE.parent.name == "reference"
        assert HANDOFF_REFERENCE.parent.parent == HANDOFF_DOC.parent

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
        home_nix = (ROOT / "nix" / "home.nix").read_text(encoding="utf-8")
        assert 'home.file.".claude/skills"' in home_nix
        assert "source = ../claude/skills;" in home_nix
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
        absent from BOTH files, or moving the rationale would have reopened it."""
        for path in (HANDOFF_DOC, HANDOFF_REFERENCE):
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

    def test_kills_the_what_it_is_exclusion(self, tmp_path: Path) -> None:
        """🔴 Without it the recall block becomes a dump of the store — the one
        thing the brief forbids, and invisible to any test that only checks the
        two wanted sections ARE present."""
        mod = _load_mutant(
            tmp_path,
            "m_sections",
            [
                (
                    'SURFACED_HEADINGS: tuple[str, ...] = (POINTERS_HEADING, NUANCE_HEADING)',
                    'SURFACED_HEADINGS: tuple[str, ...] = (POINTERS_HEADING, NUANCE_HEADING, "## What it is")',
                )
            ],
        )
        store = _make_store(tmp_path / "s")
        assert WHAT_IT_IS in mod.render_text(mod.recall(store, SCOPE))

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
        listed = [e.ref for e in mod.recall(store, SCOPE).listing]
        assert listed != sorted(listed), "the digest INDEX escaped the one ordering site"

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
            [("    if report.omitted and report.listing:", "    if False:")],
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
        """Two guards, killed separately: a shared kill would pass while either
        one alone still fired."""
        store = _make_store(tmp_path / "s")
        for name, anchor in (
            ("m_list_ref", "    if args.listing and args.ref is not None:"),
            ("m_list_limit", "    if args.listing and args.limit is not None:"),
        ):
            mod = _load_mutant(tmp_path, name, [(anchor, "    if False:")])
            assert (
                mod.main(
                    ["--store", str(store), "--scope", SCOPE, "--list", "--ref", "collector"]
                    if name == "m_list_ref"
                    else ["--store", str(store), "--scope", SCOPE, "--list", "--limit", "2"]
                )
                == 0
            ), f"{name}: the OTHER guard fired, so this kill proves nothing"

    def test_kills_the_caveat(self, tmp_path: Path) -> None:
        """🔴 Without it index recall is presented as a plain finding, which is
        the one thing `/analyze-service` says never to do."""
        mod = _load_mutant(
            tmp_path,
            "m_caveat",
            [('            f"{RECALL_LABEL} — RECALL, NOT LIVE OBSERVATION. These are notes curated by "',
              '            f"(caveat neutered) "')],
        )
        store = _make_store(tmp_path / "s")
        text = mod.render_text(mod.recall(store, SCOPE))
        assert "RECALL, NOT LIVE OBSERVATION" not in text
