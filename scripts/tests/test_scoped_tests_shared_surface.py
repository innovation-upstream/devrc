"""`scoped-tests.sh` must refuse to produce a scoped verdict for a SHARED SURFACE.

WHY THIS EXISTS, measured 2026-09-11 at `018e483b`. The scoped mapper finds test
files that NAME what you changed. For a shared library that is the wrong shape:

    module in scripts/testlib/   selected   files referencing it   targets spanned
    mockbin.py                       10              69                  7
    gitenv.py                         2              17                  8
    hermetic_git.py                   4              15                  1
    store_siting.py                   0               6                  3

A `mockbin.py` edit ran 10 files, printed `RESULT: PASS`, and never executed 59
of the 69 files that reference it. The zero-select rows were already SAFE — the
script exits 4 when the mapping selects nothing. The dangerous rows are the
middle ones, because a run that executes SOMETHING reads as a successful scoped
run.

🔴 THE REFUSAL IS THE POINT, NOT A WIDER SELECTION. `gate-inventory-2026-09-08.md`
§11 risk 1: *"a target that stops running because a rule missed a path looks
exactly like a target that passed"*. Widening the mapping would make this guard's
own correctness unobservable; refusing cannot be mistaken for coverage.

🔴 THE NEGATIVE CONTROL IS LOAD-BEARING. A trigger list that matched EVERYTHING
would satisfy every positive case here and be catastrophically wrong. So
`test_an_ordinary_subsystem_file_still_scopes_normally` asserts the opposite
direction, and without it this module would be green for a broken guard.
"""

import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCOPED = REPO_ROOT / "scripts" / "scoped-tests.sh"

# Every glob the script declares, as a concrete path a diff could contain.
# 🔴 Pinned as a LEDGER, two-way: `test_every_declared_trigger_is_exercised`
# fails when the script gains a glob nobody exercises here, so a new shared
# surface cannot be added without a case proving it fires.
TRIGGER_CASES = {
    "flake.nix": "flake.nix",
    "flake.lock": "flake.lock",
    "nix/*": "nix/home.nix",
    "scripts/lib/*": "scripts/lib/host-role.sh",
    "scripts/testlib/*": "scripts/testlib/mockbin.py",
    "scripts/run-tests.sh": "scripts/run-tests.sh",
    "scripts/run-node-tests.sh": "scripts/run-node-tests.sh",
    "scripts/gate.sh": "scripts/gate.sh",
    "conftest.py": "conftest.py",
    "*/conftest.py": "scripts/tests/conftest.py",
}


def _declared_globs() -> list[str]:
    """The globs the SCRIPT declares, read from its array literal."""
    src = SCOPED.read_text()
    start = src.index("SHARED_SURFACE_GLOBS=(")
    body = src[start : src.index("\n)", start)]
    out = []
    for line in body.splitlines()[1:]:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        out.append(line.split("#", 1)[0].strip().strip("'\""))
    return [g for g in out if g]


def _run_scoped(repo: Path) -> subprocess.CompletedProcess:
    env = {**os.environ}
    # `--base HEAD` so the comparison is against the fixture's own commit and
    # the result cannot depend on how far the real origin/main has moved.
    return subprocess.run(
        ["bash", str(SCOPED), "--base", "HEAD", "--dry-run", str(repo)],
        capture_output=True, text=True, env=env, cwd=str(repo),
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A throwaway git repo shaped enough for the script to walk it."""
    r = tmp_path / "r"
    (r / "scripts" / "tests").mkdir(parents=True)
    (r / "scripts" / "lib").mkdir(parents=True)
    (r / "scripts" / "testlib").mkdir(parents=True)
    (r / "scripts" / "dl-router" / "tests").mkdir(parents=True)
    (r / "nix").mkdir(parents=True)
    (r / "scripts" / "dl-router" / "server.py").write_text("# server\n")
    # Must NAME the module it covers: the mapper selects test files that mention
    # what changed, so a test whose text never says `server.py` maps to nothing
    # and the negative control below would fail for a fixture reason rather than
    # a guard reason.
    (r / "scripts" / "dl-router" / "tests" / "test_server.py").write_text(
        '"""Covers scripts/dl-router/server.py."""\n\n\ndef test_server():\n    pass\n'
    )
    (r / "scripts" / "tests" / "test_placeholder.py").write_text(
        "def test_placeholder():\n    pass\n"
    )
    # 🔴 The script FATALs (exit 2) if no runner exists, BEFORE it reaches the
    # trigger check -- so without this stub every case here would fail on a
    # precondition and the guard would never be exercised. It is committed in
    # the BASE commit, so it is not itself part of any test's diff.
    # Never executed: every run below passes --dry-run.
    #
    # It must also answer `--check-targets`, which is how scoped-tests.sh reads
    # the declared target list -- deliberately NOT a second hardcoded copy, so a
    # runner that cannot answer makes the script refuse (exit 2) rather than map
    # everything to nothing. The two-space/`dir`/three-space shape is what the
    # script's `sed` extracts.
    runner = r / "scripts" / "run-tests.sh"
    runner.write_text(
        "#!/bin/sh\n"
        'for a in "$@"; do\n'
        '  if [ "$a" = "--check-targets" ]; then\n'
        '    printf "  dir   scripts/tests\\n"\n'
        '    printf "  dir   scripts/dl-router/tests\\n"\n'
        "    exit 0\n"
        "  fi\n"
        "done\n"
        "echo 'stub runner -- never executed under --dry-run'\n"
    )
    runner.chmod(0o755)
    subprocess.run(["git", "init", "-q"], cwd=r, check=True)
    subprocess.run(["git", "add", "-A"], cwd=r, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "base"],
        cwd=r, check=True,
    )
    return r


@pytest.mark.parametrize("glob,path", sorted(TRIGGER_CASES.items()))
def test_a_shared_surface_change_refuses_to_scope(repo: Path, glob: str, path: str):
    """🔴 POSITIVE CASES — every declared trigger, driven as a real diff."""
    f = repo / path
    f.parent.mkdir(parents=True, exist_ok=True)
    # 🔴 APPEND, never overwrite. One of these paths IS the stub runner, and
    # clobbering it makes `--check-targets` return nothing, so the script exits 2
    # on a precondition instead of 4 on the trigger -- a fixture artefact that
    # would read as the guard failing.
    with f.open("a") as fh:
        fh.write("\n# touched\n")

    res = _run_scoped(repo)

    assert res.returncode == 4, (
        f"a diff touching {path!r} (trigger {glob!r}) produced exit "
        f"{res.returncode}, not the refusal.\nstdout={res.stdout}\nstderr={res.stderr}"
    )
    assert "SHARED SURFACE" in res.stderr, (
        f"exit 4 was reached for {path!r}, but NOT via the shared-surface "
        f"refusal -- this may be the empty-mapping exit 4 instead, which is a "
        f"different reason and would make this test green for the wrong "
        f"cause.\nstderr={res.stderr}"
    )
    assert path in res.stderr, (
        f"the refusal did not NAME the offending path {path!r}, so an operator "
        f"cannot tell which file triggered it: {res.stderr}"
    )


def test_an_ordinary_subsystem_file_still_scopes_normally(repo: Path):
    """🔴 THE NEGATIVE CONTROL. A trigger list matching EVERYTHING would pass
    every case above; only this one can see that.

    An ordinary subsystem source file must still map to its own target's tests
    and produce a normal scoped selection -- NOT the refusal.
    """
    (repo / "scripts" / "dl-router" / "server.py").write_text("# changed\n")

    res = _run_scoped(repo)

    assert "SHARED SURFACE" not in res.stderr, (
        "an ordinary subsystem file tripped the shared-surface trigger, so the "
        f"trigger list is over-broad and scoping is now dead for everything: "
        f"{res.stderr}"
    )
    assert res.returncode == 0, (
        f"an ordinary file did not produce a normal scoped run: exit "
        f"{res.returncode}\nstdout={res.stdout}\nstderr={res.stderr}"
    )
    assert "selected" in res.stdout, res.stdout


def test_every_declared_trigger_is_exercised():
    """Two-way pin: a glob added to the script with no case here fails."""
    declared = set(_declared_globs())
    exercised = set(TRIGGER_CASES)
    assert declared == exercised, (
        "the script's SHARED_SURFACE_GLOBS and this module's TRIGGER_CASES "
        f"disagree.\n  only in the script: {sorted(declared - exercised)}\n"
        f"  only in this test:  {sorted(exercised - declared)}\n"
        "Every declared trigger needs a case proving it actually fires -- a "
        "glob nobody exercises is indistinguishable from one that never matches."
    )


def test_the_refusal_names_the_gate_to_run_instead():
    """A refusal that does not say what to do instead gets worked around."""
    src = SCOPED.read_text()
    assert "scripts/gate.sh --tier both" in src, (
        "the shared-surface refusal no longer tells the operator which command "
        "produces a real verdict; without it the refusal reads as a dead end."
    )
