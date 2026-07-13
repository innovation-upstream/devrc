"""Deterministic pre-scan tests — marker/skip detection, file:line correctness,
per-repo capping, churn/large/lockfile signals, and global interleave cap.

All fixtures are built on a real temp directory tree (tmp_path) so the file-walk,
line-numbering, and ordering are exercised end-to-end without any network or git remote.
"""
import shutil
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import prescan  # noqa: E402


def _write(root: Path, rel: str, content: str) -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    return p


# ---- markers ------------------------------------------------------------------

def test_marker_extraction_file_and_line(tmp_path):
    _write(tmp_path, "a.py", "x = 1\n# TODO: fix this\ny = 2\n# FIXME later\n")
    cands = prescan.scan_markers(tmp_path, "repo", cap=10)
    assert [c.line for c in cands] == [2, 4]
    assert cands[0].kind == "marker"
    assert cands[0].file == "a.py"
    assert "TODO" in cands[0].text
    assert cands[0].repo == "repo"


def test_marker_ref_format(tmp_path):
    _write(tmp_path, "pkg/mod.go", "// HACK: temporary\n")
    c = prescan.scan_markers(tmp_path, "myrepo", cap=10)[0]
    assert c.ref == "myrepo/pkg/mod.go:1"


def test_marker_cap_is_enforced(tmp_path):
    body = "".join(f"# TODO {i}\n" for i in range(20))
    _write(tmp_path, "big.py", body)
    cands = prescan.scan_markers(tmp_path, "repo", cap=5)
    assert len(cands) == 5


def test_marker_skips_pruned_dirs(tmp_path):
    _write(tmp_path, "node_modules/dep.js", "// TODO vendored\n")
    _write(tmp_path, "src.py", "# TODO real\n")
    cands = prescan.scan_markers(tmp_path, "repo", cap=10)
    files = {c.file for c in cands}
    assert "src.py" in files
    assert not any("node_modules" in f for f in files)


def test_walk_markers_ignores_binary_exts(tmp_path):
    _write(tmp_path, "img.png", "TODO not code\n")
    cands = prescan._walk_markers(tmp_path)
    assert cands == []


# ---- markers: quoted-literal suppression (false positive class 1) -------------

def test_quoted_marker_helper_suppresses_string_literals():
    # A marker token flanked by matching quotes on both sides is a data/enum literal.
    assert prescan._has_unquoted_marker("WHEN 16 THEN RETURN 'XXX';") is False
    assert prescan._has_unquoted_marker('const k = "XXX";') is False
    assert prescan._has_unquoted_marker("label = `TODO`") is False
    # An ordinary comment marker (char before is a space, not a quote) survives.
    assert prescan._has_unquoted_marker("# TODO: real thing") is True
    assert prescan._has_unquoted_marker("// FIXME later") is True
    # Mixed quote chars on either side must NOT count as wrapped.
    assert prescan._has_unquoted_marker("x = 'XXX\"") is True


def test_quoted_marker_not_flagged_in_scan(tmp_path):
    # SQL enum literals must not become marker candidates.
    _write(tmp_path, "enum.sql",
           "SELECT CASE code\n  WHEN 16 THEN RETURN 'XXX'\n  WHEN 17 THEN 'ok'\nEND;\n")
    _write(tmp_path, "consts.js", 'const placeholder = "XXX";\nconst tag = `TODO`;\n')
    cands = prescan.scan_markers(tmp_path, "repo", cap=10)
    assert cands == []


def test_line_with_quoted_and_real_marker_still_flagged(tmp_path):
    # A quoted XXX literal AND a genuine trailing comment marker → the real one survives.
    _write(tmp_path, "q.sql",
           "WHEN 16 THEN RETURN 'XXX';  -- real comment TODO here\n")
    cands = prescan.scan_markers(tmp_path, "repo", cap=10)
    assert len(cands) == 1
    assert cands[0].line == 1
    assert "TODO" in cands[0].text


def test_genuine_marker_still_flagged(tmp_path):
    _write(tmp_path, "real.py", "def f():\n    pass  # TODO: real thing\n")
    cands = prescan.scan_markers(tmp_path, "repo", cap=10)
    assert len(cands) == 1
    assert cands[0].line == 2


# ---- markers: .md leak parity between rg and walk paths (false positive class 2)

def test_walk_markers_ignores_md_files(tmp_path):
    # `.md` is not in SCAN_EXTS — the walk path must never scan RULES.md / handoff docs.
    _write(tmp_path, "RULES.md", "- no TODO comments for core functionality\n")
    _write(tmp_path, "src.py", "# TODO real\n")
    cands = prescan._walk_markers(tmp_path)
    files = {c.file for c in cands}
    assert "src.py" in files
    assert "RULES.md" not in files


@pytest.mark.skipif(shutil.which("rg") is None, reason="ripgrep not on PATH")
def test_rg_markers_ignores_md_files(tmp_path):
    # rg walks EVERY file (ignores SCAN_EXTS); the SCAN_EXTS filter must exclude `.md`
    # so the rg path agrees with the walk path (deterministic regardless of rg presence).
    _write(tmp_path, "RULES.md", "- no TODO comments for core functionality\n")
    _write(tmp_path, "src.py", "# TODO real\n")
    cands = prescan._rg_markers(tmp_path)
    files = {c.file for c in cands}
    assert "src.py" in files
    assert "RULES.md" not in files


def test_scan_markers_excludes_md_either_path(tmp_path):
    # End-to-end via whichever backend (rg or walk) the env resolves to.
    _write(tmp_path, "doc.md", "TODO write this section\n")
    _write(tmp_path, "code.py", "# TODO real\n")
    cands = prescan.scan_markers(tmp_path, "repo", cap=10)
    files = {c.file for c in cands}
    assert "code.py" in files
    assert "doc.md" not in files


# ---- skipped tests ------------------------------------------------------------

def test_skipped_pytest_detected(tmp_path):
    _write(tmp_path, "test_x.py",
           "import pytest\n@pytest.mark.skip(reason='flaky')\ndef test_a():\n    pass\n")
    cands = prescan.scan_skipped_tests(tmp_path, "repo", cap=10)
    assert len(cands) == 1
    assert cands[0].kind == "skipped_test"
    assert cands[0].line == 2
    assert "pytest.skip" in cands[0].text


def test_skipped_js_detected(tmp_path):
    _write(tmp_path, "a.test.js", "describe('x', () => {\n  it.skip('todo', () => {});\n});\n")
    cands = prescan.scan_skipped_tests(tmp_path, "repo", cap=10)
    assert any(c.line == 2 and "js.skip" in c.text for c in cands)


def test_skipped_go_detected(tmp_path):
    _write(tmp_path, "x_test.go", "func TestFoo(t *testing.T) {\n\tt.Skip(\"wip\")\n}\n")
    cands = prescan.scan_skipped_tests(tmp_path, "repo", cap=10)
    assert any(c.line == 2 and "go.skip" in c.text for c in cands)


def test_skipped_rust_ignore_detected(tmp_path):
    _write(tmp_path, "lib.rs", "#[ignore]\n#[test]\nfn t() {}\n")
    cands = prescan.scan_skipped_tests(tmp_path, "repo", cap=10)
    assert any("rust.ignore" in c.text for c in cands)


def test_skipped_cap(tmp_path):
    body = "".join(f"@pytest.mark.skip\ndef t{i}(): pass\n" for i in range(10))
    _write(tmp_path, "test_many.py", body)
    cands = prescan.scan_skipped_tests(tmp_path, "repo", cap=3)
    assert len(cands) == 3


# ---- large files --------------------------------------------------------------

def test_large_file_over_threshold(tmp_path):
    _write(tmp_path, "big.py", "x=1\n" * 50)
    _write(tmp_path, "small.py", "x=1\n" * 5)
    cands = prescan.scan_large_files(tmp_path, "repo", cap=10, threshold=40)
    files = {c.file for c in cands}
    assert "big.py" in files
    assert "small.py" not in files
    assert cands[0].line == 0  # file-level signal
    assert "LOC" in cands[0].text


def test_large_file_sorted_desc(tmp_path):
    _write(tmp_path, "bigger.py", "x\n" * 100)
    _write(tmp_path, "big.py", "x\n" * 60)
    cands = prescan.scan_large_files(tmp_path, "repo", cap=10, threshold=40)
    assert cands[0].file == "bigger.py"


# ---- stale lockfiles ----------------------------------------------------------

def test_stale_lock_flagged(tmp_path):
    p = _write(tmp_path, "poetry.lock", "old\n")
    old = time.time() - 400 * 86400
    import os
    os.utime(p, (old, old))
    cands = prescan.scan_stale_locks(tmp_path, "repo", cap=10, max_age_days=365)
    assert len(cands) == 1
    assert cands[0].kind == "stale_lock"
    assert "untouched" in cands[0].text


def test_fresh_lock_not_flagged(tmp_path):
    _write(tmp_path, "flake.lock", "new\n")
    cands = prescan.scan_stale_locks(tmp_path, "repo", cap=10, max_age_days=365)
    assert cands == []


# ---- repo orchestration + caps ------------------------------------------------

def test_scan_repo_missing_dir_sets_error():
    rs = prescan.scan_repo("/nonexistent/path/xyz")
    assert rs.error is not None
    assert rs.candidates == []


def test_scan_repo_collects_multiple_signals(tmp_path):
    _write(tmp_path, "a.py", "# TODO x\n")
    _write(tmp_path, "test_a.py", "@pytest.mark.skip\ndef t(): pass\n")
    _write(tmp_path, "big.py", "l\n" * 900)
    rs = prescan.scan_repo(str(tmp_path))
    kinds = {c.kind for c in rs.candidates}
    assert "marker" in kinds
    assert "skipped_test" in kinds
    assert "large_file" in kinds
    assert rs.error is None


def test_per_repo_caps_respected(tmp_path):
    body = "".join(f"# TODO {i}\n" for i in range(30))
    _write(tmp_path, "a.py", body)
    rs = prescan.scan_repo(str(tmp_path), caps={"marker": 2, "skipped_test": 8,
                                                "churn": 6, "large_file": 5, "stale_lock": 3})
    markers = [c for c in rs.candidates if c.kind == "marker"]
    assert len(markers) == 2


# ---- global interleave cap ----------------------------------------------------

def _cand(repo, i):
    return prescan.Candidate(repo, "marker", f"f{i}.py", i, "t")


def test_interleave_cap_spreads_across_repos():
    a = [_cand("A", i) for i in range(10)]
    b = [_cand("B", i) for i in range(10)]
    capped = prescan._interleave_cap([a, b], 6)
    assert len(capped) == 6
    # round-robin: A,B,A,B,A,B → 3 each, no single repo monopolizes
    repos = [c.repo for c in capped]
    assert repos.count("A") == 3
    assert repos.count("B") == 3


def test_interleave_cap_handles_uneven():
    a = [_cand("A", i) for i in range(2)]
    b = [_cand("B", i) for i in range(10)]
    capped = prescan._interleave_cap([a, b], 8)
    assert len(capped) == 8
    # A exhausts after 2, rest come from B
    assert [c.repo for c in capped].count("A") == 2
    assert [c.repo for c in capped].count("B") == 6


def test_scan_all_applies_global_cap(tmp_path):
    r1 = tmp_path / "r1"
    r2 = tmp_path / "r2"
    for r in (r1, r2):
        _write(r, "a.py", "".join(f"# TODO {i}\n" for i in range(20)))
    capped, scans = prescan.scan_all([str(r1), str(r2)], limit_candidates=5)
    assert len(capped) == 5
    assert len(scans) == 2
