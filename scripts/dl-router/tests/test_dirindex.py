"""DirIndex/FileIndex: cache invalidation, awkward names, symlinks, and
permission errors. The clock is injected, so nothing here sleeps.
"""
from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dirindex import DirIndex, FileIndex  # noqa: E402


# --- basic scanning -------------------------------------------------------- #
def test_lists_top_level_directories_only(library, clock):
    (library / "Jane Doe" / "nested").mkdir()
    (library / "loose-file.mp4").write_text("x")
    idx = DirIndex(library, clock=clock)
    names = idx.names()
    assert "Jane Doe" in names
    assert "nested" not in names
    assert "loose-file.mp4" not in names


def test_hidden_directories_are_skipped(library, clock):
    (library / ".hidden").mkdir()
    assert ".hidden" not in DirIndex(library, clock=clock).names()


def test_names_are_sorted_and_stable(library, clock):
    idx = DirIndex(library, clock=clock)
    assert idx.names() == sorted(idx.names())


@pytest.mark.parametrize("name", [
    "Subject With Spaces",
    "subject [bracketed]",
    "subject (parens)",
    "José Álvarez",
    "名前",
    "sub+plus&amp",
    "dash-and_underscore.dot",
])
def test_unusual_but_legal_directory_names_are_indexed(library, clock, name):
    (library / name).mkdir()
    idx = DirIndex(library, clock=clock)
    assert name in idx.names()
    assert idx.has(name)


def test_a_symlinked_directory_INSIDE_the_root_is_a_routing_target(library,
                                                                   clock):
    """A symlinked subject dir is a legitimate layout, as long as it resolves
    back inside the library."""
    (library / "real-target").mkdir()
    (library / "linked").symlink_to(library / "real-target",
                                    target_is_directory=True)
    assert "linked" in DirIndex(library, clock=clock).names()


def test_a_symlink_ESCAPING_the_root_is_not_a_routing_target(library, tmp_path,
                                                             clock):
    """ONE invariant, honoured by every writer.

    safe_rel_path (used by /mkdir, /relocate and the yt-dlp fetcher) refuses a
    directory that resolves outside the root, so advertising one here produced
    a target that browser downloads could write THROUGH -- out of the library,
    past every containment check -- while yt-dlp refused it. The two halves
    disagreed about the same directory."""
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (library / "escape-link").symlink_to(outside, target_is_directory=True)
    idx = DirIndex(library, clock=clock)
    assert "escape-link" not in idx.names()
    # Excluded, not silently dropped: it shows up in /dirs' errors and in
    # `dl-route status`.
    assert any("escape-link" in e for e in idx.errors())


def test_broken_symlink_is_skipped(library, clock):
    (library / "dangling").symlink_to(library / "nope", target_is_directory=True)
    idx = DirIndex(library, clock=clock)
    assert "dangling" not in idx.names()


def test_missing_root_yields_an_empty_index_not_an_exception(tmp_path, clock):
    idx = DirIndex(tmp_path / "nope", clock=clock)
    assert idx.names() == []
    assert idx.errors()


def test_unreadable_root_is_reported_not_raised(tmp_path, clock):
    if os.geteuid() == 0:
        pytest.skip("root bypasses directory permissions")
    root = tmp_path / "locked"
    root.mkdir()
    (root / "Jane Doe").mkdir()
    os.chmod(root, 0o000)
    try:
        idx = DirIndex(root, clock=clock)
        assert idx.names() == []
        assert idx.errors()
    finally:
        os.chmod(root, stat.S_IRWXU)


# --- caching --------------------------------------------------------------- #
def test_cache_is_invalidated_when_the_root_mtime_changes(library, clock):
    idx = DirIndex(library, clock=clock, ttl=1000.0)
    before = idx.names()
    (library / "New Subject").mkdir()
    # A new subdirectory bumps the root's mtime, so even a long TTL re-scans.
    assert "New Subject" in idx.names()
    assert len(idx.names()) == len(before) + 1


def test_cache_is_reused_within_the_ttl(library, clock, monkeypatch):
    idx = DirIndex(library, clock=clock, ttl=100.0)
    idx.names()
    calls = []
    real_scan = idx._scan
    monkeypatch.setattr(idx, "_scan", lambda: (calls.append(1), real_scan())[1])
    idx.names()
    idx.names()
    assert calls == [], "no rescan expected inside the TTL"


def test_cache_expires_after_the_ttl(library, clock, monkeypatch):
    idx = DirIndex(library, clock=clock, ttl=5.0)
    idx.names()
    calls = []
    real_scan = idx._scan
    monkeypatch.setattr(idx, "_scan", lambda: (calls.append(1), real_scan())[1])
    clock.advance(6)
    idx.names()
    assert calls == [1]


def test_force_refresh_always_rescans(library, clock, monkeypatch):
    idx = DirIndex(library, clock=clock, ttl=1000.0)
    idx.names()
    calls = []
    real_scan = idx._scan
    monkeypatch.setattr(idx, "_scan", lambda: (calls.append(1), real_scan())[1])
    idx.refresh(force=True)
    assert calls == [1]


def test_etag_changes_only_when_the_directory_set_changes(library, clock):
    idx = DirIndex(library, clock=clock, ttl=0.0)
    first = idx.etag()
    assert idx.etag() == first
    (library / "Another One").mkdir()
    assert idx.etag() != first


def test_snapshot_shape(library, clock):
    snap = DirIndex(library, clock=clock, other_dir="other").snapshot()
    assert set(snap) >= {"etag", "otherDir", "dirs", "errors"}
    assert snap["otherDir"] == "other"
    entry = snap["dirs"][0]
    assert set(entry) == {"name", "key", "tokens"}


def test_by_key_folds_naming_conventions(library, clock):
    idx = DirIndex(library, clock=clock)
    assert idx.by_key("JANE_DOE").name == "Jane Doe"
    assert idx.by_key("John Smith").name == "john-smith"
    assert idx.by_key("nothing here") is None


# --- FileIndex ------------------------------------------------------------- #
def test_file_index_maps_normalised_stems_to_relpaths(library, clock):
    (library / "Jane Doe" / "Clip One.mp4").write_text("abc")
    idx = FileIndex(library, clock=clock)
    hits = idx.by_name_key("clipone")
    assert hits == [("Jane Doe/Clip One.mp4", 3)]


def test_file_index_ignores_hidden_files_and_dirs(library, clock):
    (library / "Jane Doe" / ".secret.mp4").write_text("x")
    (library / ".hidden").mkdir()
    (library / ".hidden" / "a.mp4").write_text("x")
    idx = FileIndex(library, clock=clock)
    assert idx.by_name_key("secret") == []
    assert idx.by_name_key("a") == []


def test_file_index_does_not_follow_symlinks_out_of_the_library(library,
                                                                tmp_path, clock):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "leaked.mp4").write_text("x")
    (library / "link").symlink_to(outside, target_is_directory=True)
    idx = FileIndex(library, clock=clock)
    assert idx.by_name_key("leaked") == []


def test_file_index_respects_the_max_files_cap(library, clock):
    for i in range(10):
        (library / "Jane Doe" / f"f{i}.mp4").write_text("x")
    idx = FileIndex(library, clock=clock, max_files=3)
    assert idx.stats()["truncated"] is True


def test_file_index_ttl(library, clock, monkeypatch):
    idx = FileIndex(library, clock=clock, ttl=10.0)
    idx.refresh()
    calls = []
    monkeypatch.setattr(idx, "_scan",
                        lambda: (calls.append(1), ({}, {}, {}, 0, False))[1])
    idx.refresh()
    assert calls == []
    clock.advance(11)
    idx.refresh()
    assert calls == [1]


# --- per-directory item counts --------------------------------------------- #
#
# The picker shows how many files each subject directory holds. The tally is a
# BY-PRODUCT of the dedupe walk, never a scan of its own -- see FileIndex's
# docstring and, more importantly, `dir_counts`, which must not refresh.
def test_file_index_counts_files_per_top_level_directory(library, clock):
    (library / "Jane Doe" / "one.mp4").write_text("x")
    (library / "Jane Doe" / "two.mp4").write_text("x")
    (library / "john-smith" / "solo.mp4").write_text("x")
    idx = FileIndex(library, clock=clock)
    idx.refresh()
    assert idx.dir_counts() == {"Jane Doe": 2, "john-smith": 1}


def test_counts_include_files_nested_below_the_subject_directory(library, clock):
    nested = library / "Jane Doe" / "2026" / "set-a"
    nested.mkdir(parents=True)
    (nested / "deep.mp4").write_text("x")
    (library / "Jane Doe" / "shallow.mp4").write_text("x")
    idx = FileIndex(library, clock=clock)
    idx.refresh()
    # A directory "holds" everything under it: a per-subject total that ignored
    # the layout inside would be a different, less useful number.
    assert idx.dir_counts()["Jane Doe"] == 2


def test_a_loose_file_in_the_library_root_is_counted_under_no_directory(
        library, clock):
    (library / "unfiled.mp4").write_text("x")
    (library / "Jane Doe" / "filed.mp4").write_text("x")
    idx = FileIndex(library, clock=clock)
    idx.refresh()
    counts = idx.dir_counts()
    assert counts == {"Jane Doe": 1}
    assert "unfiled.mp4" not in counts


def test_counts_skip_hidden_files_exactly_like_the_dedupe_index(library, clock):
    (library / "Jane Doe" / ".partial.mp4").write_text("x")
    (library / "Jane Doe" / "real.mp4").write_text("x")
    idx = FileIndex(library, clock=clock)
    idx.refresh()
    assert idx.dir_counts() == {"Jane Doe": 1}


def test_dir_counts_NEVER_triggers_a_scan(library, clock, monkeypatch):
    """THE pin on the cost decision.

    `dir_counts` is called from /dirs, which the extension hits on every picker
    open. Adding a `self.refresh()` to it would put a whole-tree walk of a media
    library on that path -- and the extension gives up on the snapshot after
    4 s, so a large library would turn a decorative counter into a picker that
    never loads. This test fails the moment that refresh is added.
    """
    (library / "Jane Doe" / "one.mp4").write_text("x")
    idx = FileIndex(library, clock=clock)
    calls = []
    real_scan = idx._scan
    monkeypatch.setattr(idx, "_scan", lambda: (calls.append(1), real_scan())[1])
    assert idx.dir_counts() == {}, "no walk has happened yet, so no counts"
    assert calls == [], "dir_counts must not scan"
    idx.refresh()
    assert idx.dir_counts() == {"Jane Doe": 1}
    assert calls == [1], "the ONE walk is the dedupe index's own"


def test_a_TRUNCATED_walk_reports_no_counts_at_all(library, clock):
    """A partial tally of an unknown fraction of the library would be rendered
    next to a directory name with no hint that it is wrong. Same rule the
    picker applies to a malformed count: a wrong number is worse than none.
    """
    for i in range(10):
        (library / "Jane Doe" / f"f{i}.mp4").write_text("x")
    idx = FileIndex(library, clock=clock, max_files=3)
    idx.refresh()
    assert idx.stats()["truncated"] is True
    assert idx.dir_counts() == {}


def test_an_untruncated_walk_of_the_same_tree_does_report_them(library, clock):
    for i in range(10):
        (library / "Jane Doe" / f"f{i}.mp4").write_text("x")
    idx = FileIndex(library, clock=clock, max_files=1000)
    idx.refresh()
    assert idx.dir_counts() == {"Jane Doe": 10}
