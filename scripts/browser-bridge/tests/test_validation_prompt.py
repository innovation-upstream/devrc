"""Contract gate for `scripts/browser-bridge/reference/validation-prompt.md`.

WHY THIS EXISTS
---------------
The file is a SAFETY CONTRACT that prompts CITE instead of restating. That
inverts the usual failure mode. While every prompt carried its own copy of the
rails, dropping one rail cost you that one run; now a prompt says "follow the
standing contract", so a rail silently deleted or reworded out of this file is
dropped from EVERY run that cites it, and the prompt still reads complete.

Measured motivation (2026-08-16..19, `activity.events`, `source='opencode'`,
`kind='prompt'`): 17 of 114 prompts drove the browser and the heavy ones ran
3.0-5.6 KB each, nearly all of it retyped scaffolding. The copies had already
drifted apart from one another -- which is the same defect, one generation
earlier.

WHAT IS PINNED, AND WHY IT IS PINNED THIS WAY
---------------------------------------------
`claude/RULES.md`: "When the artifact under test IS prose, a guard on WORDS is
walkable by REWORDING -- pin the WHOLE normalised string." So the rail ledger
below is an ORDERED list of each numbered rail's full bold lead, compared
exactly after whitespace normalisation. A cosmetic reword fails this test. That
cost is deliberate and it is the point: the rails are a machine-readable claim,
so changing one is a reviewed edit, not a typo.

Three separate things are checked, because they fail independently:

  1. THE ROUTE. A reference file nothing points at is a file the reader never
     reaches -- the #567 defect, where 47 sidecar routes pointed somewhere the
     reader never is. So SKILL.md's reference TABLE must name this file. Parsed
     out of the table, not grepped out of the document: a mention buried in
     prose is not a route.
  2. THE RAILS. All nine, in order, verbatim.
  3. THE SCOPE OF RAIL 6. Rail 6 forbids screenshots -- for a DELEGATED run,
     because a subagent sandbox commonly cannot read back from `/tmp/*` and
     `browser agent` is structurally blind. Read without its scoping caveat it
     contradicts SKILL.md's own ops table, which tells an OPERATOR to
     `screenshot` and then `Read` the `.png`. A contract that talks the reader
     out of the only op that can see is worse than no contract, so the caveat
     is pinned separately from the rail.

HARNESS DISCIPLINE
------------------
Every parser here asserts a non-empty, plausible-cardinality result before any
contract claim is made -- an empty parse would make each assertion below pass
vacuously. `test_*_control_*` are the negative controls: they run each checker
against a DOCTORED copy and assert it goes red, so a green run is evidence the
checkers can still observe something.

This module is part of the hermetic set (`scripts/run-tests.sh`), so it runs in
`nix build .#checks.x86_64-linux.pytests` -- the repo's real pre-merge gate.
"""
import re
from pathlib import Path

import pytest

BB = Path(__file__).resolve().parent.parent
SKILL_MD = BB / "SKILL.md"
DOC = BB / "reference" / "validation-prompt.md"

# The route SKILL.md must publish. Spelled as the reference table spells it.
ROUTE = "reference/validation-prompt.md"

# The nine standing rails, in order, as their full bold lead reads. Compared
# after whitespace normalisation only -- see the module docstring for why this
# is deliberately brittle to rewording.
RAILS = [
    "**ORIENT FIRST.**",
    "**YOUR OWN TAB.**",
    "**READ-ONLY BY DEFAULT.**",
    "**BLAST RADIUS IS THE NAMED SET.**",
    "**A FAILURE IS A FINDING -- REPORT AND STOP.**",
    "**INLINE READS ONLY, WHEN DELEGATING.**",
    "**A HIDDEN TAB IS A CONFOUND, NOT A RESULT.**",
    "**CLEAN UP.**",
    "**REPORT HONESTLY.**",
]

# Slots the copy-paste template must offer. A template missing a slot is a
# prompt that silently omits that dimension -- BUDGET and DO NOT TOUCH are the
# two that bound blast radius, and THE DISCRIMINATOR is the one that decides
# whether the report can settle anything.
TEMPLATE_SLOTS = [
    "TASK:",
    "SITE:",
    "INSTANCE:",
    "BUDGET:",
    "WRITES ALLOWED",
    "DO NOT TOUCH:",
    "OBSERVE",
    "THE DISCRIMINATOR",
    "REPORT",
]


def _norm(text: str) -> str:
    """Collapse whitespace and fold the en/em dashes to ASCII.

    The dash fold exists so the ledger above can be written in ASCII: the doc
    uses a real em dash, and a ledger entry that has to carry a non-ASCII
    codepoint is one bad copy-paste away from a false red that reads as a
    content failure. `grep` can render a character invisible -- RULES.md,
    "Shell & Tooling Gotchas" -- so the fold is done here, once, explicitly.
    """
    return re.sub(r"\s+", " ", text.replace("—", "--").replace("–", "-"))


def _doc_text() -> str:
    assert DOC.is_file(), (
        f"{DOC} not found -- every assertion in this module would be vacuous. "
        "If the contract moved, update DOC here AND the reference-table row in "
        f"{SKILL_MD}; a moved contract with a stale route is unreachable."
    )
    return DOC.read_text(encoding="utf-8")


def _reference_table_files(skill_text: str) -> list[str]:
    """The `file` column of SKILL.md's reference table.

    Deliberately parsed rather than grepped: the question this answers is "is
    the reader ROUTED here", and only a table row routes. A path mentioned in
    prose reads as a citation, not an index entry, and #567 is the incident
    where that distinction was worth 47 dead routes.
    """
    files: list[str] = []
    in_table = False
    for line in skill_text.splitlines():
        if line.startswith("| file | load it when"):
            in_table = True
            continue
        if in_table:
            if not line.startswith("|"):
                break
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if not cells or set(cells[0]) <= set("-: "):
                continue
            files.append(cells[0].strip("`"))
    return files


def _rail_leads(doc_text: str) -> list[str]:
    """The bold lead of each numbered rail in the standing-contract list."""
    body = doc_text.split("## The standing contract", 1)
    if len(body) < 2:
        return []
    section = body[1].split("\n## ", 1)[0]
    return [_norm(m) for m in re.findall(r"^\d+\.\s+(\*\*.+?\*\*)", section, re.M)]


# --------------------------------------------------------------------------
# Guard the guards: the parsers must fail loudly, never quietly return nothing.
# --------------------------------------------------------------------------

def test_reference_table_parser_finds_a_plausible_table():
    files = _reference_table_files(SKILL_MD.read_text(encoding="utf-8"))
    assert len(files) >= 8, (
        f"parsed only {len(files)} rows out of {SKILL_MD}'s reference table "
        f"({files}). The table has had 11+ rows since #562; this few means the "
        "parser lost the table (heading reworded? table moved?), and the route "
        "assertion below would pass or fail for the wrong reason."
    )
    assert all(f.endswith(".md") or f.endswith("/") or ">" in f for f in files), (
        f"reference-table file column holds a non-path cell: {files}"
    )


def test_rail_parser_finds_all_nine_rails():
    leads = _rail_leads(_doc_text())
    assert len(leads) == len(RAILS), (
        f"parsed {len(leads)} rails out of {DOC}, expected {len(RAILS)}: "
        f"{leads}. Either a rail was added/removed without updating RAILS "
        "here, or the numbered-list shape changed and the ledger assertion "
        "below has stopped observing anything."
    )


def test_control_rail_parser_goes_red_on_a_dropped_rail():
    """Negative control: prove the ledger check can observe a missing rail."""
    doctored = _doc_text().replace("8. **CLEAN UP.**", "8. **TIDY.**", 1)
    leads = _rail_leads(doctored)
    assert leads != [_norm(r) for r in RAILS], (
        "the rail ledger did not notice a renamed rail -- the checker is "
        "testing nothing. Fix _rail_leads before trusting any green here."
    )


def test_control_route_parser_goes_red_on_a_dropped_row():
    """Negative control: prove the route check can observe a deleted row."""
    doctored = "\n".join(
        ln for ln in SKILL_MD.read_text(encoding="utf-8").splitlines()
        if ROUTE not in ln
    )
    assert ROUTE not in _reference_table_files(doctored), (
        "the route parser still reports the row after it was deleted -- it is "
        "matching something other than the reference table."
    )


# --------------------------------------------------------------------------
# The contract itself.
# --------------------------------------------------------------------------

def test_skill_md_routes_the_reader_to_the_contract():
    files = _reference_table_files(SKILL_MD.read_text(encoding="utf-8"))
    assert ROUTE in files, (
        f"\n\n{ROUTE} is not in {SKILL_MD}'s reference table (rows: {files}).\n"
        "A reference file nothing routes to is a file the reader never reaches "
        "-- that is the #567 defect, not a documentation nit. Add a row to the "
        "'Reference files' table, and evict its bytes from elsewhere in "
        "SKILL.md: the size gate in tests/test_skill_size.py owns the ceiling."
    )


def test_every_standing_rail_is_present_and_in_order():
    leads = _rail_leads(_doc_text())
    expected = [_norm(r) for r in RAILS]
    assert leads == expected, (
        f"\n\nthe standing-contract rails in {DOC} no longer match the pinned "
        "ledger.\n"
        f"  in the file: {leads}\n"
        f"  pinned here: {expected}\n"
        "Prompts CITE this contract instead of restating it, so a rail dropped "
        "or reworded here is dropped from every run that cites it, with the "
        "prompt still reading complete. If the change is intended, update "
        "RAILS in this module IN THE SAME COMMIT -- that edit is the review."
    )


@pytest.mark.parametrize("slot", TEMPLATE_SLOTS)
def test_template_offers_every_slot(slot):
    fences = re.findall(r"```text\n(.*?)```", _doc_text(), re.S)
    assert fences, (
        f"no ```text fenced template found in {DOC} -- the copy-paste block is "
        "the whole deliverable; without it the rails have nothing to attach to."
    )
    template = "\n".join(fences)
    assert slot in template, (
        f"the copy-paste template in {DOC} no longer offers the {slot!r} slot. "
        "A dropped slot is a dimension every future prompt silently omits: "
        "BUDGET and DO NOT TOUCH bound blast radius, THE DISCRIMINATOR is what "
        "makes the report able to settle anything."
    )


def test_rail_six_keeps_its_delegation_scope():
    """Rail 6 must not read as a blanket screenshot ban.

    SKILL.md's ops table tells an OPERATOR that `screenshot` works on a
    background tab and to `Read` the `.png`. Rail 6 forbids screenshots for a
    DELEGATED run only. Stripped of that scope the contract contradicts the
    core doc and talks the reader out of the one op that can see -- so the
    caveat is pinned as its own claim, not left to survive as a side effect of
    the rail's wording.
    """
    text = _norm(_doc_text())
    assert "This rail is about DELEGATION." in text, (
        f"{DOC} rail 6 has lost its explicit delegation scope. Without it the "
        "contract reads as a blanket 'never screenshot', which contradicts "
        f"{SKILL_MD}'s screenshot row and removes the only op that can see."
    )
    assert "Read` the `.png`" in text, (
        f"{DOC} rail 6 no longer names the operator's correct path "
        "(`screenshot` then `Read` the `.png`). Naming the prohibition without "
        "naming the alternative is what makes readers over-apply it."
    )


def test_contract_does_not_restate_what_skill_md_owns():
    """The file's value is that it is SHORT enough to cite.

    It replaced 3-5.6 KB prompt preambles; a contract that grows into a second
    copy of SKILL.md regenerates the duplication it exists to remove. This is a
    soft ceiling with a lot of room -- it fails only on a wholesale paste.
    """
    size = len(DOC.read_bytes())
    assert size <= 8_192, (
        f"{DOC} is {size:,} bytes. It exists to be CITED in place of a 3-5.6 KB "
        "preamble; past ~8 KB it is cheaper to retype the rails than to load "
        "it. Move mechanism detail to the reference file that owns it "
        "(agent.md, spa-wake.md, frames-cdp.md) and leave a pointer."
    )
