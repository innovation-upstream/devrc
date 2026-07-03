#!/usr/bin/env python3
"""Idempotently register the PostToolUse nudge hooks in ~/.claude/settings.json.

settings.json is per-host and unmanaged (holds permissions/allowlists/secrets), so the
hook *scripts* are symlinked by home-manager but their *registration* is applied by
running this once per host. Safe to re-run: it adds only the entries that are missing.

Run on each host after a home-manager switch that adds a new nudge hook:
    python3 ~/workspace/devrc/scripts/claude-hooks/register-nudge-hook.py
"""
import json, os, sys

SETTINGS = os.path.expanduser("~/.claude/settings.json")

# PostToolUse(Bash) nudge hooks to ensure are registered.
CMDS = [
    "python3 ~/.claude/hooks/audit-pr-nudge.py",
    "python3 ~/.claude/hooks/shell-env-nudge.py",
]

with open(SETTINGS) as f:
    data = json.load(f)

hooks = data.setdefault("hooks", {})
post = hooks.setdefault("PostToolUse", [])

registered = {h.get("command") for entry in post for h in entry.get("hooks", [])}

added = []
for cmd in CMDS:
    if cmd in registered:
        continue
    post.append({"matcher": "Bash", "hooks": [{"type": "command", "command": cmd}]})
    added.append(cmd)

if not added:
    print("all nudge hooks already registered — no change")
    sys.exit(0)

with open(SETTINGS, "w") as f:
    json.dump(data, f, indent=2)
    f.write("\n")
print("registered PostToolUse(Bash) hooks:")
for c in added:
    print("  +", c)
