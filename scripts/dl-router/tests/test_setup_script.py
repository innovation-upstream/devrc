"""setup-brave-profile.sh -- the one-time browser profile change.

It edits the browser's `Preferences`, and the browser REWRITES that file on
exit. So the guard is the whole safety story: if it passes while a browser is
live on this profile, the change is silently reverted later and the user is
left with a router that never sees a download, and no error anywhere.

The guard's question is NOT "does a process called brave exist" -- on a machine
that also drives headless Brave for automation (each instance on its own
`--user-data-dir=/tmp/...`) that answer is yes essentially always, and gating on
it makes the setup step unrunnable while the real browser is closed. The
question is "is any live process using THIS user-data-dir".

So the tests fabricate a process table (`DL_ROUTER_PROC_DIR`) and a
`SingletonLock`, and assert on which of those actually count. A fake `pgrep` is
still installed to pin the regression: the answer to "is a brave binary
running" must no longer decide anything on its own. Nothing here touches a real
browser profile.
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

# A realistic Chromium argv0.
BRAVE_EXE = "/nix/store/deadbeef-brave-1.0/opt/brave.com/brave/brave"


def _build_env(tmp_path, brave: Path) -> dict:
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
    library.mkdir(exist_ok=True)

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    # A real python3 is needed by the script's embedded heredocs.
    (bin_dir / "python3").symlink_to(sys.executable)
    for tool in ("bash", "env", "date", "cp", "cat", "dirname", "pwd", "mktemp",
                 "rm", "ls"):
        found = _which(tool)
        if found:
            (bin_dir / tool).symlink_to(found)

    proc = tmp_path / "proc"
    proc.mkdir(exist_ok=True)
    # A process table is never empty; give it an init so "empty" stays
    # available as a distinct, suspicious case.
    add_process(proc, 1, cmdline="/sbin/init", comm="systemd")

    return {"brave": brave, "profile": profile, "library": library,
            "bin": bin_dir, "proc": proc, "tmp": tmp_path,
            "env_base": {"BRAVE_DIR": str(brave)}}


@pytest.fixture
def fake_env(tmp_path):
    """A browser user-data-dir, a library root, a PATH and a process table."""
    return _build_env(tmp_path, tmp_path / "Brave-Browser")


@pytest.fixture
def default_udd_env(tmp_path):
    """The same, but the profile lives in the DEFAULT user-data-dir.

    Needed because "a browser with no --user-data-dir at all" is on the default
    one -- which only blocks when the default is the directory being patched.
    """
    home = tmp_path / "home"
    env = _build_env(
        tmp_path, home / ".config" / "BraveSoftware" / "Brave-Browser")
    # BRAVE_DIR empty => the script falls back to its $HOME-derived default.
    env["env_base"] = {"HOME": str(home), "BRAVE_DIR": ""}
    return env


def _which(name):
    for d in os.environ.get("PATH", "").split(os.pathsep):
        candidate = Path(d) / name
        if candidate.exists():
            return candidate
    return None


def add_process(proc_dir: Path, pid, *, cmdline=None, comm="", fds=()):
    """Fabricate one /proc/<pid> entry.

    `cmdline` is written VERBATIM. A live Chromium rewrites its own argv to set
    the process title, which collapses the NUL separators into spaces -- on a
    real machine every brave process reads back as ONE space-joined blob, not a
    NUL-separated vector -- so tests use that shape. `cmdline=None` leaves the
    file out entirely: a process that is alive but opaque to us.
    """
    d = proc_dir / str(pid)
    (d / "fd").mkdir(parents=True)
    if cmdline is not None:
        (d / "cmdline").write_text(cmdline, encoding="utf-8")
    if comm:
        (d / "comm").write_text(comm + "\n", encoding="utf-8")
    for slot, target in enumerate(fds, start=3):
        (d / "fd" / str(slot)).symlink_to(target)
    return d


def singleton_lock(fake_env, pid, host="somehost"):
    (Path(fake_env["brave"]) / "SingletonLock").symlink_to(f"{host}-{pid}")


def write_pgrep(bin_dir: Path, *, exit_code: int):
    """Install a `pgrep` with a fixed answer.

    exit 0 = "a process with that binary name exists". That used to be the
    whole guard; it must not decide anything now.
    """
    (bin_dir / "pgrep").write_text(
        f"#!/usr/bin/env bash\nexit {exit_code}\n", encoding="utf-8")
    os.chmod(bin_dir / "pgrep", 0o755)


def run(fake_env, *args, env_extra=None):
    env = dict(os.environ)
    env["PATH"] = f"{fake_env['bin']}{os.pathsep}{env['PATH']}"
    env["DL_ROUTER_CONFIG"] = str(fake_env["tmp"] / "no-such-config.toml")
    env["DL_ROUTER_PROC_DIR"] = str(fake_env["proc"])
    env.update(fake_env["env_base"])
    env.update(env_extra or {})
    return subprocess.run([str(SCRIPT), *args], capture_output=True, text=True,
                          env=env, timeout=60)


def prefs(fake_env) -> dict:
    return json.loads((fake_env["profile"] / "Preferences").read_text())


def apply(fake_env, **kw):
    return run(fake_env, "--profile", "Profile 2",
               "--root", str(fake_env["library"]), **kw)


def assert_untouched(fake_env):
    assert prefs(fake_env)["download"]["default_directory"] == "/old/path"
    assert not list(fake_env["profile"].glob("Preferences.dl-router-backup-*"))


def assert_applied(fake_env):
    assert prefs(fake_env)["download"]["default_directory"] \
        == str(fake_env["library"])


# --- the guard: what BLOCKS ------------------------------------------------ #
def test_a_live_instance_on_this_user_data_dir_blocks(fake_env):
    write_pgrep(fake_env["bin"], exit_code=1)   # name-matching would say "no"
    add_process(fake_env["proc"], 901, comm="brave",
                cmdline=f"{BRAVE_EXE} --user-data-dir={fake_env['brave']} "
                        "--no-first-run")
    out = apply(fake_env)
    assert out.returncode == EXIT_REFUSED, out.stdout + out.stderr
    assert_untouched(fake_env)


def test_the_refusal_says_which_instance_and_which_profile(fake_env):
    """"The browser is running" was actively misleading: the browser the user
    cared about was closed. The message has to identify the blocker."""
    write_pgrep(fake_env["bin"], exit_code=1)
    add_process(fake_env["proc"], 901, comm="brave",
                cmdline=f"{BRAVE_EXE} --user-data-dir={fake_env['brave']}")
    err = apply(fake_env).stderr
    assert "pid 901" in err
    assert "Profile 2" in err
    assert str(fake_env["brave"]) in err


def test_an_instance_with_no_user_data_dir_blocks_the_default_one(
        default_udd_env):
    """No --user-data-dir means the default one -- the case the binary-name
    check happened to cover and a naive cmdline check would miss."""
    write_pgrep(default_udd_env["bin"], exit_code=1)
    add_process(default_udd_env["proc"], 902, comm="brave",
                cmdline=f"{BRAVE_EXE} --no-first-run --restore-last-session")
    out = apply(default_udd_env)
    assert out.returncode == EXIT_REFUSED, out.stdout + out.stderr
    assert "pid 902" in out.stderr
    assert_untouched(default_udd_env)


def test_any_process_holding_a_file_open_in_this_profile_blocks(fake_env):
    """The kernel-truth signal: it needs no cmdline parsing and catches
    holders that are not named brave at all."""
    write_pgrep(fake_env["bin"], exit_code=1)
    add_process(fake_env["proc"], 903, comm="chrome_crashpad",
                cmdline="/opt/chrome_crashpad_handler --monitor-self",
                fds=[fake_env["profile"] / "Preferences"])
    out = apply(fake_env)
    assert out.returncode == EXIT_REFUSED, out.stdout + out.stderr
    assert "pid 903" in out.stderr
    assert_untouched(fake_env)


def test_a_live_lock_holder_we_cannot_inspect_blocks(fake_env):
    """SingletonLock -> "<host>-<pid>" where the pid is alive but opaque (root,
    or hidepid). Nothing to guess about: fail closed."""
    write_pgrep(fake_env["bin"], exit_code=1)
    add_process(fake_env["proc"], 4242, cmdline=None)
    singleton_lock(fake_env, 4242)
    out = apply(fake_env)
    assert out.returncode == EXIT_REFUSED, out.stdout + out.stderr
    assert "4242" in out.stderr
    assert_untouched(fake_env)


# --- the guard: what MUST NOT block ---------------------------------------- #
def test_a_headless_instance_on_another_user_data_dir_does_not_block(fake_env):
    """THE finding. Agents run Playwright/chromedp against the SAME brave
    binary on throwaway `--user-data-dir=/tmp/...` profiles. `pgrep -x brave`
    matches them, so the guard refused with the real browser fully closed and
    nothing holding this profile -- and the documented escape hatch did not
    apply, because pgrep had answered."""
    write_pgrep(fake_env["bin"], exit_code=0)
    other = fake_env["tmp"] / "chromedp-runner1253386066"
    other.mkdir()
    add_process(fake_env["proc"], 975904, comm="brave",
                cmdline=f"{BRAVE_EXE} --headless --user-data-dir={other} "
                        "--no-first-run")
    add_process(fake_env["proc"], 975949, comm="brave",
                cmdline=f"{BRAVE_EXE} --type=zygote --no-sandbox --headless "
                        f"--user-data-dir={other}")
    out = apply(fake_env)
    assert out.returncode == 0, out.stdout + out.stderr
    assert_applied(fake_env)


def test_a_child_of_another_instance_does_not_block_the_default(
        default_udd_env):
    """Renderer/zygote/gpu children do not carry --user-data-dir, so "no
    --user-data-dir means the default one" must only apply to a MAIN process
    (no --type=). Otherwise every headless instance drags ~10 children that
    each look like a default-profile browser."""
    write_pgrep(default_udd_env["bin"], exit_code=0)
    add_process(default_udd_env["proc"], 976034, comm="brave",
                cmdline=f"{BRAVE_EXE} --type=gpu-process --no-sandbox "
                        "--headless --ozone-platform=headless")
    add_process(default_udd_env["proc"], 976036, comm="brave",
                cmdline=f"{BRAVE_EXE} --type=utility "
                        "--utility-sub-type=network.mojom.NetworkService")
    out = apply(default_udd_env)
    assert out.returncode == 0, out.stdout + out.stderr
    assert_applied(default_udd_env)


def test_a_stale_singleton_lock_does_not_block(fake_env):
    """A crash or an unclean exit leaves SingletonLock pointing at a dead pid.
    Treating the lock as authoritative would make the script permanently
    unrunnable with no way out."""
    write_pgrep(fake_env["bin"], exit_code=0)
    singleton_lock(fake_env, 4242)          # not in the process table at all
    out = apply(fake_env)
    assert out.returncode == 0, out.stdout + out.stderr
    assert_applied(fake_env)


def test_a_lock_pid_recycled_by_another_program_does_not_block(fake_env):
    """The lock is only authoritative when its pid is alive AND is a browser.
    Pids get reused."""
    write_pgrep(fake_env["bin"], exit_code=0)
    add_process(fake_env["proc"], 4242, comm="vim",
                cmdline="/usr/bin/vim\0notes.txt\0")
    singleton_lock(fake_env, 4242)
    out = apply(fake_env)
    assert out.returncode == 0, out.stdout + out.stderr
    assert_applied(fake_env)


def test_it_proceeds_when_nothing_is_using_this_profile(fake_env):
    write_pgrep(fake_env["bin"], exit_code=1)
    out = apply(fake_env)
    assert out.returncode == 0, out.stderr
    assert_applied(fake_env)
    assert prefs(fake_env)["download"]["prompt_for_download"] is False
    assert prefs(fake_env)["savefile"]["default_directory"] \
        == str(fake_env["library"])


# --- the guard: fail closed ------------------------------------------------ #
def test_no_readable_process_table_is_NOT_read_as_nothing_running(fake_env):
    """The original bug in this guard, in its new form: "the check could not
    run" must never collapse into "nothing is running"."""
    write_pgrep(fake_env["bin"], exit_code=1)
    out = apply(fake_env,
                env_extra={"DL_ROUTER_PROC_DIR": str(fake_env["tmp"] / "gone")})
    assert out.returncode == EXIT_REFUSED, out.stdout + out.stderr
    assert "could not determine" in out.stderr.lower()
    assert_untouched(fake_env)


def test_an_empty_directory_is_not_a_process_table(fake_env):
    """A real one always has pid 1. An empty (or simply wrong) directory is not
    evidence that nothing is running."""
    write_pgrep(fake_env["bin"], exit_code=1)
    empty = fake_env["tmp"] / "empty-proc"
    empty.mkdir()
    out = apply(fake_env, env_extra={"DL_ROUTER_PROC_DIR": str(empty)})
    assert out.returncode == EXIT_REFUSED, out.stdout + out.stderr
    assert "could not determine" in out.stderr.lower()
    assert_untouched(fake_env)


def test_the_override_covers_the_undetectable_case(fake_env):
    write_pgrep(fake_env["bin"], exit_code=1)
    out = apply(fake_env, env_extra={
        "DL_ROUTER_PROC_DIR": str(fake_env["tmp"] / "gone"),
        "DL_ROUTER_ASSUME_BROWSER_CLOSED": "1"})
    assert out.returncode == 0, out.stderr
    assert "could not determine" in out.stderr.lower(), \
        "say what was overridden, do not proceed silently"
    assert_applied(fake_env)


def test_the_override_does_NOT_cover_a_detected_instance(fake_env):
    """It means "I cannot tell, and I promise it is closed" -- not "ignore the
    browser you just found". There is nothing to promise about a live pid."""
    write_pgrep(fake_env["bin"], exit_code=1)
    add_process(fake_env["proc"], 901, comm="brave",
                cmdline=f"{BRAVE_EXE} --user-data-dir={fake_env['brave']}")
    out = apply(fake_env,
                env_extra={"DL_ROUTER_ASSUME_BROWSER_CLOSED": "1"})
    assert out.returncode == EXIT_REFUSED, out.stdout + out.stderr
    assert_untouched(fake_env)


def test_the_refusal_exit_code_is_stable(fake_env):
    """Other tooling keys off it; 3 means "refused", not "failed"."""
    write_pgrep(fake_env["bin"], exit_code=1)
    add_process(fake_env["proc"], 901, comm="brave",
                cmdline=f"{BRAVE_EXE} --user-data-dir={fake_env['brave']}")
    assert apply(fake_env).returncode == 3


# --- the guard: against a real /proc --------------------------------------- #
@pytest.mark.skipif(not Path("/proc/self/fd").is_dir(), reason="needs procfs")
def test_a_real_process_holding_the_profile_open_blocks(fake_env):
    """The fabricated process tables above are only as good as their fidelity
    to procfs. Prove the reader works against the real thing."""
    write_pgrep(fake_env["bin"], exit_code=1)
    child = subprocess.Popen(
        [sys.executable, "-c",
         "import sys, time\n"
         "fh = open(sys.argv[1])\n"
         "sys.stdout.write('ok'); sys.stdout.flush()\n"
         "time.sleep(120)\n",
         str(fake_env["profile"] / "Preferences")],
        stdout=subprocess.PIPE)
    try:
        assert child.stdout.read(2) == b"ok", "child never opened the file"
        out = apply(fake_env, env_extra={"DL_ROUTER_PROC_DIR": "/proc"})
        assert out.returncode == EXIT_REFUSED, out.stdout + out.stderr
        assert f"pid {child.pid}" in out.stderr
        assert_untouched(fake_env)
    finally:
        child.kill()
        child.wait()
        child.stdout.close()


@pytest.mark.skipif(not Path("/proc/self/fd").is_dir(), reason="needs procfs")
def test_a_real_process_table_with_no_holder_does_not_block(fake_env):
    """The other half: whatever else this machine is running -- headless
    browsers included -- nothing is using THIS directory, so it applies."""
    write_pgrep(fake_env["bin"], exit_code=0)
    out = apply(fake_env, env_extra={"DL_ROUTER_PROC_DIR": "/proc"})
    assert out.returncode == 0, out.stdout + out.stderr
    assert_applied(fake_env)


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
    assert_untouched(fake_env)


def test_dry_run_is_not_gated_on_the_browser(fake_env):
    """It writes nothing by construction, so refusing only forced the user to
    go and edit Preferences by hand -- which is what this script exists to make
    unnecessary. Report the instance, then show the diff anyway."""
    write_pgrep(fake_env["bin"], exit_code=1)
    add_process(fake_env["proc"], 901, comm="brave",
                cmdline=f"{BRAVE_EXE} --user-data-dir={fake_env['brave']}")
    out = run(fake_env, "--profile", "Profile 2",
              "--root", str(fake_env["library"]), "--dry-run")
    assert out.returncode == 0, out.stdout + out.stderr
    assert "dry run" in out.stdout
    assert "pid 901" in out.stderr, "still say what would block the real run"
    assert_untouched(fake_env)


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
    add_process(fake_env["proc"], 901, comm="brave",
                cmdline=f"{BRAVE_EXE} --user-data-dir={fake_env['brave']}")
    out = run(fake_env, "--list")
    assert out.returncode == 0, "listing is read-only"
