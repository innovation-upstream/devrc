"""Copy `scripts/run-tests.sh` with its target list and floor table rewritten.

WHY THIS IS SHARED
------------------
Several suites need to drive the runner against ONE throwaway target so a guard
can be exercised in milliseconds instead of paying for the ~7,200-test real run.
Two of them grew their own copy of the rewrite, and when ``TARGET_FLOORS`` was
added the copies diverged: a runner patched to a tmp target but NOT to a
matching floor now dies at the two-way pin (exit 2, "a target with NO entry in
TARGET_FLOORS") instead of reaching the guard under test. That is the
"unreachable guard" failure -- a DIFFERENT guard's error killing the test, which
stays green with the guard under test deleted.

So the rewrite lives in one place and always patches BOTH tables together.
`claude/RULES.md` -> "One rule, one place".
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
RUN_TESTS = REPO_ROOT / "scripts" / "run-tests.sh"


def _replace_array(src: str, name: str, entries: list[str]) -> str:
    """Replace a whole `NAME=( … )` bash array literal, asserting the hit.

    Same contract as the two rewrites below: a regex that silently matches
    nothing leaves the copy identical to the real runner, so the "known-bad
    state" the caller believes it built never exists and the test passes for a
    reason unrelated to its subject.
    """
    body = "\n".join(f'  "{e}"' for e in entries)
    patched, n = re.subn(
        rf"^{name}=\(.*?^\)",
        f"{name}=(\n{body}\n)" if entries else f"{name}=()",
        src,
        count=1,
        flags=re.S | re.M,
    )
    assert n == 1, f"failed to replace {name} in the copied runner"
    return patched


def patch_runner_source(src: str, targets: list[str], floors: dict[str, int], *,
                        devhost: list[str] | None = None,
                        ack: list[str] | None = None,
                        hook_tests: list[str] | None = None,
                        shell_tests: list[str] | None = None) -> str:
    """Return `src` with HERMETIC_TARGETS and TARGET_FLOORS replaced wholesale.

    Both replacements are asserted to have landed. A silently-unmatched regex
    would leave the copy identical to the real runner, so every "known-bad
    state" test built on it would exercise the REAL target list and pass or fail
    for reasons that have nothing to do with the case it claims to cover.
    """
    body = "\n".join(f"  {t}" for t in targets)
    patched, n = re.subn(
        r"^HERMETIC_TARGETS=\(.*?^\)",
        f"HERMETIC_TARGETS=(\n{body}\n)",
        src,
        count=1,
        flags=re.S | re.M,
    )
    assert n == 1, "failed to replace HERMETIC_TARGETS in the copied runner"
    assert "HERMETIC_TARGETS=(\n  " in patched, "replacement produced an empty target list"

    # 🔴 THE THIRD TABLE IN THE SAME PIN, and leaving it alone reproduced this
    # module's own founding bug. GUARD 3a checks TARGET_FLOORS against
    # ALL_KNOWN_TARGETS = HERMETIC_TARGETS + DEVHOST_TARGETS. While DEVHOST_TARGETS
    # was empty, replacing two tables happened to be enough; the moment it gained
    # an entry (scripts/tests-devhost), every patched copy died at
    # "a target with NO entry in TARGET_FLOORS" — exit 2, before reaching the
    # guard under test. Six tests in test_no_real_launchers_all_targets.py and
    # test_run_tests_preconditions.py went red for a reason unrelated to their
    # subject, which is exactly the unreachable-guard shape described above.
    #
    # Emptied by default, like EXPECTED_SKIPS: a copy driving one throwaway
    # target has no dev-host tier to run. A caller that IS testing the dev-host
    # split names it.
    patched = _replace_array(patched, "DEVHOST_TARGETS", devhost or [])

    fbody = "\n".join(f'  "{t}|{v}"' for t, v in floors.items())
    patched, n = re.subn(
        r"^TARGET_FLOORS=\(.*?^\)",
        f"TARGET_FLOORS=(\n{fbody}\n)",
        patched,
        count=1,
        flags=re.S | re.M,
    )
    assert n == 1, "failed to replace TARGET_FLOORS in the copied runner"
    assert 'TARGET_FLOORS=(\n  "' in patched, "replacement produced an empty floor table"

    # EXPECTED_SKIPS pins skips for the REAL target list (one entry, in
    # scripts/repo-cos/tests). A copy that runs a throwaway target observes zero
    # skips and would fail the skip-total accounting -- "FEWER than pinned" --
    # for a reason that has nothing to do with the guard under test, making a
    # GREEN outcome unreachable and every negative control ambiguous. Emptied
    # here, deliberately: this is the one guard a patched copy structurally
    # cannot honour, and it is the real runner's job, not the copy's.
    patched, n = re.subn(
        r"^EXPECTED_SKIPS=\(.*?^\)", "EXPECTED_SKIPS=()", patched, count=1, flags=re.S | re.M
    )
    assert n == 1, "failed to empty EXPECTED_SKIPS in the copied runner"

    # GUARD 7's ledger and the two non-pytest target lists. Left ALONE by
    # default so every existing caller keeps driving the real ones; a caller
    # that is testing GUARD 7 itself, or that wants a copy which does not pay
    # for the hook/shell scripts, names them explicitly.
    if ack is not None:
        patched = _replace_array(patched, "NOLAUNCH_ACK", ack)
    if hook_tests is not None:
        patched = _replace_array(patched, "HOOK_TESTS", hook_tests)
    if shell_tests is not None:
        patched = _replace_array(patched, "SHELL_TESTS", shell_tests)
    return patched


def runner_with_targets(
    tmp_path: Path,
    targets: list[str],
    floors: dict[str, int] | None = None,
    **kwargs,
) -> Path:
    """Write a patched copy of run-tests.sh into `tmp_path` and return its path.

    `floors` defaults to 1 per target -- the weakest floor the pin accepts, so
    the copy reaches whatever guard the caller is actually testing rather than
    tripping the floor itself.
    """
    if floors is None:
        floors = {t: 1 for t in targets}
    expected = set(targets) | set(kwargs.get("devhost") or [])
    assert set(floors) == expected, (
        "the floor table and the target list must match EXACTLY both ways -- "
        "that is the pin these copies exist to satisfy, not to bypass. The "
        "dev-host list counts too: GUARD 3a reads HERMETIC_TARGETS + "
        "DEVHOST_TARGETS.\n"
        f"targets={sorted(expected)}\nfloors={sorted(floors)}"
    )
    dst = tmp_path / "run-tests.sh"
    dst.write_text(patch_runner_source(RUN_TESTS.read_text(), targets, floors,
                                       **kwargs))
    return dst


def write_pytest_suite(directory: Path, n_tests: int, prefix: str = "test_gen") -> Path:
    """Create `directory` holding a single module with exactly `n_tests` trivial
    passing tests. Used to build a target of a KNOWN collected count."""
    directory.mkdir(parents=True, exist_ok=True)
    lines = [f"def {prefix}_{i}():\n    assert True\n" for i in range(n_tests)]
    f = directory / f"{prefix}.py"
    f.write_text("\n".join(lines) if lines else "")
    return f
