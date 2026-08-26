"""The adversarial-audit ladder must keep its STOP condition — and must not
acquire a round CAP instead.

WHAT THE RULE IS
----------------
`claude/RULES.md` mandates SEVERAL delta re-audit rounds after a review fix, and
for a measured reason: "every delta audit found something the previous round's
fix had introduced". Nothing bounded that, so the ladder ran past its own stop
condition. Measured over 443 Claude Code sessions in 14 days, audit-round
escalation was the largest single token sink in this repo:

    PR #804   8 numbered delta rounds; rounds 5, 6, 7 and 8 ALL returned
              "safe to merge" -- the stop condition was met at 5 and four
              rounds ran anyway
    PR #482   6 rounds
    PR #505   5 rounds
    PR #390   5 rounds
    PR #393   5 rounds

The rule that closes it is one sentence: **a clean round ends the ladder; never
run another round to confirm a clean round.**

🔴 WHY THIS MODULE ALSO GUARDS THE COUNTER-EVIDENCE
---------------------------------------------------
The obvious fix -- cap the rounds at N -- was written first and REJECTED, on this
repo's own evidence. Session `f23b37ec` legitimately needed round 4 because BOTH
of round 3's blockers were introduced by round 2's own fixes; a cap at 2 would
have shipped a corrupted census artifact. So the count is set by FINDINGS, never
by a number.

That makes the "not a cap" clause load-bearing, not decoration: without it the
next person reading "stop earlier" reaches for the cap again, and the rule that
replaces this one is strictly worse than no rule. Both halves are pinned.

WHY THE ASSERTIONS LOOK BRITTLE
-------------------------------
The artifact under test is PROSE. `claude/RULES.md`: "when the artifact under
test IS prose, a guard on WORDS is walkable by REWORDING -- pin the WHOLE
normalised string. A cosmetic reword then fails the test -- pay it, for a
machine-readable claim."

So every constant below is a whole normalised sentence (or heading), asserted to
occur EXACTLY once. A keyword match would be satisfied by a document that names
"clean round" while telling you to run one more; the whole-string pin is not.
A reword SHOULD fail here, and the fix is to update the constant in the same
commit -- which is exactly the moment someone should notice they are rewriting a
rule rather than reformatting a paragraph.

🔴 WHAT THIS MODULE DOES **NOT** ENFORCE -- read before trusting it as coverage
------------------------------------------------------------------------------
1. **It pins these SENTENCES, not the CONCEPT.** A semantically identical
   restatement in different words is invisible to it, exactly as
   `test_closing_condition_single_source.py` records for its own pins. There is
   no known mechanical fix.
2. **It cannot observe BEHAVIOUR.** Nothing here proves any session actually
   stopped at the clean round; it proves the instruction is present, whole, and
   in the place a reader hits it. The behavioural claim would need transcript
   measurement over future PRs and is NOT made.
3. **It does not detect a cap added in NEW words elsewhere.** It pins the
   rejection where the rule lives; a cap invented in a third file is out of
   scope.
4. **`test_the_guarded_files_exist_and_are_substantial` is an INVARIANT GUARD,
   not regression coverage.** It was green at `origin/main` and green at HEAD --
   it never caught the bug this module is about. It is labelled here rather than
   counted as a catch. It IS reachable: watched to fail by stubbing SKILL.md.

WATCHED TO FAIL -- the matrix, so this module is not taken on trust
-------------------------------------------------------------------
Red at `origin/main` (the three guarded documents restored via `git show
origin/main:<path>` into a scratch tree, this module copied in unchanged):
**4 failed, 1 passed** -- every stop-rule assertion red, the invariant guard
green, as its label above predicts. Green at HEAD: **5 passed**.

Mutation controls, each run on a copy of the HEAD tree, under
`PYTHONDONTWRITEBYTECODE=1` with the pytest cache disabled. The unmutated copy
is the positive control (5 passed), so a red below is the mutant and not the
harness:

  POS  unmutated copy .......................... 5 passed
  M1   cosmetic reword of the RULES clause
       ("FINDINGS, never by a number" -> "findings,
       not by a number") ........................ 2 failed -- the string pin AND
                                                  the relationship pin
  M2   delete the SKILL heading .................. 1 failed -- heading pin
  M3   replace the not-a-cap paragraph with an
       actual cap ("Cap the ladder at three
       rounds") ................................. 1 failed -- not-a-cap pin
  M4   move the stop clause into its OWN bullet,
       text byte-identical ...................... 1 failed -- relationship pin
                                                  ONLY
  M5   truncate SKILL.md to a stub .............. 3 failed -- the invariant guard
                                                  fires with its own message
  M6   drop the rejected-cap evidence from the
       archive .................................. 1 failed -- archive pin

🔴 M4 is the one that matters for reachability. The clause survives verbatim, so
every whole-string pin stays GREEN and only
`test_the_stop_rule_shares_a_bullet_with_the_rule_it_bounds` goes red -- which is
what proves that assertion executes and is not a second spelling of the string
pin it sits beside.
"""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

RULES_MD = REPO_ROOT / "claude" / "RULES.md"
ARCHIVE_MD = REPO_ROOT / "claude" / "RULES-ARCHIVE.md"
SKILL_MD = REPO_ROOT / "claude" / "skills" / "audit-pr" / "SKILL.md"

# --------------------------------------------------------------------------- #
# THE PINS -- whole normalised strings, never keywords.
# --------------------------------------------------------------------------- #

# `claude/RULES.md`, inside the "An audit/review fix RESETS the verification
# gate" bullet. Both halves in one constant on purpose: the stop condition and
# its not-a-cap qualifier are a single claim, and pinning only the first half
# would let the second be dropped -- which is how the rejected fix comes back.
RULES_STOP_CLAUSE = (
    "🔴 **A CLEAN round ENDS the ladder — never run another to confirm one.** "
    "Rounds continue only while the previous round produced a finding that "
    "needed fixing; the first that returns none is the last. **Not a cap** — "
    "the count is set by FINDINGS, never by a number."
)

# The sentence in the SAME bullet that MANDATES several rounds. The stop rule is
# only safe next to it; see `test_the_stop_rule_shares_a_bullet_with_the_rule_it_bounds`.
RULES_SEVERAL_ROUNDS = (
    "**Budget for SEVERAL rounds and re-audit the DELTA each time**"
)

# `claude/skills/audit-pr/SKILL.md` -- the operational spelling of the same rule.
SKILL_HEADING = (
    "### 🔴 A clean round ENDS the ladder. Never run another round to confirm "
    "a clean round."
)
SKILL_STOP_BODY = (
    "Rounds continue **only** while the previous round produced a finding that "
    "required a fix. The first round that returns no findings is the last one — "
    "stop there, and do not re-confirm it. Stop on that, not on the author "
    "saying it's done."
)
SKILL_NOT_A_CAP = (
    "🔴 **This is NOT a round cap, and a cap was rejected.** The count is set by "
    "FINDINGS, never by a number: session `f23b37ec` legitimately needed round 4 "
    "because **both** of round 3's blockers were introduced by round 2's own "
    "fixes, and a cap at 2 would have shipped a corrupted artifact."
)

# `claude/RULES-ARCHIVE.md`, anchor `audit-fix-resets-gate` -- the evidence the
# core bullet's `→ archive:` tag routes to. Pinned so the rejected-cap reasoning
# cannot be evicted into nothing: RULES.md is byte-capped, so this anchor is
# where a future editor is TOLD to move detail, and an eviction that loses the
# rejection loses the only record of why the narrow rule was chosen.
ARCHIVE_REJECTED_CAP = (
    "A numeric CAP was the first version of this fix and was **rejected**. "
    "Session `f23b37ec` legitimately needed round 4 because **both** of round "
    "3's blockers were introduced by round 2's own fixes; a cap at 2 would have "
    "shipped a corrupted census artifact."
)


def _norm(text: str) -> str:
    """Whitespace-normalised, so a re-wrap is not a failure but a reword is."""
    return " ".join(text.split())


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _assert_pinned_once(path: Path, claim: str, what: str) -> None:
    body = _norm(_read(path))
    count = body.count(_norm(claim))
    assert count == 1, (
        f"\n\n{path.relative_to(REPO_ROOT)}: {what} is not present exactly once "
        f"as written (found {count}).\n"
        f"  Expected verbatim (whitespace-normalised):\n    {_norm(claim)}\n\n"
        "  This is a WHOLE-STRING pin because the artifact is prose and a "
        "keyword guard is walkable by rewording (claude/RULES.md, "
        "spelled-guards).\n"
        "  If you reworded it deliberately, update the constant in "
        "scripts/tests/test_audit_ladder_stop_rule.py in the SAME commit.\n"
        "  If you DELETED it: the ladder loses its stop condition. Measured "
        "cost of not having one -- devrc #804 ran eight rounds, of which 5, 6, "
        "7 and 8 all returned 'safe to merge'.\n"
        "  If you replaced it with a round CAP: a cap was written first and "
        "rejected. Session f23b37ec legitimately needed round 4 because BOTH of "
        "round 3's blockers came from round 2's own fixes. Read the "
        "`audit-fix-resets-gate` section of claude/RULES-ARCHIVE.md before "
        "re-deriving that."
    )


# --------------------------------------------------------------------------- #
# GUARD THE GUARD
# --------------------------------------------------------------------------- #

def test_the_guarded_files_exist_and_are_substantial():
    """A moved or emptied file must fail loudly, not pass vacuously.

    Every assertion below is a substring count. Against a missing file the read
    raises (which is a failure), but against a TRUNCATED one a count of 0 is
    indistinguishable from a reword, and the messages above would send a reader
    hunting for a sentence in a file that no longer holds anything. Size floors
    are deliberately crude -- they only have to separate "the document" from
    "a stub".
    """
    for path, floor in ((RULES_MD, 20_000), (ARCHIVE_MD, 20_000), (SKILL_MD, 3_000)):
        assert path.is_file(), (
            f"{path} not found -- every pin in this module would fail with a "
            "misleading 'reworded' message. If the file MOVED, re-point the "
            "constant at the top of this module."
        )
        size = len(path.read_bytes())
        assert size >= floor, (
            f"{path.relative_to(REPO_ROOT)} is {size:,} B, under the {floor:,} B "
            "sanity floor -- it looks truncated, so the pins below would report "
            "a reword when the real problem is a missing document."
        )


# --------------------------------------------------------------------------- #
# THE STOP RULE
# --------------------------------------------------------------------------- #

def test_rules_md_states_the_stop_condition():
    _assert_pinned_once(
        RULES_MD, RULES_STOP_CLAUSE, "the audit-ladder stop clause"
    )


def test_audit_pr_skill_states_the_stop_condition():
    _assert_pinned_once(SKILL_MD, SKILL_HEADING, "the stop-rule heading")
    _assert_pinned_once(SKILL_MD, SKILL_STOP_BODY, "the stop-rule body")


def test_the_rejected_cap_is_recorded_where_the_rule_lives():
    """🔴 The counter-evidence is part of the rule, not commentary.

    "Stop earlier" without "and not by capping the count" is an invitation to
    re-derive the rejected fix. The skill carries the rejection inline; the
    archive carries the measurement the rejection rests on.
    """
    _assert_pinned_once(SKILL_MD, SKILL_NOT_A_CAP, "the not-a-cap clause")
    _assert_pinned_once(
        ARCHIVE_MD, ARCHIVE_REJECTED_CAP, "the rejected-cap evidence"
    )


def test_the_stop_rule_shares_a_bullet_with_the_rule_it_bounds():
    """A RELATIONSHIP pin, not a component pin.

    The stop clause and "Budget for SEVERAL rounds" are counterweights: read
    apart, each is wrong. The several-rounds mandate alone produced the eight-
    round ladder; the stop condition alone reads as "one audit is enough", which
    is the failure `audit-fix-resets-gate` exists to prevent (a fix that shipped
    a completely inert feature past 428 green tests and a second clean audit).

    RULES.md is one bullet per line, so "same line" IS "same bullet". This fails
    if either clause is moved into a bullet of its own -- deliberately. If you
    restructure the section, re-point this test rather than deleting it, and say
    in the commit how a reader still meets both claims together.
    """
    lines = [_norm(ln) for ln in _read(RULES_MD).splitlines()]
    stop = _norm(RULES_STOP_CLAUSE)
    several = _norm(RULES_SEVERAL_ROUNDS)

    holding_both = [ln for ln in lines if stop in ln and several in ln]
    assert len(holding_both) == 1, (
        "\n\nclaude/RULES.md: the audit-ladder stop clause and the "
        "'Budget for SEVERAL rounds' mandate are no longer in the same bullet "
        f"(found {len(holding_both)} lines holding both).\n"
        "  They are counterweights. Several-rounds alone is what produced the "
        "eight-round ladder on #804; the stop condition alone reads as 'one "
        "audit is enough', which is the exact failure the bullet exists to "
        "prevent.\n"
        "  Restore them to one bullet, or re-point this test deliberately and "
        "explain in the commit how a reader still meets both claims together."
    )
