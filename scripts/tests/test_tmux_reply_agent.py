"""Behavioural tests for scripts/tmux-reply-agent — the host half of clawgate's
terminal WRITE surface.

Everything here runs against a STUB HTTP server bound to loopback on an ephemeral
port and a STUB tmux binary that records its own argv. Nothing touches the real
clawgate, either host's tmux server, any pane, or the network.

WHAT THIS SUITE IS FOR
----------------------
🔴 ONE DELIVERY BY THIS AGENT IS ARBITRARY COMMAND EXECUTION AS THE OPERATOR.
It runs `tmux send-keys` into a named pane and then presses Enter, on text that
arrived over a LAN route with no human auth. So the load-bearing tests are not
the happy path — they are the four ways that execution can go wrong in a way
nobody would notice:

  1. THE TEXT MUST NEVER REACH A SHELL. It is arbitrary operator input, so
     `$(…)`, backticks, `;` and quotes in it are code the moment anything
     interpolates them into a command string. `test_the_text_is_delivered_as_one
     _argv_element_with_no_shell` asserts the exact bytes arrive as a single
     argument.

  2. `-l` AND `--` ARE PART OF THE COMMAND, NOT DECORATION. Without `-l`,
     `send-keys` reads its argument as KEY NAMES — a reply containing "C-c"
     interrupts whatever is running instead of typing three characters. Without
     `--`, text starting with `-` is parsed as a flag.

  3. THE WRITE-TIME GUARD MUST ACTUALLY REFUSE. A tmux server restart re-mints
     pane ids from %0, so a stored `%12` names a different pane. The refusal has
     to happen with NO send-keys at all — a guard that runs after the write is
     not a guard.

  4. THE TEXT MUST NEVER BE LOGGED. The operator chose a full audit log INCLUDING
     the reply text, and was told the cost: a reply may contain a secret. The
     audit log lives in the server's table behind the terminal token; the
     journal does not.

  5. THE POSITIVE CONTROL. `test_a_claimed_write_is_delivered_and_reported`
     proves the stub tmux and the stub server can observe anything at all. Every
     "no send-keys happened" assertion below is meaningless without it.

⚠ THE AGENT SHIPS DISABLED and cannot be exercised on a live host at all: the
server's routes answer 503 until CLAWGATE_TERMINAL_TOKEN is provisioned, and the
unit is not wired into `default.target` until `enableTmuxReplyAgent` is true.
That is why these tests drive the script directly against stubs rather than
asserting anything about a running system.
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "tmux-reply-agent"
COLLECTOR = REPO_ROOT / "scripts" / "session-manager"
HOME_NIX = REPO_ROOT / "nix" / "home.nix"

sys.path.insert(0, str(REPO_ROOT / "scripts"))

# 🔴 EVERY RUNTIME STUB GOES THROUGH `testlib.mockbin.write_exec`, WHICH OWNS THE
# SHEBANG. The nix check sandbox — the tier that gates the merge — has no
# `/usr/bin/env`, so a hand-written shebang cannot exec there, and the failure
# does not present as a fixture fault: the stub fails, the code under test
# correctly reports an error, and the assertion that fires points at production.
from testlib.mockbin import write_exec  # noqa: E402
from testlib import nix_units  # noqa: E402

# A synthetic marker used wherever a test needs to prove a specific string did or
# did not travel. Pairwise distinct from every other literal in this file, so a
# match cannot come from somewhere else.
MARKER = "ZZ-SYNTHETIC-REPLY-MARKER-4471-ZZ"


def load_agent():
    """Import the agent as a module so its pure functions can be tested directly.

    It has no `.py` extension (matching `session-manager` and the rest of
    `scripts/`), so it is loaded by path.
    """
    loader = importlib.machinery.SourceFileLoader("tmux_reply_agent_undertest", str(SCRIPT))
    spec = importlib.util.spec_from_file_location("tmux_reply_agent_undertest", str(SCRIPT),
                                                  loader=loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


AGENT = load_agent()


# --------------------------------------------------------------------------- #
# The stub server: hands out a fixed claim batch, records every request.
# --------------------------------------------------------------------------- #
class _Recorder(HTTPServer):
    def __init__(self, addr, handler):
        super().__init__(addr, handler)
        self.requests = []
        self.claim_batches = []      # popped one per claim; [] when exhausted
        self.claim_status = 200
        self.result_status = 200


class _Handler(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802 — name fixed by BaseHTTPRequestHandler
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length)
        self.server.requests.append({
            "path": self.path,
            "body": raw,
            "auth": self.headers.get("Authorization"),
        })
        if self.path.endswith("/claim"):
            status = self.server.claim_status
            if status != 200:
                self._reply(status, b'{"error":"no"}')
                return
            batch = self.server.claim_batches.pop(0) if self.server.claim_batches else []
            self._reply(200, json.dumps({"writes": batch}).encode())
            return
        self._reply(self.server.result_status, b'{"ok":true}')

    def _reply(self, status, body):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):  # noqa: A002 — signature fixed by the base class
        pass


@pytest.fixture
def server():
    srv = _Recorder(("127.0.0.1", 0), _Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield srv
    srv.shutdown()
    srv.server_close()


def base_url(server) -> str:
    return f"http://{server.server_address[0]}:{server.server_address[1]}"


@pytest.fixture
def tmux_stub(tmp_path):
    """A stub `tmux` that appends its own argv, one JSON array per invocation.

    🔴 IT RECORDS ARGV, NOT A COMMAND LINE. The whole point of the shell test is
    that the text arrives as ONE argument; a stub that recorded `"$*"` would
    flatten exactly the distinction under test and report success either way.
    """
    log = tmp_path / "tmux-argv.jsonl"
    log.write_text("")
    server_id = tmp_path / "tmux-server-id"
    server_id.write_text("1234:5678\n")
    rc_file = tmp_path / "tmux-sendkeys-rc"
    rc_file.write_text("0")
    err_file = tmp_path / "tmux-sendkeys-err"
    err_file.write_text("")
    new_window_out = tmp_path / "tmux-new-window-out"
    new_window_out.write_text("%77\n")

    path = tmp_path / "bin" / "tmux"
    path.parent.mkdir(exist_ok=True)
    # POSIX-sh body, no shebang — write_exec owns that. Python does the JSON
    # quoting so an argument containing quotes, backslashes or newlines is
    # recorded faithfully rather than through a shell's idea of escaping.
    write_exec(path, (
        f'''if [ "$1" = "-V" ]; then echo 'tmux 3.4'; exit 0; fi\n'''
        f'''python3 -c 'import json,sys; open(sys.argv[1],"a").write(json.dumps(sys.argv[2:])+"\\n")' '''
        f'''{log} "$@"\n'''
        f'''if [ "$1" = "display-message" ]; then cat {server_id}; exit 0; fi\n'''
        f'''if [ "$1" = "new-window" ]; then cat {new_window_out}; exit 0; fi\n'''
        f'''if [ "$1" = "send-keys" ]; then cat {err_file} >&2; exit "$(cat {rc_file})"; fi\n'''
        f'''exit 0\n'''
    ))

    class Stub:
        binary = path
        argv_log = log

        @staticmethod
        def calls():
            return [json.loads(line) for line in log.read_text().splitlines() if line.strip()]

        @staticmethod
        def send_keys_calls():
            return [c for c in Stub.calls() if c and c[0] == "send-keys"]

        @staticmethod
        def set_server_id(value):
            server_id.write_text(value + "\n")

        @staticmethod
        def fail_send_keys(rc=1, stderr=""):
            rc_file.write_text(str(rc))
            err_file.write_text(stderr)

        @staticmethod
        def set_new_window_output(value):
            new_window_out.write_text(value)

        @staticmethod
        def new_pane_id():
            return new_window_out.read_text().strip()

    return Stub


def write(**kw):
    """One claim-batch entry, with defaults that are pairwise distinct."""
    out = {
        "id": "w-abc123",
        "kind": "send-keys",
        "cwd": "",
        "host": "workbench",
        "pane": "%12",
        "text": "yes, go ahead",
        "submit": True,
        "expectTmuxServerId": "",
    }
    out.update(kw)
    return out


def run_agent(server, tmux_stub, tmp_path, *, env_extra=None, conf_text=None,
              token="not-a-real-terminal-token-not-a-real-token", timeout=30):
    """Run the agent for ONE productive round and then let it stop.

    The loop is driven to completion by the stub server: the first claim returns
    the batch, the second returns an empty one, and TMUX_REPLY_MAX_ROUNDS is not
    a thing the agent has — so the process is stopped with SIGTERM once the
    expected traffic has been seen.
    """
    conf = tmp_path / "clawgate.env"
    conf.write_text(conf_text if conf_text is not None else "")
    env = dict(os.environ)
    # Start from a clean slate so an operator's real credentials in the ambient
    # environment can never leak into a test run and aim it at production.
    for k in ("CLAWGATE_API_URL", "CLAWGATE_TERMINAL_TOKEN", "CLAWGATE_HOOK_TOKEN", "ACTIVITY_HOST"):
        env.pop(k, None)
    env["CLAWGATE_CONF_FILE"] = str(conf)
    env["HOME"] = str(tmp_path)
    env["CLAWGATE_API_URL"] = base_url(server)
    if token:
        env["CLAWGATE_TERMINAL_TOKEN"] = token
    env["TMUX_REPLY_TMUX_BIN"] = str(tmux_stub.binary)
    env["TMUX_REPLY_POLL_SECONDS"] = "0.05"
    env["TMUX_REPLY_BACKOFF_SECONDS"] = "0.05"
    env["ACTIVITY_HOST"] = "workbench"
    env.update(env_extra or {})

    proc = subprocess.Popen([sys.executable, str(SCRIPT)], env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    # Wait until the stub has seen enough traffic, then stop the loop.
    deadline = threading.Event()
    for _ in range(int(timeout / 0.05)):
        if proc.poll() is not None:
            break
        if len(server.requests) >= 2:
            break
        deadline.wait(0.05)
    proc.terminate()
    out, _ = proc.communicate(timeout=timeout)
    return proc.returncode, out


# --------------------------------------------------------------------------- #
# 1. The positive control.
# --------------------------------------------------------------------------- #
def test_a_claimed_write_is_delivered_and_reported(server, tmux_stub, tmp_path):
    """THE POSITIVE CONTROL for this whole file.

    Every "no send-keys happened" and "the text was not in X" assertion below is
    a claim about an instrument that must first be shown to observe SOMETHING.
    """
    server.claim_batches = [[write()]]
    rc, out = run_agent(server, tmux_stub, tmp_path)

    sends = tmux_stub.send_keys_calls()
    assert len(sends) == 2, f"expected the text send and the Enter send, got {sends}\n{out}"
    assert sends[0] == ["send-keys", "-t", "%12", "-l", "--", "yes, go ahead"], sends[0]
    assert sends[1] == ["send-keys", "-t", "%12", "Enter"], sends[1]

    results = [r for r in server.requests if r["path"].endswith("/result")]
    assert len(results) == 1, f"the agent did not report an outcome: {server.requests}"
    body = json.loads(results[0]["body"])
    assert body["state"] == "delivered", body
    assert body["agent"].startswith("workbench:"), body

    claims = [r for r in server.requests if r["path"].endswith("/claim")]
    assert claims, "the agent never claimed"
    assert json.loads(claims[0]["body"])["host"] == "workbench"
    assert claims[0]["auth"] == "Bearer not-a-real-terminal-token-not-a-real-token"


# --------------------------------------------------------------------------- #
# 2. The text never reaches a shell, and the flags are part of the command.
# --------------------------------------------------------------------------- #
def test_the_text_is_delivered_as_one_argv_element_with_no_shell(server, tmux_stub, tmp_path):
    """🔴 THE SINGLE MOST IMPORTANT TEST IN THIS FILE.

    The text is arbitrary operator input that arrived over the network. If
    anything between this process and execve interpolates it into a command
    string, `$(…)` and backticks are code that runs on THIS host with the
    operator's privileges, before tmux ever sees them.

    The fixture is deliberately hostile AND pairwise distinct in its parts, so a
    partial mangle (quotes eaten, `;` split, `$(` expanded) is visible as a
    difference rather than absorbed.
    """
    hostile = """-n $(id) `id` "q" 'p' ;echo x |tee /dev/null && true \\n $HOME"""
    server.claim_batches = [[write(text=hostile)]]
    run_agent(server, tmux_stub, tmp_path)

    sends = tmux_stub.send_keys_calls()
    assert sends, "nothing was sent; this test would prove nothing"
    assert sends[0][-1] == hostile, (
        "the text did not arrive byte-identical as ONE argument.\n"
        f"sent:     {sends[0][-1]!r}\nexpected: {hostile!r}\n"
        "Anything that changes here means a shell, a quoting layer or a split is "
        "standing between this agent and execve.")
    # And it really is one element, not several.
    assert len(sends[0]) == 6, f"the text was split across arguments: {sends[0]}"


def test_send_keys_is_literal_and_ends_option_parsing(server, tmux_stub, tmp_path):
    """🔴 `-l` AND `--` ARE THE COMMAND, NOT STYLE.

    Without `-l`, `send-keys` reads its argument as KEY NAMES: a reply containing
    "C-c" would interrupt whatever is running in the pane instead of typing three
    characters, and "Enter" would submit in the middle of a sentence. Without
    `--`, a reply beginning with `-` is parsed as a flag and either errors or
    changes what the command does.

    Asserting the flags' PRESENCE is not enough — their ORDER relative to the
    text is what makes them apply — so this pins the exact argv.
    """
    server.claim_batches = [[write(text="-l C-c Enter q")]]
    run_agent(server, tmux_stub, tmp_path)
    sends = tmux_stub.send_keys_calls()
    assert sends, "nothing was sent"
    assert sends[0] == ["send-keys", "-t", "%12", "-l", "--", "-l C-c Enter q"], sends[0]


def test_type_only_sends_no_enter(server, tmux_stub, tmp_path):
    """`submit: false` must type and stop.

    The operator chose that clawgate PRESSES ENTER by default, and the server
    defaults the field accordingly — but the field exists, and honouring it is
    the difference between a reply that sits visibly in a pane and one that runs.
    Without this test a mutant that ignores `submit` entirely (always Enter)
    survives every other case in this file, because they all set it true.
    """
    server.claim_batches = [[write(submit=False)]]
    run_agent(server, tmux_stub, tmp_path)
    sends = tmux_stub.send_keys_calls()
    assert len(sends) == 1, f"expected the text send alone, got {sends}"
    assert "Enter" not in sends[0]


# --------------------------------------------------------------------------- #
# 3. The write-time guard.
# --------------------------------------------------------------------------- #
def test_a_moved_tmux_server_is_refused_with_no_send_at_all(server, tmux_stub, tmp_path):
    """🔴 THE GUARD MUST RUN BEFORE THE WRITE, NOT AROUND IT.

    Within one tmux server a pane id `%N` is never reused, so a stale one simply
    fails to resolve. The dangerous case is a tmux server RESTART: ids are minted
    again from %0, so a `%12` the enqueuer saw an hour ago now names a completely
    different pane — and a submitted reply would execute there.

    The assertion is that NO send-keys happened, not merely that the outcome says
    `refused`: a guard that reports a refusal after the keys have gone is not a
    guard, and a status-only assertion cannot tell the two apart.
    """
    tmux_stub.set_server_id("9999:1111")
    server.claim_batches = [[write(expectTmuxServerId="1234:5678")]]
    run_agent(server, tmux_stub, tmp_path)

    assert tmux_stub.send_keys_calls() == [], (
        "the agent sent keys into a pane on a tmux server that is not the one the write "
        "was addressed to")
    results = [r for r in server.requests if r["path"].endswith("/result")]
    assert results, "the refusal was not reported"
    body = json.loads(results[0]["body"])
    assert body["state"] == "refused", body
    assert "identity moved" in body["detail"], body


def test_a_matching_tmux_server_is_delivered(server, tmux_stub, tmp_path):
    """The control for the refusal above: with the expectation SATISFIED the
    write goes through, so the refusal is the guard firing and not the agent
    declining everything that carries an expectation."""
    tmux_stub.set_server_id("1234:5678")
    server.claim_batches = [[write(expectTmuxServerId="1234:5678")]]
    run_agent(server, tmux_stub, tmp_path)
    assert len(tmux_stub.send_keys_calls()) == 2
    body = json.loads([r for r in server.requests if r["path"].endswith("/result")][0]["body"])
    assert body["state"] == "delivered", body


def test_an_absent_expectation_is_not_checked(server, tmux_stub, tmp_path):
    """An empty expectation means NOT CHECKED, not "matches".

    The collector reports its `tmux_server_id` as UNMEASURED in real conditions,
    so refusing every write that carries no expectation would make the feature
    fail exactly when the read model is degraded — while protecting against
    nothing, since a caller that cannot measure the id cannot supply a right one.
    """
    tmux_stub.set_server_id("9999:1111")   # different from anything, and irrelevant
    server.claim_batches = [[write(expectTmuxServerId="")]]
    run_agent(server, tmux_stub, tmp_path)
    assert len(tmux_stub.send_keys_calls()) == 2


def test_the_guard_refuses_when_the_identity_cannot_be_measured():
    """An unmeasurable live identity is a REFUSAL, not a pass.

    Driven through the predicate directly so every branch is covered, including
    the pair the behavioural tests above cannot both reach in one run.
    """
    assert AGENT.should_refuse("", "1234:5678") == (False, "")
    assert AGENT.should_refuse("", "") == (False, "")
    refuse, why = AGENT.should_refuse("1234:5678", "")
    assert refuse and "could not be measured" in why
    refuse, why = AGENT.should_refuse("1234:5678", "9999:1")
    assert refuse and "identity moved" in why
    assert AGENT.should_refuse("1234:5678", "1234:5678") == (False, "")


# --------------------------------------------------------------------------- #
# 4. The text is never logged, and never leaves in a diagnostic.
# --------------------------------------------------------------------------- #
def test_the_reply_text_is_never_written_to_the_journal(server, tmux_stub, tmp_path):
    """🔴 A CREDENTIAL-HANDLING GUARD, PINNING A RELATIONSHIP.

    The operator chose a full audit log INCLUDING the reply text over a
    metadata-only one, and was told the cost: a reply may contain a secret. That
    log lives in the server's table behind the terminal token. This unit's output
    goes to the journal, which is a different and much wider readership.

    The failing case is not hypothetical — the equivalent guard on the SERVER
    side caught a real leak in its first draft, where an agent-supplied `detail`
    string was logged and tmux quotes its arguments in its own errors. So this
    drives a delivery that FAILS, with tmux's stderr echoing the text back, and
    asserts the marker appears nowhere in the agent's output.
    """
    tmux_stub.fail_send_keys(rc=1, stderr=f"can't find pane: sending {MARKER} failed")
    server.claim_batches = [[write(text=MARKER)]]
    rc, out = run_agent(server, tmux_stub, tmp_path)

    # The positive control: the agent must have LOGGED SOMETHING about this write,
    # or "the marker is absent" is a claim about an empty buffer.
    assert "tmux-reply-agent:" in out, f"the agent logged nothing at all:\n{out}"
    assert "w-abc123" in out, f"the agent's log does not name the write:\n{out}"
    assert "failed" in out, f"the agent did not log the outcome:\n{out}"
    # …and the control that the marker was really in play.
    assert tmux_stub.send_keys_calls(), "no send was attempted; the fixture is inert"
    assert tmux_stub.send_keys_calls()[0][-1] == MARKER

    assert MARKER not in out, (
        "the operator's literal reply text reached this unit's journal. It is "
        "credential-grade and the journal is not the audit log.\n" + out)


def test_the_reported_detail_is_redacted_of_the_text(server, tmux_stub, tmp_path):
    """The same hazard, one hop further out: `detail` is stored in the audit log
    AND logged, and tmux quotes its arguments in its own errors.

    The redaction is an EXACT substitution — the agent knows the precise string —
    rather than a secret-shaped pattern, so it cannot miss the way a heuristic
    misses and cannot claim to have scrubbed something it did not.
    """
    tmux_stub.fail_send_keys(rc=1, stderr=f"can't find pane: {MARKER}")
    server.claim_batches = [[write(text=MARKER)]]
    run_agent(server, tmux_stub, tmp_path)
    results = [r for r in server.requests if r["path"].endswith("/result")]
    assert results, "no outcome was reported"
    body = json.loads(results[0]["body"])
    assert body["state"] == "failed", body
    assert MARKER not in body["detail"], body
    assert "<text>" in body["detail"], body
    # The control: the rest of the diagnostic survives, so redaction is not just
    # "throw the detail away".
    assert "can't find pane" in body["detail"], body


def test_redact_is_exact_and_bounded():
    """The redaction helper, directly. A bounded detail matters because it lands
    in a retained column and in a log line."""
    assert AGENT.redact("before secret after", "secret") == "before <text> after"
    assert AGENT.redact("a\nb\tc", "") == "a b c"
    assert len(AGENT.redact("x" * 2000, "")) == 400
    # An empty text must not redact everything.
    assert AGENT.redact("nothing to hide", "") == "nothing to hide"


# --------------------------------------------------------------------------- #
# 5. At-most-once, and the states that are NOT errors.
# --------------------------------------------------------------------------- #
def test_a_failed_result_post_does_NOT_re_execute_the_write(server, tmux_stub, tmp_path):
    """🔴 AT MOST ONCE. A retried `send-keys` runs a command twice.

    The server's claim moved this row out of `pending` permanently, so if the
    outcome report fails the correct behaviour is to LOSE the record of it — the
    row is relabelled `abandoned`, whose meaning is "delivery UNKNOWN" — and
    never to re-run the send. This is the one place in the whole path where a
    duplicate execution could be introduced.
    """
    server.result_status = 500
    server.claim_batches = [[write()]]
    _rc, out = run_agent(server, tmux_stub, tmp_path)

    text_sends = [s for s in tmux_stub.send_keys_calls() if "-l" in s]
    assert len(text_sends) == 1, (
        f"the agent executed the text {len(text_sends)} times for ONE claimed write; it is "
        f"retrying a send after a failed report: {tmux_stub.send_keys_calls()}")
    results = [r for r in server.requests if r["path"].endswith("/result")]
    assert len(results) == 1, (
        f"the agent re-POSTed the outcome {len(results)} times. A retry of the REPORT is "
        "harmless in itself, but it is one line away from a retry of the SEND, and the "
        "server refuses a replayed completion anyway.")
    # And what it says about the loss is the truthful thing, not "did not run".
    assert "UNKNOWN" in out, out


def test_an_unarmed_server_is_backed_off_from_not_crashed_on(server, tmux_stub, tmp_path):
    """🔴 503 IS THE SHIPPED STATE, NOT A FAULT.

    The server's write routes are behind a fail-closed wrapper that answers 503
    until CLAWGATE_TERMINAL_TOKEN is provisioned on the pod. That is how this
    change is deployed, so the agent must treat it as a quiet, logged-once
    condition — never as an error that fails the unit or spams the journal.
    """
    server.claim_status = 503
    rc, out = run_agent(server, tmux_stub, tmp_path)
    assert tmux_stub.send_keys_calls() == [], "the agent sent keys off a 503"
    assert "NOT ARMED" in out, out
    # Logged ONCE per condition, not per tick: the poll here is 50ms, so a
    # per-tick line would appear many times over the run.
    assert out.count("NOT ARMED") == 1, (
        "a permanent condition was logged on every tick; at the real cadence that is "
        f"17,280 lines a day and buries every real event:\n{out}")


def test_no_credentials_exits_two_and_makes_no_request(server, tmux_stub, tmp_path):
    """A configuration error that cannot self-heal, so this one EXITS.

    It is also the state the change ships in on the host side, which is why the
    message says so rather than just "missing token".
    """
    rc, out = run_agent(server, tmux_stub, tmp_path, token=None)
    assert rc == 2, f"rc={rc}\n{out}"
    assert server.requests == [], f"the agent contacted the server with no token: {server.requests}"
    assert "not armed" in out


def test_the_environment_beats_the_credential_file(tmp_path):
    """🔴 A REGRESSION GUARD FOR A MEASURED INCIDENT, NOT A PREFERENCE.

    `clawgate-stop-hook.sh` sources the same credential file with `set -a`, which
    makes the FILE beat the environment. The measured consequence over there was
    a probe aimed at a harmless address silently POSTing to PRODUCTION. On THIS
    surface the equivalent mistake sends a reply into a live pane.
    """
    conf = tmp_path / "clawgate.env"
    conf.write_text("CLAWGATE_API_URL=http://from-the-file:1\n"
                    "export CLAWGATE_TERMINAL_TOKEN='from-the-file'\n")
    env = {"CLAWGATE_API_URL": "http://from-the-env:2"}
    assert AGENT.resolve("CLAWGATE_API_URL", str(conf), env=env) == "http://from-the-env:2"
    # …and the file is still read for keys the environment does not set, in both
    # spellings a sourceable file actually carries.
    assert AGENT.resolve("CLAWGATE_TERMINAL_TOKEN", str(conf), env=env) == "from-the-file"


def test_the_credential_reader_takes_the_last_assignment(tmp_path):
    """As sourcing would. A file that sets a key twice is ordinary."""
    conf = tmp_path / "clawgate.env"
    conf.write_text("CLAWGATE_TERMINAL_TOKEN=first\n  export CLAWGATE_TERMINAL_TOKEN=\"second\"\n")
    assert AGENT.read_conf_key("CLAWGATE_TERMINAL_TOKEN", str(conf)) == "second"


def test_the_token_is_never_in_argv(server, tmux_stub, tmp_path):
    """Everything on this box can read /proc/<pid>/cmdline, and a URL lands in
    access logs — so the credential travels in a header.

    Asserted through the stub tmux's recorded argv and through the request the
    stub server received, not by reading the source: a structural check would
    type-check past a token added to a URL somewhere else.
    """
    server.claim_batches = [[write()]]
    run_agent(server, tmux_stub, tmp_path, token=MARKER + "-token-longer-than-32-chars")
    for call in tmux_stub.calls():
        assert not any(MARKER in a for a in call), f"the token reached tmux's argv: {call}"
    for req in server.requests:
        assert MARKER not in req["path"], f"the token reached the URL: {req['path']}"
        assert req["auth"] and MARKER in req["auth"], "the token was not sent as a header"


# --------------------------------------------------------------------------- #
# 6. The seams: the host label and the tmux server-id spelling.
# --------------------------------------------------------------------------- #
def test_the_host_label_follows_the_same_rule_as_the_collector(tmp_path):
    """🔴 ONE RULE, AND A DISAGREEMENT HERE IS SILENT.

    The server's queue is keyed on the host label the READ MODEL stores, which
    the collector computes from ACTIVITY_HOST (`hostname` is "nixos" on BOTH
    machines, so it cannot be the source of truth). An agent that computed a
    different label would poll for a host nobody enqueues to and deliver nothing
    at all, with no error anywhere.
    """
    env_file = tmp_path / "collector-env"
    env_file.write_text('ACTIVITY_HOST="laptop"\n')
    assert AGENT.local_host_label(env={}, env_file=str(env_file)) == "laptop"
    assert AGENT.local_host_label(env={"ACTIVITY_HOST": "WORKBENCH"}, env_file=str(env_file)) == "workbench"
    # An unrecognised label falls back rather than inventing a host.
    assert AGENT.local_host_label(env={"ACTIVITY_HOST": "nixos"}, env_file=str(tmp_path / "absent")) == "workbench"
    # And the vocabulary is the collector's own, read from its source rather than
    # restated here.
    collector_src = COLLECTOR.read_text()
    assert 'HOST_NAMES = ("workbench", "laptop")' in collector_src, (
        "the collector's host vocabulary changed; this agent's HOST_NAMES must follow it")
    assert AGENT.HOST_NAMES == ("workbench", "laptop")
    assert 'DEFAULT_LOCAL_HOST = "workbench"' in collector_src
    assert AGENT.DEFAULT_LOCAL_HOST == "workbench"


def test_the_server_id_format_matches_the_collectors():
    """🔴 A SEAM GUARD: two spellings of one token would refuse EVERY write.

    The enqueuer takes its expectation from the read model, whose
    `tmux_server_id` the collector builds as `<pid>:<start_time>` from the
    server-level tmux formats `#{pid}` and `#{start_time}`. If this agent asked
    tmux for a differently-shaped value it would compare apples to oranges and
    refuse everything — which reads exactly like "the feature does not work",
    with no error naming the cause.

    The check reads the COLLECTOR'S OWN SOURCE rather than restating the format,
    so a change there fails here instead of drifting silently.
    """
    src = COLLECTOR.read_text()
    assert 'WINDOW_FORMAT = "#{window_id}|#{window_index}|#{pid}|#{start_time}|#{session_name}"' in src, (
        "the collector's window format changed; re-derive TMUX_SERVER_ID_FORMAT from it")
    assert 'return f"{pid}:{started}", None' in src, (
        "the collector no longer joins the server id as `<pid>:<start_time>`")
    assert AGENT.TMUX_SERVER_ID_FORMAT == "#{pid}:#{start_time}", (
        f"the agent asks tmux for {AGENT.TMUX_SERVER_ID_FORMAT!r}, which is not the shape the "
        "collector stores; every write carrying an expectation would be refused")


def test_an_unmeasurable_server_id_reads_as_empty(tmp_path, monkeypatch):
    """tmux renders an unknown format verbatim, so an old tmux that does not know
    `#{start_time}` yields a bare ':'. That must read as "cannot measure" — which
    the guard turns into a REFUSAL — and never as the literal identity ':'."""
    calls = []

    def fake_run(args):
        calls.append(args)
        return 0, ":\n", ""

    monkeypatch.setattr(AGENT, "run_tmux", fake_run)
    assert AGENT.tmux_server_id() == ""
    monkeypatch.setattr(AGENT, "run_tmux", lambda a: (0, "1234:5678\n", ""))
    assert AGENT.tmux_server_id() == "1234:5678"
    monkeypatch.setattr(AGENT, "run_tmux", lambda a: (1, "", "no server running"))
    assert AGENT.tmux_server_id() == ""


# --------------------------------------------------------------------------- #
# 7. The unit ships DISABLED, and carries what the child needs.
# --------------------------------------------------------------------------- #
def home_nix() -> str:
    return HOME_NIX.read_text()


def test_the_agent_unit_SHIPS_DISABLED():
    """🔴 THE CENTRAL CLAIM OF THIS CHANGE, ASSERTED AGAINST THE CONFIGURATION.

    What this agent delivers is arbitrary command execution as the operator on
    this host. Building it and ARMING it were deliberately separated so the write
    path could be merged, deployed and audited before it could execute anything.

    The flag is read through the comment-stripping reader, not a substring
    search: this file's blocks quote directives verbatim in prose, so a raw
    `in src` check cannot tell a declaration from a comment describing one — and
    that exact failure once left a guard green over a deleted unit.
    """
    src = nix_units.strip_nix_comments(home_nix())
    assert "enableTmuxReplyAgent = false;" in src, (
        "the terminal-write agent's master switch is not false. Arming this is an "
        "operator act, deliberately separate from shipping it: it needs "
        "CLAWGATE_TERMINAL_TOKEN provisioned on the POD *and* this flag flipped, and "
        "neither implies the other.")
    assert "enableTmuxReplyAgent = true;" not in src


def test_the_agent_unit_is_declared_even_though_it_is_disabled():
    """The SERVICE must exist on both hosts even while nothing wants it, so an
    operator arming the surface can start it by hand and watch it before wiring
    it into the target. The `Install.WantedBy` is what the flag gates."""
    src = home_nix()
    assert nix_units.declares("systemd.user.services.tmux-reply-agent", src)
    unit = nix_units.strip_nix_comments(
        nix_units.unit_source("systemd.user.services.tmux-reply-agent", src))
    assert "enableTmuxReplyAgent" in unit and "default.target" in unit, unit
    # 🔴 A RESIDENT SERVICE, NOT A TIMER. A ~5s poll driven by a timer would fork
    # a process, an interpreter and a connection 17,280 times a day.
    assert nix_units.directive("Type", unit) == '"simple"', unit
    assert not nix_units.declares("systemd.user.timers.tmux-reply-agent", src)


def test_the_unit_PATH_carries_tmux_and_python():
    """🔴 EVERY BINARY THE CHILD NEEDS, because under the user manager there is no
    login-shell PATH to fall back on — a lesson drift-check.service already paid
    for, reporting COULD NOT MEASURE forever from a unit that looked correct.

    The agent is stdlib Python plus tmux. It deliberately does NOT carry curl,
    openssh or gawk: it uses urllib and never leaves this host. That is the
    opposite trim from tmux-snapshot-push's list, and copying that list wholesale
    would be carrying binaries to justify later.
    """
    unit = nix_units.strip_nix_comments(
        nix_units.unit_source("systemd.user.services.tmux-reply-agent", home_nix()))
    assert "pkgs.tmux" in unit, "without tmux every delivery fails"
    assert "pkgs.python3" in unit, "the agent is a Python program"
    assert "pkgs.openssh" not in unit and "pkgs.gawk" not in unit, (
        "the agent never leaves this host; these are copied, not needed")


def test_the_unit_gives_tmux_its_SOCKET_directory():
    """This host's tmux socket is at $XDG_RUNTIME_DIR/tmux-UID/, not tmux's
    compiled-in /tmp/tmux-UID/. Without TMUX_TMPDIR the agent cannot connect and
    every delivery fails."""
    unit = nix_units.strip_nix_comments(
        nix_units.unit_source("systemd.user.services.tmux-reply-agent", home_nix()))
    assert nix_units.directive("TMUX_TMPDIR", unit) or "TMUX_TMPDIR=%t" in unit, unit


def test_the_unit_does_not_wire_the_DND_defeating_failure_toast():
    """🔴 notify-failure@ toasts are wired to DEFEAT do-not-disturb, and that
    bypass is justified by a MEASURED rate of about one firing in nine days.

    This unit restarts on failure and polls every few seconds, so any sustained
    condition — the surface not armed, which is the state it ships in — would
    fire a DND-bypassing toast on every restart and burn down the one alert
    channel that has to keep its meaning.
    """
    unit = nix_units.strip_nix_comments(
        nix_units.unit_source("systemd.user.services.tmux-reply-agent", home_nix()))
    assert "OnFailure" not in unit, unit
    assert "notify-failure" not in unit, unit


def test_the_unit_runs_the_script_this_suite_tests():
    """A unit pointing at a different path would make every test above a claim
    about a file nothing runs."""
    unit = nix_units.strip_nix_comments(
        nix_units.unit_source("systemd.user.services.tmux-reply-agent", home_nix()))
    assert "scripts/tmux-reply-agent" in unit, unit
    assert SCRIPT.exists() and os.access(SCRIPT, os.X_OK)


# --------------------------------------------------------------------------- #
# 8. The second kind: new-session.
# --------------------------------------------------------------------------- #
def test_a_new_session_opens_a_window_at_the_cwd_and_types_into_THAT_pane(server, tmux_stub, tmp_path):
    """🔴 THE NEW PANE IS ADDRESSED BY ID, NEVER "the active pane".

    `new-window` is followed by a `send-keys`, and between the two the operator
    may have switched windows. A relative target would then type the caller's
    command into whatever they moved to — the wrong-pane failure this whole
    surface is careful about, arriving through the one path that creates its own
    target. So the agent asks tmux to PRINT the new pane's id (`-P -F
    '#{pane_id}'`) and sends to that id.
    """
    server.claim_batches = [[write(kind="new-session", pane="", cwd="/tmp/some/dir",
                                   text="claude 'do the thing'")]]
    run_agent(server, tmux_stub, tmp_path)

    calls = tmux_stub.calls()
    new_windows = [c for c in calls if c and c[0] == "new-window"]
    assert len(new_windows) == 1, f"expected one new-window, got {calls}"
    assert new_windows[0] == ["new-window", "-P", "-F", "#{pane_id}", "-c", "/tmp/some/dir"], new_windows[0]

    sends = tmux_stub.send_keys_calls()
    assert len(sends) == 2, f"expected the text send and the Enter send, got {sends}"
    # The stub's `new-window` prints nothing, so the agent must have failed
    # rather than guessed a pane — see the companion test below. Here the stub is
    # configured to print one.
    assert sends[0][:4] == ["send-keys", "-t", tmux_stub.new_pane_id(), "-l"], sends[0]
    assert sends[0][-1] == "claude 'do the thing'", sends[0]
    assert sends[1] == ["send-keys", "-t", tmux_stub.new_pane_id(), "Enter"], sends[1]

    body = json.loads([r for r in server.requests if r["path"].endswith("/result")][0]["body"])
    assert body["state"] == "delivered", body


def test_a_new_session_that_cannot_report_a_pane_FAILS_rather_than_guessing(server, tmux_stub, tmp_path):
    """If tmux does not hand back a pane id there is no safe target.

    Guessing — "the active pane", "the last window" — is exactly the class of
    fuzzy target the pane-id rule exists to forbid, and here it would run a
    command in a pane the operator is looking at. Failing is the correct outcome
    and it must produce NO send-keys at all.
    """
    tmux_stub.set_new_window_output("")
    server.claim_batches = [[write(kind="new-session", pane="", cwd="/tmp/some/dir", text="echo hi")]]
    run_agent(server, tmux_stub, tmp_path)
    assert tmux_stub.send_keys_calls() == [], (
        "the agent sent keys without a pane id from tmux; that lands in whatever pane happens "
        "to be active")
    body = json.loads([r for r in server.requests if r["path"].endswith("/result")][0]["body"])
    assert body["state"] == "failed", body


def test_a_new_session_with_no_text_just_opens_the_window(server, tmux_stub, tmp_path):
    """A bare window is a complete delivery — the caller asked for one, and
    typing nothing into it is the right amount of typing."""
    server.claim_batches = [[write(kind="new-session", pane="", cwd="/tmp/some/dir", text="")]]
    run_agent(server, tmux_stub, tmp_path)
    assert [c for c in tmux_stub.calls() if c and c[0] == "new-window"]
    assert tmux_stub.send_keys_calls() == []
    body = json.loads([r for r in server.requests if r["path"].endswith("/result")][0]["body"])
    assert body["state"] == "delivered", body


def test_an_unknown_kind_is_refused_with_no_tmux_call_at_all(server, tmux_stub, tmp_path):
    """🔴 REFUSE, DO NOT GUESS.

    A kind this agent does not recognise means the server is asking for something
    a newer build knows how to do. Falling back to the default kind would run a
    command nobody on this host reviewed; refusing is visible in the audit log and
    costs one write.
    """
    server.claim_batches = [[write(kind="reboot-the-host")]]
    run_agent(server, tmux_stub, tmp_path)
    assert [c for c in tmux_stub.calls() if c and c[0] in ("send-keys", "new-window")] == []
    body = json.loads([r for r in server.requests if r["path"].endswith("/result")][0]["body"])
    assert body["state"] == "refused", body
    assert "unknown write kind" in body["detail"], body


def test_an_absent_kind_still_means_send_keys(server, tmux_stub, tmp_path):
    """Backwards compatibility is the default, not a migration: a write with no
    `kind` at all is what every caller predating the field sent, and it means
    send-keys."""
    w = write()
    w.pop("kind", None)
    server.claim_batches = [[w]]
    run_agent(server, tmux_stub, tmp_path)
    assert len(tmux_stub.send_keys_calls()) == 2
