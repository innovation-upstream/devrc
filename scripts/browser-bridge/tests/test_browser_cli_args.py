"""Argument-parsing contract for the `browser` CLI (scripts/browser-bridge/browser).

Fully HEADLESS: no Brave, no extension, no real bridge. A tiny loopback stub HTTP
server stands in for server.py — it records the exact JSON body the CLI POSTs to
/cmd and answers with a canned success envelope. So these tests assert on the
WIRE SHAPE the parser produced, which is the thing that was wrong.

WHY THIS FILE EXISTS (found live on real Brave, 2026-07-31, after PR #250):

    $ browser js --wake 'innerWidth+"x"+innerHeight'
    browser: browser js takes exactly one JS argument (got extra: innerWidth+...)

`js`/`eval` grabbed its positional as `$1` BEFORE its flag loop ran, so a leading
`--wake` was swallowed AS the JS expression and the real expression was then
rejected as "extra". Only the trailing-flag order worked. That is not just a
usability wart: PR #250's own documented emulation-verification probe (and
reference/emulation.md) is written flags-first, so an agent following the docs got
a parse error that reads as the FEATURE being broken.

The invariant pinned here: for every subcommand that accepts both flags and a
positional, the two orders must produce a BYTE-IDENTICAL request body.

Run: nix-shell -p python312Packages.pytest curl --run \\
       "pytest scripts/browser-bridge/tests/test_browser_cli_args.py"
"""
import json
import os
import shutil
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

BB = Path(__file__).resolve().parent.parent          # scripts/browser-bridge
CLI = BB / "browser"

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None or shutil.which("curl") is None,
    reason="the browser CLI is bash and talks to the bridge with curl")


# --------------------------------------------------------------------------- #
# Stub bridge — records every /cmd body, answers a canned success envelope.
# --------------------------------------------------------------------------- #
class _Recorder(BaseHTTPRequestHandler):
    bodies: list = []                                # class-level, per-server reset

    def _reply(self, code, payload):
        raw = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n).decode()
        try:
            self.bodies.append(json.loads(raw))
        except ValueError:
            self.bodies.append({"__unparseable__": raw})
        # A generic success envelope. `data` deliberately carries no `hidden`
        # flag, so the CLI's stderr hidden-warning never fires here.
        self._reply(200, {"ok": True, "result": {"id": "c", "ok": True,
                                                 "data": {"value": 1}}})

    def do_GET(self):                                # /health etc., unused here
        self._reply(200, {"ok": True, "instances": []})

    def log_message(self, *a):                       # keep pytest output clean
        pass


@pytest.fixture
def bridge(tmp_path):
    """A running stub bridge + the env that points the CLI at it.

    Yields an object with `.run(*args)` → CompletedProcess and `.bodies` (the
    /cmd request bodies recorded so far, in order).
    """
    class _Handler(_Recorder):
        bodies = []                                  # fresh list per test

    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()

    tokfile = tmp_path / "token"
    tokfile.write_text("test-token-abc123\n")

    env = dict(os.environ)
    env.update({
        "BROWSER_BRIDGE_HOST": "127.0.0.1",
        "BROWSER_BRIDGE_PORT": str(srv.server_address[1]),
        "BROWSER_BRIDGE_TOKEN_FILE": str(tokfile),
        # Pin the session id so it can never fall back to the PPID-cached token
        # (which would write into XDG_RUNTIME_DIR from a test).
        "CLAUDE_CODE_SESSION_ID": "pytest-cli-args",
        # `pretty` uses jq when present; not having it just means raw JSON.
        "HOME": str(tmp_path),
    })

    class _Bridge:
        bodies = _Handler.bodies

        @staticmethod
        def run(*args):
            return subprocess.run(["bash", str(CLI), *args], env=env,
                                  capture_output=True, text=True, timeout=60)

    try:
        yield _Bridge
    finally:
        srv.shutdown()
        srv.server_close()


def _body(bridge, *args):
    """Run the CLI, assert it succeeded, return the single recorded /cmd body."""
    before = len(bridge.bodies)
    cp = bridge.run(*args)
    assert cp.returncode == 0, f"args={args!r} rc={cp.returncode} err={cp.stderr}"
    new = bridge.bodies[before:]
    assert len(new) == 1, f"args={args!r} produced {len(new)} /cmd requests"
    return new[0]


# --------------------------------------------------------------------------- #
# Defect 1 — flag order must not change the command (js / eval)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("sub", ["js", "eval"])
def test_wake_before_and_after_the_js_expression_are_the_same_command(bridge, sub):
    """THE REGRESSION. Pre-fix, the flags-first form exited 1 with
    "takes exactly one JS argument (got extra: ...)" and never reached the wire."""
    expr = 'innerWidth+"x"+innerHeight'
    flags_first = _body(bridge, sub, "--wake", expr)
    flags_last = _body(bridge, sub, expr, "--wake")
    assert flags_first == flags_last
    # Pin the literal contract, not just the equality (a parser that dropped BOTH
    # the expression and the flag would also make the two sides equal).
    assert flags_first["op"] == "eval", "the wire op is `eval` for both spellings"
    assert flags_first["js"] == expr
    assert flags_first["wake"] is True


@pytest.mark.parametrize("sub", ["js", "eval"])
def test_wake_with_ms_is_order_independent(bridge, sub):
    a = _body(bridge, sub, "--wake=250", "1+1")
    b = _body(bridge, sub, "1+1", "--wake=250")
    assert a == b
    assert a["js"] == "1+1" and a["wake"] is True and a["waitMs"] == 250


def test_js_without_wake_sends_no_wake_field(bridge):
    """The load-bearing half of wake_fields: a plain read must go over the wire
    byte-identically to before, so the extension keeps the light no-banner path."""
    b = _body(bridge, "js", "document.title")
    assert "wake" not in b and "waitMs" not in b


def test_js_expression_beginning_with_a_dash_needs_the_end_of_flags_separator(bridge):
    """A positional that legitimately starts with `-` is still reachable — via `--`,
    which is the documented escape hatch (and is NOT silently dropped)."""
    b = _body(bridge, "js", "--", "-1+2")
    assert b["js"] == "-1+2"
    # ... and it composes with a flag on either side of the separator.
    assert _body(bridge, "js", "--wake", "--", "-1+2") == \
        {**b, "wake": True}
    # Without `--` it is rejected as an unknown flag rather than silently guessed.
    cp = bridge.run("js", "-1+2")
    assert cp.returncode != 0
    assert "unknown flag" in cp.stderr


def test_js_still_accepts_an_empty_expression(bridge):
    """`browser js ''` kept its old meaning — the parser tracks "a positional was
    SEEN", not "the positional is non-empty"."""
    assert _body(bridge, "js", "")["js"] == ""


def test_js_rejects_a_second_expression_and_an_unknown_flag(bridge):
    for args in (("js", "1", "2"), ("js", "--", "1", "2")):
        cp = bridge.run(*args)
        assert cp.returncode != 0, args
        assert "exactly one JS argument" in cp.stderr, args
    cp = bridge.run("js", "1+1", "--nope")
    assert cp.returncode != 0 and "unknown flag" in cp.stderr


def test_js_with_no_expression_at_all_is_a_usage_error(bridge):
    for args in (("js",), ("js", "--wake")):
        cp = bridge.run(*args)
        assert cp.returncode != 0, args
        assert "usage: browser js" in cp.stderr, args


# --------------------------------------------------------------------------- #
# The other read op with flags + a positional: `text` (already order-free; these
# pin that it STAYS so, and cover the `--` handling that was fixed alongside).
# --------------------------------------------------------------------------- #
def test_text_selector_and_flags_are_order_independent(bridge):
    a = _body(bridge, "text", "--wake", "--max-bytes", "64", "main h1")
    b = _body(bridge, "text", "main h1", "--wake", "--max-bytes", "64")
    c = _body(bridge, "text", "--max-bytes=64", "main h1", "--wake")
    assert a == b == c
    assert a["selector"] == "main h1" and a["maxBytes"] == 64 and a["wake"] is True


def test_text_end_of_flags_separator_does_not_swallow_the_selector(bridge):
    """`--` used to `break` the loop and DROP everything after it, so
    `browser text -- '#id'` read the WHOLE page while looking like it scoped."""
    b = _body(bridge, "text", "--max-bytes", "99", "--", "#id")
    assert b["selector"] == "#id" and b["maxBytes"] == 99


# --------------------------------------------------------------------------- #
# Subcommands with a positional and NO flags: the positional stays bare (so a
# `-`-leading value keeps working), but an extra arg is no longer dropped.
# --------------------------------------------------------------------------- #
def test_nav_open_click_keep_a_bare_positional(bridge):
    assert _body(bridge, "nav", "https://example.com")["url"] == "https://example.com"
    assert _body(bridge, "open", "https://example.com")["url"] == "https://example.com"
    # A selector that starts with `-` must NOT need an escape here.
    assert _body(bridge, "click", "-foo")["selector"] == "-foo"


@pytest.mark.parametrize("args,needle", [
    (("nav", "https://a.test", "https://b.test"), "exactly one url"),
    (("open", "https://a.test", "https://b.test"), "at most one url"),
    (("click", "#a", "#b"), "exactly one css-selector"),
])
def test_extra_positionals_are_rejected_not_silently_dropped(bridge, args, needle):
    """Pre-fix these exited 0 having acted on the FIRST arg only — so a
    flag-order mistake like `browser nav --wake <url>` navigated to "--wake"."""
    before = len(bridge.bodies)
    cp = bridge.run(*args)
    assert cp.returncode != 0, args
    assert needle in cp.stderr, cp.stderr
    assert len(bridge.bodies) == before, "nothing may reach the wire on a usage error"


# --------------------------------------------------------------------------- #
# No-positional subcommands: `--` must not hide a stray argument either.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("sub", ["wake", "activate", "html"])
def test_no_positional_subcommands_reject_a_stray_arg_after_the_separator(bridge, sub):
    before = len(bridge.bodies)
    cp = bridge.run(sub, "--", "junk")
    assert cp.returncode != 0
    assert "no positional args" in cp.stderr
    assert len(bridge.bodies) == before


# --------------------------------------------------------------------------- #
# `emulate` — flags already work on either side of the preset; pin it, and pin
# that `--` feeds the preset rather than dropping it.
# --------------------------------------------------------------------------- #
def test_emulate_preset_and_flags_are_order_independent(bridge):
    a = _body(bridge, "emulate", "--color-scheme", "dark", "iphone-15")
    b = _body(bridge, "emulate", "iphone-15", "--color-scheme", "dark")
    c = _body(bridge, "emulate", "--color-scheme", "dark", "--", "iphone-15")
    assert a == b == c
    assert a["device"] == "iphone-15" and a["colorScheme"] == "dark"
