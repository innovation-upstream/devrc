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
    """
    sandbox = tmp_path / "sandbox"
    home = sandbox / "home"
    (home / "workspace" / "devrc" / "claudedocs").mkdir(parents=True)
    (home / "asi-leak-sentinel").mkdir(parents=True)
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
    p = _run(store, HOME=str(home))
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
    "add", "commit", "config", "diff", "init",
    "ls-files", "rev-parse", "status", "var",
}

_GIT_CALL = re.compile(r"\bgit\b(?P<rest>[^|;&]*)")

# `git` counts as a CALL only in command position. Without this the word `git`
# inside an error string ("git is not on PATH") parses as an invocation of a
# subcommand called `is`. Command position = start of line, or straight after a
# separator (`;` `&` `|` `(` including `$(` `&&` `||`), or after one of these
# words. Deliberately NOT `}`, so `"${fail_prefix} git status failed"` — prose
# inside a message — is rejected, while `$(git …)` inside the same message is
# still seen.
_CMD_WORDS = {"capture", "if", "then", "do", "else", "elif", "while", "until", "!"}
_CMD_CHARS = (";", "&", "|", "(", "!")


def _in_command_position(prefix: str) -> bool:
    p = prefix.rstrip()
    if not p:
        return True
    if p.endswith(_CMD_CHARS):
        return True
    return p.split()[-1] in _CMD_WORDS


def _git_subcommands_invoked():
    """Every git subcommand actually invoked, plus every INDIRECT one.

    Returns (concrete, indirect). `indirect` holds call sites whose subcommand
    comes from a variable — those are ledger violations by construction, because
    no static reading can say what `$_s` expands to.
    """
    concrete, indirect = set(), []
    for line in _script_code_lines():
        for m in _GIT_CALL.finditer(line):
            if not _in_command_position(line[:m.start()]):
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
    return concrete, indirect


def test_the_set_of_git_subcommands_is_exactly_the_asserted_ledger():
    """🔴 AN ASSERTED LEDGER, NOT A BLOCKLIST — and it fails when the set GROWS
    *or* SHRINKS.

    The previous version enumerated five forbidden verbs
    (push/fetch/pull/clone/ls-remote). A sweep built differently walked past it
    three times over: `git archive -o …`, `git bundle create …`, and a plain
    `cp -r "$scope" …` are all exfiltration and none of them is on that list.
    An allowlist has no such blind spot: anything not named here is a failure,
    whether or not anybody thought to forbid it.

    Shrinking is a failure too — a ledger that silently tracks whatever the
    script happens to do is not an assertion about anything.
    """
    concrete, indirect = _git_subcommands_invoked()
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


def test_the_ledger_extractor_sees_what_it_claims_to_see():
    """🔴 POSITIVE CONTROL for the extractor. `added == []` is a zero, and a zero
    is indistinguishable from a parser wired to nothing. Feed it lines it MUST
    classify, including the two obfuscations that survived the old blocklist."""
    global _script_code_lines
    original = _script_code_lines
    try:
        _script_code_lines = lambda: [  # noqa: E731
            '  git -C "$scope" push origin trunk',
            '  git -C "$scope" archive -o /tmp/x.tar HEAD',
            '  _s=push; git -C "$scope" $_s origin trunk',
            '  git -C "$scope" status --porcelain',
        ]
        concrete, indirect = _git_subcommands_invoked()
    finally:
        _script_code_lines = original
    assert "push" in concrete, "the extractor missed a plain `git push`"
    assert "archive" in concrete, "the extractor missed `git archive`"
    assert "status" in concrete, "the extractor missed the -C-prefixed `git status`"
    assert indirect, "the extractor did not flag a variable-supplied subcommand"


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
    """A systemd unit cannot answer git's "please tell me who you are". Point
    HOME at an empty directory so no global config is visible."""
    store = _seed(tmp_path)
    empty_home = tmp_path / "empty-home"
    empty_home.mkdir()
    p = _run(store, HOME=str(empty_home), GIT_CONFIG_GLOBAL=str(empty_home / "none"))
    assert p.returncode == 0, p.stderr
    who = _git(store / "some-scope", "log", "-1", "--format=%an <%ae>").stdout
    assert "analyze-service-index@localhost" in who


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


def test_the_timer_is_daily_and_catches_up_a_missed_run():
    src = _home_nix()
    i = src.index("systemd.user.timers.analyze-service-index-commit")
    block = src[i:i + 600]
    assert re.search(r'OnCalendar = "\*-\*-\* \d\d:\d\d:\d\d"', block)
    assert "Persistent = true" in block


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
    """
    store = _seed(tmp_path)
    assert _run(store).returncode == 0
    blocked = store / "some-scope" / "sub"
    blocked.mkdir()
    blocked.chmod(0o000)
    try:
        p = _run(store)
    finally:
        blocked.chmod(0o755)
    assert p.returncode == 0, (
        f"a clean tree with a warning on stderr was treated as a failure:\n{p.stderr}")
    assert "clean — nothing to commit" in p.stdout, (
        f"the warning was mistaken for dirty state; stdout was:\n{p.stdout}")
    assert "Permission denied" in p.stderr, (
        "the warning was swallowed entirely — it must still be surfaced, just "
        "not as dirty state")


def test_a_status_warning_never_reaches_the_commit_message(tmp_path):
    """The other half of the same defect: `before` is pasted verbatim into the
    commit body, so a warning line became part of the permanent history."""
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
    assert p.returncode == 0, p.stderr
    msg = _git(store / "some-scope", "log", "-1", "--format=%B").stdout
    assert "Permission denied" not in msg, (
        f"a stderr warning was committed into the message body:\n{msg}")
    assert "1 change(s)" in msg, (
        f"the change count was inflated by warning lines:\n{msg}")


def _race_hook(scope, body):
    """Install a pre-commit hook — a deterministic stand-in for a write landing
    in the window between `git add` and `git commit`.

    🔴 The shebang belongs to testlib.mockbin, not to this call site.
    `#!/usr/bin/env bash` works on the dev host and does NOT exist in the nix
    build sandbox, so a hook written that way makes `git commit` fail with
    "cannot exec" — the test then goes green-for-the-wrong-reason locally and
    red in the authoritative tier (MEASURED: exactly that, both ways).
    """
    hooks = scope / ".git" / "hooks"
    hooks.mkdir(parents=True, exist_ok=True)
    return write_exec(hooks / "pre-commit", f"{body}\nexit 0\n")


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
    _race_hook(scope, 'printf "raced\\n" > "$(git rev-parse --show-toplevel)/gamma.md"')
    p = _run(store)
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
    _race_hook(scope, 'printf "x\\n" > "$(git rev-parse --show-toplevel)/secret.pem"')
    p = _run(store)
    assert p.returncode != 0, (
        f"an uncovered file surviving the commit was reported as success:\n{p.stdout}")
    assert "STILL DIRTY" in p.stderr, p.stderr
    assert "secret.pem" in p.stderr, p.stderr
    assert "neither matched by the *.md allowlist nor already tracked" in p.stderr
    assert "secret.pem" not in _git(scope, "ls-files").stdout


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


def test_a_scope_reached_through_a_symlink_is_still_versioned(tmp_path):
    """🔴 M2. `find -type d` without `-L` did not enumerate a symlinked scope at
    all: "no scope directories — nothing to do", exit 0, unversioned."""
    real = tmp_path / "elsewhere" / "real-scope"
    real.mkdir(parents=True)
    (real / "alpha.md").write_text("a\n", encoding="utf-8")
    store = tmp_path / "store"
    store.mkdir()
    (store / "linked-scope").symlink_to(real, target_is_directory=True)
    p = _run(store)
    assert p.returncode == 0, p.stderr
    assert "nothing to do" not in p.stdout, (
        f"the symlinked scope was not enumerated:\n{p.stdout}")
    assert (real / ".git").is_dir(), "the symlinked scope was left unversioned"
    assert "alpha.md" in _git(real, "ls-files").stdout


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
    """🔴 The behavioural half: pin the setting AND prove it wins. Force signing
    on in a sandbox global config and confirm the commit still lands."""
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


def test_the_units_path_provides_git():
    """A user unit does not inherit the login PATH. Without git on it the script
    exits 1 by design — but every day, forever."""
    src = _home_nix()
    i = src.index("systemd.user.services.analyze-service-index-commit")
    block = src[i:i + 1200]
    m = re.search(r"PATH=\$\{lib\.makeBinPath \[([^\]]*)\]\}", block)
    assert m, "no explicit PATH on the unit"
    for pkg in ("pkgs.git", "pkgs.findutils", "pkgs.coreutils", "pkgs.bash"):
        assert pkg in m.group(1), f"{pkg} missing from the unit PATH"
