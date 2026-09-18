#!/usr/bin/env python3
"""Guards for `scripts/ladder-stop-rationale.py` — WHY each audit ladder stopped.

WHY THESE ARE SHAPED THIS WAY
-----------------------------
The thing under test is a CLASSIFIER OVER PROSE, so every guard is a two-way pin
against text of the shape the real corpus carries, and each direction NAMES THE
MUTATION IT KILLS and then proves a fixture can see it. That last step is the one
this arc learned the hard way: `_ROUND_REF_RE = re.compile(r"\\d")` survived a
six-negative pin in `test_ladder_range_coverage.py` because not one negative
carried a digit. **A two-way pin is only two-way against mutations its fixtures
can express**, so the `MUTANTS` assertions below build the broken pattern and
require a fixture to move under it.

🔴 EVERY POSITIVE IS A MEASURED FALSE NEGATIVE AND EVERY NEGATIVE A MEASURED
FALSE POSITIVE. None was imagined: each was found by running the classifier over
seven real repositories on 2026-09-13 and hand-reading the rows it got wrong.
The PR each came from is named on its line.

🔴 devrc IS PUBLIC, AND FOUR OF THE SEVEN MEASURED REPOS ARE CLIENT-OWNED. Text
quoted here is devrc's OWN public PR comments only — the same ones
`claudedocs/audit-ladder-review-2026-09-04.md` already quotes. Every fixture
derived from a client repo is RE-SYNTHESISED to carry the SHAPE and none of the
words, and says so on its line. PR numbers and counts are not captured text.
"""
from __future__ import annotations

import importlib.util
import inspect
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "scripts"
SCRIPT = SCRIPTS / "ladder-stop-rationale.py"
# ⚠ `ladder-range-coverage.py`'s path is NOT a constant here any more. It used to
# be, so two guards could grep its SOURCE TEXT; both are gone (see
# `test_find_carriers_carries_STATE_through_for_this_consumer`) and the sibling
# is now reached the way production reaches it — `lsr.load_siblings()`.


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def lsr():
    return _load(SCRIPT, "ladder_stop_rationale")


@pytest.fixture(scope="module")
def ad(lsr):
    _lrc, ad_mod = lsr.load_siblings()
    return ad_mod


def _block(round_no, frm="a" * 8, to="b" * 8, claim="a claim"):
    return (f"```audit-claims round={round_no} audited={frm}..{to}\n"
            f"1. {claim}\n"
            "```\n")


def _facts(pr, comments, state="MERGED"):
    return {"pr": pr, "state": state, "comments": comments}


# A `gh pr list --json …` payload of the shape `find_carriers` parses: one
# MERGED carrier (a ladder that STOPPED) and one OPEN carrier (a live one, which
# is the whole reason `state` is carried through the seam). Entirely synthetic —
# the PR numbers are out of range and the prose is written here, not captured.
MERGED_CARRIER_PR = 424242
OPEN_CARRIER_PR = 424243


def _gh_pr_list_payload():
    return json.dumps([
        {"number": MERGED_CARRIER_PR, "title": "a ladder that stopped",
         "state": "MERGED", "headRefOid": "a" * 40, "baseRefName": "main",
         "comments": [{"body": _block(2) + "\n## Round 2 — CLEAN.\n"}]},
        {"number": OPEN_CARRIER_PR, "title": "a ladder still running",
         "state": "OPEN", "headRefOid": "c" * 40, "baseRefName": "main",
         "comments": [{"body": _block(1)}]},
        # A non-carrier, so the enumerator's predicate has something to reject:
        # this one merely mentions the ledger and carries no fence.
        {"number": 424244, "title": "mentions the ledger only",
         "state": "MERGED", "headRefOid": "d" * 40, "baseRefName": "main",
         "comments": [{"body": "the audit-claims ledger is discussed here\n"}]},
    ])


# --------------------------------------------------------------------------- #
# The taxonomy, pinned TWO-WAY
# --------------------------------------------------------------------------- #

def test_the_taxonomy_is_FROZEN_and_pinned_two_way(lsr):
    """🔴 The closed taxonomy is the whole claim of this instrument.

    Two-way: a class the report cannot print, or a printed row naming no class,
    both fail. The brief that commissioned this named five starting buckets and
    said to refine and FREEZE; this is the freeze, in code rather than in prose.
    """
    assert lsr.CLASSES == ("attribution-gate", "clean-round",
                           "operator-decision", "stated-criterion",
                           "unstated"), lsr.CLASSES
    assert lsr.EXCLUDED == ("not-terminated", "no-terminal-round"), lsr.EXCLUDED

    # `merged-anyway` was PROPOSED and deliberately rejected: every merged
    # carrier's ladder is followed by a merge, so the merge discriminates nothing
    # about the stop. If someone adds it, this fails and they must argue for it.
    assert "merged-anyway" not in lsr.CLASSES + lsr.EXCLUDED

    # Every class must be REACHABLE from `classify_text`, and no class may be
    # reachable that is not in the ledger.
    reachable = set()
    for text in [
        "Stopped on the payload-attribution gate.",
        "Round 3 — CLEAN.",
        "## Ladder CLOSED at round 2 — by operator decision",
        "The ladder stops here.",
        "Round 3 fixed four findings.",
    ]:
        reachable.add(lsr.classify_text(text)[0])
    assert reachable == set(lsr.CLASSES), (
        f"classify_text can only reach {sorted(reachable)} — the ledger claims "
        f"{sorted(lsr.CLASSES)}"
    )


def test_every_class_has_a_ROW_IN_THE_RENDERED_RATE(lsr):
    """A class counted but never printed is a number nobody can read."""
    rows = [lsr.Carrier(1, "MERGED", [1], 1, 0, False, False, k, ["s"], False,
                        [], None) for k in lsr.CLASSES]
    rendered = lsr.render([("r", rows)], [])
    for k in lsr.CLASSES:
        assert k in rendered, f"{k} is never printed"
    assert "DENOMINATOR = 5" in rendered


# --------------------------------------------------------------------------- #
# The attribution-gate extractor
# --------------------------------------------------------------------------- #

# Real devrc text (public, and already quoted in the 2026-09-04 review).
_GATE_POS = [
    # devrc #1132, quoted verbatim in claudedocs/audit-ladder-review-2026-09-04.md
    "Stopped on the payload-attribution gate, not on a clean round.",
    # devrc #1427
    "The ladder STOPPED on the payload-attribution gate.",
    # devrc #1285
    "Two consecutive zero-payload rounds, so the gate fires.",
    # devrc #1435
    "the attribution gate overrides that, and it has now fired",
]
_GATE_NEG = [
    # 🔴 devrc #1599 — stops on a STATED CRITERION and mentions the gate as
    # CONTEXT. `firing` must not satisfy a pattern that wants `fires`/`fired`.
    "The attribution gate is one round from firing.",
    # 🔴 devrc #1508 — the gate is named and NEGATED, in the comment of a ladder
    # that declared no stop at all.
    "so the attribution gate has not fired and the ladder has not left the PR",
    # The skill's own prose about a prose PR, which quotes the gate by name.
    "For a docs PR the attribution gate cannot fire at all.",
    # A stop AND a gate mention in one sentence, with the gate negated.
    "The ladder stops here; the attribution gate never fired in this PR.",
]


def test_the_gate_pattern_is_pinned_BOTH_ways(lsr):
    missed = [s for s in _GATE_POS if not lsr._GATE_RE.search(s)]
    assert not missed, f"cannot see a real gate attribution: {missed}"
    false_pos = [s for s in _GATE_NEG if lsr._GATE_RE.search(s)]
    assert not false_pos, f"over-matches: {false_pos}"


def test_the_gate_negatives_CAN_SEE_the_two_mutations_that_broke_it(lsr):
    """🔴 The mutation check, because a negative set that cannot express the
    mutation is a vacuous pin.

    MUTANT A — allow `firing` beside `fires`/`fired`. Must make devrc #1599's
    sentence match.
    MUTANT B — drop the negation temper from the gap. Must make devrc #1508's
    sentence match.
    """
    mutant_a = re.compile(
        r"(?ix) (?: payload[-\s]attribution \s+ gate | attribution \s+ gate )"
        r" [^.\n]{0,60}? (?: \b fires \b | \b fired \b | \b firing \b )")
    assert mutant_a.search(_GATE_NEG[0]), (
        "the `firing` negative cannot express mutant A — it would survive")

    mutant_b = re.compile(
        r"(?ix) (?: payload[-\s]attribution \s+ gate | attribution \s+ gate )"
        r" [^.\n]{0,60}? (?: \b fires \b | \b fired \b )")
    assert mutant_b.search(_GATE_NEG[1]), (
        "the negated-gate negative cannot express mutant B — it would survive")


# --------------------------------------------------------------------------- #
# The clean-round extractor
# --------------------------------------------------------------------------- #

_CLEAN_POS = [
    # devrc #1274, quoted verbatim in the review doc.
    "## Audit round 5 — **CLEAN. The ladder ends here.**",
    "No 🔴, no 🟡, no 🟢.",
    "1. No fixes were made this round: the audit returned no findings.",
    # RE-SYNTHESISED from the shape of civitai/talos-infra #1354 (a clean-round
    # stop that spells the assertion with `none`). No client words.
    "Round 3 produced none, so there is no fix commit and no round 4.",
]
_CLEAN_NEG = [
    # 🔴 THE ABSTRACT RULE, lowercase — this repo's own ladders quote it
    # constantly because several of these PRs are ABOUT the stop rule.
    "A clean round ENDS the ladder. Never run another round to confirm one.",
    # devrc #1570: the terminal round says the opposite.
    "Round 5 was **not clean**, so the ladder continues.",
    "The working tree is clean and `bash -n` is clean.",
    # 🔴 devrc #1524 / #1338 shape: no 🔴 but several 🟡, which ARE findings and
    # keep the ladder running by the skill's own rule.
    "It returned no 🔴, three 🟡 and three 🟢. All six were fixed.",
    # 🔴 RE-SYNTHESISED from vetrllc/vetr-api #200's shape: a grep sweep that
    # found nothing, in the comment of a round that reported two findings.
    "Swept the tests directory for other stale claims; found none.",
    "Round 5 was not CLEAN, so a round 6 followed.",
]


def test_the_clean_pattern_is_pinned_BOTH_ways(lsr):
    missed = [s for s in _CLEAN_POS if not lsr._CLEAN_RE.search(s)]
    assert not missed, f"cannot see a real clean-round assertion: {missed}"
    false_pos = [s for s in _CLEAN_NEG if lsr._CLEAN_RE.search(s)]
    assert not false_pos, f"over-matches: {false_pos}"


def test_the_clean_negatives_CAN_SEE_the_three_mutations_that_broke_it(lsr):
    """MUTANT A — fold case on `CLEAN`. Must make the ABSTRACT RULE match, which
    is the single most common sentence in this corpus.
    MUTANT B — accept `no 🔴` as a finding-free round. Must make the
    three-🟡 negative match.
    MUTANT C — add `found` to the `none` verbs. Must make the grep-sweep
    negative match.
    """
    mutant_a = re.compile(r"(?i)\bclean\b")
    assert mutant_a.search(_CLEAN_NEG[0]), "the abstract-rule negative is vacuous"

    mutant_b = re.compile(
        r"(?i)\b(?:returned|found|produced)\s+(?:no|zero)\s+(?:findings?|🔴)")
    assert mutant_b.search(_CLEAN_NEG[3]), (
        "the no-🔴-but-🟡 negative cannot express mutant B")

    mutant_c = re.compile(r"(?i)\b(?:returned|produced|found)\s+none\b")
    assert mutant_c.search(_CLEAN_NEG[4]), (
        "the grep-sweep negative cannot express mutant C")


# --------------------------------------------------------------------------- #
# The stop-declared extractor
# --------------------------------------------------------------------------- #

_STOP_POS = [
    "## 🔴 The ladder stops here, and this is the stated reason",   # devrc #1599
    "## Round 5 — fixes landed · 🔴 LADDER STOPPED",                # devrc #1439
    "I am not running a round 6 to confirm it.",                    # devrc #1274
    "so per the stop rule the ladder stops",                        # devrc #1274
    # RE-SYNTHESISED shapes (external repos): a declared last round, and a
    # stop-here with no ladder noun.
    "Intended as the last round of this ladder.",
    "STOPPING HERE, with the remaining item recorded.",
]
_STOP_NEG = [
    "A clean round ENDS the ladder. Never run another round to confirm one.",
    # 🔴 RE-SYNTHESISED from civitai/talos-infra #1479: a heading explaining why
    # the NEXT round IS running. The opposite of a stop, in stop vocabulary.
    "Why round 4 is running rather than stopping here.",
    "the attribution gate has not fired and the ladder has not left the PR",
    "The old citation range stopped short of the code it describes.",
    "Instead of stopping here, round 4 re-ran the whole matrix.",
]


def test_the_stop_pattern_is_pinned_BOTH_ways(lsr):
    missed = [s for s in _STOP_POS if not lsr._STOP_RE.search(s)]
    assert not missed, f"cannot see a declared stop: {missed}"
    false_pos = [s for s in _STOP_NEG if lsr._STOP_RE.search(s)]
    assert not false_pos, f"over-matches: {false_pos}"


def test_the_stop_negatives_CAN_SEE_the_contrast_mutation(lsr):
    """MUTANT — drop the `rather than` / `instead of` / `not` lookbehinds. Must
    make BOTH contrasted negatives match, which is how #1479 was scored as a
    stop by a round that explicitly kept going."""
    mutant = re.compile(r"(?i)\b(?:stops|ends|stopping|stopped)\s+here\b")
    assert mutant.search(_STOP_NEG[1]), "the rather-than negative is vacuous"
    assert mutant.search(_STOP_NEG[4]), "the instead-of negative is vacuous"


# --------------------------------------------------------------------------- #
# The operator-decision extractor — BOTH halves required
# --------------------------------------------------------------------------- #

_OPERATOR_POS = [
    # devrc #1455, its own words.
    "Round 2 was not run before merge — it was skipped by operator instruction, "
    "not by a clean round.",
    # devrc #1404, its own words.
    "## Ladder CLOSED at round 2 — by operator decision",
    # RE-SYNTHESISED from ZacxDev/homelab-infra #623's shape: a stop attributed
    # to an explicit human decision, with an adjective between `by` and
    # `operator`.
    "## THE LADDER WAS STOPPED HERE BY AN EXPLICIT OPERATOR DECISION",
]
_OPERATOR_NEG = [
    # 🔴 devrc #1406 — an operator decision about a DESIGN FORK, in the terminal
    # comment of a ladder that declared no stop.
    "### `cairn-validate` — the operator's call on the writer-invocation fork",
    # 🔴 devrc #1404's EARLIER comment: an operator decision about a FINDING.
    "Claimed NOT addressed: finding 5. Left open as an operator decision.",
    "The writer-invocation fork, decided by the operator: ship a devrc-only CLI.",
]


def test_an_operator_decision_needs_BOTH_halves(lsr):
    missed = [s for s in _OPERATOR_POS if not lsr._OPERATOR_RE.search(s)]
    assert not missed, f"cannot see an operator stop: {missed}"
    false_pos = [s for s in _OPERATOR_NEG if lsr._OPERATOR_RE.search(s)]
    assert not false_pos, f"over-matches: {false_pos}"


def test_the_operator_negatives_CAN_SEE_the_bare_phrase_mutation(lsr):
    """MUTANT — accept an operator act with NO stop context. Must make the
    finding-disposition negative match, which is how a per-finding note would be
    promoted into a reason the ladder ended."""
    mutant = re.compile(r"(?i)\boperator'?s?\s+(?:decision|instruction|call)\b")
    assert mutant.search(_OPERATOR_NEG[1]), (
        "the finding-disposition negative cannot express the mutation")
    assert mutant.search(_OPERATOR_NEG[0])


# --------------------------------------------------------------------------- #
# The #1157 rationale extractor
# --------------------------------------------------------------------------- #

_RATIONALE_POS = [
    "## Why the ladder stops here — stated, not implicit",          # devrc #1439
    "Rounds 3, 4 and 5 changed zero *executable* payload.",         # devrc #1439
    "Remaining round-3 items deliberately **not** fixed, so the next reader "
    "knows they are open rather than absent.",                      # devrc #1599
    "The attribution gate is one round from firing.",               # devrc #1599
    "this is the stated reason",                                    # devrc #1599
    # RE-SYNTHESISED shapes (external repos).
    "Both findings were scaffolding/prose.",
    "Round 7 found **0 executable payload lines**.",
    "For this PR the payload is prose, so the gate cannot fire.",
]
_RATIONALE_NEG = [
    # 🔴 RE-SYNTHESISED from ZacxDev/homelab-infra #786's shape: the comment
    # POINTS AT a criterion stated somewhere else instead of stating it. Under
    # #1157 that is exactly what does NOT count.
    "Round 6 — the ladder stops here, on the criterion stated before it ran.",
    # 🔴 RE-SYNTHESISED from civitai/talos-infra #1455's shape: the rationale is
    # explicitly located in ANOTHER comment.
    "The ladder is being stopped here (rationale in the comment above).",
    # A bare stop, which is the thing #1157 says is not enough on its own.
    "## The ladder stops here.",
]


def test_the_rationale_pattern_is_pinned_BOTH_ways(lsr):
    missed = [s for s in _RATIONALE_POS if not lsr._RATIONALE_RE.search(s)]
    assert not missed, f"cannot see a written rationale: {missed}"
    false_pos = [s for s in _RATIONALE_NEG if lsr._RATIONALE_RE.search(s)]
    assert not false_pos, f"over-matches: {false_pos}"


def test_the_rationale_negatives_CAN_SEE_the_pointer_mutation(lsr):
    """MUTANT — accept a bare `criterion` / `rationale` anywhere. Must make both
    POINTER negatives match, which would turn "the reason is written down
    elsewhere" into "#1157 satisfied" — the precise confusion #1157 exists to
    prevent."""
    mutant = re.compile(r"(?i)\b(?:criterion|rationale)\b")
    assert mutant.search(_RATIONALE_NEG[0]), "the #786 pointer negative is vacuous"
    assert mutant.search(_RATIONALE_NEG[1]), "the #1455 pointer negative is vacuous"


# --------------------------------------------------------------------------- #
# The SUBJECT check — a true sentence about the wrong round
# --------------------------------------------------------------------------- #

def test_a_clean_claim_about_an_EARLIER_round_does_not_classify_THIS_one(lsr, ad):
    """🔴 The deepest error class: not a pattern too wide, but a TRUE sentence
    about the WRONG SUBJECT.

    RE-SYNTHESISED from civitai/talos-infra #1449, terminal round 5: its comment
    reports one 🟡 for round 5 and a finding-free round 4. Scored `clean-round`
    until matches were filtered by the round their sentence is about.
    """
    comment = (
        "## Audit round 5 — the ladder ends here, and here is why\n"
        "\n"
        "Round 5 returned one 🟡. Round 4 returned no 🔴 and no 🟡.\n"
        "\n" + _block(5)
    )
    c = lsr.classify_carrier(ad, _facts(1, [comment]))
    assert c.terminal_round == 5
    assert c.label != lsr.CLEAN, (
        f"a clean assertion about ROUND 4 classified round 5: {c.spans}")
    assert c.label == lsr.STATED, (c.label, c.spans)

    # The CONTROL on the filter itself: the same sentence, relabelled as the
    # terminal round, MUST classify clean. Without this the filter could be
    # rejecting everything and the test above would still pass.
    same_round = comment.replace("Round 4 returned", "Round 5 also returned")
    c2 = lsr.classify_carrier(ad, _facts(2, [same_round]))
    assert c2.label == lsr.CLEAN, (c2.label, c2.spans)


def test_a_stop_naming_the_NEXT_round_is_KEPT(lsr, ad):
    """🔴 The converse control, and a measured regression.

    devrc #1455's terminal round is 1 and its stop is stated as "ROUND 2 was not
    run …". A filter demanding the sentence name the TERMINAL round deleted it.
    Saying the NEXT round did not run is the canonical way to say a ladder
    stopped, so only EARLIER-round sentences are dropped.
    """
    comment = (
        _block(1) +
        "\nRound 2 was not run before merge — it was skipped by operator "
        "instruction, not by a clean round.\n"
    )
    c = lsr.classify_carrier(ad, _facts(1455, [comment]))
    assert c.terminal_round == 1
    assert c.label == lsr.OPERATOR, (c.label, c.spans)


def test_an_enumeration_of_rounds_names_ALL_of_them(lsr):
    """"Rounds 3, 4 and 5 changed zero executable payload" is about round 5 too.
    A reader of only the first number would throw devrc #1439's rationale away.
    """
    assert lsr._rounds_named("Rounds 3, 4 and 5 changed zero payload") == {3, 4, 5}
    assert lsr._rounds_named("Round 4 returned no findings") == {4}
    assert lsr._rounds_named("Remaining round-3 items") == {3}
    assert lsr._rounds_named("no round is named here") == set()


# --------------------------------------------------------------------------- #
# Terminal-round selection, and the two things that are NOT classified
# --------------------------------------------------------------------------- #

def test_the_terminal_round_is_the_LAST_comment_reporting_the_HIGHEST_round(
        lsr, ad):
    comments = [
        _block(1) + "\nround 1 notes\n",
        _block(3) + "\n## The ladder stops here\n",
        _block(2) + "\nround 2, posted late and out of order\n",
        "## Audit round 3 — a correction: CLEAN after all\n" + _block(3),
    ]
    c = lsr.classify_carrier(ad, _facts(7, comments))
    assert c.rounds == [1, 2, 3]
    assert c.terminal_round == 3
    assert c.terminal_index == 3, "the LAST comment reporting round 3 is read"
    assert c.label == lsr.CLEAN, (c.label, c.spans)


def test_an_OPEN_pr_is_NOT_TERMINATED_and_leaves_the_denominator(lsr, ad):
    """A ladder on an open PR has not stopped. Classifying why it stopped would
    invent a stop, and counting it `unstated` would inflate the headline."""
    c = lsr.classify_carrier(
        ad, _facts(9, [_block(2) + "\nstill going\n"], state="OPEN"))
    assert c.label == lsr.NOT_TERMINATED
    assert "category error" in c.reason
    rendered = lsr.render([("r", [c])], [])
    assert "DENOMINATOR = 0" in rendered
    assert "NOTHING TO RATE" in rendered, (
        "a rate over an empty denominator must be refused, not printed as 0%")


def test_a_carrier_with_NO_FENCE_says_it_is_not_a_ladder(lsr, ad):
    """🔴 MEASURED on devrc #1440: `find_carriers` tests for the SUBSTRING
    `audit-claims`, so a PR that merely DISCUSSES the ledger is enumerated as a
    carrier. That is an OVER-count direction in a population whose every other
    caveat is a floor, so it is named rather than classified."""
    c = lsr.classify_carrier(ad, _facts(1440, [
        "1. **Round 0 could move the ladder** — `--emit-claims` sits below every "
        "round gate, so an `audit-claims` block can be produced by round 0.\n"]))
    assert c.label == lsr.NO_TERMINAL_ROUND
    assert "SUBSTRING" in c.reason and "not a ladder" in c.reason


def test_a_FENCE_with_no_round_number_is_a_DIFFERENT_refusal(lsr, ad):
    """The two reasons must not share a message: one says the carrier is not a
    ladder, the other that a real ladder's header is unusable."""
    c = lsr.classify_carrier(ad, _facts(11, [
        "```audit-claims audited=aaaaaaaa..bbbbbbbb\n1. a claim\n```\n"]))
    assert c.label == lsr.NO_TERMINAL_ROUND
    assert "SUBSTRING" not in c.reason
    assert "no `round=<n>`" in c.reason


def test_a_round_is_RECOVERED_from_a_block_the_shared_parser_REJECTED(lsr, ad):
    """🔴 MEASURED on devrc #1586: its header is `audit-claims round=1
    audited=<a>..<b>`, perfectly well formed, and `parse_claims_blocks` returns
    NOTHING because the body's claims read `🔴-1 FIXED — …` rather than `1. …`.
    That is right for the DISPATCHER and wrong for this question, so the fallback
    lives here — and it reuses audit_dispatch's OWN header objects, not a copy.
    """
    comment = ("```audit-claims round=4 audited=aaaaaaaa..bbbbbbbb\n"
               "🔴-1 FIXED — the false invariant is deleted.\n"
               "```\n## The ladder stops here\n")
    blocks, malformed = ad.parse_claims_blocks([comment])
    assert blocks == [] and malformed, (
        "this fixture must be a block the SHARED parser rejects, or it is "
        "testing nothing")
    c = lsr.classify_carrier(ad, _facts(1586, [comment]))
    assert c.terminal_round == 4
    assert c.terminal_recovered is True
    rendered = lsr.render([("r", [c])], [])
    assert "RECOVERED from a block the shared parser" in rendered


def test_unstated_is_the_DEFAULT_for_a_report_that_says_nothing(lsr, ad):
    """🔴 `unstated` is a legitimate and measured-common answer — 64.9% of 191
    terminated carriers on 2026-09-13. A classifier that reached for a
    better-sounding category here would be reporting a hypothesis as a finding.
    """
    c = lsr.classify_carrier(ad, _facts(13, [
        "## Round 6 (delta, blind) — 3 🟡, 2 🟢, no 🔴. Fixed in `fb1aef02`.\n"
        "\nEach fix is described below.\n" + _block(6)]))
    assert c.label == lsr.UNSTATED
    rendered = lsr.render([("r", [c])], [])
    assert "nothing in this comment says why the ladder stopped" in rendered


def test_the_PRs_STATE_is_printed_so_merged_anyway_is_not_needed(lsr, ad):
    """`merged-anyway` was rejected as a class; the information it would have
    carried is the PR's STATE, which every row prints."""
    c = lsr.classify_carrier(ad, _facts(14, [_block(1) + "\nfixes\n"]))
    rendered = lsr.render([("r", [c])], [])
    assert "MERGED" in rendered and lsr.UNSTATED in rendered
    assert "merged-anyway" in rendered, (
        "the rejection itself must be stated in the output, or the next reader "
        "re-proposes it")


# --------------------------------------------------------------------------- #
# The rate, its denominators, and the floors
# --------------------------------------------------------------------------- #

def test_both_rates_print_their_DENOMINATOR(lsr):
    rows = [
        lsr.Carrier(1, "MERGED", [1], 1, 0, False, False, lsr.STATED, ["s"],
                    True, ["why"], None),
        lsr.Carrier(2, "MERGED", [1], 1, 0, False, False, lsr.STATED, ["s"],
                    False, [], None),
        lsr.Carrier(3, "MERGED", [1], 1, 0, False, False, lsr.UNSTATED, [],
                    False, [], None),
        lsr.Carrier(4, "OPEN", [1], 1, 0, False, False, lsr.NOT_TERMINATED, [],
                    False, [], "open"),
    ]
    rendered = lsr.render([("r", rows)], [])
    assert "DENOMINATOR = 3 TERMINATED carrier(s)" in rendered, rendered
    assert "1 not-terminated (OPEN)" in rendered
    assert "DENOMINATOR = 2 carrier(s) classified `stated-criterion`" in rendered
    assert "rationale WRITTEN   1 (50.0%)" in rendered
    assert "rationale NOT FOUND 1 (50.0%)" in rendered
    assert "unstated             1 (33.3%)" in rendered


def test_a_ZERO_sub_rate_denominator_is_REFUSED_not_printed_as_0_percent(lsr):
    """🔴 "no carrier stated a criterion" and "0% of them wrote a rationale" read
    the same and mean opposites."""
    rows = [lsr.Carrier(1, "MERGED", [1], 1, 0, False, False, lsr.UNSTATED, [],
                        False, [], None)]
    rendered = lsr.render([("r", rows)], [])
    assert "NO CARRIER was classified `stated-criterion`" in rendered
    assert "rationale WRITTEN" not in rendered


def test_the_FLOORS_are_printed_in_the_OUTPUT_not_only_the_docstring(lsr):
    """🔴 The brief's requirement, and a rule of this repo: a caveat that lives
    only in a docstring is a caveat the reader of the numbers never sees."""
    rows = [lsr.Carrier(1, "MERGED", [1], 1, 0, False, False, lsr.UNSTATED, [],
                        False, [], None)]
    rendered = lsr.render([("r", rows)], [])
    assert "does NOT return REVIEW comments" in rendered
    assert "A FLOOR, NEVER A CENSUS" in rendered
    # The direction of the whole-comment bias, which makes the headline a CEILING
    # rather than a floor — the two are opposite claims.
    assert "CEILING" in rendered
    assert "PATTERNS OVER WORDS" in rendered
    assert "client-owned" in rendered


def test_the_scan_limit_warning_survives_into_the_report(lsr):
    """Three of the seven repos hit a 400-PR scan limit on 2026-09-13, so their
    carrier counts are partial. The note comes from the shared enumerator; this
    asserts the report does not swallow it."""
    note = ("repo: scanned 400 PR(s) ... ⚠ The scan HIT ITS LIMIT (400), so "
            "older PRs were not examined at all")
    rendered = lsr.render([("repo", [])], [note])
    assert "HIT ITS LIMIT" in rendered


# --------------------------------------------------------------------------- #
# Structural: one grammar, one enumerator
# --------------------------------------------------------------------------- #

def test_it_REUSES_the_shared_parser_and_enumerator_rather_than_copying_them():
    """🔴 A second copy of the claims-block grammar is a second thing to get
    wrong, and the carrier population has already been hand-built twice in this
    arc. Asserted structurally: this script must not carry a fence regex, and
    must call both shared entry points."""
    src = SCRIPT.read_text(encoding="utf-8")
    copies = [
        ln for ln in src.splitlines()
        if "audit-claims" in ln and "re.compile" in ln
    ]
    assert not copies, f"this script re-types the fence grammar: {copies}"
    # 🔴 THE TWO `assert "<name>" in src` LINES THAT USED TO BE HERE ARE DELETED,
    # AND THEIR DELETION IS THE POINT. `assert "find_carriers" in src` passed
    # for the entire life of #1643 while EVERY `--repo` run died on
    # `TypeError: find_carriers() missing 1 required positional argument`. A
    # grep for a NAME cannot see whether the name is called, called correctly,
    # or called at all — it matched the comment prose as readily as the call.
    # The two tests below invoke both entry points instead.
    code = [ln for ln in src.splitlines()
            if "gh" in ln and "pr" in ln and "list" in ln
            and not ln.lstrip().startswith(("#", "*", "⚠", "🔴"))
            and '"""' not in ln and "`" not in ln]
    assert not code, (
        "this script builds its own `gh pr list` instead of going through "
        f"`find_carriers` — two populations, one report: {code}")

    # 🔴 BOTH ASSERTIONS ABOVE ARE REASSURING ZEROES, so each needs a POSITIVE
    # CONTROL: an empty match set is indistinguishable from a predicate wired to
    # nothing. Feed each one a line it MUST match and watch the count move.
    def _copies(text):
        return [ln for ln in text.splitlines()
                if "audit-claims" in ln and "re.compile" in ln]

    def _own_gh(text):
        return [ln for ln in text.splitlines()
                if "gh" in ln and "pr" in ln and "list" in ln
                and not ln.lstrip().startswith(("#", "*", "⚠", "🔴"))
                and '"""' not in ln and "`" not in ln]

    assert _copies('_F = re.compile(r"```audit-claims")'), (
        "the fence-copy predicate matches nothing — its zero above is not "
        "evidence")
    assert _own_gh('    cmd = ["gh", "pr", "list", "--repo", repo]'), (
        "the own-`gh pr list` predicate matches nothing — its zero above is "
        "not evidence")
    # …and the same two predicates over the real source must still be the zero
    # asserted above, so the controls cannot be read as the result.
    assert _copies(src) == [] and _own_gh(src) == []


def test_the_live_repo_path_actually_CALLS_the_shared_enumerator(lsr):
    """🔴 REGRESSION for the defect the deleted grep could not see.

    `main`'s `--repo` branch — everything that is not `--facts-file` — shipped
    in #1643 as

        lrc.find_carriers(runner, repo, limit=args.limit)

    against `find_carriers(ad, runner, repo, limit=300, state="all")`. Three
    required positionals, two supplied, so EVERY live run was

        TypeError: find_carriers() missing 1 required positional argument: 'repo'

    rc 1, reproduced on 2026-09-17 at `93685e1d`. The tool's live path had never
    run once. The guard that was supposed to catch it asserted the STRING
    `find_carriers` appeared in the source.

    So this one drives `main` down that exact branch with an injected runner.
    The real `find_carriers` is invoked, so an arity or keyword mismatch is a
    TypeError raised HERE. It needs no network: the only `gh` call the branch
    makes goes through the runner.
    """
    spawned = []

    def runner(cmd, cwd=None):    # noqa: ARG001
        spawned.append(cmd)
        assert cmd[0] == "gh", cmd
        return 0, _gh_pr_list_payload(), ""

    out = []

    class _S:
        def write(self, s):
            out.append(s)

    rc = lsr.main(["--repo", "owner/repo", "--limit", "50"], runner=runner,
                  out_stream=_S())
    text = "".join(out)

    assert rc == lsr.EXIT_OK, f"the live path did not complete: rc={rc}\n{text}"
    # The POSITIVE CONTROL for the enumerator: it must have spawned the one
    # `gh pr list` and come back with carriers. A run that enumerated NOTHING
    # would return EXIT_NOTHING_MEASURABLE above, so a reassuring zero cannot
    # pass as a pass here.
    assert len(spawned) == 1, f"expected ONE `gh pr list`, got {spawned}"
    assert f"#{MERGED_CARRIER_PR}" in text, text
    assert f"#{OPEN_CARRIER_PR}" in text, text
    assert "2 carrier(s)" in text, text
    # …and the classification really ran over the enumerated population, rather
    # than the report merely echoing the numbers.
    assert "clean-round" in text and "not-terminated" in text, text


def test_main_BINDS_find_carriers_with_the_parser_module_and_state_all(lsr,
                                                                      monkeypatch):
    """🔴 The signature pin, one level below the behavioural guard above.

    The behavioural test proves the call completes; this proves it is the call
    that was MEANT — the arguments are checked against the callee's real
    signature by `inspect.signature().bind()`, which is what fails loudly on the
    #1643 shape, and the bound values are then asserted by IDENTITY:

      * `ad` must be the `audit-dispatch` module. It is a parameter precisely so
        the fence grammar is not re-typed; passing anything else would give the
        enumerator a different grammar than the classifier uses.
      * `state` must resolve to `"all"`. This consumer's `not-terminated` class
        exists only for OPEN PRs; a narrower state drops them from the
        population and every live ladder is classified as though it had stopped.

    The wrapper DELEGATES to the real function, so this is a pin on the live
    call and not a stub the production code could diverge from.
    """
    lrc, ad_mod = lsr.load_siblings()
    real = lrc.find_carriers
    sig = inspect.signature(real)
    seen = []

    def recording(*a, **kw):
        bound = sig.bind(*a, **kw)      # TypeError on an arity/keyword mismatch
        bound.apply_defaults()
        seen.append(dict(bound.arguments))
        return real(*a, **kw)

    monkeypatch.setattr(lrc, "find_carriers", recording)
    monkeypatch.setattr(lsr, "load_siblings", lambda *a, **k: (lrc, ad_mod))

    def runner(cmd, cwd=None):    # noqa: ARG001
        return 0, _gh_pr_list_payload(), ""

    class _S:
        def write(self, s):
            pass

    rc = lsr.main(["--repo", "owner/repo", "--limit", "50"], runner=runner,
                  out_stream=_S())
    assert rc == lsr.EXIT_OK

    assert len(seen) == 1, f"`find_carriers` was called {len(seen)} time(s)"
    call = seen[0]
    assert call["ad"] is ad_mod, (
        "`find_carriers` was not handed the `audit-dispatch` module, so the "
        f"enumerator's fence grammar is not the classifier's: {call['ad']!r}")
    assert call["repo"] == "owner/repo", call
    assert call["limit"] == 50, call
    assert call["state"] == "all", (
        "this consumer needs state=all — a narrower state drops OPEN PRs from "
        f"the population and `not-terminated` becomes unreachable: {call}")


def test_find_carriers_carries_STATE_through_for_this_consumer(lsr, ad):
    """🔴 The seam guard. This script's `not-terminated` class is only possible
    because `find_carriers` requests `state`; drop that field and every OPEN
    ladder is silently classified as though it had stopped.

    ⚠ This USED TO BE two `in src` greps against `ladder-range-coverage.py` —
    `"number,comments,headRefOid,baseRefName,title,state" in src` and
    `'"state": pr.get("state")' in src`. Both are walkable by rewording (a
    reordered `--json` field list breaks the first while requesting `state`
    perfectly well) and neither could observe whether this consumer ever reached
    the enumerator at all — which, per the regression above, it never did. So
    the seam is now driven end to end: enumerate, then classify the enumerated
    dicts, and require the OPEN/MERGED split to survive the whole way.
    """
    lrc, _ad = lsr.load_siblings()
    cmds = []

    def runner(cmd, cwd=None):    # noqa: ARG001
        cmds.append(cmd)
        return 0, _gh_pr_list_payload(), ""

    carriers, note = lrc.find_carriers(ad, runner, "owner/repo", limit=50,
                                       state="all")

    assert len(cmds) == 1, f"the enumerator spawned {cmds}"
    cmd = cmds[0]
    assert "--state" in cmd and cmd[cmd.index("--state") + 1] == "all", cmd
    fields = cmd[cmd.index("--json") + 1].split(",")
    assert "state" in fields, (
        "`find_carriers` no longer requests `state`, so "
        f"`ladder-stop-rationale.py` cannot tell a stopped ladder from a live "
        f"one: {fields}")

    by_pr = {c["pr"]: c for c in carriers}
    assert sorted(by_pr) == sorted([MERGED_CARRIER_PR, OPEN_CARRIER_PR]), (
        f"the enumerated population is wrong: {sorted(by_pr)}")
    assert by_pr[OPEN_CARRIER_PR]["state"] == "OPEN", (
        "`find_carriers` requests `state` but does not pass it through")
    assert by_pr[MERGED_CARRIER_PR]["state"] == "MERGED", by_pr

    # The seam, both directions: OPEN is EXCLUDED as not-terminated, MERGED is
    # classified. One direction alone would pass against a classifier that
    # answered `not-terminated` for everything, or for nothing.
    assert lsr.classify_carrier(ad, by_pr[OPEN_CARRIER_PR]).label == \
        lsr.NOT_TERMINATED
    assert lsr.classify_carrier(ad, by_pr[MERGED_CARRIER_PR]).label == \
        lsr.CLEAN
    assert "2 carry" in note, note


def test_the_script_runs_and_its_usage_needs_no_network():
    p = subprocess.run([sys.executable, str(SCRIPT), "--help"],
                       capture_output=True, text=True, check=False)
    assert p.returncode == 0, p.stderr
    assert "--facts-file" in p.stdout
    assert "--repo" in p.stdout


def test_facts_file_mode_consults_no_gh(lsr, tmp_path):
    """The hermetic door: the whole classifier must be drivable with no `gh` and
    no network, or none of the guards above is evidence about the real run."""
    import json as _json
    f = tmp_path / "facts.json"
    f.write_text(_json.dumps([
        {"pr": 1, "state": "MERGED",
         "comments": [_block(2) + "\n## The ladder stops here — the payload is "
                                  "prose, so the gate cannot fire.\n"]},
    ]), encoding="utf-8")

    def refusing_runner(cmd, cwd=None):   # noqa: ARG001
        raise AssertionError(f"--facts-file mode spawned {cmd}")

    out = []

    class _S:
        def write(self, s):
            out.append(s)

    rc = lsr.main(["--facts-file", str(f)], runner=refusing_runner,
                  out_stream=_S())
    assert rc == lsr.EXIT_OK
    text = "".join(out)
    assert "stated-criterion" in text
    assert "#1157 rationale: WRITTEN" in text
    assert "no `gh` was consulted" in text
