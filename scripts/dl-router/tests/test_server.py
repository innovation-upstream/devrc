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


def test_learn_records_the_example_and_prior_but_no_tag_alias_for_a_performer(
        live, store):
    """The mislearning fix, at the endpoint.

    This used to assert the OPPOSITE: that a page tag became a site alias for
    whichever directory was chosen. On real traffic that turned a forum section
    name and two other posters' usernames into aliases for a subject directory
    (one of them global), because a tag list is not evidence about a PERSON.
    The example and the host prior are still recorded -- they are the parts
    that were never the problem.
    """
    status, body, _ = call(live, "POST", "/learn", {
        "context": {"page": {"url": "https://example-site.test/v",
                             "tags": ["Aster Nightingale"]}},
        "chosenDir": "Jane Doe",
        "autoDir": "other",
        "confirmed": True,
    })
    assert status == 200 and body["ok"] is True
    assert store.alias("asternightingale", "example-site.test") is None
    assert store.alias("asternightingale", "") is None
    assert body["aliases"] == 0
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


# --- the correction paths the picker exists to serve ----------------------- #
#
# The guard originally required the file to be sitting in the directory the
# /match decision NAMED. That is wrong for every case the correction flow
# exists for, because route_core deliberately files a non-auto answer into the
# CATCH-ALL while /match logs the CANDIDATE. Net effect: auto-file worked and
# every correction was refused, which kills D3's learning loop.

def test_a_below_threshold_download_filed_into_the_catch_all_CAN_be_corrected(
        live, library):
    """THE regression. A tie files into `other/` while /match logged
    'Jane Doe'; the undo must still work."""
    got = route_a_download(live, 50, tags=["Jane Doe", "john-smith"])
    assert got["auto"] is False, "precondition: this is the picker path"
    assert got["dir"] != "other", "precondition: the candidate is a subject dir"
    (library / "other" / "clip.mp4").write_text("payload")
    status, body, _ = call(live, "POST", "/relocate",
                           {"fromRelPath": "other/clip.mp4",
                            "toDir": "Jane Doe", "downloadId": 50})
    assert status == 200, body
    assert (library / "Jane Doe" / "clip.mp4").read_text() == "payload"


def test_a_confident_match_can_still_be_corrected_to_a_different_dir(live,
                                                                     library):
    got = route_a_download(live, 51, tags=["Jane Doe"])
    assert got["auto"] is True
    (library / "Jane Doe" / "clip.mp4").write_text("payload")
    status, _, _ = call(live, "POST", "/relocate",
                        {"fromRelPath": "Jane Doe/clip.mp4",
                         "toDir": "john-smith", "downloadId": 51})
    assert status == 200
    assert (library / "john-smith" / "clip.mp4").exists()


def test_the_timeout_path_can_be_corrected(live, library):
    """The extension answered from its cached local decision because the
    sidecar missed the 400 ms budget, so the file is wherever the CACHE said --
    which is not what the server logged."""
    route_a_download(live, 52, tags=["Jane Doe"])
    (library / "john-smith" / "clip.mp4").write_text("payload")
    status, body, _ = call(live, "POST", "/relocate",
                           {"fromRelPath": "john-smith/clip.mp4",
                            "toDir": "Mary_Major", "downloadId": 52})
    assert status == 200, body


def test_a_uniquified_name_still_matches_its_routing_decision(live, library):
    """conflictAction: "uniquify" means the file on disk is `clip (1).mp4`
    while the decision recorded `clip.mp4`."""
    route_a_download(live, 53, filename="clip.mp4")
    (library / "other" / "clip (1).mp4").write_text("payload")
    status, body, _ = call(live, "POST", "/relocate",
                           {"fromRelPath": "other/clip (1).mp4",
                            "toDir": "Jane Doe", "downloadId": 53})
    assert status == 200, body


def test_a_download_whose_routing_record_was_LOST_is_refused(live, library):
    """FAIL CLOSED. There is no fallback here on purpose.

    A "the extension says this download is recent and named X" fallback was
    tried and removed: with no routing decision to check it against there is
    nothing to verify the claim WITH, so it reduces to trusting the caller --
    on the one code path whose entire purpose is to refuse a move it cannot
    prove. A live payload written in the last hour with a matching name is
    exactly the shape that would get through.

    The cost of refusing is small and recoverable; the cost of the alternative
    is a broken seed."""
    (library / "other" / "late.mp4").write_text("payload")
    # `downloadFilename` is sent DELIBERATELY even though the field was
    # removed: the point is that re-adding a caller-supplied name must not
    # revive the fallback.
    status, body, _ = call(live, "POST", "/relocate",
                           {"fromRelPath": "other/late.mp4",
                            "toDir": "Jane Doe", "downloadId": 54,
                            "downloadFilename": "late.mp4"})
    assert status == 400
    assert (library / "other" / "late.mp4").exists()
    assert not (library / "Jane Doe" / "late.mp4").exists()


def test_that_refusal_says_the_record_was_lost_so_it_is_diagnosable(live,
                                                                    library):
    """"Refused" with no reason is a mystery bug report. Name the cause."""
    (library / "other" / "late.mp4").write_text("payload")
    _, body, _ = call(live, "POST", "/relocate",
                      {"fromRelPath": "other/late.mp4",
                       "toDir": "Jane Doe", "downloadId": 55})
    detail = body["detail"]
    assert "no routing decision is on record" in detail
    # The real causes -- NOT "the sidecar restarted", which an earlier draft
    # claimed: the route log is persistent SQLite and log_route commits, so a
    # restart loses nothing. A confident wrong diagnosis is worse than none.
    assert "unreachable when the download started" in detail
    assert "by hand" in detail, "the way out must be named"
    assert "restart" not in detail.lower(), \
        "do not blame a cause that cannot produce this state"


def test_an_extension_supplied_filename_cannot_substitute_for_a_record(live,
                                                                       library):
    """Nothing the caller asserts may create provenance out of nothing."""
    recent = library / "Jane Doe" / "seeding-payload.mkv"
    recent.write_text("a payload written moments ago")
    status, _, _ = call(live, "POST", "/relocate",
                        {"fromRelPath": "Jane Doe/seeding-payload.mkv",
                         "toDir": "john-smith", "downloadId": 56,
                         "downloadFilename": "seeding-payload.mkv"})
    assert status == 400
    assert recent.exists()


# --- what the guard actually proves ---------------------------------------- #
def test_relocate_refuses_a_file_the_router_never_routed(live, library):
    """A pre-existing file at a perfectly valid relative path -- exactly what a
    loose torrent payload under a subject directory looks like."""
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
    assert (library / "Jane Doe" / "payload.mp4").exists()


def test_a_routing_decision_does_not_authorise_moving_a_DIFFERENT_file(
        live, library):
    """The identity half of the proof.

    The directory check it replaced let ANY file in the named directory
    through: one /match for `innocent.mp4` authorised moving a live payload
    that merely shared the folder. Reproduced before this fix."""
    route_a_download(live, 60, tags=["Jane Doe"], filename="innocent.mp4")
    victim = library / "Jane Doe" / "seeding-payload.mkv"
    victim.write_text("a live torrent payload, written AFTER the match")
    status, body, _ = call(live, "POST", "/relocate",
                           {"fromRelPath": "Jane Doe/seeding-payload.mkv",
                            "toDir": "john-smith", "downloadId": 60})
    assert status == 400, "a match for one file must not authorise another"
    assert "this download was" in body["detail"]
    assert victim.exists()
    assert not (library / "john-smith" / "seeding-payload.mkv").exists()


def test_a_near_miss_filename_is_not_close_enough(live, library):
    route_a_download(live, 61, filename="clip.mp4")
    (library / "other" / "clip2.mp4").write_text("x")
    status, _, _ = call(live, "POST", "/relocate",
                        {"fromRelPath": "other/clip2.mp4",
                         "toDir": "Jane Doe", "downloadId": 61})
    assert status == 400


def test_relocate_refuses_a_file_older_than_its_routing_decision(live, library,
                                                                 store, clock):
    """The AGE half of the proof: a browser writes the file AFTER the decision,
    a torrent payload was already on disk. Name alone is not enough -- a
    payload could legitimately share a name with something downloaded later."""
    stale = library / "other" / "clip.mp4"
    stale.write_text("payload")
    route_a_download(live, 44, filename="clip.mp4")
    # The route was logged at the fake clock's `now`; age the file past it.
    os.utime(stale, (clock.now - 86400, clock.now - 86400))
    status, body, _ = call(live, "POST", "/relocate",
                           {"fromRelPath": "other/clip.mp4",
                            "toDir": "Jane Doe", "downloadId": 44})
    assert status == 400
    assert "predates" in body["detail"]
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


# --- per-directory counts, and the ETag decision --------------------------- #
#
# The picker shows how many files each subject directory holds. The counts are
# served in the /dirs snapshot but are DELIBERATELY NOT part of its ETag, and
# the tests below are what hold that line in both directions: one fails if the
# counts disappear, the others fail if they are ever folded into the hash.
def test_dirs_snapshot_carries_per_directory_counts(live, app, library):
    (library / "Jane Doe" / "one.mp4").write_text("x")
    (library / "Jane Doe" / "two.mp4").write_text("x")
    (library / "john-smith" / "solo.mp4").write_text("x")
    app.files.refresh(force=True)
    _, body, _ = call(live, "GET", "/dirs")
    assert body["counts"] == {"Jane Doe": 2, "john-smith": 1}


def test_counts_are_reported_only_for_indexed_directories(app, library):
    (library / "Jane Doe" / "one.mp4").write_text("x")
    hidden = library / ".stash"
    hidden.mkdir()
    (hidden / "x.mp4").write_text("x")
    app.files.refresh(force=True)
    counts = app.dirs_snapshot()["counts"]
    assert counts == {"Jane Doe": 1}


def test_THE_ETAG_IGNORES_THE_COUNTS(app, library):
    """THE pin on the ETag decision. Delete the deliberate exclusion in
    App.dirs_snapshot -- fold `counts` back in above the hash -- and this fails.

    A count changes on every completed download, and FileIndex is TTL-cached,
    so an ETag covering the counts would change when the routing configuration
    had not: it would stop being a cache validator for the thing the extension's
    cached fallback matcher actually runs on.
    """
    (library / "Jane Doe" / "one.mp4").write_text("x")
    app.files.refresh(force=True)
    first = app.dirs_snapshot()
    (library / "Jane Doe" / "two.mp4").write_text("x")
    app.files.refresh(force=True)
    second = app.dirs_snapshot()
    assert second["counts"] != first["counts"], "the counts really did change"
    assert second["etag"] == first["etag"], "but the routing payload did not"


def test_dirs_still_304s_after_a_download_changed_the_counts(live, app, library):
    """The same pin, end to end over HTTP -- the shape the extension sees."""
    (library / "Jane Doe" / "one.mp4").write_text("x")
    app.files.refresh(force=True)
    _, body, headers = call(live, "GET", "/dirs")
    assert body["counts"]["Jane Doe"] == 1
    etag = headers["ETag"]
    (library / "Jane Doe" / "two.mp4").write_text("x")
    app.files.refresh(force=True)
    status, body2, _ = call(live, "GET", "/dirs",
                            headers={"If-None-Match": etag})
    assert status == 304
    assert body2 is None


def test_the_etag_still_changes_when_the_ROUTING_payload_does(app, library):
    """The other direction: excluding the counts must not have excluded
    anything else. A new directory is a routing change and must invalidate."""
    app.files.refresh(force=True)
    first = app.dirs_snapshot()["etag"]
    (library / "New Subject").mkdir()
    app.dirs.refresh(force=True)
    assert app.dirs_snapshot()["etag"] != first


def test_serving_dirs_NEVER_walks_the_library(live, app, library,
                                              monkeypatch):
    """/dirs is the picker's own path and has a 4 s budget on the extension
    side. It must read whatever tally the dedupe walk already produced and
    never start one of its own -- a whole-tree walk here would turn a
    decorative counter into a picker that fails to load on a big library.
    """
    (library / "Jane Doe" / "one.mp4").write_text("x")
    calls = []
    real_scan = app.files._scan
    monkeypatch.setattr(app.files, "_scan",
                        lambda: (calls.append(1), real_scan())[1])
    status, body, _ = call(live, "GET", "/dirs")
    assert status == 200
    assert calls == [], "/dirs must not scan the library"
    assert body["counts"] == {}, "and it reports no counts rather than blocking"


def test_a_match_is_what_warms_the_counts(app, library):
    """The intended degradation, made explicit: counts are empty until the
    dedupe index has been walked, and /match walks it on every download -- so
    by the time any picker opens they are there."""
    (library / "Jane Doe" / "one.mp4").write_text("x")
    assert app.dirs_snapshot()["counts"] == {}
    app.match({"url": "https://example-site.test/dl/1", "filename": "clip.mp4",
               "page": {"url": "https://example-site.test/v/1",
                        "tags": ["Jane Doe"]}})
    assert app.dirs_snapshot()["counts"] == {"Jane Doe": 1}


def test_the_dirs_entries_are_untouched_by_the_counts(app, library):
    """The counts are a SEPARATE map, not a field on each `dirs` entry: the
    extension's cached fallback matcher iterates those entries, and they stay
    exactly what they were before counts existed."""
    (library / "Jane Doe" / "one.mp4").write_text("x")
    app.files.refresh(force=True)
    snap = app.dirs_snapshot()
    for entry in snap["dirs"]:
        assert set(entry) == {"name", "key", "tokens", "kind"}
