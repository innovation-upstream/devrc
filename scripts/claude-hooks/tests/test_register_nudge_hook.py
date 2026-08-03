#!/usr/bin/env python3
"""Tests for register-nudge-hook.py — the per-host settings.json registrant.

Runs the script against a temp HOME whose ~/.claude/settings.json already holds
unrelated hooks (clawgate PermissionRequest/Stop, bash-guard, the PostToolUse
nudges), and asserts:

  1. It appends claude-notify on the 3 turn-boundary events, append-only.
  2. It NEVER clobbers pre-existing hooks (the clawgate Stop hook survives).
  3. It is idempotent (a 2nd run makes no change, no duplicates).
  4. The final settings.json is valid JSON.
  5. The write is ATOMIC (uses os.replace) and leaves no .settings.*.tmp litter.

Run: python3 test_register_nudge_hook.py
"""
import os, sys, json, tempfile, subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "..", "register-nudge-hook.py")

NOTIFY = "python3 ~/.claude/hooks/claude-notify.py"
CLAWGATE_STOP = "/home/zach/.claude/clawgate-stop-hook.sh"
SEARCH_NUDGE = "python3 ~/.claude/hooks/search-tool-nudge.py"

failures = []


def check(name, cond):
    print(("PASS" if cond else "FAIL") + " - " + name)
    if not cond:
        failures.append(name)


BASE_SETTINGS = {
    "hooks": {
        "PermissionRequest": [
            {"hooks": [{"type": "command", "command": "CLAUDE_HOST=wb /home/zach/.claude/clawgate-hook.sh", "timeout": 180}]}
        ],
        "PreToolUse": [
            {"matcher": "Bash", "hooks": [{"type": "command", "command": "python3 ~/.claude/hooks/bash-guard.py"}]}
        ],
        "PostToolUse": [
            {"matcher": "Bash", "hooks": [{"type": "command", "command": "python3 ~/.claude/hooks/audit-pr-nudge.py"}]},
            {"matcher": "Bash", "hooks": [{"type": "command", "command": "python3 ~/.claude/hooks/shell-env-nudge.py"}]},
        ],
        "Stop": [
            {"hooks": [{"type": "command", "command": CLAWGATE_STOP}]}
        ],
    }
}


def run(env):
    return subprocess.run([sys.executable, SCRIPT], env=env, capture_output=True, text=True)


def cmds(data, event):
    return [h.get("command") for e in data.get("hooks", {}).get(event, []) for h in e.get("hooks", [])]


with tempfile.TemporaryDirectory() as tmp:
    home = os.path.join(tmp, "home")
    os.makedirs(os.path.join(home, ".claude"))
    settings = os.path.join(home, ".claude", "settings.json")
    with open(settings, "w") as f:
        json.dump(BASE_SETTINGS, f, indent=2)

    env = dict(os.environ)
    env["HOME"] = home

    p1 = run(env)
    check("1: first run exit 0", p1.returncode == 0)

    with open(settings) as f:
        d = json.load(f)
    check("1: valid JSON after write", isinstance(d, dict))
    for ev in ("UserPromptSubmit", "Stop", "SubagentStop"):
        check("1: claude-notify registered on " + ev, NOTIFY in cmds(d, ev))
    check("2: pre-existing clawgate Stop hook preserved", CLAWGATE_STOP in cmds(d, "Stop"))
    check("2: bash-guard preserved", "python3 ~/.claude/hooks/bash-guard.py" in cmds(d, "PreToolUse"))
    check("2: both PostToolUse nudges preserved",
          "python3 ~/.claude/hooks/audit-pr-nudge.py" in cmds(d, "PostToolUse")
          and "python3 ~/.claude/hooks/shell-env-nudge.py" in cmds(d, "PostToolUse"))
    # search-tool-nudge is absent from BASE_SETTINGS, so this also proves the registrant
    # ADDS a missing PostToolUse nudge rather than only preserving existing ones.
    check("1: search-tool-nudge registered on PostToolUse",
          SEARCH_NUDGE in cmds(d, "PostToolUse"))

    # Idempotency: a second run changes nothing and never duplicates.
    p2 = run(env)
    check("3: second run exit 0", p2.returncode == 0)
    check("3: second run reports no change", "no change" in p2.stdout)
    with open(settings) as f:
        d2 = json.load(f)
    check("3: no duplicate claude-notify on Stop", cmds(d2, "Stop").count(NOTIFY) == 1)
    check("3: no duplicate search-tool-nudge", cmds(d2, "PostToolUse").count(SEARCH_NUDGE) == 1)
    check("3: clawgate Stop still single + present", cmds(d2, "Stop").count(CLAWGATE_STOP) == 1)

    # Atomicity: no temp litter left in the dir, and the source uses os.replace.
    leftovers = [n for n in os.listdir(os.path.dirname(settings)) if n.startswith(".settings.")]
    check("5: no leftover temp files after atomic write", leftovers == [])

with open(SCRIPT) as f:
    src = f.read()
check("5: script uses os.replace for atomic write", "os.replace(" in src)

print()
if failures:
    print("%d FAILURE(S): %s" % (len(failures), ", ".join(failures)))
    sys.exit(1)
print("all register-nudge-hook tests passed")
