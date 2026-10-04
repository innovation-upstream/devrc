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

def test_the_operator_is_viewerDidAuthor_and_NOT_repo_membership():
    """🔴 THIS PREDICATE WAS WRONG TWICE. A bot denylist filtered 0 of 115 devrc
    comments; then `authorAssociation in {OWNER,MEMBER,COLLABORATOR}` re-admitted
    the very bot the denylist missed — `civitai-deploy` is `MEMBER` — and newly
    admitted devrc's ten OTHER human collaborators, all org members. Both
    measured. The fixture carries that exact shape."""
    asks, skips, examined = oa.asks_from_comments([
        # the bot BOTH earlier predicates let through
        {"authorAssociation": "MEMBER", "viewerDidAuthor": False,
         "author": {"login": "civitai-deploy"}, "body": "deploy preview ready"},
        # a teammate: a repo MEMBER who is not the operator
        {"authorAssociation": "MEMBER", "viewerDidAuthor": False,
         "author": {"login": "a-teammate"}, "body": "looks good to me"},
        {"authorAssociation": "MEMBER", "viewerDidAuthor": True,
         "author": {"login": "ZacxDev"}, "body": "do it the other way"},
    ])
    assert [a.text for a in asks] == ["do it the other way"]
    assert examined == 3
    assert sum(skips.values()) == 2
    assert any("civitai-deploy" in k for k in skips), skips
    assert any("a-teammate" in k for k in skips), skips


def test_a_MISSING_viewerDidAuthor_is_UNKNOWN_not_a_silent_no():
    """The field's absence must not be guessed from a proxy — that is how this
    predicate went wrong twice."""
    asks, skips, examined = oa.asks_from_comments([
        {"authorAssociation": "OWNER", "body": "no authorship field at all"},
    ])
    assert asks == []
    assert examined == 1
    assert any("could not be established" in k for k in skips), skips
    assert any("NOT a claim" in k for k in skips), skips


def test_no_membership_or_denylist_predicate_survives():
    """Both wrong answers are gone, and neither may come back as a proxy."""
    for gone in ("OWNER_ASSOCIATIONS", "is_owner", "KNOWN_BOT_LOGINS", "is_bot"):
        assert not hasattr(oa, gone), f"{gone} came back"
    assert oa.VIEWER_FIELD == "viewerDidAuthor"


# --------------------------------------------------------------------------
# the extractor's jsonl contract
# --------------------------------------------------------------------------

def jl(*rows):
    return "\n".join(json.dumps(r) for r in rows)


def test_both_operator_kinds_become_asks_and_command_does_not():
    out = jl(
        {"kind": "typed", "text": "make it idempotent", "session_id": SID_A},
        {"kind": "decision", "text": "the second option", "session_id": SID_A},
        {"kind": "command", "text": "/resume", "session_id": SID_A},
    )
    asks, dropped = oa.parse_rows(out)
    assert [a.text for a in asks] == ["make it idempotent", "the second option"]
    assert dropped == {}, dropped


def test_the_SILENT_kind_ledger_holds_only_command():
    """🔴 THE EXEMPTION, FIXED AS A REVIEWED LITERAL. `SILENTLY_REFUSED_KINDS` is
    the one hole in "every refusal is ledgered", so it must not grow by someone
    adding a kind to make a noisy test quiet — which is exactly how the silent drop
    this replaced came to exist."""
    assert oa.SILENTLY_REFUSED_KINDS == ("command",), oa.SILENTLY_REFUSED_KINDS
    # ...and it must not overlap the ADMITTED set, or a kind would be both.
    assert not set(oa.SILENTLY_REFUSED_KINDS) & set(oa.OPERATOR_KINDS)


def test_an_UNKNOWN_kind_is_LEDGERED_not_dropped_in_silence():
    """🔴 THE PRODUCER/CONSUMER DRIFT CASE, which is the one that costs sessions. If
    `extract_user_msgs.py` grows a kind this module has never heard of, every such
    row is refused — correctly, it is not a known operator channel — but a SILENT
    refusal is indistinguishable from a transcript that held nothing, and
    `audit-dispatch.py` branches on `if dropped` to word the UNKNOWN. So the drop
    must be visible AND must name the kind."""
    out = jl(
        {"kind": "typed", "text": "make it idempotent", "session_id": SID_A},
        {"kind": "a_kind_from_the_future", "text": "something new",
         "session_id": SID_A},
    )
    asks, dropped = oa.parse_rows(out)
    assert [a.text for a in asks] == ["make it idempotent"]
    assert sum(dropped.values()) == 1, dropped
    assert any("a_kind_from_the_future" in k for k in dropped), (
        "an unknown kind was refused without naming itself in the ledger, so a "
        f"producer/consumer drift reads as an empty transcript: {dropped}")


def test_a_row_with_NO_kind_at_all_is_also_ledgered():
    """The boundary of the same claim. `row.get("kind")` is `None` for a row the
    producer wrote without the field, which normalises to `""` — and `""` is in
    neither the admitted set nor the silent set, so it must be ledgered rather than
    fall through a gap between two ledgers."""
    asks, dropped = oa.parse_rows(jl({"text": "no kind here", "session_id": SID_A}))
    assert asks == []
    assert dropped == {"a row carrying no `kind` at all": 1}, dropped


def test_an_answer_row_is_LABELLED_as_a_decision_in_the_render():
    """An answer is the operator's, but it is a DECISION rather than free-text —
    the auditor should not quote a picked option as if it were a sentence."""
    out = oa.render([oa.Ask(source=oa.SOURCE_SESSION, text="option two",
                            session_id=SID_A, kind="decision")],
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


def test_the_exit_6_REASON_says_zero_rows_of_ANY_kind_not_just_untyped():
    """🔴 THE TEXT, NOT ONLY THE CODE SET — and that gap is why this was stale for a
    release. `test_the_extractor_exit_vocabulary_is_pinned_to_what_the_script_
    documents` pins which exit CODES are explained; it reads none of the words, so
    `EXTRACTOR_REASONS[6]` went on saying "held no operator-typed message" after
    devrc#2011 widened exit 6 to mean zero rows of ANY kind. A guard's description
    claims coverage: that one covers the keys.

    This is an OPERATOR-FACING sentence — it is what `/audit-pr`'s round 0 prints as
    the reason nothing could be attributed — so the old wording told a reader that
    the operator had not TYPED while leaving them to assume a decision channel they
    had never heard of might still hold something. Pinned as the WHOLE normalised
    string, because the artefact IS prose and a guard on a word is walkable by
    rewording; a cosmetic reword fails here on purpose.

    AND TWO-WAY, against the PRODUCER's own `--help`: if the extractor ever narrows
    exit 6 back to one channel, the second half fails and this reason gets revisited
    rather than silently over-claiming in the other direction.
    """
    want = ("the transcripts were read and held zero rows of any kind — nothing "
            "typed, no slash command, and no AskUserQuestion decision")
    got = " ".join(oa.EXTRACTOR_REASONS[6].split())
    assert got == want, (
        "the exit-6 reason drifted from what the extractor documents. This string "
        "is printed to the operator as why nothing was attributed.\n  got:  %s\n"
        "  want: %s" % (got, want))
    doc = (SCRIPTS / "session-analysis" / "extract_user_msgs.py").read_text(
        encoding="utf-8")
    assert "zero rows of ANY kind" in doc, (
        "the extractor no longer documents exit 6 as zero rows of ANY kind, so the "
        "reason pinned above may now over-claim — re-read its --help and re-pin.")


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
    constantly — including in the ask that created this module.

    🔴 THE FIXTURE LENGTH IS A LITERAL, NOT `_MARKER_WINDOW`. An earlier version
    wrote `"y" * oa._MARKER_WINDOW`, which puts the marker just past the window
    FOR EVERY VALUE OF IT — so `_MARKER_WINDOW = 10**9` SURVIVED a mutation
    sweep. Narrowing was guarded and WIDENING was not, and widening is the
    direction this module calls "strictly worse than no filter". Round 1 found
    it. 900 is chosen to exceed the shipped 400 while staying a fixed number.
    """
    assert oa._MARKER_WINDOW < 900, (
        "the window grew past this fixture — raise the literal deliberately, "
        "and say why the wider window cannot eat an ask"
    )
    tail = "y" * 900
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

    def _rows(self, tmp_path, records):
        eum = _extractor()
        proj = tmp_path / "-home-zach-workspace-devrc"
        proj.mkdir(exist_ok=True)
        p = proj / f"{SID_A}.jsonl"
        p.write_text("\n".join(json.dumps(r) for r in records) + "\n")
        return list(eum.records_of(p))

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

    def test_an_AskUserQuestion_answer_REACHES_us_as_kind_decision(self, tmp_path):
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
            ("decision", "user messages only, never that big")], rows
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

    def test_an_UNANSWERED_question_is_never_quoted_as_an_operator_ask(
            self, tmp_path):
        """🔴 THE EXCLUSION `OPERATOR_KINDS` DECLARES, AS A BRANCH RATHER THAN A
        COMMENT. devrc#1955 added `decision_unanswered` — an `AskUserQuestion`
        that was asked and never answered — and that row's text is the AGENT's
        own question and option labels, not one word of the operator. Admitting
        it here would quote an agent's proposal back to the auditor as a
        requirement the operator stated: the exact attribution error round 0
        exists to prevent, pointed the other way.

        The row MUST still be produced (the extractor's own suite owns that);
        what this pins is that it reaches the producer and is refused here.
        """
        records = [
            {"type": "assistant", "message": {"role": "assistant",
             "timestamp": "2026-01-01T00:00:00Z", "content": [
                {"type": "tool_use", "id": "tu_gone", "name": "AskUserQuestion",
                 "input": {"questions": [{
                     "question": "ship it as correctness-only?",
                     "options": [{"label": "yes"}, {"label": "no"}]}]}}]}},
        ]
        rows = self._rows(tmp_path, records)
        assert [r["kind"] for r in rows] == ["decision_unanswered"], (
            f"the producer stopped emitting the unanswered state: {rows}")
        asks, dropped = oa.parse_rows("\n".join(json.dumps(r) for r in rows))
        assert asks == [], (
            "an unanswered question was quoted as something the operator "
            f"asked for: {[a.text for a in asks]}")
        # 🔴 REFUSED IS NOT THE SAME AS INVISIBLE, and this half is the devrc#1955
        # class one layer down. The refusal above is correct; dropping the row with
        # no `dropped[...]` entry is not, because the empty block downstream
        # branches on `if dropped` — so a silent refusal renders as "the transcripts
        # held nothing at all" when in fact a question WAS asked and deliberately
        # set aside. The reason must also NAME the kind, or a kind this module has
        # never heard of is indistinguishable from the one it means to refuse.
        assert dropped, (
            "`decision_unanswered` was discarded with no ledger entry — the "
            "silent-omission shape issue #1955 exists to fix. The sibling "
            "`ValueError` branch ledgers its own drop; this one must too.")
        assert any("decision_unanswered" in k for k in dropped), (
            "the drop is ledgered but does not name the kind, so a NEW kind and a "
            f"deliberately refused one read the same: {dropped}")
        assert sum(dropped.values()) == 1, dropped

    def test_decision_rows_ARE_admitted_the_positive_control_for_the_refusal(
            self, tmp_path):
        """The pair for the test above. A refusal that refused EVERYTHING would
        pass it while deleting the whole channel, so the admitted case is
        asserted in the same breath: 1 ask in, 0 from the unanswered row."""
        records = [
            {"type": "assistant", "message": {"role": "assistant", "content": [
                {"type": "tool_use", "id": "tu_ok", "name": "AskUserQuestion",
                 "input": {}}]}},
            self._user([{"type": "tool_result", "tool_use_id": "tu_ok",
                         "content": "the option he picked"}]),
        ]
        rows = self._rows(tmp_path, records)
        asks, _ = oa.parse_rows("\n".join(json.dumps(r) for r in rows))
        assert [a.text for a in asks] == ["the option he picked"], asks

    def test_the_decision_kind_is_pinned_to_the_producers_constant(self):
        """🔴 ONE SPELLING. This module cannot import the producer (it lives
        under `scripts/session-analysis/`), so the string is re-spelled here and
        read back out of the producer's own constant. A kind renamed upstream
        with this left behind drops every decision silently — the #1955 defect,
        restored, and a rename is exactly how it would come back."""
        eum = _extractor()
        assert oa.KIND_DECISION == eum.KIND_DECISION, (
            f"this module takes {oa.KIND_DECISION!r}; the producer emits "
            f"{eum.KIND_DECISION!r}")
        assert oa.KIND_DECISION in oa.OPERATOR_KINDS
        assert eum.KIND_DECISION_UNANSWERED not in oa.OPERATOR_KINDS, (
            "the unanswered state became an operator ask")

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
    assert ask[-40:] in out, "the tail was dropped — something is still clipping"
    assert "clipped" not in out


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
    assert oa.transcript_paths(["a[bc]d"], projects_root=tmp_path) == []
    assert len(oa.transcript_paths([SID_A], projects_root=tmp_path)) == 1


def test_transcript_paths_EXCLUDES_a_subagent_transcript_at_its_REAL_depth(tmp_path):
    """Delegation means `is_corpus_member` applies — and that filter became
    load-bearing HERE only when this function started delegating.

    🔴 THE FIXTURE DEPTH IS THE POINT. An excluded transcript lives at
    `<project>/<session-id>/subagents/<id>.jsonl` — THREE levels down. The old
    private glob was `*/`, one level, so it could never reach one: measured, 965
    matches on this host and **0** in an excluded directory. A first version of
    this test put the fixture one level down and therefore passed against a
    re-introduced private glob too — it guarded nothing. `find_transcript` globs
    `**/`, so delegating WIDENS the search to this depth, which is exactly why the
    filter now has to be asserted."""
    deep = tmp_path / "-home-zach-workspace-devrc" / SID_A / "subagents"
    deep.mkdir(parents=True)
    (deep / f"{SID_B}.jsonl").write_text("{}\n")
    assert oa.transcript_paths([SID_B], projects_root=tmp_path) == [], (
        "a subagents/ transcript resolved — either this module is globbing "
        "again, or the corpus-member filter stopped being applied"
    )


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))


# --------------------------------------------------------------------------
# 🔴 ROUND 1's FINDINGS — each guard names the defect it pins
# --------------------------------------------------------------------------

def test_a_rc0_coverage_note_becomes_an_UNKNOWN():
    """🔴 THE BIGGEST ROUND-1 FINDING. The extractor exits **0** having read only
    SOME of the ids it was given, and says so on stderr with a `!` prefix.
    Discarding that made a HALF-resolved session set render as a complete read
    with no UNKNOWN and no directive — handing round 0 exactly the licence to
    delete that this module exists to remove. The operator runs two hosts, so a
    missing transcript is the ORDINARY case.

    The wording is the extractor's to change; the `!` prefix is the contract
    (`print(f"! {note}", file=err)`), so that is what this reads.
    """
    err = ("! 1 of 2 selected session(s) have NO transcript on this host "
           "(peer host? pruned?): dddddddd-1111-4222-8333-444444444444\n"
           "sessions=1 msgs=7 deduped=1 out=-\n")
    notes = oa.coverage_notes(err)
    assert len(notes) == 1, notes
    assert "NO transcript on this host" in notes[0]
    assert "sessions=1" not in " ".join(notes), (
        "the ordinary summary line was read as a coverage gap"
    )


def test_a_FULL_rc0_run_produces_no_coverage_note():
    """The negative control: a note on every run is a note nobody reads, and it
    would put a permanent UNKNOWN in every brief."""
    assert oa.coverage_notes("sessions=2 msgs=9 deduped=0 out=-\n") == []
    assert oa.coverage_notes("") == []


def test_the_sources_line_says_NAMED_BY_not_resolved():
    """An id read from a trailer is not an id resolved to a transcript — and
    conflating them is what made the partial read look complete."""
    out = oa.render([], session_ids=(SID_A,), projects_root="/nonexistent")
    assert "NAMED BY" in out
    assert "session(s) resolved from" not in out


def test_a_comment_source_with_ZERO_comments_still_prints_a_line():
    """`consulted and empty` must be distinguishable from `not consulted`."""
    out = oa.render([], comments_examined=0, projects_root="/nonexistent")
    assert "0 comment(s) examined" in out


def test_the_comment_source_declares_that_REVIEW_comments_are_invisible():
    """`gh pr view --json comments` returns ISSUE comments only. An ask left in a
    review is absent, and its absence is UNKNOWN rather than zero — the same
    blind spot the claims reader already pins for itself."""
    out = oa.render([], comments_examined=3, projects_root="/nonexistent")
    assert "ISSUE comments ONLY" in out
    assert "REVIEW comment" in out


def test_the_block_warns_against_publishing_a_quoted_ask():
    """This repo is PUBLIC and findings land in tracked `.md` AND in public PR
    comments, neither of which any content gate covers.

    ⚠ This used to assert `DO NOT COMMIT ONE`, matching a warning whose scope was
    files only while its own text named PR comments as a destination. The
    surface-coverage assertion is in
    `test_the_captured_text_warning_covers_every_surface_not_just_files`; this one
    keeps the presence check."""
    out = oa.render([oa.Ask(source=oa.SOURCE_SESSION, text="x", session_id=SID_A)],
                    session_ids=(SID_A,), projects_root="/nonexistent")
    assert "DO NOT PUBLISH A QUOTED ASK" in out


def test_the_agents_own_preview_block_is_stripped_from_an_answer():
    """🔴 Same class as the PR description this module dropped: a `selected
    preview:` block is the AGENT'S OWN PLAN, and an auditor quoting it would
    attribute the agent's plan to the operator. Measured on devrc#1887: two
    answer rows of 1,124 B carried ~440 B of the operator's actual notes."""
    raw = ('The user answered: "Which?"="Option B (Recommended)" selected preview:\n'
           'AFTER:\n  module ~603 -> ~330 lines\n  tests 49 -> ~30')
    got = oa.strip_answer_framing(raw)
    assert "module ~603" not in got, "the agent's plan survived as the operator's ask"
    assert "Option B" in got, "the operator's actual choice was stripped too"
    assert "The user answered: " not in got


def test_an_answer_that_is_ONLY_framing_is_dropped_with_a_reason():
    out = jl({"kind": "decision", "session_id": SID_A,
              "text": "The user answered: selected preview:\nAFTER: nothing"})
    asks, dropped = oa.parse_rows(out)
    assert asks == []
    assert any("harness framing" in k for k in dropped), dropped


def test_every_framing_fragment_is_actually_stripped():
    """A fragment in the tuple that the function never removes is dead weight
    that reads as coverage — the shape of round 0's eight dead guards."""
    for frag in oa.ANSWER_FRAMING:
        assert frag not in oa.strip_answer_framing(f"keep this {frag} and this")


class TestRoundOneSeamFindings(TestTheSeamWithTheRealProducer):
    """Round 1's producer-side findings, driven through the real extractor."""

    def test_a_model_authored_compaction_summary_is_NOT_the_operator(self, tmp_path):
        """🔴 THE LARGEST MISCLASSIFICATION LEFT. Measured over this host: 16
        records carrying **211,362 B = 23.6% of the entire operator corpus** —
        212x the one harness family kept — all of them a MODEL's summary of a
        conversation, emitted as the operator's verbatim ask. `CLAUDE.md` also
        names "a model's summaries of them" as captured text a public repo must
        not commit."""
        rows = self._rows(tmp_path, [
            self._user(
                "This session is being continued from a previous conversation "
                "that ran out of context. The summary below covers the earlier "
                "portion of the conversation in detail.",
                isCompactSummary=True, isVisibleInTranscriptOnly=True),
            self._user("the real ask"),
        ])
        assert [r["text"] for r in rows] == ["the real ask"], (
            f"a compaction summary reached this module: {rows}"
        )

    def test_the_DEFAULT_now_carries_the_decision_channel(self, tmp_path):
        """🔴 THIS TEST IS THE REVERSE OF THE ONE IT REPLACES, AND THE REVERSAL
        IS THE POINT. It used to read
        `test_the_opt_in_DEFAULT_is_pinned_not_just_the_parameter` and assert
        `records_of(p) == []` — "#1887's whole compatibility guarantee": the
        decision channel was opt-in so no shipped consumer's output moved.

        devrc#1955 overrules it. That guarantee is a cost paid in the wrong
        direction, because the channel's entire value is to an audit that does
        not know to ask for it: with the flag off, an audit asking "did
        everything he chose actually ship?" reads a partial decision channel
        while presenting a complete-looking one. It had already produced one
        confident wrong all-clear. So the compatibility guarantee moved to the
        FLAG — `--include-answers` is still accepted and is now inert — and the
        channel itself is unconditional.

        Still calls `records_of` with NO keyword, for the same reason the
        predecessor did: round 1's sweep showed that a test passing the flag
        explicitly cannot see the default move.
        """
        eum = _extractor()
        proj = tmp_path / "-home-zach-workspace-devrc"
        proj.mkdir(exist_ok=True)
        p = proj / f"{SID_A}.jsonl"
        p.write_text("\n".join(json.dumps(r) for r in [
            {"type": "assistant", "message": {"role": "assistant", "content": [
                {"type": "tool_use", "id": "tu_1", "name": "AskUserQuestion",
                 "input": {}}]}},
            self._user([{"type": "tool_result", "tool_use_id": "tu_1",
                         "content": "my answer"}]),
        ]) + "\n")
        assert [(r["kind"], r["text"]) for r in eum.records_of(p)] == [
            ("decision", "my answer")], (
            "records_of does NOT emit decisions by default — devrc#1955's "
            "closing condition runs the tool with no flag at all"
        )
        assert eum.build_parser().parse_args([]).include_answers is False, (
            "the retired flag's argparse default moved; it must stay False so "
            "nothing branches on it"
        )


# --------------------------------------------------------------------------
# 🔴 ROUND 2's FINDINGS
# --------------------------------------------------------------------------

def test_the_dedup_note_is_NOT_read_as_a_coverage_gap():
    """🔴 A FALSE UNKNOWN ON A COMPLETE READ. The extractor emits, with the same
    `!`, "N session(s) are ABSENT … because dedup suppressed every one of their
    messages as a repeat of another session's — they are not empty". Those
    messages WERE read and ARE in the output. Dedup is on by default and
    `audit-dispatch` does not disable it, so this fires on any PR whose trailers
    name two sessions of one arc — and a permanent UNKNOWN teaches the reader to
    skip the line that matters."""
    err = ("! 1 session(s) are ABSENT from this report because dedup suppressed "
           "every one of their messages as a repeat of another session's — they "
           "are not empty: bbbbbbbb-5555-4666-8777-888888888888\n"
           "sessions=2 msgs=4 deduped=1 out=-\n")
    assert oa.coverage_notes(err) == [], oa.coverage_notes(err)


def test_an_UNRECOGNISED_note_IS_still_read_as_a_gap():
    """The fail-safe direction: over-report rather than hide one. If the extractor
    adds a note family this does not know, it must count as a gap."""
    notes = oa.coverage_notes("! some future note nobody here anticipated\n")
    assert len(notes) == 1, notes


def test_the_dedup_note_wording_is_pinned_to_the_extractor():
    """🔴 TWO-WAY, because this is the module's one WORDING dependency — the
    prefix is otherwise the contract. If the extractor rewords the dedup note,
    this fails LOUDLY rather than silently reverting to a false UNKNOWN."""
    # 🔴 NON-EMPTY FIRST. `for marker in ()` executes ZERO assertions, so
    # emptying the tuple made this "TWO-WAY" pin pass vacuously while the hazard
    # it names went uncovered — the behaviour was caught only by its sibling.
    # Round 3 measured both directions and found this one open.
    assert oa.NOT_A_GAP_MARKERS, (
        "NOT_A_GAP_MARKERS is empty, so every `!` note counts as a gap again and "
        "this pin asserts nothing"
    )
    src = (SCRIPTS / "session-analysis" / "extract_user_msgs.py").read_text()
    for marker in oa.NOT_A_GAP_MARKERS:
        assert marker in src, (
            f"{marker!r} is no longer in the extractor's source — either it "
            "reworded the dedup note (update NOT_A_GAP_MARKERS) or the note is "
            "gone (delete the marker). Leaving it stale makes a real gap "
            "invisible."
        )


def test_the_captured_text_warning_covers_every_surface_not_just_files():
    """🔴 The earlier wording NAMED PR comments as a destination and then scoped
    the rule to files — permitting exactly the exposure its own heading forbids.
    'Wider on one axis, narrower on another'."""
    out = oa.render([oa.Ask(source=oa.SOURCE_SESSION, text="x", session_id=SID_A)],
                    session_ids=(SID_A,), projects_root="/nonexistent")
    assert "ANY SURFACE THAT LEAVES THIS MACHINE" in out
    assert "PR comment or review" in out
    assert "when a finding goes into a file" not in out, (
        "the file-only scoping came back"
    )


def test_a_non_operator_reason_does_not_assert_an_identity_it_cannot_know():
    """`viewerDidAuthor` is about whoever `gh` is authenticated as. Under a CI or
    shared token the operator's OWN comment returns false, and a reason reading
    'not the operator' would be flatly untrue."""
    ok, why = oa.is_the_operator(
        {"viewerDidAuthor": False, "author": {"login": "ZacxDev"}})
    assert not ok
    assert "authenticated as" in why
    assert why != "written by ZacxDev, not the operator"
