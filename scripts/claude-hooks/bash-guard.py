#!/usr/bin/env python3
"""PreToolUse guard for Claude Code Bash calls — the CLAUDE CODE ADAPTER.

All the checking logic lives in `guard_core.py` next to this file, which is
shared with the opencode plugin (`~/.config/opencode/plugin/guard.js`, generated
by nix/home.nix). Read guard_core.py's docstring first: it carries the DESIGN
NOTE about why message-text stripping is forbidden, and the reasoning behind the
argv parser.

🔴 THIS ADAPTER RUNS THE "claude-code" POLICY. It was FROZEN at the original six
raw-text checks until 2026-08-02, when three argv checks were added by explicit
operator decision. The list today:
  - git add -A / --all / .   -> stage specific paths instead (RULES: never blind-stage)
  - git reset --hard         -> use git restore/checkout, NOT stash (RULES: never reset --hard)
  - git -C <p> reset --hard  -> the same, through git's global-option hop (2026-08-02)
  - git stash                -> the stash stack is repo-GLOBAL (2026-08-02; RULES 🔴,
    incident 2026-07-25). `git stash list`/`show` are reads and stay allowed.
  - git clean -f             -> deletes untracked files, which here are routinely
    real work (2026-08-02; RULES 🔴). `git clean -nd` stays allowed.
  - talosctl … reset         -> WIPES a Talos node (2026-08-02). Reads such as
    `talosctl version` / `talosctl -n <ip> get members` stay allowed.
  - mkfs / mke2fs / mkswap … -> formats a filesystem (2026-08-02). Matched on the
    PROGRAM name, so naming it in an argument is not a command.
  - dd of=/dev/<block-dev>   -> overwrites a disk in place (2026-08-02).
    `of=/dev/null` and the other pseudo sinks, and file-to-file dd, stay allowed.
  - git commit on main/master/trunk -> feature branches only (2026-08-10; RULES 🔴 in
    THREE files, and violated twice anyway — 2026-08-06 and 2026-08-09/PR #366).
    Resolves the branch by shelling out to git, so it is the one check that reads
    the world; `git commit --dry-run`, a detached HEAD, a repo with no remotes, and
    the allowlisted homelab-talos (commit = live deploy) all stay allowed.
  - pkill -f <pattern>       -> matches the caller's OWN command line (2026-08-10;
    RULES 🔴). `pgrep -f` and `pkill <name>` without `-f` stay allowed.
  - large heredoc -> file    -> use the Write tool (token waste; audit-driven)
  - cd <path> && git ...     -> use git -C <path> (audit: #1 command shape, 1482x)
  - private key in a command -> reference the key file instead (never inline)
  - secret/public-IP + a publish sink (git commit / gh pr|issue) -> scrub before
    committing/posting (insights: leaked ingress IP into a public repo once)

This hook fires on EVERY Bash call in EVERY Claude Code session on both hosts,
so adding a check here changes the operator's primary tool. Exactly ONE
irreversible-action family remains opencode-ONLY and is deliberately NOT enabled
here: `rm -r` of /|$HOME|cwd|a top-level system dir. 🔴 That is a DECISION, not
the leftover of an unfinished migration — `rm -rf` has frequent legitimate use
on these hosts (build dirs, node_modules, throwaway worktrees), and a guard that
fires during routine cleanup trains the operator to route around it, which is
worse than no guard because it also reports safety. Claude Code additionally
falls back to a PROMPT the operator sees, the control opencode lacks. Do not
"finish the job" by adding it; see _IRREVERSIBLE_CHECKS in guard_core.py.
Switching this adapter to the "opencode" policy is a one-word change — and a
decision for the operator to make explicitly, not a side effect of hardening a
different tool.

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

    # The PreToolUse payload carries the session's working directory. Pass it
    # through: check_git_commit_to_main resolves WHICH REPO a `git commit` acts on,
    # and a command with no `-C` hop is answerable only from the cwd. Falling back
    # to the hook process's own cwd would usually agree, but "usually" is how a
    # guard ends up reporting on the wrong repo. `.get` with no default, so an
    # older/other harness that omits the key still gets a verdict.
    cwd = data.get("cwd")
    if not isinstance(cwd, str) or not cwd:
        cwd = None

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
        reason = guard_core.evaluate(cmd, POLICY, cwd)
    except Exception as exc:
        _deny(f"bash-guard crashed while checking this command ({exc}). Denying rather "
              f"than passing it through unchecked. Report this — the command text is "
              f"what reproduces it.")

    if reason:
        _deny(reason)
    sys.exit(0)


main()
