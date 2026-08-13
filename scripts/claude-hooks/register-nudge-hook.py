#!/usr/bin/env python3
"""Idempotently register the devrc-managed Claude Code hooks in ~/.claude/settings.json.

settings.json is per-host and unmanaged (holds permissions/allowlists/secrets), so the
hook *scripts* are symlinked by home-manager but their *registration* is applied by
running this once per host. Safe to re-run: it adds only the entries that are missing
and NEVER clobbers hooks it doesn't own (clawgate PermissionRequest/Stop, bash-guard,
tmux hooks, etc. are preserved — this only ever appends).

Registers:
  * PostToolUse(Bash): audit-pr-nudge.py, shell-env-nudge.py, search-tool-nudge.py
  * UserPromptSubmit / Stop / SubagentStop: claude-notify.py (the turn-finished
    notifier — a best-effort side-effect hook, appended alongside any existing
    Stop/clawgate-stop/tmux hooks).
  * Stop: next-step-nudge.py

🔴 Stop is a SHARED event with three pre-existing owners this script does not own and
must never disturb: the fuzzyclaw tmux writer (~/.config/tmux/task-hook.sh), the
clawgate stop hook (drives remote approval) and claude-notify.py (drives turn-finished
notifications). Clobbering any of them is a serious regression, so registration here is
strictly APPEND-ONLY — an entry is added only when its exact command string is absent
from the event's existing entries, and no existing entry is ever rewritten, reordered or
removed. tests/test_register_nudge_hook.py asserts all four coexist afterwards.

next-step-nudge is registered on Stop ONLY, not SubagentStop: a subagent's turn ends
without ever reaching the operator, so it owes them no next step. The hook refuses that
event itself as well — belt and braces, because the registration is per-host mutable
state and the hook is the thing that actually ships.

Run on each host after a home-manager switch that adds a new hook:
    python3 ~/workspace/devrc/scripts/claude-hooks/register-nudge-hook.py
"""
import json, os, sys, tempfile

SETTINGS = os.path.expanduser("~/.claude/settings.json")

# PostToolUse(Bash) nudge hooks to ensure are registered.
POST_BASH_CMDS = [
    "python3 ~/.claude/hooks/audit-pr-nudge.py",
    "python3 ~/.claude/hooks/shell-env-nudge.py",
    "python3 ~/.claude/hooks/search-tool-nudge.py",
]

# The turn-finished notifier fires on these three events (single script,
# dispatched on the event name in its stdin payload).
NOTIFY_CMD = "python3 ~/.claude/hooks/claude-notify.py"
NOTIFY_EVENTS = ["UserPromptSubmit", "Stop", "SubagentStop"]

# Hooks registered on exactly one event each: {event: [command, ...]}.
SINGLE_EVENT_CMDS = {
    "Stop": ["python3 ~/.claude/hooks/next-step-nudge.py"],
}

with open(SETTINGS) as f:
    data = json.load(f)

hooks = data.setdefault("hooks", {})
added = []


def registered_commands(event_arrays):
    """All command strings already present across an event's entry list."""
    return {h.get("command") for entry in event_arrays for h in entry.get("hooks", [])}


# --- PostToolUse(Bash) nudge hooks (append-only, matcher=Bash) ---------------
post = hooks.setdefault("PostToolUse", [])
post_registered = registered_commands(post)
for cmd in POST_BASH_CMDS:
    if cmd in post_registered:
        continue
    post.append({"matcher": "Bash", "hooks": [{"type": "command", "command": cmd}]})
    added.append("PostToolUse(Bash): " + cmd)

# --- claude-notify on the three turn-boundary events (append-only) -----------
# Preserve every existing entry on each event array (e.g. a clawgate-stop-hook or
# a tmux Stop hook) — only add claude-notify where it's missing.
for event in NOTIFY_EVENTS:
    arr = hooks.setdefault(event, [])
    if NOTIFY_CMD in registered_commands(arr):
        continue
    arr.append({"hooks": [{"type": "command", "command": NOTIFY_CMD}]})
    added.append("%s: %s" % (event, NOTIFY_CMD))

# --- single-event hooks (append-only, same discipline) -----------------------
for event, cmds in SINGLE_EVENT_CMDS.items():
    arr = hooks.setdefault(event, [])
    present = registered_commands(arr)
    for cmd in cmds:
        if cmd in present:
            continue
        arr.append({"hooks": [{"type": "command", "command": cmd}]})
        added.append("%s: %s" % (event, cmd))

if not added:
    print("all devrc-managed hooks already registered — no change")
    sys.exit(0)

# Atomic write: settings.json gates permissions, so a crash mid-write must never
# leave it truncated/corrupt. Write a temp file in the same dir, then os.replace
# (atomic rename on the same filesystem) so readers only ever see the old file
# or the fully-written new one.
d = os.path.dirname(SETTINGS) or "."
fd, tmp = tempfile.mkstemp(dir=d, prefix=".settings.", suffix=".tmp")
try:
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    os.replace(tmp, SETTINGS)
except Exception:
    try:
        os.remove(tmp)
    except OSError:
        pass
    raise
print("registered hooks:")
for c in added:
    print("  +", c)
