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
* 🔴 Layers 1-4 all key on what an AGREEING copy contains — the exact `MANDATE`
  fragment, or ten exact phrasings of the retired text. A fork is by definition a
  document that says something DIFFERENT, so those layers detect AGREEMENT and
  are blind to DISAGREEMENT. That gap was MEASURED (clawgate cg#473): a complete,
  materially conflicting second protocol appended to `write-back.md` left the
  module at 61 passed. Layer 5 is the disagreement half, and it is keyed on the
  MECHANISM TOKENS a second protocol cannot avoid naming rather than on any
  particular wording.
* `scripts/subsystem-store-api/server.py` carries the pod's own write protocol
  (flock + temp-file-rename, `PUT` behind `If-Match`). This module scans
  `claude/skills/**/*.md` only and would never see it. Recorded so nobody reads
  coverage of it into a green run here.

🔴 THE 2026-09-01 CUTOVER, and what it moved in here
----------------------------------------------------
`claudedocs/plan-cairn-phase1-cutover.md` made the POD canonical and froze every
local entry file to `0444`. The owner's write step is therefore no longer an
`Edit` against `~/.claude/analyze-service-index/` — it is `cairn append` (one
dated bullet, the server supplies the `- ` and the date) and `cairn put` (a
whole-file replace behind `If-Match`). `prune-index` moved with it: its cut is a
`cairn put`, and it KEEPS its y/N, which the plan's §5 table calls out as the
writer most likely to be forgotten.

What that means for THIS module, stated so a reader does not misread its shape:

* `MANDATE` still holds the OLD `Edit`-anchor fragment. It is now the marker of a
  document that talks about the RETIRED local mechanism, and the ledger over it
  says which two documents may record that retirement — the same two, for the
  same reason. Nothing may re-instruct it.
* the disagreement predicate (`MECHANISM`, layer 5) was widened to name the
  `cairn` verbs. Without that, a second protocol written in terms of the NEW
  mechanism would have been invisible to every layer here — the module would have
  gone on guarding the mechanism nobody uses. That is the "a guard's DESCRIPTION
  claims COVERAGE" shape arriving through a cutover rather than an edit.

🔴 NO TEST HERE READS THE REAL STORE. Nothing in this file opens
`~/.claude/analyze-service-index/`; it reads tracked documents in this repo only.
"""

from __future__ import annotations

import hashlib
import re
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


# The RETIRED mechanism fragment. Until the 2026-09-01 cutover this was the
# sentence that MANDATED how an append is performed; it is now the fragment that
# marks a document as talking about the retired LOCAL mechanism at all. Both
# doors still contain it — the owner because it names what `cairn append` and
# `--if-match` replaced, the pointer because its hashed region records what the
# 2026-08-31 fork was — and the ledger below is what says those are the only two.
#
# 🔴 IT IS NOT RENAMED TO `RETIRED_*`, deliberately: every ledger, confinement
# check and control below keys on it, and the RELATIONSHIP each asserts is
# unchanged by the cutover. What changed is why a document may carry it, and that
# is recorded in the ledgers' own reason strings rather than in a constant name.
MANDATE = "anchored on `## Nuance / work-history`"

# The verbs that REPLACED it. Named here so the disagreement predicate below can
# see a second protocol written in the new mechanism — the old `MECHANISM` regex
# knew only `Write`/`Edit`/`y/N`, so a fork phrased as `cairn put` would have
# passed every layer in this module.
WRITE_VERBS = ("cairn append", "cairn put")

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


def _region_span(doc: str, name: str) -> tuple[int, int]:
    """Half-open `[start, end)` CHARACTER offsets of the region's body.

    Split out from `_region()` so the confinement guard in layer 5 can ask
    "is this occurrence inside the region?" against the same extraction the hash
    uses. One rule, one place: two extractors would drift, and the whole point of
    this module is that a duplicated predicate disagrees with itself eventually.
    """
    begin, end = f"<!-- {name}:begin", f"<!-- {name}:end"
    i = doc.find(begin)
    assert i != -1, f"marker {begin!r} is missing from {POINTER.name}"
    i = doc.index("-->", i) + len("-->")
    j = doc.find(end, i)
    assert j != -1, f"marker {end!r} is missing from {POINTER.name}"
    return i, j


def _region(doc: str, name: str) -> str:
    """Bytes between `<!-- <name>:begin -->` and `<!-- <name>:end -->`.

    Raises rather than returning "" on a miss: an empty region hashes to a
    constant and the guard would then pass forever against nothing — the
    silent-zero shape. Matched on the marker PREFIX so the explanatory prose
    inside each marker stays editable without changing which bytes are hashed.
    Same contract as `test_subsystem_resolver.py`'s extractor, deliberately.
    """
    i, j = _region_span(doc, name)
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
# Layer 0b — the mandated mechanism is the PRE-APPROVED one at every door.
# =============================================================================

# Skills whose SKILL.md routes an executor to the owner's write half. Each MUST
# pre-approve `Edit`, because that is what the owner mandates.
WRITING_DOORS = ("analyze-service", "subsystem-index", "handoff", "prune-index")
# The read-only door, carried as the NEGATIVE CONTROL below. Without it, a guard
# asserting "these skills declare Edit" would also pass on a tree where every
# skill declared every tool — i.e. it would be testing nothing.
READ_ONLY_DOOR = "resume"

_ALLOWED = re.compile(r"^allowed-tools:\s*(.+)$", re.M)


def _allowed_tools(skill: str) -> set[str]:
    m = _ALLOWED.search((SKILLS / skill / "SKILL.md").read_text(encoding="utf-8"))
    assert m, f"{skill}/SKILL.md has no allowed-tools line"
    return {t.strip() for t in m.group(1).split(",") if t.strip()}


class TestTheMandatedToolIsThePreApprovedOne:
    """🔴 A PROTOCOL AND A FRONTMATTER CAN CONSOLIDATE APART.

    `allowed-tools` is a PRE-APPROVAL, not a restriction — every tool stays
    callable — so this is about incentive, not reachability. If a door
    pre-approves the mechanism the protocol FORBIDS and not the one it mandates,
    then at that door the wrong path runs unprompted while the right one stops
    for approval — and in a headless or subagent run there is nobody to answer.

    Found by audit on the PR that created this module: `analyze-service` was
    routed to the `Edit` mandate while its own frontmatter listed
    `Bash, Read, Write, Grep, Agent`. The pairing had been coherent BEFORE the
    consolidation (that door said "plain `Write`", and `Write` was pre-approved);
    the protocol moved and the frontmatter did not. Nothing in this repo read
    `allowed-tools` at all, so no gate could see it.

    🔴 THE MANDATED TOOLS CHANGED AT THE 2026-09-01 CUTOVER, so this assertion
    did too. The write is now `cairn append` / `cairn put` — a CLI, so `Bash` —
    plus `Write` for the two local files that remain: a first-ever entry (the API
    has no create route) and the scratch file `cairn put --file` reads. `Edit` is
    NO LONGER REQUIRED here: entry files are `0444` and an `Edit` against one
    fails with `EACCES`. Asserting it anyway would have been a pin that reads as
    coverage of a rule nothing follows, which is worse than none.
    """

    # What the owner mandates, post-cutover. `Edit` is deliberately absent — see
    # the class docstring; it is not an oversight and not an eviction.
    MANDATED_TOOLS = ("Bash", "Write")

    @pytest.mark.parametrize("skill", WRITING_DOORS)
    @pytest.mark.parametrize("tool", MANDATED_TOOLS)
    def test_a_writing_door_pre_approves_the_mandated_tool(
        self, skill: str, tool: str
    ) -> None:
        tools = _allowed_tools(skill)
        assert tool in tools, (
            f"{skill}/SKILL.md pre-approves {sorted(tools)} — no `{tool}`. The "
            f"owner mandates {' and '.join(WRITE_VERBS)} (a CLI, so `Bash`) plus "
            f"`Write` for the first-ever entry file and the `--file` scratch "
            f"copy. A door missing one of those stops for approval on the "
            f"mandated path while the forbidden local edit runs unprompted."
        )

    def test_the_read_only_door_pre_approves_NEITHER(self) -> None:
        """NEGATIVE CONTROL. Proves the guard above discriminates instead of
        passing on any tree — a skill that only READS the store must not
        pre-approve either write tool, and `resume` is that skill.

        ⚠ ITS WIDTH, stated exactly, because the mandated set changed: `resume`
        DOES pre-approve `Bash` (it runs read-only scripts), so this control
        discriminates the `Write` half of `MANDATED_TOOLS` and not the `Bash`
        half. A tree where every skill declared every tool still fails here."""
        tools = _allowed_tools(READ_ONLY_DOOR)
        assert not ({"Edit", "Write"} & tools), (
            f"{READ_ONLY_DOOR}/SKILL.md pre-approves {sorted(tools)} — it reads "
            f"the store and must pre-approve no write tool."
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
        # Both the retired fragment AND the verbs that replaced it: a summary of
        # the CURRENT mechanism is the same third copy as a summary of the old
        # one, and checking only the retired spelling would have gone quietly
        # inert at the cutover.
        for token in (MANDATE, *WRITE_VERBS):
            assert token not in body, (
                f"{ANALYZE_SKILL.name} restates the append mechanism "
                f"({token!r}) instead of routing to it. That is a THIRD copy of a "
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

    # Path (repo-relative) -> why it is allowed to carry the RETIRED fragment.
    ALLOWED: dict[str, str] = {
        "claude/skills/subsystem-index/SKILL.md": (
            "THE owner — quotes the retired anchor rule ONCE, to say what the "
            "API's per-entry flock and `--if-match` replaced. A rule dropped "
            "without naming it reads as forgotten and gets re-derived"
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
        # 🔴 SUPERSEDES `("use `Edit` anchored on `## Nuance / work-history`, not
        # `Write`", "the mandated mechanism…")`. The 2026-09-01 cutover froze
        # every local entry file to 0444, so that instruction now describes a
        # write that returns EACCES. The pin is REPLACED, not dropped: the
        # mechanism it named still has to be pinned, at its new spelling.
        (
            "cairn append --scope <scope> --ref <entry> --session <session-uuid>",
            "🔴 the mandated mechanism: the append lands on the POD, which is "
            "the authority — a local write is invisible to it",
        ),
        (
            "The server adds the `- ` and the date — do not type either",
            "🔴 the one caller error the route has already produced in "
            "production, stated where the command is",
        ),
        (
            "goes through `cairn put`",
            "🔴 the OTHER write: an `OPEN:`->`RESOLVED` rewrite is not an append",
        ),
        # 🔴 The two writes used to be ONE `Edit`, so ordering could not arise.
        # It can now, and the wrong order fails against the caller's OWN earlier
        # write — a 412 that reads like someone else's concurrent edit. Nothing
        # else in the toolchain states this; the layer-5 predicate cannot see the
        # paragraph either, because it names no STORE noun.
        (
            "the rewrite is a `cairn put` and goes FIRST, then the new bullet's "
            "`cairn append`",
            "🔴 the ORDER of the two writes — reversed, the caller's own append "
            "moves the entry out from under its own `If-Match`",
        ),
        (
            "`Write` only for a first-ever file",
            "🔴 the carve-out — a first-ever file has no prior content to lose, "
            "and the API has no create route, so it stays a LOCAL write",
        ),
        # 🔴 SUPERSEDES `("re-read the file and re-apply to current bytes",
        # "the actual safeguard…")`. The re-read was a guess at concurrency with
        # no arbiter; `--if-match` derived from a live sync is the arbiter, and
        # exit 8 is what the re-read was trying to notice. Same claim, moved to
        # the mechanism that now carries it — and the owner must still say the
        # old rule was REPLACED rather than forgotten, which the next pin holds.
        (
            "exit 8 IS the other writer, not a transient error",
            "🔴 the safeguard: a concurrent write is REFUSED, never clobbered",
        ),
        (
            "THAT IS WHAT REPLACES the two rules this step used to carry — it is "
            "a replacement, not an omission",
            "🔴 the dropped rules are named where they were dropped, so the next "
            "reader does not restore them as 'missing'",
        ),
        # 🔴 THE WHOLE CLAUSE, not the bare `entry files are \`0444\`` fragment.
        # MEASURED while adding this pin: the owner states the freeze TWICE (once
        # naming the path, once in the write half), so the short fragment
        # SURVIVED a mutation that deleted the write half's sentence outright —
        # the other occurrence satisfied it. `claude/RULES.md`: a guard on a word
        # another sentence can spell is walkable.
        (
            "entry files are `0444` and an `Edit`/`Write` against one fails with "
            "`EACCES`",
            "🔴 WHY the local write is gone: an editor write now fails EACCES, "
            "and that refusal is the design rather than a broken store",
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
# Layer 5 — DISAGREEMENT: a second protocol that agrees with nothing.
# =============================================================================
#
# 🔴 WHY THIS LAYER EXISTS, and what it is keyed on instead.
#
# Layers 1-4 ask "does this document still contain the words an agreeing copy
# would contain?". A fork does not contain them — that is what makes it a fork —
# so a REWORDED second protocol walks straight past all four. Measured, cg#473:
# the block below, appended verbatim to `write-back.md` in a `cp -a` copy of
# 50bfd91f, left this module at 61 passed / 0 failed.
#
#     ## Performing the write (analyze-service)
#     Show the proposed bullet as a unified diff … then ask the user
#     `append this to the index? (y/N)`. Proceed only on a yes. Then re-read the
#     entry file and use the `Write` tool to emit the whole file …
#
# The one thing that block CANNOT do is describe a write mechanism without naming
# a write mechanism. So layer 5 keys on two token classes and their CO-OCCURRENCE
# in one paragraph — a MECHANISM (which tool, which store write verb, or a
# confirm gate) and a STORE
# CONTEXT (this store, its anchor heading, its owner, its entry files) — and then
# asks three questions about WHICH documents may carry such a paragraph, WHERE in
# the pointer they may sit, and WHETHER the owner's have changed.
#
# 🔴 It makes NO attempt to tell an INSTRUCTION from a DESCRIPTION, and nothing
# below assumes it can. Both doors quote the retired prompt verbatim while
# recording its retirement, and a guard that banned the spelling would make the
# resolution record unwritable — which is how the next session re-derives the
# fork from scratch. So instead of judging the prose: every document allowed to
# carry such a paragraph is ENUMERATED with its reason, the pointer's occurrences
# are CONFINED to the already-hashed region, and the owner's are PINNED WHOLE.

# Front matter is a DECLARATION, not prose: `allowed-tools: …, Write, Edit, …`
# is already covered by `TestTheMandatedToolIsThePreApprovedOne`, and leaving it
# in the scan would file every skill that pre-approves `Write` as a protocol
# carrier. Stripped before any layer-5 predicate runs.
_FRONT_MATTER = re.compile(r"\A---\n.*?\n---\n", re.S)

# A write MECHANISM: the tool an executor is told to reach for, or the confirm
# gate it is told to open. Backticked tool names are this repo's spelling; the
# `use/then/plain/via <tool>` alternation catches the unbackticked form without
# matching the ordinary English verb ("Write a durable lesson", "Editing it
# means…"), which a bare `\bWrite\b` does match and which appears in the owner.
#
# 🔴 THE `cairn` ALTERNATIVE IS NOT DECORATION. Until the 2026-09-01 cutover the
# only write mechanisms were the two tools and the confirm gate, so those three
# classes were exhaustive. They stopped being exhaustive the moment the protocol
# became a CLI: a second protocol phrased entirely as `cairn put …` names no tool
# and opens no gate, and would have been invisible to every layer in this module
# while the ledgers went on reporting a clean set. A predicate is only as wide as
# the mechanisms that exist, and that set is not closed.
MECHANISM = re.compile(
    r"`Write`|`Edit`"
    r"|\b(?:Write|Edit) tool\b"
    r"|\b(?:use|using|uses|with|then|via|plain)\s+(?:a\s+|the\s+)?\*{0,2}`?(?:Write|Edit)`?\*{0,2}\b"
    r"|\(y/N\)|\by/N\b|\byes/no\b|\byes-no\b"
    r"|\bcairn\s+(?:append|put)\b"
)

# …applied to THIS store. Without this half the predicate would fire on every
# skill in the repo that mentions a tool. These are the store's own nouns: its
# directory, the heading the mandated `Edit` anchors on, the owning document, and
# the words for the things it holds.
STORE_CONTEXT = re.compile(
    r"analyze-service-index"
    r"|Nuance / work-history"
    r"|nuance heading"
    r"|subsystem-index/SKILL\.md"
    r"|index entry|entry file|index store|subsystem index"
)


def _paragraphs(text: str) -> list[str]:
    return [p for p in re.split(r"\n\s*\n", text) if p.strip()]


def _mechanism_outside_region(doc: str, region: str) -> list[tuple[int, str]]:
    """`(line, token)` for every MECHANISM occurrence OUTSIDE the named region.

    🔴 ONE expression, used by the guard below AND by its positive control.
    MEASURED (cg#473 mutation sweep, mutant M6): while the control carried its
    own copy of this filter, replacing the GUARD's copy with `if False` left all
    71 tests green — the control was proving a DUPLICATE reachable while the real
    guard was dead. `claude/RULES.md`: one rule, one place.
    """
    lo, hi = _region_span(doc, region)
    return [
        (doc[: m.start()].count("\n") + 1, m.group(0))
        for m in MECHANISM.finditer(doc)
        if not (lo <= m.start() and m.end() <= hi)
    ]


def _store_write_paragraphs(text: str) -> list[str]:
    """Paragraphs that name a write MECHANISM *and* THIS store in the same block.

    Paragraph-scoped rather than file-scoped on purpose: a file-scoped
    co-occurrence would fire on any long document that happens to mention both
    somewhere, and a line-scoped one would miss a mechanism sentence wrapped
    across two lines. It reports paragraphs; it does NOT classify them as
    instruction or description, and nothing below assumes it can.
    """
    body = _FRONT_MATTER.sub("", text)
    return [
        p for p in _paragraphs(body) if MECHANISM.search(p) and STORE_CONTEXT.search(p)
    ]


class TestTheStoreWriteCarrierLedgerIsEXACT:
    """🔴 A NEW DOOR IS CAUGHT WHETHER OR NOT IT AGREES WITH THE OWNER.

    `TestTheMechanismLedger` above enumerates the documents containing the exact
    `MANDATE` fragment — so it sees a new door that COPIES the owner and is blind
    to one that CONTRADICTS it. This is the same ledger shape over the
    disagreement predicate: the exhaustive set of `claude/skills/**/*.md` that
    state a write mechanism for this store AT ALL, in any wording.

    Fails when the set GROWS (a third door, however phrased) and when it SHRINKS
    (a door stopped describing the mechanism — for the two that must, that is a
    deletion, and layers 1/3 say which sentence).
    """

    # Path (repo-relative) -> why this document is allowed to state a mechanism.
    ALLOWED: dict[str, str] = {
        "claude/skills/subsystem-index/SKILL.md": (
            "THE owner — the single append protocol, for every caller"
        ),
        "claude/skills/subsystem-index/reference/index-write.md": (
            "the owner's own rationale file: the measured evidence for the `Edit` "
            "anchor, including the case where a concurrent `Edit` succeeds silently"
        ),
        "claude/skills/analyze-service/reference/write-back.md": (
            "the pointer — quotes the retired prompt to record what the fork was; "
            "confined to the hashed region by the test below"
        ),
        "claude/skills/analyze-service/reference/index-store.md": (
            "the store's SCHEMA doc, recording that a stale word in its own prose "
            "('a confirmed write-back') survived the 2026-08-31 retirement"
        ),
        "claude/skills/prune-index/SKILL.md": (
            "the DELETION protocol for the same store. It KEEPS its y/N, "
            "deliberately: a cut rewrites the whole entry and removes bytes that "
            "are often their content's only copy, so blast radius earns the "
            "gate. Explicitly outside the append retirement, and it says so in "
            "its own text. Since the 2026-09-01 cutover its write MECHANISM is "
            "`cairn put` — only the mechanism moved, not the gate"
        ),
        "claude/skills/prune-index/reference/writing-and-safety.md": (
            "the prune protocol's own step-by-step, loaded on demand by the "
            "skill above. 🔴 It states the mechanism and always did — before the "
            "cutover it escaped this ledger only because its numbered list named "
            "no STORE noun, which is the residual gap layer 5 documents rather "
            "than a document that was checked and cleared. Enumerated now that "
            "it names one"
        ),
    }

    @staticmethod
    def _carriers() -> set[str]:
        return {
            p.relative_to(ROOT).as_posix()
            for p in SKILLS.rglob("*.md")
            if _store_write_paragraphs(p.read_text(encoding="utf-8", errors="replace"))
        }

    def test_the_carrier_set_is_EXACTLY_the_ledger(self) -> None:
        found = self._carriers()
        expected = set(self.ALLOWED)
        grew = sorted(found - expected)
        shrank = sorted(expected - found)
        assert found == expected, (
            "the set of skill documents stating a WRITE MECHANISM for the "
            "analyze-service index store has changed.\n"
            f"  NEW carriers (a second protocol is growing): {grew or 'none'}\n"
            f"  LOST carriers (a door stopped describing it): {shrank or 'none'}\n"
            "Ledger, with the reason each entry is allowed:\n  "
            + "\n  ".join(f"{k} — {v}" for k, v in self.ALLOWED.items())
            + "\n🔴 Unlike the `MANDATE` ledger above, this one does NOT require "
            "the new door to agree with the owner — a door that CONTRADICTS it "
            "lands here too, which is the whole reason this test exists. There is "
            "ONE append protocol and it lives in "
            f"{OWNER.relative_to(ROOT)}. Route to it. If a new document genuinely "
            "must state a mechanism, add it HERE with its reason, in the same "
            "commit, and say in the commit message what keeps it from drifting."
        )

    def test_the_predicate_can_OBSERVE_a_new_carrier(self, tmp_path: Path) -> None:
        """🔴 POSITIVE CONTROL. A scan returning a reassuring set is
        indistinguishable from one wired to nothing. Feed it a paragraph that
        MUST match — worded so it shares no literal with either door, uses none
        of `BANNED_IMPERATIVES` and never quotes `MANDATE` — and watch it match.
        """
        planted = (
            "## Recording the outcome\n"
            "Re-read the entry file, then emit the whole document with the "
            "`Write` tool once the operator has agreed.\n"
        )
        assert MANDATE not in planted
        assert not [b for b in BANNED_IMPERATIVES if b in planted]
        assert _store_write_paragraphs(planted), (
            "the disagreement predicate did not fire on a paragraph naming both a "
            "write tool and this store's entry files — it is wired to nothing."
        )

    def test_the_predicate_needs_BOTH_halves(self) -> None:
        """NEGATIVE CONTROL, both directions: neither half alone may fire.

        A predicate that accepted either half would flag documents that merely
        mention a tool, and documents that merely mention the store. MEASURED
        (cg#473 sweep, mutant M3): switching this `and` to `or` fails the carrier
        ledger and the owner's pin as well as this test."""
        mechanism_only = "Present the diff, then use `Edit` on the runbook.\n"
        store_only = "The index entry lives outside every repo.\n"
        assert not _store_write_paragraphs(mechanism_only)
        assert not _store_write_paragraphs(store_only)

    def test_front_matter_is_NOT_a_mechanism_statement(self) -> None:
        """Front matter is a DECLARATION about the skill, not an instruction to
        an executor, so it is stripped before the predicate runs — `allowed-tools`
        has its own test one layer up (`TestTheMandatedToolIsThePreApprovedOne`).

        🔴 The fixture's `description:` deliberately spells BOTH halves of the
        predicate, so this test is killed by removing the strip. An earlier
        version used a plain `allowed-tools: …, Write, Edit` line, which the
        MECHANISM pattern does not match anyway — it passed with the strip
        deleted, i.e. it asserted nothing."""
        doc = (
            "---\n"
            "name: some-skill\n"
            'description: "keeps the index entry fresh; use `Edit` for that"\n'
            "allowed-tools: Bash, Read, Write, Edit\n"
            "---\n\n"
            "This document routes to the owner and states no mechanism.\n"
        )
        body_without_front_matter = _FRONT_MATTER.sub("", doc)
        assert MECHANISM.search(doc) and STORE_CONTEXT.search(doc), (
            "premise gone: the fixture's front matter no longer spells both "
            "halves, so stripping it could not be what makes this pass."
        )
        assert not (
            MECHANISM.search(body_without_front_matter)
            and STORE_CONTEXT.search(body_without_front_matter)
        )
        assert not _store_write_paragraphs(doc)


class TestThePointerStatesNoMechanismOUTSIDEThePinnedRegion:
    """🔴 THE CONFINEMENT RULE — positional, so no wording can walk it.

    `TestTheMechanismLedger.test_the_pointers_only_occurrence_is_INSIDE_the_pinned_region`
    makes exactly this argument about the `MANDATE` fragment, and is therefore
    blind to a second protocol that never quotes it. This widens the same
    argument from one agreeing string to EVERY mechanism token: inside the
    region every word is hashed, so a quote there cannot drift; outside it, a
    paragraph is free to say anything, and that is where the fork regrew.

    A second protocol appended to this file is caught by WHERE it sits, not by
    what it says — the property the measured cg#473 mutant defeated.

    ⚠ The SCOPE of "no wording can walk it", stated exactly: once a paragraph
    names a `MECHANISM` token, no rewording of the surrounding prose helps. A
    fork that names NO mechanism token — no tool, no confirm gate — is outside
    this test, and outside every layer-5 test. That is the residual gap, and it
    is a property of the token list, not of the position rule.
    """

    REGION = "one-append-protocol"

    def test_every_mechanism_token_is_inside_the_region(self, pointer: str) -> None:
        outside = _mechanism_outside_region(pointer, self.REGION)
        assert not outside, (
            f"{POINTER.relative_to(ROOT)} states a write mechanism OUTSIDE its "
            f"pinned `one-append-protocol` region:\n"
            + "\n".join(f"  line {ln}: {tok!r}" for ln, tok in outside)
            + "\n\nThis file carries NO protocol of its own — it routes to "
            f"{OWNER.relative_to(ROOT)} at step 4 and quotes the retired prompt "
            "ONCE, inside the hashed region, to record what the fork was. A "
            "mechanism sentence anywhere else is a SECOND protocol regrowing, "
            "and it does not have to repeat a single word of the old one to be "
            "the same failure: until 2026-08-31 this file gated the append "
            "behind a y/N the owner had already retired, and told you to retype "
            "the whole entry with `Write`, which is MEASURED to lose a "
            "concurrent append silently.\n"
            "If the protocol is genuinely changing, change it in the OWNER and "
            "leave this file pointing."
        )

    def test_the_confinement_check_can_SEE_an_outside_occurrence(
        self, pointer: str
    ) -> None:
        """🔴 POSITIVE CONTROL. The assertion above is a claim about an EMPTY
        list, which is the shape a check wired to nothing also produces. So plant
        a mechanism sentence after the region's end marker and watch the same
        expression report it — with a sentence that shares no literal with the
        old text (`BANNED_IMPERATIVES`) and never quotes `MANDATE`, so what is
        being proven is the POSITION rule and not one of the substring pins.

        🔴 It calls `_mechanism_outside_region` — the SAME function the guard
        calls, not a second copy of the filter. With a copy, this control stayed
        green while the guard's own filter was mutated dead (cg#473 sweep, M6).
        """
        planted = "\n\nThen use the `Write` tool on the entry file.\n"
        assert MANDATE not in planted
        assert not [b for b in BANNED_IMPERATIVES if b in planted]
        assert _mechanism_outside_region(pointer + planted, self.REGION), (
            "the confinement expression reported nothing for a file with a "
            "mechanism sentence appended after the region — it is not reading "
            "what it claims to read."
        )


class TestTheOwnersMechanismProseIsPinnedWHOLE:
    """🔴 THE OWNER CAN FORK AGAINST ITSELF, and the ledgers cannot see it.

    Every ledger above ALLOWS the owner to state the mechanism — it is the
    protocol — so a contradicting carve-out appended to the owner changes no
    ledger, quotes no banned imperative, and leaves all seven `OWNER_SENTENCES`
    present. Measured (cg#473): green.

    So the owner's mechanism prose is pinned WHOLE, the way the pointer's region
    is: every paragraph of `subsystem-index/SKILL.md` that names a write
    mechanism for this store, normalised and concatenated, under one hash.
    CONTENT-selected rather than position-delimited, deliberately — a hash over
    the write half alone would miss a carve-out inserted into the read half, and
    the read half is where the historical record lives, which is precisely the
    text a re-opener would edit.

    The price, stated rather than discovered: rewording ANY of those paragraphs
    fails this test on purpose. The rest of the document — the probe, the
    windows, the caveats, most of its length — is untouched by it.
    """

    # Updated 2026-08-31 when #1132 merged into this branch. What moved: the
    # mechanism paragraph's parenthetical, from "a whole-file retype of a
    # curated, unbacked-up entry" to "a whole-file retype of a curated entry …
    # and the hourly commit behind it can be up to seventy minutes stale" — the
    # store HAS had an off-machine backup since 2026-08-21, and #1170's own
    # conflict analysis says to take #1132's wording here.
    #
    # 🔴 Both doors re-read before this hash was pasted, which is what the
    # failure message demands: `subsystem-index/SKILL.md` mandates `Edit`
    # anchored on `## Nuance / work-history` with `Write` only for a first-ever
    # file, and `write-back.md` routes there and restates no mechanism. ONE
    # protocol, and NO per-caller carve-out — the change was a factual
    # correction inside the paragraph, not an exception added to it.
    #
    # Updated 2026-09-01 for the Cairn write-through cutover (§8 step 7 of
    # `claudedocs/plan-cairn-phase1-cutover.md`). What moved: the write step is
    # now `cairn append` / `cairn put` against the pod, and the two rules it used
    # to carry — the re-read-and-re-apply, and the `Edit` anchored on
    # `## Nuance / work-history` — are named IN PLACE as replaced, not deleted
    # silently. The selection grew 4 -> 5 paragraphs: the new header paragraph
    # stating the freeze, and the two mechanism paragraphs replacing one.
    #
    # 🔴 Both doors re-read before this hash was pasted, which is what the
    # failure message demands: `write-back.md` is UNCHANGED by that commit — it
    # still routes to the owner at step 4 and states no mechanism of its own (its
    # own region hash is untouched) — and the owner's five paragraphs describe
    # ONE protocol for both callers with NO per-caller carve-out. The `Edit`
    # anchor survives in the owner only as the sentence saying what replaced it.
    EXPECTED_SHA = "0e359f317fefd7a2fc5b6e47b51c51b6562933f8592a06df8b7f5c97cda3c7f3"

    @staticmethod
    def _digest(owner: str) -> tuple[str, list[str]]:
        paras = [_normalise(p) for p in _store_write_paragraphs(owner)]
        return hashlib.sha256("\n\n".join(paras).encode("utf-8")).hexdigest(), paras

    def test_the_owners_mechanism_paragraphs_are_UNCHANGED(self, owner: str) -> None:
        actual, paras = self._digest(owner)
        assert actual == self.EXPECTED_SHA, (
            f"\nThe MECHANISM PROSE of {OWNER.relative_to(ROOT)} CHANGED.\n"
            f"  expected sha256 {self.EXPECTED_SHA}\n"
            f"  actual   sha256 {actual}\n"
            f"  {len(paras)} paragraph(s) currently selected:\n"
            + "\n".join(f"    - {p[:160]}…" for p in paras)
            + "\n\nThis is the ONE append protocol for this store, so a paragraph "
            "added here is not a local edit: it is what every caller executes. A "
            "CARVE-OUT is the dangerous shape — 'for caller X, confirm first and "
            "retype the file instead' reinstates, for one caller, exactly the "
            "fork closed on 2026-08-31, and no substring pin above can see it "
            "because it deletes nothing.\n"
            "So: re-read these paragraphs against "
            f"{POINTER.relative_to(ROOT)}, confirm the two doors still describe "
            "ONE protocol with no per-caller exception, then paste the actual "
            "sha above into EXPECTED_SHA in the SAME commit. Updating the hash "
            "without reading the other door is the one way to make this guard "
            "worthless."
        )

    def test_the_selection_is_NOT_EMPTY(self, owner: str) -> None:
        """🔴 A hash over zero paragraphs is a constant, and would pass forever
        against a document that had been gutted — the silent-zero shape. The
        count is pinned as a FLOOR rather than an equality so that adding a
        paragraph is reported by the hash above, with its actionable message,
        rather than by an off-by-one here."""
        _, paras = self._digest(owner)
        assert len(paras) >= 3, (
            f"only {len(paras)} mechanism paragraph(s) found in "
            f"{OWNER.relative_to(ROOT)}; the pin would be guarding almost "
            f"nothing. The owner must state: what the fork was, the `already "
            f"there` comparison the retired prompt did NOT do, and the mandated "
            f"`Edit`."
        )

    def test_the_hash_MOVES_for_an_appended_carve_out(self, owner: str) -> None:
        """🔴 POSITIVE CONTROL, built from the exact shape this class exists to
        catch: a per-caller exception appended to the owner. It quotes no
        `BANNED_IMPERATIVES` and never reproduces `MANDATE`, so a moved hash is
        attributable to THIS guard and to nothing else in the module."""
        carve_out = (
            "\n\n## Exception for one caller\n"
            "When the request came in through the other door, confirm with the "
            "operator first and then emit the whole entry file with the `Write` "
            "tool; the anchored-edit rule above does not apply to that caller.\n"
        )
        assert MANDATE not in carve_out
        assert not [b for b in BANNED_IMPERATIVES if b in carve_out]
        before, before_paras = self._digest(owner)
        after, after_paras = self._digest(owner + carve_out)
        assert len(after_paras) == len(before_paras) + 1, (
            "the carve-out was not selected by the predicate at all, so this "
            "control proves nothing about the hash."
        )
        assert after != before, "the hash did not move for an appended carve-out"

    def test_a_REWRAP_does_not_move_the_hash(self, owner: str) -> None:
        """The affordance, asserted rather than described: the same paragraphs
        re-wrapped hash the same, because `_normalise` collapses whitespace. A
        changed WORD still moves it — `test_normalisation_ignores_REWRAP_and_not_REWORD`
        pins that half."""
        original = "Still print the unified diff before writing"
        assert original in owner, "premise gone: the rewrap target is not in the owner"
        rewrapped = owner.replace(original, "Still print the\nunified diff\nbefore writing", 1)
        assert rewrapped != owner
        assert self._digest(rewrapped)[0] == self._digest(owner)[0]


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


# =============================================================================
# The PRUNE door — same store, different verb, and its gate stays.
# =============================================================================


class TestThePruneDoorMovedItsMechanismAndKeptItsGate:
    """🔴 `claudedocs/plan-cairn-phase1-cutover.md` §5 names `prune-index` "the
    writer most likely to be forgotten": it runs rarely, so a break arrives weeks
    later looking like a corrupt store rather than a missed migration. The 0444
    freeze makes its whole-file `Write` return `EACCES`, so its mechanism had to
    move to `cairn put`.

    Two claims, pulling in OPPOSITE directions, which is why both are pinned:
    the MECHANISM had to change (or the skill is simply dead on every host), and
    the y/N had to SURVIVE (the retirement evidence — "the answer was always
    `y`" — was measured on an APPEND; a cut removes bytes that are often their
    content's only copy, and blast radius earns the gate). A migration that took
    the gate with it would look like a success and be a regression.

    ⚠ The ledgers above already fail if this door stops stating a mechanism at
    all. They say nothing about WHICH mechanism, or about the gate — measured:
    deleting the `cairn put` command line from this skill left every ledger and
    every layer of this module green. That gap is what this class closes.
    """

    PRUNE = SKILLS / "prune-index" / "SKILL.md"
    DETAIL = SKILLS / "prune-index" / "reference" / "writing-and-safety.md"

    SENTENCES: list[tuple[str, str, str]] = [
        (
            "SKILL.md",
            "cairn put --scope <scope> --ref <entry> --file /tmp/prune-<entry>.md",
            "🔴 the migrated mechanism — a cut lands on the pod, not on a 0444 file",
        ),
        (
            "SKILL.md",
            "the local entry files are `0444` and any editor write against one "
            "fails with `EACCES`",
            "WHY it moved, stated where the command is, so an EACCES is read as "
            "the design and not as a broken store",
        ),
        (
            "SKILL.md",
            "ask one yes/no",
            "🔴 the gate that must SURVIVE the migration",
        ),
        (
            "SKILL.md",
            "**Keep the y/N here.**",
            "the gate's reason, restated so a later reader does not 'finish' the "
            "retirement by removing it",
        ),
        (
            "SKILL.md",
            "Only the write MECHANISM moved",
            "🔴 the scope of the change, in one sentence",
        ),
        (
            "SKILL.md",
            "Exit 8 IS that writer",
            "a concurrent append fails the put LOUDLY instead of being cut away",
        ),
        (
            "SKILL.md",
            "Sync first and audit the CACHE",
            "🔴 the data-loss shape: a cut built from the frozen mirror's stale "
            "bytes silently deletes every bullet appended since the freeze",
        ),
        (
            "reference/writing-and-safety.md",
            "land the scratch file with `cairn put`",
            "the step-by-step agrees with the skill body — one mechanism, two "
            "documents, which is the pairing this module exists to compare",
        ),
        (
            "reference/writing-and-safety.md",
            "ask a single yes/no",
            "the gate survives in the detail file too",
        ),
    ]

    def _doc(self, which: str) -> str:
        path = self.PRUNE if which == "SKILL.md" else self.DETAIL
        return path.read_text(encoding="utf-8")

    @pytest.mark.parametrize(
        "which,sentence,why", SENTENCES, ids=[f"{w}:{y[:40]}" for w, _, y in SENTENCES]
    )
    def test_sentence_still_present(self, which: str, sentence: str, why: str) -> None:
        assert sentence in self._doc(which), (
            f"claude/skills/prune-index/{which} no longer contains the sentence "
            f"pinning {why}.\n  missing: {sentence!r}\n"
            f"  This is the store's DELETION protocol. Its mechanism moved to "
            f"`cairn put` at the 2026-09-01 freeze and its y/N deliberately did "
            f"NOT move — restore the sentence, or change it and update this pin "
            f"in the SAME commit saying which of the two claims changed."
        )

    def test_the_prune_pin_can_report_absence(self) -> None:
        """NEGATIVE CONTROL, both documents: a substring check against a doc that
        happens to contain everything is indistinguishable from one pointed at
        the wrong file."""
        for which in ("SKILL.md", "reference/writing-and-safety.md"):
            assert (
                "a sentence deliberately absent from the prune skill"
                not in self._doc(which)
            )

    @pytest.mark.parametrize("doc", [PRUNE, DETAIL], ids=lambda p: p.name)
    def test_the_prune_door_is_TRACKED_by_git(self, doc: Path) -> None:
        """Same argument as `TestTheDoorsAreTheDeployedOnes`: the flake source
        contains only tracked files, so an untracked door deploys as an ABSENCE
        while every pin above passes against the working copy. Returns rather
        than skipping when there is no `.git` — the nix check tier builds from a
        store copy with no repository, and `run-tests.sh` pins its skip set."""
        assert doc.is_file(), f"pinned prune door is gone: {doc}"
        if not (ROOT / ".git").exists():
            return
        rel = doc.relative_to(ROOT).as_posix()
        out = subprocess.run(
            ["git", "-C", str(ROOT), "ls-files", "--error-unmatch", "--", rel],
            capture_output=True,
            text=True,
        )
        assert out.returncode == 0, f"{rel} is not tracked by git.\n{out.stderr}"
