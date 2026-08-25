#!/usr/bin/env python3
"""Tests for `scripts/lib/transcript_search.py` — the ONE transcript-corpus search.

Two hand-written implementations of this walk existed side by side and shared no code:
`scripts/find-session.py` and `scripts/check-clickup-addressed/search-sessions.py`, plus
three narrower walks inside `check-completion.py`. Consolidating them surfaced a set of
disagreements; the tests below pin the resolution of each one.

🔴 WHICH TESTS ARE REGRESSION COVERAGE AND WHICH ARE NOT. Every test whose name is listed
in `RED_AT_BASE` below was watched FAIL against 324693fd (the pre-consolidation tree) and
pass at HEAD. The rest are invariant guards or structural ledgers — they pin something the
old code already satisfied, and they are NOT evidence that a bug was fixed. The list is
asserted against the module's own test functions, so a test cannot quietly join or leave it.
"""
import ast
import io
import json
import os
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
    only way to exercise this dimension in-process. Safe here because the runner is
    single-process and sequential (`scripts/run-tests.sh` invokes pytest with no xdist),
    and the window is this function. The restore is not merely written in a `finally` —
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

# Directory names never descended into. `.claude` is load-bearing, not hygiene: agent
# worktrees are created at `<repo>/.claude/worktrees/agent-*`, so a scan that descends
# there walks every other branch in flight and answers about a tree nobody asked about.
_SKIP_DIRS = {".git", ".claude", ".direnv", ".pytest_cache", "__pycache__",
              "node_modules", "result"}

# 🔴 EVERY `*.jsonl` GLOB SITE IN THE REPO, each with the reason it is not the shared
# walk. An ENUMERATING entry globs a wildcard filename (it walks a corpus); a BY-ID entry
# resolves one known transcript; an OS-WALK entry walks a tree in a file that also names
# `.jsonl` in code. An unlisted site is a violation BY DEFAULT — this is an enumeration
# and not a pattern, exactly like `drift-check.sh`'s settings.json allowlist, because the
# whole point is that a walk nobody enumerated is the one that goes unnoticed.
JSONL_GLOB_SITES = {
    ("scripts/lib/transcript_search.py", "ENUMERATING"):
        "THE shared corpus walk — find-session.py + check-clickup-addressed/",
    ("scripts/lib/transcript_search.py", "BY-ID"):
        "find_transcript: the targeted by-id lookup, same is_corpus_member rule",
    ("scripts/collector/claude/_shared.py", "ENUMERATING"):
        "activity collector: deployed by nix as a COPY of scripts/collector/claude/ with "
        "no scripts/lib beside it, and takes N roots from CLAUDE_PROJECTS_DIR. Excludes "
        "by basename(dirname), which is narrower than is_corpus_member",
    ("scripts/collector/claude/tailer.py", "ENUMERATING"):
        "the collector's message-stream walk, kept byte-identical to _shared's on purpose",
    ("scripts/collector/claude/tests/test_session_tailer.py", "ENUMERATING"):
        "test: globs its own tmp fixture tree, not the corpus",
    ("scripts/session-analysis/extract_genesis.py", "ENUMERATING"):
        "session-analysis one-shot: wants EVERY jsonl including subagents",
    ("scripts/session-analysis/extract_user_msgs.py", "ENUMERATING"):
        "session-analysis one-shot: wants EVERY jsonl including subagents",
    ("scripts/session-analysis/initiative-scan.py", "ENUMERATING"):
        "initiative scan: its unit is a cwd/branch, not a rankable session",
    ("scripts/session-analysis/recon_cost.py", "ENUMERATING"):
        "recon-cost accounting: its unit is a tool call, not a session",
    ("scripts/validation/reconcile.py", "ENUMERATING"):
        "telemetry reconciler: counts files against ClickHouse rows, opens none",
    ("scripts/tmux-session-restore.py", "ENUMERATING"):
        "FLAT glob of one already-known project dir; never recurses",
    ("scripts/claude-hooks/search-tool-nudge.py", "BY-ID"):
        "resolves ONE agent-<id>.jsonl in the subagents tier the shared walk excludes",
    ("scripts/lib/subsystem_touch.py", "BY-ID"):
        "resolves one session id; a second reader of the by-id rule, not of the corpus",
    ("scripts/claude-hooks/tests/test_bg_command_capture.py", "OS-WALK"):
        "test: walks its own tmp state dir",
    ("scripts/claude-hooks/tests/test_search_tool_nudge.py", "OS-WALK"):
        "test: walks its own tmp fixture tree",
}

_GLOB_ATTRS = {"glob", "rglob", "iglob"}


def _string_literals(node):
    return [n.value for n in ast.walk(node)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)]


def _jsonl_glob_sites(src):
    """(kind, lineno) for every `.jsonl` glob / corpus-shaped os.walk in `src`.

    Parsed with `ast`, never grepped: a DOCSTRING that quotes `glob("*.jsonl")` is not a
    walk, and three of this repo's do. `_selfrun.py` and this module's own prose would
    both be false positives under a regex, which is how a text-matching ledger ends up
    either noisy or narrowed until it sees nothing.
    """
    try:
        tree = ast.parse(src)
    except SyntaxError:                                # pragma: no cover - defensive
        return []
    docstring_nodes = set()
    for n in ast.walk(tree):
        if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) \
                and ast.get_docstring(n, clean=False) is not None:
            docstring_nodes.add(id(n.body[0].value))
    code_jsonl = any(isinstance(n, ast.Constant) and isinstance(n.value, str)
                     and ".jsonl" in n.value and id(n) not in docstring_nodes
                     for n in ast.walk(tree))
    sites = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        is_glob = ((isinstance(func, ast.Attribute) and func.attr in _GLOB_ATTRS)
                   or (isinstance(func, ast.Name) and func.id in _GLOB_ATTRS))
        if is_glob:
            # The pattern may be assembled — os.path.join(root, "**", "*.jsonl") — so
            # every literal in the call's subtree counts, not just the first argument.
            lits = [s for s in _string_literals(node) if ".jsonl" in s]
            if lits:
                sites.append(("ENUMERATING" if any("*.jsonl" in s for s in lits)
                              else "BY-ID", node.lineno))
        elif (isinstance(func, ast.Attribute) and func.attr == "walk"
                and isinstance(func.value, ast.Name) and func.value.id == "os"
                and code_jsonl):
            sites.append(("OS-WALK", node.lineno))
    return sites


def _scan_repo_for_jsonl_globs():
    found = {}
    for path in sorted(REPO.rglob("*.py")):
        rel = path.relative_to(REPO)
        if _SKIP_DIRS & set(rel.parts[:-1]):
            continue
        for kind, _lineno in _jsonl_glob_sites(path.read_text(errors="replace")):
            found.setdefault((rel.as_posix(), kind), []).append(_lineno)
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

    🔴 WHAT IT DOES NOT COVER, stated so nobody reads it wider than it is: a walk written
    in a non-Python file (shell `find -name '*.jsonl'`, a `.mjs` reader), one that builds
    the string `".json" + "l"`, or one that lives under a `_SKIP_DIRS` directory.
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
          "JSONL_GLOB_SITES with the reason.")
    assert not missing, (
        "these JSONL_GLOB_SITES entries no longer exist — a stale reason nobody will "
        f"notice: {missing}")


def test_the_red_at_base_ledger_names_only_tests_that_exist():
    """Keeps the honesty claim in this module's docstring machine-checked: a name can
    neither be added to RED_AT_BASE without a test behind it, nor can a listed test be
    renamed away from the claim that it was watched fail."""
    defined = {k for k in globals() if k.startswith("test_")}
    missing = RED_AT_BASE - defined
    assert not missing, f"RED_AT_BASE names tests that do not exist: {sorted(missing)}"
    assert len(RED_AT_BASE) == 4


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
