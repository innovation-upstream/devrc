#!/usr/bin/env python3
"""Hermetic tests for `scripts/waiting-windows`.

HOUSE PATTERN. Every impure source is injected: `reconcile` and `build_report`
take a report dict, a stamp dict and a clock, so nothing here touches tmux, ssh,
ClickHouse or the operator's real `$HOME`. The three tests that DO touch a disk
drive the real writer against a `tmp_path` `$HOME` on purpose — that is the
on-disk artifact-name pin, and it is worthless against a fake writer.

🔴 FIXTURE VALUES ARE PAIRWISE DISTINCT AND DISTINCT FROM EVERY CONSTANT
ASSERTED AGAINST. This repo has been bitten five times by a fixture whose value
equals the constant it tests, so a mutant that hardcodes the literal survives a
fully green suite. Concretely:

  * `THRESHOLD` is 7331, never `DEFAULT_WAIT_THRESHOLD` (14400). A mutant that
    ignores the passed threshold and uses the default is killed by every
    boundary test, because 7331 and 14400 put the same fixture ages on opposite
    sides of the comparison.
  * Every age below is distinct from 7331, from 14400 and from every other age,
    and none is a multiple of either threshold — so a mutant that scales,
    truncates or swaps one age for another cannot land on a passing value.
  * Hosts are `alpha-host`/`beta-host`, never `workbench`/`laptop`: a mutant
    that hardcodes a real host name finds nothing.
  * The two tmux generations (`500311`, `700923`) share no digit pattern, so a
    generation mutant cannot produce one from the other.
  * Window ids `@307`, `@911`, `@4242`, `@57` share no prefix with each other
    and none is `@0` — the value a fresh tmux server issues, and therefore the
    one a mutant could produce by accident.

🔴 NO CAPTURED TEXT. Every task title, label and signal line below is invented.
None of it is a real prompt, message body or transcript line; this repo is
public and the gates that enforce that are blind to `.py` files.

  run:  python -m pytest scripts/tests/test_waiting_windows.py -q
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import io
import json
import os
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPT = os.path.normpath(os.path.join(_HERE, "..", "waiting-windows"))
_SM_SCRIPT = os.path.normpath(os.path.join(_HERE, "..", "session-manager"))


def _load(path, name):
    loader = importlib.machinery.SourceFileLoader(name, path)
    spec = importlib.util.spec_from_file_location(name, path, loader=loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


ww = _load(_SCRIPT, "waiting_windows")
sm = _load(_SM_SCRIPT, "session_manager_for_ww")

# --------------------------------------------------------------------------- #
# Fixture constants — see the module docstring for why each is what it is.
# --------------------------------------------------------------------------- #
NOW = 1786800000.0            # a fixed epoch; nothing in the module names it
THRESHOLD = 7331              # NOT 14400 (the module's own default)
HOST_A = "alpha-host"
HOST_B = "beta-host"
GEN_A = "500311"
GEN_B = "700923"
WID_1 = "@307"
WID_2 = "@911"
WID_3 = "@4242"
WID_4 = "@57"

# Ages, all distinct from each other, from THRESHOLD and from 14400.
AGE_TINY = 3121               # well under THRESHOLD
AGE_BIG = 51217               # well over THRESHOLD
AGE_HUGE = 196103             # the "stranded for days" shape
AGE_MID = 9973                # over THRESHOLD, under AGE_BIG


def row(window_id=WID_1, *, claude=True, busy=False, probable=False,
        signals=(), status="idle", waiting_status="ok", age=AGE_TINY,
        session="sess-one", index="1", label="label-one", hotkey=None,
        task="synthetic task one", unsent=None, unsent_status="ok",
        include_window_id=True):
    """A session-manager row. Only the fields `waiting-windows` reads are
    populated; everything else is deliberately absent so a mutant that starts
    reading an unpopulated field fails rather than silently working."""
    r = {
        "kind": "tmux",
        "session": session,
        "window_index": index,
        "label": label,
        "hotkey": hotkey,
        "path": "/synthetic/path/one",
        "task": task,
        "claude": claude,
        "busy": busy,
        "status": status,
        "age_secs": age,
        "age_source": "ledger" if age is not None else None,
        "waiting_probable": probable,
        "waiting_signals": [{"signal": s, "line": "synthetic line for %s" % s}
                            for s in signals],
        "waiting_status": waiting_status,
        "unsent_prompt": unsent,
        "unsent_prompt_status": unsent_status,
        "claude_session_id": "synthetic-session-%s" % window_id.lstrip("@"),
    }
    if include_window_id:
        r["window_id"] = window_id
    return r


def report(hosts=None, *, summary=None, ledger_pids=None, ts="synthetic-ts"):
    """A session-manager report. `hosts` is {host: [rows]} or {host: hostblock}."""
    hosts = {HOST_A: []} if hosts is None else hosts
    blocks = {}
    pids = {HOST_A: GEN_A, HOST_B: GEN_B} if ledger_pids is None else ledger_pids
    for host, val in hosts.items():
        if isinstance(val, dict):
            blocks[host] = val
        else:
            blocks[host] = {
                "reachable": True, "error": None,
                "windows_measured": True, "windows_error": None,
                "captures_measured": True, "captures_status": "ok",
                "captures_seen": len(val), "windows": val,
            }
    return {
        "ts": ts,
        "hosts": blocks,
        "ledger": {"hosts": {h: {"tmux_pid": pids.get(h)} for h in blocks}},
        "summary": summary or {"hosts_unreachable": [], "windows_unmeasured": []},
    }


def run(rep, stamps=None, now=NOW, threshold=THRESHOLD):
    return ww.reconcile(rep, dict(stamps or {}), now, threshold)


def by_id(rows):
    return {r["window_id"]: r for r in rows}


# =========================================================================== #
# §1 — CLASSIFY, DON'T LUMP
# =========================================================================== #
def test_a_trailing_question_row_is_its_own_kind():
    out, _ = run(report({HOST_A: [row(probable=True, signals=("trailing_question",))]}))
    assert [r["kind"] for r in out["rows"]] == ["trailing_question"]


def test_a_selection_menu_row_is_its_own_kind():
    out, _ = run(report({HOST_A: [row(probable=True, signals=("selection_menu",))]}))
    assert [r["kind"] for r in out["rows"]] == ["selection_menu"]


def test_a_context_exhausted_row_is_its_own_kind():
    out, _ = run(report({HOST_A: [row(probable=True, signals=("context_exhausted",))]}))
    assert [r["kind"] for r in out["rows"]] == ["context_exhausted"]


def test_an_idle_claude_window_with_no_signal_is_its_own_kind():
    """The WIDE population the operator chose. A window with no waiting signal
    at all is still a window nobody is looking at."""
    out, _ = run(report({HOST_A: [row(probable=False, status="stale")]}))
    assert [r["kind"] for r in out["rows"]] == ["idle_no_signal"]


def test_the_four_kinds_are_counted_separately_and_never_summed_into_one():
    """🔴 The point of the classification. One undifferentiated number would
    tell the operator to walk to a terminal without saying what for, and would
    fold finished work (idle_no_signal) into the blocked count."""
    rep = report({HOST_A: [
        row(WID_1, probable=True, signals=("trailing_question",), age=AGE_BIG),
        row(WID_2, probable=True, signals=("selection_menu",), age=AGE_HUGE),
        row(WID_3, probable=True, signals=("context_exhausted",), age=AGE_MID),
        row(WID_4, probable=False, age=AGE_BIG),
    ]})
    out, _ = run(rep)
    assert out["counts"]["over_threshold_by_kind"] == {
        "context_exhausted": 1, "selection_menu": 1,
        "trailing_question": 1, "idle_no_signal": 1,
    }
    assert out["counts"]["over_threshold"] == 4


def test_the_per_kind_roll_up_publishes_every_kind_even_at_zero():
    """A bucket absent because it did not occur and a bucket absent because the
    code forgot it look identical. The whole vocabulary is always published."""
    out, _ = run(report({HOST_A: [row(probable=False, age=AGE_BIG)]}))
    assert set(out["counts"]["over_threshold_by_kind"]) == set(ww.ALL_KINDS)
    assert out["counts"]["over_threshold_by_kind"]["trailing_question"] == 0


def test_precedence_picks_the_hardest_block_and_kinds_all_keeps_the_rest():
    out, _ = run(report({HOST_A: [row(
        probable=True,
        signals=("trailing_question", "selection_menu", "context_exhausted"))]}))
    r = out["rows"][0]
    assert r["kind"] == "context_exhausted"
    assert r["kinds_all"] == ["context_exhausted", "selection_menu",
                              "trailing_question"]


def test_precedence_selection_menu_beats_trailing_question():
    out, _ = run(report({HOST_A: [row(
        probable=True, signals=("trailing_question", "selection_menu"))]}))
    assert out["rows"][0]["kind"] == "selection_menu"


def test_a_busy_window_is_not_in_the_waiting_population_at_all():
    """🔴 The row shape that made `age_secs` untrustworthy: measured waiting
    while BUSY at an age of 2.5 seconds. A busy window is working."""
    out, _ = run(report({HOST_A: [
        row(probable=True, signals=("trailing_question",), busy=True,
            age=AGE_HUGE)]}))
    assert out["rows"] == []
    assert out["counts"]["waiting_population"] == 0


def test_a_bare_shell_row_is_not_in_the_population():
    out, _ = run(report({HOST_A: [row(claude=False, age=AGE_HUGE)]}))
    assert out["rows"] == []


def test_an_unknown_signal_keeps_its_own_name_and_is_reported_as_unclassified():
    """A signal session-manager grows and this build does not know must arrive
    under its own name, never filed as one of ours."""
    out, _ = run(report({HOST_A: [row(
        probable=True, signals=("synthetic_future_signal",), age=AGE_BIG)]}))
    r = out["rows"][0]
    assert r["kind"] == "synthetic_future_signal"
    assert r["unclassified_signals"] == ["synthetic_future_signal"]


def test_probable_with_no_signals_is_an_explicitly_unclassified_block():
    out, _ = run(report({HOST_A: [row(probable=True, signals=(), age=AGE_BIG)]}))
    assert out["rows"][0]["kind"] == "waiting_unclassified"


def test_the_signal_precedence_covers_EXACTLY_session_managers_WAITING_SIGNALS():
    """🔴 THE SEAM GUARD. Two components each hermetically tested can still be
    broken TOGETHER. `waiting-windows` has its own precedence tuple and
    `session-manager` owns the signal vocabulary; nothing else makes them
    disagree audibly.

    Asserted as SET EQUALITY, so it fails when session-manager's set GROWS (a
    new signal would fall through to `unclassified_signals` and be reported as a
    nameless block) and when it SHRINKS (a stale name in our precedence would
    silently never match). Both directions, one assertion.
    """
    assert set(ww.SIGNAL_PRECEDENCE) == set(sm.WAITING_SIGNALS)
    # ...and the ORDER here is ours, not a copy of theirs: the precedence is a
    # claim about how hard each block is, which session-manager does not make.
    assert ww.SIGNAL_PRECEDENCE[0] == "context_exhausted"


def test_ALL_KINDS_is_the_signal_vocabulary_plus_exactly_one_extra_bucket():
    assert set(ww.ALL_KINDS) - set(ww.SIGNAL_PRECEDENCE) == {ww.IDLE_NO_SIGNAL}


def test_classify_returns_THREE_states_and_unmeasured_is_one_of_them():
    """🔴 FOUND BY THE MUTATION SWEEP, and the reason `classify` owns the
    predicate alone. An earlier revision ALSO tested `waiting_status != "ok"` in
    `reconcile`, one line before calling `classify`. Deleting the check inside
    `classify` then changed nothing — the caller's copy always won, so the guard
    was UNREACHABLE and every test that appeared to cover it was covering the
    duplicate instead. This exercises `classify` DIRECTLY, so the guard is
    reached by a case no earlier check rejects.
    """
    assert ww.classify(row(probable=True, signals=("selection_menu",)))[:2] == (
        ww.TRIAGE_WAITING, "selection_menu")
    assert ww.classify(row(probable=None, waiting_status="uncaptured"))[:2] == (
        ww.TRIAGE_UNMEASURED, None)
    assert ww.classify(row(claude=False))[:2] == (ww.TRIAGE_NOT_IN_POPULATION, None)
    assert ww.classify(row(busy=True, probable=True,
                           signals=("trailing_question",)))[:2] == (
        ww.TRIAGE_NOT_IN_POPULATION, None)
    assert ww.classify(row(probable=False))[:2] == (
        ww.TRIAGE_WAITING, ww.IDLE_NO_SIGNAL)


def test_the_three_triage_states_are_distinct_values():
    """A mutant that aliased two of them would make `reconcile`'s branch on
    them collapse; they are the discriminant, so they must not be equal."""
    assert len({ww.TRIAGE_WAITING, ww.TRIAGE_NOT_IN_POPULATION,
                ww.TRIAGE_UNMEASURED}) == 3


def test_an_unmeasured_row_is_NOT_classified_as_not_in_population():
    """The two non-waiting states must not be collapsed: one means 'we looked
    and it is not waiting', the other means 'we did not look'. Only the second
    is counted under coverage."""
    assert ww.classify(row(probable=None, waiting_status="uncaptured"))[0] != \
        ww.classify(row(claude=False))[0]


# =========================================================================== #
# §2 — THE THRESHOLD, FROM BOTH SIDES
# =========================================================================== #
def test_a_wait_exactly_equal_to_the_threshold_is_NOT_over_it():
    """🔴 The boundary from the low side. The comparison is `>`, not `>=`, and
    a mutant that flips it is killed here and only here."""
    out, _ = run(report({HOST_A: [row(probable=True,
                                      signals=("trailing_question",),
                                      age=THRESHOLD)]}))
    r = out["rows"][0]
    assert r["waited_secs"] == THRESHOLD
    assert r["over_threshold"] is False
    assert out["counts"]["over_threshold"] == 0


def test_a_wait_one_second_over_the_threshold_IS_over_it():
    """The boundary from the high side. Together with the test above this pins
    the comparison exactly: an off-by-one in either direction fails one of the
    two."""
    out, _ = run(report({HOST_A: [row(probable=True,
                                      signals=("trailing_question",),
                                      age=THRESHOLD + 1)]}))
    assert out["rows"][0]["over_threshold"] is True
    assert out["counts"]["over_threshold"] == 1


def test_a_wait_one_second_under_the_threshold_is_not_over_it():
    out, _ = run(report({HOST_A: [row(probable=True,
                                      signals=("trailing_question",),
                                      age=THRESHOLD - 1)]}))
    assert out["rows"][0]["over_threshold"] is False


def test_the_threshold_actually_MOVES_the_verdict_for_one_fixed_row():
    """🔴 THE POSITIVE CONTROL for the threshold itself. Every test above could
    be satisfied by a mutant that hardcodes `over_threshold` from the age alone.
    Here ONE row is judged against two thresholds that straddle its age, and the
    verdict must invert. AGE_MID (9973) is between them and equal to neither, so
    neither threshold can be produced from it."""
    rep = report({HOST_A: [row(probable=True, signals=("selection_menu",),
                               age=AGE_MID)]})
    low, _ = run(rep, threshold=AGE_MID - 2000)
    high, _ = run(rep, threshold=AGE_MID + 2000)
    assert low["rows"][0]["over_threshold"] is True
    assert high["rows"][0]["over_threshold"] is False


def test_the_flag_beats_the_environment():
    secs, source = ww.resolve_threshold(THRESHOLD,
                                        {ww.THRESHOLD_ENV: str(AGE_BIG)})
    assert (secs, source) == (THRESHOLD, "flag")


def test_the_environment_is_used_when_no_flag_is_given():
    secs, source = ww.resolve_threshold(None, {ww.THRESHOLD_ENV: str(AGE_BIG)})
    assert (secs, source) == (AGE_BIG, "env")


def test_the_default_is_used_when_neither_is_given_and_says_so():
    secs, source = ww.resolve_threshold(None, {})
    assert (secs, source) == (ww.DEFAULT_WAIT_THRESHOLD, "default")


def test_a_non_integer_environment_value_falls_back_and_NAMES_the_reason():
    secs, source = ww.resolve_threshold(None, {ww.THRESHOLD_ENV: "four hours"})
    assert secs == ww.DEFAULT_WAIT_THRESHOLD
    assert "not an integer" in source and ww.THRESHOLD_ENV in source


def test_a_non_positive_environment_value_falls_back_and_NAMES_the_reason():
    """0 would make every window over threshold; a negative one likewise. Both
    are refused rather than obeyed."""
    for bad in ("0", "-1"):
        secs, source = ww.resolve_threshold(None, {ww.THRESHOLD_ENV: bad})
        assert secs == ww.DEFAULT_WAIT_THRESHOLD
        assert "not positive" in source


def test_the_default_threshold_sits_in_the_empty_band_the_data_measured():
    """INVARIANT GUARD, not a regression test — labelled as one. The measured
    distribution has an ANSWERED mode (~98 prompts in 22h, i.e. sub-hour) and a
    STRANDED mode (29.6h and 54.5h) with nothing between. This pins that the
    default lands strictly inside that gap, so a future retune cannot silently
    move it into either mode.
    """
    assert ww.DEFAULT_WAIT_THRESHOLD == 4 * 3600
    assert 3600 < ww.DEFAULT_WAIT_THRESHOLD < int(29.6 * 3600)


# =========================================================================== #
# §3 — THE CLOCK
# =========================================================================== #
def test_the_first_observation_falls_back_to_the_ledger_age_and_LABELS_it():
    """🔴 Never silently. On the first sight of a window the elapsed term is 0,
    so the ledger age is used — and `waited_source` says so, because
    `age_secs` measures time since the last ledger HEARTBEAT, not time since the
    window asked."""
    out, _ = run(report({HOST_A: [row(probable=True,
                                      signals=("trailing_question",),
                                      age=AGE_HUGE)]}))
    r = out["rows"][0]
    assert (r["waited_secs"], r["waited_source"]) == (AGE_HUGE, "ledger_age_fallback")


def test_a_first_observation_with_no_ledger_age_is_UNMEASURED_not_zero():
    """🔴 The elapsed term is 0 on a first sight. Publishing it would be a
    measured-looking zero for a window that has never been timed."""
    out, _ = run(report({HOST_A: [row(probable=True,
                                      signals=("selection_menu",), age=None)]}))
    r = out["rows"][0]
    assert r["waited_secs"] is None
    assert r["waited_source"] == "unmeasured"
    assert r["over_threshold"] is False
    assert out["counts"]["waited_unmeasured"] == 1


def test_once_elapsed_overtakes_the_stamped_age_the_clock_takes_over():
    """Second run: the window has now been observed waiting for longer than the
    ledger age we stamped, so OUR clock is the better number and says so."""
    rep = report({HOST_A: [row(probable=True, signals=("trailing_question",),
                               age=AGE_TINY)]})
    _, stamps = run(rep, now=NOW)
    later = NOW + AGE_BIG
    out, _ = run(rep, stamps, now=later)
    r = out["rows"][0]
    assert r["waited_source"] == "first_seen"
    assert r["waited_secs"] == AGE_BIG


def test_a_long_wait_does_NOT_regress_to_zero_on_the_second_run():
    """🔴 THE REGRESSION THIS DESIGN EXISTS TO PREVENT. A window already
    AGE_HUGE into its wait when the tool first runs gets `first_seen = now`.
    The naive rule — stamp, then report elapsed — would report AGE_TINY seconds
    on the next run and drop the fleet's oldest window below the threshold it
    was above one run earlier. The stamped age is carried and the larger term
    wins.

    AGE_HUGE (196103) and AGE_TINY (3121) share no digits and neither is a
    multiple of the other, so a mutant that scaled or truncated one cannot
    produce the other.
    """
    rep = report({HOST_A: [row(probable=True, signals=("trailing_question",),
                               age=AGE_HUGE)]})
    _, stamps = run(rep, now=NOW)
    out, _ = run(rep, stamps, now=NOW + AGE_TINY)
    r = out["rows"][0]
    assert r["waited_secs"] == AGE_HUGE
    assert r["waited_source"] == "ledger_age_fallback"
    assert r["over_threshold"] is True


def test_the_stamped_age_is_the_FIRST_runs_age_not_a_later_one():
    """A mutant that re-reads the current `age_secs` into the stamp on every run
    passes every test above. Here the report's age CHANGES between runs and the
    stamp must still carry the original."""
    first = report({HOST_A: [row(probable=True, signals=("selection_menu",),
                                 age=AGE_HUGE)]})
    _, stamps = run(first, now=NOW)
    second = report({HOST_A: [row(probable=True, signals=("selection_menu",),
                                  age=AGE_TINY)]})
    out, stamps2 = run(second, stamps, now=NOW + 60)
    key = ww.stamp_key(HOST_A, GEN_A, WID_1)
    assert stamps2[key]["age_at_first_seen"] == AGE_HUGE
    assert out["rows"][0]["waited_secs"] == AGE_HUGE


def test_first_seen_is_not_re_stamped_on_every_run():
    rep = report({HOST_A: [row(probable=True, signals=("trailing_question",),
                               age=AGE_TINY)]})
    _, s1 = run(rep, now=NOW)
    _, s2 = run(rep, s1, now=NOW + AGE_BIG)
    key = ww.stamp_key(HOST_A, GEN_A, WID_1)
    assert s1[key]["first_seen"] == NOW
    assert s2[key]["first_seen"] == NOW


def test_the_stamp_key_is_host_generation_and_window_id():
    assert ww.stamp_key(HOST_A, GEN_A, WID_1) == "%s|%s|%s" % (HOST_A, GEN_A, WID_1)


# =========================================================================== #
# §4 — RESET, PRUNE, GENERATION
# =========================================================================== #
def test_a_window_that_stops_waiting_loses_its_stamp():
    waiting = report({HOST_A: [row(probable=True, signals=("selection_menu",),
                                   age=AGE_HUGE)]})
    _, stamps = run(waiting, now=NOW)
    answered = report({HOST_A: [row(probable=False, busy=True, age=AGE_TINY)]})
    out, stamps2 = run(answered, stamps, now=NOW + 60)
    assert stamps2 == {}
    assert out["prune"]["reset_not_waiting"] == 1


def test_answered_then_re_asked_does_NOT_inherit_the_ancient_age():
    """🔴 Three runs: waiting for a long time, then answered (busy), then asked
    again. The re-asked window must start a NEW clock — inheriting the first
    wait would report a question asked seconds ago as days old, which is the
    same class of lie as `age_secs` itself."""
    waiting = report({HOST_A: [row(probable=True, signals=("trailing_question",),
                                   age=AGE_HUGE)]})
    _, s1 = run(waiting, now=NOW)

    answered = report({HOST_A: [row(probable=False, busy=True, age=AGE_TINY)]})
    _, s2 = run(answered, s1, now=NOW + 100)

    reasked = report({HOST_A: [row(probable=True, signals=("trailing_question",),
                                   age=AGE_TINY)]})
    out, s3 = run(reasked, s2, now=NOW + 200)

    key = ww.stamp_key(HOST_A, GEN_A, WID_1)
    assert s3[key]["first_seen"] == NOW + 200
    assert out["rows"][0]["waited_secs"] == AGE_TINY   # the new, small age
    assert out["rows"][0]["over_threshold"] is False


def test_a_window_that_disappeared_is_pruned():
    """fuzzyclaw reached 401 files, ~90% stale, because nothing ever pruned."""
    rep1 = report({HOST_A: [row(WID_1, probable=True, signals=("selection_menu",)),
                            row(WID_2, probable=True, signals=("selection_menu",))]})
    _, stamps = run(rep1, now=NOW)
    assert len(stamps) == 2
    rep2 = report({HOST_A: [row(WID_1, probable=True, signals=("selection_menu",))]})
    out, stamps2 = run(rep2, stamps, now=NOW + 60)
    assert list(stamps2) == [ww.stamp_key(HOST_A, GEN_A, WID_1)]
    assert out["prune"]["pruned_window_gone"] == 1
    assert out["prune"]["reset_not_waiting"] == 0


def test_a_tmux_server_restart_prunes_the_old_generation_and_restamps():
    """🔴 tmux restarts window ids at @0 after a server restart, so `@307` on
    the new server is a DIFFERENT window. The generation is the host's tmux
    server pid, the same signal `scripts/lib/agent_ledger.py` uses."""
    rep1 = report({HOST_A: [row(probable=True, signals=("trailing_question",),
                                age=AGE_HUGE)]}, ledger_pids={HOST_A: GEN_A})
    _, stamps = run(rep1, now=NOW)
    assert list(stamps) == [ww.stamp_key(HOST_A, GEN_A, WID_1)]

    rep2 = report({HOST_A: [row(probable=True, signals=("trailing_question",),
                                age=AGE_TINY)]}, ledger_pids={HOST_A: GEN_B})
    out, stamps2 = run(rep2, stamps, now=NOW + 60)
    assert list(stamps2) == [ww.stamp_key(HOST_A, GEN_B, WID_1)]
    assert out["prune"]["pruned_generation"] == 1
    # The ancient wait did NOT survive the restart.
    assert out["rows"][0]["waited_secs"] == AGE_TINY


def test_an_unreachable_hosts_stamps_are_KEPT_never_pruned():
    """🔴 An unreachable host produces no rows, and 'no rows' is not evidence
    the windows are gone. Pruning here would silently destroy exactly the
    long-lived stamps this tool exists to accumulate."""
    rep1 = report({HOST_A: [row(probable=True, signals=("selection_menu",),
                                age=AGE_HUGE)]}, ledger_pids={HOST_A: GEN_A})
    _, stamps = run(rep1, now=NOW)
    down = report({HOST_A: {"reachable": False, "error": "synthetic ssh failure",
                            "windows_measured": False, "windows_error": None,
                            "captures_measured": False, "captures_status": "skipped",
                            "captures_seen": 0, "windows": []}},
                  ledger_pids={HOST_A: GEN_A})
    out, stamps2 = run(down, stamps, now=NOW + 60)
    assert list(stamps2) == [ww.stamp_key(HOST_A, GEN_A, WID_1)]
    assert out["prune"]["kept_unobserved_host"] == 1
    assert out["prune"]["pruned_window_gone"] == 0


def test_a_reachable_host_whose_window_list_failed_is_also_not_pruned_against():
    """`list-panes` succeeding says nothing about `list-windows`. A host that
    answered but returned no window list gives no liveness facts."""
    rep1 = report({HOST_A: [row(probable=True, signals=("selection_menu",))]})
    _, stamps = run(rep1, now=NOW)
    broken = report({HOST_A: {"reachable": True, "error": None,
                              "windows_measured": False,
                              "windows_error": "synthetic list-windows failure",
                              "captures_measured": True, "captures_status": "ok",
                              "captures_seen": 0, "windows": []}})
    out, stamps2 = run(broken, stamps, now=NOW + 60)
    assert len(stamps2) == 1
    assert out["prune"]["kept_unobserved_host"] == 1


def test_an_unknown_generation_keeps_stamps_and_says_so_LOUDLY():
    """No tmux server pid -> the generation cannot be checked -> a stamp cannot
    be justified as belonging to a different generation, so it is KEPT under its
    own counter. 'We kept it' must never read as 'we verified it'."""
    rep1 = report({HOST_A: [row(WID_1, probable=True, signals=("selection_menu",)),
                            row(WID_2, probable=True, signals=("selection_menu",))]},
                  ledger_pids={HOST_A: None})
    _, stamps = run(rep1, now=NOW)
    assert all(k.startswith("%s|%s|" % (HOST_A, ww.GENERATION_UNKNOWN))
               for k in stamps)
    rep2 = report({HOST_A: [row(WID_1, probable=True, signals=("selection_menu",))]},
                  ledger_pids={HOST_A: None})
    out, stamps2 = run(rep2, stamps, now=NOW + 60)
    assert len(stamps2) == 2
    assert out["prune"]["kept_generation_unknown"] == 1
    assert out["coverage"]["generation_unchecked_hosts"] == [HOST_A]
    assert out["rows"][0]["generation_checked"] is False
    assert out["coverage"]["complete"] is False


def test_a_checked_generation_marks_every_row_checked():
    out, _ = run(report({HOST_A: [row(probable=True, signals=("selection_menu",))]}))
    assert out["rows"][0]["generation_checked"] is True
    assert out["rows"][0]["generation"] == GEN_A
    assert out["coverage"]["generation_unchecked_hosts"] == []


def test_the_clock_survives_a_window_INDEX_renumbering():
    """🔴 THE REASON THE KEY IS `window_id` AND NOT `session:index`. Windows
    renumber when a window closes; the id does not."""
    rep1 = report({HOST_A: [row(WID_1, probable=True,
                                signals=("trailing_question",),
                                index="7", age=AGE_TINY)]})
    _, stamps = run(rep1, now=NOW)
    rep2 = report({HOST_A: [row(WID_1, probable=True,
                                signals=("trailing_question",),
                                index="2", age=AGE_TINY)]})
    out, _ = run(rep2, stamps, now=NOW + AGE_BIG)
    assert out["rows"][0]["waited_secs"] == AGE_BIG
    assert out["rows"][0]["waited_source"] == "first_seen"


def test_a_DIFFERENT_window_id_in_the_same_slot_does_not_inherit_the_clock():
    """The mirror image: same `session:index`, different window. A key on the
    slot would hand the new window the old one's wait."""
    rep1 = report({HOST_A: [row(WID_1, probable=True, signals=("selection_menu",),
                                index="7", age=AGE_HUGE)]})
    _, stamps = run(rep1, now=NOW)
    rep2 = report({HOST_A: [row(WID_2, probable=True, signals=("selection_menu",),
                                index="7", age=AGE_TINY)]})
    out, _ = run(rep2, stamps, now=NOW + 60)
    assert out["rows"][0]["window_id"] == WID_2
    assert out["rows"][0]["waited_secs"] == AGE_TINY


def test_two_hosts_with_the_same_window_id_keep_separate_clocks():
    """tmux ids are per-server, so `@307` exists on both hosts and means two
    different windows. A key without the host would merge them."""
    rep = report({
        HOST_A: [row(WID_1, probable=True, signals=("selection_menu",),
                     age=AGE_HUGE)],
        HOST_B: [row(WID_1, probable=True, signals=("trailing_question",),
                     age=AGE_TINY)],
    })
    out, stamps = run(rep, now=NOW)
    assert set(stamps) == {ww.stamp_key(HOST_A, GEN_A, WID_1),
                           ww.stamp_key(HOST_B, GEN_B, WID_1)}
    waits = {r["host"]: r["waited_secs"] for r in out["rows"]}
    assert waits == {HOST_A: AGE_HUGE, HOST_B: AGE_TINY}


def test_two_hosts_that_happen_to_SHARE_a_tmux_pid_still_keep_separate_clocks():
    """🔴 FOUND BY THE MUTATION SWEEP. The test above cannot see a key that
    drops the HOST, because its two hosts also have two different generations —
    the composite key stays unique either way, so the mutant survived a green
    assertion.

    Two tmux servers on two machines can trivially land on the same pid, and
    then ONLY the host separates the keys. Both hosts carry GEN_A here, and the
    same window id, with waits that are pairwise distinct and distinct from
    every threshold — so a key without the host merges them and one of the two
    waits is silently lost.
    """
    rep = report({
        HOST_A: [row(WID_1, probable=True, signals=("selection_menu",),
                     age=AGE_HUGE)],
        HOST_B: [row(WID_1, probable=True, signals=("trailing_question",),
                     age=AGE_MID)],
    }, ledger_pids={HOST_A: GEN_A, HOST_B: GEN_A})
    out, stamps = run(rep, now=NOW)
    assert len(stamps) == 2
    assert set(stamps) == {ww.stamp_key(HOST_A, GEN_A, WID_1),
                           ww.stamp_key(HOST_B, GEN_A, WID_1)}
    assert {r["host"]: r["waited_secs"] for r in out["rows"]} == {
        HOST_A: AGE_HUGE, HOST_B: AGE_MID}


# =========================================================================== #
# §5 — NULL IS NOT ZERO
# =========================================================================== #
def test_an_unreachable_hosts_count_is_null_and_never_zero():
    """🔴 The whole rule in one assertion. `0` would say 'I looked at every
    window on that host and none needs you' about a look that never happened."""
    rep = report({
        HOST_A: [row(probable=True, signals=("trailing_question",), age=AGE_HUGE)],
        HOST_B: {"reachable": False, "error": "synthetic ssh failure",
                 "windows_measured": False, "windows_error": None,
                 "captures_measured": False, "captures_status": "skipped",
                 "captures_seen": 0, "windows": []},
    }, summary={"hosts_unreachable": [HOST_B], "windows_unmeasured": []})
    out, _ = run(rep)
    per_host = out["counts"]["per_host"]
    assert per_host[HOST_B]["over_threshold"] is None
    assert per_host[HOST_B]["waiting_population"] is None
    assert per_host[HOST_B]["measured"] is False
    assert per_host[HOST_A]["over_threshold"] == 1
    assert out["counts"]["counts_are_partial"] is True
    assert out["coverage"]["hosts_unobserved"] == [HOST_B]
    assert out["coverage"]["hosts_unreachable"] == [HOST_B]


def test_when_NO_host_was_observed_the_total_itself_is_null():
    rep = report({HOST_A: {"reachable": False, "error": "synthetic ssh failure",
                           "windows_measured": False, "windows_error": None,
                           "captures_measured": False, "captures_status": "skipped",
                           "captures_seen": 0, "windows": []}},
                 summary={"hosts_unreachable": [HOST_A], "windows_unmeasured": []})
    out, _ = run(rep)
    assert out["counts"]["over_threshold"] is None
    assert out["counts"]["over_threshold_by_kind"] is None
    assert out["counts"]["waiting_population"] is None


def test_unmeasured_captures_make_the_counts_partial_and_name_the_host():
    """`--no-capture`, or a failed capture batch, makes every `waiting_probable`
    on that host null. The count for that scope is UNMEASURED."""
    rep = report({HOST_A: {"reachable": True, "error": None,
                           "windows_measured": True, "windows_error": None,
                           "captures_measured": False,
                           "captures_status": "skipped", "captures_seen": 0,
                           "windows": [row(probable=None,
                                           waiting_status="uncaptured")]}})
    out, _ = run(rep)
    assert out["coverage"]["hosts_captures_unmeasured"] == [HOST_A]
    assert out["coverage"]["complete"] is False
    assert out["counts"]["counts_are_partial"] is True


def test_a_row_whose_waiting_signal_was_not_measured_is_counted_not_filed_as_idle():
    """🔴 A row with `waiting_status != "ok"` is neither waiting nor
    not-waiting. Filing it as `idle_no_signal` would turn 'we did not look' into
    a measured observation."""
    rep = report({HOST_A: [
        row(WID_1, probable=None, waiting_status="uncaptured", age=AGE_HUGE),
        row(WID_2, probable=None, waiting_status="not_claude", age=AGE_HUGE),
    ]})
    out, _ = run(rep)
    assert out["rows"] == []
    assert out["coverage"]["rows_unmeasured"] == 2
    assert out["coverage"]["rows_unmeasured_reasons"] == {
        "uncaptured": 1, "not_claude": 1}
    assert out["coverage"]["complete"] is False


def test_upstream_windows_unmeasured_is_propagated_into_the_coverage():
    rep = report({HOST_A: [row(probable=True, signals=("selection_menu",))]},
                 summary={"hosts_unreachable": [],
                          "windows_unmeasured": ["synthetic-unmeasured-1"]})
    out, _ = run(rep)
    assert out["coverage"]["windows_unmeasured"] == ["synthetic-unmeasured-1"]
    assert out["coverage"]["complete"] is False


def test_a_row_with_no_window_id_is_counted_never_silently_dropped():
    """No stable key -> no clock. Dropping it silently would under-count the
    very population this tool exists to surface."""
    rep = report({HOST_A: [row(probable=True, signals=("trailing_question",),
                               age=AGE_HUGE, include_window_id=False)]})
    out, _ = run(rep)
    assert out["rows"] == []
    assert out["coverage"]["rows_without_window_id"] == 1
    assert out["coverage"]["complete"] is False


def test_a_fully_measured_report_is_complete_and_its_counts_are_not_partial():
    """The positive control for every `complete is False` above: the flag must
    be able to be True, or those assertions prove nothing."""
    out, _ = run(report({HOST_A: [row(probable=True,
                                      signals=("trailing_question",),
                                      age=AGE_HUGE)]},
                        ledger_pids={HOST_A: GEN_A}))
    assert out["coverage"]["complete"] is True
    assert out["counts"]["counts_are_partial"] is False
    assert out["counts"]["over_threshold"] == 1


def test_the_per_host_coverage_carries_the_upstream_measurement_discriminators():
    rep = report({HOST_A: [row(probable=True, signals=("selection_menu",))]})
    out, _ = run(rep)
    blk = out["coverage"]["per_host"][HOST_A]
    assert blk["observed"] is True
    assert blk["captures_status"] == "ok"
    assert blk["captures_measured"] is True
    assert blk["generation"] == GEN_A


# =========================================================================== #
# §6 — NO SILENT CAPS
# =========================================================================== #
def test_a_cap_reports_exactly_what_it_dropped():
    rows = [{"n": i} for i in range(9)]
    shown, trunc = ww.apply_top(rows, 4)
    assert len(shown) == 4
    assert trunc == {"shown": 4, "dropped": 5, "total": 9}


def test_no_cap_means_no_truncation_record():
    rows = [{"n": i} for i in range(9)]
    shown, trunc = ww.apply_top(rows, None)
    assert shown == rows and trunc is None


def test_a_cap_larger_than_the_row_count_drops_nothing():
    rows = [{"n": i} for i in range(3)]
    shown, trunc = ww.apply_top(rows, 11)
    assert shown == rows and trunc is None


def test_the_human_view_PRINTS_what_a_cap_dropped():
    rep = report({HOST_A: [
        row(WID_1, probable=True, signals=("selection_menu",), age=AGE_HUGE),
        row(WID_2, probable=True, signals=("selection_menu",), age=AGE_BIG),
        row(WID_3, probable=True, signals=("selection_menu",), age=AGE_MID),
    ]})
    out, _ = ww.build_report(rep, {}, NOW, THRESHOLD, "flag", "ok",
                             "/synthetic/state.json", top=1)
    text = ww.render_human(out)
    assert "TRUNCATED: showing 1 of 3; 2 NOT shown." in text


# =========================================================================== #
# §7 — OLDEST FIRST
# =========================================================================== #
def test_rows_are_sorted_oldest_first():
    """The entire reason this tool exists: nothing surfaced the waiting set
    oldest-first, so the two oldest were the two least likely to be seen."""
    rep = report({HOST_A: [
        row(WID_1, probable=True, signals=("selection_menu",), age=AGE_MID),
        row(WID_2, probable=True, signals=("selection_menu",), age=AGE_HUGE),
        row(WID_3, probable=True, signals=("selection_menu",), age=AGE_BIG),
    ]})
    out, _ = run(rep)
    assert [r["window_id"] for r in out["rows"]] == [WID_2, WID_3, WID_1]


def test_an_unmeasured_wait_sorts_LAST_not_first():
    """A `None` wait is not an old one. Sorting it first would push a measured
    multi-day row below a row that could not be timed at all."""
    rep = report({HOST_A: [
        row(WID_1, probable=True, signals=("selection_menu",), age=None),
        row(WID_2, probable=True, signals=("selection_menu",), age=AGE_TINY),
    ]})
    out, _ = run(rep)
    assert [r["window_id"] for r in out["rows"]] == [WID_2, WID_1]


# =========================================================================== #
# §8 — THE ON-DISK ARTIFACT NAMES
# =========================================================================== #
@pytest.fixture()
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("HOME", str(h))
    return h


def paths_under(root):
    out = []
    for dirpath, _dirnames, filenames in os.walk(str(root)):
        for name in filenames:
            full = os.path.join(dirpath, name)
            out.append(os.path.relpath(full, str(root)).replace(os.sep, "/"))
    return sorted(out)


def test_the_writer_creates_EXACTLY_these_paths_under_HOME(home):
    """🔴 THE ON-DISK ARTIFACT-NAME PIN, in the shape
    `scripts/claude-hooks/tests/test_on_disk_artifact_names.py` established.

    The REAL writer is driven against a throwaway `$HOME` and the COMPLETE set
    of relative paths is compared against literals. That form is deliberate:

      * a whole-path literal cannot be walked by renaming one component, the
        way a `"waiting-windows" in path` check could be;
      * comparing the COMPLETE set means the assertion fails when the set GROWS
        as well as when it shrinks — a future change that starts writing a
        second artifact arrives as a red test naming the new path, not as an
        unpinned name that a rename can silently orphan;
      * it also pins that the atomic-write temp file is GONE afterwards. A
        leftover `.state-*.tmp` in the state directory would be an unpinned
        artifact AND a file a future reader could mistake for state.
    """
    ww.save_state(ww.state_path(), {
        ww.stamp_key(HOST_A, GEN_A, WID_1): {
            "first_seen": NOW, "age_at_first_seen": AGE_HUGE, "host": HOST_A,
            "window_id": WID_1, "generation": GEN_A, "kind": "trailing_question",
            "last_seen": NOW,
        }})
    assert paths_under(home) == [".cache/waiting-windows/state.json"]


def test_the_state_path_resolves_HOME_at_CALL_time_not_import_time(home):
    """If it moved to import time, this would carry the developer's real home
    and the walk above would find nothing — a green test over an empty tree."""
    assert ww.state_path() == os.path.join(
        str(home), ".cache", "waiting-windows", "state.json")
    assert ww.state_dir() == os.path.join(str(home), ".cache", "waiting-windows")


def test_two_writes_still_leave_exactly_one_file(home):
    """The positive control for the temp-file half: an atomic write that leaked
    its temp file would show a second path here."""
    ww.save_state(ww.state_path(), {"k1": {"first_seen": NOW}})
    ww.save_state(ww.state_path(), {"k2": {"first_seen": NOW + 1}})
    assert paths_under(home) == [".cache/waiting-windows/state.json"]


def test_a_missing_state_file_is_reported_as_missing_with_an_empty_stamp_set(tmp_path):
    got = ww.load_state(str(tmp_path / "nope" / "state.json"))
    assert got == {"stamps": {}, "status": "missing"}


def test_a_malformed_state_file_degrades_to_empty_and_SAYS_malformed(tmp_path):
    """🔴 Never a crash, and never a SILENT empty set: an empty set means every
    row falls back to `ledger_age_fallback` this run, which is correct only
    because the status says why."""
    p = tmp_path / "state.json"
    p.write_text("{not json at all", encoding="utf-8")
    assert ww.load_state(str(p)) == {"stamps": {}, "status": "malformed"}


def test_a_state_file_of_the_wrong_SHAPE_is_malformed_too(tmp_path):
    p = tmp_path / "state.json"
    p.write_text(json.dumps({"version": 1, "stamps": ["not", "a", "dict"]}),
                 encoding="utf-8")
    assert ww.load_state(str(p)) == {"stamps": {}, "status": "malformed"}


def test_a_stamp_with_a_non_numeric_first_seen_is_dropped_not_trusted(tmp_path):
    """A stamp that is not a clock costs one run of fallback if dropped, and
    produces a wait computed from garbage if kept."""
    p = tmp_path / "state.json"
    p.write_text(json.dumps({"version": 1, "stamps": {
        "good": {"first_seen": NOW},
        "bad": {"first_seen": "not a number"},
        "worse": {"no_first_seen": True},
    }}), encoding="utf-8")
    got = ww.load_state(str(p))
    assert got["status"] == "ok"
    assert list(got["stamps"]) == ["good"]


def test_stamps_round_trip_through_save_and_load(tmp_path):
    p = str(tmp_path / "state.json")
    key = ww.stamp_key(HOST_B, GEN_B, WID_3)
    stamps = {key: {"first_seen": NOW, "age_at_first_seen": AGE_BIG,
                    "host": HOST_B, "window_id": WID_3, "generation": GEN_B,
                    "kind": "selection_menu", "last_seen": NOW}}
    ww.save_state(p, stamps)
    got = ww.load_state(p)
    assert got["status"] == "ok"
    assert got["stamps"] == stamps


# =========================================================================== #
# §9 — INPUT HANDLING
# =========================================================================== #
def test_an_empty_report_does_not_crash_and_reports_nothing_as_UNMEASURED():
    out, stamps = run({}, {})
    assert out["rows"] == []
    assert stamps == {}
    assert out["counts"]["over_threshold"] is None
    assert out["coverage"]["per_host"] == {}


def test_a_report_with_null_sections_does_not_crash():
    out, _ = run({"hosts": None, "ledger": None, "summary": None}, {})
    assert out["rows"] == []
    assert out["counts"]["over_threshold"] is None


def test_a_host_block_with_null_windows_does_not_crash():
    rep = {"hosts": {HOST_A: {"reachable": True, "windows_measured": True,
                              "captures_measured": True, "windows": None}},
           "ledger": {"hosts": {HOST_A: {"tmux_pid": GEN_A}}},
           "summary": {}}
    out, _ = run(rep, {})
    assert out["rows"] == []
    assert out["counts"]["over_threshold"] == 0


def test_a_row_with_a_malformed_waiting_signals_entry_does_not_crash():
    r = row(probable=True, age=AGE_BIG)
    r["waiting_signals"] = ["a bare string", None, {"signal": "selection_menu"}]
    out, _ = run(report({HOST_A: [r]}))
    assert out["rows"][0]["kind"] == "selection_menu"


def test_read_report_file_reads_a_path(tmp_path):
    p = tmp_path / "report.json"
    p.write_text(json.dumps({"ts": "synthetic-ts"}), encoding="utf-8")
    assert ww.read_report_file(str(p)) == {"ts": "synthetic-ts"}


def test_read_report_file_reads_stdin_on_a_dash(monkeypatch):
    monkeypatch.setattr(sys, "stdin",
                        io.StringIO(json.dumps({"ts": "synthetic-stdin-ts"})))
    assert ww.read_report_file("-") == {"ts": "synthetic-stdin-ts"}


def test_the_session_manager_argv_is_NOT_lean_and_the_reason_is_MEASURED():
    """🔴 A SEAM GUARD, not a restatement. `--lean` is the agent-shaped view and
    it DROPS `window_id` — the only stable key a stamp can use. That is asserted
    against session-manager's OWN field ledger rather than from memory, so if
    the lean view ever starts carrying `window_id` this test goes red and the
    flag choice can be revisited deliberately.
    """
    assert "window_id" not in sm.LEAN_ROW_FIELDS
    assert "--lean" not in ww.SESSION_MANAGER_ARGV
    assert "--json" in ww.SESSION_MANAGER_ARGV
    assert "--no-ch" in ww.SESSION_MANAGER_ARGV
    # NOT --claude-only: the full window census is what a prune needs to tell
    # "this window still exists" from "this window closed". The agent filter is
    # applied here instead, on `row["claude"]`.
    assert "--claude-only" not in ww.SESSION_MANAGER_ARGV


def test_fetch_report_parses_the_runners_stdout_and_never_touches_the_machine():
    seen = {}

    class P:
        returncode = 0
        stdout = json.dumps({"ts": "synthetic-fetch-ts"})
        stderr = ""

    def runner(argv):
        seen["argv"] = argv
        return P()

    got = ww.fetch_report("/synthetic/session-manager", runner=runner)
    assert got == {"ts": "synthetic-fetch-ts"}
    assert seen["argv"][1] == "/synthetic/session-manager"
    assert seen["argv"][2:] == list(ww.SESSION_MANAGER_ARGV)


def test_fetch_report_raises_with_the_stderr_when_session_manager_fails():
    class P:
        returncode = 3
        stdout = ""
        stderr = "synthetic upstream failure"

    with pytest.raises(RuntimeError) as e:
        ww.fetch_report("/synthetic/session-manager", runner=lambda a: P())
    assert "synthetic upstream failure" in str(e.value)


# =========================================================================== #
# §10 — THE HUMAN VIEW SAYS WHAT THE JSON SAYS
# =========================================================================== #
def _human(rep, stamps=None, now=NOW, threshold=THRESHOLD, top=None):
    out, _ = ww.build_report(rep, dict(stamps or {}), now, threshold, "flag",
                             "ok", "/synthetic/state.json", top=top)
    return out, ww.render_human(out)


def test_the_idle_no_signal_caveat_is_carried_in_the_JSON_and_PRINTED():
    """🔴 PINNED AS A WHOLE STRING, both places. A guard on two or three words
    is walkable by rewording the sentence into something that no longer warns —
    this repo has had three such guards walked in a single PR. A cosmetic
    reword now fails this test, which is the price of a machine-readable claim.
    """
    expected = (
        "session-manager does not read PRs, so a window idle because its PR is "
        "blocked looks exactly like a window idle because the work is done. The "
        "idle_no_signal bucket carries both and cannot separate them. Do not "
        "read it as a queue of work needing attention."
    )
    assert ww.IDLE_NO_SIGNAL_CAVEAT == expected
    out, text = _human(report({HOST_A: [row(probable=False, age=AGE_HUGE)]}))
    assert out["caveats"]["idle_no_signal"] == expected
    assert expected in text


def test_the_clock_caveat_is_carried_in_the_JSON_and_PRINTED():
    expected = (
        "waited_secs is this tool's own clock, not session-manager's age_secs. "
        "age_secs measures time since the window's last ledger heartbeat, which "
        "is a different question in the same units: a row was measured waiting "
        "while busy with an age_secs of 2.5 seconds. Read waited_source on "
        "every row."
    )
    assert ww.CLOCK_CAVEAT == expected
    out, text = _human(report({HOST_A: [row(probable=False, age=AGE_HUGE)]}))
    assert out["caveats"]["clock"] == expected
    assert expected in text


def test_the_human_view_says_UNMEASURED_rather_than_printing_a_zero():
    """🔴 The rendered mirror of 'null is not zero'. A reassuring `0` from a
    scan that walked nothing is the failure mode, not the all-clear."""
    rep = report({HOST_A: {"reachable": False, "error": "synthetic ssh failure",
                           "windows_measured": False, "windows_error": None,
                           "captures_measured": False, "captures_status": "skipped",
                           "captures_seen": 0, "windows": []}},
                 summary={"hosts_unreachable": [HOST_A], "windows_unmeasured": []})
    _, text = _human(rep)
    assert "OVER THRESHOLD: UNMEASURED — no host was observed this run." in text
    assert "OVER THRESHOLD: 0" not in text


def test_the_human_view_flags_a_partial_count_beside_the_number():
    rep = report({
        HOST_A: [row(probable=True, signals=("trailing_question",), age=AGE_HUGE)],
        HOST_B: {"reachable": False, "error": "synthetic ssh failure",
                 "windows_measured": False, "windows_error": None,
                 "captures_measured": False, "captures_status": "skipped",
                 "captures_seen": 0, "windows": []},
    }, summary={"hosts_unreachable": [HOST_B], "windows_unmeasured": []})
    _, text = _human(rep)
    assert "OVER THRESHOLD: 1" in text
    assert "PARTIAL" in text
    assert "COVERAGE: INCOMPLETE" in text
    assert "hosts NOT observed (their counts are null, not 0): %s" % HOST_B in text


def test_the_human_view_marks_a_row_whose_generation_could_not_be_checked():
    rep = report({HOST_A: [row(probable=True, signals=("selection_menu",),
                               age=AGE_HUGE)]}, ledger_pids={HOST_A: None})
    _, text = _human(rep)
    assert "gen-UNCHECKED" in text


def test_the_human_view_prints_the_threshold_its_source_and_the_kinds():
    rep = report({HOST_A: [row(probable=True, signals=("context_exhausted",),
                               age=AGE_HUGE)]})
    _, text = _human(rep)
    assert "threshold 2h02m (%ds, flag)" % THRESHOLD in text
    assert "by kind: context_exhausted=1" in text


def test_the_human_view_says_so_when_nothing_is_over_the_threshold():
    """A measured, genuinely-empty result must read differently from an
    unmeasured one — the two sentences are asserted against each other."""
    rep = report({HOST_A: [row(probable=True, signals=("selection_menu",),
                               age=AGE_TINY)]})
    _, text = _human(rep)
    assert "(nothing over the threshold in the measured scope)" in text
    assert "UNMEASURED" not in text


def test_humanize_labels_a_null_rather_than_rendering_it_as_a_duration():
    assert ww.humanize(None) == "unmeasured"
    assert ww.humanize(45) == "45s"
    assert ww.humanize(3121) == "52m"
    assert ww.humanize(7331) == "2h02m"
    assert ww.humanize(196103) == "2d06h"


# =========================================================================== #
# §11 — READ-ONLY
# =========================================================================== #
def test_the_script_contains_no_tmux_MUTATING_verb_and_no_process_killer():
    """🔴 Read-only, structurally. `select-window`/`switch-client`/`kill-*`
    would move the operator's focus out from under them, and a `-f` pattern
    reaching `pkill` has destroyed a sibling agent's work in this repo before.
    Asserted against the source text, which is the only thing that cannot be
    satisfied by a code path that happens not to run in a test.
    """
    src = open(_SCRIPT, "r", encoding="utf-8").read()
    for forbidden in ("select-window", "switch-client", "kill-session",
                      "kill-window", "kill-pane", "send-keys", "pkill",
                      "killall", "respawn-pane"):
        assert forbidden not in src, forbidden


def test_the_only_subprocess_argv_the_script_builds_is_the_session_manager_read():
    """`fetch_report` is the single call site, and its argv is the pinned
    read-only invocation. A second `subprocess` entry point would show up here."""
    src = open(_SCRIPT, "r", encoding="utf-8").read()
    assert src.count("subprocess.run") == 1


# =========================================================================== #
# §12 — THE WHOLE-REPORT SHAPE
# =========================================================================== #
def test_the_json_report_carries_every_field_a_poller_needs():
    """A bar pill or poller reads this payload. Pinned as SET EQUALITY so a
    field disappearing is red and a new one arrives deliberately."""
    rep = report({HOST_A: [row(probable=True, signals=("trailing_question",),
                               age=AGE_HUGE)]})
    out, _ = ww.build_report(rep, {}, NOW, THRESHOLD, "flag", "ok",
                             "/synthetic/state.json")
    assert set(out) == {
        "tool", "now", "source_ts", "threshold_secs", "threshold_source",
        "counts", "coverage", "over_threshold", "waiting_population",
        "truncated", "state", "caveats",
    }
    assert out["tool"] == "waiting-windows"
    assert out["threshold_secs"] == THRESHOLD
    assert out["state"]["path"] == "/synthetic/state.json"
    assert out["state"]["status"] == "ok"
    assert out["state"]["stamps_total"] == 1
    assert json.loads(json.dumps(out)) == out       # JSON-serialisable as-is


def test_every_row_carries_its_waited_source_so_no_number_is_unlabelled():
    rep = report({HOST_A: [
        row(WID_1, probable=True, signals=("selection_menu",), age=AGE_HUGE),
        row(WID_2, probable=True, signals=("selection_menu",), age=None),
    ]})
    out, _ = run(rep)
    sources = {r["window_id"]: r["waited_source"] for r in out["rows"]}
    assert sources == {WID_1: "ledger_age_fallback", WID_2: "unmeasured"}
    assert all(r["waited_source"] in ("first_seen", "ledger_age_fallback",
                                      "unmeasured") for r in out["rows"])


def test_over_threshold_is_a_subset_of_the_waiting_population():
    rep = report({HOST_A: [
        row(WID_1, probable=True, signals=("selection_menu",), age=AGE_HUGE),
        row(WID_2, probable=True, signals=("selection_menu",), age=AGE_TINY),
    ]})
    out, _ = ww.build_report(rep, {}, NOW, THRESHOLD, "default", "missing",
                             "/synthetic/state.json")
    assert [r["window_id"] for r in out["over_threshold"]] == [WID_1]
    assert [r["window_id"] for r in out["waiting_population"]] == [WID_1, WID_2]
    assert out["counts"]["waiting_population"] == 2
    assert out["counts"]["over_threshold"] == 1


def test_the_prune_counters_are_all_published_every_run():
    out, _ = run(report({HOST_A: [row(probable=True, signals=("selection_menu",))]}))
    assert set(out["prune"]) == {
        "kept_unobserved_host", "reset_not_waiting", "pruned_window_gone",
        "pruned_generation", "kept_generation_unknown"}
