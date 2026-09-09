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

The first is closed via `--print-remote-target`, a flag that runs role
resolution + address selection and prints the target it would use. (It began as
an env var and was changed to argv in round 2: an env seam is INHERITABLE, and an
inherited copy makes an ordinary `ship.sh` print an address and exit 0 having
converged nothing.) The
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
    full = {**os.environ, "SHIP_ROLE": "workbench", **env}
    for var in ("REMOTE_SSH", "LAPTOP_SSH"):
        if var not in env:
            full.pop(var, None)
    return subprocess.run(
        ["bash", str(SHIP), "--print-remote-target"],
        capture_output=True, text=True, env=full, timeout=120,
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


def test_the_seam_cannot_be_reached_through_the_ENVIRONMENT(tmp_path):
    """🔴 The env->flag change, guarded — it was not, by this file's own standard.

    As `$SHIP_PRINT_REMOTE_TARGET` this seam was INHERITABLE: an operator who
    exported it while debugging would get, from every later `ship.sh` in that
    shell, an address printed and rc 0 having converged NOTHING. Reverting the
    initialiser to `${SHIP_PRINT_REMOTE_TARGET:-0}` is otherwise undetectable,
    because after the change no test sets the variable at all.

    So: export it, pass NO flag, and require a real run — which here means it
    must NOT print a bare address and exit 0.
    """
    env = {**os.environ, "SHIP_ROLE": "workbench",
           "SHIP_PRINT_REMOTE_TARGET": "1",
           "SSH_PROBE_CMD": _probe_stub(tmp_path, LAPTOP_NEBULA),
           "SHIP_REPO": str(tmp_path / "norepo"),
           "SHIP_NO_SWITCH": "1"}
    for var in ("REMOTE_SSH", "LAPTOP_SSH"):
        env.pop(var, None)
    res = subprocess.run(["bash", str(SHIP), "--no-remote"], capture_output=True,
                         text=True, env=env, timeout=300)
    printed = res.stdout.strip().splitlines()
    assert printed[:1] != [LAPTOP_NEBULA] and printed[:1] != [LAPTOP_LAN], (
        "an EXPORTED SHIP_PRINT_REMOTE_TARGET still triggered the seam: the run "
        f"printed {printed[:1]!r} instead of converging. That is the inheritable "
        "vacuous green the flag exists to make impossible."
    )


def test_the_flag_is_listed_in_help(tmp_path):
    """The Usage block is what `--help` prints, and nothing read it.

    `ship.sh` asserts in a comment that this flag "appears in --help like every
    other option". It did not, for one commit: the flag was added to the arg
    loop and not to the header. Deleting the Usage lines again is otherwise
    undetectable -- and this round treated exactly that shape (an unguarded
    fix) as a defect worth closing, so it should not leave one behind.

    The positive control matters: `--help` prints a comment block by an awk
    range, so a header edit can silently truncate the whole listing. Asserting
    only the new flag would then pass while every other option vanished.
    """
    res = subprocess.run(["bash", str(SHIP), "--help"], capture_output=True,
                         text=True, timeout=60)
    assert res.returncode == 0, res.stderr
    assert "--print-remote-target" in res.stdout, (
        "the flag is not in the Usage block that --help prints, so a reader "
        "sent there by ship.sh's own comment will not find it"
    )
    for control in ("--detect-role", "--no-remote", "--no-switch"):
        assert control in res.stdout, (
            f"{control} is missing too -- the header listing is truncated, so "
            "the assertion above proves nothing about the flag specifically"
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
    guard and independently misattributes the message to the wrong program in
    the operator's log.

    ⚠ SCOPE: this asserts the LIB under both prefix values. The drift-check
    side is asserted by
    `test_drift_checks_probe_output_is_journal_clean` below, IN THIS FILE.

    🔴 That test exists because this docstring has now been wrong twice. Draft 1
    said "both callers are asserted so neither can regress alone" -- false, this
    module loaded one. Draft 2 pointed at
    `test_the_ladder_escalates_when_the_streak_FILE_cannot_be_written` in
    `test_drift_check.py` -- true when written, and FALSE BY THE TIME IT WAS
    WRITTEN: the same commit stopped `--no-remote` from probing and defaulted
    `DRIFT_SKIP_SSH_PROBE=1` module-wide, so that test can no longer emit a
    probe line at all. Measured: deleting `SSH_PROBE_LOG_PREFIX=drift-check`
    scored 594 passed. A correction that pointed at coverage its own commit had
    just removed.
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


def test_the_degraded_block_defines_the_function_the_selection_calls(tmp_path):
    """🔴 The lib-less recovery path, which had NO test and reproduced its bug.

    `ship.sh` runs in "degraded recovery mode" when `lib/host-role.sh` is
    missing -- the state you are in when a host is already broken, and the one
    the script's own recovery instructions tell you to use. The selection block
    calls `remote_ssh_candidates_of` unconditionally, so if the degraded block
    does not define it the run prints `command not found` into the middle of a
    recovery. Measured: deleting that one line reproduced the symptom while
    `pytest -k "without_the_lib or through_a_symlink"` stayed green, 4 passed.
    """
    lonely = tmp_path / "scripts"
    lonely.mkdir()
    (lonely / "ship.sh").write_text(SHIP.read_text())
    env = {**os.environ, "SHIP_ROLE": "workbench", "REMOTE_SSH": "zach@10.0.0.9"}
    env.pop("LAPTOP_SSH", None)
    res = subprocess.run(
        ["bash", str(lonely / "ship.sh"), "--print-remote-target"],
        capture_output=True, text=True, env=env, timeout=120,
    )
    assert "degraded recovery mode" in res.stderr, (
        f"expected the lib-less path; got:\n{res.stderr}"
    )
    assert "command not found" not in res.stderr, (
        "an undefined function crashed into the middle of a RECOVERY run -- the "
        f"exact shape this file rejects elsewhere:\n{res.stderr}"
    )
    assert res.stdout.strip() == "zach@10.0.0.9", (
        f"degraded mode must still honour an explicit target; got {res.stdout!r}"
    )


def test_drift_check_makes_no_ssh_connection_under_no_remote(tmp_path):
    """🔴 `--no-remote` is documented as "this host only (no ssh)".

    MEASURED before the fix: it made two outbound probe connections to the
    operator's real laptop, and `test_drift_check.py` -- which passes
    `--no-remote` throughout -- issued 558 of them across the module (279 per
    address; an earlier note said 568, which was my own count and was wrong). A
    read-only breach is still a breach, and with the laptop off-LAN each probe
    burns the full ConnectTimeout, so the suite's runtime and verdict began
    depending on the operator's network.

    The positive control below is the point: it proves this test can SEE a
    probe, so the zero above is a measurement rather than a wiring accident.
    """
    drift = SCRIPTS / "drift-check.sh"
    bindir, log = _recording_ssh(tmp_path, exit_code=255)
    env = {**os.environ, "SHIP_ROLE": "workbench",
           "PATH": f"{bindir}:{os.environ['PATH']}"}
    for var in ("REMOTE_SSH", "LAPTOP_SSH", "DRIFT_SKIP_SSH_PROBE"):
        env.pop(var, None)
    # 🔴 THIS TEST RUNS THE PRODUCTION DEADMAN, so every default it does not
    # override reaches the OPERATOR'S REAL MACHINE. Measured before these three
    # keys existed: it fetched in `$HOME/workspace/{devrc,homelab-talos,
    # tmux-fuzzyclaw}` (truncating FETCH_HEAD in three SHARED checkouts), made
    # authenticated `gh api` calls as the real user, and — worst — incremented
    # the real `unreachable-laptop` escalation streak by one PER RUN, because
    # the stub ssh always fails. Four suite runs inside one 6h timer window
    # would drive that counter to DRIFT_UNREACHABLE_ESCALATE and fire the
    # DND-bypassing failure toast for a host whose true state never got there.
    #
    # That is the same breach this very test was written to close, one layer
    # out: `test_drift_check.py`'s fixture pins these three for exactly this
    # reason and calls it a hermeticity seam. A test that reaches a live host to
    # prove nothing reaches a live host is not a test.
    env["DRIFT_REPO"] = str(tmp_path / "norepo")
    env["DRIFT_STATE_DIR"] = str(tmp_path / "state")
    env["DRIFT_GH"] = str(tmp_path / "no-gh")   # absent -> the arm takes its no-gh branch
    # 🔴 AND THE OTHER TWO. Pinning the three above was measured INSUFFICIENT:
    # `DRIFT_SESSION_MANAGER` defaults to the real 352 KB program, which the
    # phase-2 gate execs to scan the operator's LIVE tmux (57 rows, measured) --
    # `drift-check.sh` says of that scan "which no test may do", and
    # `test_drift_check.py` pins it as its FOURTH hermeticity seam for the same
    # reason. `$HOME` is the other: unredirected, each run walks the real
    # ~/.claude and ~/.config/opencode (344 managed symlinks) and reads
    # settings.json. Read-only, but the verdict then depends on the operator's
    # live session state -- and pinning both is FASTER: 3 session-manager execs
    # to 0, these tests 8.28s to 0.84s.
    env["DRIFT_SESSION_MANAGER"] = str(tmp_path / "no-session-manager")
    env["HOME"] = str(tmp_path / "home")

    subprocess.run(["bash", str(drift), "--no-remote"], capture_output=True,
                   text=True, env=env, timeout=300)
    probes = [ln for ln in (log.read_text().splitlines() if log.exists() else [])
              if "BatchMode=yes" in ln and " true" in ln]
    assert probes == [], (
        "--no-remote is documented as making no ssh connection, but the address "
        f"probe fired: {probes}"
    )

    # POSITIVE CONTROL — the same harness WITH the remote leg in scope must see
    # probes, or the empty list above proves nothing about the gate.
    log.unlink(missing_ok=True)
    subprocess.run(["bash", str(drift)], capture_output=True, text=True,
                   env=env, timeout=300)
    seen = [ln for ln in (log.read_text().splitlines() if log.exists() else [])
            if "BatchMode=yes" in ln and " true" in ln]
    assert seen, (
        "the control saw NO probe even with the remote leg in scope, so this "
        "test cannot distinguish a working gate from a probe that never runs"
    )


def test_drift_checks_probe_output_is_journal_clean(tmp_path):
    """🔴 The drift-check half of the prefix seam, asserted where it is claimed.

    `drift-check.sh` writes a journal whose every line must begin with `[`,
    `===`, `drift-check: ` or two spaces. The probe lives in a SHARED lib whose
    other caller is `ship.sh`, so its diagnostic carries whichever prefix the
    caller sets -- and an unset one says `ship:`, which both fails the hygiene
    rule and misattributes the line to a program that is not running.

    This drives the probe deliberately (`DRIFT_SKIP_SSH_PROBE=0`, remote leg in
    scope) with an ssh that always fails, so both candidates report. Hermetic:
    repo, state dir and gh are pinned into tmp_path, and ssh is a stub.
    """
    drift = SCRIPTS / "drift-check.sh"
    bindir, _log = _recording_ssh(tmp_path, exit_code=255)
    env = {**os.environ, "SHIP_ROLE": "workbench",
           "PATH": f"{bindir}:{os.environ['PATH']}",
           "DRIFT_SKIP_SSH_PROBE": "0",
           "DRIFT_REPO": str(tmp_path / "norepo"),
           "DRIFT_STATE_DIR": str(tmp_path / "state2"),
           "DRIFT_GH": str(tmp_path / "no-gh"),
           # Same five seams as the test above -- see its note. The real
           # session-manager scans live tmux; an unredirected HOME walks the
           # operator's ~/.claude.
           "DRIFT_SESSION_MANAGER": str(tmp_path / "no-session-manager"),
           "HOME": str(tmp_path / "home2")}
    for var in ("REMOTE_SSH", "LAPTOP_SSH"):
        env.pop(var, None)

    res = subprocess.run(["bash", str(drift)], capture_output=True, text=True,
                         env=env, timeout=300)
    out = res.stdout + res.stderr
    probe_lines = [ln for ln in out.splitlines() if "did not answer" in ln]
    assert probe_lines, (
        "the probe produced no diagnostic, so this test cannot see the prefix "
        f"it exists to check:\n{out[-2000:]}"
    )
    for ln in probe_lines:
        assert ln.startswith("drift-check: "), (
            f"probe line {ln!r} does not carry drift-check's prefix. The shared "
            "lib defaults to `ship:`, which fails this script's journal hygiene "
            "rule and names the wrong program in the operator's log."
        )


def test_a_failing_real_probe_moves_to_the_next_address(tmp_path):
    """The `/bin/false` mutant made every probe fail; this pins the consequence."""
    bindir, log = _recording_ssh(tmp_path, exit_code=255)
    res = _run_probe_with_real_branch(tmp_path, bindir)
    assert res.returncode != 0, "every address failed, yet a target was returned"
    assert res.stdout.strip() == ""
    tried = log.read_text().splitlines()
    assert len(tried) == 2, f"both addresses should have been tried: {tried}"
