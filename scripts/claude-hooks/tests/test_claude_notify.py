#!/usr/bin/env python3
"""Tests for claude-notify.py — the turn-finished notifier hook.

Drives the hook by piping mock Claude Code hook JSON on stdin, with a temp HOME
(so real ~/.cache and ~/.claude are untouched) and PATH stubs for dunstify /
notify-send / curl that append their invocation to a stub-log. We assert:

  1. UserPromptSubmit writes a per-session start file.
  2. Stop with elapsed >= threshold + a desktop present -> desktop toast only
     (clawgate is a FALLBACK, must NOT fire when the desktop toast worked),
     start file removed, exit 0.
  2b. Stop with elapsed >= threshold + NO desktop (headless) -> clawgate push
     fires (and no desktop toast).
  3. Stop with elapsed <  threshold -> NO notification, exit 0.
  4. Stop with stop_hook_active=true -> immediate exit 0, no notification.
  5. Stop with no start file -> exit 0, no notification, no crash.

Run: python3 test_claude_notify.py
"""
import os
import sys
import json
import time
import tempfile
import subprocess

# The hook lives one dir up (scripts/claude-hooks/); this test is in tests/.
# Locate it relative to this file so the suite runs both from the devrc repo and
# wherever it's colocated. (Mirrors test_shell_env_nudge.py's "../<hook>.py".)
HOOK = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "claude-notify.py")


def make_env(tmp):
    """A temp HOME with stub dunstify/notify-send/curl on PATH, a fake DISPLAY,
    and a fake clawgate.env so the clawgate push path is exercised."""
    home = os.path.join(tmp, "home")
    bindir = os.path.join(tmp, "bin")
    os.makedirs(bindir)
    os.makedirs(os.path.join(home, ".claude"))
    stub_log = os.path.join(tmp, "stub.log")

    for name in ("dunstify", "notify-send", "curl"):
        p = os.path.join(bindir, name)
        with open(p, "w") as f:
            # /bin/sh (not /usr/bin/env bash) so the stub also execs in the nix
            # build sandbox, which has no /usr/bin/env; the body is POSIX-sh.
            f.write('#!/bin/sh\n'
                    'echo "%s $*" >> "%s"\nexit 0\n' % (name, stub_log))
        os.chmod(p, 0o755)

    # Fake clawgate config so notify_clawgate() has a url+token to POST to.
    with open(os.path.join(home, ".claude", "clawgate.env"), "w") as f:
        f.write("CLAWGATE_API_URL=http://127.0.0.1:1/stub\n")
        f.write("CLAWGATE_HOOK_TOKEN=stub-token\n")

    env = dict(os.environ)
    env["HOME"] = home
    env["PATH"] = bindir + os.pathsep + env.get("PATH", "")
    env["DISPLAY"] = ":99"
    env.pop("CLAUDE_NOTIFY", None)
    env.pop("CLAUDE_NOTIFY_MIN_SECONDS", None)
    env.pop("CLAWGATE_API_URL", None)
    env.pop("CLAWGATE_HOOK_TOKEN", None)
    return home, stub_log, env


def run(env, payload, extra=None):
    e = dict(env)
    if extra:
        e.update(extra)
    p = subprocess.run([sys.executable, HOOK], input=json.dumps(payload),
                       text=True, capture_output=True, env=e)
    return p


def start_file(home, sid):
    return os.path.join(home, ".cache", "claude-notify", sid + ".start")


def lastnotify_file(home, sid):
    return os.path.join(home, ".cache", "claude-notify", sid + ".lastnotify")


def count(hay, needle):
    return hay.count(needle)


def read_stub(stub_log):
    if not os.path.isfile(stub_log):
        return ""
    with open(stub_log) as f:
        return f.read()


failures = []


def check(name, cond):
    print(("PASS" if cond else "FAIL") + " - " + name)
    if not cond:
        failures.append(name)


# --- Test 1: UserPromptSubmit writes a start file ---------------------------
with tempfile.TemporaryDirectory() as tmp:
    home, stub_log, env = make_env(tmp)
    sid = "sess-1"
    p = run(env, {"hook_event_name": "UserPromptSubmit", "session_id": sid,
                  "cwd": "/home/zach/workspace/civit/example"})
    check("1: UserPromptSubmit exit 0", p.returncode == 0)
    check("1: start file written", os.path.isfile(start_file(home, sid)))
    check("1: no notification on prompt submit", read_stub(stub_log) == "")

# --- Test 2: Stop, elapsed >= threshold -> notify, start file removed -------
with tempfile.TemporaryDirectory() as tmp:
    home, stub_log, env = make_env(tmp)
    sid = "sess-2"
    sf = start_file(home, sid)
    os.makedirs(os.path.dirname(sf))
    with open(sf, "w") as f:               # started 120s ago; default threshold 60s
        f.write(str(time.time() - 120))
    p = run(env, {"hook_event_name": "Stop", "session_id": sid,
                  "cwd": "/home/zach/workspace/civit/example", "stop_hook_active": False})
    log = read_stub(stub_log)
    check("2: Stop exit 0", p.returncode == 0)
    check("2: desktop notify fired", "dunstify" in log)
    check("2: clawgate NOT fired when desktop toast worked (fallback-only)",
          "curl" not in log)
    check("2: elapsed rendered as minutes", "2m" in log)
    check("2: start file removed after Stop", not os.path.isfile(sf))

# --- Test 2b: Stop, no desktop (headless) -> clawgate push is the fallback ---
with tempfile.TemporaryDirectory() as tmp:
    home, stub_log, env = make_env(tmp)
    env.pop("DISPLAY", None)               # headless: no local desktop toast
    env.pop("WAYLAND_DISPLAY", None)
    sid = "sess-2b"
    sf = start_file(home, sid)
    os.makedirs(os.path.dirname(sf))
    with open(sf, "w") as f:               # started 120s ago
        f.write(str(time.time() - 120))
    p = run(env, {"hook_event_name": "Stop", "session_id": sid,
                  "cwd": "/home/zach/workspace/civit/example", "stop_hook_active": False})
    log = read_stub(stub_log)
    check("2b: Stop exit 0", p.returncode == 0)
    check("2b: clawgate push fired when headless", "curl" in log and "/api/notify" in log)
    check("2b: no desktop toast when headless", "dunstify" not in log)
    check("2b: start file removed after Stop", not os.path.isfile(sf))

# --- Test 3: Stop, elapsed < threshold -> no notification -------------------
with tempfile.TemporaryDirectory() as tmp:
    home, stub_log, env = make_env(tmp)
    sid = "sess-3"
    sf = start_file(home, sid)
    os.makedirs(os.path.dirname(sf))
    with open(sf, "w") as f:               # started 5s ago; below 60s threshold
        f.write(str(time.time() - 5))
    p = run(env, {"hook_event_name": "Stop", "session_id": sid,
                  "cwd": "/tmp/x", "stop_hook_active": False})
    check("3: Stop exit 0", p.returncode == 0)
    check("3: no notification below threshold", read_stub(stub_log) == "")
    check("3: start file still consumed", not os.path.isfile(sf))

# --- Test 4: Stop with stop_hook_active=true -> immediate no-op --------------
with tempfile.TemporaryDirectory() as tmp:
    home, stub_log, env = make_env(tmp)
    sid = "sess-4"
    sf = start_file(home, sid)
    os.makedirs(os.path.dirname(sf))
    with open(sf, "w") as f:
        f.write(str(time.time() - 300))    # very old; would notify if not gated
    p = run(env, {"hook_event_name": "Stop", "session_id": sid,
                  "cwd": "/tmp/x", "stop_hook_active": True})
    check("4: Stop(active) exit 0", p.returncode == 0)
    check("4: no notification when stop_hook_active", read_stub(stub_log) == "")
    check("4: start file untouched when active", os.path.isfile(sf))

# --- Test 5: Stop with no start file -> exit 0, no notify, no crash ----------
with tempfile.TemporaryDirectory() as tmp:
    home, stub_log, env = make_env(tmp)
    p = run(env, {"hook_event_name": "Stop", "session_id": "sess-missing",
                  "cwd": "/tmp/x", "stop_hook_active": False})
    check("5: Stop(no start file) exit 0", p.returncode == 0)
    check("5: no notification without start file", read_stub(stub_log) == "")
    check("5: no traceback on stderr", "Traceback" not in p.stderr)

# --- Bonus: SubagentStop notifies but leaves start file for parent Stop ------
with tempfile.TemporaryDirectory() as tmp:
    home, stub_log, env = make_env(tmp)
    sid = "sess-6"
    sf = start_file(home, sid)
    os.makedirs(os.path.dirname(sf))
    with open(sf, "w") as f:
        f.write(str(time.time() - 120))
    p = run(env, {"hook_event_name": "SubagentStop", "session_id": sid,
                  "cwd": "/home/zach/workspace/civit/example", "stop_hook_active": False})
    log = read_stub(stub_log)
    check("6: SubagentStop exit 0", p.returncode == 0)
    check("6: SubagentStop notifies", "dunstify" in log and "subagent finished" in log)
    check("6: SubagentStop keeps start file for parent Stop", os.path.isfile(sf))
    check("6: SubagentStop writes lastnotify marker", os.path.isfile(lastnotify_file(home, sid)))

# --- Test 7: burst of SubagentStops collapses to ONE ping (cooldown) ---------
with tempfile.TemporaryDirectory() as tmp:
    home, stub_log, env = make_env(tmp)
    sid = "sess-7"
    sf = start_file(home, sid)
    os.makedirs(os.path.dirname(sf))
    with open(sf, "w") as f:                # parent turn started 120s ago (> threshold)
        f.write(str(time.time() - 120))
    # Two SubagentStops in quick succession (default cooldown = threshold = 60s).
    p1 = run(env, {"hook_event_name": "SubagentStop", "session_id": sid,
                   "cwd": "/home/zach/workspace/civit/example", "stop_hook_active": False})
    p2 = run(env, {"hook_event_name": "SubagentStop", "session_id": sid,
                   "cwd": "/home/zach/workspace/civit/example", "stop_hook_active": False})
    log = read_stub(stub_log)
    check("7: both SubagentStops exit 0", p1.returncode == 0 and p2.returncode == 0)
    check("7: burst collapses to ONE notification (not two)", count(log, "dunstify") == 1)
    check("7: start file still present after subagents", os.path.isfile(sf))

# --- Test 8: a Stop within the cooldown after a SubagentStop does NOT re-notify
with tempfile.TemporaryDirectory() as tmp:
    home, stub_log, env = make_env(tmp)
    sid = "sess-8"
    sf = start_file(home, sid)
    os.makedirs(os.path.dirname(sf))
    with open(sf, "w") as f:
        f.write(str(time.time() - 120))
    p1 = run(env, {"hook_event_name": "SubagentStop", "session_id": sid,
                   "cwd": "/home/zach/workspace/civit/example", "stop_hook_active": False})
    p2 = run(env, {"hook_event_name": "Stop", "session_id": sid,
                   "cwd": "/home/zach/workspace/civit/example", "stop_hook_active": False})
    log = read_stub(stub_log)
    check("8: SubagentStop then Stop -> exactly ONE notification", count(log, "dunstify") == 1)
    check("8: Stop still consumes the start file", not os.path.isfile(sf))
    check("8: Stop cleans up the lastnotify marker", not os.path.isfile(lastnotify_file(home, sid)))

# --- Test 8b: cooldown is PER-SESSION (session A's marker doesn't gate B) -----
with tempfile.TemporaryDirectory() as tmp:
    home, stub_log, env = make_env(tmp)
    cache = os.path.join(home, ".cache", "claude-notify")
    os.makedirs(cache)
    # Session A just notified (recent marker); session B has its own long turn.
    with open(lastnotify_file(home, "sess-A"), "w") as f:
        f.write(str(time.time()))
    sfb = start_file(home, "sess-B")
    with open(sfb, "w") as f:
        f.write(str(time.time() - 120))
    p = run(env, {"hook_event_name": "Stop", "session_id": "sess-B",
                  "cwd": "/home/zach/workspace/civit/example", "stop_hook_active": False})
    log = read_stub(stub_log)
    check("8b: session B notifies despite session A's recent marker (per-session)",
          count(log, "dunstify") == 1)

# --- Test 9: a Stop AFTER the cooldown has elapsed DOES notify ---------------
with tempfile.TemporaryDirectory() as tmp:
    home, stub_log, env = make_env(tmp)
    sid = "sess-9"
    cache = os.path.join(home, ".cache", "claude-notify")
    os.makedirs(cache)
    sf = start_file(home, sid)
    with open(sf, "w") as f:
        f.write(str(time.time() - 200))
    # Prior notification was 120s ago — older than the 60s cooldown, so re-notify.
    with open(lastnotify_file(home, sid), "w") as f:
        f.write(str(time.time() - 120))
    p = run(env, {"hook_event_name": "Stop", "session_id": sid,
                  "cwd": "/home/zach/workspace/civit/example", "stop_hook_active": False})
    log = read_stub(stub_log)
    check("9: Stop after cooldown elapsed DOES notify", count(log, "dunstify") == 1)
    check("9: Stop consumes start file + lastnotify", not os.path.isfile(sf)
          and not os.path.isfile(lastnotify_file(home, sid)))

print()
if failures:
    print("%d FAILURE(S): %s" % (len(failures), ", ".join(failures)))
    sys.exit(1)
print("all claude-notify tests passed")
