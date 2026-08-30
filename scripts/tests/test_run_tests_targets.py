"""Regression + invariant guards for `scripts/run-tests.sh`'s TARGET LIST.

WHY THIS EXISTS
---------------
#276 added a FILE to the runner's target list:

    scripts/claude-hooks/tests/test_guard_core.py

`run_pytest()` guarded with `if [ ! -d "$d" ]`, so it rejected the file and
reported it as ``FAIL … (missing directory)``. Consequences, all measured:

  * the pytest gate went **RED on main** and stayed red;
  * the **913 tests** in that file never ran;
  * the failure text ("missing directory") read like an ENVIRONMENT fault, so
    the natural reading was "the new #284 gate is noisy", not "a real target is
    being dropped on the floor".

The last point is the reason this file is a *named* guard rather than a bare
existence check. The next person to add a file, a glob, or a typo'd path must
get a failure that names their entry and says what is wrong with it.

WHAT IS REGRESSION COVERAGE HERE AND WHAT IS NOT
------------------------------------------------
Being honest about this, per claude/RULES.md ("a guard that pins an invariant
the bug never violated is an INVARIANT GUARD -- label it as one, don't count it
as regression coverage"):

  * ``test_check_targets_accepts_the_real_target_list`` is REGRESSION coverage.
    It is red on ``origin/main`` (see the module docstring's matrix in the PR):
    at ``origin/main`` the runner has no ``--check-targets`` at all, so the flag
    is swallowed as ROOT and the run dies ``cannot cd to ROOT=--check-targets``.

  * ``test_a_file_target_is_accepted`` is the DIRECT pin on the #276 symptom --
    it drives the runner's real acceptance path with a file target and asserts
    it is not rejected. Red before the fix for THIS guard's own reason.

  * ``test_hermetic_list_still_names_the_guard_core_file`` is an INVARIANT GUARD.
    It pins that nobody "fixes" a future failure by deleting the entry -- which
    is exactly what the runner's own error message warns against. The bug never
    violated it, so it is not regression coverage.

  * the bogus-entry / glob tests are REACHABILITY proofs for GUARD 5: they
    mutate a COPY of the runner and assert it goes red **naming that entry**,
    so a green result from the real list means the guard can actually fire.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
RUN_TESTS = REPO_ROOT / "scripts" / "run-tests.sh"

# The entry #276 added and the gate then silently refused to run.
GUARD_CORE_TARGET = "scripts/claude-hooks/tests/test_guard_core.py"


def _require_check_targets(runner: Path) -> None:
    """Fail FAST if the runner has no --check-targets flag.

    🔴 Without this the pre-fix behaviour is not just red, it is red SLOWLY and
    for the wrong reason: the old arg parser has no `--check-targets` case, so
    the flag falls through to `*) ROOT="$1"` and is silently swallowed — then the
    trailing ROOT argument overwrites it, `cd` succeeds, and the runner executes
    THE ENTIRE SUITE. Measured while building this test: each invocation ran the
    full hermetic set and blew the 120s subprocess timeout, so eight tests turned
    into eight full suite runs.

    A capability check on the source turns that into an instant, named failure.
    """
    src = runner.read_text()
    assert "--check-targets" in src, (
        f"{runner} has no --check-targets flag, so the target-list guard "
        "(GUARD 5) does not exist in this revision. This is the PRE-FIX state: "
        "run_pytest() guarded with `[ ! -d \"$d\" ]` and rejected the file "
        "target scripts/claude-hooks/tests/test_guard_core.py as a 'missing "
        "directory', taking the gate red with 913 tests unrun."
    )


def _run(args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess:
    """Run the runner with a SCRUBBED environment.

    🔴 `DEVRC_TARGETS` is removed, and that is not hygiene — it is what keeps
    the other guards in this file honest. It selects a subset exactly like
    `--targets`, so an exported value makes
    `test_check_targets_accepts_the_real_target_list`,
    `test_a_file_target_is_accepted` and `test_check_floors_accepts_the_real_table`
    validate a one-entry list while their names claim they validated the
    shipped one. The developer most likely to have it exported is the one
    debugging a subset run — i.e. exactly when these guards matter.
    """
    env = {k: v for k, v in os.environ.items() if k != "DEVRC_TARGETS"}
    return subprocess.run(
        ["bash", *args],
        cwd=str(cwd or REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
    )


def _hermetic_targets() -> list[str]:
    """Parse HERMETIC_TARGETS out of the runner.

    Deliberately parsed from the shell source rather than re-listed here: a
    second hand-maintained copy of the list is how the list and its guard drift
    apart, which is the defect class this whole file is about.
    """
    src = RUN_TESTS.read_text()
    m = re.search(r"^HERMETIC_TARGETS=\((.*?)^\)", src, re.S | re.M)
    assert m, (
        "could not find a HERMETIC_TARGETS=( ... ) block in run-tests.sh. "
        "If the array was renamed again, update this parser -- do NOT delete "
        "the test, or the target list goes back to being unguarded."
    )
    targets = []
    for raw in m.group(1).splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        targets.append(line)
    assert targets, "parsed HERMETIC_TARGETS as EMPTY -- the parser is broken, not the list"
    return targets


# --------------------------------------------------------------------------
# Guard the guard: the parser must actually see the list.
# --------------------------------------------------------------------------

def test_parser_finds_a_plausible_target_list():
    """A positive control for _hermetic_targets().

    If this regex quietly stopped matching, every assertion below would iterate
    an empty list and pass vacuously -- a harness reporting success while
    testing nothing.
    """
    targets = _hermetic_targets()
    assert len(targets) >= 10, (
        f"only {len(targets)} targets parsed; the real list is much longer, so "
        "the parser is matching the wrong thing"
    )
    assert all(t.startswith("scripts/") for t in targets), targets


# --------------------------------------------------------------------------
# REGRESSION: the real list must be accepted by the real runner.
# --------------------------------------------------------------------------

def test_check_targets_accepts_the_real_target_list():
    """RED at origin/main (no --check-targets), GREEN after the fix.

    This is the end-to-end statement of the bug: every entry in the shipped
    target list must resolve to something pytest can run.
    """
    _require_check_targets(RUN_TESTS)
    proc = _run([str(RUN_TESTS), "--check-targets", str(REPO_ROOT)])
    assert proc.returncode == 0, (
        "run-tests.sh --check-targets rejected the shipped target list.\n"
        f"exit={proc.returncode}\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )


def test_hermetic_list_still_names_the_guard_core_file():
    """INVARIANT GUARD (not regression coverage).

    The runner's GUARD 5 error tells the reader "do NOT delete the entry to make
    this pass". This pins that advice so the 913 guard-core tests cannot be
    dropped from the gate as a way of turning it green.
    """
    targets = _hermetic_targets()
    assert GUARD_CORE_TARGET in targets, (
        f"{GUARD_CORE_TARGET} is no longer in HERMETIC_TARGETS. It holds ~913 "
        "tests covering the shared guard core behind bash-guard.py and "
        "opencode's plugin/guard.js. If the suite genuinely moved, update this "
        "constant; do not simply drop the entry."
    )
    assert (REPO_ROOT / GUARD_CORE_TARGET).is_file()


def test_a_file_target_is_accepted():
    """DIRECT pin on the #276 symptom: a FILE target must not be rejected.

    Drives the real runner's real acceptance path against a copy whose list is
    reduced to a single FILE. Before the fix this reported
    ``FAIL … (missing directory)``; the assertion names that string so a
    regression fails for THIS guard's reason and not a neighbouring one.
    """
    targets = _hermetic_targets()
    _require_check_targets(RUN_TESTS)
    file_targets = [t for t in targets if t.endswith(".py")]
    assert file_targets, (
        "the shipped list no longer contains any FILE target, so this test "
        "would silently stop covering the #276 case"
    )
    proc = _run([str(RUN_TESTS), "--check-targets", str(REPO_ROOT)])
    assert "missing directory" not in (proc.stdout + proc.stderr), (
        "the runner still describes a target as a 'missing directory' -- the "
        f"file/dir conflation is back.\n{proc.stdout}\n{proc.stderr}"
    )
    assert proc.returncode == 0, f"{proc.stdout}\n{proc.stderr}"


# --------------------------------------------------------------------------
# REACHABILITY: prove GUARD 5 can go red, naming the offending entry.
# --------------------------------------------------------------------------

def _runner_copy_with_extra_target(tmp_path: Path, entry: str) -> Path:
    """Copy run-tests.sh, injecting one extra entry into HERMETIC_TARGETS."""
    dst = tmp_path / "run-tests.sh"
    src = RUN_TESTS.read_text()
    patched, n = re.subn(
        r"^HERMETIC_TARGETS=\(\n",
        f"HERMETIC_TARGETS=(\n  {entry}\n",
        src,
        count=1,
        flags=re.M,
    )
    assert n == 1, "failed to inject a target into the copied runner"
    dst.write_text(patched)
    return dst


@pytest.mark.parametrize(
    "entry,needle",
    [
        ("scripts/does/not/exist/tests", "does not exist"),
        # A glob that matches NOTHING. A matching glob is expanded by bash
        # inside the array literal (measured: injecting scripts/tests/test_*.py
        # took the list 15 -> 32) and is harmless; the dangerous one is the
        # unmatched glob, which survives as a literal `*`.
        ("scripts/tests/test_zzz_no_such_*.py", "GLOB"),
        # Exists, is a regular file, is not pytest-collectable.
        ("scripts/run-tests.sh", "pytest-collectable"),
    ],
)
def test_check_targets_rejects_a_bad_entry_by_name(tmp_path, entry, needle):
    """Break it on purpose and confirm GUARD 5 goes red FOR ITS OWN REASON.

    Each case asserts BOTH that the run fails AND that the offending entry is
    named in the output -- "it failed" alone would also be satisfied by an
    unrelated guard tripping first, which is the failure mode claude/RULES.md
    calls out ("a DIFFERENT guard's error kills your test").
    """
    _require_check_targets(RUN_TESTS)
    runner = _runner_copy_with_extra_target(tmp_path, entry)
    proc = _run([str(runner), "--check-targets", str(REPO_ROOT)])
    out = proc.stdout + proc.stderr
    assert proc.returncode == 2, (
        f"expected GUARD 5 to fail with exit 2 for {entry!r}, got "
        f"{proc.returncode}.\n{out}"
    )
    assert entry in out, f"GUARD 5 failed but never named {entry!r}.\n{out}"
    assert needle in out, (
        f"GUARD 5 named {entry!r} but not WHY (expected {needle!r}).\n{out}"
    )


def test_check_targets_is_cheap_and_runs_no_tests(tmp_path):
    """--check-targets must not execute a suite.

    If it ever started running pytest, the reachability tests above would take
    minutes each and would be silently disabled by the first person who noticed.
    MEASURED at the pre-fix revision: the flag was swallowed as ROOT and every
    invocation ran the FULL hermetic set, blowing a 120s timeout per test.
    """
    _require_check_targets(RUN_TESTS)
    proc = _run([str(RUN_TESTS), "--check-targets", str(REPO_ROOT)])
    assert "=== pytest " not in proc.stdout, (
        "--check-targets ran a suite; it is supposed to validate the list only.\n"
        f"{proc.stdout}"
    )


# --- `--targets`: running a SUBSET of the declared target list -----------------
# 🔴 EVERY TEST BELOW IS ABOUT ONE HAZARD: a subset run that tests less than the
# operator thinks it did. The mechanism exists so CI can skip suites a change
# could not have broken, which means its failure mode is a GREEN run that never
# executed the suite holding the regression. So the dangerous inputs — a typo, an
# empty selection, a target from another set — must all be FATAL, and the ones
# that are merely narrow must SAY they are narrow.
#
# These use `--check-floors`, which applies the subset and exits before pytest,
# so each costs milliseconds rather than a suite run. That is the same trick
# `_require_check_targets` above documents, for the same reason.


def _require_targets_flag(runner: Path) -> None:
    """Fail FAST and by name if `--targets` is absent from this revision.

    Without it the flag falls through to `*) ROOT="$1"` exactly as
    `--check-targets` once did, the trailing ROOT overwrites it, and the runner
    executes the ENTIRE suite — so every assertion below would pass or fail for
    reasons unrelated to what it claims to test.
    """
    assert "--targets" in runner.read_text(), (
        f"{runner} has no --targets flag, so the subset mechanism does not "
        "exist in this revision and these guards are vacuous."
    )


def test_a_subset_narrows_the_target_list_and_the_floor_follows():
    """The floor is DERIVED, so narrowing the list must narrow the floor.

    This is the property that makes a subset run safe to gate on at all: if the
    floor stayed at the full-set sum, every subset run would fail GUARD 3 and
    the mechanism would be unusable; if the floor were not derived at all, a
    subset could collapse to zero tests and still pass.
    """
    _require_targets_flag(RUN_TESTS)
    two = ["scripts/collector/tests", "scripts/opencode/tests"]
    sub = _run([str(RUN_TESTS), "--targets", " ".join(two), "--check-floors",
                str(REPO_ROOT)])
    full = _run([str(RUN_TESTS), "--check-floors", str(REPO_ROOT)])
    assert sub.returncode == 0, f"subset --check-floors failed:\n{sub.stdout}\n{sub.stderr}"

    def _floor(out: str) -> int:
        # NOT `\([^)]*\)` — the subset label contains a nested `target(s)`, so a
        # non-greedy run up to the ` = ` is the only form that reads both.
        m = re.search(r"GLOBAL floor .*? = (\d+)", out)
        assert m, f"no GLOBAL floor line in output:\n{out}"
        return int(m.group(1))

    sub_floor, full_floor = _floor(sub.stdout), _floor(full.stdout)
    assert 0 < sub_floor < full_floor, (
        f"a 2-target subset floor ({sub_floor}) must be positive and strictly "
        f"below the full-set floor ({full_floor}). Equal means the floor is not "
        "derived from the selection; zero means it collapsed."
    )


def test_a_subset_run_SAYS_it_ran_a_subset():
    """A narrowed run must announce itself on stderr.

    🔴 The verdict line of a subset run looks exactly like a full one — same
    PASS, same shape. Whoever reads it has no other way to know that most of the
    suite did not execute, so the announcement is the only thing separating
    "the gate passed" from "the gate passed on 2 of 29 targets".
    """
    _require_targets_flag(RUN_TESTS)
    proc = _run([str(RUN_TESTS), "--targets", "scripts/collector/tests",
                 "--check-floors", str(REPO_ROOT)])
    # 🔴 stderr SPECIFICALLY, and a string the floor line does not contain.
    # The first version asserted "SUBSET" in stdout+stderr, which the floor
    # line's own "a SUBSET of hermetic" satisfies — so deleting the
    # announcement entirely left the test green. Watched: that mutant survived.
    assert "selected via --targets" in proc.stderr, (
        "a --targets run did not announce the narrowing on stderr. The verdict "
        "line of a subset run is identical to a full one, so this announcement "
        f"is the only thing that distinguishes them.\n{proc.stderr}"
    )
    assert re.search(r"SELECTED target\(s\), a SUBSET", proc.stdout), (
        "the floor line must say it summed the SELECTED targets, not '$SET' — "
        f"labelling a subset sum as the full set is a false coverage claim.\n{proc.stdout}"
    )


def test_an_unknown_target_is_FATAL_not_silently_dropped():
    """The typo case, and the reason the whole mechanism is allowlist-based.

    `scripts/dl-router` (no `/tests`) is a real directory and an obvious thing
    to type. Under a prefix- or filter-based design it would select NOTHING and
    the run would exit 0 having tested nothing — indistinguishable from a pass.
    """
    _require_targets_flag(RUN_TESTS)
    proc = _run([str(RUN_TESTS), "--targets", "scripts/dl-router",
                 "--check-floors", str(REPO_ROOT)])
    assert proc.returncode == 3, (
        f"expected exit 3 for an unknown target, got {proc.returncode}.\n"
        f"{proc.stdout}\n{proc.stderr}"
    )
    assert "scripts/dl-router" in proc.stderr, (
        f"the error must NAME the offending target.\n{proc.stderr}"
    )


def test_an_empty_selection_is_FATAL():
    """'Nothing to run' is never a verdict this script may reach."""
    _require_targets_flag(RUN_TESTS)
    proc = _run([str(RUN_TESTS), "--targets", "   ", "--check-floors",
                 str(REPO_ROOT)])
    assert proc.returncode == 3, (
        f"expected exit 3 for an empty selection, got {proc.returncode}.\n"
        f"{proc.stdout}\n{proc.stderr}"
    )


def test_a_subset_may_NARROW_the_set_but_never_WIDEN_it():
    """A devhost target under `--set hermetic` is an error, not an implicit
    `--set all`.

    The two sets exist because devhost targets need a real desktop; letting
    `--targets` reach into the other set would run them in an environment that
    cannot satisfy them, and the failure would look like a code defect.
    """
    _require_targets_flag(RUN_TESTS)
    devhost = "scripts/devhost-tests"
    assert devhost in RUN_TESTS.read_text(), (
        f"{devhost} is no longer declared; pick another DEVHOST_TARGETS entry"
    )
    proc = _run([str(RUN_TESTS), "--targets", devhost, "--check-floors",
                 str(REPO_ROOT)])
    assert proc.returncode == 3, (
        f"a devhost target under --set hermetic must be FATAL, got "
        f"{proc.returncode}.\n{proc.stdout}\n{proc.stderr}"
    )


def test_a_pinned_skip_whose_TARGET_did_not_run_does_not_count():
    """🔴 The guard that a subset run broke, pinned BEHAVIOURALLY.

    EXPECTED_SKIPS entries are keyed by target. `_skip_entry_applies` honoured
    the entry's CONDITION but not whether its target was in the run, so a subset
    that excluded every pinned target still counted them:

        ERROR: 1 test(s) skipped, but 2 of 3 pinned entries apply here.

    The run was correct and the guard called it a failure — how a gate teaches
    people to ignore it.

    ⚠ This asserts BEHAVIOUR, not source text. The first version checked that
    `for _t in "${TARGETS[@]}"` appeared in the function; deleting the `if` that
    USES that loop's result left the loop in place and the test green. Watched:
    that mutant survived. A source check could see the ingredient and not the
    decision, which is the whole failure mode.

    Runs ONE tiny target that owns no pinned skip, so a broken predicate counts
    >0 expected skips against 0 observed and the run goes red.
    """
    _require_targets_flag(RUN_TESTS)
    tiny = "scripts/collector/i3/tests"
    assert tiny in _hermetic_targets(), (
        f"{tiny} is no longer a declared target; pick another small one that "
        "does not appear in EXPECTED_SKIPS"
    )
    src = RUN_TESTS.read_text()
    m = re.search(r"EXPECTED_SKIPS=\((.*?)\n\)", src, re.S)
    assert m, "could not find EXPECTED_SKIPS in run-tests.sh"
    pinned_dirs = {e.split("|")[0] for e in re.findall(r'^\s*"([^"]+)"', m.group(1), re.M)}
    assert tiny not in pinned_dirs, (
        f"{tiny} now owns a pinned skip, so it can no longer isolate this "
        f"property. Pinned targets: {sorted(pinned_dirs)}"
    )
    proc = subprocess.run(
        ["bash", str(RUN_TESTS), "--targets", tiny, str(REPO_ROOT)],
        cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=300,
    )
    combined = proc.stdout + proc.stderr
    assert "pinned entries apply here" not in combined, (
        "a subset excluding every pinned-skip target still counted pinned "
        f"entries — `_skip_entry_applies` is not filtering on TARGETS.\n{combined}"
    )
    assert proc.returncode == 0, (
        f"a single-target subset run should pass, got {proc.returncode}.\n{combined}"
    )


# --- findings from the round-1 adversarial audit of #1073 ----------------------
# Each of these kills a mutant that SURVIVED the first six. They are here because
# an audit found them, not because they were designed in — recorded so the next
# reader knows which properties were learned the hard way.


def test_the_SELECTED_set_is_exactly_what_was_REQUESTED():
    """🔴 F10. The first six tests asserted the floor MOVED and that the run
    ANNOUNCED itself — neither pins the selection's identity or size.

    Measured mutant (non-deletion, so the weak deletion sweep could not see it):

        _selected+=("$_r")
        ->  [ "${#_selected[@]}" -eq 0 ] && _selected+=("$_r")

    i.e. run only the FIRST requested target. It SURVIVED all six new tests AND
    all 69 tests across the four meta-test files. Production behaviour under it:
    `--targets "A B"` runs A only, announces "1 of the hermetic set", floors at
    A's floor alone, and exits 0 — the file header's own named hazard, silently
    green.

    So this asserts the COUNT and the IDENTITY of what ran, from the floor
    table, which lists exactly the selected targets.
    """
    _require_targets_flag(RUN_TESTS)
    want = ["scripts/collector/tests", "scripts/opencode/tests"]
    proc = _run([str(RUN_TESTS), "--targets", " ".join(want), "--check-floors",
                 str(REPO_ROOT)])
    assert proc.returncode == 0, f"{proc.stdout}\n{proc.stderr}"
    selected = {m.group(1) for m in
                re.finditer(r"^  floor \d+  (\S+)\s*$", proc.stdout, re.M)}
    assert selected == set(want), (
        f"the run selected {sorted(selected)} but was asked for {sorted(want)}. "
        "A selection that silently drops a requested target runs less than the "
        "operator believes and still exits 0."
    )


def test_a_DUPLICATE_target_is_FATAL():
    """🔴 F2. A repeat inflates every coverage number AND defeats the floor by
    its own derivation — the duplicate contributes its floor twice, then
    satisfies the doubled floor by running twice.

    Measured before the fix: one suite, two executions,
    `SUBSET — 2 …  TOTAL collected=26 …  floor: 24`, exit 0.

    Reachable by exactly the path->target mapper this is groundwork for, which
    emits duplicates by construction when two changed files share one target.
    """
    _require_targets_flag(RUN_TESTS)
    t = "scripts/collector/i3/tests"
    proc = _run([str(RUN_TESTS), "--targets", f"{t} {t}", "--check-floors",
                 str(REPO_ROOT)])
    assert proc.returncode == 3, (
        f"a duplicated target must be FATAL, got {proc.returncode}.\n"
        f"{proc.stdout}\n{proc.stderr}"
    )
    assert "more than once" in proc.stderr, proc.stderr


def test_the_value_is_NOT_glob_expanded():
    """🔴 F8. `_requested=($ONLY_TARGETS)` does PATHNAME EXPANSION as well as
    word splitting; only the splitting is wanted.

    Measured before the fix: `--targets 'scripts/collector/*/tests'` selected 5
    targets and exited 0. That makes the selection track the FILESYSTEM — delete
    one of those suites and the glob silently selects four and stays green,
    while a FULL run goes red on GUARD 5 (`does not exist`). It converts the
    #276 failure this file exists for into a silent narrowing.

    A glob must now be FATAL as an unknown target, because after `set -f` the
    literal `scripts/collector/*/tests` matches no declared entry.
    """
    _require_targets_flag(RUN_TESTS)
    proc = _run([str(RUN_TESTS), "--targets", "scripts/collector/*/tests",
                 "--check-floors", str(REPO_ROOT)])
    assert proc.returncode == 3, (
        f"a glob must not be expanded into a selection, got {proc.returncode}. "
        f"If this selected several targets, the value was expanded against the "
        f"filesystem.\n{proc.stdout}\n{proc.stderr}"
    )


def test_an_EMPTY_targets_value_is_FATAL_not_a_full_run():
    """🟢 G1 → real. `--targets ""` and `--targets=` previously fell through to
    a FULL run, contradicting the block's own stated rule and the name of
    `test_an_empty_selection_is_FATAL` (which only ever covered `"   "`).

    Fail-safe in direction, wrong in report: the operator asked for a subset and
    silently got everything. The likeliest producer is a broken caller —
    `--targets "$(compute)"` where compute returned nothing.
    """
    _require_targets_flag(RUN_TESTS)
    for args in (["--targets", ""], ["--targets="]):
        proc = _run([str(RUN_TESTS), *args, "--check-floors", str(REPO_ROOT)])
        assert proc.returncode == 3, (
            f"{args} should be FATAL, got {proc.returncode} — an empty value "
            f"must not fall through to a full run.\n{proc.stdout}\n{proc.stderr}"
        )


def test_repeating_the_flag_is_FATAL_not_last_wins():
    """🟡 F7. Last-wins silently discards the earlier selection, and the caller
    that hits it is a WRAPPER appending its own `--targets` on top of the
    operator's — so the discarded half is the half nobody is watching."""
    _require_targets_flag(RUN_TESTS)
    proc = _run([str(RUN_TESTS), "--targets", "scripts/collector/tests",
                 "--targets", "scripts/opencode/tests", "--check-floors",
                 str(REPO_ROOT)])
    assert proc.returncode == 3, (
        f"--targets twice must be FATAL, got {proc.returncode}.\n{proc.stderr}"
    )


def test_a_partial_run_is_declared_where_gate_sh_actually_LOOKS():
    """🔴 F1, and the reason it was deploy-blocking.

    `gate.sh` locates `^=\\{8,\\} .*SUMMARY` and prints from there DOWN, so the
    stderr announcement emitted at the TOP of the run is excluded by
    construction. Measured before the fix: a 1-target run through `gate.sh`
    showed `SUMMARY (hermetic set)` as its FIRST line and `GATE: RESULT=PASS` as
    its last — a positive claim that the whole set ran, on the one surface
    CLAUDE.md tells people to read.

    Asserted on the SUMMARY region specifically, not on the whole output, so it
    cannot pass on the strength of the top-of-run announcement.
    """
    _require_targets_flag(RUN_TESTS)
    proc = _run([str(RUN_TESTS), "--targets", "scripts/collector/i3/tests",
                 str(REPO_ROOT)])
    assert "SUMMARY" in proc.stdout, f"no SUMMARY banner:\n{proc.stdout[-3000:]}"
    tail = proc.stdout[proc.stdout.index("SUMMARY"):]
    assert "SUBSET" in tail or "PARTIAL RUN" in tail, (
        "the SUMMARY region — everything gate.sh shows an operator — does not "
        "say the run was partial. A reader sees full-set framing over a "
        f"fraction of the suite.\n{tail[:1500]}"
    )


def test_every_expected_skip_dir_is_a_declared_target():
    """🟡 F5. The comment on `_skip_entry_applies` asserted this was pinned; it
    was not. Pinning it now, because that filter matches a ledger dir EXACTLY
    against TARGETS while the matching loop treats it as a PREFIX — so an entry
    naming a parent directory works today and would be silently dropped under
    any subset.
    """
    src = RUN_TESTS.read_text()
    m = re.search(r"EXPECTED_SKIPS=\((.*?)\n\)", src, re.S)
    assert m, "could not find EXPECTED_SKIPS in run-tests.sh"
    dirs = {e.split("|")[0] for e in re.findall(r'^\s*"([^"]+)"', m.group(1), re.M)}
    assert dirs, "parsed ZERO ledger entries — the parser is broken, not the ledger"
    declared = set(_hermetic_targets())
    md = re.search(r"^DEVHOST_TARGETS=\((.*?)^\)", src, re.S | re.M)
    if md:
        declared |= {l.strip() for l in md.group(1).splitlines()
                     if l.strip() and not l.strip().startswith("#")}
    assert dirs <= declared, (
        f"EXPECTED_SKIPS names dir(s) that are not declared targets: "
        f"{sorted(dirs - declared)}. `_skip_entry_applies` matches these "
        "EXACTLY against TARGETS, so under a subset such an entry is silently "
        "inapplicable and its skip becomes UNPINNED."
    )


def test_DEVRC_TARGETS_is_equivalent_to_the_flag_and_names_itself():
    """🟡 F9. The env var was reachable, undocumented and untested: the string
    `DEVRC_TARGETS` occurred in exactly ONE place in the repo — the line that
    read it — so a mutant deleting that read survived the entire suite.

    Two properties, because either alone is insufficient:

    1. **Equivalence.** A caller that cannot add an argument must get the same
       selection, and the same derived floor, as one that can.
    2. **Attribution.** An EXPORTED value narrows every run-tests.sh in the
       shell — the pre-push hook's included — and nobody typed it. The run must
       name the environment as the source, or "why did this only run 3 targets"
       has no answer in the output.
    """
    _require_targets_flag(RUN_TESTS)
    t = "scripts/collector/i3/tests"
    src = RUN_TESTS.read_text()
    assert "DEVRC_TARGETS" in src, "the env override is gone from run-tests.sh"

    flag = _run([str(RUN_TESTS), "--targets", t, "--check-floors", str(REPO_ROOT)])
    env = subprocess.run(
        ["bash", str(RUN_TESTS), "--check-floors", str(REPO_ROOT)],
        cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=120,
        env={**os.environ, "DEVRC_TARGETS": t},
    )
    assert flag.returncode == 0 and env.returncode == 0, (flag.stderr, env.stderr)

    def _floor(out: str) -> str:
        m = re.search(r"GLOBAL floor .*? = (\d+)", out)
        assert m, f"no GLOBAL floor line:\n{out}"
        return m.group(1)

    assert _floor(flag.stdout) == _floor(env.stdout), (
        "DEVRC_TARGETS and --targets produced DIFFERENT floors, so they are not "
        "the same mechanism and the env path is unguarded by every other test "
        "in this file."
    )
    assert "DEVRC_TARGETS" in env.stderr, (
        "a subset selected by the environment did not name the environment as "
        f"its source — an ambient value is the one nobody typed.\n{env.stderr}"
    )
    assert "DEVRC_TARGETS" not in flag.stderr, (
        "a flag-selected subset blamed the environment.\n" + flag.stderr
    )


def test_the_meta_test_harness_is_immune_to_an_ambient_DEVRC_TARGETS():
    """🟡 F9, second half — the one that makes OTHER guards lie.

    `_run` inherits `os.environ`. With `DEVRC_TARGETS` exported, three
    target-list guards in this file pass VACUOUSLY —
    `test_check_targets_accepts_the_real_target_list`,
    `test_a_file_target_is_accepted` and `test_check_floors_accepts_the_real_table`
    would each validate a one-entry list while claiming to validate the shipped
    one. A developer debugging a subset run is exactly the person with that
    variable exported.

    So `_run` must scrub it. This asserts the scrubbing, not the intent.
    """
    saved = os.environ.get("DEVRC_TARGETS")
    os.environ["DEVRC_TARGETS"] = "scripts/collector/i3/tests"
    try:
        proc = _run([str(RUN_TESTS), "--check-floors", str(REPO_ROOT)])
        assert "SUBSET" not in proc.stderr, (
            "_run leaked an ambient DEVRC_TARGETS into the runner, so every "
            "full-set assertion in this file can pass against a 1-target run.\n"
            + proc.stderr
        )
    finally:
        if saved is None:
            os.environ.pop("DEVRC_TARGETS", None)
        else:
            os.environ["DEVRC_TARGETS"] = saved
