"""ship.sh's address SELECTION, and the real `ssh` probe line.

WHY THIS EXISTS. PR #1439's round-1 audit measured two holes that its own new
tests could not see, because those tests source `lib/host-role.sh` directly and
never load `ship.sh`:

  1. Deleting ship.sh's ENTIRE address-selection block (42 lines) left the suite
     green -- 98/98 in `test_ship_converge.py`. Every ship.sh fixture that
     reaches an ssh shim sets `$REMOTE_SSH`, which is a one-element candidate
     list, which skips the probe. So the bug the PR fixes could be reintroduced
     wholesale with nothing going red.
  2. The production probe line was never executed by any test: every probe test
     sets `$SSH_PROBE_CMD`, taking the OTHER branch. Mutating the real line --
     `StrictHostKeyChecking=accept-new` -> `=no`, `ConnectTimeout` -> `99999`,
     and the remote command `true` -> `/bin/false` -- SURVIVED all three at
     once. The `/bin/false` mutant alone makes every probe report "did not
     answer", i.e. renders the whole fallback inert while the suite stays green.

The first is closed via `SHIP_PRINT_REMOTE_TARGET=1`, a hidden mode that runs
role resolution + address selection and prints the target it would use. The
second is closed by putting a recording `ssh` FIRST ON PATH and asserting on the
argv the real line actually builds -- no network, no real host.
"""

import os
import subprocess
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
SHIP = SCRIPTS / "ship.sh"
LIB = SCRIPTS / "lib" / "host-role.sh"

LAPTOP_LAN = "zach@192.168.50.155"
LAPTOP_NEBULA = "zach@10.42.0.100"


def _probe_stub(tmp_path: Path, reachable: str, log: Path | None = None) -> str:
    """A $SSH_PROBE_CMD that succeeds only for `reachable`, optionally logging."""
    p = tmp_path / "probe.sh"
    body = "#!/usr/bin/env bash\n"
    if log:
        body += f'echo "$1" >> "{log}"\n'
    body += f'[ "$1" = "{reachable}" ]\n'
    p.write_text(body)
    p.chmod(0o755)
    return str(p)


def _ship_target(tmp_path: Path, env: dict) -> subprocess.CompletedProcess:
    """Run ship.sh's hidden target-resolution mode with a forced role."""
    full = {**os.environ, "SHIP_ROLE": "workbench",
            "SHIP_PRINT_REMOTE_TARGET": "1", **env}
    for var in ("REMOTE_SSH", "LAPTOP_SSH"):
        if var not in env:
            full.pop(var, None)
    return subprocess.run(
        ["bash", str(SHIP)], capture_output=True, text=True, env=full, timeout=120
    )


# --------------------------------------------------------------------------- #
# ship.sh's selection block -- the 42 lines that were provably unguarded
# --------------------------------------------------------------------------- #

def test_ship_falls_back_to_nebula_and_SAYS_SO(tmp_path):
    """THE MEASURED BUG, at the ship.sh level rather than the lib's."""
    res = _ship_target(tmp_path, {"SSH_PROBE_CMD": _probe_stub(tmp_path, LAPTOP_NEBULA)})
    assert res.returncode == 0, res.stderr
    assert res.stdout.strip() == LAPTOP_NEBULA, (
        f"ship.sh resolved {res.stdout.strip()!r}; the whole point of the change "
        "is that a silent LAN address does not end the run"
    )
    assert "falling back to" in res.stderr, (
        "the fallback was taken SILENTLY -- an operator reading the log cannot "
        f"tell which address was used:\n{res.stderr}"
    )


def test_ship_prefers_the_lan_address_and_stays_quiet_about_it(tmp_path):
    res = _ship_target(tmp_path, {"SSH_PROBE_CMD": _probe_stub(tmp_path, LAPTOP_LAN)})
    assert res.stdout.strip() == LAPTOP_LAN
    assert "falling back" not in res.stderr, (
        f"announced a fallback that did not happen:\n{res.stderr}"
    )


def test_ship_names_every_address_when_none_answers(tmp_path):
    """🔴 The diagnosis is the whole point: 'off' must not read like 'wrong network'."""
    res = _ship_target(
        tmp_path, {"SSH_PROBE_CMD": _probe_stub(tmp_path, "zach@nothing-answers")}
    )
    assert "NO candidate address answered" in res.stderr, res.stderr
    for addr in (LAPTOP_LAN, LAPTOP_NEBULA):
        assert addr in res.stderr, f"{addr} was tried but not named:\n{res.stderr}"
    assert res.stdout.strip() == LAPTOP_LAN, (
        "with nothing reachable the run must keep its DERIVED default and let "
        "the remote leg report the failure, not invent a target"
    )


@pytest.mark.parametrize("override", ["REMOTE_SSH", "LAPTOP_SSH"])
def test_an_explicit_target_is_never_probed_or_redirected(tmp_path, override):
    """🔴 The negative contract, at the ship.sh level.

    Asserts the stronger thing: the probe is not merely ignored, it never RUNS.
    A probe that executed and was then discarded would still make a connection
    the operator did not ask for -- which is what perturbed a convergence test
    once already.
    """
    log = tmp_path / "probed.log"
    res = _ship_target(tmp_path, {
        override: "zach@10.0.0.9",
        "SSH_PROBE_CMD": _probe_stub(tmp_path, "zach@10.0.0.9", log=log),
    })
    assert res.stdout.strip() == "zach@10.0.0.9"
    assert not log.exists(), (
        f"the probe RAN for an explicitly addressed host: {log.read_text()!r}"
    )


def test_the_skip_switch_turns_the_probe_off(tmp_path):
    """`SHIP_SKIP_SSH_PROBE` is the documented escape hatch -- unexercised until now."""
    log = tmp_path / "probed.log"
    res = _ship_target(tmp_path, {
        "SHIP_SKIP_SSH_PROBE": "1",
        "SSH_PROBE_CMD": _probe_stub(tmp_path, LAPTOP_NEBULA, log=log),
    })
    assert res.stdout.strip() == LAPTOP_LAN, (
        "with the probe skipped the first derived candidate must be used as-is"
    )
    assert not log.exists(), "SHIP_SKIP_SSH_PROBE=1 still probed"


# --------------------------------------------------------------------------- #
# The REAL ssh line -- executed via a recording stub first on PATH
# --------------------------------------------------------------------------- #

def _recording_ssh(tmp_path: Path, exit_code: int = 0) -> tuple[Path, Path]:
    """An `ssh` on PATH that records its argv and exits `exit_code`."""
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    log = tmp_path / "argv.log"
    stub = bindir / "ssh"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        f'printf "%s\\n" "$*" >> "{log}"\n'
        f"exit {exit_code}\n"
    )
    stub.chmod(0o755)
    return bindir, log


def _run_probe_with_real_branch(tmp_path, bindir, extra_env=None):
    env = {**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}"}
    env.pop("SSH_PROBE_CMD", None)          # force the REAL branch
    env.update(extra_env or {})
    return subprocess.run(
        ["bash", "-c",
         f'set -uo pipefail; source "{LIB}"; first_reachable_ssh {LAPTOP_LAN} {LAPTOP_NEBULA}'],
        capture_output=True, text=True, env=env, timeout=120,
    )


def test_the_real_probe_line_builds_the_argv_it_claims(tmp_path):
    """🔴 Kills three mutants that SURVIVED a fully green suite.

    Each assertion below corresponds to one: dropping `-n` (stdin theft),
    loosening `StrictHostKeyChecking`, unbounding `ConnectTimeout`, and running
    something other than the harmless `true` on the remote host.
    """
    bindir, log = _recording_ssh(tmp_path)
    res = _run_probe_with_real_branch(tmp_path, bindir)
    assert res.stdout.strip() == LAPTOP_LAN, res.stderr
    argv = log.read_text().splitlines()[0]

    assert "-n" in argv.split(), f"probe lost -n, so it eats the caller's stdin: {argv}"
    assert "BatchMode=yes" in argv, f"probe may now prompt: {argv}"
    assert "StrictHostKeyChecking=accept-new" in argv, (
        f"host-key policy changed; `=no` would trust a CHANGED key, which is "
        f"exactly what the fallback must refuse: {argv}"
    )
    assert "ConnectTimeout=5" in argv, (
        f"the default bound moved -- an unbounded probe hangs the run it was "
        f"added to speed up: {argv}"
    )
    assert argv.rstrip().endswith(" true"), (
        f"the probe runs something other than `true` on the remote host: {argv}"
    )


def test_the_probe_timeout_is_actually_applied(tmp_path):
    """$SSH_PROBE_TIMEOUT reaches ssh -- otherwise the knob is decorative."""
    bindir, log = _recording_ssh(tmp_path)
    _run_probe_with_real_branch(tmp_path, bindir, {"SSH_PROBE_TIMEOUT": "11"})
    assert "ConnectTimeout=11" in log.read_text()


def test_the_probes_diagnostic_carries_the_CALLERS_prefix(tmp_path):
    """🔴 SEAM: the lib is shared, and its other caller writes a checked journal.

    `drift-check.sh` accepts only lines starting with `[`, `===`,
    `drift-check: ` or two spaces. A hardcoded `ship:` here fails that hygiene
    guard -- measured, as
    `test_the_ladder_escalates_when_the_streak_FILE_cannot_be_written` -- and
    independently misattributes the message to the wrong program in the
    operator's log. Both callers are asserted so neither can regress alone.
    """
    stub = _probe_stub(tmp_path, "zach@nothing-answers")
    for prefix, expected in (("", "ship: "), ("drift-check", "drift-check: ")):
        env = {**os.environ, "SSH_PROBE_CMD": stub}
        env.pop("REMOTE_SSH", None)
        if prefix:
            env["SSH_PROBE_LOG_PREFIX"] = prefix
        else:
            env.pop("SSH_PROBE_LOG_PREFIX", None)
        res = subprocess.run(
            ["bash", "-c",
             f'set -uo pipefail; source "{LIB}"; first_reachable_ssh {LAPTOP_LAN}'],
            capture_output=True, text=True, env=env, timeout=60,
        )
        assert res.stderr.startswith(expected), (
            f"with SSH_PROBE_LOG_PREFIX={prefix!r} the diagnostic must start "
            f"{expected!r}; got {res.stderr!r}"
        )


def test_a_failing_real_probe_moves_to_the_next_address(tmp_path):
    """The `/bin/false` mutant made every probe fail; this pins the consequence."""
    bindir, log = _recording_ssh(tmp_path, exit_code=255)
    res = _run_probe_with_real_branch(tmp_path, bindir)
    assert res.returncode != 0, "every address failed, yet a target was returned"
    assert res.stdout.strip() == ""
    tried = log.read_text().splitlines()
    assert len(tried) == 2, f"both addresses should have been tried: {tried}"
