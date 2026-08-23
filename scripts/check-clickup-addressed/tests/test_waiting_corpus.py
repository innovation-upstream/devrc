#!/usr/bin/env python3
"""A LABELLED CORPUS OF RECORDS for the WAITING flag, and the score it must not fall below.

🔴 Why a SECOND corpus. `test_corpus.py` scores COMMENT TEXT — it exists because eight rounds
of the keep-open veto were argued one clever sentence at a time. The waiting flag reads none
of that text: it reads FIELDS (`mentions_found`, `clickup_status`, `date`, `my_latest_reply`,
and now two raw-ms fields), so every case in that corpus is blind to it. Three rounds of this
flag have now been argued from single anecdotes — the live `868gz0hhh` false positive, then
the tie boundary, argued to opposite conclusions from the SAME evidence by two parallel
reviews. That is the pattern a scoreboard ended for the veto, and this is the same instrument
pointed at the other half of the tool.

**When you change the flag, run this first. Add the case that motivated your change, with the
verdict a human reading the report would give, BEFORE you change any code.**

🔴 THE VERDICT IS READ OFF THE CLAIM, NEVER OFF WHETHER SOMETHING FIRED. "The flag fired" is
satisfied by four different lines that tell a reader four different things — that nobody has
answered, that the own-reply check never ran, that the date was unreadable — and this skill's
own audit found a guard asserting mere truthiness while its docstring claimed a relationship,
which let a fix-reverting mutant survive a green suite. `_verdict` therefore classifies on the
sentence the reader acts on.

Every fixture here is SYNTHETIC — see `tests/test_no_real_identifiers.py`, the ledger gate
this repo is public for. The SHAPES are what came from measurement: the live 868gz0hhh
record, the round-6 fixtures, the disputed sub-minute boundary, and the ClickUp bot-identity
finding of 2026-08-22.
"""
import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent.parent
spec = importlib.util.spec_from_file_location("check_addressed", SCRIPT_DIR / "check-addressed.py")
check_addressed = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check_addressed)

spec2 = importlib.util.spec_from_file_location("recent_comments", SCRIPT_DIR / "recent-comments.py")
recent_comments = importlib.util.module_from_spec(spec2)
spec2.loader.exec_module(recent_comments)

NOW = datetime(2026, 8, 22, 12, 0, tzinfo=timezone.utc)
SNIPPET = "Follow-up from the reporter, asking for a status update"

# The four claims the report can make about one task, plus silence.
WAITING = "WAITING"          # "…nobody has answered. Read it."
ANNOUNCE = "ANNOUNCE"        # fired, but says the own-reply check did not run
UNREADABLE = "UNREADABLE"    # fired, but says the comment date could not be read
ANSWERED = "ANSWERED"        # suppressed, and SAID SO in the answered-already block
SILENT = "SILENT"            # nothing printed about this task at all
LABELS = (WAITING, ANNOUNCE, UNREADABLE, ANSWERED, SILENT)

# Measured against the shipped implementation: 21/21, no known failures. Raising this is
# progress; a change that lowers it is a regression however it reads. Against the pre-port
# tree the same 21 cases score 10 — the eleven it fails are listed in
# `claude/skills/check-clickup-addressed/reference/validation-history.md`, not here, because a
# KNOWN_FAILURES set on a corpus that has none is an invitation to add one.
MIN_SCORE = 21


def _fmt(dt):
    return dt.strftime(check_addressed.COMMENT_DATE_FMT)


def _ms(dt):
    return int(dt.timestamp() * 1000)


def _rec(their, mine=None, reply_reported=True, with_ms=True, their_ms=True, mine_ms=True,
         status="to do", mentions=0, date_str=None):
    """One `task_status` record in the shape check-addressed.py builds from a live run.

    `their` / `mine` are instants; the record carries the MINUTE-rendered strings the report
    displays, plus the raw epoch-ms `recent-comments.py` formatted them from. The three ms
    switches exist because the mixed case — one side raw, one side rounded — is a distinct
    hazard from having neither, and a corpus that cannot express it cannot score it.
    """
    nc = {"date": date_str if date_str is not None else _fmt(their),
          "author": "Robin Example", "snippet": SNIPPET, "text": SNIPPET}
    if with_ms and their_ms:
        nc["date_ms"] = _ms(their)
    if reply_reported:
        nc["my_latest_reply"] = _fmt(mine) if mine is not None else None
        if with_ms and mine_ms and mine is not None:
            nc["my_latest_reply_ms"] = _ms(mine)
    r = {"task_id": "868gz0hhh", "status": "no_mentions_found", "sessions_searched": 1,
         "clickup_status": status, "clickup_priority": "high",
         "newest_comment": nc, "completion": [], "open": []}
    if mentions is not None:
        r["mentions_found"] = mentions
    return r


def _rec_via_producer(my_date_raw, their_ms_raw="1755819600000"):
    """A record built by the REAL producer chain, not hand-written.

    `latest_reply_ts_by` -> `build_record` -> `build_newest_comment`, so the case scores the
    seam rather than one side of it. Used for the unreadable-own-date case, whose whole point
    is what the PRODUCER emits: a hand-written record would encode the answer being tested.
    """
    comments = [
        {"user": {"id": "9", "username": "Robin Example"}, "date": their_ms_raw},
        {"user": {"id": "7", "username": "me"}, "date": my_date_raw},
    ]
    reply = recent_comments.latest_reply_by(comments, "7")
    # BASE-COMPAT, for the same reason `_blocks` reaches for the notes producer with getattr:
    # neither the ms argument nor `latest_reply_ts_by` exists on the pre-port tree, and a
    # corpus that cannot be pointed at base can only ever describe the code it shipped with. A
    # base that cannot build the case scores it as a FAILURE — which is the correct verdict
    # for a base that cannot represent the fact, not a skip.
    ts_fn = getattr(recent_comments, "latest_reply_ts_by", None)
    reply_ts = ts_fn(comments, "7") if ts_fn else None
    try:
        meta = recent_comments.build_record(
            "868gz0hhh", "t", {}, comments[0], SNIPPET, reply, reply_ts)
    except TypeError:
        meta = recent_comments.build_record(
            "868gz0hhh", "t", {}, comments[0], SNIPPET, reply)
    nc = check_addressed.build_newest_comment(meta)
    # The producer's date is a fixed instant; re-render it against NOW so the case sits
    # inside the recency window whatever day the suite runs on. The ms fields are left as
    # the producer wrote them — they are only ever compared with each other.
    nc["date"] = _fmt(NOW - timedelta(days=2))
    return {"task_id": "868gz0hhh", "status": "no_mentions_found", "sessions_searched": 1,
            "mentions_found": 0, "clickup_status": "to do", "clickup_priority": "high",
            "newest_comment": nc, "completion": [], "open": []}


def _with(r, **fields):
    """Override `newest_comment` fields on a built record, for shapes `_rec` cannot express.

    Kept deliberately blunt: a builder that grows a keyword for every one-off case stops being
    readable, and a corpus whose fixtures are hard to read is a corpus nobody audits.
    """
    r["newest_comment"].update(fields)
    return r


D = timedelta(days=1)
S = timedelta(seconds=1)
THEIRS = NOW - 2 * D          # the colleague's comment, inside the 14-day window

CORPUS = [
    # --- A: the flag's own job. A human is waiting and nothing exists anywhere.
    (WAITING, "reply_older_than_the_question",
     _rec(their=NOW - D, mine=NOW - 5 * D)),
    (WAITING, "producer_reported_no_reply_at_all",
     _rec(their=THEIRS, mine=None)),
    (WAITING, "sub_minute_reply_20s_BEFORE_the_question",
     _rec(their=THEIRS + 30 * S, mine=THEIRS + 10 * S)),

    # --- B: I answered. Suppressed, and the suppression is REPORTED — see BOT_IDENTITY.
    (ANSWERED, "the_live_868gz0hhh_shape_reply_11h_later",
     _rec(their=THEIRS, mine=NOW - timedelta(hours=11))),
    (ANSWERED, "sub_minute_reply_40s_AFTER_the_question",
     _rec(their=THEIRS + 10 * S, mine=THEIRS + 50 * S)),
    (ANSWERED, "an_exact_tie_at_the_millisecond",
     _rec(their=THEIRS + 17 * S, mine=THEIRS + 17 * S)),
    (ANSWERED, "no_ms_fields_at_all_falls_back_to_minute_ages",
     _rec(their=THEIRS, mine=NOW - timedelta(hours=11), with_ms=False)),
    (ANSWERED, "only_THEIR_ms_present_must_not_mix_resolutions",
     _rec(their=THEIRS + 30 * S, mine=THEIRS + 10 * S, mine_ms=False)),
    (ANSWERED, "only_MY_ms_present_must_not_mix_resolutions",
     _rec(their=THEIRS + 30 * S, mine=THEIRS + 10 * S, their_ms=False)),
    (ANSWERED, "a_status_outside_the_vocabulary_still_gets_the_note",
     _rec(their=THEIRS, mine=NOW - timedelta(hours=11), status="blocked")),
    (ANSWERED, "an_unreadable_display_date_decided_on_the_raw_ms",
     _rec(their=THEIRS + 10 * S, mine=THEIRS + 50 * S, date_str="2026-13-45 99:99")),

    # --- C: the check could not run. Announce, never decide.
    (ANNOUNCE, "my_latest_reply_field_absent_stale_producer",
     _rec(their=THEIRS, reply_reported=False)),
    (ANNOUNCE, "an_unreadable_date_on_MY_OWN_newest_comment",
     _rec_via_producer(my_date_raw="corrupted")),
    (UNREADABLE, "their_comment_date_unreadable_beside_a_reported_reply",
     _rec(their=THEIRS, mine=NOW - D, with_ms=False, date_str="not-a-date")),
    # 🔴 The ms decides "answered" but NO bound can be evaluated from either field, so the
    # note would be permanent. It falls through to the flag instead: a call to action survives
    # an unreadable date, a "no action needed" line does not.
    (UNREADABLE, "a_raw_ms_unusable_as_an_instant_falls_through_to_the_flag",
     _with(_rec(their=THEIRS + 10 * S, mine=THEIRS + 50 * S),
           date="-99999999999999999", date_ms=-99999999999999999)),

    # --- D: nothing to say. Every one of these is a bound, and a bound that stops bounding
    # is how a block fills with lines nobody must act on.
    (SILENT, "answered_but_the_comment_is_30_days_old",
     _rec(their=NOW - 30 * D, mine=NOW - 29 * D)),
    (SILENT, "unanswered_but_the_comment_is_90_days_old",
     _rec(their=NOW - 90 * D, mine=None)),
    (SILENT, "a_done_ticket_is_not_waiting_on_anyone",
     _rec(their=THEIRS, mine=None, status="closed")),
    (SILENT, "transcripts_mention_the_task_so_work_exists",
     _rec(their=THEIRS, mine=None, mentions=4)),
    (SILENT, "mentions_found_absent_is_not_a_reported_zero",
     _rec(their=THEIRS, mine=None, mentions=None)),
    # 🔴 The bound must not go dead because the DISPLAY date is unreadable. `format_date`
    # falls back to `str(ts_ms)` for an out-of-range epoch, which `_epoch_ms` accepts because
    # it never range-checks, so a record can carry a good `date_ms` beside an unformattable
    # `date`: age unknown, bound skipped, and the ms path still deciding. Measured while
    # porting as a 2019 ticket printing an unbounded note. The exotic input matters less than
    # the systemic one — if `format_date` ever drifts, the bound dies for EVERY record.
    (SILENT, "an_unformattable_display_date_over_a_90_day_old_raw_ms",
     _rec(their=NOW - 90 * D, mine=NOW - 89 * D, date_str="99999999999999999")),
]


def _blocks(r):
    """(flag lines, suppression notes) for one record.

    `getattr` on the notes producer so this corpus RUNS against the pre-port tree, where it
    does not exist — the red-at-base matrix is not optional here and a corpus that cannot be
    pointed at base can only ever describe the code it shipped with. A missing producer scores
    every ANSWERED case as a failure, which is the correct verdict, not a skip.
    """
    flags = [f for f in check_addressed.disagreements([r], now=NOW) if "WAITING" in f]
    notes_fn = getattr(check_addressed, "suppressed_notes", None)
    notes = notes_fn([r], now=NOW) if notes_fn else []
    return flags, notes


def _answered_block(r):
    """The printed lines of the answered-already block, or [] where it does not exist.

    The block is where the bot-identity caveat lives — once, leading it — so a guard that only
    ever reads `suppressed_notes` cannot see whether the reader is given it at all. Same
    `getattr` base-compat as `_blocks`, for the same reason.
    """
    fn = getattr(check_addressed, "report_blocks", None)
    if not fn:
        return []
    return dict(fn([r], now=NOW)).get(check_addressed.ANSWERED_HEADING, [])


def _verdict(r):
    """The CLAIM the report makes about this record, as one of `LABELS`.

    Precedence among the fired-flag labels is the strongest CAVEAT first, because the caveat
    is what changes what the reader does: "the check did not run" outranks "the date was
    unreadable" outranks the plain claim. They are near-disjoint by construction — the plain
    claim is only appended when neither caveat applies — so precedence is documented rather
    than relied upon.
    """
    flags, notes = _blocks(r)
    if flags:
        joined = " ".join(flags).lower()
        if "not reported" in joined:
            return ANNOUNCE
        if "unreadable" in joined:
            return UNREADABLE
        if "nobody has answered" in joined:
            return WAITING
        return "?"
    if notes:
        return ANSWERED
    return SILENT


def test_the_waiting_corpus_score_has_not_regressed():
    """The headline guard. Scores the real report over every labelled record."""
    got = [(name, want, _verdict(r)) for want, name, r in CORPUS]
    good = [n for n, want, g in got if g == want]
    assert len(good) >= MIN_SCORE, (
        f"waiting-flag corpus score fell to {len(good)}/{len(CORPUS)}, below the pinned "
        f"{MIN_SCORE}. Failing: {[(n, w, g) for n, w, g in got if g != w]}")


def test_no_case_that_needs_a_human_comes_out_SILENT():
    """🔴 THE DIRECTION THAT COSTS SOMETHING. A waiting colleague dropped without a trace.

    Silence is this flag's worst outcome and its most invisible: nothing about a ticket that
    goes silent here appears anywhere in the report. (NOT because "`mentions_found == 0` means
    no other rule covers the ticket" — that non-sequitur was retracted; the other rules never
    read the mention count. It is because these fixtures carry nothing for those rules to fire
    on, which is the ordinary shape and is exactly why this flag was added.) It is also the
    direction change 3 exists for — suppression used to print nothing, so an agent-posted
    reply (which ClickUp reports as mine, indistinguishably) removed the ticket from the
    report with no line to say so.
    """
    for want, name, r in CORPUS:
        if want in (WAITING, ANNOUNCE, UNREADABLE, ANSWERED):
            assert _verdict(r) != SILENT, \
                f"{name}: a task needing a human's eyes produced no output at all"


def test_no_UNANSWERED_case_is_reported_as_ANSWERED():
    """🔴 The mirror direction: claiming a question was answered when it was not.

    This is D12 inverted — instead of nagging about an answered ticket, the report would
    quietly file an unanswered one under "no action". `>=` on the raw ms is the boundary that
    decides it, so the sub-minute cases are load-bearing here, not decoration.
    """
    for want, name, r in CORPUS:
        if want in (WAITING, ANNOUNCE, UNREADABLE):
            assert _verdict(r) != ANSWERED, \
                f"{name}: an unanswered (or unknowable) question was reported as answered"


def test_every_ANSWERED_case_reaches_the_reader_under_the_bot_identity_caveat():
    """A suppression note without the caveat is a note that says the wrong thing confidently.

    ClickUp has NO bot identity: a comment posted through the `pk_` token is authored as the
    token's owner whoever typed it, so "you answered" and "an agent answered as you" are the
    same observable and the note cannot claim the first. Asserting the caveat REACHES THE
    READER, not that any particular string is in any particular line — the same lesson that
    produced this file's verdict-on-the-claim rule. It leads the BLOCK once (repeating it per
    line put ~11 KB of identical text in a `--limit 20` report); each line keeps its own
    consequence, so a reader who skips the preamble still sees why this is not "handled".
    """
    for want, name, r in CORPUS:
        if want != ANSWERED:
            continue
        _flags, notes = _blocks(r)
        assert len(notes) == 1, f"{name}: expected exactly one note, got {notes}"
        assert "if that reply was an agent's" in notes[0].lower(), \
            f"{name}: the note asserts you answered without the consequence of the caveat"
        lines = _answered_block(r)
        carriers = [l for l in lines if "bot identity" in l.lower()]
        assert len(carriers) == 1 and lines[0] is carriers[0], \
            f"{name}: the caveat must lead the block exactly once, got {len(carriers)}x"


def test_a_note_never_claims_the_report_is_silent_about_a_ticket_it_also_flags():
    """🔴 A CLAIM THE CODE CONTRADICTED. The note used to assert that nothing else in the
    report named the ticket, and *why*: `mentions_found == 0`. Both halves were wrong.

    Four shapes put a "Needs a decision" line about the SAME ticket directly above the note —
    an unknown ClickUp status, a RESOLVED-reading comment, a keep-open veto, an open cited PR
    — and NOT ONE of those four rules reads `mentions_found`, so the stated reason was a
    non-sequitur rather than an unlucky case. The corpus already contained one such shape
    (`a_status_outside_the_vocabulary_still_gets_the_note`) and scored it green, because the
    label reads the VERDICT and this was a defect in the line's prose.

    So the claim is COMPUTED now, and this is the guard that can see it: for every case, what
    the note says about other coverage must match what `disagreements` actually produced.
    """
    for want, name, r in CORPUS:
        if want != ANSWERED:
            continue
        flags = check_addressed.disagreements([r], now=NOW)
        note = _blocks(r)[1][0].lower()
        if flags:
            assert "also carries a line about this ticket" in note, (
                f"{name}: the note claims the report is silent about a ticket that has "
                f"{len(flags)} decision line(s) directly above it: {flags}")
            assert "no other flag names this ticket" not in note, f"{name}: contradictory note"
        else:
            assert "no other flag names this ticket" in note, \
                f"{name}: the note did not state that nothing else covers the ticket"


def test_every_SILENT_case_is_silent_in_BOTH_blocks():
    """A positive control on what SILENT means. A label that only checked the flag would pass
    while the note block filled up with the very lines the recency bound exists to stop."""
    for want, name, r in CORPUS:
        if want != SILENT:
            continue
        flags, notes = _blocks(r)
        assert not flags and not notes, f"{name}: expected nothing, got {flags} {notes}"


def test_the_case_ledger_is_pinned_so_the_set_cannot_shrink():
    """A LEDGER, the same instrument `test_corpus.py` uses. A corpus that can quietly lose a
    case is a scoreboard that can be won by deleting the cases you fail — and every guard
    above is only as wide as the set it iterates."""
    names = [name for _w, name, _r in CORPUS]
    assert len(names) == len(set(names)), f"duplicate case names: {names}"
    assert len(CORPUS) == 21, (
        f"corpus size changed to {len(CORPUS)}; update MIN_SCORE and this number together")
    assert {w for w, _n, _r in CORPUS} == set(LABELS), (
        "a label has no case, so every directional guard over it is vacuous — the corpus "
        "must exercise all five outcomes")


def test_the_verdict_reader_can_actually_tell_the_labels_apart():
    """🔴 POSITIVE CONTROL ON THE INSTRUMENT. `_verdict` reads substrings out of prose; a
    reword upstream could collapse two labels into one and every guard above would still be
    green, because agreement between a broken reader and a broken report is invisible.

    So: the labels the corpus actually produces must be five distinct values, and no case may
    score `?` — the reader's own "I did not recognise this line" outcome.
    """
    produced = {_verdict(r) for _w, _n, r in CORPUS}
    assert "?" not in produced, "a fired flag made a claim `_verdict` does not recognise"
    assert produced == set(LABELS), (
        f"the corpus produces only {sorted(produced)} — a reader that cannot distinguish the "
        "outcomes scores agreement with itself")
