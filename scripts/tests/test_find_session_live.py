#!/usr/bin/env python3
"""Tests for `scripts/find-session.py --live` — the live-first session lookup.

🔴 WHY THIS EXISTS. Measured on this host 2026-08-28: the transcript-archive
walk takes **30.1 s**; the live cross-host tmux scan takes **1.82 s** and its
rows already carry `task`, `label`, `hotkey`, `status`, `waiting_probable`,
`path` and `claude_session_id`. For "find that thing I lost track of, is it
still running, which window, where did it leave off", the archive is the wrong
instrument — it answers a question about the past over a corpus that cannot say
whether anything is running now. `--live` inverts the order.

🔴 EVERY TEST HERE IS HERMETIC. `RUN` — the one subprocess seam this feature
adds — is replaced wholesale, so no test spawns `session-manager`, reads a real
tmux server or touches SSH. `test_the_seam_is_actually_replaced` is the positive
control on that claim.

🔴 THE SEAM GUARDS ARE THE POINT OF THIS FILE. Both sides of this feature are
hermetically tested against their OWN fixtures, which is exactly the state
`claude/RULES.md` calls "verified in isolation is the new vacuous green": the
observed failure was a consumer assuming `detail --json` returns
`{"window": ...}` and that rows carry `window`/`cwd` (they carry
`window_index`/`path`). So this module pins the RELATIONSHIP — the argv this
file builds is parsed by `session-manager`'s OWN parser, and every row field
this file READS is checked against `session-manager`'s OWN `LEAN_ROW_FIELDS`.

🔴 RED AT BASE, AND WHAT THAT IS WORTH HERE. Every node this file held AT THE
TIME was replayed against a detached worktree at 9e452d34, and all of them went
red — but for TWO different reasons, and conflating them would overstate the
coverage:

⚠ NO COUNT HERE, and its absence is deliberate — see the note above
`R1_INVARIANT_GUARDS`. This sentence used to say "all 33 nodes"; the file
collects far more now, because four later rounds added to it. A count frozen to
a sha that the file has since outgrown reads as a live measurement, which is the
same defect the counts below it were deleted for.

  * most are red because the BEHAVIOUR did not exist (no `--live`, no live-first
    ordering, no ambiguity refusal, no LIVE/CLOSED annotation);
  * `test_json_WITHOUT_live_keeps_the_bare_ARRAY_every_caller_parses`,
    `test_the_classic_TEXT_path_is_untouched_by_this_feature`,
    `test_fmt_age_states_a_MISSING_age_rather_than_rendering_zero`,
    `test_the_seam_is_actually_replaced`,
    `test_the_fake_run_can_distinguish_a_scan_from_a_tail` and
    `test_the_exit_CODE_VOCABULARY_is_pinned_to_LITERALS` are red at base only
    because the HARNESS they use (`fs.RUN`, `fs.archive_search`, `fs.EXIT_*`,
    `fs.fmt_age`) does not exist there. The first two are BACKWARD-COMPATIBILITY
    guards — the classic paths are unchanged and these pin that — and the rest
    are controls on this module's own instrument. None of them is evidence that
    a bug was fixed, and none is counted as such.

Fixtures are synthetic: this repo is PUBLIC and no captured transcript text,
real media path or third-party hostname may appear here.
"""
from __future__ import annotations

import argparse
import ast
import importlib.machinery
import importlib.util
import inspect
import io
import json
import os
import sys
import textwrap
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPTS = REPO / "scripts"


def _load(path, name):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_file_location(name, str(path), loader=loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


fs = _load(SCRIPTS / "find-session.py", "fs_live_under_test")
sm = _load(SCRIPTS / "session-manager", "sm_for_seam_check")


# =========================================================================== #
# FIXTURES — synthetic, and pairwise distinct in every field an assertion names
# =========================================================================== #
# 🔴 THE TWO ROWS DIFFER IN THE HOTKEY *CASE*, deliberately. `M-v` opens
# scratch3/violet and `M-V` opens scratch4/Vapor — DIFFERENT sessions. A fixture
# with one case could not tell `hotkey_display` from a function that shifts
# unconditionally.
ROW_VIOLET = {
    "kind": "tmux", "host": "workbench", "session": "scratch3",
    "window_index": "2", "label": "violet", "label_source": "codename",
    "hotkey": "v", "hotkey_display": "Alt+v",
    "path": "/home/zach/workspace/zzsigma",
    "task": "refactor the zzkiwi cache",
    "runtime": "claude", "claude": True, "busy": True, "status": "busy",
    "age_secs": 240.0, "age_source": "ledger",
    "waiting_probable": False, "waiting_signals": [], "waiting_status": "ok",
    "unsent_prompt": None, "unsent_prompt_status": "ok",
    "claude_session_id": "aaaaaaaa-1111-4222-8333-444444444444",
}
ROW_VAPOR = {
    "kind": "tmux", "host": "laptop", "session": "scratch4",
    "window_index": "5", "label": "Vapor", "label_source": "codename",
    "hotkey": "V", "hotkey_display": "Alt+Shift+V",
    "path": "/home/zach/workspace/zztheta",
    "task": "zzkiwi migration follow-up",
    "runtime": "opencode", "claude": True, "busy": False, "status": "stale",
    "age_secs": 7200.0, "age_source": "ledger",
    "waiting_probable": True, "waiting_signals": ["a_question_was_asked"],
    "waiting_status": "ok",
    "unsent_prompt": None, "unsent_prompt_status": "ok",
    "claude_session_id": "bbbbbbbb-2222-4333-8444-555555555555",
}
# A live row that no `--match` term selects — it exists so the UNFILTERED second
# scan can hold a session id the FILTERED one does not.
ROW_ORCHID = {
    "kind": "tmux", "host": "workbench", "session": "scratch8",
    "window_index": "1", "label": "Orchid", "label_source": "codename",
    "hotkey": "O", "hotkey_display": "Alt+Shift+O",
    "path": "/home/zach/workspace/zzomega",
    "task": "zzunrelated bookkeeping",
    "runtime": "claude", "claude": True, "busy": False, "status": "idle",
    "age_secs": 60.0, "age_source": "ledger",
    "waiting_probable": False, "waiting_signals": [], "waiting_status": "ok",
    "unsent_prompt": None, "unsent_prompt_status": "ok",
    "claude_session_id": "cccccccc-3333-4444-8555-666666666666",
}

DEFAULT_MATCH_FIELDS = ["task", "label", "codename"]


def live_report(rows, reachable=("workbench", "laptop"), unreachable=(),
                match_fields=None):
    """A `session-manager --json --lean` payload, narrowed to what this consumer
    reads. Hosts keep their reachability facts, which is the whole reason the
    unreachable case is distinguishable at all."""
    hosts = {}
    for h in reachable:
        hosts[h] = {"reachable": True, "error": None, "windows_measured": True,
                    "windows": [r for r in rows if r["host"] == h]}
    for h in unreachable:
        hosts[h] = {"reachable": False, "error": "ssh: no route to host",
                    "windows_measured": False, "windows": []}
    return {
        "view": "lean",
        "hosts": hosts,
        "filters": {"match": None if match_fields is None else ["zzkiwi"],
                    "match_fields": match_fields},
        "summary": {"total_sessions": sum(len(h["windows"]) for h in hosts.values())},
    }


def archive_hit(session_id, project="zzsigma", term="zzkiwi"):
    """One transcript-search result, in the shape `render()` and
    `render_archive_hit()` consume. Synthetic throughout."""
    return {
        "session_id": session_id,
        "cwd": f"/home/zach/workspace/{project}",
        "project_dir": f"-home-zach-workspace-{project}",
        "branch": "feat/zzbranch",
        "first": "2026-08-20T09:00:00",
        "last": "2026-08-20T17:30:00",
        "genesis": "a synthetic opening line",
        "matched_terms": [term],
        "total_hits": 4,
        "snippets": {term: ("user", "a synthetic matching snippet")},
        "path": f"/home/zach/.claude/projects/-x/{session_id}.jsonl",
        "last_local": 0,
    }


def make_run(by_terms=None, tail=(0, "synthetic scrollback line\n", ""),
             boom=None, raw=None):
    """A fake `RUN`. It answers a SCAN by the `--match` terms it was given —
    which is exactly the contract this consumer depends on: the MATCH PREDICATE
    lives on the other side of the subprocess, not here.

    `by_terms` maps a tuple of terms to `(rc, report)`. `raw` overrides the whole
    stdout (for the "produced no JSON" path); `boom` makes the call raise.
    """
    by_terms = by_terms or {}
    calls = []

    def run(argv, timeout=None):
        calls.append(list(argv))
        if boom is not None:
            raise boom
        if "tail" in argv:
            return tail
        if raw is not None:
            return raw
        terms = tuple(argv[i + 1] for i, a in enumerate(argv) if a == "--match")
        rc, report = by_terms[terms]
        return rc, json.dumps(report), ""

    run.calls = calls
    return run


def run_main(monkeypatch, argv, run, archive=None):
    """Drive `main()` with the seam replaced, capturing stdout+stderr.

    `archive` replaces `archive_search` — the 30 s walk is not what this module
    is testing, and a test that paid for it could not observe whether it RAN.
    """
    monkeypatch.setattr(fs, "RUN", run)
    seen = {"archive_calls": 0}

    def fake_archive(a, since):
        seen["archive_calls"] += 1
        return list(archive or [])

    monkeypatch.setattr(fs, "archive_search", fake_archive)
    out, err = io.StringIO(), io.StringIO()
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", err)
    rc = fs.main(list(argv))
    seen["rc"] = rc
    seen["out"] = out.getvalue()
    seen["err"] = err.getvalue()
    seen["calls"] = run.calls
    return seen


def scan_calls(calls):
    return [c for c in calls if "scan" in c]


def tail_calls(calls):
    return [c for c in calls if "tail" in c]


# =========================================================================== #
# THE HARNESS ITSELF — validated before any verdict is read off it
# =========================================================================== #
def test_the_seam_is_actually_replaced(monkeypatch):
    """🔴 NEGATIVE CONTROL ON HERMETICITY. If `RUN` were not the only edge, a
    test could quietly spawn a real `session-manager` against the operator's
    live fleet and grade whatever it found."""
    def forbidden(argv, timeout=None):
        raise AssertionError(f"a real subprocess was attempted: {argv!r}")

    monkeypatch.setattr(fs, "RUN", forbidden)
    res = fs.live_scan(["zzkiwi"])
    assert res["status"] == "error"
    assert "AssertionError" in res["error"]


def test_the_exit_CODE_VOCABULARY_is_pinned_to_LITERALS():
    """🔴 FOUND BY THE MUTATION SWEEP, AND IT IS THE CLASSIC SHAPE.

    Every `rc` assertion in this file compares against `fs.EXIT_*`, which is
    self-satisfying: a mutant that rewrites the CONSTANT moves both sides of the
    comparison and survives. Measured — `EXIT_AMBIGUOUS = 3` -> `0` was the
    battery's POSITIVE CONTROL and it SURVIVED a fully green suite, which is
    exactly `claude/RULES.md`'s "a fixture that can only ever produce the
    constant's own value cannot see a mutant that hardcodes the literal".

    So the literals are pinned here, once, and they are the SIBLING's vocabulary
    — a caller reading an rc off either tool must not have to learn two tables.
    """
    assert (fs.EXIT_OK, fs.EXIT_USAGE, fs.EXIT_AMBIGUOUS,
            fs.EXIT_UNAVAILABLE) == (0, 2, 3, 4)
    # ...and they ARE `session-manager`'s, read from it rather than restated.
    assert fs.EXIT_OK == sm.EXIT_OK
    assert fs.EXIT_USAGE == sm.EXIT_USAGE
    assert fs.EXIT_AMBIGUOUS == sm.EXIT_EMPTY
    assert fs.EXIT_UNAVAILABLE == sm.EXIT_UNAVAILABLE
    # Collapsing any two would make a refusal indistinguishable from a success.
    assert len({fs.EXIT_OK, fs.EXIT_USAGE, fs.EXIT_AMBIGUOUS,
                fs.EXIT_UNAVAILABLE}) == 4


def test_the_fake_run_can_distinguish_a_scan_from_a_tail():
    """POSITIVE CONTROL on the fixture. A harness that cannot tell the two
    subprocesses apart cannot pin which one a fact came from, however many
    tests are green."""
    run = make_run({("zzkiwi",): (0, live_report([ROW_VIOLET]))},
                   tail=(0, "TAILTEXT\n", ""))
    rc, out, _ = run(fs.live_scan_argv(["zzkiwi"]))
    assert rc == 0 and json.loads(out)["view"] == "lean"
    assert run(fs.live_tail_argv(ROW_VIOLET, 20))[1] == "TAILTEXT\n"


# =========================================================================== #
# 🔴 SEAM GUARDS — the defect lives BETWEEN the two hermetically-tested halves
# =========================================================================== #
def test_the_live_scan_ARGV_is_one_SESSION_MANAGER_ACTUALLY_ACCEPTS():
    """🔴 Parsed by `session-manager`'s OWN parser, not by a copy of its flags.

    The observed run failed on exactly this class: it assumed a shape the other
    side does not produce. A structural check here is what stops this file
    building an argv that argparse rejects (or, worse, silently ignores).
    """
    argv = fs.live_scan_argv(["zzkiwi", "zzsigma"])
    assert argv[0] == sys.executable
    assert argv[1] == str(SCRIPTS / "session-manager")
    ns = sm.build_parser().parse_args(argv[2:])
    assert ns.subcommand == "scan"
    assert ns.json is True and ns.lean is True and ns.no_ch is True
    assert ns.match == ["zzkiwi", "zzsigma"]
    # ...and an unfiltered scan asks for NO filter at all, rather than an empty
    # one — `--match ''` would be a filter that rejects the world.
    assert sm.build_parser().parse_args(fs.live_scan_argv()[2:]).match is None


def test_the_tail_ARGV_names_the_ROWS_HOST_and_is_one_the_parser_accepts():
    """🔴 `session-manager tail` resolves `--host all` to the LOCAL host, so a
    tail of a laptop row without an explicit `--host` searches the workbench and
    reports the window missing — a not-found that is really a wrong-machine."""
    argv = fs.live_tail_argv(ROW_VAPOR, 40)
    ns = sm.build_parser().parse_args(argv[2:])
    assert ns.subcommand == "tail"
    assert ns.target == "scratch4:5"
    assert ns.host == "laptop"
    assert ns.plain is True
    assert ns.lines == 40


# The row fields the LIVE renderer reads. Pinned TWO WAY below: against what the
# source actually reads, and against what `session-manager` actually emits.
LIVE_ROW_FIELDS_READ = frozenset({
    "host", "session", "window_index", "label", "hotkey_display", "status",
    "age_secs", "age_source", "waiting_probable", "waiting_status",
    "waiting_signals", "task", "path", "claude_session_id", "runtime",
})

_LIVE_ROW_READERS = ("render_live", "live_resume_command", "live_tail_argv",
                     "_tail_outcome")


def _row_gets(*fns):
    """Every `<row>.get("field")` literal in `fns`, read by `ast`.

    These four functions consume a ROW and nothing else through `.get` — the
    scan-result dict is read by subscript throughout, deliberately, so this
    sweep cannot pick up a non-row key. A regex would; an AST walk over the
    parsed source is the version that cannot be fooled by a key name appearing
    in a comment.
    """
    found = set()
    for fn in fns:
        tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "get"
                    and node.args
                    and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str)):
                found.add(node.args[0].value)
    return found


def test_the_live_row_field_ledger_is_pinned_TWO_WAY_against_what_is_READ():
    """🔴 A LEDGER OF A SET ANOTHER FUNCTION OWNS, checked both directions.

    Reading a NEW row field without adding it here would leave the
    cross-side check below blind to it — a guard whose description claims
    coverage while its body covers one side.
    """
    read = _row_gets(*(getattr(fs, n) for n in _LIVE_ROW_READERS))
    assert read, "the AST sweep found NO row field — the gate is wired to nothing"
    assert read == set(LIVE_ROW_FIELDS_READ), (
        "the ledger disagrees with what the live renderer actually reads:\n"
        f"  only read  : {sorted(read - set(LIVE_ROW_FIELDS_READ))}\n"
        f"  only listed: {sorted(set(LIVE_ROW_FIELDS_READ) - read)}")


def test_every_live_row_field_READ_is_one_SESSION_MANAGER_ACTUALLY_EMITS():
    """🔴 THE SEAM, as a RELATIONSHIP rather than two components.

    The observed run assumed rows carried `window` and `cwd`; they carry
    `window_index` and `path`. `--lean` is what this consumer asks for, so
    `LEAN_ROW_FIELDS` is the authoritative set — a field dropped from that view
    would make this renderer print `None` forever with nothing saying why.
    """
    missing = sorted(f for f in LIVE_ROW_FIELDS_READ
                     if f not in sm.LEAN_ROW_FIELDS)
    assert not missing, (
        f"the LIVE renderer reads row fields the lean view does not carry: "
        f"{missing}. Either add them to `LEAN_ROW_FIELDS` in "
        f"`scripts/session-manager`, or stop reading them here — a `None` from "
        "a field the view omitted is indistinguishable from a measured null.")
    # POSITIVE CONTROL: prove this comparison can go RED, so the clean verdict
    # above is not a fact about an empty loop.
    assert "window" not in sm.LEAN_ROW_FIELDS, (
        "positive control broken: `window` was supposed to be a name the lean "
        "view does NOT carry")


# =========================================================================== #
# LIVE FIRST — and the archive only when it has to run
# =========================================================================== #
def test_a_live_match_SKIPS_the_30_second_archive(monkeypatch):
    """🔴 THE WHOLE POINT. 1.8 s answered the question; 30.1 s must not be paid
    on top of it."""
    run = make_run({("zzkiwi",): (0, live_report([ROW_VIOLET, ROW_VAPOR],
                                                 match_fields=DEFAULT_MATCH_FIELDS))})
    got = run_main(monkeypatch, ["zzkiwi", "--live"], run,
                   archive=[archive_hit("aaaaaaaa-1111-4222-8333-444444444444")])
    assert got["archive_calls"] == 0, "the archive walk ran despite a live hit"
    assert got["rc"] == fs.EXIT_OK
    assert "LIVE (2 matched" in got["out"]
    assert "ARCHIVE: skipped" in got["out"]
    # ...and exactly ONE subprocess: the unfiltered second scan is only paid for
    # on the archive path.
    assert len(scan_calls(got["calls"])) == 1


def test_no_live_match_FALLS_BACK_to_the_archive(monkeypatch):
    run = make_run({("zznothing",): (3, live_report([], match_fields=DEFAULT_MATCH_FIELDS)),
                    (): (0, live_report([ROW_VIOLET, ROW_ORCHID]))})
    got = run_main(monkeypatch, ["zznothing", "--live"], run,
                   archive=[archive_hit("dddddddd-4444-4555-8666-777777777777")])
    assert got["archive_calls"] == 1
    assert "no live window matched" in got["out"]
    assert "ran because: no live match" in got["out"]


def test_deep_runs_BOTH_even_when_the_live_fleet_answered(monkeypatch):
    run = make_run({("zzkiwi",): (0, live_report([ROW_VIOLET],
                                                 match_fields=DEFAULT_MATCH_FIELDS)),
                    (): (0, live_report([ROW_VIOLET, ROW_ORCHID]))})
    got = run_main(monkeypatch, ["zzkiwi", "--live", "--deep"], run,
                   archive=[archive_hit("aaaaaaaa-1111-4222-8333-444444444444")])
    assert got["archive_calls"] == 1
    assert "ran because: --deep" in got["out"]


def test_the_UNFILTERED_second_scan_is_what_the_annotation_joins_on(monkeypatch):
    """🔴 THE FILTERED SCAN CANNOT ANNOTATE. It was NARROWED by the terms, so a
    session that IS live but whose window title no longer says those words is
    absent from it — labelling its archive hit CLOSED off that set would state a
    measured absence about a window the FILTER removed.

    `ROW_ORCHID` is exactly that window: live, and matched by no term.
    """
    run = make_run({("zznothing",): (3, live_report([], match_fields=DEFAULT_MATCH_FIELDS)),
                    (): (0, live_report([ROW_VIOLET, ROW_ORCHID]))})
    got = run_main(monkeypatch, ["zznothing", "--live"], run, archive=[
        archive_hit(ROW_ORCHID["claude_session_id"]),
        archive_hit("dddddddd-4444-4555-8666-777777777777"),
    ])
    # the FILTERED scan matched nothing, yet Orchid is correctly LIVE
    assert "<LIVE>" in got["out"]
    assert "<CLOSED>" in got["out"]
    # ...and it took a second, term-free scan to know that
    unfiltered = [c for c in scan_calls(got["calls"]) if "--match" not in c]
    assert len(unfiltered) == 1


def test_archive_hits_are_annotated_LIVE_or_CLOSED_by_session_id(monkeypatch):
    run = make_run({("zznothing",): (3, live_report([], match_fields=DEFAULT_MATCH_FIELDS)),
                    (): (0, live_report([ROW_VIOLET]))})
    got = run_main(monkeypatch, ["zznothing", "--live", "--json"], run, archive=[
        archive_hit(ROW_VIOLET["claude_session_id"]),
        archive_hit("dddddddd-4444-4555-8666-777777777777"),
    ])
    blob = json.loads(got["out"])
    states = [r["live_state"] for r in blob["archive"]["results"]]
    assert states == ["LIVE", "CLOSED"]
    assert blob["archive"]["live_ids_measured"] is True


def test_the_annotation_is_UNMEASURED_not_CLOSED_when_the_live_scan_FAILED(
        monkeypatch):
    """🔴 The claim "that session is closed" requires a look that happened. A
    failed scan must not launder into a measured absence."""
    run = make_run(boom=OSError("session-manager is not on this host"))
    got = run_main(monkeypatch, ["zzkiwi", "--live", "--json"], run,
                   archive=[archive_hit(ROW_VIOLET["claude_session_id"])])
    blob = json.loads(got["out"])
    assert blob["live"]["status"] == "error"
    assert blob["archive"]["live_ids_measured"] is False
    assert [r["live_state"] for r in blob["archive"]["results"]] == ["UNMEASURED"]
    assert fs.live_state_of("anything", None) == "UNMEASURED"
    # ...and the measured counterpart, so the None branch is not the only one
    # ever exercised.
    assert fs.live_state_of("anything", set()) == "CLOSED"
    assert fs.live_state_of("x", {"x"}) == "LIVE"


# =========================================================================== #
# THE LIVE SECTION — what it says, and what it must never say
# =========================================================================== #
def test_the_LIVE_section_carries_the_CHORD_and_never_derives_it(monkeypatch):
    """🔴 THE SECOND DEFECT. The answer that sent the operator to the wrong
    window derived `Alt+Shift+V` from `hotkey: v`. The chord is READ from
    `hotkey_display` here, and both cases appear in one render so a renderer
    that shifts unconditionally fails."""
    run = make_run({("zzkiwi",): (0, live_report([ROW_VIOLET, ROW_VAPOR],
                                                 match_fields=DEFAULT_MATCH_FIELDS))})
    out = run_main(monkeypatch, ["zzkiwi", "--live"], run)["out"]
    assert "[Alt+v]" in out
    assert "[Alt+Shift+V]" in out
    assert "[Alt+Shift+v]" not in out, (
        "the renderer shifted a lower-case key — `M-v` and `M-V` are different "
        "sessions")


def test_the_LIVE_section_answers_the_whole_question_in_one_call(monkeypatch):
    """Host, address, label+chord, status/age, the waiting pair with its
    VERBATIM signal list, the task, the path, and how to get back in."""
    run = make_run({("zzkiwi",): (0, live_report([ROW_VAPOR],
                                                 match_fields=DEFAULT_MATCH_FIELDS))})
    out = run_main(monkeypatch, ["zzkiwi", "--live"], run)["out"]
    assert "laptop  scratch4:5" in out
    assert "Vapor [Alt+Shift+V]" in out
    assert "stale · 2h00m" in out
    assert "waiting_probable: true" in out
    assert '"a_question_was_asked"' in out
    assert "task: zzkiwi migration follow-up" in out
    assert "path: /home/zach/workspace/zztheta" in out
    # an opencode row resumes with opencode, never `claude --resume`
    assert f"resume: opencode --session {ROW_VAPOR['claude_session_id']}" in out
    assert "claude --resume" not in out
    # ...and the mirror image, so the branch is not hardcoded either way
    out2 = run_main(monkeypatch, ["zzkiwi", "--live"],
                    make_run({("zzkiwi",): (0, live_report([ROW_VIOLET]))}))["out"]
    assert f"resume: claude --resume {ROW_VIOLET['claude_session_id']}" in out2


def test_a_NULL_waiting_probable_renders_as_null_not_false(monkeypatch):
    """🔴 The tri-state survives the render. `waiting_probable: null` means the
    pane was never scraped; printing `false` would answer "is anything waiting
    on you" off a look that never happened."""
    row = dict(ROW_VIOLET, waiting_probable=None, waiting_signals=None,
               waiting_status="uncaptured")
    run = make_run({("zzkiwi",): (0, live_report([row]))})
    out = run_main(monkeypatch, ["zzkiwi", "--live"], run)["out"]
    assert "waiting_probable: null" in out
    assert "[uncaptured]" in out
    assert "waiting_signals:  null" in out


def test_a_row_with_NO_session_id_offers_an_ATTACH_rather_than_a_fake_resume(
        monkeypatch):
    row = dict(ROW_VIOLET, claude_session_id=None)
    run = make_run({("zzkiwi",): (0, live_report([row]))})
    out = run_main(monkeypatch, ["zzkiwi", "--live"], run)["out"]
    assert "no agent session id on this row" in out
    assert "tmux attach -t scratch3:2" in out
    assert fs.live_resume_command(row) is None


# =========================================================================== #
# 🔴 UNREACHABLE IS NEVER "NOT RUNNING"
# =========================================================================== #
def test_an_UNREACHABLE_PEER_is_loud_and_the_absence_is_labelled(monkeypatch):
    run = make_run({("zzkiwi",): (0, live_report([ROW_VIOLET],
                                                 reachable=("workbench",),
                                                 unreachable=("laptop",),
                                                 match_fields=DEFAULT_MATCH_FIELDS))})
    out = run_main(monkeypatch, ["zzkiwi", "--live"], run)["out"]
    assert "NOT searched: laptop" in out
    assert "did not answer" in out
    assert "not a measured absence on that host" in out


def test_NO_HOST_ANSWERING_is_UNMEASURED_not_an_empty_fleet(monkeypatch):
    """🔴 The one sentence this tool must never emit is "it is not running" off
    a look that never happened."""
    run = make_run({("zzkiwi",): (4, live_report([], reachable=(),
                                                 unreachable=("workbench", "laptop")))})
    got = run_main(monkeypatch, ["zzkiwi", "--live"], run)
    assert "LIVE: NO HOST ANSWERED" in got["out"]
    assert "UNMEASURED, not 'nothing is running'" in got["out"]
    # and it FELL BACK, because "we could not look" must not become "we looked"
    assert got["archive_calls"] == 1
    assert "the live scan was UNMEASURED" in got["out"]


@pytest.mark.parametrize("kw,needle", [
    (dict(boom=OSError("no such file")), "OSError"),
    (dict(raw=(2, "", "session-manager: unrecognized arguments")), "no JSON"),
    (dict(raw=(0, "not json at all", "")), "no JSON"),
])
def test_a_BROKEN_live_scan_is_a_FAILED_scan_not_an_empty_one(monkeypatch, kw,
                                                              needle):
    got = run_main(monkeypatch, ["zzkiwi", "--live"], make_run(**kw))
    assert "LIVE: SCAN FAILED" in got["out"]
    assert needle in got["out"]
    assert "NOT 'nothing is running'" in got["out"]
    assert got["archive_calls"] == 1


def test_live_scan_status_discriminates_all_three_outcomes(monkeypatch):
    """The three statuses must be mutually distinguishable — an `ok` with zero
    rows is the only one that means "we looked and found nothing"."""
    monkeypatch.setattr(fs, "RUN", make_run({("t",): (3, live_report([]))}))
    assert fs.live_scan(["t"])["status"] == "ok"
    monkeypatch.setattr(fs, "RUN", make_run(
        {("t",): (4, live_report([], reachable=(), unreachable=("workbench",)))}))
    assert fs.live_scan(["t"])["status"] == "unavailable"
    monkeypatch.setattr(fs, "RUN", make_run(boom=RuntimeError("x")))
    assert fs.live_scan(["t"])["status"] == "error"
    # ...and only `ok` yields a usable id set
    monkeypatch.setattr(fs, "RUN", make_run({(): (0, live_report([ROW_VIOLET]))}))
    assert fs.live_session_ids(fs.live_scan()) == {ROW_VIOLET["claude_session_id"]}


# =========================================================================== #
# --tail — ambiguity is REFUSED, not guessed
# =========================================================================== #
def test_tail_of_a_SINGLE_match_shells_out_with_that_ROWS_HOST(monkeypatch):
    run = make_run({("zzkiwi",): (0, live_report([ROW_VAPOR],
                                                 match_fields=DEFAULT_MATCH_FIELDS))},
                   tail=(0, "synthetic last lines\n", ""))
    got = run_main(monkeypatch, ["zzkiwi", "--live", "--tail", "40"], run)
    assert got["rc"] == fs.EXIT_OK
    calls = tail_calls(got["calls"])
    assert len(calls) == 1
    assert calls[0][2:] == ["tail", "scratch4:5", "--host", "laptop",
                            "--plain", "--lines", "40"]
    assert "TAIL laptop scratch4:5 (last 40 lines)" in got["out"]
    assert "synthetic last lines" in got["out"]


def test_tail_REFUSES_an_AMBIGUOUS_match_and_lists_the_candidates(monkeypatch):
    """🔴 `window-triage` §7: ambiguity is refused, not guessed. A scrollback
    printed from the wrong window is an answer that READS as correct, which is
    strictly worse than no answer."""
    run = make_run({("zzkiwi",): (0, live_report([ROW_VIOLET, ROW_VAPOR],
                                                 match_fields=DEFAULT_MATCH_FIELDS))})
    got = run_main(monkeypatch, ["zzkiwi", "--live", "--tail", "40"], run)
    assert got["rc"] == fs.EXIT_AMBIGUOUS
    assert tail_calls(got["calls"]) == [], "it tailed a window it had not resolved"
    assert "TAIL: REFUSED — 2 live windows matched" in got["out"]
    # both candidates are named, with a command that resolves each one
    assert "tail scratch3:2 --host workbench" in got["out"]
    assert "tail scratch4:5 --host laptop" in got["out"]


def test_tail_with_NO_live_match_refuses_rather_than_tailing_nothing(monkeypatch):
    run = make_run({("zznothing",): (3, live_report([], match_fields=DEFAULT_MATCH_FIELDS)),
                    (): (0, live_report([]))})
    got = run_main(monkeypatch, ["zznothing", "--live", "--tail", "40"], run)
    assert got["rc"] == fs.EXIT_AMBIGUOUS
    assert "no live window matched" in got["out"]
    assert tail_calls(got["calls"]) == []


def test_tail_over_an_UNMEASURED_fleet_is_UNAVAILABLE_not_ambiguous(monkeypatch):
    """A refusal because nothing was measured is a different fact from a refusal
    because two windows matched, and the exit codes say so."""
    run = make_run({("zzkiwi",): (4, live_report([], reachable=(),
                                                 unreachable=("workbench", "laptop"))),
                    (): (4, live_report([], reachable=(),
                                        unreachable=("workbench", "laptop")))})
    got = run_main(monkeypatch, ["zzkiwi", "--live", "--tail", "40"], run)
    assert got["rc"] == fs.EXIT_UNAVAILABLE
    assert "TAIL: REFUSED — the live fleet was not measured" in got["out"]


def test_tail_without_live_is_a_USAGE_error(monkeypatch):
    got = run_main(monkeypatch, ["zzkiwi", "--tail", "40"], make_run())
    assert got["rc"] == fs.EXIT_USAGE
    assert "--tail requires --live" in got["err"]
    assert got["calls"] == []
    assert got["archive_calls"] == 0


# =========================================================================== #
# --json composition, and the shape existing callers already parse
# =========================================================================== #
def test_live_composes_with_json_and_carries_every_discriminant(monkeypatch):
    run = make_run({("zzkiwi",): (0, live_report([ROW_VIOLET],
                                                 reachable=("workbench",),
                                                 unreachable=("laptop",),
                                                 match_fields=DEFAULT_MATCH_FIELDS))},
                   tail=(0, "synthetic tail\n", ""))
    got = run_main(monkeypatch, ["zzkiwi", "--live", "--json", "--tail", "12"], run)
    blob = json.loads(got["out"])
    assert set(blob) == {"live", "archive", "tail"}
    assert blob["live"]["status"] == "ok"
    assert blob["live"]["hosts_reachable"] == ["workbench"]
    assert blob["live"]["hosts_unreachable"] == ["laptop"]
    assert blob["live"]["match_fields"] == DEFAULT_MATCH_FIELDS
    assert blob["live"]["terms"] == ["zzkiwi"]
    assert [r["session"] for r in blob["live"]["rows"]] == ["scratch3"]
    assert blob["archive"]["ran"] is False
    assert blob["tail"]["refused"] is False
    assert blob["tail"]["resolved"] == {"host": "workbench",
                                        "target": "scratch3:2"}
    assert blob["tail"]["text"] == "synthetic tail\n"
    assert blob["tail"]["requested_lines"] == 12


def test_json_WITHOUT_live_keeps_the_bare_ARRAY_every_caller_parses(monkeypatch):
    """INVARIANT GUARD, and a backward-compatibility one: `--live` is opt-in
    precisely so no existing caller's output moves. Widening the array's
    elements instead would have changed a shape nobody asked to change."""
    got = run_main(monkeypatch, ["zzkiwi", "--json"], make_run(),
                   archive=[archive_hit("eeeeeeee-5555-4666-8777-888888888888")])
    blob = json.loads(got["out"])
    assert isinstance(blob, list)
    assert blob[0]["session_id"] == "eeeeeeee-5555-4666-8777-888888888888"
    assert "live_state" not in blob[0]
    assert got["calls"] == [], "the classic path ran a live scan"


def test_the_classic_TEXT_path_is_untouched_by_this_feature(monkeypatch):
    """INVARIANT GUARD. The renderer was factored out of `main`; this pins that
    the factoring changed nothing a reader sees."""
    got = run_main(monkeypatch, ["zzkiwi"], make_run(),
                   archive=[archive_hit("ffffffff-6666-4777-8888-999999999999")])
    assert "1 session(s) matched 'zzkiwi'" in got["out"]
    assert "resume: claude --resume ffffffff-6666-4777-8888-999999999999" in got["out"]
    assert "<LIVE>" not in got["out"] and "<CLOSED>" not in got["out"]
    assert got["calls"] == []


def test_deep_without_live_says_it_did_nothing(monkeypatch):
    """A silently ignored flag is how a caller concludes it was honoured."""
    got = run_main(monkeypatch, ["zzkiwi", "--deep"], make_run(), archive=[])
    assert "--deep only means something with --live" in got["err"]


def test_fmt_age_states_a_MISSING_age_rather_than_rendering_zero():
    """🔴 A null age is not age 0 — no writer has recorded that window yet."""
    assert fs.fmt_age(None) == "no age recorded"
    assert fs.fmt_age(0) == "0s"
    assert fs.fmt_age(0) != fs.fmt_age(None)
    assert fs.fmt_age(45) == "45s"
    assert fs.fmt_age(600) == "10m"
    assert fs.fmt_age(7200) == "2h00m"
    assert fs.fmt_age(90000) == "1d01h"


# =========================================================================== #
# §2 — AUDIT FIX ROUND 1 (against tip a6f09d5a)
#
#   R1-1  🔴 A PARTIAL FLEET LABELLED A RUNNING SESSION `CLOSED`. `live_scan`
#         sets `status: "ok"` when ANY host answers and `hosts_unreachable` may
#         be non-empty in that state, but `live_session_ids` returned `None`
#         only on `status != "ok"`. So with the laptop asleep — this fleet's
#         COMMON degraded state — every archive hit whose session lived there
#         was stamped `CLOSED`, `live_ids_measured` said `true`, the
#         `⚠ UNMEASURED` line never printed and the exit code was 0. The LIVE
#         section's own caveat does not cover it: that line says the absence
#         "cannot appear BELOW" and refers to the live row list, not to the
#         separate ARCHIVE block.
#   R1-3  A FAILED `--tail` printed "(empty scrollback)" and exited 0. `rc` was
#         captured and nothing branched on it.
#   R1-4  `--any` / `--project` / `--since` reached only the archive leg,
#         silently — so `--any --live` sent ANDed terms and then reported a
#         measured absence under semantics nobody asked for. `--limit` did not
#         bound the LIVE section at all.
#   R1-8  `live_scan` raised AttributeError on valid-but-non-object JSON,
#         outside the try, from a function whose contract is to discriminate.
#
# 🔴 WHICH OF THESE IS REGRESSION COVERAGE. The GREEN-at-base ones are named in
# `R1_INVARIANT_GUARDS` below, and a test asserts every name resolves.
#
# ⚠ NO NODE COUNTS HERE, DELIBERATELY, AND THE REASON IS THE HISTORY. This
# comment carried "56 nodes … 10 GREEN", then "66 … 20 GREEN"; the sibling
# section carried "29 … 13 GREEN". EVERY ONE was measured wrong — the first by a
# function-level sweep that skipped `parametrize` expansion, the last because a
# later fix in the SAME commit added five nodes after the number was typed.
#
# A count that the next commit invalidates is a claim nothing enforces, which is
# the class three consecutive audit rounds kept finding. It cannot be derived at
# test time (it needs the head tests run against an OLD sha), so it is DELETED
# rather than corrected: understated coverage that reads as precise is worse
# than no number. The per-round matrices live in the PR body, each with the sha
# and the method that produced it. What stays here is the LEDGER, because a name
# either resolves or it does not.
# =========================================================================== #
R1_INVARIANT_GUARDS = frozenset({
    # The strict default of `live_state_of` IS the old behaviour — that is the
    # point of defaulting the new argument that way.
    "test_live_state_of_defaults_to_the_STRICT_reading",
    # NEGATIVE controls on the archive-only notice: it must not fire when it has
    # nothing to say, or the reader learns to skip the line.
    "test_no_archive_only_notice_when_none_was_passed",
    "test_the_notice_does_not_fire_without_live",
    # `--limit` never reached the live leg at a6f09d5a, so the ambiguity check
    # was already unsliced. These pin that ADDING the display cap did not
    # narrow it, which is the regression the change could have introduced.
    "test_limit_does_NOT_narrow_the_TAIL_ambiguity_check",
    "test_the_JSON_live_rows_are_NOT_truncated_by_limit",
    # POSITIVE control on the isinstance guard.
    "test_a_REAL_report_object_still_parses",
    # This ledger's own gate — it has no behaviour to regress, and it was the
    # entry this ledger forgot about itself.
    "test_the_R1_invariant_guard_ledger_names_only_tests_that_exist",
})


def test_the_R1_invariant_guard_ledger_names_only_tests_that_exist():
    assert R1_INVARIANT_GUARDS, "the ledger is empty — the gate is wired to nothing"
    for entry in R1_INVARIANT_GUARDS:
        func = entry.split("[", 1)[0]
        assert func in globals(), (
            f"{entry!r} is listed as an R1 invariant guard but no such test exists")

# 🔴 ROW_VAPOR lives on the LAPTOP and ROW_VIOLET on the WORKBENCH. That split is
# what makes the partial-fleet probes reachable: with the laptop down, Vapor's
# session can only be confirmed on a host that did not answer.
PARTIAL = dict(reachable=("workbench",), unreachable=("laptop",))


def partial_run(matched_rows=(), unfiltered_rows=(ROW_VIOLET,)):
    """A fake scan pair where the LAPTOP never answers."""
    return make_run({
        ("zzterm",): (3 if not matched_rows else 0,
                      live_report(list(matched_rows),
                                  match_fields=DEFAULT_MATCH_FIELDS, **PARTIAL)),
        (): (0, live_report(list(unfiltered_rows), **PARTIAL)),
    })


def test_the_partial_fixture_really_is_partial_and_still_yields_an_id_set():
    """POSITIVE CONTROL. R1-1 only exists in the state where `status == "ok"`
    AND a host is missing AND an id set was still built — if the fixture failed
    any of those, every probe below would pass for the wrong reason."""
    monkey = partial_run()
    fs_res = None
    try:
        old, fs.RUN = fs.RUN, monkey
        fs_res = fs.live_scan()
    finally:
        fs.RUN = old
    assert fs_res["status"] == "ok"
    assert fs_res["hosts_unreachable"] == ["laptop"]
    assert fs.live_session_ids(fs_res) == {ROW_VIOLET["claude_session_id"]}
    assert fs.live_coverage_complete(fs_res) is False


def test_a_PARTIAL_fleet_never_stamps_CLOSED_but_still_confirms_LIVE(monkeypatch):
    """🔴 R1-1, THE HEADLINE, and both directions in ONE render.

    A POSITIVE is a measurement whatever the coverage — finding the id on a host
    that answered proves the session is live, and no sleeping peer makes that
    false. A NEGATIVE proves nothing unless every host answered. A blanket
    "UNMEASURED on any unreachable host" would pass the CLOSED half of this and
    fail the LIVE half, so the two are asserted together.
    """
    got = run_main(monkeypatch, ["zzterm", "--live", "--json"], partial_run(),
                   archive=[
                       archive_hit(ROW_VIOLET["claude_session_id"]),   # workbench
                       archive_hit(ROW_VAPOR["claude_session_id"]),    # laptop
                   ])
    blob = json.loads(got["out"])
    states = [r["live_state"] for r in blob["archive"]["results"]]
    assert states == ["LIVE", "UNMEASURED"], (
        "a session that could only be confirmed on the host that did NOT "
        "answer was given a verdict")
    assert "CLOSED" not in states


def test_the_partial_fleet_publishes_COVERAGE_beside_live_ids_measured(
        monkeypatch):
    """`live_ids_measured: true` alone was the lie — a set existed, so the flag
    read as a full measurement. Coverage is a SEPARATE fact and is published as
    one, with the hosts named."""
    got = run_main(monkeypatch, ["zzterm", "--live", "--json"], partial_run(),
                   archive=[archive_hit(ROW_VAPOR["claude_session_id"])])
    arch = json.loads(got["out"])["archive"]
    assert arch["live_ids_measured"] is True
    assert arch["live_coverage_complete"] is False
    assert arch["live_hosts_unreachable"] == ["laptop"]


def test_the_partial_fleet_warns_IN_THE_ARCHIVE_BLOCK(monkeypatch):
    """🔴 The LIVE section's caveat is about the live ROW LIST ("cannot appear
    below"). The annotations are a different claim under a different heading and
    need their own line."""
    got = run_main(monkeypatch, ["zzterm", "--live"], partial_run(),
                   archive=[archive_hit(ROW_VAPOR["claude_session_id"])])
    out = got["out"]
    assert "live/closed state is PARTIAL" in out
    assert "laptop did not answer" in out
    assert "UNMEASURED rather than CLOSED" in out
    assert "<UNMEASURED>" in out
    assert "<CLOSED>" not in out


def test_a_FULLY_reachable_fleet_still_says_CLOSED(monkeypatch):
    """NEGATIVE CONTROL, and the permanently-red-gate check: "never say CLOSED"
    would kill the annotation entirely and pass every probe above."""
    run = make_run({("zzterm",): (3, live_report([], match_fields=DEFAULT_MATCH_FIELDS)),
                    (): (0, live_report([ROW_VIOLET]))})
    got = run_main(monkeypatch, ["zzterm", "--live", "--json"], run, archive=[
        archive_hit(ROW_VIOLET["claude_session_id"]),
        archive_hit("dddddddd-4444-4555-8666-777777777777"),
    ])
    blob = json.loads(got["out"])
    assert [r["live_state"] for r in blob["archive"]["results"]] == ["LIVE",
                                                                    "CLOSED"]
    assert blob["archive"]["live_coverage_complete"] is True
    assert blob["archive"]["live_hosts_unreachable"] == []
    assert "live/closed state is PARTIAL" not in got["out"]


@pytest.mark.parametrize("ids,complete,sid,expect", [
    ({"x"}, True, "x", "LIVE"),
    ({"x"}, False, "x", "LIVE"),      # a positive survives partial coverage
    ({"x"}, True, "y", "CLOSED"),
    ({"x"}, False, "y", "UNMEASURED"),  # ...a negative does not
    (None, True, "y", "UNMEASURED"),
    (None, False, "y", "UNMEASURED"),
])
def test_live_state_of_is_a_function_of_BOTH_the_set_and_the_coverage(
        ids, complete, sid, expect):
    """The whole truth table, so no cell is reachable only by accident."""
    assert fs.live_state_of(sid, ids, complete) == expect


def test_live_state_of_defaults_to_the_STRICT_reading():
    """A caller that forgets the new argument gets the old, stricter behaviour —
    wrong loudly rather than wrong quietly."""
    assert fs.live_state_of("y", {"x"}) == "CLOSED"


@pytest.mark.parametrize("res,expect", [
    ({"status": "ok", "hosts_unreachable": []}, True),
    ({"status": "ok", "hosts_unreachable": ["laptop"]}, False),
    ({"status": "unavailable", "hosts_unreachable": ["a", "b"]}, False),
    ({"status": "error", "hosts_unreachable": []}, False),
])
def test_live_coverage_complete_needs_BOTH_ok_and_a_full_fleet(res, expect):
    assert fs.live_coverage_complete(res) is expect


# --------------------------------------------------------------------------- #
# R1-3 — a tail that did not run is not an empty window
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("rc,label", [
    (2, "the host answered, no such window (it closed between scan and tail)"),
    (4, "the host went unreachable"),
    (5, "no tmux server on that host"),
], ids=["no-such-window", "unreachable", "no-server"])
def test_a_FAILED_tail_is_loud_and_does_NOT_exit_zero(monkeypatch, rc, label):
    """🔴 R1-3. `rc` was captured and nothing branched on it, so all three of
    these printed "(empty scrollback)" over exit 0 — a silent zero on the one
    half of this feature that answers "where did it leave off"."""
    run = make_run({("zzterm",): (0, live_report([ROW_VAPOR],
                                                 match_fields=DEFAULT_MATCH_FIELDS))},
                   tail=(rc, "", "tail: something went wrong"))
    got = run_main(monkeypatch, ["zzterm", "--live", "--tail", "40"], run)
    assert got["rc"] == fs.EXIT_UNAVAILABLE, f"exit 0 on a failed tail ({label})"
    assert f"TAIL: FAILED — `session-manager tail` exited {rc}" in got["out"]
    assert "The scrollback was NOT read" in got["out"]
    assert "empty scrollback" not in got["out"], (
        "a failed tail still rendered as an empty window")


def test_a_MEASURED_empty_scrollback_is_still_a_success(monkeypatch):
    """NEGATIVE CONTROL, and the boundary: `session-manager tail` returns 3 for
    a window whose scrollback really IS empty. Treating every non-zero rc as a
    failure would turn a measured empty into an error."""
    for rc in (0, 3):
        run = make_run({("zzterm",): (0, live_report([ROW_VAPOR]))},
                       tail=(rc, "", ""))
        got = run_main(monkeypatch, ["zzterm", "--live", "--tail", "40"], run)
        assert got["rc"] == fs.EXIT_OK, rc
        assert "MEASURED, the pane really is blank" in got["out"]
        assert "TAIL: FAILED" not in got["out"]


def test_the_tail_rc_and_ok_TRAVEL_IN_THE_JSON(monkeypatch):
    """An empty `text` beside `ok: false` is "not read"; beside `ok: true` it is
    a measured empty pane. Publishing `error` alone left those the same whenever
    stderr happened to be quiet."""
    run = make_run({("zzterm",): (0, live_report([ROW_VAPOR]))},
                   tail=(5, "", ""))
    got = run_main(monkeypatch, ["zzterm", "--live", "--json", "--tail", "9"],
                   run)
    tail = json.loads(got["out"])["tail"]
    assert tail["rc"] == 5
    assert tail["ok"] is False
    assert tail["text"] == ""
    assert tail["refused"] is False        # it RESOLVED; the tail then failed
    assert "TAIL: FAILED" in tail["message"]


def test_the_tail_MEASURED_rc_set_is_pinned_to_literals():
    """The sibling's own vocabulary: 0 = scrollback, 3 = measured empty.
    Comparing against the constant alone would be self-satisfying."""
    assert fs.TAIL_MEASURED_RCS == (0, 3)
    assert sm.EXIT_OK in fs.TAIL_MEASURED_RCS
    assert sm.EXIT_EMPTY in fs.TAIL_MEASURED_RCS
    for bad in (sm.EXIT_USAGE, sm.EXIT_UNAVAILABLE, sm.EXIT_NO_SERVER):
        assert bad not in fs.TAIL_MEASURED_RCS


# --------------------------------------------------------------------------- #
# R1-4 — an archive-only flag must say it did not reach the live leg
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("flag,needle", [
    (["--any"], "--any (the live scan ANDs its terms; there is no OR mode)"),
    (["--project", "zzproj"], "--project"),
    (["--since", "2026-01-01"], "--since"),
], ids=["any", "project", "since"])
def test_an_ARCHIVE_ONLY_flag_says_it_did_not_reach_the_live_scan(
        monkeypatch, flag, needle):
    """🔴 R1-4. `find-session.py redis vpn --any --live` sent ANDed terms to the
    live leg and then printed "(no live window matched these terms…)" — a
    measured absence under semantics the caller did not request. This diff
    already prints five notices of exactly this class."""
    run = make_run({("zzterm",): (0, live_report([ROW_VIOLET],
                                                 match_fields=DEFAULT_MATCH_FIELDS))})
    got = run_main(monkeypatch, ["zzterm", "--live"] + flag, run)
    assert "ARCHIVE-ONLY flags, ignored by the live scan" in got["err"]
    assert needle in got["err"]
    assert "NOT filtered by them" in got["err"]


def test_no_archive_only_notice_when_none_was_passed(monkeypatch):
    """NEGATIVE CONTROL — "always warn" would pass all three probes above and
    train the reader to ignore the line."""
    run = make_run({("zzterm",): (0, live_report([ROW_VIOLET]))})
    got = run_main(monkeypatch, ["zzterm", "--live"], run)
    assert "ARCHIVE-ONLY flags" not in got["err"]


def test_the_notice_does_not_fire_without_live(monkeypatch):
    """Without `--live` those flags reach the only leg there is."""
    got = run_main(monkeypatch, ["zzterm", "--any"], make_run(), archive=[])
    assert "ARCHIVE-ONLY flags" not in got["err"]


def test_limit_BOUNDS_THE_LIVE_SECTION_and_says_how_many_it_hid(monkeypatch):
    """🔴 R1-4's other half: the real fleet is 75 rows and `--limit` bounded
    only the archive list."""
    rows = [dict(ROW_VIOLET, session=f"s{i}", window_index=str(i),
                 claude_session_id=f"0000000{i}-1111-4222-8333-444444444444")
            for i in range(5)]
    run = make_run({("zzterm",): (0, live_report(rows,
                                                 match_fields=DEFAULT_MATCH_FIELDS))})
    got = run_main(monkeypatch, ["zzterm", "--live", "--limit", "2"], run)
    assert "LIVE (5 matched" in got["out"], "the header must state the FULL count"
    assert "(showing 2 of 5 — raise --limit to see the rest)" in got["out"]
    assert "1. workbench  s0:0" in got["out"]
    assert "2. workbench  s1:1" in got["out"]
    assert "s2:2" not in got["out"]


def test_limit_does_NOT_narrow_the_TAIL_ambiguity_check(monkeypatch):
    """🔴 Capping the ambiguity check at the DISPLAY limit would turn "several
    matched, I refuse" into "one is showing, I will tail that one" — guessing
    with extra steps. `--limit 1` over two matches must still refuse."""
    run = make_run({("zzterm",): (0, live_report([ROW_VIOLET, ROW_VAPOR],
                                                 match_fields=DEFAULT_MATCH_FIELDS))})
    got = run_main(monkeypatch, ["zzterm", "--live", "--limit", "1", "--tail", "5"],
                   run)
    assert got["rc"] == fs.EXIT_AMBIGUOUS
    assert "2 live windows matched" in got["out"]
    assert tail_calls(got["calls"]) == []
    # ...and both candidates are still listed, even the one --limit hid
    assert "tail scratch3:2 --host workbench" in got["out"]
    assert "tail scratch4:5 --host laptop" in got["out"]


def test_the_JSON_live_rows_are_NOT_truncated_by_limit(monkeypatch):
    """`--limit` is a DISPLAY cap. A machine consumer asked for the payload, and
    silently handing it a slice would be a measured absence of the rest."""
    rows = [dict(ROW_VIOLET, session=f"s{i}", window_index=str(i))
            for i in range(4)]
    run = make_run({("zzterm",): (0, live_report(rows))})
    got = run_main(monkeypatch, ["zzterm", "--live", "--json", "--limit", "1"],
                   run)
    assert len(json.loads(got["out"])["live"]["rows"]) == 4


# --------------------------------------------------------------------------- #
# R1-8 — valid JSON is not a report
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("body", ["[]", "null", '"a string"', "3"],
                         ids=["array", "null", "string", "number"])
def test_valid_but_NON_OBJECT_json_is_a_discriminated_error_not_a_crash(body):
    """🔴 R1-8. `report.get("hosts")` sat OUTSIDE the try, so any of these raised
    AttributeError out of a function whose whole contract is to return a status
    instead of raising. A truncated pipe or a wrapper printing a bare array is
    enough."""
    old, fs.RUN = fs.RUN, make_run(raw=(0, body, ""))
    try:
        res = fs.live_scan(["zzterm"])
    finally:
        fs.RUN = old
    assert res["status"] == "error"
    assert "not a report object" in res["error"]
    assert res["rows"] == []
    # 🔴 R2-F3: BOTH host lists are `None`, never `[]`. An empty
    # `hosts_unreachable` is the strongest possible claim — *every host
    # answered* — about a scan that never ran, and it leaked into the payload as
    # `archive.live_hosts_unreachable: []`.
    assert res["hosts_reachable"] is None
    assert res["hosts_unreachable"] is None


def test_a_REAL_report_object_still_parses():
    """POSITIVE CONTROL on the isinstance guard — a check that rejects
    everything would satisfy all four probes above."""
    old, fs.RUN = fs.RUN, make_run({(): (0, live_report([ROW_VIOLET]))})
    try:
        res = fs.live_scan()
    finally:
        fs.RUN = old
    assert res["status"] == "ok"
    assert len(res["rows"]) == 1


# =========================================================================== #
# §3 — AUDIT FIX ROUND 2 (against tip 9f9dcbde)
#
#   F1  🟡 `--claude-only` / `--opencode-only` / `--all` still reached ONLY the
#       archive leg, silently — UNDER a comment whose last line asserted the
#       class was closed ("these are the rest"). Reproduced on the live fleet:
#       `--live --opencode-only` returned tmux windows running CLAUDE, and
#       because the live leg matched, `run_archive` stayed False so the opencode
#       corpus was never searched at all. The completeness sentence is worse
#       than the omission: it tells the next reader to stop looking. The set is
#       DATA now (`ARCHIVE_ONLY_FLAGS`) and is pinned two-way against argparse.
#   F2  🟡 `_tail_outcome` stated a measured absence under a partial fleet:
#       "no live window matched, so there is nothing to tail", exit 3, with no
#       coverage field anywhere in the `tail` JSON — while the ARCHIVE block
#       three lines earlier correctly printed `⚠ live/closed state is PARTIAL`.
#   F3  🟡 `hosts_unreachable: []` for a scan that never ran reads as "every
#       host answered". Fixed at the SOURCE (`live_scan` seeds both host lists
#       `None`), so no publisher can reintroduce it.
#   F6  🟢 exit 4 now means three things; documented rather than split.
#   F8  🟢 `--limit 0` was unbounded on the live leg and empty on the archive
#       leg — one flag, opposite meanings in one run. Now a usage error.
#
# 🔴 The GREEN-at-9f9dcbde ones are named in `R2_INVARIANT_GUARDS` below. NO
# NODE COUNTS — see the note above §2's ledger: this section's count was typed
# before a later fix in the same commit added five nodes, and was wrong on
# arrival. The matrix lives in the PR body with its sha and method.
# =========================================================================== #
R2_INVARIANT_GUARDS = frozenset({
    # NEGATIVE control on F1's extra sentence — a non-corpus archive-only flag
    # must NOT get the corpus warning, or the line becomes noise.
    "test_a_NON_corpus_archive_only_flag_gets_the_SHORT_notice",
    # NEGATIVE control on F2 — with every host answering, "there is nothing to
    # tail" is TRUE and exit 3 is right. "Always say unmeasured" would pass the
    # partial-fleet probe and make the message useless.
    "test_a_tail_with_NO_match_on_a_FULL_fleet_is_still_a_MEASURED_absence",
    # NEGATIVE control on F3 — `unavailable` means the scan RAN, so those host
    # lists are real measurements and must NOT be nulled with the error paths.
    "test_an_UNAVAILABLE_scan_publishes_REAL_host_lists",
    # BOUNDARY control on F8 — `--limit 1` is the smallest legitimate value and
    # a check one off would reject it (a permanently-red gate).
    "test_limit_ONE_is_accepted_and_bounds_both_legs",
    # This ledger's own gate.
    "test_the_R2_ledger_names_only_tests_that_exist",
})


def test_the_R2_ledger_names_only_tests_that_exist():
    assert R2_INVARIANT_GUARDS, "the ledger is empty — the gate is wired to nothing"
    for entry in R2_INVARIANT_GUARDS:
        assert entry.split("[", 1)[0] in globals(), (
            f"{entry!r} is listed as an R2 invariant guard but no such test exists")


# --------------------------------------------------------------------------- #
# F1 — the archive-only ledger, and the completeness claim made structural
# --------------------------------------------------------------------------- #
def test_the_archive_only_ledger_PARTITIONS_every_argparse_destination():
    """🔴 F1, STRUCTURALLY. The old inline list was closed by a sentence —
    "these are the rest" — that was false when written. There is no sentence
    now: every parser destination is either live-aware or archive-only, and a
    NEW flag that is neither fails here rather than shipping a silent one-leg
    filter under a comment claiming the class is closed.

    🔴 READ OFF `_actions`, NOT OFF A PARSED NAMESPACE (R2 finding 🟢-3). A flag
    declared `default=argparse.SUPPRESS` never appears in the namespace, so it
    fell into NEITHER half and the equality still held — the gate's own comment
    claimed such a flag "fails the suite" while it measurably did not.
    `test_a_SUPPRESSED_flag_cannot_escape_the_partition` is the control.
    """
    dests = fs.parser_dests()
    assert dests, "the parser declared no destinations — gate wired to nothing"
    ledger = {d for d, _, _ in fs.ARCHIVE_ONLY_FLAGS}
    assert dests == fs.LIVE_AWARE_DESTS | ledger, (
        "a find-session flag is neither live-aware nor in ARCHIVE_ONLY_FLAGS:\n"
        f"  unclassified: {sorted(dests - fs.LIVE_AWARE_DESTS - ledger)}\n"
        f"  named but absent from the parser: "
        f"{sorted((fs.LIVE_AWARE_DESTS | ledger) - dests)}\n"
        "Decide which leg it reaches; do not leave it silent.")
    # the two halves are disjoint — a flag cannot be both
    assert not (fs.LIVE_AWARE_DESTS & ledger)
    # ...and the corpus selectors are a SUBSET of the archive-only set
    assert fs.CORPUS_SELECTOR_DESTS <= ledger


def test_the_ledger_contains_the_THREE_FLAGS_THE_SENTENCE_MISSED():
    """The specific regression, named. A structural partition that happened to
    classify these as live-aware would pass the test above."""
    ledger = {d for d, _, _ in fs.ARCHIVE_ONLY_FLAGS}
    assert {"claude_only", "opencode_only", "all"} <= ledger


@pytest.mark.parametrize("flag,dest", [
    (["--claude-only"], "claude_only"),
    (["--opencode-only"], "opencode_only"),
    (["--all"], "all"),
], ids=["claude-only", "opencode-only", "all"])
def test_a_CORPUS_flag_with_live_says_the_corpus_was_never_searched(
        monkeypatch, flag, dest):
    """🔴 F1's headline: `--live --opencode-only` showed Claude tmux windows and
    never searched opencode, because a matching live leg skips the archive
    entirely. The notice has to say THAT, not merely "not filtered by them"."""
    run = make_run({("zzterm",): (0, live_report([ROW_VIOLET],
                                                 match_fields=DEFAULT_MATCH_FIELDS))})
    got = run_main(monkeypatch, ["zzterm", "--live"] + flag, run,
                   archive=[archive_hit("dddddddd-4444-4555-8666-777777777777")])
    assert "ARCHIVE-ONLY flags, ignored by the live scan" in got["err"]
    assert "CORPUS selector only steers the ARCHIVE" in got["err"]
    assert "pass --deep" in got["err"]
    # ...and the run really did skip the archive, which is what makes the
    # sentence necessary rather than decorative.
    assert got["archive_calls"] == 0
    assert "ARCHIVE: skipped" in got["out"]


def test_a_NON_corpus_archive_only_flag_gets_the_SHORT_notice(monkeypatch):
    """NEGATIVE CONTROL on the extra sentence: `--since` does not steer a corpus,
    so appending the corpus warning to it would be noise that trains the reader
    to skip the line."""
    run = make_run({("zzterm",): (0, live_report([ROW_VIOLET]))})
    got = run_main(monkeypatch, ["zzterm", "--live", "--since", "2026-01-01"], run)
    assert "ARCHIVE-ONLY flags" in got["err"]
    assert "CORPUS selector" not in got["err"]


def test_archive_only_notice_is_None_when_nothing_applies():
    """The pure function, so the notice's absence is testable without a run."""
    ns = fs.parse_args(["zzterm", "--live"])
    assert fs.archive_only_notice(ns) is None
    assert fs.archive_only_notice(fs.parse_args(["zzterm", "--live", "--all"]))


def test_the_notice_NAMES_every_flag_that_was_passed(monkeypatch):
    """Several at once must all be listed — reporting only the first would be a
    count of declarations rather than of instances."""
    run = make_run({("zzterm",): (0, live_report([ROW_VIOLET]))})
    got = run_main(monkeypatch,
                   ["zzterm", "--live", "--any", "--all", "--project", "zzp"],
                   run)
    for spelling in ("--any", "--all", "--project"):
        assert spelling in got["err"], spelling


# --------------------------------------------------------------------------- #
# F2 — the TAIL block states its own coverage
# --------------------------------------------------------------------------- #
def test_a_tail_with_NO_match_under_a_PARTIAL_fleet_is_UNMEASURED(monkeypatch):
    """🔴 F2. "no live window matched, so there is nothing to tail" is a measured
    absence, and it was printed with exit 3 while a host had not answered — three
    lines under an ARCHIVE block that correctly said PARTIAL. The reasoning that
    earned the ARCHIVE block its own line applies verbatim here."""
    got = run_main(monkeypatch, ["zzterm", "--live", "--tail", "20"],
                   partial_run(), archive=[])
    assert got["rc"] == fs.EXIT_UNAVAILABLE, "still exit 3 (AMBIGUOUS)"
    assert "TAIL: REFUSED — no live window matched, but laptop did not answer" \
        in got["out"]
    assert "NOT 'there is nothing to tail'" in got["out"]
    assert "UNMEASURED" in got["out"]


def test_a_tail_with_NO_match_on_a_FULL_fleet_is_still_a_MEASURED_absence(
        monkeypatch):
    """NEGATIVE CONTROL: with every host answering, "there is nothing to tail" is
    TRUE and the exit code stays 3. "Always say unmeasured" would pass the probe
    above and make the message useless."""
    run = make_run({("zzterm",): (3, live_report([], match_fields=DEFAULT_MATCH_FIELDS)),
                    (): (0, live_report([]))})
    got = run_main(monkeypatch, ["zzterm", "--live", "--tail", "20"], run, archive=[])
    assert got["rc"] == fs.EXIT_AMBIGUOUS
    assert "no live window matched, so there is nothing to tail" in got["out"]
    assert "did not answer" not in got["out"]


def test_a_SINGLE_match_under_a_PARTIAL_fleet_still_tails_but_DISCLOSES_it(
        monkeypatch):
    """🔴 Refusing here would make `--tail` useless whenever the laptop sleeps —
    a permanently-red gate. Claiming "this is the one" would be the guess the
    function exists to refuse. So it tails AND says the resolution may not be
    unique."""
    run = make_run({("zzterm",): (0, live_report([ROW_VIOLET],
                                                 match_fields=DEFAULT_MATCH_FIELDS,
                                                 **PARTIAL)),
                    (): (0, live_report([ROW_VIOLET], **PARTIAL))},
                   tail=(0, "synthetic scrollback\n", ""))
    got = run_main(monkeypatch, ["zzterm", "--live", "--tail", "20"], run)
    assert got["rc"] == fs.EXIT_OK
    assert "resolved on PARTIAL coverage" in got["out"]
    assert "Another window may match there" in got["out"]
    assert "synthetic scrollback" in got["out"]
    assert len(tail_calls(got["calls"])) == 1


def test_SEVERAL_matches_under_a_PARTIAL_fleet_say_the_candidate_list_is_SHORT(
        monkeypatch):
    """The refusal was already right; the candidate list is also incomplete."""
    run = make_run({("zzterm",): (0, live_report([ROW_VIOLET, ROW_VAPOR],
                                                 match_fields=DEFAULT_MATCH_FIELDS,
                                                 reachable=("workbench", "laptop"),
                                                 unreachable=())),
                    (): (0, live_report([ROW_VIOLET, ROW_VAPOR]))})
    # ...first the FULL-fleet control: no incompleteness line
    got = run_main(monkeypatch, ["zzterm", "--live", "--tail", "20"], run)
    assert got["rc"] == fs.EXIT_AMBIGUOUS
    assert "candidate list is INCOMPLETE" not in got["out"]

    rows = [dict(ROW_VIOLET), dict(ROW_VIOLET, session="scratch5",
                                   window_index="7")]
    partial = make_run({("zzterm",): (0, live_report(rows, **PARTIAL,
                                                     match_fields=DEFAULT_MATCH_FIELDS)),
                        (): (0, live_report(rows, **PARTIAL))})
    got2 = run_main(monkeypatch, ["zzterm", "--live", "--tail", "20"], partial)
    assert got2["rc"] == fs.EXIT_AMBIGUOUS
    assert "candidate list is INCOMPLETE" in got2["out"]
    assert tail_calls(got2["calls"]) == []


def test_the_tail_JSON_carries_its_OWN_coverage_fields(monkeypatch):
    """🔴 `archive.*` describes the UNFILTERED scan and is absent entirely on the
    fast path, so a `tail` consumer could not read coverage from it."""
    got = run_main(monkeypatch, ["zzterm", "--live", "--json", "--tail", "20"],
                   partial_run(), archive=[])
    tail = json.loads(got["out"])["tail"]
    assert tail["coverage_complete"] is False
    assert tail["hosts_unreachable"] == ["laptop"]
    assert tail["refused"] is True

    full = make_run({("zzterm",): (0, live_report([ROW_VIOLET]))},
                    tail=(0, "x\n", ""))
    got2 = run_main(monkeypatch, ["zzterm", "--live", "--json", "--tail", "20"],
                    full)
    tail2 = json.loads(got2["out"])["tail"]
    assert tail2["coverage_complete"] is True
    assert tail2["hosts_unreachable"] == []


# --------------------------------------------------------------------------- #
# F3 — an unmeasured host list is null, never []
# --------------------------------------------------------------------------- #
def test_live_hosts_unreachable_is_NULL_for_a_scan_that_never_ran(monkeypatch):
    """🔴 F3. `[]` reads as the strongest possible claim — every host answered —
    about a scan that errored. The same round wrote null-never-`[]` into
    `payload-contract.md` as doctrine."""
    got = run_main(monkeypatch, ["zzterm", "--live", "--json"],
                   make_run(boom=OSError("session-manager is not on this host")),
                   archive=[archive_hit("dddddddd-4444-4555-8666-777777777777")])
    blob = json.loads(got["out"])
    assert blob["live"]["status"] == "error"
    assert blob["live"]["hosts_unreachable"] is None
    assert blob["live"]["hosts_reachable"] is None
    assert blob["archive"]["live_ids_measured"] is False
    assert blob["archive"]["live_coverage_complete"] is None
    assert blob["archive"]["live_hosts_unreachable"] is None, (
        "an unmeasured scan published an EMPTY unreachable list, which reads as "
        "'every host answered'")


def test_an_UNAVAILABLE_scan_publishes_REAL_host_lists(monkeypatch):
    """NEGATIVE CONTROL, and the boundary that matters: `unavailable` means the
    scan RAN and every host was unreachable, so those lists are measurements.
    Nulling them too would lose the only fact that run produced."""
    run = make_run({("zzterm",): (4, live_report([], reachable=(),
                                                 unreachable=("workbench", "laptop"))),
                    (): (4, live_report([], reachable=(),
                                        unreachable=("workbench", "laptop")))})
    got = run_main(monkeypatch, ["zzterm", "--live", "--json"], run, archive=[])
    blob = json.loads(got["out"])
    assert blob["live"]["status"] == "unavailable"
    assert blob["live"]["hosts_unreachable"] == ["laptop", "workbench"]
    assert blob["live"]["hosts_reachable"] == []
    assert blob["archive"]["live_hosts_unreachable"] == ["laptop", "workbench"]


# --------------------------------------------------------------------------- #
# F8 — one flag, one meaning
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("value", ["0", "-1"], ids=["zero", "negative"])
def test_limit_below_one_is_a_USAGE_error_on_BOTH_legs(monkeypatch, value):
    """🔴 F8. `--limit 0` was UNBOUNDED on the live leg (`limit <= 0` meant show
    everything) and EMPTY on the archive leg (`results[:0]`) — the same flag,
    the same number, opposite meanings in one run. Rejected rather than
    silently picking a winner."""
    got = run_main(monkeypatch, ["zzterm", "--live", "--limit", value],
                   make_run(), archive=[])
    assert got["rc"] == fs.EXIT_USAGE
    assert "--limit must be at least 1" in got["err"]
    assert got["calls"] == [], "it ran a live scan despite the bad argument"
    assert got["archive_calls"] == 0


def test_limit_below_one_is_rejected_on_the_CLASSIC_path_too(monkeypatch):
    """The check sits before the `--live` branch, so both legs agree. This IS a
    behaviour change on the classic path for `--limit < 1`, and it is
    deliberate: the previous behaviour there was a silent empty result."""
    got = run_main(monkeypatch, ["zzterm", "--limit", "0"], make_run(),
                   archive=[archive_hit("dddddddd-4444-4555-8666-777777777777")])
    assert got["rc"] == fs.EXIT_USAGE
    assert got["archive_calls"] == 0


@pytest.mark.parametrize("limit,expect", [
    (None, 3), (1, 1), (2, 2), (9, 3),
    # 🔴 THE VALUE THE CLI NOW REJECTS, pinned AT THE FUNCTION anyway. `main`
    # rejects `--limit < 1`, which makes the old `limit <= 0` special case
    # unreachable from the CLI — a mutation sweep scored restoring it SURVIVED
    # for exactly that reason. `render_live` is called directly by tests and by
    # anything that imports it, so its slice semantics are pinned here rather
    # than left to depend on a caller's validation. `0` means ZERO rows, the
    # same as the archive leg's `results[:0]` — never "everything".
    (0, 0),
], ids=["none", "one", "two", "over", "zero"])
def test_render_live_slices_like_the_ARCHIVE_leg_at_every_limit(limit, expect):
    res = {"status": "ok", "rows": [dict(ROW_VIOLET, session=f"s{i}")
                                    for i in range(3)],
           "hosts_reachable": ["workbench"], "hosts_unreachable": [],
           "match_fields": DEFAULT_MATCH_FIELDS}
    rendered = "\n".join(fs.render_live(res, limit=limit))
    shown = sum(1 for line in rendered.splitlines()
                if line[:1].isdigit() and ". workbench" in line)
    assert shown == expect, rendered
    # the header always states the FULL count, whatever the display cap
    assert "LIVE (3 matched" in rendered


def test_limit_ONE_is_accepted_and_bounds_both_legs(monkeypatch):
    """POSITIVE CONTROL on the boundary — a check one off would reject the
    smallest legitimate value, which is the permanently-red-gate shape."""
    rows = [dict(ROW_VIOLET, session=f"s{i}", window_index=str(i))
            for i in range(3)]
    run = make_run({("zzterm",): (0, live_report(rows,
                                                 match_fields=DEFAULT_MATCH_FIELDS))})
    got = run_main(monkeypatch, ["zzterm", "--live", "--limit", "1"], run)
    assert got["rc"] == fs.EXIT_OK
    assert "(showing 1 of 3" in got["out"]


# =========================================================================== #
# §4 — AUDIT FIX ROUND 3-DELTA (against tip 6914aa33)
#
#   R3-🟡2  `tail.coverage_complete` published a measured-looking `false` for a
#           scan that NEVER RAN, while the sibling `archive.live_coverage_
#           complete` was `null` in the same document — the exact shape F3
#           removed from `hosts_unreachable` ONE FIELD OVER, in the same commit.
#           And `SKILL.md` names this field as the branch point, so the doc
#           pointed the reader at the one field that could not discriminate.
#   R3-🟢2  The corpus-consequence sentence fired even under `--deep`, telling a
#           caller to "pass --deep" in a run that already had.
#   R3-🟢3  The partition gate read a parsed NAMESPACE, so a flag declared
#           `default=argparse.SUPPRESS` was in neither half and the equality
#           still held — while the gate's comment claimed such a flag fails.
#
# 🔴 NO NODE COUNTS IN THIS SECTION. Two rounds running, a hand-typed
# "N new / R red / G green" was measured wrong — the second time because a fix
# added five nodes AFTER the number was written. A count that a later commit
# invalidates is a claim nothing enforces, which is the class this whole round
# is about. The per-round matrices live in the PR body with the sha and method
# that produced them; the LEDGERS below stay, because a name either resolves or
# it does not and a test checks that.
# =========================================================================== #
R3_GREEN_AT_AUDITED_TIP = frozenset({
    # NEGATIVE control on the tri-state: a MEASURED partial fleet must still
    # publish `false`. "Always null" would pass the never-ran probe and destroy
    # the field on the fleet state it exists for.
    "test_a_MEASURED_partial_fleet_still_publishes_FALSE",
    # NEGATIVE control on the --deep suppression: suppressing the corpus
    # sentence unconditionally would pass that probe and silently undo R2-F1.
    "test_the_corpus_sentence_STILL_fires_without_deep",
    # This ledger's own gate — no behaviour to regress.
    "test_the_R3_ledger_names_only_tests_that_exist",
})


def test_the_R3_ledger_names_only_tests_that_exist():
    assert R3_GREEN_AT_AUDITED_TIP, "the ledger is empty — gate wired to nothing"
    for entry in R3_GREEN_AT_AUDITED_TIP:
        assert entry.split("[", 1)[0] in globals(), (
            f"{entry!r} is listed in the R3 ledger but no such test exists")

def test_tail_coverage_is_NULL_for_a_scan_that_never_ran(monkeypatch):
    """🔴 R3-🟡2. `live_coverage_complete` is a two-valued PREDICATE and is right
    for branching; PUBLISHING its `False` says *measured, and incomplete* about
    a scan that produced no measurement."""
    got = run_main(monkeypatch, ["zzterm", "--live", "--json", "--tail", "20"],
                   make_run(boom=OSError("session-manager is not on this host")),
                   archive=[])
    blob = json.loads(got["out"])
    assert blob["live"]["status"] == "error"
    assert blob["tail"]["coverage_complete"] is None, (
        "a scan that never ran published a measured-looking coverage verdict")
    assert blob["tail"]["hosts_unreachable"] is None
    # ...and it agrees with its sibling in the SAME document, which is the
    # inconsistency that made this findable at all.
    assert blob["archive"]["live_coverage_complete"] is None


@pytest.mark.parametrize("status,unreachable,expect", [
    ("ok", [], True),
    ("ok", ["laptop"], False),
    # `unavailable` RAN and every host was unreachable — a real measurement.
    ("unavailable", ["workbench", "laptop"], False),
    ("error", None, None),
], ids=["full", "partial", "unavailable", "never-ran"])
def test_live_coverage_state_is_the_TRI_STATE_publishable_form(status,
                                                               unreachable,
                                                               expect):
    """The whole truth table, so no cell is reachable only by accident. 🔴 The
    `unavailable` row is the one that must NOT be `None`: that scan ran."""
    assert fs.live_coverage_state(
        {"status": status, "hosts_unreachable": unreachable}) is expect


def test_the_PREDICATE_and_the_PUBLISHED_form_differ_only_on_never_ran():
    """One writer each, and their disagreement is exactly one case. If they
    agreed everywhere the tri-state would be pointless; if they disagreed
    anywhere else, branching and publishing would have drifted apart."""
    cases = [
        {"status": "ok", "hosts_unreachable": []},
        {"status": "ok", "hosts_unreachable": ["laptop"]},
        {"status": "unavailable", "hosts_unreachable": ["a", "b"]},
        {"status": "error", "hosts_unreachable": None},
    ]
    differ = [c for c in cases
              if fs.live_coverage_complete(c) != fs.live_coverage_state(c)]
    assert [c["status"] for c in differ] == ["error"]


def test_a_MEASURED_partial_fleet_still_publishes_FALSE(monkeypatch):
    """NEGATIVE CONTROL: "always null" would pass the never-ran probe and
    destroy the field's usefulness on the fleet state it exists for."""
    got = run_main(monkeypatch, ["zzterm", "--live", "--json", "--tail", "20"],
                   partial_run(), archive=[])
    tail = json.loads(got["out"])["tail"]
    assert tail["coverage_complete"] is False
    assert tail["hosts_unreachable"] == ["laptop"]


# --------------------------------------------------------------------------- #
# R3-🟢2 — the corpus sentence must not fire when --deep already ran the archive
# --------------------------------------------------------------------------- #
def test_the_corpus_sentence_is_SUPPRESSED_when_deep_already_forced_the_archive(
        monkeypatch):
    """🔴 R3-🟢2. The sentence exists to say "the corpus you chose may go
    unsearched — pass --deep". Printing it in a run that already passed `--deep`
    is advice to do the thing the caller did — the same "noise that trains the
    reader to skip the line" this file refused to append to `--since`."""
    run = make_run({("zzterm",): (0, live_report([ROW_VIOLET],
                                                 match_fields=DEFAULT_MATCH_FIELDS)),
                    (): (0, live_report([ROW_VIOLET]))})
    got = run_main(monkeypatch,
                   ["zzterm", "--live", "--deep", "--opencode-only"], run,
                   archive=[archive_hit("dddddddd-4444-4555-8666-777777777777")])
    # the flag is still NAMED — it genuinely does not filter the live section
    assert "ARCHIVE-ONLY flags" in got["err"]
    assert "--opencode-only" in got["err"]
    # ...but the advice is gone, because the archive DID run
    assert "pass --deep" not in got["err"]
    assert got["archive_calls"] == 1
    assert "ran because: --deep" in got["out"]


def test_the_corpus_sentence_STILL_fires_without_deep(monkeypatch):
    """NEGATIVE CONTROL — suppressing it unconditionally would pass the probe
    above and silently undo R2-F1."""
    run = make_run({("zzterm",): (0, live_report([ROW_VIOLET],
                                                 match_fields=DEFAULT_MATCH_FIELDS))})
    got = run_main(monkeypatch, ["zzterm", "--live", "--opencode-only"], run)
    assert "pass --deep" in got["err"]


def test_archive_only_notice_reads_deep_off_the_ARGS_not_a_global():
    """The pure function, both ways, so the branch is pinned without a run."""
    base = fs.parse_args(["zzterm", "--live", "--opencode-only"])
    assert "pass --deep" in fs.archive_only_notice(base)
    deep = fs.parse_args(["zzterm", "--live", "--deep", "--opencode-only"])
    assert "pass --deep" not in fs.archive_only_notice(deep)
    assert "--opencode-only" in fs.archive_only_notice(deep)


# --------------------------------------------------------------------------- #
# R3-🟢3 — a SUPPRESSED flag must not escape the partition
# --------------------------------------------------------------------------- #
def test_parser_dests_reads_the_ACTIONS_not_a_parsed_namespace():
    """🔴 The mechanism, stated: `vars(parse_args(...))` cannot see a
    `SUPPRESS`-defaulted flag at all."""
    assert fs.parser_dests() == set(vars(fs.parse_args(["zzterm"]))), (
        "no SUPPRESS flag exists today, so the two views must agree — if they "
        "do not, one of them is already wrong")
    assert "help" not in fs.parser_dests()


def test_a_SUPPRESSED_flag_cannot_escape_the_partition(monkeypatch):
    """🔴 R3-🟢3, THE CONTROL THE OLD GATE FAILED. A flag declared with
    `default=argparse.SUPPRESS` is absent from the namespace, so the old
    namespace-based equality held with the flag classified NOWHERE — measured
    94/94 green with exactly this planted. Read off `_actions`, it is caught.
    """
    real = fs.build_parser

    def with_suppressed():
        p = real()
        p.add_argument("--newest-first", action="store_true",
                       default=argparse.SUPPRESS,
                       help="planted: classified in neither half")
        return p

    monkeypatch.setattr(fs, "build_parser", with_suppressed)
    dests = fs.parser_dests()
    assert "newest_first" in dests, (
        "the destination view cannot see a SUPPRESS-defaulted flag — the "
        "partition gate is blind to exactly the flag it claims to catch")
    ledger = {d for d, _, _ in fs.ARCHIVE_ONLY_FLAGS}
    assert dests != fs.LIVE_AWARE_DESTS | ledger, (
        "an unclassified flag left the partition equality holding")
