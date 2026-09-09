"""The pytest tier's worker count: how many, and what decides it.

WHY THIS FILE EXISTS. `run-tests.sh` used to hardcode `min(nproc, 4)`. The
comment above it said the default "ADAPTS to the machine", and it did not: the
constant 4 was doing all the work, and it happened to equal the `devrc-ci`
pod's CPU limit. On the 24-core workbench that left 20 cores idle for a solo
gate run.

The budget is now the narrowest cgroup v2 quota, falling back to `nproc`, capped
at 8. 🔴 The branch that matters — a container whose quota is NARROWER than the
node's core count — cannot occur on any host these tests run on, so it is
exercised through the documented DEVRC_TEST_CGROUP_ROOT/SELF seam against a
fake hierarchy. Without that seam the CI-pod branch would be untested on every
machine that runs the suite, which is the whole population.

Each test drives the real script and reads the banner line it prints
(`parallelism =N pytest worker(s)`), never a re-implementation of the formula.

🔴 TWO THINGS THIS FILE GOT WRONG ONCE, BOTH WORTH KEEPING IN VIEW.

1. THE EXPECTATION MUST COME FROM THE SAME SOURCE THE CODE READS. The first
   version computed `os.cpu_count()`; `run-tests.sh` floors on GNU `nproc`.
   Those two disagree — `os.cpu_count()` reports ONLINE cpus, `nproc` honours
   the CPU affinity mask and OMP_NUM_THREADS/OMP_THREAD_LIMIT — and on the nix
   build sandbox that gates the merge they measured 16 and 4. Five assertions
   here expected 8, got 4, and `tekton/devrc-pytests` was red on the PR that
   introduced them, while the same file was green on the dev host where the two
   numbers happen to agree. `_nproc()` below is the only cpu-count source this
   file may use.

2. EACH CASE NEEDS ITS OWN PROCESS, AND THAT USED TO COST A FULL SUITE RUN.
   Every case drives a different fake cgroup, so they cannot share one run.
   Measured 2026-09-08: a full nested run cost ~150s wall, eleven of them, each
   spawning its own 8 xdist workers inside an outer run that already had 8 —
   about 28 minutes of nested runs added to the tier this change exists to speed
   up. `DEVRC_TEST_BUDGET_ONLY=1` stops the runner right after the banner these
   tests read; nothing after it is observed here anyway. The seam exits NON-ZERO
   so it can never manufacture a green.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
RUNNER = REPO / "scripts" / "run-tests.sh"

PARALLELISM_RE = re.compile(r"parallelism =(\d+) pytest worker\(s\)")


def _nproc() -> int:
    """`nproc`, read exactly the way `run-tests.sh` reads it.

    🔴 NOT `os.cpu_count()` — see item 1 in this module's docstring. This
    mirrors `_devrc_default_jobs="$(nproc 2>/dev/null || echo 1)"` and the
    `''|*[!0-9]*|0 -> 1` normalisation on the next line, so an expectation
    derived from it cannot disagree with the code for a reason that is about
    the reader rather than about the budget.
    """
    try:
        proc = subprocess.run(["nproc"], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return 1
    out = proc.stdout.strip()
    if not out.isdigit() or int(out) == 0:
        return 1
    return int(out)


def _narrow_cpus(n: int) -> list[int] | None:
    """The first `n` CPUs this process is allowed to use, or None if it cannot.

    🔴 WHY NARROWING THE CPU SET IS THE ONLY WAY TO SEE ONE OF THE CLAIMS.
    `min(nproc, quota, 8)` has three inputs and two seams. The quota is seamed;
    `nproc` was not, and on a 24-core box `min(nproc, 8)` is 8 for EVERY quota
    above 8 — so a mutant that let the quota WIN OUTRIGHT was still capped to 8
    and produced the identical answer. Measured: it SURVIVED a fully green run.
    Restricting the child's affinity mask makes `nproc` small enough that a
    quota can sit between it and the cap, which is the only arrangement in which
    "the walk may only NARROW" is observable at all.

    🔴 VIA `taskset`, NOT `preexec_fn`. `subprocess`'s `preexec_fn` runs Python
    in the child between fork and exec, and this suite runs under pytest-xdist,
    whose workers are threaded — forking a threaded process and then allocating
    in the child is the classic deadlock, and a deadlock is the one failure mode
    worse than a red test. `taskset` is in this repo's own gate toolchain
    (`gateTools` in flake.nix carries `pkgs.util-linux`), so it is present in
    the sandbox tier as well as the dev shell.
    """
    if shutil.which("taskset") is None:
        return None
    allowed = sorted(os.sched_getaffinity(0))
    if len(allowed) < n:
        return None
    return allowed[:n]


def _run(env_overrides: dict[str, str], cpus: list[int] | None = None):
    """Run the runner as far as the banner, then stop.

    DEVRC_TEST_BUDGET_ONLY is the documented seam for exactly this: it exits
    after `parallelism =N` having run no tests, so a case costs the runner's
    preamble instead of a whole nested suite. Every assertion in this file is
    about that one line.

    `cpus`, when given, runs the child under `taskset` — GNU `nproc` honours the
    affinity mask, so this is how a case pins the runner's fallback input
    without touching this process or the machine.
    """
    env = dict(os.environ)
    # A nested run is forced serial by PYTEST_CURRENT_TEST, which we inherit —
    # and serial would pin the answer at 1 and make every assertion below
    # vacuous. Dropping it is the point of the run: we are testing the budget
    # computation, not the nesting rule (which test_run_tests_targets.py owns).
    env.pop("PYTEST_CURRENT_TEST", None)
    env.pop("DEVRC_TEST_JOBS", None)
    # 🔴 SCRUB THE SEAMS WE DO NOT OWN. An operator (or an audit brief) with
    # DEVRC_TEST_CGROUP_ROOT or DEVRC_TARGETS exported would otherwise steer a
    # case that deliberately names neither, and the failure would look like a
    # budget bug.
    env.pop("DEVRC_TEST_CGROUP_ROOT", None)
    env.pop("DEVRC_TEST_CGROUP_SELF", None)
    env.pop("DEVRC_TARGETS", None)
    env["DEVRC_TEST_BUDGET_ONLY"] = "1"
    env.update(env_overrides)
    argv = ["bash", str(RUNNER), str(REPO)]
    if cpus is not None:
        argv = ["taskset", "-c", ",".join(str(c) for c in cpus)] + argv
    proc = subprocess.run(
        argv,
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


# --- the instrument itself ----------------------------------------------------


def test_the_budget_only_seam_runs_no_tests_and_cannot_report_a_pass():
    """POSITIVE + NEGATIVE control for every other test in this file.

    Ten cases below read a banner from a run stopped by DEVRC_TEST_BUDGET_ONLY.
    If that seam ever became a silent early exit that still said PASS, an
    ambient value of it would empty the real gate and read as green. So: it must
    say NO TESTS RAN, and its verdict line must be FAIL.
    """
    proc = _run({})
    out = proc.stdout + proc.stderr
    assert PARALLELISM_RE.search(out), out[-2000:]
    assert "NO TESTS RAN" in out, out[-2000:]
    assert proc.returncode == 3, (
        f"the seam exited {proc.returncode}; it must exit 3 (an environment "
        "precondition stopped the run), never 0.\n" + out[-2000:]
    )
    assert "RESULT: FAIL (exit=3)" in out, (
        "a run that executed no tests announced something other than FAIL — an "
        "ambient DEVRC_TEST_BUDGET_ONLY would then empty the gate silently.\n"
        + out[-2000:]
    )
    assert "RESULT: PASS" not in out, out[-2000:]


# --- the regression the change exists for -------------------------------------


def _no_quota_anywhere(tmp_path: Path) -> dict[str, str]:
    """A fake hierarchy that is explicitly UNQUOTA'D at every level.

    🔴 WHY THE TWO TESTS BELOW ARE SEAMED RATHER THAN READING THE REAL MACHINE.
    They assert an exact worker count, and `min(nproc, quota, 8)` has two inputs.
    Reading the real hierarchy leaves the second one unknown: this box has no
    quota, but a CI builder is a container and may well have one — a builder
    with nproc 8 and a quota of 4 would red them for a reason that is not a bug.
    Re-deriving the quota in Python to compensate would mean re-implementing the
    code under test, which is worse. Pinning the quota input through the
    documented seam leaves exactly one variable, `nproc`, and the test reads
    that from the same source the code does.

    The unseamed path is not abandoned — see
    `test_the_real_unseamed_walk_produces_a_budget_within_its_own_bounds`.
    """
    return _fake_cgroup(tmp_path, [("", "max 100000"), ("leaf", "max 100000")])


def test_an_unquota_d_host_gets_more_than_the_old_hardcoded_four(tmp_path):
    """RED before this change (the old code capped at 4), green after.

    Skipped rather than asserted on a small box: on a host with <=4 usable cores
    the old and new formulas agree, so the test could not distinguish them and a
    green would mean nothing. The skip guard reads `nproc` for the same reason
    the assertion does — written on `os.cpu_count()` it did not fire on the one
    machine where it mattered, which is how the gating tier went red.
    """
    usable = _nproc()
    if usable <= 4:
        pytest.skip(f"nproc reports {usable}; old and new formulas agree below 5")
    got = _parallelism(_run(_no_quota_anywhere(tmp_path)))
    assert got == min(usable, 8), (
        f"expected min(nproc={usable}, 8) = {min(usable, 8)} workers on an "
        f"unquota'd host, got {got}. 4 means the hardcoded cap is back."
    )


def test_the_cap_is_eight_not_nproc(tmp_path):
    """The ceiling is deliberate: past ~8 the run is bounded by its biggest FILE."""
    usable = _nproc()
    if usable <= 8:
        pytest.skip(f"nproc reports {usable}; the 8-cap is not observable below 9")
    got = _parallelism(_run(_no_quota_anywhere(tmp_path)))
    assert got == 8, f"expected the cap of 8 on a {usable}-core host, got {got}"


def test_the_real_unseamed_walk_produces_a_budget_within_its_own_bounds():
    """The seams must not be the ONLY way this code is ever executed.

    Every other budget test here pins the quota through
    DEVRC_TEST_CGROUP_ROOT/SELF. This one reads the actual machine — real
    `/proc/self/cgroup`, real `/sys/fs/cgroup`, whatever they say — so a change
    that broke the unseamed path (an unreadable file aborting the run, a walk
    that never terminates, a `-n 0`) is caught here and nowhere else.

    🔴 IT ASSERTS A BOUND, NOT A POINT, AND THAT IS DELIBERATE. The host's real
    quota is unknown and unknowable to this test without re-implementing the
    walk. `1 <= budget <= min(nproc, 8)` is the complete set of claims the code
    makes independently of any machine: never zero (xdist rejects `-n 0`), never
    wider than nproc (the walk may only narrow), never past the cap.
    """
    usable = _nproc()
    got = _parallelism(_run({}))
    assert 1 <= got <= min(usable, 8), (
        f"the unseamed budget was {got}, outside 1..min(nproc={usable}, 8). "
        "Either the walk widened past nproc, or it produced a value xdist "
        "cannot use."
    )


# --- the CI-pod branch, via the seam ------------------------------------------


def test_a_cgroup_quota_narrower_than_nproc_wins(tmp_path):
    """The devrc-ci case: quota 4 on a much larger node must yield 4, not nproc.

    This is the branch the old `min(nproc, 4)` got right only by coincidence.
    🔴 Skipped where nproc is already <= 4, because there the quota and the
    fallback agree and a green would not distinguish them.
    """
    if _nproc() <= 4:
        pytest.skip(f"nproc reports {_nproc()}; a quota of 4 cannot be seen to narrow it")
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
    """The walk may only ever NARROW what nproc already allowed.

    🔴 THE CPU NARROWING IS WHAT MAKES THIS TEST ABLE TO FAIL. With the child
    left on all 24 cores, `min(nproc, 8)` is 8 and a mutant that lets the quota
    win outright is capped to 8 as well — identical output, mutant SURVIVED, and
    that is exactly what was measured before this fixture existed. The guarded
    branch has to land somewhere OTHER than its own boundary: two visible CPUs
    and a quota of 5 puts the wrong answer (5) strictly between the right one
    (2) and the cap (8).
    """
    cpus = _narrow_cpus(2)
    if cpus is None:
        pytest.skip("fewer than 2 CPUs in this process's affinity mask")
    env = _fake_cgroup(tmp_path, [("", "max 100000"), ("pod", "500000 100000")])
    got = _parallelism(_run(env, cpus=cpus))
    assert got == 2, (
        f"expected min(nproc=2, quota=5, cap=8) = 2, got {got}. 5 means the "
        "quota was allowed to WIDEN the budget past what nproc permitted."
    )


def test_the_narrowed_cpu_seam_actually_narrows_nproc(tmp_path):
    """POSITIVE CONTROL for the affinity narrowing used by the test above.

    If `preexec_fn` silently did nothing, the case above would run on all cores
    and its `== 2` would fail loudly — but a future refactor could just as
    easily make it pass for the wrong reason. So: with no quota anywhere and the
    child pinned to two CPUs, the budget must be exactly 2, and that number can
    only have come from `nproc` seeing the narrowed mask.
    """
    cpus = _narrow_cpus(2)
    if cpus is None:
        pytest.skip("fewer than 2 CPUs in this process's affinity mask")
    got = _parallelism(_run(_no_quota_anywhere(tmp_path), cpus=cpus))
    assert got == 2, (
        f"the child reported a budget of {got} while pinned to 2 CPUs; the "
        "affinity narrowing did not take effect, so every test built on it is "
        "measuring the unrestricted machine."
    )


def test_max_at_the_leaf_falls_through_to_a_parents_quota(tmp_path):
    """The walk is leaf->root: an unquota'd leaf under a quota'd parent is bounded.

    This is the shape a systemd user scope inside a quota'd slice actually has,
    and a walk that stopped at the leaf would report the node's core count.
    """
    if _nproc() <= 3:
        pytest.skip(f"nproc reports {_nproc()}; a quota of 3 cannot be seen to narrow it")
    env = _fake_cgroup(
        tmp_path,
        [("", "max 100000"), ("slice", "300000 100000"), ("slice/scope", "max 100000")],
    )
    assert _parallelism(_run(env)) == 3


def test_an_empty_cpu_max_is_skipped_and_the_walk_continues_to_the_parent(tmp_path):
    """A short/empty cpu.max must not stop the walk, and must not be read as a quota.

    ⚠ LABELLED HONESTLY: this is an INVARIANT GUARD for the `q=""; p=""` reset
    that precedes the read, not regression coverage of it. Bash's `read` assigns
    its variables even when it returns non-zero at EOF, and the walk RETURNS on
    the first numeric quota it finds, so no fixture reachable through this seam
    can make a stale value survive into the next level. The reset stays because
    the failure it forecloses is a plausible WRONG NUMBER rather than an error —
    but a mutation that deletes it does not die here, and this docstring used to
    claim it did.

    What the assertion below DOES pin, and what a mutant can still break: an
    empty cpu.max is skipped rather than treated as a quota or as a reason to
    abandon the walk, so the parent's 6-core quota is the answer.
    """
    usable = _nproc()
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
    # min(nproc, quota): the walk may only narrow what nproc already allowed, so
    # on a builder with fewer than 6 cores the correct answer is nproc's.
    assert _parallelism(_run(env)) == min(usable, 6)


def test_the_walk_stops_at_the_root_it_was_given(tmp_path):
    """A seamed walk must not climb out of its fake tree.

    The terminator used to be the literal /sys/fs/cgroup, so a fake root never
    matched it and the walk continued up the real filesystem.

    🔴 THE cpu.max ONE LEVEL ABOVE THE ROOT IS WHAT MAKES THIS OBSERVABLE, and
    it is here because the first version of this test did not have it. Measured:
    deleting the `break` from the walk left this test GREEN — with nothing above
    the fake root, an escaping walk climbs through pytest's tmp dirs to `/`,
    finds no cpu.max on the way, and returns the same "no quota" answer as a
    correct walk. The docstring claimed the escape was covered and the body
    could not see it. Now a walk that climbs one level reports 2 instead.
    """
    usable = _nproc()
    root = tmp_path / "cgroup"
    (root / "deep" / "deeper").mkdir(parents=True)
    (tmp_path / "cpu.max").write_text("200000 100000\n")  # OUTSIDE the root
    self_file = tmp_path / "self-cgroup"
    self_file.write_text("0::/deep/deeper\n")
    env = {
        "DEVRC_TEST_CGROUP_ROOT": str(root),
        "DEVRC_TEST_CGROUP_SELF": str(self_file),
    }
    got = _parallelism(_run(env))
    assert got == min(usable, 8), (
        f"expected the nproc fallback (min({usable}, 8)), got {got}. 2 means the "
        "walk climbed past the root it was given."
    )


def test_a_root_level_cgroup_path_does_not_escape_the_root(tmp_path):
    """`0::/` — the k8s cgroupns-private shape — used to walk OUT of the root.

    `${root}${cg}` is then `<root>/`, which never string-equals `$root`, so the
    `break` never fired and `dirname "<root>/"` returned the root's PARENT. The
    fixture makes that escape observable: the only cpu.max in the tree sits in
    the parent directory of the seamed root, where a correct walk can never look.
    A wrong walk reports 2; a correct one finds no quota and falls back to nproc.
    """
    usable = _nproc()
    root = tmp_path / "cgroup"
    root.mkdir()
    # OUTSIDE the seamed root, one level up — reachable only by escaping it.
    (tmp_path / "cpu.max").write_text("200000 100000\n")
    self_file = tmp_path / "self-cgroup"
    self_file.write_text("0::/\n")
    env = {
        "DEVRC_TEST_CGROUP_ROOT": str(root),
        "DEVRC_TEST_CGROUP_SELF": str(self_file),
    }
    got = _parallelism(_run(env))
    assert got == min(usable, 8), (
        f"expected the nproc fallback (min({usable}, 8)), got {got}. 2 means the "
        "walk climbed out of the root it was given and read the cpu.max above it."
    )


def test_a_hybrid_v1_v2_proc_self_cgroup_reads_the_unified_line(tmp_path):
    """On a host running cgroup v1 and v2 together, the FIRST line is a v1 one.

    `cut -d: -f3 | head -1` took it, so the walk started from a v1 controller's
    path, found no cpu.max, and the real v2 quota was silently missed — the
    budget fell back to nproc in exactly the container this feature exists for.
    """
    if _nproc() <= 4:
        pytest.skip(f"nproc reports {_nproc()}; a quota of 4 cannot be seen to narrow it")
    root = tmp_path / "cgroup"
    (root / "pod").mkdir(parents=True)
    (root / "pod" / "cpu.max").write_text("400000 100000\n")
    self_file = tmp_path / "self-cgroup"
    self_file.write_text(
        "9:cpuset:/kubepods/besteffort\n"
        "4:cpu,cpuacct:/kubepods/besteffort\n"
        "0::/pod\n"
    )
    env = {
        "DEVRC_TEST_CGROUP_ROOT": str(root),
        "DEVRC_TEST_CGROUP_SELF": str(self_file),
    }
    got = _parallelism(_run(env))
    assert got == 4, (
        f"expected the v2 quota of 4, got {got}. The walk read a cgroup v1 line "
        "instead of the `0::` unified one."
    )


def test_a_cgroup_path_containing_a_colon_is_not_truncated(tmp_path):
    """A systemd scope name may contain `:`, and `cut -d:` ate everything after it.

    `0::/sess:2.scope` became `/sess`, a directory that does not exist, so the
    quota that was really there went unread.
    """
    if _nproc() <= 4:
        pytest.skip(f"nproc reports {_nproc()}; a quota of 4 cannot be seen to narrow it")
    root = tmp_path / "cgroup"
    (root / "sess:2.scope").mkdir(parents=True)
    (root / "sess:2.scope" / "cpu.max").write_text("400000 100000\n")
    self_file = tmp_path / "self-cgroup"
    self_file.write_text("0::/sess:2.scope\n")
    env = {
        "DEVRC_TEST_CGROUP_ROOT": str(root),
        "DEVRC_TEST_CGROUP_SELF": str(self_file),
    }
    got = _parallelism(_run(env))
    assert got == 4, (
        f"expected the quota of 4 from `/sess:2.scope`, got {got}. The path was "
        "truncated at the colon."
    )


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
