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
        self.frames: list[dict] = []

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
                cursors.append({"sessionId": sid, "offset": self.offsets[sid], "reason": "accepted"})
                continue
            stored = self.offsets.get(sid)
            if stored is None:
                cursors.append({"sessionId": sid, "offset": None, "reason": "unknown-offset"})
                continue
            if d["offset"] != stored:
                cursors.append({"sessionId": sid, "offset": stored, "reason": "offset-mismatch"})
                continue
            self.tails[sid] = (self.tails.get(sid, "") + data)[-self.MAX_TAIL :]
            self.offsets[sid] = stored + nbytes
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
