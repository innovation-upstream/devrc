"""Tests for scripts/lib/transcript_stream.py — the host half of clawgate's
transcript delta stream.

Every fixture is SYNTHETIC. What these tests need is the record shape and the
byte arithmetic, not captured text.

🔴 THE SERVER IS SIMULATED FROM THE PROTOCOL, NOT MOCKED TO AGREE. `FakeServer`
below re-implements the clawgate merge rules — exact-offset-or-refuse, reset
replaces, refusal returns the STORED offset — because a fake that accepted
everything would make every resume test here vacuous. Where the two sides could
drift, the drift shows up as one of these tests failing rather than as a stream
that silently splices in production.
"""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
MODULE = REPO / "scripts" / "lib" / "transcript_stream.py"


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ts = _load(MODULE, "transcript_stream_under_test")


def line(n: int) -> str:
    return (
        '{"type":"assistant","seq":%d,"message":{"role":"assistant",'
        '"content":[{"type":"text","text":"turn %d"}]}}\n' % (n, n)
    )


def write_session(root: Path, project: str, sid: str, body: str) -> Path:
    d = root / project
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{sid}.jsonl"
    p.write_text(body, encoding="utf-8")
    return p


def append(path: Path, body: str) -> None:
    with path.open("a", encoding="utf-8") as fh:
        fh.write(body)


class FakeServer:
    """clawgate's delta merge, re-implemented from the protocol.

    Stores (tail, offset) per session and answers with the authoritative cursor
    for every session in a frame — accepted or refused.
    """

    MAX_TAIL = 256 * 1024

    def __init__(self):
        self.tails: dict[str, str] = {}
        self.offsets: dict[str, int | None] = {}
        self.hosts: dict[str, str] = {}
        self.frames: list[dict] = []

    def bulk_push(self, session_id: str, tail: str, file_bytes, host: str = "workbench"):
        """The 5-minute reconciler's write, modelled.

        🔴 THIS IS THE WRITER THE FAKE DID NOT HAVE, AND ITS ABSENCE HID A
        BLOCKER. The two feeders write the same row; nothing on this side modelled
        the other one, so a disagreement between them (the bulk push stamping a
        different `host`, or a NULL cursor) was invisible to every test here. It
        took an adversarial audit reading two units' environment blocks to find
        it. A fake that models only the writer under test is a fake that cannot
        see a seam.
        """
        self.tails[session_id] = tail[-self.MAX_TAIL:]
        self.offsets[session_id] = file_bytes  # None = UNKNOWN, as the server stores it
        self.hosts[session_id] = host

    # the two callables run_round expects
    def get(self, path):
        assert path == "/api/transcripts/stream/cursors", path
        return {
            "sessions": [
                {
                    "sessionId": sid,
                    "offset": off,
                    "reason": "accepted" if off is not None else "unknown-offset",
                }
                for sid, off in self.offsets.items()
            ]
        }

    def post(self, path, payload):
        assert path == "/api/transcripts/stream", path
        self.frames.append(payload)
        cursors = []
        for d in payload["sessions"]:
            sid = d["sessionId"]
            data = d["data"]
            nbytes = len(data.encode("utf-8"))
            if d.get("reset"):
                tail = (self.tails.get(sid, "") and "") + data
                self.tails[sid] = tail[-self.MAX_TAIL :]
                self.offsets[sid] = d["offset"] + nbytes
                self.hosts[sid] = payload["host"]
                cursors.append({"sessionId": sid, "offset": self.offsets[sid], "reason": "accepted"})
                continue
            stored = self.offsets.get(sid)
            if stored is None:
                cursors.append({"sessionId": sid, "offset": None, "reason": "unknown-offset"})
                continue
            # 🔴 THE HOST RULE, MODELLED. The stored offset is a position in the
            # host's file; an append from a DIFFERENT host is refused with a NULL
            # offset, because handing back a number that belongs to another
            # machine's file invites the client to retry against nonsense.
            if self.hosts.get(sid) not in (None, payload["host"]):
                cursors.append({"sessionId": sid, "offset": None, "reason": "host-changed"})
                continue
            if d["offset"] != stored:
                cursors.append({"sessionId": sid, "offset": stored, "reason": "offset-mismatch"})
                continue
            self.tails[sid] = (self.tails.get(sid, "") + data)[-self.MAX_TAIL :]
            self.offsets[sid] = stored + nbytes
            self.hosts[sid] = payload["host"]
            cursors.append({"sessionId": sid, "offset": self.offsets[sid], "reason": "accepted"})
        applied = sum(1 for c in cursors if c["reason"] == "accepted")
        return {"ok": True, "applied": applied, "cursors": cursors}


# ---------------------------------------------------------------------------
# planning
# ---------------------------------------------------------------------------


def test_a_first_sighting_is_a_reseed_not_an_append(tmp_path):
    """🔴 THE INODE IS THE ONLY THING THAT DISTINGUISHES 'this file grew' FROM
    'this is a different file at the same path', and on the first sighting there
    is none. Appending on the server's word alone would take an offset for a file
    that could have been rotated while this agent was down."""
    write_session(tmp_path, "proj", "sess-1", line(1) + line(2))
    state = ts.StreamState()
    # The server says it has this session at a known offset...
    state.cursors["sess-1"] = len(line(1))

    deltas, _ = plan(tmp_path, state)
    assert len(deltas) == 1
    assert deltas[0]["reset"] is True, (
        "a file this process has never stat'd was APPENDED to on the server's word — "
        "a rotation while the agent was down would splice two different files"
    )


def plan(root, state, **kw):
    return ts.plan_frame(root, state, **kw)


def test_an_append_carries_exactly_the_new_bytes_from_the_cursor(tmp_path):
    p = write_session(tmp_path, "proj", "sess-1", line(1))
    state = ts.StreamState()
    deltas, _ = plan(tmp_path, state)          # first sighting -> reseed
    assert deltas[0]["reset"] is True
    state.cursors["sess-1"] = deltas[0]["offset"] + len(deltas[0]["data"].encode())

    append(p, line(2))
    deltas, _ = plan(tmp_path, state)
    assert len(deltas) == 1
    d = deltas[0]
    assert d["reset"] is False
    assert d["offset"] == len(line(1))
    assert d["data"] == line(2), "the delta carried something other than exactly the new bytes"


def test_an_unchanged_file_produces_no_delta(tmp_path):
    """A frame with no sessions is REJECTED by the server, so the steady state
    must produce nothing at all rather than an empty delta."""
    write_session(tmp_path, "proj", "sess-1", line(1))
    state = ts.StreamState()
    deltas, _ = plan(tmp_path, state)
    state.cursors["sess-1"] = deltas[0]["offset"] + len(deltas[0]["data"].encode())

    deltas, skipped = plan(tmp_path, state)
    assert deltas == []
    assert skipped.get(ts.SKIP_UNCHANGED) == 1


def test_a_trailing_partial_line_waits_for_the_next_poll(tmp_path):
    """🔴 CUTTING ON A NEWLINE DOES TWO JOBS: no leading JSON fragment in the
    stored tail, and no multi-byte rune split across two frames."""
    p = write_session(tmp_path, "proj", "sess-1", line(1))
    state = ts.StreamState()
    deltas, _ = plan(tmp_path, state)
    state.cursors["sess-1"] = deltas[0]["offset"] + len(deltas[0]["data"].encode())

    append(p, '{"type":"assistant","partial":tr')  # no newline yet
    deltas, skipped = plan(tmp_path, state)
    assert deltas == [], "a partial record was sent"
    assert skipped.get(ts.SKIP_NO_BOUNDARY) == 1

    append(p, 'ue}\n')
    deltas, _ = plan(tmp_path, state)
    assert len(deltas) == 1
    assert deltas[0]["data"].endswith("true}\n")
    assert deltas[0]["data"].startswith('{"type":"assistant","partial":')


def test_a_truncated_file_reseeds_rather_than_appending(tmp_path):
    p = write_session(tmp_path, "proj", "sess-1", line(1) + line(2) + line(3))
    state = ts.StreamState()
    deltas, _ = plan(tmp_path, state)
    state.cursors["sess-1"] = deltas[0]["offset"] + len(deltas[0]["data"].encode())
    state.inodes["sess-1"] = os.stat(p).st_ino

    p.write_text(line(9), encoding="utf-8")  # same inode, much smaller
    deltas, _ = plan(tmp_path, state)
    # 🔴 BOTH FAILURE MODES NAMED, because deleting the truncation check does NOT
    # produce a wrong append — it produces a read past EOF, which yields NOTHING
    # and silently stops streaming that session until the reconciler resets it.
    # An assertion that only said "reset is True" would die with an IndexError
    # whose message names neither.
    assert len(deltas) == 1, (
        f"a SHRUNK file produced {len(deltas)} deltas, want 1 reset — a read past the new EOF "
        "returns nothing, so this session silently stops streaming"
    )
    assert deltas[0]["reset"] is True, "a SHRUNK file was appended to — the stored tail would " \
        "be the old end plus the new start, spliced"
    assert deltas[0]["data"] == line(9)
    assert deltas[0]["offset"] == 0


def test_a_ROTATED_file_reseeds_even_though_it_GREW(tmp_path):
    """🔴 THE CASE A SIZE COMPARISON CANNOT SEE. A recreated file can be LARGER
    than the old one at the moment it is checked, so 'shrank' reports nothing and
    the next delta appends the head of a new file onto the tail of an old one."""
    p = write_session(tmp_path, "proj", "sess-1", line(1))
    state = ts.StreamState()
    deltas, _ = plan(tmp_path, state)
    state.cursors["sess-1"] = deltas[0]["offset"] + len(deltas[0]["data"].encode())
    old_inode = state.inodes["sess-1"]

    # Replace the file with a BIGGER one at the same path, new inode.
    #
    # 🔴 THE REPLACEMENT IS CREATED WHILE THE OLD FILE STILL EXISTS, then renamed
    # over it. `unlink` then `create` frees the inode first and the filesystem
    # HANDS IT STRAIGHT BACK — measured on this host's tmpfs — so the "rotated"
    # fixture had the SAME inode as before and the test was asserting nothing.
    other = p.with_suffix(".jsonl.new")
    other.write_text(line(5) + line(6) + line(7), encoding="utf-8")
    os.replace(other, p)
    assert os.stat(p).st_ino != old_inode, "the fixture did not actually change the inode, so " \
        "this test cannot distinguish rotation from growth"

    deltas, _ = plan(tmp_path, state)
    assert len(deltas) == 1
    assert deltas[0]["reset"] is True, "a ROTATED file that happened to be LARGER was appended to"


def test_a_session_that_outruns_the_stream_SKIPS_FORWARD_rather_than_queueing(tmp_path):
    """🔴 THE BACKPRESSURE BOUND. Replaying an arbitrarily long backlog makes the
    stream slower the further behind it gets — the shape where a queue never
    drains. Past MAX_LAG_BYTES the host skips forward with a reset and lets the
    5-minute reconciler repair the gap."""
    p = write_session(tmp_path, "proj", "sess-1", line(1))
    state = ts.StreamState()
    deltas, _ = plan(tmp_path, state)
    state.cursors["sess-1"] = deltas[0]["offset"] + len(deltas[0]["data"].encode())

    lag = 4096
    big = "".join(line(i) for i in range(2, 2 + (lag // len(line(2))) + 4))
    append(p, big)

    # AT the bound: still an append, catching up piecewise.
    deltas, _ = plan(tmp_path, state, max_lag_bytes=len(big) + 1, max_delta_bytes=1024)
    assert deltas[0]["reset"] is False, "at the lag bound the host should still be appending"
    assert len(deltas[0]["data"].encode()) <= 1024

    # OVER the bound: skip forward.
    deltas, _ = plan(tmp_path, state, max_lag_bytes=len(big) - 1, max_delta_bytes=1024)
    assert deltas[0]["reset"] is True, "a session lagging past the bound kept replaying its " \
        "backlog — the stream gets slower the further behind it falls"
    assert deltas[0]["offset"] > 0, "the skip-forward reset claims to start at byte 0"


def test_one_delta_never_exceeds_the_per_session_bound(tmp_path):
    """The server REJECTS a delta over MaxDeltaBytes and a rejection changes
    nothing, so a client at or over the limit fails every poll while looking
    correctly configured."""
    p = write_session(tmp_path, "proj", "sess-1", line(1))
    state = ts.StreamState()
    deltas, _ = plan(tmp_path, state)
    state.cursors["sess-1"] = deltas[0]["offset"] + len(deltas[0]["data"].encode())

    append(p, "".join(line(i) for i in range(2, 4000)))
    deltas, _ = plan(tmp_path, state)
    assert len(deltas) == 1
    assert len(deltas[0]["data"].encode()) <= ts.MAX_DELTA_BYTES


def test_the_frame_SESSION_COUNT_bound_holds(tmp_path):
    """🔴 CAPPING THE PER-SESSION SIZE WHILE LEAVING THE SESSION COUNT UNBOUNDED
    IS NOT A BOUND — the same hole the server's own comments record.

    🔴 THE SESSIONS ARE TINY ON PURPOSE, so this can only fail on the COUNT. With
    fat sessions the aggregate bound bites first and the count is never reached —
    measured: a fixture of 73 x ~20 KB sessions SURVIVED a mutant that deleted the
    count cap entirely, because the byte budget stopped the loop at 19.
    """
    body = line(1)
    n = ts.MAX_SESSIONS_PER_FRAME + 25
    assert n * len(body.encode()) < ts.MAX_FRAME_BYTES, (
        "the fixture's total exceeds the aggregate bound, so this test would die to the "
        "OTHER guard and say nothing about the count"
    )
    for i in range(n):
        write_session(tmp_path, "proj", f"sess-{i:04d}", body)

    state = ts.StreamState()
    deltas, _ = plan(tmp_path, state)
    assert len(deltas) == ts.MAX_SESSIONS_PER_FRAME, (
        f"a frame carried {len(deltas)} sessions against a cap of {ts.MAX_SESSIONS_PER_FRAME}"
    )


def test_the_frame_AGGREGATE_BYTE_bound_holds(tmp_path):
    """🔴 THE OTHER HALF, WITH THE COUNT DELIBERATELY OUT OF REACH. Few enough
    sessions that the count cap cannot be what stops the loop, each big enough
    that their sum breaches the aggregate."""
    body = "".join(line(i) for i in range(600))
    per = len(body.encode())
    n = (ts.MAX_FRAME_BYTES // min(per, ts.MAX_DELTA_BYTES)) + 3
    assert n < ts.MAX_SESSIONS_PER_FRAME, (
        f"the fixture needs {n} sessions but the count cap is {ts.MAX_SESSIONS_PER_FRAME} — "
        "it would die to the count guard instead"
    )
    for i in range(n):
        write_session(tmp_path, "proj", f"sess-{i:04d}", body)

    state = ts.StreamState()
    deltas, _ = plan(tmp_path, state)
    # 🔴 THE BYTE TOTAL IS ASSERTED FIRST BECAUSE IT IS THE CLAIM. The count check
    # below is a fixture-reachability guard — it says the fixture was big enough
    # to reach the bound at all — and asserting it first would make every failure
    # report "the bound did not bite" even when the real finding is "the frame
    # carried 528 KiB".
    total = sum(len(d["data"].encode()) for d in deltas)
    assert total <= ts.MAX_FRAME_BYTES, (
        f"a frame carried {total} bytes against a {ts.MAX_FRAME_BYTES}-byte cap"
    )
    assert len(deltas) < n, "the aggregate bound did not bite at all — the fixture is too small"


def test_a_transcript_whose_bytes_will_not_decode_is_left_to_the_reconciler(tmp_path):
    """🔴 A LOSSY DECODE WOULD MAKE THE TWO SIDES' BYTE COUNTS DIVERGE, and
    because the host re-reads from the SERVER's offset, the same bad bytes would
    be re-sent and re-refused on every poll for ever. Skipping the session is the
    bounded failure; the bulk push (which does use errors='replace') repairs it."""
    d = tmp_path / "proj"
    d.mkdir(parents=True)
    (d / "bad.jsonl").write_bytes(b'{"type":"user","t":"\xff\xfe"}\n')
    write_session(tmp_path, "proj", "good", line(1))

    state = ts.StreamState()
    deltas, skipped = plan(tmp_path, state)
    ids = {x["sessionId"] for x in deltas}
    assert "bad" not in ids, "undecodable bytes were sent; the cursor would drift for ever"
    assert "good" in ids, "one bad transcript stopped the whole round"
    assert skipped.get(ts.SKIP_UNDECODABLE) == 1, (
        "the undecodable session was counted as something else — a real corruption "
        f"signal hidden inside the ordinary steady state; got {skipped}"
    )


def test_subagent_transcripts_are_not_streamed(tmp_path):
    """🔴 84% OF `.jsonl` FILES ON THIS HOST LIVE UNDER `subagents/` AND ARE NOT
    RESUMABLE SESSIONS. Streaming them would push thousands of rows that no
    attention entry and no tmux window can ever join to, at the fastest cadence in
    the system. The shared enumerator excludes them; a hand-rolled walk would not."""
    write_session(tmp_path, "proj", "real", line(1))
    sub = tmp_path / "proj" / "real" / "subagents"
    sub.mkdir(parents=True, exist_ok=True)
    (sub / "agent-decoy.jsonl").write_text(line(2), encoding="utf-8")

    state = ts.StreamState()
    deltas, _ = plan(tmp_path, state)
    ids = {d["sessionId"] for d in deltas}
    assert ids == {"real"}, f"a subagent transcript was streamed: {ids}"


# ---------------------------------------------------------------------------
# cursors
# ---------------------------------------------------------------------------


def test_a_null_offset_is_adopted_as_unknown_rather_than_ignored():
    """🔴 SKIPPING THE NULL WOULD LEAVE A STALE OFFSET IN PLACE and every
    subsequent delta would be refused for ever, with nothing saying why."""
    state = ts.StreamState()
    state.cursors["s"] = 500
    ts.adopt_cursors(state, [{"sessionId": "s", "offset": None, "reason": "unknown-offset"}])
    assert state.cursors["s"] is None


def test_any_refusal_carrying_a_NULL_offset_makes_the_host_reseed():
    """🔴 THE HOST BRANCHES ON THE OFFSET, NOT ON THE REASON STRING, AND THAT IS
    WHAT MAKES A NEW SERVER-SIDE REASON SAFE. The server grew `host-changed` (a
    session that moved between machines: its stored offset is a position in the
    OTHER machine's file). This side needs no new branch — a null offset already
    means "reseed" — but that has to be ASSERTED, or the next reason added on the
    server passes unhandled and the host retries against a number that means
    nothing here.
    """
    for reason in ("unknown-offset", "host-changed", "some-reason-invented-later"):
        state = ts.StreamState()
        state.cursors["s"] = 500
        ts.adopt_cursors(state, [{"sessionId": "s", "offset": None, "reason": reason}])
        assert state.cursors["s"] is None, f"a null offset with reason {reason!r} was not adopted"


@pytest.mark.parametrize("bad", [-1, "12", 1.5, True, {"a": 1}])
def test_a_malformed_offset_is_treated_as_unknown_not_adopted(bad):
    """Adopting a wrong number would splice. Unknown reseeds, which is correct."""
    state = ts.StreamState()
    state.cursors["s"] = 500
    ts.adopt_cursors(state, [{"sessionId": "s", "offset": bad}])
    assert state.cursors["s"] is None, f"offset {bad!r} was adopted"


# ---------------------------------------------------------------------------
# the resume path, end to end against the protocol
# ---------------------------------------------------------------------------


def test_a_lost_response_resumes_without_duplicating_or_skipping_a_line(tmp_path):
    """🔴 THE FAILURE A CLAWGATE REDEPLOY ACTUALLY PRODUCES, AND THE HARD
    DIRECTION. Not 'the request never arrived' — that is trivially safe. The
    dangerous one is a frame the server APPLIED and COMMITTED whose response the
    host never saw. The host still believes its old cursor; accepting the retry
    would store those bytes twice.

    The assertion is on the RECONSTRUCTED TAIL, not on a status: after the
    disconnect and the resume the server's tail must equal the file exactly.
    """
    p = write_session(tmp_path, "proj", "sess-1", line(1) + line(2))
    srv = FakeServer()
    state = ts.StreamState()

    def poll():
        return ts.run_round(srv.post, srv.get, "workbench", state, tmp_path)

    poll()                       # reseed
    append(p, line(3))
    poll()

    # The pod applies and commits, then dies before the reply is written.
    stale = dict(state.cursors)
    append(p, line(4) + line(5))
    counts = poll()
    assert counts["applied"] == 1
    state.cursors = stale        # the reply never arrived

    # It comes back. The host retries from where it thinks it is.
    append(p, line(6))
    counts = poll()
    assert counts["refused"] == 1, (
        "the retry after a lost response was ACCEPTED — lines 4 and 5 are now stored "
        "twice and nothing downstream can detect it"
    )

    # The refusal carried the server's own offset, so the very next poll resumes.
    counts = poll()
    assert counts["applied"] == 1, "the host did not resynchronise on the next poll"
    assert srv.tails["sess-1"] == p.read_text(), (
        "after the disconnect and resume the server's tail does not equal the file\n"
        f"server: {srv.tails['sess-1']!r}\nfile:   {p.read_text()!r}"
    )


def test_an_agent_restart_reseeds_and_loses_nothing_after_the_reseed(tmp_path):
    """The other disconnect shape: the AGENT restarted, so it has no cursors at
    all. It must hydrate from the server and reseed, not guess."""
    p = write_session(tmp_path, "proj", "sess-1", line(1) + line(2))
    srv = FakeServer()

    state = ts.StreamState()
    ts.run_round(srv.post, srv.get, "workbench", state, tmp_path)
    append(p, line(3))
    ts.run_round(srv.post, srv.get, "workbench", state, tmp_path)
    assert srv.tails["sess-1"] == p.read_text()

    # Restart: brand-new state. It hydrates from the server, then reseeds because
    # it has never stat'd the file.
    state2 = ts.StreamState()
    append(p, line(4))
    counts = ts.run_round(srv.post, srv.get, "workbench", state2, tmp_path)
    assert counts["applied"] == 1
    assert state2.hydrated is True
    assert srv.tails["sess-1"] == p.read_text(), "the reseed after a restart did not converge"

    # And it keeps streaming from there.
    append(p, line(5))
    ts.run_round(srv.post, srv.get, "workbench", state2, tmp_path)
    assert srv.tails["sess-1"] == p.read_text()


def test_a_server_that_forgot_everything_is_reseeded_not_appended_to(tmp_path):
    """A database restore leaves the server empty while the host believes
    everything is delivered. Asking the server is what makes this recoverable."""
    p = write_session(tmp_path, "proj", "sess-1", line(1) + line(2))
    srv = FakeServer()
    state = ts.StreamState()
    ts.run_round(srv.post, srv.get, "workbench", state, tmp_path)
    assert srv.tails["sess-1"]

    srv.tails.clear()
    srv.offsets.clear()
    state.hydrated = False       # what the agent does after any stream failure
    append(p, line(3))
    counts = ts.run_round(srv.post, srv.get, "workbench", state, tmp_path)
    assert counts["applied"] == 1
    assert srv.tails["sess-1"] == p.read_text()


def test_no_frame_is_posted_when_there_is_nothing_to_send(tmp_path):
    """🔴 THE SERVER REJECTS A FRAME WITH NO SESSIONS, so posting one would turn
    the ordinary steady state into an HTTP 400 on every poll — a stream that
    reports failure precisely when it is working."""
    write_session(tmp_path, "proj", "sess-1", line(1))
    srv = FakeServer()
    state = ts.StreamState()
    ts.run_round(srv.post, srv.get, "workbench", state, tmp_path)
    assert len(srv.frames) == 1

    counts = ts.run_round(srv.post, srv.get, "workbench", state, tmp_path)
    assert counts["sent"] == 0
    assert len(srv.frames) == 1, "an empty frame was posted"


def test_coverage_is_not_capped_at_eight_sessions(tmp_path):
    """🔴 THE CRITERION-3 CLAIM, ON THE PRODUCER SIDE. The old bulk feeder carried
    at most 6 sessions per 5-minute tick against a server cap of 8; the stream
    must represent every active session in ONE poll.

    21 is deliberately NOT a multiple of 8: a fixture of 8 or 16 could be passed
    by a mutant that restored the old cap by landing on a boundary.
    """
    n = 21
    assert n % 8 != 0
    for i in range(n):
        write_session(tmp_path, "proj", f"sess-{i:04d}", line(i))

    srv = FakeServer()
    state = ts.StreamState()
    counts = ts.run_round(srv.post, srv.get, "workbench", state, tmp_path)
    assert counts["sent"] == n, f"only {counts['sent']} of {n} sessions were carried"
    assert counts["applied"] == n
    assert len(srv.tails) == n, f"the server holds {len(srv.tails)} of {n} sessions"


def test_the_client_bounds_stay_under_the_servers(tmp_path):
    """🔴 A CLIENT TUNED EXACTLY TO A SERVER LIMIT TURNS ANY ROUNDING
    DISAGREEMENT INTO A STREAM THAT FAILS EVERY POLL WHILE LOOKING CORRECTLY
    CONFIGURED. The server's numbers are MaxDeltaBytes=65536,
    MaxFrameBytes=524288, MaxDeltaSessionsPerFrame=64."""
    assert ts.MAX_DELTA_BYTES < 65536
    assert ts.MAX_FRAME_BYTES < 524288
    assert ts.MAX_SESSIONS_PER_FRAME < 64
    # And a reseed IS a delta, so it must obey the per-delta cap — the first draft
    # of the module had RESEED_TAIL_BYTES at 192 KiB, which the server would have
    # rejected on every rotation.
    assert ts.RESEED_TAIL_BYTES <= ts.MAX_DELTA_BYTES


# ---------------------------------------------------------------------------
# Records larger than one delta — the two liveness stalls an adversarial audit
# proved on the first revision of this module. Both were SILENT: the suite was
# green, the agent logged nothing, and the affected sessions simply fell back to
# the 5-minute feed for ever.
# ---------------------------------------------------------------------------


def _huge_record(nbytes: int, tag: str = "big") -> str:
    """One JSONL record whose payload alone exceeds `nbytes`."""
    return '{"type":"user","tag":"%s","message":{"role":"user","content":"%s"}}\n' % (
        tag, "x" * nbytes)


def test_a_record_LARGER_than_one_delta_streams_in_pieces_rather_than_stalling(tmp_path):
    """🔴 PROVED STALL, FIXED. `_read_append` used to require a newline inside the
    48 KiB window; a record longer than that never has one, so the append path
    declined EVERY POLL, FOR EVER — re-reading 48 KiB every 5s (~830 MB/day) with
    nothing logged. The lag escape could not save it either: a record between
    MAX_DELTA_BYTES and MAX_LAG_BYTES never reaches that threshold.

    It is not an edge case. The server's own trimToTail records the fleet
    measurement: a single tool-result record here has been measured well past
    256 KiB. So the sessions with the biggest tool output — the ones most worth
    streaming — were exactly the ones that silently did not.
    """
    p = write_session(tmp_path, "proj", "sess-1", line(1))
    state = ts.StreamState()
    deltas, _ = plan(tmp_path, state)
    state.cursors["sess-1"] = deltas[0]["offset"] + len(deltas[0]["data"].encode())

    big = _huge_record(100 * 1024)
    assert len(big.encode()) > ts.MAX_DELTA_BYTES, "the fixture is not bigger than one delta"
    append(p, big)

    # It must make progress on EVERY poll, and finish.
    got = b""
    for i in range(12):
        deltas, skipped = plan(tmp_path, state)
        if not deltas:
            assert got, (
                f"poll {i}: nothing sent and nothing had been sent — the append path is "
                f"STALLED on a record larger than one delta; skipped={skipped}")
            break
        d = deltas[0]
        assert d["reset"] is False, "a large record should stream, not reseed"
        assert d["offset"] == state.cursors["sess-1"], "the piece did not start at the cursor"
        chunk = d["data"].encode()
        assert chunk, "an empty delta was produced"
        got += chunk
        state.cursors["sess-1"] += len(chunk)
    assert got.decode() == big, (
        "the reassembled pieces do not equal the record:\n"
        f"got {len(got)} bytes, want {len(big.encode())}")


def test_every_piece_of_a_split_record_is_valid_UTF8(tmp_path):
    """🔴 A BYTE CUT INSIDE A MULTI-BYTE RUNE IS THE WHOLE REASON THE NORMAL PATH
    CUTS ON A NEWLINE. With no newline to cut on, the rune-boundary walk is what
    keeps `_decode_exact` from rejecting a perfectly good chunk — and, downstream,
    what keeps the two sides' byte counts equal.

    The fixture is 3-byte runes, so a naive byte cut lands mid-rune 2 times in 3
    rather than by luck.
    """
    p = write_session(tmp_path, "proj", "sess-1", line(1))
    state = ts.StreamState()
    deltas, _ = plan(tmp_path, state)
    state.cursors["sess-1"] = deltas[0]["offset"] + len(deltas[0]["data"].encode())

    huge = '{"type":"user","t":"' + ("あ" * 60000) + '"}\n'
    assert len(huge.encode()) > 3 * ts.MAX_DELTA_BYTES, (
        f"the fixture is {len(huge.encode())} bytes; it must span several deltas "
        f"({ts.MAX_DELTA_BYTES} each) or the split is never exercised")
    append(p, huge)

    got = b""
    for _ in range(20):
        deltas, _ = plan(tmp_path, state)
        if not deltas:
            break
        d = deltas[0]
        assert d["reset"] is False, "an APPEND was expected; a reseed means the cursor was lost"
        piece = d["data"]
        # A round trip is the claim: had the module cut mid-rune, _decode_exact
        # would have refused the chunk and the session would be SKIPPED instead.
        assert piece.encode().decode("utf-8") == piece
        assert d["offset"] == state.cursors["sess-1"], "a piece did not start at the cursor"
        got += piece.encode()
        state.cursors["sess-1"] += len(piece.encode())
    assert got == huge.encode(), f"reassembled {len(got)} of {len(huge.encode())} bytes"


def test_a_file_whose_LAST_record_exceeds_the_window_can_still_reseed(tmp_path):
    """🔴 THE SECOND PROVED STALL, AND THE WORSE ONE. `_read_reseed` required a
    newline both before and after its cut, so a file ending in a record bigger
    than the 48 KiB window returned SKIP — and because `plan_frame` only leaves
    the reseed branch once a cursor is adopted, and no cursor can be adopted while
    the reseed declines, it was a PERMANENT RESEED LOOP.

    ⚠ The asymmetry that hid it: `build_transcript_push.read_tail` uses a 192 KiB
    window, so for last-records between the two sizes the 5-minute push worked
    perfectly while the stream looped.
    """
    body = line(1) + _huge_record(100 * 1024)
    d = tmp_path / "proj"
    d.mkdir(parents=True, exist_ok=True)
    (d / "sess-1.jsonl").write_text(body, encoding="utf-8")

    state = ts.StreamState()
    deltas, skipped = plan(tmp_path, state)
    assert len(deltas) == 1, (
        f"a file ending in an over-window record produced NO reseed — this is the "
        f"permanent loop: skipped={skipped}")
    assert deltas[0]["reset"] is True
    assert deltas[0]["data"], "the reseed carried no data"
    # The reseed starts inside the record, so it is truncated by construction —
    # the server marks it and the parser counts a leading partial.
    assert deltas[0]["offset"] > 0


def test_a_partial_line_still_being_WRITTEN_waits_rather_than_being_split(tmp_path):
    """🔴 THE CONTROL FOR THE TWO TESTS ABOVE, AND IT IS NOT THE SAME CASE. The
    fix must distinguish "this record is bigger than the window" (stream it) from
    "this line is still being written" (wait one poll). Reaching EOF inside the
    window is what tells them apart; without that check, every ordinary in-flight
    line would be shipped as a fragment.
    """
    p = write_session(tmp_path, "proj", "sess-1", line(1))
    state = ts.StreamState()
    deltas, _ = plan(tmp_path, state)
    state.cursors["sess-1"] = deltas[0]["offset"] + len(deltas[0]["data"].encode())

    append(p, '{"type":"user","partial":tr')  # short, no newline, at EOF
    deltas, skipped = plan(tmp_path, state)
    assert deltas == [], "a short in-flight line was shipped as a fragment"
    assert skipped.get(ts.SKIP_NO_BOUNDARY) == 1


def test_two_files_with_ONE_session_id_do_not_kill_the_whole_frame(tmp_path):
    """🔴 THE SERVER REJECTS THE WHOLE FRAME ON A DUPLICATE ID, AND THE AGENT
    RESENDS THE SAME FRAME. Nothing in the loop removes the offending session, so
    one duplicate would kill this host's stream permanently — not degrade it.

    Two routes, both covered: the same `<uuid>.jsonl` under two project
    directories, and — because the SERVER trims the id and this side must too —
    a name with trailing whitespace.
    """
    write_session(tmp_path, "projA", "dup-sess", line(1))
    write_session(tmp_path, "projB", "dup-sess", line(2))
    write_session(tmp_path, "projA", "dup-sess ", line(3))
    write_session(tmp_path, "projA", "unique", line(4))

    state = ts.StreamState()
    deltas, skipped = plan(tmp_path, state)
    ids = [d["sessionId"] for d in deltas]
    assert len(ids) == len(set(ids)), f"a frame carried a duplicate session id: {ids}"
    assert "unique" in ids, "the innocent session was dropped too"
    assert skipped.get(ts.SKIP_DUPLICATE_ID, 0) >= 2, (
        f"the duplicates were not counted under their own reason: {skipped}")


def test_the_candidate_limit_bounds_how_many_files_are_stat_ed(tmp_path):
    """A declared bound with no test is a claim. This one keeps a host with
    thousands of recent transcripts from stat'ing all of them every 5 seconds."""
    for i in range(40):
        write_session(tmp_path, "proj", f"sess-{i:04d}", line(i))
    state = ts.StreamState()
    deltas, _ = plan(tmp_path, state, max_candidates=7)
    assert len(deltas) == 7, f"max_candidates=7 produced {len(deltas)} deltas"


def test_the_inode_memory_is_BOUNDED(tmp_path):
    """`cursors` is wholesale-replaced on every re-hydration; `inodes` is not, so
    in a process that runs for weeks it is the one that leaks. Eviction costs the
    evicted session exactly one reseed."""
    state = ts.StreamState()
    for i in range(state.INODE_LIMIT + 50):
        state.note_inode(f"s{i}", i)
    assert len(state.inodes) <= state.INODE_LIMIT
    # Oldest-first, so the NEWEST is the one that survives.
    assert f"s{state.INODE_LIMIT + 49}" in state.inodes
    assert "s0" not in state.inodes


def test_a_huge_record_converges_END_TO_END_against_the_protocol(tmp_path):
    """🔴 THE PIECES MUST REASSEMBLE ON THE SERVER, NOT MERELY LEAVE THIS HOST.
    Splitting a record mid-way is only safe because the server concatenates by
    OFFSET; a rune-alignment that moved the data without moving the offset by the
    same number of bytes would produce a tail that is subtly wrong and reads as
    fine. This drives the real `run_round` against the protocol and compares the
    stored tail with the file.
    """
    p = write_session(tmp_path, "proj", "sess-1", line(1))
    srv = FakeServer()
    state = ts.StreamState()
    ts.run_round(srv.post, srv.get, "workbench", state, tmp_path)

    append(p, '{"type":"user","t":"' + ("あ" * 60000) + '"}\n')
    for _ in range(20):
        counts = ts.run_round(srv.post, srv.get, "workbench", state, tmp_path)
        if counts["sent"] == 0:
            break
    assert srv.tails["sess-1"] == p.read_text(), (
        "a record split across deltas did not reassemble on the server\n"
        f"stored {len(srv.tails['sess-1'])} chars, file {len(p.read_text())} chars")
    assert srv.offsets["sess-1"] == len(p.read_bytes()), (
        f"cursor {srv.offsets['sess-1']} != file size {len(p.read_bytes())} — the offset and "
        "the data disagree, so the NEXT delta would splice")


def _cjk_body(pad: int) -> str:
    """One huge CJK record, with `pad` filler bytes AFTER the runes so the rune
    grid moves relative to the reseed window."""
    return '{"t":"' + ("あ" * 60000) + '"' + ("z" * pad) + "}\n"


@pytest.mark.parametrize("pad", [0, 1, 2])
def test_a_RESEED_that_starts_mid_rune_moves_its_OFFSET_by_the_bytes_it_dropped(tmp_path, pad):
    """🔴 A RESEED SEEKS TO `size - window`, WHICH LANDS INSIDE A RUNE TWO TIMES
    IN THREE ON CJK TEXT — the one place in this module a read begins at an
    arbitrary byte offset. Aligning forward to a rune boundary is necessary (a
    strict decode would otherwise reject the whole window and the session would
    reseed for ever), but aligning the DATA without moving the OFFSET by the same
    number of bytes is worse than not aligning at all: the cursor would then name
    a position the data does not start at, and the next append would splice.

    🔴 THE PADDING IS THE POINT AND THE FIRST VERSION OF THIS TEST DID NOT HAVE
    IT. With a fixed prefix, `size - window` landed EXACTLY on a rune boundary by
    arithmetic accident (20 ASCII bytes + 3-byte runes, against a window that is a
    multiple of 3), so the alignment never ran and deleting it SURVIVED. Three
    paddings guarantee at least two land mid-rune, and the assertion below proves
    which case each one reached rather than assuming.

    The claim is the RELATIONSHIP, not the alignment: the bytes at the reported
    offset in the file must BE the bytes that were sent.
    """
    # 🔴 THE PADDING GOES AFTER THE RUNES, NOT BEFORE THEM. A prefix pad shifts
    # the file length AND the rune grid by the same amount, so `size - window`
    # stays exactly as aligned as it was — which is how the first version of this
    # test managed three paddings that all landed on a boundary.
    body = _cjk_body(pad)
    d = tmp_path / "proj"
    d.mkdir(parents=True, exist_ok=True)
    p = d / "sess-1.jsonl"
    p.write_text(body, encoding="utf-8")
    raw = p.read_bytes()
    assert len(raw) > 3 * ts.RESEED_TAIL_BYTES, "the fixture must be far larger than the window"

    naive = len(raw) - ts.RESEED_TAIL_BYTES
    mid_rune = raw[naive] & 0xC0 == 0x80

    state = ts.StreamState()
    deltas, skipped = plan(tmp_path, state)
    assert len(deltas) == 1, f"no reseed was produced: {skipped}"
    d0 = deltas[0]
    assert d0["reset"] is True
    sent = d0["data"].encode("utf-8")
    off = d0["offset"]
    assert off > 0, "the fixture did not exercise a mid-file reseed"
    assert raw[off:off + len(sent)] == sent, (
        f"the reseed says it starts at byte {off}, but the file's bytes there are not the "
        f"bytes it sent — the cursor and the data disagree, so the next append would splice "
        f"(pad={pad}, the naive seek landed mid-rune: {mid_rune})")
    if mid_rune:
        assert off > naive, (
            f"the naive seek at {naive} is INSIDE a rune and the reseed still reports {off} — "
            "the data was aligned but the offset was not moved with it")


def test_at_least_one_reseed_padding_actually_lands_MID_RUNE(tmp_path):
    """🔴 THE POSITIVE CONTROL FOR THE PARAMETRISED TEST ABOVE. If every padding
    happened to land on a boundary, all three cases would pass while exercising
    nothing — which is exactly what the unpadded version did.
    """
    hits = 0
    for pad in (0, 1, 2):
        raw = _cjk_body(pad).encode("utf-8")
        naive = len(raw) - ts.RESEED_TAIL_BYTES
        if raw[naive] & 0xC0 == 0x80:
            hits += 1
    assert hits >= 1, (
        "no padding makes the reseed seek land inside a rune, so the alignment path is never "
        "reached and its test is vacuous")


def test_a_reseed_of_an_IN_FLIGHT_FIRST_LINE_waits_rather_than_shipping_a_fragment(tmp_path):
    """🔴 THE CONTROL THE RESEED PATH DID NOT HAVE. The append path distinguishes
    "this record is bigger than the window" (stream it) from "this line is still
    being written" (wait). `_read_reseed` had no such discriminator — it always
    reaches EOF by construction — so it shipped a fragment even where the
    justification provably cannot apply: a file SMALLER than the window cannot
    contain a record larger than the window.

    Measured before the fix: a 61-byte file holding half a record produced
    `(0, 61, '{"type":"user"…half a r')`. The previous revision waited.
    """
    d = tmp_path / "proj"
    d.mkdir(parents=True, exist_ok=True)
    (d / "sess-1.jsonl").write_text('{"type":"user","message":{"role":"user","content":"half a r',
                                    encoding="utf-8")

    state = ts.StreamState()
    deltas, skipped = plan(tmp_path, state)
    assert deltas == [], f"a first line still being written was shipped as a fragment: {deltas}"
    assert skipped.get(ts.SKIP_NO_BOUNDARY) == 1

    # And once it is a whole record, it goes.
    with (d / "sess-1.jsonl").open("a", encoding="utf-8") as fh:
        fh.write('ecord"}}\n')
    deltas, _ = plan(tmp_path, state)
    assert len(deltas) == 1 and deltas[0]["reset"] is True


def test_an_append_ending_EXACTLY_at_the_window_boundary_still_waits(tmp_path):
    """🔴 THE ONE POINT `len(raw) < budget` GOT WRONG. A file that ends precisely
    `budget` bytes past the cursor reads a FULL window that is ALSO the end of the
    file; the length test then calls it an over-window record and ships a
    fragment. One chance in 49,152 per poll — and the caller has the exact answer
    in the `stat` it already took.
    """
    p = write_session(tmp_path, "proj", "sess-1", line(1))
    state = ts.StreamState()
    deltas, _ = plan(tmp_path, state)
    cur = deltas[0]["offset"] + len(deltas[0]["data"].encode())
    state.cursors["sess-1"] = cur

    budget = 64
    append(p, "Z" * budget)  # exactly `budget` bytes, no newline, at EOF
    deltas, skipped = plan(tmp_path, state, max_delta_bytes=budget)
    assert deltas == [], (
        f"a line still being written that happened to be EXACTLY the window size was shipped "
        f"as a fragment: {deltas}")
    assert skipped.get(ts.SKIP_NO_BOUNDARY) == 1

    # One byte MORE and it is genuinely an over-window record, so it streams.
    append(p, "Z")
    deltas, _ = plan(tmp_path, state, max_delta_bytes=budget)
    assert len(deltas) == 1, "a record that IS bigger than the window did not stream"


def test_a_name_that_strips_to_nothing_is_not_counted_as_a_DUPLICATE(tmp_path):
    """A file called " .jsonl" is not a duplicate of anything. Counting it as one
    reports the wrong mechanism for the one signal these counters exist to give.
    """
    write_session(tmp_path, "proj", " ", line(1))
    write_session(tmp_path, "proj", "real", line(2))
    state = ts.StreamState()
    deltas, skipped = plan(tmp_path, state)
    assert [d["sessionId"] for d in deltas] == ["real"]
    assert skipped.get(ts.SKIP_NO_SESSION_ID) == 1, f"counted as: {skipped}"
    assert ts.SKIP_DUPLICATE_ID not in skipped


def test_note_inode_evicts_LEAST_RECENTLY_SEEN_not_longest_lived(tmp_path):
    """A plain assignment leaves an existing key at its ORIGINAL position, so
    eviction would drop the longest-LIVED session — typically the most active
    one. Unreachable at the shipped limit, and wrong for free if it ever is not.
    """
    state = ts.StreamState()
    state.INODE_LIMIT = 3
    for sid in ("a", "b", "c"):
        state.note_inode(sid, 1)
    state.note_inode("a", 2)   # `a` is now the most recently seen
    state.note_inode("d", 3)   # forces one eviction
    assert "a" in state.inodes, "the most recently SEEN session was evicted"
    assert "b" not in state.inodes, "the least recently seen session survived"


def test_a_bulk_push_that_stamps_a_DIFFERENT_host_does_not_wedge_the_stream(tmp_path):
    """🔴 THE SEAM THAT HID A BLOCKER, NOW DRIVEN END TO END. The two feeders
    write the same row's `host`, and they used to derive it by different rules —
    the bulk push falling through to `uname -n` ("nixos" on BOTH machines), the
    stream reading the collector's label ("workbench"). While `host` was
    display-only that cost nothing. Once the stream made it a correctness
    predicate it became a PERMANENT reseed loop: the push stamping one value back
    every 5 minutes, the stream refusing every append 5 seconds later and
    reseeding up to 48 KiB per session.

    The rule is now single-sourced (`scripts/lib/host_label.py`, pinned across
    both feeders by `test_BOTH_feeders_resolve_the_SAME_host_label`). This test
    covers the OTHER half — that if a host change does happen (a session genuinely
    resumed on the other machine), the stream RECOVERS in one poll instead of
    looping.
    """
    p = write_session(tmp_path, "proj", "sess-1", line(1) + line(2))
    srv = FakeServer()
    state = ts.StreamState()
    ts.run_round(srv.post, srv.get, "workbench", state, tmp_path)
    assert srv.hosts["sess-1"] == "workbench"
    # 🔴 THE HOST MUST HOLD A REAL CURSOR AFTER ONE ROUND. It can only have come
    # from the RESPONSE — hydration ran against an empty server. Without that
    # adoption every poll reseeds and the two assertions below become unreachable,
    # so this is the precondition that makes the rest of the test mean anything.
    assert state.cursors.get("sess-1") is not None, (
        "the host did not adopt a cursor from the response, so it reseeds on every poll and "
        "nothing below is being tested")
    srv.bulk_push("sess-1", p.read_text(), len(p.read_bytes()), host="nixos")

    append(p, line(3))
    counts = ts.run_round(srv.post, srv.get, "workbench", state, tmp_path)
    assert counts["refused"] == 1, "the host change was not detected at all"

    # ONE more poll and it is back — a bounded gap, not a loop.
    counts = ts.run_round(srv.post, srv.get, "workbench", state, tmp_path)
    assert counts["applied"] == 1, (
        "the stream did not recover on the next poll — this is the permanent reseed loop, and "
        "it would repeat after every single bulk tick")
    assert srv.hosts["sess-1"] == "workbench"
    assert srv.tails["sess-1"].endswith(line(3))


def test_a_bulk_push_with_an_UNKNOWN_cursor_is_recovered_in_one_poll(tmp_path):
    """The other cross-writer state the fake could not previously model: a bulk
    push that reports no file size at all (which the real builder does whenever a
    transcript holds one undecodable byte). The server stores NULL, every append
    is refused as `unknown-offset`, and the host must RESEED — in one poll."""
    p = write_session(tmp_path, "proj", "sess-1", line(1) + line(2))
    srv = FakeServer()
    state = ts.StreamState()
    ts.run_round(srv.post, srv.get, "workbench", state, tmp_path)

    srv.bulk_push("sess-1", p.read_text(), None)  # fileBytes UNKNOWN

    append(p, line(3))
    counts = ts.run_round(srv.post, srv.get, "workbench", state, tmp_path)
    assert counts["refused"] == 1
    counts = ts.run_round(srv.post, srv.get, "workbench", state, tmp_path)
    assert counts["applied"] == 1, (
        "the stream did not recover on the next poll from an unknown cursor")
    assert srv.tails["sess-1"].endswith(line(3))
