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

import argparse
import contextlib
import importlib.machinery
import importlib.util
import io
import json
import os
import re
import sys
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


def test_every_contract_sentence_appears_VERBATIM_in_the_shipped_doc(body):
    """🔴 Whole normalised strings, never keywords. `claude/RULES.md`: "when the
    artifact under test IS prose, a guard on WORDS is walkable by REWORDING —
    pin the WHOLE normalised string". Both of the false claims this replaced
    were rewordings of true ones."""
    haystack = _norm(body)
    missing = [(code, txt) for code, txt in fs.EXIT_CONTRACT
               if _norm(txt) not in haystack]
    assert not missing, (
        "the shipped skill body no longer carries these EXIT_CONTRACT sentences "
        "verbatim:\n"
        + "".join(f"  exit {c}: {_norm(t)!r}\n" for c, t in missing)
        + "Reword the CONSTANT and copy it here, never the other way round — "
          "the doc is the derived artifact.")


def test_the_doc_states_every_code_and_INVENTS_none(body):
    """Both directions. A doc that documents a code the script cannot return
    sends a caller to write a branch that never fires."""
    documented = {int(m) for m in re.findall(r"^- `(\d)` — ", body, re.M)}
    declared = {code for code, _ in fs.EXIT_CONTRACT}
    assert documented == declared, (
        f"doc documents {sorted(documented)}, script declares {sorted(declared)}")


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


def test_every_EXIT_UNAVAILABLE_source_is_on_the_tail_path():
    """🔴 STRUCTURAL, so the sentence stays true of code nobody has written yet.

    The `--tail ONLY` claim is about WHERE the code can arise, and a behavioural
    probe only samples the paths it thought of. This asserts that every textual
    source of `EXIT_UNAVAILABLE` in the module is inside `_tail_outcome` or
    inside `main`'s `if a.tail is not None:` block — so a NEW return added
    outside the tail path fails here rather than silently falsifying the doc.
    """
    import ast
    import inspect
    src = inspect.getsource(fs)
    tree = ast.parse(src)

    allowed_lines = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_tail_outcome":
            allowed_lines |= set(range(node.lineno, node.end_lineno + 1))
        if isinstance(node, ast.If):
            # `if a.tail is not None:` — the tail block inside `main`
            test = ast.unparse(node.test)
            if "a.tail is not None" in test:
                allowed_lines |= set(range(node.lineno, node.end_lineno + 1))

    uses = [n.lineno for n in ast.walk(tree)
            if isinstance(n, ast.Name) and n.id == "EXIT_UNAVAILABLE"]
    assert uses, "no EXIT_UNAVAILABLE reference found — gate wired to nothing"
    stray = sorted(set(uses) - allowed_lines
                   - {n.lineno for n in ast.walk(tree)
                      if isinstance(n, ast.Assign)
                      and any(getattr(t, "id", "") == "EXIT_UNAVAILABLE"
                              for t in n.targets)}
                   - {c for c, _ in [(0, 0)]})
    # the EXIT_CONTRACT tuple itself references the constant; that is data, not
    # a return path, so exclude the contract's own lines.
    contract = next(n for n in ast.walk(tree)
                    if isinstance(n, ast.Assign)
                    and any(getattr(t, "id", "") == "EXIT_CONTRACT"
                            for t in n.targets))
    stray = [ln for ln in stray
             if not (contract.lineno <= ln <= contract.end_lineno)]
    assert not stray, (
        f"EXIT_UNAVAILABLE is returned outside the --tail path at lines {stray}. "
        "The shipped skill body says exit 4 is `--tail` ONLY; either keep it "
        "that way or change EXIT_CONTRACT and the doc together.")


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
