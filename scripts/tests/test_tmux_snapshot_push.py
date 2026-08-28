"""Behavioural tests for scripts/tmux-snapshot-push.sh — the host-side feeder
for clawgate's cross-host tmux read model.

Everything here runs against a STUB collector and a STUB HTTP server bound to
loopback on an ephemeral port. Nothing touches the real clawgate, the real
`session-manager`, either host's tmux, or the network.

WHAT THIS SUITE IS FOR
----------------------
The pusher is a timer-driven pipe, so its failure mode is silence: it can stop
delivering and everything downstream still renders the LAST snapshot, which
looks like a quiet fleet rather than a broken feeder. The load-bearing tests are
therefore not the happy path:

  1. THE POSITIVE CONTROL. `test_a_valid_document_is_pushed_and_the_server_sees
     _it` proves the stub server can observe a request at all. Every "nothing
     was posted" assertion below is meaningless without it — a zero from a
     harness wired to nothing is indistinguishable from a real zero.

  2. VERBATIM DELIVERY. The whole design rests on the host being a dumb pipe and
     the SERVER owning the vocabulary rename (`session` -> `tmuxSessionName`).
     A well-meaning future edit that "tidies" the payload here would move that
     rule to a second place and silently re-open the collision. The test
     compares the received body BYTE FOR BYTE with the collector's stdout, so
     any reshaping at all reds it.

  3. rc-PER-CONDITION. A collector that died, a server that is too old, a server
     that is unreachable and a missing token need four different fixes. One
     "something failed" code would tell an operator nothing, so each gets its
     own and each is asserted separately.

  4. ENV-BEATS-FILE. This is a REGRESSION GUARD, not a preference. The sibling
     `clawgate-stop-hook.sh` sources the same credential file with `set -a`, so
     the file beats the environment there; the measured consequence was a probe
     aimed at a harmless address silently POSTing to PRODUCTION. If this script
     ever acquires that behaviour, a test run could write into the real read
     model.

  5. THE TOKEN IS NOT IN argv. Asserted through a stub `curl` that records its
     own argv, not by reading the source — a structural check would type-check
     past a second curl invocation added later.
"""

import json
import os
import shutil
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "tmux-snapshot-push.sh"
HOME_NIX = REPO_ROOT / "nix" / "home.nix"

sys.path.insert(0, str(REPO_ROOT / "scripts"))

# 🔴 EVERY RUNTIME STUB HERE GOES THROUGH `testlib.mockbin.write_exec`, WHICH
# OWNS THE SHEBANG. Do not hand-write one.
#
# The nix check sandbox — the tier that gates the merge — has no
# `/usr/bin/env`, so a stub carrying that shebang cannot exec. It does not
# present as a fixture fault: the stub fails, the script under test correctly
# reports "collector failed", and the assertions that fire are about rc and
# request counts, pointing squarely at production code. Eleven tests in this
# file failed exactly that way while the dev host stayed green.
#
# My first fix was a hand-rolled absolute `#!{shutil.which("bash")}`. That
# execs fine, but it is the SIXTH open-coding of a rule this repo has already
# consolidated — `scripts/testlib/mockbin.py` exists precisely because five
# earlier sites re-derived it and the bug came back anyway, and
# `test_runtime_shebangs.py` is the repo-wide scan that catches exactly this.
# Its instructions say not to allowlist your way past it, so this file uses the
# one implementation. `write_exec` raises if a call site supplies its own
# shebang, which is what makes the consolidation hold.
from testlib.mockbin import (  # noqa: E402
    SH,
    SHEBANG,
    interpreter_is_executable,
    write_exec,
)

# Assembled from character codes, never written as a quoted literal — the
# repo-wide scan in `test_runtime_shebangs.py` matches any source line carrying
# a quoted `#!`, and it deliberately cannot tell a real offender from a test
# that merely mentions one. `testlib/shebang_scan.py` builds its own needles the
# same way for the same reason. Writing the literal here would make this file an
# offender for talking about the rule it follows.
HASHBANG = chr(35) + chr(33)

# A minimal but REAL-SHAPED session-manager document. Deliberately carries the
# producer's own spelling of a tmux session (`session`) so that if anyone ever
# adds client-side reshaping, the verbatim test has something to catch it on.
SAMPLE_DOC = {
    "ts": "2026-08-28T17:04:13Z",
    "local_host": "workbench",
    "hosts": {
        "workbench": {
            "reachable": True,
            "windows": [
                {"window_id": "@41", "session": "scratch2", "window_name": "gold"}
            ],
        },
        "laptop": {
            "reachable": True,
            "windows": [
                {"window_id": "@7", "session": "main", "window_name": "blue"}
            ],
        },
    },
}

# The same document, hand-formatted so that it is NOT what any JSON serialiser
# would emit: irregular indentation, a trailing space before a newline, and keys
# in an order `sort_keys` would change. Used by the verbatim test so that a
# normalising round-trip is detectable as a byte difference. See that test's
# docstring — a fixture built by json.dumps made this undetectable.
RAW_DOC = (
    '{"ts":"2026-08-28T17:04:13Z",   "local_host":"workbench",\n'
    '  "hosts" : {"workbench":{"windows":[{"window_name":"gold",'
    '"session":"scratch2","window_id":"@41"}],"reachable":true},\n'
    '   "laptop":{"windows":[{"window_name":"blue","session":"main",'
    '"window_id":"@7"}],"reachable":true}}}\n'
)


class _Recorder(HTTPServer):
    """An HTTPServer that records every request it is given."""

    def __init__(self, addr, handler, status=200, body=b'{"ok":true}'):
        super().__init__(addr, handler)
        self.requests = []
        self.reply_status = status
        self.reply_body = body


class _Handler(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802 - name fixed by BaseHTTPRequestHandler
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length)
        self.server.requests.append(
            {
                "path": self.path,
                "body": raw,
                "auth": self.headers.get("Authorization"),
                "content_type": self.headers.get("Content-Type"),
            }
        )
        self.send_response(self.server.reply_status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(self.server.reply_body)))
        self.end_headers()
        self.wfile.write(self.server.reply_body)

    def log_message(self, format, *args):  # noqa: A002 - signature fixed by the base class
        pass  # keep pytest output clean


@pytest.fixture
def server():
    srv = _Recorder(("127.0.0.1", 0), _Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield srv
    srv.shutdown()
    srv.server_close()


@pytest.fixture
def collector(tmp_path):
    """A stub `session-manager` whose stdout the test controls."""

    counter = {"n": 0}

    def _make(stdout: str, *, rc: int = 0, stderr: str = "") -> Path:
        # 🔴 The stub `cat`s a data file rather than `printf`-ing a literal.
        # printf '%s' with a shell-quoted string mangles the payload — a
        # newline arrives as a literal backslash-n — which made the verbatim
        # test fail for a reason that had nothing to do with the script. The
        # collector's stdout has to be delivered BYTE-EXACT or this whole file
        # is measuring the fixture instead of the code.
        counter["n"] += 1
        n = counter["n"]
        data = tmp_path / f"stub-stdout-{n}.bin"
        data.write_text(stdout)
        errdata = tmp_path / f"stub-stderr-{n}.bin"
        errdata.write_text(stderr)
        path = tmp_path / f"stub-session-manager-{n}"
        # POSIX-sh body, no shebang — write_exec owns that.
        return write_exec(
            path,
            f"cat {data}\n"
            f"cat {errdata} >&2\n"
            f"exit {rc}\n",
        )

    return _make


def run_push(*, collector_path, tmp_path, env_extra=None, conf_text=None, path_prefix=None):
    conf = tmp_path / "clawgate.env"
    conf.write_text(conf_text if conf_text is not None else "")
    env = dict(os.environ)
    # Start from a clean slate so an operator's real credentials in the ambient
    # environment can never leak into a test run and aim it at production.
    env.pop("CLAWGATE_API_URL", None)
    env.pop("CLAWGATE_HOOK_TOKEN", None)
    env["CLAWGATE_CONF_FILE"] = str(conf)
    env["TMUX_PUSH_COLLECTOR"] = str(collector_path)
    env["HOME"] = str(tmp_path)
    if path_prefix:
        env["PATH"] = f"{path_prefix}:{env['PATH']}"
    env.update(env_extra or {})
    return subprocess.run(
        ["bash", str(SCRIPT)],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )


def base_url(server):
    host, port = server.server_address[0], server.server_address[1]
    return f"http://{host}:{port}"


# ── 1. the positive control ──────────────────────────────────────────────────


def test_a_valid_document_is_pushed_and_the_server_sees_it(server, collector, tmp_path):
    """🔴 THE POSITIVE CONTROL for this whole file.

    Every "nothing was posted" assertion below counts requests and expects 0. A
    0 from a stub server that can never observe anything is indistinguishable
    from a real 0, so this test exists to show the count CAN move.
    """
    doc = json.dumps(SAMPLE_DOC)
    proc = run_push(
        collector_path=collector(doc),
        tmp_path=tmp_path,
        env_extra={"CLAWGATE_API_URL": base_url(server), "CLAWGATE_HOOK_TOKEN": "tok-abc"},
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert len(server.requests) == 1, "the harness never observed a request"
    req = server.requests[0]
    assert req["path"] == "/api/tmux/snapshot"
    assert req["auth"] == "Bearer tok-abc"


def test_the_body_is_the_collectors_stdout_BYTE_FOR_BYTE(server, collector, tmp_path):
    """🔴 THE DUMB-PIPE CONTRACT.

    The server owns the `session` -> `tmuxSessionName` rename. If this script
    ever reshapes, filters or re-serialises the payload, that rule lives in two
    places and the vocabulary collision quietly comes back. Comparing bytes —
    not parsed equality — means even a re-serialisation that preserves meaning
    (key reordering, whitespace) fails this test, which is the intent.

    🔴 THE FIXTURE IS DELIBERATELY NOT `json.dumps` OUTPUT, and that is the only
    reason this test can see anything. The first version used
    `json.dumps(SAMPLE_DOC)`, and a mutant that piped the payload through
    `json.dumps(json.load(...))` SURVIVED a green run — because a round-trip of
    json.dumps output is byte-identical to itself. A fixture derived from the
    transformation it is meant to detect cannot detect it. RAW_DOC below uses
    irregular whitespace and non-sorted keys that no re-serialiser would
    reproduce, so any normalisation at all moves the bytes.
    """
    doc = RAW_DOC
    # 🔴 THE FIXTURE'S OWN CONTROL. If RAW_DOC ever becomes something a
    # serialiser would emit, this test silently stops being able to see a
    # normalising mutant while still passing. Pin the property, not the habit.
    parsed = json.loads(RAW_DOC)
    assert RAW_DOC != json.dumps(parsed)
    assert RAW_DOC != json.dumps(parsed, sort_keys=True)
    assert RAW_DOC != json.dumps(parsed, separators=(",", ":"))
    assert RAW_DOC != json.dumps(parsed, indent=2)

    proc = run_push(
        collector_path=collector(doc),
        tmp_path=tmp_path,
        env_extra={"CLAWGATE_API_URL": base_url(server), "CLAWGATE_HOOK_TOKEN": "t"},
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert server.requests[0]["body"] == doc.encode()
    # And the producer's own spelling survived the trip untouched — the thing
    # the server is responsible for renaming must still be there to rename.
    assert b'"session":' in server.requests[0]["body"]


# ── 2. rc per condition ──────────────────────────────────────────────────────


def test_no_token_exits_2_and_posts_NOTHING(server, collector, tmp_path):
    proc = run_push(
        collector_path=collector(json.dumps(SAMPLE_DOC)),
        tmp_path=tmp_path,
        env_extra={"CLAWGATE_API_URL": base_url(server)},
    )
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert server.requests == []


def test_a_collector_that_fails_exits_3_and_posts_nothing(server, collector, tmp_path):
    proc = run_push(
        collector_path=collector("", rc=7, stderr="ssh: connect timed out"),
        tmp_path=tmp_path,
        env_extra={"CLAWGATE_API_URL": base_url(server), "CLAWGATE_HOOK_TOKEN": "t"},
    )
    assert proc.returncode == 3, proc.stdout + proc.stderr
    assert server.requests == []
    assert "collector failed" in proc.stdout


def test_a_failing_collector_REPORTS_ITS_REAL_EXIT_CODE(server, collector, tmp_path):
    """🔴 REGRESSION GUARD for a bug an audit found and the suite could not see.

    The original code was `if ! timeout …; then rc=$?`, which reads the status of
    the NEGATED pipeline — 0 exactly when the branch is taken. So every collector
    failure logged `rc=0`. The sibling test above passes `rc=7` and asserts only
    that "collector failed" appears, so a mutation replacing the capture with a
    literal `rc=0` SURVIVED the whole file: the shipped code was byte-equivalent
    to a mutant nothing could detect.

    This asserts the NUMBER, which is the thing that was wrong.
    """
    proc = run_push(
        collector_path=collector("", rc=42, stderr="ssh exploded"),
        tmp_path=tmp_path,
        env_extra={"CLAWGATE_API_URL": base_url(server), "CLAWGATE_HOOK_TOKEN": "t"},
    )
    assert proc.returncode == 3, proc.stdout + proc.stderr
    assert "rc=42" in proc.stdout, f"real exit code not reported: {proc.stdout!r}"
    assert "rc=0" not in proc.stdout
    assert server.requests == []


def test_a_collector_TIMEOUT_is_named_rather_than_logged_as_an_empty_diagnostic(
    server, tmp_path
):
    """The worst case of the bug above: `timeout` kills the collector, so stderr
    is EMPTY and the status is 124. Under the old code that produced
    `collector failed (rc=0):` — a log line with no information, from the path
    most likely to happen in production (a wedged ssh to a sleeping laptop)."""
    slow = tmp_path / "slow-collector"
    write_exec(slow, "sleep 30\n")
    proc = run_push(
        collector_path=slow,
        tmp_path=tmp_path,
        env_extra={
            "CLAWGATE_API_URL": base_url(server),
            "CLAWGATE_HOOK_TOKEN": "t",
            "TMUX_PUSH_COLLECT_TIMEOUT": "1",
        },
    )
    assert proc.returncode == 3, proc.stdout + proc.stderr
    assert "TIMED OUT" in proc.stdout, proc.stdout
    assert "rc=124" in proc.stdout, proc.stdout
    assert server.requests == []


@pytest.mark.parametrize("status", [301, 302, 307])
def test_a_REDIRECT_is_a_failure_not_a_silent_success(collector, tmp_path, status):
    """🔴 An audit finding, and the nastiest shape in this file.

    Success used to be `HTTP < 400`. There is no `-L`, so a 3xx meant curl
    returned the redirect, the server stored NOTHING, and this script logged
    "pushed" and exited 0 — at a 2-minute cadence, with no consumer watching the
    read model, invisible indefinitely. The realistic trigger is pointing
    CLAWGATE_API_URL at an ingress hostname instead of the origin.
    """
    srv = _Recorder(("127.0.0.1", 0), _Handler, status=status, body=b"")
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        proc = run_push(
            collector_path=collector(json.dumps(SAMPLE_DOC)),
            tmp_path=tmp_path,
            env_extra={"CLAWGATE_API_URL": base_url(srv), "CLAWGATE_HOOK_TOKEN": "t"},
        )
    finally:
        srv.shutdown()
        srv.server_close()
    assert proc.returncode == 5, proc.stdout + proc.stderr
    assert "REDIRECT" in proc.stdout, proc.stdout
    assert "pushed" not in proc.stdout


def test_304_is_rejected_WITHOUT_being_called_a_redirect(collector, tmp_path):
    """304 is "not modified", not a redirect. Lumping it into the 3xx arm told
    the operator to re-point CLAWGATE_API_URL at the origin — a diagnosis that
    would send them off in entirely the wrong direction. It must still fail
    (nothing was stored), just without the misleading advice."""
    srv = _Recorder(("127.0.0.1", 0), _Handler, status=304, body=b"")
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        proc = run_push(
            collector_path=collector(json.dumps(SAMPLE_DOC)),
            tmp_path=tmp_path,
            env_extra={"CLAWGATE_API_URL": base_url(srv), "CLAWGATE_HOOK_TOKEN": "t"},
        )
    finally:
        srv.shutdown()
        srv.server_close()
    assert proc.returncode == 5, proc.stdout + proc.stderr
    assert "REDIRECT" not in proc.stdout, proc.stdout
    assert "304" in proc.stdout


def test_a_TORN_collection_is_refused_rather_than_overwriting_a_good_snapshot(
    server, collector, tmp_path
):
    """🔴 THE ISOLATION-SEAM GUARD. An audit finding, and the discriminant was
    being thrown away at exactly the last place that still had it.

    `session-manager` publishes `windows_measured` SEPARATELY from `reachable`
    so an unmeasured zero is distinguishable from a real one. The clawgate
    server decodes only `reachable` and `windows`, so the fact dies at ingest —
    and because the table is a latest-per-host upsert with no reaper, a
    reachable-but-unenumerated host would replace a good 44-window snapshot with
    a zero that nothing downstream could ever identify as false.
    """
    doc = {
        "ts": "2026-08-28T17:04:13Z",
        "hosts": {
            "workbench": {"reachable": True, "windows_measured": True,
                          "windows": [{"window_id": "@1", "session": "s"}]},
            "laptop": {"reachable": True, "windows_measured": False, "windows": []},
        },
    }
    proc = run_push(
        collector_path=collector(json.dumps(doc)),
        tmp_path=tmp_path,
        env_extra={"CLAWGATE_API_URL": base_url(server), "CLAWGATE_HOOK_TOKEN": "t"},
    )
    assert proc.returncode == 6, proc.stdout + proc.stderr
    assert "laptop" in proc.stdout
    assert server.requests == [], "a torn collection was pushed"


def test_an_UNREACHABLE_host_is_still_pushed(server, collector, tmp_path):
    """The other half of the bound, and it is why the guard keys on
    `windows_measured` rather than on emptiness.

    A sleeping laptop is a REAL state change, not a torn read — the server keeps
    the `reachable` flag so a consumer can render it. Refusing this would stop
    the feeder every night and freeze the workbench's data too.
    """
    doc = {
        "ts": "2026-08-28T17:04:13Z",
        "hosts": {
            "workbench": {"reachable": True, "windows_measured": True,
                          "windows": [{"window_id": "@1", "session": "s"}]},
            "laptop": {"reachable": False, "windows_measured": False, "windows": []},
        },
    }
    proc = run_push(
        collector_path=collector(json.dumps(doc)),
        tmp_path=tmp_path,
        env_extra={"CLAWGATE_API_URL": base_url(server), "CLAWGATE_HOOK_TOKEN": "t"},
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert len(server.requests) == 1


def test_an_ABSENT_windows_measured_reads_as_MEASURED_not_as_torn(
    server, collector, tmp_path
):
    """🔴 Pins the safety property the gate's own comment spends three lines on,
    and which a mutation survey found UNGUARDED.

    `windows_measured is False` is not `not windows_measured`. A producer that
    never emitted the field must read as measured — otherwise every host looks
    permanently torn and the feeder freezes forever, failing closed on a field
    whose absence means "older collector", not "bad read". A mutant relaxing the
    test to `is not True` survived the suite before this existed.
    """
    doc = {
        "ts": "2026-08-28T17:04:13Z",
        "hosts": {"workbench": {"reachable": True, "windows": []}},  # no windows_measured
    }
    proc = run_push(
        collector_path=collector(json.dumps(doc)),
        tmp_path=tmp_path,
        env_extra={"CLAWGATE_API_URL": base_url(server), "CLAWGATE_HOOK_TOKEN": "t"},
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert len(server.requests) == 1


def test_a_host_that_REPORTED_windows_is_not_torn_whatever_the_flag_says(
    server, collector, tmp_path
):
    """The other half of the same predicate, also found unguarded: the emptiness
    term. If we have the windows, we have the data — the flag cannot make that
    a tear, and dropping the emptiness check would refuse a perfectly good
    snapshot."""
    doc = {
        "ts": "2026-08-28T17:04:13Z",
        "hosts": {
            "workbench": {
                "reachable": True,
                "windows_measured": False,          # says unmeasured …
                "windows": [{"window_id": "@1", "session": "s"}],  # … but we HAVE windows
            }
        },
    }
    proc = run_push(
        collector_path=collector(json.dumps(doc)),
        tmp_path=tmp_path,
        env_extra={"CLAWGATE_API_URL": base_url(server), "CLAWGATE_HOOK_TOKEN": "t"},
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert len(server.requests) == 1


def test_a_GENUINELY_MEASURED_zero_is_still_pushed(server, collector, tmp_path):
    """A workbench that really has no tmux windows — the OnStartupSec run after a
    reboot, before tmux starts — is real data. Refusing it would be the
    silent-zero error inverted."""
    doc = {
        "ts": "2026-08-28T17:04:13Z",
        "hosts": {
            "workbench": {"reachable": True, "windows_measured": True, "windows": []},
        },
    }
    proc = run_push(
        collector_path=collector(json.dumps(doc)),
        tmp_path=tmp_path,
        env_extra={"CLAWGATE_API_URL": base_url(server), "CLAWGATE_HOOK_TOKEN": "t"},
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert len(server.requests) == 1


@pytest.mark.parametrize(
    "conf_line,expected",
    [
        ('CLAWGATE_HOOK_TOKEN=plain', "plain"),
        ('export CLAWGATE_HOOK_TOKEN=exported', "exported"),
        ('   CLAWGATE_HOOK_TOKEN=indented', "indented"),
        ('export   CLAWGATE_HOOK_TOKEN="quoted-export"', "quoted-export"),
    ],
)
def test_the_conf_reader_accepts_the_spellings_a_SOURCEABLE_file_carries(
    server, collector, tmp_path, conf_line, expected
):
    """An audit finding. The reader anchored on a bare `^KEY=` while its comment
    claimed it matched shell sourcing.

    `~/.claude/clawgate.env` exists to be sourced — `clawgate-hook.sh` sources
    it — so adding `export ` is an ordinary edit. Under the old anchor that made
    THIS reader return empty and exit 2 every two minutes while the hook kept
    working: one file, two readers, silently disagreeing.
    """
    proc = run_push(
        collector_path=collector(json.dumps(SAMPLE_DOC)),
        tmp_path=tmp_path,
        conf_text=f"CLAWGATE_API_URL={base_url(server)}\n{conf_line}\n",
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert server.requests[0]["auth"] == f"Bearer {expected}"


def test_a_collector_emitting_non_json_exits_3(server, collector, tmp_path):
    proc = run_push(
        collector_path=collector("<html>502 Bad Gateway</html>"),
        tmp_path=tmp_path,
        env_extra={"CLAWGATE_API_URL": base_url(server), "CLAWGATE_HOOK_TOKEN": "t"},
    )
    assert proc.returncode == 3, proc.stdout + proc.stderr
    assert server.requests == []


def test_a_document_with_no_hosts_exits_3(server, collector, tmp_path):
    """An empty `hosts` object would be accepted as well-formed JSON and then
    rejected by the server. Catching it here keeps "the collector produced
    nothing" distinguishable from "the server refused a real document"."""
    proc = run_push(
        collector_path=collector(json.dumps({"ts": "x", "hosts": {}})),
        tmp_path=tmp_path,
        env_extra={"CLAWGATE_API_URL": base_url(server), "CLAWGATE_HOOK_TOKEN": "t"},
    )
    assert proc.returncode == 3, proc.stdout + proc.stderr
    assert server.requests == []


def test_a_404_exits_5_and_names_the_deploy_ORDER(collector, tmp_path):
    """A server too old to know the route is a DEPLOY-ORDER problem (server
    first), not a payload problem. The message has to say so or the operator
    debugs the payload — which is exactly what an unhelpful 'rejected' line
    would cause."""
    srv = _Recorder(("127.0.0.1", 0), _Handler, status=404, body=b"not found")
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        proc = run_push(
            collector_path=collector(json.dumps(SAMPLE_DOC)),
            tmp_path=tmp_path,
            env_extra={"CLAWGATE_API_URL": base_url(srv), "CLAWGATE_HOOK_TOKEN": "t"},
        )
    finally:
        srv.shutdown()
        srv.server_close()
    assert proc.returncode == 5, proc.stdout + proc.stderr
    assert "404" in proc.stdout
    assert "deploy the server first" in proc.stdout


def test_a_500_exits_5(collector, tmp_path):
    srv = _Recorder(("127.0.0.1", 0), _Handler, status=500, body=b'{"error":"boom"}')
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        proc = run_push(
            collector_path=collector(json.dumps(SAMPLE_DOC)),
            tmp_path=tmp_path,
            env_extra={"CLAWGATE_API_URL": base_url(srv), "CLAWGATE_HOOK_TOKEN": "t"},
        )
    finally:
        srv.shutdown()
        srv.server_close()
    assert proc.returncode == 5, proc.stdout + proc.stderr
    # 🔴 Distinct from the 404 branch: a 500 must NOT tell the operator to go
    # look at the deploy order.
    assert "deploy the server first" not in proc.stdout


def test_an_unreachable_server_exits_4(collector, tmp_path):
    """Bound to a port nothing is listening on. Deliberately a CLOSED port on
    loopback rather than a black-hole address: RFC 5737 TEST-NET-1 does not
    reliably hang here — curl fails it instantly with rc 28 — so a test built on
    it would be measuring something other than 'unreachable'."""
    srv = _Recorder(("127.0.0.1", 0), _Handler)
    dead = base_url(srv)
    srv.server_close()  # free the port; nothing is listening now
    proc = run_push(
        collector_path=collector(json.dumps(SAMPLE_DOC)),
        tmp_path=tmp_path,
        env_extra={"CLAWGATE_API_URL": dead, "CLAWGATE_HOOK_TOKEN": "t"},
    )
    assert proc.returncode == 4, proc.stdout + proc.stderr


# ── 3. credential precedence — the regression guard ──────────────────────────


def test_the_ENVIRONMENT_beats_the_conf_FILE(server, collector, tmp_path):
    """🔴 REGRESSION GUARD, and the direction is the whole point.

    `clawgate-stop-hook.sh` sources this same file with `set -a`, so there the
    FILE wins — and a probe aimed at a harmless address silently POSTed to
    production as a result. Here the environment must win, so pointing this
    script somewhere actually points it there.

    The conf file names a URL that would FAIL if it were used (a closed port),
    so this cannot pass by both values happening to work.
    """
    proc = run_push(
        collector_path=collector(json.dumps(SAMPLE_DOC)),
        tmp_path=tmp_path,
        conf_text=(
            "CLAWGATE_API_URL=http://127.0.0.1:1\n"
            'CLAWGATE_HOOK_TOKEN="tok-from-file"\n'
        ),
        env_extra={"CLAWGATE_API_URL": base_url(server), "CLAWGATE_HOOK_TOKEN": "tok-from-env"},
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert len(server.requests) == 1
    assert server.requests[0]["auth"] == "Bearer tok-from-env"


def test_the_conf_file_is_used_when_the_environment_is_silent(server, collector, tmp_path):
    """The other half of the bound. Asserting only that the environment wins
    would leave the file path free to be deleted entirely — which is how the
    unit actually gets its credentials, since systemd carries no login shell
    environment."""
    proc = run_push(
        collector_path=collector(json.dumps(SAMPLE_DOC)),
        tmp_path=tmp_path,
        conf_text=(
            f"CLAWGATE_API_URL={base_url(server)}\n"
            "CLAWGATE_HOOK_TOKEN='tok-from-file'\n"
        ),
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert len(server.requests) == 1
    # Quotes stripped, both flavours.
    assert server.requests[0]["auth"] == "Bearer tok-from-file"


# ── 4. the token never reaches argv ──────────────────────────────────────────


def test_the_token_is_NOT_passed_on_the_curl_COMMAND_LINE(collector, tmp_path):
    """Everything running as this user can read /proc/<pid>/cmdline.

    Asserted through a stub `curl` that records its own argv rather than by
    grepping the source: a structural check would type-check past a second curl
    invocation someone adds later, and would also pass against a script that
    builds the header correctly and then logs it.
    """
    bindir = tmp_path / "bin"
    bindir.mkdir()
    argv_log = tmp_path / "curl-argv.txt"
    cfg_copy = tmp_path / "curl-cfg-copy.txt"
    stub = bindir / "curl"
    # POSIX-sh body, no shebang — write_exec owns that.
    write_exec(
        stub,
        f'printf "%s\\n" "$@" > {argv_log}\n'
        "prev=\"\"\n"
        "for a in \"$@\"; do\n"
        f'  if [ "$prev" = "--config" ]; then cp "$a" {cfg_copy}; fi\n'
        "  prev=\"$a\"\n"
        "done\n"
        "# emulate: -o <file> -w %{http_code}\n"
        "out=\"\"; prev=\"\"\n"
        "for a in \"$@\"; do\n"
        '  if [ "$prev" = "-o" ]; then out="$a"; fi\n'
        "  prev=\"$a\"\n"
        "done\n"
        '[ -n "$out" ] && printf \'{"ok":true}\' > "$out"\n'
        "printf '200'\n",
    )

    proc = run_push(
        collector_path=collector(json.dumps(SAMPLE_DOC)),
        tmp_path=tmp_path,
        env_extra={"CLAWGATE_API_URL": "http://127.0.0.1:9", "CLAWGATE_HOOK_TOKEN": "sup3rs3cr3t"},
        path_prefix=str(bindir),
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr

    argv = argv_log.read_text()
    # POSITIVE CONTROL: the stub really did capture an argv worth searching.
    assert "--config" in argv, f"the stub curl recorded nothing usable: {argv!r}"
    assert "sup3rs3cr3t" not in argv, "the token reached the curl command line"
    # And it genuinely travelled by the config file, so this is not passing
    # because the token was simply dropped and never sent at all.
    assert "sup3rs3cr3t" in cfg_copy.read_text()
    # The token must not be echoed into the log line either.
    assert "sup3rs3cr3t" not in proc.stdout


# ── 5. hygiene ───────────────────────────────────────────────────────────────


def test_the_scratch_directory_is_removed_on_success_and_on_failure(server, collector, tmp_path):
    """A timer-driven script that leaks a temp dir per run fills the disk
    slowly enough that nothing notices until it matters."""
    scratch = tmp_path / "scratch"
    scratch.mkdir()

    ok = run_push(
        collector_path=collector(json.dumps(SAMPLE_DOC)),
        tmp_path=tmp_path,
        env_extra={
            "CLAWGATE_API_URL": base_url(server),
            "CLAWGATE_HOOK_TOKEN": "t",
            "TMPDIR": str(scratch),
        },
    )
    assert ok.returncode == 0, ok.stdout + ok.stderr
    assert list(scratch.iterdir()) == [], "scratch leaked on the SUCCESS path"

    bad = run_push(
        collector_path=collector("not json"),
        tmp_path=tmp_path,
        env_extra={
            "CLAWGATE_API_URL": base_url(server),
            "CLAWGATE_HOOK_TOKEN": "t",
            "TMPDIR": str(scratch),
        },
    )
    assert bad.returncode == 3
    assert list(scratch.iterdir()) == [], "scratch leaked on the FAILURE path"


# ── 6. the seam with the systemd unit ────────────────────────────────────────


def test_the_unit_PATH_carries_every_binary_the_collector_needs():
    """🔴 A SEAM TEST, and the reason it exists is a measured one.

    Under systemd there is no login-shell PATH. `session-manager` has a
    `python3` shebang, shells out to `tmux`, SSHes to the laptop (openssh), and
    runs `awk` via `agent_ledger.py`. drift-check.service already had to learn
    this the hard way: without its binaries its child reported COULD NOT MEASURE
    on every run forever, from a unit that looked correct.

    ⚠ THIS DOCSTRING USED TO SAY the collector "reads interface addresses to
    decide which host it is on", justifying `iproute2`. It does not —
    `local_host_label` reads ACTIVITY_HOST from the environment. That sentence
    was the audit finding that got iproute2 removed, and removing gawk in the
    same sweep is what broke the ledger, because nothing here listed it.

    ⚠ AND IT OVERSTATES ITSELF: it says RELATIONSHIP (unit PATH ⊇ what the child
    needs), but the body checks a HAND-TYPED list of names. It pins what someone
    remembered, not what the child calls — which is exactly how gawk went
    missing while this test stayed green. Treat the list below as a ledger to
    maintain, and re-measure by RUNNING the collector before removing an entry.
    """
    src = HOME_NIX.read_text()
    marker = "systemd.user.services.tmux-snapshot-push"
    assert marker in src, "the unit is not defined in nix/home.nix"
    block = src.split(marker, 1)[1]
    # Bound the search to this unit's own definition.
    block = block.split("systemd.user.timers.tmux-snapshot-push", 1)[0]
    path_lines = [ln for ln in block.splitlines() if "PATH=" in ln]
    assert path_lines, "the unit sets no PATH — the collector will not run under systemd"
    path_line = path_lines[0]
    # 🔴 `gawk` IS ON THIS LIST BECAUSE ITS ABSENCE IS SILENT, and this guard is
    # why it went missing: an earlier revision dropped gawk from the unit and
    # from this list in the same commit, so nothing reddened. The collector's
    # `agent_ledger.read_command` runs `awk 1 …`, and awk lives only in gawk —
    # coreutils has none. Without it the ledger read returns a well-formed
    # ZERO (the `2>/dev/null; exit 0` hides the error while the `echo` sentinel
    # still prints), and 34 of 45 workbench windows silently lose `runtime` and
    # their ledger-derived age. Measured both ways on this host.
    #
    # This list is hand-maintained, which is the honest limitation: it pins what
    # someone thought to write down, not what the child actually calls. The
    # docstring above says RELATIONSHIP and this is the weaker thing. Before
    # removing any entry here, RUN the collector without it and compare the
    # report — `iproute2` and `gnugrep` were removed that way and stay removed.
    for needed in ("python3", "tmux", "openssh", "coreutils", "curl", "gnused", "bash", "gawk"):
        assert needed in path_line, f"{needed} missing from the unit PATH: {path_line}"


def test_the_unit_gives_tmux_its_SOCKET_directory():
    """A DEFENSIVE invariant, and this docstring is careful about that because
    an earlier version of it claimed a live bug that does not exist.

    THE HAZARD IT GUARDS. This host's tmux socket is at
    $XDG_RUNTIME_DIR/tmux-UID/default, not tmux's compiled-in
    /tmp/tmux-UID/default. A unit that cannot see TMUX_TMPDIR gets a failed
    connect, and session-manager renders that as `reachable: true` with an
    EMPTY windows array — a perfectly valid document. The server accepts it and
    the latest-per-host upsert REPLACES a good 44-window snapshot with zero.
    "A rejected push leaves the previous snapshot in place" cannot help; nothing
    is rejected. The failure is silent and it destroys data.

    🔴 IT DOES NOT HAPPEN TODAY, and the correction matters more than the guard.
    `Environment=` ADDS to the systemd user-manager environment rather than
    replacing it, and that environment already carries
    TMUX_TMPDIR=/run/user/1000. A real transient user unit sees all 44 windows,
    and drift-check.service — same collector, only PATH and HOME set
    explicitly — has reported 42-44 rows in its journal for weeks. The claim of
    a live bug came from an `env -i` probe, which is STRICTER than systemd: it
    blanks the manager environment and so measured a condition the unit never
    runs in. A simulation that is harsher than production does not prove a
    production defect.

    So this pins a cheap belt-and-braces property: the unit should not depend on
    an inherited value for something whose absence destroys data. Asserted as a
    PROPERTY (tmux is given a runtime socket dir), not the literal `%t`, so
    /run/user/%U also passes.
    """
    src = HOME_NIX.read_text()
    marker = "systemd.user.services.tmux-snapshot-push"
    assert marker in src
    block = src.split(marker, 1)[1].split("systemd.user.timers.tmux-snapshot-push", 1)[0]
    config = "\n".join(
        ln for ln in block.splitlines() if not ln.strip().startswith("#")
    )
    tmux_env = [ln for ln in config.splitlines() if "TMUX_TMPDIR=" in ln]
    assert tmux_env, (
        "the unit does not set TMUX_TMPDIR — the collector will report the local "
        "host as reachable with ZERO windows and overwrite a good snapshot"
    )
    # It must point at the runtime dir, not /tmp: %t and /run/user/%U are the
    # two correct spellings; /tmp is exactly the wrong default we are overriding.
    line = tmux_env[0]
    assert ("%t" in line) or ("/run/user" in line), f"suspicious TMUX_TMPDIR: {line}"
    assert "/tmp" not in line, f"TMUX_TMPDIR points at /tmp, which is the broken default: {line}"


def test_the_unit_does_not_wire_the_DND_defeating_failure_toast():
    """🔴 DELIBERATE ABSENCE, pinned so nobody 'fixes' it.

    `notify-failure@` toasts are wired to defeat do-not-disturb
    (override_pause_level = 100), justified in nix/home.nix by a MEASURED rate
    of roughly one firing in nine days. This timer runs many times an hour, so
    on any sustained outage — the laptop asleep, clawgate redeploying — it would
    fire a DND-bypassing toast on every tick and burn down the one alert channel
    that must keep its meaning.

    ⚠ THE COMPENSATING CONTROL IS NOT WHAT THIS DOCSTRING USED TO SAY. It
    claimed the server's `receivedAt` stamp made a stopped feeder visible as a
    stale timestamp. Nothing reads `GET /api/tmux/snapshot` outside clawgate's
    own tests — no UI, no page, no script — so that staleness is recorded and
    unread. The real control is this unit being `Type=oneshot` with distinct
    non-zero exit codes, which land in the user manager's failed-unit list that
    `claude/skills/standup/standup.sh` reads. That covers the exit codes and NOT
    a run that exits 0 having achieved nothing, which is why the redirect and
    unmeasured-zero cases are handled in the script instead.
    """
    src = HOME_NIX.read_text()
    marker = "systemd.user.services.tmux-snapshot-push"
    assert marker in src
    block = src.split(marker, 1)[1].split("systemd.user.timers.tmux-snapshot-push", 1)[0]

    # 🔴 STRIP COMMENTS FIRST. The naive `"OnFailure" not in block` version of
    # this assertion failed against the very unit it was written to approve,
    # because the block explains at length WHY there is no OnFailure. A guard
    # that reads prose is not a guard on configuration — it would equally have
    # passed a unit that wired OnFailure while calling it something else in a
    # comment, and it blocks the documentation that makes the absence
    # deliberate rather than accidental.
    config = "\n".join(
        ln for ln in block.splitlines() if not ln.strip().startswith("#")
    )
    offenders = [ln for ln in config.splitlines() if "OnFailure" in ln]
    assert not offenders, (
        "the pusher must not wire OnFailure=notify-failure@ — see this test's "
        f"docstring. Offending lines: {offenders}"
    )

    # POSITIVE CONTROL: the comment-stripper must not have eaten the whole unit,
    # which would make the assertion above vacuously true for any input.
    assert "ExecStart" in config, "the comment stripper removed the unit body"
    assert "TimeoutStartSec" in config


def test_the_script_is_executable_and_bash():
    """🔴 ASSERT THE INTERPRETER, NOT THE LITERAL SHEBANG LINE.

    An earlier version pinned the exact source spelling and went red in the nix
    check sandbox — because the flake runs nixpkgs' `patchShebangs` over the
    source tree, so the store copy the sandbox tests reads
    `#!/nix/store/…-bash-…/bin/bash`. Both spellings are correct; what the unit
    needs is only that this is a bash script, and it invokes it as
    `bash <path>` regardless.
    """
    assert SCRIPT.exists(), SCRIPT
    assert os.access(SCRIPT, os.X_OK), "not executable"
    first = SCRIPT.read_text().splitlines()[0]
    assert first.startswith(HASHBANG), f"no shebang: {first!r}"
    assert first.rstrip().endswith("bash"), f"not a bash shebang: {first!r}"


def test_bash_and_python3_are_available_to_this_suite():
    """POSITIVE CONTROL for the suite's own dependencies: every test above shells
    out to bash and the script itself shells out to python3. If either were
    missing, the rc assertions would still 'pass' for the wrong reason on some
    of the failure-path tests."""
    assert shutil.which("bash"), "bash missing — every test here is vacuous"
    assert shutil.which("python3"), "python3 missing — the script's parse gate cannot run"


def test_a_STUB_CAN_ACTUALLY_EXEC_in_this_environment(tmp_path):
    """🔴 THE FIXTURE'S OWN POSITIVE CONTROL, and it exists because its absence
    cost a full gate run.

    Every behavioural test here depends on a generated stub being executable BY
    THE KERNEL, which depends on its shebang naming an interpreter that exists.
    The nix check sandbox has no `/usr/bin/env` — and the way that failure
    presented was NOT 'the fixture is broken'. The stub failed to exec, the
    script under test correctly reported 'collector failed', and eleven tests
    failed with assertions about rc and request counts, i.e. pointing squarely
    at the production code. The dev host was green the whole time.

    So this asserts the mechanism directly, through the same helper the fixtures
    use: when an environment breaks stub execution, exactly one test fails and
    it says so, instead of eleven blaming the code.
    """
    assert interpreter_is_executable(), (
        f"{SH} is not runnable here — every stub-based test in this file is vacuous"
    )
    stub = write_exec(tmp_path / "canary", "printf 'ran'\n")
    out = subprocess.run([str(stub)], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, f"stub failed to exec: {out.stderr!r}"
    assert out.stdout == "ran"


def test_write_exec_REFUSES_a_caller_supplied_shebang():
    """The consolidation only holds because the helper enforces it. If this ever
    stops raising, every call site is free to reintroduce the hazard and the
    repo-wide scan becomes the only thing standing between us and an
    eleven-test sandbox failure that blames the wrong code."""
    with pytest.raises(AssertionError):
        # The helper's OWN shebang constant, not a literal — see HASHBANG above.
        write_exec(Path("/tmp/never-written-by-this-test"), SHEBANG + "printf x\n")
