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
    MOVING_STATES, PathMap, QbtClient, QbtError, Response, TorrentIndex,
    derive_path_map, host_root_candidates, index_by_host_path,
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
        self.files = {}
        self.files_status = 200

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
        if "/torrents/files" in url:
            import json as _json
            import urllib.parse as _up
            qs = _up.parse_qs(_up.urlsplit(url).query)
            thash = (qs.get("hash") or [""])[0]
            if self.files_status != 200:
                return Response(self.files_status, b"")
            return Response(200, _json.dumps(self.files.get(thash, [])).encode())
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
    (tmp_path / "library" / "Jane Doe" / "clip2.mp4").write_text("x")
    torrents = [{"save_path": "/downloads/library/Jane Doe",
                 "name": "clip.mp4"},
                {"save_path": "/downloads/library/Jane Doe",
                 "name": "clip2.mp4"}]
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


# --- 6: setLocation returns before the move finishes ------------------------ #
class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def moving_client(states, *, clock=None, sleeps=None):
    """A client whose torrent walks through `states`, one per poll."""
    seq = list(states)

    class Walking(StubQbt):
        def __call__(self, method, url, data=None, headers=None):
            if url.endswith("/torrents/info"):
                import json
                state = seq.pop(0) if len(seq) > 1 else seq[0]
                return Response(200, json.dumps(
                    [{"hash": "AbC", "state": state}]).encode())
            return super().__call__(method, url, data, headers)

    return client(Walking())


def test_verify_seeding_waits_out_the_moving_state():
    """THE finding. setLocation returns as soon as the request is accepted, not
    when the payload has arrived; qBittorrent then sits in `moving`, which is
    in neither SEEDING_STATES nor BROKEN_STATES. verify_seeding returned False,
    the row raised ApplyError, and the backfill aborted with a torrent part-way
    through a relocation and nothing to roll back."""
    clock, sleeps = FakeClock(), []
    c = moving_client(["moving", "moving", "moving", "stalledUP"])
    ok = c.verify_seeding("abc", timeout_s=60, poll_s=1.0,
                          sleep=lambda s: (sleeps.append(s),
                                           setattr(clock, "now", clock.now + s)),
                          clock=clock)
    assert ok is True
    assert len(sleeps) == 3, "it must actually have waited"


def test_verify_seeding_gives_up_on_a_move_that_never_finishes():
    clock, sleeps = FakeClock(), []
    c = moving_client(["moving"])
    ok = c.verify_seeding("abc", timeout_s=5, poll_s=1.0,
                          sleep=lambda s: (sleeps.append(s),
                                           setattr(clock, "now", clock.now + s)),
                          clock=clock)
    assert ok is False, "bounded, not an infinite wait"
    assert len(sleeps) <= 6


def test_verify_seeding_still_fails_fast_on_a_broken_torrent():
    clock, sleeps = FakeClock(), []
    c = moving_client(["moving", "missingFiles"])
    ok = c.verify_seeding("abc", timeout_s=60, poll_s=1.0,
                          sleep=lambda s: (sleeps.append(s),
                                           setattr(clock, "now", clock.now + s)),
                          clock=clock)
    assert ok is False
    assert len(sleeps) == 1


def test_a_healthy_torrent_is_not_polled_at_all():
    sleeps = []
    stub = StubQbt(torrents=[{"hash": "AbC", "state": "uploading"}])
    assert client(stub).verify_seeding("abc",
                                       sleep=lambda s: sleeps.append(s)) is True
    assert sleeps == []


def test_moving_states_are_declared():
    assert "moving" in MOVING_STATES


# --- 5: per-file indexing --------------------------------------------------- #
def test_torrents_files_returns_the_file_list():
    stub = StubQbt(torrents=[{"hash": "H1"}])
    stub.files = {"H1": [{"name": "a.mp4"}, {"name": "b.mp4"}]}
    assert client(stub).torrents_files("H1") == [{"name": "a.mp4"},
                                                 {"name": "b.mp4"}]


def test_torrents_files_error_paths():
    stub = StubQbt(torrents=[{"hash": "H1"}])
    stub.files_status = 404
    with pytest.raises(QbtError, match="unknown hash"):
        client(stub).torrents_files("H1")
    stub.files_status = 500
    with pytest.raises(QbtError, match="HTTP 500"):
        client(stub).torrents_files("H1")


def test_index_includes_files_of_a_no_root_folder_torrent():
    """The population the backfill exists for: a multi-file torrent whose
    payload sits DIRECTLY at the library root. content_path names the save path
    itself, so the individual files were invisible."""
    pm = PathMap("/downloads", "/host/disk")
    torrents = [{"hash": "H1", "name": "Some Release",
                 "save_path": "/downloads", "content_path": "/downloads"}]
    without = index_by_host_path(torrents, pm)
    assert "/host/disk/part1.mp4" not in without
    assert without.complete is False

    with_files = index_by_host_path(
        torrents, pm,
        files_for=lambda h: [{"name": "part1.mp4"}, {"name": "part2.mp4"}])
    assert with_files["/host/disk/part1.mp4"]["hash"] == "H1"
    assert with_files["/host/disk/part2.mp4"]["hash"] == "H1"
    assert with_files.complete is True


def test_index_handles_a_nested_file_path():
    pm = PathMap("/downloads", "/host/disk")
    torrents = [{"hash": "H1", "name": "R", "save_path": "/downloads"}]
    idx = index_by_host_path(torrents, pm,
                             files_for=lambda h: [{"name": "R/inner/a.mkv"}])
    assert idx["/host/disk/R/inner/a.mkv"]["hash"] == "H1"


def test_a_failed_file_listing_marks_the_index_incomplete():
    pm = PathMap("/downloads", "/host/disk")
    torrents = [{"hash": "H1", "name": "R", "save_path": "/downloads"}]

    def boom(_h):
        raise RuntimeError("nope")

    idx = index_by_host_path(torrents, pm, files_for=boom)
    assert idx.complete is False
    assert idx.errors


def test_the_index_is_incomplete_without_a_path_map():
    idx = index_by_host_path([{"hash": "h"}], None)
    assert idx == {}
    assert idx.complete is False
    assert isinstance(idx, TorrentIndex)


# --- derive_path_map hardening ---------------------------------------------- #
def test_a_single_correlation_is_not_enough_to_fix_the_map(tmp_path):
    """One accidental correlation used to fix the mapping for the whole run,
    and the mapping is what decides which files are torrent-backed."""
    (tmp_path / "library" / "Jane Doe").mkdir(parents=True)
    (tmp_path / "library" / "Jane Doe" / "clip.mp4").write_text("x")
    torrents = [{"save_path": "/downloads/library/Jane Doe",
                 "name": "clip.mp4"}]
    roots = host_root_candidates(tmp_path / "library")
    assert derive_path_map(torrents, roots) is None
    # ...but an explicit min_votes=1 still allows it, for a deliberate caller.
    assert derive_path_map(torrents, roots, min_votes=1) is not None


def test_a_tie_between_two_mappings_yields_no_mapping(tmp_path):
    """Ambiguous evidence does not identify one mount point."""
    lib = tmp_path / "library"
    (lib / "a").mkdir(parents=True)
    (lib / "b").mkdir()
    (lib / "a" / "one.mp4").write_text("x")
    (lib / "b" / "two.mp4").write_text("x")
    torrents = [{"save_path": "/downloads/a", "name": "one.mp4"},
                {"save_path": "/elsewhere/b", "name": "two.mp4"}]
    assert derive_path_map(torrents, host_root_candidates(lib),
                           min_votes=1) is None


def test_a_map_that_cannot_express_the_library_root_is_refused(tmp_path):
    """Worse than no map: it would classify every loose root file as
    not-torrent-backed."""
    lib = tmp_path / "library"
    other = tmp_path / "elsewhere"
    (other / "Jane Doe").mkdir(parents=True)
    lib.mkdir()
    for name in ("a.mp4", "b.mp4"):
        (other / "Jane Doe" / name).write_text("x")
    torrents = [{"save_path": "/downloads/Jane Doe", "name": "a.mp4"},
                {"save_path": "/downloads/Jane Doe", "name": "b.mp4"}]
    roots = host_root_candidates(other)
    assert derive_path_map(torrents, roots) is not None
    assert derive_path_map(torrents, roots, library_root=lib) is None


def test_derive_path_map_looks_past_the_first_fifty_torrents(tmp_path):
    """It sampled `torrents[:50]` while claiming "the mapping most torrents
    agree on"."""
    lib = tmp_path / "library"
    (lib / "Jane Doe").mkdir(parents=True)
    for name in ("real1.mp4", "real2.mp4"):
        (lib / "Jane Doe" / name).write_text("x")
    noise = [{"save_path": "/downloads/nowhere", "name": f"ghost{i}.mp4"}
             for i in range(60)]
    torrents = noise + [
        {"save_path": "/downloads/Jane Doe", "name": "real1.mp4"},
        {"save_path": "/downloads/Jane Doe", "name": "real2.mp4"},
    ]
    assert derive_path_map(torrents, host_root_candidates(lib)) is not None
    # Pinned to the old behaviour, the real torrents are never reached.
    assert derive_path_map(torrents, host_root_candidates(lib),
                           sample=50) is None
