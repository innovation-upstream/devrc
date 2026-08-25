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
import io
import json
import os
import re
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
    try:
        ts.search(["x"], surface="everything")
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
    """
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


def test_an_ai_title_is_searchable():
    """🔴 RED at 324693fd for find-session.py, which never looked at `ai-title` records —
    while search-sessions.py always did. 490 of the 792 live transcripts carry one, so this
    was a 62%-of-corpus disagreement about what a session's text even is. Unified on
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


def test_the_corpus_enumerator_ledger_is_pinned_two_way():
    """STRUCTURAL LEDGER, not regression coverage. Fails when the set of files that walk
    the transcript corpus GROWS (a fourth hand-rolled walk) or SHRINKS (the shared one is
    gone). It asserts a relationship — one enumerator, N callers — which is what a guard
    on a consolidation has to pin; a test that only checked the callers still work would
    pass with the duplication restored."""
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
        if re.search(r"""glob\(\s*["'][^"']*\*\.jsonl""", src):
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
