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
import qbt as qbt_mod  # noqa: E402
from qbt import PathMap  # noqa: E402


class FakeQbt:
    """A stub qBittorrent. Records setLocation calls; seeding verification and
    the live torrent list are scriptable.

    `apply` re-derives every row against this at run time (spec section 3
    hazard 1), so a stub now has to answer torrents_info/torrents_files too.
    """

    def __init__(self, *, seeding=True, fail_on=None, torrents=None,
                 files=None, files_error=False):
        self.moves = []
        self.seeding = seeding
        self.fail_on = fail_on
        self.torrents = list(torrents or [])
        # {hash: [{"name": "<relative path>"}]}
        self.files = dict(files or {})
        self.files_error = files_error

    def torrents_info(self):
        return list(self.torrents)

    def torrents_files(self, torrent_hash):
        if self.files_error:
            raise RuntimeError("torrents/files unavailable")
        return list(self.files.get(torrent_hash, []))

    def set_location(self, torrent_hash, location):
        if self.fail_on and torrent_hash == self.fail_on:
            raise RuntimeError("setLocation refused")
        self.moves.append((torrent_hash, location))

    def verify_seeding(self, torrent_hash, **kw):
        return self.seeding


def no_torrents():
    """The simplest LIVE state that PROVES nothing is torrent-backed: a
    reachable qBittorrent with no torrents at all."""
    return FakeQbt(torrents=[])


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
    results = bf.apply(p, client=no_torrents())
    assert results["failed"] is None
    assert results["moved"] == ["Jane Doe.mp4"]
    assert (library / "Jane Doe" / "Jane Doe.mp4").read_text() == "payload"
    assert not (library / "Jane Doe.mp4").exists()


def test_apply_dry_run_changes_nothing_but_lists_the_operations(library, store):
    (library / "Jane Doe.mp4").write_text("payload")
    p = plan_for(library, store)
    results = bf.apply(p, client=no_torrents(), dry_run=True)
    assert (library / "Jane Doe.mp4").exists()
    assert any(op.startswith("rename ") for op in results["ops"])


def corroborating_torrent(library):
    """A second torrent so `derive_path_map` has more than one vote. A mapping
    is no longer accepted on a single accidental correlation, and apply derives
    its own map at run time."""
    (library / "Jane Doe" / "extra.mp4").write_text("x")
    return {"hash": "HX", "name": "extra.mp4",
            "save_path": "/downloads/Jane Doe",
            "content_path": "/downloads/Jane Doe/extra.mp4"}


def test_apply_uses_set_location_for_torrent_backed_rows(library, store):
    (library / "Jane Doe.mp4").write_text("payload")
    pm = PathMap("/downloads", str(library))
    torrents = [{"hash": "H1", "name": "Jane Doe.mp4",
                 "save_path": "/downloads",
                 "content_path": "/downloads/Jane Doe.mp4"},
                corroborating_torrent(library)]
    p = plan_for(library, store, torrents=torrents, path_map=pm)
    fake = FakeQbt(torrents=torrents,
                   files={"H1": [{"name": "Jane Doe.mp4"}],
                          "HX": [{"name": "extra.mp4"}]})
    results = bf.apply(p, client=fake, path_map=pm,
                       host_roots=[str(library)])
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
    results = bf.apply(
        p, path_map=pm, host_roots=[str(library)],
        client=FakeQbt(seeding=False, torrents=torrents,
                       files={"H1": [{"name": "Jane Doe.mp4"}],
                              "H2": [{"name": "john-smith.mp4"}]}))
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

    results = bf.apply(p, client=no_torrents(), rename=flaky)
    assert len(results["moved"]) == 1
    assert results["failed"] is not None
    assert len(results["aborted"]) == 1


def test_apply_refuses_a_row_whose_target_directory_is_unsafe(library, store):
    p = bf.Plan(root=str(library), created_at=0, rows=[
        bf.PlanRow(relpath="x.mp4", size=1, proposed_dir="../escape",
                   confidence=1.0, reason="", action=bf.ACTION_FS,
                   move=bf.MOVE_FS)])
    results = bf.apply(p, client=no_torrents())
    assert "unsafe target directory" in results["failed"]["error"]


def test_apply_refuses_a_row_whose_source_escapes_the_root(library, store):
    p = bf.Plan(root=str(library), created_at=0, rows=[
        bf.PlanRow(relpath="../../etc/passwd", size=1, proposed_dir="Jane Doe",
                   confidence=1.0, reason="", action=bf.ACTION_FS,
                   move=bf.MOVE_FS)])
    results = bf.apply(p, client=no_torrents())
    assert results["failed"] is not None
    assert "escapes" in results["failed"]["error"] or \
        "absolute" in results["failed"]["error"]


def test_apply_refuses_to_overwrite_an_existing_destination(library, store):
    (library / "Jane Doe.mp4").write_text("new")
    (library / "Jane Doe" / "Jane Doe.mp4").write_text("old")
    p = plan_for(library, store)
    results = bf.apply(p, client=no_torrents())
    assert "already exists" in results["failed"]["error"]
    assert (library / "Jane Doe" / "Jane Doe.mp4").read_text() == "old"


def test_apply_skips_skip_rows_entirely(library, store):
    (library / "0hv9783sdgne5ur3xh53n.mp4").write_text("x")
    p = plan_for(library, store)
    results = bf.apply(p, client=no_torrents())
    assert results["moved"] == []
    assert results["skipped"] == ["0hv9783sdgne5ur3xh53n.mp4"]
    assert (library / "0hv9783sdgne5ur3xh53n.mp4").exists()


def test_apply_creates_a_new_directory_then_moves(library, store):
    (library / "Aster Nightingale.mp4").write_text("x")
    store.upsert_alias("asternightingale", "Aster Nightingale", "")
    p = bf.plan(library, store=store, dir_names=SAMPLE_DIRS, torrents=[],
                do_seed=False)
    bf.apply(p, client=no_torrents())
    assert (library / "Aster Nightingale" / "Aster Nightingale.mp4").exists()


def test_apply_requires_a_client_for_torrent_backed_rows(library, store):
    p = bf.Plan(root=str(library), created_at=0, rows=[
        bf.PlanRow(relpath="x.mp4", size=1, proposed_dir="Jane Doe",
                   confidence=1.0, reason="", action=bf.ACTION_QBT,
                   move=bf.MOVE_QBT, torrent_hash="H1")])
    (library / "x.mp4").write_text("x")
    with pytest.raises(bf.ApplyError, match="re-validate"):
        bf.apply(p)


def test_apply_refuses_a_relative_manifest_root():
    p = bf.Plan(root="relative/path", created_at=0, rows=[])
    with pytest.raises(bf.ApplyError, match="absolute"):
        bf.apply(p, client=no_torrents())


# ===========================================================================
# Priority-2 findings. Every one of these is about the SAME failure mode:
# treating "we do not know" as "it is safe to rename", inside a live
# qBittorrent seeding target.
# ===========================================================================

# --- 5: an index MISS is not proof ----------------------------------------- #
def test_a_multi_file_torrent_at_the_root_is_seen_via_torrents_files(library,
                                                                     store):
    """THE finding. A no-root-folder torrent's payload sits directly at the
    library root -- exactly the backfill's target population -- and the index
    only knew `content_path` and `save_path/name`, so the file was MISSED and
    classified `fs`: a plain rename of a live seeding payload."""
    (library / "Jane Doe.mp4").write_text("payload")
    pm = PathMap("/downloads", str(library))
    torrents = [{"hash": "H1", "name": "Some Multi-File Release",
                 "save_path": "/downloads",
                 # content_path points at the SAVE PATH for a no-root-folder
                 # torrent, which is the library root itself -- naming nothing.
                 "content_path": "/downloads"}]
    files = {"H1": [{"name": "Jane Doe.mp4"}, {"name": "other-part.mp4"}]}
    row = bf.plan(library, store=store, dir_names=SAMPLE_DIRS,
                  torrents=torrents, path_map=pm,
                  files_for=lambda h: files[h]).rows[0]
    assert row.move == bf.MOVE_QBT
    assert row.torrent_hash == "H1"
    assert row.action == bf.ACTION_QBT


def test_without_a_file_listing_nothing_may_be_classified_fs(library, store):
    """No `files_for` -> the index cannot be exhaustive -> absence proves
    nothing -> SKIP, never `fs`."""
    (library / "Jane Doe.mp4").write_text("payload")
    pm = PathMap("/downloads", str(library))
    torrents = [{"hash": "H1", "name": "Unrelated", "save_path": "/downloads",
                 "content_path": "/downloads/Unrelated"}]
    p = bf.plan(library, store=store, dir_names=SAMPLE_DIRS,
                torrents=torrents, path_map=pm)
    row = p.rows[0]
    assert row.move == bf.MOVE_UNKNOWN
    assert row.action == bf.ACTION_SKIP
    assert any("cannot be proven safe" in n for n in p.notes)


def test_a_failing_file_listing_also_forbids_fs(library, store):
    (library / "Jane Doe.mp4").write_text("payload")
    pm = PathMap("/downloads", str(library))
    torrents = [{"hash": "H1", "name": "Unrelated", "save_path": "/downloads"}]

    def boom(_hash):
        raise RuntimeError("torrents/files unavailable")

    row = bf.plan(library, store=store, dir_names=SAMPLE_DIRS,
                  torrents=torrents, path_map=pm, files_for=boom).rows[0]
    assert row.action == bf.ACTION_SKIP
    assert row.move == bf.MOVE_UNKNOWN


def test_an_empty_torrent_list_is_positive_proof(library, store):
    """A reachable qBittorrent with NO torrents proves nothing is
    torrent-backed, so a plain rename is genuinely safe."""
    (library / "Jane Doe.mp4").write_text("payload")
    row = bf.plan(library, store=store, dir_names=SAMPLE_DIRS,
                  torrents=[]).rows[0]
    assert row.move == bf.MOVE_FS
    assert row.action == bf.ACTION_FS


def test_index_by_host_path_reports_whether_it_is_exhaustive():
    pm = PathMap("/downloads", "/host/disk")
    torrents = [{"hash": "h1", "name": "clip.mp4", "save_path": "/downloads",
                 "content_path": "/downloads/clip.mp4"}]
    partial = qbt_mod.index_by_host_path(torrents, pm)
    assert partial.complete is False
    full = qbt_mod.index_by_host_path(
        torrents, pm, files_for=lambda h: [{"name": "clip.mp4"}])
    assert full.complete is True
    assert full["/host/disk/clip.mp4"]["hash"] == "h1"


# --- 9: the filename cap ---------------------------------------------------- #
def test_a_filename_alone_never_reaches_the_auto_threshold(library, store):
    """The stem used to be smuggled in as a page TAG, scoring 0.85 through the
    tag-exact rule; spec section 7 caps the filename signal at 0.50."""
    (library / "jane doe scene 02.mp4").write_text("x")
    row = bf.plan(library, store=store, dir_names=SAMPLE_DIRS, torrents=[],
                  do_seed=False).rows[0]
    assert row.action == bf.ACTION_SKIP
    assert row.confidence <= 0.50
    assert row.signal == bf.SIGNAL_FILENAME
    assert "FILENAME" in row.reason.upper() or "filename" in row.reason


def test_a_filename_only_row_is_labelled_as_such(library, store):
    (library / "0hv9783sdgne5ur3xh53n.mp4").write_text("x")
    row = bf.plan(library, store=store, dir_names=SAMPLE_DIRS, torrents=[],
                  do_seed=False).rows[0]
    assert row.signal == bf.SIGNAL_NONE
    assert row.action == bf.ACTION_SKIP


def test_an_alias_hit_is_labelled_alias_and_may_be_filed(library, store):
    """The one signal allowed to carry a row: recorded knowledge, not a guess
    about an opaque filename."""
    (library / "Jane Doe.mp4").write_text("x")
    row = plan_for(library, store).rows[0]
    assert row.signal == bf.SIGNAL_ALIAS
    assert row.action == bf.ACTION_FS
    assert row.proposed_dir == "Jane Doe"


def test_an_alias_hit_still_will_not_rename_what_it_cannot_prove(library, store):
    """Even a confident alias must not become `fs` without positive proof the
    file is not torrent-backed."""
    (library / "Jane Doe.mp4").write_text("x")
    pm = PathMap("/downloads", str(library))
    torrents = [{"hash": "H9", "name": "Unrelated", "save_path": "/downloads"}]
    row = bf.plan(library, store=store, dir_names=SAMPLE_DIRS,
                  torrents=torrents, path_map=pm).rows[0]
    assert row.signal == bf.SIGNAL_ALIAS
    assert row.action == bf.ACTION_SKIP
    assert "NOT PROVEN" in row.reason


# --- 13: plan is read-only -------------------------------------------------- #
def test_plan_does_not_write_to_the_alias_database(library, store):
    """`plan` is advertised read-only, and was upserting into the same alias
    table that drives LIVE routing."""
    (library / "Jane Doe.mp4").write_text("x")
    before = store.alias_count()
    p = bf.plan(library, store=store, dir_names=SAMPLE_DIRS, torrents=[])
    assert store.alias_count() == before, "plan must not touch the alias DB"
    assert any("would be seeded" in n for n in p.notes)
    # ...and it is still useful: the in-memory seeds still classify the row.
    assert p.rows[0].action == bf.ACTION_FS
    assert p.rows[0].proposed_dir == "Jane Doe"


def test_seed_aliases_persists_only_when_asked(library, store):
    (library / "Jane Doe.mp4").write_text("x")
    before = store.alias_count()
    p = bf.plan(library, store=store, dir_names=SAMPLE_DIRS, torrents=[],
                persist_seeds=True)
    assert store.alias_count() > before
    assert any("seeded" in n and "would" not in n for n in p.notes)


# --- 7: the reviewed artefact is the applied artefact ----------------------- #
def test_the_tsv_round_trips_through_load_manifest(library, store, tmp_path):
    (library / "Jane Doe.mp4").write_text("x")
    (library / "0hv9783sdgne5ur3xh53n.mp4").write_text("x")
    p = plan_for(library, store)
    paths = bf.write_manifest(p, tmp_path / "manifests")
    reloaded = bf.load_manifest(paths["tsv"])
    assert reloaded.root == p.root
    assert [(r.relpath, r.action, r.move, r.proposed_dir) for r in reloaded.rows] \
        == [(r.relpath, r.action, r.move, r.proposed_dir) for r in p.rows]


def test_editing_the_action_column_in_the_tsv_actually_disables_a_row(
        library, store, tmp_path):
    """THE finding. The TSV header says to edit `action`; load_manifest read
    JSON only, so the edit was silently discarded -- on the one operation that
    can break seeding."""
    (library / "Jane Doe.mp4").write_text("payload")
    p = plan_for(library, store)
    assert p.rows[0].action == bf.ACTION_FS
    paths = bf.write_manifest(p, tmp_path / "manifests")

    tsv = Path(paths["tsv"])
    edited = tsv.read_text().replace("\nfs\t", "\nSKIP\t")
    tsv.write_text(edited)

    reviewed = bf.load_manifest(tsv)
    assert reviewed.rows[0].action == bf.ACTION_SKIP
    results = bf.apply(reviewed, client=no_torrents())
    assert results["moved"] == []
    assert results["skipped"] == ["Jane Doe.mp4"]
    assert (library / "Jane Doe.mp4").exists(), "the SKIP edit was ignored"


def test_applying_the_json_refuses_when_the_tsv_was_edited(library, store,
                                                           tmp_path):
    """Pointing apply at the JSON after editing the TSV must not silently run
    the unedited plan."""
    (library / "Jane Doe.mp4").write_text("payload")
    p = plan_for(library, store)
    paths = bf.write_manifest(p, tmp_path / "manifests")
    tsv = Path(paths["tsv"])
    tsv.write_text(tsv.read_text().replace("\nfs\t", "\nSKIP\t"))
    with pytest.raises(bf.ApplyError, match="reviewed artefact"):
        bf.load_manifest(paths["json"])


def test_applying_the_json_is_fine_when_the_tsv_is_untouched(library, store,
                                                             tmp_path):
    (library / "Jane Doe.mp4").write_text("payload")
    p = plan_for(library, store)
    paths = bf.write_manifest(p, tmp_path / "manifests")
    reloaded = bf.load_manifest(paths["json"])
    assert [r.action for r in reloaded.rows] == [r.action for r in p.rows]


@pytest.mark.parametrize("mutate,match", [
    (lambda s: s.replace("action\tmove", "move\taction"), "column header"),
    (lambda s: s.replace("\nfs\t", "\nBOGUS\t"), "action"),
    (lambda s: s.replace("#!root", "#!nroot"), "not a dl-router manifest"),
])
def test_a_mangled_tsv_is_refused_rather_than_guessed(library, store, tmp_path,
                                                      mutate, match):
    (library / "Jane Doe.mp4").write_text("x")
    paths = bf.write_manifest(plan_for(library, store), tmp_path / "manifests")
    tsv = Path(paths["tsv"])
    tsv.write_text(mutate(tsv.read_text()))
    with pytest.raises(bf.ApplyError, match=match):
        bf.load_manifest(tsv)


def test_the_tsv_header_tells_the_truth_about_being_applied(library, store):
    (library / "Jane Doe.mp4").write_text("x")
    tsv = plan_for(library, store).to_tsv()
    assert "DOES take effect" in tsv
    assert "--manifest <this .tsv>" in tsv


def test_a_filename_with_a_tab_is_left_out_of_the_plan_entirely(library, store):
    """It would break the TSV into the wrong number of columns, and
    safe_rel_path would refuse it at apply time anyway."""
    try:
        (library / "we\tird.mp4").write_text("x")
    except OSError:
        pytest.skip("filesystem rejects a tab in a filename")
    (library / "Jane Doe.mp4").write_text("x")
    p = plan_for(library, store)
    assert [r.relpath for r in p.rows] == ["Jane Doe.mp4"]


# --- 8: apply re-validates against LIVE qBittorrent ------------------------- #
def test_apply_refuses_without_a_client_even_for_fs_rows(library, store):
    (library / "Jane Doe.mp4").write_text("x")
    p = plan_for(library, store)
    assert p.rows[0].move == bf.MOVE_FS
    with pytest.raises(bf.ApplyError, match="re-validate"):
        bf.apply(p)


def test_apply_aborts_when_a_file_became_torrent_backed_since_the_plan(
        library, store):
    """The plan said `fs`; by apply time a torrent claims the file. Renaming it
    would break a live seed."""
    (library / "Jane Doe.mp4").write_text("payload")
    p = plan_for(library, store)
    assert p.rows[0].move == bf.MOVE_FS

    corroborate = corroborating_torrent(library)
    live_torrents = [
        {"hash": "NEW1", "name": "Jane Doe.mp4", "save_path": "/downloads",
         "content_path": "/downloads/Jane Doe.mp4"},
        corroborate,
    ]
    client = FakeQbt(torrents=live_torrents,
                     files={"NEW1": [{"name": "Jane Doe.mp4"}],
                            "HX": [{"name": "extra.mp4"}]})
    results = bf.apply(p, client=client, host_roots=[str(library)])
    assert results["failed"] is not None
    assert "live qBittorrent state says" in results["failed"]["error"]
    assert (library / "Jane Doe.mp4").exists()
    assert client.moves == []


def test_apply_aborts_when_the_backing_torrent_hash_changed(library, store):
    (library / "Jane Doe.mp4").write_text("payload")
    pm = PathMap("/downloads", str(library))
    corroborate = corroborating_torrent(library)
    planned = [{"hash": "H1", "name": "Jane Doe.mp4", "save_path": "/downloads",
                "content_path": "/downloads/Jane Doe.mp4"}, corroborate]
    p = plan_for(library, store, torrents=planned, path_map=pm)
    assert p.rows[0].torrent_hash == "H1"

    live = [{"hash": "DIFFERENT", "name": "Jane Doe.mp4",
             "save_path": "/downloads",
             "content_path": "/downloads/Jane Doe.mp4"}, corroborate]
    client = FakeQbt(torrents=live,
                     files={"DIFFERENT": [{"name": "Jane Doe.mp4"}],
                            "HX": [{"name": "extra.mp4"}]})
    results = bf.apply(p, client=client, path_map=pm,
                       host_roots=[str(library)])
    assert "torrent backing this file changed" in results["failed"]["error"]
    assert client.moves == []


def test_apply_uses_the_LIVE_hash_not_the_manifests(library, store):
    """Runtime derivation, not a replay of plan-time values."""
    (library / "Jane Doe.mp4").write_text("payload")
    pm = PathMap("/downloads", str(library))
    corroborate = corroborating_torrent(library)
    torrents = [{"hash": "H1", "name": "Jane Doe.mp4", "save_path": "/downloads",
                 "content_path": "/downloads/Jane Doe.mp4"}, corroborate]
    p = plan_for(library, store, torrents=torrents, path_map=pm)
    client = FakeQbt(torrents=torrents,
                     files={"H1": [{"name": "Jane Doe.mp4"}],
                            "HX": [{"name": "extra.mp4"}]})
    results = bf.apply(p, client=client, path_map=pm,
                       host_roots=[str(library)])
    assert results["failed"] is None
    assert client.moves == [("H1", "/downloads/Jane Doe")]
    assert any("revalidated against qBittorrent" in op
               for op in results["ops"])


def test_apply_refuses_when_live_state_cannot_prove_anything(library, store):
    (library / "Jane Doe.mp4").write_text("payload")
    p = plan_for(library, store)
    # Live: torrents exist and none of them NAMES this file, but their file
    # listings cannot be read -- so absence from the index proves nothing.
    client = FakeQbt(
        torrents=[{"hash": "H1", "name": "Some Multi-File Release",
                   "save_path": "/downloads", "content_path": "/downloads"},
                  corroborating_torrent(library)],
        files_error=True)
    results = bf.apply(p, client=client, host_roots=[str(library)])
    assert "cannot prove" in results["failed"]["error"]
    assert (library / "Jane Doe.mp4").exists()


def test_a_move_unknown_row_is_never_executable(library, store):
    (library / "x.mp4").write_text("x")
    p = bf.Plan(root=str(library), created_at=0, rows=[
        bf.PlanRow(relpath="x.mp4", size=1, proposed_dir="Jane Doe",
                   confidence=1.0, reason="", action=bf.ACTION_FS,
                   move=bf.MOVE_UNKNOWN)])
    results = bf.apply(p, client=no_torrents(), revalidate=False)
    assert bf.MOVE_UNKNOWN in results["failed"]["error"]
    assert (library / "x.mp4").exists()
