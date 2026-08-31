"""ONE append protocol for the `/analyze-service` index store, across BOTH doors.

WHAT IS BEING PROTECTED
-----------------------
Two skills write `~/.claude/analyze-service-index/`:

* `/handoff`, through `claude/skills/subsystem-index/SKILL.md`
* `/analyze-service`, through `claude/skills/analyze-service/reference/write-back.md`

Until 2026-08-31 each of those documents carried a FULL protocol and the two
MATERIALLY CONFLICTED. `write-back.md` gated the append behind
`append this to the index? (y/N)` and said "plain Write"; `subsystem-index`
declared that prompt retired and mandated `Edit` anchored on
`## Nuance / work-history`. Both read as *the* protocol and neither named a
winner. They disagreed about the one operation that can destroy another
session's bullet: a whole-file retype is MEASURED to lose a concurrent append
silently (`subsystem-index/reference/index-write.md`, the two-session
simulation).

Operator decision, 2026-08-31: the y/N is retired everywhere,
`subsystem-index/SKILL.md` is the single protocol, and `write-back.md` keeps only
what is `/analyze-service`-specific — what counts as notable, the auto-discovered
pointers, the bloat discipline, and the two CALLER facts (its own
`created_by: analyze-service` stamp, and where the file goes).

🔴 WHY A TEST AND NOT A COMMENT. The fork did not arrive as a mistake — it arrived
because two documents each described one write, and nothing ever compared them.
Prose asserting "these agree" is the same failure one layer up. This module is
the comparison.

WHAT THIS MODULE DOES **NOT** COVER, stated so nobody reads coverage into it
---------------------------------------------------------------------------
* It is scoped to the **APPEND** doors above. `prune-index` legitimately keeps a
  y/N and legitimately uses `Write`: a prune is a DELETION that rewrites a whole
  file, the evidence that retired the append prompt ("the answer was always
  `y`") was measured on an APPEND, and a cut removes bytes that are often their
  content's only copy. Blast radius earns that gate. So the banned-imperative
  ledger below is asserted against the two append doors ONLY, never repo-wide.
* It reads the two documents. It cannot make an agent follow them.
* Layer 1 (substring pins) catches DELETION of a named instruction and DRIFT in
  its wording. It cannot see a hostile rewrite that keeps the substrings — which
  is what layer 2 (the whole-region hash) is for, and why the region is pinned
  whole rather than by keyword.

🔴 NO TEST HERE READS THE REAL STORE. Nothing in this file opens
`~/.claude/analyze-service-index/`; it reads tracked documents in this repo only.
"""

from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SKILLS = ROOT / "claude" / "skills"

# The two doors. The VALUES are what each is allowed to be, and the pair is a
# ledger: a test below fails if the set grows or shrinks.
OWNER = SKILLS / "subsystem-index" / "SKILL.md"
POINTER = SKILLS / "analyze-service" / "reference" / "write-back.md"

# The third file an agent reaches the pointer through. It must ROUTE, never
# restate — a summary is a copy, and copies are what this module exists to stop.
ANALYZE_SKILL = SKILLS / "analyze-service" / "SKILL.md"

sys.path.insert(0, str(ROOT / "scripts" / "lib"))

import subsystem_touch as st  # noqa: E402


# The mechanism sentence: the fragment that MANDATES how an append is performed.
# Chosen as the fragment rather than a whole sentence because the same mandate is
# quoted (once) inside the pointer's pinned block, and the ledger below has to be
# able to see both occurrences to say anything about the relationship.
MANDATE = "anchored on `## Nuance / work-history`"

# 🔴 IMPERATIVES, not the string `(y/N)`. Both doors legitimately QUOTE the
# retired prompt while describing its retirement, so banning the spelling would
# make the resolution record itself unwritable — and an unwritable record is how
# the next session re-derives the fork from scratch. These are the phrasings that
# INSTRUCT an executor to ask, taken from what the two documents actually said
# before 2026-08-31 plus the near neighbours a re-add would reach for.
BANNED_IMPERATIVES: tuple[str, ...] = (
    "and ask a single yes/no",
    "ask one yes/no",
    "Write only on explicit confirm",
    "On decline, discard",
    "Ask exactly",
    "a single y/N",
    "behind an explicit y/N",
    "blocks on a y/N",
    "then plain Write",
    "then plain `Write`",
)


# --- the region extractor ------------------------------------------------------


def _region(doc: str, name: str) -> str:
    """Bytes between `<!-- <name>:begin -->` and `<!-- <name>:end -->`.

    Raises rather than returning "" on a miss: an empty region hashes to a
    constant and the guard would then pass forever against nothing — the
    silent-zero shape. Matched on the marker PREFIX so the explanatory prose
    inside each marker stays editable without changing which bytes are hashed.
    Same contract as `test_subsystem_resolver.py`'s extractor, deliberately.
    """
    begin, end = f"<!-- {name}:begin", f"<!-- {name}:end"
    i = doc.find(begin)
    assert i != -1, f"marker {begin!r} is missing from {POINTER.name}"
    i = doc.index("-->", i) + len("-->")
    j = doc.find(end, i)
    assert j != -1, f"marker {end!r} is missing from {POINTER.name}"
    body = doc[i:j].strip()
    assert body, f"region {name!r} is EMPTY — the hash would guard nothing"
    return body


def _normalise(text: str) -> str:
    """Collapse every whitespace run to one space.

    Deliberate: a markdown REFLOW is not a change of claim, and a guard that
    false-fails on rewrapping is a guard people learn to update without reading.
    A changed WORD still moves the hash, which is the property being bought.
    """
    return " ".join(text.split())


@pytest.fixture(scope="module")
def owner() -> str:
    return OWNER.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def pointer() -> str:
    return POINTER.read_text(encoding="utf-8")


# =============================================================================
# Layer 0 — the documents exist, ship, and are the ones being pinned.
# =============================================================================


class TestTheDoorsAreTheDeployedOnes:
    """A pin against a file the flake does not ship is a vacuous green."""

    @pytest.mark.parametrize("doc", [OWNER, POINTER, ANALYZE_SKILL], ids=lambda p: p.name)
    def test_the_door_exists(self, doc: Path) -> None:
        assert doc.is_file(), f"pinned door is gone: {doc}"

    @pytest.mark.parametrize("doc", [OWNER, POINTER, ANALYZE_SKILL], ids=lambda p: p.name)
    def test_the_door_is_TRACKED_by_git(self, doc: Path) -> None:
        """🔴 The flake source contains only tracked files, so an untracked door
        deploys as an ABSENCE while every pin here passes against the working
        copy. `claude/RULES.md`: a new file must be `git add`ed.

        🔴 It RETURNS rather than `pytest.skip`s when there is no `.git`. The nix
        check tier builds from a `cp -r ${./.}` store copy with no repository, so
        a skip would fire there on every run — and `run-tests.sh` pins its
        EXPECTED_SKIPS set exactly, so an unpinned skip breaks the gate. Same
        idiom as `test_subsystem_touch.py`'s tracked-doc check, deliberately: the
        dev-host tier is the one that can answer this, and it does.
        """
        if not (ROOT / ".git").exists():
            return
        rel = doc.relative_to(ROOT).as_posix()
        out = subprocess.run(
            ["git", "-C", str(ROOT), "ls-files", "--error-unmatch", "--", rel],
            capture_output=True,
            text=True,
        )
        assert out.returncode == 0, (
            f"{rel} is not tracked by git, so the flake omits it from the deploy "
            f"and every pin against it is vacuous.\n{out.stderr}"
        )


# =============================================================================
# Layer 1 — the pointer POINTS, and mandates nothing.
# =============================================================================


class TestThePointerDefersToTheOwner:
    def test_the_pointer_names_the_owning_document_BY_PATH(self, pointer: str) -> None:
        """A pointer that names only a skill stops resolving the moment that
        skill's listing entry is evicted — the same argument `/handoff` step 4
        already makes about this exact file. So the PATH has to be there."""
        assert "claude/skills/subsystem-index/SKILL.md" in pointer, (
            f"{POINTER.name} no longer names the owning protocol document by "
            f"path. It carries no protocol of its own, so a reader who cannot "
            f"reach `subsystem-index/SKILL.md` has no protocol at all."
        )

    def test_the_pointer_sends_the_reader_there_at_the_DECISION_POINT(
        self, pointer: str
    ) -> None:
        """🔴 Naming the file somewhere is not routing to it. The redirect has to
        sit at step 4 — the step that fires when a run HAS something to record —
        or the reader meets the notable/not-notable question, decides "yes", and
        finds no next instruction."""
        step4 = pointer.find("\n4. ")
        assert step4 != -1, f"{POINTER.name} lost its numbered step 4"
        step_body = pointer[step4 : pointer.find("\n\n", step4)]
        assert "subsystem-index/SKILL.md" in step_body, (
            f"step 4 of {POINTER.name} no longer routes to the owning protocol. "
            f"It reads:\n{step_body}"
        )

    @pytest.mark.parametrize("banned", BANNED_IMPERATIVES)
    def test_the_pointer_INSTRUCTS_no_mechanism_of_its_own(
        self, pointer: str, banned: str
    ) -> None:
        assert banned not in pointer, (
            f"{POINTER.name} instructs a write mechanism again: {banned!r}.\n"
            f"That is the fork this module exists to prevent — until 2026-08-31 "
            f"this file carried a SECOND protocol that disagreed with "
            f"{OWNER.relative_to(ROOT)} about the one operation that can destroy "
            f"another session's bullet.\n"
            f"There is ONE append protocol and it lives in the owner. If it is "
            f"genuinely changing, change it THERE and leave this file pointing."
        )

    @pytest.mark.parametrize("banned", BANNED_IMPERATIVES)
    def test_the_OWNER_does_not_reinstate_the_prompt_either(
        self, owner: str, banned: str
    ) -> None:
        """The retirement is symmetric. Pinning it on one door only would let the
        fork re-open from the other side, which is the direction it opened from
        the first time (`write-back.md` kept a prompt the owner had retired)."""
        assert banned not in owner, (
            f"{OWNER.name} instructs the executor to prompt again: {banned!r}. "
            f"The y/N was retired at the index write 2026-08-15, at /handoff "
            f"step 5 on 2026-08-23, and at the last door on 2026-08-31, each "
            f"time by operator decision on the same evidence: the answer was "
            f"always `y`. Declining on CONTENT is unaffected and stays normal."
        )

    def test_the_analyze_service_body_ROUTES_rather_than_SUMMARISES(self) -> None:
        """🔴 The third copy. `analyze-service/SKILL.md` describes `write-back.md`
        to the reader, and a one-line summary of the mechanism there would be a
        fourth statement of it — free to drift, and read by more sessions than
        either door. It must route and stop."""
        body = ANALYZE_SKILL.read_text(encoding="utf-8")
        assert "reference/write-back.md" in body, (
            "the skill body no longer points at write-back.md; the prose moved "
            "there is unreachable."
        )
        assert "subsystem-index/SKILL.md" in body, (
            "the skill body names write-back.md but not the document that "
            "actually carries the protocol, so a reader stops one file short."
        )
        assert MANDATE not in body, (
            f"{ANALYZE_SKILL.name} restates the append mechanism "
            f"({MANDATE!r}) instead of routing to it. That is a THIRD copy of a "
            f"predicate that already had two and drifted. Point at "
            f"{OWNER.relative_to(ROOT)}; do not summarise it."
        )


# =============================================================================
# Layer 2 — the LEDGER: which files may state the mechanism at all.
# =============================================================================


class TestTheMechanismLedger:
    """🔴 A RELATIONSHIP, not a component. Every test above inspects ONE document;
    the fork was a property of the PAIR, and `claude/RULES.md` is explicit that a
    seam guard must pin an asserted ledger that fails when the set GROWS *or*
    SHRINKS. This is that ledger: the exhaustive set of documents under
    `claude/skills/` that state the append mechanism at all.
    """

    # Path (repo-relative) -> why it is allowed to carry the mandate.
    ALLOWED: dict[str, str] = {
        "claude/skills/subsystem-index/SKILL.md": (
            "THE owner — the single append protocol, for every caller"
        ),
        "claude/skills/analyze-service/reference/write-back.md": (
            "quotes it ONCE, inside the hashed `one-append-protocol` region, to "
            "record what the fork was and how it closed"
        ),
    }

    @staticmethod
    def _carriers() -> set[str]:
        return {
            p.relative_to(ROOT).as_posix()
            for p in SKILLS.rglob("*.md")
            if MANDATE in p.read_text(encoding="utf-8", errors="replace")
        }

    def test_the_carrier_set_is_EXACTLY_the_ledger(self) -> None:
        found = self._carriers()
        expected = set(self.ALLOWED)
        grew = sorted(found - expected)
        shrank = sorted(expected - found)
        assert found == expected, (
            "the set of skill documents stating the append mechanism has "
            "changed.\n"
            f"  NEW carriers (a second protocol is growing): {grew or 'none'}\n"
            f"  LOST carriers (the protocol has no home):    {shrank or 'none'}\n"
            "Ledger, with the reason each entry is allowed:\n  "
            + "\n  ".join(f"{k} — {v}" for k, v in self.ALLOWED.items())
            + "\nA NEW carrier is the 2026-08-31 fork re-opening: two documents "
            "describing one write is how they come to disagree. Route to the "
            "owner instead. If a new door is genuinely correct, add it HERE with "
            "its reason, in the same commit, and say in the commit message what "
            "keeps it from drifting."
        )

    def test_the_pointers_only_occurrence_is_INSIDE_the_pinned_region(
        self, pointer: str
    ) -> None:
        """The ledger permits `write-back.md` to name the mechanism; this is what
        stops that permission from being a hole. Inside the region every word is
        hashed, so the quote cannot drift silently. Outside it, it could."""
        region = _region(pointer, "one-append-protocol")
        assert pointer.count(MANDATE) == region.count(MANDATE) > 0, (
            f"{POINTER.name} states the append mechanism OUTSIDE its pinned "
            f"`one-append-protocol` region ({pointer.count(MANDATE)} occurrences "
            f"in the file, {region.count(MANDATE)} inside the region). Only the "
            f"pinned quote is allowed: an unpinned one is free to drift away "
            f"from the owner, which is exactly how the two doors came to "
            f"disagree in the first place."
        )

    def test_the_ledger_probe_can_OBSERVE_a_new_carrier(self, tmp_path: Path) -> None:
        """🔴 POSITIVE CONTROL. A scan that returns a reassuring set is
        indistinguishable from one wired to nothing (`claude/RULES.md`: a zero is
        unproven until a positive control shows the pattern CAN match). Feed the
        predicate a document that MUST match and watch it match."""
        planted = tmp_path / "new-door.md"
        planted.write_text(
            f"use `Edit` {MANDATE}, not `Write`\n", encoding="utf-8"
        )
        assert MANDATE in planted.read_text(encoding="utf-8")
        # and the negative half of the same control
        clean = tmp_path / "routes-only.md"
        clean.write_text("see subsystem-index/SKILL.md for the protocol\n", encoding="utf-8")
        assert MANDATE not in clean.read_text(encoding="utf-8")


# =============================================================================
# Layer 3 — the OWNER still carries what the pointer defers to.
# =============================================================================


class TestTheOwnerCarriesTheProtocol:
    """The pointer is only correct if the thing it points at is still there. A
    green `TestThePointerDefersToTheOwner` with a gutted owner is a protocol that
    exists in no document at all — strictly worse than the fork."""

    OWNER_SENTENCES: list[tuple[str, str]] = [
        (
            "use `Edit` anchored on `## Nuance / work-history`, not `Write`",
            "🔴 the mandated mechanism: no whole-file retype of a curated entry",
        ),
        (
            "`Write` only for a first-ever file",
            "🔴 the carve-out — a first-ever file has no prior content to lose",
        ),
        (
            "re-read the file and re-apply to current bytes",
            "the actual safeguard: a concurrent append must not be clobbered",
        ),
        (
            "Still print the unified diff before writing",
            "what the retired prompt was never doing, kept",
        ),
        (
            "THIS IS THE APPEND PROTOCOL FOR EVERY WRITER OF THIS STORE",
            "🔴 the owner claims BOTH callers — the sentence that closed the fork",
        ),
        (
            "`/analyze-service`",
            "the owner names the other caller, so a reader knows it applies",
        ),
        (
            "`--writer` is REQUIRED and has no default",
            "🔴 `created_by` is the caller's, not the tool's",
        ),
    ]

    @pytest.mark.parametrize(
        "sentence,why", OWNER_SENTENCES, ids=[w[:48] for _, w in OWNER_SENTENCES]
    )
    def test_sentence_still_present(self, owner: str, sentence: str, why: str) -> None:
        assert sentence in owner, (
            f"{OWNER.relative_to(ROOT)} no longer contains the sentence pinning "
            f"{why}.\n  missing: {sentence!r}\n"
            f"  This document is the ONLY append protocol for the store — "
            f"`analyze-service/reference/write-back.md` was reduced to a pointer "
            f"at it on 2026-08-31. Deleting an instruction here deletes it "
            f"everywhere. Restore it, or move it and update this pin in the SAME "
            f"commit."
        )

    def test_the_owner_pin_can_report_absence(self, owner: str) -> None:
        """NEGATIVE CONTROL: a substring check against a document that happens to
        contain everything is indistinguishable from one pointed at the wrong
        file."""
        assert "a sentence deliberately absent from subsystem-index/SKILL.md" not in owner

    def test_the_owner_records_that_the_fork_WAS_open(self, owner: str) -> None:
        """🔴 The history is load-bearing, not decoration. A reader who half
        remembers "these two disagree" and finds no trace of it will go and
        re-derive the conflict — or, worse, restore write-back.md's copy to fix
        the "missing" protocol. The record has to say it was open AND that it is
        closed, with the date."""
        for fragment in ("2026-08-31", "MATERIALLY CONFLICTED", "write-back.md"):
            assert fragment in owner, (
                f"{OWNER.name} no longer records how the two-protocol fork was "
                f"resolved (missing {fragment!r}). Do not delete this history: a "
                f"stale memory of the conflict is common, and with no record of "
                f"the resolution the next reader re-opens it."
            )


# =============================================================================
# Layer 4 — the whole-region hash.
# =============================================================================


class TestThePointerRegionIsPinnedWHOLE:
    """🔴 WHY A HASH AND NOT MORE SUBSTRINGS. `claude/RULES.md`: when the artifact
    under test IS prose, a guard on WORDS is walkable by REWORDING — a rewrite
    that keeps every pinned substring and deletes everything else passes. This
    region is where a second protocol would be re-grown, so it is pinned WHOLE.

    A cosmetic reword therefore fails this test. That is the price, and it is
    paid deliberately for a machine-readable claim. Whitespace is normalised, so
    re-wrapping the markdown is free; changing a WORD is not.
    """

    REGION = "one-append-protocol"
    EXPECTED_SHA = "94cdbbcefea34dade95d16a328cdc2289427a2ae1e3234dd1d59f747f2ece5c4"

    def test_region_hash(self, pointer: str) -> None:
        body = _normalise(_region(pointer, self.REGION))
        actual = hashlib.sha256(body.encode("utf-8")).hexdigest()
        assert actual == self.EXPECTED_SHA, (
            f"\nThe `{self.REGION}` block of {POINTER.relative_to(ROOT)} CHANGED.\n"
            f"  expected sha256 {self.EXPECTED_SHA}\n"
            f"  actual   sha256 {actual}\n\n"
            f"That block is where the SECOND protocol used to live. It states, in\n"
            f"full, that this file carries no protocol, what the 2026-08-31\n"
            f"decision was, and the two caller facts the shared protocol leaves to\n"
            f"its caller. A substring pin cannot see a paragraph being ADDED here —\n"
            f"this hash is what does.\n\n"
            f"So: re-read the block against {OWNER.relative_to(ROOT)}, confirm they\n"
            f"still describe ONE protocol, then paste the actual sha above into\n"
            f"EXPECTED_SHA in the SAME commit. Updating the hash without reading\n"
            f"the other door is the one way to make this guard worthless."
        )

    def test_the_hash_MOVES_for_a_changed_region(self, pointer: str) -> None:
        """🔴 POSITIVE CONTROL. A hash comparison that always passes is
        indistinguishable from one wired to a constant, so prove it can move."""
        body = _normalise(_region(pointer, self.REGION))
        mutated = body.replace("NOT RESTATED", "restated", 1) + " and one more clause"
        assert mutated != body, "premise gone: the mutation did not change the body"
        assert (
            hashlib.sha256(mutated.encode("utf-8")).hexdigest() != self.EXPECTED_SHA
        ), "the hash did not move for a changed body"

    def test_normalisation_ignores_REWRAP_and_not_REWORD(self) -> None:
        """The exact width of the affordance, asserted rather than described."""
        assert _normalise("a b\nc") == _normalise("a\n  b   c")
        assert _normalise("plain Write") != _normalise("plain Edit")

    def test_a_missing_marker_fails_loudly(self) -> None:
        """NEGATIVE CONTROL on the extractor: it must raise, never silently hash
        the empty string."""
        with pytest.raises(AssertionError) as exc:
            _region("no markers here at all\n", self.REGION)
        assert "is missing from" in str(exc.value)

    def test_an_empty_region_fails_loudly(self) -> None:
        with pytest.raises(AssertionError) as exc:
            _region(
                f"<!-- {self.REGION}:begin -->\n\n<!-- {self.REGION}:end -->\n",
                self.REGION,
            )
        assert "the hash would guard nothing" in str(exc.value)


# =============================================================================
# `created_by` is the CALLER's — the code half of the same decision.
# =============================================================================


class TestCreatedByIsSuppliedByTheCaller:
    """🔴 `subsystem-index/SKILL.md`'s own text said the hardcoded
    `created_by: handoff` "is correct only while this file has one caller". As of
    2026-08-31 it has two, so the stamp is the caller's to supply.

    Every fixture below uses `analyze-service` rather than `handoff`: `handoff`
    WAS the hardcoded value, so a fixture spelling it cannot distinguish "the
    argument was written" from "the old default was" and would SURVIVE the exact
    regression this class exists to catch (`claude/RULES.md`: feed a value the
    constant CANNOT equal and watch the output move).
    """

    OTHER = "analyze-service"

    def test_the_template_REFUSES_without_a_writer(self) -> None:
        """A required keyword, not a defaulted one. `created_by:` is stamped once
        at creation and never edited, so a wrong value is permanent and invisible
        in every later reading — the module must not guess."""
        with pytest.raises(TypeError):
            st.new_entry_template("roster", "some-scope", today="2026-08-31")  # type: ignore[call-arg]

    def test_the_template_stamps_WHAT_IT_IS_GIVEN(self) -> None:
        text = st.new_entry_template(
            "roster", "some-scope", today="2026-08-31", created_by=self.OTHER
        )
        assert f"created_by: {self.OTHER}\n" in text
        assert "created_by: handoff" not in text, (
            "the template stamped `handoff` for an `analyze-service` caller — "
            "the hardcoded default is back, and it silently mis-attributes every "
            "entry the other caller creates."
        )

    def test_handoffs_own_id_still_round_trips(self) -> None:
        """The other half: parameterising must not have broken the caller that
        was previously hardcoded."""
        text = st.new_entry_template(
            "roster", "some-scope", today="2026-08-31", created_by=st.WRITER_ID
        )
        assert "created_by: handoff\n" in text
        assert st.WRITER_ID == "handoff", (
            "WRITER_ID is the id `/handoff` passes; the CLI's refusal message "
            "quotes it, and the census buckets by it."
        )

    def test_the_stamp_reaches_CENSUS_under_the_supplied_name(
        self, tmp_path: Path
    ) -> None:
        """🔴 THE SEAM. The template and the census are each testable alone and
        each green alone; what matters is that a value written by one is counted
        by the other under the SAME name. `--census` is the instrument the
        decision doc's open question is answered with, so a stamp that does not
        reach it answers nothing."""
        store = tmp_path / "store"
        (store / "some-scope").mkdir(parents=True)
        for slug, writer in (("alpha", self.OTHER), ("beta", st.WRITER_ID)):
            (store / "some-scope" / f"{slug}.md").write_text(
                st.new_entry_template(
                    slug, "some-scope", today="2026-08-31", created_by=writer
                ),
                encoding="utf-8",
            )
        c = st.census(store)
        assert c.total == 2
        assert c.by_writer == {self.OTHER: 1, st.WRITER_ID: 1}, (
            f"the census did not split the two writers: {c.by_writer!r}. Either "
            f"the template stopped honouring `created_by`, or the census stopped "
            f"reading it — and the store's coverage question is unanswerable "
            f"either way."
        )

    def test_a_writer_the_tuple_does_not_KNOW_is_still_counted_separately(
        self, tmp_path: Path
    ) -> None:
        """`new_entry_template` accepts any string rather than enumerating against
        `KNOWN_WRITERS`, and `census` buckets an unknown value under its own name.
        So a third caller is measurable the day it appears, not the day someone
        widens a tuple — and it is never folded into a known writer, which is the
        inference the whole field exists to remove."""
        store = tmp_path / "store"
        (store / "some-scope").mkdir(parents=True)
        (store / "some-scope" / "gamma.md").write_text(
            st.new_entry_template(
                "gamma", "some-scope", today="2026-08-31", created_by="some-third-skill"
            ),
            encoding="utf-8",
        )
        c = st.census(store)
        assert c.by_writer == {"some-third-skill": 1}
        assert st.UNSTAMPED not in c.by_writer, (
            "an unknown writer was folded into the pre-instrumentation bucket, "
            "which would read as `this entry predates the stamp` — a fact about "
            "a different thing entirely."
        )

    def test_the_known_writers_ledger_matches_the_CLI_choices(self) -> None:
        """Two sites, one predicate. The tuple documents the writers; argparse
        enforces them. A tuple that grows without the CLI following would accept
        a writer the CLI refuses, and vice versa."""
        parser = st._build_parser()
        writer_action = next(
            a for a in parser._actions if "--writer" in (a.option_strings or [])
        )
        assert set(writer_action.choices or ()) == set(st.KNOWN_WRITERS)
        assert writer_action.default is None, (
            "--writer grew a default. That is the hardcoded stamp again, one "
            "layer out: whichever caller does not pass the flag is silently "
            "filed under the other."
        )

    def test_the_placeholder_is_DERIVED_from_the_ledger(self) -> None:
        """The command hints the tool prints are what an agent copy-pastes. A
        hand-typed placeholder is the copy that goes stale first."""
        for writer in st.KNOWN_WRITERS:
            assert writer in st.WRITER_PLACEHOLDER

    def test_every_printed_template_hint_carries_the_flag(self) -> None:
        """🔴 A refusal an agent cannot satisfy from what it was shown is a dead
        end. The tool prints the `--template` command in two places; both must
        name `--writer`, or the copy-pasted command exits 2."""
        hint = "\n".join(st.render_no_path_footprint("some-scope"))
        assert "--template" in hint and "--writer" in hint, hint

    def test_the_cli_refuses_a_template_with_no_writer(self, capsys) -> None:
        rc = st.main(
            ["--scope", "some-scope", "--store", "/nonexistent-store", "--template", "widget"]
        )
        err = capsys.readouterr().err
        assert rc == 2, f"expected exit 2, got {rc}; stderr was:\n{err}"
        assert "--template needs --writer" in err
        # the refusal must NAME the choices — a refusal that does not say what to
        # pass is a round trip, which is what retiring the prompt was for.
        for writer in st.KNOWN_WRITERS:
            assert writer in err, f"the refusal does not name {writer!r}:\n{err}"

    def test_the_cli_stamps_the_writer_it_was_given(self, capsys) -> None:
        rc = st.main(
            [
                "--scope", "some-scope",
                "--store", "/nonexistent-store",
                "--template", "widget",
                "--today", "2026-08-31",
                "--writer", self.OTHER,
            ]
        )
        out = capsys.readouterr().out
        assert rc == 0
        assert f"created_by: {self.OTHER}" in out
        assert "created_by: handoff" not in out
