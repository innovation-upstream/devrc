#!/usr/bin/env python3
"""Build one `POST /api/transcripts` body from this host's Claude Code transcripts.

Called by `scripts/transcript-push.sh`, which owns the credentials, the HTTP and
the exit-code vocabulary. This half owns exactly one decision — WHICH sessions to
send and HOW MUCH of each — and it is separate from the shell for two reasons
that are not tidiness:

  * it is the only part with a testable contract (given a directory and a digest,
    produce a payload), and a shell heredoc is not directly testable; and
  * the tail arithmetic is a byte-boundary problem (see `read_tail`), which is
    exactly the kind of thing that is silently wrong in shell.

🔴 READ-ONLY. This opens transcript files for reading and writes one JSON
document to stdout. It never writes into the transcript tree, never executes
anything, and takes nothing from the server but a list of hashes it compares for
equality.

🔴 IT SENDS A **TAIL**, AND THE PAYLOAD SAYS SO. Measured on this fleet
2026-09-04: 315 transcript files modified within 24h, 456 MB in total, largest
single file 23 MB. `truncated` is a first-class field precisely because a
consumer that cannot tell "the whole session" from "the end of it" will state the
first while showing the second.

Exit codes, which the caller branches on:
    0   a payload was written to stdout
    10  nothing to push — every candidate already matches the server's digest
    1   anything else (message on stderr)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path

# 🔴 THE ONE PLACE THE STORED FORM IS DEFINED, and the server recomputes it
# rather than trusting what we send (see internal/transcript.NormalizePush). Two
# implementations of "the hash" is how a dedupe protocol silently degrades into
# "always push" or, worse, "never push".
def hash_tail(tail: bytes) -> str:
    return hashlib.sha256(tail).hexdigest()


def read_tail(path: Path, max_bytes: int) -> tuple[str, bool] | None:
    """Return (tail_text, truncated) for one transcript, or None if unreadable.

    🔴 THE LEADING PARTIAL RECORD IS DROPPED, NOT SHIPPED. Seeking to
    `size - max_bytes` lands in the middle of a JSON line essentially every time.
    The server's parser tolerates that (it counts a leading fragment separately
    from corruption, deliberately), but shipping it wastes the bytes and makes
    every truncated session look faintly broken. Dropping it costs one `find`.

    🔴 THE FILE IS BEING APPENDED TO AS WE READ IT — that is the normal case, not
    an edge one, because the sessions worth feeding are the live ones. So: no
    `size` is re-checked after the read, no consistency is claimed, and the
    result is explicitly "a tail as of some instant". The read is a single
    `seek`+`read` so it cannot interleave with itself.

    🔴 DECODED WITH errors="replace", AND THAT IS DELIBERATE RATHER THAN LAZY.
    The cut point is a byte offset, so it can land inside a multi-byte rune, and
    the server REJECTS a tail that is not valid UTF-8 (its column is TEXT and
    Postgres would otherwise fail the whole atomic push, taking every other
    session in the request down with it). Replacing is what makes one bad byte
    cost one glyph instead of one session. The dropped-first-line rule above
    removes almost every instance in practice; this is the backstop.
    """
    try:
        size = path.stat().st_size
        with path.open("rb") as fh:
            if size > max_bytes:
                fh.seek(size - max_bytes)
                truncated = True
            else:
                truncated = False
            raw = fh.read(max_bytes)
    except OSError:
        return None

    if truncated:
        nl = raw.find(b"\n")
        if nl == -1:
            # One record longer than the whole window: there is no boundary to
            # cut on. Send nothing rather than a fragment that parses to zero
            # events while claiming to be a conversation.
            return None
        raw = raw[nl + 1 :]

    return raw.decode("utf-8", errors="replace"), truncated


def project_of(transcript: Path) -> str:
    """The project label for a transcript.

    Claude Code names the containing directory after a SLUGIFIED cwd
    (`-home-zach-workspace-devrc`), so the last path segment is the closest thing
    to a project name available without reading the file.

    ⚠ IT IS A LABEL, NOT AN IDENTIFIER, and it is deliberately not un-slugified.
    The slug is lossy — a literal `-` in a directory name is indistinguishable
    from a `/` — so reconstructing a path from it would produce a confident wrong
    answer. The server prefers the `cwd` the RECORDS carry when it has one; this
    is the fallback for a tail that has none.
    """
    return transcript.parent.name.lstrip("-").split("-")[-1] or transcript.parent.name


def candidates(projects_dir: Path, max_age_hours: float, limit: int) -> list[Path]:
    """Recently-modified transcripts, newest first, bounded.

    🔴 RECENCY IS THE ONLY SELECTOR, ON PURPOSE. The obvious alternative — ask
    which sessions have a live tmux window — would couple this feeder to the
    session-manager collector, and that collector runs on ONE host while this
    runs on both. Worse, it would silently stop feeding a session the moment its
    window closed, which is exactly when someone wants to read what it did.
    """
    cutoff = time.time() - max_age_hours * 3600
    found: list[tuple[float, Path]] = []
    # 🔴 A BOUNDED WALK: the transcript tree is `<projects>/<slug>/<uuid>.jsonl`,
    # one level deep, and globbing it recursively would follow whatever else has
    # been dropped in there.
    try:
        project_dirs = [p for p in projects_dir.iterdir() if p.is_dir()]
    except OSError as exc:
        print(f"cannot list {projects_dir}: {exc}", file=sys.stderr)
        raise SystemExit(1)

    for d in project_dirs:
        try:
            entries = list(d.glob("*.jsonl"))
        except OSError:
            continue
        for f in entries:
            try:
                st = f.stat()
            except OSError:
                continue
            if not os.path.isfile(f):
                continue
            if st.st_mtime < cutoff:
                continue
            found.append((st.st_mtime, f))

    found.sort(key=lambda pair: pair[0], reverse=True)
    return [p for _, p in found[:limit]]


def load_digest(path: Path) -> dict[str, str]:
    """The server's `{sessionId: contentHash}`, as it reported it.

    🔴 A DIGEST THIS CANNOT PARSE IS AN ERROR, NEVER AN EMPTY ONE. Treating an
    unreadable response as "the server has nothing" is the expensive direction —
    every session re-pushed on every tick — and it would hide a server that had
    started answering with something else entirely.
    """
    with path.open("rb") as fh:
        doc = json.load(fh)
    sessions = doc.get("sessions")
    if sessions is None:
        raise SystemExit("digest response has no `sessions` key — this is not a clawgate digest")
    if not isinstance(sessions, list):
        raise SystemExit(f"digest `sessions` is {type(sessions).__name__}, want a list")
    out: dict[str, str] = {}
    for row in sessions:
        if not isinstance(row, dict):
            continue
        sid = row.get("sessionId")
        h = row.get("contentHash")
        if isinstance(sid, str) and isinstance(h, str) and sid:
            out[sid] = h
    return out


def build(args: argparse.Namespace) -> dict:
    projects_dir = Path(args.projects_dir)
    known = load_digest(Path(args.digest))

    sessions = []
    for path in candidates(projects_dir, args.max_age_hours, args.max_candidates):
        if len(sessions) >= args.max_sessions:
            break
        # The session id IS the filename stem — that is how Claude Code writes
        # them, and it is the same id the attention queue and session-manager's
        # `claude_session_id` carry, which is what makes the join work at all.
        session_id = path.stem
        if not session_id:
            continue

        result = read_tail(path, args.tail_bytes)
        if result is None:
            continue
        text, truncated = result
        if not text.strip():
            continue

        digest = hash_tail(text.encode("utf-8"))
        # 🔴 THE SKIP. This single comparison is the whole reason the steady-state
        # push is kilobytes rather than megabytes.
        if known.get(session_id) == digest:
            continue

        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue

        sessions.append(
            {
                "sessionId": session_id,
                "project": project_of(path),
                # `cwd` is left to the server, which reads it out of the records
                # themselves. Deriving it from the slug here would ship a
                # confidently wrong path (see project_of).
                "updatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(mtime)),
                "tail": text,
                "truncated": truncated,
                "contentHash": digest,
            }
        )

    return {"host": args.host, "sessions": sessions}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--projects-dir", required=True)
    ap.add_argument("--digest", required=True)
    ap.add_argument("--host", required=True)
    ap.add_argument("--tail-bytes", type=int, required=True)
    ap.add_argument("--max-sessions", type=int, required=True)
    ap.add_argument("--max-age-hours", type=float, required=True)
    ap.add_argument("--max-candidates", type=int, required=True)
    args = ap.parse_args()

    if args.tail_bytes <= 0 or args.max_sessions <= 0:
        print("tail-bytes and max-sessions must be positive", file=sys.stderr)
        return 1

    payload = build(args)
    if not payload["sessions"]:
        # 🔴 SIGNALLED BY A CODE, NOT BY AN EMPTY DOCUMENT. The server REJECTS a
        # push carrying no sessions (correctly — it is not a transcript push), so
        # emitting one would turn the ordinary steady state into an HTTP 400 on
        # every tick, i.e. a feeder that reports failure precisely when it is
        # working perfectly.
        return 10

    json.dump(payload, sys.stdout, separators=(",", ":"))
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
