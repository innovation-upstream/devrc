"""gate.sh's concurrency slot limiter and its dev-shell re-exec.

WHY THIS FILE EXISTS — both features were built from measurements, and both are
the kind whose failure mode is a HANG or a silently-skipped tier rather than a
wrong answer, so they need controls that watch them actually bite.

SLOT LIMITER. Nothing serialised gate runs. Measured 2026-09-08 across 237 real
runs, bucketed by overlapping runs: 0 others -> 14.5 min median, 6+ others ->
48.9 min. 3.4x, and superlinear (7 runs x 4 workers on 24 cores fair-shares to
~1.2x). 35 of the 117 red runs in that window died on SIGTERM at the 3600s cap,
producing no verdict for an hour of wall clock.

RE-EXEC. `run-tests.sh` refuses to run without its REQUIRED_TOOLS and prints the
`nix develop` line that fixes it. 100 of the 100 gate log dirs in /tmp using the
default log location died on exactly that, pytest never starting — a correct
instruction being re-typed by hand, 100 times.

Every test here drives the REAL gate.sh with the documented runner seams and its
own private slot dir, so nothing it does can contend with a live gate run on the
box, and nothing on the box can make it flake.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(_HERE, os.pardir)))
from testlib.mockbin import write_exec  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
GATE = REPO / "scripts" / "gate.sh"

# 🔴 MATCH A PHRASE NO PATH CAN CONTAIN. The first version of these tests looked
# for the bare word "queueing" in the gate's output — and pytest's `tmp_path` is
# named after the TEST, so a test called `..._queueing_...` handed the gate a
# --log-dir containing "queueing", which the gate echoes back in its very first
# line. The assertion matched the test's own name and passed against a gate.sh
# that has no limiter in it at all: green on the base commit, for a feature that
# did not exist. Parentheses and the space make this unreachable by any path.
QUEUED_MARKER = "slot(s) busy"


def _fake_runner(path: Path, *, sleep: float = 0.0, verdict: str = "PASS") -> Path:
    """A stand-in runner that prints a well-formed verdict and exits to match."""
    rc = 0 if verdict == "PASS" else 1
    path.parent.mkdir(parents=True, exist_ok=True)
    # 🔴 write_exec owns the shebang, and that is not style. An env-based
    # interpreter path written at RUNTIME execs on this NixOS dev host and NOT
    # in the nix build sandbox, which is the authoritative tier — so the defect
    # is structurally invisible to the tier most people run. This file arrived
    # carrying it and test_runtime_shebangs.py caught it on the first sandbox
    # run, which is that guard working.
    return write_exec(
        path,
        textwrap.dedent(
            f"""\
            echo "======== FAKE SUMMARY ========"
            sleep {sleep}
            echo "RESULT: {verdict} (exit={rc})"
            exit {rc}
            """
        ),
    )


def _gate_env(tmp_path: Path, **over: str) -> dict[str, str]:
    env = dict(os.environ)
    runner = _fake_runner(tmp_path / "fake-runner.sh")
    env.update(
        {
            "DEVRC_GATE_PYTEST_RUNNER": str(runner),
            "DEVRC_GATE_NODE_RUNNER": str(runner),
            # Never let a test re-enter `nix develop`: it would cost minutes and
            # would be testing nix, not this script.
            "DEVRC_GATE_NO_REEXEC": "1",
        }
    )
    env.update(over)
    return env


def _run_gate(tmp_path: Path, env: dict[str, str], *args: str, timeout: int = 120):
    return subprocess.run(
        ["bash", str(GATE), "--log-dir", str(tmp_path / "logs"), *args, str(REPO)],
        capture_output=True,
        text=True,
        env=env,
        timeout=timeout,
    )


# --- the limiter actually blocks ----------------------------------------------


def test_a_second_gate_waits_while_the_only_slot_is_held(tmp_path):
    """THE point of the feature: with 1 slot, run B starts only after A frees it.

    Run A holds its slot for ~4s. B is launched ~0.5s later and must not finish
    before A does. Asserting on ORDERING, not on a duration threshold, so a
    loaded box cannot flake it.
    """
    slots = tmp_path / "slots"
    env_a = _gate_env(
        tmp_path / "a",
        DEVRC_GATE_SLOTS="1",
        DEVRC_GATE_SLOT_DIR=str(slots),
    )
    # A's runner sleeps, so A holds the slot long enough for B to collide with
    # it. Overwrite the default (instant) runner _gate_env already wrote there.
    slow_a = _fake_runner(tmp_path / "a" / "fake-runner.sh", sleep=4.0)
    env_a["DEVRC_GATE_PYTEST_RUNNER"] = str(slow_a)
    env_a["DEVRC_GATE_NODE_RUNNER"] = str(slow_a)

    env_b = _gate_env(
        tmp_path / "b",
        DEVRC_GATE_SLOTS="1",
        DEVRC_GATE_SLOT_DIR=str(slots),
    )

    proc_a = subprocess.Popen(
        ["bash", str(GATE), "--tier", "pytest", "--log-dir", str(tmp_path / "la"), str(REPO)],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env_a,
    )
    time.sleep(0.5)
    b_started = time.monotonic()
    proc_b = subprocess.Popen(
        ["bash", str(GATE), "--tier", "pytest", "--log-dir", str(tmp_path / "lb"), str(REPO)],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env_b,
    )
    out_a, _ = proc_a.communicate(timeout=120)
    a_done = time.monotonic()
    out_b, _ = proc_b.communicate(timeout=120)
    b_done = time.monotonic()

    assert proc_a.returncode == 0, out_a
    assert proc_b.returncode == 0, out_b
    assert b_done >= a_done, (
        "B finished before A released the only slot — the limiter did not block.\n"
        f"--- A ---\n{out_a}\n--- B ---\n{out_b}"
    )
    assert QUEUED_MARKER in out_b, (
        "B never announced that it was queueing, so it did not take the waiting "
        f"path even though it finished later.\n{out_b}"
    )
    assert b_done - b_started >= 3.0, (
        "B returned too fast to have waited for A's ~4s hold; ordering alone "
        "could be luck."
    )


def test_two_slots_let_two_runs_overlap(tmp_path):
    """POSITIVE CONTROL for the test above: with 2 slots there is no queueing.

    Without this, a limiter wired to block ALWAYS — or one whose slot loop never
    finds a free slot — would pass the blocking test and look correct.
    """
    slots = tmp_path / "slots"
    outs = []
    procs = []
    for name in ("a", "b"):
        d = tmp_path / name
        d.mkdir()
        r = _fake_runner(d / "fake-runner.sh", sleep=3.0)
        env = _gate_env(
            d,
            DEVRC_GATE_SLOTS="2",
            DEVRC_GATE_SLOT_DIR=str(slots),
        )
        env["DEVRC_GATE_PYTEST_RUNNER"] = str(r)
        env["DEVRC_GATE_NODE_RUNNER"] = str(r)
        procs.append(
            subprocess.Popen(
                ["bash", str(GATE), "--tier", "pytest", "--log-dir", str(d / "log"), str(REPO)],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env,
            )
        )
    for p in procs:
        outs.append(p.communicate(timeout=120)[0])
        assert p.returncode == 0, outs[-1]
    assert not any(QUEUED_MARKER in o for o in outs), (
        "a run queued even though there were 2 slots for 2 runs — the limiter is "
        "narrower than it claims.\n" + "\n---\n".join(outs)
    )
    assert all("acquired slot" in o for o in outs)


def test_slots_zero_disables_the_limiter(tmp_path):
    env = _gate_env(tmp_path, DEVRC_GATE_SLOTS="0", DEVRC_GATE_SLOT_DIR=str(tmp_path / "s"))
    proc = _run_gate(tmp_path, env, "--tier", "pytest")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "slot limiter DISABLED" in proc.stdout


def test_a_full_pool_fails_open_rather_than_blocking_forever(tmp_path):
    """A limiter that can wedge the gate is worse than a slow gate.

    Hold the only slot from the test process itself, then run the gate with a
    zero wait budget: it must WARN and still produce a verdict.
    """
    import fcntl

    slots = tmp_path / "slots"
    slots.mkdir()
    holder = open(slots / "slot-1.lock", "a")
    fcntl.flock(holder.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        env = _gate_env(
            tmp_path,
            DEVRC_GATE_SLOTS="1",
            DEVRC_GATE_SLOT_DIR=str(slots),
            DEVRC_GATE_SLOT_WAIT="0",
        )
        proc = _run_gate(tmp_path, env, "--tier", "pytest")
    finally:
        holder.close()
    combined = proc.stdout + proc.stderr
    assert proc.returncode == 0, combined
    assert "still busy" in combined, combined
    assert "GATE: RESULT=PASS" in proc.stdout, (
        "the gate must still reach a verdict when it gives up on a slot — a "
        f"limiter may never change the answer.\n{combined}"
    )
    assert "slot: NONE HELD" in proc.stdout, (
        "an unslotted run must SAY it was unslotted; its timing is not "
        f"comparable to a slotted one.\n{proc.stdout}"
    )


def test_a_held_slot_is_named_in_the_gate_report(tmp_path):
    env = _gate_env(tmp_path, DEVRC_GATE_SLOTS="2", DEVRC_GATE_SLOT_DIR=str(tmp_path / "s"))
    proc = _run_gate(tmp_path, env, "--tier", "pytest")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "slot: 1 of 2" in proc.stdout, proc.stdout


def test_the_limiter_is_inert_when_nested_and_no_slot_dir_is_named(tmp_path):
    """The deadlock guard: N xdist workers spawning gate.sh must not queue.

    Every other test here names a slot dir, which opts back IN — so this is the
    only place the default nested behaviour is observable.
    """
    env = _gate_env(tmp_path, DEVRC_GATE_SLOTS="1")
    env.pop("DEVRC_GATE_SLOT_DIR", None)
    env["PYTEST_CURRENT_TEST"] = "fake::test_something (call)"
    proc = _run_gate(tmp_path, env, "--tier", "pytest")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "slot limiter INERT" in proc.stdout, proc.stdout


def test_a_rejected_slot_count_is_a_usage_error_not_a_verdict(tmp_path):
    env = _gate_env(tmp_path, DEVRC_GATE_SLOTS="two", DEVRC_GATE_SLOT_DIR=str(tmp_path / "s"))
    proc = _run_gate(tmp_path, env, "--tier", "pytest")
    assert proc.returncode == 2, (
        "a malformed DEVRC_GATE_SLOTS must exit 2 (usage), never 0 (pass) or 1 "
        f"(tests failed).\n{proc.stdout}{proc.stderr}"
    )


def test_an_orphan_spawned_by_the_tier_does_not_keep_holding_the_slot(tmp_path):
    """A flock lives on the open file DESCRIPTION, and fork/exec inherits it.

    So a runner that leaves ANY process behind — a daemonised helper, an
    orphaned server — would keep the gate's slot held after the gate exits, and
    nothing would ever say so: the pool would just be permanently one slot
    smaller and every later run would queue then fail-open, with the limiter
    silently off while still printing that it was on.

    Run 1's fake runner spawns a 30s sleeper that survives it. Run 2 must then
    take the slot IMMEDIATELY. Without the fd close in run_tier, run 2 queues.
    """
    slots = tmp_path / "slots"
    d = tmp_path / "one"
    d.mkdir()
    orphan_marker = tmp_path / "orphan.pid"
    runner = d / "spawner.sh"
    write_exec(
        runner,
        "echo '======== FAKE SUMMARY ========'\n"
        f"sleep 30 & echo $! > {orphan_marker}\n"
        "echo 'RESULT: PASS (exit=0)'\n"
        "exit 0\n",
    )

    env1 = _gate_env(d, DEVRC_GATE_SLOTS="1", DEVRC_GATE_SLOT_DIR=str(slots))
    env1["DEVRC_GATE_PYTEST_RUNNER"] = str(runner)
    p1 = subprocess.run(
        ["bash", str(GATE), "--tier", "pytest", "--log-dir", str(d / "l1"), str(REPO)],
        capture_output=True, text=True, env=env1, timeout=120,
    )
    assert p1.returncode == 0, p1.stdout + p1.stderr
    orphan_pid = None
    try:
        orphan_pid = int(orphan_marker.read_text().strip())
        # POSITIVE CONTROL: the orphan must really still be alive, or this test
        # proves nothing about inherited fds.
        os.kill(orphan_pid, 0)

        e2 = tmp_path / "two"
        e2.mkdir()
        env2 = _gate_env(e2, DEVRC_GATE_SLOTS="1", DEVRC_GATE_SLOT_DIR=str(slots),
                         DEVRC_GATE_SLOT_WAIT="0")
        p2 = subprocess.run(
            ["bash", str(GATE), "--tier", "pytest", "--log-dir", str(e2 / "l2"), str(REPO)],
            capture_output=True, text=True, env=env2, timeout=120,
        )
        combined2 = p2.stdout + p2.stderr
        assert "acquired slot 1/1 immediately" in p2.stdout, (
            "the second run could not take the slot — an orphan from the first "
            f"run is still holding the lock through an inherited fd.\n{combined2}"
        )
    finally:
        if orphan_pid:
            try:
                os.kill(orphan_pid, 9)
            except (ProcessLookupError, PermissionError):
                pass


# --- the queue does not eat the test budget -----------------------------------


def test_time_spent_waiting_is_not_charged_to_the_timeout(tmp_path):
    """The --timeout cap must start when the TIER starts, not at process start.

    A run that queued 20 min behind another must still get its full budget;
    otherwise the limiter manufactures the very timeout it exists to prevent.
    Here: a 3s hold on the only slot, a 2s tier, and a 4s cap. If queue time
    were charged, the tier would be killed and the gate would go red.
    """
    import fcntl
    import threading

    slots = tmp_path / "slots"
    slots.mkdir()
    holder = open(slots / "slot-1.lock", "a")
    fcntl.flock(holder.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    threading.Timer(3.0, holder.close).start()

    r = _fake_runner(tmp_path / "slow.sh", sleep=2.0)
    env = _gate_env(
        tmp_path,
        DEVRC_GATE_SLOTS="1",
        DEVRC_GATE_SLOT_DIR=str(slots),
        DEVRC_GATE_SLOT_WAIT="60",
    )
    env["DEVRC_GATE_PYTEST_RUNNER"] = str(r)
    proc = _run_gate(tmp_path, env, "--tier", "pytest", "--timeout", "4", timeout=120)
    combined = proc.stdout + proc.stderr
    assert QUEUED_MARKER in combined, f"the run never queued, so this proves nothing\n{combined}"
    assert proc.returncode == 0, (
        "the tier was killed even though it ran well inside its own cap — queue "
        f"time is being charged to the timeout.\n{combined}"
    )


# --- the re-exec --------------------------------------------------------------


def _fake_nix(path: Path, record: Path) -> Path:
    return write_exec(
        path,
        textwrap.dedent(
            f"""\
            printf '%s\\n' "$@" > {record}
            exit 0
            """
        ),
    )


def test_it_re_enters_nix_develop_when_not_in_a_gate_environment(tmp_path):
    """The 100-wasted-invocations fix, observed through a recording fake `nix`."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    record = tmp_path / "argv.txt"
    _fake_nix(bindir / "nix", record)

    env = _gate_env(tmp_path, DEVRC_GATE_SLOT_DIR=str(tmp_path / "s"))
    env.pop("DEVRC_GATE_NO_REEXEC")
    # The seams would suppress the re-exec; this test is ABOUT the re-exec.
    env.pop("DEVRC_GATE_PYTEST_RUNNER")
    env.pop("DEVRC_GATE_NODE_RUNNER")
    env.pop("PYTEST_CURRENT_TEST", None)
    env.pop("DEVRC_GATE_ENV", None)
    env["PATH"] = f"{bindir}:{env['PATH']}"

    proc = subprocess.run(
        ["bash", str(GATE), "--tier", "pytest", "--log-dir", str(tmp_path / "L"), str(REPO)],
        capture_output=True, text=True, env=env, timeout=120,
    )
    assert record.exists(), (
        "`nix` was never invoked — the gate ran against the ambient PATH "
        f"instead of re-entering the dev shell.\n{proc.stdout}{proc.stderr}"
    )
    argv = record.read_text().split("\n")
    assert argv[0] == "develop", argv
    assert str(REPO) in argv, argv
    # 🔴 The resolved LOG_DIR must be handed through. Replaying the raw argv
    # would let the inner shell re-run mktemp under nix's per-shell TMPDIR and
    # write its logs somewhere the outer run never announced.
    assert "--log-dir" in argv and str(tmp_path / "L") in argv, (
        f"the re-exec did not pass the resolved log dir through: {argv}"
    )


def test_it_does_not_re_exec_when_already_in_a_gate_environment(tmp_path):
    env = _gate_env(tmp_path, DEVRC_GATE_SLOT_DIR=str(tmp_path / "s"))
    env.pop("DEVRC_GATE_NO_REEXEC")
    env["DEVRC_GATE_ENV"] = "1"
    proc = _run_gate(tmp_path, env, "--tier", "pytest")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "re-entering" not in proc.stdout, proc.stdout


def test_the_loop_guard_stops_a_second_re_exec(tmp_path):
    """If the flake's shellHook ever stops setting DEVRC_GATE_ENV, this is what
    keeps the re-exec from being a fork bomb rather than a wrong answer."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    record = tmp_path / "argv.txt"
    _fake_nix(bindir / "nix", record)
    env = _gate_env(tmp_path, DEVRC_GATE_SLOT_DIR=str(tmp_path / "s"))
    env.pop("DEVRC_GATE_NO_REEXEC")
    env.pop("DEVRC_GATE_ENV", None)
    env["DEVRC_GATE_REEXEC"] = "1"
    env["PATH"] = f"{bindir}:{env['PATH']}"
    proc = _run_gate(tmp_path, env, "--tier", "pytest")
    assert not record.exists(), (
        "re-exec fired despite the loop guard being set — a second pass means "
        "there is no bound on the recursion at all."
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_no_reexec_opt_out_is_honoured(tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    record = tmp_path / "argv.txt"
    _fake_nix(bindir / "nix", record)
    env = _gate_env(tmp_path, DEVRC_GATE_SLOT_DIR=str(tmp_path / "s"))
    env["DEVRC_GATE_NO_REEXEC"] = "1"
    env.pop("DEVRC_GATE_ENV", None)
    env["PATH"] = f"{bindir}:{env['PATH']}"
    proc = _run_gate(tmp_path, env, "--tier", "pytest")
    assert not record.exists()
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_the_help_text_documents_every_env_var_the_script_reads():
    """--help used to be a hardcoded `sed 2,70p` and silently truncated as the
    header grew. A flag documented nowhere the operator looks is a flag that
    does not exist."""
    proc = subprocess.run(
        ["bash", str(GATE), "--help"], capture_output=True, text=True, timeout=30
    )
    assert proc.returncode == 0
    for var in (
        "DEVRC_GATE_SLOTS",
        "DEVRC_GATE_SLOT_WAIT",
        "DEVRC_GATE_NO_REEXEC",
        "DEVRC_GATE_SLOT_DIR",
    ):
        assert var in proc.stdout, f"{var} is read by gate.sh but absent from --help"
