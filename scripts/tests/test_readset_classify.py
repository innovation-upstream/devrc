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


def test_a_scanner_with_an_outside_operand_is_OVER_classified_on_purpose():
    """🔴 POLICY, and this assertion was REVERSED in round 3.

    It used to assert that an outside operand acquits the command. Deciding
    that from argv needs a parser per tool — `grep`'s first operand is a
    PATTERN, an option's value is not a path the command reads from, and four
    successive defects came out of guessing. The module's stated rule is that
    ambiguity resolves to ALWAYS-RUN, so a recognised scanner at a repo cwd is
    now scored a scan unless `-C` says otherwise. Over-classification is the
    safe direction; measured cost is 13 files moving OPAQUE -> ALWAYS-RUN and
    ZERO change to `scoped`, so the published ceiling is unaffected.
    """
    scans, _ = _verdict("grep -r needle /tmp/somewhere-else\t@.")
    assert len(scans) == 1, scans


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


def test_dash_C_at_an_outside_path_is_acquitted():
    """Behaviour pin, and its NAME has been wrong twice — a name is a claim.

    Round 1: a dedicated `-C` branch existed and this test was written as if it
    covered it; a mutation sweep disabled the branch and this stayed GREEN,
    because the OPERAND rule acquitted `/tmp/fixture` first. The branch was
    dead code and was deleted, and the name was changed to credit the operand
    rule. Round 3 then deleted the OPERAND rule (four defects came out of
    guessing scope from operands) and restored a real `-C` rule — so the name
    was wrong again, in the other direction. It now names no mechanism.
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


def test_an_interpreter_running_a_REPO_SCRIPT_on_a_tmp_arg_is_OPAQUE():
    """🔴 AUDIT R2-F1. The operand acquittal must not outrank the fall-through.

    As an early `continue` it became the ONLY escape from OPAQUE and fired on
    the corpus's commonest shape — 18,806 execs across 68 of 144 files, leaving
    15 of 56 "proven bounded" files holding an acquitted interpreter child.
    Real: test_claude_log_rotate.py runs `bash <REPO>/scripts/.../rotate.sh
    /tmp/...` and published triggers that do not include that script, so
    editing it would re-run nothing.
    """
    root = str(rc.REPO_ROOT)
    scans, opaque = _verdict(
        f"bash {root}/scripts/claude-log-rotate/rotate.sh /tmp/x/dot-claude\t@.")
    assert scans == [], scans
    assert len(opaque) == 1, opaque


def test_bash_running_a_repo_script_over_THE_REPO_is_opaque_not_scoped():
    """🔴 AUDIT R2-F1, the worst real instance: a walk of the whole repo."""
    root = str(rc.REPO_ROOT)
    scans, opaque = _verdict(f"bash /tmp/copy/run-tests.sh {root}\t@.")
    assert scans == [] and len(opaque) == 1, (scans, opaque)


def test_only_dash_C_acquits_a_RECOGNISED_command_now():
    """🔴 Round-3 policy: `-C` is the ONE acquittal; operands no longer acquit.

    The first assertion was reversed in round 3 for the reason above.
    """
    scans, opaque = _verdict("grep -r needle /tmp/elsewhere\t@.")
    assert len(scans) == 1 and opaque == [], (scans, opaque)
    scans2, _ = _verdict("git -C /tmp/fixture ls-files\t@.")
    assert scans2 == [], scans2


def test_the_clean_whitelists_are_PINNED_two_way():
    """🟡 AUDIT R3-F3. Spot-checks are not coverage for a whitelist.

    `_HARMLESS` and `_GIT_WRITERS` are sets where a WRONG MEMBER IS SILENTLY
    CLEAN. Round 2 added member spot-checks; a later sweep then showed dropping
    `stash` or `echo` still survived a full green suite — 3 of 21 and 1 of 10
    members actually covered. Pinning the exact sets, so adding or removing any
    member fails here and has to be argued for.
    """
    assert rc._HARMLESS == frozenset({
        "true", "false", "echo", "printf", "sleep", "uname",
        "id", "whoami", "hostname", "date"}), sorted(rc._HARMLESS)
    assert rc._GIT_WRITERS == frozenset({
        "init", "add", "commit", "config", "checkout", "branch", "tag",
        "push", "fetch", "clone", "remote", "reset", "rm", "mv", "stash",
        "switch", "restore", "update-ref", "symbolic-ref", "gc",
        "worktree"}), sorted(rc._GIT_WRITERS)


def test_an_option_VALUE_pointing_outside_no_longer_acquits_a_scan():
    """🔴 AUDIT R3-F1 — the same defect class, fourth occurrence.

    An option's value counted as an operand, so a genuine scan of THIS tree
    scored clean whenever any absolute outside path appeared anywhere in argv.
    All of these read this repo:
    """
    for argv in ("git ls-files --exclude-from /tmp/ex",
                 "find . -newer /tmp/stamp",
                 "grep -rf /tmp/patterns .",
                 "git grep -f /tmp/pats -- scripts",
                 "git log --oneline -- scripts /tmp/x"):
        scans, _ = _verdict(f"{argv}\t@.")
        assert len(scans) == 1, (argv, scans)


def test_dash_C_remains_the_ONE_acquittal_and_still_works():
    """The single surviving acquittal — unambiguous, unlike an operand."""
    scans, _ = _verdict("git -C /tmp/fixture ls-files\t@.")
    assert scans == [], scans
    root = str(rc.REPO_ROOT)
    scans2, _ = _verdict(f"git -C {root} ls-files\t@.")
    assert len(scans2) == 1, scans2
    scans3, _ = _verdict("git -C scripts ls-files\t@.")
    assert len(scans3) == 1, scans3


def test_a_relative_read_escaping_via_dotdot_is_not_recorded_in_repo(monkeypatch):
    """🟢 AUDIT R3-F4. `../../devrc-sibling/x` yielded prefix `scripts/..`."""
    monkeypatch.chdir(rp.REPO_ROOT / "scripts")
    assert rp._rel("../../devrc-sibling/x.py") is None
    assert rp._rel("../nix/home.nix") == "nix/home.nix"


def test_test_and_which_are_NOT_adjudicated_harmless():
    """🟡 AUDIT R2-F2. `test -f <repo path>` stats this tree.

    Both were in _HARMLESS and scored CLEAN — the module's own stat blind spot
    re-introduced through the exec path. A mutation emptying _HARMLESS survived
    the whole suite, so the set had no coverage at all.
    """
    for argv in ("test -f scripts/drift-check.sh", "which drift-check.sh"):
        scans, opaque = _verdict(f"{argv}\t@.")
        assert scans == [], (argv, scans)
        assert len(opaque) == 1, (argv, opaque)


def test_a_genuinely_harmless_command_is_still_clean():
    """Positive control for the set — emptying it must be detectable."""
    scans, opaque = _verdict("true\t@.")
    assert (scans, opaque) == ([], []), (scans, opaque)


def test_an_unknown_git_verb_is_opaque_but_a_known_writer_is_clean():
    """🟡 AUDIT R2-F2. _GIT_WRITERS had no guard; dropping a member survived."""
    for verb in ("init", "config", "worktree"):
        scans, opaque = _verdict(f"git {verb} something\t@.")
        assert (scans, opaque) == ([], []), (verb, scans, opaque)


def test_a_doubled_separator_in_a_cwd_still_reads_as_inside_the_repo():
    """🟢 AUDIT R2-F4. `<ROOT>//scripts` yielded '/scripts' -> read as absolute."""
    assert rp._rel(str(rp.REPO_ROOT) + "//scripts") == "scripts"


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
