"""Deterministic pre-scan tests — marker/skip detection, file:line correctness,
per-repo capping, churn/large/lockfile signals, and global interleave cap.

All fixtures are built on a real temp directory tree (tmp_path) so the file-walk,
line-numbering, and ordering are exercised end-to-end without any network or git remote.
"""
import os
import shutil
import subprocess
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


# ---- git fixtures for fetch-before-scan --------------------------------------
# Hermetic: a bare "remote" + a clone whose working tree DRIFTS behind origin. No
# network. `origin/HEAD` is set explicitly so `_default_branch` resolves via symbolic-ref.

_HAS_GIT = shutil.which("git") is not None
requires_git = pytest.mark.skipif(not _HAS_GIT, reason="git not on PATH")


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True,
                   capture_output=True, text=True)


def _init_clone(tmp_path: Path, *, branch: str = "main") -> tuple[Path, Path]:
    """Create a bare remote + a clone with one pushed commit on `branch`. origin/HEAD is
    pointed at `branch`. Returns (clone_path, bare_path)."""
    bare = tmp_path / "remote.git"
    clone = tmp_path / "clone"
    subprocess.run(["git", "init", "--quiet", "--bare", str(bare)], check=True,
                   capture_output=True, text=True)
    subprocess.run(["git", "clone", "--quiet", str(bare), str(clone)], check=True,
                   capture_output=True, text=True)
    _git(clone, "config", "user.email", "t@t")
    _git(clone, "config", "user.name", "t")
    _write(clone, "seed.py", "x = 1  # seed\n")
    _git(clone, "add", "seed.py")
    _git(clone, "commit", "--quiet", "-m", "seed")
    _git(clone, "push", "--quiet", "origin", f"HEAD:{branch}")
    # make `branch` the local + tracked branch and point origin/HEAD at it
    _git(clone, "branch", "-M", branch)
    _git(clone, "branch", f"--set-upstream-to=origin/{branch}", branch)
    _git(clone, "remote", "set-head", "origin", branch)
    return clone, bare


def _push_drift(tmp_path: Path, bare: Path, rel: str, content: str, *,
                branch: str = "main") -> None:
    """Commit a NEW file to `origin/<branch>` that the `clone` working tree lacks — via a
    throwaway second clone — so the primary clone is genuinely 'behind'."""
    other = tmp_path / "pusher"
    # Fresh clone per call so the pusher path is a throwaway; recreate it each time.
    shutil.rmtree(other, ignore_errors=True)
    subprocess.run(["git", "clone", "--quiet", str(bare), str(other)], check=True,
                   capture_output=True, text=True)
    _git(other, "config", "user.email", "t@t")
    _git(other, "config", "user.name", "t")
    # The bare's symbolic HEAD may not point at `branch`, so base the new commit on the
    # existing `origin/<branch>` tip explicitly — otherwise we'd commit an unrelated root
    # commit and the push would be a non-fast-forward.
    _git(other, "checkout", "-B", branch, f"origin/{branch}")
    _write(other, rel, content)
    _git(other, "add", rel)
    _git(other, "commit", "--quiet", "-m", "drift commit")
    _git(other, "push", "--quiet", "origin", f"HEAD:{branch}")


# ---- fetch-before-scan: fresh-ref materialization ----------------------------

@requires_git
def test_default_branch_resolves_via_origin_head(tmp_path):
    clone, _ = _init_clone(tmp_path, branch="trunk")
    assert prescan._default_branch(clone) == "trunk"


@requires_git
def test_resolve_scan_root_fresh_ref_sees_committed_drift(tmp_path):
    # The working tree does NOT have drift.py, but origin/main does. Fresh-ref mode must
    # scan the fetched ref, so the marker in drift.py appears.
    clone, bare = _init_clone(tmp_path)
    _push_drift(tmp_path, bare, "drift.py", "# TODO drifted marker\n")
    assert not (clone / "drift.py").exists()  # working tree is genuinely behind

    scan_path, mode, cleanup = prescan.resolve_scan_root(clone, fetch=True)
    try:
        assert mode == "fresh-ref (origin/main)"
        assert Path(scan_path) != clone           # a temp worktree, not the real repo
        assert (Path(scan_path) / "drift.py").exists()
    finally:
        cleanup()
    # cleanup removed the temp worktree and left no bookkeeping behind
    assert not Path(scan_path).exists()
    wt = subprocess.run(["git", "-C", str(clone), "worktree", "list"],
                        capture_output=True, text=True).stdout
    assert str(scan_path) not in wt


@requires_git
def test_scan_repo_fresh_uses_real_repo_name_not_tempdir(tmp_path):
    # #1 correctness requirement: evidence refs carry the REAL repo basename even though
    # the scanned tree is a temp worktree with a random name.
    clone, bare = _init_clone(tmp_path)
    _push_drift(tmp_path, bare, "drift.py", "# TODO drifted marker\n")

    rs = prescan.scan_repo_fresh(str(clone), fetch=True)
    assert rs.error is None
    assert rs.mode == "fresh-ref (origin/main)"
    assert rs.repo == clone.name          # NOT the temp dir name
    markers = [c for c in rs.candidates if c.kind == "marker"]
    assert any(c.file == "drift.py" for c in markers)   # saw the committed-but-undrifted content
    for c in rs.candidates:
        assert c.repo == clone.name
        assert c.ref.startswith(f"{clone.name}/")       # every ref uses the real name
        assert "repo-cos-wt-" not in c.ref              # never the temp worktree path


@requires_git
def test_scan_repo_fresh_leaves_working_tree_untouched(tmp_path):
    clone, bare = _init_clone(tmp_path)
    _push_drift(tmp_path, bare, "drift.py", "# TODO drifted marker\n")
    head_before = subprocess.run(["git", "-C", str(clone), "rev-parse", "HEAD"],
                                 capture_output=True, text=True).stdout.strip()
    branch_before = subprocess.run(
        ["git", "-C", str(clone), "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True, text=True).stdout.strip()

    prescan.scan_repo_fresh(str(clone), fetch=True)

    # working tree still behind (drift never checked out), HEAD + branch unchanged,
    # and no leftover linked worktrees.
    assert not (clone / "drift.py").exists()
    head_after = subprocess.run(["git", "-C", str(clone), "rev-parse", "HEAD"],
                                capture_output=True, text=True).stdout.strip()
    assert head_after == head_before
    branch_after = subprocess.run(
        ["git", "-C", str(clone), "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True, text=True).stdout.strip()
    assert branch_after == branch_before
    wt = subprocess.run(["git", "-C", str(clone), "worktree", "list"],
                        capture_output=True, text=True).stdout
    assert wt.count("\n") == 1  # only the main worktree remains


@requires_git
def test_no_fetch_scans_working_tree_not_ref(tmp_path):
    # --no-fetch must skip fetch/worktree entirely → scan the working tree, so the
    # origin-only drift is NOT seen.
    clone, bare = _init_clone(tmp_path)
    _push_drift(tmp_path, bare, "drift.py", "# TODO drifted marker\n")
    _write(clone, "local.py", "# TODO local marker\n")  # only in the working tree

    scan_path, mode, cleanup = prescan.resolve_scan_root(clone, fetch=False)
    try:
        assert mode == "working-tree fallback (--no-fetch)"
        assert Path(scan_path) == clone
    finally:
        cleanup()

    rs = prescan.scan_repo_fresh(str(clone), fetch=False)
    files = {c.file for c in rs.candidates if c.kind == "marker"}
    assert "local.py" in files       # working-tree content seen
    assert "drift.py" not in files   # origin-only content NOT seen


@requires_git
def test_churn_works_against_fresh_ref_worktree(tmp_path):
    # scan_churn shells out to `git log` — a linked worktree is a real git dir, so churn
    # must still return the committed history (regression guard).
    clone, bare = _init_clone(tmp_path)
    # Push several commits touching the same file so it clears the >=3 hotspot threshold.
    for i in range(4):
        _push_drift(tmp_path, bare, "hot.py", f"# rev {i}\n")
    scan_path, mode, cleanup = prescan.resolve_scan_root(clone, fetch=True)
    try:
        assert mode == "fresh-ref (origin/main)"
        churn = prescan.scan_churn(Path(scan_path), clone.name, cap=10)
        assert any(c.file == "hot.py" for c in churn)
    finally:
        cleanup()


@requires_git
def test_stale_lock_fires_against_working_tree_mtime_in_fresh_mode(tmp_path):
    # REGRESSION GUARD: a fresh worktree stamps ALL files mtime=now, which would hide
    # stale lockfiles. scan_repo_fresh must scan stale_lock against the ORIGINAL working
    # tree (real mtimes), so a genuinely-old committed lockfile still fires.
    clone, bare = _init_clone(tmp_path)
    lock = _write(clone, "poetry.lock", "old\n")
    _git(clone, "add", "poetry.lock")
    _git(clone, "commit", "--quiet", "-m", "add lock")
    _git(clone, "push", "--quiet", "origin", "HEAD:main")
    old = time.time() - 400 * 86400
    os.utime(lock, (old, old))  # make the WORKING-TREE copy genuinely old

    rs = prescan.scan_repo_fresh(str(clone), fetch=True)
    assert rs.mode == "fresh-ref (origin/main)"
    stale = [c for c in rs.candidates if c.kind == "stale_lock"]
    assert any(c.file == "poetry.lock" for c in stale), \
        "stale_lock regressed to fresh-worktree mtime (should read the working tree)"


# ---- fetch-before-scan: fallbacks --------------------------------------------

def test_resolve_scan_root_non_git_dir_falls_back(tmp_path):
    # A plain (non-git) directory → working-tree fallback, no crash. Keeps the 214
    # existing non-git-fixture tests on the direct path.
    _write(tmp_path, "a.py", "# TODO x\n")
    scan_path, mode, cleanup = prescan.resolve_scan_root(tmp_path, fetch=True)
    try:
        assert Path(scan_path) == tmp_path
        assert mode == "working-tree fallback (not a git repo)"
    finally:
        cleanup()


def test_scan_repo_fresh_non_git_dir_scans_working_tree(tmp_path):
    _write(tmp_path, "a.py", "# TODO real marker\n")
    rs = prescan.scan_repo_fresh(str(tmp_path), fetch=True)
    assert rs.error is None
    assert rs.mode.startswith("working-tree fallback")
    assert rs.repo == tmp_path.name
    assert any(c.file == "a.py" for c in rs.candidates)


def test_scan_repo_fresh_missing_dir_sets_error():
    rs = prescan.scan_repo_fresh("/nonexistent/path/xyz")
    assert rs.error is not None
    assert rs.candidates == []


def test_scan_all_fetch_false_scans_working_tree(tmp_path):
    # non-git fixture dirs with fetch=False behave exactly like the legacy scan_all.
    r1 = tmp_path / "r1"
    r2 = tmp_path / "r2"
    for r in (r1, r2):
        _write(r, "a.py", "".join(f"# TODO {i}\n" for i in range(20)))
    capped, scans = prescan.scan_all([str(r1), str(r2)], limit_candidates=5, fetch=False)
    assert len(capped) == 5
    assert len(scans) == 2
    assert all(s.mode.startswith("working-tree fallback") for s in scans)


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


# ---- skipped JS: conditional guard vs disabled block --------------------------
# Playwright/Jest `test.skip(cond, reason)` is a RUNTIME GUARD (runs when cond is
# false, e.g. Docker present in CI) — not a disabled test. Motivated by a real false
# positive: prescan flagged 7 clawgate e2e specs guarded by
# `test.skip(!dockerAvailable(), 'full-mode specs need Docker …')` as "enable me".

def test_conditional_js_skip_helper():
    # Conditional runtime guards — first arg is a condition expression → NOT flagged.
    assert prescan._is_conditional_js_skip(
        "  test.skip(!dockerAvailable(), 'needs Docker');") is True
    assert prescan._is_conditional_js_skip(
        "test.skip(process.env.CI, 'flaky in CI');") is True
    assert prescan._is_conditional_js_skip("  it.skip(isSlow, () => {});") is True
    # Disabled test blocks — first arg is a string-literal name → conditional? no.
    assert prescan._is_conditional_js_skip("  it.skip('renders the widget', () => {})") is False
    assert prescan._is_conditional_js_skip('describe.skip("auth suite", () => {})') is False
    assert prescan._is_conditional_js_skip("test.skip(`name`, async () => {})") is False
    # Bare block modifier with no arg → disabled block, not conditional.
    assert prescan._is_conditional_js_skip("describe.skip()") is False
    # A line without a JS skip → False (helper only speaks to js.skip).
    assert prescan._is_conditional_js_skip("x = 1") is False


def test_conditional_js_skip_not_flagged(tmp_path):
    # The real clawgate case + sibling condition forms must NOT become candidates.
    _write(tmp_path, "e2e.spec.ts",
           "test('full mode', () => {\n"
           "  test.skip(!dockerAvailable(), 'full-mode specs need Docker');\n"
           "  test.skip(process.env.CI, 'slow in CI');\n"
           "  it.skip(isSlow, () => {});\n"
           "});\n")
    cands = prescan.scan_skipped_tests(tmp_path, "repo", cap=10)
    assert cands == []


def test_disabled_js_test_still_flagged(tmp_path):
    # String-literal / bare disabled forms remain CI-verifiable "enable me" candidates.
    _write(tmp_path, "widget.test.ts",
           "it.skip('renders the widget', () => {});\n"
           "describe.skip('auth suite', () => {});\n"
           "test.skip('name', async () => {});\n")
    cands = prescan.scan_skipped_tests(tmp_path, "repo", cap=10)
    lines = {c.line for c in cands}
    assert lines == {1, 2, 3}
    assert all("js.skip" in c.text for c in cands)


def test_mixed_conditional_and_disabled_js(tmp_path):
    # In one file: a conditional guard is dropped but the disabled test survives.
    _write(tmp_path, "mix.spec.ts",
           "test.skip(!dockerAvailable(), 'guard');\n"
           "it.skip('disabled thing', () => {});\n")
    cands = prescan.scan_skipped_tests(tmp_path, "repo", cap=10)
    assert [c.line for c in cands] == [2]


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
