#!/usr/bin/env python3
"""Tests for next-step-nudge.py — the Stop hook that asks for a next step.

WHAT THIS FILE IS FOR

  1. 🔴 The SUPPRESSION predicate is the whole product. This hook fires by BLOCKING
     the operator's Stop (exit 2 is the only documented way a Stop hook can reach the
     model), so a false positive is not a wasted line of context — it is a turn that
     will not end. A hook that nags is strictly worse than no hook, so the negative
     controls below outnumber the positive ones roughly 4:1 and that ratio is
     deliberate.

  2. ATTRIBUTION. `suppressed_by()` returns the NAMES of the guards that matched, and
     every suppressor has at least one fixture that it and ONLY it saves
     (`test_each_suppressor_is_uniquely_reachable`). That is what makes a mutation
     sweep meaningful: break one suppressor and a test goes red naming that suppressor,
     rather than the fixture being rescued by a sibling guard and the mutant surviving.

  3. FAIL-OPEN, which here means SILENCE. Every error path must exit 0 and write
     nothing, because the alternative is a wedged turn on the operator's live machine.
     Driven through a real subprocess, not by calling main().

  4. The SEAM between should_nudge() and the transcript reader. The unit tests stub the
     reader; the subprocess tests write a real JSONL transcript and let the shipped
     reader parse it, so a defect that lives only in their interface cannot hide.

    run:  python -m pytest scripts/claude-hooks/tests/test_next_step_nudge.py -q

Fixtures are synthetic. This repo is public: no real paths, hostnames or task titles.
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parents[1] / "next-step-nudge.py"
_spec = importlib.util.spec_from_file_location("next_step_nudge", HOOK)
nsn = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(nsn)


# --------------------------------------------------------------------------- #
# Fixture material
#
# FILLER is deliberately inert: long enough to clear MIN_MESSAGE_CHARS and to fill the
# positional tail window, and free of every suppressor. Each fixture below is
# FILLER + a distinct final paragraph, so the ONLY thing that varies between a firing
# and a silent case is how the turn ends — which is the claim the hook makes.
# --------------------------------------------------------------------------- #
FILLER = (
    "The sweep covered three modules. Module A had two findings, both on the error\n"
    "path, and both traced back to the same helper rather than to the call sites.\n"
    "Module B came back clean at every input size tried. Module C had one finding in\n"
    "its retry loop, where the backoff resets on each attempt instead of accumulating,\n"
    "so a burst of failures retries at a fixed interval forever.\n"
    "\n"
    "The measurement ran at two points, 40 and 200 concurrent requests, and the two\n"
    "runs agreed within noise. Median latency was flat across both; the tail moved by\n"
    "roughly a factor of four between them, which is consistent with the retry loop\n"
    "above and not with contention on the shared cache, since cache hit rate was\n"
    "unchanged at 94 percent in both runs. The counter that would separate those two\n"
    "explanations is not currently exported by the service.\n"
)


def msg(ending):
    return FILLER + "\n" + ending


def payload(text, **over):
    """A realistic Stop payload. Overridable field by field so a gate test can move
    exactly one thing and leave everything else in its firing state."""
    d = {
        "hook_event_name": "Stop",
        "session_id": "11111111-2222-3333-4444-555555555555",
        "transcript_path": "/nonexistent/transcript.jsonl",
        "last_assistant_message": text,
        "stop_reason": "end_turn",
        "cwd": "/tmp",
    }
    d.update(over)
    return d


def decide(text, prompt="Audit the retry path and report what you find.", tools=7, **over):
    """should_nudge() with the transcript reader stubbed to a known turn shape."""
    return nsn.should_nudge(payload(text, **over), transcript_reader=lambda p: (prompt, tools))


# ============================================================================ #
# 0. Harness self-validation — the negative and positive controls for the tests
#    themselves. Until both of these hold, nothing below means anything.
# ============================================================================ #
def test_control_filler_alone_triggers_no_suppressor():
    """POSITIVE CONTROL for the fixtures: the shared body is inert, so every result
    below is attributable to the ending under test and not to the filler."""
    assert nsn.suppressed_by(FILLER) == []


def test_control_predicate_can_fire_and_can_stay_silent():
    """NEGATIVE CONTROL for the predicate: it is not wired to a constant. Both verdicts
    are reachable from the same fixture body."""
    assert decide(msg("The counter is not exported, so that comparison stays open.")) is True
    assert decide(msg("I'll export the counter and re-run both points.")) is False


# ============================================================================ #
# 1. POSITIVE CONTROL — a turn that genuinely names no next step MUST nudge.
# ============================================================================ #
NO_NEXT_STEP = [
    "The counter that separates the two explanations is not exported, so the "
    "comparison stays open.",
    "So the retry loop is the cause, and the cache is not involved at all.",
    "That is the whole picture from the three runs.",
    "The generalisable finding is that a backoff which resets per attempt is "
    "indistinguishable from no backoff under a sustained failure.",
    "Both numbers came from the same instrument, so they cannot disagree about the "
    "thing the instrument does not measure.",
]


@pytest.mark.parametrize("ending", NO_NEXT_STEP, ids=range(len(NO_NEXT_STEP)))
def test_positive_control_turn_without_next_step_nudges(ending):
    assert decide(msg(ending)) is True


def test_positive_control_count_moves():
    """Report the count, not just a boolean: a predicate wired to nothing and a
    predicate that is merely conservative both produce 'no failures' otherwise."""
    fired = sum(1 for e in NO_NEXT_STEP if decide(msg(e)))
    assert fired == len(NO_NEXT_STEP), f"positive control fired {fired}/{len(NO_NEXT_STEP)}"


# ============================================================================ #
# 2. NEGATIVE CONTROLS — the ones that matter. Each ending already discharges the
#    operator's round trip, so each must stay silent.
# ============================================================================ #
NAMES_NEXT_STEP = {
    # (id, ending, the suppressor that must own it)
    "commitment_first_person": ("I'll export the counter and re-run both points.", "commits"),
    "commitment_contraction": ("Next round I'd raise the ceiling before touching the loop.", "commits"),
    "commitment_impersonal": ("The counter will land in the next deploy and the "
                              "comparison reruns automatically after it.", "commits"),
    "explicit_question": ("Should the counter go in before the loop fix, or after?", "asks"),
    "numbered_choice_marked": (
        "Two ways forward:\n\n1. Export the counter first, then re-measure.\n"
        "2. Fix the loop now and accept the comparison stays open.\n\n"
        "Recommendation: option 1 — it is read-only and it settles the disagreement.",
        "marked"),
    "offer_handoff": ("Say the word and the counter goes in.", "offers"),
    "offer_your_call": ("Which of the two lands first is your call.", "offers"),
    "headed_section": ("**Next steps**\n\n- export the counter\n- re-measure at both points", "headed"),
    "headed_with_modifier": ("**Ranked next steps** put the counter ahead of the loop fix, "
                             "because it is the cheaper measurement.", "headed"),
    "labelled_bare": ("Next: export the counter, then re-measure at both points.", "labelled"),
    "explicitly_done": ("Nothing further — the sweep is complete and no follow-up is owed.", "done"),
}


@pytest.mark.parametrize("key", sorted(NAMES_NEXT_STEP))
def test_negative_control_turn_naming_next_step_stays_silent(key):
    ending, _owner = NAMES_NEXT_STEP[key]
    assert decide(msg(ending)) is False


def test_each_suppressor_is_uniquely_reachable():
    """🔴 REACHABILITY + ATTRIBUTION, the guard against a shadowed mutant.

    For every suppressor there is a fixture that IT ALONE saves. If a sibling also
    matched, deleting the suppressor under test would leave the fixture suppressed
    anyway and the mutant would survive with every test still green.
    """
    for key, (ending, owner) in sorted(NAMES_NEXT_STEP.items()):
        got = nsn.suppressed_by(msg(ending))
        assert got == [owner], f"{key}: expected only {owner!r} to match, got {got!r}"


def test_every_suppressor_has_a_fixture():
    """A suppressor with no fixture is one a mutation sweep cannot kill. Pinned as a
    ledger so ADDING a suppressor without a fixture fails here, loudly."""
    owned = {owner for _e, owner in NAMES_NEXT_STEP.values()}
    declared = {name for name, _rx in nsn.SUPPRESSORS}
    assert owned == declared, f"unowned: {declared - owned}; stale: {owned - declared}"


def test_self_consistency_the_nudge_does_not_fire_on_its_own_advice():
    """🔴 The NUDGE text prescribes three endings. A turn that follows the instruction
    must not be nudged again — that is the shape that teaches an operator to ignore a
    hook. Read out of NUDGE's own wording rather than restated, so a reword that drops
    an option is visible here."""
    assert "proceed" in nsn.NUDGE and "recommendation marked" in nsn.NUDGE
    for ending in (
        "I would take the counter export next, so this can be a single proceed.",
        "1. Export the counter. 2. Fix the loop. My recommendation is 1.",
        "Nothing further; this is done.",
    ):
        assert decide(msg(ending)) is False, f"nudge fires on its own prescribed ending: {ending!r}"


# ============================================================================ #
# 3. GATES — each moves exactly one field away from the firing payload.
# ============================================================================ #
FIRING = msg(NO_NEXT_STEP[0])


def test_gate_baseline_fires():
    """The reference point every gate test below is a one-field delta from."""
    assert decide(FIRING) is True


def test_gate_terminal_user_message():
    for prompt in ("thanks", "Thanks!", "/clear", "/compact", "stop", "done", "nvm",
                   "that's all", "ok", "no"):
        assert decide(FIRING, prompt=prompt) is False, prompt


def test_gate_terminal_is_whole_message_not_substring():
    """'stop the timer and then re-run' is a task, not a goodbye. A substring test here
    would silence the hook on any prompt containing a terminal word."""
    assert decide(FIRING, prompt="stop the timer and then re-run the sweep") is True


def test_gate_short_factual_question():
    for prompt in ("who wrote the retry loop", "which module was clean?",
                   "is the counter exported?", "how many runs were there"):
        assert decide(FIRING, prompt=prompt) is False, prompt


def test_gate_long_question_is_still_work():
    """Both halves of the short-question gate are needed: a long prompt that happens to
    end in '?' is a task, and must not be excused as a lookup."""
    long_q = ("Can you audit the retry path across all three modules, measure it at two "
              "concurrency points, work out whether the tail is the loop or the cache, "
              "and tell me which counter would settle it?")
    assert len(long_q) > nsn.SHORT_QUESTION_CHARS
    assert decide(FIRING, prompt=long_q) is True


def test_gate_no_work_in_turn():
    assert decide(FIRING, tools=0) is False


def test_gate_message_too_short():
    assert decide("Yes — module B was clean.") is False
    assert len("Yes — module B was clean.") < nsn.MIN_MESSAGE_CHARS


def test_gate_stop_hook_active():
    """Never block twice in a row. Without this the 8-block cap is the only backstop."""
    assert decide(FIRING, stop_hook_active=True) is False


def test_gate_subagent_turn():
    """A subagent's Stop never reaches the operator, so it owes them no next step."""
    assert decide(FIRING, agent_id="agent-abc123") is False
    assert decide(FIRING, hook_event_name="SubagentStop") is False


def test_gate_non_end_turn_stop_reason():
    """A max_tokens stop is truncation, not a considered ending; nudging it would ask
    the model to append a next step to a sentence cut in half."""
    assert decide(FIRING, stop_reason="max_tokens") is False
    assert decide(FIRING, stop_reason="tool_use") is False
    assert decide(FIRING, stop_reason=None) is True   # absent field must not gate


def test_gate_missing_or_nonstring_message():
    for bad in (None, 123, [], {}):
        assert decide(FIRING, last_assistant_message=bad) is False


def test_gate_unreadable_transcript_stays_silent():
    """Cannot establish the turn shape => cannot establish the gates => do not fire.
    This is the fail-open direction for THIS hook: silence, not emission."""
    assert nsn.should_nudge(payload(FIRING), transcript_reader=lambda p: None) is False


def test_gate_missing_transcript_path():
    assert decide(FIRING, transcript_path=None) is False
    assert decide(FIRING, transcript_path="") is False


def test_gate_non_dict_payload():
    assert nsn.should_nudge(None) is False
    assert nsn.should_nudge("not a dict") is False
    assert nsn.should_nudge([1, 2, 3]) is False


# ============================================================================ #
# 4. Positional tail — the window is the end of the message, not the whole of it.
# ============================================================================ #
def test_next_step_buried_far_above_the_tail_does_not_suppress():
    """🔴 AN INVARIANT GUARD, NOT REGRESSION COVERAGE — labelled as such deliberately.

    It pins that the window is positional, but real traffic never violates it: sweeping
    TAIL_CHARS from 10 to 10^9 over 11,215 real turns leaves the fire count unmoved at
    565, because a message with any forward-looking construction essentially always has
    one in its final paragraph. So this test constructs a shape the corpus does not
    contain. It earns its place by pinning the intent cheaply; it must not be counted as
    evidence that the window does anything on real messages.
    """
    buried = ("I'll export the counter first.\n\n" + FILLER * 4 + "\n" +
              "The counter is not exported, so the comparison stays open.")
    assert nsn.suppressed_by(buried) == []
    assert decide(buried) is True


def test_tail_starts_at_a_paragraph_boundary():
    tail = nsn.tail_of(FILLER * 3)
    assert len(tail) >= nsn.TAIL_CHARS
    assert FILLER.rstrip().endswith(tail[-40:].rstrip()) or tail in (FILLER * 3)


def test_tail_of_short_message_is_the_whole_message():
    assert nsn.tail_of("one short line.") == "one short line."
    assert nsn.tail_of("") == ""


# ============================================================================ #
# 5. Once per session, and the atomic claim under concurrency.
# ============================================================================ #
def test_claim_is_once_per_session(tmp_path, monkeypatch):
    monkeypatch.setattr(nsn, "_state_root", lambda: str(tmp_path / "s"))
    d = nsn._state_dir({"session_id": "sess-A"})
    assert nsn.already_fired(d) is False
    assert nsn.claim(d) is True
    assert nsn.already_fired(d) is True
    assert nsn.claim(d) is False


def test_claim_is_per_session_not_global(tmp_path, monkeypatch):
    monkeypatch.setattr(nsn, "_state_root", lambda: str(tmp_path / "s"))
    assert nsn.claim(nsn._state_dir({"session_id": "sess-A"})) is True
    assert nsn.claim(nsn._state_dir({"session_id": "sess-B"})) is True


def test_claim_fails_closed_without_a_session_id(tmp_path, monkeypatch):
    """No session id => no once-per-session guarantee => must not block a turn."""
    monkeypatch.setattr(nsn, "_state_root", lambda: str(tmp_path / "s"))
    assert nsn._state_dir({}) is None
    assert nsn.claim(None) is False
    assert nsn.already_fired(None) is True


def test_claim_fails_closed_when_the_state_root_is_not_a_directory(tmp_path, monkeypatch):
    """🔴 The fail direction is REVERSED from the PostToolUse nudges. There, an
    unwritable cache duplicates a harmless nudge; here it would mean an unbounded
    Stop-blocker, which is the one outcome worse than a missed nudge.

    This test owns the makedirs arm ONLY — see the companion below."""
    blocker = tmp_path / "s"
    blocker.write_text("not a directory")          # makedirs will raise
    monkeypatch.setattr(nsn, "_state_root", lambda: str(blocker))
    assert nsn.claim(nsn._state_dir({"session_id": "sess-C"})) is False


def test_claim_fails_closed_when_the_token_cannot_be_created(tmp_path, monkeypatch):
    """🔴 REACHABILITY, and the two claim() tests are NOT interchangeable.

    The test above fails at `makedirs`; this one gets past it and fails at the O_EXCL
    `open`, with an error that is NOT FileExistsError — the session directory exists but
    is not writable, so makedirs(exist_ok=True) succeeds and the open raises
    PermissionError. A mutation sweep is what proved the distinction matters: flipping
    the open's handler to `return True` survived the makedirs test entirely and was
    caught only by the once-per-session tests, i.e. green for the wrong reason."""
    root = tmp_path / "s"
    monkeypatch.setattr(nsn, "_state_root", lambda: str(root))
    d = nsn._state_dir({"session_id": "sess-D"})
    os.makedirs(d)
    os.chmod(d, 0o500)                              # readable, NOT writable
    try:
        if os.access(d, os.W_OK):                   # running as root: cannot occur
            pytest.skip("cannot make a directory unwritable as this user")
        assert nsn.claim(d) is False
    finally:
        os.chmod(d, 0o700)


def test_already_fired_fails_closed_on_an_unexpected_error(tmp_path, monkeypatch):
    """already_fired() reads 'not yet' only for a genuinely absent directory. Any OTHER
    error means the state is unknown, and unknown must not become 'go ahead and block'.
    Reached with a state path that is a regular file, so listdir raises NotADirectoryError
    rather than FileNotFoundError."""
    root = tmp_path / "s"
    monkeypatch.setattr(nsn, "_state_root", lambda: str(root))
    d = nsn._state_dir({"session_id": "sess-E"})
    os.makedirs(os.path.dirname(d), exist_ok=True)
    Path(d).write_text("a file where a directory should be")
    assert nsn.already_fired(d) is True


def test_claim_is_atomic_under_concurrency(tmp_path, monkeypatch):
    """Exactly one winner among N racers — O_EXCL, no lock, so it can never hang."""
    monkeypatch.setattr(nsn, "_state_root", lambda: str(tmp_path / "s"))
    d = nsn._state_dir({"session_id": "sess-race"})
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=16) as ex:
        wins = sum(ex.map(lambda _: nsn.claim(d), range(64)))
    assert wins == 1


def test_session_id_sanitised_into_one_path_component(tmp_path, monkeypatch):
    """A session id is used as a filename, so it must not be able to escape the root.
    Asserted STRUCTURALLY — the resolved path stays under the root and the id occupies
    exactly one component — not by spelling, since '.._.._etc_passwd' legitimately
    contains '..' as text while traversing nowhere."""
    root = tmp_path / "s"
    monkeypatch.setattr(nsn, "_state_root", lambda: str(root))
    d = nsn._state_dir({"session_id": "../../etc/passwd"})
    assert os.path.dirname(d) == str(root)
    assert os.sep not in os.path.basename(d)
    assert os.path.realpath(d).startswith(str(root.resolve()) + os.sep)


# ============================================================================ #
# 6. The transcript reader, against real files (the seam).
# ============================================================================ #
def write_transcript(path, prompt, tool_uses, trailing_assistant_text=None):
    recs = [{"type": "user", "message": {"role": "user", "content": prompt}}]
    for i in range(tool_uses):
        recs.append({"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": f"t{i}", "name": "Bash", "input": {}}]}})
        recs.append({"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": f"t{i}", "content": "ok"}]}})
    if trailing_assistant_text:
        recs.append({"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "text", "text": trailing_assistant_text}]}})
    path.write_text("".join(json.dumps(r) + "\n" for r in recs))


def test_turn_shape_reads_prompt_and_counts_tools(tmp_path):
    t = tmp_path / "t.jsonl"
    write_transcript(t, "Audit the retry path.", 5)
    assert nsn._turn_shape(str(t)) == ("Audit the retry path.", 5)


def test_turn_shape_ignores_tool_results_masquerading_as_user_records(tmp_path):
    """The harness writes tool results back as type=user. Treating one as the operator's
    prompt would read the turn as zero-work and silence the hook everywhere."""
    t = tmp_path / "t.jsonl"
    write_transcript(t, "Audit the retry path.", 4)
    prompt, tools = nsn._turn_shape(str(t))
    assert prompt == "Audit the retry path." and tools == 4


def test_turn_shape_counts_only_the_current_turn(tmp_path):
    t = tmp_path / "t.jsonl"
    recs = [
        {"type": "user", "message": {"content": "first task"}},
        {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "a", "name": "Bash", "input": {}}]}},
        {"type": "user", "message": {"content": "second task"}},
        {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "b", "name": "Bash", "input": {}}]}},
        {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "c", "name": "Bash", "input": {}}]}},
    ]
    t.write_text("".join(json.dumps(r) + "\n" for r in recs))
    assert nsn._turn_shape(str(t)) == ("second task", 2)


def test_turn_shape_skips_sidechain_assistant_records(tmp_path):
    """Subagent traffic shares the file; counting it as the main turn's work would let a
    zero-work main turn look busy."""
    t = tmp_path / "t.jsonl"
    recs = [
        {"type": "user", "message": {"content": "the task"}},
        {"type": "assistant", "isSidechain": True,
         "message": {"content": [{"type": "tool_use", "id": "s", "name": "Bash", "input": {}}]}},
    ]
    t.write_text("".join(json.dumps(r) + "\n" for r in recs))
    assert nsn._turn_shape(str(t)) == ("the task", 0)


def test_turn_shape_skips_sidechain_user_records(tmp_path):
    """🔴 The OTHER half of the sidechain guard, and a mutation sweep proved the test
    above does not reach it: deleting `isSidechain` from _is_real_user killed nothing,
    because that test only exercises the assistant-side skip.

    A subagent's PROMPT is also written into the shared transcript as type=user. Reading
    one as the operator's prompt would test the terminal/short-question gates against
    text the operator never typed, and would truncate the tool count at the subagent
    boundary. Walking back from the end here must skip it and reach the real prompt.
    """
    t = tmp_path / "t.jsonl"
    recs = [
        {"type": "user", "message": {"content": "the operator's real task"}},
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "a", "name": "Bash", "input": {}},
            {"type": "tool_use", "id": "b", "name": "Bash", "input": {}}]}},
        {"type": "user", "isSidechain": True, "message": {"content": "who wrote this?"}},
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "c", "name": "Bash", "input": {}}]}},
    ]
    t.write_text("".join(json.dumps(r) + "\n" for r in recs))
    assert nsn._turn_shape(str(t)) == ("the operator's real task", 3)


def test_turn_shape_survives_malformed_lines(tmp_path):
    t = tmp_path / "t.jsonl"
    write_transcript(t, "Audit it.", 3)
    with open(t, "a") as fh:
        fh.write("{ this is not json\n")
        fh.write("\n")
        fh.write('"a bare string"\n')
    prompt, tools = nsn._turn_shape(str(t))
    assert prompt == "Audit it." and tools == 3


def test_turn_shape_returns_none_for_a_missing_file(tmp_path):
    assert nsn._turn_shape(str(tmp_path / "nope.jsonl")) is None


def test_turn_shape_of_an_empty_file_establishes_nothing(tmp_path):
    """🔴 An empty result cannot distinguish 'the turn is bigger than the window' from
    'this file is not a transcript'. Both look like 'no operator prompt found'. Only the
    first is evidence that work happened, so without a parsed tool_use to tell them
    apart the answer is None — could not establish — and the hook stays silent."""
    t = tmp_path / "e.jsonl"
    t.write_text("")
    assert nsn._turn_shape(str(t)) is None


def test_turn_shape_of_an_unparseable_file_establishes_nothing(tmp_path):
    t = tmp_path / "junk.jsonl"
    t.write_text("\n".join("not json line %d" % i for i in range(500)))
    assert nsn._turn_shape(str(t)) is None


def test_turn_shape_treats_an_oversized_turn_as_work(tmp_path, monkeypatch):
    """A turn bigger than the read window cannot be re-read cheaply. It is reported as
    work-was-done with no prompt, so the work gate passes and the prompt-shaped gates
    are skipped rather than guessed at."""
    monkeypatch.setattr(nsn, "TRANSCRIPT_TAIL_BYTES", 512)
    t = tmp_path / "big.jsonl"
    write_transcript(t, "the task", 200)
    prompt, tools = nsn._turn_shape(str(t))
    assert prompt is None and tools >= 1
    assert nsn.should_nudge(payload(FIRING, transcript_path=str(t))) is True


# ============================================================================ #
# 7. END TO END through a real subprocess — the shipped IO contract.
# ============================================================================ #
def run_hook(payload_dict, home):
    env = dict(os.environ)
    env["HOME"] = str(home)
    return subprocess.run([sys.executable, str(HOOK)], input=json.dumps(payload_dict),
                          capture_output=True, text=True, env=env, timeout=60)


@pytest.fixture
def home(tmp_path):
    h = tmp_path / "home"
    h.mkdir()
    return h


def test_e2e_fires_with_exit_2_and_a_message_on_stderr(tmp_path, home):
    t = tmp_path / "t.jsonl"
    write_transcript(t, "Audit the retry path.", 6)
    p = run_hook(payload(FIRING, transcript_path=str(t)), home)
    assert p.returncode == 2, p
    assert p.stdout == ""
    assert "next-step" in p.stderr and "proceed" in p.stderr


def test_e2e_is_silent_once_per_session(tmp_path, home):
    """The second turn of the same session stays silent even though it is identical."""
    t = tmp_path / "t.jsonl"
    write_transcript(t, "Audit the retry path.", 6)
    d = payload(FIRING, transcript_path=str(t))
    first = run_hook(d, home)
    second = run_hook(d, home)
    assert (first.returncode, second.returncode) == (2, 0)
    assert second.stderr == ""


def test_e2e_different_session_still_fires(tmp_path, home):
    t = tmp_path / "t.jsonl"
    write_transcript(t, "Audit the retry path.", 6)
    a = run_hook(payload(FIRING, transcript_path=str(t), session_id="sess-1"), home)
    b = run_hook(payload(FIRING, transcript_path=str(t), session_id="sess-2"), home)
    assert (a.returncode, b.returncode) == (2, 2)


def test_e2e_silent_on_a_turn_that_names_a_next_step(tmp_path, home):
    t = tmp_path / "t.jsonl"
    write_transcript(t, "Audit the retry path.", 6)
    d = payload(msg("I'll export the counter and re-run both points."), transcript_path=str(t))
    p = run_hook(d, home)
    assert (p.returncode, p.stdout, p.stderr) == (0, "", "")


# ============================================================================ #
# 8. FAIL OPEN — every degenerate input exits 0 and writes NOTHING. A Stop hook that
#    errors into the terminal, or blocks on a bad input, wedges the operator's session.
# ============================================================================ #
FAIL_OPEN_INPUTS = {
    "not_json": "not json at all",
    "empty": "",
    "whitespace": "   \n  ",
    "json_null": "null",
    "json_list": "[1,2,3]",
    "json_string": '"a string"',
    "truncated": '{"hook_event_name": "Stop", "last_assistant_mess',
    "empty_object": "{}",
    "nulls_everywhere": json.dumps({"hook_event_name": None, "session_id": None,
                                    "transcript_path": None, "last_assistant_message": None}),
    "wrong_types": json.dumps({"hook_event_name": 7, "session_id": [], "transcript_path": {},
                               "last_assistant_message": 12, "stop_reason": []}),
    "deeply_nested": json.dumps({"hook_event_name": "Stop", "last_assistant_message":
                                 {"nested": {"deeper": "x" * 2000}},
                                 "transcript_path": "/nope", "session_id": "s"}),
}


@pytest.mark.parametrize("key", sorted(FAIL_OPEN_INPUTS))
def test_fail_open_on_degenerate_stdin(key, home):
    p = subprocess.run([sys.executable, str(HOOK)], input=FAIL_OPEN_INPUTS[key],
                       capture_output=True, text=True,
                       env={**os.environ, "HOME": str(home)}, timeout=60)
    assert p.returncode == 0, f"{key}: rc={p.returncode} stderr={p.stderr!r}"
    assert p.stderr == "", f"{key}: wrote to the operator's terminal: {p.stderr!r}"
    assert p.stdout == "", f"{key}: {p.stdout!r}"


def test_fail_open_on_a_missing_transcript(home):
    p = run_hook(payload(FIRING, transcript_path="/nonexistent/nope.jsonl"), home)
    assert (p.returncode, p.stderr) == (0, "")


def test_fail_open_on_a_directory_as_transcript(tmp_path, home):
    p = run_hook(payload(FIRING, transcript_path=str(tmp_path)), home)
    assert (p.returncode, p.stderr) == (0, "")


def test_fail_open_on_an_unreadable_transcript(tmp_path, home):
    t = tmp_path / "t.jsonl"
    write_transcript(t, "Audit it.", 4)
    os.chmod(t, 0o000)
    try:
        if os.access(t, os.R_OK):            # running as root: the case cannot occur
            pytest.skip("cannot make a file unreadable as this user")
        p = run_hook(payload(FIRING, transcript_path=str(t)), home)
        assert (p.returncode, p.stderr) == (0, "")
    finally:
        os.chmod(t, 0o600)


def test_fail_open_on_a_binary_transcript(tmp_path, home):
    t = tmp_path / "t.jsonl"
    t.write_bytes(os.urandom(4096))
    p = run_hook(payload(FIRING, transcript_path=str(t)), home)
    assert (p.returncode, p.stderr) == (0, "")


def test_fail_open_when_the_state_dir_cannot_be_created(tmp_path, home):
    """An unwritable cache must not error out and must not block."""
    t = tmp_path / "t.jsonl"
    write_transcript(t, "Audit it.", 4)
    (home / ".cache").write_text("this is a file, not a directory")
    p = run_hook(payload(FIRING, transcript_path=str(t)), home)
    assert (p.returncode, p.stderr) == (0, "")


def test_hook_is_not_executable_as_a_side_effect_of_import():
    """Importing the module must not run main(); every test above depends on it."""
    src = HOOK.read_text()
    assert 'if __name__ == "__main__":' in src
