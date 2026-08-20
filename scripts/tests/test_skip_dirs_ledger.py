"""🔴 SEAM GUARD: every repo-wide walker's EFFECTIVE skip set, pinned two-way.

WHY
---
Four scanners walk this repo's filesystem and each one used to carry its own
hand-written skip set. Measured 2026-08-20, they disagreed:

    public_ip_scan / client_host_scan   9 entries, HAD .pytest_cache
    test_claude_sessions                7 entries, MISSING it
    test_clawgate_predicate_single_...  6 entries, MISSING it

so an ordinary `pytest` run wrote `.pytest_cache/v/cache/nodeids` into the tree
and `test_claude_sessions` went red on it. `claude/RULES.md` -> "One rule, one
place": a predicate open-coded at N sites is wrong at N-1 of them, in the same
direction. Consolidating without pinning just resets the clock.

WHAT THIS PINS, AND WHY IT IS THE RELATIONSHIP AND NOT THE COMPONENT
--------------------------------------------------------------------
Consolidation creates a hazard it did not have before: the four sites now share
a base, so an edit to that base moves ALL FOUR. Two directions, both bad:

  WIDER  -- a name added to the base BLINDS the two security gates. This repo is
            PUBLIC and `public_ip_scan` / `client_host_scan` are what stop a real
            address or client hostname reaching it. Unioning the four sets --
            the obvious refactor -- would have pulled `.claude` and `claudedocs`
            into both, deleting 98 committed prose files from their view while
            every test stayed green.
  NARROWER-- a name removed re-opens the red gate above.

So the pin is on each scanner's EFFECTIVE set -- the exact frozen value it uses
at runtime -- not on the shared base. A structural check that "they all import
skip_dirs" would type-check right past a wrong operand. `claude/RULES.md` ->
"Verified in isolation is the new vacuous green": the defect lives in the seam.

Every assertion below names the pinned set literally, so a reword cannot walk
it and a mutation must move a number this file spells.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

import test_claude_sessions as CS  # noqa: E402
import test_clawgate_predicate_single_source as CP  # noqa: E402
from testlib import client_host_scan as H  # noqa: E402
from testlib import public_ip_scan as P  # noqa: E402
from testlib import skip_dirs  # noqa: E402

#: The shared base, spelled out. Any edit to `skip_dirs.GENERATED` must move
#: this literal too, which is what forces the four effective sets below to be
#: re-read rather than assumed.
BASE = {
    ".git", ".direnv", "result", "node_modules", "__pycache__",
    ".pytest_cache", ".mypy_cache", ".ruff_cache", "worktrees",
}

VENVS = {".venv", "venv"}

#: 🔴 THE LEDGER. scanner -> its exact effective skip set.
#:
#: The two `testlib/` entries are the SECURITY gates and get the base alone.
#: They are spelled as `BASE` with no additions on purpose: reading this table
#: must make it obvious that neither of them skips `.claude` or `claudedocs`.
#:
#: The two `scripts/tests/` entries are content LEDGERS with no `git ls-files`
#: tier, so they read raw disk and additionally skip a virtualenv and the
#: per-host `.claude` state.
EFFECTIVE = {
    "testlib/public_ip_scan.SKIP_DIRS":                    BASE,
    "testlib/client_host_scan.SKIP_DIRS":                  BASE,
    "test_claude_sessions._SKIP_DIRS":                     BASE | VENVS | {".claude", "claudedocs"},
    "test_clawgate_predicate_single_source.SKIP_DIRS":     BASE | VENVS | {".claude"},
}

LIVE = {
    "testlib/public_ip_scan.SKIP_DIRS":                    P.SKIP_DIRS,
    "testlib/client_host_scan.SKIP_DIRS":                  H.SKIP_DIRS,
    "test_claude_sessions._SKIP_DIRS":                     CS._SKIP_DIRS,
    "test_clawgate_predicate_single_source.SKIP_DIRS":     CP.SKIP_DIRS,
}


def test_the_shared_base_is_exactly_what_this_ledger_says():
    """`skip_dirs.GENERATED` cannot move without this literal moving with it."""
    assert set(skip_dirs.GENERATED) == BASE, (
        "skip_dirs.GENERATED changed.\n  pinned: %s\n  actual: %s\n"
        "Adding a name here WIDENS all four scanners at once, including two "
        "security gates on a PUBLIC repo. Removing one can re-open the "
        "permanently-red-gate bug. Update this literal only with the reason."
        % (sorted(BASE), sorted(skip_dirs.GENERATED)))
    assert set(skip_dirs.VIRTUALENVS) == VENVS, (
        "skip_dirs.VIRTUALENVS changed: pinned %s, actual %s"
        % (sorted(VENVS), sorted(skip_dirs.VIRTUALENVS)))


def test_every_scanners_effective_skip_set_is_pinned_two_way():
    """Each walker's runtime value, asserted whole. Not a subset check."""
    assert set(LIVE) == set(EFFECTIVE), (
        "the scanner ledger and the live table disagree about WHICH scanners "
        "exist: %s" % sorted(set(LIVE) ^ set(EFFECTIVE)))
    for name, expected in sorted(EFFECTIVE.items()):
        actual = set(LIVE[name])
        assert actual == expected, (
            "%s changed its effective skip set.\n"
            "  extra (a NEW BLIND SPOT):  %s\n"
            "  missing (a REOPENED GAP):  %s\n"
            "Every skip entry is a directory this scanner can no longer see. "
            "Add one only with a reason, in skip_dirs.py or at the site."
            % (name, sorted(actual - expected), sorted(expected - actual)))


def test_the_security_gates_never_inherit_the_ledgers_additions():
    """🔴 The consolidation hazard, asserted as a RELATIONSHIP.

    `claudedocs/` is committed prose on a PUBLIC repo and is one of the
    likeliest places a real address or client hostname gets written down;
    `.claude/` is per-host state that a fallback walk can reach. Neither may
    ever become invisible to the two gates that exist to catch that. This fails
    for ANY future addition too, not just these two names: the security gates'
    sets must stay a strict subset of the base.
    """
    for gate, live in (("public_ip_scan", P.SKIP_DIRS),
                       ("client_host_scan", H.SKIP_DIRS)):
        for blinding in ("claudedocs", ".claude"):
            assert blinding not in live, (
                "%s now skips %r — it is a security gate on a PUBLIC repo and "
                "that directory holds committed content it must read"
                % (gate, blinding))
        assert set(live) <= BASE, (
            "%s gained skip entries beyond the shared base: %s. A security "
            "gate widens only by an explicit, reasoned edit to THIS test."
            % (gate, sorted(set(live) - BASE)))


def test_pytest_cache_is_skipped_by_every_walker():
    """🔴 THE REGRESSION. Red at the parent of #582, green here.

    An ordinary `pytest` run writes `.pytest_cache/v/cache/nodeids`, a JSON list
    of every collected test node id — so it names, verbatim, every module and
    function in this repo. Any content ledger that walks it reads its own
    trigger tokens back out of a file the developer never wrote. Named
    separately from the table above so the failure message says WHY.
    """
    for name, live in sorted(LIVE.items()):
        assert ".pytest_cache" in live, (
            "%s does not skip .pytest_cache — running plain `pytest` will red "
            "this gate on a generated artefact" % name)


def test_the_skip_sets_actually_reject_a_generated_path():
    """POSITIVE + NEGATIVE CONTROL for the predicate itself, not just the set.

    A pinned set proves nothing if the matcher that consumes it is wrong. Every
    consumer applies `any(part in SKIP_DIRS for part in <relative>.parts)`, so
    exercise that shape: a path INSIDE a skipped directory must be rejected at
    every depth, and an ordinary source path must NOT be.
    """
    skipped = Path(".pytest_cache/v/cache/nodeids")
    nested = Path("scripts/browser-bridge/tests/.pytest_cache/v/cache/nodeids")
    kept = Path("scripts/lib/claude_sessions.py")
    for name, live in sorted(LIVE.items()):
        assert any(p in live for p in skipped.parts), name
        assert any(p in live for p in nested.parts), name
        assert not any(p in live for p in kept.parts), (
            "%s would skip %s — the scan is walking nothing" % (name, kept))
