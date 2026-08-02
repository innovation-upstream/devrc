"""Deterministic byte-size gate for `scripts/browser-bridge/SKILL.md`.

WHY THIS EXISTS
---------------
The `browser` skill body is loaded into context on every browser task, so it has
a hard budget. That budget used to be enforced by a prose warning in the project
`CLAUDE.md` ("`wc -c` before you commit"). It did not hold: the ceiling was
breached three times in a single day at 2-11 bytes of headroom, and once by 974
bytes when a feature commit added op documentation without evicting anything.
Each breach was caught by a human noticing, or not caught until later.

`claude/RULES.md`: "Prefer deterministic/structural fixes over prompt-tuning,
prose instructions, or suffix/keyword heuristics." This file is that fix. The
numbers below are the SINGLE source of truth for the ceiling -- any other
mention of it (CLAUDE.md, handoff docs) must cross-reference this module rather
than restate the literal, because a second hand-maintained copy of the number is
exactly how the drift regrows.

This module is part of the hermetic set (`scripts/run-tests.sh`), so it runs in
`nix build .#checks.x86_64-linux.pytests` -- the repo's real pre-merge gate.
"""
from pathlib import Path

# The hard ceiling: SKILL.md must never exceed this many bytes.
MAX_BYTES = 12_288

# Required working margin below the ceiling. A file sitting at 12,287 bytes
# technically holds the line but leaves no room for a one-line correction --
# and that is the exact position this file has been re-breached from. The
# ENFORCED budget is therefore MAX_BYTES - MIN_HEADROOM_BYTES = 12,038.
#
# The floor is sized in units of a REAL edit, not a round number. Measured on
# the 2026-08-02 surface audit, the two tables that actually grow are:
#   ops table       21 rows, 3,995 B -> mean 190 B/row
#   reference table 11 rows, 1,823 B -> mean 166 B/row
# At the old 100-byte floor -- BELOW both means -- the headroom test could not
# fire before the ceiling did: one new row jumped straight past both, so the
# "early warning" arrived as a surprise, which is the failure the docstring in
# test_skill_md_keeps_working_headroom describes. 250 B is >= one mean
# reference row and >= 1.3 mean ops rows, so the warning now genuinely precedes
# the breach.
MIN_HEADROOM_BYTES = 250

SKILL_MD = Path(__file__).resolve().parent.parent / "SKILL.md"
REFERENCE_DIR = SKILL_MD.parent / "reference"


def _existing_topics() -> str:
    """The reference topics that exist RIGHT NOW, read off the filesystem.

    Deliberately globbed rather than hard-coded: the previous hand-maintained
    literal drifted to 8 of 11 topics (missing emulation, read-envelopes,
    auth-pages), so the playbook steered a maintainer into creating a duplicate
    topic for content that already had a home. A hard-coded list regrows that
    bug on the next reference file added; this cannot.
    """
    topics = sorted(p.stem for p in REFERENCE_DIR.glob("*.md"))
    if not topics:
        return f"(none found under {REFERENCE_DIR} -- did reference/ move?)"
    return ", ".join(topics)


def _eviction_playbook() -> str:
    return f"""
  How to fix (the #233/#235 pattern -- do NOT just delete content):
    1. Pick a coherent block of the core that is only needed for ONE kind of
       task (a subsystem, a failure mode, a fallback path).
    2. Move it whole into scripts/browser-bridge/reference/<topic>.md
       (existing topics: {_existing_topics()}).
    3. Leave a ~110-byte pointer row in SKILL.md naming the topic and its
       REPO-ABSOLUTE path -- nix/home.nix symlinks only SKILL.md and the
       `browser` CLI into ~/.claude/skills/browser/, NOT reference/, so a
       relative path does not resolve for the reader.
    4. Re-run this test. Ceiling + headroom constants live in
       scripts/browser-bridge/tests/test_skill_size.py.
"""


def _size() -> int:
    return len(SKILL_MD.read_bytes())


def test_skill_md_exists():
    """Guard the guard: a moved/renamed SKILL.md must not silently pass."""
    assert SKILL_MD.is_file(), (
        f"{SKILL_MD} not found -- the size gate below would be vacuous. "
        "If SKILL.md moved, update SKILL_MD in this module."
    )


def test_eviction_playbook_lists_every_reference_topic():
    """Guard the guard, part 2: the playbook only renders on FAILURE, so a
    wrong topic list is invisible until the day someone needs it -- which is
    exactly how it drifted to 8 of 11. Exercise the derivation directly."""
    on_disk = sorted(p.stem for p in REFERENCE_DIR.glob("*.md"))
    assert on_disk, (
        f"no reference/*.md found under {REFERENCE_DIR} -- either the eviction "
        "pattern was abandoned or REFERENCE_DIR is wrong; either way the "
        "playbook below would name no topics."
    )
    playbook = _eviction_playbook()
    missing = [t for t in on_disk if t not in playbook]
    assert not missing, (
        f"the eviction playbook omits existing reference topics {missing}; a "
        "maintainer following it would create a duplicate topic. The list must "
        "stay derived from the filesystem, never hard-coded."
    )


def test_skill_md_under_hard_ceiling():
    size = _size()
    assert size <= MAX_BYTES, (
        f"\n\nscripts/browser-bridge/SKILL.md is OVER its hard ceiling.\n"
        f"  current:  {size:,} bytes\n"
        f"  ceiling:  {MAX_BYTES:,} bytes\n"
        f"  OVER BY:  {size - MAX_BYTES:,} bytes\n"
        f"{_eviction_playbook()}"
    )


def test_skill_md_keeps_working_headroom():
    """The ceiling alone is not enough -- keep a margin to edit into.

    Fails in the MAX_BYTES-MIN_HEADROOM .. MAX_BYTES band (and above, where the
    ceiling test also fires with the overage). "You are one word from breaking
    it" becomes a signal instead of a surprise.

    That promise only holds if the margin is bigger than one edit. It is sized
    off the MEASURED mean table row (190 B ops / 166 B reference -- see the
    comment on MIN_HEADROOM_BYTES); the earlier 100 B floor was smaller than
    both, so it fired at the same moment as the ceiling and delivered the
    surprise it exists to prevent.
    """
    size = _size()
    headroom = MAX_BYTES - size
    assert headroom >= MIN_HEADROOM_BYTES, (
        f"\n\nscripts/browser-bridge/SKILL.md has no working headroom left.\n"
        f"  current:   {size:,} bytes\n"
        f"  ceiling:   {MAX_BYTES:,} bytes\n"
        f"  free:      {headroom:,} bytes  (minimum required: "
        f"{MIN_HEADROOM_BYTES:,})\n"
        f"  budget:    {MAX_BYTES - MIN_HEADROOM_BYTES:,} bytes "
        f"(ceiling minus the required margin)\n"
        f"  RECLAIM:   {MIN_HEADROOM_BYTES - headroom:,} bytes\n"
        f"{_eviction_playbook()}"
    )
