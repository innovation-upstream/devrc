"""`urllib_transport` -- the transport that actually talks to qBittorrent.

Every other qbt test injects a stub, so the production transport had NO
coverage: the cookie jar that carries the SID session, the form encoding, the
CSRF Referer/Origin headers, and the error mapping were all unexercised.

These run against a throwaway loopback HTTP server. The live instance is never
contacted -- it is a production seeding target.
"""
from __future__ import annotations

import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from qbt import QbtClient, QbtError, urllib_transport  # noqa: E402


class Recorder(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    seen: list = []

    def log_message(self, *a):
        pass

    def _respond(self, body=b"Ok.", status=200, cookie=None):
        self.send_response(status)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        Recorder.seen.append(("GET", self.path, dict(self.headers), b""))
        if "/torrents/info" in self.path:
            self._respond(b'[{"hash":"H1","name":"n","save_path":"/downloads"}]')
        elif "/torrents/files" in self.path:
            self._respond(b'[{"name":"a.mp4"}]')
        elif self.path.endswith("/boom"):
            self._respond(b"nope", status=500)
        else:
            self._respond(b"Ok.")

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length)
        Recorder.seen.append(("POST", self.path, dict(self.headers), body))
        self._respond(b"Ok.", cookie="SID=abc123; Path=/")


@pytest.fixture
def stub_server():
    Recorder.seen = []
    srv = HTTPServer(("127.0.0.1", 0), Recorder)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}", Recorder
    srv.shutdown()
    srv.server_close()
    thread.join(timeout=5)


def test_it_posts_form_encoded_credentials_with_the_csrf_headers(stub_server):
    base, rec = stub_server
    QbtClient(base, "user", "pw").login()
    method, path, headers, body = rec.seen[0]
    assert method == "POST"
    assert path.endswith("/api/v2/auth/login")
    assert b"username=user" in body and b"password=pw" in body
    # qBittorrent rejects an off-host Referer/Origin unless CSRF is disabled.
    assert headers["Referer"] == base
    assert headers["Origin"] == base


def test_it_carries_the_session_cookie_forward(stub_server):
    """qBittorrent authenticates by SID cookie; without the jar every call
    after login would 403."""
    base, rec = stub_server
    QbtClient(base, "user", "pw").torrents_info()
    _, _, headers, _ = rec.seen[-1]
    assert "SID=abc123" in headers.get("Cookie", "")


def test_a_real_round_trip_through_the_default_transport(stub_server):
    base, _ = stub_server
    c = QbtClient(base, "user", "pw")
    assert c.torrents_info() == [{"hash": "H1", "name": "n",
                                  "save_path": "/downloads"}]
    assert c.torrents_files("H1") == [{"name": "a.mp4"}]


def test_an_http_error_comes_back_as_a_response_not_an_exception(stub_server):
    """The setLocation error paths classify by status, so a 4xx/5xx has to
    reach the caller as a Response."""
    base, _ = stub_server
    resp = urllib_transport(timeout=5)("GET", f"{base}/boom", None, {})
    assert resp.status == 500
    assert resp.text() == "nope"


def test_a_connection_failure_becomes_a_qbterror():
    transport = urllib_transport(timeout=1)
    with pytest.raises(QbtError):
        # Port 1 on loopback: reserved, never listening.
        transport("GET", "http://127.0.0.1:1/api/v2/torrents/info", None, {})


def test_a_client_built_without_a_transport_uses_urllib(stub_server):
    base, _ = stub_server
    c = QbtClient(base, "user", "pw")
    assert c._transport is not None
    c.login()
    assert c._logged_in is True


def test_each_client_gets_its_own_cookie_jar(stub_server):
    """Two clients must not share a session -- one logging out or expiring
    would silently break the other."""
    base, _ = stub_server
    a, b = QbtClient(base, "u", "p"), QbtClient(base, "u", "p")
    assert a._transport is not b._transport
