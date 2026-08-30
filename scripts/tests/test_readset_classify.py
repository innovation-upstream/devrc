"""Guards for `scripts/lib/readset_classify.py`.

🔴 EVERY CASE BELOW IS A BUG THIS CLASSIFIER ACTUALLY SHIPPED, not an imagined
one. The classifier exists to replace a regex that over-classified test files as
"repo-wide scanners" (`claudedocs/handoff-ci-speedup.md`), and its first three
drafts each reproduced that same failure in a new disguise:

  1. matched the bare tool name, so `git init -q /tmp/x` read as a repo scan;
  2. matched a read verb anywhere in argv, so a stub binary invoked as
     `/tmp/.../bw --nointeraction status` read as a repo scan;
  3. same, so `bash -c '<script mentioning git log>'` read as a repo scan —
     matching the SCRIPT TEXT, which is precisely the regex's original sin.

A classifier built to fix over-classification that over-classifies is worse than
no classifier, because its number reads as measured. These pin the acquittals.
"""

import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
_SPEC = importlib.util.spec_from_file_location(
    "readset_classify", REPO_ROOT / "scripts" / "lib" / "readset_classify.py")
rc = importlib.util.module_from_spec(_SPEC)
sys.modules["readset_classify"] = rc
_SPEC.loader.exec_module(rc)


_PSPEC = importlib.util.spec_from_file_location(
    "readset_plugin", REPO_ROOT / "scripts" / "testlib" / "readset_plugin.py")
rp = importlib.util.module_from_spec(_PSPEC)
sys.modules["readset_plugin"] = rp
_PSPEC.loader.exec_module(rp)


def _verdict(*execs):
    return rc._exec_verdict(set(execs))


# ------------------------------------------------- plugin: path attribution

def test_a_SIBLING_worktree_is_not_inside_this_repo():
    """🟡 AUDIT R1-F6. `devrc-readsets` startswith `devrc`.

    A bare prefix compare mapped /home/zach/workspace/devrc-readsets/scripts/x
    to '-readsets/scripts/x' — a phantom trigger prefix. ~45 such sibling
    worktrees exist on this box, and the corpus has tests built around them.
    """
    sibling = str(rp.REPO_ROOT) + "-readsets/scripts/x.py"
    assert rp._rel(sibling) is None, rp._rel(sibling)


def test_a_path_inside_the_repo_is_still_relative_to_it():
    """The positive control for the sibling guard — it must not over-reject."""
    inside = str(rp.REPO_ROOT) + "/scripts/lib/x.py"
    assert rp._rel(inside) == "scripts/lib/x.py"
    assert rp._rel(str(rp.REPO_ROOT)) == "."


def test_a_RELATIVE_path_read_is_not_discarded(monkeypatch):
    """🟡 AUDIT R1-F7. The `open` event carries the path AS PASSED.

    `open("scripts/x")` under a repo-root cwd used to return None and vanish —
    under-classification, the unsafe direction.
    """
    monkeypatch.chdir(rp.REPO_ROOT)
    assert rp._rel("scripts/lib/relative_read.py") == "scripts/lib/relative_read.py"


def test_os_stat_is_NOT_claimed_as_a_traced_event():
    """🔴 AUDIT R1-F2. CPython raises no audit event for stat.

    Listing it raised nothing while READING as coverage. Pinning its absence so
    nobody re-adds it believing it works.
    """
    assert "os.stat" not in rp._PATH_EVENTS
    assert "STAT-ONLY DEPENDENCIES ARE INVISIBLE" in rp.__doc__


# --------------------------------------------------------- positive controls

def test_git_ls_files_at_repo_root_IS_a_scan():
    """The one that must still fire, or every acquittal below is vacuous."""
    scans, opaque = _verdict("git ls-files\t@.")
    assert scans == ["git ls-files"], (scans, opaque)


def test_a_direct_tree_scanner_at_repo_root_IS_a_scan():
    scans, _ = _verdict("rg --files\t@.")
    assert scans == ["rg --files"]


# --------------------------------------------------------- the three bugs

def test_git_init_on_a_tmp_path_is_NOT_a_scan():
    """BUG 1: matched the tool name `git`, ignoring the subcommand.

    `init` creates a repo somewhere else; it reads nothing here.
    """
    scans, opaque = _verdict("git init -q /tmp/fixture-repo\t@<inherited>")
    assert scans == [], scans
    assert opaque == [], opaque


def test_a_read_verb_with_an_operand_OUTSIDE_the_repo_is_acquitted():
    """Reaches the OPERAND acquittal, which nothing else here does.

    🔴 Added after a mutation sweep: deleting that acquittal left all 11 other
    tests GREEN. `git init /tmp/x` never reaches it — the subcommand check
    acquits `init` first — so the guard was unreachable and the suite was
    asserting nothing about it. This uses a REAL scanner (`grep`, which would
    otherwise be scored a scan) pointed at a path outside the tree.
    """
    scans, _ = _verdict("grep -r needle /tmp/somewhere-else\t@.")
    assert scans == [], scans


def test_a_stub_binary_outside_the_repo_is_NOT_a_scan():
    """BUG 2: `status` is a git read verb, so a stub named anything matched.

    The executable lives in a tmp fixture dir — whatever it reads, it is not
    this tree, and argv[0] was excluded from the operand check.
    """
    scans, _ = _verdict("/tmp/pytest-0/bw-stub/bw --nointeraction status\t@.")
    assert scans == []


def test_a_SCANNER_at_an_absolute_path_scanning_DOT_is_a_scan():
    """🟡 AUDIT R1-F3 — THIS TEST'S ASSERTION WAS INVERTED BY THE AUDIT.

    It used to assert that an absolute-path executable outside the repo was
    ACQUITTED, and it passed, and it was wrong: where the BINARY lives says
    nothing about what it READS. `grep -r needle .` at a repo cwd scans this
    tree whether grep came from /nix/store, /usr/bin or a fixture dir — and the
    corpus really does invoke git by absolute store path. The old rule
    acquitted genuine `/nix/store/.../git ls-files` scans.
    """
    scans, _ = _verdict("/tmp/fixtures/bin/grep -r needle .\t@.")
    assert len(scans) == 1, scans


def test_a_git_WRITE_subcommand_at_repo_root_is_not_a_scan():
    """Reaches the git-subcommand check — nothing else here does.

    🔴 Mutation sweep: forcing that check to True left all 12 other tests
    green, because `git init -q /tmp/x` is acquitted by the OPERAND rule first.
    A write verb with no outside operand is the only way in.
    """
    scans, _ = _verdict("git commit -m msg\t@.")
    assert scans == [], scans


def test_bash_dash_c_is_OPAQUE_not_a_scan():
    """BUG 3: a read verb inside the SCRIPT TEXT counted as an operation.

    The correct answer is not "scan" and not "clear" — it is UNKNOWN, and it
    must land in its own bucket so nobody reads it as measured.
    """
    scans, opaque = _verdict("bash -c set -e; git log --oneline | head\t@.")
    assert scans == [], scans
    assert len(opaque) == 1, opaque


# --------------------------------------------------------- cwd / -C acquittals

def test_a_scan_outside_the_repo_is_acquitted():
    scans, _ = _verdict("git ls-files\t@<outside-repo>")
    assert scans == []


def test_dash_C_at_an_outside_path_is_acquitted_BY_THE_OPERAND_RULE():
    """Behaviour pin. The acquittal is the operand rule, NOT a `-C` rule.

    🔴 There used to be a dedicated `-C` branch and this test was written as if
    it covered it. A mutation sweep disabled that branch and this test stayed
    GREEN — because `/tmp/fixture` is an absolute operand outside the repo, so
    the operand rule had already rejected it. The branch was dead code and is
    gone; the behaviour it claimed to provide is real and still pinned here.
    """
    scans, _ = _verdict("git -C /tmp/fixture ls-files\t@.")
    assert scans == []


def test_dash_C_into_a_repo_SUBDIR_is_still_a_scan_of_this_tree():
    """The case a re-added `-C` rule must not break: it is a real scan."""
    scans, _ = _verdict("git -C scripts ls-files\t@.")
    assert scans == ["git -C scripts ls-files"], scans


# --------------------------------------------------------- bucket assignment

def test_reading_the_repo_root_makes_a_file_always_run():
    merged = {"scripts/tests/test_x.py": {"paths": {"."}, "execs": set()}}
    out = rc.classify(merged)
    assert out["scripts/tests/test_x.py"]["always_run"] is True


def test_reading_only_YOUR_OWN_FILE_is_not_a_dependency():
    """🟡 AUDIT R1-F8 — THIS TEST'S FIXTURE WAS NARROWED BY THE AUDIT.

    It used to include a sibling `helper.txt` and assert n_paths == 0, which
    passed and was wrong: excluding the whole parent directory erased every
    same-directory dependency (fixtures, the doc-path baseline). Only the
    file's own import is genuinely not a dependency.
    """
    merged = {"scripts/tests/test_x.py": {
        "paths": {"scripts/tests/test_x.py"},
        "execs": set()}}
    out = rc.classify(merged)["scripts/tests/test_x.py"]
    assert out["always_run"] is False
    assert out["opaque"] is False
    assert out["n_paths"] == 0, out["sample_paths"]


def test_an_external_read_becomes_a_trigger_prefix():
    merged = {"scripts/tests/test_x.py": {
        "paths": {"nix/pkgs/foo.nix", "scripts/lib/bar.sh"}, "execs": set()}}
    out = rc.classify(merged)["scripts/tests/test_x.py"]
    assert out["always_run"] is False
    assert set(out["triggers"]) == {"nix/pkgs", "scripts/lib"}


# ------------------------------------------- audit round 1 (PR #1120) fixes

def test_a_nested_pytest_over_a_repo_dir_is_OPAQUE_not_scoped():
    """🔴 AUDIT R1-F1. The corpus's dominant opacity shape.

    Keying OPAQUE on the literal token `-c` let a nested
    `python3 -m pytest <repo dir>` fall through BOTH branches and be reported
    as "scoped — proven bounded" with two trigger prefixes. Real: measured on
    test_hook_tests_dir_collects.py.
    """
    scans, opaque = _verdict(
        "/nix/store/x/python3 -m pytest -p no:cacheprovider scripts/claude-hooks/tests\t@.")
    assert scans == [], scans
    assert len(opaque) == 1, opaque


def test_bash_running_a_repo_SCRIPT_is_OPAQUE_not_scoped():
    """🔴 AUDIT R1-F1, second shape: `bash <repo script> <REPO_ROOT>`.

    No `-c`, so the old rule scored it clean; it is a repo-wide walk.
    """
    scans, opaque = _verdict("bash scripts/run-node-tests.sh --check-suites .\t@.")
    assert scans == [], scans
    assert len(opaque) == 1, opaque


def test_an_UNRECOGNISED_command_is_OPAQUE_never_clean():
    """🔴 AUDIT R1-F1 generalised: fall-through must be UNKNOWN, not clean."""
    scans, opaque = _verdict("some-unknown-tool --do-a-thing\t@.")
    assert scans == []
    assert len(opaque) == 1, opaque


def test_git_by_ABSOLUTE_store_path_is_still_a_scan():
    """🟡 AUDIT R1-F3. `git` lives in /nix/store here.

    Acquitting on argv[0] being an absolute path outside the repo acquitted
    real `/nix/store/.../git ls-files` scans — and the corpus does invoke git
    by absolute store path via the mockbin/gitenv machinery.
    """
    scans, _ = _verdict("/nix/store/abc-git-2.55.0/bin/git ls-files\t@.")
    assert len(scans) == 1, scans


def test_a_scan_from_a_repo_SUBDIR_cwd_is_still_a_scan():
    """🟡 AUDIT R1-F4. The untested twin of the `-C` bug, other route."""
    scans, _ = _verdict("git ls-files\t@scripts")
    assert len(scans) == 1, scans
    scans2, _ = _verdict("rg --files\t@scripts/tests")
    assert len(scans2) == 1, scans2


def test_an_operand_that_IS_the_repo_root_is_not_treated_as_outside():
    """🔴 REGRESSION INTRODUCED BY THE ROUND-1 FIX ROUND ITSELF.

    Switching the operand check to a separator-terminated prefix made
    REPO_ROOT itself fail the test — "/…/devrc" does not start with
    "/…/devrc/" — so `git -C <REPO_ROOT> ls-files` was acquitted. That is the
    most common way this corpus spells a repo scan: 14 files silently lost
    their ALWAYS-RUN verdict (22 -> 9) before this was caught by diffing the
    two classification runs.
    """
    root = str(rc.REPO_ROOT)
    scans, _ = _verdict(f"git -C {root} ls-files --error-unmatch scripts/x.py\t@.")
    assert len(scans) == 1, scans


def test_a_same_directory_fixture_is_a_dependency():
    """🟡 AUDIT R1-F8. Self-scoping must exclude the FILE, not the DIRECTORY."""
    merged = {"scripts/tests/test_x.py": {
        "paths": {"scripts/tests/test_x.py",
                  "scripts/tests/doc-path-baseline.tsv",
                  "scripts/tests/fixtures/sample.json"},
        "execs": set()}}
    out = rc.classify(merged)["scripts/tests/test_x.py"]
    assert out["n_paths"] == 2, out["sample_paths"]
    assert "scripts/tests" in out["triggers"]


def test_a_git_WRITE_verb_is_clean_but_an_UNKNOWN_verb_is_opaque():
    """The fall-through inversion must not make every git call opaque."""
    scans, opaque = _verdict("git commit -m msg\t@.")
    assert (scans, opaque) == ([], []), (scans, opaque)
    scans2, opaque2 = _verdict("git some-new-verb\t@.")
    assert scans2 == [] and len(opaque2) == 1, (scans2, opaque2)


def test_opaque_does_not_silently_become_always_run():
    """UNMEASURED must stay visible as its own bucket, per RULES.md."""
    merged = {"scripts/tests/test_x.py": {
        "paths": {"scripts/lib/a.sh"},
        "execs": {"bash -c echo hi\t@."}}}
    out = rc.classify(merged)["scripts/tests/test_x.py"]
    assert out["always_run"] is False
    assert out["opaque"] is True
