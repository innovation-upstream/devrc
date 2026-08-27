"""The adversarial-audit ladder must keep its STOP condition — and must not
acquire a round CAP instead.

WHAT THE RULE IS
----------------
`claude/RULES.md` mandates SEVERAL delta re-audit rounds after a review fix, and
for a measured reason: "every delta audit found something the previous round's
fix had introduced". Nothing said when to STOP, and the obvious stop signal --
the auditor's "safe to merge" verdict -- is the wrong one.

Verified against devrc #804's own commit bodies (`gh pr view 804 --json commits`,
then the message bodies): **rounds 5, 6 and 7 each returned "safe to merge" AND
each reported real defects that were then fixed.** Round 7's was a `didDrag`
latch pinned on its SET but not its RELEASE -- deleting the reset, deleting the
clear, and deleting BOTH were all green. A ladder keyed to the VERDICT stops at
round 5 and ships a guard that reads as pinned and is vacuous in both directions.

The rule that closes it is one sentence, keyed to findings rather than to the
verdict or to a count: **a clean round ends the ladder; never run another round
to confirm a clean round.**

🔴 A RETRACTION, KEPT BECAUSE THE RETRACTED VERSION SOUNDS RIGHT
----------------------------------------------------------------
This module first justified the rule as a fix for measured WASTE -- "#804's
rounds 5-8 all returned safe to merge, so four rounds ran after the stop
condition was met", plus round counts for #482/#505/#390/#393. **Every
load-bearing part of that is false.** #390 has ONE commit and no ladder; #393's
five commits are independent follow-on fixes, not numbered rounds; #482's rounds
are unnumbered; and critically, **no cited PR contains a single round that ran
and found nothing** -- so none of them evidences a wasted round at all. Even
#804's round 8, whose auditor called the clean state the stop condition, still
carried three 🟢 (two were shipped features that could be unwired with the suite
green). The ladder ran to 8 legitimately and this stop rule was never exercised
on it.

So the rule is FORWARD-LOOKING with a demonstrated near-miss. It is not a remedy
for observed waste, and must not be cited as one.

🔴 THE ATTRIBUTION GATE -- THE AXIS THE FINDINGS-KEYED RULE CANNOT SEE
----------------------------------------------------------------------
Added 2026-08-26. The stop rule above is keyed to findings, and there is a whole
regime in which it can never fire: a fix round WRITES new guards, the next delta
round diffs `<audited-sha>..HEAD`, so those guards are its audit surface. The
ladder manufactures its own next round's findings.

Measured on `civitai/cli` #498 (session 4719a2f0, 2026-08-26): **10 rounds over
5 h 32 m**, 75% of the session's transcript rows, **321k of 419k main-thread
output tokens**. The fix commits for **rounds 4-10 changed 1,002 lines of test
code and ZERO lines of production code** -- the last production change was round
3's `d2ec92d`. **No round was ever clean**, so the stop rule was never once
eligible to fire, and the session escalated to the operator at round 9 with its
own diagnosis ("rounds 3-10 have all been about the guards, not the feature").

Scale, measured the same day over `~/.claude/projects/**/*.jsonl` (numbered delta
re-audit dispatches, `subagents/` excluded): **110 sessions, 440 rounds, 84 of
them in the preceding 14 days**; mean deepest round 4.0; 34% ran >= 5 rounds.

So the gate is: two consecutive rounds whose FIXES changed zero production lines
=> the ladder has left the PR, stop. It is not a cap (it counts production lines,
never rounds; a round that touches production code never trips it however deep
the ladder is) and it does not retract the retraction below -- #498 contains no
round that ran and found nothing, which is the waste that retraction denies.
Different axis: real findings about scaffolding the ladder itself had written.

🔴 WHY THIS MODULE ALSO GUARDS THE COUNTER-EVIDENCE
---------------------------------------------------
The obvious fix -- cap the rounds at N -- was written first and REJECTED, on this
repo's own verifiable evidence. devrc #505's round 2 opens "Round 1 fixed six
findings and introduced two of its own", and its round 4 caught a ReDoS that
round 3's OWN fix introduced -- `{7,40}` inside a `*` loop, three 40-char shas
not returning in 30 s, hanging `scan_open_actions` and so `/handoff` with no
output -- plus a terminator requirement round 3 added that silently dropped ten
marker shapes, "the failure this detector exists to prevent, reintroduced by the
fix for the previous one". A cap at 2 or 3 ships both. So the count is set by
FINDINGS, never by a number.

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
5. **Nothing here proves the attribution gate is RUN.** Same blind spot as (2),
   and it bites harder for this one: the gate is a `git diff --numstat` an
   operator has to issue between rounds, so the only evidence it fired is a
   ladder that stopped. The behavioural claim needs transcript measurement over
   future ladders -- re-run the sweep in the docstring above -- and is NOT made
   here.
6. **The THRESHOLD is pinned, not justified.** "Two consecutive rounds" comes
   from one measured ladder (#498, n=1), where the plateau began at round 4 and
   ran to 10. One round is knowingly too tight: a round may legitimately fix only
   a guard. Whether two is right is a judgement, and this module cannot tell a
   deliberate re-tune from a quiet weakening -- it only makes the change visible
   in the diff, which is the point of pinning a number in prose at all.

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

THE ATTRIBUTION-GATE PINS -- their own matrix (2026-08-26, 3 tests added)
------------------------------------------------------------------------
Same method: each run on its OWN scratch tree built from HEAD, never the
worktree, under `PYTHONDONTWRITEBYTECODE=1` with the pytest cache disabled, and
every mutant script asserts its target string is present before editing (so a
missed mutant fails loudly instead of scoring SURVIVED). Script preserved at
`scratchpad/controls.sh` in the authoring session; it is 6 `cp`s and a `pytest`.

  POS  unmutated copy .......................... 9 passed  <- harness control
  BASE origin/main's SKILL.md, reference file
       absent (i.e. pre-change) ................ 3 failed, 6 passed
                                                 -- exactly the three new tests
                                                 red, all six pre-existing ones
                                                 green: regression coverage, not
                                                 an invariant guard
  M7   attribution heading deleted .............. 2 failed -- heading pin, plus
                                                 the order test's both-present
                                                 precondition
  M8   gate threshold reworded TWO -> THREE ..... 1 failed -- the gate pin ONLY,
                                                 which is what separates a
                                                 whole-string pin from a keyword
                                                 guard
  M9   evidence file deleted .................... 1 failed -- evidence pin
  M11  different-axis paragraph dropped from
       the evidence file ....................... 1 failed -- evidence pin

🔴 M10 is this half's reachability control, and the analogue of M4. The
attribution section is MOVED above the stop rule with its text byte-identical:
every whole-string pin stays GREEN and only
`test_the_attribution_gate_comes_after_the_rule_it_bounds` goes red (1 failed) --
which proves that assertion executes rather than restating the pins beside it.
"""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

RULES_MD = REPO_ROOT / "claude" / "RULES.md"
ARCHIVE_MD = REPO_ROOT / "claude" / "RULES-ARCHIVE.md"
SKILL_MD = REPO_ROOT / "claude" / "skills" / "audit-pr" / "SKILL.md"
EVIDENCE_MD = (
    REPO_ROOT / "claude" / "skills" / "audit-pr" / "reference"
    / "round-ladder-evidence.md"
)

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
    "FINDINGS, never by a number, and #505 is why: its round 2 opens *\"Round 1 "
    "fixed six findings and introduced two of its own\"*, and its round 4 caught "
    "a **ReDoS that round 3's own fix introduced** — three 40-char shas did not "
    "return in 30 s, hanging `/handoff` with no output — plus a terminator "
    "requirement that round 3 had added and that silently dropped ten marker "
    "shapes, *\"the failure this detector exists to prevent, reintroduced by the "
    "fix for the previous one\"*. A cap at 2 or 3 ships both."
)

# 🔴 The load-bearing EVIDENCE sentence: verdict != findings. This is what makes
# the rule findings-keyed rather than verdict-keyed, and it is the half that
# survived verification when the original "measured waste" justification did not.
SKILL_VERDICT_NOT_STOP = (
    "🔴 **A \"safe to merge\" VERDICT is not the stop signal — the FINDINGS are.** "
    "#804's rounds **5, 6 and 7 each returned \"safe to merge\" and each still "
    "reported real defects** that were then fixed"
)

# 🔴 The RETRACTION itself is pinned. Without this, deleting the retraction is a
# silent edit that leaves the false "four wasted rounds" story free to be
# rewritten from memory by the next author -- which is exactly how it got into
# five files the first time.
SKILL_NOT_WASTE = (
    "⚠ **#804 is NOT an example of a wasted round, and neither is any other PR "
    "cited here.**"
)

# `claude/RULES-ARCHIVE.md`, anchor `audit-fix-resets-gate` -- the evidence the
# core bullet's `→ archive:` tag routes to. Pinned so the rejected-cap reasoning
# cannot be evicted into nothing: RULES.md is byte-capped, so this anchor is
# where a future editor is TOLD to move detail, and an eviction that loses the
# rejection loses the only record of why the narrow rule was chosen.
ARCHIVE_REJECTED_CAP = (
    "A numeric CAP was the first version of this fix and was **rejected**, on "
    "evidence that IS verifiable: #505's round 2 opens *\"Round 1 fixed six "
    "findings and introduced two of its own\"*, and its round 4 caught a ReDoS "
    "that **round 3's own fix** introduced"
)

# The archive's copy of the retraction. Same reasoning as SKILL_NOT_WASTE, and
# more important here: the archive is where a future editor is sent to LOOK UP
# why this rule exists, so a retraction that rots out of it is worse than one
# that rots out of the skill.
ARCHIVE_RETRACTION = (
    "**Every load-bearing part of that is false**, and it was checked against "
    "`gh pr view <n> --json commits` only after it had been written into five "
    "places."
)

# --------------------------------------------------------------------------- #
# THE ATTRIBUTION GATE -- the axis the findings-keyed stop rule cannot see.
# --------------------------------------------------------------------------- #

SKILL_ATTRIBUTION_HEADING = (
    "### 🔴 ATTRIBUTION: a round that changes no PRODUCTION code is auditing "
    "the LADDER, not the PR"
)

# The gate itself. Pinned whole because every number in it is load-bearing: TWO
# consecutive rounds (one is noise -- a round can legitimately fix only a guard),
# ZERO production lines (not "few"), and the action is STOP rather than "consider
# stopping".
SKILL_ATTRIBUTION_GATE = (
    "**Two consecutive rounds whose fixes changed zero production lines ⇒ the "
    "ladder has left the PR. Stop.**"
)

# 🔴 The clause that keeps this from being read as the rejected cap, or as a
# retraction of the two rules it sits under. Without it the next reader has three
# stop rules and no idea how they compose -- and the cheapest way to "simplify"
# that is to collapse them into a count, which is the rejected fix.
SKILL_ATTRIBUTION_NOT_A_CAP = (
    "⚠ **This does not retract the two rules above, and is not a cap in "
    "disguise.** #498's rounds were not wasted in the sense those rules deny — "
    "every one found something real. The waste is on a different axis: real "
    "findings *about scaffolding the ladder itself had just written*. The gate "
    "measures the fixes; it never counts the rounds."
)

# The deployed path, not a repo-relative one: a devrc skill is READ from
# ~/.claude/skills/<name>/ by an agent whose cwd is some unrelated project, so a
# bare `reference/x.md` resolves against that cwd. Same rule as
# scripts/tests/test_doc_path_rot.py (1c).
SKILL_ROUTES_TO_EVIDENCE = (
    "`~/.claude/skills/audit-pr/reference/round-ladder-evidence.md`"
)

# The measurement the gate rests on, pinned in the evidence file so an eviction
# cannot leave the rule standing on an anecdote. The churn row IS the argument:
# rounds 4-10 changed 1,002 test lines and 0 production lines.
EVIDENCE_CHURN_ROW = "| **rounds 4–10** | **1,002** | **0** |"

# 🔴 The half a reader will want to delete as "redundant with the retraction it
# does not contradict". #498 is NOT evidence of a round that found nothing; it is
# evidence of rounds that found real things about the ladder's own scaffolding.
# Losing this distinction re-opens the retracted "measured waste" story.
EVIDENCE_DIFFERENT_AXIS = (
    "**It does NOT contradict the \"not a wasted round\" retraction.** No #498 "
    "round ran and found nothing, so it is not an instance of the waste that "
    "retraction denies. Different axis."
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
        "  If you DELETED it: the ladder loses its stop condition, and the only "
        "signal left is the auditor's VERDICT -- which devrc #804 proves is the "
        "wrong one (its rounds 5, 6 and 7 each returned 'safe to merge' while "
        "still reporting real defects).\n"
        "  If you replaced it with a round CAP: a cap was written first and "
        "rejected. devrc #505's round 1 introduced two of its own findings and "
        "round 3's fix introduced a ReDoS, both caught by later rounds -- a cap "
        "at 2 or 3 ships them. Read the `audit-fix-resets-gate` section of "
        "claude/RULES-ARCHIVE.md before re-deriving that."
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


def test_the_rule_is_justified_by_the_verdict_gap_not_by_claimed_waste():
    """🔴 The justification is guarded because it was WRONG once already.

    The first version of this work justified the rule as a fix for measured
    waste ("#804's rounds 5-8 all returned safe to merge, four ran anyway"). It
    was false, and by the time it was checked it sat in five files. What is true
    and verifiable is narrower: a "safe to merge" VERDICT is not the stop signal,
    because #804's rounds 5, 6 and 7 each returned one while still reporting real
    defects.

    Both halves are pinned: the surviving evidence, and the retraction. Pinning
    only the evidence would let the retraction be deleted, and a deleted
    retraction is how the false story gets rewritten from memory.
    """
    _assert_pinned_once(
        SKILL_MD, SKILL_VERDICT_NOT_STOP, "the verdict-is-not-the-stop-signal claim"
    )
    _assert_pinned_once(SKILL_MD, SKILL_NOT_WASTE, "the not-a-wasted-round retraction")
    _assert_pinned_once(ARCHIVE_MD, ARCHIVE_RETRACTION, "the archive's retraction")


def test_the_attribution_gate_is_stated_with_its_not_a_cap_qualifier():
    """🔴 The gate the findings-keyed stop rule cannot supply on its own.

    A delta round diffs `<audited-sha>..HEAD`, and a fix round writes new guards
    INTO that range -- so the ladder manufactures its own next round's findings
    and a stop condition keyed to findings has no exit. Measured on `civitai/cli`
    #498 (2026-08-26): ten rounds, 5 h 32 m, 77% of the session's output tokens,
    and the fix commits for rounds 4-10 changed 1,002 lines of test code and ZERO
    lines of production code. No round was ever clean, so the rule above was
    never once eligible to fire.

    All three constants are pinned together on purpose. The gate without its
    qualifier reads as a third stop rule of unknown standing next to two others,
    and the cheapest way to reconcile three is to collapse them into a count --
    which is the cap this module already rejects.
    """
    _assert_pinned_once(
        SKILL_MD, SKILL_ATTRIBUTION_HEADING, "the attribution-gate heading"
    )
    _assert_pinned_once(SKILL_MD, SKILL_ATTRIBUTION_GATE, "the attribution gate")
    _assert_pinned_once(
        SKILL_MD,
        SKILL_ATTRIBUTION_NOT_A_CAP,
        "the attribution gate's not-a-cap / not-a-retraction qualifier",
    )


def test_the_attribution_gate_comes_after_the_rule_it_bounds():
    """A RELATIONSHIP pin, and the reachability control for the one above.

    Order carries meaning here: the attribution gate BOUNDS the findings-keyed
    stop rule, it does not replace it. A reader who meets it first meets "stop
    when the fixes stop touching production code" with no "keep going while
    rounds keep finding things" in view -- which is the one-audit-is-enough
    failure `audit-fix-resets-gate` exists to prevent.

    This assertion is what fails when the section is MOVED with its text
    byte-identical; every whole-string pin above stays green in that mutant, so a
    red here proves this test executes rather than restating its neighbour.
    """
    body = _norm(_read(SKILL_MD))
    stop_at = body.find(_norm(SKILL_HEADING))
    attribution_at = body.find(_norm(SKILL_ATTRIBUTION_HEADING))

    assert stop_at != -1 and attribution_at != -1, (
        "one of the two headings is missing -- the pins above report which; this "
        "test only orders them."
    )
    assert stop_at < attribution_at, (
        "\n\nclaude/skills/audit-pr/SKILL.md: the ATTRIBUTION gate now precedes "
        "the clean-round stop rule.\n"
        "  The gate bounds that rule; it does not replace it. Read first, it "
        "says 'stop when the fixes stop touching production code' with no "
        "'keep going while rounds keep finding things' in view -- and a single "
        "audit round is how a completely inert feature shipped past 428 green "
        "tests (claude/RULES-ARCHIVE.md, audit-fix-resets-gate).\n"
        "  Restore the order, or re-point this test deliberately and say in the "
        "commit how a reader still meets the bounding rule first."
    )


def test_the_attribution_measurement_survives_where_the_skill_routes_to_it():
    """🔴 The rule is only as good as the measurement it rests on.

    The skill body carries one line of it; the rest was demoted to the skill's
    reference/ dir to pay for the rule's bytes. Two ways that goes wrong, both
    pinned: the evidence file is deleted or emptied (the rule then rests on an
    anecdote), or the body routes to it with a path that does not RESOLVE for
    the reader -- a devrc skill is read from ~/.claude/skills/<name>/ by an agent
    whose cwd is some unrelated project, so a bare `reference/x.md` opens
    nothing (scripts/tests/test_doc_path_rot.py, rule 1c).
    """
    assert EVIDENCE_MD.is_file(), (
        f"{EVIDENCE_MD.relative_to(REPO_ROOT)} is missing -- the attribution "
        "gate's measurement lives there and the SKILL.md body only summarises "
        "it in one line. Restore it, or move the measurement back into the "
        "body and re-point this test."
    )
    _assert_pinned_once(SKILL_MD, SKILL_ROUTES_TO_EVIDENCE, "the evidence route")
    _assert_pinned_once(EVIDENCE_MD, EVIDENCE_CHURN_ROW, "the #498 churn row")
    _assert_pinned_once(
        EVIDENCE_MD, EVIDENCE_DIFFERENT_AXIS, "the different-axis distinction"
    )


def test_the_stop_rule_shares_a_bullet_with_the_rule_it_bounds():
    """A RELATIONSHIP pin, not a component pin.

    The stop clause and "Budget for SEVERAL rounds" are counterweights: read
    apart, each is wrong. The several-rounds mandate alone leaves the ladder with
    no stop signal but the verdict; the stop condition alone reads as "one audit
    is enough", which
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
        "  They are counterweights. Several-rounds alone leaves the ladder with "
        "no stop signal but the auditor's verdict, which devrc #804 shows is the "
        "wrong one; the stop condition alone reads as 'one audit is enough', "
        "which is the exact failure the bullet exists to prevent.\n"
        "  Restore them to one bullet, or re-point this test deliberately and "
        "explain in the commit how a reader still meets both claims together."
    )
