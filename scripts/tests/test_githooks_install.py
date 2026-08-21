"""Guards for `githooks/install.sh` — the script whose SUCCESS PATH could not
run on either host it ships to.

🔴 THE MEASUREMENT. `install.sh` set `git config --global core.hooksPath` and
nothing else. On both of this repo's hosts `~/.config/git/config` is a nix-store
symlink written by `programs.git` in nix/home.nix, so that command fails:

    error: could not lock config file /home/zach/.config/git/config:
           Read-only file system

and under `set -euo pipefail` the script aborted there. The observable
consequence, measured on the workbench 2026-08-21: `core.hooksPath` pointed at
`.git/hooks`, which held 14 files, all `*.sample` — no pre-push hook existed, so
the blocking test gate that four comments in this repo describe as running on
"every push" had never run on a single one.

WHAT IS REGRESSION COVERAGE AND WHAT IS NOT
-------------------------------------------
`test_an_unwritable_global_config_falls_back_to_repo_local` is REGRESSION
coverage — it reproduces the exact failing path (a `--global` write that cannot
take its lock) and asserts a hook is nevertheless configured.

`test_a_writable_global_config_is_used_and_no_local_override_is_written` is the
NEGATIVE CONTROL for that fallback: without it, an implementation that ALWAYS
wrote repo-local would pass the test above while silently narrowing the audit
hook's scope on a host where the global write works. The pair is the point.

Neither test touches the real `$HOME` or the real repo: `HOME`,
`GIT_CONFIG_GLOBAL` and the githooks directory itself are all fixtures.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
GITHOOKS = REPO_ROOT / "githooks"


def _git(cwd: Path, *args: str, **kw) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True,
                          text=True, timeout=60, **kw)


@pytest.fixture()
def sandbox(tmp_path):
    """A throwaway git repo holding a COPY of githooks/, plus its own HOME.

    A copy rather than the real directory because `install.sh` chmods its
    contents and writes the repo-local config of whatever repo contains it —
    pointed at the real checkout that would reconfigure the machine running the
    suite.
    """
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(tmp_path, "init", "-q", "-b", "main", str(repo), check=True)
    shutil.copytree(GITHOOKS, repo / "githooks")
    return home, repo


def _run_install(home: Path, repo: Path, global_cfg: Path, *args: str):
    env = dict(os.environ)
    env["HOME"] = str(home)
    # 🔴 GIT_CONFIG_GLOBAL is what makes "the global config" a fixture instead
    # of the operator's real file. git honours it for `--global` reads AND
    # writes, so the unwritable case below is the same code path that fails on a
    # home-manager host, without needing that host's read-only store symlink.
    env["GIT_CONFIG_GLOBAL"] = str(global_cfg)
    env.pop("GIT_CONFIG_SYSTEM", None)
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    p = subprocess.run(["bash", str(repo / "githooks" / "install.sh"), *args],
                       capture_output=True, text=True, env=env, timeout=120)
    return p


def _local_hooks_path(repo: Path) -> str:
    return _git(repo, "config", "--local", "--get", "core.hooksPath").stdout.strip()


def _global_hooks_path(home: Path, repo: Path, global_cfg: Path) -> str:
    env = dict(os.environ)
    env["HOME"] = str(home)
    env["GIT_CONFIG_GLOBAL"] = str(global_cfg)
    return subprocess.run(["git", "config", "--global", "--get", "core.hooksPath"],
                          capture_output=True, text=True, env=env,
                          timeout=60).stdout.strip()


def test_an_unwritable_global_config_falls_back_to_repo_local(sandbox, tmp_path):
    """🔴 THE REGRESSION, driven through the real failure and not a stub.

    The global config lives in a directory with no write permission, so git
    cannot create its `.lock` file and `git config --global` exits non-zero —
    the same class of failure as the read-only /nix/store symlink on the real
    hosts. The script must still leave a hook configured.
    """
    home, repo = sandbox
    ro = tmp_path / "ro"
    ro.mkdir()
    cfg = ro / "config"
    cfg.write_text("")
    ro.chmod(0o500)
    try:
        # POSITIVE CONTROL FOR THE FIXTURE: prove the write really is impossible
        # before asserting anything about how install.sh handles it. Without
        # this, a chmod that did not take (running as root, an unusual mount)
        # would make the fallback untested and the test green.
        probe = subprocess.run(
            ["git", "config", "--global", "core.hooksPath", "/probe"],
            capture_output=True, text=True, timeout=60,
            env={**os.environ, "HOME": str(home), "GIT_CONFIG_GLOBAL": str(cfg)})
        assert probe.returncode != 0, (
            "the fixture's 'unwritable' global config accepted a write — this "
            "test would then be measuring the ordinary success path.\n"
            + probe.stdout + probe.stderr)

        p = _run_install(home, repo, cfg)
        out = p.stdout + p.stderr
        assert p.returncode == 0, (
            "install.sh aborted instead of falling back. This is the measured "
            "workbench state: no hook installed at all.\n" + out)
        assert _local_hooks_path(repo) == str(repo / "githooks"), (
            "no repo-local core.hooksPath was written, so nothing is installed:\n"
            + out)
        # It must SAY the scope narrowed — a reader left believing the audit
        # hook covers every repo is the second half of the same failure.
        assert "THIS REPO ONLY" in out, (
            "the fallback did not report its narrower scope\n" + out)
    finally:
        ro.chmod(0o700)


def test_a_writable_global_config_is_used_and_no_local_override_is_written(sandbox, tmp_path):
    """🔴 THE NEGATIVE CONTROL FOR THE FALLBACK. An implementation that always
    wrote repo-local would pass the test above — and would silently stop the
    push AUDIT from covering any other repo on a host where the global write
    works. So: global set, local untouched."""
    home, repo = sandbox
    cfg = tmp_path / "writable-gitconfig"
    p = _run_install(home, repo, cfg)
    out = p.stdout + p.stderr
    assert p.returncode == 0, out
    assert _global_hooks_path(home, repo, cfg) == str(repo / "githooks"), out
    assert _local_hooks_path(repo) == "", (
        "a repo-local override was written even though the global config was "
        "writable — the fallback is not conditional\n" + out)
    assert "GLOBAL core.hooksPath" in out, out


def test_uninstall_clears_whichever_scope_install_used(sandbox, tmp_path):
    """The inverse of the fallback, and it was missing: `--uninstall` only ever
    looked at the global config, so a repo-local install could not be undone by
    the script that created it."""
    home, repo = sandbox
    ro = tmp_path / "ro2"
    ro.mkdir()
    cfg = ro / "config"
    cfg.write_text("")
    ro.chmod(0o500)
    try:
        _run_install(home, repo, cfg)
        assert _local_hooks_path(repo) == str(repo / "githooks")
        p = _run_install(home, repo, cfg, "--uninstall")
        out = p.stdout + p.stderr
        assert p.returncode == 0, out
        assert _local_hooks_path(repo) == "", (
            "--uninstall left the repo-local core.hooksPath in place\n" + out)
    finally:
        ro.chmod(0o700)


def test_install_does_not_depend_on_the_callers_cwd(sandbox, tmp_path):
    """`$DIR` is resolved from `BASH_SOURCE`, and the repo to fall back on is
    resolved from `$DIR` — not from cwd. Driven from an unrelated directory
    because "it worked when I ran it from the repo" is not the claim."""
    home, repo = sandbox
    ro = tmp_path / "ro3"
    ro.mkdir()
    cfg = ro / "config"
    cfg.write_text("")
    ro.chmod(0o500)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    try:
        env = dict(os.environ)
        env["HOME"] = str(home)
        env["GIT_CONFIG_GLOBAL"] = str(cfg)
        p = subprocess.run(["bash", str(repo / "githooks" / "install.sh")],
                           capture_output=True, text=True, env=env,
                           cwd=str(elsewhere), timeout=120)
        out = p.stdout + p.stderr
        assert p.returncode == 0, out
        assert _local_hooks_path(repo) == str(repo / "githooks"), out
    finally:
        ro.chmod(0o700)
