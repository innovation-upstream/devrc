#!/usr/bin/env python3
"""Round 4 (2026-08-21). Two defects found by reading the code against its own docs.

  D6  `disagreements()` gates every ticket/comment cross-check on a hardcoded five-word
      status vocabulary. A ClickUp status outside it — "in review", "blocked", "needs qa"
      — silently disables ALL of them, including the keep-open veto and the flagship
      resolved-comment flag. The report then prints an empty "Needs a decision" block,
      which reads as "no disagreement" when it means "not checked". Measured at base:
      'to do' and 'open' flagged; 'in review', 'blocked', 'needs qa', 'review' returned [].

  D7  the proximity tier was unreachable. `extract_text_windows` is the only producer of
      windows in production and hardcodes distance 0, so every signal scored 1.0 (or 1.5
      with the adjacency boost) and NOTHING could ever land at or below the 0.5 "close"
      threshold. `close_completion` was therefore always == `completion`, the three
      branches behind it were dead, and the ●/○ confidence marker always printed ●.
      Vestigial from the full-text fallback deleted in round 1 — the only thing that ever
      emitted a non-unit proximity (a hardcoded 0.5).

Tests marked INVARIANT GUARD pass at base too. They are controls, not regression
coverage; `test_production_windows_are_all_distance_zero` in particular is the premise
that licenses D7's deletion, and goes red if a producer ever emits a non-zero distance.
"""
import io, json, sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(SCRIPT_DIR))

import importlib.util

spec = importlib.util.spec_from_file_location("check_completion", SCRIPT_DIR / "check-completion.py")
check_completion = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check_completion)

spec2 = importlib.util.spec_from_file_location("check_addressed", SCRIPT_DIR / "check-addressed.py")
check_addressed = importlib.util.module_from_spec(spec2)
spec2.loader.exec_module(check_addressed)

TASK = "868gx0aaa"
RESOLVED_COMMENT = "Resolved. Recommend closing."
KEEP_OPEN_COMMENT = "Still live, do not close. the queue-depth alert fired and resolved repeatedly overnight."


def _result(status_word, comment, transcript_status="likely_addressed"):
    return {
        "task_id": TASK,
        "status": transcript_status,
        "clickup_status": status_word,
        "newest_comment": {"snippet": comment},
        "completion": [], "open": [],
    }


# --------------------------------------------------------------------------- D6

def test_unknown_clickup_status_is_flagged_not_silently_skipped():
    """A status outside the vocabulary must announce that the cross-check did NOT run.

    At base this returns [] for every one of these, indistinguishable from "checked, and
    the ticket agrees with the transcripts".
    """
    for unknown in ("in review", "blocked", "needs qa", "review", "won't do"):
        flags = check_addressed.disagreements([_result(unknown, RESOLVED_COMMENT)])
        assert flags, f"status {unknown!r}: cross-check silently skipped, no flag emitted"
        joined = " ".join(flags).lower()
        assert unknown in joined, f"status {unknown!r}: flag does not name the status: {flags}"
        assert "did not run" in joined or "not check" in joined, \
            f"status {unknown!r}: flag does not say the cross-check was skipped: {flags}"


def test_missing_clickup_status_is_flagged_too():
    """A task whose status ClickUp did not return is the same blind spot, not a pass."""
    flags = check_addressed.disagreements([_result(None, RESOLVED_COMMENT)])
    assert flags, "a missing ClickUp status silently skipped the cross-check"


def test_keep_open_is_surfaced_even_on_an_unknown_status():
    """The reporter's own 'do not close' is worth surfacing whatever the status reads.

    The D5 veto only suppresses a flag, so on an unknown status it is moot — but the
    instruction itself must still reach the operator.
    """
    flags = check_addressed.disagreements([_result("in review", KEEP_OPEN_COMMENT)])
    joined = " ".join(flags).lower()
    assert "do not close" in joined, \
        f"'do not close' in the newest comment never reached the operator: {flags}"


def test_known_open_status_still_flags_a_resolved_comment():
    """INVARIANT GUARD — passes at base. The D6 fix must not swallow the round-2 flag."""
    flags = check_addressed.disagreements([_result("to do", RESOLVED_COMMENT)])
    assert any("reads as RESOLVED" in f for f in flags), f"round-2 flag lost: {flags}"


def test_known_status_veto_still_wins():
    """INVARIANT GUARD — passes at base. The D5 veto must survive the D6 fix."""
    flags = check_addressed.disagreements([_result("to do", KEEP_OPEN_COMMENT)])
    joined = " ".join(flags)
    assert "do NOT close" in joined, f"veto lost: {flags}"
    assert "close it, or say why" not in joined, \
        f"told the operator to close a ticket whose comment says not to: {flags}"


def test_agreeing_ticket_still_produces_no_flag():
    """INVARIANT GUARD — passes at base. The D6 fix must not flag every task."""
    r = _result("complete", "Confirmed fixed, thanks.", transcript_status="likely_addressed")
    assert check_addressed.disagreements([r]) == []


# --------------------------------------------------------------------------- D7

def test_production_windows_are_all_distance_zero():
    """INVARIANT GUARD — the premise that licensed deleting the proximity tier.

    `extract_text_windows` is the ONLY producer of windows outside the tests. If it ever
    emits a non-zero distance this goes red: re-read the deleted `proximity > 0.5` tier
    in check_task() before trusting any verdict, because signals could then fall below
    the threshold the deleted branches existed to catch.
    """
    text = ("prelude " * 400) + f" {TASK} fixed, PR #123 merged. " + ("tail " * 400)
    windows = check_completion.extract_text_windows(text, TASK, window_size=2000)
    assert windows, "positive control: the task ID is in the text, so a window must exist"
    assert all(distance == 0 for _, distance, _ in windows), \
        f"a producer emitted a non-zero distance: {[d for _, d, _ in windows]}"


def test_every_production_signal_scores_above_the_deleted_threshold():
    """INVARIANT GUARD — the second half of the same premise.

    Distance 0 means proximity is 1.0, or 1.5 with the adjacency boost. Both exceed the
    0.5 "close" threshold, so the tier keyed on it could never discriminate.
    """
    text = f"{TASK} PR #123 merged. " + ("filler " * 200) + " and #456 was shipped too"
    windows = check_completion.extract_text_windows(text, TASK, window_size=2000)
    signals = check_completion.extract_signals_from_windows(
        windows, check_completion.COMPLETION_PATTERNS, TASK)
    assert signals, "positive control: this text contains completion signals"
    assert all(s[2] > 0.5 for s in signals), \
        f"a production signal scored <= 0.5, the deleted tier mattered: {[s[2] for s in signals]}"


def test_confidence_marker_distinguishes_adjacent_from_same_window():
    """The ●/○ marker must report something that varies in production.

    At base it is computed inline as `"●" if prox > 0.5 else "○"`, and production
    proximity is only ever 1.0 or 1.5 — so it printed ● for every signal ever emitted,
    including signals whose snippet is 2000 characters from the task ID. Verified live on
    868gx0aaa: every line was ●.
    """
    adjacent = {"proximity": 1.5}      # the task ID is inside the signal's own snippet
    same_window_only = {"proximity": 1.0}  # merely somewhere in the +/-2000-char window
    assert check_completion.confidence_marker(adjacent) == "●"
    assert check_completion.confidence_marker(same_window_only) == "○", \
        "the marker still cannot distinguish an adjacent signal from a distant one"


def test_deleting_the_tier_preserves_every_verdict():
    """The four surviving branches must produce the same four verdicts as before.

    Built from real windows, not hand-made ones: fixtures that feed a distance production
    cannot emit are what let the dead tier look tested for two rounds.
    """
    def verdict(text):
        windows = check_completion.extract_text_windows(text, TASK, window_size=2000)
        comp = check_completion.extract_signals_from_windows(
            windows, check_completion.COMPLETION_PATTERNS, TASK)
        opn = check_completion.extract_signals_from_windows(
            windows, check_completion.OPEN_PATTERNS, TASK)
        return bool(comp), bool(opn)

    assert verdict(f"{TASK} PR #123 merged") == (True, False)
    assert verdict(f"{TASK} is still open") == (False, True)
    assert verdict(f"{TASK} PR #123 merged but the root cause is still open") == (True, True)
    assert verdict(f"{TASK} we talked about it") == (False, False)


# ------------------------------------------------------- orchestrator arg parsing

def test_include_self_runs_is_recognised_by_the_orchestrator():
    """SKILL.md documents this flag on the entry point; at base the parser drops it.

    check-addressed.py's loop ends in `else: i += 1`, so an unrecognised flag is silently
    swallowed and never forwarded to the sub-scripts. The documented escape hatch did
    nothing at all when passed to the command the Quick start tells you to run.
    """
    opts = check_addressed.parse_args(["--include-self-runs"])
    assert opts["include_self_runs"] is True


def test_include_self_runs_is_forwarded_to_both_subscripts():
    """Recognising the flag is not wiring it — assert the argv the sub-scripts receive.

    Parsing and forwarding are two separate steps and only one of them is what the flag
    means. A test that stops at parse_args would read as coverage of a flag that still did
    nothing. Measured at base, the entry point handed the sub-scripts:
        search-sessions.py:  [--limit 5 --json --exclude-session <id> <task>]
        check-completion.py: [--task <task> --json --exclude-session <id>]
    with '--include-self-runs' forwarded nowhere, and the run reported success.
    """
    calls = []

    def fake(name, *args):
        calls.append((name, list(args)))
        if name == "recent-comments.py":
            return json.dumps([{"task_id": TASK, "task_name": "t", "task_status": "to do",
                                "task_priority": "urgent", "date": "2026-08-20 10:00",
                                "author": "x", "snippet": "hi"}]), 0
        if name == "search-sessions.py":
            return "[]", 0
        return json.dumps([{"task_id": TASK, "status": "unclear", "sessions_searched": 0,
                            "mentions_found": 0, "completion": [], "open": []}]), 0

    orig_run, orig_argv, orig_stdout = check_addressed.run_script, sys.argv, sys.stdout
    check_addressed.run_script = fake
    sys.argv = ["check-addressed.py", "--transcripts", "--include-self-runs", "--no-resolve-prs"]
    sys.stdout = io.StringIO()
    try:
        check_addressed.main()
    finally:
        check_addressed.run_script, sys.argv, sys.stdout = orig_run, orig_argv, orig_stdout

    forwarded = {name for name, a in calls if "--include-self-runs" in a}
    assert forwarded == {"search-sessions.py", "check-completion.py"}, \
        f"flag not forwarded to both stages; reached: {forwarded or 'nothing'}"


def test_self_run_guard_is_on_by_default():
    """INVARIANT GUARD — the opposite direction: without the flag, nothing opts out.

    Pairs with the test above so 'always forward' cannot pass as 'forward when asked'.
    """
    calls = []

    def fake(name, *args):
        calls.append((name, list(args)))
        if name == "recent-comments.py":
            return json.dumps([{"task_id": TASK, "task_name": "t", "task_status": "to do",
                                "task_priority": "urgent", "date": "2026-08-20 10:00",
                                "author": "x", "snippet": "hi"}]), 0
        if name == "search-sessions.py":
            return "[]", 0
        return json.dumps([{"task_id": TASK, "status": "unclear", "sessions_searched": 0,
                            "mentions_found": 0, "completion": [], "open": []}]), 0

    orig_run, orig_argv, orig_stdout = check_addressed.run_script, sys.argv, sys.stdout
    check_addressed.run_script = fake
    sys.argv = ["check-addressed.py", "--transcripts", "--no-resolve-prs"]
    sys.stdout = io.StringIO()
    try:
        check_addressed.main()
    finally:
        check_addressed.run_script, sys.argv, sys.stdout = orig_run, orig_argv, orig_stdout

    assert not any("--include-self-runs" in a for _, a in calls), \
        "the prior-run guard was opted out of without anyone asking"


def test_known_flags_still_parse():
    """INVARIANT GUARD for the extraction — every flag main() used to read by hand."""
    opts = check_addressed.parse_args(
        ["--limit", "5", "--since", "2026-08-15", "--json", "--verbose",
         "--fast", "--no-resolve-prs"])
    assert opts["limit"] == 5
    assert opts["since"] == "2026-08-15"
    assert opts["as_json"] is True
    assert opts["verbose"] is True
    assert opts["fast"] is True
    assert opts["resolve_prs"] is False


def test_unknown_flag_is_rejected_not_swallowed():
    """The class behind D7's doc drift: a typo'd or retired flag must not read as success.

    A silently-ignored flag is the same reassuring-nothing this skill exists to police —
    the run looks like it honoured your request and did not.
    """
    try:
        check_addressed.parse_args(["--no-such-flag"])
    except SystemExit:
        return
    raise AssertionError("an unknown flag was accepted and silently ignored")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = failed = 0
    for t in tests:
        try:
            t()
            print(f"  ✓ {t.__name__}")
            passed += 1
        except SystemExit as e:
            # Not an Exception — see the note in run_all.py; uncaught it kills the run.
            print(f"  ✗ {t.__name__}: SystemExit({e.code}) escaped the test")
            failed += 1
        except Exception as e:
            print(f"  ✗ {t.__name__}: {type(e).__name__}: {e}")
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
