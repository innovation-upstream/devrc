"""Gate on the SKILL LISTING — the third always-on cost, and the one that fails
SILENTLY.

Every skill's `name` + `description` loads on EVERY session under a budget of 1%
of the context window, with a per-entry cap of 1,536 characters. On overflow
Claude Code DROPS descriptions, starting with the skills invoked least, with no
error at all. So a description is ROUTING SURFACE, not documentation
(`CLAUDE.md` -> Layout), and nothing in this repo measured it.

WHY THIS EXISTS
---------------
`clickup` shipped a 175-char description of generic API-speak -- "Interact with
ClickUp tasks and documents ... Use when working with ClickUp task/doc URLs or
IDs". It was the SHORTEST of the 36 shipped entries (`git show
<pre>:claude/skills/clickup/SKILL.md`), so it carried the fewest trigger
keywords, and it named none of the task-shaped siblings a "task" prompt could
route to instead -- `clawgate` (self-hosted approval UI plus its own Tasks),
`initiatives` (durable cross-repo board), `mailbox` (email action-items queue).
A low-invocation skill is also first in line to be dropped on overflow, so the
one entry least able to afford weak routing had the weakest.

WHAT IS AND IS NOT MACHINE-CHECKABLE HERE
-----------------------------------------
"Is this description good routing surface?" is a judgement, and pretending
otherwise would produce a guard that reads as coverage while providing none
(`claude/RULES.md` -> "A guard's DESCRIPTION claims COVERAGE"). Three things ARE
decidable, and only those are asserted:

  1. every shipped skill still HAS a parseable, non-empty description -- an entry
     the listing drops entirely routes nothing;
  2. no entry breaches the per-entry cap -- past it the description is truncated
     or dropped silently;
  3. `clickup` names its disambiguating siblings, AND each name it uses is a
     skill that actually exists.

(3) is a RELATIONSHIP, not a word: renaming or deleting any ledgered sibling
turns it red, so the disambiguation cannot rot into a pointer at nothing. The
DECLARED gap: the sibling ledger below is a judgement call and is checked in one
direction only -- a name in it must exist and must be named, but nothing here can
know that a FIFTH task-shaped skill has appeared. Add it here when one does.

A FOURTH did, which is why that sentence now reads FIFTH.
`check-clickup-addressed` was migrated into devrc after this gate was written,
and it is the most confusable sibling yet: it works the SAME ClickUp tasks and
asks the opposite question -- not "what is on this ticket" but "was the work
actually done", answered by reading session transcripts. The overlap has a cheap
half and an expensive half, and only the expensive half is a listing entry:
`node query.mjs awaiting` (in `clickup`) is the ~45s triage pass answering WHICH
tasks have someone else's comment last, and `check-clickup-addressed` is the
transcript-reading verdict on WHETHER each is done. `awaiting` is a CLI command,
not a skill, so it costs the listing nothing and must NOT be added to the ledger
below -- every key there is asserted to be a real skill directory.

NOT asserted: a minimum description length. Measured when this was written, the
defect was 175 chars and the next-shortest entry was 181 (`espanso-audit`), so
any floor between them would be reverse-engineered from this one incident rather
than measured -- a number that reds a genuinely narrow skill with a genuinely
short description while a 200-char generic entry sails through. Length was the
symptom; the missing siblings are the part with a decidable answer.
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SKILLS_DIR = REPO_ROOT / "claude" / "skills"

# The per-entry ceiling Claude Code applies to one listing entry. NOT a devrc
# choice -- an upstream limit, restated in `CLAUDE.md` -> Layout. Do not raise it
# to make a description fit; cut the description.
PER_ENTRY_CAP_CHARS = 1_536

# Vacuity floor on the scan itself. 36 entries were measured at the time this
# gate was written; a glob that silently matches nothing would otherwise report a
# clean sweep of an empty set -- the "reassuring zero" `claude/RULES.md` names.
# Set below the measurement so adding or retiring a skill does not red the gate,
# high enough that a broken discovery path cannot pass.
MIN_LISTING_ENTRIES = 30

# The two skills deployed by `mkOutOfStoreSymlink` from `scripts/` instead of the
# recursive `claude/skills` mapping (`nix/home.nix`; the same pair
# `test_doc_path_rot.py` maps). They are listing entries like any other, so a
# scan of `claude/skills/` alone measures 34 of 36 -- a count of DECLARATIONS
# rather than of INSTANCES. Their absence is a failure below, not a skip.
OUT_OF_TREE_SKILLS = (
    REPO_ROOT / "scripts" / "browser-bridge" / "SKILL.md",
    REPO_ROOT / "scripts" / "dl-router" / "SKILL.md",
)

# The task-shaped siblings a bare "task"/"ticket" prompt could route to instead
# of `clickup`, each with why it is confusable. Keys must be real skill
# directories -- asserted, so a rename cannot leave a dead pointer in the
# always-on listing.
CLICKUP_SIBLINGS = {
    "clawgate": "the self-hosted approval UI, which has its own Tasks",
    "initiatives": "the durable cross-repo initiative board",
    "mailbox": "the email action-items queue",
    "check-clickup-addressed": (
        "verifies from session transcripts whether work on a ClickUp task was "
        "actually completed -- the same tasks, the opposite question"
    ),
}

# The disambiguating sentence, pinned WHOLE and built from the sibling names.
#
# 🔴 Deliberately not a per-name substring check. `claude/RULES.md`: "When the
# artifact under test IS prose, a guard on WORDS is walkable by REWORDING -- pin
# the WHOLE normalised string." A description could name all three siblings in
# passing ("see also clawgate, initiatives, mailbox") and satisfy a word check
# while telling the router nothing about which one owns which case. The stated
# cost, accepted: a cosmetic reword of this ONE sentence fails this test. The
# routing keywords in the rest of the description are deliberately left free to
# change -- rigidifying those would make the gate fight the very tuning it exists
# to protect.
CLICKUP_DISAMBIGUATION = (
    "This is the EXTERNAL ClickUp workspace — the self-hosted approval UI and "
    "ITS Tasks are `clawgate`, the durable cross-repo board is `initiatives`, "
    "the email action-items queue is `mailbox`, and verifying from session "
    "transcripts whether work on a task was actually done is "
    "`check-clickup-addressed`."
)


def _load_parser():
    """The repo's OWN frontmatter reader, not a second copy of one.

    `scripts/opencode/generate-commands.py::parse_skill` is what turns these
    files into the opencode command listing, so measuring with it means this gate
    measures the string that actually ships. A hand-rolled regex here would be a
    second implementation free to disagree with the deployed one -- and it is
    disagreement, not absence, that would make this gate lie.
    """
    path = REPO_ROOT / "scripts" / "opencode" / "generate-commands.py"
    loader = importlib.machinery.SourceFileLoader("_gen_commands", str(path))
    spec = importlib.util.spec_from_loader("_gen_commands", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod.parse_skill


parse_skill = _load_parser()


def _skill_md_paths() -> list[Path]:
    """Every SKILL.md that becomes a listing entry, in-tree and out."""
    found = sorted(SKILLS_DIR.glob("*/SKILL.md"))
    for extra in OUT_OF_TREE_SKILLS:
        assert extra.is_file(), (
            f"{extra} is missing. It is deployed into ~/.claude/skills/ by an "
            "mkOutOfStoreSymlink in nix/home.nix, so it IS a listing entry. If it "
            "moved, re-point this list -- do not drop it, or the gate silently "
            "stops measuring a skill that still ships."
        )
        found.append(extra)
    return found


def _entries() -> list[tuple[str, str, str]]:
    """(repo-relative path, name, description) for every listing entry.

    A file the parser rejects is reported by `_unparseable()`, not silently
    skipped: opencode DROPS such a skill from the listing, which is the loud
    version of the failure this module gates.
    """
    out = []
    for path in _skill_md_paths():
        parsed = parse_skill(path)
        if parsed is None:
            continue
        out.append((str(path.relative_to(REPO_ROOT)), parsed["name"],
                    parsed["description"]))
    return out


def _unparseable() -> list[str]:
    return [str(p.relative_to(REPO_ROOT)) for p in _skill_md_paths()
            if parse_skill(p) is None]


def entry_chars(name: str, description: str) -> int:
    """Characters one listing entry costs.

    `name` counts because the cap is on the ENTRY, not on the description alone.

    A double-quoted YAML value reaches us with its `\\"` escapes unresolved, so
    such a description measures a character or two LONG. That is deliberate and
    stated: the error is conservative, so this check can only ever be stricter
    than reality, never laxer.
    """
    return len(name) + len(description)


def over_cap(entries) -> list[tuple[str, int]]:
    """Direction 1 of the gate, factored out so the controls below grade THE
    GATE rather than a re-implementation of it."""
    return sorted(
        (label, entry_chars(name, desc))
        for label, name, desc in entries
        if entry_chars(name, desc) > PER_ENTRY_CAP_CHARS
    )


# --------------------------------------------------------------------------- #
# The live tree
# --------------------------------------------------------------------------- #

def test_the_scan_finds_the_skills_at_all():
    """POSITIVE CONTROL on discovery. Every other assertion here is a reassuring
    ZERO, and a zero from a scan that walked nothing is indistinguishable from a
    clean sweep."""
    entries = _entries()
    assert len(entries) >= MIN_LISTING_ENTRIES, (
        f"only {len(entries)} listing entries found under {SKILLS_DIR} (+ the "
        f"{len(OUT_OF_TREE_SKILLS)} out-of-tree skills), below the "
        f"{MIN_LISTING_ENTRIES} vacuity floor. Every check in this module would "
        "otherwise pass over an almost-empty set. Fix the discovery path."
    )


def test_every_shipped_skill_has_a_parseable_description():
    """A SKILL.md the frontmatter reader rejects is DROPPED from the listing --
    it routes nothing, and nothing says so."""
    broken = _unparseable()
    assert not broken, (
        "these SKILL.md files have no `name`+`description` frontmatter the "
        f"repo's own parser can read, so they drop out of the listing: {broken}"
    )
    empty = [label for label, _, desc in _entries() if not desc.strip()]
    assert not empty, f"these skills have an empty description: {empty}"


def test_no_listing_entry_breaches_the_per_entry_cap():
    offenders = over_cap(_entries())
    assert not offenders, (
        "these listing entries exceed the "
        f"{PER_ENTRY_CAP_CHARS:,}-char per-entry cap:\n"
        + "\n".join(f"  {label}: {n:,} chars" for label, n in offenders)
        + "\nPast the cap the description is truncated or dropped SILENTLY, "
        "taking its trigger keywords with it. Cut the description -- move the "
        "narrative into the body, which costs 0 until the skill is invoked. Do "
        "NOT raise PER_ENTRY_CAP_CHARS; it is an upstream limit, not our choice."
    )


@pytest.mark.parametrize("sibling", sorted(CLICKUP_SIBLINGS))
def test_each_clickup_sibling_is_a_real_skill(sibling):
    """The disambiguation must point at something that EXISTS. A renamed or
    retired sibling turns this red instead of leaving the always-on listing
    routing readers at a skill that is gone."""
    assert (SKILLS_DIR / sibling / "SKILL.md").is_file(), (
        f"clickup's description disambiguates from `{sibling}` "
        f"({CLICKUP_SIBLINGS[sibling]}), but claude/skills/{sibling}/SKILL.md "
        "does not exist. Update both the description and this ledger."
    )


def test_clickup_disambiguates_from_its_task_shaped_siblings():
    """🔴 THE DEFECT THIS MODULE WAS WRITTEN FOR.

    `clickup`'s description named none of the skills a "task"/"ticket" prompt
    could route to instead, so three sibling skills competed for the same
    prompts with nothing in the always-on listing to separate them.

    Pinned as the WHOLE sentence, not as three separate name lookups -- see
    CLICKUP_DISAMBIGUATION for why a word check is walkable here.
    """
    entries = {name: desc for _, name, desc in _entries()}
    assert "clickup" in entries, "the clickup skill is not in the listing at all"
    normalised = " ".join(entries["clickup"].split())

    for sibling in sorted(CLICKUP_SIBLINGS):
        assert sibling in CLICKUP_DISAMBIGUATION, (
            f"`{sibling}` is in the sibling ledger but the pinned "
            "disambiguation sentence does not mention it -- the ledger and the "
            "pin have drifted apart, so this gate is checking less than it says."
        )

    assert normalised.count(CLICKUP_DISAMBIGUATION) == 1, (
        "clickup's description no longer carries its disambiguation sentence "
        "exactly once as written.\n  Expected verbatim:\n    "
        + CLICKUP_DISAMBIGUATION
        + "\n  Description is now:\n    "
        + normalised
        + "\nWithout it a 'task'/'ticket' prompt has nothing in the always-on "
        f"listing separating clickup from {sorted(CLICKUP_SIBLINGS)}. Restore "
        "the wording, or re-point this pin deliberately in the same commit."
    )


# --------------------------------------------------------------------------- #
# NEGATIVE CONTROLS -- each check is shown able to go RED, against realistic
# input rather than a textbook fixture, and graded by the SAME helpers the live
# tests use so a mutation anywhere in the pipeline moves a verdict here.
# --------------------------------------------------------------------------- #

def _real_clickup_description() -> str:
    parsed = parse_skill(SKILLS_DIR / "clickup" / "SKILL.md")
    assert parsed is not None, "fixture broken: clickup frontmatter unreadable"
    return parsed["description"]


def test_control_an_over_cap_entry_is_reported():
    """Can `over_cap` go red at all? Built from the REAL description padded past
    the cap, not from a synthetic wall of 'x' -- a check that only ever sees
    textbook input is a check whose behaviour on real input is unmeasured."""
    fat = _real_clickup_description()
    fat = fat + " " + ("routing keywords " * 100)
    assert entry_chars("clickup", fat) > PER_ENTRY_CAP_CHARS, "fixture too small"
    assert over_cap([("fixture/SKILL.md", "clickup", fat)]) == [
        ("fixture/SKILL.md", entry_chars("clickup", fat))
    ]


def test_control_an_at_cap_entry_is_not_reported():
    """The other side of the boundary: exactly AT the cap is legal. A gate that
    reds on correct input trains everyone to click through it."""
    name = "clickup"
    desc = "x" * (PER_ENTRY_CAP_CHARS - len(name))
    assert entry_chars(name, desc) == PER_ENTRY_CAP_CHARS
    assert over_cap([("fixture/SKILL.md", name, desc)]) == []


def test_control_the_name_counts_toward_the_cap():
    """ISOLATED mutation: only the name grows. If `entry_chars` ever drops the
    name, this is the assertion that moves -- the cap-breach tests above would
    not, because their descriptions breach on their own."""
    desc = "x" * (PER_ENTRY_CAP_CHARS - 1)
    assert over_cap([("fixture/SKILL.md", "ab", desc)]) == [
        ("fixture/SKILL.md", PER_ENTRY_CAP_CHARS + 1)
    ]
    assert over_cap([("fixture/SKILL.md", "a", desc)]) == []


def test_control_the_pre_change_clickup_description_fails_the_pin():
    """🔴 RED-AT-BASE, kept as a test rather than as a claim in a commit message.

    This is the exact string that shipped before this gate existed. It must fail
    the disambiguation pin -- if it ever passes, the pin has been loosened into
    something the original defect satisfies.
    """
    pre_change = (
        "Interact with ClickUp tasks and documents - get task details, view "
        "comments, create and manage tasks, create and edit docs. Use when "
        "working with ClickUp task/doc URLs or IDs."
    )
    normalised = " ".join(pre_change.split())
    assert normalised.count(CLICKUP_DISAMBIGUATION) == 0
    for sibling in CLICKUP_SIBLINGS:
        assert sibling not in normalised, (
            f"the pre-change description mentioned `{sibling}` after all -- "
            "re-derive this fixture from git history before trusting it"
        )
    # ...and it was under the cap the whole time, which is the point: the cap
    # check alone would NEVER have caught this defect. Two independent hazards,
    # two independent checks.
    assert entry_chars("clickup", pre_change) < PER_ENTRY_CAP_CHARS


def test_control_a_passing_mention_of_every_sibling_still_fails_the_pin():
    """The walk the whole-sentence pin exists to block: name all three siblings,
    disambiguate from none of them. A per-name substring check passes this."""
    walked = (
        "Interact with ClickUp tasks and documents. See also clawgate, "
        "initiatives, mailbox and check-clickup-addressed."
    )
    for sibling in CLICKUP_SIBLINGS:
        assert sibling in walked, "fixture broken: it must name every sibling"
    assert " ".join(walked.split()).count(CLICKUP_DISAMBIGUATION) == 0


def test_control_a_skill_without_frontmatter_is_reported_not_skipped(tmp_path):
    """A SKILL.md the parser rejects must arrive as a failure. Graded through
    the real parser, because "dropped from the listing" is exactly what
    `parse_skill` returning None means downstream."""
    bare = tmp_path / "SKILL.md"
    bare.write_text("# a skill with no frontmatter at all\n", encoding="utf-8")
    assert parse_skill(bare) is None

    no_desc = tmp_path / "nodesc.md"
    no_desc.write_text("---\nname: orphan\n---\nbody\n", encoding="utf-8")
    assert parse_skill(no_desc) is None


def test_control_the_module_measures_the_real_tree_not_a_fixture():
    """Cheap tripwire against this file drifting into self-referential green:
    the live entries must include skills this module never names."""
    names = {name for _, name, _ in _entries()}
    for expected in ("clickup", "browser", "dl-router", "session-manager"):
        assert expected in names, (
            f"`{expected}` is not in the scanned listing -- discovery is reading "
            "the wrong tree, or an out-of-tree skill stopped being picked up"
        )
