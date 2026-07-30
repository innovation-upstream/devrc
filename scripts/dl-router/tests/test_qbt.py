"""qBittorrent client: login, path-map derivation, setLocation, error paths.

Every test runs against an in-process stub transport. The live instance is
NEVER contacted — it is a production seeding target, and a stray setLocation
against it would move real payloads.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from qbt import (  # noqa: E402
    PathMap, QbtClient, QbtError, Response, derive_path_map,
    host_root_candidates, index_by_host_path,
)


class StubQbt:
    """Records calls and replays scripted responses."""

    def __init__(self, *, login_body=b"Ok.", login_status=200, torrents=None,
                 set_location_status=200):
        self.login_body = login_body
        self.login_status = login_status
        self.torrents = torrents if torrents is not None else []
        self.set_location_status = set_location_status
        self.calls = []
        self.torrents_status = 200

    def __call__(self, method, url, data=None, headers=None):
        self.calls.append((method, url, data, headers))
        if url.endswith("/auth/login"):
            return Response(self.login_status, self.login_body)
        if url.endswith("/torrents/info"):
            import json
            if self.torrents_status != 200:
                status, self.torrents_status = self.torrents_status, 200
                return Response(status, b"")
            return Response(200, json.dumps(self.torrents).encode())
        if url.endswith("/torrents/setLocation"):
            return Response(self.set_location_status, b"")
        return Response(404, b"")


def client(stub, **kw):
    return QbtClient("http://127.0.0.1:30880", "user", "pw", transport=stub, **kw)


# --- login ----------------------------------------------------------------- #
def test_login_succeeds_on_ok():
    stub = StubQbt()
    c = client(stub)
    c.login()
    assert c._logged_in is True
    method, url, data, headers = stub.calls[0]
    assert method == "POST"
    assert url.endswith("/api/v2/auth/login")
    assert data == {"username": "user", "password": "pw"}
    # qBittorrent's CSRF check needs a same-origin Referer.
    assert headers["Referer"] == "http://127.0.0.1:30880"


def test_login_failure_is_reported_clearly():
    with pytest.raises(QbtError, match="bad credentials"):
        client(StubQbt(login_body=b"Fails.")).login()


def test_login_403_is_distinguished():
    with pytest.raises(QbtError, match="403"):
        client(StubQbt(login_status=403)).login()


def test_login_happens_once_and_is_reused():
    stub = StubQbt(torrents=[])
    c = client(stub)
    c.torrents_info()
    c.torrents_info()
    logins = [x for x in stub.calls if x[1].endswith("/auth/login")]
    assert len(logins) == 1


def test_expired_session_triggers_exactly_one_relogin():
    stub = StubQbt(torrents=[])
    c = client(stub)
    c.login()
    stub.torrents_status = 403
    c.torrents_info()
    logins = [x for x in stub.calls if x[1].endswith("/auth/login")]
    assert len(logins) == 2


def test_transport_error_surfaces_as_qbterror():
    def boom(*a, **kw):
        raise QbtError("connection refused")

    with pytest.raises(QbtError):
        client(boom).torrents_info()


def test_non_list_torrents_info_is_rejected():
    class Weird(StubQbt):
        def __call__(self, method, url, data=None, headers=None):
            if url.endswith("/torrents/info"):
                return Response(200, b'{"not": "a list"}')
            return super().__call__(method, url, data, headers)

    with pytest.raises(QbtError, match="expected a list"):
        client(Weird()).torrents_info()


def test_non_json_response_is_rejected():
    class Garbage(StubQbt):
        def __call__(self, method, url, data=None, headers=None):
            if url.endswith("/torrents/info"):
                return Response(200, b"<html>nope</html>")
            return super().__call__(method, url, data, headers)

    with pytest.raises(QbtError, match="non-JSON"):
        client(Garbage()).torrents_info()


# --- setLocation ----------------------------------------------------------- #
def test_set_location_posts_hash_and_container_path():
    stub = StubQbt()
    c = client(stub)
    c.set_location("abc123", "/downloads/library/Jane Doe")
    call = [x for x in stub.calls if x[1].endswith("/torrents/setLocation")][0]
    assert call[2] == {"hashes": "abc123",
                       "location": "/downloads/library/Jane Doe"}


@pytest.mark.parametrize("status,message", [
    (400, "empty"), (403, "write access"), (409, "create the save path"),
    (500, "HTTP 500"),
])
def test_set_location_error_paths(status, message):
    with pytest.raises(QbtError, match=message):
        client(StubQbt(set_location_status=status)).set_location("h", "/x")


@pytest.mark.parametrize("state,ok", [
    ("uploading", True), ("stalledUP", True), ("pausedUP", True),
    ("forcedUP", True), ("error", False), ("missingFiles", False),
    ("downloading", False), ("unknown", False),
])
def test_verify_seeding_classifies_states(state, ok):
    stub = StubQbt(torrents=[{"hash": "AbC", "state": state}])
    assert client(stub).verify_seeding("abc") is ok


def test_verify_seeding_false_for_a_vanished_torrent():
    assert client(StubQbt(torrents=[])).verify_seeding("abc") is False


# --- path mapping ---------------------------------------------------------- #
def test_derive_path_map_correlates_container_and_host_paths(tmp_path):
    # Host layout: <tmp>/library/Jane Doe/clip.mp4
    # Container view: /downloads/library/Jane Doe
    (tmp_path / "library" / "Jane Doe").mkdir(parents=True)
    (tmp_path / "library" / "Jane Doe" / "clip.mp4").write_text("x")
    torrents = [{"save_path": "/downloads/library/Jane Doe",
                 "name": "clip.mp4"}]
    roots = host_root_candidates(tmp_path / "library")
    pm = derive_path_map(torrents, roots)
    assert pm is not None
    assert pm.to_host("/downloads/library/Jane Doe/clip.mp4").endswith(
        "library/Jane Doe/clip.mp4")
    assert pm.to_container(str(tmp_path / "library" / "Jane Doe")) \
        .startswith("/downloads")


def test_derive_path_map_prefers_the_mapping_most_torrents_agree_on(tmp_path):
    lib = tmp_path / "library"
    (lib / "Jane Doe").mkdir(parents=True)
    (lib / "Jane Doe" / "a.mp4").write_text("x")
    (lib / "john-smith").mkdir()
    (lib / "john-smith" / "b.mp4").write_text("x")
    torrents = [
        {"save_path": "/downloads/library/Jane Doe", "name": "a.mp4"},
        {"save_path": "/downloads/library/john-smith", "name": "b.mp4"},
    ]
    pm = derive_path_map(torrents, host_root_candidates(lib))
    assert pm.container_prefix == "/downloads"
    assert pm.host_prefix == str(tmp_path)


def test_derive_path_map_returns_none_when_nothing_correlates(tmp_path):
    torrents = [{"save_path": "/downloads/nowhere", "name": "ghost.mp4"}]
    assert derive_path_map(torrents, host_root_candidates(tmp_path)) is None


def test_derive_path_map_ignores_malformed_rows(tmp_path):
    torrents = [{"save_path": "", "name": ""},
                {"save_path": "relative/path", "name": "x"},
                {}]
    assert derive_path_map(torrents, host_root_candidates(tmp_path)) is None


def test_host_root_candidates_are_most_specific_first_and_exclude_root():
    roots = host_root_candidates("/a/b/c/library")
    assert roots[0] == "/a/b/c/library"
    assert roots[1] == "/a/b/c"
    assert "/" not in roots


def test_host_root_candidates_appends_configured_extras():
    roots = host_root_candidates("/a/b/library", extra=["/mnt/disk"])
    assert roots[-1] == "/mnt/disk"


# --- PathMap translation --------------------------------------------------- #
def test_path_map_round_trips():
    pm = PathMap("/downloads", "/host/disk")
    assert pm.to_host("/downloads/library/x.mp4") == "/host/disk/library/x.mp4"
    assert pm.to_container("/host/disk/library/x.mp4") == \
        "/downloads/library/x.mp4"


def test_path_map_returns_none_outside_its_prefix():
    pm = PathMap("/downloads", "/host/disk")
    assert pm.to_host("/elsewhere/x") is None
    assert pm.to_container("/other/x") is None


def test_path_map_handles_the_prefix_itself():
    pm = PathMap("/downloads", "/host/disk")
    assert pm.to_host("/downloads") == "/host/disk"
    assert pm.to_container("/host/disk") == "/downloads"


def test_path_map_does_not_match_a_sibling_with_a_shared_prefix():
    pm = PathMap("/downloads", "/host/disk")
    assert pm.to_host("/downloads-other/x") is None


# --- host-path index ------------------------------------------------------- #
def test_index_by_host_path_maps_content_and_save_paths():
    pm = PathMap("/downloads", "/host/disk")
    torrents = [{"hash": "h1", "name": "clip.mp4",
                 "save_path": "/downloads/library",
                 "content_path": "/downloads/library/clip.mp4"}]
    idx = index_by_host_path(torrents, pm)
    assert idx["/host/disk/library/clip.mp4"]["hash"] == "h1"


def test_index_by_host_path_is_empty_without_a_map():
    assert index_by_host_path([{"hash": "h"}], None) == {}
