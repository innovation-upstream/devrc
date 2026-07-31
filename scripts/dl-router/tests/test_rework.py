"""Regressions introduced by the audit-remediation round and fixed in it.

Grouped here rather than scattered so the delta is reviewable as a delta. Each
one names the fix round's own mistake, not the original finding.
"""
from __future__ import annotations

import os
import sqlite3
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import backfill as bf  # noqa: E402
import config as config_mod  # noqa: E402
import qbt as qbt_mod  # noqa: E402
import server as S  # noqa: E402
from conftest import SAMPLE_DIRS  # noqa: E402
from dirindex import DirIndex, FileIndex  # noqa: E402
from qbt import PathMap  # noqa: E402
from store import SCHEMA_VERSION, Store  # noqa: E402


class FakeQbt:
    def __init__(self, *, torrents=None, files=None, fail_hashes=()):
        self.torrents = list(torrents or [])
        self.files = dict(files or {})
        self.fail_hashes = set(fail_hashes)
        self.moves = []

    def torrents_info(self, hashes=None):
        return list(self.torrents)

    def torrents_files(self, torrent_hash):
        if torrent_hash in self.fail_hashes:
            raise RuntimeError("transient HTTP error")
        return list(self.files.get(torrent_hash, []))

    def set_location(self, torrent_hash, location):
        self.moves.append((torrent_hash, location))

    def verify_seeding(self, torrent_hash, **kw):
        return True


# --- NR5: one listing failure must not veto the whole run ------------------- #
def test_one_failed_file_listing_does_not_collapse_the_manifest(library, store):
    """`complete` was a single global flag: the first `torrents/files` error
    set it False forever and every `fs` row became SKIP. With ~1000 torrents,
    one transient HTTP error produced an empty manifest."""
    (library / "Jane Doe.mp4").write_text("x")
    pm = PathMap("/downloads", str(library))
    torrents = [
        # Unreadable, but its payload lives under a DIFFERENT subtree.
        {"hash": "BAD", "name": "Elsewhere",
         "save_path": "/downloads/john-smith"},
        {"hash": "OK", "name": "Fine", "save_path": "/downloads/Mary_Major"},
    ]

    def files_for(h):
        if h == "BAD":
            raise RuntimeError("transient HTTP error")
        return [{"name": "fine.mkv"}]

    p = bf.plan(library, store=store, dir_names=SAMPLE_DIRS, torrents=torrents,
                path_map=pm, files_for=files_for)
    row = p.rows[0]
    assert row.move == bf.MOVE_FS, (
        "a file outside the unreadable torrent's save path is still proven")
    assert row.action == bf.ACTION_FS
    assert any("unavailable" in n for n in p.notes)


def test_a_file_UNDER_the_unreadable_torrent_is_still_skipped(library, store):
    """The proof is per subtree, not per run: inside the unknown save path,
    absence still proves nothing."""
    (library / "Jane Doe.mp4").write_text("x")
    pm = PathMap("/downloads", str(library))
    # The unreadable torrent's save path IS the library root.
    torrents = [{"hash": "BAD", "name": "Multi", "save_path": "/downloads"}]

    def files_for(h):
        raise RuntimeError("transient HTTP error")

    row = bf.plan(library, store=store, dir_names=SAMPLE_DIRS,
                  torrents=torrents, path_map=pm, files_for=files_for).rows[0]
    assert row.move == bf.MOVE_UNKNOWN
    assert row.action == bf.ACTION_SKIP


def test_torrent_index_proves_absent_per_subtree():
    pm = PathMap("/downloads", "/host/disk")
    torrents = [{"hash": "BAD", "name": "x", "save_path": "/downloads/sub"}]

    def boom(_h):
        raise RuntimeError("nope")

    idx = qbt_mod.index_by_host_path(torrents, pm, files_for=boom)
    assert idx.complete is False
    assert idx.proves_absent("/host/disk/sub/anything.mkv") is False
    assert idx.proves_absent("/host/disk/other/anything.mkv") is True


def test_a_torrent_with_no_save_path_poisons_everything():
    """It cannot even be bounded, so nothing anywhere is provable. Fail closed."""
    pm = PathMap("/downloads", "/host/disk")
    idx = qbt_mod.index_by_host_path([{"hash": "H"}], pm,
                                     files_for=lambda h: [])
    assert idx.proves_absent("/host/disk/anything.mkv") is False


# --- NR6: --dry-run is the review step, not a privileged operation ---------- #
def test_dry_run_does_not_require_qbittorrent_credentials(library, store):
    """`apply --dry-run` is the step the docs tell you to run FIRST. Requiring
    a client for a preview that moves nothing blocked the review gate."""
    (library / "Jane Doe.mp4").write_text("payload")
    p = bf.plan(library, store=store, dir_names=SAMPLE_DIRS, torrents=[])
    assert p.rows[0].action == bf.ACTION_FS
    results = bf.apply(p, dry_run=True)
    assert results["failed"] is None
    assert (library / "Jane Doe.mp4").exists()
    assert any(op.startswith("rename ") for op in results["ops"])
    assert any("DRY RUN WITHOUT qBittorrent" in op for op in results["ops"]), \
        "the preview must say it was not re-validated"


def test_a_REAL_apply_still_requires_credentials(library, store):
    (library / "Jane Doe.mp4").write_text("payload")
    p = bf.plan(library, store=store, dir_names=SAMPLE_DIRS, torrents=[])
    with pytest.raises(bf.ApplyError, match="re-validate"):
        bf.apply(p, dry_run=False)


def test_dry_run_with_a_client_still_revalidates(library, store):
    (library / "Jane Doe.mp4").write_text("payload")
    p = bf.plan(library, store=store, dir_names=SAMPLE_DIRS, torrents=[])
    results = bf.apply(p, client=FakeQbt(torrents=[]), dry_run=True)
    assert any("revalidated against qBittorrent" in op
               for op in results["ops"])


# --- NR7: one invariant for what a routing target is ------------------------ #
def test_an_escaping_symlink_is_not_advertised_as_a_routing_target(library,
                                                                   tmp_path):
    """DirIndex advertised it while safe_rel_path (used by /mkdir, /relocate
    and the fetcher) refused it -- so browser downloads could write THROUGH it,
    out of the library, while yt-dlp could not."""
    outside = tmp_path / "outside"
    outside.mkdir()
    (library / "escape-link").symlink_to(outside, target_is_directory=True)
    idx = DirIndex(library)
    assert "escape-link" not in idx.name_set()
    assert any("escape-link" in e for e in idx.errors())


def test_an_internal_symlink_is_still_a_routing_target(library):
    (library / "real").mkdir()
    (library / "alias-link").symlink_to(library / "real",
                                        target_is_directory=True)
    assert "alias-link" in DirIndex(library).name_set()


def test_the_fetcher_and_the_index_now_agree(library, tmp_path, cfg):
    """The point of the invariant: both halves answer the same way about the
    same directory."""
    import fetcher as fetcher_mod
    from safety import UnsafeName
    outside = tmp_path / "outside"
    outside.mkdir()
    (library / "escape-link").symlink_to(outside, target_is_directory=True)
    idx = DirIndex(library)
    f = fetcher_mod.Fetcher(library, runner=lambda *a, **k: None)
    assert "escape-link" not in idx.name_set()
    with pytest.raises(UnsafeName):
        f.submit("https://example-site.test/v", "escape-link")


# --- NR8: the etag must match the entries it was computed from -------------- #
def test_snapshot_reads_entries_and_etag_under_one_lock(library):
    """Reading `_etag` outside the lock let a concurrent refresh pair
    refresh-N's entries with refresh-N+1's etag. server.py serves that as the
    HTTP ETag, so the extension caches a stale list under a fresh etag and its
    If-None-Match never refetches it."""
    idx = DirIndex(library, ttl=0.0)
    seen = []
    errors = []

    def churn():
        try:
            for i in range(60):
                (library / f"churn-{i}").mkdir(exist_ok=True)
                idx.refresh(force=True)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    def snap():
        try:
            for _ in range(200):
                s = idx.snapshot()
                seen.append((s["etag"], tuple(d["name"] for d in s["dirs"])))
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=churn), threading.Thread(target=snap)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert not errors, errors

    # Every etag must correspond to exactly one directory listing.
    by_etag: dict = {}
    for etag, names in seen:
        by_etag.setdefault(etag, set()).add(names)
    bad = {e: v for e, v in by_etag.items() if len(v) > 1}
    assert not bad, f"{len(bad)} etag(s) served with two different listings"


def test_the_file_index_does_not_hold_its_lock_across_the_scan(library):
    """Holding it serialised every request thread behind a whole-tree walk and
    would push /match past its 400 ms budget -- straight into the timeout that
    made the correction paths disagree in the first place."""
    for i in range(30):
        (library / f"f{i}.mp4").write_text("x")

    idx = FileIndex(library, ttl=0.0)
    idx.refresh()

    slow = threading.Event()
    real_scan = idx._scan

    def slow_scan():
        slow.set()
        time.sleep(0.5)
        return real_scan()

    idx._scan = slow_scan
    scanner = threading.Thread(target=lambda: idx.refresh(force=True))
    scanner.start()
    assert slow.wait(timeout=5), "the scan did not start"

    started = time.monotonic()
    idx.by_name_key("anything")           # must not block behind the scan
    elapsed = time.monotonic() - started
    scanner.join(timeout=10)
    assert elapsed < 0.3, (
        f"a reader waited {elapsed:.2f}s behind a whole-tree scan")


# --- NR9: a half-applied migration must not become a restart loop ----------- #
def test_migration_2_is_re_runnable_after_a_crash(tmp_path):
    """sqlite3 autocommits DDL, so a crash between the ALTER and the
    `PRAGMA user_version` bump left the column present with the version at 1 --
    and every subsequent open raised `duplicate column name`. server.py builds
    the Store unguarded, so that is a `Restart=always` loop."""
    db = tmp_path / "dl-router.sqlite3"
    Store(db).close()

    # Simulate the crash: keep the new column, roll the version back.
    conn = sqlite3.connect(str(db))
    conn.execute("PRAGMA user_version=1")
    conn.commit()
    conn.close()

    store = Store(db)                     # must not raise
    assert store.version() == SCHEMA_VERSION
    store.log_route(dir_name="other", download_id="7")
    assert store.route_for_download("7")["dir"] == "other"
    store.close()


def test_migration_is_idempotent_when_run_twice(tmp_path):
    db = tmp_path / "dl-router.sqlite3"
    a = Store(db)
    assert a.migrate() == SCHEMA_VERSION
    assert a.migrate() == SCHEMA_VERSION
    a.close()
    b = Store(db)
    assert b.version() == SCHEMA_VERSION
    b.close()


def test_a_broken_store_degrades_instead_of_crash_looping(tmp_path, library,
                                                          monkeypatch, capsys):
    """The App constructor opens the store and runs migrations; anything it
    raises used to go straight out of main() into the restart loop that
    load_degraded() exists to prevent."""
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(f'library_root = "{library}"\n', encoding="utf-8")
    state = tmp_path / "state"
    state.mkdir()
    # A directory where the SQLite file should be: open() fails hard.
    (state / "dl-router.sqlite3").mkdir()
    monkeypatch.setenv("DL_ROUTER_CONFIG", str(cfg_path))
    monkeypatch.setenv("DL_ROUTER_STATE_DIR", str(state))
    monkeypatch.setenv("DL_ROUTER_TOKEN_FILE", str(tmp_path / "token"))
    monkeypatch.setenv("DL_ROUTER_HOST", "127.0.0.1")
    monkeypatch.setenv("DL_ROUTER_PORT", str(_free_port()))

    captured = {}
    real_build = S.build_server

    def spy_build(host, port, app, token):
        captured["app"] = app
        server = real_build(host, port, app, token)
        monkeypatch.setattr(server, "serve_forever",
                            lambda *a, **kw: (_ for _ in ()).throw(
                                KeyboardInterrupt))
        return server

    monkeypatch.setattr(S, "build_server", spy_build)
    assert S.main([]) == 0, "it must serve, not crash"
    assert captured["app"].configured is False
    assert captured["app"].config_error
    assert "dl-router startup_error" in capsys.readouterr().out


def _free_port() -> int:
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# --- config: `0` means one thing ------------------------------------------- #
def test_a_configured_port_of_zero_is_never_silently_the_default():
    """`_validate` rejects 0 while `Config.port` turned it INTO the default via
    `or`. On the degraded path (which skips validation) `DL_ROUTER_PORT=0`
    therefore bound the live sidecar's port."""
    cfg = config_mod.Config({"port": 0})
    assert cfg.port == 0, "0 must stay 0, not become 8791"
    with pytest.raises(config_mod.ConfigError):
        config_mod._validate({"auto_threshold": 0.75, "tie_margin": 0.05,
                              "port": 0})


def test_load_degraded_refuses_an_out_of_range_env_port(tmp_path):
    bad = tmp_path / "config.toml"
    bad.write_text("[[[", encoding="utf-8")
    for value in ("0", "70000", "-1", "nonsense"):
        cfg, err = config_mod.load_degraded(bad, env={"DL_ROUTER_PORT": value})
        assert err
        assert cfg.port == config_mod.DEFAULT_PORT, value


def test_load_degraded_honours_the_library_root_from_the_environment(tmp_path,
                                                                     library):
    """A host configured entirely through the unit's environment must keep
    working when config.toml is unreadable."""
    bad = tmp_path / "config.toml"
    bad.write_text("[[[", encoding="utf-8")
    cfg, err = config_mod.load_degraded(
        bad, env={"DL_ROUTER_LIBRARY_ROOT": str(library)})
    assert err
    assert cfg.library_root == library


# --- green: verify_seeding must not re-serialise the whole torrent list ----- #
def test_verify_seeding_scopes_its_poll_to_one_hash():
    """It polls once a second for up to five minutes; with ~1000 torrents an
    unscoped call re-serialises the entire list every time for one field."""
    seen = []

    def transport(method, url, data=None, headers=None):
        seen.append(url)
        if url.endswith("/auth/login"):
            return qbt_mod.Response(200, b"Ok.")
        return qbt_mod.Response(
            200, b'[{"hash":"AbC","state":"stalledUP"}]')

    client = qbt_mod.QbtClient("http://127.0.0.1:30880", "u", "p",
                               transport=transport)
    assert client.verify_seeding("abc") is True
    info = [u for u in seen if "/torrents/info" in u]
    assert info, "no info call was made"
    assert all("hashes=abc" in u for u in info), info
