"""Tests for the browser-bridge rendezvous server.

Fully HEADLESS: no Brave, no network beyond loopback. The extension is
simulated in-process by a `FakeExtension` thread that long-polls `/poll` (with
its instance identity headers), "executes" the op (echo), and POSTs to
`/result` (echoing its instanceId) — exercising the real HTTP round-trip, the
request↔reply id correlation, AND the multi-instance registry/routing.

Run: nix-shell -p python312Packages.pytest --run "pytest scripts/browser-bridge/tests"
"""
import base64
import json
import os
import stat
import struct
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import server as S  # noqa: E402

TOKEN = "test-token-abc123"
EXT_DIR = Path(__file__).resolve().parent.parent / "extension"

# The in-repo activity spool emitter (single source of truth for the v1 line
# format). scripts/browser-bridge/tests/ -> parent.parent.parent == scripts/.
SPOOL_EMIT_PY = (Path(__file__).resolve().parent.parent.parent
                 / "collector" / "keylog" / "spool_emit.py")


# --------------------------------------------------------------------------- #
# Telemetry (browser-bridge -> activity spool) test scaffolding
# --------------------------------------------------------------------------- #
def _parse_spool_line(line: str) -> dict:
    """Decode ONE v1 spool line into a field dict (base64 keys decoded)."""
    parts = line.rstrip("\n").split("\t")
    assert parts and parts[0] == "v1", f"not a v1 line: {line!r}"
    out = {}
    for kv in parts[1:]:
        key, _, val = kv.partition("=")
        if key.startswith("b64:"):
            out[key[4:]] = base64.b64decode(val).decode("utf-8")
        else:
            out[key] = val
    return out


def _log_file(spool_dir) -> Path:
    return Path(spool_dir) / "current.log"


def _read_events(spool_dir) -> list:
    f = _log_file(spool_dir)
    if not f.exists():
        return []
    return [_parse_spool_line(ln) for ln in f.read_text().splitlines()
            if ln.strip()]


def _wait_events(spool_dir, n=1, timeout=3.0) -> list:
    """Poll the spool for >=n events (the emit runs off the critical path, after
    the HTTP response, so it lands slightly after /cmd returns)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        evs = _read_events(spool_dir)
        if len(evs) >= n:
            return evs
        time.sleep(0.02)
    return _read_events(spool_dir)


@pytest.fixture(autouse=True)
def _isolate_activity_spool(tmp_path, monkeypatch):
    """Protect the REAL activity spool: EVERY test's telemetry writes go to a
    per-test temp dir, and the lazy emitter cache is reset so each test loads it
    fresh under the current env."""
    monkeypatch.setenv("ACTIVITY_SPOOL_DIR", str(tmp_path / "activity-spool"))
    monkeypatch.setattr(S, "_spool_emit_mod", None)
    monkeypatch.setattr(S, "_spool_emit_tried", False)


@pytest.fixture
def telemetry(tmp_path, monkeypatch):
    """Pin the bridge telemetry emitter at the in-repo spool_emit (hermetic — no
    dependency on the host's ~/workspace checkout) and return the temp spool dir
    that `_isolate_activity_spool` pointed ACTIVITY_SPOOL_DIR at."""
    monkeypatch.setattr(S, "_SPOOL_EMIT_PATH", SPOOL_EMIT_PY)
    monkeypatch.setattr(S, "_spool_emit_mod", None)
    monkeypatch.setattr(S, "_spool_emit_tried", False)
    return tmp_path / "activity-spool"


# --------------------------------------------------------------------------- #
# HTTP helpers
# --------------------------------------------------------------------------- #
def _req(srv, method, path, body=None, token=TOKEN, host="127.0.0.1",
         headers=None):
    port = srv.server_address[1]
    hdrs = {}
    if token is not None:
        hdrs["Authorization"] = f"Bearer {token}"
    if host is not None:
        hdrs["Host"] = host
    if headers:
        hdrs.update(headers)
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        hdrs["Content-Type"] = "application/json"
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}", data=data, headers=hdrs,
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
    registry = S.Registry()
    handler = S.make_handler(registry, TOKEN, cmd_timeout, poll_timeout)
    srv = S.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    srv.daemon_threads = True
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv, registry


class FakeExtension(threading.Thread):
    """Simulated extension: long-polls /poll (with its instance identity) and
    echoes the op back via /result (echoing its instanceId)."""

    def __init__(self, srv, instance_id="fake-1", label="", executor=None,
                 swallow=False, active_url=None, active_title=None):
        super().__init__(daemon=True)
        self.srv = srv
        self.instance_id = instance_id
        self.label = label
        self.executor = executor or (lambda cmd: {"echo": cmd.get("op")})
        self.swallow = swallow  # pick up a command but never answer (→ timeout)
        self.active_url = active_url
        self.active_title = active_title
        self._stopev = threading.Event()

    def _poll_headers(self):
        h = {S.HDR_INSTANCE_ID: self.instance_id}
        if self.label:
            h[S.HDR_LABEL] = urllib.parse.quote(self.label)
        if self.active_url:
            h[S.HDR_ACTIVE_URL] = urllib.parse.quote(self.active_url)
        if self.active_title:
            h[S.HDR_ACTIVE_TITLE] = urllib.parse.quote(self.active_title)
        return h

    def run(self):
        while not self._stopev.is_set():
            try:
                status, cmd = _req(self.srv, "GET", "/poll",
                                   headers=self._poll_headers())
            except Exception:
                if self._stopev.is_set():
                    return
                continue
            if status == 204 or cmd is None:
                continue
            if self.swallow:
                continue
            envelope = {"id": cmd["id"], "ok": True,
                        "data": self.executor(cmd), "instanceId": self.instance_id}
            try:
                _req(self.srv, "POST", "/result", envelope)
            except Exception:
                pass

    def stop(self):
        self._stopev.set()


def _wait_connected(srv, want=True, timeout=3.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        status, body = _req(srv, "GET", "/health")
        if status == 200 and body["extension_connected"] == want:
            return True
        time.sleep(0.02)
    return False


def _wait_instances(srv, n, timeout=3.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        status, body = _req(srv, "GET", "/health")
        if status == 200 and body.get("count", 0) >= n:
            return body
        time.sleep(0.02)
    return None


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
# Command-delivery channel auth: /poll (extension long-poll) and /result must
# enforce the SAME host + bearer gate as /health and /cmd — INCLUDING when the
# new instance-scoped params are present. These are the two endpoints that
# actually move commands, so they get direct coverage. The guard runs before any
# polling/body work, so a rejected request returns immediately.
# --------------------------------------------------------------------------- #
def _poll_hdrs(instance_id="fake-1", label=""):
    h = {S.HDR_INSTANCE_ID: instance_id}
    if label:
        h[S.HDR_LABEL] = urllib.parse.quote(label)
    return h


def test_poll_missing_token_401():
    srv, _ = _serve()
    try:
        status, body = _req(srv, "GET", "/poll", token=None,
                            headers=_poll_hdrs())
        assert status == 401
        assert body["error"] == "unauthorized"
    finally:
        srv.shutdown(); srv.server_close()


def test_poll_wrong_token_401():
    srv, _ = _serve()
    try:
        status, body = _req(srv, "GET", "/poll", token="nope",
                            headers=_poll_hdrs())
        assert status == 401
        assert body["error"] == "unauthorized"
    finally:
        srv.shutdown(); srv.server_close()


def test_poll_bad_host_403():
    srv, _ = _serve()
    try:
        status, body = _req(srv, "GET", "/poll", host="evil.example.com",
                            headers=_poll_hdrs())
        assert status == 403
        assert body["error"] == "bad_host"
    finally:
        srv.shutdown(); srv.server_close()


def test_result_missing_token_401():
    srv, _ = _serve()
    try:
        status, body = _req(srv, "POST", "/result",
                            {"id": "x", "ok": True, "data": {},
                             "instanceId": "fake-1"}, token=None)
        assert status == 401
        assert body["error"] == "unauthorized"
    finally:
        srv.shutdown(); srv.server_close()


def test_result_wrong_token_401():
    srv, _ = _serve()
    try:
        status, body = _req(srv, "POST", "/result",
                            {"id": "x", "ok": True, "data": {},
                             "instanceId": "fake-1"}, token="nope")
        assert status == 401
        assert body["error"] == "unauthorized"
    finally:
        srv.shutdown(); srv.server_close()


def test_result_bad_host_403():
    srv, _ = _serve()
    try:
        status, body = _req(srv, "POST", "/result",
                            {"id": "x", "ok": True, "data": {},
                             "instanceId": "fake-1"}, host="evil.example.com")
        assert status == 403
        assert body["error"] == "bad_host"
    finally:
        srv.shutdown(); srv.server_close()


# --------------------------------------------------------------------------- #
# /health connection state (now per-instance)
# --------------------------------------------------------------------------- #
def test_health_no_extension():
    srv, _ = _serve()
    try:
        status, body = _req(srv, "GET", "/health")
        assert status == 200
        assert body["ok"] is True
        assert body["extension_connected"] is False
        assert body["count"] == 0
        assert body["instances"] == []
    finally:
        srv.shutdown(); srv.server_close()


def test_health_reflects_connected_extension():
    srv, _ = _serve(poll_timeout=5.0)
    ext = FakeExtension(srv, instance_id="a", label="alpha")
    ext.start()
    try:
        body = _wait_instances(srv, 1)
        assert body is not None, "extension never showed connected"
        assert body["extension_connected"] is True
        assert body["count"] == 1
        keys = {i["key"] for i in body["instances"]}
        assert "alpha" in keys
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


def test_health_per_instance_two():
    srv, _ = _serve(poll_timeout=5.0)
    a = FakeExtension(srv, instance_id="a", label="alpha",
                      active_url="https://a.test", active_title="A")
    b = FakeExtension(srv, instance_id="b", label="beta")
    a.start(); b.start()
    try:
        body = _wait_instances(srv, 2)
        assert body is not None
        assert body["count"] == 2
        by_key = {i["key"]: i for i in body["instances"]}
        assert set(by_key) == {"alpha", "beta"}
        assert by_key["alpha"]["instanceId"] == "a"
        assert by_key["alpha"]["activeTab"]["url"] == "https://a.test"
        assert by_key["beta"]["label"] == "beta"
    finally:
        a.stop(); b.stop(); srv.shutdown(); srv.server_close()


def test_instances_endpoint():
    srv, _ = _serve(poll_timeout=5.0)
    a = FakeExtension(srv, instance_id="a", label="alpha")
    a.start()
    try:
        assert _wait_instances(srv, 1) is not None
        status, body = _req(srv, "GET", "/instances")
        assert status == 200
        assert body["count"] == 1
        assert body["instances"][0]["key"] == "alpha"
    finally:
        a.stop(); srv.shutdown(); srv.server_close()


# --------------------------------------------------------------------------- #
# /cmd round-trip (single instance, back-compat: no target needed)
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


def test_cmd_single_instance_no_target_ok():
    """Exactly one connected instance → a command with NO target routes to it."""
    srv, _ = _serve()
    ext = FakeExtension(srv, instance_id="solo", label="only",
                        executor=lambda c: {"who": "only"})
    ext.start()
    try:
        assert _wait_instances(srv, 1) is not None
        status, body = _req(srv, "POST", "/cmd", {"op": "tabs"})
        assert status == 200
        assert body["result"]["data"]["who"] == "only"
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
# Multi-instance routing + targeting (over real HTTP)
# --------------------------------------------------------------------------- #
def test_cmd_routes_to_target_instance():
    """With two instances connected, --instance/target routes to the named one."""
    srv, _ = _serve(poll_timeout=5.0)
    a = FakeExtension(srv, instance_id="a", label="alpha",
                      executor=lambda c: {"who": "alpha"})
    b = FakeExtension(srv, instance_id="b", label="beta",
                      executor=lambda c: {"who": "beta"})
    a.start(); b.start()
    try:
        assert _wait_instances(srv, 2) is not None
        st, body = _req(srv, "POST", "/cmd", {"op": "tabs", "target": "alpha"})
        assert st == 200 and body["result"]["data"]["who"] == "alpha"
        st, body = _req(srv, "POST", "/cmd", {"op": "tabs", "target": "beta"})
        assert st == 200 and body["result"]["data"]["who"] == "beta"
        # Target by auto-id also works.
        st, body = _req(srv, "POST", "/cmd", {"op": "tabs", "target": "a"})
        assert st == 200 and body["result"]["data"]["who"] == "alpha"
    finally:
        a.stop(); b.stop(); srv.shutdown(); srv.server_close()


def test_cmd_ambiguous_no_target_409():
    """Two instances + no target → 409 ambiguous_instance, never a silent pick."""
    srv, _ = _serve(poll_timeout=5.0)
    a = FakeExtension(srv, instance_id="a", label="alpha")
    b = FakeExtension(srv, instance_id="b", label="beta")
    a.start(); b.start()
    try:
        assert _wait_instances(srv, 2) is not None
        st, body = _req(srv, "POST", "/cmd", {"op": "tabs"})
        assert st == 409
        assert body["error"] == "ambiguous_instance"
        keys = {i["key"] for i in body["instances"]}
        assert keys == {"alpha", "beta"}
    finally:
        a.stop(); b.stop(); srv.shutdown(); srv.server_close()


def test_cmd_unknown_target_404():
    srv, _ = _serve(poll_timeout=5.0)
    a = FakeExtension(srv, instance_id="a", label="alpha")
    a.start()
    try:
        assert _wait_instances(srv, 1) is not None
        st, body = _req(srv, "POST", "/cmd", {"op": "tabs", "target": "ghost"})
        assert st == 404
        assert body["error"] == "unknown_instance"
        assert body["target"] == "ghost"
    finally:
        a.stop(); srv.shutdown(); srv.server_close()


def test_poll_superseded_returns_409_not_204():
    """A blocked /poll whose connection is displaced by a NEWER connection sharing
    its routing key returns the DISTINCT `409 superseded` signal — never the idle
    `204`. This is what lets the extension back off instead of hot re-polling
    (the mutual-supersede livelock fix). Still bearer+Host guarded: this path is
    only reachable AFTER _guard, and the auth/host tests above cover /poll."""
    srv, _ = _serve(poll_timeout=5.0)
    res = {}

    def poll(iid, key):
        res[iid] = _req(srv, "GET", "/poll", headers=_poll_hdrs(iid, key))

    ta = threading.Thread(target=poll, args=("old", "dup"), daemon=True)
    ta.start()
    try:
        # "old" is registered + blocked in its long-poll.
        assert _wait_instances(srv, 1) is not None
        # "new" (different auto-id, same key) supersedes it.
        tb = threading.Thread(target=poll, args=("new", "dup"), daemon=True)
        tb.start()
        ta.join(3)
        assert "old" in res, "superseded poll never returned"
        status, body = res["old"]
        assert status == 409, f"expected 409 superseded, got {status}"
        assert body["error"] == "superseded"
    finally:
        srv.shutdown(); srv.server_close()


def test_supersede_logged_once_per_displacement_not_per_poll(monkeypatch):
    """The supersede is logged ONCE, at the displacement site — the superseded
    connection's own returning poll must NOT log (a backing-off loser can't flood
    journald). Deterministic proof of the no-churn contract (the extension's own
    back-off is only manually verifiable; the server side is unit-tested here)."""
    reg = S.Registry()
    events = []
    monkeypatch.setattr(S, "log", lambda ev, **k: events.append((ev, k)))

    got = {}

    def poll_old():
        got["old"] = reg.poll("old", "dup", 2.0)

    t = threading.Thread(target=poll_old, daemon=True)
    t.start()
    # Wait until "old" is registered AND actively blocked in its poll.
    deadline = time.time() + 3
    while time.time() < deadline:
        with reg._cond:
            inst = reg._instances.get("dup")
            if inst is not None and inst.active_polls > 0:
                break
        time.sleep(0.01)
    # A NEW connection claims the key → supersede "old" (ONE displacement event).
    reg.poll("new", "dup", 0.05)
    t.join(2)

    # The superseded connection got the DISTINCT sentinel, not an idle None.
    assert got["old"] is S.SUPERSEDED
    supersede_logs = [e for e in events if e[0] == "supersede"]
    assert len(supersede_logs) == 1, \
        f"expected exactly ONE supersede log, got {len(supersede_logs)}: {supersede_logs}"


def test_cmd_same_label_supersede_routes_to_newest():
    """A NEW connection with the SAME label supersedes the old one at that key
    (they share a routing key); the old connection is dropped and a command
    routes to the survivor.

    The superseded connection is modelled as *dropped/closed* (it stops polling)
    — labels are required to be unique per host, so two connections concurrently
    contending for one key is a misconfiguration, not steady state.
    """
    srv, _ = _serve(poll_timeout=0.3)
    old = FakeExtension(srv, instance_id="old", label="dup",
                        executor=lambda c: {"who": "old"})
    old.start()
    try:
        assert _wait_instances(srv, 1) is not None
        # Old connection is dropped (stops polling) but is still within the stale
        # window, so it is still the registered holder of key "dup".
        old.stop(); old.join(timeout=2)

        new = FakeExtension(srv, instance_id="new", label="dup",
                            executor=lambda c: {"who": "new"})
        new.start()
        # New's first poll supersedes old; the sole live holder of "dup" is now new.
        deadline = time.time() + 3
        while time.time() < deadline:
            st, body = _req(srv, "GET", "/health")
            if st == 200 and body["count"] == 1 and \
                    body["instances"][0]["instanceId"] == "new":
                break
            time.sleep(0.02)
        assert body["count"] == 1
        assert body["instances"][0]["instanceId"] == "new"

        st, body = _req(srv, "POST", "/cmd", {"op": "tabs"})
        assert st == 200 and body["result"]["data"]["who"] == "new"
        new.stop()
    finally:
        old.stop(); srv.shutdown(); srv.server_close()


# --------------------------------------------------------------------------- #
# Back-compat: a legacy extension that polls WITHOUT an instance id
# --------------------------------------------------------------------------- #
def test_legacy_no_handshake_single_instance():
    """A poll with no X-Bridge-Instance-Id registers as one synthetic instance
    and a no-target /cmd + a no-instanceId /result still round-trip."""
    srv, _ = _serve(poll_timeout=5.0)

    class LegacyExt(FakeExtension):
        def _poll_headers(self):
            return {}  # no identity headers at all

        def run(self):
            while not self._stopev.is_set():
                try:
                    status, cmd = _req(self.srv, "GET", "/poll")
                except Exception:
                    if self._stopev.is_set():
                        return
                    continue
                if status == 204 or cmd is None:
                    continue
                # NOTE: no instanceId in the result body (old wire shape).
                _req(self.srv, "POST", "/result",
                     {"id": cmd["id"], "ok": True, "data": {"who": "legacy"}})

    ext = LegacyExt(srv)
    ext.start()
    try:
        body = _wait_instances(srv, 1)
        assert body is not None
        assert body["instances"][0]["key"] == S.LEGACY_INSTANCE_ID
        st, r = _req(srv, "POST", "/cmd", {"op": "tabs"})
        assert st == 200 and r["result"]["data"]["who"] == "legacy"
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


# --------------------------------------------------------------------------- #
# Registry core (no HTTP): routing, independence, supersede, key resolution
# --------------------------------------------------------------------------- #
def test_registry_independent_queues_no_cross_delivery():
    """Command for instance A is never delivered to B's poll; each gets its own
    queue and its own reply."""
    reg = S.Registry()
    picked_a, picked_b = [], []
    stop = threading.Event()

    def poller(iid, label, bucket):
        while not stop.is_set():
            cmd = reg.poll(iid, label, 0.1)
            if cmd is not None:
                bucket.append(cmd)
                reg.deliver_result(cmd["id"],
                                   {"id": cmd["id"], "ok": True,
                                    "data": {"who": label}}, instance_id=iid)

    ta = threading.Thread(target=poller, args=("a", "alpha", picked_a),
                          daemon=True)
    tb = threading.Thread(target=poller, args=("b", "beta", picked_b),
                          daemon=True)
    ta.start(); tb.start()
    # Wait until both are registered + live.
    deadline = time.time() + 3
    while time.time() < deadline and len(reg.snapshot()) < 2:
        time.sleep(0.01)
    assert len(reg.snapshot()) == 2

    r = reg.submit({"op": "tabs"}, timeout=3.0, target="alpha")
    assert r["data"]["who"] == "alpha"
    assert len(picked_b) == 0, "B must never see A's command"
    assert len(picked_a) == 1
    stop.set()


def test_registry_key_is_label_when_set_else_autoid():
    reg = S.Registry()
    stop = threading.Event()

    def poller(iid, label):
        while not stop.is_set():
            reg.poll(iid, label, 0.05)

    # Labelled instance → key is the label.
    t1 = threading.Thread(target=poller, args=("uuid-1", "work"), daemon=True)
    # Unlabelled instance → key is the auto-id.
    t2 = threading.Thread(target=poller, args=("uuid-2", ""), daemon=True)
    t1.start(); t2.start()
    deadline = time.time() + 3
    while time.time() < deadline and len(reg.snapshot()) < 2:
        time.sleep(0.01)
    keys = {i["key"] for i in reg.snapshot()}
    assert "work" in keys       # label wins
    assert "uuid-2" in keys     # falls back to auto-id
    stop.set()


def test_registry_supersede_inflight_resolves_error_no_leak():
    """Same routing key reconnects with a NEW auto-id → the old connection is
    dropped, its in-flight submit resolves to BridgeSuperseded (no orphaned
    waiter), and a subsequent command routes to the newcomer."""
    reg = S.Registry()
    picked = []

    def poller_old():
        cmd = reg.poll("old", "dup", 2.0)
        if cmd is not None:
            picked.append(cmd)   # picked up but NEVER delivered (in-flight)

    threading.Thread(target=poller_old, daemon=True).start()

    res = {}

    def submit():
        try:
            res["v"] = reg.submit({"op": "getHtml"}, timeout=3.0, target="dup")
        except Exception as e:  # noqa: BLE001
            res["err"] = type(e).__name__

    ts = threading.Thread(target=submit, daemon=True)
    ts.start()

    # Wait until "old" picked the command (now in-flight).
    deadline = time.time() + 3
    while not picked and time.time() < deadline:
        time.sleep(0.01)
    assert picked, "old connection never picked up the command"

    # A NEW connection registers under the same key → supersede "old".
    reg.poll("new", "dup", 0.05)

    ts.join(3)
    assert res.get("err") == "BridgeSuperseded"

    # No orphaned waiter / leak: the old instance is gone; the survivor is "new".
    with reg._cond:
        assert "old" not in reg._instances
        inst = reg._instances["dup"]
        assert inst.instance_id == "new"
        assert inst.waiters == set()

    # A subsequent command routes to the newcomer.
    def deliverer():
        cmd = reg.poll("new", "dup", 2.0)
        if cmd is not None:
            reg.deliver_result(cmd["id"], {"id": cmd["id"], "ok": True,
                                           "data": {"who": "new"}},
                               instance_id="new")
    threading.Thread(target=deliverer, daemon=True).start()
    r = reg.submit({"op": "tabs"}, timeout=3.0, target="dup")
    assert r["data"]["who"] == "new"


def test_registry_id_correlation_out_of_order():
    """Two concurrent submits to the SAME instance get their OWN replies even
    when the extension answers them in reverse order."""
    reg = S.Registry()
    poll_done = threading.Event()
    picked = []

    def poller():
        while not poll_done.is_set():
            cmd = reg.poll("solo", "one", 0.2)
            if cmd is not None:
                picked.append(cmd)

    threading.Thread(target=poller, daemon=True).start()

    results = {}

    def submit(tag):
        results[tag] = reg.submit({"op": "eval", "tag": tag}, timeout=3.0)

    s1 = threading.Thread(target=submit, args=("A",), daemon=True)
    s2 = threading.Thread(target=submit, args=("B",), daemon=True)
    s1.start(); s2.start()

    deadline = time.time() + 3
    while len(picked) < 2 and time.time() < deadline:
        time.sleep(0.01)
    assert len(picked) == 2

    for cmd in reversed(picked):
        reg.deliver_result(cmd["id"], {"id": cmd["id"], "ok": True,
                                       "data": {"tag": cmd["tag"]}},
                           instance_id="solo")

    s1.join(3); s2.join(3)
    poll_done.set()
    assert results["A"]["data"]["tag"] == "A"
    assert results["B"]["data"]["tag"] == "B"


def test_registry_submit_no_extension_raises():
    reg = S.Registry()
    with pytest.raises(S.NoExtension):
        reg.submit({"op": "getHtml"}, timeout=0.2)


def test_registry_deliver_unknown_id_false():
    reg = S.Registry()
    assert reg.deliver_result("nope", {"x": 1}) is False


def test_validate_command_contract():
    # The op set is the shared contract with extension/protocol.js.
    assert set(S.ALLOWED_OPS) == {"getHtml", "eval", "tabs", "nav", "screenshot"}
    assert S.validate_command({"op": "tabs"}) == ("tabs", None)
    assert S.validate_command({"op": "eval", "js": "1"})[0] == "eval"
    assert S.validate_command({"op": "eval"})[1] == "missing_field:js"
    assert S.validate_command({"op": "bogus"})[1] == "unknown_op"
    assert S.validate_command("nope")[1] == "body_not_object"


# --------------------------------------------------------------------------- #
# Icon sanity — the manifest references all four sizes and each PNG's real IHDR
# width/height matches its declared size. Stdlib only (struct — no PIL).
# --------------------------------------------------------------------------- #
def _png_size(path):
    """Return (width, height) from a PNG's IHDR using stdlib struct only."""
    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n", f"not a PNG: {path}"
    # 8-byte signature, then IHDR chunk: 4 len + 4 "IHDR" + 4 width + 4 height.
    assert data[12:16] == b"IHDR", f"IHDR not first chunk: {path}"
    width, height = struct.unpack(">II", data[16:24])
    return width, height


def test_manifest_icons_exist_and_match_declared_size():
    manifest = json.loads((EXT_DIR / "manifest.json").read_text())
    icons = manifest.get("icons")
    assert icons, "manifest.json is missing an 'icons' map"
    assert set(icons) == {"16", "32", "48", "128"}, \
        "manifest icons must declare 16/32/48/128"
    for size, rel in icons.items():
        path = EXT_DIR / rel
        assert path.exists(), f"declared icon missing on disk: {rel}"
        w, h = _png_size(path)
        assert (w, h) == (int(size), int(size)), \
            f"{rel}: declared {size} but PNG is {w}x{h}"
    # The MV3 toolbar action icon references the same four sizes.
    action_icon = manifest.get("action", {}).get("default_icon", {})
    assert set(action_icon) == {"16", "32", "48", "128"}


def test_svg_source_present():
    assert (EXT_DIR / "icons" / "icon.svg").exists(), \
        "the SVG icon source must be committed alongside the PNGs"


# --------------------------------------------------------------------------- #
# Activity telemetry: every handled command emits ONE metadata-only,
# best-effort event into the activity spool (source=browser-bridge, kind=cmd).
# All headless — writes to a temp spool dir, never ClickHouse/network/Brave.
# --------------------------------------------------------------------------- #
def test_domain_from_result_bare_host_only():
    """Only a bare hostname is ever derived — never a path/query/port, and a
    screenshot data: URL yields nothing."""
    d = S._domain_from_result
    assert d({"data": {"url": "https://mail.google.com/mail/u/0?tok=SECRET"}}) \
        == "mail.google.com"
    assert d({"url": "https://x.test:8443/p?q=1"}) == "x.test"
    assert d({"data": {"url": "data:image/png;base64,AAAABBBB"}}) == ""
    assert d({"data": {"dataUrl": "data:image/png;base64,AAAA"}}) == ""
    assert d({"data": {"tabs": []}}) == ""
    assert d({}) == ""
    assert d(None) == ""
    assert d("not-a-dict") == ""


def test_cmd_ok_emits_one_metadata_event(telemetry):
    spool_dir = telemetry
    srv, _ = _serve()
    ext = FakeExtension(
        srv, executor=lambda c: {"url": "https://mail.google.com/mail/u/0?t=rm",
                                 "html": "<html>hi</html>"})
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        st, body = _req(srv, "POST", "/cmd", {"op": "getHtml"})
        assert st == 200
        evs = _wait_events(spool_dir, 1)
        assert len(evs) == 1, f"expected exactly one event, got {evs}"
        e = evs[0]
        assert e["source"] == "browser-bridge"
        assert e["kind"] == "cmd"
        assert e["exit_code"] == "0"
        assert int(e["duration_ms"]) >= 0        # latency present
        assert e["text"] == "mail.google.com"    # text = bare domain
        p = json.loads(e["payload"])
        assert p == {"op": "getHtml", "key": "", "outcome": "ok",
                     "domain": "mail.google.com"}
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


def test_cmd_ok_no_url_uses_op_as_text_and_omits_domain(telemetry):
    """A result with no url (e.g. tabs) → text falls back to the op, and the
    payload carries no domain key."""
    spool_dir = telemetry
    srv, _ = _serve()
    ext = FakeExtension(srv, executor=lambda c: {"tabs": [{"id": 1}]})
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        st, _ = _req(srv, "POST", "/cmd", {"op": "tabs"})
        assert st == 200
        e = _wait_events(spool_dir, 1)[0]
        assert e["text"] == "tabs"
        p = json.loads(e["payload"])
        assert p["op"] == "tabs" and p["outcome"] == "ok"
        assert "domain" not in p
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


def test_cmd_emits_routing_key_from_target(telemetry):
    """The instance routing key (the skill's --instance target) is recorded."""
    spool_dir = telemetry
    srv, _ = _serve(poll_timeout=5.0)
    a = FakeExtension(srv, instance_id="a", label="alpha",
                      executor=lambda c: {"who": "a"})
    b = FakeExtension(srv, instance_id="b", label="beta",
                      executor=lambda c: {"who": "b"})
    a.start(); b.start()
    try:
        assert _wait_instances(srv, 2) is not None
        st, _ = _req(srv, "POST", "/cmd", {"op": "tabs", "target": "alpha"})
        assert st == 200
        e = _wait_events(spool_dir, 1)[0]
        p = json.loads(e["payload"])
        assert p["key"] == "alpha"
        assert p["outcome"] == "ok"
    finally:
        a.stop(); b.stop(); srv.shutdown(); srv.server_close()


def test_cmd_no_extension_emits_error_outcome(telemetry):
    """An error dispatch (no extension) still emits, with a nonzero exit_code and
    the error outcome — no domain (no result)."""
    spool_dir = telemetry
    srv, _ = _serve()
    try:
        st, _ = _req(srv, "POST", "/cmd", {"op": "tabs"})
        assert st == 503
        e = _wait_events(spool_dir, 1)[0]
        assert e["exit_code"] == "1"
        p = json.loads(e["payload"])
        assert p["op"] == "tabs"
        assert p["outcome"] == "no_extension"
        assert "domain" not in p
    finally:
        srv.shutdown(); srv.server_close()


def test_cmd_ambiguous_emits_ambiguous_outcome(telemetry):
    """An ambiguous dispatch (>1 instance, no target) emits outcome=ambiguous."""
    spool_dir = telemetry
    srv, _ = _serve(poll_timeout=5.0)
    a = FakeExtension(srv, instance_id="a", label="alpha")
    b = FakeExtension(srv, instance_id="b", label="beta")
    a.start(); b.start()
    try:
        assert _wait_instances(srv, 2) is not None
        st, _ = _req(srv, "POST", "/cmd", {"op": "tabs"})
        assert st == 409
        e = _wait_events(spool_dir, 1)[0]
        assert e["exit_code"] == "1"
        p = json.loads(e["payload"])
        assert p["outcome"] == "ambiguous"
        assert p["op"] == "tabs"
    finally:
        a.stop(); b.stop(); srv.shutdown(); srv.server_close()


def test_cmd_timeout_emits_timeout_outcome(telemetry):
    spool_dir = telemetry
    srv, _ = _serve(cmd_timeout=0.5, poll_timeout=5.0)
    ext = FakeExtension(srv, swallow=True)
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        st, _ = _req(srv, "POST", "/cmd", {"op": "getHtml"})
        assert st == 504
        e = _wait_events(spool_dir, 1)[0]
        assert e["exit_code"] == "1"
        assert json.loads(e["payload"])["outcome"] == "timeout"
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


def test_emitted_event_is_metadata_only_no_page_content(telemetry):
    """PRIVACY: even when the command carries an eval source marker AND the
    result carries HTML, an eval return value, a screenshot data-URL, and a full
    URL with a secret query, NONE of that appears in the emitted event — only
    op/key/outcome/duration + the BARE domain."""
    spool_dir = telemetry
    srv, _ = _serve()
    marker_js = "SECRET_EVAL_MARKER_deadbeef()"
    secret_data_url = "data:image/png;base64,SCREENSHOTSECRETBYTESxyz=="
    secret_html = "<html>SECRET_HTML_BODY_TOKEN</html>"
    secret_query = "SUPERSECRETQUERY"

    def executor(cmd):
        # The extension echoes a rich result incl. sensitive fields + a full URL
        # with a query string. The command itself carried the eval marker.
        return {"url": f"https://mail.google.com/mail/u/0?token={secret_query}",
                "value": "EVAL_RETURN_SECRET", "html": secret_html,
                "dataUrl": secret_data_url}

    ext = FakeExtension(srv, executor=executor)
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        st, body = _req(srv, "POST", "/cmd", {"op": "eval", "js": marker_js})
        assert st == 200
        # Sanity: the sensitive material genuinely WAS in the round-trip.
        assert body["result"]["data"]["value"] == "EVAL_RETURN_SECRET"
        evs = _wait_events(spool_dir, 1)
        assert len(evs) == 1
        e = evs[0]
        # The fully-decoded event (every field incl. the b64 payload) leaks none.
        decoded_blob = "\t".join(f"{k}={v}" for k, v in e.items())
        # The RAW spool bytes too — defeats any base64-hidden copy.
        raw = _log_file(spool_dir).read_text()
        for secret in (marker_js, "SECRET_EVAL_MARKER", "EVAL_RETURN_SECRET",
                       secret_html, "SECRET_HTML_BODY_TOKEN", secret_data_url,
                       "SCREENSHOTSECRETBYTES", secret_query, "token="):
            assert secret not in decoded_blob, f"leaked in event: {secret}"
            assert secret not in raw, f"leaked in raw spool: {secret}"
        # What IS recorded: op + the bare domain, nothing else sensitive.
        p = json.loads(e["payload"])
        assert p["op"] == "eval"
        assert p["outcome"] == "ok"
        assert p["domain"] == "mail.google.com"   # host only — no path/query
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


def test_cmd_succeeds_when_emitter_raises(monkeypatch):
    """BEST-EFFORT: if the emitter itself raises, the command STILL returns its
    normal successful result — no 500, no exception into the handler."""
    class Boom:
        def emit(self, *a, **k):
            raise RuntimeError("spool exploded")

    monkeypatch.setattr(S, "_load_spool_emit", lambda: Boom())
    srv, _ = _serve()
    ext = FakeExtension(srv, executor=lambda c: {"url": "https://x.test"})
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        st, body = _req(srv, "POST", "/cmd", {"op": "getHtml"})
        assert st == 200
        assert body["ok"] is True
        assert body["result"]["data"]["url"] == "https://x.test"
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


def test_cmd_succeeds_when_spool_unwritable(telemetry, monkeypatch, tmp_path):
    """BEST-EFFORT: an unwritable spool path never breaks a command."""
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x")  # a regular file where a dir is expected
    monkeypatch.setenv("ACTIVITY_SPOOL_DIR", str(blocker / "spool"))
    srv, _ = _serve()
    ext = FakeExtension(srv, executor=lambda c: {"url": "https://x.test"})
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        st, body = _req(srv, "POST", "/cmd", {"op": "getHtml"})
        assert st == 200
        assert body["ok"] is True
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


def test_health_and_instances_do_not_emit(telemetry):
    """health / instances (and the extension's /poll) are noise — no events."""
    spool_dir = telemetry
    srv, _ = _serve(poll_timeout=5.0)
    ext = FakeExtension(srv, instance_id="a", label="alpha")
    ext.start()
    try:
        assert _wait_instances(srv, 1) is not None   # exercises /poll repeatedly
        _req(srv, "GET", "/health")
        _req(srv, "GET", "/instances")
        time.sleep(0.2)  # give any erroneous emit a chance to land
        assert _read_events(spool_dir) == []
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


def test_emit_cmd_event_noop_when_emitter_missing(monkeypatch):
    """emit_cmd_event is a silent no-op (never raises) when the emitter can't be
    loaded — the collector not being checked out just disables telemetry."""
    monkeypatch.setattr(S, "_load_spool_emit", lambda: None)
    S.emit_cmd_event(op="tabs", key="", outcome="ok", duration_ms=1)


def test_load_spool_emit_missing_path_returns_none(monkeypatch, tmp_path):
    monkeypatch.setattr(S, "_SPOOL_EMIT_PATH", tmp_path / "nope.py")
    monkeypatch.setattr(S, "_spool_emit_mod", None)
    monkeypatch.setattr(S, "_spool_emit_tried", False)
    assert S._load_spool_emit() is None
