#!/usr/bin/env python3
"""Idempotently register the audit-pr-nudge PostToolUse hook in ~/.claude/settings.json.

settings.json is per-host and unmanaged (holds permissions/allowlists/secrets), so
the hook *script* is symlinked by home-manager but its *registration* is applied by
running this once per host. Safe to re-run: it adds the entry only if missing.
"""
import json, os, sys

SETTINGS = os.path.expanduser("~/.claude/settings.json")
CMD = "python3 ~/.claude/hooks/audit-pr-nudge.py"

with open(SETTINGS) as f:
    data = json.load(f)

hooks = data.setdefault("hooks", {})
post = hooks.setdefault("PostToolUse", [])

# Already registered anywhere in PostToolUse? -> no-op.
for entry in post:
    for h in entry.get("hooks", []):
        if h.get("command") == CMD:
            print("already registered — no change")
            sys.exit(0)

post.append({
    "matcher": "Bash",
    "hooks": [{"type": "command", "command": CMD}],
})

# Write back, preserving 2-space indentation style used by the file.
with open(SETTINGS, "w") as f:
    json.dump(data, f, indent=2)
    f.write("\n")
print("registered audit-pr-nudge PostToolUse(Bash) hook")
