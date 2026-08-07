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
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "analyze-service-index" / "commit.sh"
HOME_NIX = ROOT / "nix" / "home.nix"

BASH = shutil.which("bash")


def _run(store, *args, **env):
    """Invoke the committer. bash is resolved to an absolute path — never via a
    shebang and never through an interpreter that may be absent in the sandbox."""
    e = dict(os.environ)
    e.update({k: str(v) for k, v in env.items()})
    assert BASH is not None, "bash not on PATH"
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
    exfiltration path for client-identifying infrastructure detail. The run
    still succeeds locally and touches nothing on the far side."""
    store = _seed(tmp_path)
    _run(store)
    far_side = tmp_path / "far-side"
    _git(store / "some-scope", "remote", "add", "origin", str(far_side))
    (store / "some-scope" / "delta.md").write_text("d\n", encoding="utf-8")
    p = _run(store)
    assert p.returncode == 0, p.stderr
    assert not far_side.exists(), "something was written to the remote path"
    refs = _git(store / "some-scope", "for-each-ref", "--format=%(refname)",
                "refs/remotes").stdout.strip()
    assert refs == "", f"remote-tracking refs appeared: {refs}"


def _script_code_lines():
    """The script with comment-only lines stripped, so a prose mention of a
    forbidden command in the rationale does not read as a call site."""
    out = []
    for line in SCRIPT.read_text(encoding="utf-8").splitlines():
        if line.lstrip().startswith("#"):
            continue
        out.append(line)
    return out


def test_the_script_contains_no_network_touching_git_subcommand():
    """Structural, not behavioural: the behavioural test above can only prove the
    paths it exercised. This pins that no push/fetch/pull/clone/remote call site
    exists at all."""
    bad = re.compile(r"\bgit\b[^|;&]*\b(push|fetch|pull|clone|ls-remote)\b")
    hits = [l for l in _script_code_lines() if bad.search(l)]
    assert not hits, f"network-touching git call site(s): {hits}"


def test_the_script_never_blind_stages():
    """claude/RULES.md 🔴 — never `git add -A` / `--all` / `.`"""
    bad = re.compile(r"git\s[^|;&]*\badd\b[^|;&]*(\s-A\b|\s--all\b|\s\.\s*$)")
    hits = [l for l in _script_code_lines() if bad.search(l)]
    assert not hits, f"blind-staging call site(s): {hits}"


def test_the_forbidden_pattern_scanners_can_actually_match(tmp_path):
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
    unversioned forever while the workbench looks healthy."""
    src = _home_nix()
    m = re.search(r"systemd\.user\.(services|timers)\.analyze-service-index-commit"
                  r"\s*=\s*(.*?)\{", src, re.S)
    assert m, "the analyze-service-index-commit unit is missing from nix/home.nix"
    for kind in ("services", "timers"):
        decl = re.search(
            rf"systemd\.user\.{kind}\.analyze-service-index-commit\s*=\s*([^{{]*)\{{",
            src)
        assert decl, f"no {kind} declaration found"
        assert "mkIf" not in decl.group(1), (
            f"the {kind} declaration is conditional: {decl.group(1)!r}")


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
