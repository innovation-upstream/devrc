#!/usr/bin/env python3
"""Writer 1 of the agent activity ledger — Claude Code, local, in tmux.

Records `{window_id, tmux_pid, session_id, transcript_path, last_activity_ts}` for
the tmux window this turn is running in, so `session-manager` can put an AGE, a
`stale` bucket and a `claude_session_id` back on its default rows. It replaces the
one thing `scripts/tmux-task-hook.sh` (`fuzzyclaw hook stop`) actually contributed,
scoped to the three fields that matter and owned in this repo.

Spec: `claudedocs/spec-agent-activity-ledger.md` (#428). Record shape, write, prune
and the read protocol all live in `agent_ledger.py`; this file is the Claude Code
adapter and nothing more.

WHICH EVENTS, AND WHY EACH ONE
------------------------------
    SessionStart      a window's first record, so a fresh agent has an age
                      immediately rather than after its first turn ends
    UserPromptSubmit  the operator just typed — unambiguous activity
    PostToolUse       🔴 the load-bearing one, THROTTLED. `classify_status` lets
                      `stale` WIN over `busy`, so a turn that grinds past the 1h
                      threshold would render `stale` while it is demonstrably
                      working if the only heartbeats were turn boundaries. Tool
                      calls are the heartbeat that makes `stale` mean stale.
    Stop              the turn ended; the age starts running from here

🔴 FAIL-OPEN, ALWAYS, AND SILENTLY. A hook that raises interrupts the operator's
turn, and nothing here is worth that: the worst case of a failed write is one row
rendering `unknown` instead of `idle`. Every path exits 0 and prints nothing —
this hook has no opinion to inject, it only records.

Registered by `register-nudge-hook.py`; deployed by `nix/home.nix` alongside
`agent_ledger.py`, which it imports as a SIBLING (Python puts the script's own
directory on `sys.path`, the same arrangement `bash-guard.py` has with
`guard_core.py`).
"""
import json
import os
import subprocess
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))


def _load_ledger():
    """Import `agent_ledger` from the deployed sibling, else the repo's lib/.

    Deployed, both files sit in `~/.claude/hooks/`. In the repo the module lives in
    `scripts/lib/` and only this file is under `scripts/claude-hooks/`, so the tests
    exercise the second branch and the host exercises the first.
    """
    import importlib.machinery
    import importlib.util
    for path in (os.path.join(_HERE, "agent_ledger.py"),
                 os.path.join(_HERE, os.pardir, "lib", "agent_ledger.py")):
        if os.path.exists(path):
            loader = importlib.machinery.SourceFileLoader("_agent_ledger", path)
            spec = importlib.util.spec_from_file_location(
                "_agent_ledger", path, loader=loader)
            mod = importlib.util.module_from_spec(spec)
            loader.exec_module(mod)
            return mod
    raise ImportError("agent_ledger.py not found beside the hook or in ../lib/")


# Events that write, and whether the write is throttled. A missing key means the
# event is ignored: registration is per-host mutable state, so the hook refuses
# events it does not own rather than trusting settings.json to be right.
THROTTLED_EVENTS = {"PostToolUse"}
WRITE_EVENTS = {"SessionStart", "UserPromptSubmit", "PostToolUse", "Stop"}
# Pruning walks the directory, so it runs on the turn boundaries (a handful of
# times per session), never on the per-tool-call path.
PRUNE_EVENTS = {"SessionStart", "Stop"}


def tmux_context(runner=None, pane=None):
    """`(window_id, tmux_pid)` for this pane, or `(None, None)`.

    ONE tmux call for both fields: they come from the same server and asking twice
    invites a skew between them for no benefit. Outside tmux — a Claude run in a
    bare terminal, or a subagent — there is no pane and both are None, which the
    record carries as "does not apply".
    """
    pane = os.environ.get("TMUX_PANE") if pane is None else pane
    if not pane:
        return None, None
    argv = ["tmux", "display-message", "-t", pane, "-p", "#{window_id}|#{pid}"]
    try:
        run = runner or (lambda a: subprocess.run(
            a, capture_output=True, text=True, timeout=2.0))
        proc = run(argv)
        if proc.returncode != 0:
            return None, None
        parts = (proc.stdout or "").strip().split("|")
    except Exception:  # noqa: BLE001 — no tmux, dead server, timeout: all "no pane"
        return None, None
    if len(parts) < 2:
        return None, None
    wid, pid = parts[0].strip(), parts[1].strip()
    if not wid.startswith("@") or not pid.isdigit():
        return None, None
    return wid, pid


def record_from_payload(AL, payload, window_id=None, pane_id=None,
                        tmux_pid=None, now=None):
    """Payload -> record, or None when this event does not write one.

    Returns None rather than raising for an unhandled event; a payload that IS ours
    but carries no `session_id` raises out of `build_record`, because a record with
    no session id cannot resolve the ClickHouse join and writing it would restore
    the exact #419 symptom under a green ledger.
    """
    if (payload or {}).get("hook_event_name") not in WRITE_EVENTS:
        return None
    return AL.build_record(
        runtime="claude",
        session_id=payload.get("session_id"),
        last_activity_ts=AL.now_iso(now),
        window_id=window_id,
        pane_id=pane_id,
        tmux_pid=tmux_pid,
        host=(os.environ.get("ACTIVITY_HOST") or "").strip() or None,
        transcript_path=payload.get("transcript_path"),
    )


def selftest(AL) -> int:
    """Positive control: write a record to a throwaway dir, read it back through
    the REAL read command, and print the pair of counts.

    🔴 This is the check the spec (§8) requires and the one #419 lacked. A reader
    that returns 0 ages is indistinguishable from a reader wired to nothing, so
    before believing a live zero, run this and watch a non-zero come back through
    the same `parse_ledger` the live path uses. It never touches the real ledger.

    It drives `AL.read_argv` itself — the command that SHIPS — pointed at a
    throwaway directory. A hand-written lookalike here would prove the lookalike
    works and leave the real read path uncontrolled.
    """
    import shutil
    import tempfile
    tmp = tempfile.mkdtemp(prefix="agent-ledger-selftest-")
    try:
        rec = AL.build_record("claude", "selftest-session",
                              AL.now_iso(), window_id="@999", tmux_pid="1")
        wrote = AL.write_record(rec, directory=tmp)
        proc = subprocess.run(list(AL.read_argv(abs_dir=tmp)),
                              capture_output=True, text=True, timeout=5.0)
        parsed = AL.parse_ledger(proc.stdout)
        ok = (wrote["written"] and parsed["measured"]
              and len(parsed["records"]) == 1
              and parsed["records"][0]["session_id"] == "selftest-session")
        print("write: %s  measured: %s  records: %s  unparseable: %s"
              % (wrote["written"], parsed["measured"],
                 len(parsed["records"]), parsed["unparseable"]))
        print("positive control: 1 expected, %d observed -> %s"
              % (len(parsed["records"]), "PASS" if ok else "FAIL"))
        return 0 if ok else 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> int:
    try:
        AL = _load_ledger()
    except Exception:  # noqa: BLE001
        return 0
    if "--selftest" in sys.argv[1:]:
        try:
            return selftest(AL)
        except Exception as e:  # noqa: BLE001 — the selftest MAY report failure
            print("selftest error: %s: %s" % (type(e).__name__, e))
            return 1
    try:
        payload = json.load(sys.stdin)
    except Exception:  # noqa: BLE001
        return 0
    try:
        event = (payload or {}).get("hook_event_name")
        if event not in WRITE_EVENTS:
            return 0
        pane = os.environ.get("TMUX_PANE") or None
        throttle = AL.DEFAULT_THROTTLE if event in THROTTLED_EVENTS else None
        # 🔴 THE THROTTLE IS CONSULTED BEFORE `tmux` IS SPAWNED, and the ordering
        # is the whole point on the hot path. `PostToolUse` fires after EVERY
        # tool call of every session, and most of those are inside the throttle
        # interval — so the common case must not pay for a subprocess. The pane
        # comes free from `$TMUX_PANE`, and the file is keyed on the pane, so the
        # record's path (and therefore the throttle decision) is known without
        # asking tmux anything. Only a write that is actually going to happen
        # pays for the window id and the server pid. Measured before this
        # ordering: 23.2 ms per call in tmux against 8.6 ms for a bare
        # interpreter start, i.e. the tmux call dominated and ran every time.
        if throttle and pane:
            path = os.path.join(
                AL.LEDGER_DIR, AL.filename_for("claude", pane_id=pane))
            if AL.is_throttled(path, payload.get("session_id"), throttle):
                return 0
        wid, pid = tmux_context(pane=pane if pane else "")
        rec = record_from_payload(AL, payload, window_id=wid, pane_id=pane,
                                  tmux_pid=pid)
        if rec is None:
            return 0
        # Still passed: the early check above is an optimisation, not the
        # authority. Outside tmux there is no pane to key on, and `write_record`
        # is the one place the rule is enforced for every writer.
        AL.write_record(rec, throttle_secs=throttle)
        if event in PRUNE_EVENTS:
            AL.prune()
    except Exception:  # noqa: BLE001 — see the fail-open note in the module docstring
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
