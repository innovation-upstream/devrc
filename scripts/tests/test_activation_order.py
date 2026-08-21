"""The activation ORDER, measured from the evaluated DAG rather than from prose.

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

🔴 THIS FILE USED TO SAY "IT CANNOT BE HERMETIC", AND THAT CLAIM WAS WRONG —
recorded because it was believed for long enough to matter. The true half:
`nix eval` genuinely cannot RUN in the nix build sandbox, whose `nix` binary has
`nix-command` disabled and which has none of the flake's inputs. The step that
does not follow: the SANDBOX does not have to evaluate anything. The DAG is a
pure value (names and two lists of names), so `flake.nix` evaluates it at
flake-eval time through home-manager's own `lib.dag.topoSort` and hands
`checks.pytests` the result as JSON in `$DEVRC_ACTIVATION_DAG_JSON`.

The cost of the wrong conclusion was measurable, and it is the reason this note
is this long. As a DEV-HOST-ONLY target this file ran in exactly one tier:
`run-tests.sh --set all`, i.e. the pre-push hook. `flake.nix` runs
`--set hermetic`, which never collects a dev-host target. And on 2026-08-21 the
hook was measured NOT INSTALLED on the workbench (`core.hooksPath` ->
`.git/hooks`, 14 files, all `*.sample`), so the delete/relink window was pinned
by nothing at all while four separate comments described this suite as running
"on every push".

MEASURED 2026-08-21, since the standing worry was that evaluating the
home-manager DAG needs `--impure`: it does not.
`nix eval .#homeConfigurations.zach --apply '<topoSort>'` succeeds in PURE mode.
The `--impure` in the fallback below belongs to `builtins.getFlake` on an
unlocked local path, not to this configuration.

TWO SOURCES, ONE EVALUATION, AND NEITHER IS A SKIP:
  * `$DEVRC_ACTIVATION_DAG_JSON` — set by flake.nix. The only thing that can
    work in the build sandbox.
  * `nix eval --impure` on the working tree — the dev-host fallback, and
    strictly MORE informative there because it sees uncommitted edits to
    nix/home.nix.
If the variable is set and unreadable, or `nix` is missing when it is not, this
FAILS. A guard that means one thing on a dev host and nothing in the tier that
gates merges is worse than no guard — that part of the old note stands.
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

# The seam between this file and flake.nix. Named once, here, and asserted
# against flake.nix's own text by `test_the_hermetic_tier_INJECTS_the_dag` —
# each side is invisible to the other and a typo on either would silently
# reinstate the dev-host-only behaviour this file was moved out of.
DAG_JSON_ENV = "DEVRC_ACTIVATION_DAG_JSON"

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


def _dag_from_json(text, source):
    """Validate and unpack one DAG document, whichever source produced it.

    ONE validator for BOTH sources on purpose: an injected file and a live eval
    are two ways to obtain the same value, and a check that ran on only one of
    them would leave the other able to hand this file `{}` unnoticed.
    """
    data = json.loads(text)
    order, edges = data["order"], data["edges"]
    assert len(order) >= MIN_PLAUSIBLE_ENTRIES, (
        "the activation DAG from %s has only %d step(s): %s. A real "
        "configuration has many more, so this is a broken reader, and a broken "
        "reader makes every window assertion below vacuously true."
        % (source, len(order), order)
    )
    assert sorted(order) == sorted(edges), (
        "topoSort dropped or invented steps (%s): sorted order %s vs sorted "
        "DAG %s" % (source, sorted(order), sorted(edges))
    )
    return order, edges


def _nix_dag():
    """The evaluated activation DAG plus home-manager's own topological order.

    Uses `c.lib.dag.topoSort`, the function home-manager itself calls to order
    the activation script — not a reimplementation here, which would pin this
    test's idea of the sort instead of the one that ships.

    Source 1 is `$DEVRC_ACTIVATION_DAG_JSON`, written by flake.nix at
    flake-eval time. It is what makes this suite hermetic; see the module
    docstring. Source 2 is a live `nix eval` of the working tree.
    """
    injected = os.environ.get(DAG_JSON_ENV)
    if injected:
        p = Path(injected)
        assert p.is_file(), (
            "%s is set to %r, which is not a readable file. That is a WIRING "
            "failure and NOT a reason to fall back: inside the nix build "
            "sandbox the fallback cannot work either (that `nix` has "
            "nix-command disabled), so a silent fallback would turn a broken "
            "injection into a confusing eval error instead of this message."
            % (DAG_JSON_ENV, injected)
        )
        return _dag_from_json(p.read_text(), "%s=%s" % (DAG_JSON_ENV, injected))

    exe = shutil.which("nix")
    assert exe, (
        "`nix` is not on PATH and %s is unset, so this test has NO source for "
        "the activation DAG. That is not a pass and must never become a skip: "
        "it would leave the delete/relink window pinned by nothing at all, "
        "which is the state this file was written to end." % DAG_JSON_ENV
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
    return _dag_from_json(p.stdout, "nix eval --impure %s" % REPO_ROOT)


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


# --- the SEAM: this reader and flake.nix's writer ---------------------------- #

def test_the_hermetic_tier_INJECTS_the_dag():
    """🔴 A SEAM NEITHER SIDE CAN SEE. This file can read an injected DAG;
    flake.nix has to be the thing that injects it. Each half is hermetically
    testable on its own and the pair can still be broken: rename the variable on
    one side and every test here still passes — on a DEV HOST, by silently
    falling back to `nix eval`. The tier that would notice is the nix sandbox,
    where the fallback cannot work, and that is exactly the tier nobody runs by
    hand.

    Asserted as a LEDGER of the two facts that must agree — the variable name
    and the derivation that produces its value — because a `"DEVRC" in flake`
    style check is walkable by any unrelated mention. The behavioural half is
    the derivation build itself: `nix build .#checks.x86_64-linux.pytests`
    executes this suite with the variable set and no usable `nix eval`, so a
    broken injection is a RED BUILD rather than a quiet fallback.
    """
    flake = (REPO_ROOT / "flake.nix").read_text()
    assert "export %s=${activationDagJson}" % DAG_JSON_ENV in flake, (
        "flake.nix does not export %s from the `activationDagJson` binding. "
        "Without it checks.pytests runs this suite with no DAG source, and in "
        "the sandbox that is a hard failure — but on a dev host it would "
        "silently fall back to `nix eval` and look fine." % DAG_JSON_ENV)
    assert "activationDagJson = pkgs.writeText" in flake, (
        "the `activationDagJson` derivation is gone from flake.nix, so the "
        "variable above is exported from nothing")
    assert "lib.dag.topoSort" in flake, (
        "flake.nix no longer sorts the DAG with home-manager's own topoSort — "
        "the injected order would then be this repo's idea of the order rather "
        "than the one the switch executes")


def test_the_injected_json_path_is_the_one_actually_read(tmp_path, monkeypatch):
    """The behavioural half of the seam, on THIS side of it: prove the reader
    honours the variable rather than always shelling out.

    Driven with a synthetic DAG whose step names appear in no real
    configuration, so a reader that ignored the variable and evaluated the real
    flake would return the real names and fail here. That is the positive
    control — without it, a `_nix_dag` that dropped the injected branch entirely
    would still pass every other test in this file on a dev host.
    """
    synthetic = {
        "order": ["s%d" % i for i in range(MIN_PLAUSIBLE_ENTRIES)],
        "edges": {"s%d" % i: {"before": [], "after": []}
                  for i in range(MIN_PLAUSIBLE_ENTRIES)},
    }
    p = tmp_path / "dag.json"
    p.write_text(json.dumps(synthetic))
    monkeypatch.setenv(DAG_JSON_ENV, str(p))
    order, edges = _nix_dag()
    assert order == synthetic["order"], (
        "the reader did not use %s — it returned %s, which is not the "
        "synthetic DAG this test wrote" % (DAG_JSON_ENV, order))
    assert set(edges) == set(synthetic["edges"])


def test_an_unreadable_injected_path_FAILS_rather_than_falling_back(tmp_path, monkeypatch):
    """🔴 THE FALLBACK MUST NOT RESCUE A BROKEN INJECTION. If the variable is set
    but names nothing, this is a wiring bug in flake.nix; quietly evaluating the
    working tree instead would make the hermetic tier untestable from the dev
    host, which is the "green in the tier I ran, red in the tier that gates
    merges" shape claude/RULES.md names."""
    monkeypatch.setenv(DAG_JSON_ENV, str(tmp_path / "does-not-exist.json"))
    with pytest.raises(AssertionError, match="not a readable file"):
        _nix_dag()
