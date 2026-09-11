"""The `cairn` skill must not deny a verb the `cairn` CLI actually has.

WHY THIS EXISTS
---------------
From devrc#1254 (`34d00d90`, which added `PUT … If-None-Match: *` and the
`cairn create` subcommand) until 2026-09-11, `claude/skills/cairn/SKILL.md`
used to say **"There is no CREATE route, and the failure does not say so."**
The CLI had shipped `cairn create` the whole time.

🔴 THE REASON IT SURVIVED IS THE INTERESTING PART: the block's CONCLUSION was
still true. A scope's FIRST entry genuinely cannot be made through the API — but
not for the reason given. The real mechanism is that the index is built by
WALKING THE STORE ROOT narrowed by the caller's allowlist
(`subsystem_recall.load_store`), so a scope with no directory on the pod's disk
resolves to nothing and the write is refused at the index. A reader checking the
conclusion against reality found it held and moved on; nothing checked the
premise. **A doc claim that is right by accident reads exactly like one that is
right.**

MEASURED 2026-09-11, with the control that makes the reading mean something:

    cairn create --scope civitai-app-requests --ref app-requests --file F
        -> rc 6  [not-found] — not found          (absent + non-allowlisted)
    cairn create --scope civitai --ref health --file F
        -> rc 9  [already-exists]                 (allowlisted, present, exists)

Two different codes, so the `not-found` is a fact about that scope rather than a
probe wired to nothing. Both wrote nothing.

WHAT THIS GUARD ASSERTS, AND WHY IT IS NOT A WORD MATCH
-------------------------------------------------------
This guard pins STATE, not words: a two-way ledger of every subcommand
`scripts/cairn` declares. A verb the CLI gains with no ledger row fails; a
ledger row naming no CLI verb fails. Rows marked ``NAMED`` must appear verbatim
in the skill body, which is what makes "the skill silently omits a verb it also
makes claims about" impossible to reintroduce.

🔴 **IT IS HALF THE GUARD, AND AN EARLIER VERSION OF THIS DOCSTRING ARGUED IT
WAS THE WHOLE OF IT — WRONGLY.** It said a guard on the refuted SENTENCE was
rejected because it "would trip on this file's own retraction". Two things were
wrong with that. First, the repo already owns a controlled instrument for the
sentence half — `_unmarked_retractions` in `test_subsystem_store_api.py`, which
normalises case, emphasis and LINE WRAPS, exempts a quote only when a retraction
marker sits within 60 characters, and carries both a negative and a positive
control. Second, the reason was self-serving: a hand sweep run while fixing
`SKILL.md` reported the repo CLEAN, and it was case-sensitive — there were FOUR
live copies of the sentence in other tracked files, one of them straddling a
newline inside a docstring where no line-based grep could ever see it.

So both guards now run, because they catch different failures: this ledger
catches FORWARD drift (a new verb the skill never mentions), and the needle
catches the SENTENCE being re-asserted anywhere in the tree. The docstrings in
this file are phrased to carry a retraction marker beside every quotation of the
refuted claim, which is the discipline that keeps them exempt — cheap, and not
the impossibility the earlier wording claimed.

``DELEGATED`` is not a loophole — it carries its reason, and the reason is
load-bearing: the skill says "Writes are not this skill's" and routes writers to
`subsystem-index`, so it owes no description of `append`/`put`. `create` is NOT
delegated, because the skill makes an explicit CLAIM about creation; a file that
claims a scope's first entry cannot be created must name the verb that claim is
about.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
CAIRN_CLI = REPO / "scripts" / "cairn"
SKILL = REPO / "claude" / "skills" / "cairn" / "SKILL.md"

NAMED = "NAMED"
DELEGATED = "DELEGATED"

# 🔴 TWO-WAY. Every row must name a real subcommand, and every subcommand must
# have a row. Adding a verb to `scripts/cairn` without deciding whether the
# skill has to mention it is exactly the drift this file exists to stop.
VERB_LEDGER: dict[str, tuple[str, str]] = {
    "sync": (NAMED, "the router's verb table — a reader picks it from there"),
    "recall": (NAMED, "the router's verb table"),
    "search": (NAMED, "the router's verb table"),
    "validate": (NAMED, "named, and carries its own 🔴 not-the-write-check caveat"),
    "ls-entries": (NAMED, "the router's verb table"),
    "doctor": (NAMED, "the 'Start here, always' block"),
    "append": (
        DELEGATED,
        "a write verb; the skill routes writers to `subsystem-index` and makes "
        "no claim about append's behaviour",
    ),
    "put": (
        DELEGATED,
        "a write verb; delegated to `subsystem-index` for the same reason as append",
    ),
    "create": (
        NAMED,
        "🔴 NOT delegated. The skill makes an explicit claim about creating a "
        "scope's first entry, so it must name the verb that claim is about — "
        "the omission this test was written for",
    ),
}


def _declared_verbs() -> set[str]:
    """Every `sub.add_parser("<name>")` literal in the CLI, via AST not grep.

    A regex over the source would also match the string inside a comment or a
    docstring — including this module's own prose if it were ever vendored — and
    would miss a call spread across lines. The parse is the structural read.
    """
    tree = ast.parse(CAIRN_CLI.read_text(encoding="utf-8"))
    verbs: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute) or func.attr != "add_parser":
            continue
        if not node.args:
            continue
        first = node.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            verbs.add(first.value)
    return verbs


def test_the_verb_extraction_found_something() -> None:
    """POSITIVE CONTROL — a zero here would make every assertion below vacuous.

    `claude/RULES.md`: a reassuring zero from a check that could not see
    anything is indistinguishable from a clean result. If `scripts/cairn` is
    ever restructured so `add_parser` is no longer called on an attribute, this
    test is what says so, instead of the ledger silently passing over an empty
    set.
    """
    verbs = _declared_verbs()
    assert verbs, f"no subcommands parsed out of {CAIRN_CLI} — the extraction is broken"
    # Anchored on verbs whose removal would be a redesign, not a rename.
    assert {"sync", "recall", "doctor"} <= verbs, (
        f"the extraction found {sorted(verbs)}, which is missing verbs the skill's "
        f"own 'Start here' block documents — suspect the parse, not the CLI"
    )


def test_every_cli_verb_has_a_ledger_row() -> None:
    """A verb added to the CLI with no decision recorded about the skill."""
    missing = sorted(_declared_verbs() - VERB_LEDGER.keys())
    assert not missing, (
        f"`scripts/cairn` declares {missing} with no row in VERB_LEDGER.\n"
        f"Decide, in this file: does {SKILL.name} have to NAME it, or is it "
        f"DELEGATED to another skill? Write the reason in the row — a row "
        f"without one is how `create` stayed undocumented for 8 days."
    )


def test_every_ledger_row_names_a_real_verb() -> None:
    """The other direction — a row outliving the verb it describes."""
    stale = sorted(VERB_LEDGER.keys() - _declared_verbs())
    assert not stale, (
        f"VERB_LEDGER has rows for {stale}, which `scripts/cairn` no longer "
        f"declares. Drop the row AND whatever {SKILL.name} says about the verb."
    )


@pytest.mark.parametrize(
    "verb",
    sorted(v for v, (mode, _) in VERB_LEDGER.items() if mode == NAMED),
)
def test_a_named_verb_appears_in_the_skill_body(verb: str) -> None:
    """The skill must actually name each verb the ledger says it names.

    🔴 This is the assertion that would have caught the original defect: the
    skill said "There is no CREATE route" while `cairn create` shipped, and the
    word `create` appeared nowhere as a verb. It pins the verb NAME — state the
    CLI decides — so a reword of the surrounding prose cannot walk it.
    """
    body = SKILL.read_text(encoding="utf-8")
    _mode, reason = VERB_LEDGER[verb]
    assert f"cairn {verb}" in body or f"`{verb}`" in body, (
        f"{SKILL} never names the `{verb}` verb, but VERB_LEDGER marks it {NAMED} "
        f"because: {reason}"
    )
