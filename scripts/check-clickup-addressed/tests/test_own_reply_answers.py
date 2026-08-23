#!/usr/bin/env python3
"""Round 6 (2026-08-22). D12 — the WAITING flag cannot see the user's OWN replies.

MEASURED LIVE, and it cost real work. On 868gz0hhh the report said:

    @Robin Example is WAITING — the ticket is `to do`, and the task ID appears in NO
    transcript, so no work exists anywhere. Commented 2d ago; nobody has answered.

Two sessions had already answered them — 2026-08-21 13:52 and 2026-08-22 01:04, the
second eleven hours before the run that printed that line. Acting on the flag
duplicated an analysis that was already in the thread.

The mechanism is a SEAM, not a logic bug, and each side is correct alone.
`recent-comments.py` drops every comment authored by `me` — right, because the report
is about what OTHER people said. `_waiting_on_a_human` then reads the newest surviving
comment and concludes nobody answered — right, given the only evidence it is handed.
Neither component can observe the other's assumption. `_waiting_on_a_human`'s own
docstring states the filter as a GUARANTEE it relies on:

    The comment is guaranteed not to be the user's own: `recent-comments.py` drops
    every comment whose author id equals `me` before any of this runs.

True, and precisely why the flag is blind: the evidence that would refute it is
removed upstream, so no amount of care inside the function can recover it. The fix has
to cross the seam — `recent-comments.py` must report the date of the user's newest
reply per task, and the flag must branch on it.

FAILURE DIRECTION. The old bug is a FALSE POSITIVE on the one block the report tells
you to act on, and this function's own docstring argues that a permanently-noisy block
is worse than no block because it trains the reader to skip it. An answered ticket
never stops being flagged, so the noise is unbounded — exactly the property the
recency bound was chosen to avoid.

ABSENT IS NOT "NO REPLY". If `my_latest_reply` is missing entirely the producer never
reported one, which is the pre-fix state. Treating that as "nobody replied" would keep
the bug alive for any stale producer; treating it as "someone replied" would silently
disable the flag if the field were ever dropped from `build_record` — this skill's own
headline defect class. It announces instead, the same way an unreadable date does.

Tests marked INVARIANT GUARD pass at base too — they are anti-widening controls, not
regression coverage. A suppression that fires on ANY reply, regardless of when, would
turn D12 into the mirror-image bug and must be killed by a test that is green at base.
"""
import importlib.util, io, json, sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(SCRIPT_DIR))

spec = importlib.util.spec_from_file_location("check_addressed", SCRIPT_DIR / "check-addressed.py")
check_addressed = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check_addressed)

spec2 = importlib.util.spec_from_file_location("recent_comments", SCRIPT_DIR / "recent-comments.py")
recent_comments = importlib.util.module_from_spec(spec2)
spec2.loader.exec_module(recent_comments)

NOW = datetime(2026, 8, 22, 12, 0, tzinfo=timezone.utc)


def _at(days_ago):
    return (NOW - timedelta(days=days_ago)).strftime("%Y-%m-%d %H:%M")


def _rec(comment_days_ago=2, reply_days_ago=None, reply_reported=True):
    """The live 868gz0hhh shape: open ticket, zero transcript evidence, colleague comment.

    `reply_reported=False` omits the key entirely — the stale-producer case, which is a
    different fact from "reported, and there is no reply" (`reply_days_ago=None`).
    """
    nc = {
        "date": _at(comment_days_ago),
        "author": "Robin Example",
        "snippet": "Follow-up from the reporter, asking for a status update",
        "text": "Follow-up from the reporter, asking for a status update",
    }
    if reply_reported:
        nc["my_latest_reply"] = _at(reply_days_ago) if reply_days_ago is not None else None
    return {
        "task_id": "868gz0hhh", "status": "no_mentions_found", "sessions_searched": 1,
        "mentions_found": 0, "clickup_status": "to do", "clickup_priority": "high",
        "newest_comment": nc, "completion": [], "open": [],
    }


def _waiting_flags(r):
    return [f for f in check_addressed.disagreements([r], now=NOW) if "WAITING" in f]


def _collect_with(fake_comments, my_id, capture_stderr=None):
    """Run the producer end to end over a stubbed ClickUp, returning the JSON rows.

    A helper rather than a third copy of the same stubbing dance: three tests now drive
    `_collect`, and a copied fixture is how two of them end up exercising different code
    while reading identically. `my_id=None` is the failed-user-lookup case.
    """
    orig = (recent_comments.get_my_user_id, recent_comments.get_my_tasks,
            recent_comments.get_comments)
    try:
        recent_comments.get_my_user_id = lambda: my_id
        recent_comments.get_my_tasks = lambda: [{"id": "868gz0hhh", "name": "t"}]
        recent_comments.get_comments = fake_comments
        buf, err = io.StringIO(), io.StringIO()
        real_out, real_err = sys.stdout, sys.stderr
        sys.stdout, sys.stderr = buf, err
        try:
            recent_comments._collect(10, False, True)
        finally:
            sys.stdout, sys.stderr = real_out, real_err
    finally:
        (recent_comments.get_my_user_id, recent_comments.get_my_tasks,
         recent_comments.get_comments) = orig
    if capture_stderr is not None:
        capture_stderr.append(err.getvalue())
    return json.loads(buf.getvalue())


# --------------------------------------------------------------------------- D12

def test_my_own_later_reply_suppresses_the_waiting_flag():
    """THE REGRESSION. Red at base: base emits WAITING over a ticket already answered.

    The exact live shape — the reporter commented 2 days ago, I replied 11 hours ago. Nobody is
    waiting on a first response, so the report must not send the reader to write one.
    """
    flags = _waiting_flags(_rec(comment_days_ago=2, reply_days_ago=0.46))
    assert flags == [], (
        "a ticket answered AFTER the colleague's comment was still reported as unanswered "
        f"— the live 868gz0hhh false positive: {flags}")


def test_a_reply_older_than_the_question_does_not_suppress():
    """INVARIANT GUARD (green at base) — anti-widening control for the fix above.

    Kills the mutant that suppresses on the mere EXISTENCE of a reply. Answering on the
    5th does not answer a question asked on the 21st, and that is the common shape on a
    long-running ticket: an old reply of mine sits under a fresh question.
    """
    flags = _waiting_flags(_rec(comment_days_ago=1, reply_days_ago=5))
    assert flags, (
        "a reply PREDATING the colleague's comment suppressed the flag — existence of a "
        "reply is not an answer to a later question")


def test_reported_absence_of_a_reply_still_flags():
    """INVARIANT GUARD (green at base). `my_latest_reply: None` is a positive statement.

    The producer looked and found no reply from me. That is the state the flag exists for,
    and it must survive the new branch.
    """
    flags = _waiting_flags(_rec(comment_days_ago=2, reply_days_ago=None))
    assert flags, "an explicitly-reported absence of any reply from me suppressed the flag"
    joined = " ".join(flags).lower()
    # 🔴 Assert the RELATIONSHIP the docstring claims, not merely that something fired. A bare
    # `assert flags` let the mutant `reply_unreported = nc.get("my_latest_reply") is None`
    # — flattening absent and null, the single mutation this whole `in`/`not in` design exists
    # to prevent — SURVIVE a fully green suite. Under it every genuinely-unanswered ticket
    # loses "nobody has answered" and gains the FALSE line "your own replies were not
    # reported". The producer side of the distinction was pinned; this consumer side was not.
    assert "nobody has answered" in joined, (
        "a reported null was not treated as evidence — the flag must state nobody answered, "
        f"because the producer did look: {flags}")
    assert "not reported" not in joined, (
        f"a reported null was misreported as an ungathered fact: {flags}")


def test_an_unreadable_comment_date_beside_a_reported_reply_still_flags():
    """Covers `theirs is not None` at check-addressed.py — an untested guard at audit time.

    The docstring there claims a malformed value on EITHER side falls through to the flag.
    Only the `mine` side was exercised: no fixture combined a REPORTED `my_latest_reply` with
    an UNREADABLE comment date, so deleting `theirs is not None` survived the whole suite and
    would raise `TypeError: '<=' not supported between 'float' and 'NoneType'` on a real
    record. A description claiming coverage the tests do not provide reads as coverage while
    providing none.
    """
    r = _rec(comment_days_ago=2, reply_days_ago=1)
    r["newest_comment"]["date"] = "not-a-date"
    flags = _waiting_flags(r)
    assert flags, "an unreadable comment date silently dropped a possibly-waiting human"
    assert "unreadable" in " ".join(flags).lower(), \
        f"the flag did not admit the comment date was unreadable: {flags}"


def test_an_unreported_reply_field_announces_instead_of_deciding():
    """A stale producer must not silently decide the question either way.

    Absent means `recent-comments.py` never reported a reply date — the pre-fix state. The
    flag still fires (a missed waiting human costs more than one noisy line, matching the
    unreadable-date branch) but must SAY the check did not run, so a reader is not told
    'nobody has answered' on evidence that was never gathered.
    """
    flags = _waiting_flags(_rec(comment_days_ago=2, reply_reported=False))
    assert flags, "an unreported reply field silently disabled the flag"
    joined = " ".join(flags).lower()
    assert "own repl" in joined or "not reported" in joined or "could not" in joined, (
        "the flag fired without disclosing that the own-reply check never ran, so an "
        f"ungathered fact reads as a gathered one: {flags}")
    assert "nobody has answered" not in joined, (
        "the flag asserted 'nobody has answered' while the check that would establish it "
        f"did not run: {flags}")


def test_a_same_minute_reply_counts_as_answered():
    """Boundary: equal timestamps. ClickUp formats to the MINUTE, so a reply written in
    the same minute as the comment renders as equal, not later. A strict `>` would flag
    it as unanswered. Measured at both ends rather than at one — the `>` vs `>=` choice
    is exactly the mutation this pins.
    """
    assert _waiting_flags(_rec(comment_days_ago=2, reply_days_ago=2)) == [], \
        "a reply in the same minute as the comment was treated as not-an-answer"


# ------------------------------------------------- the seam, from the producer side

def test_latest_reply_by_picks_my_newest_comment_by_timestamp():
    """Ranked on the raw epoch-ms, not on the formatted string.

    `format_date` falls back to `str(ts_ms)` for an unparseable value, so a max() over
    formatted strings can rank garbage above a real date. The defining value is the
    integer; formatting happens to the winner only.
    """
    comments = [
        {"user": {"id": "7"}, "date": "1755000000000"},   # mine, older
        {"user": {"id": "9"}, "date": "1755900000000"},   # someone else's, newest overall
        {"user": {"id": "7"}, "date": "1755800000000"},   # mine, newest of mine
    ]
    got = recent_comments.latest_reply_by(comments, "7")
    assert got == recent_comments.format_date("1755800000000"), \
        f"did not select my newest comment: {got}"


def test_latest_reply_by_returns_none_when_i_never_commented():
    comments = [{"user": {"id": "9"}, "date": "1755900000000"}]
    assert recent_comments.latest_reply_by(comments, "7") is None, \
        "reported a reply from me on a thread I never commented on"


def test_an_unreadable_own_date_yields_UNIDENTIFIED_not_an_older_reply():
    """🔴 ROUND 7. Red at base — base returns the OLDER reply and the flag says "answered".

    This test replaces `test_latest_reply_by_ignores_an_unparseable_date`, which pinned the
    `continue` that IS the defect. Its two claims survive: a malformed date must not win the
    ranking, and it must not crash the fetch. What changed is the third, unstated one — that
    skipping it is safe. It is not: if the unreadable date belongs to my NEWEST comment, the
    loop returns an OLDER reply of mine (or `None` when it is my only one), and `None` is the
    POSITIVE claim "I looked; you never replied". Round 6's own comment on `UNIDENTIFIED`
    names that exact property as the one this seam exists to protect; an unreadable date
    defeats it on a different input.

    The sentinel is right and "skip it" is wrong because an unreadable date has NO POSITION in
    the ranking — there is no evidence it is not the newest — so "which of my comments is
    newest" is genuinely unknowable, not merely unknown for one row.
    """
    comments = [
        {"user": {"id": "7"}, "date": "not-a-timestamp"},
        {"user": {"id": "7"}, "date": "1755800000000"},
    ]
    got = recent_comments.latest_reply_by(comments, "7")
    assert got is recent_comments.UNIDENTIFIED, (
        "an unreadable date on one of MY comments was skipped and an older reply reported as "
        f"my newest: {got!r}")
    # The two claims the replaced test made, kept: garbage never wins, and no exception.
    assert got != recent_comments.format_date("not-a-timestamp")


def test_an_unreadable_own_date_reaches_the_ANNOUNCE_branch_end_to_end():
    """The seam, not the part. The sentinel is worthless unless it survives to the consumer.

    `build_record` omits the key for UNIDENTIFIED and `_waiting_on_a_human` then announces
    rather than deciding — the same path a failed user-id lookup takes. Pinned end to end
    because every link is individually correct at base and the fix is still inert if
    `_collect` formats the sentinel into a string on the way past (`format_date` returns
    `str(ts_ms)` for anything it cannot parse, which is truthy and looks like a date).
    """
    def fake_comments(_tid):
        return [
            {"user": {"id": "9", "username": "Robin Example"},
             "date": "1755800000000", "comment": [{"text": "a question"}]},
            {"user": {"id": "7", "username": "me"},
             "date": "corrupted", "comment": [{"text": "my answer"}]},
        ]

    rows = _collect_with(fake_comments, my_id="7")
    assert rows, "no rows emitted"
    assert "my_latest_reply" not in rows[0], (
        "an unrankable set of my own comments was reported as a definite reply date or as a "
        f"definite absence of one: {rows[0]}")
    assert "my_latest_reply_ms" not in rows[0], \
        f"a raw ms was emitted without the formatted date it refines: {rows[0]}"

    r = _rec()
    r["newest_comment"] = dict(check_addressed.build_newest_comment(rows[0]),
                               date=_at(2), author="Robin Example")
    flags = _waiting_flags(r)
    assert flags and "not reported" in " ".join(flags).lower(), (
        f"the absent key did not reach the announce branch end-to-end: {flags}")
    assert "nobody has answered" not in " ".join(flags).lower(), (
        "claimed nobody answered while the evidence that would refute it was unreadable: "
        f"{flags}")


def test_build_record_carries_my_latest_reply():
    """Pins the JOIN. Both ends of this seam are testable in isolation and the fix is
    inert unless the field actually reaches the record — the same way dropping `text`
    from this dict silently reverted the keep-open veto.
    """
    r = recent_comments.build_record(
        "868gz0hhh", "name", {}, {"user": {"username": "Robin Example"}, "date": "1755900000000"},
        "some text", "2026-08-22 01:04", 1755819840000)
    assert r.get("my_latest_reply") == "2026-08-22 01:04", \
        f"build_record dropped my_latest_reply, so the fix cannot reach the flag: {r}"
    # ROUND 7: the raw ms beside it, for BOTH sides. Dropping either one is invisible in the
    # report — the comparison silently falls back to minute resolution — so both are pinned
    # here for the same reason `text` and `my_latest_reply` are.
    assert r.get("my_latest_reply_ms") == 1755819840000, \
        f"build_record dropped my reply's raw ms, so the sub-minute fix is inert: {r}"
    assert r.get("date_ms") == 1755900000000, (
        "build_record dropped the comment's own raw ms — comparing one raw instant against a "
        f"minute-rounded one is worse than comparing two rounded ones: {r}")


def test_build_record_EMITS_the_key_for_a_genuine_absence_of_replies():
    """The other direction of the conditional, which the audit-round fix left unpinned.

    `build_record` omits the key only for the UNIDENTIFIED sentinel. A genuine `None` — "I
    looked; you never replied" — must still be EMITTED, because present-and-null is the
    evidence the flag reads to say "nobody has answered". Before the sentinel the key was
    emitted unconditionally, so this direction was structurally impossible and nothing
    tested it; introducing the branch opened it. The mutant `UNIDENTIFIED = None` collapses
    the two, degrading every genuinely-unanswered ticket to the false caveat "your own
    replies were not reported", and it SURVIVED a green 168-test suite.

    Note the trap this is written around: the neighbouring mutants `if my_latest_reply:` and
    `if my_latest_reply is not None:` do die — but on `Object of type object is not JSON
    serializable`, because `object()` is both truthy and non-None. They die for the WRONG
    reason and prove nothing about the null direction. Only this assertion isolates it.
    """
    r = recent_comments.build_record(
        "868gz0hhh", "name", {}, {"user": {"username": "Robin Example"}, "date": "1755900000000"},
        "some text", None, None)
    assert "my_latest_reply" in r, (
        "a genuine 'no reply from me' lost the key, so the consumer will announce that the "
        f"check never ran instead of reporting that nobody answered: {r}")
    assert r["my_latest_reply"] is None, f"expected a reported null, got {r['my_latest_reply']!r}"
    # ROUND 7. The ms is PRECISION, not evidence, and the two must not be confused: there is
    # no reply, so there is no instant to refine, and the key is absent rather than null.
    # `my_latest_reply` carries the absent-vs-null distinction ALONE — duplicating it into a
    # second field is two chances for the record to contradict itself.
    assert "my_latest_reply_ms" not in r, (
        "a raw ms was invented for a reply that does not exist, giving the record two fields "
        f"that can disagree about whether I replied: {r}")


def test_collect_computes_the_reply_over_the_UNFILTERED_comment_list():
    """The wiring, not the parts. Added because a mutation sweep found nothing covered it.

    `latest_reply_by` and `build_record` are both correct in isolation and both pinned
    above, yet `_collect` could call the first with the wrong list — after the loop that
    discards my comments, say — and the entire fix would be inert with a fully green
    suite. Mutating the call to `latest_reply_by([], my_id)` SURVIVED every other test
    here. That is this skill's own headline defect class: a seam no single-component test
    owns.
    """
    def fake_comments(_tid):
        return [
            {"user": {"id": "9", "username": "Robin Example"},
             "date": "1755800000000", "comment": [{"text": "a question"}]},
            {"user": {"id": "7", "username": "me"},
             "date": "1755900000000", "comment": [{"text": "my answer"}]},
        ]

    calls = _collect_with(fake_comments, my_id="7")

    assert len(calls) == 1, f"expected only the colleague's comment to survive: {calls}"
    assert calls[0]["author"] == "Robin Example"
    assert calls[0]["my_latest_reply"] == recent_comments.format_date("1755900000000"), (
        "_collect did not report my reply — the reply must be computed over the FULL "
        f"comment list, before mine are discarded: {calls[0]}")
    # ROUND 7: the same join for the raw ms, at the same seam. `_collect` is the only code
    # that pairs the formatted date with its own ms; taking them from two lookups would let
    # them describe two different comments, and nothing in the report would look wrong.
    assert calls[0]["my_latest_reply_ms"] == 1755900000000, (
        f"_collect reported my reply's date without the ms it was formatted from: {calls[0]}")
    assert calls[0]["date_ms"] == 1755800000000, (
        f"_collect reported the colleague's date without its own raw ms: {calls[0]}")


def test_a_failed_user_lookup_omits_the_key_instead_of_claiming_no_reply():
    """`get_my_user_id()` → None must reach the ANNOUNCE branch, never the evidence branch.

    It returns None whenever the ClickUp CLI prints no `ID:` line — auth expiry, a network
    error, an output-format change — and `run_clickup` does not check the subprocess return
    code, so the failure is silent. Before the sentinel, no comment matched the id, so
    `latest_reply_by` returned None and the record carried `my_latest_reply: null`, which the
    consumer reads as the POSITIVE claim "I looked; you never replied". The one design
    property this seam exists to protect, defeated on the exact failure mode that produces it.
    """
    def fake_comments(_tid):
        return [{"user": {"id": "9", "username": "Robin Example"},
                 "date": "1755800000000", "comment": [{"text": "a question"}]}]

    errs = []
    rows = _collect_with(fake_comments, my_id=None, capture_stderr=errs)   # the failure under test

    assert rows, "no rows emitted"
    assert "my_latest_reply" not in rows[0], (
        "a FAILED user lookup was reported as 'I looked; you never replied' — the key must be "
        f"absent so the consumer announces rather than decides: {rows[0]}")
    assert "WARNING" in errs[0], \
        f"an unidentifiable user produced no warning on stderr: {errs[0]!r}"

    # And the consumer must actually take the announce branch on that record.
    nc = check_addressed.build_newest_comment(rows[0])
    r = _rec()
    r["newest_comment"] = dict(nc, date=_at(2), author="Robin Example")
    flags = _waiting_flags(r)
    assert flags and "not reported" in " ".join(flags).lower(), (
        f"the absent key did not reach the announce branch end-to-end: {flags}")


def test_run_script_forwards_child_stderr_to_the_operator():
    """A warning the entry point swallows is the same no-op as one that never fires.

    `run_script` uses `capture_output=True` and returned only stdout, so
    `recent-comments.py`'s "could not resolve your ClickUp user id" warning reached the
    operator ZERO times through the command everyone actually runs — measured by the audit.
    Real subprocess, not a stub: the swallowing happened in the plumbing, so a test that
    stubs the plumbing would pass over the bug.
    """
    import tempfile, subprocess as sp
    with tempfile.TemporaryDirectory() as td:
        child = Path(td) / "noisy.py"
        child.write_text(
            "import sys\n"
            "print('REAL-STDOUT')\n"
            "print('CHILD-WARNING-MARKER', file=sys.stderr)\n")
        orig_dir = check_addressed.SCRIPT_DIR
        try:
            check_addressed.SCRIPT_DIR = Path(td)
            err = io.StringIO()
            real_err, sys.stderr = sys.stderr, err
            try:
                out, rc = check_addressed.run_script("noisy.py")
            finally:
                sys.stderr = real_err
        finally:
            check_addressed.SCRIPT_DIR = orig_dir

    assert rc == 0 and "REAL-STDOUT" in out, f"stdout was disturbed: {out!r} rc={rc}"
    assert "CHILD-WARNING-MARKER" in err.getvalue(), (
        "child stderr was swallowed — a diagnostic the child emits cannot reach the person "
        f"reading the report: {err.getvalue()!r}")
    # ⚠️ BY-CONSTRUCTION INVARIANT, not coverage — labelled so nobody counts it twice.
    # `run_script` returns `result.stdout`, so the child's stderr cannot appear in `out`
    # whatever the forwarding does. Mutating the forward to write to the parent's STDOUT is
    # caught by the assertion above (stderr goes empty), never by this one. Kept as a cheap
    # tripwire for a future rewrite that merges the streams (`stderr=subprocess.STDOUT`).
    assert "CHILD-WARNING-MARKER" not in out, \
        "child stderr leaked into the stdout callers parse as data"


def test_build_newest_comment_preserves_absent_vs_null():
    """Absent and null are different facts and both must survive the hand-off.

    JSON round-trips both, so the distinction is only lost if this function flattens it
    with a `.get()`.
    """
    present = check_addressed.build_newest_comment(
        {"date": "d", "author": "a", "snippet": "s", "my_latest_reply": None})
    assert "my_latest_reply" in present, \
        "a reported null reply was flattened to absent, losing 'the producer did look'"
    absent = check_addressed.build_newest_comment({"date": "d", "author": "a", "snippet": "s"})
    assert "my_latest_reply" not in absent, \
        "an unreported reply was invented as null, claiming the producer looked when it did not"


# ======================================================================= ROUND 7
# Three follow-ups to round 6, each building on its design rather than replacing it:
#   1. an unreadable date on one of MY comments restored the false positive (above).
#   2. the tie boundary was a judgement call; the producer HAS the raw ms and threw it away.
#   3. suppression printed NOTHING, so the bot-identity caveat had nowhere to live.


def _ms_at(seconds_after_epoch_ref):
    """Epoch-ms for a fixed instant, so the sub-minute cases are exact, not clock-derived."""
    return 1755819600000 + int(seconds_after_epoch_ref * 1000)


def _sub_minute_rec(their_offset_s, my_offset_s):
    """Two comments inside the SAME rendered minute, `their_offset_s` seconds apart.

    Both `date` and `my_latest_reply` render to the identical minute string, so the round-6
    comparison sees a tie whatever the real order was. The ms fields carry the real order.
    """
    base = _ms_at(0)
    their_ms, my_ms = base + int(their_offset_s * 1000), base + int(my_offset_s * 1000)
    rendered = recent_comments.format_date(base)
    r = _rec(comment_days_ago=2)
    r["newest_comment"].update({
        "date": rendered, "my_latest_reply": rendered,
        "date_ms": their_ms, "my_latest_reply_ms": my_ms,
    })
    # The recency bound reads the RENDERED date, so pin `now` relative to it rather than to
    # NOW — otherwise the fixture ages out and every case below goes silent for the wrong
    # reason. (A silent-for-the-wrong-reason fixture is the vacuous-green shape this file
    # exists to police.)
    return r


def _flags_at(r, now):
    return [f for f in check_addressed.disagreements([r], now=now) if "WAITING" in f]


def _now_for(r):
    return datetime.strptime(r["newest_comment"]["date"], "%Y-%m-%d %H:%M").replace(
        tzinfo=timezone.utc) + timedelta(days=2)


def test_a_reply_twenty_seconds_BEFORE_the_question_is_no_longer_read_as_an_answer():
    """🔴 ROUND 7 REGRESSION. Red at base: base renders both to the same minute and suppresses.

    This is the failure direction that costs something — a colleague's question dropped from
    the report entirely because a reply I wrote 20 seconds EARLIER rounded onto it. Round 6
    chose "equal counts as answered" deliberately and correctly for minute-resolution data;
    the point here is that the data need not be minute-resolution. `recent-comments.py` has
    the raw epoch-ms and `format_date` throws the seconds away before anything compares them.
    """
    r = _sub_minute_rec(their_offset_s=30, my_offset_s=10)
    flags = _flags_at(r, _now_for(r))
    assert flags, (
        "a reply written 20 seconds BEFORE the question was counted as answering it — the "
        "minute rendering made two different instants compare equal")


def test_a_reply_forty_seconds_AFTER_the_question_still_suppresses():
    """INVARIANT GUARD (green at base, for the wrong reason there).

    The mirror of the case above, and the anti-widening control for it: at base this passes
    because BOTH sides round to the same minute and a tie counts as answered — the right
    answer from the wrong evidence. Here it passes on the fact. A mutant that inverts the ms
    comparison flips exactly one of this pair, so the pair is what makes the direction
    observable; either test alone is satisfied by `mine_ms <= theirs_ms`.
    """
    r = _sub_minute_rec(their_offset_s=10, my_offset_s=50)
    assert _flags_at(r, _now_for(r)) == [], (
        "a reply written 40 seconds AFTER the question did not suppress the flag")


def test_a_tie_is_now_a_real_tie_at_the_MILLISECOND():
    """The boundary itself. `>=` is kept, so an exact tie still counts as answered.

    At minute resolution a "tie" was common and the choice was a judgement call argued in
    both directions. At millisecond resolution a tie means two comments in the same
    millisecond, which is a genuine simultaneity and not a rounding artefact — so keeping
    `>=` cannot move any case round 6 measured, only the sub-minute ones.
    """
    r = _sub_minute_rec(their_offset_s=17, my_offset_s=17)
    assert _flags_at(r, _now_for(r)) == [], \
        "a reply in the same MILLISECOND as the comment was treated as not-an-answer"


def test_a_record_with_no_ms_fields_falls_back_to_the_minute_ages_unchanged():
    """INVARIANT GUARD (green at base). The fallback is not a legacy path.

    A record from a producer that predates the ms fields — or one whose dates would not parse
    — must keep round 6's behaviour exactly. Deleting the fallback instead of keeping it would
    resurrect D12's false positive on every such record while every ms-carrying test stayed
    green, which is the shape of a fix that only works on the fixtures it was written with.
    """
    assert _waiting_flags(_rec(comment_days_ago=2, reply_days_ago=0.46)) == [], \
        "a record without the raw-ms fields lost suppression entirely"
    assert _waiting_flags(_rec(comment_days_ago=1, reply_days_ago=5)), \
        "a record without the raw-ms fields lost the older-reply-is-not-an-answer rule"


def test_the_ms_comparison_requires_BOTH_sides_and_falls_back_otherwise():
    """Comparing one raw instant against a minute-rounded one is worse than comparing two
    rounded ones: it silently mixes resolutions and the error is invisible in the output.

    So the raw path is taken only when BOTH sides are present. With one side present the
    record must behave exactly like a record with neither — pinned in the direction that
    matters, a reply 20s BEFORE the question, where the two paths disagree.
    """
    for drop in ("date_ms", "my_latest_reply_ms"):
        r = _sub_minute_rec(their_offset_s=30, my_offset_s=10)
        del r["newest_comment"][drop]
        assert _flags_at(r, _now_for(r)) == [], (
            f"with only {drop} missing the checker still used the raw ms of the other side, "
            "comparing a raw instant against a rounded one")


def test_ms_reads_ints_only_and_refuses_a_bool():
    """Directly at the helper. ⚠️ The bool clause is BY-CONSTRUCTION, not an observed input.

    `isinstance(True, int)` is True in Python, so a JSON `true` would otherwise be compared as
    1 ms — 1970 — making every reply look newer than every question, silently. No producer
    writes a bool here; the guard is kept because its failure is quiet and wrong rather than
    loud, and it is pinned here rather than through a record fixture so nobody counts a
    record-level test as covering it.
    """
    assert check_addressed._ms(1755800000000) == 1755800000000
    assert check_addressed._ms(True) is None, "a JSON `true` was read as 1 millisecond"
    assert check_addressed._ms("1755800000000") is None, (
        "a STRING ms was accepted — the consumer's fields are machine-written ints, and a "
        "string means a drifted producer whose right answer is the age fallback")
    assert check_addressed._ms(None) is None


def test_build_newest_comment_carries_both_raw_ms_fields_and_preserves_absence():
    """The hand-off, both directions. Dropping either key is INVISIBLE in the report — the
    comparison silently falls back to minute resolution and every existing test stays green —
    which is the same way dropping `text` silently reverted the keep-open veto.
    """
    nc = check_addressed.build_newest_comment(
        {"date": "d", "author": "a", "snippet": "s", "my_latest_reply": "m",
         "date_ms": 111, "my_latest_reply_ms": 222})
    assert nc.get("date_ms") == 111 and nc.get("my_latest_reply_ms") == 222, (
        f"a raw ms was dropped at the hand-off, so the sub-minute fix is inert: {nc}")
    bare = check_addressed.build_newest_comment({"date": "d", "author": "a", "snippet": "s"})
    assert "date_ms" not in bare and "my_latest_reply_ms" not in bare, (
        f"an unreported ms was invented, mixing a fabricated instant into the ordering: {bare}")


# ------------------------------------------------------- the suppression note (change 3)

def _notes(r, now=NOW):
    return check_addressed.suppressed_notes([r], now=now)


def test_a_suppressed_flag_emits_exactly_one_line_and_the_block_carries_the_caveat():
    """🔴 ROUND 7 REGRESSION. Red at base: base emits NOTHING when it suppresses.

    ClickUp has no bot identity — measured independently the same day in a sibling tool
    implementing this same predicate. Every comment posted through the `pk_` token comes back
    authored as the token's owner, so an AGENT's reply sets `my_latest_reply` and suppresses
    this flag, and "the owner answered" and "a machine answered as the owner" are the same
    observable. Suppression printed nothing at all, so the ticket left no trace in the report
    and the caveat had nowhere to live: a genuinely-waiting colleague, dropped silently, and
    with the transcripts empty the flag that would have caught it was the one being
    suppressed. (NOT "`mentions_found == 0` means no other rule covers the ticket" — that
    non-sequitur was retracted from the note itself; the status, comment and PR rules never
    read the mention count and can each still flag the ticket.)
    """
    r = _rec(comment_days_ago=2, reply_days_ago=0.46)
    notes = _notes(r)
    assert len(notes) == 1, f"expected exactly one suppression note, got {notes}"
    joined = notes[0].lower()
    assert "868gz0hhh" in notes[0] and "robin example" in joined, \
        f"the note does not identify the ticket and the person waiting: {notes[0]}"
    assert "suppressed" in joined, \
        f"the note does not say a flag was suppressed: {notes[0]}"
    # The per-line CONSEQUENCE of the caveat stays on every line; the caveat itself is hoisted
    # to the block. Asserted separately so hoisting cannot quietly take the consequence with
    # it — a reader who skips the preamble must still see why this line is not "handled".
    assert "if that reply was an agent's" in joined, \
        f"the note dropped the consequence of the bot-identity caveat: {notes[0]}"

    # ...and the CAVEAT is in the block, exactly ONCE. It is 199 chars; repeating it per line
    # put ~11 KB of identical text in a `--limit 20` report, in the one block whose own
    # justification is that volume is how a block stops being read.
    lines = dict(check_addressed.report_blocks([r], now=NOW))[check_addressed.ANSWERED_HEADING]
    carriers = [l for l in lines if "bot identity" in l.lower()]
    assert len(carriers) == 1, \
        f"the caveat appears {len(carriers)}x in the block; it must lead it exactly once: {lines}"
    assert lines[0] is carriers[0], f"the caveat does not LEAD the block: {lines}"


def test_the_suppression_note_never_lands_in_the_needs_a_decision_block():
    """🔴 The whole point of a second block. "Needs a decision" is the one block the reader is
    told to act on; a line saying "no action needed" belongs elsewhere, and the flag's own
    recency bound was chosen on exactly this reasoning — a block that fills with lines nobody
    must act on trains the reader to skip it.
    """
    r = _rec(comment_days_ago=2, reply_days_ago=0.46)
    assert check_addressed.disagreements([r], now=NOW) == [], \
        "the suppression note leaked into the act-on-this block"
    blocks = dict(check_addressed.report_blocks([r], now=NOW))
    assert check_addressed.ANSWERED_HEADING in blocks, \
        f"the note never reached the report — a producer main() does not print is inert: {blocks}"
    assert check_addressed.DECISION_HEADING not in blocks, \
        f"an empty decision block was printed beside it: {blocks}"


def test_a_waiting_ticket_produces_a_flag_and_NO_note():
    """The two verdicts are mutually exclusive, in both directions. A mutant that emits the
    note unconditionally would otherwise pass every assertion above.
    """
    r = _rec(comment_days_ago=2, reply_days_ago=None)
    assert _waiting_flags(r), "the waiting flag stopped firing on an unanswered ticket"
    assert _notes(r) == [], f"an unanswered ticket also produced a suppression note: {_notes(r)}"


def test_the_note_obeys_the_SAME_recency_bound_as_the_flag_it_replaces():
    """🔴 Otherwise the note is the permanent noise the bound exists to prevent — worse than
    the flag, because an answered ticket never changes any input and would print forever.

    Measured at both ends of the bound rather than at one: inside it a note, outside it
    nothing at all (no flag AND no note). `UNANSWERED_COMMENT_DAYS` is 14. The bound's exact
    VALUE and its exclusivity are pinned separately, in `test_bounds_and_parsing.py` — 13 and
    30 cannot see `>=` or a doubling.
    """
    inside = _rec(comment_days_ago=13, reply_days_ago=12)
    assert len(_notes(inside)) == 1, f"a note inside the recency window went missing: {inside}"
    outside = _rec(comment_days_ago=30, reply_days_ago=29)
    assert _notes(outside) == [], \
        "a 30-day-old answered ticket still printed a note — unbounded by construction"
    assert _waiting_flags(outside) == [], "the recency bound stopped bounding the flag itself"


def test_the_note_is_bounded_to_one_line_per_task():
    """Bounded volume is the property, not just bounded age: three suppressed tasks must give
    three lines, not a per-comment firehose. The report already samples the N most recent
    comments, so this composes with that cap rather than multiplying it.
    """
    recs = []
    for tid in ("868gy0ddd", "868gy0eee", "868gy0fff"):
        r = _rec(comment_days_ago=2, reply_days_ago=0.46)
        r["task_id"] = tid
        recs.append(r)
    notes = check_addressed.suppressed_notes(recs, now=NOW)
    assert len(notes) == 3, f"expected one line per suppressed task, got {len(notes)}: {notes}"
    assert len({n.split(":")[0] for n in notes}) == 3, f"lines are not per-task: {notes}"


def test_the_act_on_this_block_is_printed_BEFORE_the_no_action_block():
    """Order is part of the claim, not cosmetics: the first block a reader meets should be
    the one that asks for something. Membership alone is satisfied by printing the "no action
    needed" lines first, which is how a reader learns that the report opens with noise.
    """
    waiting = _rec(comment_days_ago=2, reply_days_ago=None)
    answered = _rec(comment_days_ago=2, reply_days_ago=0.46)
    answered["task_id"] = "868gw0zzz"
    headings = [h for h, _lines in check_addressed.report_blocks([waiting, answered], now=NOW)]
    assert headings == [check_addressed.DECISION_HEADING, check_addressed.ANSWERED_HEADING], \
        f"the report's trailing blocks are in the wrong order: {headings}"


def test_main_actually_PRINTS_both_blocks_end_to_end():
    """🔴 THE SEAM NOBODY OWNED, and it was wider than the extraction admitted.

    Measured on this branch: replacing `main()`'s entire `for heading, lines in
    report_blocks(results)` loop with `pass` left the whole suite green. The tool's whole
    trailing output — both blocks, including the WAITING flag and four rounds of work behind
    it — could be deleted with nothing going red. `report_blocks` was extracted with a
    docstring claiming it existed "so the WIRING is testable"; extracting it moved the seam up
    one level rather than closing it, which is this skill's headline defect class wearing the
    fix for itself as a disguise.

    So: drive `main()` with the ONLY subprocess seam (`run_script`) stubbed and assert on its
    STDOUT. Two tasks, one waiting and one answered, so both blocks must appear.

    ⚠️ Dates are relative to the WALL CLOCK, because `main()` has no `now` seam — that is the
    honest shape of an end-to-end test here, and 2 days ago is inside the 14-day bound on
    every day it can run.

    ⚠️ NOT PORTED from the upstream copy: its second half drives `main()` with the transcript
    scan OFF and asserts both blocks vanish. This tool has no such flag — `main()` always runs
    search-sessions.py and check-completion.py — so that half would assert a state that cannot
    occur here. If a `--transcripts` opt-in ever lands, restore it.
    """
    now = datetime.now(timezone.utc)
    fmt = lambda dt: dt.strftime("%Y-%m-%d %H:%M")
    ms = lambda dt: int(dt.timestamp() * 1000)
    theirs, mine = now - timedelta(days=2), now - timedelta(hours=11)

    comments = [
        {"task_id": "868gz0hhh", "task_name": "unanswered", "task_status": "to do",
         "task_priority": "high", "date": fmt(theirs), "author": "Robin Example",
         "snippet": "a question nobody answered", "text": "a question nobody answered",
         "date_ms": ms(theirs), "my_latest_reply": None},
        {"task_id": "868gw0zzz", "task_name": "answered", "task_status": "to do",
         "task_priority": "high", "date": fmt(theirs), "author": "Robin Example",
         "snippet": "a question I answered", "text": "a question I answered",
         "date_ms": ms(theirs), "my_latest_reply": fmt(mine), "my_latest_reply_ms": ms(mine)},
    ]

    def fake_run_script(name, *args):
        if name == "recent-comments.py":
            return json.dumps(comments), 0
        if name == "search-sessions.py":
            return json.dumps({"sessions": [], "self_runs_skipped": 0,
                               "self_runs_skipped_ids": []}), 0
        if name == "check-completion.py":
            tid = args[args.index("--task") + 1]
            return json.dumps([{"task_id": tid, "status": "no_mentions_found",
                                "sessions_searched": 1, "mentions_found": 0,
                                "completion": [], "open": []}]), 0
        raise AssertionError(f"main() called an unexpected script: {name}")

    def drive(*argv):
        orig_run, orig_argv = check_addressed.run_script, sys.argv
        buf, real_stdout = io.StringIO(), sys.stdout
        try:
            check_addressed.run_script = fake_run_script
            sys.argv = ["check-addressed.py", *argv]
            sys.stdout = buf
            check_addressed.main()
        finally:
            sys.stdout = real_stdout
            check_addressed.run_script, sys.argv = orig_run, orig_argv
        return buf.getvalue()

    out = drive()

    assert check_addressed.DECISION_HEADING in out, \
        f"main() printed no decision block at all:\n{out[-1500:]}"
    assert check_addressed.ANSWERED_HEADING in out, \
        f"main() printed no answered-already block at all:\n{out[-1500:]}"
    assert "868gz0hhh" in out and "is WAITING" in out, \
        "the waiting flag never reached stdout"
    assert "868gw0zzz" in out and "SUPPRESSED" in out, \
        "the suppression note never reached stdout"
    assert "bot identity" in out.lower(), "the caveat never reached stdout"
    # ORDER on the real output, not just in `report_blocks`' return value.
    assert out.index(check_addressed.DECISION_HEADING) < out.index(check_addressed.ANSWERED_HEADING), \
        "the no-action block was printed before the act-on-this block"


def test_report_blocks_passes_its_now_through_to_BOTH_producers():
    """A `now` that does not reach a producer silently becomes the WALL CLOCK.

    🔴 This test is written against the wall clock on purpose, and the first version of it was
    vacuous: it passed `now=2026-08-22`, which is the day the round was written, so dropping
    the argument changed nothing and both `now`-dropping mutants SURVIVED. A seam test whose
    two sides agree by coincidence is not a test — it is the agreement that has to be
    impossible.

    So the records are dated ~100 days in the PAST and `now` is set 2 days after them: honoured,
    the comment is 2 days old and both blocks appear; ignored, it is ~100 days old, past
    `UNANSWERED_COMMENT_DAYS`, and the report goes silent. Past, not future — a future comment
    yields a NEGATIVE age, which does not trip an upper bound, so that direction agrees too.
    """
    stamp = datetime.now(timezone.utc) - timedelta(days=98)
    fmt = lambda dt: dt.strftime("%Y-%m-%d %H:%M")

    def rec(tid, reply):
        nc = {"date": fmt(stamp - timedelta(days=2)), "author": "Robin Example",
              "snippet": "q", "text": "q",
              "my_latest_reply": fmt(stamp - timedelta(days=1)) if reply else None}
        return {"task_id": tid, "status": "no_mentions_found", "sessions_searched": 1,
                "mentions_found": 0, "clickup_status": "to do", "newest_comment": nc,
                "completion": [], "open": []}

    headings = [h for h, _l in check_addressed.report_blocks(
        [rec("868gz0hhh", False), rec("868gw0zzz", True)], now=stamp)]
    assert headings == [check_addressed.DECISION_HEADING, check_addressed.ANSWERED_HEADING], (
        "a producer did not receive report_blocks' `now` and fell back to the wall clock, so "
        f"the recency bound was applied against the wrong instant: {headings}")


def test_a_note_whose_recency_bound_cannot_be_EVALUATED_is_not_printed_at_all():
    """🔴 The tail of the bound fix. An unbounded no-action line is permanent noise.

    `_epoch_ms` accepts any int; `format_date` rejects an out-of-range one. So a record can
    carry a `date_ms` that decides the ms comparison while being unusable as an instant — no
    display age, no ms age, no bound that can ever expire. Measured while porting: a note
    printed over a comment whose date was `'-99999999999999999'`.

    The flag is the safe fallback and the asymmetry is deliberate: a call to action should
    survive an unreadable date (round 5 chose to SURFACE rather than assume stale), a "no
    action needed" line should not. Without this the honest `return None` in
    `_age_days_from_ms` is indistinguishable from a dishonest `return 0` — both leave the
    bound unenforced — and a mutant swapping them survives a green suite.
    """
    r = _rec(comment_days_ago=2, reply_days_ago=1)
    r["newest_comment"].update({
        "date": "-99999999999999999", "date_ms": -99999999999999999,
        "my_latest_reply": "2019-02-12 20:00", "my_latest_reply_ms": 1550000000000,
    })
    assert _notes(r) == [], (
        "a suppression note was printed with a recency bound that can NEVER expire: "
        f"{_notes(r)}")
    flags = _waiting_flags(r)
    assert flags and "unreadable" in " ".join(flags).lower(), (
        "the record was dropped entirely instead of falling through to the flag that "
        f"surfaces the unreadable date: {flags}")


# ------------------- holes the mutation sweep opened, and the tests that close them

def test_an_unreadable_comment_date_emits_NO_date_ms_rather_than_a_zero():
    """🔴 A SURVIVING MUTANT: `_epoch_ms(...) or 0`, and it silently drops waiting humans.

    Zero is 1970, so `mine_ms >= theirs_ms` is true for EVERY reply ever written and the flag
    is suppressed on any ticket whose comment date the producer could not read. That is the
    round-6 false positive's mirror — a colleague dropped from the report instead of nagged
    about — and it survived the whole suite, because every ms fixture used a readable date.
    An absent ms is not a zero and not a null; both of those are claims, and absence is not.
    """
    rec = recent_comments.build_record(
        "868gz0hhh", "n", {}, {"user": {"username": "x"}, "date": "corrupted"},
        "some text", "2026-08-22 01:04", 1755819840000)
    assert "date_ms" not in rec, \
        f"an unreadable comment date was reported as a definite instant: {rec.get('date_ms')!r}"

    # The behavioural half: prove the absence REACHES the flag and keeps it firing. A guard
    # nobody can watch fire is indistinguishable from one wired to nothing.
    nc = check_addressed.build_newest_comment(dict(rec, date=_at(2)))
    r = _rec()
    r["newest_comment"] = dict(nc, date=_at(2), author="Robin Example",
                               my_latest_reply=_at(5))     # my reply PREDATES the question
    assert _waiting_flags(r), (
        "a reply older than the question suppressed the flag because the comment's own "
        "unreadable date had been reported as an instant")


def test_build_record_refuses_a_raw_ms_that_is_not_an_int_and_never_orphans_one():
    """Two invariants of the record shape, both ⚠️ BY-CONSTRUCTION under the real producer.

    `latest_reply_ts_by` only ever returns an int, `None` or the sentinel, so neither arm is
    reachable through `_collect` today. They are pinned rather than deleted because both
    failures are SILENT — a string ms is quietly ignored by the consumer and an orphaned ms
    is a fact about a reply the record does not claim exists — and because `build_record` is
    called directly by four tests, which is enough of a surface to keep honest.
    """
    typed = recent_comments.build_record(
        "868gz0hhh", "n", {}, {"user": {"username": "x"}, "date": "1755900000000"},
        "some text", "2026-08-22 01:04", "1755819840000")
    assert "my_latest_reply_ms" not in typed, (
        "a STRING was emitted as a raw ms; the consumer reads ints only, so this is a field "
        f"that exists, looks authoritative and is silently ignored: {typed}")

    orphan = recent_comments.build_record(
        "868gz0hhh", "n", {}, {"user": {"username": "x"}, "date": "1755900000000"},
        "some text", recent_comments.UNIDENTIFIED, 1755819840000)
    assert "my_latest_reply_ms" not in orphan and "my_latest_reply" not in orphan, (
        "an ms outlived the formatted date it refines — the record now carries a precise "
        f"instant for a reply it does not claim to have found: {orphan}")


def test_a_raw_ms_without_its_formatted_date_does_not_decide_the_question():
    """🔴 A SURVIVING MUTANT: dropping `not reply_unreported` from the answered branch.

    `build_newest_comment` copies the two ms keys independently of `my_latest_reply`, so a
    record carrying `my_latest_reply_ms` with NO `my_latest_reply` is reachable at the
    consumer — a stale or hand-built meta is all it takes. Without the guard the ms alone
    decides, and the report files an unanswered ticket under "answered already", quoting a
    reply date of `None`.

    The EVIDENCE field is `my_latest_reply` and the ms is only PRECISION: precision about a
    fact nobody reported is not evidence, and the announce branch is the only honest answer.
    """
    r = _rec(comment_days_ago=2, reply_reported=False)
    r["newest_comment"]["date_ms"] = 1755819600000
    r["newest_comment"]["my_latest_reply_ms"] = 1755819900000     # "newer", but of what?
    flags = _waiting_flags(r)
    assert _notes(r) == [], \
        f"an ms with no reply behind it was reported as an answer: {_notes(r)}"
    assert flags and "not reported" in " ".join(flags).lower(), (
        "a record whose reply was never reported did not reach the announce branch once a "
        f"stray ms was present: {flags}")
