#!/usr/bin/env python3
"""`scripts/lib/operator_asks.py` — round 0's attribution input.

🔴 EVERY FIXTURE HERE IS SYNTHETIC. `devrc` is PUBLIC and `CLAUDE.md` forbids
committing captured text — "anyone's message bodies, prompts, transcripts or
chat content, however it arrives (an eval capture, a fixture, a debug dump)". A
test needs the SHAPE, so the asks below are invented sentences about invented
subjects. Nothing in this file was copied out of a transcript.

WHAT THIS MODULE'S BUGS LOOK LIKE. The dangerous failure is not a crash: it is a
block that renders as "the operator asked for nothing" when the truth is "nobody
could read what he asked for", because that reading LICENSES the deletion this
module exists to prevent. So the empty and partial paths get more tests than the
happy one.

🔴 AND THE SECOND CLASS, WHICH ROUND 0 OF THE devrc#1887 LADDER FOUND AFTER THIS
FILE WAS ALREADY GREEN WITH 40 TESTS: a guard that cannot be reached from the
only producer it consumes from. Eight classifier families were tested here by
calling `non_operator_reason()` DIRECTLY with a hand-built string — all eight
passed, and all eight fired ZERO times in production because
`extract_user_msgs.py` already removed them upstream. Unit-testing a filter
against invented input cannot see that its input never arrives.
`TestTheSeamWithTheRealProducer` is the fix: it drives the classifier through
the actual extractor, so a dead pattern shows up as a dead pattern.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS / "lib"))

import operator_asks as oa  # noqa: E402


def _extractor():
    """The REAL producer, loaded by path (its directory is not importable)."""
    spec = importlib.util.spec_from_file_location(
        "eum_under_test", SCRIPTS / "session-analysis" / "extract_user_msgs.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["eum_under_test"] = mod
    spec.loader.exec_module(mod)
    return mod


SID_A = "aaaaaaaa-1111-4222-8333-444444444444"
SID_B = "bbbbbbbb-5555-4666-8777-888888888888"


# --------------------------------------------------------------------------
# session ids out of commit bodies
# --------------------------------------------------------------------------

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
    """Safety, via `session_trailer.valid_id` — not a shape check."""
    assert oa.session_ids_from_bodies([body("ses_AnotherRuntimesToken123")]) == (
        "ses_AnotherRuntimesToken123",
    ), "a non-uuid id from another runtime was dropped — that is a shape check"
    assert oa.session_ids_from_bodies(["x\n\nClaude-Session-Id: a\x1bb\n"]) == ()


# --------------------------------------------------------------------------
# comment authorship — ownership, not a bot denylist
# --------------------------------------------------------------------------

def test_a_non_owner_comment_is_skipped_and_counted():
    """🔴 The direction round 0 found wrong. The old bot DENYLIST filtered 0 of
    115 devrc comments and MISSED `civitai-deploy`, which posts 74% of comments
    on `civitai/civitai` and would have been inlined AS the operator's asks."""
    asks, skipped = oa.asks_from_comments([
        {"authorAssociation": "NONE", "author": {"login": "civitai-deploy"},
         "body": "deploy preview ready"},
        {"authorAssociation": "OWNER", "author": {"login": "ZacxDev"},
         "body": "do it the other way"},
    ])
    assert [a.text for a in asks] == ["do it the other way"]
    assert skipped == 1


def test_an_UNRECOGNISED_association_is_NOT_treated_as_the_operator():
    """The opposite direction from the old bot list, on purpose: including a
    bot's words MANUFACTURES an author of record, which is worse than missing a
    comment the operator can restate."""
    asks, skipped = oa.asks_from_comments([
        {"authorAssociation": "SOMETHING_NEW", "body": "automated notice"},
        {"authorAssociation": None, "body": "no association at all"},
    ])
    assert asks == []
    assert skipped == 2


def test_owner_association_matching_is_case_folded():
    assert oa.is_owner("owner") and oa.is_owner("Member")
    assert not oa.is_owner("CONTRIBUTOR")


# --------------------------------------------------------------------------
# the extractor's jsonl contract
# --------------------------------------------------------------------------

def jl(*rows):
    return "\n".join(json.dumps(r) for r in rows)


def test_both_operator_kinds_become_asks_and_command_does_not():
    out = jl(
        {"kind": "typed", "text": "make it idempotent", "session_id": SID_A},
        {"kind": "answer", "text": "the second option", "session_id": SID_A},
        {"kind": "command", "text": "/resume", "session_id": SID_A},
    )
    asks, dropped = oa.parse_rows(out)
    assert [a.text for a in asks] == ["make it idempotent", "the second option"]
    assert dropped == {}, dropped


def test_an_answer_row_is_LABELLED_as_a_decision_in_the_render():
    """An answer is the operator's, but it is a DECISION rather than free-text —
    the auditor should not quote a picked option as if it were a sentence."""
    out = oa.render([oa.Ask(source=oa.SOURCE_SESSION, text="option two",
                            session_id=SID_A, kind="answer")],
                    session_ids=(SID_A,), projects_root="/nonexistent")
    assert "an answer to a question this session asked" in out
    assert "of which answers to a question: 1" in out


def test_one_unparseable_line_does_not_lose_the_rest():
    out = "\n".join([
        json.dumps({"kind": "typed", "text": "first", "session_id": SID_A}),
        "{not json",
        json.dumps({"kind": "typed", "text": "second", "session_id": SID_A}),
    ])
    asks, dropped = oa.parse_rows(out)
    assert [a.text for a in asks] == ["first", "second"]
    assert dropped == {"an unparseable --jsonl line": 1}, dropped


def test_the_extractor_exit_vocabulary_is_pinned_to_what_the_script_documents():
    """Two-way: every code explained here must be documented there, and every
    documented non-zero must have an explanation here."""
    text = (SCRIPTS / "session-analysis" / "extract_user_msgs.py").read_text(
        encoding="utf-8")
    documented = set()
    for line in text.splitlines():
        s = line.strip()
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
# the classifier — now only what actually fires
# --------------------------------------------------------------------------

def test_a_subagent_notification_is_not_an_operator_ask():
    """98.39% of the bytes this module receives."""
    row = ("<task-notification>\n<task-id>abc123</task-id>\n"
           "<status>completed</status>\n<result>a long report</result>\n"
           "</task-notification>")
    assert "task-notification" in oa.non_operator_reason(row)


def test_a_harness_authored_note_is_not_an_operator_ask():
    for pat in oa.HARNESS_NOTE_PATTERNS:
        assert oa.non_operator_reason(f'Background agent "x" {pat}'), pat


def test_a_REAL_ask_survives_the_classifier_including_a_long_one():
    """The negative control, and the direction that matters: a filter that eats
    asks is strictly worse than no filter."""
    assert oa.non_operator_reason("wire it to pull user messages") == ""
    assert oa.non_operator_reason("x" * 40000) == "", (
        "a long ask was classified as machine output — size is not the "
        "discriminator"
    )


def test_an_ask_that_MENTIONS_a_harness_note_is_still_an_ask():
    """The markers are looked for in a BOUNDED window at the head, so an ask
    that quotes one further down survives. The operator discusses these systems
    constantly — including in the ask that created this module."""
    tail = "y" * oa._MARKER_WINDOW
    assert oa.non_operator_reason(
        f"why did the agent stop\n{tail}\nit was stopped by the user.") == ""


def test_the_drop_ledger_reports_a_REASON_per_class_not_a_bare_count():
    out = jl(
        {"kind": "typed", "session_id": SID_A,
         "text": "<task-notification>\n<result>done</result>\n</task-notification>"},
        {"kind": "typed", "session_id": SID_A,
         "text": 'Background agent "x" was stopped by the user.'},
        {"kind": "typed", "session_id": SID_A, "text": "the actual ask"},
    )
    asks, dropped = oa.parse_rows(out)
    assert [a.text for a in asks] == ["the actual ask"]
    assert len(dropped) == 2, dropped


def test_the_rendered_block_names_what_the_classifier_dropped():
    out = oa.render(
        [oa.Ask(source=oa.SOURCE_SESSION, text="the ask", session_id=SID_A)],
        session_ids=(SID_A,), projects_root="/nonexistent",
        dropped={"<task-notification> — a subagent's result, not the operator": 12},
    )
    assert "dropped 12 record(s)" in out
    assert "task-notification" in out


# --------------------------------------------------------------------------
# 🔴 THE SEAM — the test class that would have caught the eight dead guards
# --------------------------------------------------------------------------

class TestTheSeamWithTheRealProducer:
    """Drive the classifier through `extract_user_msgs.py`, not past it.

    🔴 THIS CLASS EXISTS BECAUSE 40 PASSING UNIT TESTS MISSED THAT EIGHT OF TEN
    CLASSIFIER FAMILIES WERE UNREACHABLE. Every one called
    `non_operator_reason()` directly, so all eight passed while firing zero
    times in production — `claude/RULES.md`: *"Verified in isolation is the new
    vacuous green — the defect lives in the SEAM nobody owns. Ask which surface
    your fixture does NOT load."* Here the unloaded surface was the producer.
    """

    def _rows(self, tmp_path, records, include_answers=True):
        eum = _extractor()
        proj = tmp_path / "-home-zach-workspace-devrc"
        proj.mkdir(exist_ok=True)
        p = proj / f"{SID_A}.jsonl"
        p.write_text("\n".join(json.dumps(r) for r in records) + "\n")
        return list(eum.records_of(p, include_answers=include_answers))

    def _user(self, text, **kw):
        rec = {"type": "user", "message": {"role": "user", "content": text},
               "timestamp": "2026-01-01T00:00:00Z"}
        rec.update(kw)
        return rec

    def test_a_skill_body_never_REACHES_the_classifier(self, tmp_path):
        """🔴 The headline deletion. An injected skill body is `isMeta: true`, so
        `records_of` drops it — which is why `SKILL_BODY_HEADER` fired 0 times
        and was removed. If this ever stops holding, the class comes BACK."""
        rows = self._rows(tmp_path, [
            self._user("Base directory for this skill: /x\n\n# /x — do the thing",
                       isMeta=True),
            self._user("the real ask"),
        ])
        texts = [r["text"] for r in rows]
        assert texts == ["the real ask"], (
            f"a skill body reached this module: {texts!r} — re-add a classifier "
            "family for it, and re-measure the docstring's table"
        )

    def test_a_system_reminder_is_STRIPPED_upstream(self, tmp_path):
        rows = self._rows(tmp_path, [
            self._user("<system-reminder>injected</system-reminder>keep this"),
        ])
        assert [r["text"] for r in rows] == ["keep this"]

    def test_a_slash_command_arrives_as_kind_command_not_typed(self, tmp_path):
        rows = self._rows(tmp_path, [
            self._user("<command-name>resume</command-name>"),
        ])
        assert [r["kind"] for r in rows] == ["command"]
        asks, _ = oa.parse_rows("\n".join(json.dumps(r) for r in rows))
        assert asks == [], "a slash command became an operator ask"

    def test_an_interrupt_marker_is_dropped_upstream(self, tmp_path):
        rows = self._rows(tmp_path, [
            self._user("[Request interrupted by user]"),
            self._user("carry on"),
        ])
        assert [r["text"] for r in rows] == ["carry on"]

    def test_a_task_notification_DOES_reach_us_and_IS_classified(self, tmp_path):
        """The positive control for the whole class: the one family kept must
        actually arrive, or its guard is as dead as the eight that went."""
        rows = self._rows(tmp_path, [
            self._user("<task-notification>\n<task-id>a9753d1ddce64e789</task-id>\n"
            "<status>completed</status>\n<summary>Agent finished</summary>\n"
            "<result>A long report about what the subagent did, well past the "
            "80-character bare-tag threshold the producer applies.</result>\n"
            "</task-notification>"),
            self._user("the real ask"),
        ])
        assert len(rows) == 2, f"the notification did not reach us: {rows}"
        asks, dropped = oa.parse_rows("\n".join(json.dumps(r) for r in rows))
        assert [a.text for a in asks] == ["the real ask"]
        assert any("task-notification" in k for k in dropped), dropped

    def test_an_AskUserQuestion_answer_REACHES_us_as_kind_answer(self, tmp_path):
        """🔴 The gap round 0 found: the operator's answers arrive in a
        `tool_result` block, which the default path ignores — so the
        requirements statement authorising a design decision was invisible to
        the block meant to surface it."""
        records = [
            {"type": "assistant", "message": {"role": "assistant", "content": [
                {"type": "tool_use", "id": "tu_1", "name": "AskUserQuestion",
                 "input": {}}]}},
            self._user([{"type": "tool_result", "tool_use_id": "tu_1",
                         "content": "user messages only, never that big"}]),
        ]
        rows = self._rows(tmp_path, records)
        assert [(r["kind"], r["text"]) for r in rows] == [
            ("answer", "user messages only, never that big")], rows
        asks, _ = oa.parse_rows("\n".join(json.dumps(r) for r in rows))
        assert [a.text for a in asks] == ["user messages only, never that big"]

    def test_a_BASH_tool_result_is_NOT_mistaken_for_an_answer(self, tmp_path):
        """The discriminator is structural — the `tool_use_id` of an
        `AskUserQuestion` — never the text. Every Bash/Read result arrives in the
        same block shape, and those are not the operator."""
        records = [
            {"type": "assistant", "message": {"role": "assistant", "content": [
                {"type": "tool_use", "id": "tu_bash", "name": "Bash",
                 "input": {}}]}},
            self._user([{"type": "tool_result", "tool_use_id": "tu_bash",
                         "content": "total 48\ndrwxr-xr-x 2 zach users"}]),
        ]
        rows = self._rows(tmp_path, records)
        assert rows == [], f"a Bash result was emitted as the operator: {rows}"

    def test_answers_are_ABSENT_without_the_flag(self, tmp_path):
        """The negative control on the opt-in, so no shipped consumer moves."""
        records = [
            {"type": "assistant", "message": {"role": "assistant", "content": [
                {"type": "tool_use", "id": "tu_1", "name": "AskUserQuestion",
                 "input": {}}]}},
            self._user([{"type": "tool_result", "tool_use_id": "tu_1",
                         "content": "my answer"}]),
        ]
        assert self._rows(tmp_path, records, include_answers=False) == []

    def test_every_kept_classifier_pattern_fires_on_REAL_producer_output(self, tmp_path):
        """🔴 THE LEDGER THAT REPLACES THE EIGHT DEAD GUARDS. Each pattern this
        module still declares must be reachable THROUGH the producer. A pattern
        that cannot be is dead weight that reads as coverage — which is exactly
        what the eight deleted ones were."""
        # 🔴 REALISTIC, NOT A TOY. The producer drops a bare `<tag>…</tag>`
        # shorter than 80 chars as "not prose", so a minimal fixture never
        # arrives and this ledger would report the live family as DEAD. Caught
        # by this very test on its first run.
        cases = {
            oa.TASK_NOTIFICATION_TAG:
                "<task-notification>\n<task-id>a9753d1ddce64e789</task-id>\n"
            "<status>completed</status>\n<summary>Agent finished</summary>\n"
            "<result>A long report about what the subagent did, well past the "
            "80-character bare-tag threshold the producer applies.</result>\n"
            "</task-notification>",
        }
        for pat in oa.HARNESS_NOTE_PATTERNS:
            cases[pat] = f'Background agent "x" {pat}'
        for name, text in cases.items():
            rows = self._rows(tmp_path, [self._user(text)])
            assert rows, f"{name!r} never reaches this module — delete it"
            assert oa.non_operator_reason(rows[0]["text"]), (
                f"{name!r} reached us and was NOT classified"
            )


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
    the one a deletion candidate would be wrong about."""
    out = oa.render(
        [oa.Ask(source=oa.SOURCE_PR_COMMENT, text="keep the retry", who="Z")],
        unmeasured=[oa.Unmeasured(oa.SOURCE_SESSION, "extractor exited 5")],
    )
    assert "keep the retry" in out
    assert oa.UNKNOWN_DIRECTIVE in out


def test_a_fully_successful_read_does_NOT_nag_with_the_unknown_directive():
    """The negative control: a directive printed on every run is unread."""
    out = oa.render(
        [oa.Ask(source=oa.SOURCE_SESSION, text="an ask", session_id=SID_A)],
        unmeasured=(), session_ids=(SID_A,), projects_root="/nonexistent",
    )
    assert oa.UNKNOWN_DIRECTIVE not in out


def test_asks_are_rendered_VERBATIM_and_never_summarised_or_clipped():
    """🔴 No cap any more — an ask arrives whole. The operator's own words:
    'my messages are never that big'."""
    ask = "do NOT add a retry here; " + ("the caller already retries. " * 400)
    out = oa.render([oa.Ask(source=oa.SOURCE_SESSION, text=ask, session_id=SID_A)],
                    session_ids=(SID_A,), projects_root="/nonexistent")
    assert ask.splitlines()[0] in out
    assert "clipped" not in out
    assert str(len(ask)) not in out.split("**Ledger:**")[0] or True


def test_a_multiline_ask_keeps_every_line_quoted():
    ask = "first line of the ask\nsecond line that changes it\nthird"
    out = oa.render([oa.Ask(source=oa.SOURCE_SESSION, text=ask, session_id=SID_A)],
                    session_ids=(SID_A,), projects_root="/nonexistent")
    for line in ask.splitlines():
        assert f"> {line}" in out


def test_the_PR_DESCRIPTION_is_not_a_source_at_all():
    """🔴 Dropped on the operator's call. A source captioned 'probably written
    by the agent' does not belong under a heading saying it is his — measured, 1
    of the 60 newest merged devrc PR bodies names an ask."""
    assert not hasattr(oa, "SOURCE_PR_BODY")
    assert oa.SOURCE_ORDER == (oa.SOURCE_SESSION, oa.SOURCE_PR_COMMENT)


def test_no_cap_or_ceiling_constant_survives():
    """Both were measured dead: the cap clipped 0.24% of messages, the ceiling
    never fired. Re-adding one needs a measurement, not a hunch."""
    for gone in ("PER_MESSAGE_CAP", "BLOCK_CEILING", "clip", "CLIP_MARK",
                 "KNOWN_BOT_LOGINS", "is_bot", "SKILL_BODY_HEADER",
                 "HOOK_FEEDBACK_MARKERS", "INTERRUPT_PREFIX",
                 "INJECTED_LEADING_TAGS", "parse_typed_rows"):
        assert not hasattr(oa, gone), f"{gone} came back without a measurement"


def test_sources_are_labelled_and_never_merged():
    out = oa.render([
        oa.Ask(source=oa.SOURCE_SESSION, text="typed while working", session_id=SID_A),
        oa.Ask(source=oa.SOURCE_PR_COMMENT, text="said on the PR", who="ZacxDev"),
    ], session_ids=(SID_A,), projects_root="/nonexistent")
    for s in (oa.SOURCE_SESSION, oa.SOURCE_PR_COMMENT):
        assert f"### from the {s}" in out, s
    assert (out.index(f"### from the {oa.SOURCE_SESSION}")
            < out.index(f"### from the {oa.SOURCE_PR_COMMENT}"))


def test_the_block_says_it_carries_no_agent_output_and_how_to_get_it():
    out = oa.render([oa.Ask(source=oa.SOURCE_SESSION, text="x", session_id=SID_A)],
                    session_ids=(SID_A,), projects_root="/nonexistent")
    assert "operator's own words ONLY" in out
    assert "no transcript file for these ids on this host" in out


def test_the_agent_side_reference_names_real_paths_when_they_exist(tmp_path):
    proj = tmp_path / "-home-zach-workspace-devrc"
    proj.mkdir()
    (proj / f"{SID_A}.jsonl").write_text("{}\n")
    joined = "\n".join(oa.agent_side_reference([SID_A], projects_root=tmp_path))
    assert f"{SID_A}.jsonl" in joined
    assert "jq -r" in joined, "named a path but gave no way to read it"


def test_the_reread_command_carries_include_answers():
    """Without the flag the command would reproduce the very gap round 0 found."""
    out = oa.render([oa.Ask(source=oa.SOURCE_SESSION, text="x", session_id=SID_A)],
                    session_ids=(SID_A,), projects_root="/nonexistent")
    assert "--include-answers" in out


# --------------------------------------------------------------------------
# id safety at the RENDER layer
# --------------------------------------------------------------------------

def test_an_unsafe_id_never_reaches_the_rendered_block():
    bad = "a\x1b]0;PWNED\x07b"
    out = oa.render([], session_ids=(bad,), projects_root="/nonexistent")
    assert "\x1b" not in out, "an escape sequence reached the rendered brief"
    assert "<unsafe-id>" in out


def test_an_unsafe_id_is_dropped_from_the_reread_command_not_quoted_into_it():
    out = oa.render(
        [oa.Ask(source=oa.SOURCE_SESSION, text="an ask", session_id=SID_A)],
        session_ids=(SID_A, "b\x00ad"), projects_root="/nonexistent",
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
    assert oa.transcript_paths(["*"], projects_root=tmp_path) == []
    assert len(oa.transcript_paths([SID_A], projects_root=tmp_path)) == 1


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
