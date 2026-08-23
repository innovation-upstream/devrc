#!/usr/bin/env python3
"""Round 8 (2026-08-22). Three invariants the prose GUARANTEED and nothing pinned, plus the
parser robustness a one-token mutant exposed.

⚠️ THESE ARE MUTATION-COVERAGE GUARDS, NOT REGRESSION COVERAGE — and the label needs one
extra sentence here that upstream did not need. Upstream these are literally green at its
base, because its base already carries the round-7 code they cover. **Measured against THIS
repo's pre-port `main`, four of the five below are RED** — with `AttributeError: module
'check_addressed' has no attribute 'suppressed_notes' / '_age_days_from_ms'`. That red is the
code being ABSENT, not the code being wrong: pre-port `main` never reaches these cases at all,
so a red here is not evidence that anything was fixed. **Do not count them in the port's
regression matrix.** What they are for is the mutants: each one below SURVIVED the full
battery of the round that introduced the code it covers, on a tree where the behaviour was
already correct.

  * scoping — `disagreements([r], now)` -> `disagreements(results, now)` in `suppressed_notes`.
    The guard written for that sentence only ever passed a single-record list, so it was
    structurally blind to the very thing the sentence claims.
  * the DISPLAY half of the two-ages split — reporting `bound_age` where `age` belongs
    survived, so a record with an unreadable display date would print a confident "Nd ago"
    derived from a field the formatter rejected. The decision half was well pinned; the
    display half, which is the stated REASON for having two, was not.
  * `UNANSWERED_COMMENT_DAYS` — `>` -> `>=` AND `> 14` -> `> 28` both survived, because the
    fixtures sat at 13 and 30 and 30 is more than 2x the constant. 🔴 A fixture that
    overshoots by a MULTIPLE of the step cannot see a doubling mutant. The cases below
    overshoot deliberately (20) and include one exactly ON the boundary (14.0).

🔴 NOT PORTED, deliberately: the upstream copy of this file opens with a scan-less-run
ANNOUNCEMENT — one line per run saying that the two transcript-gated rules could not fire —
because upstream made the transcript scan opt-in. This tool has no such flag: `main()` always
runs search-sessions.py and check-completion.py, no record ever carries a `not_scanned`
status, and `mentions_found` is always present. Porting the announcement would add a branch
no input here can reach, and an unreachable guard reads as protection while providing none.
The same goes for the `"mentions_found" in r` gate its sibling puts on the "open signals
remain" flag: that gate exists to stop an UNSEARCHED zero firing it, and there is no
unsearched zero here. If a `--transcripts` opt-in ever lands, both are required in the same
commit.
"""
import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent.parent
spec = importlib.util.spec_from_file_location("check_addressed", SCRIPT_DIR / "check-addressed.py")
check_addressed = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check_addressed)

NOW = datetime(2026, 8, 22, 12, 0, tzinfo=timezone.utc)
FMT = check_addressed.COMMENT_DATE_FMT


def _fmt(dt):
    return dt.strftime(FMT)


def _ms(dt):
    return int(dt.timestamp() * 1000)


def _rec(tid="868gz0hhh", days=2, reply_days=None, status="to do", date_str=None, date_ms=True):
    """A `task_status` record, carrying both the display strings and the raw epoch-ms."""
    their = NOW - timedelta(days=days)
    nc = {"date": date_str if date_str is not None else _fmt(their),
          "author": "Robin Example", "snippet": "a question", "text": "a question"}
    if date_ms:
        nc["date_ms"] = _ms(their)
    if reply_days is not None:
        mine = NOW - timedelta(days=reply_days)
        nc["my_latest_reply"] = _fmt(mine)
        nc["my_latest_reply_ms"] = _ms(mine)
    else:
        nc["my_latest_reply"] = None
    return {"task_id": tid, "status": "no_mentions_found", "sessions_searched": 1,
            "mentions_found": 0, "clickup_status": status, "clickup_priority": "high",
            "newest_comment": nc, "completion": [], "open": []}


def _flags(*recs):
    return check_addressed.disagreements(list(recs), now=NOW)


def _waiting(*recs):
    return [f for f in _flags(*recs) if "WAITING" in f]


def _notes(*recs):
    return check_addressed.suppressed_notes(list(recs), now=NOW)


def test_the_other_coverage_sentence_is_scoped_to_ITS_OWN_ticket():
    """MUTATION-COVERAGE GUARD (see the module docstring: red against pre-port `main` only
    because the code is ABSENT there, so it is NOT regression coverage). 🔴 A SURVIVING MUTANT:
    `disagreements([r], now)` -> `disagreements(results, now)`.

    This is the crux of the finding that produced the sentence at all. Under the mutant, ONE
    flagged ticket anywhere in the report makes EVERY suppression note claim that "'Needs a
    decision' above ALSO carries a line about this ticket" — a false statement about a
    different ticket, which is the same species of defect as the claim it replaced.

    The guard written for it only ever passed a SINGLE-record list, so it could not observe
    scoping at all; the only multi-record path never asserted the sentence. **A guard whose
    fixture cannot express the failure is not narrower than its docstring — it is blind to it.**
    """
    flagged = _rec(tid="868gx0aaa", status="blocked")             # unknown status -> a flag
    answered = _rec(tid="868gx0bbb", reply_days=0.5)              # answered, nothing else
    notes = _notes(flagged, answered)
    assert len(notes) == 1, f"expected one note (for the answered ticket only): {notes}"
    assert "868gx0bbb" in notes[0], f"the note is about the wrong ticket: {notes[0]}"
    assert "no other flag names this ticket" in notes[0].lower(), (
        "the note claimed another block carries a line about ITS ticket, when the only flag "
        f"in the report is about a DIFFERENT one: {notes[0]}")

    # And the true direction, in the same report: a ticket that IS flagged elsewhere says so.
    both = _rec(tid="868gx0aaa", status="blocked", reply_days=0.5)
    notes = _notes(both, _rec(tid="868gx0zzz", reply_days=0.5))
    by_tid = {n.split(":")[0]: n.lower() for n in notes}
    assert "also carries a line about this ticket" in by_tid["868gx0aaa"], \
        f"a ticket that IS flagged above did not say so: {by_tid['868gx0aaa']}"
    assert "no other flag names this ticket" in by_tid["868gx0zzz"], \
        f"a ticket flagged nowhere claimed it was: {by_tid['868gx0zzz']}"


def test_the_DISPLAY_age_never_comes_from_the_raw_ms():
    """MUTATION-COVERAGE GUARD (see the module docstring: red against pre-port `main` only
    because the code is ABSENT there, so it is NOT regression coverage). 🔴 A SURVIVING MUTANT: report `bound_age`
    where `age` belongs, in both report lines.

    The two-ages split exists precisely so a DECISION can use the raw ms while the DISPLAY
    stays honest about the `date` field the reader is looking at. Only the decision half was
    pinned, so printing a confident "Commented 2d ago" over a date the formatter REJECTED
    survived the suite — a number attributed to a field that never produced it.

    The corpus cannot see this: `_verdict` classifies ANSWERED off the note's existence, not
    its text, so both sides score identically there.
    """
    # the WAITING head
    r = _rec(days=2, date_str="99999999999999999")
    flags = _waiting(r)
    assert flags, "the flag stopped firing on an unreadable display date"
    assert "unreadable" in flags[0].lower(), \
        f"the head did not admit the date field was unreadable: {flags[0]}"
    assert "commented 2d ago" not in flags[0].lower(), (
        "the head printed a confident age derived from `date_ms` while claiming to describe "
        f"the `date` field the formatter rejected: {flags[0]}")

    # the suppression note
    note = _notes(_rec(days=2, reply_days=0.5, date_str="99999999999999999"))
    assert len(note) == 1, f"expected one note: {note}"
    assert "unreadable" in note[0].lower() and "2d ago" not in note[0].lower(), (
        f"the note printed a raw-ms age as though the display date had produced it: {note[0]}")


def test_the_recency_bound_is_pinned_ON_its_boundary_and_at_an_overshoot():
    """MUTATION-COVERAGE GUARD (see the module docstring: red against pre-port `main` only
    because the code is ABSENT there, so it is NOT regression coverage). 🔴 TWO SURVIVING MUTANTS at one constant:
    `>` -> `>=`, and `> 14` -> `> 28`.

    The old fixtures sat at 13 and 30 days. 13 cannot see `>=`; 30 is more than 2x14, so a
    DOUBLING mutant passes straight through the gap between them. "The note is bounded by
    recency" is the design argument for this entire block, and it could be silently widened
    with a green suite.

    Three points, chosen so no single mutation satisfies all of them: exactly ON the boundary
    (14.0 -> fires, kills `>=`), just past it (14.5 -> silent), and an overshoot that is NOT a
    multiple of the step (20 -> silent, kills `> 28`).
    """
    assert _waiting(_rec(days=14)), (
        f"a comment exactly {check_addressed.UNANSWERED_COMMENT_DAYS}d old was treated as "
        "stale — the bound is exclusive, and a comment ON the boundary is inside it")
    assert _waiting(_rec(days=14.5)) == [], "a comment past the bound was still flagged"
    assert _waiting(_rec(days=20)) == [], \
        "a 20-day-old comment was flagged, so the bound is wider than it says it is"
    # the note inherits the same bound, at the same three points
    assert _notes(_rec(days=14, reply_days=13.9))
    assert _notes(_rec(days=14.5, reply_days=14.4)) == []
    assert _notes(_rec(days=20, reply_days=19)) == []


def test_age_from_ms_survives_a_value_that_is_not_a_number():
    """🔴 A SURVIVING MUTANT: drop the `_ms()` call in the `bound_age` fallback.

    Under it a STRING `date_ms` from a drifted producer reaches `ms / 1000` and raises
    `TypeError`, which was NOT in this function's except tuple — the whole report crashes on a
    malformed field. `TypeError` is now caught here rather than at the call site: the parser is
    the right place to be robust, one rule in one place.
    """
    assert check_addressed._age_days_from_ms("1755800000000", NOW) is None, \
        "a string raw-ms crashed or was accepted as an instant"
    assert check_addressed._age_days_from_ms(None, NOW) is None
    assert check_addressed._age_days_from_ms(99999999999999999, NOW) is None, \
        "an out-of-range epoch was accepted as an instant (fromtimestamp raises ValueError)"
    real = check_addressed._age_days_from_ms(int((NOW - timedelta(days=3)).timestamp() * 1000), NOW)
    assert real is not None and abs(real - 3.0) < 0.01, \
        f"a usable epoch did not yield its age — positive control failed: {real}"


def test_a_bool_raw_ms_does_not_date_the_comment_to_1970():
    """⚠️ BY-CONSTRUCTION, and it is what makes `_ms()` load-bearing in the bound fallback.

    `isinstance(True, int)` is True, so without `_ms` a JSON `true` divides to 0.001s — 1970 —
    and the bound trips, silently dropping a recent comment. No producer writes a bool here;
    the guard is kept because its failure is quiet and wrong rather than loud, and pinned at
    the record level because that is where the fallback reads it.
    """
    r = _rec(days=2)
    r["newest_comment"]["date"] = "99999999999999999"     # force the fallback
    r["newest_comment"]["date_ms"] = True
    assert _waiting(r), \
        "a boolean raw-ms dated the comment to 1970 and the recency bound dropped it"
