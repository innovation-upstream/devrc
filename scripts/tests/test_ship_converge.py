"""Behavioural tests for the CONVERGE routine of scripts/ship.sh.

Everything runs against THROWAWAY git repos built in tmp_path. Nothing here
touches ~/workspace/devrc, the real hosts, or `home-manager switch`
(SHIP_NO_SWITCH=1 short-circuits the switch), and `--no-remote` means no SSH
and no skills rsync are attempted. Hermetic: git + bash only, with
GIT_CONFIG_GLOBAL/SYSTEM redirected so the host's real git config (which sets
rebase.autoStash=true) cannot influence the outcome.

THE INVARIANT UNDER TEST — ship.sh must never `git stash`.
The stash stack is repo-GLOBAL (shared by every worktree of a repo), so the old
stash/pop dance reached outside the checkout it was converging: on 2026-07-30 it
stashed another worktree's in-flight work, could not pop it back, and left the
host un-switched with `DU` conflicts. The replacement is `git merge --ff-only`,
which cannot conflict and cannot autostash: it either advances cleanly or
REFUSES, and a refusal must SKIP that host untouched.

Every scenario therefore asserts `git stash list` is empty afterwards, and
assert_no_stash_created() snapshots it before/after to prove the count never
moved. `test_ship_source_never_stashes` additionally greps the script itself.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

SHIP = Path(__file__).resolve().parents[1] / "ship.sh"

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None or shutil.which("bash") is None,
    reason="needs git + bash on PATH",
)


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def git(repo, *args, check=True):
    """Run a git command against `repo` and return stdout."""
    out = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
    )
    if check and out.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {out.stderr}")
    return out.stdout.strip()


class Repo:
    """A throwaway origin + working clone, with origin/main one commit ahead.

    Layout of the seeded `main`:
      f            — base content; the AHEAD commit MODIFIES it  (overlapping)
      stable.txt   — the ahead commit never touches it           (non-overlapping)
    and the ahead commit ADDS:
      added-upstream.txt                                         (overlapping)
    """

    def __init__(self, tmp_path, gitconfig_extra=""):
        self.root = tmp_path
        self.origin = tmp_path / "origin.git"
        self.work = tmp_path / "work"
        self.home = tmp_path / "home"
        self.home.mkdir()

        # Isolated global git config — the host's real one must not leak in.
        self.gitconfig = tmp_path / "gitconfig"
        self.gitconfig.write_text(
            "[user]\n\tname = t\n\temail = t@t\n"
            "[init]\n\tdefaultBranch = main\n" + gitconfig_extra
        )

        subprocess.run(
            ["git", "init", "-q", "--bare", "-b", "main", str(self.origin)],
            check=True, env=self.env(),
        )

        builder = tmp_path / "builder"
        subprocess.run(
            ["git", "clone", "-q", str(self.origin), str(builder)],
            check=True, env=self.env(),
        )
        (builder / "f").write_text("base\n")
        (builder / "stable.txt").write_text("stable\n")
        self._git(builder, "checkout", "-q", "-B", "main")
        self._git(builder, "add", "f", "stable.txt")
        self._git(builder, "commit", "-q", "-m", "base")
        self._git(builder, "push", "-q", "-u", "origin", "main")

        # Working clone pinned at the base commit...
        subprocess.run(
            ["git", "clone", "-q", str(self.origin), str(self.work)],
            check=True, env=self.env(),
        )
        self._git(self.work, "checkout", "-q", "main")

        # ...then origin/main advances (work is now exactly 1 behind).
        (builder / "f").write_text("base\nupstream\n")
        (builder / "added-upstream.txt").write_text("from upstream\n")
        self._git(builder, "add", "f", "added-upstream.txt")
        self._git(builder, "commit", "-q", "-m", "ahead")
        self._git(builder, "push", "-q", "origin", "main")

    def env(self, **extra):
        e = dict(os.environ)
        e.update(
            HOME=str(self.home),
            GIT_CONFIG_GLOBAL=str(self.gitconfig),
            GIT_CONFIG_SYSTEM="/dev/null",
            GIT_TERMINAL_PROMPT="0",
        )
        e.update(extra)
        return e

    def _git(self, repo, *args):
        out = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True, text=True, env=self.env(),
        )
        assert out.returncode == 0, f"setup git {args} failed: {out.stderr}"
        return out.stdout.strip()

    # -- state accessors ---------------------------------------------------- #
    def branch(self):
        return self._git(self.work, "symbolic-ref", "--quiet", "--short", "HEAD") or "DETACHED"

    def head(self):
        return self._git(self.work, "rev-parse", "HEAD")

    def origin_main(self):
        self._git(self.work, "fetch", "origin", "-q")
        return self._git(self.work, "rev-parse", "origin/main")

    def stash_list(self):
        return self._git(self.work, "stash", "list")

    def ship(self, *args):
        """Run ship.sh against this repo: local only, no home-manager switch."""
        env = self.env(
            SHIP_ROLE="workbench",     # bypass IP detection (no `ip` in sandbox)
            SHIP_REPO=str(self.work),
            SHIP_NO_SWITCH="1",        # never run a real home-manager switch
        )
        proc = subprocess.run(
            ["bash", str(SHIP), "--no-remote", *args],
            capture_output=True, text=True, env=env,
        )
        return proc.returncode, proc.stdout + proc.stderr


@pytest.fixture
def repo(tmp_path):
    return Repo(tmp_path)


def assert_converged(r, out):
    assert r.branch() == "main", f"not on main: {r.branch()}\n{out}"
    assert r.head() == r.origin_main(), f"HEAD != origin/main\n{out}"


def assert_no_stash_created(r, before, out):
    """The load-bearing assertion: the stash stack must be byte-identical."""
    assert r.stash_list() == before, (
        f"ship.sh touched the stash stack (repo-GLOBAL!)\n"
        f"before={before!r} after={r.stash_list()!r}\n{out}"
    )
    assert r.stash_list() == "", f"stash entry left behind\n{out}"


# --------------------------------------------------------------------------- #
# 1. converges a CLEAN tree
# --------------------------------------------------------------------------- #
def test_converges_clean_tree_on_main(repo):
    before = repo.stash_list()
    rc, out = repo.ship()
    assert rc == 0, out
    assert_converged(repo, out)
    assert_no_stash_created(repo, before, out)


def test_converges_clean_feature_branch_that_is_an_ancestor(repo):
    """On a feature branch whose tip is an ancestor of origin/main -> land on main."""
    repo._git(repo.work, "checkout", "-q", "-b", "feat/ancestor")
    before = repo.stash_list()
    rc, out = repo.ship()
    assert rc == 0, out
    assert_converged(repo, out)
    assert_no_stash_created(repo, before, out)


# --------------------------------------------------------------------------- #
# 2. converges a DIRTY tree whose changes do NOT overlap the incoming commits
# --------------------------------------------------------------------------- #
def test_converges_dirty_tree_not_overlapping_incoming(repo):
    """The 2026-07-30 regression case, done right: dirty but non-conflicting.

    stable.txt is modified locally and untouched upstream; newfile is untracked
    and unknown upstream. Both must survive, and the host must still converge.
    """
    (repo.work / "stable.txt").write_text("stable\nlocal edit\n")
    (repo.work / "newfile").write_text("untracked content\n")
    before = repo.stash_list()

    rc, out = repo.ship()

    assert rc == 0, out
    assert_converged(repo, out)
    assert_no_stash_created(repo, before, out)
    # WIP preserved in place — never stashed, never popped, never lost.
    assert (repo.work / "stable.txt").read_text() == "stable\nlocal edit\n"
    assert (repo.work / "newfile").read_text() == "untracked content\n"
    # ...and the incoming commit did land.
    assert (repo.work / "added-upstream.txt").exists()


def test_converges_dirty_tree_on_feature_branch(repo):
    """Dirty + on a feature branch: still lands on main with WIP intact."""
    repo._git(repo.work, "checkout", "-q", "-b", "feat/wip")
    (repo.work / "stable.txt").write_text("stable\nwip\n")
    (repo.work / "untracked-wip").write_text("wip\n")
    before = repo.stash_list()

    rc, out = repo.ship()

    assert rc == 0, out
    assert_converged(repo, out)
    assert_no_stash_created(repo, before, out)
    assert (repo.work / "stable.txt").read_text() == "stable\nwip\n"
    assert (repo.work / "untracked-wip").read_text() == "wip\n"


# --------------------------------------------------------------------------- #
# 3. SKIPS (no stash, no clobber) when a local change would be overwritten
# --------------------------------------------------------------------------- #
def test_skips_when_tracked_local_change_would_be_overwritten(repo):
    """`f` is modified locally AND modified by the incoming commit -> rc7 skip."""
    (repo.work / "f").write_text("base\nMY PRECIOUS LOCAL WORK\n")
    head_before = repo.head()
    before = repo.stash_list()

    rc, out = repo.ship()

    assert rc == 7, f"expected rc7 (cannot fast-forward), got {rc}\n{out}"
    assert_no_stash_created(repo, before, out)
    # Tree left EXACTLY as found: no clobber, no advance.
    assert (repo.work / "f").read_text() == "base\nMY PRECIOUS LOCAL WORK\n"
    assert repo.head() == head_before, "ship advanced HEAD despite skipping"
    assert not (repo.work / "added-upstream.txt").exists()
    # Message is actionable: names the blocking file + says it will not stash.
    assert "SKIPPED" in out
    assert "- f" in out, f"blocking file not named\n{out}"
    assert "never stashes" in out


def test_skips_when_untracked_file_would_be_overwritten(repo):
    """An untracked file colliding with an upstream-ADDED file -> rc7 skip."""
    (repo.work / "added-upstream.txt").write_text("my local untracked version\n")
    before = repo.stash_list()

    rc, out = repo.ship()

    assert rc == 7, f"expected rc7, got {rc}\n{out}"
    assert_no_stash_created(repo, before, out)
    assert (repo.work / "added-upstream.txt").read_text() == "my local untracked version\n"
    assert "added-upstream.txt" in out


def test_skips_when_checkout_to_main_is_blocked(repo):
    """Cannot even reach main (dirty file differs across branches) -> rc7 skip."""
    repo._git(repo.work, "checkout", "-q", "-b", "feat/diverging-file")
    (repo.work / "stable.txt").write_text("stable\ncommitted on feat\n")
    repo._git(repo.work, "commit", "-q", "-am", "feat changes stable.txt")
    (repo.work / "stable.txt").write_text("stable\ncommitted on feat\nuncommitted\n")
    before = repo.stash_list()

    rc, out = repo.ship()

    assert rc == 7, f"expected rc7, got {rc}\n{out}"
    assert_no_stash_created(repo, before, out)
    assert repo.branch() == "feat/diverging-file", "ship moved off the feature branch"
    assert (repo.work / "stable.txt").read_text().endswith("uncommitted\n")


def test_skips_when_local_main_diverged(repo):
    """Un-pushed commits on main -> rc8, never auto-rebased, nothing stashed."""
    (repo.work / "local.txt").write_text("local only\n")
    repo._git(repo.work, "add", "local.txt")
    repo._git(repo.work, "commit", "-q", "-m", "local divergent commit")
    (repo.work / "stable.txt").write_text("stable\nwip\n")
    before = repo.stash_list()

    rc, out = repo.ship()

    assert rc == 8, f"expected rc8 (diverged), got {rc}\n{out}"
    assert_no_stash_created(repo, before, out)
    assert repo.branch() == "main"
    assert "local divergent commit" in repo._git(repo.work, "log", "-1", "--format=%s")
    assert (repo.work / "stable.txt").read_text() == "stable\nwip\n"


# --------------------------------------------------------------------------- #
# 4. never creates a stash entry — even when git is CONFIGURED to autostash
# --------------------------------------------------------------------------- #
def test_never_autostashes_even_when_git_config_enables_it(tmp_path):
    """merge.autoStash=true globally must NOT let an autostash into this path.

    The host's real git config sets rebase.autoStash=true (nix/programs/git), so
    the merge equivalent is one config line away from silently reintroducing the
    exact bug. ship.sh forces `-c merge.autoStash=false`.
    """
    r = Repo(tmp_path, gitconfig_extra="[merge]\n\tautoStash = true\n")
    (r.work / "f").write_text("base\nlocal work\n")
    before = r.stash_list()

    rc, out = r.ship()

    assert rc == 7, f"autostash smuggled a merge through: rc={rc}\n{out}"
    assert_no_stash_created(r, before, out)
    assert (r.work / "f").read_text() == "base\nlocal work\n"


def test_idempotent_when_already_converged(repo):
    """Safe + no-op on a second run."""
    before = repo.stash_list()
    rc1, out1 = repo.ship()
    assert rc1 == 0, out1
    rc2, out2 = repo.ship()
    assert rc2 == 0, out2
    assert "already at origin/main" in out2
    assert_converged(repo, out2)
    assert_no_stash_created(repo, before, out1 + out2)


def test_ship_source_never_stashes():
    """Static guard: the forbidden primitives must not reappear as CODE.

    Comment lines are excluded — the header deliberately *documents* the ban and
    the incident behind it, so a naive whole-file grep would flag its own
    warning label. Only executable lines are checked.
    """
    code = [
        ln for ln in SHIP.read_text().splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    ]
    for i, line in enumerate(code, 1):
        for forbidden in ("git stash", "stash push", "stash pop", "--autostash", "reset --hard"):
            assert forbidden not in line, (
                f"ship.sh must never use {forbidden!r} (code line {i}): {line.strip()}"
            )
    # ...and the safe primitive must still be the one doing the work.
    src = SHIP.read_text()
    assert "merge --ff-only" in src
    assert "merge.autoStash=false" in src
