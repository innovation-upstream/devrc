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
HOST_ROLE_LIB = SCRIPTS / "lib" / "host-role.sh"

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

    def __init__(self, tmp_path, gitconfig_extra="", stale_managed=(), phantom_managed=False,
                 second_host=False, ship_in_repo=False, ship_changes=True,
                 ship_pad_lines=0):
        """`stale_managed` seeds those entries from the BASE commit's bytes.

        That is the real staleness shape, not a synthetic one: the deployed
        content is a genuine former version of the repo file, still reachable in
        the working clone's object store (it was cloned at that commit), while
        the working tree has been fast-forwarded past it. `phantom_managed`
        instead seeds EVERY entry with bytes that were never committed at all —
        the shape in which nothing is repo-sourced and the check must refuse to
        report a green.

        `second_host` adds a SECOND clone of the SAME origin with its own $HOME
        and its own fabricated generation, so a run can converge two hosts (the
        remote one through ssh_shim) and the cross-host agreement check (rc 19)
        has two machines to compare. `ship_in_repo` commits the REAL ship.sh (and
        its lib) INTO the throwaway repo, with the ahead commit rewriting it, so
        a run's own fast-forward replaces the script executing it —
        the rc 20 / self-supersession shape. `ship_pad_lines` inflates BOTH
        committed copies with inert header comments, so a test can overwrite the
        running script with a much SHORTER one and see whether the run survives.
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
        extra = []
        if ship_in_repo:
            extra = self._write_ship_into(builder, 1, pad_lines=ship_pad_lines)
        self._git(builder, "checkout", "-q", "-B", "main")
        self._git(builder, "add", "f", "stable.txt", *self.SOURCE_PATHS, *extra)
        self._git(builder, "commit", "-q", "-m", "base")
        self._git(builder, "push", "-q", "-u", "origin", "main")

        # Working clone pinned at the base commit...
        subprocess.run(
            ["git", "clone", "-q", str(self.origin), str(self.work)],
            check=True, env=self.env(),
        )
        self._git(self.work, "checkout", "-q", "main")

        # ...and, when a second host is wanted, ANOTHER clone pinned at the same
        # commit. Cloned here rather than after the ahead commit so both hosts
        # start exactly 1 behind, which is what makes a mid-run merge visible.
        self.remote_work = None
        self.remote_home = None
        if second_host:
            self.remote_work = tmp_path / "remote-work"
            subprocess.run(
                ["git", "clone", "-q", str(self.origin), str(self.remote_work)],
                check=True, env=self.env(),
            )
            self._git(self.remote_work, "checkout", "-q", "main")
            self.remote_home = tmp_path / "remote-home"
            self.remote_home.mkdir()

        # ...then origin/main advances (work is now exactly 1 behind). The
        # ahead commit REWRITES every managed source, so version 1 becomes a
        # historical blob and version 2 is what a converged host must serve.
        (builder / "f").write_text("base\nupstream\n")
        (builder / "added-upstream.txt").write_text("from upstream\n")
        self._write_sources(builder, 2)
        if ship_in_repo:
            # ship_changes=False writes the SAME bytes again, so the fast-forward
            # leaves scripts/ship.sh alone. That isolates a test that wants some
            # OTHER writer to be the only thing superseding the script.
            self._write_ship_into(
                builder, 2 if ship_changes else 1, pad_lines=ship_pad_lines
            )
        self._git(builder, "add", "f", "added-upstream.txt", *self.SOURCE_PATHS, *extra)
        self._git(builder, "commit", "-q", "-m", "ahead")
        self._git(builder, "push", "-q", "origin", "main")
        self.ahead_sha = self._git(builder, "rev-parse", "HEAD")

        self._seed_home_manager_generation()
        if second_host:
            self._seed_home_manager_generation(
                home=self.remote_home, work=self.remote_work, tag="r"
            )

    def _write_sources(self, tree, version):
        for src in self.SOURCE_PATHS:
            p = tree / src
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(self._src_text(src, version))

    # -- the script under test, committed INTO the repo it converges --------- #
    #
    # 🔴 THE MARKER GOES INSIDE THE CONVERGE PAYLOAD, and that placement is the
    # whole experiment. CONVERGE is expanded into a shell variable near the top
    # of ship.sh, so its bytes are fixed the moment that assignment executes —
    # long before the fast-forward, and long before bash could re-read any later
    # part of the file. A marker in the FINAL VERDICT line would be read from
    # whichever copy bash happened to have buffered, so it could report either
    # version for reasons that have nothing to do with the fix. A marker in
    # CONVERGE reports exactly one thing: which copy of ship.sh produced the
    # payload that ran. That is the defect, verbatim.
    SHIP_MARK_FROM = "✅ VERIFIED — on branch main"
    SHIP_MARK_TO = "✅ VERIFIED [SHIPVER={v}] — on branch main"

    @classmethod
    def ship_source(cls, version, pad_lines=0):
        """The real ship.sh, version-marked inside its CONVERGE payload."""
        src = SHIP.read_text()
        n = src.count(cls.SHIP_MARK_FROM)
        assert n == 2, (
            f"expected exactly 2 VERIFIED lines to mark in ship.sh, found {n}. "
            f"The patch site moved; this fixture would silently stop marking "
            f"anything and every test built on it would pass vacuously."
        )
        src = src.replace(cls.SHIP_MARK_FROM, cls.SHIP_MARK_TO.format(v=version))
        if pad_lines:
            # Padding goes in the HEADER comment block, so it changes the file's
            # LENGTH (and every byte offset after it) without changing what the
            # script does. `--help` prints the header, so it is also inert.
            pad = "".join(f"# pad {i} {'x' * 90}\n" for i in range(pad_lines))
            marker = "# Exit codes:\n"
            assert src.count(marker) == 1, "the pad insertion point is not unique"
            src = src.replace(marker, pad + marker)
        return src

    def _write_ship_into(self, tree, version, pad_lines=0):
        """Commit the real ship.sh + its lib into the throwaway repo.

        Returns the pathspec list to `git add`. The lib is copied verbatim: it is
        sourced by path relative to the *resolved* ship.sh, so it has to sit
        beside the copy that actually runs.
        """
        s = tree / "scripts" / "ship.sh"
        s.parent.mkdir(parents=True, exist_ok=True)
        s.write_text(self.ship_source(version, pad_lines=pad_lines))
        s.chmod(0o755)
        lib = tree / "scripts" / "lib" / "host-role.sh"
        lib.parent.mkdir(parents=True, exist_ok=True)
        lib.write_text(HOST_ROLE_LIB.read_text())
        return ["scripts/ship.sh", "scripts/lib/host-role.sh"]

    # -- fabricated home-manager generation --------------------------------- #
    def _seed_home_manager_generation(self, home=None, work=None, tag="a"):
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

        `home`/`work`/`tag` default to THIS Repo's own pair. Passing another set
        seeds a SECOND host's generation under the same store root — each host
        needs its own, because "resolves" and "is current" are both questions
        about one machine's own generation and the cross-host tests need two
        machines that can answer them independently.
        """
        primary = home is None
        home = self.home if home is None else home
        work = self.work if work is None else work
        store = self.root / "nixstore"
        hmfiles = store / f"{tag}aaaaaaa-home-manager-files"
        gen = store / f"{tag}bbbbbbb-home-manager-generation"
        content = store / f"content-{tag}"
        content.mkdir(parents=True)
        gen.mkdir(parents=True)
        if primary:
            self.hmfiles, self.gen = hmfiles, gen

        for rel, src in self.MANAGED_SOURCES.items():
            blob = content / rel.replace("/", "_")
            if src is None or self.phantom_managed:
                # Rendered by home-manager itself (or, under phantom_managed,
                # deliberately unknown to git): these bytes are no repo file's.
                blob.write_text(f"rendered content for {rel}\n")
            else:
                blob.write_text(self._src_text(src, 1 if rel in self.stale_managed else 2))
            for base in (hmfiles, home):
                (base / rel).parent.mkdir(parents=True, exist_ok=True)
            (hmfiles / rel).symlink_to(blob)
            (home / rel).symlink_to(hmfiles / rel)

        # mkOutOfStoreSymlink: the manifest entry points OUT of the store, at the
        # repo working tree, so the deployed path and the repo source are one
        # file. It resolves (rc 12 can judge it) and can never be stale (rc 13
        # must not count it).
        for rel, src in self.OUT_OF_STORE.items():
            for base in (hmfiles, home):
                (base / rel).parent.mkdir(parents=True, exist_ok=True)
            (hmfiles / rel).symlink_to(work / src)
            (home / rel).symlink_to(hmfiles / rel)

        (gen / "home-files").symlink_to(hmfiles)
        gcroots = home / ".local" / "state" / "home-manager" / "gcroots"
        gcroots.mkdir(parents=True)
        (gcroots / "current-home").symlink_to(gen)

        # --- UNMANAGED content that must NOT be flagged --------------------- #
        # `~/.claude/skills/clickup/` is a standalone git checkout living INSIDE
        # a home-manager-managed directory, and its node_modules is full of pnpm
        # symlinks. Any check that walks $HOME instead of the manifest trips on
        # these; the manifest never mentions them, so a correct check cannot.
        pnpm = home / ".claude" / "skills" / "clickup" / "node_modules" / ".pnpm"
        pnpm.mkdir(parents=True)
        (pnpm / "dangles").symlink_to("../../nowhere/pkg")          # broken, on purpose
        (home / ".claude" / "skills" / "clickup" / "SKILL.md").write_text("unmanaged\n")
        # ...and a plain broken symlink sitting directly among managed files.
        (home / ".claude" / "settings.local.json.bak").symlink_to("/nonexistent/nope")

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

    def ship(self, *args, script=None, **env_extra):
        """Run ship.sh against this repo: local only, no home-manager switch.

        `script` overrides which copy of ship.sh is executed. The default is the
        repo's own scripts/ship.sh; the self-supersession tests pass the copy
        that lives INSIDE the throwaway repo, because the whole question there is
        what happens when the running file is the one being fast-forwarded.
        """
        defaults = dict(
            SHIP_ROLE="workbench",     # bypass IP detection (no `ip` in sandbox)
            SHIP_REPO=str(self.work),
            SHIP_NO_SWITCH="1",        # never run a real home-manager switch
        )
        defaults.update(env_extra)     # a caller may override any of them
        env = self.env(**defaults)
        proc = subprocess.run(
            ["bash", str(script or SHIP), "--no-remote", *args],
            capture_output=True, text=True, env=env,
            timeout=120,               # a re-exec loop must fail as a TIMEOUT, not hang the suite
        )
        return proc.returncode, proc.stdout + proc.stderr

    # -- the SECOND host, reached through a fake `ssh` ----------------------- #
    def prepare_mid_run_merge(self):
        """Stage a further origin/main commit WITHOUT publishing it yet.

        Returns its sha. `ssh_shim(advance_origin=...)` moves origin/main onto it
        at the moment the remote leg starts, which is the 2026-08-19 shape: #619
        merged BETWEEN the workbench's fetch and the laptop's.

        🔴 It deliberately touches NO managed source. If it did, the remote host
        would land on a commit whose sources its generation no longer matches and
        the run would go red on rc 13 — a real failure, but not the one under
        test, and the rc 19 assertion would then be passing for the wrong reason.
        """
        builder = self.root / "builder"
        # Built on a side branch so the builder's own `main` never moves — no
        # reset, no rewind, nothing this fixture has to undo.
        self._git(builder, "checkout", "-q", "-b", "staged-next")
        (builder / "unrelated-later.txt").write_text("landed mid-run\n")
        self._git(builder, "add", "unrelated-later.txt")
        self._git(builder, "commit", "-q", "-m", "a PR merging mid-run")
        sha = self._git(builder, "rev-parse", "HEAD")
        self._git(builder, "push", "-q", "origin", "HEAD:refs/heads/staged-next")
        self._git(builder, "checkout", "-q", "main")
        return sha

    def ssh_shim(self, tmp_path, advance_to=None, strip_sha=False):
        """A fake `ssh` that runs ship.sh's payload against the SECOND host.

        ship.sh invokes `ssh -o ConnectTimeout=10 <target> "<payload>"`, so the
        shim takes the LAST argument and runs it with the second host's $HOME and
        $SHIP_REPO. The payload is byte-for-byte the string ship.sh would have put
        on the wire — that is what makes this a test of the remote leg rather than
        of a second local run.

        `advance_to` fast-forwards origin/main onto a staged commit FIRST, so the
        remote leg's own `git fetch` sees a newer origin/main than the local leg
        did. `strip_sha` instead deletes the `ship-landed-sha` line from an
        otherwise successful remote run — the shape where a leg exits 0 and the
        agreement still cannot be compared.
        """
        d = tmp_path / "ssh-shim"
        d.mkdir(exist_ok=True)
        advance = ""
        if advance_to:
            advance = (
                f'git --git-dir={self.origin} update-ref refs/heads/main {advance_to} '
                f'|| exit 97\n'
            )
        # 🔴 POSIX sh only (mockbin owns the shebang and it is /bin/sh), so no
        # PIPESTATUS here — the strip variant captures, then filters, then exits
        # with the payload's OWN status.
        run = 'exec bash -c "$payload"\n'
        if strip_sha:
            run = (
                'out=$(bash -c "$payload"); rc=$?\n'
                'printf "%s\\n" "$out" | grep -v " ship-landed-sha "\n'
                "exit $rc\n"
            )
        write_exec(
            d / "ssh",
            "payload=\n"
            'for a in "$@"; do payload="$a"; done\n'
            + advance +
            f'export HOME="{self.remote_home}"\n'
            f'export SHIP_REPO="{self.remote_work}"\n'
            f'export GIT_CONFIG_GLOBAL="{self.gitconfig}"\n'
            "export GIT_CONFIG_SYSTEM=/dev/null\n"
            "unset XDG_STATE_HOME\n"
            + run,
        )
        return d

    def ship_both_hosts(self, shim_dir, *args, script=None, **env_extra):
        """Converge BOTH fabricated hosts — local via bash, remote via the shim."""
        defaults = dict(
            SHIP_ROLE="workbench",
            SHIP_REPO=str(self.work),
            SHIP_NO_SWITCH="1",
            REMOTE_SSH="fixture@second-host",
            PATH=f"{shim_dir}:{os.environ['PATH']}",
        )
        defaults.update(env_extra)
        env = self.env(**defaults)
        proc = subprocess.run(
            ["bash", str(script or SHIP), *args],
            capture_output=True, text=True, env=env, timeout=120,
        )
        return proc.returncode, proc.stdout + proc.stderr

    def remote_head(self):
        return self._git(self.remote_work, "rev-parse", "HEAD")


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


def _ship_sections():
    """(header, converge-payload, driver-code) of ship.sh.

    `driver` is everything OUTSIDE the CONVERGE string — the part that runs only
    on the machine you typed the command on. Comment lines are dropped from the
    scanned code because the header legitimately *discusses* exit codes ("would
    look for lib/ next to the SYMLINK and exit 6"), and a ledger that counted its
    own prose would be pinning the documentation to itself.
    """
    src = SHIP.read_text()
    head, rest = src.split("CONVERGE='", 1)
    converge, tail = rest.split("\n'\n", 1)

    def code_only(text):
        return "\n".join(
            ln for ln in text.splitlines() if not ln.strip().startswith("#")
        )

    header = src.split("set -uo pipefail", 1)[0]
    return header, code_only(converge), code_only(head) + "\n" + code_only(tail)


def _ship_exit_codes(text):
    """Non-zero statuses `text` can hand back.

    Two spellings, because ship.sh uses both: a literal `exit N`, and `rc=N`
    followed by the single `exit "$rc"` at the bottom. Counting only the first
    would have missed rc 19 entirely — it is never written as `exit 19`.
    """
    codes = {int(m) for m in re.findall(r"\bexit (\d+)", text)}
    codes |= {int(m) for m in re.findall(r"\brc=(\d+)", text)}
    codes.discard(0)
    return codes


def test_the_exit_code_parser_sees_both_spellings():
    """🔴 POSITIVE CONTROL for the ledger test below.

    That test's real work is a `for` loop over whatever the parser returned, so
    a parser wired to nothing passes it in silence — the classic reassuring
    zero. These are the two spellings that must never stop being visible: an
    `exit N` inside CONVERGE, and an `rc=N` assignment in the driver.
    """
    _header, converge, driver = _ship_sections()
    assert _ship_exit_codes("exit 7\n") == {7}
    assert _ship_exit_codes('[ "$rc" = 0 ] && rc=19\n') == {19}
    assert 13 in _ship_exit_codes(converge), "the CONVERGE half reads nothing"
    assert 19 in _ship_exit_codes(driver), "the driver half reads nothing"


def test_every_exit_code_ship_can_return_is_documented_in_the_header_and_the_legend():
    """🔴 The rc ladder is published TWICE and both copies must be complete.

    ship.sh documents its exit codes in the header comment and prints a legend
    on failure. An undocumented rc is an operator staring at a bare number, and
    a legend that lags the code is worse than none — so this pins the code as
    the source of truth and both prose copies to it.

    🔴 It scans the DRIVER as well as CONVERGE. It used to read CONVERGE only,
    which made the whole outer script a blind spot: rc 2 had been undocumented
    in both ledgers since the file was written, and rc 19 (cross-host
    disagreement) is set in the driver and would have gone the same way.
    """
    header, converge, driver = _ship_sections()
    conv_codes = _ship_exit_codes(converge)
    drv_codes = _ship_exit_codes(driver)
    assert len(conv_codes) >= 8, (
        f"the exit-code parser found only {sorted(conv_codes)} in CONVERGE, "
        f"which raises nine distinct codes — the ledger is reading almost nothing"
    )
    assert {2, 6, 19, 20} <= drv_codes, (
        f"the driver half of the ledger is not seeing its own codes: {sorted(drv_codes)}"
    )

    legend = SHIP.read_text().split('echo "ship: incomplete', 1)[1]
    for rc in sorted(conv_codes | drv_codes):
        assert re.search(rf"^#\s+{rc}\s", header, re.M), (
            f"exit {rc} is reachable but not documented in the "
            f"'Exit codes:' header block"
        )
        assert f"rc{rc}=" in legend, (
            f"exit {rc} is reachable but missing from the rc legend "
            f"printed on failure"
        )


# --------------------------------------------------------------------------- #
# CROSS-HOST AGREEMENT (rc 19)
#
# 🔴 MEASURED 2026-08-19. Both hosts converged and both were verified, and the
# two machines held DIFFERENT commits:
#
#     [nixos] fast-forwarded main 4548e6b -> c7eb5c3      (workbench)
#     [nixos] fast-forwarded main 4548e6b -> e7ceb1f      (laptop)
#     ship: converged + verified at origin/main (local=workbench remote=laptop)
#
# #619 merged between the two legs' `git fetch`es, so each host landed on
# origin/main AS IT SAW IT. Every per-host check is a claim about ONE machine and
# every one of them was TRUE; rc 11 cannot see it either, because it compares
# HEAD against the `target` THAT LEG captured. Nothing compared the hosts to each
# other, and the verdict line asserted a fleet state nobody had checked.
#
# The fixture reproduces it exactly: two clones of one origin, each with its own
# fabricated home-manager generation, and a fake `ssh` that fast-forwards
# origin/main onto a staged commit at the instant the remote leg begins.
# --------------------------------------------------------------------------- #
@pytest.fixture
def two_hosts(tmp_path):
    return Repo(tmp_path, second_host=True)


def _landed_shas(out):
    return re.findall(r"^\[[^\]]*\] ship-landed-sha ([0-9a-f]+)$", out, re.M)


def test_two_hosts_landing_on_one_commit_are_reported_as_agreeing(two_hosts, tmp_path):
    """🔴 POSITIVE CONTROL — the check must be able to say YES, with the sha.

    Everything below is a `!=` or a "not compared", so all of it passes just as
    happily against a comparison wired to nothing. This is the case that proves
    the two shas are really being read and really can match: the verdict has to
    name the count of hosts compared AND the commit they agreed on.
    """
    shim = two_hosts.ssh_shim(tmp_path)

    rc, out = two_hosts.ship_both_hosts(shim)

    assert rc == 0, out
    target = two_hosts.origin_main()
    assert _landed_shas(out) == [target, target], (
        f"expected one landed-sha line per host, both at {target}\n{out}"
    )
    assert "2 hosts compared" in out, f"the compared count is not printed\n{out}"
    assert target in out.split("ship: converged")[1], (
        f"the verdict does not name the commit the hosts agreed on\n{out}"
    )
    assert two_hosts.head() == two_hosts.remote_head() == target, out


def test_a_merge_landing_between_the_two_fetches_is_not_convergence(two_hosts, tmp_path):
    """🔴 THE REGRESSION TEST — the 2026-08-19 run, reproduced end to end.

    origin/main moves after the local leg has fetched and before the remote leg
    does. Each host is internally perfect; the fleet is in two states. Before
    this check the run exited 0 and printed "converged + verified at origin/main".
    """
    staged = two_hosts.prepare_mid_run_merge()
    local_target = two_hosts.origin_main()
    assert staged != local_target, "fixture staged no new commit"
    shim = two_hosts.ssh_shim(tmp_path, advance_to=staged)

    rc, out = two_hosts.ship_both_hosts(shim)

    assert rc == 19, f"expected rc19 (hosts disagree), got {rc}\n{out}"
    assert "HOSTS DISAGREE" in out, f"wrong failure reported\n{out}"
    # 🔴 The load-bearing half: EVERY per-host check is green in this very run.
    # If any of them were red, rc 19 would be redundant rather than the only
    # thing that can see this condition.
    assert out.count("✅ VERIFIED") == 2, (
        f"a per-host check failed, so this fixture is not the mid-run-merge "
        f"case — rc19 must be the ONLY thing that goes red here\n{out}"
    )
    assert _landed_shas(out) == [local_target, staged], out
    assert local_target in out and staged in out, f"the two shas are not named\n{out}"
    # ...and the old, wrong verdict must be gone.
    assert "converged + verified at origin/main (local=" not in out, (
        f"still claiming convergence over two different commits\n{out}"
    )
    # The hosts really did diverge — this is a reporting fix, so the state it
    # reports has to be the state on disk.
    assert two_hosts.head() == local_target
    assert two_hosts.remote_head() == staged


def test_agreement_is_not_compared_when_a_converged_host_reports_no_sha(two_hosts, tmp_path):
    """🔴 REACHABILITY for the "fewer than two shas" guard — the vacuous zero.

    A remote leg that exits 0 while emitting no landed sha leaves ONE sha to
    compare. Reporting agreement on that would be a comparison over an empty
    set, which is the shape every check in this file exists to refuse; reported
    as agreement it would be indistinguishable from a real one. Reached with an
    ssh that filters the line out of an otherwise successful run — no earlier
    branch rejects it, because the leg's own status is 0.
    """
    shim = two_hosts.ssh_shim(tmp_path, strip_sha=True)

    rc, out = two_hosts.ship_both_hosts(shim)

    assert rc == 19, f"a one-sided comparison reported success: rc={rc}\n{out}"
    assert "CROSS-HOST AGREEMENT NOT COMPARED" in out, f"wrong guard fired\n{out}"
    assert "1 of 2 hosts reported a landed sha" in out, f"no examined count\n{out}"
    assert "converged + verified" not in out, out


def test_a_skipped_remote_host_keeps_its_own_diagnosis_not_rc19(two_hosts, tmp_path):
    """rc 19 must not MASK a per-host code — the distinct codes are the signal.

    With the remote repo gone the remote leg exits 3, and only one host reports a
    sha. The agreement is genuinely not compared and is said out loud, but the
    run's exit code stays 3: "no repo on that host" is the actionable diagnosis
    and "the hosts were not compared" is its consequence, not a competing cause.
    """
    shutil.rmtree(two_hosts.remote_work)
    shim = two_hosts.ssh_shim(tmp_path)

    rc, out = two_hosts.ship_both_hosts(shim)

    assert rc == 3, f"the per-host diagnosis was masked: rc={rc}\n{out}"
    assert "NOT COMPARED" in out, f"the missing comparison is not disclosed\n{out}"
    assert "converged + verified" not in out, out


def test_a_single_host_run_says_the_agreement_was_not_compared(repo):
    """--no-remote can only ever be a claim about one machine, and says so."""
    rc, out = repo.ship()

    assert rc == 0, out
    assert "cross-host agreement NOT COMPARED" in out, (
        f"a one-host run reads like a two-host one\n{out}"
    )
    assert "1 host in scope" in out, f"the scope count is not printed\n{out}"
    assert "1 host (local=workbench)" in out, f"the verdict overclaims\n{out}"
    assert "2 hosts compared" not in out, out


def test_a_run_with_no_host_in_scope_is_a_usage_error(repo):
    """🔴 A run that checked NOTHING must never exit 0.

    `--no-local --no-remote` used to reach the verdict line and print
    "converged + verified" having touched neither machine — the vacuous green in
    its purest form. drift-check.sh already spells this refusal as its own rc 2
    ("a RUN THAT CHECKED NO HOST AT ALL"), and the two ladders are deliberately
    aligned.
    """
    rc, out = repo.ship("--no-local")     # Repo.ship already passes --no-remote

    assert rc == 2, f"a run over zero hosts reported {rc}\n{out}"
    assert "NO host to converge" in out, f"the refusal is not explained\n{out}"
    assert "converged" not in out, out


def test_a_host_that_did_not_converge_emits_no_landed_sha(repo_stale):
    """🔴 NEGATIVE CONTROL for where the marker is EMITTED.

    The stale-consumer host is the sharpest case available: it fast-forwards
    successfully, so its output carries FULL 40-hex shas ("fast-forwarded main
    <sha> -> <sha>"), and then fails rc 13. The marker is emitted only AFTER
    every per-host check has passed, so this host must contribute nothing — a
    marker moved even one step earlier would let two hosts that never converged
    be assembled into "two hosts agree".

    ⚠ SCOPE: this pins the EMITTER. ship.sh's own anchored `sed` — the half that
    refuses to scrape a sha out of prose — is pinned by
    test_agreement_is_not_compared_when_a_converged_host_reports_no_sha, which is
    what goes red when the pattern is un-anchored. (Measured: an un-anchored
    parser mutant survives this test, because the regex here is Python's.)
    """
    rc, out = repo_stale.ship()

    assert rc == 13, out
    assert re.search(r"\b[0-9a-f]{40}\b", out), (
        "this fixture prints no full sha at all, so it cannot show the parser "
        f"rejecting one\n{out}"
    )
    assert _landed_shas(out) == [], f"a host that failed rc13 claimed a landed sha\n{out}"


def test_both_hosts_run_the_same_agreement_marker_from_inside_converge():
    """The marker must live in CONVERGE, the body that is shipped over ssh.

    A landed-sha emitted only by the local driver could never disagree with
    itself, and the remote host — the one that fetched second on 2026-08-19 — is
    the half the comparison exists for.
    """
    _header, converge, driver = _ship_sections()
    assert "ship-landed-sha" in converge, (
        "the landed-sha marker is not inside CONVERGE, so the remote host never "
        "emits one and the comparison can only ever be one-sided"
    )
    assert "ship_landed_sha" in driver, "nothing in the driver reads the marker"


# --------------------------------------------------------------------------- #
# SELF-SUPERSESSION (rc 20)
#
# 🔴 ship.sh's own source is one of the things ship.sh deploys, and the first
# thing a run does is fast-forward the tree that source lives in.
#
# MEASURED 2026-08-19: the run that DELIVERED #620 — the commit that added
# verify_managed_currency — printed no currency line at all and exited 0. An
# immediate second run printed "347 repo-sourced examined, 0 stale". The CONVERGE
# payload is expanded into a variable near the top of the file, long before the
# fast-forward, so the run that ships a change to ship.sh verifies with the copy
# it just replaced.
#
# MEASURED 2026-08-20, the second half: bash re-reads a script FROM A BYTE
# OFFSET. A 46 KB script that overwrites itself as its first act, replaced by a
# SHORTER file, simply STOPS at the offset and exits 0 — every later step
# skipped, silently. Replaced by a same-length file it resumes inside the NEW
# bytes and runs a splice of the two versions. Wrapping the body in a brace group
# made the same experiment run the original script to completion.
#
# The fixture commits the REAL ship.sh into the throwaway repo and has the ahead
# commit rewrite it, so the run's own fast-forward replaces the file executing
# it. The version marker is planted INSIDE the CONVERGE payload — see
# Repo.ship_source for why no other placement can answer the question.
# --------------------------------------------------------------------------- #
def _shipver_sequence(out):
    return [int(m) for m in re.findall(r"SHIPVER=(\d+)", out)]


@pytest.fixture
def self_shipping(tmp_path):
    return Repo(tmp_path, ship_in_repo=True)


def _repo_ship(r):
    return r.work / "scripts" / "ship.sh"


def test_a_run_that_ships_a_change_to_ship_sh_verifies_with_the_new_copy(self_shipping):
    """🔴 THE REGRESSION TEST — #620's own delivery run, reproduced.

    The fast-forward replaces the running script. Before the fix the whole run —
    both legs and the final verdict — was computed by the superseded copy, and
    the operator got a green from code they had just replaced.

    The assertion is on the LAST marker, not on the absence of the first: the
    superseded copy's own per-host line is still printed (it had already run),
    and it is immediately disclaimed. What must never happen is the run ENDING on
    it.
    """
    ship = _repo_ship(self_shipping)
    assert "SHIPVER=1" in ship.read_text(), "fixture is not version-marked"

    rc, out = self_shipping.ship(script=ship)

    assert rc == 0, out
    seq = _shipver_sequence(out)
    assert seq, f"no version marker in the output at all — fixture is inert\n{out}"
    assert seq[-1] == 2, (
        f"the run ENDED on the superseded copy's payload (markers seen: {seq}). "
        f"Everything the operator was shown was computed by the code this very "
        f"run replaced.\n{out}"
    )
    assert "SUPERSEDED" in out, f"the supersession is not announced\n{out}"
    assert "re-executing the NEW copy" in out, f"no re-exec happened\n{out}"
    # ...and the file on disk really is the new one, so this is not a mislabel.
    assert "SHIPVER=2" in ship.read_text()


def test_an_in_place_overwrite_of_the_running_script_does_not_truncate_the_run(tmp_path):
    """🔴 The CORRUPTION half — a different bug from the staleness half above.

    🔴 IT IS NOT REACHED BY `git`, AND THE OBVIOUS INSTRUMENT SAYS OTHERWISE.
    Measured 2026-08-20: comparing st_ino across `git merge --ff-only` reports
    the SAME inode and reads as an in-place overwrite. Holding an OPEN FD across
    the same merge shows it is not — the fd still yields the OLD bytes with
    st_nlink=0, i.e. git UNLINKS AND RECREATES, and a freed inode NUMBER is just
    immediately reused. So a fast-forward cannot corrupt a running ship.sh; three
    end-to-end runs of the PRE-FIX script (incoming copy +27 KB, -30 KB, same
    size) all completed cleanly.

    What CAN corrupt it is a writer that truncates in place — `cp` without
    --remove-destination, `install`, a `>` redirect, an editor that rewrites
    rather than renames. Measured: bash then loses everything past that point and
    exits 0, at 45 of 45 swept offsets. This drives exactly that, from inside the
    local converge where a real writer would sit, using a `home-manager` stub
    that `cp`s a ~27 KB SHORTER copy over the running file exactly once.

    ⚠ SCOPE: for the git path this is an INVARIANT GUARD, not regression
    coverage — the brace group closes a latent class, and no measured run ever
    reached it through a fast-forward. For the in-place path it is a real
    regression test and it IS red without the wrapper.
    """
    r = Repo(tmp_path, ship_in_repo=True, ship_changes=False, ship_pad_lines=300)
    ship = _repo_ship(r)
    padded = len(ship.read_text())
    replacement = tmp_path / "shorter-ship.sh"
    replacement.write_text(Repo.ship_source(2, pad_lines=0))
    assert len(replacement.read_text()) < padded - 20000, (
        "the replacement is not meaningfully shorter, so bash's resume offset "
        "would still land inside the file and no truncation could occur"
    )

    once = tmp_path / "overwritten-once"
    stub = tmp_path / "hm-inplace"
    stub.mkdir()
    write_exec(
        stub / "home-manager",
        f'[ -e "{once}" ] && exit 0\n'
        f': > "{once}"\n'
        # 🔴 plain `cp` on purpose: --remove-destination would unlink first and
        # reproduce git's (safe) behaviour instead of the hazard under test.
        f'cp "{replacement}" "{ship}"\n'
        "exit 0\n",
    )

    rc, out = r.ship(script=ship, SHIP_NO_SWITCH="0",
                     PATH=f"{stub}:{os.environ['PATH']}")

    assert once.exists(), (
        f"the stub never ran, so nothing overwrote the script and this test "
        f"proves nothing\n{out}"
    )
    assert len(ship.read_text()) < padded - 20000, "the stub did not shrink the file"
    assert "command not found" not in out, f"bash executed spliced garbage\n{out}"
    assert "syntax error" not in out, f"bash parsed spliced garbage\n{out}"
    # 🔴 The status is NOT the assertion: a byte-offset truncation exits 0 with
    # every later step simply never run. Reaching the verdict is the assertion.
    assert out.rstrip().splitlines()[-1].startswith("ship: converged + verified"), (
        f"the run never reached its verdict — truncated mid-flight and exited "
        f"{rc} with the remaining verification steps silently skipped\n{out}"
    )
    assert rc == 0, out
    assert _shipver_sequence(out)[-1] == 2, out


def test_the_self_check_reports_how_many_files_it_compared(repo):
    """🔴 POSITIVE CONTROL — "0 superseded" must carry its examined count.

    Identical in shape to "0 dangling out of 0 checked": a self-check wired to
    nothing also reports nothing superseded, and only the compared count tells
    the two apart. Both watched files (ship.sh and the lib it sources) must be in
    it, so a watch list that silently shrank to one is visible.
    """
    rc, out = repo.ship()

    assert rc == 0, out
    m = re.search(r"ship: self-check — (\d+) files compared \(([^)]*)\), (\d+) superseded", out)
    assert m, f"no self-check line with counts in output:\n{out}"
    assert int(m.group(1)) == 2, f"the watch list shrank: {m.group(2)}\n{out}"
    assert "ship.sh" in m.group(2) and "host-role.sh" in m.group(2), m.group(2)
    assert int(m.group(3)) == 0, out


def test_the_self_check_refuses_when_it_cannot_digest_its_own_source(repo, tmp_path):
    """🔴 REACHABILITY for the UNMEASURED guard.

    With no digest there is no answer, and "I could not look" must not print the
    same as "nothing changed" — that is the whole vacuous-zero failure, one
    question inward. Reached with a `cksum` that always fails; nothing else in
    ship.sh uses cksum, so no earlier branch can win.
    """
    shim_dir = tmp_path / "nocksum-shim"
    shim_dir.mkdir()
    write_exec(shim_dir / "cksum", "exit 1\n")
    # 🔴 Validate the instrument in BOTH directions before reading its verdict.
    broken = subprocess.run([str(shim_dir / "cksum")], input="x", capture_output=True, text=True)
    assert broken.returncode != 0, "the shim did not break cksum; this test proves nothing"
    real = subprocess.run(["cksum"], input="x", capture_output=True, text=True)
    assert real.returncode == 0 and real.stdout.strip(), (
        "positive control: the real cksum must produce a digest, or the guard "
        "would fire on every run and be a permanently-red gate"
    )

    rc, out = repo.ship(PATH=f"{shim_dir}:{os.environ['PATH']}")

    assert rc == 20, f"an unmeasurable self-check reported a verdict: rc={rc}\n{out}"
    assert "SELF-CHECK NOT MEASURED" in out, f"wrong guard fired\n{out}"
    assert "2 of 2 watched files" in out, f"no examined count on the refusal\n{out}"
    assert "converged + verified" not in out, out


def test_a_script_that_keeps_changing_stops_after_exactly_one_re_exec(tmp_path):
    """🔴 THE LOOP GUARD, driven by a writer that never stops.

    A re-exec on "my source changed" is an unbounded loop the moment something
    keeps changing that source. The driver here is a `home-manager` stub that
    appends a line to the running script on every switch — chosen because it
    runs INSIDE the local converge, i.e. at exactly the point a real
    fast-forward would write. (It is a driver for the guard, not a claim that
    home-manager rewrites repos.)

    Without the counter this never terminates; the subprocess timeout in
    Repo.ship is what turns that into a failure instead of a hung suite. The
    switch-count assertion is the other half: "it stopped" is not enough, it has
    to stop after the ONE re-exec the budget allows.

    ship_changes=False so the fast-forward leaves ship.sh alone — the stub is
    then the only writer, and a failure here cannot be blamed on the other one.
    """
    r = Repo(tmp_path, ship_in_repo=True, ship_changes=False)
    ship = _repo_ship(r)
    counter = tmp_path / "switch-count"
    counter.write_text("")
    stub = tmp_path / "hm-stub"
    stub.mkdir()
    write_exec(
        stub / "home-manager",
        f'echo switch >> "{counter}"\n'
        f'echo "# rewritten $(wc -l < "{counter}")" >> "{ship}"\n'
        "exit 0\n",
    )
    before = ship.read_text()

    rc, out = r.ship(script=ship, SHIP_NO_SWITCH="0",
                     PATH=f"{stub}:{os.environ['PATH']}")

    switches = len(counter.read_text().split())
    assert switches >= 1, (
        f"the home-manager stub never ran, so nothing ever superseded the "
        f"script and this test proves nothing about the guard\n{out}"
    )
    assert ship.read_text() != before, "the stub did not actually rewrite the script"
    assert rc == 20, f"expected rc20 (refusing to loop), got {rc}\n{out}"
    assert "refusing to re-exec" in out, f"wrong guard fired\n{out}"
    assert out.count("re-executing the NEW copy") == 1, (
        f"expected exactly one re-exec before the refusal\n{out}"
    )
    assert switches == 2, (
        f"expected 2 converge passes (the original and one re-exec), got "
        f"{switches} — the budget is not being counted\n{out}"
    )
    assert "converged + verified" not in out, out


def test_the_remote_leg_is_sent_the_new_payload_after_a_re_exec(tmp_path):
    """🔴 The re-exec must happen BEFORE the remote leg, not after it.

    The remote host never runs ship.sh — it runs the CONVERGE string this host
    puts on the wire. A re-exec placed after the remote leg would leave the
    laptop running the superseded payload forever while the workbench looked
    fixed, which is the same shape as the 2026-08-10 laptop-only breakage.
    """
    r = Repo(tmp_path, second_host=True, ship_in_repo=True)
    shim = r.ssh_shim(tmp_path)
    ship = _repo_ship(r)

    rc, out = r.ship_both_hosts(shim, script=ship)

    assert rc == 0, out
    # Two hosts converged after the re-exec, so the NEW marker must appear twice
    # and be the last thing seen.
    assert _shipver_sequence(out).count(2) == 2, (
        f"one of the two hosts still ran the superseded payload "
        f"(markers: {_shipver_sequence(out)})\n{out}"
    )
    assert _shipver_sequence(out)[-1] == 2, out
    # 🔴 ORDER, not just outcome. A self-check placed AFTER the remote leg still
    # ends with both hosts on the new payload — the re-exec simply redoes
    # everything — so the marker counts above cannot tell the two placements
    # apart. What can: the remote host must be visited ONCE, after the re-exec,
    # never once with each payload. (Measured: this mutant SURVIVED the marker
    # assertions alone.) On the real fleet the difference is a whole extra
    # `home-manager switch` on the laptop, run from superseded logic.
    assert out.count("=== remote (") == 1, (
        f"the remote host was converged {out.count('=== remote (')} times — the "
        f"re-exec is happening AFTER the remote leg, so the laptop is switched "
        f"once with the superseded payload before being redone\n{out}"
    )
    assert out.index("re-executing the NEW copy") < out.index("=== remote ("), (
        f"the re-exec does not precede the remote leg\n{out}"
    )
    assert "the remote leg has NOT run yet" in out, (
        f"the re-exec did not announce that it precedes the remote leg\n{out}"
    )
    assert "2 hosts compared" in out, out


def test_the_body_is_one_brace_group_that_ends_in_exit():
    """🔴 STRUCTURAL pin for the anti-splice wrapper.

    The behavioural coverage is
    test_an_in_place_overwrite_of_the_running_script_does_not_truncate_the_run;
    this is here because the wrapper is two easily-deleted characters whose
    absence changes nothing on any run that does NOT have its script overwritten
    in place, so a regression would sit invisible indefinitely.

    Anything after the closing brace is read from the file AFTER the run has
    mutated it, so the brace must be last and the `exit` must be inside it.
    """
    lines = [ln for ln in SHIP.read_text().splitlines() if ln.strip()]
    assert lines[-1].strip() == "}", (
        f"ship.sh must END with the closing brace of its body group; last line "
        f"is {lines[-1]!r}"
    )
    assert lines[-2].strip() == 'exit "$rc"', (
        f"the body group must end in an exit, or bash resumes reading the "
        f"(possibly replaced) file past the brace; got {lines[-2]!r}"
    )
    code = [ln for ln in lines if not ln.strip().startswith("#")]
    assert code[0].strip() == "{", (
        f"the body group must open before the first executable line, or the "
        f"statements ahead of it are still re-readable; got {code[0]!r}"
    )
