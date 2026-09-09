"""The pytest tier's worker count: how many, and what decides it.

WHY THIS FILE EXISTS. `run-tests.sh` used to hardcode `min(nproc, 4)`. The
comment above it said the default "ADAPTS to the machine", and it did not: the
constant 4 was doing all the work, and it happened to equal the `devrc-ci`
pod's CPU limit. On the 24-core workbench that left 20 cores idle for a solo
gate run — measured 2026-09-08, the pytest tier's median was 20.1 min over 237
real runs.

The budget is now the narrowest cgroup v2 quota, falling back to `nproc`, capped
at 8. 🔴 The branch that matters — a container whose quota is NARROWER than the
node's core count — cannot occur on any host these tests run on, so it is
exercised through the documented DEVRC_TEST_CGROUP_ROOT/SELF seam against a
fake hierarchy. Without that seam the CI-pod branch would be untested on every
machine that runs the suite, which is the whole population.

Each test drives the real script and reads the banner line it prints
(`parallelism =N pytest worker(s)`), never a re-implementation of the formula.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
RUNNER = REPO / "scripts" / "run-tests.sh"

PARALLELISM_RE = re.compile(r"parallelism =(\d+) pytest worker\(s\)")


def _run(env_overrides: dict[str, str], target: str = "scripts/collector/i3/tests"):
    """Run the runner far enough to print its banner, then let it proceed.

    `scripts/collector/i3/tests` is the smallest target in the table (floor 12),
    picked so these tests cost ~seconds rather than re-running the gate.
    """
    env = dict(os.environ)
    # A nested run is forced serial by PYTEST_CURRENT_TEST, which we inherit —
    # and serial would pin the answer at 1 and make every assertion below
    # vacuous. Dropping it is the point of the run: we are testing the budget
    # computation, not the nesting rule (which test_run_tests_targets.py owns).
    env.pop("PYTEST_CURRENT_TEST", None)
    env.pop("DEVRC_TEST_JOBS", None)
    env["DEVRC_TARGETS"] = target
    env.update(env_overrides)
    proc = subprocess.run(
        ["bash", str(RUNNER), str(REPO)],
        capture_output=True,
        text=True,
        env=env,
        timeout=900,
    )
    return proc


def _parallelism(proc) -> int:
    out = proc.stdout + proc.stderr
    m = PARALLELISM_RE.search(out)
    assert m, (
        "the runner printed no `parallelism =N pytest worker(s)` banner, so this "
        "test cannot observe the thing it asserts. That is a broken instrument, "
        "not a pass.\n--- output ---\n" + out[-3000:]
    )
    return int(m.group(1))


def _fake_cgroup(tmp_path: Path, levels: list[tuple[str, str]]) -> dict[str, str]:
    """Build a fake cgroup v2 tree.

    `levels` is leaf-last: [("", "max 100000"), ("a", "max 100000"),
    ("a/b", "400000 100000")] writes cpu.max at the root, at a/, and at a/b/,
    and reports a/b as the process's own cgroup.
    """
    root = tmp_path / "cgroup"
    for rel, content in levels:
        d = root / rel if rel else root
        d.mkdir(parents=True, exist_ok=True)
        (d / "cpu.max").write_text(content + "\n")
    leaf = levels[-1][0]
    self_file = tmp_path / "self-cgroup"
    self_file.write_text(f"0::/{leaf}\n")
    return {
        "DEVRC_TEST_CGROUP_ROOT": str(root),
        "DEVRC_TEST_CGROUP_SELF": str(self_file),
    }


# --- the regression the change exists for -------------------------------------


def test_an_unquota_d_host_gets_more_than_the_old_hardcoded_four():
    """RED before this change (the old code capped at 4), green after.

    Skipped rather than asserted on a small box: on a host with <=4 usable cores
    the old and new formulas agree, so the test could not distinguish them and a
    green would mean nothing.
    """
    usable = os.cpu_count() or 1
    if usable <= 4:
        pytest.skip(f"host has {usable} cpu(s); old and new formulas agree below 5")
    # No quota anywhere -> falls through to nproc, capped at 8.
    got = _parallelism(_run({}))
    assert got == min(usable, 8), (
        f"expected min(nproc={usable}, 8) = {min(usable, 8)} workers on an "
        f"unquota'd host, got {got}. 4 means the hardcoded cap is back."
    )


def test_the_cap_is_eight_not_nproc():
    """The ceiling is deliberate: past ~8 the run is bounded by its biggest FILE."""
    usable = os.cpu_count() or 1
    if usable <= 8:
        pytest.skip(f"host has {usable} cpu(s); the 8-cap is not observable below 9")
    got = _parallelism(_run({}))
    assert got == 8, f"expected the cap of 8 on a {usable}-core host, got {got}"


# --- the CI-pod branch, via the seam ------------------------------------------


def test_a_cgroup_quota_narrower_than_nproc_wins(tmp_path):
    """The devrc-ci case: quota 4 on a much larger node must yield 4, not nproc.

    This is the branch the old `min(nproc, 4)` got right only by coincidence.
    """
    env = _fake_cgroup(tmp_path, [("", "max 100000"), ("pod", "400000 100000")])
    assert _parallelism(_run(env)) == 4


def test_a_one_cpu_quota_yields_one_worker(tmp_path):
    """A 1-CPU pod must not run 4 workers — the oversubscription the cap feared."""
    env = _fake_cgroup(tmp_path, [("", "max 100000"), ("pod", "100000 100000")])
    assert _parallelism(_run(env)) == 1


def test_a_sub_cpu_quota_floors_at_one_rather_than_zero(tmp_path):
    """quota/period < 1 must not produce `-n 0`, which pytest-xdist rejects."""
    env = _fake_cgroup(tmp_path, [("", "max 100000"), ("pod", "50000 100000")])
    assert _parallelism(_run(env)) == 1


def test_a_quota_wider_than_nproc_does_not_widen_the_budget(tmp_path):
    """The walk may only ever NARROW what nproc already allowed."""
    usable = os.cpu_count() or 1
    env = _fake_cgroup(tmp_path, [("", "max 100000"), ("pod", "64000000 100000")])
    assert _parallelism(_run(env)) == min(usable, 8)


def test_max_at_the_leaf_falls_through_to_a_parents_quota(tmp_path):
    """The walk is leaf->root: an unquota'd leaf under a quota'd parent is bounded.

    This is the shape a systemd user scope inside a quota'd slice actually has,
    and a walk that stopped at the leaf would report the node's core count.
    """
    env = _fake_cgroup(
        tmp_path,
        [("", "max 100000"), ("slice", "300000 100000"), ("slice/scope", "max 100000")],
    )
    assert _parallelism(_run(env)) == 3


def test_an_empty_cpu_max_does_not_inherit_the_previous_levels_numbers(tmp_path):
    """A short/empty cpu.max must be skipped, not silently reuse the last read.

    Without the explicit `q=""; p=""` reset before the read, the leaf's 2-core
    quota survives into the parent iteration and gets reported for it. The tell
    would be a plausible number, not an error.
    """
    root = tmp_path / "cgroup"
    (root / "a").mkdir(parents=True)
    (root / "cpu.max").write_text("600000 100000\n")
    (root / "a" / "cpu.max").write_text("")  # unreadable-as-a-pair
    self_file = tmp_path / "self-cgroup"
    self_file.write_text("0::/a\n")
    env = {
        "DEVRC_TEST_CGROUP_ROOT": str(root),
        "DEVRC_TEST_CGROUP_SELF": str(self_file),
    }
    assert _parallelism(_run(env)) == 6


def test_the_walk_stops_at_the_root_it_was_given(tmp_path):
    """A seamed walk must not climb out of its fake tree.

    The terminator used to be the literal /sys/fs/cgroup, so a fake root never
    matched it and the walk continued up the real filesystem. With no cpu.max
    anywhere in the fake tree the answer must be "no quota" (-> nproc), and the
    run must still finish rather than walking to /.
    """
    usable = os.cpu_count() or 1
    root = tmp_path / "cgroup"
    (root / "deep" / "deeper").mkdir(parents=True)
    self_file = tmp_path / "self-cgroup"
    self_file.write_text("0::/deep/deeper\n")
    env = {
        "DEVRC_TEST_CGROUP_ROOT": str(root),
        "DEVRC_TEST_CGROUP_SELF": str(self_file),
    }
    assert _parallelism(_run(env)) == min(usable, 8)


# --- the override still wins --------------------------------------------------


def test_devrc_test_jobs_overrides_a_quota(tmp_path):
    """The documented escape hatch outranks the computed budget, both ways."""
    env = _fake_cgroup(tmp_path, [("", "max 100000"), ("pod", "200000 100000")])
    env["DEVRC_TEST_JOBS"] = "6"
    assert _parallelism(_run(env)) == 6


def test_devrc_test_jobs_one_still_gets_the_serial_path():
    """DEVRC_TEST_JOBS=1 is the documented bisect/flake-hunt mode."""
    proc = _run({"DEVRC_TEST_JOBS": "1"})
    out = proc.stdout + proc.stderr
    assert _parallelism(proc) == 1
    assert "(serial)" in out, (
        "jobs=1 must announce itself as serial — the banner is how an operator "
        "tells a serial run from a parallel one when comparing timings.\n" + out[-2000:]
    )
