"""Security: the path-traversal surface and the yt-dlp argv contract.

The directory name that becomes a filesystem path is derived from PAGE MARKUP,
and the filename comes from the server's Content-Disposition.

The hostile-input table lives in `tests/fixtures/name_cases.json` and
`tests/sanitize.test.mjs` drives `extension/sanitize.js` from THE SAME FILE.
That is deliberate: the two tables used to be hand-copied literal lists, and a
differential fuzz found 991 inputs where the implementations disagreed and
neither list noticed — with the sidecar as the LOOSER side, so `/mkdir` could
create a directory the extension would never route into.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import fetcher as fetcher_mod  # noqa: E402
from conftest import load_name_cases  # noqa: E402
from safety import (  # noqa: E402
    UnsafeName, is_http_url, is_safe_dir_name, join_dir_file, safe_dir_name,
    safe_file_name, safe_rel_path,
)

CASES = load_name_cases()
HOSTILE_DIR_NAMES = CASES["hostileDirNames"]
SAFE_DIR_NAMES = CASES["safeDirNames"]
FILE_NAME_CASES = CASES["fileNames"]
URLS_ACCEPTED = CASES["httpUrlsAccepted"]
URLS_REJECTED = CASES["httpUrlsRejected"]


def test_the_shared_fixture_actually_loaded():
    """A silently empty fixture would make every table-driven test below
    vacuously pass, which is exactly the failure mode this file exists to
    prevent."""
    assert len(HOSTILE_DIR_NAMES) >= 30
    assert len(SAFE_DIR_NAMES) >= 8
    assert len(FILE_NAME_CASES) >= 15
    assert len(URLS_ACCEPTED) >= 5
    assert len(URLS_REJECTED) >= 20
    # The {repeat, count} form must have been expanded, not left as a dict.
    assert any(isinstance(n, str) and len(n) == 121 for n in HOSTILE_DIR_NAMES)
    assert any(isinstance(n, str) and len(n) == 120 for n in SAFE_DIR_NAMES)


# --- directory names ------------------------------------------------------- #
@pytest.mark.parametrize("name", HOSTILE_DIR_NAMES)
def test_hostile_directory_names_are_rejected(name):
    assert is_safe_dir_name(name) is False
    with pytest.raises(UnsafeName):
        safe_dir_name(name)


@pytest.mark.parametrize("name", SAFE_DIR_NAMES)
def test_legitimate_directory_names_are_accepted(name):
    assert is_safe_dir_name(name) is True
    assert safe_dir_name(name) == name


def test_non_string_directory_names_are_rejected():
    for value in list(CASES["nonStringNames"]) + [b"bytes"]:
        assert is_safe_dir_name(value) is False


def test_decomposed_unicode_is_rejected_so_two_lookalikes_cannot_coexist():
    decomposed = "José"          # e + combining acute
    precomposed = "José"
    assert is_safe_dir_name(decomposed) is False
    assert is_safe_dir_name(precomposed) is True


def test_unknown_directory_is_refused_against_the_allowlist():
    known = {"Jane Doe", "other"}
    assert safe_dir_name("Jane Doe", known=known) == "Jane Doe"
    with pytest.raises(UnsafeName):
        safe_dir_name("Not Known", known=known)


def test_allowlist_can_be_bypassed_only_via_the_explicit_new_dir_flag():
    known = {"Jane Doe"}
    assert safe_dir_name("Brand New", known=known, allow_new=True) == "Brand New"


def test_fallback_is_returned_instead_of_raising():
    assert safe_dir_name("../evil", fallback="other") == "other"


def test_join_dir_file_refuses_a_traversing_directory():
    with pytest.raises(UnsafeName):
        join_dir_file("../..", "x.mp4")
    assert join_dir_file("Jane Doe", "clip.mp4") == "Jane Doe/clip.mp4"


# --- filenames ------------------------------------------------------------- #
@pytest.mark.parametrize("raw,expected", [tuple(c) for c in FILE_NAME_CASES])
def test_filenames_are_reduced_to_one_safe_component(raw, expected):
    assert safe_file_name(raw) == expected


def test_filename_bidi_characters_are_stripped():
    assert "‮" not in safe_file_name("clip‮gnp.exe")


def test_filename_zero_width_and_c1_characters_are_stripped():
    """Regression for the JS/Python divergence: JS `trim()` strips U+FEFF and
    Python `strip()` does not, while Python treats U+0085 as whitespace and JS
    does not. Both are now dropped outright on both sides."""
    for raw in ("clip﻿.mp4", "clip.mp4", "clip​.mp4"):
        assert safe_file_name(raw) == "clip.mp4"


def test_filename_colon_is_stripped_because_yt_dlp_reads_it_as_a_selector():
    # yt-dlp's `--paths TYPE:PATH` would read the part before the colon as a
    # type selector and silently truncate the destination.
    assert ":" not in safe_file_name("season:one.mp4")


def test_long_filename_is_truncated_but_keeps_its_extension():
    out = safe_file_name("a" * 400 + ".mp4")
    assert len(out) <= 200
    assert out.endswith(".mp4")


def test_filename_never_contains_a_separator():
    for raw in ("a/b/c.mp4", "a\\b\\c.mp4", "..%2f..%2fetc"):
        assert "/" not in safe_file_name(raw)
        assert "\\" not in safe_file_name(raw)


# --- relative paths -------------------------------------------------------- #
def test_safe_rel_path_resolves_inside_the_root(library):
    out = safe_rel_path("Jane Doe", root=library)
    assert out == (library / "Jane Doe").resolve()


@pytest.mark.parametrize("rel", [
    "../outside", "../../etc/passwd", "/etc/passwd", "Jane Doe/../../escape",
    "with\x00nul",
])
def test_safe_rel_path_refuses_escapes(library, rel):
    with pytest.raises(UnsafeName):
        safe_rel_path(rel, root=library)


def test_safe_rel_path_refuses_a_symlink_pointing_outside(library, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (library / "escape-link").symlink_to(outside, target_is_directory=True)
    with pytest.raises(UnsafeName):
        safe_rel_path("escape-link", root=library)


def test_safe_rel_path_refuses_empty_and_non_string():
    for value in ("", None, 7):
        with pytest.raises(UnsafeName):
            safe_rel_path(value, root=Path("/tmp"))


# --- yt-dlp argv ----------------------------------------------------------- #
@pytest.mark.parametrize("url", URLS_ACCEPTED)
def test_http_urls_are_accepted(url):
    assert is_http_url(url) is True


@pytest.mark.parametrize("url", URLS_REJECTED)
def test_non_http_or_malformed_urls_are_refused(url):
    assert is_http_url(url) is False


def test_the_host_rule_does_not_delegate_to_urlsplit():
    """`urlsplit` accepts a percent-escape and a zero-width character as a
    host; WHATWG's `new URL` collapses `http://////..` into the host `..`.
    Neither parser can be the contract, so both implementations validate the
    authority by hand against the same rule."""
    assert is_http_url("http://%2f") is False
    assert is_http_url("http://////..") is False
    assert is_http_url("https:///80") is False
    assert is_http_url("https://example-site.test:99999/v") is False


def test_build_argv_is_a_list_with_a_terminator_and_never_a_shell_string():
    argv = fetcher_mod.build_argv("https://example-site.test/v/1", "/tmp/dest",
                                  ytdlp_bin="yt-dlp")
    assert isinstance(argv, list)
    assert all(isinstance(a, str) for a in argv)
    assert argv[0] == "yt-dlp"
    assert argv[-2] == "--"
    assert argv[-1] == "https://example-site.test/v/1"


def test_build_argv_refuses_a_non_http_url():
    for bad in ("file:///etc/passwd", "-oProxyCommand=id", "javascript:x"):
        with pytest.raises(ValueError):
            fetcher_mod.build_argv(bad, "/tmp/dest")


def test_build_argv_passes_the_destination_as_a_separate_argument():
    argv = fetcher_mod.build_argv("https://example-site.test/v",
                                  "/tmp/dest dir with spaces")
    assert "/tmp/dest dir with spaces" in argv
    assert argv[argv.index("--paths") + 1] == "/tmp/dest dir with spaces"


def test_fetcher_runs_with_shell_false(monkeypatch, library):
    seen = {}

    def fake_popen(argv, cwd=None, shell=None, **kw):
        seen["argv"] = argv
        seen["shell"] = shell
        raise OSError("not actually spawning")

    monkeypatch.setattr(fetcher_mod.subprocess, "Popen", fake_popen)
    try:
        fetcher_mod.default_runner(["yt-dlp", "--", "https://example-site.test/v"])
    except OSError:
        pass
    assert seen["shell"] is False
    assert isinstance(seen["argv"], list)


def test_fetcher_submit_refuses_an_unsafe_directory(library):
    f = fetcher_mod.Fetcher(library, runner=lambda *a, **k: None)
    with pytest.raises(UnsafeName):
        f.submit("https://example-site.test/v", "../escape")
    with pytest.raises(UnsafeName):
        f.submit("https://example-site.test/v", "Jane Doe",
                 known_dirs={"other"})


def test_fetcher_submit_refuses_a_non_http_url(library):
    f = fetcher_mod.Fetcher(library, runner=lambda *a, **k: None)
    with pytest.raises(ValueError):
        f.submit("file:///etc/passwd", "other")


def test_no_download_can_land_outside_the_library(library):
    """End-to-end property: join_dir_file + the library root always resolves
    inside the root, for every hostile directory/filename pair."""
    for dir_name in SAFE_DIR_NAMES:
        for raw_name in ("../../etc/passwd", "..\\..\\evil.exe", "a/b.mp4"):
            rel = join_dir_file(dir_name, raw_name)
            resolved = safe_rel_path(rel, root=library)
            assert str(resolved).startswith(str(library.resolve()) + os.sep)
