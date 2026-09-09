"""Behavioural tests for scripts/transcript-push.sh + scripts/lib/build_transcript_push.py
— the host-side feeder for clawgate's Claude Code TRANSCRIPT read model.

Everything here runs against a STUB HTTP server bound to loopback on an ephemeral
port and a SYNTHETIC transcript tree under tmp_path. Nothing touches the real
clawgate, the real `~/.claude/projects`, or the network.

🔴 EVERY TRANSCRIPT FIXTURE IS SYNTHETIC AND HAND-WRITTEN. devrc is a PUBLIC repo
and a Claude Code transcript is captured text — real prompts, real model output,
real paths. What these tests need is the record SHAPE, and the shapes below were
derived from a measurement of record TYPE COUNTS on the live fleet (2026-09-04:
of 5,614 records across three sessions, 1,331 attachments, 862 tool_use, 862
tool_result, 419 thinking, 360 assistant text, 78 human turns), never from copied
content.

WHAT THIS SUITE IS FOR
----------------------
The feeder is a timer-driven pipe, so its failure mode is silence: it can stop
delivering while the chat view keeps rendering the LAST tail, which reads as a
quiet session rather than a broken feeder. The load-bearing tests are therefore
not the happy path:

  1. THE POSITIVE CONTROL. `test_a_changed_session_is_pushed_and_the_server_sees_it`
     proves the stub server can observe a push at all. Every "nothing was pushed"
     assertion counts requests and expects zero, and a zero from a harness wired
     to nothing is indistinguishable from a real one.

  2. THE DEDUPE LOOP, IN BOTH DIRECTIONS. A skip rule that is wrong one way
     re-pushes megabytes every tick; wrong the other way it never pushes again
     and the view silently freezes. Both are asserted, and the SECOND is the one
     a "does it skip?" test alone would miss.

  3. THE TAIL IS A TAIL, AND IT SAYS SO. `truncated` is a first-class field
     because a consumer that cannot tell "the whole session" from "the end of it"
     will state the first while showing the second.

  4. rc-PER-CONDITION. A missing token, an absent transcript tree, an unreachable
     server and a rejecting server need four different fixes. One "something
     failed" code would tell an operator nothing.

  5. THE TOKEN IS NOT IN argv. Asserted through a stub `curl` that records its
     own argv — a source-level check would type-check past a second curl
     invocation added later.
"""

import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "transcript-push.sh"
BUILDER = REPO_ROOT / "scripts" / "lib" / "build_transcript_push.py"
HOME_NIX = REPO_ROOT / "nix" / "home.nix"

sys.path.insert(0, str(REPO_ROOT / "scripts"))

# 🔴 RUNTIME STUBS GO THROUGH `testlib.mockbin.write_exec`, WHICH OWNS THE
# SHEBANG. Do not hand-write one — the nix check sandbox that gates the merge has
# no `/usr/bin/env`, so a stub carrying that shebang cannot exec, and the failure
# presents as production code misbehaving rather than as a fixture fault. See the
# long note in test_tmux_snapshot_push.py.
from testlib.mockbin import write_exec  # noqa: E402


# --- synthetic transcript records --------------------------------------------


def _rec(**fields) -> str:
    return json.dumps(fields, separators=(",", ":"))


def human_turn(text: str, session_id: str = "s") -> str:
    return _rec(type="user", sessionId=session_id, message={"role": "user", "content": text})


def assistant_turn(text: str, session_id: str = "s") -> str:
    return _rec(
        type="assistant",
        sessionId=session_id,
        message={"role": "assistant", "content": [{"type": "text", "text": text}]},
    )


def attachment(session_id: str = "s") -> str:
    return _rec(type="attachment", sessionId=session_id)


def transcript(session_id: str, *lines: str) -> str:
    return "\n".join(lines) + "\n"


# --- the stub server ----------------------------------------------------------


class _Recorder(HTTPServer):
    """An HTTPServer that records every request and serves a scripted digest."""

    def __init__(self, addr, handler):
        super().__init__(addr, handler)
        self.requests = []
        # What GET /api/transcripts/digest answers with.
        self.digest_status = 200
        self.digest_body = json.dumps({"sessions": []}).encode()
        # What POST /api/transcripts answers with.
        self.push_status = 200
        self.push_body = b'{"ok":true}'


class _Handler(BaseHTTPRequestHandler):
    def _record(self, raw):
        self.server.requests.append(
            {
                "method": self.command,
                "path": self.path,
                "body": raw,
                "auth": self.headers.get("Authorization"),
                "content_type": self.headers.get("Content-Type"),
            }
        )

    def _reply(self, status, body):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802 - name fixed by BaseHTTPRequestHandler
        self._record(b"")
        self._reply(self.server.digest_status, self.server.digest_body)

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        self._record(self.rfile.read(length))
        self._reply(self.server.push_status, self.server.push_body)

    def log_message(self, format, *args):  # noqa: A002
        pass  # keep pytest output clean


@pytest.fixture
def server():
    srv = _Recorder(("127.0.0.1", 0), _Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield srv
    srv.shutdown()
    srv.server_close()


def base_url(server):
    host, port = server.server_address[0], server.server_address[1]
    return f"http://{host}:{port}"


@pytest.fixture
def projects(tmp_path):
    """A synthetic `~/.claude/projects` tree."""

    root = tmp_path / "projects"
    root.mkdir()

    def _write(session_id: str, body: str, *, project: str = "-home-zach-workspace-devrc"):
        d = root / project
        d.mkdir(exist_ok=True)
        f = d / f"{session_id}.jsonl"
        f.write_text(body)
        return f

    _write.root = root
    return _write


def run_push(*, projects_root, tmp_path, env_extra=None, conf_text=None, path_prefix=None):
    conf = tmp_path / "clawgate.env"
    conf.write_text(conf_text if conf_text is not None else "")
    env = dict(os.environ)
    # Start from a clean slate so an operator's real credentials in the ambient
    # environment can never leak into a test run and aim it at production.
    env.pop("CLAWGATE_API_URL", None)
    env.pop("CLAWGATE_HOOK_TOKEN", None)
    env["CLAWGATE_CONF_FILE"] = str(conf)
    env["CLAUDE_PROJECTS_DIR"] = str(projects_root)
    env["HOME"] = str(tmp_path)
    env["TRANSCRIPT_PUSH_HOST"] = "testhost"
    if path_prefix:
        env["PATH"] = f"{path_prefix}:{env['PATH']}"
    env.update(env_extra or {})
    return subprocess.run(
        ["bash", str(SCRIPT)],
        capture_output=True,
        text=True,
        env=env,
        timeout=180,
    )


def pushes(server):
    return [r for r in server.requests if r["method"] == "POST"]


def digests(server):
    return [r for r in server.requests if r["method"] == "GET"]


# ── 1. the positive control ──────────────────────────────────────────────────


def test_a_changed_session_is_pushed_and_the_server_sees_it(server, projects, tmp_path):
    """🔴 THE POSITIVE CONTROL for this whole file.

    Every "nothing was pushed" assertion below counts requests and expects 0. A
    0 from a stub server that can never observe anything is indistinguishable
    from a real 0, so this test exists to show the count CAN move.
    """
    projects("sess-a", transcript("sess-a", human_turn("do it"), assistant_turn("done")))
    proc = run_push(
        projects_root=projects.root,
        tmp_path=tmp_path,
        env_extra={"CLAWGATE_API_URL": base_url(server), "CLAWGATE_HOOK_TOKEN": "tok-abc"},
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr

    assert len(digests(server)) == 1, "the dedupe pre-flight never ran"
    assert digests(server)[0]["path"] == "/api/transcripts/digest"

    sent = pushes(server)
    assert len(sent) == 1, "the harness never observed a push"
    assert sent[0]["path"] == "/api/transcripts"
    assert sent[0]["auth"] == "Bearer tok-abc"
    assert sent[0]["content_type"] == "application/json"

    body = json.loads(sent[0]["body"])
    assert body["host"] == "testhost"
    assert [s["sessionId"] for s in body["sessions"]] == ["sess-a"]
    assert "do it" in body["sessions"][0]["tail"]


# ── 2. the dedupe loop, in BOTH directions ───────────────────────────────────


def test_a_session_the_server_ALREADY_HAS_is_not_pushed(server, projects, tmp_path):
    """The skip. This one comparison is why the steady-state tick is kilobytes."""
    body = transcript("sess-a", human_turn("do it"), assistant_turn("done"))
    projects("sess-a", body)

    # Run once to learn the hash the builder computes, then feed it back as the
    # server's digest. 🔴 DERIVED FROM THE FEEDER'S OWN OUTPUT, NOT RECOMPUTED
    # HERE: a test that hashed the file itself would be asserting agreement
    # between two implementations of the tail rule, and would go green while both
    # were wrong in the same way.
    first = run_push(
        projects_root=projects.root,
        tmp_path=tmp_path,
        env_extra={"CLAWGATE_API_URL": base_url(server), "CLAWGATE_HOOK_TOKEN": "t"},
    )
    assert first.returncode == 0, first.stdout + first.stderr
    sent = json.loads(pushes(server)[0]["body"])["sessions"][0]

    server.digest_body = json.dumps(
        {"sessions": [{"sessionId": "sess-a", "contentHash": sent["contentHash"],
                       "updatedAt": "2026-09-04T10:00:00Z", "tailBytes": len(sent["tail"])}]}
    ).encode()
    server.requests.clear()

    second = run_push(
        projects_root=projects.root,
        tmp_path=tmp_path,
        env_extra={"CLAWGATE_API_URL": base_url(server), "CLAWGATE_HOOK_TOKEN": "t"},
    )
    assert second.returncode == 0, second.stdout + second.stderr
    assert len(digests(server)) == 1, "the pre-flight did not run on the second tick"
    assert pushes(server) == [], "an UNCHANGED session was pushed again"
    assert "nothing to push" in second.stdout


def test_a_session_that_GREW_is_pushed_again(server, projects, tmp_path):
    """🔴 THE OTHER DIRECTION, AND THE ONE A SKIP-ONLY TEST MISSES.

    A skip rule that is too eager never pushes again and the chat view silently
    freezes on whatever it last received — indistinguishable, on screen, from a
    session that stopped talking. `return` in place of the comparison, or a
    constant hash, passes the test above and fails this one.
    """
    body = transcript("sess-a", human_turn("do it"))
    f = projects("sess-a", body)

    first = run_push(
        projects_root=projects.root,
        tmp_path=tmp_path,
        env_extra={"CLAWGATE_API_URL": base_url(server), "CLAWGATE_HOOK_TOKEN": "t"},
    )
    assert first.returncode == 0, first.stdout + first.stderr
    sent = json.loads(pushes(server)[0]["body"])["sessions"][0]

    server.digest_body = json.dumps(
        {"sessions": [{"sessionId": "sess-a", "contentHash": sent["contentHash"],
                       "updatedAt": "2026-09-04T10:00:00Z", "tailBytes": len(sent["tail"])}]}
    ).encode()
    server.requests.clear()

    # The session says something more.
    f.write_text(body + assistant_turn("and now this") + "\n")

    second = run_push(
        projects_root=projects.root,
        tmp_path=tmp_path,
        env_extra={"CLAWGATE_API_URL": base_url(server), "CLAWGATE_HOOK_TOKEN": "t"},
    )
    assert second.returncode == 0, second.stdout + second.stderr
    resent = pushes(server)
    assert len(resent) == 1, "a session that GREW was not re-pushed — the view would freeze"
    grown = json.loads(resent[0]["body"])["sessions"][0]
    assert "and now this" in grown["tail"]
    assert grown["contentHash"] != sent["contentHash"]


def test_a_digest_the_feeder_cannot_parse_is_FATAL_not_an_empty_one(server, projects, tmp_path):
    """🔴 FAIL, NEVER 'ASSUME THE SERVER HAS NOTHING'.

    Treating an unreadable pre-flight as an empty digest is the expensive
    direction — every recent session re-pushed on every tick — and it fires
    precisely when the server is least able to absorb it. It would also hide a
    server that had started answering with something else entirely.
    """
    projects("sess-a", transcript("sess-a", human_turn("do it")))
    server.digest_body = b'{"not_what_you_expected": true}'

    proc = run_push(
        projects_root=projects.root,
        tmp_path=tmp_path,
        env_extra={"CLAWGATE_API_URL": base_url(server), "CLAWGATE_HOOK_TOKEN": "t"},
    )
    assert proc.returncode == 3, proc.stdout + proc.stderr
    assert pushes(server) == [], "a session was pushed against a digest that could not be read"


# ── 3. the tail is a TAIL, and it says so ────────────────────────────────────


def test_a_short_transcript_is_sent_WHOLE_and_marked_untruncated(server, projects, tmp_path):
    projects("sess-a", transcript("sess-a", human_turn("short"), assistant_turn("also short")))
    proc = run_push(
        projects_root=projects.root,
        tmp_path=tmp_path,
        env_extra={"CLAWGATE_API_URL": base_url(server), "CLAWGATE_HOOK_TOKEN": "t"},
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    sess = json.loads(pushes(server)[0]["body"])["sessions"][0]
    assert sess["truncated"] is False
    assert "short" in sess["tail"] and "also short" in sess["tail"]


def test_a_long_transcript_is_CUT_marked_truncated_and_keeps_the_END(server, projects, tmp_path):
    """🔴 THE DIRECTION IS THE ASSERTION. Keeping the START is the same one-line
    slice and produces a payload that answers the opposite of the reader's
    question ("what has it been doing lately") while looking identical in size.
    """
    lines = [assistant_turn(f"filler message number {i:05d}") for i in range(400)]
    lines.append(assistant_turn("THE-FINAL-MESSAGE"))
    projects("sess-a", transcript("sess-a", *lines))

    proc = run_push(
        projects_root=projects.root,
        tmp_path=tmp_path,
        env_extra={
            "CLAWGATE_API_URL": base_url(server),
            "CLAWGATE_HOOK_TOKEN": "t",
            "TRANSCRIPT_PUSH_TAIL_BYTES": "4096",
        },
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    sess = json.loads(pushes(server)[0]["body"])["sessions"][0]

    assert sess["truncated"] is True, "a cut tail was not marked truncated"
    assert len(sess["tail"]) <= 4096
    assert "THE-FINAL-MESSAGE" in sess["tail"], "the cut kept the START of the session, not the END"
    assert "filler message number 00000" not in sess["tail"]
    # 🔴 AND NO LEADING FRAGMENT. Every line the server receives must be a whole
    # record, or the parser counts a partial one on every truncated session for
    # ever and the bytes are wasted.
    first_line = sess["tail"].split("\n", 1)[0]
    json.loads(first_line)  # raises if the cut left a fragment


def test_the_content_hash_matches_the_tail_that_was_sent(server, projects, tmp_path):
    """The server RECOMPUTES this and REJECTS a mismatch (it is an integrity
    check, not a value it adopts), so a feeder whose hash describes anything but
    the bytes it sent fails every push with a 400.
    """
    import hashlib

    projects("sess-a", transcript("sess-a", human_turn("do it")))
    proc = run_push(
        projects_root=projects.root,
        tmp_path=tmp_path,
        env_extra={"CLAWGATE_API_URL": base_url(server), "CLAWGATE_HOOK_TOKEN": "t"},
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    sess = json.loads(pushes(server)[0]["body"])["sessions"][0]
    assert sess["contentHash"] == hashlib.sha256(sess["tail"].encode("utf-8")).hexdigest()


# ── 4. the bounds ────────────────────────────────────────────────────────────


def test_no_more_than_max_sessions_are_sent_in_one_push(server, projects, tmp_path):
    """🔴 THE SERVER REJECTS AN OVER-CAP PUSH OUTRIGHT, so a feeder that ignores
    this bound fails EVERY tick while looking correctly configured. The rest
    catch up on the next tick — that is the design, not a shortfall.
    """
    for i in range(12):
        projects(f"sess-{i:02d}", transcript(f"sess-{i:02d}", human_turn(f"session {i}")))

    proc = run_push(
        projects_root=projects.root,
        tmp_path=tmp_path,
        env_extra={
            "CLAWGATE_API_URL": base_url(server),
            "CLAWGATE_HOOK_TOKEN": "t",
            "TRANSCRIPT_PUSH_MAX_SESSIONS": "3",
        },
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    body = json.loads(pushes(server)[0]["body"])
    assert len(body["sessions"]) == 3, f"sent {len(body['sessions'])} sessions against a cap of 3"


def test_a_transcript_older_than_the_age_window_is_not_sent(server, projects, tmp_path):
    import time

    old = projects("sess-old", transcript("sess-old", human_turn("ages ago")))
    projects("sess-new", transcript("sess-new", human_turn("just now")))
    ancient = time.time() - 72 * 3600
    os.utime(old, (ancient, ancient))

    proc = run_push(
        projects_root=projects.root,
        tmp_path=tmp_path,
        env_extra={
            "CLAWGATE_API_URL": base_url(server),
            "CLAWGATE_HOOK_TOKEN": "t",
            "TRANSCRIPT_PUSH_MAX_AGE_HOURS": "24",
        },
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    ids = [s["sessionId"] for s in json.loads(pushes(server)[0]["body"])["sessions"]]
    assert ids == ["sess-new"], f"the age window did not exclude the old session: {ids}"


def test_the_newest_sessions_win_when_the_cap_bites(server, projects, tmp_path):
    """🔴 WHICH ones the cap keeps is a decision, not a detail. Dropping the
    NEWEST would mean the session someone is actively watching is the one that
    never arrives, on every tick, for as long as the fleet is busy.
    """
    import time

    now = time.time()
    for i in range(6):
        f = projects(f"sess-{i}", transcript(f"sess-{i}", human_turn(f"session {i}")))
        # sess-5 newest, sess-0 oldest.
        stamp = now - (6 - i) * 60
        os.utime(f, (stamp, stamp))

    proc = run_push(
        projects_root=projects.root,
        tmp_path=tmp_path,
        env_extra={
            "CLAWGATE_API_URL": base_url(server),
            "CLAWGATE_HOOK_TOKEN": "t",
            "TRANSCRIPT_PUSH_MAX_SESSIONS": "2",
        },
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    ids = sorted(s["sessionId"] for s in json.loads(pushes(server)[0]["body"])["sessions"])
    assert ids == ["sess-4", "sess-5"], f"the cap kept the wrong sessions: {ids}"


def test_every_pushed_session_carries_fileBytes_equal_to_its_file_size(server, projects, tmp_path):
    """🔴 THE SEAM WITH THE DELTA STREAM, AND ITS ABSENCE IS SILENT. `fileBytes`
    is where this tail ENDS in the file, which the server stores as the delta
    stream's resume cursor. Without it a bulk push REPLACES the tail while leaving
    the cursor describing the tail it replaced, and the next delta appends onto a
    base that no longer matches its offset — a splice, stored, with nothing to
    indicate it. Nothing about the tail itself looks wrong when this is missing.
    """
    f = projects("sess-a", transcript("sess-a", human_turn("do the thing")))
    proc = run_push(
        projects_root=projects.root,
        tmp_path=tmp_path,
        env_extra={"CLAWGATE_API_URL": base_url(server), "CLAWGATE_HOOK_TOKEN": "t"},
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    sent = json.loads(pushes(server)[0]["body"])["sessions"]
    assert len(sent) == 1
    assert sent[0]["fileBytes"] == f.stat().st_size, (
        "fileBytes does not equal the transcript's size, so the cursor the server stores "
        "will not match where the stream reads from"
    )
    # 🔴 AND IT MUST BE >= THE TAIL IT ACCOMPANIES, which is what the server
    # enforces: a cursor pointing BEFORE the start of the stored tail is a splice
    # waiting to happen, and the server rejects the whole atomic push over it.
    assert sent[0]["fileBytes"] >= len(sent[0]["tail"].encode("utf-8"))


def test_a_truncated_tail_reports_the_WHOLE_file_size_not_the_tail_length(server, projects, tmp_path):
    """The cursor is a FILE position. Reporting the tail's length instead would
    put it 250 KiB behind on every long session — and the stream would then be
    refused for ever while looking correctly configured."""
    big = transcript("sess-big", *[human_turn(f"turn {i} " + "x" * 400, "sess-big") for i in range(900)])
    f = projects("sess-big", big)
    assert f.stat().st_size > 196608, "the fixture is not big enough to be truncated"

    proc = run_push(
        projects_root=projects.root,
        tmp_path=tmp_path,
        env_extra={"CLAWGATE_API_URL": base_url(server), "CLAWGATE_HOOK_TOKEN": "t"},
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    sent = json.loads(pushes(server)[0]["body"])["sessions"][0]
    assert sent["truncated"] is True
    assert sent["fileBytes"] == f.stat().st_size
    assert sent["fileBytes"] > len(sent["tail"].encode("utf-8")), (
        "a TRUNCATED tail reported a fileBytes equal to its own length — the cursor would "
        "point at the start of the stored tail rather than its end"
    )


def test_coverage_is_not_capped_at_eight_sessions(server, projects, tmp_path):
    """🔴 RED ON PRE-CHANGE CODE. The default was MAX_PER_PUSH=6 against a server
    cap of 8, so a host with 21 changed sessions carried SIX of them per
    five-minute tick — which is why most session cards had no conversation to
    show at all (~93 live windows, measured 2026-09-07).

    21 is deliberately NOT a multiple of 8 or 6: a fixture of 8, 16 or 12 could be
    passed by a mutant restoring the old cap by landing exactly on a boundary.
    """
    n = 21
    assert n % 8 != 0 and n % 6 != 0
    for i in range(n):
        projects(f"sess-{i:02d}", transcript(f"sess-{i:02d}", human_turn(f"turn {i}", f"sess-{i:02d}")))

    proc = run_push(
        projects_root=projects.root,
        tmp_path=tmp_path,
        env_extra={"CLAWGATE_API_URL": base_url(server), "CLAWGATE_HOOK_TOKEN": "t"},
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    sent = json.loads(pushes(server)[0]["body"])["sessions"]
    assert len(sent) == n, f"one push carried {len(sent)} of {n} changed sessions"
    assert len({s["sessionId"] for s in sent}) == n


def test_the_aggregate_byte_bound_stops_the_push_before_the_session_count_does(
    server, projects, tmp_path
):
    """🔴 THIS IS THE BOUND THE SESSION COUNT USED TO STAND IN FOR. Raising the
    count from 6 to 48 is only safe because the TOTAL is bounded directly; without
    it, 48 sessions of 192 KiB would be a 9 MiB body the server refuses on every
    tick.

    The fixture keeps every session well under the per-session tail cap, so this
    can only fail on the aggregate — a fixture that also breached the tail bound
    would die to the other guard and this check would be unreachable.
    """
    body = transcript("x", *[human_turn("y" * 400, "x") for _ in range(40)])
    per = len(body.encode())
    assert per < 196608, "the fixture breaches the per-session tail cap and would test that instead"
    n = 12
    for i in range(n):
        projects(f"sess-{i:02d}", body)

    budget = per * 4  # room for ~4 sessions
    proc = run_push(
        projects_root=projects.root,
        tmp_path=tmp_path,
        env_extra={
            "CLAWGATE_API_URL": base_url(server),
            "CLAWGATE_HOOK_TOKEN": "t",
            "TRANSCRIPT_PUSH_MAX_BYTES": str(budget),
        },
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    sent = json.loads(pushes(server)[0]["body"])["sessions"]
    assert 0 < len(sent) < n, f"the aggregate bound did not bite: {len(sent)} of {n} sessions"
    total = sum(len(s["tail"].encode()) for s in sent)
    assert total <= budget + per, f"the push carried {total} bytes against a {budget}-byte budget"


def test_the_client_bounds_sit_UNDER_the_servers_not_at_them(server, projects, tmp_path):
    """🔴 A CLIENT TUNED EXACTLY TO THE SERVER'S CAP TURNS ANY ROUNDING
    DISAGREEMENT INTO A FEEDER THAT FAILS EVERY TICK while looking correctly
    configured. The server's numbers are read here as LITERALS on purpose — this
    is a cross-repo contract and the point is to fail loudly when clawgate
    changes one.
    """
    server_max_tail_bytes = 256 * 1024  # transcript.MaxTailBytes
    # 🔴 RAISED FROM 8 TO 128 WITH THE SERVER, AND THE PAIR MOVED FOR A REASON,
    # NOT TO MAKE A TEST PASS. 8 was a COVERAGE cap wearing a safety cap's
    # clothes: measured 2026-09-07 against ~93 live windows, at most 8 sessions
    # could carry a transcript per push, so most session cards had no conversation
    # to show at all. The ceiling the count was standing in for is the AGGREGATE
    # tail bytes, which the server now bounds directly (transcript.MaxPushTailBytes)
    # — so the count is free to rise and the third assertion below is the one now
    # doing the work the second used to pretend to do.
    server_max_sessions = 128  # transcript.MaxSessionsPerPush
    server_max_push_bytes = 4 * 1024 * 1024  # transcript.MaxPushTailBytes

    text = SCRIPT.read_text()
    tail_default = _shell_default(text, "TAIL_BYTES", "TRANSCRIPT_PUSH_TAIL_BYTES")
    sess_default = _shell_default(text, "MAX_PER_PUSH", "TRANSCRIPT_PUSH_MAX_SESSIONS")
    bytes_default = _shell_default(text, "MAX_PUSH_BYTES", "TRANSCRIPT_PUSH_MAX_BYTES")

    assert tail_default < server_max_tail_bytes, (
        f"the default tail ({tail_default}) is not UNDER the server's cap ({server_max_tail_bytes})"
    )
    assert sess_default < server_max_sessions, (
        f"the default session count ({sess_default}) is not UNDER the server's cap ({server_max_sessions})"
    )
    assert bytes_default < server_max_push_bytes, (
        f"the default aggregate ({bytes_default}) is not UNDER the server's cap "
        f"({server_max_push_bytes}) — this is the bound that actually holds now that the "
        f"session count is {sess_default}"
    )


def _shell_default(text: str, var: str, env_name: str) -> int:
    """Extract `VAR="${ENV:-N}"` from the script source."""
    import re

    m = re.search(rf'^{var}="\$\{{{env_name}:-(\d+)\}}"', text, re.M)
    assert m, f"could not find the default for {var} in the script"
    return int(m.group(1))


# ── 5. rc-per-condition ──────────────────────────────────────────────────────


def test_no_token_exits_2_and_pushes_nothing(server, projects, tmp_path):
    projects("sess-a", transcript("sess-a", human_turn("do it")))
    proc = run_push(
        projects_root=projects.root,
        tmp_path=tmp_path,
        env_extra={"CLAWGATE_API_URL": base_url(server)},
    )
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert server.requests == [], "a request was made with no credentials"


def test_a_missing_transcript_directory_exits_3(server, tmp_path):
    """A real state on a host where Claude Code has never run — distinct from
    every other failure because the fix is 'nothing is wrong'."""
    proc = run_push(
        projects_root=tmp_path / "does-not-exist",
        tmp_path=tmp_path,
        env_extra={"CLAWGATE_API_URL": base_url(server), "CLAWGATE_HOOK_TOKEN": "t"},
    )
    assert proc.returncode == 3, proc.stdout + proc.stderr
    assert server.requests == []


def test_an_unreachable_server_exits_4(projects, tmp_path):
    projects("sess-a", transcript("sess-a", human_turn("do it")))
    proc = run_push(
        projects_root=projects.root,
        tmp_path=tmp_path,
        # Port 1 on loopback: nothing listens, so curl fails at the transport.
        env_extra={"CLAWGATE_API_URL": "http://127.0.0.1:1", "CLAWGATE_HOOK_TOKEN": "t"},
    )
    assert proc.returncode == 4, proc.stdout + proc.stderr


def test_a_server_that_lacks_the_route_exits_5_and_says_so(server, projects, tmp_path):
    """A server predating the transcript read model is a DEPLOY-ORDER problem
    (server first), not a payload problem, and the message must say which."""
    projects("sess-a", transcript("sess-a", human_turn("do it")))
    server.digest_status = 404
    server.digest_body = b"not found"

    proc = run_push(
        projects_root=projects.root,
        tmp_path=tmp_path,
        env_extra={"CLAWGATE_API_URL": base_url(server), "CLAWGATE_HOOK_TOKEN": "t"},
    )
    assert proc.returncode == 5, proc.stdout + proc.stderr
    assert "deploy the server first" in proc.stdout
    assert pushes(server) == []


def test_a_rejected_push_exits_5(server, projects, tmp_path):
    projects("sess-a", transcript("sess-a", human_turn("do it")))
    server.push_status = 400
    server.push_body = b'{"error":"nope"}'

    proc = run_push(
        projects_root=projects.root,
        tmp_path=tmp_path,
        env_extra={"CLAWGATE_API_URL": base_url(server), "CLAWGATE_HOOK_TOKEN": "t"},
    )
    assert proc.returncode == 5, proc.stdout + proc.stderr


def test_a_REDIRECT_is_a_failure_not_a_success(server, projects, tmp_path):
    """🔴 THE SIBLING FEEDER'S MEASURED BUG, GUARDED HERE BEFORE IT HAPPENS.

    There is no `-L`, so pointing CLAWGATE_API_URL at any hostname that redirects
    (an ingress, clawgate.zacx.dev) makes curl return the redirect and store
    NOTHING. Under an `HTTP < 400` success test that logs "pushed" and exits 0 —
    invisible for ever at a five-minute cadence.
    """
    projects("sess-a", transcript("sess-a", human_turn("do it")))
    server.digest_status = 302

    proc = run_push(
        projects_root=projects.root,
        tmp_path=tmp_path,
        env_extra={"CLAWGATE_API_URL": base_url(server), "CLAWGATE_HOOK_TOKEN": "t"},
    )
    assert proc.returncode == 5, proc.stdout + proc.stderr
    assert "REDIRECTED" in proc.stdout


def test_nothing_recent_exits_0_and_pushes_nothing(server, projects, tmp_path):
    """🔴 rc 0, NOT AN EMPTY PUSH. The server REJECTS a push carrying no sessions
    (correctly — it is not a transcript push), so emitting one would turn the
    ordinary steady state into an HTTP 400 on every tick: a feeder reporting
    failure precisely when it is working.
    """
    proc = run_push(
        projects_root=projects.root,
        tmp_path=tmp_path,
        env_extra={"CLAWGATE_API_URL": base_url(server), "CLAWGATE_HOOK_TOKEN": "t"},
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert pushes(server) == []
    assert "nothing to push" in proc.stdout


# ── 6. credentials ───────────────────────────────────────────────────────────


def test_the_environment_beats_the_conf_file(server, projects, tmp_path):
    """🔴 A REGRESSION GUARD, NOT A PREFERENCE.

    `clawgate-stop-hook.sh` sources this same file with `set -a`, which makes the
    FILE beat the environment there; the measured consequence was a probe aimed
    at a harmless address silently POSTing to PRODUCTION. If this script ever
    acquires that behaviour, a test run could write into the real read model.
    """
    projects("sess-a", transcript("sess-a", human_turn("do it")))
    proc = run_push(
        projects_root=projects.root,
        tmp_path=tmp_path,
        conf_text=(
            "CLAWGATE_API_URL=http://198.51.100.1:9\n"
            "CLAWGATE_HOOK_TOKEN=file-token\n"
        ),
        env_extra={"CLAWGATE_API_URL": base_url(server), "CLAWGATE_HOOK_TOKEN": "env-token"},
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert pushes(server)[0]["auth"] == "Bearer env-token"


def test_the_conf_file_is_read_when_the_environment_is_silent(server, projects, tmp_path):
    """The other half — without it, "the environment wins" could be implemented
    as "the file is never read" and pass."""
    projects("sess-a", transcript("sess-a", human_turn("do it")))
    proc = run_push(
        projects_root=projects.root,
        tmp_path=tmp_path,
        conf_text=(
            f"export CLAWGATE_API_URL={base_url(server)}\n"
            "  CLAWGATE_HOOK_TOKEN='file-token'\n"
        ),
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert pushes(server)[0]["auth"] == "Bearer file-token"


def test_the_token_never_appears_in_curl_argv(server, projects, tmp_path):
    """🔴 EVERYTHING ON THIS BOX CAN READ /proc/<pid>/cmdline.

    Asserted through a stub `curl` that records its OWN argv, not by reading the
    source — a structural check would type-check past a second curl invocation
    added later.
    """
    projects("sess-a", transcript("sess-a", human_turn("do it")))
    bindir = tmp_path / "stubbin"
    bindir.mkdir()
    argv_log = tmp_path / "curl-argv.log"
    real_curl = subprocess.run(["bash", "-lc", "command -v curl"], capture_output=True, text=True).stdout.strip()
    assert real_curl, "no real curl on PATH to delegate to"
    write_exec(
        bindir / "curl",
        f'printf "%s\\n" "$*" >> {argv_log}\n'
        f'exec {real_curl} "$@"\n',
    )

    proc = run_push(
        projects_root=projects.root,
        tmp_path=tmp_path,
        path_prefix=str(bindir),
        env_extra={"CLAWGATE_API_URL": base_url(server), "CLAWGATE_HOOK_TOKEN": "super-secret-token"},
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    recorded = argv_log.read_text()
    # Positive control: the stub really did see the invocations.
    assert recorded.strip(), "the curl stub recorded nothing — this assertion covers nothing"
    assert "super-secret-token" not in recorded, f"the token reached curl's argv:\n{recorded}"


# ── 7. the builder's own contract ────────────────────────────────────────────


def run_builder(projects_root, digest_path, tmp_path, **kw):
    args = [
        sys.executable, str(BUILDER),
        "--projects-dir", str(projects_root),
        "--digest", str(digest_path),
        "--host", kw.get("host", "testhost"),
        "--tail-bytes", str(kw.get("tail_bytes", 196608)),
        "--max-sessions", str(kw.get("max_sessions", 6)),
        "--max-age-hours", str(kw.get("max_age_hours", 24)),
        "--max-candidates", str(kw.get("max_candidates", 200)),
    ]
    return subprocess.run(args, capture_output=True, text=True, timeout=120)


def empty_digest(tmp_path):
    p = tmp_path / "digest.json"
    p.write_text(json.dumps({"sessions": []}))
    return p


def test_the_builder_signals_nothing_to_push_with_rc_10(projects, tmp_path):
    """🔴 A CODE, NOT AN EMPTY DOCUMENT, so "the builder had nothing to send"
    cannot be confused with "the builder crashed and produced nothing"."""
    proc = run_builder(projects.root, empty_digest(tmp_path), tmp_path)
    assert proc.returncode == 10, proc.stdout + proc.stderr
    assert proc.stdout.strip() == ""


def test_the_builder_skips_a_record_longer_than_the_whole_window(projects, tmp_path):
    """One record bigger than the tail window has no boundary to cut on. Sending
    the fragment would parse to zero events while claiming to be a conversation,
    so the session is skipped instead."""
    projects("sess-huge", assistant_turn("x" * 5000) + "\n")
    proc = run_builder(projects.root, empty_digest(tmp_path), tmp_path, tail_bytes=1000)
    assert proc.returncode == 10, proc.stdout + proc.stderr


def test_the_builder_ignores_non_jsonl_files(projects, tmp_path):
    """The transcript tree is `<projects>/<slug>/<uuid>.jsonl`. Anything else
    that has been dropped in there is not a transcript."""
    d = projects.root / "-home-zach-workspace-devrc"
    d.mkdir(parents=True, exist_ok=True)
    (d / "notes.md").write_text("not a transcript")
    (d / "sess-a.jsonl.bak").write_text(human_turn("nor this") + "\n")
    projects("sess-real", transcript("sess-real", human_turn("this one")))

    proc = run_builder(projects.root, empty_digest(tmp_path), tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    ids = [s["sessionId"] for s in json.loads(proc.stdout)["sessions"]]
    assert ids == ["sess-real"], f"the builder picked up a non-transcript: {ids}"


def test_the_builder_never_emits_invalid_utf8(projects, tmp_path):
    """🔴 THE SERVER'S COLUMN IS TEXT AND POSTGRES REJECTS INVALID UTF-8, which
    would fail the WHOLE atomic push — one bad byte taking every other session in
    the request down with it. The cut point is a byte offset, so it CAN land
    inside a multi-byte rune; replacing is what makes that cost one glyph.
    """
    d = projects.root / "-multibyte"
    d.mkdir(parents=True, exist_ok=True)
    # 🔴 ensure_ascii=False, SO THE BYTES ON DISK REALLY ARE MULTI-BYTE. The
    # first version of this fixture used the default json.dumps, which escapes
    # every non-ASCII rune to `\uXXXX` — six ASCII bytes. The file was then pure
    # ASCII, no byte offset could land mid-rune, and the test passed while
    # exercising nothing. A fixture that cannot contain the hazard cannot see it.
    line = json.dumps(
        {"type": "assistant", "sessionId": "sess-mb",
         "message": {"role": "assistant", "content": [{"type": "text", "text": "界" * 40}]}},
        separators=(",", ":"), ensure_ascii=False,
    )
    body = "\n".join(line for _ in range(80)) + "\n"
    raw = body.encode("utf-8")
    (d / "sess-mb.jsonl").write_bytes(raw)

    # 🔴 THE FIXTURE'S OWN CONTROL, AND IT ALREADY EARNED ITS KEEP. The first
    # attempt hard-coded tail_bytes=3000 and every line was the same length, so
    # the cut landed exactly on a rune boundary and the test would have passed
    # while exercising nothing. Rather than pick another magic number that could
    # drift back into alignment when the fixture is edited, SEARCH for an offset
    # that provably splits a rune, and fail loudly if none exists.
    tail_bytes = None
    # The search window must be WIDER than one record: the multi-byte runes sit
    # inside the `text` field, so a run of consecutive offsets lands in the
    # surrounding ASCII and splits nothing. Measured on this fixture: offsets
    # 2910-3022 are all rune-aligned, which is exactly the gap a narrow window
    # fell into on the first attempt.
    for candidate in range(2900, 3300):
        if candidate >= len(raw):
            continue
        try:
            raw[len(raw) - candidate:].decode("utf-8")
        except UnicodeDecodeError:
            tail_bytes = candidate
            break
    assert tail_bytes is not None, (
        "no offset in 2900-3300 splits a rune in this fixture — it cannot see the hazard it is "
        "written for, so a green here would mean nothing"
    )

    proc = run_builder(projects.root, empty_digest(tmp_path), tmp_path, tail_bytes=tail_bytes)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    payload = json.loads(proc.stdout)
    tail = payload["sessions"][0]["tail"]
    tail.encode("utf-8")  # raises on an unencodable surrogate


def test_the_builder_is_read_only(projects, tmp_path):
    """🔴 A SECURITY PROPERTY, NOT A DESCRIPTION. Asserted by snapshotting the
    whole transcript tree — contents AND mtimes — around a run."""
    f = projects("sess-a", transcript("sess-a", human_turn("do it")))
    before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in projects.root.rglob("*")
              if p.is_file()}
    assert before, "the snapshot is empty — this assertion covers nothing"

    proc = run_builder(projects.root, empty_digest(tmp_path), tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr

    after = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in projects.root.rglob("*")
             if p.is_file()}
    assert after == before, "the builder modified the transcript tree"
    assert f.exists()


# ── 8. the unit ──────────────────────────────────────────────────────────────


def _unit_block() -> str:
    """The transcript-push SERVICE block from nix/home.nix, service start to timer start."""
    text = HOME_NIX.read_text()
    idx = text.index("systemd.user.services.transcript-push")
    end = text.index("systemd.user.timers.transcript-push")
    return text[idx:end]


def test_the_unit_is_NOT_serverMode_gated():
    """🔴 THE ASYMMETRY WITH tmux-snapshot-push IS THE POINT, AND IT IS THE ONE
    THING A COPY-PASTE OF THAT UNIT WOULD GET WRONG.

    That feeder is workbench-only as a CORRECTNESS requirement — its collector
    already reaches both hosts over ssh, so a second reporter would fight over
    every row. Transcripts are local files, readable from nowhere else, so a
    serverMode gate here would feed exactly half the fleet and every laptop
    session would render "no transcript stored" for ever, silently and for ever.
    """
    text = HOME_NIX.read_text()
    idx = text.index("systemd.user.timers.transcript-push")
    block = text[idx : idx + 1200]
    wanted = 'WantedBy = lib.optionals enableTranscriptPush [ "timers.target" ];'
    assert wanted in block, f"the transcript timer's WantedBy is not the ungated form:\n{block}"
    # And the sibling's gate really is different, so this is a measured contrast
    # rather than a claim about a file nobody read.
    sib = text.index("systemd.user.timers.tmux-snapshot-push")
    sib_block = text[sib : sib + 1200]
    assert "serverMode && enableTmuxSnapshotPush" in sib_block, (
        "the sibling timer is no longer serverMode-gated — this test's contrast is stale"
    )


def test_the_unit_does_not_wire_the_DND_defeating_failure_toast():
    """🔴 notify-failure@ toasts are wired to DEFEAT do-not-disturb, and that
    bypass is justified by a MEASURED rate of ~1 firing in 9 days. This timer runs
    every 5 minutes, so any sustained outage — the laptop asleep, clawgate
    mid-redeploy — would fire one on EVERY tick and burn down the one alert
    channel that has to keep its meaning.
    """
    block = _unit_block()

    # 🔴 STRIP COMMENTS FIRST, and this is not a refinement — the naive
    # `"OnFailure" not in block` version of this assertion FAILED against the
    # very unit it was written to approve, because the block explains at length
    # WHY there is no OnFailure. Measured here, and measured before in the
    # sibling suite, which carries the same note. A guard that reads prose is not
    # a guard on configuration: it would equally have passed a unit that wired
    # OnFailure while calling it something else in a comment, and it punishes the
    # documentation that makes the absence deliberate rather than accidental.
    config = "\n".join(ln for ln in block.splitlines() if not ln.strip().startswith("#"))
    offenders = [ln for ln in config.splitlines() if "OnFailure" in ln]
    assert not offenders, (
        "the transcript feeder wired OnFailure=notify-failure@; at a 5-minute cadence a "
        f"sustained outage would fire a DND-bypassing toast on every tick. Offenders: {offenders}"
    )
    # Positive control for the stripper: it must still be able to SEE a real
    # setting, or the assertion above is a fact about an empty string.
    assert "ExecStart" in config and "transcript-push.sh" in config


def test_the_unit_PATH_carries_the_binaries_the_script_needs():
    """🔴 EVERY BINARY IS FOR THE CHILD — under systemd there is no login-shell
    PATH to fall back on. drift-check.service paid for this lesson: without its
    binaries the child reported COULD NOT MEASURE on every run for ever, from a
    unit that looked completely correct.
    """
    block = _unit_block()
    for pkg in ["pkgs.bash", "pkgs.coreutils", "pkgs.curl", "pkgs.gnused", "pkgs.python3"]:
        assert pkg in block, f"the transcript feeder's PATH is missing {pkg}"


def test_the_unit_restart_triggers_name_EVERY_hard_dependency():
    """Every file this unit cannot run without is declared as a trigger.

    Asserted as a SET, not a list of `in` checks, because a membership test grows
    silently — which is how this list reached four entries having been described
    as "both halves" through three of them.

    ⚠ WHAT A MISSING TRIGGER COSTS HERE IS ONE TICK, AND THE PROSE IN THIS FILE
    SAID OTHERWISE THROUGH TWO REVISIONS. This unit is `Type=oneshot` on a
    5-minute timer whose ExecStart names the WORKING-TREE path, so the next tick
    execs current code regardless. The indefinitely-stale consequence belongs to
    the RESIDENT reply agent, which imports once and runs for weeks — see the
    two tests below, which are the ones where that reasoning applies. The
    declaration is still worth pinning (a dependency that can exit the unit
    should be visible in the unit), but this test is not guarding an outage.
    """
    import re

    block = _unit_block()
    m = re.search(r"X-Restart-Triggers = \[(.*?)\]", block, re.S)
    assert m, "the transcript-push unit declares no X-Restart-Triggers at all"
    declared = set(re.findall(r"\$\{\.\./([^}]+)\}", m.group(1)))
    want = {
        "scripts/transcript-push.sh",
        "scripts/lib/build_transcript_push.py",
        "scripts/lib/host_label.py",
        # 🔴 THE BUILDER IMPORTS IT AND CANNOT RUN WITHOUT IT (ModuleNotFoundError,
        # rc 1), and it was the OLDEST omission here — the builder has imported it
        # since the feeder shipped, before the delta tailer existed. A hand-written
        # want-set is a ledger only while somebody checks it against the thing it
        # describes: this one was pinning the 3-set that omitted it, so the gap was
        # not merely unnoticed, it was locked in by its own guard. That is why the
        # test below derives the set from SOURCE instead.
        "scripts/lib/transcript_search.py",
    }
    assert declared == want, (
        f"the transcript-push unit's restart triggers are {sorted(declared)}, want "
        f"{sorted(want)} — every file this unit hard-depends on must be one. A missing "
        "one costs at most one 5-minute tick here (oneshot + working-tree ExecStart), "
        "but an undeclared hard dependency is invisible to anyone reading the unit")


def test_the_script_and_builder_are_executable():
    """A NEW file must be `git add`ed AND executable, or the deploy succeeds and
    the unit fails at exec time."""
    assert os.access(SCRIPT, os.X_OK), f"{SCRIPT} is not executable"
    assert os.access(BUILDER, os.X_OK), f"{BUILDER} is not executable"


def test_a_SUBAGENT_transcript_is_never_pushed(projects, tmp_path):
    """🔴 SUBAGENT TRANSCRIPTS ARE NOT RESUMABLE SESSIONS, and they are the BULK
    of the corpus: measured on this host 2026-09-04, 4,884 of the 5,788 `.jsonl`
    files under `~/.claude/projects` — 84% — live under a `subagents/` directory.

    Nothing can ever join them to anything the chat view is reached from: no
    attention entry carries a subagent id, and session-manager's
    `claude_session_id` is the MAIN session's. Feeding them would fill the read
    model with thousands of unreachable rows, each up to the tail cap, against a
    retention sweep sized for real sessions.

    The exclusion comes from `transcript_search.is_corpus_member`, which is the
    ONE rule the whole repo's transcript readers share. This test exists because
    the first version of the builder open-coded its own walk and got the right
    answer BY ACCIDENT — it only descended one level, so it missed them without
    any rule saying it should. A later "make this recursive" edit would have been
    silently catastrophic.
    """
    d = projects.root / "-home-zach-workspace-devrc"
    d.mkdir(parents=True, exist_ok=True)
    sub = d / "subagents"
    sub.mkdir(exist_ok=True)
    (sub / "agent-deadbeef.jsonl").write_text(
        transcript("agent-deadbeef", human_turn("subagent work", "agent-deadbeef"))
    )
    projects("sess-main", transcript("sess-main", human_turn("main work", "sess-main")))

    proc = run_builder(projects.root, empty_digest(tmp_path), tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    ids = [s["sessionId"] for s in json.loads(proc.stdout)["sessions"]]

    assert "agent-deadbeef" not in ids, (
        "a subagent transcript was pushed — it is unreachable from every surface the chat "
        f"view is linked from, and they are 84% of the corpus. Got: {ids}"
    )
    # POSITIVE CONTROL: the walk found the real one, so the absence above is not a
    # fact about a walk that enumerated nothing.
    assert ids == ["sess-main"], f"the main session was not picked up either: {ids}"


def _reply_agent_unit_block() -> str:
    """The tmux-reply-agent SERVICE block from nix/home.nix."""
    text = HOME_NIX.read_text()
    idx = text.index("systemd.user.services.tmux-reply-agent")
    end = text.index("systemd.user.services", idx + 10)
    return text[idx:end]


def test_the_RESIDENT_agent_restart_triggers_name_EVERY_module_it_imports():
    """🔴 THE COVERAGE USED TO SIT WHERE IT MATTERED LEAST. This file already pins
    that the transcript-push TIMER names both its halves — where a stale copy
    costs at most five minutes, because the next tick execs fresh code.

    `tmux-reply-agent` is a RESIDENT service: it imports each module ONCE and then
    runs for weeks. A fix to `transcript_stream.py` (or to `transcript_search.py`,
    which it loads in turn) would land on disk and the running agent would keep
    executing the old code indefinitely — a correctness change to what the
    operator reads about a session that appears deployed and is not. The unit's
    own comment states the rule for `tmux_text_policy.py`; the two transcript
    modules were added afterwards and did not inherit it.

    ⚠ ASSERTED AS A SET, NOT AS A LIST OF `in` CHECKS. A membership test grows
    silently: the next module loaded at startup passes without anyone noticing,
    which is exactly how this gap opened.
    """
    import re

    block = _reply_agent_unit_block()
    m = re.search(r"X-Restart-Triggers = \[(.*?)\]", block, re.S)
    assert m, "the reply-agent unit declares no X-Restart-Triggers at all"
    declared = set(re.findall(r"\$\{\.\./([^}]+)\}", m.group(1)))

    want = {
        "scripts/tmux-reply-agent",
        "scripts/lib/tmux_text_policy.py",
        "scripts/lib/host_label.py",
        "scripts/lib/transcript_stream.py",
        "scripts/lib/transcript_search.py",
    }
    assert declared == want, (
        f"the reply-agent's restart triggers are {sorted(declared)}, want {sorted(want)}. "
        "A module this RESIDENT unit imports at startup but does not trigger on stays on "
        "the OLD code until something else restarts the process."
    )


def test_every_module_the_agent_loads_by_path_IS_a_restart_trigger():
    """🔴 THE RELATIONSHIP, NOT THE LIST. The test above pins a set someone typed;
    this one derives the set from the AGENT'S OWN SOURCE, so a module added to the
    agent tomorrow fails here rather than passing unseen.

    It scans for the loader idiom the agent actually uses — `os.path.join(_HERE,
    "lib", "<name>")` — plus the one transitive hop `transcript_stream` makes.
    """
    import re

    agent = (REPO_ROOT / "scripts" / "tmux-reply-agent").read_text()
    loaded = set(re.findall(r'os\.path\.join\(_HERE,\s*"lib",\s*"([^"]+)"\)', agent))
    assert loaded, "the scanner found no path-loaded modules — it is measuring nothing"

    stream = (REPO_ROOT / "scripts" / "lib" / "transcript_stream.py").read_text()
    transitive = set(re.findall(r'os\.path\.join\(_HERE,\s*"([^"]+\.py)"\)', stream))
    # 🔴 A POSITIVE CONTROL ON THE TRANSITIVE HOP TOO. The agent scan above has
    # one; this one did not, so if its regex ever stopped matching,
    # transcript_search.py would silently drop out of `loaded`, `missing` would be
    # empty, and this test would PASS while the exact gap it exists to close
    # reopened.
    assert transitive, ("the transitive scanner found no path-loaded modules in "
                        "transcript_stream.py — it is measuring nothing")
    loaded |= transitive

    block = _reply_agent_unit_block()
    m = re.search(r"X-Restart-Triggers = \[(.*?)\]", block, re.S)
    declared = set(re.findall(r"\$\{\.\./scripts/lib/([^}]+)\}", m.group(1)))
    missing = loaded - declared
    assert not missing, (
        f"the agent loads {sorted(missing)} at startup but the unit does not trigger on "
        "them — a resident service would keep running the old copy indefinitely"
    )


def test_every_lib_module_this_UNIT_hard_depends_on_IS_a_restart_trigger():
    """🔴 THE HALF THE HAND-WRITTEN LEDGER LEFT OPEN. `transcript_search.py` was
    absent from the timer's triggers from the day the feeder shipped, and the
    test that grades those triggers is a set somebody typed — so it graded the
    omission as correct. The agent unit already had a source-derived counterpart;
    the timer had none, which is precisely why the gap survived on this side.

    So: derive the timer's expected set from the two files the unit actually
    runs. A fifth dependency added to either tomorrow fails here rather than
    waiting for someone to notice the `want` set is one short.

    🔴 BOTH SOURCES, BECAUSE THE UNIT HAS TWO. The first draft of this test
    scanned only the builder's imports and its own positive control caught that:
    `host_label.py` is resolved by the SHELL (`transcript-push.sh:99`, fatal at
    exit 3), never imported by the builder, so a builder-only scanner would have
    declared the ledger complete while being blind to the dependency that
    actually kills the unit.

    ⚠ WHAT THIS GUARDS IS A DECLARATION, NOT AN OUTAGE — this unit is oneshot on a
    timer with a working-tree ExecStart, so a missing trigger costs one tick. The
    resident-agent sibling above is the one where staleness is unbounded, and
    conflating the two is the error this arc made twice.
    """
    import re

    libdir = REPO_ROOT / "scripts" / "lib"

    # (a) what the BUILDER imports — plain sibling imports off its own sys.path.
    builder = (REPO_ROOT / "scripts" / "lib" / "build_transcript_push.py").read_text()
    names = set(re.findall(r"^(?:from|import)\s+([a-z_][a-z0-9_]*)", builder, re.M))
    needed = {f"{n}.py" for n in names if (libdir / f"{n}.py").exists()}

    # (b) what the SHELL resolves out of lib/ — a different mechanism, same
    #     consequence when it is missing.
    shell = (REPO_ROOT / "scripts" / "transcript-push.sh").read_text()
    needed |= {m for m in re.findall(r'/lib/([a-z_][a-z0-9_]*\.py)', shell)
               if (libdir / m).exists()}

    # 🔴 POSITIVE CONTROL, AND IT EARNED ITS KEEP IMMEDIATELY. A scanner that
    # matches nothing yields an empty `missing` and PASSES — the same reassuring
    # zero this arc has been bitten by repeatedly. Both names below are known to
    # be real dependencies, one per mechanism, so this fails if either scanner
    # stops measuring.
    assert {"transcript_search.py", "host_label.py"} <= needed, (
        f"the dependency scanner found {sorted(needed)} — it is not measuring the "
        "unit's real dependencies, so its verdict is about the regex")

    block = _unit_block()
    m = re.search(r"X-Restart-Triggers = \[(.*?)\]", block, re.S)
    assert m, "the transcript-push unit declares no X-Restart-Triggers at all"
    declared = set(re.findall(r"\$\{\.\./scripts/lib/([^}]+)\}", m.group(1)))
    missing = needed - declared
    assert not missing, (
        f"the transcript-push unit hard-depends on {sorted(missing)} from scripts/lib "
        "but does not declare them as restart triggers — the unit's hard dependencies "
        "must be readable from the unit")


def test_an_UNDECODABLE_byte_makes_fileBytes_UNKNOWN_instead_of_400ing_the_WHOLE_push(
    server, projects, tmp_path
):
    """🔴 ONE BAD BYTE IN ONE SMALL TRANSCRIPT WOULD 400 THE ENTIRE HOST'S FEED.

    `read_tail` decodes with `errors="replace"`, which turns one bad byte into a
    three-byte U+FFFD — so a small corrupt file produces a TAIL LARGER THAN THE
    FILE IT CAME FROM. The server rejects a `fileBytes` smaller than the tail
    beside it (a cursor pointing before the start of the stored tail is a splice
    waiting to happen) and a rejection is atomic, so that one session would take
    every other session in the request down with it — on every tick, for the whole
    24-hour candidate window.

    Reporting UNKNOWN instead costs that session a stream reseed and nothing else.

    Measured on the guard and its absence:
        shipped   file=29 tail_bytes=31 fileBytes=0   -> push accepted
        no guard  file=29 tail_bytes=31 fileBytes=29  -> whole push REJECTED
    """
    d = projects.root / "-home-zach-workspace-devrc"
    d.mkdir(exist_ok=True)
    (d / "corrupt.jsonl").write_bytes(b'{"type":"user","t":"\xff"}\n')
    projects("healthy", transcript("healthy", human_turn("fine", "healthy")))

    proc = run_push(
        projects_root=projects.root,
        tmp_path=tmp_path,
        env_extra={"CLAWGATE_API_URL": base_url(server), "CLAWGATE_HOOK_TOKEN": "t"},
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    sent = {s["sessionId"]: s for s in json.loads(pushes(server)[0]["body"])["sessions"]}
    assert "healthy" in sent, "the innocent session was lost"
    bad = sent.get("corrupt")
    assert bad is not None, "the corrupt session was dropped entirely"
    assert len(bad["tail"].encode("utf-8")) > bad["fileBytes"] or bad["fileBytes"] == 0
    assert bad["fileBytes"] == 0, (
        f"fileBytes={bad['fileBytes']} for a {len(bad['tail'].encode())}-byte tail taken from a "
        "smaller file — the server rejects that, atomically, taking every other session with it"
    )


def test_fileBytes_is_derived_from_what_was_READ_not_from_the_stat(tmp_path):
    """🔴 THE FILE IS BEING APPENDED TO WHILE IT IS READ — that is the normal case
    here, not an edge one, because the sessions worth feeding are the live ones.
    `size` is a stat taken BEFORE the read, so bytes that arrive in between come
    back in `raw` and the tail ends past `size`. Reporting `size` would put the
    cursor BEHIND the stored tail's true end, and the next delta would duplicate
    the bytes in between.

    Driven directly rather than through the script: reproducing the race with a
    real concurrent writer would be a timing test for a property that is decidable
    by construction. The fixture instead makes `stat` under-report by patching it,
    which is exactly the observable the race produces.
    """
    import importlib.util

    # The builder imports `transcript_search` as a bare name, which works because
    # it is normally RUN as a script from scripts/lib. Loading it as a module here
    # needs that directory on the path — the same arrangement, spelled out.
    sys.path.insert(0, str(BUILDER.parent))
    try:
        spec = importlib.util.spec_from_file_location("btp_under_test", BUILDER)
        btp = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(btp)
    finally:
        sys.path.remove(str(BUILDER.parent))

    body = transcript("s", *[human_turn(f"turn {i}", "s") for i in range(20)])
    f = tmp_path / "s.jsonl"
    f.write_text(body)
    real_size = f.stat().st_size

    class _Stat:
        st_size = real_size - 40  # the stat that a concurrent append raced

    class _Path(type(f)):
        def stat(self, *a, **kw):
            return _Stat()

    text, truncated, file_bytes = btp.read_tail(_Path(f), 1 << 20)
    assert not truncated
    assert file_bytes == real_size, (
        f"fileBytes={file_bytes} but the read returned {real_size} bytes — the cursor would "
        "sit BEHIND the end of the stored tail and the next delta would duplicate"
    )


def test_ONE_oversized_session_alone_is_still_sent_rather_than_dropped_for_ever(
    server, projects, tmp_path
):
    """🔴 THE `and sessions` EXEMPTION, whose comment promises "deferred to the
    next tick rather than dropped for ever". Without it, a session whose tail
    alone exceeds the aggregate budget is skipped on EVERY tick — and since it is
    also the newest, it is skipped first, every time. The exemption admits it when
    the push is otherwise empty.
    """
    body = transcript("big", *[human_turn("y" * 400, "big") for _ in range(40)])
    projects("big", body)
    per = len(body.encode())

    proc = run_push(
        projects_root=projects.root,
        tmp_path=tmp_path,
        env_extra={
            "CLAWGATE_API_URL": base_url(server),
            "CLAWGATE_HOOK_TOKEN": "t",
            "TRANSCRIPT_PUSH_MAX_BYTES": str(per // 4),  # far under one session
        },
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    # 🔴 THE ABSENCE IS THE DEFECT, SO NAME IT. Without the exemption the builder
    # produces an EMPTY payload and exits 10 ("nothing to push") — the script then
    # exits 0 having POSTed nothing, and a test that indexed straight into the
    # request list would die with an IndexError that names no mechanism at all.
    assert pushes(server), (
        "a session larger than the whole budget was dropped rather than sent alone: NOTHING "
        "was pushed. It is the newest file, so it would be dropped first on every tick, for "
        f"ever. Script said: {proc.stdout.strip()[:200]}")
    sent = json.loads(pushes(server)[0]["body"])["sessions"]
    assert [s["sessionId"] for s in sent] == ["big"], (
        "a session larger than the whole budget was dropped rather than sent alone — it is "
        "the newest file, so it would be dropped first on every tick, for ever"
    )


# ---------------------------------------------------------------------------
# THE CROSS-FEEDER HOST SEAM.
#
# 🔴 THE TWO FEEDERS WRITE THE SAME ROW'S `host` COLUMN, AND NOTHING PINNED THEM
# TOGETHER. This push derived it in shell (falling through to `uname -n`, which is
# "nixos" on BOTH machines); tmux-reply-agent read the collector's env file and
# said "workbench". The disagreement was free while `host` was display-only — the
# stored rows just said "nixos", uselessly — and became a PERMANENT REFUSAL the
# moment the delta stream made `host` a correctness predicate: every 5-minute
# push stamping `nixos` back, every 5-second poll reseeding because "the host
# changed". Caught before deploy, with both values measured side by side.
# ---------------------------------------------------------------------------


def _host_label_module():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "host_label_under_test", REPO_ROOT / "scripts" / "lib" / "host_label.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _agent_module():
    import importlib.machinery
    import importlib.util

    path = str(REPO_ROOT / "scripts" / "tmux-reply-agent")
    loader = importlib.machinery.SourceFileLoader("tmux_reply_agent_hostseam", path)
    spec = importlib.util.spec_from_file_location("tmux_reply_agent_hostseam", path, loader=loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


@pytest.mark.parametrize("env_body,want", [
    ('ACTIVITY_HOST=laptop\n', "laptop"),
    ('ACTIVITY_HOST="workbench"\n', "workbench"),
    ('ACTIVITY_HOST=not-a-real-host\n', "workbench"),   # invalid -> default
    ('', "workbench"),                                    # absent -> default
])
def test_BOTH_feeders_resolve_the_SAME_host_label(server, projects, tmp_path, env_body, want):
    """🔴 THE ASSERTION IS THAT THE TWO AGREE, NOT THAT EITHER IS RIGHT. Pinning
    each side to a literal separately is what let them drift: both tests would
    stay green while the two values differed.

    The push is driven end to end (so the SHELL's resolution is what is measured,
    not a Python restatement of it) and compared with what the agent computes from
    the same file.
    """
    env_file = tmp_path / "activity-env"
    env_file.write_text(env_body)

    agent = _agent_module()
    agent_label = agent.local_host_label(env={}, env_file=str(env_file))

    # The shell half, through the real script. TRANSCRIPT_PUSH_HOST is deliberately
    # NOT set — that override is what the other tests use, and using it here would
    # bypass the very resolution under test.
    projects("host-seam", transcript("host-seam", human_turn("hi", "host-seam")))
    conf = tmp_path / "clawgate.env"
    conf.write_text("")
    env = dict(os.environ)
    for k in ("CLAWGATE_API_URL", "CLAWGATE_HOOK_TOKEN", "ACTIVITY_HOST", "TRANSCRIPT_PUSH_HOST"):
        env.pop(k, None)
    env.update({
        "CLAWGATE_CONF_FILE": str(conf),
        "CLAUDE_PROJECTS_DIR": str(projects.root),
        "HOME": str(tmp_path),
        "CLAWGATE_API_URL": base_url(server),
        "CLAWGATE_HOOK_TOKEN": "t",
        # Point the shared module at the fixture's env file.
        "HOST_LABEL_ENV_FILE": str(env_file),
    })
    proc = subprocess.run(["bash", str(SCRIPT)], capture_output=True, text=True,
                          env=env, timeout=180)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    pushed = json.loads(pushes(server)[0]["body"])["host"]

    assert agent_label == want, f"the agent resolved {agent_label!r}, want {want!r}"
    assert pushed == agent_label, (
        f"the two feeders that write the SAME row disagree about this host: the bulk push says "
        f"{pushed!r}, the stream agent says {agent_label!r}. Since `host` became a correctness "
        f"predicate, that difference is a permanent reseed loop — the push stamping one value "
        f"back every 5 minutes and the stream refusing every append 5 seconds later."
    )


def test_the_push_REFUSES_rather_than_guessing_when_the_label_cannot_be_resolved(
    server, projects, tmp_path
):
    """🔴 A FALLBACK IS WHAT PRODUCED THE DEFECT. `uname -n` returns a plausible
    name on both machines, so the old fallback failed silently and looked correct.
    Exiting is loud and cannot be mistaken for a working feed.
    """
    projects("s", transcript("s", human_turn("hi")))
    proc = run_push(
        projects_root=projects.root,
        tmp_path=tmp_path,
        env_extra={
            "CLAWGATE_API_URL": base_url(server),
            "CLAWGATE_HOOK_TOKEN": "t",
            "TRANSCRIPT_PUSH_HOST": "",  # the override is empty, so resolution runs
            "TRANSCRIPT_PUSH_HOST_LABEL": str(tmp_path / "no-such-module.py"),
        },
    )
    assert proc.returncode == 3, f"rc={proc.returncode}: {proc.stdout}{proc.stderr}"
    assert "refusing to push under a guessed name" in proc.stdout
    assert not pushes(server), "a push went out under a guessed host name"


def test_the_agent_reads_the_env_file_the_SHARED_MODULE_names(tmp_path):
    """🔴 THE PATH IS THE THIRD LITERAL, AND RE-EXPORTING THE OTHER TWO IS NOT
    ENOUGH. The consolidation re-exported HOST_NAMES and DEFAULT_LOCAL_HOST
    "rather than restating two literals" and left the env-file PATH restated in
    the agent — and because `local_host_label` passes it as the DEFAULT
    `env_file`, host_label.ACTIVITY_ENV was dead there. Measured: pointing the
    agent's copy at /nonexistent changed nothing, 203/203 still passed.

    That is the exact axis that produced the earlier blocker: two feeders reading
    two files. `test_BOTH_feeders_resolve_the_SAME_host_label` cannot see it — it
    passes `env_file=` explicitly, so it exercises the module's PARSING and never
    the agent's own path.

    🔴 IT RUNS IN A SUBPROCESS BECAUSE THE BINDING IS AT IMPORT TIME. Setting the
    variable after importing would prove nothing about what a real agent does.
    """
    env_file = tmp_path / "activity-env"
    env_file.write_text("ACTIVITY_HOST=laptop\n")

    snippet = (
        "import importlib.machinery, importlib.util, sys\n"
        "loader = importlib.machinery.SourceFileLoader('a', sys.argv[1])\n"
        "spec = importlib.util.spec_from_file_location('a', sys.argv[1], loader=loader)\n"
        "m = importlib.util.module_from_spec(spec); loader.exec_module(m)\n"
        # no env_file= argument: the DEFAULT is what is under test
        "print(m.local_host_label(env={}))\n"
    )
    env = dict(os.environ, HOST_LABEL_ENV_FILE=str(env_file))
    env.pop("ACTIVITY_HOST", None)
    out = subprocess.run(
        [sys.executable, "-c", snippet, str(REPO_ROOT / "scripts" / "tmux-reply-agent")],
        capture_output=True, text=True, env=env, timeout=120)
    assert out.returncode == 0, out.stdout + out.stderr
    got = out.stdout.strip()
    assert got == "laptop", (
        f"the agent resolved {got!r} from its DEFAULT env-file path, want 'laptop' — it is not "
        f"reading the file the shared module names, so the two feeders can be pointed at "
        f"different files and the reseed loop comes back invisibly"
    )


def test_the_BULK_push_dedupes_and_strips_session_ids_TOO(server, projects, tmp_path):
    """🔴 THE GUARD WENT ON THE PATH WHERE THE BLAST RADIUS IS SMALLER FIRST.
    The delta stream grew it, and there a duplicate costs ONE session. Here
    `NormalizePush` rejects THE WHOLE PUSH on a duplicate or an empty id, a
    rejection stores nothing, so the digest never matches, so the same poisoned
    batch is re-sent every tick — permanently, for every session on this host.
    And this is the feed the streaming design designates as the RECONCILER.

    Measured before the fix, straight out of the builder:

        emitted sessionIds: ['abc ', 'abc', 'same-uuid', 'same-uuid']
        after the server's TrimSpace: ['abc', 'abc', 'same-uuid', 'same-uuid']
        duplicates the server rejects the WHOLE push on: ['abc', 'same-uuid']

    ⚠ RAISING MAX_PER_PUSH 6 -> 48 WIDENED THIS, which is why the change that
    widened it is the one that had to close it.
    """
    body = transcript("x", human_turn("hi", "x"))
    # Same <uuid> under two project directories.
    projects("same-uuid", body, project="-home-zach-projA")
    projects("same-uuid", transcript("y", human_turn("hi there", "y")), project="-home-zach-projB")
    # "abc.jsonl" beside "abc .jsonl" — distinct on disk, one id after TrimSpace.
    projects("abc", body, project="-home-zach-projA")
    projects("abc ", transcript("z", human_turn("hello", "z")), project="-home-zach-projA")
    projects("healthy", transcript("healthy", human_turn("fine", "healthy")))

    proc = run_push(
        projects_root=projects.root,
        tmp_path=tmp_path,
        env_extra={"CLAWGATE_API_URL": base_url(server), "CLAWGATE_HOOK_TOKEN": "t"},
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert pushes(server), "nothing was pushed at all: " + proc.stdout

    ids = [s["sessionId"] for s in json.loads(pushes(server)[0]["body"])["sessions"]]
    # The server TrimSpaces before checking, so that is the comparison that matters.
    trimmed = [i.strip() for i in ids]
    assert len(trimmed) == len(set(trimmed)), (
        f"the bulk push carries ids that collide after the server's TrimSpace: {ids}. "
        "NormalizePush rejects the WHOLE push on that, nothing is stored, the digest never "
        "matches, and the same batch is re-sent on every tick for ever.")
    assert "" not in trimmed, f"an id that strips to empty was pushed: {ids}"
    assert "healthy" in trimmed, "the innocent session was dropped along with the duplicates"
