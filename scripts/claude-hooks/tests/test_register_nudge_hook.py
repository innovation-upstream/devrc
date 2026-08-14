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
NEXT_STEP = "python3 ~/.claude/hooks/next-step-nudge.py"
TMUX_STOP = "~/.config/tmux/task-hook.sh"
LEDGER = "python3 ~/.claude/hooks/agent-ledger-hook.py"

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
        # 🔴 Stop already has TWO foreign owners before this script ever runs: the
        # fuzzyclaw tmux writer and the clawgate stop hook (remote approval). Both are
        # in the fixture so "append-only" is tested against a populated event, not an
        # empty one — an append-only bug is invisible on an empty array.
        "Stop": [
            {"hooks": [{"type": "command", "command": TMUX_STOP}]},
            {"hooks": [{"type": "command", "command": CLAWGATE_STOP}]},
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
    check("2: pre-existing tmux Stop hook preserved", TMUX_STOP in cmds(d, "Stop"))

    # 🔴 ALL FOUR Stop owners coexist. Asserted as a SET EQUALITY, not four membership
    # checks: equality fails when the set GROWS (a fifth hook registered by accident)
    # as well as when it SHRINKS (one clobbered), which membership checks cannot see.
    check("2: exactly the five expected Stop hooks, none clobbered, none extra",
          set(cmds(d, "Stop")) == {TMUX_STOP, CLAWGATE_STOP, NOTIFY, NEXT_STEP, LEDGER})
    check("1: next-step-nudge registered on Stop", NEXT_STEP in cmds(d, "Stop"))
    # Ordering: the two foreign hooks keep their original relative order and stay ahead
    # of everything this script appends. Appending (rather than inserting) is what makes
    # the nudge run last, after the notifiers have already observed the turn. (It no
    # longer BLOCKS — it returns additionalContext and exits 0 — but the turn still
    # continues, so the ordering argument is unchanged.)
    stop = cmds(d, "Stop")
    check("2: foreign Stop hooks keep their original relative order",
          stop.index(TMUX_STOP) < stop.index(CLAWGATE_STOP))
    check("2: appended hooks come after the pre-existing ones",
          max(stop.index(TMUX_STOP), stop.index(CLAWGATE_STOP))
          < min(stop.index(NOTIFY), stop.index(NEXT_STEP)))
    # next-step-nudge is Stop-only: it must NOT have been added to SubagentStop.
    check("1: next-step-nudge NOT registered on SubagentStop",
          NEXT_STEP not in cmds(d, "SubagentStop"))
    check("2: bash-guard preserved", "python3 ~/.claude/hooks/bash-guard.py" in cmds(d, "PreToolUse"))
    check("2: both PostToolUse nudges preserved",
          "python3 ~/.claude/hooks/audit-pr-nudge.py" in cmds(d, "PostToolUse")
          and "python3 ~/.claude/hooks/shell-env-nudge.py" in cmds(d, "PostToolUse"))
    # search-tool-nudge is absent from BASE_SETTINGS, so this also proves the registrant
    # ADDS a missing PostToolUse nudge rather than only preserving existing ones.
    check("1: search-tool-nudge registered on PostToolUse",
          SEARCH_NUDGE in cmds(d, "PostToolUse"))

    # --- the agent activity ledger writer: FOUR events, one of them brand new --
    # 🔴 SessionStart is absent from BASE_SETTINGS entirely, so this also proves
    # the registrant CREATES an event array rather than only appending to one
    # that already exists.
    for ev in ("SessionStart", "UserPromptSubmit", "PostToolUse", "Stop"):
        check("1: agent-ledger registered on " + ev, LEDGER in cmds(d, ev))
    # ...and NOT on SubagentStop: a subagent's tool calls are not the operator
    # window's activity, and writing on them would keep a finished window
    # looking alive.
    check("1: agent-ledger NOT registered on SubagentStop",
          LEDGER not in cmds(d, "SubagentStop"))
    # 🔴 UNMATCHERED on PostToolUse, unlike the three Bash nudges. Scoping it to
    # Bash would stop the heartbeat during a long stretch of Read/Edit work, and
    # `classify_status` lets `stale` win over `busy` — so the window would render
    # stale while demonstrably working. Asserted on the ENTRY, because the
    # command string alone cannot show which matcher it was filed under.
    ledger_entries = [e for e in d["hooks"]["PostToolUse"]
                      if any(h.get("command") == LEDGER
                             for h in e.get("hooks", []))]
    check("1: agent-ledger PostToolUse entry carries NO matcher",
          len(ledger_entries) == 1 and "matcher" not in ledger_entries[0])
    # ...and the pre-existing Bash-scoped nudges kept THEIR matcher — proving
    # the assertion above is about this entry and not about a matcher-stripping
    # bug that would pass it vacuously.
    bash_entries = [e for e in d["hooks"]["PostToolUse"]
                    if any(h.get("command") == SEARCH_NUDGE
                           for h in e.get("hooks", []))]
    check("1: the Bash nudges keep their matcher",
          len(bash_entries) == 1 and bash_entries[0].get("matcher") == "Bash")

    # Idempotency: a second run changes nothing and never duplicates.
    p2 = run(env)
    check("3: second run exit 0", p2.returncode == 0)
    check("3: second run reports no change", "no change" in p2.stdout)
    with open(settings) as f:
        d2 = json.load(f)
    check("3: no duplicate claude-notify on Stop", cmds(d2, "Stop").count(NOTIFY) == 1)
    check("3: no duplicate search-tool-nudge", cmds(d2, "PostToolUse").count(SEARCH_NUDGE) == 1)
    check("3: clawgate Stop still single + present", cmds(d2, "Stop").count(CLAWGATE_STOP) == 1)
    check("3: no duplicate next-step-nudge on Stop", cmds(d2, "Stop").count(NEXT_STEP) == 1)
    check("3: Stop set unchanged by the second run",
          set(cmds(d2, "Stop")) == {TMUX_STOP, CLAWGATE_STOP, NOTIFY, NEXT_STEP, LEDGER})
    check("3: no duplicate agent-ledger on Stop", cmds(d2, "Stop").count(LEDGER) == 1)
    check("3: no duplicate agent-ledger on PostToolUse",
          cmds(d2, "PostToolUse").count(LEDGER) == 1)

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
