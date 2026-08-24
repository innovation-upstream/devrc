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

So these pin whole normalised sentences and one whole normalised table row, not
keywords. A reword SHOULD fail here; the fix is to update the constant below in the
same commit, which is exactly the moment someone should notice they are about to
create a sixth phrasing.

🔴 WHAT THIS MODULE DOES **NOT** ENFORCE -- read before trusting it as coverage
------------------------------------------------------------------------------
An audit of this PR found six ways to restate the predicate that stay green, and a
guard that reads as wider than it is stops anyone looking. Precisely:

1. **It pins ONE sentence, not the CONCEPT.** A semantically identical restatement
   in different words is invisible. There is no known mechanical fix for that; the
   prose instruction in task-authoring.md ("add no further phrasings") is the only
   control, and it is a convention, not a gate.
2. **It cannot see files outside SEARCH_ROOTS/SEARCH_SUFFIXES** -- `claudedocs/`,
   `nix/`, `githooks/`, `cmd/`, and (inside the roots) extensionless executables,
   `.html`, `.json`, `.txt`, `.toml`. Repo-root `CLAUDE.md` WAS invisible and is now
   explicitly included, because it is the highest-traffic surface for exactly this
   restatement.
3. **`claude/RULES.md` is deliberately exempt** -- it restates the predicate on
   purpose (see task-authoring.md question 1). Nothing here guards RULES.md's own
   wording; that clause remains unguarded, which is recorded rather than fixed.
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

# 🔴 The WHOLE normalised out-of-scope row, not a prefix of it.
#
# A prefix assertion is satisfied by a row that states the condition and then
# contradicts itself in its own tail ("...only if you can name its closing
# condition; otherwise file a task instead") -- an audit mutant proved exactly that
# passed when this pinned only the opening clause. Pinning the whole row is what
# makes the guard structural rather than spelled. A cosmetic edit to this row will
# fail here; update the constant in the same commit.
HOOKS_ROW = (
    "| Out of scope | **No** — semantic, produces no `PermissionRequest`. "
    "File a task **only if you can name its closing condition**; with none, say so "
    "in your reply instead of creating one "
    "(definition: `~/.claude/skills/clawgate/flows/task-authoring.md`, question 1 — "
    "the single source; do not restate it here). Note the criteria-less-create "
    "denial comes from a devrc PreToolUse hook, `clawgate-task-interview-guard.py`, "
    "NOT this one; and the criteria table in SKILL.md governs the final status on "
    "the LOCAL pickup path, which may set `complete` — it is the DISPATCHED devpod "
    "route that cannot, `taskstatus.go:79-81`. |"
)

# The unconditional form that must NOT come back anywhere in the clawgate skill.
BANNED_UNCONDITIONAL = "produces no `PermissionRequest`. File a task instead"

SEARCH_ROOTS = (REPO_ROOT / "claude", REPO_ROOT / "scripts")
SEARCH_SUFFIXES = {".md", ".py", ".mjs", ".js", ".sh"}

# Repo-root CLAUDE.md is not under either root but IS always-loaded project context
# -- the single highest-traffic place a sixth phrasing would land. An audit mutant
# that restated the predicate there survived until this was added.
EXTRA_FILES = (REPO_ROOT / "CLAUDE.md",)


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
    candidates = [
        p
        for root in SEARCH_ROOTS
        for p in root.rglob("*")
        if p.is_file() and p.suffix in SEARCH_SUFFIXES
    ]
    candidates += [p for p in EXTRA_FILES if p.is_file()]
    for p in candidates:
        if p.resolve() == SELF:
            continue
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


def test_the_definition_sentence_occurs_exactly_once_in_the_scanned_corpus():
    """The single-source claim, asserted as a RELATIONSHIP.

    Fails if the set GROWS (someone restated the predicate elsewhere) or SHRINKS
    (the definition was deleted) -- both are real regressions.

    🔴 Counts OCCURRENCES, not files. `x in text` is membership, so a SECOND copy
    inside the canonical file itself passed green while this test's own name said
    "exactly once" -- found by audit. The name and the assertion now agree.

    Scope is the scanned corpus only; see the module docstring for what that
    structurally cannot see.
    """
    hits = sorted(
        f"{p.relative_to(REPO_ROOT).as_posix()} (x{text.count(DEFINITION_MARK)})"
        for p, text in _corpus().items()
        if text.count(DEFINITION_MARK) > 0
    )
    assert hits == [f"{CANONICAL.relative_to(REPO_ROOT).as_posix()} (x1)"], (
        f"\n\nThe closing-condition predicate must be DEFINED in exactly one place.\n"
        f"  expected: [{CANONICAL.relative_to(REPO_ROOT).as_posix()}]\n"
        f"  found:    {hits}\n"
        f"A predicate open-coded at N sites is typically wrong at N-1 of them.\n"
        f"Point at the definition instead of restating it."
    )


def test_the_whole_out_of_scope_row_matches_byte_for_byte():
    """Pins the ENTIRE normalised row, not its opening clause.

    A prefix assertion is satisfied by a row that states the condition and then
    contradicts itself in its own tail -- "...only if you can name its closing
    condition; otherwise file a task instead" passed the prefix version. Found by
    audit. Pinning the whole row is what makes this structural rather than spelled.
    """
    text = _normalise(HOOKS_REF.read_text(encoding="utf-8"))
    assert _normalise(HOOKS_ROW) in text, (
        f"\n\n{HOOKS_REF.relative_to(REPO_ROOT)}'s out-of-scope row no longer matches "
        f"the pinned text.\n\n  expected (normalised):\n    {_normalise(HOOKS_ROW)}\n\n"
        f"If you edited that row deliberately, update HOOKS_ROW in this module in the "
        f"SAME commit -- and check the edit did not reintroduce an unconditional "
        f"'file a task', which is the instruction that generates the leak."
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
