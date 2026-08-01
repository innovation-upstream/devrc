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

# A manifest version pinned for the staleness tests. `manifest_version()` prefers
# the DEPLOYED extension at ~/.local/share/browser-bridge-ext/, so any test that
# asserts on a stale/fresh verdict must pin it — otherwise the suite's result
# depends on the operator's live deploy state (whether they have switched, and to
# which build). Deliberately not a real version so a leak is obvious.
PINNED_VERSION = "9.9.9-pinned"


@pytest.fixture
def pinned_manifest(tmp_path, monkeypatch):
    """Pin manifest_version() to PINNED_VERSION, hermetically: a tmp deployed
    manifest + a non-existent repo fallback. Never reads host state."""
    deployed = tmp_path / "pinned-manifest.json"
    deployed.write_text(json.dumps({"version": PINNED_VERSION}),
                        encoding="utf-8")
    monkeypatch.setattr(S, "_DEPLOYED_EXT_MANIFEST", deployed)
    monkeypatch.setattr(S, "_EXT_MANIFEST_PATH", tmp_path / "no-repo-manifest.json")
    return PINNED_VERSION

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
                 ext_version=None, ext_id=None):
        super().__init__(daemon=True)
        self.srv = srv
        self.instance_id = instance_id
        self.label = label
        self.executor = executor or (lambda cmd: {"echo": cmd.get("op")})
        self.swallow = swallow  # pick up a command but never answer (→ timeout)
        self.active_url = active_url
        self.active_title = active_title
        self.ext_version = ext_version  # reported via X-Bridge-Ext-Version (or None)
        self.ext_id = ext_id            # reported via X-Bridge-Ext-Id (or None)
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
        if self.ext_id:
            h[S.HDR_EXT_ID] = urllib.parse.quote(self.ext_id)
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
                                  "frames", "click", "type", "key", "wake",
                                  "activate", "upload", "ping", "emulate"}
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
    # `wake` is a dispatched, tab-scoped CDP op with NO required field; its
    # optional waitMs (the un-throttle settle) is a passthrough, not a routing hint.
    assert S.validate_command({"op": "wake"}) == ("wake", None)
    assert S.validate_command({"op": "wake", "waitMs": 500}) == ("wake", None)
    assert "wake" in S.TAB_SCOPED_OPS
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
    # BEHAVIOUR CHANGE: `screenshot` with no path no longer pretty-prints the
    # response — it now DECODES the data URL and writes a .png (the token fix), so
    # this capture server's dataUrl-less payload would legitimately error. The
    # `--data-url` escape hatch keeps the old pretty-print path, which is what this
    # test needs to assert the flag threading. The new default (file-writing) path
    # is covered by test_browser_cli_screenshot_fullpage_still_threaded_with_file_output.
    r, bodies = _run_browser(["screenshot", "--fullpage", "--data-url"], tmp_path)
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
    # Bumped to 0.6.0 for the `documentPredatesEmulation` hint on `emulate`.
    # 0.5.0 was the `emulate` op itself; 0.4.0 the poll-loop no-wedge change. The
    # bump is the operator's only falsifiable "is the new build loaded?" signal,
    # so this assertion is deliberately a literal — it MUST be updated in the
    # same commit as manifest.json, which is the point.
    assert isinstance(v, str) and v == "0.6.0"


def test_manifest_version_prefers_the_deployed_copy_over_the_repo(tmp_path,
                                                                 monkeypatch):
    """The version the server reports as EXPECTED is the one Brave actually
    loads — the deployed ~/.local/share/browser-bridge-ext/ copy — not the repo
    working tree (which any concurrent session's checkout can change)."""
    deployed = tmp_path / "deployed.json"
    deployed.write_text('{"version": "9.9.9"}', encoding="utf-8")
    monkeypatch.setattr(S, "_DEPLOYED_EXT_MANIFEST", deployed)
    monkeypatch.setattr(S, "_EXT_MANIFEST_PATH", EXT_DIR / "manifest.json")
    assert S.manifest_version() == "9.9.9"


def test_manifest_version_falls_back_to_repo_when_not_deployed(tmp_path,
                                                               monkeypatch):
    """Backwards-safe: a host that has not switched yet (no deployed copy) keeps
    reporting the repo manifest instead of going null."""
    monkeypatch.setattr(S, "_DEPLOYED_EXT_MANIFEST", tmp_path / "absent.json")
    monkeypatch.setattr(S, "_EXT_MANIFEST_PATH", EXT_DIR / "manifest.json")
    assert S.manifest_version() == "0.6.0"


def test_manifest_version_none_when_neither_exists(tmp_path, monkeypatch):
    monkeypatch.setattr(S, "_DEPLOYED_EXT_MANIFEST", tmp_path / "a.json")
    monkeypatch.setattr(S, "_EXT_MANIFEST_PATH", tmp_path / "b.json")
    assert S.manifest_version() is None


# --- annotate_staleness: the explicit loaded-vs-expected verdict ------------ #
def test_annotate_staleness_flags_a_mismatch_true():
    insts = [{"key": "work", "extension_version": "0.2.0"}]
    S.annotate_staleness(insts, expected="0.3.0")
    assert insts[0]["extension_stale"] is True
    assert insts[0]["extension_version_expected"] == "0.3.0"


def test_annotate_staleness_flags_a_match_false():
    insts = [{"key": "work", "extension_version": "0.3.0"}]
    S.annotate_staleness(insts, expected="0.3.0")
    assert insts[0]["extension_stale"] is False


def test_annotate_staleness_is_none_when_undecidable():
    """NEVER guess: an instance that reports no version (a build predating
    version reporting), or an unreadable manifest, yields null — not False."""
    insts = [{"key": "a", "extension_version": None},
             {"key": "b", "extension_version": ""}]
    S.annotate_staleness(insts, expected="0.3.0")
    assert [i["extension_stale"] for i in insts] == [None, None]

    insts2 = [{"key": "c", "extension_version": "0.3.0"}]
    S.annotate_staleness(insts2, expected=None)
    assert insts2[0]["extension_stale"] is None


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


def test_health_carries_an_explicit_stale_verdict_per_instance(pinned_manifest):
    """The point of the change: a yes/no, not two version strings to eyeball.
    An instance reporting 0.1.0 against the expected manifest is STALE=True."""
    srv, _ = _serve()
    ext = FakeExtension(srv, instance_id="a", label="alpha", ext_version="0.1.0")
    ext.start()
    try:
        body = _wait_instances(srv, 1)
        assert body is not None
        inst = body["instances"][0]
        assert inst["extension_version_expected"] == PINNED_VERSION
        assert inst["extension_stale"] is True
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


def test_health_stale_verdict_is_false_when_the_loaded_build_matches(
        pinned_manifest):
    srv, _ = _serve()
    ext = FakeExtension(srv, instance_id="a", label="alpha",
                        ext_version=PINNED_VERSION)
    ext.start()
    try:
        body = _wait_instances(srv, 1)
        assert body is not None
        assert body["instances"][0]["extension_stale"] is False
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


def test_whoami_carries_the_stale_verdict_per_instance(pinned_manifest):
    srv, _ = _serve()
    ext = FakeExtension(srv, instance_id="a", label="alpha", ext_version="0.1.0")
    ext.start()
    try:
        _wait_instances(srv, 1)
        st, body = _req(srv, "GET", "/whoami")
        assert st == 200
        inst = body["instances"][0]
        assert inst["extension_stale"] is True
        assert inst["extension_version_expected"] == PINNED_VERSION
        assert body["bridge"]["extension_version_current"] == PINNED_VERSION
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


def test_whoami_stale_verdict_is_null_for_an_unreporting_extension():
    """A build that predates version reporting is UNDECIDABLE, never "fine"."""
    srv, _ = _serve()
    ext = FakeExtension(srv, instance_id="a", label="alpha")  # no ext_version
    ext.start()
    try:
        _wait_instances(srv, 1)
        st, body = _req(srv, "GET", "/whoami")
        assert st == 200
        assert body["instances"][0]["extension_stale"] is None
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


# --- extension_id: WHICH DIRECTORY Brave loaded ---------------------------- #
# The version fields cannot answer this — a repo-path 0.3.1 and a deployed-path
# 0.3.1 are identical on every version field. chrome.runtime.id is path-derived,
# so it is the only signal that can confirm the migration off the git-mutable
# path took. (The path→id derivation is now MEASURED — sha256(abs path), first 32
# hex chars, nibbles mapped a-p; see extension/README.md. The server still only
# REPORTS the id and never computes an expected one: that is a deliberate
# separate decision, because an unknown path — a hand-loaded third profile, or a
# rollback to the repo path — must degrade to null, never to a false "wrong
# directory" alarm.)
def test_health_and_whoami_surface_the_reported_extension_id():
    srv, _ = _serve()
    ext = FakeExtension(srv, instance_id="a", label="alpha",
                        ext_version="0.3.1", ext_id="abcdefghijklmnop")
    ext.start()
    try:
        body = _wait_instances(srv, 1)
        assert body is not None
        assert body["instances"][0]["extension_id"] == "abcdefghijklmnop"
        st, who = _req(srv, "GET", "/whoami")
        assert st == 200
        assert who["instances"][0]["extension_id"] == "abcdefghijklmnop"
        # The deploy dir Brave SHOULD be pointed at, for the operator to read
        # next to the id. NOT an expected id — the server never guesses one.
        assert who["bridge"]["extension_dir_expected"].endswith(
            "browser-bridge-ext")
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


def test_extension_id_is_null_for_a_build_that_does_not_report_it():
    """Backwards-safe: the currently-loaded 0.2.0 build sends no id header."""
    srv, _ = _serve()
    ext = FakeExtension(srv, instance_id="a", label="alpha", ext_version="0.2.0")
    ext.start()
    try:
        body = _wait_instances(srv, 1)
        assert body is not None
        assert body["instances"][0]["extension_id"] is None
        st, who = _req(srv, "GET", "/whoami")
        assert who["instances"][0]["extension_id"] is None
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


def test_identity_headers_are_bounded_server_side():
    """A hostile/buggy extension must not be able to push an unbounded string
    into every /health and /whoami response. protocol.js's cap is client-side
    only, so the SERVER truncates the version + id it accepts."""
    srv, _ = _serve()
    huge = "x" * 5000
    ext = FakeExtension(srv, instance_id="a", label="alpha",
                        ext_version=huge, ext_id=huge)
    ext.start()
    try:
        body = _wait_instances(srv, 1)
        assert body is not None
        inst = body["instances"][0]
        assert len(inst["extension_version"]) == S.MAX_IDENTITY_CHARS
        assert len(inst["extension_id"]) == S.MAX_IDENTITY_CHARS
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


def test_every_poll_supplied_string_is_bounded_server_side():
    """The same rationale applies to EVERY /poll string, not just the two new
    ones: label and the active-tab url/title are echoed into /health, /instances
    and /whoami too, and protocol.js's cap binds only an honest extension."""
    srv, _ = _serve()
    huge = "y" * 6000
    ext = FakeExtension(srv, instance_id=huge, label=huge,
                        active_url="https://e.test/" + huge,
                        active_title=huge)
    ext.start()
    try:
        body = _wait_instances(srv, 1)
        assert body is not None
        inst = body["instances"][0]
        assert len(inst["label"]) == S.MAX_IDENTITY_CHARS
        assert len(inst["instanceId"]) == S.MAX_IDENTITY_CHARS
        assert len(inst["activeTab"]["url"]) == S.MAX_ACTIVE_TAB_CHARS
        assert len(inst["activeTab"]["title"]) == S.MAX_ACTIVE_TAB_CHARS
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


def test_a_later_poll_without_the_id_header_does_not_wipe_a_known_id():
    """Same rule the version field already follows: only overwrite a KNOWN
    value, so a legacy/partial poll cannot erase what an earlier poll reported."""
    reg = S.Registry()
    reg.poll("a", "alpha", 0.0, extension_version="0.3.1",
             extension_id="abcdefghijklmnop")
    reg.poll("a", "alpha", 0.0)          # no headers at all
    assert reg.snapshot()[0]["extension_id"] == "abcdefghijklmnop"
    assert reg.snapshot()[0]["extension_version"] == "0.3.1"


# --- `ping`: the extension build-freshness op ------------------------------ #
def test_ping_is_an_allowed_op_and_is_not_tab_scoped():
    """`ping` must reach the extension (it is the freshness tell) but must NOT
    contend for a tab — it touches no page, so it can be probed with no owned
    tab and cannot be serialized behind another session's in-flight op."""
    assert "ping" in S.ALLOWED_OPS
    assert "ping" not in S.TAB_SCOPED_OPS
    assert "ping" not in S.SERVER_OPS      # it is DISPATCHED, not answered locally
    assert S.validate_command({"op": "ping"}) == ("ping", None)


def test_ping_op_set_mirrors_the_extension_protocol_js():
    """server.py ALLOWED_OPS and extension/protocol.js ALLOWED_OPS are one
    contract — a drift here is how an op silently becomes undispatchable."""
    js = (EXT_DIR / "protocol.js").read_text(encoding="utf-8")
    block = re.search(r"export const ALLOWED_OPS = \[(.*?)\];", js, re.S)
    assert block is not None
    js_ops = set(re.findall(r'"([a-zA-Z]+)"', block.group(1)))
    assert js_ops == set(S.ALLOWED_OPS)


def test_ping_round_trips_to_the_extension_with_no_owned_tab():
    """End-to-end over the real HTTP path: a session that owns no tab can still
    probe which build is loaded (the FakeExtension echoes the op)."""
    srv, _ = _serve()
    ext = FakeExtension(srv, instance_id="a", label="alpha", ext_version="0.3.0")
    ext.start()
    try:
        _wait_instances(srv, 1)
        st, body = _req(srv, "POST", "/cmd", {"op": "ping"},
                        headers={"X-Session-Id": "sess-ping"})
        assert st == 200, body
        assert body["ok"] is True
        assert body["result"]["ok"] is True
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


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


# =========================================================================== #
# CLI token fixes: screenshot writes a FILE (never dumps base64 to stdout),
# `html` gets the same --max-bytes cap as `text`, and `js` aliases `eval`.
# All three are CLI-surface only (no server.py / extension change), so they are
# exercised against the REAL bash entrypoint + a canned in-process /cmd server.
# =========================================================================== #

# A minimal, VALID 1x1 PNG — so the CLI's base64 decode + file write is a real
# round-trip (the assertions check the PNG magic bytes on disk).
_PNG_B64 = ("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4z8"
            "AAAAMBAQDJ/pLvAAAAAElFTkSuQmCC")
_PNG_BYTES = base64.b64decode(_PNG_B64)
_PNG_DATA_URL = "data:image/png;base64," + _PNG_B64


class _CannedRecordingCmdServer:
    """`_CannedCmdServer` + it RECORDS each posted /cmd body, so one test can
    assert BOTH the request shape (op/flags on the wire) and the CLI's rendering
    of the canned result."""

    def __init__(self, data):
        self.data = data
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
                    "id": "x", "ok": True, "data": outer.data}}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        return _H


def _run_browser_canned(data, args, tmp_path, env_extra=None):
    """Run the real `browser` CLI against a canned-result server that ALSO
    records the posted bodies. Returns (CompletedProcess, bodies)."""
    state = _CannedRecordingCmdServer(data)
    srv = S.ThreadingHTTPServer(("127.0.0.1", 0), state.handler())
    srv.daemon_threads = True
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    tokf = tmp_path / "token"
    tokf.write_text("smoke-token\n")
    env = dict(os.environ)
    env.update(BROWSER_BRIDGE_HOST="127.0.0.1", BROWSER_BRIDGE_PORT=str(port),
               BROWSER_BRIDGE_TOKEN_FILE=str(tokf))
    if env_extra:
        env.update(env_extra)
    try:
        r = subprocess.run([str(BROWSER_BIN), *args], env=env,
                           capture_output=True, text=True, timeout=30)
        return r, state.bodies
    finally:
        srv.shutdown(); srv.server_close()


def _shot_data(**extra):
    d = {"url": "https://x.test", "dataUrl": _PNG_DATA_URL, "via": "cdp"}
    d.update(extra)
    return d


# --- screenshot never dumps base64 to stdout -------------------------------- #

@pytest.mark.skipif(shutil.which("curl") is None, reason="curl not on PATH")
def test_browser_cli_screenshot_no_path_writes_temp_file_and_prints_path(tmp_path):
    """No path → the PNG lands in TMPDIR and stdout is a compact JSON result
    carrying the PATH + byte size, NOT the base64 payload."""
    tmpdir = tmp_path / "shots"
    tmpdir.mkdir()
    r, _ = _run_browser_canned(_shot_data(), ["screenshot"], tmp_path,
                               env_extra={"TMPDIR": str(tmpdir)})
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["ok"] is True
    p = Path(out["path"])
    assert p.is_file(), f"no png written at {p}"
    assert p.parent == tmpdir, "the temp png must honour TMPDIR"
    assert p.name.startswith("browser-screenshot-") and p.suffix == ".png"
    # A real PNG round-tripped through base64.
    assert p.read_bytes() == _PNG_BYTES
    assert p.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    assert out["bytes"] == len(_PNG_BYTES)
    # Useful metadata carried through from the result.
    assert out["url"] == "https://x.test"
    assert out["via"] == "cdp"


@pytest.mark.skipif(shutil.which("curl") is None, reason="curl not on PATH")
def test_browser_cli_screenshot_no_path_stdout_has_no_base64_payload(tmp_path):
    """THE fix: the 133K–890K-token base64 blob must never reach stdout/stderr."""
    tmpdir = tmp_path / "shots"
    tmpdir.mkdir()
    r, _ = _run_browser_canned(_shot_data(), ["screenshot"], tmp_path,
                               env_extra={"TMPDIR": str(tmpdir)})
    assert r.returncode == 0, r.stderr
    assert "data:image/png;base64," not in r.stdout
    assert _PNG_B64 not in r.stdout
    # Not even a FRAGMENT of the payload leaks (stdout or stderr).
    assert _PNG_B64[:24] not in r.stdout
    assert _PNG_B64[:24] not in r.stderr
    # Stdout stays SMALL (a compact JSON line), not blob-sized.
    assert len(r.stdout) < 600, f"stdout unexpectedly large: {len(r.stdout)}"


@pytest.mark.skipif(shutil.which("curl") is None, reason="curl not on PATH")
def test_browser_cli_screenshot_explicit_path_unchanged(tmp_path):
    """An explicit path still writes there and prints JUST the path (back-compat)."""
    dest = tmp_path / "shot.png"
    r, _ = _run_browser_canned(_shot_data(), ["screenshot", str(dest)], tmp_path)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == str(dest)
    assert dest.read_bytes() == _PNG_BYTES
    assert _PNG_B64[:24] not in r.stdout


@pytest.mark.skipif(shutil.which("curl") is None, reason="curl not on PATH")
def test_browser_cli_screenshot_data_url_escape_hatch(tmp_path):
    """`--data-url` restores the old behaviour: the full data URL on stdout."""
    r, _ = _run_browser_canned(_shot_data(), ["screenshot", "--data-url"], tmp_path)
    assert r.returncode == 0, r.stderr
    assert _PNG_DATA_URL in r.stdout.replace("\n", "")


@pytest.mark.skipif(shutil.which("curl") is None, reason="curl not on PATH")
def test_browser_cli_screenshot_data_url_with_path_is_rejected(tmp_path):
    """--data-url and an explicit path are mutually exclusive (no silent winner)."""
    r, _ = _run_browser_canned(_shot_data(),
                               ["screenshot", "--data-url", str(tmp_path / "s.png")],
                               tmp_path)
    assert r.returncode != 0
    assert "mutually exclusive" in r.stderr.lower()


@pytest.mark.skipif(shutil.which("curl") is None, reason="curl not on PATH")
def test_browser_cli_screenshot_fullpage_still_threaded_with_file_output(tmp_path):
    """--fullpage still reaches the wire on the new (file-writing) default path."""
    tmpdir = tmp_path / "shots"
    tmpdir.mkdir()
    r, bodies = _run_browser_canned(_shot_data(), ["screenshot", "--fullpage"],
                                    tmp_path, env_extra={"TMPDIR": str(tmpdir)})
    assert r.returncode == 0, r.stderr
    assert bodies[-1]["op"] == "screenshot"
    assert bodies[-1]["fullpage"] is True
    assert Path(json.loads(r.stdout)["path"]).is_file()


@pytest.mark.skipif(shutil.which("curl") is None, reason="curl not on PATH")
def test_browser_cli_screenshot_missing_image_data_still_errors(tmp_path):
    """A result with no dataUrl is a clear error, not a silent empty file."""
    r, _ = _run_browser_canned({"url": "https://x.test"}, ["screenshot"], tmp_path)
    assert r.returncode != 0
    assert "missing image data" in r.stderr.lower()


# --- screenshot: privacy of the temp file (mode / validation / retention) --- #

@pytest.mark.skipif(shutil.which("curl") is None, reason="curl not on PATH")
def test_browser_cli_screenshot_temp_file_is_mode_0600(tmp_path):
    """PRIVACY: a screenshot is a pixel-perfect image of an AUTHENTICATED view.
    The temp PNG must be owner-only (tempfile.mkstemp), NOT umask-derived 0644 —
    /tmp is world-readable and shared with every UID on the box."""
    tmpdir = tmp_path / "shots"
    tmpdir.mkdir()
    r, _ = _run_browser_canned(_shot_data(), ["screenshot"], tmp_path,
                               env_extra={"TMPDIR": str(tmpdir)})
    assert r.returncode == 0, r.stderr
    p = Path(json.loads(r.stdout)["path"])
    mode = stat.S_IMODE(p.stat().st_mode)
    assert mode == 0o600, f"temp screenshot is {oct(mode)}, must be 0o600"


@pytest.mark.parametrize("payload,why", [
    ("data:image/png;base64,%%%%", "junk that b64decode would silently drop"),
    ("data:image/png;base64,", "an empty payload"),
    ("data:image/png;base64,notbase64!!!", "an outright invalid alphabet"),
])
@pytest.mark.skipif(shutil.which("curl") is None, reason="curl not on PATH")
def test_browser_cli_screenshot_malformed_base64_errors_and_writes_nothing(
        payload, why, tmp_path):
    """A malformed payload must EXIT NON-ZERO with a clear message and leave NO
    file. Without validate=True, b64decode ignores non-alphabet chars → these
    would decode to b"" and be written as a 0-byte "successful" .png that an
    agent then Reads as if it were a screenshot (and the last case would raise an
    uncaught binascii.Error → a raw traceback)."""
    tmpdir = tmp_path / "shots"
    tmpdir.mkdir()
    r, _ = _run_browser_canned(_shot_data(dataUrl=payload), ["screenshot"],
                               tmp_path, env_extra={"TMPDIR": str(tmpdir)})
    assert r.returncode != 0, f"{why} must not report success"
    assert "not valid base64" in r.stderr or "not a PNG" in r.stderr, r.stderr
    assert "Traceback" not in r.stderr, "must be a clean message, not a traceback"
    assert list(tmpdir.iterdir()) == [], "no file (not even a 0-byte one) may remain"


@pytest.mark.skipif(shutil.which("curl") is None, reason="curl not on PATH")
def test_browser_cli_screenshot_non_png_payload_is_rejected(tmp_path):
    """Valid base64 that isn't a PNG is refused before anything is written."""
    tmpdir = tmp_path / "shots"
    tmpdir.mkdir()
    not_png = base64.b64encode(b"GIF89a not really a png").decode()
    r, _ = _run_browser_canned(
        _shot_data(dataUrl="data:image/png;base64," + not_png),
        ["screenshot"], tmp_path, env_extra={"TMPDIR": str(tmpdir)})
    assert r.returncode != 0
    assert "not a PNG" in r.stderr
    assert list(tmpdir.iterdir()) == []


@pytest.mark.skipif(shutil.which("curl") is None, reason="curl not on PATH")
def test_browser_cli_screenshot_explicit_path_also_validates(tmp_path):
    """The same validation guards an EXPLICIT path — no 0-byte png there either."""
    dest = tmp_path / "shot.png"
    r, _ = _run_browser_canned(_shot_data(dataUrl="data:image/png;base64,%%%%"),
                               ["screenshot", str(dest)], tmp_path)
    assert r.returncode != 0
    assert not dest.exists(), "a malformed payload must not create the target file"


@pytest.mark.skipif(shutil.which("curl") is None, reason="curl not on PATH")
def test_browser_cli_screenshot_prunes_aged_temp_captures_only(tmp_path):
    """Retention: temp captures older than 24h are auto-pruned on the next
    screenshot. Strictly prefix-scoped — a fresh capture and an unrelated file
    are left alone."""
    tmpdir = tmp_path / "shots"
    tmpdir.mkdir()
    old = tmpdir / "browser-screenshot-old.png"
    fresh = tmpdir / "browser-screenshot-fresh.png"
    other = tmpdir / "someone-elses-file.png"
    unrelated_suffix = tmpdir / "browser-screenshot-notes.txt"
    for f in (old, fresh, other, unrelated_suffix):
        f.write_bytes(b"x")
    aged = time.time() - (25 * 3600)         # older than the 24h retention
    os.utime(old, (aged, aged))
    os.utime(other, (aged, aged))            # aged BUT not ours → must survive
    os.utime(unrelated_suffix, (aged, aged))  # right prefix, wrong suffix → survives

    r, _ = _run_browser_canned(_shot_data(), ["screenshot"], tmp_path,
                               env_extra={"TMPDIR": str(tmpdir)})
    assert r.returncode == 0, r.stderr
    assert not old.exists(), "an aged browser-screenshot-*.png must be pruned"
    assert fresh.exists(), "a fresh capture must NOT be pruned"
    assert other.exists(), "pruning must be scoped to our own prefix"
    assert unrelated_suffix.exists(), "pruning must be scoped to .png"
    # The new capture is there too.
    assert Path(json.loads(r.stdout)["path"]).is_file()


@pytest.mark.skipif(shutil.which("curl") is None, reason="curl not on PATH")
def test_browser_cli_screenshot_prune_never_follows_a_symlink(tmp_path):
    """An aged SYMLINK matching the prefix is unlinked as the symlink itself; the
    file it points at is never touched (lstat + a regular-file check)."""
    tmpdir = tmp_path / "shots"
    tmpdir.mkdir()
    victim = tmp_path / "precious.png"
    victim.write_bytes(b"do not delete me")
    link = tmpdir / "browser-screenshot-link.png"
    link.symlink_to(victim)
    aged = time.time() - (25 * 3600)
    os.utime(link, (aged, aged), follow_symlinks=False)

    r, _ = _run_browser_canned(_shot_data(), ["screenshot"], tmp_path,
                               env_extra={"TMPDIR": str(tmpdir)})
    assert r.returncode == 0, r.stderr
    assert victim.read_bytes() == b"do not delete me", "the target must be untouched"


@pytest.mark.skipif(shutil.which("curl") is None, reason="curl not on PATH")
def test_browser_cli_screenshot_prune_is_best_effort_and_regular_files_only(tmp_path):
    """Pruning must never fail the command and must only ever unlink a REGULAR
    file: an aged DIRECTORY whose name matches the capture pattern is skipped
    (not rmdir'd, no error), and the screenshot still succeeds."""
    tmpdir = tmp_path / "shots"
    tmpdir.mkdir()
    decoy = tmpdir / "browser-screenshot-adir.png"
    decoy.mkdir()
    (decoy / "keep").write_bytes(b"keep")
    aged = time.time() - (25 * 3600)
    os.utime(decoy, (aged, aged))

    r, _ = _run_browser_canned(_shot_data(), ["screenshot"], tmp_path,
                               env_extra={"TMPDIR": str(tmpdir)})
    assert r.returncode == 0, r.stderr
    assert decoy.is_dir() and (decoy / "keep").exists()
    assert "Traceback" not in r.stderr
    assert Path(json.loads(r.stdout)["path"]).is_file()


# --- `html` gets the same --max-bytes cap as `text` ------------------------- #

@pytest.mark.skipif(shutil.which("curl") is None, reason="curl not on PATH")
def test_browser_cli_html_default_cap_truncates_with_note(tmp_path):
    """A >32 KB outerHTML is capped at the documented 32768-byte default and the
    truncation is ANNOUNCED (the `text` convention: a note + a `truncated` count)."""
    html = "<p>" + ("x" * 50000) + "</p>"
    r, bodies = _run_browser_canned(
        {"url": "https://x.test", "title": "X", "html": html}, ["html"], tmp_path)
    assert r.returncode == 0, r.stderr
    assert bodies[-1]["op"] == "getHtml"
    assert bodies[-1]["maxBytes"] == 32768          # same default as `text`
    data = json.loads(r.stdout)["result"]["data"]
    assert data["truncated"] == len(html.encode()) - 32768
    assert data["html"].endswith("…[truncated %d bytes]" % data["truncated"])
    kept = data["html"].split("\n…[truncated")[0]
    assert len(kept.encode("utf-8")) == 32768
    assert kept == html[:32768]                     # a PREFIX of the original
    # And the whole payload is now bounded (32 KB + the note), not 50 KB.
    assert len(r.stdout) < 34000


@pytest.mark.skipif(shutil.which("curl") is None, reason="curl not on PATH")
def test_browser_cli_html_explicit_max_bytes_honoured(tmp_path):
    html = "<p>" + ("y" * 5000) + "</p>"
    r, bodies = _run_browser_canned(
        {"url": "https://x.test", "title": "X", "html": html},
        ["html", "--max-bytes", "100"], tmp_path)
    assert r.returncode == 0, r.stderr
    assert bodies[-1]["maxBytes"] == 100
    data = json.loads(r.stdout)["result"]["data"]
    kept = data["html"].split("\n…[truncated")[0]
    assert len(kept.encode("utf-8")) == 100
    assert data["truncated"] == len(html.encode()) - 100


@pytest.mark.skipif(shutil.which("curl") is None, reason="curl not on PATH")
def test_browser_cli_html_max_bytes_zero_is_truly_uncapped(tmp_path):
    """`--max-bytes 0` is a real escape hatch — the FULL html, no note."""
    html = "<p>" + ("z" * 60000) + "</p>"
    r, bodies = _run_browser_canned(
        {"url": "https://x.test", "title": "X", "html": html},
        ["html", "--max-bytes=0"], tmp_path)
    assert r.returncode == 0, r.stderr
    assert bodies[-1]["maxBytes"] == 0
    data = json.loads(r.stdout)["result"]["data"]
    assert data["html"] == html
    assert "truncated" not in data


@pytest.mark.skipif(shutil.which("curl") is None, reason="curl not on PATH")
def test_browser_cli_html_under_cap_is_byte_identical_no_regression(tmp_path):
    """Under the cap the response is passed through UNTOUCHED — no `truncated`
    field, no note, no reserialization: an ordinary `html` read is unchanged."""
    html = "<html><body>ünïcøde ok</body></html>"
    data = {"url": "https://x.test", "title": "X", "html": html}
    r, _ = _run_browser_canned(data, ["html"], tmp_path)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)["result"]["data"]
    assert out == data, "an under-cap read must be byte-for-byte the old result"
    assert "truncated" not in r.stdout


@pytest.mark.skipif(shutil.which("curl") is None, reason="curl not on PATH")
def test_browser_cli_html_truncation_never_splits_a_multibyte_char(tmp_path):
    """The cut lands on a UTF-8 boundary (normalizeText's guarantee) — the JSON
    still parses and the kept text is valid unicode."""
    html = "é" * 100                     # 2 bytes each → an odd cap splits a char
    r, _ = _run_browser_canned({"url": "https://x.test", "title": "X", "html": html},
                               ["html", "--max-bytes", "51"], tmp_path)
    assert r.returncode == 0, r.stderr
    data = json.loads(r.stdout)["result"]["data"]
    kept = data["html"].split("\n…[truncated")[0]
    assert kept == "é" * 25              # 50 bytes — the 51st (partial) byte is dropped
    assert data["truncated"] == 200 - 50


@pytest.mark.skipif(shutil.which("curl") is None, reason="curl not on PATH")
def test_browser_cli_html_rejects_bad_maxbytes_and_positional(tmp_path):
    r, _ = _run_browser_canned({"html": "<p/>"}, ["html", "--max-bytes", "nope"],
                               tmp_path)
    assert r.returncode != 0
    assert "max-bytes" in r.stderr.lower()
    r, _ = _run_browser_canned({"html": "<p/>"}, ["html", "main"], tmp_path)
    assert r.returncode != 0
    assert "positional" in r.stderr.lower()


# --- `js` is a first-class alias for `eval` --------------------------------- #

@pytest.mark.skipif(shutil.which("curl") is None, reason="curl not on PATH")
def test_browser_cli_js_and_eval_build_an_identical_request(tmp_path):
    """`js` and `eval` post the SAME body — and the WIRE op is `eval` for BOTH
    (the extension only knows `eval`; `js` is a CLI-surface alias only)."""
    canned = {"url": "https://x.test", "value": 2}
    r_js, b_js = _run_browser_canned(canned, ["js", "1+1"], tmp_path)
    r_ev, b_ev = _run_browser_canned(canned, ["eval", "1+1"], tmp_path)
    assert r_js.returncode == 0, r_js.stderr
    assert r_ev.returncode == 0, r_ev.stderr
    assert b_js[-1]["op"] == "eval", "the wire op for `js` MUST stay `eval`"
    assert b_ev[-1]["op"] == "eval"
    assert b_js[-1] == b_ev[-1]
    assert b_js[-1]["js"] == "1+1"
    assert json.loads(r_js.stdout) == json.loads(r_ev.stdout)


@pytest.mark.skipif(shutil.which("curl") is None, reason="curl not on PATH")
def test_browser_cli_js_alias_honours_frame_tab_instance(tmp_path):
    """The global flags work identically through the alias."""
    canned = {"url": "https://x.test", "value": 1}
    r, bodies = _run_browser_canned(
        canned,
        ["--frame", "42", "--tab", "77", "--instance", "work", "js", "document.title"],
        tmp_path)
    assert r.returncode == 0, r.stderr
    b = bodies[-1]
    assert b["op"] == "eval"          # still `eval` on the wire
    assert b["js"] == "document.title"
    assert b["frame"] == "42"
    assert b["tab"] == 77
    assert b["target"] == "work"


@pytest.mark.skipif(shutil.which("curl") is None, reason="curl not on PATH")
def test_browser_cli_js_requires_an_expression(tmp_path):
    r, _ = _run_browser_canned({"value": 1}, ["js"], tmp_path)
    assert r.returncode != 0
    assert "usage: browser js" in r.stderr


def test_browser_cli_help_lists_js_alias_and_screenshot_data_url():
    """`browser --help` (the header comment) documents the new surface, and the
    unknown-subcommand error lists `js` too."""
    r = subprocess.run([str(BROWSER_BIN), "--help"], capture_output=True,
                       text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    assert "browser js '<js>'" in r.stdout
    assert "--data-url" in r.stdout
    assert "--max-bytes" in r.stdout
    r2 = subprocess.run([str(BROWSER_BIN), "bogus-op"], capture_output=True,
                        text=True, timeout=30)
    assert r2.returncode != 0
    assert " js " in r2.stderr and " eval " in r2.stderr


# --------------------------------------------------------------------------- #
# `wake` op: tab-scoped, waitMs passthrough, and — the point of the whole change
# — it NEVER triggers the host-side i3 foregrounding that steals the operator's
# screen. `activate` remains the only op that does.
# --------------------------------------------------------------------------- #
def test_wake_routes_to_owned_session_tab():
    """`wake` is tab-scoped: a session that `open`ed has its wake routed to ITS
    owned tabId, so it can only ever un-throttle the tab the session owns."""
    srv, _ = _serve()
    ext = FakeExtension(srv, instance_id="solo", label="only",
                        executor=_tab_echo_exec([101]))
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        _cmd(srv, {"op": "open"}, session="A")
        st, body = _cmd(srv, {"op": "wake"}, session="A")
        assert st == 200
        assert body["result"]["data"]["tabId"] == 101, \
            "wake must route to the session's owned tab (own-tab enforced)"
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


def test_wake_explicit_tab_override():
    """`--tab <id>` forces the wake target even with no owned tab."""
    srv, _ = _serve()
    ext = FakeExtension(srv, instance_id="solo", label="only",
                        executor=_tab_echo_exec([]))
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        st, body = _req(srv, "POST", "/cmd", {"op": "wake", "tab": 88},
                        headers={S.HDR_SESSION_ID: "A"})
        assert st == 200
        assert body["result"]["data"]["tabId"] == 88
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


def test_wake_forwards_waitms_to_extension():
    """`waitMs` (the un-throttle settle) is a passthrough command field."""
    srv, _ = _serve()
    ext = FakeExtension(srv, executor=lambda c: {"tabId": 5, "woke": True})
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        st, _ = _req(srv, "POST", "/cmd", {"op": "wake", "waitMs": 500})
        assert st == 200
        wakes = [c for c in ext.dispatched if c["op"] == "wake"]
        assert wakes and wakes[0].get("waitMs") == 500
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


def test_wake_NEVER_invokes_i3_msg(monkeypatch):
    """THE REGRESSION GUARD FOR THIS WHOLE CHANGE. The host-side i3 foregrounding
    is what actually yanks the operator's screen, and it is keyed on
    op == "activate" alone. Enable i3 (so a leak WOULD be observable), then assert
    a successful `wake` spawns NO i3-msg and adds no `i3` field to the result."""
    calls = _enable_i3(monkeypatch, returncode=0)
    srv, _ = _serve()
    ext = FakeExtension(srv, executor=lambda c: {
        "tabId": 5, "woke": True, "visibilityState": "visible",
        "url": "https://model-benchmarking.civit.ai/run",
        "title": "Model Benchmarking"})
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        st, body = _req(srv, "POST", "/cmd", {"op": "wake"})
        assert st == 200
        assert body["result"]["data"]["woke"] is True
        assert calls == [], "wake must NEVER shell out to i3-msg (that is focus theft)"
        assert "i3" not in body["result"]["data"], \
            "wake does no host-side foregrounding, so it must not claim an i3 state"
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


def test_wake_telemetry_is_metadata_only(telemetry):
    """PRIVACY: a wake event emits ONLY op + the bare domain — never page content
    and never a full URL with path/query."""
    spool_dir = telemetry
    srv, _ = _serve()
    ext = FakeExtension(srv, executor=lambda c: {
        "tabId": 5, "woke": True,
        "url": "https://model-benchmarking.civit.ai/run?q=secret"})
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        st, _ = _req(srv, "POST", "/cmd", {"op": "wake"})
        assert st == 200
        e = _wait_events(spool_dir, 1)[0]
        p = json.loads(e["payload"])
        assert p["op"] == "wake"
        assert p["outcome"] == "ok"
        assert p["domain"] == "model-benchmarking.civit.ai"
        assert "secret" not in json.dumps(e), "no full URL / query ever in telemetry"
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


# --------------------------------------------------------------------------- #
# Silent-drop honesty + detector (2026-07-31 diagnosis)
#
# `extension_connected` is a bare OR across live instances, so one healthy Brave
# profile reported the bridge as UP for as long as it lived while a named
# instance (`work`) had silently dropped. The field cannot be redefined without
# breaking callers that legitimately ask "is anything up", so the truth is
# carried alongside it: `known_instances` (every routing key seen this process
# lifetime, each with `connected`) and `missing`.
#
# Alongside that, `instance_lost` gives the drop a journal trace naming the last
# command the instance never answered — the operator's second drop produced no
# evidence at all because nothing was dispatched while it was down.
# --------------------------------------------------------------------------- #
def _drop_registry():
    """A registry on a controllable clock, with `work` and `personal` polled in
    once so both are KNOWN. Returns (reg, clock-list)."""
    clock = [1000.0]
    reg = S.Registry(clock=lambda: clock[0])
    with reg._cond:  # noqa: SLF001 — white-box on purpose: no HTTP needed
        reg._register_locked("id-work", "work")          # noqa: SLF001
        reg._register_locked("id-personal", "personal")  # noqa: SLF001
    return reg, clock


def test_known_instances_reports_a_dropped_instance_as_not_connected():
    reg, clock = _drop_registry()
    known, missing = reg.known_snapshot()
    assert {k["key"] for k in known} == {"work", "personal"}
    assert all(k["connected"] for k in known)
    assert missing == []
    # `work` stops polling; `personal` keeps polling.
    clock[0] += S.CONNECT_STALE_S + 1
    with reg._cond:  # noqa: SLF001
        reg._instances["personal"].last_poll = clock[0]  # noqa: SLF001
    known, missing = reg.known_snapshot()
    by_key = {k["key"]: k for k in known}
    assert by_key["work"]["connected"] is False
    assert by_key["personal"]["connected"] is True
    assert [m["key"] for m in missing] == ["work"]


def test_a_long_gone_instance_is_forgotten_so_health_does_not_nag_forever():
    """Without a forget path the registry remembers every key for its whole
    process lifetime, so an operator who normally runs ONE profile would see a
    permanent `other: DISCONNECTED` line — a warning that is always on is a
    warning nobody reads."""
    reg, clock = _drop_registry()
    clock[0] += S.CONNECT_STALE_S + 1
    _known, missing = reg.known_snapshot()
    assert {m["key"] for m in missing} == {"work", "personal"}
    clock[0] += S.KNOWN_FORGET_S            # both now long past the cutoff
    known, missing = reg.known_snapshot()
    assert known == [] and missing == []


def test_INVARIANT_GUARD_a_freshly_polled_instance_is_not_aged_out():
    """⚠ INVARIANT GUARD, **not** coverage of the live-exemption clause.

    This pins only "a recent `last_poll` beats the age-out", i.e. that the cutoff
    keys off staleness rather than uptime. It CANNOT fail if the
    `id(inst) not in live_ids` term is deleted from the age-out, because
    `now - last_poll == 0` already makes the predicate false on its own. The
    clause itself is pinned by
    test_forget_exempts_an_instance_live_via_an_in_flight_poll below.

    Kept because it is the case an operator actually has (a profile polling for a
    week must never be reported as gone), and it is cheap."""
    reg, clock = _drop_registry()
    clock[0] += S.KNOWN_FORGET_S * 3
    with reg._cond:  # noqa: SLF001
        for inst in reg._instances.values():  # noqa: SLF001
            inst.last_poll = clock[0]         # still polling
    known, missing = reg.known_snapshot()
    assert {k["key"] for k in known} == {"work", "personal"}
    assert missing == []


def test_forget_exempts_an_instance_live_via_an_in_flight_poll():
    """The live-exemption term in the age-out, pinned for real.

    `_live_instances_locked` calls an instance alive on EITHER a fresh
    `last_poll` OR `active_polls > 0`. Only the second disjunct can be true while
    `last_poll` is ancient, so that is the state this constructs: a stale
    timestamp AND a poll thread in flight. Such an instance must NOT be forgotten
    — forgetting a connection that is mid-request is the one thing the age-out
    must never do.

    ⚠ Honest scope: this state is believed UNREACHABLE in production. `poll()`
    stamps `last_poll` at entry and again in its `finally`, so an in-flight poll's
    timestamp can be at most `poll_timeout` old — never past KNOWN_FORGET_S. The
    exemption is defensive. This test exists so the clause cannot be deleted as
    'dead' without a red test, since the reasoning that makes it dead lives in a
    different method."""
    clock = [1000.0]
    reg = S.Registry(clock=lambda: clock[0])
    with reg._cond:  # noqa: SLF001
        inst = reg._register_locked("id-work", "work")  # noqa: SLF001
        inst.active_polls = 1                 # a poll thread is parked in _cond.wait
    clock[0] += S.KNOWN_FORGET_S * 2          # ...with an ancient last_poll
    known, missing = reg.known_snapshot()
    assert [k["key"] for k in known] == ["work"], \
        "an instance with a poll IN FLIGHT must never be aged out"
    assert known[0]["connected"] is True
    assert missing == []


# --- `browser health`'s DISCONNECTED line (the only operator-visible surface) - #
def _serve_canned_health(payload):
    """A stub bridge that answers /health with `payload` verbatim, so the CLI's
    rendering can be driven against server shapes the real Registry cannot
    produce (an OLD server with no `known_instances`, a malformed entry)."""
    class H(S.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            body = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):  # silence
            pass

    srv = S.ThreadingHTTPServer(("127.0.0.1", 0), H)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def _run_health(srv, tmp_path):
    tokf = tmp_path / "token"
    tokf.write_text("health-token\n")
    env = dict(os.environ)
    env.update(BROWSER_BRIDGE_HOST="127.0.0.1",
               BROWSER_BRIDGE_PORT=str(srv.server_address[1]),
               BROWSER_BRIDGE_TOKEN_FILE=str(tokf))
    return subprocess.run([str(BROWSER_BIN), "health"], env=env,
                          capture_output=True, text=True, timeout=30)


def test_browser_health_prints_one_DISCONNECTED_line_and_keeps_stdout_json(tmp_path):
    """The whole point of this PR, exercised through the REAL CLI: a dropped
    named instance is glanceable, and stdout stays machine-parseable."""
    srv = _serve_canned_health({
        "ok": True, "extension_connected": True, "count": 1, "instances": [],
        "known_instances": [
            {"key": "personal", "connected": True, "last_seen": "2026-07-31T22:00:00Z",
             "last_unanswered_op": None},
            {"key": "work", "connected": False, "last_seen": "2026-07-31T18:02:34Z",
             "last_unanswered_op": "frames"},
        ],
        "missing": [{"key": "work"}]})
    try:
        r = _run_health(srv, tmp_path)
        assert r.returncode == 0, r.stderr
        json.loads(r.stdout)                    # stdout is UNBROKEN JSON
        lines = [x for x in r.stderr.splitlines() if x.strip()]
        assert lines == [
            "browser: work: DISCONNECTED (last seen 2026-07-31T18:02:34Z, "
            "last unanswered op: frames)"], r.stderr
    finally:
        srv.shutdown(); srv.server_close()


def test_browser_health_is_SILENT_when_every_known_instance_is_connected(tmp_path):
    srv = _serve_canned_health({
        "ok": True, "extension_connected": True, "count": 1, "instances": [],
        "known_instances": [{"key": "work", "connected": True,
                             "last_seen": "2026-07-31T22:00:00Z"}],
        "missing": []})
    try:
        r = _run_health(srv, tmp_path)
        assert r.returncode == 0 and r.stderr.strip() == "", r.stderr
    finally:
        srv.shutdown(); srv.server_close()


def test_browser_health_degrades_silently_against_an_OLD_server(tmp_path):
    """`browser` is deployed as a symlink to the working tree, so it goes live on
    `git pull` — BEFORE the switch that restarts server.py. It therefore WILL run
    against a server with no `known_instances`, and must stay silent and rc 0."""
    srv = _serve_canned_health({"ok": True, "extension_connected": True,
                                "count": 1, "instances": [{"key": "work"}]})
    try:
        r = _run_health(srv, tmp_path)
        assert r.returncode == 0 and r.stderr.strip() == "", r.stderr
        json.loads(r.stdout)
    finally:
        srv.shutdown(); srv.server_close()


def test_browser_health_survives_a_malformed_known_instances(tmp_path):
    """render_missing is the LAST command of the `health` arm, so it owns the
    exit code — a traceback would turn a working probe into rc 1. The try must
    wrap the whole walk, not just the JSON parse."""
    srv = _serve_canned_health({"ok": True, "extension_connected": True,
                                "count": 0, "instances": [],
                                "known_instances": ["not-a-dict", 7, None]})
    try:
        r = _run_health(srv, tmp_path)
        assert r.returncode == 0, r.stderr
        assert "Traceback" not in r.stderr
    finally:
        srv.shutdown(); srv.server_close()


def test_poll_timeout_at_or_above_the_extension_poll_budget_warns(capsys):
    """From extension 0.4.0 on, the extension aborts its own /poll at
    POLL_BUDGET_MS (40s); raising the server's poll_timeout to/past that makes
    every poll abort client-side and the extension backoff-spin. Nothing can
    enforce it across the two processes, so it must at least be said out loud.

    The warning MUST carry its version scope: the extension this server currently
    ships against (0.3.1) does not bound the poll fetch at all, so an unscoped
    message would describe behaviour that does not exist yet to an operator who
    is already debugging."""
    capsys.readouterr()
    S._warn_poll_timeout_vs_extension_budget(25.0)   # noqa: SLF001 — the default
    assert capsys.readouterr().err == "", "the default must be silent"
    S._warn_poll_timeout_vs_extension_budget(45.0)   # noqa: SLF001
    lines = [json.loads(x) for x in capsys.readouterr().err.splitlines() if x]
    assert [x["event"] for x in lines] == ["config_warning"]
    assert lines[0]["reason"] == "poll_timeout_exceeds_extension_poll_budget"
    assert lines[0]["applies_to_extension"] == "0.4.0+"
    assert "0.4.0" in lines[0]["detail"], \
        "the claim must name the extension version it applies to"


def test_extension_connected_still_true_but_known_instances_tells_the_truth():
    """REGRESSION for the exact reported dishonesty: with two profiles wired up
    and one dropped, the boolean stays true (by design, for its existing
    callers) — and the drop is now visible in the SAME payload."""
    reg, clock = _drop_registry()
    clock[0] += S.CONNECT_STALE_S + 1
    with reg._cond:  # noqa: SLF001
        reg._instances["personal"].last_poll = clock[0]  # noqa: SLF001
    assert reg.connected is True, \
        "extension_connected must NOT be silently redefined — callers depend on it"
    _known, missing = reg.known_snapshot()
    assert [m["key"] for m in missing] == ["work"], \
        "the dropped instance must be nameable from the same payload"


def test_health_payload_carries_known_instances_and_missing():
    srv, _ = _serve()
    ext = FakeExtension(srv, instance_id="fake-1", label="work")
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        st, body = _req(srv, "GET", "/health")
        assert st == 200
        assert [k["key"] for k in body["known_instances"]] == ["work"]
        assert body["known_instances"][0]["connected"] is True
        assert body["known_instances"][0]["last_seen"].endswith("Z")
        assert body["missing"] == []
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


def test_instance_lost_is_logged_once_and_names_the_unanswered_command(capsys):
    """The detector. A drop must leave a journal trace naming the op the
    instance never came back from — the fact that would have identified `frames`
    on 2026-07-29 without any code reading."""
    clock = [1000.0]
    reg = S.Registry(clock=lambda: clock[0])
    with reg._cond:  # noqa: SLF001
        inst = reg._register_locked("id-work", "work")  # noqa: SLF001
        # Model a dispatched-but-never-answered command (what `submit` records).
        inst.last_dispatch = {"id": "abc123", "op": "frames", "at": 0.0}
    capsys.readouterr()                       # drop registration noise
    clock[0] += S.CONNECT_STALE_S + 5
    reg.snapshot()
    reg.snapshot()                            # a second probe must NOT re-log
    lines = [json.loads(x) for x in capsys.readouterr().err.splitlines() if x]
    lost = [x for x in lines if x["event"] == "instance_lost"]
    assert len(lost) == 1, f"edge-triggered, not per-probe: {lines}"
    assert lost[0]["key"] == "work"
    assert lost[0]["last_op"] == "frames"
    assert lost[0]["last_id"] == "abc123"
    assert lost[0]["stale_s"] >= S.CONNECT_STALE_S


def test_instance_connected_is_logged_when_a_dropped_instance_returns(capsys):
    clock = [1000.0]
    reg = S.Registry(clock=lambda: clock[0])
    with reg._cond:  # noqa: SLF001
        reg._register_locked("id-work", "work")  # noqa: SLF001
    clock[0] += S.CONNECT_STALE_S + 5
    reg.snapshot()                            # → instance_lost
    with reg._cond:  # noqa: SLF001
        reg._instances["work"].last_poll = clock[0]  # noqa: SLF001
    capsys.readouterr()
    reg.snapshot()                            # → instance_connected
    lines = [json.loads(x) for x in capsys.readouterr().err.splitlines() if x]
    assert [x["event"] for x in lines if x["event"].startswith("instance_")] \
        == ["instance_connected"]


def test_last_dispatch_is_cleared_once_the_command_is_answered():
    """A healthy instance must not report a phantom `last_unanswered_op` — that
    would make every future drop report a stale, misleading op name."""
    srv, reg = _serve()
    ext = FakeExtension(srv, instance_id="fake-1", label="work")
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        st, _ = _req(srv, "POST", "/cmd", {"op": "tabs"})
        assert st == 200
        known, _missing = reg.known_snapshot()
        assert known[0]["last_unanswered_op"] is None
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


def test_last_unanswered_op_survives_a_swallowed_command():
    """The wedge signature: the extension picked the command up and never
    answered. That op name is exactly what the detector must retain."""
    srv, reg = _serve(cmd_timeout=0.3)
    ext = FakeExtension(srv, instance_id="fake-1", label="work", swallow=True)
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        st, _ = _req(srv, "POST", "/cmd", {"op": "frames"})
        assert st == 504
        known, _missing = reg.known_snapshot()
        assert known[0]["last_unanswered_op"] == "frames"
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


# --- fail FAST on an unresolvable target (never wait out cmd_timeout) -------- #
def test_unknown_instance_fails_fast_not_after_cmd_timeout():
    """MEASURED, not asserted-by-construction: a mistyped/unknown --instance must
    be rejected in well under the 20s cmd_timeout. `cmd_timeout` here is 5s, so a
    resolution error that took the timeout path would be unmissable."""
    srv, _ = _serve(cmd_timeout=5.0)
    ext = FakeExtension(srv, instance_id="fake-1", label="work")
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        t0 = time.monotonic()
        st, body = _req(srv, "POST", "/cmd",
                        {"op": "tabs", "target": "nosuchlabel"})
        elapsed = time.monotonic() - t0
        assert st == 404 and body["error"] == "unknown_instance"
        assert elapsed < 1.0, f"took {elapsed:.2f}s — it went down a waiting path"
        # And it names what IS known, so a typo and a silent drop are separable.
        assert [k["key"] for k in body["known_instances"]] == ["work"]
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


def test_no_extension_fails_fast_and_names_the_instance_that_dropped():
    """A profile that HAS dropped (no in-flight poll, last_poll older than
    CONNECT_STALE_S) must fail fast and be NAMED.

    ⚠ Scope of this claim, measured: this is the settled state AFTER the
    transition window. While an instance still has a poll thread in flight the
    server correctly considers it live and the command waits out `cmd_timeout` —
    that bounded (≤ poll_timeout + CONNECT_STALE_S) window is unchanged here and
    is not something a resolution-time check can remove.
    """
    clock = [1000.0]
    reg = S.Registry(clock=lambda: clock[0])
    with reg._cond:  # noqa: SLF001
        reg._register_locked("fake-1", "work")  # noqa: SLF001
    clock[0] += S.CONNECT_STALE_S + 1          # it stopped polling; nothing in flight
    srv, _ = _serve(cmd_timeout=5.0, registry=reg)
    try:
        t0 = time.monotonic()
        st, body = _req(srv, "POST", "/cmd", {"op": "tabs", "target": "work"})
        elapsed = time.monotonic() - t0
        assert st == 503 and body["error"] == "extension_not_connected"
        assert elapsed < 1.0, f"took {elapsed:.2f}s — it went down a waiting path"
        assert [k["key"] for k in body["known_instances"]] == ["work"]
        assert body["known_instances"][0]["connected"] is False
    finally:
        srv.shutdown(); srv.server_close()


# --------------------------------------------------------------------------- #
# `emulate`: device emulation — the OWNED-TAB-ONLY blast-radius gate
#
# The extension-side behaviour (sticky re-application, the screenshot fast-path
# refusal, touch clicks) is covered by tests/emulation.test.mjs. What lives HERE is
# the half only the server knows: which tab a session may emulate at all.
# --------------------------------------------------------------------------- #
def test_emulate_is_a_known_tab_scoped_op():
    assert "emulate" in S.ALLOWED_OPS
    assert "emulate" in S.TAB_SCOPED_OPS, \
        "emulate acts on ONE tab, so it must serialize per-tab like every other"
    assert "emulate" in S.OWNED_TAB_ONLY_OPS


def test_emulate_refused_when_the_session_owns_no_tab():
    """No `open` -> `not_owned_tab`, NOT the active-tab fallback every other
    tab-scoped op gets. The fallback is safe for a READ; for `emulate` it would
    resize the tab the OPERATOR is looking at."""
    srv, _ = _serve()
    ext = FakeExtension(srv, instance_id="solo", label="only",
                        executor=_tab_echo_exec([]))
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        st, body = _cmd(srv, {"op": "emulate", "device": "iphone-15"}, session="A")
        assert st == 409
        assert body["error"] == "not_owned_tab"
        # It never reached the browser — nothing was emulated.
        assert all(c["op"] != "emulate" for c in ext.dispatched)
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


def test_emulate_refused_when_tab_override_points_at_someone_elses_tab():
    """THE reach-around: the session DOES own a tab, but `--tab` names another.
    Without this check an agent could emulate any tab id it can guess, including
    the operator's."""
    srv, reg = _serve()
    ext = FakeExtension(srv, instance_id="solo", label="only",
                        executor=_tab_echo_exec([101]))
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        _cmd(srv, {"op": "open"}, session="A")
        assert reg.owners_snapshot() == {("only", "A"): 101}
        st, body = _req(srv, "POST", "/cmd",
                        {"op": "emulate", "device": "iphone-15", "tab": 999},
                        headers={S.HDR_SESSION_ID: "A"})
        assert st == 409
        assert body["error"] == "not_owned_tab"
        assert all(c["op"] != "emulate" for c in ext.dispatched)
        # A plain read with the same override is STILL allowed — the gate is
        # scoped to OWNED_TAB_ONLY_OPS, it did not tighten everything.
        st, body = _req(srv, "POST", "/cmd", {"op": "getHtml", "tab": 999},
                        headers={S.HDR_SESSION_ID: "A"})
        assert st == 200 and body["result"]["data"]["tabId"] == 999
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


def test_emulate_refused_for_a_session_with_no_session_id_at_all():
    """No X-Session-Id -> owns nothing -> refused. A raw token-holder cannot skip
    the gate by simply omitting the routing header."""
    srv, _ = _serve()
    ext = FakeExtension(srv, instance_id="solo", label="only",
                        executor=_tab_echo_exec([]))
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        st, body = _req(srv, "POST", "/cmd", {"op": "emulate", "device": "iphone-15"})
        assert st == 409
        assert body["error"] == "not_owned_tab"
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


def test_emulate_on_the_owned_tab_is_dispatched_with_that_tabId():
    srv, reg = _serve()
    ext = FakeExtension(srv, instance_id="solo", label="only",
                        executor=_tab_echo_exec([101]))
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        _cmd(srv, {"op": "open"}, session="A")
        st, body = _cmd(srv, {"op": "emulate", "device": "iphone-15"}, session="A")
        assert st == 200
        assert body["result"]["data"]["tabId"] == 101
        emu = [c for c in ext.dispatched if c["op"] == "emulate"]
        assert len(emu) == 1
        assert emu[0]["tabId"] == 101
        # The device field is forwarded verbatim — the extension is the single
        # authority on validating it (one rule, one place).
        assert emu[0]["device"] == "iphone-15"
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


def test_emulate_with_an_explicit_tab_equal_to_the_owned_tab_is_allowed():
    """`--tab <my own tab>` is the same tab, so it passes. The gate is 'not
    yours', not 'never use --tab'."""
    srv, _ = _serve()
    ext = FakeExtension(srv, instance_id="solo", label="only",
                        executor=_tab_echo_exec([101]))
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        _cmd(srv, {"op": "open"}, session="A")
        st, body = _req(srv, "POST", "/cmd",
                        {"op": "emulate", "device": "iphone-15", "tab": 101},
                        headers={S.HDR_SESSION_ID: "A"})
        assert st == 200
        assert body["result"]["data"]["tabId"] == 101
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


def test_emulate_telemetry_is_metadata_only(telemetry):
    """device/viewport are fine; the UA string and the geolocation COORDINATES
    are not. An emulated lat/lon is a place the operator chose to pretend to be
    — its presence is worth recording, its value is not the bridge's business."""
    spool_dir = telemetry
    srv, _ = _serve()
    ext = FakeExtension(srv, instance_id="solo", label="only",
                        executor=_tab_echo_exec([101]))
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        _cmd(srv, {"op": "open"}, session="A")
        _wait_events(spool_dir, 1)
        st, _ = _cmd(srv, {"op": "emulate", "device": "pixel-8",
                           "width": 412, "height": 915, "mobile": True,
                           "touch": True,
                           "ua": "Mozilla/5.0 (Linux; Android 14; SECRETMODEL)",
                           "geo": {"latitude": 51.5074, "longitude": -0.1278}},
                      session="A")
        assert st == 200
        evs = _wait_events(spool_dir, 2)
        e = [x for x in evs if json.loads(x["payload"])["op"] == "emulate"][0]
        p = json.loads(e["payload"])
        assert p["emu_device"] == "pixel-8"
        assert p["emu_width"] == 412 and p["emu_height"] == 915
        assert p["emu_mobile"] is True and p["emu_touch"] is True
        assert p["emu_geo"] is True, "the PRESENCE of a geo override is recorded"
        blob = json.dumps(p)
        assert "SECRETMODEL" not in blob, "the UA string must never be emitted"
        assert "51.5074" not in blob and "-0.1278" not in blob, \
            "emulated coordinates must never be emitted"
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


def test_emulate_reset_telemetry_says_reset_and_nothing_else():
    assert S._emulate_extra({"reset": True}) == {"emu_reset": True}  # noqa: SLF001
    # A raw (preset-less) emulation is labelled "raw", never left absent.
    out = S._emulate_extra({"width": 390, "height": 844})            # noqa: SLF001
    assert out["emu_device"] == "raw"
    assert out["emu_geo"] is False
    # A non-dict body cannot raise (telemetry is best-effort by contract).
    assert S._emulate_extra(None) == {}                              # noqa: SLF001


def test_not_owned_tab_is_distinct_from_no_owned_tab():
    """Two different refusals with two different remedies. Collapsing them would
    tell an operator to run `browser open` when the real problem is that they
    pointed --tab at their own window."""
    assert S.NotOwnedTab is not S.NoOwnedTab
    assert not issubclass(S.NotOwnedTab, S.NoOwnedTab)
    assert not issubclass(S.NoOwnedTab, S.NotOwnedTab)


def test_poll_budget_warning_still_claims_extension_0_4_0_plus():
    """#248's `applies_to_extension: "0.4.0+"` claim must stay accurate after the
    manifest bump to 0.5.0 — 0.5.0 IS 0.4.0+, so the claim holds and the string
    must NOT have been 'helpfully' bumped along with the manifest."""
    manifest = json.loads(
        (Path(__file__).resolve().parents[1] / "extension" / "manifest.json")
        .read_text())
    major, minor, _ = (int(x) for x in manifest["version"].split("."))
    assert (major, minor) >= (0, 4), \
        f"manifest {manifest['version']} no longer satisfies 0.4.0+"
