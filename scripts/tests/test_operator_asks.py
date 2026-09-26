#!/usr/bin/env python3
"""`scripts/lib/operator_asks.py` — round 0's attribution input.

🔴 EVERY FIXTURE HERE IS SYNTHETIC. `devrc` is PUBLIC and `CLAUDE.md` forbids
committing captured text — "anyone's message bodies, prompts, transcripts or
chat content, however it arrives (an eval capture, a fixture, a debug dump)". A
test needs the SHAPE, so the asks below are invented sentences about invented
subjects. Nothing in this file was copied out of a transcript.

WHAT THIS MODULE'S BUGS LOOK LIKE, which is what these tests are shaped around:
the dangerous failure is not a crash, it is a block that renders as "the
operator asked for nothing" when the truth is "nobody could read what he asked
for". That reading LICENSES the deletion suggestion the module exists to
prevent, so the empty and partial paths get more tests than the happy one.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))

import operator_asks as oa  # noqa: E402


# --------------------------------------------------------------------------
# session ids out of commit bodies
# --------------------------------------------------------------------------

SID_A = "aaaaaaaa-1111-4222-8333-444444444444"
SID_B = "bbbbbbbb-5555-4666-8777-888888888888"


def body(*ids, subject="chore: a synthetic commit"):
    lines = [subject, ""]
    for i in ids:
        lines.append(f"Claude-Session-Id: {i}")
    return "\n".join(lines)


def test_ids_are_read_out_of_the_body_and_deduped_across_bodies():
    got = oa.session_ids_from_bodies([
        body(SID_A), body(SID_A, SID_B), body(SID_B), "",
    ])
    assert got == (SID_A, SID_B), got


def test_a_squash_body_repeating_one_id_resolves_to_ONE_session():
    """The measured shape: GitHub concatenates every squashed message.

    Not a nicety — the extractor is invoked with one `--session` per id, so a
    12x repeat would walk the same transcript 12 times and the ledger would
    report 12 sessions for one.
    """
    squash = "\n".join([body(SID_A, subject=f"commit {n}") for n in range(12)])
    assert squash.count("Claude-Session-Id:") == 12
    assert oa.session_ids_from_bodies([squash]) == (SID_A,)


def test_a_control_character_in_a_trailer_is_dropped_by_the_writers_predicate():
    """Safety, via `session_trailer.valid_id` — not a shape check.

    The escape-to-a-terminal hazard `handoff_arc` records. An id that merely
    LOOKS unusual must survive; one carrying a C0 control must not.
    """
    assert oa.session_ids_from_bodies([body("ses_AnotherRuntimesToken123")]) == (
        "ses_AnotherRuntimesToken123",
    ), "a non-uuid id from another runtime was dropped — that is a shape check"
    assert oa.session_ids_from_bodies([f"x\n\nClaude-Session-Id: a\x1bb\n"]) == ()


# --------------------------------------------------------------------------
# clipping
# --------------------------------------------------------------------------

def test_clip_keeps_the_head_because_the_head_is_the_instruction():
    instruction = "wire the thing to the other thing so it stops doing X"
    paste = "\n".join(f"line {n} of pasted output" for n in range(400))
    msg = instruction + "\n" + paste
    kept, withheld = oa.clip(msg, cap=120)
    assert kept.startswith(instruction[:60]), kept[:80]
    assert withheld == len(msg) - 120
    assert paste.splitlines()[-1] not in kept


def test_clip_reports_zero_withheld_when_it_fits():
    assert oa.clip("short", cap=100) == ("short", 0)


def test_a_nonpositive_cap_degrades_into_a_ledger_line_not_a_traceback():
    """A misconfigured cap must not take the whole brief down with it."""
    assert oa.clip("abcdef", cap=0) == ("", 6)
    assert oa.clip("abcdef", cap=-5) == ("", 6)


# --------------------------------------------------------------------------
# bot filtering — direction matters
# --------------------------------------------------------------------------

def test_a_known_bot_comment_is_skipped_and_counted():
    asks, bots = oa.asks_from_comments([
        {"author": {"login": "github-actions[bot]"}, "body": "a CI summary"},
        {"author": {"login": "ZacxDev"}, "body": "do it the other way"},
    ])
    assert [a.text for a in asks] == ["do it the other way"]
    assert bots == 1


def test_an_UNKNOWN_author_is_KEPT_because_dropping_an_ask_is_the_worse_error():
    """The fail-safe direction. An unrecognised login is treated as the
    operator: over-including one comment is recoverable, losing the ask is the
    defect this module exists to prevent."""
    asks, bots = oa.asks_from_comments([
        {"author": {"login": "some-new-login-nobody-enumerated"},
         "body": "please keep the retry"},
    ])
    assert [a.text for a in asks] == ["please keep the retry"]
    assert bots == 0


def test_bot_matching_is_case_folded():
    assert oa.is_bot("Dependabot[Bot]")
    assert not oa.is_bot("zach")


# --------------------------------------------------------------------------
# the extractor's jsonl contract
# --------------------------------------------------------------------------

def jl(*rows):
    return "\n".join(json.dumps(r) for r in rows)


def test_only_typed_rows_become_asks():
    """`command` rows are a bare `/slash` (measured at 8 B) — not an ask, and
    counting them would pad the ledger with content nobody wrote."""
    out = jl(
        {"kind": "typed", "text": "make it idempotent", "session_id": SID_A},
        {"kind": "command", "text": "/resume", "session_id": SID_A},
        {"kind": "typed", "text": "   ", "session_id": SID_A},
    )
    asks, dropped = oa.parse_typed_rows(out)
    assert [a.text for a in asks] == ["make it idempotent"]
    assert dropped == {}, dropped


def test_one_unparseable_line_does_not_lose_the_rest():
    out = "\n".join([
        json.dumps({"kind": "typed", "text": "first", "session_id": SID_A}),
        "{not json",
        json.dumps({"kind": "typed", "text": "second", "session_id": SID_A}),
    ])
    asks, dropped = oa.parse_typed_rows(out)
    assert [a.text for a in asks] == ["first", "second"]
    assert dropped == {"an unparseable --jsonl line": 1}, dropped


def test_the_extractor_exit_vocabulary_is_pinned_to_what_the_script_documents():
    """Two-way: every code this module explains must be one the extractor
    documents, and every documented non-zero must have an explanation here.

    Without this, the extractor could add or renumber an exit and
    `extractor_reason` would keep printing a confident wrong sentence.
    """
    script = (Path(__file__).resolve().parents[1]
              / "session-analysis" / "extract_user_msgs.py")
    text = script.read_text(encoding="utf-8")
    documented = set()
    for line in text.splitlines():
        s = line.strip()
        # the `--help` epilog lists them as `  N  <reason>`
        if len(s) > 2 and s[0].isdigit() and s[1] == " " and not s.startswith("0 "):
            documented.add(int(s[0]))
    assert documented, "could not find the extractor's documented exit codes"
    ours = set(oa.EXTRACTOR_REASONS)
    assert ours == documented, (
        f"explained here but not documented there: {sorted(ours - documented)}; "
        f"documented there but unexplained here: {sorted(documented - ours)}"
    )


def test_an_unknown_exit_code_still_gets_a_reason_never_no_asks():
    r = oa.extractor_reason(99, "boom\nlast line")
    assert "99" in r and "last line" in r


# --------------------------------------------------------------------------
# 🔴 the render contract — the half that stops the defect
# --------------------------------------------------------------------------

def test_no_asks_and_no_sources_says_WE_LOOKED_NOWHERE_not_nothing_was_asked():
    out = oa.render([], unmeasured=(), session_ids=())
    assert oa.UNKNOWN_DIRECTIVE in out
    assert "no source was consulted at all" in out
    assert "NO OPERATOR ASK COULD BE READ" in out


def test_an_unreadable_source_renders_a_named_UNKNOWN_with_the_directive():
    out = oa.render([], unmeasured=[
        oa.Unmeasured(oa.SOURCE_SESSION, "no commit carries a trailer")])
    assert "! session transcript: UNKNOWN — no commit carries a trailer" in out
    assert oa.UNKNOWN_DIRECTIVE in out


def test_the_directive_ships_even_when_SOME_asks_were_read():
    """A partial read is still partial, and the source that FAILED is exactly
    the one a deletion candidate would be wrong about. An earlier shape only
    printed the directive when the block was completely empty."""
    out = oa.render(
        [oa.Ask(source=oa.SOURCE_PR_BODY, text="keep the retry")],
        unmeasured=[oa.Unmeasured(oa.SOURCE_SESSION, "extractor exited 5")],
    )
    assert "keep the retry" in out
    assert oa.UNKNOWN_DIRECTIVE in out, (
        "asks were read, so the directive was dropped — but a source still "
        "failed, which is when it matters"
    )


def test_a_fully_successful_read_does_NOT_nag_with_the_unknown_directive():
    """The negative control for the test above: a directive printed on every
    run is a directive nobody reads."""
    out = oa.render(
        [oa.Ask(source=oa.SOURCE_SESSION, text="an ask", session_id=SID_A)],
        unmeasured=(), session_ids=(SID_A,), projects_root="/nonexistent",
    )
    assert oa.UNKNOWN_DIRECTIVE not in out


def test_asks_are_rendered_VERBATIM_and_never_summarised():
    ask = "do NOT add a retry here; the caller already retries and I want the loud failure"
    out = oa.render([oa.Ask(source=oa.SOURCE_SESSION, text=ask, session_id=SID_A)],
                    session_ids=(SID_A,), projects_root="/nonexistent")
    assert f"> {ask}" in out


def test_a_multiline_ask_keeps_every_line_quoted():
    ask = "first line of the ask\nsecond line that changes it\nthird"
    out = oa.render([oa.Ask(source=oa.SOURCE_SESSION, text=ask, session_id=SID_A)],
                    session_ids=(SID_A,), projects_root="/nonexistent")
    for line in ask.splitlines():
        assert f"> {line}" in out


def test_clipping_is_reported_in_bytes_and_points_at_the_full_text():
    long = "the real instruction\n" + ("x" * 5000)
    out = oa.render([oa.Ask(source=oa.SOURCE_SESSION, text=long, session_id=SID_A)],
                    session_ids=(SID_A,), cap=100, projects_root="/nonexistent")
    assert "the real instruction" in out
    assert "clipped:" in out and "withheld" in out
    assert "extract_user_msgs.py" in out, "no route to the text it withheld"
    assert "NOT absent ask" in out


def test_the_block_ceiling_drops_messages_and_SAYS_how_many():
    asks = [oa.Ask(source=oa.SOURCE_SESSION, text="a" * 200, session_id=SID_A)
            for _ in range(50)]
    out = oa.render(asks, session_ids=(SID_A,), cap=200, ceiling=500,
                    projects_root="/nonexistent")
    assert "dropped to the block ceiling: " in out
    # the number must be non-zero, or the ceiling silently did nothing
    line = [l for l in out.splitlines() if "dropped to the block ceiling" in l][0]
    assert "ceiling: 0" not in line, line


def test_sources_are_labelled_and_never_merged():
    out = oa.render([
        oa.Ask(source=oa.SOURCE_SESSION, text="typed while working", session_id=SID_A),
        oa.Ask(source=oa.SOURCE_PR_BODY, text="written about the work"),
        oa.Ask(source=oa.SOURCE_PR_COMMENT, text="said on the PR", who="ZacxDev"),
    ], session_ids=(SID_A,), projects_root="/nonexistent")
    for s in (oa.SOURCE_SESSION, oa.SOURCE_PR_BODY, oa.SOURCE_PR_COMMENT):
        assert f"### from the {s}" in out, s
    # Session transcripts rank above the PR description: one is what the
    # operator typed while working, the other is often written BY the agent
    # about the work.
    assert (out.index(f"### from the {oa.SOURCE_SESSION}")
            < out.index(f"### from the {oa.SOURCE_PR_BODY}")
            < out.index(f"### from the {oa.SOURCE_PR_COMMENT}"))


def test_the_block_says_it_carries_no_agent_output_and_how_to_get_it():
    """The operator asked for user messages only, plus a reference for the
    agent side."""
    out = oa.render([oa.Ask(source=oa.SOURCE_SESSION, text="x", session_id=SID_A)],
                    session_ids=(SID_A,), projects_root="/nonexistent")
    assert "operator's own words ONLY" in out
    assert "no transcript file for these ids on this host" in out


def test_the_agent_side_reference_names_real_paths_when_they_exist(tmp_path):
    proj = tmp_path / "-home-zach-workspace-devrc"
    proj.mkdir()
    (proj / f"{SID_A}.jsonl").write_text("{}\n")
    lines = oa.agent_side_reference([SID_A], projects_root=tmp_path)
    joined = "\n".join(lines)
    assert f"{SID_A}.jsonl" in joined
    assert "jq -r" in joined, "named a path but gave no way to read it"


# --------------------------------------------------------------------------
# id safety at the RENDER layer
# --------------------------------------------------------------------------

def test_an_unsafe_id_never_reaches_the_rendered_block():
    """`handoff_arc` records `\\x1b[2J\\x1b]0;PWNED\\x07` reaching terminals raw
    from commit bodies in four repos. The brief is printed to a tty."""
    bad = "a\x1b]0;PWNED\x07b"
    out = oa.render([], session_ids=(bad,), projects_root="/nonexistent")
    assert "\x1b" not in out, "an escape sequence reached the rendered brief"
    assert "<unsafe-id>" in out


def test_an_unsafe_id_is_dropped_from_the_pull_command_not_quoted_into_it():
    long = "instruction\n" + "y" * 5000
    out = oa.render(
        [oa.Ask(source=oa.SOURCE_SESSION, text=long, session_id=SID_A)],
        session_ids=(SID_A, "b\x00ad"), cap=50, projects_root="/nonexistent",
    )
    cmd = [l for l in out.splitlines() if "extract_user_msgs.py" in l]
    assert cmd, out
    assert SID_A in cmd[0]
    assert "\x00" not in cmd[0]


def test_a_glob_metacharacter_in_an_id_cannot_widen_the_transcript_match(tmp_path):
    """`valid_id` does not reject `*` — it is not a control — so the glob is
    escaped separately. Without that, one bad id matches every session's
    transcript and the brief attributes their asks to this PR."""
    proj = tmp_path / "-home-zach-workspace-devrc"
    proj.mkdir()
    (proj / f"{SID_A}.jsonl").write_text("{}\n")
    (proj / f"{SID_B}.jsonl").write_text("{}\n")
    assert oa.transcript_paths(["*"], projects_root=tmp_path) == [], (
        "a bare `*` matched real transcripts"
    )
    assert len(oa.transcript_paths([SID_A], projects_root=tmp_path)) == 1


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))


# --------------------------------------------------------------------------
# 🔴 the classifier — `kind: "typed"` is NOT the operator
#
# MEASURED over 981 sessions on this host: 99.16% of user-role BYTES are
# machine-generated (injected skill bodies 59.5%, `<task-notification>` 38.9%,
# hook feedback 0.6%). The operator is 0.84%, median 18 B. This filter does
# nearly all the work, so it gets the most tests.
#
# The fixtures imitate the SHAPE of each injected form. None is copied from a
# transcript: `devrc` is public and captured text is forbidden even in a fixture.
# --------------------------------------------------------------------------

def test_a_subagent_notification_is_not_an_operator_ask():
    """38.9% of user-role bytes, mean 5,446 B. The live end-to-end run is how
    this was found — every unit fixture had been a hand-written ask, so the
    suite was green while the real block was ~99% subagent output."""
    row = ("<task-notification>\n<task-id>abc123</task-id>\n"
           "<status>completed</status>\n<result>I finished the thing and here "
           "is a long report about it.</result>\n</task-notification>")
    assert "task-notification" in oa.non_operator_reason(row)


def test_an_injected_skill_body_is_not_an_operator_ask():
    """59.5% of user-role bytes and THE HARDEST CLASS TO SPOT — it carries no
    wrapping tag, so it reads as prose until you notice the size."""
    row = ("Base directory for this skill: /home/x/.claude/skills/thing\n\n"
           "# /thing — do the thing\n\nSteps\n1. read `<doc>` in `<repo>`\n")
    assert "SKILL BODY" in oa.non_operator_reason(row)


def test_hook_feedback_is_not_an_operator_ask():
    for marker in ("Stop hook feedback:", "PostToolUse:", "PreToolUse:"):
        row = f"{marker} the guard says you must do the bookkeeping step"
        assert "hook feedback" in oa.non_operator_reason(row), marker


def test_every_enumerated_leading_tag_is_classified():
    """Two-way-ish: the enumeration must actually be consulted. A tag listed in
    `INJECTED_LEADING_TAGS` that the function does not catch is a dead entry
    that reads as coverage."""
    for tag in oa.INJECTED_LEADING_TAGS:
        assert oa.non_operator_reason(f"<{tag}>\nsomething\n</{tag}>"), tag


def test_an_interrupt_marker_is_not_an_operator_ask():
    assert oa.non_operator_reason("[Request interrupted by user]")


def test_a_REAL_ask_survives_the_classifier_including_a_long_one():
    """The negative control, and the direction that matters: a filter that eats
    asks is strictly worse than no filter."""
    assert oa.non_operator_reason("wire it to pull user messages") == ""
    assert oa.non_operator_reason("x" * 40000) == "", (
        "a long ask was classified as machine output — size is not the "
        "discriminator, and the skill-body class is why that is tempting"
    )


def test_an_ask_that_MENTIONS_a_hook_or_a_skill_is_still_an_ask():
    """🔴 THE MISCLASSIFICATION THAT WOULD HURT MOST, because the operator talks
    about these systems constantly — including in the ask that created this
    module. The markers are looked for in a BOUNDED window at the head, so a
    record that merely quotes one further down survives."""
    tail = "y" * oa._MARKER_WINDOW
    ask = (f"can you look at why the guard fires\n{tail}\n"
           "Stop hook feedback: was what it printed")
    assert oa.non_operator_reason(ask) == "", (
        "an ask discussing hook output was classified as hook output"
    )
    assert oa.non_operator_reason(
        "the Base directory for this skill: line is what I want you to match "
        "on" + "z" * oa._MARKER_WINDOW) != "", (
        "deliberately documents the LIMIT: a marker inside the head window "
        "wins even in prose about it. If this ever needs to change, the fix is "
        "a stricter anchor, not a wider window."
    )


def test_the_drop_ledger_reports_a_REASON_per_class_not_a_bare_count():
    out = jl(
        {"kind": "typed", "session_id": SID_A,
         "text": "<task-notification>\n<result>done</result>\n</task-notification>"},
        {"kind": "typed", "session_id": SID_A,
         "text": "Base directory for this skill: /x\n\n# /x"},
        {"kind": "typed", "session_id": SID_A, "text": "the actual ask"},
    )
    asks, dropped = oa.parse_typed_rows(out)
    assert [a.text for a in asks] == ["the actual ask"]
    assert len(dropped) == 2, dropped
    assert any("task-notification" in k for k in dropped)
    assert any("SKILL BODY" in k for k in dropped)


def test_the_rendered_block_names_what_the_classifier_dropped():
    """A silent filter hides a classifier bug indefinitely, and this one decides
    ~99% of the bytes."""
    out = oa.render(
        [oa.Ask(source=oa.SOURCE_SESSION, text="the ask", session_id=SID_A)],
        session_ids=(SID_A,), projects_root="/nonexistent",
        dropped={"<task-notification> — machine-generated, not typed by the operator": 12},
    )
    assert "dropped 12 record(s)" in out
    assert "task-notification" in out


def test_a_harness_authored_note_is_not_an_operator_ask():
    """Found by the LIVE run and by no fixture — which is why this enumeration
    is explicitly open rather than claimed complete."""
    assert oa.non_operator_reason(
        'Background agent "Round 4 delta re-audit" was stopped by the user.')
    assert oa.non_operator_reason(
        "Caveat: The messages below were generated while summarising")


def test_every_harness_note_pattern_is_actually_consulted():
    """A pattern in the tuple that the function never checks is a dead entry
    that reads as coverage."""
    for pat in oa.HARNESS_NOTE_PATTERNS:
        assert oa.non_operator_reason(f"something {pat} tail"), pat


def test_the_PR_description_is_labelled_as_probably_agent_written():
    """🔴 Filing agent-authored prose under "the operator's own asks" would
    MANUFACTURE an author of record — the mirror of the defect this module
    exists to fix. It is read only because it is the one source available on a
    PR whose commits carry no trailer."""
    out = oa.render([oa.Ask(source=oa.SOURCE_PR_BODY, text="some PR prose")],
                    projects_root="/nonexistent")
    assert "written **by the agent**" in out
    assert "unless the wording is plainly his" in out


def test_a_session_only_block_does_NOT_carry_the_PR_body_caveat():
    """The negative control: a caveat printed on every run is one nobody reads."""
    out = oa.render([oa.Ask(source=oa.SOURCE_SESSION, text="an ask",
                            session_id=SID_A)],
                    session_ids=(SID_A,), projects_root="/nonexistent")
    assert "written **by the agent**" not in out
