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
import time
from pathlib import Path

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
# `scripts/` on sys.path so `testlib.mockbin` — the ONE definition of "write an
# executable stub" in this repo — is importable from a tests dir two levels down.
sys.path.insert(0, os.path.abspath(os.path.join(HERE, os.pardir, os.pardir)))
from testlib import mockbin  # noqa: E402
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


def _fake_tmux(bindir, answer, log=None):
    """A stub `tmux` on PATH that answers `display-message` and records that it
    was called.

    🔴 The real binary is deliberately NOT used: these tests assert WHETHER tmux
    was spawned and how often, which the real one cannot report, and a live tmux
    would make the result depend on the operator's own session.

    🔴 Via `testlib.mockbin.write_exec`, which owns the shebang. A hand-written
    `#!/usr/bin/env bash` is DEAD in the nix build sandbox — six sites in this
    repo re-derived that lesson before `mockbin` existed, and
    `scripts/tests/test_runtime_shebangs.py` now fails the gate for a seventh.
    It caught this file.
    """
    body = ""
    if log is not None:
        body += 'echo "$@" >> %s\n' % json.dumps(str(log))
    body += "printf '%s\\n'\n" % answer
    return str(mockbin.write_exec(Path(str(bindir)) / "tmux", body))


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


def test_the_hook_keeps_NO_COPY_of_the_tmux_resolver():
    """🔴 It moved into `agent_ledger.py` when the opencode `--write` CLI became
    its second caller. A window/pid resolver copied into each writer is the
    duplicated predicate that ends up wrong at one of its sites — so the hook
    must have none of its own, and this fails if one grows back."""
    assert not hasattr(hook, "tmux_context")
    assert callable(AL.tmux_context)


def test_tmux_context_parses_the_window_and_the_SERVER_pid():
    """ONE tmux call for both fields — asking twice invites a skew between them
    for no benefit. KILLS: reading the pane pid instead of the server pid, and
    KILLS: swapping the two fields (they are distinguishable here because the
    fixture's values cannot be confused for one another)."""
    seen = []

    def runner(argv):
        seen.append(argv)
        return FakeProc(0, "@41|4025325\n")

    assert AL.tmux_context(runner=runner, pane="%11") == ("@41", "4025325")
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
    assert AL.tmux_context(runner=lambda a: FakeProc(rc, out),
                             pane="%11") == (None, None)


def test_no_TMUX_PANE_means_no_tmux_call_at_all():
    """A Claude run in a bare terminal has no pane. KILLS: shelling out to tmux
    anyway, which costs a subprocess on every tool call of every non-tmux run."""
    called = []
    assert AL.tmux_context(runner=lambda a: called.append(a),
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


def test_a_second_PostToolUse_inside_the_interval_does_not_MOVE_the_timestamp(
        tmp_path):
    """🔴 THIS TEST USED TO BE VACUOUS, and an audit caught it. It asserted "two
    calls leave ONE record" — which one-file-per-key guarantees on its own, with
    the throttle deleted, so the mutant `throttle_secs=None` at
    `agent-ledger-hook.py:main` survived the whole suite. The declaration
    (`THROTTLED_EVENTS`) was asserted; the INSTANCE, the argument actually passed
    at the call site, was not. A count of declarations is not a count of
    instances.

    The throttle's real observable is that the record's `last_activity_ts` does
    NOT advance. Two calls a second apart: with the throttle the stamp is
    unchanged, without it the second write moves it.
    """
    fake = tmp_path / "bin"
    fake.mkdir()
    _fake_tmux(fake, "@41|4025325")
    env = {"PATH": "%s:%s" % (fake, os.environ["PATH"]), "TMUX_PANE": "%11"}

    assert run_hook(payload(event="PostToolUse"), tmp_path, env=env).returncode == 0
    first = read_back(tmp_path)["records"][0]["last_activity_ts"]
    time.sleep(1.1)                      # a real second, so the stamp COULD move
    assert run_hook(payload(event="PostToolUse"), tmp_path, env=env).returncode == 0
    records = read_back(tmp_path)["records"]

    assert len(records) == 1
    assert records[0]["last_activity_ts"] == first, (
        "the second write was not throttled — the stamp advanced")


def test_the_POSITIVE_CONTROL_a_non_throttled_event_DOES_move_the_timestamp(
        tmp_path):
    """🔴 Without this, the test above passes on a hook that never writes at all.
    `Stop` is not in THROTTLED_EVENTS, so its second call must move the stamp —
    same fixture, same pane, opposite outcome."""
    fake = tmp_path / "bin"
    fake.mkdir()
    _fake_tmux(fake, "@41|4025325")
    env = {"PATH": "%s:%s" % (fake, os.environ["PATH"]), "TMUX_PANE": "%11"}

    run_hook(payload(event="Stop"), tmp_path, env=env)
    first = read_back(tmp_path)["records"][0]["last_activity_ts"]
    time.sleep(1.1)
    run_hook(payload(event="Stop"), tmp_path, env=env)
    assert read_back(tmp_path)["records"][0]["last_activity_ts"] != first


def test_the_throttle_still_applies_with_NO_PANE_where_only_the_ARGUMENT_holds(
        tmp_path):
    """🔴 THE PATH THAT LEFT THE CALL SITE UNPINNED. Everything above runs with
    `TMUX_PANE` set, so it exercises the EARLY check in `main()` — and a mutant
    that changes only the argument passed to `write_record`
    (`throttle_secs=None`) survives all of it. A delta re-audit found exactly
    that mutant still alive after the first fix round.

    Outside tmux there is no pane, so the early check cannot run and the
    call-site argument is the ONLY thing throttling the write. Consequence at
    the consuming site: a Claude run in a bare terminal would write on every
    single tool call — precisely the cost `DEFAULT_THROTTLE` exists to prevent.

    KILLS: `AL.write_record(rec, throttle_secs=None)`.
    """
    assert run_hook(payload(event="PostToolUse"), tmp_path).returncode == 0
    first = read_back(tmp_path)["records"][0]["last_activity_ts"]
    time.sleep(1.1)
    assert run_hook(payload(event="PostToolUse"), tmp_path).returncode == 0
    records = read_back(tmp_path)["records"]

    assert len(records) == 1
    assert records[0]["pane_id"] is None, "this path must have no pane"
    assert records[0]["last_activity_ts"] == first, (
        "with no pane the early check cannot run, so the throttle argument "
        "passed to write_record is the only thing suppressing this write")


def test_the_POSITIVE_CONTROL_no_pane_and_a_NON_throttled_event_does_write(
        tmp_path):
    """Without this the test above passes on a hook that stopped writing at all
    on the no-pane path. Same fixture, `Stop` instead, opposite outcome."""
    run_hook(payload(event="Stop"), tmp_path)
    first = read_back(tmp_path)["records"][0]["last_activity_ts"]
    time.sleep(1.1)
    run_hook(payload(event="Stop"), tmp_path)
    assert read_back(tmp_path)["records"][0]["last_activity_ts"] != first


def test_a_FAILED_tmux_lookup_does_not_destroy_the_joinable_record(tmp_path):
    """🔴 The regression this round's pane keying introduced, at the PROCESS
    level. A stub tmux that exits non-zero leaves `$TMUX_PANE` set and the window
    unresolved; the record must not land on top of the good pane-keyed one.
    """
    fake = tmp_path / "bin"
    fake.mkdir()
    _fake_tmux(fake, "@41|4025325")
    env = {"PATH": "%s:%s" % (fake, os.environ["PATH"]), "TMUX_PANE": "%11"}
    run_hook(payload(event="Stop", session_id="sess-good"), tmp_path, env=env)
    assert os.path.exists(os.path.join(ledger_dir(tmp_path),
                                       "claude-p11.json"))

    # now tmux fails, and the SAME pane's next turn must not clobber it
    mockbin.write_exec(fake / "tmux", "exit 1\n")
    run_hook(payload(event="Stop", session_id="sess-degraded"), tmp_path,
             env=env)
    names = sorted(os.listdir(ledger_dir(tmp_path)))
    assert "claude-p11.json" in names
    with open(os.path.join(ledger_dir(tmp_path), "claude-p11.json")) as fh:
        kept = json.loads(fh.read())
    assert kept["window_id"] == "@41" and kept["session_id"] == "sess-good"


def test_a_THROTTLED_call_never_spawns_tmux(tmp_path):
    """🔴 The ordering that makes `PostToolUse` affordable. It fires after every
    tool call of every session, and most are inside the interval — so the common
    case must not pay for a subprocess. The pane comes free from `$TMUX_PANE` and
    the file is keyed on it, so the throttle decision needs nothing from tmux.

    KILLS: moving the `tmux_context()` call back above the throttle check.
    Measured before the reorder: 23.2 ms per in-tmux call against 8.6 ms for a
    bare interpreter start — the tmux call dominated, and ran every time.
    """
    fake = tmp_path / "bin"
    fake.mkdir()
    log = tmp_path / "tmux-calls.log"
    _fake_tmux(fake, "@41|4025325", log=log)
    env = {"PATH": "%s:%s" % (fake, os.environ["PATH"]), "TMUX_PANE": "%11"}

    run_hook(payload(event="PostToolUse"), tmp_path, env=env)
    assert log.read_text().count("\n") == 1, "the first write must ask tmux"
    run_hook(payload(event="PostToolUse"), tmp_path, env=env)
    assert log.read_text().count("\n") == 1, (
        "a throttled call spawned tmux anyway")


def test_prune_actually_RUNS_on_a_turn_boundary_and_not_per_tool_call(tmp_path):
    """🔴 KILLS: deleting the `AL.prune()` call. `PRUNE_EVENTS` was asserted as a
    SET and never exercised, so dropping the call survived the suite — and prune
    is the only defence against the rot the module cites as its own motivation
    (fuzzyclaw: 401 files, ~90% stale).

    Both directions: `Stop` reaps the ancient record, `PostToolUse` leaves it
    (walking the directory after every tool call is the cost this avoids).
    """
    d = ledger_dir(tmp_path)
    os.makedirs(d, exist_ok=True)
    ancient = os.path.join(d, "claude-p999.json")

    def plant():
        with open(ancient, "w") as fh:
            fh.write(AL.encode_record(AL.build_record(
                "claude", "long-gone",
                AL.now_iso(time.time() - 30 * 86400), pane_id="%999")))

    plant()
    run_hook(payload(event="PostToolUse"), tmp_path)
    assert os.path.exists(ancient), "PostToolUse must not walk the directory"

    run_hook(payload(event="Stop"), tmp_path)
    assert not os.path.exists(ancient), "Stop did not prune"


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
