#!/usr/bin/env python3
"""PreToolUse guard for Claude Code Bash calls — the CLAUDE CODE ADAPTER.

All the checking logic lives in `guard_core.py` next to this file, which is
shared with the opencode plugin (`~/.config/opencode/plugin/guard.js`, generated
by nix/home.nix). Read guard_core.py's docstring first: it carries the DESIGN
NOTE about why message-text stripping is forbidden, and the reasoning behind the
argv parser.

🔴 THIS ADAPTER RUNS THE "claude-code" POLICY, WHICH IS FROZEN AT THE ORIGINAL
SIX CHECKS:
  - git add -A / --all / .   -> stage specific paths instead (RULES: never blind-stage)
  - git reset --hard         -> use git restore/checkout, NOT stash (RULES: never reset --hard)
  - large heredoc -> file    -> use the Write tool (token waste; audit-driven)
  - cd <path> && git ...     -> use git -C <path> (audit: #1 command shape, 1482x)
  - private key in a command -> reference the key file instead (never inline)
  - secret/public-IP + a publish sink (git commit / gh pr|issue) -> scrub before
    committing/posting (insights: leaked ingress IP into a public repo once)

This hook fires on EVERY Bash call in EVERY Claude Code session on both hosts,
so adding a check here changes the operator's primary tool. The extra
irreversible-action checks added for opencode (talosctl reset, mkfs, dd to a
block device, rm -r of /|$HOME|cwd, git stash, git clean -f) live in the
"opencode" policy and are deliberately NOT enabled here. Switching this adapter
to the "opencode" policy is a one-word change — and a decision for the operator
to make explicitly, not a side effect of hardening a different tool.

I/O contract (unchanged): reads PreToolUse JSON on stdin (`tool_name`,
`tool_input.command`), prints `hookSpecificOutput.permissionDecision = "deny"`
with a reason, exits 0.
"""
import sys, os, json

POLICY = "claude-code"

# Import the shared core from THIS file's directory. In the repo that is
# scripts/claude-hooks/; deployed it is ~/.claude/hooks/, where nix/home.nix
# links both files side by side. Bytecode writing is disabled so the guard never
# litters a home-manager-managed directory.
sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def _deny(reason):
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }))
    sys.exit(0)


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        sys.exit(0)
    if data.get("tool_name") != "Bash":
        sys.exit(0)
    cmd = (data.get("tool_input") or {}).get("command", "")
    if not cmd:
        sys.exit(0)

    # 🔴 FAIL CLOSED, LOUDLY. If the core cannot be imported (a partial
    # home-manager switch, a deleted file) the guard must not silently pass
    # every command — that is the failure mode where the operator believes they
    # are protected and are not. Denying with an actionable reason is the only
    # honest option, and the reason names the fix.
    try:
        import guard_core
    except Exception as exc:
        _deny(f"bash-guard could not load guard_core.py ({exc}). The Bash guard is "
              f"NOT running, so this call is denied rather than passed through "
              f"unchecked. Fix: ensure guard_core.py sits next to bash-guard.py "
              f"(~/.claude/hooks/guard_core.py) — re-run `home-manager switch`.")

    try:
        reason = guard_core.evaluate(cmd, POLICY)
    except Exception as exc:
        _deny(f"bash-guard crashed while checking this command ({exc}). Denying rather "
              f"than passing it through unchecked. Report this — the command text is "
              f"what reproduces it.")

    if reason:
        _deny(reason)
    sys.exit(0)


main()
