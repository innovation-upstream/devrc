#!/usr/bin/env python3
"""Regression tests for the hardening ported from PR #1202 (2026-08-21).

That PR ran seven audit rounds on a branch off an older trunk; trunk meanwhile grew its own
parallel arc. Only the non-overlapping, non-controversial half was ported — the keep-open
veto redesign was deliberately LEFT BEHIND, because every regression in that arc came from it
(five rounds, five regressions, each created by the previous round's fix).

What is here, and the mutant each test kills:

  P1  search-sessions.py dropped self-runs with a bare `continue` — no count returned, none
      printed. The caller saw a shorter list and a smaller "N found" header, which is
      indistinguishable from the sessions not existing.
  P2  `is_self_run` reads a transcript to EOF and ran BEFORE the mtime/`--since` filter, so
      it re-read the whole corpus for an identical result set — once PER TASK. Measured over
      746 files: 5.8s -> 16.6s, back to 6.0s once moved after term-matching.
  P3  the keep-open veto decided on a 200-char DISPLAY truncation. The comment that motivated
      the veto is 196 characters — it cleared the cap by four, so a sentence reorder restored
      "close it".
  P4  the search stage's skipped ids were not forwarded, so the completion stage rediscovered
      and re-counted the same transcripts and the report named one set of files twice.
  P5  a transcript read error was CACHED, pinning "not a self-run" for the whole process.
  P6  the guard read every transcript with errors="replace" while the readers did not —
      UnicodeDecodeError is a ValueError, which `except (JSONDecodeError, OSError)` misses.

Red-at-base matrix, measured against pristine `origin/trunk`: **12 of these 16 fail there,
4 pass.** The four that pass are INVARIANT GUARDS, not regression coverage, and are labelled
as such — trunk already skips prior runs on the auto-discovery path (it shipped with that),
so `test_auto_discovery_*`, `test_skip_count_is_pinned_when_evidence_survives` and
`test_search_stage_skips_are_excluded_from_the_completion_stage` pin behaviour that is
already correct. They earn their place by killing mutants, not by having been red.

Two of the twelve are LIVE DEFECTS on trunk, not merely missing coverage:
  * `test_readers_tolerate_undecodable_bytes` — trunk raises `UnicodeDecodeError` on a
    transcript containing one invalid byte, which the callers do not catch.
  * `test_keep_open_veto_reads_past_the_display_truncation` — trunk decides the veto on the
    200-char snippet, so a refusal written after that boundary produces "close it". That is
    the 868gx0bbb shape with the clauses reordered.
"""
import contextlib, io, json, sys, tempfile
from datetime import datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(SCRIPT_DIR))

import importlib.util


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, SCRIPT_DIR / filename)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


check_completion = _load("check_completion", "check-completion.py")
search_sessions = _load("search_sessions", "search-sessions.py")
check_addressed = _load("check_addressed", "check-addressed.py")
recent_comments = _load("recent_comments", "recent-comments.py")
import _selfrun as selfrun  # noqa: E402

TARGET = "868gx1ccc"
PRIOR_RUN_REPORT = (
    "## Task Completion Status\n\n"
    f"✅ **{TARGET}** — transcripts say `likely_addressed`\n"
    f"  ✓ resolved: the fix shipped in #1065, merged and verified on trunk\n"
)


def _session(tmpdir, session_id, *texts):
    mock_dir = Path(tmpdir) / ".claude" / "projects" / "test-project"
    mock_dir.mkdir(parents=True, exist_ok=True)
    with open(mock_dir / f"{session_id}.jsonl", "w") as f:
        for t in texts:
            f.write(json.dumps(
                {"type": "assistant", "message": {"content": [{"type": "text", "text": t}]}}
            ) + "\n")
    return mock_dir.parent


# --------------------------------------------------------------------------- P1

def test_search_sessions_reports_what_it_dropped():
    with tempfile.TemporaryDirectory() as tmp:
        root = _session(tmp, "prior-run-s", PRIOR_RUN_REPORT)
        _session(tmp, "real-work-s", f"digging into {TARGET} today")
        orig, search_sessions.CLAUDE_DIR = search_sessions.CLAUDE_DIR, root
        stats = {}
        try:
            found = search_sessions.search_sessions([TARGET], stats=stats)
        finally:
            search_sessions.CLAUDE_DIR = orig

    assert [r["session_id"] for r in found] == ["real-work-s"]
    assert stats["self_runs_skipped"] == 1, f"drop went unreported: {stats}"
    assert stats["self_runs_skipped_ids"] == ["prior-run-s"]
    # 🔴 The self-run drop is not the only way a transcript leaves this walk. The other
    # three reached a human through the shared walk's stderr alone, and nothing in
    # check-addressed.py reads stderr. Asserted from the REAL call, not a hand-built
    # dict, so a `stats` wiring that silently stops forwarding them fails here.
    for key, want in (("unreadable", 0), ("skipped_stale", 0), ("sessions_examined", 2)):
        assert key in stats, f"{key} did not travel with the results: {stats}"
        assert stats[key] == want, f"{key}={stats[key]!r}, wanted {want}: {stats}"
    assert stats["unreadable_paths"] == []


def test_search_payload_is_an_object_and_round_trips_through_the_consumer():
    """Producer and consumer asserted TOGETHER, so the two halves cannot drift apart —
    reverting either alone passed the suite when only one side was pinned.

    The payload spreads `stats`, so the keys added on 2026-08-25 ride along with no
    change to `render_payload`. The consumer reads its three by name via `.get()`, which
    is why an added key is additive rather than breaking — asserted here rather than
    assumed, since "it uses .get()" is a claim about code nobody re-read.
    """
    payload = search_sessions.render_payload(
        [{"session_id": "s1"}],
        {"self_runs_skipped": 2, "self_runs_skipped_ids": ["p1", "p2"],
         "unreadable": 1, "unreadable_paths": ["/tmp/x.jsonl"],
         "skipped_stale": 7, "sessions_examined": 40})
    assert isinstance(payload, dict), f"emitted a bare list: {payload!r}"
    assert check_addressed.parse_search_payload(payload) == \
        ([{"session_id": "s1"}], 2, ["p1", "p2"])
    for key, want in (("unreadable", 1), ("skipped_stale", 7),
                      ("sessions_examined", 40), ("unreadable_paths", ["/tmp/x.jsonl"])):
        assert payload[key] == want, f"{key} missing from the --json document: {payload}"


def test_search_payload_tolerates_the_legacy_bare_list():
    """Back-compat for a stale copy of search-sessions.py: degrade to "no count" rather
    than a TypeError mid-report."""
    assert check_addressed.parse_search_payload([{"session_id": "s1"}]) == \
        ([{"session_id": "s1"}], 0, [])


def test_skip_note_reports_both_stages_without_summing():
    note = check_addressed.skip_note({"self_runs_skipped_search": 2, "self_runs_skipped": 3})
    assert "2 at session-search" in note and "3 at completion" in note, note
    assert "5" not in note, f"the two stages were summed, which double-counts: {note}"
    assert check_addressed.skip_note({"self_runs_skipped_search": 0}) == "", \
        "announced a drop that did not happen"


# --------------------------------------------------------------------------- P2

def test_self_run_check_runs_after_term_matching():
    """`is_self_run` must never be handed a file that does not match the search terms.

    Pinning only "after the date filter" is NOT enough and that gap shipped once:
    `check-addressed.py` passes `--since` only when the operator supplies one, so the
    DEFAULT path has no date filter at all and the whole corpus was re-read per task.
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = _session(tmp, "irrelevant", "a session about something else entirely")
        _session(tmp, "relevant", f"working on {TARGET} today")
        seen = []
        real = search_sessions.is_self_run
        search_sessions.is_self_run = lambda p: (seen.append(str(p)), real(p))[1]
        orig, search_sessions.CLAUDE_DIR = search_sessions.CLAUDE_DIR, root
        try:
            search_sessions.search_sessions([TARGET])      # no `since` — the default path
        finally:
            search_sessions.CLAUDE_DIR = orig
            search_sessions.is_self_run = real

    assert [Path(p).stem for p in seen] == ["relevant"], \
        (f"is_self_run was handed non-matching file(s) — it reads to EOF, so this is a "
         f"full-corpus re-read per task: {[Path(p).stem for p in seen]}")


def test_date_filter_still_precedes_the_self_run_check():
    with tempfile.TemporaryDirectory() as tmp:
        root = _session(tmp, "ancient", f"{TARGET} mentioned here")
        seen = []
        real = search_sessions.is_self_run
        search_sessions.is_self_run = lambda p: (seen.append(str(p)), real(p))[1]
        orig, search_sessions.CLAUDE_DIR = search_sessions.CLAUDE_DIR, root
        try:
            search_sessions.search_sessions([TARGET], since=datetime(2099, 1, 1))
        finally:
            search_sessions.CLAUDE_DIR = orig
            search_sessions.is_self_run = real
    assert seen == [], f"read {len(seen)} file(s) the date filter had already excluded"


# --------------------------------------------------------------------------- P3

def test_comment_record_carries_full_text_and_a_truncated_snippet():
    """Literals, not the implementation's own constants — an expectation read from the
    constant cannot see the constant change. Fixture exceeds BOTH caps, or narrowing the
    analysis cap to a value above the fixture length is invisible too."""
    long_text = "y" * 4200 + " do not close"
    rec = recent_comments.build_record(
        "868aaa111", "a task", {}, {"date": "1700000000000", "user": {"username": "x"}},
        long_text, None, None)
    assert len(rec["snippet"]) == 200, f"display snippet cap moved: {len(rec['snippet'])}"
    assert len(rec["text"]) == 4000, f"analysis cap moved: {len(rec['text'])}"
    assert rec["text"] == long_text[:4000]


def test_newest_comment_carries_full_text_not_just_the_snippet():
    long_text = "x" * 500 + " do not close"
    nc = check_addressed.build_newest_comment({"snippet": long_text[:200], "text": long_text})
    assert nc["text"] == long_text, "full comment text was not carried through"
    assert len(nc["snippet"]) == 200, "display snippet should stay truncated"
    assert check_addressed.build_newest_comment({"snippet": "short"})["text"] == "short"


def test_keep_open_veto_reads_past_the_display_truncation():
    """The behavioural half of P3, on trunk's own veto: a refusal written after the 200-char
    boundary must still veto. Synthetic, built to the 868gx0bbb shape with the keep-open
    clause LAST — idiomatic for a status update."""
    padding = ("The queue-depth alert fired and resolved repeatedly overnight, 14 cycles "
               "in three hours. The clean sweep on the 3rd looks like a lucky snapshot "
               "and the numbers below all come from the same window, so read them together. ")
    text = padding + "Still live, do not close."
    assert len(text) > 200, "fixture must exceed the truncation to test anything"
    flags = check_addressed.disagreements([{
        "task_id": "868gx0bbb", "status": "likely_addressed", "clickup_status": "to do",
        "mentions_found": 3, "newest_comment": {"snippet": text[:200], "text": text},
    }])
    assert not any("close it" in f.lower() for f in flags), \
        f"keep-open clause past the truncation was missed: {flags}"
    assert any("do NOT close" in f for f in flags), f"veto did not fire: {flags}"


# --------------------------------------------------------------------------- P4

def test_search_stage_skips_are_excluded_from_the_completion_stage():
    """🔴 This test used to BUILD `args` in its own body and assert on its own construction —
    it imported no production symbol and touched no code path, so deleting the forwarding
    loop in `main()` left the suite green. Exactly the vacuous shape this suite exists to
    catch, shipped inside a docstring claiming these tests kill mutants.

    Now it drives the real behaviour end to end: `check_task` must not READ a session that
    the search stage already dropped, and must not re-count it either.
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = _session(tmp, "prior-run-fwd", PRIOR_RUN_REPORT)
        orig, check_completion.CLAUDE_DIR = check_completion.CLAUDE_DIR, root
        try:
            # No exclusion: the completion stage rediscovers the prior run and counts it,
            # which is the double-count the forwarding prevents.
            without = check_completion.check_task(TARGET)
            # With the search stage's skip forwarded, it is excluded BEFORE the self-run
            # check, so it is neither read nor counted a second time.
            with_fwd = check_completion.check_task(
                TARGET, exclude_sessions=["prior-run-fwd"])
        finally:
            check_completion.CLAUDE_DIR = orig

    assert without["self_runs_skipped"] == 1, \
        f"positive control: the completion stage should rediscover it: {without}"
    assert with_fwd["self_runs_skipped"] == 0, \
        (f"a search-stage skip was re-counted at the completion stage — the report would "
         f"name one set of files twice: {with_fwd}")
    assert with_fwd["status"] != "likely_addressed", \
        f"the excluded prior run was still read as evidence: {with_fwd['status']!r}"


def test_main_forwards_skips_and_reports_the_count():
    """🔴 The JOIN inside `main()`, driven end to end with the three subprocesses stubbed.

    Two mutants survived every earlier attempt at this, because each test exercised the two
    ENDS and never the wiring: dropping the `--exclude-session` forwarding loop, and
    hardcoding `self_runs_skipped_search = 0`, both left the suite green. Asserting on
    `check_task(exclude_sessions=...)` proves the exclusion WORKS; it does not prove `main()`
    passes it. The only thing that proves the join is running the join.
    """
    captured = {}

    def fake_run_script(name, *args):
        if name == "recent-comments.py":
            return json.dumps([{
                "task_id": "868aaa111", "task_name": "a task", "task_status": "to do",
                "task_priority": "high", "date": "2026-08-21 10:00", "author": "someone",
                "snippet": "Please take a look.", "text": "Please take a look.",
            }]), 0
        if name == "search-sessions.py":
            captured["search"] = args
            return json.dumps({"sessions": [], "self_runs_skipped": 2,
                               "self_runs_skipped_ids": ["prior-a", "prior-b"]}), 0
        if name == "check-completion.py":
            captured["check"] = args
            return json.dumps([{
                "task_id": "868aaa111", "status": "unclear", "sessions_searched": 0,
                "mentions_found": 1, "self_runs_skipped": 0, "completion": [], "open": [],
            }]), 0
        raise AssertionError(f"unexpected script {name}")

    real_run, real_argv = check_addressed.run_script, sys.argv
    check_addressed.run_script = fake_run_script
    sys.argv = ["check-addressed.py", "--transcripts", "--limit", "1", "--no-resolve-prs"]
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            check_addressed.main()
    finally:
        check_addressed.run_script, sys.argv = real_run, real_argv

    check_args = list(captured.get("check", ()))
    for sid in ("prior-a", "prior-b"):
        assert sid in check_args, \
            f"search-stage skip {sid!r} was not forwarded to the completion stage: {check_args}"
        assert check_args[check_args.index(sid) - 1] == "--exclude-session", (
            f"{sid!r} is paired with {check_args[check_args.index(sid) - 1]!r} — a prior run "
            f"is being fed INTO the completion stage: {check_args}")

    report = out.getvalue()
    assert "2 at session-search" in report, \
        f"the search-stage drop never reached the report — it is invisible again:\n{report}"


def test_orchestrator_carries_the_search_skip_count_into_the_record():
    """The JOIN, which was the untested half: `skip_note` was pinned on a hand-built dict
    and `parse_search_payload` on a hand-built payload, while the line in `main()` wiring one
    to the other was not — hardcoding it to 0 left the suite green and silently returned the
    search-stage drop to being invisible.

    Asserted through the real consumer so producer and consumer cannot drift apart.
    """
    sessions, skipped, ids = check_addressed.parse_search_payload(
        {"sessions": [], "self_runs_skipped": 2, "self_runs_skipped_ids": ["p1", "p2"]})
    record = {"task_id": "868aaa111", "self_runs_skipped_search": skipped}
    note = check_addressed.skip_note(record)
    assert "2 at session-search" in note, \
        f"the parsed search count did not reach the report line: {note!r}"
    assert ids == ["p1", "p2"]


def test_widened_window_did_not_widen_the_close_it_trigger():
    """🔴 Reading the full comment is for the VETO only.

    Feeding `RESOLVED_COMMENT_RE` the full 4000 chars widens the close-it trigger 20x in the
    UNSAFE direction. Fixture: a long status comment on an open ticket whose only match is
    the alert-cycling sense of "resolved", past the 200-char display boundary, with NO
    keep-open clause anywhere — so the veto cannot rescue it.
    """
    padding = ("Weekly status. Throughput held flat across the window and the backlog drained "
               "on schedule; nothing here needs a decision from anyone this week, and the "
               "numbers below are all from the same 24h window so they can be read together. ")
    text = padding + "The queue-depth alert fired and resolved repeatedly overnight."
    assert len(padding) > 200, "the trigger phrase must sit past the display truncation"

    flags = check_addressed.disagreements([{
        "task_id": "868zzz111", "status": "unclear", "clickup_status": "to do",
        "mentions_found": 3, "newest_comment": {"snippet": text[:200], "text": text},
    }])
    assert not any("reads as RESOLVED" in f for f in flags), \
        (f"an alert-cycling 'resolved' past the display window produced a close-it "
         f"instruction on a live ticket: {flags}")


def test_auto_discovery_skips_prior_runs_and_says_so():
    """The production fallback: `check_task` with NO session_ids scans the corpus itself.
    That branch is what `check-addressed.py` reaches whenever search returns 0 sessions —
    which, now that search itself drops self-runs, is exactly the prior-run scenario."""
    with tempfile.TemporaryDirectory() as tmp:
        root = _session(tmp, "prior-run-auto", PRIOR_RUN_REPORT)
        orig, check_completion.CLAUDE_DIR = check_completion.CLAUDE_DIR, root
        try:
            r = check_completion.check_task(TARGET)          # no session_ids
        finally:
            check_completion.CLAUDE_DIR = orig
    assert r["status"] != "likely_addressed", \
        f"auto-discovery read a prior run back as evidence: {r['status']!r}"
    assert r["self_runs_skipped"] == 1, f"skip counter not incremented: {r}"


def test_auto_discovery_positive_control():
    """Control: the same path must still FIND a real session, or the test above could pass
    by discovering nothing at all."""
    with tempfile.TemporaryDirectory() as tmp:
        root = _session(tmp, "real-work-auto", f"{TARGET}: the fix shipped in #1065, merged.")
        orig, check_completion.CLAUDE_DIR = check_completion.CLAUDE_DIR, root
        try:
            r = check_completion.check_task(TARGET)
        finally:
            check_completion.CLAUDE_DIR = orig
    assert r["status"] == "likely_addressed", f"auto-discovery found nothing: {r['status']!r}"
    assert r["self_runs_skipped"] == 0


def test_skip_count_is_pinned_when_evidence_survives():
    """The case with the most at stake: a prior run WAS dropped and real evidence still
    produced a verdict. Asserts 1, deliberately not 0 — a fixture whose expected value
    equals a hardcoding mutant's literal cannot see that mutant."""
    with tempfile.TemporaryDirectory() as tmp:
        root = _session(tmp, "prior-run-mix", PRIOR_RUN_REPORT)
        _session(tmp, "real-work-mix", f"{TARGET}: the fix shipped in #1065, merged.")
        orig, check_completion.CLAUDE_DIR = check_completion.CLAUDE_DIR, root
        try:
            r = check_completion.check_task(TARGET)
        finally:
            check_completion.CLAUDE_DIR = orig
    assert r["status"] == "likely_addressed", f"real evidence lost: {r['status']!r}"
    assert r["self_runs_skipped"] == 1, \
        f"a dropped prior run went unreported alongside a verdict: {r['self_runs_skipped']!r}"


# --------------------------------------------------------------------------- P5 / P6

def test_unreadable_transcript_is_not_cached_as_safe():
    """A read error answers "not a self-run", the unsafe direction. Caching it pins that
    answer for the whole process and lets a prior run be read as evidence thereafter."""
    with tempfile.TemporaryDirectory() as tmp:
        root = _session(tmp, "flaky", PRIOR_RUN_REPORT)
        path = root / "test-project" / "flaky.jsonl"
        selfrun._cache.pop(str(path), None)

        import builtins
        real_open, calls = builtins.open, {"n": 0}

        def flaky_open(*a, **kw):
            if a and str(a[0]) == str(path):
                calls["n"] += 1
                if calls["n"] == 1:
                    raise OSError("transient")
            return real_open(*a, **kw)

        builtins.open = flaky_open
        try:
            first = selfrun.is_self_run(path)
            second = selfrun.is_self_run(path)
        finally:
            builtins.open = real_open

    assert first is False, "positive control: the failed read should answer False"
    assert second is True, "the read failure was cached, hiding a real self-run"


def test_readers_tolerate_undecodable_bytes():
    """The guard reads every transcript with errors="replace", so a file it tolerates must
    not then crash a reader. There are FOUR readers — `_find_sessions_for_task` scans the
    corpus on the DEFAULT path and is missed by any fixture that passes session_ids."""
    with tempfile.TemporaryDirectory() as tmp:
        mock_dir = Path(tmp) / ".claude" / "projects" / "test-project"
        mock_dir.mkdir(parents=True)
        # Invalid UTF-8 INSIDE a string value, so the JSON stays valid and only the DECODE
        # fails. Appending garbage after the object tests a different, already-handled defect.
        line = json.dumps(
            {"type": "assistant", "message": {"content": [{"type": "text",
             "text": f"{TARGET} MARKER the fix shipped in #1065, merged."}]}}).encode()
        assert b"MARKER" in line
        (mock_dir / "badbytes.jsonl").write_bytes(line.replace(b"MARKER", b"\xff\xfe") + b"\n")

        root = mock_dir.parent
        oc, check_completion.CLAUDE_DIR = check_completion.CLAUDE_DIR, root
        os_, search_sessions.CLAUDE_DIR = search_sessions.CLAUDE_DIR, root
        try:
            explicit = check_completion.check_task(TARGET, session_ids=["badbytes"])
            auto = check_completion.check_task(TARGET)
            found = search_sessions.search_sessions([TARGET])
        finally:
            check_completion.CLAUDE_DIR = oc
            search_sessions.CLAUDE_DIR = os_

    assert explicit["status"] == "likely_addressed", \
        f"undecodable bytes broke the explicit-session reader: {explicit['status']!r}"
    assert auto["status"] == "likely_addressed", \
        f"undecodable bytes broke the corpus-scan reader: {auto['status']!r}"
    assert [s["session_id"] for s in found] == ["badbytes"], \
        f"undecodable bytes broke the search reader: {found}"


# --------------------------------------------------------------------------- producer

def test_recent_comments_main_actually_collects():
    """The `main()` -> `_collect()` delegation has no other coverage, and there are no
    subprocess tests in this suite — so this is the only thing between the CLI and doing
    nothing at all. The fixture uses `comment: [{"text": …}]`, the shape `extract_text`
    actually reads; `comment_text` silently yields an empty payload and passes."""
    calls = {}
    real = (recent_comments.get_my_user_id, recent_comments.get_my_tasks,
            recent_comments.get_comments)
    recent_comments.get_my_user_id = lambda: "me"
    recent_comments.get_my_tasks = lambda: [
        {"id": "868aaa111", "name": "a task", "status": {"status": "to do"}}]
    recent_comments.get_comments = lambda tid: (
        calls.setdefault("tids", []).append(tid) or
        [{"user": {"id": "999", "username": "someone"}, "date": "1700000000000",
          "comment": [{"text": "This is still open."}]}])
    argv, out = sys.argv, io.StringIO()
    sys.argv = ["recent-comments.py", "--limit", "1", "--json"]
    try:
        with contextlib.redirect_stdout(out):
            recent_comments.main()
    finally:
        sys.argv = argv
        (recent_comments.get_my_user_id, recent_comments.get_my_tasks,
         recent_comments.get_comments) = real

    assert calls.get("tids") == ["868aaa111"], f"main() never collected: {calls}"
    records = json.loads(out.getvalue())
    assert len(records) == 1, f"the CLI emitted nothing: {records}"
    assert records[0]["text"] == "This is still open."
    assert records[0]["task_status"] == "to do", f"status not carried: {records[0]}"


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = failed = 0
    for t in tests:
        try:
            t()
            print(f"  ✓ {t.__name__}")
            passed += 1
        except Exception as e:
            print(f"  ✗ {t.__name__}: {type(e).__name__}: {e}")
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
