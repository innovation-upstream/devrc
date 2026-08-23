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


def test_latest_reply_by_ignores_an_unparseable_date():
    """A malformed date must not win the max() and must not crash the fetch."""
    comments = [
        {"user": {"id": "7"}, "date": "not-a-timestamp"},
        {"user": {"id": "7"}, "date": "1755800000000"},
    ]
    assert recent_comments.latest_reply_by(comments, "7") == \
        recent_comments.format_date("1755800000000")


def test_build_record_carries_my_latest_reply():
    """Pins the JOIN. Both ends of this seam are testable in isolation and the fix is
    inert unless the field actually reaches the record — the same way dropping `text`
    from this dict silently reverted the keep-open veto.
    """
    r = recent_comments.build_record(
        "868gz0hhh", "name", {}, {"user": {"username": "Robin Example"}, "date": "1755900000000"},
        "some text", "2026-08-22 01:04")
    assert r.get("my_latest_reply") == "2026-08-22 01:04", \
        f"build_record dropped my_latest_reply, so the fix cannot reach the flag: {r}"


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
        "some text", None)
    assert "my_latest_reply" in r, (
        "a genuine 'no reply from me' lost the key, so the consumer will announce that the "
        f"check never ran instead of reporting that nobody answered: {r}")
    assert r["my_latest_reply"] is None, f"expected a reported null, got {r['my_latest_reply']!r}"


def test_collect_computes_the_reply_over_the_UNFILTERED_comment_list():
    """The wiring, not the parts. Added because a mutation sweep found nothing covered it.

    `latest_reply_by` and `build_record` are both correct in isolation and both pinned
    above, yet `_collect` could call the first with the wrong list — after the loop that
    discards my comments, say — and the entire fix would be inert with a fully green
    suite. Mutating the call to `latest_reply_by([], my_id)` SURVIVED every other test
    here. That is this skill's own headline defect class: a seam no single-component test
    owns.
    """
    calls = {}

    def fake_comments(_tid):
        return [
            {"user": {"id": "9", "username": "Robin Example"},
             "date": "1755800000000", "comment": [{"text": "a question"}]},
            {"user": {"id": "7", "username": "me"},
             "date": "1755900000000", "comment": [{"text": "my answer"}]},
        ]

    orig = (recent_comments.get_my_user_id, recent_comments.get_my_tasks,
            recent_comments.get_comments)
    try:
        recent_comments.get_my_user_id = lambda: "7"
        recent_comments.get_my_tasks = lambda: [{"id": "868gz0hhh", "name": "t"}]
        recent_comments.get_comments = fake_comments
        buf = io.StringIO()
        real_stdout, sys.stdout = sys.stdout, buf
        try:
            recent_comments._collect(10, False, True)
        finally:
            sys.stdout = real_stdout
        calls = json.loads(buf.getvalue())
    finally:
        (recent_comments.get_my_user_id, recent_comments.get_my_tasks,
         recent_comments.get_comments) = orig

    assert len(calls) == 1, f"expected only the colleague's comment to survive: {calls}"
    assert calls[0]["author"] == "Robin Example"
    assert calls[0]["my_latest_reply"] == recent_comments.format_date("1755900000000"), (
        "_collect did not report my reply — the reply must be computed over the FULL "
        f"comment list, before mine are discarded: {calls[0]}")


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

    orig = (recent_comments.get_my_user_id, recent_comments.get_my_tasks,
            recent_comments.get_comments)
    try:
        recent_comments.get_my_user_id = lambda: None          # the failure under test
        recent_comments.get_my_tasks = lambda: [{"id": "868gz0hhh", "name": "t"}]
        recent_comments.get_comments = fake_comments
        buf, err = io.StringIO(), io.StringIO()
        real_out, real_err = sys.stdout, sys.stderr
        sys.stdout, sys.stderr = buf, err
        try:
            recent_comments._collect(10, False, True)
        finally:
            sys.stdout, sys.stderr = real_out, real_err
        rows = json.loads(buf.getvalue())
    finally:
        (recent_comments.get_my_user_id, recent_comments.get_my_tasks,
         recent_comments.get_comments) = orig

    assert rows, "no rows emitted"
    assert "my_latest_reply" not in rows[0], (
        "a FAILED user lookup was reported as 'I looked; you never replied' — the key must be "
        f"absent so the consumer announces rather than decides: {rows[0]}")
    assert "WARNING" in err.getvalue(), \
        f"an unidentifiable user produced no warning on stderr: {err.getvalue()!r}"

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
