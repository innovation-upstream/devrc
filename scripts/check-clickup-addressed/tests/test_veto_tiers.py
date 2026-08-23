#!/usr/bin/env python3
"""The keep-open veto's two tiers and its clause-scoped negation.

Round 8 — 2026-08-21. Measured matrix over the 34 tests this round adds (30 here, 4 in
`test_corpus.py`): **21 red at `origin/trunk` `d11e67e87` for a BEHAVIOURAL reason, 3 red only
structurally** (`AttributeError` on symbols this change introduces — they could never have
been behaviourally red, so they are NOT regression coverage), **10 green at base, all 34 green
at HEAD.**

Several of the 10 that pass at base are red against an INTERMEDIATE version of this change
rather than against trunk — they pin regressions that two blind audits found in the FIX, not
in trunk. Their docstrings say so. Trunk gets those cases right by having no clause machinery
at all, which is not the same as being safe: it scores 24/42 on the labelled corpus, against
39/42 for what ships here.

🔴 **Before changing the veto, read `scripts/check-clickup-addressed/tests/test_corpus.py`
and score your candidate.** Eight rounds of this were argued one clever sentence at a time and
five shipped a regression. The scoreboard is what ended that.

Three hazards this file has hit repeatedly, all worth re-reading before adding a test:

1. **Do not claim RED AT BASE without running it at base** — two tests here claimed it and
   did not have it.
2. 🔴 **Do not assert a substring the surrounding sentence can also produce.** `flags()`
   defaults to `transcript_status="likely_addressed"`, and the TRANSCRIPT branch emits
   "— close it or re-check", so a bare `"close it" in j` is satisfied by a completely
   different flag. Measured: deleting the comment-level close-it trigger outright once left
   17 of 19 tests here GREEN. **Pass `transcript_status="unclear"` whenever you assert about
   the comment flag**, and assert its own words (`reads as RESOLVED`).
3. 🔴 **A mutant whose anchor no longer applies is a FAILURE, not a survivor.** Three separate
   rewrites in this round left stale anchors in the battery, each of which would have read as
   a passing mutation that never ran.

The defect this round fixes has TWO directions and one cause. Trunk's veto is absolute, so a
legitimate close-it is suppressed whenever the comment mentions an unrelated open PR, AND an
affirmative CLOSE IT is emitted over a refusal spelled any way other than the two literals
`KEEP_OPEN_RE` enumerates. The second direction is the dangerous one and is live on trunk.
"""
import importlib.util
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent.parent
spec = importlib.util.spec_from_file_location("check_addressed", SCRIPT_DIR / "check-addressed.py")
check_addressed = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check_addressed)

NOW = datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc)
RECENT = "2026-08-20 10:00"


def flags(comment, status="to do", transcript_status="likely_addressed"):
    """Drive the real `disagreements()`. Full text AND snippet, as production supplies both."""
    return check_addressed.disagreements([{
        "task_id": "868test01", "status": transcript_status, "clickup_status": status,
        "mentions_found": 3,
        "newest_comment": {"snippet": comment[:200], "text": comment,
                           "date": RECENT, "author": "colleague"},
        "completion": [], "open": [],
    }], now=NOW)


def joined(comment, **kw):
    return " ".join(flags(comment, **kw))


# --------------------------------------------------------- direction 1: over-veto

AMBIGUOUS_COMMENTS = [
    "Resolved end to end, recommend closing. (The follow-up PR is still open but unrelated.)",
    "One review thread is not resolved on the PR, but the ticket itself is done. Recommend closing.",
    "The alert fired and was not resolved automatically. Ticket work is done — recommend closing.",
    "The fix landed in #1234 and is live. The follow-up PR is still open but unrelated.",
]


def test_a_weak_refusal_beside_a_closure_claim_stops_suppressing_the_close_it():
    """RED AT BASE — all four emit an absolute `do NOT close` on trunk.

    These are the shapes that made the veto expensive: `still open` and `not resolved` are
    routinely about a PR, an alert or a sibling ticket, and the reporter is in the same
    breath asking for the ticket to be closed.
    """
    for c in AMBIGUOUS_COMMENTS:
        j = joined(c)
        assert "do NOT close" not in j, f"a weak refusal still suppressed a close-it: {c!r} -> {j}"
        assert "READ IT and decide" in j, f"no ambiguity flag emitted for {c!r}: {j}"


def test_the_ambiguity_flag_names_both_halves():
    """A flag that says "it is ambiguous" without saying WHICH two phrases collided sends the
    reader back to re-read the whole comment, which is the work the tool is meant to do."""
    j = joined(AMBIGUOUS_COMMENTS[0])
    assert "still open" in j, f"the ambiguity flag does not name the keep-open phrase: {j}"
    assert "Resolved" in j, f"the ambiguity flag does not name the closure claim: {j}"


# --------------------------------------------------------- direction 2: under-veto

NEGATED_REFUSALS = [
    "The issue isn't resolved.",
    "The issue is never resolved.",
    "This won't be resolved until next sprint.",
    "This is not fully resolved.",
    "This is unresolved.",
    "I do not recommend closing this yet.",
]


def test_a_negated_closure_claim_never_reads_as_a_resolution():
    """RED AT BASE — all six draw an affirmative "close it" on trunk.

    One rule replaces six spellings: the vocabulary is matched plainly, then the CLAUSE it
    sits in is asked whether it carries a negator. Per-word lookbehinds guarded exactly the
    two forms they named and lost this twice on the abandoned branch.
    """
    for c in NEGATED_REFUSALS:
        j = joined(c)
        assert "close it" not in j, f"told the operator to close a ticket refusing it: {c!r} -> {j}"
        assert "do NOT close" in j, f"the refusal never reached the operator: {c!r} -> {j}"


def test_contractions_are_matched_by_shape_not_by_a_stem_list():
    """`won't` has the stem "wo", not "will" — so any list of contraction stems is already
    incomplete when it is written. `\\w+n't` covers the class, including shan't/mightn't."""
    for c in ("This shan't be resolved this quarter.",
              "It mightn't be resolved before the freeze.",
              "This won't be resolved until next sprint."):
        assert "close it" not in joined(c), f"contraction not recognised as negation: {c!r}"


def test_the_quoted_phrase_carries_its_own_negator():
    """A veto reading `newest comment says "resolved" — do NOT close` quotes the comment as
    saying the OPPOSITE of what fired the flag. That is how an operator learns to skip the
    line, so the quoted span runs from the negator through the closure word.

    `not resolved` alone would pass at base — trunk's regex carries that exact literal — so
    the spellings trunk CANNOT produce are what make this red there.
    """
    for comment, want in (("The issue is not resolved.", '"not resolved"'),
                          ("This won't be resolved until next sprint.", '"won\'t be resolved"'),
                          ("Nothing has been merged.", '"Nothing has been merged"')):
        j = joined(comment)
        assert want in j, f"the flag does not quote the negator with its target: {j}"


# --------------------------------------------------------- clause scope, both directions

def test_a_trailing_negator_does_not_reach_BACKWARDS_and_invert_a_resolution():
    """🔴 The regression the first blind audit caught, and it was worse than the bug this
    round exists to fix.

    RED against the intermediate version, GREEN at trunk (which has no clause machinery to
    get this wrong) — so it is regression coverage for the FIX, not for trunk.

    Negation was scoped to the clause but NOT to word order, and commas were not yet clause
    boundaries, so any trailing "no" / "nothing" reached backwards over a plain
    resolution: 12 of 12 ordinary "work is finished, nothing outstanding" comments flipped
    from `close it` to `do NOT close`. That is an affirmative wrong instruction, not a safe
    over-veto — it would have traded 4 wrongly-suppressed close-its for 12 wrongly-created
    vetoes, on a tool whose whole priority is that its instructions are trustworthy.

    A negator now negates only closure words that FOLLOW it in the clause.
    """
    for c in ("Resolved, no further action needed.",
              "Recommend closing, no further work planned.",
              "Confirmed resolved, no repro since Tuesday.",
              "This is done, nothing else outstanding."):
        j = joined(c, transcript_status="unclear")
        assert "do NOT close" not in j, f"a trailing negator inverted a resolution: {c!r} -> {j}"
        assert "reads as RESOLVED" in j, f"the close-it flag was lost for {c!r}: {j}"


def test_the_quoted_phrase_is_a_real_substring_in_the_comments_own_word_order():
    """A negator found AFTER the closure word produced manufactured quotations — the flag
    printed `says "no … resolved"` for a comment reading "Confirmed resolved, no repro since
    Tuesday", i.e. words the comment contains in an order it never used. With negation
    positional the elided form is an honest elision. Nothing asserted on this output shape
    before; two mutants on the quoting guard survived the suite."""
    j = joined("The rollback is not (as far as I can tell) done.", transcript_status="unclear")
    assert "do NOT close" in j, f"expected a veto: {j}"
    quoted = j.split('says "', 1)[1].split('"', 1)[0]
    assert quoted, "the veto quoted an EMPTY phrase — which makes the whole flag falsy"
    src = "The rollback is not (as far as I can tell) done."
    if " … " in quoted:
        head, tail = quoted.split(" … ", 1)
        assert src.index(head) < src.index(tail), \
            f"the elided quote reverses the comment's word order: {quoted!r}"
    else:
        assert quoted in src, f"the flag quoted text the comment does not contain: {quoted!r}"


def test_parentheses_do_not_strand_a_negator_from_its_target():
    """🔴 RED AT BASE and at base-of-this-round: `()` used to be a clause boundary, which is
    exactly what `test_a_bare_comma_is_not_a_clause_boundary` forbids for commas — the same
    class, one line apart, unnoticed until audit. "This is not (yet) resolved" split into
    ["This is not ", "yet", " resolved"] and drew an affirmative CLOSE IT over a refusal."""
    for c in ("This is not (yet) resolved.",
              "This is not (fully) resolved.",
              "The rollback is not (as far as I can tell) done."):
        j = joined(c, transcript_status="unclear")
        assert "close it" not in j, f"a paren stranded the negator: {c!r} -> {j}"
        assert "do NOT close" in j, f"the refusal never reached the operator: {c!r} -> {j}"


def test_the_parenthesised_aside_is_still_read_for_ambiguity():
    """CONTROL for the fix above — dropping `()` as a boundary must not cost the round-8
    fixture its ambiguity, which it gets from the full stop that precedes the paren."""
    j = joined(AMBIGUOUS_COMMENTS[0])
    assert "READ IT and decide" in j, f"the paren fixture lost its ambiguity flag: {j}"


def test_negators_that_are_not_spelled_with_a_negator_word():
    """`cannot` has no apostrophe and no separate negator token; `far from` and `anything
    but` negate what follows them idiomatically. All three drew a CLOSE IT over a refusal.

    `anything but` needs BOTH halves of its handling — the negator entry AND the lookbehind
    in CLAUSE_SPLIT_RE — because the `but` would otherwise split the phrase away from the
    word it negates. Neither half works alone, which is why one behavioural case pins both.
    """
    for c in ("We cannot call this resolved.",
              "This is far from resolved.",
              "This is anything but resolved."):
        j = joined(c, transcript_status="unclear")
        assert "close it" not in j, f"unrecognised negation drew a close-it: {c!r} -> {j}"
        assert "do NOT close" in j, f"no veto for {c!r}: {j}"


def test_naming_a_ticket_beside_still_open_is_NOT_promoted_to_strong():
    """🔴 The reverse of what an earlier round of this change asserted, and the reversal is
    the finding.

    `(?:ticket|task) (?:is |remains? )?still open` was briefly STRONG, on the argument that
    "a PR is open, but only THIS object is *the ticket*, so there is no second reading".
    That premise is false in ClickUp, where comments reference sibling tickets constantly.
    Measured: it produced a hard veto on 4 of 4 comments where the weak tier had correctly
    said READ IT — the round-8 motivating case with the noun changed.

    Weak is the right tier for every "still open"; the ambiguity downgrade is what handles
    the ones that are about this ticket.
    """
    for c in ("Resolved end to end, recommend closing. The upstream task is still open "
              "but that is tracked separately.",
              "Fix merged. The duplicate task is still open, closing this one.",
              "This is done and deployed. The parent ticket is still open; it covers the "
              "rollout."):
        j = joined(c)
        assert "do NOT close" not in j, f"a SIBLING ticket's status vetoed this one: {c!r} -> {j}"
        assert "READ IT and decide" in j, f"no ambiguity flag: {c!r} -> {j}"


def test_the_unknown_status_branch_emits_the_ambiguity_flag_too():
    """The unknown-status branch is reachable and had zero coverage — deleting its ambiguity
    output left the suite green. An unrecognised ClickUp status already tells the reader to
    read the comment; saying WHY it is ambiguous is the part that saves them the re-read."""
    j = joined(AMBIGUOUS_COMMENTS[0], status="in review")
    assert "DID NOT RUN" in j, f"the unknown-status announcement was lost: {j}"
    assert "READ IT and decide" in j, \
        f"an ambiguous comment on an unknown status said nothing about the conflict: {j}"


def test_an_em_dash_is_a_clause_boundary():
    """Load-bearing on real prose and pinned by nothing until audit — deleting the `[—–]` arm
    left the suite green. Without it the negator reaches across the dash."""
    j = joined("This is still open — the fix was merged and shipped last week.")
    assert "READ IT and decide" in j, \
        f"an em-dash clause boundary was not honoured: {j}"


def test_negation_does_not_leak_across_clauses():
    """INVARIANT GUARD — passes at base (trunk has no clause scope to leak across), and is
    the control for the mechanism this round adds, not evidence for it.

    Comment-wide negation scope would let a single "no" anywhere silence every closure word,
    making the ambiguous tier unreachable and turning every such comment into a veto. Here
    the negator belongs to a different sentence than the resolution, so the close-it stands.
    Killed by the no-clause-splitting mutant, which is what makes it non-vacuous.

    🔴 Asserts `reads as RESOLVED` under `transcript_status="unclear"`, NOT `"close it"` at
    the default. The transcript branch emits "— close it or re-check", whose boilerplate
    satisfies a bare `"close it" in j`, so the original assertion observed a DIFFERENT flag
    than the one it names.
    """
    j = joined("There is no regression left. Resolved — recommend closing.",
               transcript_status="unclear")
    assert "reads as RESOLVED" in j, \
        f"a negator in a different clause swallowed the resolution: {j}"
    assert "do NOT close" not in j, f"negation leaked across a clause boundary: {j}"


def test_a_coordinating_conjunction_IS_a_clause_boundary():
    """The other half of clause scope, and the one every other fixture accidentally covered.

    Found by a surviving mutant: stripping `but|however|…` from the splitter changed nothing,
    because every ambiguous fixture also carried a sentence boundary that did the same work.
    Here the negator and the closure claim share one sentence, so only the conjunction
    separates them — without it the "not" reaches "done" and a legitimate close-it is vetoed.
    """
    j = joined("One review thread is not resolved but the ticket itself is done")
    assert "READ IT and decide" in j, \
        f"a negator leaked across a conjunction and suppressed the closure claim: {j}"


def test_the_ambiguity_window_reads_the_full_comment_not_the_snippet():
    """Also found by a surviving mutant: the ambiguity decision reads the FULL comment while
    the close-it trigger keeps the 200-char snippet, and nothing pinned the difference.

    A status comment that opens with an unrelated "still open" and asks for the close 600
    characters later is the ordinary shape of a long update. Reading only the snippet vetoes
    it — the exact suppression this round exists to remove.
    """
    padding = ("The follow-up PR is still open but unrelated. Throughput held flat across the "
               "window and the backlog drained on schedule; the numbers below are all from the "
               "same 24h window so they can be read together, and nothing else changed. ")
    text = padding + "Both asks are now closed — recommend closing."
    assert len(padding) > 200, "the closure claim must sit past the display truncation"
    j = joined(text)
    assert "do NOT close" not in j, \
        f"a closure claim past the display window was invisible to the ambiguity tier: {j}"
    assert "READ IT and decide" in j, f"no ambiguity flag: {j}"


def test_every_close_it_trigger_phrase_is_also_ambiguity_vocabulary():
    """🔴 THE PREMISE THAT LICENSES DELETING THE CLOSE-IT BRANCH'S NEGATION FILTER.

    That filter was written, and the sweep scored it the identity. It is unreachable because
    every phrase `RESOLVED_COMMENT_RE` matches also contains a `CLOSURE_VOCAB_RE` word, so a
    NEGATED close-it trigger is already a negated closure claim and vetoes one branch earlier.

    If that stops being true this goes red — and the filter must come back, because a
    close-it trigger outside the ambiguity vocabulary would reach the close-it branch through
    its own negation. The first assertion is the positive control: it fails if these fixtures
    drift away from the regex they claim to enumerate.
    """
    for phrase in ("resolved", "recommend closing", "can be closed", "both asks are closed",
                   "both asks are now closed", "this is done", "all asks are closed",
                   "all items are closed"):
        assert check_addressed.RESOLVED_COMMENT_RE.search(phrase), \
            f"fixture drift: {phrase!r} no longer matches the close-it trigger at all"
        assert check_addressed.CLOSURE_VOCAB_RE.search(phrase), \
            (f"{phrase!r} triggers a close-it but is NOT ambiguity vocabulary, so its negated "
             f"form reaches the close-it branch unguarded — restore the negation filter in "
             f"disagreements()")


def test_a_status_line_ending_in_a_negator_does_not_veto_the_next_sentence():
    """🔴 The regression the THIRD blind audit caught, in the rule written by the second.

    A `carry` rule propagated a clause-TRAILING negator into the following clause, to rescue
    "This is not, in my view, resolved" after commas became boundaries. But "ends on a
    negator" does not distinguish an interrupted negation from how engineers actually write a
    clean status — `Downtime: none.` `Impact: none,` `Regressions found: none.` — so it
    vetoed the resolution that followed, and reintroduced a manufactured quotation
    (`says "none … Resolved"`, two words from different sentences).

    It bought ONE corpus case and cost seven realistic close-its. Deleted; the case it was
    written for is now a recorded KNOWN FAILURE in test_corpus.py. Say what is not handled
    rather than half-handling it.
    """
    for c in ("Downtime: none. Resolved and deployed.",
              "Impact: none, resolved by the rollback.",
              "Regressions found: none. Resolved.",
              "Blockers: none. Owner: me. Reviewed by Alice. Resolved — closing."):
        j = joined(c, transcript_status="unclear")
        assert "do NOT close" not in j, \
            f"a status line ending in a negator vetoed its own resolution: {c!r} -> {j}"
        assert "none …" not in j, f"manufactured quotation across two sentences: {j}"


def test_the_symptom_then_resolution_shape_is_not_vetoed():
    """🔴 THE DOMINANT SHAPE IN A BUG COMMENT, and the reason commas ARE clause boundaries.

    A bug comment states the SYMPTOM — which is negated, because that is what a bug is —
    and then the RESOLUTION, in one comma-spliced sentence. With commas inside the clause,
    the symptom's negator reaches forward and vetoes the resolution. Measured: 10 of 10
    realistic comments of this shape became false `do NOT close`.

    This is the mirror of the trailing-negator regression above. Both were found by audit,
    one round apart, and only measuring BOTH orders against a labelled corpus settled it:
    commas-not-a-boundary scored 28/42, commas-a-boundary 39/42.
    """
    for c in ("Users cannot upload avatars, fixed in #4421 and deployed.",
              "The alert wasn't firing, resolved by the rule fix.",
              "Search returned no results for tag queries, resolved by the reindex.",
              "The job did not run on Sunday, resolved — I re-ran it.",
              "Cannot reproduce this anymore, resolved.",
              "No repro since the deploy, resolved."):
        j = joined(c, transcript_status="unclear")
        assert "do NOT close" not in j, \
            f"the symptom's negator vetoed its own resolution: {c!r} -> {j}"


def test_a_negator_AFTER_the_closure_word_never_produces_a_reversed_quote():
    """What the positional rule earns, and the only thing that distinguishes it.

    Found by mutation: making negation position-independent again left the whole suite green,
    because every fixture happened to put the negator first. It is not equivalent — it
    reverses the quote. "Resolved and not verified in prod." has no comma, so both words sit
    in one clause, and the position-independent version emits:

        newest comment says "not … Resolved" — do NOT close

    which is the manufactured-quotation defect: words the comment contains, in an order it
    never used. The elision reads as the comment having said "not resolved", which it did not.

    🔴 Stated residual, not a solved problem: the two orders disagree on the VERDICT here and
    neither is right in general. Positional calls this "close it" (wrong — it is not
    verified); position-independent calls it "do NOT close" but would call "Resolved and no
    follow-up is planned" the same (also wrong). What the negator attaches to is not
    recoverable from punctuation. Positional is kept for ONE reason only: it never fabricates
    a quotation.

    🔴 TWO CLAIMS THAT USED TO BE HERE WERE FALSE, and an audit measured both.
    "Its failure mode needs the writer to use 'and', where a comma or full stop is far more
    common" — no: the failure fires on every separator, `and` / `,` / `.` / `but` / `;` / `—`,
    all six measured drawing "close it". And "the corpus records the failing case rather than
    hiding it" — the corpus contained it zero times; the sentence asserted coverage that did
    not exist, which is the thing that stops the next person looking. The whole class (a
    negator FOLLOWING the closure word it denies) is now in `test_corpus.py`'s KNOWN_FAILURES,
    where it is counted rather than described.
    """
    j = joined("Resolved and not verified in prod.", transcript_status="unclear")
    assert "not … Resolved" not in j, f"the flag quoted the comment in reverse order: {j}"
    assert "no … Resolved" not in j, f"the flag quoted the comment in reverse order: {j}"


def test_a_weak_refusal_ALONE_still_vetoes():
    """RED-ADJACENT: passes at base, and is the round-5 regression this design must not
    repeat. WEAK means *ambiguous alongside a closure claim*, never *ignorable* — with no
    closure claim in the comment the weak phrase is the only statement about closure, so
    there is nothing for it to be ambiguous against.
    """
    for c in ("This is still open. I have not had time to look at it.",
              "The issue is not resolved.",
              "This is still open. I haven't done anything yet.",
              "This is still open. The bug is not fixed.",
              "This is still open — nothing has been merged."):
        j = joined(c)
        assert "do NOT close" in j, f"a lone weak refusal stopped vetoing: {c!r} -> {j}"
        assert "READ IT and decide" not in j, \
            f"a lone weak refusal was downgraded to ambiguous with nothing to be ambiguous " \
            f"against: {c!r} -> {j}"


def test_a_negated_done_word_does_not_fake_a_closure_claim():
    """The mirror of the round-6 blind spot: only `resolved` carried negation handling there,
    so `haven't done` / `not fixed` / `nothing merged` each faked the closure half of an
    ambiguity and downgraded a legitimate veto. Covered above behaviourally; this pins the
    unit so a vocabulary change cannot re-open it silently."""
    affirmed, negated = check_addressed.closure_claims(
        "This is still open. I haven't done anything, nothing has been merged, not fixed.")
    assert affirmed == [], f"a negated done-word was counted as a closure claim: {affirmed}"
    assert negated, "the negated done-words were not recorded at all"


def test_strong_veto_is_absolute_even_beside_a_closure_claim():
    """INVARIANT GUARD — passes at base, where the veto is absolute and nothing can downgrade
    it. It exists because this round introduces the downgrade: STRONG is an instruction about
    THIS ticket that no other reading survives, so a closure claim elsewhere in the comment
    must not reach it."""
    j = joined("Still live, do not close. The fix landed and the sibling ticket is done.")
    assert "do NOT close" in j, f"an explicit 'do not close' was downgraded: {j}"
    assert "READ IT and decide" not in j, f"a strong veto was treated as ambiguous: {j}"


def test_a_declaration_that_the_ticket_STAYS_open_is_strong():
    """🔴 REGRESSION FROM THE LIVE RUN, not from the table.

    A live comment on `868gy0fff` (2026-08-21) opened with a triage status line declaring the
    ticket is STAYING OPEN, whose ONLY closure vocabulary was one "shipped" describing a
    sub-item that landed in a different repo. The first version of this round downgraded it
    to "READ IT and decide" — trunk's absolute veto had it right. The fixture below is
    SYNTHETIC and reproduces that shape; the original wording is not reproduced here.

    These phrasings are STRONG because they are not this domain's vocabulary for anything
    else: a PR or an alert is "open", never "staying open". The synthetic 17-case table could
    not have found this; only running the tool against real comments did.
    """
    live_shape = ("Status check during ClickUp triage 2026-08-21 — staying open, and mostly "
                  "not verifiable from this repo. Per the comment thread: part one (the "
                  "tracing flag) shipped via a sibling repo, #1093. Part two, the global "
                  "timeout this task is named for, is still open and needs re-sizing.")
    j = joined(live_shape)
    assert "do NOT close" in j, f"an explicit 'staying open' was downgraded: {j}"
    assert "READ IT and decide" not in j, f"a deliberate keep-open read as ambiguous: {j}"

    for c in ("This remains open until the follow-up lands.",
              "Leaving it open for now.",
              "This stays open until Q4."):
        assert "do NOT close" in joined(c), f"not treated as a strong refusal: {c!r}"


def test_strong_wins_the_quote_over_weak():
    """A comment carrying both must quote the STRONGER phrase.

    🔴 The first version of this test asserted `"do not close" in j.lower()`, which matches
    the flag's OWN boilerplate `— do NOT close.` It therefore passed at base while base
    quoted "still open", and would have passed with the tiers reversed. Assert the QUOTED
    SPAN, not a substring that the surrounding sentence also contains.
    """
    j = joined("This is still open and the work is done. Do not close.")
    assert '"Do not close"' in j, f"the strong phrase was not what got quoted: {j}"
    assert '"still open"' not in j, f"the weak phrase outranked an explicit refusal: {j}"


# --------------------------------------------------------- controls

def test_widening_ambiguity_did_not_widen_the_close_it_trigger():
    """CONTROL, in the direction that costs money.

    `CLOSURE_VOCAB_RE` is wide (landed/shipped/deployed/…) and `RESOLVED_COMMENT_RE` is
    narrow. Only the narrow one may INSTRUCT a human to close a live ticket; the wide one
    exists solely to decide ambiguity. A comment made only of wide-vocabulary words must
    therefore produce no close-it at all.
    """
    j = joined("The fix landed and shipped to prod yesterday.", transcript_status="unclear")
    assert "close it" not in j, f"the ambiguity vocabulary manufactured a close-it flag: {j}"


def test_the_close_it_flag_still_fires_on_a_clean_resolution():
    """INVARIANT GUARD — passes at base. This flag is the single highest-value thing the tool
    produces and the whole tiering exists to stop suppressing it; a change that also stops
    EMITTING it has traded one silent failure for another.

    🔴 THIS TEST WAS VACUOUS AND OBSERVED A DIFFERENT FLAG THAN ITS OWN NAME. It asserted
    `"close it" in j` at the helper's default `transcript_status="likely_addressed"`, and the
    TRANSCRIPT branch emits "— close it or re-check", whose boilerplate contains that exact
    substring. Measured: deleting the comment-flag trigger outright (RESOLVED_COMMENT_RE
    replaced with a never-matching pattern) left 17 of the 19 tests in this file GREEN.

    So: `transcript_status="unclear"` — which makes the transcript branch unreachable — and
    assert the comment flag's own words. This is the second instance of this class in this
    file alone; the first was `"do not close" in j.lower()` matching the flag's own
    boilerplate. **Never assert a substring the surrounding sentence can also produce.**
    """
    j = joined("Resolved end to end. Recommend closing.", transcript_status="unclear")
    assert "reads as RESOLVED" in j, f"the close-it flag was lost: {j}"
    assert "close it, or say why it stays open" in j, f"the close-it flag was lost: {j}"
    assert "do NOT close" not in j, f"a clean resolution was vetoed: {j}"


def test_both_tiers_stay_gated_on_an_open_status():
    """INVARIANT GUARD — a DONE ticket must never be told "do NOT close" or "read it and
    decide"; there is nothing to decide about a ticket that is already closed. Dropping
    either `cu in OPEN_STATUSES` guard is a mutant that reads as harmless."""
    for c in ("Still live, do not close.",
              "The issue is not resolved.",
              AMBIGUOUS_COMMENTS[0]):
        j = joined(c, status="complete")
        assert "do NOT close" not in j, f"vetoed an already-closed ticket: {c!r} -> {j}"
        assert "READ IT and decide" not in j, f"ambiguity flag on a closed ticket: {c!r} -> {j}"


def test_regex_vocabularies_are_pinned_as_literals():
    """🔴 The phrase ledger must see the set GROW, not only shrink.

    Round 6's N3 mutant added `|done` to the veto regex — which vetoes every comment
    containing the word "done", killing the close-it feature outright — and passed the whole
    suite, because every other test asserts on behaviour it happens to sample. Pinning the
    patterns means a widened vocabulary has to be a deliberate edit here too.
    """
    assert check_addressed.STRONG_KEEP_OPEN_RE.pattern == (
        r"\b(?:do ?n[o']?t close|keep (?:it |this )?open|still live|"
        r"stay(?:s|ing)? open|remains? open|leav(?:e|ing) (?:it |this )?open|"
        r"still (?:broken|happening|occurring|reproducing)|"
        r"reopen(?:ing|ed)?|premature to close)\b")
    assert check_addressed.WEAK_KEEP_OPEN_RE.pattern == r"\bstill open\b"
    assert check_addressed.CLOSURE_VOCAB_RE.pattern == (
        r"\b(?:resolved|fixed|done|merged|deployed|shipped|landed|completed?|closing|closed)\b")
    assert check_addressed.RESOLVED_COMMENT_RE.pattern == (
        r"\b(?:resolved|recommend closing|can be closed|both asks are (?:now )?closed|"
        r"this is done|all (?:asks|items) (?:are )?closed)\b")
    # The two regexes that decide SCOPE. Both were widened by audit findings that a
    # behavioural test alone would not have forced anyone to look at twice.
    assert check_addressed.NEGATOR_RE.pattern == (
        r"\b(?:not|never|no|none|nothing|nobody|without|unable|cannot|can not|"
        r"far from|anything but|"
        r"yet to|fails? to|failed to)\b"
        r"|\b\w+n['’]t\b")
    assert check_addressed.CLAUSE_SPLIT_RE.pattern == (
        r"[.,;!?\n]|[—–]|(?<!anything )(?<!nothing )(?<!everything )"
        r"\b(?:but|however|although|though|whereas|while)\b")
