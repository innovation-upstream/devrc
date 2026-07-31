"""setup-brave-profile.sh -- the one-time browser profile change.

It edits the browser's `Preferences`, and the browser REWRITES that file on
exit. So the "is it running?" guard is the whole safety story: if it passes
while the browser is live, the change is silently reverted later and the user
is left with a router that never sees a download, and no error anywhere.

Every test here runs the real script with a fabricated PATH, so `pgrep` is
whatever the test says it is. Nothing touches a real browser profile.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "setup-brave-profile.sh"

# Exit codes the script's own documentation commits to.
EXIT_USAGE = 2
EXIT_REFUSED = 3


@pytest.fixture
def fake_env(tmp_path):
    """A browser profile dir, a library root, and a PATH we control."""
    brave = tmp_path / "Brave-Browser"
    profile = brave / "Profile 2"
    profile.mkdir(parents=True)
    (profile / "Preferences").write_text(json.dumps({
        "download": {"default_directory": "/old/path",
                     "prompt_for_download": True},
    }), encoding="utf-8")
    (brave / "Local State").write_text(json.dumps({
        "profile": {"info_cache": {"Profile 2": {"name": "Media"}}},
    }), encoding="utf-8")
    library = tmp_path / "library"
    library.mkdir()

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    # A real python3 is needed by the script's embedded heredocs.
    (bin_dir / "python3").symlink_to(sys.executable)
    # Everything the script shells out to -- but deliberately NOT pgrep, so a
    # test can make it genuinely absent from PATH.
    for tool in ("bash", "env", "date", "cp", "cat", "dirname", "pwd", "mktemp",
                 "rm", "ls"):
        found = _which(tool)
        if found:
            (bin_dir / tool).symlink_to(found)
    return {"brave": brave, "profile": profile, "library": library,
            "bin": bin_dir, "tmp": tmp_path}


def _which(name):
    for d in os.environ.get("PATH", "").split(os.pathsep):
        candidate = Path(d) / name
        if candidate.exists():
            return candidate
    return None


def write_pgrep(bin_dir: Path, *, exit_code: int):
    (bin_dir / "pgrep").write_text(
        f"#!/usr/bin/env bash\nexit {exit_code}\n", encoding="utf-8")
    os.chmod(bin_dir / "pgrep", 0o755)


def run(fake_env, *args, env_extra=None, with_pgrep=True):
    env = dict(os.environ)
    env["PATH"] = f"{fake_env['bin']}{os.pathsep}{env['PATH']}"
    env["BRAVE_DIR"] = str(fake_env["brave"])
    env["DL_ROUTER_CONFIG"] = str(fake_env["tmp"] / "no-such-config.toml")
    env.update(env_extra or {})
    if not with_pgrep:
        # Only our bin dir, so pgrep genuinely is not on PATH. Keep a real
        # coreutils dir for the script's own helpers.
        env["PATH"] = str(fake_env["bin"])
    return subprocess.run([str(SCRIPT), *args], capture_output=True, text=True,
                          env=env, timeout=60)


def prefs(fake_env) -> dict:
    return json.loads((fake_env["profile"] / "Preferences").read_text())


# --- the guard ------------------------------------------------------------- #
def test_it_refuses_while_the_browser_is_running(fake_env):
    write_pgrep(fake_env["bin"], exit_code=0)
    out = run(fake_env, "--profile", "Profile 2",
              "--root", str(fake_env["library"]))
    assert out.returncode == EXIT_REFUSED
    assert "running" in out.stderr.lower()
    assert prefs(fake_env)["download"]["default_directory"] == "/old/path"


def test_it_proceeds_when_the_browser_is_confirmed_closed(fake_env):
    write_pgrep(fake_env["bin"], exit_code=1)
    out = run(fake_env, "--profile", "Profile 2",
              "--root", str(fake_env["library"]))
    assert out.returncode == 0, out.stderr
    assert prefs(fake_env)["download"]["default_directory"] \
        == str(fake_env["library"])
    assert prefs(fake_env)["download"]["prompt_for_download"] is False
    assert prefs(fake_env)["savefile"]["default_directory"] \
        == str(fake_env["library"])


def test_a_missing_pgrep_is_NOT_read_as_not_running(fake_env):
    """THE finding. `pgrep ... >/dev/null 2>&1` returning 127 (binary absent
    from PATH) was indistinguishable from 1 (no match), and 2>/dev/null hid the
    "command not found". The guard passed and the script patched Preferences
    under a live browser -- which then reverted it on exit, silently."""
    out = run(fake_env, "--profile", "Profile 2",
              "--root", str(fake_env["library"]), with_pgrep=False)
    assert out.returncode == EXIT_REFUSED, out.stdout + out.stderr
    assert "pgrep" in out.stderr
    assert prefs(fake_env)["download"]["default_directory"] == "/old/path", \
        "Preferences must be untouched when the guard could not run"


def test_a_pgrep_that_errors_out_is_also_refused(fake_env):
    """127 is not the only way to get no answer -- a broken or permission-denied
    pgrep must fail closed too."""
    write_pgrep(fake_env["bin"], exit_code=126)
    out = run(fake_env, "--profile", "Profile 2",
              "--root", str(fake_env["library"]))
    assert out.returncode == EXIT_REFUSED
    assert "could not determine" in out.stderr.lower()
    assert prefs(fake_env)["download"]["default_directory"] == "/old/path"


def test_the_override_is_explicit_and_opt_in(fake_env):
    write_pgrep(fake_env["bin"], exit_code=126)
    out = run(fake_env, "--profile", "Profile 2",
              "--root", str(fake_env["library"]),
              env_extra={"DL_ROUTER_ASSUME_BROWSER_CLOSED": "1"})
    assert out.returncode == 0, out.stderr
    assert prefs(fake_env)["download"]["default_directory"] \
        == str(fake_env["library"])


def test_the_refusal_exit_code_is_stable(fake_env):
    """Other tooling keys off it; 3 means "refused", not "failed"."""
    write_pgrep(fake_env["bin"], exit_code=0)
    assert run(fake_env, "--profile", "Profile 2",
               "--root", str(fake_env["library"])).returncode == 3


# --- argument handling ----------------------------------------------------- #
@pytest.mark.parametrize("args", [
    ["--profile"],
    ["--root"],
    ["--profile", "Profile 2", "--root"],
])
def test_a_flag_with_no_value_is_a_usage_error_not_a_shift_error(fake_env, args):
    """`shift 2` with one argument left is a fatal shift under `set -e`, which
    surfaced as an unexplained non-zero exit with no message."""
    write_pgrep(fake_env["bin"], exit_code=1)
    out = run(fake_env, *args)
    assert out.returncode == EXIT_USAGE
    assert "requires a value" in out.stderr
    assert "usage:" in out.stderr


def test_an_unknown_flag_is_a_usage_error(fake_env):
    write_pgrep(fake_env["bin"], exit_code=1)
    out = run(fake_env, "--nope")
    assert out.returncode == EXIT_USAGE
    assert "unknown argument" in out.stderr


def test_no_profile_selected_is_refused_before_anything_is_read(fake_env):
    write_pgrep(fake_env["bin"], exit_code=1)
    out = run(fake_env, "--root", str(fake_env["library"]))
    assert out.returncode == EXIT_USAGE
    assert "--list" in out.stderr


def test_a_missing_library_root_is_refused(fake_env):
    write_pgrep(fake_env["bin"], exit_code=1)
    out = run(fake_env, "--profile", "Profile 2",
              "--root", str(fake_env["tmp"] / "nope"))
    assert out.returncode == EXIT_USAGE
    assert "not a directory" in out.stderr


def test_an_unknown_profile_is_refused(fake_env):
    write_pgrep(fake_env["bin"], exit_code=1)
    out = run(fake_env, "--profile", "Profile 9",
              "--root", str(fake_env["library"]))
    assert out.returncode == EXIT_USAGE
    assert "no Preferences file" in out.stderr


# --- dry run and listing --------------------------------------------------- #
def test_dry_run_writes_nothing(fake_env):
    write_pgrep(fake_env["bin"], exit_code=1)
    out = run(fake_env, "--profile", "Profile 2",
              "--root", str(fake_env["library"]), "--dry-run")
    assert out.returncode == 0, out.stderr
    assert "dry run" in out.stdout
    assert prefs(fake_env)["download"]["default_directory"] == "/old/path"
    assert not list(fake_env["profile"].glob("Preferences.dl-router-backup-*"))


def test_a_backup_is_made_before_the_edit(fake_env):
    write_pgrep(fake_env["bin"], exit_code=1)
    run(fake_env, "--profile", "Profile 2", "--root", str(fake_env["library"]))
    backups = list(fake_env["profile"].glob("Preferences.dl-router-backup-*"))
    assert len(backups) == 1
    assert json.loads(backups[0].read_text())["download"]["default_directory"] \
        == "/old/path"


def test_list_shows_the_profile_and_its_display_name(fake_env):
    write_pgrep(fake_env["bin"], exit_code=1)
    out = run(fake_env, "--list")
    assert out.returncode == 0, out.stderr
    assert "Profile 2" in out.stdout
    assert "Media" in out.stdout


def test_list_does_not_need_the_browser_to_be_closed(fake_env):
    write_pgrep(fake_env["bin"], exit_code=0)
    out = run(fake_env, "--list")
    assert out.returncode == 0, "listing is read-only"
