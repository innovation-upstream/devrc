#!/usr/bin/env python3
"""Session-id commit trailers — ONE definition, two deploy paths.

WHAT PROBLEM THIS SOLVES. `cg#365`'s closing condition is "git blame a line ->
resolve the session -> wake it". Two things break it, and only one of them was
known:

  1. THE ID SPACES ARE DISJOINT. The commit trailer carries a claude.ai token
     (`https://claude.ai/code/session_01...`). The handle that actually resumes a
     session — what `claude --resume` takes and what `find-session` prints — is
     the runtime's own session id, which the hook layer receives. MEASURED
     2026-08-30: 69 of 69 per-session state dirs under
     `~/.cache/claude-clawgate-writeback/s/` are uuid-shaped; ZERO are
     `session_…` tokens. Searching a session's transcript for its own claude.ai
     token returns 0 because they are different namespaces, not because the
     search is wrong.

  2. 🔴 COVERAGE, WHICH IS THE BIGGER HOLE AND WAS NOT KNOWN. Measured on
     `origin/main` 2026-08-30: only **46%** of the last 100 commits carry a
     `Claude-Session:` trailer at all (33% of the last 200). Stamping a second id
     onto a trailer that is absent from half of commits improves the half that
     already worked and does nothing for the rest. The trailer is at 46% because
     it is emitted by PROSE — an instruction the agent must remember — which is
     exactly what `claude/RULES.md` says to replace with something structural.

🔴 DO NOT ASSUME UUID SHAPE. `scripts/lib/cairn_who.py` records that 2 of 41
windows carried a `ses_…` token from a different runtime, and that a join
assuming uuid shape "silently matches nothing and reports a clean 'no live
window'". This module therefore treats the id as an OPAQUE STRING everywhere:
it is validated for safety (no newlines, bounded length) but never parsed,
normalised, lowercased or shape-checked.

🔴 WHY PID-KEYED STATE AND NOT ONE FILE PER REPO. The obvious design — write the
current session id to `<git-dir>/claude-session.json` and have the commit hook
read it — RACES. This box runs many concurrent sessions and ~117 worktrees
sharing one common git dir; two sessions committing in the same repo would
overwrite each other and one commit would be stamped with the OTHER session's id.
A wrong id is worse than no id: it sends a future reader to a session that never
touched the line. So state is keyed by the CLAUDE ANCESTOR PID, which both the
recording hook and the commit hook can resolve independently from their own
process ancestry, and which is unique per live session.

🔴 THIS MODULE HAS TWO DEPLOY PATHS AND THAT IS DELIBERATE. The Claude-side hook
is a home-manager STORE COPY (`home.file`), so it cannot reach `scripts/lib/`
through `__file__` — that is the #1079 trap. The established repo pattern is to
deploy the shared module ALONGSIDE the hook into `~/.claude/hooks/` and import it
from the hook's own directory, exactly as `guard_core.py` is deployed beside
`bash-guard.py`. The git-side `prepare-commit-msg` hook is installed per-clone
into the common `.git/hooks/` and reaches this file by repo path. One source,
two carriers — never two implementations.

FAILS OPEN, ALWAYS. Nothing here may ever block a commit or a tool call. Every
entry point catches broadly and degrades to "no trailer". A commit that loses its
trailer is a missing datum; a commit that cannot be made is a broken workstation.
"""
from __future__ import annotations

import json
import os
import re
import time

# Matches the process name of a Claude Code runtime in /proc/<pid>/stat's `comm`.
# 🔴 `comm` is TRUNCATED TO 15 CHARS by the kernel, which is why this is a
# substring search and not an equality test: the observed wrapper name on this
# host is `.claude-wrapped`, which is exactly 15 characters and would be a
# different string under any longer spelling.
CLAUDE_COMM_RE = re.compile(r"claude", re.IGNORECASE)

STATE_DIRNAME = "claude-session"

# An id is written into a commit message, so it must not be able to forge
# additional trailer lines or run away with the file.
_MAX_ID_LEN = 256

TRAILER_KEY = "Claude-Session-Id"
LEGACY_TRAILER_KEY = "Claude-Session"

# How far up the process tree to look before giving up. A Claude session's own
# tool call is only a few levels deep; 64 is the same bound
# `claude_sessions.own_pid_chain` uses.
_MAX_DEPTH = 64


def read_proc(pid: int):
    """(comm, ppid) for `pid`, or None when it is gone or unreadable.

    Deliberately a narrower reader than `claude_sessions._read_proc`: this module
    needs only the ancestry walk, and must not fail because /proc/uptime or
    SC_CLK_TCK is unavailable in whatever sandbox a hook runs in.
    """
    try:
        with open("/proc/%d/stat" % pid, "r") as fh:
            data = fh.read()
        # `comm` is parenthesised and may itself contain spaces and parens, so
        # split on the LAST ')' — the same hazard claude_sessions.py documents.
        rparen = data.rfind(")")
        lparen = data.find("(")
        if lparen < 0 or rparen < lparen:
            return None
        comm = data[lparen + 1:rparen]
        rest = data[rparen + 2:].split()
        return {"comm": comm, "ppid": int(rest[1])}
    except Exception:
        return None


def claude_ancestor_pid(start_pid=None, reader=read_proc):
    """The pid of the nearest Claude runtime at or above `start_pid`.

    Both the recording hook and the commit hook call this from inside the same
    session's process tree, so both land on the SAME pid — that identity is what
    makes the state lookup correct without a lock.

    Returns None when there is no Claude ancestor, which is the ordinary case for
    a human running `git commit` in a terminal. None means "do not stamp", never
    "stamp something else".
    """
    pid = os.getpid() if start_pid is None else start_pid
    seen = set()
    for _ in range(_MAX_DEPTH):
        if pid <= 1 or pid in seen:
            return None
        seen.add(pid)
        info = reader(pid)
        if not info:
            return None
        if CLAUDE_COMM_RE.search(info.get("comm") or ""):
            return pid
        pid = info.get("ppid", 0)
    return None


def valid_id(value) -> bool:
    """Is `value` safe to write into a commit message as a trailer value?

    Opaque-string discipline: this checks only what could CORRUPT the message or
    the file, never what the id should look like. A `ses_…` token, a uuid and any
    future spelling all pass.
    """
    if not isinstance(value, str):
        return False
    v = value.strip()
    if not v or len(v) > _MAX_ID_LEN:
        return False
    # A newline would let one id forge a second trailer line.
    return not any(c in v for c in "\r\n")


def state_dir(git_common_dir: str) -> str:
    return os.path.join(git_common_dir, STATE_DIRNAME)


def state_file(git_common_dir: str, pid: int) -> str:
    return os.path.join(state_dir(git_common_dir), "%d.json" % pid)


def record(git_common_dir: str, session_id: str, pid: int,
           transcript_path=None, now=None) -> bool:
    """Persist `session_id` for the live Claude process `pid`. True when written.

    Never raises. A failure to record costs a trailer, not a commit.
    """
    if not valid_id(session_id) or not isinstance(pid, int) or pid <= 0:
        return False
    try:
        d = state_dir(git_common_dir)
        os.makedirs(d, exist_ok=True)
        payload = {
            "session_id": session_id,
            "claude_pid": pid,
            "written_at": now if now is not None else time.time(),
        }
        if transcript_path:
            payload["transcript_path"] = transcript_path
        tmp = state_file(git_common_dir, pid) + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(payload, fh)
        # Atomic replace so a concurrent reader never sees a half-written file.
        os.replace(tmp, state_file(git_common_dir, pid))
        return True
    except Exception:
        return False


def lookup(git_common_dir: str, pid=None, start_pid=None, reader=read_proc):
    """The session id recorded for THIS process's Claude ancestor, or None.

    🔴 The pid is resolved from the CALLER's own ancestry, so a concurrent
    session's state file — sitting in the same directory — can never be read by
    mistake. That is the whole reason the state is pid-keyed.

    `start_pid` is where the ancestry walk BEGINS (default: this process).
    `pid` short-circuits the walk entirely with an already-resolved Claude pid.
    Both exist so the safety property above can be tested against an injected
    process tree — without them the walk always started at the live `os.getpid()`
    and the most important test in the suite could not reach its own fixture.
    """
    try:
        p = (claude_ancestor_pid(start_pid=start_pid, reader=reader)
             if pid is None else pid)
        if not p:
            return None
        with open(state_file(git_common_dir, p), "r") as fh:
            data = json.load(fh)
        sid = data.get("session_id")
        return sid if valid_id(sid) else None
    except Exception:
        return None


def prune(git_common_dir: str, alive=None) -> int:
    """Drop state files whose pid is gone. Returns how many were removed.

    Without this the directory grows one file per session forever. `alive` is
    injectable so the test suite does not have to spawn real processes.
    """
    def _alive(pid: int) -> bool:
        return read_proc(pid) is not None

    check = alive or _alive
    removed = 0
    try:
        d = state_dir(git_common_dir)
        for name in os.listdir(d):
            if not name.endswith(".json"):
                continue
            try:
                pid = int(name[:-len(".json")])
            except ValueError:
                continue
            if not check(pid):
                try:
                    os.remove(os.path.join(d, name))
                    removed += 1
                except OSError:
                    pass
    except Exception:
        pass
    return removed


def has_trailer(message: str, key: str) -> bool:
    """Does `message` already carry a `key:` trailer on a line of its own?

    🔴 Deliberately NOT `git interpret-trailers` and NOT `%(trailers:)`. MEASURED
    2026-08-30: git's own trailer parser reports NOTHING for these messages,
    because it only recognises a contiguous trailer block at the very END and
    this repo's messages carry a "Generated with" line after it. Trusting it
    reported 9 trailers where 66 exist. A line-anchored search is what matches
    reality here.
    """
    if not message:
        return False
    prefix = key.lower() + ":"
    return any(line.strip().lower().startswith(prefix)
               for line in message.splitlines())


def append_trailer(message: str, session_id: str, key: str = TRAILER_KEY) -> str:
    """Return `message` with a `key: session_id` trailer, idempotently.

    Idempotence matters because `prepare-commit-msg` runs again on `--amend` and
    on a re-edited message; without it a rebased commit accretes one trailer per
    edit.
    """
    if not valid_id(session_id) or has_trailer(message, key):
        return message
    body = (message or "").rstrip("\n")
    line = "%s: %s" % (key, session_id.strip())
    if not body:
        return line + "\n"
    # A trailer must be separated from prose by a blank line, but must NOT gain
    # one when the message already ends in a trailer block.
    tail = body.splitlines()[-1].strip()
    sep = "\n" if (":" in tail and " " not in tail.split(":", 1)[0]) else "\n\n"
    return body + sep + line + "\n"
