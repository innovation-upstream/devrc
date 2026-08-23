#!/usr/bin/env python3
"""Round 8 (2026-08-22). The DEFAULT invocation cannot fire two rules and nothing said so,
three invariants the prose GUARANTEED and nothing pinned, plus the parser robustness a
one-token mutant exposed.

🔴 SECTION 1 IS REGRESSION COVERAGE and is labelled there: it is red at the base of the
commit that made the transcript scan opt-in, where a scan-less run emits nothing at all.

⚠️ EVERYTHING BELOW SECTION 1 IS MUTATION-COVERAGE GUARDS, NOT REGRESSION COVERAGE — and the label needs one
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

🔴 The scan-less announcement and the `"mentions_found" in r` gate on "open signals remain"
were deliberately NOT ported when this repo had no `--transcripts` flag — the omission named
"if a `--transcripts` opt-in ever lands, both are required in the same commit" as its restore
condition. That opt-in landed 2026-08-22 and both are here, in section 1.
"""
import importlib.util
import io
import json
import sys
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


def _rec(tid="868gz0hhh", days=2, reply_days=None, status="to do", date_str=None, date_ms=True,
         scanned=True, transcript_status="no_mentions_found"):
    """A `task_status` record, carrying both the display strings and the raw epoch-ms.

    `scanned=False` OMITS `mentions_found`, which is exactly what the scan-less path in
    `main()` builds — an unsearched zero is not a searched one, so the key is absent rather
    than 0.
    """
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
    r = {"task_id": tid, "status": transcript_status, "sessions_searched": 1,
         "clickup_status": status, "clickup_priority": "high",
         "newest_comment": nc, "completion": [], "open": []}
    if scanned:
        r["mentions_found"] = 0
    return r


def _armed(scanned=True):
    """One record arming EVERY rule in `SCAN_ONLY_RULES` at once.

    THREE records, not one, and the split is forced rather than stylistic: "open signals
    remain" needs a DONE ClickUp status, while both "transcripts read as done" and the
    waiting flag need an OPEN one, so no single record can arm all four. They are returned
    together and fed to ONE `disagreements` call, which is what the ledger diff requires —
    per-rule fixtures would let one silently stop arming without the set changing size.

    ⚠️ The assertion that this fixture arms everything is IN the test, not here: a fixture
    that quietly stopped arming a rule would otherwise make the whole guard vacuous, and it
    already caught itself once (the waiting flag, missing on the first attempt).
    """
    their = NOW - timedelta(days=2)
    nc = {"date": _fmt(their), "date_ms": _ms(their), "author": "Robin Example",
          "snippet": "a question", "text": "a question", "my_latest_reply": None}
    cited = [{"signal": "PR merged", "snippet": "shipped in #4242",
              "pr_refs": [{"ref": "someorg/somerepo#4242", "state": "open"}]}]
    done = {"task_id": "868gy0ddd", "status": "open", "clickup_status": "closed",
            "clickup_priority": "high", "newest_comment": nc,
            "completion": cited, "open": []}
    still_open = {"task_id": "868gy0eee", "status": "likely_addressed",
                  "clickup_status": "to do", "clickup_priority": "high",
                  "newest_comment": nc, "completion": [], "open": []}
    waiting = {"task_id": "868gy0fff", "status": "no_mentions_found",
               "clickup_status": "to do", "clickup_priority": "high",
               "newest_comment": nc, "completion": [], "open": []}
    recs = (done, still_open, waiting)
    if scanned:
        done["mentions_found"] = 0
        still_open["mentions_found"] = 3
        waiting["mentions_found"] = 0
    else:
        # Exactly what the scan-less builder produces: no mention count, no verdict, and no
        # evidence lists for the cited-PR loop to walk.
        for r in recs:
            r["status"] = "not_scanned"
            r["completion"] = []
    return recs


def _flags(*recs):
    if recs and isinstance(recs[0], tuple):
        recs = recs[0]
    return check_addressed.disagreements(list(recs), now=NOW)


def _waiting(*recs):
    return [f for f in _flags(*recs) if "WAITING" in f]


def _notes(*recs):
    return check_addressed.suppressed_notes(list(recs), now=NOW)


# ------------------------------------------------ 1. the scan-less announcement
#
# 🔴 THESE FIVE ARE REGRESSION COVERAGE, not mutation-coverage guards: red at this PR's base,
# where a scan-less run emits nothing at all. Everything below this section is the mutation
# set the module docstring describes.

def test_a_scanless_run_ANNOUNCES_the_rules_it_could_not_run():
    """🔴 THE REGRESSION. An empty block reads as "checked, nothing disagrees". Checks this
    block is trusted for did not run, and only the run itself knows that.

    ⚠️ This test was named `..._the_two_rules_...` and asserted on two hardcoded substrings.
    FOUR rules go quiet, so the name was a false coverage claim and the body would have
    stayed green while the announcement dropped the other two. It now reads the LEDGER — a
    name is a claim like any other.
    """
    out = _flags(_rec(scanned=False, transcript_status="not_scanned"))
    assert out, "a scan-less run produced NO output at all — silence reading as a clean check"
    joined = " ".join(out)
    assert "--transcripts" in joined, \
        f"the announcement does not say how to enable the checks it names: {out}"
    assert "DID NOT RUN" in joined, \
        f"the announcement does not say the checks DID NOT RUN: {out}"
    missing = [n for n, _t in check_addressed.SCAN_ONLY_RULES if n not in joined]
    assert not missing, (
        "the announcement does not NAME every rule that could not fire, so a reader cannot "
        f"tell which coverage is missing: {missing}")


def test_the_scanless_ledger_matches_what_actually_goes_quiet():
    """🔴 THE RELATIONSHIP GUARD, and the reason the first announcement was wrong.

    It named TWO rules. FOUR go quiet in the scan-less default — the two gated on a SEARCHED
    zero, plus "transcripts read as done" (whose `likely_addressed` status a scan-less record
    can never hold) and the cited-PR loop (whose `completion`/`open` lists are empty). Naming
    a SUBSET is worse than naming none: it implies the unnamed ones ran, which is the same
    "an empty block reads as checked" defect the announcement exists to kill.

    So this does not check the WORDS. It arms every rule on one record, runs `disagreements`
    in both modes, and diffs. Whatever falls out of the scanned run must be exactly the
    ledger — failing when a fifth transcript-dependent rule is ADDED and when one is REMOVED,
    because a ledger asserted in one direction rots silently in the other.
    """
    scanned = _armed(scanned=True)
    quiet = _armed(scanned=False)
    before = {tail for _n, tail in check_addressed.SCAN_ONLY_RULES
              if any(tail in f for f in _flags(scanned))}
    assert before == {t for _n, t in check_addressed.SCAN_ONLY_RULES}, (
        "the fixture did not arm every ledger rule, so this test cannot see one going quiet "
        f"— it proves nothing until it does. Armed: {sorted(before)}")

    after = {tail for _n, tail in check_addressed.SCAN_ONLY_RULES
             if any(tail in f for f in _flags(quiet))}
    assert after == set(), \
        f"a ledger rule still fired on a record with no transcript evidence: {sorted(after)}"

    # 🔴 THE *GROWS* DIRECTION, and it is the half that is easy to fake. Both sets above are
    # derived FROM the ledger, so a fifth scan-dependent rule added to `disagreements` and
    # not to the ledger would be invisible to them — a two-way claim that is really one-way.
    # So: take the flags that ACTUALLY disappear between the two modes, and require every one
    # of them to be a ledger entry. A new rule that goes quiet now fails here, naming itself.
    quiet_flags = set(_flags(quiet))
    disappeared = [f for f in _flags(scanned) if f not in quiet_flags]
    unledgered = [f for f in disappeared
                  if not any(tail in f for _n, tail in check_addressed.SCAN_ONLY_RULES)]
    assert not unledgered, (
        "a rule goes quiet without the transcript scan and is NOT in SCAN_ONLY_RULES, so the "
        f"announcement does not name it and a reader is told the coverage ran: {unledgered}")

    named = " ".join(f for f in _flags(quiet) if f.startswith(check_addressed.SCANLESS_LEAD))
    for name, _tail in check_addressed.SCAN_ONLY_RULES:
        assert name in named, \
            f"rule {name!r} goes quiet in the default run and the announcement does not name it"


def test_the_announcement_never_contains_a_flags_own_tail():
    """🔴 INSTRUMENT SAFETY, made unexpressible rather than remembered.

    The announcement talks ABOUT these flags. If a rule's announcement NAME embedded that
    rule's unique TAIL, then `"<tail>" in output` — the obvious check, and the one three
    tests above use — would be TRUE in a run where the flag never fired. Upstream hit exactly
    this and it cost a false failure; it could as easily have cost a false pass.
    """
    for name, tail in check_addressed.SCAN_ONLY_RULES:
        assert tail not in name, (
            f"the announcement name {name!r} contains its own flag tail {tail!r}, so every "
            "substring check for that flag is now ambiguous")


def test_the_run_level_announcement_is_not_counted_as_a_flag_about_ONE_ticket():
    """🔴 The `[r]` scoping fix, re-entered from the other end of `disagreements`.

    The note's closing sentence is computed by asking `disagreements([r])` what else names
    THIS ticket. The scan-less announcement is a sentence about the RUN, appended to whatever
    list the function is handed — so counted, it makes the note say "'Needs a decision' above
    ALSO carries a line about this ticket" when the only line there is about the invocation.

    ⚠️ REACHABILITY WAS MEASURED, not assumed, and the first reading was wrong. `main()`
    cannot build this record (its scan-less path omits `mentions_found`, so the verdict can
    never be ANSWERED) — which nearly got the guard deleted as unreachable. But a record that
    is `not_scanned` AND carries a searched zero — a stale producer, a partial write, the
    exact shape the announcement's own comment names — reaches ANSWERED and leaks the line.
    """
    their = NOW - timedelta(days=2)
    mine = NOW - timedelta(hours=6)
    r = {"task_id": "868gz0hhh", "status": "not_scanned", "mentions_found": 0,
         "clickup_status": "to do", "clickup_priority": "high",
         "completion": [], "open": [],
         "newest_comment": {"date": _fmt(their), "date_ms": _ms(their),
                            "author": "Robin Example", "snippet": "q", "text": "q",
                            "my_latest_reply": _fmt(mine), "my_latest_reply_ms": _ms(mine)}}

    # Positive control on the fixture: it must actually reach the note, or this proves nothing.
    notes = _notes(r)
    assert len(notes) == 1, f"the fixture never reached the suppression note: {notes}"
    assert any(f.startswith(check_addressed.SCANLESS_LEAD) for f in _flags(r)), \
        "the fixture does not produce the run-level announcement, so nothing could leak"

    assert "no other flag names this ticket" in notes[0].lower(), (
        "the run-level scan-less announcement was counted as a per-ticket flag, so the note "
        f"claims another block names this ticket when none does: {notes[0]}")


def test_the_announcement_is_ONE_line_per_run_not_one_per_ticket():
    """The scan is skipped for the whole invocation, so a per-record line would put N identical
    sentences in the one block whose value depends on being short — the same volume argument
    that hoisted the bot-identity caveat. Round 4's announcement is per-RECORD because the
    status varies per record; this fact does not.
    """
    # Spelled out rather than built with an f-string: a computed id is invisible to
    # `test_no_real_identifiers.py`'s literal scan, so a real one could ride in unregistered.
    recs = [_rec(tid=tid, scanned=False, transcript_status="not_scanned")
            for tid in ("868gx0aaa", "868gx0bbb", "868gx0zzz", "868gx1ccc")]
    announcements = [f for f in _flags(*recs) if "--transcripts" in f]
    assert len(announcements) == 1, \
        f"expected exactly one announcement for {len(recs)} scan-less tickets: {announcements}"


def test_a_scanned_run_does_NOT_announce():
    """The anti-widening control. A permanent line is a line people learn to skip, and this
    one must disappear the moment the scan actually runs."""
    assert [f for f in _flags(_rec(scanned=True)) if "--transcripts" in f] == [], \
        "the scan-less announcement fired on a run that DID scan transcripts"


def test_the_open_signals_flag_needs_a_SEARCHED_zero_not_a_status_WORD():
    """🔴 A MUTANT THAT SURVIVED UPSTREAM: `"status": "not_scanned"` -> `"no_mentions_found"`.

    That sentinel was the only thing keeping "ClickUp `closed` but open signals remain" quiet
    on a scan that never ran — a SPELLED guard, where the waiting flag next to it gates on the
    STATE. Rename the sentinel and the flag fires off no evidence whatsoever, with a fully
    green suite. The gate is now `"mentions_found" in r`, which no rename can defeat.
    """
    # ⚠️ The FLAG's unique tail, not the rule's NAME — the announcement above names both
    # rules, so matching the name reports a flag that never fired.
    unscanned = _rec(status="closed", scanned=False, transcript_status="no_mentions_found")
    assert [f for f in _flags(unscanned) if "trusting the close" in f] == [], (
        "'open signals remain' fired off a transcript scan that never happened — an "
        "unsearched zero was read as a searched one")
    # Positive control: the same record WITH a searched zero must still flag, or the
    # assertion above would pass for the wrong reason.
    scanned = _rec(status="closed", scanned=True, transcript_status="no_mentions_found")
    assert [f for f in _flags(scanned) if "trusting the close" in f], \
        "the open-signals flag stopped firing on a genuinely searched zero — control failed"


def test_main_announces_in_the_DEFAULT_mode_and_stops_once_the_scan_runs():
    """END TO END, on real stdout, because the claim is about what a reader SEES.

    Upstream found this by driving `main()` and reading its output; reading the code said the
    flags were present and correct, which they are — they simply cannot fire. A test that
    stopped at `disagreements` would have agreed with the code and missed it.
    """
    now = datetime.now(timezone.utc)
    theirs = now - timedelta(days=2)
    comments = [{"task_id": "868qw0e2e", "task_name": "t", "task_status": "to do",
                 "task_priority": "high", "date": theirs.strftime(FMT),
                 "author": "Robin Example", "snippet": "a question", "text": "a question",
                 "date_ms": int(theirs.timestamp() * 1000), "my_latest_reply": None}]

    def fake(name, *args):
        if name == "recent-comments.py":
            return json.dumps(comments), 0
        if name == "search-sessions.py":
            return json.dumps({"sessions": [], "self_runs_skipped": 0,
                               "self_runs_skipped_ids": []}), 0
        if name == "check-completion.py":
            return json.dumps([{"task_id": args[args.index("--task") + 1], "status": "open",
                                "sessions_searched": 1, "mentions_found": 0,
                                "completion": [], "open": []}]), 0
        raise AssertionError(name)

    def drive(*argv):
        o_run, o_argv, buf, o_out = check_addressed.run_script, sys.argv, io.StringIO(), sys.stdout
        try:
            check_addressed.run_script = fake
            sys.argv = ["check-addressed.py", *argv]
            sys.stdout = buf
            check_addressed.main()
        finally:
            sys.stdout = o_out
            check_addressed.run_script, sys.argv = o_run, o_argv
        return buf.getvalue()

    default = drive()
    assert check_addressed.DECISION_HEADING in default, (
        "the DEFAULT run printed no decision block at all, so two rules that could not run "
        f"left no trace:\n{default[-600:]}")
    assert "--transcripts" in default and "DID NOT RUN" in default, \
        f"the default run did not announce the checks it skipped:\n{default[-600:]}"
    assert "is WAITING" not in default, \
        "the waiting flag fired without a searched zero — the announcement is not a licence"
    # 🔴 The BODY of the report has to say it too, not only the decision block. Three
    # separate renderings claimed a transcript verdict that was never formed:
    assert "Transcripts were NOT scanned" in default, (
        "the completion-status block printed the evidence-marker legend over records that "
        f"carry no evidence:\n{default[-900:]}")
    assert check_addressed.MARKER_LEGEND not in default, \
        "the marker legend describes evidence lines a scan-less run cannot produce"
    assert "mentions)" not in default, (
        "a scan-less record printed a mention COUNT — the searched-zero claim in the one "
        f"place a reader looks first:\n{default[-900:]}")
    assert "no completion verdict was formed" in default, (
        "the summary printed a tally of verdicts nobody computed — four zeroes that read as "
        f"a finding:\n{default[-400:]}")

    scanned = drive("--transcripts")
    assert "is WAITING" in scanned, "the waiting flag did not fire even WITH --transcripts"
    assert "DID NOT RUN" not in scanned, \
        f"a scanned run still announced a skipped scan:\n{scanned[-600:]}"
    # The anti-widening half: the scan-less wording must not leak into a real run.
    assert "Transcripts were NOT scanned" not in scanned and check_addressed.MARKER_LEGEND in scanned, \
        f"a scanned run rendered as if it had skipped the scan:\n{scanned[-900:]}"
    assert "no completion verdict was formed" not in scanned and " addressed," in scanned, \
        f"a scanned run withheld its own summary tally:\n{scanned[-400:]}"


def test_the_DEFAULT_run_still_carries_the_comment_derived_evidence():
    """🔴 THE COVERAGE HOLE THAT LET THREE CALL-SITE MUTANTS LIVE.

    Every other default-mode drive uses a NEUTRAL comment, so no comment- or status-derived
    rule was exercised in default mode anywhere — and three one-token mutants on the
    scan-less record's CONTENT (`newest_comment` -> `{}`, `meta` -> `{}`, `clickup_status`
    -> `None`) survived a fully green suite. The first silently deletes the RESOLVED-comment-
    on-an-open-ticket flag, which this skill's docs call the highest-value line in the report,
    on what is now the DEFAULT path.

    T4-T9 cover whether the scan-less BRANCH runs and what its count fields say. This covers
    what the record actually CARRIES, end to end, on stdout — the ClickUp evidence is the
    only evidence the default mode has, so a record that drops it has nothing left.
    """
    now = datetime.now(timezone.utc)
    theirs = now - timedelta(days=1)
    comments = [{"task_id": "868qw0e2e", "task_name": "t", "task_status": "to do",
                 "task_priority": "urgent", "date": theirs.strftime(FMT),
                 "author": "Robin Example",
                 "snippet": "this is fixed and shipped, resolved on our side",
                 "text": "this is fixed and shipped, resolved on our side",
                 "date_ms": int(theirs.timestamp() * 1000), "my_latest_reply": None}]

    def fake(name, *a):
        if name == "recent-comments.py":
            return json.dumps(comments), 0
        raise AssertionError("a scan-less run must not call " + name)

    o_run, o_argv, buf, o_out = check_addressed.run_script, sys.argv, io.StringIO(), sys.stdout
    try:
        check_addressed.run_script = fake
        sys.argv = ["check-addressed.py"]
        sys.stdout = buf
        check_addressed.main()
    finally:
        sys.stdout = o_out
        check_addressed.run_script, sys.argv = o_run, o_argv
    out = buf.getvalue()

    assert "reads as RESOLVED but the ticket is still" in out, (
        "the DEFAULT run lost the resolved-comment-on-an-open-ticket flag — the scan-less "
        f"record is not carrying its ClickUp evidence:\n{out[-900:]}")
    assert "868qw0e2e" in out and "to do" in out, \
        f"the scan-less record dropped the ticket's own status:\n{out[-900:]}"
    assert "urgent" in out, f"the scan-less record dropped the priority:\n{out[-900:]}"
    assert "resolved on our side" in out, \
        f"the scan-less record dropped the newest comment itself:\n{out[-900:]}"


def test_a_scanless_json_run_omits_UNSEARCHED_session_counts_and_declares_the_mode():
    """The absent-vs-zero discipline applied to `mentions_found`' SIBLINGS.

    `sessions_found: 0` / `sessions_by_task: {}` / `sessions_searched: 0` are unsearched
    zeros — the exact claim this path refuses to make — and a consumer was left inferring the
    mode from a spelled sentinel. `transcripts` is now stated outright.
    """
    comments = [{"task_id": "868qw0e2e", "task_name": "t", "task_status": "to do",
                 "task_priority": "high", "date": "2026-08-21 09:00",
                 "author": "Robin Example", "snippet": "q", "text": "q"}]

    def fake(name, *a):
        if name == "recent-comments.py":
            return json.dumps(comments), 0
        raise AssertionError(name)

    def drive(*argv):
        o_run, o_argv, buf, o_out = check_addressed.run_script, sys.argv, io.StringIO(), sys.stdout
        try:
            check_addressed.run_script = fake
            sys.argv = ["check-addressed.py", "--json", *argv]
            sys.stdout = buf
            check_addressed.main()
        finally:
            sys.stdout = o_out
            check_addressed.run_script, sys.argv = o_run, o_argv
        return json.loads(buf.getvalue())

    p = drive()
    assert p["transcripts"] is False, \
        "a --json consumer cannot tell the mode without parsing a spelled status sentinel"
    assert "sessions_found" not in p and "sessions_by_task" not in p, (
        "an UNSEARCHED zero was emitted as a session count — the claim this path exists to "
        f"refuse: {{k: p[k] for k in ('sessions_found', 'sessions_by_task') if k in p}}")
    assert "sessions_searched" not in p["task_status"][0], \
        f"the record emitted an unsearched session count: {p['task_status'][0]}"
    assert "mentions_found" not in p["task_status"][0], \
        "the mention count regressed to a reported zero"


def test_flags_that_only_feed_the_scan_WARN_when_the_scan_is_off():
    """`parse_args`' own rule, applied one level up: a silently-ignored flag is a run that
    looks like it honoured your request and did not. This file already shipped that bug once
    with `--include-self-runs`, which was parsed by nothing and forwarded nowhere.
    """
    comments = [{"task_id": "868qw0e2e", "task_name": "t", "task_status": "to do",
                 "task_priority": "high", "date": "2026-08-21 09:00",
                 "author": "Robin Example", "snippet": "q", "text": "q"}]

    def fake(name, *a):
        if name == "recent-comments.py":
            return json.dumps(comments), 0
        if name == "search-sessions.py":
            return json.dumps({"sessions": [], "self_runs_skipped": 0,
                               "self_runs_skipped_ids": []}), 0
        return json.dumps([{"task_id": "868qw0e2e", "status": "no_mentions_found",
                            "sessions_searched": 1, "mentions_found": 0,
                            "completion": [], "open": []}]), 0

    def drive(argv):
        o_run, o_argv, o_out, o_err = (check_addressed.run_script, sys.argv,
                                       sys.stdout, sys.stderr)
        err = io.StringIO()
        try:
            check_addressed.run_script = fake
            sys.argv = ["check-addressed.py", *argv]
            sys.stdout, sys.stderr = io.StringIO(), err
            check_addressed.main()
        finally:
            sys.stdout, sys.stderr = o_out, o_err
            check_addressed.run_script, sys.argv = o_run, o_argv
        return err.getvalue()

    warned = drive(["--since", "2026-08-01", "--include-self-runs"])
    assert "WARNING" in warned and "--since" in warned and "--include-self-runs" in warned, \
        f"inert flags were accepted in silence: {warned!r}"

    # Anti-widening controls in BOTH directions — a warning that always fires is noise, and
    # one that fires when the flags DO work is a lie about the run.
    assert drive([]) == "", \
        "a bare default run warned about flags nobody passed"
    with_scan = drive(["--transcripts", "--since", "2026-08-01", "--include-self-runs"])
    assert "WARNING" not in with_scan, \
        f"the flags were honoured and the run still called them ignored: {with_scan!r}"


# ------------------------------------------------ 2. the three unpinned invariants

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
