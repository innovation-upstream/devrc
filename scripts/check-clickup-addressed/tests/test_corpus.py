#!/usr/bin/env python3
"""A LABELLED CORPUS, and the score `disagreements()` must not fall below.

🔴 Why this file exists. Eight rounds of this veto were argued from anecdotes — one clever
sentence at a time, each fix breaking a shape the previous round had not thought of, five
consecutive rounds shipping a regression. What ended that was scoring every candidate against
the SAME labelled set: trunk 24/42, the first tiering attempt 28/42, the version that shipped
39/42. "Better" became a number instead of an argument.

Every case came from a MEASUREMENT — the round-8 table, two blind audits, and a live
ClickUp comment on 868gy0fff. None was invented to make a rule look good.

🔴 The task IDs, author names and comment wording here are SYNTHETIC (this repo is public;
see `tests/test_no_real_identifiers.py`). What is preserved is the SHAPE each case was
chosen for — negator position, clause boundaries, closure vocabulary — because that is the
whole content of a label. Paraphrase, never flatten: a case reworded into a simpler sentence
scores the same and tests nothing.

**When you change the veto, run this first.** A candidate that scores lower is worse, however
good its reasoning sounds. Add the case that motivated your change, with the verdict a human
reading it would give, BEFORE you change any code.

The three known failures are recorded here rather than hidden, and all three are over-vetoes
— the safe direction. A newly-FAILING case in the `CLOSE`/`not-VETO` direction is the one that
must never ship: that is the tool telling a human to close a ticket whose comment refuses.
"""
import importlib.util
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent.parent
spec = importlib.util.spec_from_file_location("check_addressed", SCRIPT_DIR / "check-addressed.py")
check_addressed = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check_addressed)

NOW = datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc)
VETO, CLOSE, READ, NOTVETO = "VETO", "CLOSE", "READ", "not-VETO"

# Measured 2026-08-21 against the shipped implementation. Raising this is progress; a change
# that lowers it is a regression no matter how it reads.
MIN_SCORE = 41

CORPUS = [
    # --- A: a refusal about THIS ticket. Must veto.
    (VETO, "This is still open. I have not had time to look at it."),
    (VETO, "The issue is not resolved."),
    (VETO, "The issue isn't resolved."),
    (VETO, "The issue is never resolved."),
    (VETO, "This won't be resolved until next sprint."),
    (VETO, "This is not fully resolved."),
    (VETO, "This is unresolved."),
    (VETO, "I do not recommend closing this yet."),
    (VETO, "Still live, do not close."),
    (VETO, "We cannot call this resolved."),
    (VETO, "This is far from resolved."),
    (VETO, "This is anything but resolved."),
    (VETO, "This is still open. I haven't done anything yet."),
    (VETO, "This is still open. The bug is not fixed."),
    (VETO, "This is still open — nothing has been merged."),
    (VETO, "This is not (yet) resolved."),
    (VETO, "This is not, in my view, resolved."),   # known failure: see KNOWN_FAILURES
    (VETO, "Status check during ClickUp triage — staying open, and mostly not verifiable "
           "from this repo. Per the thread: part one shipped via a sibling repo, #1093."),

    # --- B: the work is finished. Must produce the close-it flag.
    (CLOSE, "Resolved end to end. Recommend closing."),
    (CLOSE, "Resolved, no further action needed."),
    (CLOSE, "Recommend closing, no further work planned."),
    (CLOSE, "Confirmed resolved, no repro since Tuesday."),
    (CLOSE, "There is no regression left. Resolved — recommend closing."),
    (CLOSE, "The alert wasn't firing, resolved by the rule fix."),
    (CLOSE, "Search returned no results for tag queries, resolved by the reindex."),
    (CLOSE, "The job did not run on Sunday, resolved — I re-ran it."),
    (CLOSE, "Cannot reproduce this anymore, resolved."),
    (CLOSE, "No repro since the deploy, resolved."),
    (CLOSE, "No further action needed, resolved."),
    (CLOSE, "The root cause (never reproduced in staging) is resolved."),

    # --- C: the comment genuinely says both. Must refuse to instruct.
    (READ, "Resolved end to end, recommend closing. (The follow-up PR is still open "
           "but unrelated.)"),
    (READ, "One review thread is not resolved on the PR, but the ticket itself is done. "
           "Recommend closing."),
    (READ, "The alert fired and was not resolved automatically. Ticket work is done — "
           "recommend closing."),
    (READ, "The fix landed in #1234 and is live. The follow-up PR is still open but unrelated."),
    (READ, "Resolved end to end, recommend closing. The upstream task is still open "
           "but that is tracked separately."),
    (READ, "Fix merged. The duplicate task is still open, closing this one."),

    # --- A2: a negator that FOLLOWS the closure word it denies. 🔴 The corpus was blind to
    # this class entirely — measured, all 18 negator/closure pairs in the original 42 put the
    # negator FIRST — so `test_no_new_case_fails_in_the_close_it_direction` could not fail on
    # it however bad it got. Every one is a KNOWN FAILURE drawing "close it" over a refusal,
    # on trunk and here alike: it is the tool's worst outcome, unfixed, and now visible.
    (VETO, "The ticket says resolved but it isn't."),
    (VETO, "This was marked resolved, but it is not."),
    (VETO, "Claimed resolved in standup, but it never was."),
    (VETO, "Resolved and not verified in prod."),

    # --- B2: a clean status line whose own clause ends in a negator. These are the cases the
    # deleted `carry` rule broke, kept as controls so it cannot come back unnoticed.
    (CLOSE, "Downtime: none. Resolved and deployed."),
    (CLOSE, "Impact: none, resolved by the rollback."),
    (CLOSE, "Regressions found: none. Resolved."),

    # --- D: innocent prose. A veto here is a wrong instruction.
    (NOTVETO, "The fix (not the workaround) is deployed."),
    (NOTVETO, "We changed nothing but the config, and it is deployed."),
    (NOTVETO, "Users cannot upload avatars, fixed in #4421 and deployed."),
    (NOTVETO, "No objections to closing."),
    (NOTVETO, "This is done, nothing else outstanding."),
    (NOTVETO, "Nothing else outstanding, this is done."),
]



# Recorded failures, all over-vetoes (the safe direction). Listed so a fix that closes one is
# visibly progress, and so nobody re-derives them as new discoveries.
KNOWN_FAILURES = {
    # A negator inside a parenthetical that qualifies a different noun. Over-veto (safe).
    "The root cause (never reproduced in staging) is resolved.",
    "The fix (not the workaround) is deployed.",
    # "no" attaching to a noun rather than to the closure word. Over-veto (safe).
    "No objections to closing.",
    # 🔴 THE UNSAFE ONES. A negator that FOLLOWS the closure word it denies is not recognised
    # at all, so these draw an affirmative "close it" over a comment that refuses — the worst
    # thing this tool can do. TRUE ON TRUNK TOO; not a regression, and not fixed here. They
    # are listed because a corpus that omits its own worst class is worse than no corpus:
    # the close-it-direction guard below cannot fail on a shape nobody wrote down.
    "The ticket says resolved but it isn't.",
    "This was marked resolved, but it is not.",
    "Claimed resolved in standup, but it never was.",
    "Resolved and not verified in prod.",
    # An interrupted negation — the one case commas-as-boundaries loses. A `carry` rule fixed
    # exactly this and broke seven "Downtime: none. Resolved." shapes, so it was deleted and
    # this went back to being a stated gap.
    "This is not, in my view, resolved.",
}


def _verdict(comment):
    r = {"task_id": "T", "status": "unclear", "clickup_status": "to do", "mentions_found": 3,
         "newest_comment": {"snippet": comment[:200], "text": comment,
                            "date": "2026-08-20 10:00", "author": "x"},
         "completion": [], "open": []}
    out = check_addressed.disagreements([r], now=NOW)
    if not out:
        return "(silent)"
    j = " ".join(out)
    for needle, name in (("do NOT close", VETO), ("READ IT and decide", READ),
                         ("close it", CLOSE)):
        if needle in j:
            return name
    return "?"


def _ok(want, got):
    return got != VETO if want == NOTVETO else got == want


def test_the_corpus_score_has_not_regressed():
    """The headline guard. Scores the real `disagreements()` over every labelled case."""
    good = [c for want, c in CORPUS if _ok(want, _verdict(c))]
    assert len(good) >= MIN_SCORE, (
        f"corpus score fell to {len(good)}/{len(CORPUS)}, below the pinned {MIN_SCORE}. "
        f"Newly failing: {sorted(set(c for _, c in CORPUS) - set(good) - KNOWN_FAILURES)}")


def test_no_new_case_fails_in_the_close_it_direction():
    """🔴 The direction that matters. A comment a human would read as a refusal must never
    draw an affirmative close-it, and none of the recorded failures do."""
    for want, c in CORPUS:
        if want == VETO and c not in KNOWN_FAILURES:
            got = _verdict(c)
            assert got != CLOSE, \
                f"told the operator to CLOSE a ticket whose comment refuses: {c!r}"


def test_the_known_failures_are_still_exactly_these_three():
    """A LEDGER, so the set cannot silently GROW — the same reason the regex patterns are
    pinned as literals. If a fix closes one, delete it here and raise MIN_SCORE."""
    failing = {c for want, c in CORPUS if not _ok(want, _verdict(c))}
    assert failing == KNOWN_FAILURES, (
        f"the known-failure set changed.\n  newly failing: {sorted(failing - KNOWN_FAILURES)}"
        f"\n  now fixed (delete from KNOWN_FAILURES and raise MIN_SCORE): "
        f"{sorted(KNOWN_FAILURES - failing)}")


def test_every_label_is_one_of_the_four():
    """Positive control on the corpus itself: a typo'd label would silently pass `_ok`."""
    assert {w for w, _ in CORPUS} <= {VETO, CLOSE, READ, NOTVETO}
    assert len(CORPUS) == 49, f"corpus size changed to {len(CORPUS)}; update MIN_SCORE too"
