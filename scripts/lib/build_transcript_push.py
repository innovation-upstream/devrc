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
import sys
import time
from pathlib import Path

# 🔴 THE SHARED ENUMERATOR, NOT A HAND-ROLLED WALK. The first version of this file
# open-coded `projects_dir.iterdir()` + `d.glob("*.jsonl")`, and
# `scripts/tests/test_transcript_search.py::test_the_jsonl_glob_site_ledger_is_
# pinned_two_way` caught it — that ledger SCANS the tree for walk sites rather
# than naming files, precisely so a fourth hand-rolled walk cannot pass unseen.
#
# 🔴 AND IT IS A CORRECTNESS FIX, NOT ONLY A HYGIENE ONE. `is_corpus_member`
# excludes `subagents/`, which holds transcripts that are NOT resumable sessions.
# Measured on this host 2026-09-04: 4,884 of the 5,788 `.jsonl` files under
# `~/.claude/projects` — 84% — live there. My walk happened to miss them because
# it only descended one level, i.e. it was right BY ACCIDENT of its depth rather
# than by any rule; a later "let's make this recursive" edit would have started
# feeding clawgate thousands of rows that no attention entry and no tmux window
# can ever join to, and nothing would have gone red.
#
# This module's dir is on sys.path when the file is run as a script, so the
# import needs no path juggling; `transcript_search` is pure stdlib.
from transcript_search import iter_transcripts

# 🔴 THE ONE PLACE THE STORED FORM IS DEFINED, and the server recomputes it
# rather than trusting what we send (see internal/transcript.NormalizePush). Two
# implementations of "the hash" is how a dedupe protocol silently degrades into
# "always push" or, worse, "never push".
def hash_tail(tail: bytes) -> str:
    return hashlib.sha256(tail).hexdigest()


def read_tail(path: Path, max_bytes: int) -> tuple[str, bool, int] | None:
    """Return (tail_text, truncated, file_bytes) for one transcript, or None.

    🔴 `file_bytes` IS WHERE THIS TAIL ENDS IN THE FILE, AND IT IS THE DELTA
    STREAM'S RESUME CURSOR. The server stores it as the position its stored tail
    corresponds to. Without it a bulk push REPLACES the tail while leaving the
    cursor describing the tail it replaced, and the next delta appends onto a base
    that no longer matches its offset — a splice, stored, with nothing to indicate
    it. See migration 0034 on the clawgate side.

    🔴 IT IS DERIVED FROM WHAT WAS ACTUALLY READ, NOT FROM `size`, BECAUSE THE
    FILE IS BEING APPENDED TO. `size` is a stat taken before the read; the bytes
    that came back can end past it. `start + len(raw)` is exact either way.

    🔴 AND IT IS RETURNED AS 0 — MEANING *UNKNOWN* — WHENEVER THE DECODED TAIL
    RE-ENCODES LONGER THAN THE BYTES IT CAME FROM. `errors="replace"` turns one
    bad byte into a three-byte U+FFFD, so a corrupt transcript can produce a tail
    LARGER than its own file. The server rejects a fileBytes smaller than the tail
    beside it (a cursor pointing before the start of the stored tail is a splice
    waiting to happen), so claiming the honest number there would fail the WHOLE
    atomic push and take every other session in the request with it. Unknown makes
    the stream reseed that one session and costs nothing else.

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
        start = 0
        with path.open("rb") as fh:
            if size > max_bytes:
                start = size - max_bytes
                fh.seek(start)
                truncated = True
            else:
                truncated = False
            raw = fh.read(max_bytes)
    except OSError:
        return None

    file_end = start + len(raw)

    if truncated:
        nl = raw.find(b"\n")
        if nl == -1:
            # One record longer than the whole window: there is no boundary to
            # cut on. Send nothing rather than a fragment that parses to zero
            # events while claiming to be a conversation.
            return None
        raw = raw[nl + 1 :]

    text = raw.decode("utf-8", errors="replace")
    if len(text.encode("utf-8")) > file_end:
        file_end = 0  # UNKNOWN — see the docstring.
    return text, truncated, file_end


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

    The WALK is `transcript_search.iter_transcripts`, never a local glob — see
    the import for the ledger that enforces that and for the 84%-of-files
    correctness reason.
    """
    if not projects_dir.exists():
        print(f"cannot list {projects_dir}: no such directory", file=sys.stderr)
        raise SystemExit(1)

    cutoff = time.time() - max_age_hours * 3600
    found: list[tuple[float, Path]] = []
    for f in iter_transcripts(projects_dir):
        try:
            st = f.stat()
        except OSError:
            # A transcript can vanish between the walk and the stat (a session
            # cleaned up mid-run). Skipping one file must never fail the push.
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
    # 🔴 THE AGGREGATE, WHICH THE SESSION COUNT DOES NOT IMPLY. The server bounds
    # the total tail bytes in one push (MaxPushTailBytes) as well as the count, and
    # a rejection changes nothing server-side — so a builder that only counted
    # sessions would, once the count rose from 6 to 48, start producing pushes that
    # are refused on EVERY tick while looking correctly configured. Stopping here
    # means a very busy host catches up over several ticks instead.
    max_push_bytes = getattr(args, "max_push_bytes", 0) or 0
    total_bytes = 0
    claimed: set[str] = set()
    for path in candidates(projects_dir, args.max_age_hours, args.max_candidates):
        if len(sessions) >= args.max_sessions:
            break
        if max_push_bytes and total_bytes >= max_push_bytes:
            break
        # The session id IS the filename stem — that is how Claude Code writes
        # them, and it is the same id the attention queue and session-manager's
        # `claude_session_id` carry, which is what makes the join work at all.
        #
        # 🔴 STRIPPED AND DE-DUPLICATED, AND THE BLAST RADIUS HERE IS THE WHOLE
        # HOST — WHICH IS THE OPPOSITE OF WHAT THE PROSE ELSEWHERE ASSUMED. The
        # delta stream grew this guard first, and there a duplicate costs ONE
        # session. Here `NormalizePush` rejects THE ENTIRE PUSH on a duplicate or
        # an empty id, a rejection stores nothing, so the digest never matches,
        # so the same poisoned batch is re-sent on every tick — permanently, for
        # every session on this host. And this is the feed the whole streaming
        # design designates as the RECONCILER.
        #
        # Two ways two files produce one id, both closed here as they are there:
        # the same <uuid>.jsonl under two project directories, and — because the
        # server TrimSpaces the id and this side did not — "abc.jsonl" beside
        # "abc .jsonl".
        #
        # ⚠ RAISING `MAX_PER_PUSH` FROM 6 TO 48 WIDENED THIS. Six candidates
        # rarely collide; forty-eight over a 24h window is a different exposure,
        # and the change that widened it is the one that had to close it.
        # Measured over the live corpus: 962 files, 0 duplicates after TrimSpace
        # — so this is not live, and the cost of it becoming live is total.
        # Candidates arrive newest-first, so the survivor is the freshest file.
        session_id = path.stem.strip()
        if not session_id or session_id in claimed:
            continue
        claimed.add(session_id)

        result = read_tail(path, args.tail_bytes)
        if result is None:
            continue
        text, truncated, file_bytes = result
        if not text.strip():
            continue

        encoded = text.encode("utf-8")
        # 🔴 THE `and sessions` CONJUNCT IS AN EXEMPTION, AND AN EARLIER COMMENT
        # HERE CLAIMED THE OPPOSITE — "a single oversized session cannot be
        # admitted by arriving first". It can, deliberately: when the push is
        # otherwise EMPTY, a session larger than the whole budget is sent anyway,
        # because it is also the newest and would otherwise be dropped first on
        # every tick, for ever. `test_ONE_oversized_session_alone_is_still_sent_
        # rather_than_dropped_for_ever` exists to guarantee exactly that.
        #
        # What the ordering does buy is the OTHER case: once something is in the
        # push, a session that would overflow the budget is deferred to the next
        # tick rather than truncated.
        #
        # ⚠ AND THE BUDGET IS CHARGED BEFORE THE DEDUPE SKIP BELOW, so a large
        # UNCHANGED session — which contributes nothing to the payload — can end
        # the loop and defer sessions behind it by one tick. Measured and
        # deliberate: moving the check after the skip would mean hashing every
        # candidate before knowing whether there is room, and one tick of
        # deferral on a recency-ordered list is cheaper than that. With the
        # shipped values (192 KiB tail against a 3 MiB budget) it needs 16
        # unchanged sessions in one window to bite at all.
        if max_push_bytes and sessions and total_bytes + len(encoded) > max_push_bytes:
            break
        digest = hash_tail(encoded)
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
                # 🔴 THE SEAM WITH THE DELTA STREAM. See read_tail.
                #
                # ⚠ AN EARLIER VERSION OF THIS COMMENT SAID "this is `size`", and
                # read_tail's own 🔴 docstring says the opposite in capitals: it
                # is derived from what was actually READ, not from the stat,
                # because the file is being appended to while it is read. `size`
                # is not even in scope here. The reason it is not len(text) is
                # right, though: the tail is a decoded string and
                # `errors="replace"` can make it a different length from the
                # bytes it came from — which is also why read_tail reports 0 =
                # UNKNOWN when that inflation happens.
                "fileBytes": file_bytes,
            }
        )
        total_bytes += len(encoded)

    return {"host": args.host, "sessions": sessions}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--projects-dir", required=True)
    ap.add_argument("--digest", required=True)
    ap.add_argument("--host", required=True)
    ap.add_argument("--tail-bytes", type=int, required=True)
    ap.add_argument("--max-sessions", type=int, required=True)
    # Optional so an older caller keeps working: 0 means "no aggregate bound",
    # which is exactly the behaviour before this flag existed.
    ap.add_argument("--max-push-bytes", type=int, default=0)
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
