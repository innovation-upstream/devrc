"""A hook test suite must never touch the `$HOME` it inherits.

THE DEFECT (measured on origin/main, 3c54918):

    scripts/claude-hooks/tests/test_search_tool_nudge.py computed
    `HOME = os.path.expanduser("~")` at import and derived
    `STATE_ROOT = $HOME/.cache/claude-search-tool-nudge/s` from it. It then
    GLOBBED AND DELETED under that path, and drove the hook as a real
    subprocess once per scenario in the file with that same real `$HOME`
    inherited, so every child resolved the identical directory.

    Two consequences, both real:

      * it deleted entries from the operator's own nudge state, and
      * the LIVE search-tool-nudge hook fires on every Bash call of every Claude
        Code session on the box — including the session running the gate — so an
        agent's own commands mutated the state the suite was asserting on, mid
        run. The suite failed spuriously and the failure looked like a code bug.

    test_shell_env_nudge.py had the same shape (it created and removed
    `~/.cache/claude-shell-env-nudge/<sid>`).

WHAT EACH TEST HERE IS (labelled, because "it passes" is not a category):

  * test_the_suite_does_not_touch_the_home_it_inherits
        REGRESSION. Red at 3c54918, green at HEAD, for both suites — and red on
        the DELETION, which is the half a create-only check cannot see.
  * test_the_suite_does_write_state_under_its_own_temp_home
        POSITIVE CONTROL for the test above. A suite that silently stopped
        driving the hook would ALSO leave the inherited home untouched — the
        same reassuring zero as a harness wired to nothing. This reads the count
        the suite prints for its own throwaway HOME and requires it non-zero, so
        the pair is always reported together.
  * test_every_subprocess_env_in_an_isolated_suite_derives_from_os_environ
        SEAM / LEDGER guard, not regression coverage. The isolation works by
        mutating `os.environ`, which only reaches a child that inherits it or
        builds its env from it. A future spawn with a hand-assembled `env=`
        would reintroduce the leak with every existing assertion still green.
        This reads the suites' own source and fails when one appears.

🔴 This file never touches the operator's real home. Each suite is run with
`HOME` pointed at a fresh tmp_path seeded with decoys, standing in for the real
one: the assertion is "the suite left the home it was GIVEN exactly as it found
it", which is the same property, measured somewhere harmless. Nothing here
stats, creates or reads `~/.cache/*`.
"""
import ast
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
HOOK_TESTS = REPO_ROOT / "scripts" / "claude-hooks" / "tests"

# The script suites that redirect `$HOME` for themselves. Named explicitly and
# pinned by `test_the_ledger_names_only_files_that_exist` so a rename cannot
# silently drop one out of coverage.
ISOLATED_SUITES = (
    "test_search_tool_nudge.py",
    "test_shell_env_nudge.py",
)

# The line each isolated suite prints with the count it measured under its OWN
# throwaway HOME. Pinned as a whole normalised shape, not a keyword: a guard on
# the word "isolation" alone is walkable by any other line that spells it.
ISOLATION_LINE = re.compile(r"^isolation: (\d+) files under the temp HOME$", re.M)

# DECOYS — pre-existing state planted in the stand-in home before the suite runs.
#
# 🔴 Without these the guard could only see the suite CREATING things, and the
# other half of the defect is that both suites DELETE: search-tool-nudge globs
# `$HOME/.cache/claude-search-tool-nudge/s/<prefix>*` and rmtree's each hit (plus
# a legacy flat-file layout one directory up), and shell-env-nudge `os.remove`s
# its cache entry. A destructive run that tidies up after itself leaves an
# end-state diff of zero, so deletion is invisible to a create-only check. Each
# decoy is a path the PRE-CHANGE suite provably removes; they must all survive.
DECOYS = {
    "test_search_tool_nudge.py": (
        # matches TEST_SID_PREFIXES -> rmtree'd by the pre-change clear_test_state
        ".cache/claude-search-tool-nudge/s/test-session-search-nudge-DECOY/content",
        # the LEGACY flat-file layout, one directory up -> os.remove'd
        ".cache/claude-search-tool-nudge/test-search-nudge-DECOY-legacy",
    ),
    "test_shell_env_nudge.py": (
        # the exact cache entry the pre-change suite removes, twice
        ".cache/claude-shell-env-nudge/test-session-io-DO-NOT-COLLIDE",
    ),
}
# Planted for every suite: a bystander no cleanup glob names, so a guard that
# went red only because a decoy was deliberately collidable still has one path
# whose survival means "ordinary home content was left alone".
BYSTANDER = ".cache/an-unrelated-file-the-suite-must-not-touch"

_TIMEOUT = 900


def _run_suite(suite: str, home: Path):
    """Run one script suite with `$HOME` pointed at `home`.

    `home` is a throwaway directory standing in for the operator's real one, so
    a suite that leaks writes there is caught without any real state at risk.
    """
    env = dict(os.environ)
    env["HOME"] = str(home)
    return subprocess.run(
        [sys.executable, str(HOOK_TESTS / suite)],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        env=env,
        timeout=_TIMEOUT,
    )


def _tree(root: Path):
    """Every path under `root`, relative — WITHOUT creating `root`.

    `os.walk` on a missing directory yields nothing rather than raising or
    creating anything, which is the property that keeps this safe to point at a
    path that must not come into existence.
    """
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        for name in list(dirnames) + list(filenames):
            out.append(str(Path(dirpath, name).relative_to(root)))
    return sorted(out)


def _plant(home: Path, relpaths):
    """Create each `relpath` under `home` as a file with known content."""
    for rel in relpaths:
        p = home / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("decoy — the suite under test must not touch this\n")


@pytest.fixture(scope="module")
def suite_runs(tmp_path_factory):
    """Run each suite ONCE, against its own stand-in home seeded with decoys.

    Module-scoped because the tests below ask different questions of the same
    run, and re-running would double the gate's cost for no new evidence.
    """
    runs = {}
    for suite in ISOLATED_SUITES:
        home = tmp_path_factory.mktemp("stand-in-home-" + suite.replace(".py", ""))
        _plant(home, DECOYS[suite] + (BYSTANDER,))
        before = _tree(home)
        proc = _run_suite(suite, home)
        runs[suite] = (proc, before, _tree(home))
    return runs


# ---------------------------------------------------------------------------
# REGRESSION
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("suite", ISOLATED_SUITES)
def test_the_suite_does_not_touch_the_home_it_inherits(suite, suite_runs):
    """RED at 3c54918, GREEN at HEAD.

    Asserted in BOTH directions — created AND removed — because the two suites
    do both, and a create-only check is blind to a destructive run that tidies
    up after itself. The decoys planted by the fixture are what make the removal
    half measurable at all.

    Asserted on the FULL tree, not just `.cache`: the hazard is "touches the
    operator's home", and naming one subdirectory would let the next state path
    a hook invents walk straight past this guard.
    """
    proc, before, after = suite_runs[suite]
    assert proc.returncode == 0, (
        f"{suite} failed before this guard could measure anything "
        f"(exit={proc.returncode}).\n--- stdout ---\n{proc.stdout}\n"
        f"--- stderr ---\n{proc.stderr}"
    )
    # The decoys must have been planted, or "unchanged" is a comparison against
    # an absent operand and reports SAME for a home holding nothing.
    for rel in DECOYS[suite] + (BYSTANDER,):
        assert rel in before, f"decoy {rel} was never planted; before={before}"

    created = sorted(set(after) - set(before))
    removed = sorted(set(before) - set(after))
    assert (created, removed) == ([], []), (
        f"{suite} modified the $HOME it inherited:\n"
        f"  created: {json.dumps(created[:20], indent=2)}\n"
        f"  REMOVED: {json.dumps(removed[:20], indent=2)}\n"
        "Run against the operator's real home that is their own hook state being "
        "created and deleted — and the LIVE hook writing into the state the suite "
        "asserts on. Redirect `os.environ['HOME']` to a tempfile.mkdtemp BEFORE the "
        "suite computes anything from `~` (including its import of the hook)."
    )


# ---------------------------------------------------------------------------
# POSITIVE CONTROL for the regression above
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("suite", ISOLATED_SUITES)
def test_the_suite_does_write_state_under_its_own_temp_home(suite, suite_runs):
    """The other half of the pair — a ZERO alone is not evidence.

    A suite that stopped exercising the hook, or whose subprocesses all failed
    silently, would leave the inherited home untouched too. So the count the
    suite measured under its OWN throwaway HOME must be non-zero, and the two
    numbers are reported together: "N under the temp HOME, 0 in the inherited
    one".
    """
    proc, before, after = suite_runs[suite]
    m = ISOLATION_LINE.search(proc.stdout)
    assert m, (
        f"{suite} printed no `isolation: N files under the temp HOME` line, so "
        "there is no positive control and the zero above proves nothing.\n"
        f"--- stdout ---\n{proc.stdout}"
    )
    written = int(m.group(1))
    assert written > 0, (
        f"{suite} wrote NOTHING under its own temp HOME. The inherited home being "
        "clean is then indistinguishable from a suite wired to nothing — it is not "
        "evidence of isolation."
    )
    # Report the pair, so the passing message carries its own scope. The second
    # number is the CHANGE to the inherited home (created + removed), never the
    # size of that tree — it holds the fixture's decoys, which must all survive.
    changed = len(set(after) ^ set(before))
    print(f"{suite}: {written} files under the temp HOME, "
          f"{changed} changes to the inherited HOME")


# ---------------------------------------------------------------------------
# SEAM / LEDGER
# ---------------------------------------------------------------------------

def _spawn_env_sources(path: Path):
    """Source text of every `env=` kwarg passed to subprocess.run/Popen in `path`.

    Read with `ast`, not imported: importing a script suite runs it.
    """
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src, filename=str(path))
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", None)
        if name not in {"run", "Popen", "call", "check_output", "check_call"}:
            continue
        for kw in node.keywords:
            if kw.arg == "env":
                found.append(ast.get_source_segment(src, kw.value) or ast.dump(kw.value))
    return found


def test_the_ledger_names_only_files_that_exist():
    """Positive control for the two tests above and the seam guard below.

    Every one of them is parametrized over `ISOLATED_SUITES`; a typo'd or
    renamed entry would make each parametrisation fail loudly, but an entry
    silently REMOVED would make the whole file measure less while staying green.
    Pinned here as a set so the coverage cannot shrink unnoticed.
    """
    assert len(ISOLATED_SUITES) >= 2
    for suite in ISOLATED_SUITES:
        assert (HOOK_TESTS / suite).is_file(), f"{suite} is not in {HOOK_TESTS}"


@pytest.mark.parametrize("suite", ISOLATED_SUITES)
def test_every_subprocess_env_in_an_isolated_suite_derives_from_os_environ(suite):
    """SEAM guard — NOT regression coverage.

    The isolation is `os.environ["HOME"] = <tmp>`, which reaches a child only if
    the child inherits `os.environ` (no `env=` at all) or the env is BUILT from
    it (`dict(os.environ, …)` / `{**os.environ, …}`). A spawn with a
    hand-assembled env would silently hand the hook the real `$HOME` back while
    every behavioural assertion in the suite stayed green — the leak is in the
    seam, not in either component.
    """
    envs = _spawn_env_sources(HOOK_TESTS / suite)
    offenders = [e for e in envs if "os.environ" not in e]
    assert not offenders, (
        f"{suite} spawns a subprocess with an env not derived from os.environ: "
        f"{offenders}\nThat child gets the operator's REAL $HOME back. Build it "
        "as dict(os.environ, …) or {**os.environ, …}, or pass no env= at all."
    )


def test_the_env_scanner_can_actually_see_an_offender(tmp_path):
    """Positive control for the scanner itself.

    `offenders == []` above is exactly the reassuring zero a parser wired to
    nothing returns — a renamed call, a changed AST shape, a wrong attribute
    name all produce it. So the same function is fed a file it MUST flag, and
    one it must NOT, and both answers are pinned.
    """
    bad = tmp_path / "bad.py"
    bad.write_text(
        "import subprocess, sys\n"
        "subprocess.run([sys.executable], env={'HOME': '/real/home'})\n"
    )
    assert _spawn_env_sources(bad) == ["{'HOME': '/real/home'}"]

    good = tmp_path / "good.py"
    good.write_text(
        "import os, subprocess, sys\n"
        "subprocess.run([sys.executable], env=dict(os.environ, X='1'))\n"
        "subprocess.Popen([sys.executable])\n"
    )
    assert [e for e in _spawn_env_sources(good) if "os.environ" not in e] == []
