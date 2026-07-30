"""Sidecar: auth on every endpoint, the non-loopback bind refusal, each route's
happy path and its malformed payloads.

Fully headless — a real ThreadingHTTPServer on an ephemeral loopback port, no
extension, no browser.
"""
from __future__ import annotations

import json
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import server as S  # noqa: E402
from fetcher import Fetcher  # noqa: E402

TOKEN = "test-token-abc123"


class Spawner:
    def __init__(self):
        self.argvs = []

    def __call__(self, argv, cwd=None):
        self.argvs.append(argv)

        class P:
            def poll(self_inner):
                return None

            def terminate(self_inner):
                pass

        return P()


@pytest.fixture
def app(cfg, store, dir_index, file_index, library, clock):
    return S.App(cfg, store=store, dir_index=dir_index, file_index=file_index,
                 fetcher=Fetcher(library, runner=Spawner(), clock=clock),
                 clock=clock)


@pytest.fixture
def live(app):
    """A running server on an ephemeral loopback port."""
    server = S.build_server("127.0.0.1", 0, app, TOKEN)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{port}"
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


def call(base, method, path, payload=None, token=TOKEN, headers=None):
    data = json.dumps(payload).encode() if payload is not None else None
    hdrs = {"Content-Type": "application/json"}
    if token is not None:
        hdrs["Authorization"] = f"Bearer {token}"
    hdrs.update(headers or {})
    req = urllib.request.Request(base + path, data=data, method=method,
                                 headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read().decode()
            return resp.status, (json.loads(body) if body else None), resp.headers
    except urllib.error.HTTPError as exc:
        body = exc.read().decode()
        return exc.code, (json.loads(body) if body else None), exc.headers


# --- bind policy ----------------------------------------------------------- #
@pytest.mark.parametrize("host", ["0.0.0.0", "192.0.2.1", "::",  # TEST-NET-1
                                  "example-site.test"])
def test_non_loopback_bind_is_refused(app, host):
    with pytest.raises(ValueError, match="non-loopback"):
        S.build_server(host, 0, app, TOKEN)


def test_loopback_bind_is_allowed(app):
    server = S.build_server("127.0.0.1", 0, app, TOKEN)
    server.server_close()


# --- auth ------------------------------------------------------------------ #
ENDPOINTS = [("GET", "/healthz", None), ("GET", "/dirs", None),
             ("GET", "/log", None), ("GET", "/fetch/xyz", None),
             ("POST", "/match", {}), ("POST", "/learn", {}),
             ("POST", "/mkdir", {}), ("POST", "/relocate", {}),
             ("POST", "/fetch", {})]


@pytest.mark.parametrize("method,path,payload", ENDPOINTS)
def test_every_endpoint_requires_a_token(live, method, path, payload):
    status, body, _ = call(live, method, path, payload, token=None)
    assert status == 401
    assert body["error"] == "unauthorized"


@pytest.mark.parametrize("method,path,payload", ENDPOINTS)
def test_every_endpoint_rejects_a_wrong_token(live, method, path, payload):
    status, _, _ = call(live, method, path, payload, token="wrong")
    assert status == 401


def test_malformed_authorization_header_is_rejected(live):
    for value in ["", "Basic abc", "Bearer", "bearer " + TOKEN, TOKEN]:
        req = urllib.request.Request(live + "/healthz",
                                     headers={"Authorization": value})
        try:
            urllib.request.urlopen(req, timeout=5)
            raise AssertionError(f"accepted {value!r}")
        except urllib.error.HTTPError as exc:
            assert exc.code == 401


def test_off_host_header_is_refused(live):
    status, body, _ = call(live, "GET", "/healthz",
                           headers={"Host": "evil.example"})
    assert status == 403
    assert body["error"] == "bad_host"


# --- routes ---------------------------------------------------------------- #
def test_healthz(live):
    status, body, _ = call(live, "GET", "/healthz")
    assert status == 200
    assert body["ok"] is True
    assert body["configured"] is True
    assert body["dirs"] >= 5


def test_dirs_snapshot_and_etag(live):
    status, body, headers = call(live, "GET", "/dirs")
    assert status == 200
    assert {d["name"] for d in body["dirs"]} >= {"Jane Doe", "john-smith"}
    assert body["otherDir"] == "other"
    assert "threshold" in body and "matchTimeoutMs" in body
    etag = headers["ETag"]
    status2, body2, _ = call(live, "GET", "/dirs",
                             headers={"If-None-Match": etag})
    assert status2 == 304
    assert body2 is None


def test_dirs_snapshot_carries_the_site_rules(live, app):
    app.cfg.data["site_rules"] = {"example-site.test": {"tags": [".tag a"]}}
    _, body, _ = call(live, "GET", "/dirs")
    assert body["siteRules"]["example-site.test"]["tags"] == [".tag a"]


def test_match_returns_the_contract_shape(live):
    status, body, _ = call(live, "POST", "/match", {
        "url": "https://example-site.test/dl/1",
        "filename": "clip.mp4",
        "page": {"url": "https://example-site.test/v/1", "tags": ["Jane Doe"]},
    })
    assert status == 200
    assert body["dir"] == "Jane Doe"
    assert body["auto"] is True
    assert set(body) == {"dir", "confidence", "reason", "auto", "candidates",
                         "suggestNew", "dup", "ttlMs"}


def test_match_logs_the_route(live, store):
    call(live, "POST", "/match",
         {"filename": "x.mp4", "page": {"tags": ["Jane Doe"]}})
    rows = store.recent_routes(1)
    assert rows[0]["dir"] == "Jane Doe"


def test_match_surfaces_a_duplicate(live, library):
    (library / "Jane Doe" / "clip.mp4").write_text("payload")
    _, body, _ = call(live, "POST", "/match",
                      {"filename": "clip.mp4",
                       "page": {"tags": ["Jane Doe"]}})
    assert body["dup"]["relpath"] == "Jane Doe/clip.mp4"
    assert body["dup"]["where"] == "target-dir"


def test_match_uses_the_host_prior_from_the_store(live, store):
    store.set_host_prior("example-site.test", "Jane Doe")
    _, body, _ = call(live, "POST", "/match", {
        "filename": "jane_doe.mp4",
        "page": {"url": "https://example-site.test/v"},
    })
    assert "+host-prior" in body["candidates"][0]["reason"]


def test_learn_writes_an_alias_and_an_example(live, store):
    status, body, _ = call(live, "POST", "/learn", {
        "context": {"page": {"url": "https://example-site.test/v",
                             "tags": ["Aster Nightingale"]}},
        "chosenDir": "Jane Doe",
        "autoDir": "other",
    })
    assert status == 200 and body["ok"] is True
    assert store.alias("asternightingale", "example-site.test") == "Jane Doe"
    assert store.host_prior("example-site.test") == "Jane Doe"
    assert store.examples()[0]["chosen_dir"] == "Jane Doe"


def test_learn_refuses_an_unknown_or_unsafe_directory(live):
    for bad in ["Not A Real Dir", "../escape", "", None, 42]:
        status, body, _ = call(live, "POST", "/learn",
                               {"context": {}, "chosenDir": bad})
        assert status == 400, bad
        assert body["error"] == "unsafe"


def test_mkdir_creates_a_directory_and_refreshes_the_index(live, library):
    status, body, _ = call(live, "POST", "/mkdir", {"name": "Aster Nightingale"})
    assert status == 200
    assert body["created"] is True
    assert (library / "Aster Nightingale").is_dir()
    _, dirs, _ = call(live, "GET", "/dirs")
    assert "Aster Nightingale" in {d["name"] for d in dirs["dirs"]}


def test_mkdir_is_idempotent(live):
    call(live, "POST", "/mkdir", {"name": "Aster Nightingale"})
    _, body, _ = call(live, "POST", "/mkdir", {"name": "Aster Nightingale"})
    assert body["created"] is False


@pytest.mark.parametrize("name", ["..", "../escape", "a/b", "/abs", "",
                                  "x" * 200, "with\x01ctrl"])
def test_mkdir_refuses_unsafe_names(live, library, name):
    status, body, _ = call(live, "POST", "/mkdir", {"name": name})
    assert status == 400
    assert body["error"] == "unsafe"


def test_mkdir_traversal_creates_nothing_outside_the_root(live, library):
    call(live, "POST", "/mkdir", {"name": "../pwned"})
    assert not (library.parent / "pwned").exists()


def test_relocate_moves_a_file_within_the_library(live, library):
    (library / "other" / "clip.mp4").write_text("payload")
    status, body, _ = call(live, "POST", "/relocate",
                           {"fromRelPath": "other/clip.mp4",
                            "toDir": "Jane Doe"})
    assert status == 200
    assert body["moved"] is True
    assert (library / "Jane Doe" / "clip.mp4").read_text() == "payload"
    assert not (library / "other" / "clip.mp4").exists()


def test_relocate_uniquifies_instead_of_overwriting(live, library):
    (library / "other" / "clip.mp4").write_text("new")
    (library / "Jane Doe" / "clip.mp4").write_text("old")
    _, body, _ = call(live, "POST", "/relocate",
                      {"fromRelPath": "other/clip.mp4", "toDir": "Jane Doe"})
    assert (library / "Jane Doe" / "clip.mp4").read_text() == "old"
    assert body["relPath"] == "Jane Doe/clip (1).mp4"


@pytest.mark.parametrize("rel", ["../../etc/passwd", "/etc/passwd",
                                 "Jane Doe/../../out"])
def test_relocate_refuses_a_traversing_source(live, rel):
    status, body, _ = call(live, "POST", "/relocate",
                           {"fromRelPath": rel, "toDir": "Jane Doe"})
    assert status == 400
    assert body["error"] == "unsafe"


def test_relocate_refuses_an_unknown_target(live, library):
    (library / "other" / "clip.mp4").write_text("x")
    status, _, _ = call(live, "POST", "/relocate",
                        {"fromRelPath": "other/clip.mp4",
                         "toDir": "Nope"})
    assert status == 400


def test_relocate_missing_source_is_404(live):
    status, body, _ = call(live, "POST", "/relocate",
                           {"fromRelPath": "other/nope.mp4",
                            "toDir": "Jane Doe"})
    assert status == 404


def test_fetch_starts_a_job_and_reports_status(live):
    status, body, _ = call(live, "POST", "/fetch",
                           {"url": "https://example-site.test/v/1",
                            "dir": "Jane Doe"})
    assert status == 200
    job_id = body["jobId"]
    status2, body2, _ = call(live, "GET", f"/fetch/{job_id}")
    assert status2 == 200
    assert body2["state"] == "running"


def test_fetch_refuses_a_non_http_url(live):
    for bad in ["file:///etc/passwd", "javascript:x", "-oProxyCommand=id", ""]:
        status, _, _ = call(live, "POST", "/fetch", {"url": bad, "dir": "other"})
        assert status == 400, bad


def test_fetch_status_of_an_unknown_job_is_404(live):
    status, body, _ = call(live, "GET", "/fetch/nope")
    assert status == 404
    assert body["error"] == "no_such_job"


def test_log_returns_recent_routes(live, store):
    for i in range(3):
        store.log_route(dir_name=f"d{i}")
    status, body, _ = call(live, "GET", "/log?limit=2")
    assert status == 200
    assert [r["dir"] for r in body["routes"]] == ["d2", "d1"]


def test_log_limit_is_clamped_and_tolerates_garbage(live, store):
    store.log_route(dir_name="d")
    for query in ["?limit=abc", "?limit=-5", "?limit=99999", ""]:
        status, _, _ = call(live, "GET", "/log" + query)
        assert status == 200


# --- malformed payloads ---------------------------------------------------- #
def test_unknown_endpoints_are_404(live):
    assert call(live, "GET", "/nope")[0] == 404
    assert call(live, "POST", "/nope", {})[0] == 404


def test_empty_body_is_rejected(live):
    req = urllib.request.Request(
        live + "/match", data=b"", method="POST",
        headers={"Authorization": f"Bearer {TOKEN}",
                 "Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=5)
        raise AssertionError("empty body accepted")
    except urllib.error.HTTPError as exc:
        assert exc.code == 400


def test_non_json_body_is_rejected(live):
    req = urllib.request.Request(
        live + "/match", data=b"<<<not json>>>", method="POST",
        headers={"Authorization": f"Bearer {TOKEN}",
                 "Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=5)
        raise AssertionError("garbage accepted")
    except urllib.error.HTTPError as exc:
        assert exc.code == 400


def test_non_object_json_body_is_rejected(live):
    for payload in ["[1,2,3]", '"string"', "42", "null"]:
        req = urllib.request.Request(
            live + "/match", data=payload.encode(), method="POST",
            headers={"Authorization": f"Bearer {TOKEN}",
                     "Content-Type": "application/json"})
        try:
            urllib.request.urlopen(req, timeout=5)
            raise AssertionError(f"accepted {payload}")
        except urllib.error.HTTPError as exc:
            assert exc.code == 400


def test_oversized_body_is_rejected(live):
    payload = json.dumps({"filename": "x" * (S.MAX_BODY + 10)}).encode()
    req = urllib.request.Request(
        live + "/match", data=payload, method="POST",
        headers={"Authorization": f"Bearer {TOKEN}",
                 "Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=10)
        raise AssertionError("oversized body accepted")
    except urllib.error.HTTPError as exc:
        assert exc.code == 400


def test_hostile_match_payloads_never_500(live):
    payloads = [
        {}, {"page": None}, {"page": {"tags": None}},
        {"page": {"tags": [1, 2, 3]}}, {"filename": None},
        {"page": {"og": "not-a-dict"}}, {"size": "huge"},
        {"page": {"tags": ["x" * 5000]}},
        {"filename": "../../etc/passwd"},
    ]
    for payload in payloads:
        status, body, _ = call(live, "POST", "/match", payload)
        assert status == 200, (payload, status, body)


def test_nosniff_header_is_always_sent(live):
    _, _, headers = call(live, "GET", "/healthz")
    assert headers["X-Content-Type-Options"] == "nosniff"


# --- unconfigured mode ----------------------------------------------------- #
def test_routing_endpoints_are_503_without_a_library_root(tmp_path, store):
    import config as config_mod
    data = config_mod._deep_merge(config_mod.DEFAULTS, {"library_root": ""})
    cfg2 = config_mod.Config(data, path=tmp_path / "c.toml",
                             state_dir=tmp_path / "s",
                             token_file=tmp_path / "t")
    app = S.App(cfg2, store=store)
    assert app.configured is False
    server = S.build_server("127.0.0.1", 0, app, TOKEN)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{port}"
        assert call(base, "GET", "/healthz")[0] == 200
        assert call(base, "GET", "/dirs")[0] == 503
        assert call(base, "POST", "/match", {"filename": "x"})[0] == 503
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
