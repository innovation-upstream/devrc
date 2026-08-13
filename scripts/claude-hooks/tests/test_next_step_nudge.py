#!/usr/bin/env python3
"""Tests for next-step-nudge.py — the Stop hook that asks for a next step.

WHAT THIS FILE IS FOR

  1. 🔴 The SUPPRESSION predicate is the whole product. The hook fires by emitting
     `hookSpecificOutput.additionalContext` and exiting 0 — it does NOT block, and an
     earlier revision of this suite asserted the opposite throughout because the hook
     used exit 2 on a false premise. A false positive therefore costs one wasted model
     turn rather than a turn that will not end. Still worth suppressing hard — a hook
     that nags is worse than no hook — so the negative controls below outnumber the
     positive ones roughly 4:1 and that ratio is deliberate. The IO contract itself
     (stdout JSON, exit 0, nothing on stderr) is pinned in section 7, because "it does
     not block" is the claim most likely to rot.

  2. ATTRIBUTION. `suppressed_by()` returns the NAMES of the guards that matched, and
     every suppressor has at least one fixture that it and ONLY it saves
     (`test_each_suppressor_is_uniquely_reachable`). That is what makes a mutation
     sweep meaningful: break one suppressor and a test goes red naming that suppressor,
     rather than the fixture being rescued by a sibling guard and the mutant surviving.

  3. FAIL-OPEN, which here means SILENCE. Every error path must exit 0 and write
     nothing. A Stop hook runs at the moment a session is trying to end, so anything it
     writes to stderr surfaces as an error and anything it raises is felt immediately.
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


# --------------------------------------------------------------------------- #
# AST helpers — for the guards that make a claim about the hook's CODE.
#
# 🔴 Read the syntax tree, never the source text. This file's subject discusses `exit 2`
# and `stop_reason` at length in prose, so any text-level guard about them is satisfiable
# — and breakable — by a comment. Two such guards were written for this round and BOTH
# tripped on the very prose that explains why the thing they check was removed.
# --------------------------------------------------------------------------- #
def _module_ast(path):
    import ast
    return ast.parse(Path(path).read_text())


def _docstring_nodes(tree):
    """Every Constant node that IS a docstring, so they can be excluded."""
    import ast
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)):
            body = getattr(node, "body", None)
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                out.add(id(body[0].value))
    return out


def code_string_literals(path):
    """Every string literal in the module that is NOT a docstring. Comments never appear
    in an AST at all, which is the point."""
    import ast
    tree = _module_ast(path)
    skip = _docstring_nodes(tree)
    return {n.value for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
            and id(n) not in skip}


def sys_exit_calls(path):
    """The literal argument of every `sys.exit(...)` / `exit(...)` call, in source order.
    A non-literal argument is reported as the string '<non-literal>' so it cannot pass
    unnoticed."""
    import ast
    out = []
    for node in ast.walk(_module_ast(path)):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        name = (f.attr if isinstance(f, ast.Attribute)
                else f.id if isinstance(f, ast.Name) else None)
        if name != "exit":
            continue
        if not node.args:
            out.append(0)
        elif isinstance(node.args[0], ast.Constant):
            out.append(node.args[0].value)
        else:
            out.append("<non-literal>")
    return out


def payload(text, **over):
    """A realistic Stop payload. Overridable field by field so a gate test can move
    exactly one thing and leave everything else in its firing state.

    🔴 EVERY FIELD HERE EXISTS IN THE RUNTIME PAYLOAD. An earlier revision also set
    `stop_reason`, which the Stop event does not carry — so a fixture invented a field,
    the hook gated on it, and a test asserted the gate worked. All three agreed with each
    other and none of them agreed with the CLI. The real schema (claude-code 2.1.220) is
    `session_id, transcript_path, cwd, prompt_id?, permission_mode?, agent_id?,
    agent_type?, effort?` plus `hook_event_name, stop_hook_active,
    last_assistant_message?, background_tasks?, session_crons?`; pinned by
    test_payload_fixture_invents_no_fields below.
    """
    d = {
        "hook_event_name": "Stop",
        "session_id": "11111111-2222-3333-4444-555555555555",
        "transcript_path": "/nonexistent/transcript.jsonl",
        "last_assistant_message": text,
        "cwd": "/tmp",
    }
    d.update(over)
    return d


# The Stop payload's real field set, read out of the installed CLI's schema. A fixture
# field outside this set is an invented one, and inventing one is how the dead
# `stop_reason` gate came to look live.
STOP_PAYLOAD_FIELDS = {
    "session_id", "transcript_path", "cwd", "prompt_id", "permission_mode",
    "agent_id", "agent_type", "effort",
    "hook_event_name", "stop_hook_active", "last_assistant_message",
    "background_tasks", "session_crons",
}


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


# --------------------------------------------------------------------------- #
# 2b. ARMS, not just names. `test_every_suppressor_has_a_fixture` pins one fixture per
#     suppressor NAME, which says nothing about the ~30 alternation ARMS inside them —
#     a mutation sweep deleting an arm survived, because a sibling arm kept the one
#     fixture suppressed. Each entry below is owned by exactly one arm of one suppressor.
# --------------------------------------------------------------------------- #
SUPPRESSOR_ARMS = {
    # COMMITS — the impersonal-future arm. No first-person pronoun anywhere, which is
    # why this arm exists; a real corpus miss fired on exactly this shape.
    "commits_going_to": ("The counter is going to land with the loop fix.", "commits"),
    "commits_about_to": ("The re-measure is about to start on both points.", "commits"),
    "commits_due_to_run": ("The sweep is due to run against both points tonight.", "commits"),
    "commits_queued_to": ("The counter export is queued to land after the loop fix.", "commits"),
    "commits_set_to": ("The re-measure is set to start once the counter exists.", "commits"),
    # MARKED — the `worth <gerund>` / `worth a <noun>` arm. Its docstring says it was
    # added BECAUSE an earlier revision fired on it: a regression fix that shipped with
    # no regression test until now.
    "marked_worth_gerund": ("The retry loop is worth folding into the same change.", "marked"),
    "marked_worth_a_noun": ("The backoff constant is worth a glance before merging.", "marked"),
    # MARKED — the soft-preference arm.
    "marked_defaults_to": ("On a tie like this the order defaults to the cheaper "
                           "measurement first.", "marked"),
    "marked_leans_toward": ("The evidence leans toward the loop rather than the cache.",
                            "marked"),
}


@pytest.mark.parametrize("key", sorted(SUPPRESSOR_ARMS))
def test_each_alternation_arm_is_uniquely_owned(key):
    """🔴 ATTRIBUTION AT ARM GRAIN. Each fixture must be saved by exactly ONE suppressor,
    so deleting the arm under test leaves it firing rather than rescued by a sibling."""
    ending, owner = SUPPRESSOR_ARMS[key]
    got = nsn.suppressed_by(msg(ending))
    assert got == [owner], f"{key}: expected only {owner!r}, got {got!r}"


@pytest.mark.parametrize("key", sorted(SUPPRESSOR_ARMS))
def test_each_alternation_arm_suppresses_the_turn(key):
    ending, _owner = SUPPRESSOR_ARMS[key]
    assert decide(msg(ending)) is False


def test_arm_ledger_covers_every_arm_named_in_the_source_comments():
    """A ledger that can go stale silently is not a ledger. Pins the specific arms the
    source comments justify by name — deleting one from the regex without deleting the
    fixture fails above, and adding one here without an arm fails immediately."""
    for arm in ("going to", "about to", "due to run", "queued to", "set to"):
        assert nsn.COMMITS.search(f"the work is {arm} happen"), arm
    for arm in ("defaults to", "leans toward", "worth a glance", "worth folding"):
        assert nsn.MARKED.search(f"it {arm} something"), arm


# --------------------------------------------------------------------------- #
# 2c. ASKS anchoring. Previously UNTESTED: replacing the whole regex with a bare `\\?`
#     left every test in this file green, so the source comment about rhetorical
#     mid-sentence '?' was an unverified claim.
# --------------------------------------------------------------------------- #
def test_asks_allows_an_inline_answer_hint():
    """🔴 THE REGRESSION. The original anchor permitted only whitespace/quotes/brackets
    after the '?', so a question carrying its own answer hint did not match and the hook
    FIRED on a turn that was already waiting on the operator. On the real corpus, 12 of
    the 14 first-fires whose operator reply was a bare approval were this one shape — a
    prompt this repo itself ships."""
    for ending in ("Append this to the index? (y/N)",
                   "Which one should land first? [1-3]",
                   "Do you want the counter first? — your call.",
                   "Ready to merge? (yes/no)"):
        assert "asks" in nsn.suppressed_by(msg(ending)), ending
        assert decide(msg(ending)) is False, ending


def test_asks_does_not_match_a_rhetorical_question_mid_paragraph():
    """The other half: a '?' with a whole sentence after it on the same line is not the
    turn asking the operator anything. Without this, the anchor could be widened to a
    bare `\\?` and nothing would notice."""
    ending = ("So which is it? The evidence points at the retry loop rather than the "
              "cache, because the hit rate was unchanged across both runs and the tail "
              "moved by a factor of four.")
    assert "asks" not in nsn.suppressed_by(msg(ending))


def test_asks_anchor_is_not_a_bare_question_mark():
    """Mutation guard: a bare `\\?` would satisfy every ASKS fixture above. Pin the
    distinction directly, so replacing the regex with `\\?` fails HERE."""
    assert nsn.ASKS.search("what next?") is not None
    assert nsn.ASKS.search("is it? yes, and here is a long trailing clause that "
                           "runs well past forty characters on this line") is None


def test_asks_trailer_has_a_bound():
    """The trailer is bounded at 40 chars, so a '?' does not reach across an entire
    sentence to find a line ending. Measured at both sides of the boundary."""
    assert nsn.ASKS.search("ok? " + "x" * 30) is not None       # inside the bound
    assert nsn.ASKS.search("ok? " + "x" * 60) is None           # outside it


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
    """Never nudge two Stops in a row. The hook no longer blocks, but emitting
    additionalContext still continues the conversation, so a second Stop follows every
    fire; without this gate that second Stop is a candidate to fire again."""
    assert decide(FIRING, stop_hook_active=True) is False


def test_gate_subagent_turn():
    """A subagent's Stop never reaches the operator, so it owes them no next step."""
    assert decide(FIRING, agent_id="agent-abc123") is False
    assert decide(FIRING, hook_event_name="SubagentStop") is False


def test_payload_fixture_invents_no_fields():
    """🔴 A FIXTURE GUARD, and labelled as one: it constrains this file's own `payload()`,
    so it is green against the pre-change hook and is NOT regression coverage for it. Its
    red side is the OLD FIXTURE, which set `stop_reason` and would fail this immediately.

    Kept because it closes the loop that let the dead gate look live: the hook gated on a
    field, the fixture supplied it, and the test asserted the gate worked — three
    artefacts agreeing with each other and none of them with the CLI.

    An earlier revision gated on `stop_reason`, a field the Stop event does not send. The
    gate could never reject, its test was an invariant guard wearing a gate's label, and
    the fixture below hardcoded the field so everything looked consistent. Pinning the
    fixture's keys against the CLI's real field set is what makes that class of mistake
    fail loudly instead of silently agreeing with itself.
    """
    invented = set(payload("x")) - STOP_PAYLOAD_FIELDS
    assert invented == set(), f"fixture invents field(s) the runtime never sends: {invented}"


def test_no_gate_on_a_field_the_runtime_does_not_send():
    """`stop_reason` must not gate: it is absent from every real Stop payload, so a hook
    that branched on it would be reading None forever. Setting it to the values that
    'gate' used to reject must change NOTHING."""
    for bogus in ("max_tokens", "tool_use", "stop_sequence", ""):
        assert decide(FIRING, stop_reason=bogus) is True, (
            f"a gate on stop_reason={bogus!r} is back; the runtime never sends the field")
    # Structural, not spelled: the field name must not appear as a STRING LITERAL IN CODE.
    # Asserted over the AST so the prose explaining why the gate was removed — which of
    # course says "stop_reason" — cannot satisfy or break this guard.
    assert "stop_reason" not in code_string_literals(HOOK), (
        "stop_reason is read by the hook's CODE; the runtime never sends the field")


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
    """🔴 REGRESSION COVERAGE for the most sensitive constant in the hook — and it was
    demoted to "an invariant guard, not regression coverage" on the strength of a
    measurement that was wired to nothing.

    That measurement rebound the module-level `TAIL_CHARS` and called `suppressed_by()`.
    `tail_of(text, nchars=TAIL_CHARS)` binds its default at DEF time, so the rebind
    reached nothing: an eleven-point sweep re-measured the same 800-char window eleven
    times and its flat line was read as "the parameter is inert on real traffic".

    Measured properly, by passing `nchars`, the fire count runs 1,903 (at 10) -> 577 (at
    800) -> 262 (unbounded) over 11,789 real turns: a 7.3x swing, with 55% of the fires
    at the shipped value existing only because of this window. The shape below is not
    hypothetical either — the corpus contains 18 turns where the model stated something
    forward-looking well above the ending and the operator STILL had to ask for a
    recommendation.

    So this pins real behaviour on real traffic. See test_tail_window_is_load_bearing for
    the companion that pins the parameter is actually read.
    """
    buried = ("I'll export the counter first.\n\n" + FILLER * 4 + "\n" +
              "The counter is not exported, so the comparison stays open.")
    assert nsn.suppressed_by(buried) == []
    assert decide(buried) is True


def test_tail_window_is_load_bearing():
    """🔴 THE GUARD AGAINST THE SWEEP THAT MEASURED NOTHING.

    Pins that `tail_of` genuinely honours its `nchars` argument, by requiring the SAME
    text to be suppressed at a wide window and unsuppressed at a narrow one. A default
    bound at def time (the original defect) cannot satisfy both halves.
    """
    buried = ("I'll export the counter first.\n\n" + FILLER * 4 + "\n" +
              "The counter is not exported, so the comparison stays open.")

    def supp(nchars):
        t = nsn.tail_of(buried, nchars=nchars)
        return [n for n, rx in nsn.SUPPRESSORS if rx.search(t)]

    assert supp(10) == [], "a tiny window must see only the (inert) last paragraph"
    assert "commits" in supp(10 ** 9), "an unbounded window must reach the commitment"
    assert len(nsn.tail_of(buried, nchars=10)) < len(nsn.tail_of(buried, nchars=10 ** 9))


def test_tail_chars_default_matches_the_module_constant():
    """The shipped default must be the constant a maintainer reads, not a stale literal."""
    import inspect
    assert (inspect.signature(nsn.tail_of).parameters["nchars"].default
            == nsn.TAIL_CHARS == 800)


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
    unwritable cache duplicates a harmless nudge; here it would mean a nudge with no
    once-per-session bound at all, repeating on every turn of the session — which is the
    one outcome worse than a missed nudge.

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


def test_turn_shape_skips_meta_user_records(tmp_path):
    """🔴 THE THIRD WAY a type=user record is not the operator, and the dangerous one.

    Claude Code writes `isMeta` records — `<local-command-caveat>…`, command expansion
    notes — with STRING content. String content is exactly what the tool_result test
    passes through, so without an isMeta check the harness's own boilerplate is returned
    as "the opening prompt" and both prompt-shaped gates evaluate text the operator never
    typed. 6.1% of real turns (724 of 11,789) open on such a record.

    Reading back from the end here must walk past it to the operator's real prompt, and
    must keep counting tool_use across it rather than truncating the turn there.
    """
    t = tmp_path / "t.jsonl"
    recs = [
        {"type": "user", "message": {"content": "the operator's real task"}},
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "a", "name": "Bash", "input": {}}]}},
        {"type": "user", "isMeta": True,
         "message": {"content": "<local-command-caveat>caveat text</local-command-caveat>"}},
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "b", "name": "Bash", "input": {}}]}},
    ]
    t.write_text("".join(json.dumps(r) + "\n" for r in recs))
    assert nsn._turn_shape(str(t)) == ("the operator's real task", 2)


def test_meta_record_does_not_defeat_the_terminal_prompt_gate(tmp_path):
    """🔴 WHY isMeta MATTERS, asserted where the HARM lands — end to end, in the FIRING
    direction.

    A meta record is neither terminal nor a short question. So if one is read as the
    opening prompt, BOTH gates that could have stayed the hook answer "no" against text
    the operator never wrote. This fixture is that exact shape: the operator's real prompt
    is `/clear` (terminal — the hook must stay silent), and a harness caveat record sits
    after it. Without the isMeta check the caveat is returned as the prompt, the terminal
    gate never sees `/clear`, and the hook FIRES on a session the operator just ended.

    Asserted on should_nudge with the SHIPPED reader, not on the predicate helpers, so it
    covers the seam rather than one side of it.
    """
    t = tmp_path / "t.jsonl"
    recs = [
        {"type": "user", "message": {"content": "/clear"}},
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "a", "name": "Bash", "input": {}}]}},
        {"type": "user", "isMeta": True, "message": {
            "content": "<local-command-caveat>caveat text the operator never typed"
                       "</local-command-caveat>"}},
    ]
    t.write_text("".join(json.dumps(r) + "\n" for r in recs))

    prompt, tools = nsn._turn_shape(str(t))
    assert prompt == "/clear", f"the harness record was read as the prompt: {prompt!r}"
    assert tools == 1
    assert nsn.should_nudge(payload(FIRING, transcript_path=str(t))) is False, (
        "fired on a turn whose operator prompt was terminal")

    # Control: the same transcript WITHOUT the terminal prompt does fire, so the silence
    # above is the terminal gate doing its job and not some unrelated gate.
    recs[0] = {"type": "user", "message": {"content": "Audit the retry path and report."}}
    t.write_text("".join(json.dumps(r) + "\n" for r in recs))
    assert nsn.should_nudge(payload(FIRING, transcript_path=str(t))) is True


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


def test_turn_shape_reads_only_the_tail_of_a_large_transcript(tmp_path, monkeypatch):
    """🔴 FOUND BY THE MUTATION SWEEP: deleting the `seek` to the last
    TRANSCRIPT_TAIL_BYTES survived the whole suite.

    The oversized-turn test below could not catch it. Its fixture writes the operator
    prompt as line ONE, and the reader discards the first (possibly partial) line after
    seeking — so with the seek deleted, the very first `readline()` ate the prompt and the
    result was the same (None, tools) either way. Green for the wrong reason.

    Distinguishing fixture: a junk record FIRST, then the prompt. With the seek, neither
    is reachable. Without it, the discard eats the junk and the prompt becomes visible —
    which is exactly the unbounded read the cap exists to prevent.
    """
    monkeypatch.setattr(nsn, "TRANSCRIPT_TAIL_BYTES", 2048)
    t = tmp_path / "big.jsonl"
    recs = [{"type": "system", "message": {"content": "junk header"}},
            {"type": "user", "message": {"content": "the prompt that must stay out of reach"}}]
    for i in range(400):
        recs.append({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": f"t{i}", "name": "Bash", "input": {"pad": "x" * 40}}]}})
    t.write_text("".join(json.dumps(r) + "\n" for r in recs))
    assert t.stat().st_size > nsn.TRANSCRIPT_TAIL_BYTES

    prompt, tools = nsn._turn_shape(str(t))
    assert prompt is None, (
        "the reader reached a prompt outside the tail window: it is reading the whole file")
    assert tools >= 1


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


def test_e2e_fires_as_additional_context_and_does_not_block(tmp_path, home):
    """🔴 THE IO CONTRACT, and the whole point of the mechanism.

    Non-error feedback goes out as `hookSpecificOutput.additionalContext` on STDOUT with
    exit 0. The three assertions are independent and each pins a different past defect:

      * rc == 0 — exit 2 would PREVENT the stop, compelling a model that was legitimately
        finished to keep going. Asserted as `!= 2` too, explicitly, because that is the
        specific regression.
      * stderr empty — exit 2 delivered through `blockingError`, which put a "Stop hook
        error occurred · ctrl+o to see" notification in front of the operator on EVERY
        fire. Anything on stderr from a Stop hook is operator-visible noise.
      * the stdout JSON parses and is shaped to the CLI's union arm — a payload the
        runtime cannot validate is silently ignored, which looks exactly like a hook
        that never fired.
    """
    t = tmp_path / "t.jsonl"
    write_transcript(t, "Audit the retry path.", 6)
    p = run_hook(payload(FIRING, transcript_path=str(t)), home)

    assert p.returncode == 0, p
    assert p.returncode != 2, "the hook is blocking the stop again"
    assert p.stderr == "", f"operator-visible stderr from a Stop hook: {p.stderr!r}"

    out = json.loads(p.stdout)
    assert set(out) == {"hookSpecificOutput"}, out
    hso = out["hookSpecificOutput"]
    assert hso["hookEventName"] == "Stop", hso
    assert isinstance(hso["additionalContext"], str)
    assert set(hso) == {"hookEventName", "additionalContext"}, hso
    assert "next-step" in hso["additionalContext"]
    assert "proceed" in hso["additionalContext"]
    assert hso["additionalContext"] == nsn.NUDGE


def test_e2e_is_silent_once_per_session(tmp_path, home):
    """The second turn of the same session stays silent even though it is identical."""
    t = tmp_path / "t.jsonl"
    write_transcript(t, "Audit the retry path.", 6)
    d = payload(FIRING, transcript_path=str(t))
    first = run_hook(d, home)
    second = run_hook(d, home)
    assert (first.returncode, second.returncode) == (0, 0)
    assert first.stdout != "" and second.stdout == ""
    assert second.stderr == ""


def test_e2e_different_session_still_fires(tmp_path, home):
    t = tmp_path / "t.jsonl"
    write_transcript(t, "Audit the retry path.", 6)
    a = run_hook(payload(FIRING, transcript_path=str(t), session_id="sess-1"), home)
    b = run_hook(payload(FIRING, transcript_path=str(t), session_id="sess-2"), home)
    assert (a.returncode, b.returncode) == (0, 0)
    assert a.stdout != "" and b.stdout != ""


def test_e2e_headless_caller_opts_out(tmp_path, home):
    """🔴 `claude -p` inherits this host's hooks. Two of this repo's own scripts drive it
    under a hard timeout and parse the first line of the result, so the nudge would spend
    a turn of a bounded budget on a line nothing reads. Gated on an explicit marker the
    CALLERS set — pinned here together with the call sites that set it, so deleting either
    half is visible."""
    t = tmp_path / "t.jsonl"
    write_transcript(t, "Audit the retry path.", 6)
    d = payload(FIRING, transcript_path=str(t))

    env = dict(os.environ)
    env["HOME"] = str(home)
    env[nsn.OPT_OUT_ENV] = "1"
    p = subprocess.run([sys.executable, str(HOOK)], input=json.dumps(d),
                       capture_output=True, text=True, env=env, timeout=60)
    assert (p.returncode, p.stdout, p.stderr) == (0, "", "")

    # ...and the same payload without the marker DOES fire, so this is the marker's doing
    # and not some other gate. (A gate test that only shows silence proves nothing.)
    assert run_hook(d, home).stdout != ""


def test_headless_call_sites_set_the_opt_out():
    """🔴 The other half of the seam: a hook reading a marker nobody sets is inert, and
    NOTHING else in this suite would notice — the hook's own gate test passes whether or
    not any caller cooperates. This is the "verified in isolation" failure mode, so the
    guard has to pin the RELATIONSHIP, across both files.

    🔴 It also has to be STRUCTURAL. The first version of this test asserted the variable
    NAME appeared in the file, and a mutation that deleted the actual assignment left it
    green — because the comment ABOVE the call site explains why the variable is there,
    and that comment spelled the name. Both call-site mutants survived on that. So:
    strip comment lines, then require the ASSIGNMENT form on an executable line.
    """
    repo = HOOK.resolve().parents[2]
    for rel in ("githooks/audit-on-push.sh", "scripts/task-spec-drafter/drafter.sh"):
        src = (repo / rel).read_text()
        code = "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#"))
        assert "claude -p" in code, f"{rel}: no headless claude call; update this test"
        assert f"{nsn.OPT_OUT_ENV}=1" in code, (
            f"{rel}: headless `claude -p` call does not set {nsn.OPT_OUT_ENV}")


def test_e2e_silent_on_a_turn_that_names_a_next_step(tmp_path, home):
    t = tmp_path / "t.jsonl"
    write_transcript(t, "Audit the retry path.", 6)
    d = payload(msg("I'll export the counter and re-run both points."), transcript_path=str(t))
    p = run_hook(d, home)
    assert (p.returncode, p.stdout, p.stderr) == (0, "", "")


# ============================================================================ #
# 8. FAIL OPEN — every degenerate input exits 0 and writes NOTHING. A Stop hook that
#    errors into the terminal, or exits non-zero on a bad input, surfaces as a failure at
#    the exact moment the operator's session is trying to end.
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


def test_main_backstop_swallows_an_unexpected_error(monkeypatch, capsys):
    """🔴 THE WHOLE-HOOK FAIL-OPEN BACKSTOP, and it survived every mutation until now:
    flipping main()'s `except Exception` to exit(2) left all tests green, even though
    that line IS the central safety claim.

    It was unkillable because nothing in a realistic payload can make the guarded block
    raise — every helper already fails closed on its own. So reach it directly: make
    should_nudge() raise, and require main() to still exit 0 having written nothing.
    """
    import io

    def boom(_data):
        raise RuntimeError("simulated defect inside the decision path")

    monkeypatch.setattr(nsn, "should_nudge", boom)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload(FIRING))))
    with pytest.raises(SystemExit) as exc:
        nsn.main()
    assert exc.value.code == 0, "the fail-open backstop no longer exits 0"
    out = capsys.readouterr()
    assert out.out == "" and out.err == ""


def test_main_exits_zero_even_when_it_fires(monkeypatch, capsys, tmp_path):
    """The success path is exit 0 too — there is exactly ONE exit in main() and it is
    always 0. A non-zero exit from a Stop hook is how the old blocking behaviour looked."""
    import io
    monkeypatch.setattr(nsn, "_state_root", lambda: str(tmp_path / "s"))
    monkeypatch.setattr(nsn, "should_nudge", lambda _d: True)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload(FIRING))))
    with pytest.raises(SystemExit) as exc:
        nsn.main()
    assert exc.value.code == 0
    out = capsys.readouterr()
    assert out.err == ""
    assert json.loads(out.out)["hookSpecificOutput"]["hookEventName"] == "Stop"


def test_main_has_no_nonzero_exit_anywhere():
    """Structural companion to the two above: the hook must not be ABLE to exit non-zero.

    Pins the "it does not block" claim against the whole file rather than against the one
    path a test happened to drive. Read off the AST, not the text — the source discusses
    exit 2 at length in prose, and a guard a comment can satisfy is not a guard.
    """
    exits = sys_exit_calls(HOOK)
    assert exits == [0], f"the hook's exit codes are {exits}; every one must be 0"


def test_hook_is_not_executable_as_a_side_effect_of_import():
    """Importing the module must not run main(); every test above depends on it."""
    src = HOOK.read_text()
    assert 'if __name__ == "__main__":' in src
