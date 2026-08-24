"""Deterministic byte-size gate for `claude/skills/handoff/SKILL.md`.

WHY THIS EXISTS
---------------
`/handoff` is the skill that runs at the END of a session -- at a context reset,
or when the window is already tight. It is therefore the one skill body whose
cost lands at the worst possible moment, and it is the LARGEST skill body in the
repo. Measured 2026-08-23 across `claude/skills/*/SKILL.md` (sizes at the base
commit; this file has since grown -- read `_size()`, not this table):

    handoff                   46,263 B     <- this file
    check-clickup-addressed   41,207 B
    signal                    28,616 B
    activity                  27,196 B
    resume                    23,849 B

🔴 THIS IS THE FIFTH BYTE GATE IN THE REPO AND THE THIRD ON A SKILL BODY -- it
follows an established pattern, it did not invent one. The full set:

    claude/RULES.md                      test_rules_size.py
    scripts/browser-bridge/SKILL.md      browser-bridge/tests/test_skill_size.py
    claude/skills/prune-skill/SKILL.md   test_prune_skill_size.py
    claude/skills/session-manager/…      test_session_manager_skill_size.py
    claude/skills/handoff/SKILL.md       this module

⚠ THE FIRST VERSION OF THIS DOCSTRING SAID "Skill BODIES had none", AND SAID THE
GATE WAS SCOPED TO ONE BODY BECAUSE "a repo-wide ratchet would go red on
`check-clickup-addressed` the day it landed". Both were wrong, and a delta audit
measured it: two skill-body gates already existed, and 41,207 B is comfortably
UNDER this module's MAX_BYTES, so that justification never held at the constant
actually chosen. Only `claude/RULES.md` is genuinely always-on; the
browser-bridge one is a skill body too. The real reason for per-file gates is the
boring one -- bodies differ by an order of magnitude, so one shared constant
would be slack for most and a permanently-red gate for one, and `claude/RULES.md`
says a permanently-red gate is worse than none.

The reusable lesson, which is why this paragraph survives instead of being
deleted: the wrong version was a crisp, checkable, FALSE claim, and it got that
way by being SHARPENED from a vaguer one during a cleanup. Nobody re-measures a
sentence that reads like it was already checked.

WHAT THE CEILING PROTECTS -- AND WHAT IT MUST NOT DO
----------------------------------------------------
🔴 This gate must NEVER be satisfied by deleting an instruction. The skill's
prose is pinned verbatim by two lists -- `HANDOFF_SENTENCES` (in
`TestSkillDocsArePinned`, `scripts/tests/test_subsystem_touch.py`; every entry
asserts against THIS skill, though the enclosing class also covers another) and
`SKILL_PINS` (`scripts/tests/test_handoff_doc.py`, none of whose entries is in
step 4).

⚠ NO COUNTS HERE ON PURPOSE. `_pin_lists()` parses both modules and the
eviction playbook prints the live numbers. This paragraph carried "77" and "14",
measured the same day; by the end of that day they were 78 and 16, because the
round that wrote them added pins. A hand-written count of a list this module
edits is stale before the commit lands -- which is what the docstring says two
paragraphs down and did not do.

The comments in the first record that pinning only a
headline clause left the surrounding paragraph "deletable green" -- 1,513 chars
-> 100 with the suite passing, silently taking the doc's only mention of a tool
block, a fallback search and an UNFILED instruction with it.

MEASURED, so the next person sizing this up does not repeat the estimate: a full
eviction pass over step 4 on 2026-08-23 -- moving every dated measurement and
incident narrative into `reference/index-write.md` §7 while keeping every
imperative and every pinned phrase -- yielded **1,993 bytes out of 28,589**
(step 4, base -> head).

⚠ THOSE ARE BYTES, which is the unit this gate measures. Always say which.

🔴 AND HERE IS HOW NOT TO RECONCILE TWO NUMBERS. An audit reported that same
eviction as "1,890"; an earlier version of this paragraph explained the gap as
bytes-vs-characters and asserted characters run "~5% lower on this file". A
delta audit measured both halves and neither survived: the real spread is
**1.22%**, and the eviction in CHARACTERS is 2,002 -- HIGHER than the byte
figure, not lower. 1,890 was a different TREE (origin/main -> the previous tip,
which is 1,882 B / 1,890 chars), not a different unit.

The failure is `claude/RULES.md`'s "an EMPTY RESULT cannot distinguish two
mechanisms" wearing a numeric hat: two plausible mechanisms (unit, tree) explain
one discrepancy, the first was picked because it was already in mind, and a
confident reconciliation got written down. A theory that explains the gap is not
evidence for it. The cheap discriminating control was one `git cat-file -s`.

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
# 2026-08-23 (same day it was created): 48,000 -> 48,600 (+600).
#
# 🔴 WHAT WOULD NOT FIT, which is what this ledger is for: the operator retired
# step 5's y/N prompt ("it always prompts y/n, we can remove that, the answer is
# always y"). Removing a prompt COSTS bytes rather than saving them, because the
# prompt was load-bearing prose and what replaces it has to be written down --
# what now carries the protection (the four refusing statuses and the warnings
# above the diff, previously advisory because a human was reading), that the
# push is no longer branch-limited in practice, and a corrected account of the
# one `status=failed` arm where the commit DOES exist.
#
# 🔴 EVICTION WAS RUN FIRST, AND IS RECORDED BECAUSE IT WENT BADLY. Three trim
# passes over step 5 and its neighbours recovered a few hundred bytes and BROKE
# A PIN doing it (`Do NOT retry by re-running with --push` was shortened to
# `Do NOT retry with --push`; TestSkillAndModuleAgree caught it). That is the
# gate working -- and it is also the evidence that trimming imperative prose for
# bytes is how an instruction gets lost. The block evictions that did work went
# to reference/write-gate.md; what remained were sentences whose every clause
# was scope-bearing.
#
# +600 is one mean 🔴 rule of this file (measured 572 B), matching
# test_rules_size.py's precedent of modest bumps over its sizing formula. It is
# deliberately NOT sized to make the next round comfortable: the pressure is the
# point, and the answer to the round after this one is lever 1 in the playbook
# (move guidance into the tool), not another +600.
# 🔴 2026-08-24: 48,600 -> 24,000 (-24,600). RATCHETED DOWN, which this module
# has said from the start is the intended direction — and the first time the
# lever was structural rather than editorial.
#
# `/handoff` step 4 was ~26 KB of subsystem-index protocol living inside a skill
# about writing handoffs: its own tool, its own store, its own reference doc, its
# own ~72 pinned sentences. It moved WHOLE to `claude/skills/subsystem-index/`,
# which /handoff now invokes. The body went 47,143 -> 21,729 B.
#
# 🔴 THE COMBINED TOTAL BARELY MOVED (49,602 vs 47,143) and that is not the
# point. The saving is that the index protocol is no longer paid by every
# /handoff run — a skill body costs its bytes when it is INVOKED, and /handoff is
# invoked at the END of a session when the window is already tight, whereas the
# index protocol is needed only on the runs that actually reach a write.
#
# Sized as before, in units of a real edit: mean paragraph ~486 B, mean 🔴 rule
# ~572 B. 24,000 leaves ~2.3 KB over the post-extraction size — about four mean
# rules — which is a working margin without re-inviting the regrowth this gate
# exists to price. The eviction playbook's first lever is unchanged and is now
# the demonstrated one: move a coherent block to a home of its own.
MAX_BYTES = 24_000

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


def _pin_lists() -> dict[str, int]:
    """How many verbatim phrases of this skill each pin list holds, RIGHT NOW.

    Derived by parsing the two modules rather than restated, for the reason this
    module's own docstring gives about hand-maintained numbers -- and because it
    already happened here: the playbook rendered "~70 sentences ... and ~12 more"
    while the docstring said 77 and 14, so the only copy a maintainer ever SEES
    (the playbook renders on failure; the docstring does not) carried the stale
    pair. A derived count cannot drift.
    """
    import ast

    sources = {
        "HANDOFF_SENTENCES": REPO_ROOT / "scripts/tests/test_subsystem_touch.py",
        "SKILL_PINS": REPO_ROOT / "scripts/tests/test_handoff_doc.py",
    }
    counts: dict[str, int] = {}
    for name, path in sources.items():
        if not path.is_file():
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            # 🔴 isinstance FIRST, not `getattr(node, "targets", …)`. `ast.Delete`
            # also carries `targets` and has no `.value`, so a `del <name>`
            # anywhere in either module would crash this helper -- and it renders
            # inside a FAILURE message, where a traceback replaces the playbook
            # exactly when someone needs it.
            if isinstance(node, ast.Assign):
                targets = node.targets
            elif isinstance(node, ast.AnnAssign):
                targets = [node.target]
            else:
                continue
            if any(
                isinstance(tgt, ast.Name) and tgt.id == name for tgt in targets
            ) and isinstance(node.value, ast.List):
                counts[name] = len(node.value.elts)
    return counts


def _pin_counts() -> str:
    counts = _pin_lists()
    if not counts:
        return (
            "The skill's prose is pinned verbatim by HANDOFF_SENTENCES "
            "(scripts/tests/test_subsystem_touch.py) and SKILL_PINS "
            "(scripts/tests/test_handoff_doc.py) -- neither could be parsed "
            "from here, so re-count them by hand before editing."
        )
    parts = ", ".join(f"{n} in {k}" for k, n in sorted(counts.items()))
    return (
        f"The skill's prose is pinned verbatim, phrase by phrase: {parts}. "
        f"Deleting any one of them is a test failure, not a silent loss."
    )


def _eviction_playbook() -> str:
    return f"""
  How to fix -- do NOT delete or narrow an instruction to make this pass:

    🔴 {_pin_counts()} A green suite does NOT mean the skill survived an edit:
       those pins catch DELETION of a named instruction and DRIFT in its
       wording, not a rewrite that keeps the words and guts the reasoning
       around them. Read the step cold after editing it.

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

    ⚠ MEASURED: a full eviction pass over step 4 (2026-08-23) yielded 1,993
    BYTES out of 28,589 -- and three further trim passes the same day, under
    real budget pressure, yielded a few hundred more and BROKE A PIN doing it.
    If you are here again, prose eviction is close to exhausted and trimming is
    how you lose an instruction. The remaining levers, in order:

      1. move guidance INTO the tool -- subsystem_touch.py already prints
         ROUTE OUT / WRONG WINDOW? / NO PATH FOOTPRINT? / SKILL HOMES / RECOVER
         at the moment each applies, which costs nothing on the runs where it
         does not fire, unlike a paragraph paid on every run;
      2. demote a whole coherent block to reference/ with a pointer;
      3. raise MAX_BYTES, saying in the commit message which instruction could
         not be expressed in the budget. This is legitimate and has been done --
         see the ledger on the constant -- but it is third, not first.

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
    assert "write-gate" in str(ceiling.value)

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
    test FAILS.

    ⚠ WHAT THIS DOES AND DOES NOT PIN, corrected after a delta audit measured it.
    An earlier version of this docstring claimed "lower MIN_HEADROOM_BYTES by one
    byte and this goes red". FALSE: 899, 500, 100 and 1 all leave the suite
    green, because the fixture is DERIVED from the constant and therefore moves
    with it. What is pinned is the comparison's SHAPE -- measured red for
    `headroom >= 0` as a literal, for `>=` weakened to `>`, and for an off-by-one
    in `headroom`. The one value it does catch is 0, and only via the guard
    below.

    That is the right trade, not a gap to close: a test that pinned the constant
    to 900 would false-fail every legitimate ratchet, and a ratchet is the
    intended direction of travel here. But the sentence has to say so --
    `claude/RULES.md`: "a guard's DESCRIPTION claims COVERAGE."
    """
    in_band = tmp_path / "in-band.md"
    in_band.write_bytes(b"x" * (MAX_BYTES - MIN_HEADROOM_BYTES + 1))
    monkeypatch.setattr("test_handoff_skill_size.SKILL_MD", in_band)

    # 🔴 THE BAND MUST EXIST, asserted HERE with its own message. At
    # MIN_HEADROOM_BYTES = 0 the fixture above is MAX_BYTES + 1, i.e. over the
    # CEILING -- so the call below would raise, this test would fail, and it
    # would fail for the ceiling's reason with the ceiling's message. That is
    # `claude/RULES.md`'s "a mutant that removes a guard TOGETHER WITH ITS
    # ENCLOSING CONDITION ... dies for the wrong reason", and a delta audit
    # found it here after the first fix. Now a zero floor is reported as what it
    # is: a warning band nothing can enter.
    assert MIN_HEADROOM_BYTES > 0, (
        "MIN_HEADROOM_BYTES is 0, so the warning band is EMPTY and "
        "test_handoff_skill_keeps_working_headroom can never fire before the "
        "ceiling does -- the early-warning half of this gate is inert."
    )
    assert len(in_band.read_bytes()) <= MAX_BYTES, (
        "the in-band fixture is over the CEILING, so the assertion below would "
        "fire for the ceiling's reason and prove nothing about the floor"
    )

    # The ceiling is NOT breached -- this is the half that makes the assertion
    # below a statement about MIN_HEADROOM_BYTES rather than about MAX_BYTES.
    test_handoff_skill_under_hard_ceiling()

    with pytest.raises(AssertionError) as headroom:
        test_handoff_skill_keeps_working_headroom()
    msg = str(headroom.value)
    assert "RECLAIM:   1 bytes" in msg, msg
    assert "write-gate" in msg

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
    left every test GREEN while granting the real file several hundred bytes of
    extra allowance -- this file's byte and character counts differ by ~1.2%,
    and the exact figure moves with every edit, so it is not written down here.

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
