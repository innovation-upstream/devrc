"""Tests for the browser-bridge rendezvous server.

Fully HEADLESS: no Brave, no network beyond loopback. The extension is
simulated in-process by a `FakeExtension` thread that long-polls `/poll`,
"executes" the op (echo), and POSTs to `/result` — exercising the real HTTP
round-trip and the request↔reply id correlation.

Run: nix-shell -p python312Packages.pytest --run "pytest scripts/browser-bridge/tests"
"""
import json
import os
import stat
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import server as S  # noqa: E402

TOKEN = "test-token-abc123"


# --------------------------------------------------------------------------- #
# HTTP helpers
# --------------------------------------------------------------------------- #
def _req(srv, method, path, body=None, token=TOKEN, host="127.0.0.1"):
    port = srv.server_address[1]
    headers = {}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    if host is not None:
        headers["Host"] = host
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}", data=data, headers=headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        raw = e.read()
        return e.code, (json.loads(raw) if raw else None)


def _serve(cmd_timeout=5.0, poll_timeout=5.0):
    bridge = S.Bridge()
    handler = S.make_handler(bridge, TOKEN, cmd_timeout, poll_timeout)
    srv = S.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    srv.daemon_threads = True
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv, bridge


class FakeExtension(threading.Thread):
    """Simulated extension: long-polls /poll, echoes the op back via /result."""

    def __init__(self, srv, executor=None, swallow=False):
        super().__init__(daemon=True)
        self.srv = srv
        self.executor = executor or (lambda cmd: {"echo": cmd.get("op")})
        self.swallow = swallow  # pick up a command but never answer (→ timeout)
        self._stop = threading.Event()

    def run(self):
        while not self._stop.is_set():
            try:
                status, cmd = _req(self.srv, "GET", "/poll")
            except Exception:
                if self._stop.is_set():
                    return
                continue
            if status == 204 or cmd is None:
                continue
            if self.swallow:
                continue
            envelope = {"id": cmd["id"], "ok": True, "data": self.executor(cmd)}
            try:
                _req(self.srv, "POST", "/result", envelope)
            except Exception:
                pass

    def stop(self):
        self._stop.set()


def _wait_connected(srv, want=True, timeout=3.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        status, body = _req(srv, "GET", "/health")
        if status == 200 and body["extension_connected"] == want:
            return True
        time.sleep(0.02)
    return False


# --------------------------------------------------------------------------- #
# Token generation + perms
# --------------------------------------------------------------------------- #
def test_token_created_with_0600(tmp_path):
    p = tmp_path / "sub" / "token"
    tok = S.load_or_create_token(p)
    assert tok and len(tok) >= 32
    mode = stat.S_IMODE(os.stat(p).st_mode)
    assert mode == 0o600, f"expected 0600, got {oct(mode)}"


def test_token_stable_across_calls(tmp_path):
    p = tmp_path / "token"
    a = S.load_or_create_token(p)
    b = S.load_or_create_token(p)
    assert a == b


def test_empty_token_file_regenerated(tmp_path):
    p = tmp_path / "token"
    p.write_text("   \n")
    tok = S.load_or_create_token(p)
    assert tok.strip()
    assert stat.S_IMODE(os.stat(p).st_mode) == 0o600


# --------------------------------------------------------------------------- #
# Auth + host allowlist
# --------------------------------------------------------------------------- #
def test_missing_token_401():
    srv, _ = _serve()
    try:
        status, body = _req(srv, "GET", "/health", token=None)
        assert status == 401
        assert body["error"] == "unauthorized"
    finally:
        srv.shutdown(); srv.server_close()


def test_wrong_token_401():
    srv, _ = _serve()
    try:
        status, body = _req(srv, "GET", "/health", token="nope")
        assert status == 401
    finally:
        srv.shutdown(); srv.server_close()


def test_bad_host_403():
    srv, _ = _serve()
    try:
        status, body = _req(srv, "GET", "/health", host="evil.example.com")
        assert status == 403
        assert body["error"] == "bad_host"
    finally:
        srv.shutdown(); srv.server_close()


def test_host_with_port_allowed():
    srv, _ = _serve()
    try:
        status, _ = _req(srv, "GET", "/health", host="localhost:8788")
        assert status == 200
    finally:
        srv.shutdown(); srv.server_close()


# --------------------------------------------------------------------------- #
# /health connection state
# --------------------------------------------------------------------------- #
def test_health_no_extension():
    srv, _ = _serve()
    try:
        status, body = _req(srv, "GET", "/health")
        assert status == 200
        assert body == {"ok": True, "extension_connected": False}
    finally:
        srv.shutdown(); srv.server_close()


def test_health_reflects_connected_extension():
    srv, _ = _serve(poll_timeout=5.0)
    ext = FakeExtension(srv)
    ext.start()
    try:
        assert _wait_connected(srv, want=True), "extension never showed connected"
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


# --------------------------------------------------------------------------- #
# /cmd round-trip
# --------------------------------------------------------------------------- #
def test_cmd_roundtrip_getHtml():
    srv, _ = _serve()

    def executor(cmd):
        return {"html": "<html><body>hi</body></html>", "url": "https://x.test"}

    ext = FakeExtension(srv, executor=executor)
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        status, body = _req(srv, "POST", "/cmd", {"op": "getHtml"})
        assert status == 200
        assert body["ok"] is True
        assert body["result"]["data"]["html"] == "<html><body>hi</body></html>"
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


def test_cmd_nav_requires_url():
    srv, _ = _serve()
    ext = FakeExtension(srv)
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        status, body = _req(srv, "POST", "/cmd", {"op": "nav"})
        assert status == 400
        assert body["error"] == "missing_field:url"
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


def test_cmd_unknown_op_400():
    srv, _ = _serve()
    ext = FakeExtension(srv)
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        status, body = _req(srv, "POST", "/cmd", {"op": "rm_rf"})
        assert status == 400
        assert body["error"] == "unknown_op"
        assert body["op"] == "rm_rf"
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


def test_cmd_no_extension_503():
    srv, _ = _serve()
    try:
        status, body = _req(srv, "POST", "/cmd", {"op": "getHtml"})
        assert status == 503
        assert body["error"] == "extension_not_connected"
    finally:
        srv.shutdown(); srv.server_close()


def test_cmd_timeout_504():
    # Extension connects + picks up the command but never answers → 504.
    srv, _ = _serve(cmd_timeout=0.5, poll_timeout=5.0)
    ext = FakeExtension(srv, swallow=True)
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        status, body = _req(srv, "POST", "/cmd", {"op": "getHtml"})
        assert status == 504
        assert body["error"] == "timeout"
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


def test_bad_json_body_400():
    srv, _ = _serve()
    ext = FakeExtension(srv)
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        port = srv.server_address[1]
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/cmd", data=b"not-json{{",
            headers={"Authorization": f"Bearer {TOKEN}", "Host": "127.0.0.1",
                     "Content-Type": "application/json"},
            method="POST",
        )
        try:
            urllib.request.urlopen(req, timeout=5)
            assert False, "expected 400"
        except urllib.error.HTTPError as e:
            assert e.code == 400
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


def test_result_unknown_id_reported():
    srv, _ = _serve()
    try:
        status, body = _req(srv, "POST", "/result",
                            {"id": "deadbeef", "ok": True, "data": {}})
        assert status == 200
        assert body["ok"] is False
        assert body["error"] == "unknown_id"
    finally:
        srv.shutdown(); srv.server_close()


def test_unknown_path_404():
    srv, _ = _serve()
    try:
        status, _ = _req(srv, "GET", "/nope")
        assert status == 404
    finally:
        srv.shutdown(); srv.server_close()


# --------------------------------------------------------------------------- #
# Bridge core: id correlation + concurrency (no HTTP)
# --------------------------------------------------------------------------- #
def test_bridge_id_correlation_out_of_order():
    """Two concurrent submits get their OWN replies even when a mock extension
    answers them in reverse order."""
    bridge = S.Bridge()
    # Prime "connected".
    poll_done = threading.Event()

    def poller():
        # Simulate a connected extension that stays connected long enough.
        while not poll_done.is_set():
            cmd = bridge.next_command(0.2)
            if cmd is not None:
                picked.append(cmd)

    picked = []
    t = threading.Thread(target=poller, daemon=True)
    t.start()

    results = {}

    def submit(tag):
        results[tag] = bridge.submit({"op": "eval", "tag": tag}, timeout=3.0)

    s1 = threading.Thread(target=submit, args=("A",), daemon=True)
    s2 = threading.Thread(target=submit, args=("B",), daemon=True)
    s1.start(); s2.start()

    # Wait until both commands are picked up.
    deadline = time.time() + 3
    while len(picked) < 2 and time.time() < deadline:
        time.sleep(0.01)
    assert len(picked) == 2

    # Answer in REVERSE order.
    for cmd in reversed(picked):
        bridge.deliver_result(cmd["id"], {"id": cmd["id"], "ok": True,
                                          "data": {"tag": cmd["tag"]}})

    s1.join(3); s2.join(3)
    poll_done.set()
    assert results["A"]["data"]["tag"] == "A"
    assert results["B"]["data"]["tag"] == "B"


def test_bridge_submit_no_extension_raises():
    bridge = S.Bridge()
    with pytest.raises(S.NoExtension):
        bridge.submit({"op": "getHtml"}, timeout=0.2)


def test_bridge_deliver_unknown_id_false():
    bridge = S.Bridge()
    assert bridge.deliver_result("nope", {"x": 1}) is False


def test_validate_command_contract():
    # The op set is the shared contract with extension/protocol.js.
    assert set(S.ALLOWED_OPS) == {"getHtml", "eval", "tabs", "nav", "screenshot"}
    assert S.validate_command({"op": "tabs"}) == ("tabs", None)
    assert S.validate_command({"op": "eval", "js": "1"})[0] == "eval"
    assert S.validate_command({"op": "eval"})[1] == "missing_field:js"
    assert S.validate_command({"op": "bogus"})[1] == "unknown_op"
    assert S.validate_command("nope")[1] == "body_not_object"
