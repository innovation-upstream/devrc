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

MUTATION RESULTS (each control verified genuinely non-empty first):

  Round 1 — `git checkout origin/main -- browser` (21 ins / 82 del):
    14 RED (regression coverage), 5 green (labelled `INVARIANT GUARD` below —
    back-compat the fix must not take away; NOT evidence the fix works).

  Round 2 — `git checkout d15e211 -- browser browser-agent` (66/113 and 3/13):
    14 RED here + 2 in test_browser_agent.py. The `[js-...]` cases of the two
    empty-first-positional parametrizations stay green because `js` was already
    fixed in round 1 — for THIS round they are invariant guards, and they are the
    control that proves the parametrization is pointed at the right defect.

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


@pytest.fixture
def wake_fails(tmp_path):
    """A stub bridge where the PRIMARY op succeeds and the `wake` op FAILS.

    `responder(op)` decides the wake failure mode per test: an op-level
    `ok:false` envelope, or a raw HTTP status (429 / 504). Everything else gets a
    normal success envelope carrying a tabId, so the test can prove the primary
    result survived.
    """
    mode = {"wake": ("op_error", "wake_with_frame_unsupported: un-throttling is "
                                 "tab-level, not per-frame")}

    class _H(BaseHTTPRequestHandler):
        bodies: list = []

        def _reply(self, code, payload):
            raw = json.dumps(payload).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n).decode())
            self.bodies.append(body)
            op = body.get("op")
            if op == "wake":
                kind, detail = mode["wake"]
                if kind == "op_error":
                    self._reply(200, {"ok": True, "result": {
                        "id": "w", "ok": False, "error": detail}})
                    return
                self._reply(int(kind), {"ok": False, "error": detail})
                return
            self._reply(200, {"ok": True, "result": {
                "id": "c", "ok": True,
                "data": {"tabId": 4242, "url": "https://a.test"}}})

        def log_message(self, *a):
            pass

    _H.bodies = []
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    tokfile = tmp_path / "token"
    tokfile.write_text("wake-fail-token\n")
    env = dict(os.environ)
    env.update({"BROWSER_BRIDGE_HOST": "127.0.0.1",
                "BROWSER_BRIDGE_PORT": str(srv.server_address[1]),
                "BROWSER_BRIDGE_TOKEN_FILE": str(tokfile),
                "CLAUDE_CODE_SESSION_ID": "pytest-wake-fail",
                "HOME": str(tmp_path)})

    class _B:
        bodies = _H.bodies

        @staticmethod
        def set_wake_failure(kind, detail):
            mode["wake"] = (kind, detail)

        @staticmethod
        def run(*args):
            return subprocess.run(["bash", str(CLI), *args], env=env,
                                  capture_output=True, text=True, timeout=60)
    try:
        yield _B
    finally:
        srv.shutdown(); srv.server_close()


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
    """INVARIANT GUARD (green pre-fix). The load-bearing half of wake_fields: a plain read must go over the wire
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
    """INVARIANT GUARD (green pre-fix). `browser js ''` kept its old meaning — the parser tracks "a positional was
    SEEN", not "the positional is non-empty"."""
    assert _body(bridge, "js", "")["js"] == ""


def test_js_rejects_a_second_expression_and_an_unknown_flag(bridge):
    """INVARIANT GUARD (green pre-fix) — back-compat on the rejection paths."""
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
    """INVARIANT GUARD (green pre-fix) — `text` was ALREADY order-free; this pins
    that the js/eval fix did not regress the op it was modelled on."""
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
    """INVARIANT GUARD (green pre-fix) — these take NO flags, so their positional
    stays bare and a `-`-leading value must keep working with no `--`."""
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
SINGLE_POSITIONAL = [
    # subcommand, args producing "empty first positional then a second one",
    # the fragment of the duplicate-rejection message
    ("js", ["--", "", "SECOND"], "exactly one JS argument"),
    ("text", ["--", "", "#second"], "at most one selector"),
    ("type", ["--", "", "SECOND"], "one text arg"),
    ("key", ["--", "", "Enter"], "one key name"),
    ("emulate", ["--", "", "iphone-15"], "at most one preset name"),
    ("screenshot", ["--", "", "/tmp/x.png"], "at most one path"),
]


@pytest.mark.parametrize("sub,args,needle", SINGLE_POSITIONAL)
def test_an_empty_first_positional_is_not_silently_overwritten(bridge, sub, args,
                                                               needle):
    """REGRESSION (audit finding on PR #251). The five sibling `--` loops used the
    OLD `[ -z "$var" ] || die` guard, which is FALSY for an empty positional — so a
    second one silently OVERWROTE the first and went on the wire with exit 0:

        browser type -- '' SECOND   → rc 0, {"op":"type","text":"SECOND"}
        browser text -- '' '#second' → rc 0, {"selector":"#second"}

    Wrong value on the wire, exit 0 — the exact class this PR exists to remove.
    All six now route through the single pos_add/pos_rest choke point, which
    tracks "a positional was SEEN" instead."""
    before = len(bridge.bodies)
    cp = bridge.run(sub, *args)
    assert cp.returncode != 0, f"{sub} {args}: expected a usage error"
    assert needle in cp.stderr, cp.stderr
    assert len(bridge.bodies) == before, "nothing may reach the wire on a usage error"


@pytest.mark.parametrize("sub,args,needle", SINGLE_POSITIONAL)
def test_the_same_duplicate_is_rejected_without_the_separator(bridge, sub, args,
                                                              needle):
    """The `*)` arm and the `--` arm must agree — they are the same choke point.
    Same inputs, minus the `--`."""
    before = len(bridge.bodies)
    cp = bridge.run(sub, *args[1:])
    assert cp.returncode != 0, f"{sub} {args[1:]}: expected a usage error"
    assert needle in cp.stderr, cp.stderr
    assert len(bridge.bodies) == before


def test_screenshot_rejects_an_explicitly_empty_path(bridge):
    """`screenshot` alone means "write a temp file"; `screenshot -- ''` is a
    mistake, and POS_SEEN is what distinguishes them."""
    cp = bridge.run("screenshot", "--", "")
    assert cp.returncode != 0 and "must not be empty" in cp.stderr


# --------------------------------------------------------------------------- #
# S1 — `--wake[=MS]` on `nav` and `open` (2026-08-02 usage audit, F2)
#
# `browser nav --wake <url>` used to be a deliberate hard error ("nav takes no
# flags — put the url first"). The audit measured why that was the wrong answer:
# 13 `nav`→`wake` / `open`→`wake` adjacent pairs in Claude transcripts, and one
# opencode session that is literally `nav wake eval` ×5. `nav` lands you on a
# BACKGROUND (throttled) tab, so the wake is not optional — it was just a second
# call. The flag is now real, and (like text/html/js) order-free.
#
# `nav`/`open` send NO `wake` field on the wire — the extension honours `cmd.wake`
# only on getHtml/text/eval. `--wake` is a client-side compose: the nav/open op,
# then the existing `wake` op. So these tests assert TWO recorded bodies.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("sub", ["nav", "open"])
def test_wake_on_nav_and_open_issues_the_wake_in_one_invocation(bridge, sub):
    """THE REGRESSION. Pre-change `nav --wake <url>` exited 1 with "takes no flags"
    and `open --wake <url>` exited 1 with "at most one url"; neither could reach a
    `wake` at all without a second command."""
    before = len(bridge.bodies)
    cp = bridge.run(sub, "https://a.test", "--wake")
    assert cp.returncode == 0, cp.stderr
    sent = bridge.bodies[before:]
    assert [b["op"] for b in sent] == [sub, "wake"], sent
    assert sent[0]["url"] == "https://a.test"
    # The primary op's body is UNCHANGED — no `wake` field is put on the wire for
    # nav/open, so no extension build can mis-handle it.
    assert "wake" not in sent[0] and "waitMs" not in sent[0]
    assert "waitMs" not in sent[1]          # bare --wake → extension's default settle


@pytest.mark.parametrize("sub", ["nav", "open"])
def test_wake_on_nav_and_open_is_order_free_and_carries_ms(bridge, sub):
    """Flag order is free here exactly as it is for text/html/js, and `--wake=MS`
    reaches the `wake` op as waitMs."""
    before = len(bridge.bodies)
    assert bridge.run(sub, "--wake=250", "https://a.test").returncode == 0
    flags_first = bridge.bodies[before:]

    before = len(bridge.bodies)
    assert bridge.run(sub, "https://a.test", "--wake=250").returncode == 0
    flags_last = bridge.bodies[before:]

    assert flags_first == flags_last
    assert [b["op"] for b in flags_first] == [sub, "wake"]
    assert flags_first[1]["waitMs"] == 250


@pytest.mark.parametrize("sub", ["nav", "open"])
def test_no_wake_on_nav_and_open_sends_exactly_one_unchanged_command(bridge, sub):
    """INVARIANT GUARD (green pre-change). The load-bearing back-compat half: with
    no `--wake` the byte on the wire and the NUMBER of wire ops are what they were."""
    before = len(bridge.bodies)
    cp = bridge.run(sub, "https://a.test")
    assert cp.returncode == 0, cp.stderr
    sent = bridge.bodies[before:]
    assert len(sent) == 1, sent
    assert sent[0]["op"] == sub and sent[0]["url"] == "https://a.test"
    assert "wake" not in sent[0]


@pytest.mark.parametrize("sub", ["nav", "open"])
def test_nav_and_open_validate_wake_ms_at_parse_time(bridge, sub):
    """`--wake=MS` is validated in the FLAG LOOP, before anything is dispatched —
    the same property wake_fields' comment protects for the read ops. A parse-time
    rejection must leave the wire completely untouched (not "nav happened, then the
    wake was rejected")."""
    before = len(bridge.bodies)
    cp = bridge.run(sub, "https://a.test", "--wake=abc")
    assert cp.returncode != 0
    assert "--wake=MS must be a non-negative integer" in cp.stderr
    assert len(bridge.bodies) == before, "nothing may reach the wire"


@pytest.mark.parametrize("sub", ["nav", "open"])
def test_nav_and_open_still_reject_an_unknown_flag(bridge, sub):
    """Gaining ONE flag must not turn these into "anything starting with - is fine"."""
    before = len(bridge.bodies)
    cp = bridge.run(sub, "https://a.test", "--nope")
    assert cp.returncode != 0
    assert "unknown flag" in cp.stderr
    assert len(bridge.bodies) == before


@pytest.mark.parametrize("sub", ["nav", "open"])
@pytest.mark.parametrize("kind,detail", [
    ("op_error", "wake_with_frame_unsupported: tab-level, not per-frame"),
    # `unknown_op` is the ODD ONE OUT: its CLI branch neither uses the
    # "failed in the browser:" prefix nor echoes the server body, so it was the
    # ONE failure mode that still degraded to the generic token — and it is the
    # most likely `--wake` failure in this repo (a stale loaded extension), and
    # the PERMANENT one the exit-3 contract promises you can distinguish.
    ("op_error", "unknown_op"),
    ("429", "rate_limited"),
    ("504", "timeout"),
])
def test_a_failing_wake_never_swallows_the_primary_result(wake_fails, sub,
                                                          kind, detail):
    """REGRESSION (adversarial audit of #278). The primary op ALREADY HAPPENED —
    the tab really did navigate — so a failing `--wake` must not discard it.

    The first cut used `|| exit 1`, which exited rc 1 with EMPTY stdout on all
    three failure modes below. `T=$(browser nav --wake "$url")` then yielded an
    empty T for a tab that exists, and only the op-level branch even mentioned
    `wake`, so you could not tell WHICH half failed.

    Pinned: the primary JSON survives with its tabId, the wake failure is attached
    where a successful wake would have been reported, stderr says the primary
    SUCCEEDED, and the exit code is the distinct 3 rather than the generic 1.
    """
    wake_fails.set_wake_failure(kind, detail)
    r = wake_fails.run(sub, "https://a.test", "--wake")

    out = json.loads(r.stdout)
    assert out["result"]["data"]["tabId"] == 4242, (
        "the primary result was swallowed — this is the whole bug")
    assert out["result"]["data"]["wake"]["ok"] is False
    assert out["result"]["ok"] is True, "the PRIMARY op did not fail"

    # 🔴 THE REAL CAUSE, not a placeholder. This first shipped as the constant
    # "wake_failed_after_nav", which cannot distinguish a TRANSIENT `rate_limited`
    # (retry) from a PERMANENT `wake_with_frame_unsupported` (do not) — and a test
    # that only asserted `ok is False` structurally could not catch that.
    expected = detail if kind == "op_error" else \
        {"429": "rate_limited", "504": "timeout"}[kind]
    assert out["result"]["data"]["wake"]["error"] == expected, (
        out["result"]["data"]["wake"])

    assert "SUCCEEDED" in r.stderr and sub in r.stderr
    assert "only the --wake follow-up failed" in r.stderr
    assert r.returncode == 3, (
        f"expected the distinct 'primary ok, wake failed' code 3, got "
        f"{r.returncode}")

    # Both ops really were attempted, in order.
    assert [b["op"] for b in wake_fails.bodies] == [sub, "wake"]


@pytest.mark.parametrize("sub", ["nav", "open"])
def test_a_SUCCESSFUL_wake_still_exits_zero(wake_fails, sub, bridge):
    """NEGATIVE CONTROL for the exit code above: without it, `return 3` could be
    unconditional and every test above would still pass.

    Also pins the SUCCESS SHAPE, which used to be asymmetric with the failure
    shape. The extension's wake payload carries no `ok` key, so writing it raw
    made `if (data.wake.ok)` falsy on every successful wake and forced callers
    into the undiscoverable negative `wake.ok !== false`.
    """
    r = bridge.run(sub, "https://a.test", "--wake")
    assert r.returncode == 0, r.stderr
    assert "SUCCEEDED" not in r.stderr
    wake = json.loads(r.stdout)["result"]["data"]["wake"]
    assert wake["ok"] is True, (
        "success and failure must be shape-symmetric — `ok` present in both")
    assert "error" not in wake


@pytest.mark.parametrize("sub", ["nav", "open"])
def test_frame_plus_wake_is_refused_before_anything_reaches_the_wire(bridge, sub):
    """A half-state this feature CREATED. `--frame` is a global flag that cmd_op
    splices into every op; `nav`/`open` ignore it, but the standalone `wake` op is
    refused by the extension's assertWakeNotFramed. So the composed form would
    navigate and THEN fail the wake — half applied.

    Before `--wake` existed on nav/open this was a hard parse error ("nav takes no
    flags"), so the path did not exist; the feature is what opened it. Refuse it at
    PARSE time, and prove NOTHING was sent — that is the difference between a
    rejection and a half-applied command.
    """
    before = len(bridge.bodies)
    cp = bridge.run("--frame", "child.test", sub, "https://a.test", "--wake")
    assert cp.returncode != 0
    assert "--frame and --wake cannot be combined" in cp.stderr
    assert "nothing has been navigated or opened" in cp.stderr
    assert len(bridge.bodies) == before, (
        "a refusal must not put the primary op on the wire")


@pytest.mark.parametrize("sub", ["nav", "open"])
def test_frame_WITHOUT_wake_and_wake_WITHOUT_frame_both_still_work(bridge, sub):
    """NEGATIVE CONTROL for the refusal: it must fire on the CONJUNCTION only.
    A blanket "reject --frame on nav" or "reject --wake when FRAME is set anywhere"
    would pass the test above while breaking two legitimate forms."""
    before = len(bridge.bodies)
    assert bridge.run("--frame", "child.test", sub, "https://a.test").returncode == 0
    assert bridge.run(sub, "https://a.test", "--wake").returncode == 0
    sent = bridge.bodies[before:]
    assert [b["op"] for b in sent] == [sub, sub, "wake"]
    assert sent[0]["frame"] == "child.test"      # --frame alone still threads


@pytest.fixture
def gateway_504(tmp_path):
    """A stub bridge whose /cmd always answers 504 `timeout` — the shape the CLI
    renders its op-aware timeout guidance from."""
    class _H(BaseHTTPRequestHandler):
        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            self.rfile.read(n)
            raw = json.dumps({"ok": False, "error": "timeout"}).encode()
            self.send_response(504)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), _H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    tokfile = tmp_path / "token"
    tokfile.write_text("t504\n")
    env = dict(os.environ)
    env.update({"BROWSER_BRIDGE_HOST": "127.0.0.1",
                "BROWSER_BRIDGE_PORT": str(srv.server_address[1]),
                "BROWSER_BRIDGE_TOKEN_FILE": str(tokfile),
                "CLAUDE_CODE_SESSION_ID": "pytest-504",
                "HOME": str(tmp_path)})

    class _B:
        @staticmethod
        def run(*args):
            return subprocess.run(["bash", str(CLI), *args], env=env,
                                  capture_output=True, text=True, timeout=60)
    try:
        yield _B
    finally:
        srv.shutdown(); srv.server_close()


def test_a_ping_timeout_does_NOT_steer_the_operator_into_restarting_brave(
        gateway_504):
    """REGRESSION. `ping` runs on a deadline the generic 504 wording does not
    describe, and that wording — "is Brave focused / responsive?" — points at a
    dead extension, whose documented remedy is a FULL Brave restart of the
    operator's LIVE session. A ping can time out with the profile perfectly
    healthy (a wedge is one cause; a queue of concurrent ops is another).

    This message is the entire mitigation for the disclosed residual, and NOTHING
    pinned it: deleting the whole `if [ "$op" = "ping" ]` block left both suites
    fully green.
    """
    r = gateway_504.run("ping")
    assert r.returncode != 0
    err = r.stderr
    assert "do NOT restart Brave" in err, err
    assert "queued ahead of it" in err, err
    assert "OTHERWISE IDLE profile is the real" in err, err
    # ...and it must NOT fall through to the generic wording, which is the bug.
    assert "is Brave focused / responsive?" not in err, err


def test_a_NON_ping_timeout_keeps_the_generic_wording(gateway_504):
    """NEGATIVE CONTROL: without it the ping guidance could be printed for EVERY
    op — which would be wrong (a `nav` timeout really does suggest an unresponsive
    tab) and would make the test above pass for the wrong reason."""
    r = gateway_504.run("tabs")
    assert r.returncode != 0
    err = r.stderr
    assert "is Brave focused / responsive?" in err, err
    assert "do NOT restart Brave" not in err, err
    assert "queued ahead of it" not in err, err


def test_open_with_no_url_still_means_about_blank(bridge):
    """INVARIANT GUARD (green pre-change). `open` alone must keep sending a bare
    `{"op":"open"}` — POS_SEEN, not a non-empty test, is what distinguishes it from
    `open -- ''`."""
    b = _body(bridge, "open")
    assert b["op"] == "open" and "url" not in b


def test_help_does_not_require_a_token_file(tmp_path):
    """The hermetic nix gate has no ~/.config, so a token-gated --help FAILED it
    (measured at 69f5334). --help touches neither the server nor the token."""
    env = dict(os.environ,
               BROWSER_BRIDGE_TOKEN_FILE=str(tmp_path / "definitely-absent"))
    for args in (["--help"], ["-h"], ["help"], []):
        cp = subprocess.run(["bash", str(CLI), *args], env=env,
                            capture_output=True, text=True, timeout=60)
        assert cp.returncode == 0, f"{args}: {cp.stderr}"
        assert "FLAG ORDER / END OF FLAGS" in cp.stdout, args
        assert "token file" not in cp.stderr, args


def test_no_prose_line_looks_like_a_wire_op_dispatch():
    """🔴 REGRESSION, and it was invisible on this branch alone.

    The surface-parity gate (arriving via #277) harvests wire ops from this file
    with `findall(<dispatch-helper> + whitespace + identifier)` and skips only
    lines whose first non-space char is `#`. A Python DOCSTRING line inside a
    `python3 -c` block is not one — so the phrase "<helper> stderr" in a docstring
    was harvested as a phantom wire op named `stderr` and reddened the MERGED tree
    (1 failed / 424 passed) while both branches were green alone.

    This is the same harvest, run locally, so the trap is caught on THIS side too
    rather than depending on the other PR hardening its parser. Defence in depth is
    the point: either fix alone closes it, and either alone can regress.

    HARNESS NEGATIVE CONTROL is inline: the harvester must find a phantom in a
    string that contains one, or its silence here means nothing.
    """
    import re
    helper = "cmd" + "_op"          # not written as one token; see the docstring

    def harvest(text):
        out = set()
        for ln in text.splitlines():
            if ln.lstrip().startswith("#"):
                continue
            out.update(re.findall(r"\b%s\s+([A-Za-z]+)" % helper, ln))
        return out

    # NEGATIVE CONTROL: a docstring-style line with the offending shape MUST be
    # harvested — otherwise a green verdict below is a fact about the harvester.
    assert harvest('    """The cause, from %s stderr it emits."""' % helper) == \
        {"stderr"}
    assert harvest("# %s stderr in a real comment is skipped" % helper) == set()

    src = CLI.read_text(encoding="utf-8")
    real = set(re.search(r'^SUBCOMMANDS="([^"]+)"', src, re.M).group(1).split())
    # The wire names the CLI dispatches that are not spelled like their subcommand.
    real |= {"getHtml", "eval"}
    phantom = harvest(src) - real
    assert phantom == set(), (
        f"prose in the CLI reads as a wire-op dispatch: {sorted(phantom)} — "
        "reword it (name the helper in backticks, never followed by a bare word)")


def test_help_prints_the_HEADER_block_only_not_the_whole_files_comments(tmp_path):
    """REGRESSION. `--help` was `grep -E '^#( |$)' "$0"` — EVERY column-0 comment
    in the file, so it shipped 25,440 bytes of implementation commentary against a
    ~15 KB header, and it grew with every internal comment anyone wrote.

    Pinned three ways, because a size bound alone is a weak claim:
      * the header's own landmarks are present (it did not over-trim);
      * comments that live BELOW the first line of code are absent;
      * `--help` stops at the first non-comment line.
    """
    env = dict(os.environ,
               BROWSER_BRIDGE_TOKEN_FILE=str(tmp_path / "absent"))
    cp = subprocess.run(["bash", str(CLI), "--help"], env=env,
                        capture_output=True, text=True, timeout=60)
    assert cp.returncode == 0, cp.stderr

    # Landmarks from the header block — the help must still be the real help.
    for needle in ("FLAG ORDER / END OF FLAGS", "browser nav <url> [--wake[=MS]]",
                   "Global flags (before the subcommand)"):
        assert needle in cp.stdout, needle

    # Implementation commentary lives after `set -uo pipefail`, the first line of
    # code. These strings are all in the file and must NOT be in --help.
    src = CLI.read_text(encoding="utf-8")
    for needle in ("wake_fields WAKE WAITMS", "cmd_op OP [EXTRA_JSON_FIELDS]",
                   "_wake_flag ARG", "strict=validate"):
        assert needle in src, f"{needle} vanished from the source; retarget this test"
        assert needle not in cp.stdout, f"--help leaked an internal comment: {needle}"

    # And it genuinely stops at the code: the header ends right before this line.
    header = src.split("\nset -uo pipefail", 1)[0]
    assert len(cp.stdout) < len(header), "help is larger than the header block"

    # 🔴 PIN THE LAST LINE — an upper bound plus landmarks is NOT enough. `awk`
    # exits at the first non-`#` line, so a blank line inserted anywhere in the
    # final stretch of the header TRUNCATES --help from that point on, and every
    # assertion above still passes (the landmarks sit further up, and a shorter
    # output only makes the length bound more true). Deriving the expectation from
    # the source rather than hard-coding it keeps this honest as the header grows.
    expected_last = [ln[2:] if ln.startswith("# ") else ln[1:]
                     for ln in header.splitlines()[1:] if ln.startswith("#")][-1]
    assert cp.stdout.splitlines()[-1] == expected_last, (
        "--help was truncated before the end of the header block (a blank/non-`#` "
        "line in the header stops the awk)")
    # The whole header, line for line — the strongest form of the same claim.
    expected_all = [ln[2:] if ln.startswith("# ") else ln[1:]
                    for ln in header.splitlines()[1:]]
    assert cp.stdout.splitlines() == expected_all


def test_an_unknown_subcommand_is_reported_as_such_without_a_token(tmp_path):
    """A typo must not be reported as a missing token. This is what still failed
    the hermetic gate after the --help fix: `browser bogus-op` in a sandbox with
    no ~/.config died with "token file not found/readable"."""
    env = dict(os.environ,
               BROWSER_BRIDGE_TOKEN_FILE=str(tmp_path / "absent"))
    cp = subprocess.run(["bash", str(CLI), "bogus-op"], env=env,
                        capture_output=True, text=True, timeout=60)
    assert cp.returncode != 0
    assert "unknown subcommand: bogus-op" in cp.stderr
    assert "token file" not in cp.stderr
    assert " js " in cp.stderr and " eval " in cp.stderr


def test_a_glob_subcommand_cannot_pattern_match_past_the_validation(tmp_path):
    """The validation compares literally rather than via `case`, so `*` is an
    unknown subcommand and not a wildcard that matches every known one."""
    env = dict(os.environ,
               BROWSER_BRIDGE_TOKEN_FILE=str(tmp_path / "absent"))
    for bogus in ("*", "?ealth", "[hw]ealth"):
        cp = subprocess.run(["bash", str(CLI), bogus], env=env,
                            capture_output=True, text=True, timeout=60)
        assert cp.returncode != 0, bogus
        assert f"unknown subcommand: {bogus}" in cp.stderr, cp.stderr


def test_a_real_subcommand_still_requires_a_token(tmp_path):
    """The validation must not have moved the token check off the real path."""
    env = dict(os.environ,
               BROWSER_BRIDGE_TOKEN_FILE=str(tmp_path / "absent"))
    cp = subprocess.run(["bash", str(CLI), "health"], env=env,
                        capture_output=True, text=True, timeout=60)
    assert cp.returncode != 0 and "token file not found" in cp.stderr


def test_help_documents_the_end_of_flags_separator_generally(tmp_path):
    """`--` semantics changed for six subcommands, so the doc entry must not live
    only under `js`."""
    env = dict(os.environ,
               BROWSER_BRIDGE_TOKEN_FILE=str(tmp_path / "absent"))
    out = subprocess.run(["bash", str(CLI), "--help"], env=env,
                         capture_output=True, text=True, timeout=60).stdout
    head = out.split("FLAG ORDER / END OF FLAGS")[1]
    for sub in ("js", "text", "type", "screenshot"):
        assert sub in head, f"{sub} missing from the end-of-flags entry"


def test_emulate_preset_and_flags_are_order_independent(bridge):
    a = _body(bridge, "emulate", "--color-scheme", "dark", "iphone-15")
    b = _body(bridge, "emulate", "iphone-15", "--color-scheme", "dark")
    c = _body(bridge, "emulate", "--color-scheme", "dark", "--", "iphone-15")
    assert a == b == c
    assert a["device"] == "iphone-15" and a["colorScheme"] == "dark"


# --------------------------------------------------------------------------- #
# `emulate --reset --recreate` — the #319 workaround (CLI-side, no extension).
#
# WHY IT EXISTS. Measured 2026-08-03 (laptop, extension 0.7.2, fresh-tab control):
#
#     fresh tab, never emulated   innerWidth 1124   (control — read path works)
#     after `emulate iphone-15`   innerWidth  393
#     after `emulate --reset`     innerWidth  393   ← NOT restored
#     after `--reset` + re-nav    innerWidth  393   ← still NOT restored
#
# and the reset DID report `cleared: [Emulation.clearDeviceMetricsOverride,
# Emulation.setTouchEmulationEnabled, Emulation.setUserAgentOverride]`. The
# viewport size is stuck; closing the tab is the only known remedy. `--recreate`
# automates the replacement. The mechanism is NOT established — these tests pin
# the CLI's ORCHESTRATION, not a browser behaviour.
#
# What must hold:
#   1. the op ORDER is emulate(reset) → release → open(url) → close(old tab),
#      i.e. the replacement is opened BEFORE the stuck tab is closed, so no
#      failure path can leave the operator with no tab;
#   2. `release` is sent at all — `open` is idempotent server-side and would hand
#      back the SAME stuck tab to a session that still owns one;
#   3. the NEW tab id is reported (it changes, and later ops route to it);
#   4. plain `--reset` NEVER swaps tab ids (one request, nothing else);
#   5. `--recreate` alone is refused;
#   6. a non-http(s) url is refused without closing anything;
#   7. an `open` failure leaves the original tab open and names it.
# --------------------------------------------------------------------------- #
OLD_TAB, NEW_TAB = 4242, 9999
PAGE_URL = "https://example.com/a"


@pytest.fixture
def recreate_bridge(tmp_path):
    """An OP-AWARE stub: `emulate` answers a reset envelope carrying the stuck
    tab's id+url, `open` answers a NEW tab id. `fail_op` makes one op fail."""
    cfg = {"fail_op": None, "reset_url": PAGE_URL}

    class _H(BaseHTTPRequestHandler):
        bodies: list = []

        def _reply(self, code, payload):
            raw = json.dumps(payload).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n).decode())
            body["__session"] = self.headers.get("X-Session-Id")
            self.bodies.append(body)
            op = body.get("op")
            if op == cfg["fail_op"]:
                self._reply(200, {"ok": True, "result": {
                    "id": "x", "ok": False, "error": f"{op}_refused_by_stub"}})
                return
            # 🔴 THE REAL WIRE SHAPE, read off live Brave 2026-08-03:
            # {"ok":true,"result":{"id":…,"ok":true,"data":{tabId,url,…}}}.
            # An earlier version of this stub put tabId at result level; the tests
            # passed and the CLI could not find a tabId against real Brave. The
            # nesting is load-bearing — do not flatten it.
            if op == "emulate":
                self._reply(200, {"ok": True, "result": {
                    "id": "e", "ok": True, "data": {
                        "tabId": OLD_TAB, "url": cfg["reset_url"],
                        "reset": True, "cleared": [],
                        "wasEmulating": {"device": "iphone-15"}}}})
                return
            if op == "open":
                self._reply(200, {"ok": True, "result": {
                    "id": "o", "ok": True, "data": {
                        "tabId": NEW_TAB, "url": body.get("url")}}})
                return
            self._reply(200, {"ok": True, "result": {
                "id": "c", "ok": True, "data": {"closed": True}}})

        def do_GET(self):
            self._reply(200, {"ok": True, "instances": []})

        def log_message(self, *a):
            pass

    class _Handler(_H):
        bodies = []

    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    tokfile = tmp_path / "token"
    tokfile.write_text("test-token-abc123\n")
    env = dict(os.environ)
    env.update({
        "BROWSER_BRIDGE_HOST": "127.0.0.1",
        "BROWSER_BRIDGE_PORT": str(srv.server_address[1]),
        "BROWSER_BRIDGE_TOKEN_FILE": str(tokfile),
        "CLAUDE_CODE_SESSION_ID": "pytest-recreate",
        "HOME": str(tmp_path),
    })

    _cfg = cfg

    class _Bridge:
        bodies = _Handler.bodies
        cfg = _cfg

        @staticmethod
        def run(*args):
            return subprocess.run(["bash", str(CLI), *args], env=env,
                                  capture_output=True, text=True, timeout=60)

    try:
        yield _Bridge
    finally:
        srv.shutdown()
        srv.server_close()


def test_recreate_requires_reset(recreate_bridge):
    """`--recreate` is a modifier of `--reset`, never a standalone verb."""
    cp = recreate_bridge.run("emulate", "--recreate")
    assert cp.returncode != 0, cp.stdout
    assert "--recreate requires --reset" in cp.stderr, cp.stderr
    assert recreate_bridge.bodies == [], \
        "a refused --recreate must not reach the bridge at all"


def test_recreate_op_order_opens_before_it_closes(recreate_bridge):
    """emulate(reset) -> release -> open(url) -> close(OLD tab), in that order.

    The order IS the safety property: the replacement exists before the stuck tab
    is closed, so no failure path leaves the operator with no tab. And `release`
    must be there — `open` is idempotent server-side and would otherwise hand the
    SAME stuck tab back to a session that still owns one.
    """
    cp = recreate_bridge.run("emulate", "--reset", "--recreate")
    assert cp.returncode == 0, cp.stderr
    ops = [b["op"] for b in recreate_bridge.bodies]
    assert ops == ["emulate", "release", "open", "close"], ops
    assert recreate_bridge.bodies[0].get("reset") is True
    assert recreate_bridge.bodies[2].get("url") == PAGE_URL, \
        "the replacement tab must be opened at the stuck tab's own url"
    assert recreate_bridge.bodies[3].get("tab") == OLD_TAB, \
        "close must target the OLD tab explicitly, never 'whatever is owned'"


def test_recreate_reports_the_new_tab_id(recreate_bridge):
    """The tab id CHANGES; an operator who is not told that will drive the wrong
    tab with an explicit --tab."""
    cp = recreate_bridge.run("emulate", "--reset", "--recreate")
    assert cp.returncode == 0, cp.stderr
    out = json.loads(cp.stdout)
    assert out["recreated"] is True
    assert out["oldTabId"] == OLD_TAB and out["newTabId"] == NEW_TAB
    assert out["url"] == PAGE_URL and out["closedOldTab"] is True
    assert str(NEW_TAB) in out["note"] and str(OLD_TAB) in out["note"], \
        "the note must name BOTH ids so the change is unmissable"


def test_plain_reset_never_swaps_tab_ids(recreate_bridge):
    """INVARIANT GUARD (not regression coverage): `--reset` without `--recreate`
    keeps its old behaviour — one request, no release/open/close."""
    cp = recreate_bridge.run("emulate", "--reset")
    assert cp.returncode == 0, cp.stderr
    assert [b["op"] for b in recreate_bridge.bodies] == ["emulate"]


def test_recreate_refuses_a_non_http_url_and_closes_nothing(recreate_bridge):
    """`open` cannot meaningfully rebuild an about:/chrome:/file: tab. Refusing is
    the safe end state: the operator keeps the tab and is told how to close it."""
    recreate_bridge.cfg["reset_url"] = "about:blank"
    cp = recreate_bridge.run("emulate", "--reset", "--recreate")
    assert cp.returncode != 0, cp.stdout
    assert "non-http(s) url" in cp.stderr, cp.stderr
    assert f"browser --tab {OLD_TAB} close" in cp.stderr, cp.stderr
    assert [b["op"] for b in recreate_bridge.bodies] == ["emulate"], \
        "nothing may be released, opened or closed on the refusal path"


def test_recreate_open_failure_leaves_the_original_tab_open(recreate_bridge):
    """The failure that matters: if the replacement cannot be opened, the stuck
    tab must still be there and must be NAMED."""
    recreate_bridge.cfg["fail_op"] = "open"
    cp = recreate_bridge.run("emulate", "--reset", "--recreate")
    assert cp.returncode != 0, cp.stdout
    assert "close" not in [b["op"] for b in recreate_bridge.bodies], \
        "a tab must never be closed once the replacement has failed"
    assert f"browser --tab {OLD_TAB} close" in cp.stderr, cp.stderr
    assert "STILL OPEN" in cp.stderr, cp.stderr


def test_recreate_keeps_ownership_of_the_new_tab(recreate_bridge):
    """The close must NOT run under this session's own id.

    server.py drops a session's tab ownership on `close` UNCONDITIONALLY — it does
    not check that the closed tab is the owned one. So closing the OLD tab under
    our own session id evicts the mapping `open` just made for the NEW tab, and the
    next bare op silently falls back to the ACTIVE tab. MEASURED live 2026-08-03:
    a post-recreate `browser js` returned "Cannot access a chrome:// URL" — it was
    reading the operator's foreground tab. The close therefore carries a THROWAWAY
    X-Session-Id, so the server pops that mapping instead of ours.
    """
    cp = recreate_bridge.run("emulate", "--reset", "--recreate")
    assert cp.returncode == 0, cp.stderr
    by_op = {b["op"]: b["__session"] for b in recreate_bridge.bodies}
    assert by_op["emulate"] == by_op["release"] == by_op["open"], by_op
    assert by_op["close"] != by_op["open"], (
        "the close must use a throwaway session id, or it evicts the ownership "
        "of the tab `open` just created: " + repr(by_op))
