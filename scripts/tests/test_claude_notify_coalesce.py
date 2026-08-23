"""Tests for claude-notify's CROSS-SESSION coalescing gate — the DND fix.

WHY THIS FILE IS HERE AND NOT NEXT TO THE HOOK: `scripts/claude-hooks/tests/` is
deliberately NOT a pytest target (run-tests.sh names one file in it, because the
directory also holds hand-rolled scripts that call main() and sys.exit() at
import). A coalescing test written beside `test_claude_notify.py` would never be
collected by either tier — a test the gate never runs. `scripts/tests` IS
collected as a directory, so these run.

WHAT THE CHANGE IS. The per-session cooldown bounds ONE session's ping rate and
does nothing to the aggregate; with N concurrent sessions the desktop sees N
streams (measured: 1914 desktop toasts / 11 days on the workbench, peak 386/day).
A cross-session gate now allows one desktop toast per window and folds every
suppressed turn into the next toast's "+N other turns finished" line.

WHAT THESE TESTS PIN, and why each is not enough alone:

  * THE POLICY, as a pure function — emit/hold either side of the window, and
    the `held` accounting. Cheap and exhaustive, but a pure function can be
    perfect while nothing calls it.
  * THE WIRING, end-to-end through the real hook on a real subprocess with stub
    launchers — a second turn inside the window must produce NO dunstify call,
    and must NOT spill to clawgate instead (moving the noise to the phone would
    satisfy every "fewer toasts" assertion while making things worse).
  * CONSERVATION — suppressed turns are counted and NAMED in the next toast.
    This is what makes the reduction information-preserving rather than a
    suppression, so it is asserted on the toast's actual argv, not on internals.
  * FAIL-OPEN — a corrupt/unwritable state file must still toast. This system
    has already produced two silent-delivery failures (a paused queue and a
    filterless fullscreen_suppress); a broken gate must degrade to NOISY, never
    to silent.
  * THE REVERT PATH — window 0 must be an exact no-op, because that is the knob
    the operator turns if this change is wrong.
  * THE REPLAY TOOL'S OWN CONTROLS — the projection in the PR body is only worth
    the counter that produced it, and two counters built for adjacent work in
    this repo were themselves dead.
"""
import os
import sys
import json
import time
import importlib.util
import subprocess
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # scripts/
from testlib import mockbin  # noqa: E402

_HERE = os.path.dirname(os.path.abspath(__file__))
_HOOK = os.path.join(_HERE, "..", "claude-hooks", "claude-notify.py")
_REPLAY = os.path.join(_HERE, "..", "notify-volume-replay.py")


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


cn = _load(_HOOK, "claude_notify_under_test")


# --------------------------------------------------------------------------
# THE POLICY (pure function)
# --------------------------------------------------------------------------

def test_first_ever_call_emits():
    """Empty state must emit — a fresh cache must not swallow the first toast."""
    emit, state, (held, projs) = cn.coalesce_decide({}, now=1000.0, project="a", window=600)
    assert emit is True
    assert held == 0 and projs == []
    assert state["last_emit"] == 1000.0


def test_default_window_is_the_one_that_was_measured():
    """A window can be wrong in BOTH directions and this pins both.

    Too small and the change does nothing; too large and it becomes a
    suppression wearing a coalescer's clothes — at 6h the "+N" line would be the
    only surface left and DND would have been replaced by a slower DND. 600s is
    what the replay was run at: workbench 174/day -> 40/day, laptop 70/day ->
    29/day, with 4 turns left pending across 11 days.

    Every other test in this file passes an explicit window, so without this one
    the default is unpinned and a one-character edit to it ships unobserved.
    """
    assert cn.DEFAULT_GLOBAL_COOLDOWN == 600, (
        "the default window is the projection's only load-bearing constant; "
        "changing it invalidates the measured 174->40/day figure. got %r"
        % cn.DEFAULT_GLOBAL_COOLDOWN)


def test_default_window_is_used_when_the_env_var_is_absent(monkeypatch):
    """Pinning the constant is not enough — global_cooldown() must READ it. A
    constant nothing branches on is not a code path."""
    monkeypatch.delenv("CLAUDE_NOTIFY_GLOBAL_COOLDOWN_SECONDS", raising=False)
    assert cn.global_cooldown() == float(cn.DEFAULT_GLOBAL_COOLDOWN)


def test_unparseable_window_env_falls_back_to_the_default(monkeypatch):
    """A typo in the unit file must not disable the gate silently."""
    monkeypatch.setenv("CLAUDE_NOTIFY_GLOBAL_COOLDOWN_SECONDS", "ten minutes")
    assert cn.global_cooldown() == float(cn.DEFAULT_GLOBAL_COOLDOWN)


def test_second_call_inside_window_is_held():
    _, state, _ = cn.coalesce_decide({}, now=1000.0, project="a", window=600)
    emit, state, _ = cn.coalesce_decide(state, now=1000.0 + 599, project="b", window=600)
    assert emit is False, "a turn 599s after the last toast must be held (window 600)"
    assert state["held"] == 1
    assert state["pending"] == ["b"]


def test_call_exactly_at_the_window_boundary_emits():
    """Boundary is >=, not >. An off-by-one here silently doubles the window for
    a stream whose gaps land exactly on it."""
    _, state, _ = cn.coalesce_decide({}, now=1000.0, project="a", window=600)
    emit, _, _ = cn.coalesce_decide(state, now=1000.0 + 600, project="b", window=600)
    assert emit is True


def test_held_turns_are_reported_and_named_on_the_next_emit():
    """The information-preserving claim, asserted on the values that reach the
    toast: nothing is dropped, it is deferred and summarised."""
    # A realistic epoch: 0.0 is the NEVER-EMITTED sentinel, so seeding with it
    # would make every following call emit and the test would pass vacuously.
    t0 = 1_700_000_000.0
    _, state, _ = cn.coalesce_decide({}, now=t0, project="first", window=600)
    for i, proj in enumerate(["alpha", "beta", "alpha"]):
        emit, state, _ = cn.coalesce_decide(state, now=t0 + 10.0 * (i + 1), project=proj,
                                            window=600)
        assert emit is False
    emit, state, (held, projs) = cn.coalesce_decide(state, now=t0 + 1000.0,
                                                   project="last", window=600)
    assert emit is True
    assert held == 3, "all three held turns must be counted"
    assert projs == ["alpha", "beta"], "distinct, in first-seen order"
    assert state["held"] == 0 and state["pending"] == [], "state resets on emit"


def test_window_zero_disables_the_gate_entirely():
    """The revert path. Every call must emit and nothing may accumulate.

    Seeded from a realistic epoch: with now=0 the `not last` never-emitted
    sentinel would carry this test on its own and it would pass with the
    window check deleted entirely.
    """
    t0 = 1_700_000_000.0
    state = {}
    for i in range(20):
        emit, state, (held, _) = cn.coalesce_decide(state, now=t0 + i, project="p",
                                                    window=0)
        assert emit is True and held == 0
    assert state["pending"] == []


def test_window_zero_still_emits_when_the_clock_steps_backwards():
    """`window <= 0` must be checked as a DISABLE, not left to arithmetic.

    A mutation run showed `window <= 0` -> `window < 0` surviving the test above,
    because `(now - last) >= 0` happens to be true for every forward-moving
    clock. The two differ only when the clock steps BACK (NTP correction,
    suspend/resume, a VM snapshot) — and there the arithmetic form wedges the
    gate shut with the operator's declared revert knob set. That is the exact
    silent-delivery shape this change is not allowed to add, so it is pinned.
    """
    t0 = 1_700_000_000.0
    _, state, _ = cn.coalesce_decide({}, now=t0, project="a", window=0)
    emit, _, _ = cn.coalesce_decide(state, now=t0 - 3600, project="b", window=0)
    assert emit is True, "window=0 must emit even if the clock went backwards"


def test_held_count_stays_truthful_past_the_name_cap():
    """`held` counts every turn even once the NAME list is capped — a toast that
    says '+50' while 300 were held would understate what it is standing in for."""
    t0 = 1_700_000_000.0
    state = {}
    _, state, _ = cn.coalesce_decide(state, now=t0, project="p0", window=600)
    n = cn.PENDING_CAP + 25
    for i in range(n):
        _, state, _ = cn.coalesce_decide(state, now=t0 + 1.0 + i, project="p%d" % i,
                                         window=600)
    assert state["held"] == n
    assert len(state["pending"]) == cn.PENDING_CAP
    _, _, (held, projs) = cn.coalesce_decide(state, now=t0 + 100000.0, project="z",
                                             window=600)
    assert held == n
    assert len(projs) == cn.PENDING_CAP
    # WHICH names survive the cap, not merely how many. A mutation run found
    # that keeping the OLDEST names instead of the newest survived the suite:
    # the length assertion above is identical either way, so the toast could
    # have named 200 projects from hours ago and passed.
    assert projs[-1] == "p%d" % (n - 1), (
        "the cap must retain the most RECENT names; got %r..%r" % (projs[0], projs[-1]))
    assert "p0" not in projs, "the oldest names must be the ones evicted"


@pytest.mark.parametrize("corrupt", [
    {"last_emit": "not-a-number"},
    {"last_emit": None, "pending": "not-a-list"},
    {"held": "seven"},
    {"pending": [1, 2, 3]},
])
def test_corrupt_state_fields_fail_open(corrupt):
    """A malformed state file must never wedge the gate shut."""
    emit, _, _ = cn.coalesce_decide(corrupt, now=time.time(), project="p", window=600)
    assert emit is True


# --------------------------------------------------------------------------
# THE SUFFIX (what the user actually reads)
# --------------------------------------------------------------------------

def test_suffix_is_empty_when_nothing_was_held():
    """An uncoalesced toast must be byte-for-byte what it was before this
    feature existed — no '+0 other turns' noise on every single toast."""
    assert cn.coalesce_suffix(0, []) == ""


def test_suffix_names_the_projects_and_counts_them():
    s = cn.coalesce_suffix(3, ["alpha", "beta"])
    assert "+3 other turns finished" in s
    assert "alpha" in s and "beta" in s


def test_suffix_singular_for_one():
    assert "+1 other turn finished" in cn.coalesce_suffix(1, ["solo"])


def test_suffix_truncates_long_project_lists_but_keeps_the_count():
    s = cn.coalesce_suffix(20, ["p%d" % i for i in range(20)])
    assert "+20 other turns finished" in s
    assert "…" in s, "truncation must be visible, not silent"


# --------------------------------------------------------------------------
# THE WIRING (end-to-end through the real hook)
# --------------------------------------------------------------------------

def _env(tmp_path):
    """A temp HOME with stub dunstify/notify-send/curl that log their argv.

    The stubs live in tmp_path, which the suite conftest guarantees precedes its
    own session-wide stub dir on PATH, so these win.
    """
    home = tmp_path / "home"
    bindir = tmp_path / "bin"
    (home / ".claude").mkdir(parents=True)
    bindir.mkdir()
    stub_log = tmp_path / "stub.log"
    for name in ("dunstify", "notify-send", "curl"):
        # One invocation == one line. Embedded newlines in an argument (the toast
        # BODY is multi-line once a coalesce suffix is appended) are folded to
        # spaces, otherwise the suffix lands on a line the assertions never read
        # and "the toast does not mention it" becomes indistinguishable from
        # "the stub truncated it".
        #
        # mockbin.write_exec owns the shebang: a hand-written `#!/usr/bin/env sh`
        # execs on this NixOS host and ENOENTs in the nix build sandbox, which is
        # the authoritative tier. (This file's first revision did exactly that
        # and only the sandbox caught it.)
        mockbin.write_exec(
            bindir / name,
            'line="%s"\n'
            'for a in "$@"; do line="$line [$a]"; done\n'
            'printf \'%%s\\n\' "$line" | tr \'\\n\' \' \' >> "%s"\n'
            'printf \'\\n\' >> "%s"\n'
            'exit 0\n' % (name, stub_log, stub_log))
    (home / ".claude" / "clawgate.env").write_text(
        "CLAWGATE_API_URL=http://127.0.0.1:1/stub\nCLAWGATE_HOOK_TOKEN=stub-token\n")
    env = dict(os.environ)
    env["HOME"] = str(home)
    env["PATH"] = str(bindir) + os.pathsep + env.get("PATH", "")
    env["DISPLAY"] = ":99"
    for k in ("CLAUDE_NOTIFY", "CLAUDE_NOTIFY_MIN_SECONDS",
              "CLAUDE_NOTIFY_COOLDOWN_SECONDS",
              "CLAUDE_NOTIFY_GLOBAL_COOLDOWN_SECONDS",
              "CLAWGATE_API_URL", "CLAWGATE_HOOK_TOKEN"):
        env.pop(k, None)
    return home, stub_log, env


def _finish_turn(env, home, session, ran_seconds=120):
    """Drive one complete turn: seed a start file `ran_seconds` in the past, then
    deliver Stop. Returns the hook's exit code."""
    cache = home / ".cache" / "claude-notify"
    cache.mkdir(parents=True, exist_ok=True)
    (cache / (session + ".start")).write_text(str(time.time() - ran_seconds))
    payload = json.dumps({"hook_event_name": "Stop", "session_id": session,
                          "cwd": "/tmp/proj-" + session})
    p = subprocess.run(["python3", _HOOK], input=payload, text=True,
                       env=env, capture_output=True, timeout=30)
    return p.returncode


def _lines(stub_log):
    if not stub_log.exists():
        return []
    return [l for l in stub_log.read_text().splitlines() if l.strip()]


def test_end_to_end_first_turn_toasts(tmp_path):
    """POSITIVE CONTROL for this whole section: if this does not toast, every
    'no toast' assertion below is vacuous."""
    home, stub_log, env = _env(tmp_path)
    env["CLAUDE_NOTIFY_GLOBAL_COOLDOWN_SECONDS"] = "600"
    assert _finish_turn(env, home, "s1") == 0
    lines = _lines(stub_log)
    assert any(l.startswith("dunstify") for l in lines), \
        "the first turn must produce a desktop toast; got %r" % lines


def test_end_to_end_second_turn_in_window_is_silent_on_both_channels(tmp_path):
    """The core reduction — AND the anti-spillover claim. A held turn must not
    reappear as a phone push; that would move the noise, not remove it."""
    home, stub_log, env = _env(tmp_path)
    env["CLAUDE_NOTIFY_GLOBAL_COOLDOWN_SECONDS"] = "600"
    _finish_turn(env, home, "s1")
    before = len(_lines(stub_log))
    assert _finish_turn(env, home, "s2") == 0
    after = _lines(stub_log)
    assert len(after) == before, \
        "a second turn inside the window must launch NOTHING; new: %r" % after[before:]
    assert not any("curl" in l for l in after[before:]), \
        "a held desktop toast must not spill to the clawgate phone push"


def test_end_to_end_different_sessions_share_one_budget(tmp_path):
    """THE SEAM this change exists for. The per-session cooldown already
    collapses one session; the defect was that N sessions each got their own
    budget. Two DIFFERENT session ids must share the global window — a gate
    keyed per-session would pass every other test in this file."""
    home, stub_log, env = _env(tmp_path)
    env["CLAUDE_NOTIFY_GLOBAL_COOLDOWN_SECONDS"] = "600"
    for sid in ("alpha", "bravo", "charlie", "delta"):
        _finish_turn(env, home, sid)
    toasts = [l for l in _lines(stub_log) if l.startswith("dunstify")]
    assert len(toasts) == 1, \
        "4 distinct sessions inside one window must yield ONE toast, got %d: %r" \
        % (len(toasts), toasts)


def test_end_to_end_held_turns_are_named_in_the_next_toast(tmp_path):
    """Conservation, asserted on the real toast argv."""
    home, stub_log, env = _env(tmp_path)
    env["CLAUDE_NOTIFY_GLOBAL_COOLDOWN_SECONDS"] = "600"
    _finish_turn(env, home, "s1")
    _finish_turn(env, home, "s2")
    _finish_turn(env, home, "s3")
    # Reopen the window by rewinding the recorded last_emit.
    gs = home / ".cache" / "claude-notify" / "global.json"
    st = json.loads(gs.read_text())
    st["last_emit"] = time.time() - 100000
    gs.write_text(json.dumps(st))
    _finish_turn(env, home, "s4")
    toasts = [l for l in _lines(stub_log) if l.startswith("dunstify")]
    assert len(toasts) == 2, "expected an opening toast and a coalesced one: %r" % toasts
    assert "+2 other turns finished" in toasts[-1], \
        "the surviving toast must account for the 2 held turns; got %r" % toasts[-1]
    assert "proj-s2" in toasts[-1] and "proj-s3" in toasts[-1], \
        "held turns must be NAMED, not just counted; got %r" % toasts[-1]


def test_end_to_end_window_zero_restores_old_behaviour(tmp_path):
    """The documented revert knob, exercised end to end."""
    home, stub_log, env = _env(tmp_path)
    env["CLAUDE_NOTIFY_GLOBAL_COOLDOWN_SECONDS"] = "0"
    for sid in ("a", "b", "c"):
        _finish_turn(env, home, sid)
    toasts = [l for l in _lines(stub_log) if l.startswith("dunstify")]
    assert len(toasts) == 3, "window=0 must toast every turn, got %r" % toasts


def test_end_to_end_unwritable_state_fails_open(tmp_path):
    """A gate that cannot persist must toast, not go silent."""
    home, stub_log, env = _env(tmp_path)
    env["CLAUDE_NOTIFY_GLOBAL_COOLDOWN_SECONDS"] = "600"
    cache = home / ".cache" / "claude-notify"
    cache.mkdir(parents=True, exist_ok=True)
    # A directory where the state FILE belongs: open() raises every time.
    (cache / "global.json").mkdir()
    for sid in ("a", "b"):
        assert _finish_turn(env, home, sid) == 0
    toasts = [l for l in _lines(stub_log) if l.startswith("dunstify")]
    assert len(toasts) == 2, \
        "a broken gate must degrade to NOISY (2 toasts), not silent; got %r" % toasts


def test_end_to_end_headless_still_pushes_to_clawgate(tmp_path):
    """The gate is desktop-only. On a headless host the phone fallback must be
    untouched — otherwise this change would quietly reduce the away-from-machine
    signal too, which is not what it was authorised to do."""
    home, stub_log, env = _env(tmp_path)
    env["CLAUDE_NOTIFY_GLOBAL_COOLDOWN_SECONDS"] = "600"
    env.pop("DISPLAY", None)
    env.pop("WAYLAND_DISPLAY", None)
    for sid in ("a", "b", "c"):
        _finish_turn(env, home, sid)
    curls = [l for l in _lines(stub_log) if l.startswith("curl")]
    assert len(curls) == 3, \
        "every headless turn must still reach clawgate; got %r" % curls


def test_end_to_end_short_turns_still_never_notify(tmp_path):
    """The pre-existing threshold must survive the change."""
    home, stub_log, env = _env(tmp_path)
    env["CLAUDE_NOTIFY_GLOBAL_COOLDOWN_SECONDS"] = "600"
    assert _finish_turn(env, home, "s1", ran_seconds=5) == 0
    assert _lines(stub_log) == []


def test_log_records_the_coalesced_count(tmp_path):
    """A coalescer that emits one toast because it CRASHED looks identical to one
    that correctly merged N. The log line is what tells them apart, so the field
    is pinned as a contract, and the crash path is pinned to say so too."""
    home, stub_log, env = _env(tmp_path)
    env["CLAUDE_NOTIFY_GLOBAL_COOLDOWN_SECONDS"] = "600"
    _finish_turn(env, home, "s1")
    _finish_turn(env, home, "s2")
    gs = home / ".cache" / "claude-notify" / "global.json"
    st = json.loads(gs.read_text())
    st["last_emit"] = time.time() - 100000
    gs.write_text(json.dumps(st))
    _finish_turn(env, home, "s3")
    log = (home / ".claude" / "claude-notify.log").read_text()
    assert "coalesced=1" in log, \
        "the emitting toast must record how many turns it merged; got:\n%s" % log
    assert "held for next toast" in log, "the held turn must leave a trace too"
    assert "coalesce gate error" not in log, \
        "this run must NOT have taken the fail-open path"


# --------------------------------------------------------------------------
# THE REPLAY TOOL (the instrument behind the PR's projection)
# --------------------------------------------------------------------------

def test_replay_tool_self_test_passes():
    """The projection in the PR body is worth exactly what this counter is worth.
    Its self-test carries a POSITIVE control (a stream it must NOT reduce), a
    negative control (one it must), a disabled control, and a conservation
    identity. A bare 'the numbers went down' is not evidence."""
    p = subprocess.run(["python3", _REPLAY, "--self-test"],
                       capture_output=True, text=True, timeout=60)
    assert p.returncode == 0, "replay self-test failed:\n%s\n%s" % (p.stdout, p.stderr)
    assert "SELF-TEST: PASS" in p.stdout
    # Read the CONTENT, not just the exit code.
    assert "before=40 after=40" in p.stdout, "positive control did not hold"
    assert "before=40 after=1" in p.stdout, "negative control did not reduce"


def test_replay_tool_drives_the_shipped_policy_not_a_copy():
    """If the replay re-implemented the policy, the PR's projection could be
    exactly right about code that does not exist. Pin that it imports the hook."""
    src = open(_REPLAY).read()
    assert "claude-notify.py" in src
    assert "claude_notify.coalesce_decide" in src, \
        "the replay must call the SHIPPED decide function"


def test_replay_reports_unparsed_lines(tmp_path):
    """A silent parse failure would show as a reassuringly low 'before' count.
    Feed the parser garbage and require it to say so — a positive control on the
    tool's own honesty flag."""
    log = tmp_path / "notify.log"
    log.write_text(
        "[2026-08-01 10:00:00] notify event=Stop project=a elapsed=2m 0s "
        "desktop=True clawgate=False\n"
        "this line is not a notify line at all\n")
    p = subprocess.run(["python3", _REPLAY, "--window", "600", str(log)],
                       capture_output=True, text=True, timeout=60)
    assert p.returncode == 0, p.stderr
    assert "unparsed=1" in p.stdout
    assert "UNDER-count" in p.stdout, "an unparsed line must be flagged, not swallowed"
