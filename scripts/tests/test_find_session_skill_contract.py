#!/usr/bin/env python3
"""The SHIPPED `find-session` skill body, pinned against the code it describes.

🔴 WHY THIS FILE EXISTS. `claude/skills/find-session/SKILL.md` ships to both
hosts via home-manager and is the interface an agent reads before branching on an
exit code — it is PAYLOAD. Nothing in `scripts/tests/` referenced it, and it
shipped two false claims at once:

  * "`3` — `--tail` could not resolve to one window on a **FULLY MEASURED
    fleet**". Measured false: two live matches with a host DOWN also exits 3,
    while the run itself prints "this candidate list is INCOMPLETE". A wrapper
    branching `rc == 3 => here are all the candidates` reports a complete list
    under this fleet's documented common degraded state.
  * "`4` = the live scan failed or no host answered", with no `--tail`
    qualifier while the neighbouring cases named the tail explicitly. Measured
    false: every source of that code is on the tail path, and without `--tail`
    a failed scan exits 0.

🔴 THE POINT IS THE DIRECTION OF DERIVATION. Correcting the prose would have
fixed the instance; the class is "a claim wider than the thing that enforces
it", which has now produced findings in three consecutive audit rounds. So the
sentences live in `EXIT_CONTRACT` in the script, the doc copies them verbatim,
this module pins the copy, AND — because a doc that merely agrees with a
constant is still two restatements of an unchecked belief — it pins each
sentence against the BEHAVIOUR it describes.

Hermetic: the live subprocess seam (`RUN`) is replaced in every behavioural
probe, so nothing here spawns `session-manager` or reads a real tmux server.
"""
from __future__ import annotations

import ast
import contextlib
import importlib.machinery
import importlib.util
import inspect
import io
import json
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPTS = REPO / "scripts"
SKILL_MD = REPO / "claude" / "skills" / "find-session" / "SKILL.md"


def _load(path, name):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_file_location(name, str(path), loader=loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


fs = _load(SCRIPTS / "find-session.py", "fs_skill_contract")


# 🔴 GREEN AT 6914aa33 — and for these, that is the FINDING, not a weakness.
# The audit's second false claim was that exit 4 meant "the live scan failed",
# unqualified. The CODE was already right: a failed scan without `--tail` always
# exited 0. Only the DOC was wrong. So the behavioural probes pass at the audited
# tip and the doc probes do not — which is exactly the shape of "a claim wider
# than the thing that enforces it", and the reason this module pins BOTH.
#
# No counts here; see the note in `test_find_session_live.py` above
# `R1_INVARIANT_GUARDS` for why a hand-typed matrix is not kept in-tree.
R3_GREEN_AT_AUDITED_TIP = frozenset({
    "test_exit_3_ALSO_arises_on_a_fully_measured_fleet",
    "test_exit_4_is_TAIL_ONLY_a_failed_scan_without_tail_still_exits_0",
    "test_exit_4_DOES_arise_when_the_same_failure_happens_under_tail",
    # This ledger's own gate — no behaviour to regress.
    "test_the_R3_ledger_names_only_tests_that_exist",
})

# 🔴 GREEN AT 0c874add, AND THAT IS THE FINDING AGAIN. Round 5's two 🟡 were GAPS
# IN GUARDS, not broken artifacts: the shipped table and the code were correct at
# that sha, so a guard that now catches the hazard passes there too. Their
# evidence is the MUTATION sweep against the auditor's own planted hazards — the
# transposed exit rows, the `else:`-branch return and the bare literal `4`, each
# of which the auditor measured GREEN against the old guards.
#
# A red-at-base row would be the wrong claim for these, and offering one would be
# the overstatement this ladder keeps finding.
R4_GREEN_AT_AUDITED_TIP = frozenset({
    "test_the_doc_table_PAIRS_each_code_with_its_own_contract_sentence",
    "test_the_tail_path_whitelist_EXCLUDES_the_else_branch",
    "test_the_source_scan_SEES_a_bare_integer_literal",
    "test_the_source_scan_IGNORES_the_declaration_and_the_contract_table",
    "test_renaming_the_tail_helper_makes_this_guard_RED_not_vacuous",
    "test_the_bare_literal_scan_CAN_fire",
    # two of this parametrised test's three causes were already exercised; only
    # the `--since` cause is new behaviour, and that param IS red at base.
    "test_every_CAUSE_the_exit_2_sentence_NAMES_really_exits_2",
})


def test_the_R4_ledger_names_only_tests_that_exist():
    assert R4_GREEN_AT_AUDITED_TIP, "the ledger is empty — gate wired to nothing"
    for entry in R4_GREEN_AT_AUDITED_TIP:
        assert entry.split("[", 1)[0] in globals(), (
            f"{entry!r} is listed in the R4 ledger but no such test exists")


def test_the_R3_ledger_names_only_tests_that_exist():
    assert R3_GREEN_AT_AUDITED_TIP, "the ledger is empty — gate wired to nothing"
    for entry in R3_GREEN_AT_AUDITED_TIP:
        assert entry.split("[", 1)[0] in globals(), (
            f"{entry!r} is listed in the R3 ledger but no such test exists")


def _norm(text: str) -> str:
    """Whitespace-run normalisation and nothing else — line WRAPPING is cosmetic
    and must not decide a verdict; wording is not. Same normaliser the other
    prose gates in this repo use."""
    return " ".join(text.split())


@pytest.fixture
def body():
    return SKILL_MD.read_text(encoding="utf-8")


# =========================================================================== #
# THE EXIT-CODE TABLE — doc vs constant, BOTH directions
# =========================================================================== #
def test_the_skill_exists_and_the_contract_is_not_empty(body):
    """POSITIVE CONTROL before any verdict: a gate reading an empty file or an
    empty constant would pass every assertion below by vacuity."""
    assert SKILL_MD.is_file()
    assert body.strip()
    assert fs.EXIT_CONTRACT, "EXIT_CONTRACT is empty — the gate is wired to nothing"
    assert len(fs.EXIT_CONTRACT) >= 4


def _documented_exit_table(body: str) -> dict:
    """The shipped table as `{code: normalised sentence}` — the PAIRING.

    🔴 THIS IS THE WHOLE FIX FOR THE ROUND-4 GAP. Two guards used to stand here:
    one asked whether each sentence appeared SOMEWHERE in the file, the other
    compared only the SET of codes. Neither read which sentence sat beside which
    code, so TRANSPOSING the exit-3 and exit-4 rows — making the shipped skill
    say 3 means "something the tail needed was NOT measured" and 4 means "could
    not resolve to exactly one live window", exactly inverted — left 122/122
    passing, and 377/377 across every generic skill gate. Nothing else in the
    tree reads this file.

    The failure that buys is this PR's own founding argument, relocated to the
    caller: a wrapper branches `rc == 3 ⇒ report unmeasured` / `rc == 4 ⇒ here
    are the candidates`, and under the inverted table treats an ambiguous
    multi-window match as a scan failure and an unmeasured tail as a settled
    candidate list.
    """
    return {int(code): _norm(text)
            for code, text in re.findall(r"^- `(\d)` — (.*)$", body, re.M)}


def test_the_doc_table_PAIRS_each_code_with_its_own_contract_sentence(body):
    """🔴 ONE assertion replacing two weaker ones — equality on the MAPPING.

    Set-of-codes plus sentences-appear-somewhere is satisfied by any
    permutation. Equality on `{code: sentence}` is not.
    """
    documented = _documented_exit_table(body)
    declared = {code: _norm(text) for code, text in fs.EXIT_CONTRACT}
    assert documented, (
        "no `- `N` — ...` rows parsed out of the shipped skill body — the table "
        "moved or was reformatted, and this gate is reading nothing. Re-point "
        "it rather than deleting it.")
    assert documented == declared, (
        "the shipped exit-code table disagrees with `EXIT_CONTRACT`:\n"
        + "".join(
            f"  exit {c}:\n    doc   : {documented.get(c, '<MISSING>')!r}\n"
            f"    script: {declared.get(c, '<NOT DECLARED>')!r}\n"
            for c in sorted(set(documented) | set(declared))
            if documented.get(c) != declared.get(c))
        + "Reword the CONSTANT and copy it here, never the other way round — "
          "the doc is the derived artifact. A code paired with ANOTHER code's "
          "sentence is the failure this replaced two weaker guards to catch.")


def test_the_contract_codes_are_the_scripts_own_EXIT_constants():
    """The sentences must describe the constants the code actually returns, not
    a parallel set of integers."""
    declared = {code for code, _ in fs.EXIT_CONTRACT}
    assert declared == {fs.EXIT_OK, fs.EXIT_USAGE, fs.EXIT_AMBIGUOUS,
                        fs.EXIT_UNAVAILABLE}
    assert len(declared) == 4, "two codes collapsed onto one integer"


def test_no_code_appears_twice_in_the_contract():
    codes = [code for code, _ in fs.EXIT_CONTRACT]
    assert len(codes) == len(set(codes))


# =========================================================================== #
# ...AND THE SENTENCES PINNED AGAINST BEHAVIOUR
#
# A doc agreeing with a constant is still two copies of an unchecked belief.
# These are the two claims that were FALSE, executed.
# =========================================================================== #
def _run(argv, run, archive=None):
    """Drive `main()` with the subprocess seam replaced. Returns (rc, out, err)."""
    old_run, old_archive = fs.RUN, fs.archive_search
    fs.RUN = run
    fs.archive_search = lambda a, since: list(archive or [])
    out, err = io.StringIO(), io.StringIO()
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = fs.main(list(argv))
    finally:
        fs.RUN, fs.archive_search = old_run, old_archive
    return rc, out.getvalue(), err.getvalue()


ROW = {
    "kind": "tmux", "host": "workbench", "session": "scratch3",
    "window_index": "2", "label": "violet", "label_source": "codename",
    "hotkey": "v", "hotkey_display": "Alt+v", "path": "/w/zzsigma",
    "task": "zzterm synthetic", "runtime": "claude", "claude": True,
    "busy": True, "status": "busy", "age_secs": 60.0, "age_source": "ledger",
    "waiting_probable": False, "waiting_signals": [], "waiting_status": "ok",
    "unsent_prompt": None, "unsent_prompt_status": "ok",
    "claude_session_id": "aaaaaaaa-1111-4222-8333-444444444444",
}
ROW_TWO = dict(ROW, session="scratch5", window_index="7",
               claude_session_id="bbbbbbbb-2222-4333-8444-555555555555")


def _report(rows, unreachable=()):
    hosts = {"workbench": {"reachable": True, "error": None,
                           "windows_measured": True, "windows": list(rows)}}
    for h in unreachable:
        hosts[h] = {"reachable": False, "error": "ssh: no route",
                    "windows_measured": False, "windows": []}
    return {"view": "lean", "hosts": hosts,
            "filters": {"match": ["zzterm"],
                        "match_fields": ["task", "label", "codename"]},
            "summary": {"total_sessions": len(rows)}}


def _runner(report, boom=None):
    def run(argv, timeout=None):
        if boom is not None:
            raise boom
        if "tail" in argv:
            return 0, "synthetic scrollback\n", ""
        return (0 if report["hosts"]["workbench"]["windows"] else 3,
                json.dumps(report), "")
    return run


def test_exit_3_carries_NO_claim_that_the_fleet_was_fully_measured():
    """🔴 THE FIRST FALSE SENTENCE, EXECUTED. Two live matches with `laptop`
    down exits 3 — the doc used to say 3 meant a FULLY measured fleet, so a
    wrapper branching on it would report an incomplete candidate list as
    complete."""
    rc, out, _ = _run(["zzterm", "--live", "--tail", "20"],
                      _runner(_report([ROW, ROW_TWO], unreachable=("laptop",))))
    assert rc == fs.EXIT_AMBIGUOUS
    assert "candidate list is INCOMPLETE" in out, (
        "fixture broken: this run must be the partial-fleet ambiguous case")
    # ...and the doc no longer claims otherwise.
    assert "FULLY measured fleet" not in SKILL_MD.read_text(encoding="utf-8")


def test_exit_3_ALSO_arises_on_a_fully_measured_fleet():
    """The other half, so the sentence is not simply inverted: with every host
    answering, an ambiguous match is still 3."""
    rc, out, _ = _run(["zzterm", "--live", "--tail", "20"],
                      _runner(_report([ROW, ROW_TWO])))
    assert rc == fs.EXIT_AMBIGUOUS
    assert "candidate list is INCOMPLETE" not in out


@pytest.mark.parametrize("argv", [
    ["zzterm", "--live"],
    ["zzterm", "--live", "--json"],
], ids=["text", "json"])
def test_exit_4_is_TAIL_ONLY_a_failed_scan_without_tail_still_exits_0(argv):
    """🔴 THE SECOND FALSE SENTENCE, EXECUTED. The doc said 4 meant "the live
    scan failed or no host answered" with no `--tail` qualifier — but every
    source of that code is on the tail path, so a failed scan WITHOUT `--tail`
    exits 0 and reports the failure in prose instead."""
    rc, out, _ = _run(argv, _runner(_report([]), boom=OSError("no such file")),
                      archive=[])
    assert rc == fs.EXIT_OK, (
        "a failed scan without --tail no longer exits 0; the contract sentence "
        "for exit 4 says it does")
    if "--json" not in argv:
        assert "LIVE: SCAN FAILED" in out


def test_exit_4_DOES_arise_when_the_same_failure_happens_under_tail():
    """The positive half of the same sentence — otherwise "4 is unreachable"
    would satisfy the probe above."""
    rc, _, _ = _run(["zzterm", "--live", "--tail", "20"],
                    _runner(_report([]), boom=OSError("no such file")),
                    archive=[])
    assert rc == fs.EXIT_UNAVAILABLE


def _tail_path_lines(tree) -> set:
    """Line numbers that are DEFINITIONALLY on the `--tail` path.

    🔴 `node.body` ONLY — NOT `node.lineno..node.end_lineno`.

    `ast.If.end_lineno` spans the `orelse`, so whitelisting the whole node marks
    the `else:` / `elif` body of `if a.tail is not None:` as on the tail path
    when it is definitionally the NO-`--tail` path — the exact complement.
    Measured: a `return EXIT_UNAVAILABLE` planted in such an `else:` was
    computed as ALLOWED and the suite stayed 122/122 green.
    """
    allowed = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_tail_outcome":
            allowed |= set(range(node.lineno, node.end_lineno + 1))
        if isinstance(node, ast.If) and "a.tail is not None" in ast.unparse(node.test):
            for stmt in node.body:          # the TRUE branch, and nothing else
                allowed |= set(range(stmt.lineno, stmt.end_lineno + 1))
    return allowed


def _contract_lines(tree) -> set:
    """`EXIT_CONTRACT`'s own span — it names the constant as DATA, not a path.

    Empty when the tree has no such assignment, so the synthetic trees the
    controls below build do not have to carry one. That the REAL module HAS one
    is asserted separately — an empty skip set there would silently widen what
    this excludes.
    """
    node = next((n for n in ast.walk(tree)
                 if isinstance(n, ast.Assign)
                 and any(getattr(t, "id", "") == "EXIT_CONTRACT"
                         for t in n.targets)), None)
    return set() if node is None else set(range(node.lineno, node.end_lineno + 1))


def exit_unavailable_sources(tree) -> list:
    """Every line that can PRODUCE exit 4 — by name OR by literal.

    Factored out so the negative controls below grade the REAL predicate rather
    than a re-implementation of it.

    🔴 THE LITERAL HALF MATTERS. An `ast.Name` scan cannot see `return 4`, so
    the previous version was blind to the plainest possible way of writing the
    hazard — measured, and green. Declarations (`EXIT_UNAVAILABLE = 4`) and the
    contract table are excluded: they are where the value is DEFINED and
    DESCRIBED, not returned.
    """
    declarations = {n.lineno for n in ast.walk(tree)
                    if isinstance(n, ast.Assign)
                    and any(getattr(t, "id", "") == "EXIT_UNAVAILABLE"
                            for t in n.targets)}
    skip = declarations | _contract_lines(tree)
    by_name = {n.lineno for n in ast.walk(tree)
               if isinstance(n, ast.Name) and n.id == "EXIT_UNAVAILABLE"}
    by_literal = {n.lineno for n in ast.walk(tree)
                  if isinstance(n, ast.Constant) and n.value is not True
                  and isinstance(n.value, int) and n.value == fs.EXIT_UNAVAILABLE}
    return sorted((by_name | by_literal) - skip)


@pytest.mark.parametrize("argv,why", [
    (["zzterm", "--since", "not-a-date"], "an unparseable `--since`"),
    (["zzterm", "--live", "--limit", "0"], "`--limit` below 1"),
    (["zzterm", "--tail", "5"], "`--tail` without `--live`"),
], ids=["since", "limit", "tail-without-live"])
def test_every_CAUSE_the_exit_2_sentence_NAMES_really_exits_2(argv, why):
    """🔴 The module docstring claims this file "pins each sentence against the
    BEHAVIOUR it describes". The exit-2 sentence names three causes and only two
    were exercised — `--since` was added to the prose and asserted nowhere.

    A sentence that enumerates causes is a claim per cause.
    """
    assert _norm(why) in _norm(dict(fs.EXIT_CONTRACT)[fs.EXIT_USAGE]), (
        f"{why!r} is not actually named in the exit-2 sentence — this probe and "
        "the contract have drifted apart")
    rc, _, err = _run(argv, _runner(_report([])), archive=[])
    assert rc == fs.EXIT_USAGE, f"{why} did not exit {fs.EXIT_USAGE}"
    assert err.strip(), "it exited 2 without telling the operator why"


def test_no_exit_path_uses_a_BARE_LITERAL_instead_of_the_constant():
    """🔴 THE COUPLING THAT WOULD SPLIT SILENTLY. The `--since` path used
    `sys.exit(2)` — correct today, and named by the exit-2 sentence, but a
    renumbering of `EXIT_USAGE` would move the constant and leave the literal
    behind, breaking a documented pair with nothing to notice.

    Every exit value the contract declares must be produced through its NAME.
    Declarations and the contract table itself are excluded — that is where the
    values are defined and described.
    """
    tree = ast.parse(inspect.getsource(fs))
    declared = {code for code, _ in fs.EXIT_CONTRACT}
    names = {"EXIT_OK", "EXIT_USAGE", "EXIT_AMBIGUOUS", "EXIT_UNAVAILABLE"}
    skip = _contract_lines(tree) | {
        n.lineno for n in ast.walk(tree)
        if isinstance(n, ast.Assign)
        and any(getattr(t, "id", "") in names for t in n.targets)}

    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Return, ast.Call)):
            continue
        # `return <int>` and `sys.exit(<int>)`
        if isinstance(node, ast.Call):
            if ast.unparse(node.func) not in ("sys.exit", "exit"):
                continue
            args = node.args
        else:
            args = [node.value] if node.value is not None else []
        for arg in args:
            if (isinstance(arg, ast.Constant) and arg.value is not True
                    and isinstance(arg.value, int) and arg.value in declared
                    and node.lineno not in skip):
                offenders.append((node.lineno, arg.value))
    assert not offenders, (
        "an exit value the contract declares is produced as a BARE LITERAL "
        f"rather than through its constant: {offenders}. Use the EXIT_* name so "
        "a renumbering cannot split the code from the sentence that documents it.")


def test_the_bare_literal_scan_CAN_fire():
    """POSITIVE CONTROL — a scan that matched nothing would pass the test above
    on any tree at all."""
    tree = ast.parse(f"import sys\ndef f():\n    sys.exit({fs.EXIT_USAGE})\n")
    found = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call) and ast.unparse(n.func) == "sys.exit"
             and n.args and isinstance(n.args[0], ast.Constant)
             and n.args[0].value in {c for c, _ in fs.EXIT_CONTRACT}]
    assert found, "the offender shape this scan looks for is unmatchable"


def test_every_EXIT_UNAVAILABLE_source_is_on_the_tail_path():
    """🔴 STRUCTURAL, so the sentence stays true of code nobody has written yet.

    The `--tail ONLY` claim is about WHERE the code can arise, and a behavioural
    probe only samples the paths it thought of. Every source of the value — by
    NAME or by LITERAL — must sit inside `_tail_outcome` or inside the TRUE
    branch of `main`'s `if a.tail is not None:`.

    Two blind spots this had in round 4, both measured green with the hazard
    planted, both closed here: the `else:` body counted as tail-path (see
    `_tail_path_lines`), and `return 4` was invisible (see
    `exit_unavailable_sources`).
    """
    tree = ast.parse(inspect.getsource(fs))
    sources = exit_unavailable_sources(tree)
    assert sources, "no EXIT_UNAVAILABLE source found — gate wired to nothing"
    stray = sorted(set(sources) - _tail_path_lines(tree))
    assert not stray, (
        f"exit {fs.EXIT_UNAVAILABLE} can be produced outside the --tail path at "
        f"lines {stray}. The shipped skill body says it is `--tail` ONLY; either "
        "keep it that way or change EXIT_CONTRACT and the doc together.")


def test_the_tail_path_whitelist_EXCLUDES_the_else_branch():
    """🔴 NEGATIVE CONTROL for blind spot 1, graded by the REAL helper.

    `if a.tail is not None: ... else: ...` — the `else` body is the no-`--tail`
    path by definition, and `end_lineno` spanning it is what made the old
    whitelist cover its own complement.
    """
    tree = ast.parse(
        "def main(a):\n"
        "    if a.tail is not None:\n"
        "        x = 1\n"
        "    else:\n"
        "        return 4\n")
    allowed = _tail_path_lines(tree)
    assert 3 in allowed, "the TRUE branch must be on the tail path"
    assert 5 not in allowed, (
        "the else: branch is whitelisted as tail-path — that is the complement "
        "of the tail path, and it is where a stray return would hide")


def test_the_source_scan_SEES_a_bare_integer_literal():
    """🔴 NEGATIVE CONTROL for blind spot 2, graded by the REAL helper. An
    `ast.Name` scan cannot see `return 4`."""
    tree = ast.parse(f"def f():\n    return {fs.EXIT_UNAVAILABLE}\n")
    assert exit_unavailable_sources(tree) == [2], (
        "a bare integer literal equal to EXIT_UNAVAILABLE is invisible to the "
        "source scan — the plainest way to write the hazard")


def test_the_source_scan_IGNORES_the_declaration_and_the_contract_table():
    """POSITIVE CONTROL in the other direction: a scan that flagged the
    constant's own definition, or the table that DESCRIBES it, would be
    permanently red — worse than no gate."""
    tree = ast.parse(inspect.getsource(fs))
    sources = set(exit_unavailable_sources(tree))
    assert sources & _tail_path_lines(tree) == sources, (
        "the clean tree must have every source on the tail path")
    # 🔴 The REAL module must HAVE an EXIT_CONTRACT span. `_contract_lines`
    # returns empty for a tree without one, so an empty skip set here would mean
    # the exclusion is silently doing nothing rather than excluding the table.
    assert _contract_lines(tree), (
        "no EXIT_CONTRACT assignment found in the module — the contract-table "
        "exclusion is wired to nothing")
    assert not (sources & _contract_lines(tree))
    other_ints = [n.lineno for n in ast.walk(tree)
                  if isinstance(n, ast.Constant) and isinstance(n.value, int)
                  and n.value not in (fs.EXIT_UNAVAILABLE, True, False)]
    assert other_ints, "fixture: the module must contain other integer literals"
    assert not (sources & set(other_ints) - set(exit_unavailable_sources(tree)))


def test_renaming_the_tail_helper_makes_this_guard_RED_not_vacuous():
    """🔴 The failure mode a whitelist-based guard is prone to: if the anchor it
    keys on disappears, the allowed set empties and everything reads as stray —
    LOUD. Asserted so nobody "fixes" that into a silent pass."""
    src = inspect.getsource(fs).replace("_tail_outcome", "_renamed_outcome")
    tree = ast.parse(src)
    assert exit_unavailable_sources(tree), "sources vanished — that would be vacuous"
    assert set(exit_unavailable_sources(tree)) - _tail_path_lines(tree), (
        "renaming the tail helper left this guard green; it must go RED so the "
        "anchor's disappearance is visible rather than silently vacuous")


# =========================================================================== #
# THE ARCHIVE-ONLY FLAG LIST — named in the doc, owned by the script
# =========================================================================== #
# 🔴 THE ENUMERATION IS BRACKETED BY TWO ANCHOR PHRASES, not by the bullet.
#
# A `spelling in body` check is satisfied by the flag being named ANYWHERE, and
# a bullet-scoped check is satisfied by the CONTINUATION prose, which mentions
# `--opencode-only --live` a few lines further down inside the same bullet.
# Measured, twice: deleting `--opencode-only` from the enumeration survived both
# spellings of the guard. That is precisely the failure the round-3 audit found
# in the `__doc__` substring check — reproduced here while writing its
# replacement, which is why the bracketing is the point and not a detail.
#
# Same idiom, and same reason, as `test_session_manager_skill_size.py`'s
# CAVEAT_LIST_HEAD / _TAIL: bound the LIST, then compare the list.
FLAG_LIST_HEAD = "**These flags reach the ARCHIVE leg ONLY** —"
FLAG_LIST_TAIL = "— and the tool names them on stderr."


def _documented_archive_only_flags(body: str) -> list:
    """The flags the doc ENUMERATES, read from between the two anchors."""
    text = _norm(body)
    start = text.find(_norm(FLAG_LIST_HEAD))
    assert start != -1, (
        f"{FLAG_LIST_HEAD!r} not found in {SKILL_MD}. That phrase opens the "
        "archive-only enumeration and is what this gate uses to find it. If the "
        "bullet was reworded, re-point the anchor; if the enumeration was "
        "deleted, delete this gate in the same commit and say so.")
    end = text.find(_norm(FLAG_LIST_TAIL), start)
    assert end != -1, (
        f"{FLAG_LIST_TAIL!r} not found after the head anchor — the enumeration "
        "has no closing anchor, so this gate cannot bound it.")
    return re.findall(r"`(--[a-z-]+)`", text[start + len(_norm(FLAG_LIST_HEAD)):end])


def test_the_enumeration_slicer_really_bounds_the_LIST(body):
    """POSITIVE CONTROL on the slicer, because every verdict below is a
    statement about the SLICE. Its own bullet's continuation prose names
    `--opencode-only` and `--limit`; neither may be inside the bounded list by
    accident, or the two-way comparison below proves nothing."""
    text = _norm(body)
    start = text.find(_norm(FLAG_LIST_HEAD))
    end = text.find(_norm(FLAG_LIST_TAIL), start)
    sliced = text[start:end]
    assert 0 < len(sliced) < len(text) / 4, "the slice is empty or unbounded"
    assert "--limit" not in sliced, (
        "the slice reaches into the bullet's continuation prose — it would then "
        "be satisfied by a flag mentioned there rather than enumerated here")
    assert _documented_archive_only_flags(body), "the slicer parsed no flags"


def test_the_doc_enumerates_EXACTLY_the_ARCHIVE_ONLY_flags(body):
    """🔴 TWO-WAY. The doc used to open this bullet with a COUNT ("SIX flags…"),
    a claim nothing enforced — right the day it was written and silently wrong
    the moment a seventh is added. The count is gone; membership is pinned in
    BOTH directions, so the doc can neither drop a flag the script filters nor
    advertise one it does not."""
    documented = sorted(_documented_archive_only_flags(body))
    declared = sorted(s for _, s, _ in fs.ARCHIVE_ONLY_FLAGS)
    assert documented == declared, (
        "the shipped doc's archive-only enumeration disagrees with "
        "`ARCHIVE_ONLY_FLAGS`:\n"
        f"  doc enumerates : {documented}\n"
        f"  script declares: {declared}\n"
        f"  only in the doc   : {sorted(set(documented) - set(declared))}\n"
        f"  only in the script: {sorted(set(declared) - set(documented))}\n"
        "Being named elsewhere in the file does not count — a reader of this "
        "enumeration would not see it.")


def test_the_doc_does_not_carry_a_FLAG_COUNT_that_nothing_enforces(body):
    """A number in prose is the exact shape this round is removing. If a count
    is wanted, derive it; do not type it."""
    line = next((ln for ln in body.splitlines()
                 if "reach the ARCHIVE leg ONLY" in ln), None)
    assert line, "the archive-only bullet moved — re-point this gate"
    assert not re.search(r"\b(TWO|THREE|FOUR|FIVE|SIX|SEVEN|EIGHT|\d+)\s+flags",
                         line, re.I), (
        f"the archive-only bullet states a flag COUNT: {line!r}. A count drifts "
        "the moment a flag is added; name the flags, or derive the number.")
