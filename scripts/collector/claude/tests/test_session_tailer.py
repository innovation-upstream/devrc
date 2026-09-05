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
# 🔴 THE SAME module object the tailer imports. Asserting the prefilter against a
# SEPARATELY loaded copy of mention_scan would compare two independent readings
# and could not see the tailer reading a stale or different one.
import mention_scan as MS  # noqa: E402

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
    # 🔴 THE REPO MAPPING IS REDIRECTED TO A PATH THAT DOES NOT EXIST. Without
    # this, `run()` reads the OPERATOR'S REAL ~/.config/mention-open/known_repos
    # .json — which names PRIVATE repositories — so every attribution assertion
    # would be a property of that host's disk, and the nix sandbox tier (HOME is
    # a fresh empty dir) would evaluate it differently from the dev host. That is
    # the same two-tier divergence `test_mention_open.py` records for WORKSPACE.
    monkeypatch.setenv("MENTION_OPEN_KNOWN_REPOS", str(tmp_path / "no-mapping.json"))
    return {"spool": spool, "state": state, "projects": projects,
            "repos_path": tmp_path / "no-mapping.json"}


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

    # non-zero: the point of the wrapper is that the OTHER sessions survive, NOT
    # that the failure is forgiven. See the exit-status contract below.
    assert S.run() == 1
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
    # nothing else was due this tick, so emitted=0 → the run FAILS (see the
    # exit-status contract below); the point under test here is the STATE.
    assert S.run() == 1
    assert "failed=1" in _summary_line(capsys)
    assert not any(k.endswith("bad.jsonl") for k in S.load_state(env["state"]))

    # still bad on the next pass → still reported, still not marked done
    assert S.run() == 1
    assert "failed=1" in _summary_line(capsys)
    assert _spool_events(env["spool"]) == []

    # content becomes readable → it emits, with no change to the state file
    p.write_text(json.dumps(user_typed("hi")) + "\n", encoding="utf-8")
    assert S.run() == 0
    assert "failed=0" in _summary_line(capsys)
    assert [e["session"] for e in _spool_events(env["spool"])] == ["bad"]


# --------------------------------------------------------------------------- #
# `failed` must reach the EXIT STATUS, or it is the same silent zero one level up
# --------------------------------------------------------------------------- #
# 🔴 `claude-activity-source` is `Type=oneshot` with `OnFailure =
# notify-failure@%n.service`, and session-tailer.py is its SECOND ExecStart — so
# a non-zero return is the ONLY thing that reaches a toast. A run that counts
# every session as failed and still exits 0 is systemd-invisible: unit success,
# no OnFailure, and one stderr line in journald nobody is tailing.
def test_a_systemic_failure_FAILS_the_run(env, capsys):
    """Every session unsummarisable — the shape a broken deploy or a broken
    `changed_paths` produces. This MUST be non-zero."""
    for i in range(4):
        _write(env["projects"], "-home-zach-workspace-devrc", f"bad{i}",
               [user_typed("hi"), _BAD_TS_TURN])
    assert S.run() == 1
    text = _summary_line(capsys)
    assert "emitted=0 failed=4" in text
    assert "FAILING the run" in text


def test_ONE_bad_transcript_among_healthy_ones_still_fails_the_run(env, capsys):
    """A single failure is not "degraded data" — that session's summary is
    MISSING, and stays missing while the condition holds, so it must keep
    alerting. The healthy sessions still emit; the run still reports non-zero.

    ⚠ A ratio rule (`failed >= emitted`) was written first and rejected: this
    timer emits only CHANGED transcripts, so nearly every real tick has
    emitted=0 and the ratio collapses to `failed > 0` anyway."""
    for i in range(4):
        _write(env["projects"], "-home-zach-workspace-devrc", f"ok{i}",
               [user_typed(f"s{i}", ts=f"2026-07-11T1{i}:00:00.000Z")])
    _write(env["projects"], "-home-zach-workspace-devrc", "bad",
           [user_typed("hi"), _BAD_TS_TURN])
    assert S.run() == 1
    text = _summary_line(capsys)
    assert "emitted=4 failed=1" in text          # the good work still happened
    assert "FAILING the run" in text
    assert sorted(e["session"] for e in _spool_events(env["spool"])) == \
        ["ok0", "ok1", "ok2", "ok3"]


def test_the_exit_status_is_driven_by_failed_not_by_emitted(env):
    """Measured at BOTH ends of the emitted axis, not just one point: the
    verdict must not depend on how much healthy work happened alongside."""
    def _run_with(n_ok):
        for f in (env["projects"] / "-home-zach-workspace-devrc").glob("*.jsonl"):
            f.unlink()
        env["state"].unlink(missing_ok=True)
        for i in range(n_ok):
            _write(env["projects"], "-home-zach-workspace-devrc", f"ok{i}",
                   [user_typed(f"s{i}", ts=f"2026-07-11T1{i}:00:00.000Z")])
        _write(env["projects"], "-home-zach-workspace-devrc", "bad",
               [user_typed("hi"), _BAD_TS_TURN])
        return S.run()

    assert _run_with(0) == 1    # failed=1 emitted=0
    assert _run_with(9) == 1    # failed=1 emitted=9 — same verdict


def test_a_zero_message_transcript_with_a_bad_cwd_is_skipped_not_fatal(env, capsys):
    """🔴 THE 7th FATAL SHAPE — `build_event`, not `summarize_transcript`.

    PRE-EXISTING: this raises identically on the tree before this whole change,
    so it is neither introduced nor (until now) closed by it. Reachable when a
    transcript has ZERO messages *and* a non-str `cwd`: zero messages routes
    through `_mark_unobservable`, which SKIPS `CP.summarize` — the only guard
    that would reject that cwd — so the rollup comes back clean and
    `build_event` -> `project_basename(cwd)` -> `cwd.rstrip("/")` raises one
    line later, historically OUTSIDE the try.

    The cost was not just the abort: `run()` died before `save_state`, so every
    session that had already emitted this pass re-emitted next tick, forever.
    This test therefore pins the STATE as well as the survival.
    """
    _write(env["projects"], "-home-zach-workspace-devrc", "aaa-good",
           [user_typed("hi", ts="2026-07-11T09:00:00.000Z")])
    # no `user`/`assistant` turn → zero messages → unreadable; cwd is a list
    _write(env["projects"], "-home-zach-workspace-devrc", "zeromsg",
           [{"type": "system", "timestamp": "2026-07-11T10:00:00.000Z",
             "cwd": ["/srv/checkouts/widget-repo"]}])

    assert S.run() == 1
    text = _summary_line(capsys)
    assert "emitted=1 failed=1" in text
    assert "zeromsg.jsonl" in text and "AttributeError" in text
    # the healthy neighbour emitted …
    assert [e["session"] for e in _spool_events(env["spool"])] == ["aaa-good"]
    # … AND its signature was persisted, so it does not re-emit next tick
    assert any(k.endswith("aaa-good.jsonl") for k in S.load_state(env["state"]))
    assert len(_spool_events(env["spool"])) == 1
    S.run()
    assert len(_spool_events(env["spool"])) == 1


def test_a_clean_run_exits_zero(env):
    """The arm that keeps every assertion above from passing on a build that
    simply always returns 1."""
    _write(env["projects"], "-home-zach-workspace-devrc", "s1", [user_typed("hi")])
    assert S.run() == 0


def test_a_run_with_nothing_to_do_exits_zero(env):
    """No transcripts at all: emitted=0 and failed=0. `failed and …` guards the
    turnover, so an idle tick is not a failure."""
    assert S.run() == 0


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
    # `mentions` is the v3 field (the source=mentions ledger). A v1 entry has
    # never emitted one, so it migrates to the empty ledger — which re-emits any
    # mention once and re-converges, exactly like the unknown `emitted_at`.
    assert loaded[str(p)] == {"sig": S.signature(str(p)), "emitted_at": None,
                              "mentions": []}
    assert S.run(now=time.time()) == 0
    assert _spool_events(env["spool"]) == []        # unchanged → nothing emitted
    assert json.loads(env["state"].read_text())["version"] == S.STATE_VERSION


# --------------------------------------------------------------------------- #
# MENTIONS (source=mentions) — the second event stream this tailer emits
# --------------------------------------------------------------------------- #
# Every fixture below is SYNTHETIC. This repo is public and captured agent text
# is not committable, so the "assistant output" here is written for the shapes it
# exercises and nothing else.
def assistant_text(*texts, ts="2026-07-11T10:01:00.000Z",
                   cwd="/home/zach/workspace/devrc", isSidechain=False):
    """An assistant turn whose content is TEXT blocks (the ones the mention scan
    reads), as opposed to `assistant()` above which builds tool_use blocks."""
    return {"type": "assistant", "timestamp": ts, "cwd": cwd,
            "isSidechain": isSidechain,
            "message": {"role": "assistant", "model": "claude-opus-4-8",
                        "content": [{"type": "text", "text": t} for t in texts],
                        "usage": {}}}


def _mentions(spool):
    return [e for e in _spool_events(spool) if e["source"] == "mentions"]


def _summaries(spool):
    return [e for e in _spool_events(spool) if e["source"] == "claude"]


def test_collect_mentions_reads_assistant_text_only():
    """Tool inputs, tool RESULTS and user messages are deliberately out of
    scope: a tool result is usually a file the agent read, so scanning it would
    record the repo's own contents as 'mentions'."""
    objs = [
        user_typed("please look at #111"),
        user_tool_result(is_error=False, text="a file containing #222"),
        assistant([("Bash", {"command": "gh pr view 333"})]),
        assistant_text("done, see civitai/talos-infra#444"),
    ]
    got = S.collect_mentions(objs)
    assert [(m["platform"], m["id"]) for m in got] == [("github", "444")]


def test_collect_mentions_skips_sidechain_turns():
    objs = [assistant_text("#111", isSidechain=True), assistant_text("#222")]
    assert [m["id"] for m in S.collect_mentions(objs)] == ["222"]


def test_collect_mentions_dedupes_on_the_RAW_text_not_the_bare_id():
    """🔴 `devrc#7` and `talos-infra#7` are different references that share an
    id. Keying the ledger on the id alone would emit the first and silently drop
    the second for the life of the session."""
    objs = [assistant_text("devrc#7 and talos-infra#7 and devrc#7 again")]
    got = S.collect_mentions(objs)
    assert [m["raw"] for m in got] == ["devrc#7", "talos-infra#7"], (
        "keyed on the id, not the raw text")


def test_collect_mentions_is_capped(monkeypatch):
    monkeypatch.setattr(S, "MENTIONS_PER_SESSION_CAP", 3)
    objs = [assistant_text(" ".join(f"#{n}" for n in range(100, 200)))]
    assert len(S.collect_mentions(objs)) == 3


def test_collect_mentions_takes_the_turns_own_timestamp():
    objs = [assistant_text("#370", ts="2026-07-11T12:34:56.000Z")]
    (m,) = S.collect_mentions(objs)
    assert m["ts"] == "2026-07-11 12:34:56.000"


def test_a_mention_emits_its_own_event_and_roundtrips(env):
    _write(env["projects"], "-home-zach-workspace-devrc", "sess-M", [
        user_typed("do a thing"),
        assistant_text("landed as civitai/talos-infra#1065", ts="2026-07-11T10:02:00.000Z"),
    ])
    assert S.run() == 0
    assert len(_summaries(env["spool"])) == 1, "the session summary must still ship"
    (ev,) = _mentions(env["spool"])
    assert ev["kind"] == "mention-detected"
    assert ev["text"] == "civitai/talos-infra#1065"
    assert ev["session"] == "sess-M"
    assert ev["project"] == "devrc"
    assert ev["app"] == "claude-code"
    # The mention's OWN instant, not the session start (10:00).
    assert ev["ts"] == "2026-07-11 10:02:00.000"
    payload = json.loads(ev["payload"])
    assert payload["platform"] == "github"
    assert payload["reference_id"] == "1065"
    assert payload["url"] == "https://github.com/civitai/talos-infra/issues/1065"
    assert payload["candidates"] == "github"
    assert "civitai/talos-infra#1065" in payload["context"]


def test_a_bare_reference_is_emitted_as_ambiguous_with_no_url(env):
    """🔴 An ambiguous span must NOT be recorded as a resolved one. Emitting a
    clawgate row AND a github row for one `#370` would put a reference in the
    dataset that was never made."""
    _write(env["projects"], "-home-zach-workspace-devrc", "sess-A", [
        user_typed("go"), assistant_text("fixed in #370"),
    ])
    assert S.run() == 0
    (ev,) = _mentions(env["spool"])
    payload = json.loads(ev["payload"])
    assert payload["platform"] == "ambiguous"
    assert payload["url"] == ""
    assert payload["candidates"] == "clawgate,github"


def test_a_clickup_id_is_emitted(env):
    _write(env["projects"], "-home-zach-workspace-devrc", "sess-C", [
        user_typed("go"), assistant_text("ticket 868abc123 is done"),
    ])
    assert S.run() == 0
    (ev,) = _mentions(env["spool"])
    payload = json.loads(ev["payload"])
    assert payload["platform"] == "clickup"
    assert payload["url"] == "https://app.clickup.com/t/868abc123"


def test_a_session_with_no_mentions_emits_only_its_summary(env):
    _write(env["projects"], "-home-zach-workspace-devrc", "sess-N", [
        user_typed("go"), assistant_text("all done, nothing to reference"),
    ])
    assert S.run() == 0
    assert len(_summaries(env["spool"])) == 1
    assert _mentions(env["spool"]) == []


def test_a_mention_is_NOT_reemitted_when_the_session_resummarises(env):
    """🔴 THE AMPLIFICATION GUARD. A session re-emits its rollup up to ~7 times
    a day (first-seen / interim / settled). Without the per-session ledger every
    mention would ride along on each of those — the exact row-storm this file's
    header documents for session-summary itself."""
    p = _write(env["projects"], "-home-zach-workspace-devrc", "s1", [
        user_typed("go"), assistant_text("see #370"),
    ])
    t0 = time.time()
    assert S.run(now=t0) == 0
    assert len(_mentions(env["spool"])) == 1

    # Grow it, let it settle, and let the interim window elapse so the summary
    # genuinely re-emits.
    _append(p, user_typed("more", ts="2026-07-11T11:00:00.000Z"),
            mtime=t0 + SETTLE + 1)
    later = t0 + SETTLE + INTERIM + 10
    assert S.run(now=later) == 0
    assert len(_summaries(env["spool"])) == 2, "the summary must have re-emitted"
    assert len(_mentions(env["spool"])) == 1, "the mention must NOT have re-emitted"


def test_a_NEW_mention_in_a_resumed_session_IS_emitted(env):
    """The ledger must suppress repeats without suppressing new material."""
    p = _write(env["projects"], "-home-zach-workspace-devrc", "s1", [
        user_typed("go"), assistant_text("see #370"),
    ])
    t0 = time.time()
    assert S.run(now=t0) == 0
    _append(p, assistant_text("and also 868abc123", ts="2026-07-11T11:00:00.000Z"),
            mtime=t0 + SETTLE + 1)
    later = t0 + SETTLE + INTERIM + 10
    assert S.run(now=later) == 0
    ids = sorted(json.loads(e["payload"])["reference_id"]
                 for e in _mentions(env["spool"]))
    assert ids == ["370", "868abc123"]


def test_the_ledger_survives_a_restart_through_the_state_file(env):
    p = _write(env["projects"], "-home-zach-workspace-devrc", "s1", [
        user_typed("go"), assistant_text("see #370"),
    ])
    assert S.run() == 0
    assert S.load_state(env["state"])[str(p)]["mentions"] == ["ambiguous:#370"]


# --------------------------------------------------------------------------- #
# 🔴 THE DEDUPE IDENTITY INCLUDES THE ATTRIBUTION
#
# Keying on `platform:raw` alone discards the very thing this stream was widened
# to compute: 92% of mentions are a bare `#N`, so the raw text is `#1291` for all
# of them, and the FIRST occurrence in a session wins. Measured on real
# transcripts before the fix: 34 rows/day shipped `repo=""` although an
# attribution was available, and 6 rows/day dropped a second repository outright.
# --------------------------------------------------------------------------- #
def test_two_repositories_referencing_the_SAME_number_are_two_mentions():
    """🔴 THE DROPPED-REFERENCE HALF. `mention_key`'s own docstring already
    argued this class one level down for `devrc#370` vs `talos-infra#370`; the
    bare form is the same collision with the repo one token to the left instead
    of glued to the `#`."""
    got = S.collect_mentions([
        assistant_text("trowelcast PR #1291 is green"),
        assistant_text("plotwidget PR #1291 is not"),
    ], repos=FAKE_REPOS)
    assert [m["repo"] for m in got] == ["gardenersguild/trowelcast",
                                        "hobbyist/plotwidget"], (
        "the dedupe key dropped a second repository's reference")
    assert {m["raw"] for m in got} == {"#1291"}, (
        "positive control: both really are the same raw text, so only the "
        "attribution can be telling them apart")


def test_an_ATTRIBUTED_repeat_of_an_unattributed_ref_is_not_swallowed():
    """🔴 THE LOST-ATTRIBUTION HALF, and the ORDER is the point. The bare form
    usually appears first, so under a `platform:raw` key the row that ships is
    the one carrying NO repo and every later attributed occurrence is dropped —
    a loss biased systematically downward."""
    got = S.collect_mentions([
        assistant_text("still looking at #1291"),
        assistant_text("spadeworks PR #1291 landed"),
    ], repos=FAKE_REPOS)
    assert [m["repo"] for m in got] == ["", "rivalorg/spadeworks"]


def test_the_same_reference_with_the_same_attribution_is_still_ONE_mention():
    """The ledger must not become a no-op: adding the repo to the key widens the
    identity, it does not disable deduplication."""
    got = S.collect_mentions([
        assistant_text("trowelcast PR #1291"),
        assistant_text("trowelcast PR #1291 again"),
    ], repos=FAKE_REPOS)
    assert len(got) == 1, got


def test_an_UNATTRIBUTED_mention_keys_EXACTLY_as_the_deployed_tailer_did():
    """🔴 THE MIGRATION DECISION, PINNED — not a formatting preference.

    Every key in every host's `session-summary-state.json` today was written by a
    tailer whose `collect_mentions` took NO mapping, so all of them have
    `repo == ""`. Keying unconditionally as `platform:repo:raw` would have
    re-keyed the lot and re-emitted every already-shipped mention once on both
    hosts. Suffixing ONLY when there is an attribution keeps those keys byte
    identical, so the re-emit is confined to the rows whose content genuinely
    changed. A mutant that "tidies" this into one unconditional format dies here.

    The absent-key case is asserted too: `collect_mentions` always sets `repo`,
    but `mention_key` is also fed straight from a ledger-shaped dict in `run()`,
    and a missing key must degrade to the old spelling rather than raise."""
    old = "ambiguous:#370"
    assert S.mention_key({"platform": "ambiguous", "raw": "#370", "repo": ""}) == old, (
        "the unattributed key format MOVED — every host's ledger would re-emit")
    assert S.mention_key({"platform": "ambiguous", "raw": "#370"}) == old, (
        "the unattributed key format MOVED — every host's ledger would re-emit")
    assert S.mention_key({"platform": "ambiguous", "raw": "#370",
                          "repo": "rivalorg/spadeworks"}) == (
        "ambiguous:#370@rivalorg/spadeworks")


def test_an_already_emitted_unattributed_mention_does_NOT_reemit_after_the_change(env):
    """The migration claim, end to end rather than as a string assertion: a state
    file written by the OLD code (the literal keys it produced) must still
    suppress the same mention under the new one."""
    p = _write(env["projects"], "-home-zach-workspace-devrc", "s-mig", [
        user_typed("go"), assistant_text("see #370"),
    ])
    t0 = time.time()
    S.save_state(env["state"], {str(p): {"sig": "nope", "emitted_at": None,
                                         "mentions": ["ambiguous:#370"]}})
    assert S.run(now=t0) == 0
    assert _mentions(env["spool"]) == [], (
        "a pre-change ledger entry must still match — this is the whole reason "
        "the suffix is conditional")


def test_a_corrupt_mention_ledger_degrades_instead_of_crashing():
    """A ledger we cannot read must never make a mention permanently
    unemittable, and must never crash the timer."""
    for junk in (None, "not a list", 5, {"a": 1}):
        assert S._mention_keys(junk) == []
    assert S._mention_keys(["a", 2, "b"]) == ["a", "b"]


def test_the_run_summary_reports_the_mention_count(env, capsys):
    _write(env["projects"], "-home-zach-workspace-devrc", "s1", [
        user_typed("go"), assistant_text("see #370 and devrc#5"),
    ])
    assert S.run() == 0
    assert "mentions=2" in _summary_line(capsys)


def test_the_mention_count_is_printed_even_when_zero(env, capsys):
    """A counter that only appears when non-zero is indistinguishable from a
    build that never had it — the same reason `failed` is unconditional."""
    _write(env["projects"], "-home-zach-workspace-devrc", "s1", [user_typed("go")])
    assert S.run() == 0
    assert "mentions=0" in _summary_line(capsys)


def test_an_unreadable_transcript_emits_no_mentions_and_does_not_crash(env):
    d = env["projects"] / "-home-zach-workspace-devrc"
    d.mkdir(parents=True, exist_ok=True)
    (d / "garbage.jsonl").write_text("not json at all\n", encoding="utf-8")
    assert S.run() == 0
    assert _mentions(env["spool"]) == []
    assert len(_summaries(env["spool"])) == 1


# --------------------------------------------------------------------------- #
# 🔴 THE INERT-PREFILTER SEAM
#
# `_MENTION_HINTS` short-circuits BEFORE the regex pass and skipped 81% of
# assistant text blocks in one measured 24h window. Every shape below contains
# NEITHER '#' NOR '868' — the filter's former contents — so under the old value
# the scanner would never have run on them and every module-level test calling
# `scan_mentions()` DIRECTLY would still have passed. These are the only tests in
# the repo that can see that, because they go through `collect_mentions`.
# --------------------------------------------------------------------------- #
# 🔴 EVERY FIXTURE NAME IS SYNTHETIC. This repo is public and the real repo
# mapping names private repositories; nothing measured from this host may appear
# here. Values are pairwise distinct AND distinct from every constant asserted.
FAKE_REPOS = {
    "trowelcast": "gardenersguild/trowelcast",
    "plotwidget": "hobbyist/plotwidget",
    "spadeworks": "rivalorg/spadeworks",
}

# (label, assistant text, expected (platform, id)) — one row per NEW shape.
NEW_SHAPES = [
    ("github pull URL", "merged https://github.com/gardenersguild/trowelcast/pull/7",
     ("github", "7")),
    ("github issues URL", "see https://github.com/hobbyist/plotwidget/issues/4213",
     ("github", "4213")),
    ("slash audit-pr", "next up: /audit-pr 1291", ("github", "1291")),
    ("bare audit-pr", "next up: audit-pr 1291", ("github", "1291")),
    ("gh pr subcommand", "ran gh pr view 1291 to check", ("github", "1291")),
    ("gh issue subcommand", "ran gh issue close 42 after that", ("github", "42")),
    ("clawgate task", "picked up clawgate task 370 today", ("clawgate", "370")),
]


@pytest.mark.parametrize("label,text,expected",
                         NEW_SHAPES, ids=[s[0] for s in NEW_SHAPES])
def test_each_new_shape_REACHES_the_tailer_through_the_prefilter(label, text, expected):
    """🔴 THE REACHABILITY TEST. Adding the regex alone ships a dead feature: the
    prefilter drops the block before `scan_mention_spans` is ever called."""
    got = S.collect_mentions([assistant_text(text)])
    assert [(m["platform"], m["id"]) for m in got] == [expected], label


@pytest.mark.parametrize("label,text,_expected",
                         NEW_SHAPES, ids=[s[0] for s in NEW_SHAPES])
def test_every_new_shape_would_have_been_INVISIBLE_to_the_old_prefilter(
        label, text, _expected):
    """🔴 THE CONTROL FOR THE TEST ABOVE. If a shape happened to contain a '#' or
    an '868' anyway, its reachability test would pass with a prefilter that was
    never widened — green for the wrong reason, proving nothing. This asserts
    each fixture really is invisible to the OLD value, so the test above can only
    pass because the filter moved."""
    assert not any(h in text for h in ("#", "868")), (
        f"{label}: this fixture is not a witness to the widening")


def test_the_prefilter_is_DERIVED_from_the_scanners_telemetry_ledger():
    """One rule, one place: a pattern added to the ledger widens the filter in
    the same commit because there is nowhere else to put the fact.

    🔴 AND IT IS THE TELEMETRY PROFILE. `mention_hints()` with no argument
    returns the terminal profile's two literals — the OLD value — which would
    look derived, pass review, and skip every new shape."""
    assert S._MENTION_HINTS == MS.mention_hints(MS.PROFILE_TELEMETRY)
    assert S._MENTION_HINTS != MS.mention_hints(MS.PROFILE_TERMINAL)


def test_the_prefilter_still_SKIPS_a_block_with_no_hint_at_all():
    """The widening must not become "scan everything" — the short-circuit is the
    reason this rides in the tailer instead of a per-tool-call hook."""
    assert S.collect_mentions([assistant_text("nothing to see in this sentence")]) == []


def test_the_tailer_scans_with_the_WIDER_profile_not_the_default():
    """🔴 The second half of the same seam. A correct prefilter plus a default
    (terminal) profile is also a dead feature, and the prefilter test above
    cannot see it."""
    got = S.collect_mentions([assistant_text("ran gh pr view 1291")])
    assert [m["id"] for m in got] == ["1291"]


# --------------------------------------------------------------------------- #
# Attribution, end to end through the tailer
# --------------------------------------------------------------------------- #
def test_an_adjacent_repo_token_is_ATTRIBUTED_through_collect_mentions():
    """A.2 — the primary defect. 92% of mentions in one measured 24h window were
    a bare `#N`, and the repo name two words to its left was thrown away."""
    (m,) = S.collect_mentions([assistant_text("trowelcast PR #1291 is green")],
                              repos=FAKE_REPOS)
    assert m["repo"] == "gardenersguild/trowelcast"
    assert m["repo_source"] == "adjacent"


def test_attribution_names_the_repo_ACTUALLY_written():
    """Three distinct owners, three distinct expectations — a mutant returning a
    single hardcoded literal dies on two of the three."""
    for token, expected in (("trowelcast", "gardenersguild/trowelcast"),
                            ("plotwidget", "hobbyist/plotwidget"),
                            ("spadeworks", "rivalorg/spadeworks")):
        (m,) = S.collect_mentions([assistant_text(f"{token} PR #7")],
                                  repos=FAKE_REPOS)
        assert m["repo"] == expected, token


def test_a_repo_token_ABSENT_from_the_mapping_stays_unattributed():
    """🔴 THE NO-GUESSING RULE at the tailer's end of the pipe."""
    (m,) = S.collect_mentions([assistant_text("zzzunknown PR #1291")],
                              repos=FAKE_REPOS)
    assert m["repo"] == ""
    assert m["repo_source"] == ""


def test_collect_mentions_without_a_mapping_attributes_nothing_and_still_scans():
    (m,) = S.collect_mentions([assistant_text("trowelcast PR #1291")])
    assert m["id"] == "1291"
    assert m["repo"] == ""


def test_a_url_in_the_same_block_attributes_a_bare_ref_through_the_tailer():
    """A.3, reachable only because `github.com/` is now a prefilter hint."""
    got = S.collect_mentions([assistant_text(
        "https://github.com/rivalorg/spadeworks/pull/8 then also #1291")])
    bare = [m for m in got if m["id"] == "1291"]
    assert bare and bare[0]["repo"] == "rivalorg/spadeworks"
    assert bare[0]["repo_source"] == "url"


def test_a_repo_flag_in_the_same_block_attributes_through_the_tailer():
    """A.4 — the measured `gh pr <sub> N --repo owner/repo` case."""
    (m,) = S.collect_mentions([assistant_text(
        "gh pr view 1291 --repo hobbyist/plotwidget")])
    assert m["repo"] == "hobbyist/plotwidget"
    assert m["repo_source"] == "flag"


def test_the_emitted_event_CARRIES_the_attribution(env):
    """🔴 A FIELD THAT EXISTS IN A DICT IS NOT A COLUMN. The attribution is only
    worth anything if it survives `build_mention_emit_args` and the spool
    round-trip — asserting it on `collect_mentions`' return value alone would
    pin a value nothing ever ships."""
    p = env["repos_path"]
    p.write_text(json.dumps(FAKE_REPOS), encoding="utf-8")
    _write(env["projects"], "-home-zach-workspace-devrc", "sess-ATTR", [
        user_typed("go"),
        assistant_text("spadeworks PR #1291 is green",
                       ts="2026-07-11T10:02:00.000Z"),
    ])
    assert S.run() == 0
    (ev,) = _mentions(env["spool"])
    payload = json.loads(ev["payload"])
    assert payload["repo"] == "rivalorg/spadeworks"
    assert payload["repo_source"] == "adjacent"


def test_an_unattributed_mention_still_emits_with_EMPTY_attribution(env):
    _write(env["projects"], "-home-zach-workspace-devrc", "sess-NOATTR", [
        user_typed("go"), assistant_text("fixed in #370"),
    ])
    assert S.run() == 0
    (ev,) = _mentions(env["spool"])
    payload = json.loads(ev["payload"])
    assert payload["repo"] == ""
    assert payload["repo_source"] == ""


def test_a_new_shape_reaches_the_SPOOL_not_only_collect_mentions(env):
    """The whole path: transcript -> prefilter -> scanner -> emit -> spool."""
    _write(env["projects"], "-home-zach-workspace-devrc", "sess-SHAPE", [
        user_typed("go"), assistant_text("picked up clawgate task 370"),
    ])
    assert S.run() == 0
    (ev,) = _mentions(env["spool"])
    payload = json.loads(ev["payload"])
    assert payload["platform"] == "clawgate"
    assert payload["reference_id"] == "370"
    assert payload["url"] == "https://clawgate.zacx.dev/tasks/370"


# --------------------------------------------------------------------------- #
# The repo mapping the tailer loads
# --------------------------------------------------------------------------- #
def test_the_repo_mapping_path_is_resolved_at_CALL_time(tmp_path, monkeypatch):
    """🔴 A module CONSTANT is evaluated at import, so `monkeypatch.setenv` would
    be inert and every test above would silently read the OPERATOR'S REAL
    mapping — which names private repositories. `scripts/mention-open.py` records
    this exact defect twice; this is the guard that stops it recurring here."""
    p = tmp_path / "m.json"
    p.write_text(json.dumps({"plotwidget": "hobbyist/plotwidget"}), encoding="utf-8")
    monkeypatch.setenv("MENTION_OPEN_KNOWN_REPOS", str(p))
    assert S.mention_repos_path() == p
    assert S.load_mention_repos() == {"plotwidget": "hobbyist/plotwidget"}


@pytest.mark.parametrize("body", [
    "not json", "[]", '"a string"', '{"widget": "acme/widget/"}',
    '{"widget": 12}', '{"widget": "acme"}',
])
def test_an_unusable_mapping_costs_ATTRIBUTION_not_the_telemetry_pass(
        tmp_path, body):
    """Every failure is {} — this runs inside a timer-driven collector, and a
    mapping it cannot read must never stop mentions being emitted."""
    p = tmp_path / "m.json"
    p.write_text(body, encoding="utf-8")
    assert S.load_mention_repos(p) == {}


def test_an_absent_mapping_is_an_empty_mapping(tmp_path):
    assert S.load_mention_repos(tmp_path / "nope.json") == {}


# --------------------------------------------------------------------------- #
# 🔴 THE DISCLOSURE GUARD FOR THE TELEMETRY HALF
#
# `scripts/mention-open.py` sends the operator's known-repo mapping to a rofi
# window that closes, and carries a guard saying so. THIS reader sends it into a
# durable ClickHouse table and, until now, carried none — the asymmetry is
# backwards. `known_repos.json` is the file whose committed ancestor disclosed
# 232 private repository names into this PUBLIC repo (#1283).
#
# THE RULE: exactly ONE value from the mapping may leave here per mention — the
# repository that mention was ATTRIBUTED to. Never the mapping, never a second
# entry, never a count of it.
# --------------------------------------------------------------------------- #
def _everything_the_run_wrote(env, capsys) -> str:
    """Every sink a run can write to, decoded. The spool is read RAW as well as
    parsed, because a leak that is base64-encoded and one that is not are the
    same disclosure and only one of the two readings can see each."""
    cur = env["spool"] / "current.log"
    raw = cur.read_text(encoding="utf-8") if cur.exists() else ""
    decoded = json.dumps(_spool_events(env["spool"]), ensure_ascii=False)
    captured = capsys.readouterr()
    return "\n".join([raw, decoded, captured.out, captured.err])


def test_the_repo_mapping_never_reaches_the_SPOOL_beyond_the_ONE_repo_a_mention_was_attributed_to(
        env, capsys):
    """🔴 POSITIVE CONTROL FIRST. The attributed repository MUST be present —
    otherwise every absence below is satisfied by a run that emitted nothing, and
    the guard is the silent zero it exists to prevent."""
    env["repos_path"].write_text(json.dumps(FAKE_REPOS), encoding="utf-8")
    _write(env["projects"], "-home-zach-workspace-devrc", "sess-DISCLOSE", [
        user_typed("go"),
        assistant_text("trowelcast PR #1291 is green", ts="2026-07-11T10:02:00.000Z"),
    ])
    assert S.run() == 0
    everywhere = _everything_the_run_wrote(env, capsys)
    # POSITIVE CONTROL 1 — a mention really shipped, carrying its attribution.
    assert "gardenersguild/trowelcast" in everywhere
    # POSITIVE CONTROL 2 — the mapping the run held really had the other two in
    # it, so their absence is a decision and not an empty fixture.
    assert S.load_mention_repos(env["repos_path"]) == FAKE_REPOS
    for name in ("hobbyist/plotwidget", "rivalorg/spadeworks",
                 "plotwidget", "spadeworks"):
        assert name not in everywhere, (
            f"SPOOL DISCLOSURE (attributed run): {name!r} is in the operator's "
            "mapping and was NOT the repository this mention was attributed "
            "to — it must not reach the spool")


def test_an_UNATTRIBUTED_mention_ships_NO_repository_name_at_all(env, capsys):
    """The harder direction: with nothing to attribute, a run that consulted the
    mapping must leave no trace of ANY entry in it. A leak on the unattributed
    path has no legitimate value to hide behind, so the absence is total."""
    env["repos_path"].write_text(json.dumps(FAKE_REPOS), encoding="utf-8")
    _write(env["projects"], "-home-zach-workspace-devrc", "sess-BARE", [
        user_typed("go"), assistant_text("fixed in #370"),
    ])
    assert S.run() == 0
    everywhere = _everything_the_run_wrote(env, capsys)
    # POSITIVE CONTROL — the run DID emit a mention and DID load the mapping.
    assert "370" in everywhere
    assert S.load_mention_repos(env["repos_path"]) == FAKE_REPOS
    for name in FAKE_REPOS:
        assert name not in everywhere, f"SPOOL DISCLOSURE (unattributed run): {name}"
    for full in FAKE_REPOS.values():
        assert full not in everywhere, f"SPOOL DISCLOSURE (unattributed run): {full}"


def test_summarize_transcript_still_answers_from_the_shared_reader(tmp_path):
    """`load_transcript` was split out so ONE read serves both the rollup and the
    mention scan. The old entry point must be unchanged for its own callers."""
    p = tmp_path / "s.jsonl"
    p.write_text(json.dumps(assistant_text("#370")) + "\n", encoding="utf-8")
    assert S.load_transcript(str(p)) is not None
    assert S.summarize_transcript(str(p))["unreadable"] is False
    assert S.load_transcript(str(tmp_path / "missing.jsonl")) is None
