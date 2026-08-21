"""Behavioural tests for the CONVERGE routine of scripts/ship.sh.

Everything runs against THROWAWAY git repos built in tmp_path. Nothing here
touches ~/workspace/devrc, the real hosts, or `home-manager switch`
(SHIP_NO_SWITCH=1 short-circuits the switch), and `--no-remote` means no SSH is
attempted. Hermetic: git + bash only, with
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
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
SHIP = SCRIPTS / "ship.sh"

sys.path.insert(0, str(SCRIPTS))

# 🔴 mockbin owns the shebang. A stub written with `#!/usr/bin/env bash` execs
# on a NixOS dev host and ENOENTs in the nix build sandbox (no /usr/bin/env) —
# the two-tier hazard, and it bit this file: the `find` shims below were written
# that way, went green locally, and turned up as 3 sandbox failures that each
# pointed at the wrong guard. See scripts/testlib/mockbin.py.
from testlib.mockbin import write_exec  # noqa: E402

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

    The throwaway $HOME additionally carries a FABRICATED home-manager
    generation (see _seed_home_manager_generation) so the post-switch
    consumer check has something real to walk. Without it every run would hit
    the "cannot locate the manifest" branch and no test could tell a working
    check from one wired to nothing.
    """

    # Home-relative paths the fabricated generation "manages", mapped to the
    # repo file each one is deployed FROM — `None` meaning home-manager RENDERS
    # it (the real ~/.config/opencode/AGENTS.md is `home.file….text`), so its
    # bytes are not any repo file's and the currency check must exclude it
    # rather than call it stale forever.
    #
    # Deliberately spans five different `home.file` families — a top-level file,
    # a recursive skill dir, a hook, a rendered top-level opencode mirror, and
    # the recursive opencode skill mirror — so both checks are exercised
    # structurally rather than against one spelling. Two entries share ONE repo
    # source, which is real (`.config/opencode/skills` mirrors `claude/skills`)
    # and keeps a content-keyed check honest about many-to-one. (Fabricated
    # under tmp_path; chosen to mirror the real families in nix/home.nix.
    # `.claude/commands/` used to stand in for the recursive-dir shape and was
    # dropped when that family was retired — see CLAUDE.md.)
    MANAGED_SOURCES = {
        ".claude/RULES.md": "claude/RULES.md",
        ".claude/skills/bar/SKILL.md": "claude/skills/bar/SKILL.md",
        ".claude/hooks/bash-guard.py": "scripts/claude-hooks/bash-guard.py",
        ".config/opencode/AGENTS.md": None,
        ".config/opencode/skills/bar/SKILL.md": "claude/skills/bar/SKILL.md",
    }

    # `mkOutOfStoreSymlink` targets: the deployed link resolves BACK INTO the
    # repo working tree (really: the browser + dl-router skills and the
    # close-the-loop ledger). Comparing one of these against the repo source is
    # vacuously true — it is the SAME FILE — so the currency check must exclude
    # them from its evidence count instead of padding it with checks that cannot
    # fail. They still count for the RESOLUTION check, which they can fail.
    OUT_OF_STORE = {
        ".claude/skills/browser/SKILL.md": "scripts/browser-bridge/SKILL.md",
    }

    MANAGED = tuple(MANAGED_SOURCES) + tuple(OUT_OF_STORE)

    # How many managed entries the currency check can actually judge: store
    # copies whose bytes came from a repo file. Not len(MANAGED) — that is the
    # point of the two exclusions above.
    REPO_SOURCED = sum(1 for s in MANAGED_SOURCES.values() if s is not None)

    # Every repo path some managed entry is deployed from, plus the version the
    # base commit and the ahead commit give it. `_src_text` is the ONLY writer,
    # so the fabricated generation and the git history cannot drift apart.
    SOURCE_PATHS = sorted(
        {s for s in MANAGED_SOURCES.values() if s} | set(OUT_OF_STORE.values())
    )

    @staticmethod
    def _src_text(src, version):
        return f"{src}\nversion {version}\n"

    # The store path a cross-host copy leaves behind: a well-formed link into
    # ANOTHER host's home-manager closure, absent on the host doing the check.
    # This is the real 2026-08-10 failure shape, not a textbook fixture — the
    # laptop's $HOME/.claude/skills/* pointed at a `-home-manager-files` store
    # path belonging to the WORKBENCH after ship.sh rsynced them over.
    #
    # 🔴 The hash is deliberately NOT the one observed in the incident. That one
    # is the workbench's own, so it EXISTS on the machine that runs this suite —
    # every dangling case silently resolved and four tests passed while asserting
    # nothing (measured 2026-08-11: "5 checked, 0 dangling"). A test whose bad
    # case is not actually bad is the same vacuous green this check exists to
    # kill, so _assert_foreign_store_is_absent() pins it.
    FOREIGN_STORE = "/nix/store/zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz-home-manager-files"

    def __init__(self, tmp_path, gitconfig_extra="", stale_managed=(), phantom_managed=False):
        """`stale_managed` seeds those entries from the BASE commit's bytes.

        That is the real staleness shape, not a synthetic one: the deployed
        content is a genuine former version of the repo file, still reachable in
        the working clone's object store (it was cloned at that commit), while
        the working tree has been fast-forwarded past it. `phantom_managed`
        instead seeds EVERY entry with bytes that were never committed at all —
        the shape in which nothing is repo-sourced and the check must refuse to
        report a green.
        """
        self.root = tmp_path
        self.origin = tmp_path / "origin.git"
        self.work = tmp_path / "work"
        self.home = tmp_path / "home"
        self.home.mkdir()
        self.stale_managed = set(stale_managed)
        self.phantom_managed = phantom_managed
        unknown = self.stale_managed - set(self.MANAGED_SOURCES)
        assert not unknown, f"stale_managed names non-managed paths: {unknown}"

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
        self._write_sources(builder, 1)
        self._git(builder, "checkout", "-q", "-B", "main")
        self._git(builder, "add", "f", "stable.txt", *self.SOURCE_PATHS)
        self._git(builder, "commit", "-q", "-m", "base")
        self._git(builder, "push", "-q", "-u", "origin", "main")

        # Working clone pinned at the base commit...
        subprocess.run(
            ["git", "clone", "-q", str(self.origin), str(self.work)],
            check=True, env=self.env(),
        )
        self._git(self.work, "checkout", "-q", "main")

        # ...then origin/main advances (work is now exactly 1 behind). The
        # ahead commit REWRITES every managed source, so version 1 becomes a
        # historical blob and version 2 is what a converged host must serve.
        (builder / "f").write_text("base\nupstream\n")
        (builder / "added-upstream.txt").write_text("from upstream\n")
        self._write_sources(builder, 2)
        self._git(builder, "add", "f", "added-upstream.txt", *self.SOURCE_PATHS)
        self._git(builder, "commit", "-q", "-m", "ahead")
        self._git(builder, "push", "-q", "origin", "main")

        self._seed_home_manager_generation()

    def _write_sources(self, tree, version):
        for src in self.SOURCE_PATHS:
            p = tree / src
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(self._src_text(src, version))

    # -- fabricated home-manager generation --------------------------------- #
    def _seed_home_manager_generation(self):
        """Reproduce a real host's home-manager layout inside the fake $HOME.

        Faithful to what `home-manager switch` actually leaves on disk, because
        the check navigates every hop:

            $HOME/.local/state/home-manager/gcroots/current-home
                                            -> <gen>/            (symlink)
            <gen>/home-files                -> <hmfiles>/        (symlink)
            <hmfiles>/<rel>                 -> <content>/<flat>  (symlink)
            $HOME/<rel>                     -> <hmfiles>/<rel>   (symlink)

        `home-files` being a SYMLINK is the load-bearing detail: a bare
        `find <gen>/home-files` (no trailing slash, no -L) does not descend a
        symlinked start point and yields ZERO entries — a vacuous green from an
        otherwise-correct check.
        """
        store = self.root / "nixstore"
        self.hmfiles = store / "aaaaaaaa-home-manager-files"
        self.gen = store / "bbbbbbbb-home-manager-generation"
        content = store / "content"
        content.mkdir(parents=True)
        self.gen.mkdir(parents=True)

        for rel, src in self.MANAGED_SOURCES.items():
            blob = content / rel.replace("/", "_")
            if src is None or self.phantom_managed:
                # Rendered by home-manager itself (or, under phantom_managed,
                # deliberately unknown to git): these bytes are no repo file's.
                blob.write_text(f"rendered content for {rel}\n")
            else:
                blob.write_text(self._src_text(src, 1 if rel in self.stale_managed else 2))
            for base in (self.hmfiles, self.home):
                (base / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.hmfiles / rel).symlink_to(blob)
            (self.home / rel).symlink_to(self.hmfiles / rel)

        # mkOutOfStoreSymlink: the manifest entry points OUT of the store, at the
        # repo working tree, so the deployed path and the repo source are one
        # file. It resolves (rc 12 can judge it) and can never be stale (rc 13
        # must not count it).
        for rel, src in self.OUT_OF_STORE.items():
            for base in (self.hmfiles, self.home):
                (base / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.hmfiles / rel).symlink_to(self.work / src)
            (self.home / rel).symlink_to(self.hmfiles / rel)

        (self.gen / "home-files").symlink_to(self.hmfiles)
        gcroots = self.home / ".local" / "state" / "home-manager" / "gcroots"
        gcroots.mkdir(parents=True)
        (gcroots / "current-home").symlink_to(self.gen)

        # --- UNMANAGED content that must NOT be flagged --------------------- #
        # `~/.claude/skills/clickup/` is a standalone git checkout living INSIDE
        # a home-manager-managed directory, and its node_modules is full of pnpm
        # symlinks. Any check that walks $HOME instead of the manifest trips on
        # these; the manifest never mentions them, so a correct check cannot.
        pnpm = self.home / ".claude" / "skills" / "clickup" / "node_modules" / ".pnpm"
        pnpm.mkdir(parents=True)
        (pnpm / "dangles").symlink_to("../../nowhere/pkg")          # broken, on purpose
        (self.home / ".claude" / "skills" / "clickup" / "SKILL.md").write_text("unmanaged\n")
        # ...and a plain broken symlink sitting directly among managed files.
        (self.home / ".claude" / "settings.local.json.bak").symlink_to("/nonexistent/nope")

    def break_managed_symlink(self, rel):
        """Repoint a managed path at ANOTHER host's store — the real failure."""
        assert not Path(self.FOREIGN_STORE).exists(), (
            f"{self.FOREIGN_STORE} exists on this machine, so the 'broken' link "
            f"resolves and the negative control asserts nothing. Pick a hash "
            f"that is not in this host's /nix/store."
        )
        p = self.home / rel
        p.unlink()
        p.symlink_to(f"{self.FOREIGN_STORE}/{rel}")
        assert not p.exists() and p.is_symlink(), "fixture did not produce a dangling link"

    def delete_managed_path(self, rel):
        (self.home / rel).unlink()

    def drop_home_manager_generation(self):
        (self.home / ".local" / "state" / "home-manager" / "gcroots" / "current-home").unlink()

    def use_legacy_manifest_location(self):
        """Move the generation to the OTHER path home-manager has used."""
        self.drop_home_manager_generation()
        profiles = self.home / ".local" / "state" / "nix" / "profiles"
        profiles.mkdir(parents=True, exist_ok=True)
        (profiles / "home-manager").symlink_to(self.gen)

    def env(self, **extra):
        e = dict(os.environ)
        e.update(
            HOME=str(self.home),
            GIT_CONFIG_GLOBAL=str(self.gitconfig),
            GIT_CONFIG_SYSTEM="/dev/null",
            GIT_TERMINAL_PROMPT="0",
        )
        # The consumer check derives the state dir from $XDG_STATE_HOME, falling
        # back to $HOME/.local/state. Both real hosts leave it UNSET (measured),
        # so drop any ambient value: the fallback is the path under test, and an
        # inherited one would point the check outside the throwaway $HOME.
        e.pop("XDG_STATE_HOME", None)
        e.update(extra)
        return e

    def _git(self, repo, *args):
        out = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True, text=True, env=self.env(),
        )
        assert out.returncode == 0, f"setup git {args} failed: {out.stderr}"
        return out.stdout.strip()

    def _git_allow_fail(self, repo, *args):
        """For setup steps expected to fail, e.g. producing a merge conflict."""
        return subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True, text=True, env=self.env(),
        )

    def push_upstream_rename(self, src, dst):
        """Push a further origin/main commit that RENAMES src -> dst."""
        builder = self.root / "builder"
        self._git(builder, "pull", "-q", "--ff-only", "origin", "main")
        self._git(builder, "mv", src, dst)
        self._git(builder, "commit", "-q", "-m", f"rename {src} -> {dst}")
        self._git(builder, "push", "-q", "origin", "main")

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

    def ship(self, *args, **env_extra):
        """Run ship.sh against this repo: local only, no home-manager switch."""
        env = self.env(
            SHIP_ROLE="workbench",     # bypass IP detection (no `ip` in sandbox)
            SHIP_REPO=str(self.work),
            SHIP_NO_SWITCH="1",        # never run a real home-manager switch
            **env_extra,
        )
        proc = subprocess.run(
            ["bash", str(SHIP), "--no-remote", *args],
            capture_output=True, text=True, env=env,
        )
        return proc.returncode, proc.stdout + proc.stderr


@pytest.fixture
def repo(tmp_path):
    return Repo(tmp_path)


@pytest.fixture
def repo_stale(tmp_path):
    """A host that RESOLVES perfectly while serving one file's OLD bytes.

    The 2026-08-19 workbench, reduced: nothing is dangling, nothing is absent,
    the git state converges — and ~/.claude/RULES.md is the previous version.
    """
    return Repo(tmp_path, stale_managed=[".claude/RULES.md"])


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


def test_refuses_conflicted_mid_merge_tree_at_target(repo):
    """🔴 A conflicted mid-merge tree must NEVER reach `home-manager switch`.

    The dangerous shape: HEAD is ALREADY at origin/main, so the fast-forward is
    short-circuited and nothing in the merge path runs — yet MERGE_HEAD and
    unmerged entries are present. `home-manager switch --flake` builds from the
    WORKING TREE, not the commit, so conflict markers in any managed non-nix
    file (claude/RULES.md, claude/skills/**, hooks, scripts/*) would be
    DEPLOYED TO BOTH HOSTS and then reported as VERIFIED.
    """
    # Land at origin/main, then create a conflicting side branch and merge it.
    repo._git(repo.work, "fetch", "origin", "-q")   # work was cloned before the ahead commit
    repo._git(repo.work, "merge", "--ff-only", "-q", "origin/main")
    at_target = repo.head()
    repo._git(repo.work, "checkout", "-q", "-b", "side", "HEAD~1")
    (repo.work / "f").write_text("base\nside branch version\n")
    repo._git(repo.work, "commit", "-q", "-am", "side edits f")
    repo._git(repo.work, "checkout", "-q", "main")
    conflict = repo._git_allow_fail(repo.work, "merge", "side")
    assert conflict.returncode != 0, "setup should have produced a conflict"

    # Preconditions: mid-merge, conflicted, and sitting exactly at origin/main.
    assert (repo.work / ".git" / "MERGE_HEAD").exists()
    assert repo.head() == at_target == repo.origin_main()
    assert "<<<<<<<" in (repo.work / "f").read_text()
    before = repo.stash_list()

    rc, out = repo.ship()

    assert rc == 5, f"expected rc5 (conflicted tree), got {rc}\n{out}"
    # It must not have reached step 3 at all. With SHIP_NO_SWITCH=1 the switch
    # step announces itself, so its ABSENCE proves we exited before it.
    assert "SHIP_NO_SWITCH" not in out, f"reached the switch step\n{out}"
    assert "VERIFIED" not in out, f"reported success on a conflicted tree\n{out}"
    assert "unresolved merge" in out
    # Tree untouched: still mid-merge, markers intact.
    assert (repo.work / ".git" / "MERGE_HEAD").exists()
    assert "<<<<<<<" in (repo.work / "f").read_text()
    assert_no_stash_created(repo, before, out)


def test_refuses_conflicted_tree_when_also_behind(repo):
    """Same guard on the non-short-circuit path (HEAD behind origin/main)."""
    repo._git(repo.work, "checkout", "-q", "-b", "side")
    (repo.work / "stable.txt").write_text("stable\nside\n")
    repo._git(repo.work, "commit", "-q", "-am", "side")
    repo._git(repo.work, "checkout", "-q", "main")
    (repo.work / "stable.txt").write_text("stable\nmain\n")
    repo._git(repo.work, "commit", "-q", "-am", "main edit")
    assert repo._git_allow_fail(repo.work, "merge", "side").returncode != 0
    before = repo.stash_list()

    rc, out = repo.ship()

    assert rc == 5, f"expected rc5, got {rc}\n{out}"
    assert "SHIP_NO_SWITCH" not in out and "VERIFIED" not in out
    assert_no_stash_created(repo, before, out)


def test_blocking_files_named_when_upstream_renamed_the_file(repo):
    """Rename detection must not hide the blocker (message must not be empty).

    Uses stable.txt, which the ahead-commit never touches, so the rename is a
    100%-similarity R — exactly the case where `git diff --name-only` collapses
    the pair to the DESTINATION only and the source (the file the user actually
    edited) vanishes from the intersection. Renaming a file that also changed
    content would score below the rename threshold and pass either way, which
    is why this test is pinned to a pure rename.
    """
    repo.push_upstream_rename("stable.txt", "renamed-stable.txt")
    (repo.work / "stable.txt").write_text("stable\nmy local edit\n")
    before = repo.stash_list()

    rc, out = repo.ship()

    assert rc == 7, f"expected rc7, got {rc}\n{out}"
    assert "blocking files" in out, f"blocking-files section missing\n{out}"
    assert "- stable.txt" in out, f"renamed-away file not named as a blocker\n{out}"
    assert (repo.work / "stable.txt").read_text() == "stable\nmy local edit\n"
    assert_no_stash_created(repo, before, out)


def test_warns_when_gitignored_file_is_overwritten(repo):
    """Ignored files are unprotected by git — we must at least say so."""
    # Untracked .gitignore is enough — exclude rules apply whether or not the
    # ignore file itself is committed, and this keeps main un-diverged.
    (repo.work / ".gitignore").write_text("added-upstream.txt\n")
    (repo.work / "added-upstream.txt").write_text("my ignored local file\n")
    before = repo.stash_list()

    rc, out = repo.ship()

    assert rc == 0, out
    assert "WARNING" in out and "added-upstream.txt" in out, (
        f"silent clobber of an ignored file\n{out}"
    )
    # The clobber genuinely happens — the warning is the whole mitigation.
    assert (repo.work / "added-upstream.txt").read_text() == "from upstream\n"
    assert_no_stash_created(repo, before, out)


def test_reports_missing_origin_main_as_config_error_not_divergence(repo):
    """A missing origin/main must not be misreported as 'diverged'."""
    # Rename the branch on ORIGIN so `git fetch` still SUCCEEDS but no
    # origin/main exists afterwards — that is the case being classified.
    repo._git(repo.origin, "branch", "-m", "main", "master")
    repo._git(repo.work, "update-ref", "-d", "refs/remotes/origin/main")

    rc, out = repo.ship()

    assert rc == 4, f"expected rc4, got {rc}\n{out}"
    assert "no origin/main" in out, f"unclear diagnosis\n{out}"
    # Assert on the per-host DIAGNOSIS, not the whole output — the trailing
    # legend legitimately contains the word "diverged".
    assert "has diverged" not in out, f"misclassified as divergence\n{out}"


def test_verify_line_names_a_dirty_tree(repo):
    """Dirty convergence is the normal path now — the verifier must say so."""
    (repo.work / "stable.txt").write_text("stable\nwip\n")
    rc, out = repo.ship()
    assert rc == 0, out
    assert "DIRTY" in out, f"verify line hides the dirty state\n{out}"
    assert "origin/main + local WIP" in out


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


# --------------------------------------------------------------------------- #
# ship.sh must never copy over a path that home-manager MANAGES
#
# 2026-08-10: ship.sh rsynced `$HOME/.claude/skills/` workbench -> laptop AFTER
# the remote `home-manager switch` had already deployed those same skills. `-a`
# implies `-l`, so every store symlink copied with its link text VERBATIM — the
# laptop's correct links into its OWN home-manager-files closure were replaced
# by links into the WORKBENCH's store path, which does not exist there. All 15
# `~/.claude/skills/*/SKILL.md` on the laptop were left dangling (ENOENT) while
# ship.sh printed "skills synced". The rsync's own rationale ("NOT in git/nix")
# had been false since skills became a `home.file` entry.
#
# The invariant is structural, not a spelling: a path home-manager owns must not
# also be pushed around by hand, in EITHER direction, because whichever writer
# runs last wins and the two disagree about what the correct link text is.
# --------------------------------------------------------------------------- #
HOME_NIX = Path(__file__).resolve().parents[2] / "nix" / "home.nix"


def home_manager_managed_paths(nix_source):
    """Home-relative paths declared as `home.file."<path>"` in a nix module."""
    return set(re.findall(r'home\.file\."([^"]+)"', nix_source))


def rsync_home_paths(shell_source):
    """Home-relative paths that an `rsync` in `shell_source` reads or writes.

    Comment lines are excluded (as in test_ship_source_never_stashes) — only
    executable lines can actually move bytes. Recognises the three shapes a
    home path takes in this script: `$HOME/x`, `~/x`, and an ssh destination
    `$REMOTE:x` (a relative remote path is resolved against the remote $HOME).
    Returns {path: line} so a failure can name the offending line.
    """
    found = {}
    for line in shell_source.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if not re.search(r"\brsync\b", stripped):
            continue
        for tok in re.findall(r'[^\s]+', stripped):
            tok = tok.strip("\"'")
            path = None
            if tok.startswith("$HOME/"):
                path = tok[len("$HOME/"):]
            elif tok.startswith("${HOME}/"):
                path = tok[len("${HOME}/"):]
            elif tok.startswith("~/"):
                path = tok[2:]
            elif ":" in tok:
                # ssh destination: user@host:path / $VAR:path
                remote = tok.split(":", 1)[1]
                if remote and not remote.startswith("/") and not remote.startswith("//"):
                    path = remote
            if path:
                found.setdefault(path.rstrip("/"), stripped)
    return found


def _overlaps(a, b):
    """True when home-relative paths `a` and `b` are the same file or nested."""
    return a == b or a.startswith(b + "/") or b.startswith(a + "/")


def test_managed_path_extractors_actually_see_something():
    """POSITIVE CONTROL for the parsers below.

    Both halves of the real assertion are `not in` checks, so each one passes
    just as happily against a parser that is wired to nothing. These two cases
    prove the parsers CAN return a non-empty answer before a zero from them is
    allowed to mean anything.
    """
    managed = home_manager_managed_paths(HOME_NIX.read_text())
    assert ".claude/skills" in managed, (
        "home.nix parser found no `.claude/skills` home.file entry — either the "
        f"regex broke or skills stopped being managed. managed={sorted(managed)[:10]}"
    )

    # The exact pre-fix line, fed through the extractor it is meant to catch.
    pre_fix = (
        '  # a comment mentioning rsync of $HOME/.claude/commands/ must be ignored\n'
        '    if rsync -az -e "ssh -o ConnectTimeout=10" "$HOME/.claude/skills/"'
        ' "$REMOTE_SSH:.claude/skills/" 2>/dev/null; then\n'
    )
    paths = rsync_home_paths(pre_fix)
    assert ".claude/skills" in paths, f"rsync parser missed the source path: {paths}"
    assert ".claude/commands" not in paths, f"rsync parser read a COMMENT: {paths}"


def test_ship_never_rsyncs_a_home_manager_managed_path():
    """ship.sh must not hand-copy anything `home-manager switch` already owns."""
    managed = home_manager_managed_paths(HOME_NIX.read_text())
    for path, line in sorted(rsync_home_paths(SHIP.read_text()).items()):
        clash = sorted(m for m in managed if _overlaps(path, m))
        assert not clash, (
            f"ship.sh rsyncs ~/{path}, which home-manager MANAGES (home.file "
            f"{clash!r} in nix/home.nix). `rsync -a` copies store symlinks with "
            f"their link text verbatim, so this overwrites the REMOTE host's "
            f"links into its own nix store with links into THIS host's store — "
            f"they dangle there. `home-manager switch` already deploys this "
            f"path on every host; delete the rsync.\n  offending line: {line}"
        )


# --------------------------------------------------------------------------- #
# The post-switch CONSUMER check (rc12)
#
# Removing the rsync (above) fixes the cause. This is the DETECTOR, because
# three separate layers reported healthy for the entire time the laptop's
# ~/.claude/skills/ was 100% broken: ship.sh printed "skills synced" while
# causing it, drift-check.sh only ever compares git refs, and the rsync's own
# comment asserted the opposite of the truth. A deploy reporting success is a
# claim about the DEPLOY, not about the CONSUMER.
#
# The check walks home-manager's OWN manifest — the `home-files` tree of the
# host's current generation — and asserts every path it lists resolves in $HOME.
# Deriving the path set from the manifest rather than from a hardcoded
# `skills/` is what makes it catch the same break in commands/, hooks/, the
# opencode mirrors, or any home.file target added tomorrow; and it is also what
# keeps unmanaged content (the clickup checkout's pnpm symlinks) out of scope
# without needing an exclusion list that would rot.
#
# What it structurally CANNOT see, stated so nobody reads more into a green than
# is there — and it is NOT the single blind spot this comment used to name:
#   * a managed path REPLACED by a real file of the same name resolves fine and
#     is not reported;
#   * STALENESS. Every input — the manifest AND the links in it — is read out of
#     the host's own CURRENTLY-ACTIVE generation, so the reference point moves
#     with the host and an old generation is perfectly self-consistent. That is
#     rc13's job, tested in the next section.
# This check answers "does every managed path resolve", not "is every managed
# path the store link nix intended" and not "is it the CURRENT one".
# --------------------------------------------------------------------------- #
def _assert_shim_is_live(shim_dir, args, must_fail, why, expect_prefix=None):
    """🔴 Validate the INSTRUMENT before reading its verdict.

    Both `find` shims below are the whole experiment: if one silently fails to
    exec, or execs but does not alter behaviour, the test around it passes (or
    fails) for a reason that has nothing to do with ship.sh. That is not
    hypothetical — the first version of these shims carried a
    `#!/usr/bin/env bash` shebang, which does not exist in the nix build
    sandbox, so the shim never ran and the failure was reported against the
    wrong guard entirely.
    """
    p = subprocess.run(
        [str(shim_dir / "find"), *args],
        capture_output=True, text=True,
        env={**os.environ, "PATH": f"{shim_dir}:{os.environ['PATH']}"},
    )
    if must_fail:
        assert p.returncode != 0, f"{why}\nstdout={p.stdout!r} stderr={p.stderr!r}"
    else:
        assert p.returncode == 0, f"shim did not run: {p.stderr!r}"
    if expect_prefix is not None:
        assert p.stdout.startswith(expect_prefix), f"{why}\nstdout={p.stdout!r}"


def _managed_counts(out):
    """(checked, dangling) parsed out of the consumer-check line."""
    m = re.search(r"(\d+) checked, (\d+) dangling", out)
    assert m, f"no consumer-check line with counts in output:\n{out}"
    return int(m.group(1)), int(m.group(2))


def test_managed_artifact_check_reports_how_many_it_examined(repo):
    """🔴 POSITIVE CONTROL — a zero must be distinguishable from a dead probe.

    "0 dangling" is exactly what ship.sh effectively claimed for the entire
    period the laptop was broken, so the count of what was EXAMINED is the
    load-bearing half of the line. A check wired to nothing also reports
    0 dangling; only a non-zero examined count separates the two.
    """
    rc, out = repo.ship()
    assert rc == 0, out
    checked, dangling = _managed_counts(out)
    assert checked == len(Repo.MANAGED), (
        f"expected all {len(Repo.MANAGED)} managed paths to be examined, "
        f"got {checked} — the manifest walk is missing entries\n{out}"
    )
    assert dangling == 0, out


@pytest.mark.parametrize(
    "rel",
    [
        ".claude/skills/bar/SKILL.md",            # the family that actually broke
        ".claude/hooks/bash-guard.py",            # ...and three that have not yet
        ".config/opencode/AGENTS.md",
        ".config/opencode/skills/bar/SKILL.md",
    ],
)
def test_managed_artifact_check_fails_on_a_dangling_managed_symlink(repo, rel):
    """🔴 NEGATIVE CONTROL, in the REAL failure shape.

    Parametrised across four home.file families on purpose: a check that only
    caught `skills/` would pass three of these while the hazard sat in a
    different shape. Each case points the managed path at a well-formed link
    into another host's home-manager closure — byte-for-byte what the rsync
    left on the laptop — not at an obviously-bogus fixture path.
    """
    repo.break_managed_symlink(rel)

    rc, out = repo.ship()

    assert rc == 12, f"expected rc12 (consumer broken), got {rc}\n{out}"
    assert "MANAGED ARTIFACTS BROKEN" in out, f"wrong failure reported\n{out}"
    assert rel in out, f"the broken path is not named\n{out}"
    assert Repo.FOREIGN_STORE in out, f"the foreign store target is not named\n{out}"
    # It must not also claim success. The git state IS fine here — that is the
    # whole point: converged-and-verified was true while the consumer was dead.
    assert "✅ VERIFIED" not in out, f"reported VERIFIED with a broken consumer\n{out}"
    checked, dangling = _managed_counts(out)
    assert (checked, dangling) == (len(Repo.MANAGED), 1), out


def test_managed_artifact_check_fails_when_a_managed_path_is_absent(repo):
    """A managed path missing entirely is a different diagnosis, also fatal."""
    repo.delete_managed_path(".claude/RULES.md")

    rc, out = repo.ship()

    assert rc == 12, f"expected rc12, got {rc}\n{out}"
    assert ".claude/RULES.md" in out
    assert "absent" in out, f"absent vs dangling not distinguished\n{out}"


def test_managed_artifact_check_ignores_unmanaged_dangling_symlinks(repo):
    """🔴 No false positive on content home-manager does not own.

    The fixture plants two broken symlinks in $HOME that nix never deployed:
    one inside `~/.claude/skills/clickup/node_modules/.pnpm` (a standalone git
    checkout nested INSIDE a managed directory) and one sitting directly beside
    the managed files in `~/.claude`. A $HOME-walking implementation would
    report both and need an exclusion list; a manifest-driven one cannot see
    them at all.
    """
    pnpm_link = repo.home / ".claude/skills/clickup/node_modules/.pnpm/dangles"
    assert pnpm_link.is_symlink() and not pnpm_link.exists(), "fixture not broken"

    rc, out = repo.ship()

    assert rc == 0, f"false positive on unmanaged content\n{out}"
    assert "clickup" not in out, f"flagged an unmanaged checkout\n{out}"
    assert "settings.local.json.bak" not in out, f"flagged an unmanaged link\n{out}"
    checked, dangling = _managed_counts(out)
    assert dangling == 0, out


def test_managed_artifact_check_refuses_when_the_manifest_is_unlocatable(repo):
    """A probe that cannot find its input must go RED, never quietly green.

    This is the branch that turns "0 dangling" back into a lie, so it is the
    one place a silent skip would reinstate the original failure exactly.
    """
    repo.drop_home_manager_generation()

    rc, out = repo.ship()

    assert rc == 12, f"a check with no input reported success: rc={rc}\n{out}"
    assert "NOT CHECKED" in out, f"the skip is not announced\n{out}"
    assert "proves NOTHING" in out, f"the green is not disclaimed\n{out}"
    assert "✅ VERIFIED" not in out, out


def test_managed_artifact_check_refuses_a_manifest_that_lists_nothing(repo):
    """🔴 REACHABILITY for the zero-examined guard.

    A mutation sweep (2026-08-11) found this guard SURVIVING: nothing in the
    suite produced a manifest that is locatable but empty, so deleting the guard
    changed no result — it was untested code sitting in front of the exact
    vacuous green the whole check exists to prevent. This reaches it with a case
    no earlier branch rejects: the gcroot resolves, `home-files` exists and is a
    directory, and the walk simply returns nothing.
    """
    empty = repo.root / "nixstore" / "cccccccc-home-manager-files-empty"
    empty.mkdir()
    link = repo.gen / "home-files"
    link.unlink()
    link.symlink_to(empty)

    rc, out = repo.ship()

    assert rc == 12, f"an empty manifest reported success: rc={rc}\n{out}"
    assert "listed NO files" in out, f"wrong guard fired\n{out}"
    assert "broken probe, not a clean host" in out, out
    assert "✅ VERIFIED" not in out, out


def test_managed_artifact_check_refuses_unparseable_find_output(repo, tmp_path):
    """🔴 REACHABILITY for the prefix-strip guard.

    If find(1) ever changes its output shape, every entry stops matching the
    manifest prefix and would be skipped — leaving a walk that examines nothing
    and says so only through the guard below. Reached with a `find` shim that
    emits paths under a different root.
    """
    real_find = shutil.which("find")
    assert real_find, "no find on PATH"
    shim_dir = tmp_path / "reshaping-shim"
    shim_dir.mkdir()
    write_exec(shim_dir / "find", f'{real_find} "$@" | sed "s|^|/elsewhere|"\n')
    _assert_shim_is_live(
        shim_dir,
        args=[str(tmp_path), "-maxdepth", "0"],
        must_fail=False,
        expect_prefix="/elsewhere",
        why="the shim never reshaped find's output, so this test proves nothing",
    )

    rc, out = repo.ship(PATH=f"{shim_dir}:{os.environ['PATH']}")

    assert rc == 12, f"unparseable manifest output reported success: rc={rc}\n{out}"
    assert "could not derive home-relative paths" in out, f"wrong guard fired\n{out}"
    assert "✅ VERIFIED" not in out, out


def test_managed_artifact_check_finds_the_legacy_manifest_location(repo):
    """🔴 REACHABILITY for the second probed location.

    home-manager has kept its generation under both
    `…/home-manager/gcroots/current-home` and `…/nix/profiles/home-manager`;
    both exist on the workbench today (measured 2026-08-11). The fallback was a
    surviving mutant until this test — deleting it changed no result, so it was
    an untested branch whose only failure mode is a spurious rc12 after a
    home-manager upgrade, and a permanently-red gate is worse than no gate.
    """
    repo.use_legacy_manifest_location()

    rc, out = repo.ship()

    assert rc == 0, f"the legacy manifest location is not probed\n{out}"
    checked, dangling = _managed_counts(out)
    assert (checked, dangling) == (len(Repo.MANAGED), 0), out


def test_managed_artifact_check_honours_xdg_state_home(tmp_path):
    """The manifest lookup follows $XDG_STATE_HOME when it is set."""
    r = Repo(tmp_path)
    moved = tmp_path / "elsewhere-state"
    shutil.move(str(r.home / ".local" / "state"), str(moved))

    rc, out = r.ship(XDG_STATE_HOME=str(moved))

    assert rc == 0, out
    checked, _ = _managed_counts(out)
    assert checked == len(Repo.MANAGED), out


def test_managed_artifact_check_works_without_gnu_find_extensions(repo, tmp_path):
    """🔴 The manifest walk must not depend on GNU `find`.

    MEASURED 2026-08-11: over `ssh <laptop>`, `command -v find` resolves to a
    BusyBox applet in that host's nix profile, and BusyBox find has no
    `-printf`. The first draft of this check used `-printf '%P\\n'` and, run on
    the laptop that way, reported `checked=1 dangling=0` —
    a clean bill of health for a host that in fact had 46 dangling managed
    links. ship.sh runs this same routine over ssh on the REMOTE host, so the
    remote leg is precisely where a GNU-only flag silently zeroes the count.

    The shim reproduces that: a `find` that rejects the GNU-only flags while
    passing everything else through.
    """
    real_find = shutil.which("find")
    assert real_find, "no find on PATH"
    shim_dir = tmp_path / "busybox-shim"
    shim_dir.mkdir()
    write_exec(
        shim_dir / "find",
        'for a in "$@"; do\n'
        "  case $a in\n"
        "    -printf|-regextype|-quit)\n"
        '      echo "find: unrecognized: $a" >&2; exit 1 ;;\n'
        "  esac\n"
        "done\n"
        f'exec {real_find} "$@"\n',
    )
    _assert_shim_is_live(
        shim_dir,
        args=["-printf", "%p"],
        must_fail=True,
        why="the shim never rejected -printf, so this test proves nothing",
    )

    rc, out = repo.ship(PATH=f"{shim_dir}:{os.environ['PATH']}")

    assert rc == 0, f"the manifest walk needs GNU find extensions\n{out}"
    checked, _ = _managed_counts(out)
    assert checked == len(Repo.MANAGED), (
        f"BusyBox-compatible find examined {checked} of {len(Repo.MANAGED)} "
        f"managed paths — the walk silently under-counts on the remote leg\n{out}"
    )


def test_managed_artifact_check_runs_on_the_remote_leg_too(repo):
    """The routine shipped over ssh must be the SAME one that runs locally.

    The bug existed only on the laptop, so a consumer check that runs only on
    the local host is worthless for it. ship.sh has exactly one CONVERGE body,
    executed locally via `bash -c` and remotely via `ssh <host> "<body>"` — so
    this asserts the check lives INSIDE that body rather than in the local-only
    driver below it, which is the way it could regress to local-only.
    """
    src = SHIP.read_text()
    body = src.split("CONVERGE='", 1)
    assert len(body) == 2, "CONVERGE block not found — ship.sh was restructured"
    converge = body[1].split("\n'\n", 1)[0]
    assert "verify_managed_artifacts" in converge, (
        "the consumer check is not inside CONVERGE, so it cannot run on the "
        "remote host — which is the only host the original bug affected"
    )
    # ...and CONVERGE really is what gets sent over ssh.
    assert re.search(r'ssh .*"\$REMOTE_SSH".*\$CONVERGE', src), (
        "CONVERGE is no longer the body executed over ssh"
    )


# --------------------------------------------------------------------------- #
# The post-switch CURRENCY check (rc13)
#
# MEASURED 2026-08-19: the workbench served the pre-#611 ~/.claude/RULES.md
# while ship.sh printed "488 checked, 0 dangling, 0 absent" and
# "✅ VERIFIED … + switched". Nothing was broken — the host was simply on an OLD
# generation, and rc12 reads its manifest out of that same old generation, so
# its reference point moves with the host and it cannot see the condition at
# all. Re-measured against real generations on 2026-08-20: pointing a synthetic
# $HOME at home-manager generation 495 (2026-08-19 01:06) and the repo at
# origin/main, rc12 reported "441 checked, 0 dangling, 0 absent" while the new
# check reported 337 repo-sourced examined, 57 stale — .claude/RULES.md among
# them. The two questions get two exit codes because they are two operator
# actions: rc12 is a repair, rc13 is a re-switch.
#
# The comparison is by CONTENT with git as the oracle, so it needs no
# manifest-path -> repo-path table (every such table is a spelling that rots):
# a verbatim `home.file` deploy has the same git blob id as its repo source, a
# blob that is in the object store but NOT in the working tree is a HISTORICAL
# version, and a blob git has never seen was RENDERED rather than copied and is
# excluded as carrying no evidence.
# --------------------------------------------------------------------------- #
def _currency_counts(out):
    """(repo-sourced examined, stale) parsed out of the currency line."""
    m = re.search(r"(\d+) repo-sourced examined, (\d+) stale", out)
    assert m, f"no currency line with counts in output:\n{out}"
    return int(m.group(1)), int(m.group(2))


def _currency_buckets(out):
    """(not-repo-sourced, out-of-store, dirs) from the same line."""
    m = re.search(r"\((\d+) not repo-sourced, (\d+) out-of-store, (\d+) dirs\)", out)
    assert m, f"no currency bucket accounting in output:\n{out}"
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


def test_currency_check_reports_how_many_it_examined(repo):
    """🔴 POSITIVE CONTROL — a current host must report a NON-ZERO examined count.

    "0 stale" out of 0 examined is the same reassuring zero rc12 exists to
    prevent, one question later, so the pair is the assertion: this host is
    current AND something comparable was actually looked at. The count is
    REPO_SOURCED, not len(MANAGED) — the rendered entry and the out-of-store
    entry are excluded on purpose and are asserted separately below.
    """
    rc, out = repo.ship()

    assert rc == 0, out
    assert "managed artifacts CURRENT" in out, f"no currency verdict\n{out}"
    examined, stale = _currency_counts(out)
    assert (examined, stale) == (Repo.REPO_SOURCED, 0), (
        f"expected all {Repo.REPO_SOURCED} repo-sourced managed paths to be "
        f"examined and none stale\n{out}"
    )


def test_currency_check_catches_an_artifact_that_resolves_but_is_stale(repo_stale):
    """🔴 THE REGRESSION TEST — the 2026-08-19 condition, end to end.

    Every managed path resolves, so rc12 passes with a perfect green; one of
    them serves the BASE commit's bytes while the tree has been fast-forwarded
    past it. Before this check existed the run exited 0 and printed VERIFIED.
    """
    rc, out = repo_stale.ship()

    assert rc == 13, f"expected rc13 (consumer stale), got {rc}\n{out}"
    assert "MANAGED ARTIFACTS STALE" in out, f"wrong failure reported\n{out}"
    assert ".claude/RULES.md" in out, f"the stale path is not named\n{out}"
    # 🔴 The load-bearing half: the OLD check is green on this very run. If it
    # were red too, rc13 would be redundant rather than a distinct question.
    assert "✅ managed artifacts resolve" in out, (
        f"rc12 did not pass here, so this fixture is not the staleness case\n{out}"
    )
    assert "✅ VERIFIED" not in out, f"reported VERIFIED with a stale consumer\n{out}"
    examined, stale = _currency_counts(out)
    assert (examined, stale) == (Repo.REPO_SOURCED, 1), out
    # Actionable: it must say re-switch, not repair, and identify the generation.
    assert "home-manager switch --flake" in out
    assert "generation being served" in out, f"no generation fingerprint\n{out}"


def test_currency_check_catches_every_managed_family_not_just_rules_md(tmp_path):
    """Structural, not spelled: staleness in ANY family is caught.

    A check that only knew about `.claude/RULES.md` would pass three of these.
    All four repo-sourced entries are stale at once, and all four must be named.
    """
    r = Repo(tmp_path, stale_managed=[k for k, v in Repo.MANAGED_SOURCES.items() if v])

    rc, out = r.ship()

    assert rc == 13, f"expected rc13, got {rc}\n{out}"
    examined, stale = _currency_counts(out)
    assert (examined, stale) == (Repo.REPO_SOURCED, Repo.REPO_SOURCED), out
    for rel, src in Repo.MANAGED_SOURCES.items():
        if src:
            assert rel in out, f"{rel} stale but not named\n{out}"


def test_currency_check_excludes_out_of_store_symlinks_from_the_evidence(repo_stale):
    """🔴 The vacuous-check exclusion, asserted as a NUMBER, not as prose.

    A `mkOutOfStoreSymlink` target resolves back into the repo working tree, so
    comparing it against the repo source compares a file with itself: it can
    never be stale, and every one of them counted as evidence would inflate the
    examined number with checks incapable of detecting anything. The fixture has
    one, and it must land in the out-of-store bucket while the store-copy in the
    SAME run is still caught — which is what makes this an exclusion rather than
    a blanket skip.
    """
    rc, out = repo_stale.ship()

    assert rc == 13, out
    examined, stale = _currency_counts(out)
    rendered, out_of_store, _dirs = _currency_buckets(out)
    assert out_of_store == len(Repo.OUT_OF_STORE), (
        f"the mkOutOfStoreSymlink entry is not being separated out\n{out}"
    )
    assert rendered == sum(1 for v in Repo.MANAGED_SOURCES.values() if v is None), (
        f"the rendered (non-repo-sourced) entry is not being separated out\n{out}"
    )
    # The exclusion is not a skip: the buckets and the evidence add up to every
    # manifest entry, and the stale store copy in the same run was still caught.
    assert examined + rendered + out_of_store == len(Repo.MANAGED), (
        f"manifest entries went missing between the buckets\n{out}"
    )
    assert stale == 1, out
    # ...and the excluded path is NOT among the things reported stale.
    for rel in Repo.OUT_OF_STORE:
        assert rel not in out, f"an out-of-store link was reported\n{out}"


def test_currency_check_refuses_when_nothing_is_repo_sourced(tmp_path):
    """🔴 REACHABILITY for the zero-examined guard — the vacuous green, again.

    Every managed entry carries bytes git has never seen, so nothing is
    comparable. A check that reported "0 stale" here would be reporting on an
    empty set, which is exactly the shape that let the original bug run for
    months. Reached with a case no earlier branch rejects: the manifest is
    locatable, non-empty, and every path resolves.
    """
    r = Repo(tmp_path, phantom_managed=True)

    rc, out = r.ship()

    assert rc == 13, f"a comparison over nothing reported success: rc={rc}\n{out}"
    assert "0 repo-sourced artifacts examined" in out, f"wrong guard fired\n{out}"
    assert "broken probe, not a current host" in out, out
    assert "✅ VERIFIED" not in out, out
    # rc12 is still green — the host resolves fine, we simply cannot judge it.
    assert "✅ managed artifacts resolve" in out, out


def _git_shim(shim_dir, mode):
    """A `git` that is real for every subcommand except one, sabotaged per mode.

    ship.sh runs a dozen git commands; the shim must pass all of them through or
    the run fails somewhere unrelated and the test proves nothing about the
    guard it names.
    """
    real = shutil.which("git")
    assert real, "no git on PATH"
    shim_dir.mkdir(exist_ok=True)
    if mode == "truncate-hash-object":
        body = (
            "for a in \"$@\"; do\n"
            "  if [ \"$a\" = hash-object ]; then\n"
            f"    {real} \"$@\" | sed '$d'\n"
            "    exit 0\n"
            "  fi\n"
            "done\n"
            f"exec {real} \"$@\"\n"
        )
    elif mode == "empty-ls-files":
        body = (
            "for a in \"$@\"; do\n"
            "  if [ \"$a\" = ls-files ]; then exit 0; fi\n"
            "done\n"
            f"exec {real} \"$@\"\n"
        )
    else:  # pragma: no cover - programming error
        raise AssertionError(f"unknown shim mode {mode}")
    write_exec(shim_dir / "git", body)
    return shim_dir


def test_currency_check_refuses_when_the_digest_count_does_not_match(repo, tmp_path):
    """🔴 REACHABILITY + INSTRUMENT VALIDATION for the digest-count guard.

    `git hash-object --stdin-paths` emits one line per path. If it emits fewer —
    it aborts on the first unreadable path — the parallel rel/blob lists slip by
    one and every subsequent verdict is attached to the WRONG file. Silently
    dropping the tail would also shrink the examined count toward the vacuous
    zero. The guard compares the two line counts; this reaches it.
    """
    shim = _git_shim(tmp_path / "trunc-git-shim", "truncate-hash-object")
    # The shim is the whole experiment — prove it is live and that it still
    # passes normal subcommands through.
    env = {**os.environ, "PATH": f"{shim}:{os.environ['PATH']}"}
    probe = subprocess.run(
        [str(shim / "git"), "-C", str(repo.work), "hash-object", "--stdin-paths"],
        input=f"{repo.work}/f\n{repo.work}/stable.txt\n",
        capture_output=True, text=True, env=env,
    )
    assert probe.returncode == 0 and len(probe.stdout.split()) == 1, (
        f"the shim did not truncate hash-object, so this test proves nothing: "
        f"{probe.stdout!r} {probe.stderr!r}"
    )
    passthrough = subprocess.run(
        [str(shim / "git"), "-C", str(repo.work), "rev-parse", "--short", "HEAD"],
        capture_output=True, text=True, env=env,
    )
    assert passthrough.returncode == 0 and passthrough.stdout.strip(), (
        f"the shim broke unrelated git subcommands: {passthrough.stderr!r}"
    )

    rc, out = repo.ship(PATH=f"{shim}:{os.environ['PATH']}")

    assert rc == 13, f"a slipped digest list reported success: rc={rc}\n{out}"
    assert "CURRENCY NOT CHECKED" in out and "digests for" in out, (
        f"wrong guard fired\n{out}"
    )
    assert "✅ VERIFIED" not in out, out


def test_currency_check_refuses_an_empty_repo_reference_set(repo, tmp_path):
    """🔴 REACHABILITY for the reference-set guard — a comparison wired to nothing.

    The repo side of the comparison is `git ls-files` -> hash-object. If that
    comes back empty, NOTHING deployed can match the working tree, so every
    repo-sourced artifact would be reported stale — a confident, total false
    positive, and a permanently-red gate is worse than no gate. The guard
    refuses instead; this reaches it with a `git` whose ls-files says nothing.
    """
    shim = _git_shim(tmp_path / "nolsfiles-git-shim", "empty-ls-files")
    env = {**os.environ, "PATH": f"{shim}:{os.environ['PATH']}"}
    probe = subprocess.run(
        [str(shim / "git"), "-C", str(repo.work), "ls-files"],
        capture_output=True, text=True, env=env,
    )
    assert probe.returncode == 0 and probe.stdout == "", (
        f"the shim did not empty ls-files: {probe.stdout!r}"
    )
    real_probe = subprocess.run(
        ["git", "-C", str(repo.work), "ls-files"], capture_output=True, text=True,
    )
    assert real_probe.stdout.strip(), "positive control: the real repo lists files"

    rc, out = repo.ship(PATH=f"{shim}:{os.environ['PATH']}")

    assert rc == 13, f"an empty reference set reported a verdict: rc={rc}\n{out}"
    assert "CURRENCY NOT CHECKED" in out and "read NO source files" in out, (
        f"wrong guard fired\n{out}"
    )
    assert "MANAGED ARTIFACTS STALE" not in out, (
        f"an empty reference set was turned into a stale verdict\n{out}"
    )
    assert "✅ VERIFIED" not in out, out


def test_currency_check_works_without_gnu_find_extensions(repo_stale, tmp_path):
    """🔴 The currency walk must not depend on GNU `find` either.

    Same measured hazard as the resolution walk: over `ssh <laptop>` `find` is a
    BusyBox applet with no -printf, and this routine runs over ssh on the remote
    host. Asserted on the STALE fixture so a shim that silently zeroed the walk
    would show up as a missed detection rather than as an unchanged green.
    """
    real_find = shutil.which("find")
    assert real_find, "no find on PATH"
    shim_dir = tmp_path / "busybox-shim-currency"
    shim_dir.mkdir()
    write_exec(
        shim_dir / "find",
        'for a in "$@"; do\n'
        "  case $a in\n"
        "    -printf|-regextype|-quit)\n"
        '      echo "find: unrecognized: $a" >&2; exit 1 ;;\n'
        "  esac\n"
        "done\n"
        f'exec {real_find} "$@"\n',
    )
    _assert_shim_is_live(
        shim_dir,
        args=["-printf", "%p"],
        must_fail=True,
        why="the shim never rejected -printf, so this test proves nothing",
    )

    rc, out = repo_stale.ship(PATH=f"{shim_dir}:{os.environ['PATH']}")

    assert rc == 13, f"the currency walk needs GNU find extensions\n{out}"
    examined, stale = _currency_counts(out)
    assert (examined, stale) == (Repo.REPO_SOURCED, 1), (
        f"BusyBox-compatible find examined {examined} of {Repo.REPO_SOURCED} "
        f"repo-sourced paths — the walk under-counts on the remote leg\n{out}"
    )


def test_currency_check_runs_on_the_remote_leg_too():
    """The currency check must live in CONVERGE, the body shipped over ssh.

    The laptop is the host most likely to sit on an old generation — it is
    routinely shut, so it misses ships — which makes a local-only currency check
    worthless for exactly the machine that needs it.
    """
    src = SHIP.read_text()
    converge = src.split("CONVERGE='", 1)[1].split("\n'\n", 1)[0]
    assert "verify_managed_currency" in converge, (
        "the currency check is not inside CONVERGE, so it cannot run on the "
        "remote host — the one most likely to be on a stale generation"
    )
    assert "exit 13" in converge, "rc13 is not raised from inside CONVERGE"


def test_every_converge_exit_code_is_documented_in_the_header_and_the_legend():
    """🔴 The rc ladder is published TWICE and both copies must be complete.

    ship.sh documents its exit codes in the header comment and prints a legend
    on failure. An undocumented rc is an operator staring at a bare number, and
    a legend that lags the code is worse than none — so this pins the code as
    the source of truth and both prose copies to it.
    """
    src = SHIP.read_text()
    converge = src.split("CONVERGE='", 1)[1].split("\n'\n", 1)[0]
    codes = {int(m) for m in re.findall(r"\bexit (\d+)", converge)}
    codes.discard(0)
    assert len(codes) >= 8, (
        f"the exit-code parser found only {sorted(codes)} — CONVERGE raises "
        f"nine distinct codes, so the ledger is reading almost nothing"
    )
    assert 13 in codes, f"rc13 is not raised in CONVERGE: {sorted(codes)}"

    header = src.split("set -uo pipefail", 1)[0]
    legend = src.split('echo "ship: incomplete', 1)[1]
    for rc in sorted(codes):
        assert re.search(rf"^#\s+{rc}\s", header, re.M), (
            f"exit {rc} is raised in CONVERGE but not documented in the "
            f"'Exit codes:' header block"
        )
        assert f"rc{rc}=" in legend, (
            f"exit {rc} is raised in CONVERGE but missing from the rc legend "
            f"printed on failure"
        )
