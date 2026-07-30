"""Tests for the browser-bridge rendezvous server.

Fully HEADLESS: no Brave, no network beyond loopback. The extension is
simulated in-process by a `FakeExtension` thread that long-polls `/poll` (with
its instance identity headers), "executes" the op (echo), and POSTs to
`/result` (echoing its instanceId) — exercising the real HTTP round-trip, the
request↔reply id correlation, AND the multi-instance registry/routing.

Run: nix-shell -p python312Packages.pytest --run "pytest scripts/browser-bridge/tests"
"""
import base64
import hashlib
import json
import os
import re
import shutil
import stat
import struct
import subprocess
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

# Capture the REAL i3_available at import (the _disable_i3 autouse fixture stubs
# the module attribute to False for hermeticity; this reference lets the
# gating-logic test still exercise the genuine implementation).
_REAL_I3_AVAILABLE = S.i3_available

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


@pytest.fixture(autouse=True)
def _disable_i3(monkeypatch):
    """HERMETIC by default: neutralise host-side i3 foregrounding so no test
    accidentally fires a real `i3-msg` (the suite runs on Zach's graphical
    workbench where DISPLAY is set + i3-msg is on PATH). Tests that exercise the
    i3 path re-enable it explicitly (monkeypatch S.i3_available → True + a fake
    subprocess.run). With it False, `activate` reports i3:"skipped"."""
    monkeypatch.setattr(S, "i3_available", lambda: False)


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


def _serve(cmd_timeout=5.0, poll_timeout=5.0, registry=None):
    registry = registry if registry is not None else S.Registry()
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
                 swallow=False, active_url=None, active_title=None,
                 ext_version=None):
        super().__init__(daemon=True)
        self.srv = srv
        self.instance_id = instance_id
        self.label = label
        self.executor = executor or (lambda cmd: {"echo": cmd.get("op")})
        self.swallow = swallow  # pick up a command but never answer (→ timeout)
        self.active_url = active_url
        self.active_title = active_title
        self.ext_version = ext_version  # reported via X-Bridge-Ext-Version (or None)
        self.dispatched = []    # every command this fake picked up (for assertions)
        self._stopev = threading.Event()

    def _poll_headers(self):
        h = {S.HDR_INSTANCE_ID: self.instance_id}
        if self.label:
            h[S.HDR_LABEL] = urllib.parse.quote(self.label)
        if self.active_url:
            h[S.HDR_ACTIVE_URL] = urllib.parse.quote(self.active_url)
        if self.active_title:
            h[S.HDR_ACTIVE_TITLE] = urllib.parse.quote(self.active_title)
        if self.ext_version:
            h[S.HDR_EXT_VERSION] = urllib.parse.quote(self.ext_version)
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
            self.dispatched.append(cmd)
            if self.swallow:
                continue
            data = self.executor(cmd)
            # An executor may model an op-level FAILURE (the op threw in the page
            # / the target tab is gone) by returning {"__error__": "<msg>"} — the
            # fake then emits the extension's ok:false error envelope instead of a
            # success envelope. A plain dict stays a success `data` payload.
            if isinstance(data, dict) and "__error__" in data:
                envelope = {"id": cmd["id"], "ok": False,
                            "error": data["__error__"],
                            "instanceId": self.instance_id}
            else:
                envelope = {"id": cmd["id"], "ok": True, "data": data,
                            "instanceId": self.instance_id}
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
    when the extension answers them in reverse order.

    They target DIFFERENT tabs so both are concurrently in-flight — commands to
    the SAME tab now serialize FIFO (per-tab isolation), so only distinct tabs
    can be simultaneously outstanding. The transport's cid correlation is what's
    under test here and is unchanged."""
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
        # Distinct tabs (1 for A, 2 for B) → independent tab-FIFO turnstiles.
        results[tag] = reg.submit({"op": "eval", "tag": tag}, timeout=3.0,
                                  tab=(1 if tag == "A" else 2))

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
    assert set(S.ALLOWED_OPS) == {"getHtml", "text", "eval", "tabs", "nav",
                                  "screenshot", "open", "close",
                                  "frames", "click", "type", "key", "activate",
                                  "upload"}
    # `upload` is a dispatched, tab-scoped CDP op requiring selector + path.
    assert S.validate_command({"op": "upload", "selector": "#f",
                               "path": "/tmp/x"}) == ("upload", None)
    assert S.validate_command({"op": "upload", "path": "/tmp/x"})[1] \
        == "missing_field:selector"
    assert S.validate_command({"op": "upload", "selector": "#f"})[1] \
        == "missing_field:path"
    assert "upload" in S.TAB_SCOPED_OPS
    # `text` is a dispatched, tab-scoped, cheap-read op with NO required field.
    assert S.validate_command({"op": "text"}) == ("text", None)
    assert "text" in S.TAB_SCOPED_OPS
    # `activate` is a dispatched, tab-scoped op with NO required field (the server
    # injects the tabId; its optional waitMs is a passthrough, not a routing hint).
    assert S.validate_command({"op": "activate"}) == ("activate", None)
    assert S.validate_command({"op": "activate", "waitMs": 1000}) == ("activate", None)
    assert "activate" in S.TAB_SCOPED_OPS
    assert S.validate_command({"op": "tabs"}) == ("tabs", None)
    assert S.validate_command({"op": "eval", "js": "1"})[0] == "eval"
    assert S.validate_command({"op": "eval"})[1] == "missing_field:js"
    assert S.validate_command({"op": "bogus"})[1] == "unknown_op"
    assert S.validate_command("nope")[1] == "body_not_object"
    # open/close (dispatched) and release (server-side) are all accepted ops.
    assert S.validate_command({"op": "open"}) == ("open", None)
    assert S.validate_command({"op": "close"}) == ("close", None)
    assert S.validate_command({"op": "release"}) == ("release", None)
    assert "release" in S.SERVER_OPS and "release" not in S.ALLOWED_OPS


# --------------------------------------------------------------------------- #
# `text` op (B1 cheap read): dispatched + tab-scoped exactly like getHtml, its
# selector/maxBytes pass through to the extension, and its telemetry stays
# metadata-only (the page text is NEVER emitted). Headless — the FakeExtension
# echoes; the real innerText/normalization is unit-tested in protocol.test.mjs.
# --------------------------------------------------------------------------- #
def test_text_routes_to_owned_session_tab():
    """A session that `open`ed has its `text` op routed to ITS owned tabId — i.e.
    `text` is tab-scoped just like getHtml/eval/nav."""
    srv, _ = _serve()
    ext = FakeExtension(srv, instance_id="solo", label="only",
                        executor=_tab_echo_exec([101]))
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        _cmd(srv, {"op": "open"}, session="A")
        st, body = _cmd(srv, {"op": "text"}, session="A")
        assert st == 200
        assert body["result"]["data"]["tabId"] == 101, \
            "text must route to the session's owned tab (tab-scoped)"
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


def test_text_passes_selector_and_maxbytes_to_extension():
    """selector + maxBytes are forwarded verbatim to the extension command (they
    are not skill-side routing hints like target/tab)."""
    srv, _ = _serve()
    ext = FakeExtension(srv, instance_id="solo", label="only",
                        executor=lambda c: {"url": "https://x.test",
                                            "title": "X", "text": "hello"})
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        st, _ = _req(srv, "POST", "/cmd",
                     {"op": "text", "selector": "main", "maxBytes": 1024})
        assert st == 200
        text_cmds = [c for c in ext.dispatched if c["op"] == "text"]
        assert len(text_cmds) == 1
        assert text_cmds[0]["selector"] == "main"
        assert text_cmds[0]["maxBytes"] == 1024
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


def test_text_telemetry_is_metadata_only(telemetry):
    """PRIVACY: a `text` result carries page text, but telemetry emits ONLY the
    op + the bare domain — never the extracted text content."""
    spool_dir = telemetry
    srv, _ = _serve()
    secret_text = "SECRET_PAGE_TEXT_deadbeef the quick brown fox"
    ext = FakeExtension(
        srv, executor=lambda c: {"url": "https://news.ycombinator.com/",
                                 "title": "HN", "text": secret_text})
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        st, body = _req(srv, "POST", "/cmd", {"op": "text"})
        assert st == 200
        assert body["result"]["data"]["text"] == secret_text   # round-trip sanity
        e = _wait_events(spool_dir, 1)[0]
        p = json.loads(e["payload"])
        assert p["op"] == "text"
        assert p["outcome"] == "ok"
        assert p["domain"] == "news.ycombinator.com"
        assert e["text"] == "news.ycombinator.com"              # bare domain only
        raw = _log_file(spool_dir).read_text()
        assert "SECRET_PAGE_TEXT" not in raw, "text content leaked into telemetry"
        assert "quick brown fox" not in raw
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


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


# --------------------------------------------------------------------------- #
# Per-session tab isolation (the concurrent-session clobber fix)
#
# The clobber was SEMANTIC: every op targeted the ONE shared active tab, so two
# sessions' multi-step workflows interleaved on it. Fix: a session `open`s its own
# tab; the server records `(instance_key, session_id) -> tabId` and routes that
# session's tab-scoped ops to ITS tab. These are all headless — an in-process
# FakeExtension that ECHOES the dispatched tabId models "which tab did the op hit".
# --------------------------------------------------------------------------- #
def _wait_until(pred, timeout=3.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return True
        time.sleep(0.005)
    return pred()


def _tab_echo_exec(open_ids):
    """Executor: `open` returns the next id from `open_ids`; every other op echoes
    back the tabId the SERVER injected (None → active-tab fallback) + the op."""
    it = iter(open_ids)

    def _exec(cmd):
        if cmd["op"] == "open":
            return {"tabId": next(it), "url": cmd.get("url")}
        if cmd["op"] == "close":
            return {"closed": cmd.get("tabId")}
        return {"tabId": cmd.get("tabId"), "op": cmd["op"]}
    return _exec


def _cmd(srv, body, session=None):
    hdrs = {S.HDR_SESSION_ID: session} if session is not None else None
    return _req(srv, "POST", "/cmd", body, headers=hdrs)


# --- ownership: open records (instance,session)->tabId --------------------- #
def test_open_records_ownership_and_returns_tabid():
    srv, reg = _serve()
    ext = FakeExtension(srv, instance_id="solo", label="only",
                        executor=_tab_echo_exec([101]))
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        st, body = _cmd(srv, {"op": "open", "url": "https://a.test"}, session="A")
        assert st == 200
        assert body["result"]["data"]["tabId"] == 101
        assert reg.owners_snapshot() == {("only", "A"): 101}
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


def test_two_sessions_get_distinct_ownership():
    srv, reg = _serve()
    ext = FakeExtension(srv, instance_id="solo", label="only",
                        executor=_tab_echo_exec([101, 202]))
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        assert _cmd(srv, {"op": "open"}, session="A")[1]["result"]["data"]["tabId"] == 101
        assert _cmd(srv, {"op": "open"}, session="B")[1]["result"]["data"]["tabId"] == 202
        assert reg.owners_snapshot() == {("only", "A"): 101, ("only", "B"): 202}
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


# --- routing: a session's ops carry ITS owned tabId ------------------------ #
def test_owned_session_ops_route_to_its_tab():
    srv, _ = _serve()
    ext = FakeExtension(srv, instance_id="solo", label="only",
                        executor=_tab_echo_exec([101]))
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        _cmd(srv, {"op": "open"}, session="A")
        for op in ("getHtml", "eval", "nav"):
            b = {"op": op}
            if op == "eval":
                b["js"] = "1"
            if op == "nav":
                b["url"] = "https://x.test"
            st, body = _cmd(srv, b, session="A")
            assert st == 200, (op, body)
            assert body["result"]["data"]["tabId"] == 101, \
                f"{op} must route to A's owned tab 101"
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


def test_no_owned_tab_falls_back_to_active():
    """A session that never `open`ed → no tabId injected (extension uses the
    active tab). This preserves the one-shot 'read the tab I have open' path."""
    srv, _ = _serve()
    ext = FakeExtension(srv, instance_id="solo", label="only",
                        executor=lambda c: {"hasTab": ("tabId" in c), "op": c["op"]})
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        st, body = _cmd(srv, {"op": "getHtml"}, session="Z")   # session, but no open
        assert st == 200
        assert body["result"]["data"]["hasTab"] is False
        # And the dispatched command genuinely carried no tabId.
        assert all("tabId" not in c for c in ext.dispatched)
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


def test_explicit_tab_override():
    srv, _ = _serve()
    ext = FakeExtension(srv, instance_id="solo", label="only",
                        executor=_tab_echo_exec([]))
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        # No open at all, but --tab 77 forces the target.
        st, body = _req(srv, "POST", "/cmd", {"op": "getHtml", "tab": 77},
                        headers={S.HDR_SESSION_ID: "A"})
        assert st == 200
        assert body["result"]["data"]["tabId"] == 77
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


# --- `activate` op: tab-scoped foreground, waitMs passthrough, telemetry ---- #
def test_activate_routes_to_owned_session_tab():
    """`activate` is tab-scoped: a session that `open`ed has its activate routed
    to ITS owned tabId (own-tab enforcement — it can only ever foreground the tab
    the session owns, never an arbitrary one)."""
    srv, _ = _serve()
    ext = FakeExtension(srv, instance_id="solo", label="only",
                        executor=_tab_echo_exec([101]))
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        _cmd(srv, {"op": "open"}, session="A")
        st, body = _cmd(srv, {"op": "activate"}, session="A")
        assert st == 200
        assert body["result"]["data"]["tabId"] == 101, \
            "activate must route to the session's owned tab (tab-scoped, own-tab enforced)"
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


def test_activate_explicit_tab_override():
    """`--tab <id>` forces the activate target even with no owned tab."""
    srv, _ = _serve()
    ext = FakeExtension(srv, instance_id="solo", label="only",
                        executor=_tab_echo_exec([]))
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        st, body = _req(srv, "POST", "/cmd", {"op": "activate", "tab": 88},
                        headers={S.HDR_SESSION_ID: "A"})
        assert st == 200
        assert body["result"]["data"]["tabId"] == 88
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


def test_activate_forwards_waitms_to_extension():
    """`waitMs` is a passthrough command field (like text's selector/maxBytes) —
    forwarded verbatim to the extension, NOT stripped like target/tab."""
    srv, _ = _serve()
    ext = FakeExtension(srv, instance_id="solo", label="only",
                        executor=lambda c: {"tabId": c.get("tabId"),
                                            "url": "https://x.test", "status": "complete"})
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        st, _ = _req(srv, "POST", "/cmd", {"op": "activate", "waitMs": 1500})
        assert st == 200
        acts = [c for c in ext.dispatched if c["op"] == "activate"]
        assert len(acts) == 1
        assert acts[0]["waitMs"] == 1500
        # Routing hints never leak into the dispatched command.
        assert "target" not in acts[0] and "tab" not in acts[0]
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


def test_activate_telemetry_is_metadata_only(telemetry):
    """PRIVACY: an activate event emits ONLY op + the bare domain — never page
    content (activate returns no page content anyway; assert the metadata shape)."""
    spool_dir = telemetry
    srv, _ = _serve()
    ext = FakeExtension(
        srv, executor=lambda c: {"tabId": 5, "windowId": 1, "active": True,
                                 "status": "complete",
                                 "url": "https://model-benchmarking.civit.ai/run"})
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        st, _ = _req(srv, "POST", "/cmd", {"op": "activate"})
        assert st == 200
        e = _wait_events(spool_dir, 1)[0]
        p = json.loads(e["payload"])
        assert p["op"] == "activate"
        assert p["outcome"] == "ok"
        assert p["domain"] == "model-benchmarking.civit.ai"
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


# --- `activate` host-side i3 foregrounding (untrusted-title-safe) ---------- #
class _FakeProc:
    def __init__(self, returncode=0):
        self.returncode = returncode
        self.stdout = b""
        self.stderr = b""


def _enable_i3(monkeypatch, returncode=0, raise_exc=None):
    """Turn the i3 path ON and stub subprocess.run to capture the argv (never a
    real i3-msg). Returns the list the fake appends (argv, kwargs) to per call."""
    calls = []
    monkeypatch.setattr(S, "i3_available", lambda: True)
    # Deterministic resolved i3-msg path (used as argv[0]) regardless of host.
    monkeypatch.setattr(S, "_resolve_i3_msg",
                        lambda: _FAKE_I3_MSG)

    def _fake_run(argv, **kw):
        calls.append((argv, kw))
        if raise_exc is not None:
            raise raise_exc
        return _FakeProc(returncode)

    monkeypatch.setattr(S.subprocess, "run", _fake_run)
    return calls


# The absolute i3-msg path the _enable_i3 helper makes _resolve_i3_msg return, so
# argv[0] assertions are host-independent (matches _I3_MSG_FALLBACKS[0]).
_FAKE_I3_MSG = "/run/current-system/sw/bin/i3-msg"


def test_i3_focus_argv_hostile_title_is_safe():
    """KEY SECURITY TEST: a hostile, page-controlled title (i3-criteria breakout
    attempt, regex metachars, quotes, `;`, newlines, `$()`, backticks, very long)
    can NEVER break out of the `title="..."` value into an i3 command. The argv is
    a 2-element LIST (→ shell=False), constrained to class="Brave-browser", and
    the criteria has EXACTLY the 4 structural `"`, one `[`, one `]` — a hostile
    `"`/`]` would add more. Worst case is "wrong Brave window or none"."""
    hostile = (
        'evil"] exec xterm [title="pwn'      # try to close value → i3 `exec`
        + '; rm -rf ~ | cat `id` $(whoami)'  # shell metachars (irrelevant, no shell)
        + '\n\r\tmore .*+?[]{}^$\\ '          # control chars + regex metachars
        + "A" * 500                             # length bomb
    )
    argv = S.i3_focus_argv(hostile)
    assert isinstance(argv, list) and len(argv) == 2
    assert argv[0] == "i3-msg"
    crit = argv[1]
    # Structural integrity: exactly one criteria bracket pair + the 2 quoted
    # attribute values (class + title) → 4 double-quotes, and NO breakout.
    assert crit.startswith('[class="Brave-browser" title="')
    assert crit.endswith('"] focus')
    assert crit.count('"') == 4, crit
    assert crit.count("[") == 1 and crit.count("]") == 1, crit
    assert "\n" not in crit and "\r" not in crit and "\t" not in crit
    # `exec` cannot be a standalone i3 command — it only survives as inert text
    # INSIDE the quoted title value (still between title=" and "]).
    title_frag = crit[len('[class="Brave-browser" title="'):-len('"] focus')]
    assert '"' not in title_frag and "]" not in title_frag and "[" not in title_frag
    # Length is capped (the 500-char bomb cannot bloat the criteria unbounded).
    assert len(title_frag) <= len(re.escape("A" * S.I3_TITLE_MAX)) + 40


def test_i3_focus_argv_escapes_regex_metacharacters():
    """A plain title with regex metacharacters is re.escape'd so it matches
    literally (never alters i3's regex matching)."""
    argv = S.i3_focus_argv("Model Benchmarking")
    assert argv == ["i3-msg",
                    '[class="Brave-browser" title="%s"] focus'
                    % re.escape("Model Benchmarking")]


def test_i3_focus_argv_empty_title_returns_none():
    """No usable title (empty / only structural+control chars) → None → skip."""
    assert S.i3_focus_argv("") is None
    assert S.i3_focus_argv(None) is None
    assert S.i3_focus_argv('"[]\\\n\t ') is None


def test_activate_invokes_i3_msg_on_success(monkeypatch):
    """On a successful activate the server runs i3-msg with the ARGV LIST
    (shell=False), a class="Brave-browser" + re.escape'd-title criteria, and
    `focus`; the result reports i3:"applied"."""
    calls = _enable_i3(monkeypatch, returncode=0)
    srv, _ = _serve()
    ext = FakeExtension(srv, executor=lambda c: {
        "tabId": 5, "windowId": 1, "active": True, "status": "complete",
        "url": "https://model-benchmarking.civit.ai/run",
        "title": "Model Benchmarking"})
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        st, body = _req(srv, "POST", "/cmd", {"op": "activate"})
        assert st == 200
        assert body["result"]["data"]["i3"] == "applied"
        assert len(calls) == 1
        argv, kw = calls[0]
        # argv[0] is the RESOLVED ABSOLUTE i3-msg path (not the bare name) so the
        # call works even under the minimal systemd --user service PATH.
        assert argv[0] == _FAKE_I3_MSG
        assert argv[1] == ('[class="Brave-browser" title="%s"] focus'
                           % re.escape("Model Benchmarking"))
        assert kw.get("shell", False) is False  # NEVER a shell string
        assert kw.get("timeout")  # bounded
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


def test_activate_i3_skipped_when_unavailable(monkeypatch):
    """i3-msg unavailable (headless/non-i3) → SKIPPED gracefully; the activate
    still returns the Chrome-side result and NO subprocess is spawned."""
    # _disable_i3 (autouse) already forces i3_available False; also make any
    # subprocess.run a hard failure so a regression that skips the gate is caught.
    def _boom(*a, **k):
        raise AssertionError("i3-msg must NOT run when i3 is unavailable")
    monkeypatch.setattr(S.subprocess, "run", _boom)
    srv, _ = _serve()
    ext = FakeExtension(srv, executor=lambda c: {
        "tabId": 5, "active": True, "status": "complete",
        "url": "https://x.test", "title": "Anything"})
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        st, body = _req(srv, "POST", "/cmd", {"op": "activate"})
        assert st == 200
        assert body["result"]["data"]["i3"] == "skipped"
        assert body["result"]["data"]["tabId"] == 5  # Chrome-side result intact
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


def test_activate_i3_skipped_when_no_title(monkeypatch):
    """i3 available but the tab has no usable title → SKIPPED (no criteria to
    match); no subprocess is spawned."""
    calls = _enable_i3(monkeypatch, returncode=0)
    srv, _ = _serve()
    ext = FakeExtension(srv, executor=lambda c: {
        "tabId": 5, "active": True, "status": "complete", "url": "https://x.test"})
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        st, body = _req(srv, "POST", "/cmd", {"op": "activate"})
        assert st == 200
        assert body["result"]["data"]["i3"] == "skipped"
        assert calls == []
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


def test_activate_i3_failed_on_nonzero(monkeypatch):
    """i3-msg exits nonzero → non-fatal FAILED; the Chrome-side result still
    returns."""
    _enable_i3(monkeypatch, returncode=1)
    srv, _ = _serve()
    ext = FakeExtension(srv, executor=lambda c: {
        "tabId": 5, "active": True, "status": "complete",
        "url": "https://x.test", "title": "Some Tab"})
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        st, body = _req(srv, "POST", "/cmd", {"op": "activate"})
        assert st == 200
        assert body["result"]["data"]["i3"] == "failed"
        assert body["result"]["data"]["tabId"] == 5
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


def test_activate_i3_failed_on_timeout(monkeypatch):
    """i3-msg timing out → non-fatal FAILED (the call is timeout-bounded)."""
    _enable_i3(monkeypatch,
               raise_exc=subprocess.TimeoutExpired(cmd="i3-msg", timeout=1.5))
    srv, _ = _serve()
    ext = FakeExtension(srv, executor=lambda c: {
        "tabId": 5, "active": True, "status": "complete",
        "url": "https://x.test", "title": "Some Tab"})
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        st, body = _req(srv, "POST", "/cmd", {"op": "activate"})
        assert st == 200
        assert body["result"]["data"]["i3"] == "failed"
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


def test_activate_i3_telemetry_stays_metadata_only(telemetry, monkeypatch):
    """Even when i3 focusing is APPLIED, the activate telemetry event carries NO
    page title — only op / outcome / bare domain (the title can hold page
    content; the i3 step must not leak it into activity.events)."""
    spool_dir = telemetry
    _enable_i3(monkeypatch, returncode=0)
    srv, _ = _serve()
    ext = FakeExtension(srv, executor=lambda c: {
        "tabId": 5, "active": True, "status": "complete",
        "url": "https://model-benchmarking.civit.ai/run",
        "title": "SECRET PAGE CONTENT IN TITLE"})
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        st, _ = _req(srv, "POST", "/cmd", {"op": "activate"})
        assert st == 200
        e = _wait_events(spool_dir, 1)[0]
        p = json.loads(e["payload"])
        assert p["op"] == "activate" and p["outcome"] == "ok"
        assert p["domain"] == "model-benchmarking.civit.ai"
        # No title anywhere in the emitted event.
        assert "SECRET" not in json.dumps(e)
        assert "title" not in p
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


def test_i3_available_requires_display_and_i3msg(monkeypatch):
    """i3_available gates on BOTH a graphical session (DISPLAY) and a resolvable
    i3-msg — either missing → False (skip). Exercises the REAL implementation
    (the autouse fixture stubs the module attribute for hermeticity)."""
    monkeypatch.setattr(S.shutil, "which",
                        lambda n: "/run/current-system/sw/bin/i3-msg")
    monkeypatch.delenv("DISPLAY", raising=False)
    assert _REAL_I3_AVAILABLE() is False          # no DISPLAY → False
    monkeypatch.setenv("DISPLAY", ":0")
    assert _REAL_I3_AVAILABLE() is True           # DISPLAY + i3-msg → True
    # i3-msg NOT on PATH *and* no absolute fallback exists → False. Both the
    # which() lookup AND the absolute-fallback probe must miss (else the resolver
    # finds a real /run/current-system/... i3-msg on the graphical test host).
    monkeypatch.setattr(S.shutil, "which", lambda n: None)
    monkeypatch.setattr(S.os.path, "exists", lambda p: False)
    assert _REAL_I3_AVAILABLE() is False          # i3-msg absent → False


def test_resolve_i3_msg_prefers_which(monkeypatch):
    """_resolve_i3_msg returns the PATH-resolved i3-msg when one is on PATH."""
    monkeypatch.setattr(S.shutil, "which", lambda n: "/usr/bin/i3-msg")
    assert S._resolve_i3_msg() == "/usr/bin/i3-msg"


def test_resolve_i3_msg_falls_back_to_absolute_path(monkeypatch):
    """THE FIX: i3-msg is NOT on PATH (the minimal systemd --user service PATH),
    but a well-known absolute i3-msg EXISTS and is executable → resolve to it."""
    monkeypatch.setattr(S.shutil, "which", lambda n: None)
    fallback = S._I3_MSG_FALLBACKS[0]
    monkeypatch.setattr(S.os.path, "exists", lambda p: p == fallback)
    monkeypatch.setattr(S.os, "access", lambda p, mode: p == fallback)
    assert S._resolve_i3_msg() == fallback


def test_resolve_i3_msg_none_when_nothing_found(monkeypatch):
    """Nothing on PATH and no absolute fallback exists → None (→ i3 skipped)."""
    monkeypatch.setattr(S.shutil, "which", lambda n: None)
    monkeypatch.setattr(S.os.path, "exists", lambda p: False)
    assert S._resolve_i3_msg() is None


def test_i3_available_uses_absolute_fallback_off_path(monkeypatch):
    """i3_available is TRUE when DISPLAY is set and i3-msg resolves ONLY via the
    absolute fallback (which() misses) — the exact in-service condition the bug
    hit. This is what un-breaks `activate` under the minimal service PATH."""
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setattr(S.shutil, "which", lambda n: None)
    fallback = S._I3_MSG_FALLBACKS[0]
    monkeypatch.setattr(S.os.path, "exists", lambda p: p == fallback)
    monkeypatch.setattr(S.os, "access", lambda p, mode: p == fallback)
    assert _REAL_I3_AVAILABLE() is True
    # DISPLAY absent → skipped regardless of a resolvable i3-msg.
    monkeypatch.delenv("DISPLAY", raising=False)
    assert _REAL_I3_AVAILABLE() is False


# --- THE BUG: two sessions interleave without clobbering ------------------- #
def test_two_sessions_interleaved_never_clobber():
    """Session A and B each own a tab; interleaved nav+read ops each dispatch
    against their OWN tabId — the clobber (A reads B's page) cannot happen."""
    srv, _ = _serve()
    ext = FakeExtension(srv, instance_id="solo", label="only",
                        executor=_tab_echo_exec([101, 202]))
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        _cmd(srv, {"op": "open", "url": "https://a.test"}, session="A")
        _cmd(srv, {"op": "open", "url": "https://b.test"}, session="B")
        # Interleave: A nav, B nav, A read, B read.
        assert _cmd(srv, {"op": "nav", "url": "https://a2.test"}, session="A")[1]["result"]["data"]["tabId"] == 101
        assert _cmd(srv, {"op": "nav", "url": "https://b2.test"}, session="B")[1]["result"]["data"]["tabId"] == 202
        assert _cmd(srv, {"op": "getHtml"}, session="A")[1]["result"]["data"]["tabId"] == 101
        assert _cmd(srv, {"op": "getHtml"}, session="B")[1]["result"]["data"]["tabId"] == 202
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


# --- close / release ------------------------------------------------------- #
def test_close_drops_ownership_and_dispatches_remove():
    srv, reg = _serve()
    ext = FakeExtension(srv, instance_id="solo", label="only",
                        executor=_tab_echo_exec([101]))
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        _cmd(srv, {"op": "open"}, session="A")
        assert reg.owners_snapshot() == {("only", "A"): 101}
        st, body = _cmd(srv, {"op": "close"}, session="A")
        assert st == 200
        assert body["result"]["data"]["closed"] == 101
        assert reg.owners_snapshot() == {}
        # A close-shaped command (op=close, tabId=owned) was dispatched.
        close_cmds = [c for c in ext.dispatched if c["op"] == "close"]
        assert len(close_cmds) == 1 and close_cmds[0]["tabId"] == 101
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


def test_close_without_owned_tab_409_no_owned_tab():
    srv, _ = _serve()
    ext = FakeExtension(srv, instance_id="solo", label="only")
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        st, body = _cmd(srv, {"op": "close"}, session="A")   # never opened
        assert st == 409
        assert body["error"] == "no_owned_tab"
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


def test_release_drops_ownership_without_dispatch():
    srv, reg = _serve()
    ext = FakeExtension(srv, instance_id="solo", label="only",
                        executor=_tab_echo_exec([101]))
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        _cmd(srv, {"op": "open"}, session="A")
        assert reg.owners_snapshot() == {("only", "A"): 101}
        st, body = _cmd(srv, {"op": "release"}, session="A")
        assert st == 200
        assert body["result"]["data"]["released"] == 1
        assert reg.owners_snapshot() == {}
        # release is server-side: the extension NEVER saw a release/close command.
        assert all(c["op"] not in ("release", "close") for c in ext.dispatched)
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


def test_tabs_annotates_owned_tab_id():
    srv, _ = _serve()
    ext = FakeExtension(srv, instance_id="solo", label="only",
                        executor=_tab_echo_exec([101]))
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        _cmd(srv, {"op": "open"}, session="A")
        st, body = _cmd(srv, {"op": "tabs"}, session="A")
        assert st == 200
        assert body["result"]["data"]["ownedTabId"] == 101
        # A different session owns nothing here → None.
        st, body = _cmd(srv, {"op": "tabs"}, session="B")
        assert body["result"]["data"]["ownedTabId"] is None
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


# --- backward compatibility ------------------------------------------------ #
def test_backward_compat_no_session_no_open_unchanged():
    """A SINGLE session with NO session header and NO open behaves EXACTLY as
    before: active-tab dispatch, no tabId injected."""
    srv, _ = _serve()
    ext = FakeExtension(srv, instance_id="solo", label="only",
                        executor=lambda c: {"hasTab": ("tabId" in c)})
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        st, body = _req(srv, "POST", "/cmd", {"op": "getHtml"})   # no X-Session-Id
        assert st == 200
        assert body["result"]["data"]["hasTab"] is False
        assert all("tabId" not in c for c in ext.dispatched)
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


# --- security: the session id is routing-only, never trusted for auth ------ #
def test_cmd_with_session_still_enforces_auth_and_host():
    srv, _ = _serve()
    try:
        # Wrong bearer + a session header → still 401 (session never grants auth).
        st, body = _req(srv, "POST", "/cmd", {"op": "getHtml"}, token="nope",
                        headers={S.HDR_SESSION_ID: "A"})
        assert st == 401 and body["error"] == "unauthorized"
        # Bad Host + a session header → still 403.
        st, body = _req(srv, "POST", "/cmd", {"op": "getHtml"},
                        host="evil.example.com",
                        headers={S.HDR_SESSION_ID: "A"})
        assert st == 403 and body["error"] == "bad_host"
    finally:
        srv.shutdown(); srv.server_close()


# --- per-tab FIFO serialization (Registry-level, deterministic) ------------ #
def _register_live(reg, iid="solo", key="one"):
    """Register a live instance via one idle poll (leaves last_poll recent so it
    counts live within CONNECT_STALE_S) and return its Instance."""
    reg.poll(iid, key, 0.01)
    with reg._cond:
        return reg._instances[key]


def test_same_tab_commands_serialize_fifo():
    """Two commands targeting the SAME tab are granted in arrival order: the
    second does not even enqueue until the first completes."""
    reg = S.Registry()
    inst = _register_live(reg)

    r1 = {}

    def sub1():
        r1["v"] = reg.submit({"op": "getHtml"}, 5.0, session_id="s1", tab=5)

    threading.Thread(target=sub1, daemon=True).start()
    assert _wait_until(lambda: len(inst.waiters) == 1), "cmd1 never enqueued"
    cid1 = next(iter(inst.waiters))

    # A second command to the SAME tab (5) must be FIFO-gated (not enqueued yet).
    threading.Thread(
        target=lambda: reg.submit({"op": "getHtml"}, 5.0, session_id="s2", tab=5),
        daemon=True).start()
    time.sleep(0.15)
    assert inst.waiters == {cid1}, "second same-tab command must NOT enqueue while cmd1 is in-flight"

    # Complete cmd1 → its turn is released → cmd2 enqueues.
    reg.deliver_result(cid1, {"id": cid1, "ok": True, "data": {}},
                       instance_id="solo")
    assert _wait_until(lambda: inst.waiters and next(iter(inst.waiters)) != cid1), \
        "cmd2 must enqueue once cmd1 completed"
    cid2 = next(iter(inst.waiters))
    reg.deliver_result(cid2, {"id": cid2, "ok": True, "data": {}},
                       instance_id="solo")


def test_different_tab_commands_do_not_block():
    """A command to a DIFFERENT tab enqueues immediately, even while another tab's
    command is in flight (no false serialization across tabs)."""
    reg = S.Registry()
    inst = _register_live(reg)

    def sub1():
        reg.submit({"op": "getHtml"}, 5.0, session_id="s1", tab=5)

    threading.Thread(target=sub1, daemon=True).start()
    assert _wait_until(lambda: len(inst.waiters) == 1)
    cid1 = next(iter(inst.waiters))

    # Different tab (6) — must enqueue right away (→ 2 waiters), not wait for cid1.
    threading.Thread(
        target=lambda: reg.submit({"op": "getHtml"}, 5.0, session_id="s2", tab=6),
        daemon=True).start()
    assert _wait_until(lambda: len(inst.waiters) == 2), \
        "a different-tab command must not be blocked by cmd1"
    for cid in list(inst.waiters):
        reg.deliver_result(cid, {"id": cid, "ok": True, "data": {}},
                           instance_id="solo")


def test_fifo_queued_command_respects_cmd_timeout():
    """A command queued behind an in-flight one on the same tab still times out
    at cmd_timeout (a queued command can never block forever)."""
    reg = S.Registry()
    inst = _register_live(reg)

    def sub1():
        # Never delivered → holds the tab turn until ITS own timeout.
        try:
            reg.submit({"op": "getHtml"}, 5.0, session_id="s1", tab=5)
        except S.BridgeTimeout:
            pass

    threading.Thread(target=sub1, daemon=True).start()
    assert _wait_until(lambda: len(inst.waiters) == 1)

    r2 = {}

    def sub2():
        try:
            reg.submit({"op": "getHtml"}, 0.3, session_id="s2", tab=5)
        except Exception as e:  # noqa: BLE001
            r2["err"] = type(e).__name__

    t2 = threading.Thread(target=sub2, daemon=True)
    t2.start()
    t2.join(3)
    assert r2.get("err") == "BridgeTimeout", \
        "a FIFO-queued command must still honour cmd_timeout"


# --- TTL reclaim: released, NOT closed ------------------------------------- #
def test_owner_ttl_reclaims_without_closing(monkeypatch):
    """Ownership idle past the TTL is RELEASED (mapping dropped) using an injected
    clock — never a real Date.now(). Reclaim must NOT dispatch a close."""
    clock = [1000.0]
    reg = S.Registry(clock=lambda: clock[0], owner_ttl=10.0)
    with reg._cond:
        reg._owners[("k", "s")] = {"tab_id": 42, "last_seen": clock[0]}
    assert reg.owners_snapshot() == {("k", "s"): 42}

    events = []
    monkeypatch.setattr(S, "log", lambda ev, **kw: events.append(ev))
    clock[0] += 11.0                      # advance past the idle TTL
    assert reg.owners_snapshot() == {}    # reclaimed on the next touch
    assert "owner_reclaim" in events


def test_owner_ttl_reclaim_falls_back_to_active_not_close():
    """After a session's ownership expires, its next op routes with NO injected
    tabId (active-tab fallback) and NO close is ever dispatched — the real tab is
    left open. Registry-level with an injected clock (no real Date.now())."""
    clock = [1000.0]
    reg = S.Registry(clock=lambda: clock[0], owner_ttl=10.0)

    dispatched = []
    stop = threading.Event()

    def poller():
        while not stop.is_set():
            cmd = reg.poll("solo", "one", 0.05)
            if cmd is not None and cmd is not S.SUPERSEDED:
                dispatched.append(cmd)
                reg.deliver_result(cmd["id"], {"id": cmd["id"], "ok": True,
                                               "data": {}}, instance_id="solo")

    threading.Thread(target=poller, daemon=True).start()
    try:
        assert _wait_until(lambda: len(reg.snapshot()) == 1)
        # Model an owned tab, then let it go idle past the TTL.
        with reg._cond:
            reg._owners[("one", "S")] = {"tab_id": 55, "last_seen": clock[0]}
        clock[0] += 11.0                  # past owner TTL (10), within stale (40)
        reg.submit({"op": "getHtml"}, 3.0, session_id="S")   # no tab arg
        assert reg.owners_snapshot() == {}                    # ownership reclaimed
        assert dispatched, "the op was never dispatched"
        assert all("tabId" not in c for c in dispatched)      # active-tab fallback
        assert all(c["op"] != "close" for c in dispatched)    # tab NOT closed
    finally:
        stop.set()


# --------------------------------------------------------------------------- #
# Fix 1 — self-heal when an owned tab is gone
#
# When the user manually closes an owned BACKGROUND tab, the next tab-scoped op
# dispatches the stale tabId → the extension returns `owned_tab_gone` (ok:false).
# The session must DROP its ownership so the next command self-heals to the
# active-tab fallback (instead of staying wedged to the dead tab for OWNER_TTL),
# and `close` must clear the mapping even when the remove reports the tab gone.
# --------------------------------------------------------------------------- #
def _tab_gone_exec(open_ids, gone):
    """Executor: `open` hands out the next id; a tab-scoped op whose injected
    tabId is in the mutable `gone` set returns an `owned_tab_gone` error envelope;
    otherwise it echoes the injected tabId (None → active-tab fallback)."""
    it = iter(open_ids)

    def _exec(cmd):
        if cmd["op"] == "open":
            return {"tabId": next(it), "url": cmd.get("url")}
        tid = cmd.get("tabId")
        if tid is not None and tid in gone:
            return {"__error__": "owned_tab_gone"}
        if cmd["op"] == "close":
            return {"closed": tid}
        return {"tabId": tid, "op": cmd["op"]}
    return _exec


def test_is_tab_gone_helper():
    assert S._is_tab_gone("owned_tab_gone") is True
    assert S._is_tab_gone("No tab with id: 42.") is True   # chrome's raw message
    assert S._is_tab_gone("something_else") is False
    assert S._is_tab_gone(None) is False
    assert S._is_tab_gone(123) is False


def test_owned_tab_gone_drops_ownership_and_self_heals():
    """A tab-scoped op that fails with `owned_tab_gone` for the session's OWNED
    tab drops the mapping; the NEXT op falls back to the active tab (no tabId)."""
    srv, reg = _serve()
    gone = set()
    ext = FakeExtension(srv, instance_id="solo", label="only",
                        executor=_tab_gone_exec([101], gone))
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        _cmd(srv, {"op": "open"}, session="A")
        assert reg.owners_snapshot() == {("only", "A"): 101}
        # The user closes tab 101 out-of-band.
        gone.add(101)
        st, body = _cmd(srv, {"op": "getHtml"}, session="A")
        assert st == 200                          # HTTP ok; op-level failure
        assert body["result"]["ok"] is False
        assert body["result"]["error"] == "owned_tab_gone"
        assert reg.owners_snapshot() == {}        # ownership self-healed (dropped)
        # Next op now falls back to the active tab (no tabId injected).
        st, body = _cmd(srv, {"op": "getHtml"}, session="A")
        assert st == 200 and body["result"]["ok"] is True
        assert body["result"]["data"]["tabId"] is None
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


def test_close_drops_ownership_even_when_tab_already_gone():
    """`close` clears the mapping UNCONDITIONALLY — even when the extension
    reports the tab already gone (remove → ok:false). Without this, a tab the
    user closed out-of-band could never be cleared and wedged the session."""
    srv, reg = _serve()
    gone = set()
    ext = FakeExtension(srv, instance_id="solo", label="only",
                        executor=_tab_gone_exec([101], gone))
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        _cmd(srv, {"op": "open"}, session="A")
        assert reg.owners_snapshot() == {("only", "A"): 101}
        gone.add(101)                              # closed out-of-band
        st, body = _cmd(srv, {"op": "close"}, session="A")
        assert st == 200
        assert body["result"]["ok"] is False       # remove reported it gone
        assert reg.owners_snapshot() == {}          # mapping cleared regardless
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


def test_explicit_tab_gone_does_not_evict_owned_mapping():
    """An explicit --tab to a DIFFERENT (gone) tab must NOT evict the session's
    healthy owned mapping — only the session's OWN owned tab going gone self-heals."""
    srv, reg = _serve()
    gone = {999}
    ext = FakeExtension(srv, instance_id="solo", label="only",
                        executor=_tab_gone_exec([101], gone))
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        _cmd(srv, {"op": "open"}, session="A")      # owns 101
        # Target an unrelated, already-gone tab 999 via --tab.
        st, body = _req(srv, "POST", "/cmd", {"op": "getHtml", "tab": 999},
                        headers={S.HDR_SESSION_ID: "A"})
        assert st == 200 and body["result"]["ok"] is False
        assert body["result"]["error"] == "owned_tab_gone"
        # The session's own tab (101) is untouched.
        assert reg.owners_snapshot() == {("only", "A"): 101}
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


# --------------------------------------------------------------------------- #
# Fix 2 — a double `open` must not orphan a real tab
#
# A second `open` from a session used to overwrite its ownership with a brand-new
# tabId, orphaning the FIRST real tab (no ownership → never closed → leaked). The
# server now passes the owned tabId as `reuseTabId`; the extension returns the
# SAME tab when it is still live (idempotent), and only opens fresh when gone.
# --------------------------------------------------------------------------- #
def _open_reuse_exec(new_ids, live):
    """Model the SW `open` reuse logic: honour `reuseTabId` when that tab is still
    in the mutable `live` set (idempotent), else create the next new id. `close`
    removes from `live`; other ops echo their injected tabId."""
    it = iter(new_ids)

    def _exec(cmd):
        if cmd["op"] == "open":
            reuse = cmd.get("reuseTabId")
            if reuse is not None and reuse in live:
                return {"tabId": reuse, "url": cmd.get("url"), "reused": True}
            tid = next(it)
            live.add(tid)
            return {"tabId": tid, "url": cmd.get("url")}
        if cmd["op"] == "close":
            live.discard(cmd.get("tabId"))
            return {"closed": cmd.get("tabId")}
        return {"tabId": cmd.get("tabId"), "op": cmd["op"]}
    return _exec


def test_double_open_is_idempotent_no_orphan():
    """A second `open` returns the EXISTING owned tab (reused) — no second real
    tab is created, so the first tab is never orphaned/leaked."""
    srv, reg = _serve()
    live = set()
    ext = FakeExtension(srv, instance_id="solo", label="only",
                        executor=_open_reuse_exec([101, 202], live))
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        st, body = _cmd(srv, {"op": "open"}, session="A")
        assert body["result"]["data"]["tabId"] == 101
        assert live == {101}
        # Second open by the SAME session → the server injects reuseTabId=101 and
        # the extension returns the SAME live tab (no new 202 tab created).
        st, body = _cmd(srv, {"op": "open"}, session="A")
        assert st == 200
        assert body["result"]["data"]["tabId"] == 101
        assert body["result"]["data"].get("reused") is True
        assert live == {101}, "a second real tab was created → orphaned/leaked"
        assert reg.owners_snapshot() == {("only", "A"): 101}
        # The server injected the reuse hint on the second open.
        opens = [c for c in ext.dispatched if c["op"] == "open"]
        assert opens[1].get("reuseTabId") == 101
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


def test_open_after_owned_tab_gone_opens_fresh():
    """If the previously-owned tab is GONE, `open` creates a fresh tab and the
    ownership updates to it (the stale mapping is replaced, not reused)."""
    srv, reg = _serve()
    live = set()
    ext = FakeExtension(srv, instance_id="solo", label="only",
                        executor=_open_reuse_exec([101, 202], live))
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        _cmd(srv, {"op": "open"}, session="A")       # owns 101
        live.discard(101)                            # user closed it out-of-band
        st, body = _cmd(srv, {"op": "open"}, session="A")
        assert st == 200
        assert body["result"]["data"]["tabId"] == 202    # fresh tab
        assert body["result"]["data"].get("reused") is None
        assert reg.owners_snapshot() == {("only", "A"): 202}
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


# --------------------------------------------------------------------------- #
# Fix 3 — a malformed `tab` from a raw API caller is a clean 400, never a 500
# --------------------------------------------------------------------------- #
def test_coerce_tab_helper():
    assert S._coerce_tab(5) == (5, None)
    assert S._coerce_tab(0) == (0, None)
    assert S._coerce_tab("77") == (77, None)
    assert S._coerce_tab([1, 2]) == (None, "bad_tab")
    assert S._coerce_tab({"x": 1}) == (None, "bad_tab")
    assert S._coerce_tab(True) == (None, "bad_tab")      # bool is not a tab id
    assert S._coerce_tab(1.5) == (None, "bad_tab")
    assert S._coerce_tab(-3) == (None, "bad_tab")
    assert S._coerce_tab("x") == (None, "bad_tab")


def test_malformed_tab_returns_400_bad_tab_not_500():
    """A raw token-holder POSTing an unhashable `tab` (list/dict) gets a clean
    400 bad_tab — never an uncaught TypeError → 500."""
    srv, _ = _serve()
    ext = FakeExtension(srv, instance_id="solo", label="only")
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        for bad in ([1, 2], {"x": 1}, True, 1.5, "nope"):
            st, body = _req(srv, "POST", "/cmd", {"op": "getHtml", "tab": bad})
            assert st == 400, f"tab={bad!r} → {st}"
            assert body["error"] == "bad_tab"
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


def test_numeric_string_tab_is_coerced_and_routes():
    """A valid numeric-string `tab` is coerced to an int and routes normally."""
    srv, _ = _serve()
    ext = FakeExtension(srv, instance_id="solo", label="only",
                        executor=lambda c: {"tabId": c.get("tabId")})
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        st, body = _req(srv, "POST", "/cmd", {"op": "getHtml", "tab": "5"},
                        headers={S.HDR_SESSION_ID: "A"})
        assert st == 200
        assert body["result"]["data"]["tabId"] == 5
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


# --------------------------------------------------------------------------- #
# Fix 4 — explicit --tab overrides owned-tab routing (the subagent escape hatch)
#
# Sibling subagents of one parent share a session id (CLAUDE_CODE_SESSION_ID +
# $TMUX_PANE are inherited, and no subagent-unique env var exists), so they would
# own the SAME tab. The robust isolation is explicit tab handles: each driver
# `open`s and then threads its OWN --tab on every op. This asserts --tab fully
# overrides the session-ownership mapping so two same-session drivers never collide.
# --------------------------------------------------------------------------- #
def test_explicit_tab_overrides_owned_mapping():
    srv, reg = _serve()
    ext = FakeExtension(srv, instance_id="solo", label="only",
                        executor=_tab_echo_exec([101]))
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        _cmd(srv, {"op": "open"}, session="A")          # session A owns tab 101
        # An explicit --tab 999 must target 999, NOT the owned 101.
        st, body = _req(srv, "POST", "/cmd", {"op": "getHtml", "tab": 999},
                        headers={S.HDR_SESSION_ID: "A"})
        assert st == 200
        assert body["result"]["data"]["tabId"] == 999, \
            "--tab must override the session's owned-tab routing"
        # The override does not disturb the owned mapping.
        assert reg.owners_snapshot() == {("only", "A"): 101}
        # And with no --tab the same session still routes to its owned tab.
        st, body = _cmd(srv, {"op": "getHtml"}, session="A")
        assert body["result"]["data"]["tabId"] == 101
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


# --------------------------------------------------------------------------- #
# Fix 5 — per-instance concurrency backstop (rate limit + queue-depth cap)
#
# Motivation: an audit found a 44,061-event storm (43,991 evals in one hour,
# ~13/sec sustained) that saturated the SINGLE serial extension connection with
# NO backpressure (latency 10ms→5.5s). The server now enforces, PER-INSTANCE, a
# token-bucket rate limit + a pending-command depth cap; an over-limit /cmd is
# rejected with HTTP 429 (caller-visible backpressure). The admission decision
# (`_admit_locked`) is unit-tested directly with an INJECTED clock (scripts forbid
# real-time nondeterminism), plus HTTP round-trips prove the 429 + telemetry.
# --------------------------------------------------------------------------- #
def _mk_instance(reg, key="k", burst=0.0, now=0.0):
    """A bare Instance for direct _admit_locked unit tests (no HTTP/registry)."""
    return S.Instance(key, key, "", now, burst=burst)


def _instance_pending(reg):
    with reg._cond:
        return sum(i.pending for i in reg._instances.values())


# --- token bucket: burst passes, then rate_limited (frozen clock) ---------- #
def test_admit_token_bucket_burst_then_rate_limited():
    """Within `burst`, admits pass; the next over the empty bucket → rate_limited.
    Frozen clock ⇒ no refill ⇒ deterministic."""
    clock = [100.0]
    reg = S.Registry(clock=lambda: clock[0], rate_per_sec=5.0, burst=3,
                     max_queue=1000)
    inst = _mk_instance(reg, burst=3, now=clock[0])
    with reg._cond:
        for i in range(3):
            assert reg._admit_locked(inst) is None, f"burst slot {i} must admit"
            inst.pending += 1              # caller (submit) claims the slot
        verdict = reg._admit_locked(inst)
    assert verdict is not None
    reason, retry_after = verdict
    assert reason == "rate_limited"
    assert retry_after > 0                 # a positive Retry-After-style hint
    assert inst.pending == 3               # ONLY the 3 admitted bumped pending


# --- token bucket refills over (fake) time → dispatches resume ------------- #
def test_admit_token_bucket_refills_over_time():
    clock = [100.0]
    reg = S.Registry(clock=lambda: clock[0], rate_per_sec=5.0, burst=2,
                     max_queue=1000)
    inst = _mk_instance(reg, burst=2, now=clock[0])
    with reg._cond:
        assert reg._admit_locked(inst) is None
        assert reg._admit_locked(inst) is None
        assert reg._admit_locked(inst)[0] == "rate_limited"   # bucket empty
        clock[0] += 0.2                                       # +1 token @5/sec
        assert reg._admit_locked(inst) is None                # resumed
        assert reg._admit_locked(inst)[0] == "rate_limited"   # empty again
        clock[0] += 100.0                                     # long idle
        # Refill is CAPPED at `burst` (2) — no unbounded accrual.
        assert reg._admit_locked(inst) is None
        assert reg._admit_locked(inst) is None
        assert reg._admit_locked(inst)[0] == "rate_limited"


# --- queue-depth cap: fill to MAX_QUEUE → queue_full; drain resumes -------- #
def test_admit_queue_depth_cap():
    """Rate limit disabled so ONLY the depth cap is exercised."""
    reg = S.Registry(rate_per_sec=0, max_queue=2)
    inst = _mk_instance(reg, burst=0)
    with reg._cond:
        # The caller (submit) bumps pending on each admit; the depth cap reads it.
        assert reg._admit_locked(inst) is None    # pending 1
        inst.pending += 1
        assert reg._admit_locked(inst) is None    # pending 2
        inst.pending += 1
        verdict = reg._admit_locked(inst)         # 2 >= cap 2 → queue_full
        assert verdict is not None and verdict[0] == "queue_full"
        assert inst.pending == 2                  # a rejection does NOT bump pending
        inst.pending -= 1                         # a command completes (drains)
        assert reg._admit_locked(inst) is None    # slot freed → resumes


# --- strict per-instance isolation ----------------------------------------- #
def test_admit_is_strictly_per_instance():
    """Throttling instance A must NOT throttle instance B — independent buckets
    AND independent pending."""
    reg = S.Registry(rate_per_sec=5.0, burst=1, max_queue=1)
    a = _mk_instance(reg, key="a", burst=1)
    b = _mk_instance(reg, key="b", burst=1)
    with reg._cond:
        assert reg._admit_locked(a) is None                       # a: token+slot
        a.pending += 1                                            # caller claims slot
        assert reg._admit_locked(a)[0] in ("rate_limited", "queue_full")
        assert reg._admit_locked(b) is None                       # b unaffected
        b.pending += 1
        assert a.pending == 1 and b.pending == 1


# --- disable path (rate=0 AND max_queue=0) never throttles ----------------- #
def test_admit_disabled_never_throttles():
    reg = S.Registry(rate_per_sec=0, max_queue=0)
    inst = _mk_instance(reg, burst=0)
    with reg._cond:
        for _ in range(1000):
            assert reg._admit_locked(inst) is None
            inst.pending += 1                     # caller (submit) claims the slot
    assert inst.pending == 1000


# --- a rejected submit leaves NO turnstile/waiter residue (no deadlock) ---- #
def test_rate_limited_submit_leaves_no_turnstile_residue():
    """A rejected submit raises RateLimited IMMEDIATELY and leaves no residue —
    no tab-queue ticket, no waiter, pending balanced — so it can neither wedge
    the FIFO turnstile nor leak a queue slot."""
    reg = S.Registry(rate_per_sec=0.001, burst=1, max_queue=1000)  # ~no refill
    inst = _register_live(reg)
    stop = threading.Event()

    def deliverer():
        while not stop.is_set():
            cmd = reg.poll("solo", "one", 0.05)
            if cmd is not None and cmd is not S.SUPERSEDED:
                reg.deliver_result(cmd["id"], {"id": cmd["id"], "ok": True,
                                               "data": {}}, instance_id="solo")

    threading.Thread(target=deliverer, daemon=True).start()
    try:
        reg.submit({"op": "getHtml"}, 3.0, session_id="s1", tab=5)  # spends token
        with pytest.raises(S.RateLimited) as ei:
            reg.submit({"op": "getHtml"}, 3.0, session_id="s2", tab=5)
        assert ei.value.reason == "rate_limited"
        assert ei.value.key == "one"
        with reg._cond:
            assert inst.waiters == set()      # no leaked waiter
            assert reg._tab_queues == {}      # no leaked turnstile ticket
            assert inst.pending == 0          # admitted slot released
    finally:
        stop.set()


# --- Fix 1: BURST<1 with rate>0 must NOT silently brick the instance -------- #
def test_burst_below_one_with_rate_does_not_lock_out():
    """A sub-1 burst while RATE_PER_SEC>0 used to permanently brick the instance:
    rl_tokens started at 0 and the refill cap was min(0,…)=0, so `rl_tokens<1.0`
    was ALWAYS true → EVERY /cmd returned rate_limited forever (silent lockout).
    The burst is now clamped to a floor of 1 so a normal command is admitted."""
    for bad_burst in (0, 0.5):
        reg = S.Registry(rate_per_sec=5.0, burst=bad_burst)
        assert reg._burst == 1.0                  # clamped up to the sane floor
        inst = _register_live(reg)                # created with the clamped burst
        with reg._cond:
            # First admit must PASS (full bucket of 1) — not rate_limited forever.
            assert reg._admit_locked(inst) is None
    # RATE_PER_SEC=0 (unlimited) is the real disable path — honoured regardless
    # of burst, so a 0 burst there is left untouched (no spurious clamp/log).
    reg0 = S.Registry(rate_per_sec=0, burst=0)
    assert reg0._burst == 0.0


# --- Fix 2: a post-admission raise must not leak the pending slot ----------- #
def test_pending_slot_released_on_post_admission_raise(monkeypatch):
    """The pending increment lives INSIDE the try whose finally releases it, so
    a raise AFTER admission but BEFORE the turnstile completes cannot strand a
    `pending` slot (which would eventually wedge the depth cap into a permanent
    queue_full). Force such a raise mid-submit (token_hex blows up) and assert
    the balance returns to 0 and no turnstile ticket leaks."""
    reg = S.Registry(rate_per_sec=0, max_queue=1000)   # admit always succeeds
    inst = _register_live(reg)

    def _boom(*a, **k):
        raise RuntimeError("forced post-admission failure")
    # token_hex is called inside submit's try, AFTER pending is bumped.
    monkeypatch.setattr(S.secrets, "token_hex", _boom)

    with pytest.raises(RuntimeError):
        reg.submit({"op": "getHtml"}, 3.0)   # admitted, then raises past the bump
    with reg._cond:
        assert inst.pending == 0             # released by the finally, no leak
        assert reg._tab_queues == {}         # no leaked turnstile ticket either


# --- HTTP: burst over the bucket → 429 rate_limited + throttle telemetry --- #
def test_cmd_rate_limited_returns_429_and_emits_throttle(telemetry):
    """Over-burst /cmd → HTTP 429 rate_limited (with a retry_after hint), a
    DISTINCT throttle telemetry event that is metadata-only and carries a COARSE
    session hash (never the raw id), and the server stays responsive (no wedge)."""
    spool_dir = telemetry
    reg = S.Registry(rate_per_sec=0.001, burst=2, max_queue=1000)  # ~no refill
    srv, _ = _serve(registry=reg)
    ext = FakeExtension(srv, executor=lambda c: {"url": "https://x.test"})
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        assert _req(srv, "POST", "/cmd", {"op": "getHtml"})[0] == 200   # burst 1
        assert _req(srv, "POST", "/cmd", {"op": "getHtml"})[0] == 200   # burst 2
        st, body = _cmd(srv, {"op": "getHtml"}, session="floodsession")  # over
        assert st == 429
        assert body["error"] == "rate_limited"
        assert body["retry_after"] > 0
        # 3 events: 2 ok + 1 throttled.
        evs = _wait_events(spool_dir, 3)
        throttled = [e for e in evs
                     if json.loads(e["payload"]).get("outcome") == "throttled"]
        assert len(throttled) == 1, f"expected one throttle event, got {evs}"
        p = json.loads(throttled[0]["payload"])
        assert p["op"] == "getHtml"
        assert p["reason"] == "rate_limited"
        # Attribution is a COARSE, non-reversible hash of X-Session-Id — NOT raw.
        assert p["sess"] == hashlib.sha256(b"floodsession").hexdigest()[:8]
        raw = _log_file(spool_dir).read_text()
        assert "floodsession" not in raw          # raw session id NEVER stored
        # No deadlock: the server still answers after shedding load.
        assert _req(srv, "GET", "/health")[0] == 200
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


# --- HTTP: pending at MAX_QUEUE → next /cmd is 429 queue_full (immediate) --- #
def test_cmd_queue_full_returns_429():
    """Two stuck (un-answered) in-flight commands fill an instance's pending to
    MAX_QUEUE; the next /cmd is rejected FAST with 429 queue_full (it does not
    block behind the queue). Rate disabled so ONLY the cap fires."""
    reg = S.Registry(rate_per_sec=0, max_queue=2)
    srv, _ = _serve(cmd_timeout=5.0, poll_timeout=5.0, registry=reg)
    ext = FakeExtension(srv, swallow=True)     # picks up but never answers
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        for t in (1, 2):     # different tabs → both admitted + pending
            threading.Thread(
                target=lambda tt=t: _req(srv, "POST", "/cmd",
                                         {"op": "getHtml", "tab": tt}),
                daemon=True).start()
        assert _wait_until(lambda: _instance_pending(reg) >= 2), \
            "two in-flight commands never filled the queue"
        t0 = time.time()
        st, body = _req(srv, "POST", "/cmd", {"op": "getHtml", "tab": 3})
        assert st == 429 and body["error"] == "queue_full"
        assert time.time() - t0 < 2.0, "queue_full must reject immediately"
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


# --- default knobs never throttle a legitimate small burst ----------------- #
def test_default_knobs_do_not_throttle_normal_use():
    """With PRODUCTION defaults, a normal workflow (open + a handful of ops) is
    NEVER throttled — the storm-only guarantee for legit use."""
    reg = S.Registry()   # default rate=5/s, burst=20, max_queue=32
    srv, _ = _serve(registry=reg)
    ext = FakeExtension(srv, instance_id="solo", label="only",
                        executor=_tab_echo_exec([101]))
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        assert _cmd(srv, {"op": "open"}, session="A")[0] == 200
        for _ in range(10):            # 10 rapid ops, well under burst 20
            st, _b = _cmd(srv, {"op": "getHtml"}, session="A")
            assert st == 200, "a normal small burst must never be throttled"
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


# --------------------------------------------------------------------------- #
# Skill side: a 429 from /cmd makes the `browser` CLI print a back-off message
# and exit non-zero (the runaway-loop backpressure signal). Runs the REAL bash
# entrypoint against an in-process fake server that always answers /cmd with 429.
# Skipped where curl is unavailable (the CLI shells out to curl).
# --------------------------------------------------------------------------- #
BROWSER_BIN = Path(__file__).resolve().parent.parent / "browser"


@pytest.mark.skipif(shutil.which("curl") is None, reason="curl not on PATH")
def test_browser_cli_backs_off_on_429(tmp_path):
    class _H(S.BaseHTTPRequestHandler):
        def log_message(self, *a):  # noqa: A003
            pass

        def do_POST(self):
            ln = int(self.headers.get("Content-Length") or 0)
            if ln:
                self.rfile.read(ln)
            payload = json.dumps({"ok": False, "error": "rate_limited",
                                  "retry_after": 1.5}).encode("utf-8")
            self.send_response(429)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    srv = S.ThreadingHTTPServer(("127.0.0.1", 0), _H)
    srv.daemon_threads = True
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    tokf = tmp_path / "token"
    tokf.write_text("smoke-token\n")
    env = dict(os.environ)
    env.update(BROWSER_BRIDGE_HOST="127.0.0.1",
               BROWSER_BRIDGE_PORT=str(port),
               BROWSER_BRIDGE_TOKEN_FILE=str(tokf))
    try:
        r = subprocess.run([str(BROWSER_BIN), "eval", "1+1"], env=env,
                           capture_output=True, text=True, timeout=30)
        assert r.returncode != 0, "a 429 must make the CLI exit non-zero"
        low = r.stderr.lower()
        assert "rate-limited" in low or "back off" in low, \
            f"expected a back-off message on stderr, got: {r.stderr!r}"
    finally:
        srv.shutdown(); srv.server_close()


# --------------------------------------------------------------------------- #
# Skill side: `browser text [selector] [--max-bytes N]` builds the right /cmd
# body (op=text + selector + maxBytes). Runs the REAL bash entrypoint against an
# in-process fake server that CAPTURES the posted body and returns a canned
# result. Skipped where curl is unavailable.
# --------------------------------------------------------------------------- #
class _CaptureCmdServer:
    """A minimal /cmd server that records the last posted body and answers with a
    canned success envelope (so the CLI's `text` subcommand exits 0)."""

    def __init__(self):
        self.bodies = []

    def handler(self):
        outer = self

        class _H(S.BaseHTTPRequestHandler):
            def log_message(self, *a):  # noqa: A003
                pass

            def do_POST(self):
                ln = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(ln) if ln else b"{}"
                try:
                    outer.bodies.append(json.loads(raw))
                except ValueError:
                    outer.bodies.append(None)
                payload = json.dumps({"ok": True, "result": {
                    "id": "x", "ok": True,
                    "data": {"url": "https://x.test", "title": "X",
                             "text": "hello world"}}}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        return _H


def _run_browser(args, tmp_path):
    srv_state = _CaptureCmdServer()
    srv = S.ThreadingHTTPServer(("127.0.0.1", 0), srv_state.handler())
    srv.daemon_threads = True
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    tokf = tmp_path / "token"
    tokf.write_text("smoke-token\n")
    env = dict(os.environ)
    env.update(BROWSER_BRIDGE_HOST="127.0.0.1", BROWSER_BRIDGE_PORT=str(port),
               BROWSER_BRIDGE_TOKEN_FILE=str(tokf))
    try:
        r = subprocess.run([str(BROWSER_BIN), *args], env=env,
                           capture_output=True, text=True, timeout=30)
        return r, srv_state.bodies
    finally:
        srv.shutdown(); srv.server_close()


@pytest.mark.skipif(shutil.which("curl") is None, reason="curl not on PATH")
def test_browser_cli_text_default_maxbytes_no_selector(tmp_path):
    r, bodies = _run_browser(["text"], tmp_path)
    assert r.returncode == 0, r.stderr
    assert bodies, "no /cmd body captured"
    b = bodies[-1]
    assert b["op"] == "text"
    assert b["maxBytes"] == 32768          # the documented default cap
    assert "selector" not in b             # no selector given → field omitted


@pytest.mark.skipif(shutil.which("curl") is None, reason="curl not on PATH")
def test_browser_cli_text_selector_and_maxbytes(tmp_path):
    r, bodies = _run_browser(["text", "main.content", "--max-bytes", "500"],
                             tmp_path)
    assert r.returncode == 0, r.stderr
    b = bodies[-1]
    assert b["op"] == "text"
    assert b["selector"] == "main.content"
    assert b["maxBytes"] == 500


@pytest.mark.skipif(shutil.which("curl") is None, reason="curl not on PATH")
def test_browser_cli_text_rejects_bad_maxbytes(tmp_path):
    r, _ = _run_browser(["text", "--max-bytes", "not-a-number"], tmp_path)
    assert r.returncode != 0
    assert "max-bytes" in r.stderr.lower()


# --------------------------------------------------------------------------- #
# CDP (chrome.debugger) ops: frames / click / type / key + --frame reads.
# The server stays op-agnostic about CDP mechanics — it validates the op set,
# tab-scopes + routes to the owned/target tab, and forwards the typed params
# (frame/selector/text/key) verbatim to the extension. These assert exactly that,
# plus the metadata-only telemetry + rate-limit coverage for the new ops. The
# real chrome.debugger attach/detach behaviour is unit-tested in protocol.js /
# cdp_protocol.test.mjs and verified manually against live Brave (see the PR body).
# --------------------------------------------------------------------------- #
def test_cdp_ops_registered_in_contract():
    """frames/click/type/key are dispatchable + tab-scoped, with required fields."""
    for op in ("frames", "click", "type", "key"):
        assert op in S.ALLOWED_OPS, f"{op} must be an allowed op"
        assert op in S.TAB_SCOPED_OPS, f"{op} must be tab-scoped (acts on one tab)"
    assert S.REQUIRED_FIELDS["click"] == ("selector",)
    assert S.REQUIRED_FIELDS["type"] == ("text",)
    assert S.REQUIRED_FIELDS["key"] == ("key",)
    # frames takes no skill-supplied field.
    assert "frames" not in S.REQUIRED_FIELDS


def test_click_requires_selector_400():
    srv, _ = _serve()
    ext = FakeExtension(srv)
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        st, body = _req(srv, "POST", "/cmd", {"op": "click"})
        assert st == 400
        assert body["error"] == "missing_field:selector"
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


def test_type_requires_text_400():
    srv, _ = _serve()
    ext = FakeExtension(srv)
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        st, body = _req(srv, "POST", "/cmd", {"op": "type"})
        assert st == 400
        assert body["error"] == "missing_field:text"
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


def test_key_requires_key_400():
    srv, _ = _serve()
    ext = FakeExtension(srv)
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        st, body = _req(srv, "POST", "/cmd", {"op": "key"})
        assert st == 400
        assert body["error"] == "missing_field:key"
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


def test_cdp_ops_route_to_owned_session_tab():
    """frames/click/type/key are tab-scoped → they route to the session's owned tab
    (the CDP attach on the extension side is thereby confined to the owned tab)."""
    srv, _ = _serve()
    ext = FakeExtension(srv, instance_id="solo", label="only",
                        executor=_tab_echo_exec([101]))
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        _cmd(srv, {"op": "open"}, session="A")
        for body in ({"op": "frames"}, {"op": "click", "selector": "#go"},
                     {"op": "type", "text": "hi"}, {"op": "key", "key": "Enter"}):
            st, resp = _cmd(srv, body, session="A")
            assert st == 200, (body, resp)
            assert resp["result"]["data"]["tabId"] == 101, \
                f"{body['op']} must route to A's owned tab 101"
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


def test_cdp_params_forwarded_verbatim():
    """frame/selector/text/key are forwarded to the extension command untouched
    (they are typed op params, not skill-side routing hints like target/tab)."""
    srv, _ = _serve()
    ext = FakeExtension(srv, instance_id="solo", label="only",
                        executor=lambda c: {"url": "https://x.test", "ok": True})
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        _req(srv, "POST", "/cmd",
             {"op": "click", "selector": "#run", "frame": "model-benchmarking"})
        _req(srv, "POST", "/cmd", {"op": "type", "text": "a prompt", "selector": "#p"})
        _req(srv, "POST", "/cmd", {"op": "key", "key": "Enter"})
        _req(srv, "POST", "/cmd", {"op": "text", "frame": "F1"})
        by_op = {c["op"]: c for c in ext.dispatched}
        assert by_op["click"]["selector"] == "#run"
        assert by_op["click"]["frame"] == "model-benchmarking"
        assert by_op["type"]["text"] == "a prompt"
        assert by_op["type"]["selector"] == "#p"
        assert by_op["key"]["key"] == "Enter"
        assert by_op["text"]["frame"] == "F1"
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


def test_frames_telemetry_metadata_only(telemetry):
    """PRIVACY: a `frames` result lists frame URLs (incl. cross-origin ones), but
    telemetry emits ONLY the op + the bare TOP-LEVEL domain — never the frame URLs."""
    spool_dir = telemetry
    srv, _ = _serve()
    ext = FakeExtension(srv, executor=lambda c: {
        "url": "https://civitai.com/",
        "frames": [
            {"frameId": "MAIN", "url": "https://civitai.com/", "name": ""},
            {"frameId": "F1", "name": "bench",
             "url": "https://model-benchmarking.civit.ai/secret-app-path?token=abc"},
        ]})
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        st, body = _req(srv, "POST", "/cmd", {"op": "frames"})
        assert st == 200
        # round-trip sanity: the caller DOES get the frame list back.
        assert body["result"]["data"]["frames"][1]["frameId"] == "F1"
        e = _wait_events(spool_dir, 1)[0]
        p = json.loads(e["payload"])
        assert p["op"] == "frames"
        assert p["outcome"] == "ok"
        assert p["domain"] == "civitai.com"       # bare TOP-LEVEL domain only
        raw = _log_file(spool_dir).read_text()
        assert "model-benchmarking" not in raw, "a frame URL leaked into telemetry"
        assert "secret-app-path" not in raw
        assert "token=abc" not in raw
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


def test_type_telemetry_no_typed_text(telemetry):
    """PRIVACY: `type` telemetry never carries the typed text — only op/domain."""
    spool_dir = telemetry
    srv, _ = _serve()
    ext = FakeExtension(srv, executor=lambda c: {"url": "https://civitai.com/",
                                                 "typed": len(c.get("text", ""))})
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        st, _ = _req(srv, "POST", "/cmd",
                     {"op": "type", "text": "SECRET_PROMPT_cafef00d"})
        assert st == 200
        e = _wait_events(spool_dir, 1)[0]
        assert json.loads(e["payload"])["op"] == "type"
        raw = _log_file(spool_dir).read_text()
        assert "SECRET_PROMPT" not in raw, "typed text leaked into telemetry"
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


def test_cdp_op_counts_against_rate_limit():
    """A CDP op (frames) is a normal dispatch → it spends a token-bucket slot, so a
    sustained CDP flood is throttled with 429 exactly like eval/text (#178)."""
    reg = S.Registry(rate_per_sec=1.0, burst=2.0)
    srv, _ = _serve(registry=reg)
    ext = FakeExtension(srv, instance_id="solo", label="only",
                        executor=lambda c: {"url": "https://x.test", "frames": []})
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        codes = [_req(srv, "POST", "/cmd", {"op": "frames"})[0] for _ in range(6)]
        assert 429 in codes, f"a CDP-op flood must eventually 429; got {codes}"
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


@pytest.mark.skipif(shutil.which("curl") is None, reason="curl not on PATH")
def test_browser_cli_frames(tmp_path):
    r, bodies = _run_browser(["frames"], tmp_path)
    assert r.returncode == 0, r.stderr
    assert bodies[-1]["op"] == "frames"


@pytest.mark.skipif(shutil.which("curl") is None, reason="curl not on PATH")
def test_browser_cli_click_and_frame_flag(tmp_path):
    r, bodies = _run_browser(["--frame", "model-benchmarking", "click", "#run"],
                             tmp_path)
    assert r.returncode == 0, r.stderr
    b = bodies[-1]
    assert b["op"] == "click"
    assert b["selector"] == "#run"
    assert b["frame"] == "model-benchmarking"   # global --frame threaded into body


@pytest.mark.skipif(shutil.which("curl") is None, reason="curl not on PATH")
def test_browser_cli_type_with_selector(tmp_path):
    r, bodies = _run_browser(["type", "hello there", "--selector", "#prompt"],
                             tmp_path)
    assert r.returncode == 0, r.stderr
    b = bodies[-1]
    assert b["op"] == "type"
    assert b["text"] == "hello there"
    assert b["selector"] == "#prompt"


@pytest.mark.skipif(shutil.which("curl") is None, reason="curl not on PATH")
def test_browser_cli_key(tmp_path):
    r, bodies = _run_browser(["key", "Enter"], tmp_path)
    assert r.returncode == 0, r.stderr
    assert bodies[-1]["op"] == "key"
    assert bodies[-1]["key"] == "Enter"


@pytest.mark.skipif(shutil.which("curl") is None, reason="curl not on PATH")
def test_browser_cli_click_requires_selector(tmp_path):
    r, _ = _run_browser(["click"], tmp_path)
    assert r.returncode != 0
    assert "selector" in r.stderr.lower()


@pytest.mark.skipif(shutil.which("curl") is None, reason="curl not on PATH")
def test_browser_cli_screenshot_fullpage_flag(tmp_path):
    # --fullpage is threaded into the command body; a path arg still works too.
    r, bodies = _run_browser(["screenshot", "--fullpage"], tmp_path)
    # The canned capture server returns a text payload (no dataUrl), but the CLI
    # only writes a file when a path is given; with no path it pretty-prints → exit 0.
    assert r.returncode == 0, r.stderr
    assert bodies[-1]["op"] == "screenshot"
    assert bodies[-1]["fullpage"] is True


# =========================================================================== #
# whoami — read-only host identity + bridge diagnostics (GET /whoami)
# --------------------------------------------------------------------------- #
# whoami reports which HOST (laptop/workbench) + which browser profiles/instances
# + bridge diagnostics, bearer + Host guarded exactly like /health, NOT rate-
# limited, and metadata-only (active-tab DOMAIN, never the full URL — #173).
# =========================================================================== #

# --- host resolution (fully injectable → unit-testable) -------------------- #
def test_resolve_host_from_activity_host_env():
    r = S.resolve_host(env={"ACTIVITY_HOST": "laptop"}, ips=[])
    assert r == {"label": "laptop", "source": "activity_host_env", "ips": []}
    r2 = S.resolve_host(env={"ACTIVITY_HOST": "WorkBench"}, ips=[])
    assert r2["label"] == "workbench" and r2["source"] == "activity_host_env"


def test_resolve_host_ignores_bogus_activity_host_env():
    # A junk ACTIVITY_HOST is not a valid label → fall through to the next signal
    # (collector file pinned absent so this is hermetic on a real host).
    r = S.resolve_host(env={"ACTIVITY_HOST": "nixos"},
                       collector_env_path="/nonexistent",
                       ips=["192.168.50.155"])
    assert r["label"] == "laptop" and r["source"] == "ip"


def test_resolve_host_from_collector_file(tmp_path):
    f = tmp_path / "env"
    f.write_text('FOO=1\nACTIVITY_HOST="workbench"\nBAR=2\n', encoding="utf-8")
    r = S.resolve_host(env={}, collector_env_path=f, ips=["10.0.0.9"])
    assert r == {"label": "workbench", "source": "activity_collector_file",
                 "ips": ["10.0.0.9"]}


def test_resolve_host_env_beats_file(tmp_path):
    f = tmp_path / "env"
    f.write_text("ACTIVITY_HOST=workbench\n", encoding="utf-8")
    r = S.resolve_host(env={"ACTIVITY_HOST": "laptop"}, collector_env_path=f,
                       ips=[])
    assert r["label"] == "laptop" and r["source"] == "activity_host_env"


def test_resolve_host_missing_file_falls_through(tmp_path):
    missing = tmp_path / "nope" / "env"
    r = S.resolve_host(env={}, collector_env_path=missing,
                       ips=["192.168.50.250"])
    assert r["label"] == "workbench" and r["source"] == "ip"


def test_resolve_host_from_ip_primary_and_secondary():
    assert S.resolve_host(env={}, collector_env_path="/nonexistent",
                          ips=["192.168.50.250"])["label"] == "workbench"
    assert S.resolve_host(env={}, collector_env_path="/nonexistent",
                          ips=["192.168.50.155"])["label"] == "laptop"
    # nebula 10.42.0.x fallbacks also resolve.
    assert S.resolve_host(env={}, collector_env_path="/nonexistent",
                          ips=["10.42.0.30"])["label"] == "workbench"
    assert S.resolve_host(env={}, collector_env_path="/nonexistent",
                          ips=["10.42.0.100"])["label"] == "laptop"


def test_resolve_host_unknown_when_nothing_matches():
    r = S.resolve_host(env={}, collector_env_path="/nonexistent",
                       ips=["8.8.8.8", "203.0.113.5"])
    assert r == {"label": "unknown", "source": "unknown",
                 "ips": ["8.8.8.8", "203.0.113.5"]}


def test_host_from_ips_precedence_mirrors_ship():
    # Both hosts' primaries present → workbench wins (ship.sh detect_role order).
    assert S._host_from_ips(["192.168.50.155", "192.168.50.250"]) == "workbench"
    # A primary beats a secondary of the OTHER host.
    assert S._host_from_ips(["10.42.0.100", "192.168.50.250"]) == "workbench"
    assert S._host_from_ips([]) == ""


def test_parse_activity_host_variants():
    assert S._parse_activity_host("ACTIVITY_HOST=laptop") == "laptop"
    assert S._parse_activity_host("ACTIVITY_HOST='workbench'") == "workbench"
    assert S._parse_activity_host('X=1\nACTIVITY_HOST="laptop"\n') == "laptop"
    assert S._parse_activity_host("NOPE=1") == ""


# --- git short-HEAD + manifest_version (best-effort scalars) --------------- #
def test_git_short_head_none_on_non_repo(tmp_path):
    # A directory that is not a git repo → None (best-effort, never fatal).
    assert S.git_short_head(repo=tmp_path) is None


def test_git_short_head_none_on_missing_dir(tmp_path):
    assert S.git_short_head(repo=tmp_path / "does-not-exist") is None


def test_manifest_version_reads_current():
    v = S.manifest_version(path=EXT_DIR / "manifest.json")
    assert isinstance(v, str) and v == "0.2.0"


def test_manifest_version_none_on_missing(tmp_path):
    assert S.manifest_version(path=tmp_path / "nope.json") is None


def test_manifest_version_none_on_malformed(tmp_path):
    bad = tmp_path / "manifest.json"
    bad.write_text("{ not json", encoding="utf-8")
    assert S.manifest_version(path=bad) is None


# --- GET /whoami: shape, guard, zero-instances ----------------------------- #
def test_whoami_shape_zero_instances():
    srv, _ = _serve()
    try:
        status, body = _req(srv, "GET", "/whoami")
        assert status == 200
        assert body["ok"] is True
        # host block
        host = body["host"]
        assert host["label"] in ("laptop", "workbench", "unknown")
        assert host["source"] in ("activity_host_env",
                                  "activity_collector_file", "ip", "unknown")
        assert isinstance(host["ips"], list)
        # bridge block
        br = body["bridge"]
        assert br["endpoint"].startswith("http://127.0.0.1:")
        assert isinstance(br["port"], int)
        assert br["server_version"]["version"] == S.SERVER_VERSION
        assert "git" in br["server_version"]  # str or None, present either way
        assert br["connected"] == 0
        assert br["rate_limit"] == {"per_sec": S.RATE_PER_SEC,
                                    "burst": S.BURST, "max_queue": S.MAX_QUEUE}
        assert "extension_version_current" in br
        # instances empty when nothing connected — host+bridge still reported.
        assert body["instances"] == []
    finally:
        srv.shutdown(); srv.server_close()


def test_whoami_missing_token_401():
    srv, _ = _serve()
    try:
        status, _ = _req(srv, "GET", "/whoami", token=None)
        assert status == 401
    finally:
        srv.shutdown(); srv.server_close()


def test_whoami_wrong_token_401():
    srv, _ = _serve()
    try:
        status, _ = _req(srv, "GET", "/whoami", token="nope")
        assert status == 401
    finally:
        srv.shutdown(); srv.server_close()


def test_whoami_bad_host_403():
    srv, _ = _serve()
    try:
        status, _ = _req(srv, "GET", "/whoami", host="evil.example.com")
        assert status == 403
    finally:
        srv.shutdown(); srv.server_close()


def test_whoami_rate_limit_reflects_registry():
    reg = S.Registry(rate_per_sec=7, burst=13, max_queue=99)
    srv, _ = _serve(registry=reg)
    try:
        status, body = _req(srv, "GET", "/whoami")
        assert status == 200
        assert body["bridge"]["rate_limit"] == {"per_sec": 7.0, "burst": 13.0,
                                                 "max_queue": 99}
    finally:
        srv.shutdown(); srv.server_close()


def test_whoami_git_head_null_still_200(monkeypatch):
    monkeypatch.setattr(S, "git_short_head", lambda *a, **k: None)
    srv, _ = _serve()
    try:
        status, body = _req(srv, "GET", "/whoami")
        assert status == 200
        assert body["bridge"]["server_version"]["git"] is None
    finally:
        srv.shutdown(); srv.server_close()


# --- GET /whoami: instances (metadata-only, ext_version enrichment) -------- #
def test_whoami_reports_connected_instance_metadata_only():
    srv, _ = _serve()
    fake = FakeExtension(srv, instance_id="uuid-a", label="work",
                         active_url="https://example.com/some/path?q=1",
                         active_title="Example",
                         ext_version="0.1.0")
    fake.start()
    try:
        assert _wait_instances(srv, 1) is not None
        status, body = _req(srv, "GET", "/whoami")
        assert status == 200
        assert body["bridge"]["connected"] == 1
        insts = body["instances"]
        assert len(insts) == 1
        i = insts[0]
        assert i["key"] == "work"        # label is the routing key
        assert i["label"] == "work"
        assert i["instanceId"] == "uuid-a"
        # metadata-only: the DOMAIN, never the full URL/path/query (#173).
        assert i["activeTabDomain"] == "example.com"
        assert "some/path" not in json.dumps(i)
        assert i["extension_version"] == "0.1.0"
    finally:
        fake.stop(); srv.shutdown(); srv.server_close()


def test_whoami_extension_version_null_when_unreported():
    srv, _ = _serve()
    fake = FakeExtension(srv, instance_id="uuid-b", label="lab",
                         active_url="https://noext.test/")
    fake.start()
    try:
        assert _wait_instances(srv, 1) is not None
        status, body = _req(srv, "GET", "/whoami")
        assert status == 200
        i = body["instances"][0]
        assert i["extension_version"] is None
        assert i["activeTabDomain"] == "noext.test"
    finally:
        fake.stop(); srv.shutdown(); srv.server_close()


# --- CLI: `browser whoami` GETs /whoami and pretty-prints ------------------ #
class _WhoamiServer:
    """A minimal GET /whoami server returning a canned identity object."""

    def handler(self):
        class _H(S.BaseHTTPRequestHandler):
            def log_message(self, *a):  # noqa: A003
                pass

            def do_GET(self):
                payload = json.dumps({
                    "ok": True,
                    "host": {"label": "workbench", "source": "ip",
                             "ips": ["192.168.50.250"]},
                    "bridge": {"endpoint": "http://127.0.0.1:8788", "port": 8788,
                               "server_version": {"version": "x", "git": None},
                               "connected": 0,
                               "rate_limit": {"per_sec": 5.0, "burst": 20.0,
                                              "max_queue": 32},
                               "extension_version_current": "0.1.0"},
                    "instances": []}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        return _H


@pytest.mark.skipif(shutil.which("curl") is None, reason="curl not on PATH")
def test_browser_cli_whoami(tmp_path):
    srv = S.ThreadingHTTPServer(("127.0.0.1", 0), _WhoamiServer().handler())
    srv.daemon_threads = True
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    tokf = tmp_path / "token"
    tokf.write_text("smoke-token\n")
    env = dict(os.environ)
    env.update(BROWSER_BRIDGE_HOST="127.0.0.1", BROWSER_BRIDGE_PORT=str(port),
               BROWSER_BRIDGE_TOKEN_FILE=str(tokf))
    try:
        r = subprocess.run([str(BROWSER_BIN), "whoami"], env=env,
                           capture_output=True, text=True, timeout=30)
        assert r.returncode == 0, r.stderr
        out = json.loads(r.stdout)
        assert out["host"]["label"] == "workbench"
        assert out["bridge"]["extension_version_current"] == "0.1.0"
    finally:
        srv.shutdown(); srv.server_close()


# =========================================================================== #
# Gap 1 — `upload` op: audit-logged (op + target domain + the file PATH)
# =========================================================================== #
def test_upload_dispatches_and_audit_event_has_op_domain_path(telemetry):
    """An upload dispatches to the extension (tab-scoped like the other CDP ops)
    AND emits an AUDIT telemetry event carrying op + target domain + the file
    PATH (local metadata — never file content). The path is required so this
    exfil-capable action is traceable."""
    spool_dir = telemetry
    srv, _ = _serve()
    ext = FakeExtension(srv, executor=lambda c: {
        "ok": True, "selector": c.get("selector"),
        "url": "https://civitai.com/apps/run/model-benchmarking",
        "files": ["render.png"]})
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        st, body = _req(srv, "POST", "/cmd", {
            "op": "upload", "selector": "#file",
            "path": "/home/zach/pics/render.png"})
        assert st == 200
        # The path reached the extension (dispatched op), the basename came back.
        assert ext.dispatched[-1]["path"] == "/home/zach/pics/render.png"
        assert body["result"]["data"]["files"] == ["render.png"]
        e = _wait_events(spool_dir, 1)[0]
        assert e["exit_code"] == "0"
        p = json.loads(e["payload"])
        assert p["op"] == "upload"
        assert p["outcome"] == "ok"
        assert p["domain"] == "civitai.com"       # target domain (from the result url)
        assert p["path"] == "/home/zach/pics/render.png"  # AUDIT: the file path
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


def test_upload_is_tab_scoped_and_required_fields_enforced():
    """`upload` is a tab-scoped op requiring selector + path (server-side 400)."""
    assert "upload" in S.TAB_SCOPED_OPS
    assert "upload" in S.ALLOWED_OPS
    srv, _ = _serve()
    try:
        st, body = _req(srv, "POST", "/cmd", {"op": "upload", "path": "/x"})
        assert st == 400 and body["error"] == "missing_field:selector"
        st, body = _req(srv, "POST", "/cmd", {"op": "upload", "selector": "#f"})
        assert st == 400 and body["error"] == "missing_field:path"
    finally:
        srv.shutdown(); srv.server_close()


# --- CLI: path validation happens BEFORE any dispatch ---------------------- #
@pytest.mark.skipif(shutil.which("curl") is None, reason="curl not on PATH")
def test_browser_cli_upload_rejects_missing_and_nonfile_path(tmp_path):
    """`browser upload` validates the path is a readable regular file and resolves
    it to an ABSOLUTE path BEFORE any /cmd dispatch — a missing/non-file path is a
    clear error and NEVER reaches the bridge."""
    srv_state = _CaptureCmdServer()
    srv = S.ThreadingHTTPServer(("127.0.0.1", 0), srv_state.handler())
    srv.daemon_threads = True
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    tokf = tmp_path / "token"; tokf.write_text("smoke-token\n")
    env = dict(os.environ)
    env.update(BROWSER_BRIDGE_HOST="127.0.0.1", BROWSER_BRIDGE_PORT=str(port),
               BROWSER_BRIDGE_TOKEN_FILE=str(tokf))

    def run(*args):
        return subprocess.run([str(BROWSER_BIN), *args], env=env,
                              capture_output=True, text=True, timeout=30)
    try:
        # Nonexistent path → refused before dispatch.
        r = run("upload", "#f", str(tmp_path / "nope.png"))
        assert r.returncode != 0 and "no such file" in r.stderr.lower()
        # A directory (not a regular file) → refused.
        r = run("upload", "#f", str(tmp_path))
        assert r.returncode != 0 and "not a regular file" in r.stderr.lower()
        assert srv_state.bodies == [], "no upload reached the bridge for a bad path"
        # A real file → dispatched with an ABSOLUTE path + selector.
        f = tmp_path / "pic.png"; f.write_bytes(b"\x89PNG")
        r = run("upload", "#file", str(f))
        assert r.returncode == 0, r.stderr
        b = srv_state.bodies[-1]
        assert b["op"] == "upload"
        assert b["selector"] == "#file"
        assert b["path"] == os.path.realpath(str(f))  # resolved to an ABSOLUTE path
    finally:
        srv.shutdown(); srv.server_close()


# =========================================================================== #
# Gap 2 — hidden-tab self-announce: the CLI prints the note to STDERR (exit 0)
# =========================================================================== #
class _CannedCmdServer:
    """A /cmd server that answers with a caller-supplied result envelope `data`."""

    def __init__(self, data):
        self.data = data

    def handler(self):
        outer = self

        class _H(S.BaseHTTPRequestHandler):
            def log_message(self, *a):  # noqa: A003
                pass

            def do_POST(self):
                ln = int(self.headers.get("Content-Length") or 0)
                if ln:
                    self.rfile.read(ln)
                payload = json.dumps({"ok": True, "result": {
                    "id": "x", "ok": True, "data": outer.data}}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        return _H


def _run_browser_against(data, args, tmp_path):
    srv = S.ThreadingHTTPServer(("127.0.0.1", 0), _CannedCmdServer(data).handler())
    srv.daemon_threads = True
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    tokf = tmp_path / "token"; tokf.write_text("smoke-token\n")
    env = dict(os.environ)
    env.update(BROWSER_BRIDGE_HOST="127.0.0.1", BROWSER_BRIDGE_PORT=str(port),
               BROWSER_BRIDGE_TOKEN_FILE=str(tokf))
    try:
        return subprocess.run([str(BROWSER_BIN), *args], env=env,
                              capture_output=True, text=True, timeout=30)
    finally:
        srv.shutdown(); srv.server_close()


@pytest.mark.skipif(shutil.which("curl") is None, reason="curl not on PATH")
def test_browser_cli_hidden_tab_note_to_stderr_exit0(tmp_path):
    """A read whose result flags the tab hidden prints the note to STDERR but
    exits 0 (a warning, not an error) and leaves the JSON on stdout intact."""
    note = "tab is hidden — background tabs are throttled; run 'browser activate'"
    data = {"url": "https://x.test", "title": "X", "html": "<html></html>",
            "visibilityState": "hidden", "hidden": True, "note": note}
    r = _run_browser_against(data, ["html"], tmp_path)
    assert r.returncode == 0, "a hidden-tab warning must NOT fail the command"
    assert note in r.stderr, f"the note must be on stderr, got: {r.stderr!r}"
    # The JSON result is still on stdout, unbroken.
    out = json.loads(r.stdout)
    assert out["result"]["data"]["hidden"] is True


@pytest.mark.skipif(shutil.which("curl") is None, reason="curl not on PATH")
def test_browser_cli_visible_tab_no_stderr_warning(tmp_path):
    """A read on a VISIBLE tab prints NO hidden-tab warning to stderr."""
    data = {"url": "https://x.test", "title": "X", "html": "<html></html>",
            "visibilityState": "visible"}
    r = _run_browser_against(data, ["html"], tmp_path)
    assert r.returncode == 0
    assert "hidden" not in r.stderr.lower()
    assert "throttled" not in r.stderr.lower()


# =========================================================================== #
# Gap 3 — stale extension: version in /health + unknown_op → reload/restart map
# =========================================================================== #
def test_health_includes_extension_version_and_current():
    """/health surfaces each instance's reported extension_version AND the bridge-
    level extension_version_current (the manifest version the server reads)."""
    srv, _ = _serve()
    ext = FakeExtension(srv, instance_id="a", label="alpha", ext_version="0.1.0")
    ext.start()
    try:
        body = _wait_instances(srv, 1)
        assert body is not None
        # /health surfaces exactly what the server reads from the repo manifest.
        assert body["extension_version_current"] == S.manifest_version()
        inst = body["instances"][0]
        assert inst["extension_version"] == "0.1.0"   # what THIS instance reported
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


def test_health_extension_version_null_when_unreported():
    """An instance whose extension predates version reporting → null (no crash)."""
    srv, _ = _serve()
    ext = FakeExtension(srv, instance_id="b", label="beta")  # no ext_version
    ext.start()
    try:
        body = _wait_instances(srv, 1)
        assert body is not None
        assert body["instances"][0]["extension_version"] is None
        assert "extension_version_current" in body
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


def test_health_no_extension_still_reports_current_version():
    srv, _ = _serve()
    try:
        st, body = _req(srv, "GET", "/health")
        assert st == 200
        assert "extension_version_current" in body
        assert body["instances"] == []
    finally:
        srv.shutdown(); srv.server_close()


@pytest.mark.skipif(shutil.which("curl") is None, reason="curl not on PATH")
def test_browser_cli_unknown_op_maps_to_stale_extension_message(tmp_path):
    """A server-side op-level `unknown_op` for a DISPATCHED op (the extension is
    older than this CLI) maps to a clear reload/restart-Brave message + non-zero
    exit (the ↻-is-unreliable insight: the long-poll keeps the old SW alive)."""
    data_env = {"ok": False, "error": "unknown_op"}   # extension returned unknown_op

    class _H(S.BaseHTTPRequestHandler):
        def log_message(self, *a):  # noqa: A003
            pass

        def do_POST(self):
            ln = int(self.headers.get("Content-Length") or 0)
            if ln:
                self.rfile.read(ln)
            payload = json.dumps({"ok": True, "result": {
                "id": "x", **data_env}}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    srv = S.ThreadingHTTPServer(("127.0.0.1", 0), _H)
    srv.daemon_threads = True
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    tokf = tmp_path / "token"; tokf.write_text("smoke-token\n")
    env = dict(os.environ)
    env.update(BROWSER_BRIDGE_HOST="127.0.0.1", BROWSER_BRIDGE_PORT=str(port),
               BROWSER_BRIDGE_TOKEN_FILE=str(tokf))
    try:
        r = subprocess.run([str(BROWSER_BIN), "upload", "#f", str(tokf)],
                           env=env, capture_output=True, text=True, timeout=30)
        assert r.returncode != 0, "a stale-extension unknown_op must exit non-zero"
        low = r.stderr.lower()
        assert "unknown_op" in low
        assert "older than this cli" in low
        assert "restart brave" in low, f"expected restart-Brave guidance, got: {r.stderr!r}"
    finally:
        srv.shutdown(); srv.server_close()


@pytest.mark.skipif(shutil.which("curl") is None, reason="curl not on PATH")
def test_browser_cli_normal_op_result_unaffected_by_unknown_op_mapping(tmp_path):
    """A NORMAL successful op result is unaffected by the unknown_op mapping."""
    data = {"url": "https://x.test", "title": "X", "html": "<html>ok</html>"}
    r = _run_browser_against(data, ["html"], tmp_path)
    assert r.returncode == 0, r.stderr
    assert "older than this cli" not in r.stderr.lower()
    out = json.loads(r.stdout)
    assert out["result"]["data"]["html"] == "<html>ok</html>"
