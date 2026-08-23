"""Deterministic byte-size gate for `claude/skills/handoff/SKILL.md`.

WHY THIS EXISTS
---------------
`/handoff` is the skill that runs at the END of a session -- at a context reset,
or when the window is already tight. It is therefore the one skill body whose
cost lands at the worst possible moment, and it is the LARGEST skill body in the
repo. Measured 2026-08-23 across `claude/skills/*/SKILL.md`:

    handoff                   46,263 B     <- this file
    check-clickup-addressed   41,207 B
    signal                    28,616 B
    activity                  27,196 B
    resume                    23,849 B

Two other always-on docs already have enforced ceilings -- `claude/RULES.md`
(`scripts/tests/test_rules_size.py`) and `scripts/browser-bridge/SKILL.md`
(`scripts/browser-bridge/tests/test_skill_size.py`). Skill BODIES had none, and
`CLAUDE.md`'s note about ceilings names only those two. This closes the gap for
the body that most needs it. The other skills are deliberately NOT gated here:
a repo-wide ratchet would go red on `check-clickup-addressed` the day it landed,
and `claude/RULES.md` is explicit that a permanently-red gate is worse than no
gate. Extending this module per-skill, each with its own measured ceiling, is
the natural next step -- not a single shared constant.

WHAT THE CEILING PROTECTS -- AND WHAT IT MUST NOT DO
----------------------------------------------------
🔴 This gate must NEVER be satisfied by deleting an instruction. The skill's
prose is pinned verbatim by two lists -- `HANDOFF_SENTENCES` (77 entries, in
`TestSkillDocsArePinned`, `scripts/tests/test_subsystem_touch.py`; that list
covers two skills, not only this one) and `SKILL_PINS` (14 entries, in
`scripts/tests/test_handoff_doc.py`). Counts measured 2026-08-23 -- re-count
rather than trusting them. The comments in the first record that pinning only a
headline clause left the surrounding paragraph "deletable green" -- 1,513 chars
-> 100 with the suite passing, silently taking the doc's only mention of a tool
block, a fallback search and an UNFILED instruction with it.

MEASURED, so the next person sizing this up does not repeat the estimate: a full
eviction pass over step 4 on 2026-08-23 -- moving every dated measurement and
incident narrative into `reference/index-write.md` §7 while keeping every
imperative and every pinned phrase -- yielded **1,993 bytes out of 28,589**
(step 4, base -> head).

⚠ THOSE ARE BYTES, which is the unit this gate measures. Characters run ~5%
lower on this file, and an audit reported the same eviction as "1,890" that way.
The two are not in conflict -- they are different units -- but a size claim that
says only "~2 KB" cannot be checked against either, which is the same ambiguity
`test_the_gate_measures_BYTES_not_characters` exists to kill in the code.

The step is mostly imperative and procedure, not narrative. So the lever this
gate provides is NOT "cut step 4 again"; it is "do not add without evicting",
and where a real cut is available it is a TOOL change (guidance the tool prints
at the moment it applies costs nothing when it does not fire), never a prose
trim.

The numbers below are the SINGLE source of truth. Any other mention of them
(CLAUDE.md, handoff docs, PR bodies) must cross-reference this module rather
than restate the literal -- a second hand-maintained copy of the number is
exactly how the drift regrows.

This module lives in `scripts/tests`, which is in `HERMETIC_TARGETS` in
`scripts/run-tests.sh`, so it runs in `nix build .#checks.x86_64-linux.pytests`.
"""
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SKILL_MD = REPO_ROOT / "claude" / "skills" / "handoff" / "SKILL.md"
REFERENCE_DIR = SKILL_MD.parent / "reference"

# The hard ceiling: the handoff skill body must never exceed this many bytes.
#
# 🔴 An ANTI-REGROWTH ratchet, not a target. Ratcheting it DOWN as the tool
# absorbs guidance is welcome and is the intended direction of travel. Ratcheting
# it UP requires saying in the commit message which instruction could not be
# expressed in the budget.
#
# ⚠ THE CURRENT SIZE IS DELIBERATELY NOT WRITTEN DOWN HERE. It is a derived
# measurement edited in the same commits as the thing it measures, and
# test_rules_size.py records three consecutive rounds where exactly that went
# stale within its own PR. The failure messages below PRINT current / ceiling /
# free / budget / RECLAIM; that is the authority.
#
# What IS durable, and is why the ceiling sits where it does: the 2026-08-23 pass
# that set it evicted 1,993 B of dated narrative from step 4 into
# reference/index-write.md §7, and spent MORE than that back -- on the
# step-2/step-5 fix that made step 5 the doc's only writer, and then on the rules
# an adversarial audit of that fix produced (exit-3 handling, where the scratch
# file lives, `--exclude` on all three command blocks).
#
# 🔴 NET THE BODY GREW: 46,263 -> 46,784 B, +521. An earlier draft of this
# comment said "NET ~250 B" in the SHRINKING direction, which was true of an
# intermediate tree and false by the time it shipped -- the exact failure
# test_rules_size.py records three times, a derived measurement written into
# prose that the same commit keeps editing. Do not read this gate as evidence
# the body was cut. It was not. It was stopped from growing further, and the
# audit round is what proved the gate can bind: SKILL.md landed 316 B above the
# floor, and paying for those rules meant finding evictions rather than raising
# the number.
#
# Sized in units of a REAL edit rather than picked round, the way
# test_rules_size.py and test_skill_size.py both size theirs. Measured over the
# 94 paragraphs of this file: mean 486 B, median 435 B, p90 827 B, max 1,928 B;
# over its 54 🔴 rules: mean 572 B, max 1,444 B. The ceiling leaves roughly two
# mean rules of slack above the size it was set at before the warning fires,
# which is a working margin without making the gate decorative. RULES.md's own
# sizing formula (size + floor + six mean rules) would give ~50,200 here; that
# is deliberately not taken, because the finding this file is under review for is
# that it is already too big.
MAX_BYTES = 48_000

# Required working margin below the ceiling.
#
# Sized to one LARGE rule, matching test_rules_size.py's reasoning and, by
# coincidence of measurement, its value: the p90 paragraph here is 827 B and the
# mean 🔴 rule is 572 B, so a floor below ~900 B would fire at the same moment as
# the ceiling and deliver the surprise it exists to prevent.
MIN_HEADROOM_BYTES = 900


def _existing_topics() -> str:
    """The reference topics that exist RIGHT NOW, read off the filesystem.

    Globbed rather than hard-coded for the reason test_skill_size.py gives: a
    hand-maintained list drifts, and a drifted list steers a maintainer into
    creating a duplicate topic for content that already has a home.
    """
    topics = sorted(p.stem for p in REFERENCE_DIR.glob("*.md"))
    if not topics:
        return f"(none found under {REFERENCE_DIR} -- did reference/ move?)"
    return ", ".join(topics)


def _eviction_playbook() -> str:
    return f"""
  How to fix -- do NOT delete or narrow an instruction to make this pass:

    🔴 ~70 sentences in step 4 are pinned verbatim by TestSkillDocsArePinned in
       scripts/tests/test_subsystem_touch.py, and ~12 more in
       scripts/tests/test_handoff_doc.py. A green suite does NOT mean the step
       survived an edit: those pins catch DELETION of a named instruction and
       DRIFT in its wording, not a rewrite that keeps the words and guts the
       reasoning around them. Read the step cold after editing it.

    What to move OUT (to claude/skills/handoff/reference/<topic>.md):
      - dated measurements, incident narratives, byte/token counts, PR numbers
      - superseded or retracted reasoning
      - worked examples where the rule already states its own shape
      (existing topics: {_existing_topics()})
      Leave the imperative in the body with a `📖 §N` pointer.

    What STAYS in SKILL.md:
      - the imperative, its scope, and the procedure needed to comply
      - every command the executor has to run, with its flags
      - enough failure SHAPE that a reader knows when the rule applies

    ⚠ MEASURED: a full eviction pass over step 4 (2026-08-23) yielded ~2.0 KB
    out of 28.3 KB. If you are here again, prose eviction is probably exhausted.
    The remaining lever is to move guidance INTO the tool -- subsystem_touch.py
    already prints ROUTE OUT / WRONG WINDOW? / NO PATH FOOTPRINT? / SKILL HOMES
    / RECOVER at the moment each applies, which costs nothing on the runs where
    it does not fire, unlike a paragraph in the body that is paid every time.

    Ceiling + headroom constants live in scripts/tests/test_handoff_skill_size.py.
"""


def _size() -> int:
    return len(SKILL_MD.read_bytes())


def test_skill_md_exists():
    """Guard the guard: a moved/renamed SKILL.md must not silently pass."""
    assert SKILL_MD.is_file(), (
        f"{SKILL_MD} not found -- the size gate below would be vacuous. "
        "If the handoff skill moved, update SKILL_MD in this module."
    )


def test_eviction_playbook_lists_every_reference_topic():
    """Guard the guard, part 2: the playbook only renders on FAILURE, so a wrong
    topic list is invisible until the day someone needs it -- which is exactly
    how browser-bridge's drifted to 8 of 11. Exercise the derivation directly."""
    on_disk = sorted(p.stem for p in REFERENCE_DIR.glob("*.md"))
    assert on_disk, (
        f"no reference/*.md found under {REFERENCE_DIR} -- either the eviction "
        "pattern was abandoned or REFERENCE_DIR is wrong; either way the "
        "playbook below would name no destinations, and the only remaining way "
        "to satisfy MAX_BYTES would be to delete instructions."
    )
    playbook = _eviction_playbook()
    missing = [t for t in on_disk if t not in playbook]
    assert not missing, (
        f"the eviction playbook omits existing reference topics {missing}; a "
        "maintainer following it would create a duplicate topic. The list must "
        "stay derived from the filesystem, never hard-coded."
    )


def test_handoff_skill_under_hard_ceiling():
    size = _size()
    assert size <= MAX_BYTES, (
        f"\n\nclaude/skills/handoff/SKILL.md is OVER its hard ceiling.\n"
        f"  current:  {size:,} bytes\n"
        f"  ceiling:  {MAX_BYTES:,} bytes\n"
        f"  OVER BY:  {size - MAX_BYTES:,} bytes\n"
        f"{_eviction_playbook()}"
    )


def test_handoff_skill_keeps_working_headroom():
    """The ceiling alone is not enough -- keep a margin to edit into.

    Fails in the MAX_BYTES-MIN_HEADROOM .. MAX_BYTES band (and above, where the
    ceiling test also fires with the overage), so "you are one rule from
    breaking it" arrives as a signal rather than as a surprise.
    """
    size = _size()
    headroom = MAX_BYTES - size
    assert headroom >= MIN_HEADROOM_BYTES, (
        f"\n\nclaude/skills/handoff/SKILL.md has no working headroom left.\n"
        f"  current:   {size:,} bytes\n"
        f"  ceiling:   {MAX_BYTES:,} bytes\n"
        f"  free:      {headroom:,} bytes  (minimum required: "
        f"{MIN_HEADROOM_BYTES:,})\n"
        f"  budget:    {MAX_BYTES - MIN_HEADROOM_BYTES:,} bytes "
        f"(ceiling minus the required margin)\n"
        f"  RECLAIM:   {MIN_HEADROOM_BYTES - headroom:,} bytes\n"
        f"{_eviction_playbook()}"
    )


def test_the_gate_can_report_a_breach(tmp_path, monkeypatch):
    """🔴 NEGATIVE CONTROL, and it drives the REAL test functions.

    A ceiling test reads a file it never writes, so on a compliant tree nothing
    exercises its failure branch: `test_handoff_skill_under_hard_ceiling` would
    pass identically if `_size()` returned a constant, if the comparison were
    inverted, or if SKILL_MD pointed at an empty file. `claude/RULES.md` calls
    that a harness that has not been shown it can go red.

    So: repoint the module at an oversized file and watch it fail with its own
    message, then at a tiny one and watch it pass (a gate that always fails is
    equally useless). The HEADROOM half has its own control below -- it must
    not be certified from this one; see that test's docstring for why.
    """
    fat = tmp_path / "fat.md"
    fat.write_bytes(b"x" * (MAX_BYTES + 1))
    monkeypatch.setattr("test_handoff_skill_size.SKILL_MD", fat)

    with pytest.raises(AssertionError) as ceiling:
        test_handoff_skill_under_hard_ceiling()
    assert "OVER BY:  1 bytes" in str(ceiling.value)

    # The playbook rendered into that failure must name a real destination --
    # a maintainer over budget who is given no topic deletes text instead.
    assert "index-write" in str(ceiling.value)

    # POSITIVE HALF: the same function passes on a compliant file, so the red
    # above is a fact about the size and not about the harness.
    thin = tmp_path / "thin.md"
    thin.write_bytes(b"x" * 10)
    monkeypatch.setattr("test_handoff_skill_size.SKILL_MD", thin)
    test_handoff_skill_under_hard_ceiling()
    test_handoff_skill_keeps_working_headroom()


def test_the_headroom_half_fires_on_its_OWN_condition(tmp_path, monkeypatch):
    """🔴 MIN_HEADROOM_BYTES WAS A DEAD CONSTANT, and this is what killed it.

    MEASURED: setting `MIN_HEADROOM_BYTES = 0` left all five tests GREEN. The
    whole band `[0, MAX_BYTES - current size]` survived as a mutant, so the
    early-warning half of this gate -- the half whose entire purpose is to
    arrive BEFORE the surprise -- was certified by nothing.

    The cause is the exact shape `claude/RULES.md` names: "a mutant that removes
    a guard TOGETHER WITH ITS ENCLOSING CONDITION proves nothing about the
    guard, and dies for the wrong reason." The control above uses a fixture of
    `MAX_BYTES + 1`, which makes `headroom == -1`. That is over the CEILING, so
    the headroom assertion fired for the ceiling's reason and its own comparison
    was never the thing under test -- it would have fired at any floor value,
    including zero.

    So this control lands the size squarely INSIDE the warning band, where the
    ceiling is satisfied and only the floor can object:

        MAX_BYTES - MIN_HEADROOM_BYTES + 1  ->  headroom == MIN_HEADROOM_BYTES - 1

    and asserts BOTH directions -- the ceiling test PASSES while the headroom
    test FAILS. Lower MIN_HEADROOM_BYTES by one byte and this goes red.
    """
    in_band = tmp_path / "in-band.md"
    in_band.write_bytes(b"x" * (MAX_BYTES - MIN_HEADROOM_BYTES + 1))
    monkeypatch.setattr("test_handoff_skill_size.SKILL_MD", in_band)

    # The ceiling is NOT breached -- this is the half that makes the assertion
    # below a statement about MIN_HEADROOM_BYTES rather than about MAX_BYTES.
    test_handoff_skill_under_hard_ceiling()

    with pytest.raises(AssertionError) as headroom:
        test_handoff_skill_keeps_working_headroom()
    msg = str(headroom.value)
    assert "RECLAIM:   1 bytes" in msg, msg
    assert "index-write" in msg

    # And one byte the other way is clean, so the boundary is pinned on both
    # sides rather than "big enough fails".
    at_floor = tmp_path / "at-floor.md"
    at_floor.write_bytes(b"x" * (MAX_BYTES - MIN_HEADROOM_BYTES))
    monkeypatch.setattr("test_handoff_skill_size.SKILL_MD", at_floor)
    test_handoff_skill_under_hard_ceiling()
    test_handoff_skill_keeps_working_headroom()


def test_the_gate_measures_BYTES_not_characters(tmp_path, monkeypatch):
    """🔴 THE UNIT WAS UNPINNED, and it is worth ~561 bytes of silent budget.

    MEASURED: mutating `_size()` from `read_bytes()` to `read_text("utf-8")`
    left every test GREEN while granting the real file 561 bytes of extra
    allowance -- it is 46,014 bytes but 45,453 characters.

    The cause is `claude/RULES.md`'s fixture rule: every control above writes
    `b"x" * N`, pure ASCII, where bytes and characters are the same number. A
    fixture that CANNOT distinguish two implementations does not test between
    them. "Feed a value the constant cannot equal and watch the output move."

    So this one is multibyte, sized so the two readings land on OPPOSITE sides
    of the ceiling: over it in bytes, far under it in characters. A byte-reading
    `_size()` fails here; a character-reading one passes, and the `pytest.raises`
    turns that pass into a red test.
    """
    # "é" is 2 bytes, 1 character in UTF-8.
    chars = MAX_BYTES // 2 + 1
    multibyte = tmp_path / "multibyte.md"
    multibyte.write_text("é" * chars, encoding="utf-8")
    assert len(multibyte.read_bytes()) > MAX_BYTES, "fixture is not over in bytes"
    assert chars < MAX_BYTES, "fixture is not under in characters"

    monkeypatch.setattr("test_handoff_skill_size.SKILL_MD", multibyte)
    with pytest.raises(AssertionError) as breach:
        test_handoff_skill_under_hard_ceiling()
    # Name the unit in the failure, so a future reader of a red run is not left
    # inferring which number the gate meant.
    assert f"{len(multibyte.read_bytes()):,} bytes" in str(breach.value)
