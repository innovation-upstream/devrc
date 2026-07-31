"""Sidecar: auth on every endpoint, the non-loopback bind refusal, each route's
happy path and its malformed payloads.

Fully headless — a real ThreadingHTTPServer on an ephemeral loopback port, no
extension, no browser.
"""
from __future__ import annotations

import json
import os
import socket
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config as config_mod  # noqa: E402
import server as S  # noqa: E402
from fetcher import Fetcher  # noqa: E402
from store import Store  # noqa: E402

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


# --- relocate: provenance is mandatory ------------------------------------- #
#
# /relocate is an os.rename inside a LIVE qBittorrent seeding target. It used
# to move any file that existed at the supplied relative path -- correct
# `toDir` validation, correct `safe_rel_path` confinement, no path escape, and
# still able to rename a torrent payload out from under qBittorrent on nothing
# but a string from the extension. It now demands proof the router created the
# file.

def route_a_download(base, download_id, *, tags=(), filename="clip.mp4"):
    """Do what the extension does on a real download: record a /match decision
    against the browser's DownloadItem id. That record is the provenance."""
    status, body, _ = call(base, "POST", "/match", {
        "downloadId": download_id,
        "filename": filename,
        "page": {"url": "https://example-site.test/v/1", "tags": list(tags)},
    })
    assert status == 200, body
    return body


def test_relocate_moves_a_file_within_the_library(live, library):
    (library / "other" / "clip.mp4").write_text("payload")
    route_a_download(live, 41)
    status, body, _ = call(live, "POST", "/relocate",
                           {"fromRelPath": "other/clip.mp4",
                            "toDir": "Jane Doe", "downloadId": 41})
    assert status == 200
    assert body["moved"] is True
    assert (library / "Jane Doe" / "clip.mp4").read_text() == "payload"
    assert not (library / "other" / "clip.mp4").exists()


def test_relocate_uniquifies_instead_of_overwriting(live, library):
    (library / "other" / "clip.mp4").write_text("new")
    (library / "Jane Doe" / "clip.mp4").write_text("old")
    route_a_download(live, 42)
    _, body, _ = call(live, "POST", "/relocate",
                      {"fromRelPath": "other/clip.mp4", "toDir": "Jane Doe",
                       "downloadId": 42})
    assert (library / "Jane Doe" / "clip.mp4").read_text() == "old"
    assert body["relPath"] == "Jane Doe/clip (1).mp4"


def test_relocate_refuses_a_file_the_router_never_routed(live, library):
    """THE finding. A pre-existing file at a perfectly valid relative path --
    exactly what a loose torrent payload under a subject directory looks like."""
    victim = library / "Jane Doe" / "seeding-payload.mp4"
    victim.write_text("torrent payload")
    status, body, _ = call(live, "POST", "/relocate",
                           {"fromRelPath": "Jane Doe/seeding-payload.mp4",
                            "toDir": "john-smith"})
    assert status == 400
    assert body["error"] == "unsafe"
    assert "cannot prove" in body["detail"]
    assert victim.read_text() == "torrent payload", "the file must not move"
    assert not (library / "john-smith" / "seeding-payload.mp4").exists()


def test_relocate_refuses_an_unknown_download_id(live, library):
    (library / "Jane Doe" / "payload.mp4").write_text("x")
    status, body, _ = call(live, "POST", "/relocate",
                           {"fromRelPath": "Jane Doe/payload.mp4",
                            "toDir": "john-smith", "downloadId": 999999})
    assert status == 400
    assert "no routing decision on record" in body["detail"]
    assert (library / "Jane Doe" / "payload.mp4").exists()


def test_relocate_refuses_when_the_route_named_a_different_directory(live,
                                                                     library):
    """A real download id, but pointed at a file sitting somewhere else. Without
    this check one legitimate download would authorise moving any file in the
    library."""
    (library / "Jane Doe" / "someone-elses.mp4").write_text("payload")
    route_a_download(live, 43)          # files into `other`
    status, body, _ = call(live, "POST", "/relocate",
                           {"fromRelPath": "Jane Doe/someone-elses.mp4",
                            "toDir": "john-smith", "downloadId": 43})
    assert status == 400
    assert "recorded route filed into" in body["detail"]
    assert (library / "Jane Doe" / "someone-elses.mp4").exists()


def test_relocate_refuses_a_file_older_than_its_routing_decision(live, library,
                                                                 store, clock):
    """The second, independent proof: a browser writes the file AFTER the
    decision, a torrent payload was already on disk."""
    stale = library / "other" / "old-payload.mp4"
    stale.write_text("payload")
    route_a_download(live, 44)
    # The route was logged at the fake clock's `now`; age the file past it.
    os.utime(stale, (clock.now - 86400, clock.now - 86400))
    status, body, _ = call(live, "POST", "/relocate",
                           {"fromRelPath": "other/old-payload.mp4",
                            "toDir": "Jane Doe", "downloadId": 44})
    assert status == 400
    assert "predates the routing decision" in body["detail"]
    assert stale.exists()


def test_a_second_correction_still_works_after_the_first_move(live, library):
    """The ledger follows the file, so `change` twice in a row is not refused
    the second time."""
    (library / "other" / "clip.mp4").write_text("payload")
    route_a_download(live, 45)
    _, first, _ = call(live, "POST", "/relocate",
                       {"fromRelPath": "other/clip.mp4", "toDir": "Jane Doe",
                        "downloadId": 45})
    assert first["moved"] is True
    status, second, _ = call(live, "POST", "/relocate",
                             {"fromRelPath": first["relPath"],
                              "toDir": "john-smith"})
    assert status == 200, second
    assert (library / "john-smith" / "clip.mp4").read_text() == "payload"


def test_relocate_records_provenance_only_after_it_is_proven(live, library,
                                                             store):
    (library / "Jane Doe" / "untouchable.mp4").write_text("x")
    before = store.routed_file_count()
    call(live, "POST", "/relocate",
         {"fromRelPath": "Jane Doe/untouchable.mp4", "toDir": "john-smith"})
    assert store.routed_file_count() == before, \
        "a refused relocate must not seed the ledger it just failed"


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


def test_dirs_snapshot_carries_the_library_root(live, library):
    """The extension needs it to prove a completed download landed INSIDE the
    library before asking for a relocate."""
    _, body, _ = call(live, "GET", "/dirs")
    assert body["root"] == str(library)


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


# --- /fetch honours the same allowlist as every other write path ------------ #
def test_fetch_refuses_a_directory_outside_the_index(live, library):
    """/fetch passed `known_dirs=None`, so it was the one write path with no
    allowlist: yt-dlp would create whatever directory was named."""
    status, body, _ = call(live, "POST", "/fetch",
                           {"url": "https://example-site.test/v/1",
                            "dir": "Brand New Subject"})
    assert status == 400
    assert body["error"] == "unsafe"
    assert not (library / "Brand New Subject").exists()


def test_fetch_refuses_an_unsafe_directory_name(live, library):
    for bad in ("../escape", "a/b", "..", "with:colon", "Not Indexed"):
        status, _, _ = call(live, "POST", "/fetch",
                            {"url": "https://example-site.test/v/1",
                             "dir": bad})
        assert status == 400, bad
    assert not (library.parent / "escape").exists()


def test_fetch_allows_the_catch_all_even_before_it_is_indexed(live, library):
    status, body, _ = call(live, "POST", "/fetch",
                           {"url": "https://example-site.test/v/1"})
    assert status == 200, body
    assert body["dir"] == "other"


def test_fetch_refuses_a_directory_that_symlinks_out_of_the_library(
        live, library, tmp_path, dir_index):
    """`is_safe_dir_name` proves the NAME is one component and says nothing
    about where it RESOLVES; yt-dlp follows the symlink with --paths."""
    outside = tmp_path / "outside"
    outside.mkdir()
    (library / "escape-link").symlink_to(outside, target_is_directory=True)
    dir_index.refresh(force=True)
    status, body, _ = call(live, "POST", "/fetch",
                           {"url": "https://example-site.test/v/1",
                            "dir": "escape-link"})
    assert status == 400, body
    assert body["error"] == "unsafe"


# --- a bad request must never 500 (or hang) -------------------------------- #
def test_a_non_ascii_authorization_header_is_a_401_not_a_crash(live):
    """`secrets.compare_digest` raises TypeError on a non-ASCII str, and
    _auth_ok runs inside _guard, OUTSIDE _run's error mapping -- so the
    connection was closed with no response and a traceback hit the journal."""
    status, body, _ = call(live, "GET", "/healthz", token=None,
                           headers={"Authorization": "Bearer \xe9\xe9\xe9"})
    assert status == 401
    assert body["error"] == "unauthorized"


def test_the_server_still_answers_after_a_hostile_auth_header(live):
    raw_request(live, ("GET /healthz HTTP/1.1\r\nHost: 127.0.0.1\r\n"
                       "Authorization: Bearer \u4f60\u597d\r\n"
                       "Connection: close\r\n\r\n").encode("utf-8"))
    status, body, _ = call(live, "GET", "/healthz")
    assert status == 200 and body["ok"] is True


@pytest.mark.parametrize("header", [
    "Bearer \xe9", "Bearer " + "\xff" * 100,
    "bearer lowercase", "Basic dXNlcjpwdw==", "Bearer", "",
])
def test_malformed_authorization_headers_all_yield_401(live, header):
    status, body, _ = call(live, "GET", "/healthz", token=None,
                           headers={"Authorization": header})
    assert status == 401, header
    assert body["error"] == "unauthorized"


def raw_request(base: str, raw: bytes) -> bytes:
    """Send bytes urllib refuses to encode. http.server decodes headers as
    latin-1, so UTF-8 in an Authorization header arrives as a non-ASCII str --
    which is exactly what made compare_digest raise."""
    host, port = base[len("http://"):].split(":")
    with socket.create_connection((host, int(port)), timeout=10) as sock:
        sock.sendall(raw)
        chunks = []
        while True:
            got = sock.recv(4096)
            if not got:
                break
            chunks.append(got)
            if b"}" in got:
                break
    return b"".join(chunks)


def test_a_utf8_authorization_header_gets_a_real_401_response(live):
    """Regression: this used to close the connection with NO response at all
    and print a traceback into the journal."""
    raw = ("GET /healthz HTTP/1.1\r\nHost: 127.0.0.1\r\n"
           "Authorization: Bearer \u4f60\u597d\r\n"
           "Connection: close\r\n\r\n").encode("utf-8")
    resp = raw_request(live, raw)
    assert resp, "the server closed the connection without responding"
    assert b"401" in resp.split(b"\r\n", 1)[0]
    assert b"unauthorized" in resp


# --- HTTP method surface --------------------------------------------------- #
def test_head_goes_through_the_guard_and_returns_no_body(live):
    req = urllib.request.Request(live + "/healthz", method="HEAD",
                                 headers={"Authorization": f"Bearer {TOKEN}"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        assert resp.status == 200
        assert resp.read() == b""
        assert resp.headers["Content-Type"] == "application/json"


def test_head_without_a_token_is_401(live):
    req = urllib.request.Request(live + "/healthz", method="HEAD")
    try:
        with urllib.request.urlopen(req, timeout=10):
            raise AssertionError("HEAD must not bypass the guard")
    except urllib.error.HTTPError as exc:
        assert exc.code == 401


@pytest.mark.parametrize("method", ["PUT", "DELETE", "PATCH", "OPTIONS"])
def test_other_methods_are_a_json_405_after_the_guard(live, method):
    status, body, headers = call(live, method, "/healthz")
    assert status == 405
    assert body["error"] == "method_not_allowed"
    assert headers["Allow"] == "GET, HEAD, POST"


@pytest.mark.parametrize("method", ["PUT", "DELETE", "PATCH", "OPTIONS"])
def test_other_methods_still_require_a_token(live, method):
    status, body, _ = call(live, method, "/healthz", token=None)
    assert status == 401


# --- a malformed config degrades instead of crash-looping ------------------ #
def test_a_malformed_config_serves_503_instead_of_exiting(tmp_path):
    """`Restart=always` + `RestartSec=10` + a fatal ConfigError was a silent
    6-restarts-per-minute loop with nothing listening."""
    bad = tmp_path / "config.toml"
    bad.write_text("library_root = \"/tmp\"\nthis is not = valid toml [[[",
                   encoding="utf-8")
    with pytest.raises(config_mod.ConfigError):
        config_mod.load(bad)

    cfg, err = config_mod.load_degraded(bad, env={})
    assert err and "cannot read" in err
    assert cfg.library_root is None

    app = S.App(cfg, store=Store(tmp_path / "s.sqlite3"), config_error=err)
    assert app.configured is False
    health = app.healthz()
    assert health["ok"] is True
    assert health["configured"] is False
    assert "configError" in health

    server = S.build_server("127.0.0.1", 0, app, TOKEN)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{port}"
        status, body, _ = call(base, "GET", "/healthz")
        assert status == 200 and "configError" in body
        for method, path, payload in [("GET", "/dirs", None),
                                      ("POST", "/match", {"filename": "x"}),
                                      ("POST", "/mkdir", {"name": "X"}),
                                      ("POST", "/relocate", {"toDir": "X"}),
                                      ("POST", "/fetch", {"url": "https://x.test/"})]:
            status, _, _ = call(base, method, path, payload)
            assert status == 503, f"{method} {path}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_a_valid_config_reports_no_error(cfg):
    app = S.App(cfg, store=Store(":memory:"))
    assert "configError" not in app.healthz()
