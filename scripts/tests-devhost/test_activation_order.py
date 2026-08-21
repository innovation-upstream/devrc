"""The activation ORDER, measured from the evaluated DAG rather than from prose.

🔴 WHY THIS DIRECTORY EXISTS AT ALL, and why this file is not in scripts/tests.

`home.activation.reclaimManagedPaths` DELETES files and relies on the very next
step relinking them. Between its `rm` and linkGeneration's `ln` the reclaimed
files exist NOWHERE on disk, so every activation step inside that window can
abort the switch and strand them deleted-and-unlinked — strictly worse than the
bug being repaired. The entry is therefore declared
`lib.hm.dag.entryBetween ["linkGeneration"] ["installPackages"]`, and the
property that matters is not how it is SPELLED but the measured result:

    … checkFilesChanged, espansoConfigDir, installPackages,
      reclaimManagedPaths, linkGeneration …          -> ZERO steps in the window

The structural pin in `scripts/tests/test_reclaim_managed_paths.py` reads
`nix/home.nix` as TEXT. It can see the spelling and nothing else, and for a
while its own docstring — and nix/home.nix's comment, and the commit message —
claimed it "reads the BUILT script". It never has. So the zero-steps property
was pinned by nothing, and it survived BOTH of its real failure modes:

  * a NEW devrc activation entry landing inside the window. The spelling of
    reclaimManagedPaths is unchanged, so the text pin stays green.
  * home-manager RENAMING `installPackages` or `linkGeneration`. `topoSort`
    silently IGNORES an unknown name in `before`/`after` (see home-manager's
    `modules/lib/dag.nix`) rather than erroring, so the entry loses that bound
    and drifts — again with the text pin green, because the text still spells
    the name that no longer exists.

This file closes both by asking NIX. It evaluates the flake's real
`home.activation` DAG and runs it through home-manager's OWN `lib.dag.topoSort`
— the same function that orders the activation script — and asserts the
property on the RESULT.

🔴 IT CANNOT BE HERMETIC, and pretending otherwise is the failure mode this repo
has already removed twice (see run-tests.sh's EXPECTED_SKIPS block). Evaluating
the flake needs nixpkgs and home-manager out of the store plus `nix-command`;
the nix BUILD SANDBOX has a `nix` binary with `nix-command` disabled and none of
those inputs, so the eval errors there. A guard that means one thing on a dev
host and NOTHING in the tier that gates merges is worse than no guard. So this
is a DEV-HOST target: `run-tests.sh --set all` runs it, which is what
`githooks/tests-on-push.sh` runs on every push, and it FAILS rather than skips
when it cannot measure.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

# The step this PR added, and the two bounds it is declared between.
ENTRY = "reclaimManagedPaths"
LOWER = "installPackages"
UPPER = "linkGeneration"

# A real home-manager configuration has well over this many activation steps
# (16 measured on the workbench, 2026-08-21). The floor is the positive control
# for the READER: an eval that returned `{}` or a one-element list would satisfy
# every "nothing is in the window" assertion below by having no window at all.
MIN_PLAUSIBLE_ENTRIES = 8

_EXPR = """
let
  f = builtins.getFlake ("path:" + %s);
  c = f.homeConfigurations.zach.config;
in {
  order = map (e: e.name) (c.lib.dag.topoSort c.home.activation).result;
  edges = builtins.mapAttrs (n: v: { before = v.before; after = v.after; })
                            c.home.activation;
}
"""


def _nix_dag():
    """The evaluated activation DAG plus home-manager's own topological order.

    Uses `c.lib.dag.topoSort`, the function home-manager itself calls to order
    the activation script — not a reimplementation here, which would pin this
    test's idea of the sort instead of the one that ships.
    """
    exe = shutil.which("nix")
    assert exe, (
        "`nix` is not on PATH. This test measures the ACTIVATION ORDER by "
        "evaluating the flake; without nix it cannot measure anything, and a "
        "SKIP here would leave the delete/relink window pinned by nothing at "
        "all — which is the state this file was written to end. It is a "
        "dev-host target for exactly this reason (run-tests.sh DEVHOST_TARGETS)."
    )
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    p = subprocess.run(
        [exe, "eval", "--impure", "--json",
         "--extra-experimental-features", "nix-command flakes",
         "--expr", _EXPR % json.dumps(str(REPO_ROOT))],
        capture_output=True, text=True, env=env, timeout=900)
    assert p.returncode == 0, (
        "evaluating the home-manager activation DAG FAILED (exit %d). That is "
        "not a pass: nothing measured the delete/relink window this run.\n"
        "--- stderr ---\n%s" % (p.returncode, p.stderr[-4000:])
    )
    data = json.loads(p.stdout)
    order, edges = data["order"], data["edges"]
    assert len(order) >= MIN_PLAUSIBLE_ENTRIES, (
        "the evaluated activation DAG has only %d step(s): %s. A real "
        "configuration has many more, so this is a broken reader, and a broken "
        "reader makes every window assertion below vacuously true."
        % (len(order), order)
    )
    assert sorted(order) == sorted(edges), (
        "topoSort dropped or invented steps: sorted order %s vs sorted DAG %s"
        % (sorted(order), sorted(edges))
    )
    return order, edges


def window(order, lower, upper):
    """The step names STRICTLY between `lower` and `upper` in `order`.

    Separated from the assertions so the emptiness claim is made about a value
    a reader can print, and so `test_the_window_reader_can_see_an_inserted_step`
    can drive it with a synthetic order — a reader that always returns `[]`
    would make every assertion here pass, which is precisely the shape the
    guard this file replaces had.
    """
    lo, hi = order.index(lower), order.index(upper)
    assert lo < hi, (
        "%s is ordered AFTER %s (positions %d and %d) — the repair would run "
        "after the relink and do nothing at all: %s"
        % (lower, upper, lo, hi, order)
    )
    return order[lo + 1:hi]


def missing_bounds(edges, names):
    """Which of `names` the evaluated DAG does not contain.

    A function rather than an inline comprehension so the branch can be REACHED
    by a unit test below. It cannot be reached from the live DAG — reaching it
    would require home-manager to have already renamed a step — and an assertion
    that can never execute proves nothing about the case it describes.
    """
    return [n for n in names if n not in edges]


@pytest.fixture(scope="module")
def dag():
    return _nix_dag()


def test_both_bound_names_still_EXIST_in_the_activation_dag(dag):
    """🔴 THE FAILURE MODE `entryBetween` CANNOT REPORT ON ITS OWN.

    home-manager's `topoSort` (modules/lib/dag.nix) resolves `before`/`after` by
    looking each name up in the attrset and SILENTLY IGNORING one that is not
    there — it does not error, and it does not warn. So if upstream renames
    `installPackages` or `linkGeneration`, this entry quietly loses that bound
    and the sort is free to emit it back inside the delete/relink window, with
    every text-matching guard still green because `nix/home.nix` still spells
    the old name.

    Asserted here, once, against the evaluated DAG — the only place the question
    can be answered.
    """
    order, edges = dag
    del order
    missing = missing_bounds(edges, (LOWER, UPPER, ENTRY))
    assert not missing, (
        "activation step(s) %s are not in the evaluated DAG. If a BOUND name "
        "(%s / %s) vanished, topoSort ignored it silently and %s no longer has "
        "that bound — re-read home-manager's dag.nix and re-derive the "
        "ordering. Steps present: %s"
        % (missing, LOWER, UPPER, ENTRY, sorted(edges))
    )
    assert edges[ENTRY]["after"] == [LOWER], (
        "%s.after is %r, not [%r]. Without the lower bound the sort may emit it "
        "early again, back inside the window." % (ENTRY, edges[ENTRY]["after"], LOWER)
    )
    assert edges[ENTRY]["before"] == [UPPER], (
        "%s.before is %r, not [%r]. Without the upper bound the repair runs "
        "after the relink and does nothing."
        % (ENTRY, edges[ENTRY]["before"], UPPER)
    )


def test_ZERO_activation_steps_run_between_installPackages_and_linkGeneration(dag):
    """🔴 THE PROPERTY, on the evaluated order rather than on the expression.

    Measured on this repo, both sides of the reorder:
      * c20b1e0 (`entryBefore ["checkLinkTargets"]`) — SEVEN steps in the window:
        opencodeDropStaleConfig, reclaimManagedPaths, checkLinkTargets,
        writeBoundary, activityCollectorEnv, browserBridgeExtension,
        checkFilesChanged, espansoConfigDir, installPackages, linkGeneration.
      * afc942e6 (`entryBetween ["linkGeneration"] ["installPackages"]`) — ZERO:
        checkFilesChanged, espansoConfigDir, installPackages,
        reclaimManagedPaths, linkGeneration.

    The window must hold EXACTLY the repair and nothing else. A new devrc
    activation entry that sorts in here fails this test by name — which is the
    whole point, because such an entry can abort the switch while the reclaimed
    files exist nowhere on disk, and `installPackages` is `nix-env --set`, which
    this repo's memory records failing outright on an imperative-profile
    conflict.
    """
    order, _edges = dag
    between = window(order, LOWER, UPPER)
    assert between == [ENTRY], (
        "%d step(s) now run between %s and %s: %s.\n"
        "Between %s's `rm` and %s's `ln` the reclaimed files exist NOWHERE on "
        "disk. Anything in this window can abort the switch and strand them "
        "deleted-and-unlinked, which is strictly worse than the bug being "
        "repaired. Full order: %s"
        % (len(between), LOWER, UPPER, between, ENTRY, UPPER, order)
    )


def test_the_window_reader_can_see_an_inserted_step():
    """🔴 VALIDATE THE INSTRUMENT. `window()` returning `[]` unconditionally
    would make the test above pass for ever, on any tree.

    Fed a synthetic order with one extra step spliced into the window, it must
    report that step — and it must refuse an order where the bounds are the
    wrong way round rather than returning an empty slice, which is what a naive
    `order[lo+1:hi]` does when `lo > hi`.
    """
    good = ["writeBoundary", LOWER, ENTRY, UPPER, "reloadSystemd"]
    assert window(good, LOWER, UPPER) == [ENTRY]

    spliced = ["writeBoundary", LOWER, "someNewEntry", ENTRY, UPPER]
    assert window(spliced, LOWER, UPPER) == ["someNewEntry", ENTRY], (
        "the window reader cannot see a step inserted into the window, so the "
        "assertion it feeds is vacuous")

    inverted = ["writeBoundary", UPPER, ENTRY, LOWER]
    with pytest.raises(AssertionError):
        window(inverted, LOWER, UPPER)


def test_the_vanished_bound_reader_reports_a_name_the_dag_lacks():
    """🔴 REACH THE BRANCH, not just break it.

    `missing_bounds` guards the case where UPSTREAM renames a step while
    `nix/home.nix` keeps spelling the old name — `topoSort` ignores the unknown
    name silently, so nothing else in the toolchain says a word. That case
    cannot be produced from this repo (it needs a different home-manager), so
    the live assertion above can never execute its failing side, and an
    assertion that never executes is not coverage.

    Measured 2026-08-21 on the mechanism itself: rewriting this entry's `after`
    to `["installPackagesRENAMEDUPSTREAM"]` — a name in no DAG — made `nix eval`
    SUCCEED and return the same 16-step order, byte for byte. topoSort neither
    errored nor warned; it silently dropped the constraint, and today's order
    happens not to depend on it. That is precisely the hazard: nothing on any
    screen changes, the lower bound is simply gone, and the next edit to any
    activation entry is free to move this one back inside the window. Stated at
    the scope measured — this is one observation of one DAG, not a claim that
    the order can never move.
    """
    edges = {
        "writeBoundary": {"before": [], "after": []},
        "installPackagesRENAMED": {"before": [], "after": ["writeBoundary"]},
        UPPER: {"before": [], "after": ["writeBoundary"]},
        ENTRY: {"before": [UPPER], "after": [LOWER]},
    }
    assert missing_bounds(edges, (LOWER, UPPER, ENTRY)) == [LOWER], (
        "the reader cannot see a bound name that the DAG no longer contains, "
        "so the live assertion it feeds is unreachable")
    assert missing_bounds(edges, (UPPER, ENTRY)) == [], (
        "the reader reports a name that IS present — it would fail on every "
        "healthy tree, which is a permanently-red gate rather than a guard")
