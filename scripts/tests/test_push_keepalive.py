"""🔴 `githooks/install.sh` must install an SSH keepalive, or the gate it
installs will kill long pushes with SIGPIPE and report success while doing it.

WHY THIS FILE EXISTS
--------------------
MEASURED 2026-08-26 against the real github.com, twice independently (#782).

  * github.com closes an IDLE `git-receive-pack` session after ~360 s. Two runs,
    both **361 s**; the clean one also returned `rc=255` with
    `Connection to github.com closed by remote host.` on stderr.
  * git opens AND negotiates the connection BEFORE running `pre-push` — measured
    with a `GIT_SSH_COMMAND` stamp, not inferred from interleaved output:
    `ssh-launch 04:12:04Z` then `hook START 04:12:05Z`. So the connection idles
    for the hook's entire runtime.
  * `githooks/tests-on-push.sh` is precisely such a hook, and it is the thing
    `install.sh` arms. Paired push arms, identical 420 s hook, one variable:

        no keepalive          -> push rc=141 (SIGPIPE), branch ABSENT
        ServerAliveInterval=30 -> push rc=0,             branch CREATED

    An idle session with the keepalive was still alive at 1367 s (3.8x).

🔴 THE FAILURE LOOKS LIKE SUCCESS, which is why it survived undiagnosed. The
hook prints its own `✅ devrc test suite passed.` AFTER the connection is
already dead, and a wrapper's trailing command swallows the 141 — the issue
records `exit 0` being reported twice while the real status was 141 and the
branch was never created. Only `git ls-remote` distinguishes them.

WHAT IS REGRESSION COVERAGE HERE AND WHAT IS NOT (RULES.md asks for the label):

  * `test_the_installer_writes_a_push_keepalive` is THE regression test for the
    measured defect, run with the REAL `githooks/install.sh`. Its positive
    control is in-test and not optional: it asserts `hooksPath` ALSO landed in
    the same file, because without that "sshCommand is present" would be equally
    satisfied by an installer that never ran and a test reading a stale file.
  * `test_the_keepalive_interval_is_shorter_than_the_measured_close` pins the
    RELATIONSHIP that makes the fix a fix. An interval above the server's idle
    close is not a weaker mitigation, it is none at all — and nothing else in
    the tree would notice the number drifting up.
  * `test_a_foreign_sshCommand_is_not_clobbered` is REGRESSION coverage for the
    WARN path — it goes red at base, because the pre-change installer says
    nothing at all. Leaving a foreign value alone silently would be the
    reassuring-zero version of this fix: the operator stays exposed and is never
    told.
  * `test_uninstall_removes_only_our_sshCommand` is REGRESSION coverage for the
    uninstall path, both arms — ours goes, a foreign one stays.
  * `test_an_existing_keepalive_is_left_alone` is an INVARIANT GUARD, NOT a
    negative control, and the red-at-base run is what corrected that label: it
    PASSES at base, trivially, because the pre-change installer never writes
    core.sshCommand so nothing can survive it non-trivially. It is here so a
    future edit cannot start clobbering a chosen interval.
  * `test_the_installer_never_touches_the_operators_real_git_config` is an
    INVARIANT GUARD, green before this change by construction. It is labelled as
    one and is NOT counted as regression coverage; it exists because every test
    here runs an installer whose whole job is writing global git config.

MEASURED RED-AT-BASE, by swapping `git show origin/main:githooks/install.sh` in
on disk and re-running this file: **4 FAILED / 2 PASSED**.

    FAIL  test_the_installer_writes_a_push_keepalive
    FAIL  test_the_keepalive_interval_is_shorter_than_the_measured_close
    FAIL  test_uninstall_removes_only_our_sshCommand
    FAIL  test_a_foreign_sshCommand_is_not_clobbered
    PASS  test_an_existing_keepalive_is_left_alone            (invariant guard)
    PASS  test_the_installer_never_touches_the_operators_real_git_config
                                                              (invariant guard)

Green at HEAD: 6 passed.

🔴 NO `git`-METADATA READS OF THIS REPO — `nix flake check` builds
`checks.pytests` from a tracked-file copy with no `.git`, so a baseline taken
from `origin/main` would SKIP in the hermetic tier and go unnoticed. Every
fixture here is built under `tmp_path`.
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

INSTALLER = REPO_ROOT / "githooks" / "install.sh"

# The measured close, in seconds. Named here so the pin below states the
# relationship rather than a magic number.
MEASURED_IDLE_CLOSE_S = 360


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _child_env(tmp_path: Path) -> tuple[dict, Path]:
    """Environment for running the REAL installer, plus the config it writes to.

    The installer's entire job is `git config --global`, so the child gets its
    own `GIT_CONFIG_GLOBAL` under `tmp_path`. That moves the root of the SAME
    lookup git performs without changing the code path — the write lands in a
    file this test owns. HOME is redirected too, because the installer also
    seeds `$HOME/.claude/audit-on-push.env`, which is not something a test may
    create on somebody's machine.
    """
    cfg = tmp_path / "gitconfig"
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    # Belt and braces: if this ever computed an empty or absolute-root path, the
    # child would fall back to the operator's real config. Refuse to run.
    assert str(cfg).startswith(str(tmp_path)), cfg
    env = {
        **os.environ,
        "GIT_CONFIG_GLOBAL": str(cfg),
        "GIT_CONFIG_SYSTEM": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "HOME": str(home),
    }
    return env, cfg


def _run_installer(tmp_path: Path, *args: str) -> tuple[subprocess.CompletedProcess, Path]:
    env, cfg = _child_env(tmp_path)
    proc = subprocess.run(["bash", str(INSTALLER), *args], capture_output=True,
                          text=True, timeout=120, cwd=str(REPO_ROOT), env=env)
    return proc, cfg


def _get(cfg: Path, key: str) -> str:
    """Read one key back THROUGH git, not by parsing the file.

    Parsing would make the config file's format a dependency this test never
    pinned, and "no match" would then be indistinguishable from "wrong regex".
    """
    proc = subprocess.run(["git", "config", "--file", str(cfg), "--get", key],
                          capture_output=True, text=True, timeout=30,
                          env={**os.environ, "GIT_CONFIG_NOSYSTEM": "1"})
    return proc.stdout.strip()


def _digest(p: Path) -> str:
    """A file's content digest, or the literal 'ABSENT'.

    ABSENT is a value, not a skip: an installer that CREATES the operator's
    global config where none existed is the same finding as one that edits it.
    """
    try:
        return hashlib.sha256(p.read_bytes()).hexdigest()
    except OSError:
        return "ABSENT"


def _real_global_config_paths() -> list[Path]:
    env = {k: v for k, v in os.environ.items()
           if k not in ("GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM",
                        "GIT_CONFIG_NOSYSTEM")}
    out: list[Path] = []
    git = shutil.which("git")
    if git is not None:
        proc = subprocess.run(
            [git, "config", "--global", "--list", "--show-origin"],
            capture_output=True, text=True, env=env, timeout=30)
        for line in proc.stdout.splitlines():
            if line.startswith("file:"):
                out.append(Path(line[len("file:"):].split("\t", 1)[0]))
    home = Path(env.get("HOME", "/nonexistent"))
    out.append(home / ".gitconfig")
    out.append(Path(env.get("XDG_CONFIG_HOME", str(home / ".config"))) / "git" / "config")
    seen, uniq = set(), []
    for p in out:
        if str(p) not in seen:
            seen.add(str(p))
            uniq.append(p)
    return uniq


@pytest.fixture(autouse=True)
def _installer_exists():
    assert INSTALLER.is_file(), (
        f"{INSTALLER} is gone — every test in this file would prove nothing")


# --------------------------------------------------------------------------- #
# 🔴 THE REGRESSION TESTS — the measured defect
# --------------------------------------------------------------------------- #
def test_the_installer_writes_a_push_keepalive(tmp_path):
    """🔴 RUN THE REAL INSTALLER AND FOLLOW THE WRITE.

    Without this, a devrc push whose test gate runs longer than the server's
    ~360 s idle close dies with SIGPIPE and creates no branch, while the hook
    prints ✅ on the same screen.
    """
    proc, cfg = _run_installer(tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr

    # POSITIVE CONTROL, and it is load-bearing: `hooksPath` is the installer's
    # pre-existing write. If it is absent from this file then the installer
    # either did not run or wrote somewhere else, and the sshCommand assertion
    # below would be reading a file nothing ever touched — a reassuring zero.
    assert _get(cfg, "core.hooksPath"), (
        "the installer's own pre-existing write (core.hooksPath) is NOT in "
        f"{cfg}. This test is not measuring the installer:\n"
        f"{proc.stdout}\n{proc.stderr}")

    ssh_cmd = _get(cfg, "core.sshCommand")
    assert "ServerAliveInterval" in ssh_cmd, (
        "githooks/install.sh arms a pre-push gate that can occupy a push for "
        "minutes, but installs no SSH keepalive. Measured: github.com closes an "
        f"idle receive-pack session after ~{MEASURED_IDLE_CLOSE_S}s, so the push "
        "dies with SIGPIPE (rc=141) and the branch is never created — while the "
        f"hook prints its own success. See #782.\n  core.sshCommand = {ssh_cmd!r}")


def test_the_keepalive_interval_is_shorter_than_the_measured_close(tmp_path):
    """🔴 THE RELATIONSHIP, not the number.

    A keepalive longer than the server's idle close is not a weaker fix — it is
    no fix, and it would look exactly like this one. Nothing else in the tree
    would notice the value drifting upward, so it is pinned against the measured
    close rather than against a copy of itself.
    """
    _, cfg = _run_installer(tmp_path)
    ssh_cmd = _get(cfg, "core.sshCommand")
    m = re.search(r"ServerAliveInterval=(\d+)", ssh_cmd)
    assert m, f"no ServerAliveInterval=<n> in core.sshCommand: {ssh_cmd!r}"
    interval = int(m.group(1))
    assert interval > 0, (
        "ServerAliveInterval=0 DISABLES keepalives — that is this host's "
        "effective default and is the configuration that produced #782")
    assert interval < MEASURED_IDLE_CLOSE_S, (
        f"ServerAliveInterval={interval}s is not shorter than the measured "
        f"~{MEASURED_IDLE_CLOSE_S}s idle close, so the connection dies before a "
        "keepalive is ever sent. The option would be present and inert.")
    # A margin, not just 'less than': one lost probe must not exhaust the budget.
    assert interval * 3 <= MEASURED_IDLE_CLOSE_S, (
        f"ServerAliveInterval={interval}s leaves no room for a dropped probe "
        f"before the ~{MEASURED_IDLE_CLOSE_S}s close")


def test_uninstall_removes_only_our_sshCommand(tmp_path):
    """Both arms. Ours goes; a foreign one is not ours to remove."""
    # Arm A — we wrote it, uninstall clears it.
    proc, cfg = _run_installer(tmp_path / "ours")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "ServerAliveInterval" in _get(cfg, "core.sshCommand")
    proc = subprocess.run(["bash", str(INSTALLER), "--uninstall"],
                          capture_output=True, text=True, timeout=120,
                          cwd=str(REPO_ROOT),
                          env={**_child_env(tmp_path / "ours")[0]})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert _get(cfg, "core.sshCommand") == "", (
        "uninstall left our own core.sshCommand behind:\n" + proc.stdout)

    # Arm B — somebody else's survives uninstall.
    env_b, cfg_b = _child_env(tmp_path / "theirs")
    foreign = "ssh -i ~/.ssh/special_key"
    subprocess.run(["git", "config", "--file", str(cfg_b),
                    "core.sshCommand", foreign], check=True, timeout=30)
    proc = subprocess.run(["bash", str(INSTALLER), "--uninstall"],
                          capture_output=True, text=True, timeout=120,
                          cwd=str(REPO_ROOT), env=env_b)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert _get(cfg_b, "core.sshCommand") == foreign, (
        "uninstall removed a core.sshCommand this installer never wrote")


# --------------------------------------------------------------------------- #
# NEGATIVE CONTROLS ON BREADTH — the fix must not become a worse bug
# --------------------------------------------------------------------------- #
def test_a_foreign_sshCommand_is_not_clobbered(tmp_path):
    """Overwriting somebody's jump host or pinned key to fix our own hook would
    be a worse defect than #782. Warn, name the exact remedy, change nothing."""
    env, cfg = _child_env(tmp_path)
    foreign = "ssh -i ~/.ssh/special_key -J bastion.example.com"
    subprocess.run(["git", "config", "--file", str(cfg),
                    "core.sshCommand", foreign], check=True, timeout=30)

    proc = subprocess.run(["bash", str(INSTALLER)], capture_output=True,
                          text=True, timeout=120, cwd=str(REPO_ROOT), env=env)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert _get(cfg, "core.sshCommand") == foreign, (
        "the installer clobbered a pre-existing core.sshCommand")
    out = proc.stdout + proc.stderr
    assert "ServerAliveInterval" in out and foreign in out, (
        "leaving it alone SILENTLY is the reassuring-zero version of this: the "
        "operator stays exposed to #782 and is never told. The warning must "
        "name their value and the option to add.\n" + out)


def test_an_existing_keepalive_is_left_alone(tmp_path):
    """Someone who already chose an interval keeps it — and gets no warning,
    because there is nothing to warn about."""
    env, cfg = _child_env(tmp_path)
    theirs = "ssh -o ServerAliveInterval=15"
    subprocess.run(["git", "config", "--file", str(cfg),
                    "core.sshCommand", theirs], check=True, timeout=30)

    proc = subprocess.run(["bash", str(INSTALLER)], capture_output=True,
                          text=True, timeout=120, cwd=str(REPO_ROOT), env=env)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert _get(cfg, "core.sshCommand") == theirs
    assert "WARNING" not in (proc.stdout + proc.stderr).upper().replace(
        "WARNING: GLOBAL CORE.HOOKSPATH", ""), (
        "warned about a config that already carries a keepalive:\n" + proc.stdout)


# --------------------------------------------------------------------------- #
# INVARIANT GUARD — green before this change, labelled as such
# --------------------------------------------------------------------------- #
def test_the_installer_never_touches_the_operators_real_git_config(tmp_path):
    """🔴 NOT regression coverage for #782 — an invariant guard.

    Every test in this file runs a script whose job is writing global git
    config. `test_nogit_isolation.py` owns this hazard suite-wide; this is the
    local floor, because a redirect that silently stopped applying would make
    every assertion above land in the operator's real file.
    """
    real = _real_global_config_paths()
    before = {p: _digest(p) for p in real}
    proc, _ = _run_installer(tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    after = {p: _digest(p) for p in real}
    changed = [str(p) for p in real if before[p] != after[p]]
    assert not changed, (
        "the real installer modified the operator's global git config: "
        f"{changed}")
