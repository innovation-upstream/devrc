"""Tests for the browser receiver: event→fields mapping, POST→spool over a real
loopback socket, and round-trip through the existing collector.parse_line, incl.
arbitrary content (quotes / newlines / unicode / a fake password in the URL)."""
import json
import sys
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

EXT = Path(__file__).resolve().parent.parent      # browser-ext
COLLECTOR = EXT.parent                              # scripts/collector
KEYLOG = COLLECTOR / "keylog"
sys.path.insert(0, str(EXT))
sys.path.insert(0, str(KEYLOG))
sys.path.insert(0, str(COLLECTOR))

import receiver as R  # noqa: E402
import collector as C  # noqa: E402


def test_event_to_fields_nav():
    f = R.event_to_fields(
        {"kind": "nav", "url": "https://x.test/a?b=c", "title": "Hi",
         "active_ms": 1234, "ts": 99},
        "chromium",
    )
    assert f["source"] == "browser"
    assert f["kind"] == "nav"
    assert f["text"] == "https://x.test/a?b=c"
    assert f["app"] == "chromium"
    pl = json.loads(f["payload"])
    assert pl["title"] == "Hi"
    assert pl["active_ms"] == 1234
    assert pl["client_ts"] == 99


def test_event_to_fields_focus_and_defaults():
    f = R.event_to_fields({"kind": "focus", "state": "idle"}, "brave")
    assert f["kind"] == "focus"
    assert f["text"] == ""
    assert f["app"] == "brave"
    assert json.loads(f["payload"])["state"] == "idle"


def test_unknown_kind_coerced_to_nav():
    f = R.event_to_fields({"kind": "weird"}, "chromium")
    assert f["kind"] == "nav"


def test_event_to_fields_carries_scroll_metrics():
    f = R.event_to_fields(
        {"kind": "nav", "url": "https://x.test/a", "title": "T",
         "active_ms": 10, "scroll_pct": 73, "scroll_ms": 4200},
        "chromium",
    )
    pl = json.loads(f["payload"])
    assert pl["scroll_pct"] == 73
    assert pl["scroll_ms"] == 4200


def test_event_to_fields_scroll_defaults_to_zero():
    # An event WITHOUT scroll metrics (focus event / older client) still maps,
    # defaulting both to 0.
    f = R.event_to_fields({"kind": "nav", "url": "https://x.test/a"}, "chromium")
    pl = json.loads(f["payload"])
    assert pl["scroll_pct"] == 0
    assert pl["scroll_ms"] == 0


def test_fields_roundtrip_through_parse_line():
    f = R.event_to_fields(
        {"kind": "nav", "url": "https://site/x", "title": "T", "active_ms": 5},
        "chromium",
    )
    line = __import__("spool_emit").build_line(f)
    ev = C.parse_line(line)
    assert ev["source"] == "browser"
    assert ev["text"] == "https://site/x"
    assert json.loads(ev["payload"])["active_ms"] == 5


def _serve(spool_dir, app="chromium"):
    handler = R.make_handler(spool_dir, app)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv


def _post(srv, obj):
    port = srv.server_address[1]
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/event",
        data=json.dumps(obj).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=5) as r:
        return r.status, r.read()


def test_post_writes_spool_record(tmp_path):
    spool = tmp_path / "spool"
    srv = _serve(spool)
    try:
        status, _ = _post(srv, {
            "kind": "nav",
            "url": "https://example.test/path?token=password123!",
            "title": 'weird "quoted"\ttab\nnewline 你好',
            "active_ms": 4200,
        })
        assert status == 200
    finally:
        srv.shutdown()
        srv.server_close()

    cur = spool / "current.log"
    lines = [l for l in cur.read_text(encoding="utf-8").splitlines() if l]
    assert len(lines) == 1
    ev = C.parse_line(lines[0])
    assert ev is not None
    assert ev["source"] == "browser"
    assert ev["text"] == "https://example.test/path?token=password123!"
    pl = json.loads(ev["payload"])
    assert pl["title"] == 'weird "quoted"\ttab\nnewline 你好'
    assert pl["active_ms"] == 4200


def test_post_scroll_metrics_land_in_spool(tmp_path):
    spool = tmp_path / "spool"
    srv = _serve(spool)
    try:
        status, _ = _post(srv, {
            "kind": "nav",
            "url": "https://example.test/article",
            "title": "Long read",
            "active_ms": 90000,
            "scroll_pct": 88,
            "scroll_ms": 12500,
        })
        assert status == 200
    finally:
        srv.shutdown()
        srv.server_close()
    ev = C.parse_line((spool / "current.log").read_text().splitlines()[0])
    pl = json.loads(ev["payload"])
    assert pl["scroll_pct"] == 88
    assert pl["scroll_ms"] == 12500


def test_post_without_scroll_metrics_defaults_zero(tmp_path):
    spool = tmp_path / "spool"
    srv = _serve(spool)
    try:
        _post(srv, {"kind": "nav", "url": "https://example.test/x", "title": "t"})
    finally:
        srv.shutdown()
        srv.server_close()
    ev = C.parse_line((spool / "current.log").read_text().splitlines()[0])
    pl = json.loads(ev["payload"])
    assert pl["scroll_pct"] == 0
    assert pl["scroll_ms"] == 0


def test_post_arbitrary_url_content_survives(tmp_path):
    spool = tmp_path / "spool"
    srv = _serve(spool)
    nasty_url = "https://h/p?q=a%20b&x=\"';\n\t你好&pw=password123!"
    try:
        _post(srv, {"kind": "nav", "url": nasty_url, "title": "t"})
    finally:
        srv.shutdown()
        srv.server_close()
    ev = C.parse_line((spool / "current.log").read_text().splitlines()[0])
    assert ev["text"] == nasty_url


def test_bad_json_returns_400(tmp_path):
    spool = tmp_path / "spool"
    srv = _serve(spool)
    try:
        port = srv.server_address[1]
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/event",
            data=b"not json{{{",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            urllib.request.urlopen(req, timeout=5)
            assert False, "expected HTTP 400"
        except urllib.error.HTTPError as e:
            assert e.code == 400
    finally:
        srv.shutdown()
        srv.server_close()
    # No spool record written.
    cur = spool / "current.log"
    assert not cur.exists() or cur.read_text() == ""


def test_wrong_path_404(tmp_path):
    srv = _serve(tmp_path / "spool")
    try:
        port = srv.server_address[1]
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/nope", timeout=5)
            assert False
        except urllib.error.HTTPError as e:
            assert e.code == 404
    finally:
        srv.shutdown()
        srv.server_close()
