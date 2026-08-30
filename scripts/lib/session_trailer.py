#!/usr/bin/env python3
"""Session-id commit trailers — ONE definition, two deploy paths.

WHAT PROBLEM THIS SOLVES. `cg#365`'s closing condition is "git blame a line ->
resolve the session -> wake it". Two things break it, and only one was known:

  1. THE ID SPACES ARE DISJOINT. The commit trailer carries a claude.ai token
     (`https://claude.ai/code/session_01...`). The handle that actually resumes a
     session — what `claude --resume` takes and what `find-session` prints — is
     the runtime's own session id, which the hook layer receives. MEASURED
     2026-08-30: 69 of 69 per-session state dirs under
     `~/.cache/claude-clawgate-writeback/s/` are uuid-shaped; ZERO are
     `session_…` tokens. Searching a session's transcript for its own claude.ai
     token returns 0 because they are different namespaces.

  2. 🔴 COVERAGE, WHICH IS THE BIGGER HOLE AND WAS NOT KNOWN. Commits on
     `origin/main` carrying a `^Claude-Session:` trailer, counted PER COMMIT at
     `3b1a0477` on 2026-08-30: **47 of the last 100 (47%), 67 of the last 200
     (33%)**. So half of `main` cannot be resolved to a session at all,
     regardless of WHICH id is stamped. It is that low because the trailer is
     emitted by PROSE — an instruction the agent must remember — which is exactly
     what `claude/RULES.md` says to replace with something structural.

     🔴 COUNT COMMITS, NOT LINES — AND RE-MEASURE RATHER THAN QUOTING THIS. Two
     wrong figures preceded this one, and the second is the instructive one:
       * "9 of the last 200" came from `grep -c`, which counts LINES; each such
         commit repeats the token on ~3 lines.
       * "41% / 27%" was WORSE, because it was an AUDITOR'S number adopted
         without re-measuring, and it overwrote a figure of my own that had been
         essentially right. `claude/RULES.md` says to re-verify a subagent's
         self-reported results; this is the cost of skipping that. Its stated
         cause was false too: anchoring does NOT change the per-commit count
         (measured — 67 either way), because anchoring only matters when counting
         lines.
     The conclusion never moved; only the numbers did, three times. Hence the sha
     and the method beside them.

🔴 DO NOT ASSUME UUID SHAPE. `scripts/lib/cairn_who.py` records that 2 of 41
windows carried a `ses_…` token from a different runtime, and that a join
assuming uuid shape "silently matches nothing and reports a clean 'no live
window'". This module treats the id as an OPAQUE STRING everywhere: validated
for safety, never parsed, normalised, lowercased or shape-checked.

🔴 WHY PID-KEYED STATE, AND WHY IT IS PINNED BY START TIME. The obvious design —
one state file per repo — RACES: this box runs many concurrent sessions, so two
sessions committing in one repo would overwrite each other and one commit would
be stamped with the OTHER session's id. A wrong id is worse than no id. So state
is keyed by the CLAUDE ANCESTOR PID, which both halves resolve independently
from their own process ancestry.

A raw pid is NOT enough. MEASURED on this host: `kernel.pid_max` is 4194304 and
live pids already span 114904–4193245, i.e. the pid space has fully wrapped at
least once — recycling is routine, not theoretical. So every record also pins
`/proc/<pid>` field 22 (`starttime`), and a lookup whose start time disagrees is
treated as a MISS. That costs nothing: `read_proc` already parses the field.

🔴 STATE LIVES UNDER $HOME, NOT IN THE GIT DIR. It used to live in the repo's
common git dir, and that was a bug: `CLAUDE.md` mandates `git -C <path> commit`
over `cd`, so the recording hook resolved the git dir of its OWN cwd while the
commit landed in a DIFFERENT repo — state written to repo A, commit made in repo
B, no trailer. The pid is the entire key; the git dir bought nothing and cost a
subprocess on every commit-shaped tool call.

🔴 TWO DEPLOY PATHS, DELIBERATELY. The Claude-side hook is a home-manager STORE
COPY, so it cannot reach `scripts/lib/` through `__file__` (the #1079 trap); the
shared module is deployed ALONGSIDE it into `~/.claude/hooks/`, as `guard_core.py`
is beside `bash-guard.py`. The git-side hook reaches this file through its
install symlink's realpath. One source, two carriers — never two implementations.

🔴 WHY THIS IS NOT `agent_ledger.py`, WHICH ALREADY STORES THE SAME TWO FIELDS.
Asked and answered, so it does not read as unexamined duplication: that ledger
records `{runtime, session_id, transcript_path, …}` too, and is already deployed
into `~/.claude/hooks/`. But its records are keyed on **pane_id / window_id** —
tmux presence — because its consumer is `session-manager`'s view and a
ClickHouse join. This hook has to answer a different question with a different
key: "which session owns THIS process", resolved from `/proc` ancestry by a git
hook that has no tmux context and may be running under a clawgate agent, which
has no pane at all. Reusing the ledger would mean shelling out to tmux from
inside every `git commit`. Same two fields, different join key, different
consumer — so two stores, deliberately, rather than one store bent to two shapes.

FAILS OPEN, ALWAYS. Nothing here may block a commit or a tool call. Every entry
point catches broadly and degrades to "no trailer". A commit that loses its
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
# host is `.claude-wrapped`, exactly 15 characters.
CLAUDE_COMM_RE = re.compile(r"claude", re.IGNORECASE)

STATE_DIRNAME = "claude-session-trailer"

# An id is written into a commit message, so it must not be able to forge extra
# trailer lines, embed a NUL, or run away with the file.
_MAX_ID_LEN = 256

TRAILER_KEY = "Claude-Session-Id"

# How far up the process tree to look. A session's own tool call is a few levels
# deep; 64 matches `claude_sessions.own_pid_chain`'s bound.
_MAX_DEPTH = 64


def state_root() -> str:
    """Where per-session state lives. Honours XDG_CACHE_HOME."""
    override = os.environ.get("DEVRC_SESSION_TRAILER_ROOT")
    if override:
        return override
    cache = os.environ.get("XDG_CACHE_HOME") or os.path.join(
        os.path.expanduser("~"), ".cache")
    return os.path.join(cache, STATE_DIRNAME)


def state_file(pid: int, root=None) -> str:
    return os.path.join(root or state_root(), "%d.json" % pid)


def read_proc(pid: int):
    """(comm, ppid, starttime) for `pid`, or None when gone or unreadable.

    Narrower than `claude_sessions._read_proc` on purpose: this runs inside a git
    hook and must not fail because /proc/uptime or SC_CLK_TCK is unavailable, so
    it reads the raw `starttime` tick count and never converts it to an age.
    """
    try:
        with open("/proc/%d/stat" % pid, "r") as fh:
            data = fh.read()
        # `comm` is parenthesised and may contain spaces and parens — split on
        # the LAST ')', the same hazard claude_sessions.py documents.
        rparen = data.rfind(")")
        lparen = data.find("(")
        if lparen < 0 or rparen < lparen:
            return None
        comm = data[lparen + 1:rparen]
        rest = data[rparen + 2:].split()
        return {"comm": comm, "ppid": int(rest[1]), "starttime": int(rest[19])}
    except Exception:
        return None


def claude_ancestor_pid(start_pid=None, reader=read_proc):
    """The pid of the nearest Claude runtime at or above `start_pid`.

    Both halves call this from inside the same session's process tree, so both
    land on the SAME pid — that identity is what makes the lookup correct without
    a lock. Returns None when there is no Claude ancestor, which is the ordinary
    case for a human at a terminal. None means "do not stamp", never "stamp
    something else".
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

    Opaque-string discipline: this checks only what could CORRUPT the message,
    never what the id should look like. A `ses_…` token, a uuid and any future
    spelling all pass.
    """
    if not isinstance(value, str):
        return False
    v = value.strip()
    if not v or len(v) > _MAX_ID_LEN:
        return False
    # Newlines forge trailer lines; NUL and other C0 controls corrupt the file
    # and can truncate it for downstream readers. Tab is a trailer separator.
    return not any(c in v for c in "\r\n\t\x00") and all(
        ord(c) >= 0x20 or c == " " for c in v)


def record(session_id: str, pid: int, root=None, transcript_path=None,
           now=None, reader=read_proc) -> bool:
    """Persist `session_id` for the live Claude process `pid`. True when written.

    Pins the process's start time so a recycled pid cannot inherit this record.
    Never raises: a failure to record costs a trailer, not a commit.
    """
    if not valid_id(session_id) or not isinstance(pid, int) or pid <= 0:
        return False
    try:
        info = reader(pid)
        if not info:
            return False
        d = root or state_root()
        os.makedirs(d, exist_ok=True)
        payload = {
            "session_id": session_id,
            "claude_pid": pid,
            "starttime": info.get("starttime"),
            "written_at": now if now is not None else time.time(),
        }
        if transcript_path:
            payload["transcript_path"] = transcript_path
        target = state_file(pid, d)
        tmp = target + ".tmp"
        # 🔴 CREATE IT 0600, don't chmod AFTER. `open()` then `os.chmod()` leaves
        # the file world-readable between the two calls, and it names a session
        # and may carry a transcript path. Not exploitable on this host (the home
        # directory is 0700, so nobody can traverse to it) but the window is free
        # to close and a shared host would not have that protection.
        fd = os.open(tmp, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            json.dump(payload, fh)
        os.replace(tmp, target)          # atomic: no half-written read
        return True
    except Exception:
        return False


def lookup(pid=None, start_pid=None, root=None, reader=read_proc):
    """The session id recorded for THIS process's Claude ancestor, or None.

    🔴 Resolved from the CALLER's own ancestry, so a concurrent session's state
    file — sitting in the same directory — can never be read by mistake. And the
    record's pinned start time must match the LIVE process, so a recycled pid is
    a MISS rather than a wrong answer.
    """
    try:
        p = (claude_ancestor_pid(start_pid=start_pid, reader=reader)
             if pid is None else pid)
        if not p:
            return None
        with open(state_file(p, root), "r") as fh:
            data = json.load(fh)
        sid = data.get("session_id")
        if not valid_id(sid):
            return None
        pinned = data.get("starttime")
        if pinned is not None:
            live = reader(p)
            if not live or live.get("starttime") != pinned:
                return None      # pid recycled — this record is somebody else's
        return sid
    except Exception:
        return None


def prune(root=None, reader=read_proc) -> int:
    """Drop state files whose process is gone OR whose pid has been recycled."""
    removed = 0
    try:
        d = root or state_root()
        for name in os.listdir(d):
            if not name.endswith(".json"):
                continue
            try:
                pid = int(name[:-len(".json")])
            except ValueError:
                continue
            path = os.path.join(d, name)
            keep = False
            try:
                with open(path, "r") as fh:
                    data = json.load(fh)
                live = reader(pid)
                pinned = data.get("starttime")
                # Keep only a process that is alive AND is still the same one.
                keep = bool(live) and (pinned is None
                                       or live.get("starttime") == pinned)
            except Exception:
                keep = False
            if not keep:
                try:
                    os.remove(path)
                    removed += 1
                except OSError:
                    pass
    except Exception:
        pass
    return removed


def _trailer_lines(message: str, key: str):
    """Indices of lines that are a `key:` trailer AT COLUMN 0.

    🔴 COLUMN 0, NOT `.strip()`. An indented `    Claude-Session-Id: <id>` inside
    a message BODY is prose — a quoted example — and git does not treat it as a
    trailer. Matching it made `has_trailer` true for such a message, so the
    commit silently got no stamp. That is the exact shape of this feature's own
    documentation commits, so it was not a hypothetical.

    🔴 Deliberately NOT `git interpret-trailers` / `%(trailers:)`. MEASURED:
    git's parser reports 9 trailers on `origin/main` where a content search finds
    55, because it only recognises a contiguous block at the very END and this
    repo's messages carry a "Generated with" line after it.
    """
    prefix = key.lower() + ":"
    return [i for i, line in enumerate(_lines(message))
            if line.lower().startswith(prefix)]


def _lines(message):
    """Split on "\\n" ONLY — never `splitlines()`.

    🔴 `str.splitlines()` also breaks on \\r, \\x0b, \\x0c, \\x85, \\u2028 and
    \\u2029, so rejoining with "\\n" REWRITES the message: measured, a CRLF
    message lost every \\r, and a body containing \\x0b gained a line break the
    author never wrote. The rewrite path round-trips through this, so a
    normalising split silently edits prose it was only supposed to read.
    """
    return (message or "").split("\n")


def has_trailer(message: str, key: str = TRAILER_KEY) -> bool:
    return bool(_trailer_lines(message, key))


def append_trailer(message: str, session_id: str, key: str = TRAILER_KEY) -> str:
    """Return `message` carrying exactly one `key: session_id` trailer.

    Idempotent for the SAME id — `prepare-commit-msg` re-runs on every `--amend`
    and re-edit, so a naive append accretes one trailer per edit.

    🔴 And CORRECTIVE for a DIFFERENT id. Keying idempotence on the key alone
    meant that when session B amended session A's commit, the message kept A's
    id — B's rewrite attributed to a session that never made it. That is the very
    failure the pid keying exists to prevent, reached with no race at all. The
    commit as it now stands is B's work, so the trailer is rewritten in place,
    preserving position rather than appending a second one.
    """
    if not valid_id(session_id):
        return message
    sid = session_id.strip()
    line = "%s: %s" % (key, sid)

    lines = _lines(message)
    existing = _trailer_lines(message, key)
    if existing:
        # 🔴 `len(existing) == 1` matters as much as the value matching. Testing
        # only "are they all already correct" returned early on a message that
        # carried the SAME id TWICE, leaving both — measured count 2. Duplicates
        # collapse whether or not the id is the one being written.
        if len(existing) == 1 and lines[existing[0]] == line:
            return message                      # already correct — no-op
        # Rewrite the first, drop any duplicates, keep the position.
        keep, first = [], existing[0]
        for i, text in enumerate(lines):
            if i == first:
                keep.append(line)
            elif i in existing:
                continue
            else:
                keep.append(text)
        return "\n".join(keep)

    body = (message or "").rstrip("\n")
    if not body:
        return line + "\n"
    # A trailer is separated from prose by a blank line, but must not gain one
    # when the message already ends in a trailer block.
    tail = _lines(body)[-1]
    is_trailer_tail = bool(re.match(r"^[A-Za-z][A-Za-z0-9-]*:\s", tail))
    sep = "\n" if is_trailer_tail else "\n\n"
    return body + sep + line + "\n"
