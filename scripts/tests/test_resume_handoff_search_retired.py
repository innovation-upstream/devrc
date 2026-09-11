"""`/resume` must NOT call `scripts/lib/handoff_search.py` -- retired on a measurement.

WHY THIS EXISTS
---------------
This file replaces `test_resume_handoff_search_wiring.py`, which pinned the exact
OPPOSITE claim: that the `/resume` skill calls the corpus search. That guard was
correct for what it measured. The step was wired in by `#1295`, MOVED into step 4
by `#1332` because it did not fire inside step 3's prose, and given
`--exclude-slug` by `#1399`. Every one of those changes did what it set out to do:

  adoption   2 of 11 (18%)  ->  20 of 22 (91%)  ->  16 of 16 ran it with the flag
  self-hits  23 of 60 slots (38%), #1 hit 13/20  ->  0 of 48 slots, #1 hit 0 of 16

What never followed was YIELD. Measured 2026-09-11 across the 16 `/resume`
sessions that queried the corpus after `#1399`: **0 of 16** opened, mentioned or
acted on a hit document. Every one went straight from the tool result to
`claim-work` or `gh pr view`. Pre-fix the same number was 1 of 20.

🔴 THE CAUSE IS RETRIEVAL RECALL, NOT AN EMPTY CORPUS -- and that distinction is
why this is a RETIREMENT and not a ranker-tuning task. 44 of those 48 hits were
`[gotcha#0]`, the same first section of a Gotchas block; 29 distinct documents
filled 48 slots, two of them appearing 5x each across unrelated repos; ranks
clustered 0.97-1.75, i.e. no separation between a good match and a bad one. In
the single case where both recall surfaces were asked the same question -- a
store-api fsync stall in the Tekton tier -- `cairn search 'fsync'` returned the
right document and `handoff_search` ranked that SAME document 11th, unreachable
at the prescribed `--limit 3`. The corpus held the answer; the ranker could not
surface it, and `cairn recall` (step 4's surviving command) already did.

The handoff doc's rank 1 pre-committed to this outcome in writing: "if yield
stays ~1/20 with foreign hits filling every slot ... the right response is to
stop paying for the step rather than to tune the ranker again." It did, so it is.

WHAT THIS GUARD IS FOR
----------------------
Not hygiene. A retirement decided on a measurement is exactly the kind of thing a
later session re-adds from a good-faith reading of the machinery ("the index is
live and nothing calls it -- wire it up"), which is the true sentence that
motivated the ORIGINAL guard. So this fails the day the command returns to the
skill, and its message hands over the measurement that has to be redone first.

⚠ WHAT IT DOES NOT CLAIM. It does not assert the tool is gone -- it is not, and
`scripts/lib/handoff_search.py` remains fully supported for ad-hoc use. It pins
one thing: that `/resume` does not spend ~4 KB of every run on it. It is also
blind to a rewiring under a different spelling (a wrapper script, an alias); the
measurement, not this file, is the reason the step is out.

This module lives in `scripts/tests`, which is in `HERMETIC_TARGETS` in
`scripts/run-tests.sh`, so it runs in `nix build .#checks.x86_64-linux.pytests`.
It opens no database, reaches no network and shells out to nothing.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
RESUME_SKILL = REPO_ROOT / "claude" / "skills" / "resume" / "SKILL.md"
SEARCH_MODULE = REPO_ROOT / "scripts" / "lib" / "handoff_search.py"

# The retirement note that must stay in step 4, so the next reader finds the
# measurement rather than the absence. Pinned as a sentinel, not whole prose:
# the note is long and its wording is not the contract -- its PRESENCE is.
RETIREMENT_SENTINEL = "WAS RETIRED ON A MEASUREMENT"

# A "call" is an invocation, not a mention. The retirement note names the module
# (deliberately -- it tells you where the tool still lives), so a bare
# `handoff_search` substring test would fail on the note itself and be unfixable
# without deleting the explanation. What must be absent is a RUNNABLE command.
INVOCATION = re.compile(
    r"(?:python3?|\bsh\b|\bbash\b)[^\n]*handoff_search(?:\.py)?\b"
    r"|(?<![\w/.-])handoff_search\.py\s+--",
)


def _skill_text() -> str:
    assert RESUME_SKILL.is_file(), f"the /resume skill is missing: {RESUME_SKILL}"
    return RESUME_SKILL.read_text(encoding="utf-8")


def _fenced_blocks(text: str) -> list[str]:
    """Every ```-fenced block body in the skill.

    🔴 THE INDENT IS LOAD-BEARING. Every fence in this skill sits INSIDE a
    numbered list item and is indented three spaces, so a `^```` anchored at
    column 0 matches ZERO blocks -- and a needle run over zero blocks passes
    whatever the skill says. That is not hypothetical: the first draft of this
    guard was written that way and SURVIVED the mutation that re-added the
    command. `claude/RULES.md` -> "a test you have not watched FAIL proves
    nothing". `test_the_fence_scanner_actually_finds_this_skills_fences` pins
    the count so this cannot silently regress to zero again.
    """
    return re.findall(
        r"^[ \t]*```[^\n]*\n(.*?)^[ \t]*```", text, re.MULTILINE | re.DOTALL
    )


def test_the_resume_skill_is_where_it_is_expected():
    """A moved skill must not turn every assertion below into a vacuous pass."""
    assert RESUME_SKILL.is_file(), f"expected the skill at {RESUME_SKILL}"
    assert _skill_text().strip(), "the /resume skill is empty"


def test_the_fence_scanner_actually_finds_this_skills_fences():
    """Positive control for the SCANNER, not just the needle.

    The retirement check below is "no invocation among the fenced blocks". If
    the scanner returns an empty list it passes vacuously, forever, no matter
    what the skill prescribes -- which is exactly what the first draft did.
    So pin that it still sees real fences, and that they are the RUNNABLE ones.
    """
    blocks = _fenced_blocks(_skill_text())
    assert blocks, (
        "the fence scanner found NO fenced blocks in the /resume skill. Every "
        "assertion scoped to fences is now vacuous. The likeliest cause is the "
        "fences being indented inside list items while the regex anchors at "
        "column 0 -- see _fenced_blocks."
    )
    joined = "\n".join(blocks)
    assert "resume-state.sh" in joined, (
        "the scanner found fences but not step 2's reconciler command, so it is "
        "not reading the blocks an agent actually executes"
    )


def test_no_fenced_command_in_the_resume_skill_invokes_the_corpus_search():
    """The retirement, pinned where it is load-bearing: the RUNNABLE commands.

    Scoped to fenced blocks because that is what an agent executes. Prose may
    name the module -- the retirement note does, on purpose.
    """
    offenders = [
        block for block in _fenced_blocks(_skill_text()) if INVOCATION.search(block)
    ]
    assert not offenders, (
        "`/resume` invokes handoff_search again in a fenced command block:\n\n"
        + "\n---\n".join(offenders)
        + "\n\nThat step was RETIRED on a measurement, not dropped by accident: "
        "16 of 16 sessions ran it and 0 of 16 acted on a hit (yield 0/16; "
        "pre-fix 1/20), because 44 of 48 hits were the same `[gotcha#0]` "
        "section shape and the one on-topic document a session needed ranked "
        "11th -- unreachable at --limit 3 -- while `cairn search` returned it. "
        "If you are re-wiring it, REDO THE YIELD MEASUREMENT FIRST: read the "
        "turns after each invocation and count how many opened a hit. See "
        "claudedocs/handoff-handoff-search-index.md."
    )


def test_the_retirement_note_survives_in_the_skill():
    """Absence without the reason is indistinguishable from an accidental delete.

    This is the half that stops the step being re-added in good faith: the note
    is what a reader finds when they notice the index has no consumer.
    """
    text = _skill_text()
    assert RETIREMENT_SENTINEL in text, (
        "the step-4 retirement note is gone from claude/skills/resume/SKILL.md. "
        "Without it the next reader sees a live index that nothing calls -- the "
        "exact true observation that motivated wiring it in the first place -- "
        "and re-adds the step. Restore the note, or delete this guard too and "
        "say in the commit message where the measurement now lives."
    )


def test_the_note_points_at_a_handoff_doc_that_exists():
    """A retirement whose evidence cannot be found is an assertion, not a record."""
    text = _skill_text()
    referenced = re.findall(r"claudedocs/handoff-[\w.-]+\.md", text)
    assert "claudedocs/handoff-handoff-search-index.md" in referenced, (
        "step 4's retirement note must cite the handoff doc holding the "
        "measurement; nothing else records why the step is out."
    )
    doc = REPO_ROOT / "claudedocs" / "handoff-handoff-search-index.md"
    assert doc.is_file(), f"the cited evidence doc is missing: {doc}"


def test_the_tool_itself_is_still_present_and_supported():
    """The retirement is of the STEP, not of the tool -- pin that they differ.

    If a later change deletes the module, this guard's premise (ad-hoc use is
    still available) is false and the note in the skill is misleading.
    """
    assert SEARCH_MODULE.is_file(), (
        f"{SEARCH_MODULE} is gone, but claude/skills/resume/SKILL.md still tells "
        "the reader the tool remains available for ad-hoc use. Either restore it "
        "or correct that sentence."
    )


@pytest.mark.parametrize(
    "block",
    [
        'python3 ~/workspace/devrc/scripts/lib/handoff_search.py --offline --query "x"',
        "python3 scripts/lib/handoff_search.py --limit 3",
        "handoff_search.py --offline --query 'x'",
    ],
)
def test_the_needle_can_actually_see_a_reinstated_command(block):
    """Positive control: a guard that cannot go red is not a guard.

    `claude/RULES.md` -> "validate the INSTRUMENT before you read its verdict".
    These are the shapes a re-wiring would realistically take; each MUST match.
    """
    assert INVOCATION.search(block), f"the needle missed a real invocation: {block!r}"


def test_the_needle_does_not_fire_on_the_retirement_note_itself():
    """Negative control, and the reason the needle is not a bare substring.

    The note names the module on purpose. If this ever fails, the guard has
    become unsatisfiable without deleting the explanation it exists to protect.
    """
    note = (
        "The tool still exists for ad-hoc use "
        "(`scripts/lib/handoff_search.py`); what was retired is paying ~4 KB "
        "of every resume for it."
    )
    assert not INVOCATION.search(note), (
        "the needle matches the retirement note's own prose, so the guard cannot "
        "pass while the explanation stands"
    )
