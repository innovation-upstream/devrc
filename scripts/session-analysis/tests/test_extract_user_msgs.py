"""Guards for `scripts/session-analysis/extract_user_msgs.py` — scoped extraction.

🔴 NO TEST READS A REAL TRANSCRIPT. `~/.claude/projects/**` holds the operator's
own prompts and a client's infrastructure, and devrc is PUBLIC. Every transcript
here is synthesised under `tmp_path` from `LEAKCANARY-*` strings, chosen to be
unmistakable if one ever escaped into a fixture.

🔴 THE LOAD-BEARING TESTS IN THIS MODULE ARE THOSE IN
`TestAZeroIsNeverJustAZero`. They are not unit tests of an `if` — they are the
control proving the tool can tell the "nothing came back" facts apart. A wrong
`--arc` name, a measured-empty arc, ids that resolve to no transcript, no
transcript openable at all, and transcripts holding no typed message all
produce the same empty output; before this CLI they all produced the same exit
0 as well. (Exit 5 covers two of those, and its stderr line says which.) Each test
asserts the code AND that the reason names the right mechanism, because a
correct code with a misleading sentence sends the reader to the wrong fix.
"""
from __future__ import annotations

import importlib.util
import io
import json
import os
import sys
from pathlib import Path

import pytest

SA_DIR = Path(__file__).resolve().parent.parent
REPO = SA_DIR.parent.parent

_spec = importlib.util.spec_from_file_location(
    "extract_user_msgs", SA_DIR / "extract_user_msgs.py")
X = importlib.util.module_from_spec(_spec)
sys.modules["extract_user_msgs"] = X
_spec.loader.exec_module(X)

REFERENCE = REPO / "claude" / "skills" / "handoff" / "reference" / "user-messages.md"
RESUME_SKILL = REPO / "claude" / "skills" / "resume" / "SKILL.md"

# 🔴 Leak canaries. Pairwise distinct, and none a substring of any other.
LEAK_TYPED = "LEAKCANARY-operator-typed-prose"
LEAK_OTHER = "LEAKCANARY-a-second-typed-message"
LEAK_REMINDER = "LEAKCANARY-injected-system-reminder"
LEAK_TOOL = "LEAKCANARY-tool-result-body"
#: The decision channel's canaries. 🔴 PAIRWISE DISTINCT AND DISTINCT FROM EVERY
#: CONSTANT THE ASSERTIONS NAME (`X.UNANSWERED_PREFIX`,
#: `X.NO_ANSWER_TEXT_PREFIX`, the `kind` strings) — a fixture that can only
#: produce the expected constant's own value cannot see a mutant that hardcodes
#: the literal, and would survive a fully green suite.
LEAK_ASKED = "LEAKCANARY-the-question-that-was-answered"
LEAK_CHOSEN = "LEAKCANARY-the-option-he-picked"
LEAK_DECLINED = "LEAKCANARY-the-option-he-refused"
LEAK_ABANDONED = "LEAKCANARY-the-question-nobody-ever-answered"
LEAK_ABANDONED_OPT = "LEAKCANARY-an-option-on-the-abandoned-prompt"


# --- fixture builders ----------------------------------------------------------

def _user(text, ts="2026-09-01T00:00:00.000Z", **extra):
    rec = {"type": "user", "timestamp": ts,
           "message": {"role": "user", "content": [{"type": "text", "text": text}]}}
    rec.update(extra)
    return rec


def _write_session(root, project, session_id, records):
    """Write one transcript at `<root>/<project>/<session_id>.jsonl`."""
    d = Path(root) / project
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{session_id}.jsonl"
    p.write_text("".join(json.dumps(r) + "\n" for r in records))
    return p


def _run(argv, root, stdin=None):
    """Run `main(argv)` against a fixture corpus; return `(rc, stdout, stderr)`."""
    out, err = io.StringIO(), io.StringIO()
    old = sys.stdout, sys.stderr, sys.stdin
    sys.stdout, sys.stderr = out, err
    if stdin is not None:
        sys.stdin = io.StringIO(stdin)
    try:
        rc = X.main(list(argv) + ["--root", str(root)])
    finally:
        sys.stdout, sys.stderr, sys.stdin = old
    return rc, out.getvalue(), err.getvalue()


# --- the filtering predicate ---------------------------------------------------

class TestMessageOf:
    """`message_of` decides what counts as typed. It had NO test while inline."""

    def test_plain_prose_is_typed(self):
        assert X.message_of(LEAK_TYPED) == ("typed", LEAK_TYPED)

    def test_a_system_reminder_is_stripped_and_the_prose_survives(self):
        raw = f"<system-reminder>{LEAK_REMINDER}</system-reminder>\n{LEAK_TYPED}"
        kind, text = X.message_of(raw)
        assert kind == "typed"
        assert text == LEAK_TYPED
        assert LEAK_REMINDER not in text, (
            "the harness's injected reminder is not something the operator typed")

    def test_a_record_that_is_ONLY_a_system_reminder_is_dropped(self):
        assert X.message_of(
            f"<system-reminder>{LEAK_REMINDER}</system-reminder>") is None

    def test_a_slash_command_becomes_kind_command_with_its_args(self):
        raw = ("<command-name>resume</command-name>"
               "<command-args>the widget arc</command-args>")
        assert X.message_of(raw) == ("command", "resume the widget arc")

    def test_a_slash_command_with_no_args_keeps_its_name(self):
        assert X.message_of("<command-name>handoff</command-name>") == (
            "command", "handoff")

    @pytest.mark.parametrize("prefix", X.BOILERPLATE_PREFIXES)
    def test_every_boilerplate_prefix_is_dropped(self, prefix):
        """Two-way with the module's own tuple: a prefix added there is covered
        automatically, and one deleted there stops being asserted here."""
        assert X.message_of(prefix + " trailing detail") is None

    def test_boilerplate_detection_is_a_PREFIX_not_a_substring(self):
        """🔴 POSITIVE CONTROL for the test above. If `message_of` dropped on a
        substring match, a real message merely MENTIONING the boilerplate would
        vanish — and the parametrised test above would stay green either way."""
        raw = f"{LEAK_TYPED} — it said {X.BOILERPLATE_PREFIXES[0]} at the top"
        assert X.message_of(raw) == ("typed", raw)

    def test_a_short_bare_tag_is_dropped_but_a_long_one_is_kept(self):
        assert X.message_of("<some-leftover-tag>") is None
        long_tag = "<t>" + "x" * 90 + "</t>"
        assert X.message_of(long_tag) == ("typed", long_tag)

    def test_empty_and_whitespace_are_dropped(self):
        assert X.message_of("") is None
        assert X.message_of("   \n  ") is None


class TestExtractFromContent:
    def test_a_bare_string_content_is_one_message(self):
        assert X.extract_from_content(LEAK_TYPED) == [LEAK_TYPED]

    def test_only_text_blocks_are_read(self):
        content = [{"type": "text", "text": LEAK_TYPED},
                   {"type": "tool_result", "content": LEAK_TOOL},
                   {"type": "thinking", "thinking": LEAK_TOOL},
                   "not-a-dict"]
        got = X.extract_from_content(content)
        assert got == [LEAK_TYPED]
        assert LEAK_TOOL not in "".join(got)


class TestRecordsOf:
    def test_it_carries_session_project_and_timestamp(self, tmp_path):
        p = _write_session(tmp_path, "proj-a", "sess-1",
                           [_user(LEAK_TYPED, ts="2026-09-02T03:04:05.000Z")])
        (rec,) = list(X.records_of(p))
        assert rec == {"session_id": "sess-1", "project": "proj-a",
                       "ts": "2026-09-02T03:04:05.000Z", "kind": "typed",
                       "text": LEAK_TYPED}

    def test_meta_sidechain_and_assistant_records_are_skipped(self, tmp_path):
        p = _write_session(tmp_path, "proj-a", "sess-1", [
            _user(LEAK_TYPED),
            _user("LEAKCANARY-meta", isMeta=True),
            _user("LEAKCANARY-sidechain", isSidechain=True),
            {"type": "assistant", "message": {"role": "assistant",
                                              "content": [{"type": "text",
                                                           "text": LEAK_TOOL}]}},
        ])
        texts = [r["text"] for r in X.records_of(p)]
        assert texts == [LEAK_TYPED], (
            "a subagent's own transcript and the harness's meta records are not "
            "things the operator typed")

    def test_a_malformed_line_skips_that_LINE_not_the_file(self, tmp_path):
        p = _write_session(tmp_path, "proj-a", "sess-1", [_user(LEAK_TYPED)])
        p.write_text(p.read_text() + "{not json\n" +
                     json.dumps(_user(LEAK_OTHER)) + "\n")
        assert [r["text"] for r in X.records_of(p)] == [LEAK_TYPED, LEAK_OTHER]


# --- the DECISION channel (devrc#1955) -----------------------------------------

def _ask(tool_use_id, question, options, ts="2026-09-01T00:01:00.000Z",
         name="AskUserQuestion", **extra):
    """An ASSISTANT record issuing one `AskUserQuestion`.

    🔴 SHAPED FROM THE REAL SCHEMA, SYNTHESISED FROM CANARIES. Measured over
    this host's corpus 2026-10-03: the input is
    `{"questions": [{"question", "header", "multiSelect", "options": [{"label",
    "description"}]}]}` on 1,551 of 1,554 calls. `name` is a parameter so the Bash
    discriminator test builds the SAME block with a different tool.
    """
    rec = {"type": "assistant", "timestamp": ts,
           "message": {"role": "assistant", "content": [
               {"type": "tool_use", "id": tool_use_id, "name": name,
                "input": {"questions": [{
                    "question": question,
                    "header": "Pick",
                    "multiSelect": False,
                    "options": [{"label": o, "description": f"what {o} means"}
                                for o in options]}]}}]}}
    rec.update(extra)
    return rec


def _answer(tool_use_id, content, ts="2026-09-01T00:02:00.000Z", **extra):
    """A USER record carrying one `tool_result`.

    `content` is passed through verbatim so a test can hand it the string shape
    (1,550 of 1,550 answered calls measured here) or the list-of-typed-blocks shape (0 measured
    here, and the shape every other tool's result uses — so it is parsed rather
    than assumed absent).
    """
    rec = {"type": "user", "timestamp": ts,
           "message": {"role": "user", "content": [
               {"type": "tool_result", "tool_use_id": tool_use_id,
                "content": content}]}}
    rec.update(extra)
    return rec


#: The harness's real framing around an answer, with the canaries substituted
#: for the operator's own words. This is the string an audit greps.
def _answered_body(question, chosen):
    return f'Your questions have been answered: "{question}"="{chosen}"'


class TestTheDecisionChannel:
    """🔴 devrc#1955. `AskUserQuestion` answers are OPERATOR DECISIONS and they
    arrive in a `tool_result` block — the one block shape the typed walk
    ignores. Dropping them made every fork-resolution invisible to an arc audit
    while the output looked complete, and that produced a confident wrong
    all-clear on a real arc.

    These are not unit tests of an `if`. The load-bearing one is
    `test_an_UNANSWERED_question_is_emitted_as_its_OWN_kind`: the predecessor
    built a SET of `AskUserQuestion` ids and only ever emitted on a MATCH, so a
    question the operator closed without answering left no trace at all — the
    new channel inheriting the old one's silent-omission property. 4 of 1,554
    calls on this host, in 4 different sessions, all invisible.
    """

    def _rows(self, tmp_path, records):
        p = _write_session(tmp_path, "proj-d", "sess-d", records)
        return list(X.records_of(p))

    # --- the positive control, first ------------------------------------------
    def test_an_ANSWERED_question_becomes_a_decision_row(self, tmp_path):
        """🔴 THE POSITIVE CONTROL FOR THE WHOLE CHANNEL. A zero from any test
        below is indistinguishable from a fixture wired to nothing until this
        one has been watched to produce a NON-ZERO count."""
        rows = self._rows(tmp_path, [
            _ask("tu_answered", LEAK_ASKED, [LEAK_CHOSEN, LEAK_DECLINED]),
            _answer("tu_answered", _answered_body(LEAK_ASKED, LEAK_CHOSEN)),
        ])
        assert [(r["kind"], r["text"]) for r in rows] == [
            ("decision", _answered_body(LEAK_ASKED, LEAK_CHOSEN))], rows

    def test_the_decision_row_carries_the_question_AND_the_chosen_option(
            self, tmp_path):
        """Both halves, because an audit needs the fork AND how it was resolved.
        The DECLINED label must be absent — the harness emits only the pick, and
        a row that listed every option would read as if he chose them all."""
        (row,) = self._rows(tmp_path, [
            _ask("tu_both", LEAK_ASKED, [LEAK_CHOSEN, LEAK_DECLINED]),
            _answer("tu_both", _answered_body(LEAK_ASKED, LEAK_CHOSEN)),
        ])
        assert LEAK_ASKED in row["text"]
        assert LEAK_CHOSEN in row["text"]
        assert LEAK_DECLINED not in row["text"], (
            "an option he did NOT pick is in the decision text — a reader "
            f"cannot tell what he chose: {row['text']!r}")

    # --- THE REGRESSION TEST --------------------------------------------------
    def test_an_UNANSWERED_question_is_emitted_as_its_OWN_kind(self, tmp_path):
        """🔴 THE #1955 REGRESSION TEST — RED BEFORE THE FIX.

        An `AskUserQuestion` `tool_use` with NO matching `tool_result` is a real
        state: the operator aborted, interrupted, or the session ended on the
        prompt. The predecessor emitted NOTHING for it, so the decision channel
        it had just gained could not report its own empty case.
        """
        rows = self._rows(tmp_path, [
            _ask("tu_abandoned", LEAK_ABANDONED, [LEAK_ABANDONED_OPT]),
        ])
        kinds = [r["kind"] for r in rows]
        assert kinds == ["decision_unanswered"], (
            "an AskUserQuestion that was never answered produced "
            f"{kinds!r} — the aborted/unanswered state is invisible, which is "
            "the silent omission devrc#1955 is about")

    def test_the_unanswered_row_NAMES_the_question_and_the_options_offered(
            self, tmp_path):
        """A bare marker would say a decision was missed without saying WHICH.
        The options matter too: they are what the operator walked away from."""
        (row,) = self._rows(tmp_path, [
            _ask("tu_named", LEAK_ABANDONED, [LEAK_ABANDONED_OPT]),
        ])
        assert row["text"].startswith(X.UNANSWERED_PREFIX), row["text"]
        assert LEAK_ABANDONED in row["text"]
        assert LEAK_ABANDONED_OPT in row["text"], (
            "the options offered are absent — a reader cannot see what the "
            f"abandoned fork was between: {row['text']!r}")

    def test_the_unanswered_row_is_timestamped_when_the_question_was_ASKED(
            self, tmp_path):
        """The only timestamp this state has. An empty `ts` sorts LAST in the
        CLI, which would put a missed decision at the end of the chain rather
        than where the fork actually happened."""
        (row,) = self._rows(tmp_path, [
            _ask("tu_ts", LEAK_ABANDONED, [LEAK_ABANDONED_OPT],
                 ts="2026-09-04T05:06:07.000Z"),
        ])
        assert row["ts"] == "2026-09-04T05:06:07.000Z", row

    def test_an_ANSWERED_question_is_NOT_also_reported_unanswered(
            self, tmp_path):
        """The other direction of the EOF sweep: an id that matched must be
        popped, or every decision is double-counted and every arc grows a
        phantom missed fork."""
        rows = self._rows(tmp_path, [
            _ask("tu_once", LEAK_ASKED, [LEAK_CHOSEN]),
            _answer("tu_once", _answered_body(LEAK_ASKED, LEAK_CHOSEN)),
        ])
        assert [r["kind"] for r in rows] == ["decision"], rows

    # --- the structural discriminator -----------------------------------------
    def test_a_BASH_tool_result_is_NEVER_a_decision(self, tmp_path):
        """🔴 THE DISCRIMINATOR IS THE `tool_use_id`, NEVER THE TEXT. Every
        Bash/Read/Grep result arrives in the same block shape and none is the
        operator. The fixture is the SAME `_ask` block with a different `name`,
        so the only difference under test is the structural one."""
        rows = self._rows(tmp_path, [
            _ask("tu_bash", LEAK_ASKED, [LEAK_CHOSEN], name="Bash"),
            _answer("tu_bash", LEAK_TOOL),
        ])
        assert rows == [], (
            f"a Bash result was emitted as an operator decision: {rows}")

    def test_an_orphan_tool_result_naming_an_UNKNOWN_id_is_not_a_decision(
            self, tmp_path):
        """A truncated or resumed transcript can carry a result whose `tool_use`
        is in an earlier file. Unknown id, so it is not provably the operator —
        and guessing would admit every Bash result in a truncated session."""
        rows = self._rows(tmp_path, [_answer("tu_not_here", LEAK_TOOL)])
        assert rows == [], rows

    def test_a_SIDECHAIN_AskUserQuestion_is_not_a_decision(self, tmp_path):
        """A sidechain record is a subagent's own transcript. A question a
        subagent asked ITSELF is not a fork the operator resolved — and it must
        not land in the unanswered channel either, which is the case an
        `isSidechain` check placed only on the `user` branch would miss."""
        rows = self._rows(tmp_path, [
            _ask("tu_sub", LEAK_ABANDONED, [LEAK_ABANDONED_OPT],
                 isSidechain=True),
            _answer("tu_sub", _answered_body(LEAK_ASKED, LEAK_CHOSEN),
                    isSidechain=True),
        ])
        assert rows == [], f"a subagent's own question became a decision: {rows}"

    # --- schema variance ------------------------------------------------------
    def test_a_tool_result_whose_content_is_a_LIST_of_blocks_is_still_read(
            self, tmp_path):
        """🔴 THE TRANSCRIPT SCHEMA VARIES ACROSS CLAUDE CODE VERSIONS, so the
        block is parsed structurally rather than by a fixed shape. `content` is
        a plain string on all 1,550 answered calls measured here; the
        list-of-typed-blocks form is what every other tool's result uses, so it
        is handled rather than assumed absent."""
        body = _answered_body(LEAK_ASKED, LEAK_CHOSEN)
        (row,) = self._rows(tmp_path, [
            _ask("tu_list", LEAK_ASKED, [LEAK_CHOSEN]),
            _answer("tu_list", [{"type": "text", "text": body}]),
        ])
        assert (row["kind"], row["text"]) == ("decision", body), row

    def test_a_tool_input_the_harness_could_not_PARSE_still_produces_a_row(
            self, tmp_path):
        """`__unparsedToolInput` instead of `questions` — 3 of 1,554 measured
        on this host (the other 1,551 carry a readable `questions` list). The
        question is unreadable, so the row falls back to the `tool_use` id. 🔴 An unreadable question is a worse record than a
        readable one and a FAR better one than no record: a shape this tool
        cannot parse must not become a silently missing decision."""
        rows = self._rows(tmp_path, [
            {"type": "assistant", "timestamp": "2026-09-01T00:01:00.000Z",
             "message": {"role": "assistant", "content": [
                 {"type": "tool_use", "id": "tu_unparsed",
                  "name": "AskUserQuestion",
                  "input": {"__unparsedToolInput": "{\"questions\": [{\"que"}}]}},
        ])
        assert [r["kind"] for r in rows] == ["decision_unanswered"], rows
        assert "tu_unparsed" in rows[0]["text"], (
            "an unparsable question produced a row that names neither the "
            f"question nor the call: {rows[0]['text']!r}")

    def test_a_NON_STRING_tool_use_id_does_not_CRASH_the_whole_extraction(
            self, tmp_path):
        """🔴 A TRACEBACK HERE DISCARDS EVERY ROW, NOT JUST THE BAD BLOCK. The
        id becomes a dict KEY, so a list or dict raises `TypeError: unhashable
        type`, and `main` consumes this generator inside a bare
        `except OSError` — the TypeError escapes as rc 1 with no output, from a
        tool whose whole contract is that a zero means something specific.

        MEASURED: 1,554 of 1,554 real ids are strings, so this is a DEFENSIVE
        guard and is labelled as one. What makes it worth a test rather than a
        comment is the second assertion: the GOOD rows in the same transcript
        must survive. A guard that threw the file away quietly would satisfy
        'it did not crash' while being just as destructive.
        """
        rows = self._rows(tmp_path, [
            _user(LEAK_TYPED, ts="2026-09-01T00:00:00.000Z"),
            {"type": "assistant", "timestamp": "2026-09-01T00:01:00.000Z",
             "message": {"role": "assistant", "content": [
                 {"type": "tool_use", "id": ["not", "a", "string"],
                  "name": "AskUserQuestion", "input": {}}]}},
            _ask("tu_fine", LEAK_ASKED, [LEAK_CHOSEN],
                 ts="2026-09-01T00:02:00.000Z"),
            _answer("tu_fine", _answered_body(LEAK_ASKED, LEAK_CHOSEN),
                    ts="2026-09-01T00:03:00.000Z"),
            {"type": "user", "timestamp": "2026-09-01T00:04:00.000Z",
             "message": {"role": "user", "content": [
                 {"type": "tool_result", "tool_use_id": {"also": "not a str"},
                  "content": LEAK_TOOL}]}},
        ])
        assert [r["kind"] for r in rows] == ["typed", "decision"], (
            "a malformed id either crashed the extraction or took the good "
            f"rows with it: {rows}")

    def test_a_MATCHED_result_carrying_no_text_is_still_a_decision_row(
            self, tmp_path):
        """🔴 THE PREDECESSOR'S `if text:` WAS A SILENT DROP. 0 of the 1,550
        answered calls on this host — kept because a guard that only covers the shapes we happened to
        measure is how the next shape goes missing, and this is a channel whose
        entire purpose is to stop silent omission."""
        (row,) = self._rows(tmp_path, [
            _ask("tu_empty", LEAK_ASKED, [LEAK_CHOSEN]),
            _answer("tu_empty", ""),
        ])
        assert row["kind"] == "decision", row
        assert row["text"].startswith(X.NO_ANSWER_TEXT_PREFIX), row["text"]
        assert LEAK_ASKED in row["text"], (
            "a decision with no answer text names neither the question nor "
            f"the call: {row['text']!r}")

    # --- the pair: both channels, in one transcript ---------------------------
    def test_the_TYPED_channel_is_unchanged_beside_a_decision(self, tmp_path):
        """🔴 THE PAIR, REPORTED TOGETHER. Two typed rows and one decision from
        one transcript: the typed count pins that the new channel did not
        displace the old one, and the decision count is the non-zero that makes
        a zero elsewhere readable as a real zero rather than a dead fixture."""
        rows = self._rows(tmp_path, [
            _user(LEAK_TYPED, ts="2026-09-01T00:00:00.000Z"),
            _ask("tu_pair", LEAK_ASKED, [LEAK_CHOSEN]),
            _answer("tu_pair", _answered_body(LEAK_ASKED, LEAK_CHOSEN)),
            _user(LEAK_OTHER, ts="2026-09-01T00:03:00.000Z"),
        ])
        counts = {}
        for r in rows:
            counts[r["kind"]] = counts.get(r["kind"], 0) + 1
        assert counts == {"typed": 2, "decision": 1}, counts

    # --- no flag, and the retired flag is inert -------------------------------
    def test_the_channel_needs_NO_FLAG(self, tmp_path):
        """🔴 THE CONTRACT #1955 REVERSES. The channel was opt-in behind
        `--include-answers`, and the previous suite pinned that default
        explicitly. The issue overrules it: the channel's whole value is to an
        audit that does not know to ask for it, and its closing condition runs
        the tool with no flag at all. `records_of` is called with NO keyword, so
        the DEFAULT is what is under test."""
        p = _write_session(tmp_path, "proj-d", "sess-d", [
            _ask("tu_default", LEAK_ASKED, [LEAK_CHOSEN]),
            _answer("tu_default", _answered_body(LEAK_ASKED, LEAK_CHOSEN)),
        ])
        assert [r["kind"] for r in X.records_of(p)] == ["decision"], (
            "the decision channel is not in the default output — an audit "
            "that does not know to pass a flag reads a partial decision "
            "channel, which is the defect")

    def test_the_retired_flag_is_a_NO_OP_not_a_second_mode(self, tmp_path):
        """`--include-answers` is still ACCEPTED (audit-dispatch passes it, and
        `/audit-pr` prints it in a command a human copies) and must do NOTHING.
        A flag that silently did something different would be worse than one
        that was removed, so the two outputs are compared BYTE FOR BYTE."""
        _write_session(tmp_path, "proj-d", "sess-d", [
            _user(LEAK_TYPED),
            _ask("tu_flag", LEAK_ASKED, [LEAK_CHOSEN]),
            _answer("tu_flag", _answered_body(LEAK_ASKED, LEAK_CHOSEN)),
            _ask("tu_flag_abandoned", LEAK_ABANDONED, [LEAK_ABANDONED_OPT]),
        ])
        rc_off, off, _ = _run(["--session", "sess-d", "--jsonl"], tmp_path)
        rc_on, on, _ = _run(
            ["--session", "sess-d", "--jsonl", "--include-answers"], tmp_path)
        assert (rc_off, rc_on) == (0, 0), (rc_off, rc_on)
        assert off == on, "--include-answers changed the output"
        kinds = [json.loads(l)["kind"] for l in off.splitlines()]
        assert sorted(kinds) == ["decision", "decision_unanswered", "typed"], (
            f"the positive control is wired to nothing: {kinds}")

    # --- end to end, through the CLI -----------------------------------------
    def test_both_decision_kinds_reach_the_JSONL_through_main(self, tmp_path):
        """The seam: `records_of` is not the surface an audit reads — the CLI
        is. A row class that exists in the generator and is filtered, deduped or
        dropped on the way to `--jsonl` is invisible exactly where it matters."""
        _write_session(tmp_path, "proj-d", "sess-d", [
            _ask("tu_e2e_ok", LEAK_ASKED, [LEAK_CHOSEN]),
            _answer("tu_e2e_ok", _answered_body(LEAK_ASKED, LEAK_CHOSEN)),
            _ask("tu_e2e_gone", LEAK_ABANDONED, [LEAK_ABANDONED_OPT],
                 ts="2026-09-01T00:05:00.000Z"),
        ])
        rc, out, _ = _run(["--session", "sess-d", "--jsonl"], tmp_path)
        assert rc == 0, out
        rows = [json.loads(l) for l in out.splitlines()]
        by_kind = {r["kind"]: r["text"] for r in rows}
        assert set(by_kind) == {"decision", "decision_unanswered"}, by_kind
        assert LEAK_CHOSEN in by_kind["decision"]
        assert LEAK_ABANDONED in by_kind["decision_unanswered"]

    def test_a_decision_reaches_the_MARKDOWN_heading_as_its_kind(self, tmp_path):
        """Markdown is the DEFAULT format and the one a human or an agent
        reads. The kind is in the per-message heading, so a decision that
        rendered as `typed` would be quoted as something he asked for rather
        than something he chose."""
        _write_session(tmp_path, "proj-d", "sess-d", [
            _ask("tu_md", LEAK_ASKED, [LEAK_CHOSEN]),
            _answer("tu_md", _answered_body(LEAK_ASKED, LEAK_CHOSEN)),
        ])
        rc, out, _ = _run(["--session", "sess-d"], tmp_path)
        assert rc == 0, out
        assert "· decision" in out, out

    # --- the kind ledger ------------------------------------------------------
    def test_message_of_only_ever_returns_a_DECLARED_kind(self):
        """Two-way against `KINDS`. A kind produced by the filtering predicate
        and absent from the ledger is a row class nothing documents."""
        cases = ["plain prose the operator wrote",
                 "<command-name>resume</command-name>"]
        got = [X.message_of(c)[0] for c in cases]
        assert got == ["typed", "command"], got
        for kind in got:
            assert kind in X.KINDS, f"{kind!r} is not in KINDS"

    def test_KINDS_holds_every_kind_the_module_declares(self):
        """The other direction — a constant added with no ledger entry."""
        declared = {X.KIND_DECISION, X.KIND_DECISION_UNANSWERED}
        assert declared <= set(X.KINDS), declared - set(X.KINDS)
        assert len(X.KINDS) == len(set(X.KINDS)), X.KINDS

    def test_the_two_decision_kinds_are_DIFFERENT_strings(self):
        """🔴 Collapsing them is the mutant a behavioural test cannot see: both
        channels would still be emitted, and `operator_asks` would start
        quoting an agent's unanswered question back as an operator requirement.
        This guard owns 'these are two different facts'."""
        assert X.KIND_DECISION != X.KIND_DECISION_UNANSWERED


# --- selectors -----------------------------------------------------------------

class TestReadIdsFile:
    def test_comments_and_blanks_are_skipped(self, tmp_path):
        f = tmp_path / "ids.txt"
        f.write_text("# a comment\n\n  id-a  \nid-b\n\n# another\n")
        assert X.read_ids_file(str(f)) == ["id-a", "id-b"]

    def test_dash_reads_stdin(self, monkeypatch):
        monkeypatch.setattr(sys, "stdin", io.StringIO("id-a\nid-b\n"))
        assert X.read_ids_file("-") == ["id-a", "id-b"]

    def test_an_unreadable_file_RAISES_rather_than_returning_empty(self, tmp_path):
        """🔴 The whole point. An OSError degraded into `[]` would make an
        unreadable ids file indistinguishable from an empty selection."""
        with pytest.raises(OSError):
            X.read_ids_file(str(tmp_path / "nope.txt"))


class TestResolveSessions:
    def test_an_unresolvable_id_is_RETURNED_not_dropped(self, tmp_path):
        """🔴 The load-bearing half. Silently narrowing the set to whatever
        happened to resolve is how a partial extraction reads as a whole one."""
        _write_session(tmp_path, "proj-a", "sess-real", [_user(LEAK_TYPED)])
        resolved, missing = X.resolve_sessions(
            ["sess-real", "sess-ghost"], root=tmp_path)
        assert [sid for sid, _ in resolved] == ["sess-real"]
        assert missing == ["sess-ghost"]

    def test_duplicate_ids_resolve_once(self, tmp_path):
        _write_session(tmp_path, "proj-a", "sess-real", [_user(LEAK_TYPED)])
        resolved, missing = X.resolve_sessions(
            ["sess-real", "sess-real"], root=tmp_path)
        assert len(resolved) == 1 and missing == []

    def test_a_subagent_transcript_is_NOT_a_session(self, tmp_path):
        """`find_transcript` applies `is_corpus_member`; a subagent is not a
        session anybody typed into, so its id must not resolve."""
        _write_session(tmp_path, "proj-a/sess-1/subagents", "agent-7",
                       [_user(LEAK_TYPED)])
        resolved, missing = X.resolve_sessions(["agent-7"], root=tmp_path)
        assert resolved == [] and missing == ["agent-7"]


# --- the four zeros ------------------------------------------------------------

class _FakeMember:
    def __init__(self, session_id, role):
        self.session_id, self.role = session_id, role


class _FakeReport:
    def __init__(self, members, doc="handoff-fake.md", repo="devrc"):
        self.members, self.doc, self.repo = members, doc, repo
        self.unmeasured_notes = ["LEAKCANARY-note-is-surfaced"]


def _fake_find_session(*, basename="handoff-fake.md", members=(), raises=None):
    """A stand-in for `find-session.py` — the ONLY thing `arc_sessions` needs."""
    class _Unmeasured(RuntimeError):
        pass

    class _HandoffArc:
        @staticmethod
        def coverage_line(report):
            return "2 of 5 commit(s) on this doc carry no session id"

    class _Mod:
        ArcUnmeasured = _Unmeasured
        handoff_arc = _HandoffArc
        ROOT = None
        calls = []

        @staticmethod
        def arc_seed_to_doc(seed, root=None):
            return basename

        @staticmethod
        def arc_report(name):
            _Mod.calls.append(name)
            if raises:
                raise _Unmeasured(raises)
            return _FakeReport(list(members))
    return _Mod


class TestAZeroIsNeverJustAZero:
    """🔴 Four ways to come back empty, four codes, four reasons.

    Each assertion pins the code AND the mechanism the sentence names. A right
    code under a sentence pointing at the wrong mechanism sends the reader to
    the wrong fix, which is the failure this whole design exists to prevent.
    """

    def test_3_a_seed_naming_no_doc_is_UNMEASURED(self, tmp_path, monkeypatch):
        fake = _fake_find_session(basename="")
        monkeypatch.setattr(X, "_load_find_session", lambda: fake)
        rc, _, err = _run(["--arc", "not-a-doc"], tmp_path)
        assert rc == X.EXIT_ARC_UNMEASURED
        assert "names no handoff doc" in err
        assert "Nothing was measured" in err

    def test_3_no_checkout_holding_the_doc_is_UNMEASURED(self, tmp_path,
                                                        monkeypatch):
        fake = _fake_find_session(raises="no repo handle holds it")
        monkeypatch.setattr(X, "_load_find_session", lambda: fake)
        rc, _, err = _run(["--arc", "handoff-fake"], tmp_path)
        assert rc == X.EXIT_ARC_UNMEASURED
        assert "no repo handle holds it" in err

    def test_4_a_doc_that_resolves_with_no_members_is_a_MEASURED_empty_arc(
            self, tmp_path, monkeypatch):
        fake = _fake_find_session(members=())
        monkeypatch.setattr(X, "_load_find_session", lambda: fake)
        rc, _, err = _run(["--arc", "handoff-fake"], tmp_path)
        assert rc == X.EXIT_ARC_EMPTY
        assert "MEASURED empty arc" in err
        assert "not a typo in your argument" in err
        assert f"exit {X.EXIT_ARC_UNMEASURED}" in err, (
            "the reason must name the OTHER code, or a reader cannot tell "
            "which of the two they are looking at")

    def test_4_and_3_are_DIFFERENT_CODES(self):
        """The pair this module exists for. A single 'empty' code would make a
        typo and a real finding indistinguishable."""
        assert X.EXIT_ARC_UNMEASURED != X.EXIT_ARC_EMPTY

    def test_5_ids_that_resolve_to_nothing_read_NOTHING(self, tmp_path):
        rc, _, err = _run(["--session", "sess-ghost"], tmp_path)
        assert rc == X.EXIT_NO_TRANSCRIPTS
        assert "sess-ghost" in err, "every unresolved id must be named"
        assert "Nothing was read" in err
        assert f"exit {X.EXIT_NO_MESSAGES}" in err

    def test_6_transcripts_that_ARE_read_and_hold_nothing_typed(self, tmp_path):
        _write_session(tmp_path, "proj-a", "sess-1", [
            _user("<system-reminder>only boilerplate</system-reminder>"),
            _user("[Request interrupted by user]"),
        ])
        rc, _, err = _run(["--session", "sess-1"], tmp_path)
        assert rc == X.EXIT_NO_MESSAGES
        assert "WERE read" in err
        assert "measured emptiness" in err

    def test_5_and_6_are_DIFFERENT_CODES(self):
        assert X.EXIT_NO_TRANSCRIPTS != X.EXIT_NO_MESSAGES

    def test_every_exit_code_in_the_contract_is_distinct(self):
        codes = [c for c, _ in X.EXIT_CONTRACT]
        assert len(codes) == len(set(codes)), codes

    def test_2_an_unreadable_ids_file_never_degrades_to_an_empty_selection(
            self, tmp_path):
        rc, _, err = _run(["--ids-file", str(tmp_path / "nope.txt")], tmp_path)
        assert rc == X.EXIT_USAGE, (
            "an OSError degraded into an empty selection would fall through to "
            f"exit {X.EXIT_NO_TRANSCRIPTS} and read as 'those sessions are gone'")
        assert "nope.txt" in err, "the reason must name the file it could not read"

    def test_2_an_ids_file_holding_no_ids(self, tmp_path):
        f = tmp_path / "ids.txt"
        f.write_text("# only a comment\n\n")
        rc, _, err = _run(["--ids-file", str(f)], tmp_path)
        assert rc == X.EXIT_USAGE
        assert "held no session ids" in err


# --- extraction end to end -----------------------------------------------------

class TestScopedExtraction:
    def test_a_selected_session_extracts_only_that_session(self, tmp_path):
        _write_session(tmp_path, "proj-a", "sess-1", [_user(LEAK_TYPED)])
        _write_session(tmp_path, "proj-a", "sess-2", [_user(LEAK_OTHER)])
        rc, out, _ = _run(["--session", "sess-1"], tmp_path)
        assert rc == X.EXIT_OK
        assert LEAK_TYPED in out
        assert LEAK_OTHER not in out, (
            "🔴 the scope is the whole feature — an unselected session must "
            "not appear")

    def test_repeating_session_selects_both(self, tmp_path):
        _write_session(tmp_path, "proj-a", "sess-1", [_user(LEAK_TYPED)])
        _write_session(tmp_path, "proj-a", "sess-2", [_user(LEAK_OTHER)])
        rc, out, _ = _run(["--session", "sess-1", "--session", "sess-2"], tmp_path)
        assert rc == X.EXIT_OK
        assert LEAK_TYPED in out and LEAK_OTHER in out

    def test_ids_file_dash_reads_stdin(self, tmp_path):
        _write_session(tmp_path, "proj-a", "sess-1", [_user(LEAK_TYPED)])
        rc, out, _ = _run(["--ids-file", "-"], tmp_path, stdin="sess-1\n")
        assert rc == X.EXIT_OK and LEAK_TYPED in out

    def test_a_partially_resolvable_selection_SAYS_SO_and_still_succeeds(
            self, tmp_path):
        """A partial extraction that does not announce itself reads as a whole
        one — so the warning must print on the SUCCESS path, not only on 5."""
        _write_session(tmp_path, "proj-a", "sess-1", [_user(LEAK_TYPED)])
        rc, out, err = _run(["--session", "sess-1", "--session", "sess-ghost"],
                            tmp_path)
        assert rc == X.EXIT_OK
        assert "sess-ghost" in err
        assert "sess-ghost" in out, (
            "the markdown header must carry the gap too — stderr is routinely "
            "discarded by a caller redirecting stdout")

    def test_jsonl_carries_every_key_on_every_row(self, tmp_path):
        _write_session(tmp_path, "proj-a", "sess-1",
                       [_user(LEAK_TYPED),
                        _user("<command-name>handoff</command-name>")])
        rc, out, _ = _run(["--session", "sess-1", "--jsonl"], tmp_path)
        assert rc == X.EXIT_OK
        rows = [json.loads(l) for l in out.splitlines() if l.strip()]
        assert len(rows) == 2
        for r in rows:
            assert set(r) == {"session_id", "project", "ts", "kind", "text",
                              "arc_role"}, sorted(r)
        assert {r["kind"] for r in rows} == {"typed", "command"}

    def test_arc_role_is_null_outside_arc_mode(self, tmp_path):
        _write_session(tmp_path, "proj-a", "sess-1", [_user(LEAK_TYPED)])
        _, out, _ = _run(["--session", "sess-1", "--jsonl"], tmp_path)
        assert json.loads(out.splitlines()[0])["arc_role"] is None

    def test_rows_are_chronological_and_undated_rows_sort_LAST(self, tmp_path):
        _write_session(tmp_path, "proj-a", "sess-1", [
            _user("second", ts="2026-09-02T00:00:00Z"),
            _user("first", ts="2026-09-01T00:00:00Z"),
            _user("undated", ts=""),
        ])
        _, out, _ = _run(["--session", "sess-1", "--jsonl"], tmp_path)
        assert [json.loads(l)["text"] for l in out.splitlines()] == [
            "first", "second", "undated"], (
            "an undated row sorting FIRST would head the chain with the one "
            "message nothing could place in it")

    def test_dedup_suppresses_a_repeat_and_REPORTS_the_count(self, tmp_path):
        _write_session(tmp_path, "proj-a", "sess-1", [_user(LEAK_TYPED)])
        _write_session(tmp_path, "proj-a", "sess-2", [_user(LEAK_TYPED)])
        rc, out, err = _run(["--session", "sess-1", "--session", "sess-2",
                             "--jsonl"], tmp_path)
        assert rc == X.EXIT_OK
        assert len(out.splitlines()) == 1
        assert "deduped=1" in err, (
            "a suppressed message that is not counted is a silent narrowing")

    def test_dedup_does_NOT_collide_a_command_with_identical_typed_prose(
            self, tmp_path):
        """🔴 REGRESSION. The first cut of the scoped dedup keyed on
        `project + text` and dropped `kind`, so `/handoff` typed as prose and
        `/handoff` the slash command hashed the same and whichever the walk
        reached second vanished. MEASURED over a frozen 974-transcript list: it
        lost 7 rows the previous implementation kept. Two different events with
        the same text are two rows."""
        _write_session(tmp_path, "proj-a", "sess-1", [
            _user("<command-name>handoff</command-name>",
                  ts="2026-09-01T00:00:00Z"),
            _user("handoff", ts="2026-09-01T00:00:01Z"),
        ])
        rc, out, _ = _run(["--session", "sess-1", "--jsonl"], tmp_path)
        assert rc == X.EXIT_OK
        rows = [json.loads(l) for l in out.splitlines()]
        assert [(r["kind"], r["text"]) for r in rows] == [
            ("command", "handoff"), ("typed", "handoff")], rows

    def test_dedup_is_scoped_PER_PROJECT_not_global(self, tmp_path):
        """🔴 The bug this whole dedup change exists to fix, asserted directly.
        The previous implementation keyed commands on TEXT ALONE, so a
        `/handoff` in repo A suppressed the `/handoff` in repo B and which one
        survived depended on unsorted glob order. The same text in two projects
        is two events.

        Found by the mutation battery: dropping `project` from the key left the
        whole suite green, so this covers the one key component nothing else
        reached."""
        _write_session(tmp_path, "proj-a", "sess-1",
                       [_user("<command-name>handoff</command-name>")])
        _write_session(tmp_path, "proj-b", "sess-2",
                       [_user("<command-name>handoff</command-name>")])
        rc, out, err = _run(["--session", "sess-1", "--session", "sess-2",
                             "--jsonl"], tmp_path)
        assert rc == X.EXIT_OK
        rows = [json.loads(l) for l in out.splitlines()]
        assert sorted(r["project"] for r in rows) == ["proj-a", "proj-b"], rows
        assert "deduped=0" in err

    def test_dedup_keeps_two_DIFFERENT_texts_in_one_project(self, tmp_path):
        """Positive control on the `text` component — a key that dropped it
        would collapse a whole project to one row per kind."""
        _write_session(tmp_path, "proj-a", "sess-1",
                       [_user(LEAK_TYPED, ts="2026-09-01T00:00:00Z"),
                        _user(LEAK_OTHER, ts="2026-09-01T00:00:01Z")])
        rc, out, err = _run(["--session", "sess-1", "--jsonl"], tmp_path)
        assert rc == X.EXIT_OK
        assert [json.loads(l)["text"] for l in out.splitlines()] == [
            LEAK_TYPED, LEAK_OTHER]
        assert "deduped=0" in err

    def test_dedup_still_collapses_a_repeat_of_the_SAME_kind(self, tmp_path):
        """Positive control for the test above — widening the key must not have
        turned dedup off, which would make that test pass for the wrong reason."""
        _write_session(tmp_path, "proj-a", "sess-1", [
            _user("<command-name>handoff</command-name>"),
            _user("<command-name>handoff</command-name>"),
        ])
        _, out, err = _run(["--session", "sess-1", "--jsonl"], tmp_path)
        assert len(out.splitlines()) == 1
        assert "deduped=1" in err

    def test_no_dedup_keeps_the_repeat(self, tmp_path):
        _write_session(tmp_path, "proj-a", "sess-1", [_user(LEAK_TYPED)])
        _write_session(tmp_path, "proj-a", "sess-2", [_user(LEAK_TYPED)])
        _, out, err = _run(["--session", "sess-1", "--session", "sess-2",
                            "--jsonl", "--no-dedup"], tmp_path)
        assert len(out.splitlines()) == 2
        assert "deduped=0" in err

    def test_out_writes_a_file_and_leaves_stdout_clean(self, tmp_path):
        _write_session(tmp_path, "proj-a", "sess-1", [_user(LEAK_TYPED)])
        dest = tmp_path / "msgs.md"
        rc, out, _ = _run(["--session", "sess-1", "-o", str(dest)], tmp_path)
        assert rc == X.EXIT_OK
        assert LEAK_TYPED in dest.read_text()
        assert out == ""


class TestPipingToHead:
    """🔴 REGRESSION, and it was reachable from the DOCUMENTED invocation.
    `--help` and the reference doc both show `… | jq …`; piping the real tool
    to `head` raised `BrokenPipeError` with a traceback. A tool that crashes on
    its own documented pipeline teaches the reader the pipeline is wrong."""

    #: Two cut points, because there are TWO failures behind one symptom: the
    #: failed WRITE (the `except BrokenPipeError` arm) and CPython's retry of
    #: the flush AT SHUTDOWN (the `os.dup2` beside it).
    #:
    #: 🔴 THIS CLASS PINS THE `except` ARM ONLY. It does NOT reliably pin the
    #: `dup2`, and that is stated rather than implied because getting it wrong
    #: has now cost false claims twice. Deleting the `dup2` and running this
    #: class scored, in order: SURVIVED — 0 red of 1 draw, and THAT GREEN DRAW
    #: IS WHY THIS HISTORY EXISTS; then 20/20 red; then an independent audit's
    #: 22/40; then 10/40. Same mutant, same test, controls clean every time.
    #: (An earlier version of this comment recorded the first draw as "red
    #: 1/1", converting the founding error out of the history in the one file
    #: a maintainer of this test reads.) Whether the
    #: shutdown flush still holds data depends on TextIOWrapper buffer state
    #: when the pipe closes, which varies with how far `head` got, which varies
    #: with machine load. **There is no rate to find; do not measure a fifth.**
    #: The `dup2` is pinned deterministically by
    #: `test_the_shutdown_flush_is_silenced_by_redirecting_fd_1` below instead.
    CUT_POINTS = (1, 5)

    def _pipe_to_head(self, tmp_path, n, *extra):
        import subprocess
        src = SA_DIR / "extract_user_msgs.py"
        p1 = subprocess.Popen(
            [sys.executable, str(src), "--session", "sess-1", "--jsonl",
             "--no-dedup", "--root", str(tmp_path), *extra],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        p2 = subprocess.Popen(["head", f"-{n}"], stdin=p1.stdout,
                              stdout=subprocess.PIPE)
        p1.stdout.close()
        head_out = p2.communicate()[0]
        err = p1.stderr.read().decode()
        p1.stderr.close()
        rc = p1.wait()
        return rc, head_out, err

    @pytest.mark.parametrize("n", CUT_POINTS)
    def test_a_closed_stdout_exits_quietly(self, tmp_path, n):
        """🔴 ASSERTS THE EXIT CODE, NOT ONLY THE ABSENCE OF A TRACEBACK — and
        the version this replaces asserted only the latter, which stopped
        detecting the defect the moment a sibling handler was added.

        `BrokenPipeError` SUBCLASSES `OSError`. When the write-failure guard
        (`except OSError` → exit 2) landed beside this one, deleting the
        dedicated `except BrokenPipeError` arm no longer produced a traceback:
        the broken pipe fell through to the sibling and exited **2**, quietly.
        The old assertions all still held, so the mutant scored
        KILLED-WRONG-REASON — a masked regression, not a caught one. And the
        masked behaviour is itself wrong: `… | head` exiting 2 breaks the Unix
        contract for the pipeline this tool's own --help documents.

        The ordering of the two `except` arms is therefore load-bearing, and
        this is what pins it."""
        _write_session(tmp_path, "proj-a", "sess-1",
                       [_user(f"{LEAK_TYPED}-{i}" + "x" * 4000,
                              ts=f"2026-09-01T00:00:{i:02d}Z")
                        for i in range(40)])
        rc, head_out, err = self._pipe_to_head(tmp_path, n)
        assert head_out.strip(), (
            "head got no row — the positive control failed, so a silent stderr "
            "would prove nothing")
        assert rc == X.EXIT_OK, (
            f"a closed pipe exited {rc}; `… | head` must exit 0. Exit "
            f"{X.EXIT_USAGE} here means the broken pipe was caught by the "
            "write-failure guard instead of its own arm")
        assert "BrokenPipeError" not in err, err
        assert "Exception ignored" not in err, err
        assert "Traceback" not in err, err


    def test_the_shutdown_flush_is_silenced_by_redirecting_fd_1(
            self, tmp_path, monkeypatch):
        """🔴 THE DETERMINISTIC PIN ON `os.dup2`, replacing a rate that could
        not be measured. It asserts the redirect HAPPENS — fd 1 is pointed at
        `os.devnull` when the pipe breaks — rather than asserting the stderr
        noise is absent, which is the flaky observable.

        Weaker than the behavioural claim and deliberately not dressed up as
        it: this proves the line runs, not that the user sees nothing. The
        consequence is real (124 bytes at `--jsonl | head -5`) but is buffer-
        timing dependent and cannot be asserted reliably."""
        _write_session(tmp_path, "proj-a", "sess-1", [_user(LEAK_TYPED)])

        calls = []
        real_dup2 = X.os.dup2
        monkeypatch.setattr(X.os, "dup2",
                            lambda a, b: calls.append((a, b)))

        class _BrokenStdout(io.StringIO):
            def write(self, s):
                raise BrokenPipeError(32, "Broken pipe")

        broken = _BrokenStdout()
        broken.fileno = lambda: 4242          # a sentinel, never dup2'd for real
        monkeypatch.setattr(sys, "stdout", broken)
        rc = X.main(["--session", "sess-1", "--jsonl", "--root", str(tmp_path)])

        assert rc == X.EXIT_OK, "a closed pipe is not an error exit"
        assert calls, "os.dup2 was never called — the shutdown flush is unguarded"
        devnull_fd, target = calls[0]
        assert target == 4242, (
            f"dup2 redirected fd {target}, not stdout's own descriptor")
        # The SOURCE fd must be /dev/null, not some other file. Compared by
        # device id, which is what makes it /dev/null rather than merely "a fd".
        assert os.fstat(devnull_fd).st_rdev == os.stat(os.devnull).st_rdev, (
            "fd 1 was redirected somewhere that is not /dev/null")
        os.close(devnull_fd)

    def test_the_dup2_pin_can_report_absence(self, tmp_path, monkeypatch):
        """Negative control on the test above: on a run where the handler never
        RUNS (no broken pipe), `calls` must be empty. Without this, a test
        asserting on a list nothing ever appends to would pass for the wrong
        reason.

        ⚠ This said "with the handler's redirect removed", which describes a
        setup the body does not build — the control is real but narrower than
        that sentence."""
        _write_session(tmp_path, "proj-a", "sess-1", [_user(LEAK_TYPED)])
        calls = []
        monkeypatch.setattr(X.os, "dup2", lambda a, b: calls.append((a, b)))
        # No BrokenPipeError raised ⇒ the handler never runs ⇒ no redirect.
        rc = X.main(["--session", "sess-1", "--jsonl", "-o",
                     str(tmp_path / "out.jsonl"), "--root", str(tmp_path)])
        assert rc == X.EXIT_OK
        assert calls == [], (
            "dup2 ran on a path with no broken pipe — the pin would then pass "
            "whatever the handler does")


class TestCoverageNotesReachEveryFormat:
    """🔴 `--jsonl` USED TO EMIT NO COVERAGE NOTE AT ALL — not the arc coverage
    line, not `unmeasured_notes`, not the UNSCOPED banner. They reached
    `render_markdown` only, so the CANONICAL machine form (and the one the
    documented pipeline produces) carried strictly LESS than markdown while
    three shipped sentences said the opposite. A chain rendered without its
    gaps reads as complete."""

    def test_the_arc_coverage_line_reaches_stderr_in_BOTH_formats(
            self, tmp_path, monkeypatch):
        _write_session(tmp_path, "proj-a", "s1", [_user(LEAK_TYPED)])
        fake = _fake_find_session(members=[_FakeMember("s1", "wrote")])
        monkeypatch.setattr(X, "_load_find_session", lambda: fake)
        for argv in (["--arc", "handoff-fake"],
                     ["--arc", "handoff-fake", "--jsonl"]):
            rc, out, err = _run(argv, tmp_path)
            assert rc == X.EXIT_OK, argv
            assert "carry no session id" in err, (
                f"{argv}: the coverage line is absent from stderr")
            assert "LEAKCANARY-note-is-surfaced" in err, (
                f"{argv}: an unmeasured_note the resolver produced was dropped")

    def test_the_UNSCOPED_banner_reaches_stderr_in_BOTH_formats(self, tmp_path):
        _write_session(tmp_path, "proj-a", "s1", [_user(LEAK_TYPED)])
        for argv in ([], ["--jsonl"]):
            rc, out, err = _run(argv, tmp_path)
            assert rc == X.EXIT_OK, argv
            assert "UNSCOPED" in err, f"{argv}: the banner is absent from stderr"

    def test_notes_reach_the_EXIT_6_path(self, tmp_path):
        """🔴 REGRESSION FROM THE ROUND-1 FIX. The notes loop was placed AFTER
        the `if not rows: return`, so on exit 6 the UNSCOPED banner, the arc
        coverage line and `unmeasured_notes` were emitted NOWHERE — and under
        `--arc`, exit 6 IS the measured zero whose coverage line decides
        whether the zero is real. Fixing --jsonl for rc 0 moved the gap
        instead of closing it."""
        _write_session(tmp_path, "proj-a", "s1",
                       [_user("[Request interrupted by user]")])
        rc, _, err = _run([], tmp_path)
        assert rc == X.EXIT_NO_MESSAGES
        assert "UNSCOPED" in err, (
            "the banner is absent on the exit-6 path — a measured zero with no "
            "statement of what it covered")

    def test_notes_reach_the_EXIT_5_path(self, tmp_path):
        rc, _, err = _run(["--session", "ghost"], tmp_path)
        assert rc == X.EXIT_NO_TRANSCRIPTS
        assert "ghost" in err, "the unresolved id is named nowhere"

    def test_arc_coverage_notes_reach_the_EXIT_6_path(self, tmp_path,
                                                      monkeypatch):
        """The case that matters most: an arc that resolved, whose sessions
        hold nothing typed. Without the coverage line the zero cannot be told
        from an incomplete chain."""
        _write_session(tmp_path, "proj-a", "s1",
                       [_user("[Request interrupted by user]")])
        fake = _fake_find_session(members=[_FakeMember("s1", "wrote")])
        monkeypatch.setattr(X, "_load_find_session", lambda: fake)
        rc, _, err = _run(["--arc", "handoff-fake"], tmp_path)
        assert rc == X.EXIT_NO_MESSAGES
        assert "carry no session id" in err, err

    def test_every_note_is_printed_EXACTLY_ONCE(self, tmp_path):
        """Notes used to be printed inline where they were appended AND again
        by the loop, so every one reached stderr twice and a consumer counting
        `!` lines double-counted the gap."""
        _write_session(tmp_path, "proj-a", "s1", [_user(LEAK_TYPED)])
        rc, _, err = _run(["--session", "s1", "--session", "ghost"], tmp_path)
        assert rc == X.EXIT_OK
        bang = [ln for ln in err.splitlines() if ln.startswith("! ")]
        assert len(bang) == len(set(bang)), f"a note printed twice: {bang}"
        assert sum("ghost" in ln for ln in bang) == 1, bang

    def test_jsonl_stdout_stays_one_record_per_line(self, tmp_path):
        """The notes go to stderr rather than a header record precisely so
        `| jq` needs no preamble skip. Pin that."""
        _write_session(tmp_path, "proj-a", "s1", [_user(LEAK_TYPED)])
        _, out, _ = _run(["--session", "s1", "--jsonl"], tmp_path)
        for line in out.splitlines():
            json.loads(line)        # every line, no exceptions


class TestExitSixIsAboutWhatWasREAD:
    """🔴 exit 6 asserts 'the transcripts WERE read'. It was derived from the
    SELECTION size, so two reachable inputs made that assertion false."""

    def test_an_absent_corpus_is_NOT_a_measured_emptiness(self, tmp_path):
        """Reachable on any host where `~/.claude/projects` does not exist —
        a fresh host, a different $HOME, a container, the nix sandbox."""
        rc, _, err = _run([], tmp_path / "nothing-here")
        assert rc != X.EXIT_NO_MESSAGES, (
            "an empty corpus reported a MEASURED emptiness having opened "
            "nothing — the opposite of the truth")
        assert rc == X.EXIT_NO_TRANSCRIPTS

    def test_a_selection_whose_files_are_all_unreadable_reads_NOTHING(
            self, tmp_path):
        p = _write_session(tmp_path, "proj-a", "s1", [_user(LEAK_TYPED)])
        p.chmod(0o000)
        try:
            rc, _, err = _run(["--session", "s1"], tmp_path)
        finally:
            p.chmod(0o644)
        assert rc == X.EXIT_NO_TRANSCRIPTS, (
            f"opened 0 transcripts and returned {rc}; exit "
            f"{X.EXIT_NO_MESSAGES} would claim they were read")
        low = err.lower()
        assert "0 were read" in low, err
        assert "nothing was measured" in low, err
        assert "permission denied" in low, (
            "the per-file reason must survive — 'could not be opened' without "
            "the errno sends the reader looking in the wrong place")

    def test_a_readable_but_EMPTY_transcript_IS_a_measured_emptiness(
            self, tmp_path):
        """The positive control: exit 6 must still be reachable, or the fix
        above would have moved the bug rather than removed it."""
        _write_session(tmp_path, "proj-a", "s1",
                       [_user("[Request interrupted by user]")])
        rc, _, err = _run(["--session", "s1"], tmp_path)
        assert rc == X.EXIT_NO_MESSAGES
        assert "read 1 transcript(s)" in err, err


class TestDedupErasureIsAnnounced:
    """🔴 A session dedup suppressed ENTIRELY vanishes from a per-session
    report, and a reader concludes it typed nothing. Cross-session dedup is
    intended; its erasure of a whole session is a gap and must be announced —
    the same standard this module already holds for unresolved ids."""

    def test_a_fully_suppressed_session_is_named_in_BOTH_channels(self, tmp_path):
        _write_session(tmp_path, "proj-a", "s1",
                       [_user(LEAK_TYPED, ts="2026-09-01T00:00:00Z")])
        _write_session(tmp_path, "proj-a", "s3",
                       [_user(LEAK_TYPED, ts="2026-09-01T00:00:01Z")])
        rc, out, err = _run(["--session", "s1", "--session", "s3"], tmp_path)
        assert rc == X.EXIT_OK
        assert "s3" in err, "the erased session is not named on stderr"
        assert "s3" in out, (
            "the erased session is not named in the markdown header — stderr "
            "is routinely discarded by a caller redirecting stdout")
        assert "--no-dedup" in out, "the header must name the way to see them"

    def test_a_PARTIALLY_suppressed_session_is_NOT_reported_as_erased(
            self, tmp_path):
        """Negative control — a session that still contributes a row is not a
        gap, and reporting it as one would make the warning noise."""
        _write_session(tmp_path, "proj-a", "s1",
                       [_user(LEAK_TYPED, ts="2026-09-01T00:00:00Z")])
        _write_session(tmp_path, "proj-a", "s2",
                       [_user(LEAK_TYPED, ts="2026-09-01T00:00:01Z"),
                        _user(LEAK_OTHER, ts="2026-09-01T00:00:02Z")])
        rc, out, err = _run(["--session", "s1", "--session", "s2"], tmp_path)
        assert rc == X.EXIT_OK
        assert "ABSENT from this report" not in err
        assert "ABSENT from this report" not in out


class TestTheWriteGuardsArePinned:
    """🔴 EVERY GUARD THIS PR ADDED TO THE WRITE PATH, PINNED. Round 3 built an
    ad-hoc sweep over the code round 2 shipped and found THREE reachable
    mutants surviving a fully green suite and a 33/33 battery — because the
    battery gained no rows for the new code. The worst turned a TOTAL write
    failure into `rc 0 … out=<path>`, which is the exact class this PR exists
    to remove. A battery is only evidence about the rows it holds."""

    def _big(self, tmp_path, n=200):
        _write_session(tmp_path, "proj-a", "s1",
                       [_user("Y" * 400 + str(i),
                              ts=f"2026-09-01T00:00:{i % 60:02d}Z")
                        for i in range(n)])

    def test_a_write_that_fails_ENTIRELY_does_not_report_success(self, tmp_path):
        """X6 — the CLOSE guard, and the fixture size is the whole test.

        🔴 A SMALL corpus on purpose. `/dev/full` accepts the open and fails
        every write, but for a large output the failure surfaces mid-render and
        the WRITE arm catches it — so a big fixture here exercises the wrong
        guard entirely. When the whole output fits in the buffer, nothing fails
        until `close()`, and with the close guard deleted this returned
        **`rc 0 … sessions=1 msgs=2 out=/dev/full`**: a write that put not one
        byte on the device, reported as a completed extraction.

        MEASURED while building this: aimed at the big fixture, the mutant
        survived this assertion and was caught only by a sibling — the test
        named the right defect and could not see it."""
        _write_session(tmp_path, "proj-a", "s1",
                       [_user(LEAK_TYPED, ts="2026-09-01T00:00:00Z"),
                        _user(LEAK_OTHER, ts="2026-09-01T00:00:01Z")])
        rc, _, err = _run(["--session", "s1", "--jsonl", "--no-dedup",
                           "-o", "/dev/full"], tmp_path)
        assert rc == X.EXIT_USAGE, (
            f"a write that wrote nothing returned {rc} — success")
        assert "INCOMPLETE" in err, err
        assert "sessions=" not in err, (
            "the success summary printed for a write that wrote nothing")

    def test_the_mid_write_arm_reports_rather_than_tracebacks(self, tmp_path):
        """X7. Deleting the write arm restores the 'traceback at rc 1
        discarding the whole result' this PR says it fixed. Reached with a
        corpus big enough to fail during render rather than at close."""
        self._big(tmp_path, n=400)
        rc, _, err = _run(["--session", "s1", "--jsonl", "--no-dedup",
                           "-o", "/dev/full"], tmp_path)
        assert rc == X.EXIT_USAGE
        assert "Traceback" not in err

    def test_BOTH_failure_arms_say_the_output_is_INCOMPLETE(self, tmp_path):
        """Which arm fires depends on buffer size, so the same failure must not
        report two different things — round 3 measured exactly that."""
        for n in (10, 400):
            _write_session(tmp_path, "proj-a", "s1",
                           [_user("Y" * 400 + str(i),
                                  ts=f"2026-09-01T00:00:{i % 60:02d}Z")
                            for i in range(n)])
            _, _, err = _run(["--session", "s1", "--jsonl", "--no-dedup",
                              "-o", "/dev/full"], tmp_path)
            assert "INCOMPLETE" in err, f"n={n}: {err!r}"

    def test_a_failed_o_write_SAYS_the_previous_content_is_gone(self, tmp_path):
        """🔴 THE DATA CLAIM. `open(path,"w")` truncates BEFORE the first write,
        so an output error leaves a partial file where the previous content
        was. The contract said 'Nothing was written'; MEASURED, 26 bytes of
        prior content became 2,048 bytes of partial output. A caller trusting
        that sentence leaves destroyed data in place."""
        self._big(tmp_path)
        _, _, err = _run(["--session", "s1", "--jsonl", "--no-dedup",
                          "-o", "/dev/full"], tmp_path)
        assert "GONE" in err or "gone" in err, (
            f"the caller is not told the previous content was destroyed: {err!r}")

    def test_sessions_counts_what_was_READ(self, tmp_path):
        """X5. Reverting this to `len(sources)` moved the output and survived."""
        _write_session(tmp_path, "proj-a", "s1", [_user(LEAK_TYPED)])
        p = _write_session(tmp_path, "proj-a", "s2", [_user(LEAK_OTHER)])
        p.chmod(0o000)
        try:
            rc, _, err = _run(["--session", "s1", "--session", "s2"], tmp_path)
        finally:
            p.chmod(0o644)
        assert rc == X.EXIT_OK
        assert "sessions=1 " in err, (
            f"sessions= must count the 1 transcript READ, not the 2 selected: "
            f"{err!r}")

    def test_notes_reach_the_ARC_EMPTY_path(self, tmp_path, monkeypatch):
        """X3. Claim 1 named exit 4 explicitly and nothing pinned it."""
        fake = _fake_find_session(members=())
        monkeypatch.setattr(X, "_load_find_session", lambda: fake)
        rc, _, err = _run(["--arc", "handoff-fake"], tmp_path)
        assert rc == X.EXIT_ARC_EMPTY
        assert "carry no session id" in err, err

    def test_notes_reach_the_IDS_FILE_usage_path(self, tmp_path, monkeypatch):
        """The return that broke the flusher's own universal — `--arc` and
        `--ids-file` are documented as combinable, and this path dropped all
        three arc notes."""
        fake = _fake_find_session(members=[_FakeMember("s1", "wrote")])
        monkeypatch.setattr(X, "_load_find_session", lambda: fake)
        rc, _, err = _run(["--arc", "handoff-fake",
                           "--ids-file", str(tmp_path / "nope.txt")], tmp_path)
        assert rc == X.EXIT_USAGE
        assert "carry no session id" in err, (
            f"the arc's coverage notes were dropped on this return: {err!r}")


class TestUnwritableOutputIsRefusedNotCrashed:
    def test_a_bad_o_path_exits_2_rather_than_traceback(self, tmp_path):
        _write_session(tmp_path, "proj-a", "s1", [_user(LEAK_TYPED)])
        rc, _, err = _run(["--session", "s1", "-o",
                           str(tmp_path / "no-such-dir" / "out.md")], tmp_path)
        assert rc == X.EXIT_USAGE, (
            "the extraction had already run; a traceback discards the result")
        assert "no-such-dir" in err

    def test_non_utf8_on_stdin_is_REPORTED_not_crashed_and_NOT_exit_2(
            self, tmp_path, monkeypatch):
        """🔴 PINS THE EXIT CODE, because the claim about it was wrong. The
        round-1 commit and PR comment both said "an unwritable -o and non-UTF-8
        on --ids-file - BOTH now exit 2". MEASURED: the -o case does; this one
        exits 0 — the replaced byte yields an id that resolves to nothing and
        is REPORTED as unresolved, which is the file branch's behaviour and is
        right. The code was fine and the sentence was false, and the test that
        shipped with it called `read_ids_file` directly so it pinned no exit
        code at all. A caller branching on 2 for this input branches on a code
        that never arrives."""
        _write_session(tmp_path, "proj-a", "s1", [_user(LEAK_TYPED)])

        class _Bin:
            @staticmethod
            def read():
                return b"s1\n\xff\xfe-not-utf8\n"
        monkeypatch.setattr(sys, "stdin",
                            type("S", (), {"buffer": _Bin,
                                           "read": staticmethod(lambda: "")})())
        rc, out, err = _run(["--ids-file", "-"], tmp_path)
        assert rc == X.EXIT_OK, (
            f"got {rc}; the readable id still extracts, so this is not a usage "
            "error — and it is emphatically not a traceback at rc 1")
        assert LEAK_TYPED in out
        assert "1 of 2" in err, "the undecodable id must be reported unresolved"


class TestUnscopedWalk:
    def test_with_no_selector_it_walks_the_corpus_and_SAYS_it_is_unscoped(
            self, tmp_path):
        _write_session(tmp_path, "proj-a", "sess-1", [_user(LEAK_TYPED)])
        _write_session(tmp_path, "proj-b", "sess-2", [_user(LEAK_OTHER)])
        rc, out, _ = _run([], tmp_path)
        assert rc == X.EXIT_OK
        assert LEAK_TYPED in out and LEAK_OTHER in out
        assert "UNSCOPED" in out, (
            "a corpus-wide answer that does not announce itself is the 54 MiB "
            "run reading as a scoped one")

    def test_a_REAL_shaped_subagent_transcript_is_excluded(self, tmp_path):
        """🔴 THE FIXTURE SHAPE IS THE WHOLE TEST, and the version this replaces
        got it wrong. It wrote `<root>/proj-a/sess-1/subagent-x.jsonl` — parent
        dir `sess-1` — and asserted the walk INCLUDED it, which it did, because
        nothing there is named `subagents`. A real subagent transcript lives at
        `<project>/<session-id>/subagents/agent-*.jsonl`, so its parent IS
        `subagents` and it was always excluded. The old test therefore asserted
        a shape the corpus does not have, and read as coverage for a claim
        (`the unscoped walk sees subagents`) that was false: MEASURED on the
        live corpus, 0 of 5,681 such transcripts were included."""
        _write_session(tmp_path, "proj-a/sess-1/subagents", "agent-aa845366",
                       [_user(LEAK_TYPED)])
        _write_session(tmp_path, "proj-a", "sess-1", [_user(LEAK_OTHER)])
        assert [p.stem for p in X.corpus_paths(root=tmp_path)] == ["sess-1"]

    def test_a_literal_subagents_dir_and_wf_dirs_are_skipped(self, tmp_path):
        _write_session(tmp_path, "subagents", "agent-1", [_user(LEAK_TYPED)])
        _write_session(tmp_path, "wf_123", "run-1", [_user(LEAK_OTHER)])
        _write_session(tmp_path, "proj-a", "sess-1", [_user("kept")])
        assert [p.stem for p in X.corpus_paths(root=tmp_path)] == ["sess-1"]

    def test_the_walk_IS_the_shared_enumerator_not_a_second_copy(self, tmp_path):
        """One rule, one place — asserted behaviourally, over the shape that
        separated the two: the private copy tested only the IMMEDIATE parent, so
        a transcript nested BELOW a `subagents/` dir passed it and fails
        `is_corpus_member`, which tests every parent part."""
        import transcript_search
        _write_session(tmp_path, "proj-a/sess-1/subagents/nested", "agent-9",
                       [_user(LEAK_TYPED)])
        _write_session(tmp_path, "proj-a", "sess-1", [_user(LEAK_OTHER)])
        assert X.corpus_paths(root=tmp_path) == list(
            transcript_search.iter_transcripts(root=tmp_path))
        assert [p.stem for p in X.corpus_paths(root=tmp_path)] == ["sess-1"], (
            "a transcript nested below a subagents/ dir must be excluded — the "
            "private walk this replaced included it")

    def test_the_walk_is_SORTED_so_a_run_is_deterministic(self, tmp_path):
        for name in ("sess-c", "sess-a", "sess-b"):
            _write_session(tmp_path, "proj-a", name, [_user(f"m-{name}")])
        paths = [p.stem for p in X.corpus_paths(root=tmp_path)]
        assert paths == sorted(paths)


class TestArcSessions:
    def test_the_resolver_is_IMPORTED_not_re_implemented(self, tmp_path):
        """🔴 Behavioural, not spelled. `arc_sessions` must obtain its chain by
        calling the CLI's `arc_report`; a private re-derivation here would let
        the two tools answer 'which sessions worked this doc' differently while
        neither said so."""
        fake = _fake_find_session(members=[_FakeMember("s1", "originated")])
        members, _ = X.arc_sessions("handoff-fake", find_session=fake)
        assert fake.calls == ["handoff-fake.md"], (
            "arc_report was not called — the glue was re-implemented")
        assert [m.session_id for m in members] == ["s1"]

    def test_every_coverage_note_the_resolver_produced_is_SURFACED(self):
        fake = _fake_find_session(members=[_FakeMember("s1", "wrote")])
        _, notes = X.arc_sessions("handoff-fake", find_session=fake)
        joined = "\n".join(notes)
        assert "carry no session id" in joined, (
            "🔴 the coverage line is what stops a partial chain reading as a "
            "complete one, and it must appear even when the count is zero")
        assert "LEAKCANARY-note-is-surfaced" in joined, (
            "an unmeasured_note the resolver produced was dropped")

    def test_an_unresolvable_seed_RAISES_rather_than_returning_empty(self):
        fake = _fake_find_session(basename="")
        with pytest.raises(X.ArcUnresolved):
            X.arc_sessions("nope", find_session=fake)

    def test_the_member_ROLE_reaches_the_markdown_heading(self, tmp_path,
                                                          monkeypatch):
        _write_session(tmp_path, "proj-a", "s1", [_user(LEAK_TYPED)])
        fake = _fake_find_session(members=[_FakeMember("s1", "earliest-stamped")])
        monkeypatch.setattr(X, "_load_find_session", lambda: fake)
        rc, out, _ = _run(["--arc", "handoff-fake"], tmp_path)
        assert rc == X.EXIT_OK
        assert "earliest-stamped" in out, (
            "the role is handoff_arc's vocabulary and carries the caveat that "
            "`originated` was demoted; dropping it loses that caveat")

    def test_arc_role_lands_in_the_jsonl(self, tmp_path, monkeypatch):
        _write_session(tmp_path, "proj-a", "s1", [_user(LEAK_TYPED)])
        fake = _fake_find_session(members=[_FakeMember("s1", "resumed")])
        monkeypatch.setattr(X, "_load_find_session", lambda: fake)
        _, out, _ = _run(["--arc", "handoff-fake", "--jsonl"], tmp_path)
        assert json.loads(out.splitlines()[0])["arc_role"] == "resumed"


# --- the --help contract -------------------------------------------------------

class TestHelpNamesEverySelector:
    """Two-way against the parser, so a selector added with no example fails."""

    SELECTOR_DESTS = {"arc", "session", "ids_file"}

    def _selector_flags(self):
        parser = X.build_parser()
        return {a.option_strings[0] for a in parser._actions
                if a.dest in self.SELECTOR_DESTS}

    def test_the_selector_set_matches_the_parser(self):
        """If a new selector lands, this fails first and names it — before the
        vaguer 'no example' failure below."""
        parser = X.build_parser()
        dests = {a.dest for a in parser._actions
                 # 🔴 THE EXCLUSION LIST IS "NOT A SELECTOR", NOT "BORING".
                 # A selector decides WHICH SESSIONS are read; these decide
                 # WHAT IS EMITTED from the ones already chosen.
                 # `include_answers` is the latter — it adds a `kind` to the
                 # output of whatever selection ran — so it is documented
                 # under EPILOG's `output` section, and adding it to
                 # SELECTOR_DESTS instead would demand a selector example for
                 # a flag that selects nothing.
                 if a.dest not in ("help", "jsonl", "out", "no_dedup", "root",
                                   "include_answers")}
        assert dests == self.SELECTOR_DESTS, (
            f"a selector was added or removed: {sorted(dests)}. Give it an "
            "example in EPILOG and add it here.")

    def test_every_selector_has_at_least_one_example_in_the_help(self):
        for flag in self._selector_flags():
            examples = [l for l in X.EPILOG.splitlines()
                        if "extract_user_msgs.py" in l and flag in l]
            assert examples, f"--help shows no example for {flag}"

    def test_both_output_formats_are_named_in_the_help(self):
        assert "--jsonl" in X.EPILOG
        assert "markdown" in X.EPILOG

    def test_every_exit_code_is_listed_in_the_help(self):
        for code, _ in X.EXIT_CONTRACT:
            assert f"\n  {code}  " in X.EPILOG, (
                f"exit {code} is in EXIT_CONTRACT but not in --help")

    def test_the_help_lists_no_code_the_contract_does_not_define(self):
        """The other direction — a stale code in the help is a wrong promise."""
        import re
        listed = {int(m) for m in re.findall(r"^  (\d)  ", X.EPILOG, re.M)}
        assert listed == {c for c, _ in X.EXIT_CONTRACT}, listed


# --- routing: the reference doc ------------------------------------------------

class TestTheReferenceDocIsRoutedAndDeployed:
    """Mirrors `test_subsystem_recall.py`'s sidecar guards. A reference file
    that is not git-tracked is silently absent from the flake source, so the
    deploy omits it and `/resume`'s pointer dangles on every host."""

    def test_the_reference_exists_beside_the_handoff_skill(self):
        assert REFERENCE.exists(), REFERENCE
        assert REFERENCE.parent.name == "reference"
        assert REFERENCE.parent.parent.name == "handoff"

    def test_resume_ROUTES_to_it(self):
        body = RESUME_SKILL.read_text(encoding="utf-8")
        assert "handoff/reference/user-messages.md" in body, (
            "/resume must name the reference, or nothing loads it")
        assert "extract_user_msgs.py" in body, (
            "the row must name the TOOL — an agent searching for the tool by "
            "name is how the row gets found")

    def test_the_reference_is_TRACKED_so_the_deploy_carries_it(self):
        """Two tiers, neither a skip — the shape `test_subsystem_recall.py`
        documents. In the nix sandbox there is no `.git`, and the file being
        here AT ALL is the proof, because the flake copies tracked files."""
        assert REFERENCE.exists()
        if not (REPO / ".git").exists():
            return
        import subprocess
        r = subprocess.run(
            ["git", "ls-files", "--error-unmatch",
             str(REFERENCE.relative_to(REPO))],
            cwd=REPO, capture_output=True, text=True)
        assert r.returncode == 0, (
            f"{REFERENCE.relative_to(REPO)} is untracked, so the flake omits it "
            f"and /resume's pointer dangles on every host.\n{r.stderr}")

    def test_the_reference_pin_can_report_absence(self):
        """Negative control: a check against a doc containing everything is
        indistinguishable from one pointed at the wrong file."""
        assert "a sentence deliberately absent from the user-messages reference" \
            not in REFERENCE.read_text(encoding="utf-8")

    #: 🔴 THE MEANING-BEARING WORDS OF EACH CODE, not its number. The integer
    #: check below is necessary and was NOT sufficient: findings 1 and 2 of
    #: round 2 were both a code whose MEANING had become false while its number
    #: was still present, and they shipped past a green suite because every
    #: guard on this table compared `{int}` sets. The test's name said "with its
    #: meaning" and its docstring said "a row for a code the tool no longer
    #: returns is a false promise" — reading as coverage while providing none,
    #: which is worse than no guard because it stops anyone looking.
    #:
    #: Keep these to words that must be TRUE of the code, never to a phrasing:
    #: a reword should pass, a changed meaning should not.
    #:
    #: 🔴 AND EACH MUST DISCRIMINATE — a single shared word does not. The first
    #: version used `("measured",)` for 3 and `("read",)` for 5, which every
    #: neighbouring row also satisfies: an audit showed all six rows could be
    #: rewritten to mean the OPPOSITE while the ledger stayed green, and codes
    #: 3↔4 and 5↔6 could have their texts SWAPPED outright — the pair the
    #: reference itself calls "the pair that matters most". A substring test
    #: cannot see an inversion; it CAN be made to see a swap, and
    #: `test_NO_TWO_CODES_SHARE_A_ROW…` enforces that by construction, so a
    #: token weakened back to a shared word fails there rather than here.
    EXIT_MEANING_TOKENS = {
        0: ("extracted",),
        2: ("invocation", "truncated"),
        3: ("nothing was measured",),
        4: ("zero member sessions",),
        5: ("nothing was read",),
        # ⚠ WAS `"zero user-typed"` AND THAT WORDING WENT STALE WITH THE FIX.
        # Exit 6 fires on an empty `rows`, which since devrc#1955 means zero of
        # FOUR kinds, not zero typed messages — a reader of the old sentence
        # could have concluded the decisions were merely filtered out.
        6: ("zero rows of any kind",),
    }

    def test_the_reference_documents_EVERY_exit_code_with_its_meaning(self):
        """Two-way against `EXIT_CONTRACT`: a code added to the tool with no
        row here leaves the reference reading as coverage while providing none,
        and a row for a code the tool no longer returns is a false promise."""
        import re
        text = REFERENCE.read_text(encoding="utf-8")
        rows = {int(m) for m in re.findall(r"^\| (\d) \|", text, re.M)}
        assert rows == {c for c, _ in X.EXIT_CONTRACT}, (
            f"the reference's exit table lists {sorted(rows)}; the tool "
            f"defines {sorted(c for c, _ in X.EXIT_CONTRACT)}")

    def test_the_reference_names_EVERY_kind_the_module_emits(self):
        """🔴 TWO-WAY AGAINST `KINDS`, AND THIS IS THE #1955 DEFECT IN ITS
        DOCUMENTATION SHAPE. Before the fix the reference said `kind` is
        "`typed` … or `command`" while the tool already emitted a third — so the
        one document routing an agent to this tool asserted a complete channel
        list that was short by one, and nothing said so. A kind added here with
        no prose beside it fails this, and a kind described here that the tool
        never emits is a promise it cannot keep."""
        text = REFERENCE.read_text(encoding="utf-8")
        import re
        documented = set(re.findall(r"^\| `([a-z_]+)` \|", text, re.M))
        assert documented == set(X.KINDS), (
            f"the reference's kind table lists {sorted(documented)}; the tool "
            f"emits {sorted(X.KINDS)}")

    def test_the_reference_says_the_decision_channel_needs_NO_FLAG(self):
        """The reference used to document `--include-answers` as the way in. A
        reader who still believes that reads a partial channel and has no way
        to know — the issue's exact failure mode, one layer up."""
        text = REFERENCE.read_text(encoding="utf-8")
        assert "on by default" in text, (
            "the reference does not say the decision channel is on by default")

    def test_the_reference_table_rows_still_MEAN_what_the_tool_returns(self):
        """The half the integer check cannot do — see EXIT_MEANING_TOKENS."""
        import re
        text = REFERENCE.read_text(encoding="utf-8")
        for code, tokens in self.EXIT_MEANING_TOKENS.items():
            (row,) = re.findall(rf"^\| {code} \| (.*)$", text, re.M)
            low = row.lower()
            missing = [tok for tok in tokens if tok not in low]
            assert not missing, (
                f"the reference's row for exit {code} no longer means what the "
                f"tool returns — missing {missing}. Row: {row!r}")

    def test_the_help_epilog_rows_still_MEAN_what_the_tool_returns(self):
        """🔴 THE THIRD SITE. `EXIT_CONTRACT` and the reference table were both
        corrected for the `-o` case while the epilog kept saying "nothing was
        searched" — a sweep applied to two of three sites, which is the shape
        the audit skill calls out by name. This is what makes the third one
        fail rather than be noticed by a reader."""
        import re
        for code, tokens in self.EXIT_MEANING_TOKENS.items():
            block = re.search(rf"^  {code}  (.*?)(?=^  \d  |\Z)",
                              X.EPILOG, re.M | re.S)
            assert block, f"--help lists no row for exit {code}"
            low = " ".join(block.group(1).split()).lower()
            missing = [tok for tok in tokens if tok not in low]
            assert not missing, (
                f"--help's row for exit {code} no longer means what the tool "
                f"returns — missing {missing}. Row: {low!r}")

    def test_the_meaning_token_ledger_covers_every_code(self):
        """Two-way, so a new exit code cannot be added with no meaning pinned."""
        assert set(self.EXIT_MEANING_TOKENS) == {c for c, _ in X.EXIT_CONTRACT}

    def test_the_meaning_check_detects_token_ERASURE(self):
        """Negative control #1 — a row that LOST its token fails."""
        import re
        text = REFERENCE.read_text(encoding="utf-8")
        (row,) = re.findall(r"^\| 5 \| (.*)$", text, re.M)
        mutated = row.lower().replace("read", "xxxx")
        missing = [t for t in self.EXIT_MEANING_TOKENS[5] if t not in mutated]
        assert missing == list(self.EXIT_MEANING_TOKENS[5]), (
            "the meaning check cannot detect a row that lost its meaning")

    def test_NO_TWO_CODES_SHARE_A_ROW_ie_the_rows_are_not_SWAPPABLE(self):
        """🔴 NEGATIVE CONTROL #2, AND THE ONE THE TOKEN CHECK CANNOT DO ALONE.

        Round 3 showed the token ledger green over every row rewritten to mean
        the OPPOSITE, and green when codes 3 and 4 had their texts SWAPPED —
        the pair the reference itself calls "the pair that matters most". A
        substring test cannot see an inversion; what it CAN see is that each
        row satisfies only its OWN code's tokens.

        So this asserts the rows are pairwise discriminating: give code N the
        text of code M and the ledger must reject it. That is what makes a
        swap, and most inversions, fail — and it is checked by construction
        rather than by imagining a mutation."""
        import re
        text = REFERENCE.read_text(encoding="utf-8")
        rows = {int(c): r.lower()
                for c, r in re.findall(r"^\| (\d) \| (.*)$", text, re.M)}
        confusable = []
        for code, tokens in self.EXIT_MEANING_TOKENS.items():
            for other, other_row in rows.items():
                if other == code:
                    continue
                if all(tok in other_row for tok in tokens):
                    confusable.append((code, other))
        assert not confusable, (
            "these rows satisfy another code's meaning tokens, so the two are "
            f"swappable while the ledger stays green: {confusable}. Give each "
            "code a token no other row can carry.")

    def test_the_reference_carries_a_MEASURED_cost_not_a_vague_one(self):
        """🔴 PINS THE SHAPE, NOT THE DIGITS — and the version this replaces
        pinned the digits. It required the literal `54.1 MiB`, `13,768` and
        `974`, two of which are LIVE-CORPUS counts that drift by design (the
        doc says so in its own caveat). So the only thing it could ever fire on
        was somebody CORRECTLY re-measuring: it went red on the right action
        and green on a stale number, which is backwards. It was already stale
        when written — the corpus read 13,780 the same day.

        What must hold is that the claim is anchored: the word MEASURED, a
        date, a byte figure and a transcript count, in a table. The values are
        the author's to update."""
        import re
        text = REFERENCE.read_text(encoding="utf-8")
        assert "MEASURED" in text, "the cost claim lost its MEASURED anchor"
        assert re.search(r"MEASURED\s+\d{4}-\d{2}-\d{2}", text), (
            "a measurement with no date cannot be judged stale")
        assert re.search(r"\d+(?:\.\d+)?\s*MiB", text), "no byte figure"
        assert re.search(r"\|\s*transcripts read\s*\|", text), (
            "the cost table lost its transcripts-read row")
        assert re.search(r"\d+×|\d+x", text), "no ratio — the headline claim"

    def test_the_reference_gives_a_RUNNABLE_command_for_every_selector(self):
        """🔴 PINS THE COMMAND BLOCK, NOT A MENTION. The version this replaces
        asserted `flag in text`, which a flag satisfies by appearing anywhere —
        including inside the exit-code table, where `--ids-file` is named only
        to explain exit 2. So the reference could lose every runnable example
        of a selector and still pass. Measured while building the mutation
        battery: `--ids-file` occurs 3x in this doc, and deleting the one
        example that teaches you to USE it killed nothing.

        What a reader needs is a command they can copy, so that is what is
        pinned: each selector must appear on a `python3 $E …` line."""
        import re
        text = REFERENCE.read_text(encoding="utf-8")
        commands = [ln for ln in text.splitlines()
                    if re.search(r"^\s*\|?\s*python3 \$E ", ln)]
        assert commands, "the reference has no runnable command block at all"
        for flag in ("--arc", "--session", "--ids-file", "--jsonl"):
            assert any(flag in ln for ln in commands), (
                f"{flag} appears in no `python3 $E …` command — a reader has "
                f"nothing to copy. Lines found: {commands}")


class TestNoCapturedTextEscapes:
    """devrc is PUBLIC. Nothing in this module may carry a real transcript."""

    #: 🔴 ONE LIST, so a canary added for a new channel is covered by both
    #: guards below without anyone remembering to extend them separately.
    CANARIES = (LEAK_TYPED, LEAK_OTHER, LEAK_REMINDER, LEAK_TOOL,
                LEAK_ASKED, LEAK_CHOSEN, LEAK_DECLINED, LEAK_ABANDONED,
                LEAK_ABANDONED_OPT)

    def test_every_fixture_string_is_a_canary(self):
        src = Path(__file__).read_text(encoding="utf-8")
        assert "LEAKCANARY" in src
        assert len(self.CANARIES) >= 9, self.CANARIES
        for canary in self.CANARIES:
            assert canary.startswith("LEAKCANARY-")

    def test_the_canaries_are_pairwise_distinct(self):
        canaries = list(self.CANARIES)
        assert len(set(canaries)) == len(canaries)
        for a in canaries:
            for b in canaries:
                if a is not b:
                    assert a not in b, (
                        f"{a!r} is a substring of {b!r} — a mutant that emits "
                        "the wrong one would survive")

    def test_no_canary_appears_in_a_constant_the_assertions_NAME(self):
        """🔴 A FIXTURE THAT CAN ONLY PRODUCE THE EXPECTED CONSTANT'S OWN VALUE
        CANNOT SEE A MUTANT THAT HARDCODES THE LITERAL — it survives a fully
        green suite. The decision tests assert `startswith(X.UNANSWERED_PREFIX)`
        AND that a canary is present; those two are only independent claims
        while the prefixes carry no canary of their own."""
        named = (X.UNANSWERED_PREFIX, X.NO_ANSWER_TEXT_PREFIX,
                 X.KIND_DECISION, X.KIND_DECISION_UNANSWERED)
        for const in named:
            for canary in self.CANARIES:
                assert canary not in const, (
                    f"{canary!r} is inside {const!r} — the assertion pairing "
                    "them proves nothing")
