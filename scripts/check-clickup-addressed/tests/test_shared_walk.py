#!/usr/bin/env python3
"""This skill's half of the transcript-walk consolidation (devrc, 2026-08-24).

`search-sessions.py` and `check-completion.py` used to carry their own corpus walks and
their own JSONL parsers, duplicating a third in `scripts/find-session.py`. They now share
`scripts/lib/transcript_search.py`. Three of the tests below were watched RED against
324693fd — the pre-consolidation tree — and are named in `RED_AT_BASE`; the rest are
invariant guards or ledgers over the mutation harness, and are NOT evidence of a fix.
"""
import importlib.util
import json
import os
import re
import sys
import tempfile
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent.parent

RED_AT_BASE = frozenset({
    "test_a_malformed_line_costs_the_line_not_the_whole_transcript",
    "test_the_project_filter_ignores_case",
    "test_read_session_survives_a_malformed_line",
})


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, SCRIPT_DIR / filename)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


search_sessions = _load("search_sessions_shared", "search-sessions.py")
check_completion = _load("check_completion_shared", "check-completion.py")


def _user(text, **kw):
    d = {"type": "user", "message": {"content": text}}
    d.update(kw)
    return json.dumps(d)


def _write(root, project, session_id, lines, subagent=False):
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


# ------------------------------------------------------------------- RED AT BASE

def test_a_malformed_line_costs_the_line_not_the_whole_transcript():
    """🔴 RED at 324693fd.

    `search_sessions` wrapped the whole-file read in
    `except (json.JSONDecodeError, OSError): continue`, so ONE unparseable line discarded
    every message in that transcript — silently, and indistinguishably from the session
    not mentioning the term at all. A truncated tail is the normal shape of a transcript
    that is still being written. Re-measured 2026-08-25: 0 of 797 live transcripts carry
    one, so this closes a hazard that had no live instances rather than a firing bug.
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "projects"
        _write(root, "-proj", "sess", [
            _user("mentions zzcorrupt once"),
            "{ this line is not json",
            _user("and again zzcorrupt"),
        ])
        orig, search_sessions.CLAUDE_DIR = search_sessions.CLAUDE_DIR, root
        try:
            got = search_sessions.search_sessions(["zzcorrupt"], include_self_runs=True)
        finally:
            search_sessions.CLAUDE_DIR = orig
        assert [r["session_id"] for r in got] == ["sess"], got
        assert got[0]["hits"] == 2, got[0]


def test_the_project_filter_ignores_case():
    """🔴 RED at 324693fd: the filter was `project_substr not in project_dir.name` —
    case-SENSITIVE, and blind to the recorded `cwd`. find-session.py's was
    case-insensitive over cwd + dir name the whole time. Unified on the permissive one;
    `check-addressed.py` passes no `--project`, so nothing in this skill's own pipeline
    changes verdicts because of it."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "projects"
        _write(root, "-home-zach-workspace-WidgetRepo", "sess", [_user("zzproj")])
        orig, search_sessions.CLAUDE_DIR = search_sessions.CLAUDE_DIR, root
        try:
            lower = search_sessions.search_sessions(["zzproj"], project_substr="widgetrepo",
                                                    include_self_runs=True)
            exact = search_sessions.search_sessions(["zzproj"], project_substr="WidgetRepo",
                                                    include_self_runs=True)
            miss = search_sessions.search_sessions(["zzproj"], project_substr="othername",
                                                   include_self_runs=True)
        finally:
            search_sessions.CLAUDE_DIR = orig
        assert len(lower) == 1, lower
        assert len(exact) == 1, exact
        assert miss == [], miss


def test_read_session_survives_a_malformed_line():
    """🔴 RED at 324693fd, where it raised `json.JSONDecodeError`.

    `_read_session`'s `json.loads(line)` had no guard at all, so one bad line aborted the
    entire completion check rather than costing that line — while the search stage that
    chose the session silently swallowed the same file. Both stages now parse through one
    reader, so they cannot disagree about which lines a transcript contains.
    """
    with tempfile.TemporaryDirectory() as tmp:
        p = _write(Path(tmp), "-proj", "sess", [
            _user("before the damage"),
            "}{ truncated",
            _user("after the damage"),
        ])
        text = check_completion._read_session(p)
        assert "before the damage" in text and "after the damage" in text, text


# --------------------------------------------------------------- invariant guards

def test_the_completion_stage_never_attributes_work_to_a_subagent_transcript():
    """INVARIANT GUARD — green at 324693fd as well, but for a reason that no longer holds.

    There, `_find_sessions_for_task` used a FLAT `project_dir.glob("*.jsonl")` and simply
    never recursed deep enough to reach the 4,776 `subagents/` transcripts. It now uses the
    shared enumerator, which recurses and excludes them by name. The property is the same;
    the mechanism keeping it true is not, so it is pinned rather than assumed.
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "projects"
        _write(root, "-proj", "real", [_user("task zzsubtask done")])
        _write(root, "-proj", "real", [_user("task zzsubtask done")], subagent=True)
        orig, check_completion.CLAUDE_DIR = check_completion.CLAUDE_DIR, root
        try:
            sessions, skipped = check_completion._find_sessions_for_task(
                "zzsubtask", include_self_runs=True)
        finally:
            check_completion.CLAUDE_DIR = orig
        assert sessions == ["real"], sessions
        assert skipped == 0


def test_session_path_will_not_resolve_an_id_to_a_subagent_transcript():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "projects"
        d = root / "-proj" / "outer" / "subagents"
        d.mkdir(parents=True)
        (d / "ghost.jsonl").write_text(_user("hello") + "\n")
        orig, check_completion.CLAUDE_DIR = check_completion.CLAUDE_DIR, root
        try:
            assert check_completion.session_path("ghost") is None
        finally:
            check_completion.CLAUDE_DIR = orig


def test_the_raw_line_predicate_is_still_wider_than_the_parsed_search():
    """`_find_sessions_for_task` matches the task id ANYWHERE in the raw JSONL line —
    a tool_result, a file path, a metadata field — which is deliberately wider than
    search-sessions.py's parsed surface. Consolidating the WALK must not have narrowed the
    PREDICATE: this fixture hides the id only in a field neither text nor tool_use reaches.
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "projects"
        _write(root, "-proj", "meta", [
            json.dumps({"type": "user", "message": {"content": "nothing to see"},
                        "gitBranch": "fix/zzhidden"}),
        ])
        orig_cc, check_completion.CLAUDE_DIR = check_completion.CLAUDE_DIR, root
        orig_ss, search_sessions.CLAUDE_DIR = search_sessions.CLAUDE_DIR, root
        try:
            raw, _ = check_completion._find_sessions_for_task("zzhidden", include_self_runs=True)
            parsed = search_sessions.search_sessions(["zzhidden"], include_self_runs=True)
        finally:
            check_completion.CLAUDE_DIR = orig_cc
            search_sessions.CLAUDE_DIR = orig_ss
        assert raw == ["meta"], raw
        assert parsed == [], parsed


# ------------------------------------------------------- the mutation harness itself

def test_the_mutation_sweep_carries_every_shared_module_these_scripts_import():
    """🔴 A LEDGER OVER THE INSTRUMENT, pinned two-way.

    `mutation_sweep.py` runs each mutant inside a COPY of this directory. The scripts now
    import `../lib/transcript_search.py`, so the copy has to reproduce that layout or every
    test file scores FAILED TO IMPORT — at which point the NULL-CONTROL aborts and no sweep
    can be run at all. If a script starts importing a second `scripts/lib/` module and
    `SHARED_MODULES` is not extended, this fails here instead of silently disarming the
    sweep. It fails on a SHRINK too: a module listed but no longer imported is a copy step
    nobody will notice has gone stale.

    🔴 IT SCANS `tests/*.py` TOO, and for a round it did not. `SCRIPT_DIR.glob("*.py")`
    is the top-level scripts ONLY, so a shared-lib import introduced in a TEST file was
    invisible to this ledger — and a test file is exactly where an import gets added
    casually. The copy has to carry a module a test imports for the same reason it has to
    carry one a script imports.
    """
    sweep = _load("mutation_sweep_ledger", "tests/mutation_sweep.py")
    lib = SCRIPT_DIR.parent / "lib"
    sources = sorted(SCRIPT_DIR.glob("*.py")) + sorted((SCRIPT_DIR / "tests").glob("*.py"))
    assert len(sources) >= 15, f"positive control: only {len(sources)} source file(s) scanned"
    imported = set()
    for script in sources:
        src = script.read_text()
        for mod in lib.glob("*.py"):
            if f"from {mod.stem} import" in src or f"import {mod.stem}\n" in src:
                imported.add(mod.name)
    assert imported, "the positive control failed: no shared module import was detected at all"
    assert set(sweep.SHARED_MODULES) == imported, (
        f"mutation_sweep.SHARED_MODULES={sorted(sweep.SHARED_MODULES)} but the scripts "
        f"and tests import {sorted(imported)}")
    for name in sweep.SHARED_MODULES:
        assert (lib / name).exists(), name


def test_the_sweeps_null_control_is_green_in_a_fresh_copy():
    """🔴 THE SWEEP'S OWN PRECONDITION — checked by RUNNING it, in a subprocess.

    If anything is red inside a fresh mutant copy, `mutation_sweep` scores its
    NULL-CONTROL KILLED and aborts: no mutant can be scored at all, and the tool that
    tells you your guards work is silently unavailable. Two live instances, both of which
    a name-list ledger is structurally unable to anticipate: `test_awaiting_contract.py`
    reads a contract table from `parents[3]/claude/skills/clickup/test/` at MODULE level
    (fixed by `REPO_FIXTURES`), and the scripts import `../lib/transcript_search.py`
    (fixed by `SHARED_MODULES`).

    🔴 THE SUBPROCESS IS THE WHOLE POINT, and the previous version of this guard was
    BLIND for want of it. It exec'd the copied test modules IN THE OUTER PYTEST PROCESS,
    whose `sys.path`/`sys.modules` already carry the REAL `scripts/lib` — inserted there
    by the real `search-sessions.py` at import — so the copy's own layout was never
    consulted. Measured 2026-08-25: with `SHARED_MODULES = ()` in the copy, that guard
    reported ✓ green while the actual sweep in the same tree printed `NULL-CTL KILLED`,
    five files red, ABORTED. It also only ever observed IMPORT failure, while the sweep
    aborts on any red test — a repo-relative read inside a test FUNCTION was outside its
    reach despite a docstring that said it asserted "the OUTCOME".

    So this calls `run_one` itself, with the exact arguments `main()` passes, and asserts
    the verdict it acts on. Cost measured at ~0.4s: the copied suite is 244 tests in 0.3s.

    🔴 IT MUST NOT RUN INSIDE THE COPY IT SPAWNS. `run_one` runs the whole suite, and the
    suite contains this test — unbraked, the first call forks a copy that forks a copy,
    unbounded (measured: 2,492 trees under /tmp before the run was killed). Two
    independent brakes, checked against each other so neither can silently disarm the
    guard: the `NESTED_RUN_ENV` variable `run_one` exports into every child, and the
    structural fact that a mutant copy carries only the skill + `lib/` and so has no
    sibling `find-session.py`. If they ever disagree, that is a broken harness — a stray
    export in a real checkout, or a `materialize` that now copies the whole tree — and it
    fails here rather than either recursing or quietly covering nothing.
    """
    sweep = _load("mutation_sweep_nullctl", "tests/mutation_sweep.py")
    by_env = os.environ.get(sweep.NESTED_RUN_ENV) == "1"
    by_layout = not (SCRIPT_DIR.parent / "find-session.py").exists()
    assert by_env == by_layout, (
        f"the two nesting brakes disagree: {sweep.NESTED_RUN_ENV}={by_env}, "
        f"missing-sibling-find-session.py={by_layout}. One of them is now wrong, and "
        f"getting this wrong either recurses without bound or disarms the guard.")
    if by_env:
        return          # deliberate no-op INSIDE a mutant copy; the outer run does the work

    with tempfile.TemporaryDirectory() as tmp:
        verdict, killers, out = sweep.run_one("NULL-CONTROL", sweep.CA, "", "",
                                              "no mutation", tmp)
    # DEAD-RUN CONTROL first: no verdict line at all is not a pass, and reads as empty
    # output through any filter. This one is safe to run first — it cannot be satisfied by
    # a broken tree, only by a missing one.
    total = re.search(r"Total: (\d+) passed, (\d+) failed", out)
    assert total, f"the copied suite printed no verdict line at all:\n{out[-2000:]}"

    # 🔴 THE VERDICT ASSERT COMES BEFORE THE COLLECTION CONTROL, and that ordering was
    # measured, not assumed. `run_all.py` scores a file that FAILS TO IMPORT as one
    # failure for the whole file, so a copy missing `scripts/lib/` collapses from 244
    # collected to 15 — which means a leading `passed >= 200` control fires FIRST and this
    # test dies on the wrong assertion, saying "collected only 15" about the exact mutant
    # it exists to catch. Verified 2026-08-25 by running it in that order. On the green
    # path the collection control is still fully reachable, which is where it does its job.
    assert verdict == "SURVIVED", (
        f"the sweep's NULL-CONTROL scores {verdict} in a fresh copy, so every mutant "
        f"would report KILLED whatever it did and no sweep can be run.\n"
        f"copy reported {total.group(1)} passed / {total.group(2)} failed; "
        f"red without any mutation: {killers[:8]}")
    assert int(total.group(1)) >= 200, (
        f"positive control: the NULL-CONTROL is green but the copy collected only "
        f"{total.group(1)} test(s) — a suite covering nothing also reports no failures")


def test_the_red_at_base_ledger_names_only_tests_that_exist():
    defined = {k for k in globals() if k.startswith("test_")}
    missing = RED_AT_BASE - defined
    assert not missing, f"RED_AT_BASE names tests that do not exist: {sorted(missing)}"
    assert len(RED_AT_BASE) == 3


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted((k, v) for k, v in globals().items() if k.startswith("test_")):
        try:
            fn()
            print(f"  ✓ {name}")
        except Exception as e:
            failures += 1
            print(f"  ✗ {name}: {type(e).__name__}: {e}")
    print(f"\n{failures} failure(s)")
    sys.exit(1 if failures else 0)
