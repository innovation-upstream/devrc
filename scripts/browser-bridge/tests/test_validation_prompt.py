"""Contract gate for `scripts/browser-bridge/reference/validation-prompt.md`.

WHY THIS EXISTS
---------------
The file is a SAFETY CONTRACT that prompts CITE instead of restating. That
inverts the usual failure mode. While every prompt carried its own copy of the
rails, dropping one rail cost you that one run; now a prompt says "follow the
standing contract", so a rail silently deleted or reworded out of this file is
dropped from EVERY run that cites it, and the prompt still reads complete.

Measured motivation (2026-08-16..19, `activity.events`, `source='opencode'`,
`kind='prompt'`): of 125 prompts, 8 named the bridge / the `browser` skill /
`browser agent`, and 7 of those 8 ran 3.0-5.6 KB -- nearly all of it retyped
scaffolding. The copies had already drifted apart from one another, which is the
same defect one generation earlier.

WHAT IS PINNED, AND WHY IT IS PINNED THIS WAY
---------------------------------------------
🔴 THE FIRST VERSION OF THIS MODULE PINNED ONLY EACH RAIL'S BOLD HEADLINE, and
an audit walked straight through it: rail 3's body was rewritten to "Feel free
to Connect / Follow / Message / Save, submit any form, change account or
notification settings, and log out when you are done" and the suite stayed
GREEN, 17 passed. So did inverting rail 2 (reuse the operator's tab) and rail 7
(`activate` to fix a hidden tab -- a 🔴 RULES.md screen-theft hazard). The
headline is the label; the rail is the body. `claude/RULES.md`: "When the
artifact under test IS prose, a guard on WORDS is walkable by REWORDING -- pin
the WHOLE normalised string."

So `RAILS` below holds each rail's ENTIRE normalised block, lead and body, and
they are compared exactly and in order. A cosmetic reword fails this test. That
cost is deliberate and it is the point: the rails are a machine-readable claim,
so changing one is a reviewed edit -- you must paste the new text here, and that
paste IS the review.

The same lesson applies to every other assertion in this module, so each one
slices the region it is about before asserting, rather than searching the whole
document for a substring. A document-wide `in` check is satisfied by the same
words appearing anywhere, including in a section added to satisfy it.

WHAT ELSE IS CHECKED, AND WHY EACH FAILS INDEPENDENTLY
------------------------------------------------------
  1. THE ROUTE, for EVERY reference topic. A file nothing points at is a file
     the reader never reaches -- the #567 defect, where 47 sidecar routes
     pointed somewhere the reader never is. Checked as a SET EQUALITY between
     `reference/*.md` and SKILL.md's reference table, so a 14th topic added
     later is covered without editing this module. Parsed out of the TABLE, not
     grepped out of the document: a mention in prose is not a route.
  2. THE ROUTE'S TRIGGER. A row whose "load it when" cell no longer describes
     this file routes nobody -- which is the actual #567 defect, not the row's
     mere existence.
  3. THE RAILS. All nine, in order, whole blocks.
  4. THE READER-CAPABILITY SPLIT. `browser agent` is given ONE typed tool with
     an 11-op browser-only surface and the agent def denies bash/read/edit/
     write/webfetch, enforced by a fail-closed runtime gate. It CANNOT open the
     path that carries the rails. A cited prompt therefore reaches it with ZERO
     rails while still reading complete -- and that model has click/type/key/
     nav/eval on the operator's live logged-in Brave. The file must say so and
     must carry an inlineable block; both are pinned.
  5. THE INLINE BLOCK. It is the copy that actually ships to a reader that
     cannot read files, so its nine rails and their operative prohibitions are
     pinned, not just its presence.
  6. THE TEMPLATE. Its slots, and that the path it tells every prompt to cite
     actually resolves to THIS file.
  7. RAIL 6's SCOPE, asserted INSIDE rail 6's own block. Rail 6 forbids
     screenshots for a DELEGATED run. Read without that scope it contradicts
     SKILL.md's ops table, which tells an OPERATOR to `screenshot` then `Read`
     the `.png`. An earlier document-wide version of this check survived moving
     the caveat into a `## Trivia` section at end-of-file.

HARNESS DISCIPLINE
------------------
Every parser asserts a non-empty, plausible-cardinality result before any
contract claim is made -- an empty parse would make each assertion pass
vacuously. The `test_control_*` tests are the negative controls, and they assert
POSITIVELY: an earlier pair used `!=` / `not in`, which a parser stubbed to
`return []` satisfies, so they certified nothing about the parser. Each control
now names the exact expected difference.

This module is part of the hermetic set (`scripts/run-tests.sh`), so it runs in
`nix build .#checks.x86_64-linux.pytests` -- the repo's real pre-merge gate.
"""
import re
from pathlib import Path

import pytest

BB = Path(__file__).resolve().parent.parent
SKILL_MD = BB / "SKILL.md"
REFERENCE_DIR = BB / "reference"
DOC = REFERENCE_DIR / "validation-prompt.md"

# The route SKILL.md must publish, spelled as the reference table spells it.
ROUTE = "reference/validation-prompt.md"

# Words the route's "load it when…" cell must contain. This one IS a keyword
# check rather than a whole-string pin, deliberately: the trigger is prose whose
# wording should stay free to improve, and the failure it guards against is a
# cell that describes a DIFFERENT topic (measured: repointing it at "you need
# the CDP frame-attach cap semantics" left the suite green).
ROUTE_TRIGGER_WORDS = ("validation", "prompt")

# Each rail's ENTIRE normalised block. See the module docstring: pinning the
# lead alone let an audit invert three rails with a green suite.
RAILS = [
    '1. **ORIENT FIRST.** `browser whoami` before anything else -- both hosts are hostname `nixos`, and picking the wrong profile is the single commonest wasted run. If `extension_stale` is true, SAY SO in the report rather than fighting it.',
    '2. **YOUR OWN TAB.** `open` a new tab this session owns. Never `nav` a tab the operator may be using -- it can hold unsaved work.',
    '3. **READ-ONLY BY DEFAULT.** No Connect / Follow / Message / Invite / Save / Subscribe / Send. No form submit except the one named in WRITES ALLOWED. No account, privacy or notification setting changed. **Never log out.** Never click a control that spends money or quota -- Generate / Render / Create / Buy / Publish -- even when it looks like the obvious next step.',
    '4. **BLAST RADIUS IS THE NAMED SET.** Touch only what WRITES ALLOWED permits. DO NOT TOUCH is a hard list, not a preference. Anything you create for the test is a throwaway and is yours to delete (see 8).',
    '5. **A FAILURE IS A FINDING -- REPORT AND STOP.** Do not retry aggressively and do not engineer around it. A 429, a throttle notice, a permission wall or an unexpected redirect is DATA about the system under test. If a selector misses, try ONE alternative, then report the miss; do not grind.',
    "6. **INLINE READS ONLY, WHEN DELEGATING.** `browser agent` is **structurally blind** -- its tool layer never puts pixels in the model's context, on any model (`~/workspace/devrc/scripts/browser-bridge/reference/agent.md`). A dispatched subagent MAY also be unable to read a file back out of `/tmp/*`; one run in the corpus died exactly there, and others read `/tmp` fine -- so **probe it, do not assume it**: have the delegate `screenshot` once and `Read` the `.png`, and report-and-stop if that fails. Absent a passing probe, read with `text [selector]` and with `js` returning JSON. ⚠ This rail is about DELEGATION. Driving the browser yourself, `screenshot` then `Read` the `.png` is the correct and documented path (SKILL.md ops table) -- do not let this line talk you out of the one op that can see.",
    "7. **A HIDDEN TAB IS A CONFOUND, NOT A RESULT.** An empty or half-built read from a background tab is throttling, not a broken site -- `wake` and re-read before concluding anything, and re-`wake` after a reload. Never `activate` to fix it: that takes the operator's screen. `~/workspace/devrc/scripts/browser-bridge/reference/spa-wake.md`.",
    "8. **CLEAN UP.** Close the tab you opened. Delete throwaway records you created, and confirm they are gone. If anything took the operator's screen, restore BOTH the focused window and the workspace -- including on failure.",
    '9. **REPORT HONESTLY.** Quote verbatim; do not summarise away the raw ids, counts or strings that were the point. State the sample size and what it cannot support. Say what you could NOT check. Do not round toward a conclusion -- "ambiguous" is a valid answer and a useful one.',
]

# Slots the copy-paste template must offer. A dropped slot is a dimension every
# future prompt silently omits: BUDGET and DO NOT TOUCH bound blast radius, and
# THE DISCRIMINATOR is what lets the report settle anything.
TEMPLATE_SLOTS = [
    "TASK:", "SITE:", "INSTANCE:", "BUDGET:", "WRITES ALLOWED",
    "DO NOT TOUCH:", "OBSERVE", "THE DISCRIMINATOR", "REPORT",
]

# The operative content the INLINE block must carry -- the copy that ships to a
# reader which cannot open the file. Substrings, because the inline block is
# compressed prose whose wording should stay tunable; the whole-block pin lives
# on the canonical rails above. Each entry is the part whose ABSENCE would let a
# delegated run do real damage.
INLINE_MUST_CARRY = [
    "`whoami` first",
    "Never `nav` a tab the operator may be using",
    "no Connect/Follow/Message/Invite/Save/Subscribe/Send",
    "NEVER log out",
    "Generate/Render/Create/Buy/Publish",
    "DO NOT TOUCH is a hard",
    "report it and STOP",
    "you cannot see images",
    "throttling, not a broken site",
    "Never `activate`",
    "close the tab you opened",
    "Do not round toward a conclusion",
]

# The soft ceiling. Raised from 8,192 when the inline block landed: that block
# is the artifact that replaces a 3-5.6 KB retyped preamble for readers which
# cannot open the file, so it earns its bytes. Still a ceiling, because a
# contract that grows into a second copy of SKILL.md regenerates the very
# duplication it exists to remove. Fails only on a wholesale paste.
MAX_DOC_BYTES = 10_240


def _norm(text: str) -> str:
    """Collapse whitespace and fold the en/em dashes to ASCII.

    The dash fold lets the ledger above be written in ASCII: the doc uses real
    em dashes, and a pinned entry carrying a non-ASCII codepoint is one bad
    copy-paste from a false red that reads as a content failure. `grep` can
    render a character invisible (RULES.md, "Shell & Tooling Gotchas"), so the
    fold happens here, once, explicitly.
    """
    return re.sub(r"\s+", " ", text.replace("—", "--").replace("–", "-")).strip()


def _doc_text() -> str:
    assert DOC.is_file(), (
        f"{DOC} not found -- every assertion in this module would be vacuous. "
        "If the contract moved, update DOC here AND the reference-table row in "
        f"{SKILL_MD}; a moved contract with a stale route is unreachable."
    )
    return DOC.read_text(encoding="utf-8")


def _section(doc_text: str, heading_prefix: str) -> str:
    """The body of the first `## ` section whose heading starts with the prefix.

    Sectioning first is the point: a document-wide substring search is
    satisfied by the same words appearing anywhere, including in a section
    someone added to satisfy the check. Measured on the previous revision --
    moving rail 6's caveat into a `## Trivia` block at end-of-file left the
    suite green.
    """
    for chunk in doc_text.split("\n## ")[1:]:
        # Match against the heading LINE, by substring: headings carry emoji
        # severity markers ("🔴 FIRST: …") that a startswith() prefix would
        # have to encode, which is how this slicer silently returned "" the
        # first time it ran.
        if heading_prefix in chunk.split("\n", 1)[0]:
            return chunk
    return ""


def _fences(text: str) -> list[str]:
    return re.findall(r"```text\n(.*?)```", text, re.S)


def _reference_table_rows(skill_text: str) -> list[tuple[str, str]]:
    """`(file, trigger)` pairs from SKILL.md's reference table.

    Deliberately parsed rather than grepped: the question is "is the reader
    ROUTED here", and only a table row routes. #567 is the incident where that
    distinction was worth 47 dead routes.
    """
    rows: list[tuple[str, str]] = []
    in_table = False
    for line in skill_text.splitlines():
        if line.startswith("| file | load it when"):
            in_table = True
            continue
        if in_table:
            if not line.startswith("|"):
                break
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if len(cells) < 2 or set(cells[0]) <= set("-: "):
                continue
            rows.append((cells[0].strip("`"), cells[1]))
    return rows


def _rail_blocks(doc_text: str) -> list[str]:
    """Each numbered rail's WHOLE normalised block from the standing contract."""
    section = _section(doc_text, "The standing contract")
    if not section:
        return []
    blocks = re.split(r"^(?=\d+\.\s)", section, flags=re.M)
    return [_norm(b) for b in blocks if re.match(r"^\d+\.\s", b)]


# --------------------------------------------------------------------------
# Guard the guards. A parser that quietly returns nothing makes every contract
# assertion below pass vacuously -- that is how a harness reports success while
# testing nothing.
# --------------------------------------------------------------------------

def test_reference_table_parser_finds_a_plausible_table():
    rows = _reference_table_rows(SKILL_MD.read_text(encoding="utf-8"))
    assert len(rows) >= 8, (
        f"parsed only {len(rows)} rows out of {SKILL_MD}'s reference table "
        f"({[r[0] for r in rows]}). The table has had 11+ rows since #562; this "
        "few means the parser lost the table (heading reworded? table moved?), "
        "and the route assertions below would pass for the wrong reason."
    )
    assert all(f.endswith(".md") or f.endswith("/") or ">" in f for f, _ in rows), (
        f"reference-table file column holds a non-path cell: {[r[0] for r in rows]}"
    )


def test_rail_parser_finds_all_nine_rails():
    blocks = _rail_blocks(_doc_text())
    assert len(blocks) == len(RAILS), (
        f"parsed {len(blocks)} rails out of {DOC}, expected {len(RAILS)}. "
        "Either a rail was added/removed without updating RAILS here, or the "
        "numbered-list shape changed and the ledger assertion below has stopped "
        "observing anything."
    )


def test_section_slicer_finds_every_section_this_module_asserts_on():
    """Each assertion slices a section first; a renamed heading must not
    silently turn its check into an assertion about an empty string."""
    doc = _doc_text()
    for prefix in ("FIRST: can your reader", "The inline rails",
                   "The template", "The standing contract"):
        assert _section(doc, prefix), (
            f"{DOC} has no '## {prefix}…' section. A check that slices it would "
            "assert against an empty string and pass vacuously. Either restore "
            "the heading or update the prefix here."
        )


def test_control_rail_ledger_detects_a_reworded_BODY():
    """🔴 The control for the defect that shipped in the first revision.

    Rail 3's body is inverted while its headline is left untouched. The old
    headline-only ledger passed this with 17 green. Assert POSITIVELY that the
    parsed ledger differs from the pin at exactly rail 3 -- a `!=` alone would
    also be satisfied by a parser stubbed to return [].
    """
    doctored = _doc_text().replace(
        "No Connect / Follow / Message / Invite / Save /\n   Subscribe / Send.",
        "Feel free to Connect / Follow / Message / Save, and log out after.",
        1,
    )
    blocks = _rail_blocks(doctored)
    assert len(blocks) == len(RAILS), (
        "the doctored copy no longer parses into nine rails, so this control is "
        "measuring the parser rather than the ledger."
    )
    differing = [i for i, (a, b) in enumerate(zip(blocks, RAILS)) if a != b]
    assert differing == [2], (
        "the whole-block ledger did not isolate an inverted rail-3 BODY "
        f"(differing indices: {differing}, expected [2]). This is the exact "
        "walk-through an audit demonstrated against the headline-only version; "
        "if it no longer fires, the ledger has regressed to pinning labels."
    )


def test_control_route_parser_detects_a_deleted_row():
    """Negative control, asserted positively: deleting the row must remove that
    one entry and leave every other row intact."""
    skill = SKILL_MD.read_text(encoding="utf-8")
    before = _reference_table_rows(skill)
    doctored = "\n".join(ln for ln in skill.splitlines() if ROUTE not in ln)
    after = _reference_table_rows(doctored)
    removed = [f for f, _ in before if f not in [g for g, _ in after]]
    assert removed == [ROUTE], (
        f"deleting the {ROUTE} row changed the parsed set by {removed}, "
        f"expected exactly [{ROUTE!r}]. The parser is matching something other "
        "than the reference table, or has gone empty."
    )
    assert len(after) == len(before) - 1, (
        "deleting one row did not remove exactly one row -- the parser is not "
        "reading the table it claims to read."
    )


# --------------------------------------------------------------------------
# The contract itself.
# --------------------------------------------------------------------------

def test_every_reference_topic_is_routed_from_skill_md():
    """🔴 Set equality, not a spot check on this one file.

    #567 was 47 routes pointing where the reader never is. A bespoke check for
    a single file leaves the 14th topic unguarded and lets that defect regrow;
    derived from the filesystem, this cannot.
    """
    on_disk = {p.name for p in REFERENCE_DIR.glob("*.md")}
    assert on_disk, f"no reference/*.md under {REFERENCE_DIR} -- vacuous check."
    routed = {f.split("/", 1)[1] for f, _ in
              _reference_table_rows(SKILL_MD.read_text(encoding="utf-8"))
              if f.startswith("reference/") and f.endswith(".md")
              and "<" not in f}
    unrouted = sorted(on_disk - routed)
    dangling = sorted(routed - on_disk)
    assert not unrouted, (
        f"\n\nreference topics that exist but are NOT routed from {SKILL_MD}: "
        f"{unrouted}\nA reference file nothing routes to is a file the reader "
        "never reaches -- the #567 defect, not a documentation nit. Add a row "
        "to the 'Reference files' table and evict its bytes from elsewhere in "
        "SKILL.md; tests/test_skill_size.py owns the ceiling."
    )
    assert not dangling, (
        f"\n\n{SKILL_MD}'s reference table routes to files that do not exist: "
        f"{dangling}\nA row pointing at nothing costs the reader a round trip "
        "and reads as an instruction."
    )


def test_the_route_row_trigger_still_describes_this_file():
    """A row's EXISTENCE is not a route; its trigger cell is what routes.

    Measured on the previous revision: rewriting this cell to describe CDP
    frame-attach semantics left the suite green, so the row was pinned while
    the routing value it exists for was not.
    """
    rows = dict(_reference_table_rows(SKILL_MD.read_text(encoding="utf-8")))
    assert ROUTE in rows, f"{ROUTE} has no row in {SKILL_MD}'s reference table."
    trigger = rows[ROUTE].lower()
    missing = [w for w in ROUTE_TRIGGER_WORDS if w not in trigger]
    assert not missing, (
        f"the reference-table trigger for {ROUTE} is {rows[ROUTE]!r}, which no "
        f"longer mentions {missing}. A row whose 'load it when' cell describes "
        "a different topic routes nobody -- which is the #567 defect itself, "
        "not the row's absence."
    )


def test_every_standing_rail_matches_its_pinned_block():
    """🔴 The load-bearing assertion. Whole blocks, in order."""
    blocks = _rail_blocks(_doc_text())
    for i, (found, pinned) in enumerate(zip(blocks, RAILS), start=1):
        assert found == pinned, (
            f"\n\nrail {i} in {DOC} no longer matches its pinned block.\n"
            f"  in the file: {found}\n"
            f"  pinned here: {pinned}\n\n"
            "Prompts CITE this contract instead of restating it, so a rail "
            "reworded here is reworded for every run that cites it, with the "
            "prompt still reading complete. The BODY is the rail -- an earlier "
            "revision pinned only the headline and an audit inverted three "
            "rails with a fully green suite. If the change is intended, paste "
            "the new block into RAILS IN THE SAME COMMIT; that paste is the "
            "review."
        )
    assert len(blocks) == len(RAILS)


def test_the_file_tells_the_reader_browser_agent_cannot_read_it():
    """🔴 The rails ride on a filesystem path; `browser agent` has no file tool.

    Its model gets ONE typed tool with an 11-op browser-only surface, and the
    agent def denies bash/read/edit/write/webfetch -- enforced by a fail-closed
    runtime gate before the model is invoked (reference/agent.md). A prompt that
    CITES this path therefore reaches it carrying ZERO rails while still reading
    complete, and that model has click/type/key/nav/eval on the operator's live
    logged-in Brave. The capability split must be stated where a prompt author
    sees it BEFORE the template.
    """
    section = _section(_doc_text(), "FIRST: can your reader")
    assert section, (
        f"{DOC} has lost its reader-capability section. Without it the file "
        "reads as 'cite this path' to every audience, including the one that "
        "is code-enforced unable to open it."
    )
    norm = _norm(section)
    for claim in ("`browser agent`", "CANNOT", "INLINE"):
        assert claim in norm or claim.lower() in norm.lower(), (
            f"the reader-capability section no longer states {claim!r}. It must "
            "name browser agent, say it cannot read the path, and send the "
            "author to the inline block."
        )
    doc = _doc_text()
    assert doc.index("FIRST: can your reader") < doc.index("## The template"), (
        "the reader-capability section must come BEFORE the template -- an "
        "author who reaches the citation line first has already made the "
        "choice this section exists to inform."
    )


@pytest.mark.parametrize("must_carry", INLINE_MUST_CARRY)
def test_the_inline_block_carries_the_operative_prohibitions(must_carry):
    """The inline block is the copy that ships to a reader which cannot read
    files. Anything missing here is missing from those runs entirely."""
    fences = _fences(_section(_doc_text(), "The inline rails"))
    assert len(fences) == 1, (
        f"expected exactly one ```text fence in {DOC}'s inline-rails section, "
        f"found {len(fences)}. Joining several fences is how a check gets "
        "satisfied by an unrelated example block."
    )
    assert must_carry in _norm(fences[0]), (
        f"the inline rails block no longer carries {must_carry!r}. That text is "
        "the operative half of a rail; without it a delegated run that cannot "
        "read the contract file proceeds without that protection."
    )


def test_the_inline_block_numbers_all_nine_rails():
    fence = _fences(_section(_doc_text(), "The inline rails"))[0]
    numbered = re.findall(r"^(\d+) ", fence, re.M)
    assert numbered == [str(i) for i in range(1, len(RAILS) + 1)], (
        f"the inline rails block numbers {numbered}, expected 1..{len(RAILS)}. "
        "It is the whole contract for readers that cannot open this file, so a "
        "missing number is a missing rail."
    )


@pytest.mark.parametrize("slot", TEMPLATE_SLOTS)
def test_the_template_offers_every_slot(slot):
    fences = _fences(_section(_doc_text(), "The template"))
    assert len(fences) == 1, (
        f"expected exactly one ```text fence in {DOC}'s template section, found "
        f"{len(fences)}. An earlier revision joined every fence in the file, so "
        "an unrelated 'worked example' block could satisfy this check while the "
        "real template was gutted."
    )
    assert slot in fences[0], (
        f"the copy-paste template no longer offers the {slot!r} slot. A dropped "
        "slot is a dimension every future prompt silently omits: BUDGET and DO "
        "NOT TOUCH bound blast radius, THE DISCRIMINATOR is what makes the "
        "report able to settle anything."
    )


def test_the_template_cites_a_path_that_resolves_to_this_file():
    """The whole mechanism is "the prompt cites this path".

    A rename plus a coordinated update of DOC and the SKILL.md row would leave
    every emitted prompt citing a dead path, with the suite green.
    """
    fence = _fences(_section(_doc_text(), "The template"))[0]
    cited = [tok.strip("`,.") for tok in re.findall(r"\S+", fence)
             if "validation-prompt.md" in tok]
    assert cited, (
        f"the template in {DOC} no longer cites a path ending in "
        "'validation-prompt.md'. The citation line IS the delivery mechanism "
        "for all nine rails."
    )
    # Compared as a repo-relative SUFFIX plus the canonical deploy prefix, not
    # by resolving to an absolute path: this suite also runs from a git
    # worktree and from a /nix/store copy inside `nix build`, where DOC's
    # absolute path is NOT the path a reader should be sent to. Resolving was
    # the first thing tried and it failed in the worktree for exactly that
    # reason. The suffix still catches a rename; the prefix still catches a
    # citation pointed at some other checkout.
    rel = DOC.relative_to(BB.parent.parent).as_posix()
    matching = [c for c in cited
                if c.endswith(rel) and c.startswith("~/workspace/devrc/")]
    assert matching, (
        f"the template cites {cited}, none of which is the canonical "
        f"'~/workspace/devrc/{rel}'. Every prompt built from this template "
        "would send its reader to a path that does not hold the rails."
    )


def test_rail_six_keeps_its_delegation_scope_INSIDE_rail_six():
    """Rail 6 must not read as a blanket screenshot ban.

    SKILL.md's ops table tells an OPERATOR that `screenshot` works on a
    background tab and to `Read` the `.png`. Rail 6 forbids screenshots for a
    DELEGATED run only. Stripped of that scope it contradicts the core doc and
    talks the reader out of the only op that can see.

    Asserted inside rail 6's own block: a document-wide version of this check
    survived moving the caveat into a `## Trivia` section at end-of-file, and
    survived rewriting rail 6 to permit screenshots while leaving both pinned
    substrings elsewhere in the file.
    """
    rail6 = _rail_blocks(_doc_text())[5]
    assert "This rail is about DELEGATION." in rail6, (
        "rail 6 has lost its explicit delegation scope. Without it the contract "
        "reads as a blanket 'never screenshot', which contradicts "
        f"{SKILL_MD}'s screenshot row and removes the only op that can see."
    )
    assert "`Read` the `.png` is the correct and documented path" in rail6, (
        "rail 6 no longer names the operator's correct path. Naming a "
        "prohibition without naming the alternative is what makes readers "
        "over-apply it."
    )
    assert "probe it, do not assume it" in rail6, (
        "rail 6 has reverted to asserting that a delegate CANNOT read /tmp. "
        "That generalises n=1 -- other sandboxes read /tmp fine -- and a "
        "blanket ban removes all delegated visual work. RULES.md prefers the "
        "deterministic probe over the prose blanket."
    )


def test_contract_does_not_restate_what_skill_md_owns():
    """The file's value is that it is short enough to cite or paste."""
    size = len(DOC.read_bytes())
    assert size <= MAX_DOC_BYTES, (
        f"{DOC} is {size:,} bytes, over the {MAX_DOC_BYTES:,} B ceiling. It "
        "exists to replace a 3-5.6 KB preamble; past this it is cheaper to "
        "retype the rails than to load it. Move mechanism detail to the "
        "reference file that owns it (agent.md, spa-wake.md, frames-cdp.md) "
        "and leave a pointer."
    )
