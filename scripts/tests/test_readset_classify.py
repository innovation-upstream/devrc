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


def _verdict(*execs):
    return rc._exec_verdict(set(execs))


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


def test_a_SCANNER_binary_living_outside_the_repo_is_acquitted():
    """Reaches the argv[0] acquittal — nothing else here does.

    🔴 Mutation sweep: deleting that guard left all 12 other tests green. The
    stub-binary test above never reaches it, because `bw` is not a recognised
    scanner and is dropped one branch earlier. This uses a basename that IS a
    scanner (`grep`) at a path outside the tree, with an operand that would
    otherwise pass, so argv[0] is the only thing left that can acquit it.
    """
    scans, _ = _verdict("/tmp/fixtures/bin/grep -r needle .\t@.")
    assert scans == [], scans


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


def test_own_directory_reads_do_not_count_as_a_dependency():
    """Importing yourself is not a dependency on the rest of the tree."""
    merged = {"scripts/tests/test_x.py": {
        "paths": {"scripts/tests/test_x.py", "scripts/tests/helper.txt"},
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


def test_opaque_does_not_silently_become_always_run():
    """UNMEASURED must stay visible as its own bucket, per RULES.md."""
    merged = {"scripts/tests/test_x.py": {
        "paths": {"scripts/lib/a.sh"},
        "execs": {"bash -c echo hi\t@."}}}
    out = rc.classify(merged)["scripts/tests/test_x.py"]
    assert out["always_run"] is False
    assert out["opaque"] is True
