"""gate.sh's concurrency slot limiter and its dev-shell re-exec.

WHY THIS FILE EXISTS — both features were built from measurements, and both are
the kind whose failure mode is a HANG or a silently-skipped tier rather than a
wrong answer, so they need controls that watch them actually bite.

SLOT LIMITER. Nothing serialised gate runs on a box where dozens of agent
sessions each start their own. ⚠ THE HEADLINE NUMBER THIS FILE USED TO CARRY IS
WITHDRAWN: "0 others -> 14.5 min, 6+ others -> 48.9 min, 3.4x and superlinear"
was measured across 237 real runs, but bucketing runs by how many others
overlapped them is length-biased — a long run overlaps more runs BY
CONSTRUCTION — so the trend appears with zero interaction between runs. A null
Monte Carlo (237 runs, Poisson arrivals, lognormal durations fitted to the same
marginal, no interaction at all) reproduces the shape, the 14.5 min baseline and
~2.06x of the 3.4x. Contention is real and its mechanism is uncontroversial;
that dataset cannot size it. What the same window does support, being a count
rather than a bucketed median: 35 of the 117 red runs died on SIGTERM at the
3600s cap, producing no verdict for an hour of wall clock.

RE-EXEC. `run-tests.sh` refuses to run without its REQUIRED_TOOLS and prints the
`nix develop` line that fixes it. Measured across every gate log dir on the box,
101 of 382 runs (26%) died on exactly that with pytest never starting. ⚠ The
earlier "100 of 100" was a population defined by the failure's own cause: the
default LOG_DIR is a `mktemp -d`, so runs inside `nix develop` land under nix's
per-shell TMPDIR and only runs OUTSIDE it land in bare /tmp — 101/101 there, and
0 of 281 inside.

Every test here drives the REAL gate.sh with the documented runner seams and its
own private slot dir, so nothing it does can contend with a live gate run on the
box, and nothing on the box can make it flake. 🔴 `_gate_env` SCRUBS the
operator-facing gate variables out of the inherited environment: an exported
DEVRC_GATE_SLOT_DIR used to be inherited straight into these runs, which joined
the operator's pool, queued behind it and turned this file's own positive
control red on a TimeoutExpired.
"""

from __future__ import annotations

import os
import re
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

# 🔴 EVERY GATE VARIABLE THE AMBIENT ENVIRONMENT MIGHT CARRY. A test that
# inherits one of these is steered by whoever exported it: measured by an
# auditor, `DEVRC_GATE_SLOT_DIR` exported with the pool held made
# `test_a_green_runner_gives_a_green_gate_and_a_nonzero_count` fail on a 120s
# TimeoutExpired after the gate printed `all 2 slot(s) busy — queueing`. This
# list is derived from gate.sh in
# `test_the_help_text_documents_every_env_var_the_script_reads`, which fails if
# the script grows a variable nothing here has considered.
_AMBIENT_GATE_VARS = (
    "DEVRC_GATE_TIMEOUT",
    "DEVRC_GATE_SLOTS",
    "DEVRC_GATE_SLOT_WAIT",
    "DEVRC_GATE_SLOT_DIR",
    "DEVRC_GATE_SLOT_POOL",
    "DEVRC_GATE_NO_REEXEC",
    "DEVRC_GATE_ENV",
    "DEVRC_GATE_REEXEC",
    "DEVRC_GATE_PYTEST_RUNNER",
    "DEVRC_GATE_NODE_RUNNER",
)


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
    for var in _AMBIENT_GATE_VARS:
        env.pop(var, None)
    runner = _fake_runner(tmp_path / "fake-runner.sh")
    env.update(
        {
            "DEVRC_GATE_PYTEST_RUNNER": str(runner),
            "DEVRC_GATE_NODE_RUNNER": str(runner),
            # Never let a test re-enter `nix develop` by accident: it would cost
            # minutes and would be testing nix, not this script. The tests that
            # are ABOUT the re-exec pop this deliberately.
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
    assert "slot: NONE HELD — DISABLED" in proc.stdout, (
        "the report line must say WHY no slot is held; `NONE HELD` alone reads "
        f"identically to a run that queued and gave up.\n{proc.stdout}"
    )


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
    assert "this run CONTENDED" in proc.stdout, (
        "giving up on a slot is the ONE unslotted state that is a claim about "
        f"contention, and the report must distinguish it from the others.\n{proc.stdout}"
    )


def test_an_unopenable_pool_is_not_reported_as_contention(tmp_path):
    """A lock file that cannot be OPENED is a pool problem, not a busy pool.

    The old loop sent an open failure down the same `|| continue` path as a held
    lock, so the gate announced `slot(s) busy — queueing` for a pool nobody was
    holding, waited out DEVRC_GATE_SLOT_WAIT, and then blamed contention. The
    diagnosis a reader takes from that log is wrong in the expensive direction.

    Here the slot dir exists but is not writable, so `slot-1.lock` cannot be
    created and the gate must say so instead of claiming the pool is busy.
    """
    if os.geteuid() == 0:
        import pytest

        pytest.skip("root ignores the directory permission this test depends on")
    slots = tmp_path / "slots"
    slots.mkdir()
    os.chmod(slots, 0o500)
    try:
        env = _gate_env(
            tmp_path,
            DEVRC_GATE_SLOTS="1",
            DEVRC_GATE_SLOT_DIR=str(slots),
            # Large on purpose: a run that misdiagnosed this as contention would
            # sit here for the whole budget and blow the test's own timeout.
            DEVRC_GATE_SLOT_WAIT="600",
        )
        proc = _run_gate(tmp_path, env, "--tier", "pytest", timeout=120)
    finally:
        os.chmod(slots, 0o700)
    combined = proc.stdout + proc.stderr
    assert proc.returncode == 0, combined
    assert QUEUED_MARKER not in combined, (
        "the gate announced contention for a pool it could not even open.\n" + combined
    )
    assert "could not open any lock file" in combined, combined
    assert "not a contention condition" in proc.stdout, proc.stdout
    assert "GATE: RESULT=PASS" in proc.stdout, combined


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
    assert "nested inside pytest" in proc.stdout, proc.stdout


def test_a_descendant_does_not_queue_behind_its_own_ancestors_slot(tmp_path):
    """ONE SLOT PER GATE TREE. A nested run must not wait for a lock its own
    ancestor holds — that is a deadlock built out of a performance feature.

    Reproduces the reachable shape directly: hold the pool's only slot from this
    process, then run the gate with DEVRC_GATE_SLOT_POOL already naming that
    pool, exactly as an outer gate run would have exported it. Without the
    ancestry check the run queues for the whole DEVRC_GATE_SLOT_WAIT and this
    test times out; with it, the run is inert and finishes at once.
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
            DEVRC_GATE_SLOT_POOL=str(slots),
            DEVRC_GATE_SLOT_WAIT="600",
        )
        proc = _run_gate(tmp_path, env, "--tier", "pytest", timeout=120)
    finally:
        holder.close()
    combined = proc.stdout + proc.stderr
    assert proc.returncode == 0, combined
    assert QUEUED_MARKER not in combined, (
        "the run queued for a pool its own ancestor already holds a place in.\n" + combined
    )
    assert "an ancestor gate run already" in proc.stdout, proc.stdout
    assert "GATE: RESULT=PASS" in proc.stdout, combined


def test_a_gate_run_names_the_pool_it_holds_for_its_descendants(tmp_path):
    """The other half: the marker has to be EXPORTED, or the check above is dead.

    A runner that prints its own environment is the only place this is
    observable — the variable exists to be inherited by whatever the tier
    spawns, which is exactly the population that used to deadlock.
    """
    slots = tmp_path / "slots"
    d = tmp_path / "one"
    d.mkdir()
    probe = d / "probe.sh"
    write_exec(
        probe,
        "echo '======== FAKE SUMMARY ========'\n"
        'echo "POOL_SEEN=[${DEVRC_GATE_SLOT_POOL:-<unset>}]"\n'
        "echo 'RESULT: PASS (exit=0)'\n"
        "exit 0\n",
    )
    env = _gate_env(d, DEVRC_GATE_SLOTS="2", DEVRC_GATE_SLOT_DIR=str(slots))
    env["DEVRC_GATE_PYTEST_RUNNER"] = str(probe)
    proc = subprocess.run(
        ["bash", str(GATE), "--tier", "pytest", "--log-dir", str(d / "L"), str(REPO)],
        capture_output=True, text=True, env=env, timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    log = (d / "L" / "pytest.log").read_text()
    assert f"POOL_SEEN=[{slots}]" in log, (
        "the tier's children did not inherit DEVRC_GATE_SLOT_POOL, so nothing "
        f"they spawn can tell that this tree already has a slot.\n{log}"
    )


def test_a_rejected_slot_count_is_a_usage_error_not_a_verdict(tmp_path):
    """Config that does not parse is a REFUSAL, and deliberately so.

    The limiter fails open on every RUNTIME condition, but a typo'd slot count
    must not be silently read as "unlimited". Exit 2 is the script's usage code
    and is never a verdict about the tests — which is why the FAIL-OPEN comment
    in gate.sh now names this exception instead of claiming nothing can change
    the outcome.
    """
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

    A run that queued behind another must still get its full budget; otherwise
    the limiter manufactures the very timeout it exists to prevent. Here: a 3s
    hold on the only slot, a 2s tier, and a 4s cap. If queue time were charged,
    the tier would be killed and the gate would go red.
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
#
# 🔴 EVERY TEST BELOW POPS PYTEST_CURRENT_TEST. That is not boilerplate: it is
# the ONLY remaining short-circuit in front of the three re-exec guards, and
# leaving it set is how all three of them used to be unreachable. See the
# GATE_NESTED comment in gate.sh for the mutation table.


def _fake_nix(path: Path, record: Path) -> Path:
    """A `nix` that records the argv it was handed and does NOT exec it."""
    return write_exec(
        path,
        textwrap.dedent(
            f"""\
            printf '%s\\n' "$@" > {record}
            exit 0
            """
        ),
    )


def _reexec_env(tmp_path: Path, bindir: Path, **over: str) -> dict[str, str]:
    """An env in which the re-exec would fire unless a guard stops it.

    Keeps the runner seams — they are cheap and they no longer suppress the
    re-exec — so a guard that DOES fire leaves the gate running a fake runner in
    milliseconds rather than the real 7k-test suite.
    """
    env = _gate_env(tmp_path, **over)
    env.pop("DEVRC_GATE_NO_REEXEC", None)
    env.pop("DEVRC_GATE_ENV", None)
    env.pop("DEVRC_GATE_REEXEC", None)
    env.pop("PYTEST_CURRENT_TEST", None)
    env["DEVRC_GATE_SLOT_DIR"] = str(tmp_path / "s")
    env["PATH"] = f"{bindir}:{env['PATH']}"
    env.update(over)
    return env


def test_it_re_enters_nix_develop_when_not_in_a_gate_environment(tmp_path):
    """The wasted-invocations fix, observed through a recording fake `nix`."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    record = tmp_path / "argv.txt"
    _fake_nix(bindir / "nix", record)

    env = _reexec_env(tmp_path, bindir)
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
    """DEVRC_GATE_ENV=1 is the normal exit condition: the flake's shellHook set it.

    🔴 Reaches its guard: no runner-seam short-circuit stands in front of it any
    more, and PYTEST_CURRENT_TEST is popped. Deleting the DEVRC_GATE_ENV guard
    from gate.sh makes this test fail — before, it did not.
    """
    bindir = tmp_path / "bin"
    bindir.mkdir()
    record = tmp_path / "argv.txt"
    _fake_nix(bindir / "nix", record)
    env = _reexec_env(tmp_path, bindir, DEVRC_GATE_ENV="1")
    proc = _run_gate(tmp_path, env, "--tier", "pytest")
    assert not record.exists(), (
        "the gate re-entered `nix develop` from inside a gate environment — the "
        f"DEVRC_GATE_ENV guard did not fire.\n{proc.stdout}{proc.stderr}"
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "re-entering" not in proc.stdout, proc.stdout


def test_the_loop_guard_stops_a_second_re_exec(tmp_path):
    """If the flake's shellHook ever stops setting DEVRC_GATE_ENV, this is what
    keeps the re-exec from being a fork bomb rather than a wrong answer.

    DEVRC_GATE_ENV is deliberately absent here, so the ONLY thing standing
    between this run and a second `nix develop` is DEVRC_GATE_REEXEC — the
    variable gate.sh sets on itself on the way in.
    """
    bindir = tmp_path / "bin"
    bindir.mkdir()
    record = tmp_path / "argv.txt"
    _fake_nix(bindir / "nix", record)
    env = _reexec_env(tmp_path, bindir, DEVRC_GATE_REEXEC="1")
    proc = _run_gate(tmp_path, env, "--tier", "pytest")
    assert not record.exists(), (
        "re-exec fired despite the loop guard being set — a second pass means "
        f"there is no bound on the recursion at all.\n{proc.stdout}{proc.stderr}"
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_no_reexec_opt_out_is_honoured(tmp_path):
    """The documented escape hatch: run against the ambient PATH.

    DEVRC_GATE_ENV and DEVRC_GATE_REEXEC are both absent, so this variable is
    the only guard in play.
    """
    bindir = tmp_path / "bin"
    bindir.mkdir()
    record = tmp_path / "argv.txt"
    _fake_nix(bindir / "nix", record)
    env = _reexec_env(tmp_path, bindir, DEVRC_GATE_NO_REEXEC="1")
    proc = _run_gate(tmp_path, env, "--tier", "pytest")
    assert not record.exists(), (
        "DEVRC_GATE_NO_REEXEC=1 did not stop the re-exec.\n"
        f"{proc.stdout}{proc.stderr}"
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_a_relative_path_invocation_survives_the_re_exec(tmp_path):
    """`cd <repo>/scripts && bash gate.sh` must still produce a verdict.

    `${BASH_SOURCE[0]}` is whatever the caller typed, `cd "$ROOT"` happens
    before the re-exec, and the re-exec used to hand that relative path to the
    inner shell — which resolved it against the NEW cwd and died
    `bash: gate.sh: No such file or directory`, rc=127, with no GATE block, no
    RESULT line and no instruction. Before the re-exec existed the same
    invocation printed a correct, actionable FATAL, so this was a regression
    into a code outside the script's whole documented exit set.

    The fake `nix` here EXECS what it was handed rather than just recording it,
    so the failure reproduces end to end instead of being inferred from an argv.
    """
    bindir = tmp_path / "bin"
    bindir.mkdir()
    record = tmp_path / "argv.txt"
    write_exec(
        bindir / "nix",
        textwrap.dedent(
            f"""\
            printf '%s\\n' "$@" > {record}
            shift 3          # drop: develop <root> --command
            exec "$@"
            """
        ),
    )
    env = _reexec_env(tmp_path, bindir)
    proc = subprocess.run(
        ["bash", "gate.sh", "--tier", "pytest", "--log-dir", str(tmp_path / "L"), str(REPO)],
        cwd=str(REPO / "scripts"),
        capture_output=True, text=True, env=env, timeout=120,
    )
    combined = proc.stdout + proc.stderr
    assert proc.returncode != 127, (
        "the re-exec handed a relative path to the inner shell; rc=127 is not "
        f"in this script's documented exit set at all.\n{combined}"
    )
    assert record.exists(), combined
    argv = record.read_text().split("\n")
    handed = argv[4] if len(argv) > 4 else ""
    assert os.path.isabs(handed) and handed.endswith("gate.sh"), (
        f"the re-exec handed the inner shell {handed!r}, not an absolute path: {argv}"
    )
    assert "GATE: RESULT=PASS" in proc.stdout, (
        f"the re-exec'd run produced no verdict.\n{combined}"
    )
    assert proc.returncode == 0, combined


def _env_vars_gate_sh_reads() -> set[str]:
    """Every environment variable gate.sh reads, DERIVED FROM THE SOURCE.

    🔴 A LITERAL LIST HERE IS THE BUG THIS FUNCTION REPLACES. The predecessor of
    the test below was named "...documents every env var the script reads" and
    checked four hardcoded strings; four MORE that the script reads
    (DEVRC_GATE_TIMEOUT, DEVRC_GATE_ENV, DEVRC_GATE_REEXEC,
    PYTEST_CURRENT_TEST) were absent from --help and it was green. A guard whose
    name says "every" must check every, or it reads as coverage while providing
    none.

    The rule is mechanical: an ALL-CAPS name read through a
    tolerates-absence expansion (`${NAME:-…}`, `${NAME:+…}`, `${NAME-…}`,
    `${NAME+…}`) BEFORE the script ever assigns it. That is what reading an
    environment variable looks like and what reading one of the script's own
    locals does not — `GATE_SLOT_STATE` is assigned at its declaration and read
    afterwards, so it drops out; `DEVRC_GATE_REEXEC` is read first and exported
    later, so it stays in, which is correct because both halves are real.
    Comment lines are excluded, so documenting a variable cannot satisfy the
    test that checks it is documented.
    """
    lines = GATE.read_text().splitlines()
    body_start = next(i for i in range(1, len(lines)) if not lines[i].startswith("#"))
    body = "\n".join(l for l in lines[body_start:] if not l.lstrip().startswith("#"))
    first_read: dict[str, int] = {}
    for m in re.finditer(r"\$\{([A-Z][A-Z0-9_]*)(?::-|:\+|:=|:\?|-|\+)", body):
        first_read.setdefault(m.group(1), m.start())
    first_assign: dict[str, int] = {}
    for m in re.finditer(r"(?m)^[^\n]*?\b([A-Z][A-Z0-9_]*)=", body):
        first_assign.setdefault(m.group(1), m.start())
    return {n for n, p in first_read.items() if p < first_assign.get(n, 1 << 30)}


def test_the_env_var_derivation_can_actually_see_a_variable():
    """POSITIVE CONTROL for the derivation. A regex that matched nothing would
    make the test below pass over an empty set — the reassuring zero."""
    found = _env_vars_gate_sh_reads()
    assert len(found) >= 8, f"the derivation found only {sorted(found)}"
    for expected in ("DEVRC_GATE_SLOTS", "DEVRC_GATE_TIMEOUT", "PYTEST_CURRENT_TEST"):
        assert expected in found, f"{expected} not derived; found {sorted(found)}"
    # NEGATIVE CONTROL: a name gate.sh assigns before reading is not an env var.
    assert "GATE_SLOT_STATE" not in found, sorted(found)
    assert "TIER" not in found, sorted(found)


def test_the_help_text_documents_every_env_var_the_script_reads():
    """--help used to be a hardcoded `sed 2,70p` and silently truncated as the
    header grew. A flag documented nowhere the operator looks is a flag that
    does not exist.

    DEVRC_GATE_ENV is the one an operator most needs and the one that was
    missing: the banner says "not in a gate environment (DEVRC_GATE_ENV unset)"
    and --help could not say what that was.
    """
    proc = subprocess.run(
        ["bash", str(GATE), "--help"], capture_output=True, text=True, timeout=30
    )
    assert proc.returncode == 0
    undocumented = sorted(v for v in _env_vars_gate_sh_reads() if v not in proc.stdout)
    assert not undocumented, (
        f"gate.sh reads {undocumented} but --help never mentions them. Add each "
        "to the `Env:` block in the header — that block IS the help text."
    )


def test_the_ambient_scrub_list_covers_every_gate_variable():
    """The seam between two files, asserted as a LEDGER rather than a spot check.

    `_gate_env` scrubs a hardcoded tuple; gate.sh owns the real set. If the
    script grows a variable nobody adds to `_AMBIENT_GATE_VARS`, the next
    exported value silently steers these tests again — which is exactly how an
    exported DEVRC_GATE_SLOT_DIR turned this file's positive control red. Fails
    when the set GROWS or SHRINKS, not merely when it disagrees in one
    direction.

    PYTEST_CURRENT_TEST is deliberately NOT scrubbed by `_gate_env`: it is
    pytest's own, it is genuinely true while these tests run, and the re-exec
    tests pop it themselves to reach the guards behind it.
    """
    derived = _env_vars_gate_sh_reads() - {"PYTEST_CURRENT_TEST"}
    assert derived == set(_AMBIENT_GATE_VARS), (
        "the scrub list and gate.sh's env vars disagree.\n"
        f"  in gate.sh but not scrubbed: {sorted(derived - set(_AMBIENT_GATE_VARS))}\n"
        f"  scrubbed but not in gate.sh: {sorted(set(_AMBIENT_GATE_VARS) - derived)}"
    )
