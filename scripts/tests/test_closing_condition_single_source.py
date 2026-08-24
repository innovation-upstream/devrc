"""The closing-condition predicate must have exactly ONE definition.

WHY THIS EXISTS
---------------
`claude/RULES.md` requires that a filed task name the closing condition that ends
it. The clawgate hook enforces a `## Acceptance criteria` heading. The authoring
flow asks "what does DONE look like". A re-audit of devrc #772 found the SAME
predicate spelled five different ways across the tree, with none of the other four
updated when the rule landed -- and RULES.md's own "One rule, one place" bullet
warns that a predicate open-coded at N sites is typically wrong at N-1 of them.

The consolidation deliberately does NOT rename `## Acceptance criteria`: that
heading is the ENFORCED artifact (`scripts/claude-hooks/clawgate-task-interview-guard.py`
matches it literally) and every existing task body carries it. Renaming it would
break the gate and the corpus at once. What is single-sourced is the DEFINITION --
question 1 of the authoring flow -- with every other site pointing at it.

WHY THE ASSERTIONS LOOK BRITTLE
-------------------------------
The artifact under test is PROSE. RULES.md: "when the artifact under test IS prose,
a guard on WORDS is walkable by REWORDING -- pin the WHOLE normalised string. A
cosmetic reword then fails the test -- pay it, for a machine-readable claim."

So these pin whole normalised sentences, not keywords. A reword SHOULD fail here;
the fix is to update the constant below in the same commit, which is exactly the
moment someone should notice they are about to create a sixth phrasing.
"""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# The ONE place the predicate is defined. Everything else points here.
CANONICAL = REPO_ROOT / "claude" / "skills" / "clawgate" / "flows" / "task-authoring.md"
HOOKS_REF = REPO_ROOT / "claude" / "skills" / "clawgate" / "reference" / "hooks.md"

# The distinctive sentence that marks the definition site. Must occur exactly once
# in the whole tree -- that count IS the single-source claim.
DEFINITION_MARK = "they are one thing under two names, not two requirements"

# The conditional form the out-of-scope row must carry. The row previously read
# "File a task instead" unconditionally, which is verbatim the instruction that
# generated the leak the rule exists to stop.
HOOKS_CONDITIONAL = "File a task **only if you can name its closing condition**"

# The unconditional form that must NOT come back anywhere in the clawgate skill.
BANNED_UNCONDITIONAL = "produces no `PermissionRequest`. File a task instead"

SEARCH_ROOTS = (REPO_ROOT / "claude", REPO_ROOT / "scripts")
SEARCH_SUFFIXES = {".md", ".py", ".mjs", ".js", ".sh"}


def _normalise(text: str) -> str:
    """Collapse whitespace so a line-wrap is not a false failure."""
    return re.sub(r"\s+", " ", text)


SELF = Path(__file__).resolve()


def _corpus() -> dict[Path, str]:
    """Every doc/script that could restate the predicate.

    🔴 THIS MODULE EXCLUDES ITSELF. It necessarily quotes both the canonical
    sentence and the banned one as constants, so without this it matches its own
    source and reports itself as the violation -- a guard failing on its own
    declaration rather than on the thing it guards. Verified: with the exclusion
    removed, both relationship tests fail naming this file.
    """
    out = {}
    for root in SEARCH_ROOTS:
        for p in root.rglob("*"):
            if p.is_file() and p.suffix in SEARCH_SUFFIXES and p.resolve() != SELF:
                try:
                    out[p] = _normalise(p.read_text(encoding="utf-8"))
                except (UnicodeDecodeError, OSError):
                    continue
    return out


def test_the_corpus_is_non_empty():
    """Positive control: a zero-file corpus would make every test below vacuous."""
    corpus = _corpus()
    assert len(corpus) > 50, (
        f"only {len(corpus)} files collected -- the search roots are probably wrong, "
        "which would make every assertion in this module pass vacuously"
    )


def test_the_definition_exists_at_the_canonical_site():
    assert CANONICAL.is_file(), f"canonical definition file is missing: {CANONICAL}"
    assert DEFINITION_MARK in _normalise(CANONICAL.read_text(encoding="utf-8")), (
        f"\n\nThe closing-condition definition is gone from its canonical home.\n"
        f"  expected in: {CANONICAL.relative_to(REPO_ROOT)}\n"
        f"  the sentence: {DEFINITION_MARK!r}\n"
        f"If you reworded it, update DEFINITION_MARK in this module in the SAME commit."
    )


def test_the_definition_appears_exactly_once_in_the_tree():
    """The single-source claim, asserted as a RELATIONSHIP.

    Fails if the set GROWS (someone restated the predicate elsewhere) or SHRINKS
    (the definition was deleted) -- both are real regressions.
    """
    hits = sorted(
        p.relative_to(REPO_ROOT).as_posix()
        for p, text in _corpus().items()
        if DEFINITION_MARK in text
    )
    assert hits == [CANONICAL.relative_to(REPO_ROOT).as_posix()], (
        f"\n\nThe closing-condition predicate must be DEFINED in exactly one place.\n"
        f"  expected: [{CANONICAL.relative_to(REPO_ROOT).as_posix()}]\n"
        f"  found:    {hits}\n"
        f"A predicate open-coded at N sites is typically wrong at N-1 of them.\n"
        f"Point at the definition instead of restating it."
    )


def test_the_out_of_scope_row_is_conditional():
    text = _normalise(HOOKS_REF.read_text(encoding="utf-8"))
    assert _normalise(HOOKS_CONDITIONAL) in text, (
        f"\n\n{HOOKS_REF.relative_to(REPO_ROOT)} no longer states the out-of-scope "
        f"case conditionally.\n  expected: {HOOKS_CONDITIONAL!r}\n"
        f"An unconditional 'file a task' is the instruction that generates the leak."
    )


def test_the_unconditional_form_has_not_come_back():
    offenders = sorted(
        p.relative_to(REPO_ROOT).as_posix()
        for p, text in _corpus().items()
        if _normalise(BANNED_UNCONDITIONAL) in text
    )
    assert offenders == [], (
        f"\n\nThe unconditional 'File a task instead' is back in: {offenders}\n"
        f"Out-of-scope work is only filed when its closing condition can be named."
    )
