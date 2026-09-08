"""transcript_stream — the host half of clawgate's transcript DELTA STREAM.

WHAT THIS IS
------------
`transcript-push.sh` feeds clawgate's transcript read model by REPLACING each
session's stored tail every five minutes. This module feeds the same read model
by APPENDING, on the poll the host agent already runs, so a line written into a
`.jsonl` shows on `/session/<id>` in seconds rather than minutes.

🔴 IT IS THE FAST PATH, NEVER THE ONLY PATH. The five-minute bulk push stays
exactly as it was and remains the RECONCILER: it is what repairs anything this
missed while the agent was down, the laptop asleep, or the clawgate pod
mid-redeploy. A stream-only design loses every line written during a deploy
window and has no mechanism that could ever notice. Do not delete the timer
because "the stream covers it".

🔴 NO NEW CONNECTION AND NO NEW PORT. This runs inside `tmux-reply-agent`, which
already holds an OUTBOUND poll to clawgate every ~5s. Nothing listens on either
host, and no credential for either host lives in the pod. What it adds is one
more request per existing poll cycle — not a second long-lived connection, and
not a second unit.

🔴 THE CURSOR IS THE SERVER'S, NOT OURS. Every response carries the authoritative
offset for every session in the frame, accepted or refused, and this module
ADOPTS it rather than computing its own by adding len(data). That is the whole
resume protocol: a clawgate redeploy, a lost response, or a bulk push landing
between two deltas all leave the host one poll out of step, and one poll later it
is back in step having neither duplicated nor skipped a line. Computing the
cursor locally would be right until the one time it was not, and then the two
sides would be permanently spliced with nothing saying so.

🔴 A DELTA IS WHOLE RECORDS WHEREVER THERE IS A RECORD BOUNDARY TO CUT ON. Every
read is cut at a NEWLINE, which does two jobs at once: the server never stores a
leading JSON fragment, and a multi-byte rune can never be split across two frames
(a newline is ASCII, so a cut there is always a rune boundary). The trailing
partial line waits for the next poll.

⚠ AND WHERE THERE IS NO BOUNDARY IN THE WINDOW, IT CUTS ON A RUNE BOUNDARY
INSTEAD RATHER THAN DECLINING. A single tool-result record on this fleet has been
measured well past 256 KiB — far beyond one delta — so "wait for a newline" is not
a delay there, it is a PERMANENT STALL: every poll re-reads the same window, finds
no newline, and declines. That is what an earlier revision did, on both the append
and the reseed path, and it silently confined the biggest sessions to the
five-minute feed. Streaming such a record in pieces leaves the stored tail ending
mid-record for a few seconds, which the parser already tolerates.

🔴 THE BYTE COUNTS ON BOTH SIDES MUST AGREE EXACTLY, so this decodes STRICTLY.
The server advances its cursor by the byte length of the text it received; this
advances the file position by the bytes it read. `errors="replace"` would turn
one bad byte into a three-byte U+FFFD, the two counts would diverge, and — because
the host re-reads from the server's offset — the SAME bad byte would be re-sent
and re-refused on every poll for ever. A session whose bytes will not decode is
therefore SKIPPED by the stream entirely and left to the bulk reconciler, which
does use `errors="replace"` and replaces the whole tail. Cutting on newlines
makes this reachable only by real corruption, not by a cut point.

Scope: this is the operator's own clawgate feed. It is deliberately NOT the team
Cairn / handoff session-shipping path, which is opt-in and must stay so.
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import os

_HERE = os.path.dirname(os.path.abspath(__file__))


def _load_transcript_search(path: str = None):
    """Load `scripts/lib/transcript_search.py` by EXPLICIT PATH.

    🔴 THE SHARED ENUMERATOR, NOT A HAND-ROLLED WALK, AND BY PATH BECAUSE THIS
    MODULE IS ITSELF LOADED BY PATH. `tmux-reply-agent` imports this file with a
    SourceFileLoader, so `scripts/lib` is not on sys.path and a plain
    `from transcript_search import ...` would fail at runtime while working
    perfectly under pytest — the worst possible split.

    🔴 AND IT IS A CORRECTNESS FIX, NOT ONLY LEDGER HYGIENE. `is_corpus_member`
    excludes `subagents/`, which holds transcripts that are NOT resumable
    sessions: measured on this host 2026-09-04, 4,884 of 5,788 `.jsonl` files —
    84% — live there. A hand-rolled `os.walk` for `*.jsonl` would stream thousands
    of rows that no attention entry and no tmux window can ever join to, at the
    fastest cadence in the system.
    """
    path = path or os.path.join(_HERE, "transcript_search.py")
    loader = importlib.machinery.SourceFileLoader("_ts_transcript_search", path)
    spec = importlib.util.spec_from_loader("_ts_transcript_search", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


_SEARCH = _load_transcript_search()

# ── the bounds, mirrored from the server ─────────────────────────────────────
# 🔴 THESE MUST STAY UNDER THE SERVER'S OWN, NOT AT THEM. The server enforces
# MaxDeltaBytes=65536, MaxFrameBytes=524288 and MaxDeltaSessionsPerFrame=64 and
# REJECTS a frame that exceeds any of them; a rejection changes nothing
# server-side, so a client tuned exactly to a limit turns any rounding
# disagreement into a stream that fails every poll while looking correctly
# configured. Same rule, and the same reason, as transcript-push.sh's header.
MAX_DELTA_BYTES = 48 * 1024        # server cap 64 KiB
MAX_FRAME_BYTES = 384 * 1024       # server cap 512 KiB
MAX_SESSIONS_PER_FRAME = 48        # server cap 64

# 🔴 THE BACKPRESSURE BOUND, AND THE ONLY PLACE UNBOUNDED LAG IS ANSWERED. A
# session can outrun the stream: MAX_DELTA_BYTES per poll is ~9.6 KB/s at the
# agent's 5s cadence, and a burst of tool output beats that. Replaying an
# arbitrarily long backlog would make the stream slower the further behind it
# got — the shape where a queue never drains. Past this much lag the host SKIPS
# FORWARD with a reset instead, which is lossy by construction and repaired by
# the bulk reconciler on its next tick.
MAX_LAG_BYTES = 1024 * 1024

# How much of a file a reseed carries.
#
# 🔴 IT IS THE PER-DELTA CAP, NOT THE SERVER'S 256 KiB TAIL CAP, AND THE FIRST
# DRAFT OF THIS FILE HAD IT WRONG. A reseed IS a delta — it travels in the same
# `data` field and is measured by the same MaxDeltaBytes guard — so a 192 KiB
# reseed would be REJECTED by the server, and the rejection would present as "the
# stream never recovers from a rotation". The consequence of the correct value is
# real and accepted: immediately after a reseed the view holds ~48 KiB of tail
# rather than the full 256 KiB, until the bulk reconciler's next tick restores
# the wider window. That is the reconciler earning its keep, not a gap.
RESEED_TAIL_BYTES = MAX_DELTA_BYTES

# How far back a transcript is considered at all, and how many files are stat'd.
# Mirrors transcript-push.sh's own window so the two feeders agree about which
# sessions exist.
MAX_AGE_HOURS = 24.0
MAX_CANDIDATES = 200

# The reasons a session is not in a frame. Returned as counters rather than
# logged per session: this runs every 5 seconds and a per-session line would bury
# every real event in the journal.
SKIP_UNCHANGED = "unchanged"
SKIP_DUPLICATE_ID = "duplicate-session-id"
SKIP_NO_BOUNDARY = "no-record-boundary"
SKIP_UNDECODABLE = "undecodable"
SKIP_UNREADABLE = "unreadable"


class StreamState:
    """The host's memory between polls: a cursor and an inode per session.

    🔴 THE INODE IS NOT REDUNDANT WITH THE SIZE. A rotated or recreated file can
    be LARGER than the old one at the moment it is checked, so a size comparison
    alone reports "grew" and the next delta appends the head of a new file onto
    the tail of an old one. The inode is the only thing that distinguishes "this
    file grew" from "this is a different file at the same path".

    🔴 CURSORS ARE HYDRATED FROM THE SERVER, ONCE, AND NEVER PERSISTED TO DISK. A
    host-side cache of "what I sent last time" is wrong whenever the two sides
    disagree, and they disagree in both directions — a database restore leaves
    the server empty while the host believes everything is delivered. Asking the
    server is correct in both directions and needs no state on this machine.
    """

    #: Bounds `inodes`, which — unlike `cursors` — is never wholesale-replaced by
    #: a re-hydration, so in a process that runs for weeks it would otherwise grow
    #: one entry per session ever seen. Eviction is oldest-first and costs the
    #: evicted session exactly one reseed, because a missing inode is a first
    #: sighting. The bound is generous against the measured rate (~1,465 sessions
    #: a week fleet-wide) and against the 24h candidate window, so in practice
    #: nothing is ever evicted; it exists so that "in practice" is not the only
    #: thing standing between this and a slow leak.
    INODE_LIMIT = 8192

    def __init__(self) -> None:
        self.cursors: dict[str, int | None] = {}
        self.inodes: dict[str, int] = {}
        self.hydrated = False

    def note_inode(self, session_id: str, inode: int) -> None:
        """Record a file identity, bounded."""
        self.inodes[session_id] = inode
        while len(self.inodes) > self.INODE_LIMIT:
            # dicts preserve insertion order, so this evicts the oldest.
            self.inodes.pop(next(iter(self.inodes)))


def iter_candidates(projects_dir, max_age_hours=MAX_AGE_HOURS, limit=MAX_CANDIDATES, now=None):
    """Recently-modified transcripts, newest first, bounded.

    Recency is the only selector, for the reason build_transcript_push.py gives:
    asking which sessions have a live tmux window would couple this to a
    collector that runs on one host, and would stop feeding a session the moment
    its window closed — which is exactly when someone wants to read what it did.

    The WALK is `transcript_search.iter_transcripts`, never a local glob — see
    `_load_transcript_search` for the ledger that enforces that and for the
    84%-of-files correctness reason.
    """
    import time as _time

    if now is None:
        now = _time.time()
    cutoff = now - max_age_hours * 3600
    found: list[tuple[float, str]] = []
    for p in _SEARCH.iter_transcripts(projects_dir):
        path = str(p)
        try:
            st = os.stat(path)
        except OSError:
            # A transcript can vanish between the walk and the stat. Skipping
            # one file must never fail the round.
            continue
        if st.st_mtime < cutoff:
            continue
        found.append((st.st_mtime, path))
    found.sort(key=lambda pair: pair[0], reverse=True)
    return [p for _, p in found[:limit]]


def _decode_exact(raw: bytes) -> str | None:
    """Decode `raw` so the result re-encodes to exactly `raw`, or None.

    See the module header: a lossy decode makes the two sides' byte counts
    diverge and the same bytes are then re-sent and re-refused for ever.
    """
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _read_append(path: str, offset: int, budget: int):
    """Whole records starting at `offset`, at most `budget` bytes.

    Returns (start, byte_length, text) on success, or a SKIP_* reason string.

    🔴 IT RETURNS THE REASON RATHER THAN None, because the three ways this can
    decline are three different facts about the host and one of them is a real
    defect signal. "Only a partial line has landed" is the ordinary steady state;
    "these bytes will not decode" means a transcript is corrupt and the stream is
    permanently deferring that session to the reconciler. Counting them as one
    number would hide the second inside the first, which happens every few
    seconds.
    """
    try:
        with open(path, "rb") as fh:
            fh.seek(offset)
            raw = fh.read(budget)
    except OSError:
        return SKIP_UNREADABLE
    nl = raw.rfind(b"\n")
    if nl >= 0:
        raw = raw[: nl + 1]
    elif len(raw) < budget:
        # The read reached EOF without a newline: this is just a line still being
        # written. Sending it would put a fragment in the stored tail; it costs
        # one poll to wait.
        return SKIP_NO_BOUNDARY
    else:
        # 🔴 THE WINDOW IS FULL AND HOLDS NO BOUNDARY, SO THIS RECORD IS LONGER
        # THAN ONE DELTA AND WAITING CAN NEVER HELP. An earlier revision returned
        # SKIP_NO_BOUNDARY here, which STALLED that session's append path FOR
        # EVER: every poll re-read the same 48 KiB, found no newline, and declined
        # — silently, at ~830 MB/day of wasted reads, with the bulk reconciler as
        # the only feed. The lag escape could not save it either, because a record
        # between MAX_DELTA_BYTES and MAX_LAG_BYTES never reaches that threshold.
        #
        # 🔴 AND IT IS NOT AN EDGE CASE ON THIS FLEET. stream.go's own trimToTail
        # records the measurement: "a single tool-result record on this fleet has
        # been measured well past 256 KiB". So the sessions with the biggest tool
        # output — the ones an operator most wants seconds-fresh — were exactly
        # the ones that fell back to the five-minute path.
        #
        # Cutting on a RUNE boundary instead streams the record in pieces. The
        # stored tail then ends mid-record for a few seconds, which the parser
        # already tolerates (it counts a partial record rather than failing), and
        # the next delta completes it. A transient partial record beats a
        # permanent stall.
        raw = _cut_at_rune_boundary(raw)
        if not raw:
            return SKIP_NO_BOUNDARY
    text = _decode_exact(raw)
    if text is None:
        return SKIP_UNDECODABLE
    return offset, len(raw), text


def _read_reseed(path: str, size: int, window: int):
    """The tail of a file as a reseed: (start_offset, byte_length, text), or a
    SKIP_* reason string.

    The leading partial record is DROPPED where there is one to drop to, and the
    trailing one is left for the next poll, so a reseed is normally whole records
    exactly like an append is.

    🔴 "NORMALLY". WHERE THE WINDOW HOLDS NO BOUNDARY AT ALL IT SENDS THE BYTES
    ANYWAY, cut on a rune boundary. Returning SKIP here — which an earlier
    revision did, on both the leading and the trailing cut — made a file whose
    last record exceeds the window a PERMANENT RESEED LOOP: plan_frame only
    leaves the reseed branch once a cursor is adopted, and no cursor can be
    adopted while the reseed declines. Measured: three identical rounds, zero
    deltas, forever.

    ⚠ Note the asymmetry it created with the reconciler, which is why the bug was
    invisible: build_transcript_push.read_tail uses a 192 KiB window against this
    48 KiB one, so for last-records between those two sizes the five-minute push
    worked perfectly while the stream looped.
    """
    start = max(0, size - window)
    try:
        with open(path, "rb") as fh:
            fh.seek(start)
            raw = fh.read(window)
    except OSError:
        return SKIP_UNREADABLE
    if start > 0:
        nl = raw.find(b"\n")
        if 0 <= nl < len(raw) - 1:
            start += nl + 1
            raw = raw[nl + 1 :]
        else:
            # 🔴 EITHER THERE IS NO BOUNDARY IN THE WINDOW, OR THE ONLY ONE IS ITS
            # LAST BYTE — AND DROPPING TO THAT WOULD LEAVE NOTHING. Both happen
            # for the same fleet-real reason: a record larger than the window,
            # whose terminating newline is the file's last byte. An earlier
            # revision dropped unconditionally, produced an EMPTY reseed, returned
            # SKIP, and looped for ever.
            #
            # So the reseed starts MID-RECORD. The server marks the tail truncated
            # and the parser counts a leading partial, which is exactly what both
            # are for — but the start must still be a RUNE boundary, or
            # _decode_exact rejects the whole window and the loop comes back by
            # another door.
            skipped_bytes, raw = _align_to_rune_start(raw)
            start += skipped_bytes
    nl = raw.rfind(b"\n")
    if nl >= 0:
        raw = raw[: nl + 1]
    else:
        raw = _cut_at_rune_boundary(raw)
    if not raw:
        return SKIP_NO_BOUNDARY
    text = _decode_exact(raw)
    if text is None:
        return SKIP_UNDECODABLE
    return start, len(raw), text


def _align_to_rune_start(raw: bytes) -> tuple[int, bytes]:
    """Drop LEADING UTF-8 continuation bytes; return (how many, the rest).

    The mirror of `_cut_at_rune_boundary`, for the one place a read can begin at
    an arbitrary byte offset: a reseed seeks to `size - window`, which lands
    inside a rune two times in three on CJK text. The count is returned because
    the caller must move its `offset` by exactly the bytes it dropped — the
    cursor is a FILE position, and an offset that does not match the data sent is
    the splice this whole protocol exists to prevent.
    """
    i = 0
    while i < len(raw) and raw[i] & 0xC0 == 0x80:
        i += 1
    return i, raw[i:]


def _cut_at_rune_boundary(raw: bytes) -> bytes:
    """Drop a trailing INCOMPLETE UTF-8 sequence, and nothing else.

    🔴 THE CUT POINT IS A BYTE OFFSET, SO IT CAN LAND INSIDE A MULTI-BYTE RUNE.
    That is the whole reason the normal path cuts on a newline (an ASCII byte is
    always a rune boundary). Where there is no newline to cut on, this is what
    keeps `_decode_exact` from failing on a perfectly good chunk whose last three
    bytes are half a character — and, downstream, what keeps the two sides' byte
    counts equal, since a lossy decode would diverge them for ever.

    ⚠ IT REMOVES AT MOST 3 BYTES, and it removes them only when the final
    sequence is genuinely incomplete. A complete trailing rune is left alone; an
    invalid lead byte is left alone too, so `_decode_exact` still rejects real
    corruption rather than having it silently trimmed away.
    """
    n = len(raw)
    for back in range(1, 5):
        if back > n:
            return raw
        b = raw[n - back]
        if b & 0xC0 == 0x80:
            continue  # a continuation byte; keep walking back to the lead
        if b < 0x80:
            need = 1
        elif b & 0xE0 == 0xC0:
            need = 2
        elif b & 0xF0 == 0xE0:
            need = 3
        elif b & 0xF8 == 0xF0:
            need = 4
        else:
            # Not a legal lead byte. Leave it: this is corruption, and
            # _decode_exact must be the thing that says so.
            return raw
        if back == need:
            return raw  # the last sequence is complete
        return raw[: n - back]
    return raw


def session_id_of(path: str) -> str:
    """The Claude Code session id IS the filename stem."""
    return os.path.basename(path)[: -len(".jsonl")] if path.endswith(".jsonl") else ""


def project_of(path: str) -> str:
    """The project LABEL, spelled exactly as the bulk feeder spells it.

    ⚠ A LABEL, NOT AN IDENTIFIER. Claude Code names the directory after a
    slugified cwd, and the slug is lossy — a literal `-` is indistinguishable
    from a `/` — so reconstructing a path from it would ship a confidently wrong
    answer. `cwd` is left to the server, which reads it out of the records.
    """
    parent = os.path.basename(os.path.dirname(path))
    return parent.lstrip("-").split("-")[-1] or parent


def plan_frame(
    projects_dir,
    state: StreamState,
    *,
    max_delta_bytes: int = MAX_DELTA_BYTES,
    max_frame_bytes: int = MAX_FRAME_BYTES,
    max_sessions: int = MAX_SESSIONS_PER_FRAME,
    max_lag_bytes: int = MAX_LAG_BYTES,
    reseed_tail_bytes: int = RESEED_TAIL_BYTES,
    max_age_hours: float = MAX_AGE_HOURS,
    max_candidates: int = MAX_CANDIDATES,
    now=None,
) -> tuple[list[dict], dict[str, int]]:
    """Build the deltas this host would send now, plus a counter of what it skipped.

    🔴 IT MUTATES `state.inodes` BUT NEVER `state.cursors`. The cursor is the
    SERVER's answer and is only ever adopted from a response — see the module
    header. Advancing it here would be the local computation this design exists
    to avoid, and it would advance even for a delta the server refused.
    """
    deltas: list[dict] = []
    skipped: dict[str, int] = {}
    claimed: set[str] = set()

    def skip(reason: str) -> None:
        skipped[reason] = skipped.get(reason, 0) + 1

    # 🔴 ONE PLACE APPENDS A DELTA AND SPENDS THE BUDGET, because the accumulator
    # used to be written out in BOTH arms and only one of them was reachable by a
    # test. Deleting `budget -= nbytes` from the APPEND arm survived the whole
    # suite: the aggregate test exercises reseeds, so the reseed copy was pinned
    # and the append copy was not. With the append arm unbounded, 48 appending
    # sessions could build a 2.25 MiB frame against a 512 KiB server bound — a
    # 413 on every poll, permanently. The single-expression `per_session` below
    # closed half of this class; this closes the other half.
    def emit(sid, path, st, start, nbytes, text, reset):
        nonlocal budget
        deltas.append({
            "sessionId": sid,
            "project": project_of(path),
            "updatedAt": _rfc3339(st.st_mtime),
            "offset": start,
            "data": text,
            "reset": reset,
        })
        budget -= nbytes

    budget = max_frame_bytes
    for path in iter_candidates(projects_dir, max_age_hours, max_candidates, now=now):
        # 🔴 ONE EXPRESSION COMPUTES THE REMAINING ALLOWANCE AND BOTH THE BREAK
        # AND THE READ USE IT. An earlier revision had `budget <= 0` in the break
        # AND `min(max_delta_bytes, budget)` at the read — two spellings of one
        # rule, and the redundancy made the aggregate bound UNTESTABLE: deleting
        # the break left the clamp holding it, so a mutation of either survived
        # while the other silently covered. (The clamp covered it by accident, at
        # that: a negative budget made `min` negative, which made the file read
        # return nothing, which skipped the session — a bound enforced by a
        # seek-past-EOF.) With one expression there is one thing to break.
        per_session = min(max_delta_bytes, budget)
        if len(deltas) >= max_sessions or per_session <= 0:
            break
        sid = session_id_of(path)
        if not sid:
            continue
        # 🔴 ONE DUPLICATE ID WOULD KILL THIS HOST'S STREAM PERMANENTLY. The
        # server REJECTS THE WHOLE FRAME with a 400 when a session appears twice
        # (NormalizeDeltas, and rightly — the deltas would have to be applied in
        # an order nobody declared, on a protocol whose correctness rests on
        # offsets matching exactly). The agent then logs once, clears `hydrated`,
        # and sends THE SAME FRAME on the next poll: nothing in the loop removes
        # the offending session, so the stream is dead for this host until
        # somebody deletes a file.
        #
        # Two ways two files produce one id, and neither is exotic: the same
        # <uuid>.jsonl under two project directories, and — because the server
        # TrimSpaces the id and this side did not — "abc.jsonl" beside
        # "abc .jsonl". Measured on this host: 0 duplicates in 949 files, so it
        # is not live; the cost of it becoming live is total, and the fix is
        # this branch. Candidates arrive newest-first, so the survivor is the
        # freshest file.
        sid = sid.strip()
        if not sid or sid in claimed:
            skip(SKIP_DUPLICATE_ID)
            continue
        claimed.add(sid)
        try:
            st = os.stat(path)
        except OSError:
            skip(SKIP_UNREADABLE)
            continue

        offset = state.cursors.get(sid)
        known_inode = state.inodes.get(sid)
        # 🔴 A FILE THIS PROCESS HAS NEVER STAT'd IS RESEEDED EVEN WHEN THE SERVER
        # GAVE US AN OFFSET FOR IT. The inode is what distinguishes "this file
        # grew" from "this is a different file at the same path", and on the first
        # sighting there is no inode to compare against — so an append would be
        # taking the server's word about a file that could have been rotated while
        # this agent was down. The cost is exactly one reseed per session per
        # agent lifetime; the alternative is a splice that nothing detects.
        first_sighting = known_inode is None
        rotated = known_inode is not None and known_inode != st.st_ino
        truncated = offset is not None and st.st_size < offset
        lagging = offset is not None and (st.st_size - offset) > max_lag_bytes

        if offset is None or first_sighting or rotated or truncated or lagging:
            # 🔴 EVERY ONE OF THESE FIVE IS A RESEED, AND THEY ARE NOT THE SAME
            # EVENT. Unknown cursor = a fresh server or a session it has never
            # seen. First sighting = this process cannot yet vouch for the file's
            # identity. Rotated = a different file at the same path. Truncated =
            # the file shrank. Lagging = the host cannot catch up by replaying.
            # They share a REMEDY, not a cause, and collapsing them in the code is
            # fine only because the remedy is genuinely identical: send the end of
            # the file and let the server replace what it has.
            got = _read_reseed(path, st.st_size, min(reseed_tail_bytes, per_session))
            state.note_inode(sid, st.st_ino)
            if isinstance(got, str):
                skip(got)
                continue
            emit(sid, path, st, *got, reset=True)
            continue

        state.note_inode(sid, st.st_ino)
        if st.st_size == offset:
            skip(SKIP_UNCHANGED)
            continue

        got = _read_append(path, offset, per_session)
        if isinstance(got, str):
            skip(got)
            continue
        emit(sid, path, st, *got, reset=False)

    return deltas, skipped


def _rfc3339(epoch: float) -> str:
    import time as _time

    return _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime(epoch))


def adopt_cursors(state: StreamState, cursors) -> int:
    """Take the server's authoritative offsets. Returns how many were adopted.

    🔴 A `null` OFFSET IS ADOPTED AS None, NOT IGNORED. It means the server holds
    a tail whose file position it cannot state, and the correct next action is a
    RESEED — which `plan_frame` does exactly when the cursor is None. Skipping the
    null would leave the host's stale offset in place and every subsequent delta
    would be refused, for ever, with nothing saying why.
    """
    n = 0
    if not isinstance(cursors, list):
        return 0
    for c in cursors:
        if not isinstance(c, dict):
            continue
        sid = c.get("sessionId")
        if not isinstance(sid, str) or not sid:
            continue
        off = c.get("offset")
        if off is None:
            state.cursors[sid] = None
        elif isinstance(off, int) and not isinstance(off, bool) and off >= 0:
            state.cursors[sid] = off
        else:
            # A malformed offset is worse than none: adopting a wrong number
            # would splice. Treat it as unknown and reseed.
            state.cursors[sid] = None
        n += 1
    return n


def run_round(post, get, host: str, state: StreamState, projects_dir, **kw) -> dict:
    """One streaming round. Returns counters; raises nothing the caller must catch
    beyond what `post`/`get` raise.

    `post(path, payload) -> dict` and `get(path) -> dict` are supplied by the
    caller so this module does no HTTP of its own and is testable without a
    server.
    """
    if not state.hydrated:
        # One hydration per agent lifetime. A failure here leaves `hydrated`
        # False, so the next poll retries; it must not be swallowed into "the
        # server has nothing", which would make every session reseed on every
        # poll — the expensive direction, at exactly the moment the server is
        # least able to absorb it.
        doc = get("/api/transcripts/stream/cursors")
        # 🔴 REPLACE THE MAP, DO NOT MERGE INTO IT. The server is authoritative,
        # so a session ABSENT from its listing has no server-side cursor — and
        # merging would leave this host's stale offset in place for exactly that
        # session. The case is not hypothetical: a database restore, or a wiped
        # deployment, leaves the server empty while the host believes everything
        # is delivered. Merging makes that take TWO polls to recover (append,
        # refused as unknown, then reseed); replacing makes it take one, and more
        # importantly makes the host's map a copy of the server's rather than a
        # union of the server's and its own history.
        state.cursors = {}
        adopt_cursors(state, (doc or {}).get("sessions"))
        state.hydrated = True

    deltas, skipped = plan_frame(projects_dir, state, **kw)
    if not deltas:
        # 🔴 SEND NOTHING RATHER THAN AN EMPTY FRAME. The server REJECTS a frame
        # with no sessions (correctly — it is not a delta frame), so posting one
        # would turn the ordinary steady state into an HTTP 400 on every poll,
        # i.e. a stream that reports failure precisely when it is working.
        return {"sent": 0, "applied": 0, "refused": 0, "skipped": skipped}

    doc = post("/api/transcripts/stream", {"host": host, "sessions": deltas}) or {}
    adopt_cursors(state, doc.get("cursors"))

    applied = 0
    for c in doc.get("cursors") or []:
        if isinstance(c, dict) and c.get("reason") == "accepted":
            applied += 1
    return {
        "sent": len(deltas),
        "applied": applied,
        "refused": len(deltas) - applied,
        "skipped": skipped,
    }
