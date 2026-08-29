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

    # DEVHOST_TARGETS is concatenated with HERMETIC_TARGETS into
    # ALL_KNOWN_TARGETS, and the floor table pins THAT set both ways. So a
    # dev-host target surviving into a copy whose TARGET_FLOORS was just
    # replaced wholesale is "a target with NO entry in TARGET_FLOORS", and the
    # copy dies on GUARD 3a's FATAL before ever reaching the guard the caller
    # is testing.
    #
    # 🔴 This was a silent no-op until 2026-08-29: DEVHOST_TARGETS had been
    # EMPTY since the day it was added, so this helper was written — correctly
    # for the time — as if HERMETIC_TARGETS were the whole target list. The
    # first entry ever added to it took ~20 harness tests red at once, none of
    # them about the dev-host tier. Emptied for the same reason as
    # EXPECTED_SKIPS below: a copy runs a throwaway target list, so the real
    # runner's tiers are not its business.
    patched, n = re.subn(
        r"^DEVHOST_TARGETS=\(.*?^\)", "DEVHOST_TARGETS=()", patched, count=1, flags=re.S | re.M
    )
    assert n == 1, "failed to empty DEVHOST_TARGETS in the copied runner"

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
    assert set(floors) == set(targets), (
        "the floor table and the target list must match EXACTLY both ways -- "
        "that is the pin these copies exist to satisfy, not to bypass.\n"
        f"targets={sorted(targets)}\nfloors={sorted(floors)}"
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
