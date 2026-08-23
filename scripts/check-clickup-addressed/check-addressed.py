#!/usr/bin/env python3
"""Orchestrate: recent comments → task IDs → sessions → completion check.

Usage:
    python3 check-addressed.py [--limit N] [--json] [--since YYYY-MM-DD] [--verbose] [--fast]

Default: top 3 most recent comments not from the current user.
--fast: Only check the 10 most recently updated tasks (much faster for large backlogs).
"""
import importlib.util
import json, os, re, subprocess, sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent

# Import the sibling's marker rather than re-deriving it. The threshold and the two glyphs
# are ONE rule; a second copy is a second thing to drift, and round 4 already lost a round
# to a marker that existed in exactly one place and never reached the report (D10).
_spec = importlib.util.spec_from_file_location(
    "_check_completion", SCRIPT_DIR / "check-completion.py")
_check_completion = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_check_completion)
confidence_marker = _check_completion.confidence_marker


def signal_line(kind_glyph, signal, verbose=False):
    """Render one evidence line: kind glyph, then adjacency, then the signal.

    🔴 Two orthogonal facts, two columns. `kind_glyph` is `✓` completion / `○` open — that
    is WHAT the signal says. The bracketed marker is `●` the task ID is inside this
    signal's own ±40-char snippet / `○` merely somewhere in the ±2000-char window — that is
    HOW MUCH to trust the attribution, and `○` is the one to distrust.

    They are kept in separate columns because `○` already meant "open item" here. Round 4
    added `confidence_marker()` and wired it only into check-completion.py's own printer,
    so the entry point everyone actually runs never showed adjacency at all: measured
    2026-08-21, a proximity-1.5 signal and a proximity-1.0 signal rendered IDENTICALLY.
    Merging the two facts into one glyph would have made the report unreadable instead of
    silent, which is not an improvement — hence the brackets.
    """
    width = 120 if verbose else 80
    return f"  {kind_glyph} [{confidence_marker(signal)}] {signal['signal']}: {signal['snippet'][:width]}"


MARKER_LEGEND = ("Evidence lines: `✓` completion signal / `○` open signal · then `[●]` the "
                 "task ID is inside that signal's own snippet, `[○]` merely somewhere in "
                 "the same ±2000-char window — distrust `[○]`.")

# The report header, and simultaneously a SELF-RUN MARKER — `_selfrun.py` matches this exact
# literal, which is how a transcript that merely PASTED a report is recognised as a prior run.
# It is defined once here and emitted by BOTH output branches (the JSON one carries it as the
# `report_header` field); `tests/test_attribution.py` pins that both do. Changing the wording
# without changing `SELF_RUN_MARKERS` silently unhooks the guard from every future report.
SELF_RUN_HEADER = "## Task Completion Status"


def render_json(report):
    """Serialise the report for `--json`, CARRYING THE SELF-RUN MARKER.

    🔴 A --json RUN MUST BE SELF-MARKING TOO. The text branch prints `SELF_RUN_HEADER`, which
    `_selfrun.py` matches, so a transcript holding a pasted report is recognised as a prior
    run. Until 2026-08-22 the JSON branch emitted `json.dumps(report)` and no marker at all —
    so a `--json` invocation, whose output lists every task ID beside `likely_addressed` (the
    exact shape the proximity scorer rewards), was invisible to the guard. Found by an
    adversarial audit of the devrc migration, which RAN the script rather than reading it.

    🔴 This is a FUNCTION so its test can exercise the real path. The first attempt asserted a
    payload the test itself had built with the same dict-merge — it passed with the production
    branch reverted, i.e. it tested nothing. A test may not re-implement the thing it checks.
    """
    return json.dumps({**report, "report_header": SELF_RUN_HEADER}, indent=2)


def run_script(name, *args):
    """Run a sibling script and return its stdout.

    🔴 Child stderr is FORWARDED, not swallowed. `capture_output=True` captures both streams
    and this function used to return only stdout, so anything a child wrote to stderr was
    silently discarded — measured: `recent-comments.py`'s warning that it could not resolve
    the ClickUp user id (and therefore cannot tell your comments from anyone else's) appeared
    ZERO times in this entry point's output, on either stream. A warning nobody can see is
    the same no-op as one that never fires, and this skill has shipped that shape twice
    already. Forwarding costs nothing on the normal path: children are silent there.
    """
    script = SCRIPT_DIR / name
    result = subprocess.run(
        [sys.executable, str(script), *args],
        capture_output=True, text=True, timeout=300
    )
    if result.stderr:
        print(result.stderr, end="", file=sys.stderr)
    return result.stdout, result.returncode


# A ticket whose newest comment declares the work done while the ticket itself is still
# open. This is the single highest-value thing in the report and no transcript scan can
# produce it: on 2026-08-19 `868gx0aaa` sat at `to do` / urgent under a comment reading
# "Resolved ... Recommend closing".
RESOLVED_COMMENT_RE = re.compile(
    r"\b(?:resolved|recommend closing|can be closed|both asks are (?:now )?closed|"
    r"this is done|all (?:asks|items) (?:are )?closed)\b", re.IGNORECASE)

# An explicit refusal to close. This VETOES the close-it flag rather than being weighed
# against it, because that flag is an instruction to a human, not a score.
#
# Measured 2026-08-20 on 868gx0bbb, whose newest comment refused closure in its first
# clause and then said, of a service alert, that it "fired and resolved repeatedly". The
# word "resolved" there describes an ALERT cycling, and the ticket sat at `to do`/urgent — so
# the report told the operator to close a ticket whose reporter had just said, in the same
# sentence, not to. A keyword cannot tell those two senses of "resolved" apart; an explicit
# override can, and it is the reporter's own words doing the overriding.
#
# STRONG = an instruction about THIS ticket that no other reading survives. It is absolute.
# 🔴 `stay(s|ing) open` / `remains open` / `leaving it open` are here because the LIVE
# end-to-end run found them, not the 17-case table. On 2026-08-21 the real ticket
# `868gy0fff` opened with a triage status line declaring the ticket **staying open** and
# mostly not verifiable from this repo, and its only closure vocabulary was one "shipped"
# about a sub-item in another repo. The tier downgraded an unmistakable refusal to "READ IT" —
# trunk's absolute veto had it right, by luck of "still open" appearing later in the comment.
#
# They are STRONG rather than WEAK because, unlike `still open` and `not resolved`, they are
# not this domain's vocabulary for anything else: a PR or an alert is "open", never "staying
# open". A deliberate declaration about what happens to THIS ticket has no second reading.
STRONG_KEEP_OPEN_RE = re.compile(
    r"\b(?:do ?n[o']?t close|keep (?:it |this )?open|still live|"
    r"stay(?:s|ing)? open|remains? open|leav(?:e|ing) (?:it |this )?open|"
    # 🔴 `(?:ticket|task) (?:is |remains? )?still open` was here and was REMOVED. It reads
    # as naming THIS object — "a PR is open, but only this is *the ticket*" — and that
    # premise is false in ClickUp, where comments reference sibling tickets constantly:
    # "the upstream task is still open but that is tracked separately", "the duplicate task
    # is still open, closing this one". Measured, it produced a hard veto on 4 of 4 such
    # comments where the weak tier correctly said READ IT. Weak is the right tier for every
    # "still open" — the ambiguity downgrade is what handles the ones about this ticket.
    r"still (?:broken|happening|occurring|reproducing)|"
    r"reopen(?:ing|ed)?|premature to close)\b", re.IGNORECASE)

# WEAK = a refusal that is MOST OFTEN about something else in this tool's own domain. A
# GitHub review thread is resolved/unresolved, Alertmanager says firing/resolved, and a
# sibling PR is open or closed — so "still open" and "not resolved" are as likely to be
# about a PR or an alert as about the ticket. Measured on trunk 2026-08-21: a comment
# reading "Resolved end to end, recommend closing. (The follow-up PR is still open but
# unrelated.)" on a `to do` ticket emitted **do NOT close** — the veto suppressing exactly
# the flag the reporter asked for.
#
# A weak phrase is NOT ignorable: alone in a comment it is the only statement about
# closure, so it vetoes like a strong one. It downgrades only when the SAME comment also
# carries an un-negated closure claim in another clause — then the comment genuinely says
# both things and this tool refuses to instruct either way. That is the whole tier.
WEAK_KEEP_OPEN_RE = re.compile(r"\bstill open\b", re.IGNORECASE)

# Negation, decided ONCE, at clause level.
#
# Four rounds on the abandoned branch `zach/ccua-self-run-guard` tried per-word lookbehinds
# and lost the same way twice: `(?<!not )(?<!not yet )resolved` guards exactly the two
# spellings it names, so `isn't` / `never` / `won't` / `not fully` / `unresolved` all still
# read as RESOLUTIONS and drew an affirmative "close it" over a comment saying the opposite.
# Enumerating a class is the error; recognising its SHAPE is the fix. Contractions are
# matched by shape (`\w+n't`) rather than by stem because a stem list misses `won't` —
# whose stem is "wo", not "will" — and by construction cannot be completed.
NEGATOR_RE = re.compile(
    r"\b(?:not|never|no|none|nothing|nobody|without|unable|cannot|can not|"
    # Idioms that negate what FOLLOWS them without containing a negator word. `anything but`
    # also carries a lookbehind in CLAUSE_SPLIT_RE so the `but` does not split the phrase
    # away from the word it negates — both halves are needed, and neither works alone.
    r"far from|anything but|"
    r"yet to|fails? to|failed to)\b"
    r"|\b\w+n['’]t\b", re.IGNORECASE)

# Morphological negation: a refusal wearing the closure word's own root, with no separate
# negator anywhere in the clause for NEGATOR_RE to find.
NEGATED_CLOSURE_WORD_RE = re.compile(
    r"\bun(?:resolved|fixed|merged|addressed|deployed|shipped|done|closed)\b", re.IGNORECASE)

# The WIDE closure vocabulary, used ONLY to decide ambiguity and to spot a negated closure
# claim. Deliberately NOT merged into RESOLVED_COMMENT_RE: that regex TRIGGERS the close-it
# instruction, so widening it manufactures close-it flags, whereas widening this one can
# only ever downgrade a veto to "read it" or add a veto. Both directions are the safe one.
# Real comments say landed / shipped / merged / deployed / fixed, and a tier that only
# recognised the six phrasings of RESOLVED_COMMENT_RE would be close to inert on real prose.
CLOSURE_VOCAB_RE = re.compile(
    r"\b(?:resolved|fixed|done|merged|deployed|shipped|landed|completed?|closing|closed)\b",
    re.IGNORECASE)

# Clause boundaries.
#
# 🔴 COMMAS ARE BOUNDARIES, and getting this wrong twice is what makes it worth spelling out.
# The reasoning that excluded them — "This is not, in my view, resolved must keep its negator
# attached" — is true about that one sentence and false about ticket prose in general. The
# dominant shape in a bug comment states the SYMPTOM (negated) and then the RESOLUTION, in
# one comma-spliced sentence:
#
#     "Users cannot upload avatars, fixed in #4421 and deployed."
#     "The alert wasn't firing, resolved by the rule fix."
#     "The job did not run on Sunday, resolved — I re-ran it."
#
# With commas inside the clause, the symptom's negator reaches the resolution and vetoes it.
# Measured against a 42-case labelled corpus built from two blind audits and one live ticket:
# commas-not-a-boundary scored 28/42, commas-a-boundary 39/42. The interrupted-negation case
# that motivated the exclusion is handled by the `carry` rule in `closure_claims` instead —
# narrowly, by the shape that actually distinguishes it (the clause ENDS on its negator).
#
# Parens are NOT boundaries: "This is not (yet) resolved" would strand the closure word in a
# clause of its own and draw an affirmative close-it over a refusal, which is the worst
# outcome this tool has. The cost is a false veto when a parenthetical negates a different
# noun ("The fix (not the workaround) is deployed") — an over-veto, and the safe direction.
#
# `but` carries lookbehinds so the "anything but resolved" idiom is not split apart into a
# clause that reads as a plain resolution.
CLAUSE_SPLIT_RE = re.compile(
    r"[.,;!?\n]|[—–]|(?<!anything )(?<!nothing )(?<!everything )"
    r"\b(?:but|however|although|though|whereas|while)\b",
    re.IGNORECASE)

def clauses(text):
    """Split a comment into clauses. One place, so every caller scopes negation the same way."""
    return [c for c in CLAUSE_SPLIT_RE.split(text or "") if c and c.strip()]


def negated_phrase(clause, neg, m):
    """What to QUOTE back for a negated closure claim.

    The bare vocabulary word is not quotable: `newest comment says "resolved" — do NOT
    close` states the opposite of the comment it is quoting, which is how an operator stops
    trusting the line. Quote the negator through the word instead, so the reason the flag
    fired is legible in the flag itself.

    `neg` always PRECEDES `m` (see `closure_claims`), so the span is a real substring in the
    comment's own word order and the elided form is an honest elision. An earlier version
    allowed a negator found ANYWHERE in the clause, and printed manufactured quotations like
    `"no … resolved"` for a comment reading "Confirmed resolved, no repro since Tuesday" —
    words the comment contains, in an order it never used.
    """
    span = clause[neg.start():m.end()].strip()
    if span and len(span) <= 80:
        return span
    return f"{neg.group(0)} … {m.group(0)}"


def closure_claims(text):
    """(affirmed, negated) closure claims, negation scoped to the clause AND to word order.

    A single pass over CLOSURE_VOCAB_RE, so a word added to that vocabulary inherits the
    negation handling for free — which is the thing per-word lookbehinds could never do.

    🔴 A negator only negates closure words that FOLLOW it. Clause-wide, position-independent
    negation was a regression worse than the bug this tier exists to fix: with commas
    deliberately not a boundary, any trailing "no" / "nothing" reached backwards and inverted
    a plain resolution. Measured by audit — 12 of 12 ordinary "work is finished, nothing
    outstanding" comments flipped from **close it** to **do NOT close**, e.g.

        "Resolved, no further action needed."      -> do NOT close
        "Recommend closing, no further work planned." -> do NOT close

    That is an affirmative wrong instruction, not a safe over-veto, and it would have traded
    4 wrongly-suppressed close-its for 12 wrongly-created vetoes.
    """
    # 🔴 THERE IS NO CARRY ACROSS CLAUSES, and the deleted attempt is worth recording.
    #
    # A `carry` rule once propagated a clause-TRAILING negator into the next clause, to catch
    # the one shape commas-as-boundaries loses: "This is not, in my view, resolved". It bought
    # exactly that one corpus case and cost a whole class, because "ends on a negator" does not
    # distinguish an interrupted negation from the way engineers actually write a clean status:
    #
    #     "Downtime: none. Resolved and deployed."      -> do NOT close   (trunk: close it)
    #     "Impact: none, resolved by the rollback."     -> do NOT close   (trunk: close it)
    #     "Regressions found: none. Resolved."          -> do NOT close   (trunk: close it)
    #
    # It also reintroduced the manufactured quotation `negated_phrase` exists to prevent —
    # `says "none … Resolved"` over a comment whose two words come from different sentences —
    # and it was cleared only by a clause containing a closure word, so it survived arbitrarily
    # many intervening clauses. Suppressing a legitimate close-it is the second-worst thing
    # this tool does, and one corpus point does not buy seven of them.
    #
    # The interrupted-negation shape is now a recorded KNOWN FAILURE in test_corpus.py rather
    # than a rule. Say what is not handled; do not half-handle it.
    affirmed, negated = [], []
    for clause in clauses(text):
        negators = list(NEGATOR_RE.finditer(clause))
        for m in CLOSURE_VOCAB_RE.finditer(clause):
            preceding = [n for n in negators if n.start() < m.start()]
            if preceding:
                # The NEAREST preceding negator, so the quoted span stays tight.
                negated.append(negated_phrase(clause, preceding[-1], m))
            else:
                affirmed.append(m.group(0))
        negated.extend(m.group(0) for m in NEGATED_CLOSURE_WORD_RE.finditer(clause))
    return affirmed, negated


def keep_open_signal(text):
    """('strong'|'weak', phrase) or (None, None) — the reporter's refusal to close, tiered.

    STRONG is checked first and wins outright: a comment carrying both must quote the
    stronger phrase, or the tool would downgrade an explicit "do not close" on the strength
    of an incidental "still open" elsewhere in the same comment.

    A NEGATED closure claim is a weak keep-open signal in its own right, which is how
    `not resolved`, `isn't resolved`, `never resolved`, `won't be resolved`, `not fully
    resolved`, `unresolved` and `I do not recommend closing` all become one rule instead of
    six enumerated spellings.
    """
    m = STRONG_KEEP_OPEN_RE.search(text or "")
    if m:
        return "strong", m.group(0)
    m = WEAK_KEEP_OPEN_RE.search(text or "")
    if m:
        return "weak", m.group(0)
    _affirmed, negated = closure_claims(text)
    # `or m.group(0)`-style fallbacks are not enough here: an empty phrase makes
    # `if veto_phrase:` in disagreements() falsy, which SILENTLY DROPS THE WHOLE VETO rather
    # than printing a blank one. A mutant that emptied the quoted span survived the suite by
    # deleting the flag, so the tier decision must never depend on the phrase being truthy.
    for phrase in negated:
        if phrase and phrase.strip():
            return "weak", phrase.strip()
    if negated:
        return "weak", "a negated closure claim"
    return None, None


# 🔴 The close-it branch below deliberately carries NO negation filter of its own, and that
# is a claim with a premise, not an oversight.
#
# One was written, and the mutation sweep scored it the IDENTITY: reverting it to a bare
# `RESOLVED_COMMENT_RE.search()` changed no test and no behaviour. The branch is only
# reached when `keep_open_signal` returned no tier at all, and every phrase
# RESOLVED_COMMENT_RE can match contains a CLOSURE_VOCAB_RE word — so a negated close-it
# trigger has ALREADY been recorded as a negated closure claim and vetoed one branch above.
# An unreachable guard reads as protection while providing none, so it is deleted rather
# than tested. `test_every_close_it_trigger_phrase_is_also_ambiguity_vocabulary` pins the
# premise: add a phrase to RESOLVED_COMMENT_RE whose words are not in CLOSURE_VOCAB_RE and
# that test goes red, which is the signal to bring the filter back.


OPEN_STATUSES = {"to do", "open", "in progress", "backlog", "todo"}
DONE_STATUSES = {"complete", "closed", "done", "resolved"}

# How recent a comment has to be for "nobody has answered this" to be worth a human's
# attention. See `_waiting_on_a_human` for why this is bounded by RECENCY and not priority.
UNANSWERED_COMMENT_DAYS = 14
COMMENT_DATE_FMT = "%Y-%m-%d %H:%M"


def _comment_age_days(date_str, now):
    """Age of a comment in days, or None if the date cannot be read.

    `recent-comments.py` formats these itself, so an unreadable one means that formatter
    changed — which must not silently turn into "not recent". None is propagated to the
    caller rather than coerced.

    🔴 THE ROUND-5 GUARANTEE WAS NARROWED, DELIBERATELY, AND THIS SENTENCE IS THE RECORD OF
    IT. It used to read "surfaced, never swallowed", and that is no longer true in one case:
    when the display date is unreadable but `date_ms` dates the comment PAST the recency
    bound, the record is now SILENT rather than flagged. Nobody decided that on purpose —
    it fell out of `bound_age` — so it is decided here, in favour of the new behaviour:

      * the guarantee's purpose was that a formatter drift must not silently turn a RECENT
        comment into a stale one. When `date_ms` is readable the age is not unknown, and
        surfacing "age unknown" over a comment we can date to the millisecond would be a
        false claim of ignorance — the mirror of the defect the guarantee was written for.
      * when NO field can date it, the flag still fires and still says the date was
        unreadable. That is the case the guarantee was actually about, and it is intact.

    ⚠️ The residual gap, stated rather than papered over: in that one narrowed case the
    formatter drift itself now goes unannounced. It is not re-added to the decision block
    because it would be an unbounded line about tool health in the one block whose value
    depends on being short — the same trade round 5 made for the flag itself.
    """
    try:
        dt = datetime.strptime((date_str or "").strip(), COMMENT_DATE_FMT).replace(
            tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None
    return (now - dt).total_seconds() / 86400.0


def _ms(value):
    """A raw epoch-ms field as an int, or None if it is not one.

    STRICTER than the producer's own `_epoch_ms`, on purpose: these two keys are written by
    `recent-comments.py` as ints and by nothing else, so a string here means a producer that
    drifted and the right answer is to fall back to the age comparison rather than to guess.

    `bool` is excluded explicitly because `isinstance(True, int)` is True in Python and a
    JSON `true` would otherwise compare as 1 ms — 1970 — making every reply look newer than
    every question. ⚠️ BY-CONSTRUCTION, not an observed input: no producer writes a bool
    here. It is kept because the failure it prevents is silent and wrong rather than loud,
    and it is pinned directly at this function rather than through a record fixture.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _age_days_from_ms(ms, now):
    """Age in days computed from a raw epoch-ms, or None if it is absent or out of range.

    🔴 This exists so the RECENCY BOUND cannot go dead when the display date is unreadable.
    The bound reads `_comment_age_days`, which parses the FORMATTED string — and the producer's
    `format_date` falls back to `str(ts_ms)` for anything `datetime.fromtimestamp` refuses,
    which includes an out-of-range epoch. 🔴 The mechanism is that `_epoch_ms` NEVER RANGE-CHECKS
    at all: it is `int()` plus an except, so it accepts any integer, while `fromtimestamp`
    raises **`ValueError`** ("year … is out of range") on the same value. (An earlier version of
    this sentence blamed `OSError`, which `_epoch_ms` supposedly did not catch — wrong on both
    halves: the exception is `ValueError`, and `_epoch_ms` catches `ValueError` too. The
    `except` clause below is correctly wide; only the explanation was false, which is the worse
    kind of error because it sends the next reader after the wrong divergence.) So a record
    could carry a perfectly good `date_ms` beside an unformattable `date`: age unknown, bound
    skipped, and the ms path still deciding the verdict. Measured while porting this round — a
    2019 ticket printed a suppression note with no bound at all, permanently, because an
    answered ticket never changes an input.

    The exotic case matters less than the systemic one: if `format_date` ever drifts, the
    bound goes dead for EVERY record while the ms path keeps deciding. A bound that silently
    stops bounding is the failure this whole block was designed against.
    """
    if ms is None:
        return None
    try:
        dt = datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
    # `TypeError` because `ms / 1000` is the first thing that touches the value: drop the
    # `_ms()` call at the caller — a one-token edit that survived the suite — and a STRING
    # `date_ms` from a drifted producer reaches this line and raises an exception that was
    # not in this tuple, crashing the whole report. A robust parser is the right place for
    # that, not a second type check at the call site: one rule, one place.
    except (ValueError, TypeError, OSError, OverflowError):
        return None
    return (now - dt).total_seconds() / 86400.0


# The waiting flag's two outcomes. `_waiting_verdict` returns one of these with its line, so
# the report can put them in DIFFERENT blocks: WAITING is an instruction to act, ANSWERED is
# a note about a check that ran and found nothing to do. Merging them would put a line that
# needs no action into the one block the reader is told to act on.
WAITING, ANSWERED = "waiting", "answered"

# 🔴 ClickUp has NO bot identity. Measured independently 2026-08-22 in a sibling tool
# implementing this same predicate: every comment posted through a `pk_` personal token comes
# back authored as the token's OWNER, whoever actually typed it. So "the owner answered" and
# "a machine answered on the owner's behalf" are the SAME observable — there is no field that
# separates them, and no amount of care in this file can recover one.
#
# That matters here specifically because an agent-posted comment sets `my_latest_reply` and
# therefore SUPPRESSES this flag. Suppression printed nothing at all, so a genuinely-waiting
# colleague was dropped with no trace and the caveat had nowhere to live.
#
# Printed ONCE, as the block's first line, not per note. It is 199 characters and repeating it
# on every line put ~11 KB of identical no-action text in a `--limit 20` report — and this
# block's own justification is that VOLUME is how a block stops being read, so repeating the
# caveat to make sure it is seen is self-defeating. Each note carries only its own consequence.
BOT_IDENTITY_CAVEAT = (
    "ClickUp has NO bot identity — every comment posted through the `pk_` token comes back "
    "authored as you, whoever typed it — so \"you answered\" and \"an agent answered as you\" "
    "are the same observable, and EVERY line below rests on that.")


def _reply_answers_the_comment(nc, now):
    """Did my newest reply land at or after the colleague's comment? True / False / None.

    None means the question is UNDECIDABLE from this record — a malformed value on either
    side — and the caller must not read that as either answer. Round 6's rule holds: never
    silently drop a waiting human because a date would not parse.

    🔴 TWO resolutions, and the raw one wins when it is available on BOTH sides. `date_ms`
    and `my_latest_reply_ms` are the epoch-ms `recent-comments.py` formatted the display
    dates FROM; the display dates are minute-resolution, so a reply written 20 seconds
    BEFORE the question renders identical to it and the minute comparison calls it an
    answer. That boundary was a judgement call argued in both directions (round 6 shipped "a
    tie counts as answered" because the minute rendering makes ties common; a parallel review
    argued the opposite from the same evidence). Comparing the ms retires the argument: the
    tie is now a real tie — two comments in the same MILLISECOND — and the sub-minute cases
    decide themselves on the fact.
    `>=` rather than `>` is kept for that genuine tie, so this change can only ever move
    sub-minute cases; it cannot flip the shape round 6 measured.

    The age fallback is UNCHANGED and is not a legacy path: a record from a producer that
    predates the ms fields, or one whose date would not parse, must still get the round-6
    behaviour rather than losing suppression entirely — which would resurrect D12's false
    positive on every such record.
    """
    mine_ms, theirs_ms = _ms(nc.get("my_latest_reply_ms")), _ms(nc.get("date_ms"))
    if mine_ms is not None and theirs_ms is not None:
        return mine_ms >= theirs_ms
    mine = _comment_age_days(nc.get("my_latest_reply"), now)
    theirs = _comment_age_days(nc.get("date"), now)
    if mine is None or theirs is None:
        return None
    # Smaller age = more recent.
    return mine <= theirs


def _waiting_verdict(r, now):
    """A colleague asked something recently and NO work exists anywhere. Returns (kind, line).

    `kind` is WAITING (a human is waiting and nothing exists), ANSWERED (every condition
    held EXCEPT that a reply of mine already answers it — reported, quietly, in its own
    block) or None (nothing to say). One function, two outcomes, because the two verdicts
    share every condition but the last: re-deriving the preconditions in a second function
    is how a predicate ends up wrong at one of its sites.

    This is the highest-signal state the tool can reach and no existing rule produced it:
    nothing "disagrees" — the ticket is open, the transcripts are empty, and they agree.
    Measured live 2026-08-21 on `868gz0hhh` (`to do`/high, two unanswered comments from a
    colleague, 0 mentions): the "Needs a decision" block said nothing at all.

    Three conditions, and the bounding choice matters more than the detection:

    1. **Zero transcript evidence** — gated on `mentions_found == 0`, the STATE, not on the
       word `no_mentions_found`. `no_sessions_found` is the same zero and a guard spelled
       against one word passes while the hazard exists in the other's shape. Once any
       transcript mentions the task there IS evidence and the verdict branches own it.
    2. **Not a done ticket** — deliberately `not in DONE_STATUSES` rather than
       `in OPEN_STATUSES`. Gating on the open vocabulary would rebuild D6's blind spot:
       an `in review` / `blocked` ticket would silently lose the flag. Like the keep-open
       veto, this direction is safe whatever the status word reads. A DONE ticket with no
       evidence is already covered by the "verify before trusting the close" flag, and
       calling it "someone is waiting" would be false.
    3. **A recent comment** — the flag is bounded by RECENCY, not priority. Priority is a
       property of the TICKET: set once, frequently stale, and unchanged by anyone asking
       anything, so a priority bound would fire on every unstarted high-priority backlog
       item every single run — and a permanently-noisy block is worse than no block,
       because it trains the reader to skip it. Recency is a property of the INTERACTION:
       it says a human is waiting NOW, it self-clears as the comment ages, so the flag's
       false-positive volume is bounded by construction, and it composes with the tool's
       own sampling (the report already takes the N most recent comments).

    The comment is guaranteed not to be the user's own: `recent-comments.py` drops every
    comment whose author id equals `me` before any of this runs.

    🔴 That guarantee is also what made this flag BLIND until 2026-08-22, and it is a SEAM
    defect — each side is correct alone. Dropping my own comments is right for a report
    about what other people said; concluding "nobody has answered" from the surviving
    comments is right given the only evidence handed over. Neither component can observe
    the other's assumption, so no care inside this function could recover it. Measured on
    868gz0hhh: the flag said "Commented 2d ago; nobody has answered" over a ticket TWO
    sessions had already answered, the later of them eleven hours earlier — and acting on
    it duplicated an analysis already sitting in the thread. The fix has to cross the seam,
    so `recent-comments.py` now reports `my_latest_reply` and condition 4 reads it.

    4. **I have not already answered it** — suppressed when my newest reply is at or after
       the comment. Compared, not merely counted: a reply PREDATING the question does not
       answer it, which is the ordinary shape on a long-running ticket. An ABSENT
       `my_latest_reply` is a stale producer that never gathered the fact, so the flag
       fires but says the check did not run — the same treatment an unreadable date gets,
       and the reason it cannot be silently disabled by dropping one dict key.

    🔴 And suppression is REPORTED (ANSWERED), because a suppressed flag printed NOTHING and
    that is where the bot-identity caveat had to live. ClickUp cannot tell a comment I typed
    from one an agent posted with my token (`BOT_IDENTITY_CAVEAT`), so condition 4 can be
    satisfied by a machine — and the silent version of that is a waiting colleague dropped
    with no trace at all. The note carries the SAME recency bound as the flag, so it cannot
    become the permanent noise the bound exists to prevent.
    """
    # PRESENT and zero, not `get(..., 0)`. An absent key is a record that never reported a
    # mention count — inferring "no work exists anywhere", the strongest claim this flag
    # makes, from a field that is simply missing is the same mistake as reading a declared
    # field as a code path. Caught by an existing round-2 control whose fixture omits it.
    if r.get("mentions_found") != 0:
        return None, None
    cu = (r.get("clickup_status") or "").lower()
    if cu in DONE_STATUSES:
        return None, None

    nc = r.get("newest_comment") or {}
    if not (nc.get("snippet") or "").strip():
        return None, None
    # An ABSENT date and a MALFORMED one are different facts. Absent = no interaction to be
    # recent about, so there is nothing to claim. Malformed = recent-comments.py's own
    # formatter drifted, which must announce itself rather than quietly read as "stale".
    if not (nc.get("date") or "").strip():
        return None, None

    age = _comment_age_days(nc.get("date"), now)
    # 🔴 The recency bound runs BEFORE any reporting caveat, and the caveats COMPOSE rather
    # than each returning their own message. Written the other way round first, and it broke
    # two existing guards at once: the unreported-reply branch returned early, so a 90-day-old
    # backlog comment was flagged forever (the exact unbounded-noise failure the recency bound
    # exists to prevent) and an unreadable date stopped being surfaced. A condition that
    # returns its own string is a condition that silently owns every condition after it.
    #
    # It now also runs before the ANSWERED branch, which is what bounds the suppression note.
    # Moving it earlier changed no flag outcome AT THE TIME — a stale comment returned None at
    # this line instead of at the answered branch, the same None — but a note that outlived
    # the flag would be exactly the unbounded block the bound was chosen against.
    #
    # ⚠️ That "changes NO flag outcome" claim is no longer true and the correction is kept
    # here rather than the sentence quietly deleted: `bound_age` (below) made this bound
    # reachable on records whose display date is unreadable, and measured base-vs-HEAD it now
    # changes exactly one — an unformattable `date` beside a valid 90-day-old `date_ms`
    # printed a WAITING flag before and is SILENT now. That is the narrowing decided in
    # `_comment_age_days`' docstring, and it is pinned by a corpus case rather than argued.
    #
    # 🔴 TWO ages, deliberately. `age` is what the report DISPLAYS and must stay `None` when
    # the `date` field itself is unreadable — that is a fact about the producer's formatter and
    # the head says so. `bound_age` is what the bound DECIDES on, and it falls back to the raw
    # ms, because the ms path below will happily decide the verdict from `date_ms` while `age`
    # is None: measured while porting, a 2019 ticket printed an unbounded suppression note.
    # A decision and a display are different jobs and reading one field for both is what let
    # the bound go dead. See `_age_days_from_ms`.
    bound_age = age if age is not None else _age_days_from_ms(_ms(nc.get("date_ms")), now)
    if bound_age is not None and bound_age > UNANSWERED_COMMENT_DAYS:
        return None, None

    who = nc.get("author") or "someone"

    # Answered already? Compare instants, not existence — and at millisecond resolution when
    # the producer reported it. A malformed value on either side (`None` here) falls through
    # to the flag rather than suppressing it: never silently drop a waiting human because a
    # date would not parse.
    reply_unreported = "my_latest_reply" not in nc
    # 🔴 `bound_age is not None` is a condition of the NOTE, not of the flag, and the asymmetry
    # is the point. A note is a "no action needed" line: if its recency bound could not be
    # evaluated at all it can never expire, and a permanent no-action line is precisely the
    # noise the bound exists to prevent — so the record falls through to the FLAG instead,
    # which surfaces the unreadable date rather than silently suppressing a colleague.
    #
    # This is the tail of the bound fix above, and without it the fix is only half applied:
    # `_age_days_from_ms` returning None (an out-of-range `date_ms`, which `_epoch_ms` accepts
    # and `format_date` rejects) left the note unbounded by the same mechanism one layer down.
    # It is also what makes that None DISTINGUISHABLE — with the note unguarded, returning
    # `None` and returning `0` from that function produce identical output, so the honest
    # value was doing no work and a mutant swapping it survived.
    if (not reply_unreported and bound_age is not None
            and _reply_answers_the_comment(nc, now) is True):
        when = nc.get("my_latest_reply")
        seen = f"{age:.0f}d ago" if age is not None else f"at {nc.get('date')!r} (unreadable)"
        # 🔴 The line claims only what THIS check did. It used to add "nothing was printed
        # above about this ticket, and `mentions_found` is 0 so no other rule covers it
        # either" — false in four reproduced shapes (an unknown ClickUp status, a
        # RESOLVED-reading comment, a keep-open veto, an open cited PR), each of which puts a
        # line about the SAME ticket directly above this one. And the reason given was a
        # non-sequitur on top of that: not one of those four rules reads `mentions_found`.
        # Whether anything else covers the ticket is a fact `suppressed_notes` can COMPUTE, so
        # it appends that sentence rather than this one asserting it.
        return ANSWERED, (
            f"{r['task_id']}: @{who} commented {seen} and a reply from you at {when} is not "
            f"older, so the WAITING flag is SUPPRESSED. If that reply was an agent's, @{who} "
            f"is still waiting for a human — read the thread before treating this as handled.")

    head = (f"{r['task_id']}: @{who} is WAITING — the ticket is `{cu or 'unknown status'}`, "
            f"and the task ID appears in NO transcript, so no work exists anywhere.")
    if age is None:
        head += (f" (comment date {nc.get('date')!r} was unreadable, so its age is unknown "
                 f"— surfaced rather than assumed stale.)")
    else:
        head += f" Commented {age:.0f}d ago"
        # Only claim nobody answered when that was actually checked.
        head += "." if reply_unreported else "; nobody has answered. Read it."
    if reply_unreported:
        head += (" NOTE: your own replies were not reported by recent-comments.py, so whether "
                 "you already answered could not be checked — read the thread before treating "
                 "this as unanswered.")
    return WAITING, head


def _waiting_on_a_human(r, now):
    """The WAITING flag alone, or None. A thin view over `_waiting_verdict`.

    Kept as its own name because it is what `disagreements` appends to the act-on-this block
    and what four rounds of tests and mutants address. The ANSWERED verdict deliberately does
    NOT come out of here: it would land in "Needs a decision", which is the one block a
    reader is told to act on, and a line saying "no action needed" belongs somewhere else.
    """
    kind, line = _waiting_verdict(r, now)
    return line if kind == WAITING else None


def suppressed_notes(results, now=None):
    """One line per task whose WAITING flag was suppressed by a reply of mine.

    A SIBLING of `disagreements`, not part of it — its lines report a check that ran and
    found nothing to do, which is a different kind of claim from a disagreement and belongs
    in a different block of the report. Both call `_waiting_verdict`, so the preconditions
    exist once.

    🔴 It exists because the alternative is silence, and silence is what this whole skill
    polices. A suppressed flag printed NOTHING, so the ticket vanished from the report
    entirely — no line to carry `BOT_IDENTITY_CAVEAT`, and an agent-posted reply (which
    ClickUp reports as mine, indistinguishably) dropped a genuinely-waiting colleague with
    no trace. Bounded by the same recency window as the flag itself, inside
    `_waiting_verdict`, so it cannot become permanent noise.

    🔴 Each line ends with a COMPUTED statement of whether anything else in "Needs a decision"
    names the same ticket, never an asserted one. The first version asserted that nothing did,
    and four shapes were reproduced where a line about the same ticket sits directly above it —
    an unknown ClickUp status, a RESOLVED-reading comment, a keep-open veto, an open cited PR.
    Running `disagreements` on the single record is what makes the sentence true: the waiting
    flag is suppressed for these records by definition, so whatever comes back is another
    rule's line. One predicate, asked rather than assumed. 🔴 `[r]` and not `results` — the
    sentence is about THIS ticket, and a mutant widening it to the whole report survived a
    green suite because every fixture written for the sentence passed a single-record list.

    The `BOT_IDENTITY_CAVEAT` is NOT repeated per line — `report_blocks` prints it once as the
    block's first line. See the constant for why repeating it defeated its own purpose.
    """
    now = now or datetime.now(timezone.utc)
    out = []
    for r in results:
        kind, line = _waiting_verdict(r, now)
        if kind != ANSWERED:
            continue
        others = disagreements([r], now)
        if others:
            line += (" ⚠️  'Needs a decision' above ALSO carries a line about this ticket — "
                     "this note is only about the waiting check.")
        else:
            line += (" No other flag names this ticket, so nothing else in this report is "
                     "asking you to look at it.")
        out.append(line)
    return out


DECISION_HEADING = "## Needs a decision"
ANSWERED_HEADING = "## Answered already — no action, but check who answered"


def report_blocks(results, now=None):
    """The report's trailing blocks, as (heading, lines) in print order. Empty ones omitted.

    Extracted from `main()` so the WIRING is testable. Both producers below are correct in
    isolation and pinned in isolation, and a producer `main()` never calls is inert with a
    fully green suite — this skill's own headline defect class, twice over already. 🔴 That
    extraction is NOT the test: measured, replacing `main()`'s whole print loop with `pass`
    left the suite green, so `test_main_actually_PRINTS_both_blocks_end_to_end` drives
    `main()` and asserts on STDOUT. A function extracted to make a seam testable and then not
    tested THROUGH has moved the seam rather than closed it.

    🔴 TWO blocks, not one list. The suppression notes report a flag that DID NOT fire and
    ask for no action; "Needs a decision" is the one block the reader is told to act on, and
    diluting it is how a block stops being read. That reasoning is the same one that bounded
    the waiting flag by recency in the first place.
    """
    now = now or datetime.now(timezone.utc)
    blocks = []
    flags = disagreements(results, now)
    if flags:
        blocks.append((DECISION_HEADING, [f"⚠️  {f}" for f in flags]))
    notes = suppressed_notes(results, now)
    if notes:
        # The caveat leads the block ONCE. It applies to every line under it, it is 199
        # characters, and repeating it per line put ~11 KB of identical text in a `--limit 20`
        # report — in the one block whose whole justification is that volume is how a block
        # stops being read.
        blocks.append((ANSWERED_HEADING,
                       [f"🔴 {BOT_IDENTITY_CAVEAT}"] + [f"ℹ️  {n}" for n in notes]))
    return blocks


def disagreements(results, now=None):
    """Cross-check the three independent sources: ticket status, newest comment, transcripts.

    Every branch below is gated on the ClickUp status being one of the nine words above.
    ClickUp statuses are per-list and arbitrary, so that vocabulary is a guess, and until
    2026-08-21 a miss was SILENT: measured, 'in review' / 'blocked' / 'needs qa' / 'review'
    each returned [] — the keep-open veto and the resolved-comment flag both disabled, with
    no output saying so. An empty "Needs a decision" block then reads as "checked, nothing
    disagrees" when it means "not checked at all", which is the reassuring-nothing this
    whole skill exists to police. An unrecognised status is now announced instead.

    Widening the two sets is NOT the fix on its own — it only moves the silence to the next
    unrecognised word. The announcement is what makes a miss visible, so widen the sets when
    a real status shows up in a flag AND keep the announcement.

    `now` is a test seam so the recency bound in `_waiting_on_a_human` is deterministic; it
    defaults to the wall clock and main() calls this with one argument.
    """
    now = now or datetime.now(timezone.utc)
    out = []
    for r in results:
        tid = r["task_id"]
        cu = (r.get("clickup_status") or "").lower()
        nc = r.get("newest_comment") or {}
        # 🔴 TWO WINDOWS, DELIBERATELY DIFFERENT, because the two regexes fail in opposite
        # directions.
        #
        # The VETO reads the FULL comment: it is absolute, so a refusal to close that falls
        # past a truncation boundary silently restores the "close it" instruction. The
        # comment that motivated the veto is 196 characters and cleared the old 200-char cap
        # by four — write the same sentences with the keep-open clause last and the veto
        # misses it. Widening here can only ever SUPPRESS a flag, which is the safe way to
        # be wrong about an instruction to a human.
        #
        # The RESOLVED trigger keeps the 200-char snippet. Feeding it the full 4000 chars
        # widens the close-it trigger 20x in the UNSAFE direction: a long status comment
        # whose only match is "the alert fired and resolved repeatedly" at character 600
        # then produces "close it" on a live ticket — the exact alert-cycling sense of
        # "resolved" this veto exists to neutralise, in a position where the veto cannot
        # help because there is no refusal in the comment at all. Pinned in both directions
        # by `test_widened_window_did_not_widen_the_close_it_trigger`.
        veto_text = nc.get("text") or nc.get("snippet") or ""
        comment = nc.get("snippet") or ""

        tier, phrase = keep_open_signal(veto_text)
        # The ambiguity decision reads the FULL comment while the close-it trigger below
        # keeps the 200-char snippet. Widening THIS window can only downgrade a veto to
        # "read it" or leave it standing — it can never produce a close-it — which is what
        # makes the asymmetry safe in the same way the veto's own widened window is.
        affirmed, _negated = closure_claims(veto_text)
        # A weak refusal ALONGSIDE an un-negated closure claim in another clause: the
        # comment says both things, so the tool states the conflict instead of instructing.
        ambiguous = tier == "weak" and bool(affirmed)
        veto_phrase = phrase if (tier and not ambiguous) else None
        ambiguity_line = None
        if ambiguous:
            ambiguity_line = (
                f"{tid}: newest comment carries BOTH a keep-open phrase (\"{phrase}\") and a "
                f"closure claim (\"{affirmed[0]}\") in different clauses — READ IT and decide. "
                f"A weak keep-open phrase is as often about a PR, an alert or a sibling "
                f"ticket as about this one, so this checker will not instruct either way.")
        known = cu in OPEN_STATUSES or cu in DONE_STATUSES

        if not known:
            shown = f"`{cu}`" if cu else "(none returned)"
            out.append(f"{tid}: ClickUp status {shown} is outside this checker's vocabulary "
                       f"{sorted(OPEN_STATUSES | DONE_STATUSES)} — the ticket/comment "
                       f"cross-check DID NOT RUN for this task. Read the newest comment "
                       f"yourself, and add the status to the right set in check-addressed.py.")
            if veto_phrase:
                # Safe in either direction and it is the reporter's own instruction, so it
                # is surfaced whatever the status reads.
                out.append(f"{tid}: newest comment says \"{veto_phrase}\" — do NOT close.")
            elif ambiguity_line:
                out.append(ambiguity_line)
        elif veto_phrase and cu in OPEN_STATUSES:
            # The reporter's own words outrank both the keyword scan and the transcripts.
            out.append(f"{tid}: newest comment says \"{veto_phrase}\" — do NOT close. "
                       f"Any 'resolved' wording in it is noise; the ticket is correctly `{cu}`.")
        elif ambiguity_line and cu in OPEN_STATUSES:
            out.append(ambiguity_line)
        elif cu in OPEN_STATUSES and RESOLVED_COMMENT_RE.search(comment):
            out.append(f"{tid}: newest comment reads as RESOLVED but the ticket is still "
                       f"`{cu}` — close it, or say why it stays open.")
        elif r["status"] == "likely_addressed" and cu in OPEN_STATUSES:
            out.append(f"{tid}: transcripts read as done, ClickUp still `{cu}` — close it or re-check.")

        # 🔴 `"mentions_found" in r` is the STATE — a transcript scan actually ran — and the
        # status words are only how that state is spelled. Without it this branch is a
        # SPELLED guard: the scan-less record's `not_scanned` sentinel is the only thing
        # keeping it quiet, and renaming that sentinel to `no_mentions_found` fires "open
        # signals remain" off a scan that never happened. That mutant SURVIVED the whole
        # suite upstream. The waiting flag already gates on the state for exactly this
        # reason; a searched zero and an unsearched one are different facts, and only one of
        # them is evidence.
        if ("mentions_found" in r and r["status"] in ("open", "no_mentions_found")
                and cu in DONE_STATUSES):
            out.append(f"{tid}: ClickUp `{cu}` but open signals remain — verify before trusting the close.")

        # A separate `if`, not part of the chain above: nothing DISAGREES here, so it is
        # not a disagreement — it is the absence of anything at all where a human is
        # waiting, and it composes with whatever else this task triggered.
        waiting = _waiting_on_a_human(r, now)
        if waiting:
            out.append(waiting)

        for kind in ("completion", "open"):
            for s in r.get(kind, []):
                for ref in s.get("pr_refs", []):
                    if ref["state"] == "open":
                        out.append(f"{tid}: cited {ref['ref']} is still OPEN, not merged — "
                                   f"the signal that quoted it reads as done.")

    # 🔴 ANNOUNCE THE RULES THAT COULD NOT RUN. The transcript scan is opt-in, and the two
    # checks named below are gated on a SEARCHED zero (`mentions_found == 0`), which the
    # scan-less record does not carry. So in the DEFAULT invocation they are structurally
    # unable to fire — and nothing said so. Worse, when nothing else disagrees the whole
    # "Needs a decision" heading is not printed at all, which reads as *checked, nothing
    # disagrees* when it means *two rules never ran*. That is verbatim the failure this
    # function's own docstring names three paragraphs up, and the reason round 4's
    # unknown-status announcement exists — the same defect one axis over, and it arrived
    # with a change that was individually correct.
    #
    # ONE line per run, not per ticket: the scan is skipped for the whole invocation, so a
    # per-record line would put N identical sentences in the one block whose value depends
    # on being short. Round 4's announcement is per-record because the STATUS varies per
    # record; this fact does not.
    #
    # 🔴 Keyed on `status == "not_scanned"` and NOT on `"mentions_found" not in r`, which is
    # the opposite choice from the guard above — because they answer different questions and
    # the first version of this got it wrong upstream. A missing mention count is a
    # RECORD-level absence with several causes (a stale producer, a hand-built fixture, a
    # partial write); announcing "the scan was skipped for this RUN" from it is an
    # over-claim, and it fired on existing tests whose fixtures omit the key precisely to
    # exercise absent-vs-zero. `not_scanned` is the transcript verdict's OWN value for "no
    # verdict was formed", written by exactly the one code path that skips the scan. That is
    # the narrower and truer signal.
    #
    # The guard above must NOT use it, and this must not use the guard's: a flag that ACTS on
    # evidence needs the state (is there a searched zero?), while an announcement that
    # DESCRIBES the run needs the run's own declaration. Same two facts, opposite directions.
    if any(r.get("status") == "not_scanned" for r in results):
        out.append(
            "transcripts were NOT scanned (this is the default; pass `--transcripts` to "
            "enable), so TWO checks in this block DID NOT RUN for any ticket: 'nobody is on "
            "it and someone is waiting', and 'ClickUp `<done>` but open signals remain'. "
            "Both require a SEARCHED zero and an unsearched one is not the same fact, so "
            "their silence here is not evidence of anything.")
    return out


def parse_search_payload(payload):
    """Return (sessions, self_runs_skipped, skipped_ids) from search-sessions.py output.

    That script now emits an OBJECT rather than a bare list, because the self-run drop was
    otherwise INVISIBLE downstream: the caller saw only a shorter list and a smaller
    "N found" header, which is indistinguishable from the sessions not existing.

    The bare-list branch keeps a stale copy of search-sessions.py degrading to "no count"
    rather than a TypeError mid-report. It exists for a failure nobody would otherwise
    exercise, so it is pinned by a test rather than trusted.
    """
    if isinstance(payload, dict):
        return (payload.get("sessions", []),
                payload.get("self_runs_skipped", 0),
                payload.get("self_runs_skipped_ids", []))
    return payload, 0, []


def skip_note(r):
    """One line stating what the self-run guard dropped, or "" if it dropped nothing.

    The two stages are reported SEPARATELY, never summed: when the search stage hands over
    sessions the completion stage never rediscovers, but when search returns nothing,
    completion falls back to its own corpus scan and a total would count the SAME
    transcripts twice. Two labelled numbers cannot double-count; one total can.

    A function rather than an inline print so the visibility claim is pinned by a test — a
    guard nobody can watch fire is indistinguishable from one wired to nothing, and that
    applies to the line announcing the guard as much as to the guard.
    """
    parts = []
    if r.get("self_runs_skipped_search"):
        parts.append(f"{r['self_runs_skipped_search']} at session-search")
    if r.get("self_runs_skipped"):
        parts.append(f"{r['self_runs_skipped']} at completion")
    if not parts:
        return ""
    return (f"(ignored prior runs of this checker — {', '.join(parts)}; "
            f"they quote every task ID beside 'merged')")


def build_newest_comment(meta):
    """Assemble the per-task comment record.

    `snippet` is a 200-char DISPLAY truncation; `text` is the full comment and is what the
    decision logic reads. Deciding on the truncation is how the keep-open veto gets defeated
    by a sentence reorder — the comment that motivated the veto is 196 characters and
    cleared the old cap by four.
    """
    nc = {
        "date": meta.get("date"),
        "author": meta.get("author"),
        "snippet": (meta.get("snippet") or "")[:200],
        "text": meta.get("text") or meta.get("snippet") or "",
    }
    # Copied only when PRESENT. A `.get()` here would flatten "the producer reported no
    # reply" and "the producer never reported" into the same None, and `_waiting_on_a_human`
    # must tell them apart — one is evidence, the other is a gap to announce.
    if "my_latest_reply" in meta:
        nc["my_latest_reply"] = meta["my_latest_reply"]
    # The raw epoch-ms the two display dates were formatted from, carried across the same
    # hand-off and by the same rule. Absent here means the producer could not read that date
    # (or predates the fields), and `_reply_answers_the_comment` falls back to the
    # minute-resolution ages — so DROPPING either key silently reverts the precision fix and
    # nothing about the report looks different. Both are pinned by a test for that reason,
    # the same way `text` and `my_latest_reply` are.
    for key in ("date_ms", "my_latest_reply_ms"):
        if key in meta:
            nc[key] = meta[key]
    return nc


def parse_args(args):
    """Parse the entry point's flags, rejecting anything it does not know.

    This used to be an inline loop ending in `else: i += 1`, which SWALLOWED every
    unrecognised flag. `--include-self-runs` — documented in SKILL.md as the escape hatch
    that disables the prior-run guard — was never parsed and never forwarded, so passing it
    to the command the Quick start tells you to run did nothing at all and the run reported
    success. A silently-ignored flag is a run that looks like it honoured your request and
    did not; exiting is the whole point.
    """
    opts = {
        "limit": 3, "since": None, "as_json": False, "verbose": False,
        "fast": False,
        # PR resolution defaults ON; results are cached and deduped within a task.
        "resolve_prs": True, "include_self_runs": False,
        # 🔴 Transcript scanning is OPT-IN as of 2026-08-22. Measured upstream over the three
        # tickets in that day's report, the four evidence sources scored: ClickUp status 3/3
        # useful, newest-comment 3/3 and decisive in every case, transcript scan 0/3 (one
        # actively misleading — a `✓ PR merged` whose own snippet read "the race is still
        # unfixed"), cited-PR resolution 0/3 resolved with 2/3 FALSE explanations. The scan is
        # also ~60s of the ~90s runtime and the origin of every false verdict this tool has
        # shipped. It stays available, it is no longer the default.
        "transcripts": False,
    }
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--limit" and i + 1 < len(args):
            opts["limit"] = int(args[i + 1]); i += 2
        elif a == "--since" and i + 1 < len(args):
            opts["since"] = args[i + 1]; i += 2
        elif a == "--transcripts":
            opts["transcripts"] = True; i += 1
        elif a == "--no-transcripts":
            opts["transcripts"] = False; i += 1
        elif a == "--json":
            opts["as_json"] = True; i += 1
        elif a == "--verbose":
            opts["verbose"] = True; i += 1
        elif a == "--fast":
            opts["fast"] = True; i += 1
        elif a == "--no-resolve-prs":
            opts["resolve_prs"] = False; i += 1
        elif a == "--include-self-runs":
            opts["include_self_runs"] = True; i += 1
        else:
            print(f"check-addressed.py: unknown argument {a!r}\n"
                  f"Usage: check-addressed.py [--limit N] [--since YYYY-MM-DD] [--json] "
                  f"[--verbose] [--fast] [--transcripts] [--no-transcripts] "
                  f"[--no-resolve-prs] [--include-self-runs]",
                  file=sys.stderr)
            sys.exit(2)
    return opts


def main():
    opts = parse_args(sys.argv[1:])
    limit = opts["limit"]
    as_json = opts["as_json"]
    verbose = opts["verbose"]
    fast = opts["fast"]
    since = opts["since"]
    resolve_prs = opts["resolve_prs"]
    include_self_runs = opts["include_self_runs"]
    transcripts = opts["transcripts"]

    # Step 1: Get recent comments
    recent_args = ["--limit", str(limit), "--json"]
    if fast:
        recent_args.append("--fast")
    out, rc = run_script("recent-comments.py", *recent_args)
    if rc != 0:
        print(f"Error fetching comments: {out}", file=sys.stderr)
        sys.exit(1)

    comments = json.loads(out)
    if not comments:
        print("No recent comments from others on assigned tasks.")
        return

    # Step 2: Extract unique task IDs
    task_ids = list(dict.fromkeys(c["task_id"] for c in comments))

    # Never read the transcript this check is itself being written into: every task ID
    # under test appears there by construction, so it self-matches on all of them.
    self_session = os.environ.get("CLAUDE_CODE_SESSION_ID", "")

    # Step 3+4: Search sessions and check completion, PER TASK.
    #
    # This used to merge every task's ID plus four words from every task NAME into one
    # <=8-term bag and issue a single search. search-sessions.py ANDs its terms, so the
    # query asked for one session mentioning all four unrelated tasks at once — which
    # never exists. It returned 0 sessions on every multi-task run, check-completion.py
    # silently fell back to its own per-task scan, and the report still printed
    # "sessions_found: 0" describing a search whose result nothing had used.
    sessions_by_task = {}
    results = []
    for tid in task_ids:
        if not transcripts:
            # 🔴 `mentions_found` is deliberately ABSENT, not 0. Absent means "not gathered";
            # 0 is the positive claim "searched, and this task appears nowhere". The waiting
            # flag fires on `mentions_found == 0` and `r.get(...) != 0` already sends an
            # absent key down the no-claim path — so a scan-less run cannot flag every open
            # ticket as abandoned. Pinned by a test; emitting 0 here would fire it on all.
            meta = next((c for c in comments if c["task_id"] == tid), {})
            results.append({
                "task_id": tid,
                "status": "not_scanned",
                "sessions_searched": 0,
                "completion": [], "open": [], "sessions": [],
                "clickup_status": meta.get("task_status"),
                "clickup_priority": meta.get("task_priority"),
                "newest_comment": build_newest_comment(meta),
            })
            continue

        search_args = ["--limit", "5", "--json"]
        if since:
            search_args.extend(["--since", since])
        if self_session:
            search_args.extend(["--exclude-session", self_session])
        if include_self_runs:
            search_args.append("--include-self-runs")
        search_args.append(tid)

        out, rc = run_script("search-sessions.py", *search_args)
        sessions, search_skipped, skipped_ids = [], 0, []
        if rc != 0:
            print(f"Error searching sessions for {tid}: {out}", file=sys.stderr)
        else:
            sessions, search_skipped, skipped_ids = parse_search_payload(json.loads(out))
        sessions_by_task[tid] = sessions

        check_args = ["--task", tid, "--json"]
        for s in sessions[:5]:
            check_args.extend(["--session", s["session_id"]])
        if self_session:
            check_args.extend(["--exclude-session", self_session])
        # The self-runs the SEARCH stage already dropped, so the completion stage cannot
        # rediscover and re-count the same transcripts — without this the two stages'
        # counts overlap and the report names one set of files twice.
        for sid in skipped_ids:
            check_args.extend(["--exclude-session", sid])
        if include_self_runs:
            check_args.append("--include-self-runs")
        if resolve_prs:
            check_args.append("--resolve-prs")

        out, rc = run_script("check-completion.py", *check_args)
        if rc == 0:
            task_results = json.loads(out)
            if task_results:
                task_results[0]["sessions"] = [
                    {"id": s["session_id"], "date": s["date"], "hits": s["hits"]}
                    for s in sessions[:3]
                ]
                # The ticket's own state, carried through from ClickUp. It is the
                # authority the transcript scan cannot see, so it is reported beside
                # every verdict rather than left in the comments block.
                meta = next((c for c in comments if c["task_id"] == tid), {})
                task_results[0]["clickup_status"] = meta.get("task_status")
                task_results[0]["clickup_priority"] = meta.get("task_priority")
                task_results[0]["newest_comment"] = build_newest_comment(meta)
                task_results[0]["self_runs_skipped_search"] = search_skipped
                results.append(task_results[0])

    # Step 5: Build report
    report = {
        "comments": comments,
        "sessions_found": sum(len(v) for v in sessions_by_task.values()),
        "sessions_by_task": {k: len(v) for k, v in sessions_by_task.items()},
        "excluded_self_session": self_session or None,
        "task_status": results,
    }

    if as_json:
        print(render_json(report))
    else:
        print(f"## Recent Comments ({len(comments)})\n")
        for c in comments:
            print(f"- **{c['task_name'][:50]}** ({c['task_id']})")
            print(f"  {c['date']} by @{c['author']}: {c['snippet'][:100]}")
            print()

        for tid, tsessions in sessions_by_task.items():
            if not tsessions:
                continue
            print(f"\n## Matching Sessions for {tid} ({len(tsessions)} found)\n")
            show = tsessions if verbose else tsessions[:3]
            for s in show:
                print(f"- [{s['date']}] {s['project']} — {s['hits']} hits")
                print(f"  {s['opening'][:100]}")
                print(f"  `claude --resume {s['session_id']}`")
                if verbose:
                    terms_str = ", ".join(f"{t}({c})" for t, c in s.get("term_hits", {}).items())
                    print(f"  terms: {terms_str}")
                    print(f"  file: {s.get('file', '?')}")
                print()

        print(f"\n{SELF_RUN_HEADER}\n")
        if transcripts:
            print(f"{MARKER_LEGEND}\n")
        else:
            print("Transcripts were NOT scanned (`--transcripts` to enable). Everything below "
                  "comes from ClickUp: the ticket's own status and its newest comment. That is "
                  "not a claim that no work exists — it is the absence of a claim.\n")
        for r in results:
            status_icon = {"likely_addressed": "✅", "partially_addressed": "⚠️", "open": "🔴", "unclear": "❓", "no_sessions_found": "🔍", "no_mentions_found": "🔍", "not_scanned": "—"}.get(r["status"], "?")
            cu = r.get("clickup_status") or "?"
            prio = r.get("clickup_priority") or "-"
            if r["status"] == "not_scanned":
                print(f"{status_icon} **{r['task_id']}** — transcripts not scanned")
            else:
                print(f"{status_icon} **{r['task_id']}** — transcripts say `{r['status']}` "
                      f"(searched {r['sessions_searched']} sessions, {r.get('mentions_found', 0)} mentions)")
            print(f"     ClickUp says: **{cu}** / prio {prio}")
            note = skip_note(r)
            if note:
                print(f"     {note}")
            nc = r.get("newest_comment") or {}
            if nc.get("snippet"):
                print(f"     newest comment [{nc.get('date')}] @{nc.get('author')}: {nc['snippet'][:110]}")

            show_completion = r.get("completion", []) if verbose else r.get("completion", [])[:3]
            show_open = r.get("open", []) if verbose else r.get("open", [])[:3]
            for glyph, group in (("✓", show_completion), ("○", show_open)):
                for s in group:
                    print(signal_line(glyph, s, verbose))
                    for ref in s.get("pr_refs", []):
                        print(f"      ↳ {ref['ref']}: {ref['state']}")
            print()

        # Summary
        addressed = sum(1 for r in results if r["status"] == "likely_addressed")
        partial = sum(1 for r in results if r["status"] == "partially_addressed")
        open_count = sum(1 for r in results if r["status"] == "open")
        unclear = sum(1 for r in results
                      if r["status"] in ("unclear", "no_sessions_found", "no_mentions_found"))
        if transcripts:
            print(f"## Summary: {addressed} addressed, {partial} partial, {open_count} open, {unclear} unclear")
        else:
            # A tally of transcript verdicts nobody computed reads as "we looked and found
            # nothing addressed" — four zeroes that look like a finding. Say what happened.
            print(f"## Summary: {len(results)} ticket(s) read from ClickUp; transcripts not "
                  f"scanned, so no completion verdict was formed for any of them.")

        for heading, lines in report_blocks(results):
            print(f"\n{heading}\n")
            for line in lines:
                print(line)


if __name__ == "__main__":
    main()
