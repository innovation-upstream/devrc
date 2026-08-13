"""Regression + invariant guards for `scripts/run-node-tests.sh`'s SUITE LIST.

WHY THIS EXISTS
---------------
`run-node-tests.sh` collected its files with ONE hard-coded glob::

    FILES=(scripts/browser-bridge/tests/*.test.mjs)

so every other `.test.mjs` directory in the repo was invisible to the only gate
that runs node. Measured 2026-08-03 on origin/main (13bc8bd):

  * ``scripts/dl-router/tests``             — 508 tests, **never gated**
  * ``scripts/collector/browser-ext/tests`` —  21 tests, **never gated**

529 tests that no CI run had ever executed, in a repo whose gate had already
been bitten by this exact shape three times (#276: 913 pytest tests behind one
list entry, #298: 166, #306: 989). The runner reported ``RESULT: PASS`` and a
468-test total the whole time, because a hard-coded list cannot know what it is
not looking at.

Collection is now DISCOVERY (bash globstar over ``<root>/**/*.test.mjs`` for
every entry of ``DISCOVERY_ROOTS``) plus a TWO-WAY PIN (``SUITES``), and this
file guards the pin.

2026-08-13: the ROOT LIST is the same defect one level up. Discovery was rooted
at ``scripts/`` alone, so ``claude/skills/clickup/test`` -- two hermetic gates
shipped through home-manager -- was invisible to it however many suites the glob
found under ``scripts/``. The roots are now an explicit ``DISCOVERY_ROOTS``
array, parsed out of the runner HERE rather than restated, and every root must
match at least one suite (a root that globs to nothing silently shrinks the gate
while still reporting PASS).

WHAT IS REGRESSION COVERAGE HERE AND WHAT IS NOT
------------------------------------------------
Per claude/RULES.md ("a guard that pins an invariant the bug never violated is
an INVARIANT GUARD -- label it as one, don't count it as regression coverage"):

  * ``test_every_test_mjs_directory_is_pinned`` is REGRESSION coverage, and is
    the direct statement of the bug. It is RED at origin/main: two directories
    hold ``.test.mjs`` files that the shipped SUITES list (which does not exist
    there, and whose predecessor named browser-bridge alone) does not cover.

  * ``test_check_suites_accepts_the_real_repo`` is REGRESSION coverage. RED at
    origin/main, where there is no ``--check-suites`` flag at all: the old arg
    parser has no case for it, so it is swallowed as ROOT and the run dies
    ``cannot cd to ROOT=--check-suites``.

  * ``test_dl_router_and_browser_ext_are_pinned`` is an INVARIANT GUARD. It pins
    that nobody "fixes" a future failure by deleting the entry -- the move the
    runner's own error message warns against. The bug never violated it (the
    entries did not exist), so it is not regression coverage.

  * the mutation tests are REACHABILITY proofs: they patch a COPY of the runner
    and assert it goes red **naming the offending directory**, so a green result
    from the real list means the guard can actually fire rather than being
    unreachable behind an earlier check.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
RUNNER = REPO_ROOT / "scripts" / "run-node-tests.sh"

# The two suites that were ungated until 2026-08-03, with the totals measured
# that day in the nix sandbox. Named literally so that a suite being dropped
# from SUITES fails HERE with its own reason, not as an anonymous count change.
FORMERLY_UNGATED = {
    "scripts/dl-router/tests": 508,
    "scripts/collector/browser-ext/tests": 21,
    # Ungated in a different way and for longer: it lived in an UNCOMMITTED
    # ~/.claude/skills/clickup/ on two hosts and ran only when a human typed it.
    # Measured 2026-08-13 once vendored into claude/skills/clickup/test.
    "claude/skills/clickup/test": 27,
}


def _require_check_suites() -> None:
    """Fail FAST, and for the right reason, if the runner predates the fix.

    Without this the pre-fix behaviour is not merely red but red SLOWLY: the old
    arg parser has no ``--check-suites`` case, so the flag falls through to
    ``*) ROOT="$1"`` and the runner tries to ``cd`` to it. Worse, when a ROOT is
    also supplied the flag is simply overwritten and the runner executes THE
    ENTIRE 997-test suite once per test. A capability check on the source turns
    that into an instant, named failure.
    """
    src = RUNNER.read_text()
    assert "--check-suites" in src, (
        f"{RUNNER} has no --check-suites flag, so the suite-discovery pin "
        "(GUARD 3) does not exist in this revision. This is the PRE-FIX state: "
        "collection was the single hard-coded glob "
        "`FILES=(scripts/browser-bridge/tests/*.test.mjs)`, leaving 529 tests "
        "in scripts/dl-router/tests and scripts/collector/browser-ext/tests "
        "ungated by any CI check."
    )


def _run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", *args],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=120,
    )


def _pinned_suites() -> dict[str, tuple[int, int]]:
    """Parse SUITES out of the runner: {dir: (min_files, min_tests)}.

    Deliberately parsed from the shell source rather than re-listed here -- a
    second hand-maintained copy of the list is how a list and its guard drift
    apart, which is the defect class this whole file is about.
    """
    src = RUNNER.read_text()
    m = re.search(r"^SUITES=\((.*?)^\)", src, re.S | re.M)
    assert m, (
        "could not find a SUITES=( ... ) block in run-node-tests.sh. If the "
        "array was renamed, update this parser -- do NOT delete the test, or "
        "the suite list goes back to being unguarded."
    )
    out: dict[str, tuple[int, int]] = {}
    for raw in m.group(1).splitlines():
        line = raw.strip().strip('"')
        if not line or line.startswith("#"):
            continue
        parts = line.split("|")
        assert len(parts) == 3, f"malformed SUITES entry: {line!r}"
        out[parts[0]] = (int(parts[1]), int(parts[2]))
    assert out, "parsed SUITES as EMPTY -- the parser is broken, not the list"
    return out


def _discovery_roots() -> list[str]:
    """Parse DISCOVERY_ROOTS out of the runner.

    Parsed, not restated, for the same reason SUITES is: a second hand-kept copy
    of the list drifts from the first, and the drift is silent -- this file would
    keep discovering under `scripts/` alone while the runner had moved on.
    """
    src = RUNNER.read_text()
    m = re.search(r"^DISCOVERY_ROOTS=\((.*?)^\)", src, re.S | re.M)
    assert m, (
        "could not find a DISCOVERY_ROOTS=( ... ) block in run-node-tests.sh. "
        "If the array was renamed, update this parser -- do NOT fall back to a "
        "hard-coded 'scripts', which is the blindness this array exists to fix."
    )
    roots = []
    for raw in m.group(1).splitlines():
        line = raw.strip().strip('"')
        if not line or line.startswith("#"):
            continue
        roots.append(line.strip('"'))
    assert roots, "parsed DISCOVERY_ROOTS as EMPTY -- the parser is broken, not the list"
    return roots


def _discovered_dirs() -> set[str]:
    """Every directory under a DISCOVERY_ROOT holding a .test.mjs file.

    Uses pathlib rather than shelling out to `find`: the `find` on this host's
    bash PATH is BUSYBOX and rejects GNU's `-printf` (that exact trap produced a
    silently EMPTY discovery list while building this change), and the runner
    itself now uses bash globstar for the same portability reason. A second,
    independently-implemented discovery here is the point -- it cross-checks the
    runner's own rather than restating it.
    """
    out: set[str] = set()
    for root in _discovery_roots():
        base = REPO_ROOT / root
        if not base.is_dir():
            continue
        out |= {
            str(p.parent.relative_to(REPO_ROOT)) for p in base.rglob("*.test.mjs")
        }
    return out


# --------------------------------------------------------------------------
# Guard the guard: both harness halves must actually see something.
# --------------------------------------------------------------------------

def test_parsers_are_positive_controlled():
    """POSITIVE CONTROL for the two parsers this file rests on.

    Every assertion below compares a parsed set against a discovered set. If
    EITHER quietly stopped matching, the comparisons would run over empty sets
    and pass vacuously -- a harness reporting success while testing nothing, and
    the reassuring answer here ("no unpinned directories") is exactly the zero
    that an unwired harness also produces. So pin that both are non-empty and
    plausibly sized before believing any result derived from them.
    """
    pinned = _pinned_suites()
    discovered = _discovered_dirs()
    roots = _discovery_roots()
    assert len(pinned) >= 4, f"only {len(pinned)} suites parsed: {pinned}"
    assert len(discovered) >= 4, f"only {len(discovered)} dirs discovered: {discovered}"
    # Every pinned suite must sit under a DECLARED root, or discovery could never
    # have found it -- the pin would be pointing somewhere the glob does not look.
    for d in pinned:
        assert any(d.startswith(f"{r}/") for r in roots), (
            f"{d} is pinned in SUITES but lies under none of DISCOVERY_ROOTS "
            f"({roots}), so discovery cannot reach it."
        )
    # The floors must be real numbers, not a parse artefact of zeros.
    assert all(mt > 0 and mf > 0 for mf, mt in pinned.values()), pinned


def test_every_discovery_root_contributes_a_suite():
    """A root that matches nothing is an EMPTY RESULT wearing a root's clothes.

    It is indistinguishable from a typo or a moved directory, and both read as
    "no suites here" while the gate keeps reporting PASS on the other roots. The
    runner enforces this itself; this pins the property from the outside, with a
    second implementation of discovery.
    """
    discovered = _discovered_dirs()
    for root in _discovery_roots():
        assert any(d.startswith(f"{root}/") for d in discovered), (
            f"DISCOVERY_ROOTS names {root!r} but no *.test.mjs was found under it. "
            "A root globbing to nothing silently shrinks the gate."
        )


# --------------------------------------------------------------------------
# REGRESSION: the bug itself.
# --------------------------------------------------------------------------

def test_every_test_mjs_directory_is_pinned():
    """RED at origin/main. Every .test.mjs directory must be gated.

    This is the whole defect in one assertion: on origin/main the runner ran
    scripts/browser-bridge/tests and nothing else, so dl-router's 508 tests and
    browser-ext's 21 were collected by no gate at all.
    """
    pinned = set(_pinned_suites())
    discovered = _discovered_dirs()
    unpinned = discovered - pinned
    assert not unpinned, (
        f"{len(unpinned)} directory/ies hold .test.mjs files that no gate runs: "
        f"{sorted(unpinned)}. Add each to SUITES in scripts/run-node-tests.sh "
        "with a measured floor. This is the #276/#298/#306 shape: a suite that "
        "exists, passes locally, and is gated by nothing."
    )


def test_no_pinned_suite_has_vanished():
    """The other direction: a pinned suite must still exist.

    Without this, deleting or renaming a suite directory would quietly shrink
    the gate -- the failure mode a discovery-only collection step reintroduces.
    """
    pinned = set(_pinned_suites())
    discovered = _discovered_dirs()
    missing = pinned - discovered
    assert not missing, (
        f"SUITES pins {sorted(missing)} but no .test.mjs was found there. "
        "If the suite genuinely moved, update SUITES; do not drop the entry."
    )


def test_check_suites_accepts_the_real_repo():
    """RED at origin/main (no --check-suites), GREEN after the fix."""
    _require_check_suites()
    proc = _run([str(RUNNER), "--check-suites", str(REPO_ROOT)])
    assert proc.returncode == 0, (
        "run-node-tests.sh --check-suites rejected the shipped suite list.\n"
        f"exit={proc.returncode}\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )


def test_dl_router_and_browser_ext_are_pinned():
    """INVARIANT GUARD (not regression coverage).

    Pins the two suites this change rescued, by name, so they cannot be dropped
    from the gate as a way of turning a future red green -- which is exactly
    what the runner's own error text warns against.
    """
    pinned = _pinned_suites()
    for suite, measured in FORMERLY_UNGATED.items():
        assert suite in pinned, (
            f"{suite} is no longer in SUITES. It held {measured} tests that no "
            "gate ran until 2026-08-03. If it genuinely moved, update SUITES "
            "and this constant; do not simply drop the entry."
        )
        _, min_tests = pinned[suite]
        assert min_tests > measured * 0.9, (
            f"{suite}'s floor ({min_tests}) has drifted well below its measured "
            f"total ({measured}). A floor under ~90% of reality stops being able "
            "to detect the collapse it exists for -- the pytest global floor was "
            "allowed to fall below HALF before #306 caught it."
        )


def test_check_suites_runs_no_tests():
    """--check-suites must stay cheap.

    If it ever started executing node, the mutation tests below would take
    minutes each and would be disabled by the first person who noticed.
    """
    _require_check_suites()
    proc = _run([str(RUNNER), "--check-suites", str(REPO_ROOT)])
    assert "=== node --test " not in proc.stdout, (
        f"--check-suites ran a suite; it validates the list only.\n{proc.stdout}"
    )


# --------------------------------------------------------------------------
# REACHABILITY: prove GUARD 3 can go red, naming the offending directory.
# --------------------------------------------------------------------------

def _runner_copy(
    tmp_path: Path,
    *,
    drop: str | None = None,
    add: str | None = None,
    drop_root: str | None = None,
    add_root: str | None = None,
) -> Path:
    """Copy the runner, optionally mutating one SUITES or DISCOVERY_ROOTS entry."""
    src = RUNNER.read_text()
    if drop_root is not None:
        src, n = re.subn(
            rf'^\s*"{re.escape(drop_root)}"\s*$\n', "", src, count=1, flags=re.M
        )
        assert n == 1, f"failed to drop root {drop_root!r} from the copied runner"
    if add_root is not None:
        src, n = re.subn(
            r"^DISCOVERY_ROOTS=\(\n", f'DISCOVERY_ROOTS=(\n  "{add_root}"\n', src, count=1, flags=re.M
        )
        assert n == 1, "failed to inject a discovery root into the copied runner"
    if drop is not None:
        patched, n = re.subn(
            rf'^\s*"{re.escape(drop)}\|\d+\|\d+"\s*$\n', "", src, count=1, flags=re.M
        )
        assert n == 1, f"failed to drop {drop!r} from the copied runner"
        src = patched
    if add is not None:
        patched, n = re.subn(r"^SUITES=\(\n", f'SUITES=(\n  "{add}|1|1"\n', src, count=1, flags=re.M)
        assert n == 1, "failed to inject a suite into the copied runner"
        src = patched
    dst = tmp_path / "run-node-tests.sh"
    dst.write_text(src)
    return dst


@pytest.mark.parametrize("victim", sorted(FORMERLY_UNGATED))
def test_dropping_a_pinned_suite_is_loud(tmp_path, victim):
    """MUTATION: remove a suite from the pin and the guard must name it.

    This is the answer to "if the dl-router node tests silently stopped
    executing, would anything go red?" -- and it asserts the guard fails with
    ITS OWN reason (the directory named, "NOT in SUITES"), not merely that the
    run was non-zero, which an unrelated guard tripping first would also satisfy.
    """
    _require_check_suites()
    runner = _runner_copy(tmp_path, drop=victim)
    proc = _run([str(runner), "--check-suites", str(REPO_ROOT)])
    out = proc.stdout + proc.stderr
    assert proc.returncode == 2, (
        f"dropping {victim} from SUITES did NOT fail the guard (exit "
        f"{proc.returncode}) -- the pin is inert.\n{out}"
    )
    assert victim in out, f"guard failed but never named {victim!r}.\n{out}"
    assert "NOT in SUITES" in out, (
        f"guard named {victim!r} but not WHY (expected the unpinned-suite "
        f"reason, so this test cannot be satisfied by a different guard).\n{out}"
    )


def test_pinning_a_nonexistent_suite_is_loud(tmp_path):
    """MUTATION, the other direction: a pinned suite that does not exist."""
    _require_check_suites()
    ghost = "scripts/does-not-exist/tests"
    runner = _runner_copy(tmp_path, add=ghost)
    proc = _run([str(runner), "--check-suites", str(REPO_ROOT)])
    out = proc.stdout + proc.stderr
    assert proc.returncode == 2, (
        f"a pinned-but-absent suite did NOT fail the guard (exit "
        f"{proc.returncode}).\n{out}"
    )
    assert ghost in out, f"guard failed but never named {ghost!r}.\n{out}"
    assert "discovery found no" in out, (
        f"guard named {ghost!r} but not WHY (expected the vanished-suite "
        f"reason).\n{out}"
    )


def test_runner_does_not_use_find_printf():
    """Portability pin, measured rather than assumed.

    The first draft discovered suites with `find -printf`, which is a GNU
    extension. THREE different `find`s are reachable from this repo -- busybox
    under bash (~/.nix-profile/bin/find, no -printf), bfs 4.1.1 under the
    interactive zsh, and GNU findutils in the nix sandbox. Only two accept it,
    and the draft paired it with `2>/dev/null`, so on the dev host discovery
    returned an EMPTY list with no error: a false "no suites found" that the
    two-way pin caught only by accident of failing in the other direction.
    """
    # Comment lines are stripped first: the header DESCRIBES the trap, and a
    # naive substring search over the whole file matches that prose and reports
    # a defect that is not there. (Caught while writing this test -- the same
    # "your pattern matched the wrong thing" class the check itself is about.)
    code = "\n".join(
        line for line in RUNNER.read_text().splitlines() if not line.lstrip().startswith("#")
    )
    assert "-printf" not in code, (
        "run-node-tests.sh uses `find -printf`, a GNU extension that the "
        "busybox `find` on this host's bash PATH rejects. Use bash globstar "
        "(a builtin, identical in every tier) instead."
    )
    # POSITIVE CONTROL: the stripper must not have eaten the whole file, or the
    # assertion above passes vacuously on an empty string.
    assert "SUITES=(" in code and len(code) > 2000, (
        "comment-stripping left no executable shell behind -- this check would "
        "pass against anything. Fix the stripper, not the assertion."
    )


def test_dropping_a_discovery_root_is_loud(tmp_path):
    """MUTATION: remove `claude` from DISCOVERY_ROOTS and the pin must notice.

    This is the reachability proof for the ROOT list, which is a separate guard
    from the SUITES list: with the root gone, discovery simply stops looking at
    claude/ and -- before the two-way pin -- the run would have gone green having
    quietly dropped a whole suite. It must fail NAMING the vanished suite, not
    merely exit non-zero, so an unrelated guard tripping first cannot satisfy it.
    """
    _require_check_suites()
    roots = _discovery_roots()
    assert "claude" in roots, f"expected `claude` in DISCOVERY_ROOTS, got {roots}"
    runner = _runner_copy(tmp_path, drop_root="claude")
    proc = _run([str(runner), "--check-suites", str(REPO_ROOT)])
    out = proc.stdout + proc.stderr
    assert proc.returncode == 2, (
        f"dropping the `claude` discovery root did NOT fail the guard (exit "
        f"{proc.returncode}) -- the clickup suite would silently stop running.\n{out}"
    )
    assert "claude/skills/clickup/test" in out, (
        f"guard failed but never named the suite that vanished with the root.\n{out}"
    )
    assert "discovery found no" in out, (
        f"guard fired for some OTHER reason than the suite vanishing.\n{out}"
    )


def test_a_discovery_root_that_matches_nothing_is_loud(tmp_path):
    """MUTATION, the other direction: a root that globs to nothing.

    An empty root is the "EMPTY RESULT cannot distinguish two mechanisms" trap --
    a typo and a deleted directory produce the identical silence. The runner must
    refuse rather than quietly gate fewer suites.
    """
    _require_check_suites()
    ghost = "no-such-root"
    runner = _runner_copy(tmp_path, add_root=ghost)
    proc = _run([str(runner), "--check-suites", str(REPO_ROOT)])
    out = proc.stdout + proc.stderr
    assert proc.returncode == 2, (
        f"a DISCOVERY_ROOTS entry matching nothing did NOT fail the guard (exit "
        f"{proc.returncode}).\n{out}"
    )
    assert ghost in out, f"guard failed but never named {ghost!r}.\n{out}"
    assert "matched no *.test.mjs" in out, (
        f"guard named {ghost!r} but not WHY (expected the empty-root reason).\n{out}"
    )
