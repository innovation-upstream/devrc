#!/usr/bin/env python3
"""PreToolUse hook: record this session's id so `prepare-commit-msg` can stamp it.

WHY A HOOK AND NOT AN INSTRUCTION. The `Claude-Session:` trailer that exists
today is emitted because the agent is TOLD to emit it, and counted PER COMMIT on
`origin/main` at `3b1a0477` (2026-08-30) it lands on 47 of the last 100 and 67 of
the last 200 — 47% and 33%. The rest cannot be resolved to a session at all.
`claude/RULES.md` — "prefer deterministic/structural fixes over prompt-tuning,
prose instructions" — is exactly this case. (Two earlier figures here were wrong;
`scripts/lib/session_trailer.py` records both and why.)

WHAT IT RECORDS. The hook payload's `session_id`: the runtime's own handle,
which is what `claude --resume` takes. NOT the claude.ai token in the existing
trailer; those are disjoint namespaces (see `scripts/lib/session_trailer.py`).

🔴 IT WRITES NO REPO STATE, AND THAT FIXED A BUG. An earlier version resolved
the git common dir of the payload's `cwd` and wrote there. `CLAUDE.md` mandates
`git -C <path> commit` over `cd`, so a commit into ANOTHER repo recorded state
against the CWD's repo and the commit got no trailer — measured. The pid is the
entire key, so the state is per-session under $HOME and the git dir is not
consulted by either half. That also removes a `git rev-parse` subprocess from
every commit-shaped tool call.

🔴 THE TRIGGER IS COMMIT-SHAPED COMMANDS. PreToolUse fires on every Bash call;
recording only when a commit is imminent is both cheap and correctly ordered,
since PreToolUse runs immediately before the command. KNOWN GAP, stated rather
than hidden: a commit made INDIRECTLY (a script that commits internally) does
not match and gets no trailer — degrading to today's behaviour, never to a WRONG
id, because a stale record is rejected by the start-time pin in `lookup()`.

🔴 FAILS OPEN, UNCONDITIONALLY. Never denies, never delays, never raises. Prints
nothing and exits 0 on every path.
"""
from __future__ import annotations

import json
import os
import re
import sys

# Deployed ALONGSIDE this file into ~/.claude/hooks/ (the `guard_core.py`
# pattern) because a home-manager store copy cannot reach scripts/lib/ through
# __file__ — the #1079 trap. In the repo checkout the sibling path below wins.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.append(
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib")
)

try:
    import session_trailer as st
except Exception:  # pragma: no cover - fail-open import guard
    sys.exit(0)

# `git commit`, `git -C <path> commit`, `git -c k=v commit`, `git commit -m …`.
# 🔴 Written to avoid a nested quantifier over an OPTIONAL group: the earlier
# `(?:\s+-\S+(?:\s+\S+)?)*` made token partitioning ambiguous and backtracked
# super-linearly (measured ~1.65x per added dash-token). A PreToolUse hang blocks
# the agent's Bash call, so the pattern must be linear on any input.
_COMMIT_RE = re.compile(r"\bgit\b[^\n;|&]{0,400}?\bcommit\b")


def looks_like_commit(command: str) -> bool:
    return bool(command) and bool(_COMMIT_RE.search(command))


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

    # 🔴 TEST-ONLY INJECTION, for the SAME reason the git half has one, and its
    # absence here was a real defect: a nix-sandbox build has no Claude process
    # anywhere in its ancestry, so `claude_ancestor_pid()` correctly returns None
    # and this hook correctly records nothing — which made every recording test
    # pass on the dev host (where the test process genuinely IS under Claude) and
    # FAIL in the sandbox. Green in one tier, red in the other, for a reason that
    # has nothing to do with the code under test. That is the config-blind suite
    # CLAUDE.md documents, and the seam file's own docstring warns about it.
    #
    # It selects WHICH pid is used as the state key; it cannot invent a session,
    # because `record()` still resolves that pid through /proc and refuses if it
    # is not a live process.
    injected = os.environ.get("DEVRC_SESSION_TRAILER_PID")
    try:
        pid = int(injected) if injected else st.claude_ancestor_pid()
    except ValueError:
        pid = st.claude_ancestor_pid()
    if not pid:
        return

    st.record(session_id, pid, transcript_path=data.get("transcript_path"))
    st.prune()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
