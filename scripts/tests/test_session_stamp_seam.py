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
    """🔴 ROUND-2 — audit 🟡-1. `open(path,"w")` truncates BEFORE writing, and the
    write can fail. Measured: a 77-byte message became 0 bytes, the hook exited
    0, and git then refused with "Aborting commit due to empty commit message" —
    so the operator lost the message AND the commit."""

    def test_a_failed_write_leaves_the_original_message_intact(self, repo, tmp_path):
        install(repo)
        record(repo, "seam-session-1", 4242)
        msg = tmp_path / "COMMIT_EDITMSG"
        original = "feat: a thing\n\nA body worth not losing.\n"
        msg.write_text(original)
        # Make the rename fail by pointing the temp path at an unwritable dir.
        impl = IMPL
        ro = tmp_path / "ro"
        ro.mkdir()
        ro.chmod(0o500)
        try:
            out = subprocess.run(
                [sys.executable, str(impl), str(ro / "nope" / "COMMIT_EDITMSG")],
                capture_output=True, text=True,
                env={**os.environ,
                     "DEVRC_SESSION_TRAILER_PID": "4242",
                     "DEVRC_SESSION_TRAILER_ROOT": str(state_root_for(repo))},
            )
            assert out.returncode == 0
        finally:
            ro.chmod(0o700)
        # The real message, untouched by the failed run.
        assert msg.read_text() == original
