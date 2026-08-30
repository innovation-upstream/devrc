#!/usr/bin/env python3
"""The SEAM: does a real `git commit` actually come out stamped?

🔴 WHY THIS FILE EXISTS SEPARATELY FROM test_session_trailer.py. That file tests
`session_trailer.py` in isolation, and every one of its 32 tests can pass while
the shipped feature does nothing — because none of them runs `git`, installs a
hook, or reads a commit message that git itself produced. RULES.md: "Verified in
isolation is the new vacuous green — the defect lives in the SEAM nobody owns."
The seam here has four owners (the installer, git's hook contract, the hook's
path arithmetic, and the module) and no unit test crosses more than one.

🔴 WHY THE PID IS INJECTED. The hook resolves its session from its own /proc
ancestry. Under this repo's dev-host gate the test process genuinely HAS a Claude
ancestor, so an un-injected test would pass here and be structurally unable to
run in the nix sandbox — green in one tier, vacuous in the other, which is the
two-tier blindness CLAUDE.md documents. Injecting the pid makes the seam assert
the same thing in both tiers.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(_HERE, os.pardir)))
from testlib import mockbin  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
IMPL = REPO / "scripts" / "git-hooks" / "prepare_commit_msg.py"
INSTALLER = REPO / "scripts" / "install-session-stamp.sh"
LIB = REPO / "scripts" / "lib"


def state_root_for(repo) -> Path:
    """Per-test state root, a sibling of the repo.

    🔴 The state deliberately no longer lives in the repo's git dir. The
    recording hook resolves no git dir at all, because this repo mandates the
    `-C <path>` form over `cd`, and a cwd-derived git dir wrote state against the
    WRONG repository. The pid is the entire key.
    """
    return Path(repo).parent / "state"


def git(repo, *args, env=None):
    e = dict(os.environ)
    e.update({
        "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@e",
        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@e",
        "DEVRC_SESSION_TRAILER_ROOT": str(state_root_for(repo)),
    })
    if env:
        e.update(env)
    return subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True, env=e)


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "r"
    r.mkdir()
    assert git(r, "init", "-q", "-b", "main").returncode == 0
    (r / "f.txt").write_text("x\n")
    git(r, "add", "f.txt")
    return r


def install(repo):
    out = subprocess.run([str(INSTALLER), "--repo", str(repo), "--apply"],
                         capture_output=True, text=True)
    assert out.returncode == 0, out.stdout + out.stderr
    return out.stdout


def record(repo, session_id, pid):
    """Write the state the recording hook would have written.

    No `starttime` key: the hook records one, and `lookup()` only enforces the
    pin when it is present. Omitting it here keeps the seam test about the SEAM
    (git -> hook -> message) rather than re-testing the recycle guard, which
    test_session_trailer.py owns with an injected process tree.
    """
    d = state_root_for(repo)
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{pid}.json").write_text(json.dumps(
        {"session_id": session_id, "claude_pid": pid, "written_at": 0}))
    return str(d)


def last_message(repo):
    return git(repo, "log", "-1", "--format=%B").stdout


class TestTheSeam:
    def test_a_commit_is_stamped_end_to_end(self, repo):
        """🔴 THE WHOLE FEATURE, through git's own hook machinery."""
        install(repo)
        record(repo, "seam-session-1", 4242)
        out = git(repo, "commit", "-q", "-m", "feat: a thing",
                  env={"DEVRC_SESSION_TRAILER_PID": "4242"})
        assert out.returncode == 0, out.stderr
        assert "Claude-Session-Id: seam-session-1" in last_message(repo)

    def test_the_positive_control_can_move(self, repo):
        """A DIFFERENT id must produce a DIFFERENT stamp.

        Without this, a hook that hardcoded any string would satisfy the test
        above. RULES.md: feed a value the constant cannot equal and watch the
        output move.
        """
        install(repo)
        record(repo, "totally-other-id", 4243)
        git(repo, "commit", "-q", "-m", "feat: a thing",
            env={"DEVRC_SESSION_TRAILER_PID": "4243"})
        msg = last_message(repo)
        assert "Claude-Session-Id: totally-other-id" in msg
        assert "seam-session-1" not in msg

    def test_an_uninstalled_repo_is_left_byte_identical(self, repo):
        """The NEGATIVE control: no install => no stamp, so a green above is
        about the hook rather than about something else adding trailers."""
        record(repo, "seam-session-1", 4242)
        git(repo, "commit", "-q", "-m", "feat: a thing",
            env={"DEVRC_SESSION_TRAILER_PID": "4242"})
        assert "Claude-Session-Id" not in last_message(repo)

    def test_a_commit_with_no_recorded_session_is_left_alone(self, repo):
        """A human's commit. Installed hook, no state => unchanged message."""
        install(repo)
        git(repo, "commit", "-q", "-m", "feat: a thing",
            env={"DEVRC_SESSION_TRAILER_PID": "999999"})
        assert "Claude-Session-Id" not in last_message(repo)

    def test_amending_does_not_accrete_a_second_trailer(self, repo):
        """🔴 prepare-commit-msg runs again on --amend."""
        install(repo)
        record(repo, "seam-session-1", 4242)
        env = {"DEVRC_SESSION_TRAILER_PID": "4242"}
        git(repo, "commit", "-q", "-m", "feat: a thing", env=env)
        git(repo, "commit", "-q", "--amend", "--no-edit", env=env)
        git(repo, "commit", "-q", "--amend", "--no-edit", env=env)
        assert last_message(repo).count("Claude-Session-Id:") == 1

    def test_the_hook_never_blocks_a_commit_when_its_lib_is_missing(self, repo):
        """FAIL-OPEN. A broken deploy must cost a trailer, never a commit."""
        install(repo)
        record(repo, "seam-session-1", 4242)
        out = git(repo, "commit", "-q", "-m", "feat: a thing",
                  env={"DEVRC_SESSION_TRAILER_PID": "4242",
                       "DEVRC_SESSION_TRAILER_LIB": "/nonexistent/path"})
        assert out.returncode == 0, out.stderr
        assert "Claude-Session-Id" not in last_message(repo)


class TestTheInstaller:
    def test_dry_run_changes_nothing(self, repo):
        out = subprocess.run([str(INSTALLER), "--repo", str(repo)],
                             capture_output=True, text=True)
        assert out.returncode == 0
        assert "DRY-RUN" in out.stdout
        common = git(repo, "rev-parse", "--path-format=absolute",
                     "--git-common-dir").stdout.strip()
        assert not (Path(common) / "hooks" / "prepare-commit-msg").exists()

    def test_it_refuses_to_clobber_a_foreign_hook(self, repo):
        common = git(repo, "rev-parse", "--path-format=absolute",
                     "--git-common-dir").stdout.strip()
        hooks = Path(common) / "hooks"
        hooks.mkdir(parents=True, exist_ok=True)
        foreign = hooks / "prepare-commit-msg"
        # write_exec owns the shebang — a call site that supplies its own is how
        # #!/usr/bin/env comes back (testlib/mockbin.py:59, pinned by
        # test_runtime_shebangs.py).
        mockbin.write_exec(foreign, "echo someone elses hook\n")
        out = subprocess.run([str(INSTALLER), "--repo", str(repo), "--apply"],
                             capture_output=True, text=True)
        assert out.returncode == 4, out.stdout + out.stderr
        # And it must not have been touched.
        assert "someone elses hook" in foreign.read_text()

    def test_install_is_idempotent(self, repo):
        install(repo)
        out = subprocess.run([str(INSTALLER), "--repo", str(repo), "--apply"],
                             capture_output=True, text=True)
        assert out.returncode == 0
        assert "already installed" in out.stdout

    def test_uninstall_removes_only_a_devrc_managed_hook(self, repo):
        install(repo)
        out = subprocess.run([str(INSTALLER), "--repo", str(repo), "--uninstall"],
                             capture_output=True, text=True)
        assert out.returncode == 0
        common = git(repo, "rev-parse", "--path-format=absolute",
                     "--git-common-dir").stdout.strip()
        assert not (Path(common) / "hooks" / "prepare-commit-msg").exists()

    def test_a_non_repo_is_refused_with_its_own_code(self, tmp_path):
        out = subprocess.run([str(INSTALLER), "--repo", str(tmp_path), "--apply"],
                             capture_output=True, text=True)
        assert out.returncode == 3, out.stdout + out.stderr


class TestWorktreesShareTheInstall:
    def test_one_install_serves_a_worktree_too(self, repo, tmp_path):
        """🔴 The claim that makes ONE install enough on a box with ~117
        worktrees: hooks live in the COMMON git dir, which worktrees share."""
        install(repo)
        git(repo, "commit", "-q", "-m", "base",
            env={"DEVRC_SESSION_TRAILER_PID": "1"})
        wt = tmp_path / "wt"
        assert git(repo, "worktree", "add", "-q", str(wt), "-b", "side").returncode == 0
        record(repo, "shared-install-id", 4444)
        (wt / "g.txt").write_text("y\n")
        git(wt, "add", "g.txt")
        out = git(wt, "commit", "-q", "-m", "from the worktree",
                  env={"DEVRC_SESSION_TRAILER_PID": "4444"})
        assert out.returncode == 0, out.stderr
        assert "Claude-Session-Id: shared-install-id" in last_message(wt)


class TestTheInterpreterCanNeverBlockACommit:
    """🔴 ROUND-2 REGRESSION GUARD — the round-1 audit's deploy-blocking finding.

    The first version of this hook was `#!/usr/bin/env python3` and claimed in
    four docstrings that it could never block a commit. MEASURED FALSE:

        hook present, interpreter unresolvable :  rc=1, 0 commits made
        control, same repo, hook removed       :  rc=0, 1 commit  made

    No try/except inside the Python could catch it, because the Python never
    ran — the exec failed and git treated that as a hook failure. Two reachable
    paths on this host: a `home-manager switch` blanks ~/.nix-profile for ~1s so
    anything invoked by bare name dies, and the nix build sandbox has no
    /usr/bin/env at all (which is how the merge-gating tier caught it).

    🔴 THE WRAPPER IS GENERATED BY THE INSTALLER, NOT SHIPPED — see that script's
    header for why (devrc#1083 finding 9: a shipped file must be symlinked, and a link
    installed from an ephemeral agent worktree dangles when the worktree goes).
    These tests therefore install into a real repo and exercise the GENERATED
    hook, which is the artifact git actually execs.
    """

    def installed_hook(self, repo) -> Path:
        common = git(repo, "rev-parse", "--path-format=absolute",
                     "--git-common-dir").stdout.strip()
        return Path(common) / "hooks" / "prepare-commit-msg"

    def test_the_generated_hook_shebang_is_bin_sh(self, repo):
        """Structural pin on the artifact git actually execs. /bin/sh is the one
        interpreter always present — the sandbox provides it and
        testlib/mockbin.py already pins it as SH."""
        install(repo)
        first = self.installed_hook(repo).read_text().splitlines()[0]
        assert first == "#!/bin/sh", (
            "the hook's shebang decides whether a commit can be made at all; "
            f"got {first!r}")

    def test_it_exits_0_when_no_python_can_be_found(self, repo, tmp_path):
        """THE GUARD. With no python3 resolvable, the hook must do nothing and
        exit 0 rather than hand git a failure."""
        install(repo)
        hook = self.installed_hook(repo)
        msg = tmp_path / "COMMIT_EDITMSG"
        msg.write_text("feat: a thing\n")
        empty_bin = tmp_path / "emptybin"
        empty_bin.mkdir()
        out = subprocess.run(
            [str(hook), str(msg)],
            capture_output=True, text=True,
            env={"PATH": str(empty_bin), "HOME": str(tmp_path)},
        )
        assert out.returncode == 0, (out.stdout, out.stderr)
        assert msg.read_text() == "feat: a thing\n", "message must be untouched"

    def test_it_exits_0_when_the_python_half_is_missing(self, repo, tmp_path):
        """A partial deploy (wrapper present, impl absent) must also be inert."""
        install(repo)
        hook = self.installed_hook(repo)
        msg = tmp_path / "COMMIT_EDITMSG"
        msg.write_text("feat: a thing\n")
        out = subprocess.run(
            [str(hook), str(msg)], capture_output=True, text=True,
            env={**os.environ, "DEVRC_SESSION_TRAILER_LIB": "/nonexistent"},
        )
        assert out.returncode == 0
        # The impl is present here, but no session is recorded, so nothing is
        # written — the point is only that it did not FAIL.
        assert "Claude-Session-Id" not in msg.read_text()

    def test_a_commit_survives_a_python_that_exits_nonzero(self, repo, tmp_path):
        """Even a python that fails outright must not reach git as a failure."""
        install(repo)
        record(repo, "seam-session-1", 4242)
        angry = mockbin.write_exec(tmp_path / "angry-python", "exit 3\n")
        out = git(repo, "commit", "-q", "-m", "feat: a thing",
                  env={"DEVRC_SESSION_TRAILER_PID": "4242",
                       "DEVRC_SESSION_TRAILER_PYTHON": str(angry)})
        assert out.returncode == 0, out.stderr
        assert "Claude-Session-Id" not in last_message(repo)


class TestTheMessageFileIsNeverDestroyed:
    """🔴 ROUND-2 fix, 🔴 ROUND-3 test. `open(path,"w")` truncates BEFORE writing,
    and the write can fail. Measured: a 77-byte message became 0 bytes, the hook
    exited 0, and git then refused with "Aborting commit due to empty commit
    message" — the operator lost the message AND the commit.

    ⚠ THE ROUND-2 VERSION OF THIS TEST WAS VACUOUS, and an audit mutation-proved
    it: it pointed the impl at a path whose PARENT did not exist, so the initial
    `open(..., "r")` raised and the code returned before reaching any write path
    at all. Reverting the fix — restoring the truncating write — left the suite
    86/86 GREEN. The assertion was then made against a different file the hook had
    never been pointed at.

    What discriminates is the INODE: writing beside and renaming replaces the
    file, so its inode changes; truncate-in-place keeps it. That is a property of
    the mechanism rather than of the happy path, so it stays red under the mutant.
    """

    def test_the_message_is_written_by_rename_not_by_truncation(self, repo, tmp_path):
        install(repo)
        record(repo, "seam-session-1", 4242)
        msg = tmp_path / "COMMIT_EDITMSG"
        msg.write_text("feat: a thing\n\nA body worth not losing.\n")
        before = msg.stat().st_ino
        out = subprocess.run(
            [sys.executable, str(IMPL), str(msg)],
            capture_output=True, text=True,
            env={**os.environ,
                 "DEVRC_SESSION_TRAILER_PID": "4242",
                 "DEVRC_SESSION_TRAILER_ROOT": str(state_root_for(repo))},
        )
        assert out.returncode == 0, out.stderr
        text = msg.read_text()
        # It really did stamp — otherwise the inode check below is vacuous too.
        assert "Claude-Session-Id: seam-session-1" in text
        assert "A body worth not losing." in text
        assert msg.stat().st_ino != before, (
            "the message file was modified IN PLACE, which means it was truncated "
            "before writing — a failed write there empties the operator's commit "
            "message and loses the commit")

    def test_a_message_the_hook_does_not_stamp_is_left_completely_alone(self, repo, tmp_path):
        """Negative control: no session recorded => no write of any kind, so the
        inode assertion above is about the WRITE PATH, not about merely running."""
        install(repo)
        msg = tmp_path / "COMMIT_EDITMSG"
        original = "feat: a thing\n"
        msg.write_text(original)
        before = msg.stat().st_ino
        out = subprocess.run(
            [sys.executable, str(IMPL), str(msg)],
            capture_output=True, text=True,
            env={**os.environ,
                 "DEVRC_SESSION_TRAILER_PID": "999999",
                 "DEVRC_SESSION_TRAILER_ROOT": str(state_root_for(repo))},
        )
        assert out.returncode == 0
        assert msg.read_text() == original
        assert msg.stat().st_ino == before


class TestTheGeneratedWrapperCannotBeInjected:
    """🔴 ROUND-3 — the round-2 audit's most serious finding.

    `--source` used to be interpolated raw into an UNQUOTED heredoc, so a path
    containing a double quote or a backtick emitted shell that RAN ON EVERY
    `git commit` (both verified by the auditor via a marker file), and one
    containing `$` expanded at hook time to a path that did not exist, silently
    killing the feature. The heredoc is quoted now and the one interpolated value
    goes through `printf %q`.
    """

    def _install_from(self, repo, source_root, env=None):
        return subprocess.run(
            [str(INSTALLER), "--repo", str(repo), "--source", str(source_root),
             "--apply"],
            capture_output=True, text=True, env={**os.environ, **(env or {})})

    def _fake_checkout(self, tmp_path, name):
        """A --source whose NAME carries the hostile character."""
        root = tmp_path / name
        (root / "scripts" / "git-hooks").mkdir(parents=True)
        (root / "scripts" / "git-hooks" / "prepare_commit_msg.py").write_text(
            "import sys; sys.exit(0)\n")
        return root

    # 🔴 REAL PAYLOADS, NOT TEXTBOOK CHARACTERS. A first version used names like
    # `quote"root`, which merely BREAKS the quoting — it executes nothing, so the
    # guard passed against the vulnerable installer and proved nothing. These
    # close the assignment and run a command, which is what the vulnerability
    # actually was. (A directory name may contain anything but "/" and NUL.)
    @pytest.mark.parametrize("name,marker", [
        ('a";touch "$CANARY";x="b', "PWNED_QUOTE"),
        ("a`touch $CANARY`b", "PWNED_TICK"),
        ("a$(touch $CANARY)b", "PWNED_SUBST"),
        ("space root", "PWNED_SPACE"),
    ])
    def test_a_hostile_source_path_cannot_execute_anything(
            self, repo, tmp_path, name, marker):
        # 🔴 CANARY IS SET FOR BOTH PHASES, and there is NO early return.
        # An earlier version set it only when running the hook and bailed out
        # when the install failed — so the payload's `touch "$CANARY"` ran with
        # an EMPTY argument during install (a no-op) and any install failure
        # passed the test vacuously. Measured: that version survived the
        # vulnerable installer. Injection can fire at EITHER phase — heredoc
        # expansion happens at install time — so both are armed and both checked.
        canary = tmp_path / marker
        env = {"CANARY": str(canary)}
        root = self._fake_checkout(tmp_path, name)
        out = self._install_from(repo, root, env=env)
        assert not canary.exists(), (
            f"injected shell from --source {name!r} ran during INSTALL")

        hooks = Path(git(repo, "rev-parse", "--path-format=absolute",
                         "--git-common-dir").stdout.strip()) / "hooks"
        hook = hooks / "prepare-commit-msg"
        if out.returncode != 0:
            # 🔴 A REFUSAL IS SAFE ONLY IF IT IS A REFUSAL. An earlier version
            # returned here with no assertion at all, so an installer patched to
            # `exit 9` before generating anything PASSED this test — the same
            # vacuous-early-return trap already fixed in the sibling test and
            # left here. Refusing must (a) leave no hook to run the payload
            # later, and (b) be one of this script's own documented codes, not
            # an arbitrary crash on the way to writing one.
            assert not hook.exists(), (
                "install failed yet still wrote a hook built from a hostile path")
            assert out.returncode in (2, 3, 4, 5), (
                f"installer exited {out.returncode}, which is not one of its "
                f"documented refusal codes: {out.stdout}{out.stderr}")
            return
        msg = tmp_path / "COMMIT_EDITMSG"
        msg.write_text("feat: x\n")
        subprocess.run([str(hook), str(msg)], capture_output=True, text=True,
                       env={**os.environ, **env})
        assert not canary.exists(), (
            f"the generated hook EXECUTED injected shell from --source {name!r}")
        # …and the path really did survive, so this is not a vacuous pass.
        # 🔴 Compare the RESOLVED value, not the raw substring: `printf %q` escapes
        # the hostile characters, which is precisely the fix, so the literal path
        # is deliberately NOT present in the file.
        impl_line = [ln for ln in hook.read_text().splitlines()
                     if ln.startswith("impl=")]
        assert impl_line, "the generated hook has no impl= line"
        resolved = subprocess.run(
            ["sh", "-c", f'{impl_line[0]}; printf %s "$impl"'],
            capture_output=True, text=True)
        assert resolved.returncode == 0, resolved.stderr
        assert resolved.stdout == str(
            root / "scripts" / "git-hooks" / "prepare_commit_msg.py"), (
                f"impl resolved to {resolved.stdout!r}")

    def test_the_generated_hook_is_valid_shell_for_every_hostile_path(
            self, repo, tmp_path):
        """A syntactically invalid hook is 🔴-1 all over again: git refuses the
        commit. `sh -n` parses without executing."""
        root = self._fake_checkout(tmp_path, 'quote"and`tick$var')
        out = self._install_from(repo, root)
        if out.returncode != 0:
            return
        hook = Path(git(repo, "rev-parse", "--path-format=absolute",
                        "--git-common-dir").stdout.strip()) / "hooks" / "prepare-commit-msg"
        chk = subprocess.run(["sh", "-n", str(hook)], capture_output=True, text=True)
        assert chk.returncode == 0, chk.stderr


class TestTheInstallerCannotStrandTheRepo:
    """🔴 ROUND-3 — audit 🟡-A and 🟡-G, which COMPOUND: a truncated wrapper both
    refuses every commit and leaves the installer unable to replace it."""

    def _hooks(self, repo) -> Path:
        return Path(git(repo, "rev-parse", "--path-format=absolute",
                        "--git-common-dir").stdout.strip()) / "hooks"

    def test_a_marked_hook_with_no_impl_line_does_not_abort_the_installer(self, repo):
        """`grep … | sed` under `set -euo pipefail` killed the script with an
        undocumented rc 1 — including on the READ-ONLY default run."""
        hooks = self._hooks(repo)
        hooks.mkdir(parents=True, exist_ok=True)
        mockbin.write_exec(
            hooks / "prepare-commit-msg",
            "# Generated by devrc scripts/install-session-stamp.sh\nexit 0\n")
        dry = subprocess.run([str(INSTALLER), "--repo", str(repo)],
                             capture_output=True, text=True)
        assert dry.returncode in (0, 4), (dry.returncode, dry.stdout, dry.stderr)
        forced = subprocess.run(
            [str(INSTALLER), "--repo", str(repo), "--apply", "--force"],
            capture_output=True, text=True)
        assert forced.returncode == 0, (forced.returncode, forced.stdout, forced.stderr)
        assert "^impl=" not in forced.stdout

    def test_force_can_replace_a_truncated_wrapper(self, repo):
        """The compounded state: an executable but INVALID hook. --force must
        repair it rather than leaving `rm` as the only recovery."""
        hooks = self._hooks(repo)
        hooks.mkdir(parents=True, exist_ok=True)
        install(repo)
        target = hooks / "prepare-commit-msg"
        # 🔴 CUT WHERE IT ACTUALLY BREAKS THE SHELL. An earlier version kept
        # `full[:8]` — eight COMMENT lines — which `sh -n` happily accepts, so the
        # fixture never built the invalid hook its own docstring described and the
        # assertion below could not fail. A 0-byte file parses clean too, so
        # "truncated" is not by itself a broken-shell state. Cut just past the
        # first `if`, leaving an unterminated block, and ASSERT the fixture is
        # genuinely broken before asserting the installer repairs it.
        full = target.read_text().split("\n")
        cut = next(i for i, ln in enumerate(full) if ln.startswith("if "))
        target.write_text("\n".join(full[:cut + 1]) + "\n")
        assert subprocess.run(["sh", "-n", str(target)],
                              capture_output=True).returncode != 0, (
            "the fixture did not produce an invalid hook, so this test cannot fail")
        out = subprocess.run(
            [str(INSTALLER), "--repo", str(repo), "--apply", "--force"],
            capture_output=True, text=True)
        assert out.returncode == 0, (out.stdout, out.stderr)
        assert subprocess.run(["sh", "-n", str(target)],
                              capture_output=True).returncode == 0


class TestOwnershipIsStructuralNotASubstring:
    """🔴 ROUND-3 — audit 🟡-F. An unanchored marker grep DELETED a foreign hook
    that merely quoted the marker (measured, with a negative control)."""

    def _hooks(self, repo) -> Path:
        return Path(git(repo, "rev-parse", "--path-format=absolute",
                        "--git-common-dir").stdout.strip()) / "hooks"

    def test_a_foreign_hook_quoting_the_marker_is_not_uninstalled(self, repo):
        """🔴 Kills a mutant that UN-ANCHORS the marker grep: the marker appears
        only mid-line, so an unanchored match would wrongly claim this hook.

        ⚠ This fixture covers the ANCHOR half ONLY. An earlier docstring here
        claimed it exercised "BOTH halves"; that was measured FALSE — dropping
        the `^impl=` requirement leaves this test GREEN. The `impl=` half is
        covered by TestOwnershipDiscriminatesBOTHHalves below, which is why that
        class exists and must not be deleted as redundant."""
        hooks = self._hooks(repo)
        hooks.mkdir(parents=True, exist_ok=True)
        foreign = hooks / "prepare-commit-msg"
        mockbin.write_exec(foreign, (
            "impl=/somewhere/of/my/own\n"
            "  # cribbed from: Generated by devrc scripts/install-session-stamp.sh\n"
            "echo mine\n"))
        out = subprocess.run([str(INSTALLER), "--repo", str(repo), "--uninstall"],
                             capture_output=True, text=True)
        assert out.returncode == 0
        assert foreign.exists(), "a foreign hook was deleted for quoting the marker"
        assert "echo mine" in foreign.read_text()

    def test_our_own_hook_IS_uninstalled(self, repo):
        """Positive control — the anchoring did not simply stop matching."""
        install(repo)
        target = self._hooks(repo) / "prepare-commit-msg"
        assert target.exists()
        out = subprocess.run([str(INSTALLER), "--repo", str(repo), "--uninstall"],
                             capture_output=True, text=True)
        assert out.returncode == 0
        assert "UNINSTALLED" in out.stdout
        assert not target.exists()


class TestSelfRepairIsScopedToOurOwnHook:
    """🔴 ROUND-5 — the round-4 audit's deploy-blocking finding, plus the guard
    that was missing for the behaviour round 4 added.

    Round 4 let a hook that merely LOOKS structurally like ours (marker line +
    `impl=` line, no end sentinel) be overwritten with no `--force`. Measured
    side by side on a third-party hook derived from devrc's wrapper — the case
    `is_ours`'s own comment records as seen in the wild:

        before round 4 : --apply -> rc 4, file UNCHANGED
        round 4        : --apply -> rc 0, file OVERWRITTEN, their body gone

    That re-opened, one axis over, the same "destroys something that is not
    ours" class the same commit removed `is_legacy_ours` to close. Self-repair
    is now scoped to a hook whose `impl=` names THIS checkout.
    """

    def _hooks(self, repo) -> Path:
        return Path(git(repo, "rev-parse", "--path-format=absolute",
                        "--git-common-dir").stdout.strip()) / "hooks"

    def test_a_lookalike_third_party_hook_is_not_overwritten(self, repo):
        hooks = self._hooks(repo)
        hooks.mkdir(parents=True, exist_ok=True)
        target = hooks / "prepare-commit-msg"
        body = (
            "# Generated by devrc scripts/install-session-stamp.sh\n"
            "impl=/opt/mytool/stamp.py\n"
            "echo MY_OWN_HOOK_BODY\n")
        mockbin.write_exec(target, body)
        out = subprocess.run([str(INSTALLER), "--repo", str(repo), "--apply"],
                             capture_output=True, text=True)
        assert out.returncode == 4, (out.returncode, out.stdout, out.stderr)
        assert "MY_OWN_HOOK_BODY" in target.read_text(), (
            "a third-party hook that merely looks like ours was overwritten "
            "without --force")

    def test_our_own_truncated_hook_IS_repaired_without_force(self, repo):
        """Positive control — the scoping did not simply disable self-repair.
        This is the behaviour round 4 added and shipped with no test: deleting
        `FORCE=1` left the whole suite green."""
        install(repo)
        target = self._hooks(repo) / "prepare-commit-msg"
        full = target.read_text().split("\n")
        cut = next(i for i, ln in enumerate(full) if ln.startswith("if "))
        target.write_text("\n".join(full[:cut + 1]) + "\n")
        assert subprocess.run(["sh", "-n", str(target)],
                              capture_output=True).returncode != 0
        out = subprocess.run([str(INSTALLER), "--repo", str(repo), "--apply"],
                             capture_output=True, text=True)
        assert out.returncode == 0, (out.stdout, out.stderr)
        assert subprocess.run(["sh", "-n", str(target)],
                              capture_output=True).returncode == 0

    def test_the_repair_run_does_not_also_claim_another_checkout(self, repo):
        """🟡-5: the repair path fell through into the re-point branch, so one
        run printed "repairing", then "points at another checkout" naming this
        very checkout, then "Re-point it with --force" — and then did it without
        --force. Three contradictory lines, and it made the dangerous case
        log-indistinguishable from the benign one."""
        install(repo)
        target = self._hooks(repo) / "prepare-commit-msg"
        full = target.read_text().split("\n")
        cut = next(i for i, ln in enumerate(full) if ln.startswith("if "))
        target.write_text("\n".join(full[:cut + 1]) + "\n")
        out = subprocess.run([str(INSTALLER), "--repo", str(repo), "--apply"],
                             capture_output=True, text=True)
        assert out.returncode == 0
        assert "PARTIAL WRITE" in out.stdout, out.stdout
        assert "points at another checkout" not in out.stdout, out.stdout
        assert "is not ours" not in out.stdout, out.stdout


class TestOwnershipDiscriminatesBOTHHalves:
    """🔴 ROUND-5 — audit 🟡-3. Round 4 replaced a fixture that discriminated the
    `impl=` half with one that discriminates the ANCHOR half, and claimed in its
    docstring that "BOTH halves are exercised". Measured, that was false: net
    coverage of `is_ours` did not increase, it rotated. Both fixtures are kept
    now, so each half has a mutant that kills it."""

    def _hooks(self, repo) -> Path:
        return Path(git(repo, "rev-parse", "--path-format=absolute",
                        "--git-common-dir").stdout.strip()) / "hooks"

    def test_marker_at_column_zero_but_NO_impl_line_is_not_ours(self, repo):
        """Kills a mutant that drops the `^impl=` requirement."""
        hooks = self._hooks(repo)
        hooks.mkdir(parents=True, exist_ok=True)
        foreign = hooks / "prepare-commit-msg"
        mockbin.write_exec(foreign, (
            "# Generated by devrc scripts/install-session-stamp.sh\n"
            "echo NO_IMPL_LINE_HERE\n"))
        out = subprocess.run([str(INSTALLER), "--repo", str(repo), "--uninstall"],
                             capture_output=True, text=True)
        assert out.returncode == 0
        assert foreign.exists() and "NO_IMPL_LINE_HERE" in foreign.read_text()

    def test_impl_line_but_marker_only_MID_LINE_is_not_ours(self, repo):
        """Kills a mutant that un-anchors the marker grep."""
        hooks = self._hooks(repo)
        hooks.mkdir(parents=True, exist_ok=True)
        foreign = hooks / "prepare-commit-msg"
        mockbin.write_exec(foreign, (
            "impl=/somewhere/of/my/own\n"
            "  # cribbed from: Generated by devrc scripts/install-session-stamp.sh\n"
            "echo MID_LINE_MARKER\n"))
        out = subprocess.run([str(INSTALLER), "--repo", str(repo), "--uninstall"],
                             capture_output=True, text=True)
        assert out.returncode == 0
        assert foreign.exists() and "MID_LINE_MARKER" in foreign.read_text()


class TestSelfRepairRequiresTheHookToActuallyBeBroken:
    """🔴 ROUND-6 — the round-5 audit's finding, and the third time this exact
    class has been re-opened one axis narrower.

    Round 5 scoped self-repair to a hook whose `impl=` names THIS checkout. But
    `impl=` is a string anyone can write, and the justification for skipping
    `--force` is "this hook refuses every commit" — which the condition did not
    test. A hook someone derived from devrc's wrapper on this checkout (header
    kept, `impl=` kept, devrc's tail replaced with their own body) is VALID
    SHELL, refuses nothing, has no sentinel — and was destroyed with no --force.

    The discriminator is now a byte-exact PREFIX of what this run would
    generate — see TestEveryTruncationOfOurOwnHookIsRepaired. What makes THIS
    fixture discriminate is that a derived hook is not such a prefix; it
    diverges at the first line its author changed.
    """

    def _hooks(self, repo) -> Path:
        return Path(git(repo, "rev-parse", "--path-format=absolute",
                        "--git-common-dir").stdout.strip()) / "hooks"

    def _derived_hook(self, repo):
        """A third-party hook derived from ours: our header, our impl= naming
        THIS checkout, their body, no sentinel — and valid shell."""
        # 🔴 IMPL is passed as an ARGUMENT, not interpolated into the command
        # string. The earlier `f'printf "%q" "{IMPL}"'` let the shell expand the
        # path first — the exact injection `%q` exists to prevent, in the test
        # for a fix whose whole subject is that injection.
        impl = subprocess.run(
            ["bash", "-c", 'printf "%q" "$1"', "bash", str(IMPL)],
            capture_output=True, text=True).stdout
        target = self._hooks(repo) / "prepare-commit-msg"
        target.parent.mkdir(parents=True, exist_ok=True)
        mockbin.write_exec(target, (
            f"# Generated by devrc scripts/install-session-stamp.sh\n"
            f"impl={impl}\n"
            f"echo THIRD_PARTY_BODY_THAT_MUST_SURVIVE >&2\n"))
        return target

    def test_a_valid_derived_hook_is_not_destroyed_without_force(self, repo):
        target = self._derived_hook(repo)
        # The fixture must NOT be a byte-exact prefix of what the installer
        # would generate, or this test would be re-testing the prefix path
        # instead of the refusal. (It is valid shell too, which is what made the
        # since-removed `sh -n` proxy get this case wrong.)
        assert subprocess.run(["sh", "-n", str(target)],
                              capture_output=True).returncode == 0
        out = subprocess.run([str(INSTALLER), "--repo", str(repo), "--apply"],
                             capture_output=True, text=True)
        assert out.returncode == 4, (out.returncode, out.stdout, out.stderr)
        assert "THIRD_PARTY_BODY_THAT_MUST_SURVIVE" in target.read_text(), (
            "a valid, commit-serving hook was destroyed without --force")

    def test_a_hook_that_really_is_broken_IS_still_repaired(self, repo):
        """Positive control: the narrowing did not disable self-repair for the
        case it exists to serve."""
        install(repo)
        target = self._hooks(repo) / "prepare-commit-msg"
        full = target.read_text().split("\n")
        cut = next(i for i, ln in enumerate(full) if ln.startswith("if "))
        target.write_text("\n".join(full[:cut + 1]) + "\n")
        assert subprocess.run(["sh", "-n", str(target)],
                              capture_output=True).returncode != 0
        out = subprocess.run([str(INSTALLER), "--repo", str(repo), "--apply"],
                             capture_output=True, text=True)
        assert out.returncode == 0, (out.stdout, out.stderr)
        assert subprocess.run(["sh", "-n", str(target)],
                              capture_output=True).returncode == 0


class TestEveryTruncationOfOurOwnHookIsRepaired:
    """🔴 ROUND-7 — the round-6 audit swept every cut point of the generated
    wrapper and found the guard was right for only SOME of them.

    Round 6 used `sh -n` as the discriminator. Of the wrapper's cut points, the
    ones landing mid-`if` fail to parse and were repaired; the ones landing on a
    comment or after `fi` parse CLEANLY and were refused — with two false
    sentences ("points at another checkout" naming this very checkout, and "is
    not ours" about our own hook). In those the feature is silently dead:
    commits succeed, nothing is stamped, and the installer will not fix it.

    The discriminator is now a byte-exact PREFIX of what this run would
    generate, which is the exact question. This sweeps every cut point rather
    than sampling one, because sampling one is how round 6 passed.
    """

    def _hooks(self, repo) -> Path:
        return Path(git(repo, "rev-parse", "--path-format=absolute",
                        "--git-common-dir").stdout.strip()) / "hooks"

    def test_every_prefix_of_our_own_hook_is_repaired_without_force(self, repo):
        install(repo)
        target = self._hooks(repo) / "prepare-commit-msg"
        full = target.read_text()
        lines = full.split("\n")
        parses_clean = repaired = 0
        # From 2: a bare `#!/bin/sh` (cut=1) is a byte-exact prefix of our
        # output but carries no marker, and may be someone's deliberate no-op
        # hook — the installer refuses it on purpose.
        for cut in range(2, len(lines)):
            target.write_text("\n".join(lines[:cut]) + "\n")
            clean = subprocess.run(["sh", "-n", str(target)],
                                   capture_output=True).returncode == 0
            parses_clean += clean
            out = subprocess.run([str(INSTALLER), "--repo", str(repo), "--apply"],
                                 capture_output=True, text=True)
            assert out.returncode == 0, (
                f"cut at line {cut} (parses={clean}) was refused: {out.stdout}")
            assert target.read_text() == full, f"cut at line {cut} not restored"
            assert "points at another checkout" not in out.stdout, (
                f"cut at line {cut}: false claim about another checkout")
            repaired += 1
        # The sweep is only meaningful if it covered BOTH kinds of cut — a sweep
        # where everything failed to parse would not discriminate the old guard.
        assert parses_clean > 0, (
            "no cut point parsed cleanly, so this sweep cannot see the defect "
            "round 6 shipped")
        # NB: `repaired` counts iterations, not repairs — the per-iteration
        # `assert target.read_text() == full` above is what actually proves each
        # cut was restored. Kept only as a bound on the sweep's size.
        assert repaired == len(lines) - 2, (repaired, len(lines))

    def test_a_bare_shebang_stub_is_NOT_swallowed(self, repo):
        """🔴 The marker requirement inside `is_truncated_ours`, which the sweep
        above deliberately excludes and therefore cannot guard.

        A one-line hook IS a byte-exact prefix of our output — but it may be
        someone's deliberate no-op, disabling the hook on purpose. Replacing it
        with a working stamper would silently re-enable behaviour they turned
        off. Measured: without the marker requirement this is repaired.
        """
        install(repo)
        target = self._hooks(repo) / "prepare-commit-msg"
        mockbin.write_exec(target, "")          # exactly the shebang, nothing else
        before = target.read_text()
        out = subprocess.run([str(INSTALLER), "--repo", str(repo), "--apply"],
                             capture_output=True, text=True)
        assert out.returncode == 4, (out.returncode, out.stdout)
        assert target.read_text() == before, "a deliberate no-op stub was replaced"

    def test_a_hook_that_is_NOT_a_prefix_of_ours_is_still_refused(self, repo):
        """Negative control: the prefix test must not have become 'repair
        anything shorter'."""
        install(repo)
        target = self._hooks(repo) / "prepare-commit-msg"
        lines = target.read_text().split("\n")
        # our header, then a body we never wrote — shorter, but not a prefix
        target.write_text("\n".join(lines[:2]) + "\nimpl=/opt/other/x.py\necho MINE\n")
        out = subprocess.run([str(INSTALLER), "--repo", str(repo), "--apply"],
                             capture_output=True, text=True)
        assert out.returncode == 4, (out.returncode, out.stdout)
        assert "echo MINE" in target.read_text()


class TestTheInstallerLeavesNoLitterAndHonoursDryRun:
    """🔴 ROUND-8 — the round-7 audit found the cleanup trap was an UNGUARDED
    GUARD: deleting `trap cleanup_tmp EXIT` left all 37 tests green while leaking
    a scratch file on four paths. That is this ladder's signature defect landing
    on the very claim the commit said it closed.

    It also found the scratch file was being generated INTO `.git/hooks` before
    the mode branches, so the dry run created that directory when it did not
    exist — falsifying the installer's own "changes nothing unless --apply"
    header — and both the dry run and `--uninstall` aborted rc 1 (a code absent
    from the exit table) when the hooks dir was not writable.
    """

    def _hooks(self, repo) -> Path:
        return Path(git(repo, "rev-parse", "--path-format=absolute",
                        "--git-common-dir").stdout.strip()) / "hooks"

    def _run(self, repo, *args, tmpdir=None):
        env = {**os.environ}
        if tmpdir:
            env["TMPDIR"] = str(tmpdir)
        return subprocess.run([str(INSTALLER), "--repo", str(repo), *args],
                              capture_output=True, text=True, env=env)

    @pytest.mark.parametrize("args,setup", [
        (["--apply"], "clean"),
        (["--apply"], "installed"),      # the already-installed early exit
        (["--apply"], "foreign"),        # the rc-4 refusal
        ([], "clean"),                   # the dry run
        (["--uninstall"], "clean"),
    ])
    def test_no_scratch_file_survives_any_path(self, repo, tmp_path, args, setup):
        scratch = tmp_path / "scratch"
        scratch.mkdir()
        if setup == "installed":
            install(repo)
        elif setup == "foreign":
            hooks = self._hooks(repo)
            hooks.mkdir(parents=True, exist_ok=True)
            mockbin.write_exec(hooks / "prepare-commit-msg", "echo not ours\n")
        self._run(repo, *args, tmpdir=scratch)
        left = list(scratch.iterdir())
        assert left == [], f"scratch file(s) left behind on {args or ['dry-run']}: {left}"
        # …and nothing was littered into the hooks dir either.
        hooks = self._hooks(repo)
        if hooks.exists():
            assert not list(hooks.glob("*devrc-install*"))
            assert not list(hooks.glob("*devrc-session-stamp*"))

    def test_a_dry_run_does_not_even_create_the_hooks_directory(self, repo):
        """🔴 The installer's own header promises this. An earlier revision
        created the directory as a side effect of generating the wrapper before
        the mode branches, and the test named `test_dry_run_changes_nothing`
        passed anyway — it only asserted the hook FILE was absent, a name wider
        than its assertion."""
        hooks = self._hooks(repo)
        if hooks.exists():
            for f in hooks.iterdir():
                f.unlink()
            hooks.rmdir()
        assert not hooks.exists()
        out = self._run(repo)
        assert out.returncode == 0, (out.stdout, out.stderr)
        assert "DRY-RUN" in out.stdout
        assert not hooks.exists(), "the dry run created .git/hooks"

    @pytest.mark.parametrize("args", [[], ["--uninstall"]])
    def test_read_only_paths_do_not_abort_on_an_unwritable_hooks_dir(self, repo, args):
        """Neither path needs to write, so neither may fail because it cannot.
        Measured rc 1 before — a code this script's own exit table omits."""
        hooks = self._hooks(repo)
        hooks.mkdir(parents=True, exist_ok=True)
        hooks.chmod(0o500)
        try:
            out = self._run(repo, *args)
            assert out.returncode == 0, (out.returncode, out.stdout, out.stderr)
        finally:
            hooks.chmod(0o700)
