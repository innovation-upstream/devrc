#!/usr/bin/env python3
"""Tests for agent-ledger-hook.py — writer 1 of the agent activity ledger.

WHAT THIS FILE IS FOR

  1. 🔴 FAIL-OPEN MEANS SILENT. This hook fires on `PostToolUse`, i.e. after every
     single tool call the operator's session makes. Anything it raises, prints, or
     exits non-zero on is felt on every turn. Every error path is driven through a
     REAL subprocess, not by calling `main()`, because "it exits 0 and says
     nothing" is a claim about the process, not about the function.

  2. THE THROTTLE'S SESSION SCOPE. Suppressing a repeat write from the same
     session is the point; suppressing one from a DIFFERENT session would keep a
     departed session's id winning the join for a full interval. That asymmetry is
     the hook's only real logic, so it gets both directions.

  3. THE END-TO-END POSITIVE CONTROL. A subprocess with `HOME` pointed at a
     throwaway directory writes a record and it is read back through
     `agent_ledger.parse_ledger` — the same function `session-manager` uses. A
     writer test that only inspects the dict it built proves the dict.
"""
import importlib.machinery
import importlib.util
import json
import os
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
HOOK = os.path.abspath(os.path.join(HERE, os.pardir, "agent-ledger-hook.py"))
LEDGER = os.path.abspath(
    os.path.join(HERE, os.pardir, os.pardir, "lib", "agent_ledger.py"))


def _load(name, path):
    loader = importlib.machinery.SourceFileLoader(name, path)
    spec = importlib.util.spec_from_file_location(name, path, loader=loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


hook = _load("agent_ledger_hook_undertest", HOOK)
AL = _load("agent_ledger_forhook", LEDGER)

NOW = 1755000000.0


def payload(event="Stop", session_id="sess-aaaa", **kw):
    base = {"hook_event_name": event, "session_id": session_id,
            "transcript_path": "/home/zach/.claude/projects/p/a.jsonl",
            "cwd": "/home/zach/workspace/devrc"}
    base.update(kw)
    return base


def run_hook(data, home, env=None, args=()):
    """Drive the hook as Claude Code does: JSON on stdin, `HOME` decides where
    the ledger lives (`agent_ledger.LEDGER_DIR` is `$HOME/.cache/agent-ledger`,
    resolved at import), and nothing else is inherited that matters."""
    e = dict(os.environ)
    e["HOME"] = str(home)
    e.pop("TMUX_PANE", None)           # no tmux by default; opt in per test
    e.update(env or {})
    return subprocess.run(
        [sys.executable, HOOK, *args],
        input=json.dumps(data) if data is not None else "",
        capture_output=True, text=True, timeout=30, env=e)


def ledger_dir(home):
    return os.path.join(str(home), ".cache", "agent-ledger")


def read_back(home):
    """Read the written ledger through the SHIPPING read path."""
    proc = subprocess.run(list(AL.read_argv(abs_dir=ledger_dir(home))),
                          capture_output=True, text=True, timeout=10)
    return AL.parse_ledger(proc.stdout)


# =========================================================================== #
# which events write
# =========================================================================== #
def test_the_four_write_events_are_exactly_the_documented_set():
    """🔴 A LEDGER, failing in both directions. Registration lives in a per-host
    mutable `settings.json`, so the hook refuses events it does not own rather
    than trusting that file to be right — and an event silently added here is a
    write path nobody reviewed."""
    assert hook.WRITE_EVENTS == {"SessionStart", "UserPromptSubmit",
                                 "PostToolUse", "Stop"}
    assert hook.THROTTLED_EVENTS == {"PostToolUse"}
    assert hook.PRUNE_EVENTS == {"SessionStart", "Stop"}
    # the throttled and pruned sets are SUBSETS of what writes — an event in
    # neither table would throttle or prune on a path that never writes
    assert hook.THROTTLED_EVENTS <= hook.WRITE_EVENTS
    assert hook.PRUNE_EVENTS <= hook.WRITE_EVENTS


def test_PostToolUse_is_throttled_because_stale_WINS_over_busy():
    """🔴 The reason the tool-call heartbeat exists at all, pinned as a
    relationship rather than as a comment: `classify_status` lets `stale` beat
    `busy`, so with only turn-boundary events a turn grinding past the threshold
    would render `stale` while demonstrably working. KILLS: dropping PostToolUse
    from the write set, which would look harmless and re-introduce that."""
    assert "PostToolUse" in hook.WRITE_EVENTS
    assert AL.DEFAULT_THROTTLE < 3600, (
        "the throttle must stay far inside session-manager's stale threshold, "
        "or the heartbeat it exists to provide would itself go stale")


@pytest.mark.parametrize("event", ["SessionStart", "UserPromptSubmit",
                                   "PostToolUse", "Stop"])
def test_each_write_event_produces_a_record(event, tmp_path):
    assert run_hook(payload(event=event), tmp_path).returncode == 0
    parsed = read_back(tmp_path)
    assert parsed["measured"] is True and len(parsed["records"]) == 1
    assert parsed["records"][0]["session_id"] == "sess-aaaa"


@pytest.mark.parametrize("event", ["SubagentStop", "PreToolUse",
                                   "Notification", "", None])
def test_an_event_this_hook_does_not_own_writes_NOTHING(event, tmp_path):
    """KILLS: writing on any event that reaches stdin. A SubagentStop in
    particular arrives constantly and carries the PARENT's session id — writing
    on it would keep the window looking active while the operator's own turn had
    long since ended."""
    assert run_hook(payload(event=event), tmp_path).returncode == 0
    assert not os.path.exists(ledger_dir(tmp_path))


def test_record_from_payload_returns_None_for_an_unowned_event():
    """The pure half of the guard above, so the refusal is pinned at the
    function and not only at the process."""
    assert hook.record_from_payload(AL, payload(event="SubagentStop")) is None
    assert hook.record_from_payload(AL, {}) is None


def test_record_from_payload_carries_the_window_and_the_transcript():
    r = hook.record_from_payload(AL, payload(), window_id="@41",
                                 tmux_pid="4025325", now=NOW)
    assert r["runtime"] == "claude"
    assert r["session_id"] == "sess-aaaa"
    assert (r["window_id"], r["tmux_pid"]) == ("@41", "4025325")
    assert r["transcript_path"].endswith("/a.jsonl")
    assert r["last_activity_ts"] == AL.now_iso(NOW)


# =========================================================================== #
# the tmux lookup
# =========================================================================== #
class FakeProc:
    def __init__(self, rc, out):
        self.returncode, self.stdout = rc, out


def test_tmux_context_parses_the_window_and_the_SERVER_pid():
    """ONE tmux call for both fields — asking twice invites a skew between them
    for no benefit. KILLS: reading the pane pid instead of the server pid, and
    KILLS: swapping the two fields (they are distinguishable here because the
    fixture's values cannot be confused for one another)."""
    seen = []

    def runner(argv):
        seen.append(argv)
        return FakeProc(0, "@41|4025325\n")

    assert hook.tmux_context(runner=runner, pane="%11") == ("@41", "4025325")
    assert seen[0][:3] == ["tmux", "display-message", "-t"]
    assert "#{window_id}|#{pid}" in seen[0]


@pytest.mark.parametrize("rc,out", [
    (1, ""),                    # no server / bad pane
    (0, ""),                    # answered with nothing
    (0, "@41\n"),               # only one field
    (0, "notawindow|4025325"),  # window id without its sigil
    (0, "@41|notapid"),         # pid that is not a number
])
def test_tmux_context_returns_a_PAIR_OF_NULLS_rather_than_half_an_answer(rc, out):
    """🔴 KILLS: returning a window id with no generation. A record carrying a
    window and no pid is KEPT by the reader as `generation_unchecked` — so half
    an answer here is silently promoted to a trusted join downstream."""
    assert hook.tmux_context(runner=lambda a: FakeProc(rc, out),
                             pane="%11") == (None, None)


def test_no_TMUX_PANE_means_no_tmux_call_at_all():
    """A Claude run in a bare terminal has no pane. KILLS: shelling out to tmux
    anyway, which costs a subprocess on every tool call of every non-tmux run."""
    called = []
    assert hook.tmux_context(runner=lambda a: called.append(a),
                            pane="") == (None, None)
    assert called == []


def test_a_record_written_outside_tmux_has_null_window_and_pid(tmp_path):
    """End-to-end: no `TMUX_PANE`, so the record exists (the run is real
    activity) but is not joinable to any window — `no_window`, which the reader
    counts separately and never attaches to a row."""
    assert run_hook(payload(), tmp_path).returncode == 0
    r = read_back(tmp_path)["records"][0]
    assert r["window_id"] is None and r["tmux_pid"] is None


# =========================================================================== #
# the throttle
# =========================================================================== #
def test_the_throttle_is_scoped_to_the_session_not_to_the_window(tmp_path):
    """🔴 THE ASYMMETRY. Same session inside the interval: suppressed. Different
    session: written immediately, because the window has changed hands and the
    departed session's id must stop winning the join at once.

    Driven through `AL.write_record` directly with an injected clock — the hook's
    own path uses wall time, and a test that slept would be measuring the sleep.
    """
    d = str(tmp_path)
    first = AL.build_record("claude", "sess-aaaa", AL.now_iso(NOW),
                            window_id="@41", tmux_pid="1")
    AL.write_record(first, directory=d, now=NOW)

    same = AL.write_record(
        AL.build_record("claude", "sess-aaaa", AL.now_iso(NOW + 5),
                        window_id="@41", tmux_pid="1"),
        directory=d, throttle_secs=AL.DEFAULT_THROTTLE, now=NOW + 5)
    assert same["written"] is False

    handover = AL.write_record(
        AL.build_record("claude", "sess-bbbb", AL.now_iso(NOW + 6),
                        window_id="@41", tmux_pid="1"),
        directory=d, throttle_secs=AL.DEFAULT_THROTTLE, now=NOW + 6)
    assert handover["written"] is True


def test_two_PostToolUse_calls_in_a_row_leave_ONE_record(tmp_path):
    """The observable consequence of the throttle at the process level: a window
    holds one record however many tool calls run in it."""
    for _ in range(3):
        assert run_hook(payload(event="PostToolUse"), tmp_path).returncode == 0
    assert len(read_back(tmp_path)["records"]) == 1


# =========================================================================== #
# 🔴 fail-open — every path through a REAL subprocess
# =========================================================================== #
@pytest.mark.parametrize("stdin", ["", "not json", "[]", "null",
                                   '{"hook_event_name": 42}'])
def test_malformed_stdin_exits_0_silently(stdin, tmp_path):
    """KILLS: any traceback reaching the operator. This hook runs after every
    tool call; a stderr line here is felt on every turn."""
    e = dict(os.environ)
    e["HOME"] = str(tmp_path)
    proc = subprocess.run([sys.executable, HOOK], input=stdin,
                          capture_output=True, text=True, timeout=30, env=e)
    assert proc.returncode == 0
    assert proc.stdout == "" and proc.stderr == ""


def test_a_payload_with_no_session_id_writes_NOTHING_and_still_exits_0(tmp_path):
    """🔴 A record with no `session_id` is exactly the row the ClickHouse join
    cannot resolve — writing it would restore the #419 symptom underneath a
    ledger reporting records live. It must be refused, and refused SILENTLY."""
    proc = run_hook(payload(session_id=""), tmp_path)
    assert proc.returncode == 0 and proc.stderr == ""
    assert read_back(tmp_path)["records"] == []


def test_an_unwritable_ledger_directory_is_survived(tmp_path):
    """The disk is full, or `~/.cache` is root-owned. The turn must still end."""
    blocker = tmp_path / ".cache"
    blocker.write_text("a FILE where the cache directory should be\n")
    proc = run_hook(payload(), tmp_path)
    assert proc.returncode == 0 and proc.stdout == "" and proc.stderr == ""


def test_the_hook_writes_nothing_to_stdout_on_the_SUCCESS_path(tmp_path):
    """🔴 Stop-hook stdout is parsed by Claude Code as hook output. This hook has
    no opinion to inject — it only records — so anything on stdout would be an
    accidental injection into the operator's session."""
    proc = run_hook(payload(event="Stop"), tmp_path)
    assert proc.stdout == "" and proc.stderr == "" and proc.returncode == 0


# =========================================================================== #
# the selftest — this hook's own positive control
# =========================================================================== #
def test_the_selftest_passes_and_leaves_the_real_ledger_untouched(tmp_path):
    """🔴 `--selftest` is what an operator runs before believing a live zero, so
    it must (a) pass here and (b) never write into the ledger it is validating."""
    proc = run_hook(None, tmp_path, args=("--selftest",))
    assert proc.returncode == 0
    assert "positive control: 1 expected, 1 observed -> PASS" in proc.stdout
    assert not os.path.exists(ledger_dir(tmp_path))


def test_the_selftest_can_FAIL_which_is_what_makes_its_pass_mean_anything():
    """🔴 THE NEGATIVE CONTROL ON THE CONTROL. A selftest that cannot report
    failure is a decoration. Break the writer and watch it come back non-zero.
    """
    mod = _load("agent_ledger_broken", LEDGER)
    mod.write_record = lambda *a, **k: {"written": False, "path": None,
                                        "reason": "error", "error": "broken"}
    assert hook.selftest(mod) == 1
