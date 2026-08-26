"""Guards for `scripts/dl-router/tests/load_test_store.sh` — the FLAKE REPRODUCER.

WHY THIS FILE EXISTS
--------------------
The harness is an INSTRUMENT: you point it at a pytest target, it runs that
target in a loop under CPU pressure, and its exit status is the number of runs
that went red. An instrument that cannot reach its subject and reports a
failure count anyway is worse than no instrument, because the count reads as a
measurement of the code under test.

That is exactly what it did. Until 2026-08-25 the nodeid was split across a
line continuation::

    python -m pytest "$FLAKE_DIR/scripts/dl-router/tests/test_store.py" \\
      ::test_concurrent_writers_do_not_lose_rows \\
      -q --tb=short

so `::test_concurrent_writers_do_not_lose_rows` reached pytest as its OWN argv
element rather than as a suffix on the file path. pytest answers a bare `::name`
with `ERROR: directory argument cannot contain :: selection parts` and
`no tests ran in 0.01s`, exit 4. Measured on this tree at
1f3d854c: the original script reported `failures: 3 / 3` and exited 3 with the
subject never once executed; the fixed script reports `failures: 0 / 3` on the
same target with `1 passed in 9.83s` per run.

WHAT IS REGRESSION COVERAGE HERE AND WHAT IS NOT (RULES.md asks for the label)
------------------------------------------------------------------------------
  * `test_the_nodeid_reaches_pytest_as_one_argv_element` and
    `test_no_argv_element_is_a_bare_selection_part` are REGRESSION coverage.
    Both are RED against the original file (measured: the first fails because
    the nodeid is two elements, the second because `::test_concurrent_...`
    appears alone) and GREEN against the fixed one.
  * `test_a_target_that_collects_nothing_is_could_not_run` and
    `test_a_run_that_collects_nothing_mid_loop_is_could_not_run` are REGRESSION
    coverage for the CONSEQUENCE — the original scored an unreachable subject as
    `failures == RUNS`. They are RED against the original (it prints a failure
    count, exit 3) and GREEN here (exit 91, `COULD NOT RUN`).
  * `test_the_failure_count_is_the_exit_status` and
    `test_burners_are_reaped_when_the_run_ends` are INVARIANT GUARDS. The bug
    never violated either; they exist so a fix cannot quietly drop the two
    properties the original got right.
  * `test_the_harness_cannot_be_collected_by_the_gate` and
    `test_the_harness_is_tracked_by_git` are INVARIANT GUARDS on the deploy and
    the gate: `scripts/dl-router/` is a `home.file` source, so an untracked file
    there is silently absent from the built artifact, and a harness that spawns
    CPU burners must never be picked up by `scripts/run-tests.sh`.

HOW THE BEHAVIOURAL TESTS WORK
------------------------------
The harness shells out to `nix develop <dir> --command python -m pytest …`.
These tests put a STUB `nix` first on PATH that (a) appends its whole argv to a
log, one element per line, and (b) prints a canned pytest summary line and exits
with a canned status. So the argv the harness BUILDS is observable exactly, in
both tiers, with no nix, no network and no CPU burn (`BURNERS=0`).
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from testlib.hermetic_git import hermetic_git_env  # noqa: E402
from testlib.mockbin import write_exec  # noqa: E402

HARNESS = SCRIPTS / "dl-router" / "tests" / "load_test_store.sh"

DEFAULT_TARGET_SUFFIX = (
    "scripts/dl-router/tests/test_store.py"
    "::test_concurrent_writers_do_not_lose_rows"
)

# The argv log uses a record marker so one invocation's elements cannot be
# confused with the next one's — the harness calls `nix` at least twice (the
# preflight and each run).
RECORD = "=== INVOCATION ==="

# 🔴 The stub is POSIX sh via `write_exec`, never `#!/usr/bin/env` — the nix
# build sandbox has no /usr/bin/env, and a stub that cannot exec fails in a way
# that reads as "the harness is broken". See scripts/testlib/mockbin.py.
NIX_STUB_BODY = """
{
  echo "%(record)s"
  for a in "$@"; do printf '%%s\\n' "$a"; done
} >> "$ARGV_LOG"

for a in "$@"; do
  if [ "$a" = "--collect-only" ]; then
    printf '%%s\\n' "$STUB_COLLECT_OUT"
    exit "$STUB_COLLECT_RC"
  fi
done
printf '%%s\\n' "$STUB_RUN_OUT"
exit "$STUB_RUN_RC"
""" % {"record": RECORD}


def _install_nix_stub(tmp_path: Path) -> tuple[Path, Path]:
    stub_dir = tmp_path / "stubbin"
    stub_dir.mkdir()
    argv_log = tmp_path / "argv.log"
    argv_log.write_text("", encoding="utf-8")
    write_exec(stub_dir / "nix", NIX_STUB_BODY)
    return stub_dir, argv_log


def run_harness(
    tmp_path: Path,
    *,
    script: Path | None = None,
    runs: int = 2,
    targets: tuple[str, ...] = (),
    collect_out: str = "1 test collected in 0.05s",
    collect_rc: str = "0",
    run_out: str = "1 passed in 0.10s",
    run_rc: str = "0",
    extra_env: dict[str, str] | None = None,
) -> tuple[subprocess.CompletedProcess[str], Path]:
    """Run the harness with a stubbed `nix`, returning (proc, argv_log)."""
    stub_dir, argv_log = _install_nix_stub(tmp_path)
    env = dict(os.environ)
    env["PATH"] = f"{stub_dir}{os.pathsep}{env.get('PATH', '')}"
    env["ARGV_LOG"] = str(argv_log)
    env["STUB_COLLECT_OUT"] = collect_out
    env["STUB_COLLECT_RC"] = collect_rc
    env["STUB_RUN_OUT"] = run_out
    env["STUB_RUN_RC"] = run_rc
    # 🔴 BURNERS=0: a test must never spawn (nproc/2) busy loops on the gate host.
    env["BURNERS"] = "0"
    env["LOG_DIR"] = str(tmp_path / "logs")
    env["PER_RUN_TIMEOUT"] = "30"
    if extra_env:
        env.update(extra_env)
    argv = ["bash", str(script or HARNESS), str(runs), str(REPO_ROOT), *targets]
    proc = subprocess.run(
        argv, capture_output=True, text=True, env=env, timeout=120,
        cwd=str(tmp_path),
    )
    return proc, argv_log


def state_and_pgrp(stat_text: str) -> tuple[str, int] | None:
    """Parse `state` and `pgrp` out of one /proc/<pid>/stat line.

    Split AFTER the last `)`: the `comm` field is the process name in
    parentheses and may itself contain spaces and parentheses, so a plain
    `.split()` on the whole line mis-indexes every field after it.
    """
    close = stat_text.rfind(")")
    if close == -1:
        return None
    fields = stat_text[close + 2:].split()
    if len(fields) < 3 or not fields[2].lstrip("-").isdigit():
        return None
    return fields[0], int(fields[2])


def live_pids_in_group(pgid: int) -> list[int]:
    """PIDs in `pgid` that are NOT zombies.

    🔴 `os.kill(pid, 0)` / `os.killpg(pgid, 0)` SUCCEED on a zombie. That exact
    mistake made `devrc-ci` red on its first 5 of 5 runs, in a reap check green
    on every dev host — a CI step container's PID 1 does not reap, so the dead
    child lingered as `Z` and the "is it gone" probe said no. Read the STATE.
    """
    live: list[int] = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            stat = (entry / "stat").read_text(encoding="utf-8", errors="replace")
        except (FileNotFoundError, ProcessLookupError, PermissionError, OSError):
            continue  # the process exited between listdir and read
        parsed = state_and_pgrp(stat)
        if parsed is None:
            continue
        state, pgrp = parsed
        if state != "Z" and pgrp == pgid:
            live.append(int(entry.name))
    return live


def invocations(argv_log: Path) -> list[list[str]]:
    """Split the stub's argv log into one list per `nix` invocation."""
    records: list[list[str]] = []
    for line in argv_log.read_text(encoding="utf-8").splitlines():
        if line == RECORD:
            records.append([])
        elif records:
            records[-1].append(line)
    return records


# --------------------------------------------------------------------------
# REGRESSION: the nodeid must reach pytest as ONE argv element
# --------------------------------------------------------------------------

def test_the_nodeid_reaches_pytest_as_one_argv_element(tmp_path: Path) -> None:
    """RED against the original file, GREEN here.

    The original built the nodeid across a line continuation, so pytest saw
    `<path>/test_store.py` and `::test_concurrent_writers_do_not_lose_rows` as
    two separate arguments. This asserts the STATE — one element that ends with
    the full nodeid — not the absence of a spelling.
    """
    proc, argv_log = run_harness(tmp_path, runs=1)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    records = invocations(argv_log)
    assert records, "the stub `nix` was never invoked — the harness did not run"
    for argv in records:
        whole = [a for a in argv if a.endswith(DEFAULT_TARGET_SUFFIX)]
        assert len(whole) == 1, (
            "the default target must reach pytest as exactly ONE argv element "
            f"ending in {DEFAULT_TARGET_SUFFIX!r}; got argv={argv!r}")


def test_no_argv_element_is_a_bare_selection_part(tmp_path: Path) -> None:
    """RED against the original file, GREEN here.

    pytest rejects a bare `::name` outright (`directory argument cannot contain
    :: selection parts`). No argv element the harness builds may ever start with
    `::`, for any target.
    """
    proc, argv_log = run_harness(
        tmp_path, runs=1,
        targets=("scripts/tests/test_git_repo_isolation.py::test_live_cotenants"
                 "_sees_another_process_in_the_repo",))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    for argv in invocations(argv_log):
        offenders = [a for a in argv if a.startswith("::")]
        assert not offenders, (
            f"argv element(s) {offenders!r} are bare pytest selection parts — "
            "the nodeid was split across arguments again")


def test_a_relative_target_is_resolved_against_the_flake_dir(
        tmp_path: Path) -> None:
    proc, argv_log = run_harness(
        tmp_path, runs=1, targets=("scripts/tests/test_x.py::test_y",))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    wanted = f"{REPO_ROOT}/scripts/tests/test_x.py::test_y"
    for argv in invocations(argv_log):
        assert wanted in argv, f"expected {wanted!r} in argv={argv!r}"


def test_an_absolute_target_is_passed_through_unchanged(
        tmp_path: Path) -> None:
    absolute = "/somewhere/else/test_thing.py::test_case"
    proc, argv_log = run_harness(tmp_path, runs=1, targets=(absolute,))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    for argv in invocations(argv_log):
        assert absolute in argv, f"expected {absolute!r} in argv={argv!r}"


def test_several_targets_each_stay_one_argv_element(tmp_path: Path) -> None:
    proc, argv_log = run_harness(
        tmp_path, runs=1,
        targets=("scripts/tests/test_a.py::test_one",
                 "scripts/tests/test_b.py::TestC::test_two"))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    for argv in invocations(argv_log):
        assert f"{REPO_ROOT}/scripts/tests/test_a.py::test_one" in argv
        assert f"{REPO_ROOT}/scripts/tests/test_b.py::TestC::test_two" in argv


# --------------------------------------------------------------------------
# REGRESSION: an unreachable subject is COULD NOT RUN, never a failure count
# --------------------------------------------------------------------------

def test_a_target_that_collects_nothing_is_could_not_run(
        tmp_path: Path) -> None:
    """The preflight guard. RED against the original (it had none).

    Asserted on THIS guard's own message and status, not merely on "non-zero":
    a neighbouring failure would also be non-zero, and the whole point is that
    91 means "the harness could not measure", not "the code is broken".
    """
    proc, _ = run_harness(
        tmp_path, runs=3,
        collect_out="no tests collected in 0.00s", collect_rc="4")
    assert proc.returncode == 91, (
        f"expected 91 (COULD NOT RUN), got {proc.returncode}\n"
        f"{proc.stdout}{proc.stderr}")
    assert "COULD NOT RUN: the targets collect no tests" in proc.stdout
    assert "failures:" not in proc.stdout, (
        "a harness that cannot reach its subject must not print a failure "
        "COUNT — that is the exact lie this file exists to prevent")


def test_the_preflight_runs_before_any_burner_is_spawned(
        tmp_path: Path) -> None:
    """Reachability: the guard must fire on the cheap path, not after the burn."""
    proc, _ = run_harness(
        tmp_path, runs=3,
        collect_out="no tests collected in 0.00s", collect_rc="4")
    assert proc.returncode == 91
    assert "spawned" not in proc.stdout, (
        "burners were spawned before the preflight decided the target was "
        "unreachable")


def test_a_run_that_collects_nothing_mid_loop_is_could_not_run(
        tmp_path: Path) -> None:
    """Preflight green, then a run collects nothing — still not a failure count.

    Its message is deliberately DIFFERENT from the preflight's, so a test that
    passes here cannot be passing on the preflight guard's error instead.
    """
    proc, _ = run_harness(
        tmp_path, runs=3,
        collect_out="1 test collected in 0.05s", collect_rc="0",
        run_out="no tests ran in 0.01s", run_rc="4")
    assert proc.returncode == 91, (
        f"expected 91, got {proc.returncode}\n{proc.stdout}{proc.stderr}")
    assert "COULD NOT RUN after" in proc.stdout
    assert "COULD NOT RUN: the targets collect no tests" not in proc.stdout, (
        "this must be the mid-loop guard's message, not the preflight's — "
        "otherwise the test is green for the wrong reason")
    assert "failures:" not in proc.stdout


def test_a_missing_flake_dir_is_could_not_run(tmp_path: Path) -> None:
    stub_dir, argv_log = _install_nix_stub(tmp_path)
    env = dict(os.environ)
    env["PATH"] = f"{stub_dir}{os.pathsep}{env.get('PATH', '')}"
    env.update({"ARGV_LOG": str(argv_log), "STUB_COLLECT_OUT": "",
                "STUB_COLLECT_RC": "0", "STUB_RUN_OUT": "", "STUB_RUN_RC": "0",
                "BURNERS": "0", "LOG_DIR": str(tmp_path / "logs")})
    nowhere = tmp_path / "not-a-flake"
    nowhere.mkdir()
    proc = subprocess.run(
        ["bash", str(HARNESS), "2", str(nowhere)],
        capture_output=True, text=True, env=env, timeout=60)
    assert proc.returncode == 91, proc.stdout + proc.stderr
    assert "COULD NOT RUN: no flake.nix" in proc.stderr


# --------------------------------------------------------------------------
# INVARIANT GUARDS — properties the original got right, pinned so a fix
# cannot silently drop them
# --------------------------------------------------------------------------

@pytest.mark.parametrize("runs", [1, 3])
def test_the_failure_count_is_the_exit_status(
        tmp_path: Path, runs: int) -> None:
    """Two points, not one — a hardcoded 1 or a boolean would pass at runs=1."""
    proc, _ = run_harness(
        tmp_path, runs=runs, run_out="1 failed in 0.10s", run_rc="1")
    assert proc.returncode == runs, (
        f"expected exit {runs} (one per failing run), got {proc.returncode}\n"
        f"{proc.stdout}")
    assert f"failures: {runs} / {runs}" in proc.stdout


def test_a_clean_sweep_reports_zero_and_exits_zero(tmp_path: Path) -> None:
    proc, _ = run_harness(tmp_path, runs=3)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "failures: 0 / 3" in proc.stdout


def test_an_empty_run_log_does_not_kill_the_harness(tmp_path: Path) -> None:
    """`set -o pipefail` + a `grep` over an EMPTY log exits 1.

    The summary line is cosmetic; it must never be able to abort the
    measurement. Without the guard the harness dies after run 1 and never
    prints a `failures:` line at all.
    """
    proc, _ = run_harness(tmp_path, runs=3, run_out="", run_rc="0")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "failures: 0 / 3" in proc.stdout, (
        "the harness stopped early on an empty log instead of finishing its "
        f"runs:\n{proc.stdout}")


def test_runs_above_the_cap_are_refused_so_91_stays_unambiguous(
        tmp_path: Path) -> None:
    """91 is only a distinguishable status while a failure count cannot reach it."""
    proc, _ = run_harness(tmp_path, runs=91)
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "91 is reserved" in proc.stderr


def test_the_reap_probe_sees_a_live_process_and_ignores_a_zombie() -> None:
    """Validate the INSTRUMENT before reading its verdict.

    A `live_pids_in_group` that returned `[]` unconditionally would make the
    reap test below green forever. Positive control: a real live process in its
    own group MUST be seen. Then the parser is driven directly over a zombie
    line and over a `comm` carrying spaces and parentheses — the two shapes a
    naive `.split()` gets wrong.
    """
    # POSITIVE CONTROL — the probe must be able to return a NON-EMPTY answer.
    sleeper = subprocess.Popen(["sleep", "30"], start_new_session=True)
    try:
        seen = live_pids_in_group(os.getpgid(sleeper.pid))
        assert sleeper.pid in seen, (
            f"the probe cannot see a live process — it is wired to nothing "
            f"(pid {sleeper.pid}, group answer {seen})")
    finally:
        sleeper.kill()
        sleeper.wait()

    assert state_and_pgrp("42 (python3.12) S 1 4242 4242 0 -1 …") == ("S", 4242)
    assert state_and_pgrp("42 (bash) Z 1 4242 4242 0 -1 …") == ("Z", 4242)
    # A comm with spaces AND parentheses — `.split()` on the whole line would
    # read "weird)" as the state here.
    assert state_and_pgrp("42 (my (weird) proc) R 1 99 99 0 -1 …") == ("R", 99)


def test_burners_are_reaped_when_the_run_ends(tmp_path: Path) -> None:
    """The EXIT trap must leave no busy loop behind.

    Checked by PROCESS GROUP, never by a `pkill -f` pattern: a pattern would
    match this test's own command line and a sibling agent's processes.
    """
    stub_dir, argv_log = _install_nix_stub(tmp_path)
    env = dict(os.environ)
    env["PATH"] = f"{stub_dir}{os.pathsep}{env.get('PATH', '')}"
    env.update({"ARGV_LOG": str(argv_log),
                "STUB_COLLECT_OUT": "1 test collected in 0.05s",
                "STUB_COLLECT_RC": "0", "STUB_RUN_OUT": "1 passed in 0.10s",
                "STUB_RUN_RC": "0",
                "BURNERS": "2", "LOG_DIR": str(tmp_path / "logs"),
                "PER_RUN_TIMEOUT": "30"})
    proc = subprocess.Popen(
        ["bash", str(HARNESS), "1", str(REPO_ROOT)],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        env=env, start_new_session=True)
    pgid = os.getpgid(proc.pid)
    out, _ = proc.communicate(timeout=120)
    assert proc.returncode == 0, out
    assert "spawned 2 burners" in out, out
    deadline = time.monotonic() + 10
    live = live_pids_in_group(pgid)
    while live and time.monotonic() < deadline:
        time.sleep(0.1)
        live = live_pids_in_group(pgid)
    assert not live, (
        f"burners survived the EXIT trap: pids {live} are still in process "
        f"group {pgid}")


# --------------------------------------------------------------------------
# INVARIANT GUARDS — the gate must never collect it, the deploy must carry it
# --------------------------------------------------------------------------

def test_the_harness_cannot_be_collected_by_the_gate() -> None:
    """It spawns CPU burners and takes minutes — the gate must never run it.

    pytest's default `python_files` is `test_*.py` / `*_test.py`, so a `.sh`
    file is uncollectable UNLESS a config widens the pattern. Both halves are
    asserted: the name, and the absence of any pytest config that could change
    what the name means.
    """
    name = HARNESS.name
    assert name.endswith(".sh"), name
    assert not name.startswith("test_"), name
    assert not name.endswith("_test.py"), name
    # `python_files` is the ONE setting that could change what that name means.
    # Check the CONTENT of whatever config exists rather than forbidding configs
    # outright — a `pyproject.toml` added for an unrelated reason must not fail
    # this with a message about pytest collection.
    widened = []
    for cfg in ("pytest.ini", "setup.cfg", "pyproject.toml", "tox.ini"):
        path = REPO_ROOT / cfg
        if path.exists() and "python_files" in path.read_text(
                encoding="utf-8", errors="replace"):
            widened.append(cfg)
    assert not widened, (
        f"{widened} set `python_files` — re-check that the pattern still "
        f"excludes {name}, or scripts/run-tests.sh will collect a script that "
        "spawns CPU burners and takes minutes")


def deploy_carries(repo: Path, path: Path) -> tuple[bool, str]:
    """Would the nix deploy carry `path`? Answered in BOTH tiers, never skipped.

    * dev host — the flake's source is the GIT TREE, so an untracked file under
      `scripts/dl-router/` is copied into the store by nothing and is silently
      absent from the deployed artifact. `git ls-files` is the question.
    * nix build sandbox — there is no `.git` at all (`cp -r ${./.}`, and a
      flake's `./.` is the tracked tree). But the directory being read there IS
      that store copy, so PRESENCE is the same claim, already answered.

    🔴 Deliberately not a `pytest.skip`: `scripts/run-tests.sh` GUARD 2 pins the
    expected-skip SET and its TOTAL, so a new skip fails the gate — and a
    deploy-completeness check that evaporates in the tier the merge is gated on
    would be reading as coverage while providing none.
    """
    if (repo / ".git").exists():
        rel = path.relative_to(repo).as_posix()
        proc = subprocess.run(
            ["git", "-C", str(repo), "ls-files", "--error-unmatch", rel],
            capture_output=True, text=True,
            env=hermetic_git_env())
        return proc.returncode == 0, (
            f"git ls-files --error-unmatch {rel}: rc={proc.returncode} "
            f"{proc.stderr.strip()}")
    return path.exists(), (
        f"no .git under {repo} (sandbox tier) — presence of {path} in the "
        "store copy is the proof the flake carried it")


def test_the_harness_is_carried_by_the_deploy() -> None:
    """🔴 `scripts/dl-router/` is a `home.file` source.

    An untracked file there is silently ABSENT from the deployed artifact — the
    switch succeeds and the file simply is not present. This harness lived
    untracked in one host's checkout for a day for exactly that reason.
    """
    carried, why = deploy_carries(REPO_ROOT, HARNESS)
    assert carried, (
        f"{HARNESS.relative_to(REPO_ROOT).as_posix()} would NOT reach the "
        f"deployed artifact — {why}")


def test_the_deploy_check_can_say_no_on_both_branches(tmp_path: Path) -> None:
    """Negative + positive control for `deploy_carries`, on BOTH its branches.

    Without this, the sandbox branch never executes on a dev host and the
    checkout branch never executes in the sandbox — so each tier would be
    trusting a branch it cannot observe.
    """
    # --- sandbox-shaped: no .git, so presence is the answer -----------------
    sandbox = tmp_path / "storecopy"
    sandbox.mkdir()
    absent = sandbox / "nope.sh"
    carried, why = deploy_carries(sandbox, absent)
    assert carried is False, why
    present = sandbox / "here.sh"
    present.write_text("", encoding="utf-8")
    carried, why = deploy_carries(sandbox, present)
    assert carried is True, why

    # --- checkout-shaped: a real repo, file untracked then tracked ----------
    repo = tmp_path / "repo"
    repo.mkdir()
    env = hermetic_git_env()
    subprocess.run(["git", "init", "-q", str(repo)], check=True, env=env)
    tracked_later = repo / "sub" / "thing.sh"
    tracked_later.parent.mkdir()
    tracked_later.write_text("", encoding="utf-8")
    carried, why = deploy_carries(repo, tracked_later)
    assert carried is False, (
        f"an UNTRACKED file must not read as carried — {why}")
    subprocess.run(["git", "-C", str(repo), "add", "sub/thing.sh"],
                   check=True, env=env)
    carried, why = deploy_carries(repo, tracked_later)
    assert carried is True, why


def test_the_harness_is_executable() -> None:
    assert os.access(HARNESS, os.X_OK), f"{HARNESS} lost its +x bit"
