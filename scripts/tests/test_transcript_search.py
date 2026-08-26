#!/usr/bin/env python3
"""Tests for `scripts/lib/transcript_search.py` — the ONE transcript-corpus search.

Two hand-written implementations of this walk existed side by side and shared no code:
`scripts/find-session.py` and `scripts/check-clickup-addressed/search-sessions.py`, plus
three narrower walks inside `check-completion.py`. Consolidating them surfaced a set of
disagreements; the tests below pin the resolution of each one.

🔴 WHICH TESTS ARE REGRESSION COVERAGE AND WHICH ARE NOT, and against WHICH BASE. Two
ledgers, because "watched red" is meaningless without saying red at what:

  `RED_AT_BASE`     — watched FAIL against 324693fd, the PRE-CONSOLIDATION tree. These
                      pin bugs the consolidation fixed.
  `RED_AT_448D63F5` — watched FAIL against 448d63f5, THIS BRANCH'S OWN earlier head.
                      These pin defects the consolidation itself introduced or left, found
                      by the delta re-audit: a ledger whose prose was wider than its
                      detector, a counter that counted a file it never read, and prose
                      counts that were wrong in two places at once. Red at 324693fd would
                      be the wrong claim for them — the code they guard did not exist there.

Everything not in either list is an invariant guard or a structural ledger — it pins
something the old code already satisfied, and is NOT evidence that a bug was fixed. Both
lists are asserted against the module's own test functions, so a test cannot quietly join
or leave one.
"""
import ast
import io
import json
import os
import subprocess
import sys
import time
import contextlib
import importlib.util
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPTS = REPO / "scripts"
sys.path.insert(0, str(SCRIPTS / "lib"))

import transcript_search as ts  # noqa: E402

# Watched red at 324693fd with these exact assertions; see the PR body for the matrix.
RED_AT_BASE = frozenset({
    "test_all_flag_widens_the_surface_to_tool_input_and_output",
    "test_since_names_a_local_calendar_day_not_a_utc_one",
    "test_an_ai_title_is_searchable",
    "test_a_bare_timestamp_beside_an_offset_one_does_not_lose_the_later_date",
})

# Watched red at 448d63f5 — this branch's own head before the delta-audit fixes — each
# with the mutation that produced the red named beside it:
#   ledger_is_pinned_two_way          a `scripts/sixth_walker.py` planted with
#                                     `iterdir` + `os.listdir` + `endswith(".jsonl")`
#                                     reported 31 passed at 448d63f5; it now fails with
#                                     "a NEW *.jsonl walk appeared ... [DIR-LISTING]".
#                                     Also: a second glob added to an already-listed file
#                                     (only the COUNT can see that one).
#   unreadable_transcript...          1 good + 1 unreadable transcript reported
#                                     `sessions_examined: 2` under a docstring reading
#                                     "(files actually READ)".
#   prose_names_every_other...        the module docstring said "Six other subsystems" and
#                                     named eight files; README said "Six" and named six,
#                                     omitting scripts/tmux-session-restore.py.
#   no_git_fallback_is_not_dark_code  did not exist; the fallback it covers is the branch
#                                     the SANDBOX tier takes, and nothing exercised it.
RED_AT_448D63F5 = frozenset({
    "test_the_jsonl_glob_site_ledger_is_pinned_two_way",
    "test_an_unreadable_transcript_is_counted_and_named_not_silently_dropped",
    "test_the_prose_names_every_other_production_walk",
    "test_the_no_git_fallback_enumeration_is_not_dark_code",
})


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _rec(**kw):
    return json.dumps(kw)


def _user(text, ts_iso=None, **kw):
    d = {"type": "user", "message": {"content": text}}
    if ts_iso:
        d["timestamp"] = ts_iso
    d.update(kw)
    return json.dumps(d)


def _assistant(blocks, ts_iso=None, **kw):
    d = {"type": "assistant", "message": {"content": blocks}}
    if ts_iso:
        d["timestamp"] = ts_iso
    d.update(kw)
    return json.dumps(d)


def _write(root, project, session_id, lines, subagent=False):
    """Write one transcript. `subagent=True` puts it in the tier neither tool may return."""
    if subagent:
        d = Path(root) / project / session_id / "subagents"
        d.mkdir(parents=True, exist_ok=True)
        p = d / "agent-decoy.jsonl"
    else:
        d = Path(root) / project
        d.mkdir(parents=True, exist_ok=True)
        p = d / f"{session_id}.jsonl"
    p.write_text("\n".join(lines) + "\n")
    return p


def _run_find_session(root, argv):
    """Run scripts/find-session.py against `root`, return (stdout, parsed-json-or-None).

    Deliberately drives the CLI module, not the shared library: this is the harness the
    RED_AT_BASE tests were replayed through against a tree where the library did not
    exist yet, so it has to work on both.
    """
    mod = _load(SCRIPTS / "find-session.py", "fs_under_test")
    mod.ROOT = str(root)
    buf = io.StringIO()
    old_argv = sys.argv
    sys.argv = ["find-session.py"] + list(argv)
    try:
        with contextlib.redirect_stdout(buf):
            mod.main()
    finally:
        sys.argv = old_argv
    out = buf.getvalue()
    parsed = None
    if "--json" in argv:
        try:
            parsed = json.loads(out)
        except Exception:
            parsed = None
    return out, parsed


def _ids(parsed):
    return sorted(x["session_id"] for x in (parsed or []))


# --------------------------------------------------------------- corpus enumeration

def test_subagent_transcripts_are_excluded_from_the_corpus():
    """INVARIANT GUARD — green at 324693fd too, by two different mechanisms.

    find-session.py excluded `subagents/` by name; search-sessions.py never recursed deep
    enough to see one. The consolidated walk recurses AND excludes by name, so this pins
    that the second property did not get lost when the first arrived.
    """
    with tempfile.TemporaryDirectory() as tmp:
        _write(tmp, "-proj", "sess-real", [_user("the widget is broken")])
        _write(tmp, "-proj", "sess-real", [_user("the widget is broken")], subagent=True)
        found = sorted(p.name for p in ts.iter_transcripts(tmp))
        assert found == ["sess-real.jsonl"], found


def test_the_walk_recurses_so_a_deeper_main_transcript_is_still_found():
    with tempfile.TemporaryDirectory() as tmp:
        deep = Path(tmp) / "-proj" / "nested"
        deep.mkdir(parents=True)
        (deep / "sess-deep.jsonl").write_text(_user("hello") + "\n")
        found = sorted(p.stem for p in ts.iter_transcripts(tmp))
        assert found == ["sess-deep"], found


def test_a_wf_prefixed_directory_is_excluded():
    with tempfile.TemporaryDirectory() as tmp:
        _write(tmp, "wf_run", "sess-wf", [_user("hello")])
        _write(tmp, "-proj", "sess-ok", [_user("hello")])
        assert sorted(p.stem for p in ts.iter_transcripts(tmp)) == ["sess-ok"]


def test_exclude_sessions_drops_by_id():
    with tempfile.TemporaryDirectory() as tmp:
        _write(tmp, "-proj", "keep", [_user("hello")])
        _write(tmp, "-proj", "drop", [_user("hello")])
        got = sorted(p.stem for p in ts.iter_transcripts(tmp, exclude_sessions=["drop"]))
        assert got == ["keep"], got


def test_find_transcript_and_the_enumerator_agree_on_every_id():
    """`find_transcript` is a targeted glob (the full walk cost 0.16s a call inside a
    per-task loop). It is a SECOND reader of the corpus rule, so this pins that the two
    cannot disagree: every id the enumerator yields must resolve, and an id it excludes
    must not — checked against the same tree, both directions."""
    with tempfile.TemporaryDirectory() as tmp:
        _write(tmp, "-proj", "alpha", [_user("x")])
        _write(tmp, "-other", "beta", [_user("x")])
        _write(tmp, "-proj", "alpha", [_user("x")], subagent=True)
        _write(tmp, "wf_run", "gamma", [_user("x")])
        enumerated = {p.stem for p in ts.iter_transcripts(tmp)}
        assert enumerated == {"alpha", "beta"}, enumerated
        for sid in enumerated:
            assert ts.find_transcript(sid, tmp) is not None, sid
        for sid in ("agent-decoy", "gamma", "nope"):
            assert ts.find_transcript(sid, tmp) is None, sid


def test_a_missing_root_yields_nothing_rather_than_raising():
    with tempfile.TemporaryDirectory() as tmp:
        assert list(ts.iter_transcripts(Path(tmp) / "does-not-exist")) == []


# ------------------------------------------------------------------- record parsing

def test_a_malformed_line_costs_the_line_not_the_transcript():
    """The `search-sessions.py` side of this is its own regression test; here it pins the
    shared parser directly."""
    with tempfile.TemporaryDirectory() as tmp:
        p = _write(tmp, "-proj", "sess", [
            _user("first"),
            "{not json at all",
            _user("second"),
        ])
        bodies = [ts.text_of(r.get("message", {})) for r in ts.load_records(p)]
        assert bodies == ["first", "second"], bodies


def test_the_search_surfaces_nest():
    blocks = [
        {"type": "text", "text": "prose-token"},
        {"type": "tool_use", "input": {"command": "toolinput-token"}},
        {"type": "tool_result", "content": "toolout-token"},
    ]
    msg = {"content": blocks}
    text = ts.text_of(msg, ts.SURFACE_TEXT)
    tool = ts.text_of(msg, ts.SURFACE_TOOL_USE)
    every = ts.text_of(msg, ts.SURFACE_ALL)
    assert "prose-token" in text and "toolinput-token" not in text and "toolout-token" not in text
    assert "prose-token" in tool and "toolinput-token" in tool and "toolout-token" not in tool
    assert "prose-token" in every and "toolinput-token" in every and "toolout-token" in every


def test_an_unknown_surface_is_rejected_loudly():
    # `root=` is passed even though the guard raises before the walk: without it this test
    # names the OPERATOR's real corpus, and is hermetic only by accident of the argument
    # check's position. Move the check one line down and it silently becomes a 5,000-file
    # read against live data.
    with tempfile.TemporaryDirectory() as tmp:
        try:
            ts.search(["x"], root=tmp, surface="everything")
        except ValueError as e:
            assert "everything" in str(e)
        else:
            raise AssertionError("an unknown surface was accepted")


def test_first_user_text_strips_the_wrappers_a_human_never_typed():
    msg = {"content": "<system-reminder>noise</system-reminder>what I actually typed"}
    assert ts.first_user_text(msg) == "what I actually typed"
    cmd = {"content": "<command-name>/handoff</command-name><command-args>now</command-args>"}
    assert ts.first_user_text(cmd) == "/handoff now"


# -------------------------------------------------------------------------- ranking

def test_hits_count_occurrences_not_messages():
    """find-session.py counted MESSAGES containing a term (one per message, however many
    times it appeared); search-sessions.py counted OCCURRENCES over the concatenated text.
    Unified on occurrences — the fixture is built so the two answers cannot coincide."""
    with tempfile.TemporaryDirectory() as tmp:
        _write(tmp, "-proj", "dense", [_user("tok tok tok tok tok")])   # 1 message, 5 hits
        _write(tmp, "-proj", "spread", [_user("tok"), _user("tok")])    # 2 messages, 2 hits
        hits = {r["session_id"]: r["total_hits"] for r in ts.search(["tok"], root=tmp)}
        assert hits == {"dense": 5, "spread": 2}, hits
        assert [r["session_id"] for r in ts.search(["tok"], root=tmp)][0] == "dense"


def test_more_distinct_terms_outranks_more_hits():
    with tempfile.TemporaryDirectory() as tmp:
        _write(tmp, "-proj", "both", [_user("alpha beta")])
        _write(tmp, "-proj", "loud", [_user("alpha " * 40)])
        order = [r["session_id"] for r in ts.search(["alpha", "beta"], root=tmp, match_any=True)]
        assert order == ["both", "loud"], order


def test_and_is_the_default_and_any_is_opt_in():
    with tempfile.TemporaryDirectory() as tmp:
        _write(tmp, "-proj", "one", [_user("alpha only")])
        assert ts.search(["alpha", "beta"], root=tmp) == []
        assert len(ts.search(["alpha", "beta"], root=tmp, match_any=True)) == 1


def test_snippets_carry_the_role_and_surround_the_match():
    with tempfile.TemporaryDirectory() as tmp:
        _write(tmp, "-proj", "s", [
            _user("x" * 80 + " NEEDLE " + "y" * 80),
            _assistant([{"type": "text", "text": "NEEDLE again"}]),
        ])
        r = ts.search(["NEEDLE"], root=tmp)[0]
        role, snip = r["snippets"]["NEEDLE"]
        assert role == "you", role                 # first match wins, and it was the user
        assert "NEEDLE" in snip
        assert len(snip) <= 2 * ts.SNIPPET_PAD + len("NEEDLE") + 2


def test_the_session_filter_drops_after_matching_and_reports_what_it_dropped():
    with tempfile.TemporaryDirectory() as tmp:
        _write(tmp, "-proj", "keep", [_user("tok")])
        _write(tmp, "-proj", "toss", [_user("tok")])
        _write(tmp, "-proj", "nomatch", [_user("nothing here")])
        seen = []

        def drop(path):
            seen.append(path.stem)
            return path.stem == "toss"

        stats = {}
        got = ts.search(["tok"], root=tmp, session_filter=drop, stats=stats)
        assert [r["session_id"] for r in got] == ["keep"]
        assert stats["filtered_out"] == 1 and stats["filtered_out_ids"] == ["toss"]
        # The expensive filter must not have been asked about a transcript that did not
        # match — that ordering is the measured 2.8x in search-sessions.py's docstring.
        assert "nomatch" not in seen, seen


def test_project_matches_the_cwd_as_well_as_the_directory_and_ignores_case():
    with tempfile.TemporaryDirectory() as tmp:
        _write(tmp, "-encoded-dir", "s", [
            json.dumps({"type": "user", "cwd": "/home/someone/workspace/WidgetRepo",
                        "message": {"content": "tok"}})])
        assert len(ts.search(["tok"], root=tmp, project="widgetrepo")) == 1
        assert len(ts.search(["tok"], root=tmp, project="ENCODED")) == 1
        assert len(ts.search(["tok"], root=tmp, project="unrelated")) == 0


def test_since_skips_a_stale_file_without_opening_it():
    """🔴 REACHABILITY, not just the result set. `--since` is applied twice — an mtime
    prefilter and the authoritative last-message comparison — and only the second one is
    visible in the output, so a suite that checks WHICH SESSIONS came back passes whether
    or not the prefilter exists. That is exactly how it was lost: the consolidated `search`
    read every file to EOF and then dropped it on `last_local`, a measured 5.1x on the live
    corpus (7.67s vs 1.51s for `--since 2026-08-22`).

    So this asserts the file was never SCANNED, by recording the calls, and pins the
    counter that reports it. The fresh file is in the same fixture as the positive control:
    a prefilter that skipped everything would fail on it.
    """
    with tempfile.TemporaryDirectory() as tmp:
        stale = _write(tmp, "-proj", "stale", [_user("tok")])
        fresh = _write(tmp, "-proj", "fresh", [_user("tok")])
        old = (datetime.now() - timedelta(days=10)).timestamp()
        os.utime(stale, (old, old))
        since = datetime.now() - timedelta(days=2)

        scanned = []
        real = ts.scan_transcript
        ts.scan_transcript = lambda path, *a, **kw: (scanned.append(Path(path).stem)
                                                     or real(path, *a, **kw))
        try:
            stats = {}
            got = ts.search(["tok"], root=tmp, since=since, stats=stats)
        finally:
            ts.scan_transcript = real

        assert [r["session_id"] for r in got] == ["fresh"], got
        assert scanned == ["fresh"], f"the stale transcript was read to EOF anyway: {scanned}"
        assert stats["skipped_stale"] == 1 and stats["sessions_examined"] == 1, stats
        assert fresh.exists()


def test_without_since_nothing_is_prefiltered():
    """The prefilter's own negative control: with no `since`, mtime must not gate anything.

    A prefilter written as `if mtime < (since or now)` would pass the test above and drop
    the whole corpus here.
    """
    with tempfile.TemporaryDirectory() as tmp:
        p = _write(tmp, "-proj", "ancient", [_user("tok")])
        old = (datetime.now() - timedelta(days=900)).timestamp()
        os.utime(p, (old, old))
        stats = {}
        got = ts.search(["tok"], root=tmp, stats=stats)
        assert [r["session_id"] for r in got] == ["ancient"], got
        assert stats["skipped_stale"] == 0 and stats["sessions_examined"] == 1, stats


def test_an_unreadable_transcript_is_counted_and_named_not_silently_dropped():
    """🔴 The module argues three times that a drop nobody can count is indistinguishable
    from a filter wired to nothing — and then dropped an unreadable transcript on a bare
    `except OSError: continue`, uncounted, while base find-session.py printed `ERR <path>`.

    The fixture is a DIRECTORY named `<id>.jsonl`: it matches the corpus glob and passes
    `is_corpus_member`, so the walk reaches it and `open()` raises IsADirectoryError — an
    OSError that does not depend on the test user's privileges, which a chmod-000 file
    would (root reads it anyway and the guard never runs).

    🔴 It also pins `sessions_examined`, which it did NOT, and which was wrong because of
    it. `search` incremented that counter BEFORE `scan_transcript`, so this exact fixture
    — 1 good file + 1 unreadable one — reported `sessions_examined: 2` under a docstring
    reading "(files actually READ)". Two counters that both claim the same file are not a
    decomposition of the walk, so the sum is asserted here too: a future third outcome
    that lands in neither bucket fails this line rather than going unnoticed.
    """
    with tempfile.TemporaryDirectory() as tmp:
        _write(tmp, "-proj", "good", [_user("tok")])
        (Path(tmp) / "-proj" / "broken.jsonl").mkdir(parents=True)
        stats = {}
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            got = ts.search(["tok"], root=tmp, stats=stats)
        assert [r["session_id"] for r in got] == ["good"], got
        assert stats["unreadable"] == 1, stats
        assert stats["unreadable_paths"] and "broken.jsonl" in stats["unreadable_paths"][0]
        assert "broken.jsonl" in err.getvalue(), err.getvalue()
        assert stats["sessions_examined"] == 1, (
            "sessions_examined counted the file it could not open: " + repr(stats))
        walked = len(list(ts.iter_transcripts(tmp)))
        assert walked == 2, walked
        assert (stats["sessions_examined"] + stats["skipped_stale"]
                + stats["unreadable"]) == walked, stats


def test_an_empty_term_list_is_rejected_rather_than_matching_the_whole_corpus():
    """AND over no terms is vacuously true, so `search([])` returned EVERY transcript
    ranked by nothing. Neither CLI can reach it, which is why the guard belongs here."""
    with tempfile.TemporaryDirectory() as tmp:
        _write(tmp, "-proj", "s", [_user("anything")])
        try:
            ts.search([], root=tmp)
        except ValueError as e:
            assert "at least one term" in str(e), e
        else:
            raise AssertionError("an empty term list was accepted")


# ------------------------------------------------------- the two per-call-site surfaces

def test_the_sidechain_axis_is_a_live_per_call_site_difference_in_both_directions():
    """🔴 A KNOB WITH BOTH BRANCHES UNEXERCISED IS NOT A KNOB — and this one silently
    narrowed a caller for a review round.

    Base `find-session.py` skipped `isSidechain` records; base `search-sessions.py` had no
    such filter. The consolidated `search` defaults to the NARROWER of the two, so ccua
    inherited find-session's policy by omission. Measured 2026-08-25: 0 of 424,853
    user/assistant records in the live corpus are sidechain-true, so nothing moved — but
    the key is present in 795 of 797 files, so it is a layout-dependent zero.

    The fixture hides the ONLY occurrence of the token inside a sidechain record, so the
    two answers cannot coincide, and it is asserted through the two CLIs rather than the
    library: the defect was in what a CALL SITE passes, which a library-level test of the
    parameter cannot see.
    """
    sys.path.insert(0, str(SCRIPTS / "check-clickup-addressed"))
    ss = _load(SCRIPTS / "check-clickup-addressed" / "search-sessions.py", "ss_sidechain")
    with tempfile.TemporaryDirectory() as tmp:
        _write(tmp, "-proj", "side", [_user("zzsidetoken", isSidechain=True)])
        _write(tmp, "-proj", "main", [_user("ordinary text")])

        _, fs = _run_find_session(tmp, ["--json", "zzsidetoken"])
        assert _ids(fs) == [], f"find-session must keep base behaviour and skip it: {fs}"

        ss.CLAUDE_DIR = Path(tmp)
        ccua = ss.search_sessions(["zzsidetoken"], limit=50, include_self_runs=True)
        assert [r["session_id"] for r in ccua] == ["side"], ccua

        # ...and the library default is the narrow one, which is why ccua must pass it.
        assert ts.search(["zzsidetoken"], root=tmp) == []
        assert [r["session_id"] for r in
                ts.search(["zzsidetoken"], root=tmp, include_sidechains=True)] == ["side"]


def test_ai_titles_are_searched_unconditionally_with_no_knob_to_turn_them_off():
    """The `include_titles` knob is deliberately GONE, so this pins that its removal did
    not leave the narrow branch reachable by another name. Passing it must raise rather
    than be silently ignored — a `**kwargs` signature would swallow it and re-create the
    dead configurability this removed."""
    with tempfile.TemporaryDirectory() as tmp:
        _write(tmp, "-proj", "s", [_rec(type="ai-title", aiTitle="zztitletoken here")])
        assert [r["session_id"] for r in ts.search(["zztitletoken"], root=tmp)] == ["s"]
        try:
            ts.search(["zztitletoken"], root=tmp, include_titles=False)
        except TypeError:
            pass
        else:
            raise AssertionError("include_titles was accepted; the knob is back")


# ------------------------------------------------------------ RED AT BASE (find-session)

def test_all_flag_widens_the_surface_to_tool_input_and_output():
    """🔴 RED at 324693fd. `--all` was INERT there.

    Its handler read `if not a.all and typ not in ("user", "assistant"): continue`, but an
    unconditional `if typ not in ("user", "assistant"): continue` twenty lines earlier had
    already skipped everything the flag could have admitted. The SKILL.md advertised it for
    tool output the whole time. Fixture: the ONLY occurrence of each token is in a tool_use
    input and a tool_result, so the default must find nothing and `--all` must find both.
    """
    with tempfile.TemporaryDirectory() as tmp:
        _write(tmp, "-proj", "s", [_assistant([
            {"type": "text", "text": "checking"},
            {"type": "tool_use", "input": {"command": "run zzinput"}},
            {"type": "tool_result", "content": "produced zzoutput"},
        ])])
        for token in ("zzinput", "zzoutput"):
            _, narrow = _run_find_session(tmp, ["--json", token])
            assert _ids(narrow) == [], f"{token} leaked into the default surface: {narrow}"
            _, wide = _run_find_session(tmp, ["--json", "--all", token])
            assert _ids(wide) == ["s"], f"--all did not admit {token}: {wide}"


def test_since_names_a_local_calendar_day_not_a_utc_one():
    """🔴 RED at 324693fd, and measured at TWO timezones with opposite-sign offsets.

    The old code compared `fromisoformat(ts).replace(tzinfo=None)` — a naive UTC value —
    against `--since` parsed as naive LOCAL midnight. West of UTC that admitted the whole
    previous evening; east of UTC it hid the current morning. `--since` names a local
    calendar day, so both sides are converted to local first.

    TZ is set explicitly rather than inherited: a suite that takes this dimension from the
    host is structurally blind to exactly the bug under test, and would pass vacuously at
    UTC+0.

    ⚠️ It mutates PROCESS-GLOBAL state (`os.environ["TZ"]` + `time.tzset()`), which is the
    only way to exercise this dimension in-process. Safe here because the mutation is
    confined to ONE PROCESS and the window is this function.

    🔴 This used to read "safe because the runner is single-process and sequential
    (`scripts/run-tests.sh` invokes pytest with no xdist)". That premise is FALSE as of
    the xdist change — the runner now passes `-n N --dist loadfile`. The conclusion
    still holds, but for a different reason: each xdist worker is its OWN process with
    its own `os.environ`, so a `TZ` mutation cannot reach a sibling worker, and
    `loadfile` keeps this file's tests on one worker. Do not restore the old wording.
    The restore is not merely written in a `finally` —
    it is ASSERTED afterwards, because "restored in a finally" is a claim and the next
    test that reads local time would pay for it being wrong.
    """
    entry_tzname = time.tzname
    cases = [
        # tz,              local wall time of the last message, --since,   expected
        ("America/Chicago", "2026-08-23 23:30", "2026-08-24", []),    # UTC says the 24th
        ("America/Chicago", "2026-08-24 00:30", "2026-08-24", ["s"]),
        ("Asia/Tokyo",      "2026-08-24 00:30", "2026-08-24", ["s"]),  # UTC says the 23rd
        ("Asia/Tokyo",      "2026-08-23 23:30", "2026-08-24", []),
    ]
    old_tz = os.environ.get("TZ")
    try:
        for tz_name, wall, since, expected in cases:
            os.environ["TZ"] = tz_name
            time.tzset()
            local = datetime.strptime(wall, "%Y-%m-%d %H:%M")
            as_utc = local.astimezone().astimezone(timezone.utc)
            with tempfile.TemporaryDirectory() as tmp:
                _write(tmp, "-proj", "s", [_user("tok", ts_iso=as_utc.isoformat()
                                                 .replace("+00:00", "Z"))])
                _, parsed = _run_find_session(tmp, ["--json", "--since", since, "tok"])
                assert _ids(parsed) == expected, (tz_name, wall, since, parsed)
    finally:
        if old_tz is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = old_tz
        time.tzset()
    assert time.tzname == entry_tzname, (
        f"this test left the process in {time.tzname}, not the {entry_tzname} it found — "
        f"every later test that reads local time is now measuring a different host")


def test_an_ai_title_is_searchable():
    """🔴 RED at 324693fd for find-session.py, which never looked at `ai-title` records —
    while search-sessions.py always did. 493 of the 797 live transcripts carry one
    (2026-08-25), so this was a 62%-of-corpus disagreement about what a session's text even is. Unified on
    searching it, which is what the two tools now share."""
    with tempfile.TemporaryDirectory() as tmp:
        _write(tmp, "-proj", "s", [
            _rec(type="ai-title", aiTitle="migrating the zztitle service"),
            _user("unrelated body text"),
        ])
        _, parsed = _run_find_session(tmp, ["--json", "zztitle"])
        assert _ids(parsed) == ["s"], parsed


def test_a_bare_timestamp_beside_an_offset_one_does_not_lose_the_later_date():
    """🔴 RED at 324693fd.

    One record with a tz-less timestamp is enough to make the next comparison raise
    "can't compare offset-naive and offset-aware datetimes". The old code swallowed that
    in a blanket `except Exception: pass`, which did not fix the date — it DISCARDED every
    timestamp after the mismatch, so the session was ranked and dated by whichever record
    happened to come first. The fixture puts the bare one first, so the aware one that
    follows is the comparison that would have raised, and asserts the LATER date wins.
    """
    with tempfile.TemporaryDirectory() as tmp:
        _write(tmp, "-proj", "s", [
            _user("tok", ts_iso="2026-08-20T01:00:00"),
            _user("tok", ts_iso="2026-08-21T01:00:00Z"),
        ])
        _, parsed = _run_find_session(tmp, ["--json", "tok"])
        assert _ids(parsed) == ["s"], parsed
        assert parsed[0]["last"].startswith("2026-08-21"), parsed[0]["last"]
        assert parsed[0]["first"].startswith("2026-08-20"), parsed[0]["first"]


def test_a_transcript_with_no_timestamps_falls_back_to_file_mtime_for_since():
    with tempfile.TemporaryDirectory() as tmp:
        _write(tmp, "-proj", "s", [_user("tok")])
        past = (datetime.now() - timedelta(days=2)).strftime("%Y-%m-%d")
        future = (datetime.now() + timedelta(days=2)).strftime("%Y-%m-%d")
        _, kept = _run_find_session(tmp, ["--json", "--since", past, "tok"])
        _, gone = _run_find_session(tmp, ["--json", "--since", future, "tok"])
        assert _ids(kept) == ["s"] and _ids(gone) == []


# ------------------------------------------------------------------------- the seam

def test_both_clis_return_the_same_sessions_for_the_same_corpus():
    """🔴 SEAM GUARD. Each tool was hermetically correct on its own the whole time the two
    disagreed — the defect lived in the fact that nothing ever built one corpus and asked
    both. This fixture holds a real hit, a subagent decoy carrying the same term, and a
    same-named session in an excluded `wf_` project; both CLIs must answer identically.
    """
    sys.path.insert(0, str(SCRIPTS / "check-clickup-addressed"))
    ss = _load(SCRIPTS / "check-clickup-addressed" / "search-sessions.py", "ss_seam")
    with tempfile.TemporaryDirectory() as tmp:
        _write(tmp, "-proj", "real", [_user("the zzseam term")])
        _write(tmp, "-proj", "real", [_user("the zzseam term")], subagent=True)
        _write(tmp, "wf_x", "wfsess", [_user("the zzseam term")])
        _, fs = _run_find_session(tmp, ["--json", "zzseam"])
        ss.CLAUDE_DIR = Path(tmp)
        ccua = ss.search_sessions(["zzseam"], limit=50, include_self_runs=True)
        assert _ids(fs) == ["real"], fs
        assert sorted(r["session_id"] for r in ccua) == ["real"], ccua


def test_the_consolidated_callers_reach_the_corpus_only_through_the_shared_walk():
    """STRUCTURAL LEDGER over the CONSOLIDATION — the four files it actually touched.

    Fails when one of them regrows a private walk, or when the shared one disappears. It
    asserts a relationship — one enumerator, N callers — which is what a guard on a
    consolidation has to pin; a test that only checked the callers still work would pass
    with the duplication restored.

    🔴 ITS SCOPE IS THESE FOUR FILES AND NOTHING ELSE. It cannot see a walk added
    anywhere else in the repo, and for one review round the docstring above it claimed it
    could ("a fourth hand-rolled walk fails the suite") — a fifth walker planted at
    `scripts/fifth_walker.py` was reported as 2 passed. The repo-wide half is the NEXT
    test; neither is a substitute for the other.
    """
    globbers = set()
    callers = set()
    scan = [
        SCRIPTS / "find-session.py",
        SCRIPTS / "lib" / "transcript_search.py",
        SCRIPTS / "check-clickup-addressed" / "search-sessions.py",
        SCRIPTS / "check-clickup-addressed" / "check-completion.py",
    ]
    for path in scan:
        src = path.read_text()
        if _jsonl_glob_sites(src):
            globbers.add(path.name)
        if "iter_transcripts(" in src and path.name != "transcript_search.py":
            callers.add(path.name)
    assert globbers == {"transcript_search.py"}, (
        f"transcript-corpus globbing must live in exactly one module; found {sorted(globbers)}")
    assert callers == {"check-completion.py"}, sorted(callers)
    # find-session.py and search-sessions.py reach the corpus through search(), which is
    # itself the only other caller of iter_transcripts.
    for path in (SCRIPTS / "find-session.py",
                 SCRIPTS / "check-clickup-addressed" / "search-sessions.py"):
        assert "from transcript_search import" in path.read_text(), path


# ------------------------------------------------------- the repo-wide glob-site ledger

# Directory names never descended into by the FALLBACK walk (see `_repo_python_files`:
# the primary source is `git ls-files`, and this list only matters where git cannot
# answer). `.claude` is load-bearing, not hygiene: agent worktrees are created at
# `<repo>/.claude/worktrees/agent-*`, so a scan that descends there walks every other
# branch in flight and answers about a tree nobody asked about. The build/venv names are
# here because the fallback runs in the nix sandbox, where the tree is a store copy.
_SKIP_DIRS = {".git", ".claude", ".direnv", ".pytest_cache", "__pycache__",
              "node_modules", "result", ".venv", "venv", "build", "dist",
              ".mypy_cache", ".ruff_cache", ".tox", ".eggs"}

# 🔴 EVERY `*.jsonl` WALK SITE IN THE REPO, each with the reason it is not the shared
# walk, and HOW MANY of that kind the file holds. The count is part of the pin on
# purpose: keying on `(path, kind)` alone made a SECOND walk added to an already-listed
# file invisible, which is the same "reads as coverage while providing none" defect this
# ledger exists to refuse.
#
# Kinds: an ENUMERATING entry globs a wildcard filename (it walks a corpus); a BY-ID
# entry resolves one known transcript; an OS-WALK entry walks a tree in a file that names
# `.jsonl` in code; a DIR-LISTING entry lists a directory (`iterdir`/`listdir`/`scandir`)
# in a FUNCTION that names `.jsonl` in code — the hand-rolled shape that needs no glob at
# all. An unlisted site is a violation BY DEFAULT — this is an enumeration and not a
# pattern, exactly like `drift-check.sh`'s settings.json allowlist, because the whole
# point is that a walk nobody enumerated is the one that goes unnoticed.
JSONL_GLOB_SITES = {
    ("scripts/lib/transcript_search.py", "ENUMERATING"):
        (1, "THE shared corpus walk — find-session.py + check-clickup-addressed/"),
    ("scripts/lib/transcript_search.py", "BY-ID"):
        (1, "find_transcript: the targeted by-id lookup, same is_corpus_member rule"),
    ("scripts/collector/claude/_shared.py", "ENUMERATING"):
        (1, "activity collector: deployed by nix as a COPY of scripts/collector/claude/ "
            "with no scripts/lib beside it, and takes N roots from CLAUDE_PROJECTS_DIR. "
            "Excludes by basename(dirname), which is narrower than is_corpus_member"),
    ("scripts/collector/claude/tailer.py", "ENUMERATING"):
        (1, "the collector's message-stream walk, kept byte-identical to _shared's on "
            "purpose"),
    ("scripts/collector/claude/tests/test_session_tailer.py", "ENUMERATING"):
        (1, "test: globs its own tmp fixture tree, not the corpus"),
    ("scripts/session-analysis/extract_genesis.py", "ENUMERATING"):
        (1, "session-analysis one-shot: wants EVERY jsonl including subagents"),
    ("scripts/session-analysis/extract_user_msgs.py", "ENUMERATING"):
        (1, "session-analysis one-shot: wants EVERY jsonl including subagents"),
    ("scripts/session-analysis/initiative-scan.py", "ENUMERATING"):
        (1, "initiative scan: its unit is a cwd/branch, not a rankable session"),
    ("scripts/session-analysis/recon_cost.py", "ENUMERATING"):
        (1, "recon-cost accounting: its unit is a tool call, not a session"),
    ("scripts/validation/reconcile.py", "ENUMERATING"):
        (1, "telemetry reconciler: counts files against ClickHouse rows, opens none"),
    ("scripts/tmux-session-restore.py", "ENUMERATING"):
        (1, "FLAT glob of one already-known project dir; never recurses"),
    # 🔴 REMOVED 2026-08-25 with the code it described. search-tool-nudge.py used to
    # resolve ONE agent-<id>.jsonl to infer whether the session had the Grep/Glob tools.
    # That inference is gone (the tools are absent fleet-wide and the signal only ever
    # arrived after the failed call), so the hook reads no transcript at all now and
    # imports no `glob`. This ledger is pinned BOTH ways, so leaving the entry here
    # would have failed the gate as a stale reason — which is exactly how it was caught.
    ("scripts/lib/subsystem_touch.py", "BY-ID"):
        (1, "resolves one session id; a second reader of the by-id rule, not of the corpus"),
    ("scripts/claude-hooks/tests/test_bg_command_capture.py", "OS-WALK"):
        (1, "test: walks its own tmp state dir"),
    ("scripts/claude-hooks/tests/test_bg_command_capture.py", "DIR-LISTING"):
        (1, "test: lists its own tmp state dir to assert what the hook wrote"),
    ("scripts/claude-hooks/tests/test_search_tool_nudge.py", "OS-WALK"):
        (1, "test: walks its own tmp fixture tree"),
}

_GLOB_ATTRS = {"glob", "rglob", "iglob"}
# A directory listing needs no glob at all, and listing the project dirs is how a walk
# gets written in THIS repo: `_selfrun.py`'s own docstring records its pre-consolidation
# walk as "`CLAUDE_DIR.iterdir()` then `glob("*.jsonl")`, top level only" — half of which
# is already this shape. Drop the inner glob for an `os.listdir` + `endswith(".jsonl")`
# and the glob-only detector this replaced saw nothing at all (measured: 31 passed).
_DIR_LIST_ATTRS = {"iterdir", "listdir", "scandir"}
_OS_DIR_FUNCS = {"listdir", "scandir"}          # these two only count off an `os` handle


def _string_literals(node):
    return [n.value for n in ast.walk(node)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)]


def _module_str_constants(tree):
    """`name -> [str literals]` for every assignment whose value carries string literals.

    So a pattern parked in a constant — `PAT = "**/*.jsonl"` … `Path(r).glob(PAT)` — is
    resolved back to its literal. Without it the glob call's own subtree holds no string
    at all and the walk is invisible.
    """
    out = {}
    for n in ast.walk(tree):
        if isinstance(n, ast.Assign):
            targets, value = [t for t in n.targets if isinstance(t, ast.Name)], n.value
        elif isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name):
            targets, value = [n.target], n.value
        else:
            continue
        if value is None or not targets:
            continue
        lits = _string_literals(value)
        for t in targets:
            if lits:
                out.setdefault(t.id, []).extend(lits)
    return out


def _import_bindings(tree):
    """Which LOCAL names in `src` are bound to the walk primitives, aliases included.

    `from glob import glob as gg` and `from os import walk` both leave a bare Name call
    that matches no attribute and no canonical function name; both were measured invisible
    to the attribute-only matcher this replaced.
    """
    os_names = {"os"}
    glob_fns, walk_fns, dir_fns = set(), set(), set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            for a in n.names:
                if a.name == "os" and a.asname:
                    os_names.add(a.asname)
        elif isinstance(n, ast.ImportFrom):
            for a in n.names:
                local = a.asname or a.name
                if n.module == "glob" and a.name in _GLOB_ATTRS:
                    glob_fns.add(local)
                elif n.module == "os" and a.name == "walk":
                    walk_fns.add(local)
                elif n.module == "os" and a.name in _OS_DIR_FUNCS:
                    dir_fns.add(local)
    return os_names, glob_fns, walk_fns, dir_fns


def _parents(tree):
    out = {}
    for n in ast.walk(tree):
        for c in ast.iter_child_nodes(n):
            out[id(c)] = n
    return out


def _enclosing_scope(node, parents, tree):
    cur = node
    while id(cur) in parents:
        cur = parents[id(cur)]
        if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Module)):
            return cur
    return tree


def _names_jsonl(scope, docstring_nodes, consts):
    """Does `scope` mention `.jsonl` in CODE — directly, or via a string constant?"""
    for n in ast.walk(scope):
        if isinstance(n, ast.Constant) and isinstance(n.value, str) \
                and ".jsonl" in n.value and id(n) not in docstring_nodes:
            return True
        if isinstance(n, ast.Name) and any(".jsonl" in s for s in consts.get(n.id, ())):
            return True
    return False


def _jsonl_glob_sites(src):
    """(kind, lineno) for every `.jsonl` walk site in `src` — glob, os.walk, or listing.

    Parsed with `ast`, never grepped: a DOCSTRING that quotes `glob("*.jsonl")` is not a
    walk, and three of this repo's do. `_selfrun.py` and this module's own prose would
    both be false positives under a regex, which is how a text-matching ledger ends up
    either noisy or narrowed until it sees nothing.

    🔴 The DIR-LISTING arm is scoped to the ENCLOSING FUNCTION, while OS-WALK is scoped
    to the whole module. That asymmetry is measured, not stylistic: module-scoping the
    listing arm flagged 7 file/kind groups (19 calls) across this repo, nearly all of them
    unrelated `os.listdir`s in test modules that happen to mention `.jsonl` somewhere
    else — a ledger that churns on every unrelated listing is a permanently-red gate, and
    a permanently-red gate is worse than none. Function-scoping it yields exactly 1, and
    still catches the shape that matters: a listing and its `.endswith(".jsonl")` test in
    one loop body.
    """
    # Cheap text prefilter, and it costs no coverage: EVERY arm below requires a string
    # literal containing `.jsonl` somewhere in this file — the glob arm directly, the
    # os.walk and listing arms through their scope check, and a constant resolves only
    # within its own module. A file without those seven characters cannot hold a site.
    # (`".json" + "l"` is the one this skips, and it is already declared uncovered.)
    # Measured 2026-08-25: 49 of 436 tracked .py files contain the substring, and the
    # whole scan went 6.69s -> 1.01s (this prefilter plus the lazy parent map below).
    if ".jsonl" not in src:
        return []
    try:
        tree = ast.parse(src)
    except SyntaxError:                                # pragma: no cover - defensive
        return []
    docstring_nodes = set()
    for n in ast.walk(tree):
        if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) \
                and ast.get_docstring(n, clean=False) is not None:
            docstring_nodes.add(id(n.body[0].value))
    consts = _module_str_constants(tree)
    code_jsonl = _names_jsonl(tree, docstring_nodes, consts)
    os_names, glob_fns, walk_fns, dir_fns = _import_bindings(tree)
    parents = None
    sites = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        attr = func.attr if isinstance(func, ast.Attribute) else None
        name = func.id if isinstance(func, ast.Name) else None
        recv = (func.value.id if isinstance(func, ast.Attribute)
                and isinstance(func.value, ast.Name) else None)

        is_glob = attr in _GLOB_ATTRS or (name is not None
                                          and name in (_GLOB_ATTRS | glob_fns))
        is_walk = (attr == "walk" and recv in os_names) or (name is not None
                                                            and name in walk_fns)
        is_listing = ((attr == "iterdir")
                      or (attr in _OS_DIR_FUNCS and recv in os_names)
                      or (name is not None and name in dir_fns))
        if is_glob:
            # The pattern may be assembled — os.path.join(root, "**", "*.jsonl") — or
            # parked in a constant, so every literal in the call's subtree counts, plus
            # every constant any Name in it resolves to.
            lits = list(_string_literals(node))
            for n2 in ast.walk(node):
                if isinstance(n2, ast.Name):
                    lits.extend(consts.get(n2.id, ()))
            lits = [s for s in lits if ".jsonl" in s]
            if lits:
                sites.append(("ENUMERATING" if any("*.jsonl" in s for s in lits)
                              else "BY-ID", node.lineno))
        elif is_walk and code_jsonl:
            sites.append(("OS-WALK", node.lineno))
        elif is_listing and code_jsonl:
            # Only now is the parent map worth building — it is a full extra tree walk,
            # and most files that mention `.jsonl` list no directory at all.
            if parents is None:
                parents = _parents(tree)
            if _names_jsonl(_enclosing_scope(node, parents, tree),
                            docstring_nodes, consts):
                sites.append(("DIR-LISTING", node.lineno))
    return sites


def _repo_python_files():
    """Every Python file the LEDGER is a claim about — `git ls-files`, like the other gates.

    🔴 It used to `rglob` the working tree. CLAUDE.md records that all four existing
    content gates read `git ls-files`, and this one disagreeing was a live hazard, not a
    style nit: an untracked scratch `.py` holding a `.jsonl` glob (the operator's checkout
    routinely carries some) turned this red on the dev-host tier while the sandbox tier —
    which builds from the git tree — stayed green, i.e. the two tiers answered differently
    about the same repo.

    The fallback is not decoration: `nix build .#checks…` copies the tree WITHOUT `.git`,
    so the fallback is what actually runs in the sandbox tier — and both tiers are run
    before this is claimed merge-safe, because a green here is a claim about ONE tier.
    Measured 2026-08-25 on a CLEAN checkout: the two enumerations are identical (436 files
    each, zero either-way). They are NOT identical on a DIRTY one, by design — the
    fallback also sees untracked scratch, which is why `test_the_no_git_fallback_
    enumeration_is_not_dark_code` asserts a subset and not equality. `_SKIP_DIRS` covers
    the build/cache dirs a dev-host checkout would add if git were unavailable there too.
    """
    try:
        out = subprocess.run(["git", "-C", str(REPO), "ls-files", "-z", "*.py"],
                             capture_output=True, text=True, timeout=60)
        if out.returncode == 0:
            rels = [r for r in out.stdout.split("\0") if r]
            if rels:
                return [(r, REPO / r) for r in rels if (REPO / r).is_file()]
    except (OSError, subprocess.SubprocessError):       # pragma: no cover - defensive
        pass
    found = []
    for path in sorted(REPO.rglob("*.py")):
        rel = path.relative_to(REPO)
        if _SKIP_DIRS & set(rel.parts[:-1]):
            continue
        found.append((rel.as_posix(), path))
    return found


def _scan_repo_for_jsonl_globs():
    found = {}
    for rel, path in _repo_python_files():
        for kind, lineno in _jsonl_glob_sites(path.read_text(errors="replace")):
            found.setdefault((rel, kind), []).append(lineno)
    return found


def test_the_jsonl_glob_site_ledger_is_pinned_two_way():
    """🔴 THE REPO-WIDE HALF, and the one that was MISSING while its docstring claimed it.

    The previous ledger inspected a HARDCODED FOUR-FILE LIST while asserting that "a
    fourth hand-rolled walk fails the suite". It did not: an auditor planted a fifth
    walker at `scripts/fifth_walker.py` doing `glob.glob(root + "/**/*.jsonl",
    recursive=True)` and the ledger reported 2 passed. Reading as coverage while providing
    none is worse than none — it stops anyone looking.

    So this SCANS the tree instead of naming files, and asserts the discovered set of glob
    sites equals `JSONL_GLOB_SITES` exactly. Two-way on purpose: a new walk anywhere fails
    (GROWTH), and a listed site that no longer exists fails too (SHRINK — a ledger entry
    whose reason has quietly gone stale).

    🔴 WHAT IT COVERS, EXACTLY — every spelling below was PLANTED as a tracked file and
    this test watched to go red (or, for the two null controls, to stay green); none of
    it is inferred from reading the matcher. In a `git ls-files` Python file it catches:
    `glob.glob(..., recursive=True)`,
    `Path.rglob`, `Path.glob("**/*.jsonl")`, `from glob import glob`/`iglob`,
    `import glob as g` -> `g.glob`, `import pathlib as pl` -> `pl.Path(...).glob`,
    an `os.path.join`-assembled pattern, an f-string by-id pattern, `os.walk` in a module
    that names `.jsonl` in code, and — added this round, each measured MISSED before it —
    `from glob import glob as gg`, `from os import walk`, a pattern parked in a string
    constant (`PAT = "**/*.jsonl"; Path(r).glob(PAT)`), and the whole DIRECTORY-LISTING
    family: `Path.iterdir()` / `os.listdir` / `os.scandir` / `from os import listdir` in a
    function that also names `.jsonl`.

    🔴 WHAT IT DOES NOT COVER, stated so nobody reads it wider than it is:
      - a walk in a NON-PYTHON file (shell `find -name '*.jsonl'`, a `.mjs` reader);
      - a filename built by concatenation (`".json" + "l"`) or by `str.format`/`%`, since
        no literal in the tree then contains `.jsonl`;
      - a file git does not track, or one under a `_SKIP_DIRS` directory in the fallback;
      - a DIR-LISTING whose `.jsonl` test lives in a DIFFERENT function from the listing
        (a `_is_transcript(name)` helper) — the arm is function-scoped, see
        `_jsonl_glob_sites` for the measurement that forced that;
      - a listing reached through an indirection this AST never sees — `os.fdopen`,
        `pathlib.Path.walk`, `scandir` imported from `os.path`, a `getattr` call, or a
        third-party tree-walker;
      - the corpus read by a SUBPROCESS (`subprocess.run(["find", ...])`).
    """
    found = _scan_repo_for_jsonl_globs()
    # POSITIVE CONTROL. A scan that walked nothing yields an empty dict, which compares
    # equal to an empty ledger and reads exactly like a clean run.
    assert (("scripts/lib/transcript_search.py", "ENUMERATING")) in found, (
        "the scan did not even find the shared walk — it is wired to nothing, and a "
        f"zero from it means nothing. Scanned root: {REPO}")
    assert len(found) >= 10, f"positive control: only {len(found)} glob site(s) found"

    extra = sorted(k for k in found if k not in JSONL_GLOB_SITES)
    missing = sorted(k for k in JSONL_GLOB_SITES if k not in found)
    assert not extra, (
        "a NEW *.jsonl walk appeared and is not in JSONL_GLOB_SITES:\n  "
        + "\n  ".join(f"{p} [{kind}] at line(s) {found[(p, kind)]}" for p, kind in extra)
        + "\n\nIf it should use the shared enumerator, import "
          "`transcript_search.iter_transcripts`. If it genuinely should not, add it to "
          "JSONL_GLOB_SITES with (count, reason).")
    assert not missing, (
        "these JSONL_GLOB_SITES entries no longer exist — a stale reason nobody will "
        f"notice: {missing}")

    # 🔴 THE COUNT, not just the key. Keying on (path, kind) alone let a SECOND walk
    # added to an already-listed file pass unseen — the exact "reads as coverage while
    # providing none" shape this ledger exists to refuse.
    drifted = sorted((k, JSONL_GLOB_SITES[k][0], len(found[k]))
                     for k in found if len(found[k]) != JSONL_GLOB_SITES[k][0])
    assert not drifted, (
        "a *.jsonl walk was ADDED TO or REMOVED FROM a file already in the ledger — the "
        "key was already listed, so only the count can see it:\n  "
        + "\n  ".join(f"{p} [{kind}]: ledger says {want}, tree has {got} "
                      f"at line(s) {found[(p, kind)]}"
                      for (p, kind), want, got in drifted)
        + "\n\nUpdate the count in JSONL_GLOB_SITES only after reading the new site and "
          "confirming its reason is the one already written there.")


def test_the_no_git_fallback_enumeration_is_not_dark_code():
    """🔴 THE FALLBACK IS THE PATH THE MERGE GATE ACTUALLY TAKES, so it cannot be untested.

    `nix build .#checks.x86_64-linux.pytests` copies the tree with NO `.git`, so
    `git ls-files` cannot answer there and `_repo_python_files` falls through to the
    rglob. A suite that only ever exercises the git branch on the dev host is
    structurally blind to a fallback that walks nothing — and a scan that walked nothing
    yields an empty dict, which compares equal to an empty ledger and reads as a clean run.

    It asserts a SUBSET relation, not equality, on purpose: on a dirty dev-host checkout
    the fallback legitimately sees untracked scratch that `git ls-files` does not. That
    difference is the whole reason for the switch (an untracked `.py` used to turn this
    red on one tier while the other stayed green), so pinning equality here would
    re-introduce exactly the dirty-tree dependency it removed.
    """
    tracked = {rel for rel, _ in _repo_python_files()}
    assert len(tracked) > 100, f"the git enumeration found {len(tracked)} files"

    real_run = subprocess.run

    def no_git(*a, **kw):
        raise FileNotFoundError("git unavailable — simulating the nix sandbox")

    # The patch is process-global, so the window is kept to the one call that consults
    # git and restored in a `finally`; the scan below needs no subprocess at all.
    subprocess.run = no_git
    try:
        listed = _repo_python_files()
    finally:
        subprocess.run = real_run

    fallback = {rel for rel, _ in listed}
    sites = {}
    for rel, path in listed:
        for kind, lineno in _jsonl_glob_sites(path.read_text(errors="replace")):
            sites.setdefault((rel, kind), []).append(lineno)

    assert not (tracked - fallback), (
        "the no-git fallback cannot see files git tracks — it is not a fallback, it is a "
        f"different answer: {sorted(tracked - fallback)[:10]}")
    # POSITIVE CONTROL on the fallback itself: it must reach the one walk we know exists.
    assert ("scripts/lib/transcript_search.py", "ENUMERATING") in sites, (
        "the fallback scan did not find the shared walk — a zero from it in the sandbox "
        "tier would mean nothing")
    assert len(sites) >= 10, f"fallback positive control: only {len(sites)} site(s)"


def _other_production_walks():
    """The ledger's ENUMERATING production walks, minus the shared one and minus tests.

    This is the set the prose in two places claims to name — derived from the ledger, so
    it cannot be restated wrongly.
    """
    return {p for (p, kind), _v in JSONL_GLOB_SITES.items()
            if kind == "ENUMERATING"
            and p != "scripts/lib/transcript_search.py"
            and "/tests/" not in p}


def test_the_prose_names_every_other_production_walk():
    """🔴 A COUNT IN PROSE IS A CLAIM NOBODY RE-DERIVES, and both copies of it were wrong.

    `transcript_search.py`'s module docstring opened "Six other subsystems" and then named
    EIGHT files (four subsystems). `scripts/README.md` said "Six" and listed six — omitting
    `scripts/tmux-session-restore.py`, which the module docstring named and the ledger
    carried. Neither number was load-bearing and neither was checked, so both drifted in
    different directions from the same ledger.

    So the numbers are gone and the LIST is pinned instead, two-way, in BOTH places: a
    production walk added to the ledger and not named in the prose fails here, and a file
    named in the prose that is no longer a production walk fails here too. Substring
    matching is deliberate — the prose may decorate a path with backticks or a table
    pipe, but it has to contain the path.
    """
    expected = _other_production_walks()
    assert len(expected) >= 5, f"the derivation found almost nothing: {expected}"
    sources = {
        "transcript_search.py module docstring": ts.__doc__ or "",
        "scripts/README.md": (SCRIPTS / "README.md").read_text(),
    }
    for where, text in sources.items():
        # POSITIVE CONTROL: this text must mention the shared walk, or the "no missing
        # paths" result below is a fact about an empty or wrong blob, not about the prose.
        assert "transcript_search.py" in text, (
            f"{where}: read the wrong text — it does not even name the shared module")
        missing = sorted(p for p in expected if p not in text)
        assert not missing, (
            f"{where} does not name these production walks, which the ledger carries:\n  "
            + "\n  ".join(missing)
            + "\n\nAdd them to the prose (no count — the list is the pin).")

    # And the other direction, against the module docstring's explicit indented block:
    # a path listed there that the ledger no longer holds is a stale claim.
    listed = {ln.strip() for ln in (ts.__doc__ or "").splitlines()
              if ln.startswith("    scripts/") and ln.strip().endswith(".py")}
    assert listed == expected, (
        "the module docstring's list and the ledger's production walks disagree.\n"
        f"  docstring only: {sorted(listed - expected)}\n"
        f"  ledger only:    {sorted(expected - listed)}")


def test_the_red_at_base_ledger_names_only_tests_that_exist():
    """Keeps the honesty claim in this module's docstring machine-checked: a name can
    neither be added to either ledger without a test behind it, nor can a listed test be
    renamed away from the claim that it was watched fail.

    Both ledgers, and they must stay DISJOINT: a test cannot have been watched red at
    both bases, because the 448d63f5 set guards code that did not exist at 324693fd.
    Claiming both would be claiming a measurement nobody could have taken."""
    defined = {k for k in globals() if k.startswith("test_")}
    missing = RED_AT_BASE - defined
    assert not missing, f"RED_AT_BASE names tests that do not exist: {sorted(missing)}"
    assert len(RED_AT_BASE) == 4
    missing_delta = RED_AT_448D63F5 - defined
    assert not missing_delta, (
        f"RED_AT_448D63F5 names tests that do not exist: {sorted(missing_delta)}")
    assert len(RED_AT_448D63F5) == 4
    overlap = RED_AT_BASE & RED_AT_448D63F5
    assert not overlap, f"a test claims it was watched red at BOTH bases: {sorted(overlap)}"


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted((k, v) for k, v in globals().items() if k.startswith("test_")):
        try:
            fn()
            print(f"  ok   {name}")
        except Exception as e:
            failures += 1
            print(f"  FAIL {name}: {type(e).__name__}: {e}")
    print(f"\n{'FAILED' if failures else 'PASSED'}: {failures} failure(s)")
    sys.exit(1 if failures else 0)
