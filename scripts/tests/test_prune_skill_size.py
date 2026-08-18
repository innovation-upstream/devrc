"""Deterministic byte-size gate for `claude/skills/prune-skill/SKILL.md`.

Lives in `scripts/tests/` rather than beside the skill, mirroring
`scripts/tests/test_rules_size.py` which gates `claude/RULES.md` the same way.
Two reasons: `scripts/tests` is already a HERMETIC_TARGET, and every hermetic
target must start with `scripts/` -- an invariant asserted by
`test_run_tests_targets.py` as a POSITIVE CONTROL on its own parser, so bending
it to admit a `claude/` target would weaken a vacuity guard. Keeping the test
here also stops it shipping into `~/.claude/skills/prune-skill/` via
`home.file recursive`, where it would be dead weight in the deployed tree.

WHY THIS EXISTS
---------------
`prune-skill` is the skill that tells everyone else to hold a byte budget, and it
had no enforcement of its own. It grew 11,083 -> 17,391 B in a single change that
added twelve rules, and was only brought back down by six manual passes. A prose
budget did not hold here either -- the same failure `scripts/browser-bridge/tests/
test_skill_size.py` documents for the browser skill.

`claude/RULES.md`: "Prefer deterministic/structural fixes over prompt-tuning,
prose instructions, or suffix/keyword heuristics." This file is that fix. The
numbers below are the SINGLE source of truth for this skill's ceiling -- any other
mention (the skill body, a handoff doc, a PR description) must cross-reference
this module rather than restate the literal, because a second hand-maintained copy
is exactly how the drift regrows.

WHY THE CEILING IS ABOVE THE 12,288 B TARGET, AND WHAT THAT COSTS
-----------------------------------------------------------------
The skill states a 12,288 B target and browser-bridge MEETS it (11,821 B while
routing ~11x its own weight), so the target is achievable and is not in dispute.
This file does not: it sits at ~12.9 KB after being cut from 17,391 B by
demoting §6 (landing), §4's deployment table, §7's verification rationale, §0's
axes and the always-loaded model to three sidecars, plus stripping evidence from
every remaining section.

The residual is the classification taxonomy (§3) and the rule NAMES in §0/§7.
Demoting those was considered and rejected on the record: §3 is consulted at the
moment of the decision, so putting it behind a load is the same defect as burying
a rule in a table cell, and reducing §0/§7 further would make required checks
invisible without a second load.

So this ceiling is a RATCHET, not an endorsement: it pins the file where six
passes left it and forbids regrowth. It is deliberately NOT set to 12,288,
because a permanently-red gate trains everyone to click through -- which
`claude/RULES.md` names as worse than no gate. Lowering it as the file gets
leaner is the intended direction of travel; raising it needs the same kind of
justification recorded above.

The honest accounting: the skill is ~4.7% over the target it asks others to meet.
That is disclosed in the body, in the PR that introduced it, and here.
"""
from pathlib import Path

# The hard ceiling: SKILL.md must never exceed this many bytes.
# 12,864 B measured + 192 B headroom (see MIN_HEADROOM_BYTES).
MAX_BYTES = 13_056

# Required working margin below the ceiling. A file sitting one byte under
# technically holds the line but leaves no room for a one-line correction, which
# is the exact position browser-bridge was re-breached from three times in a day.
#
# Sized in units of a REAL edit rather than a round number: the two structures
# that actually grow here are the reference routing table (3 rows, 289 B -> mean
# 96 B/row) and §3's verdict sub-bullets (mean ~190 B). 192 B is >= two mean
# routing rows and >= one mean sub-bullet, so the headroom test fires BEFORE the
# ceiling rather than arriving as a surprise alongside it.
MIN_HEADROOM_BYTES = 192

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SKILL_DIR = REPO_ROOT / "claude" / "skills" / "prune-skill"
SKILL_MD = SKILL_DIR / "SKILL.md"
REFERENCE_DIR = SKILL_DIR / "reference"


def _existing_topics() -> list[str]:
    """Reference topics that exist RIGHT NOW, read off the filesystem.

    Globbed rather than hard-coded: browser-bridge's hand-maintained literal
    drifted to 8 of 11 topics and steered a maintainer into creating a duplicate
    topic for content that already had a home. A hard-coded list regrows that bug
    on the next reference file added; this cannot.
    """
    return sorted(p.name for p in REFERENCE_DIR.glob("*.md"))


def test_skill_md_exists():
    assert SKILL_MD.is_file(), f"{SKILL_MD} is missing -- did the skill move?"


def test_skill_md_under_hard_ceiling():
    size = SKILL_MD.stat().st_size
    assert size <= MAX_BYTES, (
        f"SKILL.md is {size:,} B, over the {MAX_BYTES:,} B ceiling by "
        f"{size - MAX_BYTES:,} B.\n"
        "Do NOT raise MAX_BYTES to make this pass. Demote to reference/ instead "
        "-- and per the skill's own §5, by VERBATIM LINE-RANGE SLICING, then run "
        "its §7 verification (gap audit + >=5-population survival check) over the "
        "result. Rewording does not work: six passes on this file yielded "
        "995/221/1194/359/560/243/7 B, and the ones that moved were the ones that "
        "moved whole blocks out."
    )


def test_skill_md_keeps_working_headroom():
    """Fire BEFORE the ceiling, so a breach is never a surprise."""
    size = SKILL_MD.stat().st_size
    headroom = MAX_BYTES - size
    assert headroom >= MIN_HEADROOM_BYTES, (
        f"SKILL.md is {size:,} B, leaving only {headroom:,} B under the "
        f"{MAX_BYTES:,} B ceiling -- below the {MIN_HEADROOM_BYTES:,} B working "
        "floor. The next routine edit will breach it. Evict now, while there is "
        "still room to do it deliberately."
    )


def test_every_reference_topic_is_routed_from_the_core():
    """An ORPHANED sidecar is unreachable content, and it happens silently.

    One campaign skill held 40 KB across three sidecars that no routing line
    mentioned -- previously-demoted topics whose pointers were lost in a later
    edit. `skill-audit.py` reports orphans, but nothing FAILS on one, so the
    condition persisted across sessions. This makes it fail.
    """
    body = SKILL_MD.read_text(encoding="utf-8")
    topics = _existing_topics()
    assert topics, f"no reference topics found under {REFERENCE_DIR}"
    unrouted = [t for t in topics if t not in body]
    assert not unrouted, (
        f"reference topics exist but the core routes to none of them: {unrouted}\n"
        "Either add a routing line (the default -- an orphan is usually a demoted "
        "topic that lost its pointer, not dead weight) or delete the file and say "
        "in the commit why it is dead."
    )
