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


def test_the_client_bounds_sit_UNDER_the_servers_not_at_them(server, projects, tmp_path):
    """🔴 A CLIENT TUNED EXACTLY TO THE SERVER'S CAP TURNS ANY ROUNDING
    DISAGREEMENT INTO A FEEDER THAT FAILS EVERY TICK while looking correctly
    configured. The server's numbers are read here as LITERALS on purpose — this
    is a cross-repo contract and the point is to fail loudly when clawgate
    changes one.
    """
    server_max_tail_bytes = 256 * 1024  # transcript.MaxTailBytes
    server_max_sessions = 8  # transcript.MaxSessionsPerPush

    text = SCRIPT.read_text()
    tail_default = _shell_default(text, "TAIL_BYTES", "TRANSCRIPT_PUSH_TAIL_BYTES")
    sess_default = _shell_default(text, "MAX_PER_PUSH", "TRANSCRIPT_PUSH_MAX_SESSIONS")

    assert tail_default < server_max_tail_bytes, (
        f"the default tail ({tail_default}) is not UNDER the server's cap ({server_max_tail_bytes})"
    )
    assert sess_default < server_max_sessions, (
        f"the default session count ({sess_default}) is not UNDER the server's cap ({server_max_sessions})"
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


def test_the_unit_restart_triggers_name_BOTH_halves():
    """The builder decides WHICH sessions are sent and HOW MUCH of each. A change
    there changes what this unit delivers with no edit to the shell at all, so a
    trigger on the shell alone would leave a deployed unit running the old rule.
    """
    block = _unit_block()
    assert "../scripts/transcript-push.sh" in block
    assert "../scripts/lib/build_transcript_push.py" in block


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
