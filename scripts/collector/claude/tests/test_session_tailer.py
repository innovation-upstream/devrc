"""Tests for the Layer-A session-summary emitter (session-tailer.py).

Covers:
  * rollup correctness — tool_counts, tokens, languages (by file extension), git
    commit/push counting, message counts, duration, interruptions, tool_errors
    (+ categories), models, task/mcp/web flags, churn, first_prompt,
  * ts conversion (session START, UTC),
  * idempotency (unchanged transcript → no re-emit) + MUTABLE re-emit (grows → re-emit),
  * subagent / wf_ dir skip,
  * the `unreadable` path (garbage file / empty file),
  * emit-format round-trips through the real collector parser.

No network. The real `emit` shell helper writes to a temp spool; the real
collector module parses it back — mirroring test_tailer.py.
"""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

_CLAUDE_DIR = Path(__file__).resolve().parent.parent          # scripts/collector/claude
_COLLECTOR_DIR = _CLAUDE_DIR.parent                            # scripts/collector
sys.path.insert(0, str(_CLAUDE_DIR))
sys.path.insert(0, str(_COLLECTOR_DIR))
import collector as C   # noqa: E402

# session-tailer.py has a hyphen → load via importlib (like test_activity_scan.py).
_spec = importlib.util.spec_from_file_location("session_tailer", _CLAUDE_DIR / "session-tailer.py")
S = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(S)

EMIT = _COLLECTOR_DIR / "emit"


# --------------------------------------------------------------------------- #
# Transcript fixture helpers
# --------------------------------------------------------------------------- #
def user_typed(text, *, ts="2026-07-11T10:00:00.000Z", cwd="/home/zach/workspace/devrc",
               uuid="u", isMeta=False, isSidechain=False):
    return {"type": "user", "uuid": uuid, "timestamp": ts, "cwd": cwd,
            "gitBranch": "main", "isMeta": isMeta, "isSidechain": isSidechain,
            "message": {"role": "user", "content": text}}


def user_tool_result(*, is_error, text, ts="2026-07-11T10:05:00.000Z",
                     cwd="/home/zach/workspace/devrc"):
    return {"type": "user", "timestamp": ts, "cwd": cwd,
            "message": {"role": "user", "content": [
                {"type": "tool_result", "is_error": is_error, "content": text}]}}


def assistant(tool_uses=None, *, model="claude-opus-4-8", input_tokens=0,
              output_tokens=0, ts="2026-07-11T10:01:00.000Z",
              cwd="/home/zach/workspace/devrc", isSidechain=False):
    content = []
    for tu in (tool_uses or []):
        content.append({"type": "tool_use", "name": tu[0], "input": tu[1]})
    return {"type": "assistant", "timestamp": ts, "cwd": cwd,
            "isSidechain": isSidechain,
            "message": {"role": "assistant", "model": model, "content": content,
                        "usage": {"input_tokens": input_tokens,
                                  "output_tokens": output_tokens}}}


def _write(projects_dir: Path, project_dirname: str, session: str, objs):
    d = projects_dir / project_dirname
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{session}.jsonl"
    p.write_text("\n".join(json.dumps(o) for o in objs) + "\n", encoding="utf-8")
    return p


# --------------------------------------------------------------------------- #
# Pure helpers
# --------------------------------------------------------------------------- #
def test_lang_for_path():
    assert S.lang_for_path("a/b/foo.py") == "Python"
    assert S.lang_for_path("x.nix") == "Nix"
    assert S.lang_for_path("README.md") == "Markdown"
    assert S.lang_for_path("k8s.yaml") == "YAML"
    assert S.lang_for_path("Dockerfile") == "Dockerfile"
    assert S.lang_for_path("noext") is None
    assert S.lang_for_path("") is None


def test_count_lines():
    assert S.count_lines("") == 0
    assert S.count_lines(None) == 0
    assert S.count_lines("one line") == 1
    assert S.count_lines("a\nb\nc") == 3


def test_git_commit_push_detection():
    assert S.is_git_commit("git commit -m 'x'")
    assert S.is_git_commit("git -C /repo commit -m x")
    assert S.is_git_commit("git add . && git commit -m x")
    assert not S.is_git_commit("git status")
    assert not S.is_git_commit("gitk")
    assert S.is_git_push("git push origin main")
    assert S.is_git_push("git -C /r push")
    assert not S.is_git_push("git pull")


def test_categorize_tool_error():
    assert S.categorize_tool_error("bash: exit code 1, command failed") == "Command Failed"
    assert S.categorize_tool_error("No such file or directory") == "File Not Found"
    assert S.categorize_tool_error("File has not been read yet") == "File Not Found"
    assert S.categorize_tool_error("operation timed out") == "Timeout"
    assert S.categorize_tool_error("permission denied") == "Permission Denied"
    assert S.categorize_tool_error("weird thing") == "Other"


def test_churn():
    assert S.churn("Write", {"content": "a\nb\nc"}) == (3, 0)
    assert S.churn("Edit", {"old_string": "x\ny", "new_string": "1\n2\n3"}) == (3, 2)
    assert S.churn("MultiEdit", {"edits": [
        {"old_string": "a", "new_string": "b\nc"},
        {"old_string": "d\ne", "new_string": "f"}]}) == (3, 3)
    assert S.churn("Bash", {"command": "ls"}) == (0, 0)


# --------------------------------------------------------------------------- #
# build_rollup correctness
# --------------------------------------------------------------------------- #
def test_rollup_full():
    objs = [
        user_typed("implement the feature", ts="2026-07-11T10:00:00.000Z"),
        assistant([("Read", {"file_path": "a.py"}),
                   ("Bash", {"command": "git commit -m x && git push"})],
                  input_tokens=100, output_tokens=2000,
                  ts="2026-07-11T10:01:00.000Z"),
        assistant([("Edit", {"file_path": "a.py", "old_string": "x", "new_string": "1\n2"}),
                   ("Write", {"file_path": "notes.md", "content": "line1\nline2\nline3"}),
                   ("Task", {"description": "sub"}),
                   ("WebSearch", {"query": "q"}),
                   ("mcp__serena__find_symbol", {"name": "foo"})],
                  input_tokens=50, output_tokens=500,
                  ts="2026-07-11T10:30:00.000Z"),
        user_tool_result(is_error=True, text="bash: command failed exit code 2"),
        user_typed("[Request interrupted by user]", ts="2026-07-11T10:40:00.000Z"),
    ]
    r = S.build_rollup(objs)
    assert r["tool_counts"]["Read"] == 1
    assert r["tool_counts"]["Edit"] == 1 and r["tool_counts"]["Write"] == 1
    assert r["input_tokens"] == 150 and r["output_tokens"] == 2500
    assert r["assistant_message_count"] == 2
    assert r["user_message_count"] == 1  # only the genuine typed turn (interrupt not genuine)
    assert r["user_interruptions"] == 1
    assert r["git_commits"] == 1 and r["git_pushes"] == 1
    assert r["languages"]["Python"] == 1 and r["languages"]["Markdown"] == 1
    assert r["files_modified"] == 2
    assert r["lines_added"] == 2 + 3 and r["lines_removed"] == 1
    assert r["tool_errors"] == 1
    assert r["tool_error_categories"]["Command Failed"] == 1
    assert r["uses_task_agent"] is True and r["uses_mcp"] is True
    assert r["uses_web_search"] is True and r["uses_web_fetch"] is False
    assert r["models"] == ["claude-opus-4-8"]
    assert r["first_prompt"] == "implement the feature"
    assert r["start_ts"] == "2026-07-11 10:00:00.000"
    assert r["end_ts"] == "2026-07-11 10:40:00.000"
    assert r["duration_minutes"] == 40
    assert r["unreadable"] is False
    assert r["cwd"] == "/home/zach/workspace/devrc"


def test_rollup_slash_command_counts_as_user_turn_not_first_prompt():
    objs = [
        user_typed("<command-name>handoff</command-name><command-args>now</command-args>",
                   ts="2026-07-11T09:00:00.000Z"),
        user_typed("real question", ts="2026-07-11T09:05:00.000Z"),
    ]
    r = S.build_rollup(objs)
    assert r["user_message_count"] == 2
    # first_prompt is the first TYPED turn, not the slash command
    assert r["first_prompt"] == "real question"


def test_rollup_skips_sidechain_and_meta():
    objs = [
        user_typed("genuine", ts="2026-07-11T10:00:00.000Z"),
        user_typed("meta noise", isMeta=True, ts="2026-07-11T10:01:00.000Z"),
        assistant([("Read", {"file_path": "z.py"})], isSidechain=True,
                  ts="2026-07-11T10:02:00.000Z"),
    ]
    r = S.build_rollup(objs)
    assert r["user_message_count"] == 1
    assert r["assistant_message_count"] == 0  # sidechain assistant skipped
    assert "Read" not in r["tool_counts"]


def test_rollup_unreadable_when_no_messages():
    r = S.build_rollup([{"type": "summary", "summary": "x"}])
    assert r["unreadable"] is True
    assert r["user_message_count"] == 0 and r["assistant_message_count"] == 0


# --------------------------------------------------------------------------- #
# summarize_transcript + unreadable file paths
# --------------------------------------------------------------------------- #
def test_summarize_garbage_file(tmp_path):
    p = tmp_path / "bad.jsonl"
    p.write_text("this is not json\n{also not\n", encoding="utf-8")
    r = S.summarize_transcript(str(p))
    assert r["unreadable"] is True


def test_summarize_empty_file(tmp_path):
    p = tmp_path / "empty.jsonl"
    p.write_text("", encoding="utf-8")
    r = S.summarize_transcript(str(p))
    assert r["unreadable"] is True


# --------------------------------------------------------------------------- #
# run(): idempotency, mutable re-emit, skip dirs, emit round-trip
# --------------------------------------------------------------------------- #
@pytest.fixture
def env(tmp_path, monkeypatch):
    spool = tmp_path / "spool"
    spool.mkdir()
    state = tmp_path / "session-summary-state.json"
    projects = tmp_path / "projects"
    projects.mkdir()
    monkeypatch.setenv("ACTIVITY_SPOOL_DIR", str(spool))
    monkeypatch.setenv("CLAUDE_SUMMARY_STATE", str(state))
    monkeypatch.setenv("CLAUDE_SOURCE_EMIT", str(EMIT))
    monkeypatch.setenv("CLAUDE_PROJECTS_DIR", str(projects))
    return {"spool": spool, "state": state, "projects": projects}


def _spool_events(spool: Path) -> list[dict]:
    cur = spool / "current.log"
    if not cur.exists():
        return []
    return [ev for ev in (C.parse_line(l) for l in cur.read_text().splitlines()) if ev]


def test_emits_one_summary_per_session_and_roundtrips(env):
    _write(env["projects"], "-home-zach-workspace-devrc", "sess-A", [
        user_typed("do a thing", cwd="/home/zach/workspace/devrc"),
        assistant([("Bash", {"command": "git commit -m x"})], output_tokens=42),
    ])
    assert S.run() == 0
    evs = _spool_events(env["spool"])
    assert len(evs) == 1
    ev = evs[0]
    assert ev["source"] == "claude"
    assert ev["kind"] == "session-summary"
    assert ev["session"] == "sess-A"
    assert ev["project"] == "devrc"
    assert ev["app"] == "claude-code"
    assert ev["ts"] == "2026-07-11 10:00:00.000"
    payload = json.loads(ev["payload"])
    assert payload["git_commits"] == 1
    assert payload["output_tokens"] == 42
    assert payload["unreadable"] is False


def test_idempotent_no_reemit_when_unchanged(env):
    _write(env["projects"], "-home-zach-workspace-devrc", "s1", [
        user_typed("hello"), assistant([("Read", {"file_path": "a.py"})]),
    ])
    assert S.run() == 0
    assert S.run() == 0  # second run: unchanged → no new event
    assert len(_spool_events(env["spool"])) == 1


def test_mutable_reemit_when_transcript_grows(env):
    p = _write(env["projects"], "-home-zach-workspace-devrc", "s1", [
        user_typed("hello", ts="2026-07-11T10:00:00.000Z"),
    ])
    assert S.run() == 0
    assert len(_spool_events(env["spool"])) == 1
    # session grows (a later turn) → signature changes → re-emit
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(assistant([("Edit", {"file_path": "b.go",
                "old_string": "x", "new_string": "y"})],
                ts="2026-07-11T11:00:00.000Z")) + "\n")
    assert S.run() == 0
    evs = _spool_events(env["spool"])
    assert len(evs) == 2  # append-only: two rows for the same session
    latest = json.loads(evs[-1]["payload"])
    assert latest["languages"].get("Go") == 1
    assert latest["duration_minutes"] == 60


def test_subagents_and_wf_dirs_skipped(env):
    _write(env["projects"], "subagents", "sub1", [user_typed("agent work")])
    _write(env["projects"], "wf_12345", "wf1", [user_typed("workflow work")])
    _write(env["projects"], "-home-zach-workspace-devrc", "real", [user_typed("real work")])
    assert S.run() == 0
    evs = _spool_events(env["spool"])
    assert [e["session"] for e in evs] == ["real"]


def test_unreadable_session_still_emits_flagged(env):
    d = env["projects"] / "-home-zach-workspace-devrc"
    d.mkdir(parents=True, exist_ok=True)
    (d / "bad.jsonl").write_text("garbage not json\n", encoding="utf-8")
    assert S.run() == 0
    evs = _spool_events(env["spool"])
    assert len(evs) == 1
    assert json.loads(evs[0]["payload"])["unreadable"] is True


def test_state_round_trips(env):
    _write(env["projects"], "-home-zach-workspace-devrc", "s1", [user_typed("hi")])
    S.run()
    sigs = S.load_state(env["state"])
    assert any(k.endswith("s1.jsonl") for k in sigs)
