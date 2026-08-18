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
had no enforcement of its own. It grew 11,083 -> 14,918 B in a single change that
added twelve rules (`git cat-file -s cebbd6d:claude/skills/prune-skill/SKILL.md`,
and that change's own commit message), and came back down only by moving whole
blocks out to sidecars. A prose budget did not hold here either -- the same
failure `scripts/browser-bridge/tests/test_skill_size.py` documents for the
browser skill.

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
This file does not: it sits at 12,779 B (12.48 KiB) after being cut from 14,918 B
by demoting §6 (landing), §4's deployment table, §7's verification rationale, §0's
axes and the always-loaded model to three sidecars, plus stripping evidence from
every remaining section.

The residual is the classification taxonomy (§3) and the rule NAMES in §0/§7.
Demoting those was considered and rejected on the record: §3 is consulted at the
moment of the decision, so putting it behind a load is the same defect as burying
a rule in a table cell, and reducing §0/§7 further would make required checks
invisible without a second load.

So this ceiling is a RATCHET, not an endorsement: it pins the file where the
demotion passes left it and forbids regrowth. It is deliberately NOT set to 12,288,
because a permanently-red gate trains everyone to click through -- which
`claude/RULES.md` names as worse than no gate. Lowering it as the file gets
leaner is the intended direction of travel; raising it needs the same kind of
justification recorded above.

The honest accounting: the skill is 491 B -- 4.0% -- over the target it asks
others to meet (12,779 against 12,288; `skill-audit.py` prints the same 491 B
independently). That is disclosed in the body, in the PR that introduced it, and
here. Every number in this docstring is re-measured, not carried forward: an
earlier revision restated a size, a growth figure, a percentage and a per-pass
byte ledger that were all wrong, in the module that declares itself the single
source of truth for them.
"""
import re
from pathlib import Path

# The hard ceiling: SKILL.md must never exceed this many bytes.
#
# NOT a derivation -- a measured position. SKILL.md is 12,779 B (`stat -c %s` and
# `git cat-file -s` agree), so 13,056 leaves 277 B of headroom, of which
# MIN_HEADROOM_BYTES (192) is the floor that must remain: ~85 B of true working
# room before the headroom test fires. The comment here previously read
# "12,864 B measured + 192 B headroom"; the file measured 12,834 at the time, so
# the arithmetic was describing a size the file never had. Re-measure before
# touching this number, and lower it as the file gets leaner -- never raise it.
MAX_BYTES = 13_056

# Required working margin below the ceiling. A file sitting one byte under
# technically holds the line but leaves no room for a one-line correction, which
# is the exact position browser-bridge was re-breached from three times in a day.
#
# Sized in units of a REAL edit rather than a round number, and re-measured
# against the current file rather than restated: the two structures that actually
# grow here are the reference routing table (3 rows, 348 B -> mean 116 B/row) and
# §3's verdict bullets (9 lines, 1,739 B -> mean 193 B). 192 B is therefore
# ~one mean §3 bullet, or one routing row with room to spare -- enough that the
# headroom test fires BEFORE the ceiling rather than arriving alongside it.
#
# It is NOT two mean routing rows: that claim was here, and at the real 116 B/row
# two rows are 232 B > 192. Kept at 192 on the measurement that does hold rather
# than raised to fit a sentence.
MIN_HEADROOM_BYTES = 192

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SKILL_DIR = REPO_ROOT / "claude" / "skills" / "prune-skill"
SKILL_MD = SKILL_DIR / "SKILL.md"
REFERENCE_DIR = SKILL_DIR / "reference"

# A routing PATH the core writes, with any prefix (`reference/x.md`,
# `~/.claude/skills/prune-skill/reference/x.md`, `.claude/.../reference/x.md`).
# Captures the basename, which is what resolves under REFERENCE_DIR. A
# placeholder (`reference/<topic>.md`) deliberately does not match: `<topic>` is
# not a filename, so it is not a route.
ROUTING_PATH = re.compile(r"reference/([\w.-]+\.md)")

# The block the core uses as its reference registry. Its rows are what a reader
# loads from, so a cell that stops being a path is a real break even when the
# basename still appears in prose elsewhere.
REGISTRY_MARKER = "**Reference topics**"


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
        "result. Rewording does not work: what has ever moved this file is moving "
        "whole blocks out to reference/ -- 14,918 -> 12,834 B in #531. (A per-pass "
        "byte ledger used to be quoted here; it reconciled with no pair of "
        "committed blobs and its intermediates were never committed, so it is "
        "dropped rather than guessed at.)"
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


def _routing_table_rows(body: str) -> list[str]:
    """The core's reference routing table -- the registry a reader loads from.

    Located structurally rather than by column names: the markdown table rows
    between the `**Reference topics**` marker and the next H2. If the marker is
    gone the registry moved, and that is a loud failure by design -- re-point
    this test at the new registry rather than deleting the assertion.
    """
    start = body.find(REGISTRY_MARKER)
    assert start != -1, (
        f"{REGISTRY_MARKER!r} not found in {SKILL_MD}. This test pins that block as "
        "the core's reference registry; if the registry moved, re-point the test."
    )
    end = body.find("\n## ", start)
    block = body[start:] if end == -1 else body[start:end]
    return [ln for ln in block.splitlines() if ln.lstrip().startswith("|")]


def test_every_reference_topic_is_routed_from_the_core():
    """An ORPHANED sidecar is unreachable content, and it happens silently.

    One campaign skill held 40 KB across three sidecars that no routing line
    mentioned -- previously-demoted topics whose pointers were lost in a later
    edit. `skill-audit.py` reports orphans, but nothing FAILS on one, so the
    condition persisted across sessions. This makes it fail.

    STRUCTURAL, NOT SPELLED. This started as `t not in body` -- a bare substring
    test on the basename -- and two executed probes walked straight through it,
    both shipping `4 passed`:

      * renaming `reference/` -> `refrence/` throughout SKILL.md (every routing
        path in the core dead) -- the basenames still appeared, so it passed;
      * rewriting a routing table cell to a bare prose mention
        (`` `reference/staleness-pass.md` `` -> "the staleness-pass.md notes")
        -- not a path at all, and it passed.

    The skill's own section 4 is entirely about routing paths that RESOLVE, so a
    guard that a non-path satisfies is the defect that section warns about. It
    now extracts the routing PATHS the core actually writes and resolves each
    against the filesystem, in both directions.
    """
    body = SKILL_MD.read_text(encoding="utf-8")
    topics = _existing_topics()
    assert topics, f"no reference topics found under {REFERENCE_DIR}"

    # Direction 1: no dangling route. Every `reference/<x>.md` path anywhere in
    # the core must resolve to a file that exists.
    dangling = sorted(
        {n for n in ROUTING_PATH.findall(body) if not (REFERENCE_DIR / n).is_file()}
    )
    assert not dangling, (
        f"the core routes to reference topics that do not exist: {dangling}\n"
        f"Nothing under {REFERENCE_DIR} answers those paths -- the reader follows "
        "the pointer and lands nowhere. Fix the path or restore the file."
    )

    # Direction 2: no orphan. Every topic on disk must be reachable from the
    # registry by a RESOLVING path, not merely name-dropped somewhere in prose.
    routed = {n for row in _routing_table_rows(body) for n in ROUTING_PATH.findall(row)}
    unrouted = [t for t in topics if t not in routed]
    assert not unrouted, (
        f"reference topics exist but the core's routing table does not route to "
        f"them by a resolving path: {unrouted}\n"
        f"Routed by the table: {sorted(routed) or 'NOTHING'}.\n"
        "A bare mention of the filename is NOT a route -- write "
        "`reference/<topic>.md`. Either add the routing row (the default -- an "
        "orphan is usually a demoted topic that lost its pointer, not dead "
        "weight) or delete the file and say in the commit why it is dead."
    )
