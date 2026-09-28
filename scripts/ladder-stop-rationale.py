#!/usr/bin/env python3
"""WHY each audit ladder STOPPED, classified from its TERMINAL round's comment.

    scripts/ladder-stop-rationale.py --repo innovation-upstream/devrc
    scripts/ladder-stop-rationale.py --repo ZacxDev/homelab-infra --limit 400
    scripts/ladder-stop-rationale.py --facts-file <json>   # no `gh`, no network

WHY THIS EXISTS
---------------
`claudedocs/audit-ladder-review-2026-09-04.md` lists, under *"Cannot see at
all"*:

    Why a ladder stopped — NOT AT SCALE, though it is sometimes stated outright.
    … The real limit is that this is PROSE IN A COMMENT, extracted BY HAND for
    two PRs, so it was not mined across all carriers and no rate is claimed.

and, separately:

    Whether the escape hatch's stated rationale was actually written (#1157's
    requirement). Not measured; it needs prose extraction from every terminal
    round's summary.

Its open item 4 closes *"when the terminal round's summary is classified for
every carrier and the rate is published"*. This script is that classification.

🔴 IT IS A SCRIPT AND NOT A PROCEDURE FOR ONE REASON. That review's own churn
instrument was a scratchpad variant that no longer exists, so none of its numbers
can be re-derived from the tree. Every number this prints is re-derivable by
re-running it.

THE FROZEN TAXONOMY — five classes, and the default is the honest one
--------------------------------------------------------------------
Classified from the terminal round's comment, in this PRECEDENCE:

  attribution-gate   the stop is ATTRIBUTED to the payload-attribution gate.
                     First, not last, because the one real instance says
                     *"Stopped on the payload-attribution gate, not on a clean
                     round"* — a clean-first order reads that as clean-round.
                     🔴 It requires an ATTRIBUTION, never the gate's NAME: devrc
                     #1599 says *"The attribution gate is one round from
                     firing"* while stopping on something else entirely, so
                     `firing` must not match where `fires`/`fired` does.
  clean-round        the round asserts it found nothing. Mechanism 1 of the
                     skill: a clean round ENDS the ladder and is never
                     re-confirmed. A terminal round that reports no findings
                     stopped BECAUSE it was clean, whether or not it adds "and
                     so the ladder ends".
  operator-decision  a HUMAN overrode the ladder and the comment says so. Its own
                     class rather than folded into `stated-criterion`, because
                     conflating "the session applied the skill's escape hatch"
                     with "a human said merge it" would corrupt the one rate
                     #1157 asks for, and only the first carries #1157's
                     write-it-down obligation. ADDED after the first run over the
                     real corpus mis-read devrc #1455 — whose terminal comment
                     says the next round *"was skipped by operator instruction,
                     not by a clean round"* — as `unstated`.
  stated-criterion   a stop is DECLARED for a basis that is none of the above —
                     the skill's prose escape hatch, and anything else a session
                     wrote down.
  unstated           nothing in the terminal comment says why the ladder
                     stopped.

🔴 `unstated` IS A LEGITIMATE ANSWER AND IS THE DEFAULT. A purpose found under
pressure to supply one is a hypothesis, not a finding.

🔴 `merged-anyway` WAS PROPOSED AND IS DELIBERATELY NOT IN THE TAXONOMY —
"stopped because it merged, with no stop rationale". Nothing in the record
discriminates it from `unstated`: EVERY merged carrier's ladder is followed by a
merge, so the merge cannot be evidence about the stop. Minting it would partition
`unstated` by OUTCOME while claiming to say something about CAUSE. The outcome is
not lost — every row prints the PR's state beside its classification, so a reader
who wants that split can have it without the tool asserting a mechanism.

NOT CLASSIFIED, and excluded from the rate's denominator rather than folded in:

  not-terminated     the PR is still OPEN, so its ladder has not stopped. Asking
                     why it stopped is a category error, and counting it as
                     `unstated` would invent a stop that has not happened.
  no-terminal-round  no comment on this PR names a round, so there is no
                     terminal round's summary to read. A carrier reaches this
                     only through a malformed fence; it is reported, never
                     silently dropped.

WHAT IT READS, AND THE FLOORS ON IT
-----------------------------------
⚠ `gh pr list --json comments` does **not** return REVIEW comments, so a block or
a round report posted as a review is invisible. Every count here is a FLOOR, and
the output says so rather than only this docstring. `--json reviews` exists and
is deliberately not used: it would change the population mid-arc, and the
enumerator is shared with `ladder-range-coverage.py`.

⚠ The classified text is the WHOLE terminal comment, not a "summary" section.
These reports carry no machine-identifiable summary region, and picking one would
be a second guess on top of the first. The consequence has a DIRECTION and it is
stated rather than hidden: a stop phrase anywhere in the comment counts, so
`unstated` is UNDER-counted and the compliance rate is a CEILING, not a floor.

⚠ Within that comment, a match is DISCARDED when its sentence names only EARLIER
rounds — a terminal report talks about its neighbours, and a finding-free claim
about round N−1 is not a clean round N. A LATER round number is kept: "round N+1
was not run" is how a ladder says it stopped. See `_ROUND_MENTION_RE`.

⚠ The extractors are patterns over WORDS, which `claude/RULES.md` says is
walkable by rewording. That is unavoidable here — the artefact under measurement
IS prose — so the mitigation is the one the census uses: every row prints the
MATCHED SPAN, each pattern is pinned two-way against real text, and the result is
reported as a floor on what was written down, never as a rate of what was
thought.

🔴 SPANS ARE VERBATIM PR TEXT. Four of the measured repos are client-owned. Do
not paste a span from one of those into this PUBLIC repo — counts, PR numbers and
classifications are fine; captured prose is not.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from collections import namedtuple
from pathlib import Path

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_NOTHING_MEASURABLE = 3

# --------------------------------------------------------------------------- #
# The shared core, imported rather than re-typed
# --------------------------------------------------------------------------- #
# 🔴 ONE BLOCK GRAMMAR, ONE CARRIER ENUMERATOR. `parse_claims_blocks` lives in
# `audit-dispatch.py` and `find_carriers` in `ladder-range-coverage.py`; both are
# loaded by path because their filenames carry hyphens. A second copy of either
# would be a second thing to get wrong, and the carrier population has already
# been hand-built twice in this arc.


def load_by_path(path, name):
    target = Path(path)
    if not target.exists():
        raise SystemExit(
            f"cannot find {target} — this script reuses its parser/enumerator "
            "rather than carrying a second copy."
        )
    spec = importlib.util.spec_from_file_location(name, target)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load {target} as a module")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def load_siblings(range_coverage=None, audit_dispatch=None):
    """-> (ladder_range_coverage module, audit_dispatch module)."""
    here = Path(__file__).resolve().parent
    lrc = load_by_path(range_coverage or here / "ladder-range-coverage.py",
                       "ladder_range_coverage")
    ad = lrc.load_audit_dispatch(audit_dispatch)
    return lrc, ad


# --------------------------------------------------------------------------- #
# Round detection
# --------------------------------------------------------------------------- #
# A comment REPORTS a round if it carries an `audit-claims` block (the round
# number is in the fence header, read by the shared parser) or if its prose names
# one. Both, because a round that posts no block still ran: the skill's round 0
# emits none by design, and a clean round sometimes posts prose only.
#
# 🔴 THIS IS NOT `ladder-range-coverage.py`'s `_ROUND_REF_RE`, AND THE DIFFERENCE
# IS DELIBERATE. That one reads COMMIT SUBJECTS, where `audit round 5` is a
# self-declaration by a fix. This one reads COMMENT BODIES, where the same words
# appear inside quoted rules and narration. So this pattern is anchored on the
# REPORT HEADING forms a round report actually uses, which is narrower.
_ROUND_HEADING_RE = re.compile(
    r"""(?mx)
    ^\#{1,6} \s* [^\n]*?                      # a markdown heading …
      \b audit \s+ round \s+ (?P<h>\d+) \b    # … naming the round
  | ^\#{1,6} \s* [^\n]*?
      \b round \s+ (?P<h2>\d+) \s*
      (?: \( \s* delta \s* \) \s* )?          # "Round 3 (delta) — …"
      (?: [-—–:]| \b (?:delta|re-?audit|audit) \b )
    """
)

# --------------------------------------------------------------------------- #
# The four extractors. EVERY ONE IS PINNED TWO-WAY in
# scripts/tests/test_ladder_stop_rationale.py.
# --------------------------------------------------------------------------- #

# 🔴 AN ATTRIBUTION, NOT A NAME, AND NOT A NEGATED ONE. Two measured traps, both
# on real devrc comments:
#   * `\bfires?\b|\bfired\b` and NOT `firing` — #1599 says "The attribution gate
#     is one round from firing" in the very comment that stops on something else,
#     so a name-plus-`firing` match reads that PR's stated criterion as the gate;
#   * the gap between the two halves is NEGATION-FREE — #1508 says "the
#     attribution gate HAS NOT FIRED", which an untempered `[^.\n]{0,60}` gap
#     classified as a gate stop. A pattern that cannot see `not` reports the
#     OPPOSITE of the sentence it matched.
# `_NEG_FREE` is the tempered-dot that closes the second: it consumes any
# non-sentence-ending character that is not the start of a negation word.
_NEG_FREE = r"""(?: (?! \b (?: not | never | cannot | can't | isn't | hasn't
                            | won't | no ) \b ) [^.\n] )"""
_GATE_RE = re.compile(
    r"""(?ix)
    # 🔴 THE NEGATION CAN SIT ON EITHER SIDE OF THE GATE'S NAME, so there is a
    # temper on both. "The ladder stops here; the attribution gate never fired"
    # is a stop that is explicitly NOT the gate, and the leading tempered gap
    # cannot see a word that comes later — hence the trailing lookahead.
    # ⚠ REQUIRING `on the gate` instead was tried and REVERTED: it deleted three
    # real gate stops whose wording is "stops here — the attribution gate, not a
    # clean round" (no preposition at all). Narrowing on the link word punished
    # the phrasing rather than the defect.
    (?: stopped | stopping | stops | ended | ends | end )
        """ + _NEG_FREE + r"""{0,80}?
        (?: payload[-\s]attribution | attribution \s+ gate | payload \s+ gate )
        (?! [^.\n]{0,40}? \b (?: never | not | cannot ) \b [^.\n]{0,12} fir )
  | (?: payload[-\s]attribution \s+ gate | attribution \s+ gate )
        """ + _NEG_FREE + r"""{0,60}?
        (?: \b fires \b | \b fired \b )
  | two \s+ consecutive \s+ zero[-\s]payload \s+ rounds?
    """
)

# 🔴 `CLEAN` IS MATCHED CASE-SENSITIVELY, IN CAPS, ON PURPOSE. The verdict is
# written `— **CLEAN. The ladder ends here.**`; the ABSTRACT RULE is written "a
# clean round ENDS the ladder", lowercase, and this repo's own ladders quote that
# rule constantly because several of these PRs are ABOUT the stop rule. Folding
# case makes every such quotation a clean terminal round.
_CLEAN_RE = re.compile(
    r"""(?x)
    \b (?<! [Nn][Oo][Tt] \s ) CLEAN \b                       # the verdict, caps
    # 🔴 `findings`, NEVER `🔴` ALONE. "returned no 🔴" is not a clean round —
    # this corpus is full of rounds that returned no 🔴 and several 🟡, which are
    # findings and keep the ladder running by the skill's own rule. An earlier
    # draft included `🔴` here and scored two such ladders `clean-round`.
  | (?i: \b (?: returned | found | produced | carries | has )
         \s+ (?: no | zero ) \s+ (?: new \s+ )? findings? )
    # 🔴 `produced none` — MEASURED on civitai/talos-infra #1354, which says
    # *"The ladder stops here, ON A CLEAN ROUND"* and *"Round 3 produced none, so
    # there is no round-3 fix commit and no round 4"*, and was scored
    # `stated-criterion` because neither half matched. The lowercase "clean
    # round" cannot be matched (this repo's own ladders quote the ABSTRACT rule
    # in those words constantly), so the assertive verb form is what closes it.
    # ⚠ `found` is NOT in this list. MEASURED on vetrllc/vetr-api #200, whose
    # terminal comment says *"Swept … for other stale fixed-burn claims …; FOUND
    # NONE beyond the L6 header"* — a grep sweep, not a finding-free round, and
    # it scored `clean-round` for a ladder that reported two findings. `returned`
    # and `produced` take a ROUND as their subject in this corpus; `found` takes
    # anything. ⚠ Residual: "the mutation sweep produced none" would still match.
  | (?i: \b (?: returned | produced ) \s+ none \b )
  | (?i: \b no \s+ 🔴 \s* [,.·;]? \s* (?: and \s+ )? no \s+ 🟡 )
  | (?i: \b no \s+ (?: new \s+ )? findings? \s+
         (?: this \s+ round | in \s+ this \s+ round | at \s+ all ) )
  | (?i: \b no \s+ fixes \s+ were \s+ made \s+ this \s+ round )
  | (?i: \b nothing \s+ (?: to \s+ fix | was \s+ found | left \s+ to \s+ fix ) )
    """
)

# 🔴 A HUMAN OVERRODE THE LADDER. Its own class, and it was ADDED after the first
# run mis-read devrc #1455 as `unstated`: its terminal comment says *"Round 2 was
# not run before merge — it was skipped by operator instruction, not by a clean
# round"*, which is a stated reason for the stop by any reading. The handoff
# records the same shape on #1576 ("the operator said merge it"). Kept SEPARATE
# from `stated-criterion` rather than folded in, because conflating "the session
# applied the skill's escape hatch" with "a human overrode the ladder" would
# corrupt the one rate #1157 asks for — and only the first carries #1157's
# write-it-down obligation.
# 🔴 BOTH HALVES REQUIRED, IN ONE SENTENCE: an operator act AND a ladder/round/
# merge context. A bare operator phrase is NOT enough — MEASURED on devrc #1406,
# whose terminal comment says *"the operator's call on the writer-invocation
# fork"*: a design decision, not a decision to stop auditing, and the first
# version of this pattern scored it `operator-decision`. `[^.\n]` keeps the two
# halves inside one sentence, which is what stops an operator note elsewhere in a
# long report from attaching itself to the word "round" three paragraphs away.
# 🔴 THE CONTEXT HALF IS STOP VOCABULARY, NOT `round`. MEASURED on devrc #1404,
# whose round-2 comment says *"finding 5 … left open as an operator decision"* —
# an operator decision about a FINDING, in a sentence that merely mentions a
# round. Requiring a stop word in the same sentence separates that from
# homelab-infra #623's *"THE LADDER WAS STOPPED HERE BY AN EXPLICIT OPERATOR
# DECISION"*, which is the real thing. ⚠ The alternative — tightening the
# character gap until the two separate — would be tuning on a fixture, and is why
# it was not done.
# 🔴 `by \s+ (?: \w+ \s+ ){0,2}` because #623 writes "BY AN EXPLICIT OPERATOR
# DECISION": a pattern demanding `by the operator` cannot see an adjective.
_OPERATOR_ACT = r"""(?: by \s+ (?: \w+ \s+ ){0,2} operator \s+
                          (?: instruction | decision | request | order )
                      | operator \s+ (?: instruction | decision | request
                                       | order | said | decided | asked | chose
                                       | instructed | wanted )
                      | skipped \s+ by \s+ (?: the \s+ )? operator )"""
_OPERATOR_CONTEXT = r"""(?: ladder | stops? | stopped | closed | merge
                          | skipped | not \s+ run )"""
_OPERATOR_RE = re.compile(
    r"""(?ix)
    \b """ + _OPERATOR_CONTEXT + r""" \b [^.\n]{0,80}? \b """
    + _OPERATOR_ACT + r"""
  | \b """ + _OPERATOR_ACT + r""" [^.\n]{0,80}? \b """
    + _OPERATOR_CONTEXT + r""" \b
    """
)

# A stop DECLARED. 🔴 Word order is the discriminator against the quoted rule:
# "the ladder ends here" declares; "a clean round ENDS the ladder" states a rule.
_STOP_RE = re.compile(
    r"""(?ix)
    # `the` is OPTIONAL and the passive forms are included: devrc #1439 heads its
    # terminal comment "## Round 5 — fixes landed · 🔴 LADDER STOPPED", with no
    # article and no active verb. ⚠ Word ORDER is what keeps the abstract rule
    # out — "a clean round ENDS the ladder" puts the verb first.
    (?: the \s+ )? ladder \s+ (?: stops | stopped | ends | ended | closes
                                | closed | terminates | is \s+ closed
                                | is \s+ over | is \s+ done )
    # 🔴 NOT A CONTRASTED OR NEGATED ONE. MEASURED on civitai/talos-infra #1479,
    # whose terminal comment heads a paragraph *"Why round 4 is RUNNING RATHER
    # THAN stopping here"* — the opposite of a stop, scored as one. Three
    # fixed-width lookbehinds, which is what Python allows; a variable-width
    # "any negation within N chars" is not expressible here and the three
    # spellings are the ones the corpus actually uses.
  | \b (?<! rather \s than \s ) (?<! instead \s of \s ) (?<! not \s )
       (?: stops | ends | stopping | stopped ) \s+ here \b
  | \b I \s+ am \s+ not \s+ running \s+ (?: a | another | round )
  | \b no \s+ (?: further | more | additional ) \s+ rounds? \b
  | \b (?: this | that ) \s+ is \s+ the \s+ (?: final | last ) \s+ round \b
  | \b the \s+ (?: final | last ) \s+ round \s+ (?: of | is | was ) \b
  | \b stopped \s+ on \s+ the \b
  | \b per \s+ the \s+ stop \s+ rule \b
    """
)

# #1157's REQUIREMENT: the rationale must be NAMED IN THE ROUND'S SUMMARY, not
# left implicit. Declaring a stop and explaining it are different acts — without
# the explanation "a report that ENDED the ladder on this escape hatch is
# indistinguishable from one that converged" (the skill, verbatim). So this is a
# SECOND extractor over the same text, and its rate is reported separately.
_RATIONALE_RE = re.compile(
    r"""(?ix)
    # 🔴 ADDED after the first run scored devrc #1439 `NOT FOUND` while its
    # terminal comment carries a heading reading "## Why the ladder stops here —
    # stated, not implicit" over three numbered reasons. That is #1157's
    # requirement discharged about as explicitly as it can be, and the pattern
    # could not see it: the three `why`-forms below are what closed that miss.
    \b why \s+ the \s+ ladder \s+ (?: stops | stopped | ends | ended )
  | \b stated \b [^.\n]{0,24} \b not \b [^.\n]{0,8} \b implicit \b
    # `0` as well as `zero`: civitai/talos-infra #1415 writes *"**0 executable
    # payload lines**"* and scored NOT FOUND over a spelling.
  | \b (?: zero | 0 ) \s+ \*{0,2} executable \*{0,2} \s+ payload \b
  | \b deliberately \s+ (?: \*\* )? not (?: \*\* )? \s+ fix
  | \b (?: not | never ) \s+ fixing \b
  | \b (?: this | here ) \s+ is \s+ the \s+ stated \s+ reason \b
  | \b stated \s+ (?: reason | criterion ) \b
  | \b escape \s+ hatch \b
  | \b (?: gate | it ) \s+ (?: cannot | can \s+ never | structurally \s+ cannot )
        \s+ fire
  | \b (?: payload | deliverable ) \s+ is \s+ prose \b
  | \b prose \s+ (?: payload | PR ) \b
  | \b one \s+ round \s+ from \s+ firing \b
  | \b rather \s+ than \s+ run \s+ a \s+ \w+ \s+ (?: full \s+ )? round \b
  | \b remaining \s+ (?: round-\d+ \s+ )? (?: items | findings )
        [^.\n]{0,80}? (?: not \b | open \b | unfixed \b )
  | \b will \s+ not \s+ (?: stop | terminate ) \s+ on \s+ (?: its | their ) \s+ own
    # MEASURED on vetrllc/vetr-api #200: *"Both findings were scaffolding/prose.
    # Intended as the last round of this ladder."* — the rationale IS "they were
    # scaffolding/prose", and no earlier alternative could see it. Safe to widen
    # here because this pattern is only consulted for a row that ALREADY carries
    # a declared stop.
  | \b (?: were | are | all | only | just ) \s+ (?: \*\* )?
       (?: scaffolding | prose | nits? | cosmetic | wording )
  | \b diminishing \s+ returns \b
  | \b time[-\s]box
  | \b (?: the \s+ )? operator \s+ (?: said | decided | asked | chose )
  | \b by \s+ operator \s+ decision \b
    """
)

GATE, CLEAN, OPERATOR, STATED, UNSTATED = (
    "attribution-gate", "clean-round", "operator-decision", "stated-criterion",
    "unstated")
NOT_TERMINATED, NO_TERMINAL_ROUND = "not-terminated", "no-terminal-round"
CLASSES = (GATE, CLEAN, OPERATOR, STATED, UNSTATED)
EXCLUDED = (NOT_TERMINATED, NO_TERMINAL_ROUND)

# 🔴 `self_range_rounds` IS NOT A SIXTH STOP CLASS, AND THE TAXONOMY ABOVE STAYS
# FROZEN. It answers a DIFFERENT question, on a different axis: the five classes
# say WHY the record says the ladder stopped; this says whether that record's
# payload arithmetic was measured over anything. Minting a `unearned-ledger`
# class would partition the rate by a property that is not a stop rationale —
# the same error the docstring rejects `merged-anyway` for — and would make a
# ladder's class depend on which defect you noticed first.
#
# It is carried HERE, in the prose census, because this is the tool that runs
# across every repo off comments alone: `ladder-range-coverage.py` needs a local
# checkout per repo, so it cannot produce a corpus number. And the cross-tab is
# the finding — a ladder classified `attribution-gate` whose blocks are
# self-ranges stopped on arithmetic over zero commits.
#
# 🔴 MEASURED 2026-09-23 over 241 ladders / 25 repos / 3,758 comments: NINE
# carry at least one. `ZacxDev/homelab-infra` #687 carries FOUR — all of them.
Carrier = namedtuple(
    "Carrier",
    "pr state rounds terminal_round terminal_index terminal_from_prose "
    "terminal_recovered label spans rationale rationale_span reason "
    "self_range_rounds",
    # Defaulted so every positional construction that predates the field keeps
    # working. `()` is the honest default: no self-range found.
    defaults=((),),
)


def rounds_in_comment(ad, body):
    """-> (all rounds, rounds seen ONLY in prose, rounds recovered from a block
    the shared parser REJECTED), each sorted.

    Blocks first, through the shared parser — one grammar, one place. Then the
    heading forms, for a round that posted prose and no block.

    🔴 THE THIRD SOURCE IS A FALLBACK ON A *REJECTED* BLOCK, AND IT IS NOT A
    SECOND GRAMMAR — it reuses `audit_dispatch`'s OWN `_FENCE_OPEN` and
    `_HEADER_ROUND` objects. MEASURED on devrc #1586: its block header is
    `audit-claims round=1 audited=d42e78ae..b627b094`, perfectly well formed,
    and `parse_claims_blocks` still returns NOTHING because the body's claims are
    written `🔴-1 FIXED — …` rather than `1. …`. That is correct for the
    DISPATCHER (a block with no numbered claims carries nothing to re-audit) and
    wrong for THIS question (the round number is right there), so the fallback
    belongs here rather than in the shared parser. Counted and reported, because
    a recovered round is weaker evidence than a parsed one.
    """
    text = body or ""
    rounds = set()
    blocks, _malformed = ad.parse_claims_blocks([text])
    for b in blocks:
        rounds.add(b.round_no)

    from_prose = set()
    for m in _ROUND_HEADING_RE.finditer(text):
        n = m.group("h") or m.group("h2")
        if n is not None:
            from_prose.add(int(n))

    recovered = set()
    if not rounds:
        for line in text.split("\n"):
            om = ad._FENCE_OPEN.match(line)
            if om is None:
                continue
            hm = ad._HEADER_ROUND.search(om.group("header"))
            if hm:
                recovered.add(int(hm.group(1)))
    allr = rounds | from_prose | recovered
    return (sorted(allr), sorted(from_prose - rounds - recovered),
            sorted(recovered - rounds - from_prose))


def has_fence(ad, bodies):
    """Does ANY comment carry a real `audit-claims` FENCE?

    🔴 `find_carriers` tests for the SUBSTRING `audit-claims`, so a PR that merely
    DISCUSSES the ledger in prose is enumerated as a carrier. MEASURED on devrc
    #1440: its only match is inside a numbered claim line talking about
    `--emit-claims`. Reported rather than silently classified, because it is an
    OVER-count direction in a population whose every other caveat is a floor.
    """
    for body in bodies:
        for line in (body or "").split("\n"):
            if ad._FENCE_OPEN.match(line):
                return True
    return False


# 🔴 A ROUND REPORT TALKS ABOUT OTHER ROUNDS, AND THAT IS THE DEEPEST ERROR
# CLASS HERE — not a pattern being too wide, but a true sentence about the WRONG
# SUBJECT. MEASURED on civitai/talos-infra #1449, terminal round 5: its comment
# says *"Round 5 returned no 🔴 and ONE 🟡. Round 4 returned no 🔴 and no 🟡."*
# The second sentence is a textbook clean-round assertion and it is about round
# 4, so the ladder was scored `clean-round` when round 5 had a finding and the
# comment's own heading declares a stated criterion. No amount of narrowing the
# CLEAN pattern fixes that; the subject has to be checked.
#
# So every match is discarded when its enclosing SENTENCE names round numbers and
# the terminal round is not among them. An enumeration counts as naming all of
# them — "Rounds 3, 4 and 5 changed zero executable payload" is about round 5
# too, and a regex that read only the first number would throw that away.
_ROUND_MENTION_RE = re.compile(
    r"(?i)\brounds?\b[\s\-]*((?:\d+[\s,]*(?:and|to|–|-|&)?[\s,]*)+)")
_SENT_SPLIT = re.compile(r"\n|(?<=[.!?])\s")


def _enclosing_sentence(text, i, j):
    a = 0
    for m in _SENT_SPLIT.finditer(text, 0, i):
        a = m.end()
    m = _SENT_SPLIT.search(text, j)
    return text[a:(m.start() if m else len(text))]


def _rounds_named(sentence):
    named = set()
    for m in _ROUND_MENTION_RE.finditer(sentence):
        named.update(int(d) for d in re.findall(r"\d+", m.group(1)))
    return named


def _spans(rx, text, terminal_round=None, cap=3):
    """The matched TEXT of up to `cap` matches — the census's lesson: a
    classification a reader cannot check from the output is one they must take on
    trust, and these matches sit far past any truncation of the comment.

    With `terminal_round` set, a match whose sentence is about OTHER rounds only
    is dropped; see `_ROUND_MENTION_RE` above for the case that forced it.
    """
    text = text or ""
    out = []
    for m in rx.finditer(text):
        if terminal_round is not None:
            named = _rounds_named(_enclosing_sentence(text, m.start(), m.end()))
            # 🔴 DROP ONLY A SENTENCE ABOUT *EARLIER* ROUNDS. A first version
            # required the terminal round itself to be named, and that deleted
            # devrc #1455 — terminal round 1, whose stop is stated as *"ROUND 2
            # was not run before merge — it was skipped by operator
            # instruction"*. Saying the NEXT round did not run is the canonical
            # way to say a ladder stopped, so a later round number is evidence
            # ABOUT this stop, while an earlier one is a retrospective note about
            # a round already superseded.
            if named and max(named) < terminal_round:
                continue
        s = re.sub(r"\s+", " ", m.group(0)).strip()
        if s not in out:
            out.append(s)
        if len(out) >= cap:
            break
    return out


def classify_text(text, terminal_round=None):
    """-> (label, spans, rationale_present, rationale_spans).

    PRECEDENCE: gate, then clean, then an operator override, then a declared
    stop, then `unstated`. The order is load-bearing and is argued in the module
    docstring.
    """
    gate = _spans(_GATE_RE, text, terminal_round)
    clean = _spans(_CLEAN_RE, text, terminal_round)
    operator = _spans(_OPERATOR_RE, text, terminal_round)
    stop = _spans(_STOP_RE, text, terminal_round)
    rationale = _spans(_RATIONALE_RE, text, terminal_round)
    if gate:
        return GATE, gate, bool(rationale), rationale
    if clean:
        return CLEAN, clean, bool(rationale), rationale
    if operator:
        return OPERATOR, operator, bool(rationale), rationale
    if stop:
        return STATED, stop, bool(rationale), rationale
    return UNSTATED, [], bool(rationale), rationale


def classify_carrier(ad, facts):
    """-> Carrier for one PR's facts: {pr, state, comments:[body, …]}.

    🔴 THE TERMINAL ROUND IS THE HIGHEST ROUND ANY COMMENT REPORTS, and the
    comment read is the LAST one reporting it — the order `gh pr list --json
    comments` returns, which is chronological. A correction posted after a round
    report is therefore what gets read, which is the right answer: it is the last
    word that round's author wrote about it.
    """
    pr = facts.get("pr", "?")
    state = (facts.get("state") or "").upper()
    bodies = facts.get("comments") or []

    # 🔴 THE STRUCTURAL READING, THROUGH THE SHARED PREDICATE. `self_range_blocks`
    # is `audit_dispatch`'s, and so is the `same_commit` inside it — an 8-char
    # `audited=` abbreviation must compare equal to a 40-char sha, and a local
    # `==` here would be a second dialect of the one rule this file imports
    # rather than re-types. Computed for EVERY carrier, including the excluded
    # ones: a block's range is broken whether or not the PR is still open.
    blocks, _malformed = ad.parse_claims_blocks(bodies)
    self_rounds = tuple(sorted(
        {b.round_no for b in ad.self_range_blocks(blocks)}
    ))

    reported, prose_only, recovered = [], [], []
    for i, body in enumerate(bodies):
        rs, po, rec = rounds_in_comment(ad, body)
        if rs:
            reported.append((i, rs))
        if po:
            prose_only.append((i, po))
        if rec:
            recovered.append((i, rec))

    if not reported:
        why = (
            "no comment on this PR names a round, and NO comment carries a real "
            "`audit-claims` FENCE either — `find_carriers` matches the SUBSTRING "
            "`audit-claims`, so a PR that merely DISCUSSES the ledger is "
            "enumerated as a carrier. This one is not a ladder"
            if not has_fence(ad, bodies) else
            "a real `audit-claims` fence is present but no comment names a "
            "round — a header carrying no `round=<n>`, so there is no terminal "
            "round's summary to read"
        )
        return Carrier(pr, state, [], None, None, False, False,
                       NO_TERMINAL_ROUND, [], False, [], why, self_rounds)

    all_rounds = sorted({r for _i, rs in reported for r in rs})
    terminal = all_rounds[-1]
    idx = [i for i, rs in reported if terminal in rs][-1]
    from_prose = any(i == idx and terminal in po for i, po in prose_only)
    from_recovered = any(i == idx and terminal in rec for i, rec in recovered)

    if state == "OPEN":
        return Carrier(pr, state, all_rounds, terminal, idx, from_prose,
                       from_recovered, NOT_TERMINATED, [], False, [],
                       "the PR is OPEN, so this ladder has not stopped — asking "
                       "why it stopped is a category error and calling it "
                       "`unstated` would invent a stop that has not happened",
                       self_rounds)

    label, spans, rationale, r_spans = classify_text(bodies[idx], terminal)
    return Carrier(pr, state, all_rounds, terminal, idx, from_prose,
                   from_recovered, label, spans, rationale, r_spans, None,
                   self_rounds)


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #

def _unearned_census(per_repo):
    """The UNEARNED-LEDGER section, as lines. Appended at BOTH of `render`'s
    exits — the ordinary one and the zero-denominator early return.

    🔴 IT MUST SURVIVE A ZERO DENOMINATOR. `render` returns early when no
    carrier is terminated (every PR still OPEN), and this reading does not
    consult the stop at all — so leaving it inline meant a corpus of open PRs
    with broken block ranges printed nothing. One writer, two callers.
    """
    out = []
    # 🔴 THE UNEARNED-LEDGER CENSUS — A SECOND AXIS, NOT A SIXTH CLASS. See the
    # note on `Carrier.self_range_rounds`: the five classes say why the record
    # SAYS the ladder stopped; this says whether that record's payload
    # arithmetic was measured over anything at all. Printed as its own section,
    # with its own denominator, so neither number contaminates the other.
    #
    # 🔴 THE DENOMINATOR IS EVERY CARRIER, INCLUDING THE TWO EXCLUDED CLASSES.
    # A block's range is broken whether or not the PR is open and whether or not
    # any comment names a round — this reading never consults the stop at all,
    # so scoping it to the rate's denominator would hide a defect on a live PR,
    # which is the one still worth fixing.
    unearned = [c for _repo, cs in per_repo for c in cs if c.self_range_rounds]
    everything = [c for _repo, cs in per_repo for c in cs]
    out.append("")
    out.append("### UNEARNED LEDGERS — a SECOND AXIS, not a sixth stop class")
    out.append("A block whose `audited=X..X` spans ZERO commits records a round "
               "that changed nothing BY")
    out.append("CONSTRUCTION, so the `payload=` beside it was measured over "
               "nothing. That is a fact about")
    out.append("the RECORD, not about why the ladder stopped — the taxonomy "
               "above stays frozen.")
    out.append(f"DENOMINATOR = {len(everything)} carrier(s) examined, INCLUDING "
               "not-terminated and")
    out.append("  no-terminal-round: this reading never consults the stop.")
    if not unearned:
        out.append("  0 carriers carry a self-range block in this run.")
    else:
        out.append(f"  🔴 {len(unearned)} carrier(s) carry at least one:")
        for c in unearned:
            out.append(f"     #{c.pr:<6} {c.label:<18} round(s) "
                       + ", ".join(str(r) for r in c.self_range_rounds))
        # 🔴 THE CROSS-TAB IS THE FINDING. A ladder classified `attribution-gate`
        # whose blocks are self-ranges stopped on arithmetic over zero commits —
        # the exact false stop `audit-dispatch.py`'s REFUSAL 3b exists to
        # prevent, seen after the fact. Counted rather than described, because a
        # sentence beside a number it is not computed from is what this whole
        # file's sibling was rewritten for.
        gated = [c for c in unearned if c.label == GATE]
        if gated:
            out.append(f"  🔴 {len(gated)} of those are classified `{GATE}` — "
                       "the ladder was stopped on payload")
            out.append("     arithmetic over a range spanning zero commits. "
                       "Re-read those stops before")
            out.append("     quoting them: "
                       + ", ".join(f"#{c.pr}" for c in gated))
    out.append("⚠ A FLOOR, for the same reason every count above is: a "
               "self-range is only visible where")
    out.append("  the block PARSED, and a block posted as a REVIEW comment is "
               "invisible here.")
    return out


def render(per_repo, notes):
    """per_repo: [(repo, [Carrier, …]), …]."""
    out = []
    out.append("## ladder stop-rationale census — WHY each ladder STOPPED, from "
               "its TERMINAL round")
    out.append("")
    out.append("taxonomy (FROZEN, precedence order): attribution-gate · "
               "clean-round · operator-decision ·")
    out.append("stated-criterion · unstated.")
    out.append("`unstated` is the DEFAULT and a legitimate answer. Excluded from "
               "the rate, never folded in:")
    out.append("not-terminated (the PR is OPEN) and no-terminal-round (no "
               "comment names a round).")
    out.append("🔴 `merged-anyway` is deliberately NOT a class — every merged "
               "carrier's ladder is followed")
    out.append("   by a merge, so the merge discriminates nothing about the "
               "stop. Each row prints the PR's")
    out.append("   state instead, so the outcome split is available without the "
               "tool asserting a cause.")
    out.append("")
    for n in notes:
        out.append(f"note: {n}")
    if notes:
        out.append("")

    totals = {k: 0 for k in CLASSES + EXCLUDED}
    rat_yes = rat_no = 0
    prose_terminal = recovered_terminal = 0
    per_repo_rows = []

    for repo, carriers in per_repo:
        counts = {k: 0 for k in CLASSES + EXCLUDED}
        out.append(f"### {repo} — {len(carriers)} carrier(s)")
        for c in carriers:
            counts[c.label] += 1
            totals[c.label] += 1
            if c.terminal_from_prose:
                prose_terminal += 1
            if c.terminal_recovered:
                recovered_terminal += 1
            head = (f"  #{c.pr:<6} {c.state:<7} terminal round "
                    f"{c.terminal_round if c.terminal_round is not None else '?':<3}"
                    f" {c.label}")
            if c.self_range_rounds:
                head += ("  [UNEARNED LEDGER: round(s) "
                         + ", ".join(str(r) for r in c.self_range_rounds) + "]")
            if c.label in EXCLUDED:
                out.append(head)
                out.append(f"       — {c.reason}")
                continue
            if c.label == STATED:
                if c.rationale:
                    rat_yes += 1
                else:
                    rat_no += 1
                head += ("  [#1157 rationale: WRITTEN]" if c.rationale
                         else "  [#1157 rationale: NOT FOUND]")
            out.append(head)
            if c.spans:
                out.append(f"       span: {' | '.join(c.spans)[:160]}")
            elif c.label == UNSTATED:
                out.append("       span: none — nothing in this comment says "
                           "why the ladder stopped")
            if c.label == STATED and c.rationale_span:
                out.append(f"       why:  {' | '.join(c.rationale_span)[:160]}")
        per_repo_rows.append((repo, counts, len(carriers)))
        out.append("")

    out.append("### per-repo counts")
    out.append("| repo | carriers | gate | clean | operator | stated | unstated "
               "| not-term | no-round |")
    out.append("|---|---|---|---|---|---|---|---|---|")
    for repo, c, n in per_repo_rows:
        out.append(f"| {repo} | {n} | {c[GATE]} | {c[CLEAN]} | {c[OPERATOR]} | "
                   f"{c[STATED]} | {c[UNSTATED]} | {c[NOT_TERMINATED]} | "
                   f"{c[NO_TERMINAL_ROUND]} |")

    denom = sum(totals[k] for k in CLASSES)
    out.append("")
    out.append("### THE RATE, with its denominator named")
    out.append(f"DENOMINATOR = {denom} TERMINATED carrier(s) with an "
               "identifiable terminal round.")
    out.append(f"  excluded and NOT in it: {totals[NOT_TERMINATED]} "
               f"not-terminated (OPEN) · {totals[NO_TERMINAL_ROUND]} "
               "no-terminal-round.")
    if not denom:
        out.append("🔴 NOTHING TO RATE — the denominator is 0, so no percentage "
                   "is printed. A rate over")
        out.append("   nothing is not a rate.")
        # 🔴 THE SECOND AXIS STILL PRINTS. A corpus of entirely OPEN carriers has
        # no stop rate and may still have broken block ranges, and that defect is
        # the one still worth fixing.
        out.extend(_unearned_census(per_repo))
        return "\n".join(out)

    stated_mech = denom - totals[UNSTATED]

    def pc(n):
        return f"{n} ({100.0 * n / denom:.1f}%)"

    out.append(f"  stopped for a REASON THE RECORD STATES: {pc(stated_mech)}")
    out.append(f"    attribution-gate   {pc(totals[GATE])}")
    out.append(f"    clean-round        {pc(totals[CLEAN])}")
    out.append(f"    operator-decision  {pc(totals[OPERATOR])}")
    out.append(f"    stated-criterion   {pc(totals[STATED])}")
    out.append(f"  unstated             {pc(totals[UNSTATED])}")
    out.append("")
    out.append("### the #1157 SUB-RATE — was the RATIONALE written, not just a "
               "stop declared?")
    out.append("#1157 requires the escape hatch's reason be named IN THE "
               "ROUND'S SUMMARY, because a stop")
    out.append("declared without one is indistinguishable from a ladder that "
               "converged.")
    sub = rat_yes + rat_no
    if sub:
        out.append(f"  DENOMINATOR = {sub} carrier(s) classified "
                   f"`stated-criterion`.")
        out.append(f"  rationale WRITTEN   {rat_yes} "
                   f"({100.0 * rat_yes / sub:.1f}%)")
        out.append(f"  rationale NOT FOUND {rat_no} "
                   f"({100.0 * rat_no / sub:.1f}%)")
    else:
        out.append("  🔴 NO CARRIER was classified `stated-criterion`, so there "
                   "is NO sub-rate. That is a")
        out.append("     zero denominator, not 0% compliance — the two read the "
                   "same and mean opposites.")
    out.append("")
    out.append("### what this measurement CANNOT see — read before quoting any "
               "number above")
    out.append("⚠ A FLOOR, NEVER A CENSUS. `gh pr list --json comments` does "
               "NOT return REVIEW comments,")
    out.append("  so a round report posted as a review is invisible here — the "
               "same blind spot")
    out.append("  `audit-dispatch.py` warns about when it cannot see a block a "
               "human can.")
    out.append("⚠ THE WHOLE TERMINAL COMMENT IS CLASSIFIED, not a `summary` "
               "section — these reports have")
    out.append("  no machine-identifiable one. The bias has a DIRECTION: a stop "
               "phrase ANYWHERE in the")
    out.append("  comment counts, so `unstated` is UNDER-counted and the "
               "compliance rate above is a")
    out.append("  CEILING.")
    out.append("⚠ PATTERNS OVER WORDS. A rationale phrased in words these "
               "patterns do not carry reads as")
    out.append("  absent, so the #1157 sub-rate is a FLOOR on compliance. Every "
               "row prints its matched")
    out.append("  SPAN for exactly this reason — check the span before quoting "
               "the class.")
    out.append("⚠ ONE COMMENT PER LADDER IS READ. A rationale written in an "
               "EARLIER round's comment, or")
    out.append("  in the PR body or description, is not seen.")
    if prose_terminal:
        out.append(f"⚠ {prose_terminal} terminal round(s) were identified from a "
                   "PROSE HEADING and no block.")
    if recovered_terminal:
        out.append(f"⚠ {recovered_terminal} terminal round(s) were RECOVERED from "
                   "a block the shared parser")
        out.append("  REJECTED — a well-formed `round=<n>` header over a body "
                   "whose claims are not numbered.")
        out.append("  Weaker evidence than a parsed block; the round number is "
                   "still the header's own.")
    out.append("🔴 SPANS ARE VERBATIM PR TEXT and four of these repos are "
               "client-owned. Do not paste a")
    out.append("  client-repo span into devrc, which is PUBLIC.")

    out.extend(_unearned_census(per_repo))
    return "\n".join(out)



# --------------------------------------------------------------------------- #

def main(argv=None, runner=None, out_stream=sys.stdout, err_stream=sys.stderr):
    ap = argparse.ArgumentParser(
        description="why each audit ladder stopped, from its terminal round")
    ap.add_argument("--repo", action="append", default=[],
                    help="owner/name; repeatable")
    ap.add_argument("--limit", type=int, default=400,
                    help="PRs to scan per repo (default 400)")
    ap.add_argument("--facts-file", help="JSON list of {pr, state, comments[]} "
                                        "— consults no `gh` and no network")
    ap.add_argument("--audit-dispatch", help="path to audit-dispatch.py")
    ap.add_argument("--range-coverage", help="path to ladder-range-coverage.py")
    args = ap.parse_args(argv)

    if not args.repo and not args.facts_file:
        ap.error("give at least one --repo, or --facts-file")

    lrc, ad = load_siblings(args.range_coverage, args.audit_dispatch)
    if runner is None:
        runner = lrc.real_runner

    per_repo, notes = [], []
    if args.facts_file:
        try:
            raw = json.loads(Path(args.facts_file).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            print(f"cannot read --facts-file: {e}", file=err_stream)
            return EXIT_USAGE
        facts = raw if isinstance(raw, list) else [raw]
        notes.append("--facts-file mode: no `gh` was consulted, so nothing here "
                     "was checked against a live PR")
        per_repo.append(("--facts-file", [classify_carrier(ad, f)
                                          for f in facts]))
    else:
        for repo in args.repo:
            # 🔴 `ad` IS THE FIRST ARGUMENT, and omitting it is not a style
            # slip: `find_carriers(ad, runner, repo, …)` takes the fence
            # grammar from `audit-dispatch.py` rather than re-typing it, so the
            # module is a parameter. Shipped without it in #1643, which made
            # every non-`--facts-file` run a `TypeError` — the live path had
            # never run. Pinned behaviourally by
            # `test_the_live_repo_path_actually_CALLS_the_shared_enumerator`.
            #
            # 🔴 `state="all"` IS EXPLICIT BECAUSE THIS CONSUMER DEPENDS ON IT.
            # The `not-terminated` class exists only for OPEN PRs; a narrower
            # state would drop them from the population and every live ladder
            # would silently be classified as though it had stopped.
            carriers, note = lrc.find_carriers(ad, runner, repo,
                                               limit=args.limit, state="all")
            notes.append(note)
            per_repo.append((repo, [classify_carrier(ad, f) for f in carriers]))

    print(render(per_repo, notes), file=out_stream)
    if not any(cs for _r, cs in per_repo):
        return EXIT_NOTHING_MEASURABLE
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
