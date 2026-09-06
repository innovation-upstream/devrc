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

⚠ THE AGENT SHIPPED DISABLED AND IS NOW ARMED — `enableTmuxReplyAgent` is true —
but that changes NOTHING about how these tests are built. They still drive the
script directly against stubs rather than asserting anything about a running
system: a test that reached the live agent would be sending keystrokes into the
operator's real panes. The two switches remain independent — the server's routes
answer 503 until CLAWGATE_TERMINAL_TOKEN is provisioned on the POD, and the unit
is wired into `default.target` only while `enableTmuxReplyAgent` is true.
"""
from __future__ import annotations

import ast
import importlib.machinery
import importlib.util
import json
import os
import re
import pathlib
import shutil
import subprocess
import tempfile
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
    # 🔴 A STUB THAT CAN LIE ABOUT WHERE THE WINDOW LANDED. The agent reads back
    # the session and path tmux actually chose; without a stub that can report
    # something OTHER than the request, that read-back is unpinned — measured, as
    # two surviving mutants, because `=` and `isdir` each fire first in the
    # ordinary case. These files override the derived values when non-empty.
    landed_session = tmp_path / "tmux-landed-session"
    landed_session.write_text("")
    landed_path = tmp_path / "tmux-landed-path"
    landed_path.write_text("")

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
        # 🔴 THE STUB NOW ECHOES WHAT THE AGENT READS BACK. The agent verifies the
        # session and working directory tmux ACTUALLY chose, so a stub printing a
        # bare pane id would fail every new-session test for the wrong reason.
        # It derives both from its OWN argv, which is also what makes a
        # deliberately mismatching stub expressible.
        f'''if [ "$1" = "new-window" ]; then\n'''
        f'''  python3 -c 'import sys;a=sys.argv[1:];\n'''
        f'''c=a[a.index("-c")+1] if "-c" in a else "";\n'''
        f'''c=open("{landed_path}").read().strip() or c;\n'''
        f'''t=a[a.index("-t")+1].strip("=:") if "-t" in a else "keep";\n'''
        f'''t=open("{landed_session}").read().strip() or t;\n'''
        f'''p=open("{new_window_out}").read().strip();\n'''
        f'''sys.stdout.write("" if p=="" else p+"\\t"+t+"\\t"+c+"\\n")' "$@"\n'''
        f'''  exit 0\n'''
        f'''fi\n'''
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
        def lie_about_landing(session=None, path=None):
            """Make the stub report a landing that differs from the request."""
            if session is not None:
                landed_session.write_text(session)
            if path is not None:
                landed_path.write_text(path)

        @staticmethod
        def new_pane_id():
            return new_window_out.read_text().strip()

        @staticmethod
        def reset():
            log.write_text("")

    return Stub


def write(**kw):
    """One claim-batch entry, with defaults that are pairwise distinct."""
    out = {
        "id": "w-abc123",
        "kind": "send-keys",
        "cwd": "",
        "tmuxSessionName": "",
        "host": "workbench",
        "pane": "%12",
        "text": "yes, go ahead",
        "submit": True,
        "expectTmuxServerId": "",
    }
    out.update(kw)
    return out


def real_dir(tmp_path, name="work"):
    """A directory that EXISTS, for a new-session fixture.

    🔴 `/tmp/some/dir` USED TO DO, AND THAT WAS THE HOLE. `new-window -c <missing
    path>` returns rc 0 and opens the window in the HOME DIRECTORY — measured on
    real tmux — so every stubbed fixture here was describing a delivery that, in
    production, would have run the command somewhere else entirely. The agent
    refuses a non-existent cwd now, so a fixture has to name a real one.
    """
    d = tmp_path / name
    d.mkdir(exist_ok=True)
    return str(d)


def run_agent(server, tmux_stub, tmp_path, *, env_extra=None, conf_text=None,
              token="not-a-real-terminal-token-not-a-real-token", timeout=30,
              expect_requests=2):
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
        if len(server.requests) >= expect_requests:
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
# 7. The unit is ARMED (it shipped disabled), and carries what the child needs.
# --------------------------------------------------------------------------- #
def home_nix() -> str:
    return HOME_NIX.read_text()


def test_the_agent_unit_IS_ARMED():
    """🔴 THE CENTRAL CLAIM OF THE ARMING CHANGE, ASSERTED AGAINST THE CONFIGURATION.

    This guard used to pin the flag `false` and is now pinned `true`, and the
    SYMMETRY is the point rather than a weakening: what it has always enforced is
    that the armed state is a deliberate, reviewed edit in BOTH directions. While
    it read `false`, arming meant editing this test; now that it reads `true`,
    DISARMING means editing this test. An accidental revert to `false` would stop
    every queued reply from ever executing while the server kept accepting them
    and the UI kept looking healthy — a silent reopening of the loop rank 32
    closed, which is exactly the shape a config guard exists to catch.

    🔴 BUT THE FLAG IS NOT A LIVE KILL SWITCH, AND THIS GUARD MUST NOT BE READ AS
    ONE. Flipping it `false` and shipping does NOT stop a RUNNING agent: the unit
    definition is emitted unconditionally, so the flag only removes `[Install]`,
    and `sd-switch` reads that as a CHANGED unit and plans Stop/Start. What this
    pins is the DECLARED state at next login. Stopping it now is
    `systemctl --user stop tmux-reply-agent`, on both hosts.

    What this agent delivers is arbitrary command execution as the operator on
    this host. Building it and ARMING it were deliberately separated so the write
    path could be merged, deployed and audited before it could execute anything;
    that separation did its job across seven audit rounds and is now history.

    The flag is read through the comment-stripping reader, not a substring
    search: this file's blocks quote directives verbatim in prose, so a raw
    `in src` check cannot tell a declaration from a comment describing one — and
    that exact failure once left a guard green over a deleted unit.
    """
    src = nix_units.strip_nix_comments(home_nix())
    assert "enableTmuxReplyAgent = true;" in src, (
        "the terminal-write agent's master switch is not true. Disarming is an operator "
        "act and a reviewed edit, not a drive-by revert: with this false the unit is "
        "wanted by nothing, every reply typed in the web UI queues for ever, and nothing "
        "in the server or the UI says so.")
    assert "enableTmuxReplyAgent = false;" not in src


def test_the_agent_unit_is_declared_independently_of_the_flag():
    """The SERVICE definition is emitted unconditionally; only `Install.WantedBy`
    is gated. That is what let an operator start it by hand and watch it before
    it was wired into the target, and it is what makes disarming a matter of the
    unit no longer being WANTED rather than no longer existing."""
    src = home_nix()
    assert nix_units.declares("systemd.user.services.tmux-reply-agent", src)
    unit = nix_units.strip_nix_comments(
        nix_units.unit_source("systemd.user.services.tmux-reply-agent", src))
    # 🔴 A RESIDENT SERVICE, NOT A TIMER. A ~5s poll driven by a timer would fork
    # a process, an interpreter and a connection 17,280 times a day.
    assert nix_units.directive("Type", unit) == '"simple"', unit
    assert not nix_units.declares("systemd.user.timers.tmux-reply-agent", src)


def _wanted_by_expr(nix_src: str) -> tuple:
    """-> (flag_value, condition_source, target_list_source) lifted FROM home.nix.

    🔴 THE PREVIOUS GUARD PINNED WORDS AND THE ONE MUTATION THAT MATTERS WALKED
    THROUGH IT. It asserted that the unit's text CONTAINS "enableTmuxReplyAgent"
    and "default.target"; the mutant

        WantedBy = lib.optionals (!enableTmuxReplyAgent) [ "default.target" ];

    keeps both substrings and SURVIVES — an inversion that would start the agent
    on both hosts on the next switch, i.e. the single thing this whole change
    claims cannot happen. `claude/RULES.md`: "a guard can be SPELLED rather than
    STRUCTURAL — ask whether it can pass while the hazard exists in a different
    shape."

    So the CONDITION is extracted and pinned as a whole normalised string rather
    than searched for. `!enableTmuxReplyAgent` is not `enableTmuxReplyAgent`, and
    neither is `(enableTmuxReplyAgent || true)`.
    """
    flag = re.search(r"^  enableTmuxReplyAgent = (true|false);", nix_src, re.M)
    assert flag, "the master switch declaration was not found in home.nix"
    unit = nix_units.strip_nix_comments(
        nix_units.unit_source("systemd.user.services.tmux-reply-agent", nix_src))
    m = re.search(r"WantedBy\s*=\s*lib\.optionals\s+(.+?)\s*(\[[^\]]*\])\s*;", unit, re.S)
    assert m, f"no `WantedBy = lib.optionals <cond> [ ... ];` in the unit:\n{unit}"
    cond = " ".join(m.group(1).split())
    targets = " ".join(m.group(2).split())
    return flag.group(1), cond, targets


def test_default_target_wants_the_agent_and_ONLY_via_the_bare_flag():
    """🔴 THE CENTRAL CLAIM, PINNED AS AN EXPRESSION RATHER THAN AS SUBSTRINGS.

    Two facts together determine that `default.target` wants this unit, and BOTH
    are asserted: the condition is the BARE flag (not a negation, not a wider
    expression), and the flag is `true`. A mutant that changes either is a
    different string here.

    🔴 THE CONDITION ASSERTION IS THE HALF THAT DID NOT CHANGE MEANING WHEN THE
    FLAG FLIPPED, AND IT IS THE MORE IMPORTANT HALF. Pinning the bare flag is
    what keeps the flag the ONLY thing deciding whether the agent runs: a
    disjunction like `(enableTmuxReplyAgent || isNixOS)` would want the unit on
    every host regardless of the switch, so setting the flag `false` would no
    longer disarm anything. That mutant mentions the flag's name and passes a
    word-pinning guard, which is why the whole condition is normalised and
    compared as one string.

    🔴 DETERMINISTIC, AND THERE IS DELIBERATELY NO `nix eval` BESIDE IT. A first
    draft added one as "confirmation". It PASSED on the dev host and turned the
    nix check sandbox RED — not by failing, but by SKIPPING: that sandbox has no
    recursive nix, and this repo's runner treats an unpinned skip as an error,
    because "a skip is a test that did not run". Pinning it would have bought a
    permanent EXPECTED_SKIPS entry for a test that can never run in the tier that
    gates, i.e. reading as coverage while providing none.

    It was removed rather than pinned because it confirmed nothing this test does
    not already determine: given `cond == "enableTmuxReplyAgent"` and the flag
    `false`, `lib.optionals` yields `[]` by definition. And a change to a
    DIFFERENT combinator (`lib.optional`, singular, with different semantics)
    fails the regex above rather than slipping past it.
    """
    flag, cond, targets = _wanted_by_expr(home_nix())
    assert cond == "enableTmuxReplyAgent", (
        f"the WantedBy condition is `{cond}`, not the bare flag. Anything else — a negation, a "
        "disjunction, a different flag — can want this unit while still mentioning the flag's "
        "name, which is exactly how the previous guard was walked past.")
    assert flag == "true", (
        f"enableTmuxReplyAgent is `{flag}`. The agent is ARMED: with this false the unit is "
        "wanted by nothing, and every reply typed in the web UI queues and is never executed "
        "— silently, because the server still accepts the write. Disarming is a deliberate "
        "operator act and edits this assertion with it.")
    assert targets == '[ "default.target" ]', targets


def test_the_wanted_by_guard_can_SEE_an_inverted_flag():
    """The positive control, and the whole reason the test above is shaped this
    way. The previous, word-pinning guard passed over both of these mutants."""
    src = home_nix()
    inverted = src.replace(
        'WantedBy = lib.optionals enableTmuxReplyAgent [ "default.target" ];',
        'WantedBy = lib.optionals (!enableTmuxReplyAgent) [ "default.target" ];')
    assert inverted != src, "the WantedBy line this control mutates was not found verbatim"
    _flag, cond, _t = _wanted_by_expr(inverted)
    assert cond != "enableTmuxReplyAgent", (
        "the inverted-flag mutant reads identically to the shipped condition; this guard cannot "
        "see the one mutation that would start the agent on both hosts")

    # 🔴 THE FLAG CONTROL MUTATES IN THE DIRECTION THAT IS NOW THE HAZARD.
    # It used to flip false -> true (arming by accident); with the agent armed
    # the accident to catch is the reverse — a revert to `false`, which disarms
    # the host half while the server keeps accepting writes.
    #
    # ⚠ TWICE-CORRECTED, and the SECOND correction is the one worth reading,
    # because a fix for the first one CREATED the hazard the first one denied.
    #
    # Round 1 of this change claimed the pre-flip direction would have gone
    # "green and blind". An audit refuted it: `assert flipped != src` already
    # existed, `enableTmuxReplyAgent = false` occurred ZERO times in the armed
    # file, so the stale direction failed loudly. True — at that commit.
    #
    # 🔴 THEN THE FIX FOR THAT CLAIM MADE IT FALSE. The same round wrote a
    # DISARM EXAMPLE into `nix/home.nix`'s comments:
    #
    #     #     enableTmuxReplyAgent = false;   # then merge + ship.sh, THEN:
    #
    # A comment — but `str.replace` cannot tell a declaration from a comment
    # describing one, and that line's `  #     ` prefix ENDS IN SPACES, so the
    # two-space-prefixed literal matches INSIDE it. MEASURED: reverting this
    # control to its pre-flip direction on that tree gives `1 passed` — the
    # replace hits the comment, `flipped != src` is satisfied, `_wanted_by_expr`
    # reads the untouched real declaration, and the control passes about a file
    # whose flag was never mutated. Exactly "green and blind", arrived at by
    # fixing the claim that it could not happen.
    #
    # 🔴 SO THE MUTATION IS ANCHORED, NOT SUBSTRING-MATCHED. `^  <flag> = …;$`
    # under re.M is the same shape `_wanted_by_expr` already uses, and it is why
    # THAT function was never fooled by the comment. `subn`'s count is asserted
    # to be exactly 1, so a SECOND DECLARATION fails loudly with `substituted 2`
    # rather than silently taking an extra substitution.
    #
    # ⚠ Precisely, because an earlier draft of this sentence overstated it: a
    # comment at COLUMN 0 (`#  enableTmuxReplyAgent = true;`) is INVISIBLE to the
    # anchor, not a failure — MEASURED `1 passed`. That is the wanted behaviour
    # (a comment must not be mutated), but it is not detection, and claiming
    # detection would be the same overclaim this whole control exists to record.
    #
    # The documentation is NOT the thing to delete here: the disarm example is
    # the only written rollback for a surface that executes commands. Fix the
    # test, not the sentence.
    flipped, n_flipped = re.subn(r"^  enableTmuxReplyAgent = true;$",
                                 "  enableTmuxReplyAgent = false;", src, flags=re.M)
    assert n_flipped == 1, (
        f"expected exactly ONE anchored flag declaration to mutate, substituted {n_flipped}. "
        "Zero means the declaration moved or changed spelling; more than one means there is a "
        "second declaration and this control no longer names which one it tested.")
    assert flipped != src
    assert _wanted_by_expr(flipped)[0] == "false"


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
    # 🔴 READ THE makeBinPath LIST, NOT THE WHOLE UNIT. The previous version
    # asserted `"pkgs.python3" in unit`, which is satisfied by the ExecStart line
    # (`${pkgs.python3}/bin/python3 …`) — so deleting python3 from the PATH list
    # left it green while the CHILD lost its interpreter directory. A substring
    # test over a block that mentions a package for another reason is not a test
    # of the list; the list has to be extracted first.
    m = re.search(r"PATH=\$\{lib\.makeBinPath \[([^\]]*)\]\}", unit)
    assert m, f"no PATH=...makeBinPath[...] in the unit:\n{unit}"
    entries = m.group(1).split()
    assert "pkgs.tmux" in entries, f"without tmux every delivery fails; PATH list = {entries}"
    assert "pkgs.python3" in entries, f"the agent is a Python program; PATH list = {entries}"
    assert "pkgs.coreutils" in entries, entries
    # Deliberately absent: the agent uses urllib and never leaves this host.
    for copied in ("pkgs.openssh", "pkgs.gawk", "pkgs.curl"):
        assert copied not in entries, (
            f"{copied} is in the PATH list; the agent never uses it — carried, not needed")


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
    condition — the surface not armed, which since arming means the pod's secret
    was REMOVED — would fire a DND-bypassing toast on every restart and burn down
    the one alert channel that has to keep its meaning.
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
    server.claim_batches = [[write(kind="new-session", pane="", cwd=real_dir(tmp_path),
                                   text="claude 'do the thing'")]]
    run_agent(server, tmux_stub, tmp_path)

    calls = tmux_stub.calls()
    new_windows = [c for c in calls if c and c[0] == "new-window"]
    assert len(new_windows) == 1, f"expected one new-window, got {calls}"
    assert new_windows[0] == ["new-window", "-P", "-F", AGENT.NEW_WINDOW_FORMAT,
                              "-c", real_dir(tmp_path)], new_windows[0]

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
    server.claim_batches = [[write(kind="new-session", pane="", cwd=real_dir(tmp_path), text="echo hi")]]
    run_agent(server, tmux_stub, tmp_path)
    assert tmux_stub.send_keys_calls() == [], (
        "the agent sent keys without a pane id from tmux; that lands in whatever pane happens "
        "to be active")
    body = json.loads([r for r in server.requests if r["path"].endswith("/result")][0]["body"])
    assert body["state"] == "failed", body


def test_a_new_session_with_no_text_just_opens_the_window(server, tmux_stub, tmp_path):
    """A bare window is a complete delivery — the caller asked for one, and
    typing nothing into it is the right amount of typing."""
    server.claim_batches = [[write(kind="new-session", pane="", cwd=real_dir(tmp_path), text="")]]
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


# --------------------------------------------------------------------------- #
# 9. The trust boundary: the host refuses what the server should never have sent.
# --------------------------------------------------------------------------- #
#
# 🔴 THIS IS NOT DUPLICATED VALIDATION, IT IS A BOUNDARY. "One rule, one place"
# governs two copies of a rule on the same side of a trust boundary. Here the
# server is a pod reachable from an unauthenticated LAN NodePort and THIS process
# is the thing that runs commands as the operator — the two sides fail
# independently, and the side that executes is the one that must refuse.
#
# Every case below is something the server also rejects. The point is that the
# host does not depend on that.

@pytest.mark.parametrize("mutation,expect_in", [
    # A name-shaped target does not FAIL in tmux — it resolves fuzzily and runs
    # the command in some other pane.
    ({"pane": "scratch:1"}, "pane id"),
    ({"pane": "@41"}, "pane id"),
    ({"pane": ""}, "pane id"),
    ({"pane": "-t"}, "pane id"),
    # A newline makes ONE write into TWO submissions.
    # 🔴 The message now DIAGNOSES the codepoint (offset + name + category)
    # instead of saying "control character", because the gate is an allowlist and
    # the refused set is far wider than the controls. The assertion follows the
    # gate rather than the old wording.
    ({"text": "yes\nrm -rf /"}, "a newline at offset"),
    ({"text": "yes\x1b[A"}, "non-printable character U+001B"),
    ({"text": ""}, "no text"),
    ({"text": "x" * 4000}, "size limit"),
])
def test_the_host_refuses_a_write_the_server_should_never_have_sent(
        server, tmux_stub, tmp_path, mutation, expect_in):
    server.claim_batches = [[write(**mutation)]]
    run_agent(server, tmux_stub, tmp_path)
    assert tmux_stub.send_keys_calls() == [], (
        f"the agent EXECUTED a write with {mutation} — the host is trusting the server's "
        "validation, and the host is where commands run")
    results = [r for r in server.requests if r["path"].endswith("/result")]
    assert results, "the refusal was not reported"
    body = json.loads(results[0]["body"])
    assert body["state"] == "refused", body
    assert expect_in in body["detail"], body


@pytest.mark.parametrize("cwd", ["workspace/devrc", "/home/../root", "/tmp/a\nb", ""])
def test_the_host_refuses_a_new_session_with_an_unsafe_cwd(server, tmux_stub, tmp_path, cwd):
    """A relative cwd resolves against THIS process's directory, which differs per
    host and after every restart — the silent-wrong-place failure in its purest
    form, on the one code path that creates its own target."""
    server.claim_batches = [[write(kind="new-session", pane="", cwd=cwd, text="echo hi")]]
    run_agent(server, tmux_stub, tmp_path)
    assert [c for c in tmux_stub.calls() if c and c[0] in ("new-window", "send-keys")] == [], (
        f"the agent opened a window at {cwd!r}")
    body = json.loads([r for r in server.requests if r["path"].endswith("/result")][0]["body"])
    assert body["state"] == "refused", body


def test_an_oversized_claim_batch_is_refused_WHOLE(server, tmux_stub, tmp_path):
    """🔴 REFUSE THE ROUND, DO NOT TRUNCATE IT.

    The server caps a claim at 4. A batch of a thousand — from a server that is
    wrong, compromised, or simply newer than this agent — is a thousand commands,
    and nothing downstream would stop them. Executing the first four and dropping
    the rest would be worse than refusing: it runs commands the server believes
    are in flight while silently abandoning others, a partial delivery nobody can
    reason about. Refusing leaves every row to be relabelled `abandoned`
    (delivery UNKNOWN), which is the honest record.
    """
    server.claim_batches = [[write(id=f"w-{i}") for i in range(40)]]
    _rc, out = run_agent(server, tmux_stub, tmp_path)
    assert tmux_stub.send_keys_calls() == [], (
        f"the agent executed part of an oversized batch: {tmux_stub.send_keys_calls()}")
    assert [r for r in server.requests if r["path"].endswith("/result")] == [], (
        "the agent reported outcomes for a batch it refused as a whole")
    assert "over the" in out and "writes in one claim" in out, out


def test_a_batch_at_the_cap_is_still_executed(server, tmux_stub, tmp_path):
    """The control for the refusal above: exactly at the cap goes through, so the
    refusal is the bound and not an unconditional no."""
    server.claim_batches = [[write(id=f"w-{i}") for i in range(4)]]
    run_agent(server, tmux_stub, tmp_path)
    text_sends = [s for s in tmux_stub.send_keys_calls() if "-l" in s]
    assert len(text_sends) == 4, f"expected 4 deliveries, got {text_sends}"


def test_the_validator_is_exercised_directly():
    """Every branch, including the accepting ones — without which the table above
    is satisfied by a validator that refuses everything."""
    ok = {"id": "w-abc123"}
    assert AGENT.validate({**ok, "kind": "send-keys", "pane": "%12", "text": "hi"}) == ""
    assert AGENT.validate({**ok, "pane": "%12", "text": "hi"}) == ""  # absent kind
    assert AGENT.validate({**ok, "kind": "new-session", "cwd": "/tmp", "text": "hi"}) == ""
    assert AGENT.validate({**ok, "kind": "new-session", "cwd": "/tmp", "text": ""}) == ""  # bare window
    assert "unknown write kind" in AGENT.validate({**ok, "kind": "exec", "pane": "%1", "text": "hi"})
    # Non-string fields from a malformed payload must not raise.
    assert AGENT.validate({**ok, "kind": "send-keys", "pane": 12, "text": "hi"}) != ""
    assert AGENT.validate({**ok, "kind": "new-session", "cwd": None, "text": "hi"}) != ""
    # An id this agent will not put in a URL is refused before anything else.
    assert "write id" in AGENT.validate({"kind": "send-keys", "pane": "%1", "text": "hi"})


# --------------------------------------------------------------------------- #
# 10. The text gate is an ALLOWLIST, and it is the SAME one session-write uses.
# --------------------------------------------------------------------------- #
#
# 🔴 THE DENYLIST THIS REPLACED WAS WRONG IN THE MEASURED WAY. `ord(ch) < 0x20 or
# ord(ch) == 0x7F` refuses NUL, \n, \r, ESC and DEL — and passes every C1 control
# (U+0080–U+009F), U+0085 NEL, U+2028 and U+2029. `session-write` had already
# fixed exactly this class, with a docstring recording that U+000F (Ctrl-O) got
# through its own earlier denylist and executed a pane's buffer with no Enter
# sent. Two sites, one rule, wrong at the one reachable from the network.

@pytest.mark.parametrize("ch,why", [
    ("\x0f", "Ctrl-O — the character session-write's docstring records as executing a pane's buffer"),
    ("\x85", "U+0085 NEL — a line break to some terminals, and >= 0x20 so a denylist misses it"),
    ("\x9b", "U+009B CSI — a C1 control that starts an escape sequence"),
    (" ", "U+2028 LINE SEPARATOR"),
    (" ", "U+2029 PARAGRAPH SEPARATOR"),
    ("​", "U+200B ZERO WIDTH SPACE — invisible, so the audit row and the pane disagree"),
    ("", "U+E000 private use — renders identical to its neighbour"),
    ("\n", "a newline: one write becoming TWO submissions"),
    ("\x00", "NUL"),
    ("\x1b", "ESC"),
])
def test_the_text_gate_refuses_what_a_denylist_would_pass(ch, why):
    reason = AGENT.validate({"id": "w-abc123", "kind": "send-keys", "pane": "%1",
                             "text": "echo hi" + ch})
    assert reason, f"the allowlist ACCEPTED {ch!r} — {why}"


def test_the_text_gate_still_accepts_ordinary_text():
    """The positive control. Without it every case above is satisfied by a gate
    that refuses everything, which would make the feature inert rather than safe.
    """
    # ⚠ A TAB IS DELIBERATELY ABSENT from this list — the shared policy permits
    # one, and THIS surface refuses it anyway because shell completion rewrites
    # the buffer after the audit row is written. See
    # test_the_host_refuses_a_tab_even_though_the_shared_policy_allows_one.
    for text in ("yes, go ahead", "café — naïve ünïcode ✓",
                 "run `make test` && echo $HOME; then stop", "  spaced  "):
        assert AGENT.validate({"id": "w-abc123", "kind": "send-keys",
                               "pane": "%1", "text": text}) == "", text


def test_a_trailing_tmux_separator_is_refused():
    """tmux EATS a trailing `;` off an argv token, so the pane would receive
    something other than what the audit log records — a payload silently
    corrupted somewhere nobody looks."""
    assert AGENT.validate({"id": "w-abc123", "kind": "send-keys", "pane": "%1",
                           "text": "echo hi;"}) != ""
    # …but a `;` in the MIDDLE is ordinary shell and must pass.
    assert AGENT.validate({"id": "w-abc123", "kind": "send-keys", "pane": "%1",
                           "text": "echo hi; echo there"}) == ""


def test_the_agent_and_session_write_share_ONE_predicate():
    """🔴 ONE RULE, ONE PLACE — asserted as a RELATIONSHIP, not as two green suites.

    Both programs deliver literal text to a tmux pane. Before this they each had
    their own answer to "what may be delivered", and the network-facing one was
    the weaker: a denylist that passed every C1 control. The rule now lives in
    `scripts/lib/tmux_text_policy.py`.

    🔴 THE ASSERTION IS ON `__code__.co_filename`, NOT ON OBJECT IDENTITY. Both
    programs are SCRIPTS loaded by path, so each gets its own module instance of
    the policy and the two function objects are legitimately different — an `is`
    check fails while the rule is perfectly shared, which is a guard that cries
    wolf until someone deletes it. What actually matters is the SOURCE both
    predicates were compiled from, and that is what this reads.
    """
    sw = _load_session_write()
    shared = str((REPO_ROOT / "scripts" / "lib" / "tmux_text_policy.py").resolve())
    for name, fn in (("session-write", sw.TEXT_IS_ALLOWED),
                     ("tmux-reply-agent", AGENT.TEXT_POLICY.TEXT_IS_ALLOWED)):
        got = str(pathlib.Path(fn.__code__.co_filename).resolve())
        assert got == shared, (
            f"{name}'s TEXT_IS_ALLOWED was compiled from {got}, not the shared policy at "
            f"{shared} — it has its own copy again, and a second copy of this rule has "
            "already been wrong once")
    assert sw.TEXT_EXTRA_ALLOWED == AGENT.TEXT_POLICY.TEXT_EXTRA_ALLOWED == ("\t",)
    # The separator the two could have disagreed about is pinned at
    # session-write's import against session-resolve's own constant.
    assert sw.TMUX_ARGV_SEPARATOR == AGENT.TEXT_POLICY.TMUX_ARGV_SEPARATOR


def test_neither_script_defines_a_SECOND_text_predicate():
    """The other half: no third copy, and no resurrection of the denylist.

    AST, not grep — this file's own prose names `has_control` repeatedly, and a
    raw-text scan would fire on the comment explaining why it is gone. Only
    function DEFINITIONS are examined.
    """
    for script in ("session-write", "tmux-reply-agent"):
        tree = ast.parse((REPO_ROOT / "scripts" / script).read_text())
        defined = {n.name for n in ast.walk(tree)
                   if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
        for banned in ("TEXT_IS_ALLOWED", "has_control"):
            assert banned not in defined, (
                f"{script} defines its own {banned}(); the text policy has ONE home "
                "(scripts/lib/tmux_text_policy.py) and a second copy of it has already "
                "shipped wrong once")


def test_the_shared_predicate_agrees_with_itself_across_the_whole_BMP():
    """A behavioural sweep beside the structural check above.

    The structural check says both callers import one file; this says the file's
    answer is the one the callers' own gates actually produce, over a range no
    hand-written fixture covers. It is what catches a consumer that imports the
    predicate and then second-guesses it.
    """
    policy = AGENT.TEXT_POLICY
    disagreements = []
    for cp in range(0x20, 0x2100):
        ch = chr(cp)
        allowed = policy.TEXT_IS_ALLOWED(ch)
        refused_by_agent = AGENT.validate(
            {"id": "w-abc123", "kind": "send-keys", "pane": "%1", "text": "a" + ch + "b"}) != ""
        if allowed == refused_by_agent:
            disagreements.append(f"U+{cp:04X}")
    assert not disagreements, (
        "the agent's gate disagrees with the shared allowlist at: "
        + ", ".join(disagreements[:20]))
    # The positive control: the sweep must have seen BOTH answers, or it is a
    # loop that proves nothing.
    assert policy.TEXT_IS_ALLOWED("A") and not policy.TEXT_IS_ALLOWED("\u200b")


def test_the_agent_REFUSES_TO_START_without_the_policy_module():
    """🔴 NO FALLBACK. A missing policy module must stop the program, not leave
    it with a weaker check — a silent fallback is how the denylist would come
    back, and it would come back at the network-facing site."""
    with pytest.raises(FileNotFoundError):
        AGENT._load_tmux_text_policy("/nonexistent/tmux_text_policy.py")


def _load_session_write():
    """Import session-write by path.

    🔴 REGISTERED IN sys.modules BEFORE exec_module. It defines dataclasses, and
    `dataclasses` resolves a string annotation through `sys.modules[cls.__module__]`
    — which is None for a module that is being executed but not registered, and
    the failure is an AttributeError inside the stdlib rather than anything that
    names the real cause.
    """
    name = "session_write_undertest"
    if name in sys.modules:
        return sys.modules[name]
    path = REPO_ROOT / "scripts" / "session-write"
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    try:
        loader.exec_module(mod)
    except Exception:
        del sys.modules[name]
        raise
    return mod


# --------------------------------------------------------------------------- #
# 11. The host's OWN at-most-once, the id shape, and the new-session submit.
# --------------------------------------------------------------------------- #
def test_the_host_refuses_a_write_id_it_has_already_executed(server, tmux_stub, tmp_path):
    """🔴 THE HOST'S OWN at-most-once, and it is not redundant with the server's.

    The server's guarantee is a property of its claim predicate. This agent is
    the thing that RUNS the command — so a server that is wrong, rolled back,
    restored from a backup, or simply newer would re-execute a claimed row, and
    the operator's decision that clawgate presses Enter makes that a second
    command execution.
    """
    server.claim_batches = [[write()], [write()]]   # the SAME id, twice
    run_agent(server, tmux_stub, tmp_path, expect_requests=4)
    text_sends = [s for s in tmux_stub.send_keys_calls() if "-l" in s]
    assert len(text_sends) == 1, (
        f"the agent executed the same write id {len(text_sends)} times: {tmux_stub.send_keys_calls()}")
    states = [json.loads(r["body"])["state"]
              for r in server.requests if r["path"].endswith("/result")]
    assert states[0] == "delivered", states
    assert "refused" in states[1:], (
        f"the duplicate was not reported as refused ({states}); a silently dropped duplicate is "
        "indistinguishable in the audit log from one that never arrived")


def test_the_dedupe_memory_is_bounded():
    """An unbounded set in a process that runs for weeks is a slow leak."""
    AGENT._SEEN.clear()
    for i in range(AGENT.SEEN_LIMIT + 50):
        assert not AGENT.already_executed(f"id{i}")
    assert len(AGENT._SEEN) <= AGENT.SEEN_LIMIT
    # Oldest-first eviction: the earliest ids are the ones forgotten.
    assert "id0" not in AGENT._SEEN
    assert f"id{AGENT.SEEN_LIMIT + 49}" in AGENT._SEEN
    AGENT._SEEN.clear()


@pytest.mark.parametrize("wid", ["", "../other", "a/b", "x" * 65, "a b", "a\nb", None, 7])
def test_an_unusable_write_id_is_never_put_in_a_url(server, tmux_stub, tmp_path, wid):
    """🔴 THE ID IS INTERPOLATED INTO A URL THIS AGENT CONSTRUCTS. A server-supplied
    id it cannot vouch for could steer the request somewhere else, and it would
    otherwise be unbounded text in a log line too."""
    server.claim_batches = [[write(id=wid)]]
    run_agent(server, tmux_stub, tmp_path)
    assert tmux_stub.send_keys_calls() == [], "a write with an unusable id was executed"
    for req in server.requests:
        assert "/result" not in req["path"], f"a request was aimed at {req['path']!r}"


def test_a_new_session_ALWAYS_submits_whatever_the_row_says(server, tmux_stub, tmp_path):
    """🔴 BOTH DIRECTIONS, because neither was pinned before.

    A new-session exists to produce a window that is DOING something; typing a
    command into a fresh shell and not running it leaves the operator a window to
    go and finish by hand. So `submit` on the row describes the send-keys case
    and is not consulted here — and that has to be asserted against a row saying
    `false`, or a mutant that starts honouring the field survives.
    """
    for submit in (True, False):
        server.requests.clear()
        tmux_stub.reset()
        AGENT._SEEN.clear()
        server.claim_batches = [[write(id=f"w-ns{int(submit)}", kind="new-session", pane="",
                                       cwd=real_dir(tmp_path), text="echo hi", submit=submit)]]
        run_agent(server, tmux_stub, tmp_path)
        sends = tmux_stub.send_keys_calls()
        assert len(sends) == 2, (
            f"submit={submit}: expected the text send AND the Enter send, got {sends}")
        assert sends[1][-1] == "Enter", sends[1]
    assert AGENT.NEW_SESSION_ALWAYS_SUBMITS is True


def test_a_server_supplied_pane_cannot_forge_a_log_line(server, tmux_stub, tmp_path):
    """Every field in a claim is server-controlled. A pane that failed the id
    check must not be echoed verbatim into the journal — a newline in it would
    forge a second, well-formed-looking log line."""
    server.claim_batches = [[write(pane="%1\ntmux-reply-agent: delivered everything")]]
    _rc, out = run_agent(server, tmux_stub, tmp_path)
    assert "delivered everything" not in out, out
    assert tmux_stub.send_keys_calls() == []


# --------------------------------------------------------------------------- #
# 12. The launched window goes where the CALLER said, not where tmux felt like.
# --------------------------------------------------------------------------- #
def test_a_named_tmux_session_is_passed_to_tmux_as_a_target(server, tmux_stub, tmp_path):
    """🔴 WITHOUT `-t` THE TARGET IS DECIDED BY tmux, NOT BY THE CALLER.

    `new-window` with no target joins whatever session this server considers
    CURRENT — which depends on what the operator last looked at — so a launched
    window lands somewhere nobody chose. That is the same wrong-place class the
    pane rule exists for, one level up: a whole WINDOW rather than a keystroke.

    The trailing colon is part of the contract: `-t "name:"` means "this session,
    next free index", and a session that does not exist makes tmux FAIL rather
    than silently fall back to the current one.
    """
    server.claim_batches = [[write(kind="new-session", pane="", cwd=real_dir(tmp_path),
                                   tmuxSessionName="scratch2", text="echo hi")]]
    run_agent(server, tmux_stub, tmp_path)
    new_windows = [c for c in tmux_stub.calls() if c and c[0] == "new-window"]
    assert len(new_windows) == 1, tmux_stub.calls()
    # 🔴 `=` FORCES AN EXACT SESSION MATCH. Without it tmux PREFIX-MATCHES:
    # `-t scratch2:` with `scratch2` absent and `scratch20` live returns rc 0 and
    # opens the window in `scratch20` — measured on 3.7c against the operator's
    # own server, which holds exactly that pair.
    assert new_windows[0] == ["new-window", "-P", "-F", AGENT.NEW_WINDOW_FORMAT,
                              "-c", real_dir(tmp_path), "-t", "=scratch2:"], new_windows[0]


def test_an_absent_tmux_session_leaves_the_target_to_tmux(server, tmux_stub, tmp_path):
    """The control, and the back-compatible half: absent means "wherever this
    server considers current", which is what happened before the field existed
    and the only honest answer when nobody expressed a preference. A `-t` with an
    empty value would be a target of its own."""
    server.claim_batches = [[write(kind="new-session", pane="", cwd=real_dir(tmp_path), text="echo hi")]]
    run_agent(server, tmux_stub, tmp_path)
    new_windows = [c for c in tmux_stub.calls() if c and c[0] == "new-window"]
    assert len(new_windows) == 1
    assert "-t" not in new_windows[0], new_windows[0]


@pytest.mark.parametrize("name", [
    "scratch:2",            # a window index inside another session
    "scratch:2.0",          # ...and a pane inside it
    "main.0",               # a pane address
    "-t",                   # option-shaped
    "has space",
    "weirdname",      # a C1 control the old denylist would have passed
    "a" * 65,
])
def test_the_host_refuses_a_compound_tmux_target(server, tmux_stub, tmp_path, name):
    """🔴 RE-VALIDATED ON THE HOST, NOT TRUSTED. The server checks this too; this
    process is the one that hands the value to tmux, and the two sides of the
    boundary fail independently."""
    server.claim_batches = [[write(kind="new-session", pane="", cwd=real_dir(tmp_path),
                                   tmuxSessionName=name, text="echo hi")]]
    run_agent(server, tmux_stub, tmp_path)
    assert [c for c in tmux_stub.calls() if c and c[0] in ("new-window", "send-keys")] == [], (
        f"the agent opened a window targeting {name!r}")
    body = json.loads([r for r in server.requests if r["path"].endswith("/result")][0]["body"])
    assert body["state"] == "refused", body


def test_the_host_refuses_a_tab_even_though_the_shared_policy_allows_one():
    """🔴 A DELIBERATE DIVERGENCE FROM THE SHARED POLICY, IN THE SAFE DIRECTION.

    `send-keys -l` delivers a tab literally and a bash/zsh pane runs COMPLETION on
    it: `rm -rf ~/pro` + TAB becomes `rm -rf ~/projects/`, and the Enter this path
    presses submits THAT — while clawgate's audit row still says `rm -rf ~/pro`.
    That is the identical property the trailing-`;` refusal exists for.

    `session-write` keeps the tab because it is invoked by the operator at their
    own keyboard, where the buffer is visible before anything runs. Here the row
    IS the compensating control, so a character that makes it lie costs more than
    the typography is worth.

    Both halves are asserted: the shared policy still permits it (so this is a
    divergence, not a policy change that would surprise session-write), and this
    surface refuses it anyway.
    """
    assert AGENT.TEXT_POLICY.TEXT_IS_ALLOWED("\t"), (
        "the shared policy no longer allows a tab — this test is now asserting a divergence "
        "that does not exist, and session-write's own guards would have moved too")
    assert AGENT.validate({"id": "w-abc123", "kind": "send-keys", "pane": "%1",
                           "text": "rm -rf ~/pro\t"}) != ""
    # The control: the same text without the tab is ordinary and passes.
    assert AGENT.validate({"id": "w-abc123", "kind": "send-keys", "pane": "%1",
                           "text": "rm -rf ~/projects/"}) == ""


# --------------------------------------------------------------------------- #
# 13. REAL tmux. The stub cannot express either of these.
# --------------------------------------------------------------------------- #
#
# 🔴 THE STUB ALWAYS EXITS 0, so it models neither of the two resolution rules
# that shipped as defects: tmux PREFIX-MATCHES a session name, and `-c <missing
# path>` falls back to the home directory. Both were measured live, both reported
# `delivered`, and both were invisible to every stubbed test in this file. These
# drive a REAL tmux on a private `-L` socket — never the operator's server — and
# `tmux` is in REQUIRED_TOOLS and flake.nix's gateTools so they cannot silently
# skip in the tier that gates.

@pytest.fixture
def real_tmux(tmp_path):
    """A private tmux server with known sessions, torn down afterwards.

    🔴 `-L <unique>` AND A PRIVATE TMUX_TMPDIR. This must never touch the
    operator's live server: the thing under test CREATES WINDOWS.
    """
    sock = "rank29-" + os.path.basename(str(tmp_path))
    sockdir = tempfile.mkdtemp(prefix="tmuxr29.")
    env = dict(os.environ, TMUX_TMPDIR=sockdir)

    def tmux(*args, check=True):
        p = subprocess.run(["tmux", "-L", sock, *args], capture_output=True,
                           text=True, env=env, timeout=30)
        if check and p.returncode != 0:
            raise AssertionError(f"tmux {args} failed: {p.stderr}")
        return p

    # The operator's real server holds `scratch`, `scratch2` … `scratch20` and
    # `datapacket-talos`, `datapacket-talos-2`. This reproduces the SHAPE that
    # makes a prefix match land somewhere else: a longer name present, the
    # shorter one absent.
    tmux("new-session", "-d", "-s", "scratch20")
    tmux("new-session", "-d", "-s", "keep")
    try:
        yield {"sock": sock, "env": env, "tmux": tmux}
    finally:
        subprocess.run(["tmux", "-L", sock, "kill-server"], capture_output=True,
                       env=env, timeout=30)
        shutil.rmtree(sockdir, ignore_errors=True)


def _agent_with_real_tmux(monkeypatch, real_tmux):
    """Point the agent's tmux seam at the private server."""
    sock, env = real_tmux["sock"], real_tmux["env"]

    def run(args):
        p = subprocess.run(["tmux", "-L", sock, *args], capture_output=True,
                           text=True, env=env, timeout=30)
        return p.returncode, p.stdout, p.stderr

    monkeypatch.setattr(AGENT, "run_tmux", run)


def test_a_MISSING_session_is_refused_not_prefix_matched(monkeypatch, real_tmux):
    """🔴 THE FIRST SHIPPED DEFECT, against real tmux.

    tmux prefix-matches a session name. `-t scratch2:` with `scratch2` absent and
    `scratch20` live returns rc 0 and opens the window in `scratch20` — measured
    on 3.7c against the operator's own server, where `scratch2` … `scratch20`
    both exist. End to end the agent typed the command, pressed Enter and
    reported `delivered`.

    `scratch2` is the literal this file's own fixtures use.
    """
    _agent_with_real_tmux(monkeypatch, real_tmux)
    before = real_tmux["tmux"]("list-windows", "-a", "-F", "#{session_name}").stdout

    pane, err = AGENT.open_window(str(pathlib.Path.home()), "scratch2")
    assert err, ("open_window SUCCEEDED for a session that does not exist. tmux prefix-matched "
                 "it to `scratch20` and the next step presses Enter.")
    assert pane == ""
    after = real_tmux["tmux"]("list-windows", "-a", "-F", "#{session_name}").stdout
    assert after == before, f"a window was created anyway:\nbefore={before!r}\nafter={after!r}"


def test_the_EXACT_session_still_works(monkeypatch, real_tmux):
    """The control for the refusal above: the real name opens a real window, so
    the refusal is the `=` prefix and not a gate that refuses everything."""
    _agent_with_real_tmux(monkeypatch, real_tmux)
    pane, err = AGENT.open_window(str(pathlib.Path.home()), "scratch20")
    assert not err, err
    assert pane.startswith("%")
    landed = real_tmux["tmux"](
        "display-message", "-p", "-t", pane, "#{session_name}").stdout.strip()
    assert landed == "scratch20", landed


def test_a_cwd_that_does_not_exist_is_REFUSED_not_opened_in_HOME(monkeypatch, real_tmux, tmp_path):
    """🔴 THE SECOND SHIPPED DEFECT, against real tmux.

    `new-window -c <absolute path that does not exist>` returns rc 0 and opens
    the window in the home directory — measured. End to end the agent reported
    `state='delivered' detail='opened %1'` while the command ran in $HOME, so a
    build or a recursive delete would have run there.
    """
    _agent_with_real_tmux(monkeypatch, real_tmux)
    missing = str(tmp_path / "definitely" / "not" / "here")
    pane, err = AGENT.open_window(missing, "scratch20")
    assert err, ("open_window SUCCEEDED for a directory that does not exist; tmux fell back to "
                 "the home directory and the command would have run there")
    assert pane == ""

    # The control: a directory that DOES exist opens there, and the read-back
    # agrees — so the refusal is the fallback and not an unconditional no.
    real = tmp_path / "real"
    real.mkdir()
    pane, err = AGENT.open_window(str(real), "scratch20")
    assert not err, err
    landed = real_tmux["tmux"](
        "display-message", "-p", "-t", pane, "#{pane_current_path}").stdout.strip()
    assert AGENT.same_directory(landed, str(real)), f"landed={landed!r} want={real}"


def test_a_trailing_separator_in_the_cwd_is_refused(tmp_path):
    """tmux eats a trailing separator off the `-c` word exactly as it does off a
    send-keys token, so the path becomes a DIFFERENT path — and if that does not
    exist, the window opens in the home directory. The text path already refused
    this character; the cwd path did not."""
    real = tmp_path / "build"
    real.mkdir()
    ok = {"id": "w-abc123", "kind": "new-session", "pane": "", "text": "make"}
    assert AGENT.validate({**ok, "cwd": str(real)}) == ""
    for bad in (str(real) + ";", str(real) + " ", " " + str(real)):
        assert AGENT.validate({**ok, "cwd": bad}) != "", bad


def test_a_nonexistent_cwd_is_refused_before_tmux_is_reached(tmp_path):
    """The early, legible refusal. open_window re-checks where tmux ACTUALLY
    landed; this makes the common case a clear message rather than a fallback."""
    ok = {"id": "w-abc123", "kind": "new-session", "pane": "", "text": "make"}
    assert "does not exist" in AGENT.validate({**ok, "cwd": str(tmp_path / "nope")})
    assert AGENT.validate({**ok, "cwd": str(tmp_path)}) == ""


# --------------------------------------------------------------------------- #
# 14. The guards that had no coverage.
# --------------------------------------------------------------------------- #
def test_the_shared_scan_sees_a_bad_character_at_OFFSET_ZERO():
    """🟡 THE EXACT TRAP THE POLICY'S OWN DOCSTRING RECORDS, in the one caller.

    `enumerate(text)` → `enumerate(text[1:], 1)` SURVIVED all 85 tests: every
    fixture in this file puts the bad character LATER (`"echo hi" + ch`,
    `"a" + ch + "b"`), so a scan blind at offset 0 refuses the same character at
    the end and DELIVERS it at the start. session-write's own offset-zero test
    cannot cover this — that file open-codes its own scan, and `first_disallowed`
    has exactly one caller: this agent, the network-facing one.
    """
    policy = AGENT.TEXT_POLICY
    for ch in ("\x0f", "\n", "\x1b", "\x00", "​", "\x85"):
        got = policy.first_disallowed(ch + "echo hi")
        assert got is not None, f"{ch!r} at offset 0 was not seen by the shared scan"
        assert got[0] == 0, f"{ch!r} reported at offset {got[0]}, want 0"
        # …and through the agent's own gate, which is what actually ships.
        assert AGENT.validate({"id": "w-abc123", "kind": "send-keys", "pane": "%1",
                               "text": ch + "echo hi"}) != "", ch
    # The control: an allowed character at offset 0 is still allowed, so this is
    # not a scan that refuses everything at the start.
    assert policy.first_disallowed("echo hi") is None


def test_the_response_read_is_BOUNDED(monkeypatch):
    """🟡 `resp.read(MAX)` → `resp.read()` survived all 85 tests. A bare read
    takes whatever the peer sends into a process on the operator's workstation."""
    captured = {}

    class FakeResp:
        def read(self, *args):
            captured["args"] = args
            return b'{"writes":[]}'

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(AGENT.urllib.request, "urlopen", lambda *a, **k: FakeResp())
    AGENT.post_json("http://127.0.0.1:1/x", "tok", {})
    assert captured["args"], "the response was read with NO limit argument"
    assert captured["args"][0] == AGENT.MAX_RESPONSE_BYTES


def test_a_write_that_makes_the_validator_RAISE_is_still_reported(server, tmux_stub, tmp_path):
    """🟡 A non-string `kind` raised out of the whole round: no result was POSTed
    for that row OR ANY LATER ROW in the batch, so they landed `abandoned` —
    "delivery UNKNOWN" — when the truthful answer is `refused`, nothing ran.

    An audit log that says UNKNOWN where it could say NO is exactly the failure
    its design is trying to avoid.
    """
    AGENT._SEEN.clear()
    server.claim_batches = [[write(id="w-bad", kind=123), write(id="w-later")]]
    run_agent(server, tmux_stub, tmp_path, expect_requests=4)
    results = {json.loads(r["body"])["state"]
               for r in server.requests if r["path"].endswith("/result")}
    posted = [r["path"] for r in server.requests if r["path"].endswith("/result")]
    assert any("w-bad" in p for p in posted), (
        f"the raising write was never reported, so it becomes `abandoned` — delivery UNKNOWN — "
        f"when nothing ran. Reported: {posted}")
    assert any("w-later" in p for p in posted), (
        f"a LATER write in the same batch was never reported either: {posted}")
    assert "refused" in results, results


@pytest.mark.parametrize("field,value", [
    ("kind", 123), ("kind", None), ("expectTmuxServerId", []),
    ("expectTmuxServerId", 7), ("pane", 12), ("cwd", None), ("text", 5), ("id", 9),
])
def test_the_validator_never_raises_on_a_non_string_field(field, value):
    """The sentence this test's predecessor made was wider than the test: it
    claimed "non-string fields must not raise" and exercised only `pane` and
    `cwd`. Every field a server can send is a string in the contract and none of
    them is guaranteed to be one on the wire."""
    w = {"id": "w-abc123", "kind": "send-keys", "pane": "%1", "text": "hi", field: value}
    assert isinstance(AGENT.validate(w), str)


def test_a_refusal_detail_is_CLAMPED_like_every_other(server, tmux_stub, tmp_path):
    """🟡 A validate() refusal bypassed redact() entirely: an unbounded
    server-supplied `kind` interpolated into the message produced a
    100,021-character detail, into a journal line and into the audit-log column
    — while the comment beside it asserted every interpolated field was bounded.
    """
    AGENT._SEEN.clear()
    server.claim_batches = [[write(kind="x" * 100000)]]
    _rc, out = run_agent(server, tmux_stub, tmp_path)
    body = json.loads(
        [r for r in server.requests if r["path"].endswith("/result")][0]["body"])
    assert len(body["detail"]) <= 400, (
        f"the reported detail is {len(body['detail'])} characters; every other path is clamped "
        "to 400 and this one is stored in a retained column")
    for line in out.splitlines():
        assert len(line) < 1000, f"a journal line is {len(line)} characters"


def test_a_window_that_LANDED_ELSEWHERE_is_refused_even_when_tmux_said_ok(
        server, tmux_stub, tmp_path):
    """🔴 THE READ-BACK, PINNED INDEPENDENTLY OF THE CHECKS THAT USUALLY FIRE FIRST.

    `=` makes tmux fail for a session that does not exist, and `isdir` refuses a
    cwd that does not exist — so in the ordinary case the read-back never
    decides anything, and removing it left every test green (measured, as two
    surviving mutants). It exists for the case those two cannot see: tmux
    resolving the target to something else for a reason nobody here has thought
    of. Only a stub that LIES about where the window landed can express that.

    The assertion is that NO keys are sent, not merely that the outcome says
    `refused`: the next step presses Enter, so a check that runs after the typing
    is not a check.
    """
    AGENT._SEEN.clear()
    tmux_stub.lie_about_landing(session="somewhere-else")
    server.claim_batches = [[write(id="w-sess", kind="new-session", pane="",
                                   cwd=real_dir(tmp_path), tmuxSessionName="scratch2",
                                   text="echo hi")]]
    run_agent(server, tmux_stub, tmp_path)
    assert tmux_stub.send_keys_calls() == [], (
        "the agent typed into a window tmux placed in a DIFFERENT session than the one "
        "requested, and the next step presses Enter")
    body = json.loads(
        [r for r in server.requests if r["path"].endswith("/result")][0]["body"])
    assert body["state"] == "failed", body
    assert "not the requested" in body["detail"], body


def test_a_window_that_landed_in_the_WRONG_DIRECTORY_is_refused(
        server, tmux_stub, tmp_path):
    """The same, for the working directory.

    `isdir` refuses the common case up front; this is the one where the path
    exists and tmux still put the window somewhere else. A build or a recursive
    delete running one directory over is the whole reason the read-back is there.
    """
    AGENT._SEEN.clear()
    tmux_stub.lie_about_landing(path=str(tmp_path / "elsewhere"))
    (tmp_path / "elsewhere").mkdir(exist_ok=True)
    server.claim_batches = [[write(id="w-path", kind="new-session", pane="",
                                   cwd=real_dir(tmp_path), text="make")]]
    run_agent(server, tmux_stub, tmp_path)
    assert tmux_stub.send_keys_calls() == [], (
        "the agent typed a command into a window tmux opened in a directory OTHER than the "
        "one requested")
    body = json.loads(
        [r for r in server.requests if r["path"].endswith("/result")][0]["body"])
    assert body["state"] == "failed", body
    assert "not the requested" in body["detail"], body


def test_the_agent_SHELLS_OUT_TO_TMUX_AND_NOTHING_ELSE():
    """🔴 THE PIN `test_no_real_launchers.py`'s ACKNOWLEDGEMENT PROMISES, and it
    did not exist until an audit went looking for it.

    That ledger acknowledges this file as reaching `systemctl` in PROSE — one
    line of module docstring giving the operator the second half of the disarm
    procedure. The acknowledgement is only honest while the prose stays prose,
    and its justification asserted that "test_tmux_reply_agent.py pins that the
    agent shells out to nothing else". No such test existed; the sentence named
    a compensating control that was never written.

    🔴 THAT GAP WAS MEASURED, NOT INFERRED. `launcher_scan.hazard_hits` returns a
    FILE SET, and the ledger asserts set equality — so once this file is IN the
    set for `systemctl`, a genuine call site added to it changes the set by
    nothing and the ledger stays green. Injecting

        subprocess.run(["systemctl", "--user", "restart", "some-unit"], check=False)

    into this agent left `test_no_real_launchers.py` at **77 passed**. The
    acknowledgement had blinded the very guard it was filed under.

    So the pin is written here, where it can see argv rather than file names:
    a DIRECT spawn in the agent must take its argv from `tmux_bin()`. AST, not
    grep — this file and the agent both NAME `systemctl` in prose, and a text
    scan cannot tell a docstring from a call.

    🔴 WHAT IT DOES NOT SEE, STATED RATHER THAN IMPLIED — an unqualified "every
    spawn" is what made the two previous versions of this docstring false, and a
    guard whose sentence is wider than its body reads as coverage while providing
    none. MEASURED SURVIVORS at this revision: reflective lookup
    (`getattr(subprocess, "run")`, `importlib.import_module`, `__import__`), a
    star-import (`from subprocess import *`), an indirect binding
    (`_m = subprocess` / `f = run`), and spawners in OTHER modules (`pty.spawn`,
    `asyncio.create_subprocess_exec`). Those are residuals, not oversights: each
    needs a deliberate indirection, whereas the verbs above are what ordinary
    code reaches for. If this file ever grows one of them, this guard will not
    say so — and neither will the launcher ledger.

    🔴 IT RESOLVES IMPORT BINDINGS, BECAUSE ITS FIRST VERSION DID NOT AND WAS
    NARROWER THAN THIS DOCSTRING. That draft matched only a literal
    `subprocess.<verb>(...)` / `os.<verb>(...)` attribute call, so an ALIAS
    walked straight past a guard whose sentence said "nothing else". MEASURED,
    with every `__pycache__` purged and PYTHONDONTWRITEBYTECODE=1 (a `cp -a`
    battery preserves pytest-rewritten bytecode and scores phantom survivors):

        subprocess.run(["systemctl", ...])          KILLED
        os.execv("/bin/systemctl", [...])           KILLED
        subprocess.run(cmd)  # variable argv        KILLED
        from subprocess import run as _r; _r([...]) SURVIVED
        from subprocess import run;      run([...]) SURVIVED
        import subprocess as _sp; _sp.run([...])    SURVIVED
        os.posix_spawn("/.../systemctl", [...], {}) SURVIVED

    Four survivors, and with one of them live the two files were 183 passed —
    the round-2 defect exactly, one import spelling narrower. `claude/RULES.md`:
    "a guard's DESCRIPTION claims COVERAGE — check the implementation is as wide
    as the sentence."

    So the binding map below is the guard, not a nicety: `import subprocess as X`
    and `from subprocess import run as Y` are resolved to their real targets, and
    `posix_spawn` is named explicitly rather than caught by a prefix.
    """
    import ast

    src = SCRIPT.read_text()
    tree = ast.parse(src)

    # 🔴 `getoutput`/`getstatusoutput` ARE IN THIS SET BECAUSE THEY RUN A SHELL
    # and were missing from the first five. Same module, same `subprocess.<verb>()`
    # shape this walker already sees — no aliasing, no reflection — so
    # `subprocess.getoutput(f"systemctl --user is-active {unit}")` SURVIVED a guard
    # whose sentence said "nothing else", while the launcher ledger (which asserts
    # a FILE set, and this file is already in it) is structurally blind to it too.
    SUBPROCESS_VERBS = {"run", "Popen", "call", "check_call", "check_output",
                        "getoutput", "getstatusoutput"}
    # 🔴 NOT every `os.*` — an early draft took the whole module and matched
    # `os.getpid()`, failing on a CLEAN tree. A negative control that goes red is
    # a broken instrument, not a finding. Only verbs that can START a process.
    def _os_spawns(a):
        return a.startswith("exec") or a.startswith("spawn") or a in (
            "system", "popen", "posix_spawn", "posix_spawnp", "forkpty", "fork")

    # name -> "subprocess" | "os", following `as` aliases.
    mod_alias = {}
    # bare name -> the module it was imported OUT of, following `as` aliases.
    # ⚠ SCOPE-BLIND, BOTH WAYS, AND LATENT TODAY. This walks the whole module, so
    # a `from subprocess import ...` inside a function body registers globally
    # (over-strict — fails loud, safe direction), and a LOCAL rebinding of the
    # same name is read as the import (a FALSE POSITIVE: a parameter named `run`
    # in `def _use(run, argv): return run(argv)` is flagged as a spawn). Latent
    # because `scripts/tmux-reply-agent` imports `os` and `subprocess` as modules
    # only, with no from-import, so this dict is EMPTY on the real tree. It goes
    # live the first time anyone adds one; prefer renaming the local over
    # loosening this map.
    fn_alias = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name in ("subprocess", "os"):
                    mod_alias[a.asname or a.name] = a.name
        elif isinstance(node, ast.ImportFrom):
            if node.module in ("subprocess", "os"):
                for a in node.names:
                    fn_alias[a.asname or a.name] = (node.module, a.name)

    argv0 = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        mod = attr = None
        if isinstance(fn, ast.Attribute):
            base = getattr(getattr(fn, "value", None), "id", None)
            mod, attr = mod_alias.get(base), fn.attr
        elif isinstance(fn, ast.Name) and fn.id in fn_alias:
            mod, attr = fn_alias[fn.id]

        if mod is None:
            continue
        spawns = (mod == "subprocess" and attr in SUBPROCESS_VERBS) or \
                 (mod == "os" and _os_spawns(attr))
        if not spawns:
            continue
        if not node.args:
            argv0.append(f"<no-argv:{mod}.{attr}>")
            continue
        first = node.args[0]
        if isinstance(first, ast.BinOp) and isinstance(first.left, ast.List):
            first = first.left
        if isinstance(first, ast.List) and first.elts:
            head = first.elts[0]
        else:
            head = first
        if isinstance(head, ast.Call) and isinstance(head.func, ast.Name):
            argv0.append(head.func.id)
        elif isinstance(head, ast.Constant):
            argv0.append(repr(head.value))
        else:
            argv0.append(ast.dump(head)[:60])

    assert set(argv0) == {"tmux_bin"}, (
        f"the agent's subprocess argv[0] set is {sorted(set(argv0))}, not {{'tmux_bin'}}. "
        "This agent must shell out to tmux and nothing else (this check sees DIRECT spawns; "
        "see the docstring for the reflective/star-import residuals it does not) — it runs "
        "as the operator, is "
        "driven by a network route, and `test_no_real_launchers.py` ACKNOWLEDGES its prose "
        "mention of `systemctl` on exactly this basis. A new argv[0] here is invisible to "
        "that ledger (it asserts a FILE set, and this file is already in it), so this is the "
        "only place the addition can be seen. Do not relax it to make a call site pass.")


def test_an_EMPTY_pane_current_path_does_not_collapse_the_readback(monkeypatch):
    """🔴 A WINDOW TMUX ALREADY CREATED WAS BEING REJECTED BY A `.strip()`.

    `NEW_WINDOW_FORMAT` is `#{pane_id}\\t#{session_name}\\t#{pane_current_path}`.
    The read-back did `out.strip().splitlines()[0].split("\\t")` — and `.strip()`
    removes a TRAILING TAB, so a well-formed three-field line whose last field is
    empty arrives as TWO fields and fails the length check. The window exists by
    then: the caller gets an error, a stray window is left behind, and a retry
    would make a second one.

    MEASURED as an intermittent real-tmux failure, 3 occurrences across ~12 runs
    of `test_the_EXACT_session_still_works`, one on a completely unmutated tree,
    with wall times within 0.5 s of the control — so not a load flake. The
    observed string `'%2\\tscratch20'` is byte-identical to `'%2\\tscratch20\\t'`
    after `.strip()`, which is what identified the mechanism.

    🔴 AND THE PARSE IS ONLY HALF OF IT. With the fields split correctly,
    `landed_path` is `""`, and `same_directory("", cwd)` does NOT compare empty
    against cwd — `os.path.realpath("")` returns the AGENT'S OWN cwd, so the
    check would silently compare the wrong directory and usually refuse. An
    unreadable path is "not readable yet", NOT "tmux fell back to the wrong
    directory", and the two must not share a code path.

    This drives `run_tmux` directly rather than through the tmux stub, because
    the stub composes its own third field and cannot express an EMPTY one — the
    exact value under test.
    """
    calls = []

    def fake_run_tmux(args):
        calls.append(args)
        if args[0] == "new-window":
            # Well-formed, three fields, last one EMPTY. Note the trailing tab.
            return 0, "%2\tscratch20\t\n", ""
        if args[0] == "display-message":
            return 0, str(pathlib.Path.home()) + "\n", ""
        raise AssertionError(f"unexpected tmux call: {args}")

    monkeypatch.setattr(AGENT, "run_tmux", fake_run_tmux)
    pane, err = AGENT.open_window(str(pathlib.Path.home()), "scratch20")

    assert not err, (
        f"a three-field read-back whose last field is empty was rejected: {err!r}. "
        "The window already exists at this point, so this is a lost window plus a "
        "misleading error, not a refusal.")
    assert pane == "%2", pane
    assert any(a[0] == "display-message" for a in calls), (
        "an empty pane_current_path must be RE-READ for that pane, not treated as a "
        "directory mismatch — `os.path.realpath('')` is the agent's own cwd, so the "
        "same_directory check would compare something nobody asked about.")


def test_an_UNREADABLE_pane_current_path_still_REFUSES(monkeypatch):
    """The other half, so the fix above cannot become "accept anything".

    If the re-read ALSO comes back empty the directory is genuinely unverifiable,
    and this agent presses Enter — so it must refuse rather than type into a pane
    whose location nothing confirmed.
    """
    def fake_run_tmux(args):
        if args[0] == "new-window":
            return 0, "%2\tscratch20\t\n", ""
        if args[0] == "display-message":
            return 0, "\n", ""
        raise AssertionError(f"unexpected tmux call: {args}")

    monkeypatch.setattr(AGENT, "run_tmux", fake_run_tmux)
    pane, err = AGENT.open_window(str(pathlib.Path.home()), "scratch20")
    assert pane == "", pane
    assert "directory" in err.lower(), err
