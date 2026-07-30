"""Backfill: plan classification, apply on a temp tree with a fake qBittorrent,
abort-on-failure, and the refusal to run without a manifest.

`apply` is NEVER pointed at a real library here — every test builds its own
temp tree and a stub client.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import backfill as bf  # noqa: E402
from conftest import SAMPLE_DIRS  # noqa: E402
from qbt import PathMap  # noqa: E402


class FakeQbt:
    """Records setLocation calls; seeding verification is scriptable."""

    def __init__(self, *, seeding=True, fail_on=None):
        self.moves = []
        self.seeding = seeding
        self.fail_on = fail_on

    def set_location(self, torrent_hash, location):
        if self.fail_on and torrent_hash == self.fail_on:
            raise RuntimeError("setLocation refused")
        self.moves.append((torrent_hash, location))

    def verify_seeding(self, torrent_hash):
        return self.seeding


def plan_for(library, store, **kw):
    # `torrents=[]` means "qBittorrent state KNOWN, no torrents". Passing None
    # (the module default) means "unknown", which makes every row SKIP.
    kw.setdefault("torrents", [])
    return bf.plan(library, store=store, dir_names=SAMPLE_DIRS, **kw)


# --- loose-file discovery -------------------------------------------------- #
def test_loose_root_files_ignores_directories_and_dotfiles(library):
    (library / "loose.mp4").write_text("x")
    (library / ".hidden").write_text("x")
    assert bf.loose_root_files(library) == ["loose.mp4"]


def test_loose_root_files_on_a_missing_root(tmp_path):
    assert bf.loose_root_files(tmp_path / "nope") == []


# --- alias seeding --------------------------------------------------------- #
def test_seed_aliases_from_directory_names(store):
    n = bf.seed_aliases(store, SAMPLE_DIRS)
    assert n == len(SAMPLE_DIRS)
    assert store.alias("janedoe") == "Jane Doe"
    assert store.alias("johnsmith") == "john-smith"


def test_seed_aliases_is_idempotent(store):
    bf.seed_aliases(store, SAMPLE_DIRS)
    assert bf.seed_aliases(store, SAMPLE_DIRS) == 0


def test_seed_aliases_from_torrent_names(store, library):
    pm = PathMap("/downloads", str(library))
    torrents = [{"name": "Jane Doe - Clip 01", "save_path": "/downloads/Jane Doe"}]
    bf.seed_aliases(store, SAMPLE_DIRS, torrents, pm, library)
    assert store.alias("janedoeclip01") == "Jane Doe"


def test_seed_aliases_ignores_torrents_outside_the_top_level_dirs(store, library):
    pm = PathMap("/downloads", str(library))
    torrents = [{"name": "Elsewhere", "save_path": "/downloads/Jane Doe/deep"}]
    before = store.alias_count()
    bf.seed_aliases(store, SAMPLE_DIRS, torrents, pm, library)
    assert store.alias("elsewhere") is None
    assert store.alias_count() == before + len(SAMPLE_DIRS)


# --- plan classification --------------------------------------------------- #
def test_plan_skips_an_opaque_filename(library, store):
    (library / "0hv9783sdgne5ur3xh53n_source.mp4").write_text("x")
    rows = plan_for(library, store).rows
    assert [r.action for r in rows] == [bf.ACTION_SKIP]
    assert rows[0].proposed_dir == ""


def test_plan_classifies_a_confident_non_torrent_file_as_fs(library, store):
    (library / "Jane Doe.mp4").write_text("x")
    row = plan_for(library, store).rows[0]
    assert row.action == bf.ACTION_FS
    assert row.proposed_dir == "Jane Doe"


def test_plan_skips_a_partial_filename_match(library, store):
    """Conservative by design: a name that only partly covers a directory stays
    below the threshold and lands on SKIP rather than guessing."""
    (library / "Jane Doe clip 01.mp4").write_text("x")
    row = plan_for(library, store).rows[0]
    assert row.action == bf.ACTION_SKIP
    assert row.confidence < 0.75


def test_plan_classifies_a_torrent_backed_file_as_qbt(library, store):
    (library / "Jane Doe.mp4").write_text("x")
    pm = PathMap("/downloads", str(library))
    torrents = [{"hash": "H1", "name": "Jane Doe.mp4",
                 "save_path": "/downloads",
                 "content_path": "/downloads/Jane Doe.mp4"}]
    row = plan_for(library, store, torrents=torrents, path_map=pm).rows[0]
    assert row.action == bf.ACTION_QBT
    assert row.move == bf.MOVE_QBT
    assert row.torrent_hash == "H1"


def test_plan_marks_a_new_directory_from_a_hand_set_alias(library, store):
    (library / "Aster Nightingale.mp4").write_text("x")
    store.upsert_alias("asternightingale", "Aster Nightingale", "")
    rows = bf.plan(library, store=store, dir_names=SAMPLE_DIRS,
                   torrents=[], do_seed=False).rows
    assert rows[0].action == bf.ACTION_NEW
    assert rows[0].proposed_dir == "Aster Nightingale"


def test_plan_skips_everything_when_no_path_map_is_available(library, store):
    """Torrents exist but their host paths cannot be resolved: we cannot tell
    which files are seeding payloads, so nothing may move."""
    (library / "Jane Doe.mp4").write_text("x")
    torrents = [{"hash": "H1", "name": "x", "save_path": "/downloads"}]
    p = plan_for(library, store, torrents=torrents, path_map=None)
    assert all(r.action == bf.ACTION_SKIP for r in p.rows)
    assert any("path map" in n for n in p.notes)


def test_plan_skips_everything_when_the_qbt_state_is_unknown(library, store):
    """qBittorrent unreachable: a plain rename could break a live torrent, so
    the plan refuses to classify anything as movable."""
    (library / "Jane Doe.mp4").write_text("x")
    p = bf.plan(library, store=store, dir_names=SAMPLE_DIRS, torrents=None)
    assert all(r.action == bf.ACTION_SKIP for r in p.rows)
    assert any("state unknown" in n for n in p.notes)


def test_plan_never_writes_into_the_tree(library, store):
    (library / "Jane Doe.mp4").write_text("x")
    before = sorted(p.name for p in library.iterdir())
    plan_for(library, store)
    assert sorted(p.name for p in library.iterdir()) == before


def test_plan_counts_and_manifest_round_trip(library, store, tmp_path):
    (library / "Jane Doe.mp4").write_text("x")
    (library / "0hv9783sdgne5ur3xh53n.mp4").write_text("x")
    p = plan_for(library, store)
    assert p.counts()[bf.ACTION_SKIP] == 1
    paths = bf.write_manifest(p, tmp_path / "manifests")
    reloaded = bf.load_manifest(paths["json"])
    assert [r.relpath for r in reloaded.rows] == [r.relpath for r in p.rows]
    tsv = Path(paths["tsv"]).read_text()
    assert "\t".join(bf.TSV_HEADER) in tsv


def test_manifest_tsv_has_one_row_per_file(library, store, tmp_path):
    for i in range(3):
        (library / f"file{i}.mp4").write_text("x")
    p = plan_for(library, store)
    body = [ln for ln in p.to_tsv().splitlines()
            if ln and not ln.startswith("#") and not ln.startswith("action\t")]
    assert len(body) == 3


# --- apply ----------------------------------------------------------------- #
def test_apply_refuses_without_a_manifest():
    with pytest.raises(bf.ApplyError, match="explicit reviewed manifest"):
        bf.apply(None)


def test_apply_refuses_a_missing_manifest_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        bf.apply(tmp_path / "nope.json")


def test_apply_moves_a_plain_file_with_rename(library, store):
    (library / "Jane Doe.mp4").write_text("payload")
    p = plan_for(library, store)
    results = bf.apply(p)
    assert results["failed"] is None
    assert results["moved"] == ["Jane Doe.mp4"]
    assert (library / "Jane Doe" / "Jane Doe.mp4").read_text() == "payload"
    assert not (library / "Jane Doe.mp4").exists()


def test_apply_dry_run_changes_nothing_but_lists_the_operations(library, store):
    (library / "Jane Doe.mp4").write_text("payload")
    p = plan_for(library, store)
    results = bf.apply(p, dry_run=True)
    assert (library / "Jane Doe.mp4").exists()
    assert any(op.startswith("rename ") for op in results["ops"])


def test_apply_uses_set_location_for_torrent_backed_rows(library, store):
    (library / "Jane Doe.mp4").write_text("payload")
    pm = PathMap("/downloads", str(library))
    torrents = [{"hash": "H1", "name": "Jane Doe.mp4",
                 "save_path": "/downloads",
                 "content_path": "/downloads/Jane Doe.mp4"}]
    p = plan_for(library, store, torrents=torrents, path_map=pm)
    fake = FakeQbt()
    results = bf.apply(p, client=fake, path_map=pm)
    assert results["failed"] is None
    assert fake.moves == [("H1", "/downloads/Jane Doe")]
    # setLocation moves the payload; the backfill must NOT also rename it.
    assert (library / "Jane Doe.mp4").exists()


def test_apply_aborts_when_a_torrent_stops_seeding(library, store):
    (library / "Jane Doe.mp4").write_text("x")
    (library / "john-smith.mp4").write_text("x")
    pm = PathMap("/downloads", str(library))
    torrents = [
        {"hash": "H1", "name": "Jane Doe.mp4", "save_path": "/downloads",
         "content_path": "/downloads/Jane Doe.mp4"},
        {"hash": "H2", "name": "john-smith.mp4", "save_path": "/downloads",
         "content_path": "/downloads/john-smith.mp4"},
    ]
    p = plan_for(library, store, torrents=torrents, path_map=pm)
    results = bf.apply(p, client=FakeQbt(seeding=False), path_map=pm)
    assert results["failed"]["relpath"] == "Jane Doe.mp4"
    assert "not seeding" in results["failed"]["error"]
    assert results["aborted"] == ["john-smith.mp4"]


def test_apply_aborts_the_remaining_rows_on_any_failure(library, store):
    for subject in ("Jane Doe", "john-smith", "Mary_Major"):
        (library / f"{subject}.mp4").write_text("x")
    p = plan_for(library, store)
    calls = []

    def flaky(src, dest):
        calls.append(src)
        if len(calls) == 2:
            raise OSError("disk full")
        Path(src).rename(dest)

    results = bf.apply(p, rename=flaky)
    assert len(results["moved"]) == 1
    assert results["failed"] is not None
    assert len(results["aborted"]) == 1


def test_apply_refuses_a_row_whose_target_directory_is_unsafe(library, store):
    p = bf.Plan(root=str(library), created_at=0, rows=[
        bf.PlanRow(relpath="x.mp4", size=1, proposed_dir="../escape",
                   confidence=1.0, reason="", action=bf.ACTION_FS,
                   move=bf.MOVE_FS)])
    results = bf.apply(p)
    assert "unsafe target directory" in results["failed"]["error"]


def test_apply_refuses_a_row_whose_source_escapes_the_root(library, store):
    p = bf.Plan(root=str(library), created_at=0, rows=[
        bf.PlanRow(relpath="../../etc/passwd", size=1, proposed_dir="Jane Doe",
                   confidence=1.0, reason="", action=bf.ACTION_FS,
                   move=bf.MOVE_FS)])
    results = bf.apply(p)
    assert results["failed"] is not None
    assert "escapes" in results["failed"]["error"] or \
        "absolute" in results["failed"]["error"]


def test_apply_refuses_to_overwrite_an_existing_destination(library, store):
    (library / "Jane Doe.mp4").write_text("new")
    (library / "Jane Doe" / "Jane Doe.mp4").write_text("old")
    p = plan_for(library, store)
    results = bf.apply(p)
    assert "already exists" in results["failed"]["error"]
    assert (library / "Jane Doe" / "Jane Doe.mp4").read_text() == "old"


def test_apply_skips_skip_rows_entirely(library, store):
    (library / "0hv9783sdgne5ur3xh53n.mp4").write_text("x")
    p = plan_for(library, store)
    results = bf.apply(p)
    assert results["moved"] == []
    assert results["skipped"] == ["0hv9783sdgne5ur3xh53n.mp4"]
    assert (library / "0hv9783sdgne5ur3xh53n.mp4").exists()


def test_apply_creates_a_new_directory_then_moves(library, store):
    (library / "Aster Nightingale.mp4").write_text("x")
    store.upsert_alias("asternightingale", "Aster Nightingale", "")
    p = bf.plan(library, store=store, dir_names=SAMPLE_DIRS, torrents=[],
                do_seed=False)
    bf.apply(p)
    assert (library / "Aster Nightingale" / "Aster Nightingale.mp4").exists()


def test_apply_requires_a_client_for_torrent_backed_rows(library, store):
    p = bf.Plan(root=str(library), created_at=0, rows=[
        bf.PlanRow(relpath="x.mp4", size=1, proposed_dir="Jane Doe",
                   confidence=1.0, reason="", action=bf.ACTION_QBT,
                   move=bf.MOVE_QBT, torrent_hash="H1")])
    (library / "x.mp4").write_text("x")
    results = bf.apply(p)
    assert "qBittorrent client" in results["failed"]["error"]


def test_apply_refuses_a_relative_manifest_root():
    p = bf.Plan(root="relative/path", created_at=0, rows=[])
    with pytest.raises(bf.ApplyError, match="absolute"):
        bf.apply(p)
