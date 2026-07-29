"""Unit tests for browser-agent-parse.py — the opencode `--format json` transcript
→ final-answer-schema extractor.

Fully deterministic: canned JSONL strings modelled on the REAL opencode 1.18.4
`run --format json` envelope (verified live: events are newline-delimited JSON,
the assistant text is in `{"type":"text","part":{"type":"text","text":...}}`,
bracketed by `step_start`/`step_finish`). No opencode, no network.
"""
import importlib.util
import json
from pathlib import Path

import pytest

_MOD_PATH = (Path(__file__).resolve().parent.parent / "browser-agent-parse.py")
_spec = importlib.util.spec_from_file_location("browser_agent_parse", _MOD_PATH)
P = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(P)


def _text_event(text):
    return json.dumps({"type": "text", "sessionID": "ses_1",
                       "part": {"type": "text", "text": text,
                                "messageID": "msg_1"}})


def _step_start():
    return json.dumps({"type": "step_start",
                       "part": {"type": "step-start"}})


def _step_finish():
    return json.dumps({"type": "step_finish",
                       "part": {"type": "step-finish", "reason": "stop",
                                "tokens": {"total": 10}, "cost": 0.001}})


def _tool_event(name):
    # A tool call/result event — the parser MUST ignore these (they are not the
    # assistant's text answer).
    return json.dumps({"type": "tool",
                       "part": {"type": "tool", "tool": name,
                                "state": {"status": "completed",
                                          "input": {"command": "browser --tab 5 text"},
                                          "output": "some page text"}}})


SCHEMA = {"answer": "The top story is Foo.",
          "evidence": ["Foo — 500 points"], "steps_used": 3, "status": "ok"}


def _transcript(*lines):
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
def test_extracts_schema_from_real_shaped_stream():
    t = _transcript(_step_start(), _text_event(json.dumps(SCHEMA)),
                    _step_finish())
    out = P.extract_schema(P.collect_text(t.splitlines()))
    assert out == SCHEMA


def test_ignores_tool_events_and_extracts_final_text():
    t = _transcript(_step_start(), _tool_event("bash"),
                    _text_event(json.dumps(SCHEMA)), _step_finish())
    out = P.extract_schema(P.collect_text(t.splitlines()))
    assert out == SCHEMA
    # The tool output ("some page text") must NOT be treated as the answer.
    assert out["answer"] == "The top story is Foo."


def test_extracts_schema_embedded_in_prose():
    prose = ("Here is my final answer.\n\n" + json.dumps(SCHEMA)
             + "\n\nDone.")
    t = _transcript(_text_event(prose), _step_finish())
    out = P.extract_schema(P.collect_text(t.splitlines()))
    assert out == SCHEMA


def test_concatenates_multiple_text_parts():
    # opencode may stream the final message as several text parts.
    j = json.dumps(SCHEMA)
    half = len(j) // 2
    t = _transcript(_text_event(j[:half]), _text_event(j[half:]),
                    _step_finish())
    out = P.extract_schema(P.collect_text(t.splitlines()))
    assert out == SCHEMA


def test_picks_the_last_schema_object():
    early = {"answer": "early guess", "evidence": [], "steps_used": 1,
             "status": "partial"}
    t = _transcript(_text_event(json.dumps(early)),
                    _text_event(json.dumps(SCHEMA)), _step_finish())
    out = P.extract_schema(P.collect_text(t.splitlines()))
    assert out == SCHEMA          # the LAST one wins (the final answer)


def test_brace_in_string_does_not_end_object_early():
    schema = {"answer": "contains a } brace and a { brace in the text",
              "evidence": ["a{b}c"], "steps_used": 2, "status": "ok"}
    t = _transcript(_text_event(json.dumps(schema)), _step_finish())
    out = P.extract_schema(P.collect_text(t.splitlines()))
    assert out == schema


def test_no_json_returns_none():
    t = _transcript(_text_event("I could not complete the task, sorry."),
                    _step_finish())
    assert P.extract_schema(P.collect_text(t.splitlines())) is None


def test_json_without_schema_keys_returns_none():
    # A JSON blob that is not the schema (no answer/status) is ignored.
    t = _transcript(_text_event('{"foo": 1, "bar": [2,3]}'), _step_finish())
    assert P.extract_schema(P.collect_text(t.splitlines())) is None


def test_normalizes_loose_fields():
    loose = {"answer": "ok", "evidence": "single-string-not-list",
             "steps_used": "4", "status": "weird"}
    t = _transcript(_text_event(json.dumps(loose)), _step_finish())
    out = P.extract_schema(P.collect_text(t.splitlines()))
    assert out["evidence"] == ["single-string-not-list"]   # coerced to a list
    assert out["steps_used"] == 4                           # coerced to int
    assert out["status"] == "blocked"                       # invalid → blocked


def test_missing_evidence_and_steps_default():
    minimal = {"answer": "just this", "status": "ok"}
    t = _transcript(_text_event(json.dumps(minimal)), _step_finish())
    out = P.extract_schema(P.collect_text(t.splitlines()))
    assert out == {"answer": "just this", "evidence": [],
                   "steps_used": 0, "status": "ok"}


def test_non_json_lines_are_skipped():
    t = "not json at all\n" + _text_event(json.dumps(SCHEMA)) + "\nalso not\n"
    out = P.extract_schema(P.collect_text(t.splitlines()))
    assert out == SCHEMA


# --- the CLI entrypoint (exit codes the wrapper branches on) ---------------- #
def test_main_exit0_and_compact_output(tmp_path, capsys):
    f = tmp_path / "t.jsonl"
    f.write_text(_transcript(_text_event(json.dumps(SCHEMA)), _step_finish()))
    rc = P.main([str(f)])
    assert rc == 0
    out = capsys.readouterr().out.strip()
    assert json.loads(out) == SCHEMA
    assert "\n" not in out                     # single compact line


def test_main_exit2_on_no_schema(tmp_path):
    f = tmp_path / "t.jsonl"
    f.write_text(_transcript(_text_event("no json here"), _step_finish()))
    assert P.main([str(f)]) == 2


def test_main_exit2_on_missing_file(tmp_path):
    assert P.main([str(tmp_path / "nope.jsonl")]) == 2
