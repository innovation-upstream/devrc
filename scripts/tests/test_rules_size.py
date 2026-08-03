"""Deterministic byte-size gate for `claude/RULES.md`.

WHY THIS EXISTS
---------------
`claude/RULES.md` is PAID TWICE on every unit of work:

  1. Claude Code loads it into every session (via `~/.claude/CLAUDE.md`'s
     `@RULES.md` import, deployed by `nix/home.nix`).
  2. `nix/home.nix` CONCATENATES it into `~/.config/opencode/AGENTS.md`
     (PRINCIPLES + RULES + opencode-addendum), so every opencode session
     carries it too -- including the cheap flash-model `nav` subagent whose
     entire job is glob/grep/read and which needs none of it.

It was append-only by process, and it grew accordingly -- measured via
`git show <rev>:claude/RULES.md | wc -c`:

    2026-06-23    8,348      2026-07-30   14,147
    2026-07-11    9,487      2026-08-02   41,566   <- 2.9x in three days

Twelve commits touched it in the three days before the split, titled "rescue
stranded lessons" and "record what today measured". Every one of those commits
was individually correct: a real lesson, really measured. Nothing in the process
ever asked what the file cost, so nothing ever pushed back.

`claude/RULES.md` itself says: "Prefer deterministic/structural fixes over
prompt-tuning, prose instructions, or suffix/keyword heuristics." A comment at
the top of RULES.md asking people to keep it short is the prose fix, and the
growth curve above is what the prose fix achieves. This file is the structural
one.

WHAT THE CEILING PROTECTS -- AND WHAT IT MUST NOT DO
----------------------------------------------------
RULES.md's opening rule is "Read every rule at its WIDEST reading", and the
`git stash` prohibition was re-broadened on 2026-08-01 precisely because a
narrow wording let a subagent walk into the failure it forbade. So this gate
must NEVER be satisfied by narrowing or deleting a rule.

The eviction target is `claude/RULES-ARCHIVE.md`: worked incidents, dates, byte
counts, store paths, PR numbers, retracted theories. The RULE -- its imperative,
its priority marker, its triggers, its widest scope, and any procedure needed to
act on it -- stays in the core. The playbook below says this on every failure,
because the failure message is the only documentation anyone reads at the moment
they are over budget.

The numbers below are the SINGLE source of truth. Any other mention of them
(CLAUDE.md, handoff docs, PR bodies) must cross-reference this module rather
than restate the literal -- a second hand-maintained copy of the number is
exactly how the drift regrows.

This module lives in `scripts/tests`, which is in `HERMETIC_TARGETS` in
`scripts/run-tests.sh`, so it runs in `nix build .#checks.x86_64-linux.pytests`
-- the repo's real pre-merge gate.
"""
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
RULES_MD = REPO_ROOT / "claude" / "RULES.md"
ARCHIVE_MD = REPO_ROOT / "claude" / "RULES-ARCHIVE.md"

# The hard ceiling: RULES.md must never exceed this many bytes.
#
# Sized off the post-split measurement, NOT off a round number and NOT off an
# aspiration. At the split RULES.md was 33,105 B (down from 41,566 B); this
# ceiling is that plus ~1.4 KB, i.e. room for a genuinely new rule or two before
# anyone has to make a decision.
#
# 🔴 This is an ANTI-REGROWTH gate, not a target. It is deliberately NOT set to
# the 15 KB that motivated the split -- see the module docstring of the split PR
# and the note on MIN_HEADROOM_BYTES. Ratcheting it DOWN as rules consolidate is
# welcome and is the intended direction of travel. Ratcheting it UP requires
# saying in the commit message which rule could not be expressed in the budget.
MAX_BYTES = 34_500

# Required working margin below the ceiling.
#
# Sized in units of a REAL edit, the way test_skill_size.py's floor is: measured
# at the split, the mean SUBSTANTIVE bullet in RULES.md (a 🔴/🟡 rule with its
# scope clause and procedure) is ~500 B, and the largest are ~900 B. A floor
# below that would fire at the same moment as the ceiling and deliver the
# surprise it exists to prevent, so it is set to one full large rule.
MIN_HEADROOM_BYTES = 900


def _size() -> int:
    return len(RULES_MD.read_bytes())


def _archive_anchors() -> list[str]:
    """The `## <anchor>` headings that exist in the archive RIGHT NOW.

    Globbed off the file rather than hard-coded, for the same reason
    test_skill_size.py globs its reference topics: a hand-maintained list drifts,
    and a drifted list steers a maintainer into duplicating an entry that
    already has a home.
    """
    return [
        ln[3:].strip()
        for ln in ARCHIVE_MD.read_text(encoding="utf-8").splitlines()
        if ln.startswith("## ") and not ln.startswith("## Table")
    ]


def _eviction_playbook() -> str:
    anchors = _archive_anchors()
    listed = ", ".join(anchors) if anchors else "(none found -- did the archive move?)"
    return f"""
  How to fix -- do NOT delete or narrow a rule to make this pass:

    🔴 A rule must not lose SCOPE. RULES.md's own opening rule is "read every
       rule at its WIDEST reading", and the `git stash` ban had to be
       re-broadened after a narrow wording let a subagent walk straight into
       the failure it forbade. Trimming "for ANY reason", "not just X",
       "this is a CLASS, not one manifestation" or a rule's triggers to buy
       bytes REINTRODUCES that failure mode. Don't.

    What to move OUT (to claude/RULES-ARCHIVE.md):
      - worked incident narratives: dates, hostnames, store paths, byte counts,
        PR numbers, the blow-by-blow of how it was diagnosed
      - superseded or retracted theories
      - enumerated ground cases where one representative case carries the point

    What STAYS in claude/RULES.md:
      - the imperative, its priority marker (🔴/🟡/🟢) and its triggers
      - the widest scope of the hazard
      - enough of the failure SHAPE that a reader knows when the rule applies
      - any command/procedure needed to actually comply

    Mechanics:
      1. Add a `## <anchor>` section to claude/RULES-ARCHIVE.md holding the
         evidence, with an italic `*Supports: <the rule>.*` line under it.
         (existing anchors: {listed})
      2. Leave a `→ archive: <anchor>` tag on the rule in claude/RULES.md.
      3. Re-run this test. Ceiling + headroom constants live in
         scripts/tests/test_rules_size.py.

    Remember RULES.md is paid TWICE -- once per Claude Code session, and again
    inside the generated ~/.config/opencode/AGENTS.md.
"""


def test_rules_md_exists():
    """Guard the guard: a moved/renamed RULES.md must not silently pass."""
    assert RULES_MD.is_file(), (
        f"{RULES_MD} not found -- the size gate below would be vacuous. "
        "If RULES.md moved, update RULES_MD in this module."
    )


def test_archive_exists_and_has_anchors():
    """Guard the guard, part 2.

    The playbook only renders on FAILURE, so a wrong/empty anchor list is
    invisible until the day someone needs it. Exercise the derivation directly,
    and fail loudly if the archive was deleted -- without it the only way to
    satisfy the ceiling is to delete rules, which is the outcome this whole
    module exists to prevent.
    """
    assert ARCHIVE_MD.is_file(), (
        f"{ARCHIVE_MD} not found. RULES.md's eviction target is gone, so the "
        "only remaining way to satisfy MAX_BYTES is to delete or narrow rules. "
        "Restore the archive."
    )
    anchors = _archive_anchors()
    assert anchors, (
        f"{ARCHIVE_MD} has no `## <anchor>` sections -- the eviction playbook "
        "would name no destinations."
    )
    playbook = _eviction_playbook()
    missing = [a for a in anchors if a not in playbook]
    assert not missing, (
        f"the eviction playbook omits existing archive anchors {missing}; a "
        "maintainer following it would create a duplicate entry. The list must "
        "stay derived from the file, never hard-coded."
    )


def test_every_archive_pointer_resolves():
    """A `→ archive: <anchor>` tag in the core must name a real archive section.

    A dangling pointer is worse than no pointer: it tells a reader the evidence
    exists and then wastes the lookup. This also catches an archive section
    renamed without updating the core.
    """
    import re

    anchors = set(_archive_anchors())
    referenced = set(
        re.findall(r"→ archive: ([a-z0-9-]+)", RULES_MD.read_text(encoding="utf-8"))
    )
    dangling = sorted(referenced - anchors)
    assert not dangling, (
        f"claude/RULES.md points at archive anchors that do not exist: "
        f"{dangling}.\nArchive has: {sorted(anchors)}"
    )


def test_rules_md_under_hard_ceiling():
    size = _size()
    assert size <= MAX_BYTES, (
        f"\n\nclaude/RULES.md is OVER its hard ceiling.\n"
        f"  current:  {size:,} bytes\n"
        f"  ceiling:  {MAX_BYTES:,} bytes\n"
        f"  OVER BY:  {size - MAX_BYTES:,} bytes\n"
        f"{_eviction_playbook()}"
    )


def test_rules_md_keeps_working_headroom():
    """The ceiling alone is not enough -- keep a margin to edit into.

    Fails in the MAX_BYTES-MIN_HEADROOM .. MAX_BYTES band (and above, where the
    ceiling test also fires with the overage), so "you are one rule from
    breaking it" arrives as a signal rather than as a surprise. The margin is
    sized to one full large rule; see the comment on MIN_HEADROOM_BYTES.
    """
    size = _size()
    headroom = MAX_BYTES - size
    assert headroom >= MIN_HEADROOM_BYTES, (
        f"\n\nclaude/RULES.md has no working headroom left.\n"
        f"  current:   {size:,} bytes\n"
        f"  ceiling:   {MAX_BYTES:,} bytes\n"
        f"  free:      {headroom:,} bytes  (minimum required: "
        f"{MIN_HEADROOM_BYTES:,})\n"
        f"  budget:    {MAX_BYTES - MIN_HEADROOM_BYTES:,} bytes "
        f"(ceiling minus the required margin)\n"
        f"  RECLAIM:   {MIN_HEADROOM_BYTES - headroom:,} bytes\n"
        f"{_eviction_playbook()}"
    )
