#!/usr/bin/env python3
"""PreToolUse hook: record this session's id so `prepare-commit-msg` can stamp it.

WHY A HOOK AND NOT AN INSTRUCTION. The `Claude-Session:` trailer that exists
today is emitted because the agent is TOLD to emit it, and measured on
`origin/main` 2026-08-30 that lands on **46%** of the last 100 commits. The other
54% are not resolvable to a session at all. `claude/RULES.md` — "prefer
deterministic/structural fixes over prompt-tuning, prose instructions" — is
exactly this case, and the 46% is what the prose version scores.

WHAT IT RECORDS. The hook payload's `session_id`: the runtime's own handle, which
is what `claude --resume` takes. It is NOT the claude.ai token in the existing
trailer; those are disjoint namespaces (see `scripts/lib/session_trailer.py`).

🔴 THE TRIGGER IS COMMIT-SHAPED COMMANDS, NOT EVERY CALL. PreToolUse fires on
every Bash tool call, and resolving a repo's common git dir costs a subprocess.
Recording only when the command is about to commit is both cheap and correctly
ordered: PreToolUse runs immediately before the command, so the value written is
the one that commit will read, with no window for another session to interleave.
KNOWN GAP, stated rather than hidden: a commit made INDIRECTLY (a script that
commits internally, an editor integration) does not match, so it gets no trailer.
That degrades to today's behaviour for those commits — never to a WRONG id.

🔴 FAILS OPEN, UNCONDITIONALLY. This hook must never deny, never delay and never
raise. It prints nothing and exits 0 on every path including catastrophic ones.
A hook that can block a commit is a worse defect than the missing trailer it
exists to fix.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys

# Deployed ALONGSIDE this file into ~/.claude/hooks/ (the `guard_core.py`
# pattern) because a home-manager store copy cannot reach scripts/lib/ through
# __file__ — the #1079 trap. In the repo checkout the same import resolves via
# the path appended below.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.append(
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib")
)

try:
    import session_trailer as st
except Exception:  # pragma: no cover - fail-open import guard
    sys.exit(0)

# `git commit`, `git -C <path> commit`, `git commit -m …`. Requires the two words
# in order so that `git log --format=%H` (which contains neither) and a message
# body merely mentioning the word "commit" do not trigger a pointless subprocess.
_COMMIT_RE = re.compile(r"\bgit\b(?:\s+-[^\s]+(?:\s+[^\s]+)?)*\s+commit\b")


def looks_like_commit(command: str) -> bool:
    return bool(command) and bool(_COMMIT_RE.search(command))


def git_common_dir(cwd: str):
    """Absolute path to the repo's COMMON git dir, or None outside a repo.

    The COMMON dir (not `--git-dir`) is deliberate: every worktree of a clone
    shares it, so one recorded state serves a commit made from any of this box's
    ~117 devrc worktrees.
    """
    try:
        out = subprocess.run(
            ["git", "-C", cwd, "rev-parse", "--path-format=absolute",
             "--git-common-dir"],
            capture_output=True, text=True, timeout=5,
        )
        if out.returncode != 0:
            return None
        path = (out.stdout or "").strip()
        return path or None
    except Exception:
        return None


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except Exception:
        return

    if (data.get("tool_name") or "") != "Bash":
        return

    command = ((data.get("tool_input") or {}).get("command")) or ""
    if not looks_like_commit(command):
        return

    session_id = data.get("session_id")
    if not st.valid_id(session_id):
        return

    cwd = data.get("cwd") or os.getcwd()
    common = git_common_dir(cwd)
    if not common:
        return

    pid = st.claude_ancestor_pid()
    if not pid:
        return

    st.record(common, session_id, pid,
              transcript_path=data.get("transcript_path"))
    # Keep the directory from growing one file per session forever.
    st.prune(common)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
