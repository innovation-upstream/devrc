"""Tests for scripts/claude-log-rotate/rotate.sh — the ~/.claude log size cap.

Two layers, deliberately:

  1. CONFIG — `--print-config` is pure text, so these run everywhere and pin the
     policy (size cap, generation count, copytruncate, `*.log` only).
  2. BEHAVIOUR — a real `logrotate` run against a real temp directory. 🔴 These
     do NOT skip when the binary is missing: `logrotate` is in the pytests
     check's PATH (flake.nix) and in REQUIRED_TOOLS, and a skip here would be a
     test reporting safety while measuring nothing. If the binary is absent the
     tests FAIL and name the fix.

Every behavioural assertion is paired with a control that moves the number the
other way, because the reassuring answer in most of them ("the file is small
now", "the .bak files are untouched") is also what a harness wired to nothing
produces.
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ROTATE = ROOT / "scripts" / "claude-log-rotate" / "rotate.sh"


def _print_config(target="/home/someone/.claude", **env):
    e = dict(os.environ)
    e.update(env)
    p = subprocess.run(["bash", str(ROTATE), "--print-config", target],
                       capture_output=True, text=True, env=e)
    assert p.returncode == 0, p.stderr
    return p.stdout


def _run(target, **env):
    e = dict(os.environ)
    e.update(env)
    return subprocess.run(["bash", str(ROTATE), str(target)],
                          capture_output=True, text=True, env=e)


# --------------------------------------------------------------------------- #
# 0. harness self-validation
# --------------------------------------------------------------------------- #
def test_the_script_exists_and_is_executable():
    assert ROTATE.is_file(), f"{ROTATE} missing — every test below is vacuous"
    assert os.access(ROTATE, os.X_OK), f"{ROTATE} is not executable"


def test_logrotate_is_on_path():
    """🔴 NOT a skipif. `logrotate` is declared in flake.nix's pytests check and
    in run-tests.sh's REQUIRED_TOOLS. If it is missing, the behavioural tests
    below would silently stop measuring anything — so fail loudly instead."""
    assert shutil.which("logrotate") is not None, (
        "logrotate is not on PATH. Add it to the pytests check in flake.nix "
        "(and REQUIRED_TOOLS in scripts/run-tests.sh) rather than skipping "
        "these tests — a skipped rotation test reports safety it never checked."
    )


# --------------------------------------------------------------------------- #
# 1. the generated config — the policy, pinned
# --------------------------------------------------------------------------- #
def test_config_caps_size_and_keeps_a_few_generations():
    conf = _print_config()
    assert "size 10M" in conf
    assert "rotate 3" in conf


def test_config_uses_copytruncate():
    """🔴 Load-bearing, not an optimisation. The writers (a hook that runs on
    every tool call, plus two out-of-repo processes) hold open fds. Renaming out
    from under them would leave them appending to an unlinked inode — the log
    would look frozen while consuming the same disk."""
    assert "copytruncate" in _print_config()


def test_config_targets_only_log_files_and_never_the_bak_files():
    """🔴 The scope fence. ~/.claude holds 9 stale hand-made `.bak` copies of
    config (settings.json.bak.*, RULES.md.bak*, PRINCIPLES.md.bak*,
    CLAUDE.md.bak-*). An automated job must not be the thing that decides to
    delete a config backup."""
    conf = _print_config()
    assert '"/home/someone/.claude/*.log"' in conf
    assert ".bak" not in conf
    # exactly one glob stanza, so a second pattern cannot creep in unnoticed
    assert conf.count("{") == 1


def test_config_glob_is_quoted_so_it_is_evaluated_at_rotate_time():
    """An unquoted pattern would be expanded by the SHELL when the config is
    generated, freezing the file list — a log created later would never rotate.
    logrotate must do the globbing itself."""
    conf = _print_config()
    assert conf.lstrip().startswith('"')


def test_config_honours_the_env_tunables():
    conf = _print_config(CLAUDE_LOG_ROTATE_SIZE="1k", CLAUDE_LOG_ROTATE_KEEP="7")
    assert "size 1k" in conf
    assert "rotate 7" in conf
    # negative control: the defaults are gone, so this is really reading the env
    assert "size 10M" not in conf
    assert "rotate 3" not in conf


def test_config_target_directory_is_the_argument():
    assert '"/tmp/elsewhere/*.log"' in _print_config("/tmp/elsewhere")


# --------------------------------------------------------------------------- #
# 2. behaviour — a real logrotate run
# --------------------------------------------------------------------------- #
@pytest.fixture
def logdir(tmp_path):
    d = tmp_path / "dot-claude"
    d.mkdir()
    return d


def _mk(path: Path, size: int):
    path.write_bytes(b"x" * size)
    return path


def test_an_oversized_log_is_rotated_and_truncated(logdir, tmp_path):
    """POSITIVE CONTROL for the whole harness: the number must MOVE. A big log
    becomes a small live file plus a rotated generation."""
    big = _mk(logdir / "notify.log", 200_000)
    assert big.stat().st_size == 200_000
    p = _run(logdir, CLAUDE_LOG_ROTATE_SIZE="1k",
             CLAUDE_LOG_ROTATE_STATE=str(tmp_path / "st"))
    assert p.returncode == 0, p.stderr
    assert big.exists(), "copytruncate must keep the original file in place"
    assert big.stat().st_size == 0, "the live file was not truncated"
    rotated = list(logdir.glob("notify.log.*"))
    assert rotated, f"nothing was rotated; dir={list(logdir.iterdir())}"


def test_a_small_log_is_left_alone(logdir, tmp_path):
    """NEGATIVE CONTROL, same harness, same code path: under the threshold
    nothing happens. Paired with the test above, a rotation result cannot be
    'this script rotates unconditionally'."""
    small = _mk(logdir / "daemon.log", 100)
    p = _run(logdir, CLAUDE_LOG_ROTATE_SIZE="1M",
             CLAUDE_LOG_ROTATE_STATE=str(tmp_path / "st"))
    assert p.returncode == 0, p.stderr
    assert small.stat().st_size == 100
    assert list(logdir.glob("daemon.log.*")) == []


def test_bak_and_other_files_are_never_touched(logdir, tmp_path):
    """🔴 The scope fence, measured rather than read off the config. Each of
    these is far OVER the threshold, so 'untouched' cannot be an accident of
    them being small — the ONLY reason they survive is the `*.log` glob."""
    victims = {
        "settings.json.bak.1769397206": 200_000,
        "RULES.md.bak.20260608-081434": 200_000,
        "PRINCIPLES.md.bak-20260616-204947": 200_000,
        "CLAUDE.md.bak-2026-07-30": 200_000,
        "settings.json": 200_000,
        "notes.md": 200_000,
    }
    for name, size in victims.items():
        _mk(logdir / name, size)
    # a real .log in the same directory, so the run definitely DOES something —
    # otherwise "nothing was touched" would also be true of a no-op run.
    canary = _mk(logdir / "notify.log", 200_000)
    before = {f.name for f in logdir.iterdir()}

    p = _run(logdir, CLAUDE_LOG_ROTATE_SIZE="1k",
             CLAUDE_LOG_ROTATE_STATE=str(tmp_path / "st"))
    assert p.returncode == 0, p.stderr
    assert canary.stat().st_size == 0, (
        "the canary .log was not rotated, so this run proves nothing about "
        "what the glob spared"
    )
    for name, size in victims.items():
        f = logdir / name
        assert f.exists(), f"{name} was DELETED"
        assert f.stat().st_size == size, f"{name} was modified"
    # Nothing NEW may appear except a rotation of the canary. Checked as a set
    # difference rather than per-file globs: `settings.json.*` would match the
    # victim `settings.json.bak.…`, i.e. the obvious per-file glob reports a
    # rotation that never happened.
    created = {f.name for f in logdir.iterdir()} - before
    assert created and all(n.startswith("notify.log.") for n in created), (
        f"unexpected files created: {sorted(created)}"
    )


def test_generations_are_capped(logdir, tmp_path):
    """`rotate N` must actually bound the number of kept generations, or the
    directory grows without limit in a different shape."""
    state = tmp_path / "st"
    log = logdir / "clawgate-hook.log"
    for _ in range(8):
        _mk(log, 200_000)
        p = _run(logdir, CLAUDE_LOG_ROTATE_SIZE="1k",
                 CLAUDE_LOG_ROTATE_KEEP="2", CLAUDE_LOG_ROTATE_STATE=str(state))
        assert p.returncode == 0, p.stderr
    gens = list(logdir.glob("clawgate-hook.log.*"))
    assert 0 < len(gens) <= 2, f"expected at most 2 generations, got {gens}"


def test_a_missing_directory_is_not_an_error(tmp_path):
    """`missingok`: the timer fires on both hosts and must not spam
    notify-failure@ on a host that has no ~/.claude logs yet."""
    p = _run(tmp_path / "nope", CLAUDE_LOG_ROTATE_STATE=str(tmp_path / "st"))
    assert p.returncode == 0, p.stderr


# --------------------------------------------------------------------------- #
# 3. wiring — a rotation script nothing runs rotates nothing
#
# The script is only half the change; the timer is the other half, and a
# perfectly green script test says nothing about whether it ever fires. These
# read nix/home.nix directly, the same way test_opencode_guard_plugin.py pins
# the guard's deploy path.
# --------------------------------------------------------------------------- #
HOME_NIX = (ROOT / "nix" / "home.nix").read_text()


def test_home_nix_declares_the_service_and_the_timer():
    assert "systemd.user.services.claude-log-rotate" in HOME_NIX
    assert "systemd.user.timers.claude-log-rotate" in HOME_NIX


def test_the_unit_runs_the_script_this_file_tests():
    """The ExecStart must point at the script under test, or these tests are
    about a file nothing executes.

    🔴 Asserted on the ExecStart LINE, not on the file. A mutation sweep caught
    the whole-file version surviving: the path also appears in the surrounding
    comment and in `X-Restart-Triggers`, so a substring test over home.nix
    stayed green with ExecStart repointed at a completely different script.
    """
    execs = [l.strip() for l in HOME_NIX.splitlines() if "ExecStart" in l
             and "claude-log-rotate" in l]
    assert len(execs) == 1, f"expected one claude-log-rotate ExecStart, got {execs}"
    assert execs[0].endswith('scripts/claude-log-rotate/rotate.sh";'), execs[0]


def test_the_unit_puts_logrotate_on_its_path():
    """🔴 The unit env is minimal, so PATH is explicit. Without pkgs.logrotate
    the wrapper exits non-zero every night (loudly, by design — see
    test_it_fails_loudly_when_logrotate_is_absent) and nothing is ever capped."""
    svc = HOME_NIX.split("systemd.user.services.claude-log-rotate", 1)[1]
    svc = svc.split("systemd.user.timers.claude-log-rotate", 1)[0]
    assert "pkgs.logrotate" in svc


def test_the_timer_is_not_server_mode_gated():
    """Both hosts run Claude Code and both accumulate these logs. A
    `lib.mkIf serverMode` here would silently leave the laptop uncapped —
    the exact shape of "shipped, but not where the problem is"."""
    timer = HOME_NIX.split("systemd.user.timers.claude-log-rotate", 1)[1]
    timer = timer.split("Install", 1)[0]
    assert "serverMode" not in timer
    svc = HOME_NIX.split("systemd.user.services.claude-log-rotate", 1)[1]
    svc = svc.split("Unit", 1)[0]
    assert "serverMode" not in svc


def test_the_timer_is_persistent():
    """Growth is slow and unattended; a host powered off at 04:00 must catch up
    rather than skip the cycle."""
    timer = HOME_NIX.split("systemd.user.timers.claude-log-rotate", 1)[1]
    assert "Persistent = true" in timer.split("Install", 1)[0]


def test_logrotate_is_pinned_in_both_gate_tiers():
    """🔴 The two-tier lesson: a tool present in one tier and absent in the
    other makes these tests' verdict depend on where they ran. `logrotate` must
    be in the flake check's inputs AND in run-tests.sh's REQUIRED_TOOLS."""
    flake = (ROOT / "flake.nix").read_text()
    assert "pkgs.logrotate" in flake
    runner = (ROOT / "scripts" / "run-tests.sh").read_text()
    required = [l for l in runner.splitlines() if l.startswith("REQUIRED_TOOLS=")]
    assert len(required) == 1, f"expected one REQUIRED_TOOLS line, got {required}"
    assert "logrotate" in required[0]


def test_it_fails_loudly_when_logrotate_is_absent(logdir, tmp_path):
    """🔴 The wrapper must not report success when it did nothing. Simulated by
    handing it an empty PATH — the same shape as a unit whose PATH= line lost
    the package."""
    _mk(logdir / "notify.log", 200_000)
    e = dict(os.environ)
    e["PATH"] = str(tmp_path / "empty-bin")
    e["CLAUDE_LOG_ROTATE_STATE"] = str(tmp_path / "st")
    # bash by ABSOLUTE path: emptying PATH also hides the interpreter, and
    # "bash not found" would be a different failure wearing this test's name.
    bash = shutil.which("bash")
    assert bash, "bash not on PATH — this test cannot run"
    p = subprocess.run([bash, str(ROTATE), str(logdir)],
                       capture_output=True, text=True, env=e)
    assert p.returncode != 0
    assert "logrotate not on PATH" in p.stderr
