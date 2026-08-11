"""Tests for the Layer-A session-summary emitter (session-tailer.py).

Covers:
  * rollup correctness — tool_counts, tokens (incl. cache-read/creation), languages
    (by file extension), git commit/push counting, message counts, duration,
    interruptions, tool_errors (+ categories), models, task/mcp/web flags, churn,
  * no raw prompt free-text leaks into the payload (first_prompt was dropped),
  * ts conversion (session START, UTC); sidechain turns excluded from duration,
  * idempotency (unchanged transcript → no re-emit),
  * EMIT-ON-SETTLE: an active session doesn't re-emit every tick; a settled one
    emits once; a resumed-after-settle one re-emits; the interim backstop bounds
    a never-settling session; state survives a restart; a corrupt/missing/v1
    state file degrades instead of crashing,
  * subagent / wf_ dir skip,
  * the `unreadable` path (garbage file / empty file),
  * emit-format round-trips through the real collector parser.

No network. The real `emit` shell helper writes to a temp spool; the real
collector module parses it back — mirroring test_tailer.py.
"""
import importlib.util
import json
import sys
import time
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

# The row-count cap this emitter must respect is OWNED by the validation
# harness, not by this file — read it from there so the two cannot drift. (The
# module must be registered in sys.modules before exec_module: it defines a
# @dataclass, and dataclasses resolves annotations via sys.modules[__module__].)
_INV_PATH = _COLLECTOR_DIR.parent / "validation" / "invariants.py"
_inv_spec = importlib.util.spec_from_file_location("devrc_invariants", _INV_PATH)
INV = importlib.util.module_from_spec(_inv_spec)
sys.modules["devrc_invariants"] = INV
_inv_spec.loader.exec_module(INV)

# Settle policy in seconds, from the module's own defaults (no magic numbers).
SETTLE = S.DEFAULT_SETTLE_MINUTES * 60.0
INTERIM = S.DEFAULT_INTERIM_HOURS * 3600.0


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
              output_tokens=0, cache_read_tokens=0, cache_creation_tokens=0,
              ts="2026-07-11T10:01:00.000Z",
              cwd="/home/zach/workspace/devrc", isSidechain=False):
    content = []
    for tu in (tool_uses or []):
        content.append({"type": "tool_use", "name": tu[0], "input": tu[1]})
    return {"type": "assistant", "timestamp": ts, "cwd": cwd,
            "isSidechain": isSidechain,
            "message": {"role": "assistant", "model": model, "content": content,
                        "usage": {"input_tokens": input_tokens,
                                  "output_tokens": output_tokens,
                                  "cache_read_input_tokens": cache_read_tokens,
                                  "cache_creation_input_tokens": cache_creation_tokens}}}


def _write(projects_dir: Path, project_dirname: str, session: str, objs):
    d = projects_dir / project_dirname
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{session}.jsonl"
    p.write_text("\n".join(json.dumps(o) for o in objs) + "\n", encoding="utf-8")
    return p


def _append(path: Path, obj, *, mtime: float | None = None):
    """Append a turn to a transcript (simulating a live session growing). The
    file's mtime IS the settle signal, so tests can pin it explicitly."""
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(obj) + "\n")
    if mtime is not None:
        import os as _os
        _os.utime(path, (mtime, mtime))
    return path


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
                  cache_read_tokens=1000, cache_creation_tokens=200,
                  ts="2026-07-11T10:01:00.000Z"),
        assistant([("Edit", {"file_path": "a.py", "old_string": "x", "new_string": "1\n2"}),
                   ("Write", {"file_path": "notes.md", "content": "line1\nline2\nline3"}),
                   ("Task", {"description": "sub"}),
                   ("WebSearch", {"query": "q"}),
                   ("mcp__serena__find_symbol", {"name": "foo"})],
                  input_tokens=50, output_tokens=500,
                  cache_read_tokens=3000, cache_creation_tokens=50,
                  ts="2026-07-11T10:30:00.000Z"),
        user_tool_result(is_error=True, text="bash: command failed exit code 2"),
        user_typed("[Request interrupted by user]", ts="2026-07-11T10:40:00.000Z"),
    ]
    r = S.build_rollup(objs)
    assert r["tool_counts"]["Read"] == 1
    assert r["tool_counts"]["Edit"] == 1 and r["tool_counts"]["Write"] == 1
    assert r["input_tokens"] == 150 and r["output_tokens"] == 2500
    assert r["cache_read_tokens"] == 4000 and r["cache_creation_tokens"] == 250
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
    # first_prompt was DROPPED (unscrubbed raw-prompt leak surface — reintroduced
    # in PR-2 only after the scrubber lands). No raw prompt text in the rollup.
    assert "first_prompt" not in r
    assert "message_hours" not in r
    assert r["start_ts"] == "2026-07-11 10:00:00.000"
    assert r["end_ts"] == "2026-07-11 10:40:00.000"
    assert r["duration_minutes"] == 40
    assert r["unreadable"] is False
    assert r["cwd"] == "/home/zach/workspace/devrc"


def test_rollup_slash_command_counts_as_user_turn():
    objs = [
        user_typed("<command-name>handoff</command-name><command-args>now</command-args>",
                   ts="2026-07-11T09:00:00.000Z"),
        user_typed("real question", ts="2026-07-11T09:05:00.000Z"),
    ]
    r = S.build_rollup(objs)
    assert r["user_message_count"] == 2
    # no raw prompt free-text is stored (first_prompt dropped in PR-1 hardening)
    assert "first_prompt" not in r


def test_rollup_has_no_raw_prompt_freetext_field():
    """The rollup must NOT carry any raw transcript free-text (unscrubbed leak
    surface deferred to PR-2). Guards against re-adding first_prompt et al."""
    objs = [
        user_typed("this prompt might contain a pasted secret token ABC123",
                   ts="2026-07-11T10:00:00.000Z"),
        assistant([("Read", {"file_path": "a.py"})], ts="2026-07-11T10:01:00.000Z"),
    ]
    r = S.build_rollup(objs)
    assert "first_prompt" not in r
    assert "message_hours" not in r
    serialized = json.dumps(r)
    assert "pasted secret token" not in serialized


def test_rollup_cache_tokens_summed():
    objs = [
        user_typed("go", ts="2026-07-11T10:00:00.000Z"),
        assistant([], input_tokens=10, output_tokens=20,
                  cache_read_tokens=500, cache_creation_tokens=30,
                  ts="2026-07-11T10:01:00.000Z"),
        assistant([], input_tokens=5, output_tokens=8,
                  cache_read_tokens=100, cache_creation_tokens=0,
                  ts="2026-07-11T10:02:00.000Z"),
    ]
    r = S.build_rollup(objs)
    assert r["input_tokens"] == 15 and r["output_tokens"] == 28
    assert r["cache_read_tokens"] == 600 and r["cache_creation_tokens"] == 30


def test_rollup_sidechain_timestamps_dont_inflate_duration():
    """A subagent/sidechain turn timestamped far outside the session window must
    NOT stretch duration_minutes — the sidechain skip happens before min/max."""
    objs = [
        user_typed("start", ts="2026-07-11T10:00:00.000Z"),
        assistant([("Read", {"file_path": "a.py"})], ts="2026-07-11T10:10:00.000Z"),
        # sidechain turn 5 hours later — must be ignored for duration + counts
        assistant([("Read", {"file_path": "z.py"})], isSidechain=True,
                  ts="2026-07-11T15:00:00.000Z"),
    ]
    r = S.build_rollup(objs)
    assert r["duration_minutes"] == 10   # not 300
    assert r["end_ts"] == "2026-07-11 10:10:00.000"
    assert "Read" in r["tool_counts"] and r["tool_counts"]["Read"] == 1


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


def test_settled_growth_reemits_the_complete_rollup(env):
    """A session that grew and then SETTLED re-emits, and the newest row is the
    complete rollup (the argMax-on-read contract stays correct).

    🔴 UPDATED 2026-08-02. This test used to settle 20 minutes after the
    first-seen emit and assert the row landed immediately. That is precisely the
    amplification being fixed — the settled branch now consults `emitted_at`, so
    a settle inside the interim window DEFERS. Both halves are asserted here:
    the deferral, and that the deferral costs nothing but latency (the rollup
    still lands, still complete, on the first tick past the window). Reverting
    the fix turns the first half red.
    """
    t0 = time.time()
    p = _write(env["projects"], "-home-zach-workspace-devrc", "s1", [
        user_typed("hello", ts="2026-07-11T10:00:00.000Z"),
    ])
    assert S.run(now=t0) == 0                    # first-seen
    assert len(_spool_events(env["spool"])) == 1
    # session grows (a later turn) …
    _append(p, assistant([("Edit", {"file_path": "b.go",
                                    "old_string": "x", "new_string": "y"})],
                         ts="2026-07-11T11:00:00.000Z"), mtime=t0)
    # … and settles, but only SETTLE after the last emit → DEFERRED, no new row
    assert S.run(now=t0 + SETTLE + 1) == 0
    assert len(_spool_events(env["spool"])) == 1
    # … and once the interim window has passed it lands, complete. The deferral
    # did NOT record the new signature, which is what makes this re-evaluate.
    assert S.run(now=t0 + INTERIM + SETTLE + 1) == 0
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


def test_run_checkpoints_and_resumes_after_interrupt(env, monkeypatch):
    """A run interrupted (SIGTERM-style) mid-backfill must persist the sessions it
    already emitted, so the next run RESUMES rather than re-emitting everything
    (the first-deploy duplicate-storm the checkpointing fix prevents)."""
    monkeypatch.setattr(S, "CHECKPOINT_EVERY", 1)  # checkpoint after every emit
    for i in range(3):
        _write(env["projects"], "-home-zach-workspace-devrc", f"s{i}",
               [user_typed(f"session {i}", ts=f"2026-07-11T1{i}:00:00.000Z")])

    real_emit = S.emit_event
    calls = {"n": 0}

    def flaky_emit(emit, ev):
        if calls["n"] >= 2:              # emit two, then simulate SIGTERM
            raise KeyboardInterrupt("simulated interrupt mid-backfill")
        calls["n"] += 1
        return real_emit(emit, ev)

    monkeypatch.setattr(S, "emit_event", flaky_emit)
    with pytest.raises(KeyboardInterrupt):
        S.run()

    # two sessions reached the spool AND were checkpointed to state
    assert len(_spool_events(env["spool"])) == 2
    checkpointed = [k for k in S.load_state(env["state"]) if k.endswith(".jsonl")]
    assert len(checkpointed) == 2

    # resume with a healthy emit: only the un-emitted session emits (no re-storm)
    monkeypatch.setattr(S, "emit_event", real_emit)
    assert S.run() == 0
    assert len(_spool_events(env["spool"])) == 3  # 2 + 1, NOT 2 + 3


def test_run_prunes_deleted_transcripts_from_state(env):
    """A transcript that disappears is dropped from the state file on the next
    full pass (state doesn't grow forever)."""
    p = _write(env["projects"], "-home-zach-workspace-devrc", "gone", [user_typed("hi")])
    S.run()
    assert any(k.endswith("gone.jsonl") for k in S.load_state(env["state"]))
    p.unlink()
    S.run()
    assert not any(k.endswith("gone.jsonl") for k in S.load_state(env["state"]))


# --------------------------------------------------------------------------- #
# One unsummarisable transcript must not cost every other session's summary
# --------------------------------------------------------------------------- #
# 🔴 The abort this closes was NOT hypothetical-only: `run()` had no per-session
# try, so any exception out of `summarize_transcript` killed the pass and stopped
# ALL claude session-summary emission until the offending transcript changed.
# Most shapes are now rejected at the extraction site (see
# test_session_tailer_paths.py::TestClaudeUnusableFilePaths); a NON-STRING
# `timestamp` is deliberately left unguarded there, so these tests exercise the
# wrapper with a case no earlier check rejects rather than with dead code.
_BAD_TS_TURN = {"type": "assistant", "timestamp": 12345,
                "cwd": "/srv/checkouts/widget-repo",
                "message": {"role": "assistant", "model": "m", "usage": {},
                            "content": []}}


def _summary_line(capsys) -> str:
    out = capsys.readouterr()
    line = [l for l in out.out.splitlines() if l.startswith("session-tailer: scanned")]
    assert len(line) == 1, out.out
    return line[0] + "\n<<STDERR>>\n" + out.err


def test_a_run_with_no_bad_transcript_reports_failed_zero(env, capsys):
    """POSITIVE CONTROL, first half. A `failed=0` from a counter wired to
    nothing is indistinguishable from a real zero — so the pair below shows the
    number MOVE across two runs of the same code path."""
    for i in range(3):
        _write(env["projects"], "-home-zach-workspace-devrc", f"ok{i}",
               [user_typed(f"session {i}", ts=f"2026-07-11T1{i}:00:00.000Z")])
    assert S.run() == 0
    text = _summary_line(capsys)
    assert "failed=0" in text
    assert "ERROR summarising" not in text
    assert len(_spool_events(env["spool"])) == 3


def test_one_unsummarisable_transcript_does_not_abort_the_run(env, capsys):
    """POSITIVE CONTROL, second half: same three good sessions, plus one bad —
    failed moves 0 -> 1, and all three good sessions still emit."""
    for i in range(3):
        _write(env["projects"], "-home-zach-workspace-devrc", f"ok{i}",
               [user_typed(f"session {i}", ts=f"2026-07-11T1{i}:00:00.000Z")])
    _write(env["projects"], "-home-zach-workspace-devrc", "bad",
           [user_typed("hi"), _BAD_TS_TURN])

    assert S.run() == 0
    text = _summary_line(capsys)
    assert "failed=1" in text
    # REPORTED, not swallowed: the offending path and the exception are named.
    assert "ERROR summarising" in text
    assert "bad.jsonl" in text
    assert "TypeError" in text
    # and the three healthy sessions were NOT collateral damage
    assert sorted(e["session"] for e in _spool_events(env["spool"])) == \
        ["ok0", "ok1", "ok2"]


def test_a_skipped_transcript_is_not_marked_done_and_retries(env, capsys):
    """The signature is recorded ONLY after a successful emit, so a skipped
    transcript is re-evaluated next tick instead of being silently written off —
    and it emits for real once the content becomes summarisable."""
    p = _write(env["projects"], "-home-zach-workspace-devrc", "bad",
               [user_typed("hi"), _BAD_TS_TURN])
    assert S.run() == 0
    assert "failed=1" in _summary_line(capsys)
    assert not any(k.endswith("bad.jsonl") for k in S.load_state(env["state"]))

    # still bad on the next pass → still reported, still not marked done
    assert S.run() == 0
    assert "failed=1" in _summary_line(capsys)
    assert _spool_events(env["spool"]) == []

    # content becomes readable → it emits, with no change to the state file
    p.write_text(json.dumps(user_typed("hi")) + "\n", encoding="utf-8")
    assert S.run() == 0
    assert "failed=0" in _summary_line(capsys)
    assert [e["session"] for e in _spool_events(env["spool"])] == ["bad"]


def test_an_emit_failure_is_still_FATAL(env, monkeypatch):
    """🔴 The wrapper's scope is load-bearing. `emit_event` is OUTSIDE it: a
    broken spool/helper is systemic, and turning it into a per-session skip
    would convert a loud outage into a quiet count — the exact silent-zero this
    payload exists to remove. Widening the try to cover the emit turns this
    test red."""
    _write(env["projects"], "-home-zach-workspace-devrc", "s1", [user_typed("hi")])

    def broken_emit(emit, ev):
        raise RuntimeError("spool is unwritable")

    monkeypatch.setattr(S, "emit_event", broken_emit)
    with pytest.raises(RuntimeError, match="spool is unwritable"):
        S.run()


def test_the_unusable_path_counter_reaches_the_payload(env):
    """End to end: the diagnostic is on the emitted event, not just in a rollup
    dict, so it is queryable rather than only visible in a unit test."""
    _write(env["projects"], "-home-zach-workspace-devrc", "s1", [
        user_typed("hi", cwd="/srv/checkouts/widget-repo"),
        assistant([("Write", {"file_path": "   ", "content": "x"})],
                  cwd="/srv/checkouts/widget-repo"),
    ])
    assert S.run() == 0
    payload = json.loads(_spool_events(env["spool"])[0]["payload"])
    assert payload["unusable_file_paths"] == 1
    assert payload["changed_paths"] == []


# --------------------------------------------------------------------------- #
# emit_decision — the pure settle policy (fake clock, no sleeping)
# --------------------------------------------------------------------------- #
def _decide(prev, sig, mtime, now, settle=SETTLE, interim=INTERIM):
    return S.emit_decision(prev, sig, mtime, now, settle, interim)


def test_decision_unchanged_signature_never_emits():
    prev = {"sig": "a", "emitted_at": 0.0}
    assert _decide(prev, "a", mtime=0.0, now=10 * INTERIM) == (False, "unchanged")


def test_decision_settled_emits():
    """🔴 UPDATED 2026-08-02: the fixture's last emit must now be older than the
    interim window, because the settled branch is rate-limited on `emitted_at`.
    The old fixture (`emitted_at=100.0`, `now=1000+SETTLE`) is only ~35 min past
    the last emit and is now the DEFERRED case — see
    test_decision_settled_is_rate_limited_by_the_last_emit, which pins it."""
    prev = {"sig": "old", "emitted_at": 100.0}
    assert _decide(prev, "new", mtime=1000.0,
                   now=100.0 + INTERIM + SETTLE) == (True, "settled")


# --------------------------------------------------------------------------- #
# 🔴 The settled-branch rate limit (2026-08-02).
#
# THE BUG: the settled branch returned (True, "settled") without ever consulting
# `emitted_at`, while the interim branch immediately below it was rate-limited.
# So an ACTIVE session was bounded to one emit per interim window, and a session
# idling BETWEEN BURSTS was bounded by nothing — one full rollup per burst,
# forever. That is the ordinary `claude --resume` shape, i.e. the bound was
# missing from exactly the common case. Measured over the 30 days to 2026-08-02:
# 31,815 rows / 482 sessions, median 2 but p90 209, max 572, mean 66, against
# validation/invariants.py's `session_summary_rows_bounded` (>24/session/24h).
# --------------------------------------------------------------------------- #
def test_decision_settled_is_rate_limited_by_the_last_emit():
    """🔴 THE regression pin. RED before the fix: this returned
    (True, "settled") because the branch never read `emitted_at`."""
    prev = {"sig": "old", "emitted_at": 1000.0}
    # settled (idle >= SETTLE) but the last emit was only an hour ago
    assert _decide(prev, "new", mtime=1000.0,
                   now=1000.0 + 3600) == (False, "settled-recently")


def test_decision_settled_reemits_after_a_long_gap():
    """🔴 The case the rate limit MUST NOT swallow, pinned explicitly: a session
    that genuinely settled, was resumed DAYS later, and settles again. Its
    `emitted_at` is far older than the interval, so it emits."""
    prev = {"sig": "old", "emitted_at": 1000.0}
    two_days = 2 * 86400
    assert _decide(prev, "new", mtime=1000.0 + two_days,
                   now=1000.0 + two_days + SETTLE) == (True, "settled")


def test_decision_settled_with_no_known_last_emit_always_emits():
    """A session's FIRST rollup is never deferred — neither for a transcript we
    have never emitted for, nor for a v1/corrupt state entry with no timestamp.
    This is what keeps `session_summary_no_orphans` (2h grace) satisfiable."""
    assert _decide(None, "new", mtime=1000.0,
                   now=1000.0 + SETTLE) == (True, "settled")
    assert _decide({"sig": "old", "emitted_at": None}, "new", mtime=1000.0,
                   now=1000.0 + SETTLE) == (True, "settled")


def test_decision_settle_zero_bypasses_the_rate_limit():
    """The documented `CLAUDE_SUMMARY_SETTLE_MINUTES=0` escape hatch restores
    the pre-emit-on-settle "every change emits" behaviour, so the rate limit
    deliberately does not apply to it — otherwise the knob would be a no-op."""
    prev = {"sig": "old", "emitted_at": 1000.0}
    assert _decide(prev, "new", mtime=1000.0, now=1000.0,
                   settle=0) == (True, "settled")


def test_decision_settled_rate_limit_follows_the_interim_knob():
    """One knob, not two: the settled bound IS `interim_s`. Shrinking the knob
    shrinks the bound, and `interim=0` (the documented "no interim emits"
    setting) restores the old unbounded settled behaviour — which is what makes
    it usable as the positive control in the end-to-end test below."""
    prev = {"sig": "old", "emitted_at": 1000.0}
    assert _decide(prev, "new", mtime=1000.0, now=1000.0 + 3600,
                   interim=1800)[0] is True          # bound below the gap
    assert _decide(prev, "new", mtime=1000.0, now=1000.0 + 3600,
                   interim=7200) == (False, "settled-recently")
    assert _decide(prev, "new", mtime=1000.0, now=1000.0 + 3600,
                   interim=0) == (True, "settled")


def test_decision_active_first_seen_emits_once_then_defers():
    # never emitted before → one emit so a live session isn't missing entirely
    assert _decide(None, "new", mtime=1000.0, now=1000.0) == (True, "first-seen")
    # emitted a moment ago and still active → deferred
    prev = {"sig": "old", "emitted_at": 1000.0}
    assert _decide(prev, "new", mtime=1000.0, now=1000.0 + 60) == (False, "active")


def test_decision_interim_backstop():
    prev = {"sig": "old", "emitted_at": 1000.0}
    # still active (just written) but last emit is older than the backstop
    assert _decide(prev, "new", mtime=1000.0 + INTERIM,
                   now=1000.0 + INTERIM) == (True, "interim")


def test_decision_unknown_last_emit_emits_once():
    """A v1/corrupt entry with no emit timestamp emits once to establish one."""
    prev = {"sig": "old", "emitted_at": None}
    assert _decide(prev, "new", mtime=1000.0, now=1000.0) == (True, "interim")


def test_decision_settle_zero_disables_the_gate():
    prev = {"sig": "old", "emitted_at": 1000.0}
    assert _decide(prev, "new", mtime=1000.0, now=1000.0,
                   settle=0)[0] is True  # every change emits (legacy behaviour)


def test_decision_interim_zero_disables_the_backstop():
    prev = {"sig": "old", "emitted_at": 0.0}
    assert _decide(prev, "new", mtime=1000.0, now=1000.0,
                   interim=0) == (False, "active")


def test_settle_policy_env_overrides(monkeypatch):
    monkeypatch.setenv("CLAUDE_SUMMARY_SETTLE_MINUTES", "5")
    monkeypatch.setenv("CLAUDE_SUMMARY_INTERIM_HOURS", "2")
    assert S.settle_seconds() == 300.0
    assert S.interim_seconds() == 7200.0
    # garbage / negative / empty all fall back to the defaults (never crash)
    for bad in ("", "abc", "-3"):
        monkeypatch.setenv("CLAUDE_SUMMARY_SETTLE_MINUTES", bad)
        assert S.settle_seconds() == SETTLE


# --------------------------------------------------------------------------- #
# run(): emit-on-settle end to end (injected clock — no sleeping)
# --------------------------------------------------------------------------- #
def test_active_session_does_not_reemit_on_consecutive_ticks(env):
    """The regression this fix targets: a live session used to re-ship its whole
    rollup on EVERY 5-min tick (97.4% of all rows were superseded duplicates)."""
    t0 = time.time()
    p = _write(env["projects"], "-home-zach-workspace-devrc", "live", [
        user_typed("start", ts="2026-07-11T10:00:00.000Z"),
    ])
    assert S.run(now=t0) == 0                       # first-seen emit
    assert len(_spool_events(env["spool"])) == 1
    for tick in range(1, 13):                       # an hour of 5-min ticks
        t = t0 + tick * 300
        _append(p, assistant([("Read", {"file_path": f"f{tick}.py"})],
                             ts="2026-07-11T10:%02d:00.000Z" % tick), mtime=t)
        assert S.run(now=t) == 0
    assert len(_spool_events(env["spool"])) == 1    # still ONE row, not 13


def test_settled_session_emits_exactly_once(env):
    """A session idle past the settle window emits its final rollup once, and
    never again while it stays untouched."""
    t0 = time.time()
    _write(env["projects"], "-home-zach-workspace-devrc", "done", [
        user_typed("finish up", ts="2026-07-11T10:00:00.000Z"),
        assistant([("Bash", {"command": "git commit -m x"})],
                  ts="2026-07-11T10:20:00.000Z"),
    ])
    now = t0 + SETTLE + 1
    assert S.run(now=now) == 0
    assert len(_spool_events(env["spool"])) == 1
    for later in (now + 300, now + 3600, now + 10 * INTERIM):
        assert S.run(now=later) == 0
    assert len(_spool_events(env["spool"])) == 1    # exactly once, forever


def test_resumed_after_settle_reemits_the_final_rollup(env):
    """`claude --resume` is real here (multi-day sessions exist). A settled
    session that gets resumed must re-emit once it settles again, so the newest
    row — the one argMax picks — is the COMPLETE rollup."""
    t0 = time.time()
    p = _write(env["projects"], "-home-zach-workspace-devrc", "resumed", [
        user_typed("day one", ts="2026-07-11T10:00:00.000Z"),
    ])
    assert S.run(now=t0 + SETTLE + 1) == 0          # settled → final rollup #1
    assert len(_spool_events(env["spool"])) == 1

    # …two days later the session is resumed and grows again
    t_resume = t0 + 2 * 86400
    _append(p, assistant([("Edit", {"file_path": "b.go", "old_string": "x",
                                    "new_string": "y"})],
                         ts="2026-07-13T10:00:00.000Z"), mtime=t_resume)
    # The first tick after the resume emits once (its last emit is older than the
    # interim backstop, so the live session shows up again promptly) …
    assert S.run(now=t_resume + 300) == 0
    assert len(_spool_events(env["spool"])) == 2
    # … and then it is ACTIVE again → no per-tick storm on the following ticks.
    for tick in range(2, 8):
        _append(p, assistant([("Read", {"file_path": "a.py"})],
                             ts="2026-07-13T10:00:00.000Z"),
                mtime=t_resume + tick * 300)
        assert S.run(now=t_resume + tick * 300) == 0
    assert len(_spool_events(env["spool"])) == 2
    # Once it settles again it re-emits, and the newest row is complete.
    # 🔴 UPDATED 2026-08-02: the settle must now be past the interim window from
    # the previous emit (at t_resume+300), because the settled branch is
    # rate-limited. Inside the window it defers — asserted first, so this test
    # covers BOTH sides of the fix rather than quietly moving the goalposts.
    assert S.run(now=t_resume + 8 * 300 + SETTLE) == 0
    assert len(_spool_events(env["spool"])) == 2         # deferred, not emitted
    assert S.run(now=t_resume + 300 + INTERIM + SETTLE) == 0
    evs = _spool_events(env["spool"])
    assert len(evs) == 3
    latest = json.loads(evs[-1]["payload"])
    assert latest["languages"].get("Go") == 1       # the resumed work is in it
    assert latest["end_ts"] == "2026-07-13 10:00:00.000"


def test_interim_backstop_bounds_a_never_settling_session(env):
    """A session that NEVER goes idle still gets bounded interim rollups (so a
    12h agent run isn't missing from the report), at most one per INTERIM."""
    t0 = time.time()
    p = _write(env["projects"], "-home-zach-workspace-devrc", "marathon", [
        user_typed("long haul", ts="2026-07-11T10:00:00.000Z"),
    ])
    assert S.run(now=t0) == 0                       # first-seen
    ticks = int(12 * 3600 / 300)                    # 12h of 5-min ticks
    for tick in range(1, ticks + 1):
        t = t0 + tick * 300
        _append(p, assistant([("Read", {"file_path": "a.py"})],
                             ts="2026-07-11T10:00:00.000Z"), mtime=t)
        assert S.run(now=t) == 0
    n = len(_spool_events(env["spool"]))
    expected = 1 + int(12 * 3600 // INTERIM)        # first-seen + 3 interims
    assert n == expected, f"expected {expected} bounded emits, got {n}"
    assert n < 10                                   # vs 145 under the old rule


def test_bursty_resumed_session_stays_under_the_invariant_cap(env, monkeypatch):
    """🔴 The end-to-end regression, WITH its own positive control.

    Scenario = the measured one: a session worked on in short bursts, each
    followed by enough quiet to cross the settle window. Before the fix every
    such burst re-entered the settled branch and shipped a full rollup, so the
    row count tracked the burst count with no ceiling at all.

    The reassuring answer here is a SMALL NUMBER, and a small number is exactly
    what a harness wired to nothing produces. So the same scenario is run twice
    through the same code path, and the only thing that changes is the knob:

      POSITIVE CONTROL  interim=0 -> the settled branch is unbounded, which IS
                        the pre-fix behaviour. The count MUST move well past the
                        cap. If it does not, the harness is not generating
                        bursts and the bounded number below means nothing.
      UNDER TEST        the 4h default -> bounded.

    Cap is validation/invariants.py's SUMMARY_ROWS_PER_SESSION_CAP (24 rows per
    session per 24h), read from the module rather than restated here.
    """
    def burst_run(interim_hours):
        """36 bursts over 24h (one every 40 min), each settling before the next.
        Returns the number of session-summary rows emitted."""
        monkeypatch.setenv("CLAUDE_SUMMARY_INTERIM_HOURS", str(interim_hours))
        for f in env["spool"].glob("*"):
            f.unlink()
        if env["state"].exists():
            env["state"].unlink()
        t0 = time.time()
        p = _write(env["projects"], "-home-zach-workspace-devrc", "bursty", [
            user_typed("burst 0", ts="2026-07-11T10:00:00.000Z"),
        ])
        assert S.run(now=t0) == 0                      # first-seen
        step = 40 * 60                                 # > SETTLE, so each settles
        for i in range(1, 37):
            t = t0 + i * step
            _append(p, assistant([("Read", {"file_path": f"f{i}.py"})],
                                 ts="2026-07-11T10:00:00.000Z"), mtime=t)
            # tick once right after the write (active) and once after it settles
            assert S.run(now=t + 60) == 0
            assert S.run(now=t + SETTLE + 60) == 0
        return len(_spool_events(env["spool"]))

    cap = INV.SUMMARY_ROWS_PER_SESSION_CAP

    unbounded = burst_run(0)          # POSITIVE CONTROL — pre-fix behaviour
    assert unbounded > cap, (
        f"positive control produced only {unbounded} rows, which is under the "
        f"cap of {cap} — the scenario is not generating bursts, so the bounded "
        f"number below would be a fact about the harness, not about the fix"
    )

    bounded = burst_run(S.DEFAULT_INTERIM_HOURS)
    assert bounded <= cap, (
        f"{bounded} rows for one session in 24h, cap is {cap} "
        f"(session_summary_rows_bounded)"
    )
    # and it is a real reduction, not a rounding difference
    assert bounded < unbounded / 2


def test_state_survives_a_restart(env):
    """State is on disk, so a fresh process (timer re-fire / reboot) keeps the
    settle bookkeeping instead of re-emitting everything."""
    t0 = time.time()
    p = _write(env["projects"], "-home-zach-workspace-devrc", "s1", [
        user_typed("hi", ts="2026-07-11T10:00:00.000Z"),
    ])
    assert S.run(now=t0) == 0
    raw = json.loads(env["state"].read_text())
    assert raw["version"] == S.STATE_VERSION
    entry = next(v for k, v in raw["sessions"].items() if k.endswith("s1.jsonl"))
    assert entry["sig"] and entry["emitted_at"] == t0

    # "restart": run() re-reads the state file from scratch every pass.
    _append(p, assistant([("Read", {"file_path": "a.py"})]), mtime=t0 + 60)
    assert S.run(now=t0 + 60) == 0
    assert len(_spool_events(env["spool"])) == 1    # no re-emit after restart


def test_corrupt_state_file_does_not_kill_the_run(env):
    """Garbage state degrades to 'never emitted' (emit once, rewrite valid
    state) rather than raising and failing the systemd oneshot."""
    _write(env["projects"], "-home-zach-workspace-devrc", "s1", [user_typed("hi")])
    for junk in ("{not json", "[]", "null", '{"sessions": 5}', ""):
        env["state"].write_text(junk, encoding="utf-8")
        assert S.load_state(env["state"]) == {}
        assert S.run() == 0                          # no exception
        assert json.loads(env["state"].read_text())["version"] == S.STATE_VERSION


def test_missing_state_file_and_dir_are_created(env, tmp_path, monkeypatch):
    nested = tmp_path / "no" / "such" / "dir" / "state.json"
    monkeypatch.setenv("CLAUDE_SUMMARY_STATE", str(nested))
    _write(env["projects"], "-home-zach-workspace-devrc", "s1", [user_typed("hi")])
    assert S.load_state(nested) == {}
    assert S.run() == 0
    assert nested.exists()
    assert len(_spool_events(env["spool"])) == 1


def test_v1_state_file_migrates_without_a_reemit_storm(env):
    """The deployed state file is v1 ({"sigs": {path: sig}}). An UNCHANGED
    transcript must not re-emit just because the schema moved."""
    p = _write(env["projects"], "-home-zach-workspace-devrc", "s1", [user_typed("hi")])
    env["state"].write_text(json.dumps(
        {"version": 1, "sigs": {str(p): S.signature(str(p))}}), encoding="utf-8")
    loaded = S.load_state(env["state"])
    assert loaded[str(p)] == {"sig": S.signature(str(p)), "emitted_at": None}
    assert S.run(now=time.time()) == 0
    assert _spool_events(env["spool"]) == []        # unchanged → nothing emitted
    assert json.loads(env["state"].read_text())["version"] == S.STATE_VERSION
